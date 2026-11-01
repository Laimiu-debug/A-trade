"""Causal prior-strength support reclaim with three frozen decision clocks.

This pure builder consumes an explicitly raw, full-sample account-data view.
It neither certifies historical tradability nor evaluates subsequent returns.
Price validity is counted once per source-calendar slot; missing rows never
shorten a lookback. All ranking inputs precede formation T. Only confirmation
uses T+1/T+2, at its own later decision date, without replacing original Top5.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import math


VERSION = "strong-undercut-reclaim-timing-v1"
SID = "strongUndercutReclaimV1"
MODES = ("immediate", "delay2", "confirm2")
PRICE_BASIS = "raw-unadjusted"
PRIOR_SLOTS, AMOUNT_SLOTS, DELAY_SLOTS, TOP_N = 61, 20, 2, 5
RANK_THRESHOLD = .8


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


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _halted(value):
    # Unknown daily status is a limitation, not evidence of suspension.
    return value is False or type(value) is int and value == 0 or value == "0"


def _price_reason(bar, day, status):
    """Same OHLCV/envelope/raw/no-known-halt quality as prior raw windows.

    The caller has explicitly declared the dataset raw. A missing per-bar basis
    inherits that declaration; an explicit conflicting bar basis is invalid.
    """
    if bar is None:
        return "missingAlignedRawBar"
    if not isinstance(bar, dict) or bar.get("date") != day:
        return "rawHistoryDateMismatch"
    if bar.get("priceBasisId", PRICE_BASIS) != PRICE_BASIS:
        return "rawPriceBasisUnknown"
    if "knownSuspended" in bar and type(bar["knownSuspended"]) is not bool:
        return "invalidKnownSuspensionFlag"
    if _halted(status) or bar.get("knownSuspended") is True:
        return ("suspensionContradictsPositiveVolume" if _positive(bar.get("volume"))
                else "knownSuspension")
    if not all(_positive(bar.get(key)) for key in ("open", "high", "low", "close", "volume")):
        return "nonpositiveOrUnknownRawOHLCV"
    if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
        return "invalidRawOHLCEnvelope"
    return None


def _prepare_source(source, calendar, calendar_set, end):
    if not isinstance(source, dict) or not isinstance(source.get("bars"), (list, tuple)):
        raise ValueError("Each source must supply a bars sequence")
    rows = {}
    previous = None
    for bar in source["bars"]:
        if not isinstance(bar, dict):
            raise ValueError("Raw bars must be mappings with source-calendar dates")
        day = _day(bar.get("date"))
        if day > end or day not in calendar_set:
            raise ValueError("Future or off-calendar raw evidence is not accepted")
        if previous is not None and day <= previous:
            raise ValueError("Raw bar dates must be unique and increasing")
        rows[day], previous = bar, day
    statuses = source.get("tradeStatusByDate", {})
    if not isinstance(statuses, dict):
        raise ValueError("tradeStatusByDate must be a mapping")
    # Status-only records cannot introduce unseen sessions or future evidence.
    for day in statuses:
        if _day(day) > end or day not in calendar_set:
            raise ValueError("Future or off-calendar status evidence is not accepted")
    aligned = [rows.get(day) for day in calendar]
    reasons = [_price_reason(bar, day, statuses.get(day)) for day, bar in zip(calendar, aligned)]
    invalid_prefix = [0]
    for reason in reasons:
        invalid_prefix.append(invalid_prefix[-1] + (reason is not None))
    return {"bars": aligned, "reasons": reasons, "invalidPrefix": invalid_prefix}


def _complete(source, first, stop):
    return first >= 0 and source["invalidPrefix"][stop] == source["invalidPrefix"][first]


def _signal(formation_id, symbol, day, rank):
    return {"signalId": formation_id, "strategyId": SID, "symbol": symbol,
            "signalDate": day, "originalRank": rank}


def _read_close(bar):
    return bar["close"] if isinstance(bar, dict) and _positive(bar.get("close")) else None


def _confirmation(source, calendar, index, support, target_set):
    indices = [i for i in range(index + 1, min(index + DELAY_SLOTS + 1, len(calendar)))
               if calendar[i] in target_set]
    days = [calendar[i] for i in indices]
    closes = [_read_close(source["bars"][i]) for i in indices]
    if len(indices) != DELAY_SLOTS:
        return {"status": "censored", "dates": days, "closes": closes,
                "reasons": ["decisionOutsideTarget"]}
    reasons = [calendar[i] + ":" + source["reasons"][i] for i in indices
               if source["reasons"][i] is not None]
    if reasons:
        return {"status": "unknown", "dates": days, "closes": closes, "reasons": reasons}
    reasons = [day + ":closeBelowSupport" for day, close in zip(days, closes) if close < support]
    return {"status": "failed" if reasons else "passed", "dates": days,
            "closes": closes, "reasons": reasons}


def build_undercut_reclaim(dataset, *, start, end, progress=None):
    """Return all formation evidence, original Top5 and three five-field schedules.

    Dataset calendar, dated bars and trade-status keys must be at or before end.
    Warmup before start is retained. Supply the full original sample, including
    illiquid/ineligible securities: only prior price completeness defines ranks.
    Appending source sessions with a later end may resolve prior censoring but
    cannot change an existing formation or immediate schedule. No I/O or mutation.
    """
    start, end = _day(start), _day(end)
    if start > end:
        raise ValueError("start must not be after end")
    if not isinstance(dataset, dict) or dataset.get("priceBasisId") != PRICE_BASIS:
        raise ValueError("An explicitly raw-unadjusted dataset is required")
    values = dataset.get("calendar")
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("A nonempty source calendar is required")
    calendar = list(values)
    if any(_day(d) > end for d in calendar) or any(a >= b for a, b in zip(calendar, calendar[1:])):
        raise ValueError("Calendar must be unique, increasing and contain no future dates")
    target_dates = [d for d in calendar if start <= d <= end]
    if not target_dates:
        raise ValueError("No source-calendar sessions in requested target")
    calendar_set, target_set = set(calendar), set(target_dates)
    securities = dataset.get("securities")
    if not isinstance(securities, (list, tuple)) or not securities:
        raise ValueError("A nonempty full original securities sample is required")
    originals = {}
    for security in securities:
        symbol = security.get("symbol") if isinstance(security, dict) else None
        if not isinstance(symbol, str) or not symbol.strip() or symbol in originals:
            raise ValueError("Sample symbols must be nonempty and unique")
        originals[symbol] = security
    symbols = sorted(originals)
    benchmark = _prepare_source(dataset.get("benchmark"), calendar, calendar_set, end)
    sources = {}
    for n, symbol in enumerate(symbols, 1):
        sources[symbol] = _prepare_source(originals[symbol], calendar, calendar_set, end)
        if progress is not None and (n % 100 == 0 or n == len(symbols)):
            progress({"stage": "priceValidity", "completedSymbols": n, "totalSymbols": len(symbols)})
    daily_rows, opportunities = [], []
    schedules = {mode: [] for mode in MODES}
    confirmation_counts = Counter({key: 0 for key in ("passed", "failed", "unknown", "censored")})
    for index, day in enumerate(calendar):
        if day not in target_set:
            continue
        first = index - PRIOR_SLOTS
        prior_days = calendar[max(0, first):index]
        prior20_days = prior_days[-AMOUNT_SLOTS:]
        counts = {"sampleCount": len(symbols), "priorPriceIncomplete": 0,
                  "priorReturnInvalid": 0, "rankPopulationCount": 0, "strongCount": 0,
                  "amountUnknownCount": 0, "formationPriceUnknownCount": 0,
                  "setupCount": 0, "selectedCount": 0}
        benchmark_complete = first >= 0 and _complete(benchmark, first, index)
        benchmark_reason = ("insufficientPriorCalendarHistory" if first < 0
                            else "incompleteBenchmarkPriorPrice" if not benchmark_complete else None)
        benchmark_ratio, benchmark_return = None, None
        if benchmark_complete:
            try:
                benchmark_ratio = benchmark["bars"][index - 1]["close"] / benchmark["bars"][first]["close"]
                benchmark_return = benchmark_ratio - 1
            except (ValueError, OverflowError, ZeroDivisionError):
                benchmark_ratio = None
            if not _positive(benchmark_ratio) or not _finite(benchmark_return):
                benchmark_complete, benchmark_return = False, None
                benchmark_reason = "nonfiniteBenchmarkPriorReturn"
        population = []
        for symbol in symbols:
            source = sources[symbol]
            if first < 0 or not _complete(source, first, index):
                counts["priorPriceIncomplete"] += 1
                continue
            if not benchmark_complete:
                continue
            try:
                ratio = source["bars"][index - 1]["close"] / source["bars"][first]["close"]
                own, relative = ratio - 1, ratio / benchmark_ratio - 1
            except (ValueError, OverflowError, ZeroDivisionError):
                own, relative = None, None
            if not _finite(own) or not _finite(relative):
                counts["priorReturnInvalid"] += 1
                continue
            population.append({"symbol": symbol, "priorOwn60": own, "priorRelative60": relative})
        # Midrank uses every prior-price-valid finite-return member, before own
        # positive, amount, setup-day quality or any account eligibility check.
        ordered = sorted(population, key=lambda r: (r["priorRelative60"], r["symbol"]))
        cursor, size = 0, len(ordered)
        while cursor < size:
            stop = cursor + 1
            while stop < size and ordered[stop]["priorRelative60"] == ordered[cursor]["priorRelative60"]:
                stop += 1
            percentile = (cursor + .5 * (stop - cursor)) / size
            for member in ordered[cursor:stop]:
                member["priorRank60"] = percentile
            cursor = stop
        counts["rankPopulationCount"] = size
        candidates = []
        for member in population:
            if member["priorRank60"] < RANK_THRESHOLD or member["priorOwn60"] <= 0:
                continue
            counts["strongCount"] += 1
            source = sources[member["symbol"]]
            prior20 = source["bars"][index - AMOUNT_SLOTS:index]
            if not all(_positive(bar.get("amount")) for bar in prior20):
                counts["amountUnknownCount"] += 1
                continue
            try:
                prior_amount = math.fsum(bar["amount"] for bar in prior20) / AMOUNT_SLOTS
            except (ValueError, OverflowError):
                prior_amount = None
            if not _positive(prior_amount):
                counts["amountUnknownCount"] += 1
                continue
            if source["reasons"][index] is not None:
                counts["formationPriceUnknownCount"] += 1
                continue
            support = min(bar["low"] for bar in prior20)
            bar = source["bars"][index]
            if bar["low"] < support and bar["close"] > support:
                candidates.append({**member, "priorAmount20": prior_amount, "support": support,
                                   "formationLow": bar["low"], "formationClose": bar["close"]})
        candidates.sort(key=lambda r: (-r["priorAmount20"], r["symbol"]))
        selected_ids = []
        for rank, candidate in enumerate(candidates, 1):
            candidate.update(originalRank=rank, selected=rank <= TOP_N)
            if rank > TOP_N:
                continue
            symbol = candidate["symbol"]
            formation_id = f"{SID}:{day}:{symbol}"
            selected_ids.append(formation_id)
            confirmation = _confirmation(sources[symbol], calendar, index, candidate["support"], target_set)
            confirmation_counts[confirmation["status"]] += 1
            delayed_day = (calendar[index + DELAY_SLOTS]
                           if index + DELAY_SLOTS < len(calendar) and calendar[index + DELAY_SLOTS] in target_set else None)
            decisions = {"immediate": day, "delay2": delayed_day, "confirm2": delayed_day}
            signals = {mode: None for mode in MODES}
            for mode in MODES:
                if decisions[mode] is not None and (mode != "confirm2" or confirmation["status"] == "passed"):
                    signals[mode] = _signal(formation_id, symbol, decisions[mode], rank)
                    schedules[mode].append(dict(signals[mode]))
            opportunity = {key: value for key, value in candidate.items() if key != "selected"}
            opportunity.update(formationId=formation_id, formationDate=day, priorDate=calendar[index - 1],
                               decisionDates=decisions, confirmation=confirmation, signals=signals)
            opportunities.append(opportunity)
        counts.update(setupCount=len(candidates), selectedCount=len(selected_ids))
        daily_rows.append({"formationDate": day, "priorDate": calendar[index - 1] if index else None,
                           "priorWindowDates": prior_days, "prior20Dates": prior20_days,
                           "rankPopulationCount": size, "rankPopulationSha256": _digest(population),
                           "benchmarkPrior60": {"complete": benchmark_complete, "reason": benchmark_reason,
                                                "return60": benchmark_return},
                           "counts": counts, "candidates": candidates, "selectedFormationIds": selected_ids})
        if progress is not None and (len(daily_rows) % 20 == 0 or day == target_dates[-1]):
            progress({"stage": "formation", "completedDays": len(daily_rows), "totalDays": len(target_dates),
                      "formationDate": day, "opportunityCount": len(opportunities)})
    counts = {"sampleCount": len(symbols), "targetDayCount": len(target_dates),
              "setupCount": sum(row["counts"]["setupCount"] for row in daily_rows),
              "opportunityCount": len(opportunities),
              "daysWithSetups": sum(bool(row["candidates"]) for row in daily_rows),
              "daysWithSelected": sum(bool(row["selectedFormationIds"]) for row in daily_rows),
              "confirmation": dict(confirmation_counts),
              "signals": {mode: len(schedules[mode]) for mode in MODES}}
    return {"version": VERSION, "strategyId": SID, "modes": list(MODES), "calendar": calendar,
            "targetDates": target_dates, "dailyRows": daily_rows, "opportunities": opportunities,
            "schedules": schedules, "counts": counts, "certified": False, "targetAchieved": False}
