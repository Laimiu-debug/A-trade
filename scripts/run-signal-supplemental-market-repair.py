#!/usr/bin/env python3
"""Unchanged market-repair method on the qualified supplemental interval.

Only immutable binding and the single-attempt lifecycle are new. The private
frozen repair module supplies every role/gate/rank/control/event operation.
No parent detector is rerun and no development active state is imported.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-market-repair-v1.json"
REGISTRATION_SHA256 = "436dccdc20811fcbb38d3eeeaf99beb966e71c114ffe10996933a38ccd4315b8"
SOURCE_WORKSPACE = ROOT / "artifacts/strategy-search/supplemental-source-2024-2025-v1/workspace"
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/signal-supplemental-market-repair-v1"
OLD_RUNNER = "scripts/run-signal-market-repair.py"
PARENT_RUNNER = "scripts/run-signal-supplemental-persistent-leader.py"
NEW_VERIFIER = "scripts/verify-signal-supplemental-market-repair-result.py"
NEW_TESTS = "tests/test_signal_supplemental_market_repair.py"


def _import(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _import("_supplemental_private_repair", OLD_RUNNER)
source_helper = _import("_supplemental_repair_source_binding", PARENT_RUNNER)
runtime, source_tools = legacy.runtime, legacy.source_tools
CANDIDATE_ID, PARENT_ID, GROUPS = legacy.CANDIDATE_ID, legacy.PARENT_ID, legacy.GROUPS
GROUP_MAPPING, HORIZONS = legacy.GROUP_MAPPING, legacy.HORIZONS
PARENT_FILES, PARENT_INPUTS = legacy.PARENT_FILES, legacy.PARENT_INPUTS
MARKET_OUTPUTS, RAW_FILES = legacy.MARKET_OUTPUTS, legacy.RAW_FILES
ATTEMPT_FILES, RECEIPT_KEYS = legacy.ATTEMPT_FILES, legacy.RECEIPT_KEYS
sha, digest, same, require = legacy.sha, legacy.digest, legacy.same, legacy.require
read_json, write_json = legacy.read_json, legacy.write_json
_audit_ok = legacy._audit_ok
LOADED_SOURCE_SHA256 = sha(__file__)
LOADED_RUNNER_SHA256 = sha(legacy.__file__)
LOADED_PARENT_SHA256 = sha(source_helper.__file__)
STAGE = "qualified-supplemental-validation"


def _local_path(name):
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in name and ":" not in name,
            "Unsafe bound relative path")
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Bound file missing or outside project: " + name)
    return path


def _configure(document=None):
    legacy.ROOT = ROOT
    if document is not None:
        source_helper.ROOT = ROOT
        source_helper.REGISTRATION = _local_path(document["parent"]["registration"])
        source_helper.REGISTRATION_SHA256 = document["parent"]["registrationSha256"]
        source_helper.SOURCE_WORKSPACE = SOURCE_WORKSPACE


def registration():
    require(type(REGISTRATION_SHA256) is str and len(REGISTRATION_SHA256) == 64, "Supplemental repair registration is pending")
    require(sha(REGISTRATION) == REGISTRATION_SHA256, "Supplemental repair registration changed")
    require(sha(__file__) == LOADED_SOURCE_SHA256 and sha(legacy.__file__) == LOADED_RUNNER_SHA256 and
            sha(source_helper.__file__) == LOADED_PARENT_SHA256, "Loaded executable changed after import")
    document = read_json(REGISTRATION)
    require(document.get("status") == "rules-frozen-before-first-supplemental-repair-outcomes" and
            document.get("registeredAt") and document.get("candidateId") == CANDIDATE_ID, "Supplemental rule is not registered")
    for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        ref = document[key]
        require(sha(_local_path(ref["path"])) == ref["sha256"], "Frozen reference changed: " + key)
    require(document["frozenRunner"] == {"path": OLD_RUNNER, "sha256": LOADED_RUNNER_SHA256}, "Frozen repair runner differs")
    previous = read_json(_local_path(document["frozenRegistration"]["path"]))
    for key in ("candidateId", "objective", "decisionTiming", "marketRepair", "resilience", "gateSemantics",
                "selectionAndReplay", "apiContract", "requiredRecords", "comparisonPlan"):
        require(same(document[key], previous[key]), "Frozen repair mathematics changed: " + key)
    method = document["frozenMethodImplementation"]
    require(method.get(OLD_RUNNER) == LOADED_RUNNER_SHA256 and
            method.get(document["frozenRegistration"]["path"]) == document["frozenRegistration"]["sha256"], "Incomplete frozen repair closure")
    for name, expected in method.items():
        require(sha(_local_path(name)) == expected, "Frozen repair dependency changed: " + name)
    require(same(document["selectionAndReplay"]["groupMapping"], GROUP_MAPPING) and
            document["parent"]["candidateId"] == PARENT_ID and
            document["unchangedExecutionAndSample"]["horizonsSessions"] == list(HORIZONS), "Frozen candidate/groups/horizons changed")
    expected_policy = {"freshOwnActiveGroupsAtStart": True, "carryAcrossYearBoundary": True,
        "targetOnlyGateHistory": True, "firstTenTargetSessionsUnknown": True, "automaticRetry": False,
        "completeSourceAndExecutableBeforeFirstGate": True, "completedOnlyVerifies": True, "exclusiveFreeze": True}
    require(same(document.get("runPolicy"), expected_policy), "Supplemental execution policy differs")
    _configure(document)
    parent_document = source_helper.registration()
    for key in ("sample", "fixedPopulation", "sourceMaterialization", "sourcePanelRegistration", "sourcePanelVerifier",
                "sourcePanelClosureAudit", "eventPolicy", "auditAndEvaluation", "exposure", "downstreamPolicy"):
        require(same(document[key], parent_document[key]), "Supplemental parent contract differs: " + key)
    require(document["parent"]["sourceImplementation"].get(PARENT_RUNNER) == LOADED_PARENT_SHA256, "Parent binding code is not pinned")
    return document


def _check_archive(path, implementation):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == set(implementation), "Archive dependency population changed")
        for name, expected in implementation.items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected == sha(_local_path(name)), "Archived dependency changed: " + name)


def _archive(path, implementation):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, expected in implementation.items():
            content = _local_path(name).read_bytes()
            require(hashlib.sha256(content).hexdigest() == expected, "Code changed before archive")
            archive.writestr(name, content)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fail(output, phase, error, protocol_hash=None):
    write_json(output / "failure.json", {"phase": phase, "failedAt": _now(), "protocolHash": protocol_hash,
        "errorType": type(error).__name__, "error": str(error), "automaticRetryAllowed": False})


def load_binding():
    """Validate all immutable inputs without iterating market or signal rows."""
    doc = registration()
    files = {str(REGISTRATION.resolve()): REGISTRATION_SHA256}

    def pin(record):
        require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "Incomplete file pin")
        path, expected = _local_path(record["path"]), record["sha256"]
        require(isinstance(expected, str) and len(expected) == 64 and sha(path) == expected, "Bound file changed: " + record["path"])
        require(str(path) not in files or files[str(path)] == expected, "Conflicting file pins")
        files[str(path)] = expected
        return path

    parent, market, raw = doc["parent"], doc["marketInput"], doc["originalRawSourcePanelBinding"]
    require(set(parent["completedInputs"]) == PARENT_INPUTS and set(parent["completedOutputs"]) == PARENT_FILES, "Incomplete parent binding")
    require(set(market["outputs"]) == MARKET_OUTPUTS and set(raw["files"]) == RAW_FILES, "Incomplete panel binding")
    parent_paths = {k: pin(v) for k, v in {**parent["completedInputs"], **parent["completedOutputs"]}.items()}
    market_paths = {k: pin(v) for k, v in market["outputs"].items()}
    market_paths.update(completion=pin(market["completion"]), audit=pin(market["actualAudit"]))
    raw_paths = {k: pin(v) for k, v in raw["files"].items()}
    for record in (doc["designSource"], doc["registrationGenerator"], parent["actualAuditVerifier"],
                   market["registration"], market["verifier"], raw["snapshot"], raw["sourceSelection"], *doc["sourceIntegrityPins"].values()):
        pin(record)
    for name, expected in {**parent["sourceImplementation"], **market["implementation"]}.items():
        pin({"path": name, "sha256": expected})
    pp, pc, pa, ps = (read_json(parent_paths[n]) for n in
        ("protocol.json", "completion.json", "independent-result-verification.json", "snapshot.json"))
    mp, mc, ma = (read_json(market_paths[n]) for n in ("protocol.json", "completion", "audit"))
    manifest, rc, ra = (read_json(raw_paths[n]) for n in ("manifest.json", "completion.json", "independent-verification.json"))
    sample, inputs = doc["sample"], manifest["inputs"]
    dates, symbols = raw["targetDates"], inputs["symbols"]
    calendar = inputs["calendar"]["dates"]
    require(dates == sorted(set(dates)) and len(dates) == sample["expectedScoredSessions"] and
        all(date.fromisoformat(d).isoformat() == d for d in dates), "Target calendar changed")
    require(dates == [d for d in calendar if sample["signalStart"] <= d <= sample["signalEnd"]] and
        calendar == sorted(set(calendar)) and all(d <= sample["signalEnd"] for d in calendar), "Raw calendar or cutoff changed")
    require(symbols == sorted(set(symbols)) and len(symbols) == sample["expectedFullSecurities"] and
        set(inputs["securities"]) == set(symbols) and inputs["symbolCount"] == len(symbols) and
        digest(symbols) == inputs["symbolsSha256"] == raw["symbolsSha256"] and
        digest(calendar) == inputs["calendar"]["sha256"] == raw["calendarHash"], "Source population changed")
    fingerprint = sample["expectedFullSnapshotFingerprint"]
    require(inputs["snapshotFingerprint"] == ps["fingerprint"] == pc["snapshotFingerprint"] == fingerprint and
        inputs["retainedBarCount"] == ps["barCount"] == sample["expectedFullBars"] and ps["symbols"] == symbols,
        "Parent/raw snapshot binding differs")
    require(digest(pp) == pc["protocolHash"] == parent["protocolHash"] and
        pp["implementation"] == parent["sourceImplementation"] and pp["registrationSha256"] == parent["registrationSha256"] and
        pp["strategyOrder"] == [PARENT_ID] and pp["controlSeed"] == runtime.CONTROL_SEED, "Parent protocol differs")
    require(same(pp["config"], doc["frozenExecutionConfig"]) and digest(pp["config"]) == doc["executionConfigHash"], "Exact parent config changed")
    require(same(pp["registration"]["eventPolicy"], doc["eventPolicy"]) and same(pp["registration"]["auditAndEvaluation"], doc["auditAndEvaluation"]), "Parent event policy changed")
    require(set(pc["files"]) == PARENT_FILES, "Parent receipt is incomplete")
    for key, item in pc["files"].items():
        require((ROOT / parent["completedArtifactDirectory"] / item["path"]).resolve() == parent_paths[key] and
            item["sha256"] == parent["completedOutputs"][key]["sha256"], "Parent receipt path/hash differs")
    require(_audit_ok(pa) and pa["resultSha256"] == pc["files"]["result"]["sha256"] and
        pa["sourceFingerprint"] == fingerprint and pa["verifierSha256"] == parent["actualAuditVerifier"]["sha256"], "Parent actual audit is not accepted")
    require(same(pc["panelBinding"], raw["identity"]), "Parent raw panel identity differs")
    expected_raw = {name: rec["sha256"] for name, rec in raw["files"].items()}
    require(manifest["featureVersion"] == "daily-leadership-raw-v1" and _audit_ok(ra) and
        ra["sampleManifestHash"] == rc["sampleManifestHash"] == expected_raw["manifest.json"] and
        ra["featureRows"] == rc["featureRows"] == len(symbols) * len(dates) == sample["expectedFeatureRows"] and
        rc["targetSessions"] == len(dates), "Raw panel actual audit/population differs")
    require(set(rc["files"]) == RAW_FILES - {"completion.json", "independent-verification.json"}, "Raw panel receipt incomplete")
    for name, entry in rc["files"].items():
        require(entry == {"path": name, "sha256": expected_raw[name]}, "Raw panel receipt changed")
    require(digest({k: v for k, v in mp.items() if k != "protocolHash"}) == mp["protocolHash"] == market["protocolHash"] == mc["protocolHash"] == ma["protocolHash"], "Market protocol hash differs")
    require(mp["dates"] == dates and mp["symbols"] == symbols and mp["calendarHash"] == raw["calendarHash"] and
        mp["inputManifestSha256"] == ma["inputManifestSha256"] == expected_raw["manifest.json"] and
        mp["inputFilesSha256"] == expected_raw and mp["implementation"] == market["implementation"] and
        mp["registrationSha256"] == market["registration"]["sha256"], "Market source binding differs")
    require(_audit_ok(ma) and ma["verifierSha256"] == market["verifier"]["sha256"] and
        mc["roleRows"] == ma["roleRows"] == len(symbols) * len(dates) and mc["dailyRows"] == ma["dailyRows"] == len(dates), "Market actual audit is not accepted")
    require(set(mc["files"]) == MARKET_OUTPUTS and set(ma["resultFileHashes"]) == MARKET_OUTPUTS | {"completion.json"}, "Market receipt incomplete")
    for name in MARKET_OUTPUTS:
        expected = market["outputs"][name]["sha256"]
        require(mc["files"][name] == {"path": name, "sha256": expected} and ma["resultFileHashes"][name] == expected, "Market receipt/audit file changed")
    for obj in (mp, mc, ma):
        require(obj.get("returnsCalculated") is False and type(obj.get("newStrategyTrials")) is int and obj["newStrategyTrials"] == 0, "Market observation flags differ")
    require(mp["executionArchiveSha256"] == market["outputs"]["execution-sources.zip"]["sha256"], "Market archive whole hash differs")
    _check_archive(parent_paths["executionSources"], parent["sourceImplementation"])
    _check_archive(market_paths["execution-sources.zip"], market["implementation"])
    for name, expected in manifest.get("physicalSourceHashes", {}).items():
        path = Path(name).resolve()
        require(sha(path) == expected, "Physical source changed")
        files[str(path)] = expected
    # Supplemental chains are validated intact; no old audit shape is fabricated.
    parent_document = source_helper.registration()
    panel_binding = source_helper.verify_panel_binding(parent_document)
    source = panel_binding["source"]
    require(same(panel_binding["identity"], pc["panelBinding"]) and
        same(pp.get("panelBinding"), panel_binding["identity"]) and same(pa.get("panelBinding"), panel_binding["identity"]),
        "Extended parent panel/source binding differs")
    require(same(pp.get("registration"), parent_document) and same(ps.get("selection"), doc["sample"]["selection"]) and
        same(read_json(parent_paths["selection.json"]), doc["sample"]["selection"]) and
        same(pp.get("selection"), doc["sample"]["selection"]) and same(pp.get("sourceBinding"), source["identity"]),
        "Explicit supplemental source/selection differs")
    require(symbols == doc["fixedPopulation"]["symbols"] and digest(symbols) == doc["fixedPopulation"]["sha256"],
        "Supplemental fixed population differs")
    extra = parent["additionalInputs"]
    require(set(extra) == {"rootExecutionSources", "freezeAttempt", "events", "attempt"}, "Incomplete parent lifecycle binding")
    extra_paths = {key: pin(record) for key, record in extra.items()}
    parent_directory = (ROOT / parent["completedArtifactDirectory"]).resolve()
    extra_names = {"rootExecutionSources": "execution-sources.zip", "freezeAttempt": "freeze-attempt.json",
        "events": "events.jsonl", "attempt": "attempts/attempt-0001/attempt.json"}
    for key, name in extra_names.items():
        require(extra_paths[key] == parent_directory / name, "Parent lifecycle path differs")
    frozen_names = {"protocol": "protocol.json", "snapshot": "snapshot.json", "selection": "selection.json",
        "executionSources": "execution-sources.zip"}
    require(set(pc.get("frozenFiles", {})) == set(frozen_names), "Parent frozen root closure differs")
    for key, name in frozen_names.items():
        require(pc["frozenFiles"][key] == {"path": name, "sha256": sha(parent_directory / name)}, "Parent frozen root file differs")
    require(pc.get("attempt") == 1 and pc.get("attemptSha256") == extra["attempt"]["sha256"] and
        read_json(extra_paths["attempt"]).get("protocolHash") == parent["protocolHash"] and
        read_json(extra_paths["freezeAttempt"]).get("registrationSha256") == parent["registrationSha256"], "Parent attempt identity differs")
    require(pp.get("executionArchiveSha256") == extra["rootExecutionSources"]["sha256"] and
        pp.get("snapshotSha256") == parent["completedInputs"]["snapshot.json"]["sha256"] and
        pp.get("selectionSha256") == parent["completedInputs"]["selection.json"]["sha256"], "Parent root archive/snapshot binding differs")
    _check_archive(extra_paths["rootExecutionSources"], parent["sourceImplementation"])
    require(pa.get("schema") == "supplemental-persistent-leader-independent-verification-v1" and
        pa.get("registrationSha256") == parent["registrationSha256"] and pa.get("protocolHash") == parent["protocolHash"] and
        pa.get("completionSha256") == parent["completedInputs"]["completion.json"]["sha256"] and
        same(pa.get("implementation"), pp["implementation"]) and
        same(pa.get("resultFileHashes"), {item["path"]: item["sha256"] for item in pc["files"].values()}) and
        pa.get("evaluatedRows") == len(symbols) * len(dates) and pa.get("targetSessions") == len(dates),
        "Supplemental parent audit closure differs")
    require(ma["resultFileHashes"]["completion.json"] == market["completion"]["sha256"], "Supplemental market completion audit differs")
    require(ma.get("schema") == "supplemental-market-state-independent-verification-v1" and
        same(ma.get("implementation"), mp["implementation"]) and
        same(ma.get("sourcePanelClosureAudit"), doc["sourcePanelClosureAudit"]), "Supplemental market audit identity differs")
    market_document = read_json(pin(market["registration"]))
    require(same(mp.get("registration"), market_document) and
        same(market_document.get("fixedPopulation"), doc["fixedPopulation"]), "Supplemental market registration/population differs")
    for key in ("sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        require(same(market_document[key], doc[key]), "Market source panel evidence differs: " + key)
    pin({"path": parent["registration"], "sha256": parent["registrationSha256"]})
    for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        pin(doc[key])
    for name, expected in source["identity"]["files"].items():
        pin({"path": (source["directory"] / name).relative_to(ROOT.resolve()).as_posix(), "sha256": expected})
    for name, expected in {**source["protocol"]["inputFilesSha256"], **source["protocol"]["implementation"], **source["protocol"]["metadata"]}.items():
        pin({"path": name, "sha256": expected})
    closure = panel_binding["closureAudit"]
    pin({"path": closure["verifierPath"], "sha256": closure["verifierSha256"]})
    identity = {"registrationSha256": REGISTRATION_SHA256, "inputFilesSha256": files,
        "parentProtocolHash": parent["protocolHash"], "marketProtocolHash": market["protocolHash"],
        "snapshotFingerprint": fingerprint, "calendarHash": raw["calendarHash"],
        "sourceMaterialization": source["identity"], "panelBinding": panel_binding["identity"]}
    return {"document": doc, "parent_protocol": pp, "manifest": manifest, "dates": dates, "symbols": symbols,
        "files": files, "identity": identity, "parentPaths": parent_paths, "marketPaths": market_paths, "rawPaths": raw_paths,
        "source": source, "panel_binding": panel_binding}


# The original routines retain their private module globals and exactly the
# original target-session history, paired rank controls and fresh active state.
iter_records = legacy.iter_records
iter_parent_days = legacy.iter_parent_days
iter_market_days = legacy.iter_market_days
iter_role_days = legacy.iter_role_days
collect_signals = legacy.collect_signals
evaluate_signals = legacy.evaluate_signals


def implementation_hashes(binding):
    document = registration()
    names = set(document["parent"]["sourceImplementation"]) | set(document["frozenMethodImplementation"]) | set(document["marketInput"]["implementation"])
    names.update(binding["source"]["protocol"]["implementation"])
    names.update(binding["source"]["protocol"]["metadata"])
    names.update({Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(),
        REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix(), NEW_VERIFIER, NEW_TESTS})
    for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        names.add(document[key]["path"])
    names.add(binding["panel_binding"]["closureAudit"]["verifierPath"])
    return {name: sha(_local_path(name)) for name in sorted(names)}


def _safe_args(args, binding):
    _configure(binding["document"])
    workspace, output = legacy._safe_args(args, binding)
    require(workspace == SOURCE_WORKSPACE.resolve(), "Only the frozen supplemental workspace is allowed")
    source = binding["source"]["directory"]
    require(not output.is_relative_to(source) and not source.is_relative_to(output), "Output overlaps the complete materialized source")
    require(all(not _local_path(name).is_relative_to(output) for name in implementation_hashes(binding)), "Output contains execution evidence")
    return workspace, output


def _inventory(output, *, completed):
    root_names = {"protocol.json", "execution-sources.zip", "freeze-attempt.json", ".run.lock", "events.jsonl"}
    if completed:
        root_names.add("attempts")
    for path in output.iterdir():
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Output links are forbidden")
        report = completed and path.is_file() and (path.name == "independent-result-verification.json" or
            (path.name.startswith("independent-result-verification-failed-") and path.name.endswith(".json")))
        require(path.name in root_names or report, "Unexpected output or prior failed/interrupted attempt; no automatic restart")
    if completed:
        attempts = output / "attempts"
        require(sorted(p.name for p in attempts.iterdir()) == ["attempt-0001"], "Unexpected additional attempts")
        attempt = attempts / "attempt-0001"
        require({p.name for p in attempt.iterdir()} == set(ATTEMPT_FILES.values()) | {"completion.json"}, "Completed attempt inventory differs")
        for path in [attempts, attempt, *attempt.iterdir()]:
            require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Attempt links are forbidden")


def _verify_protocol(output, binding, workspace):
    protocol = read_json(output / "protocol.json")
    require(protocol.get("schema") == "supplemental-market-repair-v1" and
        protocol.get("protocolHash") == digest({k: v for k, v in protocol.items() if k != "protocolHash"}), "Protocol hash/schema changed")
    require(same(protocol.get("registration"), binding["document"]) and protocol.get("registrationSha256") == REGISTRATION_SHA256 and
        same(protocol.get("binding"), binding["identity"]) and same(protocol.get("config"), binding["parent_protocol"]["config"]) and
        protocol.get("workspace") == str(workspace) and protocol.get("strategyOrder") == [CANDIDATE_ID] and
        protocol.get("groups") == list(GROUPS) and protocol.get("stage") == STAGE and protocol.get("pilot") is False and
        protocol.get("certified") is False and protocol.get("targetAchieved") is False and
        same(protocol.get("exposure"), binding["document"]["exposure"]) and
        same(protocol.get("downstreamPolicy"), binding["document"]["downstreamPolicy"]), "Frozen supplemental protocol/config changed")
    require(protocol["implementation"] == implementation_hashes(binding), "Execution source changed")
    require(sha(output / "execution-sources.zip") == protocol["executionArchiveSha256"], "Whole execution archive changed")
    _check_archive(output / "execution-sources.zip", protocol["implementation"])
    marker = read_json(output / "freeze-attempt.json")
    require(sha(output / "freeze-attempt.json") == protocol["freezeAttemptSha256"] and
        marker.get("registrationSha256") == REGISTRATION_SHA256 and marker.get("candidateId") == CANDIDATE_ID and
        marker.get("stage") == STAGE and marker.get("certified") is False and marker.get("targetAchieved") is False,
        "Freeze attempt identity differs")
    return protocol


def freeze(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    output.mkdir(parents=True, exist_ok=True)
    with source_tools.output_lock(output):
        require(not (output / "protocol.json").exists(), "Already frozen; use execute for completed verification only")
        require({p.name for p in output.iterdir()} == {".run.lock"}, "Unregistered output is not empty; failed freeze is preserved")
        write_json(output / "freeze-attempt.json", {"registrationSha256": REGISTRATION_SHA256, "startedAt": _now(),
            "candidateId": CANDIDATE_ID, "stage": STAGE, "certified": False, "targetAchieved": False})
        try:
            implementation = implementation_hashes(binding)
            _archive(output / "execution-sources.zip", implementation)
            protocol = {"schema": "supplemental-market-repair-v1", "createdAt": _now(),
                "registration": binding["document"], "registrationSha256": REGISTRATION_SHA256, "binding": binding["identity"],
                "workspace": str(workspace), "config": deepcopy(binding["parent_protocol"]["config"]),
                "strategyOrder": [CANDIDATE_ID], "groups": list(GROUPS), "stage": STAGE, "pilot": False,
                "certified": False, "targetAchieved": False, "implementation": implementation, "pythonVersion": sys.version,
                "exposure": binding["document"]["exposure"], "downstreamPolicy": binding["document"]["downstreamPolicy"],
                "executionArchiveSha256": sha(output / "execution-sources.zip"),
                "freezeAttemptSha256": sha(output / "freeze-attempt.json")}
            protocol["protocolHash"] = digest(protocol)
            require(same(load_binding()["identity"], binding["identity"]) and implementation_hashes(binding) == implementation,
                "Input/code drift while freezing")
            write_json(output / "protocol.json", protocol)
            return protocol
        except Exception as error:
            _fail(output, "freeze", error)
            raise


def validate_dataset(dataset, binding):
    legacy.validate_dataset(dataset, binding)
    require(all(bar["date"] <= binding["document"]["sample"]["signalEnd"] for bar in dataset["benchmark"]["bars"]),
        "Post-cutoff benchmark observations are forbidden")


def load_dataset(args, binding):
    dataset = legacy.load_dataset(args, binding)
    validate_dataset(dataset, binding)
    return dataset


def verify_completion(output, receipt, protocol):
    _inventory(output, completed=True)
    legacy.verify_completion(output, receipt, protocol)
    require(receipt.get("attempt") == 1 and receipt.get("freezeAttemptSha256") == protocol["freezeAttemptSha256"] and
        sha(output / "freeze-attempt.json") == protocol["freezeAttemptSha256"], "Completed freeze/attempt identity differs")
    marker = read_json(output / "attempts/attempt-0001/attempt.json")
    require(marker.get("attempt") == 1 and marker.get("protocolHash") == protocol["protocolHash"] and
        marker.get("candidateId") == CANDIDATE_ID and marker.get("certified") is False and marker.get("targetAchieved") is False,
        "Completed attempt marker changed")



def execute(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    require((output / "protocol.json").is_file(), "Freeze and review protocol before executing")
    with source_tools.output_lock(output):
        protocol = _verify_protocol(output, binding, workspace)
        protocol_sha = sha(output / "protocol.json")
        attempt = output / "attempts/attempt-0001"
        receipt_path = attempt / "completion.json"
        if receipt_path.exists():
            require(sorted(p.name for p in (output / "attempts").iterdir()) == ["attempt-0001"], "Unexpected extra attempts")
            verify_completion(output, read_json(receipt_path), protocol)
            print("Completed supplemental market-repair study verified; no signals or outcomes recomputed", flush=True)
            return 0
        _inventory(output, completed=False)
        require(not (output / "attempts").exists(), "Prior failed/interrupted attempt retained; automatic restart is prohibited")
        attempt.mkdir(parents=True)
        write_json(attempt / "attempt.json", {"protocolHash": protocol["protocolHash"], "startedAt": datetime.now(timezone.utc).isoformat(),
            "attempt": 1, "candidateId": CANDIDATE_ID, "certified": False, "targetAchieved": False})
        source_tools.event(output, protocol["protocolHash"], "study_started", attempt=1)
        try:
            dataset = load_dataset(args, binding)
            validate_dataset(dataset, binding)
            signals, performance = collect_signals(binding, attempt)
            source_tools.event(output, protocol["protocolHash"], "filtering_completed", **performance)
            tick = time.perf_counter()
            result = evaluate_signals(dataset, protocol["config"], signals)
            performance["eventEvaluationSeconds"] = time.perf_counter() - tick
            accounting = performance["filterAccounting"]
            for group in result["groups"]:
                counts = accounting[group["strategyId"]]
                counts.update(newAttemptedEvents=sum(e["strategyId"] == group["strategyId"] for e in result["events"]),
                    newActiveSuppressed=sum(e["strategyId"] == group["strategyId"] for e in result["overlapSuppressed"]))
                require(counts["parentSignals"] == sum(counts[k] for k in ("gateTrueAdmitted", "gateFalseFiltered", "gateUnknownFiltered")) and
                    counts["gateTrueAdmitted"] == counts["newAttemptedEvents"] + counts["newActiveSuppressed"], "Signal disposition accounting failed")
            result.update(protocolHash=protocol["protocolHash"], snapshotFingerprint=dataset["fingerprint"],
                strategyOrder=[CANDIDATE_ID], stage=STAGE, pilot=False, diagnosticUnverifiedHistory=True,
                certified=False, targetAchieved=False, filterAccounting=accounting,
                marketGateCounts=performance["marketGateCounts"], candidateGateCounts=performance["candidateGateCounts"],
                sourceRoleRows=performance["sourceRoleRows"], unmatchedTrueStrategySlots=performance["unmatchedTrueStrategySlots"],
                parentProtocolHash=binding["document"]["parent"]["protocolHash"],
                exposure=binding["document"]["exposure"], downstreamPolicy=binding["document"]["downstreamPolicy"],
                resultMeaning="raw-price-market-repair-resilience-qualified-supplemental-diagnostic;not-economic-profit-or-goal-certification")
            with gzip.open(attempt / "result.json.gz", "xt", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            summary = {k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")}
            summary.update(performance=performance, ranking=None, selectionEligible=False)
            write_json(attempt / "summary.json", summary)
            final = load_binding()
            require(same(final["identity"], binding["identity"]) and sha(output / "protocol.json") == protocol_sha, "Input/protocol drift during study")
            _verify_protocol(output, final, workspace)
            validate_dataset(dataset, final)
            paths = {k: "attempts/attempt-0001/" + name for k, name in ATTEMPT_FILES.items()}
            paths.update(executionSources="execution-sources.zip", protocol="protocol.json")
            receipt = {"protocolHash": protocol["protocolHash"], "snapshotFingerprint": dataset["fingerprint"], "attempt": 1,
                "certified": False, "targetAchieved": False, "freezeAttemptSha256": protocol["freezeAttemptSha256"],
                "files": {k: {"path": name, "sha256": sha(output / name)} for k, name in paths.items()}}
            write_json(receipt_path, receipt)
            source_tools.event(output, protocol["protocolHash"], "study_succeeded", receipt=receipt)
            return 0
        except Exception as exc:
            _fail(output, "execute", exc, protocol["protocolHash"])
            write_json(attempt / "failure.json", {"protocolHash": protocol["protocolHash"], "errorType": type(exc).__name__,
                "error": str(exc), "failedAt": datetime.now(timezone.utc).isoformat(), "automaticRetryAllowed": False})
            source_tools.event(output, protocol["protocolHash"], "study_failed", errorType=type(exc).__name__, error=str(exc))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=SOURCE_WORKSPACE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--diagnostic-unverified-history", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.freeze_only:
            protocol = freeze(args)
            print(json.dumps({"protocolHash": protocol["protocolHash"], "evaluationExecuted": False}), flush=True)
            return 0
        return execute(args)
    except (ValueError, OSError, KeyError) as exc:
        print("Market-repair study stopped: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
