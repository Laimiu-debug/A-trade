"""AI-assisted parameter change proposals with schema validation."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Callable

import httpx

from ..models import AIChatContext, AIProviderConfig
from .ai_playbook import build_playbook_text
from .strategy_registry import StrategyRegistry

logger = logging.getLogger(__name__)

PROPOSAL_TTL_MINUTES = 15
MAX_CHANGES = 10
BLOCKLIST_TARGETS = {"api_key", "api_key_path", "ai_providers"}

# AI 可写的 app_config 白名单(修复4:从黑名单改为白名单)。
# key = AppConfig 字段名,value = 类型与范围约束。
# 只有这里列出的字段才允许 AI 通过 app_config scope 修改。
# 敏感字段(api_key/api_key_path/ai_providers/tdx_data_path 等)不在白名单内,自动被拒。
APPROVED_APP_CONFIG_FIELDS: dict[str, dict[str, Any]] = {
    "initial_capital": {"minimum": 10000.0, "maximum": 100_000_000.0},
    "return_window_days": {"minimum": 5, "maximum": 60, "type": "integer"},
    "candles_window_bars": {"minimum": 120, "maximum": 5000, "type": "integer"},
    "top_n": {"minimum": 1, "maximum": 500, "type": "integer"},
    "turnover_threshold": {"minimum": 0.0, "maximum": 1.0},
    "amount_threshold": {"minimum": 0.0, "maximum": 1e12},
    "amplitude_threshold": {"minimum": 0.0, "maximum": 1.0},
    "backtest_plateau_workers": {"minimum": 1, "maximum": 32, "type": "integer"},
}

BACKTEST_FORM_FIELDS: dict[str, dict[str, Any]] = {
    "backtest.stop_loss": {"minimum": 0.0, "maximum": 0.5},
    "backtest.take_profit": {"minimum": 0.0, "maximum": 1.5},
    "backtest.trailing_stop_pct": {"minimum": 0.0, "maximum": 0.5},
    "backtest.position_pct": {"minimum": 0.01, "maximum": 1.0},
    "backtest.max_positions": {"minimum": 1, "maximum": 100, "type": "integer"},
    "backtest.max_hold_days": {"minimum": 1, "maximum": 365, "type": "integer"},
    "backtest.min_score": {"minimum": 0.0, "maximum": 100.0},
    "backtest.entry_delay_days": {"minimum": 0, "maximum": 10, "type": "integer"},
    "screener.step4.min_ai_confidence": {"minimum": 0.0, "maximum": 1.0},
    "screener.step4.final_top_n": {"minimum": 1, "maximum": 50, "type": "integer"},
}


class AIParameterProposalService:
    def __init__(
        self,
        registry: StrategyRegistry,
        *,
        resolve_provider: Callable[[], AIProviderConfig | None],
        resolve_api_key: Callable[[AIProviderConfig | None], str],
        enrich_context: Callable[[AIChatContext], AIChatContext],
        load_user_principles: Callable[[], list[str]],
        timeout_sec: Callable[[], float],
        get_config: Callable[[], Any],
        set_config: Callable[[Any], Any],
    ) -> None:
        self._registry = registry
        self._resolve_provider = resolve_provider
        self._resolve_api_key = resolve_api_key
        self._enrich_context = enrich_context
        self._load_user_principles = load_user_principles
        self._timeout_sec = timeout_sec
        self._get_config = get_config
        self._set_config = set_config
        self._proposals: dict[str, dict[str, Any]] = {}
        # 保护 _proposals 的并发读写(FastAPI 同步路由跑在线程池)。
        # 注意:只锁 _proposals 读写,httpx 调用与 set_config 在锁外。
        self._lock = RLock()

    def create_proposal(self, *, user_message: str, context: AIChatContext) -> dict[str, Any]:
        enriched = self._enrich_context(context)
        strategy_id = (
            enriched.refs.get("strategy_id")
            or enriched.payload.get("strategy_id")
            or "wyckoff_trend_v1"
        )
        playbook_text = build_playbook_text(
            self._registry,
            scope=enriched.page,
            strategy_ids=[str(strategy_id)],
            user_principles=self._load_user_principles(),
            compact=True,
        )
        runtime = json.dumps(
            {
                "page": enriched.page,
                "title": enriched.title,
                "refs": enriched.refs,
                "payload": enriched.payload,
            },
            ensure_ascii=False,
        )
        system = (
            "你是 Final Trade 参数助手。根据 RUNTIME 回测/选股结果与用户请求，"
            "输出 JSON 对象，格式："
            '{"summary":"...", "changes":[{"scope":"page_form|strategy_params|app_config",'
            '"target":"backtest.stop_loss", "new_value":0.04, "reason":"...", "risk_level":"low|medium|high"}]} '
            "规则：changes 最多10条；new_value 必须在 schema 内；不要修改 api_key；"
            "scope=page_form 用于回测表单或选股 step 参数；scope=strategy_params 时 target 写 strategy.<id>.<param>。"
            f"\nPLAYBOOK:\n{playbook_text}\nRUNTIME:\n{runtime}\n"
        )
        provider = self._resolve_provider()
        api_key = self._resolve_api_key(provider)
        if provider is None or not api_key:
            return self._heuristic_proposal(user_message, enriched)

        body = {
            "model": provider.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message.strip()},
            ],
        }
        try:
            with httpx.Client(timeout=float(self._timeout_sec())) as client:
                response = client.post(
                    f"{provider.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
            response.raise_for_status()
            content = (
                response.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            parsed = self._parse_json_block(content)
        except Exception as exc:
            logger.warning("parameter proposal LLM failed: %s", exc)
            return self._heuristic_proposal(user_message, enriched)

        if not parsed:
            return self._heuristic_proposal(user_message, enriched)
        return self._store_proposal(enriched, parsed, user_message)

    def apply_proposal(
        self,
        proposal_id: str,
        *,
        change_ids: list[str],
        confirm_high_risk: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._proposals.get(proposal_id)
        if record is None:
            raise ValueError("AI_PROPOSAL_NOT_FOUND")
        expires_at = datetime.fromisoformat(str(record["expires_at"]))
        if datetime.now() > expires_at:
            with self._lock:
                self._proposals.pop(proposal_id, None)
            raise ValueError("AI_PROPOSAL_EXPIRED")

        selected = {item["change_id"]: item for item in record.get("changes", [])}
        applied: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for change_id in change_ids:
            change = selected.get(change_id)
            if change is None:
                rejected.append({"change_id": change_id, "reason": "变更项不存在"})
                continue
            if change.get("risk_level") == "high" and not confirm_high_risk:
                rejected.append({"change_id": change_id, "reason": "高风险变更需二次确认"})
                continue
            if change.get("validation_error"):
                rejected.append({"change_id": change_id, "reason": change["validation_error"]})
                continue
            scope = str(change.get("scope", ""))
            if scope == "app_config":
                try:
                    self._apply_app_config(change)
                    applied.append(change)
                except Exception as exc:
                    rejected.append({"change_id": change_id, "reason": str(exc)})
            elif scope in ("page_form", "strategy_params"):
                # 后端无法直接写回前端表单/策略运行时参数。
                # 标记 pending_frontend=True,前端据此写回对应表单,而非显示"已应用"假象。
                applied.append({**change, "pending_frontend": True})
            else:
                rejected.append({"change_id": change_id, "reason": f"不支持的 scope: {scope}"})

        return {"applied": applied, "rejected": rejected, "proposal_id": proposal_id}

    def _apply_app_config(self, change: dict[str, Any]) -> None:
        target = str(change.get("target", ""))
        if not target.startswith("app_config."):
            raise ValueError("无效 app_config target")
        field = target.split(".", 1)[1]
        # 修复4:白名单校验。只有 APPROVED_APP_CONFIG_FIELDS 列出的字段才允许 AI 修改。
        spec = APPROVED_APP_CONFIG_FIELDS.get(field)
        if spec is None:
            raise ValueError(f"AI 不可修改配置字段: {field}(仅允许白名单内字段)")
        # 兜底:敏感字段即使误入白名单也拒绝
        if field in BLOCKLIST_TARGETS:
            raise ValueError("禁止修改敏感字段")
        new_value = change.get("new_value")
        error = self._validate_number(new_value, spec)
        if error:
            raise ValueError(error)
        config = self._get_config()
        data = config.model_dump()
        data[field] = new_value
        updated = type(config).model_validate(data)
        self._set_config(updated)

    def _heuristic_proposal(self, user_message: str, context: AIChatContext) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        text = user_message.lower()
        payload = context.payload
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        max_dd = float(metrics.get("max_drawdown", 0) or 0)
        if "止损" in user_message or "stop" in text:
            current = 0.05
            req = payload.get("run_request")
            if isinstance(req, dict) and req.get("stop_loss") is not None:
                current = float(req["stop_loss"])
            suggested = round(min(0.08, max(0.02, current - 0.01 if max_dd < -0.15 else current)), 3)
            changes.append(
                self._build_change(
                    scope="page_form",
                    target="backtest.stop_loss",
                    old_value=current,
                    new_value=suggested,
                    reason="回撤偏大时适度收紧止损",
                    risk_level="low",
                )
            )
        parsed = {"summary": "基于规则生成的参数建议（AI Provider 不可用或未返回结构化结果）", "changes": changes}
        return self._store_proposal(context, parsed, user_message)

    def _store_proposal(self, context: AIChatContext, parsed: dict[str, Any], user_message: str) -> dict[str, Any]:
        proposal_id = f"pp_{uuid.uuid4().hex[:12]}"
        raw_changes = parsed.get("changes") if isinstance(parsed.get("changes"), list) else []
        validated: list[dict[str, Any]] = []
        for item in raw_changes[:MAX_CHANGES]:
            if not isinstance(item, dict):
                continue
            change = self._normalize_change(item, context)
            if change is not None:
                validated.append(change)
        expires_at = (datetime.now() + timedelta(minutes=PROPOSAL_TTL_MINUTES)).isoformat(timespec="seconds")
        record = {
            "proposal_id": proposal_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": expires_at,
            "context_page": context.page,
            "summary": str(parsed.get("summary") or user_message)[:400],
            "changes": validated,
        }
        with self._lock:
            self._proposals[proposal_id] = record
        return record

    def _normalize_change(self, item: dict[str, Any], context: AIChatContext) -> dict[str, Any] | None:
        scope = str(item.get("scope", "page_form")).strip()
        target = str(item.get("target", "")).strip()
        if not target:
            return None
        if any(block in target for block in BLOCKLIST_TARGETS):
            return None
        new_value = item.get("new_value")
        old_value = item.get("old_value")
        error = self._validate_change(scope, target, new_value, context)
        change_id = uuid.uuid4().hex[:10]
        return {
            "change_id": change_id,
            "scope": scope,
            "target": target,
            "path": target.split("."),
            "old_value": old_value,
            "new_value": new_value,
            "reason": str(item.get("reason", ""))[:200],
            "risk_level": str(item.get("risk_level", "low")),
            "validation_error": error,
        }

    def _validate_change(
        self,
        scope: str,
        target: str,
        new_value: Any,
        context: AIChatContext,
    ) -> str | None:
        if scope == "page_form":
            spec = BACKTEST_FORM_FIELDS.get(target)
            if spec is None and not target.startswith("screener."):
                return f"未知 page_form 字段: {target}"
            if spec is not None:
                return self._validate_number(new_value, spec)
            return None
        if scope == "strategy_params":
            match = re.match(r"strategy\.([^.]+)\.(.+)", target)
            if not match:
                return "strategy_params target 格式应为 strategy.<id>.<param>"
            strategy_id, param_key = match.group(1), match.group(2)
            normalized = self._registry.normalize_params(strategy_id, {param_key: new_value})
            if param_key not in normalized:
                return f"参数 {param_key} 未通过 schema 校验"
            return None
        if scope == "app_config":
            field = target.split(".", 1)[-1]
            # 修复4:白名单校验,与 _apply_app_config 保持一致
            if field in BLOCKLIST_TARGETS:
                return "禁止修改敏感配置"
            spec = APPROVED_APP_CONFIG_FIELDS.get(field)
            if spec is None:
                return f"AI 不可修改配置字段: {field}(仅允许白名单内字段)"
            return self._validate_number(new_value, spec)
        return f"未知 scope: {scope}"

    @staticmethod
    def _validate_number(value: Any, spec: dict[str, Any]) -> str | None:
        try:
            if spec.get("type") == "integer":
                parsed = int(value)
            else:
                parsed = float(value)
        except (TypeError, ValueError):
            return "数值格式无效"
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if minimum is not None and parsed < float(minimum):
            return f"低于下限 {minimum}"
        if maximum is not None and parsed > float(maximum):
            return f"超过上限 {maximum}"
        return None

    def _build_change(
        self,
        *,
        scope: str,
        target: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        risk_level: str,
    ) -> dict[str, Any]:
        return {
            "change_id": uuid.uuid4().hex[:10],
            "scope": scope,
            "target": target,
            "path": target.split("."),
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
            "risk_level": risk_level,
            "validation_error": self._validate_change(scope, target, new_value, AIChatContext(page="generic", title="")),
        }

    @staticmethod
    def _parse_json_block(content: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]+\}", content)
            if not match:
                return None
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
