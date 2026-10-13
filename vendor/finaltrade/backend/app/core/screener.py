"""
Stock screening business logic.

Implements the core screening algorithm that filters stocks
based on technical indicators, volume patterns, and trends.
"""

import logging
from datetime import datetime
from typing import Any

from ..models import (
    CandlePoint,
    ScreenerParams,
    ScreenerResult,
    ScreenerMode,
    ScreenerRunDetail,
    ScreenerStepPools,
    ScreenerStepSummary,
    Stage,
    ThemeStage,
    TrendClass,
)
from .signal_analyzer import SignalAnalyzer

logger = logging.getLogger(__name__)

# Constants
THEME_STAGES: tuple[ThemeStage, ThemeStage, ThemeStage] = ("发酵中", "高潮", "退潮")


class ScreenerEngine:
    """
    Core stock screening engine.

    Implements the multi-step filtering process:
    1. Initial pool filtering
    2. Technical indicator analysis
    3. Trend classification
    4. Risk assessment
    """

    def __init__(
        self,
        candles_provider,
        symbol_name_resolver,
    ):
        """
        Initialize screener engine.

        Args:
            candles_provider: Function to get candles for a symbol
            symbol_name_resolver: Function to resolve stock names
        """
        self._ensure_candles = candles_provider
        self._resolve_symbol_name = symbol_name_resolver

    def run_screener(
        self,
        params: ScreenerParams,
        input_pool: list[dict[str, str]],
    ) -> ScreenerRunDetail:
        """
        Run the stock screener with given parameters.

        Args:
            params: Screening parameters
            input_pool: Initial pool of stocks to screen

        Returns:
            ScreenerRunDetail with results and metadata
        """
        import time
        import uuid
        from ..store import InMemoryStore

        run_id = str(uuid.uuid4())
        created_at = InMemoryStore._now_datetime()

        # Step pools tracking
        step_pools = ScreenerStepPools(
            initial=[],
            after_volume_filter=[],
            after_technical_filter=[],
            after_trend_class=[],
            after_risk_filter=[],
        )

        results = []
        processed_symbols = set()

        for stock in input_pool:
            symbol = stock["symbol"]

            # Skip duplicates
            if symbol in processed_symbols:
                continue
            processed_symbols.add(symbol)

            try:
                # Build result row
                row = self.build_screener_result(
                    symbol=symbol,
                    as_of_date=params.as_of_date,
                )

                if row is None:
                    continue

                # Apply multi-step filtering
                if not self._pass_step_1_volume(row, params):
                    continue

                step_pools.after_volume_filter.append(symbol)

                if not self._pass_step_2_technical(row, params):
                    continue

                step_pools.after_technical_filter.append(symbol)

                if not self._pass_step_3_trend(row, params):
                    continue

                step_pools.after_trend_class.append(symbol)

                if not self._pass_step_4_risk(row, params):
                    continue

                step_pools.after_risk_filter.append(symbol)

                results.append(row)

                # Apply top-N limit
                if params.top_n and len(results) >= params.top_n:
                    break

            except Exception as e:
                logger.warning(f"Failed to screen {symbol}: {e}")
                continue

        # Create run detail
        run = ScreenerRunDetail(
            run_id=run_id,
            created_at=created_at,
            params=params,
            results=results,
            step_pools=step_pools,
            step_summaries=self._build_step_summaries(step_pools),
        )

        return run

    def build_screener_result(
        self,
        symbol: str,
        as_of_date: str | None = None,
    ) -> ScreenerResult | None:
        """
        Build a complete screener result for a symbol.

        This is the core analysis method that calculates all
        technical indicators and metrics.

        Args:
            symbol: Stock symbol
            as_of_date: Optional as-of date

        Returns:
            ScreenerResult or None if analysis fails
        """
        candles = self._ensure_candles(symbol)
        if not candles:
            return None

        # Get symbol name
        name = self._resolve_symbol_name(symbol)
        if name is None:
            name = symbol

        # Calculate metrics
        latest = candles[-1]
        latest_price = latest.close
        prev = candles[-2] if len(candles) > 1 else latest
        day_change = latest_price - prev.close
        day_change_pct = day_change / prev.close if prev.close > 0 else 0

        # Calculate all technical indicators
        ret40, turnover20, amplitude20 = self._calc_return_metrics(candles)
        up_down_volume_ratio, pullback_days, pullback_volume_ratio = self._calc_volume_metrics(candles)
        retrace20 = self._calc_retrace(candles, 20)

        # Classify trend and stage
        trend_class, stage = self._classify_trend_and_stage(ret40, turnover20, candles)

        # Determine theme stage
        theme_stage = self._infer_theme_stage(ret40, candles)

        # Calculate score
        score = self._calc_score(
            trend_class=trend_class,
            ret40=ret40,
            turnover20=turnover20,
            up_down_volume_ratio=up_down_volume_ratio,
            retrace20=retrace20,
        )

        return ScreenerResult(
            symbol=symbol,
            name=name,
            latest_price=latest_price,
            day_change=day_change,
            day_change_pct=day_change_pct,
            score=score,
            ret40=ret40,
            turnover20=turnover20,
            up_down_volume_ratio=up_down_volume_ratio,
            pullback_days=pullback_days,
            pullback_volume_ratio=pullback_volume_ratio,
            retrace20=retrace20,
            amplitude20=amplitude20,
            trend_class=trend_class,
            stage=stage,
            theme_stage=theme_stage,
            labels=[],
            reject_reasons=[],
            degraded=False,
            degraded_reason=None,
        )

    def _pass_step_1_volume(self, row: ScreenerResult, params: ScreenerParams) -> bool:
        """Filter step 1: Volume and turnover requirements."""
        if params.mode == "strict":
            return row.turnover20 >= params.min_turnover
        return True

    def _pass_step_2_technical(self, row: ScreenerResult, params: ScreenerParams) -> bool:
        """Filter step 2: Technical indicator requirements."""
        # Skip if too volatile
        if row.amplitude20 > 0.15:
            return False
        return True

    def _pass_step_3_trend(self, row: ScreenerResult, params: ScreenerParams) -> bool:
        """Filter step 3: Trend classification requirements."""
        if params.mode == "strict":
            # Only accept A and A_B trends
            return row.trend_class in ("A", "A_B")
        return True

    def _pass_step_4_risk(self, row: ScreenerResult, params: ScreenerParams) -> bool:
        """Filter step 4: Risk assessment."""
        # Skip if significant retrace
        if row.retrace20 > 0.15:
            row.reject_reasons.append("significant_retrace")
            return False
        return True

    def _calc_return_metrics(self, candles: list[CandlePoint]) -> tuple[float, float, float]:
        """Calculate return-based metrics."""
        if len(candles) < 40:
            return 0.0, 0.0, 0.0

        closes = [c.close for c in candles]
        volumes = [max(0, int(c.volume)) for c in candles]

        # 40-day return
        ret40 = (closes[-1] - closes[-40]) / closes[-40] if len(closes) >= 40 else 0.0

        # 20-day turnover (sum of volumes / 20)
        turnover20 = sum(volumes[-20:]) / 20

        # 20-day amplitude (max - min) / avg
        highs = [c.high for c in candles[-20:]]
        lows = [c.low for c in candles[-20:]]
        amplitude20 = (max(highs) - min(lows)) / closes[-1] if closes[-1] > 0 else 0.0

        return ret40, turnover20, amplitude20

    def _calc_volume_metrics(self, candles: list[CandlePoint]) -> tuple[float, int, float]:
        """Calculate volume-based metrics."""
        if len(candles) < 20:
            return 1.0, 0, 1.0

        volumes = [max(0, int(c.volume)) for c in candles]
        closes = [c.close for c in candles]

        # Up/down volume ratio
        up_volume = 0
        for i, v in enumerate(volumes[-20:]):
            idx = len(candles) - 20 + i
            if idx > 0 and closes[idx] >= closes[idx - 1]:
                up_volume += v
        down_volume = sum(volumes[-20:]) - up_volume
        up_down_volume_ratio = up_volume / down_volume if down_volume > 0 else 1.0

        # Pullback metrics: 从近20日最高收盘价到当前的交易日距离
        pb_window = min(20, len(candles))
        pb_closes = [float(candles[len(candles) - pb_window + i].close) for i in range(pb_window)]
        pb_peak_offset = max(range(len(pb_closes)), key=lambda i: pb_closes[i])
        pullback_days = len(pb_closes) - 1 - pb_peak_offset
        pullback_volume_sum = 0.0
        if pullback_days > 0:
            for i in range(len(candles) - pullback_days, len(candles)):
                if i > 0 and candles[i].close < candles[i - 1].close:
                    pullback_volume_sum += volumes[i]

        avg_volume = sum(volumes[-20:]) / 20
        pullback_volume_ratio = pullback_volume_sum / avg_volume if avg_volume > 0 else 1.0

        return up_down_volume_ratio, pullback_days, pullback_volume_ratio

    def _calc_retrace(self, candles: list[CandlePoint], window: int) -> float:
        """Calculate retracement from recent high."""
        if len(candles) < window:
            return 0.0

        closes = [c.close for c in candles]
        recent_high = max(closes[-window:])
        current = closes[-1]

        return (recent_high - current) / recent_high if recent_high > 0 else 0.0

    def _classify_trend_and_stage(
        self,
        ret40: float,
        turnover20: float,
        candles: list[CandlePoint],
    ) -> tuple[TrendClass, Stage]:
        """Classify trend class and stage."""
        # Simple classification based on return and turnover
        if ret40 > 0.30 and turnover20 > 0.08:
            trend_class: TrendClass = "A"
        elif ret40 > 0.15 and turnover20 > 0.06:
            trend_class = "A_B"
        elif ret40 > 0.05:
            trend_class = "B"
        else:
            trend_class = "C"

        # Determine stage based on recent price action
        if len(candles) < 20:
            stage: Stage = "Early"
        else:
            closes = [c.close for c in candles[-20:]]
            if closes[-1] > max(closes[:-1]) * 0.98:
                stage = "Late"
            elif closes[-1] > closes[0] * 1.05:
                stage = "Mid"
            else:
                stage = "Early"

        return trend_class, stage

    def _infer_theme_stage(self, ret40: float, candles: list[CandlePoint]) -> ThemeStage:
        """Infer theme stage based on price momentum."""
        if len(candles) < 10:
            return "发酵中"

        # Check recent acceleration
        recent_ret = (candles[-1].close - candles[-5].close) / candles[-5].close
        earlier_ret = (candles[-5].close - candles[-10].close) / candles[-10].close if len(candles) >= 10 else 0

        if recent_ret > earlier_ret * 1.5 and ret40 > 0.20:
            return "高潮"
        elif recent_ret < 0:
            return "退潮"
        else:
            return "发酵中"

    def _calc_score(
        self,
        trend_class: TrendClass,
        ret40: float,
        turnover20: float,
        up_down_volume_ratio: float,
        retrace20: float,
    ) -> float:
        """Calculate overall score (0-100)."""
        # Base score by trend class
        trend_scores = {"A": 85, "A_B": 75, "B": 60, "C": 40}
        score = trend_scores.get(trend_class, 50)

        # Adjust for return
        score += min(ret40 * 50, 20)

        # Adjust for turnover quality
        if turnover20 > 0.08:
            score += 5

        # Adjust for volume quality
        if up_down_volume_ratio > 1.2:
            score += 5

        # Penalize for retrace
        score -= retrace20 * 50

        return max(0, min(100, score))

    def _build_step_summaries(self, pools: ScreenerStepPools) -> list[ScreenerStepSummary]:
        """Build step summaries from pools."""
        return [
            ScreenerStepSummary(
                step_name="initial_pool",
                count=len(pools.initial),
            ),
            ScreenerStepSummary(
                step_name="after_volume_filter",
                count=len(pools.after_volume_filter),
            ),
            ScreenerStepSummary(
                step_name="after_technical_filter",
                count=len(pools.after_technical_filter),
            ),
            ScreenerStepSummary(
                step_name="after_trend_class",
                count=len(pools.after_trend_class),
            ),
            ScreenerStepSummary(
                step_name="after_risk_filter",
                count=len(pools.after_risk_filter),
            ),
        ]


def create_screener_engine(
    candles_provider,
    symbol_name_resolver,
) -> ScreenerEngine:
    """
    Factory function to create ScreenerEngine.

    Args:
        candles_provider: Function to get candles for a symbol
        symbol_name_resolver: Function to resolve stock names

    Returns:
        ScreenerEngine instance
    """
    return ScreenerEngine(
        candles_provider=candles_provider,
        symbol_name_resolver=symbol_name_resolver,
    )
