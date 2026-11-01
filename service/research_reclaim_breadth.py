"""Pure admission masks on the frozen immediate reclaim opportunity stream.

Both axes are the existing T-1 matched-five-session breadth measurements. This
module neither scores stocks nor manages positions, and never refills Top5 gaps.
The account retains responsibility for later execution, exits and cutoff censoring.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math


VERSION = "research-reclaim-breadth-v1"
STRATEGY_ID = "strongUndercutReclaimV1"
MODES = ("knownContext", "breadthMajority", "breadthImproving", "breadthBoth")
STATES = ("majorityImproving", "majorityNotImproving", "notMajorityImproving",
          "notMajorityNotImproving", "unknown")
SIGNAL_FIELDS = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _day(value):
    _require(isinstance(value, str), "Date must be a canonical YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Date must be a canonical YYYY-MM-DD string") from None
    _require(parsed.isoformat() == value, "Date must be a canonical YYYY-MM-DD string")
    return value


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _known_axes(context, earlier):
    """Recheck, rather than trust, the supplied known quadrant label."""
    _require(earlier is not None, "Known context needs the exact T-6 calendar slot")
    paired = context.get("pairedCount")
    _require(type(paired) is int and paired > 0, "Known context needs a positive pairedCount")
    today = context.get("positiveBreadthToday")
    past = context.get("positiveBreadthEarlier")
    change = context.get("matchedBreadthChange5")
    _require(all(_finite(v) for v in (today, past, change)), "Known breadth evidence must be finite")
    _require(0 <= today <= 1 and 0 <= past <= 1 and -100 <= change <= 100,
             "Known breadth evidence outside its domain")
    _require(abs(change - 100 * (today - past)) <= 1e-9,
             "Breadth change differs from its same-paired-population endpoints")
    majority, improving = today > .5, change > 0
    expected = STATES[(0 if majority else 2) + (0 if improving else 1)]
    _require(context["state"] == expected, "Known context state differs from its breadth axes")
    return majority, improving


def filter_reclaim_breadth(signals, context_rows, *, calendar):
    """Return four masks, with one evidence record per original signal per mask.

    All context date identities are checked. Numeric values are read only for
    dates referenced by the supplied signals: future numeric observations cannot
    revise earlier admission. Unknown contexts are retained in all four evidence
    streams and admitted by none. Coverage ratios add no admission threshold.
    """
    _require(isinstance(calendar, (list, tuple)) and bool(calendar), "Source calendar is required")
    days = list(calendar)
    _require(all(_day(d) == d for d in days) and days == sorted(set(days)),
             "Source calendar must be unique and increasing")
    indexes = {day: i for i, day in enumerate(days)}
    _require(isinstance(context_rows, (list, tuple)), "Context rows must be a sequence")
    contexts = {}
    for row in context_rows:
        _require(isinstance(row, dict), "Context row must be a mapping")
        day = _day(row.get("formationDate"))
        _require(day in indexes and day not in contexts, "Duplicate or off-calendar context date")
        at = indexes[day]
        as_of = days[at - 1] if at else None
        earlier = days[at - 6] if at >= 6 else None
        _require("asOfDate" in row and row["asOfDate"] == as_of,
                 "Context asOfDate must be exact source-calendar T-1")
        _require("earlierDate" in row and row["earlierDate"] == earlier,
                 "Context earlierDate must be exact source-calendar T-6")
        contexts[day] = row

    _require(isinstance(signals, list), "Signals must be a complete list")
    ids, date_symbols, date_ranks = set(), set(), set()
    for signal in signals:
        _require(isinstance(signal, dict) and set(signal) == SIGNAL_FIELDS,
                 "Signals must retain exactly the original five fields")
        day = _day(signal["signalDate"])
        _require(day in indexes, "Signal date outside source calendar")
        symbol, rank = signal["symbol"], signal["originalRank"]
        _require(isinstance(symbol, str) and bool(symbol.strip()), "Signal symbol must be nonempty")
        _require(signal["strategyId"] == STRATEGY_ID and
                 signal["signalId"] == f"{STRATEGY_ID}:{day}:{symbol}",
                 "Expected frozen immediate reclaim signal identity")
        _require(type(rank) is int and 1 <= rank <= 5, "Original Top5 rank must be retained")
        _require(signal["signalId"] not in ids and (day, symbol) not in date_symbols
                 and (day, rank) not in date_ranks, "Duplicate signal identity, symbol or rank")
        ids.add(signal["signalId"])
        date_symbols.add((day, symbol))
        date_ranks.add((day, rank))

    schedules = {mode: [] for mode in MODES}
    evidence = {mode: [] for mode in MODES}
    counts = {mode: {status: 0 for status in ("included", "excluded", "unknown")} for mode in MODES}
    checked = {}
    for signal in signals:
        day = signal["signalDate"]
        at = indexes[day]
        as_of = days[at - 1] if at else None
        if day not in checked:
            context = contexts.get(day)
            if context is None:
                checked[day] = ("unknown", None, "missingContextRow")
            else:
                _require(context.get("state") in STATES, "Unrecognized context state")
                status = context.get("status")
                _require(status in ("known", "unknown"), "Unrecognized context status")
                _require((status == "unknown") == (context["state"] == "unknown"),
                         "Context status and state disagree")
                if status == "unknown":
                    reason = context.get("reason")
                    _require(isinstance(reason, str) and bool(reason.strip()), "Unknown context needs its reason")
                    checked[day] = ("unknown", None, reason)
                else:
                    majority, improving = _known_axes(context, days[at - 6] if at >= 6 else None)
                    checked[day] = (context["state"], (True, majority, improving, majority and improving), None)
        state, decisions, unknown_reason = checked[day]
        for index, mode in enumerate(MODES):
            status = "unknown" if decisions is None else "included" if decisions[index] else "excluded"
            reason = unknown_reason if decisions is None else mode if decisions[index] else "not" + mode[0].upper() + mode[1:]
            evidence[mode].append({"signalId": signal["signalId"], "symbol": signal["symbol"],
                "signalDate": day, "originalRank": signal["originalRank"], "formationDate": day,
                "asOfDate": as_of, "state": state, "status": status, "reason": reason})
            counts[mode][status] += 1
            if status == "included":
                schedules[mode].append(deepcopy(signal))
    return {"version": VERSION, "inputSignalCount": len(signals),
            "schedules": schedules, "evidence": evidence, "counts": counts}
