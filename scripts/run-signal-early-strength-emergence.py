"""One frozen early-strength-emergence diagnostic; no economic certification.

The complete audited panel drives new episodes directly, without an old breakout
or peer filter. Top5 is chosen before the early-stage mask and independent active
policies. Frozen source snapshots and original event mathematics are unchanged.
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
from service.signal_early_strength_emergence import (
    CANDIDATE_ID, CONTROL_ID, VERSION,
    project_panel, EarlyStrengthEmergenceStudy,
)
from service.signal_path_risk import PathRiskStudy, summarize_paths

_spec = importlib.util.spec_from_file_location("_early_strength_emergence_events", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
REGISTRATION = ROOT / "docs/signal-early-strength-emergence-v1.json"
REGISTRATION_SHA256 = "2fa9f3164206b0b50a4468f4759be3ab283cf89579c777d6e5907fed23b3f356"
GROUPS = (CANDIDATE_ID, CONTROL_ID)
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
PREREQUISITES = {"scopedTests":"artifacts/strategy-search/early-strength-emergence-scoped-tests-v1.json",
    "prerunReview":"artifacts/strategy-search/early-strength-emergence-prerun-review-v1.json"}
REVIEWED_FILES = ("docs/signal-early-strength-emergence-v1.json",
    "service/signal_early_strength_emergence.py","scripts/run-signal-early-strength-emergence.py",
    "scripts/verify-signal-early-strength-emergence-result.py","tests/test_signal_early_strength_emergence.py",
    "tests/test_signal_early_strength_emergence_runner.py","tests/test_signal_early_strength_emergence_verifier.py")
runtime.STRATEGIES, runtime.GROUPS = (CANDIDATE_ID,), GROUPS
PANEL_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
               "summary.json", "execution-sources.zip", "attempt.json"}
ATTEMPT_FILES = {"projections":"projections.jsonl.gz", "predicates":"predicates.jsonl.gz",
    "formations":"formations.jsonl.gz", "transitions":"transitions.jsonl.gz",
    "episodeTerminals":"episode-terminals.jsonl.gz", "confirmations":"confirmations.jsonl.gz",
    "selections":"selection.jsonl.gz", "coverage":"coverage.jsonl.gz", "signals":"signals.jsonl.gz",
    "slotOutcomes":"slot-outcomes.jsonl.gz", "result":"result.json.gz", "summary":"summary.json",
    "attempt":"attempt.json"}
RECEIPT_KEYS = {*ATTEMPT_FILES, "protocol", "executionSources"}
AUDIT_REFERENCES = {
    "scripts/verify-signal-session-decomposition-result.py": "fd71659842d12ed716305b35cef7f23d8f6f42d82906d4faae5918636f62e10a",
    "scripts/verify-signal-market-repair-result.py": "922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf",
    "scripts/verify-signal-persistent-leader-result.py": "3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222"}
ADDITIONAL_IMPLEMENTATION = ("scripts/verify-signal-early-strength-emergence-result.py",
                             "tests/test_signal_early_strength_emergence_verifier.py")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


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
_LOADED_RULE_SHA = sha(ROOT / "service/signal_early_strength_emergence.py")
_LOADED_EVENT_SHA = sha(runtime.__file__)


def registration():
    require(REGISTRATION_SHA256 is not None and sha(REGISTRATION) == REGISTRATION_SHA256,
            "Registration is not frozen or has changed")
    doc = read_json(REGISTRATION)
    require(doc.get("candidateId") == CANDIDATE_ID and doc.get("controlId") == CONTROL_ID and doc.get("registeredAt"),
            "Registered candidate/control identity differs")
    require(doc.get("certified") is False and doc.get("targetAchieved") is False and
            doc["audit"]["horizons"] == list(HORIZONS),
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
        require(path.is_absolute() and path.is_file(), "Physical source missing: " + name)
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
                row = json.loads(line, object_pairs_hook=_object,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: "+value)))
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


def collect_signals(dataset, binding, attempt):
    """Consume every frozen feature row once; no parent candidate or Q filter."""
    validate_dataset(dataset, binding)
    securities={s["symbol"]:s for s in dataset["securities"]}
    securities["sh000300"]=dataset["benchmark"]
    bars={symbol:{b["date"]:b for b in security["bars"]} for symbol,security in securities.items()}
    calendar=dataset["calendar"]; indices={day:i for i,day in enumerate(calendar)}
    def raw(symbol,day):
        bar=bars[symbol].get(day)
        status=securities[symbol].get("tradeStatusByDate",{}).get(day)
        halted=status is False or type(status) is int and status==0 or status=="0"
        trading=status is True or type(status) is int and status==1 or status=="1"
        return {**bar,"priceBasisId":"raw-unadjusted","knownSuspended":halted,
            "tradeStatusState":"halt" if halted else "trading" if trading else "unknown"} if bar is not None else None
    start=indices[binding["dates"][0]]
    windows={symbol:deque((raw(symbol,d) for d in calendar[max(0,start-5):start]),maxlen=6)
             for symbol in (*binding["symbols"],"sh000300")}
    study=EarlyStrengthEmergenceStudy(binding["dates"],binding["symbols"],formation_calendar=calendar)
    signals=[]; counts=Counter({k:0 for k in ("formations","transitions","confirmations","sourceFeatureRows",
        "scoredSessions","globalTop5Slots","selectionEligibleConfirmations")})
    formation_states=Counter(); masks=Counter(); reasons=Counter()
    started=notice=time.perf_counter()
    keys=("projections","predicates","formations","transitions","episodeTerminals","confirmations",
          "selections","coverage","signals")
    with ExitStack() as stack:
        writers={key:stack.enter_context(gzip.open(attempt/ATTEMPT_FILES[key],"xt",encoding="utf-8",compresslevel=1)) for key in keys}
        for index,(day,rows) in enumerate(iter_panel_days(binding)):
            absolute=indices[day]; calendar61=calendar[max(0,absolute-60):absolute+1]
            source={"panelManifestSha256":binding["identity"]["panelManifestSha256"],
                "featureFileSha256":binding["document"]["sourceBinding"]["files"]["features.jsonl.gz"],
                "calendarHash":binding["identity"]["calendarHash"],"targetIndex":index}
            projected={}
            for row in rows:
                symbol=row["symbol"]
                windows[symbol].append(raw(symbol,day))
                projected[symbol]=project_panel(symbol,day,calendar61,row)
                runtime.write_record(writers["projections"],{"date":day,"symbol":symbol,
                    "source":{**source,"sourceRowSha256":digest(row),"sourceHashes":row["sourceHashes"]},
                    "projection":projected[symbol]})
            windows["sh000300"].append(raw("sh000300",day))
            result=study.step(day,index,projected,{s:list(windows[s]) for s in binding["symbols"]},list(windows["sh000300"]))
            require([r["symbol"] for r in result["evaluations"]]==binding["symbols"] and
                    all(r["date"]==day for r in result["evaluations"]),"Pure evaluation population/order differs")
            for row in result["evaluations"]:
                runtime.write_record(writers["predicates"],{"date":day,"symbol":row["symbol"],"source":source,"record":row})
            for key,field in (("formations","formations"),("transitions","transitions"),("confirmations","rawConfirmations")):
                for row in result[field]:
                    runtime.write_record(writers[key],{"date":day,"source":source,"record":row})
                    counts[key]+=1
            selection=result["selection"]
            require(selection["date"]==day and same(selection["signals"],result["signals"]),"Selection signal identity differs")
            runtime.write_record(writers["selections"],{"date":day,"source":source,"selection":selection})
            for signal in result["signals"]:
                require(signal["strategyId"] in GROUPS and signal["signalDate"]==day,"Selected signal identity differs")
                signals.append(signal);runtime.write_record(writers["signals"],signal)
            daily_reasons=Counter(row["crossingReason"] for row in result["evaluations"])
            daily_masks=Counter(row["episode"]["earlyMask"] for row in result["formations"] if row["episode"] is not None)
            daily_states=Counter(row["kind"] for row in result["formations"])
            raw_eligible=sum(r["selectionEligible"] is True for r in result["rawConfirmations"])
            require(raw_eligible==len(selection["fullRanking"]),"Confirmation ranking denominator differs")
            runtime.write_record(writers["coverage"],{"date":day,"targetIndex":index,"source":source,
                "sourceFeatureRows":len(rows),"evaluationReasonCounts":dict(daily_reasons),
                "formations":len(result["formations"]),"formationKinds":dict(daily_states),
                "activatedEarlyMaskCounts":{v:daily_masks[v] for v in ("true","false","unknown")},
                "transitions":len(result["transitions"]),"rawConfirmations":len(result["rawConfirmations"]),
                "selectionEligibleConfirmations":raw_eligible,"globalTop5Slots":len(selection["slots"]),
                "selectedSignals":{g:sum(s["strategyId"]==g for s in result["signals"]) for g in GROUPS},
                "episodeCounts":result["episodeCounts"],"certified":False,"targetAchieved":False})
            counts.update(sourceFeatureRows=len(rows),scoredSessions=1,globalTop5Slots=len(selection["slots"]),
                          selectionEligibleConfirmations=raw_eligible)
            reasons.update(daily_reasons);formation_states.update(daily_states);masks.update(daily_masks)
            if time.perf_counter()-notice>=30:
                print(f'Projected {index+1}/{len(binding["dates"])} days; {counts["sourceFeatureRows"]} rows; diagnostic only',flush=True)
                notice=time.perf_counter()
        terminal=study.finish(cutoff=binding["dates"][-1])
        for row in terminal["transitions"]:
            runtime.write_record(writers["transitions"],{"date":binding["dates"][-1],"source":None,"phase":"cutoff","record":row})
            counts["transitions"]+=1
        for row in terminal["episodes"]: runtime.write_record(writers["episodeTerminals"],row)
    require(counts["sourceFeatureRows"]==len(binding["dates"])*len(binding["symbols"]),"Full panel population differs")
    accounting={"formations":counts["formations"],"formationKinds":dict(formation_states),
        "activatedEarlyMaskCounts":{s:masks[s] for s in ("true","false","unknown")},
        "episodes":len(terminal["episodes"]),"suppressedByLock":formation_states["formationSuppressedByLock"],"terminalStateCounts":dict(Counter(e["state"] for e in terminal["episodes"])),
        "rawConfirmations":counts["confirmations"],"selectionEligibleConfirmations":counts["selectionEligibleConfirmations"],
        "globalTop5Slots":counts["globalTop5Slots"],"cutoffEpisodeCounts":terminal["episodeCounts"]}
    return signals,{"scoringSeconds":time.perf_counter()-started,**dict(counts),"episodeAccounting":accounting,
        "evaluationReasonCounts":dict(reasons),"selectedSignals":{g:sum(s["strategyId"]==g for s in signals) for g in GROUPS},
        "sourceFeatureMathematicsRecomputed":False,"parentCandidateStreamUsed":False}


def evaluate_signals(dataset, config, signals):
    result=runtime.evaluate_signals(dataset,deepcopy(config),signals)
    study=PathRiskStudy(dataset,config)
    paths=[study.evaluate(event) for event in result["events"] if event["entry"]["status"]=="filled"]
    result.update(pathRiskEvents=paths,pathRiskSummary=summarize_paths(paths,horizons=HORIZONS),
        actionEvidence="unverified",comparableRightsVerified=False,historicalEligibilityVerified=False,
        resultMeaning="raw-price/early-strength-emergence;registered-legacy-cost-opening-proxy;not-economic-profit-certification")
    for group in result["groups"]:
        selected=[s for s in signals if s["strategyId"]==group["strategyId"]]
        group.update(role="all-common-confirmation-shadow" if group["strategyId"]==CONTROL_ID else "early-mask-confirmation",
            distinctSignalSymbols=len({s["symbol"] for s in selected}),distinctSignalDates=len({s["signalDate"] for s in selected}))
    return result


def _records(path):
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line,object_pairs_hook=_object,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: "+value)))


def slot_dispositions(selections, signals, result):
    identity=lambda r:(r["strategyId"],r["symbol"],r["signalDate"])
    selected={identity(r):r for r in signals}; events={identity(r):r for r in result["events"]}
    suppressed={identity(r):r for r in result["overlapSuppressed"]}
    require(len(selected)==len(signals) and len(events)==len(result["events"]) and
        len(suppressed)==len(result["overlapSuppressed"]) and not set(events)&set(suppressed) and
        set(selected)==set(events)|set(suppressed),"Every selected signal needs exactly one disposition")
    accounted=set();rows=[]
    def side(sid,slot):
        if sid==CANDIDATE_ID and not slot["candidateAdmitted"]:
            return {"status":"filteredEarlyUnknown" if slot["earlyMask"]=="unknown" else "filteredEarlyFalse",
                "eventId":None,"entryStatus":None}
        key=(sid,slot["symbol"],slot["date"])
        require(key in selected and key not in accounted,"Slot missing, duplicated or maps to another signal")
        signal=selected[key]
        require(all(signal[k]==slot[k] for k in ("episodeId","formationDate","earlyMask","rank")),"Slot lineage differs")
        accounted.add(key)
        if key in suppressed:
            event=suppressed[key]
            return {"status":"activeSuppressed","eventId":None,"entryStatus":None,"reason":event["reason"],
                "blockedByEventId":event["blockedByEventId"],"activeUntil":event["activeUntil"]}
        event=events[key]
        return {"status":"attempted","eventId":event["eventId"],"entryStatus":event["entry"]["status"],
            "entryReason":event["entry"].get("reason"),"entryDate":event["entry"].get("date"),
            "targetDate":event["outcomes"]["nextOpenNet30"]["hitDate"],"terminal":deepcopy(event["terminal"])}
    for wrapper in selections:
        for slot in wrapper["selection"]["slots"]:
            require(slot["date"]==wrapper["date"] and slot["baselineAdmitted"] is True and
                slot["candidateAdmitted"] is (slot["earlyMask"]=="true"),"Original slot policy differs")
            rows.append({**slot,"candidateDisposition":side(CANDIDATE_ID,slot),"baselineDisposition":side(CONTROL_ID,slot),
                "interpretation":"original-common-Top5;two-independent-active-policies;not-randomized-or-fill-matched"})
    require(accounted==set(selected),"Original slots must cover every selected signal")
    summary={"globalTop5Slots":len(rows),"candidateAdmitted":sum(r["candidateAdmitted"] for r in rows),
        "baselineAdmitted":len(rows),"earlyMaskCounts":{s:sum(r["earlyMask"]==s for r in rows) for s in ("true","false","unknown")}}
    for name in ("candidate","baseline"):
        sides=[r[name+"Disposition"] for r in rows]
        summary[name+"Dispositions"]=dict(Counter(r["status"] for r in sides))
        summary[name+"EntryStatuses"]=dict(Counter(r["entryStatus"] for r in sides if r["entryStatus"] is not None))
    return rows,summary



def implementation_hashes(binding):
    require(sha(__file__) == _LOADED_RUNNER_SHA and sha(ROOT/"service/signal_early_strength_emergence.py") == _LOADED_RULE_SHA and
            sha(ROOT/"scripts/run-signal-target-study.py") == _LOADED_EVENT_SHA, "Imported execution source changed")
    paths = {Path(__file__), REGISTRATION, ROOT/"service/signal_early_strength_emergence.py",
        ROOT/"tests/test_signal_early_strength_emergence_runner.py", ROOT/"tests/test_signal_early_strength_emergence.py",
        ROOT/"scripts/run-signal-target-study.py", ROOT/"scripts/run-strategy-search.py", ROOT/"service/signal_target_study.py",
        ROOT/"service/signal_path_risk.py", ROOT/"service/research_target.py", ROOT/"service/strategies.py",
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


def load_prerequisites():
    files={name:sha(_local(name)) for name in REVIEWED_FILES}
    paths={key:_local(name) for key,name in PREREQUISITES.items()}
    scoped,review=(read_json(paths[k]) for k in ("scopedTests","prerunReview"))
    require(scoped.get("schema")=="early-strength-emergence-scoped-tests-v1" and scoped.get("passed") is True and
        type(scoped.get("testsRun")) is int and scoped["testsRun"]>0 and
        all(type(scoped.get(k)) is int and scoped[k]==0 for k in ("failures","errors","skipped")) and
        isinstance(scoped.get("uniqueTestIds"),list) and all(isinstance(x,str) and x for x in scoped["uniqueTestIds"]) and
        len(scoped["uniqueTestIds"])==len(set(scoped["uniqueTestIds"]))==scoped["testsRun"] and
        scoped.get("sourcesStableDuringTests") is True and same(scoped.get("files"),files) and
        scoped.get("certified") is False and scoped.get("targetAchieved") is False,"Scoped synthetic prerequisite differs")
    require(review.get("schema")=="early-strength-emergence-prerun-review-v1" and review.get("passed") is True and
        type(review.get("failureCount")) is int and review["failureCount"]==0 and
        review.get("scopedTestsSha256")==sha(paths["scopedTests"]) and same(review.get("files"),files) and
        review.get("certified") is False and review.get("targetAchieved") is False,"Pre-run review prerequisite differs")
    return {key:{"path":PREREQUISITES[key],"sha256":sha(path)} for key,path in paths.items()}


def _verify_protocol(output, binding, workspace):
    protocol = read_json(output/"protocol.json")
    require(protocol.get("schema") == "early-strength-emergence-development-v1" and
        protocol.get("protocolHash") == digest({k:v for k,v in protocol.items() if k != "protocolHash"}), "Protocol schema/hash changed")
    require(same(protocol.get("registration"), binding["document"]) and protocol.get("registrationSha256") == REGISTRATION_SHA256 and
        same(protocol.get("binding"), binding["identity"]) and same(protocol.get("config"), binding["document"]["config"]) and
        protocol.get("workspace") == str(workspace) and protocol.get("strategyOrder") == [CANDIDATE_ID] and
        protocol.get("groups") == list(GROUPS) and same(protocol.get("prerequisites"),load_prerequisites()) and protocol.get("certified") is False and protocol.get("targetAchieved") is False,
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
    prerequisites=load_prerequisites()
    output.mkdir(parents=True, exist_ok=True)
    with source_tools.output_lock(output):
        require({p.name for p in output.iterdir()} == {".run.lock"}, "Exclusive freeze requires a new empty output")
        write_json(output/"freeze-attempt.json", {"candidateId":CANDIDATE_ID,"registrationSha256":REGISTRATION_SHA256,
                                                "startedAt":_now(),"certified":False,"targetAchieved":False})
        try:
            implementation = implementation_hashes(binding)
            _archive(output/"execution-sources.zip", implementation)
            protocol = {"schema":"early-strength-emergence-development-v1","createdAt":_now(),
                "registration":binding["document"],"registrationSha256":REGISTRATION_SHA256,"binding":binding["identity"],
                "workspace":str(workspace),"config":deepcopy(binding["document"]["config"]),
                "strategyOrder":[CANDIDATE_ID],"groups":list(GROUPS),"implementation":implementation,"pythonVersion":sys.version,
                "prerequisites":prerequisites,
                "stage":"development-diagnostic","certified":False,"targetAchieved":False,
                "executionArchiveSha256":sha(output/"execution-sources.zip"),"freezeAttemptSha256":sha(output/"freeze-attempt.json")}
            protocol["protocolHash"] = digest(protocol)
            require(same(load_binding()["identity"], binding["identity"]) and implementation_hashes(binding) == implementation and
                    same(load_prerequisites(),prerequisites),
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
    journal = [json.loads(line, object_pairs_hook=_object,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: "+value))) for line in (output/"events.jsonl").read_text(encoding="utf-8").splitlines()]
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
            require(accounting["globalTop5Slots"] == performance["globalTop5Slots"], "Slot denominator accounting differs")
            with gzip.open(attempt/"slot-outcomes.jsonl.gz","xt",encoding="utf-8",compresslevel=1) as stream:
                for row in slot_rows:
                    runtime.write_record(stream,row)
            result.update(protocolHash=protocol["protocolHash"],snapshotFingerprint=dataset["fingerprint"],
                strategyOrder=[CANDIDATE_ID],stage="development-diagnostic",pilot=False,diagnosticUnverifiedHistory=True,
                episodeAccounting=performance["episodeAccounting"],slotAccounting=accounting,
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
    parser.add_argument("--output",type=Path,default=ROOT/"artifacts/strategy-search/signal-early-strength-emergence-v1")
    parser.add_argument("--diagnostic-unverified-history",action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze-only",action="store_true")
    mode.add_argument("--run-once",action="store_true")
    args = parser.parse_args(argv)
    if args.freeze_only:
        print(json.dumps({"protocolHash":freeze(args)["protocolHash"]}),flush=True)
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
