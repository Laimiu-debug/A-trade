"""One fixed, T-close volatility ceiling on saved original reclaim admissions.

The inclusive21-slot feature uses20 TR/reference terms ending on signal T.
It is a historical range statistic, not a maximum-loss guarantee or Wilder ATR.
Original IDs/ranks, market conditions, orders and exits are unchanged.
"""
from copy import deepcopy
import math

from .research_liquid_trend_breakout import day, positive

VERSION = "reclaim-entry-volatility-cap-v1"
SID = "strongUndercutReclaimV1"
CEILING = .05
BASE_POLICIES = ("liquid", "liquidTrend")
POLICY_MAP = {"liquidNtr5": "liquid", "liquidTrendNtr5": "liquidTrend"}
SIGNAL_KEYS = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}


def bar_issue(bar, expected_day):
    if not isinstance(bar, dict):
        return "missingAlignedBar"
    if bar.get("date") != expected_day:
        return "barDateMismatch"
    if bar.get("priceBasisId", "raw-unadjusted") != "raw-unadjusted":
        return "conflictingPriceBasis"
    if any(not positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume")):
        return "invalidOHLCV"
    if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
        return "invalidOHLCEnvelope"
    return None


def feature_at_close(calendar, index, bars_by_date, preclose_by_date):
    if type(index) is not int or not 0 <= index < len(calendar):
        raise ValueError("An existing signal-close calendar index is required")
    required = calendar[max(0, index - 20):index + 1]
    if any(day(d) != d for d in required) or required != sorted(set(required)):
        raise ValueError("Ordered unique exact source slots required")
    if not isinstance(bars_by_date, dict) or not isinstance(preclose_by_date, dict):
        raise ValueError("Explicit raw-bar and reference mappings required")
    observations = [bars_by_date.get(d) for d in required]
    issues = [{"date": d, "reason": reason} for d, b in zip(required, observations) if (reason := bar_issue(b, d))]
    if len(required) != 21:
        issues.insert(0, {"date": calendar[index], "reason": "insufficient21CalendarSlots"})
    terms = []
    for i in range(1, len(required)):
        d, bar, previous = required[i], observations[i], observations[i - 1]
        explicit = d in preclose_by_date
        reference = preclose_by_date[d] if explicit else previous.get("close") if isinstance(previous, dict) else None
        reason = bar_issue(bar, d)
        if not positive(reference):
            reference_reason = "invalidExplicitReference" if explicit else "invalidPreviousCalendarClose"
            reason = reason or reference_reason
            issues.append({"date": d, "reason": reference_reason})
        tr = normalized = None
        if reason is None:
            tr = max(bar["high"] - bar["low"], abs(bar["high"] - reference), abs(bar["low"] - reference))
            normalized = tr / reference
            if not math.isfinite(tr) or not math.isfinite(normalized):
                tr = normalized = None
                reason = "nonFiniteTrueRange"
                issues.append({"date": d, "reason": reason})
        terms.append({"date": d, "referenceSource": "precloseByDate" if explicit else "previousCalendarClose",
            "referenceValue": reference if positive(reference) else None, "trueRange": tr, "trPct": normalized,
            "status": "unknown" if reason else "known"})
    mean = None
    if not issues:
        try:
            mean = math.fsum(row["trPct"] for row in terms) / 20
        except OverflowError:
            pass
        if mean is None or not math.isfinite(mean):
            mean = None
            issues.append({"date": calendar[index], "reason": "nonFiniteMean"})
    known = mean is not None and not issues
    return {"asOfDate": calendar[index], "windowDates": required, "trTerms": terms,
        "meanTRPct20": mean, "ceiling": CEILING, "windowStatus": "known" if known else "unknown",
        "admissionStatus": "unknown" if not known else "passed" if mean <= CEILING else "failed", "issues": issues}


def validate_schedule(rows, calendar_set, start, end):
    if not isinstance(rows, list):
        raise ValueError("A frozen list of original signals is required")
    by_id, ranks, previous = {}, set(), None
    for row in rows:
        if not isinstance(row, dict) or set(row) != SIGNAL_KEYS or row["strategyId"] != SID:
            raise ValueError("Exact original immediate five-field schema required")
        d = day(row["signalDate"])
        rank, identity = row["originalRank"], row["signalId"]
        if (not isinstance(identity, str) or not identity or identity in by_id or type(rank) is not int or not 1 <= rank <= 5 or
                not isinstance(row["symbol"], str) or not row["symbol"] or d not in calendar_set or not start <= d <= end or (d, rank) in ranks):
            raise ValueError("Original signal identity/date/rank invalid")
        ordering = (d, rank, row["symbol"])
        if previous is not None and ordering <= previous:
            raise ValueError("Frozen chronological original rank order required")
        by_id[identity], previous = row, ordering
        ranks.add((d, rank))
    return by_id


def build_admissions(dataset, original, bases, *, start, end):
    start, end = day(start), day(end)
    if (dataset.get("priceBasisId") != "raw-unadjusted" or not isinstance(dataset.get("calendar"), list) or
            not dataset.get("calendarSource") or dataset["calendarSource"].startswith("observed")):
        raise ValueError("Explicit raw-unadjusted source and independent calendar required")
    calendar = dataset["calendar"]
    if (not calendar or calendar != sorted(set(calendar)) or any(day(d) > end for d in calendar) or
            start not in calendar or end not in calendar or start > end):
        raise ValueError("Exact ordered target calendar/endpoints required")
    if not isinstance(bases, dict) or set(bases) != set(BASE_POLICIES):
        raise ValueError("Both unchanged parent market admissions required")
    originals = validate_schedule(original, set(calendar), start, end)
    for rows in bases.values():
        validate_schedule(rows, set(calendar), start, end)
        if any(originals.get(row["signalId"]) != row for row in rows):
            raise ValueError("Parent admission must preserve exact original signals/ranks")
    liquid_ids = {r["signalId"] for r in bases["liquid"]}
    if any(r["signalId"] not in liquid_ids for r in bases["liquidTrend"]):
        raise ValueError("Parent market-alignment subset changed")
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities:
        raise ValueError("Full unchanged source cohort required")
    source = {s["symbol"]: s for s in securities}
    if len(source) != len(securities) or any(r["symbol"] not in source for r in original):
        raise ValueError("Unique full source membership and original symbols required")
    indices = {d: i for i, d in enumerate(calendar)}
    lookups = {}
    for symbol in {r["symbol"] for r in original}:
        mapping, previous = {}, None
        for bar in source[symbol]["bars"]:
            d = day(bar["date"])
            if d not in indices or (previous is not None and d <= previous):
                raise ValueError("Duplicate, unordered or off-calendar raw bar")
            mapping[d], previous = bar, d
        lookups[symbol] = mapping
    features, by_id = [], {}
    for signal in original:
        symbol, d = signal["symbol"], signal["signalDate"]
        row = {**deepcopy(signal), **feature_at_close(calendar, indices[d], lookups[symbol], source[symbol].get("precloseByDate", {}))}
        features.append(row)
        by_id[signal["signalId"]] = row
    schedules = {policy: [deepcopy(r) for r in bases[parent] if by_id[r["signalId"]]["admissionStatus"] == "passed"]
                 for policy, parent in POLICY_MAP.items()}
    return {"version": VERSION, "features": features, "schedules": schedules, "ceiling": CEILING,
        "counts": {"fixedPopulation": len(securities), "originalOpportunities": len(original),
            "knownFeatures": sum(r["windowStatus"] == "known" for r in features),
            "capPassed": sum(r["admissionStatus"] == "passed" for r in features),
            "unknownFeatures": sum(r["windowStatus"] == "unknown" for r in features),
            "signals": {p: len(v) for p, v in schedules.items()}},
        "entryClock": "completedTclose;noTplus1bar", "reranked": False, "targetAchieved": False}
