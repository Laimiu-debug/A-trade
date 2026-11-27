"""Six authentic 顽主杯 master-trader strategies + VCP contraction breakout,
with unified signal generation, structural exits, and 4-stage market regime position rules.

Design invariants:
- Strictly causal: only uses candles up to and including the signal close (`candles[-1]`).
- Board-aware limit rules: 10% mainboard, 20% STAR/ChiNext, 30% BSE.
- All schema parameters (including master execution discipline switches) are genuinely wired.
- Independent structural exit signals (`evaluate_master_alpha_exit`) allow exits even
  after a held stock leaves the buy pool.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Sequence

MASTER_TRADER_STYLE_MAP: dict[str, str] = {
    "wujiang_mainline_v1": "wujiang_dynamic",
    "mainline_halfway_v1": "huichanglan_mainline",
    "first_to_second_v1": "xiwangjiejie_1to2",
    "highboard_sniper_v1": "dongshizhang_highboard",
    "ruhan_halfway_v1": "ruhan_halfway",
    "strong_trend_dip_v1": "songsong_defensive",
}

STYLE_TO_STRATEGY_ID: dict[str, str] = {v: k for k, v in MASTER_TRADER_STYLE_MAP.items()}

MASTER_ALPHA_STRATEGY_IDS: frozenset[str] = frozenset({
    *MASTER_TRADER_STYLE_MAP.keys(),
    "vcp_contraction_v1",
})

# Master execution defaults mirroring service/master_rules.py so R03/R05/R12 share one rule set
MASTER_STYLE_EXECUTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "wujiang_dynamic": {
        "default_slots": 4,
        "first_weight": 0.18,
        "add_weight": 0.15,
        "single_cap": 0.35,
        "stop_loss": 0.05,
        "take_profit": 0.18,
        "max_hold_days": 5,
        "use_add_on_win": True,
        "stage_slot_ratio": {"advance": 1.0, "trial": 1.0, "chop": 0.75, "decline": 0.50},
        "stage_pos_scale": {"advance": 1.0, "trial": 0.85, "chop": 0.78, "decline": 0.55},
        "stage_total_cap": {"advance": 0.88, "trial": 0.68, "chop": 0.58, "decline": 0.30},
    },
    "huichanglan_mainline": {
        "default_slots": 3,
        "first_weight": 0.20,
        "add_weight": 0.10,
        "single_cap": 0.32,
        "stop_loss": 0.048,
        "take_profit": 0.16,
        "max_hold_days": 4,
        "use_add_on_win": False,
        "stage_slot_ratio": {"advance": 1.0, "trial": 1.0, "chop": 1.0, "decline": 0.66},
        "stage_pos_scale": {"advance": 1.0, "trial": 0.82, "chop": 0.78, "decline": 0.60},
        "stage_total_cap": {"advance": 0.80, "trial": 0.60, "chop": 0.58, "decline": 0.35},
    },
    "xiwangjiejie_1to2": {
        "default_slots": 3,
        "first_weight": 0.24,
        "add_weight": 0.0,
        "single_cap": 0.25,
        "stop_loss": 0.06,
        "take_profit": 0.18,
        "max_hold_days": 3,
        "use_add_on_win": False,
        "stage_slot_ratio": {"advance": 1.0, "trial": 1.0, "chop": 1.0, "decline": 0.66},
        "stage_pos_scale": {"advance": 1.0, "trial": 0.90, "chop": 1.0, "decline": 0.55},
        "stage_total_cap": {"advance": 0.78, "trial": 0.68, "chop": 0.75, "decline": 0.25},
    },
    "dongshizhang_highboard": {
        "default_slots": 2,
        "first_weight": 0.28,
        "add_weight": 0.0,
        "single_cap": 0.30,
        "stop_loss": 0.06,
        "take_profit": 0.25,
        "max_hold_days": 4,
        "use_add_on_win": False,
        "stage_slot_ratio": {"advance": 1.0, "trial": 1.0, "chop": 1.0, "decline": 0.50},
        "stage_pos_scale": {"advance": 1.0, "trial": 0.90, "chop": 1.0, "decline": 0.85},
        "stage_total_cap": {"advance": 0.50, "trial": 0.45, "chop": 0.50, "decline": 0.35},
    },
    "ruhan_halfway": {
        "default_slots": 5,
        "first_weight": 0.18,
        "add_weight": 0.10,
        "single_cap": 0.28,
        "stop_loss": 0.05,
        "take_profit": 0.14,
        "max_hold_days": 4,
        "use_add_on_win": True,
        "stage_slot_ratio": {"advance": 1.0, "trial": 0.80, "chop": 1.0, "decline": 0.40},
        "stage_pos_scale": {"advance": 1.0, "trial": 0.90, "chop": 0.95, "decline": 0.50},
        "stage_total_cap": {"advance": 0.88, "trial": 0.72, "chop": 0.78, "decline": 0.28},
    },
    "songsong_defensive": {
        "default_slots": 4,
        "first_weight": 0.18,
        "add_weight": 0.0,
        "single_cap": 0.22,
        "stop_loss": 0.032,
        "take_profit": 0.09,
        "max_hold_days": 3,
        "use_add_on_win": False,
        "stage_slot_ratio": {"advance": 1.0, "trial": 1.0, "chop": 1.0, "decline": 0.0},
        "stage_pos_scale": {"advance": 1.0, "trial": 0.85, "chop": 0.78, "decline": 0.0},
        "stage_total_cap": {"advance": 0.82, "trial": 0.62, "chop": 0.52, "decline": 0.0},
    },
}


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    return parsed if math.isfinite(parsed) else float(fallback)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return int(fallback)


def _safe_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return fallback


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _sma(values: Sequence[float], end_idx: int, window: int) -> float | None:
    if window <= 0 or end_idx + 1 < window:
        return None
    seg = values[end_idx - window + 1 : end_idx + 1]
    return sum(seg) / float(window)


def _board_of(symbol: str) -> str:
    s = str(symbol or "").strip().lower()
    code = s[2:] if s.startswith(("sh", "sz", "bj")) else s
    if s.startswith("bj") or code.startswith(("43", "83", "87", "88", "92")):
        return "bse"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def _limit_ratio(symbol: str) -> float:
    board = _board_of(symbol)
    if board == "bse":
        return 0.30
    if board in ("star", "chinext"):
        return 0.20
    return 0.10


def _is_limit_up(close_px: float, prev_close: float, lim: float) -> bool:
    if prev_close <= 0:
        return False
    return (close_px / prev_close - 1.0) >= (lim - 0.005)


def _grade_from_score(score: float) -> str:
    if score >= 80.0:
        return "A"
    if score >= 65.0:
        return "B"
    return "C"


def calc_ashare_lot_shares(budget: float, exec_price: float, symbol: str) -> int:
    """Compute exact A-share integer lot shares (STAR board min 200 shares, others 100 shares)."""
    if budget <= 0 or exec_price <= 0 or not math.isfinite(budget) or not math.isfinite(exec_price):
        return 0
    raw_shares = int(math.floor(budget / exec_price / 100.0)) * 100
    if _board_of(symbol) == "star" and raw_shares < 200:
        return 0
    return max(0, raw_shares)


def build_index_regime_map(
    get_candles: Callable[[str], Sequence[Any]],
    start_date: str,
    end_date: str,
) -> dict[str, str]:
    """Build causal 4-stage market regime map (advance/trial/chop/decline) from local TDX index bars."""
    bars: list[Any] = []
    for idx_sym in ("sh000852", "sh000001", "sz399001"):
        try:
            loaded = list(get_candles(idx_sym) or [])
            if len(loaded) >= 25:
                bars = loaded
                break
        except Exception:
            continue
    if not bars:
        return {}

    closes = [max(0.0, float(getattr(c, "close", 0.0) or 0.0)) for c in bars]
    times = [str(getattr(c, "time", "") or "") for c in bars]
    regime_by_date: dict[str, str] = {}
    for i, day in enumerate(times):
        if not day or day > end_date:
            continue
        c_now = closes[i]
        if c_now <= 0:
            continue
        w_start = max(0, i - 19)
        ma20 = sum(closes[w_start : i + 1]) / max(1, i - w_start + 1)
        above_ma20 = c_now >= ma20
        ret20 = ((c_now / closes[i - 20] - 1.0) * 100.0) if i >= 20 and closes[i - 20] > 0 else 0.0
        ret5 = ((c_now / closes[i - 5] - 1.0) * 100.0) if i >= 5 and closes[i - 5] > 0 else 0.0
        if above_ma20 and ret20 >= 3.0:
            stage = "advance"
        elif (not above_ma20) and ret20 <= -3.0:
            stage = "decline"
        elif above_ma20 and ret5 >= 1.5:
            stage = "trial"
        else:
            stage = "chop"
        if day >= start_date:
            regime_by_date[day] = stage
    return regime_by_date


def resolve_master_regime_slot_and_scale(
    strategy_id: str,
    stage: str | None,
    base_max_positions: int,
    base_position_pct: float,
    strategy_params: dict[str, Any] | None = None,
) -> tuple[int, float, float]:
    """Return (effective_max_slots, effective_position_pct, total_exposure_cap_ratio) for a date."""
    sid = str(strategy_id or "").strip()
    style_key = MASTER_TRADER_STYLE_MAP.get(sid)
    if not style_key:
        return max(1, int(base_max_positions)), float(base_position_pct), 1.0
    p = strategy_params if isinstance(strategy_params, dict) else {}
    use_regime = _safe_bool(p.get("use_regime_switch"), True)
    if not use_regime or not stage:
        return max(1, int(base_max_positions)), float(base_position_pct), 0.95

    cfg = MASTER_STYLE_EXECUTION_DEFAULTS[style_key]
    stg = stage if stage in ("advance", "trial", "chop", "decline") else "chop"
    slot_ratio = float(cfg["stage_slot_ratio"].get(stg, 1.0))
    pos_scale = float(cfg["stage_pos_scale"].get(stg, 1.0))
    total_cap = float(cfg["stage_total_cap"].get(stg, 0.85))
    if slot_ratio <= 0.0 or total_cap <= 0.0:
        return 0, 0.0, 0.0
    eff_slots = max(1, int(round(max(1, int(base_max_positions)) * slot_ratio)))
    eff_pos_pct = max(0.03, min(float(cfg["single_cap"]), float(base_position_pct) * pos_scale))
    return eff_slots, eff_pos_pct, total_cap


def calculate_master_alpha_signals_from_arrays(
    times: Sequence[str],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    vols: Sequence[float],
    amts: Sequence[float],
    end_idx: int,
    *,
    symbol: str = "",
    board: str | None = None,
    lim: float | None = None,
    sector_data: dict[str, Any] | None = None,
    need_vcp: bool = True,
) -> dict[str, Any]:
    """Zero-allocation array-indexed causal feature calculation (exact same formula)."""
    n = end_idx + 1
    trigger_date = times[end_idx] if 0 <= end_idx < len(times) else ""
    resolved_board = board if board is not None else _board_of(symbol)
    resolved_lim = lim if lim is not None else _limit_ratio(symbol)
    sym_lower = symbol.strip().lower() if symbol else ""
    if n < 30 or end_idx >= len(closes):
        return {
            "has_data": False,
            "trigger_date": trigger_date,
            "symbol": sym_lower,
            "board": resolved_board,
            "limit_ratio": resolved_lim,
        }

    i = end_idx
    c_now = closes[i]
    c_prev = closes[i - 1]
    v_now = vols[i]
    if c_now <= 0 or c_prev <= 0 or v_now <= 0:
        return {
            "has_data": False,
            "trigger_date": trigger_date,
            "symbol": sym_lower,
            "board": resolved_board,
            "limit_ratio": resolved_lim,
        }

    day_gain = (c_now / c_prev - 1.0) * 100.0
    high_gain = (highs[i] / c_prev - 1.0) * 100.0
    prev_gain = ((c_prev / closes[i - 2] - 1.0) * 100.0) if i >= 2 and closes[i - 2] > 0 else 0.0
    ret5 = ((c_now / closes[i - 5] - 1.0) * 100.0) if i >= 5 and closes[i - 5] > 0 else 0.0
    ret10 = ((c_now / closes[i - 10] - 1.0) * 100.0) if i >= 10 and closes[i - 10] > 0 else 0.0
    ret20 = ((c_now / closes[i - 20] - 1.0) * 100.0) if i >= 20 and closes[i - 20] > 0 else 0.0
    ret40 = ((c_now / closes[i - 40] - 1.0) * 100.0) if i >= 40 and closes[i - 40] > 0 else ret20

    ma5 = sum(closes[i - 4 : i + 1]) * 0.2
    ma10 = sum(closes[i - 9 : i + 1]) * 0.1
    ma20 = sum(closes[i - 19 : i + 1]) * 0.05
    ma20_prev = sum(closes[i - 20 : i]) * 0.05 if i >= 20 else ma20
    ma60 = sum(closes[i - 59 : i + 1]) / 60.0 if i >= 59 else None
    ma60_prev = sum(closes[i - 60 : i]) / 60.0 if i >= 60 else ma60

    vol_ma5 = sum(vols[i - 5 : i]) * 0.2 if i >= 5 else v_now
    vol_ma20 = sum(vols[i - 20 : i]) * 0.05 if i >= 20 else v_now
    vol_ratio_5 = v_now / vol_ma5 if vol_ma5 > 0 else 1.0
    vol_ratio_20 = v_now / vol_ma20 if vol_ma20 > 0 else 1.0
    vol_ratio_prev = v_now / vols[i - 1] if vols[i - 1] > 0 else 1.0

    ud_start = max(1, i - 19)
    up_v = 0.0
    dn_v = 0.0
    for j in range(ud_start, i + 1):
        cj = closes[j]
        cj_prev = closes[j - 1]
        if cj > cj_prev:
            up_v += vols[j]
        elif cj < cj_prev:
            dn_v += vols[j]
    up_down_vol_ratio = up_v / (dn_v if dn_v > 1.0 else 1.0)

    zt_thresh = 1.0 + resolved_lim - 0.005
    consec_zt = 0
    k = i
    while k >= 1 and closes[k - 1] > 0 and (closes[k] / closes[k - 1]) >= zt_thresh:
        consec_zt += 1
        k -= 1
    is_zt = consec_zt >= 1
    bombed_zt = (not is_zt) and (high_gain >= (resolved_lim * 100.0 - 0.5))
    prev_is_zt = i >= 2 and closes[i - 2] > 0 and (c_prev / closes[i - 2]) >= zt_thresh
    prev2_is_zt = i >= 3 and closes[i - 3] > 0 and (closes[i - 2] / closes[i - 3]) >= zt_thresh

    zt_in_5d = 0
    zt_in_10d = 0
    s10 = max(1, i - 9)
    s5 = max(1, i - 4)
    for j in range(s10, i + 1):
        if closes[j - 1] > 0 and (closes[j] / closes[j - 1]) >= zt_thresh:
            zt_in_10d += 1
            if j >= s5:
                zt_in_5d += 1

    h_now = highs[i]
    l_now = lows[i]
    is_one_word = abs(h_now - l_now) <= max(c_now * 0.002, 0.01)

    span = h_now - l_now
    if span < 1e-6:
        span = 1e-6
    close_strength = (c_now - l_now) / span
    body_top = opens[i] if opens[i] > c_now else c_now
    upper_wick_ratio = (h_now - body_top) / span
    prior20_high = max(highs[max(0, i - 20) : i]) if i >= 1 else h_now
    prior40_high = max(highs[max(0, i - 40) : i]) if i >= 1 else h_now
    peak20 = max(closes[max(0, i - 19) : i + 1])
    drawdown20 = (1.0 - c_now / peak20) if peak20 > 0 else 0.0

    vcp_ok = False
    vcp_last_range = 1.0
    vcp_vol_shrink = 1.0
    if need_vcp and i >= 32:
        lo1 = min(lows[i - 30 : i - 20])
        r1 = (max(highs[i - 30 : i - 20]) - lo1) / max(lo1, 0.01)
        v1 = sum(vols[i - 30 : i - 20]) * 0.1
        lo2 = min(lows[i - 20 : i - 10])
        r2 = (max(highs[i - 20 : i - 10]) - lo2) / max(lo2, 0.01)
        lo3 = min(lows[i - 10 : i])
        r3 = (max(highs[i - 10 : i]) - lo3) / max(lo3, 0.01)
        v3 = sum(vols[i - 10 : i]) * 0.1
        vcp_last_range = r3
        vcp_vol_shrink = v3 / max(v1, 1e-6)
        vcp_ok = (r3 <= r1 * 0.85 and r3 <= max(r2 * 1.05, 0.10)) or (r3 <= 0.11 and r2 <= 0.18)

    amt_yi = amts[i] * 1e-8 if amts[i] > 0 else (c_now * v_now * 1e-8)
    amt_ma20_yi = (sum(amts[max(0, i - 19) : i + 1]) / min(20, i + 1)) * 1e-8

    sector_rank = 99
    if isinstance(sector_data, dict):
        sector_rank = _safe_int(sector_data.get("sector_rank"), 99)

    return {
        "has_data": True,
        "trigger_date": trigger_date,
        "symbol": sym_lower,
        "board": resolved_board,
        "limit_ratio": resolved_lim,
        "close": round(c_now, 4),
        "prev_close": round(c_prev, 4),
        "day_gain": round(day_gain, 3),
        "prev_gain": round(prev_gain, 3),
        "ret5": round(ret5, 3),
        "ret10": round(ret10, 3),
        "ret20": round(ret20, 3),
        "ret40": round(ret40, 3),
        "ma5": round(ma5, 4),
        "ma10": round(ma10, 4),
        "ma20": round(ma20, 4),
        "ma20_rising": bool(ma20_prev is not None and ma20 > ma20_prev),
        "ma60": round(ma60, 4) if ma60 is not None else None,
        "ma60_rising": bool(ma60 is not None and ma60_prev is not None and ma60 > ma60_prev),
        "above_ma5": c_now >= ma5,
        "above_ma10": c_now >= ma10,
        "above_ma20": c_now > ma20,
        "above_ma60": bool(ma60 is not None and c_now > ma60),
        "ma_bullish": bool(ma5 > ma10 > ma20 and c_now > ma20),
        "vol_ratio_5": round(vol_ratio_5, 3),
        "vol_ratio_20": round(vol_ratio_20, 3),
        "vol_ratio_prev": round(vol_ratio_prev, 3),
        "up_down_vol_ratio": round(up_down_vol_ratio, 3),
        "consec_zt": consec_zt,
        "is_zt": is_zt,
        "bombed_zt": bombed_zt,
        "prev_is_zt": prev_is_zt,
        "prev2_is_zt": prev2_is_zt,
        "zt_in_5d": zt_in_5d,
        "zt_in_10d": zt_in_10d,
        "is_one_word": is_one_word,
        "close_strength": round(close_strength, 4),
        "upper_wick_ratio": round(upper_wick_ratio, 4),
        "breakout_20": c_now > prior20_high,
        "breakout_40": c_now > prior40_high,
        "prior20_high": round(prior20_high, 4),
        "prior40_high": round(prior40_high, 4),
        "drawdown20": round(drawdown20, 4),
        "dist_ma10_pct": round((c_now / ma10 - 1.0) * 100.0, 3) if ma10 > 0 else 0.0,
        "dist_ma20_pct": round((c_now / ma20 - 1.0) * 100.0, 3) if ma20 > 0 else 0.0,
        "vcp_ok": vcp_ok,
        "vcp_last_range": round(vcp_last_range, 4),
        "vcp_vol_shrink": round(vcp_vol_shrink, 4),
        "amt_yi": round(amt_yi, 3),
        "amt_ma20_yi": round(amt_ma20_yi, 3),
        "sector_rank": sector_rank,
        "bars_count": n,
    }


def calculate_master_alpha_signals(
    candles: Sequence[Any],
    *,
    symbol: str = "",
    sector_data: dict[str, Any] | None = None,
    need_vcp: bool = True,
) -> dict[str, Any]:
    """Compute all causal features required by the 6 master-trader strategies + VCP."""
    ordered = list(candles or [])
    if len(ordered) < 30:
        trigger_date = str(getattr(ordered[-1], "time", "") or "") if ordered else ""
        return {
            "has_data": False,
            "trigger_date": trigger_date,
            "symbol": str(symbol or "").strip().lower(),
            "board": _board_of(symbol),
            "limit_ratio": _limit_ratio(symbol),
        }
    times = [str(getattr(c, "time", "") or "") for c in ordered]
    opens = [max(0.0, float(getattr(c, "open", 0.0) or 0.0)) for c in ordered]
    highs = [max(0.0, float(getattr(c, "high", 0.0) or 0.0)) for c in ordered]
    lows = [max(0.0, float(getattr(c, "low", 0.0) or 0.0)) for c in ordered]
    closes = [max(0.0, float(getattr(c, "close", 0.0) or 0.0)) for c in ordered]
    vols = [max(0.0, float(getattr(c, "volume", 0.0) or 0.0)) for c in ordered]
    amts = [max(0.0, float(getattr(c, "amount", 0.0) or 0.0)) for c in ordered]
    return calculate_master_alpha_signals_from_arrays(
        times,
        opens,
        highs,
        lows,
        closes,
        vols,
        amts,
        len(ordered) - 1,
        symbol=symbol,
        sector_data=sector_data,
        need_vcp=need_vcp,
    )


def evaluate_master_alpha_signal(
    strategy_id: str,
    indicator: dict[str, Any] | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one of the 6 master-trader strategies + VCP with all user params respected."""
    sid = str(strategy_id or "").strip()
    ind = indicator if isinstance(indicator, dict) else {}
    p = params if isinstance(params, dict) else {}

    base = {
        "signal": False,
        "signal_score": 0.0,
        "event_grade": "C",
        "label": "",
        "phase": "观察",
        "reason": "",
        "trigger_date": str(ind.get("trigger_date") or ""),
    }
    if not ind.get("has_data"):
        return base

    board = str(ind.get("board") or "main")
    day_gain = _safe_float(ind.get("day_gain"), 0.0)
    prev_gain = _safe_float(ind.get("prev_gain"), 0.0)
    ret5 = _safe_float(ind.get("ret5"), 0.0)
    ret20 = _safe_float(ind.get("ret20"), 0.0)
    vol_r20 = _safe_float(ind.get("vol_ratio_20"), 0.0)
    vol_r5 = _safe_float(ind.get("vol_ratio_5"), 0.0)
    vol_rprev = _safe_float(ind.get("vol_ratio_prev"), 0.0)
    ud_ratio = _safe_float(ind.get("up_down_vol_ratio"), 0.0)
    close_str = _safe_float(ind.get("close_strength"), 0.0)
    wick = _safe_float(ind.get("upper_wick_ratio"), 0.0)
    amt_yi = _safe_float(ind.get("amt_yi"), 0.0)
    dd20 = _safe_float(ind.get("drawdown20"), 1.0)
    dist_ma20 = _safe_float(ind.get("dist_ma20_pct"), 0.0)
    consec_zt = _safe_int(ind.get("consec_zt"), 0)
    is_zt = bool(ind.get("is_zt"))
    bombed_zt = bool(ind.get("bombed_zt"))
    prev_is_zt = bool(ind.get("prev_is_zt"))
    is_one_word = bool(ind.get("is_one_word"))
    zt_in_5d = _safe_int(ind.get("zt_in_5d"), 0)
    above_ma5 = bool(ind.get("above_ma5", True))
    above_ma20 = bool(ind.get("above_ma20"))
    ma20_rising = bool(ind.get("ma20_rising"))
    above_ma60 = bool(ind.get("above_ma60"))
    ma_bullish = bool(ind.get("ma_bullish"))
    breakout_20 = bool(ind.get("breakout_20"))
    breakout_40 = bool(ind.get("breakout_40"))
    sector_rank = _safe_int(ind.get("sector_rank"), 99)

    sector_bonus = 8.0 if sector_rank <= 5 else (4.0 if sector_rank <= 15 else 0.0)

    # 1. 乌江望月 · 双轨主线核心 (wujiang_mainline_v1)
    if sid == "wujiang_mainline_v1":
        track_mode = str(p.get("track_mode", "dual")).strip().lower()
        min_amt_yi = _safe_float(p.get("min_amount_yi"), 2.5)
        min_vol_r20 = _safe_float(p.get("min_vol_ratio_20"), 1.35)
        max_dist_ma20 = _safe_float(p.get("max_dist_ma20_pct"), 16.0)
        min_close_str = _safe_float(p.get("min_close_strength"), 0.80)
        min_score = _safe_float(p.get("min_score"), 62.0)

        if not (above_ma20 and amt_yi >= min_amt_yi and close_str >= min_close_str and wick <= 0.20 and not bombed_zt):
            return base

        track_20cm = (
            track_mode in ("dual", "growth_20cm")
            and board in ("star", "chinext")
            and amt_yi >= max(min_amt_yi, 5.0)
            and ma20_rising
            and (above_ma60 or ind.get("ma60") is None)
            and breakout_20
            and 4.0 <= day_gain <= 14.0
            and dist_ma20 <= max_dist_ma20
            and vol_r20 >= min_vol_r20
            and ud_ratio >= 1.25
        )
        repack = (not prev_is_zt) and prev_gain <= -1.0 and zt_in_5d >= 1 and is_zt and vol_r20 >= 1.15
        strong_zt = (
            is_zt
            and consec_zt >= 2
            and not is_one_word
            and 0.85 <= vol_rprev <= 2.6
            and (above_ma60 or ind.get("ma60") is None)
        )
        first_zt_breakout = (
            is_zt
            and consec_zt == 1
            and not is_one_word
            and breakout_40
            and ma20_rising
            and dist_ma20 <= max_dist_ma20
            and 1.2 <= vol_r20 <= 3.0
            and ud_ratio >= 1.2
        )
        track_10cm = track_mode in ("dual", "main_10cm") and board == "main" and (repack or strong_zt or first_zt_breakout)

        if not (track_20cm or track_10cm):
            return base

        score = 56.0
        score += 12.0 if repack else (10.0 if strong_zt else 8.0)
        score += min(12.0, max(0.0, ud_ratio - 1.0) * 14.0)
        score += min(10.0, max(0.0, vol_r20 - 1.0) * 6.0)
        score += min(6.0, amt_yi * 0.5) + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        label = (
            "乌江20cm容量起爆" if track_20cm
            else ("乌江主板首阴反包" if repack else (f"乌江主板{consec_zt}板核心" if strong_zt else "乌江主板首板突破"))
        )
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": label,
            "phase": "20cm科技起爆" if track_20cm else "主板题材核心",
            "reason": f"{label}：涨幅{day_gain:+.1f}%，量比{vol_r20:.2f}x，涨跌量比{ud_ratio:.2f}，成交{amt_yi:.1f}亿，距MA20 {dist_ma20:+.1f}%",
            "trigger_date": base["trigger_date"],
        }

    # 2. 灰常懒 · 绝对主线半路秒砍 (mainline_halfway_v1)
    if sid == "mainline_halfway_v1":
        min_gain = _safe_float(p.get("min_day_gain_pct"), 2.5)
        max_gain = _safe_float(p.get("max_day_gain_pct"), 6.5)
        min_vol_r20 = _safe_float(p.get("min_vol_ratio_20"), 1.35)
        min_ud = _safe_float(p.get("min_up_down_vol_ratio"), 1.25)
        max_dist_ma20 = _safe_float(p.get("max_dist_ma20_pct"), 9.5)
        min_ret20 = _safe_float(p.get("min_ret20_pct"), 2.0)
        min_score = _safe_float(p.get("min_score"), 60.0)

        triggered = (
            board in ("main", "chinext", "star")
            and above_ma20
            and ma20_rising
            and (above_ma60 or ind.get("ma60") is None)
            and ma_bullish
            and breakout_20
            and min_gain <= day_gain <= max_gain
            and 1.0 <= dist_ma20 <= max_dist_ma20
            and vol_r20 >= min_vol_r20
            and ud_ratio >= min_ud
            and ret20 >= min_ret20
            and ret20 <= 28.0
            and close_str >= 0.72
            and wick <= 0.24
            and not bombed_zt
            and amt_yi >= 2.0
        )
        if not triggered:
            return base
        score = 54.0
        score += min(14.0, max(0.0, (max_dist_ma20 - dist_ma20) / max(max_dist_ma20, 1.0) * 14.0))
        score += min(14.0, max(0.0, ud_ratio - 1.0) * 16.0)
        score += min(10.0, max(0.0, vol_r20 - 1.0) * 7.0)
        score += min(8.0, close_str * 8.0) + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": "灰常懒主线半路",
            "phase": "低位均线突破启动",
            "reason": f"灰常懒主线半路+{day_gain:.1f}%：突破20日高点，距MA20仅{dist_ma20:+.1f}%，量比{vol_r20:.2f}x，涨跌量比{ud_ratio:.2f}",
            "trigger_date": base["trigger_date"],
        }

    # 3. 希望姐姐 · 纯主板一进二接力 (first_to_second_v1)
    if sid == "first_to_second_v1":
        min_amt_yi = _safe_float(p.get("min_amount_yi"), 1.5)
        min_vr_prev = _safe_float(p.get("min_vol_ratio_prev"), 0.8)
        max_vr_prev = _safe_float(p.get("max_vol_ratio_prev"), 2.8)
        require_above_ma20 = _safe_bool(p.get("require_above_ma20"), True)
        exclude_one_word = _safe_bool(p.get("exclude_one_word"), True)
        min_score = _safe_float(p.get("min_score"), 58.0)

        # Match 1->2 candidate (1-board finished today for tomorrow 1->2, OR confirmed 2-board turnover relay)
        target_boards = _safe_int(p.get("signal_board_stage"), 1)
        board_match = (consec_zt in (1, 2)) if target_boards <= 0 else (consec_zt == target_boards or consec_zt == 2)
        triggered = (
            board == "main"
            and board_match
            and amt_yi >= min_amt_yi
            and min_vr_prev <= vol_rprev <= max_vr_prev
            and (not require_above_ma20 or above_ma20)
            and (not exclude_one_word or not is_one_word)
            and not bombed_zt
        )
        if not triggered:
            return base
        vol_sweet = 16.0 if 1.0 <= vol_rprev <= 2.0 else 8.0
        score = 56.0 + vol_sweet + (8.0 if above_ma60 else 0.0) + min(10.0, amt_yi * 1.2) + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        lbl = "纯主板一进二备选" if consec_zt == 1 else "纯主板一进二晋级"
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": lbl,
            "phase": "一进二换手接力",
            "reason": f"{lbl}：纯主板{consec_zt}连板，较昨量比{vol_rprev:.2f}x，成交{amt_yi:.1f}亿，MA20上方",
            "trigger_date": base["trigger_date"],
        }

    # 4. 暴涨基金董事长 · 主板高标狙击 (highboard_sniper_v1)
    if sid == "highboard_sniper_v1":
        min_boards = max(2, _safe_int(p.get("min_board_height"), 2))
        min_amt_yi = _safe_float(p.get("min_amount_yi"), 1.8)
        max_vr_prev = _safe_float(p.get("max_vol_ratio_prev"), 3.2)
        require_above_ma20 = _safe_bool(p.get("require_above_ma20"), True)
        exclude_one_word = _safe_bool(p.get("exclude_one_word"), False)
        min_score = _safe_float(p.get("min_score"), 65.0)

        triggered = (
            board == "main"
            and is_zt
            and consec_zt >= min_boards
            and amt_yi >= min_amt_yi
            and vol_rprev <= max_vr_prev
            and (not require_above_ma20 or above_ma20)
            and (not exclude_one_word or not is_one_word)
        )
        if not triggered:
            return base
        score = 62.0 + min(22.0, consec_zt * 6.5) + min(10.0, amt_yi * 0.9) + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": f"董事长主板{consec_zt}连板高标",
            "phase": "高标情绪抱团龙头",
            "reason": f"纯主板{consec_zt}连板最高标梯队，成交{amt_yi:.1f}亿，较昨量比{vol_rprev:.2f}x，5日+{ret5:.1f}%",
            "trigger_date": base["trigger_date"],
        }

    # 5. 如晗 · 分仓半路推土机 (ruhan_halfway_v1)
    if sid == "ruhan_halfway_v1":
        min_gain = _safe_float(p.get("min_day_gain_pct"), 3.0)
        max_gain = _safe_float(p.get("max_day_gain_pct"), 9.6)
        min_amt_yi = _safe_float(p.get("min_amount_yi"), 2.5)
        min_vol_r5 = _safe_float(p.get("min_vol_ratio_5"), 1.20)
        min_ret5 = _safe_float(p.get("min_ret5_pct"), 2.0)
        min_score = _safe_float(p.get("min_score"), 58.0)

        triggered = (
            board in ("main", "chinext", "star")
            and above_ma20
            and above_ma5
            and consec_zt < 2
            and (min_gain <= day_gain <= max_gain or consec_zt == 1)
            and vol_r5 >= min_vol_r5
            and amt_yi >= min_amt_yi
            and ret5 >= min_ret5
            and close_str >= 0.68
            and not bombed_zt
        )
        if not triggered:
            return base
        score = 54.0 + min(18.0, max(0.0, vol_r5 - 1.0) * 9.0) + min(14.0, max(0.0, ret5) * 0.8) + (8.0 if is_zt else 3.0) + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": "如晗分仓半路启动",
            "phase": "0板/首板推土机启动",
            "reason": f"MA20上方0板/首板放量启动({day_gain:+.1f}%)，5日量比{vol_r5:.2f}x，成交{amt_yi:.1f}亿，5日+{ret5:.1f}%",
            "trigger_date": base["trigger_date"],
        }

    # 6. 松雅湖的松松 · 强趋势缩量低吸防守 (strong_trend_dip_v1)
    if sid == "strong_trend_dip_v1":
        min_ret20 = _safe_float(p.get("min_ret20_pct"), 8.0)
        min_dd20 = _safe_float(p.get("min_drawdown20"), 0.04)
        max_dd20 = _safe_float(p.get("max_drawdown20"), 0.14)
        max_vol_r20 = _safe_float(p.get("max_vol_ratio_20"), 0.85)
        max_dist_ma20 = _safe_float(p.get("max_dist_ma20_pct"), 6.0)
        min_ud = _safe_float(p.get("min_up_down_vol_ratio"), 1.25)
        min_score = _safe_float(p.get("min_score"), 58.0)

        triggered = (
            board != "bse"
            and above_ma20
            and ma20_rising
            and (above_ma60 or ind.get("ma60") is None)
            and ret20 >= min_ret20
            and ret20 <= 45.0
            and min_dd20 <= dd20 <= max_dd20
            and -2.0 <= day_gain <= 3.5
            and prev_gain <= 0.8
            and vol_r20 <= max_vol_r20
            and 0.0 <= dist_ma20 <= max_dist_ma20
            and ud_ratio >= min_ud
            and close_str >= 0.55
            and wick <= 0.35
            and amt_yi >= 1.5
        )
        if not triggered:
            return base
        shrink_bonus = min(16.0, max(0.0, (1.0 - vol_r20) * 30.0))
        support_bonus = min(14.0, max(0.0, (max_dist_ma20 - dist_ma20) / max(max_dist_ma20, 1.0) * 14.0))
        score = 52.0 + shrink_bonus + support_bonus + min(12.0, max(0.0, ud_ratio - 1.0) * 14.0) + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": "松松强趋势低吸",
            "phase": "回踩MA20缩量承接",
            "reason": f"强趋势(20日+{ret20:.1f}%)回撤{dd20*100:.1f}%后缩量企稳({day_gain:+.1f}%, 量比{vol_r20:.2f}x)，贴近MA20({dist_ma20:+.1f}%)",
            "trigger_date": base["trigger_date"],
        }

    # 7. VCP三段收缩突破 (vcp_contraction_v1)
    if sid == "vcp_contraction_v1":
        max_base_range = _safe_float(p.get("max_base_range"), 0.12)
        max_vol_shrink = _safe_float(p.get("max_vol_shrink"), 0.85)
        min_breakout_vr = _safe_float(p.get("min_breakout_vol_ratio"), 1.30)
        min_close_str = _safe_float(p.get("min_close_strength"), 0.68)
        min_score = _safe_float(p.get("min_score"), 58.0)

        vcp_last_range = _safe_float(ind.get("vcp_last_range"), 1.0)
        vcp_vol_shrink = _safe_float(ind.get("vcp_vol_shrink"), 1.0)
        triggered = (
            bool(ind.get("vcp_ok"))
            and vcp_last_range <= max_base_range
            and vcp_vol_shrink <= max_vol_shrink
            and above_ma20
            and ma20_rising
            and (above_ma60 or ind.get("ma60") is None)
            and (breakout_20 or breakout_40)
            and vol_r20 >= min_breakout_vr
            and close_str >= min_close_str
            and day_gain >= 2.0
            and dist_ma20 <= 12.0
            and ud_ratio >= 1.15
            and amt_yi >= 1.2
        )
        if not triggered:
            return base
        tight_bonus = min(20.0, max(0.0, (max_base_range - vcp_last_range) / max(max_base_range, 0.01) * 20.0))
        dry_bonus = min(14.0, max(0.0, (1.0 - vcp_vol_shrink) * 22.0))
        brk_bonus = min(14.0, max(0.0, vol_r20 - 1.0) * 10.0) + (6.0 if breakout_40 else 2.0)
        score = 50.0 + tight_bonus + dry_bonus + brk_bonus + sector_bonus
        score = _clamp(score)
        if score < min_score:
            return base
        return {
            "signal": True,
            "signal_score": round(score, 2),
            "event_grade": _grade_from_score(score),
            "label": "VCP收缩突破",
            "phase": "紧致平台放量突破",
            "reason": f"末段波幅收敛至{vcp_last_range*100:.1f}%、蓄势缩量比{vcp_vol_shrink:.2f}，当日{vol_r20:.2f}x放量突破{'40日' if breakout_40 else '20日'}高点",
            "trigger_date": base["trigger_date"],
        }

    return base


def evaluate_master_alpha_exit(
    strategy_id: str,
    indicator: dict[str, Any] | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Independent structural exit signal checked even after leaving the buy pool."""
    sid = str(strategy_id or "").strip()
    ind = indicator if isinstance(indicator, dict) else {}
    p = params if isinstance(params, dict) else {}
    if not ind.get("has_data"):
        return {"exit_signal": False, "exit_reason": ""}

    use_fast_cut = _safe_bool(p.get("use_fast_weak_cut"), True)
    day_gain = _safe_float(ind.get("day_gain"), 0.0)
    above_ma5 = bool(ind.get("above_ma5", True))
    above_ma20 = bool(ind.get("above_ma20", True))
    is_zt = bool(ind.get("is_zt", False))
    bombed_zt = bool(ind.get("bombed_zt", False))
    prev_is_zt = bool(ind.get("prev_is_zt", False))
    vol_r20 = _safe_float(ind.get("vol_ratio_20"), 1.0)
    wick = _safe_float(ind.get("upper_wick_ratio"), 0.0)

    if sid in ("first_to_second_v1", "highboard_sniper_v1"):
        if use_fast_cut and bombed_zt:
            return {"exit_signal": True, "exit_reason": "炸板次日早盘核出"}
        if prev_is_zt and not is_zt and day_gain <= -1.5:
            return {"exit_signal": True, "exit_reason": "断板转弱退出"}
        if use_fast_cut and not above_ma5 and day_gain <= -3.0:
            return {"exit_signal": True, "exit_reason": "破MA5快砍退出"}
    elif sid == "wujiang_mainline_v1":
        if not above_ma20 and day_gain <= -2.5:
            return {"exit_signal": True, "exit_reason": "破MA20趋势失效"}
        if use_fast_cut and bombed_zt and day_gain <= 1.0:
            return {"exit_signal": True, "exit_reason": "炸板回落次日快砍"}
        if use_fast_cut and not above_ma5 and day_gain <= -5.0:
            return {"exit_signal": True, "exit_reason": "大阴破MA5极速纠错"}
        if wick >= 0.60 and vol_r20 >= 2.5 and day_gain <= -1.0:
            return {"exit_signal": True, "exit_reason": "高位巨量长上影派发"}
    elif sid in ("mainline_halfway_v1", "ruhan_halfway_v1"):
        if not above_ma20 and day_gain <= -1.5:
            return {"exit_signal": True, "exit_reason": "跌破MA20退出"}
        if use_fast_cut and bombed_zt and day_gain <= 0.5:
            return {"exit_signal": True, "exit_reason": "炸板转弱早盘快砍"}
        if use_fast_cut and not above_ma5 and day_gain <= -4.0:
            return {"exit_signal": True, "exit_reason": "破MA5早盘15分秒砍"}
    elif sid == "strong_trend_dip_v1":
        if not above_ma20 and day_gain <= -2.0:
            return {"exit_signal": True, "exit_reason": "支撑跌破止损"}
        if use_fast_cut and not above_ma5 and day_gain <= -3.2:
            return {"exit_signal": True, "exit_reason": "松松铁血破位止损"}
    elif sid == "vcp_contraction_v1":
        if not above_ma20 and day_gain <= -2.0:
            return {"exit_signal": True, "exit_reason": "突破失效跌破MA20"}

    return {"exit_signal": False, "exit_reason": ""}
