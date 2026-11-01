"""As-of price-breakout Top5 with a prior-session OBV-high admission mask.

Only finite positive OHLCVA and the OHLC envelope determine window validity.
No investor identity, trading-status reconstruction or account state is used.
"""
from array import array
from copy import deepcopy
from datetime import date
import math


VERSION = "volume-lead-breakout-v1"
STRATEGY_ID = "volumeLeadBreakoutV1"
POLICIES = ("priceBreakout", "volumeLead")
FORMATION_STATES = ("passed", "failed", "unknown")
COMMON_STATES = ("candidate", "notTriggered", "commonUnknown")
ADMISSION_STATES = ("included", "excluded", "unknown")
NUMERIC_FIELDS = ("open", "high", "low", "close", "volume", "amount")
REASONS = ("insufficientSourceHistory", "missingAlignedBar",
           "nonpositiveOrUnknownOpen", "nonpositiveOrUnknownHigh", "nonpositiveOrUnknownLow",
           "nonpositiveOrUnknownClose", "nonpositiveOrUnknownVolume", "nonpositiveOrUnknownAmount",
           "invalidOHLCEnvelope", "nonfinitePriorMeanAmount", "nonfiniteObvLevel",
           "currentBreakoutFalse", "previousBreakoutTrue", "previousUpFalse")
BASIS = {
    "protocol": "docs/volume-lead-breakout-v1.json",
    "commonWindow": "Exactly23 source-calendar slots T-22..T; missing slots are never compressed",
    "quality": "Every slot needs finite strictly positive OHLC, volume and amount, and a valid OHLC envelope",
    "qualityLimits": "Valid numbers do not establish historical suspension, corporate-action or investor identity; no extra status, basis, preclose, ST or listing-age filter",
    "priceSetup": "C_T > max(H_T-20..T-1); C_T-1 > max(H_T-21..T-2) is false; C_T-1 > C_T-2",
    "commonOrder": ["mean(amount_T-20..T-1) descending", "symbol ascending"],
    "commonTop5": "Freeze price-setup Top5 before OBV admission; no backfill or account-state selection",
    "obv": "OBV_T-22=0; each subsequent level adds sign(close change)*current volume through T-1; flat close adds0",
    "obvComparison": "Strict OBV_T-1 > max(OBV_T-21..T-2); baseline and T are not in the20 comparison levels",
    "arithmetic": "Mean amount uses math.fsum(values)/20; OBV levels use math.fsum((previousLevel,signedVolume)); nonfinite derived values are commonUnknown before ranking",
    "reasonCounts": "Nonexclusive reasons, each counted at most once per symbol-day; known false setup predicates are distinct from unknown evidence",
    "signalClock": "Signal at T close, including the final target session; inherited account attempts at the next source-session open",
    "scope": "Fixed-cohort historical development comparison; no parameter search, date gate or independent holdout claim",
}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return value


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _positive(value):
    return _finite(value) and value > 0


def _bar_reasons(bar):
    if bar is None:
        return ("missingAlignedBar",)
    reasons = ["nonpositiveOrUnknown" + field.title() for field in NUMERIC_FIELDS if not _positive(bar.get(field))]
    if all(_positive(bar.get(field)) for field in ("open", "high", "low", "close")):
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            reasons.append("invalidOHLCEnvelope")
    return tuple(reasons)


def _prepare(dataset, end):
    _require(isinstance(dataset, dict), "Dataset must be an object")
    supplied = dataset.get("calendar")
    _require(isinstance(supplied, (list, tuple)) and supplied, "Source calendar required")
    days = [_day(day) for day in supplied]
    _require(days == sorted(set(days)), "Calendar must be unique and increasing")
    calendar = [day for day in days if day <= end]
    calendar_set = set(calendar)
    securities = dataset.get("securities")
    _require(isinstance(securities, list) and securities, "Full source securities list required")
    result = {}
    for security in securities:
        _require(isinstance(security, dict), "Security must be an object")
        symbol = security.get("symbol")
        _require(isinstance(symbol, str) and symbol.strip() and symbol not in result, "Unique nonempty source symbols required")
        source_bars = security.get("bars")
        _require(isinstance(source_bars, (list, tuple)), "Security bars sequence required")
        by_day, previous = {}, None
        for bar in source_bars:
            _require(isinstance(bar, dict), "Dated bar objects required")
            day = _day(bar.get("date"))
            if day > end:
                # Ignore future numeric fields and defects, without changing any
                # earlier window. Historical identity defects are rejected below.
                continue
            _require(day in calendar_set and (previous is None or previous < day),
                     "Historical bar dates must be unique, increasing and on source calendar")
            previous, by_day[day] = day, bar
        bars = [by_day.get(day) for day in calendar]
        reasons = [_bar_reasons(bar) for bar in bars]
        invalid = array("I", [0])
        for reason in reasons:
            invalid.append(invalid[-1] + bool(reason))
        result[symbol] = {"bars": bars, "reasons": reasons, "invalidPrefix": invalid}
    return calendar, {symbol: result[symbol] for symbol in sorted(result)}


def _ordered_reasons(reasons):
    found = set(reasons)
    return [reason for reason in REASONS if reason in found]


def _evaluate(source, calendar, at):
    first = max(0, at - 22)
    if at < 22 or source["invalidPrefix"][at + 1] != source["invalidPrefix"][first]:
        invalid_slots = [{"date": calendar[i], "reasons": list(source["reasons"][i])}
                         for i in range(first, at + 1) if source["reasons"][i]]
        reasons = (["insufficientSourceHistory"] if at < 22 else []) + [r for slot in invalid_slots for r in slot["reasons"]]
        return "commonUnknown", _ordered_reasons(reasons), invalid_slots, None
    bars = source["bars"][at - 22:at + 1]
    current_high = max(bar["high"] for bar in bars[2:22])
    previous_high = max(bar["high"] for bar in bars[1:21])
    current, previous, before_previous = (bars[i]["close"] for i in (22, 21, 20))
    predicates = {"currentBreakout": current > current_high,
                  "previousNotBreakout": not previous > previous_high,
                  "previousUp": previous > before_previous}
    failures = [reason for predicate, reason in (("currentBreakout", "currentBreakoutFalse"),
                ("previousNotBreakout", "previousBreakoutTrue"), ("previousUp", "previousUpFalse")) if not predicates[predicate]]
    if failures:
        return "notTriggered", failures, [], None
    reasons, amount_mean = [], None
    try:
        amount_mean = math.fsum(bar["amount"] for bar in bars[2:22]) / 20
    except (OverflowError, ValueError):
        pass
    if not _positive(amount_mean):
        reasons.append("nonfinitePriorMeanAmount")
    levels, directions, signed = [0], [None], [0]
    for index in range(1, 22):
        direction = (bars[index]["close"] > bars[index - 1]["close"]) - (bars[index]["close"] < bars[index - 1]["close"])
        increment = direction * bars[index]["volume"]
        try:
            level = math.fsum((levels[-1], increment))
        except (OverflowError, ValueError):
            level = None
        if not _finite(level):
            reasons.append("nonfiniteObvLevel")
            break
        directions.append(direction); signed.append(increment); levels.append(level)
    if reasons:
        return "commonUnknown", reasons, [], None
    dates = calendar[at - 22:at + 1]
    prior_maximum = max(levels[1:21])
    passed = levels[21] > prior_maximum
    evidence = {"windowDates": dates, "priorAmountDates": dates[2:22], "priorMeanAmount": amount_mean,
        "currentClose": current, "previousClose": previous, "beforePreviousClose": before_previous,
        "currentPrior20High": current_high, "previousPrior20High": previous_high, "predicates": predicates,
        "obv": {"baselineDate": dates[0], "levelDates": dates[:22], "closeDirections": directions,
                "signedVolumes": signed, "levels": levels, "comparisonDates": dates[1:21],
                "previousLevel": levels[21], "prior20Maximum": prior_maximum, "passed": passed}}
    return "candidate", [], [], evidence


def _empty_counts():
    return {"currentCandidateCount": 0, "top5OpportunityCount": 0, "rankExcludedCount": 0,
        "selectedFormationCounts": {state: 0 for state in FORMATION_STATES},
        "policyCounts": {policy: {state: 0 for state in ADMISSION_STATES} for policy in POLICIES},
        "signals": {policy: 0 for policy in POLICIES}, "notScheduled": {policy: 0 for policy in POLICIES},
        "commonStatusCounts": {state: 0 for state in COMMON_STATES}, "commonReasonCounts": {reason: 0 for reason in REASONS}}


def _add_counts(total, current):
    for key, value in current.items():
        if isinstance(value, dict):
            _add_counts(total[key], value)
        else:
            total[key] += value


def build_volume_lead_breakout(dataset, *, start, end, progress=None):
    """Build two schedules from immutable source data; no I/O or accounts.

    All target sessions and all source symbols remain in the denominator.
    Only price candidates retain full computed evidence; unknown symbols retain
    their actual invalid slots. Other known-false symbol-days retain full reason
    counts and can be independently reconstructed from the bound raw dataset.
    """
    start, end = _day(start), _day(end)
    _require(start <= end, "Ordered research interval required")
    _require(progress is None or callable(progress), "progress must be callable or None")
    calendar, sources = _prepare(dataset, end)
    targets = [day for day in calendar if start <= day <= end]
    _require(targets, "At least one target source session required")
    positions = {day: index for index, day in enumerate(calendar)}
    daily_rows, opportunities = [], []
    counts, schedules = _empty_counts(), {policy: [] for policy in POLICIES}
    for day in targets:
        at = positions[day]
        daily_counts, candidates, unknowns = _empty_counts(), [], []
        for symbol, source in sources.items():
            status, reasons, invalid_slots, evidence = _evaluate(source, calendar, at)
            daily_counts["commonStatusCounts"][status] += 1
            for reason in reasons:
                daily_counts["commonReasonCounts"][reason] += 1
            if status == "commonUnknown":
                unknowns.append({"symbol": symbol, "reasons": reasons, "invalidSlots": invalid_slots})
            elif status == "candidate":
                candidates.append({"symbol": symbol, "formationStatus": "passed" if evidence["obv"]["passed"] else "failed",
                                   "evidence": evidence})
        candidates.sort(key=lambda item: (-item["evidence"]["priorMeanAmount"], item["symbol"]))
        for rank, candidate in enumerate(candidates, 1):
            candidate.update(originalRank=rank, selectedTop5=rank <= 5)
        daily_counts.update(currentCandidateCount=len(candidates), top5OpportunityCount=min(5, len(candidates)),
                            rankExcludedCount=max(0, len(candidates) - 5))
        selected_ids = []
        for item in candidates[:5]:
            symbol, status, rank = item["symbol"], item["formationStatus"], item["originalRank"]
            identity = f"{STRATEGY_ID}:{day}:{symbol}"
            daily_counts["selectedFormationCounts"][status] += 1
            admission, signals = {}, {}
            for policy in POLICIES:
                included = policy == "priceBreakout" or status == "passed"
                reason = "commonPriceBreakout" if policy == "priceBreakout" else (
                    "priorObvHigh" if included else "priorObvNotHigh")
                admission[policy] = {"status": "included" if included else "excluded", "reason": reason}
                signal = {"signalId": identity, "strategyId": STRATEGY_ID, "symbol": symbol,
                          "signalDate": day, "originalRank": rank} if included else None
                signals[policy] = signal
                daily_counts["policyCounts"][policy][admission[policy]["status"]] += 1
                daily_counts["signals" if included else "notScheduled"][policy] += 1
                if included:
                    schedules[policy].append(deepcopy(signal))
            opportunities.append({"signalId": identity, "formationId": identity, "strategyId": STRATEGY_ID,
                "symbol": symbol, "signalDate": day, "originalRank": rank, "formationDate": day,
                "peerFormationDate": calendar[at - 1], "formationStatus": status, "evidence": deepcopy(item["evidence"]),
                "policyAdmission": admission, "signals": signals})
            selected_ids.append(identity)
        daily_rows.append({"formationDate": day, "peerFormationDate": calendar[at - 1] if at else None,
            "currentCandidates": candidates, "commonRanking": [{key: item[key] for key in
                ("symbol", "originalRank", "selectedTop5", "formationStatus")} for item in candidates],
            "selectedSignalIds": selected_ids, "commonUnknowns": unknowns, "counts": daily_counts})
        _add_counts(counts, daily_counts)
        if progress is not None and (len(daily_rows) % 50 == 0 or day == targets[-1]):
            progress({"phase": VERSION, "date": day, "processedTargetDays": len(daily_rows),
                      "targetDayCount": len(targets), "opportunityCount": len(opportunities)})
    counts = {"sampleCount": len(sources), "targetDayCount": len(targets), **counts}
    metadata = {"version": VERSION, "strategyId": STRATEGY_ID, "policies": list(POLICIES), "calendar": calendar,
        "targetDates": targets, "symbols": list(sources), "start": start, "end": end,
        "counts": deepcopy(counts), "basis": deepcopy(BASIS), "certified": False, "targetAchieved": False}
    return {"metadata": metadata, "dailyRows": daily_rows, "opportunities": opportunities,
            "schedules": schedules, "counts": counts}
