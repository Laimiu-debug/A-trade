"""Build one frozen raw daily feature panel; no labels, trading or policy search."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import date, datetime, timedelta, timezone
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
from service.portfolio_signals import DailyScorer
from service.portfolio_universe import _configured_source, _digest
from service.signal_leadership_features import FEATURE_VERSION, daily_features, add_cross_section_ranks, PersistenceTracker
from service import tdx

_spec = importlib.util.spec_from_file_location("_leadership_read_only_source", Path(__file__).with_name("run-strategy-search.py"))
source_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source_tools)
REGISTRATION = ROOT / "docs/signal-leadership-panel-v1.json"
REGISTRATION_SHA256 = "b18a19345c287eb9a6eb4c94752395bb96e43e33eb1dc649d7c8fbabbe679fd3"
SOURCE_WORKSPACE = ROOT / "artifacts/strategy-search/source-workspace"
FEATURE_FIELDS = (
    "return20", "return60", "relativeReturn20", "relativeReturn60", "relativeReturn20Percentile",
    "relativeReturn60Percentile", "meanStrengthPercentile20", "minimumStrengthPercentile20",
    "topQuantileSessionCount20", "positiveCloseReturnSessions20", "closePathMaxDrawdown20",
    "signedPathEfficiency20", "closeAboveMA20Sessions20", "amountToday", "prior20MeanAmount",
    "amountRelativeToOwnPrior20",
)


def registration():
    if source_tools.file_hash(REGISTRATION) != REGISTRATION_SHA256:
        raise ValueError("Feature registration changed or is not frozen")
    document = source_tools.read_json(REGISTRATION)
    if document["featureVersion"] != FEATURE_VERSION:
        raise ValueError("Feature implementation version differs from registration")
    design = ROOT / document["designReference"]
    if source_tools.file_hash(design) != document["designSha256"]:
        raise ValueError("Feature design reference changed")
    return document


def saved_calendar(document):
    spec, sample = document["calendar"], document["sample"]
    path = ROOT / spec["path"]
    if source_tools.file_hash(path) != spec["sha256"]:
        raise ValueError("Saved calendar source hash changed")
    payload = source_tools.read_json(path)
    if (payload.get("provider") != spec["provider"] or payload.get("api") != spec["api"] or
            payload.get("errorCode") != "0" or payload.get("rowBoundReached") is not False or
            payload.get("parameters") != {"start_date": spec["start"], "end_date": spec["end"]} or
            payload.get("fields") != ["calendar_date", "is_trading_day"]):
        raise ValueError("Saved calendar is not a complete successful registered response")
    first, last = date.fromisoformat(spec["start"]), date.fromisoformat(spec["end"])
    expected = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    rows = payload.get("rows")
    if (not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != 2 or r[1] not in ("0", "1") for r in rows)
            or [r[0] for r in rows] != expected):
        raise ValueError("Saved calendar has missing, duplicate or unordered natural dates")
    days = [r[0] for r in rows if r[1] == "1"]
    warmup = sum(d < sample["signalStart"] for d in days)
    scored = sum(sample["signalStart"] <= d <= sample["signalEnd"] for d in days)
    if (len(days), warmup, scored) != (sample["expectedCalendarSessions"], sample["expectedWarmupSessions"], sample["expectedScoredSessions"]):
        raise ValueError("Saved calendar session counts differ from registration")
    return days


def validate_sample(dataset, document, calendar):
    sample = document["sample"]
    symbols = [s["symbol"] for s in dataset["securities"]]
    if (len(set(symbols)) != len(symbols) or len(symbols) != sample["expectedFullSecurities"] or
            dataset.get("fingerprint") != sample["expectedFullSnapshotFingerprint"] or
            sum(len(s["bars"]) for s in dataset["securities"]) != sample["expectedFullBars"] or
            len(symbols) * sample["expectedScoredSessions"] != sample["expectedFeatureRows"]):
        raise ValueError("Full source differs from registered frozen sample/fingerprint/counts")
    original = dataset["calendar"]
    if (original != sorted(set(original)) or
            [d for d in original if sample["warmupStart"] <= d <= sample["signalEnd"]] != calendar):
        raise ValueError("Original observed calendar differs from saved exchange calendar")
    for security in dataset["securities"]:
        dates = [b["date"] for b in security["bars"]]
        if dates != sorted(set(dates)) or any(d > sample["signalEnd"] for d in dates):
            raise ValueError("Raw security dates are duplicated, unordered or beyond cutoff")
    benchmark = dataset.get("benchmark")
    if benchmark is not None and benchmark.get("symbol") != sample["selection"]["benchmarkSymbol"]:
        raise ValueError("Benchmark identity differs from registration")
    dates = [b["date"] for b in (benchmark or {}).get("bars", [])]
    if dates != sorted(set(dates)) or any(d > sample["signalEnd"] for d in dates):
        raise ValueError("Benchmark dates are duplicated, unordered or beyond cutoff")
    if set(d for d in dates if d >= sample["warmupStart"]) - set(calendar):
        raise ValueError("Benchmark contains a non-calendar observation")


def load_snapshot(workspace, document=None, calendar=None):
    document = document or registration()
    calendar = saved_calendar(document) if calendar is None else calendar
    sample = document["sample"]
    dataset = source_tools.bound_snapshot(source_tools.snapshot_universe(workspace, sample["selection"],
        sample["signalEnd"], sample["signalStart"]), sample["signalStart"], sample["signalEnd"])
    validate_sample(dataset, document, calendar)
    return dataset, calendar


def execution_hashes(document):
    # Include transitive local source-loader/scorer imports, including optional
    # cached-history readers. No engine or research service is instantiated.
    paths = {Path(__file__), Path(source_tools.__file__), REGISTRATION, ROOT / document["designReference"],
             ROOT / "service/signal_leadership_features.py", ROOT / "service/tdx.py",
             ROOT / "service/strategies.py", ROOT / "service/execution_evidence.py", ROOT / "service/research_target.py",
             *ROOT.glob("service/portfolio_*.py")}
    return {p.relative_to(ROOT).as_posix(): source_tools.file_hash(p) for p in sorted(paths)}


def source_file_hashes(workspace, document):
    root, _, _ = _configured_source(workspace)
    paths = {workspace / "market.sqlite3"}
    for name in ("market.sqlite3-wal", "research-history.sqlite3", "research-history.sqlite3-wal"):
        if (workspace / name).is_file():
            paths.add(workspace / name)
    # Snapshot enumeration accepts both 600001.day and sh600001.day. Preserve
    # both when duplicated, so a noncanonical source cannot evade drift checks.
    for market in document["sample"]["selection"]["markets"]:
        for path in (root / "vipdoc" / market / "lday").glob("*.day"):
            stem = path.stem.lower()
            symbol = market + stem if len(stem) == 6 and stem.isdigit() else stem
            try:
                if tdx.symbol_parts(symbol)[0] == market and source_tools.board_of(symbol) == "main":
                    paths.add(tdx.within(path, root))
            except ValueError:
                continue
    benchmark_path = tdx.daily_path(root, document["sample"]["selection"]["benchmarkSymbol"])
    if benchmark_path.is_file():
        paths.add(benchmark_path)
    for market in tdx.MARKETS:
        path = root / "T0002/hq_cache" / f"{market}s.tnf"
        if path.is_file():
            paths.add(path)
    return {str(p.resolve()): source_tools.file_hash(p) for p in sorted(paths)}


def input_identity(dataset, calendar, document):
    sample = document["sample"]
    symbols = sorted(s["symbol"] for s in dataset["securities"])
    securities = {}
    for security in dataset["securities"]:
        # The full evidence mapping is hashed, including explicit absence. A
        # current name is provenance only and never supplies historical ST.
        evidence = {k: v for k, v in security.items() if k not in ("bars", "name", "sourceSha256", "forceSeed", "historyWindow")}
        securities[security["symbol"]] = {"retainedRawBarsSha256": _digest(security["bars"]),
            "sourceSha256": security.get("sourceSha256"), "datedEvidenceSha256": _digest(evidence)}
    benchmark = dataset.get("benchmark") or {}
    bars = [b for b in benchmark.get("bars", []) if sample["warmupStart"] <= b["date"] <= sample["signalEnd"]]
    dates = {b["date"] for b in bars}
    return {"snapshotFingerprint": dataset["fingerprint"], "fullDatasetContentSha256": _digest(dataset),
        "symbols": symbols, "symbolCount": len(symbols), "symbolsSha256": _digest(symbols),
        "retainedBarCount": sum(len(s["bars"]) for s in dataset["securities"]), "securities": securities,
        "calendar": {"dates": calendar, "sha256": _digest(calendar), "originalSource": dataset.get("calendarSource"),
            "savedProvider": document["calendar"], "originalRestrictedDatesEqualSavedExchangeDates": True},
        "benchmark": {"symbol": document["sample"]["selection"]["benchmarkSymbol"], "rawBarsSha256": _digest(bars),
            "barCount": len(bars), "missingDates": [d for d in calendar if d not in dates],
            "datesExactlyMatchCalendar": [b["date"] for b in bars] == calendar}}


def archive_execution(output, implementation):
    path = output / "execution-sources.zip"
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, digest in implementation.items():
            raw = (ROOT / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Execution source changed before archival")
            archive.writestr(name, raw)
    return source_tools.file_hash(path)


def eligibility_settings(document):
    sample = document["sample"]
    return normalize_config({"startDate": sample["signalStart"], "endDate": sample["signalEnd"],
        "strategies": [{"id": "trendBreakout", "kind": "trendBreakout", "pool": "base"}],
        "combinations": [], "universe": {"strictHistory": False, "excludeSt": True,
            "minListingDays": 120, "minAmount20": 50000000}})


def _raw(bar):
    return {**bar, "priceBasisId": "raw-unadjusted"} if bar is not None else None


def write_record(stream, row):
    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def as_of_eligibility(scorer, dataset, securities, day, document):
    states = scorer._qualified_universe(day)["eligibilityBySymbol"]
    for symbol, security in securities.items():
        if not security["bars"] or security["bars"][0]["date"] > day:
            states[symbol] = {"eligible": False, "reason": "notObservedYet", "unknown": []}
        elif symbol in scorer.load_errors:
            # _Series validates its entire source. A future corrupt observation
            # must not turn today's otherwise usable base qualification unknown.
            prefix = {**security, "bars": [b for b in security["bars"] if b["date"] <= day]}
            for key in ("stByDate", "stHistory", "tradeStatusByDate", "memberHistory", "memberByDate", "precloseByDate",
                        "factorByDate", "openingVolumeByDate"):
                if isinstance(prefix.get(key), dict):
                    prefix[key] = {d: v for d, v in prefix[key].items() if d <= day}
            prefix["corporateActions"] = [a for a in prefix.get("corporateActions", []) if a.get("exDate", "") <= day]
            partial = {**dataset, "securities": [prefix], "benchmark": None,
                "calendar": [d for d in dataset["calendar"] if d <= day],
                "membershipByDate": {d: v for d, v in dataset.get("membershipByDate", {}).items() if d <= day}}
            fallback = DailyScorer(partial, eligibility_settings(document), pools_only=True)
            states[symbol] = fallback._qualified_universe(day)["eligibilityBySymbol"][symbol]
    return states


def as_of_evidence(dataset, security, day):
    def dated(primary, fallback=None, *, valid=lambda value: type(value) is bool):
        key = primary if primary in security or fallback is None else fallback
        value = security.get(key, {}).get(day)
        provided = value is not None and value != ""
        interpretable = provided and valid(value)
        return {"value": value, "source": key, "status": "provided-for-date-unverified" if interpretable else "unknown",
                "sourceValueStatus": "provided-valid" if interpretable else "provided-invalid" if provided else "missing"}
    st = dated("stByDate", "stHistory")
    member = dated("memberHistory", "memberByDate")
    members = dataset.get("membershipByDate", {}).get(day)
    if isinstance(members, (list, tuple, set)):
        member = {"value": security["symbol"] in members, "source": "dataset.membershipByDate",
                  "status": "provided-for-date-unverified", "sourceValueStatus": "provided-valid"}
    listing = security.get("listingDate")
    try:
        valid_listing = isinstance(listing, str) and date.fromisoformat(listing).isoformat() == listing
    except ValueError:
        valid_listing = False
    return {"historicalSt": st, "historicalMembership": member,
        "listingDate": {"value": listing, "source": "listingDate",
                        "status": "provided-source-not-certified-asof" if valid_listing else "unknown",
                        "sourceValueStatus": "provided-valid" if valid_listing else "provided-invalid" if listing else "missing"},
        "tradeStatus": dated("tradeStatusByDate", valid=lambda v: type(v) is bool or type(v) is int and v in (0, 1) or type(v) is str and v in ("0", "1")),
        "actionEvidence": {"status": "unverified", "effectiveDateSourceObservations": [
            a for a in security.get("corporateActions", []) if a.get("exDate") == day],
            "pointInTimeTermsVerified": False}, "certified": False, "currentNameUsed": False}


def coverage(day, rows, manifest_hash):
    reasons, unknown, groups, counts = Counter(), Counter(), Counter(), Counter()
    for row in rows:
        eligibility = row["baseEligibility"]
        if eligibility is None:
            reasons["notEvaluatedDuringWarmup"] += 1
        else:
            reasons[eligibility["reason"]] += 1
            counts["eligible" if eligibility["eligible"] else "ineligible"] += 1
        # Qualification can return early for missing/not-yet-observed data.
        # Historical evidence is independently unknown even on those rows.
        for key, source_key in (("historicalSt", "historicalSt"), ("historicalMembership", "historicalMembership"),
                                ("listingDate", "listingDate"), ("historicalTradeStatus", "tradeStatus")):
            unknown[key] += row["asOfEvidence"][source_key]["status"] == "unknown"
        for name, state in row["completeness"].items():
            groups[name + (":complete" if state["complete"] else ":incomplete")] += 1
        for field in ("missingDates", "invalidDates", "haltDates", "unknownTradeStatusDates"):
            counts[field + ":current"] += day in row.get(field, [])
            counts[field + ":anyWindow"] += bool(row.get(field, []))
    denominators = {}
    for horizon in (20, 60):
        key = f"relativeReturn{horizon}CrossSectionCount"
        values = {row[key] for row in rows}
        expected = sum(row[f"relativeReturn{horizon}"] is not None for row in rows)
        if values != {expected}:
            raise ValueError("Cross-section denominator does not reconcile")
        denominators[str(horizon)] = expected
    return {"asOfDate": day, "sampleManifestHash": manifest_hash, "rows": len(rows),
        "featureNonNullCounts": {key: sum(r[key] is not None for r in rows) for key in FEATURE_FIELDS},
        "featureGroupCompleteness": dict(groups), "eligibilityReasons": dict(reasons),
        "unknownHistoricalFields": dict(unknown), "qualityCounts": dict(counts),
        "crossSectionDenominators": denominators}


def extract(dataset, document, calendar, manifest, manifest_hash, output):
    securities = {s["symbol"]: s for s in dataset["securities"]}
    symbols = manifest["inputs"]["symbols"]
    # Index only raw security.bars, independently of any scorer transformations.
    bars = {symbol: {b["date"]: b for b in securities[symbol]["bars"]} for symbol in symbols}
    benchmark = {b["date"]: b for b in (dataset.get("benchmark") or {}).get("bars", [])}
    windows, statuses = {s: deque(maxlen=61) for s in symbols}, {s: deque(maxlen=61) for s in symbols}
    benchmark_window, calendar_window, benchmark_statuses = deque(maxlen=61), deque(maxlen=61), deque(maxlen=61)
    tracker = PersistenceTracker()
    scorer = DailyScorer(dataset, eligibility_settings(document), pools_only=True)
    totals = {name: Counter() for name in ("featureNonNullCounts", "featureGroupCompleteness", "eligibilityReasons",
                                         "unknownHistoricalFields", "qualityCounts", "crossSectionDenominators")}
    emitted_rows = emitted_days = 0
    last_notice = time.perf_counter()
    with gzip.open(output / "features.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as feature_stream, gzip.open(
            output / "daily-coverage.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as coverage_stream, gzip.open(
            output / "warmup-coverage.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as warmup_stream:
        for index, day in enumerate(calendar):
            calendar_window.append(day)
            benchmark_window.append(_raw(benchmark.get(day)))
            benchmark_statuses.append((dataset.get("benchmark") or {}).get("tradeStatusByDate", {}).get(day))
            rows = []
            for symbol in symbols:
                windows[symbol].append(_raw(bars[symbol].get(day)))
                statuses[symbol].append(securities[symbol].get("tradeStatusByDate", {}).get(day))
                rows.append(daily_features(symbol, day, list(calendar_window), list(windows[symbol]), list(benchmark_window),
                    trade_status61=list(statuses[symbol]), benchmark_trade_status61=list(benchmark_statuses)))
            add_cross_section_ranks(rows)
            tracker.update(day, rows)
            if day < document["sample"]["signalStart"]:
                for row in rows:
                    row["baseEligibility"] = None
                    row["asOfEvidence"] = as_of_evidence(dataset, securities[row["symbol"]], day)
                write_record(warmup_stream, coverage(day, rows, manifest_hash))
                if time.perf_counter() - last_notice >= 30:
                    print(f"Feature panel warmup: {index + 1}/{document['sample']['expectedWarmupSessions']} sessions; no target rows emitted", flush=True)
                    last_notice = time.perf_counter()
                continue
            if [(r["asOfDate"], r["symbol"]) for r in rows] != [(day, s) for s in symbols]:
                raise ValueError("Feature output lost, duplicated or reordered a sample row")
            qualified = as_of_eligibility(scorer, dataset, securities, day, document)
            for row in rows:
                symbol = row["symbol"]
                state = qualified.get(symbol, {"eligible": False, "reason": "invalidHistory", "unknown": []})
                row.update(sampleManifestHash=manifest_hash, calendarHash=manifest["inputs"]["calendar"]["sha256"],
                    sourceHashes={**manifest["inputs"]["securities"][symbol],
                        "benchmarkRawBarsSha256": manifest["inputs"]["benchmark"]["rawBarsSha256"]},
                    baseEligibility={"eligible": state["eligible"], "reason": state["reason"], "unknown": state["unknown"],
                        "certified": False, "affectsFeaturesOrRanks": False},
                    comparableActionEvidence="unverified", historicalEligibilityVerified=False, certified=False)
                evidence = as_of_evidence(dataset, securities[symbol], day)
                row.update(asOfEvidence=evidence, historicalStEvidence=evidence["historicalSt"]["status"],
                    historicalMembershipEvidence=evidence["historicalMembership"]["status"],
                    historicalListingEvidence=evidence["listingDate"]["status"],
                    historicalTradeStatusEvidence=evidence["tradeStatus"]["status"])
                write_record(feature_stream, row)
            daily = coverage(day, rows, manifest_hash)
            write_record(coverage_stream, daily)
            for key in totals:
                totals[key].update(daily[key])
            emitted_rows += len(rows)
            emitted_days += 1
            if time.perf_counter() - last_notice >= 30:
                print(f"Feature panel: {emitted_days}/{document['sample']['expectedScoredSessions']} target sessions; {emitted_rows} rows", flush=True)
                last_notice = time.perf_counter()
    if (emitted_days, emitted_rows) != (document["sample"]["expectedScoredSessions"], document["sample"]["expectedFeatureRows"]):
        raise ValueError("Incomplete feature panel output")
    return {"targetSessions": emitted_days, "warmupSessions": len(calendar) - emitted_days, "featureRows": emitted_rows,
            **{key: dict(value) for key, value in totals.items()}}


def execute(args):
    workspace, output = Path(args.workspace).resolve(), Path(args.output).resolve()
    if workspace != SOURCE_WORKSPACE.resolve() or not workspace.is_dir():
        raise ValueError("Use only the registered isolated source workspace")
    if output.is_relative_to(workspace):
        raise ValueError("Output must be outside the read-only input workspace")
    document = registration()
    # The directory is the attempt claim. Both failed and successful attempts
    # are immutable and cannot be resumed/replaced by this feature-only CLI.
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    source_tools.write_json(output / "attempt.json", {"startedAt": datetime.now(timezone.utc).isoformat(),
        "registrationSha256": REGISTRATION_SHA256, "workspace": str(workspace), "featureVersion": FEATURE_VERSION})
    try:
        calendar = saved_calendar(document)
        implementation = execution_hashes(document)
        physical_sources = source_file_hashes(workspace, document)
        dataset, calendar = load_snapshot(workspace, document, calendar)
        if source_file_hashes(workspace, document) != physical_sources:
            raise ValueError("Physical source changed during snapshot")
        identity = input_identity(dataset, calendar, document)
        manifest = {"schema": "raw-leadership-panel-manifest-v1", "frozenAt": datetime.now(timezone.utc).isoformat(),
            "featureVersion": FEATURE_VERSION, "registration": document, "registrationSha256": REGISTRATION_SHA256,
            "implementation": implementation, "inputs": identity, "physicalSourceHashes": physical_sources,
            "executionArchiveSha256": archive_execution(output, implementation), "pythonVersion": sys.version,
            "certified": False, "targetAchieved": False, "futureLabelsAccessed": False}
        source_tools.write_json(output / "manifest.json", manifest)
        manifest_hash = source_tools.file_hash(output / "manifest.json")
        summary = extract(dataset, document, calendar, manifest, manifest_hash, output)
        if input_identity(dataset, calendar, document) != identity:
            raise ValueError("In-memory source changed during feature extraction")
        if source_file_hashes(workspace, document) != physical_sources:
            raise ValueError("Physical source changed during feature extraction")
        if execution_hashes(document) != implementation or saved_calendar(document) != calendar:
            raise ValueError("Execution source or saved calendar changed during extraction")
        if (source_tools.file_hash(output / "execution-sources.zip") != manifest["executionArchiveSha256"] or
                source_tools.file_hash(output / "manifest.json") != manifest_hash):
            raise ValueError("Frozen manifest or execution archive changed during extraction")
        summary.update(featureVersion=FEATURE_VERSION, sampleManifestHash=manifest_hash, certified=False, targetAchieved=False,
            tradePoliciesAdded=0, futureLabelsAccessed=False, strategyReturnsCalculated=False,
            forwardReturnsCalculated=False, backwardPriceReturnsCalculated=True,
            elapsedSeconds=time.perf_counter() - started, peakWorkingSetMiB=source_tools.peak_memory_mib(),
            rankingPopulation="all-finite-relative-return-rows;independent-of-legacy-eligibility",
            meaning="raw-feature-panel-only;not-leader-truth-or-economic-profit-certification")
        source_tools.write_json(output / "summary.json", summary)
        files = ("manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
                 "summary.json", "execution-sources.zip", "attempt.json")
        receipt = {"schema": "raw-leadership-panel-completion-v1", "sampleManifestHash": manifest_hash,
            "registrationSha256": REGISTRATION_SHA256, "completedAt": datetime.now(timezone.utc).isoformat(),
            "featureRows": summary["featureRows"], "targetSessions": summary["targetSessions"],
            "files": {name: {"path": name, "sha256": source_tools.file_hash(output / name)} for name in files}}
        source_tools.write_json(output / "completion.json", receipt)
        print(f"Raw feature panel complete: {output / 'summary.json'}; no labels or trading policy", flush=True)
        return 0
    except Exception as exc:
        source_tools.write_json(output / "failure.json", {"failedAt": datetime.now(timezone.utc).isoformat(),
            "errorType": type(exc).__name__, "error": str(exc), "elapsedSeconds": time.perf_counter() - started})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    try:
        return execute(parser.parse_args(argv))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Feature panel stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
