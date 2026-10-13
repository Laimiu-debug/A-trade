"""
AI-based stock analysis module.

Integrates with AI providers to generate stock insights, themes,
and rise reasons using web evidence and technical indicators.
"""

import json
import logging
from typing import Any

import httpx

from ..models import (
    AIAnalysisRecord,
    AIProviderConfig,
    ScreenerResult,
    CandlePoint,
)
from .signal_analyzer import WYCKOFF_EVENT_ORDER

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """
    Analyzes stocks using AI providers to generate insights,
    identify themes, and extract rise reasons.
    """

    def __init__(
        self,
        provider: AIProviderConfig | None,
        api_key_resolver,
        web_evidence_collector,
        symbol_name_resolver,
        candles_provider,
        context_builder,
    ):
        """
        Initialize AI analyzer.

        Args:
            provider: AI provider configuration
            api_key_resolver: Function to resolve API keys
            web_evidence_collector: Function to collect web evidence
            symbol_name_resolver: Function to resolve stock names
            candles_provider: Function to get candle data
            context_builder: Function to build AI context
        """
        self.provider = provider
        self._resolve_api_key = api_key_resolver
        self._collect_web_evidence = web_evidence_collector
        self._resolve_symbol_name = symbol_name_resolver
        self._ensure_candles = candles_provider
        self._build_context_text = context_builder

    def analyze_stock(
        self,
        symbol: str,
        row: ScreenerResult | None,
        source_urls: list[str],
    ) -> AIAnalysisRecord | None:
        """
        Analyze a stock using AI provider.

        Args:
            symbol: Stock symbol
            row: Screener result with technical indicators
            source_urls: Configured news source URLs

        Returns:
            AIAnalysisRecord if successful, None otherwise
        """
        if not self._is_provider_ready():
            return None

        try:
            prompt_ctx = self._compose_prompt_context(symbol, row, source_urls)
            response = self._call_ai_api(prompt_ctx["prompt"])

            if not response:
                return None

            return self._parse_ai_response(
                symbol=symbol,
                response=response,
                prompt_ctx=prompt_ctx,
            )
        except Exception as e:
            logger.error(f"AI analysis failed for {symbol}: {e}")
            return None

    def _is_provider_ready(self) -> bool:
        """Check if provider is properly configured."""
        if self.provider is None:
            return False
        if not self.provider.base_url.strip():
            return False
        if not self.provider.model.strip():
            return False
        api_key = self._resolve_api_key(self.provider)
        return bool(api_key)

    def _compose_prompt_context(
        self,
        symbol: str,
        row: ScreenerResult | None,
        source_urls: list[str],
    ) -> dict[str, Any]:
        """
        Compose the full prompt context for AI analysis.

        This is a simplified version - the full implementation would
        include all the web evidence collection and industry analysis.
        """
        # Get basic stock info
        stock_name = self._resolve_symbol_name(symbol, row)
        board_label = self._market_board_label(symbol)

        # Build row features text
        row_text = "no_recent_context"
        if row:
            row_text = (
                f"trend={row.trend_class}, stage={row.stage}, ret={row.ret40:.4f}, "
                f"turnover20={row.turnover20:.4f}, retrace20={row.retrace20:.4f}, "
                f"vol_ratio={row.up_down_volume_ratio:.4f}, theme={row.theme_stage}"
            )

        # Get candles for technical context
        candles = self._ensure_candles(symbol)
        context_text = self._build_context_text(symbol, row)

        # Build prompt
        prompt = self._build_analysis_prompt(
            symbol=symbol,
            stock_name=stock_name,
            board_label=board_label,
            row_text=row_text,
            context_text=context_text,
            candles=candles,
        )

        return {
            "prompt": prompt,
            "symbol": symbol,
            "stock_name": stock_name,
            "board_label": board_label,
        }

    def _build_analysis_prompt(
        self,
        symbol: str,
        stock_name: str,
        board_label: str,
        row_text: str,
        context_text: str,
        candles: list[CandlePoint],
    ) -> str:
        """Build the AI prompt for stock analysis."""
        # Build recent kline snapshot
        recent_kline = self._build_recent_kline(candles, lookback=16)

        prompt = (
            "你是A股短线量价分析助手。只输出 JSON，不要任何解释。\\n"
            "JSON keys 固定为: conclusion, confidence, summary, breakout_date, rise_reasons, trend_bull_type, theme_name。\\n"
            "任务只做两件事：\\n"
            "A) 从候选交易日中选出当前这一轮的起爆日 breakout_date。\\n"
            "B) 给出 1~2 条上涨原因 rise_reasons（优先公司事件，缺失时给行业驱动）。\\n"
            "硬约束：\\n"
            "1) breakout_date 必须从 breakout_candidates 中选择。\\n"
            "2) 起爆日优先满足量价共振：当日涨幅>=4%、当日成交量>=前10日均量1.5倍，且突破近20日高点或结束盘整。\\n"
            "3) 若历史有上一轮炒作且中间有明显盘整，必须选择新一轮起爆日，不得回到旧周期。\\n"
            "4) rise_reasons 每条<=26字，禁止媒体名/网址/其他股票代码。\\n"
            "5) 若个股无明确利好，rise_reasons 第一条必须写行业驱动：...。\\n"
            "6) summary 仅一句话，<=40字。\\n"
            f"symbol={symbol}\\n"
            f"name={stock_name}\\n"
            f"board={board_label}\\n"
            f"features={row_text}\\n"
            f"context={context_text}\\n"
            f"recent_kline=\\n{recent_kline}\\n"
        )
        return prompt

    def _build_recent_kline(self, candles: list[CandlePoint], lookback: int = 16) -> str:
        """Build recent kline snapshot for AI context."""
        if not candles:
            return "no_data"

        segment = candles[-lookback:] if len(candles) > lookback else candles
        lines = []
        for point in segment:
            lines.append(
                f"{point.time} o={point.open:.2f} h={point.high:.2f} "
                f"l={point.low:.2f} c={point.close:.2f} v={int(point.volume)}"
            )
        return "\n".join(lines)

    def _call_ai_api(self, prompt: str) -> dict[str, Any] | None:
        """
        Call the AI provider API.

        Returns parsed JSON response or None if failed.
        """
        if self.provider is None:
            return None

        api_key = self._resolve_api_key(self.provider)
        if not api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": self.provider.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                "temperature": 0.3,
            }

            timeout = httpx.Timeout(30.0, connect=10.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    self.provider.base_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                # Extract content based on response format
                if "choices" in data:
                    # OpenAI format
                    content = data["choices"][0]["message"]["content"]
                elif "output" in data:
                    # Some providers use "output"
                    content = data["output"]
                else:
                    logger.warning(f"Unexpected response format: {data}")
                    return None

                # Parse JSON response
                return json.loads(content)

        except httpx.HTTPStatusError as e:
            logger.error(f"AI API HTTP error: {e.response.status_code} - {e.response.text}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
        except Exception as e:
            logger.error(f"AI API call failed: {e}")

        return None

    def _parse_ai_response(
        self,
        symbol: str,
        response: dict[str, Any],
        prompt_ctx: dict[str, Any],
    ) -> AIAnalysisRecord:
        """Parse AI response into AIAnalysisRecord."""
        conclusion = response.get("conclusion", "")
        confidence = self._extract_confidence(response)
        summary = response.get("summary", "")[:80]  # Limit length
        breakout_date = response.get("breakout_date", "")
        rise_reasons = response.get("rise_reasons", [])
        trend_bull_type = response.get("trend_bull_type", "")
        theme_name = response.get("theme_name", "")

        return AIAnalysisRecord(
            symbol=symbol,
            fetched_at=self._now_datetime(),
            provider=self.provider.provider if self.provider else "",
            conclusion=conclusion,
            confidence=confidence,
            summary=summary,
            breakout_date=breakout_date,
            rise_reasons=rise_reasons[:3],  # Limit to 3
            trend_bull_type=trend_bull_type,
            theme_name=theme_name,
            evidence_urls=prompt_ctx.get("evidence_urls", []),
        )

    def _extract_confidence(self, response: dict[str, Any]) -> float:
        """Extract and validate confidence score from response."""
        confidence = response.get("confidence", 0.5)
        try:
            conf_val = float(confidence)
            return max(0.0, min(1.0, conf_val))
        except (ValueError, TypeError):
            return 0.5

    def _market_board_label(self, symbol: str) -> str:
        """Get market board label for symbol."""
        if symbol.startswith("sh6"):
            return "沪市主板"
        if symbol.startswith("sh68"):
            return "科创板"
        if symbol.startswith("sz0"):
            return "深市主板"
        if symbol.startswith("sz3"):
            return "创业板"
        if symbol.startswith("bj8"):
            return "北交所"
        return "未知"

    @staticmethod
    def _now_datetime() -> str:
        """Get current datetime in ISO format."""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def create_ai_analyzer(
    provider_getter,
    api_key_resolver,
    web_evidence_collector,
    symbol_name_resolver,
    candles_provider,
    context_builder,
) -> AIAnalyzer | None:
    """
    Factory function to create AIAnalyzer.

    Args:
        provider_getter: Function to get active AI provider config
        api_key_resolver: Function to resolve API keys
        web_evidence_collector: Function to collect web evidence
        symbol_name_resolver: Function to resolve stock names
        candles_provider: Function to get candle data
        context_builder: Function to build AI context

    Returns:
        AIAnalyzer instance or None if no active provider
    """
    provider = provider_getter()
    if provider is None:
        return None

    return AIAnalyzer(
        provider=provider,
        api_key_resolver=api_key_resolver,
        web_evidence_collector=web_evidence_collector,
        symbol_name_resolver=symbol_name_resolver,
        candles_provider=candles_provider,
        context_builder=context_builder,
    )
