"""Master-Style Causal Historical Backtest & Ablation Verification Engine (A-trade T11).

Formalizes each of the 6 顽主杯 masters' quantitative playbooks into an auditable,
zero-lookahead, A-share realistic daily portfolio simulator:
  - Strict T+1 settlement (cannot sell on buy date)
  - 100-share integer lots (200 for STAR)
  - Explicit commission (0.03%, min ¥5), sell stamp duty (0.05%), and slippage (0.15%)
  - Limit-up unbuyable & limit-down unsellable execution checks (open >= limit-up blocks buy;
    open <= limit-down defers sell to next available day)
  - Automatic 3-Way Proof / Ablation Comparison:
      1. Full Master Mode (with regime switch + fast stop-loss + position sizing)
      2. No-Discipline Ablation (same stock signals, NO regime switch & loose -15% stop-loss)
      3. Benchmark Buy-and-Hold (中证1000 sh000852)
    Proves quantitatively whether & WHY the master's risk discipline and regime switch work!
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import datetime as dt
import math
import sqlite3
import struct
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from service import tdx
from service.master_advisor import STYLE_PROFILES, detect_style_key
from service.master_rules import (
    ACTION_LABELS,
    board_label,
    calc_buy_shares,
    calc_trim_shares,
    describe_active_rules,
    plan_next_day_orders,
    resolve_position_limits,
    strategy_params,
)
from service.master_fallback_market import (
    build_fallback_market_env,
    get_fallback_all_stock_symbols,
    get_fallback_index_bars,
    get_fallback_names,
    get_fallback_stock_bars,
)

COMMISSION_RATE = 0.0003
MIN_COMMISSION = 5.0
STAMP_DUTY_SELL = 0.0005
SLIPPAGE_RATE = 0.0015
MAX_TDX_UNIVERSE = 600


class BacktestCancelled(Exception):
    """Raised cooperatively when a background backtest task is paused or stopped."""


def _date_to_int(iso_date: str) -> int:
    try:
        return int(iso_date.replace("-", ""))
    except Exception:
        return 0


def _read_tdx_daily_window(
    path: Path,
    start_date: str,
    end_date: str,
    warmup_bars: int = 35,
) -> list[dict[str, Any]]:
    """Binary-search a TDX .day file and decode only [start_date - warmup .. end_date]."""
    data = tdx.read_stable(path)
    tdx.validate_size(len(data))
    rec_size = tdx.DAY.size
    total = len(data) // rec_size
    if total == 0:
        return []
    start_int = _date_to_int(start_date)
    end_int = _date_to_int(end_date)

    # Lower bound for start_int
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi) // 2
        d_val = struct.unpack_from("<I", data, mid * rec_size)[0]
        if d_val < start_int:
            lo = mid + 1
        else:
            hi = mid
    start_idx = max(0, lo - warmup_bars)

    # Upper bound for end_int
    lo, hi = start_idx, total
    while lo < hi:
        mid = (lo + hi) // 2
        d_val = struct.unpack_from("<I", data, mid * rec_size)[0]
        if d_val <= end_int:
            lo = mid + 1
        else:
            hi = mid
    end_idx = lo

    out: list[dict[str, Any]] = []
    prev_date = ""
    for i in range(start_idx, end_idx):
        row = tdx.decode_record(data[i * rec_size:(i + 1) * rec_size])
        if row["date"] <= prev_date:
            continue
        prev_date = row["date"]
        out.append(row)
    return out


def _slice_bars_window(
    bars: list[dict[str, Any]],
    start_date: str | None = None,
    end_date: str | None = None,
    warmup_bars: int = 35,
) -> list[dict[str, Any]]:
    if not bars or (not start_date and not end_date):
        return bars
    dates = [b["date"] for b in bars]
    lo = max(0, bisect_left(dates, start_date) - warmup_bars) if start_date else 0
    hi = bisect_right(dates, end_date) if end_date else len(bars)
    return bars[lo:hi]


def _build_tdx_market_env(
    root: Path,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build causal daily market regime table and benchmark bars directly from local TDX indices."""
    def load_idx(sym: str) -> list[dict[str, Any]]:
        try:
            p = tdx.daily_path(root, sym)
            if not p.is_file():
                return []
            return _read_tdx_daily_window(p, start_date, end_date, warmup_bars=30)
        except Exception:
            return []

    sh = load_idx("sh000001")
    sz = load_idx("sz399001")
    cyb = load_idx("sz399006")
    zz1k = load_idx("sh000852")
    kc50 = load_idx("sh000688")
    bench = zz1k or sh
    if not sh and not bench:
        return {}, []
    anchor = sh or bench

    def idx_map(bars: list[dict[str, Any]]) -> dict[str, tuple[int, dict[str, Any]]]:
        return {b["date"]: (i, b) for i, b in enumerate(bars)}

    m_sh, m_sz, m_cyb, m_zz, m_kc = (
        idx_map(sh), idx_map(sz), idx_map(cyb), idx_map(zz1k), idx_map(kc50)
    )

    def idx_stats(bars_list: list[dict[str, Any]], m: dict[str, tuple[int, dict[str, Any]]], d: str):
        if d not in m:
            return None, None, None, None
        pos, bar = m[d]
        prev_c = bars_list[pos - 1]["close"] if pos > 0 else bar["open"]
        pct = ((bar["close"] / prev_c - 1.0) * 100.0) if prev_c > 0 else 0.0
        w = bars_list[max(0, pos - 19):pos + 1]
        ma20 = sum(x["close"] for x in w) / len(w)
        ret5 = ((bar["close"] / bars_list[pos - 5]["close"]) - 1.0) * 100.0 if pos >= 5 and bars_list[pos - 5]["close"] > 0 else 0.0
        ret20 = ((bar["close"] / bars_list[pos - 20]["close"]) - 1.0) * 100.0 if pos >= 20 and bars_list[pos - 20]["close"] > 0 else 0.0
        return round(pct, 2), bar["close"] >= ma20, round(ret5, 2), round(ret20, 2)

    env: dict[str, dict[str, Any]] = {}
    for b_anchor in anchor:
        d = b_anchor["date"]
        sh_amt = m_sh[d][1]["amount"] if d in m_sh else 0.0
        sz_amt = m_sz[d][1]["amount"] if d in m_sz else 0.0
        to_yi = round((sh_amt + sz_amt) / 1e8, 1) if (sh_amt + sz_amt) > 0 else 15000.0

        sh_pct, sh_abv, sh_r5, sh_r20 = idx_stats(sh, m_sh, d)
        cyb_pct, _, _, _ = idx_stats(cyb, m_cyb, d)
        zz_pct, zz_abv, zz_r5, zz_r20 = idx_stats(zz1k, m_zz, d)
        kc_pct, kc_abv, _, kc_r20 = idx_stats(kc50, m_kc, d)

        r20 = zz_r20 if zz_r20 is not None else (sh_r20 or 0.0)
        abv = zz_abv if zz_abv is not None else bool(sh_abv)
        r5 = zz_r5 if zz_r5 is not None else (sh_r5 or 0.0)

        if abv and r20 >= 3.0:
            stage, stage_label = "advance", "主升"
        elif (not abv) and r20 <= -3.0:
            stage, stage_label = "decline", "主跌退潮"
        elif abv and r5 >= 1.5:
            stage, stage_label = "trial", "低位试错"
        else:
            stage, stage_label = "chop", "高位震荡"

        env[d] = {
            "date": d,
            "stage": stage,
            "stageLabel": stage_label,
            "shChangePct": sh_pct or 0.0,
            "cybChangePct": cyb_pct,
            "zz1000ChangePct": zz_pct if zz_pct is not None else (sh_pct or 0.0),
            "kc50ChangePct": kc_pct,
            "zz1000Ret20": zz_r20,
            "zz1000AboveMA20": zz_abv,
            "kc50Ret20": kc_r20,
            "kc50AboveMA20": kc_abv,
            "turnoverYi": to_yi,
            "source": "tdx",
        }
    return env, bench


def _select_tdx_universe(
    rows: list[Any],
    preferred_symbols: set[str],
    limit: int = MAX_TDX_UNIVERSE,
) -> list[tuple[str, str]]:
    """Select a balanced SH/SZ A-share sample excluding BSE (which none of the 6 master styles trade)."""
    eligible: list[tuple[str, str]] = []
    for r in rows:
        sym = str(r["symbol"]).lower()
        if sym.startswith("bj"):
            continue
        code = sym[2:]
        if code.startswith(("11", "12", "51", "56", "58", "15", "16")):
            continue
        eligible.append((sym, str(r["name"] or code)))

    if len(eligible) <= limit:
        return eligible

    chosen: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Keep priority symbols (e.g., master focus stocks) up to half the budget
    pref_cap = limit // 2
    for sym, name in eligible:
        if sym in preferred_symbols and sym not in seen:
            chosen.append((sym, name))
            seen.add(sym)
            if len(chosen) >= pref_cap:
                break

    # Fill the rest evenly across boards (main SH, main SZ, ChiNext, STAR) so we never clump into bank stocks
    buckets: dict[str, list[tuple[str, str]]] = {"sh_main": [], "sz_main": [], "chinext": [], "star": []}
    for sym, name in eligible:
        if sym in seen:
            continue
        if sym.startswith("sh68"):
            buckets["star"].append((sym, name))
        elif sym.startswith("sz30"):
            buckets["chinext"].append((sym, name))
        elif sym.startswith("sh"):
            buckets["sh_main"].append((sym, name))
        else:
            buckets["sz_main"].append((sym, name))

    remaining = limit - len(chosen)
    total_rem = sum(len(b) for b in buckets.values())
    if remaining > 0 and total_rem > 0:
        for bname, items in buckets.items():
            if not items:
                continue
            quota = max(1, int(round(remaining * len(items) / total_rem)))
            step = max(1.0, len(items) / quota)
            for k in range(min(quota, len(items))):
                idx = min(len(items) - 1, int(k * step))
                sym, name = items[idx]
                if sym not in seen:
                    chosen.append((sym, name))
                    seen.add(sym)
                    if len(chosen) >= limit:
                        break
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def _board(sym: str) -> str:
    if sym.startswith("sh68"):
        return "star"
    if sym.startswith("sz30"):
        return "chinext"
    if sym.startswith("bj"):
        return "bse"
    return "main"


def _lim_pct(sym: str) -> float:
    b = _board(sym)
    return 20.0 if b in ("star", "chinext") else (30.0 if b == "bse" else 10.0)


def _prepare_stock_features(
    sym: str,
    bars: list[dict],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Precompute strictly causal daily features using O(1) rolling sums."""
    bars = _slice_bars_window(bars, start_date, end_date, warmup_bars=30)
    if not bars:
        return {}
    out: dict[str, dict[str, Any]] = {}
    lim = _lim_pct(sym)
    board = _board(sym)
    consec_zt = 0
    closes: list[float] = []
    vols: list[float] = []
    sum5 = 0.0
    sum10 = 0.0
    sum20 = 0.0
    vol_sum5 = 0.0

    for i, b in enumerate(bars):
        c = float(b["close"])
        o = float(b["open"])
        h = float(b["high"])
        lo = float(b["low"])
        v = float(b["volume"])
        amt = float(b["amount"])
        prev_c = closes[i - 1] if i > 0 else o

        pct = b.get("pct")
        if pct is None:
            pct = ((c / prev_c - 1.0) * 100.0) if prev_c > 0 else 0.0
        is_zt = pct >= lim - 0.5 and abs(c - h) / max(h, 0.01) < 0.006
        is_dt = pct <= -(lim - 0.5) and abs(c - lo) / max(lo, 0.01) < 0.006
        open_pct = ((o / prev_c - 1.0) * 100.0) if prev_c > 0 else 0.0
        high_pct = ((h / prev_c - 1.0) * 100.0) if prev_c > 0 else 0.0
        low_pct = ((lo / prev_c - 1.0) * 100.0) if prev_c > 0 else 0.0
        open_at_zt = open_pct >= lim - 0.55
        open_at_dt = open_pct <= -(lim - 0.55)
        bombed_zt = (not is_zt) and (high_pct >= lim - 0.5)
        prior_zt = consec_zt
        consec_zt = (consec_zt + 1) if is_zt else 0

        # O(1) rolling MA5 / MA10 / MA20 & prior-5d average volume
        vol5 = (vol_sum5 / min(i, 5)) if i >= 1 else v
        vol_ratio = (v / vol5) if vol5 > 0 else 1.0

        closes.append(c)
        vols.append(v)
        sum5 += c
        if i >= 5:
            sum5 -= closes[i - 5]
        sum10 += c
        if i >= 10:
            sum10 -= closes[i - 10]
        sum20 += c
        if i >= 20:
            sum20 -= closes[i - 20]
        vol_sum5 += v
        if i >= 5:
            vol_sum5 -= vols[i - 5]

        ma5 = sum5 / min(i + 1, 5)
        ma10 = sum10 / min(i + 1, 10)
        ma20 = sum20 / min(i + 1, 20)
        ret5 = ((c / closes[i - 5]) - 1.0) * 100.0 if i >= 5 and closes[i - 5] > 0 else 0.0
        ret20 = ((c / closes[i - 20]) - 1.0) * 100.0 if i >= 20 and closes[i - 20] > 0 else 0.0

        d = b["date"]
        if start_date and d < start_date:
            continue
        out[d] = {
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "prevClose": prev_c,
            "pct": round(pct, 2),
            "openPct": round(open_pct, 2),
            "highPct": round(high_pct, 2),
            "lowPct": round(low_pct, 2),
            "amountYi": amt / 1e8,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "aboveMA5": c >= ma5,
            "aboveMA20": c >= ma20,
            "ret5": ret5,
            "ret20": ret20,
            "volRatio": vol_ratio,
            "isZt": is_zt,
            "isDt": is_dt,
            "openAtZt": open_at_zt,
            "openAtDt": open_at_dt,
            "bombedZt": bombed_zt,
            "priorZt": prior_zt,
            "consecZt": consec_zt,
            "board": board,
            "lim": lim,
        }
    return out


def _strategy_params(style_key: str, overrides: dict[str, Any]) -> dict[str, Any]:
    return strategy_params(style_key, overrides)


def _score_candidate(style_key: str, f: dict[str, Any], env: dict[str, Any]) -> float | None:
    """Strictly causal end-of-day t signal score (generates next-day t+1 open order)."""
    if f["amountYi"] < 1.5 or f["isDt"]:
        return None
    board = f["board"]
    kc_abv = env.get("kc50AboveMA20")
    to_yi = env.get("turnoverYi") or 15000.0
    stage = env.get("stage", "chop")

    if style_key == "wujiang_dynamic":
        if not f["aboveMA20"]:
            return None
        # Dynamic track switch: 20cm Tech when KC50 > MA20 & turnover >= 2.0万亿, else 10cm Mainboard theme leaders
        if kc_abv is True and to_yi >= 20000:
            if board in ("star", "chinext") and f["amountYi"] >= 5.0 and f["pct"] >= 3.0 and f["ret20"] >= 10.0:
                return 60.0 + f["ret20"] * 0.4 + f["pct"] * 1.5 + (15.0 if f["isZt"] else 0.0)
            if board == "main" and f["isZt"] and f["amountYi"] >= 8.0:
                return 55.0 + f["consecZt"] * 8.0 + min(20.0, f["amountYi"])
            return None
        else:
            # Weak / shrinking liquidity -> drop dead tech, rotate to mainboard theme cores
            if board == "star":
                return None
            if board == "main" and (f["consecZt"] >= 1 or (f["pct"] >= 4.5 and f["ret5"] >= 10.0 and f["volRatio"] >= 1.2)):
                return 62.0 + f["consecZt"] * 9.0 + min(15.0, f["amountYi"] * 1.5) + min(12.0, f["ret5"] * 0.4)
            return None

    if style_key == "huichanglan_mainline":
        if not f["aboveMA20"] or board not in ("main", "chinext"):
            return None
        # Avoid dead tech bottom-fishing; require strong recent mainline momentum + clean breakout/pullback
        if f["ret20"] >= 12.0 and (2.0 <= f["pct"] <= 7.5 or f["isZt"]) and f["volRatio"] >= 1.15 and not f["bombedZt"]:
            return 58.0 + f["ret20"] * 0.4 + (14.0 if f["isZt"] else 6.0) + min(10.0, f["amountYi"])
        return None

    if style_key == "xiwangjiejie_1to2":
        # Strictly mainboard 1-board (next day 1->2 candidate), avoid index heavy decline day
        if board != "main" or f["consecZt"] != 1 or not f["aboveMA20"]:
            return None
        if stage == "decline" and (env.get("zz1000ChangePct") or 0.0) <= -1.2:
            return None
        return 70.0 + min(18.0, f["volRatio"] * 4.0) + min(12.0, f["amountYi"] * 1.5)

    if style_key == "dongshizhang_highboard":
        # Strictly mainboard >= 2 consecutive limit-ups (high-board sniper)
        if board != "main" or f["consecZt"] < 2 or not f["aboveMA20"]:
            return None
        return 75.0 + f["consecZt"] * 10.0 + min(15.0, f["amountYi"])

    if style_key == "ruhan_halfway":
        # Diversified non-highboard early breakout / halfway momentum above MA20
        if not f["aboveMA20"] or f["consecZt"] >= 2 or board == "bse":
            return None
        if (3.0 <= f["pct"] <= 9.6 or f["consecZt"] == 1) and f["volRatio"] >= 1.25 and f["amountYi"] >= 2.5:
            return 55.0 + min(20.0, f["volRatio"] * 5.0) + min(15.0, f["ret5"] * 0.7) + (10.0 if f["isZt"] else 0.0)
        return None

    # songsong_defensive: strong trend pullback / dip buy above MA20
    if not f["aboveMA20"] or f["ret20"] < 15.0 or board == "bse":
        return None
    if -4.5 <= f["pct"] <= 2.5 and f["aboveMA5"]:
        return 62.0 + min(25.0, f["ret20"] * 0.4) + min(10.0, f["amountYi"])
    return None


def _simulate(
    style_key: str,
    params: dict[str, Any],
    dates: list[str],
    features_by_sym: dict[str, dict[str, dict[str, Any]]],
    names: dict[str, str],
    env_by_date: dict[str, dict[str, Any]],
    initial_capital: float,
    *,
    ablation_no_discipline: bool = False,
    cancelled: Callable[[], bool] | None = None,
    day_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run one causal daily-signal / next-open execution simulation governed by hard rules."""
    cash = float(initial_capital)
    holdings: dict[str, dict[str, Any]] = {}
    pending_sells: list[dict[str, Any]] = []
    pending_adds: list[dict[str, Any]] = []
    pending_buys: list[dict[str, Any]] = []

    curve: list[dict[str, Any]] = []
    position_timeline: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    closed_episodes: list[dict[str, Any]] = []
    peak_eq = float(initial_capital)
    prev_day_eq = float(initial_capital)
    max_dd = 0.0
    total_fees = 0.0
    consec_losses = 0
    throttle_days = 0
    halt_new_buys = False
    empty_days = 0

    rule_stats: dict[str, int] = {
        "openBuys": 0,
        "addWins": 0,
        "profitTrims": 0,
        "regimeTrims": 0,
        "capTrims": 0,
        "techRotations": 0,
        "hardStops": 0,
        "fastWeakCuts": 0,
        "profitExits": 0,
        "timeExits": 0,
        "throttleDays": 0,
        "dailyBreakerTriggers": 0,
        "auctionCancels": 0,
        "capBlocks": 0,
        "regimeShutoffs": 0,
    }

    stage_labels = {"advance": "主升", "trial": "低位试错", "chop": "高位震荡", "decline": "主跌退潮"}
    final_plan: dict[str, Any] | None = None
    total_days = len(dates)
    previous_stage = "chop"

    def _live_open_equity(cur_day: str) -> float:
        mv_now = 0.0
        for s, h in holdings.items():
            f_s = features_by_sym.get(s, {}).get(cur_day)
            px_s = f_s["open"] if f_s else h["lastClose"]
            mv_now += h["qty"] * px_s
        return cash + mv_now

    def _live_open_mv(cur_day: str) -> float:
        mv_now = 0.0
        for s, h in holdings.items():
            f_s = features_by_sym.get(s, {}).get(cur_day)
            px_s = f_s["open"] if f_s else h["lastClose"]
            mv_now += h["qty"] * px_s
        return mv_now

    for idx, day in enumerate(dates):
        if cancelled and (idx % 10 == 0) and cancelled():
            raise BacktestCancelled()
        if day_progress and (idx == 0 or (idx + 1) % 20 == 0 or idx + 1 == total_days):
            day_progress(idx + 1, total_days)
        # ── 1. Execute morning open SELLS & PARTIAL TRIMS (T+1 safe) ─────────
        still_pending_sells: list[dict[str, Any]] = []
        eq_before_open = _live_open_equity(day)
        for order in pending_sells:
            sym = order["symbol"]
            pos = holdings.get(sym)
            if not pos or pos["qty"] <= 0:
                continue
            if pos["entryDate"] >= day:
                still_pending_sells.append(order)
                continue
            f = features_by_sym.get(sym, {}).get(day)
            if not f or f["openAtDt"]:
                # Missing bar or limit-down at open: cannot sell, carry to next day
                still_pending_sells.append(order)
                continue

            req_ratio = float(order.get("sellRatio") or 1.0)
            if req_ratio >= 0.99 or int(order.get("quantity") or 0) >= pos["qty"]:
                sell_qty = pos["qty"]
            else:
                sell_qty = calc_trim_shares(pos["qty"], req_ratio, sym)
            if sell_qty <= 0:
                continue

            exec_px = f["open"] * (1.0 - SLIPPAGE_RATE)
            before_w = (pos["qty"] * f["open"] / eq_before_open * 100.0) if eq_before_open > 0 else 0.0
            share_frac = sell_qty / max(1, pos["qty"])
            cost_part = pos["cost"] * share_frac
            gross = sell_qty * exec_px
            fee = max(MIN_COMMISSION, gross * COMMISSION_RATE) + gross * STAMP_DUTY_SELL
            net = gross - fee
            total_fees += fee
            cash += net
            pnl = net - cost_part
            ret_pct = (pnl / cost_part * 100.0) if cost_part > 0 else 0.0
            hold_d = idx - pos["entryIdx"]

            pos["qty"] -= sell_qty
            pos["cost"] = max(0.0, pos["cost"] - cost_part)
            pos["realizedPnl"] += pnl

            act_type = order.get("actionType") or "sell_profit"
            if pos["qty"] > 0 and act_type == "trim_profit":
                pos["trimmedProfit"] = True

            # Account for exact rule execution stats
            if act_type == "trim_profit":
                rule_stats["profitTrims"] += 1
            elif act_type == "trim_regime":
                rule_stats["regimeTrims"] += 1
            elif act_type == "trim_cap":
                rule_stats["capTrims"] += 1
            elif act_type == "sell_rotate":
                rule_stats["techRotations"] += 1
            elif act_type == "cut_stop":
                rule_stats["hardStops"] += 1
            elif act_type == "cut_weak":
                rule_stats["fastWeakCuts"] += 1
            elif act_type == "sell_time":
                rule_stats["timeExits"] += 1
            else:
                rule_stats["profitExits"] += 1

            if pos["qty"] <= 0:
                ep_pnl = pos["realizedPnl"]
                ep_cost = max(1.0, pos["totalEntryCost"])
                ep_ret = ep_pnl / ep_cost * 100.0
                closed_episodes.append({
                    "symbol": sym, "name": names.get(sym, sym[2:]), "entryDate": pos["entryDate"],
                    "exitDate": day, "holdDays": hold_d, "pnl": ep_pnl, "returnPct": ep_ret,
                    "entryStage": pos["entryStage"], "reason": order.get("reason") or "规则退出",
                })
                if ep_pnl <= 0:
                    consec_losses += 1
                    if consec_losses >= int(params.get("consecLossThrottle", 2)) and not ablation_no_discipline:
                        throttle_days = max(throttle_days, 2)
                else:
                    consec_losses = 0
                del holdings[sym]

            eq_after_sell = max(1.0, _live_open_equity(day))
            rem_qty = holdings[sym]["qty"] if sym in holdings else 0
            after_w = (rem_qty * f["open"] / eq_after_sell * 100.0) if rem_qty > 0 else 0.0
            after_total_pos = (_live_open_mv(day) / eq_after_sell * 100.0)
            trade_w = (gross / eq_after_sell * 100.0)

            trades.append({
                "date": day,
                "side": "sell",
                "actionType": act_type,
                "actionLabel": order.get("actionLabel") or ACTION_LABELS.get(act_type, "卖出"),
                "ruleCode": order.get("ruleCode") or "",
                "symbol": sym,
                "code": sym[2:],
                "name": names.get(sym, sym[2:]),
                "board": board_label(_board(sym)),
                "quantity": sell_qty,
                "price": round(exec_px, 3),
                "amount": round(gross, 2),
                "fees": round(fee, 2),
                "pnl": round(pnl, 2),
                "returnPct": round(ret_pct, 2),
                "holdDays": hold_d,
                "tradeWeightPct": round(trade_w, 1),
                "beforeStockWeightPct": round(before_w, 1),
                "afterStockWeightPct": round(after_w, 1),
                "afterTotalPosPct": round(after_total_pos, 1),
                "afterHoldingCount": len(holdings),
                "reason": order.get("reason") or "规则退出",
                "stage": previous_stage,
            })
        pending_sells = still_pending_sells

        # Compute live equity & hard caps at open before morning adds/buys
        eq_open = max(1.0, _live_open_equity(day))
        open_limits = resolve_position_limits(
            style_key,
            previous_stage,
            eq_open,
            params,
            throttled=(throttle_days > 0),
            halt_new_buys=halt_new_buys,
            ablation_no_discipline=ablation_no_discipline,
        )

        # ── 2. Execute morning ADDS (pyramiding on confirmed ZT winners) ─────
        for order in pending_adds:
            sym = order["symbol"]
            pos = holdings.get(sym)
            if not pos:
                continue
            f = features_by_sym.get(sym, {}).get(day)
            if not f or f["openAtZt"] or f["openAtDt"]:
                continue
            if f["openPct"] < -1.0:
                rule_stats["auctionCancels"] += 1
                continue
            cur_mv = _live_open_mv(day)
            cur_w = (pos["qty"] * f["open"]) / eq_open
            single_room = max(0.0, open_limits["singleCap"] - cur_w)
            total_room = max(0.0, open_limits["totalPosCap"] - (cur_mv / eq_open))
            add_w = min(float(order.get("targetWeight") or open_limits["addWinWeight"]), single_room, total_room)
            if add_w < 0.03:
                rule_stats["capBlocks"] += 1
                continue
            exec_px = f["open"] * (1.0 + SLIPPAGE_RATE)
            budget = min(cash * 0.96, eq_open * add_w)
            qty = calc_buy_shares(budget, exec_px, sym, COMMISSION_RATE)
            if qty > 0:
                gross = qty * exec_px
                fee = max(MIN_COMMISSION, gross * COMMISSION_RATE)
                if gross + fee <= cash:
                    before_w = cur_w * 100.0
                    cash -= (gross + fee)
                    total_fees += fee
                    pos["qty"] += qty
                    pos["cost"] += (gross + fee)
                    pos["totalEntryCost"] += (gross + fee)
                    pos["addedWin"] = True
                    rule_stats["addWins"] += 1
                    eq_after = max(1.0, _live_open_equity(day))
                    after_w = (pos["qty"] * f["open"] / eq_after * 100.0)
                    after_total_pos = (_live_open_mv(day) / eq_after * 100.0)
                    trades.append({
                        "date": day,
                        "side": "buy",
                        "actionType": "add_win",
                        "actionLabel": ACTION_LABELS["add_win"],
                        "ruleCode": order.get("ruleCode") or "PYRAMID_ZT_WINNER",
                        "symbol": sym,
                        "code": sym[2:],
                        "name": names.get(sym, sym[2:]),
                        "board": board_label(_board(sym)),
                        "quantity": qty,
                        "price": round(exec_px, 3),
                        "amount": round(gross, 2),
                        "fees": round(fee, 2),
                        "pnl": None,
                        "returnPct": None,
                        "holdDays": None,
                        "tradeWeightPct": round(gross / eq_after * 100.0, 1),
                        "beforeStockWeightPct": round(before_w, 1),
                        "afterStockWeightPct": round(after_w, 1),
                        "afterTotalPosPct": round(after_total_pos, 1),
                        "afterHoldingCount": len(holdings),
                        "reason": order.get("reason") or "封板确认·次日顺势浮盈加仓",
                        "stage": previous_stage,
                    })
        pending_adds = []

        # ── 3. Execute morning NEW BUYS (sized under live hard caps) ─────────
        for order in pending_buys:
            sym = order["symbol"]
            if sym in holdings or len(holdings) >= open_limits["maxSlots"]:
                continue
            f = features_by_sym.get(sym, {}).get(day)
            if not f or f["openAtZt"] or f["openAtDt"]:
                continue
            if style_key not in ("dongshizhang_highboard", "xiwangjiejie_1to2") and f["openPct"] > 7.5:
                rule_stats["auctionCancels"] += 1
                continue
            if (not ablation_no_discipline) and style_key in ("dongshizhang_highboard", "xiwangjiejie_1to2") and f["openPct"] < -1.5:
                rule_stats["auctionCancels"] += 1
                continue
            cur_mv = _live_open_mv(day)
            total_room = max(0.0, open_limits["totalPosCap"] - (cur_mv / eq_open))
            if total_room < 0.03:
                rule_stats["capBlocks"] += 1
                continue
            target_w = min(float(order.get("targetWeight") or open_limits["firstEntryWeight"]), open_limits["singleCap"], total_room)
            exec_px = f["open"] * (1.0 + SLIPPAGE_RATE)
            budget = min(cash * 0.96, eq_open * target_w)
            qty = calc_buy_shares(budget, exec_px, sym, COMMISSION_RATE)
            if qty > 0:
                gross = qty * exec_px
                fee = max(MIN_COMMISSION, gross * COMMISSION_RATE)
                if gross + fee <= cash:
                    cash -= (gross + fee)
                    total_fees += fee
                    holdings[sym] = {
                        "qty": qty,
                        "cost": gross + fee,
                        "totalEntryCost": gross + fee,
                        "realizedPnl": 0.0,
                        "entryDate": day,
                        "entryIdx": idx,
                        "entryStage": previous_stage,
                        "peakPrice": exec_px,
                        "lastClose": f["open"],
                        "addedWin": False,
                        "trimmedProfit": False,
                    }
                    rule_stats["openBuys"] += 1
                    eq_after = max(1.0, _live_open_equity(day))
                    after_w = (qty * f["open"] / eq_after * 100.0)
                    after_total_pos = (_live_open_mv(day) / eq_after * 100.0)
                    trades.append({
                        "date": day,
                        "side": "buy",
                        "actionType": "open_buy",
                        "actionLabel": ACTION_LABELS["open_buy"],
                        "ruleCode": order.get("ruleCode") or "SIZED_FIRST_ENTRY",
                        "symbol": sym,
                        "code": sym[2:],
                        "name": names.get(sym, sym[2:]),
                        "board": board_label(_board(sym)),
                        "quantity": qty,
                        "price": round(exec_px, 3),
                        "amount": round(gross, 2),
                        "fees": round(fee, 2),
                        "pnl": None,
                        "returnPct": None,
                        "holdDays": None,
                        "tradeWeightPct": round(gross / eq_after * 100.0, 1),
                        "beforeStockWeightPct": 0.0,
                        "afterStockWeightPct": round(after_w, 1),
                        "afterTotalPosPct": round(after_total_pos, 1),
                        "afterHoldingCount": len(holdings),
                        "reason": order.get("reason") or "规则首仓建仓",
                        "stage": previous_stage,
                    })
        pending_buys = []

        # ── 4. End-of-day mark-to-market, drawdown circuit breaker & snapshot ─
        # Today's regime depends on closing prices and can first govern tomorrow's fills.
        env = env_by_date.get(day, {"stage": "chop", "stageLabel": "震荡", "zz1000ChangePct": 0.0, "turnoverYi": 15000.0})
        stage = env.get("stage", "chop")
        stage_lbl = env.get("stageLabel") or stage_labels.get(stage, "震荡")
        previous_stage = stage
        mv = 0.0
        day_feats: dict[str, dict[str, Any]] = {}
        day_holdings_snap: list[dict[str, Any]] = []
        for sym, pos in holdings.items():
            f = features_by_sym.get(sym, {}).get(day)
            if f:
                day_feats[sym] = f
            px = f["close"] if f else pos["lastClose"]
            pos["lastClose"] = px
            pos["peakPrice"] = max(pos["peakPrice"], f["high"] if f else px)
            mv += pos["qty"] * px

        equity = cash + mv
        if not holdings:
            empty_days += 1
        peak_eq = max(peak_eq, equity)
        dd_pct = ((equity / peak_eq) - 1.0) * 100.0 if peak_eq > 0 else 0.0
        max_dd = min(max_dd, dd_pct)
        pos_ratio = (mv / equity * 100.0) if equity > 0 else 0.0

        # Daily drawdown circuit breaker (e.g., <= -2.0% single day halts new opens + halves next-day size)
        day_ret_pct = ((equity / prev_day_eq) - 1.0) * 100.0 if prev_day_eq > 0 else 0.0
        prev_day_eq = equity
        if (not ablation_no_discipline) and day_ret_pct <= float(params.get("dailyLossBreakerPct", -2.0)):
            halt_new_buys = True
            throttle_days = max(throttle_days, 2)
            rule_stats["dailyBreakerTriggers"] += 1

        is_throttled_today = (throttle_days > 0) and (not ablation_no_discipline)
        if is_throttled_today:
            rule_stats["throttleDays"] += 1

        curve.append({
            "date": day,
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "returnPct": round((equity / initial_capital - 1.0) * 100.0, 2),
            "drawdownPct": round(dd_pct, 2),
            "positionPct": round(pos_ratio, 1),
            "holdingCount": len(holdings),
            "stage": stage,
            "stageLabel": stage_lbl,
            "throttled": is_throttled_today,
        })

        # Keep daily holdings snapshots for the last 90 trading days for interactive playback
        if idx >= max(0, total_days - 90):
            for sym, pos in holdings.items():
                px = pos["lastClose"]
                avg_c = pos["cost"] / max(1, pos["qty"])
                h_mv = pos["qty"] * px
                h_w = (h_mv / equity * 100.0) if equity > 0 else 0.0
                h_pnl_pct = ((px / avg_c) - 1.0) * 100.0 if avg_c > 0 else 0.0
                tag = "已加仓" if pos["addedWin"] else ("已减半" if pos["trimmedProfit"] else "首仓")
                day_holdings_snap.append({
                    "symbol": sym,
                    "code": sym[2:],
                    "name": names.get(sym, sym[2:]),
                    "board": board_label(_board(sym)),
                    "quantity": pos["qty"],
                    "avgCost": round(avg_c, 3),
                    "closePrice": round(px, 3),
                    "marketValue": round(h_mv, 2),
                    "weightPct": round(h_w, 1),
                    "pnlPct": round(h_pnl_pct, 2),
                    "statusTag": tag,
                })
            day_holdings_snap.sort(key=lambda x: -x["weightPct"])
            position_timeline.append({
                "date": day,
                "stage": stage,
                "stageLabel": stage_lbl,
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "cashPct": round(max(0.0, 100.0 - pos_ratio), 1),
                "positionPct": round(pos_ratio, 1),
                "holdingCount": len(holdings),
                "throttled": is_throttled_today,
                "holdings": day_holdings_snap,
            })

        # ── 5. Score EOD candidates & invoke unified rule planner ────────────
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for sym, fmap in features_by_sym.items():
            if sym in holdings:
                continue
            f_c = fmap.get(day)
            if not f_c:
                continue
            sc = _score_candidate(style_key, f_c, env)
            if sc is not None:
                scored.append((sc, sym, f_c))
        scored.sort(key=lambda x: -x[0])
        cand_cap = max(15, int(params.get("maxPositions", 5)) * 2 + 4)
        cand_dicts = [
            {
                "symbol": sym,
                "code": sym[2:],
                "name": names.get(sym, sym[2:]),
                "score": round(sc, 1),
                "close": f_c["close"],
                "boardCode": f_c["board"],
            }
            for sc, sym, f_c in scored[:cand_cap]
        ]
        for sc, sym, f_c in scored[:cand_cap]:
            day_feats[sym] = f_c

        holding_inputs = [
            {
                "symbol": sym,
                "code": sym[2:],
                "name": names.get(sym, sym[2:]),
                "qty": pos["qty"],
                "avgCost": pos["cost"] / max(1, pos["qty"]),
                "peakPrice": pos["peakPrice"],
                "lastClose": pos["lastClose"],
                "heldDays": idx - pos["entryIdx"],
                "addedWin": pos["addedWin"],
                "trimmedProfit": pos["trimmedProfit"],
            }
            for sym, pos in holdings.items()
        ]

        plan = plan_next_day_orders(
            style_key=style_key,
            params=params,
            env=env,
            equity=equity,
            cash=cash,
            holdings=holding_inputs,
            features_by_sym=day_feats,
            candidates=cand_dicts,
            throttled=is_throttled_today,
            halt_new_buys=halt_new_buys,
            ablation_no_discipline=ablation_no_discipline,
            commission_rate=COMMISSION_RATE,
            stamp_duty_sell=STAMP_DUTY_SELL,
            slippage_rate=SLIPPAGE_RATE,
        )

        # Consume daily flags after planning
        halt_new_buys = False
        if throttle_days > 0:
            throttle_days -= 1

        if idx + 1 < total_days:
            for b in plan["blockedSignals"]:
                if b["ruleCode"] in ("CAP_BLOCK_ADD", "MAX_SLOTS_FULL", "TOTAL_CAP_FULL"):
                    rule_stats["capBlocks"] += 1
                elif b["ruleCode"] in ("DECLINE_SHUTOFF", "DAILY_LOSS_BREAKER"):
                    rule_stats["regimeShutoffs"] += 1
            for o in plan["orders"]:
                if o["side"] == "sell":
                    if not any(ps["symbol"] == o["symbol"] for ps in pending_sells):
                        pending_sells.append(o)
                elif o["actionType"] == "add_win":
                    pending_adds.append(o)
                elif o["actionType"] == "open_buy":
                    pending_buys.append(o)
        else:
            final_plan = {
                "signalDate": day,
                "stage": stage,
                "stageLabel": stage_lbl,
                **plan,
            }

    # Build final end-of-backtest holdings table
    final_eq = curve[-1]["equity"] if curve else float(initial_capital)
    last_day = dates[-1] if dates else ""
    final_stop_line = -15.0 if ablation_no_discipline else float(params.get("stopLossPct", -5.0))
    final_holdings: list[dict[str, Any]] = []
    for sym, pos in holdings.items():
        if pos["qty"] <= 0:
            continue
        f = features_by_sym.get(sym, {}).get(last_day) or {}
        px = pos["lastClose"]
        avg_c = pos["cost"] / max(1, pos["qty"])
        h_mv = pos["qty"] * px
        h_w = (h_mv / final_eq * 100.0) if final_eq > 0 else 0.0
        u_pnl = h_mv - pos["cost"]
        u_ret = (u_pnl / pos["cost"] * 100.0) if pos["cost"] > 0 else 0.0
        eff_stop = max(final_stop_line, -0.5) if ((pos["addedWin"] or pos["trimmedProfit"]) and not ablation_no_discipline) else final_stop_line
        final_holdings.append({
            "symbol": sym,
            "code": sym[2:],
            "name": names.get(sym, sym[2:]),
            "board": board_label(_board(sym)),
            "quantity": pos["qty"],
            "avgCost": round(avg_c, 3),
            "closePrice": round(px, 3),
            "marketValue": round(h_mv, 2),
            "weightPct": round(h_w, 1),
            "unrealizedPnl": round(u_pnl, 2),
            "unrealizedPnlPct": round(u_ret, 2),
            "holdDays": (total_days - 1 - pos["entryIdx"]) if total_days else 0,
            "addedWin": pos["addedWin"],
            "trimmedProfit": pos["trimmedProfit"],
            "statusTag": "已浮盈加仓" if pos["addedWin"] else ("已冲高减半" if pos["trimmedProfit"] else "首仓持有"),
            "stopPrice": round(avg_c * (1.0 + eff_stop / 100.0), 2) if avg_c > 0 else None,
            "ma5": round(float(f["ma5"]), 2) if f.get("ma5") else None,
        })
    final_holdings.sort(key=lambda x: -x["weightPct"])
    final_cash_pct = round(max(0.0, (cash / final_eq * 100.0)) if final_eq > 0 else 100.0, 1)

    # Summarize metrics
    tot_ret = ((final_eq / initial_capital) - 1.0) * 100.0
    years = max(0.08, len(dates) / 242.0)
    ann_ret = (((final_eq / initial_capital) ** (1.0 / years)) - 1.0) * 100.0 if final_eq > 0 else -100.0
    calmar = round(tot_ret / abs(max_dd), 2) if max_dd < -0.1 else None

    wins = [e for e in closed_episodes if e["pnl"] > 0]
    losses = [e for e in closed_episodes if e["pnl"] <= 0]
    wr = round(len(wins) / len(closed_episodes) * 100.0, 1) if closed_episodes else 0.0
    avg_w = mean(e["returnPct"] for e in wins) if wins else 0.0
    avg_l = mean(e["returnPct"] for e in losses) if losses else 0.0
    payoff = round(avg_w / abs(avg_l), 2) if avg_l < 0 else None
    g_win = sum(e["pnl"] for e in wins)
    g_loss = sum(e["pnl"] for e in losses)
    pf = round(g_win / abs(g_loss), 2) if g_loss < 0 else None
    avg_hold = round(mean(e["holdDays"] for e in closed_episodes), 2) if closed_episodes else 0.0

    # Regime breakdown
    by_stage = []
    for sk in ("advance", "trial", "chop", "decline"):
        seps = [e for e in closed_episodes if e["entryStage"] == sk]
        sdays = [c for c in curve if c["stage"] == sk]
        if not sdays:
            continue
        sw = [e for e in seps if e["pnl"] > 0]
        by_stage.append({
            "stage": sk,
            "label": stage_labels[sk],
            "days": len(sdays),
            "avgPosPct": round(mean(c["positionPct"] for c in sdays), 1),
            "episodes": len(seps),
            "winRatePct": round(len(sw) / len(seps) * 100.0, 1) if seps else None,
            "avgReturnPct": round(mean(e["returnPct"] for e in seps), 2) if seps else None,
            "totalPnl": round(sum(e["pnl"] for e in seps), 2),
        })

    return {
        "summary": {
            "initialCapital": round(initial_capital, 2),
            "finalEquity": round(final_eq, 2),
            "totalReturnPct": round(tot_ret, 2),
            "annualizedReturnPct": round(ann_ret, 2),
            "maxDrawdownPct": round(max_dd, 2),
            "calmarRatio": calmar,
            "tradeCount": len(trades),
            "closedEpisodes": len(closed_episodes),
            "winRatePct": wr,
            "avgWinPct": round(avg_w, 2),
            "avgLossPct": round(avg_l, 2),
            "payoffRatio": payoff,
            "profitFactor": pf,
            "avgHoldDays": avg_hold,
            "emptyDayRatioPct": round(empty_days / max(1, len(dates)) * 100.0, 1),
            "avgPositionPct": round(mean(c["positionPct"] for c in curve), 1) if curve else 0.0,
            "avgHoldingCount": round(mean(c["holdingCount"] for c in curve), 2) if curve else 0.0,
            "maxHoldingCount": max((c["holdingCount"] for c in curve), default=0),
            "totalFees": round(total_fees, 2),
        },
        "byStage": by_stage,
        "curve": curve,
        "trades": trades[-5000:],  # retain up to 5000 trades for full pagination & K-line overlays
        "totalTrades": len(trades),
        "ruleExecutionStats": rule_stats,
        "finalHoldings": final_holdings,
        "finalCash": round(cash, 2),
        "finalCashPct": final_cash_pct,
        "positionTimeline": position_timeline,
        "nextDayOrders": final_plan,
    }


def _valid_iso_date(value: Any, default_value: str) -> str:
    raw = str(value or "").strip() or default_value
    try:
        parsed = dt.date.fromisoformat(raw)
        if not 1990 <= parsed.year <= 2100:
            raise ValueError()
        return parsed.isoformat()
    except ValueError:
        raise ValueError(f"回测日期无效：{raw}，请使用 YYYY-MM-DD 格式") from None


def normalize_master_backtest_config(
    payload: dict[str, Any] | None = None,
    master: dict | None = None,
) -> dict[str, Any]:
    """Validate and normalize master backtest parameters for both direct RPC and ResearchService tasks."""
    payload = payload or {}
    raw_style = str(payload.get("styleKey") or "").strip()
    skey = detect_style_key(master, raw_style)
    try:
        initial_capital = float(payload.get("initialCapital") or 100000.0)
    except (TypeError, ValueError):
        raise ValueError("回测初始本金必须是有效数字") from None
    if not math.isfinite(initial_capital) or not (10000.0 <= initial_capital <= 5e8):
        raise ValueError("回测初始本金须在 1 万元至 5 亿元之间")

    start_date = _valid_iso_date(payload.get("startDate"), "2026-05-01")
    end_date = _valid_iso_date(payload.get("endDate"), "2026-11-25")
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")

    data_source = str(payload.get("dataSource") or "auto").strip().lower()
    if data_source not in ("auto", "tdx", "bundled"):
        data_source = "auto"

    raw_max_pos = payload.get("maxPositions")
    custom_flag = payload.get("customMaxPositions")
    if custom_flag is False or raw_max_pos in (None, "", 0, "0", "auto", "default"):
        effective_payload = {k: v for k, v in payload.items() if k != "maxPositions"}
        effective_payload["customMaxPositions"] = False
        custom_max_pos = False
    else:
        try:
            parsed_max_pos = int(raw_max_pos)
        except (TypeError, ValueError):
            raise ValueError("最大持仓数量须为 1 至 20 之间的整数") from None
        if not 1 <= parsed_max_pos <= 20:
            raise ValueError("最大持仓数量须在 1 至 20 只之间")
        effective_payload = {**payload, "maxPositions": parsed_max_pos, "customMaxPositions": True}
        custom_max_pos = True

    compare_counts = bool(payload.get("comparePositionCounts", True))
    params = _strategy_params(skey, effective_payload)
    master_id = str(payload.get("masterId") or (master or {}).get("id") or "").strip() or None
    master_name = str(payload.get("masterName") or (master or {}).get("name") or "").strip() or None
    return {
        "styleKey": skey,
        "styleName": STYLE_PROFILES[skey]["name"],
        "masterId": master_id,
        "masterName": master_name,
        "startDate": start_date,
        "endDate": end_date,
        "initialCapital": round(initial_capital, 2),
        "stopLossPct": params["stopLossPct"],
        "maxPositions": params["maxPositions"],
        "baseMaxPositions": params["baseMaxPositions"],
        "customMaxPositions": custom_max_pos,
        "comparePositionCounts": compare_counts,
        "useRegimeSwitch": bool(params["useRegimeSwitch"]),
        "useFastWeakCut": bool(params["useFastWeakCut"]),
        "useAddOnWin": bool(params["useAddOnWin"]),
        "usePartialTrim": bool(params["usePartialTrim"]),
        "dataSource": data_source,
    }


def _query_local_tdx_source(
    owner,
    directory: Path | None,
    start_date: str,
    end_date: str,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """Read local TDX path & non-BSE SH/SZ stock rows from MarketService owner or read-only market.sqlite3."""
    root = None
    rows: list[dict[str, Any]] = []
    if owner is not None and hasattr(owner, "_get") and hasattr(owner, "db"):
        chosen = owner._get("path")
        if chosen:
            try:
                root = tdx.normalize_directory(chosen)
            except Exception:
                root = None
        if root is not None:
            try:
                fetched = owner.db.execute(
                    "SELECT symbol, name FROM securities WHERE kind='stock' AND market IN ('sh','sz') "
                    "AND earliest_date <= ? AND latest_date >= ? ORDER BY symbol",
                    (end_date, start_date),
                ).fetchall()
                rows = [{"symbol": r["symbol"], "name": r["name"]} for r in fetched]
            except Exception:
                rows = []
        return root, rows

    if directory is None:
        return None, []
    cache = Path(directory) / "market.sqlite3"
    if not cache.exists():
        return None, []
    import json
    db = sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        path_row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
        if not path_row:
            return None, []
        try:
            root = tdx.normalize_directory(json.loads(path_row[0]))
        except Exception:
            return None, []
        fetched = db.execute(
            "SELECT symbol, name FROM securities WHERE kind='stock' AND market IN ('sh','sz') "
            "AND earliest_date <= ? AND latest_date >= ? ORDER BY symbol",
            (end_date, start_date),
        ).fetchall()
        rows = [{"symbol": r["symbol"], "name": r["name"]} for r in fetched]
        return root, rows
    except sqlite3.Error:
        return None, []
    finally:
        db.close()


def run_master_backtest(
    owner,
    master: dict | None,
    payload: dict[str, Any] | None = None,
    progress=None,
    *,
    cancelled: Callable[[], bool] | None = None,
    directory: Path | None = None,
) -> dict[str, Any]:
    """Run Master-Style Backtest + No-Discipline Ablation + 中证1000 Benchmark."""
    cfg = normalize_master_backtest_config(payload, master)
    skey = cfg["styleKey"]
    profile = STYLE_PROFILES[skey]
    initial_capital = float(cfg["initialCapital"])
    start_date = cfg["startDate"]
    end_date = cfg["endDate"]
    data_source_req = cfg["dataSource"]

    def check_cancelled() -> None:
        if cancelled and cancelled():
            raise BacktestCancelled()

    check_cancelled()

    # Discover optional local TDX directory & SH/SZ catalogue
    tdx_root: Path | None = None
    tdx_rows: list[dict[str, Any]] = []
    if data_source_req in ("auto", "tdx"):
        tdx_root, tdx_rows = _query_local_tdx_source(owner, directory, start_date, end_date)

    # Load bundled fallback market env & indices
    fb_env = build_fallback_market_env()
    fb_bench = get_fallback_index_bars("sh000852") or get_fallback_index_bars("sh000001")
    fb_bench_dates = [b["date"] for b in fb_bench if start_date <= b["date"] <= end_date]
    fb_syms = [
        s for s in get_fallback_all_stock_symbols()
        if not s.startswith("bj") and not s[2:].startswith(("11", "12", "51", "56", "58", "15", "16"))
    ]
    fb_names = get_fallback_names()

    # Check how well bundled stocks actually cover [start_date, end_date] (2023-03..2026-02 only has ~16 stocks)
    fb_active_count = 0
    for sym in fb_syms:
        bars = get_fallback_stock_bars(sym)
        if bars and bars[0]["date"] <= end_date and bars[-1]["date"] >= start_date:
            fb_active_count += 1

    want_tdx = False
    fallback_note = ""
    if data_source_req == "tdx":
        if tdx_root is not None and len(tdx_rows) >= 20:
            want_tdx = True
        else:
            fallback_note = "本地通达信目录未配置或该区间缺少足够沪深日线，已自动回退到内置真实历史样本。"
    elif data_source_req == "auto":
        # Automatically upgrade to local TDX when bundled sample is sparse (< 40 stocks) or misses the date range
        if (len(fb_bench_dates) < 5 or fb_active_count < 40) and tdx_root is not None and len(tdx_rows) >= 30:
            want_tdx = True

    env_by_date: dict[str, dict[str, Any]] = dict(fb_env)
    bench_bars: list[dict[str, Any]] = list(fb_bench)
    bench_name = "中证1000 (sh000852)" if get_fallback_index_bars("sh000852") else "上证指数 (sh000001)"
    source_used = "bundled"
    source_label = "内置 2022–2026 真实历史行情样本（544 只实盘交割单池，零外网依赖）"
    features_by_sym: dict[str, dict[str, dict[str, Any]]] = {}
    names = dict(fb_names)

    if want_tdx and tdx_root is not None:
        selected = _select_tdx_universe(tdx_rows, set(fb_names.keys()), limit=MAX_TDX_UNIVERSE)
        tdx_env, tdx_bench = _build_tdx_market_env(tdx_root, start_date, end_date)
        if tdx_env:
            env_by_date.update(tdx_env)
        tdx_bench_dates = [b["date"] for b in tdx_bench if start_date <= b["date"] <= end_date]
        if len(tdx_bench_dates) >= 5:
            bench_bars = tdx_bench
            bench_name = "中证1000 (sh000852 · 本地TDX)" if tdx.daily_path(tdx_root, "sh000852").is_file() else "上证指数 (sh000001 · 本地TDX)"

        names = {sym: nm for sym, nm in selected}
        total_sel = len(selected)
        for idx_i, (sym, _nm) in enumerate(selected, 1):
            if idx_i % 25 == 0:
                check_cancelled()
            if progress and (idx_i == 1 or idx_i % 40 == 0 or idx_i == total_sel):
                progress("加载本地通达信回测日线", idx_i, total_sel)
            try:
                bars = _read_tdx_daily_window(tdx.daily_path(tdx_root, sym), start_date, end_date, warmup_bars=35)
                if len(bars) >= 25:
                    fmap = _prepare_stock_features(sym, bars, start_date=start_date, end_date=end_date)
                    if fmap:
                        features_by_sym[sym] = fmap
            except Exception:
                continue

        if len(features_by_sym) >= 20:
            source_used = "tdx"
            source_label = f"本地通达信沪深均衡样本（{len(features_by_sym)} 只主板/创业/科创 A 股，已剔除北交所）"
        else:
            features_by_sym = {}
            names = dict(fb_names)
            fallback_note = "本地通达信在该区间有效沪深日线不足 20 只，已自动回退到内置历史样本。"

    if not features_by_sym:
        total_fb = len(fb_syms)
        for idx_i, sym in enumerate(fb_syms, 1):
            if idx_i % 40 == 0:
                check_cancelled()
            if progress and (idx_i == 1 or idx_i % 80 == 0 or idx_i == total_fb):
                progress("准备高手策略回测特征", idx_i, total_fb)
            bars = get_fallback_stock_bars(sym)
            if len(bars) >= 25 and bars[0]["date"] <= end_date and bars[-1]["date"] >= start_date:
                fmap = _prepare_stock_features(sym, bars, start_date=start_date, end_date=end_date)
                if fmap:
                    features_by_sym[sym] = fmap
        source_label = f"内置 2022–2026 真实历史行情样本（区间有效 {len(features_by_sym)} 只标的）"

    # Determine trading calendar inside [start_date, end_date]
    all_bench_dates = [b["date"] for b in bench_bars if start_date <= b["date"] <= end_date]
    if len(all_bench_dates) < 5 and features_by_sym:
        # Fallback calendar from union of stock dates if index file is absent in local TDX
        union_dates = sorted({d for fmap in features_by_sym.values() for d in fmap.keys() if start_date <= d <= end_date})
        if len(union_dates) >= 5:
            all_bench_dates = union_dates
    if len(all_bench_dates) < 5:
        raise ValueError(f"区间 {start_date} 至 {end_date} 有效交易日不足 5 天，请调整回测时间区间或检查本地通达信日线覆盖。")

    params = _strategy_params(skey, cfg)
    check_cancelled()

    if progress:
        progress("执行高手完整模式回测", 0, len(all_bench_dates))
    main_res = _simulate(
        skey,
        params,
        all_bench_dates,
        features_by_sym,
        names,
        env_by_date,
        initial_capital,
        ablation_no_discipline=False,
        cancelled=cancelled,
        day_progress=(lambda done, tot: progress("执行高手完整模式回测", done, tot)) if progress else None,
    )

    check_cancelled()
    if progress:
        progress("执行无纪律对照组消融验证", 0, len(all_bench_dates))
    ablation_res = _simulate(
        skey,
        params,
        all_bench_dates,
        features_by_sym,
        names,
        env_by_date,
        initial_capital,
        ablation_no_discipline=True,
        cancelled=cancelled,
        day_progress=(lambda done, tot: progress("执行无纪律对照组消融验证", done, tot)) if progress else None,
    )

    # Align benchmark curve (中证1000 / 上证)
    bmap = {b["date"]: b["close"] for b in bench_bars}
    b0 = bmap.get(all_bench_dates[0]) or next((bmap[d] for d in all_bench_dates if d in bmap), 1.0)
    b_peak = b0
    b_max_dd = 0.0
    bench_curve = []
    last_b = b0
    for d in all_bench_dates:
        c = bmap.get(d, last_b)
        last_b = c
        b_peak = max(b_peak, c)
        dd = ((c / b_peak) - 1.0) * 100.0
        b_max_dd = min(b_max_dd, dd)
        bench_curve.append({"date": d, "returnPct": round((c / b0 - 1.0) * 100.0, 2), "drawdownPct": round(dd, 2)})
    bench_ret = bench_curve[-1]["returnPct"] if bench_curve else 0.0

    # Combine 3-way curve for SVG chart & generic research viewers (R04/R09/G03)
    combined_curve = []
    for m_pt, a_pt, b_pt in zip(main_res["curve"], ablation_res["curve"], bench_curve):
        bench_eq = round(initial_capital * (1.0 + b_pt["returnPct"] / 100.0), 2)
        combined_curve.append({
            "date": m_pt["date"],
            "equity": m_pt["equity"],
            "cash": m_pt.get("cash", 0.0),
            "ablationEquity": a_pt["equity"],
            "benchmarkEquity": bench_eq,
            "returnPct": m_pt["returnPct"],
            "drawdownPct": m_pt["drawdownPct"],
            "masterRetPct": m_pt["returnPct"],
            "ablationRetPct": a_pt["returnPct"],
            "benchRetPct": b_pt["returnPct"],
            "masterDdPct": m_pt["drawdownPct"],
            "ablationDdPct": a_pt["drawdownPct"],
            "positionPct": m_pt["positionPct"],
            "holdingCount": m_pt["holdingCount"],
            "stage": m_pt["stage"],
            "stageLabel": m_pt.get("stageLabel", ""),
            "throttled": m_pt.get("throttled", False),
        })

    # Optional fast multi-position-count comparison reusing precomputed features
    position_count_comparison: list[dict[str, Any]] = []
    if cfg.get("comparePositionCounts", True):
        base_slots = int(params.get("baseMaxPositions", params["maxPositions"]))
        cur_slots = int(params["maxPositions"])
        slot_grid = sorted({1, 2, 3, 4, 5, 6, 8, 10, base_slots, cur_slots})
        if progress:
            progress("对比不同持仓数量收益表现", 0, len(slot_grid))
        for s_idx, slot_n in enumerate(slot_grid, 1):
            check_cancelled()
            is_current = (slot_n == cur_slots)
            if is_current:
                sw_res = main_res
            else:
                sw_params = _strategy_params(skey, {**cfg, "maxPositions": slot_n, "customMaxPositions": True})
                sw_res = _simulate(
                    skey,
                    sw_params,
                    all_bench_dates,
                    features_by_sym,
                    names,
                    env_by_date,
                    initial_capital,
                    ablation_no_discipline=False,
                    cancelled=cancelled,
                )
            sw_sum = sw_res["summary"]
            raw_c = sw_res["curve"]
            if len(raw_c) <= 260:
                mini_curve = [{"date": pt["date"], "returnPct": pt["returnPct"], "equity": pt["equity"], "drawdownPct": pt["drawdownPct"]} for pt in raw_c]
            else:
                step_k = max(1, (len(raw_c) - 1) // 240)
                picked_idxs = sorted({*range(0, len(raw_c), step_k), len(raw_c) - 1})
                mini_curve = [{"date": raw_c[i]["date"], "returnPct": raw_c[i]["returnPct"], "equity": raw_c[i]["equity"], "drawdownPct": raw_c[i]["drawdownPct"]} for i in picked_idxs]
            position_count_comparison.append({
                "maxPositions": slot_n,
                "isCurrent": is_current,
                "isStyleDefault": slot_n == base_slots,
                "totalReturnPct": sw_sum["totalReturnPct"],
                "annualizedReturnPct": sw_sum["annualizedReturnPct"],
                "maxDrawdownPct": sw_sum["maxDrawdownPct"],
                "calmarRatio": sw_sum["calmarRatio"],
                "winRatePct": sw_sum["winRatePct"],
                "payoffRatio": sw_sum["payoffRatio"],
                "profitFactor": sw_sum["profitFactor"],
                "tradeCount": sw_sum["tradeCount"],
                "closedEpisodes": sw_sum["closedEpisodes"],
                "avgPositionPct": sw_sum["avgPositionPct"],
                "avgHoldingCount": sw_sum["avgHoldingCount"],
                "maxHoldingCount": sw_sum["maxHoldingCount"],
                "finalEquity": sw_sum["finalEquity"],
                "alphaVsBenchmarkPct": round(sw_sum["totalReturnPct"] - bench_ret, 2),
                "curve": mini_curve,
            })
            if progress and (s_idx == len(slot_grid) or s_idx % 3 == 0):
                progress("对比不同持仓数量收益表现", s_idx, len(slot_grid))

    ms = main_res["summary"]
    as_ = ablation_res["summary"]
    alpha_vs_bench = round(ms["totalReturnPct"] - bench_ret, 2)
    alpha_vs_ablation = round(ms["totalReturnPct"] - as_["totalReturnPct"], 2)
    dd_saved_vs_ablation = round(abs(as_["maxDrawdownPct"]) - abs(ms["maxDrawdownPct"]), 2)

    rs = main_res["ruleExecutionStats"]
    slot_mode_txt = (
        f"自定义上限 {params['maxPositions']} 仓（原版默认 {params['baseMaxPositions']} 仓）"
        if cfg.get("customMaxPositions")
        else f"原版默认上限 {params['maxPositions']} 仓"
    )
    verdict_points = [
        f"相对同期基准 {bench_name}（{bench_ret:+.2f}%，最大回撤 {b_max_dd:.2f}%），「{profile['name'].split('·')[0].strip()} · {slot_mode_txt}」收益 {ms['totalReturnPct']:+.2f}%（超额 Alpha {alpha_vs_bench:+.2f}%），期末净值 ¥{ms['finalEquity']:,.2f}，最大回撤 {ms['maxDrawdownPct']:.2f}%。",
        f"对比「去掉大盘择时+放宽止损至-15%」的无纪律对照组（收益 {as_['totalReturnPct']:+.2f}%，最大回撤 {as_['maxDrawdownPct']:.2f}%）：守纪律版本收益差 {alpha_vs_ablation:+.2f}%，回撤{'大幅收窄 ' + str(dd_saved_vs_ablation) + ' 个百分点' if dd_saved_vs_ablation > 0 else '变化 ' + str(dd_saved_vs_ablation) + ' 个百分点'}。",
        f"规则硬执行流水：首仓分仓 {rs['openBuys']} 笔 · 浮盈加仓 {rs['addWins']} 笔 · 冲高减半 {rs['profitTrims']} 笔 · 退潮/超限压仓 {rs['regimeTrims'] + rs['capTrims']} 笔 · 硬止损/破位快砍 {rs['hardStops'] + rs['fastWeakCuts']} 笔 · 降档风控 {rs['throttleDays']} 天 · 拦截超仓/弱势撤单 {rs['capBlocks'] + rs['auctionCancels']} 次。",
        f"成交统计：共完成 {ms['closedEpisodes']} 轮闭环（胜率 {ms['winRatePct']}%，盈亏比 {ms['payoffRatio'] or '—'}，盈利因子 {ms['profitFactor'] or '—'}），日均持股 {ms['avgHoldingCount']} 只（峰值 {ms['maxHoldingCount']} 只），平均持有 {ms['avgHoldDays']} 天，空仓日占 {ms['emptyDayRatioPct']}%，已实扣税费 ¥{ms['totalFees']:.0f} 及双边 0.15% 滑点。",
    ]

    caveats = [
        "严格采用「收盘出信号 → 次日开盘成交」因果回测（零未来函数），资金档位分仓、单票与四阶段总仓位硬上限、浮盈加仓、冲高减半50%、退潮压仓及单日回撤熔断全部写入代码强制执行；含 A 股 T+1、整手限制、开盘涨跌停不可成交约束及 0.03% 佣金 + 0.05% 印花税 + 0.15% 滑点。",
    ]
    if fallback_note:
        caveats.insert(0, fallback_note)
    if source_used == "bundled" and len(features_by_sym) < 40:
        caveats.insert(
            0,
            f"提示：当前区间内置样本仅有 {len(features_by_sym)} 只股票有日线覆盖（内置 544 股主要集中在 2022 下半年及 2026 年 3–11 月），可能导致信号偏少；建议在「设置 → 数据源」配置本地通达信日线后切换本地全市场池回测。",
        )
    elif source_used == "bundled":
        caveats.append(
            "使用内置 544 只历史实盘标的池回测时存在一定的样本选择偏差（标的均来自历届大佬关注池），用于验证『择时开关 + 止损纪律 vs 无纪律死扛』的路径有效性最可靠；配置本地通达信日线后可切换本地均衡沪深股票池进一步检验。"
        )
    else:
        caveats.append(
            "当前使用本地通达信沪深主板/创业板/科创板均衡采样池（已剔除北交所）及本地指数环境，结果不依赖内置样本切片。"
        )

    return {
        "kind": "masterBacktest",
        "styleKey": skey,
        "styleName": profile["name"],
        "config": cfg,
        "dateRange": {"startDate": all_bench_dates[0], "endDate": all_bench_dates[-1], "tradingDays": len(all_bench_dates)},
        "dataSource": source_used,
        "dataSourceLabel": source_label,
        "universeCount": len(features_by_sym),
        "params": params,
        "activeRules": describe_active_rules(skey, initial_capital, params),
        "ruleExecutionStats": rs,
        "finalHoldings": main_res["finalHoldings"],
        "finalCash": main_res["finalCash"],
        "finalCashPct": main_res["finalCashPct"],
        "positionTimeline": main_res["positionTimeline"],
        "nextDayOrders": main_res["nextDayOrders"],
        "summary": ms,
        "metrics": {
            "totalReturnPct": ms["totalReturnPct"],
            "maxDrawdownPct": ms["maxDrawdownPct"],
            "closedTradeCount": ms["closedEpisodes"],
            "tradeCount": ms["tradeCount"],
            "winRatePct": ms["winRatePct"],
            "annualizedReturnPct": ms["annualizedReturnPct"],
            "benchmarkReturnPct": bench_ret,
            "excessReturnPct": alpha_vs_bench,
        },
        "ablationSummary": as_,
        "benchmarkSummary": {"name": bench_name, "totalReturnPct": bench_ret, "maxDrawdownPct": round(b_max_dd, 2)},
        "proofMetrics": {
            "alphaVsBenchmarkPct": alpha_vs_bench,
            "alphaVsAblationPct": alpha_vs_ablation,
            "drawdownSavedPct": dd_saved_vs_ablation,
            "verdictPoints": verdict_points,
        },
        "positionCountComparison": position_count_comparison,
        "byStage": main_res["byStage"],
        "curve": combined_curve,
        "trades": main_res["trades"],
        "totalTrades": main_res["totalTrades"],
        "caveats": caveats,
    }


def simulate_single_stock_master(owner, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the exact hard-coded master rule engine on ONE stock to annotate its K-line chart."""
    payload = payload or {}
    market, code = tdx.symbol_parts(payload.get("symbol"))
    symbol = f"{market}{code}"
    raw_style = str(payload.get("styleKey") or "wujiang_dynamic").strip()
    skey = detect_style_key(None, raw_style)
    profile = STYLE_PROFILES[skey]
    params = _strategy_params(skey, payload)
    initial_capital = float(payload.get("initialCapital") or 100000.0)
    if not math.isfinite(initial_capital) or not (10000.0 <= initial_capital <= 5e8):
        initial_capital = 100000.0

    # Resolve local TDX root if configured
    tdx_root: Path | None = None
    stock_name = code
    if owner is not None and hasattr(owner, "_get"):
        chosen = owner._get("path")
        if chosen:
            try:
                tdx_root = tdx.normalize_directory(chosen)
            except Exception:
                tdx_root = None
        try:
            row = owner.db.execute("SELECT name FROM securities WHERE symbol=?", (symbol,)).fetchone()
            if row and row["name"]:
                stock_name = row["name"]
        except Exception:
            pass

    start_req = str(payload.get("startDate") or "").strip()
    end_req = str(payload.get("endDate") or "").strip()

    bars: list[dict[str, Any]] = []
    source_used = "tdx"
    if tdx_root is not None:
        p = tdx.daily_path(tdx_root, symbol)
        if p.is_file():
            if start_req and end_req:
                bars = _read_tdx_daily_window(p, start_req, end_req, warmup_bars=35)
            else:
                all_bars = tdx.parse_daily(tdx.read_stable(p))
                # Cover up to last 600 trading days (~2.5 years) + warmup so zooming K-line shows full history
                bars = all_bars[-635:] if len(all_bars) > 635 else all_bars

    if not bars:
        fb_bars = get_fallback_stock_bars(symbol)
        if fb_bars:
            bars = fb_bars
            source_used = "bundled"
            fb_names = get_fallback_names()
            if stock_name == code and symbol in fb_names:
                stock_name = fb_names[symbol]

    if len(bars) < 25:
        raise ValueError(f"{symbol} 可用日线不足 25 根，无法测算高手买卖点。")

    eff_end = _valid_iso_date(end_req, bars[-1]["date"]) if end_req else bars[-1]["date"]
    default_start = bars[max(0, len(bars) - 600)]["date"]
    eff_start = _valid_iso_date(start_req, default_start) if start_req else default_start
    if eff_start > eff_end:
        eff_start = bars[0]["date"]

    fmap = _prepare_stock_features(symbol, bars, start_date=eff_start, end_date=eff_end)
    if len(fmap) < 5:
        raise ValueError(f"{symbol} 在 {eff_start} 至 {eff_end} 有效交易日不足 5 天。")

    sim_dates = sorted(fmap.keys())
    env_by_date: dict[str, dict[str, Any]] = dict(build_fallback_market_env())
    if tdx_root is not None:
        tdx_env, _ = _build_tdx_market_env(tdx_root, sim_dates[0], sim_dates[-1])
        if tdx_env:
            env_by_date.update(tdx_env)

    res = _simulate(
        skey,
        params,
        sim_dates,
        {symbol: fmap},
        {symbol: stock_name},
        env_by_date,
        initial_capital,
        ablation_no_discipline=False,
    )

    return {
        "symbol": symbol,
        "code": code,
        "name": stock_name,
        "styleKey": skey,
        "styleName": profile["name"],
        "shortStyleName": profile["name"].split("·")[0].strip(),
        "dataSource": source_used,
        "dateRange": {"startDate": sim_dates[0], "endDate": sim_dates[-1], "tradingDays": len(sim_dates)},
        "activeRules": describe_active_rules(skey, initial_capital, params),
        "ruleExecutionStats": res["ruleExecutionStats"],
        "summary": res["summary"],
        "curve": res["curve"],
        "trades": res["trades"],
        "totalTrades": res["totalTrades"],
        "nextDayOrders": res["nextDayOrders"],
    }
