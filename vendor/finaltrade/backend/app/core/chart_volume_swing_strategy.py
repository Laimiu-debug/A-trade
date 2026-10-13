"""图形 + 量价波段策略（chart_volume_swing_v1）。

按 K 线结构与量价关系做平台/箱体突破后的波段，不依赖 ret40、价格位置、
「早期/晚期」、脉冲涨停等排除逻辑——涨过的票、高位横盘同样可以出信号。

四层闭环：
  1. 图形：箱体/平台 + 有效突破
  2. 量价：上涨日量 vs 下跌日量、突破放量、回踩缩量
  3. 确认：回踩箱顶（A）或突破后站稳（B）
  4. 评分：图形质量 + 量价健康（供排序，非因子拟合）

纯计算模块，独立回测验证后再考虑接入主系统。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from ..models import CandlePoint


@dataclass(frozen=True)
class ChartVolumeSwingParams:
    box_windows: tuple[int, ...] = (20, 30, 40)
    box_range_min: float = 0.06
    box_range_max: float = 0.35
    breakout_vol_ratio: float = 1.5
    min_close_strength: float = 0.45
    max_breakout_wick_ratio: float = 0.55
    hold_ratio: float = 0.97
    retest_band: float = 0.04
    shrink_vol_vs_ma5: float = 0.75
    vol_up_down_ratio: float = 1.3
    vol_lookback: int = 20
    confirm_hold_days: int = 2
    max_breakout_age_days: int = 18
    min_score: float = 50.0
    vol_shrink_in_box_max: float = 1.05
    retest_recent_days: int = 5
    min_days_after_breakout: int = 2


def resolve_chart_volume_swing_params(params: dict[str, Any] | None) -> ChartVolumeSwingParams:
    raw = params if isinstance(params, dict) else {}
    base = ChartVolumeSwingParams()

    def _f(key: str, fallback: float) -> float:
        try:
            v = float(raw.get(key, fallback))
        except Exception:
            return float(fallback)
        return v if math.isfinite(v) else float(fallback)

    def _i(key: str, fallback: int) -> int:
        try:
            return int(raw.get(key, fallback))
        except Exception:
            return int(fallback)

    wins = raw.get("box_windows")
    if isinstance(wins, (list, tuple)) and wins:
        box_windows = tuple(max(10, int(x)) for x in wins)
    else:
        box_windows = base.box_windows

    return ChartVolumeSwingParams(
        box_windows=box_windows,
        box_range_min=_f("box_range_min", base.box_range_min),
        box_range_max=_f("box_range_max", base.box_range_max),
        breakout_vol_ratio=_f("breakout_vol_ratio", base.breakout_vol_ratio),
        min_close_strength=_f("min_close_strength", base.min_close_strength),
        max_breakout_wick_ratio=_f("max_breakout_wick_ratio", base.max_breakout_wick_ratio),
        hold_ratio=_f("hold_ratio", base.hold_ratio),
        retest_band=_f("retest_band", base.retest_band),
        shrink_vol_vs_ma5=_f("shrink_vol_vs_ma5", base.shrink_vol_vs_ma5),
        vol_up_down_ratio=_f("vol_up_down_ratio", base.vol_up_down_ratio),
        vol_lookback=max(10, _i("vol_lookback", base.vol_lookback)),
        confirm_hold_days=max(1, min(4, _i("confirm_hold_days", base.confirm_hold_days))),
        max_breakout_age_days=max(5, _i("max_breakout_age_days", base.max_breakout_age_days)),
        min_score=_f("min_score", base.min_score),
        vol_shrink_in_box_max=_f("vol_shrink_in_box_max", base.vol_shrink_in_box_max),
        retest_recent_days=max(2, _i("retest_recent_days", base.retest_recent_days)),
        min_days_after_breakout=max(1, _i("min_days_after_breakout", base.min_days_after_breakout)),
    )


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def _safe_float(v: Any, fb: float = 0.0) -> float:
    try:
        x = float(v)
    except Exception:
        return fb
    return x if math.isfinite(x) else fb


def _ma_at(values: Sequence[float], idx: int, w: int) -> float:
    s = max(0, idx - w + 1)
    seg = values[s : idx + 1]
    return sum(seg) / float(len(seg)) if seg else float(values[idx])


def _vol_ratio(volumes: Sequence[float], idx: int, w: int = 20) -> float:
    base = _ma_at(volumes, idx - 1, w) if idx > 0 else volumes[idx]
    return float(volumes[idx]) / max(base, 1e-9)


def _close_strength(opens: Sequence[float], highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], idx: int) -> float:
    span = max(float(highs[idx]) - float(lows[idx]), 1e-6)
    return (float(closes[idx]) - float(lows[idx])) / span


def _upper_wick_ratio(opens: Sequence[float], highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], idx: int) -> float:
    span = max(float(highs[idx]) - float(lows[idx]), 1e-6)
    top = max(float(opens[idx]), float(closes[idx]))
    return (float(highs[idx]) - top) / span


def _ma_bullish(closes: Sequence[float], idx: int) -> bool:
    ma5 = _ma_at(closes, idx, 5)
    ma10 = _ma_at(closes, idx, 10)
    ma20 = _ma_at(closes, idx, 20)
    return ma5 > ma10 > ma20 and closes[idx] > ma20


def _ret_n(closes: Sequence[float], idx: int, n: int) -> float:
    s = idx - n
    if s < 0 or closes[s] <= 0:
        return 0.0
    return (closes[idx] - closes[s]) / closes[s]


def _price_pos(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], idx: int, w: int = 60) -> float:
    s = max(0, idx - w + 1)
    h = max(highs[s : idx + 1])
    l = min(lows[s : idx + 1])
    if h <= l:
        return 0.5
    return (closes[idx] - l) / (h - l)


def _evaluate_box(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    *,
    box_end_idx: int,
    box_window: int,
    cfg: ChartVolumeSwingParams,
) -> dict[str, Any] | None:
    start = box_end_idx - box_window + 1
    if start < 0:
        return None

    win_highs = list(highs[start : box_end_idx + 1])
    win_lows = list(lows[start : box_end_idx + 1])
    win_closes = list(closes[start : box_end_idx + 1])
    win_vols = list(volumes[start : box_end_idx + 1])
    if len(win_closes) < box_window:
        return None

    box_high = max(win_closes)
    box_low = min(win_lows)
    if box_low <= 0:
        return None
    box_range = (max(win_highs) - box_low) / box_low
    if not (cfg.box_range_min <= box_range <= cfg.box_range_max):
        return None

    half = len(win_closes) // 2
    front_vol = sum(win_vols[:half]) / max(1, half)
    back_vol = sum(win_vols[half:]) / max(1, len(win_vols) - half)
    vol_shrink = back_vol / front_vol if front_vol > 0 else 1.0
    if vol_shrink > cfg.vol_shrink_in_box_max:
        return None

    return {
        "box_window": box_window,
        "box_high": float(box_high),
        "box_low": float(box_low),
        "box_range": float(box_range),
        "vol_shrink": float(vol_shrink),
        "box_end_idx": int(box_end_idx),
    }


def _volume_price_health(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    idx: int,
    *,
    cfg: ChartVolumeSwingParams,
) -> tuple[bool, dict[str, Any]]:
    """趋势票 v1 量价逻辑：上涨日量 vs 下跌日量，排除明显量价背离。"""
    lb = cfg.vol_lookback
    s = max(1, idx - lb + 1)
    up_vol = 0.0
    down_vol = 0.0
    for j in range(s, idx + 1):
        if closes[j] >= closes[j - 1]:
            up_vol += volumes[j]
        else:
            down_vol += volumes[j]
    ratio = up_vol / max(down_vol, 1.0)

    # 近 5 日价涨量缩
    if idx >= 5 and closes[idx - 5] > 0:
        ret5 = (closes[idx] - closes[idx - 5]) / closes[idx - 5]
        vol5_now = sum(volumes[idx - 4 : idx + 1]) / 5.0
        vol5_prev = sum(volumes[idx - 9 : idx - 4]) / 5.0 if idx >= 9 else vol5_now
        divergence = ret5 > 0.03 and vol5_prev > 0 and vol5_now < vol5_prev * 0.75
    else:
        ret5 = 0.0
        divergence = False

    ok = ratio >= cfg.vol_up_down_ratio and not divergence
    return ok, {"up_down_vol_ratio": round(ratio, 4), "ret5": round(ret5, 4), "vol_divergence": divergence}


def _breakout_bar_ok(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    idx: int,
    *,
    box_high: float,
    cfg: ChartVolumeSwingParams,
) -> tuple[bool, dict[str, Any]]:
    close_px = closes[idx]
    if close_px <= box_high * 1.003:
        return False, {}
    vr = _vol_ratio(volumes, idx)
    cstr = _close_strength(opens, highs, lows, closes, idx)
    wick = _upper_wick_ratio(opens, highs, lows, closes, idx)
    bearish = close_px < opens[idx]
    huge_wick_dump = wick >= cfg.max_breakout_wick_ratio and vr >= 2.0 and bearish
    ok = vr >= cfg.breakout_vol_ratio and cstr >= cfg.min_close_strength and not huge_wick_dump
    return ok, {
        "breakout_vol_ratio": round(vr, 4),
        "close_strength": round(cstr, 4),
        "upper_wick_ratio": round(wick, 4),
        "breakout_price": float(close_px),
    }


def _find_chart_setup(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    *,
    cfg: ChartVolumeSwingParams,
) -> dict[str, Any] | None:
    today = len(closes) - 1
    if today < 70:
        return None

    best: dict[str, Any] | None = None
    scan_lo = max(max(cfg.box_windows), today - cfg.max_breakout_age_days)

    for breakout_idx in range(today, scan_lo - 1, -1):
        for box_window in sorted(cfg.box_windows, reverse=True):
            box_end = breakout_idx - 1
            if box_end < box_window - 1:
                continue
            box = _evaluate_box(closes, highs, lows, volumes, box_end_idx=box_end, box_window=box_window, cfg=cfg)
            if box is None:
                continue
            b_ok, b_meta = _breakout_bar_ok(
                opens, highs, lows, closes, volumes, breakout_idx,
                box_high=float(box["box_high"]), cfg=cfg,
            )
            if not b_ok:
                continue
            candidate = {**box, **b_meta, "breakout_idx": int(breakout_idx)}
            if best is None or candidate["breakout_idx"] > best["breakout_idx"]:
                best = candidate
            break
        if best is not None and best["breakout_idx"] == breakout_idx:
            break
    return best


def _confirm_pullback(
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    *,
    setup: dict[str, Any],
    cfg: ChartVolumeSwingParams,
) -> tuple[bool, dict[str, Any]]:
    today = len(closes) - 1
    breakout_idx = int(setup["breakout_idx"])
    meta: dict[str, Any] = {}
    if today - breakout_idx < cfg.min_days_after_breakout:
        return False, meta
    box_high = float(setup["box_high"])
    band_hi = box_high * (1.0 + cfg.retest_band)
    band_lo = box_high * (1.0 - cfg.retest_band)
    recent_lo = max(breakout_idx + 1, today - cfg.retest_recent_days + 1)
    retest_idx = -1
    retest_low = None
    for j in range(recent_lo, today + 1):
        if band_lo <= lows[j] <= band_hi:
            if retest_low is None or lows[j] < retest_low:
                retest_low = float(lows[j])
                retest_idx = j
    if retest_idx < 0:
        return False, meta
    ma5 = _ma_at(volumes, today, 5)
    shrink = volumes[today] <= ma5 * cfg.shrink_vol_vs_ma5
    hold = closes[today] >= box_high * cfg.hold_ratio
    meta = {
        "pullback_shrink_ratio": round(float(volumes[today]) / max(ma5, 1e-9), 4),
        "retest_distance": round(abs(retest_low - box_high) / max(box_high, 0.01), 4),
        "retest_idx_offset": today - retest_idx,
    }
    return shrink and hold, meta


def _confirm_hold_above(
    closes: Sequence[float],
    opens: Sequence[float],
    volumes: Sequence[float],
    *,
    setup: dict[str, Any],
    cfg: ChartVolumeSwingParams
) -> bool:
    today = len(closes) - 1
    breakout_idx = int(setup["breakout_idx"])
    need = cfg.confirm_hold_days
    if today - breakout_idx < cfg.min_days_after_breakout + need - 1:
        return False
    box_high = float(setup["box_high"])
    level = box_high * cfg.hold_ratio
    for j in range(today - need + 1, today + 1):
        if closes[j] < level:
            return False
        if closes[j] < opens[j] and _vol_ratio(volumes, j, 5) > 1.3:
            return False
    return True


def calculate_chart_volume_swing_signal(
    candles: list[CandlePoint],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = resolve_chart_volume_swing_params(params)
    trigger_date = str(candles[-1].time) if candles else ""
    base: dict[str, Any] = {"has_data": False, "signal": False, "trigger_date": trigger_date}
    if len(candles) < 80:
        return base

    opens = [float(c.open) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    closes = [max(0.01, float(c.close)) for c in candles]
    volumes = [max(0.0, float(c.volume)) for c in candles]
    last = len(candles) - 1

    setup = _find_chart_setup(opens, highs, lows, closes, volumes, cfg=cfg)
    if setup is None:
        base["has_data"] = True
        base["reject_reason"] = "no_chart_setup"
        return base

    vp_ok, vp_meta = _volume_price_health(opens, highs, lows, closes, volumes, last, cfg=cfg)
    ma_ok = _ma_bullish(closes, last)

    confirm_type = ""
    pullback_meta: dict[str, Any] = {}
    pb_ok, pullback_meta = _confirm_pullback(lows, closes, volumes, setup=setup, cfg=cfg)
    if pb_ok:
        confirm_type = "pullback_retest"
    elif _confirm_hold_above(closes, opens, volumes, setup=setup, cfg=cfg):
        confirm_type = "hold_above"

    base.update(
        {
            "has_data": True,
            "signal": bool(confirm_type and vp_ok and ma_ok),
            "confirm_type": confirm_type,
            "chart_pattern": "box_breakout",
            "box_high": round(float(setup["box_high"]), 4),
            "box_low": round(float(setup["box_low"]), 4),
            "box_range": round(float(setup["box_range"]), 4),
            "box_window": int(setup["box_window"]),
            "breakout_offset": last - int(setup["breakout_idx"]),
            "breakout_vol_ratio": setup.get("breakout_vol_ratio"),
            "close_strength": setup.get("close_strength"),
            "vol_shrink_in_box": round(float(setup["vol_shrink"]), 4),
            "ma_bullish": ma_ok,
            "volume_price_ok": vp_ok,
            "ret_40": round(_ret_n(closes, last, 40), 4),
            "price_pos_60": round(_price_pos(highs, lows, closes, last), 4),
            **vp_meta,
            **pullback_meta,
        }
    )
    if not vp_ok:
        base["reject_reason"] = "volume_price"
    elif not ma_ok:
        base["reject_reason"] = "ma_structure"
    elif not confirm_type:
        base["reject_reason"] = "no_confirm"
    return base


def _freshness_score(breakout_offset: float, cfg: ChartVolumeSwingParams) -> float:
    """突破后等待确认：3~10 日最佳，过早追、过晚失效。"""
    if breakout_offset <= 1:
        return 35.0
    if breakout_offset <= 10:
        return _clamp(100.0 - abs(breakout_offset - 6.0) / 6.0 * 35.0)
    return _clamp(55.0 - (breakout_offset - 10.0) / max(cfg.max_breakout_age_days - 10, 1) * 45.0)


def _breakout_vol_score(bvr: float, cfg: ChartVolumeSwingParams) -> float:
    """突破放量：1.5~2.5 最佳，过低无效、过高易脉冲。"""
    if bvr < cfg.breakout_vol_ratio:
        return 0.0
    if bvr <= 2.2:
        return _clamp((bvr - cfg.breakout_vol_ratio) / max(2.2 - cfg.breakout_vol_ratio, 0.01) * 100.0)
    return _clamp(100.0 - (bvr - 2.2) / 1.8 * 80.0)


def _box_range_score(box_range: float, cfg: ChartVolumeSwingParams) -> float:
    """平台振幅：10%~20% 最理想。"""
    if box_range < cfg.box_range_min or box_range > cfg.box_range_max:
        return 0.0
    sweet = 0.15
    dist = abs(box_range - sweet)
    return _clamp(100.0 - dist / 0.12 * 100.0)


def evaluate_chart_volume_swing_signal(indicator: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = resolve_chart_volume_swing_params(params)
    if not indicator.get("has_data") or not indicator.get("signal"):
        reason = str(indicator.get("reject_reason") or "no_signal")
        return {"signal": False, "signal_score": 0.0, "reject_reason": reason}

    confirm = str(indicator.get("confirm_type") or "")
    box_range = _safe_float(indicator.get("box_range"), 0.15)
    bvr = _safe_float(indicator.get("breakout_vol_ratio"), 1.0)
    shrink = _safe_float(indicator.get("vol_shrink_in_box"), 1.0)
    vp = _safe_float(indicator.get("up_down_vol_ratio"), 1.0)
    cstr = _safe_float(indicator.get("close_strength"), 0.5)
    box_window = _safe_float(indicator.get("box_window"), 30.0)
    breakout_offset = _safe_float(indicator.get("breakout_offset"), 5.0)

    range_score = _box_range_score(box_range, cfg)
    breakout_score = _breakout_vol_score(bvr, cfg)
    shrink_score = _clamp((1.05 - shrink) / 0.35 * 100.0)
    vp_score = _clamp((vp - cfg.vol_up_down_ratio) / 1.5 * 100.0)
    quality_score = _clamp(cstr * 100.0)
    length_score = _clamp((box_window - 20.0) / 20.0 * 100.0)
    freshness_score = _freshness_score(breakout_offset, cfg)

    retest_score = 50.0
    shrink_today_score = 50.0
    if confirm == "pullback_retest":
        retest_dist = _safe_float(indicator.get("retest_distance"), 0.03)
        retest_score = _clamp(100.0 - retest_dist / max(cfg.retest_band, 0.01) * 100.0)
        pb_shrink = _safe_float(indicator.get("pullback_shrink_ratio"), 0.8)
        shrink_today_score = _clamp((cfg.shrink_vol_vs_ma5 - pb_shrink) / max(cfg.shrink_vol_vs_ma5, 0.01) * 100.0)
        confirm_bonus = 8.0
    else:
        confirm_bonus = 3.0

    score = _clamp(
        range_score * 0.10
        + breakout_score * 0.16
        + shrink_score * 0.14
        + vp_score * 0.28
        + quality_score * 0.06
        + length_score * 0.06
        + freshness_score * 0.10
        + retest_score * 0.06
        + shrink_today_score * 0.06
        + confirm_bonus
    )

    breakdown = {
        "range": round(range_score, 2),
        "breakout": round(breakout_score, 2),
        "box_vol_shrink": round(shrink_score, 2),
        "volume_price": round(vp_score, 2),
        "candle_quality": round(quality_score, 2),
        "box_length": round(length_score, 2),
        "freshness": round(freshness_score, 2),
        "retest": round(retest_score, 2),
        "pullback_shrink": round(shrink_today_score, 2),
        "confirm_bonus": round(confirm_bonus, 2),
    }

    if score < cfg.min_score:
        return {
            "signal": False,
            "signal_score": round(score, 2),
            "score_breakdown": breakdown,
            "reject_reason": "min_score",
        }

    grade = "C"
    if score >= 75:
        grade = "A"
    elif score >= 62:
        grade = "B"
    return {
        "signal": True,
        "signal_score": round(score, 2),
        "event_grade": grade,
        "confirm_type": confirm,
        "chart_pattern": str(indicator.get("chart_pattern") or "box_breakout"),
        "score_breakdown": breakdown,
    }


def dedupe_signals_by_score(signals: list[dict[str, Any]], *, window_days: int = 15) -> list[dict[str, Any]]:
    """同股 window_days 日内只保留 signal_score 最高的一笔（优中选优去重）。"""
    from datetime import datetime

    if not signals:
        return []

    def _parse(d: str) -> datetime:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d")

    def _days(a: str, b: str) -> int:
        return abs((_parse(a) - _parse(b)).days)

    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for s in signals:
        by_symbol.setdefault(str(s["symbol"]), []).append(s)

    out: list[dict[str, Any]] = []
    for items in by_symbol.values():
        items.sort(key=lambda x: str(x["signal_date"]))
        cluster: list[dict[str, Any]] = []
        for s in items:
            if not cluster:
                cluster = [s]
                continue
            if _days(str(s["signal_date"]), str(cluster[0]["signal_date"])) < window_days:
                cluster.append(s)
            else:
                out.append(max(cluster, key=lambda x: float(x.get("signal_score") or 0.0)))
                cluster = [s]
        if cluster:
            out.append(max(cluster, key=lambda x: float(x.get("signal_score") or 0.0)))
    return sorted(out, key=lambda x: str(x["signal_date"]))
