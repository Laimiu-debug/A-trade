"""Audit public local TDX LC1 coverage without returns, network or workspace writes.

All accepted stock/index files: inspect size and first/last record only.
At most three evenly selected stock files per market: validate complete minute
timestamps and count dates; endpoint span alone does not prove continuous data.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import statistics
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service import intraday, tdx


def endpoints(path, root):
    path = tdx.within(path, root)
    before = path.stat()
    if before.st_size < intraday.LC1_RECORD.size or before.st_size % intraday.LC1_RECORD.size:
        raise ValueError("Empty or partial LC1 records")
    with path.open("rb") as stream:
        first = stream.read(intraday.LC1_RECORD.size)
        stream.seek(-intraday.LC1_RECORD.size, 2)
        last = stream.read(intraday.LC1_RECORD.size)
    after = path.stat()
    if intraday._signature(before) != intraday._signature(after):
        raise ValueError("File changed during endpoint read")
    start, first_row = intraday._decode(intraday.LC1_RECORD.unpack(first))
    end, last_row = intraday._decode(intraday.LC1_RECORD.unpack(last))
    if (start, first_row["time"]) > (end, last_row["time"]):
        raise ValueError("Reversed endpoint timestamps")
    return {"symbol": path.stem, "start": start, "end": end,
            "firstTime": first_row["time"], "lastTime": last_row["time"],
            "bars": before.st_size // intraday.LC1_RECORD.size}


def summary(rows):
    if not rows:
        return {"count": 0}
    counts = [row["bars"] for row in rows]
    return {"count": len(rows), "earliestFirst": min(row["start"] for row in rows),
            "latestFirst": max(row["start"] for row in rows),
            "earliestLast": min(row["end"] for row in rows), "latestLast": max(row["end"] for row in rows),
            "barsMin": min(counts), "barsMedian": statistics.median(counts), "barsMax": max(counts),
            "firstDateDistribution": dict(Counter(row["start"] for row in rows).most_common(10)),
            "lastDateDistribution": dict(Counter(row["end"] for row in rows).most_common(10)),
            "startAtOrBefore": {day: sum(row["start"] <= day for row in rows)
                                for day in ("2022-01-01", "2023-01-01", "2024-01-01", "2026-01-01")}}


def audit(root, sample_count):
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "source": str(root), "markets": {},
              "scope": "LC1 endpoints for all stock/index files; bounded complete timestamp samples; no return calculation",
              "samplesPerMarket": sample_count,
              "limitations": ["Endpoint span does not establish complete trade-day or minute coverage.",
                              "Minute OHLC does not determine ordering of two thresholds hit within the same minute.",
                              "Minute volume is not exact opening-auction volume or executable order quantity.",
                              "Historical price touches do not prove achievable fills; T+1 and trading status remain required."]}
    for market in tdx.MARKETS:
        folder = tdx.within(root / "vipdoc" / market / "minline", root)
        paths = sorted(folder.glob("*.lc1"))
        stocks, indices, errors = [], [], []
        for path in paths:
            stem = path.stem.lower()
            symbol = market + stem if len(stem) == 6 and stem.isdigit() else stem
            try:
                source_market, code = tdx.symbol_parts(symbol)
                kind = tdx.security_kind(source_market, code)
                if source_market != market or kind not in ("stock", "index"):
                    continue
                row = endpoints(path, root)
                row["symbol"] = symbol
                (stocks if kind == "stock" else indices).append((row, path))
            except (ValueError, OSError) as error:
                errors.append({"symbol": symbol, "errorType": type(error).__name__})
        samples = []
        selected = sorted({round((len(stocks) - 1) * i / max(1, sample_count - 1))
                           for i in range(sample_count)}) if stocks else []
        for index in selected:
            row, path = stocks[index]
            try:
                data = intraday._read_stable(path, root)
                dates = Counter()
                previous = None
                for raw in intraday.LC1_RECORD.iter_unpack(data):
                    day, minute = intraday._decode(raw)
                    identity = (day, minute["time"])
                    if previous is not None and identity <= previous:
                        raise ValueError("Non-increasing minute timestamp")
                    dates[day] += 1
                    previous = identity
                samples.append({**row, "distinctDates": len(dates), "minBarsPerDate": min(dates.values()),
                                "maxBarsPerDate": max(dates.values()),
                                "barsPerDateDistribution": dict(Counter(dates.values())),
                                "monthlyRecordDateCounts": dict(Counter(day[:7] for day in dates)),
                                "calendarGapsOver10Days": [
                                    {"before": before, "after": after, "calendarGapDays": gap}
                                    for before, after in zip(list(dates), list(dates)[1:])
                                    if (gap := (date.fromisoformat(after) - date.fromisoformat(before)).days) > 10],
                                "validation": "Complete sampled file: timestamps and OHLC valid; no external-calendar reconciliation"})
            except (ValueError, OSError) as error:
                samples.append({**row, "validation": "failed", "errorType": type(error).__name__})
        report["markets"][market] = {"directoryExists": folder.is_dir(), "allLc1Files": len(paths),
                                     "stocks": summary([row for row, _ in stocks]),
                                     "indices": summary([row for row, _ in indices]), "errors": errors,
                                     "samples": samples,
                                     "lc5FileCountOnly": len(list((root / "vipdoc" / market / "fzline").glob("*.lc5")))}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Configured public TDX root")
    parser.add_argument("--samples-per-market", type=int, choices=range(4), default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = tdx.normalize_directory(str(args.source))
    output = args.output.resolve()
    repo_artifacts = Path(__file__).resolve().parent.parent / "artifacts" / "strategy-search"
    if not output.is_relative_to(repo_artifacts) or output.is_relative_to(root):
        parser.error("Output must be within repository artifacts/strategy-search and outside the source")
    report = audit(root, args.samples_per_market)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({market: {"stocks": item["stocks"], "sampleCount": len(item["samples"]),
                              "errorCount": len(item["errors"])} for market, item in report["markets"].items()}))


if __name__ == "__main__":
    main()
