"""Attribute immutable relative-repair control accounts at 10/30/50 bps.

Uses already executed accounts, never recomputes signals or simulates costs by
deducting a fee from baseline profits. Every original entry event is retained.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/relative-repair-cost-stress-v1"
PERIODS = ("2022-2023", "2024-2025")
COSTS = {"control10": 10, "control30": 30, "control50": 50}
HELPER_PATH = ROOT / "scripts/summarize-relative-repair-events.py"
HELPER_SHA = "5292d3c585f639f6895dc3c9c37a60ed5b16ff46734a5a8f1a08a0e16a691922"


def helper():
    spec = importlib.util.spec_from_file_location("_relative_repair_cost_event_reference", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.require(module.sha(HELPER_PATH) == HELPER_SHA, "Frozen attribution helper changed")
    return module


def execution_views(result, attributed, ref):
    """One view per original signal; prices and fills come from this account."""
    positions = {}
    for event in attributed.values():
        for kind, key in (("closed", "closedEpisodes"), ("open", "openPositions")):
            for record in event[key]:
                sid = record["signalId"]
                ref.require(sid not in positions, "Signal has multiple final episodes")
                positions[sid] = (kind, record)
    buys = {r["signalId"]: r for r in result["trades"] if r["side"] == "buy"}
    ref.require(len(buys) == sum(r["side"] == "buy" for r in result["trades"]), "Repeated signal entry")
    views = {}
    for row in result["research"]["opportunities"]:
        sid = row["signalId"]
        ref.require(sid not in views, "Duplicate opportunity")
        view = {key: row.get(key) for key in ("symbol", "signalDate", "originalRank", "status", "reason",
            "attemptDate", "plannedQuantity", "unfilledQuantity", "budget", "reservePrice")}
        view["filledQuantity"] = row.get("filledQuantity", 0)
        view["entryFill"] = None
        view["terminalDisposition"] = "notEntered"
        view["episode"] = None
        if view["filledQuantity"] > 0:
            ref.require(sid in buys and sid in positions, "Entry or episode missing")
            view["entryFill"] = {key: buys[sid].get(key) for key in
                ("date", "price", "openingPrice", "quantity", "amount", "fee", "slippage", "cashAfter")}
            kind, item = positions[sid]
            view["terminalDisposition"] = kind
            record = item["record"]
            if kind == "closed":
                view["episode"] = {key: record.get(key) for key in
                    ("entryDate", "exitDate", "pnlAmount", "investedAmount", "holdingDays")}
                view["episode"]["netReturnPct"] = item["netReturnPct"]
            else:
                view["episode"] = {key: record.get(key) for key in
                    ("entryDate", "quantity", "costBasis", "marketValue", "priceDate")}
                view["episode"].update(unrealizedPnlAmount=item["unrealizedPnlAmount"],
                                       realizedPnlWhileOpen=item["realizedPnlWhileOpen"])
        else:
            ref.require(sid not in buys and sid not in positions, "Unfilled signal has an episode")
        views[sid] = view
    return views


def compare_execution(baseline, stressed, membership, event_ids, ref):
    ref.require(set(baseline) == set(stressed), "Cost scenario changed signal identities")
    labels = ("bothEntered", "baselineOnlyEntered", "stressOnlyEntered", "neitherEntered",
              "statusChanged", "quantityChanged", "entryDateChanged", "entryPriceChanged",
              "terminalDispositionChanged", "closedExitDateChanged", "anyRecordedPathChange")
    totals = {key: 0 for key in labels}
    events = {event: {"opportunities": 0, "counts": {key: 0 for key in labels}, "signals": []}
              for event in event_ids}
    for sid, before in baseline.items():
        after = stressed[sid]
        ref.require(all(before[k] == after[k] for k in ("symbol", "signalDate", "originalRank")),
                    "Cost scenario changed signal fields")
        event = membership[sid]
        a, b = before["filledQuantity"] > 0, after["filledQuantity"] > 0
        disposition = ("bothEntered" if a and b else "baselineOnlyEntered" if a else
                       "stressOnlyEntered" if b else "neitherEntered")
        changes = [disposition]
        if before["status"] != after["status"]:
            changes.append("statusChanged")
        if before["filledQuantity"] != after["filledQuantity"]:
            changes.append("quantityChanged")
        if a and b:
            if before["entryFill"]["date"] != after["entryFill"]["date"]:
                changes.append("entryDateChanged")
            if before["entryFill"]["price"] != after["entryFill"]["price"]:
                changes.append("entryPriceChanged")
        if before["terminalDisposition"] != after["terminalDisposition"]:
            changes.append("terminalDispositionChanged")
        if before["terminalDisposition"] == after["terminalDisposition"] == "closed":
            if before["episode"]["exitDate"] != after["episode"]["exitDate"]:
                changes.append("closedExitDateChanged")
        if before != after:
            changes.append("anyRecordedPathChange")
        event_row = events[event]
        event_row["opportunities"] += 1
        for label in changes:
            totals[label] += 1
            event_row["counts"][label] += 1
        event_row["signals"].append({"signalId": sid, "eventId": event, "changes": changes,
                                    "baseline10": deepcopy(before), "stressed": deepcopy(after)})
    ref.require(sum(totals[k] for k in labels[:4]) == len(baseline), "Execution comparison partition differs")
    return {"baseline": "control10", "opportunities": len(baseline), "counts": totals, "events": events,
            "meaning": "Observed account paths at fixed signal slots; no subtraction-only cost approximation.",
            "quantityChangeBasis": "includes zero-versus-positive entry differences; no forced matched fills"}


def summarize(directory, ref):
    ref.require(not (directory / "failure.json").exists(), "Failed experiment cannot be summarized")
    ref.require((directory / "comparison.json").is_file(), "All four new accounts must finish first")
    plan = ref.read(directory / "plan.json")
    comparison = ref.read(directory / "comparison.json")
    ref.require(plan["schema"] == "relative-repair-cost-stress-v1"
                and comparison["schema"] == "relative-repair-cost-comparison-v1",
                "Wrong experiment schema")
    ref.require(plan["newAccounts"] == comparison["newAccounts"] == 4
                and plan["reusedBaselineAccounts"] == comparison["reusedBaselineAccounts"] == 2,
                "Expected four new accounts and two reused references")
    ref.require(comparison["planSha256"] == ref.sha(directory / "plan.json"), "Plan binding changed")
    pairs = {(r["period"], r["id"]) for r in comparison["rows"]}
    ref.require(len(comparison["rows"]) == 6 and pairs == {(p, c) for p in PERIODS for c in COSTS},
                "Expected two full periods at all three costs")
    compared = {(r["period"], r["id"]): r for r in comparison["rows"]}
    files = {}

    def bind(path, expected=None):
        path = path.resolve()
        ref.require(path.is_relative_to(ROOT), "Input must remain in workspace")
        value = ref.sha(path)
        if expected is not None:
            ref.require(value == expected, "Input hash differs: " + str(path))
        files[path] = value
        return path

    def frozen(path):
        path = path.resolve()
        name = path.relative_to(ROOT).as_posix()
        ref.require(name in plan["frozenInputs"], "Parent input missing frozen pin: " + name)
        return bind(path, plan["frozenInputs"][name])

    def reference(value):
        return bind(ROOT / value["path"], value["sha256"])

    def account_file(period, name):
        path = directory / period / name
        # The completion manifest binds newly produced account files.
        expected = comparison["artifacts"][period][name]
        return bind(path, expected)

    bind(directory / "plan.json")
    bind(directory / "comparison.json")
    bind(HELPER_PATH, HELPER_SHA)
    output = {"schema": "relative-repair-cost-event-outcomes-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(), "scriptSha256": ref.sha(Path(__file__)),
        "reusedAttributionHelperSha256": HELPER_SHA, "periods": {},
        "costBpsPerSide": COSTS, "accountsReused": 2, "accountsNewlyExecutedByRunner": 4,
        "accountsExecutedByThisScript": 0, "signalsRecomputed": False,
        "interpretation": "Entry-event attribution from independently executed cost accounts, retaining all original events.",
        "limitations": ["Event closed means are trade averages, never event portfolio returns or compound returns.",
            "Costs can change lot sizing, cash, entry availability and exits; actual account paths are compared by unchanged signalId.",
            "Closed PnL excludes terminal open unrealized and realized-while-open PnL; reconciliation residual stays unallocated.",
            "Both previously inspected historical periods remain included; no positive-period or event selection.",
            "Frozen raw-price, universe, source-provenance and valuation limitations remain; no signal replay or raw reload here.",
            "Matching two frozen covariates does not establish causality; this diagnostic does not certify profitability."],
        "certified": False, "targetAchieved": False}
    for period in PERIODS:
        spec = plan["periods"][period]
        local_reference = ref.read(account_file(period, "baseline-reference.json"))
        for key in ("result", "summary", "signals", "source"):
            ref.require(local_reference[key] == spec["baseline"][key], "Baseline reference differs: " + key)
        baseline_paths = {key: reference(spec["baseline"][key]) for key in ("result", "summary", "signals", "source")}
        parent_period = baseline_paths["result"].parent
        parent = parent_period.parent
        evidence_path = frozen(parent_period / "signals-evidence.json.gz")
        old_events_path = frozen(parent / "event-outcomes.json")
        # Preserve parent certification and contextual binding without loading the raw projection.
        for name in ("plan.json", "comparison.json", "validation.json"):
            frozen(parent / name)
        readouts = [name for name in plan["frozenInputs"]
                    if (ROOT / name).parent == parent and Path(name).name.startswith("readout")
                    and Path(name).suffix == ".json"]
        ref.require(readouts, "Missing frozen parent readout")
        for name in readouts:
            frozen(ROOT / name)
        projection = spec["sourceProjection"]
        ref.require(plan["frozenInputs"].get(projection["path"]) == projection["sha256"],
                    "Inherited projection identity differs from frozen pin")
        evidence = ref.read(evidence_path)
        old_events = ref.read(old_events_path)["periods"][period]
        events, membership = ref.event_structure(evidence)
        event_ids = [e["eventId"] for e in events]
        ref.require(event_ids == [e["eventId"] for e in old_events["events"]], "Original event coverage differs")
        schedule_path = account_file(period, "signals.json")
        ref.require(schedule_path.read_bytes() == baseline_paths["signals"].read_bytes(), "Signals are not original bytes")
        schedule = ref.read(schedule_path)
        ref.require(schedule == evidence["schedules"]["matchedControl"], "Fixed control signal schedule changed")
        source = ref.read(account_file(period, "source.json"))
        ref.require(source == ref.read(baseline_paths["source"]) == spec["sourceIdentity"], "Source identity changed")
        accounts, views = {}, {}
        for identifier, bps in COSTS.items():
            row = compared[(period, identifier)]
            ref.require(row["slippageBps"] == bps and row["reused"] is (identifier == "control10"),
                        "Cost/reuse label differs")
            if identifier == "control10":
                result = ref.read(baseline_paths["result"])
                summary = ref.read(baseline_paths["summary"])
                policy_id = "matchedControl"
            else:
                result = ref.read(account_file(period, identifier + ".result.json.gz"))
                summary = ref.read(account_file(period, identifier + ".summary.json"))
                policy_id = identifier
            expected_config = deepcopy(spec["config"])
            expected_config["slippageBps"] = float(bps)
            ref.require(result["config"] == spec["configs"][identifier] == expected_config,
                        "Cost scenario changed settings beyond slippage")
            attributed, totals = ref.attribute_account(policy_id, schedule, membership, event_ids, result, summary)
            for event in events:
                event["groups"][identifier] = attributed[event["eventId"]]
            if identifier == "control10":
                ref.require(totals == old_events["accounts"]["matchedControl"], "Original 10bps account attribution changed")
                for old_event in old_events["events"]:
                    ref.require(attributed[old_event["eventId"]] == old_event["groups"]["matchedControl"],
                                "Original 10bps event attribution changed")
            for field, expected in (("signals", len(schedule)), ("closed", totals["closedCount"]),
                                    ("openPositions", totals["openCountAtCutoff"])):
                ref.require(row[field] == expected, "Comparison count differs: " + field)
            ref.near(row["accountReturnPct"], totals["accountReturnPct"], "Comparison return differs")
            ref.near(row["closedMeanPct"], totals["meanClosedNetReturnPct"], "Comparison closed mean differs")
            ref.near(row["maxDrawdownPct"], summary["account"]["maxDrawdownPct"], "Comparison drawdown differs")
            views[identifier] = execution_views(result, attributed, ref)
            totals.update(id=identifier, slippageBps=bps, reused=identifier == "control10",
                          feesPaid=summary["account"]["feesPaid"],
                          slippageAlreadyInFillPrices=summary["account"]["slippageAlreadyInFillPrices"],
                          maxDrawdownPct=summary["account"]["maxDrawdownPct"])
            accounts[identifier] = totals
        comparisons = {identifier: compare_execution(views["control10"], views[identifier], membership, event_ids, ref)
                       for identifier in ("control30", "control50")}
        for identifier, comparison_row in comparisons.items():
            comparison_row["costId"] = identifier
            comparison_row["slippageBps"] = COSTS[identifier]
        output["periods"][period] = {"eventCount": len(events),
            "endReasonCounts": dict(Counter(e["endReason"] for e in events)),
            "originalControlSignalCount": len(schedule), "events": events,
            "accounts": accounts, "executionComparisons": comparisons}
    ref.require(sum(p["eventCount"] for p in output["periods"].values()) == 9, "Must preserve all nine frozen events")
    ref.require(all(ref.sha(path) == value for path, value in files.items()), "Input changed during attribution")
    output["inputArtifacts"] = {path.relative_to(ROOT).as_posix(): value for path, value in files.items()}
    output["upstreamFrozenPins"] = deepcopy(plan["frozenInputs"])
    output["sourceProjectionRead"] = False
    return output


def self_test(ref):
    ref.self_test()
    keys = ("both", "base", "stress", "none")
    base, stressed = {}, {}
    for key in keys:
        for output, entered in ((base, key in ("both", "base")), (stressed, key in ("both", "stress"))):
            output[key] = {"symbol": key, "signalDate": "2024-01-02", "originalRank": 1,
                "status": "filled" if entered else "notPlanned", "filledQuantity": 100 if entered else 0,
                "entryFill": {"date": "2024-01-03", "price": 10} if entered else None,
                "terminalDisposition": "closed" if entered else "notEntered",
                "episode": {"exitDate": "2024-01-10"} if entered else None}
    stressed["both"]["entryFill"]["price"] = 10.02
    stressed["both"]["filledQuantity"] = 200
    stressed["both"]["episode"]["exitDate"] = "2024-01-11"
    before = deepcopy((base, stressed))
    result = compare_execution(base, stressed, dict.fromkeys(keys, "event1"), ["event1", "event2"], ref)
    for label in ("bothEntered", "baselineOnlyEntered", "stressOnlyEntered", "neitherEntered",
                  "entryPriceChanged", "closedExitDateChanged"):
        ref.require(result["counts"][label] == 1, "Synthetic execution change differs")
    ref.require(result["counts"]["quantityChanged"] == 3 and result["events"]["event2"]["opportunities"] == 0,
                "Quantity changes or empty event dropped")
    ref.require((base, stressed) == before, "Input mutated")
    rejected = 0
    for bad in ({k: v for k, v in stressed.items() if k != "none"},
                {**stressed, "both": {**stressed["both"], "originalRank": 2}}):
        try:
            compare_execution(base, bad, dict.fromkeys(keys, "event1"), ["event1"], ref)
        except ValueError:
            rejected += 1
    ref.require(rejected == 2, "Schedule drift not rejected")
    return {"status": "passed", "frozenHelperSelfTests": True, "executionPartitionCases": 4,
            "intentionalRejections": rejected, "emptyEventPreserved": True, "realArtifactsRead": False}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    ref = helper()
    if args.self_test:
        print(json.dumps(self_test(ref)))
        return
    destination = args.directory / "event-outcomes.json"
    ref.require(not destination.exists(), "Refusing to overwrite prior event outcomes")
    output = summarize(args.directory, ref)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "passed", "accounts": 6, "originalEvents": 9,
                      "output": str(destination)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
