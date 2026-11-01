"""Independent checks for the supplemental 2024-25 account comparison.

Only standard-library arithmetic is used. No strategy, fee, account, metrics or
runner implementation is imported. Reads completed artifacts and writes only
validation.json in the specified new output directory, without replacing it.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median


SID = "rawThreeStageContractionBreakoutDiagnostic"
SIGNAL_FIELDS = ("signalId", "strategyId", "signalDate", "symbol", "originalRank")
START, END = "2024-01-01", "2025-12-31"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts/strategy-search/supplemental-force-context-screen-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def near(actual, expected, message):
    require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), message)


def day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, "Noncanonical date")
    return value


def key(row):
    return row["strategyId"], row["signalDate"], row["symbol"]


def projection(row):
    return {field: row[field] for field in SIGNAL_FIELDS}


def checked_schedule(rows):
    require(isinstance(rows, list), "Schedule must be a list")
    ids, keys, ranks = set(), set(), set()
    for row in rows:
        require(row["strategyId"] == SID and START <= day(row["signalDate"]) <= END, "Wrong strategy/period")
        require(isinstance(row["symbol"], str) and bool(row["symbol"]), "Missing symbol")
        require(isinstance(row["signalId"], str) and bool(row["signalId"]), "Missing signal ID")
        rank = row["originalRank"]
        require(type(rank) is int and 1 <= rank <= 5, "Rank outside original Top5")
        require(row["signalId"] not in ids and key(row) not in keys, "Duplicate signal ID/date-symbol")
        require((row["signalDate"], rank) not in ranks, "Duplicate date-rank")
        ids.add(row["signalId"])
        keys.add(key(row))
        ranks.add((row["signalDate"], rank))
    expected = sorted(rows, key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"]))
    require(rows == expected, "Schedule order differs from date/original-rank/symbol")
    return {key(row): projection(row) for row in rows}


def verify_complete_top5(candidates, opportunities, scored_dates):
    """Rebuild ranks from all raw triggers; preserve repeated stocks across days.

    Candidate rows need strategyId, signalDate, symbol, prior20MeanAmount. This
    checks selection completeness relative to the supplied candidate log, not
    detector coverage; the manifest adapter must separately reconcile coverage.
    """
    actual = checked_schedule(opportunities)
    require(scored_dates == sorted(set(scored_dates)), "Scored calendar is unordered/duplicate")
    require(all(START <= day(d) <= END for d in scored_dates), "Scored date outside new period")
    calendar = set(scored_dates)
    groups, seen = defaultdict(list), set()
    for row in candidates:
        require(row["strategyId"] == SID and row["signalDate"] in calendar, "Foreign candidate strategy/date")
        require(key(row) not in seen, "Duplicate raw date-symbol candidate")
        require(finite(row["prior20MeanAmount"]) and row["prior20MeanAmount"] > 0, "Unknown ranking amount")
        seen.add(key(row))
        groups[row["signalDate"]].append(row)
    expected = {}
    for signal_day in scored_dates:
        ranked = sorted(groups[signal_day], key=lambda row: (-row["prior20MeanAmount"], row["symbol"]))
        for rank, row in enumerate(ranked, 1):
            if "rank" in row:
                require(row["rank"] == rank, "Candidate rank differs from amount/symbol ordering")
            if "selectedTop5" in row:
                require(row["selectedTop5"] is (rank <= 5), "Candidate Top5 flag differs")
            if rank <= 5:
                expected[key(row)] = rank
    require(set(actual) == set(expected), "Opportunity population is not complete raw Top5 (dedup/backfill/subset)")
    require(all(actual[k]["originalRank"] == rank for k, rank in expected.items()), "Original rank changed")
    counts = Counter(row["symbol"] for row in opportunities)
    return {"rawCandidates": len(seen), "opportunities": len(actual), "scoredSessions": len(scored_dates),
            "distinctSymbols": len(counts), "repeatedSymbolOpportunities": sum(n - 1 for n in counts.values())}


def verify_pressure_subset(base, selected, observations, *, threshold, operator, value_field="pressure20"):
    """Check a predeclared pressure gate on original Top5, without rank refill.

    observations supplies exactly one strategy/date/symbol and pressure value
    for every base opportunity. None is unknown and excluded, never zero.
    """
    require(finite(threshold) and operator in (">", ">=", "<", "<="), "Pressure gate not explicit")
    original, actual = checked_schedule(base), checked_schedule(selected)
    observed = {}
    for row in observations:
        require(key(row) not in observed, "Duplicate pressure observation")
        value = row[value_field]
        require(value is None or finite(value), "Invalid pressure is not explicit unknown")
        if value is not None:
            require(-1 <= value <= 1, "Pressure ratio outside [-1,1]")
        observed[key(row)] = value
    require(set(observed) == set(original), "Pressure coverage differs from complete opportunity population")
    compare = {">": lambda x: x > threshold, ">=": lambda x: x >= threshold,
               "<": lambda x: x < threshold, "<=": lambda x: x <= threshold}[operator]
    expected = {k: row for k, row in original.items() if observed[k] is not None and compare(observed[k])}
    require(actual == expected, "Pressure subset/ranks/IDs differ (possible refill, dedup, or unknown-as-zero)")
    return {"base": len(original), "selected": len(expected), "unknown": sum(v is None for v in observed.values()),
            "rejectedKnown": sum(v is not None and not compare(v) for v in observed.values())}


def verify_account_matrix(specifications, *, variant_ids):
    """Caller maps manifest entries to id/variantId/slippageBps/config."""
    require(len(variant_ids) == 2 and len(set(variant_ids)) == 2, "Exactly two fixed variants required")
    require(len(specifications) == 4 and len({r["id"] for r in specifications}) == 4, "Exactly four unique accounts required")
    require({(r["variantId"], r["slippageBps"]) for r in specifications} ==
            {(variant, slip) for variant in variant_ids for slip in (10, 30)}, "Account matrix differs from 2 x 2")
    for row in specifications:
        config = row["config"]
        for field, expected in {"initialCapital": 500000, "maxPositions": 5, "maxStockWeight": .2,
                                "commissionRate": .0003, "minCommission": 5, "transferFeeRate": .00001,
                                "stopLossPct": .05, "trailingStopPct": .1, "takeProfitPct": 0,
                                "maxHoldingDays": 0, "maxParticipationRate": .01}.items():
            near(config[field], expected, "Account config differs: " + field)
        require(config["slippageBps"] == row["slippageBps"] and config["stampDutyRate"] in (None, .0005), "Fee assumptions differ")
        require(config["rebalanceMode"] == "risk" and config["rebalanceEvery"] == 1, "Entry cadence changed")
        require(START <= config["startDate"] <= config["endDate"] <= END, "Account dates outside 2024-25")
    return {"accounts": 4, "variants": list(variant_ids), "slippageBps": [10, 30]}


def reference_fee(amount, side):
    require(finite(amount) and amount >= 0 and side in ("buy", "sell"), "Invalid fee notional/side")
    parts = {"commission": max(5., amount * .0003), "transfer": amount * .00001,
             "stampDuty": amount * .0005 if side == "sell" else 0.}
    return {**parts, "total": sum(parts.values())}


def verify_fill_arithmetic(trades, *, slippage_bps, initial_cash=500000, fee_ledger=None):
    """Audit actual fills, paid quotes, cash and shares; no undeclared cashflows.

    The new dataset must declare no corporate-action cash/share instructions.
    This function intentionally refuses cash discrepancies rather than guessing
    a dividend or other adjustment from a discrepancy.
    """
    require(slippage_bps in (10, 30), "Unexpected slippage tier")
    cash, total_fee, total_slippage = initial_cash, 0., 0.
    quantities, buy_dates, ids = Counter(), {}, set()
    if fee_ledger is not None:
        require(len(fee_ledger["paidLedger"]) == len(trades), "Paid fee row count differs")
    previous_day = START
    for index, row in enumerate(trades):
        fill_day, symbol, side = day(row["date"]), row["symbol"], row["side"]
        require(START <= previous_day <= fill_day <= END, "Fill outside new execution interval or unordered")
        previous_day = fill_day
        require(row["fillId"] not in ids, "Duplicate fill ID")
        ids.add(row["fillId"])
        require(type(row["quantity"]) is int and row["quantity"] > 0, "Invalid fill quantity")
        require(finite(row["openingPrice"]) and row["openingPrice"] > 0, "Missing opening price")
        require(day(row["signalDate"]) < fill_day, "Same-day close-to-open execution")
        expected_price = row["openingPrice"] * (1 + slippage_bps / 10000 * (1 if side == "buy" else -1))
        near(row["price"], expected_price, "Wrong opening slippage price")
        amount = row["quantity"] * expected_price
        expected_fee = reference_fee(amount, side)
        near(row["amount"], amount, "Fill amount differs from quantity x price")
        near(row["fee"], expected_fee["total"], "Per-fill fee mismatch")
        slip = row["quantity"] * abs(expected_price - row["openingPrice"])
        near(row["slippage"], slip, "Slippage amount mismatch")
        require(row["quantityBefore"] == quantities[symbol], "Share ledger before-fill mismatch")
        if side == "buy":
            require(quantities[symbol] == 0 and row["positionAction"] == "open", "Added to existing position")
            quantities[symbol] += row["quantity"]
            buy_dates[symbol] = fill_day
            cash -= amount + expected_fee["total"]
        else:
            require(buy_dates.get(symbol, END) < fill_day, "T+1 violation/no prior purchase")
            require(row["quantity"] <= quantities[symbol], "Oversold position")
            quantities[symbol] -= row["quantity"]
            cash += amount - expected_fee["total"]
        require(row["quantityAfter"] == quantities[symbol], "Share ledger after-fill mismatch")
        near(row["cashAfter"], cash, "Cash ledger mismatch or slippage deducted twice")
        require(cash >= -1e-7 and sum(q > 0 for q in quantities.values()) <= 5, "Cash/position limit violated")
        total_fee += expected_fee["total"]
        total_slippage += slip
        if fee_ledger is not None:
            paid = fee_ledger["paidLedger"][index]
            require(paid["fillId"] == row["fillId"] and paid["tradeIndex"] == index, "Paid fee/fill linkage differs")
            quote = paid["quote"]
            require(quote["side"] == side and quote["symbol"] == symbol, "Paid fee side/symbol differs")
            require(quote["context"]["effective_fee_date"] == fill_day and
                    quote["context"]["execution_date"] == fill_day and quote["context"]["purpose"] == "fillQuote", "Paid fee date/purpose differs")
            for field, expected in expected_fee.items():
                near(quote["breakdown"][field], expected, "Paid component mismatch: " + field)
    if fee_ledger is not None:
        require(len(fee_ledger["paidLedger"]) == len(trades), "Paid fee row count differs")
        near(fee_ledger["paidFeeTotal"], total_fee, "Total paid fees differ")
    return {"fills": len(trades), "cash": cash, "fees": total_fee, "slippage": total_slippage,
            "positions": {symbol: qty for symbol, qty in quantities.items() if qty}}


def verify_opportunity_execution(schedule, observed, trades, calendar, *, start, end):
    original = checked_schedule(schedule)
    require(calendar == sorted(set(calendar)), "Execution calendar invalid")
    index = {d: i for i, d in enumerate(calendar)}
    require(start in index and end in index, "Study endpoints absent from independent calendar")
    require(len(observed) == len(schedule), "Opportunity execution log count differs")
    require({key(r): projection(r) for r in observed} == original, "Execution log changes source opportunities")
    buys = {}
    for fill in trades:
        if fill["side"] == "buy":
            require(fill["signalId"] not in buys, "Repeated attempt/add for one opportunity")
            buys[fill["signalId"]] = fill
    for row in observed:
        signal_day = row["signalDate"]
        require(start <= signal_day <= end and signal_day in index, "Opportunity outside execution calendar")
        offset = index[signal_day] + 1
        next_day = calendar[offset] if offset < len(calendar) else None
        require(row["nextSession"] == next_day, "Entry compressed to next observed stock bar")
        status = row["status"]
        require(status in ("notPlanned", "censored", "filled", "partial", "expired"), "Incomplete opportunity at completion")
        if row["attemptDate"] is not None:
            require(row["attemptDate"] == next_day and row["attemptDate"] <= end, "Delayed entry attempt")
        fill = buys.pop(row["signalId"], None)
        if status in ("filled", "partial"):
            require(row["attemptDate"] == next_day, "Filled opportunity has no same next-session attempt")
            require(fill is not None and fill["date"] == next_day and fill["symbol"] == row["symbol"], "Opportunity/fill mismatch")
            require(fill["originalRank"] == row["originalRank"], "Execution rank changed")
            require(0 < fill["quantity"] <= row["plannedQuantity"], "Exceeded close-frozen planned quantity")
            require(fill["quantity"] == row["filledQuantity"], "Recorded filled quantity differs")
            require(row["unfilledQuantity"] == row["plannedQuantity"] - fill["quantity"], "Unfilled remainder mismatch")
            require((status == "filled") is (row["unfilledQuantity"] == 0), "Full/partial status differs from remainder")
        else:
            require(fill is None, "Fill exists for rejected/expired opportunity")
        if status == "censored":
            require(next_day is None or next_day > end, "False cutoff censoring")
        if status in ("censored", "notPlanned"):
            require(row["attemptDate"] is None, "Rejected/censored opportunity was attempted")
    require(not buys, "Buy without source opportunity")
    return dict(Counter(r["status"] for r in observed))


def verify_exit_evidence(trades, *, allow_no_progress):
    allowed = {"stopLoss", "trailingStop", "baseEligibilityLost", "delisted", "historicalST"}
    if allow_no_progress:
        allowed.add("noProgress5")
    counts = Counter()
    for row in trades:
        if row["side"] != "sell":
            continue
        reason = row["reason"]
        require(reason in allowed, "Unexpected exit/cutoff liquidation: " + str(reason))
        require(row["signalDate"] < row["date"], "Exit executed before a later opening")
        counts[reason] += 1
        if reason not in ("stopLoss", "trailingStop", "noProgress5"):
            continue
        evidence = row["riskEvidence"]
        if reason == "noProgress5":
            require(evidence["date"] == row["signalDate"] and evidence["holdingDays"] == 5, "NoProgress check retried after day five")
            require(evidence["status"] == "triggered" and evidence["observedClose"] <= evidence["riskEntryPrice"], "NoProgress threshold differs")
        else:
            require(evidence["observedDate"] == row["signalDate"], "Risk signal date differs")
            if reason == "stopLoss":
                require(evidence["observedClose"] / evidence["riskEntryPrice"] - 1 <= -.05, "Stop threshold differs")
            else:
                require(evidence["observedClose"] / evidence["closePeak"] - 1 <= -.1, "Trailing threshold differs")
    return dict(counts)


def verify_terminal_account(ledger, positions, *, final_cash, final_equity, final_curve):
    require(len({r["symbol"] for r in positions}) == len(positions), "Duplicate terminal position")
    require({r["symbol"]: r["quantity"] for r in positions} == ledger["positions"], "Open positions omitted or fabricated at cutoff")
    market = 0.
    for row in positions:
        require(finite(row["markPrice"]) and row["markPrice"] > 0, "Unknown terminal mark")
        near(row["marketValue"], row["quantity"] * row["markPrice"], "Terminal position value mismatch")
        market += row["marketValue"]
    near(final_cash, ledger["cash"], "Terminal cash differs from paid ledger")
    near(final_equity, final_cash + market, "Open positions excluded from final equity")
    near(final_curve["equity"], final_equity, "Final curve/result equity differs")
    near(final_curve["marketValue"], market, "Final curve market value differs")
    return {"openPositions": len(positions), "marketValue": market, "finalEquity": final_equity}


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz")
          else Path(path).open(encoding="utf-8")) as stream:
        return json.load(stream)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def object_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def match_number(actual, expected, message):
    if expected is None:
        require(actual is None, message + ": unknown denominator became a value")
    else:
        near(actual, expected, message)


def mean(values):
    return math.fsum(values) / len(values) if values else None


def verify_combined_pressure(base, selected, observations):
    original, actual = checked_schedule(base), checked_schedule(selected)
    expected, counts, seen = {}, Counter(), set()
    for row in observations:
        k = key(row)
        require(k not in seen and k in original, "Duplicate/foreign pressure observation")
        seen.add(k)
        require(projection(row) == original[k], "Pressure evidence changed original ID/rank")
        p5, p20 = row["pressure5"], row["pressure20"]
        require(all(v is None or (finite(v) and -1 <= v <= 1) for v in (p5, p20)), "Invalid pressure ratio")
        status = "unknown" if p5 is None or p20 is None else "included" if p20 > 0 and p5 > p20 else "excluded"
        require(row["status"] == status, "Combined pressure predicate/status differs")
        counts[status] += 1
        if status == "included":
            expected[k] = original[k]
    require(seen == set(original), "Pressure evidence missing original Top5 opportunities")
    require(actual == expected, "Combined pressure is not an unchanged-ID/rank subset; refill/dedup suspected")
    return {name: counts[name] for name in ("included", "excluded", "unknown")}


def verify_calendar(source, sample):
    evidence = source["calendarEvidence"]
    path = ROOT / evidence["path"]
    require(digest(path) == evidence["sha256"], "Calendar evidence changed")
    saved = read(path)
    require(saved["schema"] == "exchange-notice-derived-calendar-v1", "Independent calendar schema differs")
    require(saved["start"] == "2023-01-01" and saved["end"] == END, "Calendar warmup interval differs")
    ranges = [r for year in saved["closureRanges"].values() for r in year]
    for start, end in ranges:
        require(day(start) <= day(end), "Reversed closure range")
    expected, current = [], date.fromisoformat(saved["start"])
    while current.isoformat() <= saved["end"]:
        iso = current.isoformat()
        if current.weekday() < 5 and not any(a <= iso <= b for a, b in ranges):
            expected.append(iso)
        current += timedelta(days=1)
    require(saved["dates"] == expected, "Saved calendar differs from weekdays minus stated closures")
    require(dict(Counter(d[:4] for d in expected)) == {"2023": 242, "2024": 242, "2025": 243}, "Year session counts differ")
    dates = [d for d in expected if sample["start"] <= d <= sample["end"]]
    require(len(dates) == sample["expectedSessions"] == 485, "Execution calendar is not 485 sessions")
    require(dates[0] == "2024-01-02" and dates[-1] == END, "Execution endpoints differ")
    return expected, dates


def verify_schedule_evidence(built, schedules, calendar, sample, source):
    require(built["schedules"] == schedules, "Saved schedules differ from builder evidence")
    selections, coverage, metadata = built["selections"], built["coverage"], built["metadata"]
    require([r["date"] for r in selections] == calendar, "Selection coverage differs from full independent calendar")
    require([r["date"] for r in coverage] == calendar, "Detector coverage differs from full independent calendar")
    candidates = []
    for row in selections:
        require(row["strategyId"] == SID and row["activeExclusionApplied"] is False and row["backfillApplied"] is False,
                "Active event exclusion/backfill applied")
        require(row["rawCandidateCount"] == len(row["fullRanking"]), "Raw candidate count mismatch")
        candidates.extend({**r, "strategyId": SID, "signalDate": row["date"]} for r in row["fullRanking"])
        require(row["selectedTop5Symbols"] == [r["symbol"] for r in row["fullRanking"] if r["rank"] <= 5], "Top5 list differs")
    ranking = verify_complete_top5(candidates, schedules["baseline"], calendar)
    feature_schedule = [{"signalId": r["strategyId"] + ":" + r["signalDate"] + ":" + r["symbol"],
                         "strategyId": r["strategyId"], "signalDate": r["signalDate"], "symbol": r["symbol"],
                         "originalRank": r["rank"]} for r in built["top5Signals"]]
    require(feature_schedule == schedules["baseline"], "Top5 source feature log differs")
    for row in built["top5Signals"]:
        feature = row["features"]
        require(row["eligible"] is True and row["selectedTop5"] is True, "Selected opportunity is not eligible Top5")
        require(feature["observations"] == 81 and feature["historyEnd"] == row["signalDate"], "Signal observation window differs")
        close = feature["current"]["close"]
        require(close > feature["prior40High"] and close > feature["ma80"] > feature["previousMa80"], "Selected price predicates fail")
        near(feature["volumeRatio40"], feature["current"]["volume"] / feature["prior40MeanVolume"], "Breakout volume ratio differs")
        require(feature["volumeRatio40"] >= 1.2, "Selected breakout volume predicate fails")
        blocks = feature["contractionBlocks"]
        require(len(blocks) == 3 and all(r["sessions"] == 10 for r in blocks), "Contraction block sizes differ")
        with localcontext() as context:
            context.prec = 28
            ranges = [Decimal(str(r["maxHigh"])) / Decimal(str(r["minLow"])) - 1 for r in blocks]
            require(all(Decimal(r["rangeDecimal"]) == value for r, value in zip(blocks, ranges)), "Decimal range arithmetic differs")
            require(ranges[0] > ranges[1] > ranges[2] and ranges[2] <= Decimal("0.08"), "Selected contraction predicate fails")
        require(blocks[2]["meanVolume"] <= .7 * blocks[0]["meanVolume"], "Selected dry-volume predicate fails")
        near(row["prior20MeanAmount"], feature["prior20MeanAmount"], "Rank and feature amount differ")
        require(not feature["failedConditions"] and all(v is True for v in feature["conditions"].values()), "Selected predicate flags fail")
    pressure = verify_combined_pressure(schedules["baseline"], schedules["pressure"], built["filterEvidence"])
    for row in coverage:
        require(row["evaluatedSecurities"] == sample["expectedSecurities"] == 3141, "Detector omitted securities")
        require(sum(row["evaluatedKinds"].values()) == 3141 and sum(row["evaluatedReasons"].values()) == 3141,
                "Per-session detector dispositions do not cover every security")
        require(row["strictHistory"] is False and row["pointInTimeComplete"] is False, "Historical identity uncertainty concealed")
    for field, expected in {"scoredSessions": 485, "securities": 3141, "evaluatedSecuritySessions": 485 * 3141,
                            "rawCandidateCount": len(candidates), "top5SignalCount": len(schedules["baseline"]),
                            "pressureSignalCount": len(schedules["pressure"])}.items():
        require(metadata[field] == expected, "Schedule metadata differs: " + field)
    require(metadata["filterCounts"] == pressure, "Filter count totals differ")
    require(metadata["backfillApplied"] is False and metadata["returnsCalculated"] is False, "Builder used fill or returns")
    require(metadata["source"]["datasetFingerprint"] == source["sourceDatasetFingerprint"], "Schedule/raw source mismatch")
    require(metadata["source"]["bindingIdentity"] == source["sourceBindingIdentity"], "Schedule/source binding mismatch")
    for row in built["top5Signals"] + built["filterEvidence"]:
        require(row["source"]["datasetFingerprint"] == source["sourceDatasetFingerprint"], "Opportunity source differs")
        require(row["source"]["bindingIdentitySha256"] == metadata["source"]["bindingIdentitySha256"], "Opportunity binding differs")
    return {"ranking": ranking, "pressure": pressure, "evaluatedSecuritySessions": 485 * 3141,
            "detectorCompletenessBasis": "logged coverage and pinned detector; detector not independently rerun"}


def verify_episodes_and_curve(result, calendar):
    """Rebuild episodes and each close's cash/shares from fills, preserving open positions."""
    index = {d: i for i, d in enumerate(calendar)}
    fills = defaultdict(list)
    for fill in result["trades"]:
        fills[fill["date"]].append(fill)
    require([r["date"] for r in result["curve"]] == calendar, "Curve missing independent sessions")
    require([r["date"] for r in result["daily"]] == calendar, "Daily holding evidence missing sessions")
    state, closed, cash, previous, peak = {}, [], 500000., 500000., 500000.
    for curve, daily in zip(result["curve"], result["daily"]):
        d = curve["date"]
        require(daily["fills"] == fills[d], "Daily fills differ from full ledger")
        for fill in fills[d]:
            symbol, amount, fee, qty = (fill[k] for k in ("symbol", "amount", "fee", "quantity"))
            if fill["side"] == "buy":
                require(symbol not in state, "Unexpected add in episode reconstruction")
                state[symbol] = {"quantity": qty, "cost": amount + fee, "invested": amount + fee, "realized": 0., "entry": d}
                cash -= amount + fee
            else:
                position = state[symbol]
                removed = position["cost"] * qty / position["quantity"]
                realized = amount - fee - removed
                near(fill["realizedPnl"], realized, "Sell realized PnL differs from proportional net cost")
                position["realized"] += realized
                position["cost"] -= removed
                position["quantity"] -= qty
                cash += amount - fee
                if position["quantity"] == 0:
                    closed.append({"symbol": symbol, "entryDate": position["entry"], "exitDate": d,
                                   "pnlAmount": position["realized"], "investedAmount": position["invested"],
                                   "returnPct": position["realized"] / position["invested"] * 100,
                                   "holdingDays": index[d] - index[position["entry"]] + 1})
                    del state[symbol]
        holdings = daily["holdings"]
        require(len(holdings) == len(state) and {r["symbol"] for r in holdings} == set(state), "Daily holding set differs")
        market = 0.
        for row in holdings:
            position = state[row["symbol"]]
            require(row["quantity"] == position["quantity"], "Daily shares differ from fills")
            near(row["costBasis"], position["cost"], "Daily remaining cost differs from fills")
            require(row["holdingDays"] == index[d] - index[position["entry"]] + 1, "Holding clock differs from independent sessions")
            near(row["marketValue"], row["quantity"] * row["markPrice"], "Daily holding mark arithmetic differs")
            market += row["marketValue"]
        near(curve["cash"], cash, "Daily cash ledger differs")
        near(curve["marketValue"], market, "Daily stock market value differs")
        near(curve["equity"], cash + market, "Daily equity ignores cash/open holdings")
        near(daily["cash"], cash, "Daily/curve cash differs")
        near(daily["equity"], curve["equity"], "Daily/curve equity differs")
        near(curve["dailyReturn"], curve["equity"] / previous - 1, "Daily equity return differs")
        peak = max(peak, curve["equity"])
        near(curve["drawdownPct"], (peak - curve["equity"]) / peak * 100, "Daily peak drawdown differs")
        previous = curve["equity"]
    require(len(closed) == len(result["closedTrades"]), "Open/closed episode count differs")
    for expected, actual in zip(closed, result["closedTrades"]):
        for field in ("symbol", "entryDate", "exitDate", "holdingDays"):
            require(actual[field] == expected[field], "Closed episode identity/holding duration differs")
        for field in ("pnlAmount", "investedAmount", "returnPct"):
            near(actual[field], expected[field], "Closed episode arithmetic differs: " + field)
    return {"dailyCashAndShareChecks": len(calendar), "reconstructedClosedEpisodes": len(closed), "remainingEpisodes": len(state)}


def verify_structural_observations(result, calendar):
    detail = result["research"]["structuralExits"]
    require(detail["rule"] == "noProgress5" and detail["standardRiskHasPriority"] is True, "Structural rule/priority differs")
    require(detail["window"] == "entrySessionPlusFour;noDelayedRetry", "NoProgress window differs")
    index = {d: i for i, d in enumerate(calendar)}
    seen, counts = set(), Counter()
    buys = {(r["symbol"], r["date"]): r for r in result["trades"] if r["side"] == "buy"}
    for row in detail["observations"]:
        k = row["symbol"], row["entryDate"]
        require(k in buys and k not in seen, "Repeated or unbacked fifth-session observation")
        seen.add(k)
        require(index[row["date"]] - index[row["entryDate"]] == 4 and row["holdingDays"] == 5, "NoProgress uses stock-bar clock/delayed retry")
        near(row["riskEntryPrice"], buys[k]["price"], "NoProgress reference changed from entry fill price")
        status = row["status"]
        require(status in ("triggered", "notTriggered", "unknown", "skippedExistingExit"), "Unknown noProgress disposition")
        counts[status] += 1
        if status != "skippedExistingExit":
            require(row["retryOnLaterSession"] is False, "Unknown fifth-session check retried")
        if status in ("triggered", "notTriggered"):
            require(finite(row["observedClose"]), "Known structural close invalid")
            require((status == "triggered") is (row["observedClose"] <= row["riskEntryPrice"]), "Fifth-session threshold differs")
    require(detail["observationCounts"] == {k: counts[k] for k in detail["observationCounts"]}, "NoProgress observation totals differ")
    for fill in result["trades"]:
        if fill["side"] == "sell" and fill["reason"] == "noProgress5":
            require(fill["riskEvidence"] in detail["observations"], "Structural exit lacks retained observation")
    return dict(counts)


def verify_close_budgets(result, features, slippage_bps):
    by_key = {key(r): r["features"] for r in features}
    equities = {r["date"]: r["equity"] for r in result["curve"]}
    quotes = {r["quoteId"]: r for r in result["research"]["feeLedger"]["quotes"]}
    buys = {r["signalId"]: r for r in result["trades"] if r["side"] == "buy"}
    count = 0
    for row in result["research"]["opportunities"]:
        if row["plannedQuantity"] is None:
            require(row["budget"] is None and row["reservePrice"] is None, "Unplanned opportunity has an untracked budget")
            continue
        count += 1
        require(row["symbol"].startswith(("sh60", "sz00")), "Unexpected non-mainboard lot rule")
        budget = equities[row["signalDate"]] * .2
        price = by_key[key(row)]["current"]["close"] * (1 + slippage_bps / 10000)
        near(row["budget"], budget, "Entry budget not fixed at 20 percent of signal-close equity")
        near(row["reservePrice"], price, "Reserve price differs from signal close with slippage")
        quantity = math.floor(budget / price / 100) * 100
        while quantity and quantity * price + reference_fee(quantity * price, "buy")["total"] > budget:
            quantity -= 100
        require(row["plannedQuantity"] == quantity, "Close-frozen quantity differs from lot/cost affordability")
        quote = quotes[row["reserveQuoteId"]]
        require(quote["context"]["purpose"] == "signalReserve" and quote["context"]["effective_fee_date"] == row["signalDate"],
                "Reserve fee used later execution date")
        near(quote["amount"], quantity * price, "Reserve fee amount differs")
        for component, expected in reference_fee(quantity * price, "buy").items():
            near(quote["breakdown"][component], expected, "Reserve fee arithmetic differs")
        if row["signalId"] in buys:
            buy = buys[row["signalId"]]
            require(buy["quantity"] % 100 == 0 and buy["amount"] + buy["fee"] <= budget + 1e-7, "Opening fill exceeds frozen close budget/lot")
    return {"closeBudgetAndQuantityChecks": count}


def verify_closed_summary(records, summary):
    wins = [r for r in records if r["pnlAmount"] > 0]
    losses = [r for r in records if r["pnlAmount"] < 0]
    values = [r["returnPct"] for r in records]
    gross_profit = math.fsum(r["pnlAmount"] for r in wins)
    gross_loss = -math.fsum(r["pnlAmount"] for r in losses)
    mean_win, mean_loss = mean([r["returnPct"] for r in wins]), mean([r["returnPct"] for r in losses])
    for field, expected in {"count": len(records), "wins": len(wins), "losses": len(losses),
                            "zeros": len(records) - len(wins) - len(losses), "unknownPnlCount": 0,
                            "unknownReturnCount": 0}.items():
        require(summary[field] == expected, "Closed statistics count differs: " + field)
    expected = {"netPnlAmount": math.fsum(r["pnlAmount"] for r in records), "grossProfitAmount": gross_profit,
                "grossLossAmount": gross_loss, "winRatePct": len(wins) / len(records) * 100 if records else None,
                "meanReturnPct": mean(values), "medianReturnPct": median(values) if values else None,
                "meanWinReturnPct": mean_win, "meanLossReturnPct": mean_loss,
                "payoffRatio": mean_win / abs(mean_loss) if mean_win is not None and mean_loss else None,
                "profitFactor": gross_profit / gross_loss if gross_loss else None}
    for field, value in expected.items():
        match_number(summary[field], value, "Closed summary arithmetic differs: " + field)
    ordered = sorted(records, key=lambda r: (r["exitDate"], r["entryDate"], r["symbol"]))
    run, longest = 0, 0
    for row in ordered:
        run = run + 1 if row["pnlAmount"] < 0 else 0
        longest = max(longest, run)
    require(summary["maxConsecutiveLosses"] == (longest if records else None), "Closed loss streak differs")
    match_number(summary["holdingSessions"]["mean"], mean([r["holdingDays"] for r in records]), "Closed holding mean differs")


def verify_summary(result, summary, specifications):
    require(summary["inputResultSha256"] == object_digest(result), "Summary result content binding differs")
    require(summary["quality"]["integrityIssues"] == [], "Summary reports integrity issues")
    require(summary["sourceQuality"] == result["quality"], "Source data quality removed")
    verify_closed_summary(result["closedTrades"], summary["closedEpisodes"])
    specs = [{"id": "overall", "start": result["config"]["startDate"], "end": END, "kind": "overall"}] + specifications
    require(len(specs) == len(summary["periods"]), "Summary period count differs")
    curve = result["curve"]
    for spec, period in zip(specs, summary["periods"]):
        require(period["id"] == spec["id"] and period["kind"] == spec["kind"], "Period identity/kind differs")
        require(period["requestedStart"] == spec["start"] and period["requestedEnd"] == spec["end"], "Period boundary changed")
        selected = [r for r in curve if spec["start"] <= r["date"] <= spec["end"]]
        previous = [r for r in curve if r["date"] < spec["start"]]
        entering = previous[-1]["equity"] if previous else 500000.
        require(selected and period["observedSessions"] == len(selected), "Period session count differs")
        require(period["observedStart"] == selected[0]["date"] and period["observedEnd"] == selected[-1]["date"], "Observed period bounds differ")
        peak, drawdown = entering, 0.
        for row in selected:
            peak = max(peak, row["equity"])
            drawdown = max(drawdown, (peak - row["equity"]) / peak * 100)
        for field, expected in {"enteringEquity": entering, "endingEquity": selected[-1]["equity"],
                                "netPnlAmount": selected[-1]["equity"] - entering,
                                "returnPct": (selected[-1]["equity"] / entering - 1) * 100,
                                "maxDrawdownPct": drawdown}.items():
            near(period[field], expected, "Account interval arithmetic differs: " + spec["id"] + ":" + field)
        require(period["enteringEquityDate"] == (previous[-1]["date"] if previous else None), "Interval starts at wrong equity date")
        near(period["exposurePct"]["mean"], mean([r["marketValue"] / r["equity"] * 100 for r in selected]), "Interval exposure differs")
        require(period["valuationUnreliableDates"] == [r["date"] for r in selected if r["valuationReliable"] is not True], "Valuation caveat removed")
        require(period["closedEpisodeAssignment"] == "exitDate;entireEpisodePnlNotPartialFillDate", "Closed episodes assigned by entry cohort")
        verify_closed_summary([r for r in result["closedTrades"] if spec["start"] <= r["exitDate"] <= spec["end"]], period["closedEpisodes"])
    account = summary["account"]
    near(account["netReturnPct"], (curve[-1]["equity"] / 500000 - 1) * 100, "Account return differs")
    near(account["maxDrawdownPct"], max(r["drawdownPct"] for r in curve), "Account max drawdown differs")
    near(account["feesPaid"], math.fsum(r["fee"] for r in result["trades"]), "Summary fees differ")
    require(account["noForcedLiquidation"] is True and summary["openPositions"]["count"] == len(result["positions"]), "Open positions missing in summary")
    require(summary["openPositions"]["excludedFromClosedEpisodeDenominator"] is True, "Open positions counted as closed losses")
    positive = sorted([r for r in result["closedTrades"] if r["pnlAmount"] > 0],
                      key=lambda r: (-r["pnlAmount"], r["exitDate"], r["entryDate"], r["symbol"]))
    for n in (1, 5):
        expected = math.fsum(r["pnlAmount"] for r in positive[:n]) / math.fsum(r["pnlAmount"] for r in positive) * 100 if positive else None
        match_number(summary["concentration"][f"top{n}ProfitSharePct"], expected, "Closed-profit concentration differs")
    return {"periods": len(specs), "retrospectivePhases": sum(r["kind"] == "retrospectiveNarrativePhase" for r in specs),
            "periodReturnBasis": "previous close to last in-period close; not entry cohorts"}


def verify_artifacts(output):
    filenames = ["plan.json", "fee-parity.json", "source.json", "schedule-evidence.json.gz", "baseline.signals.json",
                 "pressure.signals.json", "comparison.json"]
    account_ids = ("baseline10", "pressure10", "baseline30", "pressure30")
    filenames += [name + suffix for name in account_ids for suffix in (".result.json.gz", ".summary.json")]
    require(all((output / name).is_file() for name in filenames), "Runner outputs incomplete; wait for comparison.json and four results")
    before = {name: digest(output / name) for name in filenames}
    plan, source, built, comparison = (read(output / name) for name in
                                     ("plan.json", "source.json", "schedule-evidence.json.gz", "comparison.json"))
    require(plan["schema"] == "supplemental-force-context-screen-v1", "Wrong experiment")
    require(plan["sample"]["start"] == "2024-01-02" and plan["sample"]["end"] == END, "Old sample reused")
    require(plan["sample"]["holdoutClaim"] is False and plan["parameterSearch"] is False, "Holdout/selection claim changed")
    require(plan["entryWeight"] == .2 and plan["exitRule"] == "noProgress5", "Entry/exit plan changed")
    require(plan["fees"]["policyClass"] == "LegacyFeePolicy" and plan["fees"]["policyId"] == "legacy-engine-delegate-v1", "Fee continuation not explicit")
    require(all(digest(ROOT / name) == expected for name, expected in plan["sourcePins"].items()), "Pinned source changed since plan")
    require(comparison["planSha256"] == before["plan.json"] and comparison["schedule"] == built["metadata"], "Comparison manifest binding differs")
    for item in (plan, comparison):
        require(item["certified"] is False and item["targetAchieved"] is False, "Arithmetic pass promoted to certification")
    matrix = [{"id": v["id"], "variantId": v["schedule"], "slippageBps": v["slippageBpsEachSide"],
               "config": plan["configs"][v["id"]]} for v in plan["variants"]]
    matrix_check = verify_account_matrix(matrix, variant_ids=("baseline", "pressure"))
    require({r["id"] for r in matrix} == set(account_ids), "Unexpected account IDs")
    base_config = {k: v for k, v in matrix[0]["config"].items() if k != "slippageBps"}
    require(all({k: v for k, v in r["config"].items() if k != "slippageBps"} == base_config for r in matrix), "Parameters other than slip vary across accounts")
    require(source["sourceImmutable"] is True and source["rawPriceAdjustment"] == "none", "Source repair/adjustment introduced")
    require(source["accountStartDate"] == "2024-01-02" and source["accountEndDate"] == END, "Account source interval differs")
    all_dates, calendar = verify_calendar(source, plan["sample"])
    schedules = {kind: read(output / (kind + ".signals.json")) for kind in ("baseline", "pressure")}
    schedule_check = verify_schedule_evidence(built, schedules, calendar, plan["sample"], source)
    parity = read(output / "fee-parity.json")
    expected_parity = {(market, amount, side, d) for market in ("SH", "SZ") for amount in (0., 1., 10000., 100000., 1000000.)
                       for side in ("buy", "sell") for d in ("2024-01-02", "2024-12-31", "2025-01-02", END)}
    require(parity["count"] == 80 and parity["failures"] == 0 and len(parity["checks"]) == 80, "Fee parity grid incomplete")
    require({(r["market"], r["amount"], r["side"], r["date"]) for r in parity["checks"]} == expected_parity, "Fee parity cases differ")
    for row in parity["checks"]:
        near(row["fee"], reference_fee(row["amount"], row["side"])["total"], "Fee parity arithmetic differs")
        require(row["matchesLate2023Formula"] is True, "Fee continuation parity failed")
    atlas = read(ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json")
    phase_specs = [r for r in plan["evaluationIntervals"] if r["kind"] == "retrospectiveNarrativePhase"]
    frozen_phases = [r for r in atlas["phases"] if r["startDate"] >= START]
    require(len(phase_specs) == len(frozen_phases) == 8, "Retrospective phase count differs")
    require([(r["id"], r["start"], r["end"]) for r in phase_specs] ==
            [(r["id"], r["startDate"], r["endDate"]) for r in frozen_phases], "Phase boundaries changed after outcomes")
    require([r["id"] for r in comparison["rows"]] == [r["id"] for r in matrix], "Comparison account ordering differs")
    accounts = []
    for specification, compared in zip(matrix, comparison["rows"]):
        name = specification["id"]
        result = read(output / (name + ".result.json.gz"))
        summary = read(output / (name + ".summary.json"))
        require(result["status"] == "completed" and result["config"] == specification["config"], "Account execution/config incomplete: " + name)
        require(result["dataDigest"] == source["accountDatasetDigest"] and result["fingerprint"] == source["accountSnapshotFingerprint"], "Account dataset identity mismatch")
        for field in ("actionLedger", "cashReceivables", "actionPayables", "preExRights"):
            require(not result[field], "Unsupported corporate-action ledger encountered")
        require(result["reservedActionCash"] == 0, "Unexplained reserved action cash")
        research, schedule = result["research"], schedules[specification["variantId"]]
        require(research["identity"]["signalSchedule"] == schedule, "Executed schedule differs from frozen signals")
        require(research["identity"]["datasetDigest"] == source["accountDatasetDigest"], "Research identity dataset mismatch")
        require(research["feeLedger"]["identity"]["settings"]["profileId"] == plan["fees"]["policyId"], "Actual fee policy differs from plan")
        ledger = verify_fill_arithmetic(result["trades"], slippage_bps=specification["slippageBps"], fee_ledger=research["feeLedger"])
        for field, expected in (("totalFees", ledger["fees"]), ("totalSlippage", ledger["slippage"])):
            near(result["metrics"][field], expected, "Account metric differs: " + field)
        execution = verify_opportunity_execution(schedule, research["opportunities"], result["trades"], all_dates,
                                                 start="2024-01-02", end=END)
        exits = verify_exit_evidence(result["trades"], allow_no_progress=True)
        structural = verify_structural_observations(result, calendar)
        budgets = verify_close_budgets(result, built["top5Signals"], specification["slippageBps"])
        episodes = verify_episodes_and_curve(result, calendar)
        terminal = verify_terminal_account(ledger, result["positions"], final_cash=result["curve"][-1]["cash"],
                                           final_equity=result["metrics"]["finalEquity"], final_curve=result["curve"][-1])
        statistics = verify_summary(result, summary, plan["evaluationIntervals"])
        for field, expected in {"signals": len(schedule), "closed": summary["closedEpisodes"]["count"],
                                "openPositions": len(result["positions"]), "slippageBpsEachSide": specification["slippageBps"]}.items():
            require(compared[field] == expected, "Comparison count/config differs: " + field)
        for field, expected in {"accountReturnPct": summary["account"]["netReturnPct"], "maxDrawdownPct": summary["account"]["maxDrawdownPct"],
                                "winRatePct": summary["closedEpisodes"]["winRatePct"], "closedMeanPct": summary["closedEpisodes"]["meanReturnPct"],
                                "meanWinPct": summary["closedEpisodes"]["meanWinReturnPct"], "meanLossPct": summary["closedEpisodes"]["meanLossReturnPct"],
                                "payoffRatio": summary["closedEpisodes"]["payoffRatio"], "profitFactor": summary["closedEpisodes"]["profitFactor"],
                                "meanExposurePct": summary["periods"][0]["exposurePct"]["mean"],
                                "top5ShareOfGrossProfitPct": summary["concentration"]["top5ProfitSharePct"]}.items():
            match_number(compared[field], expected, "Comparison arithmetic differs: " + field)
        require(compared["maxConsecutiveLosses"] == summary["closedEpisodes"]["maxConsecutiveLosses"], "Comparison loss streak differs")
        require(compared["holdingSessions"] == summary["closedEpisodes"]["holdingSessions"], "Comparison holding distribution differs")
        require(compared["unreliableValuationDays"] == len(summary["quality"]["valuationUnreliableDates"]), "Comparison valuation caveat differs")
        for group, kind in (("years", "calendarYear"), ("phases", "retrospectiveNarrativePhase")):
            periods = [p for p in summary["periods"] if p["kind"] == kind]
            require([p["id"] for p in compared[group]] == [p["id"] for p in periods], "Comparison periods differ")
            for compact, period in zip(compared[group], periods):
                require(compact["closed"] == period["closedEpisodes"]["count"], "Comparison period closed count differs")
                near(compact["accountReturnPct"], period["returnPct"], "Comparison period return differs")
                near(compact["maxDrawdownPct"], period["maxDrawdownPct"], "Comparison period drawdown differs")
        accounts.append({"id": name, "status": "passed", "fills": ledger["fills"], "fees": ledger["fees"],
                         "slippage": ledger["slippage"], "execution": execution, "exits": exits, "noProgress5": structural,
                         "closeBudgets": budgets, "dailyAndEpisodes": episodes, "terminal": terminal, "statistics": statistics})
    require(all(digest(output / name) == sha for name, sha in before.items()), "Input artifact changed during validation")
    require(all(digest(ROOT / name) == expected for name, expected in plan["sourcePins"].items()), "Source changed during validation")
    return {"schema": "supplemental-force-context-independent-validation-v1", "status": "passed",
            "validatedAt": datetime.now(timezone.utc).isoformat(), "verifierSha256": digest(Path(__file__)),
            "inputArtifacts": before, "sourcePins": plan["sourcePins"], "matrix": matrix_check,
            "schedule": schedule_check, "feeParityCases": 80, "accounts": accounts, "selfTests": self_test(),
            "limits": ["Arithmetic and retained evidence audit; no independent replay of all detector decisions or market data collection.",
                       "Pressure values are pinned shared-function observations; combined threshold and no-refill selection independently checked.",
                       "Per-fill execution time and recorded opening-price arithmetic checked; opening liquidity and raw-price accuracy remain unverified.",
                       "Calendar reconstructed from saved notice-derived ranges; notices were not newly fetched.",
                       "Legacy fee arithmetic is a continued economic assumption; not certification of historical broker fees.",
                       "Current-survivor sample, incomplete historical identities/rights, previously used data and retrospective phases retain plan limitations."],
            "certified": False, "targetAchieved": False}


def self_test():
    """Hand-sized positive and adversarial cases; does not load real outcomes."""
    count = 0
    def rejects(fn):
        nonlocal count
        try:
            fn()
        except (ValueError, KeyError, IndexError):
            count += 1
        else:
            raise AssertionError("Mutation unexpectedly passed")
    days = ["2024-01-02", "2024-01-03"]
    candidates = [{"strategyId": SID, "signalDate": d, "symbol": "sh600001", "prior20MeanAmount": 100.} for d in days]
    opportunities = [{"signalId": SID + ":" + d + ":sh600001", "strategyId": SID, "signalDate": d,
                      "symbol": "sh600001", "originalRank": 1} for d in days]
    require(verify_complete_top5(candidates, opportunities, days)["repeatedSymbolOpportunities"] == 1, "Cross-day repeat lost")
    rejects(lambda: verify_complete_top5(candidates, opportunities[:1], days))
    observations = [{**r, "pressure20": value} for r, value in zip(opportunities, (None, 0.))]
    verify_pressure_subset(opportunities, opportunities[1:], observations, threshold=0., operator=">=")
    rejects(lambda: verify_pressure_subset(opportunities, opportunities, observations, threshold=0., operator=">="))
    near(reference_fee(10000., "buy")["total"], 5.1, "Minimum commission fixture")
    near(reference_fee(100000., "sell")["total"], 81., "Sell fee fixture")
    combined = [{**r, "pressure5": p5, "pressure20": p20, "status": status} for r, p5, p20, status in
                zip(opportunities, (.2, .2), (.1, .2), ("included", "excluded"))]
    verify_combined_pressure(opportunities, opportunities[:1], combined)
    rejects(lambda: verify_combined_pressure(opportunities, opportunities, combined))
    unknown = [{**combined[0], "pressure5": None, "status": "unknown"}, combined[1]]
    verify_combined_pressure(opportunities, [], unknown)
    rejects(lambda: verify_combined_pressure(opportunities, opportunities[:1], unknown))
    # Six candidates prove the verifier does not confuse "all triggers" with Top5.
    crowded = [{"strategyId": SID, "signalDate": days[0], "symbol": f"sh60000{i}", "prior20MeanAmount": 10.}
               for i in range(1, 7)]
    top5 = [{"signalId": str(i), "strategyId": SID, "signalDate": days[0], "symbol": r["symbol"], "originalRank": i}
            for i, r in enumerate(crowded[:5], 1)]
    verify_complete_top5(crowded, top5, days)
    rejects(lambda: verify_complete_top5(crowded, top5[:-1] + [{**top5[-1], "symbol": "sh600006"}], days))
    cash = 500000.
    fills = []
    for index, (d, signal_day, side, opening) in enumerate((("2024-01-03", "2024-01-02", "buy", 10.),
                                                          ("2024-01-04", "2024-01-03", "sell", 12.))):
        price = opening * (1.001 if side == "buy" else .999)
        amount, qty = price * 100, 100
        fee = reference_fee(amount, side)["total"]
        cash += (-amount if side == "buy" else amount) - fee
        fills.append({"date": d, "signalDate": signal_day, "symbol": "sh600001", "side": side, "quantity": qty,
                      "price": price, "openingPrice": opening, "amount": amount, "fee": fee,
                      "slippage": qty * abs(price - opening), "cashAfter": cash, "fillId": str(index),
                      "quantityBefore": 0 if side == "buy" else qty, "quantityAfter": qty if side == "buy" else 0,
                      "positionAction": "open" if side == "buy" else "exit", "signalId": opportunities[0]["signalId"],
                      "originalRank": 1})
    verify_fill_arithmetic(fills, slippage_bps=10)
    rejects(lambda: verify_fill_arithmetic([{**fills[0], "fee": fills[0]["fee"] + 1}, fills[1]], slippage_bps=10))
    rejects(lambda: verify_fill_arithmetic([{**fills[0], "cashAfter": fills[0]["cashAfter"] - fills[0]["slippage"]}, fills[1]], slippage_bps=10))
    rejects(lambda: verify_fill_arithmetic([{**fills[0], "signalDate": fills[0]["date"]}, fills[1]], slippage_bps=10))
    observed = [{**opportunities[0], "nextSession": "2024-01-03", "status": "filled", "attemptDate": "2024-01-03",
                 "plannedQuantity": 100, "filledQuantity": 100, "unfilledQuantity": 0}]
    execution_days = days + ["2024-01-04"]
    verify_opportunity_execution(opportunities[:1], observed, fills, execution_days, start=days[0], end=execution_days[-1])
    rejects(lambda: verify_opportunity_execution(opportunities[:1], [{**observed[0], "attemptDate": "2024-01-04"}],
                                                 fills, execution_days, start=days[0], end=execution_days[-1]))
    return {"status": "passed", "adversarialRejections": count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check-only", action="store_true", help="Verify without writing or replacing validation.json")
    arguments = parser.parse_args()
    if arguments.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
    else:
        output = arguments.output.resolve()
        receipt = output / "validation.json"
        require(arguments.check_only or not receipt.exists(), "validation.json already exists; use --check-only for a read-only rerun")
        result = verify_artifacts(output)
        if not arguments.check_only:
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        print(json.dumps({"status": result["status"], "accounts": len(result["accounts"]),
                          "receipt": str(receipt) if not arguments.check_only else None}, ensure_ascii=False))
