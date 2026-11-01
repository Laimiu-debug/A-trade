"""Pure fixed-cohort strength feedback; no I/O, account state or outcomes.

The rank and bar-quality definitions intentionally reuse the frozen batch-34
helpers. The caller must pin that module as well as this one. A cohort is fixed
five source sessions before each decision, and missing continuations never
shrink its denominator. The original control queue is only masked.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from service.research_leader_lag import (
    _Series, _day, _digest, _finite, _positive, _rank, _relative_return,
)


VERSION = "strength-feedback-v1"
STRATEGY_ID = "strengthFeedbackV1"
SOURCE_STRATEGY_ID = "leaderLagFollowersV1"
MODES = ("known", "positive")
STATES = ("pass", "fail", "unknown")
FEEDBACK_STATUSES = ("positive", "flat", "negative", "unknown")
SIGNAL_FIELDS = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}
HELPER_SHA256 = "1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e"
FORMATION_LAG, RANK_HORIZON, COHORT_PERCENTILE = 5, 20, .90


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _majority(counts):
    """Decide the same strict-majority verdict for every unknown assignment."""
    size = sum(counts.values())
    positive, unknown = counts["positive"], counts["unknown"]
    if size == 0:
        return "unknown", "emptyFrozenCohort"
    if 2 * positive > size:
        return "pass", "confirmedPositiveMajority"
    if 2 * (positive + unknown) <= size:
        return "fail", "positiveMajorityImpossible"
    return "unknown", "ambiguousPositiveMajority"


def _feedback(stock, at):
    reason = stock.window_reason(at, FORMATION_LAG + 1)
    if reason is not None:
        return None, "unknown", "stock:" + reason
    ratio = stock.bars[at]["close"] / stock.bars[at - FORMATION_LAG]["close"]
    if not _positive(ratio) or not _finite(ratio - 1):
        return None, "unknown", "nonfiniteOrUnderflowFeedbackRatio"
    value = ratio - 1
    return value, "positive" if value > 0 else "negative" if value < 0 else "flat", None


def _validate_signals(signals, symbols, target_dates):
    _require(isinstance(signals, list), "Source signals must be a complete list")
    ids, date_symbols, date_ranks, order = set(), set(), set(), []
    for signal in signals:
        _require(isinstance(signal, dict) and set(signal) == SIGNAL_FIELDS,
                 "Source signals must contain exactly the original five fields")
        sid, symbol = signal["signalId"], signal["symbol"]
        day, rank = _day(signal["signalDate"]), signal["originalRank"]
        _require(signal["strategyId"] == SOURCE_STRATEGY_ID,
                 "Only frozen leaderLagFollowersV1 control signals are accepted")
        _require(isinstance(sid, str) and bool(sid.strip()), "Source signalId must be nonempty")
        _require(isinstance(symbol, str) and symbol in symbols, "Source signal outside fixed board cohort")
        _require(day in target_dates, "Source signal outside target source sessions")
        _require(type(rank) is int and 1 <= rank <= 5, "Source originalRank must remain in Top5")
        _require(sid not in ids and (day, symbol) not in date_symbols and (day, rank) not in date_ranks,
                 "Duplicate source signal identity, date/symbol or date/rank")
        ids.add(sid)
        date_symbols.add((day, symbol))
        date_ranks.add((day, rank))
        order.append((day, rank, symbol, sid))
    _require(order == sorted(order), "Source signals must already be in their frozen canonical order")


def build_strength_feedback(dataset, start, end, source_signals, progress=None, on_members=None):
    """Return daily feedback and two masks without changing the source queue.

    ``on_members`` receives one detached complete cohort record per target day.
    Only its digest is retained in dailyRows, avoiding an additional full member
    history in memory. The runner owns persistence and checks source/helper pins.
    Signals are decisions at A close; next-open execution remains the account's
    job, including an unexecuted signal on the final source session.
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
    _validate_signals(source_signals, set(symbols), set(targets))
    series = {symbol: _Series(by_symbol[symbol], calendar, end) for symbol in symbols}
    benchmark = _Series(benchmark_source, calendar, end)
    indexes = {day: at for at, day in enumerate(calendar)}
    daily_rows, contexts = [], {}
    day_states = {state: 0 for state in STATES}
    for offset, day in enumerate(targets):
        at = indexes[day]
        formation_at = at - FORMATION_LAG
        formation_day = calendar[formation_at] if formation_at >= 0 else None
        values, reasons = {}, Counter()
        for symbol in symbols:
            if formation_at < 0:
                value, reason = None, "insufficientCohortCalendarHistory"
            else:
                value, reason = _relative_return(series[symbol], benchmark, formation_at, RANK_HORIZON)
            values[symbol] = value
            if reason is not None:
                reasons[reason] += 1
        ranks, population = _rank(values)
        members = []
        feedback_counts = {status: 0 for status in FEEDBACK_STATUSES}
        for symbol in symbols:
            if ranks[symbol] is None or ranks[symbol] < COHORT_PERCENTILE:
                continue
            value, status, reason = _feedback(series[symbol], at)
            members.append({"symbol": symbol, "relativeReturn20": values[symbol],
                "percentile": ranks[symbol], "returnSinceCohort": value,
                "status": status, "reason": reason})
            feedback_counts[status] += 1
        state, reason = _majority(feedback_counts)
        if formation_at < 0:
            reason = "insufficientCohortCalendarHistory"
        size, member_digest = len(members), _digest(members)
        row = {"formationDate": day, "cohortDate": formation_day,
            "rankWindowDates": calendar[max(0, formation_at - RANK_HORIZON):formation_at + 1]
                if formation_at >= 0 else [],
            "feedbackWindowDates": calendar[max(0, formation_at):at + 1],
            "rankPopulation": population, "rankReasonCounts": dict(sorted(reasons.items())),
            "cohortSize": size, "feedbackCounts": feedback_counts,
            "positiveFractionLower": feedback_counts["positive"] / size if size else None,
            "positiveFractionUpper": (feedback_counts["positive"] + feedback_counts["unknown"]) / size
                if size else None,
            "state": state, "reason": reason, "memberDigest": member_digest}
        daily_rows.append(row)
        contexts[day] = row
        day_states[state] += 1
        if on_members is not None:
            on_members(deepcopy({"version": VERSION, "formationDate": day,
                "cohortDate": formation_day, "rankPopulation": population, "cohortSize": size,
                "memberDigest": member_digest, "members": members}))
        if progress is not None and ((offset + 1) % 10 == 0 or offset + 1 == len(targets)):
            progress({"phase": "strength-feedback", "date": day, "completedDays": offset + 1,
                "totalDays": len(targets), "dayStates": dict(day_states), "cohortSize": size})

    schedules = {mode: [] for mode in MODES}
    policy_counts = {mode: {status: 0 for status in ("included", "excluded", "unknown")} for mode in MODES}
    opportunities = []
    for source_signal in source_signals:
        row = contexts[source_signal["signalDate"]]
        signal = deepcopy(source_signal)
        signal["strategyId"] = STRATEGY_ID
        decisions, signals = {}, {}
        for mode in MODES:
            admitted = row["state"] == "pass" or mode == "known" and row["state"] == "fail"
            status = "unknown" if row["state"] == "unknown" else "included" if admitted else "excluded"
            reason = row["reason"] if row["state"] == "unknown" or mode == "positive" else "knownFeedbackState"
            decisions[mode] = {"status": status, "reason": reason}
            signals[mode] = deepcopy(signal) if admitted else None
            policy_counts[mode][status] += 1
            if admitted:
                schedules[mode].append(deepcopy(signal))
        opportunities.append({**signal, "formationId": source_signal["signalId"],
            "sourceStrategyId": source_signal["strategyId"], "formationDate": row["formationDate"],
            "cohortDate": row["cohortDate"], "state": row["state"], "cohortSize": row["cohortSize"],
            "feedbackCounts": deepcopy(row["feedbackCounts"]), "memberDigest": row["memberDigest"],
            "decisions": decisions, "signals": signals})
    counts = {"sampleCount": len(symbols), "targetDayCount": len(targets),
        "inputSignalCount": len(source_signals), "opportunityCount": len(opportunities),
        "dayStates": day_states, "signals": {mode: len(schedules[mode]) for mode in MODES},
        "notScheduled": {mode: len(source_signals) - len(schedules[mode]) for mode in MODES},
        "policyCounts": policy_counts}
    return {"version": VERSION, "strategyId": STRATEGY_ID, "modes": list(MODES),
        "calendar": calendar, "targetDates": targets, "dailyRows": daily_rows,
        "opportunities": opportunities, "schedules": schedules, "counts": counts,
        "basis": {"helperModule": "service/research_leader_lag.py", "helperSha256": HELPER_SHA256,
            "benchmark": "sh000300", "priceBasisId": "raw-unadjusted",
            "sourceStrategyId": SOURCE_STRATEGY_ID, "rankHorizon": RANK_HORIZON,
            "formationLag": FORMATION_LAG, "cohortPercentile": COHORT_PERCENTILE,
            "rankDefinition": "(stockF/stockFminus20)/(benchmarkF/benchmarkFminus20)-1",
            "midrankDefinition": "(zeroBasedFirst+exclusiveStop)/(2*validPopulationSize)",
            "feedbackDefinition": "closeA/closeF-1; all six exact source slots must be valid",
            "majorityDefinition": "N=0 unknown; 2P>N pass; 2(P+U)<=N fail; otherwise unknown",
            "denominator": "All members frozen at F; unknown feedback remains in N",
            "knownDecisionIsCompleteCohort": False, "sourceSignalOrderingPreserved": True,
            "top5Refill": False, "execution": "Decision at A close; account executes next open",
            "historicalStatus": "Absent status is unverified, not a tradability guarantee",
            "scope": "Same-board fixed historical survivor cohort; no all-market or holdout claim"},
        "certified": False, "targetAchieved": False}
