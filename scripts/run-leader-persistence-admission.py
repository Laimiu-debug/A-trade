"""Frozen common-A queue / historical-persistence account comparison."""
from argparse import ArgumentParser
import ast
from copy import deepcopy
from datetime import datetime
import gc
import gzip
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "leader-persistence-admission-v1"
PREVIOUS = BASE / "leader-stage-coverage-v1"
LAG = BASE / "leader-lag-followers-v1"
ACCOUNT_PARENT = BASE / "leader-amount-handoff-v1"
PROTOCOL = ROOT / "docs/leader-persistence-admission-v1.json"
PROGRAM_SHA = "499157c26e4d641f121652a2a788b4508bff72555164ebd70828a0fc7d07029b"
PREVIOUS_SHA = "523b10adaf72c04b29608d97a96a7a79c72ba639a86f4ac3ded59d3353a69dfc"
HELPER = "scripts/run-all-opportunity-feedback.py"
HELPER_SHA = "1d0a9fca498669a170f016fa46af4353562f5353427331cd88b9c4a50917e67c"
SID = "leaderPersistenceAdmissionV1"
PERIODS, BOARDS = ("2022-2023", "2024-2025"), ("main", "chinext", "star")
POLICIES, EXITS = ("knownAll", "persistent", "nonpersistent"), ("trail10",)
VERIFIER = "scripts/verify-leader-persistence-admission.py"
SUMMARY = "scripts/summarize-leader-persistence-admission.py"

def module(path):
    spec = importlib.util.spec_from_file_location("_admission_" + Path(path).stem.replace("-", "_"), ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value

OLD = module(HELPER)
require, sha, digest, ref, bound, read, write, atomic_once, emit, now = (
    getattr(OLD, k) for k in ("require", "sha", "digest", "ref", "bound", "read", "write", "atomic_once", "emit", "now"))
require(sha(ROOT / HELPER) == HELPER_SHA, "Frozen account helper changed")
execute_account, intervals = OLD.execute_account, OLD.intervals

def closure(paths):
    pending, found = list(paths), set()
    while pending:
        name = pending.pop()
        if name in found: continue
        path = ROOT / name
        require(path.is_file(), "Missing source: " + name)
        found.add(name)
        if not name.endswith(".py"): continue
        for node in ast.walk(ast.parse(path.read_text("utf-8-sig"))):
            candidates = []
            if isinstance(node, ast.ImportFrom) and node.level:
                base = path.parent
                for _ in range(node.level - 1): base = base.parent
                if node.module:
                    candidate = base.joinpath(*node.module.split(".")).with_suffix(".py")
                    if candidate.is_relative_to(ROOT): candidates = [candidate.relative_to(ROOT).as_posix()]
                else:
                    candidates = [(base / (a.name + ".py")).relative_to(ROOT).as_posix() for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "service":
                    candidates = ["service/" + a.name + ".py" for a in node.names]
                elif node.module.startswith("service."):
                    candidates = [node.module.replace(".", "/") + ".py"]
            elif isinstance(node, ast.Import):
                candidates = [a.name.replace(".", "/") + ".py" for a in node.names if a.name.startswith("service.")]
            pending.extend(n for n in candidates if (ROOT / n).is_file())
    return sorted(found)

def config_for(config):
    def replace(value):
        if isinstance(value, dict): return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list): return [replace(v) for v in value]
        return SID if value == "leaderAmountHandoffV1" else deepcopy(value)
    result = replace(config)
    require(result["allocation"]["strategyIds"] == [SID] and result["strategies"][0]["id"] == SID, "Scorer identity")
    require(result["stopLossPct"] == .05 and result["trailingStopPct"] == .1 and
            result["takeProfitPct"] == result["maxHoldingDays"] == 0, "Frozen trail10 required")
    return result

def fee_policy(period, plan):
    from service.research_account_fees import DatedFeePolicy, LegacyFeePolicy
    if period == "2024-2025": return LegacyFeePolicy()
    f = plan["feePolicy"]
    return DatedFeePolicy(commission_rate=f["commissionRate"], min_commission=f["minCommission"],
        commission_includes_transfer=f["commissionIncludesTransfer"], stamp_duty_override=f["stampDutyOverride"],
        security_type=f["securityType"], trading_mode=f["tradingMode"])

def verify_plan():
    plan = read(OUT / "plan.json")
    for key in ("sourcePins", "inputPins"):
        for name, expected in plan[key].items(): bound({"path": name, "sha256": expected})
    require(sha(BASE / "program.json") == PROGRAM_SHA, "Registry changed during experiment")
    require(plan["policies"] == list(POLICIES) and plan["exits"] == list(EXITS) and
            plan["strategyId"] == SID and plan["newAccounts"] == 18 and plan["referenceAccountsReused"] == 0,
            "Fixed experiment changed")
    return plan

def register():
    require(not OUT.exists(), "Never replace a registered experiment")
    require(sha(BASE / "program.json") == PROGRAM_SHA and sha(PREVIOUS / "delivery.json") == PREVIOUS_SHA, "Predecessor differs")
    previous = read(PREVIOUS / "delivery.json")
    for item in previous["files"]: require(bound(item).is_relative_to(PREVIOUS), "Foreign prior member")
    program = read(BASE / "program.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 47 and program["goalStatus"] == "active"
            and program["targetAchieved"] is False, "Prior progress")
    inputs = {}
    def pin(item):
        bound(item); inputs[item["path"]] = item["sha256"]; return deepcopy(item)
    prior_refs = {}
    for key, folder, name in (
        ("previousDelivery", PREVIOUS, "delivery.json"), ("previousPlan", PREVIOUS, "plan.json"),
        ("previousValidation", PREVIOUS, "validation.json"), ("previousReadoutValidation", PREVIOUS, "readout-validation.json"),
        ("lagPlan", LAG, "plan.json"), ("lagInputCompletion", LAG, "input-completion.json"),
        ("lagInputValidation", LAG, "input-validation.json"), ("lagValidation", LAG, "validation.json"),
        ("parentAccountPlan", ACCOUNT_PARENT, "plan.json"), ("parentAccountValidation", ACCOUNT_PARENT, "validation.json"),
        ("parentAccountDelivery", ACCOUNT_PARENT, "delivery.json"), ("parentAccountComparison", ACCOUNT_PARENT, "comparison.json")):
        prior_refs[key] = pin(ref(folder / name))
    account_plan = read(bound(prior_refs["parentAccountPlan"]))
    for item in read(bound(prior_refs["parentAccountDelivery"]))["files"]:
        require(bound(item).is_relative_to(ACCOUNT_PARENT), "Foreign account-parent member")
    account_proof = read(bound(prior_refs["parentAccountValidation"]))
    require(account_proof["passed"] and account_proof["planSha256"] == prior_refs["parentAccountPlan"]["sha256"]
            and account_proof["comparisonSha256"] == prior_refs["parentAccountComparison"]["sha256"], "Parent account proof")
    source_plan = pin(account_plan["sourcePlan"])
    sources = deepcopy(account_plan["sourceData"])
    prior_plan = read(bound(prior_refs["previousPlan"]))
    parent_data = {p: {} for p in PERIODS}
    for p in PERIODS:
        for b in BOARDS:
            group = next(g for g in prior_plan["groups"] if (g["period"], g["board"]) == (p, b))
            source = sources[p][b]
            require(source["accountData"] == group["accountData"], "Source cohort drift")
            for value in source.values():
                if isinstance(value, dict) and {"path", "sha256"} <= set(value): pin(value)
            parent_data[p][b] = {"metadata": pin(group["lagMetadata"]), "daily": pin(group["lagDaily"]),
                                "accountData": pin(group["accountData"])}
    contract = pin(ref(ROOT / "docs/account-exit-comparison-v1.json"))
    fee = deepcopy(read(bound(contract))["feePolicy"])
    require(type(fee["minCommission"]) is int and fee["minCommission"] == 5, "Typed fee policy")
    periods = {p: {"startDate": account_plan["periods"][p]["startDate"],
        "endDate": account_plan["periods"][p]["endDate"],
        "policyConfigs": {"trail10": config_for(account_plan["periods"][p]["policyConfigs"]["trail10"])}} for p in PERIODS}
    protocol = read(PROTOCOL)
    require(protocol["candidateIds"] == list(POLICIES) and protocol["exits"] == list(EXITS) and
            protocol["trialAccounting"]["newAccounts"] == 18, "Protocol matrix")
    auditor, summary = module(VERIFIER), module(SUMMARY)
    seed = ["docs/leader-persistence-admission-v1.json", "docs/fixed-signal-account-contract-v1.json", "service/research_leader_persistence_admission.py",
        "tests/test_research_leader_persistence_admission.py", "scripts/run-leader-persistence-admission.py",
        "scripts/execute-leader-persistence-admission.py", VERIFIER, "tests/test_leader_persistence_admission_verifier.py",
        SUMMARY, "tests/test_leader_persistence_admission_readout.py", HELPER,
        "scripts/render-leader-persistence-admission.py", "scripts/verify-leader-persistence-admission-preview.cjs",
        "scripts/finalize-leader-persistence-admission.py",
        "scripts/run-online-feedback-entry.py", "scripts/summarize-online-feedback-entry.py",
        "scripts/summarize-undercut-reclaim-timing.py", "service/research_fixed_signal_account.py",
        "service/research_account_fees.py", "service/research_account_outcomes.py",
        "tests/test_research_fixed_signal_account.py", "tests/test_research_account_fees.py",
        "tests/test_research_account_outcomes.py"]
    dependencies = {**auditor.DEPENDENCIES, **getattr(summary, "DEPENDENCIES", {})}
    seed += list(dependencies)
    sources_pins = {name: sha(ROOT / name) for name in closure(seed)}
    for name, expected in dependencies.items(): require(sources_pins[name] == expected, "Frozen helper drift: " + name)
    for name in sources_pins.keys() & account_plan["sourcePins"].keys():
        require(sources_pins[name] == account_plan["sourcePins"][name], "Inherited code drift: " + name)
    plan = {"schema": OUT.name, "registeredAt": now(), "previousProgram": ref(BASE / "program.json"),
        **prior_refs, "parentData": parent_data, "sourcePlan": source_plan, "sourceData": sources,
        "periods": periods, "boards": list(BOARDS), "policies": list(POLICIES), "exits": list(EXITS),
        "strategyId": SID, "accountContract": contract, "feePolicy": fee, "atlas": pin(account_plan["atlas"]),
        "protocol": ref(PROTOCOL), "rules": protocol, "sourcePins": sources_pins, "inputPins": inputs,
        **protocol["trialAccounting"], "previousTurnClassification": "progress",
        "parameterSearch": False, "holdoutClaim": False, "certified": False, "targetAchieved": False}
    for p in PERIODS: fee_policy(p, plan).settings(periods[p]["policyConfigs"]["trail10"])
    OUT.mkdir(exist_ok=False)
    write(OUT / "plan.json", plan)
    with (OUT / "program-before.json").open("xb") as f: f.write((BASE / "program.json").read_bytes())
    for name in sources_pins:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as f: f.write((ROOT / name).read_bytes())
    return {"registered": True, "plan": ref(OUT / "plan.json"), "newAccountsPlanned": 18}

def pretests():
    plan = verify_plan()
    require(not (OUT / "input-completion.json").exists() and not (OUT / "run-start.json").exists(), "Tests must precede preparation")
    started, commands = now(), []
    for name in ("test_research_leader_persistence_admission.py", "test_leader_persistence_admission_verifier.py",
                 "test_leader_persistence_admission_readout.py", "test_research_fixed_signal_account.py",
                 "test_research_account_fees.py", "test_research_account_outcomes.py"):
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        commands.append({"argv": argv, "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            write(OUT / "pre-account-tests-failure.json", {"failedAt": now(), "commands": commands})
            raise ValueError("Pre-account test failed: " + name)
    verify_plan()
    receipt = {"passed": True, "startedAt": started, "completedAt": now(), "planSha256": sha(OUT / "plan.json"),
        "sourcePins": plan["sourcePins"], "commands": commands,
        "testCounts": [int(re.search(r"Ran (\d+) tests?", r["stderr"]).group(1)) for r in commands],
        "allChecksBeforeFirstNewAccount": True}
    write(OUT / "pre-account-tests.json", receipt)
    return {"passed": True, "testCounts": receipt["testCounts"]}

def write_lines(path, rows):
    import json
    with gzip.open(path, "xt", encoding="utf-8", compresslevel=1) as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")

def prepare():
    from service.research_leader_persistence_admission import build_leader_persistence_admission
    plan = verify_plan(); tests = read(OUT / "pre-account-tests.json")
    require(tests["passed"] and tests["planSha256"] == sha(OUT / "plan.json") and tests["sourcePins"] == plan["sourcePins"], "Pretests")
    require(not (OUT / "run-start.json").exists() and not (OUT / "input-completion.json").exists(), "Preparation already started/completed")
    require(atomic_once(OUT / "prepare-start.json", {"startedAt": now(), "planSha256": sha(OUT / "plan.json"),
        "preAccountTestsSha256": sha(OUT / "pre-account-tests.json")}), "Do not retry partial preparation")
    groups = []
    for p in PERIODS:
        for b in BOARDS:
            source, parent = plan["sourceData"][p][b], plan["parentData"][p][b]
            data = read(bound(source["accountData"]))
            require(digest(data) == source["accountDatasetDigest"], "Dataset digest")
            metadata = read(bound(parent["metadata"]))
            metadata["symbols"] = sorted(s["symbol"] for s in data["securities"])
            daily = OLD.records(bound(parent["daily"]))
            folder = OUT / p / b; folder.mkdir(parents=True, exist_ok=False)
            evidence = build_leader_persistence_admission(metadata, daily,
                start=plan["periods"][p]["startDate"], end=plan["periods"][p]["endDate"])
            for key, name in (("dailyRows", "daily-evidence.jsonl.gz"), ("opportunities", "opportunities.jsonl.gz")):
                write_lines(folder / name, evidence[key])
            write(folder / "metadata.json", evidence["metadata"])
            for policy in POLICIES: write(folder / (policy + ".signals.json"), evidence["schedules"][policy])
            group = {"period": p, "board": b, "accountData": source["accountData"],
                "accountDatasetDigest": source["accountDatasetDigest"], "parentMetadata": parent["metadata"],
                "parentDaily": parent["daily"], "counts": evidence["counts"], "metadata": ref(folder / "metadata.json"),
                "daily": ref(folder / "daily-evidence.jsonl.gz"), "opportunities": ref(folder / "opportunities.jsonl.gz"),
                "schedules": {policy: ref(folder / (policy + ".signals.json")) for policy in POLICIES}}
            require(digest(data) == source["accountDatasetDigest"], "Builder mutated data")
            write(folder / "input-completion.json", {**group, "planSha256": sha(OUT / "plan.json"), "completedAt": now()})
            group["inputReceipt"] = ref(folder / "input-completion.json"); groups.append(group)
            emit({"prepared": [p, b], "counts": evidence["counts"]})
            del data, metadata, daily, evidence; gc.collect()
    verify_plan()
    receipt = {"schema": OUT.name + "-inputs", "completedAt": now(), "planSha256": sha(OUT / "plan.json"),
        "preAccountTestsSha256": sha(OUT / "pre-account-tests.json"), "groups": groups,
        "allSchedulesFrozenBeforeAccounts": True, "newAccountsExecuted": 0,
        "counts": {"sourceGroups": 6, "preparedSchedules": 18, "accountScheduleBindings": 18,
            "opportunities": sum(g["counts"]["top5OpportunityCount"] for g in groups),
            "signals": {p: sum(g["counts"]["signals"][p] for g in groups) for p in POLICIES}}}
    write(OUT / "input-completion.json", receipt)
    return {"prepared": True, "counts": receipt["counts"]}

def prerequisites():
    plan = verify_plan()
    inputs, proof, tests = [read(OUT / name) for name in ("input-completion.json", "input-validation.json", "pre-account-tests.json")]
    hashes = {key: sha(OUT / name) for key, name in (("plan", "plan.json"), ("completion", "input-completion.json"),
        ("validation", "input-validation.json"), ("tests", "pre-account-tests.json"))}
    require(proof["passed"] and proof["planSha256"] == hashes["plan"] and
        proof["inputCompletionSha256"] == hashes["completion"] and proof["preAccountTestsSha256"] == hashes["tests"]
        and proof["verifierSha256"] == sha(ROOT / VERIFIER), "Independent input audit changed")
    require(tests["passed"] and tests["sourcePins"] == plan["sourcePins"] and tests["planSha256"] == hashes["plan"]
        and inputs["preAccountTestsSha256"] == hashes["tests"] and inputs["planSha256"] == hashes["plan"]
        and inputs["allSchedulesFrozenBeforeAccounts"] and inputs["newAccountsExecuted"] == 0, "Schedule chronology")
    require(datetime.fromisoformat(tests["completedAt"]) <= datetime.fromisoformat(inputs["completedAt"]), "Tests after inputs")
    require(len(inputs["groups"]) == 6 and inputs["counts"]["preparedSchedules"] == 18 and
        {(g["period"], g["board"]) for g in inputs["groups"]} == {(p,b) for p in PERIODS for b in BOARDS}, "Six groups")
    for g in inputs["groups"]:
        require(g["accountData"] == plan["sourceData"][g["period"]][g["board"]]["accountData"]
            and set(g["schedules"]) == set(POLICIES), "Prepared group identity")
        for key in ("metadata", "daily", "opportunities", "inputReceipt"): bound(g[key])
        for item in g["schedules"].values(): bound(item)
    return plan, inputs, hashes

def checked_terminal(receipt, group, hashes, start_sha):
    require(receipt["completed"] and receipt["planSha256"] == hashes["plan"] and receipt["runStartSha256"] == start_sha
        and receipt["inputCompletionSha256"] == hashes["completion"] and receipt["inputValidationSha256"] == hashes["validation"]
        and receipt["preAccountTestsSha256"] == hashes["tests"] and receipt["exit"] == "trail10"
        and (receipt["period"], receipt["board"]) == (group["period"], group["board"]), "Terminal binding")
    require(len(receipt["rows"]) == 3 and {r["policy"] for r in receipt["rows"]} == set(POLICIES), "Three accounts")
    for r in receipt["rows"]:
        require((r["period"],r["board"],r["exit"]) == (group["period"],group["board"],"trail10")
            and r["schedule"] == group["schedules"][r["policy"]] and r["reusedAccount"] is False, "Account binding")
        for k in ("result", "checkpoint", "summary", "schedule"): bound(r[k])
    return receipt["rows"]

def run(group_name):
    from service.research_account_outcomes import summarize_account
    require(group_name in {p + "/" + b for p in PERIODS for b in BOARDS}, "Unknown group")
    plan, inputs, hashes = prerequisites()
    group = next(g for g in inputs["groups"] if g["period"] + "/" + g["board"] == group_name)
    start_refs = {"planSha256": hashes["plan"], "inputCompletion": ref(OUT / "input-completion.json"),
        "inputValidation": ref(OUT / "input-validation.json"), "tests": ref(OUT / "pre-account-tests.json")}
    atomic_once(OUT / "run-start.json", {"startedAt": now(), **start_refs})
    start = read(OUT / "run-start.json")
    require(all(start[k] == v for k,v in start_refs.items()), "Run-start changed")
    require(datetime.fromisoformat(inputs["completedAt"]) <= datetime.fromisoformat(start["startedAt"]), "Inputs follow run")
    run_sha = sha(OUT / "run-start.json"); p,b = group["period"],group["board"]
    folder = OUT / p / b / "trail10"; folder.mkdir(exist_ok=True)
    terminal = folder / "group-completion.json"
    if terminal.exists():
        checked_terminal(read(terminal), group, hashes, run_sha)
        return {"completed": True, "alreadyComplete": True, "group": group_name}
    OLD.require_unstarted_group(folder)
    require(atomic_once(folder / "group-run-start.json", {"startedAt": now(), "planSha256": hashes["plan"],
        "runStartSha256": run_sha}), "Group already claimed")
    data, atlas = read(bound(group["accountData"])), read(bound(plan["atlas"]))
    require(digest(data) == group["accountDatasetDigest"], "Dataset changed")
    rows = []
    for policy in POLICIES:
        schedule = read(bound(group["schedules"][policy]))
        emit({"accountStart": [p,b,"trail10",policy], "signals": len(schedule)})
        result, checkpoint = execute_account(data, schedule, plan["periods"][p]["policyConfigs"]["trail10"], "trail10", fee_policy(p,plan))
        write(folder / (policy + ".result.json.gz"), result)
        write(folder / (policy + ".checkpoint.json.gz"), checkpoint)
        summary = summarize_account(result, checkpoint, policy_id=policy, policy_label=policy, intervals=intervals(p,atlas))
        require(not summary["quality"]["integrityIssues"], "Account integrity error")
        write(folder / (policy + ".summary.json"), summary)
        rows.append({"period": p, "board": b, "exit": "trail10", "policy": policy, "candidate": policy,
            "schedule": group["schedules"][policy], "result": ref(folder / (policy + ".result.json.gz")),
            "checkpoint": ref(folder / (policy + ".checkpoint.json.gz")), "summary": ref(folder / (policy + ".summary.json")),
            "reusedAccount": False})
        emit({"accountComplete": [p,b,"trail10",policy], "returnPct": summary["account"]["netReturnPct"],
            "drawdownPct": summary["account"]["maxDrawdownPct"], "closed": summary["closedEpisodes"]["count"]})
        del result, checkpoint, summary; gc.collect()
    require(digest(data) == group["accountDatasetDigest"], "Account mutated data")
    receipt = {"period": p, "board": b, "exit": "trail10", "completed": True, "completedAt": now(),
        "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
        "inputValidationSha256": hashes["validation"], "preAccountTestsSha256": hashes["tests"],
        "runStartSha256": run_sha, "rows": rows, "newAccounts": 3, "referenceAccountsReused": 0}
    require(atomic_once(terminal, receipt), "Duplicate terminal")
    return {"completed": True, "group": group_name, "accounts": 3}

def collect():
    plan, inputs, hashes = prerequisites(); run_sha = sha(OUT / "run-start.json")
    start = read(OUT / "run-start.json")
    require(start["planSha256"] == hashes["plan"] and start["inputCompletion"] == ref(OUT / "input-completion.json")
        and start["inputValidation"] == ref(OUT / "input-validation.json") and start["tests"] == ref(OUT / "pre-account-tests.json"),
        "Run start drift")
    rows, receipts = [], []
    for group in inputs["groups"]:
        path = OUT / group["period"] / group["board"] / "trail10/group-completion.json"
        require(path.exists(), "Incomplete group: " + str(path))
        rows += checked_terminal(read(path), group, hashes, run_sha); receipts.append(ref(path))
    require(len(rows) == 18, "All18 accounts required")
    value = {"schema": OUT.name + "-comparison", "completedAt": now(), "planSha256": hashes["plan"],
        "inputCompletionSha256": hashes["completion"], "inputValidationSha256": hashes["validation"],
        "preAccountTestsSha256": hashes["tests"], "runStartSha256": run_sha, "rows": rows, "groupCompletions": receipts,
        **plan["rules"]["trialAccounting"], "holdoutClaim": False, "certified": False, "targetAchieved": False}
    write(OUT / "comparison.json", value)
    return {"completed": True, "accounts": 18, "comparison": ref(OUT / "comparison.json")}

if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("command", choices=("register","tests","prepare","run","collect"))
    parser.add_argument("--group")
    args = parser.parse_args()
    emit(run(args.group) if args.command == "run" else {"register":register,"tests":pretests,"prepare":prepare,"collect":collect}[args.command]())

