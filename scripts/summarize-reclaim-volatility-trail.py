"""Read twelve audited volatility-trail accounts; never rebuild signals or accounts."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/reclaim-volatility-trail-v1"
HELPER = ROOT / "scripts/summarize-board-coverage-reclaim.py"
METRICS = ROOT / "scripts/summarize-volume-anchor-acceptance.py"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("trail10", "volatilityTrail")
SID = "strongUndercutReclaimV1"
CONTRASTS = (("volatilityTrailVsTrail10", "trail10", "volatilityTrail"),)
STATUSES = ("triggered", "notTriggered", "unknown", "skippedExistingExit", "skippedStandardExit")
WINDOWS = ("known", "unknown", "invalidDistance")
PARAMETERS = {"windowSlots": 21, "trueRangeCount": 20, "multiplier": 3, "windowEnd": "heldCloseT-1"}
COUNTS = {"newAccounts": 6, "newSetupMechanisms": 0, "newEntryPolicies": 0,
          "newExitDefinitions": 1, "transferredExitPolicies": 0,
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
    raw = result["research"].get("volatilityTrail")
    require(result["research"].get("structuralExits") is None, "No day5 or structure adapter in either policy")
    if mode == "trail10":
        require(raw is None, "Original trail10 must have no volatility adapter")
        selected = []
    else:
        require(mode == "volatilityTrail" and raw is not None
            and raw["version"] == "research-volatility-trail-v1"
            and raw["parameters"] == PARAMETERS, "Wrong volatility adapter or parameters")
        all_rows = raw["observations"]
        decisions = Counter(r["decisionStatus"] for r in all_rows)
        windows = Counter(r["window"]["windowStatus"] for r in all_rows)
        require(set(decisions) <= set(STATUSES) and set(windows) <= set(WINDOWS)
            and raw["observationCounts"] == {k: decisions[k] for k in STATUSES}
            and raw["windowCounts"] == {k: windows[k] for k in WINDOWS}, "Saved observation/window counts differ")
        selected = [r for r in all_rows if (start is None or start <= r["date"]) and (end is None or r["date"] <= end)]
    decision = Counter(r["decisionStatus"] for r in selected)
    windows = Counter(r["window"]["windowStatus"] for r in selected)
    return {"configured": mode == "volatilityTrail", "rule": "meanTRPct20x3Ratchet" if mode == "volatilityTrail" else None,
        "parameters": deepcopy(PARAMETERS) if mode == "volatilityTrail" else None,
        "observationCount": len(selected), "decisionCounts": {k: decision[k] for k in STATUSES},
        "windowCounts": {k: windows[k] for k in WINDOWS},
        "windowDecisionCounts": {w: {d: sum(r["window"]["windowStatus"] == w and r["decisionStatus"] == d for r in selected) for d in STATUSES} for w in WINDOWS},
        "windowIssueCounts": labels(issue["reason"] for r in selected for issue in r["window"].get("issues", [])),
        "decisionReasonCounts": labels(r["decisionReason"] for r in selected),
        "uniqueObservedHoldingEpisodes": len({(r["symbol"], r["entryDate"]) for r in selected}),
        "uniqueUnavailableWindowHoldingEpisodes": len({(r["symbol"], r["entryDate"]) for r in selected if r["window"]["windowStatus"] != "known"}),
        "uniqueUnknownDecisionHoldingEpisodes": len({(r["symbol"], r["entryDate"]) for r in selected if r["decisionStatus"] == "unknown"}),
        "carriedThresholdObservations": sum(r["usedCarriedThreshold"] for r in selected),
        "carriedThresholdTriggers": sum(r["usedCarriedThreshold"] and r["decisionStatus"] == "triggered" for r in selected),
        "unavailableWindowKnownThresholdObservations": sum(r["window"]["windowStatus"] != "known" and r["thresholdKnown"] for r in selected),
        "unavailableWindowWithoutThresholdObservations": sum(r["window"]["windowStatus"] != "known" and not r["thresholdKnown"] for r in selected),
        "knownWindowScaledDistancePct": BOARD.BASE.distribution(r["window"]["scaledDistancePct"] for r in selected if r["window"]["windowStatus"] == "known"),
        "knownThresholdRatchetRatio": BOARD.BASE.distribution(r["ratchetRatio"] for r in selected if r["thresholdKnown"]),
        "distributionDenominators": "Scaled-distance distribution contains only windowStatus=known and retains the source fraction (0.1 means10%); ratio distribution contains only thresholdKnown observations in riskEntryPrice units. Both are held-close observations, not unique trades; excluded unknown counts remain explicit.",
        "meaning": "Saved close-time observations only. Window availability and exit decisions are separate: an unavailable window can still trigger an inherited floor. Unknown, priority skips and absent adapter are not successful trend survival."}


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
    source_mode = mode
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
        execution="reused-frozen-account" if mode == "trail10" else "new-independent-account",
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
    require(len(indexed) == len(accounts) == 12 and set(indexed) == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES},
            "All twelve account policies required")
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
    require(plan["schema"] == "reclaim-volatility-trail-v1" and plan["reportModes"] == list(MODES), "Wrong experiment family/policies")
    require(comparison["planSha256"] == completion["planSha256"] == input_proof["planSha256"] == plan_sha
        and input_proof["passed"] and input_proof["inputCompletionSha256"] == sha(files["input-completion.json"])
        and proof["inputValidationSha256"] == sha(files["input-validation.json"])
        and proof["inputCompletionSha256"] == sha(files["input-completion.json"]), "Input/account proof chain differs")
    require(completion["allSchedulesFrozenBeforeAccounts"] is True and proof["accountArtifacts"] == comparison["artifacts"], "Account artifacts or schedule freeze differ")
    for key, expected in COUNTS.items(): require(plan[key] == comparison[key] == expected, "Trial scope differs: " + key)
    for key in ("sourcePins", "inputPins"):
        for name, digest in plan[key].items(): bound({"path": name, "sha256": digest})
    parent = bound(plan["sourcePlan"]).parent
    bound(plan["sourceComparison"]); bound(plan["baselinePlan"])
    old_comparison = read(bound(plan["baselineComparison"]))
    old_report = read(pin(parent / "readout.json")); old_proof = read(pin(parent / "readout-validation.json"))
    require(old_proof["passed"] and old_proof["readoutSha256"] == sha(parent / "readout.json")
        and old_proof["comparisonSha256"] == sha(parent / "comparison.json"), "Parent readout proof differs")
    matrix = [(p, b, m) for p in PERIODS for b in BOARDS for m in MODES]
    rows = comparison["rows"]; by_row = {(r["period"], r["board"], r["id"]): r for r in rows}
    require(len(rows) == len(by_row) == 12 and list(by_row) == matrix, "Complete ordered account matrix required")
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
                source_mode = mode
                if mode == "trail10":
                    require(refs == plan["baselineArtifacts"][period][board], "Baseline bytes differ")
                    old_row = next(r for r in old_comparison["rows"] if (r["period"], r["board"], r["id"]) == (period, board, "trail10"))
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
        == (12, 12, 6, 24, 48, 96, 12, 12), "Complete matrix lost")
    require(len(opportunities) == len({(r["period"], r["board"], r["formationId"]) for r in opportunities})
        == sum(r["formationTop5"] for r in formation) == proof["counts"]["sourceImmediateOpportunities"], "Original population lost")
    comparisons = compare_accounts(accounts)
    report = {"schema": "reclaim-volatility-trail-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "inputPins": pins, "sourceBindings": bindings,
        "verification": {"passed": True, "path": location(files["validation.json"]), "sha256": sha(files["validation.json"]), "meaning": "Independent account audit and complete saved-ledger readout; not strategy certification."},
        "method": {"experiment": "One new volatility-normalized ratchet exit compared with reused percentage-trail accounts; all original UR signals and six sources unchanged.",
            "contrasts": "volatilityTrail minus trail10 retains5% initial stop and no day5; replace10% percentage trailing with3*mean normalized TR20 from21 exact prior source slots endingT-1.",
            "account": "Six trail10 accounts reused byte-for-byte from batch36 and six new independent500000 accounts. Same SID and all five signal fields, no filtering or backfill.",
            "interval": "Prior curve close starts each interval; carried positions and unrealized changes retained. Amounts add, percentages compound.9/24 is descriptive, not an entry date gate.",
            "closure": "Closed episodes condition on execution and closure. Open positions, nonentry and unknown exit evidence remain separate.",
            "extraExits": "Window status and decision status are separate. Unknown/invalid new distances retain the previously known ratio floor, which can still trigger. Existing forced exits and new standard exits retain priority.",
            "unrealized": "openUnrealizedPnlPctOfFinalEquity=100*terminal unrealized PnL/final equity if positive; not share of profit.",
            "benchmark": "Any relative/excess return uses sh000300, not board-matched or beta-neutral. Boards are not pure technology industries.",
            "compatibility": "minListingDays120/minAmount20 are compatibility config, not extra fixed-signal gates.",
            "accountsRerunByThisScript": 0, "signalsGenerated": 0, "profitHurdles": None},
        "accounts": accounts, "accountRows": deepcopy(rows), "comparisons": comparisons,
        "primaryComparisons": [r for r in comparisons if r["id"] == CONTRASTS[0][0]],
        "yearRows": years, "halfYearRows": halves, "phaseRows": phase_rows, "curveEpochRows": epochs,
        "coverageRows": coverage, "formationCoverageRows": formation, "inputCountsByPeriodBoard": input_counts, "partitionReconciliation": partitions,
        "trialAccounting": {"experimentBatches": 1, **{k: v for k, v in COUNTS.items() if k != "newAccounts"}, "newAccountExecutions": 6, "summarizerAccountExecutions": 0},
        "limitations": list(dict.fromkeys(plan["limitations"] + [
            "Unknown/stale held prices or unavailable new volatility windows are not durable-trend evidence; prior floors may still trigger, and all such holdings remain in quality/open-position denominators.",
            "Changing exits changes later capital and admissions; the same signal schedule does not produce matched realized trades or causal isolated trade effects.",
            "Historical samples were already inspected; no holdout, winner, profit hurdle or stable small-loss/large-win claim."])),
        "decision": {"selectedBoard": None, "selectedPolicy": None, "automaticNextStrategy": None, "profitHurdle": None}, "certified": False, "targetAchieved": False}
    for name, digest in pins.items(): require(sha(ROOT / name) == digest, "Input changed while summarizing: " + name)
    with gzip.open(sidecar, "xt", encoding="utf-8", compresslevel=1) as stream:
        for row in opportunities: stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    report["opportunityRowsArtifact"] = {"path": location(sidecar), "sha256": sha(sidecar), "count": len(opportunities),
        "format": "jsonl+gzip", "identity": ["period", "board", "formationId"], "policiesPerRow": list(MODES)}
    with output.open("x", encoding="utf-8") as stream: json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2); stream.write("\n")
    return {"passed": True, "path": location(output), "sha256": sha(output), "accounts": 12, "comparisons": 6, "opportunities": len(opportunities)}


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
    require(inherited["passed"] and len(fixtures) == 6, "Frozen board synthetic suite failed")
    complete = []
    for args in fixtures:
        p, b, config, original, schedule, metadata, result, summary, row, phases = args
        for mode in MODES:
            trial_result, trial_summary, trial_row, trial_config = map(deepcopy, (result, summary, row, config))
            trial_config.update(stopLossPct=.05, trailingStopPct=0 if mode == "volatilityTrail" else .1)
            trial_summary["policyId"] = mode; trial_row.update(id=mode, policy=mode)
            if mode == "volatilityTrail":
                trial_result["research"]["volatilityTrail"] = {"version": "research-volatility-trail-v1", "parameters": deepcopy(PARAMETERS),
                    "observations": [], "observationCounts": {s: 0 for s in STATUSES}, "windowCounts": {s: 0 for s in WINDOWS}}
            before = deepcopy((trial_result, trial_summary, trial_row, trial_config))
            item = summarize_account(p, b, mode, trial_config, original, schedule, metadata, trial_result, trial_summary, trial_row, phases, mode)
            require(item["account"]["sourcePolicyId"] == mode and item["account"]["effectiveConfig"] == trial_config
                and item["account"]["exitEvidence"]["configured"] == (mode == "volatilityTrail")
                and item["account"]["sourceQuality"]["pointInTimeComplete"] is False
                and len(item["yearRows"]) == 2 and len(item["halfYearRows"]) == 4
                and all(part["policy"] == mode for part in item["phaseRows"])
                and before == (trial_result, trial_summary, trial_row, trial_config), "Complete adapter/mutation differs")
            attach_dispositions([], item, mode, metadata["targetDates"], metadata["targetDates"])
            complete.append(item)
    require(len(complete) == 12 and sum(len(r["curveEpochRows"]) for r in complete) == 12, "Twelve policy/epoch adapters required")
    observations = []
    cases = [("known", "triggered", False, .95), ("unknown", "triggered", True, .95),
        ("invalidDistance", "notTriggered", True, .95), ("unknown", "unknown", False, None),
        ("known", "skippedStandardExit", False, None), ("known", "skippedExistingExit", False, 1.05),
        ("known", "notTriggered", False, .95)]
    for w, d, carried, ratio in cases:
        observations.append({"date": "2024-01-03", "symbol": "s", "entryDate": "2024-01-02", "decisionStatus": d,
            "decisionReason": "syntheticReason", "window": {"windowStatus": w, "scaledDistancePct": .06 if w == "known" else None,
                "issues": [] if w == "known" else [{"reason": "syntheticMissing"}]},
            "ratchetRatio": ratio, "thresholdKnown": ratio is not None, "usedCarriedThreshold": carried})
    result = {"research": {"volatilityTrail": {"version": "research-volatility-trail-v1", "parameters": deepcopy(PARAMETERS),
        "observations": observations, "observationCounts": {s: sum(r["decisionStatus"] == s for r in observations) for s in STATUSES},
        "windowCounts": {s: sum(r["window"]["windowStatus"] == s for r in observations) for s in WINDOWS}}}}
    before = deepcopy(result); evidence = exit_evidence(result, "volatilityTrail")
    require(result == before and evidence["observationCount"] == 7 and evidence["carriedThresholdTriggers"] == 1
        and evidence["carriedThresholdObservations"] == 2 and evidence["unavailableWindowKnownThresholdObservations"] == 2
        and evidence["unavailableWindowWithoutThresholdObservations"] == 1
        and evidence["windowDecisionCounts"]["unknown"]["triggered"] == 1, "Unknown-window carried threshold lost")
    require(evidence["knownWindowScaledDistancePct"]["count"] == 4 and evidence["knownThresholdRatchetRatio"]["count"] == 5
        and evidence["uniqueUnavailableWindowHoldingEpisodes"] == 1 and evidence["uniqueUnknownDecisionHoldingEpisodes"] == 1,
        "Conditioned observation denominators differ")
    require(exit_evidence(result, "volatilityTrail", "2024-01-04", "2024-01-05")["observationCount"] == 0, "Close-date window differs")
    require(exit_evidence({"research": {}}, "trail10")["configured"] is False, "Absent adapter mislabelled")
    for kind in ("adapter", "decision", "window", "parameters"):
        changed = deepcopy(result)
        if kind == "decision": changed["research"]["volatilityTrail"]["observationCounts"]["unknown"] = 0
        elif kind == "window": changed["research"]["volatilityTrail"]["windowCounts"]["unknown"] = 0
        elif kind == "parameters": changed["research"]["volatilityTrail"]["parameters"]["multiplier"] = 2
        try: exit_evidence(changed, "trail10" if kind == "adapter" else "volatilityTrail")
        except ValueError: pass
        else: raise AssertionError("Invalid observation accepted: " + kind)
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
    require(len(contrasts) == 6 and all(r["candidateMinusControl"]["accountReturnPct"] == 1
        and r["candidateMinusControl"]["unknown"] is None for r in contrasts), "Six signed comparisons differ")
    try: compare_accounts(accounts[:-1])
    except ValueError: pass
    else: raise AssertionError("Incomplete matrix accepted")
    return {"passed": True, "checks": ["frozen board interval synthetic suite", "all12 complete adapters and source identity",
        "five decision and three window states", "unavailable window can trigger carried floor", "distinct conditional distribution denominators",
        "unknown episode denominators", "close-date intervals", "absent adapter distinguished", "four invalid observations rejected",
        "six signed comparisons and nulls", "incomplete matrix rejected", "no mutation"],
        "inheritedSyntheticChecks": len(inherited["checks"]), "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--directory", type=Path, default=DEFAULT); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(); print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
