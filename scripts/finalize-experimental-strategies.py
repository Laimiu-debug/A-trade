"""Close the user-stopped research and bind the reviewed application delivery."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/final-research-review-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ref(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path)}


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def main():
    if (OUT / "final-closeout.json").exists():
        raise ValueError("Final closeout already exists; do not replace delivered evidence")
    program_path = ROOT / "artifacts/strategy-search/program.json"
    program = read(program_path)
    assert program["progress"]["completedFixedCandidateTrials"] == 49
    frozen = ROOT / "artifacts/strategy-search/volume-lead-breakout-v1/delivery.json"
    delivery = read(frozen)
    for member in delivery["files"]:
        assert digest(ROOT / member["path"]) == member["sha256"], member["path"]
    for path, sha in delivery["sourcePins"].items():
        assert digest(ROOT / path) == sha, path
    assert delivery["candidateTrialsAfter"] == 49 and delivery["targetAchieved"] is False
    source_files = ["service/experimental_strategies.py", "service/experimental_strategy_service.py",
        "service/experimental_scan.py", "service/experimental_scan_service.py", "service/entry.py",
        "desktop/business-service.cjs", "preview/index.html", "preview/app.js", "preview/data.js",
        "preview/experimental-strategies.js", "preview/experimental-strategies.css", "docs/experimental-strategies-v1.json",
        "tests/test_experimental_strategies.py", "tests/test_experimental_scan.py",
        "tests/experimental-pipe.test.cjs", "tests/experimental-strategies.test.cjs",
        "scripts/validate-experimental-current-scan.py", "scripts/finalize-experimental-strategies.py"]
    tests = []
    commands = [
        ("python", [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_experimental*.py", "-v"]),
        ("node", [NODE, "--test", "tests/experimental-pipe.test.cjs", "tests/experimental-strategies.test.cjs"])]
    for name, command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        log = OUT / ("final-" + name + "-tests.log")
        with log.open("x", encoding="utf-8") as stream:
            stream.write(result.stdout + result.stderr)
        tests.append({"command": command, "exitCode": result.returncode, "log": ref(log)})
        assert result.returncode == 0, str(log)
    current = OUT / "current-scan-validation-0002/verification.json"
    current_proof = read(current)
    assert current_proof["passed"] and current_proof["existingRootDataUnchanged"]
    for path, sha in current_proof["sourceCodePins"].items():
        assert digest(ROOT / path) == sha, path
    app_path = ROOT / "artifacts/experimental-strategies-app-ui/attempt-0005/app-ui-verification.json"
    app = read(app_path)
    assert app["passed"] and app["newScans"] == 0 and app["newAccounts"] == 0
    assert app["protectedBefore"] == app["protectedAfter"]
    for member in app["inputPins"]:
        assert digest(member["path"]) == member["sha256"], member["path"]
    for member in app["sourcePins"]:
        assert digest(ROOT / member["path"]) == member["sha256"], member["path"]
    overview = read(OUT / "research-overview.json")
    assert overview["registeredCandidateTrials"] == 49 and overview["progressIndexEntries"] == 92
    for member in [overview["program"], *overview["sourceReviews"]]:
        assert digest(ROOT / member["path"]) == member["sha256"], member["path"]
    native_visual = OUT / "preview-qa-0005/visual-review.json"
    assert read(native_visual)["passed"]
    now = datetime.now(timezone.utc).isoformat()
    validation = {"passed": True, "completedAt": now, "tests": tests,
        "code": [ref(p) for p in source_files], "currentScan": ref(current), "fullApp": ref(app_path),
        "originalBatch49SealedMembersVerified": len(delivery["files"]), "originalBatch49": ref(frozen),
        "rootVisualReview": {"viewedActualImages": [ref(ROOT / "artifacts/experimental-strategies-app-ui/attempt-0005" / n)
            for n in ("01-current-environment-native.png", "02-candidate-evidence-native.png", "03-real-research-and-rules-native.png")],
            "finding": "Environment/candidate views readable. Third viewport shows current panel, not a separate visual certification of the rule table; rule table completeness checked by application DOM test."},
        "nativeChartReview": ref(native_visual), "newFinancialBacktestsAfterBatch49": 0,
        "currentScanExecutions": 2, "meaning": "Two current-screen implementation validations; not financial account backtests or independent profitability evidence."}
    write(OUT / "application-validation.json", validation)
    write(OUT / "program-before-closeout.json", program)
    program.update(goalStatus="paused", updatedDate="2026-10-17", updatedAt=now, targetAchieved=False)
    program["latestUserIntent"] += [
        "Stop further research after completing batch49, summarize all research and retain three executable experimental strategies in A-trade.",
        "The primary delivered workflow must analyze the available current market environment and identify stocks meeting the fixed rules; historical replay is secondary."]
    program["progress"]["lastTurnClassification"] = "user-requested-research-closeout-and-three-experimental-strategies-delivered"
    program["progress"]["nextStage"] = "Paused at the explicit user request. No new research batch or parameter search. The three experimental strategies are available for user-initiated current screening and frozen historical replay. No stable-profit success claim."
    program["workBoundaries"]["userWorkspace"] = "Existing root account, market, research and state files preserved. User-requested current-screen results stored only in new experimental-scans task folders; future opt-in frozen replays use experimental-strategies folders."
    program["researchCloseout"] = {"date": "2026-10-17", "reason": "explicit-user-request-to-stop-after-current-verification",
        "registeredCandidateTrials": 49, "progressIndexEntries": 92, "newResearchBatchesAfter49": 0,
        "newFinancialBacktestsAfter49": 0, "targetAchieved": False,
        "summary": ref("docs/strategy-research-summary-20261017.md"),
        "overview": ref(OUT / "research-overview.json"), "strategyCatalogue": ref("docs/experimental-strategies-v1.json"),
        "applicationValidation": ref(OUT / "application-validation.json"),
        "entry": "策略与回测 → 实验策略 → 分析当前环境与选股", "packageBuilt": False}
    # Only the live coordination record changes; all research snapshots stay sealed.
    program_path.write_text(json.dumps(program, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    write(OUT / "program-after-closeout.json", program)
    closeout = {"schema": "user-requested-research-closeout-v1", "completedAt": now,
        "goalStatus": "paused", "targetAchieved": False, "researchClosedAfterBatch": 49,
        "currentScreeningAvailable": True, "automaticExecution": False, "packageBuilt": False,
        "programBefore": ref(OUT / "program-before-closeout.json"), "programAfter": ref(OUT / "program-after-closeout.json"),
        "summary": ref("docs/strategy-research-summary-20261017.md"), "overview": ref(OUT / "research-overview.json"),
        "applicationValidation": ref(OUT / "application-validation.json"),
        "selectedChart": ref(OUT / "selected-chart.html"), "selectedSources": ref(OUT / "selected-sources.html"),
        "originalBatch49": ref(frozen), "currentResult": ref(OUT / "current-scan-validation-0002/result.json"),
        "note": "Source application integration and user-requested stopping completed; the original profit objective is not achieved or certified."}
    write(OUT / "final-closeout.json", closeout)
    print(json.dumps({"passed": True, "goalStatus": "paused", "researchClosedAfterBatch":49,
        "sealedMembers":len(delivery["files"]), "result":str(OUT / "final-closeout.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
