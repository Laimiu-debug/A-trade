"""Causal full upward gaps and a common two-session observation clock.

No account state, outcomes, file I/O, parameter search or input mutation. Raw
quality and cross-sectional midranks reuse the frozen leader/lag helpers.
"""
from collections import Counter
from copy import deepcopy
import math

from .research_leader_lag import _Series, _day, _digest, _finite, _positive, _rank, _relative_return

VERSION = "full-gap-retention-v1"
STRATEGY_ID = "fullGapRetentionV1"
MODES = ("control", "retained")
TOP_N, HISTORY, LOOKBACK, DELAY = 5, 120, 20, 2
MIN_AMOUNT, MIN_RANK = 50_000_000, .8
BASIS = {
    "price": "raw-unadjusted; positive finite OHLCV, envelope and no known suspension; exact source slots",
    "rank": "all price-complete cohort members; RS60 versus sh000300; ascending tied midrank (i+j)/(2N)",
    "history": "at least120 valid OHLCV observations through F-1; not listing age or120 consecutive sessions",
    "st": "exclude only explicit stByDate[F-1] is True; unknown historical status retained",
    "activity": "F volume > mean prior20 share volumes; each prior amount positive; mean amount>=50000000 CNY",
    "gap": "raw lowF > raw highF-1; fixed boundary highF-1; no additional price-high breakout",
    "lock": "every qualified setup, including non-Top5, owns F..F+2; locked setups do not extend or replace",
    "selection": "unlocked qualified setups sort prior20 mean amount descending then symbol; original Top5; no refill",
    "clock": "both policies decide at F+2; F+1 and F+2 must have complete OHLCV/status; no earlier signal",
    "retention": "both subsequent lows strictly greater than the frozen boundary; equality fails",
    "reference": "F..F+2 explicit preclose mismatch uses portfolio_engine._observe_actions: abs(reference-previousClose)>max(.005,previousClose*1e-7); unknown for both policies; no reconstruction",
    "limitations": "fixed local surviving cohorts, unknown historic eligibility and undeclared corporate actions; development history, no certification",
}


def _mean(values):
    try:
        value = math.fsum(values) / len(values)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return value if _positive(value) else None


def _safe(value):
    return value if _finite(value) else None


def _reference_checks(security, source, calendar, indices):
    """Detection only; never retroactively adjusts the formation boundary."""
    mapping = security.get("precloseByDate", {})
    if not isinstance(mapping, dict):
        raise ValueError("precloseByDate must be a mapping")
    rows = []
    for index in indices:
        day = calendar[index]
        previous = source.bars[index - 1] if index else None
        previous_close = previous.get("close") if previous else None
        explicit = day in mapping
        value = mapping.get(day)
        threshold = max(.005, previous_close * 1e-7) if _positive(previous_close) else None
        if not explicit:
            status = "notDeclared"
        elif not _positive(value):
            status = "invalidReference"
        elif index == 0 or source.reasons[index - 1] is not None:
            status = "unknownPreviousBar"
        elif abs(value - previous_close) > threshold:
            status = "changedReference"
        else:
            status = "unchangedReference"
        rows.append({"date": day, "status": status, "explicit": explicit,
                     "previousClose": _safe(previous_close), "preclose": _safe(value), "threshold": threshold})
    return rows


def _confirmation(security, source, calendar, index, boundary, target_set):
    indices = [i for i in range(index + 1, min(index + 3, len(calendar))) if calendar[i] in target_set]
    dates = [calendar[i] for i in indices]
    lows = [_safe(source.bars[i].get("low")) if source.bars[i] else None for i in indices]
    checks = _reference_checks(security, source, calendar, [index, *indices])
    bar_reasons = [{"date": calendar[i], "reason": source.reasons[i]} for i in indices]
    if len(indices) != DELAY:
        status, retained, reasons = "censored", None, ["decisionOutsideTarget"]
    else:
        reasons = [row["date"] + ":" + row["reason"] for row in bar_reasons if row["reason"]]
        reasons += [row["date"] + ":" + row["status"] for row in checks
                    if row["status"] not in ("notDeclared", "unchangedReference")]
        status = "unknown" if reasons else "known"
        retained = None if reasons else all(value > boundary for value in lows)
    return {"status": status, "dates": dates, "lows": lows, "barReasons": bar_reasons,
            "referenceChecks": checks, "reasons": reasons, "retained": retained}


def _signal(identity, symbol, day, rank):
    return {"signalId": identity, "strategyId": STRATEGY_ID, "symbol": symbol,
            "signalDate": day, "originalRank": rank}


def build_full_gap_retention(dataset, *, start, end, progress=None, on_evidence=None):
    """Return compact daily/formation/Top5 evidence and two five-field schedules.

    on_evidence receives one separate full-symbol evidence record per target F.
    Numeric observations after end are not inspected. Increasing end can resolve
    a previous censor but cannot change a prior formation, rank, lock or Top5.
    """
    start, end = _day(start), _day(end)
    if start > end or dataset.get("priceBasisId") != "raw-unadjusted":
        raise ValueError("Ordered dates and explicitly raw-unadjusted data required")
    full_calendar = dataset.get("calendar")
    if not isinstance(full_calendar, list) or not full_calendar:
        raise ValueError("Nonempty source calendar required")
    if any(_day(d) != d for d in full_calendar) or full_calendar != sorted(set(full_calendar)):
        raise ValueError("Source calendar must have unique increasing canonical dates")
    calendar = [d for d in full_calendar if d <= end]
    target_dates = [d for d in calendar if start <= d <= end]
    if not target_dates:
        raise ValueError("No target source sessions")
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities:
        raise ValueError("Complete fixed cohort required")
    originals = {}
    for security in securities:
        symbol = security.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in originals:
            raise ValueError("Unique nonempty symbols required")
        if not isinstance(security.get("stByDate", {}), dict):
            raise ValueError("stByDate must be a mapping")
        originals[symbol] = security
    symbols = sorted(originals)
    sources = {symbol: _Series(originals[symbol], calendar, end) for symbol in symbols}
    benchmark = _Series(dataset["benchmark"], calendar, end)
    target_set = set(target_dates)
    locks, daily, formations, opportunities = {}, [], [], []
    schedules = {mode: [] for mode in MODES}
    total_reasons = Counter()
    confirmations = Counter({key: 0 for key in ("known", "unknown", "censored")})
    selected_outcomes = Counter({key: 0 for key in ("retained", "filled", "unknown", "censored")})
    for index, day in enumerate(calendar):
        if day not in target_set:
            continue
        prior = index - 1
        values, own_values, rank_reasons = {}, {}, {}
        for symbol in symbols:
            source = sources[symbol]
            relative, reason = _relative_return(source, benchmark, prior, 60)
            own = None
            if reason is None:
                own = source.bars[prior]["close"] / source.bars[prior - 60]["close"] - 1
                if not _finite(own):
                    relative, own, reason = None, None, "nonfiniteOwnReturn"
            values[symbol], own_values[symbol], rank_reasons[symbol] = relative, own, reason
        ranks, population = _rank(values)
        rows, qualified, unlocked = [], [], []
        for symbol in symbols:
            source, security = sources[symbol], originals[symbol]
            bar = source.bars[index]
            prior_bar = source.bars[prior] if prior >= 0 else None
            prior_count = index - source.bad[index]
            st = security.get("stByDate", {}).get(calendar[prior]) if prior >= 0 else None
            st_known = st if type(st) is bool else None
            activity_reason = source.window_reason(prior, LOOKBACK)
            window = source.bars[index - LOOKBACK:index] if index >= LOOKBACK else []
            mean_volume = _mean([b["volume"] for b in window]) if activity_reason is None else None
            mean_amount = None
            amount_reason = "insufficientCalendarHistory" if len(window) != LOOKBACK else None
            if amount_reason is None:
                if any(b is None or not _positive(b.get("amount")) for b in window):
                    amount_reason = "nonpositiveOrUnknownPriorAmount"
                else:
                    mean_amount = _mean([b["amount"] for b in window])
                    if mean_amount is None:
                        amount_reason = "nonfinitePriorMeanAmount"
            reasons = []
            if rank_reasons[symbol]: reasons.append("rank:" + rank_reasons[symbol])
            if activity_reason: reasons.append("volume:" + activity_reason)
            elif mean_volume is None: reasons.append("volume:nonfinitePriorMeanVolume")
            if amount_reason: reasons.append("amount:" + amount_reason)
            if source.reasons[index]: reasons.append("formation:" + source.reasons[index])
            failures = []
            if prior_count < HISTORY: failures.append("insufficientObservedHistory120")
            if st is True: failures.append("knownPriorST")
            if own_values[symbol] is not None and own_values[symbol] <= 0: failures.append("nonpositivePriorOwn60")
            if ranks[symbol] is not None and ranks[symbol] < MIN_RANK: failures.append("priorRankBelowTop20Percent")
            if mean_amount is not None and mean_amount < MIN_AMOUNT: failures.append("priorMeanAmountBelowMinimum")
            eligible = not reasons and not failures
            gap = (bar["low"] > prior_bar["high"]) if eligible else None
            active = (bar["volume"] > mean_volume) if eligible else None
            raw_setup = eligible and gap and active
            if eligible and not gap: failures.append("notFullUpwardGap")
            if eligible and not active: failures.append("formationVolumeNotAbovePriorMean")
            lock = locks.get(symbol)
            locked = bool(raw_setup and lock and index <= lock["untilIndex"])
            identity = STRATEGY_ID + ":" + day + ":" + symbol
            row = {"symbol": symbol, "priorValidObservations": prior_count, "knownStPrior": st_known,
                   "priorOwn60": own_values[symbol], "priorRelative60": values[symbol], "priorRank60": ranks[symbol],
                   "priorMeanAmount20": mean_amount, "priorMeanVolume20": mean_volume,
                   "formationLow": _safe(bar.get("low")) if bar else None,
                   "formationVolume": _safe(bar.get("volume")) if bar else None,
                   "gapBoundary": _safe(prior_bar.get("high")) if prior_bar else None,
                   "qualityReasons": reasons, "failureReasons": failures,
                   "eligibilityKnown": not reasons, "eligible": eligible,
                   "rawSetup": bool(raw_setup), "selectionStatus": "notSetup", "originalRank": None,
                   "lockOwner": lock["formationId"] if locked else None}
            rows.append(row)
            total_reasons.update(reasons)
            if not raw_setup:
                continue
            formation = {"formationId": identity, "symbol": symbol, "formationDate": day,
                         "priorDate": calendar[prior], **deepcopy({k: v for k, v in row.items() if k != "symbol"}),
                         "lockUntilDate": calendar[min(index + DELAY, len(calendar) - 1)],
                         "lockUntilIndex": index + DELAY, "lockApplied": not locked}
            if locked:
                formation["selectionStatus"] = row["selectionStatus"] = "locked"
                formation["lockUntilDate"] = lock["untilDate"]
                formation["lockUntilIndex"] = lock["untilIndex"]
            else:
                locks[symbol] = {"formationId": identity, "untilIndex": index + DELAY,
                                 "untilDate": formation["lockUntilDate"]}
                unlocked.append((formation, row))
            qualified.append(formation)
        unlocked.sort(key=lambda pair: (-pair[0]["priorMeanAmount20"], pair[0]["symbol"]))
        for rank, (formation, row) in enumerate(unlocked, 1):
            selection = "selected" if rank <= TOP_N else "rankExcluded"
            formation["selectionStatus"] = row["selectionStatus"] = selection
            formation["originalRank"] = row["originalRank"] = rank
            if rank > TOP_N:
                continue
            symbol, identity = formation["symbol"], formation["formationId"]
            confirmation = _confirmation(originals[symbol], sources[symbol], calendar, index,
                                         formation["gapBoundary"], target_set)
            decision_date = calendar[index + DELAY] if index + DELAY < len(calendar) and calendar[index + DELAY] in target_set else None
            known = confirmation["status"] == "known"
            outcome = ("retained" if confirmation["retained"] else "filled") if known else confirmation["status"]
            signals = {mode: (_signal(identity, symbol, decision_date, rank)
                              if known and (mode == "control" or confirmation["retained"]) else None) for mode in MODES}
            opportunity = {**deepcopy(formation), "signalId": identity, "decisionDate": decision_date,
                           "confirmation": confirmation, "control": True if known else None,
                           "retained": confirmation["retained"], "signals": signals}
            opportunities.append(opportunity)
            confirmations[confirmation["status"]] += 1
            selected_outcomes[outcome] += 1
            for mode in MODES:
                if signals[mode]: schedules[mode].append(deepcopy(signals[mode]))
        formations.extend(qualified)
        daily_row = {"formationDate": day, "priorDate": calendar[prior] if prior >= 0 else None,
                     "priorWindowDates61": calendar[max(0, index - 61):index],
                     "activityWindowDates20": calendar[max(0, index - 20):index],
                     "rankPopulation": population, "rankReasonCounts": dict(sorted(Counter(r for r in rank_reasons.values() if r).items())),
                     "fixedPopulation": len(symbols), "qualityUnknownCount": sum(not r["eligibilityKnown"] for r in rows),
                     "eligibleCount": sum(r["eligible"] for r in rows), "rawSetupCount": len(qualified),
                     "lockedSetupCount": sum(f["selectionStatus"] == "locked" for f in qualified),
                     "rankedSetupCount": len(unlocked), "selectedCount": min(TOP_N, len(unlocked)),
                     "candidates": [{"formationId": f["formationId"], "symbol": f["symbol"],
                                     "originalRank": f["originalRank"], "priorMeanAmount20": f["priorMeanAmount20"],
                                     "selected": f["selectionStatus"] == "selected"} for f, _ in unlocked],
                     "symbolEvidenceDigest": _digest(rows)}
        daily.append(daily_row)
        if on_evidence is not None:
            on_evidence({"formationDate": day, "priorDate": daily_row["priorDate"],
                         "rankPopulation": deepcopy(population), "rows": rows, "rowsDigest": daily_row["symbolEvidenceDigest"]})
        if progress is not None and (len(daily) % 10 == 0 or day == target_dates[-1]):
            progress({"stage": "formations", "completedDays": len(daily), "targetDays": len(target_dates),
                      "formations": len(formations), "opportunities": len(opportunities)})
    return {"version": VERSION, "strategyId": STRATEGY_ID, "modes": list(MODES), "calendar": calendar,
            "targetDates": target_dates, "dailyRows": daily, "formations": formations, "opportunities": opportunities,
            "schedules": schedules, "basis": deepcopy(BASIS), "counts": {
                "sourceSymbols": len(symbols), "targetDays": len(target_dates), "symbolDays": len(symbols) * len(target_dates),
                "rawSetups": len(formations), "lockedSetups": sum(f["selectionStatus"] == "locked" for f in formations),
                "rankExcludedSetups": sum(f["selectionStatus"] == "rankExcluded" for f in formations),
                "opportunities": len(opportunities), "confirmation": dict(confirmations), "outcomes": dict(selected_outcomes),
                "signals": {mode: len(rows) for mode, rows in schedules.items()}, "qualityReasonCounts": dict(sorted(total_reasons.items()))},
            "certified": False, "targetAchieved": False}
