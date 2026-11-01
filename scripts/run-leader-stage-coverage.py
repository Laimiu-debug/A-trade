"""Freeze and execute the zero-account leader coverage diagnostic."""
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
OUT = BASE / "leader-stage-coverage-v1"
PREVIOUS = BASE / "leader-incremental-prediction-v1"
LAG = BASE / "leader-lag-followers-v1"
HANDOFF = BASE / "leader-amount-handoff-v1"
ENV = BASE / "board-environment-context-v1"
PROGRAM_SHA = "a12ab1d1699ccc3a12dc31c6a4653b3efa7db4d50cfcb3fb891432fc68d65616"
DELIVERY_SHA = "648ab1f39befc8a690ae58a4e8e47ea3ff3e73dfdc44d4903f10e48eeb9028db"
SOURCES = (
    "docs/leader-stage-coverage-v1.json", "service/research_leader_lag.py",
    "scripts/verify-leader-lag-followers.py", "service/research_leader_stage_coverage.py",
    "tests/test_research_leader_stage_coverage.py", "scripts/run-leader-stage-coverage.py",
    "scripts/verify-leader-stage-coverage.py", "tests/test_leader_stage_coverage_verifier.py",
    "scripts/summarize-leader-stage-coverage.py", "tests/test_leader_stage_coverage_readout.py",
    "scripts/render-leader-stage-coverage.py", "scripts/verify-leader-stage-coverage-preview.cjs",
    "scripts/finalize-leader-stage-coverage.py",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ref(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed input: " + item["path"])
    return path


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_lines(path, rows):
    with gzip.open(path, "xt", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def frozen():
    plan = read(OUT / "plan.json")
    for key in ("sourcePins", "inputPins"):
        for name, value in plan[key].items():
            bound({"path": name, "sha256": value})
    require(sha(BASE / "program.json") == PROGRAM_SHA, "Registry changed during diagnostic")
    return plan


def register():
    require(not OUT.exists(), "Never replace an existing registered diagnostic")
    require(sha(BASE / "program.json") == PROGRAM_SHA and sha(PREVIOUS / "delivery.json") == DELIVERY_SHA,
            "Unexpected predecessor")
    program = read(BASE / "program.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 46 and
            program["goalStatus"] == "active" and program["targetAchieved"] is False, "Unexpected progress")
    previous = read(PREVIOUS / "delivery.json")
    for item in previous["files"]:
        require(bound(item).is_relative_to(PREVIOUS), "Previous sealed member outside family")
    sources = {name: sha(ROOT / name) for name in SOURCES}
    inputs = {}

    def pin(item):
        bound(item)
        inputs[item["path"]] = item["sha256"]
        return item

    for folder, names in (
        (PREVIOUS, ("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json", "decision.json", "delivery.json")),
        (LAG, ("plan.json", "input-completion.json", "input-validation.json")),
        (HANDOFF, ("plan.json", "input-completion.json", "input-validation.json", "delivery.json")),
        (ENV, ("plan.json", "validation.json")),
    ):
        for name in names:
            pin(ref(folder / name))
    lp, lc, lv = [read(LAG / n) for n in ("plan.json", "input-completion.json", "input-validation.json")]
    ep, ev = read(ENV / "plan.json"), read(ENV / "validation.json")
    require(lv["passed"] and lv["planSha256"] == sha(LAG / "plan.json") and
            lv["inputCompletionSha256"] == sha(LAG / "input-completion.json"), "Unverified parent evidence")
    require(ev["passed"] and ev["planSha256"] == sha(ENV / "plan.json"), "Unverified environment")
    groups = []
    prediction = read(PREVIOUS / "completion.json")
    handoff = read(HANDOFF / "input-completion.json")
    for pg in prediction["groups"]:
        period, board = pg["period"], pg["board"]
        parent = read(bound(pin(lc["outputs"][period][board])))
        hg = next(g for g in handoff["groups"] if (g["period"], g["board"]) == (period, board))
        raw = parent["accountData"]
        require(raw == hg["accountData"] == ep["sourceData"][period][board]["accountData"] ==
                lp["sourceData"][period][board]["accountData"], "Different source cohorts")
        item = {"period": period, "board": board, "start": pg["start"], "end": pg["end"], "accountData": pin(raw)}
        for key, filename in (("lagMetadata", "signal-metadata.json"), ("lagDaily", "daily-evidence.jsonl.gz"),
                              ("lagRanks", "rank-evidence.jsonl.gz"), ("lagEvents", "leader-events.jsonl.gz")):
            source = parent["files"][filename]
            require(source == lv["inputArtifacts"][period][board][filename], "Parent artifact not audited")
            item[key] = pin(source)
        require(item["lagEvents"] == hg["parentLeaderEvents"], "Handoff consumes different leaders")
        item.update(handoffFormations=pin(hg["formations"]), predictionRows=pin(pg["rows"]),
                    environmentDaily=pin(ev["artifacts"][period][board]["daily.jsonl.gz"]))
        groups.append(item)
    require({(g["period"], g["board"]) for g in groups} ==
            {(p, b) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")} and len(groups) == 6,
            "Six full cohorts required")
    atlas = pin(read(PREVIOUS / "plan.json")["atlas"])
    focus = next(p for p in read(bound(atlas))["phases"] if p["id"] == "2024-09-policy-rally")
    require((focus["startDate"], focus["endDate"]) == ("2024-09-24", "2024-10-08"), "Fixed focus differs")
    plan = {"schema": "leader-stage-coverage-v1", "registeredAt": now(),
            "previousProgram": ref(BASE / "program.json"), "previousDelivery": ref(PREVIOUS / "delivery.json"),
            "protocol": ref(ROOT / SOURCES[0]), "atlas": atlas, "sourcePins": sources, "inputPins": inputs,
            "groups": groups, "focus": {"phaseId": focus["id"], "start": focus["startDate"], "end": focus["endDate"]},
            "newAccounts": 0, "reusedAccounts": 0, "newTradingCandidates": 0,
            "previousTurnClassification": "progress:46th diagnostic completed;84 sealed members freshly checked; all6 fixed prediction specifications deteriorated and policy-launch coverage gap changed next action",
            "holdoutClaim": False, "targetAchieved": False}
    OUT.mkdir()
    write(OUT / "plan.json", plan)
    with (OUT / "program-before.json").open("xb") as stream:
        stream.write((BASE / "program.json").read_bytes())
    print(json.dumps({"registered": ref(OUT / "plan.json"), "sourceFiles": len(sources), "newAccounts": 0}), flush=True)


def tests():
    frozen()
    require(not (OUT / "run-start.json").exists(), "Tests must precede new extraction")
    checks = []
    for name in ("test_research_leader_stage_coverage.py", "test_leader_stage_coverage_verifier.py", "test_leader_stage_coverage_readout.py"):
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        checks.append({"argv": argv, "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    proof = {"passed": all(c["exitCode"] == 0 for c in checks), "completedAt": now(),
             "planSha256": sha(OUT / "plan.json"), "checks": checks, "realDataReads": 0, "accountExecutions": 0}
    write(OUT / "pre-execution-tests.json", proof)
    require(proof["passed"], "Synthetic tests failed; preserve receipt")
    frozen()
    print(json.dumps({"passed": True, "tests": ref(OUT / "pre-execution-tests.json")}), flush=True)


def execute():
    plan = frozen()
    proof = read(OUT / "pre-execution-tests.json")
    require(proof["passed"] and proof["planSha256"] == sha(OUT / "plan.json"), "Passing frozen tests required")
    write(OUT / "run-start.json", {"startedAt": now(), "planSha256": sha(OUT / "plan.json"),
          "preExecutionTestsSha256": sha(OUT / "pre-execution-tests.json"), "newAccounts": 0})
    from service.research_leader_stage_coverage import build_leader_stage_coverage
    groups = []
    for source in plan["groups"]:
        if source["period"] != "2024-2025":
            continue
        directory = OUT / source["period"] / source["board"]
        directory.mkdir(parents=True, exist_ok=False)
        dataset = read(bound(source["accountData"]))
        parent = lines(bound(source["lagDaily"]))
        result = build_leader_stage_coverage(dataset, parent, start=plan["focus"]["start"], end=plan["focus"]["end"])
        old_ranks = {r["date"]: r for r in lines(bound(source["lagRanks"]))}
        require(all(row == old_ranks[row["date"]] for row in result["rankRows"]), "Rank replay differs from frozen parent")
        item = {"period": source["period"], "board": source["board"], **{k: plan["focus"][k] for k in ("start", "end")},
                "counts": result["counts"]}
        for key in ("metadata", "verification"):
            write(directory / (key + ".json"), result[key])
            item[key] = ref(directory / (key + ".json"))
        for key in ("rankRows", "dailyRows", "symbolRows"):
            path = directory / (key + ".jsonl.gz")
            write_lines(path, result[key])
            item[key] = ref(path)
        write(directory / "completion.json", item)
        groups.append(item)
        print(json.dumps({"completedGroup": [source["period"], source["board"]], "counts": result["counts"]}), flush=True)
        del dataset, parent, result
        gc.collect()
    frozen()
    require(len(groups) == 3, "All three focal cohorts required")
    completion = {"schema": "leader-stage-coverage-completion-v1", "passed": True, "completedAt": now(),
        "planSha256": sha(OUT / "plan.json"), "preExecutionTestsSha256": sha(OUT / "pre-execution-tests.json"),
        "runStartSha256": sha(OUT / "run-start.json"), "focusGroups": groups,
        "newAccounts": 0, "reusedAccounts": 0, "holdoutClaim": False, "targetAchieved": False}
    write(OUT / "completion.json", completion)
    print(json.dumps({"passed": True, "completion": ref(OUT / "completion.json")}), flush=True)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "tests", "run"))
    args = parser.parse_args()
    try:
        {"register": register, "tests": tests, "run": execute}[args.mode]()
    except Exception as error:
        if OUT.exists():
            attempts = OUT / "execution-attempts"
            attempts.mkdir(exist_ok=True)
            write(attempts / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"),
                  {"passed": False, "mode": args.mode, "errorType": type(error).__name__, "message": str(error), "at": now()})
        raise
