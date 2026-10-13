"""Multi-turn AI chat with playbook, runtime context, sessions, and streaming."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from threading import RLock
from typing import Any, Callable

import httpx

from ..models import AIChatContext, AIChatMessage, AIChatRequest, AIChatResponse, AIChatUsage, AIProviderConfig
from ..utils.persistence import StatePersistenceError
from .ai_playbook import PageScope, build_playbook_text
from .ai_session_store import load_session, load_usage_totals, prune_expired_sessions, save_session, save_usage_totals
from .strategy_registry import StrategyRegistry

logger = logging.getLogger(__name__)

VALID_CONCLUSIONS = ("发酵中", "高潮", "退潮", "Unknown")


def normalize_ai_conclusion(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Unknown"
    if text in VALID_CONCLUSIONS:
        return text
    if "退潮" in text:
        return "退潮"
    if "高潮" in text:
        return "高潮"
    if "发酵" in text:
        return "发酵中"
    if any(token in text for token in ("看多", "看涨", "bull", "Bull")):
        return "发酵中"
    if any(token in text for token in ("看空", "看跌", "bear", "Bear")):
        return "退潮"
    return "Unknown"


class AIChatService:
    def __init__(
        self,
        registry: StrategyRegistry,
        *,
        resolve_provider: Callable[[], AIProviderConfig | None],
        resolve_api_key: Callable[[AIProviderConfig | None], str],
        enrich_context: Callable[[AIChatContext], AIChatContext],
        load_user_principles: Callable[[], list[str]],
        timeout_sec: Callable[[], float],
        retry_count: Callable[[], int],
    ) -> None:
        self._registry = registry
        self._resolve_provider = resolve_provider
        self._resolve_api_key = resolve_api_key
        self._enrich_context = enrich_context
        self._load_user_principles = load_user_principles
        self._timeout_sec = timeout_sec
        self._retry_count = retry_count
        self._sessions: dict[str, list[AIChatMessage]] = {}
        self._usage_totals: dict[str, int] = load_usage_totals()
        self._session_usage: dict[str, dict[str, int]] = {}
        # Establish the lifetime ledger before any session cleanup.
        save_usage_totals(self._usage_totals)
        # 保护 _sessions / _usage_totals 的并发读写(FastAPI 同步路由跑在线程池)。
        # 注意:只锁共享状态,httpx 调用在锁外,避免并发对话串行化。
        self._lock = RLock()
        prune_expired_sessions()

    def chat(self, request: AIChatRequest) -> AIChatResponse:
        session_id, history, enriched, api_messages, provider, api_key = self._prepare_request(request)
        if provider is None or not api_key:
            assistant = AIChatMessage(role="assistant", content=self._fallback_reply(enriched, self._last_user_text(request)))
            return self._finalize(session_id, history, request.messages, assistant, enriched, usage=None)

        content, usage = self._call_provider_sync(provider, api_key, api_messages)
        assistant = AIChatMessage(role="assistant", content=content)
        return self._finalize(session_id, history, request.messages, assistant, enriched, usage=usage)

    def chat_stream(self, request: AIChatRequest) -> Iterator[str]:
        session_id, history, enriched, api_messages, provider, api_key = self._prepare_request(request)
        if provider is None or not api_key:
            text = self._fallback_reply(enriched, self._last_user_text(request))
            yield self._sse({"type": "delta", "content": text})
            assistant = AIChatMessage(role="assistant", content=text)
            response = self._finalize(session_id, history, request.messages, assistant, enriched, usage=None)
            yield self._sse({"type": "done", "session_id": response.session_id, "message": response.message.model_dump(), "citations": response.citations})
            return

        chunks: list[str] = []
        usage: AIChatUsage | None = None
        response: AIChatResponse | None = None
        finalization_attempted = False
        try:
            for event in self._call_provider_stream(provider, api_key, api_messages):
                if event.get("type") == "delta":
                    piece = str(event.get("content", ""))
                    chunks.append(piece)
                    yield self._sse({"type": "delta", "content": piece})
                elif event.get("type") == "usage":
                    usage = AIChatUsage(
                        prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(event.get("completion_tokens", 0) or 0),
                        total_tokens=int(event.get("total_tokens", 0) or 0),
                    )
            content = "".join(chunks).strip() or "（空回复）"
            assistant = AIChatMessage(role="assistant", content=content)
            finalization_attempted = True
            response = self._finalize(session_id, history, request.messages, assistant, enriched, usage=usage)
            yield self._sse(
                {
                    "type": "done",
                    "session_id": response.session_id,
                    "message": response.message.model_dump(),
                    "usage": response.usage.model_dump() if response.usage else None,
                    "citations": response.citations,
                }
            )
        except Exception as exc:
            if isinstance(exc, StatePersistenceError):
                raise
            logger.warning("AI chat stream failed: %s", exc)
            content = "".join(chunks).strip()
            if not content:
                content = f"AI 对话失败: {type(exc).__name__}。请检查 Provider 配置、模型名称与 API Key。"
            assistant = AIChatMessage(role="assistant", content=content)
            finalization_attempted = True
            response = self._finalize(session_id, history, request.messages, assistant, enriched, usage=usage)
            yield self._sse(
                {
                    "type": "done",
                    "session_id": response.session_id,
                    "message": response.message.model_dump(),
                    "usage": response.usage.model_dump() if response.usage else None,
                    "citations": response.citations,
                }
            )
        finally:
            # 客户端中途断开时(GeneratorExit), done 事件可能发不出,
            # 但已接收的 delta 内容必须落盘,否则 session 历史错乱、usage 漏报。
            if response is None and not finalization_attempted:
                content = "".join(chunks).strip() or "（空回复）"
                assistant = AIChatMessage(role="assistant", content=content)
                self._finalize(session_id, history, request.messages, assistant, enriched, usage=usage)

    def get_session(self, session_id: str) -> list[AIChatMessage]:
        with self._lock:
            if session_id in self._sessions:
                return list(self._sessions[session_id])
        disk = load_session(session_id)
        if disk is None:
            return []
        messages = disk.get("messages")
        if not isinstance(messages, list):
            return []
        parsed: list[AIChatMessage] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            content = str(item.get("content", ""))
            if role in ("user", "assistant", "system") and content:
                parsed.append(AIChatMessage(role=role, content=content))
        with self._lock:
            self._sessions[session_id] = parsed
        return parsed

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            self._sessions.pop(session_id, None)
        from .ai_session_store import delete_session

        return delete_session(session_id)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        from .ai_session_store import list_sessions

        return list_sessions(limit=limit)

    def get_usage_totals(self) -> dict[str, int]:
        with self._lock:
            return dict(self._usage_totals)

    def _prepare_request(
        self,
        request: AIChatRequest,
    ) -> tuple[str, list[AIChatMessage], AIChatContext, list[dict[str, str]], AIProviderConfig | None, str]:
        user_messages = [item for item in request.messages if item.role == "user"]
        if not user_messages:
            raise ValueError("AI_CHAT_EMPTY")

        session_id = request.session_id or uuid.uuid4().hex
        history = self.get_session(session_id) if request.session_id else list(self._sessions.get(session_id, []))
        enriched = self._enrich_context(request.context)
        strategy_id = enriched.refs.get("strategy_id") or enriched.payload.get("strategy_id")
        strategy_ids = [str(strategy_id)] if strategy_id else None
        scope: PageScope = enriched.page if enriched.page in {
            "chart",
            "screener",
            "signals",
            "signals_backtest",
            "cross_validate",
            "backtest",
            "strategy",
            "event_judgment",
            "review",
            "trade",
            "portfolio",
            "market_trend",
            "sector_capital",
            "abnormal_movement",
            "sentiment_valuation",
            "settings",
            "ai_records",
            "generic",
        } else "generic"
        playbook_text = build_playbook_text(
            self._registry,
            scope=scope,
            strategy_ids=strategy_ids,
            user_principles=self._load_user_principles(),
            compact=True,
        )
        provider = self._resolve_provider()
        api_key = self._resolve_api_key(provider)
        system_prompt = self._build_system_prompt(playbook_text, enriched)
        api_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for item in history[-10:]:
            api_messages.append({"role": item.role, "content": item.content})
        for item in request.messages:
            api_messages.append({"role": item.role, "content": item.content})
        return session_id, history, enriched, api_messages, provider, api_key

    def _finalize(
        self,
        session_id: str,
        history: list[AIChatMessage],
        request_messages: list[AIChatMessage],
        assistant: AIChatMessage,
        enriched: AIChatContext,
        *,
        usage: AIChatUsage | None,
    ) -> AIChatResponse:
        merged = history + request_messages + [assistant]
        with self._lock:
            self._sessions[session_id] = merged[-20:]
            if session_id not in self._session_usage:
                disk = load_session(session_id) or {}
                old = disk.get('usage_total', {}) if disk.get('usage_scope') == 'session' else {}
                self._session_usage[session_id] = {key: int(old.get(key, 0) or 0) for key in self._usage_totals}
            if usage is not None:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    self._usage_totals[key] = int(self._usage_totals.get(key, 0) or 0) + int(getattr(usage, key, 0) or 0)
                    self._session_usage[session_id][key] += int(getattr(usage, key, 0) or 0)
            session_snapshot = list(self._sessions[session_id])
            save_usage_totals(self._usage_totals)
            # Serialize snapshots and writes together so an older response cannot overwrite a newer save.
            save_session(
                session_id,
                messages=[item.model_dump() for item in session_snapshot],
                usage_total=dict(self._session_usage[session_id]),
                page=enriched.page,
                title=enriched.title,
            )
        return AIChatResponse(
            session_id=session_id,
            message=assistant,
            usage=usage,
            citations=["playbook", "runtime"],
        )

    def _build_system_prompt(self, playbook_text: str, context: AIChatContext) -> str:
        runtime_payload = self._trim_runtime_payload(context.payload)
        runtime = json.dumps(
            {
                "page": context.page,
                "title": context.title,
                "as_of_date": context.as_of_date,
                "symbol": context.symbol,
                "refs": context.refs,
                "payload": runtime_payload,
            },
            ensure_ascii=False,
        )
        base_rules = (
            "你是 Final Trade 内置 A 股分析助手。\n"
            "规则：\n"
            "1. 只依据 PLAYBOOK 与 RUNTIME 回答；缺失数据请明确说明。\n"
            "2. 解释程序逻辑与结果，不给具体买卖指令。\n"
            "3. ai_confidence 是本地结构评分，不是 LLM 结论。\n"
            "4. 使用简体中文，条理清晰。\n"
        )
        page_rules = ""
        if context.page == "sentiment_valuation":
            page_rules = (
                "\n【情绪估值页专用规则 — 必须遵守】\n"
                "当用户问「某票怎么样」「能不能买」「贵不贵」「还能不能涨」等，"
                "一律用 PLAYBOOK.sentiment_valuation 的五因子模型分析，禁止用 DCF/传统价值投资框架替代。\n"
                "回答结构：\n"
                "1) 五因子逐项：当期盈利(亿)、复合增速系数、基准PE、大盘水位系数、情绪溢价\n"
                "2) 计算理论市值 = 盈利×增速×PE×大盘×情绪；若有 RUNTIME.valuation_summary 或 market_quote 优先引用\n"
                "3) 对比实际市值，给出隐含情绪系数及情绪标签（中性/温和溢价/高溢价/极高溢价）\n"
                "4) 判断主要在赚哪个因子的钱：盈利、增速预期、大盘贝塔、情绪\n"
                "5) 区分业绩驱动 vs 纯情绪驱动，提示情绪退潮/预期下修风险\n"
                "6) 产品涨价/供不应求可同时抬升盈利、增速预期与情绪 — 点明是否为该模式\n"
                "7) 不给具体买卖价位；结尾注明仅供研究理解，不构成投资建议\n"
                "若 RUNTIME 缺数据，给出合理假设区间并说明假设依据（行业、稀缺性、涨停活跃度等）。\n"
            )
        return (
            f"{base_rules}{page_rules}\n"
            f"PLAYBOOK:\n{playbook_text}\n\n"
            f"RUNTIME:\n{runtime}\n"
        )

    @staticmethod
    def _trim_runtime_payload(payload: dict[str, Any], max_chars: int = 16000) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
        except TypeError:
            return {"truncated": True, "note": "payload 无法序列化，已省略"}
        if len(encoded) <= max_chars:
            return payload
        return {
            "truncated": True,
            "original_size": len(encoded),
            "preview": encoded[:max_chars],
        }

    def _call_provider_sync(
        self,
        provider: AIProviderConfig,
        api_key: str,
        messages: list[dict[str, str]],
    ) -> tuple[str, AIChatUsage | None]:
        body = {"model": provider.model, "temperature": 0.2, "messages": messages, "stream": False}
        attempts = max(1, int(self._retry_count()) + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=float(self._timeout_sec())) as client:
                    response = client.post(
                        f"{provider.base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=body,
                    )
                response.raise_for_status()
                payload = response.json()
                content = (
                    payload.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if not content:
                    raise ValueError("AI_EMPTY_CONTENT")
                usage_raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                usage = AIChatUsage(
                    prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
                    total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
                )
                return content, usage
            except Exception as exc:
                last_exc = exc
                logger.warning("AI chat attempt %s/%s failed: %s", attempt + 1, attempts, exc)
        raise last_exc or RuntimeError("AI_CHAT_FAILED")

    def _call_provider_stream(
        self,
        provider: AIProviderConfig,
        api_key: str,
        messages: list[dict[str, str]],
    ) -> Iterator[dict[str, Any]]:
        body = {"model": provider.model, "temperature": 0.2, "messages": messages, "stream": True}
        with httpx.Client(timeout=float(self._timeout_sec())) as client:
            with client.stream(
                "POST",
                f"{provider.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if data_text == "[DONE]":
                        break
                    try:
                        payload = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload.get("usage"), dict):
                        usage_raw = payload["usage"]
                        yield {
                            "type": "usage",
                            "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                            "completion_tokens": usage_raw.get("completion_tokens", 0),
                            "total_tokens": usage_raw.get("total_tokens", 0),
                        }
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                    if not isinstance(delta, dict):
                        continue
                    piece = delta.get("content")
                    if piece:
                        yield {"type": "delta", "content": str(piece)}

    @staticmethod
    def _sse(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def _last_user_text(request: AIChatRequest) -> str:
        for item in reversed(request.messages):
            if item.role == "user":
                return item.content
        return ""

    def _fallback_reply(self, context: AIChatContext, question: str) -> str:
        symbol = context.symbol or context.payload.get("symbol") or "—"
        row = context.payload.get("screener_row")
        if isinstance(row, dict):
            return (
                f"当前无法连接 AI Provider。基于本地数据：{symbol} "
                f"趋势={row.get('trend_class')} 阶段={row.get('stage')} "
                f"结构置信度={row.get('ai_confidence')}。"
                f"你的问题「{question}」请在配置 API Key 后重试。"
            )
        metrics = context.payload.get("metrics")
        if isinstance(metrics, dict):
            return (
                f"当前无法连接 AI Provider。回测摘要：收益={metrics.get('total_return')} "
                f"回撤={metrics.get('max_drawdown')} 交易数={metrics.get('trade_count')}。"
                f"你的问题「{question}」请在配置 API Key 后重试。"
            )
        valuation = context.payload.get("valuation_summary")
        if isinstance(valuation, dict):
            outputs = valuation.get("valuation_outputs")
            if isinstance(outputs, dict):
                return (
                    f"当前无法连接 AI Provider。情绪估值摘要："
                    f"理论市值={outputs.get('theoretical_cap_yi')}亿，"
                    f"隐含情绪={outputs.get('implied_sentiment_coef')}。"
                    f"你的问题「{question}」请在配置 API Key 后重试。"
                )
        return f"当前无法连接 AI Provider。你的问题「{question}」请在系统设置中配置 API Key 后重试。"
