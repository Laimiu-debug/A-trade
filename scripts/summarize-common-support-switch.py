"""Describe common-support switching after all 84 account references are audited.

The 60 inherited accounts and their already-audited intervals are copied, not
rerun. Only the 24 new accounts are joined to their original opportunities.
No model fitting, prediction-error recomputation, or strategy execution occurs.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "artifacts/strategy-search/common-support-switch-v1"
HELPER = ROOT / "scripts/summarize-online-feedback-entry.py"
spec = importlib.util.spec_from_file_location("common_support_account_math", HELPER)
ACCOUNT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ACCOUNT)
MATH = ACCOUNT.MATH
require, finite, close = ACCOUNT.require, ACCOUNT.finite, ACCOUNT.close
sha, digest, read = ACCOUNT.sha, ACCOUNT.digest, ACCOUNT.read
PERIODS, BOARDS, EXITS = ACCOUNT.PERIODS, ACCOUNT.BOARDS, ACCOUNT.EXITS
MODELS = ("intercept", "ridge")
POPULATIONS = ("originalEntered", "allExecutable")
INHERITED = ("baseline", *(p + "__" + m for p in POPULATIONS for m in MODELS))
NEW = tuple("commonSupport__" + m for m in MODELS)
CANDIDATES = (*INHERITED, *NEW)
STATES = ("bothFitted", "originalOnlyFitted", "allOnlyFitted", "neitherFitted")
CONTRASTS = tuple(("commonVs" + label + m.title(), p + "__" + m, "commonSupport__" + m, family)
    for m in MODELS for label, p, family in (("Original", "originalEntered", "commonSupportChange"),
                                           ("All", "allExecutable", "extraCoverageRemoved")))
KEYS = ("period", "board", "exit", "candidate")


def records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def identity(row):
    return tuple(row[k] for k in KEYS)


def candidate_identity(candidate):
    require(candidate in CANDIDATES, "Unknown account identity")
    population, model = (None, None) if candidate == "baseline" else candidate.split("__")
    return {"candidate": candidate, "population": population, "model": model}


def model_value(row, model):
    value = row["models"][model]
    require(value["status"] in ("fitted", "fallback") and type(value["allow"]) is bool, "Missing frozen model status")
    if value["status"] == "fitted":
        require(finite(value["prediction"]) and value["allow"] == (value["prediction"] > 0), "Frozen prediction/allow differs")
    else:
        require(value["prediction"] is None and value["allow"] is True, "Fallback must retain original signals")
    return value


def switch_coverage(key, model, original, decisions, schedule, target_dates, prepared_counts=None):
    """Check frozen mask selection without fitting or creating a schedule file."""
    require(model in MODELS and target_dates == sorted(set(target_dates)), "Unknown model or unordered target dates")
    indexes = {}
    for population in POPULATIONS:
        daily = decisions[population]
        require([r["formationDate"] for r in daily] == target_dates, "Complete aligned daily decisions required")
        indexes[population] = {r["formationDate"]: r for r in daily}
        require(len(indexes[population]) == len(daily), "Duplicate source decision date")
    rows, counts_by_day = [], {}
    for day in target_dates:
        old, all_ = (indexes[p][day] for p in POPULATIONS)
        require(old["asOfDate"] == all_["asOfDate"], "Different prediction information dates")
        a, z = model_value(old, model), model_value(all_, model)
        flags = a["status"] == "fitted", z["status"] == "fitted"
        state = "bothFitted" if all(flags) else "originalOnlyFitted" if flags[0] else "allOnlyFitted" if flags[1] else "neitherFitted"
        population = "allExecutable" if state == "bothFitted" else "originalEntered"
        chosen = z if population == "allExecutable" else a
        row = {"formationDate": day, "asOfDate": old["asOfDate"], "supportState": state,
            "selectedPopulation": population, "originalAllow": a["allow"], "allAllow": z["allow"],
            "selectedAllow": chosen["allow"], "changedAllow": chosen["allow"] != a["allow"],
            "models": {model: deepcopy(chosen)}}
        rows.append(row); counts_by_day[day] = row
    original_ids = [r["signalId"] for r in original]
    require(len(original_ids) == len(set(original_ids)) and all(r["signalDate"] in counts_by_day for r in original), "Invalid original opportunity identity")
    expected = [r for r in original if counts_by_day[r["signalDate"]]["selectedAllow"]]
    require(expected == schedule, "Scheduled identities/fields/order differ from frozen switch")
    def state_counts(source):
        return {s: sum(r["supportState"] == s for r in source) for s in STATES}
    signal_rows = [counts_by_day[r["signalDate"]] for r in original]
    counts = {"originalSignals": len(original), "scheduledSignals": len(schedule),
        "rejectedSignals": len(original) - len(schedule), "decisionDays": len(rows),
        "supportStateDays": state_counts(rows), "supportStateSignals": state_counts(signal_rows),
        "admittedSignalsBySupportState": state_counts([r for r in signal_rows if r["selectedAllow"]]),
        "rejectedSignalsBySupportState": state_counts([r for r in signal_rows if not r["selectedAllow"]]),
        "selectedPopulationDays": {p: sum(r["selectedPopulation"] == p for r in rows) for p in POPULATIONS},
        "selectedPopulationSignals": {p: sum(r["selectedPopulation"] == p for r in signal_rows) for p in POPULATIONS},
        "originalAllowedDays": sum(r["originalAllow"] for r in rows), "allAllowedDays": sum(r["allAllow"] for r in rows),
        "selectedAllowedDays": sum(r["selectedAllow"] for r in rows),
        "originalAllowedSignals": sum(r["originalAllow"] for r in signal_rows),
        "allAllowedSignals": sum(r["allAllow"] for r in signal_rows),
        "changedAllowDays": sum(r["changedAllow"] for r in rows), "changedAllowSignals": sum(r["changedAllow"] for r in signal_rows),
        "addedSignals": sum(not r["originalAllow"] and r["selectedAllow"] for r in signal_rows),
        "removedSignals": sum(r["originalAllow"] and not r["selectedAllow"] for r in signal_rows)}
    require(sum(counts["supportStateDays"].values()) == len(rows)
        and sum(counts["supportStateSignals"].values()) == len(original), "Support-state denominator differs")
    require(all(not r["changedAllow"] or r["supportState"] == "bothFitted" for r in rows)
        and all(r["selectedAllow"] == r["allAllow"] or r["supportState"] != "bothFitted" for r in rows), "Switch changed the wrong support regime")
    if prepared_counts is not None:
        require(prepared_counts == counts, "Prepared switch counts differ")
    coverage = {**key, **candidate_identity("commonSupport__" + model), "targetDayCount": len(rows),
        "originalOpportunities": len(original), "scheduledSignals": len(schedule), "notScheduledSignals": len(original) - len(schedule),
        **{s + "Days": counts["supportStateDays"][s] for s in STATES},
        **{s + "Signals": counts["supportStateSignals"][s] for s in STATES},
        "selectedAllDays": counts["selectedPopulationDays"]["allExecutable"],
        "selectedOriginalDays": counts["selectedPopulationDays"]["originalEntered"],
        "selectedAllSignals": counts["selectedPopulationSignals"]["allExecutable"],
        "selectedOriginalSignals": counts["selectedPopulationSignals"]["originalEntered"],
        **{k: deepcopy(counts[k]) for k in ("changedAllowDays", "changedAllowSignals", "addedSignals", "removedSignals",
            "supportStateDays", "supportStateSignals", "admittedSignalsBySupportState", "rejectedSignalsBySupportState")},
        "modelStatusCounts": MATH.labels(r["models"][model]["status"] for r in rows),
        "commonVsAllChangedAllowDays": sum(r["selectedAllow"] != r["allAllow"] for r in rows),
        "commonVsAllChangedAllowSignals": sum(r["selectedAllow"] != r["allAllow"] for r in signal_rows),
        "commonVsAllAddedSignals": sum(not r["allAllow"] and r["selectedAllow"] for r in signal_rows),
        "commonVsAllRemovedSignals": sum(r["allAllow"] and not r["selectedAllow"] for r in signal_rows),
        "preparedCounts": counts,
        "meaning": "All target days and original opportunities remain in separate denominators. Unprefixed changes are versus originalEntered and occur only on bothFitted days. commonVsAll changes are versus allExecutable and occur only outside bothFitted. The switch is not simply an all-only-date filter: originalOnlyFitted days can also differ from allExecutable fallback."}
    return coverage, rows


def reuse_tables(parent, reference_rows):
    """Normalize current experiment provenance without altering saved numbers."""
    reference = {identity(r): r for r in reference_rows}
    expected = {(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in INHERITED}
    require(len(reference) == len(reference_rows) == 60 and set(reference) == expected, "All 60 exact inherited descriptors required")
    account_index = {identity(r): r for r in parent["accounts"]}
    flat_index = {identity(r): r for r in parent["accountRows"]}
    require(len(account_index) == len(parent["accounts"]) == len(flat_index) == len(parent["accountRows"]) == 60
            and set(account_index) == set(flat_index) == expected, "Parent account matrix incomplete")
    accounts, flat = [], []
    for k in reference:
        account, row = account_index[k], flat_index[k]
        require(account["artifacts"] == reference[k], "Inherited account descriptor changed")
        metadata = {"executionOrigin": "reused", "reusedAccount": True,
                    "sourceReusedAccount": reference[k]["reusedAccount"]}
        accounts.append({**deepcopy(account), **metadata})
        flat.append({**deepcopy(row), **metadata})
    tables = {}
    for field, count in (("yearRows", 120), ("halfYearRows", 240), ("phaseRows", 480), ("curveEpochRows", 60),
                         ("coverageRows", 60), ("partitionReconciliation", 60)):
        source = parent[field]
        require(len(source) == count and all(identity(r) in expected for r in source), "Parent table count/identity differs: " + field)
        tables[field] = [{**deepcopy(r), "executionOrigin": "reused"} for r in source]
    return accounts, flat, tables


def compare_accounts(rows):
    index = {identity(r): r for r in rows}
    expected = {(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in CANDIDATES}
    require(len(index) == len(rows) == 84 and set(index) == expected, "Complete 84-account matrix required")
    output = []
    for p in PERIODS:
        for b in BOARDS:
            for e in EXITS:
                for cid, control, candidate, family in CONTRASTS:
                    a, z = (index[(p, b, e, c)]["metrics"] for c in (control, candidate))
                    require(set(a) == set(z), "Metric fields differ")
                    output.append({"period": p, "board": b, "exit": e, "id": cid, "comparisonFamily": family,
                        "control": control, "candidate": candidate, "controlMetrics": deepcopy(a), "candidateMetrics": deepcopy(z),
                        "candidateMinusControl": {k: z[k] - a[k] if finite(a[k]) and finite(z[k]) else None for k in sorted(a)},
                        "meaning": "Complete shared-capital accounts; changed admissions can change later holdings, cash and capacity. Not a sum of paired independent paths or a per-trade causal effect."})
    return output


def interval_rows(key, summary, result, original, schedule, selected):
    rows = ACCOUNT.interval_rows({**key, "candidate": key["model"]}, summary, result, original, schedule, selected)
    for row in rows:
        observed = [r for r in selected if row["requestedStart"] <= r["formationDate"] <= row["requestedEnd"]]
        row.update(key)
        row["executionOrigin"] = "new"
        row["switchSupportStateCounts"] = {s: sum(r["supportState"] == s for r in observed) for s in STATES}
        row["switchSelectedPopulationCounts"] = {p: sum(r["selectedPopulation"] == p for r in observed) for p in POPULATIONS}
    return rows


def baseline_membership(rows):
    """Read the already-audited parent dispositions, without label-path replay."""
    seen, entered, counts = set(), {}, Counter()
    for row in rows:
        k = identity(row)
        require(k[0] in PERIODS and k[1] in BOARDS and k[2] in EXITS and k[3] in INHERITED,
                "Unexpected inherited disposition identity")
        unique = (*k, row["formationId"])
        require(unique not in seen, "Duplicate inherited disposition")
        seen.add(unique); counts[k] += 1
        if row["candidate"] == "baseline":
            require(row["scheduled"] is True and row["positionState"] in ("closed", "openAtCutoff", "notEntered"),
                    "Parent baseline cannot be masked")
            entered.setdefault(k[:3], set())
            if row["positionState"] in ("closed", "openAtCutoff"):
                entered[k[:3]].add(row["formationId"])
    return entered, counts, len(seen)


def build(directory):
    directory = Path(directory).resolve()
    destinations = {n: directory / n for n in ("readout.json", "opportunity-dispositions.jsonl.gz", "candidate-entry-episodes.jsonl.gz")}
    require(all(not p.exists() for p in destinations.values()), "Readout outputs already exist; never overwrite")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        require(path.is_relative_to(ROOT), "Readout input outside repository")
        name, value = path.relative_to(ROOT).as_posix(), sha(path)
        require(pins.setdefault(name, value) == value, "Readout input changed")
        return path

    def bound(item):
        path = pin(ROOT / item["path"])
        require(sha(path) == item["sha256"], "Artifact digest mismatch: " + item["path"])
        return path

    def ref(path):
        return {"path": Path(path).resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}

    names = ("plan.json", "comparison.json", "input-completion.json", "input-validation.json", "validation.json")
    plan, comparison, completion, input_proof, proof = [read(pin(directory / n)) for n in names]
    plan_sha = sha(directory / "plan.json")
    require(plan["schema"] == "common-support-switch-v1"
        and comparison["planSha256"] == completion["planSha256"] == plan_sha, "Plan/comparison/completion identity")
    require(proof["passed"] is True and input_proof["passed"] is True
        and proof["planSha256"] == input_proof["planSha256"] == plan_sha
        and proof["comparisonSha256"] == sha(directory / "comparison.json")
        and proof["inputCompletionSha256"] == input_proof["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and proof["inputValidationSha256"] == sha(directory / "input-validation.json"), "Passing exact full audit chain required")
    verifier = "scripts/verify-common-support-switch.py"
    require(proof["verifierSha256"] == plan["sourcePins"][verifier] == sha(ROOT / verifier), "Independent verifier changed")
    require(proof["counts"]["accounts"] == 84 and proof["counts"]["newAccounts"] == 24
        and proof["counts"]["reusedAccounts"] == 60 and comparison["newAccounts"] == 24
        and comparison["referenceAccountsReused"] == 60, "Full account matrix/trial scope")
    require(completion["allSchedulesFrozenBeforeAccounts"] is True
        and plan["holdoutClaim"] is False and plan["parameterSearch"] is False, "Frozen scope required")
    for name, value in plan["sourcePins"].items():
        bound({"path": name, "sha256": value})
    pin(__file__); pin(HELPER); pin(ACCOUNT.HELPER)
    parent = read(bound(plan["parentReadout"]))
    parent_proof = read(bound(plan["parentReadoutValidation"]))
    parent_comparison = read(bound(plan["parentComparison"]))
    bound(plan["parentValidation"]); bound(plan["parentPlan"]); bound(plan["parentDelivery"])
    require(parent_proof["passed"] is True and parent_proof["readoutSha256"] == plan["parentReadout"]["sha256"]
        and parent_proof["comparisonSha256"] == plan["parentComparison"]["sha256"]
        and parent_proof["planSha256"] == plan["parentPlan"]["sha256"]
        and parent_proof["validationSha256"] == plan["parentValidation"]["sha256"], "Parent readout proof changed")
    require(comparison["referenceRows"] == plan["referenceRows"] == parent_comparison["rows"], "All 60 parent descriptors must remain exact")
    index = {identity(r): r for r in comparison["rows"]}
    expected = {(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in CANDIDATES}
    require(len(index) == len(comparison["rows"]) == 84 and set(index) == expected, "Complete account descriptors required")
    require(comparison["newRows"] == [r for r in comparison["rows"] if r["candidate"] in NEW]
        and comparison["referenceRows"] == [r for r in comparison["rows"] if r["candidate"] in INHERITED], "New/reused descriptor partitions differ")
    for descriptor in comparison["referenceRows"]:
        for name in ("result", "checkpoint", "summary", "schedule"):
            bound(descriptor[name])
    accounts, flat, tables = reuse_tables(parent, comparison["referenceRows"])
    old_opportunities = parent["opportunityRowsArtifact"]
    entered, parent_counts, parent_n = baseline_membership(records(bound(old_opportunities)))
    require(parent_n == old_opportunities["count"] == 81550, "Full inherited opportunity sidecar required")
    for field in ("candidateEntryRowsArtifact", "pairedPredictionRowsArtifact"):
        bound(parent[field])
    require(parent["counts"]["comparisons"] == len(parent["comparisons"]) == 96
        and parent["counts"]["predictionEvaluationRows"] == len(parent["predictionEvaluationRows"]) == 192
        and parent["pairedPredictionRowsArtifact"]["count"] == 65240, "Inherited comparison/prediction scope differs")
    atlas = read(bound(plan["atlas"]))
    phases = {r["id"]: r for r in atlas["phases"]}
    require(len(phases) == len(atlas["phases"]) == 16, "All original atlas phases required")
    groups = {(r["period"], r["board"], r["exit"]): r for r in completion["groups"]}
    expected_groups = {(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS}
    require(len(groups) == len(completion["groups"]) == 12 and set(groups) == expected_groups, "Twelve source/exit groups required")
    dispositions, entries_all, shifts, switch_rows, decision_refs, intervals = ([] for _ in range(6))
    original_counts = {}
    for period in PERIODS:
        for board in BOARDS:
            original = read(bound(plan["baselineSchedules"][period][board]))
            metadata = read(bound(plan["metadata"][period][board]))
            dates = metadata["targetDates"]
            original_counts[(period, board)] = len(original)
            for exit_policy in EXITS:
                key = {"period": period, "board": board, "exit": exit_policy}
                group = groups[(period, board, exit_policy)]
                require(group["baselineSchedule"] == plan["baselineSchedules"][period][board]
                    and group["decisions"] == plan["decisionArtifacts"][period][board][exit_policy]
                    and group["inheritedRows"] == [index[(period, board, exit_policy, c)] for c in INHERITED], "Prepared references differ")
                for c in INHERITED:
                    require(parent_counts[(period, board, exit_policy, c)] == len(original), "Inherited opportunity denominator differs")
                decisions = {p: list(records(bound(group["decisions"][p]))) for p in POPULATIONS}
                for population in POPULATIONS:
                    decision_refs.append({**key, "population": population, "artifact": deepcopy(group["decisions"][population]),
                        "targetDayCount": len(decisions[population]), "scope": "Byte-reused frozen decisions; no new forecasts or fitting."})
                for model in MODELS:
                    candidate = "commonSupport__" + model
                    account_key = {**key, **candidate_identity(candidate)}
                    descriptor = index[(period, board, exit_policy, candidate)]
                    prepared = group["candidates"][candidate]
                    require(descriptor["population"] == prepared["population"] == "commonSupport"
                        and descriptor["model"] == prepared["model"] == model and descriptor["reusedAccount"] is False
                        and descriptor["schedule"] == prepared["schedule"], "New account identity/mask differs")
                    schedule = read(bound(descriptor["schedule"]))
                    switch, selected = switch_coverage(key, model, original, decisions, schedule, dates, prepared["counts"])
                    switch_rows.append(switch)
                    result = read(bound(descriptor["result"]))
                    bound(descriptor["checkpoint"])
                    summary = read(bound(descriptor["summary"]))
                    require(summary["inputResultSha256"] == digest(result) and summary["policyId"] == candidate
                        and result["status"] == "completed" and not summary["quality"]["integrityIssues"], "Summary/account identity mismatch")
                    require([r["date"] for r in result["curve"]] == dates, "Incomplete source account timeline")
                    detail, entries = ACCOUNT.attach_episodes(account_key, original, schedule, result, summary,
                        entered[(period, board, exit_policy)])
                    dispositions.extend(detail); entries_all.extend(entries)
                    shifts.append(ACCOUNT.sample_shift(account_key, entries, summary))
                    scalar = ACCOUNT.metrics(summary, len(schedule), len(original))
                    provenance = {"executionOrigin": "new", "reusedAccount": False, "sourceReusedAccount": False}
                    flat.append({**account_key, **provenance, "metrics": scalar})
                    accounts.append({**account_key, **provenance, "artifacts": deepcopy(descriptor), "metrics": scalar,
                        **{n: deepcopy(summary[n]) for n in ("account", "closedEpisodes", "concentration", "openPositions", "rights", "unresolvedExits", "reconciliation", "quality", "warnings")},
                        "sourceQualityScalarFlags": {n: v for n, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                        "fullSourceQuality": {"summaryRef": deepcopy(descriptor["summary"]), "field": "sourceQuality"}})
                    states = Counter(r["positionState"] for r in detail)
                    tables["coverageRows"].append({**account_key, "executionOrigin": "new", "originalOpportunities": len(original),
                        "scheduledSignals": len(schedule), "notScheduled": states["notScheduled"], "entered": len(entries),
                        "closed": states["closed"], "openAtCutoff": states["openAtCutoff"], "notEntered": states["notEntered"],
                        "accountOpportunityReasons": MATH.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
                        "valuationUnreliableDays": scalar["unreliableValuationDays"]})
                    measured = interval_rows(account_key, summary, result, original, schedule, selected)
                    for row in measured:
                        if row["kind"] == "retrospectiveNarrativePhase":
                            phase = phases[row["id"]]
                            require((row["requestedStart"], row["requestedEnd"]) == (phase["startDate"], phase["endDate"]), "Atlas dates changed")
                            row["phaseTitle"] = phase["title"]
                    intervals.extend(measured)
                    tables["partitionReconciliation"].append({**account_key, "executionOrigin": "new", "passed": True,
                        "checks": ACCOUNT.partition_intervals(measured, summary, result["curve"])})
    require(sum(original_counts.values()) == 8155 and len(dispositions) == 32620, "All original opportunities required")
    for name, kind in (("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
                       ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch")):
        tables[name].extend(r for r in intervals if r["kind"] == kind)
    expected_counts = {"yearRows": 168, "halfYearRows": 336, "phaseRows": 672, "curveEpochRows": 84,
                       "coverageRows": 84, "partitionReconciliation": 84}
    require(all(len(tables[n]) == value for n, value in expected_counts.items()), "Full 84-account time/coverage matrices")
    require(len(switch_rows) == len(shifts) == len(decision_refs) == 24, "Full switch coverage/decision matrices")
    order = {k: i for i, k in enumerate(index)}
    accounts.sort(key=lambda r: order[identity(r)]); flat.sort(key=lambda r: order[identity(r)])
    for rows in tables.values():
        rows.sort(key=lambda r: (order[identity(r)], r.get("requestedStart", ""), r.get("id", "")))
    contrasts = compare_accounts(flat)
    require(all(sha(ROOT / n) == value for n, value in pins.items()), "Inputs changed during readout")

    def write_lines(name, values):
        path = destinations[name]
        with gzip.open(path, "xt", encoding="utf-8", compresslevel=1) as stream:
            for row in values:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        return {**ref(path), "count": len(values), "format": "jsonl+gzip",
                "identity": ["period", "board", "exit", "candidate", "formationId"]}

    opportunity_ref = write_lines("opportunity-dispositions.jsonl.gz", dispositions)
    entry_ref = write_lines("candidate-entry-episodes.jsonl.gz", entries_all)
    inherited = {"readout": deepcopy(plan["parentReadout"]), "validation": deepcopy(plan["parentReadoutValidation"]),
        "comparisons": {"field": "comparisons", "count": 96},
        "predictionEvaluationRows": {"field": "predictionEvaluationRows", "count": 192},
        "pairedPredictionRowsArtifact": deepcopy(parent["pairedPredictionRowsArtifact"]),
        "sampleShiftRows": {"field": "sampleShiftRows", "count": 48},
        "feedbackCoverageRows": {"field": "feedbackCoverageRows", "count": 48}}
    report = {"schema": "common-support-switch-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "comparisonSha256": sha(directory / "comparison.json"),
        "verification": {"passed": True, **ref(directory / "validation.json")}, "inputPins": pins,
        "accounts": accounts, "accountRows": flat, "comparisons": contrasts, "primaryComparisons": contrasts,
        "populationComparisons": [r for r in contrasts if r["comparisonFamily"] == "commonSupportChange"],
        "coverageComparisons": [r for r in contrasts if r["comparisonFamily"] == "extraCoverageRemoved"], **tables,
        "switchCoverageRows": switch_rows, "sampleShiftRows": shifts, "feedbackDecisionArtifacts": decision_refs,
        "inheritedEvidence": inherited, "opportunityRowsArtifact": opportunity_ref, "candidateEntryRowsArtifact": entry_ref,
        "inheritedOpportunityRowsArtifact": deepcopy(parent["opportunityRowsArtifact"]),
        "inheritedCandidateEntryRowsArtifact": deepcopy(parent["candidateEntryRowsArtifact"]),
        "counts": {"accounts": 84, "newAccounts": 24, "reusedAccounts": 60, "comparisons": 48,
            "primaryComparisons": 48, "populationComparisons": 24, "coverageComparisons": 24,
            **expected_counts, "switchCoverageRows": 24, "sampleShiftRows": 24,
            "originalOpportunities": 8155, "sourceExitOpportunities": 16310,
            "policyDispositions": parent_n + len(dispositions), "newPolicyDispositions": len(dispositions),
            "inheritedPolicyDispositions": parent_n, "actualCandidateEntryEpisodes": len(entries_all),
            "inheritedCandidateEntryEpisodes": parent["candidateEntryRowsArtifact"]["count"],
            "inheritedComparisons": 96, "inheritedPredictionEvaluationRows": 192,
            "inheritedPairedPredictionOpportunities": 65240, "newPredictionEvaluationRows": 0,
            "frozenDecisionLibraries": 24, "frozenDecisionDays": sum(r["targetDayCount"] for r in decision_refs)},
        "method": {"accounts": "84 complete cash/position accounts: 60 byte-reused parent references and 24 new common-support policies. Historical reusedAccount flags in descriptors are preserved; executionOrigin states current-batch reuse. Performance uses continuous equity, not closed-only PnL.",
            "switch": "Within each model and T: both originalEntered and allExecutable fitted => use frozen allExecutable allow; otherwise use frozen originalEntered allow, including its retain-all fallback. Original Top5, identity, order, exits and costs remain fixed; no new fit or independent path execution.",
            "contrasts": "48 new signed account contrasts: commonSupport minus originalEntered and commonSupport minus allExecutable within each model and source. Prior 96 contrasts and 192 paired evaluations are referenced without recomputation.",
            "coverage": "Four support states cover every target T and original opportunity separately. common-minus-all changes only outside bothFitted, including allOnlyFitted and potentially originalOnlyFitted; it is not guaranteed to only add admissions.",
            "opportunities": "Only 32,620 new candidate dispositions are written. The 81,550 inherited dispositions remain an exact referenced sidecar. Not scheduled, scheduled but not entered, closed and open remain distinct; unentered opportunities are not zero-return trades.",
            "intervals": "Years, halves, all 16 narrative phases and 9.24 descriptive cuts retain continuous account equity and cross-boundary positions. Episode PnL follows final exit date; admissions and support counts use formation T, with evidence known by T-1.",
            "sampleShift": "New actual entries are compared with the unchanged original baseline membership. Candidate-only entries and open realized/unrealized PnL are retained; these are not a per-trade counterfactual return decomposition.",
            "quality": "Bound source-quality details, stale open marks, fees, unrealized PnL, exposure and profit concentration remain visible. Arithmetic reconciliation does not certify prices or realizability.",
            "dependence": "Same-day opportunities, repeated stocks, overlapping holdings and shared capital are dependent. Differences between account policies are not isolated causal effects of individual admission changes.",
            "scope": "2022-2025 repeatedly observed development data; 2026 exposed but unused. No holdout, profitability hurdle, stability or strategy promotion claim."},
        "newAccounts": 24, "referenceAccountsReused": 60, "signalsGenerated": False,
        "summarizerAccountExecutions": 0, "summarizerModelFits": 0, "summarizerPathExecutions": 0,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    with destinations["readout.json"].open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "readout": ref(destinations["readout.json"]), "counts": report["counts"]}


def self_test():
    import io
    import unittest
    test_file = ROOT / "tests/test_common_support_switch_readout.py"
    test_spec = importlib.util.spec_from_file_location("common_support_readout_synthetic", test_file)
    test_module = importlib.util.module_from_spec(test_spec)
    test_spec.loader.exec_module(test_module)
    log = io.StringIO()
    outcome = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_module))
    require(outcome.wasSuccessful(), log.getvalue())
    return {"passed": True, "syntheticTests": outcome.testsRun, "realAccountReads": 0,
            "modelFits": 0, "accountRuns": 0, "filesWritten": 0, "details": log.getvalue()}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False, allow_nan=False))

