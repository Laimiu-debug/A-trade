"""Causal descriptive board environment from frozen raw sources, without trades.

Price and amount availability inherit the historical leadership feature helper;
RS membership inherits the separately frozen leader-lag helper. Amount is not
conditioned on valid OHLC. Neither helper certifies historical market coverage.
"""
from __future__ import annotations

from array import array
from collections import Counter
from copy import deepcopy
from statistics import mean

from .research_leader_lag import _Series, _day, _digest, _finite, _positive, _rank, _relative_return
from .signal_leadership_features import _observations

VERSION = "research-board-environment-v1"
HORIZON, LAG, TOP_PERCENTILE = 20, 5, .90


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _median(values):
    # Same midpoint arithmetic as signal_market_state._median, avoiding a+b
    # overflow for a finite two-value mean.
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else mean(values[middle - 1:middle + 1])


def _prefix(reasons):
    result = array("I", [0])
    for value in reasons:
        result.append(result[-1] + bool(value))
    return result


def _window_reasons(reasons, prefix, at, slots):
    first = at - slots + 1
    if first < 0 or at < 0:
        return ["insufficientCalendarHistory"]
    if prefix[at + 1] == prefix[first]:
        return []
    return sorted({reason for group in reasons[first:at + 1] for reason in group})


def _price(item, at):
    issues = _window_reasons(item["priceReasons"], item["priceBad"], at, HORIZON + 1)
    if issues:
        return None, issues
    bars = item["series"].bars
    ratio = bars[at]["close"] / bars[at - HORIZON]["close"]
    if not _positive(ratio) or not _finite(ratio - 1):
        return None, ["nonfiniteOrUnderflowReturnRatio"]
    return ratio - 1, []


def _amount(item, at):
    first = max(0, at - HORIZON)
    bars, issues, prefix = item["series"].bars, item["amountReasons"], item["amountBad"]
    if at < 0:
        return {"amountToday": None, "prior20MeanAmount": None, "knownAmountSessions20": 0,
                "amountRatio": None, "amountReasons": ["insufficientCalendarHistory"]}
    prior_issues = _window_reasons(issues, prefix, at - 1, HORIZON)
    today_issues = list(issues[at])
    known = at - first - (prefix[at] - prefix[first])
    today = bars[at]["amount"] if not today_issues else None
    prior = mean(row["amount"] for row in bars[at - HORIZON:at]) if not prior_issues else None
    reasons = sorted(set(prior_issues + today_issues))
    ratio = None
    if not reasons:
        if not _positive(prior):
            reasons = ["nonfiniteOrNonpositivePriorMeanAmount"]
        else:
            ratio = today / prior
            if not _positive(ratio):
                reasons, ratio = ["nonfiniteOrUnderflowDerivedRatio"], None
    return {"amountToday": today, "prior20MeanAmount": prior, "knownAmountSessions20": known,
            "amountRatio": ratio, "amountReasons": reasons}


def _retention_summary(statuses, retained_status):
    counts = Counter(statuses)
    size = len(statuses)
    retained, exited, unknown = (counts[key] for key in (retained_status, "knownExit", "unknown"))
    return {"retainedCount": retained, "knownExitCount": exited, "unknownCount": unknown,
            "retentionLower": retained / size if size else None,
            "retentionUpper": (retained + unknown) / size if size else None}


def _matched(today, earlier, population):
    result = {"available": earlier is not None, "earlierDate": earlier["asOfDate"] if earlier else None,
        "todayEvaluableCount": len(today["values"]), "earlierEvaluableCount": None,
        "pairedCount": None, "pairedCoverageToday": None, "pairedCoverageEarlier": None,
        "pairedCoverageFixed": None, "positiveBreadthToday": None, "positiveBreadthEarlier": None,
        "medianReturnToday": None, "medianReturnEarlier": None, "matchedBreadthChange5": None,
        "matchedMedianReturnChange5": None, "unpairedTodayCount": None, "unpairedEarlierCount": None,
        "reason": "earlierTargetSessionUnavailable" if earlier is None else None}
    if earlier is None:
        return result
    old, current = earlier["values"], today["values"]
    common = sorted(current.keys() & old.keys())
    size, a, b = len(common), len(current), len(old)
    result.update(earlierEvaluableCount=b, pairedCount=size,
        pairedCoverageToday=size / a if a else None, pairedCoverageEarlier=size / b if b else None,
        pairedCoverageFixed=size / population, unpairedTodayCount=a - size, unpairedEarlierCount=b - size)
    if not size:
        result["reason"] = "emptyMatchedPopulation"
        return result
    current_values, old_values = [current[s] for s in common], [old[s] for s in common]
    bt, bp = sum(v > 0 for v in current_values) / size, sum(v > 0 for v in old_values) / size
    mt, mp = _median(current_values), _median(old_values)
    result.update(positiveBreadthToday=bt, positiveBreadthEarlier=bp, medianReturnToday=mt,
                  medianReturnEarlier=mp, matchedBreadthChange5=100 * (bt - bp),
                  matchedMedianReturnChange5=mt - mp)
    if not _finite(result["matchedMedianReturnChange5"]):
        result.update(matchedMedianReturnChange5=None, reason="nonfiniteMedianDifference")
    return result


def build_board_environment(dataset, start, end, frozen_members_by_date, progress=None, on_evidence=None):
    """Return all target T's environment through A=T-1, never a signal.

    frozen_members_by_date[T] is the saved relative-feedback member record,
    including formationDate/asOfDate/cohortDate/topMembers. Its F-frozen top
    membership and core ranking fields must exactly match reconstruction. All
    stocks remain in evidence; the callback is detached from internal state.
    """
    start, end = _day(start), _day(end)
    _require(start <= end and isinstance(dataset, dict) and dataset.get("priceBasisId") == "raw-unadjusted",
             "Explicit frozen raw-unadjusted dataset and ordered dates required")
    supplied = dataset.get("calendar")
    _require(isinstance(supplied, list) and bool(supplied), "Source calendar required")
    for day in supplied:
        _day(day)
    _require(supplied == sorted(set(supplied)), "Source calendar must be unique and increasing")
    calendar = [day for day in supplied if day <= end]
    targets = [day for day in calendar if start <= day <= end]
    _require(targets and isinstance(frozen_members_by_date, dict), "Target dates and frozen member mapping required")
    sources = dataset.get("securities")
    _require(isinstance(sources, list) and bool(sources), "Nonempty fixed cohort required")
    by_symbol = {}
    for source in sources:
        symbol = source.get("symbol") if isinstance(source, dict) else None
        _require(isinstance(symbol, str) and bool(symbol.strip()) and symbol not in by_symbol,
                 "Fixed cohort symbols must be unique and nonempty")
        by_symbol[symbol] = source
    symbols, size = sorted(by_symbol), len(by_symbol)
    benchmark_source = dataset.get("benchmark")
    _require(isinstance(benchmark_source, dict) and benchmark_source.get("symbol") == "sh000300",
             "Frozen common benchmark must be sh000300")

    def raw_series(source):
        _require(isinstance(source.get("bars"), list), "Source bars must be a list")
        bars = []
        for row in source["bars"]:
            _require(isinstance(row, dict), "Raw bars must be objects")
            day = _day(row.get("date"))
            # Declare only missing bar basis from the pinned raw dataset;
            # preserve explicit conflicts, and inspect no future numeric field.
            bars.append({"priceBasisId": "raw-unadjusted", **row} if day <= end else row)
        return _Series({**source, "bars": bars}, calendar, end)

    benchmark = raw_series(benchmark_source)
    items = {}
    for symbol in symbols:
        source = by_symbol[symbol]
        series = raw_series(source)
        statuses = source.get("tradeStatusByDate", {})
        observations = _observations(calendar, series.bars, [statuses.get(day) for day in calendar])
        price_reasons = [tuple(row["reasons"]) for row in observations]
        amount_reasons = [tuple(row["amountReasons"]) for row in observations]
        items[symbol] = {"series": series, "priceReasons": price_reasons, "priceBad": _prefix(price_reasons),
                         "amountReasons": amount_reasons, "amountBad": _prefix(amount_reasons)}
    indexes = {day: at for at, day in enumerate(calendar)}
    rank_cache, matched_history, rows = {}, [], []
    next_rank_at = max(0, indexes[targets[0]] - 1 - LAG)
    empty_rank = {"values": dict.fromkeys(symbols), "ranks": dict.fromkeys(symbols),
                  "reasons": dict.fromkeys(symbols, "insufficientCalendarHistory"),
                  "population": {"count": 0, "sha256": _digest([])}}
    totals = {"sampleCount": size, "targetDayCount": len(targets), "priceEvaluableObservations": 0,
              "amountEvaluableObservations": 0, "matchedAvailableDays": 0,
              "endpointCounts": dict.fromkeys(("retained", "knownExit", "unknown"), 0),
              "continuousCounts": dict.fromkeys(("confirmedRetained", "knownExit", "unknown"), 0)}
    for offset, day in enumerate(targets):
        at = indexes[day] - 1
        formation_at = at - LAG
        as_of = calendar[at] if at >= 0 else None
        cohort = calendar[formation_at] if formation_at >= 0 else None
        while next_rank_at <= at:
            values, reasons = {}, {}
            for symbol in symbols:
                values[symbol], reasons[symbol] = _relative_return(items[symbol]["series"], benchmark, next_rank_at, HORIZON)
            ranks, population = _rank(values)
            rank_cache[next_rank_at] = {"values": values, "ranks": ranks, "reasons": reasons, "population": population}
            next_rank_at += 1
        current = rank_cache.get(at, empty_rank)
        prior = rank_cache.get(formation_at, empty_rank)
        record = frozen_members_by_date.get(day)
        _require(isinstance(record, dict) and record.get("formationDate") == day
                 and record.get("asOfDate") == as_of and record.get("cohortDate") == cohort,
                 "Frozen member record must match exact T/A/F source dates")
        members = record.get("topMembers")
        _require(isinstance(members, list) and all(isinstance(member, dict) for member in members),
                 "Frozen top member list required")
        expected = [s for s in symbols if prior["ranks"][s] is not None and prior["ranks"][s] >= TOP_PERCENTILE]
        _require([member.get("symbol") for member in members] == expected,
                 "Frozen membership differs from all original F-ranked top-decile members")
        for member in members:
            s = member["symbol"]
            _require(member.get("relativeReturn20") == prior["values"][s]
                     and member.get("percentile") == prior["ranks"][s], "Frozen member rank/value changed")
        if "rankPopulation" in record:
            _require(record["rankPopulation"] == prior["population"], "Frozen rank-population evidence differs")
        member_set = set(expected)
        evidence, price_values, amount_values, price_reasons, amount_reasons = [], {}, [], Counter(), Counter()
        endpoints, continuous = [], []
        for symbol in symbols:
            item = items[symbol]
            price, p_reasons = _price(item, at)
            amount = _amount(item, at)
            if price is not None:
                price_values[symbol] = price
            if amount["amountRatio"] is not None:
                amount_values.append(amount["amountRatio"])
            price_reasons.update(p_reasons)
            amount_reasons.update(amount["amountReasons"])
            retention = None
            if symbol in member_set:
                path = []
                for observed_at in range(formation_at + 1, at + 1):
                    observed = rank_cache.get(observed_at, empty_rank)
                    percentile = observed["ranks"][symbol]
                    status = "unknown" if percentile is None else "retained" if percentile >= TOP_PERCENTILE else "knownExit"
                    path.append({"date": calendar[observed_at] if observed_at >= 0 else None,
                        "relativeReturn20": observed["values"][symbol], "percentile": percentile,
                        "status": status, "reason": observed["reasons"][symbol]})
                endpoint = path[-1]["status"]
                statuses = [entry["status"] for entry in path]
                sustained = "knownExit" if "knownExit" in statuses else "unknown" if "unknown" in statuses else "confirmedRetained"
                retention = {"endpointStatus": endpoint, "continuousStatus": sustained, "path": path}
                endpoints.append(endpoint)
                continuous.append(sustained)
            evidence.append({"symbol": symbol, "return20": price, "priceReasons": p_reasons, **amount,
                "relativeReturn20": current["values"][symbol], "currentRank": current["ranks"][symbol],
                "rankReason": current["reasons"][symbol], "frozenMember": symbol in member_set, "retention": retention})
        price_count, amount_count = len(price_values), len(amount_values)
        positive = sum(value > 0 for value in price_values.values())
        flat = sum(value == 0 for value in price_values.values())
        now = {"asOfDate": as_of, "values": price_values}
        matched = _matched(now, matched_history[-LAG] if len(matched_history) >= LAG else None, size)
        if matched["available"]:
            _require(matched["earlierDate"] == cohort, "Matched endpoints differ from exact five-session source lag")
        row = {"formationDate": day, "asOfDate": as_of, "cohortDate": cohort, "fixedPopulation": size,
            "price20": {"evaluableCount": price_count, "coverage": price_count / size,
                "unavailableCount": size - price_count, "positiveCount": positive, "zeroCount": flat,
                "negativeCount": price_count - positive - flat, "positiveReturn20Breadth": positive / price_count if price_count else None,
                "medianAbsoluteReturn20": _median(price_values.values()), "reasonCounts": dict(sorted(price_reasons.items()))},
            "amount": {"evaluableCount": amount_count, "coverage": amount_count / size,
                "unavailableCount": size - amount_count, "medianRelativeToOwnPrior20": _median(amount_values),
                "knownAmountSessions20Distribution": dict(sorted(Counter(str(r["knownAmountSessions20"]) for r in evidence).items())),
                "reasonCounts": dict(sorted(amount_reasons.items()))},
            "matched5": matched, "currentRankPopulation": deepcopy(current["population"]),
            "rankReasonCounts": dict(sorted(Counter(r for r in current["reasons"].values() if r is not None).items())),
            "retention": {"cohortSize": len(members), "endpoint": _retention_summary(endpoints, "retained"),
                          "continuous": _retention_summary(continuous, "confirmedRetained")},
            "frozenMemberDigest": _digest(members), "evidenceDigest": _digest(evidence)}
        rows.append(row)
        totals["priceEvaluableObservations"] += price_count
        totals["amountEvaluableObservations"] += amount_count
        totals["matchedAvailableDays"] += matched["available"]
        for key, values in (("endpointCounts", endpoints), ("continuousCounts", continuous)):
            for value in values:
                totals[key][value] += 1
        if on_evidence is not None:
            on_evidence(deepcopy({"formationDate": day, "asOfDate": as_of, "cohortDate": cohort,
                "fixedPopulation": size, "frozenMemberDigest": row["frozenMemberDigest"],
                "evidenceDigest": row["evidenceDigest"], "rows": evidence}))
        matched_history.append(now)
        if len(matched_history) > LAG:
            matched_history.pop(0)
        for old in list(rank_cache):
            if old < formation_at + 1:
                del rank_cache[old]
        if progress is not None and ((offset + 1) % 10 == 0 or offset + 1 == len(targets)):
            progress({"phase": VERSION, "date": day, "completedDays": offset + 1, "totalDays": len(targets)})
    return {"version": VERSION, "calendar": calendar, "targetDates": targets, "dailyRows": rows, "counts": totals,
        "basis": {"clock": "Each target T uses A=T-1; F=T-6; no T numerical field enters a metric.",
            "price": "Raw closeA/closeAminus20-1 on 21 complete slots, using signal_leadership_features._observations price validity; no benchmark or amount gate.",
            "amount": "Independent aligned raw/positive volume/explicit positive amount/no explicit halt gate. Prior20 excludes A. statistics.mean then median of individual ratios; zero is unavailable, not imputed.",
            "matched5": "Same valid-price intersection at A and the observation five target rows earlier. First five target rows unknown despite cached warmup; breadth change is percentage points.",
            "ranking": "All known same-board RS20 relative to sh000300, exact tied midrank, no eligibility or amount gate; research_leader_lag._Series/_relative_return/_rank.",
            "gateDifference": "Legacy price/amount flags recognize False, integer0 or string0; lag rank additionally recognizes float0 and knownSuspended. Missing bar basis inherits the pinned raw declaration, explicit conflicts stay invalid.",
            "retention": "Original F top percentile>=0.90. Endpoint compares A membership; continuous requires top membership on all F+1..A five sessions. Any known exit dominates unknown days; otherwise unknown stays in original N.",
            "bounds": "Observation-missingness bounds, not confidence intervals. Empty original cohort has null bounds.",
            "scope": "Fixed period-start local survivor board cohort, not historical whole-market coverage or market-wide leader identity. Raw-price rights/status limitations are inherited; no reconstruction.",
            "signalsGenerated": False, "accountExecutions": 0}, "certified": False, "targetAchieved": False}
