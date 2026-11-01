#!/usr/bin/env python3
"""Independent replacement-rule reconstruction and frozen outcome arithmetic.

No production rule, trial runner or event evaluator is imported. Q and market
features are hash-bound audited inputs, not recomputed price features here.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-leadership-replacement-v1.json"
REGISTRATION_SHA256 = "d4cfafdcb56f4f8f606da39bf03260dd8a51fec550c2daaa19a31fe0291f76bd"
CLARIFICATION = ROOT / "artifacts/strategy-search/leadership-replacement-registration-clarification-v1.json"
CLARIFICATION_SHA256 = "a9dfdd92e288385cca324ba310c63f23cf2aa3217ba66b4b05b2ff82291862d5"
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/signal-leadership-replacement-v1"
CANDIDATE_ID = "rawLeadershipReplacementDiagnostic"
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
CONTROL_SEED = "a-trade-signal-same-day-base-pool-control-v1"
FEATURE_VERSION = "market-state-observations-v1"
Q_DEFINITION = "persistent-price-strength-five-gates-v1"
STATES = ("true", "false", "unknown")
RANK_FIELDS = ("meanStrengthPercentile20", "relativeReturn60Percentile")
REFERENCE_PATH = "scripts/verify-signal-market-repair-result.py"
REFERENCE_SHA256 = "922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf"
NEW_VERIFIER = "scripts/verify-signal-leadership-replacement-result.py"
NEW_RUNNER = "scripts/run-signal-leadership-replacement.py"
NEW_RULE = "service/signal_leadership_replacement.py"
NEW_TESTS = "tests/test_signal_leadership_replacement_verifier.py"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_PATH = Path(__file__).resolve()
VERIFIER_SHA256 = sha(VERIFIER_PATH)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("Duplicate JSON key: " + key)
        value[key] = item
    return value


def parse(value):
    def reject(token):
        raise ValueError("Nonfinite JSON constant: " + token)
    return json.loads(value, object_pairs_hook=pairs, parse_constant=reject)


def read(path):
    return parse(Path(path).read_bytes())


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = parse(line)
            if not isinstance(row, dict):
                raise ValueError("JSONL row is not an object")
            yield row


def bounded(value, low=0, high=1):
    try:
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        return False


def qualification(row):
    value = row.get("baseEligibility")
    if (not isinstance(value, dict) or type(value.get("eligible")) is not bool or
        not isinstance(value.get("reason"), str) or not value["reason"] or
        not isinstance(value.get("unknown"), list) or any(not isinstance(s, str) for s in value["unknown"])):
        return {"state": "unknown", "reason": "baseEligibilityUnavailable"}
    reason = value["reason"]
    state = "unknown" if reason in {"invalidHistory", "missingDailyBar", "notObservedYet", "strictHistoryMissing"} else str(value["eligible"]).lower()
    return {"state": state, "reason": reason}


def reference_day(symbols, dates, index, roles, market_row):
    """Rebuild all proposal values independently from three exact role slices."""
    day = dates[index]
    if len(roles) < 3:
        cohort = {"state": "unknown", "anchorDate": None, "members": None, "memberHash": None,
            "denominator": None, "threshold": None, "anchorQCounts": None, "endpoints": [],
            "unknownReasons": ["firstTwoRoleSessionsUnavailable"], "failedPredicates": []}
    else:
        members = [symbol for symbol, row in zip(symbols, roles[0]) if row["qState"] == "true"]
        positions = [i for i, row in enumerate(roles[0]) if row["qState"] == "true"]
        n, threshold = len(members), (len(members)+1)//2
        endpoints = []
        for cross in roles[1:]:
            states = {symbols[i]: cross[i]["qState"] for i in positions}
            counts = {state: list(states.values()).count(state) for state in STATES}
            lower, upper = counts["false"], counts["false"]+counts["unknown"]
            gate = "false" if n == 0 or upper < threshold else "true" if lower >= threshold else "unknown"
            endpoints.append({"date": cross[0]["date"], "state": gate, "counts": counts, "memberStates": states,
                "failureFractionLower": lower/n if n else None, "failureFractionUpper": upper/n if n else None})
        endpoint_states = {e["state"] for e in endpoints}
        cohort = {"state": "unknown" if "unknown" in endpoint_states else "false" if "false" in endpoint_states else "true",
            "anchorDate": roles[0][0]["date"], "members": members, "memberHash": digest(members),
            "denominator": n, "threshold": threshold,
            "anchorQCounts": {state: sum(r["qState"] == state for r in roles[0]) for state in STATES},
            "endpoints": endpoints,
            "unknownReasons": ["oldCohortThresholdUnresolved:"+e["date"] for e in endpoints if e["state"] == "unknown"],
            "failedPredicates": ["emptyObservedOldCohort"] if n == 0 else ["oldCohortDeterioration:"+e["date"] for e in endpoints if e["state"] == "false"]}
    predicates = {}
    for name, threshold, domain in (("price20.coverage", .9, (0, 1)),
            ("matched5.pairedCoverageFixed", .9, (0, 1)), ("matched5.matchedBreadthChange5", 0, (-100, 100))):
        group, field = name.split(".")
        value = market_row.get(group)
        value = value.get(field) if isinstance(value, dict) else None
        valid = bounded(value, *domain)
        predicates[name] = {"value": value if valid else None, "operator": ">=", "threshold": threshold,
            "passed": value >= threshold if valid else None,
            "unknownReasons": [] if valid else ["requiredMarketValueUnavailable:"+name]}
    matched = market_row["matched5"]
    available = matched.get("available")
    predicates["matched5.available"] = {"value": available if type(available) is bool else None,
        "operator": "is", "threshold": True, "passed": available if type(available) is bool else None,
        "unknownReasons": [] if available is True else ["matchedComparisonUnavailable"]}
    unknown = {reason for p in predicates.values() for reason in p["unknownReasons"]}
    if index < 5:
        unknown.add("firstFiveTargetSessionsUnavailable")
    failed = [name for name, p in predicates.items() if p["passed"] is False]
    market = {"state": "unknown" if unknown else "false" if failed else "true", "predicates": predicates,
        "earlierDate": dates[index-5] if index >= 5 else None, "unknownReasons": sorted(unknown),
        "failedPredicates": failed, "breadthChangeUnit": "percentage-points"}
    evaluations, candidates = [], []
    for i, symbol in enumerate(symbols):
        history = [{"date": cross[i]["date"], "qState": cross[i]["qState"]} for cross in roles]
        missing = ["requiredQStateUnavailable:"+r["date"] for r in history if r["qState"] == "unknown"]
        if len(history) < 3:
            missing.append("firstTwoRoleSessionsUnavailable")
        expected = ["false", "true", "true"][-len(history):]
        bad = ["challengerQ:"+r["date"] for r, goal in zip(history, expected) if r["qState"] not in ("unknown", goal)]
        challenger = "unknown" if missing else "false" if bad else "true"
        unknown = cohort["unknownReasons"] + market["unknownReasons"] + missing
        failed = cohort["failedPredicates"] + market["failedPredicates"] + bad
        current = roles[-1][i]
        eligible = qualification(current)
        if eligible["state"] == "unknown":
            unknown.append("entryQualification:"+eligible["reason"])
        if eligible["state"] == "false":
            failed.append("entryQualification:"+eligible["reason"])
        values = current.get("criterionValues")
        values = values if isinstance(values, dict) else {}
        ranking = {field: values.get(field) if bounded(values.get(field)) else None for field in RANK_FIELDS}
        unknown += ["rankingEvidenceUnavailable:"+field for field, value in ranking.items() if value is None]
        state = "unknown" if unknown else "false" if failed else "true"
        evaluations.append({"date": day, "symbol": symbol, "candidateGate": state, "oldCohortGate": cohort["state"],
            "marketGate": market["state"], "challengerGate": challenger, "challengerHistory": history,
            "qualification": eligible, "baseEligibility": deepcopy(current.get("baseEligibility")), "rankingValues": ranking,
            "unknownReasons": sorted(set(unknown)), "failedPredicates": sorted(set(failed)), "certified": False, "targetAchieved": False})
        if state == "true":
            candidates.append({"strategyId": CANDIDATE_ID, "symbol": symbol, "signalDate": day, **ranking,
                "certified": False, "targetAchieved": False, "actionEvidence": "unverified"})
    candidates.sort(key=lambda row: (-row[RANK_FIELDS[0]], -row[RANK_FIELDS[1]], row["symbol"]))
    for rank, row in enumerate(candidates, 1):
        row.update(rank=rank, selectedTop5=rank <= 5, score=row[RANK_FIELDS[0]], rawScore=row[RANK_FIELDS[0]])
    top = candidates[:5]
    selected = {row["symbol"] for row in top}
    eligible = [symbol for symbol, row in zip(symbols, roles[-1]) if qualification(row)["state"] == "true"]
    pool = sorted(set(eligible)-selected)
    order = sorted(pool, key=lambda symbol: (hashlib.sha256(f"{CONTROL_SEED}:{day}:{CANDIDATE_ID}:{symbol}".encode()).hexdigest(), symbol))[:len(top)]
    controls = [{"strategyId": GROUPS[1], "pairedStrategyId": CANDIDATE_ID, "symbol": symbol,
        "signalDate": day, "rank": i+1, "pairedStrategyRank": i+1, "pairedStrategySymbol": top[i]["symbol"],
        "selectedTop5": False, "score": 0., "rawScore": 0., "certified": False,
        "targetAchieved": False, "actionEvidence": "unverified"} for i, symbol in enumerate(order)]
    matching = {"seed": CONTROL_SEED, "baseEligibleCount": len(eligible), "eligibleControlPoolSize": len(pool),
        "eligibleControlPoolHash": digest(pool), "requestedCount": len(top), "matchedCount": len(order),
        "shortfall": len(top)-len(order), "selectedSymbols": order}
    return {"version": "leadership-replacement-proposal-v1", "candidateId": CANDIDATE_ID, "date": day, "targetIndex": index,
        "daily": {"oldCohort": cohort, "market": market, "evaluatedSecurities": len(symbols),
            "candidateGateCounts": {state: sum(r["candidateGate"] == state for r in evaluations) for state in STATES}},
        "evaluations": evaluations, "rankedCandidates": candidates, "signals": deepcopy(top)+controls,
        "selection": {"date": day, "strategyId": CANDIDATE_ID, "rawCandidateCount": len(candidates),
            "fullRanking": deepcopy(candidates), "selectedTop5Symbols": [r["symbol"] for r in top],
            "controlMatching": matching, "activeExclusionApplied": False},
        "status": "proposal-only-unvalidated", "returnsCalculated": False, "certified": False, "targetAchieved": False}


def private_reference(root):
    path = root / REFERENCE_PATH
    if sha(path) != REFERENCE_SHA256:
        raise ValueError("Frozen independent reference changed")
    spec = importlib.util.spec_from_file_location("_replacement_independent_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(directory):
    result = set()
    for path in [directory, *directory.rglob("*")]:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Linked artifact tree")
        if path.is_file():
            result.add(path.relative_to(directory).as_posix())
    return result


def bind_inputs(reference, root, audit):
    audit.pin(Path(REGISTRATION), REGISTRATION_SHA256)
    document = read(REGISTRATION)
    audit.require(document["candidateId"] == CANDIDATE_ID and document["status"] == "registered-rule-awaiting-reviewed-runner" and
        document["stage"] == "development" and document["hypothesisCount"] == 1, "registered-one-replacement-rule")
    for item in reference.refs(document):
        audit.pin(reference.within(root, item["path"]), item["sha256"])
    proposal = read(reference.within(root, document["proposal"]["path"]))
    for name in ("singleRule", "controlSampling", "eventExecution"):
        audit.require(reference.exact(document[name], proposal[name]), "registered-proposal-rule:"+name)
    audit.pin(Path(CLARIFICATION), CLARIFICATION_SHA256)
    clarification = read(CLARIFICATION)
    clarification_ref = {"path": Path(CLARIFICATION).resolve().relative_to(root).as_posix(), "sha256": CLARIFICATION_SHA256}
    audit.require(clarification["schema"] == "leadership-replacement-registration-clarification-v1" and
        clarification["candidateId"] == CANDIDATE_ID and clarification["newRegistration"] == {
            "path": Path(REGISTRATION).resolve().relative_to(root).as_posix(), "sha256": REGISTRATION_SHA256} and
        all(reference.exact(clarification[key], document[key]) for key in ("inputBindingRegistration", "proposal", "pureRule", "prototypeReview")) and
        clarification["preservedSourceSections"]["newRegistrationWholeFileSha256BeforeAndAfter"] == REGISTRATION_SHA256 and
        reference.exact(clarification["preservedSourceSections"]["sourceRegistration"], document["inputBindingRegistration"]),
        "pre-execution-clarification-exact-registration-binding")
    reference.REGISTRATION = document["inputBindingRegistration"]["path"]
    reference.REGISTRATION_SHA256 = document["inputBindingRegistration"]["sha256"]
    # The frozen independent source binder checks only prior input receipts and
    # audits. It never iterates parent candidates/signals or recalculates trades.
    input_audit = reference.Audit()
    source, parent_protocol, market_protocol = reference.bind_sources(root, None, input_audit)
    for name in ("sample", "frozenExecutionConfig", "eventPolicy", "auditAndEvaluation"):
        audit.require(reference.exact(document[name], source[name]), "unchanged-input-execution-contract:"+name)
    audit.require(reference.exact(document["sourceImplementation"], source["parent"]["sourceImplementation"]) and
        document["executionConfigHash"] == digest(document["frozenExecutionConfig"]), "source-implementation-and-config-identity")
    for name, expected in document["inputBindingImplementation"].items():
        audit.pin(reference.within(root, name), expected)
    raw, market, sample = source["originalRawSourcePanelBinding"], source["marketInput"], document["sample"]
    raw_files = {name: reference.within(root, value["path"]) for name, value in raw["files"].items()}
    manifest, receipt, proof = [read(raw_files[name]) for name in ("manifest.json", "completion.json", "independent-verification.json")]
    inputs = manifest["inputs"]
    dates, symbols, calendar = raw["targetDates"], inputs["symbols"], inputs["calendar"]["dates"]
    audit.require(dates == sorted(set(dates)) and symbols == sorted(set(symbols)) and calendar == sorted(set(calendar)) and
        all(date.fromisoformat(day).isoformat() == day for day in calendar) and
        dates == [d for d in calendar if sample["signalStart"] <= d <= sample["signalEnd"]] and
        all(d <= sample["signalEnd"] for d in calendar) and len(dates) == sample["expectedScoredSessions"] and
        len(symbols) == inputs["symbolCount"] == sample["expectedFullSecurities"] and
        digest(symbols) == inputs["symbolsSha256"] == raw["symbolsSha256"] and
        digest(calendar) == inputs["calendar"]["sha256"] == raw["calendarHash"], "fixed-symbol-calendar-population")
    raw_hashes = {name: value["sha256"] for name, value in raw["files"].items()}
    audit.require(set(raw_files) == {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz", "summary.json", "execution-sources.zip", "attempt.json", "completion.json", "independent-verification.json"} and
        set(receipt["files"]) == set(raw_files)-{"completion.json", "independent-verification.json"}, "feature-completion-exact-set")
    for name, item in receipt["files"].items():
        audit.require(reference.exact(item, {"path": name, "sha256": raw_hashes[name]}), "feature-completion-file:"+name)
    audit.require(proof["passed"] is True and type(proof["failureCount"]) is int and proof["failureCount"] == 0 and
        proof["sampleManifestHash"] == receipt["sampleManifestHash"] == raw_hashes["manifest.json"] and
        proof["featureRows"] == receipt["featureRows"] == sample["expectedFeatureRows"] == len(dates)*len(symbols) and
        receipt["targetSessions"] == len(dates) and inputs["snapshotFingerprint"] == sample["expectedFullSnapshotFingerprint"] and
        inputs["retainedBarCount"] == sample["expectedFullBars"], "feature-audit-and-complete-population")
    audit.require(manifest["executionArchiveSha256"] == raw_hashes["execution-sources.zip"], "feature-whole-archive")
    reference.archive(audit, raw_files["execution-sources.zip"], manifest["implementation"], root)
    market_paths = {name: reference.within(root, item["path"]) for name, item in market["outputs"].items()}
    market_receipt = read(reference.within(root, market["completion"]["path"]))
    market_proof = read(reference.within(root, market["actualAudit"]["path"]))
    market_summary = read(market_paths["summary.json"])
    outputs = {"protocol.json", "execution-sources.zip", "attempt.json", "daily.jsonl.gz", "roles.jsonl.gz", "summary.json"}
    audit.require(set(market_paths) == set(market_receipt["files"]) == set(market_proof["resultFileHashes"]) == outputs and
        market_receipt["protocolHash"] == market["protocolHash"] and
        market_protocol["dates"] == dates and market_protocol["symbols"] == symbols and
        market_protocol["calendarHash"] == raw["calendarHash"] and
        reference.exact(market_protocol["implementation"], market["implementation"]) and
        reference.exact(market_protocol["inputFilesSha256"], raw_hashes) and
        market_protocol["inputManifestSha256"] == market_proof["inputManifestSha256"] == raw_hashes["manifest.json"] and
        market_receipt["dailyRows"] == len(dates) and market_receipt["roleRows"] == len(dates)*len(symbols), "market-exact-input-and-receipt")
    for name, item in market["outputs"].items():
        audit.require(reference.exact(market_receipt["files"][name], {"path": name, "sha256": item["sha256"]}) and
            market_proof["resultFileHashes"][name] == item["sha256"], "market-six-file-audit:"+name)
    # Actual frozen completion has two flags; certification flags belong to
    # protocol, summary and audit, not an invented completion schema.
    for name, obj in (("protocol", market_protocol), ("summary", market_summary), ("audit", market_proof), ("completion", market_receipt)):
        audit.require(obj.get("returnsCalculated") is False and type(obj.get("newStrategyTrials")) is int and obj["newStrategyTrials"] == 0,
            "market-observation-flags:"+name)
        audit.require(all((key not in obj and name == "completion") or obj.get(key) is False for key in ("certified", "targetAchieved")),
            "market-certification-flags:"+name)
    declared = {str(reference.within(root, reference.REGISTRATION)): reference.REGISTRATION_SHA256}
    for item in reference.refs(source):
        declared[str(reference.within(root, item["path"]))] = item["sha256"]
    for mapping in (source["parent"]["sourceImplementation"], market["implementation"]):
        for name, expected in mapping.items():
            declared[str(reference.within(root, name))] = expected
    for name, expected in manifest.get("physicalSourceHashes", {}).items():
        path = Path(name).resolve()
        audit.pin(path, expected)
        declared[str(path)] = expected
    source_identity = {"registrationSha256": reference.REGISTRATION_SHA256, "inputFilesSha256": declared,
        "parentProtocolHash": source["parent"]["protocolHash"], "marketProtocolHash": market["protocolHash"],
        "snapshotFingerprint": sample["expectedFullSnapshotFingerprint"], "calendarHash": raw["calendarHash"]}
    files = dict(declared)
    files[str(Path(REGISTRATION).resolve())] = REGISTRATION_SHA256
    files[str(Path(CLARIFICATION).resolve())] = CLARIFICATION_SHA256
    for item in reference.refs(document):
        files[str(reference.within(root, item["path"]))] = item["sha256"]
    identity = {"registrationSha256": REGISTRATION_SHA256, "inputRegistrationSha256": reference.REGISTRATION_SHA256,
        "inputFilesSha256": files, "sourceBinding": source_identity, "snapshotFingerprint": sample["expectedFullSnapshotFingerprint"],
        "calendarHash": raw["calendarHash"], "marketProtocolHash": market["protocolHash"], "clarification": clarification_ref}
    audit.checks += input_audit.checks
    audit.failure_count += input_audit.failure_count
    audit.issues.extend(input_audit.issues[:max(0, 100-len(audit.issues))])
    for path, expected in input_audit.bound.items():
        audit.pin(path, expected)
    for path, expected in files.items():
        audit.pin(Path(path), expected)
    dataset = reference.load_dataset(source, parent_protocol, root, audit)
    audit.require(dataset["fingerprint"] == sample["expectedFullSnapshotFingerprint"] and digest(dataset) == inputs["fullDatasetContentSha256"] and
        sorted(s["symbol"] for s in dataset["securities"]) == symbols and sum(len(s["bars"]) for s in dataset["securities"]) == sample["expectedFullBars"] and
        dataset["calendar"] == sorted(set(dataset["calendar"])) and all(d <= sample["signalEnd"] for d in dataset["calendar"]) and
        [d for d in dataset["calendar"] if sample["warmupStart"] <= d <= sample["signalEnd"]] == calendar and
        all(b["date"] <= sample["signalEnd"] for s in dataset["securities"] for b in s["bars"]), "full-raw-source-content-and-cutoff")
    return {"document": document, "inputDocument": source, "parentProtocol": parent_protocol, "marketProtocol": market_protocol,
        "manifest": manifest, "dates": dates, "symbols": symbols, "rawPaths": raw_files, "marketPaths": market_paths,
        "identity": identity, "dataset": dataset, "clarification": {**clarification_ref, "document": clarification}}


ATTEMPT_FILES = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
    "selections": "selection.jsonl.gz", "predicates": "predicates.jsonl.gz", "dailyGates": "daily-gates.jsonl.gz",
    "result": "result.json.gz", "summary": "summary.json", "attempt": "attempt.json"}


def bind_execution(reference, output, root, binding, audit):
    document = binding["document"]
    protocol = read(output / "protocol.json")
    audit.require(protocol["schema"] == "leadership-replacement-development-v1" and protocol["protocolHash"] ==
        digest({k: v for k, v in protocol.items() if k != "protocolHash"}), "protocol-canonical-identity")
    audit.require(reference.exact(protocol["registration"], document) and protocol["registrationSha256"] == REGISTRATION_SHA256 and
        reference.exact(protocol["binding"], binding["identity"]) and reference.exact(protocol["config"], document["frozenExecutionConfig"]) and
        reference.exact(protocol["clarification"], binding["clarification"]) and
        Path(protocol["workspace"]).resolve() == Path(binding["parentProtocol"]["workspace"]).resolve() and
        protocol["strategyOrder"] == [CANDIDATE_ID] and protocol["groups"] == list(GROUPS) and
        protocol["certified"] is False and protocol["targetAchieved"] is False, "exact-protocol-source-config-groups")
    names = set(document["sourceImplementation"]) | set(document["inputBindingImplementation"]) | {
        NEW_RUNNER, Path(REGISTRATION).resolve().relative_to(root).as_posix(), document["pureRule"]["path"],
        document["proposal"]["path"], document["inputBindingRegistration"]["path"], NEW_VERIFIER,
        document["prototypeReview"]["path"], document["registrationGenerator"]["path"], REFERENCE_PATH, reference.OLD_AUDITOR, binding["clarification"]["path"],
        *document["implementationFreeze"]["tests"]}
    audit.require(set(protocol["implementation"]) == names and protocol["implementation"][NEW_VERIFIER] == VERIFIER_SHA256 and
        protocol["implementation"][REFERENCE_PATH] == REFERENCE_SHA256 and
        protocol["implementation"][reference.OLD_AUDITOR] == reference.OLD_AUDITOR_SHA256 and
        protocol["implementation"][NEW_RULE] == document["pureRule"]["sha256"], "exact-reviewed-implementation-closure")
    for name, expected in {**document["sourceImplementation"], **document["inputBindingImplementation"]}.items():
        audit.require(protocol["implementation"].get(name) == expected, "frozen-dependency:"+name)
    audit.pin(output / "execution-sources.zip", protocol["executionArchiveSha256"])
    reference.archive(audit, output / "execution-sources.zip", protocol["implementation"], root)
    attempt = output / "attempts/attempt-0001"
    receipt = read(attempt / "completion.json")
    paths = {key: "attempts/attempt-0001/"+name for key, name in ATTEMPT_FILES.items()}
    paths.update(executionSources="execution-sources.zip", protocol="protocol.json")
    audit.require(set(receipt["files"]) == set(paths) and receipt["protocolHash"] == protocol["protocolHash"] and
        receipt["snapshotFingerprint"] == binding["identity"]["snapshotFingerprint"] and type(receipt["attempt"]) is int and receipt["attempt"] == 1 and
        receipt["certified"] is False and receipt["targetAchieved"] is False, "eleven-file-complete-receipt")
    for key, name in paths.items():
        audit.require(reference.exact(receipt["files"][key], {"path": name, "sha256": sha(output / name)}), "receipt-file:"+key)
        audit.pin(output / name, receipt["files"][key]["sha256"])
    freeze, marker = read(output / "freeze-attempt.json"), read(attempt / "attempt.json")
    audit.require(receipt["freezeAttemptSha256"] == protocol["freezeAttemptSha256"] == sha(output / "freeze-attempt.json") and
        freeze["candidateId"] == CANDIDATE_ID and freeze["registrationSha256"] == REGISTRATION_SHA256 and
        marker["candidateId"] == CANDIDATE_ID and marker["protocolHash"] == protocol["protocolHash"] and
        type(marker["attempt"]) is int and marker["attempt"] == 1 and
        all(obj["certified"] is False and obj["targetAchieved"] is False for obj in (freeze, marker)) and
        datetime.fromisoformat(freeze["startedAt"]) <= datetime.fromisoformat(marker["startedAt"]), "pre-run-freeze-one-attempt")
    journal = [parse(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    audit.require([r["event"] for r in journal] == ["study_started", "scoring_completed", "study_succeeded"] and
        all(r["protocolHash"] == protocol["protocolHash"] for r in journal) and reference.exact(journal[-1]["receipt"], receipt), "complete-success-journal")
    tree = inventory(output)
    reports = {name for name in tree if "/" not in name and (name == "independent-result-verification.json" or
        name.startswith("independent-result-verification-failed-") and name.endswith(".json"))}
    expected = set(paths.values()) | {"freeze-attempt.json", "events.jsonl", "attempts/attempt-0001/completion.json"}
    audit.require(tree-reports == expected, "complete-exact-output-inventory")
    for name in expected:
        audit.pin(output / name)
    return protocol, receipt, attempt, tree


def replay(reference, binding, attempt, audit):
    document, source = binding["document"], binding["inputDocument"]
    symbols, dates = binding["symbols"], binding["dates"]
    raw, inputs = source["originalRawSourcePanelBinding"], binding["manifest"]["inputs"]
    market_stream = records(binding["marketPaths"]["daily.jsonl.gz"])
    role_stream = records(binding["marketPaths"]["roles.jsonl.gz"])
    feature_stream = records(binding["rawPaths"]["features.jsonl.gz"])
    emitted = {key: records(attempt / ATTEMPT_FILES[key]) for key in ("candidates", "coverage", "signals", "selections", "predicates", "dailyGates")}
    file_source = {"roleFileSha256": source["marketInput"]["outputs"]["roles.jsonl.gz"]["sha256"],
        "featureFileSha256": raw["files"]["features.jsonl.gz"]["sha256"], "marketProtocolHash": source["marketInput"]["protocolHash"],
        "panelManifestSha256": raw["files"]["manifest.json"]["sha256"], "calendarHash": raw["calendarHash"]}
    history, windows = deque(maxlen=3), deque(maxlen=3)
    signals, gate_counts, market_counts, cohort_counts = [], Counter(), Counter(), Counter()
    totals = {"sourceRoleRows": 0, "sourceFeatureRows": 0, "rawCandidateCount": 0, "top5SignalCount": 0, "controlSignalCount": 0}
    notice = time.perf_counter()

    def compare(key, expected):
        actual = next(emitted[key], None)
        audit.check(reference.exact(actual, expected), "independent-"+key+"-row", date=expected.get("date", expected.get("signalDate")), symbol=expected.get("symbol"))

    for index, day in enumerate(dates):
        market = next(market_stream, None)
        audit.require(isinstance(market, dict) and market.get("date") == day and market.get("featureVersion") == FEATURE_VERSION and
            market.get("qDefinition") == Q_DEFINITION and type(market.get("fixedPopulation")) is int and market["fixedPopulation"] == len(symbols), "complete-market-date-identity")
        audit.require(market.get("protocolHash") == file_source["marketProtocolHash"] and market.get("calendarHash") == raw["calendarHash"] and
            market.get("inputManifestSha256") == file_source["panelManifestSha256"] and market.get("inputFeatureFileSha256") == file_source["featureFileSha256"], "market-source-identity")
        audit.require(isinstance(market.get("matched5"), dict) and "earlierDate" in market["matched5"] and
            market["matched5"]["earlierDate"] == (dates[index-5] if index >= 5 else None) and
            (index >= 5 or market["matched5"].get("available") is not True), "exact-target-only-T-minus-five")
        current, row_sources = [], {}
        market_sha = digest(market)
        for symbol in symbols:
            role, feature = next(role_stream, None), next(feature_stream, None)
            audit.require(isinstance(role, dict) and role.get("date") == day and role.get("symbol") == symbol and
                role.get("featureVersion") == FEATURE_VERSION and role.get("qDefinition") == Q_DEFINITION and role.get("qState") in STATES,
                "complete-role-row-identity")
            audit.require(isinstance(feature, dict) and feature.get("asOfDate") == day and feature.get("symbol") == symbol and
                feature.get("featureVersion") == "daily-leadership-raw-v1", "complete-feature-row-identity")
            expected_sources = {**inputs["securities"][symbol], "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}
            for row in (role, feature):
                audit.require(row.get("sampleManifestHash") == file_source["panelManifestSha256"] and row.get("calendarHash") == raw["calendarHash"] and
                    reference.exact(row.get("sourceHashes"), expected_sources), "row-source-feature-binding")
            audit.require(role.get("protocolHash") == file_source["marketProtocolHash"] and role.get("inputManifestSha256") == file_source["panelManifestSha256"] and
                role.get("inputFeatureFileSha256") == file_source["featureFileSha256"], "role-source-market-binding")
            criteria = {field: feature.get(field) if bounded(feature.get(field), -1 if field == "signedPathEfficiency20" else 0, 1) else None
                for field in ("meanStrengthPercentile20", "minimumStrengthPercentile20", "relativeReturn60Percentile", "closePathMaxDrawdown20", "signedPathEfficiency20")}
            audit.require(reference.exact(role.get("criterionValues"), criteria) and reference.exact(role.get("baseEligibility"), feature.get("baseEligibility")), "role-feature-values-and-base-qualification")
            current.append(role)
            row_sources[symbol] = {**file_source, "marketRowSha256": market_sha, "roleRowSha256": digest(role), "featureRowSha256": digest(feature)}
        history.append(current)
        windows.append({"date": day, "sha256": digest(current)})
        daily_source = {**file_source, "marketRowSha256": market_sha, "roleWindow": list(windows)}
        expected = reference_day(symbols, dates, index, list(history), market)
        daily, selection = expected["daily"], expected["selection"]
        compare("dailyGates", {"date": day, "targetIndex": index, "version": expected["version"], "candidateId": CANDIDATE_ID,
            **daily, "source": daily_source, "certified": False, "targetAchieved": False})
        for key, field in (("predicates", "evaluations"), ("candidates", "rankedCandidates"), ("signals", "signals")):
            for row in expected[field]:
                item = {**row, "source": row_sources[row["symbol"]]}
                compare(key, item)
                if key == "signals":
                    signals.append(item)
        compare("selections", {**selection, "source": daily_source})
        selected, controls = len(selection["selectedTop5Symbols"]), selection["controlMatching"]["matchedCount"]
        compare("coverage", {"date": day, "evaluatedSecurities": len(symbols), "candidateGateCounts": daily["candidateGateCounts"],
            "rawCandidateCount": len(expected["rankedCandidates"]), "selectedStrategyCount": selected, "controlSignalCount": controls,
            "controlMatching": selection["controlMatching"], "sourceRoleRows": len(symbols), "sourceFeatureRows": len(symbols), "source": daily_source})
        gate_counts.update(daily["candidateGateCounts"])
        market_counts[daily["market"]["state"]] += 1
        cohort_counts[daily["oldCohort"]["state"]] += 1
        totals["sourceRoleRows"] += len(symbols)
        totals["sourceFeatureRows"] += len(symbols)
        totals["rawCandidateCount"] += len(expected["rankedCandidates"])
        totals["top5SignalCount"] += selected
        totals["controlSignalCount"] += controls
        if time.perf_counter()-notice >= 30:
            print(f"Independent replacement replay: {index+1}/{len(dates)} sessions", flush=True)
            notice = time.perf_counter()
    for label, stream in {"sourceMarket": market_stream, "sourceRoles": role_stream, "sourceFeatures": feature_stream, **emitted}.items():
        audit.require(next(stream, None) is None, "complete-EOF:"+label)
    totals.update(scoredSessions=len(dates), candidateGateCounts=dict(gate_counts), marketGateCounts=dict(market_counts), oldCohortGateCounts=dict(cohort_counts))
    return signals, totals


def numeric_metadata(value, audit, location="result"):
    integer = {"hits", "denominator", "unresolved", "quantity", "tradingDay", "tradingDays", "horizonSessions",
        "completeCalendarCohort", "eligibleEntryCohort", "missingOrInvalidDataExcluded", "rightCensoredBeforeHorizon",
        "potentialFollowupSessions", "observedFollowupSessions"}
    numeric = {"price", "fee", "proceeds", "investedCapital", "remainingCash", "netReturnPct", "hitRate", "lower", "upper",
        "medianHitTradingDays", "waitingMedianSessions", "grossMaePct"}
    if isinstance(value, dict):
        for key, item in value.items():
            path = location+"."+key
            if item is not None and (key in integer or key.endswith("Count")):
                audit.check(type(item) is int and item >= 0, "outcome-integer-type", field=path)
            elif item is not None and key in numeric:
                audit.check(type(item) in (int, float) and math.isfinite(item), "outcome-numeric-type", field=path)
            elif isinstance(item, float):
                audit.check(math.isfinite(item), "outcome-finite-type", field=path)
            if key.endswith("Counts") and isinstance(item, dict):
                audit.check(all(type(n) is int and n >= 0 for n in item.values()), "outcome-count-map-type", field=path)
            numeric_metadata(item, audit, path)
    elif isinstance(value, list):
        for item in value:
            numeric_metadata(item, audit, location)


def audit_result(reference, binding, protocol, attempt, signals, totals, root, audit):
    with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
        result = parse(stream.read())
    summary = read(attempt / "summary.json")
    expected = {k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")}
    audit.require(set(summary) == set(expected) | {"performance", "ranking", "selectionEligible"} and
        reference.exact({k: summary[k] for k in expected}, expected) and summary["ranking"] is None and
        summary["selectionEligible"] is False, "complete-summary-result-identity")
    audit.require(result["protocolHash"] == protocol["protocolHash"] and result["snapshotFingerprint"] == binding["dataset"]["fingerprint"] and
        result["strategyOrder"] == [CANDIDATE_ID] and result["stage"] == "development-diagnostic" and result["pilot"] is False and
        result["diagnosticUnverifiedHistory"] is True and result["certified"] is False and result["targetAchieved"] is False and
        result["historicalEligibilityVerified"] is False and result["comparableRightsVerified"] is False and
        result["actionEvidence"] == "unverified", "result-registered-uncertified-identity")
    for key, expected in totals.items():
        audit.check(reference.exact(summary["performance"].get(key), expected), "independent-performance:"+key)
        if key in ("candidateGateCounts", "marketGateCounts", "oldCohortGateCounts", "sourceRoleRows", "sourceFeatureRows"):
            audit.check(reference.exact(result.get(key), expected), "independent-result-accounting:"+key)
    audit.require(totals["sourceRoleRows"] == binding["document"]["sample"]["expectedFeatureRows"] and
        sum(totals["candidateGateCounts"].values()) == totals["sourceRoleRows"], "all-symbol-predicate-conservation")
    numeric_metadata(result, audit)
    reference.CANDIDATE_ID, reference.GROUPS = CANDIDATE_ID, GROUPS
    event_document = {"frozenExecutionConfig": binding["document"]["frozenExecutionConfig"],
        "originalRawSourcePanelBinding": {"targetDates": binding["dates"]}}
    result_counts = reference.event_audit(audit, result, signals, binding["dataset"], event_document, root)
    return {**totals, **result_counts, "admittedSignals": len(signals), "dailyRows": len(binding["dates"])}


def verify(output: Path, root: Path = ROOT) -> dict:
    """Read-only replay. No market strategy evaluation or report write."""
    started = time.perf_counter()
    output, root = Path(output).resolve(), Path(root).resolve()
    report = {"schema": "leadership-replacement-independent-verification-v1", "checkedAt": datetime.now(timezone.utc).isoformat(),
        "output": str(output), "verifierSha256": VERIFIER_SHA256, "registrationSha256": REGISTRATION_SHA256,
        "reusedIndependentVerifierSha256": REFERENCE_SHA256, "certified": False, "targetAchieved": False,
        "dailyRows": 0, "sourceRoleRows": 0, "sourceFeatureRows": 0,
        "scope": [
            "Every registered symbol/day reconstructed from complete aligned audited role and feature streams: fixed T-2 cohort, both endpoint false/unknown bounds, exact challenger sequence, contemporaneous market predicates, unknown priority and unchanged diagnostic base qualification.",
            "Independent complete ranking, pre-active Top5, new-candidate-ID hash controls and original pairing; all predicate/daily/selection/coverage/candidate/signal logs, source lineage and EOF.",
            "Frozen independent event functions replay fresh active groups across years, all signal dispositions, filled price/fee/slippage and T+1 arithmetic, raw/net-high observations, adverse paths and seven-horizon/cutoff cohort arithmetic.",
            "Exact registration/input identities, source dataset content, execution closure/ZIP, single-attempt receipt/journal and start/end source hashes. Parent selection and event streams are not used to form any replacement candidate."],
        "limitations": [
            "Registered-cost/raw-price diagnostic only: the frozen registered fee model is replayed unchanged; separately recorded historical transfer-fee-model differences are not corrected or certified by this audit.",
            "Trusts the separately audited Q labels, feature ranks, market aggregates and original base-eligibility evidence; this is not a second raw-price reconstruction of all upstream features or historical eligibility.",
            "Outcome arithmetic is not a complete independent execution engine: full buy/sell eligibility, maximum affordable/participation-limited quantity and every first-executable or omitted opening target are not proved. Capacity flags are inherited; high/path audits use the recorded main exit boundary.",
            "Raw daily observations do not certify auction fills, economic corporate-action profits or the timing of two intraday events. Entry-day adverse prices may be T+1 locked; exit-day prices after the opening exit are excluded.",
            "Fixed survivor-biased development sample, adaptive prior exposure and correlated observations remain; controls are descriptive, not causal treatment pairs, and results cannot certify market influence or future net +30 probability."]}
    try:
        reference = private_reference(root)
    except Exception as error:
        report.update(passed=False, checks=1, failureCount=1, issues=[{"check": "independent-reference-unavailable", "errorType": type(error).__name__, "message": str(error)}], elapsedSeconds=time.perf_counter()-started)
        return report
    audit = reference.Audit()
    try:
        audit.pin(VERIFIER_PATH, VERIFIER_SHA256)
        audit.pin(root / REFERENCE_PATH, REFERENCE_SHA256)
        binding = bind_inputs(reference, root, audit)
        protocol, receipt, attempt, tree = bind_execution(reference, output, root, binding, audit)
        report.update(protocolHash=protocol["protocolHash"], implementation=protocol["implementation"],
            clarification=binding["identity"]["clarification"],
            completionSha256=sha(attempt / "completion.json"), resultSha256=receipt["files"]["result"]["sha256"],
            resultFileHashes={key: value["sha256"] for key, value in receipt["files"].items()}, sourceFingerprint=binding["dataset"]["fingerprint"])
        signals, totals = replay(reference, binding, attempt, audit)
        report.update(audit_result(reference, binding, protocol, attempt, signals, totals, root, audit))
        audit.require(inventory(output) == tree, "post-audit-output-tree")
    except Exception as error:
        audit.check(False, "replacement-audit-exception", errorType=type(error).__name__, message=str(error))
    for path, expected in audit.bound.items():
        try:
            audit.check(sha(path) == expected, "post-audit-file-hash", path=str(path))
        except OSError as error:
            audit.check(False, "post-audit-file-unavailable", path=str(path), message=str(error))
    report.update(passed=audit.failure_count == 0, checks=audit.checks, failureCount=audit.failure_count, issues=audit.issues,
        sourceHashes={str(p): value for p, value in audit.bound.items()}, elapsedSeconds=time.perf_counter()-started)
    return report


def save_report(output, report):
    name = "independent-result-verification.json" if report["passed"] else "independent-result-verification-failed-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".json"
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
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "failureCount": report["failureCount"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
