#!/usr/bin/env python3
"""Frozen persistent-leader method on the explicitly bound supplemental sample.

The original detector, collection, ranking, controls and event/path evaluators
are reused privately. This module owns the new source/period registration and
single-attempt lifecycle; the original development execute is never called.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-persistent-leader-v1.json"
REGISTRATION_SHA256 = "950c21a066bbe84c16212a5758052da3c188c96ae9339011bb915c297c85da6e"
SOURCE_WORKSPACE = ROOT / "artifacts/strategy-search/supplemental-source-2024-2025-v1/workspace"
OLD_RUNNER = "scripts/run-signal-persistent-leader.py"
NEW_VERIFIER = "scripts/verify-signal-supplemental-persistent-leader-result.py"
NEW_TESTS = "tests/test_signal_supplemental_persistent_leader.py"
_spec = importlib.util.spec_from_file_location("_supplemental_private_persistent_leader", ROOT / OLD_RUNNER)
parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parent)
runtime, source_tools = parent.runtime, parent.source_tools
CANDIDATE_ID, STRATEGIES, GROUPS = parent.CANDIDATE_ID, parent.STRATEGIES, parent.GROUPS
PANEL_FILES, EXTRA_FILES = parent.PANEL_FILES, parent.EXTRA_FILES
HORIZONS, CONTROL_SEED = runtime.HORIZONS, runtime.CONTROL_SEED
_parent_registration = parent.registration
_parent_binding = parent.verify_panel_binding
_parent_validate = parent.validate_sample
_parent_protocol = parent.make_protocol
_parent_collect = parent.collect_signals
_parent_evaluate = parent.evaluate_signals
_raw_bound_snapshot = parent._base_bound_snapshot
_write_json = parent._write_json
_original_source_binding = parent._verify_source_binding
_ROOT_FILES = {"protocol": "protocol.json", "selection": "selection.json", "snapshot": "snapshot.json",
               "executionSources": "execution-sources.zip"}
ATTEMPT_FILES = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
                 "result": "result.json.gz", "summary": "summary.json", **EXTRA_FILES}


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


LOADED_SOURCE_SHA256 = file_hash(__file__)
LOADED_RUNNER_SHA256 = file_hash(parent.__file__)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "Duplicate JSON key: " + key)
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError("Nonfinite JSON: " + value)


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_pairs, parse_constant=_nonfinite)


def _relative(name):
    value = PurePosixPath(name)
    require(not value.is_absolute() and ".." not in value.parts and "\\" not in name and ":" not in name,
            "Unsafe relative binding path")
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()), "Bound dependency escapes root")
    return path


def _pin(item, label):
    path = _relative(item["path"])
    require(type(item.get("sha256")) is str and len(item["sha256"]) == 64 and file_hash(path) == item["sha256"], label + " changed")
    return path


def _same(a, b):
    return encoded(a) == encoded(b)


def registration():
    require(type(REGISTRATION_SHA256) is str and len(REGISTRATION_SHA256) == 64, "Supplemental strategy registration is pending")
    require(file_hash(REGISTRATION) == REGISTRATION_SHA256, "Supplemental strategy registration changed")
    require(file_hash(__file__) == LOADED_SOURCE_SHA256 and file_hash(parent.__file__) == LOADED_RUNNER_SHA256,
            "Loaded executable changed after import")
    document = read(REGISTRATION)
    require(document.get("candidateId") == CANDIDATE_ID and document.get("registeredAt"), "Original candidate identity is required")
    for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        _pin(document[key], key)
    require(document["frozenRunner"] == {"path": OLD_RUNNER, "sha256": LOADED_RUNNER_SHA256}, "Frozen original runner identity differs")
    previous = read(_relative(document["frozenRegistration"]["path"]))
    for key in ("candidateId", "objective", "featureEvidence", "baseEligibility", "trigger", "selection"):
        require(_same(document[key], previous[key]), "Frozen candidate method differs: " + key)
    require(_same({k: v for k, v in document["eventPolicy"].items() if k != "cutoff"},
                  {k: v for k, v in previous["eventPolicy"].items() if k != "cutoff"}), "Frozen event method differs")
    method = document["frozenMethodImplementation"]
    require(type(method) is dict and method.get(OLD_RUNNER) == LOADED_RUNNER_SHA256 and
            method.get(document["frozenRegistration"]["path"]) == document["frozenRegistration"]["sha256"], "Frozen method closure is incomplete")
    for name, expected in method.items():
        require(file_hash(_relative(name)) == expected, "Frozen method dependency changed: " + name)
    sample, fixed = document["sample"], document["fixedPopulation"]
    require(type(fixed["symbols"]) is list and fixed["symbols"] == sorted(set(fixed["symbols"])) and
            type(fixed["count"]) is int and fixed["count"] == len(fixed["symbols"]) == sample["expectedFullSecurities"] and
            fixed["sha256"] == digest(fixed["symbols"]), "Exact fixed population identity differs")
    expected_selection = {"mode": "explicit", "source": "local", "markets": ["sh", "sz"], "boards": ["main"],
                          "benchmarkSymbol": "sh000300", "symbols": fixed["symbols"]}
    require(_same(sample["selection"], expected_selection), "Source selection must explicitly retain the original fixed population")
    for key in ("signalStart", "signalEnd", "warmupStart", "latestPermittedObservation"):
        require(type(sample[key]) is str and date.fromisoformat(sample[key]).isoformat() == sample[key], "Noncanonical interval date")
    require(sample["warmupStart"] < sample["signalStart"] <= sample["signalEnd"] == sample["latestPermittedObservation"], "Registered interval differs")
    require(sample["expectedFeatureRows"] == sample["expectedFullSecurities"] * sample["expectedScoredSessions"], "Registered feature row count differs")
    return document


def _archive_check(path, implementation, expected_sha=None):
    if expected_sha is not None:
        require(file_hash(path) == expected_sha, "Whole execution archive SHA changed")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == set(implementation), "Execution archive member closure differs")
        for name, expected in implementation.items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected == file_hash(_relative(name)),
                    "Archived or current executable differs: " + name)


def load_source_binding(document=None):
    document = document or registration()
    spec = document["sourceMaterialization"]
    directory = _relative(spec["directory"])
    require((directory / "workspace").resolve() == SOURCE_WORKSPACE.resolve(), "Wrong isolated supplemental workspace")
    pins = spec["files"]
    require({p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} == set(pins), "Materialized source inventory differs")
    for path in [directory, *directory.rglob("*")]:
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Source links are not allowed")
    for name, expected in pins.items():
        require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts and "\\" not in name and ":" not in name,
                "Unsafe source member path")
        require(file_hash(directory / name) == expected, "Materialized source changed: " + name)
    protocol, manifest, completion, audit = [read(directory / name) for name in ("protocol.json", "manifest.json", "completion.json", "independent-verification.json")]
    require(digest({k: v for k, v in protocol.items() if k != "protocolHash"}) == protocol.get("protocolHash") == spec["protocolHash"] and
            protocol.get("registrationSha256") == spec["registrationSha256"], "Source protocol identity differs")
    require(all(item.get("protocolHash") == spec["protocolHash"] for item in (manifest, completion, audit)), "Source receipt/audit protocol differs")
    require(all(all(item.get(flag) is False for flag in ("returnsCalculated", "featuresCalculated", "certified", "targetAchieved"))
                for item in (protocol, manifest, completion, audit)), "Source diagnostic scope differs")
    require(audit.get("passed") is True and type(audit.get("failureCount")) is int and audit["failureCount"] == 0 and
            audit.get("verifierSha256") == spec["independentVerifierSha256"] and audit.get("completionSha256") == pins["completion.json"] and
            _same(audit.get("implementation"), protocol["implementation"]) and _same(audit.get("sourceHashes"), protocol["inputFilesSha256"]) and
            _same(audit.get("resultFileHashes"), completion["files"]), "Source independent audit identity differs")
    require(set(pins) == set(completion["files"]) | {"completion.json", "independent-verification.json"} and
            all(pins[name] == expected for name, expected in completion["files"].items()), "Source completion file closure differs")
    fixed, sample = document["fixedPopulation"], document["sample"]
    require(manifest["fixedSymbols"] == fixed["symbols"] == protocol["registration"]["fixedPopulation"]["symbols"] and
            manifest["benchmarkSymbol"] == sample["selection"]["benchmarkSymbol"] and manifest["cutoff"] == sample["signalEnd"] and
            manifest.get("prefixNumericQualityBefore2023Verified") is False, "Materialized fixed population or cutoff differs")
    rows = manifest["sourceFiles"]
    require([row["symbol"] for row in rows] == sorted(fixed["symbols"] + [manifest["benchmarkSymbol"]]), "Materialized source substituted or omitted symbols")
    physical = {}
    for row in rows:
        symbol = row["symbol"]
        name = f"tdx/vipdoc/{symbol[:2]}/lday/{symbol}.day"
        require(row["path"] == name and pins.get(name) == row["prefixSha256"] and type(row["prefixRecords"]) is int and
                row["prefixRecords"] > 0 and row["prefixBytes"] == row["prefixRecords"] * 32 and
                row["firstDate"] <= row["lastDate"] <= sample["signalEnd"], "Invalid materialized prefix identity")
        physical[str((directory / name).resolve())] = pins[name]
    require(set(completion["files"]) == {row["path"] for row in rows} | {"workspace/market.sqlite3", "protocol.json", "attempt.json", "manifest.json", "execution-sources.zip"},
            "Source includes an unexpected catalogue/cache or artifact")
    require(manifest["workspace"]["path"] == "workspace/market.sqlite3" and manifest["workspace"]["sha256"] == pins["workspace/market.sqlite3"] and
            manifest["workspace"]["tdxPath"] == str((directory / "tdx").resolve()), "Workspace identity differs")
    physical[str((directory / "workspace/market.sqlite3").resolve())] = pins["workspace/market.sqlite3"]
    for name, expected in protocol["inputFilesSha256"].items():
        require(file_hash(_relative(name)) == expected, "Upstream source evidence changed: " + name)
    require(protocol["implementation"].get("scripts/verify-signal-supplemental-source.py") == spec["independentVerifierSha256"], "Source verifier pin differs")
    _archive_check(directory / "execution-sources.zip", {**protocol["implementation"], **protocol["metadata"]}, protocol["executionArchiveSha256"])
    return {"directory": directory, "protocol": protocol, "manifest": manifest, "physical": physical,
            "identity": {"directory": str(directory), "protocolHash": spec["protocolHash"], "registrationSha256": spec["registrationSha256"],
                         "files": pins, "inputFilesSha256": protocol["inputFilesSha256"]}}


def verify_panel_binding(document):
    _configure()
    binding = _parent_binding(document)
    directory, manifest, audit = binding["directory"], binding["manifest"], binding["audit"]
    all_pins = {**binding["identity"]["panelFilesSha256"], "completion.json": binding["identity"]["panelCompletionSha256"]}
    require({p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} == set(all_pins), "Panel file inventory differs")
    for path in [directory, *directory.rglob("*")]:
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Panel links are not allowed")
    source_document = read(_relative(document["sourcePanelRegistration"]["path"]))
    source_sha = document["sourcePanelRegistration"]["sha256"]
    require(_same(manifest.get("registration"), source_document) and
            manifest.get("registrationSha256") == binding["completion"].get("registrationSha256") == audit.get("registrationSha256") == source_sha,
            "Source feature registration identity differs")
    require(source_document["fixedPopulation"] == document["fixedPopulation"] and manifest["inputs"]["symbols"] == document["fixedPopulation"]["symbols"] and
            _same(source_document["sourceMaterialization"], document["sourceMaterialization"]), "Source feature fixed sample or materialization differs")
    for key in ("signalStart", "signalEnd", "warmupStart", "expectedFullSnapshotFingerprint", "expectedFullSecurities", "expectedFullBars", "expectedScoredSessions", "expectedFeatureRows"):
        require(document["sample"][key] == source_document["sample"][key], "Source feature sample field differs: " + key)
    require(audit.get("schema") == "supplemental-leadership-panel-independent-verification-v1" and
            audit.get("verifierSha256") == document["sourcePanelVerifier"]["sha256"] and
            audit.get("targetSessions") == document["sample"]["expectedScoredSessions"] and
            _same(audit.get("implementation"), manifest["implementation"]) and
            _same(audit.get("resultFileHashes"), {k: v for k, v in all_pins.items() if k != "independent-verification.json"}),
            "Supplemental feature independent audit closure differs")
    require(audit.get("certified") is False and audit.get("targetAchieved") is False and
            audit.get("strategyReturnsCalculated") is False and audit.get("forwardReturnsCalculated") is False,
            "Source feature audit scope differs")
    _archive_check(directory / "execution-sources.zip", manifest["implementation"], manifest["executionArchiveSha256"])
    closure = read(_relative(document["sourcePanelClosureAudit"]["path"]))
    require(closure.get("passed") is True and type(closure.get("failureCount")) is int and closure["failureCount"] == 0 and
            closure.get("sampleManifestHash") == all_pins["manifest.json"] and closure.get("completionSha256") == all_pins["completion.json"] and
            closure.get("targetSessions") == document["sample"]["expectedScoredSessions"] and closure.get("featureRows") == document["sample"]["expectedFeatureRows"],
            "Supplemental feature closure audit identity differs")
    _pin({"path": closure["verifierPath"], "sha256": closure["verifierSha256"]}, "Panel closure verifier")
    source = load_source_binding(document)
    require(_same(manifest["physicalSourceHashes"], source["physical"]), "Feature physical inputs differ from exact isolated source")
    binding["identity"].update(sourceMaterialization=source["identity"], sourcePanelRegistration=document["sourcePanelRegistration"],
        sourcePanelVerifier=document["sourcePanelVerifier"], sourcePanelClosureAudit=document["sourcePanelClosureAudit"])
    binding.update(source=source, closureAudit=closure)
    return binding


def implementation_hashes():
    document = registration()
    source = load_source_binding(document)
    names = set(document["frozenMethodImplementation"]) | set(source["protocol"]["implementation"]) | set(source["protocol"]["metadata"]) | {
        Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(), Path(REGISTRATION).resolve().relative_to(ROOT.resolve()).as_posix(), NEW_VERIFIER, NEW_TESTS}
    for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        names.add(document[key]["path"])
    closure = read(_relative(document["sourcePanelClosureAudit"]["path"]))
    names.add(closure["verifierPath"])
    _pin({"path": closure["verifierPath"], "sha256": closure["verifierSha256"]}, "Panel closure verifier")
    return {name: file_hash(_relative(name)) for name in sorted(names)}


def settings(args):
    return parent.settings(args)


def validate_sample(dataset, start, end):
    _configure()
    _parent_validate(dataset, start, end)
    document = registration()
    require(sorted(s["symbol"] for s in dataset["securities"]) == document["fixedPopulation"]["symbols"], "Loaded exact fixed population differs")
    require(all(bar["date"] <= end for security in dataset["securities"] for bar in security["bars"]) and
            all(bar["date"] <= end for bar in dataset["benchmark"]["bars"]), "Post-cutoff raw observations are forbidden")


def bound_snapshot(dataset, start, end):
    result = _raw_bound_snapshot(dataset, start, end)
    validate_sample(result, start, end)
    return result


def _configure():
    parent.ROOT = ROOT
    parent.REGISTRATION, parent.REGISTRATION_SHA256 = REGISTRATION, REGISTRATION_SHA256
    parent.registration, parent.verify_panel_binding = registration, verify_panel_binding
    parent.validate_sample, parent.implementation_hashes = validate_sample, implementation_hashes
    # No DEVELOPMENT_END or execute substitution: new lifecycle below owns dates.


def iter_panel_days(binding):
    return parent.iter_panel_days(binding)


def collect_signals(dataset, config, attempt):
    _configure()
    return _parent_collect(dataset, config, Path(attempt))


def evaluate_signals(dataset, config, signals):
    return _parent_evaluate(dataset, config, signals)


def make_protocol(args):
    _configure()
    document = registration()
    result = _parent_protocol(args)
    result.update(schema="supplemental-persistent-price-leader-v1", stage="qualified-supplemental-validation",
        studyKind="unchanged-persistent-leader-supplemental-diagnostic", latestPermittedObservation=args.end,
        selection=document["sample"]["selection"], sourceBinding=load_source_binding(document)["identity"],
        sampleUse="qualified-supplemental-interval;prior-default-strategy-exposure;not-pristine-holdout",
        exposure=document.get("exposure"), downstreamPolicy=document.get("downstreamPolicy"),
        lifecycle="freeze-source-config-selection-archive-before-predicates;one-attempt;failed-run-preserved-no-restart")
    return result


def _args(args):
    document = registration()
    args.workspace, args.output = Path(args.workspace).resolve(), Path(args.output).resolve()
    sample = document["sample"]
    require(args.diagnostic_unverified_history is True and args.max_symbols is None and
            (args.start, args.end) == (sample["signalStart"], sample["signalEnd"]), "Use exactly the registered diagnostic sample/window")
    require(args.workspace == SOURCE_WORKSPACE.resolve(), "Only the frozen isolated source workspace is allowed")
    source = _relative(document["sourceMaterialization"]["directory"])
    panel = (ROOT / document["registrationPrerequisites"]["panelDirectory"]).resolve()
    for directory in (source, panel):
        require(args.output != directory and not args.output.is_relative_to(directory) and not directory.is_relative_to(args.output), "Output overlaps a frozen source/panel")
    for name in implementation_hashes():
        require(not _relative(name).is_relative_to(args.output), "Output contains frozen execution evidence")
    return document


def _load_dataset(args, document, binding):
    # This real explicit selection participates in both loading and fingerprinting.
    data = bound_snapshot(runtime.snapshot_universe(args.workspace, document["sample"]["selection"], args.end, args.start), args.start, args.end)
    _original_source_binding(data, binding)
    return data


def _snapshot(data, args, document):
    return {"fingerprint": data["fingerprint"], "fullDatasetContentSha256": digest(data),
        "symbols": sorted(s["symbol"] for s in data["securities"]), "selection": document["sample"]["selection"],
        "sourceStart": args.start, "observationCutoff": args.end, "barCount": sum(len(s["bars"]) for s in data["securities"]),
        "quality": data.get("dataQuality", {}), "coverage": data.get("universeCoverage", {})}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fail(output, phase, error, protocol_hash=None):
    path = output / "failure.json"
    if not path.exists():
        _write_json(path, {"failedAt": _now(), "phase": phase, "protocolHash": protocol_hash,
            "errorType": type(error).__name__, "error": str(error), "noAutomaticRetry": True})


def freeze(args):
    document = _args(args)
    require(not args.output.exists(), "Freeze output already exists; never overwrite a prior attempt")
    args.output.mkdir(parents=True, exist_ok=False)
    _write_json(args.output / "freeze-attempt.json", {"startedAt": _now(), "registrationSha256": REGISTRATION_SHA256})
    try:
        with source_tools.output_lock(args.output):
            binding = verify_panel_binding(document)
            data = _load_dataset(args, document, binding)
            protocol = make_protocol(args)
            _write_json(args.output / "selection.json", protocol["selection"])
            _write_json(args.output / "snapshot.json", _snapshot(data, args, document))
            parent._archive_sources(args.output / "execution-sources.zip", protocol["implementation"])
            protocol["executionArchiveSha256"] = file_hash(args.output / "execution-sources.zip")
            protocol["snapshotSha256"] = file_hash(args.output / "snapshot.json")
            protocol["selectionSha256"] = file_hash(args.output / "selection.json")
            after = verify_panel_binding(registration())
            require(_same(after["identity"], binding["identity"]) and _same(implementation_hashes(), protocol["implementation"]), "Inputs changed while freezing")
            _archive_check(args.output / "execution-sources.zip", protocol["implementation"], protocol["executionArchiveSha256"])
            _write_json(args.output / "protocol.json", protocol)
            source_tools.event(args.output, digest(protocol), "protocol_registered")
            return protocol
    except Exception as error:
        _fail(args.output, "freeze", error)
        raise


def _frozen_files(output):
    return {key: {"path": name, "sha256": file_hash(output / name)} for key, name in _ROOT_FILES.items()}


def _inventory(output, *, completed):
    root_names = set(_ROOT_FILES.values()) | {"freeze-attempt.json", "events.jsonl", ".run.lock"}
    if completed:
        root_names |= {"attempts", "independent-result-verification.json"}
        root_names.update(p.name for p in output.iterdir() if p.is_file() and
                          p.name.startswith("independent-result-verification-failed-") and p.name.endswith(".json"))
    actual = {p.name for p in output.iterdir()}
    require(actual <= root_names and set(_ROOT_FILES.values()) | {"freeze-attempt.json", "events.jsonl"} <= actual,
            "Study root contains unexpected or missing artifacts")
    if completed:
        attempts = output / "attempts"
        require(attempts.is_dir() and {p.name for p in attempts.iterdir()} == {"attempt-0001"}, "Only the registered first attempt is allowed")
        expected = set(ATTEMPT_FILES.values()) | {"attempt.json", "completion.json"}
        require({p.name for p in (attempts / "attempt-0001").iterdir()} == expected, "Successful attempt artifact inventory differs")


def validate_protocol(args, binding=None):
    document = _args(args)
    require(not (args.output / "failure.json").exists(), "Failed attempt is preserved; automatic restart is forbidden")
    binding = binding or verify_panel_binding(document)
    protocol = read(args.output / "protocol.json")
    expected = make_protocol(args)
    extras = {"executionArchiveSha256", "snapshotSha256", "selectionSha256"}
    require(_same({k: v for k, v in protocol.items() if k not in extras}, expected), "Frozen protocol/configuration/source identity changed")
    require(_same(read(args.output / "selection.json"), document["sample"]["selection"]) and
            file_hash(args.output / "selection.json") == protocol["selectionSha256"] and
            file_hash(args.output / "snapshot.json") == protocol["snapshotSha256"], "Frozen selection/snapshot changed")
    require(read(args.output / "freeze-attempt.json").get("registrationSha256") == REGISTRATION_SHA256, "Freeze attempt identity differs")
    _archive_check(args.output / "execution-sources.zip", protocol["implementation"], protocol["executionArchiveSha256"])
    snapshot = read(args.output / "snapshot.json")
    require(snapshot["fingerprint"] == document["sample"]["expectedFullSnapshotFingerprint"] and snapshot["symbols"] == document["fixedPopulation"]["symbols"] and
            snapshot["fullDatasetContentSha256"] == binding["manifest"]["inputs"]["fullDatasetContentSha256"] and
            _same(snapshot["selection"], protocol["selection"]), "Frozen snapshot binding differs")
    return protocol


def verify_completion(output, receipt, protocol_hash, fingerprint):
    output = Path(output).resolve()
    _inventory(output, completed=True)
    require(receipt.get("protocolHash") == protocol_hash and receipt.get("snapshotFingerprint") == fingerprint and receipt.get("attempt") == 1,
            "Completed study identity changed")
    require(set(receipt.get("files", {})) == set(ATTEMPT_FILES) and _same(receipt.get("frozenFiles"), _frozen_files(output)), "Completed file closure changed")
    attempt_marker = output / "attempts/attempt-0001/attempt.json"
    require(receipt.get("attemptSha256") == file_hash(attempt_marker) and
            read(attempt_marker).get("protocolHash") == protocol_hash and read(attempt_marker).get("attempt") == 1,
            "Completed attempt marker identity differs")
    binding = verify_panel_binding(registration())
    require(_same(receipt.get("panelBinding"), binding["identity"]), "Completed panel/source binding differs")
    for key, name in ATTEMPT_FILES.items():
        expected_path = "attempts/attempt-0001/" + name
        record = receipt["files"][key]
        require(record == {"path": expected_path, "sha256": file_hash(output / expected_path)}, "Successful artifact changed; no rerun: " + key)
    protocol = read(output / "protocol.json")
    require(digest(protocol) == protocol_hash and _same(protocol["implementation"], implementation_hashes()), "Completed protocol/executables differ")
    _archive_check(output / receipt["files"]["executionSources"]["path"], protocol["implementation"], receipt["files"]["executionSources"]["sha256"])


def execute(args):
    document = _args(args)
    require(args.output.is_dir() and (args.output / "protocol.json").is_file(), "Freeze the registered study before execution")
    with source_tools.output_lock(args.output):
        binding = verify_panel_binding(document)
        protocol = validate_protocol(args, binding)
        protocol_hash = digest(protocol)
        attempt = args.output / "attempts/attempt-0001"
        receipt_path = attempt / "completion.json"
        if receipt_path.exists():
            verify_completion(args.output, read(receipt_path), protocol_hash, document["sample"]["expectedFullSnapshotFingerprint"])
            print("Verified completed supplemental study; no signals or outcomes recomputed", flush=True)
            return 0
        _inventory(args.output, completed=False)
        require(not (args.output / "attempts").exists(), "Existing incomplete attempt is preserved; no automatic restart")
        attempt.mkdir(parents=True, exist_ok=False)
        _write_json(attempt / "attempt.json", {"startedAt": _now(), "protocolHash": protocol_hash, "attempt": 1})
        source_tools.event(args.output, protocol_hash, "study_started", attempt=1)
        frozen_files = _frozen_files(args.output)
        parent._CONTEXT = None
        try:
            data = _load_dataset(args, document, binding)
            require(_same(_snapshot(data, args, document), read(args.output / "snapshot.json")), "Frozen loaded source differs")
            signals, performance = collect_signals(data, protocol["config"], attempt)
            child_archive_sha = file_hash(attempt / "execution-sources.zip")
            source_tools.event(args.output, protocol_hash, "scoring_completed", attempt=1, **performance)
            started = time.perf_counter()
            result = evaluate_signals(data, protocol["config"], signals)
            performance.update(eventEvaluationSeconds=time.perf_counter() - started, peakWorkingSetMiB=source_tools.peak_memory_mib(), peakMemoryScope="whole-process-high-water-mark")
            snapshot = read(args.output / "snapshot.json")
            result.update(stage=protocol["stage"], studyKind=protocol["studyKind"], pilot=False, diagnosticUnverifiedHistory=True,
                certified=False, targetAchieved=False, strategyOrder=list(STRATEGIES), protocolHash=protocol_hash,
                snapshotFingerprint=data["fingerprint"], sourceQuality=snapshot["quality"], sourceCoverage=snapshot["coverage"],
                exposure=document.get("exposure"), sampleUse=protocol["sampleUse"])
            summary = {key: value for key, value in result.items() if key not in ("events", "overlapSuppressed", "pathRiskEvents")}
            summary.update(ranking=None, selectionEligible=False, performance=performance)
            with gzip.open(attempt / "result.json.gz", "xt", encoding="utf-8", compresslevel=1) as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            _write_json(attempt / "summary.json", summary)
            parent._final_checks(attempt)
            validate_protocol(args)
            require(_same(frozen_files, _frozen_files(args.output)) and file_hash(attempt / "execution-sources.zip") == child_archive_sha,
                    "Frozen root or attempt archive changed during evaluation")
            receipt = {"protocolHash": protocol_hash, "snapshotFingerprint": data["fingerprint"], "attempt": 1,
                "attemptSha256": file_hash(attempt / "attempt.json"),
                "panelBinding": binding["identity"], "frozenFiles": frozen_files,
                "files": {key: {"path": (attempt / name).relative_to(args.output).as_posix(), "sha256": file_hash(attempt / name)} for key, name in ATTEMPT_FILES.items()}}
            _write_json(receipt_path, receipt)
            source_tools.event(args.output, protocol_hash, "study_succeeded", receipt=receipt)
            print(f"Supplemental persistent-leader diagnostic complete: {attempt / 'summary.json'}; proxy evidence only", flush=True)
            return 0
        except Exception as error:
            _fail(args.output, "execute", error, protocol_hash)
            _write_json(attempt / "failure.json", {"failedAt": _now(), "errorType": type(error).__name__, "error": str(error), "noAutomaticRetry": True})
            source_tools.event(args.output, protocol_hash, "study_failed", attempt=1, errorType=type(error).__name__, error=str(error))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=SOURCE_WORKSPACE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-unverified-history", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        sample = registration()["sample"]
        args.start, args.end, args.max_symbols = sample["signalStart"], sample["signalEnd"], None
        if args.freeze_only:
            protocol = freeze(args)
            print(json.dumps({"frozen": True, "protocolHash": digest(protocol), "output": str(args.output)}))
            return 0
        return execute(args)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Supplemental persistent leader stopped: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
