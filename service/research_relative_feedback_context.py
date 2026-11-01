"""Causal strong/weak cohort feedback for attribution of existing accounts.

No signals, account execution, outcomes or I/O. Ranking and raw-bar quality
reuse the frozen leader/lag helpers; the upper cohort matches batch 35 at the
same observation date. Both cohorts keep their original missing members.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from service.research_leader_lag import (
    _Series, _day, _digest, _finite, _positive, _rank, _relative_return,
)

VERSION = "reclaim-relative-feedback-context-v1"
HELPER_SHA256 = "1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e"
STATES = ("stronger", "weaker", "equal", "unknown")
LABELS = {"stronger": "强势队列上涨反馈占优", "weaker": "弱势队列上涨反馈占优",
          "equal": "两队列上涨反馈相同", "unknown": "反馈差方向不明"}
FEEDBACK_STATUSES = ("positive", "flat", "negative", "unknown")
RANK_HORIZON, FEEDBACK_LAG = 20, 5
TOP_PERCENTILE, BOTTOM_PERCENTILE = .90, .10


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _feedback(stock, at):
    # Intentionally identical to batch 35: absolute feedback, not new RS at A.
    reason = stock.window_reason(at, FEEDBACK_LAG + 1)
    if reason is not None:
        return None, "unknown", "stock:" + reason
    ratio = stock.bars[at]["close"] / stock.bars[at - FEEDBACK_LAG]["close"]
    if not _positive(ratio) or not _finite(ratio - 1):
        return None, "unknown", "nonfiniteOrUnderflowFeedbackRatio"
    value = ratio - 1
    return value, "positive" if value > 0 else "negative" if value < 0 else "flat", None


def _group(members):
    counts = {status: 0 for status in FEEDBACK_STATUSES}
    for member in members:
        counts[member["status"]] += 1
    size = len(members)
    return {"cohortSize": size, "feedbackCounts": counts,
            "positiveFractionLower": counts["positive"] / size if size else None,
            "positiveFractionUpper": (counts["positive"] + counts["unknown"]) / size if size else None,
            "memberDigest": _digest(members)}


def _compare_counts(top, bottom):
    """Exact sign for every assignment of missing members, with fixed Ns."""
    nt, nb = sum(top.values()), sum(bottom.values())
    denominator = nt * nb
    difference = {"lowerNumerator": None, "upperNumerator": None,
                  "denominator": denominator, "lower": None, "upper": None}
    if not denominator:
        reason = "emptyFrozenCohorts" if not nt and not nb else "emptyTopCohort" if not nt else "emptyBottomCohort"
        return difference, "unknown", reason
    lower = top["positive"] * nb - (bottom["positive"] + bottom["unknown"]) * nt
    upper = (top["positive"] + top["unknown"]) * nb - bottom["positive"] * nt
    difference.update(lowerNumerator=lower, upperNumerator=upper,
                      lower=lower / denominator, upper=upper / denominator)
    if lower > 0:
        return difference, "stronger", "confirmedStrongerFeedback"
    if upper < 0:
        return difference, "weaker", "confirmedWeakerFeedback"
    if lower == upper == 0:
        return difference, "equal", "confirmedEqualFeedback"
    return difference, "unknown", "ambiguousFeedbackDifference"


def build_relative_feedback_context(dataset, start, end, progress=None, on_members=None):
    """Return every target formation T's context, known only through T-1.

    At A=T-1, observe the two cohorts fixed at F=A-5. The callback receives a
    detached member record once per T; full member histories are not retained
    in the return value. No execution eligibility or source signals are read.
    """
    start, end = _day(start), _day(end)
    _require(start <= end and isinstance(dataset, dict)
             and dataset.get("priceBasisId") == "raw-unadjusted",
             "Use an ordered interval and explicit raw-unadjusted dataset")
    supplied = dataset.get("calendar")
    _require(isinstance(supplied, list) and bool(supplied), "Source calendar must be nonempty")
    for day in supplied:
        _day(day)
    _require(supplied == sorted(set(supplied)), "Source calendar must be unique and increasing")
    calendar = [day for day in supplied if day <= end]
    targets = [day for day in calendar if start <= day <= end]
    _require(bool(targets), "Requested interval has no source trading sessions")
    securities = dataset.get("securities")
    _require(isinstance(securities, list) and bool(securities), "A nonempty fixed board cohort is required")
    by_symbol = {}
    for security in securities:
        symbol = security.get("symbol") if isinstance(security, dict) else None
        _require(isinstance(symbol, str) and bool(symbol.strip()) and symbol not in by_symbol,
                 "Fixed cohort symbols must be unique and nonempty")
        by_symbol[symbol] = security
    benchmark_source = dataset.get("benchmark")
    _require(isinstance(benchmark_source, dict) and benchmark_source.get("symbol") == "sh000300",
             "The registered benchmark must be sh000300")
    symbols = sorted(by_symbol)
    series = {symbol: _Series(by_symbol[symbol], calendar, end) for symbol in symbols}
    benchmark = _Series(benchmark_source, calendar, end)
    indexes = {day: at for at, day in enumerate(calendar)}
    daily_rows = []
    states = {state: 0 for state in STATES}
    totals = {side: {status: 0 for status in FEEDBACK_STATUSES} for side in ("top", "bottom")}
    for offset, day in enumerate(targets):
        at = indexes[day]
        as_of_at, cohort_at = at - 1, at - 1 - FEEDBACK_LAG
        as_of = calendar[as_of_at] if as_of_at >= 0 else None
        cohort = calendar[cohort_at] if cohort_at >= 0 else None
        values, reasons = {}, Counter()
        for symbol in symbols:
            if cohort_at < 0:
                value, reason = None, "insufficientCohortCalendarHistory"
            else:
                value, reason = _relative_return(series[symbol], benchmark, cohort_at, RANK_HORIZON)
            values[symbol] = value
            if reason is not None:
                reasons[reason] += 1
        ranks, population = _rank(values)
        member_lists = {"topMembers": [], "bottomMembers": []}
        for symbol in symbols:
            percentile = ranks[symbol]
            if percentile is None:
                continue
            side = "topMembers" if percentile >= TOP_PERCENTILE else "bottomMembers" if percentile <= BOTTOM_PERCENTILE else None
            if side is None:
                continue
            value, status, reason = _feedback(series[symbol], as_of_at)
            member_lists[side].append({"symbol": symbol, "relativeReturn20": values[symbol],
                "percentile": percentile, "returnSinceCohort": value, "status": status, "reason": reason})
        top, bottom = (_group(member_lists[key]) for key in ("topMembers", "bottomMembers"))
        difference, state, reason = _compare_counts(top["feedbackCounts"], bottom["feedbackCounts"])
        if as_of_at < 0:
            reason = "asOfSessionUnavailable"
        elif cohort_at < 0:
            reason = "insufficientCohortCalendarHistory"
        elif population["count"] == 0:
            reason = "emptyRankPopulation"
        member_digest = _digest(member_lists)
        row = {"formationDate": day, "asOfDate": as_of, "cohortDate": cohort,
            "rankWindowDates": calendar[max(0, cohort_at - RANK_HORIZON):cohort_at + 1] if cohort_at >= 0 else [],
            "feedbackWindowDates": calendar[max(0, cohort_at):as_of_at + 1] if as_of_at >= 0 else [],
            "rankPopulation": population, "rankReasonCounts": dict(sorted(reasons.items())),
            "top": top, "bottom": bottom, "difference": difference,
            "state": state, "reason": reason, "memberDigest": member_digest}
        daily_rows.append(row)
        states[state] += 1
        for side, group in (("top", top), ("bottom", bottom)):
            for status in FEEDBACK_STATUSES:
                totals[side][status] += group["feedbackCounts"][status]
        if on_members is not None:
            on_members(deepcopy({"formationDate": day, "asOfDate": as_of, "cohortDate": cohort,
                "rankPopulation": population, **member_lists, "memberDigest": member_digest}))
        if progress is not None and ((offset + 1) % 10 == 0 or offset + 1 == len(targets)):
            progress({"phase": VERSION, "date": day, "completedDays": offset + 1,
                "totalDays": len(targets), "dayStates": dict(states),
                "topCohortSize": top["cohortSize"], "bottomCohortSize": bottom["cohortSize"]})
    return {"version": VERSION, "calendar": calendar, "targetDates": targets, "dailyRows": daily_rows,
        "counts": {"sampleCount": len(symbols), "targetDayCount": len(targets), "dayStates": states,
            "cohortMemberObservations": {side: sum(counts.values()) for side, counts in totals.items()},
            "feedbackCounts": totals},
        "basis": {"helperModule": "service/research_leader_lag.py", "helperSha256": HELPER_SHA256,
            "benchmark": "sh000300", "priceBasisId": "raw-unadjusted", "asOfLag": 1,
            "rankHorizon": RANK_HORIZON, "feedbackLag": FEEDBACK_LAG,
            "topPercentile": TOP_PERCENTILE, "bottomPercentile": BOTTOM_PERCENTILE,
            "rankDefinition": "(stockF/stockFminus20)/(benchmarkF/benchmarkFminus20)-1",
            "midrankDefinition": "(zeroBasedFirst+exclusiveStop)/(2*validPopulationSize)",
            "feedbackDefinition": "closeA/closeF-1; all six exact source slots must be valid",
            "clock": "A=T-1; F=T-6; rank F-20..F; feedback F..A; current T excluded",
            "denominator": "Each full cohort frozen at F, including unknown feedback; ties are not truncated",
            "differenceDefinition": "top positive-fraction bounds minus bottom positive-fraction bounds; integer cross-products determine strict sign",
            "knownDecisionIsCompleteCohort": False,
            "historicalStatus": "Absent status is unverified, not a tradability guarantee",
            "scope": "Same-board fixed historical survivor cohort; descriptive context only, no signal, account, all-market or holdout claim"},
        "certified": False, "targetAchieved": False}
