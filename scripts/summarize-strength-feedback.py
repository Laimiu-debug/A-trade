"""Read audited strength-feedback policies, including six byte-reused baselines.

No market signals or accounts are run here. Formation identities are preserved
even when a new policy changes only the schedule's strategyId.
"""
from __future__ import annotations
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/strength-feedback-v1"
HELPER = ROOT / "scripts/summarize-leader-lag-followers.py"
PROTOCOL = ROOT / "docs/signal-strength-feedback-v1.json"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("baseline", "known", "positive")
NEW_MODES = ("known", "positive")
SID, BASELINE_SID = "strengthFeedbackV1", "leaderLagFollowersV1"
LABELS = {"baseline": "原始跟随", "known": "状态可判定", "positive": "强势延续过半"}
SIGNAL_FIELDS = ("signalId", "strategyId", "symbol", "signalDate", "originalRank")
CONTRASTS = (("knownVsBaseline", "baseline", "known"),
             ("positiveVsKnown", "known", "positive"),
             ("positiveVsBaseline", "baseline", "positive"))

spec = importlib.util.spec_from_file_location("strength_feedback_old_math", HELPER)
PRIOR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PRIOR)
BOARD, BASE = PRIOR.BOARD, PRIOR.BASE
require, finite, close = PRIOR.require, PRIOR.finite, PRIOR.close
sha, location, read, records = PRIOR.sha, PRIOR.location, PRIOR.read, PRIOR.records
total, labels = PRIOR.total, PRIOR.labels
inside, digest, sum_counts = PRIOR.inside, PRIOR.digest, PRIOR.sum_counts
EPOCHS, partition_check, metric_values = PRIOR.EPOCHS, PRIOR.partition_check, PRIOR.metric_values


def decision(row, mode):
    if mode == "baseline":
        return {"status": "included", "reason": "reusedOriginalControl"}
    return row["decisions"][mode]


def expected_signal(row, mode):
    require(row["formationId"] == row["signalId"] and row["sourceStrategyId"] == BASELINE_SID
            and row["strategyId"] == SID and row["formationDate"] == row["signalDate"], "Original identity changed")
    if mode == "baseline":
        return {**{key: row[key] for key in SIGNAL_FIELDS}, "strategyId": BASELINE_SID}
    require(mode in NEW_MODES, "Unknown report policy")
    signal = row["signals"][mode]
    require((signal is not None) == (decision(row, mode)["status"] == "included"), "Decision/schedule presence differs")
    if signal is not None:
        require(signal == {key: row[key] for key in SIGNAL_FIELDS}, "New policy changed fields besides strategyId")
    return signal


def ordered_schedule(original, mode):
    values = [s for row in original if (s := expected_signal(row, mode)) is not None]
    return sorted(values, key=lambda s: (s["signalDate"], s["originalRank"], s["symbol"], s["signalId"]))


def formation_counts(original, schedule, account_opportunities, mode, start=None, end=None):
    require(len({r["formationId"] for r in original}) == len(original), "Duplicate original formation")
    require(list(schedule) == ordered_schedule(original, mode), "Frozen schedule differs")
    observed = {r["signalId"]: r for r in account_opportunities}
    require(len(observed) == len(account_opportunities) and set(observed) == {r["signalId"] for r in schedule}, "Account population differs")
    cohort = [r for r in original if inside(r["formationDate"], start, end)]
    admitted = [observed[r["formationId"]] for r in cohort if r["formationId"] in observed]
    entered = sum(finite(r.get("filledQuantity")) and r["filledQuantity"] > 0 for r in admitted)
    status = Counter("scheduled" if expected_signal(r, mode) is not None else decision(r, mode)["status"] for r in cohort)
    return {"formationTop5": len(cohort), "originalControlOpportunities": len(cohort),
        "feedbackStates": labels(r["state"] for r in cohort),
        "policyDispositionCounts": {key: status[key] for key in ("scheduled", "excluded", "unknown")},
        "gateReasonCounts": labels(decision(r, mode)["reason"] for r in cohort),
        "selectedSignalsFromFormation": len(admitted), "notScheduledFromFormation": len(cohort)-len(admitted),
        "enteredFromFormation": entered, "notEnteredFromFormation": len(admitted)-entered,
        "uniqueFormationDays": len({r["formationDate"] for r in cohort}),
        "uniqueSymbols": len({r["symbol"] for r in cohort}),
        "terminalStatusCounts": labels(r.get("status", "missing") for r in admitted),
        "terminalReasonCounts": labels(r.get("reason", "missing") for r in admitted),
        "meaning": "Original parent control opportunity cohort at A; F=A-5 is feedback cohort cutoff. Terminal dispositions are measured at account cutoff, not interval end."}


def interval(result, start, end, initial, original=(), schedule=(), mode="baseline"):
    view = dict(result)
    view["research"] = {"opportunities": []}
    measured = BOARD.interval(view, start, end, initial)
    account_opps = result.get("research", {}).get("opportunities", [])
    measured["opportunitiesByFormationDate"] = formation_counts(original, schedule, account_opps, mode, start, end)
    measured["opportunitiesByDecisionDate"] = BASE.decision_counts(schedule, account_opps, start, end)
    return measured


def attach_dispositions(original, schedule, result, summary, mode):
    observed = BASE.execution_dates(result)
    require(set(observed) == {r["signalId"] for r in schedule}, "Saved dispositions differ from schedule")
    account = {r["signalId"]: r for r in result["research"]["opportunities"]}
    opened = {(r["record"]["symbol"], r["record"]["entryDate"]): r for r in summary["openPositions"]["positions"]}
    require(len(opened) == summary["openPositions"]["count"], "Duplicate open episode")
    for row in original:
        sid, signal = row["formationId"], expected_signal(row, mode)
        detail = observed.get(sid)
        require((detail is not None) == (signal is not None), "Schedule/account binding differs")
        if detail is None:
            detail = {"signalId": sid, "signalDate": None, "entryDate": None, "exitDate": None,
                "positionState": "notScheduled", "status": "notScheduled", "reason": decision(row, mode)["reason"],
                "admissionStatus": decision(row, mode)["status"], "filledQuantity": 0, "attemptDate": None,
                "closedReturnPct": None, "closedPnlAmount": None, "accountOpportunity": None, "openPositionAtCutoff": None}
        else:
            position = opened.get((row["symbol"], detail["entryDate"]))
            require((position is not None) == (detail["positionState"] == "openAtCutoff"), "Open episode linkage differs")
            detail = {**deepcopy(detail), "admissionStatus": "included", "accountOpportunity": deepcopy(account[sid]),
                "openPositionAtCutoff": deepcopy(position)}
        row.setdefault("accountDispositions", {})[mode] = {"formationId": sid, "formationDate": row["formationDate"],
            "cohortDate": row["cohortDate"], "feedbackState": row["state"],
            "strategyId": BASELINE_SID if mode == "baseline" else SID,
            "plannedNextEntryDate": row.get("plannedNextEntryDate"), "nextEntryWithinPeriod": row.get("nextEntryWithinPeriod"),
            "decisionDate": signal["signalDate"] if signal else None, **detail}


def compare_accounts(accounts):
    indexed = {(r["period"], r["board"], r["policy"]): r for r in accounts}
    require(len(indexed) == len(accounts) == 18 and set(indexed) == {(p,b,m) for p in PERIODS for b in BOARDS for m in MODES}, "Exactly18 complete accounts required")
    output = []
    for period in PERIODS:
        for board in BOARDS:
            values = {mode: metric_values(indexed[(period, board, mode)]) for mode in MODES}
            keys = set(values["baseline"])
            require(all(set(v) == keys for v in values.values()), "Contrast metric coverage differs")
            for name, control, candidate in CONTRASTS:
                a, b = values[control], values[candidate]
                output.append({"period": period, "board": board, "id": name, "control": control, "candidate": candidate,
                    "controlMetrics": a, "candidateMetrics": b,
                    "candidateMinusControl": {k: b[k]-a[k] if finite(a[k]) and finite(b[k]) else None for k in sorted(keys)},
                    "meaning": "Whole-account policy difference includes feedback availability, state admission, exposure and capital paths; not a causal return estimate."})
    return output


def summarize_account(period, board, mode, config, original, schedule, metadata, result, summary, row, phases):
    require((row["period"], row["board"], row["id"]) == (period, board, mode), "Comparison identity differs")
    require(result["status"] == "completed" and summary["policyId"] == ("control" if mode == "baseline" else mode)
            and not summary["quality"]["integrityIssues"], "Incomplete/incorrect account")
    require(schedule == ordered_schedule(original, mode), "Frozen policy schedule differs")
    initial, dates = config["initialCapital"], metadata["targetDates"]
    require(initial == 500000 and dates == sorted(set(dates)) and [r["date"] for r in result["curve"]] == dates,
            "Capital or complete calendar differs")
    attach_dispositions(original, schedule, result, summary, mode)
    overall = interval(result, config["startDate"], config["endDate"], initial, original, schedule, mode)
    saved = summary["account"]
    for key, source in (("returnPct", "netReturnPct"), ("maxDrawdownPct", "maxDrawdownPct"),
        ("netPnlAmount", "netPnlAmount"), ("endingEquity", "finalEquity"), ("feesPaid", "feesPaid"),
        ("buyFillCount", "buyFillCount"), ("sellFillCount", "sellFillCount")):
        close(overall[key], saved[source], f"{period}/{board}/{mode}/{key}")
    closed = overall["closedEpisodesByExitDate"]
    close(closed["netPnlAmount"], summary["closedEpisodes"]["netPnlAmount"], "Closed net PnL")
    close(closed["returnPct"]["mean"], summary["closedEpisodes"]["meanReturnPct"], "Closed mean")
    for key, actual in (("accountReturnPct", overall["returnPct"]), ("maxDrawdownPct", overall["maxDrawdownPct"]),
        ("meanExposurePct", overall["exposurePct"]["mean"]), ("closed", closed["count"]),
        ("signals", len(schedule)), ("openPositions", summary["openPositions"]["count"])):
        close(row[key], actual, "Comparison " + key)
    require(overall["opportunitiesByDecisionDate"]["entered"] == overall["buyFillCount"]
            == closed["count"] + summary["openPositions"]["count"], "Entered/closed/open counts differ")
    opened = deepcopy(summary["openPositions"])
    opened["unrealizedPnlAmount"] = total(r["unrealizedPnlAmount"] for r in opened["positions"])
    opened["realizedPnlWhileOpen"] = total(r["realizedPnlWhileOpen"] for r in opened["positions"])
    bridge = summary["reconciliation"]
    close(opened["unrealizedPnlAmount"], bridge["openPositionUnrealizedPnl"], "Open unrealized PnL")
    close(opened["realizedPnlWhileOpen"], bridge["openPositionRealizedPnl"], "Open realized PnL")
    close(total([closed["netPnlAmount"], opened["unrealizedPnlAmount"], opened["realizedPnlWhileOpen"],
        bridge["rightsAttributionGapAmount"]]), saved["netPnlAmount"], "Closed/open/rights bridge")
    account = {"period": period, "board": board, "id": mode, "policy": mode, "label": LABELS[mode],
        "sourcePolicyId": summary["policyId"], "sourcePolicyLabel": summary["policyLabel"],
        "sourceStrategyId": BASELINE_SID if mode == "baseline" else SID,
        "execution": "reused-frozen-account" if mode == "baseline" else "new-independent-account", "comparisonRow": deepcopy(row), "account": deepcopy(saved),
        "curveAttribution": overall, "closedEpisodes": deepcopy(summary["closedEpisodes"]), "closedDistribution": closed,
        "openPositionsAtCutoff": opened, "reconciliation": deepcopy(bridge),
        **{key: deepcopy(summary[key]) for key in ("unresolvedExits", "concentration", "quality", "rights", "sourceQuality")},
        "sourceQualityScalarFlags": {k: v for k, v in summary["sourceQuality"].items() if not isinstance(v, (list, dict))},
        "warningCount": len(summary.get("warnings", [])),
        "opportunityStatusCounts": deepcopy(summary["opportunityDispositions"]["opportunityCounts"]),
        "configuredUniverseValues": deepcopy(config["universe"]),
        "configuredUniverseMeaning": "Compatibility only: fixed-signal execution does not enforce minListingDays120/minAmount20."}
    coverage = {"period": period, "board": board, "id": mode, "policy": mode,
        **deepcopy(overall["opportunitiesByFormationDate"]), **deepcopy(overall["opportunitiesByDecisionDate"]),
        "buyFillCount": overall["buyFillCount"], "sellFillCount": overall["sellFillCount"],
        "closedCount": closed["count"], "openCountAtCutoff": opened["count"],
        "unresolvedExitCountAtCutoff": summary["unresolvedExits"]["count"], "curveSessions": len(dates),
        "valuationUnreliableDays": len(overall["valuationUnreliableDates"]),
        "qualityScalarFlags": deepcopy(account["sourceQualityScalarFlags"])}
    years, halves, phase_rows, epochs = [], [], [], []
    for saved_period in summary["periods"]:
        if saved_period["kind"] not in ("calendarYear", "calendarHalfYear"):
            continue
        measured = interval(result, saved_period["requestedStart"], saved_period["requestedEnd"], initial, original, schedule, mode)
        for key in ("returnPct", "maxDrawdownPct", "netPnlAmount", "enteringEquity", "endingEquity"):
            close(measured[key], saved_period[key], "Year/halfyear " + key)
        item = BASE.compact_period(saved_period, period, mode)
        item.update(measured)
        item["board"] = board
        (years if saved_period["kind"] == "calendarYear" else halves).append(item)
    relevant = [p for p in phases if p["startDate"] <= config["endDate"] and p["endDate"] >= config["startDate"]]
    require(all(sum(p["startDate"] <= d <= p["endDate"] for p in relevant) == 1 for d in dates), "Atlas coverage differs")
    for phase in relevant:
        measured = interval(result, phase["startDate"], phase["endDate"], initial, original, schedule, mode)
        measured.update(period=period, board=board, policy=mode, phaseId=phase["id"], phaseTitle=phase["title"],
            sentimentSummary=phase["sentimentSummary"], dominantThemes=deepcopy(phase["dominantThemes"]),
            representatives=deepcopy(phase["representatives"]), labelAvailability=phase["labelAvailability"],
            attributionMeaning="Hindsight calendar interval with carried positions/equity; never a signal input.")
        phase_rows.append(measured)
    checks = [partition_check(part, overall, len(original), len(schedule), label)
              for part, label in ((years, "years"), (halves, "halfyears"), (phase_rows, "phases"))]
    if period == "2024-2025":
        for epoch, start, end in EPOCHS:
            measured = interval(result, start, end, initial, original, schedule, mode)
            measured.update(period=period, board=board, policy=mode, phaseId=epoch,
                attributionMeaning="User2024-09-24 boundary; continuous capital and holdings, no reset.")
            epochs.append(measured)
        checks.append(partition_check(epochs, overall, len(original), len(schedule), "userEventEpochs"))
    return {"account": account, "coverage": coverage, "yearRows": years, "halfYearRows": halves,
        "phaseRows": phase_rows, "curveEpochRows": epochs,
        "reconciliation": {"period": period, "board": board, "policy": mode, "passed": True, "checks": checks}}


def feedback_coverage(inputs, metadata, counts, original):
    daily = records(inputs["daily-evidence.jsonl.gz"])
    require([r["formationDate"] for r in daily] == metadata["targetDates"], "Every target day must be retained")
    by_day = {r["formationDate"]: r for r in daily}
    member_observations, seen = 0, []
    for group in PRIOR.stream_records(inputs["member-evidence.jsonl.gz"]):
        day = group["formationDate"]
        require(day in by_day and day not in seen, "Member day missing or duplicated")
        row, members = by_day[day], group["members"]
        require(group["memberDigest"] == digest(members) == row["memberDigest"]
                and group["cohortDate"] == row["cohortDate"]
                and group["rankPopulation"] == row["rankPopulation"]
                and group["cohortSize"] == len(members) == row["cohortSize"], "Complete frozen member binding differs")
        require([r["symbol"] for r in members] == sorted({r["symbol"] for r in members}), "Member identity/order differs")
        observed = Counter(r["status"] for r in members)
        require(set(observed) <= {"positive", "flat", "negative", "unknown"}, "Unknown member status")
        require({k: observed[k] for k in ("positive", "flat", "negative", "unknown")} == row["feedbackCounts"], "Fixed denominator count differs")
        for member in members:
            value, status = member["returnSinceCohort"], member["status"]
            require(finite(member["relativeReturn20"]) and finite(member["percentile"]) and .9 <= member["percentile"] <= 1,
                    "Frozen rank member invalid")
            require((value is None and status == "unknown") or finite(value) and
                    status == ("positive" if value > 0 else "negative" if value < 0 else "flat"), "Feedback sign differs")
        n, p, u = row["cohortSize"], row["feedbackCounts"]["positive"], row["feedbackCounts"]["unknown"]
        expected = "unknown" if not n else "pass" if 2*p > n else "fail" if 2*(p+u) <= n else "unknown"
        require(row["state"] == expected, "Robust state differs")
        close(row["positiveFractionLower"], p/n if n else None, "Positive lower bound")
        close(row["positiveFractionUpper"], (p+u)/n if n else None, "Positive upper bound")
        member_observations += n
        seen.append(day)
    require(seen == metadata["targetDates"], "All complete daily member records required")
    states = Counter(r["state"] for r in daily)
    require({s: states[s] for s in ("pass", "fail", "unknown")} == counts["dayStates"], "Daily states do not conserve")
    require(counts["targetDayCount"] == len(daily) and counts["opportunityCount"] == counts["inputSignalCount"] == len(original), "Input denominator differs")
    for row in original:
        require(row["formationDate"] in by_day, "Opportunity day outside context")
        current = by_day[row["formationDate"]]
        require(all(row[k] == current[k] for k in ("cohortDate", "state", "cohortSize", "feedbackCounts", "memberDigest")), "Opportunity feedback binding differs")
        for mode in NEW_MODES:
            status = "unknown" if row["state"] == "unknown" else "included" if row["state"] == "pass" or mode == "known" else "excluded"
            require(decision(row, mode)["status"] == status, "Mask state differs")
            expected_signal(row, mode)
    for mode in NEW_MODES:
        policies = Counter(decision(r, mode)["status"] for r in original)
        require({s: policies[s] for s in ("included", "excluded", "unknown")} == counts["policyCounts"][mode]
                and policies["included"] == counts["signals"][mode]
                and len(original)-policies["included"] == counts["notScheduled"][mode], "Opportunity mask conservation differs")
    return {"passed": True, "counts": deepcopy(counts), "targetDayCount": len(daily),
        "fullMemberRecords": len(seen), "memberObservations": member_observations,
        "dayStateCounts": {s: states[s] for s in ("pass", "fail", "unknown")},
        "dayReasonCounts": labels(r["reason"] for r in daily),
        "memberFeedbackCounts": sum_counts(r["feedbackCounts"] for r in daily),
        "knownDecisionWithUnknownMembersDays": sum(r["state"] != "unknown" and r["feedbackCounts"]["unknown"] > 0 for r in daily),
        "completeNonemptyCohortDays": sum(r["cohortSize"] > 0 and r["feedbackCounts"]["unknown"] == 0 for r in daily),
        "emptyCohortDays": sum(r["cohortSize"] == 0 for r in daily),
        "rankPopulation": PRIOR.distribution(r["rankPopulation"] for r in daily),
        "cohortSize": PRIOR.distribution(r["cohortSize"] for r in daily),
        "rankUnknownReasonCounts": sum_counts(r["rankReasonCounts"] for r in daily),
        "opportunityStateCounts": labels(r["state"] for r in original),
        "uniqueOpportunityDays": len({r["formationDate"] for r in original}),
        "opportunityDayStateCounts": labels(by_day[d]["state"] for d in sorted({r["formationDate"] for r in original})),
        "basis": deepcopy(metadata["basis"]),
        "evidenceArtifacts": {name: {"path": location(path), "sha256": sha(path)} for name,path in inputs.items() if name.endswith('.jsonl.gz')},
        "meaning": "All target days and all F-frozen member observations, including unknown members. Repeated windows and multiple signals on a day are dependent; these are not independent outcome samples."}, daily


def feedback_interval(daily, start, end):
    rows = [r for r in daily if inside(r["formationDate"], start, end)]
    return {"days": len(rows), "dayStateCounts": labels(r["state"] for r in rows),
        "memberObservations": sum(r["cohortSize"] for r in rows),
        "memberFeedbackCounts": sum_counts(r["feedbackCounts"] for r in rows),
        "knownDecisionWithUnknownMembersDays": sum(r["state"] != "unknown" and r["feedbackCounts"]["unknown"] > 0 for r in rows)}


def build(directory=DEFAULT, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    destination, sidecar = directory / "readout.json", directory / "opportunity-dispositions.jsonl.gz"
    require(not destination.exists() and not sidecar.exists(), "Never overwrite readout outputs")
    pins = {}
    def pin(path):
        path = Path(path).resolve()
        value = sha(path)
        require(pins.setdefault(location(path), value) == value, "Input changed")
        return path
    def bound(reference):
        require(isinstance(reference, dict) and {"path", "sha256"} <= set(reference), "Invalid reference")
        path = Path(reference["path"])
        path = pin(path if path.is_absolute() else ROOT / path)
        require(pins[location(path)] == reference["sha256"], "Artifact digest differs")
        return path
    paths = {name: pin(directory / name) for name in ("plan.json", "comparison.json", "input-completion.json", "input-validation.json")}
    pin(validation_path)
    for path in (ATLAS, PROTOCOL, HELPER, PRIOR.HELPER, PRIOR.PRIOR.HELPER, BOARD.HELPER, Path(__file__)):
        pin(path)
    require(sha(ATLAS) == ATLAS_SHA, "Frozen atlas changed")
    plan, comparison, completion, validation, input_validation = (read(p) for p in
        (paths["plan.json"], paths["comparison.json"], paths["input-completion.json"], validation_path, paths["input-validation.json"]))
    plan_sha = sha(paths["plan.json"])
    BOARD.validation_binding(validation, plan_sha, sha(paths["comparison.json"]))
    require(validation["inputValidationSha256"] == sha(paths["input-validation.json"])
            and validation["inputCompletionSha256"] == sha(paths["input-completion.json"]), "Account input proof chain differs")
    require(input_validation["passed"] is True and input_validation["planSha256"] == plan_sha
            and input_validation["inputCompletionSha256"] == sha(paths["input-completion.json"]), "Input proof differs")
    require(plan["schema"] == "strength-feedback-v1" and plan["modes"] == list(NEW_MODES)
            and plan["reportModes"] == list(MODES) and plan["boards"] == list(BOARDS), "Experiment population differs")
    require(bound(plan["protocol"]) == PROTOCOL and plan["rules"] == read(PROTOCOL), "Protocol binding differs")
    require(comparison["planSha256"] == completion["planSha256"] == plan_sha
            and completion["allSchedulesFrozenBeforeAccounts"] is True, "Input clock proof differs")
    for k, n in (("newAccounts",12),("newSetupMechanisms",0),("newEntryPolicies",2),("newUniverseVariants",0),("referenceAccountsReused",6)):
        require(plan[k] == comparison[k] == n, "Trial accounting differs: " + k)
    require(plan["parameterSearch"] is False and plan["profitHurdles"] is None and plan["holdoutClaim"] is False, "Unsupported research claim")
    for mapping in (plan["sourcePins"], plan["inputPins"]):
        for name, expected in mapping.items(): bound({"path":name,"sha256":expected})
    old_plan = read(bound(plan["baselinePlan"]))
    old_comparison = read(bound(plan["baselineComparison"]))
    parent = bound(plan["baselinePlan"]).parent
    old_readout = read(pin(parent / "readout.json"))
    old_proof = read(pin(parent / "readout-validation.json"))
    require(old_proof["passed"] is True and old_proof["readoutSha256"] == sha(parent / "readout.json")
            and old_proof["comparisonSha256"] == sha(parent / "comparison.json"), "Parent readout proof changed")
    require(validation["parentValidationSha256"] == sha(pin(parent / "validation.json"))
            and validation["parentInputValidationSha256"] == sha(pin(parent / "input-validation.json")), "Baseline proof chain differs")
    indexed = {(r["period"],r["board"],r["id"]):r for r in comparison["rows"]}
    require(len(comparison["rows"]) == len(indexed) == 18 and set(indexed) == {(p,b,m) for p in PERIODS for b in BOARDS for m in MODES}, "All18 account rows required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == 16 and all(r["labelAvailability"] == "retrospectiveResearchOnly" for r in phases), "All16 retrospective phases required")
    accounts,years,halves,phase_rows,epochs,coverage,formation,feedback,feedback_days,opportunities,partitions = ([] for _ in range(11))
    source_bindings, all_counts = {}, {}
    for period in PERIODS:
        source_bindings[period], all_counts[period] = {}, {}
        for board in BOARDS:
            folder, binding = directory / period / board, plan["sourceData"][period][board]
            receipt_path = bound(completion["outputs"][period][board]); receipt = read(receipt_path)
            require(receipt_path == folder / "input-completion.json" and receipt["planSha256"] == plan_sha
                    and (receipt["period"],receipt["board"]) == (period,board), "Source receipt identity differs")
            require(all(receipt[k] == v for k,v in binding.items()) and receipt["baselineSchedule"] == plan["baselineSchedules"][period][board]
                    and receipt["baselineInput"] == plan["baselineInputs"][period][board], "Source/baseline bindings differ")
            for key in ("accountData","sourceManifest","sourceValidation"): bound(binding[key])
            parent_input = read(bound(plan["baselineInputs"][period][board]))
            baseline = read(bound(plan["baselineSchedules"][period][board]))
            parent_opps = records(bound(parent_input["files"]["opportunities.jsonl.gz"]))
            parent_by_id = {r["formationId"]:r for r in parent_opps}
            require(len(parent_by_id) == len(parent_opps) and
                    [r["signals"]["control"] for r in parent_opps if r["signals"]["control"] is not None] == baseline, "Complete parent source queue differs")
            parent_coverage = next(r for r in old_readout["formationCoverageRows"] if (r["period"],r["board"]) == (period,board))
            inputs = {name:bound(ref) for name,ref in receipt["files"].items()}
            require(all(p.parent == folder for p in inputs.values()), "New input escaped folder")
            original = records(inputs["opportunities.jsonl.gz"])
            metadata, counts = read(inputs["signal-metadata.json"]), read(inputs["input-counts.json"])
            require(counts == receipt["counts"] == metadata["counts"] and metadata["modes"] == list(NEW_MODES)
                    and counts["sampleCount"] == binding["members"], "Metadata coverage differs")
            require(ordered_schedule(original,"baseline") == baseline and [r["formationId"] for r in original] == [r["signalId"] for r in baseline], "Original opportunity order changed")
            measured, daily = feedback_coverage(inputs, metadata, counts, original)
            feedback.append({"period":period,"board":board,**measured})
            feedback_days.extend({"period":period,"board":board,**deepcopy(r)} for r in daily)
            formation.append({"period":period,"board":board,"sourceCohort":deepcopy(parent_coverage["sourceCohort"]),
                "parentOriginalTop5":len(parent_opps),"parentControlSignals":len(baseline),"formationTop5":len(original),
                "upstreamNotScheduled":len(parent_opps)-len(baseline),"parentLeaderEventCount":parent_coverage["leaderEventCount"],
                "parentLayerCounts":deepcopy(parent_coverage["layerCounts"]),"feedbackOpportunityStates":labels(r["state"] for r in original),
                "feedbackDayCount":len(daily),"parentReadout":{"path":location(parent / "readout.json"),"sha256":sha(parent / "readout.json")},
                "meaning":"New population is the parent's exact executable control queue; original upstream Top5 and four-layer lineage remain bound, never resurrected."})
            calendar = metadata["calendar"]
            require(calendar == sorted(set(calendar)), "Source calendar invalid")
            day_indexes = {d:i for i,d in enumerate(calendar)}
            for r in original:
                a = day_indexes[r["formationDate"]]
                require(r["cohortDate"] == (calendar[a-5] if a>=5 else None), "Five-slot cohort clock differs")
                next_day = calendar[a+1] if a+1 < len(calendar) else None
                raw = parent_by_id[r["formationId"]]
                r.update(period=period,board=board,plannedNextEntryDate=next_day,nextEntryWithinPeriod=next_day in metadata["targetDates"],
                    parentLineage={k:deepcopy(raw[k]) for k in ("eventId","peerFormationDate","anchorSymbol","originalLeaderRank","frozenMemberRank","groupSha256")})
            for mode in MODES:
                source_mode = "control" if mode == "baseline" else mode
                schedule = baseline if mode == "baseline" else read(inputs[mode+".signals.json"])
                require(schedule == ordered_schedule(original,mode), "Policy schedule changed")
                refs = comparison["artifacts"][period][board][mode]
                if mode == "baseline":
                    require(refs == plan["baselineArtifacts"][period][board], "Baseline artifact bytes not reused")
                    raw_row = next(r for r in old_comparison["rows"] if (r["period"],r["board"],r["id"]) == (period,board,"control"))
                    require(indexed[(period,board,mode)] == {**raw_row,"id":"baseline","policy":"baseline","label":LABELS[mode]}, "Baseline comparison metadata-only normalization differs")
                artifacts = {name:bound(ref) for name,ref in refs.items()}
                require(mode == "baseline" or all(p.parent == folder for p in artifacts.values()), "New account artifact escaped folder")
                result, summary = read(artifacts[source_mode+".result.json.gz"]), read(artifacts[source_mode+".summary.json"])
                config = old_plan["periods"][period]["config"] if mode == "baseline" else plan["periods"][period]["config"]
                item = summarize_account(period,board,mode,config,original,schedule,metadata,result,summary,indexed[(period,board,mode)],phases)
                for key in ("yearRows","halfYearRows","phaseRows","curveEpochRows"):
                    for segment in item[key]: segment["feedbackByActivationDate"] = feedback_interval(daily,segment["requestedStart"],segment["requestedEnd"])
                item["account"]["artifactRefs"],item["account"]["sourceBinding"] = deepcopy(refs),deepcopy(binding)
                accounts.append(item["account"]);coverage.append(item["coverage"])
                years.extend(item["yearRows"]);halves.extend(item["halfYearRows"]);phase_rows.extend(item["phaseRows"]);epochs.extend(item["curveEpochRows"]);partitions.append(item["reconciliation"])
                del result,summary,item
            require(all(set(r["accountDispositions"]) == set(MODES) for r in original), "Every original opportunity needs three dispositions")
            opportunities.extend(original)
            source_bindings[period][board] = {**deepcopy(binding),"inputCompletion":deepcopy(completion["outputs"][period][board]),
                "baselineInput":deepcopy(plan["baselineInputs"][period][board]),"baselineSchedule":deepcopy(plan["baselineSchedules"][period][board]),
                "baselineArtifacts":deepcopy(plan["baselineArtifacts"][period][board]),"evidenceArtifacts":deepcopy(receipt["files"])}
            all_counts[period][board] = deepcopy(counts)
    require(tuple(map(len,(accounts,years,halves,phase_rows,epochs,coverage,formation,feedback))) == (18,36,72,144,18,18,6,6), "Complete18-policy readout required")
    require(len(opportunities) == len({(r["period"],r["board"],r["formationId"]) for r in opportunities}) == sum(r["formationTop5"] for r in formation), "Original population lost or duplicated")
    comparisons = compare_accounts(accounts)
    report = {"schema":"strength-feedback-readout-v1","generatedAt":datetime.now(timezone.utc).isoformat(),
        "planSha256":plan_sha,"inputPins":pins,"sourceBindings":source_bindings,
        "verification":{"passed":True,"path":location(validation_path),"sha256":sha(validation_path),"meaning":"Independent input/account audit; exact accounting validation is not strategy certification."},
        "method":{"setup":"Exact parent control queue; no upstream leader/member changes, substitutions or resurrections.",
            "feedback":"At A-5 freeze RS20 top-decile members; absolute close feedback over all six exact slots F..A. Unknown observations stay in fixed N.",
            "majority":"N=0 unknown; 2P>N pass; 2(P+U)<=N fail; otherwise unknown. Known verdict can include unknown members.",
            "identity":"Baseline keeps source strategy/control metadata. New schedules retain signalId/date/symbol/rank and change only strategyId.",
            "comparisons":"known-baseline separates state availability; positive-known adds positive verdict; positive-baseline includes both. Differences describe full accounts, not causal effects.",
            "populations":"All target days, frozen member observations, original control opportunities, schedules, entries, closed and open episodes remain separate.",
            "intervals":"Prior-close equity and carried positions for all years/halves/16 hindsight phases and 9-24 epochs; PnL adds and return compounds, no reset or annualization.",
            "closedAndOpen":"Closed-trade metrics condition on entry and exit maturity. Open realized/unrealized PnL, fees, exposure, concentration and valuation flags remain separate.",
            "benchmark":"RS ranking and saved excess-return metrics use sh000300; feedback itself is absolute raw-price return, not board-matched or beta-neutral.",
            "scope":"Same-board fixed historical survivor cohort; not industry membership, market-wide leadership, investor identity, fresh holdout or a 2026 test.",
            "accountsRerunByThisScript":0,"parameterSearch":False,"profitHurdles":None},
        "accounts":accounts,"accountRows":[deepcopy(indexed[(p,b,m)]) for p in PERIODS for b in BOARDS for m in MODES],
        "comparisons":comparisons,"primaryComparisons":[r for r in comparisons if r["id"]=="positiveVsKnown"],
        "availabilityComparisons":[r for r in comparisons if r["id"]=="knownVsBaseline"],"overallComparisons":[r for r in comparisons if r["id"]=="positiveVsBaseline"],
        "yearRows":years,"halfYearRows":halves,"phaseRows":phase_rows,"curveEpochRows":epochs,
        "coverageRows":coverage,"formationCoverageRows":formation,"feedbackCoverageRows":feedback,"feedbackDailyRows":feedback_days,
        "inputCountsByPeriodBoard":all_counts,"partitionReconciliation":partitions,
        "trialAccounting":{"experimentBatches":1,"newSetupMechanisms":0,"newEntryPolicies":2,"newUniverseVariants":0,
            "newAccountExecutions":12,"referenceAccountsReused":6,"summarizerAccountExecutions":0},
        "limitations":list(dict.fromkeys(plan["limitations"]+[
            "Repeated rolling cohorts and same-day opportunities are dependent; day/member counts are not independent trade samples.",
            "Account differences include exposure and capital-path changes; fewer losses caused by avoiding trades do not prove small-loss/large-win efficacy.",
            "Closed-trade means omit open or unexecuted opportunities and cannot establish stable payoff.",
            "No board, policy, period or threshold is automatically promoted."])),
        "decision":{"selectedBoard":None,"selectedPolicy":None,"automaticNextStrategy":None,"profitHurdle":None},"certified":False,"targetAchieved":False}
    for name,expected in pins.items(): require(sha(ROOT/name) == expected,"Input changed during readout: "+name)
    with gzip.open(sidecar,"xt",encoding="utf-8",compresslevel=1) as stream:
        for row in opportunities: stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n")
    report["opportunityRowsArtifact"] = {"path":location(sidecar),"sha256":sha(sidecar),"count":len(opportunities),"format":"jsonl+gzip",
        "identity":["period","board","formationId"],"policiesPerRow":list(MODES)}
    with destination.open('x',encoding='utf-8') as stream: json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.write('\n')
    return {"passed":True,"path":location(destination),"sha256":sha(destination),"accounts":18,"comparisons":18,
        "yearRows":36,"halfYearRows":72,"phaseRows":144,"curveEpochRows":18,"opportunityRowsArtifact":report["opportunityRowsArtifact"],"certified":False}




def synthetic_opportunities():
    values = []
    for i, state in enumerate(("pass", "fail", "unknown"), 1):
        signal = {"signalId":f"old:{i}","strategyId":SID,"symbol":f"s{i}","signalDate":f"2024-07-0{i}","originalRank":1}
        decisions = {mode:{"status":"unknown" if state=="unknown" else "included" if mode=="known" or state=="pass" else "excluded", "reason":state} for mode in NEW_MODES}
        values.append({**signal,"formationId":signal["signalId"],"sourceStrategyId":BASELINE_SID,"formationDate":signal["signalDate"],
            "cohortDate":"2024-06-24","state":state,"decisions":decisions,
            "signals":{mode:deepcopy(signal) if decisions[mode]["status"]=="included" else None for mode in NEW_MODES}})
    return values


def self_test():
    original = synthetic_opportunities()
    for mode, expected in zip(MODES,(3,2,1)):
        schedule = ordered_schedule(original,mode)
        executed = [{**s,"filledQuantity":0,"status":"notPlanned","reason":"capacity"} for s in schedule]
        c = formation_counts(original,schedule,executed,mode)
        require(c["formationTop5"]==3 and c["selectedSignalsFromFormation"]==expected and c["notScheduledFromFormation"]==3-expected and c["notEnteredFromFormation"]==expected,"Mask denominator differs")
        require(all(s["strategyId"]==(BASELINE_SID if mode=="baseline" else SID) for s in schedule),"Source/new strategy identities conflated")
    bad=deepcopy(original);bad[0]["signals"]["positive"]["signalId"]="changed"
    try: ordered_schedule(bad,"positive")
    except ValueError: pass
    else: raise AssertionError("Changed ID accepted")
    days=("2024-06-28","2024-07-01","2024-07-02","2024-07-03")
    result={"curve":[{"date":d,"equity":e,"cash":e,"marketValue":0.,"valuationReliable":True} for d,e in zip(days,(100.,90.,95.,99.))],
        "daily":[{"date":d,"holdings":[]} for d in days],"trades":[],"closedTrades":[],
        "decisions":[{"date":days[1],"action":"sell","status":"queued","reason":"stop"},{"date":days[1],"action":"exit","status":"queued","reason":"old"}],"research":{"opportunities":[]}}
    whole=interval(result,days[0],days[-1],125.)
    left,right=interval(result,days[0],days[0],125.),interval(result,days[1],days[-1],125.)
    close(right["returnPct"],-1.,"Prior close");close(right["maxDrawdownPct"],10.,"Entering equity DD")
    require(right["queuedExitReasons"]=={"stop":1},"Queued sell fix lost")
    partition_check([left,right],whole,0,0,"synthetic")
    schedule=ordered_schedule(original,"known")
    result["research"]["opportunities"]=[{**s,"filledQuantity":0,"status":"notPlanned","reason":"capacity"} for s in schedule]
    attached=deepcopy(original);attach_dispositions(attached,schedule,result,{"openPositions":{"count":0,"positions":[]}},"known")
    require(attached[2]["accountDispositions"]["known"]["positionState"]=="notScheduled" and attached[0]["accountDispositions"]["known"]["positionState"]=="notEntered","Execution distinction lost")
    require(formation_counts(original,schedule,result["research"]["opportunities"],"known",days[0],days[0])["formationTop5"]==0,"F mistaken for A")
    require(PRIOR.distribution([None,1.])["mean"] is None,"Null removed")
    all_accounts, collected = [], Counter()
    for period in PERIODS:
        first, last = map(int, period.split("-"))
        calendar = [f"{year}-{month:02d}-03" for year in (first, last) for month in (1, 4, 7, 10)]
        fixture = {"status": "completed", "curve": [{"date": d, "equity": 500000., "cash": 500000.,
            "marketValue": 0., "valuationReliable": True} for d in calendar],
            "daily": [{"date": d, "holdings": []} for d in calendar], "trades": [], "closedTrades": [],
            "decisions": [], "research": {"opportunities": []}}
        config = {"initialCapital": 500000, "startDate": f"{first}-01-01", "endDate": f"{last}-12-31",
                  "universe": {"minListingDays": 120, "minAmount20": 50000000}}
        saved_periods, phases = [], []
        for year in (first, last):
            for kind, bounds in (("calendarYear", (("01-01", "12-31"),)),
                                 ("calendarHalfYear", (("01-01", "06-30"), ("07-01", "12-31")))):
                for start, end in bounds:
                    saved_periods.append({**interval(fixture, f"{year}-{start}", f"{year}-{end}", 500000), "kind": kind})
            for n, (start, end) in enumerate((("01-01", "03-31"), ("04-01", "06-30"),
                                             ("07-01", "09-30"), ("10-01", "12-31")), 1):
                phases.append({"id": f"{year}-{n}", "title": "synthetic", "startDate": f"{year}-{start}",
                    "endDate": f"{year}-{end}", "sentimentSummary": "synthetic", "dominantThemes": [],
                    "representatives": [], "labelAvailability": "retrospectiveResearchOnly"})
        summary = {"policyId": None, "policyLabel": "synthetic", "quality": {"integrityIssues": []},
            "account": {"netReturnPct": 0., "maxDrawdownPct": 0., "netPnlAmount": 0., "finalEquity": 500000.,
                        "feesPaid": 0., "buyFillCount": 0, "sellFillCount": 0},
            "closedEpisodes": {"netPnlAmount": 0., "meanReturnPct": None}, "openPositions": {"count": 0, "positions": []},
            "reconciliation": {"closedEpisodePnl": 0., "openPositionUnrealizedPnl": 0., "openPositionRealizedPnl": 0.,
                               "rightsAttributionGapAmount": 0.},
            "sourceQuality": {"pointInTimeComplete": False}, "unresolvedExits": {"count": 0},
            "concentration": {"top1ProfitSharePct": None, "top5ProfitSharePct": None}, "rights": {},
            "opportunityDispositions": {"opportunityCounts": {}}, "periods": saved_periods}
        for board in BOARDS:
            for mode in MODES:
                summary["policyId"] = "control" if mode == "baseline" else mode
                row = {"period": period, "board": board, "id": mode, "policy": mode, "accountReturnPct": 0.,
                       "maxDrawdownPct": 0., "meanExposurePct": 0., "closed": 0, "signals": 0, "openPositions": 0}
                item = summarize_account(period, board, mode, config, [], [], {"targetDates": calendar}, fixture, summary, row, phases)
                require(item["coverage"]["notEntered"] == 0 and item["account"]["sourceQuality"]["pointInTimeComplete"] is False,
                        "Empty policy/unknown quality lost")
                require(all(c["passed"] for c in item["reconciliation"]["checks"]), "Full account partitions failed")
                for name in ("yearRows", "halfYearRows", "phaseRows", "curveEpochRows"):
                    collected[name] += len(item[name])
                all_accounts.append(item["account"])
    require(dict(collected) == {"yearRows": 36, "halfYearRows": 72, "phaseRows": 144, "curveEpochRows": 18}, "Complete time matrix differs")
    for account in all_accounts:
        account["comparisonRow"]["accountReturnPct"] = float(MODES.index(account["policy"]))
    contrasts = compare_accounts(all_accounts)
    require(len(contrasts) == 18 and all(r["candidateMinusControl"]["accountReturnPct"] == (2. if r["id"] == "positiveVsBaseline" else 1.) for r in contrasts), "Three registered contrast directions")
    require(all(r["candidateMinusControl"]["top1ShareOfGrossProfitPct"] is None for r in contrasts), "Undefined contrast filled")
    require(sum(a["execution"]=="reused-frozen-account" for a in all_accounts)==6 and all(a["sourcePolicyId"]==("control" if a["policy"]=="baseline" else a["policy"]) for a in all_accounts),"Six reused identity adapters required")
    return {"passed":True,"checks":["three masks and original population","mixed strategy identity","changed ID rejected","previous close/DD","queued sell","partition reconciliation","A versus F","notScheduled versus notEntered","null preservation","18 account adapters including6 reused","36/72/144/18 intervals","18 signed contrasts","source quality retained"],"filesWritten":0,"actualResultsRead":False}


if __name__=="__main__":
    parser=ArgumentParser();parser.add_argument("--directory",type=Path,default=DEFAULT);parser.add_argument("--validation",type=Path);parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory,args.validation),ensure_ascii=False))
