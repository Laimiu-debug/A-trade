"""
趋势为王策略 — 核心计算模块

Port from G:\\CODE\\趋势为王\\趋势为王_筛选器.py

三种选股模式:
  模式A: 涨停板精选  (精确率~47%, 每天2-3只)
  模式B: 涨势确认    (精确率~38%, 每天5-10只, 平均未来涨幅~22%)
  模式C: 涨停跟踪池  (精确率~57%, 均盈~30%, 次日低吸候选)
"""
from __future__ import annotations

import math
from typing import Any

from ..models import CandlePoint


def _sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _score_zt(f: dict[str, Any]) -> float:
    """涨停板模式评分 (from 趋势为王_筛选器.score_zt)"""
    s = 0.0
    # 量比(低好)
    vr = float(f.get("vol_ratio", 1.0))
    if vr < 1.0:
        s += 15
    elif vr < 1.5:
        s += 10
    elif vr < 2.0:
        s += 5
    else:
        s -= 5
    # 弹性
    mh = float(f.get("max_hist", 0))
    if mh > 80:
        s += 15
    elif mh > 50:
        s += 10
    elif mh > 30:
        s += 5
    # 3日涨幅
    g3d = f.get("gain_3d")
    if g3d is not None:
        g3d = float(g3d)
        if 25 <= g3d < 40:
            s += 12
        elif 15 <= g3d < 25:
            s += 8
        elif 10 <= g3d < 15:
            s += 5
    # 前日涨幅
    pg = float(f.get("prev_gain", 0))
    if pg > 7:
        s += 12
    elif pg > 3:
        s += 8
    elif pg > 0:
        s += 3
    # MA60上方
    if f.get("above_ma60"):
        s += 5
    # 波动
    ar = float(f.get("avg_range", 0))
    if ar > 5:
        s += 5
    elif ar > 3:
        s += 3
    # 板块(无板块数据时为中性)
    sr = float(f.get("sector_rank", 99))
    if sr <= 5:
        s += 15
    elif sr <= 10:
        s += 10
    elif sr <= 15:
        s += 5
    sm5 = float(f.get("sector_m5", 0))
    if sm5 > 3:
        s += 5
    elif sm5 > 0:
        s += 2
    return s


def _score_confirm(f: dict[str, Any]) -> float:
    """涨势确认模式评分 (from 趋势为王_筛选器.score_confirm)"""
    s = 0.0
    g5d = f.get("gain_5d")
    g3d = f.get("gain_3d")
    if g5d is not None and float(g5d) >= 15:
        s += 15
    elif g5d is not None and float(g5d) >= 10:
        s += 10
    elif g3d is not None and float(g3d) >= 10:
        s += 8
    elif g3d is not None and float(g3d) >= 7:
        s += 5
    # 连阳
    cy = int(f.get("consec_yang", 0))
    y5 = int(f.get("yang_5d", 0))
    if cy >= 4:
        s += 12
    elif cy >= 3:
        s += 8
    elif y5 >= 3:
        s += 5
    # 弹性
    mh = float(f.get("max_hist", 0))
    if mh > 50:
        s += 10
    elif mh > 30:
        s += 5
    # 均线
    if f.get("above_ma60"):
        s += 5
    if f.get("ma_bullish"):
        s += 5
    # 板块
    sr = float(f.get("sector_rank", 99))
    if sr <= 10:
        s += 8
    elif sr <= 20:
        s += 4
    return s


def calculate_trend_king_signal(
    candles: list[CandlePoint],
    sector_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从K线数据计算趋势为王策略的所有指标, 存入 snapshot.

    Port from 趋势为王_筛选器.analyze_stock(), adapted for CandlePoint.

    sector_data: 可选, {"sector_name": str, "sector_rank": int, "sector_m5": float}
    """
    base: dict[str, Any] = {
        "has_data": False,
        "trigger_date": str(candles[-1].time) if candles else "",
        "mode_a": False,
        "mode_b": False,
        "mode_c": False,
    }

    if len(candles) < 120:
        return base

    closes = [max(0.0, float(c.close)) for c in candles]
    opens = [max(0.0, float(c.open)) for c in candles]
    highs = [max(0.0, float(c.high)) for c in candles]
    lows = [max(0.0, float(c.low)) for c in candles]
    vols = [max(0.0, float(c.volume)) for c in candles]

    latest_close = closes[-1]
    prev_close = closes[-2]
    if latest_close <= 0 or prev_close <= 0:
        return base

    # ── 涨幅指标 ──
    day_gain = (latest_close - prev_close) / prev_close * 100.0

    gain_3d: float | None = None
    if len(closes) >= 4 and closes[-4] > 0:
        gain_3d = (closes[-1] - closes[-4]) / closes[-4] * 100.0

    gain_5d: float | None = None
    if len(closes) >= 6 and closes[-6] > 0:
        gain_5d = (closes[-1] - closes[-6]) / closes[-6] * 100.0

    prev_gain = 0.0
    if len(closes) >= 3 and closes[-3] > 0:
        prev_gain = (closes[-2] - closes[-3]) / closes[-3] * 100.0

    # ── 量比 (当日量 / 20日均量) ──
    vol_ma20 = _sma(vols[-20:], 20) or 0.0
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

    # ── 历史弹性 (过去250天内, 任意20日最大涨幅) ──
    max_hist = 0.0
    lookback_start = max(0, len(candles) - 250)
    for i in range(lookback_start, len(candles) - 10):
        if closes[i] > 0:
            future_idx = min(i + 20, len(closes) - 1)
            g = (closes[future_idx] - closes[i]) / closes[i] * 100.0
            if g > max_hist:
                max_hist = g

    # ── 价格位置 (60日区间) ──
    h60 = max(highs[-60:])
    l60 = min(lows[-60:])
    price_pos = (latest_close - l60) / (h60 - l60) * 100.0 if h60 > l60 else 50.0

    # ── 均线 ──
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    above_ma60 = ma60 is not None and latest_close > ma60
    ma_bullish = (
        ma5 is not None
        and ma10 is not None
        and ma20 is not None
        and ma5 > ma10 > ma20
    )

    # ── 日均振幅 (60日) ──
    ranges = []
    for i in range(-60, 0):
        if closes[i] > 0:
            ranges.append((highs[i] - lows[i]) / closes[i] * 100.0)
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0

    # ── 连续阳线 ──
    consec_yang = 0
    for j in range(len(candles) - 1, max(len(candles) - 6, -1), -1):
        if closes[j] >= opens[j]:
            consec_yang += 1
        else:
            break

    # ── 近5日阳线数 ──
    yang_5d = sum(1 for i in range(-5, 0) if closes[i] >= opens[i])

    # ── 模式评分 ──
    _sector_rank = 99
    _sector_m5 = 0.0
    _sector_name = None
    if sector_data and isinstance(sector_data, dict):
        _sector_rank = int(sector_data.get("sector_rank", 99))
        _sector_m5 = float(sector_data.get("sector_m5", 0.0))
        _sector_name = sector_data.get("sector_name")

    scoring_context = {
        "vol_ratio": vol_ratio,
        "max_hist": max_hist,
        "gain_3d": gain_3d,
        "gain_5d": gain_5d,
        "prev_gain": prev_gain,
        "above_ma60": above_ma60,
        "ma_bullish": ma_bullish,
        "avg_range": avg_range,
        "consec_yang": consec_yang,
        "yang_5d": yang_5d,
        "sector_rank": _sector_rank,
        "sector_m5": _sector_m5,
    }
    zt_score = _score_zt(scoring_context)
    cfm_score = _score_confirm(scoring_context)

    # ── 模式触发判断 ──
    mode_a = day_gain >= 9.5 and max_hist >= 30

    mode_b = False
    if day_gain < 9.5:
        g3d_ok = gain_3d is not None and gain_3d >= 7
        g5d_ok = gain_5d is not None and gain_5d >= 10
        if g3d_ok or g5d_ok:
            mode_b = max_hist >= 30

    mode_c = mode_a and vol_ratio < 1.5 and max_hist > 50 and prev_gain > 3

    # ── 次日低吸买价 ──
    buy_aggressive = round(latest_close * 0.97, 2)
    buy_conservative = round(latest_close * 0.95, 2)

    base.update({
        "has_data": True,
        "trigger_date": str(candles[-1].time),
        "close": latest_close,
        "day_gain": round(day_gain, 2),
        "gain_3d": round(gain_3d, 2) if gain_3d is not None else None,
        "gain_5d": round(gain_5d, 2) if gain_5d is not None else None,
        "prev_gain": round(prev_gain, 2),
        "vol_ratio": round(vol_ratio, 2),
        "max_hist": round(max_hist, 1),
        "price_pos": round(price_pos, 1),
        "above_ma60": above_ma60,
        "ma_bullish": ma_bullish,
        "avg_range": round(avg_range, 2),
        "consec_yang": consec_yang,
        "yang_5d": yang_5d,
        "zt_score": zt_score,
        "cfm_score": cfm_score,
        "mode_a": mode_a,
        "mode_b": mode_b,
        "mode_c": mode_c,
        "sector_name": _sector_name,
        "sector_rank": _sector_rank,
        "sector_m5": _sector_m5,
        "buy_aggressive": buy_aggressive,
        "buy_conservative": buy_conservative,
    })
    return base


def evaluate_trend_king_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据策略参数评估趋势为王信号, 返回评估结果."""
    raw_params = params if isinstance(params, dict) else {}

    has_data = bool(indicator.get("has_data"))
    mode = str(raw_params.get("mode", "all")).strip().lower()

    # ── 可调阈值 ──
    min_day_gain_a = _safe_float(raw_params.get("min_day_gain_a"), 9.5)
    min_hist = _safe_float(raw_params.get("min_hist"), 30.0)
    min_gain_3d_b = _safe_float(raw_params.get("min_gain_3d_b"), 7.0)
    min_gain_5d_b = _safe_float(raw_params.get("min_gain_5d_b"), 10.0)
    max_vol_ratio_c = _safe_float(raw_params.get("max_vol_ratio_c"), 1.5)
    min_hist_c = _safe_float(raw_params.get("min_hist_c"), 50.0)
    min_prev_gain_c = _safe_float(raw_params.get("min_prev_gain_c"), 3.0)

    day_gain = _safe_float(indicator.get("day_gain"), 0.0)
    gain_3d = indicator.get("gain_3d")
    gain_5d = indicator.get("gain_5d")
    max_hist_val = _safe_float(indicator.get("max_hist"), 0.0)
    vol_ratio = _safe_float(indicator.get("vol_ratio"), 1.0)
    prev_gain = _safe_float(indicator.get("prev_gain"), 0.0)

    # ── 重新评估各模式 (支持参数调节) ──
    mode_a = has_data and day_gain >= min_day_gain_a and max_hist_val >= min_hist
    mode_b = False
    if has_data and day_gain < min_day_gain_a:
        g3d_ok = gain_3d is not None and _safe_float(gain_3d, 0) >= min_gain_3d_b
        g5d_ok = gain_5d is not None and _safe_float(gain_5d, 0) >= min_gain_5d_b
        if g3d_ok or g5d_ok:
            mode_b = max_hist_val >= min_hist
    mode_c = (
        has_data
        and mode_a
        and vol_ratio < max_vol_ratio_c
        and max_hist_val > min_hist_c
        and prev_gain > min_prev_gain_c
    )

    # ── 根据选中模式判断触发 ──
    triggered = False
    if mode == "a":
        triggered = mode_a
    elif mode == "b":
        triggered = mode_b
    elif mode == "c":
        triggered = mode_c
    else:  # "all"
        triggered = mode_a or mode_b

    # ── 评分归一化 ──
    zt_score = _safe_float(indicator.get("zt_score"), 0.0)
    cfm_score = _safe_float(indicator.get("cfm_score"), 0.0)

    if mode_a:
        raw_score = zt_score
    elif mode_b:
        raw_score = cfm_score
    else:
        raw_score = max(zt_score, cfm_score)

    signal_score = min(100.0, max(0.0, raw_score / 92.0 * 100.0))

    return {
        "signal": triggered,
        "mode_a": mode_a,
        "mode_b": mode_b,
        "mode_c": mode_c,
        "signal_score": signal_score,
        "zt_score": zt_score,
        "cfm_score": cfm_score,
        "day_gain": day_gain,
        "vol_ratio": vol_ratio,
        "max_hist": max_hist_val,
        "trigger_date": str(indicator.get("trigger_date", "")),
    }


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return float(parsed)
