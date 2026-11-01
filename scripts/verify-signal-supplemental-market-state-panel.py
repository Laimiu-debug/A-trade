#!/usr/bin/env python3
"""Independent supplemental market/role replay from a hash-bound feature panel.

Reuses the frozen independent observation formulas only. No production market
builder/core, raw-price loader, strategy outcome or live user database is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/supplemental-market-state-panel-v1"
DEFAULT_PANEL = ROOT / "artifacts/strategy-search/supplemental-leadership-panel-v1"
REGISTRATION = ROOT / "docs/signal-supplemental-market-state-panel-v1.json"
REGISTRATION_SHA256 = "12185a946bff6cfc0bfdd5a1eabae3908ea2c2a0817040a8f758bffe1b6e672b"
OLD_VERIFIER = "scripts/verify-signal-market-state-panel.py"
OLD_VERIFIER_SHA256 = "0cc733d87280a16f2bd6dfa8ecbf81174d9985e67b606405726c467c9c2b9830"
OLD_BUILDER = "scripts/build-signal-market-state-panel.py"
OLD_REGISTRATION = "docs/signal-market-state-panel-v1.json"
NEW_BUILDER = "scripts/build-signal-supplemental-market-state-panel.py"
NEW_VERIFIER = "scripts/verify-signal-supplemental-market-state-panel.py"
NEW_TESTS = "tests/test_signal_supplemental_market_state.py"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SOURCE_SHA256 = sha(VERIFIER_SOURCE_PATH)


def unique_pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def nonfinite(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique_pairs, parse_constant=nonfinite)


def path_under(root, name):
    part = PurePosixPath(name)
    if not part.parts or part.is_absolute() or ".." in part.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe relative path")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Relative path escapes root")
    return path


def exact_inventory(directory):
    files = set()
    for path in directory.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Artifact contains a symlink or junction")
        if path.is_file():
            files.add(path.relative_to(directory).as_posix())
    return files


def reference_module(root):
    path = root / OLD_VERIFIER
    if sha(path) != OLD_VERIFIER_SHA256:
        raise ValueError("Frozen independent market-state verifier changed")
    spec = importlib.util.spec_from_file_location("_supplemental_market_independent_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def implementation_names(document, registration_name, closure):
    return set(document["frozenObservationImplementation"]) | {
        registration_name, NEW_BUILDER, NEW_VERIFIER, NEW_TESTS,
        document["frozenBuilder"]["path"], document["frozenIndependentVerifier"]["path"],
        document["sourcePanelRegistration"]["path"], document["sourcePanelVerifier"]["path"],
        document["sourcePanelClosureAudit"]["path"], closure["verifierPath"]}


def supplemental_binding(reference, output, panel, root, audit):
    registration_path = Path(REGISTRATION)
    if not registration_path.is_absolute():
        registration_path = root / registration_path
    registration_path = registration_path.resolve()
    registration_name = registration_path.relative_to(root).as_posix()
    audit.require(REGISTRATION_SHA256 is not None and sha(registration_path) == REGISTRATION_SHA256,
                  "frozen-supplemental-market-registration")
    document = read(registration_path)
    audit.require(set(document["frozenObservationImplementation"]) == {OLD_BUILDER, OLD_REGISTRATION, "service/signal_market_state.py"},
                  "exact-original-observation-closure")
    audit.require(document["frozenBuilder"] == {"path": OLD_BUILDER, "sha256": document["frozenObservationImplementation"][OLD_BUILDER]},
                  "original-observation-builder-reference")
    previous_registration = read(root / OLD_REGISTRATION)
    for key in ("featureVersion", "sourceFeatureVersion", "design", "metrics", "qDefinition", "roles", "lifecycle"):
        audit.require(reference.exact_json_value(document[key], previous_registration[key]), "unchanged-observation-definition:" + key)
    audit.require(document["frozenIndependentVerifier"] == {"path": OLD_VERIFIER, "sha256": OLD_VERIFIER_SHA256},
                  "original-independent-formulas-unchanged")
    closure_spec = document["sourcePanelClosureAudit"]
    closure_path = path_under(root, closure_spec["path"])
    audit.require(sha(closure_path) == closure_spec["sha256"], "source-supplementary-closure-audit-hash")
    closure = read(closure_path)
    audit.require(sha(path_under(root, closure["verifierPath"])) == closure["verifierSha256"],
                  "source-supplementary-closure-verifier-hash")
    expected_implementation = implementation_names(document, registration_name, closure)
    # This module instance is private. Only binding constants change; no reader,
    # numerical function, production implementation or observation state does.
    reference.REGISTRATION = registration_name
    reference.REGISTRATION_SHA256 = REGISTRATION_SHA256
    reference.IMPLEMENTATION = expected_implementation
    data = reference.binding(output, panel, root, audit)
    _, source_manifest, protocol, symbols, days, lineage, bound_files = data
    audit.require(protocol["implementation"][NEW_VERIFIER] == VERIFIER_SOURCE_SHA256,
                  "current-independent-adapter-frozen-in-archive")
    audit.require(protocol["implementation"].get(closure_spec["path"]) == closure_spec["sha256"] and
                  protocol["implementation"].get(closure["verifierPath"]) == closure["verifierSha256"],
                  "closure-audit-and-verifier-in-execution-archive")
    audit.require(reference.exact_json_value(protocol["registration"], document), "strict-supplemental-registration-types")
    for name, expected in document["frozenObservationImplementation"].items():
        audit.require(protocol["implementation"].get(name) == expected and sha(path_under(root, name)) == expected,
                      "frozen-observation-dependency:" + name)
    for name in ("frozenBuilder", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier"):
        item = document[name]
        path = path_under(root, item["path"])
        audit.require(sha(path) == item["sha256"] == protocol["implementation"].get(item["path"]),
                      "registered-reference-binding:" + name)
        bound_files[path] = item["sha256"]
    fixed = document["fixedPopulation"]
    audit.require(symbols == fixed["symbols"] and type(fixed["count"]) is int and len(symbols) == fixed["count"] and
                  reference.digest(symbols) == fixed["sha256"], "unchanged-original-fixed-symbol-frame")

    source_registration_spec = document["sourcePanelRegistration"]
    source_registration = read(path_under(root, source_registration_spec["path"]))
    source_receipt, source_audit, source_summary = [read(panel / name) for name in (
        "completion.json", "independent-verification.json", "summary.json")]
    audit.require(source_manifest["registration"] == source_registration and
                  source_manifest["registrationSha256"] == source_receipt["registrationSha256"] ==
                  source_audit["registrationSha256"] == source_registration_spec["sha256"],
                  "audited-source-panel-registration-chain")
    audit.require(source_registration["fixedPopulation"] == fixed and
                  source_registration["sample"]["signalStart"] == document["sample"]["start"] and
                  source_registration["sample"]["signalEnd"] == document["sample"]["end"],
                  "source-panel-fixed-sample-and-target-period")
    audit.require(source_audit.get("schema") == "supplemental-leadership-panel-independent-verification-v1" and
                  source_audit.get("verifierSha256") == document["sourcePanelVerifier"]["sha256"],
                  "source-mathematical-audit-current-verifier")
    audit.require(reference.exact_json_value(source_audit.get("implementation"), source_manifest["implementation"]),
                  "source-mathematical-audit-implementation-binding")
    expected_results = {name: item["sha256"] for name, item in source_receipt["files"].items()}
    expected_results["completion.json"] = document["input"]["filesSha256"]["completion.json"]
    audit.require(source_audit.get("resultFileHashes") == expected_results, "source-mathematical-audit-output-chain")
    source_sample = source_registration["sample"]
    market_sample = document["sample"]
    audit.require((market_sample["start"], market_sample["end"], market_sample["symbols"], market_sample["sessions"], market_sample["rows"], market_sample["snapshotFingerprint"]) ==
                  (source_sample["signalStart"], source_sample["signalEnd"], source_sample["expectedFullSecurities"], source_sample["expectedScoredSessions"], source_sample["expectedFeatureRows"], source_sample["expectedFullSnapshotFingerprint"]),
                  "source-feature-and-observation-exact-sample-identity")
    source_calendar = source_manifest["inputs"]["calendar"]["dates"]
    audit.require(all(day <= market_sample["end"] for day in source_calendar) and
                  sum(day < market_sample["start"] for day in source_calendar) == source_sample["expectedWarmupSessions"],
                  "source-calendar-no-future-and-exact-feature-warmup")
    audit.require(type(source_audit.get("targetSessions")) is int and
                  source_audit["targetSessions"] == source_receipt["targetSessions"] == source_summary["targetSessions"] ==
                  source_sample["expectedScoredSessions"] == len(days), "source-target-session-counts")
    audit.require(type(source_audit.get("warmupSessions")) is int and
                  source_audit["warmupSessions"] == source_summary["warmupSessions"] == source_sample["expectedWarmupSessions"],
                  "source-feature-warmup-count-is-not-role-history")
    audit.require(source_audit["featureRows"] == source_sample["expectedFeatureRows"] == len(days) * len(symbols),
                  "source-target-feature-row-count")
    for obj in (source_manifest, source_summary, source_audit):
        audit.require(obj.get("certified") is False and obj.get("targetAchieved") is False, "source-panel-uncertified")
    audit.require(source_audit.get("strategyReturnsCalculated") is False and source_audit.get("forwardReturnsCalculated") is False and
                  source_audit.get("backwardPriceReturnsCalculated") is True,
                  "source-audit-no-forward-or-strategy-result")
    source_archive = panel / "execution-sources.zip"
    audit.require(sha(source_archive) == source_manifest["executionArchiveSha256"], "source-panel-whole-code-archive")
    reference.archive_matches(source_archive, source_manifest["implementation"], root, audit)
    for name, expected in source_manifest["implementation"].items():
        bound_files[path_under(root, name)] = expected

    # The mathematical source replay explicitly did not require the entire
    # supplemental producer closure. Keep the supplementary closure audit a
    # distinct hash-bound statement, not an expansion of the original scope.
    audit.require(closure.get("passed") is True and type(closure.get("failureCount")) is int and closure["failureCount"] == 0,
                  "source-supplementary-closure-audit-passed")
    audit.require(closure.get("sampleManifestHash") == document["input"]["filesSha256"]["manifest.json"] and
                  closure.get("completionSha256") == document["input"]["filesSha256"]["completion.json"] and
                  type(closure.get("targetSessions")) is int and closure["targetSessions"] == len(days) and
                  type(closure.get("featureRows")) is int and closure["featureRows"] == len(days) * len(symbols),
                  "source-supplementary-closure-audit-population-chain")
    bound_files[closure_path] = closure_spec["sha256"]
    audit.require(exact_inventory(panel) == reference.INPUT_FILES, "exact-nine-source-panel-files")
    audit.require(exact_inventory(output) - {"independent-verification.json"} == reference.OUTPUT_FILES | {"completion.json"},
                  "exact-observation-output-files")
    return data


def verify(output: Path, panel: Path | None = None, root: Path = ROOT) -> dict:
    """Return a read-only report; synthetic fixtures can explicitly patch registration."""
    output, panel, root = Path(output).resolve(), Path(panel or DEFAULT_PANEL).resolve(), Path(root).resolve()
    started = time.perf_counter()
    report = {"schema": "supplemental-market-state-independent-verification-v1",
        "verifiedAt": datetime.now(timezone.utc).isoformat(), "output": str(output), "inputPanel": str(panel),
        "roleRows": 0, "dailyRows": 0, "verifierSha256": VERIFIER_SOURCE_SHA256,
        "reusedIndependentVerifierSha256": OLD_VERIFIER_SHA256,
        "newStrategyTrials": 0, "returnsCalculated": False, "certified": False, "targetAchieved": False,
        "scope": ["Independent full-population E20/EA aggregates, fixed five-target-day intersections, all tri-state Q predicates, adjacent role changes and exact sidecars/EOF, using unchanged frozen independent formulas.",
            "Role state starts empty on the first target day; first-day prior roles and first five matched comparisons are unavailable. Feature warmup rows are not role history.",
            "Exact source/output hashes, new execution closure, unchanged observation dependencies, source feature mathematical-audit chain and separately registered supplementary closure audit."],
        "limitations": ["The already audited feature panel is a trusted input; no raw-price reconstruction or production market implementation is called.",
            "Base eligibility is an unchanged metadata sidecar. Neither complete eligibility decisions nor market influence, trader control, tradability, corporate-action returns or net30 probability are certified.",
            "This fixed local survivor sample and previously exposed supplemental interval are not historical whole-market coverage or a pristine holdout.",
            "Synthetic success alone is not an actual-market verification."]}
    try:
        reference = reference_module(root)
    except Exception as error:
        report.update(passed=False, checks=1, failureCount=1,
                      issues=[{"check": "frozen-independent-reference-unavailable", "errorType": type(error).__name__, "message": str(error)}],
                      elapsedSeconds=time.perf_counter() - started)
        return report
    audit = reference.Audit()
    try:
        audit.require(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "new-verifier-source-at-start")
        data = supplemental_binding(reference, output, panel, root, audit)
        report.update(protocolHash=data[2]["protocolHash"], inputManifestSha256=data[5]["inputManifestSha256"],
                      implementation=data[2]["implementation"],
                      resultFileHashes={name: sha(output / name) for name in reference.OUTPUT_FILES | {"completion.json"}},
                      sourcePanelClosureAudit=data[0]["sourcePanelClosureAudit"])
        # audit_rows initializes previous=None and an empty five-session deque;
        # only dates returned by binding (the target interval) are consumed.
        report.update(reference.audit_rows(output, panel, data, audit))
        for path, expected in data[-1].items():
            audit.require(sha(path) == expected, "bound-file-unchanged:" + str(path))
        audit.require(exact_inventory(panel) == reference.INPUT_FILES, "source-panel-inventory-unchanged")
        audit.require(exact_inventory(output) - {"independent-verification.json"} == reference.OUTPUT_FILES | {"completion.json"},
                      "output-inventory-unchanged")
        report["sourceHashes"] = {str(path): expected for path, expected in data[-1].items()}
    except Exception as error:
        audit.check(False, "supplemental-independent-replay-exception", errorType=type(error).__name__, message=str(error))
    try:
        audit.check(sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256 and sha(root / OLD_VERIFIER) == OLD_VERIFIER_SHA256,
                    "new-and-reused-verifier-sources-unchanged")
    except OSError as error:
        audit.check(False, "verifier-source-unavailable", message=str(error))
    report.update(passed=audit.failures == 0, checks=audit.checks, failureCount=audit.failures,
                  issues=audit.issues, elapsedSeconds=time.perf_counter() - started)
    return report


def save_report(output, report):
    name = "independent-verification.json" if report["passed"] else "independent-verification-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = Path(output) / name
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = verify(args.output, DEFAULT_PANEL)
    saved = save_report(args.output, result)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"], "failureCount": result["failureCount"], "report": str(saved)}, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
