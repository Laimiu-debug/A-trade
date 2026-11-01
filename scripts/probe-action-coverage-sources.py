"""Bounded public source discovery; never decode/run anti-bot challenges.

This probe discovers bulk pagination/fields before a complete-cohort acquisition
contract is frozen. It neither fetches stock prices nor opens market accounts.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/action-source-coverage-v1/probe"


def write(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    requests_plan = [
        {"id": "em-688200", "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
         "params": {"sortColumns": "REPORT_DATE", "sortTypes": "-1", "pageSize": "500", "pageNumber": "1",
            "reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "source": "WEB", "client": "WEB",
            "filter": '(SECURITY_CODE="688200")'}},
        {"id": "sse-bonus-index", "url": "https://www.sse.com.cn/market/stockdata/dividends/bonus/", "params": {}},
        {"id": "sse-cash-index", "url": "https://www.sse.com.cn/market/stockdata/dividends/bonus/index_his.shtml", "params": {}},
    ]
    plan = {"schema": "action-source-bulk-discovery-probe-v1", "registeredAt": datetime.now(timezone.utc).isoformat(),
        "requests": requests_plan, "maximumRequests": len(requests_plan), "serial": True, "retries": 0,
        "requestTimeoutSeconds": [8, 25], "proxy": "requests system configuration; no alternate routing",
        "sourceCodeSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "actualMarketAccounts": 0, "strategyTrials": 0, "fullCoverageCertified": False}
    write(OUT / "plan.json", plan)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.sse.com.cn/"})
    rows = []
    for item in requests_plan:
        record = {"id": item["id"], "requestedAt": datetime.now(timezone.utc).isoformat(), "url": item["url"]}
        try:
            response = session.get(item["url"], params=item["params"], timeout=(8, 25))
            content = response.content
            path = OUT / (item["id"] + ".body")
            path.write_bytes(content)
            record.update(statusCode=response.status_code, bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                contentType=response.headers.get("Content-Type"), archive=path.relative_to(ROOT).as_posix())
            if item["id"].startswith("em-"):
                data = response.json()
                write(OUT / (item["id"] + ".json"), data)
                result = data.get("result") or {}
                entries = result.get("data") or []
                record.update(success=data.get("success"), pages=result.get("pages"), count=result.get("count"),
                    rows=len(entries), fieldNames=list(entries[0]) if entries else [],
                    datedExamples=[r for r in entries if str(r.get("REPORT_DATE", "")).startswith(("2024", "2025"))])
            else:
                # Save source only. Inspection of literal linked JS is a later
                # bounded operation; scripts are never evaluated here.
                text = response.text
                record.update(scriptCount=text.lower().count("<script"), usableHtml=("上市公司送股情况" in text))
        except Exception as exc:
            record.update(error=type(exc).__name__, message=str(exc)[:300])
        rows.append(record)
        write(OUT / (item["id"] + ".receipt.json"), record)
        print(json.dumps({k: record[k] for k in ("id", "statusCode", "bytes", "success", "rows", "usableHtml", "error") if k in record}), flush=True)
    session.close()
    write(OUT / "completion.json", {"completedAt": datetime.now(timezone.utc).isoformat(), "requestsAttempted": len(rows),
        "receipts": rows, "noEventsProven": False, "fullCoverageCertified": False, "actualMarketAccounts": 0})


if __name__ == "__main__":
    main()
