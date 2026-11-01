"""Three preregistered development trials; never a strategy or goal acceptance test.

The input workspace is read only. All writes belong to a separate output directory.
Successful trials are immutable; resuming checks the source fingerprint and artifacts.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import ctypes
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service import tdx
from service.portfolio_config import normalize_config
from service.portfolio_engine import PortfolioEngine, ENGINE_VERSION, RULES_VERSION, _digest
from service.portfolio_prepared import PreparedCache
from service.portfolio_signals import SCORER_VERSION
from service.portfolio_universe import snapshot_universe, board_of, _retain_causal_window
from service.research_target import summarize_result

STRATEGIES = ("momentum", "trendBreakout", "trendPullback")
PILOT_SEED = "a-trade-short-swing-performance-pilot-v1"
SCHEMA = "strategy-development-search-v1"


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def peak_memory_mib():
    if os.name != "nt":
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024 ** 2 if sys.platform == "darwin" else 1024)
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("faults", ctypes.c_uint32)] + [
            (name, ctypes.c_size_t) for name in ("peak", "working", "pagedPeak", "paged", "nonpagedPeak", "nonpaged", "pagefile", "pagefilePeak")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_uint32]
    return counters.peak / 1024 ** 2 if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb) else None


def write_json(path, value, *, replace=False):
    path = Path(path)
    if path.exists() and not replace:
        raise ValueError(f"Refusing to overwrite {path.name}")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def output_lock(output):
    path = output / ".run.lock"
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError("Output is locked; verify the prior process has stopped before removing .run.lock") from None
    try:
        with handle:
            json.dump({"pid": os.getpid(), "createdAt": datetime.now(timezone.utc).isoformat()}, handle)
        yield
    finally:
        path.unlink()


def event(output, protocol_hash, name, **fields):
    row = {"event": name, "at": datetime.now(timezone.utc).isoformat(), "protocolHash": protocol_hash, **fields}
    with (output / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def discover_symbols(workspace, source_id=None):
    """Only source identities, before reading bars; never replace unreadable picks."""
    if source_id:
        path = workspace / "portfolio-history.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT data FROM packages WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise ValueError("Historical source does not exist")
        header = json.loads(zlib.decompress(row[0]))
        storage = header.get("_storage", {})
        if storage.get("version") != "sharded-v1":
            raise ValueError("Historical pilot requires a sharded source manifest; do not inspect future bars to select symbols")
        symbols = storage["manifest"]["securities"]
    else:
        path = workspace / "market.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
        if not row:
            raise ValueError("Local market source is not configured")
        root = tdx.normalize_directory(json.loads(row[0]))
        symbols = set()
        for market in ("sh", "sz"):
            folder = tdx.within(root / "vipdoc" / market / "lday", root)
            if not folder.is_dir():
                raise ValueError(f"Missing source directory for {market}")
            for path in folder.glob("*.day"):
                stem = path.stem.lower()
                symbol = market + stem if re.fullmatch(r"\d{6}", stem) else stem
                if re.fullmatch(r"(?:sh|sz)\d{6}", symbol) and symbol[:2] == market:
                    symbols.add(symbol)
    return sorted(s for s in symbols if s[:2] in ("sh", "sz") and board_of(s) == "main")


def pilot_symbols(symbols, maximum):
    return sorted(sorted(set(symbols), key=lambda s: (hashlib.sha256((PILOT_SEED + ":" + s).encode()).hexdigest(), s))[:maximum])


def bound_snapshot(dataset, start, end):
    """Also bounds legacy imported snapshots, which can retain later source rows."""
    securities = []
    for security in dataset["securities"]:
        security["bars"] = [row for row in security["bars"] if row["date"] <= end]
        if not security["bars"]:
            continue
        if not security.get("historyWindow"):
            _retain_causal_window(security, start)
        first = security["bars"][0]["date"]
        for key, values in list(security.items()):
            if (key.endswith("ByDate") or key in ("stHistory", "memberHistory")) and isinstance(values, dict):
                security[key] = {day: value for day, value in values.items() if first <= day <= end}
        if "corporateActions" in security:
            security["corporateActions"] = [a for a in security["corporateActions"] if a.get("exDate", "9999") <= end]
        securities.append(security)
    if not securities:
        raise ValueError("No securities observed by the registered cutoff")
    if any(s["symbol"][:2] not in ("sh", "sz") or board_of(s["symbol"]) != "main" for s in securities):
        raise ValueError("Snapshot returned securities outside the registered SH/SZ main-board universe")
    dataset["securities"] = securities
    first = min(s["bars"][0]["date"] for s in securities)
    dataset["calendar"] = [day for day in dataset.get("calendar", []) if first <= day <= end]
    if not any(start <= day <= end for day in dataset["calendar"]):
        raise ValueError("No trading sessions in the registered interval")
    if isinstance(dataset.get("membershipByDate"), dict):
        dataset["membershipByDate"] = {day: members for day, members in dataset["membershipByDate"].items() if first <= day <= end}
    if isinstance(dataset.get("benchmark"), dict):
        dataset["benchmark"]["bars"] = [row for row in dataset["benchmark"].get("bars", []) if first <= row["date"] <= end]
    if isinstance(dataset.get("historyCoverage"), dict):
        dataset["historyCoverage"]["endDate"] = min(end, dataset["historyCoverage"]["endDate"])
    dataset.update(startDate=start, endDate=end)
    dataset.pop("fingerprint", None)
    dataset["fingerprint"] = _digest(dataset)
    return dataset


def make_protocol(args):
    trials = []
    for strategy in STRATEGIES:
        config = normalize_config({
            "startDate": args.start, "endDate": args.end, "initialCapital": 1000000,
            "strategies": [{"id": strategy, "kind": strategy, "pool": "base"}],
            "allocation": {"mode": "single", "strategyIds": [strategy]}, "combinations": [],
            "weighting": "equal", "rebalanceMode": "risk", "topN": 5, "maxPositions": 5,
            "maxStockWeight": .1, "maxHoldingDays": 9, "stopLossPct": .05,
            "takeProfitPct": 0, "trailingStopPct": 0,
            "universe": {"strictHistory": not args.diagnostic_unverified_history, "excludeSt": True,
                         "minListingDays": 120, "minAmount20": 50000000},
        })
        trials.append({"id": strategy, "config": config})
    return {"schema": SCHEMA, "stage": "development", "workspace": str(args.workspace),
            "startDate": args.start, "endDate": args.end, "maxSymbols": args.max_symbols,
            "diagnosticUnverifiedHistory": args.diagnostic_unverified_history,
            "source": "history" if args.history_source_id else "local", "sourceId": args.history_source_id,
            "benchmarkSymbol": None if args.history_source_id else "sh000300",
            "markets": ["sh", "sz"], "boards": ["main"], "pilotSeed": PILOT_SEED,
            "pilotSelection": "source-symbol-sha256-before-bars;no-replacement-for-missing-history",
            "pilotMeaning": "performance-only;not-strategy-selection-or-acceptance",
            "successMeaning": "trial-execution-only;never-investment-goal-acceptance",
            "sampleUse": "all-inspected-dates-are-development;no-independent-lockbox-claim",
            "accountMode": "one-continuous-shared-cash-ledger-per-trial;no-fold-resets",
            "holdingRule": "exit-queued-at-ninth-held-session-close;earliest-next-session-open;restrictions-can-delay",
            "exposureNote": "five-positions-times-ten-percent-cap;unused-budget-remains-cash",
            "versions": {"engine": ENGINE_VERSION, "rules": RULES_VERSION, "scorer": SCORER_VERSION},
            "implementation": {str(path.relative_to(ROOT)): file_hash(path) for path in sorted({Path(__file__), *ROOT.glob("service/portfolio_*.py"), ROOT / "service/strategies.py", ROOT / "service/research_target.py", ROOT / "service/execution_evidence.py"})},
            "trials": trials}


def verify_receipt(output, receipt, protocol_hash, fingerprint):
    if receipt.get("trialId") not in STRATEGIES:
        raise ValueError("Completed trial is not registered in this protocol")
    if receipt.get("protocolHash") != protocol_hash or receipt.get("snapshotFingerprint") != fingerprint:
        raise ValueError("Completed trial identity mismatch")
    for key in ("result", "summary"):
        path = (output / receipt[key]["path"]).resolve()
        if not path.is_relative_to(output) or not path.is_file() or file_hash(path) != receipt[key]["sha256"]:
            raise ValueError(f"Successful trial artifact missing or changed: {key}; refusing to rerun")


def completed_trials(output, protocol_hash, fingerprint):
    completed = {}
    events_path = output / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("protocolHash") != protocol_hash:
                raise ValueError("Event log belongs to another protocol")
            if row["event"] == "trial_succeeded":
                receipt = row["receipt"]
                verify_receipt(output, receipt, protocol_hash, fingerprint)
                if receipt["trialId"] in completed:
                    raise ValueError("Duplicate successful trial in the append-only log")
                completed[receipt["trialId"]] = receipt
    for path in sorted((output / "trials").glob("*/attempt-*/completion.json")):
        receipt = read_json(path)
        verify_receipt(output, receipt, protocol_hash, fingerprint)
        previous = completed.get(receipt["trialId"])
        if previous and previous != receipt:
            raise ValueError("Multiple completed attempts for one registered trial")
        if not previous:
            event(output, protocol_hash, "trial_succeeded", receipt=receipt, recovered=True)
            completed[receipt["trialId"]] = receipt
    return completed


def execute(args):
    args.workspace, args.output = Path(args.workspace).resolve(), Path(args.output).resolve()
    if not args.workspace.is_dir():
        raise ValueError("Input workspace does not exist")
    if args.output.is_relative_to(args.workspace):
        raise ValueError("Output must be outside the read-only input workspace")
    for value in (args.start, args.end):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Dates must use YYYY-MM-DD")
    if args.start > args.end:
        raise ValueError("Start date must not follow end date")
    if args.max_symbols is not None and not 1 <= args.max_symbols <= 9000:
        raise ValueError("--max-symbols must be between 1 and 9000")
    if args.history_source_id and not re.fullmatch(r"[a-f0-9]{64}", args.history_source_id):
        raise ValueError("--history-source-id must be a source SHA-256 identifier")
    args.output.mkdir(parents=True, exist_ok=True)
    with output_lock(args.output):
        protocol = make_protocol(args)
        protocol_hash = _digest(protocol)
        path = args.output / "protocol.json"
        if path.exists():
            if read_json(path) != protocol:
                raise ValueError("Existing protocol differs; use a new study directory, never overwrite it")
        else:
            if any(args.output.iterdir()) and set(p.name for p in args.output.iterdir()) != {".run.lock"}:
                raise ValueError("Unregistered output is not empty")
            write_json(path, protocol)
            event(args.output, protocol_hash, "protocol_registered")
        selection = {"mode": "all", "markets": ["sh", "sz"], "boards": ["main"],
                     "source": protocol["source"], "sourceId": args.history_source_id,
                     "benchmarkSymbol": protocol["benchmarkSymbol"]}
        if args.max_symbols is not None:
            selected = pilot_symbols(discover_symbols(args.workspace, args.history_source_id), args.max_symbols)
            selection.update(mode="explicit", symbols=selected)
        else:
            selected = None
        selection_path = args.output / "selection.json"
        if selection_path.exists():
            if read_json(selection_path) != selection:
                raise ValueError("Registered source symbol selection changed; refusing a replacement pilot")
        else:
            write_json(selection_path, selection)
        event(args.output, protocol_hash, "snapshot_started")
        tick = time.perf_counter()
        try:
            dataset = bound_snapshot(snapshot_universe(args.workspace, selection, args.end, args.start), args.start, args.end)
        except Exception as exc:
            event(args.output, protocol_hash, "snapshot_failed", errorType=type(exc).__name__, error=str(exc))
            raise
        snapshot_seconds = time.perf_counter() - tick
        manifest = {"fingerprint": dataset["fingerprint"], "symbols": sorted(s["symbol"] for s in dataset["securities"]),
                    "requestedPilotSymbols": selected, "barCount": sum(len(s["bars"]) for s in dataset["securities"]),
                    "startDate": args.start, "endDate": args.end, "quality": dataset.get("dataQuality", {}),
                    "coverage": dataset.get("universeCoverage", {})}
        manifest_path = args.output / "snapshot.json"
        if manifest_path.exists():
            if read_json(manifest_path) != manifest:
                raise ValueError("Frozen snapshot fingerprint or coverage changed; refusing to resume")
        else:
            write_json(manifest_path, manifest)
        event(args.output, protocol_hash, "snapshot_ready", fingerprint=dataset["fingerprint"], seconds=snapshot_seconds,
              symbolCount=len(dataset["securities"]), barCount=manifest["barCount"], peakWorkingSetMiB=peak_memory_mib())
        completed = completed_trials(args.output, protocol_hash, dataset["fingerprint"])
        cache = PreparedCache()
        failures = []
        for trial in protocol["trials"]:
            trial_id = trial["id"]
            if trial_id in completed:
                print(f"{trial_id}: verified existing completion; skipped", flush=True)
                continue
            folder = args.output / "trials" / trial_id
            folder.mkdir(parents=True, exist_ok=True)
            attempt_number = 1 + max((int(p.name.split("-")[-1]) for p in folder.glob("attempt-*") if p.is_dir()), default=0)
            attempt = folder / f"attempt-{attempt_number:04d}"
            attempt.mkdir()
            event(args.output, protocol_hash, "trial_started", trialId=trial_id, attempt=attempt_number)
            started = time.perf_counter()
            engine = result = None
            try:
                engine = cache.create(PortfolioEngine, dataset, trial["config"])
                initialized = time.perf_counter()
                last_notice = initialized
                chunks = []
                while True:
                    chunk_start = time.perf_counter()
                    done = engine.step(5)
                    chunks.append(time.perf_counter() - chunk_start)
                    if done:
                        break
                    if time.perf_counter() - last_notice >= 30:
                        print(f"{trial_id}: {engine.progress['completedDays']}/{engine.progress['totalDays']} sessions", flush=True)
                        last_notice = time.perf_counter()
                simulated = time.perf_counter()
                result = engine.result()
                reported = time.perf_counter()
                summary = summarize_result(result, stage="development", pilot=args.max_symbols is not None)
                summary.update(trialId=trial_id, stage="development", pilot=args.max_symbols is not None,
                               diagnosticUnverifiedHistory=args.diagnostic_unverified_history,
                               protocolHash=protocol_hash, snapshotFingerprint=dataset["fingerprint"],
                               executionStatus="completed", selectionEligible=False,
                               performance={"initializationSeconds": initialized - started,
                                            "simulationSeconds": simulated - initialized,
                                            "reportSeconds": reported - simulated})
                summary.setdefault("goalAssessment", {})["targetAchieved"] = False
                result_path = attempt / "result.json.gz"
                with gzip.open(result_path, "xt", encoding="utf-8") as stream:
                    json.dump(result, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                summary["performance"]["serializationSeconds"] = time.perf_counter() - reported
                summary["performance"].update(peakWorkingSetMiB=peak_memory_mib(),
                                               peakMemoryScope="whole-process-high-water-mark",
                                               fiveDayChunkMaxSeconds=max(chunks),
                                               fiveDayChunkMeanSeconds=sum(chunks) / len(chunks))
                summary_path = attempt / "summary.json"
                write_json(summary_path, summary)
                receipt = {"trialId": trial_id, "attempt": attempt_number, "protocolHash": protocol_hash,
                           "snapshotFingerprint": dataset["fingerprint"],
                           "result": {"path": result_path.relative_to(args.output).as_posix(), "sha256": file_hash(result_path)},
                           "summary": {"path": summary_path.relative_to(args.output).as_posix(), "sha256": file_hash(summary_path)}}
                write_json(attempt / "completion.json", receipt)
                event(args.output, protocol_hash, "trial_succeeded", receipt=receipt)
                completed[trial_id] = receipt
                print(f"{trial_id}: development trial completed in {time.perf_counter() - started:.2f}s; no acceptance claim", flush=True)
            except Exception as exc:
                event(args.output, protocol_hash, "trial_failed", trialId=trial_id, attempt=attempt_number,
                      errorType=type(exc).__name__, error=str(exc))
                failures.append(trial_id)
                print(f"{trial_id}: failed ({type(exc).__name__})", file=sys.stderr, flush=True)
            finally:
                result = engine = None
        aggregate = {"schema": SCHEMA, "stage": "development", "targetAchieved": False,
                     "pilot": args.max_symbols is not None, "diagnosticUnverifiedHistory": args.diagnostic_unverified_history,
                     "protocolHash": protocol_hash, "snapshotFingerprint": dataset["fingerprint"],
                     "trialOrder": list(STRATEGIES), "ranking": None, "failedTrials": failures,
                     "trials": [read_json(args.output / completed[s]["summary"]["path"]) for s in STRATEGIES if s in completed]}
        write_json(args.output / "summary.json", aggregate, replace=True)
        return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-symbols", type=int, help="Hash-selected performance pilot only; never strategy selection")
    parser.add_argument("--diagnostic-unverified-history", action="store_true")
    parser.add_argument("--history-source-id", help="Optional already-imported historical package SHA-256 ID")
    args = parser.parse_args(argv)
    try:
        return execute(args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Research runner stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
