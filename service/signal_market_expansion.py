"""Pure, fixed daily entry gate; reads no files and changes no event policy.

The caller binds the audited panel and checks its exact exchange-calendar
T-5 date. Missing numerical evidence is unknown; wrong row identity is an
integrity error. Only marketGate='true' permits a new signal to be replayed.
"""
from __future__ import annotations

from datetime import date
import math


VERSION = "market-expansion-gate-v1"
CANDIDATE_ID = "rawPersistentPriceLeaderMarketExpansionDiagnostic"
SOURCE_FEATURE_VERSION = "market-state-observations-v1"
# Last two numerical bounds are respectively raw return proportion and
# breadth-change percentage points, not a five-session investment return.
CONDITIONS = (
    ("price20.coverage", ">=", .90, 0, 1),
    ("matched5.available", "is", True, None, None),
    ("matched5.pairedCoverageFixed", ">=", .90, 0, 1),
    ("price20.medianAbsoluteReturn20", ">", 0, -1, None),
    ("matched5.matchedBreadthChange5", ">", 0, -100, 100),
)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


def evaluate_market_gate(day, target_index, market_row):
    """Return all five predicates and an evidence-first three-valued gate.

    target_index is the zero-based ordinal in the frozen target calendar. Its
    first five dates have no frozen T-5 endpoint. The runner must validate the
    exact earlierDate against that calendar before calling this pure function.
    Required values use exact binary floating-point comparisons, with no
    epsilon, clipping, minimum candidate count or parameter adaptation.
    """
    day = _day(day)
    if type(target_index) is not int or target_index < 0:
        raise ValueError("target_index must be a nonnegative integer, excluding bool")
    if not isinstance(market_row, dict) or market_row.get("date") != day:
        raise ValueError("Market row date must match the requested target session")
    if market_row.get("featureVersion") != SOURCE_FEATURE_VERSION:
        raise ValueError("Market source feature version mismatch")
    matched = market_row.get("matched5")
    if isinstance(matched, dict):
        earlier = matched.get("earlierDate")
        if earlier is not None and _day(earlier) >= day:
            raise ValueError("Earlier comparison date must precede the target session")
        if matched.get("available") is True and earlier is None:
            raise ValueError("Available comparison requires its earlier date")
    predicates = {}
    for field, operator, threshold, lower, upper in CONDITIONS:
        group, name = field.split(".")
        source = market_row.get(group)
        value = source.get(name) if isinstance(source, dict) else None
        reasons = []
        passed = None
        if operator == "is":
            if type(value) is not bool:
                reasons.append("requiredBooleanUnavailable:" + field)
            else:
                passed = value is True
                if not value:
                    reasons.append("matchedComparisonUnavailable")
        elif not _finite(value):
            reasons.append("requiredFiniteNumberUnavailable:" + field)
        elif value < lower or upper is not None and value > upper:
            reasons.append("requiredNumberOutOfRange:" + field)
        else:
            passed = value >= threshold if operator == ">=" else value > threshold
        predicates[field] = {"value": value if type(value) is bool or _finite(value) else None,
            "operator": operator, "threshold": threshold, "passed": passed, "unknownReasons": reasons}
    unknown = {reason for predicate in predicates.values() for reason in predicate["unknownReasons"]}
    if target_index < 5:
        unknown.add("firstFiveTargetSessionsUnavailable")
    failed = [field for field, predicate in predicates.items() if predicate["passed"] is False]
    gate = "unknown" if unknown else "false" if failed else "true"
    return {"date": day, "targetIndex": target_index, "version": VERSION, "candidateId": CANDIDATE_ID,
            "marketGate": gate, "newEntryAllowed": gate == "true", "predicates": predicates,
            "failedPredicates": failed, "unknownReasons": sorted(unknown),
            "certified": False, "targetAchieved": False}
