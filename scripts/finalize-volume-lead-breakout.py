"""Seal batch49 evidence before publishing the recoverable research registry update."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "volume-lead-breakout-v1"
FAMILY = OUT.name
PROGRAM_BEFORE = "0f7d03439605596ad07c41011a59257c4eacaa3978714bbffc41f9fd4fe1b061"
PREVIOUS_DELIVERY = "eca2b8a6c4cd8a8fb37f8691c1f83d242c7fb3b8e210219c5bfee602ac1a4dce"
PERIODS, BOARDS = ("2022-2023", "2024-2025"), ("main", "chinext", "star")
POLICIES = ("priceBreakout", "volumeLead")
AUDITOR = "scripts/verify-volume-lead-breakout.py"
SUMMARY = "scripts/summarize-volume-lead-breakout.py"
RENDERER = "scripts/render-volume-lead-breakout.py"
PREVIEW = "scripts/verify-volume-lead-breakout-preview.cjs"
DRIVER = "scripts/execute-volume-lead-breakout.py"
TABLES = {"accountRows": 12, "comparisons": 6, "formationCoverageRows": 6, "coverageRows": 12,
          "yearRows": 24, "halfYearRows": 48, "phaseRows": 96, "curveEpochRows": 12}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def ref(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed artifact: " + item["path"])
    return path


def stamp(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, "Timestamp must include timezone")
    return result


def write_once(path, value):
    """Publish only a complete JSON file; an identical prior publication is reusable."""
    if path.exists():
        require(read(path) == value, "Existing artifact differs: " + path.name)
        return
    temporary = path.parent / (".seal-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(read(path) == value, "Concurrent artifact differs: " + path.name)
    finally:
        temporary.unlink(missing_ok=True)


def verify_predecessor(plan):
    require(plan["previousProgram"]["sha256"] == PROGRAM_BEFORE and
            sha(OUT / "program-before.json") == PROGRAM_BEFORE and
            plan["previousDelivery"]["sha256"] == PREVIOUS_DELIVERY, "Wrong predecessor")
    path = bound(plan["previousDelivery"])
    delivery = read(path)
    require(delivery["candidateTrialsAfter"] == 48 and len(delivery["files"]) == 202 and
            len({r["path"] for r in delivery["files"]}) == 202, "All202 sealed48 members required")
    for item in delivery["files"]:
        require(bound(item).is_relative_to(path.parent), "Previous member outside family")


def verify_pins(plan):
    for kind in ("sourcePins", "inputPins"):
        for name, expected in plan[kind].items():
            bound({"path": name, "sha256": expected})
    for name in (Path(__file__).relative_to(ROOT).as_posix(), AUDITOR, SUMMARY, RENDERER, PREVIEW, DRIVER):
        require(plan["sourcePins"].get(name) == sha(ROOT / name), "Unregistered executable: " + name)
    for name, expected in plan["sourcePins"].items():
        copy = (OUT / "frozen-sources" / name).resolve()
        require(copy.is_relative_to(OUT / "frozen-sources") and sha(copy) == expected, "Frozen source copy differs: " + name)


def verify_execution(plan):
    names = {"planSha256": "plan.json", "preAccountTestsSha256": "pre-account-tests.json",
             "inputCompletionSha256": "input-completion.json", "inputValidationSha256": "input-validation.json",
             "runStartSha256": "run-start.json", "comparisonSha256": "comparison.json"}
    hashes = {key: sha(OUT / name) for key, name in names.items()}
    tests, prepared, inputs, start, comparison, audit = [read(OUT / name) for name in
        ("pre-account-tests.json", "input-completion.json", "input-validation.json", "run-start.json", "comparison.json", "validation.json")]
    require(plan["schema"] == FAMILY and plan["strategyId"] == "volumeLeadBreakoutV1" and
            plan["policies"] == list(POLICIES) and plan["boards"] == list(BOARDS) and plan["exits"] == ["trail10"], "Registered scope differs")
    trial = {"newSetupMechanisms": 1, "newEntryPolicies": 2, "newAccounts": 12, "referenceAccountsReused": 0,
             "newUniverseVariants": 0, "completedResearchBatchesAdded": 1}
    require(plan["rules"]["trialAccounting"] == trial and all(plan[k] == comparison[k] == v for k, v in trial.items()), "Trial accounting differs")
    require(all(plan[k] is comparison[k] is False for k in ("holdoutClaim", "certified", "targetAchieved")), "Unsupported promotion claim")
    for item in (tests, inputs, audit):
        require(item["passed"] is True and item["planSha256"] == hashes["planSha256"], "Exact passing proof required")
    require(tests["sourcePins"] == plan["sourcePins"] and tests["allChecksBeforeFirstNewAccount"] is True, "Test source identity differs")
    patterns = ("test_research_volume_lead_breakout.py", "test_volume_lead_breakout_verifier.py",
                "test_volume_lead_breakout_readout.py", "test_research_fixed_signal_account.py",
                "test_research_account_fees.py", "test_research_account_outcomes.py")
    require(len(tests["commands"]) == len(tests["testCounts"]) == len(patterns), "All six actual pretest commands required")
    for command, count, pattern in zip(tests["commands"], tests["testCounts"], patterns):
        match = re.search(r"Ran (\d+) tests?", command["stderr"])
        require(command["exitCode"] == 0 and pattern in command["argv"] and match is not None and
                int(match.group(1)) == count and count > 0, "Missing actual passing test output")
    prepare_start = read(OUT / "prepare-start.json")
    require(prepare_start["planSha256"] == hashes["planSha256"] and
            prepare_start["preAccountTestsSha256"] == hashes["preAccountTestsSha256"], "Prepare-start differs")
    require(stamp(plan["registeredAt"]) <= stamp(tests["startedAt"]) <= stamp(tests["completedAt"]) <=
            stamp(prepare_start["startedAt"]) <= stamp(prepared["completedAt"]) <= stamp(inputs["verifiedAt"]) <=
            stamp(start["startedAt"]) <= stamp(comparison["completedAt"]) <= stamp(audit["verifiedAt"]), "Execution chronology differs")
    for item in (prepared, inputs, audit):
        for key in ("planSha256", "preAccountTestsSha256"):
            require(item[key] == hashes[key], "Proof test/plan binding differs")
    for item in (inputs, audit):
        require(item["inputCompletionSha256"] == hashes["inputCompletionSha256"] and
                item["verifierSha256"] == plan["sourcePins"][AUDITOR], "Independent proof changed")
        for kind in ("sourcePins", "inputPins"):
            for name, expected in item[kind].items():
                bound({"path": name, "sha256": expected})
    require(audit["inputValidationSha256"] == hashes["inputValidationSha256"] and
            audit["comparisonSha256"] == hashes["comparisonSha256"] and audit["runStart"] == ref(OUT / "run-start.json"), "Account proof ancestry differs")
    require(start["planSha256"] == hashes["planSha256"] and
            all(start[key] == ref(OUT / name) for key, name in (("inputCompletion", "input-completion.json"),
                ("inputValidation", "input-validation.json"), ("tests", "pre-account-tests.json"))), "Account start changed")
    require(all(comparison[k] == v for k, v in hashes.items() if k != "comparisonSha256"), "Comparison binding differs")
    groups = prepared["groups"]
    require([(g["period"], g["board"]) for g in groups] == [(p, b) for p in PERIODS for b in BOARDS] and
            inputs["preparedGroups"] == groups and prepared["allSchedulesFrozenBeforeAccounts"] is True and
            prepared["newAccountsExecuted"] == 0, "Complete input population required")
    for key, expected in (("sourceGroups", 6), ("preparedSchedules", 12), ("accountScheduleBindings", 12)):
        require(prepared["counts"][key] == inputs["counts"][key] == expected, "Incomplete input matrix")
    for group in groups:
        require(group["accountData"] == plan["sourceData"][group["period"]][group["board"]]["accountData"], "Source cohort changed")
        for key in ("accountData", "metadata", "daily", "opportunities", "inputReceipt"):
            bound(group[key])
        require(list(group["schedules"]) == list(POLICIES), "Missing schedule arm")
        for item in group["schedules"].values(): bound(item)
    rows = comparison["rows"]
    require([tuple(r[k] for k in ("period", "board", "exit", "policy")) for r in rows] ==
            [(p, b, "trail10", q) for p in PERIODS for b in BOARDS for q in POLICIES], "Complete12-account matrix required")
    require(audit["accountArtifacts"] == rows == [r["artifactRefs"] for r in audit["accounts"]] and
            tuple(audit["counts"][k] for k in ("accounts", "newAccounts", "reusedAccounts")) == (12, 12, 0), "Audited account matrix differs")
    require(len(comparison["groupCompletions"]) == 6, "Missing terminal groups")
    for group, terminal_ref in zip(groups, comparison["groupCompletions"]):
        path = bound(terminal_ref)
        require(path == OUT / group["period"] / group["board"] / "trail10/group-completion.json", "Terminal path differs")
        terminal = read(path)
        group_rows = [r for r in rows if (r["period"], r["board"]) == (group["period"], group["board"])]
        require(terminal["completed"] is True and terminal["rows"] == group_rows and terminal["newAccounts"] == 2 and
                terminal["referenceAccountsReused"] == 0 and all(terminal[k] == v for k, v in hashes.items() if k != "comparisonSha256"), "Terminal proof differs")
        group_start = read(path.parent / "group-run-start.json")
        require(group_start["planSha256"] == hashes["planSha256"] and group_start["runStartSha256"] == hashes["runStartSha256"] and
                stamp(start["startedAt"]) <= stamp(group_start["startedAt"]) <= stamp(terminal["completedAt"]) <= stamp(comparison["completedAt"]), "Group chronology differs")
        for row in group_rows:
            require(row["candidate"] == row["policy"] and row["reusedAccount"] is False and
                    row["schedule"] == group["schedules"][row["policy"]], "New account schedule differs")
            for key in ("schedule", "result", "checkpoint", "summary"): bound(row[key])
    driver = read(OUT / "execution-logs/driver-completion.json")
    require(driver["passed"] is True and driver["driverSha256"] == plan["sourcePins"][DRIVER] and
            driver["planSha256"] == hashes["planSha256"] and driver["inputValidationSha256"] == hashes["inputValidationSha256"] and
            driver["automaticRetries"] == 0 and driver["maxConcurrentGroups"] == 3 and
            sorted(r["group"] for r in driver["groups"]) == sorted(p + "/" + b for p in PERIODS for b in BOARDS) and
            all(r["exitCode"] == 0 for r in driver["groups"]), "Single successful six-group execution required")
    return tests, prepared, audit


def verify_readout(plan, prepared, audit):
    report, proof = read(OUT / "readout.json"), read(OUT / "readout-validation.json")
    require(proof["passed"] is True and proof["verifierSha256"] == plan["sourcePins"][SUMMARY], "Independent readout proof required")
    for key, name in (("planSha256", "plan.json"), ("comparisonSha256", "comparison.json"),
                      ("inputCompletionSha256", "input-completion.json"), ("accountValidationSha256", "validation.json"),
                      ("readoutSha256", "readout.json")):
        require(proof[key] == sha(OUT / name), "Readout proof changed: " + key)
    require(report["planSha256"] == proof["planSha256"] and report["comparisonSha256"] == proof["comparisonSha256"] and
            report["verification"]["passed"] is True and bound(report["verification"]) == OUT / "validation.json", "Readout account provenance differs")
    for pins in (report["inputPins"], proof["sourcePins"]):
        for name, expected in pins.items(): bound({"path": name, "sha256": expected})
    for key, size in {**TABLES, "accounts": 12, "primaryComparisons": 6, "partitionReconciliation": 12}.items():
        count_key = "accounts" if key == "accountRows" else key
        require(len(report[key]) == size and report["counts"][count_key] == size, "Incomplete readout: " + key)
    count = prepared["counts"]["opportunities"]
    require(report["counts"] == proof["counts"] and count == audit["counts"]["sourceOpportunityCount"] ==
            report["counts"]["sourceOpportunityCount"] and report["counts"]["policyDispositions"] == 2 * count and
            report["newAccounts"] == 12 and report["referenceAccountsReused"] == report["accountExecutionsBySummarizer"] == 0 and
            all(report[k] is False for k in ("holdoutClaim", "certified", "targetAchieved")), "Readout scope differs")
    for key, count_key in (("opportunityRowsArtifact", "policyDispositions"), ("entryRowsArtifact", "actualEntryEpisodes")):
        item = report[key]
        require(bound(item).is_relative_to(OUT) and item["sha256"] == proof[key + "Sha256"] and
                item["count"] == report["counts"][count_key], "Unbound complete sidecar: " + key)
    require(stamp(audit["verifiedAt"]) <= stamp(proof["completedAt"]), "Readout audit predates account proof")
    return report


def verify_presentation(plan):
    render = read(OUT / "render-receipt.json")
    require(render["passed"] is True and render["builderSha256"] == plan["sourcePins"][RENDERER] and
            render["deliveryOrder"] == ["sources"] and render["sourceRows"] == sum(TABLES.values()), "Renderer scope or identity differs")
    for name in ("plan.json", "input-completion.json", "input-validation.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json"):
        require(render["inputPins"].get(name) == sha(OUT / name), "Missing render analysis pin: " + name)
    for name, expected in render["inputPins"].items():
        path = (OUT / name).resolve()
        require(path.is_relative_to(OUT) and sha(path) == expected, "Render input changed")
    require(bound(render["payload"]).is_relative_to(OUT) and bound(render["fragment"]).is_relative_to(OUT), "Presentation outside batch")
    payload = read(bound(render["payload"]))
    require(len(payload["items"]) == 1 and [len(q["rows"]) for q in payload["items"][0]["queries"]] == list(TABLES.values()), "Source table population differs")
    require(render["presentationHelpers"], "Missing shared renderer provenance")
    for item in render["presentationHelpers"]:
        require(sha(item["path"]) == item["sha256"], "Presentation helper changed")
    review = read(OUT / "visual-review.json")
    qa_path = bound(review["qa"])
    require(qa_path.is_relative_to(OUT / "browser-qa"), "QA outside batch")
    qa = read(qa_path)
    require(review["passed"] is True and review["actualImagesViewed"] is True and qa["passed"] is True and
            not qa["errors"] and review["images"] == qa["images"] and len(qa["images"]) == 3 and
            review["renderReceiptSha256"] == qa["renderReceiptSha256"] == sha(OUT / "render-receipt.json") and
            qa["scriptSha256"] == plan["sourcePins"][PREVIEW], "Actual bound screenshot review required")
    require([(v["viewportWidth"], v["colorScheme"]) for v in qa["views"]] == [(780, "light"), (360, "light"), (780, "dark")] and
            [r["rows"] for r in qa["tableChecks"]] == list(TABLES.values()), "Preview matrix differs")
    for item, view in zip(review["images"], qa["views"]):
        path = bound(item)
        require(path.is_relative_to(qa_path.parent), "Unbound image")
        with path.open("rb") as stream:
            header = stream.read(24)
        require(header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR" and
                struct.unpack(">II", header[16:24])[0] == view["viewportWidth"], "PNG viewport differs")
        require(0 < view["width"] <= view["viewportWidth"] and
                view["scroll"] <= view["width"] + 1 and view["right"] <= view["width"] + 1, "Preview overflow")
    return render, review


def archive_helpers(render):
    folder = OUT / "frozen-presentation-helpers"
    folder.mkdir(exist_ok=True)
    copies = []
    for index, item in enumerate(render["presentationHelpers"]):
        original = Path(item["path"])
        target = folder / (str(index).zfill(3) + "-" + original.name)
        if not target.exists():
            temporary = folder / (".seal-" + uuid4().hex + ".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(original.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                try: os.link(temporary, target)
                except FileExistsError: pass
            finally:
                temporary.unlink(missing_ok=True)
        require(sha(target) == item["sha256"], "Archived renderer helper differs")
        copies.append({"original": item, "archive": ref(target)})
    write_once(folder / "manifest.json", {"helpers": copies})


def publish(delivery):
    require(delivery["schema"] == FAMILY + "-delivery" and delivery["programBeforeSha256"] == PROGRAM_BEFORE and
            delivery["candidateTrialsAfter"] == 49 and delivery["newAccounts"] == 12 and delivery["referenceAccountsReused"] == 0 and
            delivery["plan"] == ref(OUT / "plan.json") and delivery["targetAchieved"] is delivery["certified"] is False, "Wrong sealed delivery")
    require(len({r["path"] for r in delivery["files"]}) == len(delivery["files"]), "Duplicate sealed member")
    for item in delivery["files"]: require(bound(item).is_relative_to(OUT), "Member outside batch")
    before, after = OUT / "program-before.json", OUT / "program-after.json"
    require(sha(before) == delivery["programBeforeSha256"] and sha(after) == delivery["programAfterSha256"], "Registry snapshot changed")
    state = read(after)
    require(state["goalStatus"] == "active" and state["targetAchieved"] is False and
            state["progress"]["completedFixedCandidateTrials"] == 49, "Unsupported registry state")
    target = BASE / "program.json"
    if sha(target) == delivery["programAfterSha256"]: return
    require(sha(target) == delivery["programBeforeSha256"], "Registry changed")
    temporary = BASE / ("admission-program-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(after.read_bytes()); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    require(sha(target) == delivery["programAfterSha256"], "Registry replacement mismatch")


def main():
    plan = read(OUT / "plan.json")
    verify_predecessor(plan)
    verify_pins(plan)
    delivery_path = OUT / "delivery.json"
    if delivery_path.exists():
        publish(read(delivery_path))
        print(json.dumps({"passed": True, "alreadyFinalized": True, "delivery": ref(delivery_path)}))
        return
    require(sha(BASE / "program.json") == PROGRAM_BEFORE, "Registry changed before sealing")
    tests, prepared, audit = verify_execution(plan)
    report = verify_readout(plan, prepared, audit)
    render, review = verify_presentation(plan)
    decision = read(OUT / "decision.json")
    require(decision["targetAchieved"] is False and decision["certified"] is False and decision["newAccounts"] == 12 and
            isinstance(decision["nextStage"], str) and decision["nextStage"].strip(), "Explicit provisional research decision required")
    completed = stamp(decision["completedAt"])
    require(completed >= stamp(read(OUT / "readout-validation.json")["completedAt"]), "Decision predates validated readout")
    program = read(OUT / "program-before.json")
    require(program["progress"]["completedFixedCandidateTrials"] == 48 and program["goalStatus"] == "active" and
            program["targetAchieved"] is False, "Prior progress differs")
    refs = {key: ref(OUT / name) for key, name in (("plan", "plan.json"), ("preAccountTests", "pre-account-tests.json"),
        ("inputCompletion", "input-completion.json"), ("inputValidation", "input-validation.json"), ("runStart", "run-start.json"),
        ("comparison", "comparison.json"), ("validation", "validation.json"), ("readout", "readout.json"),
        ("readoutValidation", "readout-validation.json"), ("decision", "decision.json"), ("renderReceipt", "render-receipt.json"),
        ("visualReview", "visual-review.json"), ("driverCompletion", "execution-logs/driver-completion.json"))}
    refs.update(opportunityRowsArtifact=report["opportunityRowsArtifact"], entryRowsArtifact=report["entryRowsArtifact"],
                previewVerification=review["qa"], payload=render["payload"], fragment=render["fragment"])
    program["progress"]["completedFixedCandidateTrials"] = 49
    program["progress"]["volumeLeadBreakout"] = {
        "directory": OUT.relative_to(ROOT).as_posix(), "status": "completed-development-account-comparison",
        **plan["rules"]["trialAccounting"], "exit": "trail10", "policies": list(POLICIES),
        "countingNote": "Legacy completedFixedCandidateTrials counts completed research batches, not independently successful strategies.",
        "deliveryPath": delivery_path.relative_to(ROOT).as_posix(), **refs, "counts": report["counts"],
        "preAccountTestCounts": tests["testCounts"],
        "testScopeNote": "Actual six command counts are recorded separately; inherited historical test totals are unchanged and not claimed rerun in full.",
        "holdoutClaim": False, "targetAchieved": False, "certified": False}
    program["progress"]["lastTurnClassification"] = "new-completed-volume-lead-breakout-account-comparison"
    program["progress"]["nextStage"] = decision["nextStage"]
    program["updatedAt"] = decision["completedAt"]
    program["updatedDate"] = completed.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    program["goalStatus"], program["targetAchieved"] = "active", False
    write_once(OUT / "program-after.json", program)
    archive_helpers(render)
    delivery = {"schema": FAMILY + "-delivery", "completedAt": decision["completedAt"],
        "programBeforeSha256": PROGRAM_BEFORE, "programAfterSha256": sha(OUT / "program-after.json"),
        "candidateTrialsAfter": 49, **plan["rules"]["trialAccounting"], "references": refs, "plan": refs["plan"],
        "sourcePins": plan["sourcePins"], "inputPins": plan["inputPins"],
        "diagnosticOnly": False, "goalStatus": "active", "holdoutClaim": False, "certified": False, "targetAchieved": False,
        "files": [ref(p) for p in sorted(OUT.rglob("*")) if p.is_file() and p != delivery_path]}
    write_once(delivery_path, delivery)
    publish(delivery)
    print(json.dumps({"passed": True, "delivery": ref(delivery_path), "members": len(delivery["files"]),
                      "programSha256": sha(BASE / "program.json"), "newAccounts": 12, "targetAchieved": False}))


if __name__ == "__main__":
    main()
