from __future__ import annotations

import math
from typing import Any, Protocol

from ..models import BacktestRunRequest, CandlePoint, ScreenerResult, SignalResult


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


class StrategyPlugin(Protocol):
    strategy_id: str

    def build_universe(
        self,
        *,
        candidates: list[ScreenerResult],
        params: dict[str, Any],
        mode: str,
    ) -> list[ScreenerResult]:
        ...

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        ...

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        ...

    def entry_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        ...

    def exit_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        ...


class BaseStrategyPlugin:
    strategy_id = "base"

    def build_universe(
        self,
        *,
        candidates: list[ScreenerResult],
        params: dict[str, Any],
        mode: str,
    ) -> list[ScreenerResult]:
        _ = params, mode
        return list(candidates)

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row, snapshot, params
        return True

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = signal, row, params
        return float(fallback_score)

    def entry_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        _ = params
        return payload

    def exit_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        _ = params
        return payload


class WyckoffTrendPlugin(BaseStrategyPlugin):
    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = str(strategy_id).strip() or "wyckoff_trend_v1"


class ScoreOnlyRankPlugin(BaseStrategyPlugin):
    strategy_id = "score_only_rank_v1"

    @staticmethod
    def _safe_float(params: dict[str, Any], key: str, fallback: float) -> float:
        try:
            value = float(params.get(key, fallback))
        except Exception:
            return float(fallback)
        if not math.isfinite(value):
            return float(fallback)
        return float(value)

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row
        min_score = self._safe_float(params, "min_score", 55.0)
        try:
            entry_quality_score = float(snapshot.get("entry_quality_score", 0.0) or 0.0)
        except Exception:
            entry_quality_score = 0.0
        if not math.isfinite(entry_quality_score):
            entry_quality_score = 0.0
        return entry_quality_score >= float(min_score)

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = row, params
        try:
            quality = float(signal.entry_quality_score)
        except Exception:
            quality = float(fallback_score)
        if not math.isfinite(quality):
            quality = float(fallback_score)
        return _clamp_score(quality)


class ThsMainRetailSignalPlugin(BaseStrategyPlugin):
    def __init__(self, strategy_id: str, *, trigger_key: str) -> None:
        self.strategy_id = str(strategy_id).strip()
        self._trigger_key = str(trigger_key).strip()

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row, params
        indicator = snapshot.get("ths_main_retail_signal")
        if not isinstance(indicator, dict):
            return False
        return bool(indicator.get(self._trigger_key, False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = row, params
        try:
            quality = float(signal.entry_quality_score)
        except Exception:
            quality = float(fallback_score)
        if not math.isfinite(quality):
            quality = float(fallback_score)
        return _clamp_score(quality)


class MatrixSignalPlugin(BaseStrategyPlugin):
    """矩阵信号策略插件 — 将 backtest_signal_matrix 的 S1-S9 向量化逻辑
    适配为逐票评估接口，使其可在策略中心统一管理。

    S1: 波动收敛 (ATR ratio < threshold)
    S2: 量能萎缩 (vol_ma10/vol_ma60 < threshold)
    S3: 40日涨幅 top_n (由 build_universe 预筛)
    S4: 横盘整理 (振幅收敛 + 价格靠近MA20)
    S5: 突破买入 (创20日新高 + 放量)
    S6: 均线回踩买入 (站上MA10 + 高于10日低点)
    S7: 多头排列 (MA10 > MA20 > MA60)
    S8: 破位卖出 (破20日低点 或 跌破MA20*0.97)
    S9: 放量下跌 (跌破MA10 + 放量 + 40日负收益)
    """

    strategy_id = "matrix_signal_v1"

    @staticmethod
    def _safe_float(params: dict[str, Any], key: str, fallback: float) -> float:
        try:
            value = float(params.get(key, fallback))
        except Exception:
            return float(fallback)
        if not __import__("math").isfinite(value):
            return float(fallback)
        return float(value)

    def build_universe(
        self,
        *,
        candidates: list[ScreenerResult],
        params: dict[str, Any],
        mode: str,
    ) -> list[ScreenerResult]:
        """池筛选：pool_score >= min_pool_score 才入池。
        pool_score = S1 + S2 + S3 + S4 (各 0/1)。
        S3 由 ret40 排名 top_n 近似。
        """
        _ = mode
        atr_ratio_max = self._safe_float(params, "atr_ratio_max", 0.7)
        vol_ratio_max = self._safe_float(params, "vol_ratio_max", 0.6)
        sideways_range_max = self._safe_float(params, "sideways_range_max", 0.18)
        near_ma20_max = self._safe_float(params, "near_ma20_max", 0.06)
        min_pool_score = int(self._safe_float(params, "min_pool_score", 2))
        ret40_top_n = int(self._safe_float(params, "ret40_top_n", 500))

        # 先按 ret40 降序取 top_n 作为 S3 候选
        sorted_by_ret40 = sorted(candidates, key=lambda r: float(r.ret40), reverse=True)
        s3_symbols: set[str] = set()
        for i, row in enumerate(sorted_by_ret40):
            if i >= ret40_top_n:
                break
            s3_symbols.add(row.symbol)

        out: list[ScreenerResult] = []
        for row in candidates:
            s1 = 1 if float(row.retrace20) < atr_ratio_max else 0
            s2 = 1 if float(row.vol_slope20) < vol_ratio_max else 0
            s3 = 1 if row.symbol in s3_symbols else 0
            s4 = 1 if (float(row.retrace20) < sideways_range_max and float(row.price_vs_ma20) < near_ma20_max) else 0
            pool_score = s1 + s2 + s3 + s4
            if pool_score >= min_pool_score:
                out.append(row)
        return out

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        """买入信号：(S5 | S6) & in_pool。
        S5: 突破20日高点 + 放量 (近似: ret40 > 0 且 up_down_volume_ratio > breakout_vol_ratio)
        S6: 均线回踩 (近似: price_vs_ma20 接近0 且 pullback_days <= max_pullback_days)
        """
        _ = snapshot
        breakout_vol_ratio = self._safe_float(params, "breakout_vol_ratio", 1.2)
        max_pullback_days = int(self._safe_float(params, "max_pullback_days", 3))

        # S5: 突破买入 — 涨幅为正 + 量比达标
        s5 = float(row.ret40) > 0 and float(row.up_down_volume_ratio) >= breakout_vol_ratio
        # S6: 回踩买入 — 价格靠近MA20 + 回调天数有限
        s6 = abs(float(row.price_vs_ma20)) < 0.06 and int(row.pullback_days) <= max_pullback_days
        return s5 or s6

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        """评分逻辑与矩阵引擎一致：
        raw_score = pool_score + S5*2.0 + S6*1.5 + S7*1.0
        score = clamp(raw_score / 8.5 * 100, 0, 100)
        """
        atr_ratio_max = self._safe_float(params, "atr_ratio_max", 0.7)
        vol_ratio_max = self._safe_float(params, "vol_ratio_max", 0.6)
        sideways_range_max = self._safe_float(params, "sideways_range_max", 0.18)
        near_ma20_max = self._safe_float(params, "near_ma20_max", 0.06)
        breakout_vol_ratio = self._safe_float(params, "breakout_vol_ratio", 1.2)
        max_pullback_days = int(self._safe_float(params, "max_pullback_days", 3))

        s1 = 1.0 if float(row.retrace20) < atr_ratio_max else 0.0
        s2 = 1.0 if float(row.vol_slope20) < vol_ratio_max else 0.0
        s4 = 1.0 if (float(row.retrace20) < sideways_range_max and float(row.price_vs_ma20) < near_ma20_max) else 0.0
        # S3 在 rank 阶段近似为 ret40 > 0
        s3 = 1.0 if float(row.ret40) > 0 else 0.0
        pool_score = s1 + s2 + s3 + s4

        s5 = 1.0 if (float(row.ret40) > 0 and float(row.up_down_volume_ratio) >= breakout_vol_ratio) else 0.0
        s6 = 1.0 if (abs(float(row.price_vs_ma20)) < 0.06 and int(row.pullback_days) <= max_pullback_days) else 0.0
        # S7: 多头排列 — 近似: MA10 > MA20 天数足够
        s7 = 1.0 if int(row.ma10_above_ma20_days) >= 5 else 0.0

        raw_score = pool_score + s5 * 2.0 + s6 * 1.5 + s7 * 1.0
        score = max(0.0, min(100.0, raw_score / 8.5 * 100.0))
        return score


class RelativeStrengthBreakoutPlugin(BaseStrategyPlugin):
    strategy_id = "relative_strength_breakout_v1"

    @staticmethod
    def _safe_float(params: dict[str, Any], key: str, fallback: float) -> float:
        try:
            return float(params.get(key, fallback))
        except Exception:
            return float(fallback)

    def build_universe(
        self,
        *,
        candidates: list[ScreenerResult],
        params: dict[str, Any],
        mode: str,
    ) -> list[ScreenerResult]:
        _ = mode
        min_ret40 = self._safe_float(params, "min_ret40", 0.12)
        max_retrace20 = self._safe_float(params, "max_retrace20", 0.22)
        min_up_down_volume_ratio = self._safe_float(params, "min_up_down_volume_ratio", 1.15)
        min_vol_slope20 = self._safe_float(params, "min_vol_slope20", 0.02)
        min_ai_confidence = self._safe_float(params, "min_ai_confidence", 0.0)

        out: list[ScreenerResult] = []
        for row in candidates:
            if float(row.ret40) < min_ret40:
                continue
            if float(row.retrace20) > max_retrace20:
                continue
            if float(row.up_down_volume_ratio) < min_up_down_volume_ratio:
                continue
            if float(row.vol_slope20) < min_vol_slope20:
                continue
            if float(row.ai_confidence) < min_ai_confidence:
                continue
            out.append(row)
        return out

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = snapshot
        min_ret40 = self._safe_float(params, "min_ret40", 0.12)
        max_retrace20 = self._safe_float(params, "max_retrace20", 0.22)
        min_up_down_volume_ratio = self._safe_float(params, "min_up_down_volume_ratio", 1.15)
        return (
            float(row.ret40) >= min_ret40
            and float(row.retrace20) <= max_retrace20
            and float(row.up_down_volume_ratio) >= min_up_down_volume_ratio
        )

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = fallback_score
        w_health = self._safe_float(params, "rank_weight_health", 0.25)
        w_event = self._safe_float(params, "rank_weight_event", 0.25)
        w_strength = self._safe_float(params, "rank_weight_strength", 0.30)
        w_volume = self._safe_float(params, "rank_weight_volume", 0.10)
        w_structure = self._safe_float(params, "rank_weight_structure", 0.10)
        weight_sum = max(0.01, w_health + w_event + w_strength + w_volume + w_structure)

        strength_score = _clamp_score(float(row.ret40) / 0.40 * 100.0)
        volume_score = _clamp_score((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        structure_score = _clamp_score(
            100.0
            - abs(float(row.retrace20) - 0.12) / 0.20 * 100.0
            - max(0.0, float(row.pullback_volume_ratio) - 0.92) * 100.0
        )
        score = (
            float(signal.health_score) * w_health
            + float(signal.event_score) * w_event
            + strength_score * w_strength
            + volume_score * w_volume
            + structure_score * w_structure
        ) / weight_sum
        return _clamp_score(score)


def _rolling_mean(values: list[float], window: int) -> list[float]:
    out = [math.nan] * len(values)
    if window <= 0 or len(values) < window:
        return out
    running = 0.0
    for idx, value in enumerate(values):
        running += float(value)
        if idx >= window:
            running -= float(values[idx - window])
        if idx >= window - 1:
            out[idx] = running / float(window)
    return out


def _safe_ratio(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if not math.isfinite(float(numerator)):
        return float(fallback)
    if not math.isfinite(float(denominator)) or abs(float(denominator)) <= 1e-9:
        return float(fallback)
    return float(numerator) / float(denominator)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return float(parsed)


def calculate_wulong_cluster_signal(candles: list[CandlePoint]) -> dict[str, Any]:
    periods = (5, 10, 20, 30, 60)
    trigger_date = str(candles[-1].time) if candles else ""
    base: dict[str, Any] = {
        "trigger_date": trigger_date,
        "has_data": False,
        "bullish_alignment": False,
        "close_above_all_mas": False,
        "rising_ma_count": 0,
        "current_spread_pct": 0.0,
        "convergence_spread_pct": math.nan,
        "pre_convergence_max_spread_pct": 0.0,
        "convergence_offset_days": -1,
        "spread_expansion_multiple": 0.0,
        "volume_ratio_20": 0.0,
        "breakout_ratio_pct": 0.0,
        "daily_return_pct": 0.0,
        "ma_values": {},
    }
    if len(candles) < max(periods) + 5:
        return base

    closes = [max(0.0, float(point.close)) for point in candles]
    highs = [max(0.0, float(point.high)) for point in candles]
    volumes = [max(0.0, float(point.volume)) for point in candles]
    ma_series = {period: _rolling_mean(closes, period) for period in periods}
    vol_ma20_series = _rolling_mean(volumes, 20)
    last_idx = len(candles) - 1

    current_ma_values: dict[str, float] = {}
    current_mas: list[float] = []
    for period in periods:
        current_value = ma_series[period][last_idx]
        if not math.isfinite(current_value):
            return base
        current_mas.append(float(current_value))
        current_ma_values[f"ma{period}"] = round(float(current_value), 6)

    current_close = max(0.01, closes[last_idx])
    current_spread_pct = _safe_ratio(max(current_mas) - min(current_mas), current_close)
    close_above_all_mas = current_close > max(current_mas)
    bullish_alignment = all(current_mas[idx] > current_mas[idx + 1] for idx in range(len(current_mas) - 1))

    rise_probe_offset = min(3, last_idx)
    rising_ma_count = 0
    if rise_probe_offset > 0:
        probe_idx = last_idx - rise_probe_offset
        for period in periods:
            previous_value = ma_series[period][probe_idx]
            current_value = ma_series[period][last_idx]
            if math.isfinite(previous_value) and math.isfinite(current_value) and current_value > previous_value:
                rising_ma_count += 1

    recent_high_start = max(0, last_idx - 20)
    previous_high = max(highs[recent_high_start:last_idx], default=highs[last_idx])
    breakout_ratio_pct = _safe_ratio(current_close - previous_high, max(0.01, previous_high))
    daily_return_pct = (
        _safe_ratio(current_close - closes[last_idx - 1], max(0.01, closes[last_idx - 1]))
        if last_idx > 0
        else 0.0
    )
    volume_ratio_20 = _safe_ratio(volumes[last_idx], vol_ma20_series[last_idx], 0.0)

    search_start = max(max(periods) - 1, last_idx - 12)
    convergence_idx = -1
    convergence_spread_pct = math.nan
    for idx in range(search_start, last_idx):
        mas = [ma_series[period][idx] for period in periods]
        if any(not math.isfinite(value) for value in mas):
            continue
        price = max(0.01, closes[idx])
        spread_pct = _safe_ratio(max(mas) - min(mas), price, math.inf)
        if not math.isfinite(convergence_spread_pct) or spread_pct < convergence_spread_pct:
            convergence_idx = idx
            convergence_spread_pct = spread_pct

    pre_convergence_max_spread_pct = 0.0
    if convergence_idx >= 0:
        pre_start = max(max(periods) - 1, convergence_idx - 15)
        for idx in range(pre_start, convergence_idx):
            mas = [ma_series[period][idx] for period in periods]
            if any(not math.isfinite(value) for value in mas):
                continue
            price = max(0.01, closes[idx])
            spread_pct = _safe_ratio(max(mas) - min(mas), price)
            pre_convergence_max_spread_pct = max(pre_convergence_max_spread_pct, spread_pct)

    spread_expansion_multiple = (
        _safe_ratio(current_spread_pct, max(convergence_spread_pct, 1e-6), 0.0)
        if convergence_idx >= 0 and math.isfinite(convergence_spread_pct)
        else 0.0
    )

    base.update(
        {
            "has_data": True,
            "bullish_alignment": bullish_alignment,
            "close_above_all_mas": close_above_all_mas,
            "rising_ma_count": rising_ma_count,
            "current_spread_pct": round(current_spread_pct, 6),
            "convergence_spread_pct": round(convergence_spread_pct, 6) if math.isfinite(convergence_spread_pct) else math.nan,
            "pre_convergence_max_spread_pct": round(pre_convergence_max_spread_pct, 6),
            "convergence_offset_days": last_idx - convergence_idx if convergence_idx >= 0 else -1,
            "spread_expansion_multiple": round(spread_expansion_multiple, 6),
            "volume_ratio_20": round(volume_ratio_20, 6),
            "breakout_ratio_pct": round(breakout_ratio_pct, 6),
            "daily_return_pct": round(daily_return_pct, 6),
            "ma_values": current_ma_values,
        }
    )
    return base


def evaluate_wulong_cluster_signal(indicator: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_params = params if isinstance(params, dict) else {}
    convergence_threshold_pct = _safe_float(raw_params.get("convergence_threshold_pct"), 0.012)
    pre_convergence_min_spread_pct = _safe_float(raw_params.get("pre_convergence_min_spread_pct"), 0.03)
    min_current_spread_pct = _safe_float(raw_params.get("min_current_spread_pct"), 0.015)
    min_spread_expansion_multiple = _safe_float(raw_params.get("min_spread_expansion_multiple"), 1.8)
    min_volume_ratio20 = _safe_float(raw_params.get("min_volume_ratio20"), 1.3)
    min_breakout_return_pct = _safe_float(raw_params.get("min_breakout_return_pct"), 0.008)
    max_convergence_age_days = max(1, _safe_int(raw_params.get("max_convergence_age_days"), 8))
    min_rising_ma_count = max(1, min(5, _safe_int(raw_params.get("min_rising_ma_count"), 4)))

    has_data = bool(indicator.get("has_data"))
    bullish_alignment = bool(indicator.get("bullish_alignment"))
    close_above_all_mas = bool(indicator.get("close_above_all_mas"))
    rising_ma_count = max(0, _safe_int(indicator.get("rising_ma_count"), 0))
    current_spread_pct = max(0.0, _safe_float(indicator.get("current_spread_pct"), 0.0))
    convergence_spread_pct = _safe_float(indicator.get("convergence_spread_pct"), math.nan)
    pre_convergence_max_spread_pct = max(0.0, _safe_float(indicator.get("pre_convergence_max_spread_pct"), 0.0))
    convergence_offset_days = _safe_int(indicator.get("convergence_offset_days"), -1)
    spread_expansion_multiple = max(0.0, _safe_float(indicator.get("spread_expansion_multiple"), 0.0))
    volume_ratio20 = max(0.0, _safe_float(indicator.get("volume_ratio_20"), 0.0))
    breakout_ratio_pct = _safe_float(indicator.get("breakout_ratio_pct"), 0.0)
    daily_return_pct = _safe_float(indicator.get("daily_return_pct"), 0.0)
    breakout_strength_pct = max(daily_return_pct, breakout_ratio_pct)

    convergence_found = has_data and convergence_offset_days >= 0 and math.isfinite(convergence_spread_pct)
    convergence_ok = convergence_found and convergence_spread_pct <= convergence_threshold_pct
    convergence_recent = convergence_found and convergence_offset_days <= max_convergence_age_days
    pre_dispersion_ok = pre_convergence_max_spread_pct >= pre_convergence_min_spread_pct
    expansion_ok = (
        current_spread_pct >= min_current_spread_pct
        and spread_expansion_multiple >= min_spread_expansion_multiple
    )
    volume_ok = volume_ratio20 >= min_volume_ratio20
    breakout_ok = breakout_strength_pct >= min_breakout_return_pct
    rising_ok = rising_ma_count >= min_rising_ma_count

    convergence_score = (
        _clamp_score((1.0 - (convergence_spread_pct / max(convergence_threshold_pct, 1e-6))) * 100.0)
        if convergence_ok
        else 0.0
    )
    expansion_score = _clamp_score(
        45.0 * _safe_ratio(current_spread_pct, max(min_current_spread_pct, 1e-6), 0.0)
        + 55.0 * _safe_ratio(spread_expansion_multiple, max(min_spread_expansion_multiple, 1e-6), 0.0)
    )
    trend_score = _clamp_score(
        (35.0 if bullish_alignment else 0.0)
        + (25.0 if close_above_all_mas else 0.0)
        + min(40.0, max(0.0, float(rising_ma_count)) / 5.0 * 40.0)
    )
    volume_score = _clamp_score(_safe_ratio(volume_ratio20, max(min_volume_ratio20, 1e-6), 0.0) * 100.0)
    breakout_score = _clamp_score(
        _safe_ratio(breakout_strength_pct, max(min_breakout_return_pct, 1e-6), 0.0) * 100.0
    )
    signal_score = _clamp_score(
        convergence_score * 0.18
        + expansion_score * 0.24
        + trend_score * 0.22
        + volume_score * 0.18
        + breakout_score * 0.18
    )
    health_score = _clamp_score(trend_score * 0.6 + convergence_score * 0.4)
    event_score = _clamp_score(expansion_score * 0.55 + breakout_score * 0.45)
    volatility_score = _clamp_score(volume_score)

    event_grade = "C"
    if signal_score >= 80.0:
        event_grade = "A"
    elif signal_score >= 65.0:
        event_grade = "B"

    return {
        "signal": bool(
            convergence_ok
            and convergence_recent
            and pre_dispersion_ok
            and expansion_ok
            and bullish_alignment
            and close_above_all_mas
            and rising_ok
            and volume_ok
            and breakout_ok
        ),
        "convergence_ok": convergence_ok,
        "convergence_recent": convergence_recent,
        "pre_dispersion_ok": pre_dispersion_ok,
        "expansion_ok": expansion_ok,
        "volume_ok": volume_ok,
        "breakout_ok": breakout_ok,
        "rising_ok": rising_ok,
        "signal_score": signal_score,
        "health_score": health_score,
        "event_score": event_score,
        "trend_score": trend_score,
        "structure_score": expansion_score,
        "phase_score": convergence_score,
        "volatility_score": volatility_score,
        "event_grade": event_grade,
        "breakout_strength_pct": breakout_strength_pct,
        "trigger_date": str(indicator.get("trigger_date") or "").strip(),
        "current_spread_pct": current_spread_pct,
        "convergence_spread_pct": convergence_spread_pct if math.isfinite(convergence_spread_pct) else 0.0,
        "pre_convergence_max_spread_pct": pre_convergence_max_spread_pct,
        "convergence_offset_days": convergence_offset_days,
        "spread_expansion_multiple": spread_expansion_multiple,
        "volume_ratio_20": volume_ratio20,
        "rising_ma_count": rising_ma_count,
        "bullish_alignment": bullish_alignment,
        "close_above_all_mas": close_above_all_mas,
        "ma_values": indicator.get("ma_values") if isinstance(indicator.get("ma_values"), dict) else {},
    }


class WulongClusterPlugin(BaseStrategyPlugin):
    def __init__(self, strategy_id: str = "wulong_cluster_v1") -> None:
        self.strategy_id = str(strategy_id).strip() or "wulong_cluster_v1"

    def build_universe(
        self,
        *,
        candidates: list[ScreenerResult],
        params: dict[str, Any],
        mode: str,
    ) -> list[ScreenerResult]:
        _ = mode
        min_ret40 = _safe_float(params.get("min_ret40"), 0.06)
        max_retrace20 = _safe_float(params.get("max_retrace20"), 0.20)
        min_up_down_volume_ratio = _safe_float(params.get("min_up_down_volume_ratio"), 1.05)
        min_vol_slope20 = _safe_float(params.get("min_vol_slope20"), 0.0)
        min_ma10_above_ma20_days = max(0, _safe_int(params.get("min_ma10_above_ma20_days"), 3))
        min_ma5_above_ma10_days = max(0, _safe_int(params.get("min_ma5_above_ma10_days"), 2))
        allow_upper_shadow_risk = bool(params.get("allow_upper_shadow_risk", False))
        allow_blowoff_top = bool(params.get("allow_blowoff_top", False))

        out: list[ScreenerResult] = []
        for row in candidates:
            if float(row.ret40) < min_ret40:
                continue
            if float(row.retrace20) > max_retrace20:
                continue
            if float(row.up_down_volume_ratio) < min_up_down_volume_ratio:
                continue
            if float(row.vol_slope20) < min_vol_slope20:
                continue
            if int(row.ma10_above_ma20_days) < min_ma10_above_ma20_days:
                continue
            if int(row.ma5_above_ma10_days) < min_ma5_above_ma10_days:
                continue
            if (not allow_upper_shadow_risk) and bool(row.has_upper_shadow_risk):
                continue
            if (not allow_blowoff_top) and bool(row.has_blowoff_top):
                continue
            out.append(row)
        return out

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row
        indicator = snapshot.get("wulong_cluster_signal")
        if not isinstance(indicator, dict):
            return False
        evaluation = evaluate_wulong_cluster_signal(indicator, params)
        return bool(evaluation.get("signal", False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = params
        quality_score = _safe_float(getattr(signal, "entry_quality_score", fallback_score), fallback_score)
        trend_bonus = _clamp_score(max(0.0, float(row.ret40)) / 0.30 * 100.0)
        volume_bonus = _clamp_score((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        return _clamp_score(quality_score * 0.75 + trend_bonus * 0.15 + volume_bonus * 0.10)


class TrendKingPlugin(BaseStrategyPlugin):
    """趋势为王策略插件 — 涨停板精选/涨势确认/次日低吸."""

    def __init__(self, strategy_id: str = "trend_king_v1") -> None:
        self.strategy_id = str(strategy_id).strip() or "trend_king_v1"

    def _resolve_mode(self, params: dict[str, Any]) -> str:
        explicit = str(params.get("mode", "")).strip().lower()
        if explicit in ("a", "b", "c", "all"):
            return explicit
        if "limitup" in self.strategy_id:
            return "a"
        if "rally" in self.strategy_id:
            return "b"
        if "pullback" in self.strategy_id:
            return "c"
        return "all"

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row
        indicator = snapshot.get("trend_king_signal")
        if not isinstance(indicator, dict):
            return False
        from .trend_king_strategy import evaluate_trend_king_signal
        merged_params = dict(params) if isinstance(params, dict) else {}
        if "mode" not in merged_params:
            merged_params["mode"] = self._resolve_mode(params)
        evaluation = evaluate_trend_king_signal(indicator, merged_params)
        return bool(evaluation.get("signal", False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = params
        quality_score = _safe_float(getattr(signal, "entry_quality_score", fallback_score), fallback_score)
        trend_bonus = _clamp_score(max(0.0, float(row.ret40)) / 0.30 * 100.0)
        volume_bonus = _clamp_score((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        return _clamp_score(quality_score * 0.65 + trend_bonus * 0.20 + volume_bonus * 0.15)


class LimitUpArbPlugin(BaseStrategyPlugin):
    """涨停套利 — 昨日涨停 + 今日放量上涨；出场由回测页止盈/止损/持仓天数配置。"""

    def __init__(self, strategy_id: str = "limit_up_arb_v1") -> None:
        self.strategy_id = str(strategy_id).strip() or "limit_up_arb_v1"

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row
        indicator = snapshot.get("limit_up_arb_signal")
        if not isinstance(indicator, dict):
            return False
        from .limit_up_arb_strategy import evaluate_limit_up_arb_signal

        evaluation = evaluate_limit_up_arb_signal(indicator, params)
        return bool(evaluation.get("signal", False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = params
        quality_score = _safe_float(getattr(signal, "entry_quality_score", fallback_score), fallback_score)
        volume_bonus = _clamp_score((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        return _clamp_score(quality_score * 0.85 + volume_bonus * 0.15)

    def entry_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        _ = params
        return payload.model_copy(update={"entry_delay_days": 1})

    def exit_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        _ = params
        return payload.model_copy(update={"exit_events": []})


class EmotionLimitUpPlugin(BaseStrategyPlugin):
    """情绪涨停 — 天下无双金叉/紫转黄 + 当日涨停共振。"""

    def __init__(self, strategy_id: str = "emotion_limit_up_v1") -> None:
        self.strategy_id = str(strategy_id).strip() or "emotion_limit_up_v1"

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row
        indicator = snapshot.get("emotion_limit_up_signal")
        if not isinstance(indicator, dict):
            return False
        from .emotion_limit_up_strategy import evaluate_emotion_limit_up_signal

        evaluation = evaluate_emotion_limit_up_signal(indicator, params)
        return bool(evaluation.get("signal", False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = params
        quality_score = _safe_float(getattr(signal, "entry_quality_score", fallback_score), fallback_score)
        hist_bonus = _clamp_score(_safe_float(getattr(row, "ret40", 0.0), 0.0) / 0.35 * 100.0)
        volume_bonus = _clamp_score((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        return _clamp_score(quality_score * 0.70 + hist_bonus * 0.20 + volume_bonus * 0.10)

    def entry_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        _ = params
        if int(payload.entry_delay_days or 0) <= 0:
            return payload.model_copy(update={"entry_delay_days": 1})
        return payload


class ForceRhythmPlugin(BaseStrategyPlugin):
    """主散节奏波 — 主力量能波形定节奏买点，散户量能作卖出参考。"""

    def __init__(self, strategy_id: str = "ths_force_rhythm_v1") -> None:
        self.strategy_id = str(strategy_id).strip() or "ths_force_rhythm_v1"

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row
        indicator = snapshot.get("force_rhythm_signal")
        if not isinstance(indicator, dict):
            return False
        from .force_rhythm_strategy import evaluate_force_rhythm_signal

        evaluation = evaluate_force_rhythm_signal(indicator, params)
        return bool(evaluation.get("signal", False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = params
        quality_score = _safe_float(getattr(signal, "entry_quality_score", fallback_score), fallback_score)
        rhythm_bonus = _clamp_score(max(0.0, float(row.ret40)) / 0.35 * 100.0)
        volume_bonus = _clamp_score((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        return _clamp_score(quality_score * 0.75 + rhythm_bonus * 0.15 + volume_bonus * 0.10)

    def exit_policy(
        self,
        *,
        payload: BacktestRunRequest,
        params: dict[str, Any],
    ) -> BacktestRunRequest:
        _ = params
        return payload.model_copy(update={"exit_events": []})


class B1MultiTimeframePlugin(BaseStrategyPlugin):
    """B1 战法策略插件 — 多时间框架（月MACD+周DIF+日KDJ）."""

    def __init__(self, strategy_id: str = "b1_mtf_v1") -> None:
        self.strategy_id = str(strategy_id).strip() or "b1_mtf_v1"

    def build_universe(
        self,
        *,
        candidates: list[ScreenerResult],
        params: dict[str, Any],
        mode: str,
    ) -> list[ScreenerResult]:
        _ = params, mode
        return list(candidates)

    def generate_signals(
        self,
        *,
        row: ScreenerResult,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        _ = row, params
        indicator = snapshot.get("b1_mtf_signal")
        if not isinstance(indicator, dict):
            return False
        return bool(indicator.get("signal", False))

    def rank_signals(
        self,
        *,
        signal: SignalResult,
        row: ScreenerResult,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        _ = params
        quality_score = _safe_float(getattr(signal, "entry_quality_score", fallback_score), fallback_score)
        trend_bonus = _clamp_score(max(0.0, float(row.ret40)) / 0.30 * 100.0)
        volume_bonus = _clamp_score((1.0 - min(1.0, float(row.pullback_volume_ratio))) / 1.0 * 30.0)
        return _clamp_score(quality_score * 0.70 + trend_bonus * 0.15 + volume_bonus * 0.15)
