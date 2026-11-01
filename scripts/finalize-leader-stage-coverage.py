"""Seal verified coverage evidence and advance the research-batch registry."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "leader-stage-coverage-v1"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def ref(path):
    return {"path": Path(path).resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed artifact: " + item["path"])
    return path


def write_once(path, value):
    if path.exists():
        require(read(path) == value, "Existing artifact differs: " + path.name)
        return
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def publish(delivery):
    for item in delivery["files"]:
        require(bound(item).is_relative_to(OUT), "Member outside batch")
    target = BASE / "program.json"
    if sha(target) == delivery["programAfterSha256"]:
        return
    require(sha(target) == delivery["programBeforeSha256"], "Registry changed")
    temporary = BASE / ("stage-program-" + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write((OUT / "program-after.json").read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    require(sha(target) == delivery["programAfterSha256"], "Registry replacement mismatch")


def main():
    plan = read(OUT / "plan.json")
    previous = bound(plan["previousDelivery"])
    for item in read(previous)["files"]:
        require(bound(item).is_relative_to(previous.parent), "Previous member outside family")
    delivery_path = OUT / "delivery.json"
    if delivery_path.exists():
        publish(read(delivery_path))
        print(json.dumps({"passed": True, "alreadyFinalized": True, "delivery": ref(delivery_path)}))
        return
    for key in ("sourcePins", "inputPins"):
        for name, value in plan[key].items():
            bound({"path": name, "sha256": value})
    plan_sha = sha(OUT / "plan.json")
    tests, start, completion, validation, proof = [read(OUT / name) for name in
        ("pre-execution-tests.json", "run-start.json", "completion.json", "validation.json", "readout-validation.json")]
    for value in (tests, completion, validation, proof):
        require(value["passed"] is True and value["planSha256"] == plan_sha, "Exact passing proof required")
    require(start["planSha256"] == plan_sha and start["preExecutionTestsSha256"] == sha(OUT / "pre-execution-tests.json")
            and completion["preExecutionTestsSha256"] == start["preExecutionTestsSha256"]
            and completion["runStartSha256"] == sha(OUT / "run-start.json"), "Stale run receipts")
    require(datetime.fromisoformat(plan["registeredAt"]) < datetime.fromisoformat(tests["completedAt"])
            < datetime.fromisoformat(start["startedAt"]), "Unregistered execution")
    require(validation["completionSha256"] == sha(OUT / "completion.json") and
            validation["verifierSha256"] == sha(ROOT / "scripts/verify-leader-stage-coverage.py"), "Stale independent audit")
    require(proof["readoutSha256"] == sha(OUT / "readout.json") and
            proof["completionSha256"] == sha(OUT / "completion.json") and
            proof["sourceValidationSha256"] == sha(OUT / "validation.json") and
            proof["verifierSha256"] == sha(ROOT / "scripts/summarize-leader-stage-coverage.py"), "Stale aggregation proof")
    require(len(plan["groups"]) == 6 and len(completion["focusGroups"]) == 3 and
            {g["board"] for g in completion["focusGroups"]} == {"main", "chinext", "star"}
            and completion["newAccounts"] == completion["reusedAccounts"] == 0, "Complete zero-account scope required")
    for group in completion["focusGroups"]:
        for key in ("metadata", "dailyRows", "symbolRows", "rankRows", "verification"):
            bound(group[key])
    report = read(OUT / "readout.json")
    for key, size in (("overallRows", 6), ("phaseRows", 48), ("epochRows", 6)):
        require(len(report[key]) == size, "Missing predeclared readout: " + key)
    bound(report["dailyRowsArtifact"])
    require(proof["dailyRowsArtifactSha256"] == report["dailyRowsArtifact"]["sha256"], "Stale daily aggregate proof")
    render = read(OUT / "render-receipt.json")
    require(render["passed"] and render["builderSha256"] == sha(ROOT / "scripts/render-leader-stage-coverage.py"), "Renderer changed")
    for name, value in render["inputPins"].items():
        require(sha(OUT / name) == value, "Render analysis changed")
    for item in (render["payload"], render["fragment"]):
        bound(item)
    for item in render["presentationHelpers"]:
        require(sha(item["path"]) == item["sha256"], "Presentation helper changed")
    review = read(OUT / "visual-review.json")
    qa = read(bound(review["qa"]))
    require(review["passed"] and review["actualImagesViewed"] and qa["passed"] and not qa["errors"]
            and review["images"] == qa["images"] and review["renderReceiptSha256"] == qa["renderReceiptSha256"]
            == sha(OUT / "render-receipt.json") and
            qa["scriptSha256"] == sha(ROOT / "scripts/verify-leader-stage-coverage-preview.cjs"), "Actual bound visual review required")
    for item in review["images"]:
        bound(item)
    decision = read(OUT / "decision.json")
    require(decision["targetAchieved"] is False and decision["certified"] is False and
            decision["newAccounts"] == 0 and decision["nextStage"], "Explicit provisional decision required")
    require(sha(OUT / "program-before.json") == plan["previousProgram"]["sha256"], "Original registry changed")
    program = read(OUT / "program-before.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 46 and program["goalStatus"] == "active", "Prior progress differs")
    program["progress"]["completedFixedCandidateTrials"] = 47
    program["progress"]["leaderStageCoverage"] = {
        "directory": OUT.relative_to(ROOT).as_posix(), "status": "completed-development-coverage-diagnostic",
        "completedResearchBatchesAdded": 1, "newTradingCandidates": 0, "newAccounts": 0, "reusedAccounts": 0,
        "countingNote": "Legacy completedFixedCandidateTrials counts research batches; this is not a tested trading candidate.",
        "deliveryPath": delivery_path.relative_to(ROOT).as_posix(),
        **{key: ref(OUT / name) for key, name in (("plan", "plan.json"), ("completion", "completion.json"),
          ("validation", "validation.json"), ("readout", "readout.json"), ("readoutValidation", "readout-validation.json"),
          ("decision", "decision.json"), ("renderReceipt", "render-receipt.json"), ("visualReview", "visual-review.json"))},
        "targetAchieved": False, "certified": False}
    program["progress"]["lastTurnClassification"] = "new-completed-leader-coverage-diagnostic-with-independent-raw-reconstruction"
    program["progress"]["nextStage"] = decision["nextStage"]
    program["updatedAt"] = decision["completedAt"]
    program["updatedDate"] = datetime.fromisoformat(decision["completedAt"]).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    program["targetAchieved"] = False
    write_once(OUT / "program-after.json", program)
    for name in plan["sourcePins"]:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == sha(ROOT / name), "Frozen source changed")
        else:
            with target.open("xb") as stream:
                stream.write((ROOT / name).read_bytes())
    delivery = {"schema": "leader-stage-coverage-delivery-v1", "completedAt": decision["completedAt"],
        "programBeforeSha256": sha(OUT / "program-before.json"), "programAfterSha256": sha(OUT / "program-after.json"),
        "candidateTrialsAfter": 47, "newAccounts": 0, "diagnosticOnly": True, "certified": False, "targetAchieved": False,
        "files": [ref(p) for p in sorted(OUT.rglob("*")) if p.is_file() and p != delivery_path]}
    write_once(delivery_path, delivery)
    publish(delivery)
    print(json.dumps({"passed": True, "delivery": ref(delivery_path), "members": len(delivery["files"]),
                      "programSha256": sha(BASE / "program.json"), "newAccounts": 0, "targetAchieved": False}))


if __name__ == "__main__":
    main()
