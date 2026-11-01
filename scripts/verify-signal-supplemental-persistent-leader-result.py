#!/usr/bin/env python3
"""Independent replay of the unchanged leader method on its supplemental frame.

The frozen independent verifier supplies classification and outcome arithmetic;
neither its hard-coded main nor any production detector/evaluator is called.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
from statistics import mean
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-persistent-leader-v1.json"
REGISTRATION_SHA256 = "950c21a066bbe84c16212a5758052da3c188c96ae9339011bb915c297c85da6e"
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/signal-supplemental-persistent-leader-v1"
OLD_VERIFIER = "scripts/verify-signal-persistent-leader-result.py"
OLD_VERIFIER_SHA256 = "3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222"
NEW_VERIFIER = "scripts/verify-signal-supplemental-persistent-leader-result.py"
NEW_RUNNER = "scripts/run-signal-supplemental-persistent-leader.py"
NEW_TESTS = "tests/test_signal_supplemental_persistent_leader.py"
CANDIDATE_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
CONTROL_SEED = "a-trade-signal-same-day-base-pool-control-v1"
# Exact original normalized config, excluding only startDate/endDate; no
# production configuration normalizer is imported by this verifier.
FROZEN_CONFIG_WITHOUT_DATES_SHA256 = "8ef9e676196b91c9f685cca012ed7e94bd2da40ec0c32536ddc53b27d26c9d0d"
PANEL_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
               "summary.json", "execution-sources.zip", "attempt.json"}
ATTEMPT_FILES = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
                 "result": "result.json.gz", "summary": "summary.json", "selections": "selection.jsonl.gz",
                 "predicates": "predicates.jsonl.gz", "executionSources": "execution-sources.zip"}
ROOT_FILES = {"protocol": "protocol.json", "selection": "selection.json", "snapshot": "snapshot.json",
              "executionSources": "execution-sources.zip"}


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SHA256 = sha(VERIFIER_SOURCE_PATH)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    result = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).iterencode(value):
        result.update(chunk.encode("utf-8"))
    return result.hexdigest()


def exact(a, b):
    return encoded(a) == encoded(b)


def unique_pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def nonfinite(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def loads(content):
    return json.loads(content, object_pairs_hook=unique_pairs, parse_constant=nonfinite)


def read(path):
    return loads(Path(path).read_bytes())


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield loads(line)


def relative(root, name):
    part = PurePosixPath(name)
    if not part.parts or part.is_absolute() or ".." in part.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe relative artifact path")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact escapes root")
    return path


def inventory(directory):
    result = set()
    for path in [directory, *directory.rglob("*")]:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Artifact tree contains a link")
        if path.is_file():
            result.add(path.relative_to(directory).as_posix())
    return result


def private_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Audit:
    def __init__(self):
        self.checks, self.failures, self.issues = 0, 0, []
        self.bound = {}

    def check(self, ok, label, **details):
        self.checks += 1
        if not ok:
            self.failures += 1
            if len(self.issues) < 100:
                self.issues.append({"check": label, **details})
        return bool(ok)

    def require(self, ok, label, **details):
        if not self.check(ok, label, **details):
            raise ValueError(label)

    def pin(self, path, expected, label):
        path = Path(path).resolve()
        self.require(type(expected) is str and len(expected) == 64 and sha(path) == expected, label)
        self.bound[path] = expected
        return path


def archive(path, implementation, root, audit, expected_sha=None):
    if expected_sha is not None:
        audit.pin(path, expected_sha, "whole-execution-archive")
    with zipfile.ZipFile(path) as zipped:
        names = zipped.namelist()
        audit.require(len(names) == len(set(names)) and set(names) == set(implementation), "exact-archive-members")
        for name, expected in implementation.items():
            audit.pin(relative(root, name), expected, "current-execution-dependency:" + name)
            audit.require(hashlib.sha256(zipped.read(name)).hexdigest() == expected, "archived-execution-dependency:" + name)


def source_binding(root, document, audit):
    spec = document["sourceMaterialization"]
    directory = relative(root, spec["directory"])
    pins = spec["files"]
    audit.require(inventory(directory) == set(pins), "source-exact-file-tree")
    for name, expected in pins.items():
        audit.pin(relative(directory, name), expected, "materialized-source:" + name)
    protocol, manifest, completion, proof = [read(directory / name) for name in
        ("protocol.json", "manifest.json", "completion.json", "independent-verification.json")]
    audit.require(digest({k: v for k, v in protocol.items() if k != "protocolHash"}) == protocol["protocolHash"] == spec["protocolHash"], "source-protocol-hash")
    audit.require(protocol["registrationSha256"] == spec["registrationSha256"] and
                  all(item["protocolHash"] == spec["protocolHash"] for item in (manifest, completion, proof)), "source-receipt-audit-identity")
    audit.require(proof.get("passed") is True and type(proof.get("failureCount")) is int and proof["failureCount"] == 0,
                  "source-independent-audit-passed")
    audit.require(proof["verifierSha256"] == spec["independentVerifierSha256"] == protocol["implementation"]["scripts/verify-signal-supplemental-source.py"] and
                  proof["completionSha256"] == pins["completion.json"] and exact(proof["implementation"], protocol["implementation"]) and
                  exact(proof["sourceHashes"], protocol["inputFilesSha256"]) and exact(proof["resultFileHashes"], completion["files"]), "source-audit-complete-identity-chain")
    audit.require(set(pins) == set(completion["files"]) | {"completion.json", "independent-verification.json"} and
                  all(pins[name] == expected for name, expected in completion["files"].items()), "source-completion-closure")
    for item in (protocol, manifest, completion, proof):
        audit.require(all(item.get(flag) is False for flag in ("returnsCalculated", "featuresCalculated", "certified", "targetAchieved")), "source-no-feature-or-return-claim")
    fixed, sample = document["fixedPopulation"], document["sample"]
    audit.require(exact(protocol["registration"]["fixedPopulation"], fixed) and manifest["fixedSymbols"] == fixed["symbols"] and
                  manifest["cutoff"] == sample["signalEnd"] and manifest["benchmarkSymbol"] == sample["selection"]["benchmarkSymbol"], "source-fixed-frame-cutoff")
    selected = sorted(fixed["symbols"] + [manifest["benchmarkSymbol"]])
    audit.require([r["symbol"] for r in manifest["sourceFiles"]] == selected and len(set(selected)) == len(selected), "source-keeps-all-fixed-and-benchmark")
    audit.require([r["symbol"] for r in manifest["excludedSources"]] == protocol["registration"]["excludedOtherPinnedSymbols"] and
                  proof["excludedOtherPinnedSymbols"] == len(manifest["excludedSources"]), "source-exclusions-not-hidden")
    physical = {}
    for row in manifest["sourceFiles"]:
        symbol = row["symbol"]
        name = f"tdx/vipdoc/{symbol[:2]}/lday/{symbol}.day"
        audit.require(row["path"] == name and pins[name] == row["prefixSha256"] and type(row["prefixRecords"]) is int and
                      row["prefixRecords"] > 0 and row["prefixBytes"] == row["prefixRecords"] * 32 and row["firstDate"] <= row["lastDate"] <= sample["signalEnd"], "source-prefix-identity")
        physical[str(relative(directory, name))] = pins[name]
    audit.require(set(completion["files"]) == {r["path"] for r in manifest["sourceFiles"]} |
                  {"workspace/market.sqlite3", "protocol.json", "attempt.json", "manifest.json", "execution-sources.zip"}, "source-no-extra-catalog-or-cache")
    audit.require(manifest.get("prefixNumericQualityBefore2023Verified") is False and manifest["workspace"]["path"] == "workspace/market.sqlite3" and
                  manifest["workspace"]["sha256"] == pins["workspace/market.sqlite3"] and manifest["workspace"]["tdxPath"] == str(directory / "tdx"), "source-workspace-and-numeric-scope")
    physical[str(directory / "workspace/market.sqlite3")] = pins["workspace/market.sqlite3"]
    for name, expected in protocol["inputFilesSha256"].items():
        audit.pin(relative(root, name), expected, "upstream-source-evidence:" + name)
    archive(directory / "execution-sources.zip", {**protocol["implementation"], **protocol["metadata"]}, root, audit, protocol["executionArchiveSha256"])
    return {"directory": directory, "protocol": protocol, "physical": physical,
            "identity": {"directory": str(directory), "protocolHash": spec["protocolHash"], "registrationSha256": spec["registrationSha256"],
                         "files": pins, "inputFilesSha256": protocol["inputFilesSha256"]}}


def panel_binding(panel, root, document, source, audit):
    requested, sample = document["registrationPrerequisites"], document["sample"]
    audit.require(panel == relative(root, requested["panelDirectory"]), "registered-panel-directory")
    pins = {**requested["panelFilesSha256"], "completion.json": requested["panelCompletionSha256"]}
    audit.require(set(pins) == PANEL_FILES | {"completion.json", "independent-verification.json"} and inventory(panel) == set(pins), "exact-nine-panel-files")
    for name, expected in pins.items():
        audit.pin(panel / name, expected, "source-panel-file:" + name)
    manifest, receipt, proof, summary = [read(panel / name) for name in ("manifest.json", "completion.json", "independent-verification.json", "summary.json")]
    inputs = manifest["inputs"]
    audit.require(requested["panelManifestSha256"] == pins["manifest.json"] == receipt["sampleManifestHash"] == proof["sampleManifestHash"] and
                  requested["actualPanelAuditSha256"] == pins["independent-verification.json"], "panel-primary-identities")
    audit.require(set(receipt["files"]) == PANEL_FILES and all(exact(item, {"path": name, "sha256": pins[name]}) for name, item in receipt["files"].items()), "panel-completion-files")
    source_doc = read(relative(root, document["sourcePanelRegistration"]["path"]))
    source_sha = document["sourcePanelRegistration"]["sha256"]
    audit.require(exact(manifest["registration"], source_doc) and manifest["registrationSha256"] == receipt["registrationSha256"] == proof["registrationSha256"] == source_sha, "feature-registration-chain")
    audit.require(exact(source_doc["fixedPopulation"], document["fixedPopulation"]) and exact(source_doc["sourceMaterialization"], document["sourceMaterialization"]), "feature-materialization-frame-chain")
    for key in ("signalStart", "signalEnd", "warmupStart", "expectedFullSnapshotFingerprint", "expectedFullSecurities", "expectedFullBars", "expectedScoredSessions", "expectedFeatureRows", "expectedWarmupSessions"):
        audit.require(exact(sample[key], source_doc["sample"][key]), "feature-sample-field:" + key)
    audit.require(proof.get("passed") is True and type(proof.get("failureCount")) is int and proof["failureCount"] == 0 and
                  proof.get("schema") == "supplemental-leadership-panel-independent-verification-v1" and
                  proof["verifierSha256"] == document["sourcePanelVerifier"]["sha256"], "feature-mathematical-audit")
    audit.require(exact(proof["implementation"], manifest["implementation"]) and exact(proof["resultFileHashes"], {k: v for k, v in pins.items() if k != "independent-verification.json"}), "feature-audit-closure")
    for item in (manifest, proof, summary):
        audit.require(item.get("certified") is False and item.get("targetAchieved") is False, "feature-no-certification")
    audit.require(proof.get("strategyReturnsCalculated") is False and proof.get("forwardReturnsCalculated") is False and
                  proof.get("backwardPriceReturnsCalculated") is True, "feature-audit-not-forward-outcomes")
    symbols, calendar = inputs["symbols"], inputs["calendar"]["dates"]
    audit.require(symbols == document["fixedPopulation"]["symbols"] and inputs["symbolsSha256"] == digest(symbols) and
                  type(inputs["symbolCount"]) is int and inputs["symbolCount"] == len(symbols) and set(inputs["securities"]) == set(symbols), "feature-exact-symbol-identity")
    audit.require(calendar == sorted(set(calendar)) and all(type(d) is str and date.fromisoformat(d).isoformat() == d and sample["warmupStart"] <= d <= sample["signalEnd"] for d in calendar) and
                  inputs["calendar"]["sha256"] == digest(calendar), "canonical-no-future-feature-calendar")
    dates = [d for d in calendar if sample["signalStart"] <= d <= sample["signalEnd"]]
    audit.require(type(proof["targetSessions"]) is int and proof["targetSessions"] == receipt["targetSessions"] == summary["targetSessions"] == sample["expectedScoredSessions"] == len(dates) and
                  type(proof["featureRows"]) is int and proof["featureRows"] == receipt["featureRows"] == sample["expectedFeatureRows"] == len(dates)*len(symbols) and
                  type(proof["warmupSessions"]) is int and proof["warmupSessions"] == summary["warmupSessions"] == sample["expectedWarmupSessions"] == len(calendar)-len(dates), "feature-target-warmup-row-counts")
    audit.require(inputs["snapshotFingerprint"] == sample["expectedFullSnapshotFingerprint"] and inputs["retainedBarCount"] == sample["expectedFullBars"] and
                  exact(manifest["physicalSourceHashes"], source["physical"]), "feature-exact-raw-source-binding")
    archive(panel / "execution-sources.zip", manifest["implementation"], root, audit, manifest["executionArchiveSha256"])
    closure = read(relative(root, document["sourcePanelClosureAudit"]["path"]))
    audit.require(closure.get("passed") is True and type(closure.get("failureCount")) is int and closure["failureCount"] == 0 and
                  closure["sampleManifestHash"] == pins["manifest.json"] and closure["completionSha256"] == pins["completion.json"] and
                  type(closure["targetSessions"]) is int and closure["targetSessions"] == len(dates) and
                  type(closure["featureRows"]) is int and closure["featureRows"] == len(dates)*len(symbols), "supplementary-feature-closure-audit")
    audit.pin(relative(root, closure["verifierPath"]), closure["verifierSha256"], "supplementary-closure-verifier")
    identity = {"directory": str(panel), "panelManifestSha256": pins["manifest.json"], "panelCompletionSha256": pins["completion.json"],
        "actualPanelAuditSha256": pins["independent-verification.json"], "panelFilesSha256": requested["panelFilesSha256"],
        "featureVersion": "daily-leadership-raw-v1", "snapshotFingerprint": inputs["snapshotFingerprint"], "calendarHash": inputs["calendar"]["sha256"],
        "sourceMaterialization": source["identity"], **{k: document[k] for k in ("sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit")}}
    audit.require(manifest["featureVersion"] == identity["featureVersion"], "frozen-feature-version")
    return manifest, identity, dates, closure


def prepare(output, panel, root, audit):
    registration = Path(REGISTRATION).resolve()
    audit.pin(registration, REGISTRATION_SHA256, "registered-supplemental-method")
    document = read(registration)
    audit.require(document["candidateId"] == CANDIDATE_ID and document.get("registeredAt"), "original-candidate-identity")
    for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"):
        item = document[key]
        audit.pin(relative(root, item["path"]), item["sha256"], "registered-reference:" + key)
    audit.require(document["frozenIndependentVerifier"] == {"path": OLD_VERIFIER, "sha256": OLD_VERIFIER_SHA256}, "frozen-independent-verifier-identity")
    original = read(relative(root, document["frozenRegistration"]["path"]))
    for key in ("candidateId", "objective", "featureEvidence", "baseEligibility", "trigger", "selection"):
        audit.require(exact(document[key], original[key]), "unchanged-method:" + key)
    audit.require(exact({k: v for k, v in document["eventPolicy"].items() if k != "cutoff"},
                        {k: v for k, v in original["eventPolicy"].items() if k != "cutoff"}), "unchanged-event-policy-except-cutoff-description")
    sample, fixed = document["sample"], document["fixedPopulation"]
    audit.require(type(fixed["count"]) is int and fixed["count"] == len(fixed["symbols"]) == sample["expectedFullSecurities"] and
                  fixed["symbols"] == sorted(set(fixed["symbols"])) and fixed["sha256"] == digest(fixed["symbols"]), "fixed-frame-identity")
    for key in ("expectedFullSecurities", "expectedFullBars", "expectedScoredSessions", "expectedFeatureRows", "expectedWarmupSessions"):
        audit.require(type(sample[key]) is int and sample[key] > 0, "registered-count-type:" + key)
    for key in ("signalStart", "signalEnd", "warmupStart", "latestPermittedObservation"):
        audit.require(type(sample[key]) is str and date.fromisoformat(sample[key]).isoformat() == sample[key], "registered-canonical-date:" + key)
    audit.require(sample["warmupStart"] < sample["signalStart"] <= sample["signalEnd"] == sample["latestPermittedObservation"], "registered-window-order")
    expected_selection = {"mode": "explicit", "source": "local", "markets": ["sh", "sz"], "boards": ["main"],
                          "benchmarkSymbol": "sh000300", "symbols": fixed["symbols"]}
    audit.require(exact(sample["selection"], expected_selection), "explicit-fixed-frame-selection")
    source = source_binding(root, document, audit)
    panel = Path(panel).resolve() if panel is not None else relative(root, document["registrationPrerequisites"]["panelDirectory"])
    manifest, panel_identity, dates, closure = panel_binding(panel, root, document, source, audit)
    protocol, snapshot, selection = [read(output / name) for name in ("protocol.json", "snapshot.json", "selection.json")]
    ph = digest(protocol)
    attempt = output / "attempts/attempt-0001"
    receipt = read(attempt / "completion.json")
    root_names = set(ROOT_FILES.values()) | {"freeze-attempt.json", "events.jsonl"}
    expected_inventory = root_names | {"attempts/attempt-0001/" + n for n in set(ATTEMPT_FILES.values()) | {"attempt.json", "completion.json"}}
    actual_inventory = inventory(output)
    reports = {name for name in actual_inventory if "/" not in name and
               (name == "independent-result-verification.json" or (name.startswith("independent-result-verification-failed-") and name.endswith(".json")))}
    audit.require(actual_inventory - reports == expected_inventory, "one-successful-attempt-exact-file-tree")
    for name in expected_inventory:
        audit.pin(relative(output, name), sha(relative(output, name)), "completed-output-file:" + name)
    audit.require(exact(protocol["registration"], document) and protocol["registrationSha256"] == REGISTRATION_SHA256 and
                  protocol["schema"] == "supplemental-persistent-price-leader-v1" and protocol["stage"] == "qualified-supplemental-validation", "protocol-exact-registration")
    audit.require(protocol["signalStart"] == sample["signalStart"] and protocol["signalEnd"] == protocol["observationCutoff"] == protocol["latestPermittedObservation"] == sample["signalEnd"], "protocol-target-and-observation-window")
    audit.require(Path(protocol["workspace"]).resolve() == source["directory"] / "workspace" and exact(protocol["sourceBinding"], source["identity"]) and
                  exact(protocol["panelBinding"], panel_identity) and exact(receipt["panelBinding"], panel_identity), "protocol-source-panel-identities")
    audit.require(exact(selection, expected_selection) and exact(protocol["selection"], selection) and exact(snapshot["selection"], selection) and
                  protocol["selectionSha256"] == sha(output / "selection.json") and protocol["snapshotSha256"] == sha(output / "snapshot.json"), "frozen-real-explicit-selection")
    config = protocol["config"]
    audit.require(digest({k: v for k, v in config.items() if k not in ("startDate", "endDate")}) == FROZEN_CONFIG_WITHOUT_DATES_SHA256 and
                  config["startDate"] == sample["signalStart"] and config["endDate"] == sample["signalEnd"], "unchanged-full-normalized-configuration")
    audit.require(protocol["strategyOrder"] == [CANDIDATE_ID] and protocol["controlSeed"] == CONTROL_SEED and
                  type(protocol["topNPerStrategy"]) is int and protocol["topNPerStrategy"] == 5 and
                  exact(protocol["reportHorizonsTradingDays"], [5, 10, 20, 40, 60, 120, 250]) and
                  protocol["holdingDeadline"] is None and protocol["stopLoss"] is None and protocol["targetNetReturnPct"] == 30 and
                  protocol["eventBudget"] == 100000, "frozen-target-selection-and-lifecycle-constants")
    audit.require(protocol["maxSymbols"] is None and protocol["pilot"] is False and protocol["diagnosticUnverifiedHistory"] is True and
                  all(protocol[k] is False for k in ("certified", "targetAchieved", "historicalEligibilityVerified", "comparableRightsVerified")), "full-diagnostic-uncertified-protocol")
    names = set(document["frozenMethodImplementation"]) | set(source["protocol"]["implementation"]) | set(source["protocol"]["metadata"]) | {
        registration.relative_to(root).as_posix(), NEW_RUNNER, NEW_VERIFIER, NEW_TESTS, closure["verifierPath"]}
    names.update(document[key]["path"] for key in ("frozenRunner", "frozenRegistration", "frozenIndependentVerifier", "sourcePanelRegistration", "sourcePanelVerifier", "sourcePanelClosureAudit"))
    audit.require(set(protocol["implementation"]) == names and protocol["implementation"][NEW_VERIFIER] == VERIFIER_SHA256, "exact-new-execution-closure-and-self")
    for name, expected in document["frozenMethodImplementation"].items():
        audit.require(protocol["implementation"].get(name) == expected, "unchanged-frozen-method-dependency:" + name)
    archive(output / "execution-sources.zip", protocol["implementation"], root, audit, protocol["executionArchiveSha256"])
    archive(attempt / "execution-sources.zip", protocol["implementation"], root, audit)
    audit.require(receipt["protocolHash"] == ph and type(receipt["attempt"]) is int and receipt["attempt"] == 1 and set(receipt["files"]) == set(ATTEMPT_FILES), "one-successful-completion-identity")
    for key, name in ATTEMPT_FILES.items():
        audit.require(exact(receipt["files"][key], {"path": "attempts/attempt-0001/" + name, "sha256": sha(attempt / name)}), "completed-artifact-identity:" + key)
    audit.require(exact(receipt["frozenFiles"], {key: {"path": name, "sha256": sha(output / name)} for key, name in ROOT_FILES.items()}), "four-frozen-root-files")
    attempt_record, freeze_record = read(attempt / "attempt.json"), read(output / "freeze-attempt.json")
    audit.require(receipt["attemptSha256"] == sha(attempt / "attempt.json") and attempt_record["protocolHash"] == ph and type(attempt_record["attempt"]) is int and attempt_record["attempt"] == 1 and
                  freeze_record["registrationSha256"] == REGISTRATION_SHA256 and
                  datetime.fromisoformat(freeze_record["startedAt"]) <= datetime.fromisoformat(attempt_record["startedAt"]), "freeze-precedes-only-attempt")
    lifecycle = [loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    audit.require([r["event"] for r in lifecycle] == ["protocol_registered", "study_started", "scoring_completed", "study_succeeded"] and
                  all(r["protocolHash"] == ph for r in lifecycle) and exact(lifecycle[-1]["receipt"], receipt), "single-attempt-lifecycle-log")
    reference = private_module(root / OLD_VERIFIER, "_independent_supplemental_persistent_reference")
    source_loader = private_module(root / "scripts/run-strategy-search.py", "_independent_supplemental_persistent_source")
    dataset = source_loader.bound_snapshot(reference.snapshot_universe(source["directory"] / "workspace", selection,
        sample["signalEnd"], sample["signalStart"]), sample["signalStart"], sample["signalEnd"])
    symbols = sorted(s["symbol"] for s in dataset["securities"])
    audit.require(symbols == fixed["symbols"] and len(set(symbols)) == len(dataset["securities"]) and
                  sum(len(s["bars"]) for s in dataset["securities"]) == snapshot["barCount"] == sample["expectedFullBars"], "independently-loaded-source-counts")
    audit.require(dataset["fingerprint"] == snapshot["fingerprint"] == receipt["snapshotFingerprint"] == sample["expectedFullSnapshotFingerprint"] and
                  digest(dataset) == snapshot["fullDatasetContentSha256"] == manifest["inputs"]["fullDatasetContentSha256"], "independently-loaded-full-source-identity")
    audit.require(snapshot["symbols"] == symbols and snapshot["sourceStart"] == sample["signalStart"] and snapshot["observationCutoff"] == sample["signalEnd"] and
                  exact(snapshot["quality"], dataset.get("dataQuality", {})) and exact(snapshot["coverage"], dataset.get("universeCoverage", {})), "snapshot-metadata")
    calendar = dataset["calendar"]
    audit.require(calendar == sorted(set(calendar)) and all(date.fromisoformat(d).isoformat() == d and d <= sample["signalEnd"] for d in calendar) and
                  [d for d in calendar if sample["warmupStart"] <= d <= sample["signalEnd"]] == manifest["inputs"]["calendar"]["dates"] and
                  [d for d in calendar if sample["signalStart"] <= d <= sample["signalEnd"]] == dates, "raw-calendar-no-future-and-target-identity")
    audit.require(all(bar["date"] <= sample["signalEnd"] for security in dataset["securities"] + [dataset["benchmark"]] for bar in security["bars"]), "no-post-cutoff-source-bar")
    return {"reference": reference, "document": document, "source": source, "panel": panel, "manifest": manifest, "panelIdentity": panel_identity,
            "protocol": protocol, "protocolHash": ph, "snapshot": snapshot, "receipt": receipt, "attempt": attempt,
            "dataset": dataset, "dates": dates, "symbols": symbols, "outputInventory": actual_inventory}


def numeric_metadata(value, audit, path="result"):
    """Reject booleans/non-finite values masquerading as outcome numeric fields."""
    integer_keys = {"hits", "denominator", "unresolved", "quantity", "tradingDay", "tradingDays", "horizonSessions",
                    "completeCalendarCohort", "eligibleEntryCohort", "missingOrInvalidDataExcluded", "rightCensoredBeforeHorizon",
                    "potentialFollowupSessions", "observedFollowupSessions"}
    numeric_keys = {"price", "fee", "proceeds", "investedCapital", "remainingCash", "netReturnPct", "hitRate", "lower", "upper",
                    "medianHitTradingDays", "waitingMedianSessions", "grossMaePct"}
    if isinstance(value, dict):
        for key, item in value.items():
            label = path + "." + key
            if item is not None and (key in integer_keys or key.endswith("Count")):
                audit.check(type(item) is int and item >= 0, "outcome-integer-type", field=label)
            elif item is not None and key in numeric_keys:
                audit.check(type(item) in (int, float) and math.isfinite(item), "outcome-numeric-type", field=label)
            elif isinstance(item, float):
                audit.check(math.isfinite(item), "outcome-finite-number", field=label)
            if key.endswith("Counts") and isinstance(item, dict):
                audit.check(all(type(n) is int and n >= 0 for n in item.values()), "outcome-count-map-types", field=label)
            numeric_metadata(item, audit, label)
    elif isinstance(value, list):
        for item in value:
            numeric_metadata(item, audit, path)


def replay(prepared, audit):
    old, document, protocol = prepared["reference"], prepared["document"], prepared["protocol"]
    dataset, symbols, dates = prepared["dataset"], prepared["symbols"], prepared["dates"]
    panel, manifest, binding, attempt = prepared["panel"], prepared["manifest"], prepared["panelIdentity"], prepared["attempt"]
    sample, sid, check = document["sample"], CANDIDATE_ID, audit.check
    with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
        result = loads(stream.read())
    summary = read(attempt / "summary.json")
    coverage, selections, candidates, signals = [list(records(attempt / name)) for name in
        ("coverage.jsonl.gz", "selection.jsonl.gz", "candidates.jsonl.gz", "signals.jsonl.gz")]
    expected_summary = {k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")}
    audit.require(set(summary) == set(expected_summary) | {"ranking", "selectionEligible", "performance"} and
                  exact({k: summary[k] for k in expected_summary}, expected_summary) and summary["ranking"] is None and summary["selectionEligible"] is False,
                  "complete-result-summary-identity")
    for obj in (result, summary):
        check(obj["protocolHash"] == prepared["protocolHash"] and obj["snapshotFingerprint"] == dataset["fingerprint"] and
              obj["stage"] == protocol["stage"] and obj["studyKind"] == protocol["studyKind"] and obj["strategyOrder"] == [sid] and
              obj["sampleUse"] == protocol["sampleUse"] and exact(obj["exposure"], document["exposure"]), "result-protocol-identity")
        check(obj["certified"] is False and obj["targetAchieved"] is False and obj["pilot"] is False and obj["diagnosticUnverifiedHistory"] is True and
              obj["historicalEligibilityVerified"] is False and obj["comparableRightsVerified"] is False, "result-diagnostic-only")
    numeric_metadata(result, audit)
    audit.require([r["date"] for r in coverage] == [r["date"] for r in selections] == dates, "exact-target-date-coverage")
    identity = old.identity
    valid_groups = {sid, "control_" + sid}
    audit.require(all(s["strategyId"] in valid_groups and s["signalDate"] in dates and s["symbol"] in symbols for s in signals) and
                  len(signals) == len({identity(s) for s in signals}), "unique-in-frame-signals")
    check(Counter(identity(s) for s in signals) == Counter(identity(e) for e in result["events"] + result["overlapSuppressed"]), "all-and-only-signal-dispositions")
    audit.require(len(result["events"]) == len({identity(e) for e in result["events"]}) and
                  len(result["overlapSuppressed"]) == len({identity(e) for e in result["overlapSuppressed"]}), "unique-event-and-suppression-identities")
    by_day, signals_by_day = defaultdict(list), defaultdict(list)
    for row in candidates:
        audit.require(row["strategyId"] == sid and row["signalDate"] in dates and row["symbol"] in symbols, "candidate-in-frame")
        by_day[row["signalDate"]].append(row)
    for row in signals:
        signals_by_day[row["signalDate"]].append(row)
    securities = {s["symbol"]: s for s in dataset["securities"]}
    raw = {s: {b["date"]: b for b in security["bars"]} for s, security in securities.items()}
    calendar, ci = dataset["calendar"], {d: i for i, d in enumerate(dataset["calendar"])}
    panel_rows, predicate_rows = records(panel / "features.jsonl.gz"), records(attempt / "predicates.jsonl.gz")
    total_rows, total_kinds, total_reasons = 0, Counter(), Counter()
    last_notice = time.perf_counter()
    for cover, selection in zip(coverage, selections):
        day = cover["date"]
        days = calendar[max(0, ci[day]-60):ci[day]+1]
        eligible, expected_signals, kinds, reasons = set(), set(), Counter(), Counter()
        rows = by_day[day]
        candidate_lookup = {r["symbol"]: r for r in rows}
        for symbol in symbols:
            row, record = next(panel_rows, None), next(predicate_rows, None)
            audit.require(row is not None and record is not None, "full-panel-predicate-stream-not-truncated")
            total_rows += 1
            audit.require((row["asOfDate"], row["symbol"]) == (day, symbol) and (record["date"], record["symbol"]) == (day, symbol), "full-population-row-order")
            expected_hashes = {**manifest["inputs"]["securities"][symbol], "benchmarkRawBarsSha256": manifest["inputs"]["benchmark"]["rawBarsSha256"]}
            check(row["featureVersion"] == "daily-leadership-raw-v1" and row["sampleManifestHash"] == binding["panelManifestSha256"] and
                  row["calendarHash"] == binding["calendarHash"] and exact(row["sourceHashes"], expected_hashes), "panel-row-source-identity")
            check(record["panelManifestSha256"] == binding["panelManifestSha256"] and record["panelFeatureFileSha256"] == binding["panelFilesSha256"]["features.jsonl.gz"] and
                  record["calendarHash"] == row["calendarHash"] and exact(record["sourceHashes"], row["sourceHashes"]), "predicate-panel-source-identity")
            audit.require(type(row["baseEligibility"]["eligible"]) is bool, "source-base-eligibility-type")
            bars = [raw[symbol].get(d) for d in days]
            kind, predicates = old.expected_kind(row, days, bars)
            check(record["kind"] == kind, "all-row-independent-classification", day=day, symbol=symbol)
            kinds[record["kind"]] += 1
            reasons[record["reason"]] += 1
            if row["baseEligibility"]["eligible"] is True:
                eligible.add(symbol)
            audit.require(set(record["predicates"]) == {k for k, _, _ in old.GATES} | {"closeAbovePrior5High"}, "exact-six-predicate-fields")
            for key, op, threshold in old.GATES:
                p = record["predicates"][key]
                expected_value = row[key] if old.valid_feature(key, row[key]) else None
                check(p["operator"] == op and type(p["threshold"]) in (int, float) and p["threshold"] == threshold and
                      (p["value"] is None or old.finite(p["value"])) and old.same(p["value"], expected_value), "predicate-value-and-rule", field=key)
            check(all(p["passed"] is None or type(p["passed"]) is bool for p in record["predicates"].values()), "predicate-boolean-or-null-types")
            if predicates is not None:
                check(exact({k: p["passed"] for k, p in record["predicates"].items()}, predicates), "all-evaluable-six-predicate-decisions")
                check(old.same(record["predicates"]["closeAbovePrior5High"]["value"], bars[-1]["close"]) and
                      old.same(record["predicates"]["closeAbovePrior5High"]["threshold"], max(b["high"] for b in bars[-6:-1])) and
                      record["predicates"]["closeAbovePrior5High"]["operator"] == ">", "prior-five-breakout-excludes-current")
            if kind == "signaled":
                expected_signals.add(symbol)
                candidate = candidate_lookup.get(symbol)
                check(candidate is not None, "no-omitted-raw-candidate", day=day, symbol=symbol)
                if candidate is not None:
                    facts = candidate["features"]
                    check(exact(facts["breakoutBars"], [{k: b[k] for k in ("date", "open", "high", "low", "close", "volume")} for b in bars[-6:]]), "candidate-six-raw-bars")
                    check(exact(facts["archivedRankDates20"], row["strengthRankSessionDates20"]) and
                          exact(facts["archivedRankPercentiles20"], row["trailing20DailyStrengthPercentiles"]) and
                          exact(facts["archivedRankCrossSectionCounts20"], row["trailing20StrengthCrossSectionCounts"]) and
                          old.same(mean(facts["archivedRankPercentiles20"]), candidate["meanStrengthPercentile20"]) and
                          old.same(candidate["relativeReturn60Percentile"], row["relativeReturn60Percentile"]), "candidate-archived-rank-lineage")
                    check(exact(candidate["predicates"], record["predicates"]) and exact(candidate["features"], record["features"]), "candidate-predicate-lineage")
        check(set(candidate_lookup) == expected_signals and len(rows) == len(expected_signals), "exact-full-candidate-set")
        ordered = sorted(rows, key=lambda r: (-r["meanStrengthPercentile20"], -r["relativeReturn60Percentile"], r["symbol"]))
        check(exact(rows, ordered) and all(type(r["rank"]) is int and r["rank"] == i+1 and r["selectedTop5"] is (i < 5) for i, r in enumerate(rows)), "lexicographic-full-rank-top-five")
        top = [r["symbol"] for r in rows[:5]]
        check(exact(selection["fullRanking"], [{k: r[k] for k in ("symbol", "rank", "meanStrengthPercentile20", "relativeReturn60Percentile", "selectedTop5")} for r in rows]) and
              all(r["score"] == r["rawScore"] == r["meanStrengthPercentile20"] for r in rows), "selection-full-ranking")
        check(selection["selectedTop5Symbols"] == top and selection["activeExclusionApplied"] is False and selection["certified"] is False and
              type(selection["rawCandidateCount"]) is int and selection["rawCandidateCount"] == len(rows) and selection["strategyId"] == sid and
              selection["panelManifestSha256"] == binding["panelManifestSha256"], "top-five-before-active-no-backfill")
        pool = eligible - set(top)
        controls = sorted(pool, key=lambda s: (hashlib.sha256(f"{CONTROL_SEED}:{day}:{sid}:{s}".encode()).hexdigest(), s))[:len(top)]
        matching = {"baseEligibleCount": len(eligible), "eligibleControlPoolSize": len(pool), "requestedCount": len(top),
                    "matchedCount": len(controls), "shortfall": len(top)-len(controls), "selectedSymbols": controls}
        check(exact(matching, cover["controlMatching"][sid]) and exact(matching, selection["controlMatching"]), "base-eligible-minus-top-five-control-pool")
        expected_daily_signals = rows[:5] + [{"strategyId": "control_"+sid, "pairedStrategyId": sid, "symbol": symbol,
            "signalDate": day, "rank": rank, "score": 0., "rawScore": 0., "scoreBasis": "deterministic-control-hash-not-strategy-score",
            "selectedTop5": False, "panelManifestSha256": binding["panelManifestSha256"]} for rank, symbol in enumerate(controls, 1)]
        check(exact(signals_by_day[day], expected_daily_signals), "exact-strategy-control-signal-order-and-lineage")
        check(exact(cover["evaluatedKinds"], dict(kinds)) and exact(cover["evaluatedReasons"], dict(reasons)) and
              type(cover["evaluatedSecurities"]) is int and sum(kinds.values()) == cover["evaluatedSecurities"] == len(symbols) and
              exact(cover["candidateCounts"], {sid: len(rows)}) and type(cover["eligibleSecurities"]) is int and cover["eligibleSecurities"] == len(eligible) and
              cover["pointInTimeComplete"] is False and cover["panelManifestSha256"] == binding["panelManifestSha256"], "daily-full-row-accounting")
        total_kinds.update(kinds)
        total_reasons.update(reasons)
        if time.perf_counter() - last_notice > 30:
            print(f"Supplemental leader independent replay: {total_rows}/{sample['expectedFeatureRows']} rows", flush=True)
            last_notice = time.perf_counter()
    check(next(panel_rows, None) is None and next(predicate_rows, None) is None and total_rows == sample["expectedFeatureRows"], "exact-feature-predicate-EOF")
    performance = summary["performance"]
    expected_performance = {"scoredSessions": len(dates), "evaluatedSecuritySessions": total_rows, "evaluatedKinds": dict(total_kinds),
        "evaluatedReasons": dict(total_reasons), "candidateCounts": {sid: len(candidates)},
        "top5SignalCount": sum(s["strategyId"] == sid for s in signals), "controlSignalCount": sum(s["strategyId"] != sid for s in signals)}
    check(all(exact(performance[k], v) for k, v in expected_performance.items()), "all-performance-counts")
    for group in result["groups"]:
        selected = [s for s in signals if s["strategyId"] == group["strategyId"]]
        check(type(group["distinctSignalSymbols"]) is int and group["distinctSignalSymbols"] == len({s["symbol"] for s in selected}) and
              type(group["distinctSignalDates"]) is int and group["distinctSignalDates"] == len({s["signalDate"] for s in selected}), "distinct-signal-dependence-counts")
    # This frozen independent helper starts active={} once and does not reset at
    # a year boundary. It audits observed arithmetic, not full execution gates.
    old.audit_outcomes(check, result, signals, dates, {d: i for i, d in enumerate(dates)}, calendar, ci, raw, securities, protocol["config"])
    return {"evaluatedRows": total_rows, "targetSessions": len(dates), "candidateCount": len(candidates),
            "signalCount": len(signals), "events": len(result["events"]), "resultSha256": sha(attempt / "result.json.gz")}


def verify(output: Path, panel: Path | None = None, root: Path = ROOT) -> dict:
    """Read-only independent report; never starts a production trial or writes files."""
    output, root = Path(output).resolve(), Path(root).resolve()
    started, audit = time.perf_counter(), Audit()
    report = {"schema": "supplemental-persistent-leader-independent-verification-v1", "checkedAt": datetime.now(timezone.utc).isoformat(),
        "output": str(output), "verifierSha256": VERIFIER_SHA256, "reusedIndependentVerifierSha256": OLD_VERIFIER_SHA256,
        "registrationSha256": REGISTRATION_SHA256, "certified": False, "targetAchieved": False,
        "scope": ["All fixed source-panel rows classified independently; all evaluable six predicates, raw candidates, lexicographic ranks, original same-day base-pool controls, exact pre-active signal order and complete unique dispositions.",
            "Fresh per-group active replay across the full interval without year reset. Observed entry/terminal fee/slippage arithmetic, T+1 timing, raw adverse paths excluding post-target-opening lows, seven horizon denominators and missing-data bounds use the frozen independent outcome audit.",
            "Exact new execution closure/ZIP, original configuration except dates, complete source-materialization chain and separately bound feature mathematical and supplemental closure audits; all source/output hashes rechecked after replay."],
        "limitations": ["Shares the original source loader and trusts the independently audited feature panel; does not recompute all cross-sectional ranks or all base-eligibility decisions from raw data.",
            "Partial/unevaluable predicate truth values and exact rejection reason priority are not independently reconstructed in full; five feature values, evaluable decisions and complete accounting are checked.",
            "Does not independently prove whether each entry should fill, the maximal affordable/participation-limited quantity, the first eligible sell opening or omission of an earlier target. Fee/path/lifecycle arithmetic is not a complete execution-engine replica or actual auction fill certification.",
            "Source prefix byte/SQLite correctness and original quality scope are inherited from the hash-bound source audit, not rederived here; old-prefix numeric quality is not extended.",
            "Raw prices lack complete corporate-action and historical-membership certification. This fixed survivor sample and previously exposed supplemental period are not a pristine holdout, whole-market evidence, market influence or future net30 probability certification.",
            "Synthetic success is not an actual-market verification."]}
    try:
        audit.pin(VERIFIER_SOURCE_PATH, VERIFIER_SHA256, "verifier-source-at-start")
        audit.pin(root / OLD_VERIFIER, OLD_VERIFIER_SHA256, "frozen-independent-functions")
        prepared = prepare(output, panel, root, audit)
        report.update(protocolHash=prepared["protocolHash"], sourceFingerprint=prepared["dataset"]["fingerprint"],
            implementation=prepared["protocol"]["implementation"], panelBinding=prepared["panelIdentity"],
            sourceMaterializationProtocolHash=prepared["document"]["sourceMaterialization"]["protocolHash"],
            resultFileHashes={item["path"]: item["sha256"] for item in prepared["receipt"]["files"].values()},
            completionSha256=sha(prepared["attempt"] / "completion.json"))
        report.update(replay(prepared, audit))
        audit.require(inventory(output) == prepared["outputInventory"], "output-inventory-unchanged")
        audit.require(inventory(prepared["panel"]) == PANEL_FILES | {"completion.json", "independent-verification.json"}, "panel-inventory-unchanged")
        audit.require(inventory(prepared["source"]["directory"]) == set(prepared["document"]["sourceMaterialization"]["files"]), "source-inventory-unchanged")
    except Exception as error:
        audit.check(False, "supplemental-independent-replay-exception", errorType=type(error).__name__, message=str(error))
    for path, expected in audit.bound.items():
        try:
            audit.check(sha(path) == expected, "bound-file-unchanged", path=str(path))
        except OSError as error:
            audit.check(False, "bound-file-unavailable", path=str(path), message=str(error))
    report.update(passed=audit.failures == 0, checks=audit.checks, failureCount=audit.failures, issues=audit.issues,
                  sourceHashes={str(p): h for p, h in audit.bound.items()}, elapsedSeconds=time.perf_counter()-started)
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
