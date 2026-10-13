"""Causal, complete-cross-section candidate scores for a strategy portfolio.

Individual events stay individual events. A missing golden cross today is not a
forced exit. Cross-strategy AND exists only in an explicit intersection node.
"""
from __future__ import annotations

from array import array
from collections import Counter, deque
import copy
import datetime as dt
import math
from statistics import stdev

from service.strategies import (FORCE_VERSION, FORCE_WARMUP, evaluate,
                               prepare_bars, _force_state, _positive_ratio, _ratio_score)
from service.portfolio_config import POOL_LABELS, catalogue, normalize_config, normalize_screen_config

SCORER_VERSION = "portfolio-causal-reference-adjusted-midrank-v2"
KINDS = {s["id"] for s in catalogue()} | {"forcePurpleToYellow"}
FORCE_KINDS = {"forceGoldenCross", "forcePurpleToYellow", "forceTrend"}
FORCE_KEYS = ("mainForce", "retailForce", "forceDominance", "signalScore", "mainForcePowerScore",
              "forceGapScore", "mainForceExplosionRatio", "mainVsRetailRatio")


def _valid_day(value):
    if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
        raise ValueError("评分日期必须为 YYYY-MM-DD")
    return value


def _finite(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def _same_ma_price(left, right):
    # Prefix-sum subtraction can leave roundoff on mathematically equal MAs.
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def _ma_above(left, right):
    return left > right and not _same_ma_price(left, right)


def _condition(key, label, actual, operator, threshold, unit="", *, enabled=True, stable_gt=False):
    if not enabled:
        passed = True
    elif actual is None or threshold is None:
        passed = None
    elif operator == ">":
        passed = _ma_above(actual, threshold) if stable_gt else actual > threshold
    elif operator == ">=":
        passed = actual >= threshold
    elif operator == "<=":
        passed = actual <= threshold
    elif operator == "<":
        passed = actual < threshold
    elif operator == "between":
        passed = threshold[0] <= actual <= threshold[1]
    else:
        passed = actual == threshold
    return {"key": key, "label": label, "actual": actual, "operator": operator,
            "threshold": threshold, "unit": unit, "enabled": enabled, "required": enabled, "passed": passed,
            "status": "unknown" if passed is None else "pass" if passed else "fail"}


def _combine_conditions(values, mode="intersection"):
    if mode == "intersection":
        return False if False in values else None if None in values else True
    return True if True in values else None if None in values else False


def _pool_evidence(conditions, *, mode="intersection", passed=None):
    if passed is None:
        passed = _combine_conditions([item["passed"] for item in conditions], mode)
    return {"status": "unknown" if passed is None else "pass" if passed else "fail",
            "passed": passed, "inPool": passed is True, "conditions": conditions}


def rank_normalize(rows):
    """Midrank/n: singleton=.5; equal raw scores stay equal; no batch-local ranks."""
    ordered = sorted(rows, key=lambda row: (row["rawScore"], row["symbol"]))
    size, start = len(ordered), 0
    while start < size:
        stop = start + 1
        while stop < size and ordered[stop]["rawScore"] == ordered[start]["rawScore"]:
            stop += 1
        score = (start + (stop - start) / 2) / size
        for row in ordered[start:stop]:
            row["score"] = score
        start = stop
    return sorted(ordered, key=lambda row: (-row["score"], row["symbol"]))


def _seeded_force(bars, references, seed):
    """Resume the existing formula exactly; source seed is before first retained bar."""
    if seed.get("version") != FORCE_VERSION or seed.get("previousDate", "") >= bars[0]["date"]:
        raise ValueError("主散前置状态版本或日期不匹配")
    offset = seed.get("observationsBefore")
    if type(offset) is not int or offset < 4:
        raise ValueError("主散前置状态观测数无效")
    up_queue, down_queue = deque(seed["upQueue"]), deque(seed["downQueue"])
    if len(up_queue) != 3 or len(down_queue) != 3 or any(not _finite(v) for v in [*up_queue, *down_queue]):
        raise ValueError("主散前置方向成交量状态无效")
    main, retail, older_main = seed["main"], seed["retail"], seed["previousMain"]
    previous_close = seed["previousClose"]
    if any(not _finite(v) for v in (main, retail, older_main)) or not _finite(previous_close, True):
        raise ValueError("主散前置EMA状态无效")
    up_sum, down_sum = seed.get("upSum", sum(up_queue)), seed.get("downSum", sum(down_queue))
    for index, bar in enumerate(bars):
        reference = references.get(bar["date"], previous_close)
        up = bar["volume"] / 100 if bar["close"] > reference else 0.
        down = bar["volume"] / 100 if bar["close"] < reference else 0.
        up_queue.append(up)
        down_queue.append(down)
        up_sum += up
        down_sum += down
        up_sum -= up_queue.popleft()
        down_sum -= down_queue.popleft()
        prior_main, prior_retail = main, retail
        main = .5 * (up_sum / 3) + .5 * prior_main
        retail = (2 / 11) * (down_sum / 3) + (1 - 2 / 11) * prior_retail
        state, previous_state = _force_state(main, prior_main), _force_state(prior_main, older_main)
        turn = prior_main > 0 and previous_state == "falling" and main > 0 and state == "rising"
        cross = main > retail and prior_main <= prior_retail
        dominance = (main - retail) / (main + retail) if main + retail > 0 else None
        growth = _positive_ratio(main, prior_main, 12)
        prev_growth = _positive_ratio(prior_main, older_main, 12)
        explosion = _positive_ratio(main, max((prior_main + older_main) / 2, 0), 15)
        acceleration = _positive_ratio(growth, max(prev_growth, 1), 6)
        versus = _positive_ratio(main, retail, 8)
        power = min(100., max(0., .45 * _ratio_score(growth, 12) + .40 * _ratio_score(explosion, 15) + .15 * _ratio_score(acceleration, 6)))
        gap = min(100., max(0., .55 * max(0., dominance or 0.) * 100 + .45 * _ratio_score(versus, 8)))
        state_bonus = (6 if state == "rising" else 0) + (4 if previous_state == "falling" and state == "rising" else 0)
        score = min(100., max(0., 12 + (12 if turn else 0) + (18 if cross else 0) + power * .48 + gap * .32 + state_bonus))
        yield {"mainForce": main, "retailForce": retail, "forceDominance": dominance, "signalScore": round(score, 2),
               "mainForcePowerScore": round(power, 2), "forceGapScore": round(gap, 2),
               "mainForceExplosionRatio": round(explosion, 4), "mainVsRetailRatio": round(versus, 4),
               "goldenCross": cross, "purpleToYellow": turn, "mainForceState": state,
               "prevMainForceState": previous_state}
        previous_close, older_main = bar["close"], prior_main


def _compact_force(bars, references, seed):
    rows = _seeded_force(bars, references, seed) if seed else prepare_bars(bars, references)["force"]
    values = {key: array("d") for key in FORCE_KEYS}
    flags = {key: bytearray() for key in ("goldenCross", "purpleToYellow", "rising", "previousFalling")}
    for row in rows:
        for key in FORCE_KEYS:
            values[key].append(float("nan") if row.get(key) is None else row[key])
        flags["goldenCross"].append(bool(row.get("goldenCross")))
        flags["purpleToYellow"].append(bool(row.get("purpleToYellow")))
        flags["rising"].append(row.get("mainForceState") == "rising")
        flags["previousFalling"].append(row.get("prevMainForceState") == "falling")
    return {**values, **flags}


class _Series:
    def __init__(self, security, need_force):
        self.security = security
        for key in ("listingDate", "delistDate"):
            if security.get(key):
                _valid_day(security[key])
        self.bars = security["bars"]
        self.signal_bars = self.bars
        references = security.get("precloseByDate", {})
        if any(i and day in references and abs(references[day] - self.bars[i - 1]["close"]) > .005
               for i, day in enumerate(bar["date"] for bar in self.bars)):
            self.signal_bars = []
            factor = 1.
            for i, bar in enumerate(self.bars):
                reference = references.get(bar["date"])
                if i and reference is not None and reference > 0 and abs(reference - self.bars[i - 1]["close"]) > max(.005, self.bars[i - 1]["close"] * 1e-7):
                    factor *= self.bars[i - 1]["close"] / reference
                self.signal_bars.append({**bar, **{key: bar[key] * factor for key in ("open", "high", "low", "close")}})
        self.dates, self.by_date = [], {}
        self.sums = {k: array("d", [0.]) for k in ("close", "volume", "amount", "zeros")}
        previous = ""
        for i, bar in enumerate(self.bars):
            day = _valid_day(bar.get("date"))
            if day <= previous:
                raise ValueError("日线日期重复或非递增")
            if any(not _finite(bar.get(k), True) for k in ("open", "high", "low", "close")):
                raise ValueError("日线价格无效")
            if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
                raise ValueError("日线 OHLC 包络无效")
            if not _finite(bar.get("volume")) or not _finite(bar.get("amount", 0)):
                raise ValueError("日线成交量或金额无效")
            self.dates.append(day)
            self.by_date[day] = i
            for key in self.sums:
                value = int(bar["volume"] <= 0) if key == "zeros" else self.signal_bars[i]["close"] if key == "close" else bar.get(key, 0)
                self.sums[key].append(self.sums[key][-1] + value)
            previous = day
        self.offset = int(security.get("historyWindow", {}).get("observationsBefore", 0))
        self.force = _compact_force(self.bars, security.get("precloseByDate", {}), security.get("forceSeed")) if need_force and self.bars else None
        self.maxima = {}

    def mean(self, key, start, stop):
        return (self.sums[key][stop] - self.sums[key][start]) / (stop - start) if stop > start else None

    def peak(self, key, window, index):
        cache_key = (key, window)
        if cache_key not in self.maxima:
            maxima, queue = array("d"), deque()
            for i, row in enumerate(self.signal_bars):
                while queue and queue[0] <= i - window:
                    queue.popleft()
                while queue and self.signal_bars[queue[-1]][key] <= row[key]:
                    queue.pop()
                queue.append(i)
                maxima.append(self.signal_bars[queue[0]][key])
            self.maxima[cache_key] = maxima
        return self.maxima[cache_key][index]

    def force_evidence(self, index):
        result = {key: None if math.isnan(values[index]) else values[index]
                  for key, values in self.force.items() if key in FORCE_KEYS}
        result.update({"goldenCross": bool(self.force["goldenCross"][index]),
                       "purpleToYellow": bool(self.force["purpleToYellow"][index]),
                       "mainForceState": "rising" if self.force["rising"][index] else "not-rising",
                       "formulaVersion": FORCE_VERSION,
                       "directionReference": "exAdjustedPreclose" if self.dates[index] in self.security.get("precloseByDate", {}) else "previousRawClose"})
        return result


class DailyScorer:
    def __init__(self, dataset, config, *, _prepared=None, pools_only=False):
        self.pools_only = pools_only
        self.dataset, self.config = dataset, (normalize_screen_config(config) if pools_only else normalize_config(config))
        self.strategies = [] if pools_only else list(self.config.get("strategies", []))
        self.combinations = [] if pools_only else list(self.config.get("combinations", []))
        defaults = {} if pools_only else {s["id"]: {p["key"]: p["default"] for p in s["parameters"]} for s in catalogue()}
        self.nodes, self.order = {}, []
        for strategy in self.strategies:
            sid, kind = strategy.get("id"), strategy.get("kind")
            if not isinstance(sid, str) or not sid or sid in self.nodes or kind not in KINDS:
                raise ValueError("策略 ID 或类型无效")
            node = {**strategy, "params": {**defaults[kind], **strategy.get("params", {})}, "pool": strategy.get("pool", "base")}
            if node["pool"] not in POOL_LABELS:
                raise ValueError("策略入口池无效")
            self.nodes[sid] = node
            self.order.append(sid)
        for combination in self.combinations:
            sid = combination.get("id")
            if not isinstance(sid, str) or not sid or sid in self.nodes:
                raise ValueError("组合 ID 重复或无效")
            if combination.get("method") not in {"union", "intersection", "weighted"}:
                raise ValueError("组合方法无效")
            if not isinstance(combination.get("children"), list) or not combination["children"]:
                raise ValueError("组合不能为空")
            self.nodes[sid] = combination
        pending, visited = set(), set(self.order)

        def visit(sid):
            if sid in visited:
                return
            if sid in pending:
                raise ValueError("组合存在循环引用")
            if sid not in self.nodes:
                raise ValueError("组合引用未知策略")
            pending.add(sid)
            node = self.nodes[sid]
            children = node.get("children", [])
            if len({c.get("id") for c in children}) != len(children):
                raise ValueError("组合重复引用子策略")
            for child in children:
                if not _finite(child.get("weight", 1)):
                    raise ValueError("组合权重必须是非负有限数")
                visit(child.get("id"))
            if sum(c.get("weight", 1) for c in children) <= 0:
                raise ValueError("组合有效权重不能为零")
            if not _finite(node.get("threshold", 0)) or node.get("threshold", 0) > 1:
                raise ValueError("组合阈值须在 0 至 1")
            pending.remove(sid)
            visited.add(sid)
            self.order.append(sid)
        for combination in self.combinations:
            visit(combination["id"])
        if not self.strategies and not pools_only:
            raise ValueError("至少配置一个单策略")
        need_force = any(s["kind"] in FORCE_KINDS for s in self.strategies)
        self.series, self.load_errors = {}, {}
        for security in dataset.get("securities", []):
            symbol = security.get("symbol")
            if symbol in self.series or symbol in self.load_errors:
                raise ValueError("股票池包含重复证券")
            try:
                prior = _prepared.series.get(symbol) if _prepared is not None else None
                self.series[symbol] = prior if prior is not None and (not need_force or prior.force is not None) else _Series(security, need_force)
            except (TypeError, ValueError, KeyError, OverflowError) as exc:
                self.load_errors[symbol] = str(exc)
        self.rules = {"strictHistory": False, "excludeSt": True, "minListingDays": 60, "minAmount20": 10000000,
                      **self.config.get("universe", {})}
        self.pool_settings = {"trend": {"window": 60, "minReturn": 0},
                              "structure": {"window": 20, "minDrawdown": .02, "maxDrawdown": .25},
                              "volume": {"window": 20, "minRatio": 1.2},
                              "combined": {"pools": ["trend", "structure", "volume"], "mode": "intersection"}}
        for key, settings in self.config.get("poolSettings", {}).items():
            if key in self.pool_settings:
                self.pool_settings[key].update(settings)
        benchmark = dataset.get("benchmark")
        if pools_only:
            self.benchmark = None
        elif _prepared is not None:
            self.benchmark = _prepared.benchmark
        elif isinstance(benchmark, dict) and benchmark.get("bars"):
            # An index only needs closes for regime recognition. These defaults
            # belong to scoring inputs; no index OHLC or volume is used for fills.
            benchmark = {**benchmark, "bars": [{"open": bar["close"], "high": bar["close"], "low": bar["close"], "volume": 0, **bar} for bar in benchmark["bars"]]}
            self.benchmark = _Series(benchmark, False)
        else:
            self.benchmark = None
        self._last_date, self._last_result = None, None

    def _base_values(self, series, day, index, members=None, *, evidence=False):
        security = series.security
        member = security.get("memberHistory", security.get("memberByDate", {})).get(day)
        if members is not None:
            member = security["symbol"] in members
        listing = security.get("listingDate")
        status = security.get("tradeStatusByDate", {}).get(day)
        status = {"0": 0, "1": 1}.get(status, status) if isinstance(status, str) else status
        observed = series.offset + index + 1 if index is not None else None
        if listing and observed is not None:
            # Calendar days only cap the observed-history count; this is not a
            # claim that missing historical observations are exchange sessions.
            observed = min(observed, (dt.date.fromisoformat(day) - dt.date.fromisoformat(listing)).days + 1)
        amplitude = None
        if index is not None and index >= 19 and (evidence or self.rules.get("minAmplitude20") is not None):
            amplitude = sum((bar["high"] - bar["low"]) / bar["close"]
                            for bar in series.signal_bars[index - 19:index + 1]) / 20
        return {"member": member, "listing": listing, "status": status,
                "historicalSt": security.get("stByDate", security.get("stHistory", {})).get(day),
                "observedDays": observed,
                "volume": series.bars[index]["volume"] if index is not None else None,
                "amount20": series.mean("amount", index - 19, index + 1) if index is not None and index >= 19 else None,
                "amplitude20": amplitude}

    def _eligibility(self, series, day, index, members=None, *, values=None):
        security = series.security
        values = self._base_values(series, day, index, members) if values is None else values
        member, listing = values["member"], values["listing"]
        unknown = []
        if security.get("delistDate") and day >= security["delistDate"]:
            return {"eligible": False, "reason": "delisted", "unknown": []}
        if member is False:
            return {"eligible": False, "reason": "notHistoricalMember", "unknown": []}
        if listing and day < listing:
            return {"eligible": False, "reason": "notListed", "unknown": []}
        if index is None:
            first = security.get("historyWindow", {}).get("sourceFirstDate") or (series.dates[0] if series.dates else None)
            return {"eligible": False, "reason": "notObservedYet" if first and day < first else "missingDailyBar", "unknown": []}
        historical_st = values["historicalSt"]
        if type(historical_st) is not bool:
            unknown.append("historicalSt")
        if type(member) is not bool:
            unknown.append("historicalMembership")
        if not listing:
            unknown.append("listingDate")
        if self.rules["strictHistory"] and unknown:
            return {"eligible": False, "reason": "strictHistoryMissing", "unknown": unknown}
        if member is False:
            return {"eligible": False, "reason": "notHistoricalMember", "unknown": unknown}
        if listing and day < listing:
            return {"eligible": False, "reason": "notListed", "unknown": unknown}
        if self.rules["excludeSt"] and historical_st is True:
            return {"eligible": False, "reason": "historicalSt", "unknown": unknown}
        status = values["status"]
        if status == 0 or values["volume"] <= 0:
            return {"eligible": False, "reason": "suspendedOrZeroVolume", "unknown": unknown}
        if status not in (0, 1):
            unknown.append("historicalTradeStatus")
            if self.rules["strictHistory"]:
                return {"eligible": False, "reason": "strictHistoryMissing", "unknown": unknown}
        observed = values["observedDays"]
        if observed < self.rules["minListingDays"]:
            return {"eligible": False, "reason": "insufficientListingHistory", "unknown": unknown, "observedDays": observed}
        average = values["amount20"]
        if self.rules["minAmount20"] > 0 and (average is None or average < self.rules["minAmount20"]):
            return {"eligible": False, "reason": "amount20BelowMinimum", "unknown": unknown, "amount20": average}
        minimum_amplitude = self.rules.get("minAmplitude20")
        if minimum_amplitude is not None and (values["amplitude20"] is None or values["amplitude20"] < minimum_amplitude):
            return {"eligible": False, "reason": "amplitude20BelowMinimum", "unknown": unknown, "amplitude20": values["amplitude20"]}
        return {"eligible": True, "reason": "eligible", "unknown": unknown, "observedDays": observed, "amount20": average,
                "listingAgeBasis": "observed-history-lower-bound"}

    def _base_evidence(self, series, day, index, state, values):
        security = series.security
        known_st = values["historicalSt"] if type(values["historicalSt"]) is bool else None
        member = values["member"] if type(values["member"]) is bool else None
        status = values["status"] if values["status"] in (0, 1) else None
        conditions = [
            _condition("dailyBar", "截止日有有效日线", index is not None, "==", True),
            _condition("notDelisted", "未到已知退市日", not security.get("delistDate") or day < security["delistDate"], "==", True),
            _condition("historicalMember", "属于当日历史股票池", member, "==", True),
            _condition("listingDate", "上市日期不晚于截止日", values["listing"], "<=", day, "date"),
            _condition("historicalSt", "剔除已知历史 ST", known_st, "==", False, enabled=self.rules["excludeSt"]),
            _condition("tradeStatus", "当日可交易状态", status, "==", 1),
            _condition("positiveVolume", "当日成交量大于零", values["volume"], ">", 0, "股"),
            _condition("listingDays", "已观测上市历史达到下限", values["observedDays"], ">=", self.rules["minListingDays"], "日"),
            _condition("amount20", "20 日平均成交额达到下限", values["amount20"], ">=", self.rules["minAmount20"], "元", enabled=self.rules["minAmount20"] > 0),
            _condition("amplitude20", "20 日平均振幅达到下限", values["amplitude20"], ">=", self.rules.get("minAmplitude20"), "ratio", enabled=self.rules.get("minAmplitude20") is not None),
        ]
        missing = [item["key"] for item in conditions if item["key"] in ("historicalMember", "listingDate", "historicalSt", "tradeStatus") and item["actual"] is None]
        for item in conditions:
            if item["key"] in missing:
                if self.rules["strictHistory"]:
                    item.update(required=True, passed=None, status="unknown", note="严格历史资格要求提供该历史字段。")
                else:
                    item.update(required=False, passed=None, status="unknown", note="历史字段缺失；未启用严格历史资格，不因此排除。")
        conditions.append(_condition("strictHistory", "严格历史资格字段完整", len(missing), "==", 0, "项", enabled=self.rules["strictHistory"]))
        evidence = _pool_evidence(conditions, passed=state["eligible"])
        evidence.update(reason=state["reason"], inPool=state["eligible"], unknownFields=missing,
                        strictHistory=self.rules["strictHistory"], listingAgeBasis="observed-history-lower-bound")
        if (state["reason"] == "strictHistoryMissing"
                or state["reason"] == "amount20BelowMinimum" and values["amount20"] is None
                or state["reason"] == "amplitude20BelowMinimum" and values["amplitude20"] is None):
            evidence.update(status="unknown", passed=None)
        return evidence

    def _pools(self, series, index, *, evidence=False, ranking=None):
        """One set of conditions drives both membership and human-readable evidence."""
        known = series is not None and index is not None
        close = series.signal_bars[index]["close"] if known else None
        details, values = {}, {}
        p = self.pool_settings["trend"]
        window = int(p["window"])
        ready = known and index >= window
        average = series.mean("close", index - window + 1, index + 1) if ready else None
        previous = series.mean("close", index - window, index) if ready else None
        change = close / series.signal_bars[index - window]["close"] - 1 if ready else None
        details["trend"] = _pool_evidence([
            _condition("closeAboveAverage", "收盘价高于趋势窗口均线", close, ">", average, "元"),
            _condition("averageRising", "趋势窗口均线高于前一日均线", average, ">", previous, "元"),
            _condition("windowReturn", "趋势窗口涨幅达到下限", change, ">=", p["minReturn"], "ratio"),
            _condition("returnRank", "完整基础池有效样本涨幅排名在范围内", ranking.get("rank") if ranking else None, "between",
                       [p.get("rankStart", 1), p.get("rankEnd", 500)], "名", enabled=p.get("rankEnabled", False)),
        ])
        details["trend"].update(window=window, requiredObservations=window + 1,
                                observedObservations=index + 1 if known else 0,
                                priceBasis="causalReferenceAdjustedSignals",
                                rankWindow=p.get("rankWindow", 40), rankUniverseCount=ranking.get("universeCount", 0) if ranking else 0,
                                rankUnavailableCount=ranking.get("unavailableCount", 0) if ranking else 0,
                                tiePolicy="return-desc-symbol-asc")
        if ranking and ranking.get("note"):
            details["trend"]["conditions"][-1]["note"] = ranking["note"]
        values["trendReturnPct"] = change * 100 if change is not None else None
        values["returnRank"] = ranking.get("rank") if ranking else None
        values["rankReturnPct"] = ranking["return"] * 100 if ranking and ranking.get("return") is not None else None
        p = self.pool_settings["structure"]
        window = int(p["window"])
        ready = known and index >= window - 1
        peak_basis = p.get("peakBasis", "close")
        peak = series.peak(peak_basis, window, index) if ready else None
        drawdown = 1 - close / peak if ready else None
        peak_age = None
        if ready and (evidence or p.get("maxPeakAgeDays") is not None):
            peak_index = max(range(index - window + 1, index + 1), key=lambda i: (series.signal_bars[i][peak_basis], i))
            peak_age = index - peak_index
        ma20 = series.mean("close", index - 19, index + 1) if known and index >= 19 else None
        price_vs_ma20 = (0. if _same_ma_price(close, ma20) else close / ma20 - 1) if ma20 else None
        ma_counts = [None, None]
        if known and index >= 38 and (evidence or p.get("minMa10Above20Days") is not None or p.get("minMa5Above10Days") is not None):
            for position, (fast, slow) in enumerate(((10, 20), (5, 10))):
                checks = [_ma_above(series.mean("close", i-fast+1, i+1), series.mean("close", i-slow+1, i+1))
                          for i in range(index - 19, index + 1)]
                if p.get("maDaysMode", "count") == "consecutive":
                    count = 0
                    for passed in reversed(checks):
                        if not passed:
                            break
                        count += 1
                    ma_counts[position] = count
                else:
                    ma_counts[position] = sum(checks)
        details["structure"] = _pool_evidence([
            _condition("drawdown", "距窗口最高" + ("收盘价" if peak_basis == "close" else "价") + "的回撤在范围内", drawdown, "between",
                       [p["minDrawdown"], p["maxDrawdown"]], "ratio"),
            _condition("peakAgeDays", "所选窗口峰值距今交易日数不超过上限", peak_age, "<=", p.get("maxPeakAgeDays"), "日", enabled=p.get("maxPeakAgeDays") is not None),
            _condition("ma10Above20Days", "MA10 严格高于 MA20 的天数达到下限", ma_counts[0], ">=", p.get("minMa10Above20Days"), "日", enabled=p.get("minMa10Above20Days") is not None),
            _condition("ma5Above10Days", "MA5 严格高于 MA10 的天数达到下限", ma_counts[1], ">=", p.get("minMa5Above10Days"), "日", enabled=p.get("minMa5Above10Days") is not None),
            _condition("priceAboveMa20", "收盘价严格高于 MA20", close, ">", ma20, "元", enabled=p.get("requireAboveMa20", False), stable_gt=True),
            _condition("absPriceVsMa20", "价格相对 MA20 的绝对偏离不超过上限", abs(price_vs_ma20) if price_vs_ma20 is not None else None, "<=", p.get("maxPriceVsMa20"), "ratio", enabled=p.get("maxPriceVsMa20") is not None),
        ])
        details["structure"].update(window=window, requiredObservations=window,
                                    observedObservations=index + 1 if known else 0,
                                    peakClose=peak if peak_basis == "close" else None, peakValue=peak, peakBasis=peak_basis,
                                    peakTiePolicy="most-recent", maDaysMode=p.get("maDaysMode", "count"), maObservationWindow=20,
                                    maRequiredObservations=39, ma20=ma20, priceVsMa20=price_vs_ma20,
                                    priceBasis="causalReferenceAdjustedSignals")
        for item in details["structure"]["conditions"]:
            if item["key"] in ("ma10Above20Days", "ma5Above10Days"):
                item["note"] = "近 20 个交易日累计满足天数；需要完整 39 根日线。" if p.get("maDaysMode", "count") == "count" else "截至当日连续满足天数，上限 20 日；需要完整 39 根日线。"
        values["drawdownPct"] = drawdown * 100 if drawdown is not None else None
        p = self.pool_settings["volume"]
        window = int(p["window"])
        ready = known and index >= window
        previous_volume = series.mean("volume", index - window, index) if ready else None
        volume = series.bars[index]["volume"] if known else None
        ratio = volume / previous_volume if previous_volume else None
        quality = {key: None for key in ("volumeChange20", "upDownVolumeRatio", "downVolumeRatio", "blowoffRisk", "divergenceRisk", "upperShadowRisk")}
        quality_notes, counts = {}, {"upOrFlatDays": None, "downDays": None}
        needs_quality = evidence or any(p.get(key) is not None for key in ("minVolumeChange20", "minUpDownVolumeRatio", "maxDownVolumeRatio")) or any(p.get(key, False) for key in ("excludeBlowoff", "excludeDivergence", "excludeUpperShadow"))
        if known and needs_quality:
            bars = series.signal_bars
            if index >= 19:
                sample = bars[index - 19:index + 1]
                average_volume20 = sum(bar["volume"] for bar in sample) / 20
                first_volume = sample[0]["volume"]
                quality["volumeChange20"] = volume / first_volume - 1 if first_volume > 0 else None
                if first_volume <= 0:
                    quality_notes["volumeChange20"] = "20 日窗口首日成交量为零，首尾变化无有效分母。"
                quality["blowoffRisk"] = any(bar["volume"] > average_volume20 * 2.5 and bar["close"] <= bar["open"] for bar in sample)
            if index >= 20:
                up = [bars[i]["volume"] for i in range(index-19, index+1) if bars[i]["close"] >= bars[i-1]["close"]]
                down = [bars[i]["volume"] for i in range(index-19, index+1) if bars[i]["close"] < bars[i-1]["close"]]
                counts = {"upOrFlatDays": len(up), "downDays": len(down)}
                mean_up = sum(up) / len(up) if up else None
                mean_down = sum(down) / len(down) if down else None
                quality["upDownVolumeRatio"] = mean_up / mean_down if mean_up is not None and mean_down is not None and mean_down > 0 else None
                quality["downVolumeRatio"] = mean_down / average_volume20 if mean_down is not None and average_volume20 > 0 else None
                if not down:
                    quality_notes["upDownVolumeRatio"] = quality_notes["downVolumeRatio"] = "近 20 个交易日无下跌日；下跌日平均成交量未知，不填造量比。"
                elif not up:
                    quality_notes["upDownVolumeRatio"] = "近 20 个交易日无上涨或平盘日；上涨日平均成交量未知。"
                elif mean_down <= 0:
                    quality_notes["upDownVolumeRatio"] = "下跌日平均成交量为零，上涨/下跌量比无有效分母。"
                if average_volume20 <= 0:
                    quality_notes["downVolumeRatio"] = "20 日平均成交量为零，下跌日量比无有效分母。"
            if index >= 9:
                recent = sum(bar["volume"] for bar in bars[index-4:index+1]) / 5
                earlier = sum(bar["volume"] for bar in bars[index-9:index-4]) / 5
                quality["divergenceRisk"] = bars[index]["close"] > bars[index-5]["close"] and recent < earlier * .9
            if index >= 4:
                quality["upperShadowRisk"] = any(
                    bar["high"] > bar["low"] and (bar["high"] - max(bar["open"], bar["close"])) / (bar["high"] - bar["low"]) > .5
                    and bar["close"] <= bar["open"] for bar in bars[index-4:index+1])
        details["volume"] = _pool_evidence([
            _condition("previousMeanPositive", "此前窗口平均成交量大于零", previous_volume, ">", 0, "股"),
            _condition("volumeRatio", "当日成交量与此前均量之比达到下限", ratio, ">=", p["minRatio"], "倍"),
            _condition("volumeChange20", "20 日窗口首尾成交量变化达到下限", quality["volumeChange20"], ">=", p.get("minVolumeChange20"), "ratio", enabled=p.get("minVolumeChange20") is not None),
            _condition("upDownVolumeRatio", "上涨或平盘日均量与下跌日均量之比达到下限", quality["upDownVolumeRatio"], ">=", p.get("minUpDownVolumeRatio"), "倍", enabled=p.get("minUpDownVolumeRatio") is not None),
            _condition("downVolumeRatio", "下跌日均量与全 20 日均量之比不超过上限", quality["downVolumeRatio"], "<=", p.get("maxDownVolumeRatio"), "倍", enabled=p.get("maxDownVolumeRatio") is not None),
            _condition("blowoffRisk", "排除近 20 日放量收阴或平盘风险", quality["blowoffRisk"], "==", False, enabled=p.get("excludeBlowoff", False)),
            _condition("divergenceRisk", "排除价格上涨而近 5 日均量明显下降", quality["divergenceRisk"], "==", False, enabled=p.get("excludeDivergence", False)),
            _condition("upperShadowRisk", "排除近 5 日长上影且收阴或平盘风险", quality["upperShadowRisk"], "==", False, enabled=p.get("excludeUpperShadow", False)),
        ])
        required = {"volumeChange20": 20, "upDownVolumeRatio": 21, "downVolumeRatio": 21,
                    "blowoffRisk": 20, "divergenceRisk": 10, "upperShadowRisk": 5}
        for item in details["volume"]["conditions"]:
            if item["key"] in required:
                item["requiredObservations"] = required[item["key"]]
                if item["key"] in quality_notes:
                    item["note"] = quality_notes[item["key"]]
                elif item["actual"] is None:
                    item["note"] = f"至少需要 {required[item['key']]} 根完整日线。"
        details["volume"].update(window=window, requiredObservations=window + 1,
                                 observedObservations=index + 1 if known else 0,
                                 volume=volume, previousMeanVolume=previous_volume, **counts)
        values["volumeRatio"] = ratio
        combined = self.pool_settings["combined"]
        selected = combined["pools"]
        details["combined"] = _pool_evidence([
            _condition(key, POOL_LABELS[key] + "条件通过", details[key]["passed"], "==", True)
            for key in selected], mode=combined["mode"])
        if not selected:
            details["combined"] = _pool_evidence([], passed=True)
        details["combined"].update(mode=combined["mode"], pools=selected[:])
        result = {"base": True, **{key: item["passed"] is True for key, item in details.items()}}
        return (result, details, values) if evidence else result

    def _signal(self, series, index, node):
        kind, params = node["kind"], node["params"]
        if kind not in FORCE_KINDS:
            return evaluate(series.signal_bars, index, {"strategyIds": [kind], "strategyParams": {kind: params}})
        if series.offset + index < FORCE_WARMUP or index < min(FORCE_WARMUP, series.offset + index):
            return None
        start = index - FORCE_WARMUP
        if series.sums["zeros"][index + 1] - series.sums["zeros"][start] > 0:
            return None
        f = series.force_evidence(index)
        if kind == "forcePurpleToYellow":
            if not f["purpleToYellow"] or f["signalScore"] is None or f["forceDominance"] is None or f["forceDominance"] < params.get("minDominance", -1):
                return None
            raw_score = f["signalScore"] / 100
        else:
            if f["forceDominance"] is None or f["mainForce"] <= f["retailForce"] or f["forceDominance"] < params["minDominance"]:
                return None
            if kind == "forceGoldenCross":
                if not f["goldenCross"]:
                    return None
                raw_score = f["signalScore"] / 100
            else:
                count = int(params["confirmDays"])
                if index < count - 1 or not series.force["rising"][index]:
                    return None
                for i in range(index - count + 1, index + 1):
                    dominance = series.force["forceDominance"][i]
                    if not math.isfinite(dominance) or dominance < params["minDominance"] or series.force["mainForce"][i] <= series.force["retailForce"][i]:
                        return None
                raw_score = f["forceDominance"]
        return {"score": raw_score, "strategies": [{"strategyId": kind, **f,
                "semantics": "simplified-persistent-dominance-not-full-rhythm" if kind == "forceTrend" else "single-day-event"}]}

    def _regime(self, day):
        if self.benchmark is None:
            return {"status": "unknown", "reason": "benchmarkUnavailable", "affectsWeights": False}
        series = self.benchmark
        index = series.by_date.get(day)
        if index is None or index < 60:
            return {"status": "unknown", "reason": "benchmarkHistoryMissing", "symbol": series.security.get("symbol"), "affectsWeights": False}
        ma = series.mean("close", index - 59, index + 1)
        previous_ma = series.mean("close", index - 60, index)
        returns = [series.bars[i]["close"] / series.bars[i - 1]["close"] - 1 for i in range(index - 19, index + 1)]
        volatility = stdev(returns) * math.sqrt(252)
        close = series.bars[index]["close"]
        return {"status": "observed", "symbol": series.security.get("symbol"), "date": day,
                "trend": "up" if close > ma > previous_ma else "down" if close < ma < previous_ma else "mixed",
                "volatility": "high" if volatility >= .3 else "low" if volatility < .15 else "medium",
                "annualizedVolatility20": volatility, "ma60": ma, "close": close,
                "affectsWeights": False, "source": series.security.get("source", "supplied-real-index")}

    def _qualified_universe(self, day, *, evidence=False):
        _valid_day(day)
        base, pools = {}, {key: [] for key in POOL_LABELS}
        indices, by_pool, unknown, base_values = {}, {}, Counter(), {}
        evidence_by_symbol, values_by_symbol = {}, {}
        members = self.dataset.get("membershipByDate", {}).get(day)
        members = set(members) if isinstance(members, (list, tuple, set)) else None
        for symbol in sorted(self.series):
            series = self.series[symbol]
            index = series.by_date.get(day)
            indices[symbol] = index
            values = self._base_values(series, day, index, members, evidence=evidence)
            base_values[symbol] = values
            base[symbol] = self._eligibility(series, day, index, members, values=values)
            unknown.update(base[symbol]["unknown"])
        # Rank the complete eligible cross-section before applying any trend,
        # structure or volume conditions. Pagination never enters this path.
        rank_settings = self.pool_settings["trend"]
        rank_window = rank_settings.get("rankWindow", 40)
        ranked = []
        if evidence or rank_settings.get("rankEnabled", False):
            for symbol, state in base.items():
                index = indices[symbol]
                if state["eligible"] and index is not None and index >= rank_window:
                    bars = self.series[symbol].signal_bars
                    ranked.append((symbol, bars[index]["close"] / bars[index-rank_window]["close"] - 1))
            ranked.sort(key=lambda item: (-item[1], item[0]))
        ranks = {symbol: {"rank": position + 1, "return": change} for position, (symbol, change) in enumerate(ranked)}
        unavailable = sum(state["eligible"] for state in base.values()) - len(ranked)
        for symbol, state in base.items():
            series, index, values = self.series[symbol], indices[symbol], base_values[symbol]
            rank = {**ranks.get(symbol, {}), "universeCount": len(ranked), "unavailableCount": unavailable}
            if symbol not in ranks:
                rank["note"] = "基础资格未通过，不参与排名。" if not state["eligible"] else f"排名需要 {rank_window + 1} 根完整日线；该股不计入有效排名分母。"
            if evidence:
                flags, details, metrics = self._pools(series, index, evidence=True, ranking=rank)
                details["base"] = self._base_evidence(series, day, index, base[symbol], values)
                for item in details.values():
                    item["inPool"] = base[symbol]["eligible"] and item["passed"] is True
                    item["baseEligible"] = base[symbol]["eligible"]
                evidence_by_symbol[symbol] = details
                values_by_symbol[symbol] = {**metrics, "amount20": values["amount20"], "amplitude20": values["amplitude20"]}
                by_pool[symbol] = flags
            elif base[symbol]["eligible"]:
                by_pool[symbol] = self._pools(series, index, ranking=rank)
            if base[symbol]["eligible"]:
                for key, passed in by_pool[symbol].items():
                    if passed:
                        pools[key].append(symbol)
        for symbol, error in self.load_errors.items():
            base[symbol] = {"eligible": False, "reason": "invalidHistory", "unknown": [], "detail": error}
            if evidence:
                _, details, metrics = self._pools(None, None, evidence=True, ranking={
                    "universeCount": len(ranked), "unavailableCount": unavailable,
                    "note": "原始日线无效，不参与排名。"})
                details["base"] = {"status": "unknown", "passed": None, "conditions": [], "reason": "invalidHistory"}
                for item in details.values():
                    item.update(inPool=False, baseEligible=False)
                evidence_by_symbol[symbol] = details
                values_by_symbol[symbol] = {**metrics, "amount20": None, "amplitude20": None}
        coverage = {"totalSecurities": len(base), "eligibleSecurities": len(pools["base"]),
                    "reasons": dict(Counter(v["reason"] for v in base.values())),
                    "unknownHistoricalFields": dict(unknown), "failedSecurities": copy.deepcopy(self.load_errors),
                    "sourceCoverage": copy.deepcopy(self.dataset.get("universeCoverage", {})),
                    "normalization": "same-day-full-eligible-signal-midrank/n", "batchTruncation": False,
                    "strictHistory": self.rules["strictHistory"],
                    "pointInTimeComplete": self.dataset.get("dataQuality", {}).get("pointInTimeComplete") is True and not unknown and not self.load_errors,
                    "qualityReasons": list(self.dataset.get("dataQuality", {}).get("reasons", [])) + (["historical-fields-unknown"] if unknown else [])}
        if evidence or rank_settings.get("rankEnabled", False):
            coverage["returnRanking"] = {"window": rank_window, "enabled": rank_settings.get("rankEnabled", False),
                "rankedCount": len(ranked), "unavailableCount": unavailable,
                "basis": "base-eligible-complete-return-window", "tiePolicy": "return-desc-symbol-asc"}
        return {"indices": indices, "byPool": by_pool, "eligibilityBySymbol": base, "pools": pools,
                "coverage": coverage, "poolEvidenceBySymbol": evidence_by_symbol, "poolValuesBySymbol": values_by_symbol}

    def screen(self, day):
        """Pool conditions only: no strategy evaluation, score, or regime."""
        qualified = self._qualified_universe(day, evidence=True)
        qualified["coverage"]["normalization"] = "none-pool-conditions-only"
        return {"date": day, **{key: qualified[key] for key in
                ("eligibilityBySymbol", "pools", "coverage", "poolEvidenceBySymbol", "poolValuesBySymbol")}}

    def daily(self, day):
        if self.pools_only:
            raise ValueError("股票池筛选实例不运行策略评分")
        _valid_day(day)
        if day == self._last_date:
            return copy.deepcopy(self._last_result)
        universe = self._qualified_universe(day)
        base, pools = universe["eligibilityBySymbol"], universe["pools"]
        indices, by_pool = universe["indices"], universe["byPool"]
        strategies, eligibility = {}, {}
        for sid in self.order:
            node = self.nodes[sid]
            candidates, states = [], {}
            if "kind" in node:
                for symbol, state in base.items():
                    if not state["eligible"]:
                        states[symbol] = {"eligible": False, "reason": state["reason"], "baseEligible": False}
                        continue
                    if not by_pool[symbol][node["pool"]]:
                        states[symbol] = {"eligible": False, "reason": "poolFilter", "baseEligible": True}
                        continue
                    signal = self._signal(self.series[symbol], indices[symbol], node)
                    states[symbol] = {"eligible": signal is not None, "reason": "signal" if signal else "noSignal", "baseEligible": True}
                    if signal is not None:
                        candidates.append({"symbol": symbol, "rawScore": float(signal["score"]), "eligible": True,
                                           "evidence": {"kind": node["kind"], "pool": node["pool"], "details": signal["strategies"],
                                                        "priceBasis": "causalReferenceAdjustedSignals;rawExecutionPrices"}})
            else:
                children = [child for child in node["children"] if child.get("weight", 1) > 0]
                child_rows = {child["id"]: {r["symbol"]: r for r in strategies[child["id"]]} for child in children}
                denominator = sum(c.get("weight", 1) for c in children)
                for symbol, state in base.items():
                    present = [c for c in children if symbol in child_rows[c["id"]]]
                    qualified = state["eligible"] and bool(present) and (node["method"] != "intersection" or len(present) == len(children))
                    weighted_score = sum(c.get("weight", 1) * child_rows[c["id"]].get(symbol, {}).get("score", 0) for c in children) / denominator
                    qualified = qualified and weighted_score >= node.get("threshold", 0)
                    states[symbol] = {"eligible": qualified, "baseEligible": state["eligible"],
                                      "reason": "combinationQualified" if qualified else state["reason"] if not state["eligible"] else "combinationNotQualified"}
                    if qualified:
                        candidates.append({"symbol": symbol, "rawScore": weighted_score, "eligible": True,
                            "evidence": {"method": node["method"], "missingChildScore": 0,
                                         "eligibility": "all-positive-weight-children" if node["method"] == "intersection" else "any-positive-weight-child",
                                         "children": [{"id": c["id"], "weight": c.get("weight", 1),
                                                       "score": child_rows[c["id"]].get(symbol, {}).get("score", 0),
                                                       "matched": symbol in child_rows[c["id"]]} for c in children]}})
            strategies[sid] = rank_normalize(candidates)
            eligibility[sid] = states
        result = {"date": day, "version": SCORER_VERSION, "strategies": strategies,
                  "eligibilityBySymbol": base, "strategyEligibility": eligibility, "pools": pools,
                  "coverage": universe["coverage"],
                  "regime": self._regime(day)}
        self._last_date, self._last_result = day, result
        return copy.deepcopy(result)
