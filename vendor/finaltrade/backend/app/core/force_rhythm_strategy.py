"""主散节奏波策略 — 以主力量能波形为核心（二元判定），散户量能仅作卖出参考。"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Sequence

from ..models import CandlePoint


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _rolling_ma(values: Sequence[float], window: int) -> list[float]:
    span = max(1, int(window))
    queue: deque[float] = deque()
    running = 0.0
    out: list[float] = []
    for raw in values:
        value = float(raw)
        queue.append(value)
        running += value
        if len(queue) > span:
            running -= queue.popleft()
        out.append(running / float(len(queue)))
    return out


def _ema(values: Sequence[float], window: int) -> list[float]:
    span = max(1, int(window))
    alpha = 2.0 / (float(span) + 1.0)
    out: list[float] = []
    ema_value = 0.0
    for index, raw in enumerate(values):
        value = float(raw)
        if index == 0:
            ema_value = value
        else:
            ema_value = alpha * value + (1.0 - alpha) * ema_value
        out.append(ema_value)
    return out


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return float(parsed)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


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


def _find_local_troughs(values: Sequence[float], *, min_distance: int = 4) -> list[int]:
    if len(values) < 3:
        return []
    troughs: list[int] = []
    for index in range(1, len(values) - 1):
        current = float(values[index])
        if current > float(values[index - 1]) or current > float(values[index + 1]):
            continue
        if troughs and index - troughs[-1] < min_distance:
            if current < float(values[troughs[-1]]):
                troughs[-1] = index
            continue
        troughs.append(index)
    return troughs


def _coefficient_of_variation(samples: Sequence[float]) -> float:
    if len(samples) < 2:
        return math.inf
    mean_value = sum(float(item) for item in samples) / float(len(samples))
    if mean_value <= 1e-9:
        return math.inf
    variance = sum((float(item) - mean_value) ** 2 for item in samples) / float(len(samples))
    return math.sqrt(max(0.0, variance)) / mean_value


def _percentile_rank(value: float, samples: Sequence[float]) -> float:
    if not samples:
        return 0.5
    count = sum(1 for item in samples if float(item) <= float(value))
    return float(count) / float(len(samples))


def _autocorr_at_lag(values: Sequence[float], lag: int) -> float:
    if lag <= 0 or len(values) <= lag + 2:
        return 0.0
    series = [float(item) for item in values]
    mean_value = sum(series) / float(len(series))
    variance = sum((item - mean_value) ** 2 for item in series)
    if variance <= 1e-12:
        return 0.0
    numerator = sum(
        (series[index] - mean_value) * (series[index + lag] - mean_value)
        for index in range(len(series) - lag)
    )
    return float(numerator) / float(variance)


def _best_autocorr_near_cycle(values: Sequence[float], cycle_mean: float) -> float:
    if not math.isfinite(cycle_mean) or cycle_mean <= 0.0:
        return 0.0
    center = max(1, int(round(cycle_mean)))
    best = 0.0
    for lag in range(max(1, center - 2), center + 3):
        best = max(best, _autocorr_at_lag(values, lag))
    return float(best)


def _main_force_state(current: float, previous: float) -> str:
    if current > previous:
        return "rising"
    if current < previous:
        return "falling"
    return "flat"


def _trough_percentile_at(main_force: Sequence[float], index: int, *, lookback: int = 80) -> float:
    if index < 0 or index >= len(main_force):
        return 0.5
    window_start = max(0, index + 1 - lookback)
    window = main_force[window_start : index + 1]
    return _percentile_rank(float(main_force[index]), window)


def _deep_trough_streak(main_force: Sequence[float], *, threshold: float, lookback: int = 80) -> int:
    streak = 0
    for bars_ago in range(0, 5):
        index = len(main_force) - 1 - bars_ago
        if index < 0:
            break
        if _trough_percentile_at(main_force, index, lookback=lookback) <= threshold:
            streak += 1
        else:
            break
    return streak


def _recent_flip_context(
    main_force: Sequence[float],
    *,
    follow_bars_ago: int,
    lookback: int = 80,
) -> tuple[int, float]:
    if len(main_force) < 3 or follow_bars_ago <= 0:
        return -1, 0.5
    index = len(main_force) - 1 - follow_bars_ago
    if index < 2:
        return -1, 0.5
    current = float(main_force[index])
    prev = float(main_force[index - 1])
    prev2 = float(main_force[index - 2])
    if _main_force_state(prev, prev2) != "falling":
        return -1, 0.5
    if _main_force_state(current, prev) != "rising":
        return -1, 0.5
    return follow_bars_ago, _trough_percentile_at(main_force, index, lookback=lookback)


def resolve_force_rhythm_params(params: dict[str, Any] | None) -> dict[str, Any]:
    raw_params = params if isinstance(params, dict) else {}
    return {
        "min_cycle_count": max(2, _safe_int(raw_params.get("min_cycle_count"), 3)),
        "max_cycle_cv": _safe_float(raw_params.get("max_cycle_cv"), 0.55),
        "max_amplitude_cv": _safe_float(raw_params.get("max_amplitude_cv"), 0.85),
        "min_autocorr": _safe_float(raw_params.get("min_autocorr"), 0.05),
        "min_wave_swing_ratio": _safe_float(raw_params.get("min_wave_swing_ratio"), 0.12),
        "trough_percentile_max": _safe_float(raw_params.get("trough_percentile_max"), 0.40),
        "peak_percentile_min": _safe_float(raw_params.get("peak_percentile_min"), 0.65),
        "peak_reject_percentile_min": _safe_float(raw_params.get("peak_reject_percentile_min"), 0.72),
        "flip_trough_percentile_min": _safe_float(raw_params.get("flip_trough_percentile_min"), 0.42),
        "flip_trough_percentile_max": _safe_float(raw_params.get("flip_trough_percentile_max"), 0.55),
        "trough_turn_percentile_min": _safe_float(raw_params.get("trough_turn_percentile_min"), 0.17),
        "trough_turn_percentile_max": _safe_float(raw_params.get("trough_turn_percentile_max"), 0.30),
        "deep_trough_percentile_max": _safe_float(raw_params.get("deep_trough_percentile_max"), 0.015),
        "deep_trough_streak_target": max(1, _safe_int(raw_params.get("deep_trough_streak_target"), 2)),
        "flip_follow_bars_ago": max(1, _safe_int(raw_params.get("flip_follow_bars_ago"), 2)),
        "flip_follow_max_trough_percentile": _safe_float(
            raw_params.get("flip_follow_max_trough_percentile"), 0.35
        ),
        "flip_follow_min_trough_percentile": _safe_float(
            raw_params.get("flip_follow_min_trough_percentile"), 0.17
        ),
        "retail_block_min_trough_percentile": _safe_float(
            raw_params.get("retail_block_min_trough_percentile"), 0.22
        ),
        "retail_high_percentile_min": _safe_float(raw_params.get("retail_high_percentile_min"), 0.60),
        "require_trough_turn": bool(raw_params.get("require_trough_turn", True)),
        "block_buy_on_retail_sell": bool(raw_params.get("block_buy_on_retail_sell", True)),
    }


def detect_rhythm_wave_pattern(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """二元判定：主力量能是否已形成规律节奏波（不看散户、不看综合分）。"""
    cfg = resolve_force_rhythm_params(params)
    has_data = bool(indicator.get("has_data"))
    cycle_count = max(0, _safe_int(indicator.get("cycle_count"), 0))
    cycle_cv = _safe_float(indicator.get("cycle_cv"), math.inf)
    amplitude_cv = _safe_float(indicator.get("amplitude_cv"), math.inf)
    autocorr = _safe_float(indicator.get("autocorr"), 0.0)
    wave_swing_ratio = _safe_float(indicator.get("wave_swing_ratio"), 0.0)

    enough_cycles = cycle_count >= cfg["min_cycle_count"]
    cycle_regular = math.isfinite(cycle_cv) and cycle_cv <= cfg["max_cycle_cv"]
    amplitude_regular = math.isfinite(amplitude_cv) and amplitude_cv <= cfg["max_amplitude_cv"]
    autocorr_ok = autocorr >= cfg["min_autocorr"]
    swing_ok = wave_swing_ratio >= cfg["min_wave_swing_ratio"]

    rhythm_pattern_formed = bool(
        has_data
        and enough_cycles
        and cycle_regular
        and amplitude_regular
        and autocorr_ok
        and swing_ok
    )

    return {
        "rhythm_pattern_formed": rhythm_pattern_formed,
        "enough_cycles": enough_cycles,
        "cycle_regular": cycle_regular,
        "amplitude_regular": amplitude_regular,
        "autocorr_ok": autocorr_ok,
        "swing_ok": swing_ok,
    }


def detect_retail_sell_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """散户量能卖出信号：散户占优且抬升，或散户处于相对高位。"""
    cfg = resolve_force_rhythm_params(params)
    main_force = _safe_float(indicator.get("main_force"), 0.0)
    retail_force = _safe_float(indicator.get("retail_force"), 0.0)
    prev_retail = _safe_float(indicator.get("prev_retail_force"), retail_force)
    retail_percentile = _safe_float(indicator.get("retail_percentile"), 0.5)
    retail_state = str(indicator.get("retail_force_state") or "flat")

    retail_dominates = retail_force > main_force and main_force > 0.0
    retail_rising = retail_state == "rising"
    retail_elevated = retail_percentile >= cfg["retail_high_percentile_min"]

    retail_sell_signal = bool(retail_dominates and (retail_rising or retail_elevated))

    return {
        "retail_sell_signal": retail_sell_signal,
        "retail_dominates": retail_dominates,
        "retail_rising": retail_rising,
        "retail_elevated": retail_elevated,
    }


def detect_main_peak_sell_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """主力波峰卖出：主力处于相对高位且开始回落。"""
    cfg = resolve_force_rhythm_params(params)
    main_state = str(indicator.get("main_force_state") or "flat")
    trough_percentile = _safe_float(indicator.get("trough_percentile"), 0.5)
    peak_zone = trough_percentile >= cfg["peak_percentile_min"]
    main_peak_turn = bool(peak_zone and main_state == "falling")
    return {
        "main_peak_sell_signal": main_peak_turn,
        "main_at_peak_zone": peak_zone,
    }


def calculate_force_rhythm_signal(candles: Sequence[CandlePoint]) -> dict[str, Any]:
    ordered = list(candles)
    trigger_date = str(ordered[-1].time) if ordered else ""
    base: dict[str, Any] = {
        "available": False,
        "trigger_date": trigger_date,
        "has_data": False,
        "rhythm_pattern_formed": False,
        "trough_turn": False,
        "retail_sell_signal": False,
        "main_peak_sell_signal": False,
        "cycle_count": 0,
        "cycle_mean_days": 0.0,
        "cycle_cv": math.inf,
        "amplitude_cv": math.inf,
        "autocorr": 0.0,
        "autocorr_score": 0.0,
        "wave_swing_ratio": 0.0,
        "rhythm_regularity_score": 0.0,
        "trough_percentile": 0.5,
        "retail_percentile": 0.5,
        "main_force": 0.0,
        "retail_force": 0.0,
        "prev_main_force": 0.0,
        "prev2_main_force": 0.0,
        "prev_retail_force": 0.0,
        "prev_main_force_state": "flat",
        "prev_trough_percentile": 0.5,
        "deep_trough_streak": 0,
        "recent_flip_bars_ago": -1,
        "recent_flip_trough_percentile": 0.5,
        "main_force_state": "flat",
        "retail_force_state": "flat",
        "main_force_power_score": 0.0,
        "signal_score": 0.0,
        "health_score": 0.0,
        "event_score": 0.0,
    }
    if len(ordered) < 40:
        return base

    main_force, retail_force = _compute_main_retail_series(ordered)
    lookback = min(len(main_force), 80)
    window_start = len(main_force) - lookback
    main_window = main_force[window_start:]
    retail_window = retail_force[window_start:]

    troughs = _find_local_troughs(main_window, min_distance=4)
    cycle_count = max(0, len(troughs) - 1)

    current_main = float(main_force[-1])
    prev_main = float(main_force[-2]) if len(main_force) >= 2 else current_main
    prev2_main = float(main_force[-3]) if len(main_force) >= 3 else prev_main
    current_retail = float(retail_force[-1])
    prev_retail = float(retail_force[-2]) if len(retail_force) >= 2 else current_retail
    prev2_retail = float(retail_force[-3]) if len(retail_force) >= 3 else prev_retail

    main_force_state = _main_force_state(current_main, prev_main)
    prev_main_force_state = _main_force_state(prev_main, prev2_main)
    retail_force_state = _main_force_state(current_retail, prev_retail)
    trough_percentile = _percentile_rank(current_main, main_window)
    prev_trough_percentile = (
        _trough_percentile_at(main_force, len(main_force) - 2, lookback=lookback)
        if len(main_force) >= 2
        else trough_percentile
    )
    retail_percentile = _percentile_rank(current_retail, retail_window)
    deep_trough_streak = _deep_trough_streak(
        main_force,
        threshold=0.015,
        lookback=lookback,
    )
    recent_flip_bars_ago, recent_flip_trough_percentile = _recent_flip_context(
        main_force,
        follow_bars_ago=2,
        lookback=lookback,
    )

    if cycle_count < 2:
        base.update(
            {
                "available": True,
                "has_data": True,
                "main_force": round(current_main, 6),
                "retail_force": round(current_retail, 6),
                "prev_main_force": round(prev_main, 6),
                "prev2_main_force": round(prev2_main, 6),
                "prev_retail_force": round(prev_retail, 6),
                "prev_main_force_state": prev_main_force_state,
                "prev_trough_percentile": round(prev_trough_percentile, 4),
                "deep_trough_streak": deep_trough_streak,
                "recent_flip_bars_ago": recent_flip_bars_ago,
                "recent_flip_trough_percentile": round(recent_flip_trough_percentile, 4),
                "main_force_state": main_force_state,
                "retail_force_state": retail_force_state,
                "trough_percentile": round(trough_percentile, 4),
                "retail_percentile": round(retail_percentile, 4),
            }
        )
        return base

    intervals = [float(troughs[index + 1] - troughs[index]) for index in range(len(troughs) - 1)]
    cycle_mean = sum(intervals) / float(len(intervals))
    cycle_cv = _coefficient_of_variation(intervals)

    amplitudes: list[float] = []
    for index, trough_idx in enumerate(troughs[:-1]):
        next_trough = int(troughs[index + 1])
        if next_trough <= trough_idx:
            continue
        segment = [float(main_window[pos]) for pos in range(trough_idx, next_trough + 1)]
        if not segment:
            continue
        amplitudes.append(max(segment) - float(main_window[trough_idx]))
    amplitude_cv = _coefficient_of_variation(amplitudes) if amplitudes else math.inf
    mean_amplitude = sum(amplitudes) / float(len(amplitudes)) if amplitudes else 0.0
    mean_main_level = sum(float(item) for item in main_window) / float(len(main_window))
    wave_swing_ratio = (
        mean_amplitude / max(mean_main_level, 1e-6)
        if mean_amplitude > 0.0 and mean_main_level > 0.0
        else 0.0
    )

    autocorr = _best_autocorr_near_cycle(main_window, cycle_mean)
    autocorr_score = _clamp(max(0.0, autocorr) * 100.0)

    rhythm_regularity_score = _clamp(
        (1.0 - min(1.0, cycle_cv / 0.55)) * 35.0
        + (1.0 - min(1.0, amplitude_cv / 0.85)) * 30.0
        + max(0.0, autocorr) * 35.0
    )

    was_falling = prev_main <= prev2_main
    trough_turn = bool(
        was_falling
        and current_main > prev_main
        and trough_percentile <= 0.40
    )

    main_force_power_score = _clamp(
        (1.0 - trough_percentile) * 55.0
        + (35.0 if main_force_state == "rising" else 0.0)
        + min(10.0, wave_swing_ratio * 40.0)
    )

    metrics = {
        "has_data": True,
        "cycle_count": cycle_count,
        "cycle_cv": cycle_cv,
        "amplitude_cv": amplitude_cv,
        "autocorr": autocorr,
        "wave_swing_ratio": wave_swing_ratio,
        "main_force": current_main,
        "retail_force": current_retail,
        "prev_retail_force": prev_retail,
        "retail_percentile": retail_percentile,
        "retail_force_state": retail_force_state,
        "main_force_state": main_force_state,
        "trough_percentile": trough_percentile,
    }
    pattern = detect_rhythm_wave_pattern(metrics)
    retail_sell = detect_retail_sell_signal(metrics)
    main_peak_sell = detect_main_peak_sell_signal(metrics)

    turn_bonus = 18.0 if trough_turn else 0.0
    pattern_bonus = 24.0 if pattern["rhythm_pattern_formed"] else 0.0
    signal_score = _clamp(
        12.0 + turn_bonus + pattern_bonus + rhythm_regularity_score * 0.40 + main_force_power_score * 0.20
    )

    base.update(
        {
            "available": True,
            "has_data": True,
            "rhythm_pattern_formed": pattern["rhythm_pattern_formed"],
            "trough_turn": trough_turn,
            "retail_sell_signal": retail_sell["retail_sell_signal"],
            "main_peak_sell_signal": main_peak_sell["main_peak_sell_signal"],
            "cycle_count": cycle_count,
            "cycle_mean_days": round(cycle_mean, 2),
            "cycle_cv": round(cycle_cv, 4) if math.isfinite(cycle_cv) else math.inf,
            "amplitude_cv": round(amplitude_cv, 4) if math.isfinite(amplitude_cv) else math.inf,
            "autocorr": round(autocorr, 4),
            "autocorr_score": round(autocorr_score, 2),
            "wave_swing_ratio": round(wave_swing_ratio, 4),
            "rhythm_regularity_score": round(rhythm_regularity_score, 2),
            "trough_percentile": round(trough_percentile, 4),
            "retail_percentile": round(retail_percentile, 4),
            "main_force": round(current_main, 6),
            "retail_force": round(current_retail, 6),
            "prev_main_force": round(prev_main, 6),
            "prev2_main_force": round(prev2_main, 6),
            "prev_retail_force": round(prev_retail, 6),
            "prev_main_force_state": prev_main_force_state,
            "prev_trough_percentile": round(prev_trough_percentile, 4),
            "deep_trough_streak": deep_trough_streak,
            "recent_flip_bars_ago": recent_flip_bars_ago,
            "recent_flip_trough_percentile": round(recent_flip_trough_percentile, 4),
            "main_force_state": main_force_state,
            "retail_force_state": retail_force_state,
            "main_force_power_score": round(main_force_power_score, 2),
            "signal_score": round(signal_score, 2),
            "health_score": round(rhythm_regularity_score, 2),
            "event_score": round(main_force_power_score, 2),
        }
    )
    return base


def _detect_force_rhythm_buy_trigger(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """节奏波买点：紫转黄同向触发 + 波峰/散户过滤。"""
    cfg = resolve_force_rhythm_params(params)
    trough_percentile = _safe_float(indicator.get("trough_percentile"), 0.5)
    main_state = str(indicator.get("main_force_state") or "flat")
    prev_state = str(indicator.get("prev_main_force_state") or "flat")
    flip = prev_state == "falling" and main_state == "rising"
    trough_turn = bool(indicator.get("trough_turn"))
    deep_streak = max(0, _safe_int(indicator.get("deep_trough_streak"), 0))
    flip_bars_ago = _safe_int(indicator.get("recent_flip_bars_ago"), -1)
    flip_trough_pct = _safe_float(indicator.get("recent_flip_trough_percentile"), 0.5)

    flip_band = bool(
        flip
        and cfg["flip_trough_percentile_min"] <= trough_percentile <= cfg["flip_trough_percentile_max"]
    )
    flip_follow = bool(
        flip_bars_ago == cfg["flip_follow_bars_ago"]
        and cfg["flip_follow_min_trough_percentile"] <= flip_trough_pct <= cfg["flip_follow_max_trough_percentile"]
        and main_state == "rising"
    )
    deep_trough = bool(
        trough_percentile <= cfg["deep_trough_percentile_max"]
        and main_state == "falling"
        and deep_streak == cfg["deep_trough_streak_target"]
    )

    trigger_reason = ""
    if flip_band:
        trigger_reason = "flip_band"
    elif flip_follow:
        trigger_reason = "flip_follow"
    elif deep_trough:
        trigger_reason = "deep_trough"

    triggered = bool(trigger_reason)
    peak_reject = trough_percentile >= cfg["peak_reject_percentile_min"]
    if peak_reject and not (flip_follow or deep_trough):
        triggered = False
        trigger_reason = "peak_zone"

    return {
        "triggered": triggered,
        "trigger_reason": trigger_reason,
        "flip_band": flip_band,
        "flip_follow": flip_follow,
        "deep_trough": deep_trough,
        "peak_reject": peak_reject,
    }


def evaluate_force_rhythm_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = resolve_force_rhythm_params(params)
    pattern = detect_rhythm_wave_pattern(indicator, params)
    retail_sell = detect_retail_sell_signal(indicator, params)
    main_peak_sell = detect_main_peak_sell_signal(indicator, params)
    buy_trigger = _detect_force_rhythm_buy_trigger(indicator, params)

    trough_percentile = _safe_float(indicator.get("trough_percentile"), 0.5)
    trough_turn = bool(indicator.get("trough_turn"))
    signal_score = _safe_float(indicator.get("signal_score"), 0.0)
    rhythm_regularity_score = _safe_float(indicator.get("rhythm_regularity_score"), 0.0)
    main_force_power_score = _safe_float(indicator.get("main_force_power_score"), 0.0)

    signal = bool(pattern["rhythm_pattern_formed"] and buy_trigger["triggered"])

    if signal and cfg["block_buy_on_retail_sell"] and retail_sell["retail_sell_signal"]:
        if (
            trough_percentile > cfg["retail_block_min_trough_percentile"]
            and not buy_trigger["deep_trough"]
        ):
            signal = False

    if signal and main_peak_sell["main_peak_sell_signal"] and not buy_trigger["deep_trough"]:
        signal = False

    event_grade = "C"
    if signal_score >= 80.0:
        event_grade = "A"
    elif signal_score >= 65.0:
        event_grade = "B"

    return {
        "signal": signal,
        "trigger_reason": buy_trigger["trigger_reason"] if signal else "",
        "buy_trigger_checks": buy_trigger,
        "rhythm_pattern_formed": pattern["rhythm_pattern_formed"],
        "pattern_checks": pattern,
        "retail_sell_signal": retail_sell["retail_sell_signal"],
        "retail_sell_checks": retail_sell,
        "signal_score": round(signal_score, 2),
        "health_score": round(rhythm_regularity_score, 2),
        "event_score": round(main_force_power_score, 2),
        "rhythm_regularity_score": round(rhythm_regularity_score, 2),
        "main_force_power_score": round(main_force_power_score, 2),
        "cycle_count": max(0, _safe_int(indicator.get("cycle_count"), 0)),
        "trough_percentile": round(trough_percentile, 4),
        "retail_percentile": _safe_float(indicator.get("retail_percentile"), 0.5),
        "trough_turn": trough_turn,
        "event_grade": event_grade,
        "trigger_date": str(indicator.get("trigger_date") or "").strip(),
    }


def evaluate_force_rhythm_exit(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    retail_sell = detect_retail_sell_signal(indicator, params)
    main_peak_sell = detect_main_peak_sell_signal(indicator, params)
    exit_signal = bool(
        retail_sell["retail_sell_signal"] or main_peak_sell["main_peak_sell_signal"]
    )
    reasons: list[str] = []
    if retail_sell["retail_sell_signal"]:
        reasons.append("散户卖出")
    if main_peak_sell["main_peak_sell_signal"]:
        reasons.append("主力波峰")
    return {
        "exit_signal": exit_signal,
        "exit_reason": "/".join(reasons),
        "retail_sell_signal": retail_sell["retail_sell_signal"],
        "main_peak_sell_signal": main_peak_sell["main_peak_sell_signal"],
    }
