"""Independent arithmetic and causal-source checks for market-context screening.

No strategy, account, fee-policy or filter implementation is imported. Selected
pure accounting checks from the prior independent verifier are loaded into a
fresh, period-specific module namespace; its source is never changed. Reads
completed, bound artifacts and creates only a new validation.json receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ("breadthMajority", "breadthImproving", "strongCohort", "preBreakoutPressure")
PERIODS = {"2022-2023": ("2022-01-04", "2023-12-29", "dated-transfer-assumption-v1"),
           "2024-2025": ("2024-01-02", "2025-12-31", "legacy-engine-delegate-v1")}
DEFAULT_OUT = ROOT / "artifacts/strategy-search/market-context-screen-v1"
PRIOR_VERIFIER = ROOT / "scripts/verify-supplemental-force-context.py"
PRIOR_VERIFIER_SHA256 = "d2390f1142238ae00fc3f2430eaa4b91bdd66a408a0657f496f0a6158fc05e9d"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def near(actual, expected, message):
    require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), message)


def iso(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, "Noncanonical date")
    return value


def bounded(value, low, high):
    return finite(value) and low <= value <= high


def nonnegative_count(value):
    return type(value) is int and value >= 0


def result_gate(state, values, reason):
    return {"status": state, "inputs": values, "reason": reason}


def isolated_account_reference(period):
    """Load old checks without mutating a source file or a shared module object."""
    require(period in PERIODS, "Unknown fixed period")
    require(hashlib.sha256(PRIOR_VERIFIER.read_bytes()).hexdigest() == PRIOR_VERIFIER_SHA256,
            "Prior independent verifier changed")
    spec = importlib.util.spec_from_file_location("_market_account_reference_" + period, PRIOR_VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.START, module.END = PERIODS[period][:2]
    return module


def reference_market_gates(row, calendar, index):
    """Calculate A/B/C from frozen panel fields, without outcome inspection."""
    require(calendar == sorted(set(calendar)) and 0 <= index < len(calendar), "Invalid target calendar/index")
    require(row["date"] == calendar[index] and row["featureVersion"] == "market-state-observations-v1",
            "Market row identity differs")
    require(row["fixedPopulation"] == 3141, "Market fixed survivor population changed")
    price, paired, cohort = (row.get(k, {}) for k in ("price20", "matched5", "yesterdayCohort"))
    n, positive, breadth = (price.get(k) for k in ("evaluableCount", "positiveCount", "positiveReturn20Breadth"))
    a_values = {k: price.get(k) for k in ("evaluableCount", "positiveCount", "unavailableCount", "coverage", "positiveReturn20Breadth")}
    if not nonnegative_count(n) or not nonnegative_count(positive) or not n:
        a = result_gate("unknown", a_values, "requiredBreadthEvidenceUnavailable")
    else:
        require(0 <= positive <= n <= 3141, "Impossible price breadth counts")
        if breadth is not None:
            near(breadth, positive / n, "Stored breadth differs from positive/evaluable population")
        a = result_gate("included" if 2 * positive > n else "excluded", a_values, "positive20Majority")
    expected_earlier = calendar[index - 5] if index >= 5 else None
    require(paired.get("earlierDate") == expected_earlier, "Matched breadth does not use exact fifth earlier target date")
    b_values = {k: paired.get(k) for k in ("available", "earlierDate", "pairedCount", "pairedCoverageFixed",
                                         "positiveBreadthToday", "positiveBreadthEarlier", "matchedBreadthChange5")}
    change, pair_n = paired.get("matchedBreadthChange5"), paired.get("pairedCount")
    if index < 5:
        require(paired.get("available") is False, "Pre-target matched comparison fabricated")
    if (index < 5 or paired.get("available") is not True or not nonnegative_count(pair_n) or pair_n == 0
            or not bounded(change, -100, 100)):
        b = result_gate("unknown", b_values, "requiredMatchedComparisonUnavailable")
    else:
        today, earlier = paired.get("positiveBreadthToday"), paired.get("positiveBreadthEarlier")
        require(pair_n <= 3141, "Impossible matched population")
        if not bounded(today, 0, 1) or not bounded(earlier, 0, 1):
            b = result_gate("unknown", b_values, "requiredMatchedEndpointUnavailable")
        else:
            near(change, 100 * (today - earlier), "Matched change is not a same-population percentage-point difference")
            b = result_gate("included" if change > 0 else "excluded", b_values, "positiveMatchedBreadthChange")
    expected_previous = calendar[index - 1] if index else None
    require(cohort.get("previousDate") == expected_previous, "Cohort does not use exact previous target date")
    c_values = {k: cohort.get(k) for k in ("previousCohortAvailable", "previousDate", "eligibleCount",
                                         "continuedCount", "observedFailureCount", "unknownCount", "observedCount")}
    cohort_n, continued, failures, unknown = (cohort.get(k) for k in
                                             ("eligibleCount", "continuedCount", "observedFailureCount", "unknownCount"))
    if index == 0:
        require(cohort.get("previousCohortAvailable") is False, "Pre-target role history fabricated")
    if (cohort.get("previousCohortAvailable") is not True
            or not all(nonnegative_count(v) for v in (cohort_n, continued, failures, unknown))):
        c = result_gate("unknown", c_values, "priorCohortEvidenceUnavailable")
    else:
        require(continued + failures + unknown == cohort_n <= 3141, "Prior cohort counts do not conserve membership")
        require(cohort.get("observedCount") == continued + failures, "Observed cohort excludes/absorbs wrong states")
        if cohort_n == 0:
            c = result_gate("excluded", c_values, "emptyPriorCohort")
        elif unknown > 0:
            c = result_gate("unknown", c_values, "priorCohortHasUnknown")
        else:
            c = result_gate("included" if continued > failures else "excluded", c_values, "continuedGreaterThanFailure")
    return dict(zip(FILTERS[:3], (a, b, c)))


def reference_pressure_window(bars, slots):
    """Independent pressure5/20 formula over at most 40 fixed calendar slots.

    Missing/invalid bars remain dependent unknown values. No observed-bar
    compression, price filling, amount weighting, or participant inference.
    """
    require(slots == sorted(set(slots)) and 0 < len(slots) <= 40, "Invalid pressure calendar slots")
    require(isinstance(bars, list), "Pressure raw evidence must be a bar list")
    dates = [iso(b["date"]) for b in bars]
    require(dates == sorted(set(dates)) and set(dates) <= set(slots), "Pressure bars outside fixed window/duplicate")
    by_date = {b["date"]: b for b in bars}
    aligned = [by_date.get(d) for d in slots]
    def valid(b):
        return (isinstance(b, dict) and all(finite(b.get(k)) and b[k] > 0 for k in ("open", "high", "low", "close", "volume"))
                and b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"])
    answer = {}
    for window in (5, 20):
        components = []
        for i in range(len(slots) - window, len(slots)):
            if i < 20 or not all(valid(b) for b in aligned[i - 20:i + 1]):
                components = []
                break
            bar, previous = aligned[i], aligned[i - 1]
            true_range = max(bar["high"], previous["close"]) - min(bar["low"], previous["close"])
            pressure = (bar["close"] - previous["close"]) / true_range if true_range else 0.
            volumes = [b["volume"] for b in aligned[i - 20:i]]
            scale = max(volumes)
            previous_mean = scale * (math.fsum(v / scale for v in volumes) / 20)
            weight = min(bar["volume"] / previous_mean, 3.)
            components.append((pressure, weight))
        answer["pressure" + str(window)] = (math.fsum(p * w for p, w in components) / math.fsum(w for _, w in components)
                                              if len(components) == window else None)
    return {**answer, "asOfDate": slots[-1], "windowStart": slots[0], "windowEnd": slots[-1],
            "windowSlots": len(slots), "missingOrInvalidSlots": [d for d, b in zip(slots, aligned) if not valid(b)]}


def reference_prior_pressure(signal, observation, calendar, raw_window=None):
    require(calendar == sorted(set(calendar)) and signal["signalDate"] in calendar, "Signal absent from independent calendar")
    index = calendar.index(signal["signalDate"])
    previous = calendar[index - 1] if index else None
    if observation is None or previous is None:
        return result_gate("unknown", {"previousCalendarDate": previous}, "priorPressureUnavailable")
    require(observation.get("asOfDate") == observation.get("previousCalendarDate") == previous,
            "Prior pressure is not at the exact preceding exchange session")
    p5, p20 = observation.get("pressure5"), observation.get("pressure20")
    if raw_window is not None:
        slots = calendar[max(0, index - 40):index]
        recomputed = reference_pressure_window(raw_window, slots)
        for field in ("pressure5", "pressure20"):
            if recomputed[field] is None:
                require(observation.get(field) is None, "Missing pressure input was filled/coerced")
            else:
                near(observation.get(field), recomputed[field], "Prior pressure differs from independent raw-window arithmetic")
    values = {"asOfDate": observation.get("asOfDate"), "previousCalendarDate": previous, "pressure5": p5, "pressure20": p20}
    if not all(bounded(v, -1, 1) for v in (p5, p20)):
        return result_gate("unknown", values, "priorPressureNumericUnknown")
    return result_gate("included" if p20 > 0 and p5 > p20 else "excluded", values, "positiveStrengtheningPriorPressure")


def reference_fee(amount, side, effective_date, profile_id):
    require(finite(amount) and amount >= 0 and side in ("buy", "sell"), "Invalid fee notional/side")
    iso(effective_date)
    if profile_id == "dated-transfer-assumption-v1":
        require("2022-01-01" <= effective_date <= "2023-12-31", "Dated fee used outside frozen supported dates")
        transfer_rate = .00002 if effective_date < "2022-04-29" else .00001
    else:
        require(profile_id == "legacy-engine-delegate-v1" and "2024-01-01" <= effective_date <= "2025-12-31",
                "Wrong fee continuation/profile period")
        transfer_rate = .00001
    stamp_rate = .001 if effective_date < "2023-08-28" else .0005
    parts = {"commission": max(5., amount * .0003), "transfer": amount * transfer_rate,
             "stampDuty": amount * stamp_rate if side == "sell" else 0.}
    return {**parts, "total": sum(parts.values())}


def verify_fills(result, period):
    """Rebuild actual paid cash/share ledger with period-specific dated fees."""
    start, end, profile = PERIODS[period]
    require(result["config"]["startDate"] == start and result["config"]["endDate"] == end, "Account period differs")
    require(result["config"]["slippageBps"] == 10, "Unregistered cost stress or slippage tier")
    fee_ledger = result["research"]["feeLedger"]
    settings = fee_ledger["identity"]["settings"]
    require(settings["profileId"] == profile and settings["commissionIncludesTransfer"] is False
            and settings["stampDutyOverride"] is None, "Fee policy differs from period registration")
    near(settings["commissionRate"], .0003, "Ledger commission rate differs")
    near(settings["minCommission"], 5., "Ledger minimum commission differs")
    paid_rows, trades = fee_ledger["paidLedger"], result["trades"]
    require(len(paid_rows) == len(trades), "Paid quote count differs from fills")
    cash, fees, slippage = 500000., 0., 0.
    quantities, buys, ids, previous_day = Counter(), {}, set(), start
    for index, (row, paid) in enumerate(zip(trades, paid_rows)):
        d, symbol, side, quantity = (row[k] for k in ("date", "symbol", "side", "quantity"))
        require(start <= previous_day <= iso(d) <= end and iso(row["signalDate"]) < d, "Noncausal/out-of-period fill")
        require(row["fillId"] not in ids and type(quantity) is int and quantity > 0, "Duplicate fill/invalid quantity")
        ids.add(row["fillId"])
        previous_day = d
        price = row["openingPrice"] * (1.001 if side == "buy" else .999)
        require(finite(row["openingPrice"]) and row["openingPrice"] > 0, "Invalid raw opening price")
        near(row["price"], price, "Execution slip price differs")
        amount = quantity * price
        charge = reference_fee(amount, side, d, profile)
        slip = quantity * abs(price - row["openingPrice"])
        near(row["amount"], amount, "Fill notional differs")
        near(row["fee"], charge["total"], "Dated fill fee differs")
        near(row["slippage"], slip, "Recorded slippage differs")
        require(row["quantityBefore"] == quantities[symbol], "Share ledger mismatch before fill")
        if side == "buy":
            require(not quantities[symbol] and row["positionAction"] == "open", "Added to existing position")
            quantities[symbol] += quantity
            buys[symbol] = d
            cash -= amount + charge["total"]
        else:
            require(buys.get(symbol, end) < d and quantity <= quantities[symbol], "T+1 violation or oversell")
            quantities[symbol] -= quantity
            cash += amount - charge["total"]
        require(row["quantityAfter"] == quantities[symbol], "Share ledger mismatch after fill")
        near(row["cashAfter"], cash, "Cash ledger differs or slippage double deducted")
        require(cash >= -1e-7 and sum(q > 0 for q in quantities.values()) <= 5, "Cash/position constraint violated")
        quote = paid["quote"]
        require(paid["fillId"] == row["fillId"] and paid["tradeIndex"] == index, "Paid quote/fill link differs")
        require(paid["quoteId"] == quote["quoteId"] == row["feeQuoteId"], "Paid quote ID differs from actual fill")
        require(quote["symbol"] == symbol and quote["side"] == side, "Fee quote side/symbol differs")
        near(quote["amount"], amount, "Paid quote amount differs from fill notional")
        require(quote["context"]["effective_fee_date"] == quote["context"]["execution_date"] == d
                and quote["context"]["purpose"] == "fillQuote" and quote["context"]["signal_date"] == row["signalDate"],
                "Actual fee quoted at wrong date")
        near(quote["rates"]["commission"], .0003, "Paid commission rate label differs")
        near(quote["rates"]["transfer"], .00002 if profile == PERIODS["2022-2023"][2] and d < "2022-04-29" else .00001,
             "Paid transfer rate label differs")
        near(quote["rates"]["chargedStampDuty"], (.001 if d < "2023-08-28" else .0005) if side == "sell" else 0.,
             "Paid stamp rate label differs")
        for component, expected in charge.items():
            near(quote["breakdown"][component], expected, "Paid fee component differs: " + component)
        fees += charge["total"]
        slippage += slip
    near(fee_ledger["paidFeeTotal"], fees, "Paid ledger total differs")
    return {"fills": len(trades), "cash": cash, "fees": fees, "slippage": slippage,
            "positions": {s: q for s, q in quantities.items() if q}}


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz")
          else Path(path).open(encoding="utf-8")) as stream:
        return json.load(stream)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def content_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def bound_path(relative):
    path = (ROOT / relative).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Missing/out-of-repository input")
    return path


def target_calendar(source, period, full_calendar):
    evidence = source["calendarEvidence"]
    path = bound_path(evidence["path"])
    require(sha(path) == evidence["sha256"], "Independent calendar evidence changed")
    document = read(path)
    if period == "2022-2023":
        require(document["errorCode"] == "0" and document["fields"] == ["calendar_date", "is_trading_day"], "Calendar provider schema differs")
        dates = [d for d, status in document["rows"] if status == "1"]
    else:
        require(document["schema"] == "exchange-notice-derived-calendar-v1", "Saved calendar schema differs")
        dates = document["dates"]
    require(dates == sorted(set(dates)), "Independent calendar unordered/duplicate")
    require(full_calendar == sorted(set(full_calendar)), "Pressure source calendar unordered/duplicate")
    require([d for d in full_calendar if dates[0] <= d <= dates[-1]] == dates, "Pressure calendar compresses missing stock sessions")
    start, end, _ = PERIODS[period]
    target = [d for d in dates if start <= d <= end]
    require((target[0], target[-1], len(target)) == (start, end, 484 if period == "2022-2023" else 485), "Wrong target interval")
    return dates, target


def bound_market_rows(registration, target, source):
    binding = registration["marketBinding"]
    directory = bound_path(binding["directory"] + "/protocol.json").parent
    protocol, completion, audit = (read(directory / f) for f in ("protocol.json", "completion.json", "independent-verification.json"))
    require(audit["passed"] is True and audit["failureCount"] == 0, "Frozen market audit did not pass")
    require(content_sha({k: v for k, v in protocol.items() if k != "protocolHash"}) == protocol["protocolHash"], "Market protocol content hash differs")
    require(protocol["protocolHash"] == binding["protocolHash"] == completion["protocolHash"] == audit["protocolHash"], "Market protocol linkage differs")
    require(protocol["dates"] == target and len(protocol["symbols"]) == 3141, "Market population/target calendar differs")
    require(sha(directory / "daily.jsonl.gz") == completion["files"]["daily.jsonl.gz"]["sha256"]
            == audit["resultFileHashes"]["daily.jsonl.gz"], "Frozen daily bytes differ from completion/audit")
    require(protocol["registration"]["sample"]["snapshotFingerprint"] ==
            source.get("sourceSnapshotFingerprint", source.get("sourceDatasetFingerprint")), "Market/raw source snapshot mismatch")
    require(protocol["calendarHash"] == source["calendarEvidence"]["calendarHash"], "Market/account calendar identity differs")
    rows = []
    with gzip.open(directory / "daily.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            require(len(rows) < len(target) and row["date"] == target[len(rows)], "Daily stream gap/duplicate/order differs")
            require(row["protocolHash"] == binding["protocolHash"] and row["calendarHash"] == binding["calendarHash"]
                    and row["inputManifestSha256"] == binding["inputManifestSha256"]
                    and row["inputFeatureFileSha256"] == binding["inputFeatureFileSha256"], "Daily source lineage differs")
            rows.append(row)
    require(len(rows) == len(target), "Daily stream truncated")
    return rows


def verify_masks(baseline, filtered, pressure, daily_rows, calendar, target, source, reference):
    require(filtered["version"] == "fixed-market-context-v1" and filtered["inputSignalCount"] == len(baseline), "Filter identity/population differs")
    require(set(filtered["schedules"]) == set(filtered["evidence"]) == set(filtered["counts"]) == set(FILTERS), "Unregistered filter matrix")
    reference.checked_schedule(baseline)
    require(set(pressure["rows"]) == {s["signalId"] for s in baseline}, "Prior pressure evidence does not cover exact full opportunity set")
    require(content_sha(calendar) == pressure["sourceCalendarSha256"], "Prior-pressure source calendar digest differs")
    markets = {r["date"]: reference_market_gates(r, target, i) for i, r in enumerate(daily_rows)}
    expected = {name: [] for name in FILTERS}
    counts = {name: Counter() for name in FILTERS}
    shared_raw = {}
    for i, signal in enumerate(baseline):
        observation = pressure["rows"][signal["signalId"]]
        require(observation["symbol"] == signal["symbol"] and observation["signalDate"] == signal["signalDate"], "Prior window opportunity identity differs")
        require(observation["sourceDatasetFingerprint"] == source.get("sourceSnapshotFingerprint", source.get("sourceDatasetFingerprint")), "Prior-pressure raw source differs")
        offset = calendar.index(signal["signalDate"])
        slots = calendar[max(0, offset - 40):offset]
        require(observation["windowDates"] == slots and len(observation["windowBars"]) == len(slots), "Prior pressure shifted/compressed calendar window")
        require(content_sha(observation["windowBars"]) == observation["windowSha256"], "Raw pressure window content digest differs")
        for d, bar in zip(slots, observation["windowBars"]):
            require(bar is None or bar["date"] == d, "Pressure bar is not aligned to its calendar slot")
            pair = signal["symbol"], d
            if pair in shared_raw:
                require(shared_raw[pair] == bar, "Overlapping raw evidence contradicts itself")
            else:
                shared_raw[pair] = bar
        decision = {**markets[signal["signalDate"]], FILTERS[3]: reference_prior_pressure(
            signal, observation, calendar, [b for b in observation["windowBars"] if b is not None])}
        for name in FILTERS:
            require(len(filtered["evidence"][name]) == len(baseline), "Filter evidence skips rejected/unknown opportunities")
            saved = filtered["evidence"][name][i]
            for field in ("signalId", "signalDate", "symbol", "originalRank"):
                require(saved[field] == signal[field], "Filter evidence changes source identity/rank")
            require(saved["status"] == decision[name]["status"], "Gate classification differs: " + name + ":" + signal["signalId"])
            # Evidence fields are a subset of our retained independent values.
            for field, value in saved["inputs"].items():
                require(field in decision[name]["inputs"] and value == decision[name]["inputs"][field], "Logged gate input differs from source")
            counts[name][saved["status"]] += 1
            if decision[name]["status"] == "included":
                expected[name].append(signal)
    for name in FILTERS:
        require(filtered["schedules"][name] == expected[name], "Filter schedule is not the original ordered subset; refill/dedup/re-ranking")
        require(filtered["counts"][name] == {s: counts[name][s] for s in ("included", "excluded", "unknown")}, "Filter count totals differ")
        reference.checked_schedule(expected[name])
    repeated = Counter(r["symbol"] for r in baseline)
    return {"inputOpportunities": len(baseline), "repeatedSymbolOpportunitiesRetained": sum(n - 1 for n in repeated.values()),
            "pressureWindowsRecomputed": len(baseline), "gates": {name: dict(counts[name]) for name in FILTERS}}, expected


def verify_reserves(result, period):
    profile = PERIODS[period][2]
    equity = {r["date"]: r["equity"] for r in result["curve"]}
    quotes = {r["quoteId"]: r for r in result["research"]["feeLedger"]["quotes"]}
    buys = {r["signalId"]: r for r in result["trades"] if r["side"] == "buy"}
    count = 0
    for row in result["research"]["opportunities"]:
        if row["plannedQuantity"] is None:
            require(row["budget"] is None and row["reservePrice"] is None, "Untracked unplanned budget")
            continue
        count += 1
        budget, price = equity[row["signalDate"]] * .2, row["reservePrice"]
        require(finite(price) and price > 0, "Invalid reserve price")
        near(row["budget"], budget, "Entry budget is not fixed signal-close 20 percent equity")
        require(row["symbol"].startswith(("sh60", "sz00")), "Unexpected lot rule")
        quantity = math.floor(budget / price / 100) * 100
        while quantity and quantity * price + reference_fee(quantity * price, "buy", row["signalDate"], profile)["total"] > budget:
            quantity -= 100
        require(row["plannedQuantity"] == quantity, "Close-frozen quantity differs from dated reserve affordability")
        quote = quotes[row["reserveQuoteId"]]
        require(quote["context"]["purpose"] == "signalReserve" and quote["context"]["effective_fee_date"] == row["signalDate"], "Reserve fee anticipates a future fee date")
        near(quote["amount"], quantity * price, "Reserve quote notional differs")
        for component, expected in reference_fee(quantity * price, "buy", row["signalDate"], profile).items():
            near(quote["breakdown"][component], expected, "Dated reserve fee differs")
        buy = buys.get(row["signalId"])
        if buy is not None:
            require(buy["quantity"] % 100 == 0 and buy["amount"] + buy["fee"] <= budget + 1e-7, "Opening buy exceeds frozen budget/lot")
    return count


def calendar_intervals(period):
    specs = []
    for year in period.split("-"):
        specs.extend(({"id": year, "kind": "calendarYear", "start": year + "-01-01", "end": year + "-12-31"},
                      {"id": year + "-H1", "kind": "calendarHalfYear", "start": year + "-01-01", "end": year + "-06-30"},
                      {"id": year + "-H2", "kind": "calendarHalfYear", "start": year + "-07-01", "end": year + "-12-31"}))
    return specs


def verify_account(result, summary, schedule, *, period, reference, source, target, calendar, baseline=False):
    require(result["status"] == "completed", "Incomplete account")
    require(result["dataDigest"] == source["accountDatasetDigest"] and result["fingerprint"] == source["accountSnapshotFingerprint"], "Account dataset differs")
    require(result["research"]["identity"]["signalSchedule"] == schedule, "Actual executed schedule differs")
    require(result["research"]["identity"]["entryWeight"] == .2, "Actual entry weight differs")
    require(result["research"]["identity"]["signalScheduleSha256"] == content_sha(schedule), "Actual schedule digest differs")
    for field in ("actionLedger", "cashReceivables", "actionPayables", "preExRights"):
        require(not result[field], "Unregistered corporate action cash/share instructions")
    require(result["reservedActionCash"] == 0, "Unregistered action reservation")
    ledger = verify_fills(result, period)
    near(result["metrics"]["totalFees"], ledger["fees"], "Metrics fee total differs")
    near(result["metrics"]["totalSlippage"], ledger["slippage"], "Metrics slippage total differs")
    execution = reference.verify_opportunity_execution(schedule, result["research"]["opportunities"], result["trades"], calendar,
                                                     start=PERIODS[period][0], end=PERIODS[period][1])
    episodes = reference.verify_episodes_and_curve(result, target)
    exits = reference.verify_exit_evidence(result["trades"], allow_no_progress=True)
    structural = reference.verify_structural_observations(result, target)
    terminal = reference.verify_terminal_account(ledger, result["positions"], final_cash=result["curve"][-1]["cash"],
                                                 final_equity=result["metrics"]["finalEquity"], final_curve=result["curve"][-1])
    specs = calendar_intervals(period)
    if baseline and period == "2024-2025":
        # Baseline summary is byte-bound to the prior experiment; it retains
        # descriptive phase periods which the new comparison intentionally omits.
        specs = [{"id": p["id"], "kind": p["kind"], "start": p["requestedStart"], "end": p["requestedEnd"]}
                 for p in summary["periods"][1:]]
    stats = reference.verify_summary(result, summary, specs)
    return {"fills": ledger["fills"], "fees": ledger["fees"], "slippage": ledger["slippage"],
            "execution": execution, "closeReserveChecks": verify_reserves(result, period), "exits": exits,
            "noProgress5": structural, "episodes": episodes, "terminal": terminal, "statistics": stats}


def verify_compact(row, summary, count, period, name, reference):
    require(row["period"] == period and row["id"] == name and row["signals"] == count, "Comparison identity/count differs")
    require("phases" not in row, "Asymmetric retrospective phase summaries leaked into comparison")
    require(row["execution"] == ("reused-identical-existing-account" if name == "baseline" else "new-independent-account"), "Baseline replay/reuse attribution differs")
    require(row["closed"] == summary["closedEpisodes"]["count"] and row["openPositions"] == summary["openPositions"]["count"], "Comparison position counts differ")
    fields = {"accountReturnPct": summary["account"]["netReturnPct"], "maxDrawdownPct": summary["account"]["maxDrawdownPct"],
              "meanExposurePct": summary["periods"][0]["exposurePct"]["mean"], "winRatePct": summary["closedEpisodes"]["winRatePct"],
              "meanWinPct": summary["closedEpisodes"]["meanWinReturnPct"], "meanLossPct": summary["closedEpisodes"]["meanLossReturnPct"],
              "closedMeanPct": summary["closedEpisodes"]["meanReturnPct"], "payoffRatio": summary["closedEpisodes"]["payoffRatio"],
              "profitFactor": summary["closedEpisodes"]["profitFactor"], "top5ShareOfGrossProfitPct": summary["concentration"]["top5ProfitSharePct"]}
    for field, value in fields.items():
        reference.match_number(row[field], value, "Comparison differs: " + field)
    require(row["holdingSessions"] == summary["closedEpisodes"]["holdingSessions"] and
            row["maxConsecutiveLosses"] == summary["closedEpisodes"]["maxConsecutiveLosses"], "Comparison duration/loss streak differs")
    require(row["unreliableValuationDays"] == len(summary["quality"]["valuationUnreliableDates"]), "Comparison omitted valuation caveat")
    years = [p for p in summary["periods"] if p["kind"] == "calendarYear"]
    require([y["id"] for y in row["years"]] == [p["id"] for p in years], "Comparison calendar years differ")
    for y, p in zip(row["years"], years):
        require(y["closed"] == p["closedEpisodes"]["count"], "Comparison yearly episode count differs")
        near(y["accountReturnPct"], p["returnPct"], "Comparison yearly interval return differs")
        near(y["maxDrawdownPct"], p["maxDrawdownPct"], "Comparison yearly drawdown differs")


def verify_artifacts(output):
    files = ["plan.json", "comparison.json"]
    for period in PERIODS:
        files.extend(period + "/" + name for name in ("baseline.summary.json", "baseline.signals.json", "source.json",
                                                       "prior-pressure.json.gz", "filter-evidence.json.gz"))
        files.extend(period + "/" + name + ending for name in FILTERS
                     for ending in (".signals.json", ".result.json.gz", ".summary.json"))
    require(all((output / f).is_file() for f in files), "New runner artifacts incomplete")
    input_hashes = {f: sha(output / f) for f in files}
    plan, comparison = read(output / "plan.json"), read(output / "comparison.json")
    require(plan["schema"] == "market-context-screen-v1" and comparison["schema"] == "market-context-comparison-v1", "Wrong experiment identity")
    require(list(plan["periods"]) == list(PERIODS), "Independent periods differ")
    require(plan["newEntryVariants"] == 4 and plan["configurationsNewlyExecuted"] == 8 and plan["referenceAccountsReused"] == 2,
            "Wrong bounded experiment count")
    require(plan["costStressNewlyExecuted"] is False and plan["parameterSearch"] is False and
            plan["retrospectiveLabelsUsedAsSignals"] is False and plan["profitHurdles"] is None, "Unregistered optimization/selection")
    require(plan["newIndicators"] == 0 and "separate 500000 account" in plan["evaluation"]
            and "Do not combine into a continuous four-year curve" in plan["evaluation"], "Separate period-account interpretation omitted")
    require(all("Previously inspected" in p["sampleClaim"] and "not pristine out of sample" in p["sampleClaim"]
                for p in plan["periods"].values()), "Previously used sample described as pristine out of sample")
    require(comparison["planSha256"] == input_hashes["plan.json"], "Comparison plan binding differs")
    require(comparison["limitations"] == plan["limitations"] and comparison["profitHurdles"] is None,
            "Comparison omitted methodological limits or added a profit hurdle")
    require(len(comparison["rows"]) == 10, "Expected two baseline plus eight new account comparisons")
    require(all(sha(bound_path(p)) == expected for p, expected in plan["sourcePins"].items()), "Registered source changed")
    for document in (plan, comparison):
        require(document["certified"] is False and document["targetAchieved"] is False, "Arithmetic pass promoted to certification")
    accounts, masks, frozen_inputs = [], {}, {}
    for period in PERIODS:
        reference = isolated_account_reference(period)
        registration, destination = plan["periods"][period], output / period
        for item in registration["baselineFiles"]:
            path = bound_path(item["path"])
            require(sha(path) == item["sha256"], "Pinned baseline or market file changed")
            frozen_inputs[item["path"]] = item["sha256"]
        paths = [bound_path(x["path"]) for x in registration["baselineFiles"] if x["path"].endswith(".result.json.gz")]
        require(len(paths) == 1, "Baseline result reference ambiguous")
        baseline_result = read(paths[0])
        baseline_summary = read(str(paths[0]).replace(".result.json.gz", ".summary.json"))
        source = read(destination / "source.json")
        require(source == registration["sourceIdentity"] == comparison["sourceIdentities"][period] == read(paths[0].parent / "source.json"), "Reused account source identity differs")
        signals = read(destination / "baseline.signals.json")
        require(signals == baseline_result["research"]["identity"]["signalSchedule"] and len(signals) == registration["signalCount"]
                == (131 if period == "2022-2023" else 213), "Complete frozen baseline opportunities changed")
        if period == "2022-2023":
            originals = [bound_path(x["path"]) for x in registration["baselineFiles"] if x["path"].endswith("signals.jsonl.gz")]
            require(len(originals) == 1, "Original pre-active signal tape unavailable")
            with gzip.open(originals[0], "rt", encoding="utf-8") as stream:
                raw_signals = [json.loads(line) for line in stream]
            rebuilt = [{"signalId": r["strategyId"] + ":" + r["signalDate"] + ":" + r["symbol"], "strategyId": r["strategyId"],
                        "signalDate": r["signalDate"], "symbol": r["symbol"], "originalRank": r["rank"]}
                       for r in raw_signals if r["strategyId"] == reference.SID]
            require(sorted(rebuilt, key=lambda r: (r["signalDate"], r["originalRank"], r["symbol"])) == signals, "Baseline source was event-deduplicated")
        require(read(destination / "baseline.summary.json") == baseline_summary, "Reused baseline summary was modified")
        require(registration["config"] == baseline_result["config"], "Baseline account config changed")
        for key, expected in {"initialCapital": 500000., "maxPositions": 5, "maxStockWeight": .2, "slippageBps": 10.,
                              "stopLossPct": .05, "trailingStopPct": .1, "takeProfitPct": 0., "maxHoldingDays": 0,
                              "commissionRate": .0003, "minCommission": 5., "transferFeeRate": .00001,
                              "maxParticipationRate": .01, "rebalanceEvery": 1}.items():
            near(registration["config"][key], expected, "Fixed account setting differs: " + key)
        require(registration["config"]["stampDutyRate"] is None and registration["config"]["rebalanceMode"] == "risk", "Fixed fee/cadence setting differs")
        prior = read(destination / "prior-pressure.json.gz")
        calendar = prior["sourceCalendar"]
        saved_calendar, target = target_calendar(source, period, calendar)
        require([r["date"] for r in baseline_result["curve"]] == target, "Baseline calendar differs from independent evidence")
        market_rows = bound_market_rows(registration, target, source)
        filtered = read(destination / "filter-evidence.json.gz")
        masks[period], schedules = verify_masks(signals, filtered, prior, market_rows, calendar, target, source, reference)
        for name in ("baseline",) + FILTERS:
            if name == "baseline":
                result, summary, schedule = baseline_result, baseline_summary, signals
            else:
                result, summary = read(destination / (name + ".result.json.gz")), read(destination / (name + ".summary.json"))
                schedule = read(destination / (name + ".signals.json"))
                require(schedule == schedules[name], "Persisted signals differ from independent mask")
            require(result["config"] == registration["config"], "Filter changed account config beyond opportunities")
            checks = verify_account(result, summary, schedule, period=period, reference=reference, source=source,
                                    target=target, calendar=saved_calendar, baseline=name == "baseline")
            compact_rows = [r for r in comparison["rows"] if r["period"] == period and r["id"] == name]
            require(len(compact_rows) == 1, "Missing/duplicate comparison row")
            verify_compact(compact_rows[0], summary, len(schedule), period, name, reference)
            accounts.append({"period": period, "id": name, "status": "passed", **checks})
    require(all(sha(output / name) == expected for name, expected in input_hashes.items()), "New output changed during validation")
    require(all(sha(bound_path(name)) == expected for name, expected in frozen_inputs.items()), "Frozen input changed during validation")
    require(all(sha(bound_path(p)) == expected for p, expected in plan["sourcePins"].items()), "Source changed during validation")
    return {"schema": "market-context-independent-validation-v1", "status": "passed", "verifiedAt": datetime.now(timezone.utc).isoformat(),
            "verifierSha256": sha(Path(__file__)), "reusedVerifierSha256": PRIOR_VERIFIER_SHA256,
            "inputArtifacts": input_hashes, "frozenInputs": frozen_inputs, "sourcePins": plan["sourcePins"],
            "schedules": masks, "accounts": accounts, "selfTests": self_test(),
            "designAssertions": {"separateTwoYearAccounts": True, "initialCapitalPerAccount": 500000,
                                 "retrospectivePhaseInputsUsed": False, "comparisonPhaseFieldsPresent": False,
                                 "pristineOutOfSampleClaim": False, "strategyEffectivenessEstablished": False},
            "limits": ["Market conditions recalculated from hash-bound audited daily aggregates; underlying full feature/role mathematics not replayed.",
                       "All 344 T-1 pressure windows independently recalculated from saved raw-bar evidence; full raw files not loaded again.",
                       "Window provenance inherits the pinned source loader; raw prices/rights and historical universe remain unverified.",
                       "Reserve affordability checked against recorded signal-close reserve prices; raw signal-day close and opening liquidity not independently recollected.",
                       "Prior pure independent account checks reused in isolated period namespaces; no frozen verifier modification or shared global override.",
                       "2022-2023 dated fees and 2024-2025 Legacy continuation remain their respective declared assumptions, not broker-charge certification.",
                       "Two separate 500000 accounts per rule, not one continuous four-year strategy or untouched holdout; no new cost stress."],
            "certified": False, "targetAchieved": False}


def self_test():
    rejections = 0
    def rejects(fn):
        nonlocal rejections
        try:
            fn()
        except (ValueError, KeyError):
            rejections += 1
        else:
            raise AssertionError("Mutation unexpectedly accepted")
    near(reference_fee(100000, "sell", "2022-04-28", PERIODS["2022-2023"][2])["total"], 132., "Old transfer fixture")
    near(reference_fee(100000, "sell", "2022-04-29", PERIODS["2022-2023"][2])["total"], 131., "Transfer boundary fixture")
    near(reference_fee(100000, "sell", "2023-08-28", PERIODS["2022-2023"][2])["total"], 81., "Stamp boundary fixture")
    near(reference_fee(10000, "buy", "2024-01-02", PERIODS["2024-2025"][2])["total"], 5.1, "Minimum commission fixture")
    rejects(lambda: reference_fee(10000, "buy", "2024-01-02", PERIODS["2022-2023"][2]))
    # Dates are synthetic fixed slots; the production adapter binds real calendars.
    from datetime import timedelta
    calendar = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(41)]
    bars = [{"date": d, "open": 10., "high": 10., "low": 10., "close": 10., "volume": 100.} for d in calendar[:-1]]
    values = reference_pressure_window(bars, calendar[:-1])
    require(values["pressure5"] == values["pressure20"] == 0., "Zero true-range is not neutral")
    signal = {"signalDate": calendar[-1]}
    observation = {"asOfDate": calendar[-2], "previousCalendarDate": calendar[-2], "pressure5": 0., "pressure20": 0.}
    require(reference_prior_pressure(signal, observation, calendar, bars)["status"] == "excluded", "Pressure zero must fail strict gate")
    rejects(lambda: reference_prior_pressure(signal, {**observation, "asOfDate": calendar[-1]}, calendar, bars))
    rejects(lambda: reference_prior_pressure(signal, {**observation, "asOfDate": calendar[-3], "previousCalendarDate": calendar[-3]}, calendar, bars))
    broken = reference_pressure_window(bars[1:], calendar[:-1])
    require(broken["pressure5"] == 0. and broken["pressure20"] is None, "Missing oldest slot was compressed or over-propagated")
    rejects(lambda: reference_prior_pressure(signal, observation, calendar, bars[1:]))
    old, new = isolated_account_reference("2022-2023"), isolated_account_reference("2024-2025")
    require(old.START == "2022-01-04" and new.START == "2024-01-02" and old is not new, "Period namespace leaked")
    row = {"date": calendar[5], "featureVersion": "market-state-observations-v1", "fixedPopulation": 3141,
           "price20": {"positiveCount": 3, "evaluableCount": 4, "positiveReturn20Breadth": .75},
           "matched5": {"available": True, "earlierDate": calendar[0], "pairedCount": 4,
                        "positiveBreadthToday": .75, "positiveBreadthEarlier": .5, "matchedBreadthChange5": 25.},
           "yesterdayCohort": {"previousCohortAvailable": True, "previousDate": calendar[4], "eligibleCount": 5,
                               "continuedCount": 3, "observedFailureCount": 1, "unknownCount": 1, "observedCount": 4},
           "qUnknownCount": 100}
    gates = reference_market_gates(row, calendar, 5)
    require([gates[k]["status"] for k in FILTERS[:3]] == ["included", "included", "unknown"], "Cohort missingness was dropped")
    empty = {**row, "yesterdayCohort": {**row["yesterdayCohort"], "eligibleCount": 0, "continuedCount": 0,
                                       "observedFailureCount": 0, "unknownCount": 0, "observedCount": 0}}
    require(reference_market_gates(empty, calendar, 5)["strongCohort"]["status"] == "excluded", "Empty cohort became unknown/pass")
    known = {**row, "yesterdayCohort": {**row["yesterdayCohort"], "eligibleCount": 4, "unknownCount": 0}}
    require(reference_market_gates(known, calendar, 5)["strongCohort"]["status"] == "included", "Global unknown Q incorrectly contaminated observed cohort")
    tie = {**row, "price20": {"positiveCount": 2, "evaluableCount": 4, "positiveReturn20Breadth": .5}}
    require(reference_market_gates(tie, calendar, 5)["breadthMajority"]["status"] == "excluded", "Exact half passed a strict majority")
    rejects(lambda: reference_market_gates({**row, "matched5": {**row["matched5"], "earlierDate": calendar[1]}}, calendar, 5))
    rejects(lambda: reference_market_gates({**row, "yesterdayCohort": {**row["yesterdayCohort"], "eligibleCount": 4}}, calendar, 5))
    rejects(lambda: reference_market_gates({**row, "matched5": {**row["matched5"], "matchedBreadthChange5": .25}}, calendar, 5))
    return {"status": "passed", "adversarialRejections": rejections,
            "positiveCases": ["transfer/stamp date boundaries", "zeroTR neutrality", "missing pressure slots",
                              "isolated period namespaces", "strict majority tie", "unknown/empty/observed prior cohorts"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check-only", action="store_true", help="Verify without creating/replacing validation.json")
    arguments = parser.parse_args()
    if arguments.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
    else:
        output = arguments.output.resolve()
        receipt = output / "validation.json"
        require(arguments.check_only or not receipt.exists(), "validation.json exists; use --check-only for a read-only rerun")
        result = verify_artifacts(output)
        if not arguments.check_only:
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        print(json.dumps({"status": result["status"], "accounts": len(result["accounts"]),
                          "receipt": str(receipt) if not arguments.check_only else None}, ensure_ascii=False))
