"""Build complete pre-active contraction schedules from an already loaded source.

No source loading, account execution or result-based parameter changes occur here.
The original detector is stateless: repeated dates are retained; each account
owns its holding/duplicate-buy suppression after the original daily Top5.
"""
from __future__ import annotations

from argparse import Namespace
from collections import Counter, deque
from copy import deepcopy
from datetime import date
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.force_statistics import prepare_force_statistics
from service.portfolio_signals import DailyScorer
from service.signal_contraction_price_proxy import CANDIDATE_ID, evaluate_contraction, rank_contraction_signals

_spec = importlib.util.spec_from_file_location(
    "_supplemental_contraction_method_only", ROOT / "scripts/run-signal-contraction-price-proxy.py")
original = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(original)

VERSION = "supplemental-contraction-pressure-schedule-v1"


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_pins():
    paths = ("scripts/build-supplemental-contraction-schedule.py",
             "scripts/run-signal-contraction-price-proxy.py", "service/signal_contraction_price_proxy.py",
             "service/force_statistics.py", "service/portfolio_signals.py", "service/portfolio_config.py",
             "docs/signal-contraction-price-proxy-v1.json")
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


def _date(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected canonical YYYY-MM-DD date")
    return value


def pressure_filter(row):
    """Fixed context gate; missing/nonfinite evidence never becomes a zero."""
    short, long = row.get("pressure5"), row.get("pressure20")
    known = all(type(v) in (int, float) and math.isfinite(v) for v in (short, long))
    status = "unknown" if not known else "included" if long > 0 and short > long else "excluded"
    return {"pressure5": short if type(short) in (int, float) and math.isfinite(short) else None,
            "pressure20": long if type(long) in (int, float) and math.isfinite(long) else None,
            "status": status,
            "reason": "requiredPressureUnknown" if not known else
                "positiveAndStrengtheningPressure" if status == "included" else "pressureConditionNotMet"}


def _project(row):
    return {"signalId": f"{CANDIDATE_ID}:{row['signalDate']}:{row['symbol']}",
            "strategyId": CANDIDATE_ID, "symbol": row["symbol"],
            "signalDate": row["signalDate"], "originalRank": row["rank"]}


def build_schedule(dataset, binding=None, *, start="2024-01-01", end="2025-12-31", progress=None):
    """Return schedules.baseline/pressure and complete daily selection evidence.

    ``dataset`` is the existing raw snapshot, not a reference-adjusted view.
    ``binding`` optionally supplies its already-verified ``identity``; this
    helper preserves that provenance without reopening the large source files.
    Dates are parameters for small synthetic checks, not a parameter search.
    Unknown pressure excludes only the filtered schedule, never the baseline.
    Inputs are not modified. There is no cross-day signal cooldown or active
    state; same-symbol opportunities on different dates must remain present.
    """
    _date(start), _date(end)
    if start > end or not isinstance(dataset, dict):
        raise ValueError("Invalid interval or dataset")
    calendar = dataset["calendar"]
    if not isinstance(calendar, list) or not calendar or calendar != sorted(set(calendar)):
        raise ValueError("Calendar must be nonempty, sorted and unique")
    for day in calendar:
        _date(day)
    securities = {s["symbol"]: s for s in dataset["securities"]}
    if len(securities) != len(dataset["securities"]) or any(type(s) is not str or not s for s in securities):
        raise ValueError("Security symbols must be unique nonempty strings")
    indices = [i for i, day in enumerate(calendar) if start <= day <= end]
    if not indices:
        raise ValueError("No target sessions in the supplied calendar")
    if binding is not None and not isinstance(binding, dict):
        raise ValueError("binding must be an object")
    identity = deepcopy((binding or {}).get("identity", {}))
    source = {"datasetFingerprint": dataset.get("fingerprint"),
              "calendarSha256": _digest(calendar), "bindingIdentity": identity,
              "bindingIdentitySha256": _digest(identity),
              "priceBasis": "series.bars-raw-unadjusted"}
    source_ref = {key: value for key, value in source.items() if key != "bindingIdentity"}
    pins = _source_pins()
    original.registration()  # Check the unchanged original rule document only.
    config = original.settings(Namespace(start=start, end=end, diagnostic_unverified_history=True))
    scorer = DailyScorer(dataset, config, pools_only=True)
    symbols, first = sorted(securities), indices[0]
    windows = {s: deque((original._raw_view(scorer.series.get(s), day)
                        for day in calendar[max(0, first - 80):first]), maxlen=81) for s in symbols}
    result = {"schema": VERSION, "schedules": {"baseline": [], "pressure": []},
              "top5Signals": [], "filterEvidence": [], "selections": [], "coverage": []}
    kinds, reasons, filter_counts = Counter(), Counter(), Counter()
    raw_count = 0
    started = last_notice = time.monotonic()
    for ordinal, index in enumerate(indices):
        day = calendar[index]
        dates = calendar[max(0, index - 80):index + 1]
        scored = scorer._qualified_universe(day)
        daily_kinds, daily_reasons, candidates = Counter(), Counter(), []
        for symbol in symbols:
            windows[symbol].append(original._raw_view(scorer.series.get(symbol), day))
            eligibility = scored["eligibilityBySymbol"].get(symbol,
                {"eligible": False, "reason": "invalidHistory", "unknown": []})
            status = securities[symbol].get("tradeStatusByDate", {}).get(day)
            known_halt = status is False or type(status) is int and status == 0 or status == "0"
            outcome = evaluate_contraction(symbol, day, dates, list(windows[symbol]), eligibility,
                                           known_suspended=known_halt)
            daily_kinds[outcome["kind"]] += 1
            daily_reasons[outcome["reason"]] += 1
            if outcome["signal"] is not None:
                candidates.append(outcome["signal"])
        ranked = rank_contraction_signals(candidates)
        raw_count += len(ranked)
        for row in ranked:
            if not row["selectedTop5"]:
                continue
            symbol = row["symbol"]
            raw81 = list(windows[symbol])
            raw40, dates40 = raw81[-40:], dates[-40:]
            # 40 aligned slots are sufficient for the exact frozen pressure20
            # calculation (20 return pressures, each using its prior20 volume).
            prepared = prepare_force_statistics([b for b in raw40 if b is not None], calendar=dates40)
            pressure = prepared["current"]
            projected = _project(row)
            evidence = {**projected, **pressure_filter(pressure),
                        "source": {**source_ref, "raw81Sha256": _digest(raw81),
                                   "pressureWindowStart": dates40[0], "pressureWindowEnd": day,
                                   "pressureWindowSlots": len(dates40)}}
            result["top5Signals"].append({**row,
                "scoreBasis": "raw-prior20-mean-amount-excluding-current-T", "source": evidence["source"]})
            result["schedules"]["baseline"].append(projected)
            if evidence["status"] == "included":
                result["schedules"]["pressure"].append(deepcopy(projected))
            result["filterEvidence"].append(evidence)
            filter_counts[evidence["status"]] += 1
        result["selections"].append({"date": day, "strategyId": CANDIDATE_ID,
            "rawCandidateCount": len(ranked), "fullRanking": [{k: r[k] for k in
                ("symbol", "rank", "prior20MeanAmount", "selectedTop5")} for r in ranked],
            "selectedTop5Symbols": [r["symbol"] for r in ranked if r["selectedTop5"]],
            "activeExclusionApplied": False, "backfillApplied": False})
        coverage = scored.get("coverage", {})
        result["coverage"].append({"date": day, "evaluatedSecurities": len(symbols),
            "evaluatedKinds": dict(daily_kinds), "evaluatedReasons": dict(daily_reasons),
            **{key: coverage.get(key) for key in ("eligibleSecurities", "unknownHistoricalFields",
                                                "strictHistory", "pointInTimeComplete")}})
        kinds.update(daily_kinds)
        reasons.update(daily_reasons)
        if progress is not None and (time.monotonic() - last_notice >= 30 or ordinal + 1 == len(indices)):
            progress({"phase": "contraction-schedule", "sessions": ordinal + 1,
                      "totalSessions": len(indices), "rawCandidates": raw_count,
                      "baseline": len(result["schedules"]["baseline"]),
                      "pressure": len(result["schedules"]["pressure"])})
            last_notice = time.monotonic()
    for schedule in result["schedules"].values():
        if len(schedule) != len({r["signalId"] for r in schedule}):
            raise ValueError("Duplicate symbol-date signal identity")
    if _source_pins() != pins:
        raise ValueError("Schedule implementation changed during generation")
    result["metadata"] = {"source": source, "implementation": pins,
        "startDate": start, "endDate": end, "firstSession": calendar[indices[0]],
        "lastSession": calendar[indices[-1]], "scoredSessions": len(indices),
        "securities": len(symbols), "evaluatedSecuritySessions": len(indices) * len(symbols),
        "rawCandidateCount": raw_count, "top5SignalCount": len(result["schedules"]["baseline"]),
        "pressureSignalCount": len(result["schedules"]["pressure"]),
        "filterCounts": {k: filter_counts[k] for k in ("included", "excluded", "unknown")},
        "evaluatedKinds": dict(kinds), "evaluatedReasons": dict(reasons),
        "eligibilityConfig": deepcopy(config["universe"]),
        "ranking": "prior20MeanAmount descending then symbol ascending; original Top5 before pressure",
        "emission": "stateless daily; no cooldown; account independently suppresses held-symbol entries",
        "filterRule": "pressure20 > 0 and pressure5 > pressure20", "backfillApplied": False,
        "elapsedSeconds": time.monotonic() - started,
        "returnsCalculated": False, "certified": False, "targetAchieved": False,
        "limitations": ["Caller owns source binding verification; this helper does not reload source files.",
                        "Original raw-price and unknown historical-identity limitations are retained.",
                        "Supplemental historical validation is not a pristine holdout."]}
    return result
