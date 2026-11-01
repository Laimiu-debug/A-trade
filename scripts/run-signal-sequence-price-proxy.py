"""One preregistered raw-price sequence diagnostic; never certified +30% profit.

No strict action evidence is fabricated. Raw prices and historical-identity
fallbacks require an explicit diagnostic flag. Every completed sequence is
ranked before active-event exclusion. No candidate or parameter search occurs.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import gzip
import hashlib
import importlib.util
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_config import normalize_config
from service.portfolio_signals import DailyScorer
from service.signal_sequence_price_proxy import RawPriceSequence, CANDIDATE_ID, rank_price_signals, WAITING
from service.signal_path_risk import PathRiskStudy, summarize_paths

_spec = importlib.util.spec_from_file_location("_raw_sequence_private_runtime", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
REGISTRATION = ROOT / "docs/signal-sequence-price-proxy-v1.json"
REGISTRATION_SHA256 = "01755a6de90e1c3e2f807173b5b08fda8d923e6dd4064c322042579470df843f"
STRATEGIES = (CANDIDATE_ID,)
GROUPS = (CANDIDATE_ID, "control_" + CANDIDATE_ID)
OMITTED_TRANSITIONS = {"notTriggered", "unevaluable"}
EXTRA_FILES = {"transitions": "transitions.jsonl.gz", "finalStates": "final-state.jsonl.gz"}
_base_protocol, _base_evaluate = runtime.make_protocol, runtime.evaluate_signals
_base_execute, _write_json = runtime.execute, source_tools.write_json


def registration():
    if source_tools.file_hash(REGISTRATION) != REGISTRATION_SHA256:
        raise ValueError("Registered price-proxy rules changed; use a new study version")
    return source_tools.read_json(REGISTRATION)


def settings(args):
    # This legacy configuration carries existing eligibility/execution rules;
    # its strategy is NEVER evaluated. The custom detector owns all patterns.
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
    paths = (Path(__file__), ROOT / "service/signal_sequence_price_proxy.py", ROOT / "service/signal_path_risk.py", REGISTRATION)
    protocol["implementation"].update({path.relative_to(ROOT).as_posix(): source_tools.file_hash(path) for path in paths})
    protocol.update(schema="signal-sequence-raw-price-development-v1", studyKind="one-raw-price-sequence-diagnostic",
                    registration=document, registrationSha256=REGISTRATION_SHA256,
                    ranking="close/anchor-minus-one-ascending-then-symbol;score-is-negative-distance;no-active-backfill",
                    candidateLog="all-completed-raw-sequences-with-B-R-T-facts-before-top-five",
                    eligibility="existing-DailyScorer-pools-only-qualified-universe-on-every-session",
                    priceBasis="series.bars-unadjusted-independent-views;never-series.signal_bars",
                    initialState="idle-at-first-development-session;80-preceding-slots-warmup-only",
                    transitionLog={"individual": "all-except-notTriggered-and-unevaluable", "omitted": "daily-kind-and-reason-counts",
                                   "reconciliation": "every-security-every-session=written+omitted", "finalStates": "all-detectors-including-pending"},
                    actionEvidence="unverified", historicalEligibilityVerified=False, comparableRightsVerified=False,
                    pathRisk={"enabled": True, "boundariesRawLowPct": [-10, -20], "horizonsSessions": list(runtime.HORIZONS),
                              "meaning": "descriptive-path-only;no-stop-or-trade-change"},
                    controlInterpretation="descriptive-only;independent-active-suppression-breaks-date-pairing")
    return protocol


def _raw_view(series, day):
    if series is None:
        return None
    index = series.by_date.get(day)
    return {**series.bars[index], "priceBasisId": "raw-unadjusted"} if index is not None else None


def collect_signals(dataset, config, attempt):
    document = registration()
    sample = document["sample"]
    if (config["startDate"], config["endDate"]) != (sample["signalStart"], sample["signalEnd"]):
        raise ValueError("Use exactly the preregistered signal window; no new period selection")
    if dataset.get("fingerprint") != sample["expectedFullSnapshotFingerprint"] or len(dataset["securities"]) != sample["expectedFullSecurities"]:
        raise ValueError("Full source differs from the preregistered frozen snapshot")
    scorer = DailyScorer(dataset, config, pools_only=True)
    securities = {row["symbol"]: row for row in dataset["securities"]}
    symbols = sorted(securities)
    calendar = dataset["calendar"]
    day_indices = [i for i, day in enumerate(calendar) if config["startDate"] <= day <= config["endDate"]]
    sequences = {symbol: RawPriceSequence(symbol, keep_history=False) for symbol in symbols}
    windows = {}
    first_index = day_indices[0] if day_indices else 0
    for symbol in symbols:
        series = scorer.series.get(symbol)
        prior = [_raw_view(series, day) for day in calendar[max(0, first_index - 80):first_index]]
        windows[symbol] = deque([None] * (80 - len(prior)) + prior, maxlen=81)
    signals, formations = [], {}
    totals, omitted_reasons = Counter(), Counter()
    raw_count = written_count = step_count = 0
    started = last_notice = time.perf_counter()
    with gzip.open(attempt / "candidates.jsonl.gz", "xt", encoding="utf-8") as candidates_file, gzip.open(
            attempt / "coverage.jsonl.gz", "xt", encoding="utf-8") as coverage_file, gzip.open(
            attempt / "transitions.jsonl.gz", "xt", encoding="utf-8") as transition_file:
        for ordinal, calendar_index in enumerate(day_indices):
            day = calendar[calendar_index]
            scored = scorer._qualified_universe(day)
            daily_counts, daily_omitted = Counter(), Counter()
            raw_signals, written = [], 0
            for symbol in symbols:
                current = _raw_view(scorer.series.get(symbol), day)
                windows[symbol].append(current)
                eligibility = scored["eligibilityBySymbol"].get(symbol, {"eligible": False, "reason": "invalidHistory", "unknown": []})
                status = securities[symbol].get("tradeStatusByDate", {}).get(day)
                known_suspended = status is False or type(status) is int and status == 0 or status == "0"
                step = sequences[symbol].step(day, calendar_index, current, list(windows[symbol]), eligibility,
                                              known_suspended=known_suspended)
                transition, signal = step["transition"], step["signal"]
                kind, reason = transition["kind"], transition["reason"]
                daily_counts[kind] += 1
                if kind in OMITTED_TRANSITIONS:
                    daily_omitted[kind + ":" + reason] += 1
                else:
                    runtime.write_record(transition_file, transition)
                    written += 1
                if kind == "anchored":
                    formations[symbol] = {"B": deepcopy(transition)}
                elif kind == "retested":
                    formations.setdefault(symbol, {})["R"] = deepcopy(transition)
                if signal is not None:
                    raw_signals.append({**signal, "formation": {**formations.get(symbol, {}), "T": deepcopy(transition)}})
                if transition["after"]["name"] == "idle":
                    formations.pop(symbol, None)
            ranked = rank_price_signals(raw_signals)
            raw_count += len(ranked)
            for row in ranked:
                item = {**row, "strategyId": CANDIDATE_ID, "score": -row["distanceFromAnchor"],
                        "rawScore": -row["distanceFromAnchor"], "scoreBasis": "negative-raw-distance-from-frozen-anchor"}
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
            coverage = scored.get("coverage", {})
            runtime.write_record(coverage_file, {"date": day, "candidateCounts": {CANDIDATE_ID: len(ranked)},
                "controlMatching": {CANDIDATE_ID: matching}, "sequenceSteps": len(symbols),
                "transitionCounts": dict(daily_counts), "omittedTransitionReasons": dict(daily_omitted),
                "transitionsWritten": written, "transitionsOmitted": sum(daily_omitted.values()),
                **{key: coverage.get(key) for key in ("totalSecurities", "eligibleSecurities", "reasons", "unknownHistoricalFields", "strictHistory", "pointInTimeComplete")}})
            totals.update(daily_counts)
            omitted_reasons.update(daily_omitted)
            written_count += written
            step_count += len(symbols)
            if time.perf_counter() - last_notice >= 30:
                print(f"Advanced {ordinal + 1}/{len(day_indices)} raw-price sessions; proxy evidence only", flush=True)
                last_notice = time.perf_counter()
    states = Counter()
    with gzip.open(attempt / "final-state.jsonl.gz", "xt", encoding="utf-8") as stream:
        for symbol in symbols:
            state = sequences[symbol].state
            states[state["name"]] += 1
            runtime.write_record(stream, {"symbol": symbol, "observationCutoff": config["endDate"], "state": state,
                "pendingAtCutoff": state["name"] in WAITING, "formation": formations.get(symbol),
                "interpretation": "unfinished-formation-is-not-an-entry-or-a-failed-trade", "certified": False})
    with gzip.open(attempt / "signals.jsonl.gz", "xt", encoding="utf-8") as stream:
        for row in signals:
            runtime.write_record(stream, row)
    return signals, {"scoringSeconds": time.perf_counter() - started, "scoredSessions": len(day_indices),
        "candidateCounts": {CANDIDATE_ID: raw_count}, "top5SignalCount": sum(s["strategyId"] == CANDIDATE_ID for s in signals),
        "controlSignalCount": sum(s["strategyId"] != CANDIDATE_ID for s in signals), "sequenceSteps": step_count,
        "transitionCounts": dict(totals), "transitionsWritten": written_count, "transitionsOmitted": sum(omitted_reasons.values()),
        "omittedTransitionReasons": dict(omitted_reasons), "terminalStateCounts": dict(states),
        "pendingAtCutoff": sum(value for key, value in states.items() if key in WAITING)}


def evaluate_signals(dataset, config, signals):
    result = _base_evaluate(dataset, config, signals)
    path_study = PathRiskStudy(dataset, config)
    paths = [path_study.evaluate(event) for event in result["events"] if event["entry"]["status"] == "filled"]
    result.update(pathRiskEvents=paths, pathRiskSummary=summarize_paths(paths, horizons=runtime.HORIZONS),
                  actionEvidence="unverified", comparableRightsVerified=False, historicalEligibilityVerified=False,
                  resultMeaning="raw-unadjusted-price-diagnostic;not-corporate-action-economic-profit-or-goal-certification")
    for group in result["groups"]:
        group["role"] = "control" if group["strategyId"].startswith("control_") else "raw-price-sequence-diagnostic"
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
                raise ValueError("Required sequence audit artifact is missing")
            value["files"][key] = {"path": file.relative_to(output).as_posix(), "sha256": source_tools.file_hash(file)}
    return _write_json(path, value)


def verify_completion(output, receipt, protocol_hash, fingerprint):
    if receipt.get("protocolHash") != protocol_hash or receipt.get("snapshotFingerprint") != fingerprint:
        raise ValueError("Completed study identity differs from registered protocol/source")
    if set(receipt.get("files", {})) != {"candidates", "coverage", "signals", "result", "summary", *EXTRA_FILES}:
        raise ValueError("Completed sequence artifact manifest is incomplete")
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
        raise ValueError("This registered full-sample study does not permit --max-symbols")
    return _base_execute(args)


runtime.STRATEGIES, runtime.GROUPS = STRATEGIES, GROUPS
runtime.settings, runtime.make_protocol = settings, make_protocol
runtime.collect_signals, runtime.evaluate_signals = collect_signals, evaluate_signals
runtime.verify_completion, runtime.execute = verify_completion, execute
source_tools.write_json = _write_with_extra_receipt
runtime.__doc__ = __doc__
main = runtime.main


if __name__ == "__main__":
    raise SystemExit(main())
