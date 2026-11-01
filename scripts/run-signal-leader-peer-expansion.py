"""One frozen temporal leader / fixed peer-expansion diagnostic; never certification.

Signals use archived leadership at F and T, then fixed F peer membership.
The complete temporal-leader Top5 is a nested baseline, not a matched control.
The unchanged event runtime supplies independent active state, costs and targets.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import ExitStack, closing
from copy import deepcopy
from datetime import date, datetime, timezone
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
from service.signal_leader_peer_expansion import (
    CANDIDATE_ID, CONTROL_ID, VERSION, HISTORY_SLOTS,
    form_peer_group, evaluate_peer_expansion, select_peer_signals,
)
from service.signal_persistent_leader import (THRESHOLDS, evaluate_persistent_leader, rank_persistent_leader_signals)
from service.signal_path_risk import PathRiskStudy, summarize_paths

_spec = importlib.util.spec_from_file_location("_leader_peer_expansion_events", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
REGISTRATION = ROOT / "docs/signal-leader-peer-expansion-v1.json"
REGISTRATION_SHA256 = "80a781ced9fb746677a76f554e065be650a88edbe6f56f96bd522a45a77e873f"
GROUPS = (CANDIDATE_ID, CONTROL_ID)
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
runtime.STRATEGIES, runtime.GROUPS = (CANDIDATE_ID,), GROUPS
PANEL_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
               "summary.json", "execution-sources.zip", "attempt.json"}
ATTEMPT_FILES = {"predicates": "predicates.jsonl.gz", "candidates": "candidates.jsonl.gz",
    "coverage": "coverage.jsonl.gz", "selections": "selection.jsonl.gz", "peerGroups": "peer-groups.jsonl.gz",
    "peerExpansion": "peer-expansion.jsonl.gz",
    "signals": "signals.jsonl.gz", "slotOutcomes": "slot-outcomes.jsonl.gz", "result": "result.json.gz",
    "summary": "summary.json", "attempt": "attempt.json"}
RECEIPT_KEYS = {*ATTEMPT_FILES, "protocol", "executionSources"}
KINDS = ("ineligible", "unevaluable", "notTriggered", "signaled")
EXPANSION_KINDS = ("expanded", "notExpanded", "unevaluable")
AUDIT_REFERENCES = {
    "scripts/verify-signal-market-repair-result.py": "922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf",
    "scripts/verify-signal-persistent-leader-result.py": "3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222",
    "scripts/verify-signal-session-decomposition-result.py": "fd71659842d12ed716305b35cef7f23d8f6f42d82906d4faae5918636f62e10a"}
ADDITIONAL_IMPLEMENTATION = ("scripts/verify-signal-leader-peer-expansion-result.py",
                             "tests/test_signal_leader_peer_expansion_verifier.py")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: " + value)))


def same(left, right):
    return digest(left) == digest(right)


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        stream.write("\n")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _local(name):
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file(), "Missing or escaping bound file: " + str(name))
    return path


_LOADED_RUNNER_SHA = sha(__file__)
_LOADED_RULE_SHA = sha(ROOT / "service/signal_leader_peer_expansion.py")
_LOADED_EVENT_SHA = sha(runtime.__file__)
_LOADED_PARENT_SHA = sha(ROOT / "service/signal_persistent_leader.py")


def registration():
    require(REGISTRATION_SHA256 is not None and sha(REGISTRATION) == REGISTRATION_SHA256,
            "Registration is not frozen or has changed")
    doc = read_json(REGISTRATION)
    require(doc.get("candidateId") == CANDIDATE_ID and doc.get("controlId") == CONTROL_ID and doc.get("registeredAt"),
            "Registered candidate/control identity differs")
    require(doc.get("certified") is False and doc.get("targetAchieved") is False and
            doc["rule"]["historySlots"] == HISTORY_SLOTS and doc["audit"]["horizons"] == list(HORIZONS),
            "Registered diagnostic flags/window/horizons differ")
    config = doc["config"]
    require(same(normalize_config(config), config), "Execution config must be the registered normalized object")
    require((config["startDate"], config["endDate"]) == (doc["sample"]["signalStart"], doc["sample"]["signalEnd"]) and
            config["universe"]["strictHistory"] is False and config["topN"] == 5 and
            all(config[k] == 0 for k in ("maxHoldingDays", "stopLossPct", "takeProfitPct", "trailingStopPct")),
            "Registered event interval, eligibility or no-expiry policy differs")
    return doc


def _check_archive(path, hashes):
    with zipfile.ZipFile(path) as archive:
        require(len(archive.namelist()) == len(hashes) and set(archive.namelist()) == set(hashes), "Archive member closure differs")
        for name, expected in hashes.items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected, "Archive member changed: " + name)


def load_binding():
    """Metadata and hashes only; no candidate, feature or market-row scan."""
    doc = registration()
    requested, sample = doc["sourceBinding"], doc["sample"]
    directory = (ROOT / requested["panelDirectory"]).resolve()
    require(set(requested["files"]) == PANEL_FILES | {"completion.json", "independent-verification.json"}, "Panel file binding is incomplete")
    hashes = {name: value for name, value in requested["files"].items() if name != "completion.json"}
    require(set(hashes) == PANEL_FILES | {"independent-verification.json"}, "Panel file binding is incomplete")
    all_hashes = dict(requested["files"])
    require({p.name for p in directory.iterdir()} == set(all_hashes), "Panel inventory differs")
    files = {}
    for name, expected in all_hashes.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and sha(path) == expected, "Panel hash differs: " + name)
        files[str(path)] = expected
    manifest, completion, audit = (read_json(directory / name) for name in
        ("manifest.json", "completion.json", "independent-verification.json"))
    inputs = manifest["inputs"]
    symbols, calendar = inputs["symbols"], inputs["calendar"]["dates"]
    dates = [d for d in calendar if sample["signalStart"] <= d <= sample["signalEnd"]]
    require(manifest.get("featureVersion") == "daily-leadership-raw-v1" and
            completion.get("sampleManifestHash") == hashes["manifest.json"] == audit.get("sampleManifestHash") and
            audit.get("passed") is True and type(audit.get("failureCount")) is int and audit["failureCount"] == 0,
            "Actual panel audit is not accepted")
    require(symbols == sorted(set(symbols)) and len(symbols) == sample["expectedFullSecurities"] and
            inputs["symbolCount"] == len(symbols) and inputs["symbolsSha256"] == digest(symbols) and
            set(inputs["securities"]) == set(symbols) and calendar == sorted(set(calendar)) and
            all(date.fromisoformat(d).isoformat() == d and sample["warmupStart"] <= d <= sample["signalEnd"] for d in calendar) and
            inputs["calendar"]["sha256"] == digest(calendar) and len(dates) == sample["expectedScoredSessions"] and
            inputs["snapshotFingerprint"] == sample["expectedFullSnapshotFingerprint"] and
            inputs["retainedBarCount"] == sample["expectedFullBars"], "Panel sample/calendar differs")
    count = len(symbols) * len(dates)
    require(completion.get("featureRows") == count == audit.get("featureRows") and
            completion.get("targetSessions") == len(dates) and set(completion.get("files", {})) == PANEL_FILES,
            "Panel completion population differs")
    for name in PANEL_FILES:
        require(completion["files"][name] == {"path": name, "sha256": hashes[name]}, "Panel receipt differs")
    require(manifest.get("executionArchiveSha256") == hashes["execution-sources.zip"], "Panel archive whole hash differs")
    _check_archive(directory / "execution-sources.zip", manifest["implementation"])
    physical = manifest.get("physicalSourceHashes", {})
    require(bool(physical), "Physical source binding missing")
    for name, expected in physical.items():
        path = Path(name)
        require(path.is_absolute() and path.is_file() and sha(path) == expected, "Physical source changed: " + name)
        files[str(path.resolve())] = expected
    source_binding = doc["sourceBinding"]
    workspace = (ROOT / source_binding["workspace"]).resolve()
    require((ROOT / source_binding["panelDirectory"]).resolve() == directory and
            same(source_binding["files"], all_hashes), "Source/panel bindings disagree")
    require(str(workspace / "market.sqlite3") in files, "Workspace database is not source-bound")
    require(same(physical_source_hashes(workspace, sample), physical), "Physical source inventory differs")
    files[str(REGISTRATION.resolve())] = REGISTRATION_SHA256
    identity = {"registrationSha256": REGISTRATION_SHA256, "inputFilesSha256": files,
        "panelManifestSha256": hashes["manifest.json"], "panelCompletionSha256": all_hashes["completion.json"],
        "actualPanelAuditSha256": hashes["independent-verification.json"],
        "snapshotFingerprint": inputs["snapshotFingerprint"], "fullDatasetContentSha256": inputs["fullDatasetContentSha256"],
        "symbolsSha256": inputs["symbolsSha256"], "calendarHash": inputs["calendar"]["sha256"]}
    return {"document": doc, "sample": sample, "directory": directory, "manifest": manifest,
        "completion": completion, "audit": audit, "workspace": workspace, "symbols": symbols,
        "calendar": calendar, "dates": dates, "files": files, "identity": identity}


def physical_source_hashes(workspace, sample):
    """Mirror only source inventory, including optional files the loader reads."""
    with closing(sqlite3.connect((workspace/"market.sqlite3").as_uri()+"?mode=ro", uri=True)) as db:
        root = source_tools.tdx.normalize_directory(json.loads(db.execute("SELECT value FROM meta WHERE key='path'").fetchone()[0]))
    paths = {workspace/"market.sqlite3"}
    for name in ("market.sqlite3-wal", "research-history.sqlite3", "research-history.sqlite3-wal"):
        if (workspace/name).is_file():
            paths.add(workspace/name)
    for market in sample["selection"]["markets"]:
        for path in (root/"vipdoc"/market/"lday").glob("*.day"):
            stem = path.stem.lower()
            symbol = market+stem if len(stem) == 6 and stem.isdigit() else stem
            try:
                if source_tools.tdx.symbol_parts(symbol)[0] == market and source_tools.board_of(symbol) == "main":
                    paths.add(source_tools.tdx.within(path, root))
            except ValueError:
                continue
    benchmark = source_tools.tdx.daily_path(root, sample["selection"]["benchmarkSymbol"])
    if benchmark.is_file():
        paths.add(benchmark)
    for market in source_tools.tdx.MARKETS:
        path = root/"T0002/hq_cache"/(market+"s.tnf")
        if path.is_file():
            paths.add(path)
    return {str(path.resolve()): sha(path) for path in sorted(paths)}


def iter_panel_days(binding):
    inputs = binding["manifest"]["inputs"]
    with gzip.open(binding["directory"] / "features.jsonl.gz", "rt", encoding="utf-8") as stream:
        for day in binding["dates"]:
            rows = []
            for symbol in binding["symbols"]:
                line = stream.readline()
                require(bool(line), "Panel ended before full population")
                row = json.loads(line, object_pairs_hook=_object)
                expected = {**inputs["securities"][symbol], "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}
                require(row.get("symbol") == symbol and row.get("asOfDate") == day and
                    row.get("featureVersion") == "daily-leadership-raw-v1" and
                    row.get("sampleManifestHash") == binding["identity"]["panelManifestSha256"] and
                    row.get("calendarHash") == inputs["calendar"]["sha256"] and same(row.get("sourceHashes"), expected),
                    "Panel row order, identity or evidence binding differs")
                rows.append(row)
            yield day, rows
        require(not stream.readline(), "Panel has trailing rows outside registered population")


def validate_dataset(dataset, binding):
    sample, inputs = binding["sample"], binding["manifest"]["inputs"]
    require(dataset["fingerprint"] == sample["expectedFullSnapshotFingerprint"] and
        digest(dataset) == inputs["fullDatasetContentSha256"] and
        sorted(s["symbol"] for s in dataset["securities"]) == binding["symbols"] and
        len(dataset["securities"]) == len(binding["symbols"]) and
        sum(len(s["bars"]) for s in dataset["securities"]) == sample["expectedFullBars"] and
        dataset["calendar"] == sorted(set(dataset["calendar"])) and
        all(d <= sample["signalEnd"] for d in dataset["calendar"]) and
        [d for d in dataset["calendar"] if sample["warmupStart"] <= d <= sample["signalEnd"]] == binding["calendar"] and
        all(b["date"] <= sample["signalEnd"] for s in dataset["securities"] for b in s["bars"]) and
        all(b["date"] <= sample["signalEnd"] for b in dataset["benchmark"]["bars"]), "Frozen raw dataset differs")


def load_dataset(args, binding):
    sample = binding["sample"]
    dataset = source_tools.bound_snapshot(runtime.snapshot_universe(Path(args.workspace).resolve(), sample["selection"],
        sample["signalEnd"], sample["signalStart"]), sample["signalStart"], sample["signalEnd"])
    validate_dataset(dataset, binding)
    return dataset


def _halted(status):
    return status is False or type(status) is int and status == 0 or status == "0"


def _parent_summary(outcome):
    known = outcome["kind"] in ("signaled", "notTriggered")
    five = {name: outcome["predicates"][name]["passed"] for name, _, _ in THRESHOLDS}
    return {"date": outcome["date"], "kind": outcome["kind"], "reason": outcome["reason"],
        "fiveThresholdsPass": five, "strong": known and all(value is True for value in five.values()),
        "outcomeSha256": digest({k:v for k,v in outcome.items() if k != "signal"})}


def collect_signals(dataset, binding, attempt):
    """Advance every fixed symbol once per target day; no future panel rows.

    F qualification reuses the exact parent outcome calculated and logged at F.
    Its hash points to that full predicate record, never to a later reconstruction.
    Raw and panel windows are independent of holdings and event results.
    """
    validate_dataset(dataset, binding)
    securities = {s["symbol"]: s for s in dataset["securities"]}
    all_sources = {**securities, "__benchmark__": dataset["benchmark"]}
    bars = {symbol: {b["date"]: b for b in security["bars"]} for symbol, security in all_sources.items()}
    calendar, symbols = dataset["calendar"], binding["symbols"]
    indices = {day: i for i, day in enumerate(calendar)}
    start_index = indices[binding["dates"][0]]
    def raw(symbol, day):
        bar = bars[symbol].get(day)
        status = all_sources[symbol].get("tradeStatusByDate", {}).get(day)
        return {**bar, "priceBasisId": "raw-unadjusted", "knownSuspended": _halted(status)} if bar is not None else None
    windows = {symbol: deque((raw(symbol, d) for d in calendar[max(0, start_index-HISTORY_SLOTS+1):start_index]),
                             maxlen=HISTORY_SLOTS) for symbol in all_sources}
    panel_ring, parent_ring = deque(maxlen=11), deque(maxlen=10)
    signals, current_totals, temporal_totals, expansion_totals = [], Counter(), Counter(), Counter()
    parent_candidates = parent_top5 = source_peer_records = 0
    started = last_notice = time.perf_counter()
    writer_keys = ("predicates", "candidates", "coverage", "selections", "peerGroups", "peerExpansion", "signals")
    with ExitStack() as stack:
        writers = {key: stack.enter_context(gzip.open(attempt / ATTEMPT_FILES[key], "xt", encoding="utf-8", compresslevel=1))
                   for key in writer_keys}
        for ordinal, (day, rows) in enumerate(iter_panel_days(binding), 1):
            index = indices[day]
            slots = calendar[max(0, index-HISTORY_SLOTS+1):index+1]
            for symbol in all_sources:
                windows[symbol].append(raw(symbol, day))
            raw_windows = {symbol: list(window) for symbol, window in windows.items()}
            panel_ring.append((day, {row["symbol"]: row for row in rows}))
            formation_day = calendar[index-10] if index >= 10 else None
            formation_available = len(panel_ring) == 11
            formation_rows = panel_ring[0][1] if formation_available else None
            formation_parents = parent_ring[0][1] if formation_available else None
            if formation_available:
                require(panel_ring[0][0] == parent_ring[0][0] == formation_day, "Formation panel is not exactly T minus ten sessions")
            current_parents, raw_candidates, kinds, temporal = {}, [], Counter(), Counter()
            for row in rows:
                symbol = row["symbol"]
                outcome = evaluate_persistent_leader(symbol, day, slots[-61:], raw_windows[symbol][-61:], row)
                require(outcome["kind"] in KINDS and outcome["symbol"] == symbol and outcome["date"] == day,
                        "Parent returned unknown identity/classification")
                current_parents[symbol] = _parent_summary(outcome)
                past = formation_parents[symbol] if formation_available else {
                    "date": formation_day, "kind": "unevaluable", "reason": "formationPanelUnavailable",
                    "fiveThresholdsPass": {name: None for name, _, _ in THRESHOLDS}, "strong": None, "outcomeSha256": None}
                if not formation_available:
                    classification = "formationUnavailable"
                elif outcome["kind"] != "signaled":
                    classification = "currentRejected"
                elif past["strong"] is not True:
                    classification = "formationRejected"
                else:
                    classification = "temporalCandidate"
                    candidate = deepcopy(outcome["signal"])
                    candidate.update(formationDate=formation_day, formationParent=deepcopy(past), eligible=True)
                    raw_candidates.append(candidate)
                kinds[outcome["kind"]] += 1
                temporal[classification] += 1
                record = {"version": VERSION, "symbol": symbol, "date": day,
                    "currentParent": {k:v for k,v in outcome.items() if k != "signal"},
                    "formationParent": past, "temporalClassification": classification,
                    "panelManifestSha256": binding["identity"]["panelManifestSha256"],
                    "sourceHashes": row["sourceHashes"], "calendarHash": row["calendarHash"], "certified": False}
                runtime.write_record(writers["predicates"], record)
            parent_ring.append((day, current_parents))
            ranked = rank_persistent_leader_signals(raw_candidates)
            for item in ranked:
                runtime.write_record(writers["candidates"], item)
            top5 = [item for item in ranked if item["selectedTop5"]]
            outcomes, group_hashes, member_sets = {}, {}, {}
            daily_expansion = Counter()
            if top5:
                require(formation_available and len(slots) == HISTORY_SLOTS, "Selected parent has no full causal history")
                formation_raw = {symbol: raw_windows[symbol][:-10] for symbol in symbols}
                expansion_raw = {symbol: raw_windows[symbol][-11:] for symbol in symbols}
                eligibility = {symbol: formation_rows[symbol].get("baseEligibility") for symbol in symbols}
                for item in top5:
                    symbol = item["symbol"]
                    group = form_peer_group(symbol, formation_day, slots[:-10], formation_raw,
                                            raw_windows["__benchmark__"][:-10], eligibility)
                    group_hash = digest(group)
                    group_hashes[symbol] = group_hash
                    member_sets[symbol] = set(group["members"])
                    source_peer_records += len(group["candidateEvaluations"])
                    runtime.write_record(writers["peerGroups"], {"date": day, "symbol": symbol, "rank": item["rank"],
                        "formationDate": formation_day, "groupSha256": group_hash, "group": group})
                    expansion = evaluate_peer_expansion(group, slots[-11:], expansion_raw, raw_windows["__benchmark__"][-11:])
                    require(expansion["kind"] in EXPANSION_KINDS, "Unexpected expansion classification")
                    outcomes[symbol] = expansion
                    daily_expansion[expansion["kind"]] += 1
                    runtime.write_record(writers["peerExpansion"], {"date": day, "symbol": symbol, "rank": item["rank"],
                        "formationDate": formation_day, "groupSha256": group_hash, "expansion": expansion})
                    if time.perf_counter()-last_notice >= 30:
                        print(f"Evaluated {ordinal}/{len(binding['dates'])} days, peer anchor {item['rank']}/{len(top5)}; diagnostic only", flush=True)
                        last_notice = time.perf_counter()
            selected = select_peer_signals(day, top5, outcomes)
            # A stable complete slot ledger allows independent joins after the two active policies diverge.
            selected_slots = [{"date": day, "symbol": item["symbol"], "rank": item["rank"],
                "formationDate": item["formationDate"], "groupSha256": group_hashes[item["symbol"]],
                "expansionKind": outcomes[item["symbol"]]["kind"], "expansionReason": outcomes[item["symbol"]]["reason"],
                "candidateAdmitted": outcomes[item["symbol"]]["kind"] == "expanded", "baselineAdmitted": True}
                for item in top5]
            overlaps = [{"left": a["symbol"], "right": b["symbol"],
                "sharedMembers": sorted(member_sets[a["symbol"]] & member_sets[b["symbol"]]),
                "sharedCount": len(member_sets[a["symbol"]] & member_sets[b["symbol"]])}
                for i,a in enumerate(top5) for b in top5[i+1:]]
            runtime.write_record(writers["selections"], {"date": day, "selection": selected, "slots": selected_slots,
                "membershipOverlap": overlaps, "activeExclusionApplied": False, "noBackfill": True})
            expected = {(CONTROL_ID, item["symbol"]) for item in top5} | {
                (CANDIDATE_ID, item["symbol"]) for item in top5 if outcomes[item["symbol"]]["kind"] == "expanded"}
            actual = {(signal["strategyId"], signal["symbol"]) for signal in selected["signals"]}
            require(actual == expected and len(actual) == len(selected["signals"]), "Nested selection changed original Top5 slots")
            for signal in selected["signals"]:
                require(signal["signalDate"] == day and signal["rank"] == next(i["rank"] for i in top5 if i["symbol"] == signal["symbol"]),
                        "Selected signal date or original rank changed")
                signals.append(signal)
                runtime.write_record(writers["signals"], signal)
            require(sum(kinds.values()) == sum(temporal.values()) == len(symbols), "Full symbol/date classification count differs")
            runtime.write_record(writers["coverage"], {"date": day, "evaluatedSecurities": len(rows),
                "currentParentCounts": {key: kinds[key] for key in KINDS}, "temporalCounts": dict(temporal),
                "parentCandidates": len(ranked), "parentTop5": len(top5),
                "expansionCounts": {key: daily_expansion[key] for key in EXPANSION_KINDS},
                "candidateSignals": daily_expansion["expanded"], "baselineSignals": len(top5), "certified": False})
            current_totals.update(kinds); temporal_totals.update(temporal); expansion_totals.update(daily_expansion)
            parent_candidates += len(ranked); parent_top5 += len(top5)
            if time.perf_counter()-last_notice >= 30:
                print(f"Evaluated {ordinal}/{len(binding['dates'])} leader/peer days; diagnostic only", flush=True)
                last_notice = time.perf_counter()
    require(sum(current_totals.values()) == len(symbols)*len(binding["dates"]), "Full panel population differs")
    return signals, {"scoringSeconds": time.perf_counter()-started, "scoredSessions": len(binding["dates"]),
        "evaluatedSecuritySessions": sum(current_totals.values()), "currentParentCounts": {key: current_totals[key] for key in KINDS},
        "temporalCounts": dict(temporal_totals), "parentCandidates": parent_candidates, "parentTop5": parent_top5,
        "expansionCounts": {key: expansion_totals[key] for key in EXPANSION_KINDS},
        "sourcePeerCandidateRecords": source_peer_records, "candidateSignalCount": expansion_totals["expanded"],
        "controlSignalCount": parent_top5}


def evaluate_signals(dataset, config, signals):
    result = runtime.evaluate_signals(dataset, deepcopy(config), signals)
    study = PathRiskStudy(dataset, config)
    paths = [study.evaluate(event) for event in result["events"] if event["entry"]["status"] == "filled"]
    result.update(pathRiskEvents=paths, pathRiskSummary=summarize_paths(paths, horizons=HORIZONS),
        actionEvidence="unverified", comparableRightsVerified=False, historicalEligibilityVerified=False,
        resultMeaning="raw-price/leader-peer-expansion;nested-baseline;registered-cost-opening-proxy;not-economic-profit-certification")
    for group in result["groups"]:
        group.pop("pairedStrategyId", None)
        selected = [s for s in signals if s["strategyId"] == group["strategyId"]]
        group.update(role="temporal-leader-only-nested-baseline" if group["strategyId"] == CONTROL_ID else "peer-expansion-filtered-leader",
                     distinctSignalSymbols=len({s["symbol"] for s in selected}),
                     distinctSignalDates=len({s["signalDate"] for s in selected}), actionEvidence="unverified")
    return result


def _records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line, object_pairs_hook=_object)


def slot_dispositions(selections, signals, result):
    """Every original Top5 slot retains both separate policy dispositions."""
    key = lambda row: (row["strategyId"], row["symbol"], row["signalDate"])
    selected = {key(row): row for row in signals}
    events = {key(row): row for row in result["events"]}
    suppressed = {key(row): row for row in result["overlapSuppressed"]}
    require(len(selected) == len(signals) and len(events) == len(result["events"]) and
            len(suppressed) == len(result["overlapSuppressed"]) and not set(events) & set(suppressed) and
            set(selected) == set(events) | set(suppressed), "Every selected signal must have one active disposition")
    accounted, rows = set(), []
    def side(sid, slot):
        identity = (sid, slot["symbol"], slot["date"])
        if sid == CANDIDATE_ID and not slot["candidateAdmitted"]:
            require(identity not in selected, "Filtered candidate was selected")
            return {"status": "filteredUnknown" if slot["expansionKind"] == "unevaluable" else "filteredNotExpanded",
                    "entryStatus": None, "eventId": None}
        require(identity in selected and identity not in accounted, "Missing or repeated original slot")
        accounted.add(identity)
        if identity in suppressed:
            row = suppressed[identity]
            return {"status": "activeSuppressed", "eventId": None, "entryStatus": None,
                    "reason": row["reason"], "blockedByEventId": row["blockedByEventId"], "activeUntil": row["activeUntil"]}
        event = events[identity]
        return {"status": "attempted", "eventId": event["eventId"], "entryStatus": event["entry"]["status"],
            "entryReason": event["entry"].get("reason"), "entryDate": event["entry"].get("date"),
            "targetDate": event["outcomes"]["nextOpenNet30"]["hitDate"], "terminal": deepcopy(event["terminal"])}
    for selection in selections:
        for slot in selection["slots"]:
            require(slot["date"] == selection["date"] and slot["baselineAdmitted"] is True and
                    slot["candidateAdmitted"] == (slot["expansionKind"] == "expanded"), "Original slot gate differs")
            rows.append({**slot, "candidateDisposition": side(CANDIDATE_ID, slot),
                "baselineDisposition": side(CONTROL_ID, slot),
                "interpretation": "nested-intentions;separate-active-state;not-matched-or-independent-observations"})
    require(accounted == set(selected), "Original slots must account for all selected signals")
    summary = {"parentTop5": len(rows), "candidateAdmitted": sum(row["candidateAdmitted"] for row in rows),
        "baselineAdmitted": len(rows), "expansionCounts": dict(Counter(row["expansionKind"] for row in rows))}
    for name in ("candidate", "baseline"):
        sides = [row[name+"Disposition"] for row in rows]
        summary[name+"Dispositions"] = dict(Counter(row["status"] for row in sides))
        summary[name+"EntryStatuses"] = dict(Counter(row["entryStatus"] for row in sides if row["entryStatus"] is not None))
    return rows, summary


def implementation_hashes(binding):
    require(sha(__file__) == _LOADED_RUNNER_SHA and sha(ROOT/"service/signal_leader_peer_expansion.py") == _LOADED_RULE_SHA and
            sha(ROOT/"scripts/run-signal-target-study.py") == _LOADED_EVENT_SHA and
            sha(ROOT/"service/signal_persistent_leader.py") == _LOADED_PARENT_SHA, "Imported execution source changed")
    paths = {Path(__file__), REGISTRATION, ROOT/"service/signal_leader_peer_expansion.py",
        ROOT/"tests/test_signal_leader_peer_expansion_runner.py", ROOT/"tests/test_signal_leader_peer_expansion.py",
        ROOT/"scripts/run-signal-target-study.py", ROOT/"scripts/run-strategy-search.py", ROOT/"service/signal_target_study.py",
        ROOT/"service/signal_persistent_leader.py", ROOT/"service/signal_path_risk.py", ROOT/"service/research_target.py", ROOT/"service/strategies.py",
        ROOT/"service/tdx.py", ROOT/"service/execution_evidence.py", *ROOT.glob("service/portfolio_*.py")}
    doc = binding["document"]
    for name, expected in AUDIT_REFERENCES.items():
        require(sha(_local(name)) == expected, "Frozen independent audit changed: " + name)
        paths.add(_local(name))
    paths.update(_local(name) for name in ADDITIONAL_IMPLEMENTATION)
    for name, expected in doc.get("sourceImplementation", {}).items():
        require(sha(_local(name)) == expected, "Frozen inherited dependency changed: " + name)
        paths.add(_local(name))
    for name in doc.get("implementationFreeze", {}).get("files", []):
        paths.add(_local(name))
    return {path.resolve().relative_to(ROOT.resolve()).as_posix(): sha(path) for path in sorted(paths)}


def _archive(path, hashes):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, expected in hashes.items():
            raw = _local(name).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == expected, "Code changed during archive")
            archive.writestr(name, raw)


def _safe_args(args, binding):
    workspace, output = Path(args.workspace).resolve(), Path(args.output).resolve()
    require(args.diagnostic_unverified_history is True, "Explicit --diagnostic-unverified-history required")
    require(workspace == binding["workspace"], "Use exactly the registered source workspace")
    with closing(sqlite3.connect((workspace/"market.sqlite3").as_uri()+"?mode=ro", uri=True)) as db:
        source_root = Path(json.loads(db.execute("SELECT value FROM meta WHERE key='path'").fetchone()[0])).resolve()
    protected = (workspace, source_root, binding["directory"])
    require(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in protected), "Output overlaps immutable input")
    require(all(not Path(name).is_relative_to(output) for name in binding["files"]) and
            all(not _local(name).is_relative_to(output) for name in implementation_hashes(binding)), "Output contains bound inputs/code")
    return workspace, output


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
        attempts, attempt = output/"attempts", output/"attempts/attempt-0001"
        require([p.name for p in attempts.iterdir()] == ["attempt-0001"] and
                {p.name for p in attempt.iterdir()} == set(ATTEMPT_FILES.values()) | {"completion.json"}, "Completed inventory differs")
        for path in (attempts, attempt, *attempt.iterdir()):
            require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Attempt links forbidden")


def _verify_protocol(output, binding, workspace):
    protocol = read_json(output/"protocol.json")
    require(protocol.get("schema") == "leader-peer-expansion-development-v1" and
        protocol.get("protocolHash") == digest({k:v for k,v in protocol.items() if k != "protocolHash"}), "Protocol schema/hash changed")
    require(same(protocol.get("registration"), binding["document"]) and protocol.get("registrationSha256") == REGISTRATION_SHA256 and
        same(protocol.get("binding"), binding["identity"]) and same(protocol.get("config"), binding["document"]["config"]) and
        protocol.get("workspace") == str(workspace) and protocol.get("strategyOrder") == [CANDIDATE_ID] and
        protocol.get("groups") == list(GROUPS) and protocol.get("certified") is False and protocol.get("targetAchieved") is False,
        "Frozen protocol identity/config changed")
    require(protocol["implementation"] == implementation_hashes(binding) and
            sha(output/"execution-sources.zip") == protocol["executionArchiveSha256"], "Execution source/archive drift")
    _check_archive(output/"execution-sources.zip", protocol["implementation"])
    marker = read_json(output/"freeze-attempt.json")
    require(sha(output/"freeze-attempt.json") == protocol["freezeAttemptSha256"] and
            marker.get("registrationSha256") == REGISTRATION_SHA256 and marker.get("candidateId") == CANDIDATE_ID,
            "Freeze marker differs")
    return protocol


def _fail(output, phase, error):
    write_json(output/"failure.json", {"phase":phase,"errorType":type(error).__name__,"error":str(error),
                                      "failedAt":_now(),"automaticRetryAllowed":False})


def freeze(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    output.mkdir(parents=True, exist_ok=True)
    with source_tools.output_lock(output):
        require({p.name for p in output.iterdir()} == {".run.lock"}, "Exclusive freeze requires a new empty output")
        write_json(output/"freeze-attempt.json", {"candidateId":CANDIDATE_ID,"registrationSha256":REGISTRATION_SHA256,
                                                "startedAt":_now(),"certified":False,"targetAchieved":False})
        try:
            implementation = implementation_hashes(binding)
            _archive(output/"execution-sources.zip", implementation)
            protocol = {"schema":"leader-peer-expansion-development-v1","createdAt":_now(),
                "registration":binding["document"],"registrationSha256":REGISTRATION_SHA256,"binding":binding["identity"],
                "workspace":str(workspace),"config":deepcopy(binding["document"]["config"]),
                "strategyOrder":[CANDIDATE_ID],"groups":list(GROUPS),"implementation":implementation,"pythonVersion":sys.version,
                "stage":"development-diagnostic","certified":False,"targetAchieved":False,
                "executionArchiveSha256":sha(output/"execution-sources.zip"),"freezeAttemptSha256":sha(output/"freeze-attempt.json")}
            protocol["protocolHash"] = digest(protocol)
            require(same(load_binding()["identity"], binding["identity"]) and implementation_hashes(binding) == implementation,
                    "Input/code drift during freeze")
            write_json(output/"protocol.json", protocol)
            return protocol
        except Exception as error:
            _fail(output, "freeze", error)
            raise


def verify_completion(output, receipt, protocol):
    _inventory(output, completed=True)
    require(receipt.get("protocolHash") == protocol["protocolHash"] and receipt.get("attempt") == 1 and
        receipt.get("snapshotFingerprint") == protocol["binding"]["snapshotFingerprint"] and
        receipt.get("certified") is False and receipt.get("targetAchieved") is False and
        receipt.get("freezeAttemptSha256") == protocol["freezeAttemptSha256"] and set(receipt.get("files", {})) == RECEIPT_KEYS,
        "Completed receipt differs")
    paths = {k:"attempts/attempt-0001/"+name for k,name in ATTEMPT_FILES.items()}
    paths.update(protocol="protocol.json", executionSources="execution-sources.zip")
    for key, name in paths.items():
        require(receipt["files"][key] == {"path":name,"sha256":sha(output/name)}, "Completed artifact changed; no recompute")
    marker = read_json(output/"attempts/attempt-0001/attempt.json")
    require(marker.get("attempt") == 1 and marker.get("protocolHash") == protocol["protocolHash"] and
            marker.get("candidateId") == CANDIDATE_ID, "Attempt marker differs")
    journal = [json.loads(line, object_pairs_hook=_object) for line in (output/"events.jsonl").read_text(encoding="utf-8").splitlines()]
    require([r.get("event") for r in journal] == ["study_started","scoring_completed","study_succeeded"] and
            all(r.get("protocolHash") == protocol["protocolHash"] for r in journal) and journal[-1].get("receipt") == receipt,
            "Completion journal differs")


def execute(args):
    binding = load_binding()
    workspace, output = _safe_args(args, binding)
    require((output/"protocol.json").is_file(), "Freeze and review before execution")
    with source_tools.output_lock(output):
        protocol = _verify_protocol(output,binding,workspace)
        protocol_sha = sha(output/"protocol.json")
        attempt = output/"attempts/attempt-0001"
        if (attempt/"completion.json").exists():
            verify_completion(output,read_json(attempt/"completion.json"),protocol)
            return 0
        _inventory(output)
        require(not (output/"attempts").exists(), "Failed/interrupted attempt retained; no automatic retry")
        attempt.mkdir(parents=True)
        write_json(attempt/"attempt.json", {"attempt":1,"candidateId":CANDIDATE_ID,"protocolHash":protocol["protocolHash"],
                                            "startedAt":_now(),"certified":False,"targetAchieved":False})
        source_tools.event(output,protocol["protocolHash"],"study_started",attempt=1)
        try:
            dataset = load_dataset(args,binding)
            signals, performance = collect_signals(dataset,binding,attempt)
            source_tools.event(output,protocol["protocolHash"],"scoring_completed",**performance)
            tick = time.perf_counter()
            result = evaluate_signals(dataset,protocol["config"],signals)
            performance["eventEvaluationSeconds"] = time.perf_counter()-tick
            slot_rows, accounting = slot_dispositions(_records(attempt/"selection.jsonl.gz"),signals,result)
            require(accounting["parentTop5"] == performance["parentTop5"] and
                    accounting["candidateAdmitted"] == performance["candidateSignalCount"], "Slot denominator accounting differs")
            with gzip.open(attempt/"slot-outcomes.jsonl.gz","xt",encoding="utf-8",compresslevel=1) as stream:
                for row in slot_rows:
                    runtime.write_record(stream,row)
            result.update(protocolHash=protocol["protocolHash"],snapshotFingerprint=dataset["fingerprint"],
                strategyOrder=[CANDIDATE_ID],stage="development-diagnostic",pilot=False,diagnosticUnverifiedHistory=True,
                currentParentCounts=performance["currentParentCounts"],temporalCounts=performance["temporalCounts"],
                expansionCounts=performance["expansionCounts"],slotAccounting=accounting,
                certified=False,targetAchieved=False)
            for group in result["groups"]:
                require(group["rawSelectedSignals"] == group["signalCount"]+group["overlapSuppressedCount"], "Active counts differ")
            with gzip.open(attempt/"result.json.gz","xt",encoding="utf-8",compresslevel=1) as stream:
                json.dump(result,stream,ensure_ascii=False,allow_nan=False,separators=(",",":"))
            summary = {k:v for k,v in result.items() if k not in ("events","overlapSuppressed","pathRiskEvents")}
            summary.update(performance=performance,ranking=None,selectionEligible=False)
            write_json(attempt/"summary.json",summary)
            final = load_binding()
            require(same(final["identity"],binding["identity"]) and sha(output/"protocol.json") == protocol_sha, "Post-run input/protocol drift")
            _verify_protocol(output,final,workspace)
            validate_dataset(dataset,final)
            paths = {k:"attempts/attempt-0001/"+name for k,name in ATTEMPT_FILES.items()}
            paths.update(protocol="protocol.json",executionSources="execution-sources.zip")
            receipt = {"protocolHash":protocol["protocolHash"],"snapshotFingerprint":dataset["fingerprint"],"attempt":1,
                "freezeAttemptSha256":protocol["freezeAttemptSha256"],"certified":False,"targetAchieved":False,
                "files":{k:{"path":name,"sha256":sha(output/name)} for k,name in paths.items()}}
            write_json(attempt/"completion.json",receipt)
            source_tools.event(output,protocol["protocolHash"],"study_succeeded",receipt=receipt)
            return 0
        except Exception as error:
            _fail(output,"execute",error)
            write_json(attempt/"failure.json",{"protocolHash":protocol["protocolHash"],"errorType":type(error).__name__,
                "error":str(error),"failedAt":_now(),"automaticRetryAllowed":False})
            source_tools.event(output,protocol["protocolHash"],"study_failed",errorType=type(error).__name__,error=str(error))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",type=Path,default=ROOT/"artifacts/strategy-search/source-workspace")
    parser.add_argument("--output",type=Path,default=ROOT/"artifacts/strategy-search/signal-leader-peer-expansion-v1")
    parser.add_argument("--diagnostic-unverified-history",action="store_true")
    parser.add_argument("--freeze-only",action="store_true")
    args = parser.parse_args(argv)
    if args.freeze_only:
        print(json.dumps({"protocolHash":freeze(args)["protocolHash"]}),flush=True)
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
