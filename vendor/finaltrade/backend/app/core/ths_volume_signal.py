from __future__ import annotations

import math
from collections import deque
from typing import Sequence

from ..models import CandlePoint


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


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _positive_ratio(current: float, previous: float, *, zero_cap: float) -> float:
    if current <= 0.0:
        return 0.0
    if previous <= 0.0:
        return float(zero_cap)
    return float(current) / float(previous)


def _saturating_ratio_score(ratio: float, *, ceiling: float) -> float:
    if not math.isfinite(ratio) or ratio <= 1.0:
        return 0.0
    capped = min(float(ratio), max(1.01, float(ceiling)))
    return _clamp(math.log1p(capped - 1.0) / math.log1p(max(0.01, float(ceiling) - 1.0)) * 100.0)


def _main_force_state(current: float, previous: float) -> str:
    if current > previous:
        return "rising"
    if current < previous:
        return "falling"
    return "flat"


def calculate_ths_main_retail_signal(candles: Sequence[CandlePoint]) -> dict[str, object]:
    ordered = list(candles)
    if not ordered:
        return {
            "available": False,
            "trigger_date": "",
            "main_force": 0.0,
            "retail_force": 0.0,
            "prev_main_force": 0.0,
            "prev_retail_force": 0.0,
            "prev2_main_force": 0.0,
            "main_force_state": "flat",
            "prev_main_force_state": "flat",
            "purple_to_yellow": False,
            "golden_cross": False,
            "main_force_power_score": 0.0,
            "force_gap_score": 0.0,
            "retail_pressure_score": 0.0,
            "main_force_explosion_ratio": 0.0,
            "main_vs_retail_ratio": 0.0,
            "signal_score": 0.0,
        }

    closes = [float(item.close) for item in ordered]
    volumes = [float(item.volume) / 100.0 for item in ordered]

    up_volume: list[float] = [0.0]
    down_volume: list[float] = [0.0]
    for index in range(1, len(ordered)):
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

    current_main = float(main_force[-1])
    current_retail = float(retail_force[-1])
    prev_main = float(main_force[-2]) if len(main_force) >= 2 else current_main
    prev_retail = float(retail_force[-2]) if len(retail_force) >= 2 else current_retail
    prev2_main = float(main_force[-3]) if len(main_force) >= 3 else prev_main

    current_state = _main_force_state(current_main, prev_main)
    previous_state = _main_force_state(prev_main, prev2_main)

    purple_to_yellow = (
        len(main_force) >= 3
        and prev_main > 0.0
        and previous_state == "falling"
        and current_main > 0.0
        and current_state == "rising"
    )
    golden_cross = (
        len(main_force) >= 2
        and current_main > current_retail
        and prev_main <= prev_retail
    )

    total_force = current_main + current_retail
    dominance = (current_main - current_retail) / total_force if total_force > 0 else 0.0
    day_growth_ratio = _positive_ratio(current_main, prev_main, zero_cap=12.0)
    prev_growth_ratio = _positive_ratio(prev_main, prev2_main, zero_cap=12.0)
    prev_average_main = max((prev_main + prev2_main) / 2.0, 0.0)
    explosion_ratio = _positive_ratio(current_main, prev_average_main, zero_cap=15.0)
    acceleration_ratio = _positive_ratio(day_growth_ratio, max(prev_growth_ratio, 1.0), zero_cap=6.0)
    main_vs_retail_ratio = _positive_ratio(current_main, current_retail, zero_cap=8.0)

    growth_score = _saturating_ratio_score(day_growth_ratio, ceiling=12.0)
    explosion_score = _saturating_ratio_score(explosion_ratio, ceiling=15.0)
    acceleration_score = _saturating_ratio_score(acceleration_ratio, ceiling=6.0)
    dominance_score = _clamp(max(0.0, dominance) * 100.0)
    ratio_score = _saturating_ratio_score(main_vs_retail_ratio, ceiling=8.0)

    main_force_power_score = _clamp(
        growth_score * 0.45
        + explosion_score * 0.40
        + acceleration_score * 0.15
    )
    force_gap_score = _clamp(
        dominance_score * 0.55
        + ratio_score * 0.45
    )
    retail_pressure_score = _clamp(100.0 - force_gap_score)

    trigger_bonus = 0.0
    if purple_to_yellow:
        trigger_bonus += 12.0
    if golden_cross:
        trigger_bonus += 18.0

    state_bonus = 6.0 if current_state == "rising" else 0.0
    if previous_state == "falling" and current_state == "rising":
        state_bonus += 4.0
    signal_score = _clamp(
        12.0
        + trigger_bonus
        + main_force_power_score * 0.48
        + force_gap_score * 0.32
        + state_bonus,
    )

    return {
        "available": len(main_force) >= 3,
        "trigger_date": str(ordered[-1].time),
        "main_force": round(current_main, 6),
        "retail_force": round(current_retail, 6),
        "prev_main_force": round(prev_main, 6),
        "prev_retail_force": round(prev_retail, 6),
        "prev2_main_force": round(prev2_main, 6),
        "main_force_state": current_state,
        "prev_main_force_state": previous_state,
        "purple_to_yellow": bool(purple_to_yellow),
        "golden_cross": bool(golden_cross),
        "main_force_power_score": round(main_force_power_score, 2),
        "force_gap_score": round(force_gap_score, 2),
        "retail_pressure_score": round(retail_pressure_score, 2),
        "main_force_explosion_ratio": round(explosion_ratio, 4),
        "main_vs_retail_ratio": round(main_vs_retail_ratio, 4),
        "signal_score": round(signal_score, 2),
    }
