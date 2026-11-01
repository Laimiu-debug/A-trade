"""Inspect the archived public industry history; no membership admission yet."""
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1/windows-trust-probe-v1"


def main():
    import pandas as pd
    fallback = None
    if importlib.util.find_spec("xlrd") is None:
        fallback = Path("C:/Users/25647/AppData/Local/Programs/Python/Python313/Lib/site-packages/xlrd")
        spec = importlib.util.spec_from_file_location("xlrd", fallback / "__init__.py", submodule_search_locations=[str(fallback)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["xlrd"] = module
        spec.loader.exec_module(module)
    receipt = json.loads((OUT / "receipt.json").read_text(encoding="utf-8-sig"))
    source = OUT / "sw-industry-history.source.xls"
    if receipt["status"] != "archived" or hashlib.sha256(source.read_bytes()).hexdigest() != receipt["sha256"]:
        raise ValueError("Archived official source receipt mismatch")
    frame = pd.read_excel(source, engine="xlrd", dtype=str)
    frame = frame.astype(object).where(pd.notna(frame), None)
    records = frame.to_dict(orient="records")
    if len(records) < 1:
        raise ValueError("Industry file empty")
    with gzip.open(OUT / "raw-records.json.gz", "xt", encoding="utf-8") as handle:
        json.dump({"columns": list(frame.columns), "records": records}, handle, ensure_ascii=False, allow_nan=False)
    columns = {}
    for name in frame.columns:
        values = frame[name]
        item = {"nonempty": int(values.notna().sum()), "distinctNonempty": int(values.nunique(dropna=True))}
        if any(word in str(name) for word in ("日期", "时间", "date", "time")):
            dates = pd.to_datetime(values, errors="coerce")
            item.update(parsedDates=int(dates.notna().sum()), minimum=str(dates.min()), maximum=str(dates.max()),
                afterCurrentDay=int((dates > pd.Timestamp("2026-10-18")).sum()))
        columns[str(name)] = item
    value = {"source": {"path": source.relative_to(ROOT).as_posix(), "sha256": receipt["sha256"], "url": receipt["url"]},
        "receivedAt": receipt["requestedAt"], "inspectedAt": datetime.now(timezone.utc).isoformat(), "rows": len(frame), "columns": columns,
        "duplicateWholeRows": int(frame.duplicated().sum()), "parserRuntime": sys.executable, "missingBiffReaderSuppliedByPurePythonXlrd": str(fallback) if fallback else None,
        "historicalPublicAvailabilityAndRevisionSemanticsVerified": False, "membershipReplayPermitted": False,
        "newMarketAccounts": 0, "newStrategyTrials": 0,
        "nextAction": "Verify field meanings and classification hierarchy, then independently validate effective and known-date event replay against complete original records."}
    with (OUT / "inspection.json").open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
