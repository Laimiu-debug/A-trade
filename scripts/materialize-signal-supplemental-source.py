#!/usr/bin/env python3
"""Materialize registered raw prefixes from the frozen quality ZIP only.

No live TDX file, original database, strategy, feature or price decoder is used.
The complete source bytes retain their provenance; only original prefix bytes
enter the isolated TDX tree. Existing successful or failed outputs are immutable.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import struct
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-source-v1.json"
REGISTRATION_SHA = "4794213b5f01d90b32ce499d4b06283fce602bfb7b3a49bcfdb3693b268138a8"
QUALITY_FILES = {"protocol.json", "completion.json", "summary.json", "symbol-summary.json",
    "daily-coverage.json", "symbols.jsonl.gz", "input-sources.zip", "execution-sources.zip",
    "freeze-attempt.json", "audit-attempt.json", "independent-verification.json"}
QUALITY_OUTPUTS = {"summary.json", "symbol-summary.json", "daily-coverage.json", "symbols.jsonl.gz"}
IMPLEMENTATION = {"scripts/materialize-signal-supplemental-source.py", "scripts/verify-signal-supplemental-source.py",
                  "tests/test_signal_supplemental_source.py"}
EVIDENCE_FILES = {"protocol.json", "attempt.json", "manifest.json", "execution-sources.zip"}
FLAGS = {"returnsCalculated": False, "featuresCalculated": False, "certified": False, "targetAchieved": False}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


LOADED_SOURCE_SHA = file_sha(__file__)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON value: " + value)


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_pairs, parse_constant=_nonfinite)


def save(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value) + b"\n")


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same(left, right):
    return encoded(left) == encoded(right)


def _day(value):
    require(type(value) is str and date.fromisoformat(value).isoformat() == value, "Expected canonical ISO date")
    return value


def _path(name):
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in name and ":" not in name, "Unsafe repository-relative input")
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()), "Input path escapes repository")
    return path


def _name(path):
    return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()


def _symbol(symbol):
    require(type(symbol) is str and len(symbol) == 8 and symbol[:2] in ("sh", "sz") and symbol[2:].isdigit(), "Invalid symbol")
    return symbol


def _member(source):
    symbol = _symbol(source["symbol"])
    member = source.get("member")
    if member is None:
        return None
    require(type(member) is str, "Invalid source ZIP member")
    parts = PurePosixPath(member).parts
    require(len(parts) == 3 and parts[:2] == ("raw", symbol[:2]) and
            parts[2] in (symbol + ".day", symbol[2:] + ".day") and "\\" not in member,
            "Source ZIP alias/path disagrees with symbol")
    return member


def date_prefix(raw, cutoff):
    """Check date structure only, preserving the audited prefix semantics.

    Invalid or unordered complete records after the last <= cutoff record do
    not veto a structurally sound prefix. They remain whole-source diagnostics.
    An incomplete raw tail has unknown date and always makes the prefix unsafe.
    """
    require(type(raw) is bytes, "Prefix input must be bytes")
    _day(cutoff)
    count, remainder = divmod(len(raw), 32)
    dates, invalid, transitions = [], [], []
    previous = None
    end_index = 0
    for index in range(count):
        word = struct.unpack_from("<I", raw, index * 32)[0]
        try:
            day = date(word // 10000, word // 100 % 100, word % 100).isoformat()
        except ValueError:
            day = None
            invalid.append(index)
        dates.append(day)
        if day is None:
            continue
        if day <= cutoff:
            end_index = index + 1
        if previous is not None and day <= previous[1]:
            transitions.append({"previousRecordIndex": previous[0], "recordIndex": index,
                                "previousDate": previous[1], "date": day})
        previous = (index, day)
    issues = []
    if any(index < end_index for index in invalid):
        issues.append("invalidDateInPrefix")
    if any(day is not None and day > cutoff for day in dates[:end_index]):
        issues.append("dateAfterEndInPrefix")
    if any(item["recordIndex"] < end_index for item in transitions):
        issues.append("nonIncreasingDateInPrefix")
    if remainder:
        issues.append("incompleteRawTailHasUnknownDate")
    require(end_index > 0, "Registered source has an empty cutoff prefix")
    require(not issues, "Unusable cutoff prefix: " + ",".join(issues))
    prefix_bytes = end_index * 32
    return {"prefixBytes": prefix_bytes, "prefixRecords": end_index, "prefixSha256": sha(raw[:prefix_bytes]),
            "firstDate": dates[0], "lastDate": dates[end_index - 1],
            "rawRecordCount": count, "trailingBytes": remainder,
            "invalidDateRecordIndices": invalid, "dateOrderTransitions": transitions}


def load_binding():
    require(file_sha(REGISTRATION) == REGISTRATION_SHA, "Source registration changed")
    document = read(REGISTRATION)
    require(document.get("schema") == "supplemental-source-registration-v1" and
            document.get("stage") == "cutoff-source-materialization-only", "Unexpected source registration")
    require(all(document.get(key) is value for key, value in FLAGS.items()), "Source conversion cannot calculate outcomes or certify")
    _day(document["cutoff"])
    _day(document["signalStart"])
    require(document["signalStart"] <= document["cutoff"], "Invalid supplemental interval")
    fixed = document["fixedPopulation"]["symbols"]
    require(fixed == sorted(set(fixed)) and type(document["fixedPopulation"]["count"]) is int and
            len(fixed) == document["fixedPopulation"]["count"] and sha(encoded(fixed)) == document["fixedPopulation"]["sha256"], "Fixed population changed")
    benchmark = _symbol(document["benchmarkSymbol"])
    require(benchmark not in fixed and document["expectedMaterializedFiles"] == len(fixed) + 1, "Benchmark/population mismatch")
    for symbol in fixed:
        _symbol(symbol)
    expected_excluded = document["excludedOtherPinnedSymbols"]
    require(expected_excluded == sorted(set(expected_excluded)) and not set(expected_excluded).intersection([*fixed, benchmark]), "Excluded population overlap")
    workspace = document["workspace"]
    require(workspace["relativePath"] == "workspace" and workspace["tdxRelativePath"] == "tdx" and
            workspace["database"] == "market.sqlite3" and same(workspace["tables"], {"meta": ["key", "value"], "securities": ["symbol", "name"]}) and
            type(workspace["securitiesRows"]) is int and workspace["securitiesRows"] == 0, "Unexpected minimal workspace contract")
    quality = document["quality"]
    require(set(quality["files"]) == QUALITY_FILES, "Incomplete quality package binding")
    directory = _path(quality["directory"])
    files = {_name(REGISTRATION): REGISTRATION_SHA}
    for name, expected in quality["files"].items():
        path = directory / name
        require(file_sha(path) == expected, "Quality input changed: " + name)
        files[_name(path)] = expected
    for spec in (document["featureCalendar"], document["frozenCandidate"]):
        path = _path(spec["path"])
        require(file_sha(path) == spec["sha256"], "Registered metadata changed")
        files[_name(path)] = spec["sha256"]
    protocol, completion, audit = (read(directory / name) for name in
        ("protocol.json", "completion.json", "independent-verification.json"))
    require(protocol.get("schema") == "supplemental-interval-quality-protocol-v1" and
            sha(encoded({k: v for k, v in protocol.items() if k != "protocolHash"})) == protocol.get("protocolHash") == quality["protocolHash"] and
            protocol["registrationSha256"] == quality["registrationSha256"], "Quality protocol identity differs")
    require(completion.get("schema") == "supplemental-interval-quality-completion-v1" and
            completion.get("protocolHash") == protocol["protocolHash"] and set(completion["files"]) == QUALITY_OUTPUTS,
            "Quality completion differs")
    require(all(completion["files"][name] == quality["files"][name] for name in QUALITY_OUTPUTS), "Quality receipt output SHA differs")
    require(protocol["inputArchiveSha256"] == quality["files"]["input-sources.zip"] and
            protocol["executionArchiveSha256"] == quality["files"]["execution-sources.zip"], "Quality ZIP binding differs")
    require(audit.get("schema") == "supplemental-interval-quality-independent-verification-v1" and
            audit.get("passed") is True and type(audit.get("failureCount")) is int and audit["failureCount"] == 0 and
            audit.get("protocolHash") == protocol["protocolHash"] and audit.get("verifierSha256") == quality["independentVerifierSha256"] and
            audit.get("coreFieldsNotIndependentlyRecomputed") == [], "Quality independent verification is not accepted")
    require(same(audit["sourceHashes"], {key: value for key, value in quality["files"].items() if key != "independent-verification.json"}), "Quality verified input/output closure differs")
    for obj in (protocol, completion, audit):
        require(all(obj.get(key) is False for key in ("returnsCalculated", "certified", "targetAchieved")), "Quality result scope changed")
    require(audit.get("strategyRanksCalculated") is False and audit.get("candidateGatesCalculated") is False, "Quality audit scope changed")
    calendar = read(_path(document["featureCalendar"]["path"]))
    require(same(calendar, protocol["calendar"]) and calendar["end"] == document["cutoff"] and
            protocol["registration"]["auditEnd"] == document["cutoff"], "Cutoff/calendar differs from audited prefix")
    require(calendar["start"] == document["featureCalendar"]["warmupStart"] and
            calendar["dates"] == sorted(set(calendar["dates"])), "Feature warmup calendar changed")
    for day in calendar["dates"]:
        _day(day)
        require(calendar["start"] <= day <= calendar["end"], "Calendar date outside interval")
    require(sum(day < document["signalStart"] for day in calendar["dates"]) == document["featureCalendar"]["expectedWarmupSessions"] and
            sum(document["signalStart"] <= day <= document["cutoff"] for day in calendar["dates"]) == document["featureCalendar"]["expectedTargetSessions"], "Calendar warmup/target counts differ")
    require(same(protocol["registration"]["fixedPopulation"], document["fixedPopulation"]), "Original quality fixed population descriptor differs")
    sources = protocol["sources"]
    require(len({row["symbol"] for row in sources}) == len(sources), "Duplicate quality source symbol")
    summaries = read(directory / "symbol-summary.json")
    require([row["symbol"] for row in summaries] == [row["symbol"] for row in sources], "Symbol summary order/population mismatch")
    summary_map = {row["symbol"]: row for row in summaries}
    selected, excluded = [], []
    for source in sources:
        symbol = _symbol(source["symbol"])
        _member(source)
        group = "benchmark" if symbol == benchmark else "fixed" if symbol in fixed else "otherPinned"
        require(source["group"] == group and summary_map[symbol]["group"] == group and
                summary_map[symbol]["sourceReadStatus"] == source["sourceReadStatus"], "Quality grouping/availability mismatch")
        (selected if group != "otherPinned" else excluded).append(source)
    require(sorted(row["symbol"] for row in selected) == sorted([*fixed, benchmark]) and
            sorted(row["symbol"] for row in excluded) == expected_excluded, "No omitted, added or replaced source is permitted")
    group_counts = dict(Counter(row["group"] for row in sources))
    require(same(group_counts, protocol["sourceGroupCounts"]) and same(group_counts, audit["sourceGroupCounts"]) and
            audit["symbolDateCellsVerified"] == len(sources) * len(calendar["dates"]), "Quality source/count coverage mismatch")
    return {"document": document, "qualityProtocol": protocol, "qualityCompletion": completion,
            "qualityAudit": audit, "symbolSummaries": summary_map, "selected": sorted(selected, key=lambda row: row["symbol"]),
            "excluded": sorted(excluded, key=lambda row: row["symbol"]), "files": files,
            "qualityDirectory": directory, "calendar": calendar}


def implementation_hashes():
    require(file_sha(__file__) == LOADED_SOURCE_SHA, "Materializer source changed after import")
    return {name: file_sha(_path(name)) for name in sorted(IMPLEMENTATION)}


def _input_check(binding):
    for name, expected in binding["files"].items():
        require(file_sha(_path(name)) == expected, "Input changed during materialization: " + name)


def _inventory(output):
    files = {}
    for path in output.rglob("*"):
        require(not path.is_symlink(), "Unexpected output symlink")
        if path.is_file():
            files[path.relative_to(output).as_posix()] = file_sha(path)
    return files


def _database_info(output):
    path = output / "workspace/market.sqlite3"
    before = file_sha(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity check failed")
        require(db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == [("meta",), ("securities",)], "Unexpected SQLite tables")
        require(db.execute("SELECT name FROM sqlite_master WHERE type IN ('view','trigger')").fetchall() == [], "Unexpected SQLite views/triggers")
        expected_columns = {
            "meta": [(0, "key", "TEXT", 0, None, 1), (1, "value", "TEXT", 1, None, 0)],
            "securities": [(0, "symbol", "TEXT", 0, None, 1), (1, "name", "TEXT", 1, None, 0)]}
        for table, columns in expected_columns.items():
            require(db.execute("PRAGMA table_info(" + table + ")").fetchall() == columns, "Unexpected SQLite columns")
        require(db.execute("SELECT key,value FROM meta").fetchall() == [("path", json.dumps(str((output / "tdx").resolve())))], "Unexpected SQLite path row")
        require(db.execute("SELECT symbol,name FROM securities").fetchall() == [], "Current names must remain empty")
    require(file_sha(path) == before, "Read-only SQLite verification changed bytes")
    return {"path": "workspace/market.sqlite3", "sha256": before, "tdxPath": str((output / "tdx").resolve()),
            "integrityCheck": "ok", "tables": {"meta": ["key", "value"], "securities": ["symbol", "name"]},
            "metaRows": 1, "securitiesRows": 0}


def _create_database(output):
    directory = output / "workspace"
    directory.mkdir()
    with closing(sqlite3.connect(directory / "market.sqlite3")) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE securities(symbol TEXT PRIMARY KEY, name TEXT NOT NULL)")
        db.execute("INSERT INTO meta(key,value) VALUES(?,?)", ("path", json.dumps(str((output / "tdx").resolve()))))
        db.commit()
    return _database_info(output)


def _archive(output, implementation, metadata):
    require(not set(implementation).intersection(metadata), "Ambiguous code/metadata closure")
    with zipfile.ZipFile(output / "execution-sources.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, expected in sorted({**implementation, **metadata}.items()):
            raw = _path(name).read_bytes()
            require(sha(raw) == expected, "Code/metadata changed while archiving")
            archive.writestr(name, raw)
    return file_sha(output / "execution-sources.zip")


def _check_archive(output, protocol):
    path = output / "execution-sources.zip"
    require(file_sha(path) == protocol["executionArchiveSha256"], "Execution ZIP whole hash differs")
    with zipfile.ZipFile(path) as archive:
        pins = {**protocol["implementation"], **protocol["metadata"]}
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == set(pins), "Execution ZIP member closure differs")
        for name, expected in pins.items():
            require(sha(archive.read(name)) == expected, "Execution ZIP member changed")


def materialize(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Existing output is immutable; no restart or overwrite")
    # Binding preflight reads only registered metadata and immutable package files.
    binding = load_binding()
    document = binding["document"]
    require(not output.is_relative_to(binding["qualityDirectory"]) and
            not binding["qualityDirectory"].is_relative_to(output) and
            not any(_path(name).is_relative_to(output) for name in binding["files"]), "Output overlaps a frozen input")
    output.mkdir(parents=True, exist_ok=False)
    save(output / "attempt.json", {"schema": "supplemental-source-attempt-v1", "startedAt": now(),
        "registrationSha256": REGISTRATION_SHA, "qualityProtocolHash": document["quality"]["protocolHash"], **FLAGS})
    try:
        implementation = implementation_hashes()
        metadata = {_name(REGISTRATION): REGISTRATION_SHA,
                    document["featureCalendar"]["path"]: document["featureCalendar"]["sha256"],
                    document["frozenCandidate"]["path"]: document["frozenCandidate"]["sha256"]}
        protocol = {"schema": "supplemental-source-protocol-v1", "createdAt": now(),
            "registrationSha256": REGISTRATION_SHA, "registration": document, "inputFilesSha256": binding["files"],
            "qualityProtocolHash": document["quality"]["protocolHash"], "implementation": implementation, "metadata": metadata,
            "executionArchiveSha256": _archive(output, implementation, metadata), **FLAGS}
        protocol["protocolHash"] = sha(encoded(protocol))
        save(output / "protocol.json", protocol)
        protocol_sha = file_sha(output / "protocol.json")
        source_files = []
        with zipfile.ZipFile(binding["qualityDirectory"] / "input-sources.zip") as archive:
            members = [row["member"] for row in binding["qualityProtocol"]["sources"] if row["member"] is not None]
            names = archive.namelist()
            require(len(names) == len(set(names)) and len(members) == len(set(members)) and set(names) == set(members), "Quality source ZIP member closure changed")
            for source in binding["selected"]:
                symbol = source["symbol"]
                summary = binding["symbolSummaries"][symbol]
                require(source["sourceReadStatus"] == "available" and summary["sourceReadStatus"] == "available", "Required source unavailable: " + symbol)
                require(summary["prefixIdentityUsable"] is True and summary["prefixIdentityIssues"] == [], "Required source prefix unsafe: " + symbol)
                raw = archive.read(_member(source))
                require(source["sha256"] == source["expectedSha256"] == sha(raw) and
                        type(source["byteLength"]) is int and source["byteLength"] == len(raw), "Full source member SHA/size differs: " + symbol)
                prefix = date_prefix(raw, document["cutoff"])
                for actual, expected in (("prefixBytes", "prefixThroughEndBytes"), ("prefixRecords", "prefixThroughEndRecords"),
                                         ("prefixSha256", "prefixThroughEndSha256"), ("rawRecordCount", "rawRecordCount"),
                                         ("trailingBytes", "trailingBytes"), ("invalidDateRecordIndices", "invalidDateRecordIndices")):
                    require(same(prefix[actual], summary[expected]), "Independent prefix/structure differs from audit: " + symbol + ":" + actual)
                expected_transitions = [{key: value for key, value in item.items() if key != "withinInterval"} for item in summary["dateOrderTransitions"]]
                require(same(prefix["dateOrderTransitions"], expected_transitions), "Whole-source date-order diagnostic differs: " + symbol)
                relative = "tdx/vipdoc/" + symbol[:2] + "/lday/" + symbol + ".day"
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(raw[:prefix["prefixBytes"]])
                require(file_sha(target) == prefix["prefixSha256"], "Materialized prefix write differs")
                source_files.append({"symbol": symbol, "group": source["group"], "sourceMember": source["member"],
                    "sourceFullSha256": source["sha256"], "sourceBytes": len(raw), "path": relative,
                    **{key: prefix[key] for key in ("prefixSha256", "prefixBytes", "prefixRecords", "firstDate", "lastDate")},
                    "sourceStructuralIssue": summary["summary"]["structureIssue"],
                    "sourceStructureReasons": summary["summary"]["structureReasons"]})
                if len(source_files) % 500 == 0:
                    print(json.dumps({"stage": "materializing", "sourceFiles": len(source_files)}), flush=True)
        database = _create_database(output)
        exclusions = [{"symbol": row["symbol"], "group": row["group"], "sourceMember": row["member"],
                       "sourceFullSha256": row["expectedSha256"], "reason": document["exclusionReason"]} for row in binding["excluded"]]
        manifest = {"schema": "supplemental-source-manifest-v1", "protocolHash": protocol["protocolHash"],
            "cutoff": document["cutoff"], "fixedSymbols": document["fixedPopulation"]["symbols"], "benchmarkSymbol": document["benchmarkSymbol"],
            "sourceFiles": source_files, "excludedSources": exclusions, "workspace": database,
            "prefixNumericQualityBefore2023Verified": False,
            "limitations": [*document["limitations"],
                "Only prefix byte/date identity is rechecked here. Numeric quality before 2023 is not verified; original earlier OHLC is retained without repair.",
                "Whole-source date anomalies outside a usable prefix remain recorded; prefix validity does not certify the complete original file.",
                "A later loader failure must be separately recorded without dropping or replacing a fixed symbol."], **FLAGS}
        save(output / "manifest.json", manifest)
        expected_files = EVIDENCE_FILES | {row["path"] for row in source_files} | {database["path"]}
        files = _inventory(output)
        require(set(files) == expected_files and len(source_files) == document["expectedMaterializedFiles"], "Unexpected or incomplete materialized output inventory")
        require(files["protocol.json"] == protocol_sha, "Protocol changed during materialization")
        require(implementation_hashes() == implementation, "Implementation changed during materialization")
        _check_archive(output, protocol)
        _input_check(binding)
        save(output / "completion.json", {"schema": "supplemental-source-completion-v1", "completedAt": now(),
            "protocolHash": protocol["protocolHash"], "files": files, **FLAGS})
        return read(output / "completion.json")
    except Exception as error:
        save(output / "failure.json", {"failedAt": now(), "errorType": type(error).__name__, "message": str(error), **FLAGS})
        raise


def verify_completed(output, binding=None):
    """Read-only local receipt check; it does not repeat independent raw replay."""
    output = Path(output).resolve()
    binding = binding or load_binding()
    protocol, manifest, receipt = (read(output / name) for name in ("protocol.json", "manifest.json", "completion.json"))
    require(not (output / "failure.json").exists(), "Failed source is immutable")
    require(protocol.get("schema") == "supplemental-source-protocol-v1" and
            sha(encoded({key: value for key, value in protocol.items() if key != "protocolHash"})) == protocol.get("protocolHash"), "Source protocol changed")
    require(protocol["registrationSha256"] == REGISTRATION_SHA and same(protocol["registration"], binding["document"]) and
            same(protocol["inputFilesSha256"], binding["files"]) and protocol["implementation"] == implementation_hashes(), "Source binding changed")
    require(receipt.get("schema") == "supplemental-source-completion-v1" and manifest.get("schema") == "supplemental-source-manifest-v1" and
            receipt["protocolHash"] == manifest["protocolHash"] == protocol["protocolHash"], "Source receipt identity differs")
    require(all(obj.get(key) is value for obj in (protocol, manifest, receipt) for key, value in FLAGS.items()), "Source scope flags changed")
    expected_paths = {"tdx/vipdoc/" + row["symbol"][:2] + "/lday/" + row["symbol"] + ".day" for row in binding["selected"]}
    require(set(receipt["files"]) == expected_paths | EVIDENCE_FILES | {"workspace/market.sqlite3"}, "Source receipt file closure differs")
    actual = _inventory(output)
    require(set(actual) - {"completion.json", "independent-verification.json"} == set(receipt["files"]), "Unexpected output file")
    require(all(actual[name] == expected for name, expected in receipt["files"].items()), "Completed output bytes changed")
    require(same(_database_info(output), manifest["workspace"]), "Minimal workspace changed")
    _check_archive(output, protocol)
    _input_check(binding)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = materialize(args.output)
    print(json.dumps({"completed": True, "protocolHash": receipt["protocolHash"], "output": str(args.output.resolve()),
                      "returnsCalculated": False, "featuresCalculated": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
