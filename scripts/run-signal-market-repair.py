"""Frozen market-repair and prior-strength filter over parent pre-active signals.

Freeze first; execution is a separate action. No parent detector, cross-section
or control lottery is rerun. The private baseline evaluator starts fresh active
state and retains the existing fees, target proxies and censoring rules.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.signal_market_repair import CANDIDATE_ID, SOURCE_FEATURE_VERSION, evaluate_market_repair, evaluate_resilient_gate
from service.signal_path_risk import PathRiskStudy, summarize_paths

_spec = importlib.util.spec_from_file_location("_market_repair_private_base", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
_base_evaluate = runtime.evaluate_signals
_LOADED_RUNNER_SHA = source_tools.file_hash(Path(__file__))
_LOADED_GATE_SHA = source_tools.file_hash(ROOT / "service/signal_market_repair.py")
REGISTRATION = ROOT / "docs/signal-market-repair-resilience-price-proxy-v1.json"
REGISTRATION_SHA256 = "13ef6bc30ad4d7007cad62c37babadc4e2a24f5f96a35cf7fcbb40cfdedc5a05"
PARENT_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
GROUP_MAPPING = {PARENT_ID: GROUPS[0], "control_" + PARENT_ID: GROUPS[1]}
runtime.STRATEGIES, runtime.GROUPS = (CANDIDATE_ID,), GROUPS
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
PARENT_FILES = {"candidates", "coverage", "signals", "result", "summary", "predicates", "selections", "executionSources"}
PARENT_INPUTS = {"protocol.json", "snapshot.json", "selection.json", "completion.json", "independent-result-verification.json"}
MARKET_OUTPUTS = {"attempt.json", "daily.jsonl.gz", "execution-sources.zip", "protocol.json", "roles.jsonl.gz", "summary.json"}
RAW_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz", "summary.json", "execution-sources.zip", "attempt.json", "independent-verification.json", "completion.json"}
ATTEMPT_FILES = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
    "result": "result.json.gz", "summary": "summary.json", "selections": "selection.jsonl.gz",
    "marketGates": "market-gates.jsonl.gz", "filterDecisions": "filter-decisions.jsonl.gz", "attempt": "attempt.json"}
RECEIPT_KEYS = {*ATTEMPT_FILES, "executionSources", "protocol"}


def sha(path):
    return source_tools.file_hash(Path(path))


def digest(value):
    return runtime._digest(value)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def same(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def registration():
    require(sha(REGISTRATION) == REGISTRATION_SHA256, "Registration source changed")
    document = read_json(REGISTRATION)
    require(document.get("candidateId") == CANDIDATE_ID and document.get("registeredAt") and
        document.get("status") == "registered-rule-awaiting-reviewed-runner", "Rule is not registered")
    require(same(document["selectionAndReplay"]["groupMapping"], GROUP_MAPPING), "Registered group mapping changed")
    require(document["parent"]["candidateId"] == PARENT_ID, "Parent identity changed")
    require(document["unchangedExecutionAndSample"]["horizonsSessions"] == list(HORIZONS), "Registered horizons changed")
    return document


def _local_path(name):
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Bound file missing or outside project: " + str(name))
    return path


def _check_archive(path, implementation):
    with zipfile.ZipFile(path) as archive:
        require(sorted(archive.namelist()) == sorted(implementation), "Archive dependency population changed")
        for name, expected in implementation.items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected, "Archived dependency changed: " + name)


def _audit_ok(audit):
    return audit.get("passed") is True and type(audit.get("failureCount")) is int and audit["failureCount"] == 0


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
    require(set(mc["files"]) == MARKET_OUTPUTS and set(ma["resultFileHashes"]) == MARKET_OUTPUTS, "Market receipt incomplete")
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
    identity = {"registrationSha256": REGISTRATION_SHA256, "inputFilesSha256": files,
        "parentProtocolHash": parent["protocolHash"], "marketProtocolHash": market["protocolHash"],
        "snapshotFingerprint": fingerprint, "calendarHash": raw["calendarHash"]}
    return {"document": doc, "parent_protocol": pp, "manifest": manifest, "dates": dates, "symbols": symbols,
        "files": files, "identity": identity, "parentPaths": parent_paths, "marketPaths": market_paths, "rawPaths": raw_paths}


def iter_records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line, object_pairs_hook=unique_pairs)
            require(isinstance(row, dict), "JSONL record is not an object")
            yield row


def _end(iterator, label):
    require(next(iterator, None) is None, label + " has extra records")


def iter_market_days(binding):
    doc, dates = binding["document"], binding["dates"]
    source = iter_records(binding["marketPaths"]["daily.jsonl.gz"])
    for index, day in enumerate(dates):
        row = next(source, None)
        require(row is not None and row.get("date") == day and row.get("featureVersion") == SOURCE_FEATURE_VERSION,
            "Market row date/version missing or changed")
        require(type(row.get("fixedPopulation")) is int and row["fixedPopulation"] == len(binding["symbols"]), "Market fixed population differs")
        require(row.get("protocolHash") == doc["marketInput"]["protocolHash"] and
            row.get("calendarHash") == doc["originalRawSourcePanelBinding"]["calendarHash"] and
            row.get("inputManifestSha256") == doc["originalRawSourcePanelBinding"]["files"]["manifest.json"]["sha256"] and
            row.get("inputFeatureFileSha256") == doc["originalRawSourcePanelBinding"]["files"]["features.jsonl.gz"]["sha256"], "Market row source identity differs")
        require(isinstance(row.get("matched5"), dict) and row["matched5"].get("earlierDate") == (dates[index - 5] if index >= 5 else None), "Market exact T-5 date differs")
        yield row
    _end(source, "Market daily stream")


def iter_role_days(binding):
    """Validate the entire immutable role population, including noncandidates."""
    doc, inputs = binding["document"], binding["manifest"]["inputs"]
    source = iter_records(binding["marketPaths"]["roles.jsonl.gz"])
    raw_binding = doc["originalRawSourcePanelBinding"]
    for day in binding["dates"]:
        rows = []
        for symbol in binding["symbols"]:
            row = next(source, None)
            require(row is not None and row.get("date") == day and row.get("symbol") == symbol,
                "Role population/date/symbol order differs")
            require(row.get("featureVersion") == SOURCE_FEATURE_VERSION and
                row.get("qDefinition") == "persistent-price-strength-five-gates-v1" and
                row.get("qState") in ("true", "false", "unknown"), "Role feature/Q definition or enum differs")
            require(row.get("protocolHash") == doc["marketInput"]["protocolHash"] and
                row.get("calendarHash") == raw_binding["calendarHash"] and
                row.get("inputManifestSha256") == row.get("sampleManifestHash") == raw_binding["files"]["manifest.json"]["sha256"] and
                row.get("inputFeatureFileSha256") == raw_binding["files"]["features.jsonl.gz"]["sha256"] and
                same(row.get("sourceHashes"), {**inputs["securities"][symbol],
                    "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}), "Role row source identity differs")
            rows.append(row)
        yield day, rows
    _end(source, "Market roles stream")


def iter_parent_days(binding):
    paths, dates, symbols = binding["parentPaths"], binding["dates"], set(binding["symbols"])
    candidates, signals = iter_records(paths["candidates"]), iter_records(paths["signals"])
    selections, coverage = iter_records(paths["selections"]), iter_records(paths["coverage"])
    candidate, signal = next(candidates, None), next(signals, None)
    manifest_hash = binding["document"]["originalRawSourcePanelBinding"]["files"]["manifest.json"]["sha256"]
    for day in dates:
        selection, cover = next(selections, None), next(coverage, None)
        require(selection is not None and cover is not None and selection.get("date") == cover.get("date") == day, "Parent daily population/order changed")
        require(selection.get("strategyId") == PARENT_ID and selection.get("activeExclusionApplied") is False and
            selection.get("panelManifestSha256") == cover.get("panelManifestSha256") == manifest_hash,
            "Parent selection identity/active boundary changed")
        rows, selected = [], []
        while candidate is not None and candidate.get("signalDate") == day:
            rows.append(candidate)
            candidate = next(candidates, None)
        while signal is not None and signal.get("signalDate") == day:
            selected.append(signal)
            signal = next(signals, None)
        require(candidate is None or candidate.get("signalDate", "") > day, "Parent candidates out of order")
        require(signal is None or signal.get("signalDate", "") > day, "Parent signals out of order")
        require(len({r.get("symbol") for r in rows}) == len(rows), "Duplicate raw candidate")
        require(all(r.get("strategyId") == PARENT_ID and r.get("symbol") in symbols and
            r.get("panelManifestSha256") == manifest_hash for r in rows), "Parent candidate identity changed")
        require(rows == sorted(rows, key=lambda r: (-r["meanStrengthPercentile20"], -r["relativeReturn60Percentile"], r["symbol"])), "Parent lexicographic ranking changed")
        for rank, row in enumerate(rows, 1):
            require(type(row.get("rank")) is int and row["rank"] == rank and row.get("selectedTop5") is (rank <= 5), "Parent rank/Top5 changed")
        full_rank = [{k: r[k] for k in ("symbol", "rank", "meanStrengthPercentile20", "relativeReturn60Percentile", "selectedTop5")} for r in rows]
        top = rows[:5]
        require(same(selection.get("fullRanking"), full_rank) and selection.get("rawCandidateCount") == len(rows) and
            selection.get("selectedTop5Symbols") == [r["symbol"] for r in top] and same(selected[:len(top)], top), "Parent full ranking/Top5/signal linkage changed")
        controls = selected[len(top):]
        matching = selection["controlMatching"]
        require(same(cover["controlMatching"], {PARENT_ID: matching}) and cover["candidateCounts"] == {PARENT_ID: len(rows)} and
            cover["evaluatedSecurities"] == len(symbols) and sum(cover["evaluatedKinds"].values()) == len(symbols) and
            sum(cover["evaluatedReasons"].values()) == len(symbols), "Parent coverage differs")
        control_symbols = [r["symbol"] for r in controls]
        require(len(set(control_symbols)) == len(controls) and not set(control_symbols).intersection(r["symbol"] for r in top) and
            control_symbols == matching["selectedSymbols"] and matching["requestedCount"] == len(top) and
            matching["matchedCount"] == len(controls) and matching["shortfall"] == len(top) - len(controls) and
            matching["baseEligibleCount"] == cover["eligibleSecurities"] and
            matching["eligibleControlPoolSize"] == matching["baseEligibleCount"] - len(top), "Parent control matching changed")
        require(control_symbols == sorted(control_symbols, key=lambda s: (hashlib.sha256(f"{runtime.CONTROL_SEED}:{day}:{PARENT_ID}:{s}".encode()).hexdigest(), s)), "Parent control order changed")
        for rank, row in enumerate(controls, 1):
            require(row.get("strategyId") == "control_" + PARENT_ID and row.get("pairedStrategyId") == PARENT_ID and
                row.get("symbol") in symbols and type(row.get("rank")) is int and row["rank"] == rank and
                row.get("selectedTop5") is False and row.get("panelManifestSha256") == manifest_hash,
                "Parent control identity/rank changed")
        yield day, rows, selection, cover, selected
    require(candidate is None and signal is None, "Parent sparse streams have trailing records")
    _end(candidates, "Parent candidates")
    _end(signals, "Parent signals")
    _end(selections, "Parent selections")
    _end(coverage, "Parent coverage")


def collect_signals(binding, attempt):
    started = time.perf_counter()
    last_notice = started
    signals, accounting, gate_counts, candidate_counts = [], {g: Counter() for g in GROUPS}, Counter(), Counter()
    market_history, role_history = deque(maxlen=6), deque(maxlen=5)
    source_role_rows, unmatched_true = 0, 0
    names = {k: ATTEMPT_FILES[k] for k in ("candidates", "coverage", "signals", "selections", "marketGates", "filterDecisions")}
    with ExitStack() as stack:
        writers = {k: stack.enter_context(gzip.open(attempt / name, "xt", encoding="utf-8", compresslevel=1)) for k, name in names.items()}
        market, roles = iter_market_days(binding), iter_role_days(binding)
        for index, (day, candidates, selection, coverage, parent_signals) in enumerate(iter_parent_days(binding)):
            market_row = next(market, None)
            require(market_row is not None and market_row["date"] == day, "Market/parent calendar mismatch")
            role_day = next(roles, None)
            require(role_day is not None and role_day[0] == day, "Roles/parent calendar mismatch")
            source_role_rows += len(role_day[1])
            market_history.append(market_row)
            gate = evaluate_market_repair(binding["dates"], index, list(market_history))
            state = gate["marketGate"]
            gate_counts[state] += 1
            gate_record = {**gate, "sourceMarketRowSha256": digest(market_row),
                "sourceMarketRowHashes": [{"date": row["date"], "sha256": digest(row)} for row in market_history],
                "marketDailySha256": binding["document"]["marketInput"]["outputs"]["daily.jsonl.gz"]["sha256"]}
            runtime.write_record(writers["marketGates"], gate_record)
            compounds, daily_candidates = {}, Counter()
            for candidate in candidates:
                symbol = candidate["symbol"]
                history = [rows[symbol] for rows in role_history]
                compound = evaluate_resilient_gate(symbol, binding["dates"], index, history, gate)
                compound.update(sourceRoleRowHashes=[{"date": row["date"], "symbol": symbol, "sha256": digest(row)} for row in history],
                    sourceRolesFileSha256=binding["document"]["marketInput"]["outputs"]["roles.jsonl.gz"]["sha256"],
                    sourceMarketGateSha256=digest(gate_record))
                compounds[candidate["rank"]] = compound
                candidate_counts[compound["signalGate"]] += 1
                daily_candidates[compound["signalGate"]] += 1
                runtime.write_record(writers["candidates"], {**candidate, "parentStrategyId": candidate["strategyId"],
                    "marketGate": state, "signalGate": compound["signalGate"], "compoundGate": compound,
                    "compoundGateSha256": digest(compound), "parentCandidateSha256": digest(candidate)})
            strategy_by_rank = {row["rank"]: row for row in parent_signals if row["strategyId"] == PARENT_ID}
            control_ranks = {row["rank"] for row in parent_signals if row["strategyId"] == "control_" + PARENT_ID}
            unmatched = sum(compounds[rank]["signalGate"] == "true" and rank not in control_ranks for rank in strategy_by_rank)
            unmatched_true += unmatched
            strategy_gates = [{"rank": rank, "symbol": strategy["symbol"], "signalGate": compounds[rank]["signalGate"],
                "strategyGateSha256": digest(compounds[rank])} for rank, strategy in strategy_by_rank.items()]
            admitted_today = 0
            for parent in parent_signals:
                old_id, new_id = parent["strategyId"], GROUP_MAPPING[parent["strategyId"]]
                source_key = [old_id, parent["symbol"], parent["signalDate"]]
                paired = strategy_by_rank[parent["rank"]]
                compound = compounds[parent["rank"]]
                signal_state = compound["signalGate"]
                accounting[new_id]["parentSignals"] += 1
                accounting[new_id][{"true": "gateTrueAdmitted", "false": "gateFalseFiltered", "unknown": "gateUnknownFiltered"}[signal_state]] += 1
                lineage = {"parentStrategyId": old_id, "sourceSignalKey": source_key,
                    "parentSignalSha256": digest(parent), "parentSignalsFileSha256": binding["document"]["parent"]["completedOutputs"]["signals"]["sha256"],
                    "pairedParentSignalKey": [PARENT_ID, paired["symbol"], day], "pairedParentRank": paired["rank"],
                    "strategyGate": compound, "strategyGateSha256": digest(compound)}
                runtime.write_record(writers["filterDecisions"], {"parentSignal": parent, **lineage,
                    "newStrategyId": new_id, "marketGate": state, "signalGate": signal_state,
                    "gateReasons": compound["unknownReasons"], "failedPredicates": compound["failedPredicates"],
                    "failedMarketPredicates": gate["failedPredicates"], "admitted": signal_state == "true"})
                if signal_state == "true":
                    admitted_today += 1
                    signal = {**deepcopy(parent), **lineage, "strategyId": new_id, "marketGate": state, "signalGate": signal_state}
                    if "pairedStrategyId" in signal:
                        signal["pairedStrategyId"] = CANDIDATE_ID
                    signals.append(signal)
                    runtime.write_record(writers["signals"], signal)
            runtime.write_record(writers["selections"], {**selection, "marketGate": state, "gateReasons": gate["unknownReasons"],
                "failedMarketPredicates": gate["failedPredicates"], "parentSelectionSha256": digest(selection),
                "strategyGates": strategy_gates, "unmatchedTrueStrategySlots": unmatched})
            runtime.write_record(writers["coverage"], {**coverage, "marketGate": state, "admittedSignals": admitted_today,
                "parentCoverageSha256": digest(coverage), "candidateGateCounts": {key: daily_candidates[key] for key in ("true", "false", "unknown")},
                "sourceRoleRows": len(role_day[1]), "unmatchedTrueStrategySlots": unmatched})
            # Current T roles are validated now but only become history at T+1.
            role_history.append({row["symbol"]: row for row in role_day[1]})
            if time.perf_counter() - last_notice >= 30:
                print(f"Market repair: {index + 1}/{len(binding['dates'])} sessions; {source_role_rows} source role rows checked", flush=True)
                last_notice = time.perf_counter()
        _end(market, "Market daily stream")
        _end(roles, "Market roles stream")
    fields = ("parentSignals", "gateTrueAdmitted", "gateFalseFiltered", "gateUnknownFiltered")
    return signals, {"scoringSeconds": time.perf_counter() - started, "scoredSessions": sum(gate_counts.values()),
        "marketGateCounts": {k: gate_counts[k] for k in ("true", "false", "unknown")},
        "candidateGateCounts": {k: candidate_counts[k] for k in ("true", "false", "unknown")},
        "sourceRoleRows": source_role_rows, "unmatchedTrueStrategySlots": unmatched_true,
        "filterAccounting": {g: {k: accounting[g][k] for k in fields} for g in GROUPS}}


def evaluate_signals(dataset, config, signals):
    result = _base_evaluate(dataset, deepcopy(config), signals)
    study = PathRiskStudy(dataset, config)
    paths = [study.evaluate(event) for event in result["events"] if event["entry"]["status"] == "filled"]
    result.update(pathRiskEvents=paths, pathRiskSummary=summarize_paths(paths, horizons=HORIZONS),
        actionEvidence="unverified", historicalEligibilityVerified=False, comparableRightsVerified=False,
        resultMeaning="raw-price-market-repair-resilience-development-diagnostic;not-economic-profit-or-goal-certification")
    for group in result["groups"]:
        group["role"] = "control" if group["strategyId"].startswith("control_") else "market-repair-resilient-price-leader-diagnostic"
        selected = [row for row in signals if row["strategyId"] == group["strategyId"]]
        group.update(distinctSignalSymbols=len({row["symbol"] for row in selected}),
            distinctSignalDates=len({row["signalDate"] for row in selected}), actionEvidence="unverified")
    return result


def implementation_hashes(binding):
    require(sha(Path(__file__)) == _LOADED_RUNNER_SHA and sha(ROOT / "service/signal_market_repair.py") == _LOADED_GATE_SHA,
        "New executable source changed after import")
    names = set(binding["document"]["parent"]["sourceImplementation"])
    names.update({Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(), REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix(),
        "service/signal_market_repair.py"})
    return {name: sha(_local_path(name)) for name in sorted(names)}


def _archive(path, implementation):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, expected in implementation.items():
            content = _local_path(name).read_bytes()
            require(hashlib.sha256(content).hexdigest() == expected, "Code changed before archive")
            archive.writestr(name, content)


def _safe_args(args, binding):
    require(args.diagnostic_unverified_history is True, "Explicit --diagnostic-unverified-history is required")
    workspace, output = Path(args.workspace).resolve(), Path(args.output).resolve()
    require(workspace.is_dir(), "Source workspace missing")
    require(workspace == Path(binding["parent_protocol"]["workspace"]).resolve(), "Use the frozen parent source workspace")
    protected = [workspace, (ROOT / binding["document"]["parent"]["completedArtifactDirectory"]).resolve(),
        (ROOT / binding["document"]["marketInput"]["directory"]).resolve(), binding["rawPaths"]["manifest.json"].parent]
    require(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in protected), "Output overlaps an immutable input directory")
    require(not any(Path(p).is_relative_to(output) for p in binding["files"]), "Output contains a bound source")
    return workspace, output


def _verify_protocol(output, binding, workspace):
    protocol = read_json(output / "protocol.json")
    require(protocol.get("schema") == "market-repair-development-v1" and
        protocol.get("protocolHash") == digest({k: v for k, v in protocol.items() if k != "protocolHash"}), "Protocol hash/schema changed")
    require(same(protocol.get("registration"), binding["document"]) and protocol.get("registrationSha256") == REGISTRATION_SHA256 and
        same(protocol.get("binding"), binding["identity"]) and same(protocol.get("config"), binding["parent_protocol"]["config"]) and
        protocol.get("workspace") == str(workspace) and protocol.get("strategyOrder") == [CANDIDATE_ID] and
        protocol.get("groups") == list(GROUPS) and protocol.get("certified") is False and protocol.get("targetAchieved") is False,
        "Frozen protocol identity/config changed")
    require(protocol["implementation"] == implementation_hashes(binding), "Execution source changed")
    require(sha(output / "execution-sources.zip") == protocol["executionArchiveSha256"], "Whole execution archive changed")
    _check_archive(output / "execution-sources.zip", protocol["implementation"])
    return protocol


def freeze(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    output.mkdir(parents=True, exist_ok=True)
    with source_tools.output_lock(output):
        if (output / "protocol.json").exists():
            return _verify_protocol(output, binding, workspace)
        require({p.name for p in output.iterdir()} == {".run.lock"}, "Unregistered output is not empty")
        implementation = implementation_hashes(binding)
        _archive(output / "execution-sources.zip", implementation)
        protocol = {"schema": "market-repair-development-v1", "createdAt": datetime.now(timezone.utc).isoformat(),
            "registration": binding["document"], "registrationSha256": REGISTRATION_SHA256, "binding": binding["identity"],
            "workspace": str(workspace), "config": deepcopy(binding["parent_protocol"]["config"]),
            "strategyOrder": [CANDIDATE_ID], "groups": list(GROUPS), "stage": "development", "pilot": False,
            "certified": False, "targetAchieved": False, "implementation": implementation, "pythonVersion": sys.version,
            "executionArchiveSha256": sha(output / "execution-sources.zip")}
        protocol["protocolHash"] = digest(protocol)
        # Inputs and executable bytes are checked again before the pre-run receipt.
        require(same(load_binding()["identity"], binding["identity"]) and implementation_hashes(binding) == implementation, "Input/code drift while freezing")
        write_json(output / "protocol.json", protocol)
        return protocol


def load_dataset(args, binding):
    sample = binding["document"]["sample"]
    selection = read_json(binding["parentPaths"]["selection.json"])
    dataset = source_tools.bound_snapshot(runtime.snapshot_universe(Path(args.workspace).resolve(), selection,
        sample["signalEnd"], sample["signalStart"]), sample["signalStart"], sample["signalEnd"])
    validate_dataset(dataset, binding)
    return dataset


def validate_dataset(dataset, binding):
    sample, inputs = binding["document"]["sample"], binding["manifest"]["inputs"]
    require(dataset["fingerprint"] == sample["expectedFullSnapshotFingerprint"] and
        digest(dataset) == inputs["fullDatasetContentSha256"] and
        sorted(s["symbol"] for s in dataset["securities"]) == binding["symbols"] and
        len(dataset["securities"]) == len(binding["symbols"]) and
        sum(len(s["bars"]) for s in dataset["securities"]) == sample["expectedFullBars"] and
        dataset["calendar"] == sorted(set(dataset["calendar"])) and
        all(d <= sample["signalEnd"] for d in dataset["calendar"]) and
        [d for d in dataset["calendar"] if sample["warmupStart"] <= d <= sample["signalEnd"]] == inputs["calendar"]["dates"] and
        all(b["date"] <= sample["signalEnd"] for s in dataset["securities"] for b in s["bars"]), "Frozen raw dataset differs")


def verify_completion(output, receipt, protocol):
    require(receipt.get("protocolHash") == protocol["protocolHash"] and receipt.get("snapshotFingerprint") ==
        protocol["binding"]["snapshotFingerprint"] and receipt.get("certified") is False and receipt.get("targetAchieved") is False and
        set(receipt.get("files", {})) == RECEIPT_KEYS, "Completed receipt identity/population changed")
    expected = {k: "attempts/attempt-0001/" + name for k, name in ATTEMPT_FILES.items()}
    expected.update(executionSources="execution-sources.zip", protocol="protocol.json")
    for key, name in expected.items():
        record = receipt["files"][key]
        require(record.get("path") == name and sha(output / name) == record.get("sha256"), "Completed artifact changed; refusing recompute")
    require(not (output / "attempts/attempt-0001/failure.json").exists(), "Completion and failure coexist")


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
            print("Completed market-repair study verified; no signals or outcomes recomputed", flush=True)
            return 0
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
                strategyOrder=[CANDIDATE_ID], stage="development", pilot=False, diagnosticUnverifiedHistory=True,
                certified=False, targetAchieved=False, filterAccounting=accounting,
                marketGateCounts=performance["marketGateCounts"], candidateGateCounts=performance["candidateGateCounts"],
                sourceRoleRows=performance["sourceRoleRows"], unmatchedTrueStrategySlots=performance["unmatchedTrueStrategySlots"],
                parentProtocolHash=binding["document"]["parent"]["protocolHash"])
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
                "certified": False, "targetAchieved": False,
                "files": {k: {"path": name, "sha256": sha(output / name)} for k, name in paths.items()}}
            write_json(receipt_path, receipt)
            source_tools.event(output, protocol["protocolHash"], "study_succeeded", receipt=receipt)
            return 0
        except Exception as exc:
            write_json(attempt / "failure.json", {"protocolHash": protocol["protocolHash"], "errorType": type(exc).__name__,
                "error": str(exc), "failedAt": datetime.now(timezone.utc).isoformat(), "automaticRetryAllowed": False})
            source_tools.event(output, protocol["protocolHash"], "study_failed", errorType=type(exc).__name__, error=str(exc))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
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
