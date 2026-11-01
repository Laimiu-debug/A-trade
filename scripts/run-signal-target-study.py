"""Preregistered three-strategy signal diagnostics, not certified executable returns.

Signals are generated once per close. This development runner never reads an
observation beyond 2023-12-31, never ranks strategies, and never accepts the goal.
"""
from __future__ import annotations

import argparse
from datetime import date
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_config import normalize_config
from service.portfolio_engine import _digest
from service.portfolio_signals import DailyScorer, SCORER_VERSION
from service.portfolio_universe import snapshot_universe

# Reuse only the existing read-only source and durable-file utilities. The
# account runner's annual-return evaluator is never called by this runner.
_spec = importlib.util.spec_from_file_location("_signal_source_tools", Path(__file__).with_name("run-strategy-search.py"))
source_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source_tools)

SCHEMA = "signal-target-development-v1"
STRATEGIES = ("momentum", "trendBreakout", "trendPullback")
GROUPS = tuple(sid for strategy in STRATEGIES for sid in (strategy, "control_" + strategy))
CONTROL_SEED = "a-trade-signal-same-day-base-pool-control-v1"
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
DEVELOPMENT_END = "2023-12-31"


def write_record(stream, row):
    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def settings(args):
    return normalize_config({
        "startDate": args.start, "endDate": args.end, "initialCapital": 1000000,
        "strategies": [{"id": sid, "kind": sid, "pool": "base"} for sid in STRATEGIES],
        "combinations": [], "allocation": {"mode": "equal", "strategyIds": list(STRATEGIES)},
        "weighting": "equal", "topN": 5, "maxPositions": 5, "maxStockWeight": .1,
        "rebalanceMode": "risk", "maxHoldingDays": 0, "stopLossPct": 0,
        "takeProfitPct": 0, "trailingStopPct": 0,
        "universe": {"strictHistory": not args.diagnostic_unverified_history, "excludeSt": True,
                     "minListingDays": 120, "minAmount20": 50000000},
    })


def make_protocol(args):
    helper_path = ROOT / "service/signal_target_study.py"
    implementation = {Path(__file__), Path(source_tools.__file__), helper_path,
                      *ROOT.glob("service/portfolio_*.py"), ROOT / "service/strategies.py",
                      ROOT / "service/tdx.py", ROOT / "service/execution_evidence.py"}
    return {
        "schema": SCHEMA, "stage": "development", "studyKind": "existing-default-strategy-baseline",
        "workspace": str(args.workspace), "signalStart": args.start, "signalEnd": args.end,
        "observationCutoff": args.end, "latestPermittedObservation": DEVELOPMENT_END,
        "source": "local", "markets": ["sh", "sz"], "boards": ["main"], "benchmarkSymbol": "sh000300",
        "maxSymbols": args.max_symbols, "pilot": args.max_symbols is not None,
        "pilotSeed": source_tools.PILOT_SEED,
        "pilotSelection": "source-symbol-sha256-before-bars;no-replacement-for-missing-history",
        "pilotMeaning": "performance-only;not-strategy-selection-or-acceptance",
        "diagnosticUnverifiedHistory": args.diagnostic_unverified_history,
        "config": settings(args), "strategyOrder": list(STRATEGIES), "topNPerStrategy": 5,
        "ranking": "score-descending-then-symbol-ascending;no-active-event-backfill",
        "candidateLog": "all-triggered-candidates-with-rank-and-top-five-flag",
        "controlSelection": "same-day-base-eligible;exclude-paired-strategy-top-five;fixed-hash-no-future-filter",
        "controlSeed": CONTROL_SEED, "controlCount": "paired-strategy-top-five-count;report-pool-shortfall",
        "controlInterpretation": "paired-date-descriptive-baseline;groups-and-market-days-not-independent",
        "entryRule": "signal-close-T;one-attempt-next-calendar-session-open;no-delayed-entry",
        "overlapRule": "same-symbol-same-strategy-active-until-net-open-target-proxy-or-cutoff",
        "holdingDeadline": None, "stopLoss": None, "targetNetReturnPct": 30,
        "eventBudget": 100000,
        "reportHorizonsTradingDays": list(HORIZONS), "candidateProbabilityScreenPct": 80,
        "candidateProbabilityMeaning": "provisional-review-screen-only;not-a-guarantee-or-acceptance",
        "capitalMeaning": "independent-signal-event-notional;not-a-shared-funded-portfolio",
        "targetOutcome": "first-cost-adjusted-sellable-open-proxy;daily-high-touch-is-separate",
        "certified": False, "targetAchieved": False,
        "sampleUse": "all-inspected-dates-are-development;cross-stock-and-cross-strategy-events-are-correlated",
        "scorerVersion": SCORER_VERSION,
        "implementation": {str(p.relative_to(ROOT)): source_tools.file_hash(p) for p in sorted(implementation)},
    }


def register_json(path, value, message):
    if path.exists():
        if source_tools.read_json(path) != value:
            raise ValueError(message)
    else:
        source_tools.write_json(path, value)


def verify_completion(output, receipt, protocol_hash, fingerprint):
    if receipt.get("protocolHash") != protocol_hash or receipt.get("snapshotFingerprint") != fingerprint:
        raise ValueError("Completed study identity differs from its registered protocol and source")
    required = {"candidates", "coverage", "signals", "result", "summary"}
    if set(receipt.get("files", {})) != required:
        raise ValueError("Completed study artifact manifest is incomplete")
    for row in receipt["files"].values():
        path = (output / row["path"]).resolve()
        if not path.is_relative_to(output) or not path.is_file() or source_tools.file_hash(path) != row["sha256"]:
            raise ValueError("Successful study artifact missing or changed; refusing to rerun")


def previous_completion(output, protocol_hash, fingerprint):
    completed = None
    log = output / "events.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("protocolHash") != protocol_hash:
                raise ValueError("Event log belongs to another protocol")
            if row["event"] == "study_succeeded":
                receipt = row["receipt"]
                verify_completion(output, receipt, protocol_hash, fingerprint)
                if completed is not None:
                    raise ValueError("Multiple successful study records; do not choose between reruns")
                completed = receipt
    for path in sorted((output / "attempts").glob("attempt-*/completion.json")):
        receipt = source_tools.read_json(path)
        verify_completion(output, receipt, protocol_hash, fingerprint)
        if completed is not None and completed != receipt:
            raise ValueError("Multiple successful study attempts; do not choose between reruns")
        if completed is None:
            source_tools.event(output, protocol_hash, "study_succeeded", receipt=receipt, recovered=True)
            completed = receipt
    return completed


def collect_signals(dataset, config, attempt):
    scorer = DailyScorer(dataset, config)
    days = [day for day in dataset["calendar"] if config["startDate"] <= day <= config["endDate"]]
    signals, counts = [], {sid: 0 for sid in STRATEGIES}
    started, last_notice = time.perf_counter(), time.perf_counter()
    with gzip.open(attempt / "candidates.jsonl.gz", "xt", encoding="utf-8") as candidates_file, gzip.open(
            attempt / "coverage.jsonl.gz", "xt", encoding="utf-8") as coverage_file:
        for day_index, day in enumerate(days):
            scored = scorer.daily(day)
            daily_counts = {}
            matching = {}
            base_eligible = {symbol for symbol, state in scored["eligibilityBySymbol"].items() if state.get("eligible") is True}
            for sid in STRATEGIES:
                rows = sorted(scored["strategies"][sid], key=lambda row: (-row["score"], row["symbol"]))
                counts[sid] += len(rows)
                daily_counts[sid] = len(rows)
                for rank, row in enumerate(rows, 1):
                    item = {"strategyId": sid, "symbol": row["symbol"], "signalDate": day,
                            "rank": rank, "score": row["score"], "rawScore": row["rawScore"],
                            "selectedTop5": rank <= config["topN"]}
                    write_record(candidates_file, item)
                    if item["selectedTop5"]:
                        signals.append(item)
                top = {row["symbol"] for row in rows[:config["topN"]]}
                pool = base_eligible - top
                control_symbols = sorted(pool, key=lambda symbol: (
                    hashlib.sha256(f"{CONTROL_SEED}:{day}:{sid}:{symbol}".encode()).hexdigest(), symbol))[:len(top)]
                matching[sid] = {"baseEligibleCount": len(base_eligible), "eligibleControlPoolSize": len(pool),
                                 "requestedCount": len(top), "matchedCount": len(control_symbols),
                                 "shortfall": len(top) - len(control_symbols), "selectedSymbols": control_symbols}
                for rank, symbol in enumerate(control_symbols, 1):
                    signals.append({"strategyId": "control_" + sid, "pairedStrategyId": sid,
                                    "symbol": symbol, "signalDate": day, "rank": rank, "score": 0., "rawScore": 0.,
                                    "scoreBasis": "deterministic-control-hash-not-strategy-score", "selectedTop5": False})
            coverage = scored.get("coverage", {})
            write_record(coverage_file, {"date": day, "candidateCounts": daily_counts, "controlMatching": matching,
                                        **{key: coverage.get(key) for key in ("totalSecurities", "eligibleSecurities", "reasons", "unknownHistoricalFields", "strictHistory", "pointInTimeComplete")}})
            if time.perf_counter() - last_notice >= 30:
                print(f"Scored {day_index + 1}/{len(days)} development sessions; no outcome acceptance", flush=True)
                last_notice = time.perf_counter()
    with gzip.open(attempt / "signals.jsonl.gz", "xt", encoding="utf-8") as stream:
        for row in signals:
            write_record(stream, row)
    return signals, {"scoringSeconds": time.perf_counter() - started, "scoredSessions": len(days),
                     "candidateCounts": counts,
                     "top5SignalCount": sum(row["strategyId"] in STRATEGIES for row in signals),
                     "controlSignalCount": sum(row["strategyId"] not in STRATEGIES for row in signals)}


def evaluate_signals(dataset, config, signals):
    from service.signal_target_study import SignalTargetStudy, summarize_events
    evaluator = SignalTargetStudy(dataset, config=config, budget=100000, target_pct=30)
    active, events, suppressed = {}, [], []
    for signal in signals:
        key = (signal["strategyId"], signal["symbol"])
        prior = active.get(key)
        if prior is not None:
            hit_day = prior["outcomes"]["nextOpenNet30"].get("hitDate")
            # A target exit at today's open releases the symbol before today's
            # close signal. A censored holding remains active through cutoff.
            if hit_day is None or signal["signalDate"] < hit_day:
                suppressed.append({**signal, "reason": "same-symbol-same-group-active-event",
                                   "blockedByEventId": prior["eventId"], "activeUntil": prior.get("activeUntil")})
                continue
        event = evaluator.evaluate(signal)
        event.update(eventId=_digest([signal["strategyId"], signal["symbol"], signal["signalDate"]])[:24],
                     strategyId=signal["strategyId"], symbol=signal["symbol"], signalDate=signal["signalDate"])
        events.append(event)
        if event["entry"]["status"] == "filled":
            active[key] = event
        else:
            active.pop(key, None)
    groups = []
    for sid in GROUPS:
        rows = [event for event in events if event["strategyId"] == sid]
        group = summarize_events(rows, horizons=HORIZONS, precision_target=.8)
        group.update(strategyId=sid, role="control" if sid.startswith("control_") else "baseline-strategy",
                     pairedStrategyId=sid.removeprefix("control_"), certified=False, targetAchieved=False,
                     rawSelectedSignals=sum(s["strategyId"] == sid for s in signals),
                     overlapSuppressedCount=sum(s["strategyId"] == sid for s in suppressed))
        groups.append(group)
    return {"events": events, "overlapSuppressed": suppressed, "groups": groups,
            "eventCount": len(events), "overlapSuppressedCount": len(suppressed),
            "eventModel": "independent-notional-events;no-shared-portfolio-capital-or-independent-observation-claim",
            "resultMeaning": "daily-data-target-proxy;not-certified-execution-or-future-probability"}


def execute(args):
    args.workspace, args.output = Path(args.workspace).resolve(), Path(args.output).resolve()
    if not args.workspace.is_dir():
        raise ValueError("Input workspace does not exist")
    if args.output.is_relative_to(args.workspace):
        raise ValueError("Output must be outside the read-only input workspace")
    for day in (args.start, args.end):
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Dates must use YYYY-MM-DD")
    if not "2022-01-01" <= args.start <= args.end <= DEVELOPMENT_END:
        raise ValueError("This development runner is restricted to 2022-01-01 through 2023-12-31")
    if args.max_symbols is not None and not 1 <= args.max_symbols <= 9000:
        raise ValueError("--max-symbols must be between 1 and 9000")
    args.output.mkdir(parents=True, exist_ok=True)
    with source_tools.output_lock(args.output):
        protocol = make_protocol(args)
        protocol_hash = _digest(protocol)
        path = args.output / "protocol.json"
        if not path.exists():
            if set(p.name for p in args.output.iterdir()) != {".run.lock"}:
                raise ValueError("Unregistered output is not empty")
            source_tools.write_json(path, protocol)
            source_tools.event(args.output, protocol_hash, "protocol_registered")
        else:
            register_json(path, protocol, "Existing protocol differs; never overwrite or amend a completed study")
        selection = {"mode": "all", "source": "local", "markets": ["sh", "sz"], "boards": ["main"], "benchmarkSymbol": "sh000300"}
        if args.max_symbols is not None:
            selection.update(mode="explicit", symbols=source_tools.pilot_symbols(source_tools.discover_symbols(args.workspace), args.max_symbols))
        register_json(args.output / "selection.json", selection, "Registered source symbol selection changed; no replacement pilot")
        started = time.perf_counter()
        source_tools.event(args.output, protocol_hash, "snapshot_started")
        try:
            dataset = source_tools.bound_snapshot(snapshot_universe(args.workspace, selection, args.end, args.start), args.start, args.end)
            manifest = {"fingerprint": dataset["fingerprint"], "symbols": sorted(s["symbol"] for s in dataset["securities"]),
                        "sourceStart": args.start, "observationCutoff": args.end,
                        "barCount": sum(len(s["bars"]) for s in dataset["securities"]),
                        "quality": dataset.get("dataQuality", {}), "coverage": dataset.get("universeCoverage", {})}
            register_json(args.output / "snapshot.json", manifest, "Frozen snapshot fingerprint or coverage changed; refusing to resume")
        except Exception as exc:
            source_tools.event(args.output, protocol_hash, "snapshot_failed", errorType=type(exc).__name__, error=str(exc))
            raise
        source_tools.event(args.output, protocol_hash, "snapshot_ready", fingerprint=dataset["fingerprint"],
                           seconds=time.perf_counter() - started, symbolCount=len(dataset["securities"]),
                           barCount=manifest["barCount"], peakWorkingSetMiB=source_tools.peak_memory_mib())
        completed = previous_completion(args.output, protocol_hash, dataset["fingerprint"])
        if completed:
            print("Verified completed signal study; no signals or outcomes recomputed", flush=True)
            return 0
        attempts = args.output / "attempts"
        attempts.mkdir(exist_ok=True)
        number = 1 + max((int(p.name.split("-")[-1]) for p in attempts.glob("attempt-*") if p.is_dir()), default=0)
        attempt = attempts / f"attempt-{number:04d}"
        attempt.mkdir()
        source_tools.event(args.output, protocol_hash, "study_started", attempt=number)
        try:
            signals, performance = collect_signals(dataset, protocol["config"], attempt)
            source_tools.event(args.output, protocol_hash, "scoring_completed", attempt=number, **performance)
            tick = time.perf_counter()
            result = evaluate_signals(dataset, protocol["config"], signals)
            performance["eventEvaluationSeconds"] = time.perf_counter() - tick
            result.update(stage="development", studyKind=protocol["studyKind"], pilot=protocol["pilot"],
                          diagnosticUnverifiedHistory=args.diagnostic_unverified_history,
                          certified=False, targetAchieved=False, strategyOrder=list(STRATEGIES),
                          protocolHash=protocol_hash, snapshotFingerprint=dataset["fingerprint"],
                          sourceQuality=manifest["quality"], sourceCoverage=manifest["coverage"])
            summary = {key: value for key, value in result.items() if key not in ("events", "overlapSuppressed")}
            summary.update(ranking=None, selectionEligible=False, performance=performance)
            performance.update(peakWorkingSetMiB=source_tools.peak_memory_mib(), peakMemoryScope="whole-process-high-water-mark")
            with gzip.open(attempt / "result.json.gz", "xt", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            source_tools.write_json(attempt / "summary.json", summary)
            files = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
                     "result": "result.json.gz", "summary": "summary.json"}
            receipt = {"protocolHash": protocol_hash, "snapshotFingerprint": dataset["fingerprint"], "attempt": number,
                       "files": {key: {"path": (attempt / name).relative_to(args.output).as_posix(),
                                       "sha256": source_tools.file_hash(attempt / name)} for key, name in files.items()}}
            source_tools.write_json(attempt / "completion.json", receipt)
            source_tools.event(args.output, protocol_hash, "study_succeeded", receipt=receipt)
            print(f"Signal development diagnostic complete: {attempt / 'summary.json'}; proxy evidence only", flush=True)
            return 0
        except Exception as exc:
            source_tools.event(args.output, protocol_hash, "study_failed", attempt=number, errorType=type(exc).__name__, error=str(exc))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=DEVELOPMENT_END)
    parser.add_argument("--max-symbols", type=int, help="Hash-selected performance pilot only; never a strategy-selection result")
    parser.add_argument("--diagnostic-unverified-history", action="store_true")
    try:
        return execute(parser.parse_args(argv))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Signal study stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
