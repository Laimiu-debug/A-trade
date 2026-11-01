"""Current, end-of-day screening with the three unchanged research detectors.

The market context is descriptive. It does not change admissions, optimize any
threshold, run an account or claim that the present cohort is a historical one.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean, median

from . import tdx, trading_calendar
from .experimental_strategies import IDS, build_signals, _check_stop, _dataset
from .market_research import read_raw_tail
from .portfolio_universe import _configured_source, board_of

VERSION = "experimental-current-scan-v1"
BOARDS = ("main", "chinext", "star")
BOARD_NAMES = {"main": "主板", "chinext": "创业板", "star": "科创板"}
NAMES = dict(zip(IDS, ("收缩突破·停滞退出", "持续强势·再次突破", "支撑收回·波动跟踪")))
TZ = timezone(timedelta(hours=8))


def _progress(callback, phase, completed=0, total=0, **extra):
    if callback:
        callback({"phase": phase, "completed": completed, "total": total, **extra})


def scan_clock(now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError("扫描时钟需要明确时区")
    now = now.astimezone(TZ)
    today = now.date()
    # During the session, a partially written daily bar is never a close signal.
    end = today if (now.hour, now.minute) >= (15, 15) else today - timedelta(days=1)
    sessions = trading_calendar.trading_sessions((end - timedelta(days=35)).isoformat(), end.isoformat())
    if not sessions:
        raise ValueError("当前日期不在已声明交易日历范围内，请先更新交易日历")
    return {"requestedDate": today.isoformat(), "expectedAsOfDate": sessions[-1], "requestedAt": now.isoformat()}


def load_current_dataset(directory, *, clock=None, progress=None, cancelled=None):
    """Read configured TDX directly, preserving all current board members.

    No market database is opened for writing. Independent planned exchange
    sessions come from trading_calendar, not from a union of observed prices.
    """
    clock = clock or scan_clock()
    _check_stop(cancelled)
    end = clock["expectedAsOfDate"]
    root, names, warnings = _configured_source(directory)
    paths, failures = {}, []
    for market in ("sh", "sz"):
        folder = tdx.within(root / "vipdoc" / market / "lday", root)
        if not folder.is_dir():
            failures.append({"symbol": None, "reason": market + " 日线目录缺失"})
            continue
        for path in sorted(folder.glob("*.day")):
            stem = path.stem.lower()
            symbol = market + stem if re.fullmatch(r"\d{6}", stem) else stem
            try:
                source_market, _ = tdx.symbol_parts(symbol)
                if source_market != market or board_of(symbol) not in BOARDS:
                    continue
                safe = tdx.within(path, root)
                if symbol in paths:
                    failures.append({"symbol": symbol, "reason": "重复来源，保留标准文件名"})
                    if stem != symbol:
                        continue
                paths[symbol] = safe
            except ValueError:
                failures.append({"symbol": symbol, "reason": "证券路径或身份无效"})
    if not paths:
        raise ValueError("已配置目录没有沪深 A 股日线，请先在行情设置中选择并下载数据")
    securities, signatures = [], {}
    for completed, (symbol, path) in enumerate(sorted(paths.items()), 1):
        _check_stop(cancelled)
        try:
            raw, _ = read_raw_tail(path, 330, end)
            rows = tdx.parse_daily(raw) if raw else []
            signatures[symbol] = hashlib.sha256(raw).hexdigest()
            securities.append({"symbol": symbol, "name": names.get(symbol, symbol), "board": board_of(symbol),
                "bars": rows, "adjustment": "none", "sourceSha256": signatures[symbol],
                "localCoverageOnly": True, "historicalNameKnown": False})
        except (ValueError, OSError):
            failures.append({"symbol": symbol, "reason": "日线缺失、无效或正在更新"})
            securities.append({"symbol": symbol, "name": names.get(symbol, symbol), "board": board_of(symbol), "bars": []})
        if completed % 100 == 0 or completed == len(paths):
            _progress(progress, "读取本地日线", completed, len(paths))
    benchmark = None
    try:
        raw, _ = read_raw_tail(tdx.daily_path(root, "sh000300"), 330, end)
        if raw:
            benchmark = {"symbol": "sh000300", "bars": tdx.parse_daily(raw)}
            signatures["sh000300"] = hashlib.sha256(raw).hexdigest()
    except (ValueError, OSError):
        warnings.append("沪深300日线缺失；依赖相对强度的两套策略无法评估。")
    if benchmark is None and not any("沪深300" in warning for warning in warnings):
        warnings.append("沪深300截止日前没有可用日线；依赖相对强度的两套策略无法评估。")
    benchmark_latest = benchmark["bars"][-1]["date"] if benchmark else None
    last_dates = Counter(s["bars"][-1]["date"] for s in securities if s["bars"])
    if not last_dates:
        raise ValueError("未读到可用的已完成日线")
    # Most recent date among the modal stock dates; an isolated later stock
    # cannot move the entire cohort clock. A stale benchmark is kept visible.
    stock_date = max(last_dates, key=lambda d: (last_dates[d], d))
    as_of = min(end, stock_date, benchmark["bars"][-1]["date"] if benchmark else end)
    covered_years = sorted(trading_calendar.CALENDAR_SOURCES)
    first = max(min(b["date"] for s in securities for b in s["bars"]), f"{covered_years[0]}-01-01")
    calendar = trading_calendar.trading_sessions(first, as_of)
    if not calendar or len(calendar) < 120:
        raise ValueError("独立交易日历与本地日线交集不足120个交易日，无法运行三组实验扫描")
    allowed = set(calendar)
    off_calendar = 0
    for source in securities + ([benchmark] if benchmark else []):
        off_calendar += sum(first <= b["date"] <= as_of and b["date"] not in allowed for b in source["bars"])
        source["bars"] = [b for b in source["bars"] if b["date"] in allowed]
    if off_calendar:
        warnings.append(f"有 {off_calendar} 条非声明交易日日线未参与计算。")
    lag_days = trading_calendar.trading_sessions(as_of, end)
    lag = len(lag_days) - 1 if lag_days else None
    data = {"calendar": calendar, "calendarSource": "published-exchange-planned-sessions:service.trading_calendar",
        "priceBasisId": "raw-unadjusted", "adjustment": "none", "securities": securities, "benchmark": benchmark}
    metadata = {**clock, "asOfDate": as_of, "stale": as_of < end, "lagSessions": lag,
        "source": {"kind": "TDX local raw daily", "directory": str(root), "stockModalLatestDate": stock_date,
            "benchmarkLatestDate": benchmark_latest,
            "calendarSources": {str(y): trading_calendar.CALENDAR_SOURCES[y] for y in sorted({int(d[:4]) for d in calendar})},
            "calendarBasis": "交易所已发布休市安排；不预测临时休市", "windowRecords": 330,
            "fingerprint": hashlib.sha256(json.dumps(signatures, sort_keys=True).encode()).hexdigest(),
            "scope": "当前本地沪深文件，按板分别排名；不含北交所", "priceBasis": "通达信未复权原始价"},
        "failures": failures, "warnings": warnings}
    _check_stop(cancelled)
    return data, metadata


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _metric(label, value, unit="", denominator=None):
    result = {"label": label, "value": value, "unit": unit}
    if denominator is not None:
        result["denominator"] = denominator
    return result


def market_context(dataset, securities):
    """Complete aligned windows; unknown inputs never become zero or a vote."""
    days = dataset["calendar"]
    amounts, r20, above20, above60, advancing, matched_now, matched_prior = [], [], [], [], [], [], []
    present = 0
    for security in securities:
        by_day = {b["date"]: b for b in security["bars"]}
        if days[-1] not in by_day:
            continue
        present += 1

        def window(n, lag=0):
            selected = days[len(days) - lag - n:len(days) - lag if lag else len(days)]
            rows = [by_day.get(d) for d in selected]
            return rows if len(rows) == n and all(r and _number(r.get("close")) and r["close"] > 0
                and _number(r.get("volume")) and r["volume"] > 0 for r in rows) else None

        w2, w20, w21, w60 = window(2), window(20), window(21), window(60)
        if w2:
            advancing.append(w2[-1]["close"] > w2[0]["close"])
        if w20:
            above20.append(w20[-1]["close"] > mean(r["close"] for r in w20))
        if w60:
            above60.append(w60[-1]["close"] > mean(r["close"] for r in w60))
        if w21:
            current_return = w21[-1]["close"] / w21[0]["close"] - 1
            if _number(current_return):
                r20.append(current_return)
            if all(_number(r.get("amount")) and r["amount"] > 0 for r in w21):
                amount_ratio = w21[-1]["amount"] / mean(r["amount"] for r in w21[:-1])
                if _number(amount_ratio):
                    amounts.append(amount_ratio)
            old = window(21, 5)
            if old:
                old_return = old[-1]["close"] / old[0]["close"] - 1
                if _number(current_return) and _number(old_return):
                    matched_now.append(current_return)
                    matched_prior.append(old_return)
    rate = lambda values: 100 * sum(values) / len(values) if values else None
    a20, a60, participation = rate(above20), rate(above60), rate([r > 0 for r in r20])
    breadth_change = (rate([r > 0 for r in matched_now]) - rate([r > 0 for r in matched_prior])) if matched_now else None
    label = "数据不足" if a20 is None or a60 is None else (
        "趋势扩散" if a20 >= 60 and a60 >= 50 else "弱势收缩" if a20 < 40 and a60 < 50 else "震荡分化")
    metrics = [_metric("站上20日均线占比", a20, "%", len(above20)),
        _metric("站上60日均线占比", a60, "%", len(above60)),
        _metric("当日上涨占比", rate(advancing), "%", len(advancing)),
        _metric("20日正收益参与度", participation, "%", len(r20)),
        _metric("20日收益中位数", 100 * median(r20) if r20 else None, "%", len(r20)),
        _metric("同股参与度较5日前变化", breadth_change, "百分点", len(matched_now)),
        _metric("成交额/前20日均额中位数", median(amounts) if amounts else None, "倍", len(amounts))]
    return {"label": label, "summary": f"本地样本 {len(securities)} 只，截止日有日线 {present} 只；指标分母仅含对应完整观察窗口。",
        "metrics": metrics, "sampleCount": len(securities), "asOfCount": present,
        "amountBasis": "仅完整21槽正有限close、volume、amount共同子集；个股当日额/自身前20槽均额的中位数，不是独立成交额覆盖或全市场总额",
        "classificationCoverage": {"ma20Known": len(above20), "ma60Known": len(above60),
            "complete": bool(securities) and len(above20) == len(above60) == len(securities)}}


def _candidate(strategy_id, signal, evidence, security):
    symbol = signal["symbol"]
    values, reason = [], ""
    if strategy_id == IDS[0]:
        row = next(r for r in evidence["dailyRows"][-1]["ranking"] if r["symbol"] == symbol)
        f = row["features"]
        reason = "突破前40日高点；80日均线上行；三段振幅与末段成交量收缩；成交量达到前40日均量1.2倍。"
        values = [_metric("前40日高点", f["prior40High"], "元"), _metric("80日均线", f["ma80"], "元"),
            _metric("突破量/前40日均量", f["volumeRatio40"], "倍"),
            *[_metric(f"第{i+1}段振幅", b["rangeRatio"] * 100, "%") for i, b in enumerate(f["contractionBlocks"])],
            _metric("末段/首段均量", f["contractionBlocks"][-1]["meanVolume"] / f["contractionBlocks"][0]["meanVolume"], "倍")]
    elif strategy_id == IDS[1]:
        row = next(r for r in evidence["opportunities"] if r["symbol"] == symbol)
        v = row["parentCandidate"]["currentEvidence"]["values"]
        reason = "当前及10个交易日前均满足五项持续强势条件；当前突破前5日高点；先排Top5，再核验历史持续性。"
        values = [_metric("20日平均强度分位", v["meanStrengthPercentile20"] * 100, "%"),
            _metric("20日最低强度分位", v["minimumStrengthPercentile20"] * 100, "%"),
            _metric("60日相对收益分位", v["relativeReturn60Percentile"] * 100, "%"),
            _metric("20日收盘路径最大回撤", v["closePathMaxDrawdown20"] * 100, "%"),
            _metric("20日路径效率", v["signedPathEfficiency20"], ""),
            _metric("历史强势核验日", row["peerFormationDate"], "")]
    else:
        row = next(r for r in evidence["opportunities"] if r["symbol"] == symbol)
        reason = "此前60日上涨且相对强度进入本板前20%；盘中跌破前20日支撑，收盘重新站回；按此前20日成交额排序。"
        values = [_metric("前20日支撑", row["support"], "元"), _metric("当日最低价", row["formationLow"], "元"),
            _metric("此前60日收益", row["priorOwn60"] * 100, "%"),
            _metric("此前60日相对强度分位", row["priorRank60"] * 100, "%")]
    warnings = ["日线条件命中；次日成交、停牌和涨跌停状态仍需确认。"]
    if "ST" in security.get("name", "").upper() or "退" in security.get("name", ""):
        warnings.append("当前名称含风险标识，需单独核实；原始排名未回填替换。")
    return {"symbol": symbol, "name": security.get("name", symbol), "board": security["board"],
        "signalDate": signal["signalDate"], "close": security["bars"][-1]["close"],
        "rank": signal["originalRank"], "reason": reason, "evidence": values, "warnings": warnings}


def analyze_dataset(dataset, metadata, *, progress=None, cancelled=None, builder=build_signals):
    """Analyze one already bounded independent-calendar snapshot, no outcomes."""
    day = metadata["asOfDate"]
    if not dataset.get("calendar") or dataset["calendar"][-1] != day:
        raise ValueError("扫描截止日与独立日历不一致")
    _check_stop(cancelled)
    _dataset(dataset, day, day)
    if dataset.get("priceBasisId") != "raw-unadjusted":
        raise ValueError("扫描输入必须明确原始未复权价格基准")
    securities = dataset["securities"]
    if any(s.get("board") not in BOARDS or s["board"] != board_of(s["symbol"]) for s in securities):
        raise ValueError("股票身份与扫描板块不一致")
    lookup = {s["symbol"]: s for s in securities}
    context = market_context(dataset, securities)
    board_contexts = {b: market_context(dataset, [s for s in securities if s["board"] == b]) for b in BOARDS}
    context["boards"] = [{"board": b, **v} for b, v in board_contexts.items()]
    context["methodology"] = "描述性分档：MA20占比≥60%且MA60占比≥50%为趋势扩散；分别<40%、<50%为弱势收缩；其余震荡分化。未知不计0。这些分档未验证收益，不改变三套策略原始准入。"
    benchmark = dataset.get("benchmark")
    if benchmark:
        bm = {b["date"]: b for b in benchmark["bars"]}
        recent = [bm.get(d) for d in dataset["calendar"][-61:]]
        if len(recent) == 61 and all(r and _number(r.get("close")) and r["close"] > 0 for r in recent):
            context["metrics"] += [_metric("沪深300近20日收益", (recent[-1]["close"] / recent[-21]["close"] - 1) * 100, "%"),
                _metric("沪深300较60日均线", (recent[-1]["close"] / mean(r["close"] for r in recent[-60:]) - 1) * 100, "%")]
    results = []
    for strategy_id in IDS:
        _check_stop(cancelled)
        selected_boards = ("main",) if strategy_id == IDS[0] else BOARDS
        candidates, board_results = [], []
        for board in selected_boards:
            cohort = [s for s in securities if s["board"] == board]
            _progress(progress, f"{NAMES[strategy_id]} · {BOARD_NAMES[board]}", 0, len(cohort))
            if not cohort or strategy_id != IDS[0] and not benchmark:
                board_results.append({"board": board, "signalCount": 0, "evaluatedCount": 0,
                    "status": "unavailable", "reason": "股票池或沪深300基准缺失"})
                continue
            view = {**dataset, "securities": cohort}
            def report(value):
                _check_stop(cancelled)
                _progress(progress, f"{NAMES[strategy_id]} · {BOARD_NAMES[board]}",
                    value.get("completedSymbols", value.get("completed", 0)),
                    value.get("totalSymbols", value.get("total", len(cohort))))
            built = builder(strategy_id, view, start=day, end=day, progress=report, cancelled=cancelled)
            _check_stop(cancelled)
            evidence = built["evidence"]
            daily = evidence["dailyRows"][-1]
            if strategy_id == IDS[0]:
                counts = daily["counts"]
                evaluated = sum(v for k, v in counts.items() if k != "unevaluable")
            elif strategy_id == IDS[1]:
                counts = daily["parentActivationCounts"]
                evaluated = sum(v for k, v in counts.items() if k != "unknown")
            else:
                counts = daily["counts"]
                evaluated = (counts["rankPopulationCount"] - counts["amountUnknownCount"]
                    - counts["formationPriceUnknownCount"]) if daily["benchmarkPrior60"]["complete"] else 0
            for signal in built["schedule"]:
                if signal["signalDate"] != day:
                    raise ValueError("扫描结果夹入了其他交易日信号")
                security = lookup[signal["symbol"]]
                if not security["bars"] or security["bars"][-1]["date"] != day:
                    raise ValueError("旧日证券不能混入当日候选")
                candidates.append(_candidate(strategy_id, signal, evidence, security))
            board_results.append({"board": board, "signalCount": len(built["schedule"]), "evaluatedCount": evaluated,
                "sampleCount": len(cohort), "status": "evaluated" if evaluated else "unavailable", "counts": counts})
        available_boards = [b for b in selected_boards if board_contexts[b]["sampleCount"]]
        modes = [board_contexts[b]["label"] for b in available_boards]
        complete = len(available_boards) == len(selected_boards) and all(
            board_contexts[b]["classificationCoverage"]["complete"] for b in available_boards)
        known_modes = [mode for mode in modes if mode != "数据不足"]
        coverage_suffix = "（覆盖不完整）" if not complete else ""
        fit_label = "环境数据不足" if not known_modes else (
            "已观察样本偏弱，谨慎观察" if any(mode == "弱势收缩" for mode in known_modes) else (
                "已知样本趋势扩散" + coverage_suffix if all(mode == "趋势扩散" for mode in known_modes)
                else "已观察样本震荡分化" + coverage_suffix))
        explanations = {
            IDS[0]: "关注收缩后的放量突破能否获得市场参与；本策略只按已研究主板规则筛选。",
            IDS[1]: "关注持续强股的延续；普涨修复初期的新领涨股可能因10日前不强而被排除。",
            IDS[2]: "关注强势股回调后收回支撑；市场弱时收回可能再失守，旧期回测三板均亏。"}
        results.append({"strategyId": strategy_id, "name": NAMES[strategy_id], "candidates": candidates,
            "counts": {"signals": len(candidates), "evaluated": sum(r["evaluatedCount"] for r in board_results)},
            "environmentFit": {"label": fit_label, "summary": explanations[strategy_id] + " 环境提示不改变原始触发规则。"},
            "boards": board_results, "limitations": ["股票池为当前本地覆盖，每板独立排名；不是历史冻结样本的复验。",
                "选股是原始价量形态匹配，不证明主力身份或保证收益。"]})
    coverage = {"loaded": len(securities), "asOfCount": context["asOfCount"],
        "asOfPct": 100 * context["asOfCount"] / len(securities) if securities else None,
        "failed": len(metadata.get("failures", [])), "boardCounts": dict(Counter(s["board"] for s in securities))}
    warnings = list(metadata.get("warnings", []))
    if metadata.get("stale"):
        warnings.insert(0, f"本地可比较日线截至 {day}，落后应有截止日 {metadata['expectedAsOfDate']}；结果不是今天的新信号，请先更新通达信。")
    if coverage["asOfCount"] < coverage["loaded"] or coverage["failed"]:
        warnings.append("部分股票缺失截止日日线或读取失败；缺失不补值，相对排名可能受覆盖影响。")
    warnings += ["原始价格未复权，权益事件可能影响形态；不推断真实主力资金或盘中情绪。",
        "三组为研究后保留的实验策略，尚无跨环境稳定盈利结论。"]
    _check_stop(cancelled)
    return {"version": VERSION, **metadata, "coverage": coverage, "environment": context, "strategies": results,
        "warnings": warnings, "executionNotice": "收盘后筛选；原规则最早在下一交易日尝试买入，本页面不下单。过期信号不能视为当前买入指令。",
        "newResearchTrial": False, "certified": False, "targetAchieved": False}


def run_current_scan(directory, *, progress=None, cancelled=None):
    dataset, metadata = load_current_dataset(directory, progress=progress, cancelled=cancelled)
    return analyze_dataset(dataset, metadata, progress=progress, cancelled=cancelled)
