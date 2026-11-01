"""Frozen, serial public BaoStock evidence batch; never calculate returns.

Four dated member snapshots determine eight SHA256-selected SH/SZ main-board
securities. Public source fields are preserved exactly, with page-level hashes.
The script writes only artifacts/strategy-search/evidence-supplement-v1 and
does not modify the user workspace, frozen research code or baseline manifests.
"""
from __future__ import annotations

import argparse
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
OUTPUT = ROOT / "artifacts" / "strategy-search" / "evidence-supplement-v1"
DATES = ("2022-01-04", "2022-07-01", "2023-01-03", "2023-12-29")
START, END = "2021-08-02", "2023-12-31"
SEED = "a-trade-evidence-supplement-v1-2022-2023-four-date-membership"
MAIN_BOARD_PATTERN = r"^(?:sh\.60[0135]\d{3}|sz\.00[0123]\d{3})$"
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST"
MAX_ROWS, MAX_SENDS = 10000, 150


def stamp():
    return datetime.now(timezone.utc).isoformat()


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


def protocol():
    return {"schema": "bounded-public-evidence-v1", "registeredAt": stamp(), "provider": "baostock",
            "packageVersion": importlib.metadata.version("baostock"),
            "scriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "purpose": "Small development evidence-quality batch; no return computation or strategy search",
            "membershipRequests": [{"id": "members-" + day, "api": "query_all_stock", "parameters": {"day": day}} for day in DATES],
            "selection": {"seed": SEED, "count": 8, "codeRegex": MAIN_BOARD_PATTERN,
                          "union": "All qualifying codes in any of the four complete member snapshots, including halted statuses; no current-file or performance filter",
                          "ranking": "Ascending SHA256 UTF-8 bytes of seed + ':' + exact dotted code; tie by dotted code ascending; first eight",
                          "failurePolicy": "If any membership query errors/truncates, do not select replacements or start the eight-security phase"},
            "securityRequestTemplate": requests_for("<selected-dotted-code>"),
            "requestManifest": "After successful membership queries and selection, freeze exact 48 security requests before executing any of them",
            "limits": {"wireSendsIncludingAuthentication": MAX_SENDS, "rowsPerQuery": MAX_ROWS,
                       "singleConnection": True, "serial": True, "socketAndRequestSeconds": 10,
                       "queryDeadlineSeconds": 280, "wallSeconds": 300, "automaticRetries": 0,
                       "reservedLogoutSends": 1, "maxReplyBytes": 16 * 1024 * 1024},
            "rowBoundPolicy": "Reaching 10000 stored rows always marks truncated; never claim complete at the bound",
            "pagination": "Save public fields/rows for every received page and its hash; follow exact-full SDK pages; oversized SDK unpaged replies below bound are retained and labeled explicitly",
            "scope": {"historyStart": START, "historyEnd": END, "dividendOperateYears": [2021, 2022, 2023],
                      "metadataCaution": "stock_basic is a retrieval-time identity response. Current status is never used for historical selection. Raw action publication/payment fields may reference other dates; do not treat those fields as historically available before their dates."},
            "certified": False, "targetAchieved": False,
            "limitations": ["Four dates do not establish daily historical universe completeness.",
                            "Eight hash-selected securities do not certify high hit probability or complete corporate actions.",
                            "No minute, opening-auction or order execution evidence is acquired.",
                            "Raw fields remain unchanged; empty/unknown values are not filled with zero."]}


class ProbeStop(RuntimeError):
    pass


def worker():
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
        members, membership_files = set(), []
        for day in DATES:
            request = {"id": "members-" + day, "api": "query_all_stock", "parameters": {"day": day}}
            response = query(request)
            if not response["complete"] or "code" not in response["fields"]:
                raise ProbeStop("Membership selection requires all four complete snapshots")
            position = response["fields"].index("code")
            for row in response["rows"]:
                if len(row) != len(response["fields"]):
                    raise ProbeStop("Membership field/row shape mismatch")
                if re.fullmatch(MAIN_BOARD_PATTERN, row[position]):
                    members.add(row[position])
            latest = manifest["requests"][-1]
            membership_files.append({"date": day, "path": latest["path"], "sha256": latest["sha256"]})
        ranked = sorted(members, key=lambda code: (hashlib.sha256((SEED + ":" + code).encode("utf-8")).hexdigest(), code))
        if len(ranked) < 8:
            raise ProbeStop("Historical membership union has fewer than eight eligible codes")
        selected = ranked[:8]
        requests = [request for code in selected for request in requests_for(code)]
        request_manifest = {"schema": "frozen-public-evidence-requests-v1", "frozenAt": stamp(),
                            "protocolSha256": manifest["protocol"]["sha256"], "membershipSources": membership_files,
                            "historicalMainBoardUnion": sorted(members), "unionCount": len(members),
                            "selectionSeed": SEED, "selectionCount": 8,
                            "selected": [{"code": code, "rank": rank, "selectionHash": hashlib.sha256((SEED + ":" + code).encode("utf-8")).hexdigest()} for rank, code in enumerate(selected, 1)],
                            "requests": requests, "automaticReplacementAllowed": False}
        request_hash = write(OUTPUT / "request-manifest.json", request_manifest, frozen=True)
        manifest["requestManifest"] = {"path": "request-manifest.json", "sha256": request_hash}
        manifest["selectedCodes"] = selected
        write(manifest_path, manifest)
        print(json.dumps({"selectionFrozen": True, "unionCount": len(members), "selectedCodes": selected, "queries": len(requests)}), flush=True)
        for request in requests:
            query(request)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker()
        return
    if OUTPUT.exists():
        parser.error("Output already exists; never overwrite or rerun the frozen evidence batch")
    OUTPUT.mkdir(parents=True)
    write(OUTPUT / "protocol.json", protocol(), frozen=True)
    try:
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker"], timeout=300, check=False)
        status = {"exitCode": result.returncode, "timedOut": False}
    except subprocess.TimeoutExpired:
        status = {"timedOut": True, "timeoutSeconds": 300, "logoutConfirmed": False}
    write(OUTPUT / "process.json", status, frozen=True)


if __name__ == "__main__":
    main()
