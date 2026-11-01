"""Pure admission diagnostic for the frozen leader-lag definition.

No signals, peer groups, accounts, parameter changes or file access. Only the
requested focus is expanded to every symbol; archived parent rows are checked,
never used to choose the cross-sectional rank population.
"""
from collections import Counter, deque
from copy import deepcopy

from . import research_leader_lag as lag


VERSION = "leader-stage-coverage-v1"
HELPER_SHA256 = "1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e"
FIELDS = tuple(field for field, _, _ in lag.THRESHOLDS)
CURRENT_FAILURES = ("unknown", *FIELDS, "breakout", "passed")
FORMATION_FAILURES = ("unknown", *FIELDS, "passed")
DISPOSITIONS = ("currentUnknown", "currentPredicateFailed", "currentNoBreakout",
                "formationUnknown", "formationPredicateFailed", "leaderRankExcluded", "selectedLeader")
COUNT_FIELDS = ("currentCandidateCount", "temporalCandidateCount", "leaderEventCount")
PARENT_FIELDS = ("formationDate", "peerFormationDate", "rankPopulations", "rankReasonCounts",
                 "activationCounts", "formationCounts", "leaderCandidates", "leaderRanking", "leaderEventIds")
BASIS = {
    "protocol": "docs/leader-stage-coverage-v1.json",
    "helper": {"path": "service/research_leader_lag.py", "sha256": HELPER_SHA256},
    "priceBasisId": "raw-unadjusted", "benchmarkSymbol": "sh000300",
    "rankScope": "All fixed-cohort symbols; independent exact21/61-slot valid populations; no eligibility prefilter",
    "rankClock": "Earliest focus A minus29 source slots (earliest F minus19), through final focus A; raw windows retain earlier history",
    "formationLag": 10,
    "conditions": [{"field": field, "operator": operator, "threshold": value}
                   for field, operator, value in lag.THRESHOLDS],
    "activation": "A five conditions and strict close above preceding5 highs; F five conditions only",
    "firstFailure": "Unknown first, then first false predicate in frozen order, then A breakout; descriptive ordering, not causal importance",
    "masks": "Full true/false/unknown masks overlap across failed conditions; F excludes breakout",
    "formationPopulation": "F evidence is rebuilt for all symbols; formationEvaluatedByParent is true only for original A candidates",
    "rs20Top20": "At most20 known raw RS20 values descending, symbol ascending; no RS60 or leadership prefilter",
    "scope": "Fixed board-cohort historical development diagnostic; no trading account, causal leadership or investor-identity claim",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def condition_failure(evidence, *, include_breakout):
    """Return exclusive reporting failure and the complete overlapping mask."""
    names = FIELDS + (("breakout",) if include_breakout else ())
    values = {} if evidence is None else dict(evidence["predicates"])
    if include_breakout and evidence is not None:
        values["breakout"] = evidence["breakout"]
    mask = {key: [] for key in ("true", "false", "unknown")}
    for name in names:
        value = values.get(name)
        _require(value is None or type(value) is bool, "Condition predicate must be bool or null")
        mask["unknown" if value is None else "true" if value else "false"].append(name)
    # Required raw/history evidence may be unknown even if several individually
    # computable predicates are false. Preserve that distinction and every mask.
    if evidence is None or evidence["status"] == "unknown" or mask["unknown"]:
        return "unknown", mask
    return (mask["false"][0] if mask["false"] else "passed"), mask


def _input(dataset, saved_daily_rows, start, end):
    start, end = lag._day(start), lag._day(end)
    _require(start <= end and isinstance(dataset, dict)
             and dataset.get("priceBasisId") == "raw-unadjusted", "Ordered focus and explicit raw basis required")
    supplied = dataset.get("calendar")
    _require(isinstance(supplied, list) and supplied and supplied == sorted(set(supplied)),
             "Source calendar must be unique and increasing")
    for day in supplied:
        lag._day(day)
    calendar = [day for day in supplied if day <= end]
    targets = [day for day in calendar if start <= day <= end]
    _require(targets, "Focus has no source sessions")
    securities = dataset.get("securities")
    _require(isinstance(securities, list) and securities, "Nonempty fixed cohort required")
    by_symbol = {}
    for security in securities:
        symbol = security.get("symbol") if isinstance(security, dict) else None
        _require(isinstance(symbol, str) and symbol.strip() and symbol not in by_symbol,
                 "Cohort symbols must be unique and nonempty")
        by_symbol[symbol] = security
    benchmark = dataset.get("benchmark")
    _require(isinstance(benchmark, dict) and benchmark.get("symbol") == "sh000300",
             "Frozen benchmark must be sh000300")
    _require(isinstance(saved_daily_rows, list), "Parent daily evidence must be a list")
    parent, previous = {}, None
    allowed, focal = set(supplied), set(targets)
    for row in saved_daily_rows:
        _require(isinstance(row, dict), "Parent daily row must be an object")
        day = lag._day(row.get("formationDate"))
        _require(day in allowed and (previous is None or previous < day),
                 "Parent dates must be source-calendar aligned, unique and increasing")
        previous = day
        if day in focal:
            parent[day] = row  # Nonfocus numerical fields are not inspected.
    _require(set(parent) == focal, "Missing focus parent daily evidence")
    return calendar, targets, by_symbol, benchmark, parent


def _domain(counter, names):
    return {name: counter[name] for name in names}


def _formation_reason(prior):
    if prior is None:
        return "formationStateUnavailable"
    return {"passed": "formationFiveConditionsPassed", "failed": "formationFiveConditionsFailed",
            "unknown": "formationEvidenceUnknown"}[prior["status"]]


def _verify_day(actual, parent):
    day = actual["formationDate"]
    for field in PARENT_FIELDS:
        _require(field in parent and lag._digest(actual[field]) == lag._digest(parent[field]),
                 f"Parent reconciliation failed: {day}.{field}")
    _require(isinstance(parent.get("counts"), dict), f"Parent counts missing: {day}")
    for field in COUNT_FIELDS:
        _require(field in parent["counts"] and lag._digest(actual["counts"][field]) == lag._digest(parent["counts"][field]),
                 f"Parent reconciliation failed: {day}.counts.{field}")
    return {"formationDate": day, "passed": True, "parentDailySha256": lag._digest(parent),
            "checkedFields": list(PARENT_FIELDS) + ["counts." + key for key in COUNT_FIELDS]}


def build_leader_stage_coverage(dataset, saved_daily_rows, *, start, end, progress=None):
    """Rebuild the original upstream funnel; reject any archived-row mismatch.

    Dates outside the focus may be supplied in parent evidence. Calendar and
    bar date structure are validated, but future numerical bars are ignored by
    the frozen series helper. Parent rank-stream comparison belongs to the
    runner/auditor: ``rankRows`` preserves the original complete digest schema.
    """
    calendar, targets, securities, benchmark_source, parent = _input(dataset, saved_daily_rows, start, end)
    symbols = sorted(securities)
    series = {symbol: lag._Series(securities[symbol], calendar, end) for symbol in symbols}
    benchmark = lag._Series(benchmark_source, calendar, end)
    first_target = calendar.index(targets[0])
    first_rank = max(0, first_target - 29)
    histories = {symbol: deque(maxlen=20) for symbol in symbols}
    state_ring = deque(maxlen=11)
    rank_rows, daily_rows, symbol_rows, checks = [], [], [], []
    total_activation, total_formation, total_dispositions = Counter(), Counter(), Counter()
    total_failures = {key: Counter() for key in ("current", "formationAll", "formationEvaluatedByParent")}
    total_candidates = Counter()
    for at in range(first_rank, len(calendar)):
        day = calendar[at]
        ranks, populations, reason_counts, relative = {}, {}, {}, {}
        for horizon in (20, 60):
            values, reasons = {}, Counter()
            for symbol in symbols:
                value, reason = lag._relative_return(series[symbol], benchmark, at, horizon)
                values[symbol] = value
                if reason is not None:
                    reasons[reason] += 1
            relative[horizon] = values
            ranks[horizon], populations[str(horizon)] = lag._rank(values)
            reason_counts[str(horizon)] = dict(sorted(reasons.items()))
        rank_rows.append({"date": day, "populations": populations, "reasonCounts": reason_counts})
        current = {}
        for symbol in symbols:
            histories[symbol].append(ranks[20][symbol])
            current[symbol] = lag._leadership(series[symbol], benchmark, at, calendar,
                                               histories[symbol], ranks[60][symbol])
        state_ring.append((day, current))
        if at < first_target:
            continue
        formation_at = at - 10
        formation_day = calendar[formation_at] if formation_at >= 0 else None
        past = state_ring[0][1] if len(state_ring) == 11 and state_ring[0][0] == formation_day else None
        candidates, temporal = [], []
        activations, formations, dispositions = Counter(), Counter(), Counter()
        failures = {key: Counter() for key in total_failures}
        states = []
        for symbol in symbols:
            state, prior = current[symbol], past[symbol] if past is not None else None
            activation = "unknown" if state["status"] == "unknown" else (
                "candidate" if state["status"] == "passed" and state["breakout"] else "notTriggered")
            activations[activation] += 1
            evaluated = activation == "candidate"
            formation_status = (prior["status"] if prior is not None else "unknown") if evaluated else None
            formation_reason = _formation_reason(prior) if evaluated else None
            if evaluated:
                formations[formation_status] += 1
                candidate = {"symbol": symbol, "formationDate": day, "peerFormationDate": formation_day,
                    "currentEvidence": deepcopy(state), "formationEvidence": deepcopy(prior),
                    "formationStatus": formation_status, "formationReason": formation_reason,
                    "originalLeaderRank": None, "selectedTop5": False}
                candidates.append(candidate)
                if formation_status == "passed":
                    temporal.append(candidate)
            current_failure, current_mask = condition_failure(state, include_breakout=True)
            formation_failure, formation_mask = condition_failure(prior, include_breakout=False)
            failures["current"][current_failure] += 1
            failures["formationAll"][formation_failure] += 1
            if evaluated:
                failures["formationEvaluatedByParent"][formation_failure] += 1
            states.append({"formationDate": day, "peerFormationDate": formation_day, "symbol": symbol,
                "currentEvidence": deepcopy(state), "formationEvidence": deepcopy(prior),
                "formationEvaluatedByParent": evaluated, "activation": activation,
                "formationStatus": formation_status, "formationReason": formation_reason,
                "originalLeaderRank": None, "selectedTop5": False,
                "currentFirstFailure": current_failure, "formationFirstFailure": formation_failure,
                "failureMask": {"current": current_mask, "formation": formation_mask}})
        temporal.sort(key=lambda row: (-row["currentEvidence"]["values"]["meanStrengthPercentile20"],
            -row["currentEvidence"]["values"]["relativeReturn60Percentile"], row["symbol"]))
        for rank, candidate in enumerate(temporal, 1):
            candidate.update(originalLeaderRank=rank, selectedTop5=rank <= 5)
        candidates_by_symbol = {row["symbol"]: row for row in candidates}
        for state in states:
            candidate = candidates_by_symbol.get(state["symbol"])
            if candidate:
                state.update(originalLeaderRank=candidate["originalLeaderRank"], selectedTop5=candidate["selectedTop5"])
            failure = state["currentFirstFailure"]
            disposition = ("currentUnknown" if failure == "unknown" else
                "currentPredicateFailed" if failure in FIELDS else
                "currentNoBreakout" if failure == "breakout" else
                "formationUnknown" if state["formationFirstFailure"] == "unknown" else
                "formationPredicateFailed" if state["formationFirstFailure"] != "passed" else
                "selectedLeader" if state["selectedTop5"] else "leaderRankExcluded")
            state["endToEndDisposition"] = disposition
            dispositions[disposition] += 1
        selected = temporal[:5]
        leaders = [{"symbol": row["symbol"], "originalLeaderRank": row["originalLeaderRank"],
                    "selectedTop5": row["selectedTop5"]} for row in temporal]
        top_symbols = sorted((s for s in symbols if relative[20][s] is not None),
                             key=lambda s: (-relative[20][s], s))[:20]
        top20 = [{"symbol": s, "relativeReturn20": relative[20][s], "percentile20": ranks[20][s],
                  "currentEvidence": deepcopy(current[s]), "formationEvidence": deepcopy(past[s]) if past else None}
                 for s in top_symbols]
        row = {"formationDate": day, "peerFormationDate": formation_day,
            "rankPopulations": deepcopy(populations), "rankReasonCounts": deepcopy(reason_counts),
            "activationCounts": _domain(activations, ("candidate", "notTriggered", "unknown")),
            "formationCounts": _domain(formations, ("passed", "failed", "unknown")),
            "leaderCandidates": candidates, "leaderRanking": leaders,
            "leaderEventIds": [f"{lag.STRATEGY_ID}:{day}:{item['symbol']}" for item in selected],
            "counts": dict(zip(COUNT_FIELDS, (len(candidates), len(temporal), len(selected)))),
            "rs20Top20": top20, "firstFailureCounts": {
                "current": _domain(failures["current"], CURRENT_FAILURES),
                "formationAll": _domain(failures["formationAll"], FORMATION_FAILURES),
                "formationEvaluatedByParent": _domain(failures["formationEvaluatedByParent"], FORMATION_FAILURES)},
            "endToEndCounts": _domain(dispositions, DISPOSITIONS)}
        checks.append(_verify_day(row, parent[day]))
        daily_rows.append(row)
        symbol_rows.extend(states)
        total_activation.update(activations); total_formation.update(formations)
        total_dispositions.update(dispositions); total_candidates.update(row["counts"])
        for key in failures:
            total_failures[key].update(failures[key])
        if progress is not None and (len(daily_rows) % 10 == 0 or at == len(calendar) - 1):
            progress({"phase": "leader-stage-coverage", "date": day, "processedFocusDays": len(daily_rows),
                      "focusDayCount": len(targets), "symbolDayCount": len(symbol_rows)})
    counts = {"sampleCount": len(symbols), "focusDayCount": len(targets), "rankDayCount": len(rank_rows),
        "symbolDayCount": len(symbol_rows), "parentReconciledDayCount": len(checks),
        **{key: total_candidates[key] for key in COUNT_FIELDS},
        "activationCounts": _domain(total_activation, ("candidate", "notTriggered", "unknown")),
        "formationCounts": _domain(total_formation, ("passed", "failed", "unknown")),
        "firstFailureCounts": {"current": _domain(total_failures["current"], CURRENT_FAILURES),
            "formationAll": _domain(total_failures["formationAll"], FORMATION_FAILURES),
            "formationEvaluatedByParent": _domain(total_failures["formationEvaluatedByParent"], FORMATION_FAILURES)},
        "endToEndCounts": _domain(total_dispositions, DISPOSITIONS),
        "top20RowCount": sum(len(row["rs20Top20"]) for row in daily_rows)}
    return {"metadata": {"version": VERSION, "calendar": calendar, "targetDates": targets, "symbols": symbols,
                "focusStart": start, "focusEnd": end, "rankClockStart": calendar[first_rank],
                "rankClockDates": [row["date"] for row in rank_rows], "basis": deepcopy(BASIS),
                "newSignals": 0, "accountExecutions": 0, "targetAchieved": False},
            "rankRows": rank_rows, "dailyRows": daily_rows, "symbolRows": symbol_rows, "counts": counts,
            "verification": {"passed": True, "parentDailyChecks": checks,
                "rankStreamComparison": "Required externally against frozen parent rank-evidence; complete rebuilt rows supplied"}}
