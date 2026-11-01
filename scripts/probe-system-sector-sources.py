"""Bounded read-only sector-source intake; never backfill current membership."""
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1/source-intake-v1"
URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
TDX_ROOT = Path("E:/TDX")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, obj):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def parse_xls(path):
    import pandas as pd
    # Bundled pandas has no BIFF reader. The installed pure-Python xlrd supplies
    # only that missing capability; no dependency directory is modified.
    fallback = None
    if importlib.util.find_spec("xlrd") is None:
        fallback = Path("C:/Users/25647/AppData/Local/Programs/Python/Python313/Lib/site-packages/xlrd")
        spec = importlib.util.spec_from_file_location("xlrd", fallback / "__init__.py", submodule_search_locations=[str(fallback)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["xlrd"] = module
        spec.loader.exec_module(module)
    frame = pd.read_excel(path, engine="xlrd", dtype=str)
    frame = frame.astype(object).where(pd.notna(frame), None)
    if not len(frame) or len(frame.columns) < 3:
        raise ValueError("Empty or unexpected industry history table")
    records = frame.to_dict(orient="records")
    target = OUT / "sw-history.raw-records.json.gz"
    with gzip.open(target, "xt", encoding="utf-8") as handle:
        json.dump({"columns": list(frame.columns), "records": records}, handle, ensure_ascii=False, allow_nan=False)
    dates = {}
    for name in frame.columns:
        if any(word in str(name) for word in ("日期", "时间", "date", "time")):
            values = pd.to_datetime(frame[name], errors="coerce")
            dates[str(name)] = {"parsed": int(values.notna().sum()), "missingOrUnparsed": int(values.isna().sum()),
                "minimum": str(values.min()) if values.notna().any() else None,
                "maximum": str(values.max()) if values.notna().any() else None}
    return {"status": "parsed", "rows": len(frame), "columns": list(frame.columns), "dateFields": dates,
        "duplicateWholeRows": int(frame.duplicated().sum()), "rawParsedRecords": {"path": target.relative_to(ROOT).as_posix(), "sha256": sha(target)},
        "parserRuntime": sys.executable, "biffReaderFallback": str(fallback) if fallback else None,
        "effectiveVsPublicAvailabilitySemanticsVerified": False, "membershipReplayPermitted": False,
        "reason": "Date field names alone do not prove public availability or revision semantics; classification-code mapping and complete change history require validation."}


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    start = datetime.now(timezone.utc).isoformat()
    index_rows = []
    for path in sorted((TDX_ROOT / "vipdoc/sh/lday").glob("sh88*.day")):
        data = path.read_bytes()
        valid_size = len(data) > 0 and len(data) % 32 == 0
        dates = [str(row[0]) for row in struct.iter_unpack("<IIIIIfII", data)] if valid_size else []
        index_rows.append({"symbol": path.stem, "path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "rows": len(dates), "firstDateRaw": dates[0] if dates else None, "lastDateRaw": dates[-1] if dates else None,
            "datesStrictlyOrdered": dates == sorted(set(dates)) if dates else False})
    current_path = TDX_ROOT / "T0002/hq_cache/tdxhy.cfg"
    current = {"path": str(current_path), "exists": current_path.is_file(), "historicalMembershipPermitted": False,
        "meaning": "Current local classification snapshot; no verified historical effective/known dates."}
    if current_path.is_file():
        current.update(bytes=current_path.stat().st_size, sha256=sha(current_path),
            observedAt=start, modifiedAt=datetime.fromtimestamp(current_path.stat().st_mtime, timezone.utc).isoformat(),
            nonemptyLines=sum(bool(s.strip()) for s in current_path.read_text(encoding="gb18030", errors="replace").splitlines()))
    official = {"url": URL, "attempts": 1, "requestedAt": start, "noRetriesOrAccessBypass": True}
    try:
        request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise ValueError("Source exceeds bounded20MB intake")
            official.update(statusCode=response.status, finalUrl=response.url, contentType=response.headers.get("Content-Type"),
                lastModified=response.headers.get("Last-Modified"), receivedAt=datetime.now(timezone.utc).isoformat())
        target = OUT / "sw-industry-history.source.xls"
        with target.open("xb") as handle:
            handle.write(raw)
        official.update(status="archived", bytes=len(raw), source={"path": target.relative_to(ROOT).as_posix(), "sha256": sha(target)})
        try:
            official["inspection"] = parse_xls(target)
        except Exception as error:
            official["inspection"] = {"status": "notParsed", "errorType": type(error).__name__, "message": str(error), "membershipReplayPermitted": False}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        official.update(status="unavailable", errorType=type(error).__name__, message=str(error))
    write(OUT / "readout.json", {"schema": "system-sector-source-intake-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "builder": {"path": Path(__file__).relative_to(ROOT).as_posix(), "sha256": sha(Path(__file__))},
        "localSectorIndexHistory": index_rows, "localCurrentIndustrySnapshot": current, "officialHistoryCandidate": official,
        "newMarketAccounts": 0, "newStrategyTrials": 0, "completeSystemValidated": False, "targetAchieved": False,
        "nextAction": "Validate historical classification code hierarchy, effective/availability and revision semantics before building sector member breadth and stock-role panels."})
    print(json.dumps({"localIndexFiles": len(index_rows), "officialHistory": official["status"],
        "inspection": official.get("inspection", {}).get("status"), "rows": official.get("inspection", {}).get("rows"), "membershipReplayPermitted": False}))


if __name__ == "__main__":
    main()
