"""Pure, bounded relative-repair event schedules and their replay evidence.

This is a descriptive matched comparison within an existing hypothesis family,
not identification of a causal effect. Only supplied raw, unadjusted prices are
used. There is no account state, I/O, price adjustment or future event warmup.
"""
from collections import Counter
from copy import deepcopy
from datetime import date
import math
from statistics import mean


VERSION = "relative-repair-event-v1"
STRATEGY_ID = "relativeRepairEventV1"
GROUPS = ("allRepaired", "repaired", "matchedControl")


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return value


def _positive(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return deepcopy(value)


def _bars(rows, calendar_set):
    if not isinstance(rows, list):
        raise ValueError("bars must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each supplied bar must be an object")
        day = _day(row.get("date"))
        if day in result:
            raise ValueError("Duplicate bar date")
        if day not in calendar_set:
            raise ValueError("Bar date outside supplied calendar")
        result[day] = row
    return result


def _midrank(values):
    """Ascending percentile (strictly-less + half-equal) / full valid N."""
    counts = Counter(values)
    lower, result, n = 0, {}, len(values)
    for value in sorted(counts):
        result[value] = (lower + counts[value] / 2) / n
        lower += counts[value]
    return result


def _freeze(calendar, peak_index, stocks):
    dates = calendar[max(0, peak_index - 60):peak_index + 1]
    valid, invalid, inputs = [], [], []
    for symbol, bars in stocks.items():
        close = [bars.get(day, {}).get("close") for day in dates]
        amounts = [bars.get(day, {}).get("amount") for day in dates[-20:]]
        inputs.append({"symbol": symbol, "closes": _safe(close), "amounts20": _safe(amounts)})
        reasons = []
        if len(dates) != 61:
            reasons.append("insufficient61CalendarSlots")
        if not all(_positive(value) for value in close):
            reasons.append("missingOrInvalidClose61")
        if len(amounts) != 20 or not all(_positive(value) for value in amounts):
            reasons.append("missingOrInvalidAmount20")
        if not reasons:
            ret, amount = close[-1] / close[0] - 1, mean(amounts)
            if not math.isfinite(ret) or not math.isfinite(amount):
                reasons.append("nonfiniteDerivedFeature")
        if reasons:
            invalid.append({"symbol": symbol, "reasons": reasons})
        else:
            valid.append({"symbol": symbol, "anchorClose": close[-1],
                          "return60": ret, "amount20": amount})
    rp = _midrank([r["return60"] for r in valid])
    ap = _midrank([r["amount20"] for r in valid])
    valid.sort(key=lambda r: (-r["return60"], r["symbol"]))
    limit = (len(valid) + 4) // 5
    for rank, row in enumerate(valid, 1):
        row.update(return60Rank=rank, return60Percentile=rp[row["return60"]],
                   amount20Percentile=ap[row["amount20"]],
                   candidate=rank <= limit and row["return60"] > 0)
    frozen = [deepcopy(row) for row in valid if row["candidate"]]
    return frozen, {"validPopulation": valid, "invalidPopulation": invalid,
                    "validPopulationCount": len(valid), "top20Count": limit}, {
                        "dates61": dates, "amountDates20": dates[-20:], "securities": inputs}


def _prior_peak(calendar, index, benchmark):
    dates = calendar[max(0, index - 20):index]
    closes = [benchmark.get(day, {}).get("close") for day in dates]
    evidence = {"dates20": dates, "closes20": _safe(closes)}
    if len(dates) != 20 or not all(_positive(c) for c in closes):
        return None, evidence
    peak = max(closes)
    peak_date = max(day for day, close in zip(dates, closes) if close == peak)
    return {"date": peak_date, "close": peak}, evidence


def _observe(frozen, stocks, day, previous, seen, used, unknown):
    observations, crosses = [], []
    for feature in frozen:
        symbol, anchor = feature["symbol"], feature["anchorClose"]
        current = stocks[symbol].get(day, {})
        previous_bar = stocks[symbol].get(previous, {})
        close, prev, volume = current.get("close"), previous_bar.get("close"), current.get("volume")
        row = {"symbol": symbol, "previousDate": previous, "previousClose": _safe(prev),
               "close": _safe(close), "volume": _safe(volume), "anchorClose": anchor,
               "seenBefore": symbol in seen, "controlUsedBefore": symbol in used}
        if symbol in unknown:
            row.update(crossStatus="unknown", crossReason="eventCloseHistoryTerminated")
        elif symbol in seen:
            row.update(crossStatus="excluded", crossReason="firstCrossAlreadySeen")
        elif not _positive(close) or not _positive(prev):
            unknown[symbol] = day
            row.update(crossStatus="unknown", crossReason="adjacentCloseUnavailable")
        elif not (prev < anchor <= close):
            row.update(crossStatus="excluded", crossReason="noUpwardCross")
        else:
            crosses.append(feature)
            seen.add(symbol)
            row["firstPriceCross"] = True
            if _positive(volume):
                row.update(crossStatus="eligibleFirstCross", crossReason="adjacentPositiveVolumeRepair")
            else:
                missing = type(volume) not in (int, float) or not math.isfinite(volume)
                row.update(crossStatus="unknown" if missing else "excluded",
                           crossReason="volumeUnavailable" if missing else "nonpositiveVolume")
        observations.append(row)
    # All first-crosses become seen before any Top5 selection or control matching.
    for row in observations:
        symbol = row["symbol"]
        if symbol in unknown:
            status, reason = "unknown", "eventCloseHistoryTerminated"
        elif symbol in seen:
            status, reason = "excluded", "firstCrossSeen"
        elif symbol in used:
            status, reason = "excluded", "controlAlreadyUsed"
        elif not _positive(row["close"]):
            status, reason = "unknown", "currentCloseUnavailable"
        elif row["close"] >= row["anchorClose"]:
            status, reason = "excluded", "notBelowAnchor"
        elif not _positive(row["volume"]):
            value = row["volume"]
            status, reason = (("excluded", "nonpositiveVolume") if type(value) in (int, float)
                              and math.isfinite(value) else ("unknown", "volumeUnavailable"))
        else:
            status, reason = "eligible", "belowAnchorUnusedAndUnseen"
        row.update(controlStatus=status, controlReason=reason)
    return observations, sorted(crosses, key=lambda r: (-r["amount20"], r["symbol"]))


def _signal(group, event, day, symbol, rank):
    return {"signalId": f"{group}:{event}:{day}:{symbol}", "strategyId": STRATEGY_ID,
            "symbol": symbol, "signalDate": day, "originalRank": rank}


def build_relative_repair(dataset, *, start, end, progress=None):
    """Build a full Top5 schedule and two equal-length paired diagnostics.

    Events start armed and flat at the first supplied session in [start, end].
    Warmup is usable only for feature and benchmark lookbacks. All active ages
    count supplied exchange slots, including unknown observations. No price
    gap is bridged. Caller is responsible for calendar and raw-price provenance.
    """
    start, end = _day(start), _day(end)
    if start > end or not isinstance(dataset, dict):
        raise ValueError("Expected dataset and ordered target bounds")
    calendar = dataset.get("calendar")
    if not isinstance(calendar, list) or any(_day(d) != d for d in calendar) or calendar != sorted(set(calendar)):
        raise ValueError("Calendar must be ordered and unique")
    calendar_set, indexes = set(calendar), {d: i for i, d in enumerate(calendar)}
    benchmark_source = dataset.get("benchmark")
    if not isinstance(benchmark_source, dict) or benchmark_source.get("symbol") != "sh000300":
        raise ValueError("Expected sh000300 benchmark")
    benchmark = _bars(benchmark_source.get("bars"), calendar_set)
    securities = dataset.get("securities")
    if not isinstance(securities, list):
        raise ValueError("securities must be a list")
    stocks = {}
    for security in securities:
        if not isinstance(security, dict) or not isinstance(security.get("symbol"), str) or not security["symbol"]:
            raise ValueError("Each security needs a symbol")
        if security["symbol"] in stocks:
            raise ValueError("Duplicate security symbol")
        stocks[security["symbol"]] = _bars(security.get("bars"), calendar_set)
    stocks = dict(sorted(stocks.items()))
    target = [d for d in calendar if start <= d <= end]
    schedules = {group: [] for group in GROUPS}
    events, daily, freeze_inputs = [], [], {}
    active, armed, seen, used, unknown = None, True, set(), set(), {}

    def finish(event, day, reason, censored=False):
        event.update(endDate=day, endReason=reason, censored=censored,
                     seenSymbols=sorted(seen), usedControlSymbols=sorted(used),
                     unknownSymbols=deepcopy(unknown))

    for number, day in enumerate(target):
        index = indexes[day]
        close = benchmark.get(day, {}).get("close")
        peak, peak_evidence = _prior_peak(calendar, index, benchmark)
        row = {"date": day, "armedBefore": armed, "eventId": active["eventId"] if active else None,
               "benchmarkClose": _safe(close), "prior20Peak": peak,
               "benchmarkWindow": peak_evidence, "status": "idle", "reason": "noDrawdownTrigger",
               "observations": [], "firstCrosses": [], "selectedTop5": [], "pairs": [],
               "unmatched": [], "unknownCounts": {"benchmark": 0, "cross": 0, "control": 0}}
        if active is not None:
            age = index - active["triggerIndex"]
            row.update(age=age, status="observing", reason="activeEvent")
            active["observedSessions"] = age
            if not _positive(close):
                row.update(status="unknown", reason="benchmarkCloseUnavailable")
                row["unknownCounts"]["benchmark"] = 1
                finish(active, day, "benchmarkCloseUnavailable")
                active = None
            elif close >= active["peakClose"]:
                row.update(status="terminated", reason="benchmarkRecovered")
                finish(active, day, "benchmarkRecovered")
                active = None
            else:
                observations, crosses = _observe(active["candidates"], stocks, day, calendar[index - 1], seen, used, unknown)
                row["observations"] = observations
                row["unknownCounts"]["cross"] = sum(r["crossStatus"] == "unknown" for r in observations)
                row["unknownCounts"]["control"] = sum(r["controlStatus"] == "unknown" for r in observations)
                observation_map = {r["symbol"]: r for r in observations}
                qualified = [f for f in crosses if observation_map[f["symbol"]]["crossStatus"] == "eligibleFirstCross"]
                ranks = {f["symbol"]: rank for rank, f in enumerate(qualified, 1)}
                first = [{"symbol": f["symbol"], "date": day, "rank": ranks.get(f["symbol"]),
                          "amount20": f["amount20"], "selected": ranks.get(f["symbol"], 6) <= 5,
                          "status": observation_map[f["symbol"]]["crossStatus"],
                          "reason": observation_map[f["symbol"]]["crossReason"]} for f in crosses]
                row["firstCrosses"] = first
                row["selectedTop5"] = [deepcopy(r) for r in first if r["selected"]]
                active["firstCrosses"].extend(deepcopy(first))
                for rank, repaired in enumerate(qualified[:5], 1):
                    schedules["allRepaired"].append(_signal("allRepaired", active["eventId"], day, repaired["symbol"], rank))
                if qualified:
                    available = {r["symbol"] for r in observations if r["controlStatus"] == "eligible"}
                    features = {r["symbol"]: r for r in active["candidates"]}
                    for rank, repaired in enumerate(qualified[:5], 1):
                        distances = [{"symbol": symbol,
                            "distance": abs(repaired["return60Percentile"] - features[symbol]["return60Percentile"])
                                + abs(repaired["amount20Percentile"] - features[symbol]["amount20Percentile"])}
                            for symbol in available]
                        distances.sort(key=lambda r: (r["distance"], r["symbol"]))
                        if not distances:
                            row["unmatched"].append({"symbol": repaired["symbol"], "originalRank": rank,
                                                     "reason": "noEligibleControl"})
                            continue
                        best = distances[0]
                        pair = {"eventId": active["eventId"], "date": day, "originalRank": rank,
                                "repairedSymbol": repaired["symbol"], "controlSymbol": best["symbol"],
                                "distance": best["distance"], "eligibleControls": distances}
                        row["pairs"].append(pair)
                        active["pairs"].append(deepcopy(pair))
                        used.add(best["symbol"])
                        available.remove(best["symbol"])
                        for group, symbol in (("repaired", repaired["symbol"]), ("matchedControl", best["symbol"])):
                            schedules[group].append(_signal(group, active["eventId"], day, symbol, rank))
                if age == 40:
                    finish(active, day, "observationWindowExpired")
                    row["endReason"] = "observationWindowExpired"
                    active = None
        elif armed:
            if not _positive(close) or peak is None:
                row.update(status="unknown", reason="benchmarkTriggerHistoryUnavailable")
                row["unknownCounts"]["benchmark"] = 1
            elif close <= .95 * peak["close"]:
                event_id = f"relativeRepair:{day}"
                frozen, population, raw = _freeze(calendar, indexes[peak["date"]], stocks)
                freeze_inputs[event_id] = raw
                active = {"eventId": event_id, "triggerDate": day, "triggerIndex": index,
                          "triggerClose": close, "peakDate": peak["date"], "peakClose": peak["close"],
                          "candidates": frozen, "population": population, "observedSessions": 0,
                          "firstCrosses": [], "pairs": [], "endDate": None, "endReason": None,
                          "censored": False}
                events.append(active)
                seen, used, unknown, armed = set(), set(), {}, False
                trigger_observations = []
                for feature in frozen:
                    symbol = feature["symbol"]
                    stock_close = stocks[symbol].get(day, {}).get("close")
                    known = _positive(stock_close)
                    if not known:
                        unknown[symbol] = day
                    trigger_observations.append({"symbol": symbol, "close": _safe(stock_close),
                        "status": "observed" if known else "unknown",
                        "reason": "triggerCloseObserved" if known else "triggerCloseUnavailable"})
                active["triggerObservations"] = trigger_observations
                row["unknownCounts"]["cross"] = len(unknown)
                row.update(eventId=event_id, status="triggered", reason="fivePercentBenchmarkDrawdown", age=0)
        # A completed event may rearm today, but cannot trigger again today.
        if active is None and not armed:
            if _positive(close) and peak is not None and close > peak["close"]:
                armed = True
                row["rearmed"] = True
            elif row["status"] == "idle":
                row.update(status="waitingRearm", reason="benchmarkNotAbovePrior20High")
                if not _positive(close) or peak is None:
                    row.update(status="unknown", reason="benchmarkRearmHistoryUnavailable")
                    row["unknownCounts"]["benchmark"] = 1
        row["armedAfter"] = armed
        daily.append(row)
        if progress is not None:
            progress({"completed": number + 1, "total": len(target), "date": day,
                      "eventCount": len(events), "pairedSignals": len(schedules["repaired"])})
    if active is not None:
        finish(active, target[-1], "periodCutoff", censored=True)
    return {"version": VERSION, "schedules": schedules, "events": events, "dailyEvidence": daily,
            "evidence": {"freezeInputs": freeze_inputs},
            "metadata": {"strategyId": STRATEGY_ID, "start": start, "end": end,
                "targetSessions": len(target), "universeCount": len(stocks), "eventCount": len(events),
                "pairedSignals": len(schedules["repaired"]), "allRepairedSignals": len(schedules["allRepaired"]),
                "initialState": "flatAndArmed",
                "eventWarmup": "none; features may use pre-period raw observations",
                "percentileBasis": "(strictly-less + 0.5 * equal) / all-valid-population-N",
                "rankingBasis": "return60 descending then symbol; ceil(N*0.2), positive return only",
                "amountWindow": "20 calendar slots ending at peak, inclusive",
                "benchmarkMissingPolicy": "advance age; terminate event unknown; no signals; require strict new high to rearm",
                "stockMissingPolicy": "invalid close from trigger through first cross permanently terminates stock observation for event",
                "firstCrossPolicy": "consume first price cross even if volume unavailable/nonpositive; only positive volume qualifies for Top5",
                "eventCloseHistoryRequired": True, "priceBasis": "raw-unadjusted",
                "selectionBasis": "all first crosses seen before Top5; no backfill; no positions",
                "hypothesisFamily": "existing relative-strength/repair family; not a new hypothesis family",
                "interpretation": "descriptive matched comparison, not causal proof",
                "historicalEligibilityVerified": False, "comparableRightsVerified": False,
                "certified": False, "targetAchieved": False}}
