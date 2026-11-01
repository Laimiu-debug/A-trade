"""Extract one frozen, outcome-free market/role panel from audited daily features."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.signal_market_state import MarketStateTracker

REGISTRATION = ROOT / "docs/signal-market-state-panel-v1.json"
REGISTRATION_SHA256 = "40a68c2a85cf4fd2b4e9d4968d80c027726187ea36f70487fa902662b07c2e20"
PANEL_FILES = {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
               "summary.json", "execution-sources.zip", "attempt.json", "completion.json", "independent-verification.json"}
OUTPUT_FILES = {"protocol.json", "execution-sources.zip", "attempt.json", "daily.jsonl.gz", "roles.jsonl.gz", "summary.json"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def json_line(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n")


def implementation_hashes():
    paths = [Path(__file__), ROOT / "service/signal_market_state.py", REGISTRATION]
    return {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted(paths)}


def load_binding():
    if file_hash(REGISTRATION) != REGISTRATION_SHA256:
        raise ValueError("Market-state registration changed")
    document = read(REGISTRATION)
    if (document.get("status") != "registered-before-first-real-extraction" or
            document.get("newStrategyTrials") != 0 or document.get("returnsCalculated") is not False or
            document.get("futureLabelsCreated") is not False):
        raise ValueError("Expected an outcome-free observation registration")
    if file_hash(ROOT / document["design"]["path"]) != document["design"]["sha256"]:
        raise ValueError("Observation design provenance changed")
    directory = (ROOT / document["input"]["directory"]).resolve()
    hashes = document["input"]["filesSha256"]
    if set(hashes) != PANEL_FILES:
        raise ValueError("Incomplete panel input binding")
    for name, expected in hashes.items():
        if file_hash(directory / name) != expected:
            raise ValueError("Panel input changed: " + name)
    manifest, completion, audit = [read(directory / name) for name in
                                   ("manifest.json", "completion.json", "independent-verification.json")]
    sample, inputs = document["sample"], manifest["inputs"]
    symbols, calendar = inputs["symbols"], inputs["calendar"]["dates"]
    dates = [d for d in calendar if sample["start"] <= d <= sample["end"]]
    if (manifest["featureVersion"] != document["sourceFeatureVersion"] or
            completion["sampleManifestHash"] != hashes["manifest.json"] or
            audit.get("sampleManifestHash") != hashes["manifest.json"] or audit.get("passed") is not True or
            type(audit.get("failureCount")) is not int or audit["failureCount"] != 0 or
            audit.get("featureRows") != sample["rows"] or completion.get("featureRows") != sample["rows"] or
            completion.get("targetSessions") != sample["sessions"] or
            symbols != sorted(set(symbols)) or len(symbols) != sample["symbols"] or
            inputs["symbolsSha256"] != digest(symbols) or inputs["symbolCount"] != len(symbols) or
            inputs["snapshotFingerprint"] != sample["snapshotFingerprint"] or
            calendar != sorted(set(calendar)) or digest(calendar) != inputs["calendar"]["sha256"] or
            len(dates) != sample["sessions"] or len(dates)*len(symbols) != sample["rows"] or
            set(inputs["securities"]) != set(symbols)):
        raise ValueError("Panel audit, sample or calendar identity is inconsistent")
    expected_receipt = PANEL_FILES - {"completion.json", "independent-verification.json"}
    if set(completion["files"]) != expected_receipt or any(
            completion["files"][name] != {"path": name, "sha256": hashes[name]} for name in expected_receipt):
        raise ValueError("Panel completion file set changed")
    return {"document": document, "directory": directory, "manifest": manifest,
            "symbols": symbols, "dates": dates, "hashes": hashes}


def iter_days(binding):
    inputs = binding["manifest"]["inputs"]
    with gzip.open(binding["directory"] / "features.jsonl.gz", "rt", encoding="utf-8") as stream:
        for day in binding["dates"]:
            rows = []
            for symbol in binding["symbols"]:
                line = stream.readline()
                if not line:
                    raise ValueError("Panel ended before its full registered population")
                row = json.loads(line)
                sources = {**inputs["securities"][symbol], "benchmarkRawBarsSha256": inputs["benchmark"]["rawBarsSha256"]}
                if (row.get("symbol") != symbol or row.get("asOfDate") != day or
                        row.get("featureVersion") != binding["document"]["sourceFeatureVersion"] or
                        row.get("sampleManifestHash") != binding["hashes"]["manifest.json"] or
                        row.get("calendarHash") != inputs["calendar"]["sha256"] or row.get("sourceHashes") != sources):
                    raise ValueError("Panel row order, version or source identity changed")
                rows.append(row)
            yield day, rows
        if stream.readline():
            raise ValueError("Panel contains trailing rows")


def verify_archive(path, hashes):
    with zipfile.ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(hashes):
            raise ValueError("Execution archive names changed")
        if any(hashlib.sha256(archive.read(name)).hexdigest() != expected for name, expected in hashes.items()):
            raise ValueError("Execution archive source bytes changed")


def freeze(output):
    binding = load_binding()
    if output == binding["directory"] or binding["directory"] in output.parents:
        raise ValueError("Output must not modify the frozen panel")
    implementation = implementation_hashes()
    payload = {"schema": "market-state-observation-protocol-v1", "createdAt": now(),
        "registrationSha256": REGISTRATION_SHA256, "registration": binding["document"],
        "implementation": implementation, "pythonVersion": sys.version,
        "inputManifestSha256": binding["hashes"]["manifest.json"],
        "inputFilesSha256": binding["hashes"], "calendarHash": binding["manifest"]["inputs"]["calendar"]["sha256"],
        "symbols": binding["symbols"], "dates": binding["dates"],
        "returnsCalculated": False, "newStrategyTrials": 0, "certified": False, "targetAchieved": False}
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(output / "execution-sources.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, expected in implementation.items():
            content = (ROOT / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Implementation drift before archive")
            archive.writestr(name, content)
    verify_archive(output / "execution-sources.zip", implementation)
    payload["executionArchiveSha256"] = file_hash(output / "execution-sources.zip")
    payload["protocolHash"] = digest(payload)
    write(output / "protocol.json", payload)
    return payload


def validate_protocol(output, binding):
    protocol = read(output / "protocol.json")
    body = {k: v for k, v in protocol.items() if k != "protocolHash"}
    if (digest(body) != protocol.get("protocolHash") or
            protocol.get("schema") != "market-state-observation-protocol-v1" or
            protocol.get("returnsCalculated") is not False or protocol.get("newStrategyTrials") != 0 or
            protocol.get("certified") is not False or protocol.get("targetAchieved") is not False or
            protocol["registrationSha256"] != REGISTRATION_SHA256 or
            protocol["registration"] != binding["document"] or
            protocol["inputFilesSha256"] != binding["hashes"] or
            protocol.get("executionArchiveSha256") != file_hash(output / "execution-sources.zip") or
            protocol["implementation"] != implementation_hashes() or
            protocol["symbols"] != binding["symbols"] or protocol["dates"] != binding["dates"] or
            protocol["inputManifestSha256"] != binding["hashes"]["manifest.json"] or
            protocol["calendarHash"] != binding["manifest"]["inputs"]["calendar"]["sha256"]):
        raise ValueError("Frozen execution protocol or dependency changed")
    verify_archive(output / "execution-sources.zip", protocol["implementation"])
    return protocol


def verify_completed(output, protocol):
    receipt = read(output / "completion.json")
    if (receipt["protocolHash"] != protocol["protocolHash"] or set(receipt["files"]) != OUTPUT_FILES or
            receipt["roleRows"] != len(protocol["symbols"])*len(protocol["dates"]) or
            receipt["dailyRows"] != len(protocol["dates"]) or receipt["returnsCalculated"] is not False or
            receipt.get("newStrategyTrials") != 0):
        raise ValueError("Completed receipt identity changed")
    for name, entry in receipt["files"].items():
        if entry != {"path": name, "sha256": file_hash(output / name)}:
            raise ValueError("Completed artifact changed: " + name)
    return receipt


def execute(output):
    binding = load_binding()
    protocol = validate_protocol(output, binding)
    if (output / "completion.json").exists():
        return verify_completed(output, protocol)
    if (output / "attempt.json").exists() or (output / "failure.json").exists():
        raise ValueError("Existing incomplete attempt is preserved; diagnose before any explicit recovery")
    write(output / "attempt.json", {"startedAt": now(), "protocolHash": protocol["protocolHash"],
                                   "rule": "one-complete-extraction;no-outcome-inputs"})
    started = last_notice = time.perf_counter()
    protocol_file_hash = file_hash(output / "protocol.json")
    archive_hash = file_hash(output / "execution-sources.zip")
    rows_written, days_written, totals = 0, 0, Counter()
    lineage = {"inputManifestSha256": binding["hashes"]["manifest.json"],
               "inputFeatureFileSha256": binding["hashes"]["features.jsonl.gz"],
               "calendarHash": protocol["calendarHash"], "protocolHash": protocol["protocolHash"]}
    try:
        tracker = MarketStateTracker(binding["symbols"], binding["dates"])
        with gzip.open(output / "daily.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as daily_stream, \
             gzip.open(output / "roles.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as role_stream:
            for day, rows in iter_days(binding):
                observation = tracker.update(day, rows)
                daily, roles = observation["daily"], observation["roles"]
                if len(roles) != len(rows) or daily["date"] != day:
                    raise ValueError("Observation output coverage changed")
                json_line(daily_stream, {**daily, **lineage})
                for source, role in zip(rows, roles):
                    if role["symbol"] != source["symbol"] or role["date"] != day:
                        raise ValueError("Role output identity changed")
                    json_line(role_stream, {**role, **lineage, "sourceHashes": source["sourceHashes"]})
                    totals[role["qState"]] += 1
                    rows_written += 1
                days_written += 1
                if time.perf_counter()-last_notice >= 25:
                    print(f"Market-state observations: {days_written}/{len(binding['dates'])} days; {rows_written} rows", flush=True)
                    last_notice = time.perf_counter()
        if rows_written != binding["document"]["sample"]["rows"] or days_written != len(binding["dates"]):
            raise ValueError("Incomplete extraction")
        summary = {"schema": "market-state-observation-summary-v1", "completedAt": now(),
            "protocolHash": protocol["protocolHash"], "roleRows": rows_written, "dailyRows": days_written,
            "qCounts": dict(totals), "elapsedSeconds": time.perf_counter()-started,
            "returnsCalculated": False, "newStrategyTrials": 0, "futureLabelsCreated": False,
            "certified": False, "targetAchieved": False,
            "meaning": "All covered-sample observations, including unknowns. Q is price-strength evidence, not market influence or a trade signal."}
        write(output / "summary.json", summary)
        after_binding = load_binding()
        validate_protocol(output, after_binding)
        if file_hash(output / "protocol.json") != protocol_file_hash or file_hash(output / "execution-sources.zip") != archive_hash:
            raise ValueError("Frozen execution inputs changed during extraction")
        receipt = {"schema": "market-state-observation-completion-v1", "completedAt": now(),
            "protocolHash": protocol["protocolHash"], "roleRows": rows_written, "dailyRows": days_written,
            "returnsCalculated": False, "newStrategyTrials": 0,
            "files": {name: {"path": name, "sha256": file_hash(output / name)} for name in sorted(OUTPUT_FILES)}}
        write(output / "completion.json", receipt)
        return verify_completed(output, protocol)
    except Exception as exc:
        write(output / "failure.json", {"failedAt": now(), "protocolHash": protocol["protocolHash"],
            "exception": type(exc).__name__, "message": str(exc), "roleRowsWritten": rows_written,
            "dailyRowsWritten": days_written, "returnsCalculated": False, "noAutomaticRetry": True})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.freeze_only:
        result = freeze(output)
        print(json.dumps({"frozen": True, "protocolHash": result["protocolHash"], "output": str(output)}))
    else:
        result = execute(output)
        print(json.dumps({"completed": True, "roleRows": result["roleRows"], "dailyRows": result["dailyRows"], "output": str(output)}))


if __name__ == "__main__":
    main()
