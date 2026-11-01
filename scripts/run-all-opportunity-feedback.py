"""Replay fixed audited feedback masks in complete shared-capital accounts.

This runner never imports a feedback fitter or an independent-path executor.
It preserves original signal identities/ranks and all execution rules.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "all-opportunity-feedback-v1"
LIBRARY = BASE / "independent-shadow-labels-v1"
PROTOCOL = ROOT / "docs/all-opportunity-feedback-v1.json"
PROGRAM_SHA = "007ca53d96e2f0918dc7b4d527443b1e045ff71ae9acdfd572d2a347ba3d019f"
PREVIOUS_DELIVERY_SHA = "e2549ec42e556ca6266453d012b7ec250e22c26fbdf3902802e5b3acad101c62"
PERIODS, BOARDS = ("2022-2023", "2024-2025"), ("main", "chinext", "star")
EXITS, POPULATIONS, MODELS = ("trail10", "volatilityTrail"), ("originalEntered", "allExecutable"), ("intercept", "ridge")
CANDIDATES = tuple(p + "__" + m for p in POPULATIONS for m in MODELS)
SID = "strongUndercutReclaimV1"
VERIFIER = "scripts/verify-all-opportunity-feedback.py"
SUMMARY = "scripts/summarize-all-opportunity-feedback.py"
SIGNAL_FIELDS = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}


def now(): return datetime.now(timezone.utc).isoformat()
def require(ok, message):
    if not ok: raise ValueError(message)
def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
def ref(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}
def bound(item):
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed input: " + item["path"])
    return path
def read(path):
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)
def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]
def write(path, value):
    path = Path(path)
    with (gzip.open if str(path).endswith(".gz") else open)(path, "xt", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=None if str(path).endswith(".gz") else 2)
        stream.write("\n")
def atomic_once(path, value):
    """Publish a complete immutable small receipt; concurrent readers see no prefix."""
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    write(temporary, value)
    try:
        try:
            os.link(temporary, path)
            return True
        except FileExistsError:
            return False
    finally:
        temporary.unlink()
def emit(value): print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
def module(name):
    spec = importlib.util.spec_from_file_location("_all_feedback_" + name.replace("-", "_").replace(".", "_"), ROOT / "scripts" / name)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result
def dimensions(candidate):
    require(candidate in CANDIDATES, "Unknown fixed candidate")
    return tuple(candidate.split("__"))
def group_key(group): return tuple(group[k] for k in ("period", "board", "exit"))


def verify_plan():
    plan = read(OUT / "plan.json")
    for field in ("sourcePins", "inputPins"):
        for name, value in plan[field].items():
            bound({"path": name, "sha256": value})
    require(plan["candidates"] == list(CANDIDATES) and plan["newAccounts"] == 48
            and plan["referenceAccountsReused"] == 12, "Fixed account matrix changed")
    return plan


def register():
    require(not OUT.exists(), "Batch already registered; never replace it")
    require(sha(BASE / "program.json") == PROGRAM_SHA and sha(LIBRARY / "delivery.json") == PREVIOUS_DELIVERY_SHA,
            "Research registry or completed predecessor changed")
    program = read(BASE / "program.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 39
            and program["goalStatus"] == "active" and program["targetAchieved"] is False, "Unexpected research state")
    delivery = read(LIBRARY / "delivery.json")
    for item in delivery["files"]:
        bound(item)
    previous = read(LIBRARY / "plan.json")
    parent_path = bound(previous["parentPlan"]); parent = read(parent_path)
    report_path = LIBRARY / "readout.json"; report = read(report_path)
    validation, report_validation = (read(LIBRARY / name) for name in ("validation.json", "readout-validation.json"))
    completion_path = LIBRARY / "library-completion.json"; completion = read(completion_path)
    require(validation["passed"] is True and validation["planSha256"] == sha(LIBRARY / "plan.json")
            and validation["libraryCompletionSha256"] == sha(completion_path), "Predecessor paths not audited")
    require(report_validation["passed"] is True and report_validation["readoutSha256"] == sha(report_path)
            and report_validation["planSha256"] == sha(LIBRARY / "plan.json")
            and report_validation["pathValidationSha256"] == sha(LIBRARY / "validation.json")
            and report_validation["libraryCompletionSha256"] == sha(completion_path), "Predecessor decisions not audited")
    require(report["counts"]["decisionLibraries"] == 24 and report["counts"]["originalFormations"] == 8155,
            "Predecessor opportunity population differs")
    protocol = read(PROTOCOL)
    require(protocol["candidateIds"] == list(CANDIDATES) and protocol["populations"] == list(POPULATIONS)
            and protocol["models"] == list(MODELS) and protocol["trialAccounting"]["newAccounts"] == 48
            and protocol["trialAccounting"]["referenceAccountsReused"] == 12, "Protocol matrix differs")
    sources = dict(parent["sourcePins"])
    added = ["docs/all-opportunity-feedback-v1.json", "scripts/run-all-opportunity-feedback.py",
        "tests/test_all_opportunity_feedback_runner.py", VERIFIER, "tests/test_all_opportunity_feedback_verifier.py",
        SUMMARY, "tests/test_all_opportunity_feedback_readout.py", "scripts/run-online-feedback-entry.py",
        "scripts/summarize-online-feedback-entry.py", "scripts/summarize-undercut-reclaim-timing.py",
        "service/research_fixed_signal_account.py", "service/research_volatility_trail.py",
        "service/research_account_outcomes.py", "service/research_account_fees.py"]
    sources.update({name: sha(ROOT / name) for name in added})
    for name, expected in sources.items():
        require(sha(ROOT / name) == expected, "Inherited execution source changed: " + name)
    library_refs = {"libraryPlan": ref(LIBRARY / "plan.json"), "libraryDelivery": ref(LIBRARY / "delivery.json"),
        "libraryReadout": ref(report_path), "libraryReadoutValidation": ref(LIBRARY / "readout-validation.json"),
        "libraryValidation": ref(LIBRARY / "validation.json"), "libraryCompletion": ref(completion_path)}
    inputs = {item["path"]: item["sha256"] for item in library_refs.values()}
    def pin(item):
        bound(item); inputs[item["path"]] = item["sha256"]; return deepcopy(item)
    pin(previous["parentPlan"]); pin(previous["parentInputCompletion"]); pin(previous["parentInputValidation"])
    pin(parent["atlas"]); pin(parent["opportunities"])
    decision_groups = {group_key(g): g for g in report["decisionArtifacts"]}
    path_groups = {group_key(g): g for g in completion["groups"]}
    expected_groups = {(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS}
    require(len(report["decisionArtifacts"]) == len(completion["groups"]) == 12
            and set(decision_groups) == set(path_groups) == expected_groups, "Complete predecessor matrix required")
    decision_refs, path_refs = {}, {}
    for period in PERIODS:
        decision_refs[period], path_refs[period] = {}, {}
        for board in BOARDS:
            decision_refs[period][board], path_refs[period][board] = {}, {}
            for field in ("sourceData", "baselineSchedules", "metadata"):
                require(previous[field][period][board] == parent[field][period][board], "Inherited source binding differs")
            pin(parent["sourceData"][period][board]["accountData"])
            pin(parent["baselineSchedules"][period][board]); pin(parent["metadata"][period][board])
            for exit_name in EXITS:
                key = period, board, exit_name
                samples = decision_groups[key]["samples"]
                require(set(samples) == set(POPULATIONS), "Unexpected predecessor label population")
                decision_refs[period][board][exit_name] = {sample: pin(samples[sample]) for sample in POPULATIONS}
                path_refs[period][board][exit_name] = pin(path_groups[key]["paths"])
                for item in parent["accountArtifacts"][period][board][exit_name].values(): pin(item)
    plan = {"schema": OUT.name, "registeredAt": now(), "previousProgram": ref(BASE / "program.json"),
        "previousDelivery": library_refs["libraryDelivery"], "parentPlan": previous["parentPlan"],
        "parentInputCompletion": previous["parentInputCompletion"], "parentInputValidation": previous["parentInputValidation"],
        **library_refs, "protocol": ref(PROTOCOL), "rules": protocol, "sourcePins": sources, "inputPins": inputs,
        "periods": deepcopy(parent["periods"]), "boards": list(BOARDS), "exits": list(EXITS),
        "populations": list(POPULATIONS), "models": list(MODELS), "candidates": list(CANDIDATES),
        "sourceData": deepcopy(parent["sourceData"]), "baselineSchedules": deepcopy(parent["baselineSchedules"]),
        "accountArtifacts": deepcopy(parent["accountArtifacts"]), "metadata": deepcopy(parent["metadata"]),
        "decisionArtifacts": decision_refs, "libraryPaths": path_refs, "opportunities": parent["opportunities"],
        "atlas": parent["atlas"], **protocol["trialAccounting"],
        "candidateTrialCountBefore": 39, "parameterSearch": False, "holdoutClaim": False, "certified": False, "targetAchieved": False}
    OUT.mkdir(exist_ok=False)
    write(OUT / "plan.json", plan)
    with (OUT / "program-before.json").open("xb") as stream: stream.write((BASE / "program.json").read_bytes())
    for name in sources:
        target = OUT / "sources" / name; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: stream.write((ROOT / name).read_bytes())
    return {"registered": True, "plan": ref(OUT / "plan.json"), "candidateTrialCountUnchanged": 39}


def pretests():
    plan = verify_plan(); started = now()
    require(not (OUT / "run-start.json").exists(), "Pretests must precede all new accounts")
    commands = [[sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_all_opportunity_feedback_runner", "-v"],
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_fixed_signal_account",
         "tests.test_research_account_fees", "tests.test_research_account_outcomes", "tests.test_research_volatility_trail", "-v"],
        [sys.executable, "-X", "utf8", "-B", VERIFIER, "--self-test"],
        [sys.executable, "-X", "utf8", "-B", SUMMARY, "--self-test"]]
    results = []
    for argv in commands:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        results.append({"argv": argv, "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, "Pre-account check failed: " + result.stdout + result.stderr)
    counts = [re.search(r"Ran (\d+) tests?", item["stderr"]) for item in results[:2]]
    require(all(counts), "Unit test counts missing")
    verify_plan()
    receipt = {"schema": OUT.name + "-pre-account-tests", "passed": True, "startedAt": started, "completedAt": now(),
        "planSha256": sha(OUT / "plan.json"), "sourcePins": plan["sourcePins"], "commands": results,
        "newUnitTests": int(counts[0].group(1)), "inheritedUnitTests": int(counts[1].group(1)),
        "allChecksBeforeFirstNewAccount": True}
    write(OUT / "pre-account-tests.json", receipt)
    return {"passed": True, "newUnitTests": receipt["newUnitTests"], "inheritedUnitTests": receipt["inheritedUnitTests"]}


def select_schedule(schedule, decisions, model, *, calendar, target_dates, key, population):
    """Consume the frozen Boolean only; never retrain, reorder or backfill."""
    require(model in MODELS and population in POPULATIONS, "Unknown fixed mask")
    require(calendar == sorted(set(calendar)) and target_dates == sorted(set(target_dates))
            and set(target_dates) <= set(calendar), "Strict source/target calendar required")
    indexes = {day: i for i, day in enumerate(calendar)}
    require([r["formationDate"] for r in decisions] == target_dates, "All target-date decisions required in original order")
    by_day = {}
    for row in decisions:
        day = row["formationDate"]; index = indexes[day]
        require(row["asOfDate"] == (calendar[index - 1] if index else None), "Feedback must use exactly T-1")
        require(all(row[k] == key[k] for k in ("period", "board", "exit")) and row["sample"] == population,
                "Frozen decision population/source differs")
        for mode in MODELS:
            item = row["models"][mode]
            require(type(item["allow"]) is bool, "Explicit Boolean model allow required")
            if item["status"] == "fallback":
                require(item["prediction"] is None and item["allow"] is True
                        and item["reason"] in ("insufficientTrainingFormationDays", "currentFeaturesUnknown"), "Fallback must retain signals")
            else:
                value = item["prediction"]
                require(item["status"] == "fitted" and type(value) in (int, float) and math.isfinite(value)
                        and item["allow"] == (value > 0)
                        and item["reason"] == ("positivePrediction" if value > 0 else "nonPositivePrediction"),
                        "Frozen fitted decision inconsistent")
        by_day[day] = row
    seen, day_symbols, ranks, sort_keys = set(), set(), set(), []
    for signal in schedule:
        require(set(signal) == SIGNAL_FIELDS and signal["strategyId"] == SID, "Original five-field signal required")
        sid, day, symbol, rank = (signal[k] for k in ("signalId", "signalDate", "symbol", "originalRank"))
        require(isinstance(sid, str) and sid and sid not in seen and isinstance(symbol, str) and symbol
                and day in by_day and (day, symbol) not in day_symbols
                and type(rank) is int and 1 <= rank <= 5 and (day, rank) not in ranks, "Invalid or duplicate original signal")
        seen.add(sid); day_symbols.add((day, symbol)); ranks.add((day, rank)); sort_keys.append((day, rank, symbol))
    require(sort_keys == sorted(sort_keys), "Do not repair original signal ordering")
    return [deepcopy(s) for s in schedule if by_day[s["signalDate"]]["models"][model]["allow"]]


def mask_counts(schedule, decisions, model):
    by_day = {r["formationDate"]: r["models"][model] for r in decisions}
    picked = [by_day[s["signalDate"]] for s in schedule]
    return {"originalSignals": len(schedule), "scheduledSignals": sum(r["allow"] for r in picked),
        "rejectedSignals": sum(not r["allow"] for r in picked), "decisionDays": len(decisions),
        "fittedDays": sum(r["status"] == "fitted" for r in by_day.values()),
        "fallbackDays": sum(r["status"] == "fallback" for r in by_day.values()),
        "fittedAdmittedSignals": sum(r["status"] == "fitted" and r["allow"] for r in picked),
        "fittedRejectedSignals": sum(r["status"] == "fitted" and not r["allow"] for r in picked),
        "fallbackAdmittedSignals": sum(r["status"] == "fallback" and r["allow"] for r in picked)}


def prepare():
    plan = verify_plan(); tests = read(OUT / "pre-account-tests.json")
    require(tests["passed"] is True and tests["planSha256"] == sha(OUT / "plan.json")
            and tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Exact pre-account checks required")
    require(not (OUT / "run-start.json").exists(), "All masks must precede all accounts")
    original = records(bound(plan["opportunities"]))
    require(len(original) == 8155, "Original opportunity denominator differs")
    groups = []
    for period in PERIODS:
        for board in BOARDS:
            schedule = read(bound(plan["baselineSchedules"][period][board]))
            source_rows = [r for r in original if r["period"] == period and r["board"] == board]
            require(schedule == [r["signals"]["immediate"] for r in source_rows], "Frozen formation/schedule join differs")
            md = read(bound(plan["metadata"][period][board]))
            for exit_name in EXITS:
                key = {"period": period, "board": board, "exit": exit_name}
                folder = OUT / period / board / exit_name; folder.mkdir(parents=True, exist_ok=False)
                candidates = {}
                for population in POPULATIONS:
                    daily = records(bound(plan["decisionArtifacts"][period][board][exit_name][population]))
                    for model in MODELS:
                        candidate = population + "__" + model
                        selected = select_schedule(schedule, daily, model, calendar=md["calendar"], target_dates=md["targetDates"], key=key, population=population)
                        path = folder / (candidate + ".signals.json"); write(path, selected)
                        candidates[candidate] = {"population": population, "model": model, "schedule": ref(path),
                                                 "counts": mask_counts(schedule, daily, model)}
                groups.append({**key, "baseline": plan["accountArtifacts"][period][board][exit_name],
                    "baselineSchedule": plan["baselineSchedules"][period][board],
                    "accountData": plan["sourceData"][period][board]["accountData"],
                    "libraryPaths": plan["libraryPaths"][period][board][exit_name],
                    "decisions": plan["decisionArtifacts"][period][board][exit_name], "candidates": candidates})
                emit({"prepared": [period, board, exit_name], "signals": {k: v["counts"]["scheduledSignals"] for k, v in candidates.items()}})
    verify_plan()
    completion = {"schema": OUT.name + "-inputs", "planSha256": sha(OUT / "plan.json"),
        "preAccountTestsSha256": sha(OUT / "pre-account-tests.json"), "completedAt": now(), "groups": groups,
        "allSchedulesFrozenBeforeAccounts": True, "newAccountsExecuted": 0, "pathsExecuted": 0, "modelsRefitted": 0,
        "counts": {"preparedGroups": 12, "originalSchedules": 6, "preparedSchedules": 48,
                   "sourceImmediateOpportunities": len(original), "sourceExitOpportunities": len(original) * 2}}
    write(OUT / "input-completion.json", completion)
    return {"prepared": True, "groups": 12, "schedules": 48, "completion": ref(OUT / "input-completion.json")}


def validate_prerequisites(plan, completion, validation, tests, hashes):
    require(tests["passed"] is True and tests["planSha256"] == hashes["plan"]
            and tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Pre-account checks missing or changed")
    require(completion["planSha256"] == hashes["plan"] and completion["preAccountTestsSha256"] == hashes["tests"]
            and completion["allSchedulesFrozenBeforeAccounts"] is True and completion["newAccountsExecuted"] == 0
            and completion["modelsRefitted"] == 0 and completion["pathsExecuted"] == 0, "Input freeze differs")
    require(validation["passed"] is True and validation["planSha256"] == hashes["plan"]
            and validation["inputCompletionSha256"] == hashes["completion"]
            and validation["preAccountTestsSha256"] == hashes["tests"]
            and validation["verifierSha256"] == hashes["verifier"], "Independent input audit missing or changed")
    expected = {(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS}
    require(len(completion["groups"]) == 12 and {group_key(g) for g in completion["groups"]} == expected,
            "All twelve groups must freeze before any account")
    for group in completion["groups"]:
        p, b, e = group_key(group)
        require(group["baseline"] == plan["accountArtifacts"][p][b][e]
                and group["baselineSchedule"] == plan["baselineSchedules"][p][b]
                and group["accountData"] == plan["sourceData"][p][b]["accountData"]
                and group["decisions"] == plan["decisionArtifacts"][p][b][e]
                and group["libraryPaths"] == plan["libraryPaths"][p][b][e], "Prepared source binding differs")
        require(set(group["candidates"]) == set(CANDIDATES), "All four masks required per source/exit")
        for candidate, item in group["candidates"].items():
            require((item["population"], item["model"]) == dimensions(candidate), "Prepared candidate dimensions differ")
    require(completion["counts"]["preparedSchedules"] == 48, "All 48 schedules must be independently audited")
    require(datetime.fromisoformat(tests["completedAt"]) <= datetime.fromisoformat(completion["completedAt"]),
            "Pretests must finish before schedule freeze")


def intervals(period, atlas):
    return module("run-online-feedback-entry.py").intervals(period, atlas)


def execute_account(data, schedule, config, exit_name, fee):
    from service.research_fixed_signal_account import FixedSignalAccount
    from service.research_volatility_trail import VolatilityTrailAccount
    require(exit_name in EXITS, "Unknown fixed exit")
    cls = FixedSignalAccount if exit_name == "trail10" else VolatilityTrailAccount
    engine = cls(data, schedule, deepcopy(config), entry_weight=.2, fee_policy=fee)
    engine.step(20000)
    result, checkpoint = engine.result(), engine.checkpoint()
    require(result["status"] == "completed" and result["research"]["identity"]["signalSchedule"] == schedule,
            "Account incomplete or original signal binding lost")
    return result, checkpoint


def selected_groups(groups, requested):
    if requested is None: return list(groups)
    require(requested in {p + "/" + b for p in PERIODS for b in BOARDS}, "--group must be PERIOD/BOARD")
    p, b = requested.split("/")
    result = [g for g in groups if g["period"] == p and g["board"] == b]
    require(len(result) == 2 and {g["exit"] for g in result} == set(EXITS), "Both exits required for the selected board")
    return result


def require_unstarted_group(folder):
    folder = Path(folder)
    require(not (folder / "group-run-start.json").exists()
            and not any(folder.glob("*.result.json.gz")) and not any(folder.glob("*.checkpoint.json.gz"))
            and not any(folder.glob("*.summary.json")),
            "Nonterminal group has prior output; retain it for explicit inspection before any replay")


def checked_terminal(receipt, group, hashes, start_sha):
    require(receipt["completed"] is True and receipt["planSha256"] == hashes["plan"]
            and receipt["inputCompletionSha256"] == hashes["completion"]
            and receipt["inputValidationSha256"] == hashes["validation"]
            and receipt["preAccountTestsSha256"] == hashes["tests"]
            and receipt["runStartSha256"] == start_sha and group_key(receipt) == group_key(group), "Completed group binding differs")
    rows = receipt["rows"]
    require([r["candidate"] for r in rows] == ["baseline", *CANDIDATES], "Completed group account matrix differs")
    for row in rows:
        require(group_key(row) == group_key(group), "Completed account source differs")
        candidate = row["candidate"]
        population, model = (None, None) if candidate == "baseline" else dimensions(candidate)
        require((row["population"], row["model"]) == (population, model)
                and row["reusedAccount"] == (candidate == "baseline"), "Completed account dimensions differ")
        expected_schedule = group["baselineSchedule"] if candidate == "baseline" else group["candidates"][candidate]["schedule"]
        require(row["schedule"] == expected_schedule, "Completed schedule differs")
        if candidate == "baseline":
            require(row["result"] == group["baseline"][group["exit"] + ".result.json.gz"]
                    and row["checkpoint"] == group["baseline"][group["exit"] + ".checkpoint.json.gz"], "Baseline bytes must be reused")
        for field in ("result", "checkpoint", "summary", "schedule"): bound(row[field])
    return rows


def collect_comparison(plan, completion, hashes, start_sha):
    rows, missing, receipts = [], [], []
    for group in completion["groups"]:
        folder = OUT.joinpath(*group_key(group)); path = folder / "group-completion.json"
        if not path.exists(): missing.append("/".join(group_key(group))); continue
        rows.extend(checked_terminal(read(path), group, hashes, start_sha)); receipts.append(ref(path))
    if missing: return {"completed": False, "pendingGroups": missing, "terminalGroups": len(receipts)}
    require(len(rows) == 60 and sum(not r["reusedAccount"] for r in rows) == 48, "Complete sixty-account matrix required")
    value = {"schema": OUT.name + "-comparison", "completedAt": now(), "planSha256": hashes["plan"],
        "inputCompletionSha256": hashes["completion"], "inputValidationSha256": hashes["validation"],
        "preAccountTestsSha256": hashes["tests"], "runStartSha256": start_sha, "rows": rows, "groupCompletions": receipts,
        **plan["rules"]["trialAccounting"], "holdoutClaim": False, "certified": False, "targetAchieved": False}
    path = OUT / "comparison.json"
    if not atomic_once(path, value):
        saved = read(path)
        require({k: v for k, v in saved.items() if k != "completedAt"} == {k: v for k, v in value.items() if k != "completedAt"},
                "Existing comparison differs; never overwrite")
    return {"completed": True, "accounts": 60, "newAccounts": 48, "comparison": ref(path)}


def run(only_group=None):
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan(); completion = read(OUT / "input-completion.json")
    validation = read(OUT / "input-validation.json"); tests = read(OUT / "pre-account-tests.json")
    hashes = {k: sha(OUT / name) for k, name in (("plan", "plan.json"), ("completion", "input-completion.json"),
        ("validation", "input-validation.json"), ("tests", "pre-account-tests.json"))}
    hashes["verifier"] = sha(ROOT / VERIFIER)
    validate_prerequisites(plan, completion, validation, tests, hashes)
    # Validate every generated mask, even for a single board worker.
    for group in completion["groups"]:
        for item in group["candidates"].values(): bound(item["schedule"])
    selected = selected_groups(completion["groups"], only_group)
    start_refs = {"planSha256": hashes["plan"], "inputCompletion": ref(OUT / "input-completion.json"),
        "inputValidation": ref(OUT / "input-validation.json"), "tests": ref(OUT / "pre-account-tests.json")}
    start_path = OUT / "run-start.json"
    atomic_once(start_path, {"startedAt": now(), **start_refs})
    start = read(start_path)
    require(all(start[k] == v for k, v in start_refs.items()), "Existing run-start dependency changed")
    require(datetime.fromisoformat(completion["completedAt"]) <= datetime.fromisoformat(start["startedAt"]), "Schedules must freeze before any account")
    start_sha = sha(start_path)
    if only_group is None:
        # Board workers never publish the global comparison. The orchestrator
        # explicitly collects after they finish; collecting cannot start a missing account.
        return collect_comparison(plan, completion, hashes, start_sha)
    atlas = read(bound(plan["atlas"]))
    data, cached_key = None, None
    for group in selected:
        period, board, exit_name = group_key(group); folder = OUT / period / board / exit_name
        terminal = folder / "group-completion.json"
        if terminal.exists():
            checked_terminal(read(terminal), group, hashes, start_sha)
            emit({"skippedCompletedGroup": [period, board, exit_name]}); continue
        # A nonterminal previous attempt is retained for inspection, never silently replayed.
        require_unstarted_group(folder)
        write(folder / "group-run-start.json", {"startedAt": now(), "planSha256": hashes["plan"], "runStartSha256": start_sha})
        key = period, board
        if cached_key != key:
            if data is not None: del data; gc.collect()
            data = read(bound(group["accountData"])); cached_key = key
            require(digest(data) == plan["sourceData"][period][board]["accountDatasetDigest"], "Source dataset digest differs")
        if period == "2022-2023":
            helper = module("load-market-context-screen.py")._module("run-direction-account-screen.py").load_helper()
            fee = helper.fee_policy(helper.load_binding()["document"])
        else:
            fee = LegacyFeePolicy()
        rows = []
        for candidate in ("baseline", *CANDIDATES):
            population, model = (None, None) if candidate == "baseline" else dimensions(candidate)
            schedule_ref = group["baselineSchedule"] if candidate == "baseline" else group["candidates"][candidate]["schedule"]
            schedule = read(bound(schedule_ref))
            if candidate == "baseline":
                result_ref, checkpoint_ref = (group["baseline"][exit_name + suffix] for suffix in (".result.json.gz", ".checkpoint.json.gz"))
                result, checkpoint = read(bound(result_ref)), read(bound(checkpoint_ref))
            else:
                emit({"accountStart": [period, board, exit_name, candidate], "signals": len(schedule)})
                result, checkpoint = execute_account(data, schedule, plan["periods"][period]["policyConfigs"][exit_name], exit_name, fee)
                write(folder / (candidate + ".result.json.gz"), result)
                write(folder / (candidate + ".checkpoint.json.gz"), checkpoint)
                result_ref, checkpoint_ref = ref(folder / (candidate + ".result.json.gz")), ref(folder / (candidate + ".checkpoint.json.gz"))
            summary = summarize_account(result, checkpoint, policy_id=candidate, policy_label=candidate, intervals=intervals(period, atlas))
            require(not summary["quality"]["integrityIssues"], "Accounting integrity failure")
            summary_path = folder / (candidate + ".summary.json"); write(summary_path, summary)
            rows.append({"period": period, "board": board, "exit": exit_name, "candidate": candidate,
                "population": population, "model": model, "summary": ref(summary_path), "result": result_ref,
                "checkpoint": checkpoint_ref, "schedule": schedule_ref, "reusedAccount": candidate == "baseline"})
            emit({"accountComplete": [period, board, exit_name, candidate], "returnPct": summary["account"]["netReturnPct"],
                "drawdownPct": summary["account"]["maxDrawdownPct"], "closed": summary["closedEpisodes"]["count"]})
            del result, checkpoint, summary; gc.collect()
        require(digest(data) == plan["sourceData"][period][board]["accountDatasetDigest"], "Account mutated frozen source")
        receipt = {"period": period, "board": board, "exit": exit_name, "completed": True, "completedAt": now(),
            "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
            "inputValidationSha256": hashes["validation"], "preAccountTestsSha256": hashes["tests"],
            "runStartSha256": start_sha, "rows": rows, "newAccounts": 4, "referenceAccountsReused": 1}
        require(atomic_once(terminal, receipt), "Group unexpectedly completed concurrently")
    if data is not None: del data; gc.collect()
    return {"completed": True, "scope": "board", "group": only_group,
        "completedGroups": [ref(OUT.joinpath(*group_key(group)) / "group-completion.json") for group in selected],
        "comparisonPublished": False}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_all_opportunity_feedback_runner.py")
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic runner checks failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "historicalAccounts": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("mode", nargs="?", choices=("register", "pretests", "prepare", "run"))
    parser.add_argument("--group"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    require(args.self_test or args.mode is not None, "Choose a runner mode")
    require(args.group is None or args.mode == "run", "--group is only valid for run")
    try:
        emit(self_test() if args.self_test else run(args.group) if args.mode == "run" else globals()[args.mode]())
    except Exception as error:
        if OUT.exists() and not args.self_test:
            folder = OUT / "failures"; folder.mkdir(exist_ok=True)
            write(folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"),
                {"at": now(), "mode": args.mode, "group": args.group, "type": type(error).__name__, "message": str(error)})
        raise
