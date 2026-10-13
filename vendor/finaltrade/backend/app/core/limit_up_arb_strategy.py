"""涨停套利 — 昨日涨停 + 今日放量上攻确认；入场为信号日收盘，出场由回测参数决定。"""

from __future__ import annotations

import math
from typing import Any, Sequence

from ..models import CandlePoint
from .market_momentum import _is_limit_up


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return float(parsed)


def _sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(float(item) for item in values[-window:]) / float(window)


def resolve_limit_up_arb_params(params: dict[str, Any] | None) -> dict[str, Any]:
    raw = params if isinstance(params, dict) else {}
    return {
        "min_day_gain": _safe_float(raw.get("min_day_gain"), 3.0),
        "min_volume_ratio_prev": _safe_float(raw.get("min_volume_ratio_prev"), 1.2),
        "min_volume_ratio_ma5": _safe_float(raw.get("min_volume_ratio_ma5"), 0.0),
        "sector_rank_weight": _safe_float(raw.get("sector_rank_weight"), 0.08),
    }


def resolve_limit_up_arb_exit_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """出场配置与回测页一致：take_profit/stop_loss 为比例（如 0.03 = 3%），0 表示不启用。"""
    raw = config if isinstance(config, dict) else {}
    ambiguity = str(raw.get("ambiguity_policy", "conservative"))
    if ambiguity not in ("conservative", "optimistic"):
        ambiguity = "conservative"
    return {
        "take_profit": max(0.0, _safe_float(raw.get("take_profit"), 0.0)),
        "stop_loss": max(0.0, _safe_float(raw.get("stop_loss"), 0.0)),
        "ambiguity_policy": ambiguity,
    }


def calculate_limit_up_arb_signal(
    candles: list[CandlePoint],
    *,
    symbol: str = "",
    sector_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "has_data": False,
        "trigger_date": str(candles[-1].time) if candles else "",
        "signal": False,
    }
    if len(candles) < 3:
        return base

    closes = [max(0.0, float(item.close)) for item in candles]
    volumes = [max(0.0, float(item.volume)) for item in candles]
    latest_close = closes[-1]
    prev_close = closes[-2]
    prev_prev_close = closes[-3]
    if latest_close <= 0 or prev_close <= 0 or prev_prev_close <= 0:
        return base

    prev_bar = {
        "close": prev_close,
        "open": float(candles[-2].open),
        "high": float(candles[-2].high),
        "low": float(candles[-2].low),
        "volume": volumes[-2],
    }
    prev_limit_up = _is_limit_up(prev_bar, prev_prev_close, symbol)
    day_gain = (latest_close - prev_close) / prev_close * 100.0
    volume_ratio_prev = volumes[-1] / volumes[-2] if volumes[-2] > 0 else 0.0
    vol_ma5 = _sma(volumes, 5)
    volume_ratio_ma5 = volumes[-1] / vol_ma5 if vol_ma5 and vol_ma5 > 0 else 0.0

    sector_rank = 99
    if sector_data and isinstance(sector_data, dict):
        sector_rank = int(sector_data.get("sector_rank", 99))

    limit_up_date = str(candles[-2].time) if prev_limit_up else ""
    base.update(
        {
            "has_data": True,
            "trigger_date": str(candles[-1].time),
            "limit_up_date": limit_up_date,
            "prev_limit_up": prev_limit_up,
            "day_gain": round(day_gain, 2),
            "volume_ratio_prev": round(volume_ratio_prev, 3),
            "volume_ratio_ma5": round(volume_ratio_ma5, 3),
            "sector_rank": sector_rank,
            "entry_price": round(latest_close, 4),
            "entry_timing": "same_day_close",
        }
    )
    return base


def evaluate_limit_up_arb_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = resolve_limit_up_arb_params(params)
    has_data = bool(indicator.get("has_data"))
    prev_limit_up = bool(indicator.get("prev_limit_up"))
    day_gain = _safe_float(indicator.get("day_gain"), 0.0)
    volume_ratio_prev = _safe_float(indicator.get("volume_ratio_prev"), 0.0)
    volume_ratio_ma5 = _safe_float(indicator.get("volume_ratio_ma5"), 0.0)

    volume_ok = volume_ratio_prev >= cfg["min_volume_ratio_prev"]
    if cfg["min_volume_ratio_ma5"] > 0:
        volume_ok = volume_ok and volume_ratio_ma5 >= cfg["min_volume_ratio_ma5"]

    signal = (
        has_data
        and prev_limit_up
        and day_gain >= cfg["min_day_gain"]
        and volume_ok
    )

    score = 0.0
    if signal:
        score += min(40.0, max(0.0, (day_gain - cfg["min_day_gain"]) * 4.0))
        score += min(35.0, max(0.0, (volume_ratio_prev - cfg["min_volume_ratio_prev"]) * 20.0))
        if cfg["min_volume_ratio_ma5"] > 0:
            score += min(15.0, max(0.0, (volume_ratio_ma5 - cfg["min_volume_ratio_ma5"]) * 8.0))
        sector_rank = int(indicator.get("sector_rank", 99))
        sector_weight = max(0.0, min(0.3, cfg["sector_rank_weight"]))
        if sector_rank <= 5:
            score += 100.0 * sector_weight
        elif sector_rank <= 10:
            score += 70.0 * sector_weight
        elif sector_rank <= 20:
            score += 35.0 * sector_weight

    return {
        "signal": signal,
        "signal_score": round(min(100.0, score), 2),
        "trigger_date": str(indicator.get("trigger_date") or ""),
        "label": "涨停套利",
        "signal_stage": "confirmed" if signal else "none",
        "prev_limit_up": prev_limit_up,
        "limit_up_date": str(indicator.get("limit_up_date") or ""),
        "day_gain": day_gain,
        "volume_ratio_prev": volume_ratio_prev,
        "volume_ratio_ma5": volume_ratio_ma5,
        "entry_price": indicator.get("entry_price"),
        "entry_timing": str(indicator.get("entry_timing") or "same_day_close"),
    }


def evaluate_limit_up_arb_exit(
    entry_price: float,
    next_candle: CandlePoint,
    *,
    exit_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按回测同款止盈/止损规则评估次日出场（未触发则收盘卖）。"""
    cfg = resolve_limit_up_arb_exit_config(exit_config)
    entry = max(0.0, float(entry_price))
    if entry <= 0:
        return {"exit_price": 0.0, "exit_reason": "invalid_entry", "pnl_ratio": 0.0, "win": False}

    next_high = max(0.0, float(next_candle.high))
    next_low = max(0.0, float(next_candle.low))
    next_close = max(0.0, float(next_candle.close))

    stop_price = entry * (1.0 - cfg["stop_loss"]) if cfg["stop_loss"] > 0 else None
    take_price = entry * (1.0 + cfg["take_profit"]) if cfg["take_profit"] > 0 else None

    exit_price = next_close
    exit_reason = "time_exit"
    hit_take_profit = False
    hit_stop_loss = False

    if stop_price is not None and next_low <= stop_price:
        # 同日双触达时:乐观口径取止盈,保守口径取止损
        if cfg["ambiguity_policy"] == "optimistic" and take_price is not None and next_high >= take_price:
            exit_price = take_price
            exit_reason = "take_profit"
            hit_take_profit = True
        else:
            exit_price = stop_price
            exit_reason = "stop_loss"
            hit_stop_loss = True
    elif take_price is not None and next_high >= take_price:
        exit_price = take_price
        exit_reason = "take_profit"
        hit_take_profit = True

    pnl_ratio = (exit_price - entry) / entry
    return {
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "pnl_ratio": round(pnl_ratio, 6),
        "win": pnl_ratio > 0,
        "hit_take_profit": hit_take_profit,
        "hit_stop_loss": hit_stop_loss,
    }
