"""Read already audited volume-anchor accounts; never form signals or run accounts.

All original anchor Top5 opportunities remain in an independently hashed sidecar,
including acceptance failure, unknown data, censoring and execution nonentry.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/volume-anchor-acceptance-v1"
HELPER = ROOT / "scripts/summarize-board-coverage-reclaim.py"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PROTOCOL = ROOT / "docs/signal-volume-anchor-acceptance-v1.json"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("immediate", "delay3", "accept3")
CONTRASTS = (("primary", "delay3", "accept3"), ("secondary", "immediate", "delay3"))


def helper_module():
    spec = importlib.util.spec_from_file_location("volume_anchor_readout_base", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BOARD = helper_module()
BASE = BOARD.BASE
require, finite, close = BOARD.require, BOARD.finite, BOARD.close
sha, location, read, records = BOARD.sha, BOARD.location, BOARD.read, BOARD.records
total, labels, distribution = BOARD.total, BOARD.labels, BOARD.distribution
EPOCHS = BOARD.EPOCHS


def inside(day, start=None, end=None):
    return day is not None and (start is None or start <= day <= end)


def acceptance_state(row):
    state = row["acceptance"]["status"]
    require(state in ("passed", "failed", "unknown", "censored"), "Unexpected acceptance state")
    return state


def expected_signal(row, mode):
    state = acceptance_state(row)
    expected = mode == "immediate" or (mode == "delay3" and state in ("passed", "failed")) or (mode == "accept3" and state == "passed")
    signal = row["signals"][mode]
    require((signal is not None) == expected, "Acceptance/schedule admission differs")
    if signal is not None:
        require(set(signal) == {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}
                and signal["signalId"] == row["formationId"] and signal["symbol"] == row["symbol"]
                and signal["originalRank"] == row["originalRank"], "Original signal identity/rank changed")
        require(signal["signalDate"] == row["decisionDates"][mode], "Decision date differs from signal")
    return signal


def ordered_schedule(original, mode):
    signals = [s for row in original if (s := expected_signal(row, mode)) is not None]
    return sorted(signals, key=lambda s: (s["signalDate"], s["originalRank"], s["symbol"], s["signalId"]))


def formation_counts(original, schedule, account_opportunities, mode, start=None, end=None):
    original_ids = {r["formationId"] for r in original}
    require(len(original_ids) == len(original), "Duplicate anchor identity")
    require(list(schedule) == ordered_schedule(original, mode), "Schedule differs from original frozen anchors")
    selected = {r["signalId"] for r in schedule}
    executed = {r["signalId"]: r for r in account_opportunities}
    require(len(executed) == len(account_opportunities) and set(executed) == selected, "Account/schedule opportunity mismatch")
    cohort = [r for r in original if inside(r["formationDate"], start, end)]
    observed = [executed[r["formationId"]] for r in cohort if r["formationId"] in selected]
    entered = sum(finite(r.get("filledQuantity")) and r["filledQuantity"] > 0 for r in observed)
    schedule_states = Counter("scheduled" if r["formationId"] in selected else acceptance_state(r) for r in cohort)
    return {"formationTop5": len(cohort), "acceptanceStates": labels(acceptance_state(r) for r in cohort),
        "policyDispositionCounts": {k: schedule_states[k] for k in ("scheduled", "failed", "unknown", "censored")},
        "selectedSignalsFromFormation": len(observed), "notScheduledFromFormation": len(cohort) - len(observed),
        "enteredFromFormation": entered, "notEnteredFromFormation": len(observed) - entered,
        "uniqueFormationDays": len({r["formationDate"] for r in cohort}), "uniqueSymbols": len({r["symbol"] for r in cohort}),
        "terminalStatusCounts": labels(r.get("status", "missing") for r in observed),
        "terminalReasonCounts": labels(r.get("reason", "missing") for r in observed),
        "meaning": "Cohort date is original A, including later decisions and cutoff dispositions; notScheduled differs from scheduled-but-notEntered."}


def interval(result, start, end, initial, original=(), schedule=(), mode="immediate"):
    view = dict(result)
    view["research"] = {"opportunities": []}
    measured = BOARD.interval(view, start, end, initial)
    opportunities = result.get("research", {}).get("opportunities", [])
    measured["opportunitiesByFormationDate"] = formation_counts(original, schedule, opportunities, mode, start, end)
    measured["opportunitiesByDecisionDate"] = BASE.decision_counts(schedule, opportunities, start, end)
    return measured


def partition_check(parts, overall, formation_count, schedule_count, label):
    close(total(r["netPnlAmount"] for r in parts), overall["netPnlAmount"], label + " PnL addition")
    compounded = (math.prod(1 + r["returnPct"] / 100 for r in parts) - 1) * 100 \
        if all(finite(r["returnPct"]) for r in parts) else None
    close(compounded, overall["returnPct"], label + " return compounding")
    require(sum(r["observedSessions"] for r in parts) == overall["observedSessions"], label + " curve coverage")
    for key, expected in (("formationTop5", formation_count), ("selectedSignalsFromFormation", schedule_count),
                          ("enteredFromFormation", overall["buyFillCount"])):
        require(sum(r["opportunitiesByFormationDate"][key] for r in parts) == expected, label + " formation " + key)
    for key in ("selectedSignals", "entered", "notEntered"):
        require(sum(r["opportunitiesByDecisionDate"][key] for r in parts)
                == overall["opportunitiesByDecisionDate"][key], label + " decision " + key)
    require(sum(r["closedEpisodesByExitDate"]["count"] for r in parts)
            == overall["closedEpisodesByExitDate"]["count"], label + " closed episodes")
    return {"kind": label, "passed": True, "intervals": len(parts),
            "netPnlAmount": total(r["netPnlAmount"] for r in parts), "compoundedReturnPct": compounded}


def attach_dispositions(original, schedule, result, summary, mode):
    observed = BASE.execution_dates(result)
    require(set(observed) == {s["signalId"] for s in schedule}, "Account dispositions differ from schedule")
    raw = {r["signalId"]: r for r in result["research"]["opportunities"]}
    opened = {(r["record"]["symbol"], r["record"]["entryDate"]): r for r in summary["openPositions"]["positions"]}
    require(len(opened) == summary["openPositions"]["count"], "Duplicate open episode identity")
    for row in original:
        sid, signal = row["formationId"], expected_signal(row, mode)
        detail = observed.get(sid)
        require((detail is not None) == (signal is not None), "Formation/schedule/account binding differs")
        if detail is None:
            disposition = {"signalId": sid, "signalDate": None, "entryDate": None, "exitDate": None,
                "positionState": "notScheduled", "status": "notScheduled", "reason": acceptance_state(row),
                "filledQuantity": 0, "attemptDate": None, "closedReturnPct": None, "closedPnlAmount": None,
                "accountOpportunity": None, "openPositionAtCutoff": None}
        else:
            open_row = opened.get((row["symbol"], detail["entryDate"]))
            require((open_row is not None) == (detail["positionState"] == "openAtCutoff"), "Open episode linkage differs")
            disposition = {**deepcopy(detail), "accountOpportunity": deepcopy(raw[sid]),
                           "openPositionAtCutoff": deepcopy(open_row)}
        row.setdefault("accountDispositions", {})[mode] = {"formationId": sid, "formationDate": row["formationDate"],
            "decisionDate": signal["signalDate"] if signal else None, **disposition}


def metric_values(account):
    values = deepcopy(account["comparisonRow"])
    saved, curve, bridge = account["account"], account["curveAttribution"], account["reconciliation"]
    values.update(netPnlAmount=saved["netPnlAmount"], finalEquity=saved["finalEquity"],
        closedPnlAmount=bridge["closedEpisodePnl"], openRealizedPnlAmount=bridge["openPositionRealizedPnl"],
        openUnrealizedPnlAmount=bridge["openPositionUnrealizedPnl"],
        investedSessionPct=curve["investedSessionPct"], feesPaid=curve["feesPaid"],
        slippageAlreadyInFillPrices=curve["slippageAlreadyInFillPrices"],
        buyFillCount=curve["buyFillCount"], sellFillCount=curve["sellFillCount"],
        notEntered=curve["opportunitiesByDecisionDate"]["notEntered"],
        notScheduled=curve["opportunitiesByFormationDate"]["notScheduledFromFormation"],
        top1ShareOfGrossProfitPct=account["concentration"]["top1ProfitSharePct"],
        top5ShareOfGrossProfitPct=account["concentration"]["top5ProfitSharePct"])
    return {k: v for k, v in values.items() if v is None or finite(v)}


def compare_accounts(accounts):
    indexed = {(r["period"], r["board"], r["policy"]): r for r in accounts}
    require(len(indexed) == len(accounts) == 18 and set(indexed) == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES},
            "Exactly18 unique account policies required")
    rows = []
    for period in PERIODS:
        for board in BOARDS:
            metrics = {mode: metric_values(indexed[(period, board, mode)]) for mode in MODES}
            keys = set(metrics["immediate"])
            require(all(set(v) == keys for v in metrics.values()), "Contrast metric fields differ")
            for contrast, control, candidate in CONTRASTS:
                rows.append({"period": period, "board": board, "id": contrast, "control": control, "candidate": candidate,
                    "controlMetrics": metrics[control], "candidateMetrics": metrics[candidate],
                    "candidateMinusControl": {k: metrics[candidate][k] - metrics[control][k]
                        if finite(metrics[candidate][k]) and finite(metrics[control][k]) else None for k in sorted(keys)},
                    "meaning": "Acceptance within the same complete-data delayed clock; full historical policy difference, not causal proof."
                        if contrast == "primary" else "Combined waiting, known-data admission and account execution/path difference; not an isolated acceptance effect."})
    return rows


def summarize_account(period, board, mode, config, original, schedule, metadata, result, summary, row, phases):
    require((row["period"], row["board"], row["id"]) == (period, board, mode), "Comparison identity differs")
    require(result["status"] == "completed" and summary["policyId"] == mode
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
    account = {"period": period, "board": board, "id": mode, "policy": mode, "label": summary["policyLabel"],
        "execution": "new-independent-account", "comparisonRow": deepcopy(row), "account": deepcopy(saved),
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


def build(directory, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    paths = {name: directory / name for name in ("plan.json", "comparison.json", "input-completion.json", "input-validation.json")}
    destination, sidecar = directory / "readout.json", directory / "opportunity-dispositions.jsonl.gz"
    require(not destination.exists() and not sidecar.exists(), "Readout/sidecar already exists; no overwrite")
    require(all(p.is_file() for p in (*paths.values(), validation_path, ATLAS, PROTOCOL)), "Validated frozen evidence required")
    require(sha(ATLAS) == ATLAS_SHA, "Original atlas changed")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        value = sha(path)
        require(pins.setdefault(location(path), value) == value, "Input changed: " + location(path))
        return path

    def bound(reference):
        require(isinstance(reference, dict) and {"path", "sha256"} <= set(reference), "Invalid artifact descriptor")
        path = Path(reference["path"])
        path = pin(path if path.is_absolute() else ROOT / path)
        require(pins[location(path)] == reference["sha256"], "Artifact binding differs: " + reference["path"])
        return path

    for path in (*paths.values(), validation_path, ATLAS, PROTOCOL, HELPER, BOARD.HELPER, Path(__file__)):
        pin(path)
    plan, comparison = read(paths["plan.json"]), read(paths["comparison.json"])
    completion, validation, input_validation = read(paths["input-completion.json"]), read(validation_path), read(paths["input-validation.json"])
    plan_sha = pins[location(paths["plan.json"])]
    BOARD.validation_binding(validation, plan_sha, pins[location(paths["comparison.json"])])
    require(validation["inputValidationSha256"] == pins[location(paths["input-validation.json"])]
            and validation["inputCompletionSha256"] == pins[location(paths["input-completion.json"])], "Account input audit chain differs")
    require(input_validation["passed"] is True and input_validation["planSha256"] == plan_sha
            and input_validation["inputCompletionSha256"] == pins[location(paths["input-completion.json"])], "Input proof differs")
    require(plan["schema"] == "volume-anchor-acceptance-v1" and plan["modes"] == list(MODES)
            and plan["boards"] == list(BOARDS) and set(plan["periods"]) == set(PERIODS), "Policy/board/period scope differs")
    require(bound(plan["protocol"]) == PROTOCOL and plan["rules"] == read(PROTOCOL), "Protocol binding differs")
    require(comparison["planSha256"] == completion["planSha256"] == plan_sha
            and completion["allSchedulesFrozenBeforeAccounts"] is True, "Schedules not frozen for this plan")
    for key, expected in (("newAccounts", 18), ("newSetupMechanisms", 1), ("newEntryPolicies", 3),
                          ("newUniverseVariants", 0), ("referenceAccountsReused", 0)):
        require(plan[key] == comparison[key] == expected, "Trial accounting differs: " + key)
    require(plan["parameterSearch"] is False and plan["profitHurdles"] is None and plan["holdoutClaim"] is False,
            "Readout cannot select parameters, profit hurdles or claim holdout")
    for mapping in (plan["sourcePins"], plan["inputPins"]):
        for name, expected in mapping.items():
            bound({"path": name, "sha256": expected})
    rows = deepcopy(comparison["rows"])
    indexed = {(r["period"], r["board"], r["id"]): r for r in rows}
    require(len(rows) == len(indexed) == 18 and set(indexed) == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES}
            and all(r["policy"] == r["id"] for r in rows), "Exactly18 distinct account rows required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == len({p["id"] for p in phases}) == 16
            and all(p["labelAvailability"] == "retrospectiveResearchOnly" for p in phases), "All original16 hindsight phases required")
    accounts, years, halves, phase_rows, epochs, coverage, formation_coverage, opportunities, partitions = ([] for _ in range(9))
    source_bindings, all_counts = {}, {}
    for period in PERIODS:
        source_bindings[period], all_counts[period] = {}, {}
        for board in BOARDS:
            folder, binding = directory / period / board, plan["sourceData"][period][board]
            receipt_path = bound(completion["outputs"][period][board])
            require(receipt_path == folder / "input-completion.json", "Board receipt path differs")
            receipt = read(receipt_path)
            require(receipt["planSha256"] == plan_sha and receipt["period"] == period and receipt["board"] == board,
                    "Board receipt identity differs")
            require(all(receipt[k] == v for k, v in binding.items()), "Frozen source binding differs")
            for key in ("accountData", "sourceManifest", "sourceValidation"):
                bound(binding[key])
            parent_validation = read(bound(binding["sourceValidation"]))
            require(parent_validation["passed"] is True
                    and validation["parentSourceValidationSha256"] == binding["sourceValidation"]["sha256"]
                    and parent_validation["datasetPins"][binding["accountData"]["path"]] == binding["accountData"]["sha256"],
                    "Previously audited source differs")
            manifest = read(bound(binding["sourceManifest"]))
            require(manifest["passed"] is True and manifest["period"] == period, "Parent source manifest differs")
            cohort = deepcopy(manifest["boards"][board])
            require(cohort["includedCount"] == binding["members"]
                    and cohort["accountData"]["datasetDigest"] == binding["accountDatasetDigest"], "Fixed source cohort differs")
            cohort["accountData"] = {**cohort["accountData"], **deepcopy(binding["accountData"])}
            cohort_records = [r for r in manifest["records"] if r["board"] == board]
            cohort["selectionStatusCounts"] = labels(r["selectionStatus"] for r in cohort_records)
            cohort["selectionReasonCounts"] = labels(r["reason"] for r in cohort_records)
            inputs = {name: bound(reference) for name, reference in receipt["files"].items()}
            require(all(path.parent == folder for path in inputs.values()), "Input escaped registered board folder")
            original, daily = records(inputs["opportunities.jsonl.gz"]), records(inputs["daily-evidence.jsonl.gz"])
            metadata, counts = read(inputs["signal-metadata.json"]), read(inputs["input-counts.json"])
            require(counts == receipt["counts"] == metadata["counts"] and metadata["modes"] == list(MODES), "Input counts/modes differ")
            require(counts["sampleCount"] == binding["members"] and counts["opportunityCount"] == len(original)
                    and counts["targetDayCount"] == len(metadata["targetDates"]), "Formation denominators differ")
            require([r["formationDate"] for r in daily] == metadata["targetDates"]
                    and [sid for r in daily for sid in r["selectedFormationIds"]] == [r["formationId"] for r in original],
                    "Complete daily/original Top5 coverage differs")
            states = Counter(acceptance_state(r) for r in original)
            require(counts["acceptance"] == {k: states[k] for k in ("passed", "failed", "unknown", "censored")},
                    "Acceptance states differ from complete opportunity set")
            target_index = {day: i for i, day in enumerate(metadata["targetDates"])}
            for formation in original:
                day = formation["formationDate"]
                require(day in target_index, "Anchor outside target calendar")
                later = target_index[day] + 3
                later = metadata["targetDates"][later] if later < len(metadata["targetDates"]) else None
                require(formation["decisionDates"] == {"immediate": day, "delay3": later, "accept3": later}
                        and ((later is None) == (acceptance_state(formation) == "censored")), "Three-slot/cutoff clock differs")
                formation.update(period=period, board=board)
            daily_totals = {key: sum(r["counts"][key] for r in daily) for key in daily[0]["counts"]}
            require(daily_totals["setupCount"] == counts["setupCount"]
                    and daily_totals["selectedCount"] == len(original)
                    and daily_totals["sampleCount"] == counts["sampleCount"] * counts["targetDayCount"], "Daily evidence totals differ")
            unknown_reasons = Counter()
            for day in daily:
                unknown_reasons.update(day["unknownReasonCounts"])
            formation_coverage.append({"period": period, "board": board, "sourceCohort": cohort,
                "formationTop5": len(original), "counts": deepcopy(counts), "formationEvidenceTotals": daily_totals,
                "unknownReasonCounts": dict(sorted(unknown_reasons.items())),
                "uniqueFormationDays": len({r["formationDate"] for r in original}), "uniqueSymbols": len({r["symbol"] for r in original}),
                "acceptanceStateCounts": dict(counts["acceptance"]),
                "knownFailedStepsInsideCensored": sum(r["acceptance"]["knownFailCount"] for r in original if acceptance_state(r) == "censored"),
                "unknownStepsInsideCensored": sum(r["acceptance"]["unknownStepCount"] for r in original if acceptance_state(r) == "censored"),
                "knownFailedStepsInsideUnknown": sum(r["acceptance"]["knownFailCount"] for r in original if acceptance_state(r) == "unknown"),
                "meaning": "One source/anchor cohort per board-period; daily counts are symbol-day evaluations. Censoring takes precedence while partial failure/unknown evidence remains retained."})
            for mode in MODES:
                schedule = read(inputs[mode + ".signals.json"])
                require(schedule == ordered_schedule(original, mode) and len(schedule) == counts["signals"][mode], "Frozen schedule differs")
                artifacts = {name: bound(reference) for name, reference in comparison["artifacts"][period][board][mode].items()}
                require(all(path.parent == folder for path in artifacts.values()), "Account artifact escaped board folder")
                result, summary = read(artifacts[mode + ".result.json.gz"]), read(artifacts[mode + ".summary.json"])
                item = summarize_account(period, board, mode, plan["periods"][period]["config"], original, schedule,
                    metadata, result, summary, indexed[(period, board, mode)], phases)
                item["account"]["artifactRefs"] = deepcopy(comparison["artifacts"][period][board][mode])
                item["account"]["sourceBinding"] = deepcopy(binding)
                accounts.append(item["account"]); coverage.append(item["coverage"])
                years.extend(item["yearRows"]); halves.extend(item["halfYearRows"])
                phase_rows.extend(item["phaseRows"]); epochs.extend(item["curveEpochRows"])
                partitions.append(item["reconciliation"])
                del result, summary, item
            require(all(set(r["accountDispositions"]) == set(MODES) for r in original), "Every anchor needs all three dispositions")
            opportunities.extend(original)
            source_bindings[period][board] = {**deepcopy(binding), "inputCompletion": deepcopy(completion["outputs"][period][board]),
                "dailyEvidence": deepcopy(receipt["files"]["daily-evidence.jsonl.gz"]),
                "originalOpportunities": deepcopy(receipt["files"]["opportunities.jsonl.gz"])}
            all_counts[period][board] = deepcopy(counts)
            del daily, original, manifest
    require(tuple(map(len, (accounts, years, halves, phase_rows, epochs, coverage, formation_coverage))) == (18, 36, 72, 144, 18, 18, 6),
            "Complete18-policy and6-source readout matrix required")
    require(len(opportunities) == sum(r["formationTop5"] for r in formation_coverage)
            == len({(r["period"], r["board"], r["formationId"]) for r in opportunities}), "Original anchors lost/duplicated")
    comparisons = compare_accounts(accounts)
    readout = {"schema": "volume-anchor-acceptance-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "inputPins": pins, "sourceBindings": source_bindings,
        "verification": {"passed": True, "path": location(validation_path), "sha256": sha(validation_path),
            "meaning": "Independent input/account audit plus complete arithmetic/coverage reconciliation; not strategy certification."},
        "method": {
            "setup": "A close strictly exceeds prior20 highs and A volume strictly exceeds prior20 volumes; rank prior20 mean amount, original Top5 at A before future acceptance or account state.",
            "tradedAverage": "Q_d=sum(amount_A..d)/sum(shares_A..d), known at d close. Accept when close_d*cumulativeShares>=cumulativeAmount for each of A+1..A+3. Not holder or main-force inventory cost.",
            "primaryContrast": "accept3 minus delay3 within the same complete-data delayed clock; full historical policy contrast, not randomized causal proof.",
            "secondaryContrast": "delay3 minus immediate combines waiting, known-data admission and actual account execution/path effects; not an isolated acceptance effect.",
            "unknownAndCensoring": "Both delayed modes retain unknown or right-censored anchors without signals. Immediate remains. Available failure/unknown steps are preserved even when censored; no complete-case deletion of original anchors.",
            "formationAndExecution": "Original A formation cohorts remain distinct from actual policy signalDate, entryDate and exitDate. notScheduled differs from scheduled-but-notEntered. No rerank or backfill.",
            "accounts": "18 new independent two-year500000 accounts across all three boards and policies. No reused accounts, four-year chaining, annualization or account runs by this script.",
            "intervals": "All years, halves,16 retrospective atlas stages and2024-09-24 boundary use actual previous close equity and carried holdings; initial500000 only if no prior curve point. PnL adds; returns compound; drawdown includes entering equity.",
            "closedAndOpen": "Closed means condition on execution and exit maturity; all terminal open marks, realized PnL while open, costs, exposure, tails and quality flags remain separate.",
            "quality": "Independent numeric audit does not remove original valuation/corporate-action/eligibility/opening-capacity flags. No missing values or dates silently removed.",
            "benchmark": "Any saved excessReturn is relative to common sh000300/CSI300/沪深300, not a matched board index or beta-neutral return. Benchmark is not an entry feature in this setup.",
            "boards": "Listing boards are not technology-industry membership; source and board histories were already inspected. No2026 input or holdout claim.",
            "compatibility": "Retained minListingDays120/minAmount20 config values are not enforced by FrozenSignalScorer.",
            "queuedExitCorrection": "Count action=sell AND status=queued, separately from sell fills and closed episodes; original account artifacts unchanged.",
            "parameterSearch": False, "profitHurdles": None, "accountsRerunByThisScript": 0},
        "accounts": accounts, "accountRows": [deepcopy(indexed[(p, b, m)]) for p in PERIODS for b in BOARDS for m in MODES],
        "comparisons": comparisons, "primaryComparisons": [r for r in comparisons if r["id"] == "primary"],
        "secondaryComparisons": [r for r in comparisons if r["id"] == "secondary"],
        "yearRows": years, "halfYearRows": halves, "phaseRows": phase_rows, "curveEpochRows": epochs,
        "coverageRows": coverage, "formationCoverageRows": formation_coverage, "inputCountsByPeriodBoard": all_counts,
        "partitionReconciliation": partitions,
        "trialAccounting": {"experimentBatches": 1, "newSetupMechanisms": 1, "newEntryPolicies": 3,
            "newUniverseVariants": 0, "newAccountExecutions": 18, "referenceAccountsReused": 0, "summarizerAccountExecutions": 0},
        "limitations": list(dict.fromkeys(plan["limitations"] + [
            "Conditioning on acceptance changes opportunity density, capital usage and later trades; differences do not identify investor control or causal support.",
            "Closed-trade means alone omit long-lived open positions and cannot establish stable small-loss/large-win profitability.",
            "Common CSI300 excess performance is not board-index matched or beta neutral; listing boards are not industry classifications.",
            "All original anchors retain partial failed/unknown acceptance evidence; censoring does not imply eventual failure.",
            "No board/policy/date/threshold is automatically selected or promoted by this report."])),
        "decision": {"selectedBoard": None, "selectedPolicy": None, "automaticNextStrategy": None, "profitHurdle": None},
        "certified": False, "targetAchieved": False}
    for name, expected in pins.items():
        path = Path(name)
        require(sha(path if path.is_absolute() else ROOT / path) == expected, "Input changed during readout: " + name)
    with gzip.open(sidecar, "xt", encoding="utf-8", compresslevel=1) as stream:
        for row in opportunities:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    readout["opportunityRowsArtifact"] = {"path": location(sidecar), "sha256": sha(sidecar), "count": len(opportunities),
        "format": "jsonl+gzip", "identity": ["period", "board", "formationId"], "policiesPerRow": list(MODES)}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(readout, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "path": location(destination), "sha256": sha(destination),
        "opportunityRowsArtifact": readout["opportunityRowsArtifact"], "accounts": len(accounts), "comparisons": len(comparisons),
        "yearRows": len(years), "halfYearRows": len(halves), "phaseRows": len(phase_rows), "curveEpochRows": len(epochs),
        "coverageRows": len(coverage), "formationCoverageRows": len(formation_coverage), "certified": False}


def self_test():
    # All tests are synthetic; no source, schedule or new return is read.
    days = ("2024-06-28", "2024-07-01", "2024-07-02", "2024-07-03")
    original = []
    for n, state in enumerate(("passed", "failed", "unknown", "censored"), 1):
        sid = "test:" + state
        signal = {"signalId": sid, "strategyId": "test", "symbol": state, "signalDate": days[0], "originalRank": n}
        original.append({"formationId": sid, "formationDate": days[0], "symbol": state, "originalRank": n,
            "decisionDates": {"immediate": days[0], "delay3": days[-1] if state != "censored" else None,
                              "accept3": days[-1] if state != "censored" else None},
            "acceptance": {"status": state}, "signals": {"immediate": signal,
                "delay3": {**signal, "signalDate": days[-1]} if state in ("passed", "failed") else None,
                "accept3": {**signal, "signalDate": days[-1]} if state == "passed" else None}})
    for mode, expected in zip(MODES, (4, 2, 1)):
        schedule = ordered_schedule(original, mode)
        opps = [{**s, "filledQuantity": 0, "status": "notPlanned", "reason": "capacity"} for s in schedule]
        counts = formation_counts(original, schedule, opps, mode)
        require(counts["formationTop5"] == 4 and counts["selectedSignalsFromFormation"] == expected
                and counts["notScheduledFromFormation"] == 4 - expected and counts["notEnteredFromFormation"] == expected,
                "Censored/unknown/nonentry denominators differ")
    bad = deepcopy(original)
    bad[2]["signals"]["delay3"] = bad[2]["signals"]["immediate"]
    try: ordered_schedule(bad, "delay3")
    except ValueError: pass
    else: raise AssertionError("Unknown admitted to delayed schedule")
    result = {"curve": [{"date": d, "equity": e, "cash": e, "marketValue": 0., "valuationReliable": True}
        for d, e in zip(days, (100., 90., 95., 99.))], "daily": [{"date": d, "holdings": []} for d in days],
        "trades": [], "closedTrades": [], "decisions": [{"date": days[1], "action": "sell", "status": "queued", "reason": "stop"},
            {"date": days[1], "action": "exit", "status": "queued", "reason": "old"}], "research": {"opportunities": []}}
    whole = interval(result, days[0], days[-1], 125.)
    left, right = interval(result, days[0], days[0], 125.), interval(result, days[1], days[-1], 125.)
    close(right["returnPct"], -1., "Prior-close boundary")
    close(right["maxDrawdownPct"], 10., "Entering equity drawdown")
    require(right["queuedExitReasons"] == {"stop": 1}, "Queued sell correction lost")
    partition_check([left, right], whole, 0, 0, "synthetic")
    scheduled = ordered_schedule(original, "delay3")
    result["research"]["opportunities"] = [{**s, "filledQuantity": 0, "status": "notPlanned", "reason": "capacity"} for s in scheduled]
    before = interval(result, days[0], days[0], 125., original, scheduled, "delay3")
    after = interval(result, days[1], days[-1], 125., original, scheduled, "delay3")
    require(before["opportunitiesByFormationDate"]["selectedSignalsFromFormation"] == 2
            and before["opportunitiesByDecisionDate"]["selectedSignals"] == 0
            and after["opportunitiesByFormationDate"]["formationTop5"] == 0
            and after["opportunitiesByDecisionDate"]["selectedSignals"] == 2, "A/decision dates conflated")
    attached = deepcopy(original)
    attach_dispositions(attached, scheduled, result, {"openPositions": {"count": 0, "positions": []}}, "delay3")
    require(attached[2]["accountDispositions"]["delay3"]["positionState"] == "notScheduled"
            and attached[0]["accountDispositions"]["delay3"]["positionState"] == "notEntered", "Nonentry vs nonschedule lost")
    require(distribution([None, 1.])["mean"] is None, "Unknown distribution removed")
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
                summary["policyId"] = mode
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
    require(len(contrasts) == 12 and all(r["candidateMinusControl"]["accountReturnPct"] == 1. for r in contrasts), "Primary/secondary contrast orientation")
    require(all(r["candidateMinusControl"]["top1ShareOfGrossProfitPct"] is None for r in contrasts), "Undefined contrast filled")
    return {"passed": True, "checks": ["all four acceptance states and three policies", "unknown delayed rejection",
        "previous-close and entering drawdown", "queued sell correction", "partition PnL and compounding",
        "formation versus delayed decision cohort", "notScheduled versus notEntered sidecar", "unknown numeric preservation",
        "18 complete empty-policy adapters and original quality", "36/72/144/18 full interval matrix", "12 signed contrasts with null propagation"],
        "filesWritten": 0, "actualResultsRead": False}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory, args.validation), ensure_ascii=False))
