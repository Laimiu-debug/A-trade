"""Quantitative ledger & A-share market-regime alignment analyzer for 顽主杯 master traders.

Reads enriched CSV ledgers + real historical K-lines (2022-2026) from market_cache/,
computes verified statistics per session and per trader, and outputs:
  artifacts/wechat-jiaogedan/analysis/trader_quant_metrics.json
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(r"h:\CODE\A-trade\artifacts\wechat-jiaogedan\analysis")
LEDGER_DIR = ROOT / "ledgers"
CACHE_DIR = ROOT / "market_cache"
OUT_JSON = ROOT / "trader_quant_metrics.json"


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def safe_mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 3) if xs else None


def safe_median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 3) if xs else None


def time_bucket(t: str) -> str:
    if not t or len(t) < 5:
        return "无时间戳"
    hhmm = t[:5]
    if hhmm <= "09:25":
        return "09:15-09:25 集合竞价"
    if hhmm <= "09:45":
        return "09:26-09:45 开盘抢筹"
    if hhmm <= "10:30":
        return "09:46-10:30 早盘主攻"
    if hhmm <= "11:30":
        return "10:31-11:30 上午后段"
    if hhmm <= "14:00":
        return "13:00-14:00 午后初段"
    if hhmm <= "15:00":
        return "14:01-15:00 尾盘博弈"
    return "15:00+ 盘后/隔夜排单"


def limit_up_pct(code: str, name: str) -> float:
    if "ST" in (name or "").upper():
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    if code.startswith(("11", "12")):
        return 20.0
    return 10.0


def build_market_regime_table(indices_data: dict) -> tuple[dict[str, dict], list[str]]:
    """Build daily market context indexed by ISO date using sh000001, sz399001, sz399006, sh000852, sh000688."""
    idx_maps: dict[str, dict[str, dict]] = {}
    idx_lists: dict[str, list[dict]] = {}
    for sym, obj in indices_data.items():
        bars = obj.get("bars") or []
        idx_lists[sym] = bars
        idx_maps[sym] = {b["date"]: (i, b) for i, b in enumerate(bars)}

    dates = [b["date"] for b in idx_lists.get("sh000001", [])]
    date_to_pos = {d: i for i, d in enumerate(dates)}

    # Precompute total market turnover (SH + SZ) in 亿元
    total_turnover: list[float] = []
    for d in dates:
        sh_amt = idx_maps.get("sh000001", {}).get(d, (0, {}))[1].get("amount", 0.0)
        sz_amt = idx_maps.get("sz399001", {}).get(d, (0, {}))[1].get("amount", 0.0)
        total_turnover.append((sh_amt + sz_amt) / 1e8)

    regime: dict[str, dict] = {}
    for i, d in enumerate(dates):
        rec: dict[str, Any] = {"date": d, "turnover_yi": round(total_turnover[i], 1)}
        if i >= 19:
            avg20_to = sum(total_turnover[i - 19 : i + 1]) / 20.0
            rec["turnover_ma20_yi"] = round(avg20_to, 1)
            rec["turnover_ratio"] = round(total_turnover[i] / avg20_to, 3) if avg20_to > 0 else 1.0
        else:
            rec["turnover_ma20_yi"] = rec["turnover_yi"]
            rec["turnover_ratio"] = 1.0

        for sym in ("sh000001", "sz399006", "sh000852", "sh000688"):
            m = idx_maps.get(sym, {}).get(d)
            if not m:
                continue
            pos, bar = m
            bars = idx_lists[sym]
            c = bar["close"]
            ma20 = sum(x["close"] for x in bars[max(0, pos - 19) : pos + 1]) / min(20, pos + 1)
            ret5 = ((c / bars[pos - 5]["close"]) - 1) * 100.0 if pos >= 5 else 0.0
            ret20 = ((c / bars[pos - 20]["close"]) - 1) * 100.0 if pos >= 20 else 0.0
            rec[f"{sym}_close"] = c
            rec[f"{sym}_pct"] = bar["pct"]
            rec[f"{sym}_ma20"] = round(ma20, 2)
            rec[f"{sym}_above_ma20"] = c >= ma20
            rec[f"{sym}_ret5"] = round(ret5, 2)
            rec[f"{sym}_ret20"] = round(ret20, 2)

        # Primary retail/active-money regime using 中证1000 (fallback 上证指数)
        r20 = rec.get("sh000852_ret20", rec.get("sh000001_ret20", 0.0))
        abv = rec.get("sh000852_above_ma20", rec.get("sh000001_above_ma20", False))
        day_pct = rec.get("sh000852_pct", rec.get("sh000001_pct", 0.0))
        if abv and r20 >= 3.0:
            trend = "强主升(中证1000>MA20且20日≥+3%)"
        elif (not abv) and r20 <= -3.0:
            trend = "弱势退潮(中证1000<MA20且20日≤-3%)"
        else:
            trend = "震荡分化"

        if day_pct >= 0.4:
            tape = "阳线日(≥+0.4%)"
        elif day_pct <= -0.4:
            tape = "阴线日(≤-0.4%)"
        else:
            tape = "平盘震荡"

        rec["trend_regime"] = trend
        rec["day_tape"] = tape
        rec["trade_idx"] = i
        regime[d] = rec

    return regime, dates


def summarize_market_window(
    indices_data: dict, regime: dict[str, dict], start_date: str, end_date: str
) -> dict:
    window_dates = [d for d in sorted(regime.keys()) if start_date <= d <= end_date]
    if not window_dates:
        return {}
    out: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "market_trading_days": len(window_dates),
        "avg_daily_turnover_yi": round(
            statistics.mean(regime[d]["turnover_yi"] for d in window_dates), 1
        ),
        "min_daily_turnover_yi": round(min(regime[d]["turnover_yi"] for d in window_dates), 1),
        "max_daily_turnover_yi": round(max(regime[d]["turnover_yi"] for d in window_dates), 1),
        "regime_day_counts": dict(Counter(regime[d]["trend_regime"] for d in window_dates)),
        "indices": {},
    }
    for sym, obj in indices_data.items():
        bars = [b for b in obj.get("bars", []) if start_date <= b["date"] <= end_date]
        if len(bars) < 2:
            continue
        # Peak-to-trough max drawdown
        peak = bars[0]["close"]
        max_dd = 0.0
        for b in bars:
            if b["close"] > peak:
                peak = b["close"]
            dd = (b["close"] / peak - 1.0) * 100.0
            if dd < max_dd:
                max_dd = dd
        pct_total = (bars[-1]["close"] / bars[0]["open"] - 1.0) * 100.0
        up_days = sum(1 for b in bars if b["pct"] > 0)
        out["indices"][sym] = {
            "name": obj["name"],
            "start_close": bars[0]["close"],
            "end_close": bars[-1]["close"],
            "period_return_pct": round(pct_total, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "up_day_ratio_pct": round(up_days / len(bars) * 100.0, 1),
        }
    return out


def analyze_stock_entry_context(
    code: str, name: str, trade_date: str, trade_price: float, stock_klines: dict[str, list[dict]]
) -> dict | None:
    bars = stock_klines.get(code)
    if not bars:
        return None
    pos = None
    for i, b in enumerate(bars):
        if b["date"] == trade_date:
            pos = i
            break
    if pos is None:
        return None
    b = bars[pos]
    # Validate price within day's range (tolerance 4%)
    if b["low"] <= 0 or b["high"] <= 0 or not (b["low"] * 0.96 <= trade_price <= b["high"] * 1.04):
        return None

    # Exact ex-dividend previous close
    prev_close = b["close"] - b["chg"] if b["chg"] != 0 else (bars[pos - 1]["close"] if pos > 0 else b["open"])
    if prev_close <= 0:
        prev_close = b["open"]

    lim = limit_up_pct(code, name)
    entry_pct = (trade_price / prev_close - 1.0) * 100.0
    is_cb = code.startswith(("11", "12"))
    is_etf = code.startswith(("51", "56", "58", "15", "16"))

    # Check if new IPO (first 5 trading days or name starts with N/C or huge move)
    is_ipo = (pos < 5 and b["amp"] > 22) or name.startswith(("N", "C")) or entry_pct > 35.0

    # Prior consecutive limit-up days
    prev_consec_zt = 0
    if not is_cb and not is_etf:
        for j in range(pos - 1, max(-1, pos - 15), -1):
            pb = bars[j]
            if pb["pct"] >= lim - 0.45 and abs(pb["close"] - pb["high"]) / max(pb["high"], 0.01) < 0.005:
                prev_consec_zt += 1
            else:
                break

    # Did stock close at limit-up on entry day?
    closed_zt = (not is_cb and not is_etf) and (
        b["pct"] >= lim - 0.45 and abs(b["close"] - b["high"]) / max(b["high"], 0.01) < 0.005
    )

    # Entry tactic classification
    if is_cb:
        if entry_pct >= 12.0:
            tactic = "转债强势追涨(≥+12%)"
        elif entry_pct >= 3.0:
            tactic = "转债顺势半路(+3%~12%)"
        elif entry_pct >= -1.5:
            tactic = "转债平盘套利"
        else:
            tactic = "转债水下低吸(<-1.5%)"
    elif is_etf:
        tactic = "ETF波段"
    elif is_ipo:
        tactic = "新股/次新博弈"
    elif entry_pct >= lim - 0.45:
        if prev_consec_zt == 0:
            tactic = "首板打板"
        elif prev_consec_zt == 1:
            tactic = "一进二打板"
        else:
            tactic = f"高标连板打板({prev_consec_zt}板+)"
    elif entry_pct >= (7.5 if lim >= 20 else 4.5):
        tactic = "强势半路追涨"
    elif entry_pct >= 1.5:
        tactic = "红盘低位半路"
    elif entry_pct >= -1.5:
        tactic = "平盘附近承接"
    else:
        tactic = "水下低吸/抄底"

    # Pre-entry momentum
    ret_5d = ((bars[pos - 1]["close"] / bars[pos - 6]["close"]) - 1.0) * 100.0 if pos >= 6 else None
    ret_20d = ((bars[pos - 1]["close"] / bars[pos - 21]["close"]) - 1.0) * 100.0 if pos >= 21 else None
    ma20 = sum(x["close"] for x in bars[max(0, pos - 20) : pos]) / min(20, pos) if pos > 0 else b["open"]
    above_ma20 = (bars[pos - 1]["close"] >= ma20) if pos > 0 else True

    # Forward returns from actual fill price (using chained pct_chg after T+0 close to stay ex-div safe!)
    intraday_to_close = (b["close"] / trade_price - 1.0) * 100.0
    t1_open_ret = None
    t1_high_ret = None
    t1_close_ret = None
    t3_close_ret = None
    t5_close_ret = None

    if pos + 1 < len(bars):
        b1 = bars[pos + 1]
        # Only use raw open/high ratio if no split/dividend jump between b and b1
        expected_prev1 = b1["close"] - b1["chg"]
        ratio_adj = (expected_prev1 / b["close"]) if (b["close"] > 0 and expected_prev1 > 0) else 1.0
        # If no ex-div (ratio_adj ~ 1.0), direct unadjusted ratio is exact
        if 0.98 <= ratio_adj <= 1.02:
            t1_open_ret = (b1["open"] / trade_price - 1.0) * 100.0
            t1_high_ret = (b1["high"] / trade_price - 1.0) * 100.0
            t1_close_ret = (b1["close"] / trade_price - 1.0) * 100.0
        else:
            t1_close_ret = ((1 + intraday_to_close / 100.0) * (1 + b1["pct"] / 100.0) - 1.0) * 100.0

    # Chain pct_chg for T+3 and T+5
    def chained_ret(days_ahead: int) -> float | None:
        if pos + days_ahead >= len(bars):
            return None
        mult = 1.0 + intraday_to_close / 100.0
        for k in range(1, days_ahead + 1):
            mult *= 1.0 + bars[pos + k]["pct"] / 100.0
        return (mult - 1.0) * 100.0

    t3_close_ret = chained_ret(3)
    t5_close_ret = chained_ret(5)

    return {
        "entry_pct": round(entry_pct, 2),
        "tactic": tactic,
        "prev_consec_zt": prev_consec_zt,
        "closed_zt": closed_zt,
        "is_ipo": is_ipo,
        "ret_5d": round(ret_5d, 2) if ret_5d is not None else None,
        "ret_20d": round(ret_20d, 2) if ret_20d is not None else None,
        "above_ma20": above_ma20,
        "intraday_to_close_ret": round(intraday_to_close, 2),
        "t1_open_ret": round(t1_open_ret, 2) if t1_open_ret is not None else None,
        "t1_high_ret": round(t1_high_ret, 2) if t1_high_ret is not None else None,
        "t1_close_ret": round(t1_close_ret, 2) if t1_close_ret is not None else None,
        "t3_close_ret": round(t3_close_ret, 2) if t3_close_ret is not None else None,
        "t5_close_ret": round(t5_close_ret, 2) if t5_close_ret is not None else None,
        "day_close_pct": b["pct"],
        "day_turnover": b["turnover"],
    }


def reconstruct_episodes(rows: list[dict], dates_order: dict[str, int]) -> dict:
    """Reconstruct clean flat-to-flat round-trip episodes per security without fabricating carry-over PnL."""
    by_sec: dict[str, list[dict]] = defaultdict(list)
    for idx, r in enumerate(rows):
        key = r["code"] if r.get("code") else r["name"]
        by_sec[key].append((idx, r))

    clean_episodes: list[dict] = []
    carryover_sells = 0
    open_end_positions = 0
    partial_mismatch_closed = 0

    for key, sec_rows in by_sec.items():
        # Sort chronologically (within same date & time, preserve order; if no time, buys before sells on same day? No, keep slip chronological order!)
        sec_rows.sort(key=lambda x: (x[1]["tradeDate"], x[1]["tradeTime"] or "99:99:99", x[0]))
        pos_qty = 0.0
        buy_amt = 0.0
        sell_amt = 0.0
        peak_cost = 0.0
        buy_qty_total = 0.0
        sell_qty_total = 0.0
        buy_fills = 0
        sell_fills = 0
        entry_date = None
        last_date = None
        sec_name = sec_rows[-1][1]["name"]
        sec_code = sec_rows[-1][1]["code"]
        sec_board = sec_rows[-1][1].get("board", "")
        has_margin = False

        for _, r in sec_rows:
            side = r["side"]
            qty = safe_float(r["quantity"])
            amt = safe_float(r["amount"])
            d = r["tradeDate"]
            if qty <= 0 or amt <= 0:
                continue

            if side == "buy":
                if pos_qty <= 0:
                    # Start new episode
                    entry_date = d
                    buy_amt = 0.0
                    sell_amt = 0.0
                    peak_cost = 0.0
                    buy_qty_total = 0.0
                    sell_qty_total = 0.0
                    buy_fills = 0
                    sell_fills = 0
                    has_margin = False
                pos_qty += qty
                buy_amt += amt
                buy_qty_total += qty
                buy_fills += 1
                peak_cost = max(peak_cost, buy_amt - sell_amt)
                last_date = d
                if "融资" in (r.get("brokerAction") or ""):
                    has_margin = True
            elif side == "sell":
                if pos_qty <= 0:
                    # Selling pre-contest carry-over position
                    carryover_sells += 1
                    continue
                if qty > pos_qty * 1.03:
                    # Selling more than bought in-contest => mixed with carry-over inventory; split cleanly or skip
                    carryover_sells += 1
                    # Close only the matched portion? Better: skip contaminating PnL and reset to 0
                    pos_qty = 0.0
                    partial_mismatch_closed += 1
                    continue
                pos_qty -= qty
                sell_amt += amt
                sell_qty_total += qty
                sell_fills += 1
                last_date = d

                # Check if flat (within 1.5% residual due to tiny OCR/dividend rounding)
                if pos_qty <= max(10.0, buy_qty_total * 0.015):
                    # Adjust tiny residual if exact qty matches closely
                    if abs(sell_qty_total - buy_qty_total) / max(buy_qty_total, 1.0) <= 0.015:
                        pnl = sell_amt - buy_amt
                        # Return on total capital deployed (peak_cost or buy_amt)
                        ret_pct = (pnl / buy_amt) * 100.0 if buy_amt > 0 else 0.0
                        # Trading days held
                        i0 = dates_order.get(entry_date, 0)
                        i1 = dates_order.get(last_date, i0)
                        hold_tdays = max(0, i1 - i0)
                        clean_episodes.append({
                            "code": sec_code,
                            "name": sec_name,
                            "board": sec_board,
                            "entry_date": entry_date,
                            "exit_date": last_date,
                            "hold_tdays": hold_tdays,
                            "buy_fills": buy_fills,
                            "sell_fills": sell_fills,
                            "buy_amount": round(buy_amt, 2),
                            "sell_amount": round(sell_amt, 2),
                            "pnl": round(pnl, 2),
                            "return_pct": round(ret_pct, 2),
                            "used_margin": has_margin,
                        })
                    pos_qty = 0.0

        if pos_qty > 0:
            open_end_positions += 1

    return {
        "episodes": clean_episodes,
        "carryover_sells_skipped": carryover_sells,
        "partial_mismatch_skipped": partial_mismatch_closed,
        "open_end_positions": open_end_positions,
    }


def analyze_job(
    job_key: str,
    rows: list[dict],
    indices_data: dict,
    regime: dict[str, dict],
    dates_order: dict[str, int],
    stock_klines: dict[str, list[dict]],
) -> dict:
    if not rows:
        return {}
    trader = rows[0]["trader"]
    session_label = rows[0]["session"]
    dates = sorted({r["tradeDate"] for r in rows if r.get("tradeDate")})
    start_date, end_date = dates[0], dates[-1]
    mkt_win = summarize_market_window(indices_data, regime, start_date, end_date)
    mkt_days = mkt_win.get("market_trading_days", len(dates))

    buys = [r for r in rows if r["side"] == "buy"]
    sells = [r for r in rows if r["side"] == "sell"]
    buy_amt_total = sum(safe_float(r["amount"]) for r in buys)
    sell_amt_total = sum(safe_float(r["amount"]) for r in sells)
    buy_days = sorted({r["tradeDate"] for r in buys})

    # Board mix (by buy amount and count)
    board_amt = defaultdict(float)
    board_cnt = Counter()
    for r in buys:
        b = r.get("board") or "未知"
        board_amt[b] += safe_float(r["amount"])
        board_cnt[b] += 1
    board_mix = {
        b: {
            "buy_fills": board_cnt[b],
            "buy_amount_wan": round(board_amt[b] / 1e4, 1),
            "buy_amount_pct": round(board_amt[b] / max(buy_amt_total, 1.0) * 100.0, 1),
        }
        for b in sorted(board_amt, key=lambda x: -board_amt[x])
    }

    # Margin usage
    margin_buys = [r for r in buys if "融资" in (r.get("brokerAction") or "")]
    margin_buy_amt = sum(safe_float(r["amount"]) for r in margin_buys)

    # Intraday timing distribution
    buy_time_cnt = Counter(time_bucket(r.get("tradeTime", "")) for r in buys)
    sell_time_cnt = Counter(time_bucket(r.get("tradeTime", "")) for r in sells)
    buy_time_amt = defaultdict(float)
    for r in buys:
        buy_time_amt[time_bucket(r.get("tradeTime", ""))] += safe_float(r["amount"])

    # Stock-level buy concentration
    stock_buy_amt = defaultdict(float)
    stock_buy_cnt = Counter()
    stock_label = {}
    for r in buys:
        k = r["code"] or r["name"]
        stock_buy_amt[k] += safe_float(r["amount"])
        stock_buy_cnt[k] += 1
        stock_label[k] = f"{r['name']}({r['code']})" if r.get("code") else r["name"]
    top_bought = sorted(stock_buy_amt.items(), key=lambda x: -x[1])[:12]

    # Daily buy outlay stats (for active days vs all market days)
    daily_buy = defaultdict(float)
    daily_fills = Counter()
    daily_stocks = defaultdict(set)
    for r in buys:
        d = r["tradeDate"]
        daily_buy[d] += safe_float(r["amount"])
        daily_fills[d] += 1
        daily_stocks[d].add(r["code"] or r["name"])

    # Entry technical structure (aligned to real K-lines)
    entry_ctxs: list[tuple[dict, dict]] = []
    for r in buys:
        code = r.get("code") or ""
        if not code:
            continue
        ctx = analyze_stock_entry_context(
            code, r["name"], r["tradeDate"], safe_float(r["price"]), stock_klines
        )
        if ctx:
            entry_ctxs.append((r, ctx))

    tactic_cnt = Counter(ctx["tactic"] for _, ctx in entry_ctxs)
    tactic_amt = defaultdict(float)
    tactic_t1 = defaultdict(list)
    for r, ctx in entry_ctxs:
        t = ctx["tactic"]
        tactic_amt[t] += safe_float(r["amount"])
        if ctx["t1_close_ret"] is not None:
            tactic_t1[t].append(ctx["t1_close_ret"])
    ctx_buy_amt_total = sum(safe_float(r["amount"]) for r, _ in entry_ctxs) or 1.0
    tactic_summary = {
        t: {
            "fills": tactic_cnt[t],
            "fills_pct": round(tactic_cnt[t] / max(1, len(entry_ctxs)) * 100.0, 1),
            "buy_amt_pct": round(tactic_amt[t] / ctx_buy_amt_total * 100.0, 1),
            "avg_t1_close_ret": safe_mean(tactic_t1[t]),
            "t1_win_rate": round(sum(1 for x in tactic_t1[t] if x > 0) / len(tactic_t1[t]) * 100.0, 1) if tactic_t1[t] else None,
        }
        for t in sorted(tactic_cnt, key=lambda x: -tactic_cnt[x])
    }

    # Prior consecutive ZT distribution (for non-CB/non-ETF entries)
    stock_ctxs = [(r, ctx) for r, ctx in entry_ctxs if not r["code"].startswith(("11", "12", "51", "56", "58", "15", "16"))]
    consec_zt_dist = Counter(
        "首板/非连板(0板)" if ctx["prev_consec_zt"] == 0
        else ("1板进2" if ctx["prev_consec_zt"] == 1
        else ("2板进3" if ctx["prev_consec_zt"] == 2 else "3板+高标"))
        for _, ctx in stock_ctxs
    )

    # Reconstruct clean round-trip episodes
    ep_res = reconstruct_episodes(rows, dates_order)
    episodes = ep_res["episodes"]
    wins = [e for e in episodes if e["pnl"] > 0]
    losses = [e for e in episodes if e["pnl"] <= 0]
    total_ep_pnl = sum(e["pnl"] for e in episodes)
    gross_win = sum(e["pnl"] for e in wins)
    gross_loss = sum(e["pnl"] for e in losses)
    avg_win_pct = safe_mean([e["return_pct"] for e in wins])
    avg_loss_pct = safe_mean([e["return_pct"] for e in losses])
    payoff_ratio = (
        round(avg_win_pct / abs(avg_loss_pct), 2)
        if (avg_win_pct is not None and avg_loss_pct is not None and avg_loss_pct != 0)
        else None
    )
    profit_factor = round(gross_win / abs(gross_loss), 2) if gross_loss < 0 else None

    hold_days = [e["hold_tdays"] for e in episodes]
    t0_cnt = sum(1 for h in hold_days if h == 0)
    d1_cnt = sum(1 for h in hold_days if h == 1)
    d2_3_cnt = sum(1 for h in hold_days if 2 <= h <= 3)
    d4p_cnt = sum(1 for h in hold_days if h >= 4)

    # Market-regime conditioning across ALL market trading days in the window
    win_dates = [d for d in sorted(regime.keys()) if start_date <= d <= end_date]
    regime_stats: dict[str, dict] = {}
    for axis_name, axis_key in (("by_trend_regime", "trend_regime"), ("by_day_tape", "day_tape")):
        buckets = defaultdict(lambda: {"mkt_days": 0, "buy_days": 0, "buy_amt": 0.0, "buy_fills": 0, "t1_rets": [], "ep_rets": [], "ep_wins": 0, "ep_total": 0})
        for d in win_dates:
            bname = regime[d][axis_key]
            buckets[bname]["mkt_days"] += 1
            if daily_buy.get(d, 0.0) > 0:
                buckets[bname]["buy_days"] += 1
                buckets[bname]["buy_amt"] += daily_buy[d]
                buckets[bname]["buy_fills"] += daily_fills[d]
        for r, ctx in entry_ctxs:
            d = r["tradeDate"]
            if d in regime and ctx["t1_close_ret"] is not None:
                buckets[regime[d][axis_key]]["t1_rets"].append(ctx["t1_close_ret"])
        for e in episodes:
            d = e["entry_date"]
            if d in regime:
                b = buckets[regime[d][axis_key]]
                b["ep_total"] += 1
                b["ep_rets"].append(e["return_pct"])
                if e["pnl"] > 0:
                    b["ep_wins"] += 1
        regime_stats[axis_name] = {
            k: {
                "market_days": v["mkt_days"],
                "active_buy_days": v["buy_days"],
                "participation_rate_pct": round(v["buy_days"] / max(1, v["mkt_days"]) * 100.0, 1),
                "avg_daily_buy_wan": round((v["buy_amt"] / max(1, v["mkt_days"])) / 1e4, 1),
                "avg_active_day_buy_wan": round((v["buy_amt"] / max(1, v["buy_days"])) / 1e4, 1) if v["buy_days"] else 0.0,
                "entry_t1_avg_ret_pct": safe_mean(v["t1_rets"]),
                "episode_count": v["ep_total"],
                "episode_win_rate_pct": round(v["ep_wins"] / v["ep_total"] * 100.0, 1) if v["ep_total"] else None,
                "episode_avg_ret_pct": safe_mean(v["ep_rets"]),
            }
            for k, v in buckets.items()
        }

    # Enrich top winning/losing episodes with market index context on entry_date
    def annotate_ep(e: dict) -> dict:
        m = regime.get(e["entry_date"], {})
        return {
            **e,
            "entry_mkt_regime": m.get("trend_regime", ""),
            "entry_sh_pct": m.get("sh000001_pct"),
            "entry_zz1000_pct": m.get("sh000852_pct"),
            "entry_turnover_yi": m.get("turnover_yi"),
        }

    top_wins = [annotate_ep(e) for e in sorted(episodes, key=lambda x: -x["pnl"])[:10]]
    top_losses = [annotate_ep(e) for e in sorted(episodes, key=lambda x: x["pnl"])[:8]]

    # Per-stock aggregated PnL across clean episodes (shows true profit concentration!)
    stock_pnl = defaultdict(float)
    stock_ep_cnt = Counter()
    stock_ep_buy = defaultdict(float)
    for e in episodes:
        lbl = f"{e['name']}({e['code']})" if e["code"] else e["name"]
        stock_pnl[lbl] += e["pnl"]
        stock_ep_cnt[lbl] += 1
        stock_ep_buy[lbl] += e["buy_amount"]
    top_pnl_stocks = [
        {"stock": k, "episodes": stock_ep_cnt[k], "buy_wan": round(stock_ep_buy[k] / 1e4, 1), "pnl_wan": round(v / 1e4, 1)}
        for k, v in sorted(stock_pnl.items(), key=lambda x: -x[1])[:10]
    ]
    worst_pnl_stocks = [
        {"stock": k, "episodes": stock_ep_cnt[k], "buy_wan": round(stock_ep_buy[k] / 1e4, 1), "pnl_wan": round(v / 1e4, 1)}
        for k, v in sorted(stock_pnl.items(), key=lambda x: x[1])[:8]
    ]

    return {
        "job_key": job_key,
        "trader": trader,
        "session": session_label,
        "market_window": mkt_win,
        "overview": {
            "total_fills": len(rows),
            "buy_fills": len(buys),
            "sell_fills": len(sells),
            "verified_fills": sum(1 for r in rows if r.get("verified") == "True"),
            "mapped_fills": sum(1 for r in rows if r.get("code")),
            "kline_aligned_buys": len(entry_ctxs),
            "unique_securities": len({r["code"] or r["name"] for r in rows}),
            "active_trade_days": len(dates),
            "active_buy_days": len(buy_days),
            "market_trading_days": mkt_days,
            "zero_buy_days": max(0, mkt_days - len(buy_days)),
            "zero_buy_day_ratio_pct": round(max(0, mkt_days - len(buy_days)) / max(1, mkt_days) * 100.0, 1),
            "avg_fills_per_active_day": round(len(rows) / max(1, len(dates)), 1),
            "avg_unique_stocks_bought_per_buy_day": round(
                statistics.mean(len(v) for v in daily_stocks.values()), 2
            ) if daily_stocks else 0.0,
            "total_buy_wan": round(buy_amt_total / 1e4, 1),
            "total_sell_wan": round(sell_amt_total / 1e4, 1),
            "total_turnover_yi": round((buy_amt_total + sell_amt_total) / 1e8, 3),
            "median_single_buy_wan": round(statistics.median([safe_float(r["amount"]) for r in buys]) / 1e4, 1) if buys else 0.0,
            "max_single_buy_wan": round(max((safe_float(r["amount"]) for r in buys), default=0.0) / 1e4, 1),
            "margin_buy_fills": len(margin_buys),
            "margin_buy_amount_wan": round(margin_buy_amt / 1e4, 1),
            "margin_buy_ratio_pct": round(margin_buy_amt / max(buy_amt_total, 1.0) * 100.0, 1),
        },
        "board_mix": board_mix,
        "timing": {
            "buy_count": dict(buy_time_cnt),
            "buy_amount_pct": {
                k: round(v / max(buy_amt_total, 1.0) * 100.0, 1) for k, v in buy_time_amt.items()
            },
            "sell_count": dict(sell_time_cnt),
        },
        "entry_structure": {
            "aligned_buy_count": len(entry_ctxs),
            "tactics": tactic_summary,
            "stock_consec_zt_dist": dict(consec_zt_dist),
            "avg_entry_pct": safe_mean([ctx["entry_pct"] for _, ctx in entry_ctxs]),
            "median_entry_pct": safe_median([ctx["entry_pct"] for _, ctx in entry_ctxs]),
            "above_ma20_ratio_pct": round(
                sum(1 for _, ctx in stock_ctxs if ctx["above_ma20"]) / max(1, len(stock_ctxs)) * 100.0, 1
            ) if stock_ctxs else None,
            "avg_pre5d_ret_pct": safe_mean([ctx["ret_5d"] for _, ctx in stock_ctxs if ctx["ret_5d"] is not None]),
            "avg_pre20d_ret_pct": safe_mean([ctx["ret_20d"] for _, ctx in stock_ctxs if ctx["ret_20d"] is not None]),
            "entry_day_closed_zt_pct": round(
                sum(1 for _, ctx in stock_ctxs if ctx["closed_zt"]) / max(1, len(stock_ctxs)) * 100.0, 1
            ) if stock_ctxs else None,
            "forward_returns": {
                "intraday_to_close_avg": safe_mean([ctx["intraday_to_close_ret"] for _, ctx in entry_ctxs]),
                "t1_open_avg": safe_mean([ctx["t1_open_ret"] for _, ctx in entry_ctxs if ctx["t1_open_ret"] is not None]),
                "t1_high_avg": safe_mean([ctx["t1_high_ret"] for _, ctx in entry_ctxs if ctx["t1_high_ret"] is not None]),
                "t1_close_avg": safe_mean([ctx["t1_close_ret"] for _, ctx in entry_ctxs if ctx["t1_close_ret"] is not None]),
                "t3_close_avg": safe_mean([ctx["t3_close_ret"] for _, ctx in entry_ctxs if ctx["t3_close_ret"] is not None]),
                "t5_close_avg": safe_mean([ctx["t5_close_ret"] for _, ctx in entry_ctxs if ctx["t5_close_ret"] is not None]),
                "t1_close_win_rate_pct": round(
                    sum(1 for _, ctx in entry_ctxs if ctx["t1_close_ret"] is not None and ctx["t1_close_ret"] > 0)
                    / max(1, sum(1 for _, ctx in entry_ctxs if ctx["t1_close_ret"] is not None)) * 100.0,
                    1,
                ),
            },
        },
        "episodes": {
            "clean_count": len(episodes),
            "carryover_sells_skipped": ep_res["carryover_sells_skipped"],
            "partial_mismatch_skipped": ep_res["partial_mismatch_skipped"],
            "open_end_positions": ep_res["open_end_positions"],
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate_pct": round(len(wins) / max(1, len(episodes)) * 100.0, 1),
            "avg_return_pct": safe_mean([e["return_pct"] for e in episodes]),
            "median_return_pct": safe_median([e["return_pct"] for e in episodes]),
            "avg_win_pct": avg_win_pct,
            "avg_loss_pct": avg_loss_pct,
            "payoff_ratio": payoff_ratio,
            "profit_factor": profit_factor,
            "total_clean_pnl_wan": round(total_ep_pnl / 1e4, 1),
            "gross_win_wan": round(gross_win / 1e4, 1),
            "gross_loss_wan": round(gross_loss / 1e4, 1),
            "avg_hold_tdays": safe_mean(hold_days),
            "median_hold_tdays": safe_median(hold_days),
            "hold_dist_pct": {
                "T+0_same_day": round(t0_cnt / max(1, len(episodes)) * 100.0, 1),
                "1_trading_day": round(d1_cnt / max(1, len(episodes)) * 100.0, 1),
                "2_3_trading_days": round(d2_3_cnt / max(1, len(episodes)) * 100.0, 1),
                "4plus_trading_days": round(d4p_cnt / max(1, len(episodes)) * 100.0, 1),
            },
            "top_10_wins": top_wins,
            "top_8_losses": top_losses,
            "top_pnl_stocks": top_pnl_stocks,
            "worst_pnl_stocks": worst_pnl_stocks,
        },
        "top_bought_stocks": [
            {"stock": stock_label[k], "buy_fills": stock_buy_cnt[k], "buy_wan": round(v / 1e4, 1), "share_pct": round(v / max(buy_amt_total, 1.0) * 100.0, 1)}
            for k, v in top_bought
        ],
        "regime_conditioning": regime_stats,
    }


def analyze_dongshizhang_orders(orders_path: Path) -> dict:
    if not orders_path.exists():
        return {}
    rows = list(csv.DictReader(orders_path.open(encoding="utf-8-sig")))
    status_cnt = Counter(r.get("orderStatus", "") for r in rows)
    side_status = Counter((r.get("side", ""), r.get("orderStatus", "")) for r in rows)
    buy_orders = [r for r in rows if r.get("side") == "buy"]
    overnight_buys = [r for r in buy_orders if (r.get("tradeTime") or "") >= "15:05" or (r.get("tradeTime") or "") < "09:15"]
    auction_buys = [r for r in buy_orders if "09:15" <= (r.get("tradeTime") or "") <= "09:25:59"]
    intraday_buys = [r for r in buy_orders if "09:26" <= (r.get("tradeTime") or "") <= "15:00:59"]

    def summarize_bucket(bs: list[dict]) -> dict:
        filled = sum(1 for r in bs if "已成" in r.get("orderStatus", "") and "撤" not in r.get("orderStatus", ""))
        partial = sum(1 for r in bs if "部成" in r.get("orderStatus", "") or "部撤" in r.get("orderStatus", ""))
        cancelled = sum(1 for r in bs if ("已撤" in r.get("orderStatus", "") or r.get("isCancelled") == "True") and "部成" not in r.get("orderStatus", "") and "部撤" not in r.get("orderStatus", ""))
        return {"total": len(bs), "full_fill": filled, "partial_cancel": partial, "full_cancel": cancelled,
                "cancel_rate_pct": round((cancelled + partial) / max(1, len(bs)) * 100.0, 1)}

    top_queued = Counter(r.get("name", "") for r in buy_orders).most_common(15)
    overnight_stocks = Counter(r.get("name", "") for r in overnight_buys).most_common(15)
    return {
        "total_orders": len(rows),
        "buy_orders_total": len(buy_orders),
        "sell_orders_total": len(rows) - len(buy_orders),
        "status_counts": dict(status_cnt),
        "side_status": {f"{k[0]}_{k[1]}": v for k, v in side_status.items()},
        "buy_order_timing": {
            "overnight_queue": summarize_bucket(overnight_buys),
            "auction_0915_0925": summarize_bucket(auction_buys),
            "intraday_0926_1500": summarize_bucket(intraday_buys),
        },
        "top_15_queued_buy_stocks": [{"name": k, "orders": v} for k, v in top_queued],
        "top_15_overnight_queued_stocks": [{"name": k, "orders": v} for k, v in overnight_stocks],
    }


def main():
    indices_data = json.loads((CACHE_DIR / "indices_2022_2026.json").read_text(encoding="utf-8"))
    stock_klines = json.loads((CACHE_DIR / "stock_klines.json").read_text(encoding="utf-8"))
    regime, all_dates = build_market_regime_table(indices_data)
    dates_order = {d: i for i, d in enumerate(all_dates)}

    enriched_files = sorted(LEDGER_DIR.glob("*_enriched.csv"))
    print(f"[ANALYZE] Processing {len(enriched_files)} enriched ledgers...")

    results: dict[str, Any] = {"jobs": {}, "combined_wujiang": {}, "dongshizhang_orders": {}}
    wujiang_all_rows: list[dict] = []

    for p in enriched_files:
        job_key = p.name.replace("_enriched.csv", "")
        rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
        res = analyze_job(job_key, rows, indices_data, regime, dates_order, stock_klines)
        results["jobs"][job_key] = res
        if job_key.startswith("wujiang_"):
            wujiang_all_rows.extend(rows)

        ov = res["overview"]
        ep = res["episodes"]
        en = res["entry_structure"]
        mw = res["market_window"]
        sh_ret = mw.get("indices", {}).get("sh000001", {}).get("period_return_pct")
        zz_ret = mw.get("indices", {}).get("sh000852", {}).get("period_return_pct")
        print(
            f"  {job_key:16s} | fills={ov['total_fills']:4d} | uniq={ov['unique_securities']:3d} "
            f"| zeroBuy={ov['zero_buy_day_ratio_pct']:4.1f}% | turnover={ov['total_turnover_yi']:5.2f}亿 "
            f"| margin={ov['margin_buy_ratio_pct']:4.1f}% | ep={ep['clean_count']:3d} "
            f"| WR={ep['win_rate_pct']:4.1f}% | PF={str(ep['profit_factor']):>5s} "
            f"| hold={str(ep['avg_hold_tdays']):>4s}d | T1={str(en['forward_returns']['t1_close_avg']):>5s}% "
            f"| SH={sh_ret}% ZZ1000={zz_ret}%"
        )

    if wujiang_all_rows:
        wujiang_all_rows.sort(key=lambda x: (x["tradeDate"], x["tradeTime"], x["code"]))
        results["combined_wujiang"] = analyze_job(
            "wujiang_71_72_73_all", wujiang_all_rows, indices_data, regime, dates_order, stock_klines
        )

    dz_orders = LEDGER_DIR / "dongshizhang_72_all_orders.csv"
    if dz_orders.exists():
        results["dongshizhang_orders"] = analyze_dongshizhang_orders(dz_orders)
        print(f"  [ORDERS] dongshizhang_72: {results['dongshizhang_orders']['total_orders']} total entrust orders")

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SAVED] {OUT_JSON}")


if __name__ == "__main__":
    main()
