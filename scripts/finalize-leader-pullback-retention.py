"""Publish one audited development batch to the local research registry only."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "leader-pullback-retention-v1"


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


def once(path, value):
    if path.exists():
        require(read(path) == value, "Existing evidence differs: " + str(path))
        return
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_files(delivery):
    for item in delivery["files"]:
        path = (ROOT / item["path"]).resolve()
        require(path.is_relative_to(OUT.resolve()) and sha(path) == item["sha256"], "Changed delivery member")


def verify_predecessor(plan):
    item = plan["previousDelivery"]
    path = (ROOT / item["path"]).resolve()
    require(path.parent == (BASE / "leader-matched-anchor-v1").resolve()
            and sha(path) == item["sha256"], "Previous delivery changed")
    for member in read(path)["files"]:
        target = (ROOT / member["path"]).resolve()
        require(target.is_relative_to(path.parent) and sha(target) == member["sha256"],
                "Previous batch member changed: " + member["path"])


def apply_registry(delivery):
    path = BASE / "program.json"
    if sha(path) == delivery["programAfterSha256"]:
        return
    require(sha(path) == delivery["programBeforeSha256"], "Registry changed during batch")
    temporary = BASE / ("leader-pullback-program-" + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write((OUT / "program-after.json").read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    require(sha(path) == delivery["programAfterSha256"], "Published registry mismatch")


def main():
    delivery_path = OUT / "delivery.json"
    plan = read(OUT / "plan.json")
    verify_predecessor(plan)
    if delivery_path.exists():
        delivery = read(delivery_path)
        verify_files(delivery)
        apply_registry(delivery)
        print(json.dumps({"passed": True, "alreadyFinalized": True, "delivery": ref(delivery_path)}))
        return
    for field in ("sourcePins", "inputPins"):
        for name, expected in plan[field].items():
            require(sha(ROOT / name) == expected, "Frozen input changed: " + name)
    comparison = read(OUT / "comparison.json")
    for key, filename in (("planSha256", "plan.json"), ("inputCompletionSha256", "input-completion.json"),
                          ("inputValidationSha256", "input-validation.json"), ("preAccountTestsSha256", "pre-account-tests.json"),
                          ("runStartSha256", "run-start.json")):
        require(comparison[key] == sha(OUT / filename), "Comparison dependency changed")
    run_start = read(OUT / "run-start.json")
    require(run_start["planSha256"] == sha(OUT / "plan.json"), "Run start plan changed")
    for key, filename in (("inputCompletion", "input-completion.json"), ("inputValidation", "input-validation.json"), ("tests", "pre-account-tests.json")):
        require(run_start[key] == ref(OUT / filename), "Run start dependency changed")
    expected = {(p,b,e,c) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")
                for e in ("trail10", "volatilityTrail") for c in ("control", "retained")}
    require(len(comparison["rows"]) == 24 and
            {(r["period"],r["board"],r["exit"],r["policy"]) for r in comparison["rows"]} == expected,
            "Complete preregistered matrix required")
    for row in comparison["rows"]:
        for field in ("schedule", "result", "checkpoint", "summary"):
            item = row[field]
            require(sha(ROOT / item["path"]) == item["sha256"], "Account artifact changed")
    for name in ("pre-account-tests.json", "input-validation.json", "validation.json", "readout-validation.json"):
        proof = read(OUT / name)
        require(proof["passed"] is True and proof["planSha256"] == sha(OUT / "plan.json"), "Missing/stale check: " + name)
    input_audit = read(OUT / "input-validation.json")
    for key, filename in (("inputCompletionSha256", "input-completion.json"), ("preAccountTestsSha256", "pre-account-tests.json")):
        require(input_audit[key] == sha(OUT / filename), "Stale input audit binding")
    require(input_audit["verifierSha256"] == sha(ROOT / "scripts/verify-leader-pullback-retention.py"), "Input verifier changed")
    account_audit = read(OUT / "validation.json")
    for key, filename in (("comparisonSha256", "comparison.json"), ("inputCompletionSha256", "input-completion.json"),
                          ("inputValidationSha256", "input-validation.json"), ("preAccountTestsSha256", "pre-account-tests.json")):
        require(account_audit[key] == sha(OUT / filename), "Stale account audit binding")
    audit = read(OUT / "readout-validation.json")
    require(audit["readoutSha256"] == sha(OUT / "readout.json") and
            audit["comparisonSha256"] == sha(OUT / "comparison.json"), "Stale readout audit")
    require(audit["accountValidationSha256"] == sha(OUT / "validation.json"), "Readout audit uses old account proof")
    require(audit["verifierSha256"] == sha(ROOT / "scripts/verify-leader-pullback-retention-readout.py"),
            "Readout verifier changed")
    visual = read(OUT / "visual-review.json")
    require(visual["passed"] is True and visual["actualImagesViewed"] is True, "Root visual review required")
    require(visual["renderReceiptSha256"] == sha(OUT / "render-receipt.json"), "Stale visualization review")
    render = read(OUT / "render-receipt.json")
    require(render["builderSha256"] == sha(ROOT / "scripts/render-leader-pullback-retention.py"), "Renderer changed")
    for helper in render["presentationHelpers"]:
        require(sha(helper["path"]) == helper["sha256"], "Presentation helper changed")
    for name, expected in render["inputPins"].items():
        require(sha(OUT / name) == expected, "Presentation uses stale analysis: " + name)
    for values in (render["payloads"], render["fragments"]):
        for item in values.values():
            require(sha(OUT / item["path"]) == item["sha256"], "Rendered artifact changed")
    require(sha(ROOT / render["contextSource"]["path"]) == render["contextSource"]["sha256"], "Market context changed")
    qa_ref = visual["qa"]
    require(sha(ROOT / qa_ref["path"]) == qa_ref["sha256"], "Browser receipt changed")
    qa = read(ROOT / qa_ref["path"])
    require(qa["scriptSha256"] == sha(ROOT / "scripts/verify-leader-pullback-retention-preview.cjs"), "Preview verifier changed")
    require(qa["passed"] is True and qa["errors"] == [] and qa["images"] == visual["images"] and
            qa["renderReceiptSha256"] == visual["renderReceiptSha256"], "Browser review mismatch")
    for item in visual["images"]:
        require(sha(ROOT / item["path"]) == item["sha256"], "Reviewed image changed")
    decision = read(OUT / "decision.json")
    require(decision["targetAchieved"] is False and decision["certified"] is False and decision["nextStage"],
            "Development finding and explicit next step required")
    previous = OUT / "program-before.json"
    require(sha(previous) == plan["previousProgram"]["sha256"], "Program snapshot changed")
    program = read(previous)
    require(program["progress"]["completedFixedCandidateTrials"] == 44 and program["goalStatus"] == "active",
            "Unexpected prior registry")
    program["progress"]["completedFixedCandidateTrials"] = 45
    program["progress"]["leaderPullbackRetention"] = {
        "directory": OUT.relative_to(ROOT).as_posix(), "status": "completed-development-experiment",
        "candidateTrialsAdded": 1, "newSetupMechanisms": 1, "newEntryPolicies": 2, "newAccounts": 24,
        "reusedAccounts": 0, "deliveryPath": delivery_path.relative_to(ROOT).as_posix(),
        "countingNote": "The legacy trials field counts completed batches; both fixed entry policies and all 24 accounts are disclosed.",
        **{field: ref(OUT / filename) for field, filename in (
            ("plan", "plan.json"), ("inputCompletion", "input-completion.json"),
            ("preAccountTests", "pre-account-tests.json"), ("inputValidation", "input-validation.json"),
            ("comparison", "comparison.json"), ("validation", "validation.json"),
            ("readout", "readout.json"), ("readoutValidation", "readout-validation.json"),
            ("decision", "decision.json"), ("renderReceipt", "render-receipt.json"),
            ("visualReview", "visual-review.json"))},
        "targetAchieved": False, "certified": False}
    program["progress"]["nextStage"] = decision["nextStage"]
    program["progress"]["lastTurnClassification"] = "new-first-down-retention-with-completed-account-comparison"
    program["targetAchieved"] = False
    timestamp = decision["completedAt"]
    program["updatedAt"] = timestamp
    program["updatedDate"] = datetime.fromisoformat(timestamp).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    once(OUT / "program-after.json", program)
    for name in ("scripts/finalize-leader-pullback-retention.py", "scripts/render-leader-pullback-retention.py",
                 "scripts/verify-leader-pullback-retention-preview.cjs", "scripts/verify-leader-pullback-retention-readout.py"):
        path = ROOT / name
        require(path.is_file(), "Postprocessing source absent: " + name)
        target = OUT / "postprocessing-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == sha(path), "Archived postprocessing source differs")
        else:
            with target.open("xb") as stream:
                stream.write(path.read_bytes())
    files = [ref(p) for p in sorted(OUT.rglob("*")) if p.is_file() and p != delivery_path]
    delivery = {"schema": "leader-pullback-retention-delivery-v1", "completedAt": timestamp,
                "programBeforeSha256": sha(previous), "programAfterSha256": sha(OUT / "program-after.json"),
                "newAccounts": 24, "candidateTrialsAfter": 45, "certified": False,
                "targetAchieved": False, "files": files}
    once(delivery_path, delivery)
    verify_files(delivery)
    apply_registry(delivery)
    print(json.dumps({"passed": True, "delivery": ref(delivery_path), "files": len(files),
                      "programSha256": sha(BASE / "program.json"), "targetAchieved": False}))


if __name__ == "__main__":
    main()
