"""Read the existing local TDX action cache with the installed public reader.

This source can supply effective price-reference terms and unsupported event
flags. It cannot prove announcement, record, payment, listing or broker custody.
No application cache is altered or refreshed and no network request is made.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import struct
import sys
import time

from pytdx.reader.gbbq_reader import GbbqReader
from pytdx.parser.get_xdxr_info import XDXR_CATEGORY_MAPPING, GetXdXrInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/action-source-coverage-v1/local-cache"
SOURCE = Path("E:/TDX/T0002/hq_cache/gbbq")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    raw = SOURCE.read_bytes()
    count, = struct.unpack("<I", raw[:4])
    if len(raw) != 4 + 29 * count:
        raise ValueError("Local cache record length/count mismatch")
    archive = OUT / "gbbq.snapshot"
    archive.write_bytes(raw)
    cohort_path = ROOT / "artifacts/strategy-search/action-source-coverage-v1/acquisition/cohort.json"
    cohort = json.loads(cohort_path.read_text("utf-8"))
    members = {row["symbol"] for row in cohort["members"]}
    reader_path, semantic_path = Path(inspect.getfile(GbbqReader)), Path(inspect.getfile(GetXdXrInfo))
    plan = {"schema": "local-action-cache-read-plan-v1", "registeredAt": datetime.now(timezone.utc).isoformat(),
        "sourcePath": str(SOURCE), "sourceSha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "expectedRecords": count,
        "snapshot": archive.relative_to(ROOT).as_posix(), "reader": str(reader_path), "readerSha256": sha(reader_path),
        "semanticSource": str(semantic_path), "semanticSourceSha256": sha(semantic_path),
        "pytdxVersion": importlib.metadata.version("pytdx"), "cohortSha256": sha(cohort_path),
        "start": "2023-01-01", "end": "2026-10-15", "networkRequests": 0, "marketAccounts": 0,
        "semantics": "Category1 numeric terms are candidate ex-reference evidence; other categories remain separate unsupported-event flags, not assumed dividends.",
        "missingRightsDatesInferred": False, "marketUseCertified": False}
    write(OUT / "plan.json", plan)
    print(json.dumps({"state": "decoding-local-snapshot", "records": count, "bytes": len(raw)}), flush=True)
    start = time.monotonic()
    df = GbbqReader().get_df(str(archive))
    if len(df) != count or sha(SOURCE) != plan["sourceSha256"]:
        raise ValueError("Decoded record count mismatch or source changed during read")
    rows = df.to_dict("records")
    if len(rows) != count:
        raise ValueError("Decoded row count changed")
    payload = json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    with (OUT / "all-records.json.gz").open("xb") as handle:
        handle.write(gzip.compress(payload, mtime=0))
    selected = []
    for row in rows:
        market, code = int(row["market"]), row["code"]
        symbol = ("sh" if market == 1 else "sz" if market == 0 else "unknown") + code
        if symbol not in members:
            continue
        value = str(int(row["datetime"]))
        day = value[:4] + "-" + value[4:6] + "-" + value[6:8]
        if not plan["start"] <= day <= plan["end"]:
            continue
        selected.append({"symbol": symbol, "date": day, "category": int(row["category"]),
            "categoryLabel": XDXR_CATEGORY_MAPPING.get(row["category"], "unknown"),
            "rawFields": {key: row[key] for key in ("hongli_panqianliutong", "peigujia_qianzongguben", "songgu_qianzongguben", "peigu_houzongguben")}})
    write(OUT / "cohort-records.json", {"schema": "local-action-cache-cohort-records-v1", "rows": selected,
        "sourceSha256": plan["sourceSha256"], "readerSha256": plan["readerSha256"], "notEntitlementInstructions": True})
    categories = {str(k): int(v) for k, v in df["category"].value_counts().to_dict().items()}
    examples = [r for r in selected if r["symbol"] in ("sh688195", "sh688200") and r["category"] == 1 and r["date"].startswith("2026")]
    write(OUT / "completion.json", {"status": "completed", "completedAt": datetime.now(timezone.utc).isoformat(),
        "decodedRecords": len(rows), "cohortRows": len(selected), "categoryCountsAll": categories,
        "examples": examples, "sourceUnchanged": True, "networkRequests": 0,
        "elapsedSeconds": time.monotonic() - start, "fullRightsLedgerComplete": False,
        "allRecordsSha256": sha(OUT / "all-records.json.gz"), "cohortRecordsSha256": sha(OUT / "cohort-records.json")})
    print(json.dumps({"state": "completed", "cohortRows": len(selected), "seconds": round(time.monotonic() - start, 3), "examples": examples}), flush=True)


if __name__ == "__main__":
    main()
