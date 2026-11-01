"""Freeze and fetch the remaining 117 missing-history securities exactly once.

Five serial bounded batches reuse the frozen v1 worker without modifying it.
Default command only freezes all requests. --execute consumes the whole series;
any failed/incomplete batch stops it permanently. This is data quality, not a
return study; neither a user database nor the registered 3141-stock run is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "artifacts" / "strategy-search"
OUTPUT = BASE / "evidence-missing-history-v2"
V1_OUTPUT = BASE / "evidence-missing-history-v1"
V1_SCRIPT = ROOT / "scripts" / "supplement-missing-historical-evidence.py"
MAX_BATCHES, BATCH_SIZE, SERIES_SENDS, SERIES_SECONDS = 5, 24, 750, 1500


def load_v1():
    spec = importlib.util.spec_from_file_location("missing_history_v1_transport", V1_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v1 = load_v1()
read, write, sha, require, stamp = v1.read, v1.write, v1.sha, v1.require, v1.stamp


def validate_batch(directory, registered, *, require_complete=False):
    """Run the unchanged v1 raw/page validator with a read-only result sink."""
    module = load_v1()
    module.OUTPUT = directory
    module.verify_frozen = lambda: registered
    # The original validator's only write is its final report. Do not rewrite v1.
    module.write = lambda path, value, **kwargs: None
    result = module.validate_output()
    result["plannedQueries"] = len(registered["requests"])
    manifest = read(directory / "manifest.json")
    expected_codes = [s["code"] for s in registered["selected"]]
    require(not manifest["selectedCodes"] or manifest["selectedCodes"] == expected_codes, "Run selected codes mismatch")
    if manifest.get("requestManifest"):
        require(manifest["requestManifest"]["sha256"] == sha(directory / "request-manifest.json"), "Run request manifest mismatch")
    process = read(directory / "process.json") if (directory / "process.json").exists() else {}
    complete = (manifest["status"] == "completed" and
                result["attemptedQueries"] == result["completeQueries"] == len(registered["requests"]) and
                not result["sourceSemanticsAnomalies"] and result["logoutConfirmed"] and
                process.get("exitCode") == 0 and process.get("timedOut") is False)
    for record in manifest["requests"]:
        if record.get("complete"):
            require(record.get("errorCode") == "0" and not record.get("rowBoundReached"), "Invalid completed response")
            payload = read(directory / record["path"])
            require(payload.get("errorCode") == "0" and not payload.get("rowBoundReached"), "Invalid completed raw response")
    result["completeBatch"] = complete
    if require_complete:
        require(complete, "Required completed batch is incomplete")
    return result


def verified_v1():
    registered = v1.verify_frozen()
    validation = validate_batch(V1_OUTPUT, registered, require_complete=True)
    require(len(registered["selected"]) == 8 and len(registered["requests"]) == 48, "Expected completed v1 eight-security batch")
    manifest = read(V1_OUTPUT / "manifest.json")
    stored = read(V1_OUTPUT / "validation.json")
    require(stored["manifestSha256"] == sha(V1_OUTPUT / "manifest.json"), "V1 validation manifest hash mismatch")
    require(stored["protocolSha256"] == sha(V1_OUTPUT / "protocol.json"), "V1 validation protocol hash mismatch")
    require(stored["requestManifestSha256"] == sha(V1_OUTPUT / "request-manifest.json"), "V1 validation request hash mismatch")
    return {"directory": str(V1_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
            "protocolSha256": sha(V1_OUTPUT / "protocol.json"),
            "requestManifestSha256": sha(V1_OUTPUT / "request-manifest.json"),
            "manifestSha256": sha(V1_OUTPUT / "manifest.json"),
            "validationSha256": sha(V1_OUTPUT / "validation.json"),
            "processSha256": sha(V1_OUTPUT / "process.json"),
            "completedSymbols": [s["symbol"] for s in registered["selected"]],
            "requests": [{**{k: r[k] for k in ("id", "api", "parameters", "path", "sha256", "complete", "rowCount", "pages")},
                          "sourceBatch": "v1"} for r in manifest["requests"]],
            "pageFilesVerified": validation["pageFilesVerified"]}


def make_plan(source, completed):
    symbols = source["missingUnionSymbols"]
    require(len(symbols) == len(set(symbols)) == 125, "Expected exact 125-symbol universe")
    ordered = sorted(symbols, key=lambda s: (hashlib.sha256((v1.SEED + ":" + s).encode()).hexdigest(), s))
    require(completed["completedSymbols"] == ordered[:8], "V1 is not the original fixed eight")
    remaining = ordered[8:]
    selected = [{"symbol": s, "code": s[:2] + "." + s[2:], "rank": i + 9,
                 "selectionHash": hashlib.sha256((v1.SEED + ":" + s).encode()).hexdigest()}
                for i, s in enumerate(remaining)]
    batches = []
    for offset in range(0, len(selected), BATCH_SIZE):
        group = selected[offset:offset + BATCH_SIZE]
        batches.append({"id": f"batch-{len(batches) + 1:02d}", "selected": group,
                        "requests": [request for s in group for request in v1.requests_for(s["code"])]})
    require([len(b["selected"]) for b in batches] == [24, 24, 24, 24, 21], "Unexpected fixed batches")
    requests = [r for batch in batches for r in batch["requests"]]
    require(len(requests) == len({r["id"] for r in requests}) == 702, "Expected 702 unique requests")
    return {"schema": "missing-history-series-registration-v2", "sourceAuditSha256": source["auditSha256"],
            "sourceVerification": source, "reusedV1": completed, "selectionSeed": v1.SEED,
            "selectionRanking": "Ascending SHA256(seed + ':' + undotted symbol); skip only the verified completed original eight",
            "selected": selected, "requests": requests, "batches": batches,
            "automaticReplacementAllowed": False, "retryOrResumeAllowed": False}


def freeze():
    source, completed = v1.verify_sources(), verified_v1()
    plan = make_plan(source, completed)
    require(not OUTPUT.exists(), "Output already exists; never overwrite frozen series")
    OUTPUT.mkdir(parents=True)
    protocol = {"schema": "bounded-missing-history-series-v2", "registeredAt": stamp(),
                "scriptSha256": sha(Path(__file__)), "transportScriptSha256": sha(V1_SCRIPT),
                "sourceAuditSha256": source["auditSha256"], "requestCount": 702,
                "reusedV1QueryCount": 48, "reusedV1ManifestSha256": completed["manifestSha256"],
                "perBatchLimits": read(V1_OUTPUT / "protocol.json")["limits"],
                "seriesLimits": {"batches": MAX_BATCHES, "wireSendsIncludingAuthentication": SERIES_SENDS,
                                 "wallSeconds": SERIES_SECONDS, "automaticRetries": 0,
                                 "failurePolicy": "Any failure, incomplete response, timeout, validation failure or unconfirmed logout permanently stops all later batches"},
                "transportReuse": "Private instance of the byte-frozen v1 worker; adapt only output path and frozen batch registration verifier",
                "limitsScope": "Each worker has <=300 seconds; the series has a shared 1500-second worker deadline including gaps. Local final reporting occurs after network work ends.",
                "limitations": read(V1_OUTPUT / "protocol.json")["limitations"],
                "returnsCalculated": False, "certified": False, "targetAchieved": False}
    plan["frozenAt"] = stamp()
    plan["protocolSha256"] = write(OUTPUT / "protocol.json", protocol, frozen=True)
    plan_sha = write(OUTPUT / "request-manifest.json", plan, frozen=True)
    for batch in plan["batches"]:
        directory = OUTPUT / "batches" / batch["id"]
        directory.mkdir(parents=True)
        batch_protocol = {**protocol, "seriesProtocolSha256": plan["protocolSha256"],
                          "seriesRequestManifestSha256": plan_sha, "batchId": batch["id"],
                          "requestCount": len(batch["requests"])}
        batch_registration = {**batch, "sourceAuditSha256": source["auditSha256"],
                              "seriesRequestManifestSha256": plan_sha, "frozenAt": stamp(),
                              "protocolSha256": write(directory / "protocol.json", batch_protocol, frozen=True)}
        write(directory / "request-manifest.json", batch_registration, frozen=True)
    print(v1.json.dumps({"frozen": True, "remaining": 117, "requests": 702,
                         "batchSizes": [len(b["selected"]) for b in plan["batches"]]}), flush=True)


def verify_frozen():
    protocol, plan = read(OUTPUT / "protocol.json"), read(OUTPUT / "request-manifest.json")
    require(protocol["scriptSha256"] == sha(Path(__file__)), "Frozen v2 implementation changed")
    require(protocol["transportScriptSha256"] == sha(V1_SCRIPT), "Frozen v1 transport changed")
    require(plan["protocolSha256"] == sha(OUTPUT / "protocol.json"), "Series protocol mismatch")
    expected = make_plan(v1.verify_sources(), verified_v1())
    require({k: v for k, v in plan.items() if k not in ("frozenAt", "protocolSha256")} == expected, "Series registration/source drift")
    for batch in plan["batches"]:
        directory = OUTPUT / "batches" / batch["id"]
        registered, batch_protocol = read(directory / "request-manifest.json"), read(directory / "protocol.json")
        require(registered["protocolSha256"] == sha(directory / "protocol.json"), "Batch protocol mismatch")
        require(registered["seriesRequestManifestSha256"] == batch_protocol["seriesRequestManifestSha256"] == sha(OUTPUT / "request-manifest.json"), "Batch series linkage mismatch")
        require(batch_protocol == {**protocol, "seriesProtocolSha256": plan["protocolSha256"],
                                  "seriesRequestManifestSha256": sha(OUTPUT / "request-manifest.json"),
                                  "batchId": batch["id"], "requestCount": len(batch["requests"])}, "Batch protocol drift")
        require({k: registered[k] for k in ("id", "selected", "requests")} == batch, "Batch request drift")
        require(registered["sourceAuditSha256"] == plan["sourceAuditSha256"], "Batch source mismatch")
    return plan


def worker_batch(batch_id):
    plan = verify_frozen()
    execution = read(OUTPUT / "run.json")
    require(execution["status"] == "running" and execution["activeBatch"] == batch_id,
            "Only the active series batch may run")
    require(execution["parentPid"] == os.getppid(), "Worker must be launched by its active series parent")
    batch = next((b for b in plan["batches"] if b["id"] == batch_id), None)
    require(batch is not None, "Unknown frozen batch")
    directory = OUTPUT / "batches" / batch_id
    require(not (directory / "manifest.json").exists(), "Batch already attempted")
    write(directory / "attempt.json", {"startedAt": stamp(), "parentPid": os.getppid()}, frozen=True)
    module = load_v1()
    module.OUTPUT = directory
    module.verify_frozen = lambda: read(directory / "request-manifest.json")
    module.worker()


def execute_series(*, run=subprocess.run, clock=time.monotonic):
    plan = verify_frozen()
    # Exclusive creation consumes this series even when its first worker fails.
    started = clock()
    execution = {"schema": "missing-history-series-run-v2", "startedAt": stamp(), "status": "running",
                 "parentPid": os.getpid(), "activeBatch": None, "batches": [], "wireSends": 0,
                 "protocolSha256": sha(OUTPUT / "protocol.json"),
                 "requestManifestSha256": sha(OUTPUT / "request-manifest.json"),
                 "returnsCalculated": False, "certified": False}
    write(OUTPUT / "run.json", execution, frozen=True)
    try:
        for batch in plan["batches"]:
            remaining = SERIES_SECONDS - (clock() - started)
            require(remaining > 0, "Series wall deadline reached")
            require(len(execution["batches"]) < MAX_BATCHES and execution["wireSends"] < SERIES_SENDS, "Series attempt/send bound reached")
            directory = OUTPUT / "batches" / batch["id"]
            require(not (directory / "attempt.json").exists() and not (directory / "manifest.json").exists(), "No batch retry is permitted")
            execution["activeBatch"] = batch["id"]
            record = {"id": batch["id"], "status": "running", "startedAt": stamp()}
            execution["batches"].append(record)
            write(OUTPUT / "run.json", execution)
            try:
                result = run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker-batch", batch["id"]],
                             timeout=min(300, remaining), check=False)
                process = {"exitCode": result.returncode, "timedOut": False}
            except subprocess.TimeoutExpired:
                process = {"timedOut": True, "logoutConfirmed": False}
            write(directory / "process.json", process, frozen=True)
            record["processSha256"] = sha(directory / "process.json")
            if (directory / "manifest.json").exists():
                manifest = read(directory / "manifest.json")
                execution["wireSends"] += manifest["wireSends"]
                record["manifestSha256"] = sha(directory / "manifest.json")
                record["wireSends"] = manifest["wireSends"]
                validation = validate_batch(directory, read(directory / "request-manifest.json"))
                write(directory / "validation.json", validation, frozen=True)
                record["validationSha256"] = sha(directory / "validation.json")
                require(validation["completeBatch"], "Batch incomplete, failed or logout unconfirmed; no later batch may run")
            else:
                raise ValueError("Worker did not produce a manifest; no later batch may run")
            require(execution["wireSends"] <= SERIES_SENDS, "Series wire bound exceeded")
            record.update(status="completed", finishedAt=stamp())
            write(OUTPUT / "run.json", execution)
            print(v1.json.dumps({"batchCompleted": batch["id"], "queries": len(batch["requests"]),
                                 "batchWireSends": record["wireSends"], "seriesWireSends": execution["wireSends"]}), flush=True)
        execution["status"] = "completed"
    except Exception as error:
        execution.update(status="stopped", exceptionType=type(error).__name__, stopReason=str(error))
        if execution["batches"] and execution["batches"][-1]["status"] == "running":
            execution["batches"][-1].update(status="stopped", finishedAt=stamp())
    finally:
        execution.update(activeBatch=None, finishedAt=stamp(), elapsedSeconds=round(clock() - started, 3))
        write(OUTPUT / "run.json", execution)
    return execution


def build_report():
    plan = verify_frozen()
    execution = read(OUTPUT / "run.json")
    references = [{"batch": "v1", "directory": V1_OUTPUT, "registered": read(V1_OUTPUT / "request-manifest.json")}]
    references += [{"batch": b["id"], "directory": OUTPUT / "batches" / b["id"],
                    "registered": read(OUTPUT / "batches" / b["id"] / "request-manifest.json")} for b in plan["batches"]]
    rows, empty = Counter(), Counter()
    records, details = [], []
    for reference in references:
        directory, registered = reference["directory"], reference["registered"]
        if (directory / "manifest.json").exists():
            result = validate_batch(directory, registered)
            manifest = read(directory / "manifest.json")
            for record in manifest["requests"]:
                records.append({**copy.deepcopy(record), "sourceBatch": reference["batch"],
                                "sourceDirectory": str(directory.relative_to(ROOT)).replace("\\", "/"),
                                "sourceManifestSha256": sha(directory / "manifest.json")})
                if "rowCount" in record:
                    rows[record["api"]] += record["rowCount"]
                    if record["rowCount"] == 0:
                        empty[record["api"]] += 1
            details.extend([{**d, "sourceBatch": reference["batch"],
                             "sixQueriesComplete": all(r["complete"] is True for r in d["allRequests"])} for d in result["details"]])
        else:
            details.extend([{"symbol": s["symbol"], "sourceBatch": reference["batch"], "sixQueriesComplete": False,
                             "dailyComplete": None, "dailyRows": 0, "basicRows": [], "dailyStart": None,
                             "dailyEnd": None, "status": "unattempted"} for s in registered["selected"]])
    require(len(details) == len({d["symbol"] for d in details}) == 125, "Combined coverage must retain every original symbol exactly once")
    combined = {"schema": "missing-history-combined-references-v2", "createdAt": stamp(),
                "sourceAuditSha256": plan["sourceAuditSha256"], "v2RunSha256": sha(OUTPUT / "run.json"),
                "reusedV1": plan["reusedV1"], "requests": records, "expectedQueriesIncludingV1": 750,
                "unattemptedRequestIds": [r["id"] for r in plan["requests"] if r["id"] not in {x["id"] for x in records}],
                "returnsCalculated": False, "certified": False}
    combined_sha = write(OUTPUT / "combined-manifest.json", combined, frozen=True)
    report = {"schema": "missing-history-quality-summary-v2", "createdAt": stamp(),
              "combinedManifestSha256": combined_sha, "seriesStatus": execution["status"],
              "originalMissingSymbols": 125, "reusedV1Symbols": 8, "plannedNewSymbols": 117,
              "sixQueriesCompleteSymbols": sum(d["sixQueriesComplete"] for d in details),
              "nonemptyCompleteDailySymbols": sum(d["dailyComplete"] is True and d["dailyRows"] > 0 for d in details),
              "dailyEmptySymbols": [d["symbol"] for d in details if d["dailyComplete"] is True and not d["dailyRows"]],
              "dailyShortHistorySymbols": [d["symbol"] for d in details if d["dailyComplete"] and d["dailyEnd"] and d["dailyEnd"] < "2023-12-29"],
              "incompleteOrUnattemptedSymbols": [d["symbol"] for d in details if not d["sixQueriesComplete"]],
              "newWireSends": execution["wireSends"], "newElapsedSeconds": execution["elapsedSeconds"],
              "totalRawRowsIncludingV1": sum(rows.values()), "rowsByAPIIncludingV1": dict(rows),
              "emptyQueriesByAPIIncludingV1": dict(empty), "details": details,
              "limitations": read(OUTPUT / "protocol.json")["limitations"],
              "returnsCalculated": False, "certified": False, "targetAchieved": False}
    write(OUTPUT / "quality-summary.json", report, frozen=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--worker-batch", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_batch:
        worker_batch(args.worker_batch)
    elif args.execute:
        execution = execute_series()
        report = build_report()
        print(v1.json.dumps({"seriesStatus": execution["status"], "completeSymbolsIncludingV1": report["sixQueriesCompleteSymbols"],
                             "newWireSends": report["newWireSends"]}), flush=True)
    else:
        freeze()


if __name__ == "__main__":
    main()
