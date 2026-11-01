"""Archive the two local market-wide index turnover series, with official checks.

TDX index amount is a provider proxy for the broad exchange turnover. It must
not be described as a certified historical all-A cash-auction total.
"""
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import struct

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/market-turnover-proxy-v1"
OFFICIAL = ROOT / "artifacts/strategy-search/market-turnover-official-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def main():
    OUT.mkdir(exist_ok=False)
    sources, records, checks = {}, {}, []
    for market, symbol in (("sh", "sh000001"), ("sz", "sz399106")):
        source = Path("E:/TDX/vipdoc") / market / "lday" / (symbol + ".day")
        copied = OUT / (symbol + ".day")
        copied.write_bytes(source.read_bytes())
        if copied.stat().st_size % 32:
            raise ValueError("Invalid TDX record length")
        values = {}
        for raw in struct.iter_unpack("<IIIIIfII", copied.read_bytes()):
            key = str(raw[0])
            day = key[:4] + "-" + key[4:6] + "-" + key[6:]
            if day in values or raw[5] <= 0:
                raise ValueError("Duplicate or invalid source amount")
            values[day] = float(raw[5])
        sources[market], records[market] = ref(copied), values
        errors = []
        for path in sorted((OFFICIAL / "raw" / market).glob("*.json")):
            document = json.loads(path.read_text("utf-8"))
            day = document["date"]
            if market == "sh":
                rows = {r["PRODUCT_CODE"]: r for r in document["response"]["result"]}
                official = float(sum(Decimal(rows[k]["TRADE_AMT"]) for k in ("01", "03")) * 100_000_000)
            else:
                import html
                rows = {html.unescape(r["lbmc"]).strip(): r for r in document["response"][0]["data"]}
                official = float(sum(Decimal(rows[k]["cjje"].replace(",", "")) for k in ("主板A股", "创业板A股")) * 100_000_000)
            if day in values:
                error = (values[day] / official - 1) * 100
                errors.append(abs(error))
                checks.append({"exchange": market, "date": day, "proxyCny": values[day], "officialAShareCny": official,
                               "differencePct": error, "officialSource": ref(path)})
        print(market, "official checks", len(errors), "median absolute error pct", statistics.median(errors) if errors else None,
              "max", max(errors) if errors else None, flush=True)
    with gzip.open(ROOT / "artifacts/strategy-search/board-coverage-reclaim-v1/2024-2025/star.account-data.json.gz", "rt", encoding="utf-8") as f:
        calendar = json.load(f)["calendar"]
    days = [d for d in calendar if "2023-11-20" <= d <= "2025-12-31"]
    missing = [d for d in days if d not in records["sh"] or d not in records["sz"]]
    if missing:
        raise ValueError("Market proxy misses source-calendar dates: " + str(missing[:5]))
    rows = [{"date": d, "shAmountCny": records["sh"][d], "szAmountCny": records["sz"][d],
             "totalAmountCny": records["sh"][d] + records["sz"][d], "sources": sources} for d in days]
    document = {"schema": "tdx-broad-sh-sz-turnover-proxy-v1", "passed": True, "failures": [], "expectedDates": len(days), "rows": rows,
                "completedAt": datetime.now(timezone.utc).isoformat(), "builder": ref(Path(__file__)), "sources": sources,
                "officialChecks": checks, "proxy": True, "pointInTimeArchive": False, "certified": False,
                "meaning": "Local provider broad Shanghai/Shenzhen market turnover proxy from index amount fields; not the sum of surviving research securities. Index amount differs slightly from official A-share-only totals. Beijing excluded. No net-inflow or auction-capacity claim.",
                "officialDownloadInterruption": "SZSE requests began RemoteDisconnected after75 archived dates. Stopped batch rather than fill unknown official amounts. SSE successful records retained. Proxy results cannot certify official-total conditions."}
    with (OUT / "completion.json").open("x", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({"passed": True, "dates": len(rows), "officialCheckCount": len(checks), "receipt": str(OUT / 'completion.json')}))


if __name__ == "__main__":
    main()
