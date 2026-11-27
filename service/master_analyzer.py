"""Quantitative reverse-engineering engine for master-trader delivery slips.

Aligns every trade and idle day in a master's database against local Tongdaxin
daily bars, optional 1-minute bars, market breadth, emotion cycle, leadership
ladder, and same-day cross-section cohorts to explain:
  1. Why those stocks were bought under that day's market environment
  2. How position sizing, scaling, adding/reducing, and risk contraction work
  3. How take-profit, stop-loss, and exit discipline create the equity curve
  4. Both full-spectrum reconstruction and a non-realtime executable playbook
"""
from __future__ import annotations

import datetime as dt
import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from service import tdx
from service.emotion_cycle import STAGES, cycle_timeline
from service.intraday import MissingIntraday, read_intraday
from service.leader_research import Leadership, limit_flags
from service.market_research import board_of, read_raw_tail, rows_from_raw

VERSION = "master-analyzer-v1"
MAX_MARKET_WINDOW_DAYS = 460
WARMUP_DAYS = 75
BENCHMARK_SYMBOLS = ("sh000001", "sz399006")
BOARD_LABELS = {
    "main": "主板",
    "chinext": "创业板",
    "star": "科创板",
    "bse": "北交所",
    "index": "指数",
}
ENTRY_PATTERN_LABELS = {
    "relay_board": "连板接力",
    "first_board_breakout": "首板打板/放量突破",
    "rebound_wrap": "断板反包/弱转强",
    "trend_pullback": "强势趋势回踩低吸",
    "oversold_rebound": "低位/超跌反弹",
    "other": "其他结构",
}
EXIT_PATTERN_LABELS = {
    "next_day_cut": "次日果断止损/核出",
    "fast_stop_ma5": "短期破MA5/弱势止损",
    "deep_stop": "深幅回撤止损",
    "board_break_profit": "断板/冲高止盈",
    "staged_take_profit": "分批兑现止盈",
    "trend_profit": "趋势波段止盈",
    "time_flat_exit": "横盘换股/微盈亏了结",
    "open": "当前持仓未平",
}


def _r(value, digits: int = 2):
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _pct(num, den, digits: int = 2):
    if num is None or den is None or den == 0:
        return None
    return _r((num / den) * 100.0, digits)


def _safe_mean(values, digits: int = 2):
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return _r(mean(clean), digits) if clean else None


def _safe_median(values, digits: int = 2):
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return _r(median(clean), digits) if clean else None


def _percentile(values, p: float, digits: int = 2):
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean:
        return None
    idx = (len(clean) - 1) * max(0.0, min(1.0, p))
    lo = int(math.floor(idx))
    hi = min(len(clean) - 1, lo + 1)
    frac = idx - lo
    return _r(clean[lo] * (1.0 - frac) + clean[hi] * frac, digits)


def _session_bucket(trade_time: str) -> str:
    if not trade_time:
        return "unknown"
    hhmm = trade_time[:5]
    if hhmm <= "09:25":
        return "auction"
    if hhmm <= "09:45":
        return "open_rush"
    if hhmm <= "10:30":
        return "morning_attack"
    if hhmm <= "11:30":
        return "mid_morning"
    if hhmm <= "14:00":
        return "early_afternoon"
    return "late_close"


SESSION_LABELS = {
    "auction": "集合竞价 (≤09:25)",
    "open_rush": "开盘抢筹 (09:26-09:45)",
    "morning_attack": "早盘主攻 (09:46-10:30)",
    "mid_morning": "上午后段 (10:31-11:30)",
    "early_afternoon": "午后博弈 (13:00-14:00)",
    "late_close": "尾盘偷袭/确认 (14:01-15:00)",
    "unknown": "未含精确时间",
}


def _nominal_limit_pct(board: str, name: str, day: str) -> float:
    st = "ST" in (name or "").upper()
    if board == "bse":
        return 30.0
    if board == "star" or (board == "chinext" and day >= "2020-08-24"):
        return 20.0
    if st and (board != "main" or day < "2026-07-06"):
        return 5.0
    return 10.0


def _load_stock_daily(root: Path | None, symbol: str) -> list[dict]:
    if not symbol:
        return []
    if root is not None:
        try:
            path = tdx.daily_path(root, symbol)
            if path.is_file():
                bars = tdx.parse_daily(tdx.read_stable(path))
                if bars:
                    return bars
        except Exception:
            pass
    try:
        from service.master_fallback_market import get_fallback_index_bars, get_fallback_stock_bars
        bars = get_fallback_stock_bars(symbol)
        if not bars:
            bars = get_fallback_index_bars(symbol)
        return bars
    except Exception:
        return []


def _enrich_bar_indicators(bars: list[dict], symbol: str, name: str) -> tuple[dict[str, dict], list[str]]:
    """Precompute causal daily indicators for a traded stock."""
    if not bars:
        return {}, []
    board = board_of(symbol, "stock")
    dates = [b["date"] for b in bars]
    by_date: dict[str, dict] = {}
    tuples = [(b["date"], b["close"], b["volume"], b["amount"]) for b in bars]
    board_height = 0
    recent_limit_ups: list[int] = []

    for i, bar in enumerate(bars):
        prev = bars[i - 1] if i > 0 else None
        prev_tuple = tuples[i - 1] if i > 0 else None
        cur_tuple = tuples[i]
        is_up, is_down, observable = limit_flags(prev_tuple, cur_tuple, board, name)
        prior_height = board_height
        if bar["volume"] <= 0:
            board_height = 0
        elif is_up:
            board_height += 1
            recent_limit_ups.append(i)
        else:
            board_height = 0

        def ma(period: int, end_idx: int):
            if end_idx + 1 < period:
                return None
            window = bars[end_idx + 1 - period:end_idx + 1]
            return mean(x["close"] for x in window)

        def ret(period: int, idx: int):
            if idx < period or bars[idx - period]["close"] <= 0:
                return None
            return (bars[idx]["close"] / bars[idx - period]["close"] - 1.0) * 100.0

        def prev_high(period: int, idx: int):
            if idx < 1:
                return None
            start = max(0, idx - period)
            return max(x["high"] for x in bars[start:idx])

        ma5 = ma(5, i)
        ma10 = ma(10, i)
        ma20 = ma(20, i)
        ma60 = ma(60, i)
        prev_ma5 = ma(5, i - 1) if i > 0 else None
        prev_ma10 = ma(10, i - 1) if i > 0 else None
        prev_ma20 = ma(20, i - 1) if i > 0 else None
        prev_ma60 = ma(60, i - 1) if i > 0 else None

        vol5_prev = mean(x["volume"] for x in bars[i - 5:i]) if i >= 5 else None
        amt5_prev = mean(x["amount"] for x in bars[i - 5:i]) if i >= 5 else None
        vol_ratio5 = (bar["volume"] / vol5_prev) if vol5_prev and vol5_prev > 0 else None

        change_pct = ((bar["close"] / prev["close"] - 1.0) * 100.0) if prev and prev["close"] > 0 else None
        open_gap_pct = ((bar["open"] / prev["close"] - 1.0) * 100.0) if prev and prev["close"] > 0 else None
        high20_prev = prev_high(20, i)
        high60_prev = prev_high(60, i)
        prev_close = prev["close"] if prev else None
        dd20_before = ((prev_close / high20_prev - 1.0) * 100.0) if prev_close and high20_prev and high20_prev > 0 else None

        # Check whether stock had a limit-up in the prior 2..6 trading days and broke before today
        had_recent_lu_broken = False
        if prior_height == 0:
            for lu_idx in reversed(recent_limit_ups):
                gap = i - lu_idx
                if 2 <= gap <= 6:
                    had_recent_lu_broken = True
                    break
                if gap > 6:
                    break

        post3 = ((bars[i + 3]["close"] / bar["close"] - 1.0) * 100.0) if i + 3 < len(bars) and bar["close"] > 0 else None
        post5 = ((bars[i + 5]["close"] / bar["close"] - 1.0) * 100.0) if i + 5 < len(bars) and bar["close"] > 0 else None
        next1_open = ((bars[i + 1]["open"] / bar["close"] - 1.0) * 100.0) if i + 1 < len(bars) and bar["close"] > 0 else None
        next1_close = ((bars[i + 1]["close"] / bar["close"] - 1.0) * 100.0) if i + 1 < len(bars) and bar["close"] > 0 else None

        by_date[bar["date"]] = {
            "index": i,
            "date": bar["date"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "amount": bar["amount"],
            "prevClose": prev_close,
            "prevAmount": prev["amount"] if prev else None,
            "amt5Prev": amt5_prev,
            "changePct": _r(change_pct),
            "openGapPct": _r(open_gap_pct),
            "volRatio5": _r(vol_ratio5),
            "ma5": _r(ma5, 4),
            "ma10": _r(ma10, 4),
            "ma20": _r(ma20, 4),
            "ma60": _r(ma60, 4),
            "prevMa5": _r(prev_ma5, 4),
            "prevMa10": _r(prev_ma10, 4),
            "prevMa20": _r(prev_ma20, 4),
            "prevMa60": _r(prev_ma60, 4),
            "bullStackBefore": bool(prev_ma5 and prev_ma10 and prev_ma20 and prev_ma5 > prev_ma10 > prev_ma20),
            "ret3Before": _r(ret(3, i - 1)) if i > 0 else None,
            "ret5Before": _r(ret(5, i - 1)) if i > 0 else None,
            "ret10Before": _r(ret(10, i - 1)) if i > 0 else None,
            "ret20Before": _r(ret(20, i - 1)) if i > 0 else None,
            "ret5Today": _r(ret(5, i)),
            "ret20Today": _r(ret(20, i)),
            "high20Prev": _r(high20_prev, 4),
            "high60Prev": _r(high60_prev, 4),
            "dd20Before": _r(dd20_before),
            "breakout20": bool(high20_prev and bar["high"] > high20_prev),
            "priorBoardHeight": prior_height,
            "boardHeight": board_height,
            "isLimitUp": bool(is_up),
            "isLimitDown": bool(is_down),
            "observable": bool(observable),
            "hadRecentLuBroken": had_recent_lu_broken,
            "next1OpenPct": _r(next1_open),
            "next1ClosePct": _r(next1_close),
            "post3Pct": _r(post3),
            "post5Pct": _r(post5),
        }
    return by_date, dates


def _classify_entry_pattern(trade: dict, bar: dict | None, board: str) -> str:
    if not bar:
        return "other"
    limit_pct = _nominal_limit_pct(board, trade.get("name", ""), trade["tradeDate"])
    buy_vs_prev = None
    if bar.get("prevClose") and bar["prevClose"] > 0:
        buy_vs_prev = (trade["price"] / bar["prevClose"] - 1.0) * 100.0

    prior_h = bar.get("priorBoardHeight") or 0
    near_limit_buy = buy_vs_prev is not None and buy_vs_prev >= limit_pct * 0.85

    if prior_h >= 1 and (bar.get("isLimitUp") or near_limit_buy or (buy_vs_prev is not None and buy_vs_prev >= limit_pct * 0.4)):
        return "relay_board"
    if bar.get("hadRecentLuBroken") and ((buy_vs_prev is not None and buy_vs_prev >= -2.0) or bar.get("isLimitUp")):
        return "rebound_wrap"
    if prior_h == 0 and (bar.get("isLimitUp") or near_limit_buy or (bar.get("breakout20") and (bar.get("volRatio5") or 0) >= 1.3)):
        return "first_board_breakout"
    ret20 = bar.get("ret20Before")
    if (bar.get("bullStackBefore") or (ret20 is not None and ret20 >= 8.0)) and (
        buy_vs_prev is None or buy_vs_prev < limit_pct * 0.55
    ):
        return "trend_pullback"
    if ret20 is not None and ret20 <= -8.0:
        return "oversold_rebound"
    if prior_h >= 1:
        return "relay_board"
    return "other"


def _scan_market_environment(owner, root: Path | None, first_date: str, last_date: str, buy_dates: set[str], progress=None):
    """Single-pass scan over local TDX daily bars across the master's active period.

    Returns:
      - market_days: ordered trading dates in [first_date, last_date]
      - env_by_date: date -> emotion stage, breadth, limitUp/Down, maxHeight, premium, promotion, benchmark
      - cross_section: buy_date -> {symbol: {amountRankPct, ret5RankPct, ret20RankPct, changeRankPct, cohortSize}}
      - coverage: summary stats & warnings
    """
    warnings: list[str] = []

    def _fallback_env_result(reason_note: str):
        try:
            from service.master_fallback_market import build_fallback_market_env
            fb_env = build_fallback_market_env()
            fb_dates = sorted(d for d in fb_env.keys() if first_date <= d <= last_date)
            if fb_dates:
                sub_env = {d: fb_env[d] for d in fb_dates}
                return fb_dates, sub_env, {}, {
                    "available": True,
                    "stockCount": 544,
                    "failedStocks": 0,
                    "windowStart": fb_dates[0],
                    "windowEnd": fb_dates[-1],
                    "activeTradingDays": len(fb_dates),
                    "warnings": [reason_note],
                }
        except Exception:
            pass
        return [], {}, {}, {"available": False, "stockCount": 0, "warnings": warnings}

    if root is None:
        return _fallback_env_result("当前使用内置 2022–2026 真实历史行情样本自动对齐收益曲线与大盘周期；配置本地通达信目录后可升级为全市场 5,000 只日线与 1 分钟分时对齐。")

    try:
        sec_rows = owner.db.execute(
            "SELECT symbol, code, name, market, kind, earliest_date, latest_date FROM securities WHERE kind='stock'"
        ).fetchall()
    except Exception:
        sec_rows = []

    if not sec_rows:
        return _fallback_env_result("本地通达信尚未扫描索引，当前已自动切换内置 2022–2026 真实历史行情样本对齐；在「设置 → 数据源」扫描本地通达信后将启用全市场分位对齐。")

    # Build master trading calendar from benchmark or top liquid stocks
    benchmarks: dict[str, dict[str, dict]] = {}
    bench_dates: list[str] = []
    for bsym in BENCHMARK_SYMBOLS:
        bbars = _load_stock_daily(root, bsym)
        if bbars:
            bmap, _ = _enrich_bar_indicators(bbars, bsym, bsym)
            benchmarks[bsym] = bmap
            if not bench_dates:
                bench_dates = [b["date"] for b in bbars if b["date"] <= last_date]

    # Collect trading dates up to last_date covering [first_date - warmup .. last_date]
    if bench_dates:
        start_idx = bisect_left(bench_dates, first_date)
        window_start_idx = max(0, start_idx - WARMUP_DAYS)
        window_dates = bench_dates[window_start_idx:]
        if len(window_dates) > MAX_MARKET_WINDOW_DAYS:
            window_dates = window_dates[-MAX_MARKET_WINDOW_DAYS:]
            warnings.append(f"交割单时间跨度较长，全市场情绪与分位扫描覆盖最近 {MAX_MARKET_WINDOW_DAYS} 个交易日。")
    else:
        window_dates = []

    # Filter candidate stocks that overlap our window
    window_earliest = window_dates[0] if window_dates else first_date
    candidates = [
        dict(row) for row in sec_rows
        if row["earliest_date"] <= last_date and row["latest_date"] >= window_earliest
    ]
    if not candidates:
        return _fallback_env_result(f"本地通达信日线未覆盖交割单区间（{first_date} 至 {last_date}），已自动使用内置 2022–2026 真实历史行情样本对齐。")

    records_needed = len(window_dates) + 5 if window_dates else MAX_MARKET_WINDOW_DAYS
    records_needed = min(MAX_MARKET_WINDOW_DAYS + 10, max(120, records_needed))

    # If no benchmark dates, discover trading calendar from first 25 stocks
    discovered_dates: set[str] = set(window_dates)
    if not discovered_dates:
        for sec in candidates[:30]:
            try:
                _, ds = read_raw_tail(tdx.daily_path(root, sec["symbol"]), records_needed, last_date)
                discovered_dates.update(ds)
            except Exception:
                continue
        window_dates = sorted(discovered_dates)[-MAX_MARKET_WINDOW_DAYS:]

    if not window_dates:
        return [], {}, {}, {"available": False, "stockCount": 0, "warnings": ["未能从本地日线构建交易日历。"]}

    date_index = {d: i for i, d in enumerate(window_dates)}
    leadership = Leadership(window_dates)
    totals = [0] * len(window_dates)
    advance_cnt = [0] * len(window_dates)
    change_cnt = [0] * len(window_dates)
    total_amount = [0.0] * len(window_dates)
    buy_date_samples: dict[str, list[tuple[str, float, float | None, float | None, float | None]]] = {
        d: [] for d in buy_dates if d in date_index
    }

    loaded = 0
    failed = 0
    total_candidates = len(candidates)
    for num, sec in enumerate(candidates, 1):
        if progress and (num == 1 or num % 200 == 0 or num == total_candidates):
            progress("对齐全市场环境与分位", num, total_candidates)
        sym = sec["symbol"]
        try:
            raw, _ = read_raw_tail(tdx.daily_path(root, sym), records_needed, last_date)
            rows = rows_from_raw(raw)
        except Exception:
            failed += 1
            continue
        if not rows:
            continue
        aligned = [None] * len(window_dates)
        first_seen = None
        for r in rows:
            idx = date_index.get(r[0])
            if idx is not None:
                aligned[idx] = r
                if first_seen is None:
                    first_seen = idx
        if first_seen is None:
            continue
        loaded += 1
        for i in range(first_seen, len(window_dates)):
            totals[i] += 1

        board = board_of(sym, "stock")
        leadership.add(sec, aligned, board)

        # Accumulate simple daily market breadth + cross-section samples on buy dates
        for i, r in enumerate(aligned):
            if r is None or r[2] <= 0:
                continue
            total_amount[i] += r[3]
            prev = aligned[i - 1] if i > 0 else None
            chg = None
            if prev is not None and prev[2] > 0 and prev[1] > 0:
                chg = (r[1] / prev[1] - 1.0) * 100.0
                change_cnt[i] += 1
                if chg > 0:
                    advance_cnt[i] += 1
            day = window_dates[i]
            if day in buy_date_samples:
                p5 = aligned[i - 5] if i >= 5 else None
                p20 = aligned[i - 20] if i >= 20 else None
                ret5 = (r[1] / p5[1] - 1.0) * 100.0 if p5 and p5[1] > 0 else None
                ret20 = (r[1] / p20[1] - 1.0) * 100.0 if p20 and p20[1] > 0 else None
                buy_date_samples[day].append((sym, r[3], ret5, ret20, chg))

    leader_result = leadership.finish()
    emotion_rows = cycle_timeline(leader_result["timeline"], [n + failed for n in totals])

    env_by_date: dict[str, dict] = {}
    sh_map = benchmarks.get("sh000001", {})
    cyb_map = benchmarks.get("sz399006", {})
    for i, emo in enumerate(emotion_rows):
        day = emo["date"]
        metrics = emo["metrics"]
        ev = emo["evidence"]
        adv_ratio = (advance_cnt[i] / change_cnt[i]) if change_cnt[i] > 0 else None
        sh = sh_map.get(day)
        cyb = cyb_map.get(day)
        env_by_date[day] = {
            "date": day,
            "stage": emo["stage"],
            "stageLabel": emo["label"],
            "observation": emo["observation"],
            "observationLabel": emo["observationLabel"],
            "reason": emo["reason"],
            "scope": emo["scope"],
            "limitUpCount": ev["limitUp"],
            "limitDownCount": ev["limitDown"],
            "maxHeight": ev["maxHeight"],
            "premium": _r(ev["premium"]),
            "promotionRate": _r(ev["promotion"] * 100.0) if ev["promotion"] is not None else None,
            "highFailureRate": _r(ev["highFailure"] * 100.0) if ev["highFailure"] is not None else None,
            "declineRatio": _r(ev["declineRatio"] * 100.0) if ev["declineRatio"] is not None else None,
            "advanceRatio": _r(adv_ratio * 100.0) if adv_ratio is not None else None,
            "aboveMA20Ratio": _r((metrics.get("trend", {}).get("aboveMA20Ratio") or 0) * 100.0)
            if metrics.get("trend", {}).get("aboveMA20Ratio") is not None
            else None,
            "marketAmountYi": _r(total_amount[i] / 1e8, 1),
            "observedCount": ev["observed"],
            "shClose": _r(sh["close"]) if sh else None,
            "shChangePct": sh["changePct"] if sh else None,
            "shAboveMA20": bool(sh["ma20"] and sh["close"] > sh["ma20"]) if sh else None,
            "cybChangePct": cyb["changePct"] if cyb else None,
        }

    # Compute exact cross-section percentile ranks on every buy date
    cross_section: dict[str, dict[str, dict]] = {}
    for day, samples in buy_date_samples.items():
        if not samples:
            continue
        n_total = len(samples)
        amts = sorted(x[1] for x in samples)
        r5s = sorted(x[2] for x in samples if x[2] is not None)
        r20s = sorted(x[3] for x in samples if x[3] is not None)
        chgs = sorted(x[4] for x in samples if x[4] is not None)

        def rank_pct(arr, val):
            if val is None or not arr:
                return None
            pos = bisect_right(arr, val)
            return _r((pos / len(arr)) * 100.0, 1)

        day_ranks: dict[str, dict] = {}
        for sym, amt, ret5, ret20, chg in samples:
            day_ranks[sym] = {
                "cohortSize": n_total,
                "amountRankPct": rank_pct(amts, amt),
                "ret5RankPct": rank_pct(r5s, ret5),
                "ret20RankPct": rank_pct(r20s, ret20),
                "changeRankPct": rank_pct(chgs, chg),
                "marketMedianAmountYi": _r(amts[len(amts) // 2] / 1e8, 3),
                "marketMedianRet5": _r(r5s[len(r5s) // 2], 2) if r5s else None,
                "marketMedianRet20": _r(r20s[len(r20s) // 2], 2) if r20s else None,
            }
        cross_section[day] = day_ranks

    active_days = [d for d in window_dates if first_date <= d <= last_date]
    coverage = {
        "available": loaded > 0,
        "stockCount": loaded,
        "failedStocks": failed,
        "windowStart": window_dates[0],
        "windowEnd": window_dates[-1],
        "activeTradingDays": len(active_days),
        "warnings": warnings,
    }
    return active_days, env_by_date, cross_section, coverage


def _read_intraday_context(root: Path | None, cache: dict, symbol: str, trade_date: str, trade_time: str, trade_price: float):
    if root is None or not symbol or not trade_date:
        return None
    key = (symbol, trade_date)
    if key not in cache:
        try:
            cache[key] = read_intraday(root, symbol, trade_date)
        except (MissingIntraday, ValueError):
            cache[key] = None
    data = cache[key]
    if not data or not data.get("rows"):
        return None
    rows = data["rows"]
    hhmm = trade_time[:5] if trade_time else ""
    cum_amt = 0.0
    cum_vol = 0.0
    matched = None
    prior_vols: list[float] = []
    for row in rows:
        cum_amt += float(row.get("amount") or 0)
        cum_vol += float(row.get("volume") or 0)
        if hhmm and row["time"] <= hhmm:
            matched = row
            prior_vols.append(float(row.get("volume") or 0))
        elif hhmm and row["time"] > hhmm:
            break
    if not hhmm:
        # Without exact time, compute full-day VWAP for price comparison
        full_amt = sum(float(r.get("amount") or 0) for r in rows)
        full_vol = sum(float(r.get("volume") or 0) for r in rows)
        vwap = (full_amt / full_vol) if full_vol > 0 else None
        return {
            "provider": "tdx_lc1",
            "matchedMinute": None,
            "vwap": _r(vwap, 4),
            "priceVsVwapPct": _r((trade_price / vwap - 1.0) * 100.0) if vwap and vwap > 0 else None,
            "minuteVolRatio": None,
        }
    if matched is None:
        matched = rows[0]
    vwap = (cum_amt / cum_vol) if cum_vol > 0 else None
    avg_prior = mean(prior_vols[-16:-1]) if len(prior_vols) >= 6 else None
    min_vol_ratio = (float(matched["volume"]) / avg_prior) if avg_prior and avg_prior > 0 else None
    return {
        "provider": "tdx_lc1",
        "matchedMinute": matched["time"],
        "minuteClose": matched["close"],
        "vwap": _r(vwap, 4),
        "priceVsVwapPct": _r((trade_price / vwap - 1.0) * 100.0) if vwap and vwap > 0 else None,
        "minuteVolRatio": _r(min_vol_ratio),
    }


def _reconstruct_episodes(
    trades: list[dict],
    stock_bars: dict[str, tuple[dict[str, dict], list[str]]],
    env_by_date: dict[str, dict],
    cross_section: dict[str, dict[str, dict]],
    intraday_cache: dict,
    root: Path | None,
):
    """Replay trades chronologically into round-trip episodes & enriched executions."""
    # Detect same-day T-trades: same symbol has both buy and sell on same date
    day_sides: dict[tuple[str, str], set[str]] = defaultdict(set)
    for t in trades:
        day_sides[(t["tradeDate"], t["symbol"])].add(t["side"])

    positions: dict[str, dict] = {}
    episodes: list[dict] = []
    enriched_trades: list[dict] = []

    for t in trades:
        sym = t["symbol"]
        day = t["tradeDate"]
        side = t["side"]
        qty = float(t["quantity"])
        price = float(t["price"])
        amount = float(t["amount"])
        fees = float(t.get("fees") or 0.0)
        board = board_of(sym, "stock")

        bar_map, _ = stock_bars.get(sym, ({}, []))
        bar = bar_map.get(day)
        env = env_by_date.get(day)
        cs = cross_section.get(day, {}).get(sym)
        is_t_day = len(day_sides[(day, sym)]) == 2

        buy_vs_prev = _r((price / bar["prevClose"] - 1.0) * 100.0) if bar and bar.get("prevClose") else None
        day_range = (bar["high"] - bar["low"]) if bar else 0.0
        intraday_pos = (
            _r(((price - bar["low"]) / day_range) * 100.0, 1)
            if bar and day_range > 1e-6
            else (100.0 if bar and bar.get("isLimitUp") else 50.0 if bar else None)
        )
        if intraday_pos is not None:
            intraday_pos = max(0.0, min(100.0, intraday_pos))

        intraday_ctx = _read_intraday_context(root, intraday_cache, sym, day, t.get("tradeTime", ""), price)
        session = _session_bucket(t.get("tradeTime", ""))

        pos = positions.get(sym)
        leg_role = ""
        realized_leg_pnl = None
        realized_leg_pct = None
        entry_pattern = None

        if side == "buy":
            entry_pattern = _classify_entry_pattern(t, bar, board)
            if pos is None or pos["qty"] <= 1e-6:
                leg_role = "open"
                pos = {
                    "id": f"ep-{len(episodes) + 1}",
                    "symbol": sym,
                    "code": t["code"],
                    "name": t["name"],
                    "board": board,
                    "boardLabel": BOARD_LABELS.get(board, board),
                    "entryDate": day,
                    "entryTime": t.get("tradeTime", ""),
                    "entryPriceFirst": price,
                    "entryPattern": entry_pattern,
                    "entryPatternLabel": ENTRY_PATTERN_LABELS.get(entry_pattern, "其他结构"),
                    "entryStage": env["stage"] if env else "unknown",
                    "entryStageLabel": env["stageLabel"] if env else "待确认",
                    "qty": qty,
                    "costTotal": amount + fees,
                    "avgCost": (amount + fees) / qty,
                    "totalBoughtQty": qty,
                    "totalBoughtAmount": amount + fees,
                    "totalSoldQty": 0.0,
                    "totalSoldAmount": 0.0,
                    "peakQty": qty,
                    "peakCostAmount": amount + fees,
                    "buyCount": 1,
                    "sellCount": 0,
                    "addWinCount": 0,
                    "addLossCount": 0,
                    "tTradeCount": int(is_t_day),
                    "realizedPnl": 0.0,
                    "legs": [],
                    "closed": False,
                    "exitDate": None,
                }
                positions[sym] = pos
            else:
                if is_t_day:
                    leg_role = "t_buy"
                    pos["tTradeCount"] += 1
                elif price > pos["avgCost"] * 1.002:
                    leg_role = "add_win"
                    pos["addWinCount"] += 1
                else:
                    leg_role = "add_loss"
                    pos["addLossCount"] += 1
                pos["qty"] += qty
                pos["costTotal"] += amount + fees
                pos["avgCost"] = pos["costTotal"] / pos["qty"]
                pos["totalBoughtQty"] += qty
                pos["totalBoughtAmount"] += amount + fees
                pos["peakQty"] = max(pos["peakQty"], pos["qty"])
                pos["peakCostAmount"] = max(pos["peakCostAmount"], pos["costTotal"])
                pos["buyCount"] += 1
        else:
            if pos is None or pos["qty"] <= 1e-6:
                # Selling stock held before slip window began
                leg_role = "sell_prior_holding"
            else:
                matched_qty = min(qty, pos["qty"])
                cost_basis = pos["avgCost"] * matched_qty
                proceeds = (amount - fees) * (matched_qty / qty)
                realized_leg_pnl = proceeds - cost_basis
                realized_leg_pct = (realized_leg_pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
                pos["realizedPnl"] += realized_leg_pnl
                pos["totalSoldQty"] += matched_qty
                pos["totalSoldAmount"] += proceeds
                pos["qty"] = max(0.0, pos["qty"] - matched_qty)
                pos["costTotal"] = max(0.0, pos["costTotal"] - cost_basis)
                pos["sellCount"] += 1
                if pos["qty"] <= 1e-4:
                    pos["qty"] = 0.0
                    pos["closed"] = True
                    pos["exitDate"] = day
                    leg_role = "close_win" if pos["realizedPnl"] >= 0 else "close_loss"
                elif is_t_day:
                    leg_role = "t_sell"
                else:
                    leg_role = "reduce_win" if realized_leg_pnl >= 0 else "reduce_loss"

        enriched = {
            **t,
            "board": board,
            "boardLabel": BOARD_LABELS.get(board, board),
            "legRole": leg_role,
            "episodeId": pos["id"] if pos else None,
            "isTDay": is_t_day,
            "session": session,
            "sessionLabel": SESSION_LABELS[session],
            "entryPattern": entry_pattern,
            "entryPatternLabel": ENTRY_PATTERN_LABELS.get(entry_pattern, "") if entry_pattern else "",
            "buyVsPrevClosePct": buy_vs_prev,
            "intradayPricePos": intraday_pos,
            "realizedLegPnl": _r(realized_leg_pnl),
            "realizedLegPct": _r(realized_leg_pct),
            "bar": {
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "changePct": bar["changePct"],
                "openGapPct": bar["openGapPct"],
                "amountYi": _r(bar["amount"] / 1e8, 3),
                "volRatio5": bar["volRatio5"],
                "ma5": bar["ma5"],
                "ma10": bar["ma10"],
                "ma20": bar["ma20"],
                "bullStackBefore": bar["bullStackBefore"],
                "ret5Before": bar["ret5Before"],
                "ret20Before": bar["ret20Before"],
                "dd20Before": bar["dd20Before"],
                "priorBoardHeight": bar["priorBoardHeight"],
                "boardHeight": bar["boardHeight"],
                "isLimitUp": bar["isLimitUp"],
                "breakout20": bar["breakout20"],
                "next1OpenPct": bar["next1OpenPct"],
                "next1ClosePct": bar["next1ClosePct"],
                "post3Pct": bar["post3Pct"],
                "post5Pct": bar["post5Pct"],
            } if bar else None,
            "intraday": intraday_ctx,
            "marketEnv": {
                "stage": env["stage"],
                "stageLabel": env["stageLabel"],
                "limitUpCount": env["limitUpCount"],
                "limitDownCount": env["limitDownCount"],
                "maxHeight": env["maxHeight"],
                "premium": env["premium"],
                "promotionRate": env["promotionRate"],
                "advanceRatio": env["advanceRatio"],
                "aboveMA20Ratio": env["aboveMA20Ratio"],
                "shChangePct": env["shChangePct"],
            } if env else None,
            "crossSection": cs,
        }
        enriched_trades.append(enriched)

        if pos is not None:
            pos["legs"].append({
                "tradeId": t["id"],
                "date": day,
                "time": t.get("tradeTime", ""),
                "side": side,
                "legRole": leg_role,
                "quantity": qty,
                "price": price,
                "amount": amount,
                "realizedLegPnl": _r(realized_leg_pnl),
            })
            if pos["closed"]:
                episodes.append(pos)
                del positions[sym]

    # Remaining open positions
    for pos in positions.values():
        episodes.append(pos)

    # Finalize episode metrics (holding days, MFE, MAE, profit giveback, post-exit, exit pattern)
    finalized_episodes: list[dict] = []
    for ep in episodes:
        sym = ep["symbol"]
        bar_map, date_list = stock_bars.get(sym, ({}, []))
        entry_date = ep["entryDate"]
        exit_date = ep["exitDate"]
        avg_buy_price = ep["totalBoughtAmount"] / ep["totalBoughtQty"] if ep["totalBoughtQty"] > 0 else ep["entryPriceFirst"]

        # Holding days
        hold_trading_days = None
        hold_cal_days = None
        if exit_date:
            d0 = dt.date.fromisoformat(entry_date)
            d1 = dt.date.fromisoformat(exit_date)
            hold_cal_days = (d1 - d0).days
            if date_list:
                i0 = bisect_left(date_list, entry_date)
                i1 = bisect_left(date_list, exit_date)
                if i0 < len(date_list) and i1 < len(date_list):
                    hold_trading_days = max(0, i1 - i0)
            if hold_trading_days is None:
                hold_trading_days = max(0, hold_cal_days)

        # MFE / MAE across holding window using local daily bars
        mfe_pct = None
        mae_pct = None
        if date_list and avg_buy_price > 0:
            i_start = bisect_left(date_list, entry_date)
            i_end = bisect_right(date_list, exit_date) - 1 if exit_date else len(date_list) - 1
            if i_start <= i_end < len(date_list):
                window_bars = [bar_map[date_list[k]] for k in range(i_start, i_end + 1) if date_list[k] in bar_map]
                if window_bars:
                    max_high = max(b["high"] for b in window_bars)
                    min_low = min(b["low"] for b in window_bars)
                    mfe_pct = _r((max_high / avg_buy_price - 1.0) * 100.0)
                    mae_pct = _r((min_low / avg_buy_price - 1.0) * 100.0)

        # Return %, outcome, post-exit performance, exit pattern
        if ep["closed"]:
            denom = ep["peakCostAmount"] if ep["peakCostAmount"] > 0 else ep["totalBoughtAmount"]
            ret_pct = _r((ep["realizedPnl"] / denom) * 100.0) if denom > 0 else 0.0
            outcome = "win" if ep["realizedPnl"] > 1e-4 else ("loss" if ep["realizedPnl"] < -1e-4 else "flat")
            exit_bar = bar_map.get(exit_date)
            post3 = exit_bar["post3Pct"] if exit_bar else None
            post5 = exit_bar["post5Pct"] if exit_bar else None
            giveback = _r(max(0.0, mfe_pct - ret_pct)) if mfe_pct is not None and ret_pct is not None else None

            # Classify exit discipline pattern
            if outcome == "loss":
                if hold_trading_days is not None and hold_trading_days <= 1:
                    exit_pattern = "next_day_cut"
                elif ret_pct is not None and ret_pct <= -8.0:
                    exit_pattern = "deep_stop"
                else:
                    exit_pattern = "fast_stop_ma5"
            elif abs(ret_pct or 0.0) < 1.2:
                exit_pattern = "time_flat_exit"
            elif ep["sellCount"] >= 2:
                exit_pattern = "staged_take_profit"
            elif exit_bar and (exit_bar.get("priorBoardHeight") or 0) >= 1 and not exit_bar.get("isLimitUp"):
                exit_pattern = "board_break_profit"
            else:
                exit_pattern = "trend_profit"
        else:
            last_bar = bar_map[date_list[-1]] if date_list else None
            unrealized = ((last_bar["close"] - ep["avgCost"]) * ep["qty"]) if last_bar else None
            denom = ep["peakCostAmount"] if ep["peakCostAmount"] > 0 else ep["totalBoughtAmount"]
            ret_pct = _r(((ep["realizedPnl"] + (unrealized or 0.0)) / denom) * 100.0) if denom > 0 and unrealized is not None else None
            outcome = "open"
            post3, post5, giveback = None, None, None
            exit_pattern = "open"

        finalized_episodes.append({
            "id": ep["id"],
            "symbol": sym,
            "code": ep["code"],
            "name": ep["name"],
            "board": ep["board"],
            "boardLabel": ep["boardLabel"],
            "entryDate": entry_date,
            "exitDate": exit_date,
            "closed": ep["closed"],
            "outcome": outcome,
            "holdingTradingDays": hold_trading_days,
            "holdingCalendarDays": hold_cal_days,
            "buyCount": ep["buyCount"],
            "sellCount": ep["sellCount"],
            "addWinCount": ep["addWinCount"],
            "addLossCount": ep["addLossCount"],
            "tTradeCount": ep["tTradeCount"],
            "avgBuyPrice": _r(avg_buy_price, 4),
            "peakQty": _r(ep["peakQty"], 2),
            "peakCostAmount": _r(ep["peakCostAmount"], 2),
            "totalBoughtAmount": _r(ep["totalBoughtAmount"], 2),
            "totalSoldAmount": _r(ep["totalSoldAmount"], 2),
            "realizedPnl": _r(ep["realizedPnl"], 2),
            "returnPct": ret_pct,
            "mfePct": mfe_pct,
            "maePct": mae_pct,
            "profitGivebackPct": giveback,
            "postExit3dPct": post3,
            "postExit5dPct": post5,
            "entryPattern": ep["entryPattern"],
            "entryPatternLabel": ep["entryPatternLabel"],
            "entryStage": ep["entryStage"],
            "entryStageLabel": ep["entryStageLabel"],
            "exitPattern": exit_pattern,
            "exitPatternLabel": EXIT_PATTERN_LABELS.get(exit_pattern, ""),
            "legs": ep["legs"],
        })

    return finalized_episodes, enriched_trades


def _reconstruct_daily_curve(
    master: dict,
    trades: list[dict],
    active_days: list[str],
    stock_bars: dict[str, tuple[dict[str, dict], list[str]]],
    env_by_date: dict[str, dict],
):
    """Reconstruct daily marked-to-market equity curve, drawdown, and position ratios."""
    trade_days = sorted({t["tradeDate"] for t in trades})
    all_days = sorted(set(active_days) | set(trade_days))
    if not all_days:
        return [], {"capitalBasis": 0, "capitalSource": "none", "maxDrawdownPct": 0, "totalReturnPct": 0}

    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["tradeDate"]].append(t)

    # First pass to measure peak exposure & any explicit accountEquity hints
    holdings: dict[str, dict] = {}
    last_known_price: dict[str, float] = {}
    peak_exposure = 0.0
    observed_equities: list[tuple[str, float]] = []
    daily_raw = []
    cash_flow = 0.0  # cumulative proceeds - purchases - fees

    for day in all_days:
        day_buy_amt = 0.0
        day_sell_amt = 0.0
        day_buy_cnt = 0
        day_sell_cnt = 0
        for t in by_day.get(day, []):
            sym = t["symbol"]
            qty = float(t["quantity"])
            price = float(t["price"])
            amt = float(t["amount"])
            fees = float(t.get("fees") or 0.0)
            last_known_price[sym] = price
            if t.get("accountEquity"):
                observed_equities.append((day, float(t["accountEquity"])))
            if t["side"] == "buy":
                day_buy_amt += amt
                day_buy_cnt += 1
                cash_flow -= (amt + fees)
                pos = holdings.setdefault(sym, {"qty": 0.0, "cost": 0.0, "name": t["name"], "code": t["code"]})
                pos["qty"] += qty
                pos["cost"] += (amt + fees)
            else:
                day_sell_amt += amt
                day_sell_cnt += 1
                cash_flow += (amt - fees)
                pos = holdings.get(sym)
                if pos and pos["qty"] > 0:
                    sell_q = min(qty, pos["qty"])
                    avg_c = pos["cost"] / pos["qty"]
                    pos["qty"] = max(0.0, pos["qty"] - sell_q)
                    pos["cost"] = max(0.0, pos["cost"] - avg_c * sell_q)
                    if pos["qty"] <= 1e-4:
                        del holdings[sym]

        # Mark active holdings to local TDX close (or last trade price fallback)
        holding_values = []
        total_mv = 0.0
        for sym, pos in holdings.items():
            bar_map, _ = stock_bars.get(sym, ({}, []))
            bar = bar_map.get(day)
            px = bar["close"] if bar else last_known_price.get(sym, pos["cost"] / pos["qty"])
            if bar:
                last_known_price[sym] = bar["close"]
            mv = pos["qty"] * px
            total_mv += mv
            holding_values.append((sym, pos["name"], mv))

        peak_exposure = max(peak_exposure, total_mv, day_buy_amt)
        holding_values.sort(key=lambda x: -x[2])
        top1_mv = holding_values[0][2] if holding_values else 0.0
        daily_raw.append({
            "date": day,
            "holdingCount": len(holdings),
            "marketValue": total_mv,
            "top1MarketValue": top1_mv,
            "top1Symbol": holding_values[0][0] if holding_values else None,
            "top1Name": holding_values[0][1] if holding_values else None,
            "cumNetPnl": cash_flow + total_mv,
            "buyAmount": day_buy_amt,
            "sellAmount": day_sell_amt,
            "buyCount": day_buy_cnt,
            "sellCount": day_sell_cnt,
        })

    # Determine capital base (transparently labeled)
    if master.get("initialCapital"):
        capital_base = float(master["initialCapital"])
        capital_source = "user_configured"
    elif observed_equities:
        capital_base = observed_equities[0][1]
        capital_source = "slip_equity"
    else:
        # Ensure minimum positive equity floor to avoid negative denominator
        min_cash_need = max(peak_exposure, max((abs(min(0.0, d["cumNetPnl"])) for d in daily_raw), default=0.0) * 1.2)
        capital_base = max(10000.0, round(min_cash_need * 1.05, 2))
        capital_source = "estimated_peak_exposure"

    curve = []
    peak_nav = 1.0
    max_dd = 0.0
    daily_returns = []
    prev_nav = 1.0
    for row in daily_raw:
        equity = capital_base + row["cumNetPnl"]
        nav = max(0.05, equity / capital_base) if capital_base > 0 else 1.0
        peak_nav = max(peak_nav, nav)
        dd_pct = ((nav / peak_nav) - 1.0) * 100.0
        max_dd = min(max_dd, dd_pct)
        day_ret = ((nav / prev_nav) - 1.0) * 100.0 if prev_nav > 0 else 0.0
        daily_returns.append(day_ret)
        prev_nav = nav
        pos_ratio = min(150.0, (row["marketValue"] / equity) * 100.0) if equity > 0 else 0.0
        top1_weight = min(150.0, (row["top1MarketValue"] / equity) * 100.0) if equity > 0 else 0.0
        env = env_by_date.get(row["date"])
        curve.append({
            "date": row["date"],
            "equity": _r(equity, 2),
            "cumPnl": _r(row["cumNetPnl"], 2),
            "returnPct": _r((nav - 1.0) * 100.0, 2),
            "drawdownPct": _r(dd_pct, 2),
            "positionRatioPct": _r(pos_ratio, 1),
            "top1WeightPct": _r(top1_weight, 1),
            "holdingCount": row["holdingCount"],
            "marketValue": _r(row["marketValue"], 2),
            "buyCount": row["buyCount"],
            "sellCount": row["sellCount"],
            "buyAmount": _r(row["buyAmount"], 2),
            "sellAmount": _r(row["sellAmount"], 2),
            "stage": env["stage"] if env else "unknown",
            "stageLabel": env["stageLabel"] if env else "待确认",
            "shChangePct": env["shChangePct"] if env else None,
        })

    final_ret = curve[-1]["returnPct"] if curve else 0.0
    # Smoothness / bulldozer metrics: % of positive weeks, calmar-like return/|maxDD|
    calmar_like = _r(final_ret / abs(max_dd), 2) if max_dd < -0.1 else None
    meta = {
        "capitalBasis": _r(capital_base, 2),
        "capitalSource": capital_source,
        "capitalSourceLabel": {
            "user_configured": "用户填写的参考本金",
            "slip_equity": "交割单中记录的参考总资产",
            "estimated_peak_exposure": "按历史峰值持仓市值估算（可在档案中填写实际本金以获得精确收益率）",
        }[capital_source],
        "totalPnl": curve[-1]["cumPnl"] if curve else 0.0,
        "totalReturnPct": final_ret,
        "maxDrawdownPct": _r(max_dd, 2),
        "returnToDrawdown": calmar_like,
    }
    return curve, meta


def _build_environment_analysis(curve: list[dict], episodes: list[dict], env_by_date: dict[str, dict]):
    if not env_by_date:
        return {"available": False, "byStage": [], "buyVsIdle": [], "summary": "缺少本地通达信日线覆盖，暂无法对齐市场情绪周期。"}

    # Compare days with new buys vs idle/no-buy days
    buy_days = [c["date"] for c in curve if c["buyCount"] > 0 and c["date"] in env_by_date]
    idle_days = [c["date"] for c in curve if c["buyCount"] == 0 and c["date"] in env_by_date]
    empty_days = [c["date"] for c in curve if c["holdingCount"] == 0 and c["date"] in env_by_date]

    def avg_env(day_list, key):
        return _safe_mean([env_by_date[d].get(key) for d in day_list])

    buy_vs_idle = [
        {"metric": "涨停家数", "unit": "家", "buyDay": avg_env(buy_days, "limitUpCount"), "idleDay": avg_env(idle_days, "limitUpCount"), "emptyDay": avg_env(empty_days, "limitUpCount")},
        {"metric": "跌停家数", "unit": "家", "buyDay": avg_env(buy_days, "limitDownCount"), "idleDay": avg_env(idle_days, "limitDownCount"), "emptyDay": avg_env(empty_days, "limitDownCount")},
        {"metric": "连板最高高度", "unit": "板", "buyDay": avg_env(buy_days, "maxHeight"), "idleDay": avg_env(idle_days, "maxHeight"), "emptyDay": avg_env(empty_days, "maxHeight")},
        {"metric": "昨日涨停溢价", "unit": "%", "buyDay": avg_env(buy_days, "premium"), "idleDay": avg_env(idle_days, "premium"), "emptyDay": avg_env(empty_days, "premium")},
        {"metric": "连板晋级率", "unit": "%", "buyDay": avg_env(buy_days, "promotionRate"), "idleDay": avg_env(idle_days, "promotionRate"), "emptyDay": avg_env(empty_days, "promotionRate")},
        {"metric": "全市场上涨占比", "unit": "%", "buyDay": avg_env(buy_days, "advanceRatio"), "idleDay": avg_env(idle_days, "advanceRatio"), "emptyDay": avg_env(empty_days, "advanceRatio")},
        {"metric": "站上MA20广度", "unit": "%", "buyDay": avg_env(buy_days, "aboveMA20Ratio"), "idleDay": avg_env(idle_days, "aboveMA20Ratio"), "emptyDay": avg_env(empty_days, "aboveMA20Ratio")},
        {"metric": "上证指数涨跌幅", "unit": "%", "buyDay": avg_env(buy_days, "shChangePct"), "idleDay": avg_env(idle_days, "shChangePct"), "emptyDay": avg_env(empty_days, "shChangePct")},
    ]

    # Breakdown by emotion stage
    by_stage = []
    closed_eps = [e for e in episodes if e["closed"]]
    for stage_key in ("trial", "advance", "chop", "decline", "unknown"):
        stage_days = [c for c in curve if c["stage"] == stage_key]
        if not stage_days:
            continue
        stage_buy_days = [c for c in stage_days if c["buyCount"] > 0]
        stage_eps = [e for e in closed_eps if e["entryStage"] == stage_key]
        wins = [e for e in stage_eps if e["outcome"] == "win"]
        by_stage.append({
            "stage": stage_key,
            "label": STAGES[stage_key],
            "tradingDays": len(stage_days),
            "buyDays": len(stage_buy_days),
            "attackFreqPct": _pct(len(stage_buy_days), len(stage_days), 1),
            "avgPositionPct": _safe_mean([c["positionRatioPct"] for c in stage_days], 1),
            "avgHoldingCount": _safe_mean([c["holdingCount"] for c in stage_days], 2),
            "closedEpisodes": len(stage_eps),
            "winRatePct": _pct(len(wins), len(stage_eps), 1),
            "avgReturnPct": _safe_mean([e["returnPct"] for e in stage_eps], 2),
            "totalPnl": _r(sum(e["realizedPnl"] for e in stage_eps), 2),
        })

    return {
        "available": True,
        "buyDayCount": len(buy_days),
        "idleDayCount": len(idle_days),
        "emptyDayCount": len(empty_days),
        "byStage": by_stage,
        "buyVsIdle": buy_vs_idle,
    }


def _build_selection_analysis(enriched_trades: list[dict], episodes: list[dict]):
    buys = [t for t in enriched_trades if t["side"] == "buy"]
    open_buys = [t for t in buys if t["legRole"] == "open"]
    closed_eps = [e for e in episodes if e["closed"]]

    # Pattern breakdown
    pattern_stats = []
    for pkey, plabel in ENTRY_PATTERN_LABELS.items():
        p_buys = [t for t in open_buys if t["entryPattern"] == pkey]
        p_eps = [e for e in closed_eps if e["entryPattern"] == pkey]
        if not p_buys and not p_eps:
            continue
        wins = [e for e in p_eps if e["outcome"] == "win"]
        pattern_stats.append({
            "pattern": pkey,
            "label": plabel,
            "openBuyCount": len(p_buys),
            "sharePct": _pct(len(p_buys), len(open_buys), 1),
            "closedEpisodes": len(p_eps),
            "winRatePct": _pct(len(wins), len(p_eps), 1),
            "avgReturnPct": _safe_mean([e["returnPct"] for e in p_eps], 2),
            "avgHoldDays": _safe_mean([e["holdingTradingDays"] for e in p_eps], 1),
        })
    pattern_stats.sort(key=lambda x: -x["openBuyCount"])

    # Board preference
    board_counter = Counter(t["board"] for t in open_buys)
    boards = [
        {
            "board": b,
            "label": BOARD_LABELS.get(b, b),
            "count": cnt,
            "sharePct": _pct(cnt, len(open_buys), 1),
        }
        for b, cnt in board_counter.most_common()
    ]

    # Session timing distribution
    session_counter = Counter(t["session"] for t in buys)
    sessions = [
        {
            "session": s,
            "label": SESSION_LABELS[s],
            "count": cnt,
            "sharePct": _pct(cnt, len(buys), 1),
        }
        for s, cnt in session_counter.most_common()
    ]

    # Intraday buy price positioning buckets
    pos_buckets = [
        ("水下/低吸区 (<0%)", lambda t: t["buyVsPrevClosePct"] is not None and t["buyVsPrevClosePct"] < 0),
        ("温和承接区 (0% ~ +4%)", lambda t: t["buyVsPrevClosePct"] is not None and 0 <= t["buyVsPrevClosePct"] < 4),
        ("强势半路区 (+4% ~ +8.5%)", lambda t: t["buyVsPrevClosePct"] is not None and 4 <= t["buyVsPrevClosePct"] < 8.5),
        ("涨停/近板打板区 (≥+8.5%)", lambda t: t["buyVsPrevClosePct"] is not None and t["buyVsPrevClosePct"] >= 8.5),
    ]
    bar_buys = [t for t in buys if t["buyVsPrevClosePct"] is not None]
    price_zones = [
        {
            "label": label,
            "count": sum(1 for t in bar_buys if fn(t)),
            "sharePct": _pct(sum(1 for t in bar_buys if fn(t)), len(bar_buys), 1),
        }
        for label, fn in pos_buckets
    ]

    # Cross-section control group comparison (master's bought stocks vs market median on same day)
    cs_buys = [t for t in open_buys if t.get("crossSection")]
    control_group = {
        "sampleCount": len(cs_buys),
        "avgAmountRankPct": _safe_mean([t["crossSection"]["amountRankPct"] for t in cs_buys], 1),
        "medianAmountRankPct": _safe_median([t["crossSection"]["amountRankPct"] for t in cs_buys], 1),
        "avgRet5RankPct": _safe_mean([t["crossSection"]["ret5RankPct"] for t in cs_buys], 1),
        "avgRet20RankPct": _safe_mean([t["crossSection"]["ret20RankPct"] for t in cs_buys], 1),
        "avgChangeRankPct": _safe_mean([t["crossSection"]["changeRankPct"] for t in cs_buys], 1),
        "avgStockAmountYi": _safe_mean([t["bar"]["amountYi"] for t in open_buys if t.get("bar")], 2),
        "medianStockAmountYi": _safe_median([t["bar"]["amountYi"] for t in open_buys if t.get("bar")], 2),
        "marketMedianAmountYi": _safe_mean([t["crossSection"]["marketMedianAmountYi"] for t in cs_buys], 3),
        "avgVolRatio5": _safe_mean([t["bar"]["volRatio5"] for t in open_buys if t.get("bar")], 2),
        "avgRet5Before": _safe_mean([t["bar"]["ret5Before"] for t in open_buys if t.get("bar")], 2),
        "avgRet20Before": _safe_mean([t["bar"]["ret20Before"] for t in open_buys if t.get("bar")], 2),
        "bullStackRatePct": _pct(
            sum(1 for t in open_buys if t.get("bar") and t["bar"]["bullStackBefore"]),
            sum(1 for t in open_buys if t.get("bar")),
            1,
        ),
        "breakout20RatePct": _pct(
            sum(1 for t in open_buys if t.get("bar") and t["bar"]["breakout20"]),
            sum(1 for t in open_buys if t.get("bar")),
            1,
        ),
        "priorLimitUpRatePct": _pct(
            sum(1 for t in open_buys if t.get("bar") and (t["bar"]["priorBoardHeight"] or 0) >= 1),
            sum(1 for t in open_buys if t.get("bar")),
            1,
        ),
        "avgIntradayPosPct": _safe_mean([t["intradayPricePos"] for t in buys], 1),
        "avgVwapDiffPct": _safe_mean([t["intraday"]["priceVsVwapPct"] for t in buys if t.get("intraday")], 2),
    }

    return {
        "totalBuys": len(buys),
        "openBuys": len(open_buys),
        "patterns": pattern_stats,
        "boards": boards,
        "sessions": sessions,
        "priceZones": price_zones,
        "controlGroup": control_group,
    }


def _build_position_and_exit_analysis(curve: list[dict], episodes: list[dict], enriched_trades: list[dict], capital_meta: dict):
    closed = [e for e in episodes if e["closed"]]
    wins = [e for e in closed if e["outcome"] == "win"]
    losses = [e for e in closed if e["outcome"] == "loss"]

    win_rate = _pct(len(wins), len(closed), 1)
    avg_win_pct = _safe_mean([e["returnPct"] for e in wins], 2)
    avg_loss_pct = _safe_mean([e["returnPct"] for e in losses], 2)
    payoff_ratio = (
        _r(abs(avg_win_pct / avg_loss_pct), 2)
        if avg_win_pct is not None and avg_loss_pct is not None and abs(avg_loss_pct) > 1e-6
        else None
    )
    gross_profit = sum(e["realizedPnl"] for e in wins)
    gross_loss = abs(sum(e["realizedPnl"] for e in losses))
    profit_factor = _r(gross_profit / gross_loss, 2) if gross_loss > 1e-6 else (None if not gross_profit else 99.0)

    # Holding count distribution across active days
    total_days = len(curve)
    empty_days = sum(1 for c in curve if c["holdingCount"] == 0)
    dist_counts = [
        ("空仓 (0只)", empty_days),
        ("单吊 (1只)", sum(1 for c in curve if c["holdingCount"] == 1)),
        ("精简分仓 (2只)", sum(1 for c in curve if c["holdingCount"] == 2)),
        ("适度分散 (3-4只)", sum(1 for c in curve if 3 <= c["holdingCount"] <= 4)),
        ("广泛分散 (≥5只)", sum(1 for c in curve if c["holdingCount"] >= 5)),
    ]
    holding_distribution = [
        {"label": label, "days": cnt, "sharePct": _pct(cnt, total_days, 1)}
        for label, cnt in dist_counts
    ]

    # Single-stock sizing relative to capital basis
    cap = capital_meta["capitalBasis"] or 1.0
    ep_weights = [min(150.0, (e["peakCostAmount"] / cap) * 100.0) for e in episodes if e["peakCostAmount"] > 0]

    # Scaling & adding behavior
    staged_buys = [e for e in episodes if e["buyCount"] >= 2]
    staged_sells = [e for e in closed if e["sellCount"] >= 2]
    add_win_eps = [e for e in closed if e["addWinCount"] > 0]
    add_loss_eps = [e for e in closed if e["addLossCount"] > 0]
    t_trade_eps = [e for e in closed if e["tTradeCount"] > 0]

    # Consecutive-loss sizing contraction test:
    # Sort closed episodes by exitDate, check if the NEXT new entry after a loss uses smaller size
    sorted_eps = sorted(episodes, key=lambda e: (e["entryDate"], e["id"]))
    after_win_sizes = []
    after_loss_sizes = []
    last_outcome = None
    for ep in sorted_eps:
        if last_outcome == "win":
            after_win_sizes.append(ep["peakCostAmount"])
        elif last_outcome == "loss":
            after_loss_sizes.append(ep["peakCostAmount"])
        if ep["closed"]:
            last_outcome = ep["outcome"]
    avg_after_win = _safe_mean(after_win_sizes, 2)
    avg_after_loss = _safe_mean(after_loss_sizes, 2)
    loss_contraction_pct = (
        _r((avg_after_loss / avg_after_win - 1.0) * 100.0, 1)
        if avg_after_win and avg_after_loss is not None and avg_after_win > 0
        else None
    )

    # Exit pattern breakdown
    exit_patterns = []
    for xkey, xlabel in EXIT_PATTERN_LABELS.items():
        if xkey == "open":
            continue
        eps = [e for e in closed if e["exitPattern"] == xkey]
        if not eps:
            continue
        exit_patterns.append({
            "pattern": xkey,
            "label": xlabel,
            "count": len(eps),
            "sharePct": _pct(len(eps), len(closed), 1),
            "avgReturnPct": _safe_mean([e["returnPct"] for e in eps], 2),
            "avgHoldDays": _safe_mean([e["holdingTradingDays"] for e in eps], 1),
            "avgPost3dPct": _safe_mean([e["postExit3dPct"] for e in eps], 2),
        })
    exit_patterns.sort(key=lambda x: -x["count"])

    # Loss tail & stop-loss sharpness
    loss_rets = [e["returnPct"] for e in losses if e["returnPct"] is not None]
    win_rets = [e["returnPct"] for e in wins if e["returnPct"] is not None]
    cut_within_1d_pct = _pct(
        sum(1 for e in losses if e["holdingTradingDays"] is not None and e["holdingTradingDays"] <= 1),
        len(losses),
        1,
    )
    cut_within_5pct = _pct(
        sum(1 for r in loss_rets if r >= -5.0),
        len(loss_rets),
        1,
    )
    sell_near_peak_pct = _pct(
        sum(1 for e in closed if e["postExit3dPct"] is not None and e["postExit3dPct"] <= 0),
        sum(1 for e in closed if e["postExit3dPct"] is not None),
        1,
    )

    position = {
        "avgPositionPct": _safe_mean([c["positionRatioPct"] for c in curve], 1),
        "maxPositionPct": _r(max((c["positionRatioPct"] for c in curve), default=0.0), 1),
        "emptyDayRatioPct": _pct(empty_days, total_days, 1),
        "avgHoldingCount": _safe_mean([c["holdingCount"] for c in curve], 2),
        "maxHoldingCount": max((c["holdingCount"] for c in curve), default=0),
        "medianSingleWeightPct": _safe_median(ep_weights, 1),
        "p90SingleWeightPct": _percentile(ep_weights, 0.9, 1),
        "holdingDistribution": holding_distribution,
        "stagedBuyRatePct": _pct(len(staged_buys), len(episodes), 1),
        "stagedSellRatePct": _pct(len(staged_sells), len(closed), 1),
        "addWinCount": sum(e["addWinCount"] for e in episodes),
        "addWinEpisodeWinRatePct": _pct(sum(1 for e in add_win_eps if e["outcome"] == "win"), len(add_win_eps), 1),
        "addLossCount": sum(e["addLossCount"] for e in episodes),
        "addLossEpisodeWinRatePct": _pct(sum(1 for e in add_loss_eps if e["outcome"] == "win"), len(add_loss_eps), 1),
        "tTradeDaysCount": len({(t["tradeDate"], t["symbol"]) for t in enriched_trades if t["isTDay"]}),
        "tTradeEpisodeWinRatePct": _pct(sum(1 for e in t_trade_eps if e["outcome"] == "win"), len(t_trade_eps), 1),
        "avgSizeAfterWin": avg_after_win,
        "avgSizeAfterLoss": avg_after_loss,
        "lossContractionPct": loss_contraction_pct,
    }

    exits = {
        "closedCount": len(closed),
        "openCount": sum(1 for e in episodes if not e["closed"]),
        "winCount": len(wins),
        "lossCount": len(losses),
        "winRatePct": win_rate,
        "avgWinPct": avg_win_pct,
        "avgLossPct": avg_loss_pct,
        "payoffRatio": payoff_ratio,
        "profitFactor": profit_factor,
        "avgHoldDaysAll": _safe_mean([e["holdingTradingDays"] for e in closed], 1),
        "avgHoldDaysWin": _safe_mean([e["holdingTradingDays"] for e in wins], 1),
        "avgHoldDaysLoss": _safe_mean([e["holdingTradingDays"] for e in losses], 1),
        "medianWinPct": _safe_median(win_rets, 2),
        "medianLossPct": _safe_median(loss_rets, 2),
        "p90WinPct": _percentile(win_rets, 0.9, 2),
        "worstLossPct": _r(min(loss_rets), 2) if loss_rets else None,
        "bestWinPct": _r(max(win_rets), 2) if win_rets else None,
        "avgMfeWinPct": _safe_mean([e["mfePct"] for e in wins], 2),
        "avgMaeWinPct": _safe_mean([e["maePct"] for e in wins], 2),
        "avgMaeLossPct": _safe_mean([e["maePct"] for e in losses], 2),
        "avgProfitGivebackPct": _safe_mean([e["profitGivebackPct"] for e in wins], 2),
        "avgPostExit3dPct": _safe_mean([e["postExit3dPct"] for e in closed], 2),
        "avgPostExit5dPct": _safe_mean([e["postExit5dPct"] for e in closed], 2),
        "cutWithin1dPct": cut_within_1d_pct,
        "cutWithin5PctLossRate": cut_within_5pct,
        "sellNearPeakRatePct": sell_near_peak_pct,
        "patterns": exit_patterns,
    }
    return position, exits


def _build_dual_track_guidance(
    selection: dict,
    position: dict,
    exits: dict,
    environment: dict,
    episodes: list[dict],
    enriched_trades: list[dict],
):
    """Build BOTH full-spectrum master rules AND non-realtime executable playbook."""
    buys = [t for t in enriched_trades if t["side"] == "buy"]
    closed = [e for e in episodes if e["closed"]]

    # Measure realtime / live-screen dependency honestly
    timed_buys = [t for t in buys if t["session"] != "unknown"]
    rush_buys = [t for t in timed_buys if t["session"] in ("auction", "open_rush")]
    bar_buys = [t for t in buys if t["buyVsPrevClosePct"] is not None]
    near_limit_buys = [
        t for t in bar_buys
        if t["buyVsPrevClosePct"] >= _nominal_limit_pct(t["board"], t.get("name", ""), t["tradeDate"]) * 0.85
    ]
    one_day_exits = [e for e in closed if e["holdingTradingDays"] is not None and e["holdingTradingDays"] <= 1]

    rush_pct = _pct(len(rush_buys), len(timed_buys), 1)
    near_limit_pct = _pct(len(near_limit_buys), len(bar_buys), 1)
    one_day_pct = _pct(len(one_day_exits), len(closed), 1)
    t_day_ratio = _pct(position["tTradeDaysCount"], max(1, len(buys)), 1)

    dep_components = [x for x in (rush_pct, near_limit_pct, one_day_pct) if x is not None]
    realtime_dependency = _r(mean(dep_components), 1) if dep_components else None
    if realtime_dependency is None:
        dep_level = "待补充行情/时间字段"
    elif realtime_dependency >= 60:
        dep_level = "高盯盘依赖（强盘口/早盘打板或隔日超短节奏）"
    elif realtime_dependency >= 35:
        dep_level = "中等盯盘依赖（部分盘中强弱确认，可条件单转化大半）"
    else:
        dep_level = "低盯盘依赖（波段/趋势承接为主，非常适合盘后复盘+条件单）"

    # Evaluate the subset of master's closed episodes that are naturally non-realtime friendly:
    # (trend_pullback / rebound_wrap / oversold_rebound OR held >= 2 trading days and not pure limit-up chase)
    ep_first_buy = {}
    for t in enriched_trades:
        if t["side"] == "buy" and t["episodeId"] and t["episodeId"] not in ep_first_buy:
            ep_first_buy[t["episodeId"]] = t

    non_rt_eps = []
    rt_only_eps = []
    for e in closed:
        fb = ep_first_buy.get(e["id"])
        is_chase = False
        if fb and fb["buyVsPrevClosePct"] is not None:
            lim = _nominal_limit_pct(e["board"], e["name"], e["entryDate"])
            is_chase = fb["buyVsPrevClosePct"] >= lim * 0.85
        is_fast = (e["holdingTradingDays"] is not None and e["holdingTradingDays"] <= 1)
        if not is_chase and (not is_fast or e["entryPattern"] in ("trend_pullback", "rebound_wrap", "oversold_rebound")):
            non_rt_eps.append(e)
        else:
            rt_only_eps.append(e)

    def subset_summary(eps):
        if not eps:
            return {"count": 0, "winRatePct": None, "avgReturnPct": None, "avgHoldDays": None}
        ws = [x for x in eps if x["outcome"] == "win"]
        return {
            "count": len(eps),
            "winRatePct": _pct(len(ws), len(eps), 1),
            "avgReturnPct": _safe_mean([x["returnPct"] for x in eps], 2),
            "avgHoldDays": _safe_mean([x["holdingTradingDays"] for x in eps], 1),
        }

    # Build data-backed original master rules
    original_rules = []
    if environment.get("available") and environment.get("byStage"):
        best_stage = max(
            [s for s in environment["byStage"] if s["closedEpisodes"] > 0] or environment["byStage"],
            key=lambda s: (s["avgPositionPct"] or 0, s["buyDays"]),
        )
        decline_stage = next((s for s in environment["byStage"] if s["stage"] == "decline"), None)
        rule_text = f"主攻环境偏重「{best_stage['label']}」（日均仓位 {best_stage['avgPositionPct'] or 0:.1f}%，出手频率 {best_stage['attackFreqPct'] or 0:.1f}%）"
        if decline_stage:
            rule_text += f"；「主跌退潮」期日均仓位降至 {decline_stage['avgPositionPct'] or 0:.1f}%（出手 {decline_stage['buyDays']}/{decline_stage['tradingDays']} 日）"
        original_rules.append({"dimension": "择时与环境开关", "rule": rule_text})

    top_pat = selection["patterns"][0] if selection["patterns"] else None
    cg = selection["controlGroup"]
    if top_pat:
        sel_text = f"核心买点结构为「{top_pat['label']}」（占首仓 {top_pat['sharePct']}%，胜率 {top_pat['winRatePct'] if top_pat['winRatePct'] is not None else '—'}%，均收益 {top_pat['avgReturnPct'] if top_pat['avgReturnPct'] is not None else '—'}%）"
        if cg.get("medianAmountRankPct") is not None:
            sel_text += f"；偏好高流动性核心标的（成交额位于全市场前 {max(0.1, round(100 - cg['medianAmountRankPct'], 1))}% 分位，中位成交额 {cg['medianStockAmountYi']} 亿）"
        original_rules.append({"dimension": "选股与买点画像", "rule": sel_text})

    pos_text = (
        f"日均持股 {position['avgHoldingCount'] or 0} 只（最多 {position['maxHoldingCount']} 只），"
        f"单票中位仓位约 {position['medianSingleWeightPct'] or 0}%，空仓天数占比 {position['emptyDayRatioPct'] or 0}%。"
    )
    if position["addWinCount"] or position["addLossCount"]:
        pos_text += f" 加仓习惯：浮盈加仓 {position['addWinCount']} 次 vs 浮亏补仓 {position['addLossCount']} 次。"
    if position["lossContractionPct"] is not None:
        direction = "收缩" if position["lossContractionPct"] < -5 else ("放大" if position["lossContractionPct"] > 5 else "基本稳定")
        pos_text += f" 上一笔亏损后，下一笔仓位平均{direction} {abs(position['lossContractionPct'])}%。"
    original_rules.append({"dimension": "分仓与加减仓纪律", "rule": pos_text})

    exit_text = (
        f"胜率 {exits['winRatePct'] if exits['winRatePct'] is not None else '—'}%，"
        f"盈亏比 {exits['payoffRatio'] if exits['payoffRatio'] is not None else '—'}，"
        f"盈利单均拿 {exits['avgHoldDaysWin'] if exits['avgHoldDaysWin'] is not None else '—'} 天 vs 亏损单均拿 {exits['avgHoldDaysLoss'] if exits['avgHoldDaysLoss'] is not None else '—'} 天。"
    )
    if exits["worstLossPct"] is not None:
        exit_text += f" 亏损中位数 {exits['medianLossPct']}%，最大单笔亏损 {exits['worstLossPct']}%。"
    if exits["cutWithin1dPct"] is not None:
        exit_text += f" {exits['cutWithin1dPct']}% 的亏损单在次日即完成止损。"
    original_rules.append({"dimension": "止盈止损与退出", "rule": exit_text})

    # Build concrete Non-Realtime Executable Playbook for user who cannot watch live screen
    stop_line = round(min(-3.0, max(-8.0, (exits["medianLossPct"] or -5.0) * 1.15)), 1)
    take_line = round(max(6.0, min(25.0, (exits["medianWinPct"] or 10.0))), 1)
    max_single = round(min(35.0, max(15.0, (position["medianSingleWeightPct"] or 25.0))), 0)
    max_slots = max(2, min(5, int(round((position["avgHoldingCount"] or 2) + 1))))

    non_realtime_checklist = [
        {
            "phase": "盘后筛选（15:00-15:30 / 晚间）",
            "action": (
                f"先在 A-trade「情绪周期 T10」检查环境：若处于主跌退潮期则总仓位压至 20% 以下或空仓；"
                f"否则聚焦全市场成交额前 {max(5, int(round(100 - (cg.get('medianAmountRankPct') or 85))))}% 的高流动性主线核心"
                f"（参考中位成交额 ≥ {cg.get('medianStockAmountYi') or 5} 亿），剔除冷门杂毛。"
            ),
        },
        {
            "phase": "买点改造（不追秒板，改用盘前条件单/回踩低吸）",
            "action": (
                "放弃需要全天盯盘的 09:30-09:45 急拉打板与日内高频做 T；"
                "改为盘后锁定的 2-3 只核心候选，次日通过券商「价格条件单 / 回落买入条件单」挂在均线支撑位（如 MA5 / 前日收盘 ±2% 区间）分两笔低吸，不成交不强追。"
            ),
        },
        {
            "phase": "分仓与风控上限（防止单票黑天鹅）",
            "action": (
                f"参考大佬节奏裁剪为非盯盘安全版：同时持股不超过 {max_slots} 只，单票首次建仓不超过总资金 {int(max_single)}%；"
                "只允许在收盘确认浮盈且趋势完好时次日加仓，严禁盘中无计划浮亏摊平；连续亏损 2 笔后强制将单票上限减半。"
            ),
        },
        {
            "phase": "自动化止盈止损（次日委托条件单）",
            "action": (
                f"买入成交当晚立即预埋止损与移动止盈条件单：硬止损参考 {stop_line}%（或收盘跌破 MA5/MA10 次日集合竞价无条件执行）；"
                f"盈利达 +{take_line}% 先减仓一半锁利润，剩余底仓设「高位回落 {max(3.0, round((exits.get('avgProfitGivebackPct') or 4.0), 1))}% 止盈条件单」让利润奔跑。"
            ),
        },
    ]

    return {
        "realtimeDependencyPct": realtime_dependency,
        "realtimeDependencyLevel": dep_level,
        "dependencyBreakdown": {
            "openRushBuyPct": rush_pct,
            "nearLimitChasePct": near_limit_pct,
            "nextDayExitPct": one_day_pct,
            "tTradeFreqPct": t_day_ratio,
        },
        "nonRealtimeSubset": subset_summary(non_rt_eps),
        "realtimeHeavySubset": subset_summary(rt_only_eps),
        "originalRules": original_rules,
        "nonRealtimeChecklist": non_realtime_checklist,
    }


def compare_with_self_account(master_analysis: dict, self_summary: dict) -> dict:
    """Compare master's quantitative discipline against the user's own account metrics."""
    m_exits = master_analysis.get("exits") or {}
    m_pos = master_analysis.get("position") or {}
    rows = []

    def add_row(label, master_val, self_val, unit="", advice=""):
        rows.append({
            "metric": label,
            "master": master_val,
            "self": self_val,
            "unit": unit,
            "advice": advice,
        })

    m_wr, s_wr = m_exits.get("winRatePct"), self_summary.get("winRatePct")
    add_row(
        "交易胜率", m_wr, s_wr, "%",
        "先对照选股环境与买点结构，减少逆情绪出手" if (m_wr is not None and s_wr is not None and s_wr + 8 < m_wr) else "胜率接近，重点看盈亏比与止损控制",
    )
    m_pr, s_pr = m_exits.get("payoffRatio"), self_summary.get("payoffRatio")
    add_row(
        "盈亏比 (均盈/均亏)", m_pr, s_pr, "倍",
        "盈亏比偏低通常是「赚一点就跑、亏了死扛」导致，优先严守止损线与分批止盈" if (m_pr is not None and s_pr is not None and s_pr < m_pr * 0.8) else "盈亏结构尚可，保持截断亏损纪律",
    )
    m_hw, s_hw = m_exits.get("avgHoldDaysWin"), self_summary.get("avgHoldDaysWin")
    m_hl, s_hl = m_exits.get("avgHoldDaysLoss"), self_summary.get("avgHoldDaysLoss")
    add_row(
        "盈利单平均持有天数", m_hw, s_hw, "天",
        "对比大佬持盈耐心，尝试用移动止盈替代过早全清" if (m_hw is not None and s_hw is not None and s_hw < m_hw * 0.7) else "持盈周期与大佬接近",
    )
    add_row(
        "亏损单平均持有天数", m_hl, s_hl, "天",
        "你的亏损单持有明显偏长！推土机曲线最核心的是买错立刻认错砍仓" if (m_hl is not None and s_hl is not None and s_hl > (m_hl or 1) + 1.5) else "止损速度控制较好",
    )
    m_wl, s_wl = m_exits.get("worstLossPct"), self_summary.get("worstLossPct")
    add_row(
        "最大单笔亏损", m_wl, s_wl, "%",
        "存在单笔大面拖垮净值，必须设置收盘破位/硬止损条件单" if (m_wl is not None and s_wl is not None and s_wl < m_wl - 3) else "单笔尾部风险受控",
    )
    add_row(
        "日均持股数量", m_pos.get("avgHoldingCount"), self_summary.get("avgHoldingCount"), "只",
        "避免过度分散成杂货铺，也避免无风控单吊",
    )
    return {
        "selfAccountName": self_summary.get("accountName") or "我的实盘账户",
        "selfTradeCount": self_summary.get("tradeCount", 0),
        "selfClosedCount": self_summary.get("closedCount", 0),
        "rows": rows,
    }


def analyze_master_trades(owner, master: dict, trades: list[dict], progress=None) -> dict:
    """Main entry point: reverse-engineer a master's entire trade database."""
    if not trades:
        return {
            "version": VERSION,
            "masterId": master["id"],
            "masterName": master["name"],
            "empty": True,
            "summary": {"tradeCount": 0, "buyCount": 0, "sellCount": 0, "uniqueStocks": 0},
            "warnings": ["该大佬数据库暂无成交记录，请先导入交割单图片、表格或手工录入。"],
        }

    chosen = owner._get("path")
    root = None
    warnings: list[str] = []
    if chosen:
        try:
            root = tdx.normalize_directory(chosen)
        except ValueError as exc:
            warnings.append(str(exc))

    first_date = trades[0]["tradeDate"]
    last_date = trades[-1]["tradeDate"]
    unique_symbols = sorted({t["symbol"] for t in trades if t.get("symbol")})
    buy_dates = {t["tradeDate"] for t in trades if t["side"] == "buy"}

    # 1. Load local daily bars for every traded stock
    stock_bars: dict[str, tuple[dict[str, dict], list[str]]] = {}
    name_by_sym = {t["symbol"]: t["name"] for t in trades}
    matched_stocks = 0
    for idx, sym in enumerate(unique_symbols, 1):
        if progress and (idx == 1 or idx % 20 == 0 or idx == len(unique_symbols)):
            progress("读取交割单标的日线", idx, len(unique_symbols))
        bars = _load_stock_daily(root, sym)
        if bars:
            matched_stocks += 1
        stock_bars[sym] = _enrich_bar_indicators(bars, sym, name_by_sym.get(sym, ""))

    # 2. Scan full market environment & cross-section cohorts over [first_date..last_date]
    active_days, env_by_date, cross_section, market_cov = _scan_market_environment(
        owner, root, first_date, last_date, buy_dates, progress=progress
    )
    warnings.extend(market_cov.get("warnings") or [])

    # 3. Reconstruct episodes + enriched trades (including optional local .lc1 1m bars)
    intraday_cache: dict = {}
    episodes, enriched_trades = _reconstruct_episodes(
        trades, stock_bars, env_by_date, cross_section, intraday_cache, root
    )

    # 4. Reconstruct daily portfolio curve, drawdown & position sizing
    curve, capital_meta = _reconstruct_daily_curve(master, trades, active_days, stock_bars, env_by_date)

    # 5. Aggregate all 5 analytical pillars
    environment = _build_environment_analysis(curve, episodes, env_by_date)
    selection = _build_selection_analysis(enriched_trades, episodes)
    position, exits = _build_position_and_exit_analysis(curve, episodes, enriched_trades, capital_meta)
    guidance = _build_dual_track_guidance(selection, position, exits, environment, episodes, enriched_trades)

    intraday_matched = sum(1 for t in enriched_trades if t.get("intraday"))
    bar_matched = sum(1 for t in enriched_trades if t.get("bar"))
    if root is None and not chosen:
        if bar_matched > 0:
            warnings.append("尚未配置本地通达信目录，已自动启用内置 2022–2026 顽主杯真实日线行情库完成 K 线与大盘情绪对齐；配置本地通达信后可扩展至全市场最新日线与 1 分钟分时。")
        else:
            warnings.append("尚未在「设置 → 数据源」配置通达信目录，当前仅展示账本统计；配置本地通达信日线后可自动对齐大盘情绪、个股K线、MFE/MAE 与同日市场分位。")
    if root and bar_matched < len(enriched_trades):
        warnings.append(f"有 {len(enriched_trades) - bar_matched} 笔记录未在本地日线中找到当日 K 线（可能为休市日录入偏差、未下载对应市场或新旧代码变更）。")

    # Sort episodes newest first for UI display, keeping top winners & worst losers highlighted
    episodes_desc = sorted(episodes, key=lambda e: (e["entryDate"], e["id"]), reverse=True)
    closed_sorted_pnl = sorted([e for e in episodes if e["closed"]], key=lambda e: e["returnPct"] or 0.0, reverse=True)

    return {
        "version": VERSION,
        "masterId": master["id"],
        "masterName": master["name"],
        "styleTag": master.get("styleTag") or "",
        "empty": False,
        "dateRange": {"firstDate": first_date, "lastDate": last_date, "tradingDays": len(curve)},
        "summary": {
            "tradeCount": len(trades),
            "buyCount": sum(1 for t in trades if t["side"] == "buy"),
            "sellCount": sum(1 for t in trades if t["side"] == "sell"),
            "uniqueStocks": len(unique_symbols),
            "episodeCount": len(episodes),
            "closedEpisodes": exits["closedCount"],
            "openEpisodes": exits["openCount"],
            **capital_meta,
            "winRatePct": exits["winRatePct"],
            "payoffRatio": exits["payoffRatio"],
            "profitFactor": exits["profitFactor"],
            "avgHoldDaysWin": exits["avgHoldDaysWin"],
            "avgHoldDaysLoss": exits["avgHoldDaysLoss"],
            "avgPositionPct": position["avgPositionPct"],
            "avgHoldingCount": position["avgHoldingCount"],
        },
        "coverage": {
            "tdxConfigured": root is not None,
            "tradedStocksMatched": matched_stocks,
            "tradedStocksTotal": len(unique_symbols),
            "tradesWithDailyBar": bar_matched,
            "tradesWithIntraday": intraday_matched,
            "marketEnvDays": len(env_by_date),
            "marketStockCount": market_cov.get("stockCount", 0),
        },
        "curve": curve,
        "environment": environment,
        "selection": selection,
        "position": position,
        "exits": exits,
        "guidance": guidance,
        "episodes": episodes_desc,
        "representativeCases": {
            "topWins": closed_sorted_pnl[:5],
            "worstLosses": list(reversed(closed_sorted_pnl[-5:])) if len(closed_sorted_pnl) > 0 else [],
        },
        "enrichedTrades": list(reversed(enriched_trades)),
        "warnings": list(dict.fromkeys(warnings)),
    }
