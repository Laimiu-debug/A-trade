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
from service.portfolio_config import catalogue, normalize_config

SCORER_VERSION = "portfolio-causal-midrank-v1"
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
                value = int(bar["volume"] <= 0) if key == "zeros" else bar.get(key, 0)
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
            for i, row in enumerate(self.bars):
                while queue and queue[0] <= i - window:
                    queue.popleft()
                while queue and self.bars[queue[-1]][key] <= row[key]:
                    queue.pop()
                queue.append(i)
                maxima.append(self.bars[queue[0]][key])
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
    def __init__(self, dataset, config):
        self.dataset, self.config = dataset, normalize_config(config)
        self.strategies = list(self.config.get("strategies", []))
        self.combinations = list(self.config.get("combinations", []))
        defaults = {s["id"]: {p["key"]: p["default"] for p in s["parameters"]} for s in catalogue()}
        self.nodes, self.order = {}, []
        for strategy in self.strategies:
            sid, kind = strategy.get("id"), strategy.get("kind")
            if not isinstance(sid, str) or not sid or sid in self.nodes or kind not in KINDS:
                raise ValueError("策略 ID 或类型无效")
            node = {**strategy, "params": {**defaults[kind], **strategy.get("params", {})}, "pool": strategy.get("pool", "base")}
            if node["pool"] not in {"base", "trend", "structure", "volume"}:
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
        if not self.strategies:
            raise ValueError("至少配置一个单策略")
        need_force = any(s["kind"] in FORCE_KINDS for s in self.strategies)
        self.series, self.load_errors = {}, {}
        for security in dataset.get("securities", []):
            symbol = security.get("symbol")
            if symbol in self.series or symbol in self.load_errors:
                raise ValueError("股票池包含重复证券")
            try:
                self.series[symbol] = _Series(security, need_force)
            except (TypeError, ValueError, KeyError, OverflowError) as exc:
                self.load_errors[symbol] = str(exc)
        self.rules = {"strictHistory": False, "excludeSt": True, "minListingDays": 60, "minAmount20": 10000000,
                      **self.config.get("universe", {})}
        self.pool_settings = {"trend": {"window": 60, "minReturn": 0},
                              "structure": {"window": 20, "minDrawdown": .02, "maxDrawdown": .25},
                              "volume": {"window": 20, "minRatio": 1.2}}
        for key, settings in self.config.get("poolSettings", {}).items():
            if key in self.pool_settings:
                self.pool_settings[key].update(settings)
        benchmark = dataset.get("benchmark")
        self.benchmark = _Series(benchmark, False) if isinstance(benchmark, dict) and benchmark.get("bars") else None
        self._last_date, self._last_result = None, None

    def _eligibility(self, series, day, index, members=None):
        security = series.security
        unknown = []
        member = security.get("memberHistory", security.get("memberByDate", {})).get(day)
        if members is not None:
            member = security["symbol"] in members
        listing = security.get("listingDate")
        if security.get("delistDate") and day >= security["delistDate"]:
            return {"eligible": False, "reason": "delisted", "unknown": []}
        if member is False:
            return {"eligible": False, "reason": "notHistoricalMember", "unknown": []}
        if listing and day < listing:
            return {"eligible": False, "reason": "notListed", "unknown": []}
        if index is None:
            first = security.get("historyWindow", {}).get("sourceFirstDate") or (series.dates[0] if series.dates else None)
            return {"eligible": False, "reason": "notObservedYet" if first and day < first else "missingDailyBar", "unknown": []}
        historical_st = security.get("stByDate", security.get("stHistory", {})).get(day)
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
        status = security.get("tradeStatusByDate", {}).get(day)
        status = {"0": 0, "1": 1}.get(status, status) if isinstance(status, str) else status
        if status == 0 or series.bars[index]["volume"] <= 0:
            return {"eligible": False, "reason": "suspendedOrZeroVolume", "unknown": unknown}
        if status not in (0, 1):
            unknown.append("historicalTradeStatus")
            if self.rules["strictHistory"]:
                return {"eligible": False, "reason": "strictHistoryMissing", "unknown": unknown}
        observed = series.offset + index + 1
        if listing:
            # Calendar days form an upper bound: never let recent listing pass
            # merely because a malformed snapshot contains earlier observations.
            observed = min(observed, (dt.date.fromisoformat(day) - dt.date.fromisoformat(listing)).days + 1)
        if observed < self.rules["minListingDays"]:
            return {"eligible": False, "reason": "insufficientListingHistory", "unknown": unknown, "observedDays": observed}
        average = series.mean("amount", index - 19, index + 1) if index >= 19 else None
        if self.rules["minAmount20"] > 0 and (average is None or average < self.rules["minAmount20"]):
            return {"eligible": False, "reason": "amount20BelowMinimum", "unknown": unknown, "amount20": average}
        return {"eligible": True, "reason": "eligible", "unknown": unknown, "observedDays": observed, "amount20": average,
                "listingAgeBasis": "observed-history-lower-bound"}

    def _pools(self, series, index):
        result = {"base": True, "trend": False, "structure": False, "volume": False}
        close = series.bars[index]["close"]
        p = self.pool_settings["trend"]
        window = int(p["window"])
        if index >= window:
            average = series.mean("close", index - window + 1, index + 1)
            previous = series.mean("close", index - window, index)
            result["trend"] = close > average > previous and close / series.bars[index - window]["close"] - 1 >= p["minReturn"]
        p = self.pool_settings["structure"]
        window = int(p["window"])
        if index >= window - 1:
            drawdown = 1 - close / series.peak("close", window, index)
            result["structure"] = p["minDrawdown"] <= drawdown <= p["maxDrawdown"]
        p = self.pool_settings["volume"]
        window = int(p["window"])
        if index >= window:
            previous_volume = series.mean("volume", index - window, index)
            result["volume"] = previous_volume > 0 and series.bars[index]["volume"] / previous_volume >= p["minRatio"]
        return result

    def _signal(self, series, index, node):
        kind, params = node["kind"], node["params"]
        if kind not in FORCE_KINDS:
            return evaluate(series.bars, index, {"strategyIds": [kind], "strategyParams": {kind: params}})
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

    def daily(self, day):
        _valid_day(day)
        if day == self._last_date:
            return copy.deepcopy(self._last_result)
        base, pools = {}, {key: [] for key in ("base", "trend", "structure", "volume")}
        indices, by_pool, unknown = {}, {}, Counter()
        members = self.dataset.get("membershipByDate", {}).get(day)
        members = set(members) if isinstance(members, (list, tuple, set)) else None
        for symbol in sorted(self.series):
            series = self.series[symbol]
            index = series.by_date.get(day)
            indices[symbol] = index
            base[symbol] = self._eligibility(series, day, index, members)
            unknown.update(base[symbol]["unknown"])
            if base[symbol]["eligible"]:
                by_pool[symbol] = self._pools(series, index)
                for key, passed in by_pool[symbol].items():
                    if passed:
                        pools[key].append(symbol)
        for symbol, error in self.load_errors.items():
            base[symbol] = {"eligible": False, "reason": "invalidHistory", "unknown": [], "detail": error}
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
                                           "evidence": {"kind": node["kind"], "pool": node["pool"], "details": signal["strategies"]}})
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
        reasons = dict(Counter(v["reason"] for v in base.values()))
        result = {"date": day, "version": SCORER_VERSION, "strategies": strategies,
                  "eligibilityBySymbol": base, "strategyEligibility": eligibility, "pools": pools,
                  "coverage": {"totalSecurities": len(base), "eligibleSecurities": len(pools["base"]), "reasons": reasons,
                               "unknownHistoricalFields": dict(unknown), "failedSecurities": copy.deepcopy(self.load_errors),
                               "sourceCoverage": copy.deepcopy(self.dataset.get("universeCoverage", {})),
                               "normalization": "same-day-full-eligible-signal-midrank/n", "batchTruncation": False,
                               "strictHistory": self.rules["strictHistory"],
                               "pointInTimeComplete": self.dataset.get("dataQuality", {}).get("pointInTimeComplete") is True and not unknown and not self.load_errors,
                               "qualityReasons": list(self.dataset.get("dataQuality", {}).get("reasons", [])) + (["historical-fields-unknown"] if unknown else [])},
                  "regime": self._regime(day)}
        self._last_date, self._last_result = day, result
        return copy.deepcopy(result)
