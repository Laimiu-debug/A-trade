"""Read-only local market-source audit; never compute or print returns.

Read only market.meta.path, whitelisted research dates, saved task date metadata,
and the configured source's public daily files. SQLite connections use mode=ro
and query_only. No service constructors, account databases, secrets or live APIs.
Use --from-audit for an offline summary without rescanning the source.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service import tdx

BENCHMARKS = ("sh000985", "sh000001", "sh000300", "sz399300", "sz399001", "sz399006", "bj899050")


def documented_usage_dates():
    """Extract only declared dates, never old report returns or trade outcomes."""
    path = Path(__file__).resolve().parent.parent / "docs" / "strategy-backtesting.md"
    if not path.is_file():
        return {"status": "documentationUnavailable"}
    raw = path.read_bytes()
    records = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        # This bounded block is the project's existing six-stock validation report.
        if 134 <= number <= 144:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", line)
            if dates:
                records.append({"line": number, "dates": dates})
    observed = {day for row in records for day in row["dates"]}
    if not {"2023-01-03", "2026-10-09", "2026-01-05"}.issubset(observed):
        return {"source": "docs/strategy-backtesting.md:134-144", "sha256": hashlib.sha256(raw).hexdigest(),
                "dateEvidence": records, "status": "documentChangedNeedsReview",
                "priorResultsReproduced": False, "priorReturnsLoadedOrCalculated": False}
    return {"source": "docs/strategy-backtesting.md:134-144", "sha256": hashlib.sha256(raw).hexdigest(),
            "dateEvidence": records, "interpretation": "Existing documentation reports six-stock strategy research spanning 2023-01-03..2026-10-09 with training through 2026-01-05.",
            "priorResultsReproduced": False, "priorReturnsLoadedOrCalculated": False,
            "holdoutImplication": "2024-01-01..2026-02-12 cannot be described as an untouched holdout. Missing output directories do not negate documented historical usage."}


def connect_readonly(path):
    import sqlite3
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.execute("PRAGMA query_only=ON")
    return connection


def iso_day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD")
    return value


def configured_root(workspace):
    with closing(connect_readonly(workspace / "market.sqlite3")) as db:
        row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
    if row is None:
        raise ValueError("The workspace has no configured local source")
    return tdx.normalize_directory(json.loads(row[0]))


def usage_dates(workspace):
    """Only date/status metadata is selected; no results, names or holdings."""
    result = {}
    path = workspace / "research.sqlite3"
    if path.is_file():
        with closing(connect_readonly(path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("validation_usage", "development_usage"):
                if table in tables:
                    query = f"SELECT start_date,end_date,COUNT(*) FROM {table} GROUP BY start_date,end_date ORDER BY start_date,end_date"
                    result[table] = [dict(start=a, end=b, count=c) for a, b, c in db.execute(query)]
            if "research_freezes" in tables:
                result["frozenResearchCount"] = db.execute("SELECT COUNT(*) FROM research_freezes").fetchone()[0]
            if "jobs" in tables:
                rows = db.execute("""SELECT json_extract(meta,'$.kind'),json_extract(meta,'$.status'),
                    result IS NOT NULL,json_extract(meta,'$.config.startDate'),json_extract(meta,'$.config.endDate')
                    FROM jobs""")
                counts = Counter((kind, status, bool(done), start, end) for kind, status, done, start, end in rows)
                result["researchJobs"] = [dict(zip(("kind", "status", "hasResult", "start", "end"), key), count=count)
                                          for key, count in sorted(counts.items(), key=lambda item: str(item[0]))]
    for filename in ("backtest-tasks.json", "plateau-tasks.json"):
        path = workspace / "finaltrade" / filename
        if not path.is_file():
            continue
        if path.stat().st_size > 128 * 1024 * 1024:
            result[filename] = {"status": "notInspected", "reason": "saved task file exceeds 128 MiB bound"}
            continue
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        counts = Counter()
        for item in records.get("tasks", []):
            task, payload = item.get("task", {}), item.get("payload") or {}
            base = payload.get("base_payload", payload)
            counts[(task.get("status"), base.get("date_from"), base.get("date_to"), bool(task.get("result")))] += 1
        result[filename] = [dict(zip(("status", "start", "end", "hasResult"), key), count=count)
                            for key, count in sorted(counts.items(), key=lambda item: str(item[0]))]
    result["limitations"] = ("Only current workspace ledgers and saved strategy-center tasks. Deleted reports, "
                             "other workspaces and external use remain unknown. An empty ledger does not prove an untouched holdout.")
    return result


def summarize_files(records, endpoint_start, endpoint_end):
    if not records:
        return {"count": 0}
    counts = [row["bars"] for row in records]
    return {"count": len(records), "earliestFirst": min(row["start"] for row in records),
            "latestFirst": max(row["start"] for row in records), "earliestLast": min(row["end"] for row in records),
            "latestLast": max(row["end"] for row in records), "recordCount": sum(counts),
            "barsMin": min(counts), "barsMedian": statistics.median(counts), "barsMax": max(counts),
            "latestDateDistribution": dict(Counter(row["end"] for row in records).most_common(12)),
            "startAtOrBefore": {day: sum(row["start"] <= day for row in records)
                                for day in ("2015-01-01", "2018-01-01", "2020-01-01", "2022-01-01", "2024-01-01")},
            "hasTwoYearEndpointSpan": sum(row["start"] <= endpoint_start and row["end"] >= endpoint_end for row in records)}


def audit_workspace(workspace, audit_date, sample_count, endpoint_start, endpoint_end):
    root = configured_root(workspace)
    report = {"auditDate": audit_date, "purpose": "Read-only source and date-usage audit; no returns computed",
              "configuredSource": str(root), "scope": "Local file coverage is not historical point-in-time universe",
              "parameters": {"samplesPerMarket": sample_count, "endpointStart": endpoint_start, "endpointEnd": endpoint_end},
              "files": {}, "samples": {}, "benchmarks": {}, "usedSamples": usage_dates(workspace),
              "documentedPriorUsage": documented_usage_dates()}
    for market in tdx.MARKETS:
        stocks, indices, errors = [], [], []
        folder = tdx.within(root / "vipdoc" / market / "lday", root)
        for path in sorted(folder.glob("*.day")):
            stem = path.stem.lower()
            symbol = market + stem if len(stem) == 6 and stem.isdigit() else stem
            try:
                source_market, code = tdx.symbol_parts(symbol)
                kind = tdx.security_kind(source_market, code)
                if source_market != market or kind not in ("stock", "index"):
                    continue
                start, end = tdx.read_range(path)
                record = dict(symbol=symbol, start=start, end=end, bars=path.stat().st_size // tdx.DAY.size, path=path)
                (stocks if kind == "stock" else indices).append(record)
            except (ValueError, OSError) as error:
                errors.append({"symbol": symbol, "errorType": type(error).__name__})
        report["files"][market] = {"stocks": summarize_files(stocks, endpoint_start, endpoint_end),
                                   "indices": summarize_files(indices, endpoint_start, endpoint_end),
                                   "readRangeErrors": errors, "directoryExists": folder.is_dir()}
        selected = sorted({round((len(stocks) - 1) * i / max(1, sample_count - 1))
                           for i in range(sample_count)}) if stocks else []
        samples = []
        for index in selected:
            item = stocks[index]
            try:
                bars = tdx.parse_daily(tdx.read_stable(item["path"]))
                samples.append({"symbol": item["symbol"], "start": bars[0]["date"], "end": bars[-1]["date"],
                                "bars": len(bars), "zeroVolumeBars": sum(bar["volume"] <= 0 for bar in bars),
                                "zeroAmountBars": sum(bar["amount"] <= 0 for bar in bars),
                                "validation": "full-file valid dates/order/OHLC/amount"})
            except (ValueError, OSError) as error:
                samples.append({"symbol": item["symbol"], "validation": "failed", "errorType": type(error).__name__})
        report["samples"][market] = samples
        for item in indices:
            if item["symbol"] in BENCHMARKS:
                bars = tdx.parse_daily(tdx.read_stable(item["path"]))
                report["benchmarks"][item["symbol"]] = {"start": bars[0]["date"], "end": bars[-1]["date"],
                                                         "bars": len(bars), "validation": "full-file valid"}
    for symbol in BENCHMARKS:
        report["benchmarks"].setdefault(symbol, {"available": False})
    report["evidence"] = {"historyPackageDatabaseExists": (workspace / "portfolio-history.sqlite3").exists(),
                          "supplementalHistoryCacheExists": (workspace / "research-history.sqlite3").exists(),
                          "scope": "Existence only; imported-package contents are not inspected by this local-source audit",
                          "localDailyMissing": ["historicalMembership", "historicalST", "listingAndDelistingDates",
                                                "completeCorporateActions", "openingAuctionVolume", "independentCalendar"]}
    report["summary"] = {"stockFiles": sum(item["stocks"]["count"] for item in report["files"].values()),
                         "sampleFilesFullyParsed": sum(row.get("validation", "").startswith("full-file valid")
                                                      for rows in report["samples"].values() for row in rows),
                         "sourceEligibleForCertifiedResearch": False,
                         "allowedNextUse": "Development diagnostics; complete historical and execution evidence remains required",
                         "sourceDateCaution": "Latest record dates alone do not certify completed daily observations; freeze a cutoff."}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--workspace", type=Path, help="Existing workspace; necessary fields are read only")
    inputs.add_argument("--from-audit", type=Path, help="Read a saved audit; no live data scan")
    parser.add_argument("--as-of", type=iso_day, help="Required for a fresh audit, YYYY-MM-DD")
    parser.add_argument("--endpoint-start", type=iso_day, default="2024-10-13")
    parser.add_argument("--endpoint-end", type=iso_day, default="2026-10-13")
    parser.add_argument("--samples-per-market", type=int, choices=range(0, 22), default=7)
    parser.add_argument("--output", type=Path, help="Optional JSON output outside the source/workspace")
    args = parser.parse_args()
    if args.from_audit:
        if args.output:
            parser.error("--from-audit only summarizes an existing audit and does not write output")
        report = json.loads(args.from_audit.read_text(encoding="utf-8-sig"))
        if not isinstance(report.get("summary"), dict) or not isinstance(report.get("files"), dict):
            parser.error("Input is not a source audit")
    else:
        if not args.as_of:
            parser.error("A fresh audit requires --as-of")
        if args.endpoint_start > args.endpoint_end:
            parser.error("Endpoint dates are reversed")
        workspace = args.workspace.resolve(strict=True)
        root = configured_root(workspace)
        if args.output and any(args.output.resolve().is_relative_to(base) for base in (workspace, root)):
            parser.error("Audit output must be outside the user workspace and market source")
        report = audit_workspace(workspace, args.as_of, args.samples_per_market, args.endpoint_start, args.endpoint_end)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"auditDate": report["auditDate"], "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
