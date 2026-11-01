"""Replay a fixed common-support Boolean switch; never fit or execute label paths."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime
import gc
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "common-support-switch-v1"
PARENT = BASE / "all-opportunity-feedback-v1"
PROTOCOL = ROOT / "docs/common-support-switch-v1.json"
PROGRAM_SHA = "4a8699a8fe2b926a45e98700562f486c3f95dae85392f880f2d967d791a70ace"
PREVIOUS_DELIVERY_SHA = "fd8abe5db443e566189148bde07d3ad02d9af333751b765133fe3c8873003324"
PARENT_RUNNER = "scripts/run-all-opportunity-feedback.py"
PARENT_RUNNER_SHA = "1d0a9fca498669a170f016fa46af4353562f5353427331cd88b9c4a50917e67c"
VERIFIER = "scripts/verify-common-support-switch.py"
SUMMARY = "scripts/summarize-common-support-switch.py"

_spec = importlib.util.spec_from_file_location("_frozen_all_opportunity_runner", ROOT / PARENT_RUNNER)
OLD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OLD)
require, sha, digest, ref, bound, read = OLD.require, OLD.sha, OLD.digest, OLD.ref, OLD.bound, OLD.read
records, write, atomic_once, emit, now, module = OLD.records, OLD.write, OLD.atomic_once, OLD.emit, OLD.now, OLD.module
require(sha(ROOT / PARENT_RUNNER) == PARENT_RUNNER_SHA, "Frozen account runner changed")
PERIODS, BOARDS, EXITS, MODELS, SID = OLD.PERIODS, OLD.BOARDS, OLD.EXITS, OLD.MODELS, OLD.SID
POPULATIONS = OLD.POPULATIONS
CANDIDATES = tuple("commonSupport__" + m for m in MODELS)
REFERENCE_CANDIDATES = ("baseline", *OLD.CANDIDATES)
SUPPORT_STATES = ("bothFitted", "originalOnlyFitted", "allOnlyFitted", "neitherFitted")
group_key, selected_groups = OLD.group_key, OLD.selected_groups
require_unstarted_group, execute_account, intervals = OLD.require_unstarted_group, OLD.execute_account, OLD.intervals


def dimensions(candidate):
    require(candidate in CANDIDATES, "Unknown common-support candidate")
    return "commonSupport", candidate.split("__")[1]


def reference_rows(plan, key):
    rows = [r for r in plan["referenceRows"] if group_key(r) == tuple(key)]
    require([r["candidate"] for r in rows] == list(REFERENCE_CANDIDATES), "Five exact parent accounts required")
    return rows


def verify_plan():
    plan = read(OUT / "plan.json")
    for field in ("sourcePins", "inputPins"):
        for path, value in plan[field].items(): bound({"path": path, "sha256": value})
    require(plan["candidates"] == list(CANDIDATES) and plan["supportStates"] == list(SUPPORT_STATES)
            and plan["newAccounts"] == 24 and plan["referenceAccountsReused"] == 60
            and plan["comparisonAccounts"] == 84 and len(plan["referenceRows"]) == 60, "Fixed matrix changed")
    return plan


def register():
    require(not OUT.exists(), "Batch already registered; never replace it")
    require(sha(BASE / "program.json") == PROGRAM_SHA and sha(PARENT / "delivery.json") == PREVIOUS_DELIVERY_SHA,
            "Registry or finalized predecessor changed")
    program, delivery = read(BASE / "program.json"), read(PARENT / "delivery.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 40 and program["goalStatus"] == "active"
            and program["targetAchieved"] is False, "Unexpected research state")
    for item in delivery["files"]: bound(item)
    parent = read(PARENT / "plan.json")
    comparison = read(PARENT / "comparison.json")
    validation = read(PARENT / "validation.json")
    report = read(PARENT / "readout.json")
    report_validation = read(PARENT / "readout-validation.json")
    require(validation["passed"] is True and validation["planSha256"] == sha(PARENT / "plan.json")
            and validation["comparisonSha256"] == sha(PARENT / "comparison.json"), "Parent accounts not audited")
    require(report_validation["passed"] is True and report_validation["planSha256"] == sha(PARENT / "plan.json")
            and report_validation["comparisonSha256"] == sha(PARENT / "comparison.json")
            and report_validation["validationSha256"] == sha(PARENT / "validation.json")
            and report_validation["readoutSha256"] == sha(PARENT / "readout.json"), "Parent readout not audited")
    require(len(comparison["rows"]) == report["counts"]["accounts"] == validation["counts"]["accounts"] == 60
            and validation["counts"]["newAccounts"] == 48 and validation["counts"]["reusedAccounts"] == 12
            and [r["artifactRefs"] for r in validation["accounts"]] == comparison["rows"], "Exact parent matrix required")
    protocol = read(PROTOCOL)
    require(protocol["candidateIds"] == list(CANDIDATES) and protocol["supportStates"] == list(SUPPORT_STATES)
            and protocol["models"] == list(MODELS) and protocol["trialAccounting"]["newAccounts"] == 24
            and protocol["trialAccounting"]["referenceAccountsReused"] == 60
            and protocol["trialAccounting"]["comparisonAccounts"] == 84, "Protocol matrix differs")
    sources = dict(parent["sourcePins"])
    additions = ["docs/common-support-switch-v1.json", "scripts/run-common-support-switch.py",
        "tests/test_common_support_switch_runner.py", VERIFIER, "tests/test_common_support_switch_verifier.py",
        SUMMARY, "tests/test_common_support_switch_readout.py", PARENT_RUNNER,
        "scripts/execute-common-support-switch.py"]
    sources.update({name: sha(ROOT / name) for name in additions})
    for name, value in sources.items(): require(sha(ROOT / name) == value, "Frozen inherited code changed: " + name)
    inputs = dict(parent["inputPins"])
    def pin(item):
        bound(item); inputs[item["path"]] = item["sha256"]; return deepcopy(item)
    parent_refs = {name: pin(ref(PARENT / filename)) for name, filename in (
        ("parentPlan", "plan.json"), ("parentDelivery", "delivery.json"), ("parentComparison", "comparison.json"),
        ("parentValidation", "validation.json"), ("parentReadout", "readout.json"),
        ("parentReadoutValidation", "readout-validation.json"), ("parentInputCompletion", "input-completion.json"),
        ("parentInputValidation", "input-validation.json"))}
    expected = [(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in REFERENCE_CANDIDATES]
    require([(*group_key(r), r["candidate"]) for r in comparison["rows"]] == expected, "Parent row order differs")
    for row in comparison["rows"]:
        for name in ("result", "checkpoint", "summary", "schedule"): pin(row[name])
    inherited = ("periods", "boards", "exits", "models", "sourceData", "baselineSchedules", "accountArtifacts",
                 "metadata", "decisionArtifacts", "libraryPaths", "opportunities", "atlas", "libraryPlan",
                 "libraryDelivery", "libraryReadout", "libraryReadoutValidation", "libraryValidation", "libraryCompletion")
    plan = {"schema": OUT.name, "registeredAt": now(), "previousProgram": ref(BASE / "program.json"),
        "previousDelivery": parent_refs["parentDelivery"], **parent_refs,
        **{name: deepcopy(parent[name]) for name in inherited}, "referenceRows": deepcopy(comparison["rows"]),
        "populations": list(POPULATIONS), "candidates": list(CANDIDATES), "supportStates": list(SUPPORT_STATES),
        "protocol": ref(PROTOCOL), "rules": protocol, "sourcePins": sources, "inputPins": inputs,
        **protocol["trialAccounting"], "candidateTrialCountBefore": 40,
        "parameterSearch": False, "holdoutClaim": False, "certified": False, "targetAchieved": False}
    OUT.mkdir(exist_ok=False)
    write(OUT / "plan.json", plan)
    with (OUT / "program-before.json").open("xb") as stream: stream.write((BASE / "program.json").read_bytes())
    for name in sources:
        target = OUT / "sources" / name; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: stream.write((ROOT / name).read_bytes())
    return {"registered": True, "plan": ref(OUT / "plan.json"), "candidateTrialCountUnchanged": 40}


def pretests():
    plan = verify_plan(); started = now()
    require(not (OUT / "run-start.json").exists(), "Pretests must precede accounts")
    commands = [[sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_common_support_switch_runner", "-v"],
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_fixed_signal_account",
         "tests.test_research_account_fees", "tests.test_research_account_outcomes", "tests.test_research_volatility_trail", "-v"],
        [sys.executable, "-X", "utf8", "-B", VERIFIER, "--self-test"],
        [sys.executable, "-X", "utf8", "-B", SUMMARY, "--self-test"]]
    results = []
    for argv in commands:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        results.append({"argv": argv, "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, "Pre-account check failed: " + result.stdout + result.stderr)
    counts = [re.search(r"Ran (\d+) tests?", r["stderr"]) for r in results[:2]]
    require(all(counts), "Actual test counts missing")
    verify_plan()
    receipt = {"schema": OUT.name + "-pre-account-tests", "passed": True, "startedAt": started, "completedAt": now(),
        "planSha256": sha(OUT / "plan.json"), "sourcePins": plan["sourcePins"], "commands": results,
        "newUnitTests": int(counts[0].group(1)), "inheritedUnitTests": int(counts[1].group(1)),
        "allChecksBeforeFirstNewAccount": True}
    write(OUT / "pre-account-tests.json", receipt)
    return {"passed": True, "newUnitTests": receipt["newUnitTests"], "inheritedUnitTests": receipt["inheritedUnitTests"]}


def support_state(original, all_executable):
    pair = original["status"] == "fitted", all_executable["status"] == "fitted"
    return {(True, True): "bothFitted", (True, False): "originalOnlyFitted",
            (False, True): "allOnlyFitted", (False, False): "neitherFitted"}[pair]


def select_schedule(schedule, original, all_executable, model, *, calendar, target_dates, key):
    """Validate frozen decisions, then select exact original signals without fitting."""
    for population, daily in zip(POPULATIONS, (original, all_executable)):
        OLD.select_schedule(schedule, daily, model, calendar=calendar, target_dates=target_dates, key=key, population=population)
    days = []
    for left, right in zip(original, all_executable, strict=True):
        a, b = left["models"][model], right["models"][model]
        state = support_state(a, b)
        selected_population = "allExecutable" if state == "bothFitted" else "originalEntered"
        allow = b["allow"] if state == "bothFitted" else a["allow"]
        days.append({"formationDate": left["formationDate"], "supportState": state,
            "selectedPopulation": selected_population, "allow": allow,
            "originalAllow": a["allow"], "allAllow": b["allow"]})
    by_day = {r["formationDate"]: r for r in days}
    signal_days = [by_day[s["signalDate"]] for s in schedule]
    selected = [deepcopy(s) for s, d in zip(schedule, signal_days) if d["allow"]]
    def histogram(rows, field, domain):
        counts = Counter(r[field] for r in rows)
        return {name: counts[name] for name in domain}
    counts = {"originalSignals": len(schedule), "scheduledSignals": len(selected),
        "rejectedSignals": len(schedule) - len(selected), "decisionDays": len(days),
        "supportStateDays": histogram(days, "supportState", SUPPORT_STATES),
        "supportStateSignals": histogram(signal_days, "supportState", SUPPORT_STATES),
        "admittedSignalsBySupportState": histogram([r for r in signal_days if r["allow"]], "supportState", SUPPORT_STATES),
        "rejectedSignalsBySupportState": histogram([r for r in signal_days if not r["allow"]], "supportState", SUPPORT_STATES),
        "selectedPopulationDays": histogram(days, "selectedPopulation", POPULATIONS),
        "selectedPopulationSignals": histogram(signal_days, "selectedPopulation", POPULATIONS),
        "originalAllowedDays": sum(r["originalAllow"] for r in days), "allAllowedDays": sum(r["allAllow"] for r in days),
        "selectedAllowedDays": sum(r["allow"] for r in days),
        "originalAllowedSignals": sum(r["originalAllow"] for r in signal_days), "allAllowedSignals": sum(r["allAllow"] for r in signal_days),
        "changedAllowDays": sum(r["allow"] != r["originalAllow"] for r in days),
        "changedAllowSignals": sum(r["allow"] != r["originalAllow"] for r in signal_days),
        "addedSignals": sum(r["allow"] and not r["originalAllow"] for r in signal_days),
        "removedSignals": sum(not r["allow"] and r["originalAllow"] for r in signal_days)}
    require(all(r["allow"] == r["originalAllow"] or r["supportState"] == "bothFitted" for r in days)
            and all(r["allow"] == r["allAllow"] or r["supportState"] != "bothFitted" for r in days), "Switch scope differs")
    return selected, counts


def prepare():
    plan = verify_plan(); tests = read(OUT / "pre-account-tests.json")
    require(tests["passed"] is True and tests["planSha256"] == sha(OUT / "plan.json")
            and tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Exact pre-account checks required")
    require(not (OUT / "run-start.json").exists(), "All masks must precede accounts")
    original = records(bound(plan["opportunities"]))
    require(len(original) == 8155, "Original opportunity denominator differs")
    groups = []
    for period in PERIODS:
        for board in BOARDS:
            schedule = read(bound(plan["baselineSchedules"][period][board]))
            require(schedule == [r["signals"]["immediate"] for r in original if r["period"] == period and r["board"] == board],
                    "Original formation/schedule join differs")
            md = read(bound(plan["metadata"][period][board]))
            for exit_name in EXITS:
                key = {"period": period, "board": board, "exit": exit_name}
                folder = OUT / period / board / exit_name; folder.mkdir(parents=True, exist_ok=False)
                decisions = plan["decisionArtifacts"][period][board][exit_name]
                daily = {p: records(bound(decisions[p])) for p in POPULATIONS}
                candidates = {}
                for model in MODELS:
                    selected, counts = select_schedule(schedule, daily["originalEntered"], daily["allExecutable"], model,
                        calendar=md["calendar"], target_dates=md["targetDates"], key=key)
                    candidate = "commonSupport__" + model; path = folder / (candidate + ".signals.json")
                    write(path, selected)
                    candidates[candidate] = {"population": "commonSupport", "model": model, "schedule": ref(path), "counts": counts}
                groups.append({**key, "inheritedRows": deepcopy(reference_rows(plan, group_key(key))),
                    "baselineSchedule": plan["baselineSchedules"][period][board],
                    "accountData": plan["sourceData"][period][board]["accountData"],
                    "decisions": decisions, "candidates": candidates})
                emit({"prepared": list(group_key(key)), "signals": {c: v["counts"]["scheduledSignals"] for c, v in candidates.items()}})
    verify_plan()
    value = {"schema": OUT.name + "-inputs", "planSha256": sha(OUT / "plan.json"),
        "preAccountTestsSha256": sha(OUT / "pre-account-tests.json"), "completedAt": now(), "groups": groups,
        "allSchedulesFrozenBeforeAccounts": True, "newAccountsExecuted": 0, "pathsExecuted": 0, "modelsRefitted": 0,
        "counts": {"preparedGroups": 12, "originalSchedules": 6, "preparedSchedules": 24,
            "sourceImmediateOpportunities": len(original), "sourceExitOpportunities": len(original) * 2, "referenceAccounts": 60}}
    write(OUT / "input-completion.json", value)
    return {"prepared": True, "groups": 12, "schedules": 24, "completion": ref(OUT / "input-completion.json")}


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
    require([group_key(g) for g in completion["groups"]] == [(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS],
            "All twelve groups required in original order")
    for g in completion["groups"]:
        p, b, e = group_key(g)
        require(g["inheritedRows"] == reference_rows(plan, (p, b, e))
                and g["baselineSchedule"] == plan["baselineSchedules"][p][b]
                and g["accountData"] == plan["sourceData"][p][b]["accountData"]
                and g["decisions"] == plan["decisionArtifacts"][p][b][e], "Prepared binding differs")
        require(list(g["candidates"]) == list(CANDIDATES), "Both masks required per group")
        for candidate, item in g["candidates"].items():
            require((item["population"], item["model"]) == dimensions(candidate), "Candidate dimensions differ")
    require(completion["counts"] == {"preparedGroups": 12, "originalSchedules": 6, "preparedSchedules": 24,
            "sourceImmediateOpportunities": 8155, "sourceExitOpportunities": 16310, "referenceAccounts": 60},
            "All 24 schedules must be frozen and audited")
    require(datetime.fromisoformat(tests["completedAt"]) <= datetime.fromisoformat(completion["completedAt"]),
            "Pretests must precede schedule freeze")


def checked_terminal(receipt, group, hashes, start_sha):
    require(receipt["completed"] is True and receipt["planSha256"] == hashes["plan"]
            and receipt["inputCompletionSha256"] == hashes["completion"]
            and receipt["inputValidationSha256"] == hashes["validation"]
            and receipt["preAccountTestsSha256"] == hashes["tests"] and receipt["runStartSha256"] == start_sha
            and group_key(receipt) == group_key(group) and receipt["newAccounts"] == 2
            and receipt["referenceAccountsReused"] == 5, "Completed group binding differs")
    rows = receipt["rows"]
    require([r["candidate"] for r in rows] == [*REFERENCE_CANDIDATES, *CANDIDATES]
            and rows[:5] == group["inheritedRows"], "Parent five rows must remain byte-bound and unchanged")
    for row in rows:
        require(group_key(row) == group_key(group), "Completed account source differs")
        if row["candidate"] in CANDIDATES:
            require((row["population"], row["model"]) == dimensions(row["candidate"])
                    and row["reusedAccount"] is False
                    and row["schedule"] == group["candidates"][row["candidate"]]["schedule"], "New account dimensions or mask differ")
            folder = OUT.joinpath(*group_key(group))
            for field, suffix in (("result", ".result.json.gz"), ("checkpoint", ".checkpoint.json.gz"), ("summary", ".summary.json")):
                require((ROOT / row[field]["path"]).resolve() == folder / (row["candidate"] + suffix), "New account artifact path differs")
        for field in ("result", "checkpoint", "summary", "schedule"): bound(row[field])
    return rows


def collect_comparison(plan, completion, hashes, start_sha):
    rows, missing, receipts = [], [], []
    for group in completion["groups"]:
        path = OUT.joinpath(*group_key(group)) / "group-completion.json"
        if not path.exists(): missing.append("/".join(group_key(group))); continue
        rows.extend(checked_terminal(read(path), group, hashes, start_sha)); receipts.append(ref(path))
    if missing: return {"completed": False, "pendingGroups": missing, "terminalGroups": len(receipts)}
    inherited = [r for r in rows if r["candidate"] in REFERENCE_CANDIDATES]
    new = [r for r in rows if r["candidate"] in CANDIDATES]
    require(len(rows) == 84 and len(new) == 24 and inherited == plan["referenceRows"], "Full 84-account matrix required")
    value = {"schema": OUT.name + "-comparison", "completedAt": now(), "planSha256": hashes["plan"],
        "inputCompletionSha256": hashes["completion"], "inputValidationSha256": hashes["validation"],
        "preAccountTestsSha256": hashes["tests"], "runStartSha256": start_sha,
        "rows": rows, "referenceRows": inherited, "newRows": new, "groupCompletions": receipts,
        **plan["rules"]["trialAccounting"], "holdoutClaim": False, "certified": False, "targetAchieved": False}
    path = OUT / "comparison.json"
    if not atomic_once(path, value):
        saved = read(path)
        require({k: v for k, v in saved.items() if k != "completedAt"} == {k: v for k, v in value.items() if k != "completedAt"},
                "Existing comparison differs; never overwrite")
    return {"completed": True, "accounts": 84, "newAccounts": 24, "comparison": ref(path)}


def run(only_group=None):
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan(); completion = read(OUT / "input-completion.json")
    validation, tests = read(OUT / "input-validation.json"), read(OUT / "pre-account-tests.json")
    hashes = {k: sha(OUT / filename) for k, filename in (("plan", "plan.json"), ("completion", "input-completion.json"),
        ("validation", "input-validation.json"), ("tests", "pre-account-tests.json"))}
    hashes["verifier"] = sha(ROOT / VERIFIER)
    validate_prerequisites(plan, completion, validation, tests, hashes)
    for group in completion["groups"]:
        for item in group["candidates"].values(): bound(item["schedule"])
    selected = selected_groups(completion["groups"], only_group)
    start_refs = {"planSha256": hashes["plan"], "inputCompletion": ref(OUT / "input-completion.json"),
        "inputValidation": ref(OUT / "input-validation.json"), "tests": ref(OUT / "pre-account-tests.json")}
    start_path = OUT / "run-start.json"
    atomic_once(start_path, {"startedAt": now(), **start_refs})
    start = read(start_path)
    require(all(start[k] == v for k, v in start_refs.items()), "Existing run-start dependency changed")
    require(datetime.fromisoformat(completion["completedAt"]) <= datetime.fromisoformat(start["startedAt"]), "Freeze must precede accounts")
    start_sha = sha(start_path)
    if only_group is None: return collect_comparison(plan, completion, hashes, start_sha)
    atlas = read(bound(plan["atlas"])); data, cached_key = None, None
    for group in selected:
        p, b, e = group_key(group); folder = OUT / p / b / e; terminal = folder / "group-completion.json"
        if terminal.exists():
            checked_terminal(read(terminal), group, hashes, start_sha); emit({"skippedCompletedGroup": [p, b, e]}); continue
        require_unstarted_group(folder)
        write(folder / "group-run-start.json", {"startedAt": now(), "planSha256": hashes["plan"], "runStartSha256": start_sha})
        if cached_key != (p, b):
            if data is not None: del data; gc.collect()
            data = read(bound(group["accountData"])); cached_key = p, b
            require(digest(data) == plan["sourceData"][p][b]["accountDatasetDigest"], "Source dataset digest differs")
        if p == "2022-2023":
            helper = module("load-market-context-screen.py")._module("run-direction-account-screen.py").load_helper()
            fee = helper.fee_policy(helper.load_binding()["document"])
        else: fee = LegacyFeePolicy()
        # Parent account artifacts, including summaries, are references only.
        rows = deepcopy(group["inheritedRows"])
        for row in rows:
            for field in ("result", "checkpoint", "summary", "schedule"): bound(row[field])
        for candidate in CANDIDATES:
            population, model = dimensions(candidate); schedule_ref = group["candidates"][candidate]["schedule"]
            schedule = read(bound(schedule_ref))
            emit({"accountStart": [p, b, e, candidate], "signals": len(schedule)})
            result, checkpoint = execute_account(data, schedule, plan["periods"][p]["policyConfigs"][e], e, fee)
            result_path, checkpoint_path = folder / (candidate + ".result.json.gz"), folder / (candidate + ".checkpoint.json.gz")
            write(result_path, result); write(checkpoint_path, checkpoint)
            summary = summarize_account(result, checkpoint, policy_id=candidate, policy_label=candidate, intervals=intervals(p, atlas))
            require(not summary["quality"]["integrityIssues"], "Accounting integrity failure")
            summary_path = folder / (candidate + ".summary.json"); write(summary_path, summary)
            rows.append({"period": p, "board": b, "exit": e, "candidate": candidate, "population": population, "model": model,
                "summary": ref(summary_path), "result": ref(result_path), "checkpoint": ref(checkpoint_path),
                "schedule": schedule_ref, "reusedAccount": False})
            emit({"accountComplete": [p, b, e, candidate], "returnPct": summary["account"]["netReturnPct"],
                  "drawdownPct": summary["account"]["maxDrawdownPct"], "closed": summary["closedEpisodes"]["count"]})
            del result, checkpoint, summary; gc.collect()
        require(digest(data) == plan["sourceData"][p][b]["accountDatasetDigest"], "Account mutated frozen source")
        receipt = {"period": p, "board": b, "exit": e, "completed": True, "completedAt": now(),
            "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
            "inputValidationSha256": hashes["validation"], "preAccountTestsSha256": hashes["tests"],
            "runStartSha256": start_sha, "rows": rows, "newAccounts": 2, "referenceAccountsReused": 5}
        require(atomic_once(terminal, receipt), "Group unexpectedly completed concurrently")
    if data is not None: del data; gc.collect()
    return {"completed": True, "scope": "board", "group": only_group,
        "completedGroups": [ref(OUT.joinpath(*group_key(g)) / "group-completion.json") for g in selected], "comparisonPublished": False}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_common_support_switch_runner.py")
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic runner checks failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "historicalAccounts": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("mode", nargs="?", choices=("register", "pretests", "prepare", "run"))
    parser.add_argument("--mode", dest="mode_option", choices=("register", "pretests", "prepare", "run"))
    parser.add_argument("--group"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    require(args.mode is None or args.mode_option is None or args.mode == args.mode_option, "Conflicting modes")
    args.mode = args.mode_option or args.mode
    require(args.self_test or args.mode is not None, "Choose a runner mode")
    require(args.group is None or args.mode == "run", "--group is only valid for run")
    try:
        emit(self_test() if args.self_test else run(args.group) if args.mode == "run" else globals()[args.mode]())
    except Exception as error:
        if OUT.exists() and not args.self_test:
            folder = OUT / "failures"; folder.mkdir(exist_ok=True)
            write(folder / (datetime.now().strftime("%Y%m%dT%H%M%S%f") + ".json"),
                {"at": now(), "mode": args.mode, "group": args.group, "type": type(error).__name__, "message": str(error)})
        raise
