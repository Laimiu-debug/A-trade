"""Close the fixed 48-account experiment only after independent and visual review."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "all-opportunity-feedback-v1"
ORDER = ["oldChart", "oldSources", "newChart", "newSources"]
RENDER = "scripts/render-all-opportunity-feedback.py"
QA = "scripts/verify-all-opportunity-feedback-preview.cjs"
AUDIT = "scripts/verify-all-opportunity-feedback-readout.py"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ref(path):
    return {"path": Path(path).resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}


def write(path, value):
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(read(path) == value, "Existing finalization evidence differs: " + str(path))
    finally:
        temporary.unlink()


def bound(item, base=ROOT, external=False):
    path = Path(item["path"])
    path = (path if path.is_absolute() else base / path).resolve()
    if not external:
        require(path.is_relative_to(base.resolve()), "Artifact outside declared root")
    require(sha(path) == item["sha256"], "Changed artifact: " + str(path))
    return path


def verify_presentation():
    render = read(OUT / "render-receipt.json")
    freeze = read(OUT / "presentation-source-pins.json")
    for name in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json",
                 "presentation-source-pins.json"):
        require(render["inputPins"][name] == sha(OUT / name), "Stale presentation input: " + name)
    require(freeze["passed"] is True and freeze["planSha256"] == sha(OUT / "plan.json"), "Presentation freeze missing")
    for name, value in freeze["sourcePins"].items():
        bound({"path": name, "sha256": value})
    require(freeze["sourcePins"][RENDER] == render["builderSha256"] == sha(ROOT / RENDER)
            and freeze["sourcePins"][QA] == sha(ROOT / QA), "Presentation sources changed")
    require(render["deliveryOrder"] == ORDER and set(render["fragments"]) == set(render["payloads"]) == set(ORDER)
            and render["chartRows"] == 24 and render["newAccountExecutions"] == 0
            and render["analysisArtifactsModified"] is False, "Presentation matrix/scope differs")
    for values in (render["fragments"], render["payloads"]):
        for item in values.values():
            bound(item, OUT)
    helpers = [bound(item, external=True) for item in render["presentationHelpers"]]
    require(len(set(helpers)) == 3 and {p.name for p in helpers} ==
            {"render-inline-chart.mjs", "render-inline-sources.mjs", "render.py"}, "Shared renderer provenance missing")
    visual = read(OUT / "visual-review.json")
    qa_path = bound(visual["qa"])
    qa = read(qa_path)
    require(qa_path.is_relative_to(OUT / "browser-qa") and qa["passed"] is True
            and qa["renderReceiptSha256"] == sha(OUT / "render-receipt.json")
            and qa["scriptSha256"] == sha(ROOT / QA) and qa["errors"] == []
            and qa["actualMarketRuns"] == 0 and qa["analysisArtifactsModified"] is False, "Browser review missing/stale")
    require(all(qa[k] == render[k] for k in ("fragments", "payloads", "deliveryOrder", "tableRows", "presentationHelpers")),
            "Browser review differs from delivered payloads")
    require(len(qa["chartViews"]) == 6 and len(qa["interactions"]) == 2
            and sum(r["rows"] for r in qa["tableChecks"]) == sum(map(sum, render["tableRows"])), "Incomplete chart/source QA")
    require(visual["passed"] is True and visual["reviewedBy"] == "root" and visual["actualImagesViewed"] is True
            and visual["renderReceiptSha256"] == sha(OUT / "render-receipt.json") and visual["observations"],
            "Actual root image review missing")
    observed = {bound(item): item["sha256"] for item in visual["images"]}
    images = {bound(item, OUT): item["sha256"] for item in qa["images"]}
    require(len(observed) == len(images) == 8 and observed == images, "All eight final images must be reviewed")
    return render, qa_path, helpers


def apply_registry(delivery):
    program = BASE / "program.json"
    if sha(program) == delivery["programAfterSha256"]:
        return
    require(sha(program) == delivery["programBeforeSha256"], "Registry changed during experiment")
    temporary = BASE / ("all-opportunity-feedback-program-" + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write((OUT / "program-after.json").read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, program)
    require(sha(program) == delivery["programAfterSha256"], "Registry publication differs")


def main():
    if (OUT / "delivery.json").exists():
        delivery = read(OUT / "delivery.json")
        for item in delivery["files"]:
            bound(item)
        apply_registry(delivery)
        return {"completed": True, "reusedDelivery": True, "delivery": ref(OUT / "delivery.json")}
    spec = importlib.util.spec_from_file_location("all_feedback_runner", ROOT / "scripts/run-all-opportunity-feedback.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    plan = runner.verify_plan()
    names = ("plan.json", "pre-account-tests.json", "input-completion.json", "input-validation.json",
             "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    values = {name: read(OUT / name) for name in names}
    pins = {name: sha(OUT / name) for name in names}
    tests, completion, inputs, comparison, validation, report, audit = [values[name] for name in names[1:]]
    require(tests["passed"] is True and tests["allChecksBeforeFirstNewAccount"] is True
            and tests["sourcePins"] == plan["sourcePins"] and len(tests["commands"]) >= 4
            and all(row["exitCode"] == 0 for row in tests["commands"]), "Prerequisite checks missing")
    for proof in (tests, completion, inputs, comparison, validation, report, audit):
        require(proof["planSha256"] == pins["plan.json"], "Stale experiment binding")
    for proof in (completion, inputs, comparison, validation):
        require(proof["preAccountTestsSha256"] == pins["pre-account-tests.json"], "Prerequisite test evidence changed")
    start_path = bound(validation["runStart"])
    start = read(start_path)
    require(start_path == OUT / "run-start.json" and comparison["runStartSha256"] == sha(start_path)
            and start["planSha256"] == pins["plan.json"], "Run-start evidence changed")
    for field, filename in (("inputCompletion", "input-completion.json"), ("inputValidation", "input-validation.json"), ("tests", "pre-account-tests.json")):
        require(start[field] == ref(OUT / filename), "Run-start dependency changed")
    require(comparison["newAccounts"] == 48 and comparison["referenceAccountsReused"] == 12
            and len(comparison["rows"]) == 60 and sum(r["reusedAccount"] for r in comparison["rows"]) == 12,
            "Full 48 new/12 reused account matrix required")
    require(completion["counts"]["preparedSchedules"] == 48 and completion["allSchedulesFrozenBeforeAccounts"] is True,
            "All masks must be frozen before accounts")
    for proof in (inputs, validation):
        require(proof["passed"] is True and proof["inputCompletionSha256"] == pins["input-completion.json"]
                and proof["verifierSha256"] == sha(ROOT / "scripts/verify-all-opportunity-feedback.py"), "Stale independent account audit")
    require(validation["comparisonSha256"] == pins["comparison.json"]
            and validation["inputValidationSha256"] == pins["input-validation.json"], "Account audit chain differs")
    require(audit["passed"] is True and audit["comparisonSha256"] == pins["comparison.json"]
            and audit["validationSha256"] == pins["validation.json"] and audit["readoutSha256"] == pins["readout.json"]
            and audit["verifierSha256"] == sha(ROOT / AUDIT), "Independent readout audit chain differs")
    for proof in (inputs, validation, audit):
        for name, value in proof.get("sourcePins", {}).items():
            bound({"path": name, "sha256": value})
        for name, value in proof.get("inputPins", {}).items():
            bound({"path": name, "sha256": value})
    for row in comparison["rows"]:
        for kind in ("result", "checkpoint", "summary", "schedule"):
            bound(row[kind])
    for key in ("opportunityRowsArtifact", "candidateEntryRowsArtifact", "pairedPredictionRowsArtifact"):
        bound(report[key])
    require(report["counts"]["accounts"] == 60 and report["counts"]["policyDispositions"] == 81550
            and report["counts"]["pairedPredictionOpportunities"] == 65240, "Complete outcome denominators required")
    for flag in ("certified", "holdoutClaim", "targetAchieved"):
        require(report[flag] is False, "Development result overclaims scope")
    render, qa_path, helpers = verify_presentation()
    decision = read(OUT / "decision.json")
    require(decision["targetAchieved"] is False and decision["certified"] is False and decision["holdoutClaim"] is False
            and decision["newAccounts"] == 48 and decision["candidateTrialsAdded"] == 1
            and decision["nextStage"] and decision["readoutSha256"] == pins["readout.json"], "Unsupported research decision")
    require(sha(BASE / "program.json") == plan["previousProgram"]["sha256"] == sha(OUT / "program-before.json"),
            "Research registry changed")
    post = set(plan["sourcePins"]) | {RENDER, QA, AUDIT, "scripts/finalize-all-opportunity-feedback.py"}
    for proof in (inputs, validation, audit, read(OUT / "presentation-source-pins.json")):
        post.update(proof.get("sourcePins", {}))
    post_pins = {}
    for name in sorted(post):
        target = OUT / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == sha(ROOT / name), "Frozen source changed: " + name)
        else:
            shutil.copy2(ROOT / name, target)
        post_pins[name] = sha(target)
    external = []
    for index, path in enumerate(helpers, 1):
        target = OUT / "sources/external-presentation" / (str(index) + "-" + path.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        require(sha(path) == sha(target), "Presentation helper archive differs")
        external.append({"original": {"path": str(path), "sha256": sha(path)}, "archived": ref(target)})
    archive = OUT / "reproduction-sources.zip"
    archive_members = {path.relative_to(OUT).as_posix(): sha(path) for path in sorted((OUT / "sources").rglob("*")) if path.is_file()}
    if not archive.exists():
        temporary = BASE / ("all-opportunity-feedback-sources-" + uuid4().hex + ".tmp")
        try:
            with zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED, strict_timestamps=False) as handle:
                for name in archive_members:
                    handle.write(OUT / name, name)
            os.link(temporary, archive)
        finally:
            if temporary.exists():
                temporary.unlink()
    with zipfile.ZipFile(archive) as handle:
        require(handle.testzip() is None, "Corrupt reproduction archive")
        require(len(handle.namelist()) == len(archive_members) and set(handle.namelist()) == set(archive_members)
                and all(hashlib.sha256(handle.read(name)).hexdigest() == value for name,value in archive_members.items()),
                "Existing reproduction archive differs")
    program = read(BASE / "program.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 39, "Unexpected trial count")
    receipts = {key: ref(OUT / name) for key, name in (("plan", "plan.json"), ("comparison", "comparison.json"),
        ("validation", "validation.json"), ("readout", "readout.json"), ("readoutValidation", "readout-validation.json"),
        ("preAccountTests", "pre-account-tests.json"), ("inputValidation", "input-validation.json"),
        ("decision", "decision.json"), ("renderReceipt", "render-receipt.json"), ("visualReview", "visual-review.json"))}
    program["progress"]["allOpportunityFeedback"] = {"directory": OUT.name, "status": "completed-development-experiment",
        "candidateTrialsAdded": 1, "newAccounts": 48, "reusedAccounts": 12, **receipts, "browserQA": ref(qa_path),
        "deliveryPath": (OUT / "delivery.json").relative_to(ROOT).as_posix(), "targetAchieved": False, "certified": False}
    program["progress"]["completedFixedCandidateTrials"] = 40
    program["progress"]["nextStage"] = decision["nextStage"]
    program["progress"]["lastTurnClassification"] = "experiment: fixed 48 shared-capital candidate accounts plus 12 reused controls; population/model contrasts and paired forecast evaluation; development only."
    program["goalStatus"], program["targetAchieved"] = "active", False
    program["updatedAt"] = datetime.now(timezone.utc).isoformat()
    program["updatedDate"] = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if (OUT / "program-after.json").exists():
        saved = read(OUT / "program-after.json")
        require({k:v for k,v in saved.items() if k not in ("updatedAt","updatedDate")} ==
                {k:v for k,v in program.items() if k not in ("updatedAt","updatedDate")}, "Existing registry snapshot differs")
        program = saved
    write(OUT / "program-after.json", program)
    inventory = [{**ref(path), "bytes": path.stat().st_size} for path in sorted(OUT.rglob("*"))
                 if path.is_file() and path.name != "delivery.json"]
    delivery = {"schema": OUT.name + "-delivery", "completedAt": datetime.now(timezone.utc).isoformat(),
        "files": inventory, "sourceArchive": ref(archive), "postprocessingSourcePins": post_pins,
        "externalPresentationHelpers": external, **receipts, "browserQA": ref(qa_path),
        "programBeforeSha256": plan["previousProgram"]["sha256"], "programAfterSha256": sha(OUT / "program-after.json"),
        "candidateTrialCount": 40, "candidateTrialsAdded": 1, "newAccounts": 48, "referenceAccountsReused": 12,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    write(OUT / "delivery.json", delivery)
    for item in inventory:
        bound(item)
    apply_registry(delivery)
    return {"completed": True, "delivery": ref(OUT / "delivery.json"), "files": len(inventory), "candidateTrials": 40,
            "newAccounts": 48, "referenceAccountsReused": 12, "targetAchieved": False}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
