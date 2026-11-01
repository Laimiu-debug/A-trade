"""Bounded single-connection BaoStock schema probe for 2022 development data.

Uses the already installed official package. Saves only public endpoint fields,
rows and errors, never authentication requests, SDK objects or credentials.
At most ten wire sends; no retries; socket timeout five seconds and subprocess
wall timeout forty seconds. No user-workspace or global configuration writes.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import socket
import subprocess
import sys

BASE = Path(__file__).resolve().parent.parent / "artifacts" / "strategy-search" / "provider-probe"
REQUESTS = [
    ("calendar", "query_trade_dates", {"start_date": "2022-01-01", "end_date": "2022-01-14"}),
    ("stock-basic", "query_stock_basic", {"code": "sh.600000"}),
    ("daily", "query_history_k_data_plus", {"code": "sh.600000", "fields":
     "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST",
     "start_date": "2022-01-01", "end_date": "2022-01-14", "frequency": "d", "adjustflag": "3"}),
    ("adjust-factors", "query_adjust_factor", {"code": "sh.600000", "start_date": "2022-01-01", "end_date": "2022-12-31"}),
    ("dividend", "query_dividend_data", {"code": "sh.600000", "year": "2022", "yearType": "report"}),
]
MINUTE_REQUESTS = [
    ("five-minute", "query_history_k_data_plus", {"code": "sh.600000", "fields":
     "date,time,code,open,high,low,close,volume,amount,adjustflag", "start_date": "2022-01-04",
     "end_date": "2022-01-06", "frequency": "5", "adjustflag": "3"}),
]
CALENDAR_REQUESTS = [
    ("calendar-with-warmup", "query_trade_dates", {"start_date": "2021-08-02", "end_date": "2023-12-31"}),
]


def write_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def worker(output, minute=False, calendar=False):
    socket.setdefaulttimeout(5)
    import baostock as bs
    from baostock.util import socketutil
    from baostock.common import context

    requests = CALENDAR_REQUESTS if calendar else MINUTE_REQUESTS if minute else REQUESTS
    manifest = {"startedAt": datetime.now(timezone.utc).isoformat(), "provider": "baostock",
                "packageVersion": importlib.metadata.version("baostock"),
                "packageSource": "https://pypi.org/project/baostock/0.9.4/",
                "accessRules": "https://www.baostock.com/blacklist", "mode": "Single connection, serial, no retry",
                "dataScope": ("2021-08-02..2023-12-31 independent trading calendar" if calendar else
                              "Fixed three-day five-minute 2022 probe" if minute else "2022 daily and metadata probe"),
                "returnCalculations": False,
                "limits": {"wireSends": 10, "socketSeconds": 5, "wallSeconds": 40, "rowsPerQuery": 1000},
                "plannedQueries": [{"name": n, "api": a, "parameters": p} for n, a, p in requests],
                "results": [], "status": "running", "wireSends": 0,
                "limitations": ["One surviving security cannot establish delisted/ST/halt coverage.",
                                "Stock-basic status is a current response, not a dated historical state.",
                                "Adjustment factors are not complete cash, rights, merger or tax cash flows.",
                                "Five-minute OHLC does not establish same-bar trigger order or exact auction volume.",
                                "No whole-pool data download; only the fixed listed requests."]}
    manifest_path = output / "manifest.json"
    original_send = socketutil.send_msg

    def bounded_send(message):
        if manifest["wireSends"] >= 10:
            raise RuntimeError("Probe wire-send budget reached")
        manifest["wireSends"] += 1
        write_json(manifest_path, manifest)
        return original_send(message)

    socketutil.send_msg = bounded_send
    write_json(manifest_path, manifest)
    logged_in = False
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            login = bs.login()
        manifest["login"] = {"errorCode": login.error_code, "errorMessage": login.error_msg}
        logged_in = login.error_code == "0"
        if not logged_in:
            manifest["status"] = "loginFailed"
            return
        for name, api, parameters in requests:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = getattr(bs, api)(**parameters)
                rows = []
                while result.error_code == "0" and len(rows) < 1000 and result.next():
                    rows.append(result.get_row_data())
            payload = {"provider": "baostock", "api": api, "parameters": parameters,
                       "retrievedAt": datetime.now(timezone.utc).isoformat(), "errorCode": result.error_code,
                       "errorMessage": result.error_msg, "fields": result.fields, "rows": rows,
                       "rowBoundReached": len(rows) == 1000}
            filename = name + ".json"
            digest = write_json(output / filename, payload)
            manifest["results"].append({"name": name, "file": filename, "sha256": digest,
                                        "errorCode": result.error_code, "rowCount": len(rows), "fields": result.fields})
            write_json(manifest_path, manifest)
            if result.error_code != "0":
                manifest["status"] = "queryFailed"
                break
        else:
            manifest["status"] = "completed"
    except Exception as error:
        manifest["status"] = "exception"
        manifest["exceptionType"] = type(error).__name__
    finally:
        if logged_in:
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = bs.logout()
                manifest["logout"] = {"errorCode": result.error_code, "errorMessage": result.error_msg}
            except Exception as error:
                manifest["logout"] = {"exceptionType": type(error).__name__}
        active_socket = getattr(context, "default_socket", None)
        if active_socket is not None:
            active_socket.close()
        manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
        write_json(manifest_path, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--five-minute", action="store_true", help="Probe only fixed 2022-01-04..06 five-minute bars")
    mode.add_argument("--calendar", action="store_true", help="Probe only 2021-08-02..2023-12-31 trading calendar")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or BASE / ("2021-2023-calendar" if args.calendar else
                                   "2022-single-security-five-minute" if args.five_minute else "2022-single-security")).resolve()
    if not output.is_relative_to(BASE):
        parser.error("Output must stay in repository artifacts/strategy-search/provider-probe")
    if args.worker:
        worker(output, args.five_minute, args.calendar)
        return
    if output.exists():
        parser.error("Use a new probe directory to preserve the previous response and hash")
    output.mkdir(parents=True)
    try:
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--output", str(output)]
        if args.five_minute:
            command.append("--five-minute")
        if args.calendar:
            command.append("--calendar")
        result = subprocess.run(command,
                                capture_output=True, timeout=40, check=False)
        process_status = {"exitCode": result.returncode}
    except subprocess.TimeoutExpired:
        process_status = {"timedOut": True, "timeoutSeconds": 40}
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"status": "workerFailed"}
    manifest["process"] = process_status
    if process_status.get("timedOut"):
        manifest["status"] = "wallTimeout"
    write_json(manifest_path, manifest)
    print(json.dumps({"status": manifest["status"], "wireSends": manifest.get("wireSends"),
                      "results": manifest.get("results", []), "process": process_status}))


if __name__ == "__main__":
    main()
