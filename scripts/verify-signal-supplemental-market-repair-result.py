#!/usr/bin/env python3
"""Independent supplemental repair/resilience replay; no production evaluator.

Frozen independent gate/lineage/outcome functions are reused privately. The
supplemental parent verifier supplies source/receipt preparation only, never
its outcome replay. Both reused files and their dependencies are hash bound.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import time

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-market-repair-v1.json"
REGISTRATION_SHA256 = "436dccdc20811fcbb38d3eeeaf99beb966e71c114ffe10996933a38ccd4315b8"
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/signal-supplemental-market-repair-v1"
OLD_VERIFIER = "scripts/verify-signal-market-repair-result.py"
OLD_VERIFIER_SHA256 = "922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf"
PARENT_VERIFIER = "scripts/verify-signal-supplemental-persistent-leader-result.py"
PARENT_VERIFIER_SHA256 = "52194e94a9f3b2d63bc8c75c4179d84a7bfd1dc2c930efc968390165d50fd848"
NEW_RUNNER = "scripts/run-signal-supplemental-market-repair.py"
NEW_VERIFIER = "scripts/verify-signal-supplemental-market-repair-result.py"
NEW_TESTS = "tests/test_signal_supplemental_market_repair.py"
MARKET_OUTPUTS = {"protocol.json", "execution-sources.zip", "attempt.json", "daily.jsonl.gz", "roles.jsonl.gz", "summary.json"}


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SHA256 = sha(VERIFIER_SOURCE_PATH)


def unique_pairs(values):
    out = {}
    for key, value in values:
        if key in out:
            raise ValueError("Duplicate JSON key: " + key)
        out[key] = value
    return out


def nonfinite(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique_pairs, parse_constant=nonfinite)


def path_under(root, name):
    item = PurePosixPath(name)
    if not item.parts or item.is_absolute() or ".." in item.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe artifact-relative path")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Artifact escapes root")
    return result


def inventory(directory):
    names = set()
    for path in [directory, *directory.rglob("*")]:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Artifact tree contains a link")
        if path.is_file():
            names.add(path.relative_to(directory).as_posix())
    return names


def private_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bind_parent(reference, helper, document, root, audit):
    """Verify the completed parent without redoing any candidate/outcome trial."""
    parent = document["parent"]
    directory = path_under(root, parent["completedArtifactDirectory"])
    audit.require(parent["actualAuditVerifier"] == {"path": PARENT_VERIFIER, "sha256": PARENT_VERIFIER_SHA256}, "supplemental-parent-independent-verifier-identity")
    helper.REGISTRATION = path_under(root, parent["registration"])
    helper.REGISTRATION_SHA256 = parent["registrationSha256"]
    parent_audit = helper.Audit()
    # prepare is exclusively binding + source loading. Do not call replay,
    # verify, a production parent module, or any detector/event calculator.
    prepared = helper.prepare(directory, None, root, parent_audit)
    audit.checks += parent_audit.checks
    audit.failure_count += parent_audit.failures
    audit.issues.extend(parent_audit.issues[:max(0, 100-len(audit.issues))])
    for path, expected in parent_audit.bound.items():
        audit.pin(path, expected)
    proof = read(path_under(root, parent["completedInputs"]["independent-result-verification.json"]["path"]))
    receipt, protocol = prepared["receipt"], prepared["protocol"]
    audit.require(proof.get("schema") == "supplemental-persistent-leader-independent-verification-v1" and proof.get("passed") is True and
                  type(proof.get("failureCount")) is int and proof["failureCount"] == 0 and proof["verifierSha256"] == PARENT_VERIFIER_SHA256 and
                  proof["reusedIndependentVerifierSha256"] == reference.OLD_AUDITOR_SHA256, "completed-parent-independent-audit-passed")
    audit.require(proof["protocolHash"] == prepared["protocolHash"] == parent["protocolHash"] and
                  proof["completionSha256"] == parent["completedInputs"]["completion.json"]["sha256"] and
                  proof["resultSha256"] == parent["completedOutputs"]["result"]["sha256"] and
                  reference.exact(proof["implementation"], protocol["implementation"]) and
                  reference.exact(proof["resultFileHashes"], {v["path"]: v["sha256"] for v in receipt["files"].values()}), "parent-completion-result-execution-audit-chain")
    audit.require(type(proof["evaluatedRows"]) is int and proof["evaluatedRows"] == document["sample"]["expectedFeatureRows"] and
                  type(proof["targetSessions"]) is int and proof["targetSessions"] == document["sample"]["expectedScoredSessions"] and
                  proof["sourceFingerprint"] == document["sample"]["expectedFullSnapshotFingerprint"] and
                  proof["certified"] is False and proof["targetAchieved"] is False, "parent-audit-complete-target-population")
    audit.require(set(parent["completedInputs"]) == {"protocol.json", "snapshot.json", "selection.json", "completion.json", "independent-result-verification.json"} and
                  set(parent["completedOutputs"]) == set(helper.ATTEMPT_FILES) and
                  set(parent["additionalInputs"]) == {"rootExecutionSources", "freezeAttempt", "events", "attempt"}, "complete-parent-input-output-reference-sets")
    input_paths = {"protocol.json": directory / "protocol.json", "snapshot.json": directory / "snapshot.json", "selection.json": directory / "selection.json",
        "completion.json": prepared["attempt"] / "completion.json", "independent-result-verification.json": directory / "independent-result-verification.json"}
    extra_paths = {"rootExecutionSources": directory / "execution-sources.zip", "freezeAttempt": directory / "freeze-attempt.json",
                   "events": directory / "events.jsonl", "attempt": prepared["attempt"] / "attempt.json"}
    for label, expected in {**input_paths, **extra_paths}.items():
        entry = parent["completedInputs"].get(label, parent["additionalInputs"].get(label))
        audit.require(path_under(root, entry["path"]) == expected and sha(expected) == entry["sha256"], "parent-exact-reference-path:" + label)
    for key, item in receipt["files"].items():
        entry = parent["completedOutputs"][key]
        audit.require(path_under(root, entry["path"]) == path_under(directory, item["path"]) and entry["sha256"] == item["sha256"], "parent-output-receipt-reference:" + key)
    for key in ("fixedPopulation", "sourceMaterialization", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit", "sample", "eventPolicy", "auditAndEvaluation", "exposure", "downstreamPolicy"):
        audit.require(reference.exact(document[key], prepared["document"][key]), "parent-exact-inherited-field:" + key)
    audit.require(reference.exact(document["frozenExecutionConfig"], protocol["config"]) and reference.digest(protocol["config"]) == document["executionConfigHash"] and
                  reference.exact(parent["sourceImplementation"], protocol["implementation"]), "parent-unchanged-full-config-and-implementation")
    raw = document["originalRawSourcePanelBinding"]
    audit.require(reference.exact(raw["identity"], prepared["panelIdentity"]) and raw["calendarHash"] == prepared["panelIdentity"]["calendarHash"] and
                  raw["symbolsSha256"] == prepared["manifest"]["inputs"]["symbolsSha256"] and raw["targetDates"] == prepared["dates"], "exact-parent-feature-calendar-identity")
    audit.require(set(raw["files"]) == helper.PANEL_FILES | {"completion.json", "independent-verification.json"}, "complete-raw-panel-reference-set")
    for name, spec in raw["files"].items():
        audit.require(path_under(root, spec["path"]) == prepared["panel"] / name and spec["sha256"] == sha(prepared["panel"] / name), "raw-panel-reference:" + name)
    audit.require(raw["snapshot"] == parent["completedInputs"]["snapshot.json"] and raw["sourceSelection"] == parent["completedInputs"]["selection.json"], "parent-raw-snapshot-selection-reference")
    return prepared


def bind_market(reference, document, prepared, root, audit):
    market, raw, sample = document["marketInput"], document["originalRawSourcePanelBinding"], document["sample"]
    directory = path_under(root, market["directory"])
    audit.require(set(market["outputs"]) == MARKET_OUTPUTS and inventory(directory) == MARKET_OUTPUTS | {"completion.json", "independent-verification.json"}, "exact-completed-market-file-tree")
    for name, item in market["outputs"].items():
        audit.require(path_under(root, item["path"]) == directory / name, "market-output-path:" + name)
    audit.require(path_under(root, market["completion"]["path"]) == directory / "completion.json" and
                  path_under(root, market["actualAudit"]["path"]) == directory / "independent-verification.json", "market-completion-audit-paths")
    protocol, receipt, proof = [read(directory / name) for name in ("protocol.json", "completion.json", "independent-verification.json")]
    audit.require(reference.digest({k: v for k, v in protocol.items() if k != "protocolHash"}) == protocol["protocolHash"] == market["protocolHash"] == receipt["protocolHash"] == proof["protocolHash"], "market-protocol-receipt-audit-identity")
    expected_results = {name: item["sha256"] for name, item in market["outputs"].items()}
    expected_results["completion.json"] = market["completion"]["sha256"]
    audit.require(proof.get("schema") == "supplemental-market-state-independent-verification-v1" and proof.get("passed") is True and
                  type(proof.get("failureCount")) is int and proof["failureCount"] == 0 and proof["verifierSha256"] == market["verifier"]["sha256"] and
                  type(proof["dailyRows"]) is int and proof["dailyRows"] == sample["expectedScoredSessions"] and
                  type(proof["roleRows"]) is int and proof["roleRows"] == sample["expectedFeatureRows"] and
                  reference.exact(proof["resultFileHashes"], expected_results), "market-independent-seven-file-audit")
    audit.require(set(receipt["files"]) == MARKET_OUTPUTS and all(reference.exact(receipt["files"][name], {"path": name, "sha256": item["sha256"]}) for name, item in market["outputs"].items()), "market-six-file-completion")
    audit.require(reference.exact(protocol["implementation"], market["implementation"]) and reference.exact(proof["implementation"], market["implementation"]), "market-audit-exact-implementation")
    expected_raw = {name: value["sha256"] for name, value in raw["files"].items()}
    audit.require(protocol["dates"] == prepared["dates"] and protocol["symbols"] == prepared["symbols"] and protocol["calendarHash"] == raw["calendarHash"] and
                  protocol["inputManifestSha256"] == proof["inputManifestSha256"] == expected_raw["manifest.json"] and
                  reference.exact(protocol["inputFilesSha256"], expected_raw) and receipt["dailyRows"] == len(prepared["dates"]) and
                  receipt["roleRows"] == len(prepared["dates"])*len(prepared["symbols"]), "market-original-panel-population-binding")
    registration = read(path_under(root, market["registration"]["path"]))
    audit.require(reference.exact(protocol["registration"], registration) and protocol["registrationSha256"] == market["registration"]["sha256"] and
                  registration["fixedPopulation"] == document["fixedPopulation"] and registration["sourcePanelRegistration"] == document["sourcePanelRegistration"] and
                  registration["sourcePanelVerifier"] == document["sourcePanelVerifier"] and registration["sourcePanelClosureAudit"] == document["sourcePanelClosureAudit"] and
                  reference.exact(proof["sourcePanelClosureAudit"], document["sourcePanelClosureAudit"]), "market-new-registration-feature-dual-audit-chain")
    for item in (protocol, receipt, proof):
        audit.require(item.get("returnsCalculated") is False and type(item.get("newStrategyTrials")) is int and item["newStrategyTrials"] == 0 and
                      item.get("certified") is False and item.get("targetAchieved") is False, "market-observations-only-flags")
    audit.require(protocol["executionArchiveSha256"] == market["outputs"]["execution-sources.zip"]["sha256"], "market-whole-archive-sha")
    reference.archive(audit, directory / "execution-sources.zip", market["implementation"], root)
    return protocol


def bind_sources(reference, helper, root, audit):
    registration = Path(REGISTRATION).resolve()
    audit.pin(registration, REGISTRATION_SHA256)
    audit.require(REGISTRATION_SHA256 is not None, "registration-must-be-frozen")
    document = read(registration)
    audit.require(document["candidateId"] == reference.CANDIDATE_ID and document["status"] == "rules-frozen-before-first-supplemental-repair-outcomes", "one-frozen-supplemental-repair-rule")
    for item in reference.refs(document):
        audit.pin(path_under(root, item["path"]), item["sha256"])
    audit.pin(path_under(root, document["parent"]["registration"]), document["parent"]["registrationSha256"])
    audit.require(document["frozenIndependentVerifier"] == {"path": OLD_VERIFIER, "sha256": OLD_VERIFIER_SHA256}, "unchanged-repair-independent-formulas")
    original = read(path_under(root, document["frozenRegistration"]["path"]))
    for key in ("candidateId", "objective", "decisionTiming", "marketRepair", "resilience", "gateSemantics", "selectionAndReplay", "apiContract", "requiredRecords", "comparisonPlan"):
        audit.require(reference.exact(document[key], original[key]), "unchanged-repair-rule-field:" + key)
    audit.require(reference.exact(document["runPolicy"], {"freshOwnActiveGroupsAtStart": True, "carryAcrossYearBoundary": True,
        "targetOnlyGateHistory": True, "firstTenTargetSessionsUnknown": True, "automaticRetry": False,
        "completeSourceAndExecutableBeforeFirstGate": True, "completedOnlyVerifies": True, "exclusiveFreeze": True}), "unchanged-target-only-startup-and-active-policy")
    for name, expected in document["frozenMethodImplementation"].items():
        audit.pin(path_under(root, name), expected)
    prepared = bind_parent(reference, helper, document, root, audit)
    market = bind_market(reference, document, prepared, root, audit)
    return document, prepared, market


def registered_input_files(reference, document, prepared, root, audit):
    """Reconstruct the producer's declared input set, separately from audit pins."""
    values = {str(Path(REGISTRATION).resolve()): REGISTRATION_SHA256}

    def add(path, expected):
        path = Path(path).resolve()
        audit.require(str(path) not in values or values[str(path)] == expected, "nonconflicting-input-pins")
        audit.pin(path, expected)
        values[str(path)] = expected

    def ref(item):
        add(path_under(root, item["path"]), item["sha256"])

    parent, market, raw = document["parent"], document["marketInput"], document["originalRawSourcePanelBinding"]
    for mapping in (parent["completedInputs"], parent["completedOutputs"], parent["additionalInputs"], market["outputs"], raw["files"]):
        for item in mapping.values():
            ref(item)
    for item in (market["completion"], market["actualAudit"], document["designSource"], document["registrationGenerator"],
                 parent["actualAuditVerifier"], market["registration"], market["verifier"], raw["snapshot"], raw["sourceSelection"], *document["sourceIntegrityPins"].values()):
        ref(item)
    add(path_under(root, parent["registration"]), parent["registrationSha256"])
    for field in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        ref(document[field])
    source_protocol = prepared["source"]["protocol"]
    for mapping in (parent["sourceImplementation"], market["implementation"], source_protocol["inputFilesSha256"], source_protocol["implementation"], source_protocol["metadata"]):
        for name, expected in mapping.items():
            add(path_under(root, name), expected)
    for name, expected in document["sourceMaterialization"]["files"].items():
        add(path_under(prepared["source"]["directory"], name), expected)
    for name, expected in prepared["manifest"]["physicalSourceHashes"].items():
        add(Path(name), expected)
    closure = read(path_under(root, document["sourcePanelClosureAudit"]["path"]))
    add(path_under(root, closure["verifierPath"]), closure["verifierSha256"])
    return values, closure


def bind_execution(reference, helper, document, prepared, market, output, root, audit):
    files, closure = registered_input_files(reference, document, prepared, root, audit)
    binding = {"registrationSha256": REGISTRATION_SHA256, "inputFilesSha256": files,
        "parentProtocolHash": document["parent"]["protocolHash"], "marketProtocolHash": document["marketInput"]["protocolHash"],
        "snapshotFingerprint": document["sample"]["expectedFullSnapshotFingerprint"],
        "calendarHash": document["originalRawSourcePanelBinding"]["calendarHash"],
        "sourceMaterialization": prepared["source"]["identity"], "panelBinding": prepared["panelIdentity"]}
    protocol = read(output / "protocol.json")
    audit.require(protocol["schema"] == "supplemental-market-repair-v1" and protocol["stage"] == "qualified-supplemental-validation" and
                  reference.digest({k: v for k, v in protocol.items() if k != "protocolHash"}) == protocol["protocolHash"], "canonical-supplemental-protocol")
    audit.require(reference.exact(protocol["registration"], document) and protocol["registrationSha256"] == REGISTRATION_SHA256 and
                  reference.exact(protocol["binding"], binding) and reference.exact(protocol["config"], document["frozenExecutionConfig"]) and
                  Path(protocol["workspace"]).resolve() == prepared["source"]["directory"] / "workspace" and
                  protocol["strategyOrder"] == [reference.CANDIDATE_ID] and protocol["groups"] == list(reference.GROUPS) and
                  protocol["pilot"] is False and protocol["certified"] is False and protocol["targetAchieved"] is False and
                  reference.exact(protocol["exposure"], document["exposure"]) and reference.exact(protocol["downstreamPolicy"], document["downstreamPolicy"]), "exact-protocol-input-config-and-groups")
    source_protocol = prepared["source"]["protocol"]
    implementation_names = set(document["parent"]["sourceImplementation"]) | set(document["frozenMethodImplementation"]) | set(document["marketInput"]["implementation"]) | \
        set(source_protocol["implementation"]) | set(source_protocol["metadata"]) | {
            Path(REGISTRATION).resolve().relative_to(root).as_posix(), NEW_RUNNER, NEW_VERIFIER, NEW_TESTS, closure["verifierPath"]}
    implementation_names.update(document[field]["path"] for field in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"))
    audit.require(set(protocol["implementation"]) == implementation_names and protocol["implementation"][NEW_VERIFIER] == VERIFIER_SHA256 and
                  protocol["implementation"][OLD_VERIFIER] == OLD_VERIFIER_SHA256 and protocol["implementation"][PARENT_VERIFIER] == PARENT_VERIFIER_SHA256,
                  "exact-new-and-reused-execution-closure")
    for name, expected in document["frozenMethodImplementation"].items():
        audit.require(protocol["implementation"].get(name) == expected, "frozen-repair-implementation:" + name)
    audit.pin(output / "execution-sources.zip", protocol["executionArchiveSha256"])
    reference.archive(audit, output / "execution-sources.zip", protocol["implementation"], root)
    attempt = output / "attempts/attempt-0001"
    receipt = read(attempt / "completion.json")
    expected_paths = {key: "attempts/attempt-0001/" + name for key, name in reference.ATTEMPT_FILES.items()}
    expected_paths.update(executionSources="execution-sources.zip", protocol="protocol.json")
    audit.require(set(receipt["files"]) == set(expected_paths) and receipt["protocolHash"] == protocol["protocolHash"] and
                  receipt["snapshotFingerprint"] == binding["snapshotFingerprint"] and type(receipt["attempt"]) is int and receipt["attempt"] == 1 and
                  receipt["certified"] is False and receipt["targetAchieved"] is False, "complete-eleven-artifact-receipt")
    for key, name in expected_paths.items():
        item = receipt["files"][key]
        audit.require(reference.exact(item, {"path": name, "sha256": sha(path_under(output, name))}), "exact-completed-artifact:" + key)
        audit.pin(path_under(output, name), item["sha256"])
    audit.pin(attempt / "completion.json")
    freeze = read(output / "freeze-attempt.json")
    metadata = read(attempt / "attempt.json")
    audit.require(protocol["freezeAttemptSha256"] == receipt["freezeAttemptSha256"] == sha(output / "freeze-attempt.json") and
                  freeze["registrationSha256"] == REGISTRATION_SHA256 and freeze["candidateId"] == reference.CANDIDATE_ID and
                  freeze["stage"] == protocol["stage"] and freeze["certified"] is False and freeze["targetAchieved"] is False,
                  "pre-execution-freeze-receipt")
    audit.require(metadata["protocolHash"] == protocol["protocolHash"] and metadata["candidateId"] == reference.CANDIDATE_ID and
                  type(metadata["attempt"]) is int and metadata["attempt"] == 1 and metadata["certified"] is False and metadata["targetAchieved"] is False and
                  datetime.fromisoformat(freeze["startedAt"]) <= datetime.fromisoformat(metadata["startedAt"]), "one-attempt-after-freeze")
    tree = inventory(output)
    reports = {name for name in tree if "/" not in name and (name == "independent-result-verification.json" or
               (name.startswith("independent-result-verification-failed-") and name.endswith(".json")))}
    expected_tree = set(expected_paths.values()) | {"freeze-attempt.json", "events.jsonl", "attempts/attempt-0001/completion.json"}
    audit.require(tree - reports == expected_tree, "exact-completed-output-tree-no-other-attempt")
    for name in expected_tree:
        audit.pin(path_under(output, name))
    journal = [json.loads(line, object_pairs_hook=unique_pairs, parse_constant=nonfinite) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    audit.require([r["event"] for r in journal] == ["study_started", "filtering_completed", "study_succeeded"] and
                  all(r["protocolHash"] == protocol["protocolHash"] for r in journal) and reference.exact(journal[-1]["receipt"], receipt), "single-success-lifecycle-events")
    return protocol, receipt, attempt, tree


def replay(reference, helper, document, prepared, protocol, receipt, attempt, root, audit):
    signals, accounting, gates, parent_count, extras = reference.replay_filters(document, root, attempt, audit)
    result, summary = reference.gz_json(attempt / "result.json.gz"), read(attempt / "summary.json")
    expected_summary = {k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")}
    audit.require(set(summary) == set(expected_summary) | {"performance", "ranking", "selectionEligible"} and
                  reference.exact({k: summary[k] for k in expected_summary}, expected_summary), "complete-summary-result-match")
    audit.require(result["protocolHash"] == protocol["protocolHash"] and result["snapshotFingerprint"] == prepared["dataset"]["fingerprint"] and
                  result["parentProtocolHash"] == document["parent"]["protocolHash"] and result["strategyOrder"] == [reference.CANDIDATE_ID] and
                  result["stage"] == protocol["stage"] and result["certified"] is False and result["targetAchieved"] is False and
                  result["pilot"] is False and result["diagnosticUnverifiedHistory"] is True and
                  summary["ranking"] is None and summary["selectionEligible"] is False, "result-registered-identity-and-no-certification")
    helper.numeric_metadata(result, audit)
    for group in reference.GROUPS:
        accounting[group].update(newAttemptedEvents=sum(e["strategyId"] == group for e in result["events"]),
            newActiveSuppressed=sum(e["strategyId"] == group for e in result["overlapSuppressed"]))
        counts = accounting[group]
        audit.check(counts["parentSignals"] == sum(counts[k] for k in ("gateTrueAdmitted", "gateFalseFiltered", "gateUnknownFiltered")) and
                    counts["gateTrueAdmitted"] == counts["newAttemptedEvents"] + counts["newActiveSuppressed"], "complete-filter-disposition-accounting")
    audit.check(reference.exact(accounting, result["filterAccounting"]) and reference.exact(gates, result["marketGateCounts"]) and
                reference.exact(summary["performance"]["filterAccounting"], accounting) and reference.exact(summary["performance"]["marketGateCounts"], gates) and
                type(summary["performance"]["scoredSessions"]) is int and summary["performance"]["scoredSessions"] == len(prepared["dates"]),
                "all-market-and-filter-accounting")
    for key, value in extras.items():
        audit.check(reference.exact(result[key], value) and reference.exact(summary["performance"][key], value), "complete-role-and-compound-accounting:" + key)
    # Extra membership/uniqueness guards precede the frozen independent active
    # and outcome audit, which starts empty once for the complete target period.
    expected_groups = set(reference.GROUPS)
    audit.require(all(s["strategyId"] in expected_groups and s["signalDate"] in prepared["dates"] and s["symbol"] in prepared["symbols"] for s in signals), "admitted-signal-frame")
    for key in ("events", "overlapSuppressed"):
        audit.require(len(result[key]) == len({reference.identity(r) for r in result[key]}), "unique-signal-dispositions:" + key)
    result_counts = reference.event_audit(audit, result, signals, prepared["dataset"], document, root)
    return {**result_counts, "dailyRows": len(prepared["dates"]), "parentSignals": parent_count,
            "admittedSignals": len(signals), "marketGateCounts": gates, "sourceFingerprint": prepared["dataset"]["fingerprint"], **extras}


def verify(output: Path, root: Path = ROOT) -> dict:
    """Return a read-only report; no production strategy is evaluated."""
    output, root = Path(output).resolve(), Path(root).resolve()
    started = time.perf_counter()
    report = {"schema": "supplemental-market-repair-independent-verification-v1", "checkedAt": datetime.now(timezone.utc).isoformat(),
        "output": str(output), "verifierSha256": VERIFIER_SHA256, "registrationSha256": REGISTRATION_SHA256,
        "reusedIndependentVerifierSha256": OLD_VERIFIER_SHA256, "reusedParentBindingVerifierSha256": PARENT_VERIFIER_SHA256,
        "dailyRows": 0, "parentSignals": 0, "admittedSignals": 0, "certified": False, "targetAchieved": False,
        "scope": ["Unchanged frozen independent six-target-session repair gate, prior-five-role resilience, unknown precedence, earliest index ten, full ordered market/role EOF, all parent candidates and original-rank mirrored control lineage.",
            "Full admitted/filtered/unknown accounting, unique signal/event/suppression sets, fresh active replay across the year boundary, filled fee/slippage arithmetic, T+1 timing, raw/net-high observations, adverse paths and seven-horizon cohort arithmetic.",
            "Supplemental parent prepare is reused only for exact source/materialization/feature mathematical-plus-closure binding, parent receipt/root files and raw loading. No parent candidate or event evaluator is called; the completed parent audit is independently hash bound.",
            "Exact new implementation archive, new market seven-file independent-audit chain, immutable source/output identities and verifier source hashes before and after replay."],
        "limitations": ["Trusts the independently audited parent selection and source feature/market aggregates; does not independently reconstruct all historical feature ranks, base eligibility or the original eligible control pool.",
            "Does not independently prove complete buy/sell eligibility, maximum affordable/participation-limited order size or the first executable net opening target, including every omitted earlier opening target. High/path audits use the recorded main exit boundary.",
            "Capacity-validity evidence and source byte/SQLite quality scope are inherited from registered audited inputs. Arithmetic, daily high observations and raw adverse boundaries do not certify actual auction execution or corporate-action economic profit.",
            "Fixed survivor-biased population, known earlier exposure and correlated observations remain. This is not an untouched holdout, causal control pairing, market influence or future net +30 probability certificate.",
            "Synthetic success alone is not an actual-market verification."]}
    try:
        if sha(root / OLD_VERIFIER) != OLD_VERIFIER_SHA256 or sha(root / PARENT_VERIFIER) != PARENT_VERIFIER_SHA256:
            raise ValueError("Frozen independent helper changed")
        reference = private_module(root / OLD_VERIFIER, "_supplemental_repair_independent_reference")
        helper = private_module(root / PARENT_VERIFIER, "_supplemental_repair_parent_binding_reference")
    except Exception as error:
        report.update(passed=False, checks=1, failureCount=1, issues=[{"check": "frozen-reference-unavailable", "errorType": type(error).__name__, "message": str(error)}], elapsedSeconds=time.perf_counter()-started)
        return report
    audit = reference.Audit()
    try:
        audit.pin(VERIFIER_SOURCE_PATH, VERIFIER_SHA256)
        audit.pin(root / OLD_VERIFIER, OLD_VERIFIER_SHA256)
        audit.pin(root / PARENT_VERIFIER, PARENT_VERIFIER_SHA256)
        document, prepared, market = bind_sources(reference, helper, root, audit)
        protocol, receipt, attempt, tree = bind_execution(reference, helper, document, prepared, market, output, root, audit)
        report.update(protocolHash=protocol["protocolHash"], implementation=protocol["implementation"],
            resultSha256=receipt["files"]["result"]["sha256"], completionSha256=sha(attempt / "completion.json"),
            resultFileHashes={k: v["sha256"] for k, v in receipt["files"].items()},
            sourceMaterializationProtocolHash=document["sourceMaterialization"]["protocolHash"])
        report.update(replay(reference, helper, document, prepared, protocol, receipt, attempt, root, audit))
        audit.require(inventory(output) == tree, "output-tree-unchanged")
        audit.require(inventory(prepared["source"]["directory"]) == set(document["sourceMaterialization"]["files"]), "source-tree-unchanged")
        audit.require(inventory(prepared["panel"]) == helper.PANEL_FILES | {"completion.json", "independent-verification.json"}, "feature-panel-tree-unchanged")
        audit.require(inventory(path_under(root, document["marketInput"]["directory"])) == MARKET_OUTPUTS | {"completion.json", "independent-verification.json"}, "market-panel-tree-unchanged")
    except Exception as error:
        audit.check(False, "supplemental-repair-audit-exception", errorType=type(error).__name__, message=str(error))
    for path, expected in audit.bound.items():
        try:
            audit.check(sha(path) == expected, "post-audit-bound-file", path=str(path))
        except OSError as error:
            audit.check(False, "post-audit-missing-file", path=str(path), message=str(error))
    report.update(passed=audit.failure_count == 0, checks=audit.checks, failureCount=audit.failure_count, issues=audit.issues,
                  sourceHashes={str(path): value for path, value in audit.bound.items()}, elapsedSeconds=time.perf_counter()-started)
    return report


def save_report(output, report):
    name = "independent-result-verification.json" if report["passed"] else "independent-result-verification-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = Path(output) / name
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "failureCount": report["failureCount"], "report": str(path)}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
