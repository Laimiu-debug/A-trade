"""Pure, unvalidated leadership-replacement proposal; no prices or trades.

The caller binds audited role/market/feature sources and the complete target
calendar. Supply only the available T-2..T role cross-sections and T market row.
This module checks stream identity but does not certify the supplied Q labels.
Positions are deliberately absent: all ranking/control slots precede external
active-event handling, which must carry across years without replacing slots.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import math


VERSION = "leadership-replacement-proposal-v1"
CANDIDATE_ID = "rawLeadershipReplacementDiagnostic"
CONTROL_SEED = "a-trade-signal-same-day-base-pool-control-v1"
FEATURE_VERSION = "market-state-observations-v1"
Q_DEFINITION = "persistent-price-strength-five-gates-v1"
STATES = ("true", "false", "unknown")
TOP_N = 5
RANK_FIELDS = ("meanStrengthPercentile20", "relativeReturn60Percentile")
UNKNOWN_BASE_REASONS = {"invalidHistory", "missingDailyBar", "notObservedYet", "strictHistoryMissing"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return value


def _bounded(value, low=0, high=1):
    try:
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        return False


def _symbols(symbols):
    if (not isinstance(symbols, (list, tuple)) or not symbols
            or any(not isinstance(s, str) or not s for s in symbols)
            or list(symbols) != sorted(set(symbols))):
        raise ValueError("Supply the complete sorted unique fixed symbol list")


def _combine(states):
    return "unknown" if "unknown" in states else "false" if "false" in states else "true"


def _qualification(row):
    sidecar = row.get("baseEligibility")
    if (not isinstance(sidecar, dict) or type(sidecar.get("eligible")) is not bool
            or not isinstance(sidecar.get("reason"), str) or not sidecar["reason"]
            or not isinstance(sidecar.get("unknown"), list)
            or not all(isinstance(v, str) for v in sidecar["unknown"])):
        return {"state": "unknown", "reason": "baseEligibilityUnavailable"}
    if sidecar["reason"] in UNKNOWN_BASE_REASONS:
        return {"state": "unknown", "reason": sidecar["reason"]}
    return {"state": "true" if sidecar["eligible"] else "false", "reason": sidecar["reason"]}


def _inputs(symbols, target_dates, target_index, role_days, market_row):
    _symbols(symbols)
    if (not isinstance(target_dates, (list, tuple)) or not target_dates
            or any(_day(d) != d for d in target_dates)
            or list(target_dates) != sorted(set(target_dates))
            or type(target_index) is not int or not 0 <= target_index < len(target_dates)):
        raise ValueError("Supply the ordered target calendar and an exact session index")
    expected = target_dates[max(0, target_index-2):target_index+1]
    if not isinstance(role_days, (list, tuple)) or len(role_days) != len(expected):
        raise ValueError("Supply exactly the available T-2..T target cross-sections")
    for day, rows in zip(expected, role_days):
        if not isinstance(rows, (list, tuple)) or len(rows) != len(symbols):
            raise ValueError("Every role session requires every fixed symbol")
        for symbol, row in zip(symbols, rows):
            if (not isinstance(row, dict) or row.get("date") != day or row.get("symbol") != symbol
                    or row.get("featureVersion") != FEATURE_VERSION or row.get("qDefinition") != Q_DEFINITION
                    or row.get("qState") not in STATES):
                raise ValueError("Role identity, order, version or Q state mismatch")
    if (not isinstance(market_row, dict) or market_row.get("date") != target_dates[target_index]
            or market_row.get("featureVersion") != FEATURE_VERSION
            or market_row.get("qDefinition") != Q_DEFINITION
            or type(market_row.get("fixedPopulation")) is not int
            or market_row["fixedPopulation"] != len(symbols)):
        raise ValueError("Market identity/version/population mismatch")


def _cohort(role_days):
    if len(role_days) < 3:
        return {"state": "unknown", "anchorDate": None, "members": None, "memberHash": None,
                "denominator": None, "threshold": None, "anchorQCounts": None, "endpoints": [],
                "unknownReasons": ["firstTwoRoleSessionsUnavailable"], "failedPredicates": []}
    anchor = role_days[0]
    members = [row["symbol"] for row in anchor if row["qState"] == "true"]
    member_set = set(members)
    size, endpoints = len(members), []
    threshold = (size+1)//2
    for rows in role_days[1:]:
        states = {row["symbol"]: row["qState"] for row in rows if row["symbol"] in member_set}
        counts = {state: sum(value == state for value in states.values()) for state in STATES}
        false, unknown = counts["false"], counts["unknown"]
        state = ("false" if not size else "true" if false >= threshold else
                 "false" if false+unknown < threshold else "unknown")
        endpoints.append({"date": rows[0]["date"], "state": state, "counts": counts, "memberStates": states,
                          "failureFractionLower": false/size if size else None,
                          "failureFractionUpper": (false+unknown)/size if size else None})
    return {"state": _combine([row["state"] for row in endpoints]), "anchorDate": anchor[0]["date"],
            "members": members, "memberHash": _digest(members), "denominator": size, "threshold": threshold,
            "anchorQCounts": {state: sum(row["qState"] == state for row in anchor) for state in STATES},
            "endpoints": endpoints,
            "unknownReasons": ["oldCohortThresholdUnresolved:"+row["date"] for row in endpoints if row["state"] == "unknown"],
            "failedPredicates": (["emptyObservedOldCohort"] if not size else
                                 ["oldCohortDeterioration:"+row["date"] for row in endpoints if row["state"] == "false"])}


def _market(target_dates, index, row):
    matched = row.get("matched5")
    expected = target_dates[index-5] if index >= 5 else None
    if not isinstance(matched, dict) or "earlierDate" not in matched or matched["earlierDate"] != expected:
        raise ValueError("Market comparison must use the exact T-5 target session")
    if index < 5 and matched.get("available") is True:
        raise ValueError("Pre-target matched history cannot be declared available")
    price = row.get("price20")
    price = price if isinstance(price, dict) else {}
    specs = (("price20.coverage", price.get("coverage"), .9, 0, 1),
             ("matched5.pairedCoverageFixed", matched.get("pairedCoverageFixed"), .9, 0, 1),
             ("matched5.matchedBreadthChange5", matched.get("matchedBreadthChange5"), 0, -100, 100))
    predicates = {}
    for name, value, threshold, low, high in specs:
        valid = _bounded(value, low, high)
        predicates[name] = {"value": value if valid else None, "operator": ">=", "threshold": threshold,
                            "passed": value >= threshold if valid else None,
                            "unknownReasons": [] if valid else ["requiredMarketValueUnavailable:"+name]}
    available = matched.get("available")
    predicates["matched5.available"] = {"value": available if type(available) is bool else None,
        "operator": "is", "threshold": True, "passed": available if type(available) is bool else None,
        "unknownReasons": [] if available is True else ["matchedComparisonUnavailable"]}
    unknown = sorted({r for p in predicates.values() for r in p["unknownReasons"]}
                     | ({"firstFiveTargetSessionsUnavailable"} if index < 5 else set()))
    failed = [name for name, p in predicates.items() if p["passed"] is False]
    return {"state": "unknown" if unknown else "false" if failed else "true", "predicates": predicates,
            "earlierDate": expected, "unknownReasons": unknown, "failedPredicates": failed,
            "breadthChangeUnit": "percentage-points"}


def rank_replacement_signals(signals):
    """Return full lexicographic ranks and pre-active Top5; never accept positions."""
    rows = deepcopy(list(signals))
    if len({row.get("symbol") for row in rows}) != len(rows):
        raise ValueError("Duplicate candidate symbol")
    for row in rows:
        if (row.get("strategyId") != CANDIDATE_ID or not isinstance(row.get("symbol"), str) or not row["symbol"]
                or any(not _bounded(row.get(field)) for field in RANK_FIELDS)):
            raise ValueError("Candidate ranking identity or bounded values unavailable")
        _day(row.get("signalDate"))
    if len({row["signalDate"] for row in rows}) > 1:
        raise ValueError("Rank one session only")
    rows.sort(key=lambda row: (-row[RANK_FIELDS[0]], -row[RANK_FIELDS[1]], row["symbol"]))
    for rank, row in enumerate(rows, 1):
        row.update(rank=rank, selectedTop5=rank <= TOP_N, score=row[RANK_FIELDS[0]], rawScore=row[RANK_FIELDS[0]])
    return rows


def select_replacement_controls(day, symbols, top5_signals, eligibility_rows):
    """Draw once from full audited base-eligible minus this rule's pre-active Top5.

    Controls' own Q is deliberately irrelevant. The caller must retain original
    pair ranks after independent active exclusions; no later control redraw.
    """
    _day(day)
    _symbols(symbols)
    if (not isinstance(eligibility_rows, (list, tuple)) or len(eligibility_rows) != len(symbols)
            or any(not isinstance(r, dict) or r.get("symbol") != s or r.get("date") != day
                   for s, r in zip(symbols, eligibility_rows))):
        raise ValueError("Controls require the complete same-date qualification cross-section")
    if len(top5_signals) > TOP_N or len({r.get("symbol") for r in top5_signals}) != len(top5_signals):
        raise ValueError("Supply unique original Top5 slots")
    for rank, row in enumerate(top5_signals, 1):
        if (row.get("strategyId") != CANDIDATE_ID or row.get("signalDate") != day or row.get("symbol") not in symbols
                or type(row.get("rank")) is not int or row["rank"] != rank or row.get("selectedTop5") is not True):
            raise ValueError("Control pairing requires original contiguous pre-active ranks")
    selected = {r["symbol"] for r in top5_signals}
    eligible = {r["symbol"] for r in eligibility_rows if _qualification(r)["state"] == "true"}
    if not selected <= eligible:
        raise ValueError("Strategy slots must satisfy the same base qualification")
    pool = sorted(eligible-selected)
    sampled = sorted(pool, key=lambda s: (hashlib.sha256(f"{CONTROL_SEED}:{day}:{CANDIDATE_ID}:{s}".encode()).hexdigest(), s))[:len(top5_signals)]
    controls = [{"strategyId": "control_"+CANDIDATE_ID, "pairedStrategyId": CANDIDATE_ID,
                 "symbol": symbol, "signalDate": day, "rank": rank, "pairedStrategyRank": rank,
                 "pairedStrategySymbol": top5_signals[rank-1]["symbol"], "selectedTop5": False,
                 "score": 0., "rawScore": 0., "certified": False, "targetAchieved": False,
                 "actionEvidence": "unverified"} for rank, symbol in enumerate(sampled, 1)]
    return {"controls": controls, "matching": {"seed": CONTROL_SEED, "baseEligibleCount": len(eligible),
            "eligibleControlPoolSize": len(pool), "eligibleControlPoolHash": _digest(pool),
            "requestedCount": len(top5_signals), "matchedCount": len(controls),
            "shortfall": len(top5_signals)-len(controls), "selectedSymbols": sampled}}


def evaluate_replacement_day(symbols, target_dates, target_index, role_days, market_row):
    """Evaluate every fixed symbol, returning predicates and pre-active selection.

    Missing numeric/qualification evidence is unknown; malformed row identity,
    population, date/order/version or Q enum raises ValueError. The first five
    target sessions remain unknown; indicator warmup is not role-history warmup.
    No calendar-year reset, active state, labels, prices or return calculation is
    performed. Inputs and outputs do not share mutable state.
    """
    _inputs(symbols, target_dates, target_index, role_days, market_row)
    day = target_dates[target_index]
    cohort, market = _cohort(role_days), _market(target_dates, target_index, market_row)
    evaluations, candidates = [], []
    for position, symbol in enumerate(symbols):
        current = role_days[-1][position]
        history = [{"date": rows[position]["date"], "qState": rows[position]["qState"]} for rows in role_days]
        unknown, failed = list(cohort["unknownReasons"])+market["unknownReasons"], list(cohort["failedPredicates"])+market["failedPredicates"]
        challenger_unknown = ["requiredQStateUnavailable:"+r["date"] for r in history if r["qState"] == "unknown"]
        if len(history) < 3:
            challenger_unknown.append("firstTwoRoleSessionsUnavailable")
        desired = ("false", "true", "true")[-len(history):]
        challenger_failed = ["challengerQ:"+r["date"] for r, expected in zip(history, desired)
                             if r["qState"] != "unknown" and r["qState"] != expected]
        challenger = "unknown" if challenger_unknown else "false" if challenger_failed else "true"
        unknown += challenger_unknown
        failed += challenger_failed
        qualification = _qualification(current)
        if qualification["state"] == "unknown":
            unknown.append("entryQualification:"+qualification["reason"])
        elif qualification["state"] == "false":
            failed.append("entryQualification:"+qualification["reason"])
        values = current.get("criterionValues")
        values = values if isinstance(values, dict) else {}
        rank_values = {field: values.get(field) if _bounded(values.get(field)) else None for field in RANK_FIELDS}
        unknown += ["rankingEvidenceUnavailable:"+field for field, value in rank_values.items() if value is None]
        state = "unknown" if unknown else "false" if failed else "true"
        evaluations.append({"date": day, "symbol": symbol, "candidateGate": state, "oldCohortGate": cohort["state"],
            "marketGate": market["state"], "challengerGate": challenger, "challengerHistory": history,
            "qualification": qualification, "baseEligibility": deepcopy(current.get("baseEligibility")),
            "rankingValues": rank_values, "unknownReasons": sorted(set(unknown)), "failedPredicates": sorted(set(failed)),
            "certified": False, "targetAchieved": False})
        if state == "true":
            candidates.append({"strategyId": CANDIDATE_ID, "symbol": symbol, "signalDate": day, **rank_values,
                               "certified": False, "targetAchieved": False, "actionEvidence": "unverified"})
    ranked = rank_replacement_signals(candidates)
    top = [r for r in ranked if r["selectedTop5"]]
    control = select_replacement_controls(day, symbols, top, role_days[-1])
    counts = Counter(row["candidateGate"] for row in evaluations)
    return {"version": VERSION, "candidateId": CANDIDATE_ID, "date": day, "targetIndex": target_index,
            "daily": {"oldCohort": cohort, "market": market, "evaluatedSecurities": len(symbols),
                      "candidateGateCounts": {state: counts[state] for state in STATES}},
            "evaluations": evaluations, "rankedCandidates": ranked, "signals": deepcopy(top)+control["controls"],
            "selection": {"date": day, "strategyId": CANDIDATE_ID, "rawCandidateCount": len(ranked),
                          "fullRanking": deepcopy(ranked), "selectedTop5Symbols": [r["symbol"] for r in top],
                          "controlMatching": control["matching"], "activeExclusionApplied": False},
            "status": "proposal-only-unvalidated", "returnsCalculated": False, "certified": False, "targetAchieved": False}
