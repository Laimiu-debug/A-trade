"""Register and execute a bounded prediction diagnostic; never run an account."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search"
FAMILY = "leader-incremental-prediction-v1"
OUT = BASE / FAMILY
PREVIOUS = BASE / "leader-pullback-retention-v1"
PARENT = BASE / "leader-amount-handoff-v1"
PROGRAM_SHA = "4cf07f7f11cdce3a05e824ac7735df1b8b90157ac45f2555e2479969c8179368"
PREVIOUS_SHA = "b47250bfb6fcb7663c578d54e8f7bdeed264f16605ceadcb6ef568fd9270b3f6"
PARENT_SHA = "9e59cfff71905056f90e8f3785f271fa211d5cd03fd275b1b76dbf3aa51c217c"
SOURCES = (
    "docs/leader-incremental-prediction-v1.json",
    "service/research_leader_lag.py",
    "service/research_leader_incremental_prediction.py",
    "tests/test_research_leader_incremental_prediction.py",
    "scripts/run-leader-incremental-prediction.py",
    "scripts/verify-leader-incremental-prediction.py",
    "tests/test_leader_incremental_prediction_verifier.py",
    "scripts/summarize-leader-incremental-prediction.py",
    "tests/test_leader_incremental_prediction_readout.py",
    "scripts/render-leader-incremental-prediction.py",
    "scripts/verify-leader-incremental-prediction-preview.cjs",
    "scripts/finalize-leader-incremental-prediction.py",
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ref(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def bound(item):
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed input: " + item["path"])
    return path


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_lines(path, values):
    with gzip.open(path, "xt", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def verify_delivery(path, expected):
    require(sha(path) == expected, "Previous delivery changed")
    delivery = read(path)
    for item in delivery["files"]:
        require(bound(item).is_relative_to(path.parent), "Delivery member outside batch")
    return len(delivery["files"])


def frozen():
    plan = read(OUT / "plan.json")
    require(plan["schema"] == FAMILY and plan["newAccounts"] == 0, "Wrong diagnostic identity")
    for field in ("sourcePins", "inputPins"):
        for name, expected in plan[field].items():
            bound({"path": name, "sha256": expected})
    require(sha(BASE / "program.json") == PROGRAM_SHA, "Research registry changed during run")
    return plan


def register():
    require(not OUT.exists(), "Do not replace an existing diagnostic")
    require(sha(BASE / "program.json") == PROGRAM_SHA, "Unexpected registry")
    program = read(BASE / "program.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 45
            and program["goalStatus"] == "active" and program["targetAchieved"] is False, "Unexpected progress state")
    previous_members = verify_delivery(PREVIOUS / "delivery.json", PREVIOUS_SHA)
    parent_members = verify_delivery(PARENT / "delivery.json", PARENT_SHA)
    previous_plan = read(PREVIOUS / "plan.json")
    inputs = read(PREVIOUS / "input-completion.json")
    require(inputs["planSha256"] == sha(PREVIOUS / "plan.json"), "Prior input provenance")
    source_pins = {name: sha(ROOT / name) for name in SOURCES}
    input_pins = {}

    def pin(item):
        bound(item)
        input_pins[item["path"]] = item["sha256"]
        return item

    for path in (PREVIOUS / "plan.json", PREVIOUS / "input-completion.json", PREVIOUS / "validation.json",
                 PREVIOUS / "decision.json", PARENT / "plan.json", PARENT / "input-completion.json",
                 PARENT / "input-validation.json", PARENT / "validation.json"):
        pin(ref(path))
    groups = []
    for group in inputs["groups"]:
        period, board = group["period"], group["board"]
        first, last = period.split("-")
        groups.append({"period": period, "board": board, "start": first + "-01-01", "end": last + "-12-31",
                       "accountData": pin(group["accountData"]), "parentFormations": pin(group["parentFormations"])})
    require(len(groups) == 6 and {(g["period"], g["board"]) for g in groups} ==
            {(p, b) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")}, "Six complete cohorts required")
    plan = {"schema": FAMILY, "registeredAt": now(), "previousProgram": ref(BASE / "program.json"),
            "previousDelivery": pin(ref(PREVIOUS / "delivery.json")), "parentDelivery": pin(ref(PARENT / "delivery.json")),
            "protocol": ref(ROOT / SOURCES[0]), "atlas": pin(previous_plan["atlas"]),
            "sourcePins": source_pins, "inputPins": input_pins, "groups": groups,
            "previousTurnClassification": "verified-progress:45th completed batch,24 new accounts,239 sealed delivery members rechecked",
            "verifiedPriorDeliveryMembers": {"previous": previous_members, "parent": parent_members},
            "models": ["baseline", "augmented"], "zeroForecast": "descriptive common-support benchmark",
            "newAccounts": 0, "reusedAccounts": 0, "candidateTrialMeaning": "Prediction diagnostic only, not a trading-candidate replay",
            "holdoutClaim": False, "targetAchieved": False}
    OUT.mkdir()
    write(OUT / "plan.json", plan)
    with (OUT / "program-before.json").open("xb") as stream:
        stream.write((BASE / "program.json").read_bytes())
    print(json.dumps({"registered": ref(OUT / "plan.json"), "sourceFiles": len(source_pins), "newAccounts": 0}), flush=True)


def test_before_execution():
    frozen()
    require(not (OUT / "run-start.json").exists(), "Tests must precede new fits")
    test_files = ["test_research_leader_incremental_prediction.py", "test_leader_incremental_prediction_verifier.py",
                  "test_leader_incremental_prediction_readout.py"]
    checks = []
    for name in test_files:
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        checks.append({"argv": argv, "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    proof = {"passed": all(c["exitCode"] == 0 for c in checks), "completedAt": now(),
             "planSha256": sha(OUT / "plan.json"), "checks": checks, "realDataReads": 0, "accountExecutions": 0}
    write(OUT / "pre-execution-tests.json", proof)
    require(proof["passed"], "Pre-execution tests failed; retain receipt")
    frozen()
    print(json.dumps({"passed": True, "tests": ref(OUT / "pre-execution-tests.json")}), flush=True)


def execute():
    plan = frozen()
    tests = read(OUT / "pre-execution-tests.json")
    require(tests["passed"] and tests["planSha256"] == sha(OUT / "plan.json"), "Exact passing preregistered tests required")
    write(OUT / "run-start.json", {"startedAt": now(), "planSha256": sha(OUT / "plan.json"),
                                  "preExecutionTestsSha256": sha(OUT / "pre-execution-tests.json"), "newAccounts": 0})
    from service.research_leader_incremental_prediction import build_leader_incremental_prediction
    import numpy
    groups = []
    for source in plan["groups"]:
        period, board = source["period"], source["board"]
        directory = OUT / period / board
        directory.mkdir(parents=True, exist_ok=False)
        dataset_path = bound(source["accountData"])
        formation_path = bound(source["parentFormations"])
        dataset, formations = read(dataset_path), lines(formation_path)
        result = build_leader_incremental_prediction(dataset, formations, start=source["start"], end=source["end"])
        require(result["formations"] == formations, "All parent formation records must be preserved")
        formed = sum(f["status"] == "formed" for f in formations)
        require(len(result["fits"]) == formed * 20 and len(result["rows"]) == formed * 100, "Every planned member/forecast slot required")
        write(directory / "metadata.json", result["metadata"])
        for key, filename in (("formations", "formations.jsonl.gz"), ("fits", "fits.jsonl.gz"), ("rows", "predictions.jsonl.gz")):
            write_lines(directory / filename, result[key])
        require(sha(dataset_path) == source["accountData"]["sha256"] and sha(formation_path) == source["parentFormations"]["sha256"], "Source changed")
        record = {k: source[k] for k in ("period", "board", "start", "end")}
        record.update({"metadata": ref(directory / "metadata.json"), "formations": ref(directory / "formations.jsonl.gz"),
                       "fits": ref(directory / "fits.jsonl.gz"), "rows": ref(directory / "predictions.jsonl.gz"), "counts": result["counts"]})
        write(directory / "completion.json", record)
        groups.append(record)
        print(json.dumps({"completedGroup": [period, board], "counts": result["counts"]}, ensure_ascii=False), flush=True)
        del dataset, formations, result
        gc.collect()
    frozen()
    completion = {"passed": True, "completedAt": now(), "planSha256": sha(OUT / "plan.json"),
                  "preExecutionTestsSha256": sha(OUT / "pre-execution-tests.json"), "runStartSha256": sha(OUT / "run-start.json"),
                  "numpyVersion": numpy.__version__, "groups": groups, "newAccounts": 0, "reusedAccounts": 0,
                  "holdoutClaim": False, "targetAchieved": False}
    write(OUT / "completion.json", completion)
    print(json.dumps({"passed": True, "completion": ref(OUT / "completion.json"), "groups": len(groups), "newAccounts": 0}), flush=True)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "tests", "run"))
    args = parser.parse_args()
    try:
        {"register": register, "tests": test_before_execution, "run": execute}[args.mode]()
    except Exception as error:
        if OUT.exists():
            attempts = OUT / "execution-attempts"
            attempts.mkdir(exist_ok=True)
            write(attempts / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"),
                  {"passed": False, "mode": args.mode, "errorType": type(error).__name__, "message": str(error), "at": now()})
        raise
