#!/usr/bin/env python3
"""Bind the frozen market-observation method to an audited supplemental panel.

Only provenance and the fixed input interval change. The private original
builder retains its target-only tracker, streaming reader and run lifecycle.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-market-state-panel-v1.json"
REGISTRATION_SHA256 = "12185a946bff6cfc0bfdd5a1eabae3908ea2c2a0817040a8f758bffe1b6e672b"
OLD_REGISTRATION = "docs/signal-market-state-panel-v1.json"
OLD_BUILDER = "scripts/build-signal-market-state-panel.py"
NEW_VERIFIER = "scripts/verify-signal-supplemental-market-state-panel.py"
NEW_TESTS = "tests/test_signal_supplemental_market_state.py"

_spec = importlib.util.spec_from_file_location("_supplemental_frozen_market_builder", ROOT / OLD_BUILDER)
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
base = runtime
PANEL_FILES = runtime.PANEL_FILES
OUTPUT_FILES = runtime.OUTPUT_FILES
_original_load_binding = runtime.load_binding
_original_freeze = runtime.freeze
_original_execute = runtime.execute
_original_validate_protocol = runtime.validate_protocol
_original_verify_completed = runtime.verify_completed
_original_iter_days = runtime.iter_days


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


LOADED_SOURCE_SHA256 = file_hash(__file__)
LOADED_BUILDER_SHA256 = file_hash(runtime.__file__)
LOADED_CORE_SHA256 = file_hash(ROOT / "service/signal_market_state.py")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "Duplicate JSON key: " + key)
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError("Nonfinite JSON value: " + value)


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_pairs, parse_constant=_nonfinite)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _relative(name):
    value = PurePosixPath(name)
    require(not value.is_absolute() and ".." not in value.parts and "\\" not in name and ":" not in name,
            "Unsafe relative binding path")
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()), "Bound path escapes repository")
    return path


def _pin(reference, label):
    require(type(reference) is dict and set(reference) == {"path", "sha256"}, label + " must name a path and SHA256")
    path = _relative(reference["path"])
    require(type(reference["sha256"]) is str and len(reference["sha256"]) == 64 and
            file_hash(path) == reference["sha256"], label + " changed")
    return path


def registration():
    require(type(REGISTRATION_SHA256) is str and len(REGISTRATION_SHA256) == 64,
            "Supplemental market-state registration is pending")
    require(file_hash(REGISTRATION) == REGISTRATION_SHA256, "Supplemental market-state registration changed")
    require(file_hash(__file__) == LOADED_SOURCE_SHA256 and file_hash(runtime.__file__) == LOADED_BUILDER_SHA256,
            "Loaded supplemental/original builder changed")
    document = read(REGISTRATION)
    require(document.get("status") == "registered-before-first-real-extraction" and
            type(document.get("newStrategyTrials")) is int and document["newStrategyTrials"] == 0 and
            all(document.get(key) is False for key in ("returnsCalculated", "futureLabelsCreated", "certified", "targetAchieved")),
            "Expected an outcome-free observation registration")
    for key in ("frozenBuilder", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier"):
        _pin(document[key], key)
    require(document["frozenBuilder"] == {"path": OLD_BUILDER, "sha256": LOADED_BUILDER_SHA256},
            "Frozen builder must be the unchanged original")
    frozen = document["frozenObservationImplementation"]
    require(type(frozen) is dict and set(frozen) == {OLD_BUILDER, "service/signal_market_state.py", OLD_REGISTRATION},
            "Frozen observation implementation must retain the exact original closure")
    for name, expected in frozen.items():
        require(file_hash(_relative(name)) == expected, "Frozen observation dependency changed: " + name)
    require(frozen[OLD_BUILDER] == LOADED_BUILDER_SHA256 and frozen["service/signal_market_state.py"] == LOADED_CORE_SHA256,
            "Loaded observation method differs from the frozen method")
    previous = read(_relative(OLD_REGISTRATION))
    for key in ("featureVersion", "sourceFeatureVersion", "design", "metrics", "qDefinition", "roles", "lifecycle"):
        require(encoded(document[key]) == encoded(previous[key]), "Frozen observation definition differs: " + key)
    _pin(document["design"], "Observation design")
    fixed, sample = document["fixedPopulation"], document["sample"]
    require(type(fixed["symbols"]) is list and fixed["symbols"] == sorted(set(fixed["symbols"])) and
            type(fixed["count"]) is int and fixed["count"] == len(fixed["symbols"]) == sample["symbols"] and
            fixed["sha256"] == digest(fixed["symbols"]), "Fixed population identity differs")
    for key in ("start", "end"):
        require(type(sample[key]) is str and date.fromisoformat(sample[key]).isoformat() == sample[key], "Noncanonical target interval")
    require(sample["start"] <= sample["end"] and type(sample["sessions"]) is int and sample["sessions"] > 0 and
            type(sample["rows"]) is int and sample["rows"] == sample["symbols"] * sample["sessions"],
            "Target population/session counts differ")
    _pin(document["sourcePanelClosureAudit"], "Supplemental panel closure audit")
    return document


def _closure_audit(document, hashes):
    report = read(_relative(document["sourcePanelClosureAudit"]["path"]))
    sample = document["sample"]
    require(report.get("passed") is True and type(report.get("failureCount")) is int and report["failureCount"] == 0 and
            report.get("sampleManifestHash") == hashes["manifest.json"] and
            report.get("completionSha256") == hashes["completion.json"] and
            type(report.get("targetSessions")) is int and report["targetSessions"] == sample["sessions"] and
            type(report.get("featureRows")) is int and report["featureRows"] == sample["rows"],
            "Supplemental panel closure audit identity or counts differ")
    _pin({"path": report["verifierPath"], "sha256": report["verifierSha256"]}, "Panel closure verifier")
    return report


def load_binding():
    document = registration()
    _configure()
    binding = _original_load_binding()
    directory, hashes = binding["directory"], binding["hashes"]
    require(directory == _relative(document["input"]["directory"]), "Input panel path differs")
    require({p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} == PANEL_FILES,
            "Supplemental panel file inventory differs")
    require(not directory.is_symlink() and not (hasattr(directory, "is_junction") and directory.is_junction()), "Panel directory cannot be a link")
    for path in directory.rglob("*"):
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Panel members cannot be links")
    manifest = binding["manifest"]
    completion, audit = read(directory / "completion.json"), read(directory / "independent-verification.json")
    source_document = read(_relative(document["sourcePanelRegistration"]["path"]))
    source_sha = document["sourcePanelRegistration"]["sha256"]
    require(encoded(manifest.get("registration")) == encoded(source_document) and
            manifest.get("registrationSha256") == completion.get("registrationSha256") == audit.get("registrationSha256") == source_sha,
            "Supplemental feature registration identity differs")
    require(source_document["fixedPopulation"] == document["fixedPopulation"] and
            binding["symbols"] == document["fixedPopulation"]["symbols"], "Supplemental panel substituted fixed symbols")
    sample, source_sample = document["sample"], source_document["sample"]
    require((sample["start"], sample["end"], sample["symbols"], sample["sessions"], sample["rows"], sample["snapshotFingerprint"]) ==
            (source_sample["signalStart"], source_sample["signalEnd"], source_sample["expectedFullSecurities"],
             source_sample["expectedScoredSessions"], source_sample["expectedFeatureRows"], source_sample["expectedFullSnapshotFingerprint"]),
            "Market target interval must equal the audited feature target interval")
    calendar = manifest["inputs"]["calendar"]["dates"]
    require(all(type(day) is str and date.fromisoformat(day).isoformat() == day and day <= sample["end"] for day in calendar) and
            sum(day < sample["start"] for day in calendar) == source_sample["expectedWarmupSessions"],
            "Source calendar boundary or warmup count differs")
    require(audit.get("schema") == "supplemental-leadership-panel-independent-verification-v1" and
            audit.get("verifierSha256") == document["sourcePanelVerifier"]["sha256"] and
            audit.get("targetSessions") == sample["sessions"] and audit.get("warmupSessions") == source_sample["expectedWarmupSessions"] and
            encoded(audit.get("implementation")) == encoded(manifest["implementation"]),
            "Supplemental feature independent audit identity differs")
    require(encoded(audit.get("resultFileHashes")) == encoded({name: hashes[name] for name in PANEL_FILES - {"independent-verification.json"}}),
            "Supplemental feature audit output closure differs")
    for item in (manifest, audit):
        require(all(item.get(key) is False for key in ("certified", "targetAchieved")), "Source feature diagnostic flags differ")
    require(audit.get("strategyReturnsCalculated") is False and audit.get("forwardReturnsCalculated") is False and
            audit.get("backwardPriceReturnsCalculated") is True, "Source feature audit return scope differs")
    require(file_hash(directory / "execution-sources.zip") == manifest["executionArchiveSha256"], "Source feature archive SHA differs")
    with zipfile.ZipFile(directory / "execution-sources.zip") as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == set(manifest["implementation"]), "Source feature archive member closure differs")
        for name, expected in manifest["implementation"].items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected == file_hash(_relative(name)),
                    "Source feature archived/current implementation differs: " + name)
    closure = _closure_audit(document, hashes)
    return {**binding, "completion": completion, "audit": audit, "closureAudit": closure}


def implementation_hashes():
    document = registration()
    names = set(document["frozenObservationImplementation"]) | {
        Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(),
        Path(REGISTRATION).resolve().relative_to(ROOT.resolve()).as_posix(), NEW_VERIFIER, NEW_TESTS,
        document["sourcePanelClosureAudit"]["path"]}
    for key in ("frozenBuilder", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier"):
        names.add(document[key]["path"])
    closure = read(_relative(document["sourcePanelClosureAudit"]["path"]))
    _pin({"path": closure["verifierPath"], "sha256": closure["verifierSha256"]}, "Panel closure verifier")
    names.add(closure["verifierPath"])
    return {name: file_hash(_relative(name)) for name in sorted(names)}


def _configure():
    runtime.ROOT = ROOT
    runtime.REGISTRATION = REGISTRATION
    runtime.REGISTRATION_SHA256 = REGISTRATION_SHA256
    runtime.read = read
    runtime.load_binding = load_binding
    runtime.implementation_hashes = implementation_hashes


def iter_days(binding):
    # The frozen reader yields target days only; no warmup roles are created.
    return _original_iter_days(binding)


def _output_path(output, binding):
    output = Path(output).resolve()
    directory = binding["directory"]
    require(output != directory and not output.is_relative_to(directory) and not directory.is_relative_to(output),
            "Output and audited input panel must not overlap")
    for name in implementation_hashes():
        require(not _relative(name).is_relative_to(output), "Output contains a frozen execution dependency")
    return output


def freeze(output):
    binding = load_binding()
    return _original_freeze(_output_path(output, binding))


def execute(output):
    binding = load_binding()
    return _original_execute(_output_path(output, binding))


def validate_protocol(output, binding):
    _configure()
    return _original_validate_protocol(Path(output).resolve(), binding)


def verify_completed(output, protocol):
    return _original_verify_completed(Path(output).resolve(), protocol)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    if args.freeze_only:
        result = freeze(args.output)
        print(json.dumps({"frozen": True, "protocolHash": result["protocolHash"], "output": str(args.output.resolve())}))
    else:
        result = execute(args.output)
        print(json.dumps({"completed": True, "roleRows": result["roleRows"], "dailyRows": result["dailyRows"], "output": str(args.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
