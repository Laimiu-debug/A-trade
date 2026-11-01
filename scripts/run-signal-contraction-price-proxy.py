"""One registered raw three-stage-contraction development diagnostic.

Uses only raw TDX bars through T and the frozen 3141-stock development sample.
The existing event lifecycle, costs and path-risk helpers remain unchanged.
No network, parameter search, independent validation or goal certification.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import gzip
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_config import normalize_config
from service.portfolio_signals import DailyScorer
from service.signal_contraction_price_proxy import CANDIDATE_ID, evaluate_contraction, rank_contraction_signals
from service.signal_path_risk import PathRiskStudy, summarize_paths

_spec = importlib.util.spec_from_file_location("_raw_contraction_private_runtime", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
REGISTRATION = ROOT / "docs/signal-contraction-price-proxy-v1.json"
REGISTRATION_SHA256 = "4e9d8ca66467d0402c065efd3e9c4438e9967696844c439f2597e30ebb84053d"
STRATEGIES = (CANDIDATE_ID,)
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
EXTRA_FILES = {"selections": "selection.jsonl.gz"}
_base_protocol, _base_evaluate = runtime.make_protocol, runtime.evaluate_signals
_base_execute, _write_json = runtime.execute, source_tools.write_json
_base_bound_snapshot = source_tools.bound_snapshot


def registration():
    if source_tools.file_hash(REGISTRATION) != REGISTRATION_SHA256:
        raise ValueError("Registered contraction rules changed; use a new study version")
    document = source_tools.read_json(REGISTRATION)
    if document["candidateId"] != CANDIDATE_ID:
        raise ValueError("Detector candidate differs from registration")
    return document


def settings(args):
    # Only legacy eligibility and execution configuration is reused. No legacy
    # strategy is evaluated; the separate raw detector owns the candidate.
    return normalize_config({
        "startDate": args.start, "endDate": args.end, "initialCapital": 1000000,
        "strategies": [{"id": "trendBreakout", "kind": "trendBreakout", "pool": "base"}],
        "combinations": [], "allocation": {"mode": "equal", "strategyIds": ["trendBreakout"]},
        "weighting": "equal", "topN": 5, "maxPositions": 5, "maxStockWeight": .1,
        "rebalanceMode": "risk", "maxHoldingDays": 0, "stopLossPct": 0, "takeProfitPct": 0, "trailingStopPct": 0,
        "universe": {"strictHistory": not args.diagnostic_unverified_history, "excludeSt": True,
                     "minListingDays": 120, "minAmount20": 50000000},
    })


def make_protocol(args):
    document = registration()
    protocol = _base_protocol(args)
    paths = (Path(__file__), ROOT / "service/signal_contraction_price_proxy.py", ROOT / "service/signal_path_risk.py",
             ROOT / "service/research_target.py", REGISTRATION)
    protocol["implementation"].update({path.relative_to(ROOT).as_posix(): source_tools.file_hash(path) for path in paths})
    protocol.update(schema="signal-contraction-raw-price-development-v1", studyKind="one-raw-price-contraction-diagnostic",
        registration=document, registrationSha256=REGISTRATION_SHA256,
        ranking="raw-prior20-mean-amount-descending-then-symbol;T-20-through-T-1;no-active-backfill",
        candidateLog="every-raw-trigger-with-all-detector-features-and-quality-before-top-five",
        selectionLog="every-development-session-full-raw-ranking-top-five-and-control-selection",
        evaluationCoverage="one-mutually-exclusive-kind-and-reason-per-security-per-session;sum-equals-all-security-session-pairs",
        eligibility="existing-DailyScorer-base-eligibility;amount20-includes-T;pattern-ranking-excludes-T",
        priceBasis="series.bars-unadjusted-independent-views;never-series.signal_bars",
        patternState="stateless;no-cooldown-and-no-holding-suppression-during-detection",
        actionEvidence="unverified", historicalEligibilityVerified=False, comparableRightsVerified=False,
        pathRisk={"enabled": True, "boundariesRawLowPct": [-10, -20], "horizonsSessions": list(runtime.HORIZONS),
                  "meaning": "descriptive-path-only;no-stop-or-trade-change"},
        controlInterpretation="descriptive-only;non-top-five-raw-triggers-can-be-controls;independent-active-suppression-breaks-date-pairing",
        pythonVersion=sys.version)
    return protocol


def validate_sample(dataset, start, end):
    sample = registration()["sample"]
    if (start, end) != (sample["signalStart"], sample["signalEnd"]):
        raise ValueError("Use exactly the preregistered signal window; no new period selection")
    calendar = dataset["calendar"]
    if calendar != sorted(set(calendar)) or any(day > end for day in calendar):
        raise ValueError("Source calendar is not uniquely ordered and bounded at the cutoff")
    if (dataset.get("fingerprint") != sample["expectedFullSnapshotFingerprint"] or
            len(dataset["securities"]) != sample["expectedFullSecurities"] or
            sum(len(s["bars"]) for s in dataset["securities"]) != sample["expectedFullBars"] or
            sum(start <= day <= end for day in calendar) != sample["expectedScoredSessions"]):
        raise ValueError("Full source differs from the preregistered frozen snapshot/counts")
    if any(s["bars"] and s["bars"][-1]["date"] > end for s in dataset["securities"]):
        raise ValueError("Source observations exceed the registered cutoff")


def bound_snapshot(dataset, start, end):
    result = _base_bound_snapshot(dataset, start, end)
    validate_sample(result, start, end)
    return result


def _raw_view(series, day):
    index = series.by_date.get(day) if series is not None else None
    return {**series.bars[index], "priceBasisId": "raw-unadjusted"} if index is not None else None


def collect_signals(dataset, config, attempt):
    validate_sample(dataset, config["startDate"], config["endDate"])
    scorer = DailyScorer(dataset, config, pools_only=True)
    securities = {row["symbol"]: row for row in dataset["securities"]}
    symbols, calendar = sorted(securities), dataset["calendar"]
    indices = [i for i, day in enumerate(calendar) if config["startDate"] <= day <= config["endDate"]]
    first = indices[0]
    windows = {symbol: deque((_raw_view(scorer.series.get(symbol), day)
                              for day in calendar[max(0, first - 80):first]), maxlen=81) for symbol in symbols}
    signals, kinds, reasons = [], Counter(), Counter()
    raw_count = evaluated = 0
    started = last_notice = time.perf_counter()
    with gzip.open(attempt / "candidates.jsonl.gz", "xt", encoding="utf-8") as candidates_file, gzip.open(
            attempt / "coverage.jsonl.gz", "xt", encoding="utf-8") as coverage_file, gzip.open(
            attempt / "selection.jsonl.gz", "xt", encoding="utf-8") as selection_file:
        for ordinal, index in enumerate(indices):
            day = calendar[index]
            calendar81 = calendar[max(0, index - 80):index + 1]
            scored = scorer._qualified_universe(day)
            raw_signals, daily_kinds, daily_reasons = [], Counter(), Counter()
            for symbol in symbols:
                windows[symbol].append(_raw_view(scorer.series.get(symbol), day))
                eligibility = scored["eligibilityBySymbol"].get(symbol, {"eligible": False, "reason": "invalidHistory", "unknown": []})
                status = securities[symbol].get("tradeStatusByDate", {}).get(day)
                known_suspended = status is False or type(status) is int and status == 0 or status == "0"
                outcome = evaluate_contraction(symbol, day, calendar81, list(windows[symbol]), eligibility,
                                               known_suspended=known_suspended)
                daily_kinds[outcome["kind"]] += 1
                daily_reasons[outcome["reason"]] += 1
                if outcome["signal"] is not None:
                    raw_signals.append(outcome["signal"])
            if sum(daily_kinds.values()) != len(symbols) or sum(daily_reasons.values()) != len(symbols):
                raise ValueError("Daily evaluation coverage does not reconcile to the full cross section")
            ranked = rank_contraction_signals(raw_signals)
            raw_count += len(ranked)
            for row in ranked:
                item = {**row, "strategyId": CANDIDATE_ID, "scoreBasis": "raw-prior20-mean-amount-excluding-current-T"}
                runtime.write_record(candidates_file, item)
                if item["selectedTop5"]:
                    signals.append(item)
            top = {row["symbol"] for row in ranked if row["selectedTop5"]}
            base_eligible = {symbol for symbol, state in scored["eligibilityBySymbol"].items() if state.get("eligible") is True}
            pool = base_eligible - top
            control_symbols = sorted(pool, key=lambda symbol: (hashlib.sha256(
                f"{runtime.CONTROL_SEED}:{day}:{CANDIDATE_ID}:{symbol}".encode()).hexdigest(), symbol))[:len(top)]
            for rank, symbol in enumerate(control_symbols, 1):
                signals.append({"strategyId": "control_" + CANDIDATE_ID, "pairedStrategyId": CANDIDATE_ID,
                    "symbol": symbol, "signalDate": day, "rank": rank, "score": 0., "rawScore": 0.,
                    "scoreBasis": "deterministic-control-hash-not-strategy-score", "selectedTop5": False})
            matching = {"baseEligibleCount": len(base_eligible), "eligibleControlPoolSize": len(pool),
                        "requestedCount": len(top), "matchedCount": len(control_symbols),
                        "shortfall": len(top) - len(control_symbols), "selectedSymbols": control_symbols}
            runtime.write_record(selection_file, {"date": day, "strategyId": CANDIDATE_ID,
                "rawCandidateCount": len(ranked), "fullRanking": [{k: row[k] for k in
                    ("symbol", "rank", "prior20MeanAmount", "selectedTop5")} for row in ranked],
                "selectedTop5Symbols": [row["symbol"] for row in ranked if row["selectedTop5"]],
                "controlMatching": matching, "activeExclusionApplied": False, "certified": False})
            coverage = scored.get("coverage", {})
            runtime.write_record(coverage_file, {"date": day, "candidateCounts": {CANDIDATE_ID: len(ranked)},
                "controlMatching": {CANDIDATE_ID: matching}, "evaluatedSecurities": len(symbols),
                "evaluatedKinds": dict(daily_kinds), "evaluatedReasons": dict(daily_reasons),
                **{key: coverage.get(key) for key in ("totalSecurities", "eligibleSecurities", "reasons", "unknownHistoricalFields", "strictHistory", "pointInTimeComplete")}})
            kinds.update(daily_kinds)
            reasons.update(daily_reasons)
            evaluated += len(symbols)
            if time.perf_counter() - last_notice >= 30:
                print(f"Evaluated {ordinal + 1}/{len(indices)} raw-contraction sessions; proxy evidence only", flush=True)
                last_notice = time.perf_counter()
    with gzip.open(attempt / "signals.jsonl.gz", "xt", encoding="utf-8") as stream:
        for row in signals:
            runtime.write_record(stream, row)
    return signals, {"scoringSeconds": time.perf_counter() - started, "scoredSessions": len(indices),
        "candidateCounts": {CANDIDATE_ID: raw_count}, "top5SignalCount": sum(s["strategyId"] == CANDIDATE_ID for s in signals),
        "controlSignalCount": sum(s["strategyId"] != CANDIDATE_ID for s in signals), "evaluatedSecuritySessions": evaluated,
        "evaluatedKinds": dict(kinds), "evaluatedReasons": dict(reasons)}


def evaluate_signals(dataset, config, signals):
    result = _base_evaluate(dataset, config, signals)
    path_study = PathRiskStudy(dataset, config)
    paths = [path_study.evaluate(event) for event in result["events"] if event["entry"]["status"] == "filled"]
    result.update(pathRiskEvents=paths, pathRiskSummary=summarize_paths(paths, horizons=runtime.HORIZONS),
                  actionEvidence="unverified", comparableRightsVerified=False, historicalEligibilityVerified=False,
                  resultMeaning="raw-unadjusted-price-diagnostic;not-corporate-action-economic-profit-or-goal-certification")
    for group in result["groups"]:
        group["role"] = "control" if group["strategyId"].startswith("control_") else "raw-price-contraction-diagnostic"
        selected = [s for s in signals if s["strategyId"] == group["strategyId"]]
        group.update(distinctSignalSymbols=len({s["symbol"] for s in selected}),
                     distinctSignalDates=len({s["signalDate"] for s in selected}), actionEvidence="unverified")
    return result



def _write_with_extra_receipt(path, value):
    # The private baseline runtime invokes this BEFORE its success receipt and
    # event are written. In-place augmentation makes both records identical.
    if Path(path).name == "summary.json":
        value.pop("pathRiskEvents", None)
    if Path(path).name == "completion.json":
        attempt, output = Path(path).parent, Path(path).parent.parent.parent
        for key, name in EXTRA_FILES.items():
            file = attempt / name
            if not file.is_file():
                raise ValueError("Required contraction selection audit artifact is missing")
            value["files"][key] = {"path": file.relative_to(output).as_posix(), "sha256": source_tools.file_hash(file)}
    return _write_json(path, value)


def verify_completion(output, receipt, protocol_hash, fingerprint):
    if receipt.get("protocolHash") != protocol_hash or receipt.get("snapshotFingerprint") != fingerprint:
        raise ValueError("Completed study identity differs from registered protocol/source")
    if set(receipt.get("files", {})) != {"candidates", "coverage", "signals", "result", "summary", *EXTRA_FILES}:
        raise ValueError("Completed contraction artifact manifest is incomplete")
    for row in receipt["files"].values():
        path = (output / row["path"]).resolve()
        if not path.is_relative_to(output) or not path.is_file() or source_tools.file_hash(path) != row["sha256"]:
            raise ValueError("Successful study artifact missing or changed; refusing to rerun")


def execute(args):
    if not args.diagnostic_unverified_history:
        raise ValueError("Raw-price study requires explicit --diagnostic-unverified-history")
    sample = registration()["sample"]
    if (args.start, args.end) != (sample["signalStart"], sample["signalEnd"]):
        raise ValueError("Use exactly the preregistered signal window; no new period selection")
    if args.max_symbols is not None:
        raise ValueError("This registered full-sample study does not permit a sample limit")
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
        print(f"Contraction study stopped: {error}", file=sys.stderr)
        return 2


# Only this private runtime instance is adapted; frozen modules/files stay intact.
runtime.STRATEGIES, runtime.GROUPS = STRATEGIES, GROUPS
runtime.settings, runtime.make_protocol = settings, make_protocol
runtime.collect_signals, runtime.evaluate_signals = collect_signals, evaluate_signals
runtime.verify_completion, runtime.execute = verify_completion, execute
source_tools.write_json, source_tools.bound_snapshot = _write_with_extra_receipt, bound_snapshot


if __name__ == "__main__":
    raise SystemExit(main())
