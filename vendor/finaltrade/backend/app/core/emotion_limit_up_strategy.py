"""情绪涨停策略 — 天下无双信号 + 涨停共振（模式 J）。"""

from __future__ import annotations

import math
from typing import Any, Sequence

from ..models import CandlePoint
from .ths_volume_signal import _ema, _main_force_state, _rolling_ma


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return float(parsed)


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(float(item) for item in values[-window:]) / float(window)


def _compute_main_retail_series(candles: Sequence[CandlePoint]) -> tuple[list[float], list[float]]:
    closes = [float(item.close) for item in candles]
    volumes = [float(item.volume) / 100.0 for item in candles]

    up_volume: list[float] = [0.0]
    down_volume: list[float] = [0.0]
    for index in range(1, len(candles)):
        volume = volumes[index]
        if closes[index] > closes[index - 1]:
            up_volume.append(volume)
            down_volume.append(0.0)
        elif closes[index] < closes[index - 1]:
            up_volume.append(0.0)
            down_volume.append(volume)
        else:
            up_volume.append(0.0)
            down_volume.append(0.0)

    main_force = _ema(_rolling_ma(up_volume, 3), 3)
    retail_force = _ema(_rolling_ma(down_volume, 3), 10)
    return main_force, retail_force


def _day_golden_cross(main_force: Sequence[float], retail_force: Sequence[float], index: int) -> bool:
    if index < 1:
        return False
    return float(main_force[index]) > float(retail_force[index]) and float(main_force[index - 1]) <= float(
        retail_force[index - 1]
    )


def _day_purple_to_yellow(main_force: Sequence[float], index: int) -> bool:
    if index < 2:
        return False
    prev_main = float(main_force[index - 1])
    prev2_main = float(main_force[index - 2])
    current_main = float(main_force[index])
    if prev_main <= 0.0 or current_main <= 0.0:
        return False
    previous_state = _main_force_state(prev_main, prev2_main)
    current_state = _main_force_state(current_main, prev_main)
    return previous_state == "falling" and current_state == "rising"


def _scan_window_signals(
    main_force: Sequence[float],
    retail_force: Sequence[float],
    *,
    end_index: int,
    lookback_days: int,
) -> dict[str, Any]:
    start_index = max(2, end_index - lookback_days + 1)
    golden_cross_days: list[int] = []
    purple_days: list[int] = []
    for index in range(start_index, end_index + 1):
        if _day_golden_cross(main_force, retail_force, index):
            golden_cross_days.append(index)
        if _day_purple_to_yellow(main_force, index):
            purple_days.append(index)

    latest_signal_index = -1
    for index in sorted(set(golden_cross_days + purple_days)):
        latest_signal_index = max(latest_signal_index, index)

    signal_age = end_index - latest_signal_index if latest_signal_index >= 0 else None
    return {
        "golden_cross_days": golden_cross_days,
        "purple_to_yellow_days": purple_days,
        "has_golden_cross": bool(golden_cross_days),
        "has_purple_to_yellow": bool(purple_days),
        "dual_signal": bool(golden_cross_days) and bool(purple_days),
        "latest_signal_index": latest_signal_index,
        "signal_age_days": signal_age,
    }


def _score_freshness(signal_age: int | None, *, max_days: int = 5) -> float:
    if signal_age is None:
        return 0.0
    if signal_age <= 2:
        return 20.0
    if signal_age <= 4:
        return 15.0
    if signal_age <= max(1, int(max_days)):
        return 10.0
    return 0.0


def _score_dual_signal(dual_signal: bool, has_any: bool) -> float:
    if dual_signal:
        return 12.0
    if has_any:
        return 5.0
    return 0.0


def _score_force_ratio(main_force: float, retail_force: float) -> float:
    if main_force <= 0.0:
        return 0.0
    if retail_force <= 0.0:
        return 12.0
    ratio = main_force / retail_force
    if ratio >= 3.0:
        return 12.0
    if ratio >= 2.0:
        return 8.0
    if ratio >= 1.5:
        return 4.0
    return 0.0


def _score_limit_up_volume_ratio(vol_ratio: float) -> float:
    if vol_ratio < 1.0:
        return 12.0
    if vol_ratio < 1.5:
        return 8.0
    if vol_ratio < 2.0:
        return 4.0
    return 0.0


def _score_hist_elasticity(max_hist: float) -> float:
    if max_hist > 80.0:
        return 10.0
    if max_hist > 50.0:
        return 7.0
    if max_hist > 30.0:
        return 3.0
    return 0.0


def _score_sector_rank(sector_rank: float) -> float:
    if sector_rank <= 5:
        return 10.0
    if sector_rank <= 10:
        return 7.0
    if sector_rank <= 20:
        return 3.0
    return 0.0


def _score_prev_gain(prev_gain: float) -> float:
    if prev_gain > 5.0:
        return 8.0
    if prev_gain > 3.0:
        return 5.0
    if prev_gain > 0.0:
        return 2.0
    return 0.0


def resolve_emotion_limit_up_params(params: dict[str, Any] | None) -> dict[str, Any]:
    raw = params if isinstance(params, dict) else {}
    return {
        "min_day_gain": _safe_float(raw.get("min_day_gain"), 9.5),
        "lookback_days": max(1, _safe_int(raw.get("lookback_days"), 5)),
        "min_score": _safe_float(raw.get("min_score"), 0.0),
        "require_next_day_confirm": bool(raw.get("require_next_day_confirm", False)),
        "next_day_volume_multiple": max(1.0, _safe_float(raw.get("next_day_volume_multiple"), 2.0)),
        "max_drawdown_from_limit_pct": max(0.0, _safe_float(raw.get("max_drawdown_from_limit_pct"), 5.0)),
    }


def calculate_emotion_limit_up_signal(
    candles: Sequence[CandlePoint],
    *,
    lookback_days: int = 5,
    sector_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = list(candles)
    base: dict[str, Any] = {
        "has_data": False,
        "trigger_date": str(ordered[-1].time) if ordered else "",
        "signal": False,
        "signal_score": 0.0,
        "label": "",
    }
    if len(ordered) < 15:
        return base

    closes = [max(0.0, float(item.close)) for item in ordered]
    highs = [max(0.0, float(item.high)) for item in ordered]
    lows = [max(0.0, float(item.low)) for item in ordered]
    vols = [max(0.0, float(item.volume)) for item in ordered]

    latest_close = closes[-1]
    prev_close = closes[-2]
    if latest_close <= 0 or prev_close <= 0:
        return base

    day_gain = (latest_close - prev_close) / prev_close * 100.0
    prev_gain = 0.0
    if len(closes) >= 3 and closes[-3] > 0:
        prev_gain = (closes[-2] - closes[-3]) / closes[-3] * 100.0

    vol_ma20 = _sma(vols, 20) or 0.0
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

    max_hist = 0.0
    lookback_start = max(0, len(ordered) - 250)
    for index in range(lookback_start, len(ordered) - 10):
        if closes[index] <= 0:
            continue
        future_idx = min(index + 20, len(closes) - 1)
        gain = (closes[future_idx] - closes[index]) / closes[index] * 100.0
        max_hist = max(max_hist, gain)

    ma60 = _sma(closes, 60)
    above_ma60 = ma60 is not None and latest_close > ma60

    main_force, retail_force = _compute_main_retail_series(ordered)
    end_index = len(ordered) - 1
    window_days = max(1, int(lookback_days))
    window = _scan_window_signals(main_force, retail_force, end_index=end_index, lookback_days=window_days)

    current_main = float(main_force[-1])
    current_retail = float(retail_force[-1])
    force_ratio = current_main / current_retail if current_retail > 0 else (99.0 if current_main > 0 else 0.0)

    sector_rank = 99.0
    if sector_data and isinstance(sector_data, dict):
        sector_rank = _safe_float(sector_data.get("sector_rank"), 99.0)

    score = (
        _score_freshness(window.get("signal_age_days"), max_days=window_days)
        + _score_dual_signal(bool(window.get("dual_signal")), bool(window.get("has_golden_cross") or window.get("has_purple_to_yellow")))
        + _score_force_ratio(current_main, current_retail)
        + _score_limit_up_volume_ratio(vol_ratio)
        + _score_hist_elasticity(max_hist)
        + _score_sector_rank(sector_rank)
        + _score_prev_gain(prev_gain)
        + (5.0 if above_ma60 else 0.0)
    )
    score = max(0.0, min(100.0, score))

    has_signal = bool(window.get("has_golden_cross") or window.get("has_purple_to_yellow"))
    limit_up = day_gain >= 9.5
    triggered = limit_up and has_signal

    labels: list[str] = []
    if limit_up:
        labels.append("涨停")
    if window.get("has_golden_cross"):
        labels.append("金叉")
    if window.get("has_purple_to_yellow"):
        labels.append("紫转黄")
    if window.get("dual_signal"):
        labels.append("双信号")

    base.update(
        {
            "has_data": True,
            "trigger_date": str(ordered[-1].time),
            "signal": triggered,
            "signal_score": round(score, 2),
            "label": "+".join(labels) if labels else "",
            "day_gain": round(day_gain, 2),
            "prev_gain": round(prev_gain, 2),
            "vol_ratio": round(vol_ratio, 2),
            "max_hist": round(max_hist, 1),
            "above_ma60": above_ma60,
            "main_force": round(current_main, 6),
            "retail_force": round(current_retail, 6),
            "force_ratio": round(force_ratio, 4),
            "limit_up": limit_up,
            "has_golden_cross": bool(window.get("has_golden_cross")),
            "has_purple_to_yellow": bool(window.get("has_purple_to_yellow")),
            "dual_signal": bool(window.get("dual_signal")),
            "signal_age_days": window.get("signal_age_days"),
            "sector_rank": int(sector_rank),
            "limit_up_close": round(latest_close, 2),
            "limit_up_low": round(lows[-1], 2),
            "limit_up_volume": round(vols[-1], 2),
        }
    )
    return base


def evaluate_emotion_limit_up_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = resolve_emotion_limit_up_params(params)
    has_data = bool(indicator.get("has_data"))
    day_gain = _safe_float(indicator.get("day_gain"), 0.0)
    signal_score = _safe_float(indicator.get("signal_score"), 0.0)

    has_volume_signal = bool(indicator.get("has_golden_cross")) or bool(indicator.get("has_purple_to_yellow"))
    limit_up = has_data and day_gain >= cfg["min_day_gain"]
    signal = limit_up and has_volume_signal and signal_score >= cfg["min_score"]

    event_grade = "C"
    if signal_score >= 75.0:
        event_grade = "A"
    elif signal_score >= 55.0:
        event_grade = "B"

    return {
        "signal": signal,
        "signal_score": round(signal_score, 2),
        "trigger_date": str(indicator.get("trigger_date") or ""),
        "label": str(indicator.get("label") or "情绪涨停"),
        "event_grade": event_grade,
        "limit_up": limit_up,
        "dual_signal": bool(indicator.get("dual_signal")),
        "signal_age_days": indicator.get("signal_age_days"),
    }


def evaluate_next_day_entry(
    limit_up_candle: CandlePoint,
    next_day_candle: CandlePoint,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """评估涨停次日是否满足倍量 + 没死 的尾盘买入条件。"""
    cfg = resolve_emotion_limit_up_params(params)
    limit_close = max(0.0, float(limit_up_candle.close))
    limit_volume = max(0.0, float(limit_up_candle.volume))
    next_volume = max(0.0, float(next_day_candle.volume))
    next_low = max(0.0, float(next_day_candle.low))

    volume_ok = next_volume >= limit_volume * cfg["next_day_volume_multiple"] if limit_volume > 0 else False
    floor_price = limit_close * (1.0 - cfg["max_drawdown_from_limit_pct"] / 100.0)
    alive_ok = next_low >= floor_price if limit_close > 0 else False

    action = "放弃"
    if volume_ok and alive_ok:
        action = "尾盘买入"
    elif volume_ok:
        action = "观望"
    elif alive_ok:
        action = "小仓试水"

    return {
        "volume_ok": volume_ok,
        "alive_ok": alive_ok,
        "action": action,
        "entry_allowed": volume_ok and alive_ok,
    }
