"""Offline reconciliation of saved provider calendar against local index dates.

Reads date integers only from the declared benchmark; no prices or returns.
Does not modify any source file, baseline protocol, manifest or snapshot.
"""
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "artifacts" / "strategy-search"


def run():
    source_audit = json.loads((BASE / "source-data-audit-2026-10-15.json").read_text(encoding="utf-8"))
    tdx_root = Path(source_audit["configuredSource"])
    provider_dir = BASE / "provider-probe" / "2021-2023-calendar"
    raw_path = provider_dir / "calendar-with-warmup.json"
    response = json.loads(raw_path.read_text(encoding="utf-8"))
    receipt = json.loads((provider_dir / "manifest.json").read_text(encoding="utf-8"))
    if receipt["status"] != "completed" or receipt["logout"]["errorCode"] != "0":
        raise ValueError("Calendar query did not complete with logout")
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if receipt["results"][0]["sha256"] != digest:
        raise ValueError("Saved provider response hash changed")
    if response["errorCode"] != "0" or response["rowBoundReached"]:
        raise ValueError("Calendar response failed or may be truncated")
    rows = [dict(zip(response["fields"], row)) for row in response["rows"]]
    start, end = response["parameters"]["start_date"], response["parameters"]["end_date"]
    expected_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if len(rows) != expected_days or len({row["calendar_date"] for row in rows}) != expected_days:
        raise ValueError("Calendar dates are duplicated or incomplete")
    official = {row["calendar_date"] for row in rows if row["is_trading_day"] == "1"}
    results = []
    for symbol in ("sh000300", "sh000001", "sz399001"):
        path = tdx_root / "vipdoc" / symbol[:2] / "lday" / (symbol + ".day")
        local = set()
        count = 0
        previous = None
        if path.stat().st_size % 32:
            raise ValueError("Partial TDX daily record")
        with path.open("rb") as stream:
            while raw_date := stream.read(4):
                count += 1
                if len(raw_date) != 4:
                    raise ValueError("Partial date integer")
                encoded, = struct.unpack("<I", raw_date)
                day = date(encoded // 10000, encoded // 100 % 100, encoded % 100).isoformat()
                if previous is not None and day <= previous:
                    raise ValueError("Non-increasing benchmark dates")
                previous = day
                if day > end:
                    break
                stream.seek(28, 1)
                if start <= day <= end:
                    local.add(day)
        date_digest = hashlib.sha256(json.dumps(sorted(local), separators=(",", ":")).encode()).hexdigest()
        results.append({"symbol": symbol, "path": str(path), "dateRecordsRead": count,
                        "selectedDateCount": len(local), "selectedDateSha256": date_digest,
                        "missingOfficialTradeDates": sorted(official - local),
                        "unexpectedLocalDates": sorted(local - official),
                        "matchesOfficialTradeDates": local == official})
    snapshot_path = BASE / "signal-baseline-v1" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    report = {"scope": "Independent saved calendar vs declared local benchmark and two index cross-checks only",
              "baselineSnapshotFingerprint": snapshot["fingerprint"],
              "baselineSnapshotSha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
              "baselineSnapshotContainsCalendar": isinstance(snapshot.get("calendar"), list),
              "officialSource": "BaoStock query_trade_dates", "officialResponseSha256": digest,
              "range": {"start": start, "end": end}, "calendarRows": len(rows),
              "officialTradingDays": len(official),
              "officialTradingDaysByYear": dict(Counter(day[:4] for day in sorted(official))),
              "development2022_2023TradingDays": sum(day >= "2022-01-01" for day in official),
              "benchmarkComparison": results, "returnsCalculated": False,
              "baselineModified": False, "entireStockUniverseLoaded": False,
              "limitations": ["Baseline snapshot stores no calendar array. Index-date agreement does not prove equality to its exact stock-union calendar.",
                              "No per-stock trading-status or missing-bar completeness claim follows from a calendar match.",
                              "This independent check does not retroactively change the frozen baseline manifest or calendar provenance."]}
    output = BASE / "calendar-reconciliation-2021-2023.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    run()
