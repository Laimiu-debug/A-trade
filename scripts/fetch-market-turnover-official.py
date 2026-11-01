"""Archive daily Shanghai/Shenzhen A-share turnover, excluding funds/bonds/B shares.

Historical downloads are revised public records, not a point-in-time archive.
Every observed date and source unit is checked; missing observations stay errors.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import html
import json
from pathlib import Path
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/market-turnover-official-v1"
SOURCE = ROOT / "artifacts/strategy-search/board-coverage-reclaim-v1/2024-2025/star.account-data.json.gz"


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def number(value):
    result = Decimal(value.replace(",", ""))
    if not result.is_finite() or result <= 0:
        raise ValueError("Positive finite turnover required")
    return result


def parse(exchange, day, data):
    if exchange == "sh":
        rows = {r["PRODUCT_CODE"]: r for r in data["result"]}
        values = []
        for key in ("01", "03"):
            if rows[key]["TRADE_DATE"] != day.replace("-", ""):
                raise ValueError("SSE response date differs")
            values.append(number(rows[key]["TRADE_AMT"]))
        return int(sum(values) * 100_000_000)
    report = data[0]
    if report["metadata"]["subname"] != day:
        raise ValueError("SZSE response date differs")
    if "亿元" not in report["metadata"]["cols"]["cjje"]:
        raise ValueError("SZSE turnover unit changed")
    rows = {html.unescape(r["lbmc"]).strip(): r for r in report["data"]}
    return int(sum(number(rows[k]["cjje"]) for k in ("主板A股", "创业板A股")) * 100_000_000)


def fetch_day(day):
    session = requests.Session()
    session.trust_env = False
    refs, amounts = {}, {}
    for exchange in ("sh", "sz"):
        path = OUT / "raw" / exchange / (day + ".json")
        if path.exists():
            receipt = json.loads(path.read_text("utf-8"))
            data = receipt["response"]
        else:
            if exchange == "sh":
                url = "https://query.sse.com.cn/commonQuery.do"
                params = {"sqlId": "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C", "PRODUCT_CODE": "01,02,03,11,17",
                          "type": "inParams", "SEARCH_DATE": day}
                referer = "https://www.sse.com.cn/market/stockdata/overview/day/"
            else:
                url = "https://www.szse.cn/api/report/ShowReport/data"
                params = {"SHOWTYPE": "JSON", "CATALOGID": "1803_sczm", "TABKEY": "tab1", "txtQueryDate": day}
                referer = "https://www.szse.cn/market/overview/index.html"
            attempts = []
            for attempt in range(2):
                try:
                    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": referer}, timeout=22)
                    response.raise_for_status()
                    data = response.json()
                    parse(exchange, day, data)
                    attempts.append({"attempt": attempt + 1, "statusCode": response.status_code, "passed": True})
                    break
                except Exception as exc:
                    attempts.append({"attempt": attempt + 1, "passed": False, "error": type(exc).__name__ + ": " + str(exc)[:200]})
                    if attempt:
                        return {"date": day, "failedExchange": exchange, "attempts": attempts}
                    time.sleep(.6)
            receipt = {"exchange": exchange, "date": day, "url": response.url,
                       "downloadedAt": datetime.now(timezone.utc).isoformat(), "attempts": attempts, "response": data}
            save(path, receipt)
            time.sleep(.10)
        amounts[exchange] = parse(exchange, day, data)
        refs[exchange] = {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}
    return {"date": day, "shAmountCny": amounts["sh"], "szAmountCny": amounts["sz"],
            "totalAmountCny": sum(amounts.values()), "sources": refs}


def main():
    with gzip.open(SOURCE, "rt", encoding="utf-8") as f:
        calendar = json.load(f)["calendar"]
    days = [d for d in calendar if "2023-11-20" <= d <= "2025-12-31"]
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_day, d): d for d in days}
        for i, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if i % 30 == 0:
                print(json.dumps({"completedDates": i, "totalDates": len(days), "failures": sum("failedExchange" in r for r in rows)}), flush=True)
    rows.sort(key=lambda r: r["date"])
    failures = [r for r in rows if "failedExchange" in r]
    receipt = {"schema": "official-sh-sz-a-share-turnover-v1", "scope": "SSE main A + STAR, SZSE main A + ChiNext A; excludes B shares, funds, bonds and Beijing",
               "sourceUnit": "1e8 CNY, rounded to 0.01 source units", "outputUnit": "CNY", "calendarSourceSha256": sha(SOURCE),
               "builderSha256": sha(Path(__file__)), "completedAt": datetime.now(timezone.utc).isoformat(),
               "expectedDates": len(days), "rows": rows, "failures": failures, "passed": not failures,
               "pointInTimeArchive": False, "publicationTimeVerified": False,
               "availabilityAssumption": "EOD observations used only after date T close for earliest T+1 opening attempt; historical publication timestamps not archived"}
    name = "completion.json" if not failures else "incomplete-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json"
    save(OUT / name, receipt)
    print(json.dumps({"passed": not failures, "dates": len(rows), "failures": len(failures), "receipt": str(OUT / name)}), flush=True)


if __name__ == "__main__":
    main()
