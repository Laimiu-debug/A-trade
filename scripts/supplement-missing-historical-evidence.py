"""Single frozen data-quality probe of eight missing historical-universe stocks.

No returns, trading rules, user database access, replacement sample or retries.
Freeze first; the separate --execute command performs one bounded network batch.
The original supplement transport and raw page recording are preserved.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "artifacts" / "strategy-search"
OUTPUT = BASE / "evidence-missing-history-v1"
ORIGINAL = ROOT / "scripts" / "supplement-strategy-evidence.py"
AUDIT = BASE / "historical-universe-coverage-v1.json"
DATES = ("2022-01-04", "2022-07-01", "2023-01-03", "2023-12-29")
START, END = "2021-08-02", "2023-12-31"
SEED = "a-trade-missing-historical-coverage-v1"
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST"
MAX_ROWS, MAX_SENDS = 10000, 150


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write(path, value, *, frozen=False):
    content = encode(value)
    if frozen:
        with path.open("xb") as stream:
            stream.write(content)
    else:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
    return hashlib.sha256(content).hexdigest()


def selected_symbols(symbols):
    require(len(symbols) == len(set(symbols)), "Duplicate missing symbols")
    require(all(re.fullmatch(r"(?:sh|sz)\d{6}", s) for s in symbols), "Invalid undotted symbol")
    return sorted(symbols, key=lambda s: (hashlib.sha256((SEED + ":" + s).encode("utf-8")).hexdigest(), s))[:8]


def requests_for(code):
    stem = code.replace(".", "")
    return [
        {"id": stem + "-basic", "api": "query_stock_basic", "parameters": {"code": code}},
        {"id": stem + "-daily", "api": "query_history_k_data_plus", "parameters": {
            "code": code, "fields": DAILY_FIELDS, "start_date": START, "end_date": END,
            "frequency": "d", "adjustflag": "3"}},
        {"id": stem + "-factors", "api": "query_adjust_factor", "parameters": {
            "code": code, "start_date": START, "end_date": END}},
        *[{"id": stem + f"-dividend-{year}", "api": "query_dividend_data", "parameters": {
            "code": code, "year": str(year), "yearType": "operate"}} for year in (2021, 2022, 2023)],
    ]


def verify_sources():
    """Reconstruct missing membership sets from hashed raw responses, offline."""
    supplement = BASE / "evidence-supplement-v1"
    audit = read(AUDIT)
    snapshot_path = BASE / "signal-baseline-v1" / "snapshot.json"
    snapshot = read(snapshot_path)
    original_protocol = read(supplement / "protocol.json")
    original_manifest = read(supplement / "manifest.json")
    original_requests = read(supplement / "request-manifest.json")
    source_paths = [AUDIT, ROOT / "scripts" / "audit-signal-historical-universe.py", snapshot_path,
                    ORIGINAL, supplement / "protocol.json", supplement / "manifest.json",
                    supplement / "request-manifest.json"]
    require(sha(source_paths[1]) == audit["scriptSha256"], "Audit implementation changed")
    require(sha(snapshot_path) == audit["baselineSnapshotSha256"], "Baseline snapshot hash mismatch")
    require(snapshot["fingerprint"] == audit["baselineFingerprint"], "Baseline fingerprint mismatch")
    require(sha(ORIGINAL) == original_protocol["scriptSha256"], "Original supplement implementation changed")
    require(sha(supplement / "protocol.json") == original_manifest["protocol"]["sha256"] == original_requests["protocolSha256"], "Original protocol hash mismatch")
    require(sha(supplement / "request-manifest.json") == original_manifest["requestManifest"]["sha256"], "Original request manifest hash mismatch")
    selected = set(snapshot["symbols"])
    require(len(selected) == audit["frozenLocalSampleCount"], "Baseline symbol count mismatch")
    snapshots = {x["date"]: x for x in audit["dailySnapshots"]}
    records = {r["parameters"]["day"]: r for r in original_manifest["requests"] if r["api"] == "query_all_stock"}
    require(set(snapshots) == set(records) == set(DATES), "Four snapshot dates mismatch")
    historical, missing_union, details = set(), set(), []
    for day in DATES:
        record, observed = records[day], snapshots[day]
        raw_path = supplement / record["path"]
        source_paths.append(raw_path)
        require(sha(raw_path) == record["sha256"] == observed["membershipResponseSha256"], "Membership raw hash mismatch")
        payload = read(raw_path)
        require(record["complete"] is True and payload["complete"] is True and payload["errorCode"] == "0", "Incomplete membership source")
        require(payload["pages"] == record["pages"], "Membership page records mismatch")
        page_rows = []
        for page in record["pages"]:
            page_path = supplement / page["path"]
            source_paths.append(page_path)
            require(sha(page_path) == page["sha256"], "Membership page hash mismatch")
            content = read(page_path)
            require(content["fields"] == payload["fields"] and len(content["rows"]) == page["rowCount"], "Membership page shape mismatch")
            page_rows.extend(content["rows"])
        require(page_rows == payload["rows"], "Membership aggregate does not match pages")
        require(all(len(row) == len(payload["fields"]) for row in page_rows), "Membership row shape mismatch")
        code_index = payload["fields"].index("code")
        members = {row[code_index].replace(".", "") for row in page_rows
                   if re.fullmatch(original_protocol["selection"]["codeRegex"], row[code_index])}
        missing = members - selected
        require(missing == {item["symbol"] for item in observed["missing"]}, "Daily missing set mismatch")
        require(len(members) == observed["providerMainBoardCount"] and len(missing) == observed["missingFromFrozenSampleCount"], "Daily set count mismatch")
        historical.update(members)
        missing_union.update(missing)
        details.append({"date": day, "members": len(members), "missing": len(missing), "membershipResponseSha256": record["sha256"]})
    require(sorted(missing_union) == audit["missingUnionSymbols"], "Missing union mismatch")
    require(len(missing_union) == audit["missingUnionCount"] == 125, "Expected exact frozen 125-symbol union")
    require(len(historical) == audit["observedMembershipUnionCount"] == original_requests["unionCount"], "Historical union count mismatch")
    require(historical == {s.replace(".", "") for s in original_requests["historicalMainBoardUnion"]}, "Historical union set mismatch")
    return {"auditSha256": sha(AUDIT), "missingUnionSymbols": sorted(missing_union),
            "missingUnionCount": len(missing_union), "dailySnapshots": details,
            "sourceFiles": [{"path": str(p.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(p)} for p in source_paths],
            "verification": "Independent raw membership minus frozen baseline sets; no current status or return used"}


def registration(source):
    selected = selected_symbols(source["missingUnionSymbols"])
    return {"schema": "frozen-missing-history-requests-v1", "frozenAt": stamp(),
            "sourceAuditSha256": source["auditSha256"], "sourceVerification": source,
            "selectionSeed": SEED, "selectionCount": 8,
            "selectionRanking": "Ascending SHA256 UTF-8(seed + ':' + exact undotted symbol), tie by symbol",
            "selected": [{"symbol": symbol, "code": symbol[:2] + "." + symbol[2:], "rank": i,
                          "selectionHash": hashlib.sha256((SEED + ":" + symbol).encode("utf-8")).hexdigest()}
                         for i, symbol in enumerate(selected, 1)],
            "requests": [r for s in selected for r in requests_for(s[:2] + "." + s[2:])],
            "automaticReplacementAllowed": False}


def freeze():
    source = verify_sources()
    registered = registration(source)
    require(len(registered["requests"]) == 48, "Expected exactly 48 requests")
    require(not OUTPUT.exists(), "Output exists; frozen batches must never be overwritten")
    OUTPUT.mkdir(parents=True)
    protocol = {"schema": "bounded-missing-history-evidence-v1", "registeredAt": stamp(),
                "provider": "baostock", "packageVersion": importlib.metadata.version("baostock"),
                "scriptSha256": sha(Path(__file__)), "originalTransportScriptSha256": sha(ORIGINAL),
                "sourceAuditSha256": source["auditSha256"], "requestCount": 48,
                "purpose": "Missing historical coverage retrieval and data quality only; no returns or strategy change",
                "limits": {"wireSendsIncludingAuthentication": MAX_SENDS, "rowsPerQuery": MAX_ROWS,
                           "socketAndRequestSeconds": 10, "queryDeadlineSeconds": 280, "wallSeconds": 300,
                           "reservedLogoutSends": 1, "singleConnection": True, "serial": True,
                           "automaticRetries": 0, "maxReplyBytes": 16 * 1024 * 1024},
                "scope": {"historyStart": START, "historyEnd": END, "dividendOperateYears": [2021, 2022, 2023]},
                "rawRecording": "Original supplement page/aggregate encoding, hashes, pagination and bounds; empty/error/short replies retained",
                "limitations": ["Current stock_basic status is not a historical eligibility label.",
                                "Sparse four-date member snapshots do not prove complete historical coverage.",
                                "No replacement, no retry, no user database, no return calculations.",
                                "Not merged into the registered 3141-stock sequence study.",
                                "Empty factors/dividends do not prove absence of corporate actions."],
                "returnsCalculated": False, "certified": False, "targetAchieved": False}
    registered["protocolSha256"] = write(OUTPUT / "protocol.json", protocol, frozen=True)
    write(OUTPUT / "request-manifest.json", registered, frozen=True)
    print(json.dumps({"frozen": True, "selected": selected_symbols(source["missingUnionSymbols"]), "requests": 48}))


def verify_frozen():
    protocol, registered = read(OUTPUT / "protocol.json"), read(OUTPUT / "request-manifest.json")
    require(sha(Path(__file__)) == protocol["scriptSha256"], "Frozen fetch script changed")
    require(importlib.metadata.version("baostock") == protocol["packageVersion"], "Provider SDK version changed")
    require(sha(OUTPUT / "protocol.json") == registered["protocolSha256"], "Frozen protocol mismatch")
    source = verify_sources()
    require(source == registered["sourceVerification"], "Frozen sources changed")
    expected = registration(source)
    for key in ("sourceAuditSha256", "selectionSeed", "selectionCount", "selectionRanking", "selected", "requests", "automaticReplacementAllowed"):
        require(registered[key] == expected[key], "Frozen registration mismatch: " + key)
    return registered


class ProbeStop(RuntimeError):
    pass


def worker():
    verify_frozen()
    socket.setdefaulttimeout(10)
    import baostock as bs
    from baostock.common import context, contants
    from baostock.util import socketutil

    started = time.monotonic()
    manifest_path = OUTPUT / "manifest.json"
    manifest = {"schema": "bounded-public-evidence-v1", "startedAt": stamp(), "status": "running",
                "protocol": {"path": "protocol.json", "sha256": hashlib.sha256((OUTPUT / "protocol.json").read_bytes()).hexdigest()},
                "wireSends": 0, "requests": [], "selectedCodes": [], "singleConnection": True,
                "returnsCalculated": False, "userWorkspaceModified": False, "baselineModified": False,
                "auth": {}, "certified": False}
    logout_mode = False

    def bounded_send(message):
        # The SDK owns serialization. No authentication message/object is saved.
        elapsed = time.monotonic() - started
        deadline = 295 if logout_mode else 280
        budget = MAX_SENDS if logout_mode else MAX_SENDS - 1
        if manifest["wireSends"] >= budget or elapsed >= deadline:
            raise ProbeStop("Reserved send or elapsed-time bound reached")
        manifest["wireSends"] += 1
        write(manifest_path, manifest)
        sock = getattr(context, "default_socket", None)
        if sock is None:
            raise ProbeStop("SDK socket unavailable")
        request_deadline = min(started + deadline, time.monotonic() + 10)
        sock.settimeout(max(.001, request_deadline - time.monotonic()))
        sock.sendall((message + "\n").encode("utf-8"))
        data = bytearray()
        while not data.endswith(b"<![CDATA[]]>\n"):
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeStop("Bounded reply timeout")
            sock.settimeout(remaining)
            chunk = sock.recv(8192)
            if not chunk:
                raise ProbeStop("Provider closed the connection before reply completion")
            data.extend(chunk)
            if len(data) > 16 * 1024 * 1024:
                raise ProbeStop("Reply byte bound reached")
        header = bytes(data[:contants.MESSAGE_HEADER_LENGTH]).decode("utf-8")
        parts = header.split(contants.MESSAGE_SPLIT)
        if len(parts) < 3:
            raise ProbeStop("Invalid SDK reply header")
        if parts[1] in contants.COMPRESSED_MESSAGE_TYPE_TUPLE:
            length = int(parts[2])
            decoder = zlib.decompressobj()
            body = decoder.decompress(bytes(data[contants.MESSAGE_HEADER_LENGTH:contants.MESSAGE_HEADER_LENGTH + length]), 16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024 or decoder.unconsumed_tail or not decoder.eof:
                raise ProbeStop("Decompressed reply bound or framing error")
            return header + body.decode("utf-8")
        return bytes(data).decode("utf-8")

    socketutil.send_msg = bounded_send
    (OUTPUT / "raw").mkdir()
    (OUTPUT / "pages").mkdir()
    write(manifest_path, manifest)

    def query(request):
        record = {**request, "status": "running", "startedAt": stamp(), "pages": [], "wireSendsBefore": manifest["wireSends"]}
        manifest["requests"].append(record)
        write(manifest_path, manifest)
        rows, fields = [], []
        code, error_message, exception = None, None, None
        complete, truncated, terminal_reason = False, False, None
        page_count = 0
        result = None
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = getattr(bs, request["api"])(**request["parameters"])
            seen_pages = set()
            while True:
                code, error_message = result.error_code, result.error_msg
                current_fields = list(result.fields)
                if fields and current_fields != fields:
                    raise ProbeStop("Provider fields changed across pages")
                fields = current_fields
                page_number = str(result.cur_page_num)
                if page_number in seen_pages:
                    raise ProbeStop("Provider page did not advance")
                seen_pages.add(page_number)
                source_rows = result.data if isinstance(result.data, list) else []
                available = MAX_ROWS - len(rows)
                saved_rows = source_rows[:available]
                page_count += 1
                page = {"provider": "baostock", "api": request["api"], "parameters": request["parameters"],
                        "retrievedAt": stamp(), "errorCode": code, "errorMessage": error_message,
                        "fields": fields, "rows": saved_rows, "sdkPageNumber": page_number,
                        "sdkPerPageCount": result.per_page_count, "receivedRows": len(source_rows),
                        "rowBoundReached": len(rows) + len(saved_rows) >= MAX_ROWS,
                        "pageRowsTruncated": len(saved_rows) < len(source_rows)}
                filename = f"pages/{request['id']}-page-{page_count:03d}.json"
                page_hash = write(OUTPUT / filename, page, frozen=True)
                record["pages"].append({"path": filename, "sha256": page_hash, "rowCount": len(saved_rows), "sdkPageNumber": page_number})
                rows.extend(saved_rows)
                write(manifest_path, manifest)
                if code != "0":
                    terminal_reason = "providerError"
                    break
                if len(rows) >= MAX_ROWS:
                    truncated, terminal_reason = True, "rowBoundReached"
                    break
                expected_page_size = int(contants.BAOSTOCK_PER_PAGE_COUNT)
                if len(source_rows) < expected_page_size:
                    complete, terminal_reason = True, "shortOrEmptyTerminalPage"
                    break
                if len(source_rows) > expected_page_size:
                    complete, terminal_reason = True, "sdkDeclaredUnpagedOversizeReply"
                    break
                result.cur_row_num = len(source_rows)
                before = str(result.cur_page_num)
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    advanced = result.next()
                if not advanced and str(result.cur_page_num) == before:
                    code, error_message = result.error_code, result.error_msg
                    terminal_reason = "paginationDidNotAdvance"
                    break
                # A successfully received empty terminal page is preserved too.
        except Exception as error:
            exception = type(error).__name__
            terminal_reason = "exception"
        payload = {"provider": "baostock", "api": request["api"], "parameters": request["parameters"],
                   "retrievedAt": stamp(), "errorCode": code, "errorMessage": error_message,
                   "fields": fields, "rows": rows, "rowBoundReached": truncated,
                   "complete": complete and exception is None, "terminalReason": terminal_reason,
                   "exceptionType": exception, "pages": record["pages"]}
        filename = f"raw/{request['id']}.json"
        digest = write(OUTPUT / filename, payload, frozen=True)
        record.update(status="completed" if payload["complete"] else "incomplete", path=filename, sha256=digest,
                      finishedAt=stamp(), rowCount=len(rows), errorCode=code, rowBoundReached=truncated,
                      complete=payload["complete"], terminalReason=terminal_reason,
                      exceptionType=exception, wireSendsAfter=manifest["wireSends"])
        write(manifest_path, manifest)
        print(json.dumps({"query": request["id"], "status": record["status"], "rows": len(rows), "pages": len(record["pages"]), "wireSends": manifest["wireSends"]}), flush=True)
        if exception:
            raise ProbeStop("A query failed; no retry or new connection is permitted")
        return payload

    logged_in = False
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            login = bs.login()
        manifest["auth"]["login"] = {"errorCode": login.error_code, "errorMessage": login.error_msg}
        logged_in = login.error_code == "0"
        if not logged_in:
            raise ProbeStop("Provider login failed")
        registered = verify_frozen()
        requests = registered["requests"]
        manifest["requestManifest"] = {"path": "request-manifest.json", "sha256": sha(OUTPUT / "request-manifest.json")}
        manifest["selectedCodes"] = [item["code"] for item in registered["selected"]]
        write(manifest_path, manifest)
        for request in requests:
            response = query(request)
            if not response["complete"]:
                raise ProbeStop("Provider query incomplete; preserve failure and stop without retry")
        manifest["status"] = "completed" if all(r["complete"] for r in manifest["requests"]) else "completedWithIncompleteQueries"
    except Exception as error:
        manifest["status"] = "stopped"
        manifest["exceptionType"] = type(error).__name__
        manifest["stopReason"] = str(error) if isinstance(error, ProbeStop) else "Unexpected worker failure; public partial evidence retained"
    finally:
        logout_mode = True
        if logged_in:
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = bs.logout()
                manifest["auth"]["logout"] = {"errorCode": result.error_code, "errorMessage": result.error_msg}
            except Exception as error:
                manifest["auth"]["logout"] = {"exceptionType": type(error).__name__, "confirmed": False}
        sock = getattr(context, "default_socket", None)
        if sock is not None:
            sock.close()
        manifest["finishedAt"] = stamp()
        manifest["elapsedSeconds"] = round(time.monotonic() - started, 3)
        write(manifest_path, manifest)
        print(json.dumps({"status": manifest["status"], "wireSends": manifest["wireSends"], "queries": len(manifest["requests"]), "logout": manifest["auth"].get("logout")}), flush=True)


def validate_output():
    registered = verify_frozen()
    manifest = read(OUTPUT / "manifest.json")
    require(manifest["protocol"]["sha256"] == sha(OUTPUT / "protocol.json"), "Run protocol mismatch")
    require(manifest["wireSends"] <= MAX_SENDS, "Wire bound exceeded")
    expected = registered["requests"]
    require(len(manifest["requests"]) <= len(expected), "Unregistered request")
    pages_checked, aggregate_checked, anomalies = 0, 0, []
    responses = {}
    for ordinal, record in enumerate(manifest["requests"]):
        require(all(record[k] == expected[ordinal][k] for k in ("id", "api", "parameters")), "Request order/parameters changed")
        if "path" not in record:
            anomalies.append({"id": record["id"], "reason": "workerStoppedDuringQuery"})
            continue
        require(sha(OUTPUT / record["path"]) == record["sha256"], "Aggregate hash mismatch")
        payload = read(OUTPUT / record["path"])
        require(payload["pages"] == record["pages"], "Aggregate page records mismatch")
        require(payload["api"] == record["api"] and payload["parameters"] == record["parameters"], "Aggregate request mismatch")
        combined, page_numbers = [], []
        for page in record["pages"]:
            require(sha(OUTPUT / page["path"]) == page["sha256"], "Page hash mismatch")
            raw = read(OUTPUT / page["path"])
            require(raw["fields"] == payload["fields"] and raw["api"] == record["api"] and raw["parameters"] == record["parameters"], "Page fields/request mismatch")
            require(len(raw["rows"]) == page["rowCount"], "Page row count mismatch")
            require(all(len(row) == len(raw["fields"]) for row in raw["rows"]), "Page row shape mismatch")
            combined.extend(raw["rows"])
            page_numbers.append(raw["sdkPageNumber"])
            pages_checked += 1
        require(len(page_numbers) == len(set(page_numbers)), "Repeated pagination page")
        require(combined == payload["rows"] and len(combined) == record["rowCount"] <= MAX_ROWS, "Aggregate/page row mismatch")
        require(record["complete"] == payload["complete"], "Completion status mismatch")
        require(not (len(combined) >= MAX_ROWS and payload["complete"]), "Bounded response incorrectly complete")
        responses[record["id"]] = payload
        aggregate_checked += 1
    details = []
    for item in registered["selected"]:
        symbol = item["symbol"]
        basic, daily = responses.get(symbol + "-basic"), responses.get(symbol + "-daily")
        basic_rows = [] if basic is None else [dict(zip(basic["fields"], row)) for row in basic["rows"]]
        daily_rows = [] if daily is None else [dict(zip(daily["fields"], row)) for row in daily["rows"]]
        dates = [row["date"] for row in daily_rows]
        require(dates == sorted(set(dates)), "Duplicate or out-of-order daily dates")
        require(all(START <= d <= END for d in dates), "Daily date outside requested range")
        for row in daily_rows:
            if row.get("code") != item["code"] or row.get("adjustflag") != "3":
                anomalies.append({"id": symbol + "-daily", "reason": "rawDailyCodeOrAdjustmentMismatch", "date": row["date"]})
        details.append({"symbol": symbol, "basicRows": basic_rows,
                        "dailyComplete": daily.get("complete") if daily else None,
                        "dailyRows": len(dates), "dailyStart": dates[0] if dates else None, "dailyEnd": dates[-1] if dates else None,
                        "isSTValues": dict(Counter(row.get("isST") for row in daily_rows)),
                        "tradeStatusValues": dict(Counter(row.get("tradestatus") for row in daily_rows)),
                        "allRequests": [{"id": r["id"], "complete": responses.get(r["id"], {}).get("complete"),
                                         "rows": len(responses[r["id"]]["rows"]) if r["id"] in responses else None,
                                         "errorCode": responses.get(r["id"], {}).get("errorCode")}
                                        for r in expected if r["parameters"]["code"] == item["code"]]})
    result = {"status": "passed", "checkedAt": stamp(), "manifestSha256": sha(OUTPUT / "manifest.json"),
              "protocolSha256": sha(OUTPUT / "protocol.json"), "requestManifestSha256": sha(OUTPUT / "request-manifest.json"),
              "sourceAuditSha256": registered["sourceAuditSha256"], "missingUnionCount": 125,
              "selectedSymbols": [s["symbol"] for s in registered["selected"]],
              "plannedQueries": 48, "attemptedQueries": len(manifest["requests"]),
              "completeQueries": sum(r.get("complete") is True for r in manifest["requests"]),
              "unattemptedQueries": [r["id"] for r in expected[len(manifest["requests"]):]],
              "aggregateFilesVerified": aggregate_checked, "pageFilesVerified": pages_checked,
              "wireSends": manifest["wireSends"], "elapsedSeconds": manifest.get("elapsedSeconds"),
              "fetchStatus": manifest["status"], "logoutConfirmed": manifest.get("auth", {}).get("logout", {}).get("errorCode") == "0",
              "sourceSemanticsAnomalies": anomalies, "details": details, "returnsCalculated": False, "certified": False,
              "limitations": read(OUTPUT / "protocol.json")["limitations"]}
    write(OUTPUT / "validation.json", result, frozen=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Execute the already frozen exact batch once")
    mode.add_argument("--validate", action="store_true", help="Validate a retained batch, including partial failures")
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker()
    elif args.validate:
        print(json.dumps(validate_output()))
    elif args.execute:
        verify_frozen()
        require(not (OUTPUT / "manifest.json").exists() and not (OUTPUT / "process.json").exists(), "Batch already attempted; never retry")
        try:
            result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker"], timeout=300, check=False)
            process = {"exitCode": result.returncode, "timedOut": False}
        except subprocess.TimeoutExpired:
            process = {"timedOut": True, "timeoutSeconds": 300, "logoutConfirmed": False}
        write(OUTPUT / "process.json", process, frozen=True)
        if (OUTPUT / "manifest.json").exists():
            result = validate_output()
            print(json.dumps({"validation": result["status"], "fetchStatus": result["fetchStatus"], "queries": result["attemptedQueries"], "wireSends": result["wireSends"]}))
    else:
        freeze()


if __name__ == "__main__":
    main()
