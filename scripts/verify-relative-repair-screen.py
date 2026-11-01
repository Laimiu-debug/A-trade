"""Independent relative-repair event and account arithmetic verification.

The signal oracle below uses only the retained close/volume/amount projection.
It never imports the new signal implementation or runs an account. Existing
independent accounting checks are loaded in separate period namespaces. Source
prices, historical membership and actual market execution are not certified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from fractions import Fraction

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts/strategy-search/relative-repair-screen-v1"
REGISTERED_PLAN_SHA256 = "c0b649c81716ae32b2e3503404f2142a076dd41c05d1d409fc712250d0d252bd"
SID = "relativeRepairEventV1"
POLICIES = ("allRepaired", "repaired", "matchedControl")
PERIODS = {"2022-2023": ("2022-01-04", "2023-12-29", 484),
           "2024-2025": ("2024-01-02", "2025-12-31", 485)}
ACCOUNT_REFERENCE = "scripts/verify-market-context-screen.py"
ACCOUNT_REFERENCE_SHA256 = "f08d534351f55a6715dd1e43533f1ac914799d55ba4f1d502ca8c870fca235b1"
SUPPLEMENTAL_REFERENCE = "scripts/verify-supplemental-force-context.py"
SUPPLEMENTAL_REFERENCE_SHA256 = "d2390f1142238ae00fc3f2430eaa4b91bdd66a408a0657f496f0a6158fc05e9d"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def positive(value):
    return finite(value) and value > 0


def iso(value):
    require(type(value) is str and date.fromisoformat(value).isoformat() == value, "Noncanonical date")
    return value


def read(path):
    path = Path(path)
    with (gzip.open(path, "rt", encoding="utf-8") if path.name.endswith(".gz")
          else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def same(actual, expected, where="root"):
    """Strict structure/identities, bounded tolerance only for float arithmetic."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), where + ": keys differ")
        for key in expected:
            same(actual[key], expected[key], where + "." + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), where + ": row count differs")
        for index, (a, e) in enumerate(zip(actual, expected)):
            same(a, e, where + "[" + str(index) + "]")
    elif type(expected) is float:
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-9), where + ": numeric differs")
    else:
        require(type(actual) is type(expected) and actual == expected, where + ": value/type differs")


def local(relative):
    path = (ROOT / relative).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Missing/outside-root input: " + str(relative))
    return path


def accounting(period):
    require(period in PERIODS, "Unknown fixed account period")
    for filename, expected in ((ACCOUNT_REFERENCE, ACCOUNT_REFERENCE_SHA256),
                               (SUPPLEMENTAL_REFERENCE, SUPPLEMENTAL_REFERENCE_SHA256)):
        require(sha(local(filename)) == expected, "Frozen independent verifier changed: " + filename)
    spec = importlib.util.spec_from_file_location("_relative_account_" + period, local(ACCOUNT_REFERENCE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reference = module.isolated_account_reference(period)
    reference.SID = SID
    return module, reference


def checked_input(value, *, start, end):
    """Validate identities/order without converting unknown prices into zero."""
    require(isinstance(value, dict), "Missing signal input")
    calendar = value["calendar"]
    require(isinstance(calendar, list) and calendar and calendar == sorted(set(calendar)), "Calendar duplicate/unordered/empty")
    for day in calendar:
        iso(day)
    require(start in calendar and end in calendar, "Target interval absent from calendar")
    allowed = set(calendar)
    securities = value["securities"]
    require(isinstance(securities, list), "Missing fixed securities")
    symbols = [row["symbol"] for row in securities]
    require(symbols == sorted(set(symbols)), "Security order/identity duplicate")
    require(all(type(s) is str and len(s) == 8 and s.startswith(("sh60", "sz00")) and s[2:].isdigit()
                for s in symbols), "Non-mainboard fixed security")
    benchmark = value["benchmark"]
    require(benchmark["symbol"] == "sh000300", "Unexpected benchmark")
    maps = {}
    for security in securities + [benchmark]:
        bars = security["bars"]
        require(isinstance(bars, list), "Missing projected bar list")
        days = [iso(row["date"]) for row in bars]
        require(days == sorted(set(days)) and set(days) <= allowed, "Projected bar duplicate/order/off-calendar/future")
        require(all(set(row) == {"date", "close", "volume", "amount"} for row in bars), "Projection fields differ")
        maps[security["symbol"]] = {row["date"]: row for row in bars}
    return calendar, symbols, maps


def printable(value):
    if type(value) is float and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    return value


def market_path(calendar, benchmark, start, end):
    """First pass: index-only episodes. No stock data affects this pass."""
    events, rows, active, armed = [], [], None, True
    for index, day in enumerate(calendar):
        if not start <= day <= end:
            continue
        previous_dates = calendar[max(0, index - 20):index]
        previous_closes = [benchmark.get(d, {}).get("close") for d in previous_dates]
        price = benchmark.get(day, {}).get("close")
        peak = None
        if len(previous_dates) == 20 and all(positive(x) for x in previous_closes):
            maximum = max(previous_closes)
            peak = {"date": previous_dates[max(i for i, x in enumerate(previous_closes) if x == maximum)], "close": maximum}
        row = {"date": day, "armedBefore": armed, "eventId": active["eventId"] if active else None,
               "benchmarkClose": printable(price), "prior20Peak": peak,
               "benchmarkWindow": {"dates20": previous_dates, "closes20": [printable(x) for x in previous_closes]},
               "status": "idle", "reason": "noDrawdownTrigger", "observations": [], "firstCrosses": [],
               "selectedTop5": [], "pairs": [], "unmatched": [], "unknownCounts": {"benchmark": 0, "cross": 0, "control": 0}}
        if active:
            age = index - active["triggerIndex"]
            active["observedSessions"] = age
            row.update(age=age, status="observing", reason="activeEvent")
            ending = None
            if not positive(price):
                row.update(status="unknown", reason="benchmarkCloseUnavailable")
                row["unknownCounts"]["benchmark"] = 1
                ending = "benchmarkCloseUnavailable"
            elif price >= active["peakClose"]:
                row.update(status="terminated", reason="benchmarkRecovered")
                ending = "benchmarkRecovered"
            elif age == 40:
                ending = "observationWindowExpired"
                row["endReason"] = ending
            if ending:
                active.update(endDate=day, endReason=ending)
                active = None
        elif armed:
            if not positive(price) or peak is None:
                row.update(status="unknown", reason="benchmarkTriggerHistoryUnavailable")
                row["unknownCounts"]["benchmark"] = 1
            elif price <= .95 * peak["close"]:
                active = {"eventId": "relativeRepair:" + day, "triggerDate": day, "triggerIndex": index,
                          "triggerClose": price, "peakDate": peak["date"], "peakClose": peak["close"],
                          "observedSessions": 0, "endDate": None, "endReason": None, "censored": False}
                events.append(active)
                armed = False
                row.update(eventId=active["eventId"], status="triggered", reason="fivePercentBenchmarkDrawdown", age=0)
        if not active and not armed:
            if positive(price) and peak is not None and price > peak["close"]:
                armed = True
                row["rearmed"] = True
            elif row["status"] == "idle":
                row.update(status="waitingRearm", reason="benchmarkNotAbovePrior20High")
                if not positive(price) or peak is None:
                    row.update(status="unknown", reason="benchmarkRearmHistoryUnavailable")
                    row["unknownCounts"]["benchmark"] = 1
        row["armedAfter"] = armed
        rows.append(row)
    if active:
        active.update(endDate=rows[-1]["date"], endReason="periodCutoff", censored=True)
    return events, rows


def freeze_population(calendar, peak_index, symbols, maps):
    """Independent rank computation: sorted tie blocks and exact mean arithmetic."""
    dates = calendar[max(0, peak_index - 60):peak_index + 1]
    valid, invalid, raw = [], [], []
    for symbol in symbols:
        prices = [maps[symbol].get(d, {}).get("close") for d in dates]
        amounts = [maps[symbol].get(d, {}).get("amount") for d in dates[-20:]]
        raw.append({"symbol": symbol, "closes": [printable(x) for x in prices],
                    "amounts20": [printable(x) for x in amounts]})
        reasons = []
        if len(dates) != 61:
            reasons.append("insufficient61CalendarSlots")
        if not all(positive(x) for x in prices):
            reasons.append("missingOrInvalidClose61")
        if len(amounts) != 20 or not all(positive(x) for x in amounts):
            reasons.append("missingOrInvalidAmount20")
        if not reasons:
            change = prices[-1] / prices[0] - 1
            average = float(sum((Fraction(x) for x in amounts), Fraction()) / 20)
            if not finite(change) or not finite(average):
                reasons.append("nonfiniteDerivedFeature")
        if reasons:
            invalid.append({"symbol": symbol, "reasons": reasons})
        else:
            valid.append({"symbol": symbol, "anchorClose": prices[-1], "return60": change, "amount20": average})
    n = len(valid)
    for feature, output in (("return60", "return60Percentile"), ("amount20", "amount20Percentile")):
        ordered = sorted(valid, key=lambda r: r[feature])
        left = 0
        while left < n:
            right = left + 1
            while right < n and ordered[right][feature] == ordered[left][feature]:
                right += 1
            percentile = (left + right) / (2 * n)
            for row in ordered[left:right]:
                row[output] = percentile
            left = right
    valid.sort(key=lambda r: (-r["return60"], r["symbol"]))
    limit = math.ceil(n / 5)
    for rank, row in enumerate(valid, 1):
        row.update(return60Rank=rank, candidate=rank <= limit and row["return60"] > 0)
    return ([deepcopy(r) for r in valid if r["candidate"]],
            {"validPopulation": valid, "invalidPopulation": invalid, "validPopulationCount": n, "top20Count": limit},
            {"dates61": dates, "amountDates20": dates[-20:], "securities": raw})


def make_signal(group, event, day, symbol, rank):
    return {"signalId": f"{group}:{event}:{day}:{symbol}", "strategyId": SID,
            "symbol": symbol, "signalDate": day, "originalRank": rank}


def reference_signals(projected, *, start, end):
    """Two-pass oracle: market episodes, then each stock's earliest terminal.

    Each stock's first missing close or first adjacent upward crossing is fixed
    from its observation prefix before ranking. Later matching cannot change
    this terminal and account positions are never an input.
    """
    calendar, symbols, maps = checked_input(projected, start=start, end=end)
    events, rows = market_path(calendar, maps["sh000300"], start, end)
    dates = {d: i for i, d in enumerate(calendar)}
    schedules = {p: [] for p in POLICIES}
    freeze_inputs = {}
    for event in events:
        eid, trigger = event["eventId"], event["triggerDate"]
        frozen, population, inputs = freeze_population(calendar, dates[event["peakDate"]], symbols, maps)
        event.update(candidates=frozen, population=population, firstCrosses=[], pairs=[])
        freeze_inputs[eid] = inputs
        selected_days = [r for r in rows if r["eventId"] == eid and r["status"] == "observing"]
        trigger_row = next(r for r in rows if r["date"] == trigger)
        trigger_observations, terminals, used = [], {}, set()
        for feature in frozen:
            symbol, anchor = feature["symbol"], feature["anchorClose"]
            value = maps[symbol].get(trigger, {}).get("close")
            observed = positive(value)
            trigger_observations.append({"symbol": symbol, "close": printable(value),
                "status": "observed" if observed else "unknown",
                "reason": "triggerCloseObserved" if observed else "triggerCloseUnavailable"})
            if not observed:
                terminals[symbol] = (trigger, "unknown")
                continue
            for daily in selected_days:
                day = daily["date"]
                current = maps[symbol].get(day, {}).get("close")
                previous = maps[symbol].get(calendar[dates[day] - 1], {}).get("close")
                if not positive(current) or not positive(previous):
                    terminals[symbol] = (day, "unknown")
                    break
                if previous < anchor <= current:
                    terminals[symbol] = (day, "cross")
                    break
        event["triggerObservations"] = trigger_observations
        trigger_row["unknownCounts"]["cross"] = sum(r["status"] == "unknown" for r in trigger_observations)
        features = {f["symbol"]: f for f in frozen}
        for daily in selected_days:
            day = daily["date"]
            previous_date = calendar[dates[day] - 1]
            observations, first_features = [], []
            for feature in frozen:
                symbol, anchor = feature["symbol"], feature["anchorClose"]
                current_bar = maps[symbol].get(day, {})
                current, volume = current_bar.get("close"), current_bar.get("volume")
                previous = maps[symbol].get(previous_date, {}).get("close")
                terminal_day, terminal_kind = terminals.get(symbol, (None, None))
                unknown = terminal_kind == "unknown" and terminal_day <= day
                seen_before = terminal_kind == "cross" and terminal_day < day
                seen_today = terminal_kind == "cross" and terminal_day <= day
                observation = {"symbol": symbol, "previousDate": previous_date, "previousClose": printable(previous),
                    "close": printable(current), "volume": printable(volume), "anchorClose": anchor,
                    "seenBefore": seen_before, "controlUsedBefore": symbol in used}
                if unknown:
                    state, reason = "unknown", ("adjacentCloseUnavailable" if terminal_day == day else "eventCloseHistoryTerminated")
                elif seen_before:
                    state, reason = "excluded", "firstCrossAlreadySeen"
                elif terminal_kind == "cross" and terminal_day == day:
                    first_features.append(feature)
                    observation["firstPriceCross"] = True
                    if positive(volume):
                        state, reason = "eligibleFirstCross", "adjacentPositiveVolumeRepair"
                    else:
                        state, reason = ("excluded", "nonpositiveVolume") if finite(volume) else ("unknown", "volumeUnavailable")
                else:
                    state, reason = "excluded", "noUpwardCross"
                observation.update(crossStatus=state, crossReason=reason)
                if unknown:
                    control = "unknown", "eventCloseHistoryTerminated"
                elif seen_today:
                    control = "excluded", "firstCrossSeen"
                elif symbol in used:
                    control = "excluded", "controlAlreadyUsed"
                elif not positive(current):
                    control = "unknown", "currentCloseUnavailable"
                elif current >= anchor:
                    control = "excluded", "notBelowAnchor"
                elif not positive(volume):
                    control = ("excluded", "nonpositiveVolume") if finite(volume) else ("unknown", "volumeUnavailable")
                else:
                    control = "eligible", "belowAnchorUnusedAndUnseen"
                observation.update(controlStatus=control[0], controlReason=control[1])
                observations.append(observation)
            first_features.sort(key=lambda r: (-r["amount20"], r["symbol"]))
            observed = {r["symbol"]: r for r in observations}
            qualified = [f for f in first_features if observed[f["symbol"]]["crossStatus"] == "eligibleFirstCross"]
            ranks = {f["symbol"]: i + 1 for i, f in enumerate(qualified)}
            first_crosses = [{"symbol": f["symbol"], "date": day, "rank": ranks.get(f["symbol"]),
                "amount20": f["amount20"], "selected": f["symbol"] in ranks and ranks[f["symbol"]] <= 5,
                "status": observed[f["symbol"]]["crossStatus"], "reason": observed[f["symbol"]]["crossReason"]}
                for f in first_features]
            daily.update(observations=observations, firstCrosses=first_crosses,
                         selectedTop5=[deepcopy(r) for r in first_crosses if r["selected"]])
            daily["unknownCounts"]["cross"] = sum(r["crossStatus"] == "unknown" for r in observations)
            daily["unknownCounts"]["control"] = sum(r["controlStatus"] == "unknown" for r in observations)
            event["firstCrosses"].extend(deepcopy(first_crosses))
            controls = {r["symbol"] for r in observations if r["controlStatus"] == "eligible"}
            for rank, repaired in enumerate(qualified[:5], 1):
                schedules["allRepaired"].append(make_signal("allRepaired", eid, day, repaired["symbol"], rank))
                distances = sorted(({"symbol": s, "distance":
                    abs(repaired["return60Percentile"] - features[s]["return60Percentile"]) +
                    abs(repaired["amount20Percentile"] - features[s]["amount20Percentile"])} for s in controls),
                    key=lambda r: (r["distance"], r["symbol"]))
                if not distances:
                    daily["unmatched"].append({"symbol": repaired["symbol"], "originalRank": rank, "reason": "noEligibleControl"})
                    continue
                picked = distances[0]
                pair = {"eventId": eid, "date": day, "originalRank": rank, "repairedSymbol": repaired["symbol"],
                    "controlSymbol": picked["symbol"], "distance": picked["distance"], "eligibleControls": distances}
                daily["pairs"].append(pair)
                event["pairs"].append(deepcopy(pair))
                used.add(picked["symbol"])
                controls.remove(picked["symbol"])
                for policy, symbol in (("repaired", repaired["symbol"]), ("matchedControl", picked["symbol"])):
                    schedules[policy].append(make_signal(policy, eid, day, symbol, rank))
        event.update(seenSymbols=sorted(s for s, (_d, kind) in terminals.items() if kind == "cross"),
                     usedControlSymbols=sorted(used),
                     unknownSymbols={s: d for s, (d, kind) in terminals.items() if kind == "unknown"})
    return {"events": events, "dailyEvidence": rows, "evidence": {"freezeInputs": freeze_inputs}, "schedules": schedules}


def verify_close_evidence(result, projected):
    """Use the retained signal projection for close-price checks, not open fills."""
    maps = {s["symbol"]: {b["date"]: b for b in s["bars"]} for s in projected["securities"]}
    observed = 0
    for row in result["research"]["opportunities"]:
        if row["plannedQuantity"] is None:
            continue
        bar = maps[row["symbol"]].get(row["signalDate"], {})
        require(positive(bar.get("close")), "Planned entry has no valid signal-day projected close")
        same(row["reservePrice"], bar["close"] * 1.001, "signal-close-reserve")
        observed += 1
    for row in result["trades"]:
        if row["side"] != "sell" or row["reason"] not in ("stopLoss", "trailingStop", "noProgress5"):
            continue
        evidence = row["riskEvidence"]
        bar = maps[row["symbol"]].get(row["signalDate"], {})
        require(positive(bar.get("close")), "Risk exit has no valid projected close")
        same(evidence["observedClose"], bar["close"], "risk-close")
    return {"signalCloseReserves": observed, "openingPriceRecollected": False}


def verify_config(config, planned, period):
    same(config, planned, "account-config")
    start, end, _ = PERIODS[period]
    require(config["startDate"] == start and config["endDate"] == end, "Account period differs")
    for field, expected in {"initialCapital": 500000., "maxPositions": 5, "maxStockWeight": .2,
                           "slippageBps": 10., "stopLossPct": .05, "trailingStopPct": .1,
                           "takeProfitPct": 0., "maxHoldingDays": 0, "commissionRate": .0003,
                           "minCommission": 5., "transferFeeRate": .00001, "maxParticipationRate": .01,
                           "rebalanceEvery": 1}.items():
        require(finite(config[field]) and config[field] == expected, "Account setting differs: " + field)
    require(config["stampDutyRate"] is None and config["rebalanceMode"] == "risk", "Fee/cadence differs")
    require(len(config["strategies"]) == 1 and config["strategies"][0]["id"] == SID and
            config["allocation"] == {"mode": "single", "strategyIds": [SID]}, "Account strategy differs")


def verify_signal_evidence(projected, actual, *, start, end):
    expected = reference_signals(projected, start=start, end=end)
    require(set(actual) == {"version", "schedules", "events", "dailyEvidence", "evidence", "metadata"}, "Signal evidence root keys differ")
    require(actual["version"] == "relative-repair-event-v1", "Unexpected signal evidence version")
    for field in expected:
        same(actual[field], expected[field], field)
    metadata = actual["metadata"]
    counts = {"strategyId": SID, "start": start, "end": end,
        "targetSessions": len(expected["dailyEvidence"]), "universeCount": len(projected["securities"]),
        "eventCount": len(expected["events"]), "pairedSignals": len(expected["schedules"]["repaired"]),
        "allRepairedSignals": len(expected["schedules"]["allRepaired"])}
    for field, value in counts.items():
        same(metadata[field], value, "metadata." + field)
    for field, value in {"initialState": "flatAndArmed", "eventWarmup": "none; features may use pre-period raw observations",
        "percentileBasis": "(strictly-less + 0.5 * equal) / all-valid-population-N",
        "rankingBasis": "return60 descending then symbol; ceil(N*0.2), positive return only",
        "amountWindow": "20 calendar slots ending at peak, inclusive", "eventCloseHistoryRequired": True,
        "priceBasis": "raw-unadjusted", "interpretation": "descriptive matched comparison, not causal proof",
        "historicalEligibilityVerified": False, "comparableRightsVerified": False,
        "certified": False, "targetAchieved": False}.items():
        same(metadata[field], value, "metadata." + field)
    details = {"events": len(expected["events"]), "targetSessions": len(expected["dailyEvidence"]),
        "frozenPopulationRows": sum(len(e["population"]["validPopulation"]) + len(e["population"]["invalidPopulation"]) for e in expected["events"]),
        "frozenCandidates": sum(len(e["candidates"]) for e in expected["events"]),
        "priceCrosses": sum(len(e["firstCrosses"]) for e in expected["events"]),
        "eventEndReasons": dict(Counter(e["endReason"] for e in expected["events"])),
        "signals": {p: len(rows) for p, rows in expected["schedules"].items()},
        "unmatchedTop5": sum(len(r["unmatched"]) for r in expected["dailyEvidence"]),
        "matching": "event-local covariate nearest-neighbour, not causal or guaranteed paired fills"}
    return expected["schedules"], details


def verify_comparison(row, summary, count, period, policy, reference):
    require(row["period"] == period and row["id"] == policy and row["signals"] == count, "Comparison identity/count differs")
    require("phases" not in row, "Retrospective labels introduced into comparison")
    require(row["closed"] == summary["closedEpisodes"]["count"] and row["openPositions"] == summary["openPositions"]["count"], "Comparison episode counts differ")
    fields = {"accountReturnPct": summary["account"]["netReturnPct"], "maxDrawdownPct": summary["account"]["maxDrawdownPct"],
        "meanExposurePct": summary["periods"][0]["exposurePct"]["mean"], "winRatePct": summary["closedEpisodes"]["winRatePct"],
        "meanWinPct": summary["closedEpisodes"]["meanWinReturnPct"], "meanLossPct": summary["closedEpisodes"]["meanLossReturnPct"],
        "closedMeanPct": summary["closedEpisodes"]["meanReturnPct"], "payoffRatio": summary["closedEpisodes"]["payoffRatio"],
        "profitFactor": summary["closedEpisodes"]["profitFactor"], "top5ShareOfGrossProfitPct": summary["concentration"]["top5ProfitSharePct"]}
    for field, expected in fields.items():
        reference.match_number(row[field], expected, "Comparison " + field)
    same(row["holdingSessions"], summary["closedEpisodes"]["holdingSessions"], "comparison.holdingSessions")
    same(row["maxConsecutiveLosses"], summary["closedEpisodes"]["maxConsecutiveLosses"], "comparison.lossStreak")
    require(row["unreliableValuationDays"] == len(summary["quality"]["valuationUnreliableDates"]), "Comparison valuation caveat omitted")
    years = [p for p in summary["periods"] if p["kind"] == "calendarYear"]
    require([p["id"] for p in row["years"]] == [p["id"] for p in years], "Comparison year ordering differs")
    for actual, expected in zip(row["years"], years):
        require(actual["closed"] == expected["closedEpisodes"]["count"], "Comparison yearly count differs")
        same(actual["accountReturnPct"], expected["returnPct"], "comparison.yearReturn")
        same(actual["maxDrawdownPct"], expected["maxDrawdownPct"], "comparison.yearDD")


def verify_artifacts(output, *, progress=None):
    output = Path(output)
    emit = progress or (lambda _row: None)
    verifier_sha = sha(__file__)
    require(not (output / "failure.json").exists(), "Runner recorded failure")
    require(sha(output / "plan.json") == REGISTERED_PLAN_SHA256, "Plan differs from the pre-run registered identity")
    plan, comparison = read(output / "plan.json"), read(output / "comparison.json")
    require(plan["schema"] == "relative-repair-screen-v1" and comparison["schema"] == "relative-repair-comparison-v1", "Experiment schema differs")
    require(plan["strategyId"] == SID and list(plan["periods"]) == list(PERIODS), "Experiment identity/period order differs")
    for field, expected in {"newEntryVariants": 1, "matchedSubsetDiagnostics": 1, "controlVariants": 1,
        "configurationsNewlyExecuted": 6, "parameterSearch": False, "profitHurdles": None,
        "retrospectivePhaseLabelsUsed": False, "costStressNewlyExecuted": False,
        "certified": False, "targetAchieved": False}.items():
        same(plan[field], expected, "plan." + field)
    require(comparison["certified"] is False and comparison["targetAchieved"] is False and comparison["profitHurdles"] is None, "Exploratory result promoted to certification")
    same(comparison["limitations"], plan["limitations"], "comparison.limitations")
    require(comparison["planSha256"] == sha(output / "plan.json"), "Comparison plan hash differs")
    require(len(comparison["rows"]) == 6 and {(r["period"], r["id"]) for r in comparison["rows"]} ==
            {(period, policy) for period in PERIODS for policy in POLICIES}, "Missing/duplicate/foreign account comparison")
    code_pins = plan["sourcePins"]
    require(all(sha(local(p)) == expected for p, expected in code_pins.items()), "Registered source changed")
    known_files = {"plan.json": sha(output / "plan.json"), "comparison.json": sha(output / "comparison.json")}
    frozen_inputs, accounts, signal_checks = {}, [], {}
    for period, (start, end, count) in PERIODS.items():
        emit({"phase": "verify-relative-signals", "period": period})
        account_module, reference = accounting(period)
        record = plan["periods"][period]
        require("not pristine out of sample" in record["sampleClaim"], "Historical use limitation omitted")
        for item in record["baselineFiles"]:
            path = local(item["path"])
            require(sha(path) == item["sha256"], "Pinned upstream file changed: " + item["path"])
            frozen_inputs[item["path"]] = item["sha256"]
        source_refs = [local(r["path"]) for r in record["baselineFiles"] if Path(r["path"]).name == "source.json"]
        result_refs = [local(r["path"]) for r in record["baselineFiles"] if r["path"].endswith(".result.json.gz")]
        manifests = [local(r["path"]) for r in record["baselineFiles"] if Path(r["path"]).name == "manifest.json"]
        require(len(source_refs) == len(result_refs) == len(manifests) == 1, "Ambiguous upstream source/config/manifest")
        original_config = deepcopy(read(result_refs[0])["config"])
        original_config["strategies"][0]["id"] = SID
        original_config["allocation"]["strategyIds"] = [SID]
        same(record["config"], original_config, "configuration-inheritance")
        folder = output / period
        required_files = {"source.json", "signal-input.json.gz", "projection-binding.json", "signals-evidence.json.gz"}
        required_files.update(policy + ending for policy in POLICIES for ending in (".signals.json", ".result.json.gz", ".summary.json"))
        require({p.name for p in folder.iterdir()} == required_files, "Period artifact inventory differs")
        artifact_hashes = {name: sha(folder / name) for name in sorted(required_files)}
        same(comparison["artifacts"][period], artifact_hashes, "comparison.artifacts." + period)
        known_files.update({period + "/" + name: value for name, value in artifact_hashes.items()})
        source = read(folder / "source.json")
        same(source, record["sourceIdentity"], "source-plan")
        same(source, read(source_refs[0]), "source-baseline")
        projected = read(folder / "signal-input.json.gz")
        projection = read(folder / "projection-binding.json")
        same(projection["sourceIdentity"], source, "projection.sourceIdentity")
        require(projection["signalInputSha256"] == artifact_hashes["signal-input.json.gz"] and
                projection["calendarSha256"] == digest(projected["calendar"]), "Projection file/calendar binding differs")
        source_fp = source.get("sourceSnapshotFingerprint", source.get("sourceDatasetFingerprint"))
        require(projected["sourceDatasetFingerprint"] == source_fp and
                projected["priceBasis"] == "raw-unadjusted;no-future-reference-normalization", "Projection raw price/source identity differs")
        calendar, symbols, _maps = checked_input(projected, start=start, end=end)
        del _maps
        manifest_inputs = read(manifests[0])["inputs"]
        require(symbols == manifest_inputs["symbols"] and len(symbols) == projection["securityCount"] == 3141,
                "Projected fixed population changed")
        require(sum(len(s["bars"]) for s in projected["securities"]) == projection["barCount"], "Projection bar count differs")
        require(calendar[-1] == end, "Signal projection contains post-cutoff dates")
        saved_calendar, target = account_module.target_calendar(source, period, calendar)
        require(calendar == saved_calendar and len(target) == count, "Account projection calendar differs from saved dates")
        source_calendar_path = source["calendarEvidence"]["path"]
        frozen_inputs[source_calendar_path] = source["calendarEvidence"]["sha256"]
        signal_evidence = read(folder / "signals-evidence.json.gz")
        schedules, details = verify_signal_evidence(projected, signal_evidence, start=start, end=end)
        signal_checks[period] = details
        del signal_evidence
        for policy in POLICIES:
            emit({"phase": "verify-relative-account", "period": period, "id": policy})
            schedule = read(folder / (policy + ".signals.json"))
            same(schedule, schedules[policy], policy + ".signals")
            reference.checked_schedule(schedule)
            result = read(folder / (policy + ".result.json.gz"))
            summary = read(folder / (policy + ".summary.json"))
            verify_config(result["config"], record["config"], period)
            require(summary["policyId"] == policy, "Summary policy differs")
            checks = account_module.verify_account(result, summary, schedule, period=period, reference=reference,
                source=source, target=target, calendar=calendar)
            checks["projectedCloses"] = verify_close_evidence(result, projected)
            row = next(r for r in comparison["rows"] if r["period"] == period and r["id"] == policy)
            verify_comparison(row, summary, len(schedule), period, policy, reference)
            accounts.append({"period": period, "id": policy, "status": "passed", **checks})
            del result, summary
        del projected
    require(all(sha(output / name) == expected for name, expected in known_files.items()), "Output changed during verification")
    require(all(sha(local(name)) == expected for name, expected in frozen_inputs.items()), "Upstream input changed during verification")
    require(all(sha(local(name)) == expected for name, expected in code_pins.items()), "Source changed during verification")
    require(sha(__file__) == verifier_sha and sha(local(ACCOUNT_REFERENCE)) == ACCOUNT_REFERENCE_SHA256 and
            sha(local(SUPPLEMENTAL_REFERENCE)) == SUPPLEMENTAL_REFERENCE_SHA256, "Independent verifier changed during verification")
    return {"schema": "relative-repair-independent-validation-v1", "status": "passed", "passed": True,
        "failureCount": 0, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "verifierSha256": verifier_sha, "planSha256": REGISTERED_PLAN_SHA256,
        "reusedVerifiers": {ACCOUNT_REFERENCE: ACCOUNT_REFERENCE_SHA256,
        SUPPLEMENTAL_REFERENCE: SUPPLEMENTAL_REFERENCE_SHA256}, "inputArtifacts": known_files,
        "frozenInputs": frozen_inputs, "sourcePins": code_pins, "signals": signal_checks, "accounts": accounts,
        "isolatedReferenceSettings": {p: {"START": v[0], "END": v[1], "SID": SID} for p, v in PERIODS.items()},
        "scope": {"newSignalImplementationImported": False, "eventsAndMatchingIndependentlyRecomputed": True,
            "frozenStrengthAndRepairFromProjectedPricesRecomputed": True, "sourcePriceTruthCertified": False,
            "fullOriginalRawReloaded": False, "openingPriceRecollected": False,
            "accountEngineRerun": False, "separatePeriodReferenceNamespaces": True},
        "limits": ["Signal mathematics uses the retained raw projection; its original-price and universe provenance inherits the pinned loader, not a new physical source audit.",
            "Cash, holdings, dated fees, T+1, reserves, exit evidence and summaries are checked with frozen independent arithmetic; actual opening liquidity and all execution gates are not independently reconstructed.",
            "Controls match only two frozen covariates without a distance cap; independent account fills need not remain paired.",
            "Six separate 500000 accounts on already-used history; no causal leadership, participant identity, pristine holdout or future profitability claim."],
        "certified": False, "targetAchieved": False}


def self_test():
    """Hand-expected synthetic cases, no production imports or market reads."""
    passed = []

    def fixture(n=50, sessions=150):
        days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
        benchmark = [{"date": d, "close": 100 if i < 80 else 95 if i == 80 else 96,
                      "volume": 100, "amount": 1000} for i, d in enumerate(days)]
        stocks = []
        for s in range(n):
            anchor = 100 + s
            gain = 1 - s / 100 if s < math.ceil(n / 5) else .2 - s / 1000
            stocks.append({"symbol": "sh" + str(600001 + s), "bars": [
                {"date": d, "close": anchor / (1 + gain) if i < 79 else anchor if i == 79 else .9 * anchor,
                 "volume": 100, "amount": (n - s) * 1000} for i, d in enumerate(days)]})
        return {"calendar": days, "benchmark": {"symbol": "sh000300", "bars": benchmark}, "securities": stocks}

    def setbar(data, s, i, **values):
        security = data["benchmark"] if s is None else data["securities"][s]
        next(row for row in security["bars"] if row["date"] == data["calendar"][i]).update(values)

    def replay(data, end=120):
        return reference_signals(data, start=data["calendar"][80], end=data["calendar"][end])

    def rejects(call):
        try:
            call()
        except (ValueError, KeyError, TypeError):
            return
        raise AssertionError("Malformed/tampered case accepted")

    data = fixture()
    setbar(data, 0, 81, close=100)
    out = replay(data)
    require(out["events"][0]["peakDate"] == data["calendar"][79], "Latest tied peak not frozen")
    require(len(out["events"][0]["candidates"]) == 10, "Top fifth denominator differs")
    require([len(out["schedules"][p]) for p in POLICIES] == [1, 1, 1], "Known nonempty schedule differs")
    require(out["schedules"]["matchedControl"][0]["symbol"] == "sh600002", "Nearest matched control differs")
    require(out["events"][0]["candidates"][0]["return60Percentile"] == .99, "Percentile denominator differs")
    passed.append("nonempty-latest-peak-fixed-population-greedy-match")

    data = fixture()
    for s, volume in ((0, 0), (1, None), (2, -1), (3, 100)):
        setbar(data, s, 81, close=100 + s, volume=volume)
        setbar(data, s, 83, close=100 + s, volume=100)
    out = replay(data)
    require([s["symbol"] for s in out["schedules"]["allRepaired"]] == ["sh600004"], "Consumed volume failures reappear")
    require(out["events"][0]["seenSymbols"] == ["sh60000" + str(s) for s in range(1, 5)], "Price crossings not all consumed")
    passed.append("zero-missing-negative-volume-consumed-before-ranking")

    data = fixture(35)
    for s in range(6):
        setbar(data, s, 81, close=100 + s)
    out = replay(data)
    require([len(out["schedules"][p]) for p in POLICIES] == [5, 1, 1], "Matching shortage changed Top5")
    require(len(out["dailyEvidence"][1]["unmatched"]) == 4, "Unmatched slots omitted")
    require(out["schedules"]["repaired"][0]["originalRank"] == 1, "Pair rank compressed")
    passed.append("all-top5-preserved-no-matching-backfill")

    data = fixture()
    setbar(data, 0, 120, close=100)
    out = replay(data)
    require(len(out["schedules"]["allRepaired"]) == 1 and out["events"][0]["endReason"] == "observationWindowExpired", "Fortieth session excluded")
    setbar(data, None, 120, close=100)
    out = replay(data)
    require(not out["schedules"]["allRepaired"] and out["events"][0]["endReason"] == "benchmarkRecovered", "Stock tested before index recovery")
    passed.append("day40-final-observation-and-index-termination-priority")

    data = fixture()
    setbar(data, None, 81, close=None)
    setbar(data, 0, 81, close=100)
    out = replay(data)
    require(len(out["events"]) == 1 and out["events"][0]["endReason"] == "benchmarkCloseUnavailable", "Missing active index not terminal")
    require(not out["schedules"]["allRepaired"] and not any(r.get("rearmed") for r in out["dailyEvidence"]), "Unknown/equal high rearmed")
    setbar(data, None, 103, close=97)
    setbar(data, None, 104, close=90)
    out = replay(data)
    require(len(out["events"]) == 2 and out["events"][1]["triggerDate"] == data["calendar"][104], "Strict local high failed to rearm later event")
    passed.append("missing-index-terminal-strict-rearm-no-same-day-trigger")

    data = fixture()
    setbar(data, 0, 80, close=None)
    setbar(data, 0, 82, close=100)
    setbar(data, 1, 81, close=101)
    setbar(data, 1, 82, close=None)
    out = replay(data)
    require(out["events"][0]["unknownSymbols"] == {"sh600001": data["calendar"][80]}, "Unknown before/after consumed crossing conflated")
    require([r["symbol"] for r in out["schedules"]["allRepaired"]] == ["sh600002"], "Missing trigger close bridged")
    passed.append("missing-stock-persistent-only-until-first-cross")

    data = fixture()
    for i in (80, 81, 83):
        setbar(data, 0, i, close=100)
    before = replay(data)
    require([r["signalDate"] for r in before["schedules"]["allRepaired"]] == [data["calendar"][83]], "Trigger-day above anchor treated retroactively")
    changed = deepcopy(data)
    for row in changed["benchmark"]["bars"] + changed["securities"][0]["bars"]:
        if row["date"] > changed["calendar"][120]:
            row.update(close=1e9, amount=1e12)
    same(replay(changed), before, "future-independence")
    passed.append("trigger-above-anchor-and-future-append-invariance")

    data = fixture(0)
    out = replay(data)
    require(len(out["events"]) == 1 and not any(out["schedules"].values()), "Empty frozen population drops index event")
    data = fixture(1)
    setbar(data, 0, 81, close=100)
    out = replay(data)
    require(out["events"][0]["candidates"][0]["return60Percentile"] == .5 and
            [len(out["schedules"][p]) for p in POLICIES] == [1, 0, 0], "Single-name denominator/control fabricated")
    passed.append("empty-and-single-valid-population-no-fictional-controls")

    data = fixture()
    before = deepcopy(data)
    replay(data)
    same(data, before, "source-immutable")
    broken = deepcopy(data)
    broken["securities"][0]["bars"].append(deepcopy(broken["securities"][0]["bars"][0]))
    rejects(lambda: replay(broken))
    broken = deepcopy(data)
    broken["calendar"][80], broken["calendar"][81] = broken["calendar"][81], broken["calendar"][80]
    rejects(lambda: replay(broken))
    rejects(lambda: same({"count": True}, {"count": 1}, "bool-count"))
    rejected = deepcopy(out)
    rejected["schedules"]["allRepaired"] = []
    rejects(lambda: same(rejected, out, "tampered-schedule"))
    passed.append("identity-order-immutability-and-semantic-tampering")
    return {"passed": True, "testsRun": len(passed), "uniqueTestIds": passed, "failures": 0,
            "productionSignalImports": False, "marketDataRead": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return 0
    destination = args.output / "validation.json"
    require(not destination.exists(), "Successful receipt already exists; do not overwrite")
    try:
        result = verify_artifacts(args.output, progress=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
        result["selfTests"] = self_test()
    except Exception as error:
        result = {"schema": "relative-repair-independent-validation-v1", "status": "failed", "passed": False,
                  "failureCount": 1, "verifiedAt": datetime.now(timezone.utc).isoformat(),
                  "error": {"type": type(error).__name__, "message": str(error)}, "verifierSha256": sha(__file__),
                  "certified": False, "targetAchieved": False}
        name = "validation-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        with (args.output / name).open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        raise
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "accounts": len(result["accounts"]),
                      "receipt": str(destination), "sha256": sha(destination)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

