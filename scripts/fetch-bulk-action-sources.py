"""Frozen-cohort, serial, bounded public dividend/bonus census acquisition.

EM supplies cross-market discovery. SSE supplies independent exchange tables.
Neither empty responses nor complete pagination certify all real-world events.
There are no price queries, strategy runs, retries or anti-bot workarounds.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/action-source-coverage-v1/acquisition"
START, END = "2023-01-01", "2026-10-15"
PAGE_SIZE = 500
EM_COLUMNS = ["SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "REPORT_DATE", "BONUS_IT_RATIO",
    "BONUS_RATIO", "IT_RATIO", "PRETAX_BONUS_RMB", "PLAN_NOTICE_DATE", "EQUITY_RECORD_DATE",
    "EX_DIVIDEND_DATE", "ASSIGN_PROGRESS", "IMPL_PLAN_PROFILE", "NOTICE_DATE", "PUBLISH_DATE"]


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path)}


def read(path):
    return json.loads(path.read_text("utf-8-sig"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    cohort, pins = [], []
    plan52_path = ROOT / "artifacts/strategy-search/reclaim-2026-replication-v1/plan.json"
    plan52 = read(plan52_path)
    input_path = ROOT / "artifacts/strategy-search/reclaim-2026-replication-v1/input-validation.json"
    inputs = read(input_path)
    for group in inputs["groups"]:
        source = group["sourceProof"]
        path = ROOT / source["path"]
        if digest(path) != source["sha256"]:
            raise ValueError("Frozen source-proof digest mismatch")
        proof = read(path)
        if not proof["passed"] or proof["missingCurrentFiles"]:
            raise ValueError("Frozen cohort is not fully accounted for")
        cohort.extend({"symbol": row["symbol"], "board": group["board"]} for row in proof["prefixes"])
        pins.append(ref(path))
    if len({row["symbol"] for row in cohort}) != len(cohort):
        raise ValueError("Overlapping board membership")
    write(OUT / "cohort.json", {"members": sorted(cohort, key=lambda row: row["symbol"]), "proofs": pins,
        "selection": "all frozen study members; no winner/holding selection", "pointInTimeUniverseCertified": False})
    groups = [{"id": "em", "provider": "eastmoney", "maximumPages": 80,
        "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
        "params": {"reportName": "RPT_SHAREBONUS_DET", "columns": ",".join(EM_COLUMNS), "source": "WEB", "client": "WEB",
            "sortColumns": "EX_DIVIDEND_DATE,SECURITY_CODE,REPORT_DATE", "sortTypes": "1,1,1", "pageSize": str(PAGE_SIZE),
            "filter": f"(EX_DIVIDEND_DATE>='{START}')(EX_DIVIDEND_DATE<='{END}')"}}]
    for year in (2023, 2024, 2025, 2026):
        for kind in ("bonus", "cash"):
            groups.append({"id": f"sse-{kind}-{year}", "provider": "sse", "maximumPages": 15,
                "url": "https://query.sse.com.cn/commonQuery.do", "year": year, "kind": kind,
                "params": {"isPagination": "true", "pageHelp.pageSize": str(PAGE_SIZE), "pageHelp.cacheSize": "1",
                    **({"sqlId": "COMMON_SSE_SJ_GPSJ_FHSG_SG_L", "SEARCH_YEAR": str(year)} if kind == "bonus" else
                       {"sqlId": "COMMON_SSE_SJ_GPSJ_FHSG_SSGSFHQK_L", "CONDITION_AG": "1", "A_REG_DATE": str(year)})}})
    protocol = {"schema": "bulk-action-census-acquisition-v1", "registeredAt": now(), "sourceStart": START, "sourceEnd": END,
        "goalSplit": "2024-09-24", "members": len(cohort), "cohort": ref(OUT / "cohort.json"),
        "priorStudyPlan": ref(plan52_path), "inputValidation": ref(input_path), "groups": groups,
        "sourcePins": [ref(Path(__file__)), ref(ROOT / "artifacts/strategy-search/action-source-coverage-v1/probe/em-688200.json"),
            ref(ROOT / "artifacts/strategy-search/action-source-coverage-v1/probe3/sse-search-current.body")],
        "rationale": "2023 onward covers earlier than120-session warmup for2024 start; retain full frozen cohorts and2026 extensions.",
        "maximumWireRequests": 208, "serial": True, "minimumDelaySeconds": .5, "retries": 0,
        "timeoutSeconds": [8, 25], "stopAtFirstSourceFailure": True, "maximumResponseBytes": 8_000_000,
        "futureReturnFieldsRequested": False, "marketPriceQueries": 0, "newStrategyTrials": 0, "newMarketAccounts": 0,
        "limitations": ["pagination completeness is provider-response scope, not a no-events proof",
            "EM discovery lacks cash-pay/share-listing/virtual-reference terms; never infer these dates or zero missing shares",
            "current tables may reflect revisions; publication/PIT and complete unsupported-event coverage require separate checks",
            "SSE tables are independent crosschecks for SH only; SZ remains a coverage limitation"]}
    write(OUT / "plan.json", protocol)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.sse.com.cn/"})
    summaries, total_requests, failure = [], 0, None
    try:
        for group in groups:
            entries, receipts, expected_count, expected_pages = [], [], None, None
            page = 1
            while True:
                params = dict(group["params"])
                if group["provider"] == "eastmoney":
                    params["pageNumber"] = str(page)
                else:
                    params.update({"pageHelp.pageNo": str(page), "pageHelp.beginPage": str(page), "pageHelp.endPage": str(page)})
                receipt = {"group": group["id"], "page": page, "url": group["url"], "params": params, "requestedAt": now()}
                total_requests += 1
                if total_requests > protocol["maximumWireRequests"]:
                    raise ValueError("Wire-request bound exceeded")
                response = session.get(group["url"], params=params, timeout=(8, 25))
                raw = response.content
                if len(raw) > protocol["maximumResponseBytes"]:
                    raise ValueError("Response byte bound exceeded")
                raw_path = OUT / "raw" / group["id"] / f"page{page:03d}.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(raw)
                receipt.update(statusCode=response.status_code, contentType=response.headers.get("content-type"),
                    raw=ref(raw_path), bytes=len(raw), receivedAt=now())
                # Archive first even when a challenge/error was returned. Do not
                # execute its body, change routes or retry the failed request.
                write(OUT / "receipts" / group["id"] / f"page{page:03d}.json", receipt)
                response.raise_for_status()
                data = json.loads(raw)
                if group["provider"] == "eastmoney":
                    if data.get("success") is not True or data.get("code") != 0:
                        raise ValueError("Provider returned failure")
                    result = data["result"]
                    rows, count, pages = result["data"], int(result["count"]), int(result["pages"])
                    if any(set(row) != set(EM_COLUMNS) for row in rows):
                        raise ValueError("EM requested-column/schema drift")
                else:
                    rows, metadata = data["result"], data["pageHelp"]
                    count, pages = int(metadata["total"]), int(metadata["pageCount"])
                    if int(metadata["pageNo"]) != page or int(metadata["pageSize"]) != PAGE_SIZE:
                        raise ValueError("Exchange page coordinates mismatch")
                    if metadata.get("data") is not None and metadata["data"] != rows:
                        raise ValueError("Exchange duplicate data views disagree")
                if not isinstance(rows, list) or not 0 < pages <= group["maximumPages"] or pages != math.ceil(count / PAGE_SIZE):
                    raise ValueError("Invalid pagination bounds")
                if expected_count is None:
                    expected_count, expected_pages = count, pages
                if (expected_count, expected_pages) != (count, pages) or len(rows) != min(PAGE_SIZE, count - (page - 1) * PAGE_SIZE):
                    raise ValueError("Moving counts, truncated or repeated page")
                entries.extend(rows)
                receipts.append(ref(OUT / "receipts" / group["id"] / f"page{page:03d}.json"))
                if page == pages:
                    break
                page += 1
                time.sleep(.5)
            row_hashes = [hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for row in entries]
            if len(set(row_hashes)) != len(row_hashes) or len(entries) != expected_count:
                raise ValueError("Duplicate rows or row-count mismatch")
            summary = {"group": group["id"], "completedAt": now(), "pages": expected_pages, "rows": expected_count,
                "providerPaginationComplete": True, "rowDigestsUnique": True, "receipts": receipts,
                "fullEconomicCoverageCertified": False}
            write(OUT / (group["id"] + ".rows.json"), {"provider": group["provider"], "rows": entries, "proof": summary})
            summaries.append(summary)
            print(json.dumps({"completed": group["id"], "pages": expected_pages, "rows": expected_count, "requests": total_requests}), flush=True)
            time.sleep(.5)
    except Exception as exc:
        failure = {"error": type(exc).__name__, "message": str(exc)[:350], "group": group["id"], "page": page}
        print(json.dumps({"stopped": failure, "requests": total_requests}), flush=True)
    finally:
        session.close()
        write(OUT / "completion.json", {"completedAt": now(), "status": "completed" if failure is None else "stopped-at-source-failure",
            "requestsAttempted": total_requests, "completedGroups": summaries, "expectedGroups": len(groups),
            "allGroupsComplete": failure is None and len(summaries) == len(groups), "failure": failure,
            "newMarketAccounts": 0, "newStrategyTrials": 0, "fullEconomicCoverageCertified": False})
    if failure is not None:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
