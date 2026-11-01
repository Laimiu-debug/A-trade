"""Freeze common current-leader Top5 before applying historical persistence.

This is an evidence-only transformation of audited leader-lag daily candidates.
It does not calculate raw features, peer groups, account states or new rankings
other than the explicitly registered common A-candidate ordering.
"""
from collections import Counter
from copy import deepcopy

from .research_leader_lag import THRESHOLDS, _day, _digest, _finite


VERSION = "leader-persistence-admission-v1"
STRATEGY_ID = "leaderPersistenceAdmissionV1"
PARENT_STRATEGY_ID = "leaderLagFollowersV1"
POLICIES = ("knownAll", "persistent", "nonpersistent")
FORMATION_STATES = ("passed", "failed", "unknown")
ADMISSION_STATES = ("included", "excluded", "unknown")
FIELDS = tuple(name for name, _, _ in THRESHOLDS)
BASIS = {
    "protocol": "docs/leader-persistence-admission-v1.json",
    "input": "All audited parent daily leaderCandidates, before the parent F filter; parent evidence is retained unchanged",
    "symbols": "Runner-provided sorted identities from the same bound account dataset; not the union of observed candidates",
    "commonOrder": ["meanStrengthPercentile20 descending", "relativeReturn60Percentile descending", "symbol ascending"],
    "commonTop5": "Take first5 A candidates before inspecting F status; unknown and excluded ranks are never backfilled",
    "historicalClock": "F equals A minus10 exact source-calendar slots; F does not require breakout",
    "knownAll": "Include commonTop5 with F passed or failed; exclude unknown from every policy",
    "persistent": "Include commonTop5 with F passed",
    "nonpersistent": "Include commonTop5 with F failed",
    "identity": "New opportunity ID identifies the A candidate, not a parent event or follower; all policies share it",
    "signalClock": "SignalDate=A close; inherited fixed-account next-session one-attempt execution applies externally",
    "scope": "Fixed board-cohort development comparison; no raw feature recomputation, peer requirement, market-date gate or account-state selection",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _dates(values, label):
    _require(isinstance(values, list) and values and values == sorted(set(values)), label + " must be unique and increasing")
    for day in values:
        _day(day)
    return values


def _evidence(value, day, label):
    _require(isinstance(value, dict) and value.get("date") == day and value.get("status") in FORMATION_STATES,
             label + " identity/status differs")
    fields, predicates, reasons = value.get("values"), value.get("predicates"), value.get("reasons")
    _require(isinstance(fields, dict) and set(fields) == set(FIELDS)
             and isinstance(predicates, dict) and set(predicates) == set(FIELDS)
             and isinstance(reasons, list), label + " condition schema differs")
    for name, operator, threshold in THRESHOLDS:
        number, predicate = fields[name], predicates[name]
        _require(number is None or _finite(number), label + " nonfinite condition value")
        expected = None if number is None else (number >= threshold if operator == ">=" else number <= threshold)
        _require(type(predicate) is type(expected) and predicate == expected, label + " predicate/value disagreement")
    if value["status"] == "unknown":
        _require(bool(reasons), label + " unknown without evidence reason")
    else:
        _require(not reasons and all(v is not None for v in fields.values()), label + " known evidence has missing condition")
        _require(value["status"] == ("passed" if all(predicates.values()) else "failed"), label + " status/predicate disagreement")
    return value


def _count_map(values, names, label):
    _require(isinstance(values, dict) and set(values) == set(names)
             and all(type(v) is int and v >= 0 for v in values.values()), label + " count domain differs")
    return values


def _order(candidate):
    values = candidate["currentEvidence"]["values"]
    return -values["meanStrengthPercentile20"], -values["relativeReturn60Percentile"], candidate["symbol"]


def _candidates(row, calendar, positions, symbols):
    day, at = row["formationDate"], positions[row["formationDate"]]
    formation = calendar[at - 10] if at >= 10 else None
    _require(row.get("peerFormationDate") == formation, "Parent F must be A minus10 source sessions")
    candidates = row.get("leaderCandidates")
    _require(isinstance(candidates, list), "All parent current candidates are required")
    activation = _count_map(row.get("activationCounts"), ("candidate", "notTriggered", "unknown"), "Parent activation")
    formation_counts = _count_map(row.get("formationCounts"), FORMATION_STATES, "Parent formation")
    _require(sum(activation.values()) == len(symbols) and activation["candidate"] == len(candidates),
             "Parent full-cohort/candidate denominator differs")
    previous, found = None, set()
    for candidate in candidates:
        _require(isinstance(candidate, dict), "Parent candidate must be an object")
        symbol = candidate.get("symbol")
        _require(symbol in symbols and symbol not in found and (previous is None or previous < symbol),
                 "Parent candidates must be unique source symbols in original symbol order")
        previous = symbol; found.add(symbol)
        _require(candidate.get("formationDate") == day and candidate.get("peerFormationDate") == formation,
                 "Parent candidate clock differs")
        current = _evidence(candidate.get("currentEvidence"), day, "Current evidence")
        _require(current["status"] == "passed" and current.get("breakout") is True
                 and _finite(current.get("close")) and _finite(current.get("prior5High"))
                 and current["close"] > current["prior5High"] > 0, "Parent candidate is not A strong-plus-breakout")
        prior = candidate.get("formationEvidence")
        if prior is None:
            status, reason = "unknown", "formationStateUnavailable"
        else:
            _require(formation is not None, "F evidence cannot precede the source calendar")
            status = _evidence(prior, formation, "Formation evidence")["status"]
            reason = {"passed": "formationFiveConditionsPassed", "failed": "formationFiveConditionsFailed",
                      "unknown": "formationEvidenceUnknown"}[status]
        _require(candidate.get("formationStatus") == status and candidate.get("formationReason") == reason,
                 "Parent formation classification differs from its evidence")
    actual_counts = Counter(c["formationStatus"] for c in candidates)
    _require(formation_counts == {k: actual_counts[k] for k in FORMATION_STATES}, "Parent F counts disagree with all candidates")
    # These are provenance checks only. New common ordering below does not use
    # the old ranks, which were assigned after excluding F failed/unknown.
    old_ranked = sorted((c for c in candidates if c["formationStatus"] == "passed"), key=_order)
    old_ranks = {c["symbol"]: i for i, c in enumerate(old_ranked, 1)}
    for candidate in candidates:
        rank = old_ranks.get(candidate["symbol"])
        _require(candidate.get("originalLeaderRank") == rank
                 and type(candidate.get("selectedTop5")) is bool
                 and candidate["selectedTop5"] == (rank is not None and rank <= 5), "Parent old rank provenance differs")
    _require(row.get("leaderRanking") == [{"symbol": c["symbol"], "originalLeaderRank": i, "selectedTop5": i <= 5}
                 for i, c in enumerate(old_ranked, 1)], "Parent temporal ranking differs")
    expected_ids = [f"{PARENT_STRATEGY_ID}:{day}:{c['symbol']}" for c in old_ranked[:5]]
    _require(row.get("leaderEventIds") == expected_ids, "Parent leader event provenance differs")
    counts = row.get("counts", {})
    _require(all(type(counts.get(k)) is int and counts[k] == v for k, v in (
        ("currentCandidateCount", len(candidates)), ("temporalCandidateCount", len(old_ranked)),
        ("leaderEventCount", min(5, len(old_ranked))))), "Parent daily counts differ")
    return candidates


def _empty_counts():
    return {"currentCandidateCount": 0, "top5OpportunityCount": 0, "rankExcludedCount": 0,
            "selectedFormationCounts": {s: 0 for s in FORMATION_STATES},
            "policyCounts": {p: {s: 0 for s in ADMISSION_STATES} for p in POLICIES},
            "signals": {p: 0 for p in POLICIES}, "notScheduled": {p: 0 for p in POLICIES}}


def _admission(status, policy):
    if status == "unknown":
        return {"status": "unknown", "reason": "formationEvidenceUnknown"}
    included = policy == "knownAll" or (policy == "persistent") == (status == "passed")
    reason = "formationEvidenceKnown" if policy == "knownAll" else (
        "formationPersistent" if status == "passed" else "formationNotPersistent")
    return {"status": "included" if included else "excluded", "reason": reason}


def _add_counts(total, current):
    for key in ("currentCandidateCount", "top5OpportunityCount", "rankExcludedCount"):
        total[key] += current[key]
    for state in FORMATION_STATES:
        total["selectedFormationCounts"][state] += current["selectedFormationCounts"][state]
    for policy in POLICIES:
        for state in ADMISSION_STATES:
            total["policyCounts"][policy][state] += current["policyCounts"][policy][state]
        for key in ("signals", "notScheduled"):
            total[key][policy] += current[key][policy]


def build_leader_persistence_admission(parent_metadata, parent_daily_rows, *, start, end, progress=None):
    """Create three masks over a common A-ranked Top5, without market reads.

    ``parent_metadata`` is the immutable parent metadata extended by the runner
    with the full sorted ``symbols`` from its bound account source. Original
    metadata and every parent row remain unchanged. Extra dates outside the
    requested interval are checked structurally, not evaluated numerically.
    """
    start, end = _day(start), _day(end)
    _require(start <= end and isinstance(parent_metadata, dict), "Ordered research interval required")
    _require(parent_metadata.get("strategyId") == PARENT_STRATEGY_ID, "Wrong parent strategy identity")
    supplied = _dates(parent_metadata.get("calendar"), "Parent source calendar")
    parent_targets = _dates(parent_metadata.get("targetDates"), "Parent target dates")
    _require(parent_targets == [d for d in supplied if parent_targets[0] <= d <= parent_targets[-1]],
             "Parent target dates must retain every source session")
    symbols = parent_metadata.get("symbols")
    _require(isinstance(symbols, list) and symbols and all(isinstance(s, str) and s.strip() for s in symbols)
             and symbols == sorted(set(symbols)), "Explicit sorted complete source symbols required")
    sample_count = parent_metadata.get("counts", {}).get("sampleCount")
    _require(type(sample_count) is int and sample_count == len(symbols), "Full source symbol denominator differs")
    calendar = [d for d in supplied if d <= end]
    targets = [d for d in parent_targets if start <= d <= end]
    _require(targets and targets == [d for d in calendar if start <= d <= end], "Requested targets differ from complete parent interval")
    _require(isinstance(parent_daily_rows, list) and all(isinstance(r, dict) for r in parent_daily_rows), "Parent daily list required")
    _require([r.get("formationDate") for r in parent_daily_rows] == parent_targets,
             "Missing, duplicate or misordered parent daily rows")
    positions, symbol_set, wanted = {d: i for i, d in enumerate(calendar)}, set(symbols), set(targets)
    daily_rows, opportunities = [], []
    schedules, counts = {p: [] for p in POLICIES}, _empty_counts()
    for parent in parent_daily_rows:
        day = parent["formationDate"]
        if day not in wanted:
            continue
        candidates = _candidates(parent, calendar, positions, symbol_set)
        ordered = sorted(candidates, key=_order)
        parent_sha = _digest(parent)
        daily_counts = _empty_counts()
        daily_counts.update(currentCandidateCount=len(ordered), top5OpportunityCount=min(5, len(ordered)),
                            rankExcludedCount=max(0, len(ordered) - 5))
        ranked = [{"symbol": c["symbol"], "originalRank": rank, "selectedTop5": rank <= 5,
                   "formationStatus": c["formationStatus"], "parentCandidate": deepcopy(c)}
                  for rank, c in enumerate(ordered, 1)]
        ids = []
        for item in ranked[:5]:
            status, symbol, rank = item["formationStatus"], item["symbol"], item["originalRank"]
            identity = f"{STRATEGY_ID}:{day}:{symbol}"
            daily_counts["selectedFormationCounts"][status] += 1
            admissions, signals = {}, {}
            for policy in POLICIES:
                decision = _admission(status, policy)
                admissions[policy] = decision
                daily_counts["policyCounts"][policy][decision["status"]] += 1
                included = decision["status"] == "included"
                signal = {"signalId": identity, "strategyId": STRATEGY_ID, "symbol": symbol,
                          "signalDate": day, "originalRank": rank} if included else None
                signals[policy] = signal
                daily_counts["signals" if included else "notScheduled"][policy] += 1
                if included:
                    schedules[policy].append(deepcopy(signal))
            opportunities.append({"signalId": identity, "formationId": identity, "strategyId": STRATEGY_ID,
                "symbol": symbol, "signalDate": day, "originalRank": rank, "formationDate": day,
                "peerFormationDate": parent["peerFormationDate"], "formationStatus": status,
                "parentDailySha256": parent_sha, "parentCandidate": deepcopy(item["parentCandidate"]),
                "policyAdmission": admissions, "signals": signals})
            ids.append(identity)
        daily_rows.append({"formationDate": day, "peerFormationDate": parent["peerFormationDate"],
            "parentDailySha256": parent_sha, "parentActivationCounts": deepcopy(parent["activationCounts"]),
            "parentFormationCounts": deepcopy(parent["formationCounts"]), "currentCandidates": ranked,
            "commonRanking": [{k: deepcopy(item[k]) for k in ("symbol", "originalRank", "selectedTop5", "formationStatus")}
                              for item in ranked], "selectedSignalIds": ids, "counts": daily_counts})
        _add_counts(counts, daily_counts)
        if progress is not None and (len(daily_rows) % 50 == 0 or day == targets[-1]):
            progress({"phase": "leader-persistence-admission", "date": day, "processedTargetDays": len(daily_rows),
                      "targetDayCount": len(targets), "opportunityCount": len(opportunities)})
    counts = {"sampleCount": len(symbols), "targetDayCount": len(targets), **counts}
    metadata = {"version": VERSION, "strategyId": STRATEGY_ID, "parentStrategyId": PARENT_STRATEGY_ID,
        "policies": list(POLICIES), "calendar": calendar, "targetDates": targets, "symbols": list(symbols),
        "start": start, "end": end, "counts": deepcopy(counts), "basis": deepcopy(BASIS),
        "certified": False, "targetAchieved": False}
    return {"metadata": metadata, "dailyRows": daily_rows, "opportunities": opportunities,
            "schedules": schedules, "counts": counts}
