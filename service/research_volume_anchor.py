"""Pure causal price/volume anchors and cumulative traded-average acceptance.

Implements docs/signal-volume-anchor-acceptance-v1.json. An original Top5 is
fixed at A, before any A+1..A+3 evidence. Both delayed policies require the same
complete evidence. No investor identity, future outcome or account is used.
"""
from __future__ import annotations

from array import array
from collections import Counter
from datetime import date
import math


VERSION = "volume-anchor-acceptance-v1"
SID = "volumeAnchorAcceptanceV1"
MODES = ("immediate", "delay3", "accept3")
PRICE_BASIS = "raw-unadjusted"
PRIOR_SLOTS, DELAY_SLOTS, TOP_N = 20, 3, 5
CENT_TOLERANCE = .01


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _positive(value):
    return _finite(value) and value > 0


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return value


def _halted(value):
    return value is False or type(value) is int and value == 0 or value == "0"


def _bar_reasons(bar, source, status):
    """Keep simultaneous observed defects, in this stable evidence order.

Source units must be explicit. An optional conflicting per-bar declaration is
also invalid. Unknown historical status does not become a suspension, and an
absent per-bar price basis inherits the required raw dataset declaration.
"""
    if bar is None:
        return ("missingAlignedRawBar",)
    reasons = []
    if bar.get("priceBasisId", PRICE_BASIS) != PRICE_BASIS:
        reasons.append("rawPriceBasisUnknown")
    if "knownSuspended" in bar and type(bar["knownSuspended"]) is not bool:
        reasons.append("invalidKnownSuspensionFlag")
    if _halted(status) or bar.get("knownSuspended") is True:
        reasons.append("suspensionContradictsPositiveVolume" if _positive(bar.get("volume")) else "knownSuspension")
    volume_unit = source.get("volumeUnit") == "shares" and bar.get("volumeUnit", "shares") == "shares"
    amount_unit = source.get("amountUnit") == "CNY" and bar.get("amountUnit", "CNY") == "CNY"
    if not volume_unit:
        reasons.append("volumeUnitNotShares")
    if not amount_unit:
        reasons.append("amountUnitNotCNY")
    prices_positive = all(_positive(bar.get(k)) for k in ("open", "high", "low", "close"))
    if not prices_positive or not _positive(bar.get("volume")):
        reasons.append("nonpositiveOrUnknownRawOHLCV")
    envelope = prices_positive and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]
    if prices_positive and not envelope:
        reasons.append("invalidRawOHLCEnvelope")
    if not _positive(bar.get("amount")):
        reasons.append("nonpositiveOrUnknownAmount")
    if volume_unit and amount_unit and envelope and _positive(bar.get("amount")) and _positive(bar.get("volume")):
        average = bar["amount"] / bar["volume"]
        if not _finite(average):
            reasons.append("nonfiniteTradedAverage")
        elif not bar["low"] - CENT_TOLERANCE <= average <= bar["high"] + CENT_TOLERANCE:
            reasons.append("tradedAverageOutsideOHLC")
    return tuple(reasons)


def _prepare_source(source, calendar, calendar_set, end):
    if not isinstance(source.get("bars"), (list, tuple)):
        raise ValueError("Each security must supply a bars sequence")
    rows, previous = {}, None
    for bar in source["bars"]:
        if not isinstance(bar, dict):
            raise ValueError("Raw bars must be mappings with dated identities")
        day = _day(bar.get("date"))
        if day > end:
            # Future numbers and their defects cannot affect earlier outputs.
            continue
        if day not in calendar_set or previous is not None and day <= previous:
            raise ValueError("Historical bar dates must be unique, increasing and on calendar")
        rows[day], previous = bar, day
    statuses = source.get("tradeStatusByDate", {})
    if not isinstance(statuses, dict):
        raise ValueError("tradeStatusByDate must be a mapping")
    for day in statuses:
        if _day(day) <= end and day not in calendar_set:
            raise ValueError("Historical status dates must be on calendar")
    bars = [rows.get(day) for day in calendar]
    reasons = [_bar_reasons(bar, source, statuses.get(day)) for day, bar in zip(calendar, bars)]
    invalid = array("I", [0])
    for reason in reasons:
        invalid.append(invalid[-1] + bool(reason))
    return {"bars": bars, "reasons": reasons, "invalidPrefix": invalid}


def _complete(source, first, stop):
    return first >= 0 and source["invalidPrefix"][stop] == source["invalidPrefix"][first]


def _signal(formation_id, symbol, day, rank):
    return {"signalId": formation_id, "strategyId": SID, "symbol": symbol,
            "signalDate": day, "originalRank": rank}


def _evidence_number(bar, field):
    value = bar.get(field) if isinstance(bar, dict) else None
    return value if _finite(value) else None


def _acceptance(source, calendar, index):
    """Evaluate each completed close using its own A..d prefix, never later bars."""
    anchor = source["bars"][index]
    amounts, volumes = [anchor["amount"]], [anchor["volume"]]
    complete_prefix, steps = True, []
    indices = range(index + 1, min(index + DELAY_SLOTS + 1, len(calendar)))
    for offset, at in enumerate(indices, 1):
        day, bar = calendar[at], source["bars"][at]
        bar_reasons = list(source["reasons"][at])
        reasons = list(bar_reasons)
        accepted = cumulative_amount = cumulative_shares = average = comparison = None
        if bar_reasons:
            complete_prefix = False
        elif not complete_prefix:
            reasons.append("incompleteCumulativeEvidence")
        else:
            amounts.append(bar["amount"])
            volumes.append(bar["volume"])
            try:
                cumulative_amount, cumulative_shares = math.fsum(amounts), math.fsum(volumes)
            except (OverflowError, ValueError):
                cumulative_amount = cumulative_shares = None
            if not _positive(cumulative_amount) or not _positive(cumulative_shares):
                cumulative_amount = cumulative_shares = None
                reasons.append("nonfiniteCumulativeTotals")
                complete_prefix = False
            else:
                average = cumulative_amount / cumulative_shares
                comparison = bar["close"] * cumulative_shares
                if not _finite(average) or not _finite(comparison):
                    average = average if _finite(average) else None
                    comparison = comparison if _finite(comparison) else None
                    reasons.append("nonfiniteAcceptanceComparison")
                else:
                    accepted = comparison >= cumulative_amount
                    if not accepted:
                        reasons.append("closeBelowAnchoredAverage")
        steps.append({"date": day, "offset": offset, "barStatus": "unknown" if bar_reasons else "valid",
            "barReasons": bar_reasons, "close": _evidence_number(bar, "close"),
            "amount": _evidence_number(bar, "amount"), "volume": _evidence_number(bar, "volume"),
            "cumulativeAmount": cumulative_amount, "cumulativeShares": cumulative_shares,
            "q": average, "closeTimesCumulativeShares": comparison, "accepted": accepted, "reasons": reasons})
    available = len(steps)
    censored = available < DELAY_SLOTS
    unknown = any(step["accepted"] is None for step in steps)
    failed = any(step["accepted"] is False for step in steps)
    status = "censored" if censored else "unknown" if unknown else "failed" if failed else "passed"
    reasons = ["decisionOutsideTarget"] if censored else []
    reasons.extend(step["date"] + ":" + reason for step in steps for reason in step["reasons"])
    return {"status": status, "scheduledDate": None if censored else calendar[index + DELAY_SLOTS],
        "dates": [step["date"] for step in steps], "requiredSessions": DELAY_SLOTS,
        "availableSessions": available, "complete": not censored and not unknown,
        "knownPassCount": sum(step["accepted"] is True for step in steps),
        "knownFailCount": sum(step["accepted"] is False for step in steps),
        "unknownStepCount": sum(step["accepted"] is None for step in steps), "steps": steps, "reasons": reasons}


def build_volume_anchor(dataset, start, end, progress=None):
    """Build all anchors and three five-field signal schedules without I/O/mutation.

All source securities remain in the sample. Historical missing/invalid slots
stay unknown; valid-price false patterns stay false. Future dated numeric bars
are ignored. A later end can resolve censoring, never revise an earlier anchor
or the cumulative evidence of an already completed acceptance step.
"""
    start, end = _day(start), _day(end)
    if start > end:
        raise ValueError("start must not be after end")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    if not isinstance(dataset, dict) or dataset.get("priceBasisId") != PRICE_BASIS:
        raise ValueError("An explicitly raw-unadjusted dataset is required")
    supplied = dataset.get("calendar")
    if not isinstance(supplied, (list, tuple)) or not supplied:
        raise ValueError("A nonempty source calendar is required")
    days = [_day(day) for day in supplied]
    if any(a >= b for a, b in zip(days, days[1:])):
        raise ValueError("Source calendar must be unique and increasing")
    calendar = [day for day in days if day <= end]
    target_dates = [day for day in calendar if day >= start]
    if not target_dates:
        raise ValueError("No source-calendar sessions in target")
    calendar_set, target_set = set(calendar), set(target_dates)
    securities = dataset.get("securities")
    if not isinstance(securities, (list, tuple)) or not securities:
        raise ValueError("A nonempty fixed securities sample is required")
    originals = {}
    for security in securities:
        symbol = security.get("symbol") if isinstance(security, dict) else None
        if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip() or symbol in originals:
            raise ValueError("Sample symbols must be nonempty, unique and without outer whitespace")
        originals[symbol] = security
    symbols, sources = sorted(originals), {}
    for ordinal, symbol in enumerate(symbols, 1):
        sources[symbol] = _prepare_source(originals[symbol], calendar, calendar_set, end)
        if progress is not None and (ordinal % 100 == 0 or ordinal == len(symbols)):
            progress({"stage": "barValidity", "completedSymbols": ordinal, "totalSymbols": len(symbols)})
    daily_rows, opportunities = [], []
    schedules = {mode: [] for mode in MODES}
    acceptance_counts = Counter({key: 0 for key in ("passed", "failed", "unknown", "censored")})
    for index, day in enumerate(calendar):
        if day not in target_set:
            continue
        first = index - PRIOR_SLOTS
        prior_dates = calendar[max(0, first):index]
        counts = {key: 0 for key in ("priorWindowUnknownCount", "formationBarUnknownCount", "anchorUnknownCount",
                                    "anchorKnownFalseCount", "setupCount", "selectedCount")}
        counts["sampleCount"] = len(symbols)
        candidates, unknown_reasons = [], Counter()
        for symbol in symbols:
            source = sources[symbol]
            prior_complete = _complete(source, first, index)
            formation_complete = not source["reasons"][index]
            if not prior_complete or not formation_complete:
                counts["priorWindowUnknownCount"] += not prior_complete
                counts["formationBarUnknownCount"] += not formation_complete
                counts["anchorUnknownCount"] += 1
                reasons = {reason for slot in source["reasons"][max(0, first):index + 1] for reason in slot}
                if first < 0:
                    reasons.add("insufficientPriorCalendarHistory")
                unknown_reasons.update(sorted(reasons))
                continue
            prior = source["bars"][first:index]
            bar = source["bars"][index]
            high, volume_high = max(b["high"] for b in prior), max(b["volume"] for b in prior)
            if not (bar["close"] > high and bar["volume"] > volume_high):
                counts["anchorKnownFalseCount"] += 1
                continue
            try:
                prior_amount = math.fsum(b["amount"] for b in prior) / PRIOR_SLOTS
            except (OverflowError, ValueError):
                prior_amount = None
            if not _positive(prior_amount):
                counts["anchorUnknownCount"] += 1
                unknown_reasons["nonfinitePriorMeanAmount"] += 1
                continue
            candidates.append({"symbol": symbol, "priorAmount20": prior_amount, "prior20High": high,
                "prior20VolumeHigh": volume_high, "formationClose": bar["close"],
                "formationVolume": bar["volume"], "formationAmount": bar["amount"]})
        candidates.sort(key=lambda row: (-row["priorAmount20"], row["symbol"]))
        selected_ids = []
        for rank, candidate in enumerate(candidates, 1):
            candidate.update(originalRank=rank, selected=rank <= TOP_N)
            if rank > TOP_N:
                continue
            symbol = candidate["symbol"]
            formation_id = f"{SID}:{day}:{symbol}"
            selected_ids.append(formation_id)
            acceptance = _acceptance(sources[symbol], calendar, index)
            acceptance_counts[acceptance["status"]] += 1
            decisions = {"immediate": day, "delay3": acceptance["scheduledDate"], "accept3": acceptance["scheduledDate"]}
            signals = {mode: None for mode in MODES}
            for mode in MODES:
                admitted = mode == "immediate" or (mode == "delay3" and acceptance["complete"]) or (mode == "accept3" and acceptance["status"] == "passed")
                if admitted:
                    signals[mode] = _signal(formation_id, symbol, decisions[mode], rank)
                    schedules[mode].append(dict(signals[mode]))
            opportunity = {key: value for key, value in candidate.items() if key != "selected"}
            opportunity.update(formationId=formation_id, formationDate=day, priorDate=calendar[index - 1],
                decisionDates=decisions, acceptance=acceptance, signals=signals)
            opportunities.append(opportunity)
        counts.update(setupCount=len(candidates), selectedCount=len(selected_ids))
        daily_rows.append({"formationDate": day, "priorDate": calendar[index - 1] if index else None,
            "priorWindowDates": prior_dates, "counts": counts, "unknownReasonCounts": dict(sorted(unknown_reasons.items())),
            "candidates": candidates, "selectedFormationIds": selected_ids})
        if progress is not None and (len(daily_rows) % 20 == 0 or day == target_dates[-1]):
            progress({"stage": "formation", "completedDays": len(daily_rows), "totalDays": len(target_dates),
                "formationDate": day, "opportunityCount": len(opportunities)})
    for values in schedules.values():
        values.sort(key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"], row["signalId"]))
    counts = {"sampleCount": len(symbols), "targetDayCount": len(target_dates),
        "setupCount": sum(row["counts"]["setupCount"] for row in daily_rows), "opportunityCount": len(opportunities),
        "daysWithSetups": sum(bool(row["candidates"]) for row in daily_rows),
        "daysWithSelected": sum(bool(row["selectedFormationIds"]) for row in daily_rows),
        "acceptance": dict(acceptance_counts), "signals": {mode: len(schedules[mode]) for mode in MODES}}
    return {"version": VERSION, "strategyId": SID, "modes": list(MODES), "calendar": calendar,
        "targetDates": target_dates, "dailyRows": daily_rows, "opportunities": opportunities,
        "schedules": schedules, "counts": counts,
        "basis": {"priceBasisId": PRICE_BASIS, "volumeUnit": "shares", "amountUnit": "CNY",
            "priorSlots": PRIOR_SLOTS, "delaySlots": DELAY_SLOTS, "topN": TOP_N,
            "tradedAverageEnvelopeTolerance": CENT_TOLERANCE,
            "anchor": "close_A > prior20 max high AND volume_A > prior20 max volume; no relative-strength screen",
            "ranking": "prior20 mean explicit amount descending, then symbol ascending; original Top5 fixed at A",
            "acceptance": "at every A+1..A+3 close: close_d * fsum(shares_A..d) >= fsum(amount_A..d)",
            "unknownPriority": "censored > unknown > failed > passed; observed partial failures and unknowns retained",
            "unknownReasonCountUnit": "each symbol/reason at most once per formation day; prior/current incomplete counts can overlap",
            "eligibility": "unknown historical status retained, not a certified trading-history claim",
            "meaning": "observed event-anchored traded average; not holder cost or investor identity",
            "future": "numeric bars after registered end ignored; no after-end evidence in windows or schedules"},
        "certified": False, "targetAchieved": False}
