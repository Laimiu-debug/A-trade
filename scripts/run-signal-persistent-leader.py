"""One frozen persistent-price-leader development diagnostic, not certification.

The audited feature panel owns causal features and base-eligibility metadata.
Every panel row is inspected before the unchanged event evaluator can run.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_config import normalize_config
from service.portfolio_universe import _digest
from service.signal_persistent_leader import (
    CANDIDATE_ID, PANEL_FEATURE_VERSION, evaluate_persistent_leader, rank_persistent_leader_signals,
)
from service.signal_path_risk import PathRiskStudy, summarize_paths

_spec = importlib.util.spec_from_file_location("_persistent_leader_private_runtime", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
REGISTRATION = ROOT / "docs/signal-persistent-leader-price-proxy-v1.json"
REGISTRATION_SHA256 = "29759eb2bdd157480c515e2bbcdcdde6e8f21ea675f7bc2841f025f0df6d5bbf"
STRATEGIES = (CANDIDATE_ID,)
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
PANEL_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
               "summary.json", "execution-sources.zip", "attempt.json"}
EXTRA_FILES = {"predicates": "predicates.jsonl.gz", "selections": "selection.jsonl.gz",
               "executionSources": "execution-sources.zip"}
_base_protocol, _base_evaluate, _base_execute = runtime.make_protocol, runtime.evaluate_signals, runtime.execute
_base_bound_snapshot, _write_json = source_tools.bound_snapshot, source_tools.write_json
_CONTEXT = None


def registration():
    # No fallback to the design draft or to an unbound panel is permitted.
    if source_tools.file_hash(REGISTRATION) != REGISTRATION_SHA256:
        raise ValueError("Persistent-leader registration changed")
    document = source_tools.read_json(REGISTRATION)
    if document.get("candidateId") != CANDIDATE_ID or not document.get("registeredAt"):
        raise ValueError("Candidate is not formally registered")
    return document


def verify_panel_binding(document):
    requested = document["registrationPrerequisites"]
    directory = (ROOT / requested["panelDirectory"]).resolve()
    digests = requested.get("panelFilesSha256")
    expected = PANEL_FILES | {"independent-verification.json"}
    if not isinstance(digests, dict) or set(digests) != expected:
        raise ValueError("Registered panel file binding is incomplete")
    for name, digest in digests.items():
        if (not isinstance(digest, str) or len(digest) != 64 or
                source_tools.file_hash(directory / name) != digest):
            raise ValueError("Registered panel file hash changed: " + name)
    for field, name in (("panelManifestSha256", "manifest.json"), ("actualPanelAuditSha256", "independent-verification.json")):
        if requested.get(field) != digests[name]:
            raise ValueError("Registered panel binding references disagree")
    completion_hash = requested.get("panelCompletionSha256")
    if not isinstance(completion_hash, str) or source_tools.file_hash(directory / "completion.json") != completion_hash:
        raise ValueError("Registered panel completion hash changed")
    manifest, completion, audit = (source_tools.read_json(directory / name) for name in
                                   ("manifest.json", "completion.json", "independent-verification.json"))
    sample, inputs = document["sample"], manifest["inputs"]
    if (manifest.get("featureVersion") != PANEL_FEATURE_VERSION or
            completion.get("sampleManifestHash") != digests["manifest.json"] or
            audit.get("sampleManifestHash") != digests["manifest.json"] or audit.get("passed") is not True or
            type(audit.get("failureCount")) is not int or audit["failureCount"] != 0 or
            audit.get("featureRows") != sample["expectedFeatureRows"] or
            completion.get("featureRows") != sample["expectedFeatureRows"] or
            completion.get("targetSessions") != sample["expectedScoredSessions"]):
        raise ValueError("Actual panel verification or row identity is not accepted")
    if set(completion.get("files", {})) != PANEL_FILES:
        raise ValueError("Panel completion is incomplete")
    for name, entry in completion["files"].items():
        if entry != {"path": name, "sha256": digests[name]}:
            raise ValueError("Panel completion differs from registered files")
    symbols, calendar = inputs["symbols"], inputs["calendar"]["dates"]
    if (symbols != sorted(set(symbols)) or len(symbols) != sample["expectedFullSecurities"] or
            inputs.get("symbolCount") != len(symbols) or inputs.get("symbolsSha256") != _digest(symbols) or
            inputs.get("snapshotFingerprint") != sample["expectedFullSnapshotFingerprint"] or
            inputs.get("retainedBarCount") != sample["expectedFullBars"] or
            set(inputs["securities"]) != set(symbols) or calendar != sorted(set(calendar)) or
            inputs["calendar"].get("sha256") != _digest(calendar) or
            any(d < sample["warmupStart"] or d > sample["signalEnd"] for d in calendar) or
            sum(sample["signalStart"] <= d <= sample["signalEnd"] for d in calendar) != sample["expectedScoredSessions"] or
            len(symbols) * sample["expectedScoredSessions"] != sample["expectedFeatureRows"]):
        raise ValueError("Panel sample or calendar differs from registration")
    identity = {"directory": str(directory), "panelManifestSha256": digests["manifest.json"],
        "panelCompletionSha256": completion_hash, "actualPanelAuditSha256": digests["independent-verification.json"],
        "panelFilesSha256": dict(digests), "featureVersion": PANEL_FEATURE_VERSION,
        "snapshotFingerprint": inputs["snapshotFingerprint"], "calendarHash": inputs["calendar"]["sha256"]}
    return {"directory": directory, "manifest": manifest, "completion": completion, "audit": audit,
            "identity": identity, "sample": sample}


def iter_panel_days(binding):
    inputs, sample = binding["manifest"]["inputs"], binding["sample"]
    symbols = inputs["symbols"]
    dates = [d for d in inputs["calendar"]["dates"] if sample["signalStart"] <= d <= sample["signalEnd"]]
    with gzip.open(binding["directory"] / "features.jsonl.gz", "rt", encoding="utf-8") as stream:
        for day in dates:
            rows = []
            for symbol in symbols:
                line = stream.readline()
                if not line:
                    raise ValueError("Panel ended before the full registered row population")
                row = json.loads(line)
                expected_hashes = {**inputs["securities"][symbol],
                    "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}
                if (not isinstance(row, dict) or row.get("symbol") != symbol or row.get("asOfDate") != day or
                        row.get("featureVersion") != PANEL_FEATURE_VERSION or
                        row.get("sampleManifestHash") != binding["identity"]["panelManifestSha256"] or
                        row.get("calendarHash") != inputs["calendar"]["sha256"] or row.get("sourceHashes") != expected_hashes):
                    raise ValueError("Panel row order, identity, version or source binding changed")
                rows.append(row)
            yield day, rows
        if stream.readline():
            raise ValueError("Panel contains trailing rows outside the registered population")


def settings(args):
    # A legacy strategy-shaped config satisfies the unchanged execution schema;
    # no legacy strategy/scorer is run and the detector supplies its own ID.
    return normalize_config({"startDate": args.start, "endDate": args.end, "initialCapital": 1000000,
        "strategies": [{"id": "trendBreakout", "kind": "trendBreakout", "pool": "base"}],
        "combinations": [], "allocation": {"mode": "equal", "strategyIds": ["trendBreakout"]},
        "weighting": "equal", "topN": 5, "maxPositions": 5, "maxStockWeight": .1, "rebalanceMode": "risk",
        "maxHoldingDays": 0, "stopLossPct": 0, "takeProfitPct": 0, "trailingStopPct": 0,
        "commissionRate": .0003, "minCommission": 5, "transferFeeRate": .00001, "slippageBps": 10,
        "maxParticipationRate": .01, "stampDutyRate": None,
        "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 120, "minAmount20": 50000000}})


def implementation_hashes():
    paths = {Path(__file__), Path(runtime.__file__), Path(source_tools.__file__), REGISTRATION,
        ROOT / "service/signal_persistent_leader.py", ROOT / "service/signal_target_study.py",
        ROOT / "service/signal_path_risk.py", ROOT / "service/research_target.py", ROOT / "service/tdx.py",
        ROOT / "service/strategies.py", ROOT / "service/execution_evidence.py", *ROOT.glob("service/portfolio_*.py")}
    return {p.relative_to(ROOT).as_posix(): source_tools.file_hash(p) for p in sorted(paths)}


def make_protocol(args):
    document = registration()
    binding = verify_panel_binding(document)
    protocol = _base_protocol(args)
    protocol.update(schema="persistent-price-leader-development-v1", studyKind="one-audited-panel-persistent-leader-diagnostic",
        registration=document, registrationSha256=REGISTRATION_SHA256, panelBinding=binding["identity"],
        implementation=implementation_hashes(),
        ranking="meanStrengthPercentile20-desc;relativeReturn60Percentile-desc;symbol-asc;no-active-backfill",
        candidateLog="all-raw-candidates-with-six-predicates-and-frozen-panel-evidence",
        predicateLog="every-fixed-sample-security-date;exclusive-kind-and-all-independently-knowable-predicates",
        selectionLog="full-raw-ranking-top-five-before-active-exclusion;all-control-matching",
        eligibility="audited-panel-baseEligibility-only;no-recomputed-scorer-or-ranks",
        priceBasis="security.bars-unadjusted-direct-calendar-alignment", actionEvidence="unverified",
        historicalEligibilityVerified=False, comparableRightsVerified=False,
        pathRisk={"enabled": True, "boundariesRawLowPct": [-10, -20], "horizonsSessions": list(runtime.HORIZONS)},
        controlInterpretation="descriptive-only;non-top-five-triggers-can-be-controls;independent-active-suppression-breaks-pairing",
        pythonVersion=sys.version)
    return protocol


def validate_sample(dataset, start, end):
    sample = registration()["sample"]
    if (start, end) != (sample["signalStart"], sample["signalEnd"]):
        raise ValueError("Use exactly the registered signal window")
    symbols = [s["symbol"] for s in dataset["securities"]]
    if (dataset.get("fingerprint") != sample["expectedFullSnapshotFingerprint"] or
            len(symbols) != sample["expectedFullSecurities"] or len(set(symbols)) != len(symbols) or
            sum(len(s["bars"]) for s in dataset["securities"]) != sample["expectedFullBars"] or
            dataset["calendar"] != sorted(set(dataset["calendar"])) or any(d > end for d in dataset["calendar"]) or
            sum(start <= d <= end for d in dataset["calendar"]) != sample["expectedScoredSessions"]):
        raise ValueError("Source differs from registered frozen sample/fingerprint/counts")


def bound_snapshot(dataset, start, end):
    result = _base_bound_snapshot(dataset, start, end)
    validate_sample(result, start, end)
    return result


def _verify_source_binding(dataset, binding):
    inputs = binding["manifest"]["inputs"]
    if _digest(dataset) != inputs["fullDatasetContentSha256"]:
        raise ValueError("Full raw snapshot content differs from audited panel source")
    sample = binding["sample"]
    if [d for d in dataset["calendar"] if sample["warmupStart"] <= d <= sample["signalEnd"]] != inputs["calendar"]["dates"]:
        raise ValueError("Raw source calendar differs from audited panel calendar")
    _verify_physical_source_hashes(binding)


def _verify_physical_source_hashes(binding):
    for name, digest in binding["manifest"].get("physicalSourceHashes", {}).items():
        if source_tools.file_hash(Path(name)) != digest:
            raise ValueError("Physical panel source changed: " + name)


def _archive_sources(path, implementation):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, digest in implementation.items():
            content = (ROOT / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("Execution source changed before archive")
            archive.writestr(name, content)


def _verify_archive(path, implementation):
    with zipfile.ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(implementation):
            raise ValueError("Execution archive entries differ from frozen dependencies")
        for name, digest in implementation.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("Execution archive source bytes changed")


def collect_signals(dataset, config, attempt):
    global _CONTEXT
    document = registration()
    validate_sample(dataset, config["startDate"], config["endDate"])
    binding = verify_panel_binding(document)
    _verify_source_binding(dataset, binding)
    implementation = implementation_hashes()
    protocol_path = attempt.parent.parent / "protocol.json"
    if protocol_path.is_file():
        protocol = source_tools.read_json(protocol_path)
        if protocol["implementation"] != implementation or protocol["panelBinding"] != binding["identity"] or protocol["config"] != config:
            raise ValueError("Protocol dependency, panel or configuration drift before extraction")
    _archive_sources(attempt / EXTRA_FILES["executionSources"], implementation)
    _CONTEXT = {"dataset": dataset, "binding": binding, "implementation": implementation, "attempt": attempt,
        "protocolPath": protocol_path if protocol_path.is_file() else None,
        "protocolSha256": source_tools.file_hash(protocol_path) if protocol_path.is_file() else None}
    inputs = binding["manifest"]["inputs"]
    symbols, calendar = inputs["symbols"], inputs["calendar"]["dates"]
    securities = {s["symbol"]: s for s in dataset["securities"]}
    raw = {s: {b["date"]: b for b in securities[s]["bars"]} for s in symbols}
    first = next(i for i, d in enumerate(calendar) if d >= config["startDate"])
    windows = {s: deque((raw[s].get(d) for d in calendar[max(0, first - 60):first]), maxlen=61) for s in symbols}
    indices = {d: i for i, d in enumerate(calendar)}
    signals, total_kinds, total_reasons = [], Counter(), Counter()
    total_raw = evaluated = days = 0
    started = last_notice = time.perf_counter()
    with gzip.open(attempt / "predicates.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as predicate_file, gzip.open(
            attempt / "candidates.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as candidates_file, gzip.open(
            attempt / "coverage.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as coverage_file, gzip.open(
            attempt / "selection.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as selection_file:
        for day, panel_rows in iter_panel_days(binding):
            index = indices[day]
            calendar61 = calendar[max(0, index - 60):index + 1]
            candidates, kinds, reasons, base_eligible = [], Counter(), Counter(), set()
            for row in panel_rows:
                symbol = row["symbol"]
                windows[symbol].append(raw[symbol].get(day))
                raw61 = [{**bar, "priceBasisId": "raw-unadjusted"} if bar is not None else None for bar in windows[symbol]]
                outcome = evaluate_persistent_leader(symbol, day, calendar61, raw61, row)
                kinds[outcome["kind"]] += 1
                reasons[outcome["reason"]] += 1
                reference = {"panelManifestSha256": binding["identity"]["panelManifestSha256"],
                    "panelFeatureFileSha256": binding["identity"]["panelFilesSha256"]["features.jsonl.gz"],
                    "calendarHash": row["calendarHash"], "sourceHashes": row["sourceHashes"]}
                runtime.write_record(predicate_file, {**{k: v for k, v in outcome.items() if k != "signal"}, **reference})
                state = row.get("baseEligibility")
                if isinstance(state, dict) and state.get("eligible") is True:
                    base_eligible.add(symbol)
                if outcome["signal"] is not None:
                    candidates.append({**outcome["signal"], **reference})
            if sum(kinds.values()) != len(symbols) or sum(reasons.values()) != len(symbols):
                raise ValueError("Daily all-row predicate coverage is incomplete")
            ranked = rank_persistent_leader_signals(candidates)
            for signal in ranked:
                runtime.write_record(candidates_file, signal)
                if signal["selectedTop5"]:
                    signals.append(signal)
            selected = [s["symbol"] for s in ranked if s["selectedTop5"]]
            pool = base_eligible - set(selected)
            controls = sorted(pool, key=lambda s: (hashlib.sha256(
                f"{runtime.CONTROL_SEED}:{day}:{CANDIDATE_ID}:{s}".encode()).hexdigest(), s))[:len(selected)]
            for rank, symbol in enumerate(controls, 1):
                signals.append({"strategyId": "control_" + CANDIDATE_ID, "pairedStrategyId": CANDIDATE_ID,
                    "symbol": symbol, "signalDate": day, "rank": rank, "score": 0., "rawScore": 0.,
                    "scoreBasis": "deterministic-control-hash-not-strategy-score", "selectedTop5": False,
                    "panelManifestSha256": binding["identity"]["panelManifestSha256"]})
            matching = {"baseEligibleCount": len(base_eligible), "eligibleControlPoolSize": len(pool),
                "requestedCount": len(selected), "matchedCount": len(controls), "shortfall": len(selected) - len(controls),
                "selectedSymbols": controls}
            runtime.write_record(selection_file, {"date": day, "strategyId": CANDIDATE_ID,
                "panelManifestSha256": binding["identity"]["panelManifestSha256"], "rawCandidateCount": len(ranked),
                "fullRanking": [{k: r[k] for k in ("symbol", "rank", "meanStrengthPercentile20", "relativeReturn60Percentile", "selectedTop5")} for r in ranked],
                "selectedTop5Symbols": selected, "controlMatching": matching, "activeExclusionApplied": False, "certified": False})
            runtime.write_record(coverage_file, {"date": day, "panelManifestSha256": binding["identity"]["panelManifestSha256"],
                "evaluatedSecurities": len(symbols), "evaluatedKinds": dict(kinds), "evaluatedReasons": dict(reasons),
                "candidateCounts": {CANDIDATE_ID: len(ranked)}, "controlMatching": {CANDIDATE_ID: matching},
                "eligibleSecurities": len(base_eligible), "pointInTimeComplete": False})
            total_kinds.update(kinds)
            total_reasons.update(reasons)
            total_raw += len(ranked)
            evaluated += len(symbols)
            days += 1
            if time.perf_counter() - last_notice >= 30:
                print(f"Persistent leader: {days}/{binding['sample']['expectedScoredSessions']} sessions; {evaluated} predicate rows", flush=True)
                last_notice = time.perf_counter()
    # The generator's EOF check has now run. No event/outcome was evaluated.
    if days != binding["sample"]["expectedScoredSessions"] or evaluated != binding["sample"]["expectedFeatureRows"]:
        raise ValueError("Registered panel population is incomplete")
    with gzip.open(attempt / "signals.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as stream:
        for signal in signals:
            runtime.write_record(stream, signal)
    return signals, {"scoringSeconds": time.perf_counter() - started, "scoredSessions": days,
        "evaluatedSecuritySessions": evaluated, "evaluatedKinds": dict(total_kinds), "evaluatedReasons": dict(total_reasons),
        "candidateCounts": {CANDIDATE_ID: total_raw}, "top5SignalCount": sum(s["strategyId"] == CANDIDATE_ID for s in signals),
        "controlSignalCount": sum(s["strategyId"] != CANDIDATE_ID for s in signals)}


def evaluate_signals(dataset, config, signals):
    result = _base_evaluate(dataset, config, signals)
    study = PathRiskStudy(dataset, config)
    paths = [study.evaluate(event) for event in result["events"] if event["entry"]["status"] == "filled"]
    result.update(pathRiskEvents=paths, pathRiskSummary=summarize_paths(paths, horizons=runtime.HORIZONS),
        actionEvidence="unverified", historicalEligibilityVerified=False, comparableRightsVerified=False,
        resultMeaning="audited-raw-price-feature-diagnostic;not-corporate-action-economic-profit-or-goal-certification")
    for group in result["groups"]:
        selected = [s for s in signals if s["strategyId"] == group["strategyId"]]
        group.update(role="control" if group["strategyId"].startswith("control_") else "persistent-price-leader-diagnostic",
            distinctSignalSymbols=len({s["symbol"] for s in selected}), distinctSignalDates=len({s["signalDate"] for s in selected}),
            actionEvidence="unverified")
    return result


def _final_checks(attempt):
    if _CONTEXT is None or _CONTEXT["attempt"] != attempt:
        raise ValueError("Missing frozen pre-extraction source context")
    binding = verify_panel_binding(registration())
    if binding["identity"] != _CONTEXT["binding"]["identity"] or implementation_hashes() != _CONTEXT["implementation"]:
        raise ValueError("Panel or execution source drift during study")
    if _CONTEXT["protocolPath"] is not None and source_tools.file_hash(_CONTEXT["protocolPath"]) != _CONTEXT["protocolSha256"]:
        raise ValueError("Frozen protocol changed during study")
    _verify_source_binding(_CONTEXT["dataset"], binding)
    _verify_archive(attempt / EXTRA_FILES["executionSources"], _CONTEXT["implementation"])


def _write_with_extra_receipt(path, value):
    if Path(path).name == "summary.json":
        value.pop("pathRiskEvents", None)
    if Path(path).name == "completion.json":
        attempt, output = Path(path).parent, Path(path).parent.parent.parent
        _final_checks(attempt)
        for key, name in EXTRA_FILES.items():
            file = attempt / name
            if not file.is_file():
                raise ValueError("Required persistent-leader artifact is missing")
            value["files"][key] = {"path": file.relative_to(output).as_posix(), "sha256": source_tools.file_hash(file)}
        value["panelBinding"] = _CONTEXT["binding"]["identity"]
    return _write_json(path, value)


def verify_completion(output, receipt, protocol_hash, fingerprint):
    if receipt.get("protocolHash") != protocol_hash or receipt.get("snapshotFingerprint") != fingerprint:
        raise ValueError("Completed study protocol/source identity changed")
    if set(receipt.get("files", {})) != {"candidates", "coverage", "signals", "result", "summary", *EXTRA_FILES}:
        raise ValueError("Completed persistent-leader artifact manifest is incomplete")
    binding = verify_panel_binding(registration())
    if receipt.get("panelBinding") != binding["identity"]:
        raise ValueError("Completed panel binding changed")
    _verify_physical_source_hashes(binding)
    for record in receipt["files"].values():
        path = (output / record["path"]).resolve()
        if not path.is_relative_to(output) or not path.is_file() or source_tools.file_hash(path) != record["sha256"]:
            raise ValueError("Successful study artifact missing or changed; refusing to rerun")
    protocol = source_tools.read_json(output / "protocol.json")
    if protocol["implementation"] != implementation_hashes():
        raise ValueError("Completed execution dependencies changed")
    _verify_archive(output / receipt["files"]["executionSources"]["path"], protocol["implementation"])


def execute(args):
    global _CONTEXT
    _CONTEXT = None
    if not args.diagnostic_unverified_history:
        raise ValueError("Raw-price study requires explicit --diagnostic-unverified-history")
    document = registration()
    sample = document["sample"]
    if (args.start, args.end) != (sample["signalStart"], sample["signalEnd"]) or args.max_symbols is not None:
        raise ValueError("Use the registered full sample/window; no sample limit or date changes")
    panel = (ROOT / document["registrationPrerequisites"]["panelDirectory"]).resolve()
    if Path(args.output).resolve().is_relative_to(panel):
        raise ValueError("Output must be outside the immutable audited panel")
    return _base_execute(args)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostic-unverified-history", action="store_true")
    args = parser.parse_args(argv)
    args.start, args.end, args.max_symbols = "2022-01-01", "2023-12-31", None
    try:
        return execute(args)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Persistent leader study stopped: {error}", file=sys.stderr)
        return 2


runtime.STRATEGIES, runtime.GROUPS = STRATEGIES, GROUPS
runtime.settings, runtime.make_protocol = settings, make_protocol
runtime.collect_signals, runtime.evaluate_signals = collect_signals, evaluate_signals
runtime.verify_completion, runtime.execute = verify_completion, execute
source_tools.write_json, source_tools.bound_snapshot = _write_with_extra_receipt, bound_snapshot


if __name__ == "__main__":
    raise SystemExit(main())
