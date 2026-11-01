"""Seal a completed prediction diagnostic and advance the local batch ledger."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "leader-incremental-prediction-v1"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def ref(path):
    return {"path": Path(path).relative_to(ROOT).as_posix(), "sha256": sha(path)}


def check_ref(item):
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed file: " + item["path"])
    return path


def write_once(path, value):
    if path.exists():
        require(read(path) == value, "Existing artifact differs")
        return
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def publish(delivery):
    for item in delivery["files"]:
        require(check_ref(item).is_relative_to(OUT), "Sealed file outside batch")
    target = BASE / "program.json"
    if sha(target) == delivery["programAfterSha256"]:
        return
    require(sha(target) == delivery["programBeforeSha256"], "Registry changed")
    temporary = BASE / ("prediction-program-" + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write((OUT / "program-after.json").read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    require(sha(target) == delivery["programAfterSha256"], "Registry replacement mismatch")


def main():
    plan = read(OUT / "plan.json")
    for key in ("previousDelivery", "parentDelivery"):
        path = check_ref(plan[key])
        for item in read(path)["files"]:
            require(check_ref(item).is_relative_to(path.parent), "Previous member outside family")
    delivery_path = OUT / "delivery.json"
    if delivery_path.exists():
        publish(read(delivery_path))
        print(json.dumps({"passed": True, "alreadyFinalized": True, "delivery": ref(delivery_path)}))
        return
    for field in ("sourcePins", "inputPins"):
        for name, value in plan[field].items():
            check_ref({"path": name, "sha256": value})
    plan_sha = sha(OUT / "plan.json")
    tests, start, completion, validation, readout_proof = [read(OUT / name) for name in
        ("pre-execution-tests.json", "run-start.json", "completion.json", "validation.json", "readout-validation.json")]
    for proof in (tests, completion, validation, readout_proof):
        require(proof["passed"] is True and proof["planSha256"] == plan_sha, "Missing exact passing proof")
    require(start["planSha256"] == plan_sha and
            start["preExecutionTestsSha256"] == sha(OUT / "pre-execution-tests.json"), "Stale run start")
    require(completion["runStartSha256"] == sha(OUT / "run-start.json") and
            completion["preExecutionTestsSha256"] == sha(OUT / "pre-execution-tests.json"), "Stale completion")
    require(validation["completionSha256"] == sha(OUT / "completion.json") and
            validation["verifierSha256"] == sha(ROOT / "scripts/verify-leader-incremental-prediction.py"), "Stale independent audit")
    require(readout_proof["readoutSha256"] == sha(OUT / "readout.json") and
            readout_proof["completionSha256"] == sha(OUT / "completion.json") and
            readout_proof["sourceValidationSha256"] == sha(OUT / "validation.json") and
            readout_proof["verifierSha256"] == sha(ROOT / "scripts/summarize-leader-incremental-prediction.py"), "Stale readout audit")
    require(datetime.fromisoformat(plan["registeredAt"]) < datetime.fromisoformat(tests["completedAt"])
            < datetime.fromisoformat(start["startedAt"]), "Execution precedes frozen registration/tests")
    expected = {(p, b) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")}
    require(len(completion["groups"]) == 6 and
            {(g["period"], g["board"]) for g in completion["groups"]} == expected
            and completion["newAccounts"] == completion["reusedAccounts"] == 0, "Full diagnostic scope required")
    for group in completion["groups"]:
        for key in ("metadata", "formations", "fits", "rows"):
            check_ref(group[key])
    report = read(OUT / "readout.json")
    for name, count in (("primaryRows", 6), ("yearRows", 12), ("halfYearRows", 24), ("phaseRows", 48), ("epochRows", 6), ("coverageRows", 6)):
        require(len(report[name]) == count, "Missing complete readout table: " + name)
    check_ref(report["eventRowsArtifact"])
    render = read(OUT / "render-receipt.json")
    require(render["builderSha256"] == sha(ROOT / "scripts/render-leader-incremental-prediction.py"), "Renderer changed")
    for name, expected_sha in render["inputPins"].items():
        require(sha(OUT / name) == expected_sha, "Render uses old analysis")
    for helper in render["presentationHelpers"]:
        require(sha(helper["path"]) == helper["sha256"], "Presentation helper changed")
    for items in (render["payloads"], render["fragments"]):
        for item in items.values():
            require(sha(OUT / item["path"]) == item["sha256"], "Rendered output changed")
    review = read(OUT / "visual-review.json")
    require(review["passed"] and review["actualImagesViewed"] and
            review["renderReceiptSha256"] == sha(OUT / "render-receipt.json"), "Actual visual review required")
    qa = read(check_ref(review["qa"]))
    require(qa["passed"] and not qa["errors"] and qa["images"] == review["images"] and
            qa["renderReceiptSha256"] == review["renderReceiptSha256"] and
            qa["scriptSha256"] == sha(ROOT / "scripts/verify-leader-incremental-prediction-preview-v2.cjs") and
            qa["originalScriptSha256"] == sha(ROOT / "scripts/verify-leader-incremental-prediction-preview.cjs"), "Browser QA stale")
    for item in review["images"]:
        check_ref(item)
    decision = read(OUT / "decision.json")
    require(decision["targetAchieved"] is False and decision["certified"] is False and
            decision["newAccounts"] == 0 and decision["nextStage"], "Explicit provisional diagnostic decision required")
    before = OUT / "program-before.json"
    require(sha(before) == plan["previousProgram"]["sha256"], "Registry snapshot differs")
    program = read(before)
    require(program["progress"]["completedFixedCandidateTrials"] == 45 and program["goalStatus"] == "active", "Unexpected prior progress")
    program["progress"]["completedFixedCandidateTrials"] = 46
    program["progress"]["leaderIncrementalPrediction"] = {
        "directory": OUT.relative_to(ROOT).as_posix(), "status": "completed-development-prediction-diagnostic",
        "completedResearchBatchesAdded": 1, "newTradingCandidates": 0, "newAccounts": 0, "reusedAccounts": 0,
        "countingNote": "The legacy completedFixedCandidateTrials counter counts research batches. This is explicitly a conditional prediction diagnostic, not a tested trading strategy.",
        "deliveryPath": delivery_path.relative_to(ROOT).as_posix(),
        **{key: ref(OUT / filename) for key, filename in (("plan", "plan.json"), ("tests", "pre-execution-tests.json"),
            ("completion", "completion.json"), ("validation", "validation.json"), ("readout", "readout.json"),
            ("readoutValidation", "readout-validation.json"), ("decision", "decision.json"),
            ("renderReceipt", "render-receipt.json"), ("visualReview", "visual-review.json"))},
        "targetAchieved": False, "certified": False}
    program["progress"]["lastTurnClassification"] = "new-completed-incremental-leader-prediction-diagnostic-with-independent-reconstruction"
    program["progress"]["nextStage"] = decision["nextStage"]
    program["targetAchieved"] = False
    program["updatedAt"] = decision["completedAt"]
    program["updatedDate"] = datetime.fromisoformat(decision["completedAt"]).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    write_once(OUT / "program-after.json", program)
    for name in plan["sourcePins"]:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == sha(ROOT / name), "Frozen-source archive differs")
        else:
            with target.open("xb") as stream:
                stream.write((ROOT / name).read_bytes())
    repair_sources = (Path(__file__), ROOT / "scripts/verify-leader-incremental-prediction-preview-v2.cjs")
    for source in repair_sources:
        target = OUT / "presentation-repair-sources" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == sha(source), "Actual presentation repair source changed")
        else:
            with target.open("xb") as stream:
                stream.write(source.read_bytes())
    write_once(OUT / "finalization-repair.json", {
        "passed": True, "originalFinalizer": ref(ROOT / "scripts/finalize-leader-incremental-prediction.py"),
        "actualFinalizer": ref(Path(__file__)),
        "originalQA": ref(ROOT / "scripts/verify-leader-incremental-prediction-preview.cjs"),
        "actualQA": ref(ROOT / "scripts/verify-leader-incremental-prediction-preview-v2.cjs"),
        "scope": "Only accept explicitly bound QA v2 with original-script identity and archive both actual repair sources. All original research/finalization checks remain; no model, output or trading change.",
        "reason": "Original tooltip QA parsed Chinese compact text as Number, causing NaN. QA v2 checks the actual formatter output against every source value; failed original attempt is preserved.",
        "researchReruns": 0, "newAccounts": 0})
    files = [ref(path) for path in sorted(OUT.rglob("*")) if path.is_file() and path != delivery_path]
    delivery = {"schema": "leader-incremental-prediction-delivery-v1", "completedAt": decision["completedAt"],
                "programBeforeSha256": sha(before), "programAfterSha256": sha(OUT / "program-after.json"),
                "candidateTrialsAfter": 46, "newAccounts": 0, "diagnosticOnly": True,
                "targetAchieved": False, "certified": False, "files": files}
    write_once(delivery_path, delivery)
    publish(delivery)
    print(json.dumps({"passed": True, "delivery": ref(delivery_path), "members": len(files),
                      "programSha256": sha(BASE / "program.json"), "newAccounts": 0, "targetAchieved": False}))


if __name__ == "__main__":
    main()
