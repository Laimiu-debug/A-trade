"""Register, freeze and replay a new full-gap source under two existing exits.

There is no shadow-label, model-fitting or old UR opportunity dependency.
Registration and all historical execution require explicit CLI invocation.
"""
from argparse import ArgumentParser
import ast
from copy import deepcopy
from datetime import datetime
import gc
import gzip
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "full-gap-retention-v1"
PARENT = BASE / "common-support-switch-v1"
PROTOCOL = ROOT / "docs/full-gap-retention-v1.json"
PROGRAM_SHA = "4879c3881f9ce60f93f8b8f797105cbd82201da3cd6e4f3cb33f23c1eabf9bc8"
PREVIOUS_DELIVERY_SHA = "d7517568052ab89a58f7174439e4e325f84e12046b488418eab8535a04ac42db"
FROZEN_RUNNER = "scripts/run-all-opportunity-feedback.py"
FROZEN_RUNNER_SHA = "1d0a9fca498669a170f016fa46af4353562f5353427331cd88b9c4a50917e67c"
VERIFIER = "scripts/verify-full-gap-retention.py"
SUMMARY = "scripts/summarize-full-gap-retention.py"
TEST = "tests/test_research_full_gap_retention.py"
SID = "fullGapRetentionV1"
PERIODS, BOARDS = ("2022-2023", "2024-2025"), ("main", "chinext", "star")
EXITS, POLICIES = ("trail10", "volatilityTrail"), ("control", "retained")

_spec = importlib.util.spec_from_file_location("_gap_frozen_account_helpers", ROOT / FROZEN_RUNNER)
OLD = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(OLD)
require, sha, digest, ref, bound, read = OLD.require, OLD.sha, OLD.digest, OLD.ref, OLD.bound, OLD.read
write, atomic_once, emit, now = OLD.write, OLD.atomic_once, OLD.emit, OLD.now
execute_account, intervals = OLD.execute_account, OLD.intervals
require(sha(ROOT / FROZEN_RUNNER) == FROZEN_RUNNER_SHA, "Frozen account utilities changed")


def module(path):
    spec = importlib.util.spec_from_file_location("_gap_" + Path(path).stem.replace("-", "_"), ROOT / path)
    value = importlib.util.module_from_spec(spec); sys.modules[spec.name] = value; spec.loader.exec_module(value)
    return value


def config_for(config):
    """Only the frozen scorer identity changes; no execution parameter changes."""
    def replace(value):
        if isinstance(value, dict): return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list): return [replace(v) for v in value]
        return SID if value == "strongUndercutReclaimV1" else deepcopy(value)
    answer = replace(config)
    require(answer["allocation"]["strategyIds"] == [SID]
            and [r["id"] for r in answer["strategies"]] == [SID], "Unexpected source scorer identity")
    return answer


def fee_policy(period, plan):
    from service.research_account_fees import DatedFeePolicy, LegacyFeePolicy
    if period == "2024-2025": return LegacyFeePolicy()
    fee = plan["feePolicy"]
    return DatedFeePolicy(commission_rate=fee["commissionRate"], min_commission=fee["minCommission"],
        commission_includes_transfer=fee["commissionIncludesTransfer"], stamp_duty_override=fee["stampDutyOverride"],
        security_type=fee["securityType"], trading_mode=fee["tradingMode"])


def service_closure(paths):
    """Pin local static service imports, without inheriting unrelated trial pins."""
    pending, found = list(paths), set()
    while pending:
        name = pending.pop()
        if name in found: continue
        path = ROOT / name
        require(path.is_file(), "Missing required code: " + name)
        found.add(name)
        if not name.endswith(".py"): continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            candidates = []
            if isinstance(node, ast.ImportFrom):
                if node.level and name.startswith("service/"):
                    candidates = ["service/" + (node.module or alias.name).replace(".", "/") + ".py" for alias in node.names]
                elif node.module and node.module.startswith("service"):
                    candidates = [node.module.replace(".", "/") + ".py"]
                    if node.module == "service": candidates = ["service/" + alias.name + ".py" for alias in node.names]
            elif isinstance(node, ast.Import):
                candidates = [a.name.replace(".", "/") + ".py" for a in node.names if a.name.startswith("service.")]
            pending.extend(n for n in candidates if (ROOT / n).is_file())
    return sorted(found)


def verify_plan():
    plan = read(OUT / "plan.json")
    for key in ("sourcePins", "inputPins"):
        for path, expected in plan[key].items(): bound({"path": path, "sha256": expected})
    require(plan["strategyId"] == SID and plan["policies"] == list(POLICIES)
            and plan["exits"] == list(EXITS) and plan["newAccounts"] == 24
            and plan["referenceAccountsReused"] == 0, "Fixed experiment matrix changed")
    return plan


def register():
    require(not OUT.exists(), "Batch already registered; no replacement")
    require(sha(BASE / "program.json") == PROGRAM_SHA and sha(PARENT / "delivery.json") == PREVIOUS_DELIVERY_SHA,
            "Registry or completed predecessor differs")
    program = read(BASE / "program.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 41 and program["goalStatus"] == "active"
            and program["targetAchieved"] is False, "Unexpected research state")
    parent, proof = read(PARENT / "plan.json"), read(PARENT / "validation.json")
    require(proof["passed"] is True and proof["planSha256"] == sha(PARENT / "plan.json"), "Parent binding not audited")
    protocol = read(PROTOCOL)
    require(protocol["candidateIds"] == list(POLICIES) and protocol["trialAccounting"]["newAccounts"] == 24
            and protocol["trialAccounting"]["referenceAccountsReused"] == 0, "Protocol matrix differs")
    inputs = {}
    def pin(item):
        bound(item); inputs[item["path"]] = item["sha256"]; return deepcopy(item)
    parent_refs = {key: pin(ref(PARENT / filename)) for key, filename in (
        ("parentPlan", "plan.json"), ("previousDelivery", "delivery.json"), ("parentValidation", "validation.json"))}
    board_plan = pin(ref(BASE / "board-coverage-reclaim-v1/plan.json"))
    sources = deepcopy(parent["sourceData"])
    for period in PERIODS:
        for board in BOARDS:
            source = sources[period][board]
            for value in source.values():
                if isinstance(value, dict) and {"path", "sha256"} <= set(value): pin(value)
            source_proof = read(bound(source["sourceValidation"]))
            require(source_proof["passed"] is True and source_proof["planSha256"] == board_plan["sha256"]
                    and source_proof["datasetPins"][source["accountData"]["path"]] == source["accountData"]["sha256"],
                    "Frozen board-source validation differs")
    contract = pin(ref(ROOT / "docs/account-exit-comparison-v1.json"))
    fee = read(bound(contract))["feePolicy"]
    require(type(fee["minCommission"]) is int and fee["minCommission"] == 5, "Preserve typed fee setting identity")
    periods = {p: {"startDate": parent["periods"][p]["startDate"], "endDate": parent["periods"][p]["endDate"],
                   "policyConfigs": {e: config_for(parent["periods"][p]["policyConfigs"][e]) for e in EXITS}} for p in PERIODS}
    seed = ["docs/full-gap-retention-v1.json", "service/research_full_gap_retention.py", TEST,
            "scripts/run-full-gap-retention.py", "scripts/execute-full-gap-retention.py", VERIFIER,
            "tests/test_full_gap_retention_verifier.py", SUMMARY, "tests/test_full_gap_retention_readout.py",
            "scripts/summarize-online-feedback-entry.py", "scripts/summarize-undercut-reclaim-timing.py",
            FROZEN_RUNNER, "scripts/run-online-feedback-entry.py", "service/research_account_outcomes.py",
            "service/research_fixed_signal_account.py", "service/research_volatility_trail.py",
            "tests/test_research_fixed_signal_account.py", "tests/test_research_account_fees.py",
            "tests/test_research_account_outcomes.py", "tests/test_research_volatility_trail.py",
            "scripts/verify-relative-repair-screen.py", "scripts/verify-market-context-screen.py",
            "scripts/verify-supplemental-force-context.py"]
    auditor = module(VERIFIER)
    seed += list(auditor.DEPENDENCIES)
    # The summarizer publishes direct frozen helper dependencies for its arithmetic.
    summarizer = module(SUMMARY)
    seed += list(getattr(summarizer, "DEPENDENCIES", {}))
    code = service_closure(seed)
    code_pins = {name: sha(ROOT / name) for name in code}
    for name in set(code_pins) & set(parent["sourcePins"]):
        require(code_pins[name] == parent["sourcePins"][name], "Inherited frozen implementation changed: " + name)
    for name, expected in auditor.DEPENDENCIES.items(): require(code_pins[name] == expected, "Independent helper changed")
    plan = {"schema": OUT.name, "registeredAt": now(), "previousProgram": ref(BASE / "program.json"),
            **parent_refs, "sourcePlan": board_plan, "accountContract": contract, "feePolicy": deepcopy(fee),
            "sourceData": sources, "periods": periods, "boards": list(BOARDS), "exits": list(EXITS),
            "policies": list(POLICIES), "strategyId": SID, "atlas": pin(parent["atlas"]),
            "protocol": ref(PROTOCOL), "rules": protocol, "inputPins": inputs, "sourcePins": code_pins,
            **protocol["trialAccounting"], "parameterSearch": False, "holdoutClaim": False,
            "certified": False, "targetAchieved": False}
    for p in PERIODS:
        for e in EXITS: fee_policy(p, plan).settings(periods[p]["policyConfigs"][e])
    OUT.mkdir(exist_ok=False)
    write(OUT / "plan.json", plan)
    with (OUT / "program-before.json").open("xb") as stream: stream.write((BASE / "program.json").read_bytes())
    for name in code:
        target = OUT / "sources" / name; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: stream.write((ROOT / name).read_bytes())
    return {"registered": True, "plan": ref(OUT / "plan.json"), "candidateTrialCountUnchanged": 41}


def pretests():
    plan = verify_plan(); started = now()
    require(not (OUT / "run-start.json").exists(), "Checks must precede all accounts")
    commands = [[sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_full_gap_retention", "-v"],
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_fixed_signal_account",
         "tests.test_research_account_fees", "tests.test_research_account_outcomes", "tests.test_research_volatility_trail", "-v"],
        [sys.executable, "-X", "utf8", "-B", VERIFIER, "--self-test"],
        [sys.executable, "-X", "utf8", "-B", SUMMARY, "--self-test"]]
    results = []
    for argv in commands:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        results.append({"argv": argv, "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, "Pre-account check failed: " + result.stdout + result.stderr)
    counts = [re.search(r"Ran (\d+) tests?", row["stderr"]) for row in results[:2]]
    require(all(counts), "Actual test counts required")
    verify_plan()
    receipt = {"schema": OUT.name + "-pre-account-tests", "passed": True, "startedAt": started, "completedAt": now(),
               "planSha256": sha(OUT / "plan.json"), "sourcePins": plan["sourcePins"], "commands": results,
               "newUnitTests": int(counts[0].group(1)), "inheritedUnitTests": int(counts[1].group(1)),
               "allChecksBeforeFirstNewAccount": True}
    write(OUT / "pre-account-tests.json", receipt)
    return {"passed": True, "newUnitTests": receipt["newUnitTests"], "inheritedUnitTests": receipt["inheritedUnitTests"]}


def line(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def prepare():
    from service.research_full_gap_retention import build_full_gap_retention
    plan = verify_plan(); tests = read(OUT / "pre-account-tests.json")
    require(tests["passed"] is True and tests["planSha256"] == sha(OUT / "plan.json")
            and tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Frozen successful pretests required")
    require(not (OUT / "run-start.json").exists(), "All schedules must be prepared before accounts")
    groups = []
    for period in PERIODS:
        for board in BOARDS:
            source = plan["sourceData"][period][board]; data = read(bound(source["accountData"]))
            require(digest(data) == source["accountDatasetDigest"], "Dataset content binding differs")
            folder = OUT / period / board; folder.mkdir(parents=True, exist_ok=False)
            emit({"preparing": [period, board], "symbols": len(data["securities"])})
            with gzip.open(folder / "symbol-evidence.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as stream:
                evidence = build_full_gap_retention(data, start=plan["periods"][period]["startDate"],
                    end=plan["periods"][period]["endDate"], on_evidence=lambda row: line(stream, row),
                    progress=lambda value: emit({"period": period, "board": board, **value}))
            for key, name in (("dailyRows", "daily-evidence.jsonl.gz"), ("formations", "formations.jsonl.gz"),
                              ("opportunities", "opportunities.jsonl.gz")):
                with gzip.open(folder / name, "xt", encoding="utf-8", compresslevel=1) as stream:
                    for row in evidence[key]: line(stream, row)
            write(folder / "metadata.json", {k: v for k, v in evidence.items()
                if k not in ("dailyRows", "formations", "opportunities", "schedules")})
            for policy in POLICIES: write(folder / (policy + ".signals.json"), evidence["schedules"][policy])
            require(digest(data) == source["accountDatasetDigest"], "Feature builder mutated source")
            group = {"period": period, "board": board, "accountData": source["accountData"],
                     "accountDatasetDigest": source["accountDatasetDigest"], "counts": evidence["counts"],
                     "metadata": ref(folder / "metadata.json"), "daily": ref(folder / "daily-evidence.jsonl.gz"),
                     "symbolEvidence": ref(folder / "symbol-evidence.jsonl.gz"), "formations": ref(folder / "formations.jsonl.gz"),
                     "opportunities": ref(folder / "opportunities.jsonl.gz"),
                     "schedules": {policy: ref(folder / (policy + ".signals.json")) for policy in POLICIES}}
            write(folder / "input-completion.json", {**group, "planSha256": sha(OUT / "plan.json"), "completedAt": now()})
            group["inputReceipt"] = ref(folder / "input-completion.json"); groups.append(group)
            emit({"prepared": [period, board], "counts": evidence["counts"]})
            del data, evidence; gc.collect()
    verify_plan()
    value = {"schema": OUT.name + "-inputs", "completedAt": now(), "planSha256": sha(OUT / "plan.json"),
             "preAccountTestsSha256": sha(OUT / "pre-account-tests.json"), "groups": groups,
             "allSchedulesFrozenBeforeAccounts": True, "newAccountsExecuted": 0,
             "counts": {"sourceGroups": 6, "preparedSchedules": 12, "accountScheduleBindings": 24,
                        "rawSetups": sum(g["counts"]["rawSetups"] for g in groups),
                        "opportunities": sum(g["counts"]["opportunities"] for g in groups),
                        "signals": {p: sum(g["counts"]["signals"][p] for g in groups) for p in POLICIES}}}
    write(OUT / "input-completion.json", value)
    return {"prepared": True, "completion": ref(OUT / "input-completion.json"), "counts": value["counts"]}


def validate_prerequisites(plan, completion, validation, tests, hashes):
    require(tests["passed"] is True and tests["planSha256"] == hashes["plan"]
            and tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True,
            "Pretests missing or changed")
    require(completion["planSha256"] == hashes["plan"] and completion["preAccountTestsSha256"] == hashes["tests"]
            and completion["allSchedulesFrozenBeforeAccounts"] is True and completion["newAccountsExecuted"] == 0,
            "All schedules must precede accounts")
    require(validation["passed"] is True and validation["planSha256"] == hashes["plan"]
            and validation["inputCompletionSha256"] == hashes["completion"]
            and validation["preAccountTestsSha256"] == hashes["tests"]
            and validation["verifierSha256"] == hashes["verifier"], "Independent input audit differs")
    require(len(completion["groups"]) == 6 and {(g["period"], g["board"]) for g in completion["groups"]}
            == {(p, b) for p in PERIODS for b in BOARDS}, "All six sources required")
    require(completion["counts"]["preparedSchedules"] == 12 and completion["counts"]["accountScheduleBindings"] == 24,
            "All24 account schedule bindings required before execution")
    for group in completion["groups"]:
        source = plan["sourceData"][group["period"]][group["board"]]
        require(group["accountData"] == source["accountData"] and group["accountDatasetDigest"] == source["accountDatasetDigest"]
                and set(group["schedules"]) == set(POLICIES), "Prepared source binding differs")
    require(datetime.fromisoformat(tests["completedAt"]) <= datetime.fromisoformat(completion["completedAt"]),
            "Tests must finish before input freeze")


def prerequisites():
    plan = verify_plan(); completion = read(OUT / "input-completion.json")
    tests, validation = read(OUT / "pre-account-tests.json"), read(OUT / "input-validation.json")
    hashes = {k: sha(OUT / name) for k, name in (("plan", "plan.json"), ("completion", "input-completion.json"),
              ("tests", "pre-account-tests.json"), ("validation", "input-validation.json"))}
    hashes["verifier"] = sha(ROOT / VERIFIER)
    validate_prerequisites(plan, completion, validation, tests, hashes)
    for group in completion["groups"]:
        for key in ("metadata", "daily", "symbolEvidence", "formations", "opportunities", "inputReceipt"): bound(group[key])
        for item in group["schedules"].values(): bound(item)
    return plan, completion, hashes


def group_key(row): return tuple(row[k] for k in ("period", "board", "exit"))


def checked_terminal(receipt, group, exit_name, hashes, run_sha):
    require(receipt["completed"] is True and group_key(receipt) == (group["period"], group["board"], exit_name), "Terminal identity differs")
    for key, value in (("planSha256", hashes["plan"]), ("inputCompletionSha256", hashes["completion"]),
                       ("inputValidationSha256", hashes["validation"]), ("preAccountTestsSha256", hashes["tests"]),
                       ("runStartSha256", run_sha)):
        require(receipt[key] == value, "Terminal dependency changed: " + key)
    require([row["policy"] for row in receipt["rows"]] == list(POLICIES), "Two terminal policies required")
    for row in receipt["rows"]:
        require(group_key(row) == group_key(receipt) and row["schedule"] == group["schedules"][row["policy"]]
                and row["reusedAccount"] is False, "Terminal account identity differs")
        for key in ("result", "checkpoint", "summary", "schedule"): bound(row[key])
    return receipt["rows"]


def collect():
    plan, completion, hashes = prerequisites()
    start = read(OUT / "run-start.json"); run_sha = sha(OUT / "run-start.json")
    require(start["planSha256"] == hashes["plan"] and start["inputCompletion"] == ref(OUT / "input-completion.json")
            and start["inputValidation"] == ref(OUT / "input-validation.json") and start["tests"] == ref(OUT / "pre-account-tests.json"),
            "Run start changed")
    rows, receipts, missing = [], [], []
    for group in completion["groups"]:
        for exit_name in EXITS:
            path = OUT / group["period"] / group["board"] / exit_name / "group-completion.json"
            if not path.exists(): missing.append([group["period"], group["board"], exit_name]); continue
            rows += checked_terminal(read(path), group, exit_name, hashes, run_sha); receipts.append(ref(path))
    if missing: return {"completed": False, "pendingGroups": missing}
    require(len(rows) == 24, "All24 new accounts required")
    value = {"schema": OUT.name + "-comparison", "completedAt": now(), "planSha256": hashes["plan"],
             "inputCompletionSha256": hashes["completion"], "inputValidationSha256": hashes["validation"],
             "preAccountTestsSha256": hashes["tests"], "runStartSha256": run_sha, "rows": rows,
             "groupCompletions": receipts, **plan["rules"]["trialAccounting"],
             "holdoutClaim": False, "certified": False, "targetAchieved": False}
    if not atomic_once(OUT / "comparison.json", value):
        saved = read(OUT / "comparison.json")
        require({k: v for k, v in saved.items() if k != "completedAt"} == {k: v for k, v in value.items() if k != "completedAt"},
                "Existing comparison differs; cannot replace")
    return {"completed": True, "accounts": 24, "comparison": ref(OUT / "comparison.json")}


def run(only_group=None):
    if only_group is None: return collect()
    require(only_group in {p + "/" + b for p in PERIODS for b in BOARDS}, "--group must be PERIOD/BOARD")
    from service.research_account_outcomes import summarize_account
    plan, completion, hashes = prerequisites()
    group = next(g for g in completion["groups"] if g["period"] + "/" + g["board"] == only_group)
    start_refs = {"planSha256": hashes["plan"], "inputCompletion": ref(OUT / "input-completion.json"),
                  "inputValidation": ref(OUT / "input-validation.json"), "tests": ref(OUT / "pre-account-tests.json")}
    atomic_once(OUT / "run-start.json", {"startedAt": now(), **start_refs})
    start = read(OUT / "run-start.json")
    require(all(start[key] == value for key, value in start_refs.items()), "Existing run-start binding differs")
    require(datetime.fromisoformat(completion["completedAt"]) <= datetime.fromisoformat(start["startedAt"]), "Inputs follow account start")
    run_sha = sha(OUT / "run-start.json")
    period, board = group["period"], group["board"]
    data, atlas = None, read(bound(plan["atlas"]))
    done = []
    for exit_name in EXITS:
        folder = OUT / period / board / exit_name; folder.mkdir(exist_ok=True)
        terminal = folder / "group-completion.json"
        if terminal.exists():
            checked_terminal(read(terminal), group, exit_name, hashes, run_sha); done.append(ref(terminal)); continue
        OLD.require_unstarted_group(folder)
        require(atomic_once(folder / "group-run-start.json", {"startedAt": now(), "planSha256": hashes["plan"],
                "runStartSha256": run_sha}), "Group already claimed; never duplicate an account")
        if data is None:
            data = read(bound(group["accountData"]))
            require(digest(data) == group["accountDatasetDigest"], "Dataset content changed")
        rows = []
        for policy in POLICIES:
            schedule = read(bound(group["schedules"][policy]))
            emit({"accountStart": [period, board, exit_name, policy], "signals": len(schedule)})
            result, checkpoint = execute_account(data, schedule, plan["periods"][period]["policyConfigs"][exit_name],
                                                 exit_name, fee_policy(period, plan))
            write(folder / (policy + ".result.json.gz"), result)
            write(folder / (policy + ".checkpoint.json.gz"), checkpoint)
            summary = summarize_account(result, checkpoint, policy_id=policy, policy_label=policy, intervals=intervals(period, atlas))
            require(not summary["quality"]["integrityIssues"], "Account integrity failure")
            write(folder / (policy + ".summary.json"), summary)
            rows.append({"period": period, "board": board, "exit": exit_name, "policy": policy,
                         "schedule": group["schedules"][policy], "result": ref(folder / (policy + ".result.json.gz")),
                         "checkpoint": ref(folder / (policy + ".checkpoint.json.gz")),
                         "summary": ref(folder / (policy + ".summary.json")), "reusedAccount": False})
            emit({"accountComplete": [period, board, exit_name, policy], "returnPct": summary["account"]["netReturnPct"],
                  "drawdownPct": summary["account"]["maxDrawdownPct"], "closed": summary["closedEpisodes"]["count"]})
            del result, checkpoint, summary; gc.collect()
        require(digest(data) == group["accountDatasetDigest"], "Account mutated frozen data")
        receipt = {"period": period, "board": board, "exit": exit_name, "completed": True, "completedAt": now(),
                   "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
                   "inputValidationSha256": hashes["validation"], "preAccountTestsSha256": hashes["tests"],
                   "runStartSha256": run_sha, "rows": rows, "newAccounts": 2, "referenceAccountsReused": 0}
        require(atomic_once(terminal, receipt), "Terminal unexpectedly already exists")
        done.append(ref(terminal))
    if data is not None: del data; gc.collect()
    return {"completed": True, "scope": "board", "group": only_group, "completedGroups": done, "comparisonPublished": False}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=Path(TEST).name)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic checks failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "historicalAccounts": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("mode", nargs="?", choices=("register", "pretests", "prepare", "run", "collect"))
    parser.add_argument("--group"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    require(args.self_test or args.mode is not None, "Choose a mode")
    require(args.group is None or args.mode == "run", "--group applies only to run")
    try:
        emit(self_test() if args.self_test else run(args.group) if args.mode == "run" else globals()[args.mode]())
    except Exception as error:
        if OUT.exists() and not args.self_test:
            directory = OUT / "failures"; directory.mkdir(exist_ok=True)
            write(directory / (datetime.now().strftime("%Y%m%dT%H%M%S%f") + ".json"),
                  {"at": now(), "mode": args.mode, "group": args.group, "type": type(error).__name__, "message": str(error)})
        raise
