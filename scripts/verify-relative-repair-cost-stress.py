"""Independent accounting audit for a frozen matched-control cost stress.

Only slippage changes. No new signals, market data, strategy decisions or
account are computed here. The prior independently checked signal projection
supplies signal-close prices; original price truth and opening capacity remain
outside this arithmetic audit. Old verifier files are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts/strategy-search/relative-repair-cost-stress-v1"
PARENT = "artifacts/strategy-search/relative-repair-screen-v1"
PARENT_PLAN_SHA256 = "c0b649c81716ae32b2e3503404f2142a076dd41c05d1d409fc712250d0d252bd"
PARENT_VALIDATION_SHA256 = "a69150374f0c048e43ed9d5f487416af72606bde7bb189149cfc34ba00c57eab"
SID = "relativeRepairEventV1"
TIERS = {"control10": 10, "control30": 30, "control50": 50}
PERIODS = {"2022-2023": ("2022-01-04", "2023-12-29", 484, "dated-transfer-assumption-v1"),
           "2024-2025": ("2024-01-02", "2025-12-31", 485, "legacy-engine-delegate-v1")}
REFERENCES = {
    "scripts/verify-relative-repair-screen.py": "d332acb6874b5bf5a3954d0d47824fabfea9284d28b902d788b9fe0314cda690",
    "scripts/verify-market-context-screen.py": "f08d534351f55a6715dd1e43533f1ac914799d55ba4f1d502ca8c870fca235b1",
    "scripts/verify-supplemental-force-context.py": "d2390f1142238ae00fc3f2430eaa4b91bdd66a408a0657f496f0a6158fc05e9d"}
REGISTERED_PLAN_SHA256 = "47b265b50ae7f3e1280bd66af8c7869e6343c63dbf4a84a7ffa18454448570c3"
RUNNER_SHA256 = "df5ede67a6a8fe12dcfa60c065f82fe05df708a064c509da422a1b6c971b1722"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def near(actual, expected, message):
    require(finite(actual) and finite(expected) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), message)


def iso(value):
    require(type(value) is str and date.fromisoformat(value).isoformat() == value, "Noncanonical date")
    return value


def read(path):
    path = Path(path)
    with (gzip.open(path, "rt", encoding="utf-8") if path.name.endswith(".gz") else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def local(relative):
    path = (ROOT / relative).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Missing/outside-root source: " + str(relative))
    return path


def pinned(reference):
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}, "Expected exact path/hash reference")
    path = local(reference["path"])
    require(sha(path) == reference["sha256"], "Pinned source changed: " + reference["path"])
    return path


def reference_modules(period):
    """Each call creates a private chain; no prior/shared module is patched."""
    require(period in PERIODS, "Unknown fixed period")
    for path, expected in REFERENCES.items():
        require(sha(local(path)) == expected, "Independent dependency changed: " + path)
    name = next(iter(REFERENCES))
    spec = importlib.util.spec_from_file_location("_cost_stress_reference_" + period, local(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    market, account = module.accounting(period)
    return module, market, account


def fee(amount, side, effective_date, period):
    require(finite(amount) and amount >= 0 and side in ("buy", "sell"), "Invalid fee notional/side")
    start, end, _n, _profile = PERIODS[period]
    require(start <= iso(effective_date) <= end, "Fee date outside fixed account period")
    transfer = .00002 if period == "2022-2023" and effective_date < "2022-04-29" else .00001
    stamp = (.001 if effective_date < "2023-08-28" else .0005) if side == "sell" else 0.
    breakdown = {"commission": max(5., amount * .0003), "transfer": amount * transfer,
                 "stampDuty": amount * stamp}
    return {"rates": {"commission": .0003, "transfer": transfer, "chargedStampDuty": stamp},
            "breakdown": {**breakdown, "total": sum(breakdown.values())}}


def verify_fills(result, period, slippage_bps):
    """Rebuild paid fees, cash and shares using each actual fill's own date."""
    start, end, _n, profile = PERIODS[period]
    require(slippage_bps in TIERS.values() and type(slippage_bps) in (int, float), "Unknown stress tier")
    require(result["config"]["slippageBps"] == slippage_bps, "Recorded slippage setting differs")
    ledger = result["research"]["feeLedger"]
    settings = ledger["identity"]["settings"]
    require(settings["profileId"] == profile and settings["commissionIncludesTransfer"] is False and
            settings["stampDutyOverride"] is None, "Period fee regime changed")
    near(settings["commissionRate"], .0003, "Commission rate changed")
    near(settings["minCommission"], 5., "Commission minimum changed")
    paid, trades = ledger["paidLedger"], result["trades"]
    require(len(paid) == len(trades), "Paid quote/fill count differs")
    quotes = {q["quoteId"]: q for q in ledger["quotes"]}
    require(len(quotes) == len(ledger["quotes"]), "Duplicate fee quote handles")
    quantities, last_buy = Counter(), {}
    cash, total_fees, total_slippage = 500000., 0., 0.
    previous_day, fill_ids, quote_ids = start, set(), set()
    for index, (fill, charge) in enumerate(zip(trades, paid)):
        day, symbol, side, quantity = (fill[k] for k in ("date", "symbol", "side", "quantity"))
        require(start <= previous_day <= iso(day) <= end and iso(fill["signalDate"]) < day, "Fill date/cause not chronological")
        require(side in ("buy", "sell") and type(quantity) is int and quantity > 0, "Invalid fill side/quantity")
        require(fill["fillId"] not in fill_ids and charge["quoteId"] not in quote_ids, "Reused fill or paid quote")
        fill_ids.add(fill["fillId"])
        quote_ids.add(charge["quoteId"])
        previous_day = day
        opening = fill["openingPrice"]
        require(finite(opening) and opening > 0, "Invalid recorded opening price")
        multiplier = 1 + (slippage_bps / 10000 if side == "buy" else -slippage_bps / 10000)
        price = opening * multiplier
        amount, slip = quantity * price, quantity * abs(price - opening)
        expected_fee = fee(amount, side, day, period)
        near(fill["price"], price, "Wrong side/tier slipped execution price")
        near(fill["amount"], amount, "Execution notional differs")
        near(fill["slippage"], slip, "Recorded slippage differs")
        near(fill["fee"], expected_fee["breakdown"]["total"], "Execution fee differs")
        require(fill["quantityBefore"] == quantities[symbol], "Pre-fill share ledger differs")
        if side == "buy":
            require(not quantities[symbol] and fill["positionAction"] == "open", "Unexpected position add")
            cash -= amount + fill["fee"]
            quantities[symbol] += quantity
            last_buy[symbol] = day
        else:
            require(last_buy.get(symbol, end) < day and quantity <= quantities[symbol], "T+1 violation or oversell")
            cash += amount - fill["fee"]
            quantities[symbol] -= quantity
        require(fill["quantityAfter"] == quantities[symbol], "Post-fill share ledger differs")
        near(fill["cashAfter"], cash, "Cash mismatch or slippage charged twice")
        require(cash >= -1e-7 and sum(v > 0 for v in quantities.values()) <= 5, "Cash/position limit broken")
        quote = charge["quote"]
        require(charge["fillId"] == fill["fillId"] and charge["tradeIndex"] == index and
                charge["quoteId"] == quote["quoteId"] == fill["feeQuoteId"], "Paid fee/fill identity differs")
        require(quote["symbol"] == symbol and quote["side"] == side, "Paid quote side/symbol differs")
        require(quotes.get(quote["quoteId"]) == quote, "Paid quote absent/different from quote ledger")
        economic_fill = {k: v for k, v in fill.items() if k not in
            {"capacityBasis", "openingCapacityVerified", "executionValid"}}
        require(charge["fill"] == economic_fill, "Paid ledger fill differs from economic execution")
        near(quote["amount"], amount, "Paid quote amount differs")
        context = quote["context"]
        require(context["purpose"] == "fillQuote" and context["effective_fee_date"] == context["execution_date"] == day
                and context["decision_date"] == day and context["symbol"] == symbol
                and context["signal_date"] == fill["signalDate"], "Actual fee uses incorrect execution date")
        for field, expected in expected_fee["breakdown"].items():
            near(quote["breakdown"][field], expected, "Paid fee component differs: " + field)
        for field, expected in expected_fee["rates"].items():
            near(quote["rates"][field], expected, "Paid fee rate differs: " + field)
        total_fees += fill["fee"]
        total_slippage += slip
    near(ledger["paidFeeTotal"], total_fees, "Total paid fees differ")
    near(result["metrics"]["totalFees"], total_fees, "Metrics fee total differs")
    near(result["metrics"]["totalSlippage"], total_slippage, "Metrics slippage total differs")
    return {"fills": len(trades), "cash": cash, "fees": total_fees, "slippage": total_slippage,
            "positions": {s: q for s, q in quantities.items() if q}}


def verify_reserves(result, close_by_key, period, slippage_bps):
    equities = {row["date"]: row["equity"] for row in result["curve"]}
    quotes = {row["quoteId"]: row for row in result["research"]["feeLedger"]["quotes"]}
    buys = {row["signalId"]: row for row in result["trades"] if row["side"] == "buy"}
    count = 0
    for opportunity in result["research"]["opportunities"]:
        if opportunity["plannedQuantity"] is None:
            require(opportunity["budget"] is None and opportunity["reservePrice"] is None, "Unplanned budget fabricated")
            continue
        count += 1
        day, symbol = opportunity["signalDate"], opportunity["symbol"]
        close = close_by_key.get((symbol, day))
        require(finite(close) and close > 0, "Planned opportunity lacks valid pinned close")
        require(symbol.startswith(("sh60", "sz00")), "Unexpected reserve lot rule")
        price, budget = close * (1 + slippage_bps / 10000), equities[day] * .2
        near(opportunity["budget"], budget, "Entry budget not frozen at signal-close equity")
        near(opportunity["reservePrice"], price, "Reserve price uses wrong slippage or later close")
        quantity = math.floor(budget / price / 100) * 100
        while quantity and quantity * price + fee(quantity * price, "buy", day, period)["breakdown"]["total"] > budget:
            quantity -= 100
        require(opportunity["plannedQuantity"] == quantity, "Dated reserve affordability differs")
        quote = quotes[opportunity["reserveQuoteId"]]
        context = quote["context"]
        require(context["purpose"] == "signalReserve" and context["effective_fee_date"] == context["signal_date"] == day
                and context["decision_date"] == day and context["symbol"] == symbol
                and context["execution_date"] is None, "Reserve anticipates actual fill fee date")
        require(quote["symbol"] == symbol and quote["side"] == "buy", "Reserve quote symbol/side differs")
        near(quote["amount"], quantity * price, "Reserve quote notional differs")
        for component, expected in fee(quantity * price, "buy", day, period)["breakdown"].items():
            near(quote["breakdown"][component], expected, "Reserve fee differs")
        fill = buys.get(opportunity["signalId"])
        if fill:
            require(fill["quantity"] % 100 == 0 and fill["quantity"] <= quantity and
                    fill["amount"] + fill["fee"] <= budget + 1e-7, "Actual buy exceeds frozen lot/budget")
    return count


def verify_account(result, summary, schedule, config, source, calendar, period, slippage_bps, closes, references):
    relative, market, account = references
    verify_recorded_config(result["config"], config, relative.same)
    require(result["status"] == "completed" and result["dataDigest"] == source["accountDatasetDigest"] and
            result["fingerprint"] == source["accountSnapshotFingerprint"], "Incomplete account or source differs")
    identity = result["research"]["identity"]
    relative.same(identity["signalSchedule"], schedule, "unchanged-schedule")
    require(identity["signalScheduleSha256"] == digest(schedule) and identity["entryWeight"] == .2, "Schedule/entry weight changed")
    for field in ("actionLedger", "cashReceivables", "actionPayables", "preExRights"):
        require(not result[field], "Unregistered rights instructions introduced")
    require(result["reservedActionCash"] == 0, "Unregistered rights cash reservation")
    start, end, sessions, _profile = PERIODS[period]
    target = [d for d in calendar if start <= d <= end]
    require(len(target) == sessions, "Wrong independent target session count")
    ledger = verify_fills(result, period, slippage_bps)
    execution = account.verify_opportunity_execution(schedule, result["research"]["opportunities"], result["trades"],
        calendar, start=start, end=end)
    episodes = account.verify_episodes_and_curve(result, target)
    exits = account.verify_exit_evidence(result["trades"], allow_no_progress=True)
    structural = account.verify_structural_observations(result, target)
    terminal = account.verify_terminal_account(ledger, result["positions"], final_cash=result["curve"][-1]["cash"],
        final_equity=result["metrics"]["finalEquity"], final_curve=result["curve"][-1])
    summary_checks = account.verify_summary(result, summary, market.calendar_intervals(period))
    return {"fills": ledger["fills"], "fees": ledger["fees"], "slippage": ledger["slippage"],
        "execution": execution, "reserveChecks": verify_reserves(result, closes, period, slippage_bps),
        "episodes": episodes, "exits": exits, "noProgress5": structural, "terminal": terminal, "statistics": summary_checks}


def verify_cost_only_config(original, observed, bps):
    require(bps in TIERS.values() and type(bps) is int, "Unregistered cost tier")
    require(original["slippageBps"] == 10, "Original account is not 10bps")
    expected = deepcopy(original)
    expected["slippageBps"] = bps
    require(observed == expected, "Configuration changed beyond slippageBps")


def verify_recorded_config(actual, registered, strict_compare):
    # The unchanged engine normalizes config.slippageBps to float; plan tier
    # labels are integers. Only this field's numeric representation may differ.
    require(finite(actual.get("slippageBps")) and finite(registered.get("slippageBps")) and
        actual["slippageBps"] == registered["slippageBps"], "Recorded cost tier differs")
    strict_compare({k: v for k, v in actual.items() if k != "slippageBps"},
        {k: v for k, v in registered.items() if k != "slippageBps"}, "cost-only-config")


def collect_signal_closes(projected, schedule):
    """Retain only the already audited signal dates, never reselect stocks."""
    needed = {}
    for row in schedule:
        needed.setdefault(row["symbol"], set()).add(row["signalDate"])
    result, symbols = {}, []
    for security in projected["securities"]:
        symbol = security["symbol"]
        symbols.append(symbol)
        if symbol not in needed:
            continue
        previous = None
        for bar in security["bars"]:
            day = iso(bar["date"])
            require(previous is None or previous < day, "Projected stock dates unordered/duplicate")
            previous = day
            if day in needed[symbol]:
                result[symbol, day] = bar["close"]
    require(symbols == sorted(set(symbols)), "Projected universe unordered/duplicate")
    require(set(needed) <= set(symbols), "Signal symbol absent from frozen projection")
    require(len(result) == sum(len(v) for v in needed.values()), "Signal date absent from frozen projection")
    return result, symbols


def verify_artifacts(output, *, progress=None):
    output = Path(output)
    emit = progress or (lambda _message: None)
    self_hash = sha(__file__)
    require(not (output / "failure.json").exists(), "Runner failure present")
    require(sha(output / "plan.json") == REGISTERED_PLAN_SHA256, "Plan was not the frozen pre-run plan")
    plan, comparison = read(output / "plan.json"), read(output / "comparison.json")
    require(plan["schema"] == "relative-repair-cost-stress-v1" and
        comparison["schema"] == "relative-repair-cost-comparison-v1", "Unexpected study schema")
    require(list(plan["periods"]) == list(PERIODS), "Missing/extra/reordered period")
    require(plan["tiers"] == [{"id": k, "slippageBps": v, "reused": v == 10} for k, v in TIERS.items()], "Cost tiers changed")
    for field, value in {"newAccounts": 4, "reusedBaselineAccounts": 2, "newEntryVariants": 0,
        "parameterSearch": False, "profitHurdles": None, "entryWeight": .2, "exitRule": "noProgress5",
        "certified": False, "targetAchieved": False}.items():
        require(type(plan[field]) is type(value) and plan[field] == value, "Plan scope differs: " + field)
    for field in ("newAccounts", "reusedBaselineAccounts", "profitHurdles", "certified", "targetAchieved", "limitations"):
        require(comparison[field] == plan[field], "Comparison scope differs: " + field)
    require(comparison["planSha256"] == REGISTERED_PLAN_SHA256, "Comparison plan identity differs")
    rows = comparison["rows"]
    expected_pairs = [(period, tier) for period in PERIODS for tier in TIERS]
    require([(r["period"], r["id"]) for r in rows] == expected_pairs, "Six-account matrix omitted/reordered/duplicated")
    source_pins, frozen = plan["sourcePins"], dict(plan["frozenInputs"])
    require(source_pins["scripts/run-relative-repair-cost-stress.py"] == RUNNER_SHA256, "Runner is not frozen source")
    require(frozen[PARENT + "/plan.json"] == PARENT_PLAN_SHA256 and
        frozen[PARENT + "/validation.json"] == PARENT_VALIDATION_SHA256, "Original independently audited study changed")
    for path, expected in {**source_pins, **frozen}.items():
        require(sha(local(path)) == expected, "Frozen input/code changed: " + path)
    parent_plan = read(local(PARENT + "/plan.json"))
    proof = read(local(PARENT + "/validation.json"))
    require(proof["passed"] is True and proof["failureCount"] == 0 and
        proof["planSha256"] == PARENT_PLAN_SHA256 and proof["verifierSha256"] == REFERENCES[next(iter(REFERENCES))],
        "Parent signal/account audit not valid")
    for path, expected in parent_plan["sourcePins"].items():
        require(sha(local(path)) == expected, "Parent source dependency changed: " + path)
        frozen[path] = expected
    known = {"plan.json": sha(output / "plan.json"), "comparison.json": sha(output / "comparison.json")}
    accounts, signal_bindings = [], {}
    required_files = {"signals.json", "source.json", "baseline-reference.json"} | {
        tier + suffix for tier in ("control30", "control50") for suffix in (".result.json.gz", ".summary.json")}
    require(set(comparison["artifacts"]) == set(PERIODS), "Artifact periods differ")
    for period, (start, end, count, _profile) in PERIODS.items():
        refs = reference_modules(period)
        relative, market, account = refs
        record, folder = plan["periods"][period], output / period
        require({p.name for p in folder.iterdir()} == required_files and all((folder / p).is_file() for p in required_files),
            "Period inventory missing/extra files")
        hashes = {name: sha(folder / name) for name in sorted(required_files)}
        require(hashes == comparison["artifacts"][period], "New output hash differs")
        known.update({period + "/" + name: h for name, h in hashes.items()})
        baseline = record["baseline"]
        require(set(baseline) == {"result", "summary", "signals", "source"}, "Incomplete baseline references")
        suffixes = {"result": "matchedControl.result.json.gz", "summary": "matchedControl.summary.json",
            "signals": "matchedControl.signals.json", "source": "source.json"}
        paths = {}
        for kind, suffix in suffixes.items():
            ref = baseline[kind]
            require(ref["path"] == PARENT + "/" + period + "/" + suffix, "Wrong original matched-control input")
            paths[kind] = pinned(ref)
            require(frozen[ref["path"]] == ref["sha256"] == proof["inputArtifacts"][period + "/" + suffix], "Baseline not in original proof")
        require(read(folder / "baseline-reference.json") == baseline, "Copied baseline references differ")
        require(hashes["signals.json"] == baseline["signals"]["sha256"] and
            (folder / "signals.json").read_bytes() == paths["signals"].read_bytes(), "Signal bytes changed")
        schedule, source = read(folder / "signals.json"), read(folder / "source.json")
        account.checked_schedule(schedule)
        relative.same(source, read(paths["source"]), "source-baseline")
        relative.same(source, record["sourceIdentity"], "source-registered")
        original, original_summary = read(paths["result"]), read(paths["summary"])
        relative.same(record["config"], original["config"], "baseline-config")
        relative.same(record["config"], parent_plan["periods"][period]["config"], "parent-config")
        relative.verify_config(original["config"], record["config"], period)
        require(set(record["configs"]) == set(TIERS), "Configuration tiers differ")
        require(original["research"]["identity"]["signalSchedule"] == schedule, "Parent schedule differs")
        projection_ref = record["sourceProjection"]
        require(projection_ref["path"] == PARENT + "/" + period + "/signal-input.json.gz" and
            projection_ref["sha256"] == frozen[projection_ref["path"]] == proof["inputArtifacts"][period + "/signal-input.json.gz"],
            "Raw projection is not original audited source")
        emit({"phase": "verify-cost-source", "period": period})
        projected = read(pinned(projection_ref))
        require(projected["sourceDatasetFingerprint"] == source.get("sourceSnapshotFingerprint", source.get("sourceDatasetFingerprint"))
            and projected["priceBasis"] == "raw-unadjusted;no-future-reference-normalization", "Projected source differs")
        calendar = projected["calendar"]
        saved, target = market.target_calendar(source, period, calendar)
        require(calendar == saved and calendar[-1] == end and len(target) == count, "Account calendar/window differs")
        closes, symbols = collect_signal_closes(projected, schedule)
        require(len(symbols) == 3141 and all(s.startswith(("sh60", "sz00")) for s in symbols), "Fixed mainboard population differs")
        del projected
        calendar_ref = source["calendarEvidence"]
        frozen[calendar_ref["path"]] = calendar_ref["sha256"]
        signal_bindings[period] = {"signals": len(schedule), "sha256": hashes["signals.json"],
            "byteIdenticalToParent": True, "projectionSha256": projection_ref["sha256"],
            "targetSessions": len(target), "securities": len(symbols)}
        original_settings = original["research"]["feeLedger"]["identity"]["settings"]
        for tier, bps in TIERS.items():
            emit({"phase": "verify-cost-account", "period": period, "id": tier, "reused": bps == 10})
            verify_cost_only_config(record["config"], record["configs"][tier], bps)
            result = original if bps == 10 else read(folder / (tier + ".result.json.gz"))
            summary = original_summary if bps == 10 else read(folder / (tier + ".summary.json"))
            require(summary["policyId"] == ("matchedControl" if bps == 10 else tier), "Summary identity differs")
            relative.same(result["research"]["feeLedger"]["identity"]["settings"], original_settings, "unchanged-fee-regime")
            checks = verify_account(result, summary, schedule, record["configs"][tier], source, calendar, period, bps, closes, refs)
            row = next(r for r in rows if (r["period"], r["id"]) == (period, tier))
            require(type(row["reused"]) is bool and row["reused"] == (bps == 10) and row["slippageBps"] == bps, "Reused/cost row mislabeled")
            relative.verify_comparison(row, summary, len(schedule), period, tier, account)
            accounts.append({"period": period, "id": tier, "slippageBps": bps, "reused": bps == 10,
                "status": "passed", "accountReturnPct": summary["account"]["netReturnPct"],
                "maxDrawdownPct": summary["account"]["maxDrawdownPct"], **checks})
            del result, summary
        del original, original_summary
    require(all(sha(output / name) == h for name, h in known.items()), "Output changed during verification")
    require(all(sha(local(name)) == h for name, h in {**frozen, **source_pins, **REFERENCES}.items()), "Input/code changed during verification")
    require(sha(__file__) == self_hash, "Verifier changed during verification")
    return {"schema": "relative-repair-cost-stress-independent-validation-v1", "status": "passed", "passed": True,
        "failureCount": 0, "verifiedAt": datetime.now(timezone.utc).isoformat(), "verifierSha256": self_hash,
        "planSha256": REGISTERED_PLAN_SHA256, "inputArtifacts": known, "frozenInputs": frozen,
        "sourcePins": source_pins, "reusedVerifiers": REFERENCES, "signals": signal_bindings, "accounts": accounts,
        "newAccounts": 4, "reusedBaselineAccounts": 2, "newEntryVariants": 0,
        "scope": {"signalBytesVerified": True, "signalMathematicsRecomputed": False,
            "parentSignalProofInherited": True, "slippageAndDatedFeesIndependentlyComputed": True,
            "accountArithmeticAndSummariesRecomputed": True, "accountEngineRerun": False,
            "separatePeriodReferenceNamespaces": True, "sourcePriceTruthCertified": False,
            "openingPriceRecollected": False, "allExecutionGatesIndependentlyReconstructed": False},
        "limitations": plan["limitations"], "certified": False, "targetAchieved": False}


def self_test():
    """Small hand-constructed ledgers; no market outcomes or engine calls."""
    import unittest

    def fixture(period, bps):
        days = ("2023-08-24", "2023-08-28") if period == "2022-2023" else ("2024-01-03", "2024-01-04")
        signal_dates = ("2023-08-23", "2023-08-25") if period == "2022-2023" else ("2024-01-02", "2024-01-03")
        cash, trades, paid, quotes = 500000., [], [], []
        for index, side in enumerate(("buy", "sell")):
            opening = (10., 11.)[index]
            price = opening * (1 + (1 if side == "buy" else -1) * bps / 10000)
            amount = price * 900
            commission, transfer, stamp = max(5., amount * .0003), amount * .00001, (amount * .0005 if index else 0.)
            charge = commission + transfer + stamp
            cash += amount - charge if index else -amount - charge
            fill = {"date": days[index], "signalDate": signal_dates[index], "symbol": "sh600001", "side": side,
                "quantity": 900, "openingPrice": opening, "price": price, "amount": amount, "fee": charge,
                "slippage": 900 * abs(price - opening), "cashAfter": cash, "quantityBefore": 900 if index else 0,
                "quantityAfter": 0 if index else 900, "positionAction": "close" if index else "open",
                "fillId": "f" + str(index), "feeQuoteId": index + 10}
            quote = {"quoteId": index + 10, "symbol": "sh600001", "side": side, "amount": amount,
                "context": {"symbol": "sh600001", "signal_date": signal_dates[index], "decision_date": days[index],
                    "effective_fee_date": days[index], "execution_date": days[index], "purpose": "fillQuote"},
                "breakdown": {"commission": commission, "transfer": transfer, "stampDuty": stamp, "total": charge},
                "rates": {"commission": .0003, "transfer": .00001, "chargedStampDuty": .0005 if index else 0.}}
            paid.append({"fillId": fill["fillId"], "tradeIndex": index, "quoteId": quote["quoteId"],
                "fill": deepcopy(fill), "quote": deepcopy(quote)})
            fill.update(capacityBasis="previousVolumeApproximation", openingCapacityVerified=False, executionValid=True)
            trades.append(fill)
            quotes.append(quote)
        return {"config": {"slippageBps": bps}, "trades": trades,
            "metrics": {"totalFees": sum(f["fee"] for f in trades), "totalSlippage": sum(f["slippage"] for f in trades)},
            "research": {"feeLedger": {"identity": {"settings": {"profileId": PERIODS[period][3],
                "commissionIncludesTransfer": False, "stampDutyOverride": None, "commissionRate": .0003, "minCommission": 5.}},
                "paidLedger": paid, "quotes": quotes, "paidFeeTotal": sum(f["fee"] for f in trades)}}}

    class IndependentCostTests(unittest.TestCase):
        def test_all_tiers_both_periods_buy_sell_cash_and_annotation(self):
            for period in PERIODS:
                for bps in TIERS.values():
                    result = fixture(period, bps)
                    out = verify_fills(result, period, bps)
                    buy, sell = result["trades"]
                    self.assertEqual(out["positions"], {})
                    self.assertAlmostEqual(out["cash"], 500000 - buy["amount"] - buy["fee"] + sell["amount"] - sell["fee"])
                    self.assertEqual(out["fills"], 2)

        def test_hand_fee_date_boundaries(self):
            self.assertAlmostEqual(fee(100000, "buy", "2022-04-28", "2022-2023")["breakdown"]["total"], 32)
            self.assertAlmostEqual(fee(100000, "buy", "2022-04-29", "2022-2023")["breakdown"]["total"], 31)
            self.assertAlmostEqual(fee(100000, "sell", "2023-08-25", "2022-2023")["breakdown"]["total"], 131)
            self.assertAlmostEqual(fee(100000, "sell", "2023-08-28", "2022-2023")["breakdown"]["total"], 81)
            self.assertAlmostEqual(fee(1900, "sell", "2024-01-04", "2024-2025")["breakdown"]["total"], 5 + .019 + .95)
            with self.assertRaises(ValueError):
                fee(100000, "buy", "2024-01-03", "2022-2023")

        def test_tampered_fill_side_price_fee_cash_tplusone_and_paid_rejected(self):
            mutations = [lambda r: r["trades"][0].update(price=10.05),
                lambda r: r["trades"][1].update(side="buy"),
                lambda r: r["trades"][0].update(fee=0),
                lambda r: r["trades"][1].update(cashAfter=500000),
                lambda r: r["trades"][1].update(date=r["trades"][0]["date"], signalDate="2023-08-23"),
                lambda r: r["research"]["feeLedger"]["paidLedger"][1].update(quoteId=10),
                lambda r: r["research"]["feeLedger"]["paidLedger"][0]["quote"]["context"].update(effective_fee_date="2023-08-23")]
            for change in mutations:
                r = fixture("2022-2023", 30)
                change(r)
                with self.assertRaises(ValueError):
                    verify_fills(r, "2022-2023", 30)

        def test_reserve_signal_day_boundary_not_future_fill_day(self):
            day, price, budget, quantity = "2022-04-28", 10 * 1.003, 10035.15, 900
            amount = quantity * price
            q = {"quoteId": 7, "symbol": "sh600001", "side": "buy", "amount": amount,
                "context": {"symbol": "sh600001", "purpose": "signalReserve", "signal_date": day,
                    "decision_date": day, "effective_fee_date": day, "execution_date": None},
                "breakdown": {"commission": 5., "transfer": amount * .00002, "stampDuty": 0., "total": 5 + amount * .00002}}
            r = {"curve": [{"date": day, "equity": budget / .2}], "trades": [],
                "research": {"feeLedger": {"quotes": [q]}, "opportunities": [{"signalId": "s", "symbol": "sh600001",
                    "signalDate": day, "plannedQuantity": quantity, "budget": budget, "reservePrice": price, "reserveQuoteId": 7}]}}
            self.assertEqual(verify_reserves(r, {("sh600001", day): 10}, "2022-2023", 30), 1)
            # 1000 shares fit only under the future lower transfer rate.
            self.assertLess(1000 * price + 5 + 1000 * price * .00001, budget)
            self.assertGreater(1000 * price + 5 + 1000 * price * .00002, budget)
            r["research"]["opportunities"][0]["plannedQuantity"] = 1000
            with self.assertRaises(ValueError):
                verify_reserves(r, {("sh600001", day): 10}, "2022-2023", 30)

        def test_config_cost_only_and_no_source_mutation(self):
            original = {"slippageBps": 10, "stopLossPct": .05, "allocation": {"strategyIds": [SID]}}
            before = deepcopy(original)
            for bps in TIERS.values():
                other = deepcopy(original)
                other["slippageBps"] = bps
                verify_cost_only_config(original, other, bps)
            for key, value in (("stopLossPct", .1), ("allocation", {"strategyIds": ["other"]})):
                other = deepcopy(original)
                other.update(slippageBps=30)
                other[key] = value
                with self.assertRaises(ValueError):
                    verify_cost_only_config(original, other, 30)
            self.assertEqual(original, before)
            strict = reference_modules("2022-2023")[0].same
            actual = deepcopy(original)
            actual["slippageBps"] = 10.0
            verify_recorded_config(actual, original, strict)
            actual["slippageBps"] = True
            with self.assertRaises(ValueError):
                verify_recorded_config(actual, original, strict)
            actual.update(slippageBps=10., stopLossPct=.1)
            with self.assertRaises(ValueError):
                verify_recorded_config(actual, original, strict)

        def test_pinned_close_selection_missing_duplicate_and_future_no_effect(self):
            source = {"securities": [{"symbol": "sh600001", "bars": [{"date": "2022-01-04", "close": 10},
                {"date": "2022-01-05", "close": 11}]}]}
            schedule = [{"symbol": "sh600001", "signalDate": "2022-01-04"}]
            initial = collect_signal_closes(source, schedule)[0]
            source["securities"][0]["bars"].append({"date": "2022-01-06", "close": 1000})
            self.assertEqual(collect_signal_closes(source, schedule)[0], initial)
            source["securities"][0]["bars"].append({"date": "2022-01-06", "close": 1000})
            with self.assertRaises(ValueError):
                collect_signal_closes(source, schedule)
            with self.assertRaises(ValueError):
                collect_signal_closes({"securities": []}, schedule)

        def test_period_reference_namespaces_are_separate(self):
            old = reference_modules("2022-2023")[-1]
            new = reference_modules("2024-2025")[-1]
            self.assertIsNot(old, new)
            self.assertEqual((old.START, old.END, old.SID), (*PERIODS["2022-2023"][:2], SID))
            self.assertEqual((new.START, new.END, new.SID), (*PERIODS["2024-2025"][:2], SID))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IndependentCostTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    require(result.wasSuccessful(), "Independent synthetic checks failed")
    return {"selfTestsPassed": True, "testsRun": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped), "marketOutcomesRead": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False), flush=True)
        return 0
    destination = args.output / "validation.json"
    require(not destination.exists(), "Successful validation already exists; will not overwrite/recompute")
    started = datetime.now(timezone.utc)
    try:
        report = verify_artifacts(args.output, progress=lambda message: print(json.dumps(message, ensure_ascii=False), flush=True))
        report["elapsedSeconds"] = (datetime.now(timezone.utc) - started).total_seconds()
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        print(json.dumps({"passed": True, "validation": str(destination), "sha256": sha(destination),
            "accounts": len(report["accounts"]), "newAccounts": 4, "reusedBaselineAccounts": 2,
            "elapsedSeconds": report["elapsedSeconds"]}, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        failure = args.output / ("validation-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        with failure.open("x", encoding="utf-8") as handle:
            json.dump({"schema": "relative-repair-cost-stress-independent-validation-v1", "passed": False,
                "failureCount": 1, "error": type(error).__name__ + ": " + str(error),
                "verifierSha256": sha(__file__), "planSha256": REGISTERED_PLAN_SHA256}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps({"passed": False, "failure": str(failure), "error": str(error)}, ensure_ascii=False), flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
