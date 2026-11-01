#!/usr/bin/env python3
"""Read-only verification of an isolated cutoff source; no strategy evaluation."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sqlite3
import struct
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-source-v1.json"
REGISTRATION_SHA = "4794213b5f01d90b32ce499d4b06283fce602bfb7b3a49bcfdb3693b268138a8"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_SOURCE_PATH = Path(__file__).resolve()
VERIFIER_SOURCE_SHA256 = file_sha(VERIFIER_SOURCE_PATH)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON: " + value)


def loads(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)


def read(path):
    return loads(Path(path).read_bytes())


def relative(root, name):
    if type(name) is not str:
        raise ValueError("Relative path must be a string")
    value = PurePosixPath(name)
    if not value.parts or value.is_absolute() or ".." in value.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe relative path")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Relative path escapes root")
    return result


def prefix_reference(raw: bytes, cutoff: str) -> dict:
    """Independently decode date words only, preserving every original byte."""
    if type(raw) is not bytes:
        raise TypeError("Raw input must be immutable bytes")
    if type(cutoff) is not str or date.fromisoformat(cutoff).isoformat() != cutoff:
        raise ValueError("Noncanonical cutoff")
    count, tail = divmod(len(raw), 32)
    dates, invalid_indices = [], []
    last = 0
    for index in range(count):
        word = struct.unpack_from("<I", raw, index * 32)[0]
        try:
            day = date(word // 10000, (word // 100) % 100, word % 100).isoformat()
        except ValueError:
            day = None
            invalid_indices.append(index)
        dates.append(day)
        if day is not None and day <= cutoff:
            last = index + 1
    chosen = dates[:last]
    valid_chosen = [day for day in chosen if day is not None]
    issues = []
    if any(day is None for day in chosen):
        issues.append("invalidDateInPrefix")
    if any(day > cutoff for day in valid_chosen):
        issues.append("dateAfterEndInPrefix")
    if any(right <= left for left, right in zip(valid_chosen, valid_chosen[1:])):
        issues.append("nonIncreasingDateInPrefix")
    if tail:
        issues.append("incompleteRawTailHasUnknownDate")
    prefix = raw[:last * 32]
    ordered = [(index, day) for index, day in enumerate(dates) if day is not None]
    transitions = [{"previousRecordIndex": left_i, "recordIndex": right_i,
                    "previousDate": left, "date": right}
                   for (left_i, left), (right_i, right) in zip(ordered, ordered[1:]) if right <= left]
    return {"prefixThroughEndSha256": sha(prefix), "prefixThroughEndBytes": len(prefix),
            "prefixThroughEndRecords": last, "prefixIdentityUsable": not issues,
            "prefixIdentityIssues": issues, "rawRecordCount": count,
            "invalidDateRecords": len(invalid_indices), "invalidDateRecordIndices": invalid_indices,
            "trailingBytes": tail, "dateOrderTransitions": transitions, "firstDate": chosen[0] if chosen else None,
            "lastDate": chosen[-1] if chosen else None}


class Audit:
    def __init__(self):
        self.checks = 0
        self.failure_count = 0
        self.issues = []

    def check(self, condition, message):
        self.checks += 1
        if not condition:
            self.failure_count += 1
            if len(self.issues) < 100:
                self.issues.append(message)
        return bool(condition)

    def equal(self, actual, expected, message):
        return self.check(encoded(actual) == encoded(expected), message)

    def require(self, condition, message):
        if not self.check(condition, message):
            raise ValueError(message)


def check_sqlite(database, tdx, audit):
    """Inspect only the newly materialized DB, with an explicit read-only URI."""
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        audit.require(db.execute("PRAGMA query_only").fetchone() == (1,), "SQLite query-only mode")
        audit.require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity")
        objects = db.execute("SELECT type,name,tbl_name FROM sqlite_schema WHERE NOT (type='index' AND name LIKE 'sqlite_autoindex_%') ORDER BY type,name").fetchall()
        audit.equal(objects, [("table", "meta", "meta"), ("table", "securities", "securities")], "Only the two registered SQLite tables")
        columns = {
            "meta": [(0, "key", "TEXT", 0, None, 1), (1, "value", "TEXT", 1, None, 0)],
            "securities": [(0, "symbol", "TEXT", 0, None, 1), (1, "name", "TEXT", 1, None, 0)],
        }
        for table, expected in columns.items():
            audit.equal(db.execute("PRAGMA table_info(" + table + ")").fetchall(), expected, "SQLite columns: " + table)
        meta = db.execute("SELECT key,value FROM meta ORDER BY key").fetchall()
        audit.require(len(meta) == 1 and meta[0][0] == "path", "Exactly one SQLite meta.path")
        audit.equal(loads(meta[0][1]), str(tdx.resolve()), "SQLite path points to the new isolated TDX root")
        audit.require(db.execute("SELECT COUNT(*) FROM securities").fetchone() == (0,), "No names or securities copied into SQLite")


def inventory(output):
    files, directories = set(), set()
    for path in output.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Output contains a symbolic link or junction")
        if not path.resolve().is_relative_to(output.resolve()):
            raise ValueError("Output escapes its isolated directory")
        name = path.relative_to(output).as_posix()
        if path.is_file():
            files.add(name)
        elif path.is_dir():
            directories.add(name)
        else:
            raise ValueError("Unexpected output file type")
    return files, directories


FLAGS = ("returnsCalculated", "featuresCalculated", "certified", "targetAchieved")
IMPLEMENTATION = {"scripts/materialize-signal-supplemental-source.py",
                  "scripts/verify-signal-supplemental-source.py", "tests/test_signal_supplemental_source.py"}
QUALITY_FILES = {"protocol.json", "completion.json", "summary.json", "symbol-summary.json",
                 "daily-coverage.json", "symbols.jsonl.gz", "input-sources.zip", "execution-sources.zip",
                 "freeze-attempt.json", "audit-attempt.json", "independent-verification.json"}


def false_flags(value, audit, label):
    for flag in FLAGS:
        audit.require(value.get(flag) is False, label + " false-only: " + flag)


def canonical_protocol(value, audit, label):
    audit.require(sha(encoded({key: item for key, item in value.items() if key != "protocolHash"})) == value.get("protocolHash"), label + " canonical hash")


def _verify(output, root, audit, report):
    registration_path = Path(REGISTRATION).resolve()
    registration_name = registration_path.relative_to(root).as_posix()
    audit.require(file_sha(registration_path) == REGISTRATION_SHA, "Pinned source registration")
    document = read(registration_path)
    audit.require(document.get("schema") == "supplemental-source-registration-v1", "Source registration schema")
    audit.require(document.get("stage") == "cutoff-source-materialization-only", "Source-only stage")
    false_flags(document, audit, "Registration")
    protocol, manifest, receipt = (read(output / name) for name in ("protocol.json", "manifest.json", "completion.json"))
    canonical_protocol(protocol, audit, "Source protocol")
    audit.require(protocol.get("schema") == "supplemental-source-protocol-v1", "Source protocol schema")
    audit.require(protocol.get("registrationSha256") == REGISTRATION_SHA, "Protocol registration binding")
    audit.equal(protocol.get("registration"), document, "Exact embedded registration")
    report["protocolHash"] = protocol["protocolHash"]
    for value, label, schema in ((manifest, "Manifest", "supplemental-source-manifest-v1"),
                                  (receipt, "Completion", "supplemental-source-completion-v1")):
        audit.require(value.get("schema") == schema and value.get("protocolHash") == protocol["protocolHash"], label + " identity")
        false_flags(value, audit, label)
    false_flags(protocol, audit, "Protocol")
    audit.require(manifest.get("prefixNumericQualityBefore2023Verified") is False, "No retrospective numeric-quality certification")
    audit.require(type(manifest.get("limitations")) is list and bool(manifest["limitations"]), "Materialization limitations recorded")
    attempt = read(output / "attempt.json")
    audit.require(attempt.get("schema") == "supplemental-source-attempt-v1" and attempt.get("registrationSha256") == REGISTRATION_SHA and attempt.get("qualityProtocolHash") == document["quality"]["protocolHash"], "Attempt identity")
    false_flags(attempt, audit, "Attempt")
    audit.require(not (output / "failure.json").exists(), "Completed source has no failed materialization marker")

    quality = document["quality"]
    audit.require(set(quality["files"]) == QUALITY_FILES, "Complete registered quality-file closure")
    quality_root = relative(root, quality["directory"])
    metadata = {registration_name: REGISTRATION_SHA,
                document["featureCalendar"]["path"]: document["featureCalendar"]["sha256"],
                document["frozenCandidate"]["path"]: document["frozenCandidate"]["sha256"]}
    input_pins = {**metadata, **{quality["directory"] + "/" + name: value for name, value in quality["files"].items()}}
    audit.equal(protocol.get("inputFilesSha256"), input_pins, "Complete immutable input binding")
    audit.equal(protocol.get("metadata"), metadata, "Exact metadata closure")
    audit.require(set(protocol["implementation"]) == IMPLEMENTATION, "Exact implementation closure")
    source_hashes, input_raw = {}, None
    for name, expected in input_pins.items():
        path = relative(root, name)
        if name == quality["directory"] + "/input-sources.zip":
            input_raw = path.read_bytes()
            actual = sha(input_raw)
        else:
            actual = file_sha(path)
        source_hashes[name] = actual
        audit.require(actual == expected, "Immutable input SHA: " + name)
    report["sourceHashes"] = source_hashes
    report["implementation"] = protocol["implementation"]
    execution_raw = (output / "execution-sources.zip").read_bytes()
    audit.require(sha(execution_raw) == protocol["executionArchiveSha256"], "Whole execution archive SHA")
    with zipfile.ZipFile(io.BytesIO(execution_raw)) as archive:
        pins = {**metadata, **protocol["implementation"]}
        names = archive.namelist()
        audit.require(len(names) == len(set(names)) and set(names) == set(pins), "Exact execution archive members")
        for name, expected in pins.items():
            audit.require(sha(archive.read(name)) == expected, "Archived source SHA: " + name)
            audit.require(file_sha(relative(root, name)) == expected, "Current source SHA: " + name)

    quality_protocol = read(quality_root / "protocol.json")
    quality_receipt = read(quality_root / "completion.json")
    quality_audit = read(quality_root / "independent-verification.json")
    quality_summary = read(quality_root / "symbol-summary.json")
    canonical_protocol(quality_protocol, audit, "Quality protocol")
    audit.require(protocol.get("qualityProtocolHash") == quality["protocolHash"] == quality_protocol["protocolHash"], "Parent quality protocol binding")
    audit.require(quality_protocol.get("registrationSha256") == quality["registrationSha256"], "Parent quality registration binding")
    audit.require(quality_receipt.get("schema") == "supplemental-interval-quality-completion-v1" and quality_receipt.get("protocolHash") == quality["protocolHash"], "Parent completion identity")
    quality_output_names = {"summary.json", "symbol-summary.json", "daily-coverage.json", "symbols.jsonl.gz"}
    audit.equal(quality_receipt.get("files"), {name: quality["files"][name] for name in quality_output_names}, "Parent completion output closure")
    audit.require(quality_audit.get("passed") is True and type(quality_audit.get("failureCount")) is int and quality_audit["failureCount"] == 0, "Parent independent audit passed without failures")
    audit.require(quality_audit.get("schema") == "supplemental-interval-quality-independent-verification-v1" and quality_audit.get("coreFieldsNotIndependentlyRecomputed") == [], "Parent independent audit full scope")
    audit.require(quality_audit.get("strategyRanksCalculated") is False and quality_audit.get("candidateGatesCalculated") is False, "Parent audit contains no strategy evaluation")
    audit.require(quality_audit.get("protocolHash") == quality["protocolHash"] and quality_audit.get("verifierSha256") == quality["independentVerifierSha256"], "Parent audit provenance")
    audit.require(quality_protocol["implementation"].get("scripts/verify-signal-interval-quality.py") == quality["independentVerifierSha256"], "Parent verifier bound in parent source closure")
    audit.equal(quality_audit.get("sourceHashes"), {name: value for name, value in quality["files"].items() if name != "independent-verification.json"}, "Parent audit exact source hashes")
    audit.require(quality_protocol.get("inputArchiveSha256") == quality["files"]["input-sources.zip"] and quality_protocol.get("executionArchiveSha256") == quality["files"]["execution-sources.zip"], "Parent archive references")
    for value in (quality_protocol, quality_receipt, quality_audit):
        for key in ("returnsCalculated", "certified", "targetAchieved"):
            audit.require(value.get(key) is False, "Parent quality false-only: " + key)

    fixed = document["fixedPopulation"]["symbols"]
    benchmark = document["benchmarkSymbol"]
    excluded = document["excludedOtherPinnedSymbols"]
    audit.require(fixed == sorted(set(fixed)) and len(fixed) == document["fixedPopulation"]["count"] and benchmark not in fixed, "Fixed population unique and benchmark separate")
    audit.require(sha(encoded(fixed)) == document["fixedPopulation"]["sha256"], "Fixed symbol-list hash")
    audit.require(excluded == sorted(set(excluded)) and not set(excluded).intersection(fixed + [benchmark]), "Excluded identities form a separate partition")
    for key in ("symbols", "count", "sha256"):
        audit.equal(quality_protocol["registration"]["fixedPopulation"][key], document["fixedPopulation"][key], "Original fixed population unchanged: " + key)
    audit.equal(manifest.get("fixedSymbols"), fixed, "Manifest fixed symbols unchanged")
    audit.require(manifest.get("benchmarkSymbol") == benchmark and manifest.get("cutoff") == document["cutoff"], "Manifest benchmark/cutoff")
    selected = set(fixed + [benchmark])
    audit.require(document["expectedMaterializedFiles"] == len(selected), "Exact materialized population count")
    parent_sources = quality_protocol["sources"]
    parent_symbols = [item["symbol"] for item in parent_sources]
    audit.require(len(parent_symbols) == len(set(parent_symbols)) and set(parent_symbols) == selected | set(excluded), "Complete parent identity partition")
    audit.require(len(quality_summary) == len(parent_symbols) and len({item["symbol"] for item in quality_summary}) == len(quality_summary), "Unique complete parent symbol summaries")
    summaries = {item["symbol"]: item for item in quality_summary}
    audit.require(set(summaries) == set(parent_symbols), "Parent summary identity closure")
    audit.equal(quality_protocol["sourceGroupCounts"], {"fixed": len(fixed), "benchmark": 1, "otherPinned": len(excluded)}, "Parent source partition counts")
    audit.require(quality_audit.get("symbolDateCellsVerified") == len(parent_symbols) * len(quality_protocol["calendar"]["dates"]), "Parent audit covered all symbol/date cells")
    audit.require(quality_protocol["registration"]["auditEnd"] == document["cutoff"], "Prefix cutoff equals audited cutoff")
    calendar = read(relative(root, document["featureCalendar"]["path"]))
    audit.equal(calendar, quality_protocol["calendar"], "Unchanged registered calendar")
    audit.require(calendar["start"] == document["featureCalendar"]["warmupStart"] and calendar["end"] == document["cutoff"], "Registered calendar bounds")
    audit.require(calendar["dates"] == sorted(set(calendar["dates"])), "Ordered unique calendar")
    for day in calendar["dates"]:
        audit.require(type(day) is str and date.fromisoformat(day).isoformat() == day and calendar["start"] <= day <= calendar["end"], "Canonical in-range calendar date")
    audit.equal(sum(day < document["signalStart"] for day in calendar["dates"]), document["featureCalendar"]["expectedWarmupSessions"], "Registered warmup sessions")
    audit.equal(sum(document["signalStart"] <= day <= document["cutoff"] for day in calendar["dates"]), document["featureCalendar"]["expectedTargetSessions"], "Registered target sessions")

    expected_rows, excluded_rows, expected_day_files = [], [], {}
    with zipfile.ZipFile(io.BytesIO(input_raw)) as archive:
        names = archive.namelist()
        members = [item["member"] for item in parent_sources if item.get("member") is not None]
        audit.require(len(names) == len(set(names)) and len(members) == len(set(members)) and set(names) == set(members), "Parent raw archive exact members")
        for source in parent_sources:
            symbol, group = source["symbol"], source["group"]
            expected_group = "benchmark" if symbol == benchmark else "fixed" if symbol in selected else "otherPinned"
            audit.require(group == expected_group, "Source group identity: " + symbol)
            prior = summaries[symbol]
            audit.require(prior.get("group") == group and prior.get("sourceReadStatus") == source["sourceReadStatus"], "Parent summary source identity: " + symbol)
            if symbol not in selected:
                excluded_rows.append({"symbol": symbol, "group": group, "sourceMember": source.get("member"),
                                      "sourceFullSha256": source["expectedSha256"], "reason": document["exclusionReason"]})
                # Excluded identity is still present in the complete audited input closure.
                continue
            audit.require(source.get("sourceReadStatus") == "available" and type(source.get("member")) is str, "Every selected source remains available: " + symbol)
            raw = archive.read(source["member"])
            audit.require(sha(raw) == source.get("sha256") == source.get("expectedSha256"), "Original raw member SHA: " + symbol)
            audit.require(type(source.get("byteLength")) is int and source["byteLength"] == len(raw), "Original raw member length: " + symbol)
            reference = prefix_reference(raw, document["cutoff"])
            for key in ("prefixThroughEndSha256", "prefixThroughEndBytes", "prefixThroughEndRecords", "prefixIdentityUsable", "prefixIdentityIssues",
                        "rawRecordCount", "invalidDateRecords", "invalidDateRecordIndices", "trailingBytes"):
                audit.equal(prior.get(key), reference[key], "Independent original-date/prefix identity " + key + ": " + symbol)
            audit.equal([{key: value for key, value in item.items() if key != "withinInterval"} for item in prior["dateOrderTransitions"]], reference["dateOrderTransitions"], "Whole-source date-order diagnostics: " + symbol)
            audit.require(reference["prefixIdentityUsable"] is True and reference["prefixThroughEndRecords"] > 0, "Usable nonempty prefix for every selected symbol: " + symbol)
            name = "tdx/vipdoc/" + symbol[:2] + "/lday/" + symbol + ".day"
            prefix = raw[:reference["prefixThroughEndBytes"]]
            materialized = relative(output, name).read_bytes()
            audit.require(materialized == prefix, "Materialized file is the exact immutable original prefix: " + symbol)
            expected_day_files[name] = sha(materialized)
            expected_rows.append({"symbol": symbol, "group": group, "sourceMember": source["member"],
                "sourceFullSha256": source["sha256"], "sourceBytes": len(raw), "path": name,
                "prefixSha256": reference["prefixThroughEndSha256"], "prefixBytes": len(prefix),
                "prefixRecords": reference["prefixThroughEndRecords"], "firstDate": reference["firstDate"], "lastDate": reference["lastDate"],
                "sourceStructuralIssue": prior["summary"]["structureIssue"], "sourceStructureReasons": prior["summary"]["structureReasons"]})
    # Lexical file order is explicit; it is not a strategy rank or selection step.
    expected_rows.sort(key=lambda row: row["symbol"])
    excluded_rows.sort(key=lambda row: row["symbol"])
    audit.equal(manifest.get("sourceFiles"), expected_rows, "All selected source-to-prefix mappings")
    audit.equal(manifest.get("excludedSources"), excluded_rows, "Every other-pinned exclusion remains visible")

    workspace_path = document["workspace"]["relativePath"] + "/" + document["workspace"]["database"]
    audit.require(workspace_path == "workspace/market.sqlite3" and document["workspace"]["tdxRelativePath"] == "tdx", "Only the registered isolated workspace paths")
    database = relative(output, workspace_path)
    tdx = relative(output, document["workspace"]["tdxRelativePath"])
    check_sqlite(database, tdx, audit)
    workspace_expected = {"path": workspace_path, "sha256": file_sha(database), "tdxPath": str(tdx.resolve()),
        "integrityCheck": "ok", "tables": {"meta": ["key", "value"], "securities": ["symbol", "name"]}, "metaRows": 1, "securitiesRows": 0}
    audit.equal(manifest.get("workspace"), workspace_expected, "Complete minimal workspace manifest")
    expected_files = {**expected_day_files, workspace_path: file_sha(database)}
    for name in ("protocol.json", "attempt.json", "manifest.json", "execution-sources.zip"):
        expected_files[name] = file_sha(output / name)
    audit.equal(receipt.get("files"), expected_files, "Exact completion file/hash closure")
    files, directories = inventory(output)
    # One successful post-materialization verification is a permitted append-only artifact.
    if "independent-verification.json" in files:
        previous = read(output / "independent-verification.json")
        audit.require(previous.get("passed") is True and previous.get("protocolHash") == protocol["protocolHash"], "Existing independent report belongs to this successful source")
        files.remove("independent-verification.json")
    audit.equal(sorted(files), sorted(set(expected_files) | {"completion.json"}), "Exact output files; no names, TNF, caches or sidecars")
    expected_directories = {parent.as_posix() for name in expected_files for parent in PurePosixPath(name).parents if parent.as_posix() != "."}
    audit.equal(sorted(directories), sorted(expected_directories), "Exact output directory tree")
    for name, expected in expected_files.items():
        audit.check(file_sha(relative(output, name)) == expected, "Output unchanged during independent verification: " + name)
    audit.check(file_sha(output / "completion.json") == report["completionSha256"], "Completion unchanged during verification")
    for name, expected in input_pins.items():
        audit.check(file_sha(relative(root, name)) == expected, "Immutable input unchanged during verification: " + name)
    for name, expected in protocol["implementation"].items():
        audit.check(file_sha(relative(root, name)) == expected, "Implementation unchanged during verification: " + name)
    report.update(materializedFiles=len(expected_rows), fixedSymbols=len(fixed), benchmarkSymbols=1,
                  excludedOtherPinnedSymbols=len(excluded_rows), prefixRecords=sum(row["prefixRecords"] for row in expected_rows),
                  prefixBytes=sum(row["prefixBytes"] for row in expected_rows), resultFileHashes=expected_files)


def verify(output: Path, root: Path = ROOT) -> dict:
    """Return a complete read-only audit report; do not write it or repair inputs."""
    started = time.monotonic()
    output, root = Path(output).resolve(), Path(root).resolve()
    audit = Audit()
    report = {"schema": "supplemental-source-independent-verification-v1", "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "verifierSha256": VERIFIER_SOURCE_SHA256, "returnsCalculated": False, "featuresCalculated": False,
        "certified": False, "targetAchieved": False, "prefixNumericQualityBefore2023Verified": False,
        "scope": "Independently decode all original date words for every selected frozen raw member, reconstruct exact cutoff-prefix bytes and identities, compare old audited prefix references, verify the full fixed-sample/benchmark/excluded partition and all output files, minimal read-only SQLite, source/code archives, attempts and completion. No production conversion code is imported.",
        "limitations": ["OHLC/amount quality relies on the already hash-bound independent interval-quality audit; this verifier does not recertify numeric content before 2023 or evaluate later-tail numeric values.",
            "All prefixes retain original missing dates and prices. Fixed local survivor bias, historical identity/rights gaps and prior interval exposure remain.",
            "This is source transformation evidence only; no signals, returns, tradability or economic net30 certification."]}
    try:
        audit.require(file_sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "Verifier matches imported bytes")
        report["completionSha256"] = file_sha(output / "completion.json")
        _verify(output, root, audit, report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, EOFError, sqlite3.Error, zipfile.BadZipFile) as error:
        audit.check(False, type(error).__name__ + ": " + str(error))
    try:
        audit.check(file_sha(VERIFIER_SOURCE_PATH) == VERIFIER_SOURCE_SHA256, "Verifier source unchanged after verification")
    except OSError as error:
        audit.check(False, "Verifier unavailable after verification: " + str(error))
    report.update(passed=audit.failure_count == 0, checks=audit.checks, failureCount=audit.failure_count,
                  issues=audit.issues, elapsedSeconds=round(time.monotonic() - started, 6))
    return report


def save_report(output, report):
    filename = "independent-verification.json" if report["passed"] else "independent-verification-failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = Path(output) / filename
    with path.open("xb") as stream:
        stream.write(encoded(report) + b"\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/strategy-search/supplemental-source-2024-2025-v1")
    args = parser.parse_args(argv)
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "failureCount": report["failureCount"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
