"""Read eighteen audited exit-policy accounts; never rebuild signals or accounts."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/reclaim-exit-transfer-v1"
HELPER = ROOT / "scripts/summarize-board-coverage-reclaim.py"
METRICS = ROOT / "scripts/summarize-volume-anchor-acceptance.py"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("baseline", "trail10", "structure10")
SID = "strongUndercutReclaimV1"
CONTRASTS = (("trail10VsBaseline", "baseline", "trail10"),
             ("structure10VsTrail10", "trail10", "structure10"),
             ("structure10VsBaseline", "baseline", "structure10"))
STATUSES = ("triggered", "notTriggered", "unknown", "skippedExistingExit")
COUNTS = {"newAccounts": 12, "newSetupMechanisms": 0, "newEntryPolicies": 0,
          "newExitDefinitions": 0, "transferredExitPolicies": 2,
          "newUniverseVariants": 0, "referenceAccountsReused": 6}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


BOARD = module(HELPER, "reclaim_exit_summary_board")
METRIC = module(METRICS, "reclaim_exit_summary_metrics")
require, close, finite = BOARD.require, BOARD.close, BOARD.finite
read, records, sha, location = BOARD.read, BOARD.records, BOARD.sha, BOARD.location
total, labels = BOARD.total, BOARD.labels


def exit_evidence(result, mode, start=None, end=None):
    expected = {"baseline": "noProgress5", "trail10": None, "structure10": "structure10"}[mode]
    extra = result["research"].get("structuralExits")
    if expected is None:
        require(extra is None, "trail10 must have no fifth-session or structural adapter")
        selected = []
    else:
        require(extra is not None and extra["rule"] == expected, "Extra exit adapter differs")
        all_rows = extra["observations"]
        observed = Counter(r["status"] for r in all_rows)
        require(set(observed) <= set(STATUSES) and extra["observationCounts"] == {k: observed[k] for k in STATUSES},
                "Extra exit observation counts differ")
        selected = [r for r in all_rows if (start is None or start <= r["date"]) and (end is None or r["date"] <= end)]
    counts = Counter(r["status"] for r in selected)
    return {"configured": expected is not None, "rule": expected, "observationCount": len(selected),
        "observationCounts": {k: counts[k] for k in STATUSES},
        "unknownReasonCounts": labels(u["reason"] for r in selected for u in r.get("unknown", [])),
        "uniqueObservedHoldingEpisodes": len({(r["symbol"], r["entryDate"]) for r in selected}),
        "uniqueUnknownHoldingEpisodes": len({(r["symbol"], r["entryDate"]) for r in selected if r["status"] == "unknown"}),
        "meaning": "Saved close-time extra-rule observations; absent adapter is not complete evidence. Unknown or skipped is not trend survival or a successful nontrigger. Standard exits keep priority."}


def attach_dispositions(original, item, mode, calendar, target_dates):
    require(len(original) == len(item["opportunities"]), "Opportunity count changed")
    indexes = {day: i for i, day in enumerate(calendar)}
    for formed, saved in zip(original, item["opportunities"]):
        require(formed["formationId"] == saved["formationId"] and saved["formationEvidence"] ==
                {k: v for k, v in formed.items() if k not in ("period", "board", "plannedNextEntryDate", "nextEntryWithinPeriod", "accountDispositions")},
                "Original formation evidence changed")
        index = indexes[formed["formationDate"]]
        next_day = calendar[index + 1] if index + 1 < len(calendar) else None
        formed.update(period=item["account"]["period"], board=item["account"]["board"],
                      plannedNextEntryDate=next_day, nextEntryWithinPeriod=next_day in target_dates)
        formed.setdefault("accountDispositions", {})[mode] = {**deepcopy(saved["accountDisposition"]),
            "formationId": formed["formationId"], "formationDate": formed["formationDate"],
            "strategyId": SID, "decisionDate": saved["signal"]["signalDate"],
            "plannedNextEntryDate": next_day, "nextEntryWithinPeriod": next_day in target_dates,
            "accountOpportunity": deepcopy(saved["accountOpportunity"]),
            "openPositionAtCutoff": deepcopy(saved["openPositionAtCutoff"])}


def summarize_account(period, board, mode, config, original, schedule, metadata, result, summary, row, phases, label):
    source_mode = "immediate" if mode == "baseline" else mode
    require(summary["policyId"] == source_mode and (row["period"], row["board"], row["id"]) == (period, board, mode),
            "Original/display account identity differs")
    require(schedule == [r["signals"]["immediate"] for r in original]
            and all(s["strategyId"] == SID for s in schedule), "Original immediate schedule changed")
    # Normalize display-only names for the immutable board accounting helper;
    # the original summary, result, config and five-field schedule are preserved.
    normalized_summary = {**summary, "policyId": "immediate"}
    normalized_row = {**row, "id": "immediate", "policy": "immediate"}
    item = BOARD.summarize_loaded_account(period, board, config, original, schedule, metadata,
        result, normalized_summary, normalized_row, phases)
    account = item["account"]
    account.update(id=mode, policy=mode, label=label, comparisonRow=deepcopy(row), sourcePolicyId=source_mode,
        sourcePolicyLabel=summary["policyLabel"], sourceStrategyId=SID,
        execution="reused-frozen-account" if mode == "baseline" else "new-independent-account",
        effectiveConfig=deepcopy(config), exitEvidence=exit_evidence(result, mode))
    final_equity = account["account"]["finalEquity"]
    unrealized = account["reconciliation"]["openPositionUnrealizedPnl"]
    account["openUnrealizedPnlPctOfFinalEquity"] = unrealized / final_equity * 100 if finite(final_equity) and final_equity > 0 else None
    account["openUnrealizedContributionMeaning"] = "Unrealized amount divided by terminal equity, not share of net profit or realized return."
    item["coverage"].update(id=mode, policy=mode, exitEvidence=deepcopy(account["exitEvidence"]),
        sellFillReasons=deepcopy(account["curveAttribution"]["sellFillReasons"]),
        queuedExitReasons=deepcopy(account["curveAttribution"]["queuedExitReasons"]),
        openUnrealizedPnlAmount=unrealized,
        openUnrealizedPnlPctOfFinalEquity=account["openUnrealizedPnlPctOfFinalEquity"])
    for key in ("yearRows", "halfYearRows", "phaseRows", "curveEpochRows"):
        for part in item[key]:
            part["policy"] = mode
            part["exitEvidence"] = exit_evidence(result, mode, part["requestedStart"], part["requestedEnd"])
    item["reconciliation"]["policy"] = mode
    return item


def metric_values(account):
    values = METRIC.metric_values(account)
    values["openUnrealizedPnlPctOfFinalEquity"] = account["openUnrealizedPnlPctOfFinalEquity"]
    return values


def compare_accounts(accounts):
    indexed = {(r["period"], r["board"], r["policy"]): metric_values(r) for r in accounts}
    require(len(indexed) == len(accounts) == 18 and set(indexed) == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES},
            "All eighteen account policies required")
    rows = []
    for p in PERIODS:
        for b in BOARDS:
            for name, control, candidate in CONTRASTS:
                left, right = indexed[(p, b, control)], indexed[(p, b, candidate)]
                require(left.keys() == right.keys(), "Contrast metric coverage differs")
                rows.append({"period": p, "board": b, "id": name, "control": control, "candidate": candidate,
                    "controlMetrics": left, "candidateMetrics": right,
                    "candidateMinusControl": {k: right[k] - left[k] if finite(right[k]) and finite(left[k]) else None for k in sorted(left)},
                    "meaning": "Same original entry schedules, different complete exit-policy accounts; positions, subsequent admissions and capital paths differ. Not paired-trade causal attribution."})
    return rows


def build(directory=DEFAULT):
    directory = Path(directory).resolve()
    output, sidecar = directory / "readout.json", directory / "opportunity-dispositions.jsonl.gz"
    require(not output.exists() and not sidecar.exists(), "Never overwrite readout or sidecar")
    pins = {}
    def pin(path):
        path = Path(path).resolve(); digest = sha(path)
        require(pins.setdefault(location(path), digest) == digest, "Input changed: " + location(path))
        return path
    def bound(ref):
        require(isinstance(ref, dict) and set(ref) >= {"path", "sha256"}, "Invalid artifact reference")
        path = Path(ref["path"]); path = pin(path if path.is_absolute() else ROOT / path)
        require(pins[location(path)] == ref["sha256"], "Hash binding differs: " + ref["path"])
        return path
    names = ("plan.json", "comparison.json", "input-completion.json", "input-validation.json", "validation.json")
    files = {n: pin(directory / n) for n in names}
    for path in (Path(__file__), HELPER, METRICS, ATLAS): pin(path)
    require(sha(ATLAS) == ATLAS_SHA, "Atlas changed")
    plan, comparison, completion, input_proof, proof = (read(files[n]) for n in names)
    plan_sha = sha(files["plan.json"])
    BOARD.validation_binding(proof, plan_sha, sha(files["comparison.json"]))
    require(plan["schema"] == "reclaim-exit-transfer-v1" and plan["reportModes"] == list(MODES), "Wrong experiment family/policies")
    require(comparison["planSha256"] == completion["planSha256"] == input_proof["planSha256"] == plan_sha
        and input_proof["passed"] and input_proof["inputCompletionSha256"] == sha(files["input-completion.json"])
        and proof["inputValidationSha256"] == sha(files["input-validation.json"])
        and proof["inputCompletionSha256"] == sha(files["input-completion.json"]), "Input/account proof chain differs")
    require(completion["allSchedulesFrozenBeforeAccounts"] is True and proof["accountArtifacts"] == comparison["artifacts"], "Account artifacts or schedule freeze differ")
    for key, expected in COUNTS.items(): require(plan[key] == comparison[key] == expected, "Trial scope differs: " + key)
    for key in ("sourcePins", "inputPins"):
        for name, digest in plan[key].items(): bound({"path": name, "sha256": digest})
    parent = bound(plan["baselinePlan"]).parent
    old_comparison = read(bound(plan["baselineComparison"]))
    old_report = read(pin(parent / "readout.json")); old_proof = read(pin(parent / "readout-validation.json"))
    require(old_proof["passed"] and old_proof["readoutSha256"] == sha(parent / "readout.json")
        and old_proof["comparisonSha256"] == sha(parent / "comparison.json"), "Parent readout proof differs")
    matrix = [(p, b, m) for p in PERIODS for b in BOARDS for m in MODES]
    rows = comparison["rows"]; by_row = {(r["period"], r["board"], r["id"]): r for r in rows}
    require(len(rows) == len(by_row) == 18 and list(by_row) == matrix, "Complete ordered account matrix required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == 16 and all(p["labelAvailability"] == "retrospectiveResearchOnly" for p in phases), "Original retrospective phases required")
    accounts, coverage, formation, years, halves, phase_rows, epochs, partitions, opportunities = ([] for _ in range(9))
    bindings, input_counts = {}, {}
    for period in PERIODS:
        bindings[period], input_counts[period] = {}, {}
        for board in BOARDS:
            receipt = read(bound(completion["outputs"][period][board]))
            source_input = read(bound(plan["baselineInputs"][period][board]))
            require(receipt["planSha256"] == plan_sha and receipt["period"] == period and receipt["board"] == board
                and receipt["files"] == source_input["files"], "Binding receipt differs from original inputs")
            inputs = {name: bound(ref) for name, ref in receipt["files"].items()}
            original = records(inputs["opportunities.jsonl.gz"])
            schedule = read(bound(plan["baselineSchedules"][period][board]))
            metadata, counts = read(inputs["signal-metadata.json"]), read(inputs["input-counts.json"])
            require(schedule == [r["signals"]["immediate"] for r in original]
                and counts == metadata["counts"] == source_input["counts"]
                and counts["opportunityCount"] == counts["signals"]["immediate"] == len(original)
                and receipt["signalCount"] == len(schedule), "Original signal population differs")
            pristine = deepcopy(original)
            parent_cov = next(r for r in old_report["coverageRows"] if (r["period"], r["board"]) == (period, board))
            formation.append({"period": period, "board": board, "formationTop5": len(original),
                "sourceImmediateSignals": len(schedule), "sameScheduleForEveryPolicy": True,
                "sourceCohort": deepcopy(parent_cov["sourceCohort"]),
                "formationEvidenceTotals": deepcopy(parent_cov["formationEvidenceTotals"]),
                "benchmarkUnavailableDays": parent_cov["benchmarkUnavailableDays"],
                "benchmarkReasonCounts": deepcopy(parent_cov["benchmarkReasonCounts"]),
                "parentReadout": {"path": location(parent / "readout.json"), "sha256": sha(parent / "readout.json")}})
            for mode in MODES:
                refs = comparison["artifacts"][period][board][mode]
                source_mode = "immediate" if mode == "baseline" else mode
                if mode == "baseline":
                    require(refs == plan["baselineArtifacts"][period][board], "Baseline bytes differ")
                    old_row = next(r for r in old_comparison["rows"] if (r["period"], r["board"]) == (period, board))
                    require(by_row[(period, board, mode)] == {**old_row, "id": mode, "policy": mode, "label": plan["labels"][mode]}, "Baseline display metadata differs")
                artifacts = {name: bound(ref) for name, ref in refs.items()}
                result, summary = read(artifacts[source_mode + ".result.json.gz"]), read(artifacts[source_mode + ".summary.json"])
                require(result["research"]["identity"]["signalSchedule"] == schedule, "Every policy must use identical five-field signals")
                config = plan["periods"][period]["policyConfigs"][mode]
                item = summarize_account(period, board, mode, config, pristine, schedule, metadata,
                    result, summary, by_row[(period, board, mode)], phases, plan["labels"][mode])
                item["account"].update(artifactRefs=deepcopy(refs), sourceBinding=deepcopy(plan["sourceData"][period][board]))
                attach_dispositions(original, item, mode, metadata["calendar"], metadata["targetDates"])
                accounts.append(item["account"]); coverage.append(item["coverage"]); partitions.append(item["reconciliation"])
                years.extend(item["yearRows"]); halves.extend(item["halfYearRows"]); phase_rows.extend(item["phaseRows"]); epochs.extend(item["curveEpochRows"])
                del result, summary, item
            opportunities.extend(original); input_counts[period][board] = deepcopy(counts)
            bindings[period][board] = {**deepcopy(plan["sourceData"][period][board]), "inputCompletion": deepcopy(completion["outputs"][period][board]),
                "baselineInput": deepcopy(plan["baselineInputs"][period][board]), "baselineSchedule": deepcopy(plan["baselineSchedules"][period][board]),
                "baselineArtifacts": deepcopy(plan["baselineArtifacts"][period][board]), "evidenceArtifacts": deepcopy(receipt["files"])}
    require((len(accounts), len(coverage), len(formation), len(years), len(halves), len(phase_rows), len(epochs), len(partitions))
        == (18, 18, 6, 36, 72, 144, 18, 18), "Complete matrix lost")
    require(len(opportunities) == len({(r["period"], r["board"], r["formationId"]) for r in opportunities})
        == sum(r["formationTop5"] for r in formation) == proof["counts"]["sourceImmediateOpportunities"], "Original population lost")
    comparisons = compare_accounts(accounts)
    report = {"schema": "reclaim-exit-transfer-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "inputPins": pins, "sourceBindings": bindings,
        "verification": {"passed": True, "path": location(files["validation.json"]), "sha256": sha(files["validation.json"]), "meaning": "Independent account audit and complete saved-ledger readout; not strategy certification."},
        "method": {"experiment": "Existing exits transferred to six UR sources; no new exit definition, entry signal, board or parameter search.",
            "contrasts": "trail10-baseline removes only day5; structure10-trail10 replaces the10% trail with prior10-slot low breach while both retain5% initial stop and no day5.",
            "naming": "trail10 here retains5% initial stop; the older early-strength same-name policy had no initial stop and is not this account.",
            "account": "Six original immediate accounts reused byte-for-byte and twelve new independent500000 accounts. Same SID and all five signal fields, no filtering/backfill.",
            "interval": "Prior curve close starts each interval; carried positions and unrealized changes retained. Amounts add, percentages compound.9/24 is descriptive, not an entry date gate.",
            "closure": "Closed episodes condition on execution and closure. Open positions, execution nonentry and unknown extra-exit evidence remain separate.",
            "extraExits": "Read saved observations only; structure10 uses exact ten prior calendar slots, unknowns cannot trigger it. Standard/queued exits keep priority. Missing adapter is not evidence completeness.",
            "unrealized": "openUnrealizedPnlPctOfFinalEquity=100*terminal unrealized PnL/final equity if positive; not share of profit.",
            "benchmark": "Any relative/excess return uses sh000300, not board-matched or beta-neutral. Boards are not pure technology industries.",
            "compatibility": "minListingDays120/minAmount20 remain compatibility configuration, not extra fixed-signal entry gates.",
            "accountsRerunByThisScript": 0, "signalsGenerated": 0, "profitHurdles": None},
        "accounts": accounts, "accountRows": deepcopy(rows), "comparisons": comparisons,
        "primaryComparisons": [r for r in comparisons if r["id"] == CONTRASTS[0][0]],
        "secondaryComparisons": [r for r in comparisons if r["id"] == CONTRASTS[1][0]],
        "overallComparisons": [r for r in comparisons if r["id"] == CONTRASTS[2][0]],
        "yearRows": years, "halfYearRows": halves, "phaseRows": phase_rows, "curveEpochRows": epochs,
        "coverageRows": coverage, "formationCoverageRows": formation, "inputCountsByPeriodBoard": input_counts, "partitionReconciliation": partitions,
        "trialAccounting": {"experimentBatches": 1, **{k: v for k, v in COUNTS.items() if k != "newAccounts"}, "newAccountExecutions": 12, "summarizerAccountExecutions": 0},
        "limitations": list(dict.fromkeys(plan["limitations"] + [
            "Unknown/stale held prices can prevent a structural trigger; these holdings are not evidence of a durable trend and remain in equity/quality/open-position denominators.",
            "Changing exits changes later capital and admissions; the same signal schedule does not produce matched realized trades or causal isolated trade effects.",
            "Historical samples were already inspected; no holdout, winner, profit hurdle or stable small-loss/large-win claim."])),
        "decision": {"selectedBoard": None, "selectedPolicy": None, "automaticNextStrategy": None, "profitHurdle": None}, "certified": False, "targetAchieved": False}
    for name, digest in pins.items(): require(sha(ROOT / name) == digest, "Input changed while summarizing: " + name)
    with gzip.open(sidecar, "xt", encoding="utf-8", compresslevel=1) as stream:
        for row in opportunities: stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    report["opportunityRowsArtifact"] = {"path": location(sidecar), "sha256": sha(sidecar), "count": len(opportunities),
        "format": "jsonl+gzip", "identity": ["period", "board", "formationId"], "policiesPerRow": list(MODES)}
    with output.open("x", encoding="utf-8") as stream: json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2); stream.write("\n")
    return {"passed": True, "path": location(output), "sha256": sha(output), "accounts": 18, "comparisons": 18, "opportunities": len(opportunities)}


def self_test():
    fixtures = []
    original_adapter = BOARD.summarize_loaded_account
    def capture(*args):
        fixtures.append(deepcopy(args))
        return original_adapter(*args)
    try:
        BOARD.summarize_loaded_account = capture
        inherited = BOARD.self_test()
    finally:
        BOARD.summarize_loaded_account = original_adapter
    require(inherited["passed"] and inherited["filesWritten"] == 0, "Frozen interval synthetic checks failed")
    require(len(fixtures) == 6, "Six pure board fixtures required")
    complete = []
    for args in fixtures:
        p, b, config, original, schedule, metadata, result, summary, row, phases = args
        for mode in MODES:
            trial_result, trial_summary, trial_row, trial_config = map(deepcopy, (result, summary, row, config))
            trial_config.update(stopLossPct=.05, trailingStopPct=0 if mode == "structure10" else .1)
            trial_summary["policyId"] = "immediate" if mode == "baseline" else mode
            trial_row.update(id=mode, policy=mode)
            if mode != "trail10":
                trial_result["research"]["structuralExits"] = {"rule": "noProgress5" if mode == "baseline" else mode,
                    "observations": [], "observationCounts": {s: 0 for s in STATUSES}}
            before = deepcopy((trial_result, trial_summary, trial_row, trial_config))
            item = summarize_account(p, b, mode, trial_config, original, schedule, metadata, trial_result,
                trial_summary, trial_row, phases, mode)
            require(item["account"]["sourcePolicyId"] == trial_summary["policyId"]
                and item["account"]["effectiveConfig"] == trial_config
                and item["account"]["exitEvidence"]["configured"] == (mode != "trail10")
                and item["account"]["sourceQuality"]["pointInTimeComplete"] is False
                and len(item["yearRows"]) == 2 and len(item["halfYearRows"]) == 4
                and all(part["policy"] == mode for part in item["phaseRows"])
                and before == (trial_result, trial_summary, trial_row, trial_config), "Complete adapter identity/quality/mutation differs")
            attach_dispositions([], item, mode, metadata["targetDates"], metadata["targetDates"])
            complete.append(item)
    require(len(complete) == 18 and sum(len(r["curveEpochRows"]) for r in complete) == 18, "All policy/epoch adapters required")
    result = {"research": {"structuralExits": {"rule": "structure10", "observations": [
        {"date": "2024-01-03", "symbol": "s", "entryDate": "2024-01-02", "status": state,
         "unknown": [{"reason": "missingDailyBar"}] if state == "unknown" else []} for state in STATUSES],
        "observationCounts": {s: 1 for s in STATUSES}}}}
    before = deepcopy(result); e = exit_evidence(result, "structure10")
    require(e["observationCount"] == 4 and e["unknownReasonCounts"] == {"missingDailyBar": 1}
        and e["uniqueUnknownHoldingEpisodes"] == 1 and result == before, "Exit status/unknown accounting differs")
    require(exit_evidence(result, "structure10", "2024-01-04", "2024-01-05")["observationCount"] == 0, "Exit interval boundary differs")
    absent = exit_evidence({"research": {}}, "trail10")
    require(absent["configured"] is False and absent["rule"] is None and absent["observationCount"] == 0, "Absent adapter mislabelled")
    for mode in ("baseline", "trail10"):
        try: exit_evidence(result, mode)
        except ValueError: pass
        else: raise AssertionError("Wrong adapter accepted")
    changed = deepcopy(result); changed["research"]["structuralExits"]["observationCounts"]["unknown"] = 0
    try: exit_evidence(changed, "structure10")
    except ValueError: pass
    else: raise AssertionError("Changed observation counts accepted")
    accounts = []
    for p in PERIODS:
        for b in BOARDS:
            for n, mode in enumerate(MODES):
                accounts.append({"period": p, "board": b, "policy": mode, "comparisonRow": {"accountReturnPct": n, "unknown": None},
                    "account": {"netPnlAmount": n, "finalEquity": 500000 + n}, "curveAttribution": {"investedSessionPct": 1, "feesPaid": 0,
                        "slippageAlreadyInFillPrices": 0, "buyFillCount": 0, "sellFillCount": 0,
                        "opportunitiesByDecisionDate": {"notEntered": 0}, "opportunitiesByFormationDate": {"notScheduledFromFormation": 0}},
                    "reconciliation": {"closedEpisodePnl": n, "openPositionRealizedPnl": 0, "openPositionUnrealizedPnl": 0},
                    "concentration": {"top1ProfitSharePct": None, "top5ProfitSharePct": None}, "openUnrealizedPnlPctOfFinalEquity": 0})
    contrasts = compare_accounts(accounts)
    require(len(contrasts) == 18 and [r["candidateMinusControl"]["accountReturnPct"] for r in contrasts[:3]] == [1, 1, 2]
        and all(r["candidateMinusControl"]["unknown"] is None for r in contrasts), "All18 signed/null-preserving contrasts differ")
    try: compare_accounts(accounts[:-1])
    except ValueError: pass
    else: raise AssertionError("Incomplete policy matrix accepted")
    return {"passed": True, "checks": ["frozen interval and full board synthetic suite", "all18 complete policy adapters with identity/config/quality/chronological partitions", "four exit-observation states",
        "unknown reasons and episode denominator", "close-date interval boundary", "absent adapter not complete evidence", "adapter identity mismatch rejected",
        "stored observation count mismatch rejected", "eighteen signed contrasts and null preservation", "incomplete account matrix rejected", "no input mutation"],
        "inheritedSyntheticChecks": len(inherited["checks"]), "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--directory", type=Path, default=DEFAULT); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(); print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
