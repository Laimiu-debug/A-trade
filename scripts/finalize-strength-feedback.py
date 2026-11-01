"""Register strength-feedback only after bound audits and actual visual review.

This does not compute feedback, rerun accounts or promote a strategy. Normal
execution is for the root agent after review; --self-test uses synthetic values
only and does not read real results or write any file.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "strength-feedback-v1"
PARENT = BASE / "leader-lag-followers-v1"
PROGRAM = BASE / "program.json"
BEFORE = "339b14546548c49230e97720fd433d0d7669a156371ffa3d2d39fb1dbc0b4c82"
PLAN_SHA = "fe80aaf6d254cf0ca42934c7d50b7f1c915b65ebd53a8092663634136b002c23"
PRE_ACCOUNT_TESTS_SHA = "e6a768ecab629844d8b1ad9949ad863b06e4d18747612b87d9bf04984b8b93f1"
VERIFIER = "scripts/verify-strength-feedback.py"
READOUT_VERIFIER = "scripts/verify-strength-feedback-readout.py"
READOUT_VERIFIER_SHA = "313f3bdcb2c4330c53347305c26b8a6fb5442406718a2c821e938ce2de591d41"
SUMMARIZER = "scripts/summarize-strength-feedback.py"
RENDERER = "scripts/render-strength-feedback.py"
QA_SCRIPT = "scripts/verify-strength-feedback-preview.cjs"
DIAGNOSTIC = "scripts/diagnose-strength-feedback-selection.py"
DIAGNOSTIC_SHA = "d2b102cc633976b0112894f0b9b2baa428fcde1def66b32df201dd3594e46543"
TABLE_ROWS = [[18, 18, 18, 6, 6, 18, 36, 72, 144, 18]]
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
LABELS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
MODES = ("baseline", "known", "positive")
CONTRASTS = {"knownVsBaseline": ("baseline", "known"),
             "positiveVsKnown": ("known", "positive"),
             "positiveVsBaseline": ("baseline", "positive")}
TRIALS = {"experimentBatches": 1, "newSetupMechanisms": 0, "newEntryPolicies": 2,
          "newUniverseVariants": 0, "newAccountExecutions": 12, "referenceAccountsReused": 6,
          "summarizerAccountExecutions": 0}
MATRIX = {"accounts": 18, "accountRows": 18, "comparisons": 18, "yearRows": 36,
          "halfYearRows": 72, "phaseRows": 144, "curveEpochRows": 18, "coverageRows": 18,
          "formationCoverageRows": 6, "feedbackCoverageRows": 6, "partitionReconciliation": 18}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def ref(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path), "bytes": path.stat().st_size}


def bound(item, base=ROOT):
    require(isinstance(item, dict) and {"path", "sha256"} <= set(item), "Missing file descriptor")
    path = (base / item["path"]).resolve()
    require(not Path(item["path"]).is_absolute() and path.is_relative_to(base.resolve()), "Reference escaped its base")
    require(path.is_file() and sha(path) == item["sha256"], "Reference missing or changed: " + item["path"])
    if "bytes" in item:
        require(path.stat().st_size == item["bytes"], "Reference byte size changed")
    return path


def pin_map(pins, base=ROOT):
    require(isinstance(pins, dict) and bool(pins), "Missing nonempty pins")
    for name, value in pins.items():
        bound({"path": name, "sha256": value}, base)


def number(value, suffix=""):
    return f"{value:.2f}{suffix}" if type(value) in (int, float) and math.isfinite(value) else "unknown"


def validate_matrix(result):
    require(result["trialAccounting"] == TRIALS, "Trial accounting changed")
    require(all(len(result[key]) == count for key, count in MATRIX.items()), "Incomplete readout matrix")
    for key in ("accounts", "accountRows", "coverageRows"):
        identities = {(r["period"], r["board"], r["policy"]) for r in result[key]}
        require(identities == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES}, "Duplicate or missing account identity: " + key)
    require({(r["period"], r["board"], r["id"]) for r in result["comparisons"]}
            == {(p, b, c) for p in PERIODS for b in BOARDS for c in CONTRASTS}, "Comparison matrix differs")
    for row in result["comparisons"]:
        require((row["control"], row["candidate"]) == CONTRASTS[row["id"]], "Wrong comparison orientation")
    for key, contrast in (("primaryComparisons", "positiveVsKnown"),
                          ("availabilityComparisons", "knownVsBaseline"),
                          ("overallComparisons", "positiveVsBaseline")):
        require(result[key] == [r for r in result["comparisons"] if r["id"] == contrast], "Comparison subset differs")
    require(all(row["passed"] is True for row in result["partitionReconciliation"]), "Partition reconciliation failed")
    require(result["certified"] is False and result["targetAchieved"] is False, "Unregistered certification")
    require(all(result["decision"][key] is None for key in
                ("selectedBoard", "selectedPolicy", "automaticNextStrategy", "profitHurdle")), "Unregistered promotion")


def opportunity_count(result):
    """Original executable control queue, not all earlier raw member Top5 rows."""
    selected = sum(result["inputCountsByPeriodBoard"][p][b]["inputSignalCount"] for p in PERIODS for b in BOARDS)
    for p in PERIODS:
        for b in BOARDS:
            counts = result["inputCountsByPeriodBoard"][p][b]
            require(counts["inputSignalCount"] == counts["opportunityCount"], "Source opportunity count changed")
    require(result["opportunityRowsArtifact"]["count"] == selected
            == sum(r["formationTop5"] for r in result["formationCoverageRows"])
            == sum(r["parentControlSignals"] for r in result["formationCoverageRows"]), "Original control population not conserved")
    require(len(result["feedbackDailyRows"]) == sum(result["inputCountsByPeriodBoard"][p][b]["targetDayCount"]
            for p in PERIODS for b in BOARDS), "Full target-day feedback coverage differs")
    return selected


def findings(result):
    lines = []
    for row in result["accounts"]:
        saved, opened = row["comparisonRow"], row["openPositionsAtCutoff"]
        lines.append(f"{row['period']} {LABELS[row['board']]} {row['policy']}: cumulative net return "
            f"{number(saved['accountReturnPct'], '%')}, maximum drawdown {number(saved['maxDrawdownPct'], '%')}, "
            f"mean exposure {number(saved['meanExposurePct'], '%')}; closed episodes {saved['closed']}, "
            f"terminal open positions {saved['openPositions']}; closed PnL {number(row['closedDistribution']['netPnlAmount'])}, "
            f"open unrealized PnL {number(opened['unrealizedPnlAmount'])}, open realized PnL "
            f"{number(opened['realizedPnlWhileOpen'])}; unreliable valuation sessions {number(saved.get('unreliableValuationDays'))}.")
    for row in result["comparisons"]:
        delta = row["candidateMinusControl"]
        lines.append(f"{row['period']} {LABELS[row['board']]} {row['id']}: candidate-control net-return difference "
            f"{number(delta['accountReturnPct'])} percentage points, drawdown difference {number(delta['maxDrawdownPct'])} "
            f"percentage points, exposure difference {number(delta['meanExposurePct'])} percentage points. "
            "These are full-account differences including altered capital paths, not causal effects.")
    for row in result["curveEpochRows"]:
        lines.append(f"2024-2025 {LABELS[row['board']]} {row['policy']} {row['phaseId']}: return "
            f"{number(row['returnPct'], '%')}, drawdown {number(row['maxDrawdownPct'], '%')}; "
            "continuous inherited equity and positions, no event-date restart.")
    lines.extend([
        "Six exact parent control accounts are reused without execution, and twelve new accounts apply two entry policies; no new setup or universe variant.",
        "Known-baseline measures availability/ambiguity selection, positive-known is the primary feedback contrast, and positive-baseline retains their combined effect. All eighteen signed contrasts remain visible.",
        "F=A-5 fixes a same-board RS20 top-decile cohort. A observes absolute raw close-price continuation over six exact slots. Unknown members stay in the original denominator; a known majority verdict does not imply a completely observed cohort.",
        "Original signal IDs, dates, ranks and owner lineage are unchanged. The new strategy identity changes metadata only; masks do not replace Top5 slots or liquidate existing positions.",
        "Feedback days, overlapping member observations, original opportunities and completed trades are different dependent populations. A close-price feedback is not executable net profit or evidence of investor identity.",
        "All 2022-2025 periods were previously inspected and 2026 is excluded. Same-board survivor cohorts and common CSI300 ranking do not identify all-market leadership, board-matched beta or pure technology industries.",
        "Raw-price corporate-action, historical-status and execution-proxy limitations and valuation flags persist despite arithmetic validation. No policy/board is promoted, no fixed payoff threshold is introduced, and the research goal remains active."])
    for mode in MODES:
        rows = [r for r in result["accounts"] if r["policy"] == mode]
        if rows and all(r["comparisonRow"].get("signals") == 0 for r in rows):
            lines.append(f"All {mode} accounts have zero scheduled signals. Cash-only results do not demonstrate small-loss/large-win efficacy or justify promotion over a losing active comparator.")
    return lines


def validate_tests(checks, plan_sha):
    require(checks["schema"] == "strength-feedback-pre-account-tests-v1" and checks["passed"] is True
            and checks["planSha256"] == plan_sha and checks["unitTests"] == checks["productionUnitIncrement"] == 20
            and checks["allChecksBeforeFirstRealSignals"] is True, "Pre-account production test evidence differs")
    require(tuple(checks[key] for key in ("independentSyntheticGroups", "readoutSyntheticGroups",
            "independentReadoutSyntheticGroups")) == (17, 13, 8), "Actual synthetic group counts differ")
    require(len(checks["commands"]) == 4 and all(row["exitCode"] == 0 for row in checks["commands"]), "Pre-account commands failed")
    cross = checks["syntheticCrossCheck"]
    require(cross["exactAllFieldsEqual"] is True and cross["realSourceRead"] is False and cross["accountExecutions"] == 0,
            "Synthetic cross-check did not pass or changed scope")


def validate_audits(plan, plan_sha):
    proofs = {name: read(OUT / name) for name in ("input-validation.json", "validation.json", "readout-validation.json")}
    for name, proof in proofs.items():
        require(proof["passed"] is True and proof["planSha256"] == plan_sha, "Unpassed or mismatched audit: " + name)
        script = READOUT_VERIFIER if name == "readout-validation.json" else VERIFIER
        require(proof["verifierSha256"] == plan["sourcePins"][script] == sha(ROOT / script), "Audit code differs: " + name)
        if proof.get("inputPins"):
            pin_map(proof["inputPins"])
        if proof.get("independentReferences"):
            pin_map(proof["independentReferences"])
    inputs, accounts, readout = (proofs[name] for name in ("input-validation.json", "validation.json", "readout-validation.json"))
    for proof in (inputs, accounts):
        require(proof["inputCompletionSha256"] == sha(OUT / "input-completion.json"), "Input completion binding changed")
        for key in ("baselineArtifacts", "baselineSchedules", "baselineInputs", "baselineComparison", "baselinePlan"):
            require(proof[key] == plan[key], "Baseline proof binding differs: " + key)
    require(inputs["counts"]["boardPeriodInputs"] == 6 and inputs["counts"]["schedules"] == 12,
            "Independent input matrix incomplete")
    require(tuple(accounts["counts"][key] for key in ("accounts", "newAccounts", "reusedAccounts")) == (18, 12, 6),
            "Independent account reuse/execution counts differ")
    require(accounts["inputValidationSha256"] == sha(OUT / "input-validation.json"), "Account input audit changed")
    require(accounts["parentInputValidationSha256"] == sha(PARENT / "input-validation.json")
            and accounts["parentValidationSha256"] == sha(PARENT / "validation.json"), "Reused baseline audit differs")
    for proof in (accounts, readout):
        require(proof["comparisonSha256"] == sha(OUT / "comparison.json"), "Comparison binding differs")
    require(readout["readoutSha256"] == sha(OUT / "readout.json")
            and readout["accountValidationSha256"] == sha(OUT / "validation.json"), "Readout audit bindings differ")
    require(readout["verifierSha256"] == READOUT_VERIFIER_SHA, "Registered independent readout verifier differs")
    return proofs


def validate_selection_shape(diagnostic):
    require(diagnostic["schema"] == "strength-feedback-selection-diagnostic-v1" and diagnostic["passed"] is True,
            "Selection diagnostic did not pass")
    require(all(type(diagnostic[key]) is int and diagnostic[key] == 0
                for key in ("newSignals", "newAccounts", "parameterChanges")), "Diagnostic changed registered trial scope")
    groups, reconciliations = diagnostic["rows"], diagnostic["reconciliation"]
    require(len(groups) == 18 and {(r["period"], r["board"], r["positiveAdmission"]) for r in groups}
            == {(p, b, state) for p in PERIODS for b in BOARDS for state in ("included", "excluded", "unknown")},
            "Diagnostic must retain all three baseline selection groups per source")
    require(len(reconciliations) == 6 and {(r["period"], r["board"]) for r in reconciliations}
            == {(p, b) for p in PERIODS for b in BOARDS} and all(r["passed"] is True for r in reconciliations),
            "Diagnostic source reconciliation missing")


def validate_selection_diagnostic(result):
    diagnostic = read(OUT / "selection-diagnostic.json")
    validate_selection_shape(diagnostic)
    require(diagnostic["scriptSha256"] == sha(ROOT / DIAGNOSTIC) == DIAGNOSTIC_SHA,
            "Selection diagnostic script changed")
    require(diagnostic["readoutSha256"] == sha(OUT / "readout.json")
            and diagnostic["readoutValidationSha256"] == sha(OUT / "readout-validation.json"),
            "Selection diagnostic does not bind the audited readout")
    require(diagnostic["opportunityRowsArtifact"] == result["opportunityRowsArtifact"],
            "Selection diagnostic used another opportunity population")
    bound(diagnostic["opportunityRowsArtifact"])
    return diagnostic


def validate_presentation():
    render = read(OUT / "render-receipt.json")
    pin_map(render["inputPins"], OUT)
    require(render["inputPins"].get("readout-validation.json") == sha(OUT / "readout-validation.json")
            and render["inputPins"].get("readout.json") == sha(OUT / "readout.json"), "Presentation must bind independently audited readout")
    require(render["builderSha256"] == sha(ROOT / RENDERER), "Renderer changed")
    fragments = {key: bound(value, OUT) for key, value in render["fragments"].items()}
    require(set(fragments) == {"chart", "sources"}, "Chart and Sources required")
    require(render["chartSha256"] == sha(fragments["chart"]) and render["sourceSha256"] == sha(fragments["sources"]), "Fragment hashes differ")
    require(render["tableRows"] == TABLE_ROWS and render["chartRows"] == 6, "Complete Sources/primary-difference coverage missing")
    visual = read(OUT / "visual-review.json")
    require(visual["passed"] is True and visual["images"], "Root actual screenshot review has not passed")
    qa_path = bound({"path": visual["qaPath"], "sha256": visual["qaSha256"]})
    require(qa_path.is_relative_to(OUT), "QA belongs to another experiment")
    qa = read(qa_path)
    require(qa["passed"] is True and qa["renderReceiptSha256"] == sha(OUT / "render-receipt.json")
            and qa["scriptSha256"] == sha(ROOT / QA_SCRIPT), "QA is stale or failed")

    def qa_file(item):
        raw = item.get("path", item.get("file"))
        require(isinstance(raw, str), "QA file path missing")
        path = Path(raw)
        path = (path if path.is_absolute() else OUT / path).resolve()
        require(path.is_relative_to(OUT) and sha(path) == item["sha256"], "QA reference changed or escaped experiment")
        return path

    require(set(qa["fragments"]) == set(fragments), "QA did not cover both fragments")
    for key, path in fragments.items():
        require(qa_file(qa["fragments"][key]) == path, "QA tested a different fragment")
    images = qa.get("images", qa.get("screenshots", []))
    require(images, "QA screenshots missing")
    image_pins = {qa_file(item): item["sha256"] for item in images}
    for item in visual["images"]:
        require(image_pins.get(bound(item)) == item["sha256"], "Root-reviewed image not in the bound QA")
    return render, qa_path


def finalize(plan_sha):
    require(plan_sha == PLAN_SHA == sha(OUT / "plan.json"), "Explicit registered plan hash differs")
    require(sha(PROGRAM) == BEFORE, "Research registry changed; reconcile before writing")
    outputs = ("execution-sources.zip", "execution-sources-manifest.json", "program-before.json", "program-after.json", "delivery.json")
    require(not any((OUT / name).exists() for name in outputs), "Finalization outputs exist; no overwrite")
    pending = BASE / "program.strength-feedback-pending.json"
    require(not pending.exists(), "Pending registry output exists")
    plan, result, comparison, checks = (read(OUT / name) for name in
        ("plan.json", "readout.json", "comparison.json", "pre-account-tests.json"))
    require(sha(OUT / "pre-account-tests.json") == PRE_ACCOUNT_TESTS_SHA, "Original pre-account test receipt changed")
    require(plan["schema"] == "strength-feedback-v1" and result["planSha256"] == comparison["planSha256"] == plan_sha, "Wrong experiment binding")
    for pins in (plan["sourcePins"], plan["inputPins"], result["inputPins"], checks["pins"]):
        pin_map(pins)
    validate_tests(checks, plan_sha)
    audits = validate_audits(plan, plan_sha)
    validate_matrix(result)
    require(result["accountRows"] == comparison["rows"], "Report differs from the eighteen comparison accounts")
    opportunity_total = opportunity_count(result)
    require(all(audits[name]["counts"]["sourceControlOpportunities"] == opportunity_total
                for name in ("input-validation.json", "validation.json")), "Independently verified source-control population differs")
    require(audits["input-validation.json"]["counts"]["dailyRows"] == len(result["feedbackDailyRows"]),
            "Independently verified daily feedback coverage differs")
    readout_counts = audits["readout-validation.json"]["counts"]
    require(readout_counts["originalControlOpportunities"] == opportunity_total
            and readout_counts["policyDispositions"] == opportunity_total * 3
            and readout_counts["feedbackDays"] == len(result["feedbackDailyRows"]), "Independent readout population differs")
    require(all(readout_counts[key] == count for key, count in MATRIX.items())
            and readout_counts["fullCurveIntervals"] == 18 and readout_counts["subIntervals"] == 270,
            "Independent readout matrix differs")
    sidecar = bound(result["opportunityRowsArtifact"])
    require(sidecar == OUT / "opportunity-dispositions.jsonl.gz"
            and result["opportunityRowsArtifact"]["policiesPerRow"] == list(MODES), "Opportunity sidecar identity differs")
    require(audits["readout-validation.json"]["opportunityRowsArtifactSha256"] == sha(sidecar), "Sidecar audit binding differs")
    require(result["verification"]["passed"] is True and bound(result["verification"]) == OUT / "validation.json", "Wrong account audit in readout")
    validate_selection_diagnostic(result)
    for p in PERIODS:
        for b in BOARDS:
            refs = comparison["artifacts"][p][b]["baseline"]
            require(refs == plan["baselineArtifacts"][p][b], "Baseline was not reused byte-for-byte")
            for item in refs.values():
                bound(item)
    render, qa_path = validate_presentation()
    program = read(PROGRAM)
    progress = program["progress"]
    require(progress["completedFixedCandidateTrials"] == 34 and progress["currentScopeTestsPassed"] == 938
            and "strengthFeedback" not in progress and program["goalStatus"] == "active", "Registry state differs")
    observed_findings = findings(result)
    observed_findings.append("The selection diagnostic groups observed baseline closed episodes by the frozen positive-policy "
        "included/excluded/unknown decision and reconciles all six sources. Excluded baseline PnL is not the new strategy's "
        "return, an achievable avoided loss or an independent causal effect. The diagnostic adds zero signals, accounts or parameter changes.")
    now = datetime.now(timezone.utc).isoformat()
    source_names = set(plan["sourcePins"]) | set(checks["pins"]) | {
        VERIFIER, READOUT_VERIFIER, SUMMARIZER, RENDERER, QA_SCRIPT, DIAGNOSTIC,
        "docs/signal-strength-feedback-v1.json", Path(__file__).relative_to(ROOT).as_posix()}
    for proof in audits.values():
        source_names.update(proof.get("independentReferences", {}))
    for name in result["inputPins"]:
        if Path(name).suffix.lower() in (".py", ".cjs", ".mjs", ".js"):
            source_names.add(name)
    # Include any authorized presentation repair/visual-review source, while
    # result/failure artifacts remain separately hash-bound in delivery.files.
    source_names.update(path.relative_to(ROOT).as_posix() for path in OUT.glob("*.py"))
    source_names = sorted(source_names)
    require(all((ROOT / name).resolve().is_relative_to(ROOT) and (ROOT / name).is_file() for name in source_names), "Execution dependency missing")
    source_pins = {name: sha(ROOT / name) for name in source_names}
    with zipfile.ZipFile(OUT / "execution-sources.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_names:
            archive.write(ROOT / name, name)
    with zipfile.ZipFile(OUT / "execution-sources.zip") as archive:
        require(set(archive.namelist()) == set(source_names) and len(archive.namelist()) == len(source_names), "Archive inventory differs")
        require(all(hashlib.sha256(archive.read(name)).hexdigest() == value for name, value in source_pins.items()), "Archived code hash differs")
    write(OUT / "execution-sources-manifest.json", {"createdAt": now, "planSha256": plan_sha, "sourcePins": source_pins,
        "archive": ref(OUT / "execution-sources.zip"), "meaning": "Registered code and executed audit/readout/display helpers; datasets and outputs remain receipt-bound rather than copied into the source archive."})
    progress["strengthFeedback"] = {"status": "completed-development-feedback-comparison-independently-verified-not-promoted",
        "directory": OUT.name, "planSha256": plan_sha, "comparisonSha256": sha(OUT / "comparison.json"),
        "readoutSha256": sha(OUT / "readout.json"), "auditRefs": {name: ref(OUT / name) for name in audits},
        "trialAccounting": TRIALS, "accountRows": result["accountRows"], "comparisons": result["comparisons"],
        "coverageRows": result["coverageRows"], "formationCoverageRows": result["formationCoverageRows"],
        "feedbackCoverageRows": result["feedbackCoverageRows"], "curveEpochRows": result["curveEpochRows"],
        "opportunityRowsArtifact": result["opportunityRowsArtifact"], "inputCountsByPeriodBoard": result["inputCountsByPeriodBoard"],
        "findings": observed_findings, "limitations": result["limitations"], "preAccountTests": ref(OUT / "pre-account-tests.json"),
        "newProductionUnitTests": 20, "selfTestsExcludedFromProductionUnitCount": True,
        "selectionDiagnostic": ref(OUT / "selection-diagnostic.json"),
        "selectedWinner": None, "holdoutClaim": False, "certified": False, "targetAchieved": False}
    progress["completedFixedCandidateTrials"], progress["currentScopeTestsPassed"] = 35, 958
    progress["currentScopeTestEvidence"] += (
        " Strength-feedback adds one batch, no new setup or universe, two admission policies and twelve new accounts "
        "plus six byte-reused baselines. Twenty actual production tests raise938 to958; independent audit/readout/render/finalizer "
        "synthetic checks and implementation-reference cross-checks are excluded from that distinct production count.")
    progress["lastTurnClassification"] = (
        "progress: completed twelve strength-feedback accounts with six unchanged baseline references; input/account/readout "
        "audits and browser/root visual review passed, all eighteen contrasts and all chronological partitions retained; "
        "no policy or board promoted, no stable-profit or fresh-holdout claim.")
    progress["nextStage"] = (
        "先对照六个来源的positive-known、known-baseline和positive-baseline全账户差，分别解释反馈选择和"
        "状态可判定性、暴露与资金路径、闭仓分布、期末浮盈、费用及估值质量。已知多数状态不等于"
        "成员完全可观察，零信号空仓不能证明小亏大赚。保留所有年份和阶段，不按较好板块或事后阶段"
        "添加开关；下一机制仍需先查重并冻结，再运行完整账户，无固定收益门槛，不自动指定下一策略，目标保持active。")
    program.update(updatedAt=now, updatedDate=datetime.now(timezone.utc).date().isoformat(), goalStatus="active")
    with (OUT / "program-before.json").open("xb") as stream:
        stream.write(PROGRAM.read_bytes())
    require(sha(OUT / "program-before.json") == BEFORE, "Registry changed before backup")
    write(OUT / "program-after.json", program)
    with pending.open("xb") as stream:
        stream.write((OUT / "program-after.json").read_bytes())
    pin_map(source_pins)
    require(sha(PROGRAM) == BEFORE and sha(OUT / "plan.json") == plan_sha, "Registry/plan changed before replacement")
    os.replace(pending, PROGRAM)
    files = {path.relative_to(ROOT).as_posix(): sha(path) for path in OUT.rglob("*") if path.is_file()}
    write(OUT / "delivery.json", {"completedAt": now, "files": files,
        "programBeforeSha256": BEFORE, "programAfterSha256": sha(PROGRAM), "planSha256": plan_sha,
        "candidateBatchesBefore": 34, "candidateBatchesAfter": 35, "newAccounts": 12, "referenceAccountsReused": 6,
        "newSetupMechanisms": 0, "newEntryPolicies": 2, "newUniverseVariants": 0,
        "distinctNewUnitTests": 20, "currentScopeTestsPassed": 958, "originalControlOpportunities": opportunity_total,
        "preAccountTests": ref(OUT / "pre-account-tests.json"), "audits": {name: ref(OUT / name) for name in audits},
        "readout": ref(OUT / "readout.json"), "opportunities": ref(sidecar), "renderReceipt": ref(OUT / "render-receipt.json"),
        "selectionDiagnostic": ref(OUT / "selection-diagnostic.json"), "selectionDiagnosticScript": ref(ROOT / DIAGNOSTIC),
        "browserQa": ref(qa_path), "visualReview": ref(OUT / "visual-review.json"),
        "executionSources": ref(OUT / "execution-sources.zip"), "executionSourceManifest": ref(OUT / "execution-sources-manifest.json"),
        "actualDataPeriods": list(PERIODS), "data2026Included": False, "onlyRepositoryResearchFilesWritten": True,
        "userWorkspaceWrites": 0, "holdoutClaim": False, "certified": False, "targetAchieved": False, "goalStatus": "active"})
    return {"completed": True, "programSha256": sha(PROGRAM), "deliverySha256": sha(OUT / "delivery.json"),
            "candidateBatches": 35, "newAccounts": 12, "reusedAccounts": 6, "currentScopeTestsPassed": 958, "goalStatus": "active"}


def self_test():
    require(number(None) == number(float("nan")) == "unknown" and number(-1.234, "%") == "-1.23%", "Metric formatting")
    fixture = {key: [{} for _ in range(count)] for key, count in MATRIX.items()}
    fixture.update(trialAccounting=deepcopy(TRIALS), certified=False, targetAchieved=False,
        decision={key: None for key in ("selectedBoard", "selectedPolicy", "automaticNextStrategy", "profitHurdle")})
    account_ids = [{"period": p, "board": b, "policy": m} for p in PERIODS for b in BOARDS for m in MODES]
    for key in ("accounts", "accountRows", "coverageRows"):
        fixture[key] = deepcopy(account_ids)
    fixture["comparisons"] = [{"period": p, "board": b, "id": c, "control": pair[0], "candidate": pair[1]}
        for p in PERIODS for b in BOARDS for c, pair in CONTRASTS.items()]
    for key, contrast in (("primaryComparisons", "positiveVsKnown"), ("availabilityComparisons", "knownVsBaseline"), ("overallComparisons", "positiveVsBaseline")):
        fixture[key] = [r for r in fixture["comparisons"] if r["id"] == contrast]
    fixture["partitionReconciliation"] = [{"passed": True} for _ in range(18)]
    validate_matrix(fixture)
    for key, value in (("accounts", fixture["accounts"][:-1]), ("trialAccounting", {**TRIALS, "newSetupMechanisms": 1}),
                       ("primaryComparisons", []), ("targetAchieved", True)):
        try:
            validate_matrix({**fixture, key: value})
        except ValueError:
            pass
        else:
            raise AssertionError("Incomplete or scope-changing report accepted: " + key)
    dynamic = {"inputCountsByPeriodBoard": {p: {b: {"inputSignalCount": i * 3 + j + 1,
        "opportunityCount": i * 3 + j + 1, "targetDayCount": 2} for j, b in enumerate(BOARDS)} for i, p in enumerate(PERIODS)},
        "opportunityRowsArtifact": {"count": 21}, "formationCoverageRows": [{"formationTop5": n, "parentControlSignals": n} for n in range(1, 7)],
        "feedbackDailyRows": [{} for _ in range(12)]}
    require(opportunity_count(dynamic) == 21, "Dynamic original-control denominator")
    changed = deepcopy(dynamic)
    changed["formationCoverageRows"][0]["parentControlSignals"] += 1
    try:
        opportunity_count(changed)
    except ValueError:
        pass
    else:
        raise AssertionError("Changed original control population accepted")
    for row in fixture["accounts"]:
        row.update(comparisonRow={"accountReturnPct": -1., "maxDrawdownPct": 3., "meanExposurePct": None,
            "closed": 0, "openPositions": 0, "signals": 0, "unreliableValuationDays": 5},
            closedDistribution={"netPnlAmount": 0}, openPositionsAtCutoff={"unrealizedPnlAmount": 0, "realizedPnlWhileOpen": 0})
    for row in fixture["comparisons"]:
        row["candidateMinusControl"] = {"accountReturnPct": None, "maxDrawdownPct": -2., "meanExposurePct": 0}
    fixture["curveEpochRows"] = [{"board": b, "policy": m, "phaseId": e, "returnPct": None, "maxDrawdownPct": 4.}
        for b in BOARDS for m in MODES for e in ("before20240924", "from20240924")]
    messages = findings(fixture)
    require(sum("cumulative net return" in m for m in messages) == 18 and sum("candidate-control" in m for m in messages) == 18,
            "All signed/null accounts and contrasts retained")
    require(sum("continuous inherited equity" in m for m in messages) == 18
            and sum("Cash-only results" in m for m in messages) == 3, "Epochs and inactive policy interpretation")
    diagnostic = {"schema": "strength-feedback-selection-diagnostic-v1", "passed": True,
        "newSignals": 0, "newAccounts": 0, "parameterChanges": 0,
        "rows": [{"period": p, "board": b, "positiveAdmission": state}
            for p in PERIODS for b in BOARDS for state in ("included", "excluded", "unknown")],
        "reconciliation": [{"period": p, "board": b, "passed": True} for p in PERIODS for b in BOARDS]}
    validate_selection_shape(diagnostic)
    for key, value in (("newSignals", 1), ("newAccounts", True), ("parameterChanges", 1), ("rows", diagnostic["rows"][:-1])):
        try:
            validate_selection_shape({**diagnostic, key: value})
        except ValueError:
            pass
        else:
            raise AssertionError("Incomplete or scope-changing diagnostic accepted: " + key)
    return {"passed": True, "checks": 15, "persistentFilesWritten": 0, "programModified": False,
            "actualResultsRead": False, "meaning": "Synthetic finalizer guards only; no production unit-test credit."}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    if not args.self_test and not args.plan_sha256:
        parser.error("Finalization requires --plan-sha256 for the registered immutable plan")
    print(json.dumps(self_test() if args.self_test else finalize(args.plan_sha256), ensure_ascii=False))
