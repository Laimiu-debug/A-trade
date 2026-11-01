"""Pure causal repair/resilience gates over an already bound observation panel.

No prices, outcomes, files, controls or active positions are read here. Missing
numeric evidence is unknown; malformed row identity/calendar is an integrity
error. The caller owns complete population and source-hash validation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math


VERSION = "market-repair-resilience-gate-v1"
CANDIDATE_ID = "rawMarketRepairResilientLeaderBreakoutDiagnostic"
SOURCE_FEATURE_VERSION = "market-state-observations-v1"
Q_DEFINITION = "persistent-price-strength-five-gates-v1"
STATES = ("true", "false", "unknown")
QUALITY = (("price20.coverage", ">=", .9, 0, 1),
           ("matched5.available", "is", True, None, None),
           ("matched5.pairedCoverageFixed", ">=", .9, 0, 1),
           ("matched5.matchedBreadthChange5", "finiteInDomain", None, -100, 100))


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Dates must be YYYY-MM-DD strings")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Noncanonical date")
    except ValueError as exc:
        raise ValueError("Dates must be YYYY-MM-DD strings") from exc
    return value


def _calendar(target_dates, target_index):
    if not isinstance(target_dates, (list, tuple)) or not target_dates:
        raise ValueError("A nonempty ordered target calendar is required")
    days = tuple(_day(day) for day in target_dates)
    if tuple(sorted(set(days))) != days:
        raise ValueError("Target calendar must be unique and ordered")
    if type(target_index) is not int or not 0 <= target_index < len(days):
        raise ValueError("target_index must identify an existing target session")
    return days


def _rows(rows, expected_dates):
    if not isinstance(rows, (list, tuple)) or len(rows) != len(expected_dates):
        raise ValueError("Exactly the available required calendar slots must be supplied")
    for row, day in zip(rows, expected_dates):
        if not isinstance(row, dict) or row.get("date") != day:
            raise ValueError("Missing, duplicate, extra or out-of-order observation row")
        if row.get("featureVersion") != SOURCE_FEATURE_VERSION:
            raise ValueError("Source observation featureVersion mismatch")


def _numeric(row, group, field):
    values = row.get(group)
    return values.get(field) if isinstance(values, dict) else None


def _predicate(value, operator, threshold, lower, upper, label):
    reasons, passed = [], None
    if operator == "is":
        if type(value) is not bool:
            reasons.append("requiredBooleanUnavailable:" + label)
        else:
            passed = value
            if not value:
                reasons.append("matchedComparisonUnavailable:" + label)
    elif not _finite(value):
        reasons.append("requiredFiniteNumberUnavailable:" + label)
    elif value < lower or value > upper:
        reasons.append("requiredNumberOutOfRange:" + label)
    else:
        passed = value >= threshold if operator == ">=" else True
    return {"value": value if type(value) is bool or _finite(value) else None,
            "operator": operator, "threshold": threshold, "domain": [lower, upper] if lower is not None else None,
            "passed": passed, "unknownReasons": reasons}


def _state(predicates, extra_unknown=()):
    failed = [name for name, value in predicates.items() if value["passed"] is False]
    unknown = sorted(set(extra_unknown).union(reason for value in predicates.values()
                                              for reason in value["unknownReasons"]))
    return "unknown" if unknown else "false" if failed else "true", failed, unknown


def evaluate_market_repair(target_dates, target_index, market_rows):
    """Evaluate T-5..T market evidence without adding a return-level filter.

    market_rows contains every *available target* slot in that interval. The
    first ten target indices cannot form this six-by-five-day comparison. A
    later whole-row gap is not equivalent to that legitimate initial boundary.
    """
    days = _calendar(target_dates, target_index)
    start = max(0, target_index-5)
    _rows(market_rows, days[start:target_index+1])
    predicates, history, differences = {}, [], {}
    for index, row in zip(range(start, target_index+1), market_rows):
        day = days[index]
        matched = row.get("matched5")
        expected_earlier = days[index-5] if index >= 5 else None
        if not isinstance(matched, dict) or "earlierDate" not in matched or matched["earlierDate"] != expected_earlier:
            raise ValueError("Market earlierDate must equal the exact fifth earlier target date")
        if index < 5 and matched.get("available") is True:
            raise ValueError("A pre-target comparison cannot be declared available")
        evidence = {"date": day, "targetIndex": index, "featureVersion": SOURCE_FEATURE_VERSION,
                    "earlierDate": expected_earlier, "predicates": {}}
        for field, operator, threshold, lower, upper in QUALITY:
            group, name = field.split(".")
            key = day + ":" + field
            predicate = _predicate(_numeric(row, group, name), operator, threshold, lower, upper, key)
            predicates[key] = predicate
            evidence["predicates"][field] = deepcopy(predicate)
        diff = evidence["predicates"]["matched5.matchedBreadthChange5"]
        differences[index] = diff["value"] if diff["passed"] is True else None
        history.append(evidence)

    previous_values = [differences.get(i) for i in range(target_index-5, target_index)]
    count = sum(value < 0 for value in previous_values) if all(value is not None for value in previous_values) else None
    previous, current = differences.get(target_index-1), differences[target_index]
    for name, value, operator, threshold, passed in (
            ("priorFiveNegativeCount", count, ">=", 3, None if count is None else count >= 3),
            ("previousNonPositive", previous, "<=", 0, None if previous is None else previous <= 0),
            ("currentPositive", current, ">", 0, None if current is None else current > 0)):
        predicates[name] = {"value": value, "operator": operator, "threshold": threshold, "passed": passed,
                            "unknownReasons": ["requiredDifferenceWindowUnavailable:" + name] if value is None else []}
    extra = ["firstTenTargetSessionsUnavailable"] if target_index < 10 else []
    state, failed, unknown = _state(predicates, extra)
    return {"date": days[target_index], "targetIndex": target_index, "version": VERSION,
            "candidateId": CANDIDATE_ID, "marketGate": state, "newEntryAllowed": state == "true",
            "predicates": predicates, "failedPredicates": failed, "unknownReasons": unknown,
            "history": history, "certified": False, "targetAchieved": False}


def _market_identity(market_gate, day, target_index):
    if (not isinstance(market_gate, dict) or market_gate.get("date") != day
            or type(market_gate.get("targetIndex")) is not int or market_gate["targetIndex"] != target_index
            or market_gate.get("version") != VERSION or market_gate.get("candidateId") != CANDIDATE_ID
            or market_gate.get("marketGate") not in STATES):
        raise ValueError("Market gate identity/state mismatch")
    for name in ("unknownReasons", "failedPredicates"):
        values = market_gate.get(name)
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values) or len(values) != len(set(values)):
            raise ValueError("Malformed market-gate evidence")
    expected = "unknown" if market_gate["unknownReasons"] else "false" if market_gate["failedPredicates"] else "true"
    if (market_gate["marketGate"] != expected or market_gate.get("newEntryAllowed") is not (expected == "true")
            or market_gate.get("certified") is not False or market_gate.get("targetAchieved") is not False):
        raise ValueError("Contradictory market-gate evidence")
    if target_index < 10 and market_gate["marketGate"] != "unknown":
        raise ValueError("Insufficient market history cannot form a known gate")


def evaluate_resilient_gate(symbol, target_dates, target_index, role_rows, market_gate):
    """Combine the strategy symbol's five prior Q states and the market gate.

    A control inherits this result externally; its own Q is never substituted.
    This function neither validates a parent breakout nor chooses a Top5 slot.
    """
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("A nonempty strategy symbol is required")
    days = _calendar(target_dates, target_index)
    _market_identity(market_gate, days[target_index], target_index)
    start = max(0, target_index-5)
    _rows(role_rows, days[start:target_index])
    history, failed, unknown = [], list(market_gate["failedPredicates"]), set(market_gate["unknownReasons"])
    for row in role_rows:
        if row.get("symbol") != symbol or row.get("qDefinition") != Q_DEFINITION or row.get("qState") not in STATES:
            raise ValueError("Role symbol/Q definition/state mismatch")
        state, key = row["qState"], row["date"] + ":qState"
        passed = None if state == "unknown" else state == "true"
        reasons = ["requiredQStateUnavailable:" + row["date"]] if passed is None else []
        if passed is False:
            failed.append(key)
        unknown.update(reasons)
        history.append({"date": row["date"], "symbol": symbol, "featureVersion": SOURCE_FEATURE_VERSION,
                        "qDefinition": Q_DEFINITION, "qState": state, "passed": passed, "unknownReasons": reasons})
    if target_index < 5:
        unknown.add("firstFiveRoleSessionsUnavailable")
    state = "unknown" if unknown else "false" if failed else "true"
    return {"date": days[target_index], "symbol": symbol, "targetIndex": target_index, "version": VERSION,
            "candidateId": CANDIDATE_ID, "signalGate": state, "newEntryAllowed": state == "true",
            "marketGate": market_gate["marketGate"], "roleHistory": history,
            "failedPredicates": failed, "unknownReasons": sorted(unknown), "certified": False, "targetAchieved": False}
