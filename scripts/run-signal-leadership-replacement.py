"""One frozen leadership-replacement development diagnostic; freeze before run.

The legacy repair module supplies immutable input binding only. Every candidate
and control is independently formed from all audited role/feature rows. The
separate baseline event runtime retains its daily opening proxies and costs;
neither data lineage nor this replay certifies actual economic execution.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.signal_leadership_replacement import CANDIDATE_ID, VERSION, evaluate_replacement_day
from service.signal_path_risk import PathRiskStudy, summarize_paths


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


legacy = _module("_leadership_replacement_input", "run-signal-market-repair.py")
runtime = _module("_leadership_replacement_events", "run-signal-target-study.py")
source_tools = runtime.source_tools
sha, digest = legacy.sha, legacy.digest
read_json, write_json, same, require = legacy.read_json, legacy.write_json, legacy.same, legacy.require
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
runtime.STRATEGIES, runtime.GROUPS = (CANDIDATE_ID,), GROUPS
REGISTRATION = ROOT / "docs/signal-leadership-replacement-v1.json"
REGISTRATION_SHA256 = "d4cfafdcb56f4f8f606da39bf03260dd8a51fec550c2daaa19a31fe0291f76bd"
CLARIFICATION = ROOT / "artifacts/strategy-search/leadership-replacement-registration-clarification-v1.json"
CLARIFICATION_SHA256 = "a9dfdd92e288385cca324ba310c63f23cf2aa3217ba66b4b05b2ff82291862d5"
_LOADED_RUNNER_SHA = sha(Path(__file__))
_LOADED_RULE_SHA = sha(ROOT / "service/signal_leadership_replacement.py")
_LOADED_INPUT_SHA = sha(ROOT / "scripts/run-signal-market-repair.py")
_LOADED_EVENT_SHA = sha(ROOT / "scripts/run-signal-target-study.py")
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
CRITERIA = ("meanStrengthPercentile20", "minimumStrengthPercentile20", "relativeReturn60Percentile",
            "closePathMaxDrawdown20", "signedPathEfficiency20")
ATTEMPT_FILES = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz",
    "signals": "signals.jsonl.gz", "selections": "selection.jsonl.gz", "predicates": "predicates.jsonl.gz",
    "dailyGates": "daily-gates.jsonl.gz", "result": "result.json.gz", "summary": "summary.json", "attempt": "attempt.json"}
RECEIPT_KEYS = {*ATTEMPT_FILES, "executionSources", "protocol"}
AUDIT_REFERENCES = {
    "scripts/verify-signal-market-repair-result.py": "922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf",
    "scripts/verify-signal-persistent-leader-result.py": "3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _local_path(name):
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Missing or escaping source: " + str(name))
    return path


def _pin(record):
    require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "Incomplete source pin")
    path = _local_path(record["path"])
    require(sha(path) == record["sha256"], "Source pin changed: " + record["path"])
    return path


def clarification(doc):
    require(CLARIFICATION.resolve().is_relative_to(ROOT.resolve()) and sha(CLARIFICATION) == CLARIFICATION_SHA256,
            "Interpretive clarification changed")
    value = read_json(CLARIFICATION)
    require(value.get("schema") == "leadership-replacement-registration-clarification-v1" and
            value.get("candidateId") == CANDIDATE_ID and value.get("newRegistration") == {
                "path": REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix(), "sha256": REGISTRATION_SHA256},
            "Clarification registration identity differs")
    for key in ("inputBindingRegistration", "proposal", "pureRule", "prototypeReview"):
        require(same(value.get(key), doc[key]), "Clarification source reference differs: " + key)
    preserved = value.get("preservedSourceSections", {})
    require(preserved.get("sourceRegistration") == doc["inputBindingRegistration"] and
            preserved.get("newRegistrationWholeFileSha256BeforeAndAfter") == REGISTRATION_SHA256 and
            value.get("certified") is False and value.get("targetAchieved") is False, "Clarification closure differs")
    return {"path": CLARIFICATION.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": CLARIFICATION_SHA256, "document": value}


def registration():
    require(REGISTRATION_SHA256 is not None and sha(REGISTRATION) == REGISTRATION_SHA256, "Registration not frozen or changed")
    doc = read_json(REGISTRATION)
    require(doc.get("candidateId") == CANDIDATE_ID and doc.get("registeredAt") and
            doc.get("status") == "registered-rule-awaiting-reviewed-runner", "Rule is not registered")
    proposal = read_json(_pin(doc["proposal"]))
    for key in ("singleRule", "controlSampling", "eventExecution"):
        require(same(doc[key], proposal[key]), "Proposed single rule changed: " + key)
    require(_pin(doc["pureRule"]) == (ROOT / "service/signal_leadership_replacement.py").resolve() and
            doc["pureRule"]["sha256"] == _LOADED_RULE_SHA, "Imported pure rule differs")
    for name, expected in {**doc["sourceImplementation"], **doc["inputBindingImplementation"]}.items():
        require(sha(_local_path(name)) == expected, "Frozen inherited method changed: " + name)
    require(doc["inputBindingImplementation"].get("scripts/run-signal-market-repair.py") == _LOADED_INPUT_SHA and
            doc["sourceImplementation"].get("scripts/run-signal-target-study.py") == _LOADED_EVENT_SHA,
            "Imported input/event implementation differs")
    original = read_json(_pin(doc["inputBindingRegistration"]))
    for key in ("sample", "frozenExecutionConfig", "eventPolicy", "auditAndEvaluation"):
        require(same(doc[key], original[key]), "Source or execution policy changed: " + key)
    require(same(doc["sourceImplementation"], original["parent"]["sourceImplementation"]), "Inherited dependency closure changed")
    require(doc["independentVerifier"] == {"path": "scripts/verify-signal-leadership-replacement-result.py"}, "Verifier path differs")
    require(doc["implementationFreeze"]["tests"] == ["tests/test_signal_leadership_replacement.py",
            "tests/test_signal_leadership_replacement_runner.py", "tests/test_signal_leadership_replacement_verifier.py"],
            "Test archive population differs")
    for key in ("prototypeReview", "registrationGenerator"):
        _pin(doc[key])
    clarification(doc)
    return doc


def _configure(doc):
    legacy.ROOT = ROOT
    legacy.REGISTRATION = _local_path(doc["inputBindingRegistration"]["path"])
    legacy.REGISTRATION_SHA256 = doc["inputBindingRegistration"]["sha256"]


def load_binding():
    """Hash/metadata-only binding; never iterate original candidate/event files."""
    doc = registration()
    _configure(doc)
    original = legacy.load_binding()
    explanation = clarification(doc)
    files = dict(original["files"])
    files[str(REGISTRATION.resolve())] = REGISTRATION_SHA256
    files[str(CLARIFICATION.resolve())] = CLARIFICATION_SHA256
    for record in (doc["pureRule"], doc["proposal"], doc["inputBindingRegistration"], doc["prototypeReview"], doc["registrationGenerator"]):
        files[str(_pin(record))] = record["sha256"]
    identity = {"registrationSha256": REGISTRATION_SHA256,
        "inputRegistrationSha256": doc["inputBindingRegistration"]["sha256"], "inputFilesSha256": files,
        "sourceBinding": original["identity"], "snapshotFingerprint": original["identity"]["snapshotFingerprint"],
        "calendarHash": original["identity"]["calendarHash"], "marketProtocolHash": original["identity"]["marketProtocolHash"],
        "clarification": {key: explanation[key] for key in ("path", "sha256")}}
    return {**original, "document": doc, "inputBinding": original, "files": files, "identity": identity, "clarification": explanation}


def iter_market_days(binding):
    yield from legacy.iter_market_days(binding["inputBinding"])


def iter_role_days(binding):
    yield from legacy.iter_role_days(binding["inputBinding"])


def iter_feature_days(binding):
    stream = legacy.iter_records(binding["rawPaths"]["features.jsonl.gz"])
    original = binding["inputBinding"]["document"]["originalRawSourcePanelBinding"]
    inputs = binding["manifest"]["inputs"]
    for day in binding["dates"]:
        rows = []
        for symbol in binding["symbols"]:
            row = next(stream, None)
            require(isinstance(row, dict) and row.get("asOfDate") == day and row.get("symbol") == symbol and
                    row.get("featureVersion") == "daily-leadership-raw-v1", "Feature population/date/version differs")
            require(row.get("sampleManifestHash") == original["files"]["manifest.json"]["sha256"] and
                    row.get("calendarHash") == original["calendarHash"] and same(row.get("sourceHashes"),
                    {**inputs["securities"][symbol], "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}),
                    "Feature source identity differs")
            rows.append(row)
        yield day, rows
    legacy._end(stream, "Feature stream")


def criterion_values(feature):
    """Match the frozen Q producer's value projection without recomputing Q."""
    result = {}
    for field in CRITERIA:
        value = feature.get(field)
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and (-1 if field == "signedPathEfficiency20" else 0) <= value <= 1
        except OverflowError:
            valid = False
        result[field] = value if valid else None
    return result


def collect_signals(binding, attempt):
    """Read every row and EOF before the caller may evaluate any event."""
    history, window_refs = deque(maxlen=3), deque(maxlen=3)
    markets, roles, features = iter_market_days(binding), iter_role_days(binding), iter_feature_days(binding)
    original = binding["inputBinding"]["document"]["originalRawSourcePanelBinding"]
    file_source = {"roleFileSha256": sha(binding["marketPaths"]["roles.jsonl.gz"]),
        "featureFileSha256": original["files"]["features.jsonl.gz"]["sha256"],
        "marketProtocolHash": binding["identity"]["marketProtocolHash"],
        "panelManifestSha256": original["files"]["manifest.json"]["sha256"], "calendarHash": binding["identity"]["calendarHash"]}
    signals, counts, market_counts, cohort_counts = [], Counter(), Counter(), Counter()
    raw_count = role_count = top_count = control_count = 0
    started = notice = time.perf_counter()
    with ExitStack() as stack:
        writers = {key: stack.enter_context(gzip.open(attempt / ATTEMPT_FILES[key], "xt", encoding="utf-8", compresslevel=1))
                   for key in ("candidates", "coverage", "signals", "selections", "predicates", "dailyGates")}
        for index, day in enumerate(binding["dates"]):
            market, role_item, feature_item = next(markets, None), next(roles, None), next(features, None)
            require(market is not None and role_item is not None and feature_item is not None and
                    market["date"] == role_item[0] == feature_item[0] == day, "Aligned daily source stream missing")
            current, feature_rows = role_item[1], feature_item[1]
            market_row_hash = digest(market)
            row_sources = {}
            for role, feature in zip(current, feature_rows):
                require(same(role.get("criterionValues"), criterion_values(feature)) and
                        same(role.get("baseEligibility"), feature.get("baseEligibility")) and
                        same(role.get("sourceHashes"), feature.get("sourceHashes")), "Role/feature criteria or qualification differs")
                row_sources[role["symbol"]] = {**file_source, "marketRowSha256": market_row_hash,
                                               "roleRowSha256": digest(role), "featureRowSha256": digest(feature)}
            history.append(current)
            window_refs.append({"date": day, "sha256": digest(current)})
            outcome = evaluate_replacement_day(binding["symbols"], binding["dates"], index, list(history), market)
            daily_source = {**file_source, "marketRowSha256": market_row_hash, "roleWindow": list(window_refs)}
            daily = outcome["daily"]
            runtime.write_record(writers["dailyGates"], {"date": day, "targetIndex": index, "version": VERSION,
                "candidateId": CANDIDATE_ID, **daily, "source": daily_source, "certified": False, "targetAchieved": False})
            for row in outcome["evaluations"]:
                runtime.write_record(writers["predicates"], {**row, "source": row_sources[row["symbol"]]})
            for row in outcome["rankedCandidates"]:
                runtime.write_record(writers["candidates"], {**row, "source": row_sources[row["symbol"]]})
            for row in outcome["signals"]:
                signal = {**row, "source": row_sources[row["symbol"]]}
                runtime.write_record(writers["signals"], signal)
                signals.append(signal)
            runtime.write_record(writers["selections"], {**outcome["selection"], "source": daily_source})
            selected = len(outcome["selection"]["selectedTop5Symbols"])
            controls = outcome["selection"]["controlMatching"]["matchedCount"]
            cover = {"date": day, "evaluatedSecurities": len(current), "candidateGateCounts": daily["candidateGateCounts"],
                "rawCandidateCount": len(outcome["rankedCandidates"]), "selectedStrategyCount": selected,
                "controlSignalCount": controls, "controlMatching": outcome["selection"]["controlMatching"],
                "sourceRoleRows": len(current), "sourceFeatureRows": len(feature_rows), "source": daily_source}
            runtime.write_record(writers["coverage"], cover)
            counts.update(daily["candidateGateCounts"])
            market_counts[daily["market"]["state"]] += 1
            cohort_counts[daily["oldCohort"]["state"]] += 1
            role_count += len(current)
            raw_count += len(outcome["rankedCandidates"])
            top_count += selected
            control_count += controls
            if time.perf_counter()-notice >= 30:
                print(f"Leadership replacement: {index+1}/{len(binding['dates'])} sessions; {role_count} role rows", flush=True)
                notice = time.perf_counter()
        for stream, label in ((markets, "Market"), (roles, "Roles"), (features, "Features")):
            legacy._end(stream, label)
    require(role_count == binding["document"]["sample"]["expectedFeatureRows"] and sum(counts.values()) == role_count,
            "All-stock coverage accounting differs")
    return signals, {"scoringSeconds": time.perf_counter()-started, "scoredSessions": len(binding["dates"]),
        "sourceRoleRows": role_count, "sourceFeatureRows": role_count, "candidateGateCounts": dict(counts),
        "marketGateCounts": dict(market_counts), "oldCohortGateCounts": dict(cohort_counts),
        "rawCandidateCount": raw_count, "top5SignalCount": top_count, "controlSignalCount": control_count}


def evaluate_signals(dataset, config, signals):
    result = runtime.evaluate_signals(dataset, deepcopy(config), signals)
    study = PathRiskStudy(dataset, config)
    paths = [study.evaluate(event) for event in result["events"] if event["entry"]["status"] == "filled"]
    result.update(pathRiskEvents=paths, pathRiskSummary=summarize_paths(paths, horizons=HORIZONS),
                  actionEvidence="unverified", historicalEligibilityVerified=False, comparableRightsVerified=False,
                  resultMeaning="registered-cost/raw-price-leadership-replacement-development-diagnostic;not-actual-historical-fee-or-execution-certification")
    for group in result["groups"]:
        selected = [r for r in signals if r["strategyId"] == group["strategyId"]]
        group.update(role="control" if group["strategyId"].startswith("control_") else "leadership-replacement-diagnostic",
                     distinctSignalSymbols=len({r["symbol"] for r in selected}),
                     distinctSignalDates=len({r["signalDate"] for r in selected}), actionEvidence="unverified")
    return result


def implementation_hashes(binding):
    require(sha(Path(__file__)) == _LOADED_RUNNER_SHA and sha(ROOT / "service/signal_leadership_replacement.py") == _LOADED_RULE_SHA,
            "New source changed after import")
    doc = binding["document"]
    names = set(doc["sourceImplementation"]) | set(doc["inputBindingImplementation"])
    for name, expected in AUDIT_REFERENCES.items():
        require(sha(_local_path(name)) == expected, "Frozen independent audit dependency changed")
    names.update(AUDIT_REFERENCES)
    names.update({Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(),
                  REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix(), doc["pureRule"]["path"],
                  doc["proposal"]["path"], doc["inputBindingRegistration"]["path"], doc["independentVerifier"]["path"],
                  doc["prototypeReview"]["path"], doc["registrationGenerator"]["path"],
                  binding["clarification"]["path"], *doc["implementationFreeze"]["tests"]})
    return {name: sha(_local_path(name)) for name in sorted(names)}


def _safe_args(args, binding):
    _configure(binding["document"])
    workspace, output = legacy._safe_args(args, binding["inputBinding"])
    require(all(not _local_path(name).is_relative_to(output) for name in implementation_hashes(binding)), "Output overlaps execution sources")
    return workspace, output


def _archive(path, implementation):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, expected in implementation.items():
            raw = _local_path(name).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == expected, "Source drift during archive")
            archive.writestr(name, raw)


def _inventory(output, completed=False):
    allowed = {".run.lock", "freeze-attempt.json", "protocol.json", "execution-sources.zip", "events.jsonl"}
    if completed:
        allowed.add("attempts")
    for path in output.iterdir():
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Output links forbidden")
        report = completed and path.is_file() and (path.name == "independent-result-verification.json" or
                  path.name.startswith("independent-result-verification-failed-") and path.suffix == ".json")
        require(path.name in allowed or report, "Unexpected output or failed attempt; restart forbidden")
    if completed:
        attempts, attempt = output / "attempts", output / "attempts/attempt-0001"
        require(sorted(p.name for p in attempts.iterdir()) == ["attempt-0001"] and
                {p.name for p in attempt.iterdir()} == set(ATTEMPT_FILES.values()) | {"completion.json"}, "Completed inventory differs")
        for path in (attempts, attempt, *attempt.iterdir()):
            require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Attempt links forbidden")


def _verify_protocol(output, binding, workspace):
    protocol = read_json(output / "protocol.json")
    require(protocol.get("schema") == "leadership-replacement-development-v1" and
            protocol.get("protocolHash") == digest({k: v for k, v in protocol.items() if k != "protocolHash"}), "Protocol schema/hash differs")
    require(same(protocol.get("registration"), binding["document"]) and protocol.get("registrationSha256") == REGISTRATION_SHA256 and
            same(protocol.get("binding"), binding["identity"]) and same(protocol.get("config"), binding["document"]["frozenExecutionConfig"]) and
            protocol.get("workspace") == str(workspace) and protocol.get("strategyOrder") == [CANDIDATE_ID] and
            protocol.get("groups") == list(GROUPS) and protocol.get("certified") is False and protocol.get("targetAchieved") is False,
            "Protocol binding/config differs")
    require(same(protocol.get("clarification"), binding["clarification"]), "Protocol clarification differs")
    require(protocol["implementation"] == implementation_hashes(binding) and
            sha(output / "execution-sources.zip") == protocol["executionArchiveSha256"], "Execution source/archive drift")
    legacy._check_archive(output / "execution-sources.zip", protocol["implementation"])
    marker = read_json(output / "freeze-attempt.json")
    require(sha(output / "freeze-attempt.json") == protocol["freezeAttemptSha256"] and
            marker.get("registrationSha256") == REGISTRATION_SHA256 and marker.get("candidateId") == CANDIDATE_ID,
            "Freeze marker differs")
    return protocol


def _fail(output, phase, error):
    write_json(output / "failure.json", {"phase": phase, "errorType": type(error).__name__, "error": str(error),
                                         "failedAt": _now(), "automaticRetryAllowed": False})


def freeze(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    output.mkdir(parents=True, exist_ok=True)
    with source_tools.output_lock(output):
        require({p.name for p in output.iterdir()} == {".run.lock"}, "Exclusive freeze requires a new empty output")
        write_json(output / "freeze-attempt.json", {"candidateId": CANDIDATE_ID, "registrationSha256": REGISTRATION_SHA256,
                                                   "startedAt": _now(), "certified": False, "targetAchieved": False})
        try:
            implementation = implementation_hashes(binding)
            _archive(output / "execution-sources.zip", implementation)
            protocol = {"schema": "leadership-replacement-development-v1", "createdAt": _now(),
                "registration": binding["document"], "registrationSha256": REGISTRATION_SHA256, "binding": binding["identity"],
                "clarification": binding["clarification"],
                "workspace": str(workspace), "config": deepcopy(binding["document"]["frozenExecutionConfig"]),
                "strategyOrder": [CANDIDATE_ID], "groups": list(GROUPS), "implementation": implementation,
                "pythonVersion": sys.version, "certified": False, "targetAchieved": False,
                "executionArchiveSha256": sha(output / "execution-sources.zip"), "freezeAttemptSha256": sha(output / "freeze-attempt.json")}
            protocol["protocolHash"] = digest(protocol)
            require(same(load_binding()["identity"], binding["identity"]) and implementation_hashes(binding) == implementation,
                    "Input/code drift during freeze")
            write_json(output / "protocol.json", protocol)
            return protocol
        except Exception as error:
            _fail(output, "freeze", error)
            raise


def load_dataset(args, binding):
    return legacy.load_dataset(args, binding["inputBinding"])


def validate_dataset(dataset, binding):
    legacy.validate_dataset(dataset, binding["inputBinding"])
    require(all(b["date"] <= binding["document"]["sample"]["signalEnd"] for b in dataset["benchmark"]["bars"]),
            "Post-cutoff benchmark bar")


def verify_completion(output, receipt, protocol):
    _inventory(output, completed=True)
    require(receipt.get("protocolHash") == protocol["protocolHash"] and receipt.get("attempt") == 1 and
            receipt.get("snapshotFingerprint") == protocol["binding"]["snapshotFingerprint"] and
            receipt.get("certified") is False and receipt.get("targetAchieved") is False and
            receipt.get("freezeAttemptSha256") == protocol["freezeAttemptSha256"] and set(receipt.get("files", {})) == RECEIPT_KEYS,
            "Completed receipt identity differs")
    expected = {k: "attempts/attempt-0001/"+name for k, name in ATTEMPT_FILES.items()}
    expected.update(executionSources="execution-sources.zip", protocol="protocol.json")
    for key, name in expected.items():
        require(receipt["files"][key] == {"path": name, "sha256": sha(output / name)}, "Completed artifact changed; no recompute")
    marker = read_json(output / "attempts/attempt-0001/attempt.json")
    require(marker.get("attempt") == 1 and marker.get("protocolHash") == protocol["protocolHash"] and
            marker.get("candidateId") == CANDIDATE_ID, "Attempt marker changed")
    journal = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    require([r.get("event") for r in journal] == ["study_started", "scoring_completed", "study_succeeded"] and
            all(r.get("protocolHash") == protocol["protocolHash"] for r in journal) and journal[-1].get("receipt") == receipt,
            "Completed journal differs")


def execute(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    require((output / "protocol.json").is_file(), "Freeze and review before execution")
    with source_tools.output_lock(output):
        protocol = _verify_protocol(output, binding, workspace)
        protocol_sha = sha(output / "protocol.json")
        attempt = output / "attempts/attempt-0001"
        receipt_path = attempt / "completion.json"
        if receipt_path.exists():
            verify_completion(output, read_json(receipt_path), protocol)
            print("Completed leadership-replacement diagnostic verified; no recomputation", flush=True)
            return 0
        _inventory(output)
        require(not (output / "attempts").exists(), "Failed/interrupted attempt retained; no automatic retry")
        attempt.mkdir(parents=True)
        write_json(attempt / "attempt.json", {"attempt": 1, "candidateId": CANDIDATE_ID, "protocolHash": protocol["protocolHash"],
                                            "startedAt": _now(), "certified": False, "targetAchieved": False})
        source_tools.event(output, protocol["protocolHash"], "study_started", attempt=1)
        try:
            dataset = load_dataset(args, binding)
            validate_dataset(dataset, binding)
            signals, performance = collect_signals(binding, attempt)
            source_tools.event(output, protocol["protocolHash"], "scoring_completed", **performance)
            tick = time.perf_counter()
            result = evaluate_signals(dataset, protocol["config"], signals)
            performance["eventEvaluationSeconds"] = time.perf_counter()-tick
            for group in result["groups"]:
                require(group["rawSelectedSignals"] == group["signalCount"]+group["overlapSuppressedCount"], "Active disposition count differs")
            result.update(protocolHash=protocol["protocolHash"], snapshotFingerprint=dataset["fingerprint"],
                strategyOrder=[CANDIDATE_ID], stage="development-diagnostic", pilot=False, diagnosticUnverifiedHistory=True,
                candidateGateCounts=performance["candidateGateCounts"], marketGateCounts=performance["marketGateCounts"],
                oldCohortGateCounts=performance["oldCohortGateCounts"], sourceRoleRows=performance["sourceRoleRows"],
                sourceFeatureRows=performance["sourceFeatureRows"], certified=False, targetAchieved=False)
            with gzip.open(attempt / "result.json.gz", "xt", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            summary = {k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")}
            summary.update(performance=performance, ranking=None, selectionEligible=False)
            write_json(attempt / "summary.json", summary)
            final = load_binding()
            require(same(final["identity"], binding["identity"]) and sha(output / "protocol.json") == protocol_sha, "Post-execution input/protocol drift")
            _verify_protocol(output, final, workspace)
            validate_dataset(dataset, final)
            paths = {k: "attempts/attempt-0001/"+name for k, name in ATTEMPT_FILES.items()}
            paths.update(executionSources="execution-sources.zip", protocol="protocol.json")
            receipt = {"protocolHash": protocol["protocolHash"], "snapshotFingerprint": dataset["fingerprint"], "attempt": 1,
                       "freezeAttemptSha256": protocol["freezeAttemptSha256"], "certified": False, "targetAchieved": False,
                       "files": {k: {"path": name, "sha256": sha(output/name)} for k, name in paths.items()}}
            write_json(receipt_path, receipt)
            source_tools.event(output, protocol["protocolHash"], "study_succeeded", receipt=receipt)
            return 0
        except Exception as error:
            _fail(output, "execute", error)
            write_json(attempt / "failure.json", {"protocolHash": protocol["protocolHash"], "errorType": type(error).__name__,
                "error": str(error), "failedAt": _now(), "automaticRetryAllowed": False})
            source_tools.event(output, protocol["protocolHash"], "study_failed", errorType=type(error).__name__, error=str(error))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT / "artifacts/strategy-search/source-workspace")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/strategy-search/signal-leadership-replacement-v1")
    parser.add_argument("--diagnostic-unverified-history", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    if args.freeze_only:
        print(json.dumps({"protocolHash": freeze(args)["protocolHash"]}), flush=True)
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
