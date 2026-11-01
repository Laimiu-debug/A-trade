"""Complete readout of frozen reclaim entry/breadth policies; no account runs.

The original immediate accounts are read by their registered references.  This
script only creates a new readout after independent validation has passed.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/reclaim-breadth-entry-v1"
PARENT = ROOT / "artifacts/strategy-search/strong-undercut-reclaim-timing-v1"
HELPER = ROOT / "scripts/summarize-undercut-reclaim-timing.py"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PERIODS = ("2022-2023", "2024-2025")
BASELINE = "immediate"
NEW_POLICIES = ("knownContext", "breadthMajority", "breadthImproving", "breadthBoth")
POLICIES = (BASELINE, *NEW_POLICIES)
CONTRASTS = (("knownVsOriginal", BASELINE, "knownContext"),
             ("majorityVsKnown", "knownContext", "breadthMajority"),
             ("improvingVsKnown", "knownContext", "breadthImproving"),
             ("bothVsMajority", "breadthMajority", "breadthBoth"),
             ("bothVsImproving", "breadthImproving", "breadthBoth"))
EPOCHS = (("before20240924", "2024-01-02", "2024-09-23"),
          ("from20240924", "2024-09-24", "2025-12-31"))


def helper_module():
    spec = importlib.util.spec_from_file_location("reclaim_breadth_readout_base", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = helper_module()
require, finite, close = BASE.require, BASE.finite, BASE.close
sha, location, read, records = BASE.sha, BASE.location, BASE.read, BASE.records
total, labels, distribution = BASE.total, BASE.labels, BASE.distribution
closed_distribution, execution_dates = BASE.closed_distribution, BASE.execution_dates


def in_interval(day, start=None, end=None):
    return day is not None and (start is None or start <= day <= end)


def formation_counts(original, schedule, account_opportunities, mode, start=None, end=None):
    """Retain all formation gates, independently from later account disposition."""
    by_id = {r["formationId"]: r for r in original}
    require(len(by_id) == len(original), "Duplicate original formation identity")
    selected = {r["signalId"]: r for r in schedule}
    executed = {r["signalId"]: r for r in account_opportunities}
    require(len(selected) == len(schedule) and len(executed) == len(account_opportunities),
            "Duplicate selected/account opportunity identity")
    require(set(selected) <= set(by_id) and set(executed) == set(selected),
            "Original/schedule/account identities disagree")
    cohort = [r for r in original if in_interval(r["formationDate"], start, end)]
    decisions = [executed[r["formationId"]] for r in cohort if r["formationId"] in selected]
    entered = sum(finite(r.get("filledQuantity")) and r["filledQuantity"] > 0 for r in decisions)
    statuses = Counter(r["gateDispositions"][mode]["status"] for r in cohort)
    require(set(statuses) <= {"admitted", "rejected", "unknown"}, "Unexpected gate disposition")
    require(statuses["admitted"] == len(decisions), "Admitted gate and actual schedule disagree")
    return {"formationTop5": len(cohort),
        "contextStateCounts": labels(r["contextState"] for r in cohort),
        "gateStatusCounts": {k: statuses[k] for k in ("admitted", "rejected", "unknown")},
        "gateReasonCounts": labels(r["gateDispositions"][mode]["reason"] for r in cohort),
        "selectedSignalsFromFormation": len(decisions),
        "notScheduledFromFormation": len(cohort) - len(decisions),
        "enteredFromFormation": entered, "notEnteredFromFormation": len(decisions) - entered,
        "terminalStatusCounts": labels(r.get("status", "missing") for r in decisions),
        "terminalReasonCounts": labels(r.get("reason", "missing") for r in decisions),
        "meaning": "formationDate cohort; gate, later account execution and cutoff status are separate"}


def interval(result, start, end, initial, original=(), schedule=(), mode=BASELINE):
    """Reuse frozen price arithmetic; correct queued sell accounting locally."""
    view = dict(result)
    view["research"] = {"opportunities": []}
    measured = BASE.interval(view, start, end, initial)
    decisions = [r for r in result.get("decisions", []) if start <= r["date"] <= end
                 and r.get("action") == "sell" and r.get("status") == "queued"]
    measured["queuedExitReasons"] = labels(r.get("reason", "missing") for r in decisions)
    measured["queuedExitDecisionCount"] = len(decisions)
    measured["queuedExitDefinition"] = "decision.action == sell AND decision.status == queued; queued decisions are not sell fills"
    account_opps = result.get("research", {}).get("opportunities", [])
    measured["opportunitiesByFormationDate"] = formation_counts(original, schedule, account_opps, mode, start, end)
    measured["opportunitiesByDecisionDate"] = BASE.decision_counts(schedule, account_opps, start, end)
    return measured


def metric_values(account):
    """Uniform levels for every predeclared contrast; undefined remains null."""
    values = deepcopy(account["comparisonRow"])
    summary, curve = account["account"], account["curveAttribution"]
    bridge = account["reconciliation"]
    values.update(netPnlAmount=summary["netPnlAmount"], finalEquity=summary["finalEquity"],
        closedPnlAmount=bridge["closedEpisodePnl"],
        openRealizedPnlAmount=bridge["openPositionRealizedPnl"],
        openUnrealizedPnlAmount=bridge["openPositionUnrealizedPnl"],
        investedSessionPct=curve["investedSessionPct"], feesPaid=curve["feesPaid"],
        slippageAlreadyInFillPrices=curve["slippageAlreadyInFillPrices"],
        buyFillCount=curve["buyFillCount"], sellFillCount=curve["sellFillCount"],
        notEntered=curve["opportunitiesByDecisionDate"]["notEntered"],
        notScheduled=curve["opportunitiesByFormationDate"]["notScheduledFromFormation"],
        top1ShareOfGrossProfitPct=account["concentration"]["top1ProfitSharePct"],
        top5ShareOfGrossProfitPct=account["concentration"]["top5ProfitSharePct"])
    return {key: value for key, value in values.items() if value is None or finite(value)}


def compare_accounts(accounts):
    indexed = {(a["period"], a["policy"]): a for a in accounts}
    require(len(indexed) == len(accounts) == 10, "Ten unique policy accounts required")
    comparisons, interactions = [], []
    for period in PERIODS:
        metrics = {mode: metric_values(indexed[(period, mode)]) for mode in POLICIES}
        keys = set(metrics[BASELINE])
        require(all(set(v) == keys for v in metrics.values()), "Comparison metric coverage differs")
        for contrast_id, control, candidate in CONTRASTS:
            comparisons.append({"period": period, "id": contrast_id, "control": control, "candidate": candidate,
                "controlMetrics": metrics[control], "candidateMetrics": metrics[candidate],
                "candidateMinusControl": {k: metrics[candidate][k] - metrics[control][k]
                    if finite(metrics[candidate][k]) and finite(metrics[control][k]) else None for k in sorted(keys)},
                "meaning": "full policy difference, including cash, exposure, execution and path interactions; not a causal effect"})
        interactions.append({"period": period, "formula": "breadthBoth - breadthMajority - breadthImproving + knownContext",
            "values": {k: metrics["breadthBoth"][k] - metrics["breadthMajority"][k]
                - metrics["breadthImproving"][k] + metrics["knownContext"][k]
                if all(finite(metrics[m][k]) for m in NEW_POLICIES) else None for k in sorted(keys)},
            "meaning": "descriptive arithmetic interaction between complete account metrics; nonlinear path statistics and outcome-selected accounts do not identify causal synergy"})
    return comparisons, interactions


def check_partition(measured_rows, overall, original_count, schedule_count, label):
    close(total(r["netPnlAmount"] for r in measured_rows), overall["netPnlAmount"], label + " PnL addition")
    compounded = (math.prod(1 + r["returnPct"] / 100 for r in measured_rows) - 1) * 100 \
        if all(finite(r["returnPct"]) for r in measured_rows) else None
    close(compounded, overall["returnPct"], label + " return compounding")
    require(sum(r["observedSessions"] for r in measured_rows) == overall["observedSessions"], label + " curve coverage")
    for field, expected in (("formationTop5", original_count), ("selectedSignalsFromFormation", schedule_count),
                            ("enteredFromFormation", overall["buyFillCount"])):
        require(sum(r["opportunitiesByFormationDate"][field] for r in measured_rows) == expected,
                label + " formation totals: " + field)
    for field in ("selectedSignals", "entered", "notEntered"):
        require(sum(r["opportunitiesByDecisionDate"][field] for r in measured_rows)
                == overall["opportunitiesByDecisionDate"][field], label + " decision totals: " + field)
    require(sum(r["closedEpisodesByExitDate"]["count"] for r in measured_rows)
            == overall["closedEpisodesByExitDate"]["count"], label + " closed episode totals")
    return {"kind": label, "passed": True, "intervals": len(measured_rows),
            "netPnlAmount": total(r["netPnlAmount"] for r in measured_rows), "compoundedReturnPct": compounded}


def normalize_formations(parent_original, baseline_schedule, contexts, evidence, counts):
    """Bind saved masks to every original immediate formation without re-ranking."""
    parent_ids = [r["formationId"] for r in parent_original]
    require(len(parent_ids) == len(set(parent_ids)), "Duplicate parent formation ID")
    require(baseline_schedule == [r["signals"][BASELINE] for r in parent_original],
            "Parent immediate schedule differs from its original formations")
    ctx = {r["formationDate"]: r for r in contexts}
    require(len(ctx) == len(contexts), "Duplicate context date")
    require(set(evidence) == set(counts) == set(NEW_POLICIES), "All four gate evidence/count streams required")
    state_masks = {"majorityImproving": (True, True, True, True),
        "majorityNotImproving": (True, True, False, False),
        "notMajorityImproving": (True, False, True, False),
        "notMajorityNotImproving": (True, False, False, False), "unknown": (False, False, False, False)}
    for mode in NEW_POLICIES:
        require([r["signalId"] for r in evidence[mode]] == parent_ids,
                "Evidence must retain every original ID and original order: " + mode)
        observed = Counter(r["status"] for r in evidence[mode])
        require(set(observed) <= {"included", "excluded", "unknown"}, "Unexpected evidence status")
        require(counts[mode] == {k: observed[k] for k in ("included", "excluded", "unknown")},
                "Input gate counts differ from complete evidence")
    result = []
    for i, (parent, signal) in enumerate(zip(parent_original, baseline_schedule)):
        day, sid = parent["formationDate"], parent["formationId"]
        require(signal["signalId"] == sid and signal["signalDate"] == day, "Immediate identity/date differs")
        context = ctx.get(day)
        state = context["state"] if context is not None else "unknown"
        require(state in state_masks, "Unexpected context state")
        gates = {BASELINE: {"status": "admitted", "reason": "originalImmediate"}}
        signals = {BASELINE: deepcopy(signal)}
        raw = {}
        for mode in NEW_POLICIES:
            row = evidence[mode][i]
            require(all(row[key] == signal[key] for key in ("signalId", "symbol", "signalDate", "originalRank"))
                    and row["formationDate"] == day and row["state"] == state,
                    "Evidence changed original identity, date, rank or context state")
            if context is not None:
                require(row["asOfDate"] == context["asOfDate"], "Evidence context as-of changed")
            raw[mode] = deepcopy(row)
            gates[mode] = {"status": {"included": "admitted", "excluded": "rejected", "unknown": "unknown"}[row["status"]],
                           "reason": row["reason"]}
            signals[mode] = deepcopy(signal) if row["status"] == "included" else None
        known, majority, improving, both = [signals[m] is not None for m in NEW_POLICIES]
        require((known, majority, improving, both) == state_masks[state], "Gate masks differ from frozen context quadrant")
        require(known == (state != "unknown"), "Known schedule does not match context availability")
        require(not (majority or improving or both) or known, "Policy admitted an unknown context")
        require(both == (majority and improving), "Intersection mask differs from the two component masks")
        require(all((gates[m]["status"] == "unknown") == (state == "unknown") for m in NEW_POLICIES),
                "Unknown context not retained in every policy")
        result.append({"formationId": sid, "formationDate": day, "symbol": signal["symbol"],
            "originalRank": signal["originalRank"], "originalSignal": deepcopy(signal),
            "contextState": state, "context": deepcopy(context), "gateEvidence": raw,
            "gateDispositions": gates, "signals": signals,
            "formationEvidence": deepcopy(parent), "accountDispositions": {}})
    return result


def check_binding(validation, plan_sha, comparison_sha):
    require(validation.get("passed") is True and validation.get("failureCount", 0) == 0,
            "Independent account validation has not passed")
    require(validation.get("planSha256") == plan_sha, "Validation plan binding differs")
    require(validation.get("comparisonSha256") == comparison_sha, "Validation comparison binding differs")


def build(directory, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    paths = {name: directory / name for name in ("plan.json", "comparison.json", "input-completion.json")}
    require(all(p.is_file() for p in (*paths.values(), validation_path, ATLAS)),
            "Frozen plan/comparison/completion/validation/atlas are required")
    require(sha(ATLAS) == ATLAS_SHA, "Original atlas hash changed")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        digest = sha(path)
        old = pins.setdefault(location(path), digest)
        require(old == digest, "Input changed while reading: " + location(path))
        return path

    def bound(reference):
        require(isinstance(reference, dict) and set(reference) >= {"path", "sha256"}, "Invalid artifact reference")
        path = Path(reference["path"])
        path = path if path.is_absolute() else ROOT / path
        require(sha(pin(path)) == reference["sha256"], "Artifact binding differs: " + reference["path"])
        return path

    for path in (*paths.values(), validation_path, ATLAS, Path(__file__), HELPER):
        pin(path)
    plan, comparison = read(paths["plan.json"]), read(paths["comparison.json"])
    completion, validation = read(paths["input-completion.json"]), read(validation_path)
    plan_sha = sha(paths["plan.json"])
    check_binding(validation, plan_sha, sha(paths["comparison.json"]))
    require(plan["modes"] == list(NEW_POLICIES) and plan["baselineMode"] == BASELINE, "Registered policy set differs")
    require(comparison["planSha256"] == completion["planSha256"] == plan_sha, "Plan receipt binding differs")
    require(completion.get("allSchedulesFrozenBeforeAccounts") is True, "Schedules were not all frozen before accounts")
    for key, expected in (("newAccounts", 8), ("newPolicyVariants", 4), ("referenceAccountsReused", 2)):
        require(plan[key] == comparison[key] == expected, "Trial accounting differs: " + key)
    require(plan["newSetupMechanisms"] == 0 and plan["parameterSearch"] is False and plan["profitHurdles"] is None,
            "This readout is for the frozen four admission policies only")
    for name in ("comparison.json", "readout.json", "validation.json"):
        path = pin(PARENT / name)
        require(plan["frozenInputs"][location(path)] == sha(path), "Parent evidence is not the frozen version")
    parent_comparison, parent_readout = read(PARENT / "comparison.json"), read(PARENT / "readout.json")
    require(parent_readout["inputPins"][location(HELPER)] == sha(HELPER), "Frozen helper source changed")
    rows = deepcopy(comparison["rows"])
    require(len(rows) == 8 and {(r["period"], r["id"]) for r in rows}
            == {(p, m) for p in PERIODS for m in NEW_POLICIES}, "Eight unique new account rows required")
    rows.extend(deepcopy(r) for r in parent_comparison["rows"] if r["id"] == BASELINE)
    by_account = {(r["period"], r["id"]): r for r in rows}
    require(len(rows) == len(by_account) == 10, "Exactly ten unique comparison rows required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == len({p["id"] for p in phases}) == 16, "All sixteen atlas phases required")
    accounts, years, halves, phase_rows, epochs, coverage = [], [], [], [], [], []
    opportunities, period_counts, partitions, source_bindings = [], {}, [], {}
    for period in PERIODS:
        folder, spec = directory / period, plan["periods"][period]
        receipt_path = bound(completion["outputs"][period])
        require(receipt_path == (folder / "input-completion.json").resolve(), "Wrong period receipt path")
        receipt = read(receipt_path)
        require(receipt["planSha256"] == plan_sha and receipt["period"] == period, "Period input receipt differs")
        input_paths = {name: bound(reference) for name, reference in receipt["files"].items()}
        require(all(path.parent == folder for path in input_paths.values()), "Period input path outside registered folder")
        baseline_paths = {name: bound(reference) for name, reference in spec["baselineFiles"].items()}
        counts, contexts = read(input_paths["input-counts.json"]), read(input_paths["contexts.json"])
        require(counts == receipt["counts"], "Period counts differ from completion")
        require(receipt["accountData"] == spec["accountData"], "Account data reference differs")
        metadata = read(baseline_paths["signal-metadata.json"])
        target_dates = metadata["targetDates"]
        require(target_dates == sorted(set(target_dates)), "Source target calendar must be ordered and unique")
        require([r["formationDate"] for r in contexts] == target_dates, "Daily contexts do not cover source target calendar")
        parent_original = records(baseline_paths["opportunities.jsonl.gz"])
        original_schedule = read(baseline_paths["immediate.signals.json"])
        evidence = {m: read(input_paths[m + ".evidence.json"]) for m in NEW_POLICIES}
        original = normalize_formations(parent_original, original_schedule, contexts, evidence, counts)
        require(len(original) == spec["originalSignalCount"], "Original opportunity count differs")
        for row in original:
            row["period"] = period
        opportunities.extend(original)
        period_counts[period] = {"originalFormations": len(original), "targetSessions": len(target_dates),
            "contextStateCounts": labels(r["contextState"] for r in original),
            "dailyContextStateCounts": labels(r["state"] for r in contexts), "gates": deepcopy(counts)}
        source_bindings[period] = {k: deepcopy(spec[k]) for k in ("config", "accountData", "sourceIdentity", "contextBinding", "baselineFiles")}
        source_bindings[period]["newInputCompletion"] = deepcopy(completion["outputs"][period])
        require(read(input_paths["source.json"]) == spec["sourceIdentity"] == read(baseline_paths["source.json"]),
                "Account source identities differ")
        for mode in POLICIES:
            reused = mode == BASELINE
            if reused:
                account_paths = {suffix: baseline_paths[mode + suffix]
                                 for suffix in (".signals.json", ".summary.json", ".result.json.gz", ".checkpoint.json.gz")}
            else:
                account_paths = {".signals.json": input_paths[mode + ".signals.json"]}
                account_paths.update({suffix: bound(comparison["artifacts"][period][mode + suffix])
                                      for suffix in (".summary.json", ".result.json.gz", ".checkpoint.json.gz")})
            schedule = read(account_paths[".signals.json"])
            expected_schedule = [r["signals"][mode] for r in original if r["signals"][mode] is not None]
            require(schedule == expected_schedule, "Policy schedule identity/order changed")
            result, summary = read(account_paths[".result.json.gz"]), read(account_paths[".summary.json"])
            require(result["status"] == "completed" and summary["policyId"] == mode, "Uncompleted or wrong account")
            require(not summary["quality"]["integrityIssues"], "Account summary contains integrity issues")
            config, initial = spec["config"], spec["config"]["initialCapital"]
            require(initial == 500000 and [r["date"] for r in result["curve"]] == target_dates,
                    "Account initial capital or curve calendar differs")
            observed = execution_dates(result)
            require(set(observed) == {r["signalId"] for r in schedule}, "Schedule/account opportunity coverage differs")
            for formation in original:
                sid = formation["formationId"]
                detail = observed.get(sid)
                gate = formation["gateDispositions"][mode]
                require((detail is not None) == (gate["status"] == "admitted"), "Gate/account binding differs")
                formation["accountDispositions"][mode] = {"formationId": sid, "formationDate": formation["formationDate"],
                    "decisionDate": formation["formationDate"] if detail else None, "gateStatus": gate["status"],
                    "gateReason": gate["reason"],
                    **(detail if detail is not None else {"signalId": sid, "signalDate": None, "entryDate": None,
                        "exitDate": None, "positionState": "notScheduled", "status": "notScheduled", "reason": gate["reason"],
                        "filledQuantity": 0, "attemptDate": None, "closedReturnPct": None, "closedPnlAmount": None})}
            overall = interval(result, config["startDate"], config["endDate"], initial, original, schedule, mode)
            saved, row = summary["account"], by_account[(period, mode)]
            for key, source in (("returnPct", "netReturnPct"), ("maxDrawdownPct", "maxDrawdownPct"),
                                ("netPnlAmount", "netPnlAmount"), ("endingEquity", "finalEquity"),
                                ("feesPaid", "feesPaid"), ("buyFillCount", "buyFillCount"), ("sellFillCount", "sellFillCount")):
                close(overall[key], saved[source], f"{period}/{mode}/{key}")
            closed = overall["closedEpisodesByExitDate"]
            close(closed["netPnlAmount"], summary["closedEpisodes"]["netPnlAmount"], "Whole closed net PnL")
            close(closed["returnPct"]["mean"], summary["closedEpisodes"]["meanReturnPct"], "Whole closed return mean")
            for key, actual in (("accountReturnPct", overall["returnPct"]), ("maxDrawdownPct", overall["maxDrawdownPct"]),
                                ("meanExposurePct", overall["exposurePct"]["mean"]), ("closed", closed["count"]),
                                ("signals", len(schedule)), ("openPositions", summary["openPositions"]["count"])):
                close(row[key], actual, "Comparison " + key)
            entered = overall["opportunitiesByDecisionDate"]["entered"]
            require(entered == overall["buyFillCount"] == closed["count"] + summary["openPositions"]["count"],
                    "Entry/closed/open counts differ")
            open_positions = deepcopy(summary["openPositions"])
            open_positions["unrealizedPnlAmount"] = total(p["unrealizedPnlAmount"] for p in open_positions["positions"])
            open_positions["realizedPnlWhileOpen"] = total(p["realizedPnlWhileOpen"] for p in open_positions["positions"])
            bridge = summary["reconciliation"]
            close(open_positions["unrealizedPnlAmount"], bridge["openPositionUnrealizedPnl"], "Open unrealized bridge")
            close(open_positions["realizedPnlWhileOpen"], bridge["openPositionRealizedPnl"], "Open realized bridge")
            close(total([closed["netPnlAmount"], open_positions["unrealizedPnlAmount"],
                         open_positions["realizedPnlWhileOpen"], bridge["rightsAttributionGapAmount"]]),
                  saved["netPnlAmount"], "Full closed/open/unallocated PnL bridge")
            account = {"period": period, "id": mode, "policy": mode, "label": summary["policyLabel"],
                "execution": "reused-original-immediate" if reused else "new-independent-account",
                "comparisonRow": deepcopy(row), "account": deepcopy(saved), "curveAttribution": overall,
                "closedEpisodes": deepcopy(summary["closedEpisodes"]), "closedDistribution": closed,
                "openPositionsAtCutoff": open_positions, "unresolvedExits": deepcopy(summary["unresolvedExits"]),
                "concentration": deepcopy(summary["concentration"]), "reconciliation": deepcopy(bridge),
                "sourceQuality": deepcopy(summary["sourceQuality"]), "quality": deepcopy(summary["quality"]),
                "sourceQualityScalarFlags": {k: v for k, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                "rights": deepcopy(summary["rights"]), "warnings": deepcopy(summary.get("warnings", [])),
                "opportunityDispositions": deepcopy(summary["opportunityDispositions"]),
                "artifactRefs": {suffix: {"path": location(path), "sha256": sha(path)} for suffix, path in account_paths.items()}}
            accounts.append(account)
            coverage.append({"period": period, "id": mode, "policy": mode,
                **deepcopy(overall["opportunitiesByFormationDate"]), **deepcopy(overall["opportunitiesByDecisionDate"]),
                "unknownContextFormations": sum(r["contextState"] == "unknown" for r in original),
                "knownContextFormations": sum(r["contextState"] != "unknown" for r in original),
                "buyFillCount": overall["buyFillCount"], "sellFillCount": overall["sellFillCount"],
                "closedCount": closed["count"], "openCountAtCutoff": open_positions["count"],
                "unresolvedExitCountAtCutoff": summary["unresolvedExits"]["count"],
                "curveSessions": len(target_dates), "baselineReused": reused})
            account_years, account_halves = [], []
            for saved_period in summary["periods"]:
                if saved_period["kind"] not in ("calendarYear", "calendarHalfYear"):
                    continue
                measured = interval(result, saved_period["requestedStart"], saved_period["requestedEnd"], initial, original, schedule, mode)
                for key in ("returnPct", "maxDrawdownPct", "netPnlAmount", "enteringEquity", "endingEquity"):
                    close(measured[key], saved_period[key], "Saved yearly/halfyear metric: " + key)
                period_row = BASE.compact_period(saved_period, period, mode)
                period_row.update(measured)
                period_row["accountMode"] = mode
                (account_years if saved_period["kind"] == "calendarYear" else account_halves).append(period_row)
            years.extend(account_years)
            halves.extend(account_halves)
            relevant = [p for p in phases if p["startDate"] <= config["endDate"] and p["endDate"] >= config["startDate"]]
            require(all(sum(p["startDate"] <= d <= p["endDate"] for p in relevant) == 1 for d in target_dates),
                    "Atlas phases overlap or omit target dates")
            account_phases = []
            for phase in relevant:
                measured = interval(result, phase["startDate"], phase["endDate"], initial, original, schedule, mode)
                measured.update(period=period, id=mode, policy=mode, phaseId=phase["id"], phaseTitle=phase["title"],
                    sentimentSummary=phase["sentimentSummary"], dominantThemes=deepcopy(phase["dominantThemes"]),
                    representatives=deepcopy(phase["representatives"]), labelAvailability=phase["labelAvailability"],
                    attributionMeaning="complete retrospective calendar intervals; carries positions and unrealized changes; never an entry filter")
                account_phases.append(measured)
            phase_rows.extend(account_phases)
            tests = [check_partition(part, overall, len(original), len(schedule), label)
                     for part, label in ((account_years, "years"), (account_halves, "halfyears"), (account_phases, "phases"))]
            if period == "2024-2025":
                account_epochs = []
                for epoch_id, start, end in EPOCHS:
                    measured = interval(result, start, end, initial, original, schedule, mode)
                    measured.update(period=period, id=mode, policy=mode, phaseId=epoch_id,
                        attributionMeaning="user-specified event boundary; continuous curve with actual prior equity and carried positions; no restart or liquidation")
                    account_epochs.append(measured)
                epochs.extend(account_epochs)
                tests.append(check_partition(account_epochs, overall, len(original), len(schedule), "userEventEpochs"))
            partitions.append({"period": period, "policy": mode, "checks": tests, "passed": True})
    require((len(accounts), len(years), len(halves), len(phase_rows), len(epochs), len(coverage))
            == (10, 20, 40, 80, 10, 10), "Incomplete account/year/halfyear/phase/epoch/coverage matrix")
    require(len(opportunities) == 3803 and len({(r["period"], r["formationId"]) for r in opportunities}) == 3803,
            "All 3803 original formations must be retained exactly once")
    require(all(set(r["accountDispositions"]) == set(POLICIES) for r in opportunities), "Missing per-policy disposition")
    comparisons, interactions = compare_accounts(accounts)
    readout = {"schema": "reclaim-breadth-entry-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "inputPins": pins, "sourceBindings": source_bindings,
        "verification": {"passed": True, "path": location(validation_path), "sha256": sha(validation_path),
            "meaning": "independent gate/account validation plus this readout's complete arithmetic and coverage reconciliation; not strategy certification"},
        "method": {"policySet": "Original immediate baseline I plus K known context, L majority, M improving, B both; only four new entry-admission policies.",
            "primaryContrast": "L minus K; secondary M minus K, B minus L, B minus M. K minus I is the evidence-availability policy difference.",
            "interaction": "B-L-M+K over complete policy metrics is descriptive arithmetic, not a causal interaction or randomized factorial estimate.",
            "population": "All original 3803 frozen Top5 formations, before execution or outcome, retain every gate and actual account disposition; no re-ranking or backfill.",
            "clock": "Exact T-1 matched-five-session breadth, with earlier T-6; T close decision then existing next-source-open attempt. No market-driven forced exits.",
            "account": "Ten separate two-year capital paths: eight new accounts plus two original immediate references. No four-year chaining, annualization or account rerun by this script.",
            "phaseStart": "Last supplied curve equity strictly before interval start; otherwise registered 500000. Include entering equity in interval drawdown.",
            "phasePnl": "Ending minus entering equity includes carried holdings and unrealized changes. Net amounts add; return percentages compound.",
            "closedEpisodes": "Complete closed episodes by exitDate; conditional on admission and closed status, separate from calendar mark-to-market PnL and terminal open holdings.",
            "openPositions": "All terminal holdings, realized PnL while open, unrealized PnL, holding age, unresolved exits and quality preserved.",
            "formationDates": "Gate cohorts use formationDate; decisions, fills and closed episodes use their observed respective dates. Terminal dispositions are at account cutoff.",
            "queuedExitCorrection": "New wrapper counts action=sell AND status=queued. Original frozen summary/helper artifacts are unchanged.",
            "phaseDefinition": "All original sixteen hindsight atlas intervals; descriptive only. 2024-09-24 user boundary is neither an entry gate nor an account restart.",
            "unknowns": "Missing numeric evidence stays null; unknown gate evidence remains in denominators. No complete-case outcome deletion.",
            "parameterSearch": False, "profitHurdles": None, "newTrialsByThisScript": 0, "accountsRerunByThisScript": 0},
        "accounts": accounts, "accountRows": [deepcopy(by_account[(p, m)]) for p in PERIODS for m in POLICIES],
        "comparisons": comparisons, "knownVsOriginalRows": [r for r in comparisons if r["id"] == "knownVsOriginal"],
        "descriptiveInteractionRows": interactions, "yearRows": years, "halfYearRows": halves,
        "phaseRows": phase_rows, "curveEpochRows": epochs, "coverageRows": coverage,
        "opportunityRows": opportunities, "inputCountsByPeriod": period_counts, "partitionReconciliation": partitions,
        "trialAccounting": {"experimentBatches": 1, "newSetupMechanisms": 0, "newPolicyVariants": 4,
            "newAccountExecutions": 8, "referenceAccountsReused": 2, "summarizerAccountExecutions": 0},
        "limitations": list(dict.fromkeys(plan["limitations"] + [
            "Reduced exposure or drawdown alone is not incremental entry skill; no return divided by exposure is treated as an independent counterfactual.",
            "Closed means select both execution and exit maturity; long right tails may remain open at cutoff and cross several regimes.",
            "All context outcomes and historical periods were already examined. No pristine holdout or future stable-profit claim.",
            "Cash, holdings, capacity, fees and later opportunities interact across independent policies. Arithmetic differences and B-L-M+K do not identify causality.",
            "Formation-cohort attribution and calendar-interval PnL differ because positions carry across boundaries.",
            "Unallocated PnL/rights residuals remain explicitly separate; no residual is assigned to a preferred policy or state."])),
        "decision": {"selectedPolicy": None, "automaticNextStrategy": None, "profitHurdle": None},
        "certified": False, "targetAchieved": False}
    for path, expected in pins.items():
        require(sha(ROOT / path if not Path(path).is_absolute() else Path(path)) == expected,
                "Input changed during summarization: " + path)
    destination = directory / "readout.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(readout, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "path": location(destination), "sha256": sha(destination),
        "accounts": len(accounts), "yearRows": len(years), "halfYearRows": len(halves), "phaseRows": len(phase_rows),
        "curveEpochRows": len(epochs), "coverageRows": len(coverage), "originalFormations": len(opportunities),
        "comparisons": len(comparisons), "descriptiveInteractionRows": len(interactions), "certified": False}


def self_test():
    days = ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05")
    result = {"curve": [{"date": d, "equity": v, "cash": v / 2, "marketValue": v / 2,
                          "valuationReliable": True} for d, v in zip(days, (100., 80., 90., 99.))],
        "daily": [{"date": d, "holdings": [{"holdingDays": i}]} for i, d in enumerate(days, 1)],
        "trades": [], "closedTrades": [], "research": {"opportunities": []},
        "decisions": [{"date": days[1], "action": "sell", "status": "queued", "reason": "stopLoss"},
                      {"date": days[1], "action": "sell", "status": "rejected", "reason": "bad"},
                      {"date": days[1], "action": "buy", "status": "queued", "reason": "bad"},
                      {"date": days[1], "action": "exit", "status": "queued", "reason": "bad"},
                      {"date": days[3], "action": "sell", "status": "queued", "reason": "later"}]}
    mid = interval(result, days[1], days[2], 125.)
    close(mid["enteringEquity"], 100., "Include entering close")
    close(mid["returnPct"], -10., "Interval return")
    close(mid["maxDrawdownPct"], 20., "Drawdown from entering close")
    require(mid["queuedExitReasons"] == {"stopLoss": 1} and mid["queuedExitDecisionCount"] == 1,
            "Only queued sell decisions counted")
    require(result["decisions"][3]["action"] == "exit", "Original result mutated")
    whole = interval(result, days[0], days[-1], 125.)
    parts = [interval(result, days[0], days[0], 125.), mid, interval(result, days[-1], days[-1], 125.)]
    check_partition(parts, whole, 0, 0, "synthetic")
    damaged = deepcopy(result)
    damaged["curve"][1]["equity"] = None
    require(interval(damaged, days[1], days[2], 125.)["returnPct"] is None, "Missing equity compressed")
    require(interval(result, "2024-02-01", "2024-02-02", 125.)["returnPct"] is None, "Empty interval is zero")
    require(distribution([1., None])["mean"] is None, "Unknowns removed from distribution")
    def formation(sid, gate):
        return {"formationId": sid, "formationDate": days[0], "contextState": "unknown" if gate == "unknown" else "state",
                "gateDispositions": {m: {"status": gate, "reason": gate} for m in POLICIES}}
    original = [formation("a", "admitted"), formation("b", "rejected"), formation("c", "unknown")]
    schedule = [{"signalId": "a", "symbol": "a", "strategyId": "test", "signalDate": days[0], "originalRank": 1}]
    opps = [{**schedule[0], "filledQuantity": 1, "status": "filled", "reason": "filled"}]
    counts = formation_counts(original, schedule, opps, "breadthBoth")
    require(counts["formationTop5"] == 3 and counts["enteredFromFormation"] == 1
            and counts["gateStatusCounts"] == {"admitted": 1, "rejected": 1, "unknown": 1}, "Full gate denominators")
    bad = deepcopy(original)
    bad[1]["gateDispositions"]["breadthBoth"]["status"] = "admitted"
    try: formation_counts(bad, schedule, opps, "breadthBoth")
    except ValueError: pass
    else: raise AssertionError("Gate/schedule mismatch accepted")
    accounts = []
    for period in PERIODS:
        for n, mode in enumerate(POLICIES):
            accounts.append({"period": period, "policy": mode, "comparisonRow": {"accountReturnPct": float(n), "closedMeanPct": None},
                "account": {"netPnlAmount": float(n), "finalEquity": 100. + n},
                "curveAttribution": {"investedSessionPct": 50., "feesPaid": 1., "slippageAlreadyInFillPrices": 1.,
                    "buyFillCount": 1, "sellFillCount": 0, "opportunitiesByDecisionDate": {"notEntered": 0},
                    "opportunitiesByFormationDate": {"notScheduledFromFormation": 0}},
                "reconciliation": {"closedEpisodePnl": 0., "openPositionRealizedPnl": 0., "openPositionUnrealizedPnl": float(n)},
                "concentration": {"top1ProfitSharePct": None, "top5ProfitSharePct": None}})
    comparisons, interactions = compare_accounts(accounts)
    require(len(comparisons) == 10 and len(interactions) == 2, "Full predeclared contrast coverage")
    close(comparisons[0]["candidateMinusControl"]["accountReturnPct"], 1., "K-original sign")
    close(interactions[0]["values"]["accountReturnPct"], 0., "B-L-M+K sign")
    require(interactions[0]["values"]["closedMeanPct"] is None, "Undefined interaction filled")
    synthetic_dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    state_masks = [("majorityImproving", (True, True, True, True)),
        ("majorityNotImproving", (True, True, False, False)),
        ("notMajorityImproving", (True, False, True, False)),
        ("notMajorityNotImproving", (True, False, False, False)), ("unknown", (False, False, False, False))]
    parent_original, original_schedule, contexts = [], [], []
    evidence = {m: [] for m in NEW_POLICIES}
    for n, (day, (state, masks)) in enumerate(zip(synthetic_dates, state_masks)):
        signal = {"signalId": f"test:{day}:symbol", "strategyId": "test", "symbol": "symbol",
                  "signalDate": day, "originalRank": 1}
        parent_original.append({"formationId": signal["signalId"], "formationDate": day,
                                "signals": {BASELINE: signal}})
        original_schedule.append(signal)
        context = {"formationDate": day, "asOfDate": synthetic_dates[n - 1] if n else None, "state": state}
        contexts.append(context)
        for mode, mask in zip(NEW_POLICIES, masks):
            status = "unknown" if state == "unknown" else "included" if mask else "excluded"
            evidence[mode].append({k: signal[k] for k in ("signalId", "symbol", "signalDate", "originalRank")}
                | {"formationDate": day, "asOfDate": context["asOfDate"], "state": state, "status": status, "reason": status})
    counts = {m: {s: sum(r["status"] == s for r in evidence[m]) for s in ("included", "excluded", "unknown")}
              for m in NEW_POLICIES}
    normalized = normalize_formations(parent_original, original_schedule, contexts, evidence, counts)
    require(len(normalized) == 5 and normalized[-1]["signals"][BASELINE] == original_schedule[-1]
            and all(normalized[-1]["signals"][m] is None for m in NEW_POLICIES), "Original unknown baseline removed")
    require([sum(r["signals"][m] is not None for r in normalized) for m in POLICIES] == [5, 4, 2, 2, 1],
            "Four quadrants, unknown and common admission intersection")
    for mutation in ("reverse", "rank", "state", "count", "intersection"):
        bad_evidence, bad_counts = deepcopy(evidence), deepcopy(counts)
        if mutation == "reverse": bad_evidence["knownContext"].reverse()
        elif mutation == "rank": bad_evidence["breadthMajority"][0]["originalRank"] = 2
        elif mutation == "state": bad_evidence["breadthMajority"][0]["state"] = "unknown"
        elif mutation == "count": bad_counts["breadthMajority"]["included"] += 1
        else:
            bad_evidence["breadthBoth"][1]["status"] = "included"
            bad_counts["breadthBoth"]["included"] += 1
            bad_counts["breadthBoth"]["excluded"] -= 1
        try: normalize_formations(parent_original, original_schedule, contexts, bad_evidence, bad_counts)
        except ValueError: pass
        else: raise AssertionError("Malformed gate binding accepted: " + mutation)
    valid = {"passed": True, "planSha256": "plan", "comparisonSha256": "comparison"}
    check_binding(valid, "plan", "comparison")
    for mutation in ({"passed": False}, {"planSha256": "wrong"}, {"comparisonSha256": "wrong"}, {"failureCount": 1}):
        try: check_binding(valid | mutation, "plan", "comparison")
        except ValueError: pass
        else: raise AssertionError("Unvalidated input accepted")
    return {"passed": True, "checks": ["previous-close and initial-capital interval boundaries",
        "entering equity in drawdown", "queued sell/status correction without source mutation", "partition addition and compounding",
        "empty and missing values retained", "all gate and execution denominators", "gate schedule mismatch rejected",
        "all registered contrasts and interaction signs", "undefined contrast values remain null",
        "all four quadrants and original unknown baseline retained", "evidence order/rank/state/count/intersection binding",
        "passed validation and exact plan/comparison binding"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory, args.validation), ensure_ascii=False))
