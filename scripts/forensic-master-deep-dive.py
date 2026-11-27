"""Ultra-deep forensic dissection of 乌江望月 (71/72/73届) and the other 5 masters.

Computes exact empirical evidence for:
1. Wujiang error-correction loop: losing-trade exit timing, cut vs average-down outcomes,
   post-big-loss next-1~3d capital & leverage adjustment.
2. Wujiang winning-trade scaling vs trimming: multi-leg add_win vs add_loss vs staged_sell
   conditions (intraday gain, ZT status, market index, volume) & outcomes.
3. Wujiang week-by-week & day-by-day rotation timeline from 20cm Tech -> 10cm Mainboard Theme Cores:
   exact entry dates, exit dates, contemporaneous index returns/MA20/market turnover, and
   post-exit 3d/5d stock performance!
4. Fill-by-fill anatomy of Wujiang's 8 most instructive campaigns (wins & losses).
5. All 6 masters' exact position-sizing, drawdown-throttle, timing rationale, and exit discipline
   metrics to drive both the deep playbooks and A-trade's Advisor + Backtest engines.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(r"h:\CODE\A-trade\artifacts\wechat-jiaogedan\analysis")
LEDGER_DIR = ROOT / "ledgers"
CACHE_DIR = ROOT / "market_cache"
OUT_PATH = ROOT / "forensic_deep_dive.json"


def sf(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def smean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 3) if xs else None


def smed(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 3) if xs else None


def load_market():
    indices = json.loads((CACHE_DIR / "indices_2022_2026.json").read_text(encoding="utf-8"))
    klines = json.loads((CACHE_DIR / "stock_klines.json").read_text(encoding="utf-8"))
    # Build daily market lookup
    idx_maps = {}
    idx_lists = {}
    for sym, obj in indices.items():
        bars = obj.get("bars") or []
        idx_lists[sym] = bars
        idx_maps[sym] = {b["date"]: (i, b) for i, b in enumerate(bars)}
    dates = [b["date"] for b in idx_lists["sh000001"]]
    date_pos = {d: i for i, d in enumerate(dates)}
    mkt = {}
    for i, d in enumerate(dates):
        sh_amt = idx_maps["sh000001"].get(d, (0, {}))[1].get("amount", 0.0)
        sz_amt = idx_maps["sz399001"].get(d, (0, {}))[1].get("amount", 0.0)
        rec = {"date": d, "turnover_yi": round((sh_amt + sz_amt) / 1e8, 1)}
        for sym in ("sh000001", "sz399006", "sh000852", "sh000688"):
            m = idx_maps[sym].get(d)
            if not m:
                continue
            pos, b = m
            bs = idx_lists[sym]
            ma20 = sum(x["close"] for x in bs[max(0, pos - 19):pos + 1]) / min(20, pos + 1)
            r5 = ((b["close"] / bs[pos - 5]["close"]) - 1) * 100.0 if pos >= 5 else 0.0
            r20 = ((b["close"] / bs[pos - 20]["close"]) - 1) * 100.0 if pos >= 20 else 0.0
            rec[f"{sym}_close"] = b["close"]
            rec[f"{sym}_pct"] = b["pct"]
            rec[f"{sym}_ma20"] = round(ma20, 2)
            rec[f"{sym}_above_ma20"] = b["close"] >= ma20
            rec[f"{sym}_ret5"] = round(r5, 2)
            rec[f"{sym}_ret20"] = round(r20, 2)
        mkt[d] = rec
    return mkt, date_pos, klines


def stock_bar_ctx(klines: dict, code: str, d: str) -> dict | None:
    bars = klines.get(code)
    if not bars:
        return None
    pos = next((i for i, b in enumerate(bars) if b["date"] == d), None)
    if pos is None:
        return None
    b = bars[pos]
    prev_close = b["close"] - b["chg"] if b["chg"] != 0 else (bars[pos - 1]["close"] if pos > 0 else b["open"])
    lim = 20.0 if code.startswith(("300", "301", "688", "689")) else (30.0 if code.startswith(("43", "83", "87", "88", "92")) else 10.0)
    is_zt = b["pct"] >= lim - 0.45 and abs(b["close"] - b["high"]) / max(b["high"], 0.01) < 0.005
    is_dt = b["pct"] <= -(lim - 0.45) and abs(b["close"] - b["low"]) / max(b["low"], 0.01) < 0.005
    prev_zt = 0
    for j in range(pos - 1, max(-1, pos - 15), -1):
        pb = bars[j]
        if pb["pct"] >= lim - 0.45 and abs(pb["close"] - pb["high"]) / max(pb["high"], 0.01) < 0.005:
            prev_zt += 1
        else:
            break
    ma5 = sum(x["close"] for x in bars[max(0, pos - 4):pos + 1]) / min(5, pos + 1)
    ma20 = sum(x["close"] for x in bars[max(0, pos - 19):pos + 1]) / min(20, pos + 1)
    # Post 1d, 3d, 5d close returns from today's close
    def post_ret(k):
        if pos + k >= len(bars):
            return None
        m = 1.0
        for step in range(1, k + 1):
            m *= 1.0 + bars[pos + step]["pct"] / 100.0
        return round((m - 1.0) * 100.0, 2)
    return {
        "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
        "pct": b["pct"], "prev_close": round(prev_close, 3), "lim": lim,
        "is_zt": is_zt, "is_dt": is_dt, "prev_zt": prev_zt,
        "ma5": round(ma5, 3), "ma20": round(ma20, 3),
        "turnover": b["turnover"], "amount_yi": round(b["amount"] / 1e8, 2),
        "post1d": post_ret(1), "post3d": post_ret(3), "post5d": post_ret(5),
        "next_open_pct": round((bars[pos + 1]["open"] / b["close"] - 1.0) * 100.0, 2) if pos + 1 < len(bars) and b["close"] > 0 else None,
        "next_high_pct": round((bars[pos + 1]["high"] / b["close"] - 1.0) * 100.0, 2) if pos + 1 < len(bars) and b["close"] > 0 else None,
        "next_low_pct": round((bars[pos + 1]["low"] / b["close"] - 1.0) * 100.0, 2) if pos + 1 < len(bars) and b["close"] > 0 else None,
    }


def time_slot(t: str) -> str:
    if not t:
        return "无时间"
    hhmm = t[:5]
    if hhmm <= "09:25":
        return "09:15-09:25 竞价"
    if hhmm <= "09:35":
        return "09:26-09:35 开盘前5分"
    if hhmm <= "09:45":
        return "09:36-09:45 开盘5-15分"
    if hhmm <= "10:30":
        return "09:46-10:30 早盘主攻"
    if hhmm <= "11:30":
        return "10:31-11:30 上午后段"
    if hhmm <= "14:00":
        return "13:00-14:00 午后初段"
    return "14:01-15:00 尾盘"


def analyze_trader_forensics(slug: str, files: list[str], mkt: dict, date_pos: dict, klines: dict) -> dict:
    rows = []
    for fn in files:
        rows.extend(list(csv.DictReader((LEDGER_DIR / fn).open(encoding="utf-8-sig"))))
    rows.sort(key=lambda r: (r["tradeDate"], r["tradeTime"] or "99:99:99", r["code"], 0 if r["side"] == "buy" else 1))

    # Reconstruct full leg-by-leg episodes
    by_sec = defaultdict(list)
    for i, r in enumerate(rows):
        by_sec[r["code"] or r["name"]].append((i, r))

    episodes = []
    for key, srows in by_sec.items():
        pos_qty = 0.0
        cost = 0.0
        ep = None
        for _, r in srows:
            side = r["side"]
            qty = sf(r["quantity"])
            amt = sf(r["amount"])
            px = sf(r["price"])
            d = r["tradeDate"]
            tm = r.get("tradeTime") or ""
            if qty <= 0 or amt <= 0:
                continue
            if side == "buy":
                if pos_qty <= 0:
                    ep = {
                        "code": r["code"], "name": r["name"], "board": r.get("board", ""),
                        "entry_date": d, "exit_date": None, "legs": [],
                        "buy_amt": 0.0, "sell_amt": 0.0, "buy_qty": 0.0, "sell_qty": 0.0,
                        "add_win_legs": 0, "add_loss_legs": 0, "same_day_add_legs": 0,
                        "next_day_add_win": 0, "next_day_add_loss": 0,
                        "sell_days": set(), "buy_days": set(),
                        "margin_amt": 0.0, "peak_cost": 0.0,
                    }
                    leg_role = "open"
                else:
                    avg_c = cost / pos_qty
                    if d == ep["entry_date"]:
                        leg_role = "same_day_add"
                        ep["same_day_add_legs"] += 1
                    elif px >= avg_c * 1.005:
                        leg_role = "next_day_add_win"
                        ep["add_win_legs"] += 1
                        ep["next_day_add_win"] += 1
                    elif px <= avg_c * 0.995:
                        leg_role = "next_day_add_loss"
                        ep["add_loss_legs"] += 1
                        ep["next_day_add_loss"] += 1
                    else:
                        leg_role = "next_day_add_flat"
                pos_qty += qty
                cost += amt
                ep["buy_amt"] += amt
                ep["buy_qty"] += qty
                ep["peak_cost"] = max(ep["peak_cost"], cost)
                ep["buy_days"].add(d)
                if "融资" in (r.get("brokerAction") or ""):
                    ep["margin_amt"] += amt
                bctx = stock_bar_ctx(klines, r["code"], d) if r["code"] else None
                ep["legs"].append({
                    "date": d, "time": tm, "side": "buy", "role": leg_role,
                    "qty": qty, "price": px, "amount_wan": round(amt / 1e4, 2),
                    "action": r.get("brokerAction", ""),
                    "vs_prev_pct": round((px / bctx["prev_close"] - 1) * 100, 2) if bctx and bctx["prev_close"] > 0 else None,
                    "day_pct": bctx["pct"] if bctx else None,
                    "is_zt": bctx["is_zt"] if bctx else None,
                    "prev_zt": bctx["prev_zt"] if bctx else None,
                    "mkt_zz1000_pct": mkt.get(d, {}).get("sh000852_pct"),
                    "mkt_kc50_pct": mkt.get(d, {}).get("sh000688_pct"),
                    "mkt_to_yi": mkt.get(d, {}).get("turnover_yi"),
                })
            else:
                if pos_qty <= 0 or ep is None:
                    continue
                if qty > pos_qty * 1.03:
                    pos_qty = 0.0
                    cost = 0.0
                    ep = None
                    continue
                avg_c = cost / pos_qty
                sell_ret_pct = round((px / avg_c - 1.0) * 100.0, 2) if avg_c > 0 else 0.0
                pos_qty -= qty
                cost = max(0.0, cost - avg_c * min(qty, pos_qty + qty))
                ep["sell_amt"] += amt
                ep["sell_qty"] += qty
                ep["sell_days"].add(d)
                bctx = stock_bar_ctx(klines, r["code"], d) if r["code"] else None
                is_final = pos_qty <= max(10.0, ep["buy_qty"] * 0.015)
                ep["legs"].append({
                    "date": d, "time": tm, "side": "sell",
                    "role": "final_exit" if is_final else "partial_trim",
                    "qty": qty, "price": px, "amount_wan": round(amt / 1e4, 2),
                    "vs_cost_pct": sell_ret_pct,
                    "vs_prev_pct": round((px / bctx["prev_close"] - 1) * 100, 2) if bctx and bctx["prev_close"] > 0 else None,
                    "day_pct": bctx["pct"] if bctx else None,
                    "post3d_after_exit": bctx["post3d"] if bctx else None,
                    "post5d_after_exit": bctx["post5d"] if bctx else None,
                    "mkt_zz1000_pct": mkt.get(d, {}).get("sh000852_pct"),
                })
                if is_final:
                    if abs(ep["sell_qty"] - ep["buy_qty"]) / max(ep["buy_qty"], 1.0) <= 0.015:
                        ep["exit_date"] = d
                        ep["pnl"] = round(ep["sell_amt"] - ep["buy_amt"], 2)
                        ep["ret_pct"] = round((ep["pnl"] / ep["buy_amt"]) * 100.0, 2) if ep["buy_amt"] > 0 else 0.0
                        ep["hold_tdays"] = max(0, date_pos.get(d, 0) - date_pos.get(ep["entry_date"], 0))
                        ep["buy_days_cnt"] = len(ep["buy_days"])
                        ep["sell_days_cnt"] = len(ep["sell_days"])
                        del ep["buy_days"], ep["sell_days"]
                        episodes.append(ep)
                    pos_qty = 0.0
                    cost = 0.0
                    ep = None

    # 1. Error-correction stats (losing episodes)
    wins = [e for e in episodes if e["pnl"] > 0]
    losses = [e for e in episodes if e["pnl"] <= 0]

    loss_exit_slots = Counter()
    win_exit_slots = Counter()
    loss_hold_dist = Counter()
    win_hold_dist = Counter()
    loss_post3d = []
    win_post3d = []

    for e in losses:
        sells = [l for l in e["legs"] if l["side"] == "sell"]
        if sells:
            loss_exit_slots[time_slot(sells[0]["time"])] += 1
            if sells[-1].get("post3d_after_exit") is not None:
                loss_post3d.append(sells[-1]["post3d_after_exit"])
        loss_hold_dist[f"{min(e['hold_tdays'], 4)}d+"] += 1

    for e in wins:
        sells = [l for l in e["legs"] if l["side"] == "sell"]
        if sells:
            win_exit_slots[time_slot(sells[0]["time"])] += 1
            if sells[-1].get("post3d_after_exit") is not None:
                win_post3d.append(sells[-1]["post3d_after_exit"])
        win_hold_dist[f"{min(e['hold_tdays'], 4)}d+"] += 1

    # 2. Multi-day scaling (next-day add_win vs add_loss vs single-day buy)
    ep_add_win = [e for e in episodes if e["next_day_add_win"] > 0]
    ep_add_loss = [e for e in episodes if e["next_day_add_loss"] > 0]
    ep_staged_sell = [e for e in episodes if e["sell_days_cnt"] >= 2]
    ep_single_sell = [e for e in episodes if e["sell_days_cnt"] == 1]

    def ep_stats(es):
        if not es:
            return {"count": 0}
        w = [x for x in es if x["pnl"] > 0]
        return {
            "count": len(es),
            "win_rate_pct": round(len(w) / len(es) * 100.0, 1),
            "avg_ret_pct": smean([x["ret_pct"] for x in es]),
            "med_ret_pct": smed([x["ret_pct"] for x in es]),
            "total_pnl_wan": round(sum(x["pnl"] for x in es) / 1e4, 1),
            "avg_hold_days": smean([x["hold_tdays"] for x in es]),
        }

    # 3. Post-big-loss next-day sizing/behavior (after episodes losing > 3% or > 50万)
    daily_buy = defaultdict(float)
    daily_margin = defaultdict(float)
    daily_stocks = defaultdict(set)
    for r in rows:
        if r["side"] == "buy":
            daily_buy[r["tradeDate"]] += sf(r["amount"])
            if "融资" in (r.get("brokerAction") or ""):
                daily_margin[r["tradeDate"]] += sf(r["amount"])
            daily_stocks[r["tradeDate"]].add(r["code"] or r["name"])

    # Compute sequential episode post-loss size change
    eps_chrono = sorted(episodes, key=lambda e: (e["exit_date"], e["entry_date"]))
    after_loss_sizes, after_win_sizes = [], []
    for i in range(1, len(eps_chrono)):
        prev_e, cur_e = eps_chrono[i - 1], eps_chrono[i]
        if prev_e["pnl"] < 0:
            after_loss_sizes.append(cur_e["buy_amt"] / 1e4)
        else:
            after_win_sizes.append(cur_e["buy_amt"] / 1e4)

    # 4. Losing trades where entry day did NOT close ZT vs DID close ZT
    entry_zt_eps = [e for e in episodes if e["legs"][0].get("is_zt") is True]
    entry_nozt_eps = [e for e in episodes if e["legs"][0].get("is_zt") is False]

    # 5. Daily consecutive-loss streak analysis
    max_consec_loss = 0
    cur_l = 0
    for e in eps_chrono:
        if e["pnl"] <= 0:
            cur_l += 1
            max_consec_loss = max(max_consec_loss, cur_l)
        else:
            cur_l = 0

    return {
        "slug": slug,
        "total_clean_episodes": len(episodes),
        "win_count": len(wins),
        "loss_count": len(losses),
        "max_consec_loss_episodes": max_consec_loss,
        "loss_exit_timing": dict(loss_exit_slots),
        "win_exit_timing": dict(win_exit_slots),
        "loss_hold_dist": dict(loss_hold_dist),
        "win_hold_dist": dict(win_hold_dist),
        "avg_post3d_after_loss_exit": smean(loss_post3d),
        "loss_exit_avoided_further_drop_pct": round(sum(1 for x in loss_post3d if x < 0) / max(1, len(loss_post3d)) * 100.0, 1) if loss_post3d else None,
        "avg_post3d_after_win_exit": smean(win_post3d),
        "win_exit_near_peak_pct": round(sum(1 for x in win_post3d if x < 0) / max(1, len(win_post3d)) * 100.0, 1) if win_post3d else None,
        "scaling_behavior": {
            "next_day_add_on_win": ep_stats(ep_add_win),
            "next_day_add_on_loss": ep_stats(ep_add_loss),
            "staged_multi_day_sell": ep_stats(ep_staged_sell),
            "single_day_clear_sell": ep_stats(ep_single_sell),
            "entry_day_closed_zt": ep_stats(entry_zt_eps),
            "entry_day_not_zt": ep_stats(entry_nozt_eps),
        },
        "after_loss_vs_win_next_size_wan": {
            "after_loss_avg": smean(after_loss_sizes),
            "after_loss_med": smed(after_loss_sizes),
            "after_win_avg": smean(after_win_sizes),
            "after_win_med": smed(after_win_sizes),
        },
        "episodes_raw": episodes,
    }


def analyze_wujiang_rotation_and_campaigns(wj_forensics: dict, mkt: dict, klines: dict) -> dict:
    """Build week-by-week rotation timeline (Tech 20cm -> Mainboard Theme Cores) & full campaign logs."""
    rows = []
    for fn in ("wujiang_71_enriched.csv", "wujiang_72_enriched.csv", "wujiang_73_enriched.csv"):
        rows.extend(list(csv.DictReader((LEDGER_DIR / fn).open(encoding="utf-8-sig"))))
    rows.sort(key=lambda r: (r["tradeDate"], r["tradeTime"] or ""))

    # Group by ISO week (YYYY-Www)
    weeks = defaultdict(lambda: {
        "dates": set(), "total_buy": 0.0, "margin_buy": 0.0,
        "kc_buy": 0.0, "cyb_buy": 0.0, "mb_buy": 0.0, "mb_zt_buy": 0.0,
        "top_stocks": defaultdict(float), "sessions": set(),
    })
    for r in rows:
        if r["side"] != "buy":
            continue
        d = r["tradeDate"]
        import datetime as dt
        iso = dt.date.fromisoformat(d).isocalendar()
        wk = f"{iso.year}-W{iso.week:02d}"
        w = weeks[wk]
        w["dates"].add(d)
        w["sessions"].add(r["session"])
        amt = sf(r["amount"])
        w["total_buy"] += amt
        if "融资" in (r.get("brokerAction") or ""):
            w["margin_buy"] += amt
        b = r.get("board", "")
        if "科创" in b:
            w["kc_buy"] += amt
        elif "创业" in b:
            w["cyb_buy"] += amt
        elif "主板" in b:
            w["mb_buy"] += amt
            bctx = stock_bar_ctx(klines, r["code"], d) if r["code"] else None
            if bctx and (bctx["prev_zt"] >= 1 or bctx["is_zt"]):
                w["mb_zt_buy"] += amt
        w["top_stocks"][f"{r['name']}({r['code']})"] += amt

    weekly_timeline = []
    for wk in sorted(weeks.keys()):
        w = weeks[wk]
        ds = sorted(w["dates"])
        d0, d1 = ds[0], ds[-1]
        # Market performance in that week
        sh_pcts = [mkt[d]["sh000001_pct"] for d in ds if d in mkt]
        kc_pcts = [mkt[d]["sh000688_pct"] for d in ds if d in mkt]
        zz_pcts = [mkt[d]["sh000852_pct"] for d in ds if d in mkt]
        tos = [mkt[d]["turnover_yi"] for d in ds if d in mkt]
        tot = max(w["total_buy"], 1.0)
        top3 = sorted(w["top_stocks"].items(), key=lambda x: -x[1])[:3]
        weekly_timeline.append({
            "week": wk,
            "date_range": f"{d0}..{d1}",
            "session": sorted(w["sessions"])[-1],
            "avg_turnover_yi": round(statistics.mean(tos), 0) if tos else 0,
            "sh_sum_pct": round(sum(sh_pcts), 2),
            "kc50_sum_pct": round(sum(kc_pcts), 2),
            "zz1000_sum_pct": round(sum(zz_pcts), 2),
            "kc50_above_ma20_end": mkt.get(d1, {}).get("sh000688_above_ma20"),
            "zz1000_above_ma20_end": mkt.get(d1, {}).get("sh000852_above_ma20"),
            "total_buy_wan": round(w["total_buy"] / 1e4, 1),
            "margin_pct": round(w["margin_buy"] / tot * 100.0, 1),
            "kc_pct": round(w["kc_buy"] / tot * 100.0, 1),
            "cyb_pct": round(w["cyb_buy"] / tot * 100.0, 1),
            "mb_pct": round(w["mb_buy"] / tot * 100.0, 1),
            "mb_zt_theme_pct": round(w["mb_zt_buy"] / tot * 100.0, 1),
            "top3_bought": [f"{k}:{v/1e4:.0f}万" for k, v in top3],
        })

    # Signature campaigns fill-by-fill (including wins, losses, and mainboard theme pivots)
    target_codes = {
        "688797": "臻宝科技(71届科创次新暴利)",
        "688825": "长鑫科技(72届硬科技滚仓)",
        "002185": "华天科技(72届科技退潮止损)",
        "002086": "东方海洋(73届主板题材总龙)",
        "002242": "九阳股份(73届主板题材双波)",
        "002343": "慈文传媒(73届传媒主线)",
        "600825": "新华传媒(73届传媒抱团)",
        "002827": "高争民爆(73届最大单笔止损纠错)",
    }
    campaigns = {}
    for code, label in target_codes.items():
        eps = [e for e in wj_forensics["episodes_raw"] if e["code"] == code]
        # Also raw fills in case of boundary carryovers (e.g., 东方海洋 / 新华传媒)
        c_fills = [r for r in rows if r["code"] == code]
        by_day_fills = defaultdict(lambda: {"buy_qty": 0, "buy_amt": 0.0, "sell_qty": 0, "sell_amt": 0.0, "margin_amt": 0.0, "times": []})
        for r in c_fills:
            dd = by_day_fills[r["tradeDate"]]
            q, a = sf(r["quantity"]), sf(r["amount"])
            if r["side"] == "buy":
                dd["buy_qty"] += q
                dd["buy_amt"] += a
                if "融资" in (r.get("brokerAction") or ""):
                    dd["margin_amt"] += a
            else:
                dd["sell_qty"] += q
                dd["sell_amt"] += a
            dd["times"].append(f"{'B' if r['side']=='buy' else 'S'}@{r['tradeTime'][:5]}:{r['price']}({a/1e4:.0f}万)")
        daily_log = []
        for d in sorted(by_day_fills.keys()):
            dd = by_day_fills[d]
            bctx = stock_bar_ctx(klines, code, d)
            m = mkt.get(d, {})
            daily_log.append({
                "date": d,
                "buy_wan": round(dd["buy_amt"] / 1e4, 1),
                "sell_wan": round(dd["sell_amt"] / 1e4, 1),
                "margin_wan": round(dd["margin_amt"] / 1e4, 1),
                "buy_avg_px": round(dd["buy_amt"] / dd["buy_qty"], 3) if dd["buy_qty"] else None,
                "sell_avg_px": round(dd["sell_amt"] / dd["sell_qty"], 3) if dd["sell_qty"] else None,
                "stock_close": bctx["close"] if bctx else None,
                "stock_pct": bctx["pct"] if bctx else None,
                "stock_is_zt": bctx["is_zt"] if bctx else None,
                "stock_prev_zt": bctx["prev_zt"] if bctx else None,
                "post3d_from_close": bctx["post3d"] if bctx else None,
                "mkt_sh_pct": m.get("sh000001_pct"),
                "mkt_zz1000_pct": m.get("sh000852_pct"),
                "mkt_kc50_pct": m.get("sh000688_pct"),
                "mkt_to_yi": m.get("turnover_yi"),
                "fills_sample": dd["times"][:6],
            })
        campaigns[label] = {
            "code": code,
            "clean_episodes": [
                {"entry": e["entry_date"], "exit": e["exit_date"], "hold": e["hold_tdays"],
                 "buy_wan": round(e["buy_amt"]/1e4, 1), "pnl_wan": round(e["pnl"]/1e4, 1),
                 "ret_pct": e["ret_pct"], "add_win": e["next_day_add_win"], "add_loss": e["next_day_add_loss"]}
                for e in eps
            ],
            "daily_log": daily_log,
        }

    # Post-big-loss next-day reaction trace (top 8 losing episodes)
    big_losses = sorted([e for e in wj_forensics["episodes_raw"] if e["pnl"] < -800000], key=lambda x: x["pnl"])[:8]
    daily_totals = defaultdict(lambda: {"buy": 0.0, "margin": 0.0, "stocks": []})
    for r in rows:
        if r["side"] == "buy":
            daily_totals[r["tradeDate"]]["buy"] += sf(r["amount"])
            if "融资" in (r.get("brokerAction") or ""):
                daily_totals[r["tradeDate"]]["margin"] += sf(r["amount"])
            daily_totals[r["tradeDate"]]["stocks"].append(r["name"])
    all_trade_days = sorted(daily_totals.keys())
    post_loss_reactions = []
    for e in big_losses:
        ex_d = e["exit_date"]
        sells = [l for l in e["legs"] if l["side"] == "sell"]
        first_sell_t = sells[0]["time"] if sells else ""
        # Find next active day after exit_date
        idx = all_trade_days.index(ex_d) if ex_d in all_trade_days else -1
        same_day = daily_totals.get(ex_d, {"buy": 0, "margin": 0, "stocks": []})
        next_d = all_trade_days[idx + 1] if 0 <= idx + 1 < len(all_trade_days) else None
        nxt = daily_totals.get(next_d, {"buy": 0, "margin": 0, "stocks": []}) if next_d else {"buy": 0, "margin": 0, "stocks": []}
        post_loss_reactions.append({
            "stock": f"{e['name']}({e['code']})",
            "entry": e["entry_date"], "exit": ex_d, "hold_days": e["hold_tdays"],
            "pnl_wan": round(e["pnl"] / 1e4, 1), "ret_pct": e["ret_pct"],
            "first_sell_time": first_sell_t,
            "post3d_after_cut": sells[-1].get("post3d_after_exit") if sells else None,
            "same_day_new_buy_wan": round(same_day["buy"] / 1e4, 1),
            "same_day_margin_pct": round(same_day["margin"] / max(same_day["buy"], 1) * 100, 1),
            "next_day": next_d,
            "next_day_buy_wan": round(nxt["buy"] / 1e4, 1),
            "next_day_margin_pct": round(nxt["margin"] / max(nxt["buy"], 1) * 100, 1),
            "next_day_top_stocks": [k for k, _ in Counter(nxt["stocks"]).most_common(4)],
        })

    return {
        "weekly_rotation_timeline": weekly_timeline,
        "signature_campaigns": campaigns,
        "post_big_loss_reactions": post_loss_reactions,
    }


def main():
    mkt, date_pos, klines = load_market()
    jobs = {
        "wujiang_all": ["wujiang_71_enriched.csv", "wujiang_72_enriched.csv", "wujiang_73_enriched.csv"],
        "huichanglan_73": ["huichanglan_73_enriched.csv"],
        "xiwangjiejie_73": ["xiwangjiejie_73_enriched.csv"],
        "dongshizhang_72": ["dongshizhang_72_enriched.csv"],
        "ruhan_52": ["ruhan_52_enriched.csv"],
        "songsong_50": ["songsong_50_enriched.csv"],
    }
    out = {"traders": {}, "wujiang_deep": {}}
    wj_raw = None
    for slug, files in jobs.items():
        res = analyze_trader_forensics(slug, files, mkt, date_pos, klines)
        if slug == "wujiang_all":
            wj_raw = res
        clean = {k: v for k, v in res.items() if k != "episodes_raw"}
        out["traders"][slug] = clean
        sc = clean["scaling_behavior"]
        print(f"\n=== {slug} (clean={clean['total_clean_episodes']}, W={clean['win_count']}/L={clean['loss_count']}, maxConsecL={clean['max_consec_loss_episodes']}) ===")
        print(f"  LOSS EXIT TIME : {clean['loss_exit_timing']}")
        print(f"  WIN  EXIT TIME : {clean['win_exit_timing']}")
        print(f"  LOSS HOLD DIST : {clean['loss_hold_dist']} | post3d={clean['avg_post3d_after_loss_exit']}% (avoided drop={clean['loss_exit_avoided_further_drop_pct']}%)")
        print(f"  WIN  HOLD DIST : {clean['win_hold_dist']} | post3d={clean['avg_post3d_after_win_exit']}% (near peak={clean['win_exit_near_peak_pct']}%)")
        print(f"  SCALING: addWin={sc['next_day_add_on_win']} | addLoss={sc['next_day_add_on_loss']}")
        print(f"  SELL STYLE: staged={sc['staged_multi_day_sell']} | singleDay={sc['single_day_clear_sell']}")
        print(f"  ENTRY ZT: closedZT={sc['entry_day_closed_zt']} | notZT={sc['entry_day_not_zt']}")
        print(f"  AFTER LOSS SIZE: {clean['after_loss_vs_win_next_size_wan']}")

    wj_deep = analyze_wujiang_rotation_and_campaigns(wj_raw, mkt, klines)
    out["wujiang_deep"] = wj_deep

    print("\n=== WUJIANG WEEK-BY-WEEK ROTATION (2026-05 -> 2026-11) ===")
    for w in wj_deep["weekly_rotation_timeline"]:
        print(
            f"  {w['week']} ({w['date_range']}) | TO={w['avg_turnover_yi']:5.0f}亿 | KC50={w['kc50_sum_pct']:+6.2f}%(>{'Y' if w['kc50_above_ma20_end'] else 'N'}) ZZ1k={w['zz1000_sum_pct']:+6.2f}% "
            f"| Buy={w['total_buy_wan']:6.0f}万 Mgn={w['margin_pct']:4.1f}% | 科创={w['kc_pct']:4.1f}% 创业={w['cyb_pct']:4.1f}% 主板={w['mb_pct']:4.1f}%(连板题材={w['mb_zt_theme_pct']:4.1f}%) "
            f"| {', '.join(w['top3_bought'])}"
        )

    print("\n=== WUJIANG POST-BIG-LOSS ERROR CORRECTION REACTIONS ===")
    for r in wj_deep["post_big_loss_reactions"]:
        print(f"  {r['stock']} ({r['entry']}->{r['exit']}, {r['hold_days']}d) {r['pnl_wan']:+.1f}万({r['ret_pct']:+.1f}%) cut@{r['first_sell_time']} post3d={r['post3d_after_cut']}% | sameDayBuy={r['same_day_new_buy_wan']}万(mgn {r['same_day_margin_pct']}%) -> nxtDay({r['next_day']})={r['next_day_buy_wan']}万(mgn {r['next_day_margin_pct']}%) {r['next_day_top_stocks']}")

    print("\n=== WUJIANG SIGNATURE CAMPAIGNS DAILY LOG ===")
    for lbl, c in wj_deep["signature_campaigns"].items():
        print(f"\n  [{lbl}] clean_eps={c['clean_episodes']}")
        for dl in c["daily_log"]:
            print(f"    {dl['date']} | B={dl['buy_wan']:6.1f}万(mgn {dl['margin_wan']:5.1f}万 @{dl['buy_avg_px']}) S={dl['sell_wan']:6.1f}万(@{dl['sell_avg_px']}) | stk={dl['stock_pct']}% ZT={dl['stock_is_zt']} prevZT={dl['stock_prev_zt']} post3d={dl['post3d_from_close']}% | SH={dl['mkt_sh_pct']}% ZZ1k={dl['mkt_zz1000_pct']}% KC50={dl['mkt_kc50_pct']}% TO={dl['mkt_to_yi']}亿")

    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {OUT_PATH}")


if __name__ == "__main__":
    main()
