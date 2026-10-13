"""混合波段策略 v1 — 图形量价 + 趋势池 + THS/节奏波综合打分（独立脚本选股，不接入 FinalTrade 主程序）。"""
from __future__ import annotations

from typing import Any

from dataclasses import asdict

from ..models import CandlePoint
from .chart_volume_swing_strategy import (
    calculate_chart_volume_swing_signal,
    evaluate_chart_volume_swing_signal,
    resolve_chart_volume_swing_params,
)
from .force_rhythm_strategy import calculate_force_rhythm_signal
from .ths_volume_signal import calculate_ths_main_retail_signal


def _ma_at(values: list[float], idx: int, w: int) -> float:
    s = max(0, idx - w + 1)
    seg = values[s : idx + 1]
    return sum(seg) / len(seg) if seg else values[idx]


def _ret_n(closes: list[float], idx: int, n: int) -> float:
    s = idx - n
    if s < 0 or closes[s] <= 0:
        return 0.0
    return (closes[idx] - closes[s]) / closes[s]


def _trend_ma10_pullback(closes: list[float], vols: list[float], idx: int) -> bool:
    if idx < 45:
        return False
    ma5, ma10, ma20 = _ma_at(closes, idx, 5), _ma_at(closes, idx, 10), _ma_at(closes, idx, 20)
    if not (ma5 > ma10 > ma20 and closes[idx] > ma20):
        return False
    if _ret_n(closes, idx, 40) < 0.05:
        return False
    if not (ma10 * 0.98 <= closes[idx] <= ma10 * 1.03):
        return False
    if vols[idx] > _ma_at(vols, idx, 5) * 0.85:
        return False
    up_v = down_v = 0.0
    for j in range(max(1, idx - 19), idx + 1):
        if closes[j] >= closes[j - 1]:
            up_v += vols[j]
        else:
            down_v += vols[j]
    return up_v >= down_v * 1.2


def resolve_hybrid_band_params(params: dict[str, Any] | None) -> dict[str, Any]:
    raw = params if isinstance(params, dict) else {}
    base = resolve_chart_volume_swing_params(raw)
    merged = {**asdict(base), **raw}
    merged["min_hybrid_score"] = float(raw.get("min_hybrid_score", 82.0))
    merged["ret40_rank_bonus_100"] = float(raw.get("ret40_rank_bonus_100", 8.0))
    merged["ret40_rank_bonus_200"] = float(raw.get("ret40_rank_bonus_200", 5.0))
    merged["ret40_rank_bonus_500"] = float(raw.get("ret40_rank_bonus_500", 2.0))
    return merged


def calculate_hybrid_band_signal(
    candles: list[CandlePoint],
    params: dict[str, Any] | None = None,
    *,
    ret40_rank: int | None = None,
) -> dict[str, Any]:
    cfg = resolve_hybrid_band_params(params)
    chart = calculate_chart_volume_swing_signal(candles, params=cfg)
    trigger_date = str(candles[-1].time) if candles else ""
    base: dict[str, Any] = {
        "has_data": bool(chart.get("has_data")),
        "signal": False,
        "trigger_date": trigger_date,
        "chart": chart,
    }
    if not chart.get("has_data"):
        return base

    w = candles
    ths = calculate_ths_main_retail_signal(w)
    rhythm = calculate_force_rhythm_signal(w)
    closes = [float(c.close) for c in w]
    vols = [float(c.volume) for c in w]
    i = len(w) - 1
    trend_pb = _trend_ma10_pullback(closes, vols, i)

    ev = evaluate_chart_volume_swing_signal(chart, cfg)
    chart_score = float(ev.get("signal_score") or 0.0)
    hybrid = chart_score
    hybrid += 10.0 if ths.get("purple_to_yellow") else 0.0
    hybrid += 8.0 if ths.get("golden_cross") else 0.0
    rs = float(rhythm.get("signal_score") or 0.0)
    hybrid += 12.0 if rhythm.get("trough_turn") and rs >= 55 else 0.0
    hybrid += 6.0 if trend_pb else 0.0
    if ret40_rank is not None:
        r = int(ret40_rank)
        if r <= 100:
            hybrid += cfg["ret40_rank_bonus_100"]
        elif r <= 200:
            hybrid += cfg["ret40_rank_bonus_200"]
        elif r <= 500:
            hybrid += cfg["ret40_rank_bonus_500"]

    signal = bool(chart.get("signal") or trend_pb) and hybrid >= cfg["min_hybrid_score"]
    base.update(
        {
            "signal": signal,
            "hybrid_score": round(hybrid, 2),
            "chart_score": chart_score,
            "event_grade": ev.get("event_grade"),
            "confirm_type": chart.get("confirm_type") or ("trend_ma10_pullback" if trend_pb else ""),
            "purple_to_yellow": bool(ths.get("purple_to_yellow")),
            "golden_cross": bool(ths.get("golden_cross")),
            "trough_turn": bool(rhythm.get("trough_turn")),
            "rhythm_score": rs,
            "trend_ma10_pullback": trend_pb,
            "ret_40": chart.get("ret_40"),
            "box_high": chart.get("box_high"),
        }
    )
    return base


def evaluate_hybrid_band_signal(indicator: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = resolve_hybrid_band_params(params)
    if not indicator.get("signal"):
        return {"signal": False, "signal_score": float(indicator.get("hybrid_score") or 0.0)}
    score = float(indicator.get("hybrid_score") or 0.0)
    grade = "C"
    if score >= 92:
        grade = "A"
    elif score >= 82:
        grade = "B"
    return {
        "signal": score >= cfg["min_hybrid_score"],
        "signal_score": round(score, 2),
        "event_grade": grade,
        "hybrid_score": round(score, 2),
    }
