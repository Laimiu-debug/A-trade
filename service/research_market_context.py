"""Four independent, fixed market-context masks for existing signal slots.

No source I/O, ranking, execution, backfilling or parameter search occurs here.
Market breadth and Q-cohort observations are from signal-day close. The pressure
mask uses the caller's independently calendar-bound previous market session;
this function checks that its as-of date matches that session and precedes T.
Unknown evidence never admits a signal. Returned signals preserve every input
field and their original order. Inputs and returned objects share no mutables.
"""

from copy import deepcopy
from datetime import date
import math


VERSION = "fixed-market-context-v1"
FILTER_IDS = ("breadthMajority", "breadthImproving", "strongCohort",
              "preBreakoutPressure")
STATUSES = ("included", "excluded", "unknown")


def _day(value):
    if not isinstance(value, str):
        raise ValueError("date must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must be an ISO calendar date") from None
    if parsed.isoformat() != value:
        raise ValueError("date must be an ISO calendar date")
    return value


def _count(value):
    return type(value) is int and value >= 0


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _safe(value):
    """Retain nonfinite supplied evidence as text, never a JSON numeric NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return deepcopy(value)


def _out(status, reason, inputs):
    return {"status": status, "reason": reason, "inputs": _safe(inputs)}


def _section(row, name):
    value = row.get(name)
    if value is not None and not isinstance(value, dict):
        raise ValueError(f"{name} must be an object or null")
    return value or {}


def _majority(row):
    source = _section(row, "price20")
    positive, total = source.get("positiveCount"), source.get("evaluableCount")
    inputs = {"positiveCount": positive, "evaluableCount": total}
    if not all(_count(value) for value in (positive, total)):
        return _out("unknown", "breadthCountsUnavailable", inputs)
    if positive > total:
        raise ValueError("positiveCount exceeds evaluableCount")
    if total == 0:
        return _out("unknown", "emptyBreadthPopulation", inputs)
    inputs["positiveReturn20Breadth"] = positive / total
    passed = 2 * positive > total
    return _out("included" if passed else "excluded",
                "breadthAboveHalf" if passed else "breadthNotAboveHalf", inputs)


def _improving(row):
    source = _section(row, "matched5")
    inputs = {key: source.get(key) for key in
              ("available", "earlierDate", "pairedCount", "matchedBreadthChange5")}
    earlier = inputs["earlierDate"]
    if earlier is not None and _day(earlier) >= row["date"]:
        raise ValueError("matched5 earlierDate must precede signal day")
    if inputs["available"] is not True:
        return _out("unknown", "matchedPopulationUnavailable", inputs)
    size, change = inputs["pairedCount"], inputs["matchedBreadthChange5"]
    if not _count(size):
        return _out("unknown", "matchedCountUnavailable", inputs)
    if size == 0:
        return _out("unknown", "emptyMatchedPopulation", inputs)
    if not _finite(change) or not -100 <= change <= 100:
        return _out("unknown", "matchedBreadthChangeUnavailable", inputs)
    passed = change > 0
    return _out("included" if passed else "excluded",
                "matchedBreadthImproving" if passed else "matchedBreadthNotImproving", inputs)


def _cohort(row):
    source = _section(row, "yesterdayCohort")
    inputs = {key: source.get(key) for key in
              ("previousCohortAvailable", "previousDate", "eligibleCount",
               "continuedCount", "observedFailureCount", "unknownCount")}
    previous = inputs["previousDate"]
    if previous is not None and _day(previous) >= row["date"]:
        raise ValueError("yesterdayCohort previousDate must precede signal day")
    if inputs["previousCohortAvailable"] is not True:
        return _out("unknown", "previousCohortUnavailable", inputs)
    total, kept, failed, unknown = (inputs[key] for key in
                                    ("eligibleCount", "continuedCount",
                                     "observedFailureCount", "unknownCount"))
    if not all(_count(value) for value in (total, kept, failed, unknown)):
        return _out("unknown", "cohortCountsUnavailable", inputs)
    if kept + failed + unknown != total:
        raise ValueError("yesterdayCohort counts do not sum to eligibleCount")
    if total == 0:
        return _out("excluded", "emptyPreviousCohort", inputs)
    if unknown:
        return _out("unknown", "cohortMemberStateUnknown", inputs)
    passed = kept > failed
    return _out("included" if passed else "excluded",
                "cohortContinuationMajority" if passed else "cohortContinuationNotMajority", inputs)


def _pressure(signal, source):
    if source is not None and not isinstance(source, dict):
        raise ValueError("prior_pressure entries must be objects or null")
    source = source or {}
    inputs = {key: source.get(key) for key in
              ("asOfDate", "previousCalendarDate", "pressure5", "pressure20")}
    as_of, expected = inputs["asOfDate"], inputs["previousCalendarDate"]
    for value in (as_of, expected):
        if value is not None and _day(value) >= signal["signalDate"]:
            raise ValueError("prior pressure dates must precede signalDate")
    if as_of is not None and expected is not None and as_of != expected:
        raise ValueError("prior pressure asOfDate differs from previousCalendarDate")
    if as_of is None or expected is None:
        return _out("unknown", "priorPressureDateUnavailable", inputs)
    short, long = inputs["pressure5"], inputs["pressure20"]
    if not all(_finite(value) for value in (short, long)):
        return _out("unknown", "priorPressureUnavailable", inputs)
    passed = long > 0 and short > long
    return _out("included" if passed else "excluded",
                "priorPressurePositiveAndStrengthening" if passed else "priorPressureConditionFalse", inputs)


def filter_market_context(signals, daily_rows, *, prior_pressure=None):
    """Apply four separate masks, preserving the original complete signal slots.

    ``daily_rows`` is a list of the already source-bound market daily records;
    this function does not certify their source, sample, calendar or formula.
    ``prior_pressure`` maps signalId to asOfDate/previousCalendarDate and two
    pressure values. Only the caller can prove the expected date is exchange
    T-1 and the pressure window contains 40 aligned slots ending on that date.

    Missing or invalid required numeric evidence produces unknown. Duplicated
    identities, malformed structures, noncausal dates and internally
    contradictory known counts raise ValueError. Appended future observations
    are not used to evaluate previous signals. Global Q-unknown counts do not
    substitute for unknownCount within yesterday's fixed Q-true cohort.
    """
    if not isinstance(signals, list) or not isinstance(daily_rows, list):
        raise ValueError("signals and daily_rows must be lists")
    if prior_pressure is not None and not isinstance(prior_pressure, dict):
        raise ValueError("prior_pressure must be a mapping or None")
    prior_pressure = prior_pressure or {}
    by_day = {}
    for row in daily_rows:
        if not isinstance(row, dict):
            raise ValueError("daily rows must be objects")
        day = _day(row.get("date"))
        if day in by_day:
            raise ValueError("daily dates must be unique")
        by_day[day] = row

    schedules = {key: [] for key in FILTER_IDS}
    evidence = {key: [] for key in FILTER_IDS}
    counts = {key: {status: 0 for status in STATUSES} for key in FILTER_IDS}
    seen, cache = set(), {}
    for signal in signals:
        if not isinstance(signal, dict):
            raise ValueError("signals must be objects")
        signal_id = signal.get("signalId")
        if not isinstance(signal_id, str) or not signal_id.strip() or signal_id in seen:
            raise ValueError("signalId must be a unique nonempty string")
        seen.add(signal_id)
        day = _day(signal.get("signalDate"))
        if day not in cache:
            row = by_day.get(day)
            if row is None:
                cache[day] = {key: _out("unknown", "marketDailyUnavailable", {})
                              for key in FILTER_IDS[:3]}
            else:
                cache[day] = dict(zip(FILTER_IDS[:3],
                                      (_majority(row), _improving(row), _cohort(row))))
        decisions = {**cache[day], FILTER_IDS[3]: _pressure(signal, prior_pressure.get(signal_id))}
        identity = {"signalId": signal_id, "signalDate": day}
        for key in ("symbol", "rank", "originalRank"):
            if key in signal:
                identity[key] = deepcopy(signal[key])
        for key in FILTER_IDS:
            decision = deepcopy(decisions[key])
            evidence[key].append({**deepcopy(identity), **decision})
            counts[key][decision["status"]] += 1
            if decision["status"] == "included":
                schedules[key].append(deepcopy(signal))
    return {"version": VERSION, "inputSignalCount": len(signals),
            "schedules": schedules, "evidence": evidence, "counts": counts}
