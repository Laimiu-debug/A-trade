#!/usr/bin/env python3
"""Registered adapter for the frozen raw leadership builder on isolated prefixes.

Feature extraction, ranking, persistence, as-of eligibility and receipt writing
remain the original private builder functions. This adapter changes only input
registration, immutable source bindings, the saved calendar and code closure.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-leadership-panel-v1.json"
REGISTRATION_SHA256 = "8b0f07ecd4e9b49e95f9abdecbcbd9a33af14d51dccd06395ddebf5e7796a558"
SOURCE_WORKSPACE = ROOT / "artifacts/strategy-search/supplemental-source-2024-2025-v1/workspace"

_spec = importlib.util.spec_from_file_location("_supplemental_frozen_leadership_builder", ROOT / "scripts/build-signal-leadership-panel.py")
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
base = runtime
source_tools = runtime.source_tools
_original_validate_sample = runtime.validate_sample
_original_source_file_hashes = runtime.source_file_hashes
_original_execution_hashes = runtime.execution_hashes


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


LOADED_SOURCE_SHA256 = file_sha(__file__)
LOADED_BUILDER_SHA256 = file_sha(runtime.__file__)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON value: " + value)


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_pairs, parse_constant=_nonfinite)


def _relative(name):
    value = PurePosixPath(name)
    require(not value.is_absolute() and ".." not in value.parts and "\\" not in name and ":" not in name, "Unsafe relative binding path")
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()), "Bound path escapes repository")
    return path


def _file_pins(pins):
    for name, expected in pins.items():
        require(type(expected) is str and len(expected) == 64 and file_sha(_relative(name)) == expected, "Frozen dependency changed: " + name)


def _false_flags(value):
    return all(value.get(key) is False for key in ("returnsCalculated", "featuresCalculated", "certified", "targetAchieved"))


def registration():
    require(type(REGISTRATION_SHA256) is str and len(REGISTRATION_SHA256) == 64, "Supplemental feature registration is pending")
    require(file_sha(REGISTRATION) == REGISTRATION_SHA256, "Supplemental feature registration changed")
    document = read(REGISTRATION)
    require(document.get("featureVersion") == runtime.FEATURE_VERSION, "Frozen feature version differs")
    for key in ("frozenBuilder", "frozenFeatureRegistration", "frozenIndependentVerifier"):
        item = document[key]
        require(file_sha(_relative(item["path"])) == item["sha256"], "Frozen method reference changed: " + key)
    require(_relative(document["frozenBuilder"]["path"]) == Path(runtime.__file__).resolve() and
            document["frozenBuilder"]["sha256"] == LOADED_BUILDER_SHA256, "Loaded builder differs from frozen reference")
    previous = read(_relative(document["frozenFeatureRegistration"]["path"]))
    require(previous["featureVersion"] == document["featureVersion"], "Feature registration version changed")
    require(file_sha(_relative(document["designReference"])) == document["designSha256"], "Feature design reference changed")
    require(type(document.get("frozenFeatureImplementation")) is dict and bool(document["frozenFeatureImplementation"]), "Frozen feature implementation closure is missing")
    _file_pins(document["frozenFeatureImplementation"])
    require(document["frozenFeatureImplementation"].get(document["frozenBuilder"]["path"]) == document["frozenBuilder"]["sha256"] and
            document["frozenFeatureImplementation"].get(document["frozenFeatureRegistration"]["path"]) == document["frozenFeatureRegistration"]["sha256"], "Frozen feature closure and reference pins disagree")
    fixed, sample = document["fixedPopulation"], document["sample"]
    require(fixed["symbols"] == sorted(set(fixed["symbols"])) and len(fixed["symbols"]) == fixed["count"] == sample["expectedFullSecurities"] and
            digest(fixed["symbols"]) == fixed["sha256"], "Fixed sample identity/count changed")
    expected_selection = {**previous["sample"]["selection"], "mode": "explicit", "symbols": fixed["symbols"]}
    require(sample["selection"] == expected_selection, "Source selection must explicitly retain the unchanged fixed population")
    return document


def saved_calendar(document):
    spec, sample = document["calendar"], document["sample"]
    path = _relative(spec["path"])
    require(file_sha(path) == spec["sha256"], "Supplemental saved calendar changed")
    calendar = read(path)
    require(calendar.get("schema") == spec.get("schema") == "exchange-notice-derived-calendar-v1", "Expected the registered derived exchange calendar")
    require(calendar["start"] == spec["start"] == sample["warmupStart"] and
            calendar["end"] == spec["end"] == sample["signalEnd"], "Derived calendar bounds differ")
    days = calendar["dates"]
    require(type(days) is list and days == sorted(set(days)), "Derived calendar is duplicated or unordered")
    for day in days:
        require(type(day) is str and date.fromisoformat(day).isoformat() == day and calendar["start"] <= day <= calendar["end"], "Calendar date is noncanonical or outside bounds")
    expected = (sample["expectedCalendarSessions"], sample["expectedWarmupSessions"], sample["expectedScoredSessions"])
    actual = (len(days), sum(day < sample["signalStart"] for day in days),
              sum(sample["signalStart"] <= day <= sample["signalEnd"] for day in days))
    require(actual == expected and actual[0] == actual[1] + actual[2], "Derived calendar session counts differ")
    years = {year: sum(day.startswith(year) for day in days) for year in sorted({day[:4] for day in days})}
    require(encoded(calendar["sessionsByYear"]) == encoded(years), "Derived calendar annual session counts differ")
    # Calendar source certification remains the registered notice transcription;
    # this validates its arithmetic, not the original external notices.
    if "closureRanges" in calendar:
        ranges = []
        for year, intervals in calendar["closureRanges"].items():
            for left, right in intervals:
                require(date.fromisoformat(left).isoformat() == left and date.fromisoformat(right).isoformat() == right and
                        left <= right and left[:4] == right[:4] == year, "Invalid calendar closure range")
                ranges.append((left, right))
        first, last = date.fromisoformat(calendar["start"]), date.fromisoformat(calendar["end"])
        rebuilt = []
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            if day.weekday() < 5 and not any(left <= day.isoformat() <= right for left, right in ranges):
                rebuilt.append(day.isoformat())
        require(days == rebuilt, "Calendar dates disagree with archived weekday/closure arithmetic")
    return days


def _archive_matches(path, protocol):
    require(file_sha(path) == protocol["executionArchiveSha256"], "Source whole execution archive changed")
    pins = {**protocol["implementation"], **protocol["metadata"]}
    require(not set(protocol["implementation"]).intersection(protocol["metadata"]), "Source archive closure overlaps")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == set(pins), "Source archive member closure differs")
        for name, expected in pins.items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected, "Archived source dependency changed")
    _file_pins(pins)


def load_source_binding(document=None):
    document = document or registration()
    spec = document["sourceMaterialization"]
    directory = _relative(spec["directory"])
    require((directory / "workspace").resolve() == SOURCE_WORKSPACE.resolve(), "Unexpected isolated source workspace")
    pins = spec["files"]
    for name, expected in pins.items():
        path = directory / name
        require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts and "\\" not in name and ":" not in name,
                "Unsafe source artifact member")
        require(path.resolve().is_relative_to(directory) and file_sha(path) == expected, "Materialized source file changed: " + name)
    actual = set()
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "Materialized source contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    require(actual == set(pins), "Materialized source has extra or omitted files")
    protocol, manifest, completion, audit = (read(directory / name) for name in
        ("protocol.json", "manifest.json", "completion.json", "independent-verification.json"))
    require(protocol.get("schema") == "supplemental-source-protocol-v1" and
            digest({key: value for key, value in protocol.items() if key != "protocolHash"}) == protocol.get("protocolHash") == spec["protocolHash"] and
            protocol["registrationSha256"] == spec["registrationSha256"], "Materialized source protocol identity changed")
    require(manifest.get("schema") == "supplemental-source-manifest-v1" and completion.get("schema") == "supplemental-source-completion-v1" and
            manifest["protocolHash"] == completion["protocolHash"] == protocol["protocolHash"], "Source manifest/completion identity changed")
    require(all(_false_flags(value) for value in (protocol, manifest, completion, audit)), "Source scope flags changed")
    require(set(pins) == set(completion["files"]) | {"completion.json", "independent-verification.json"} and
            all(pins[name] == expected for name, expected in completion["files"].items()), "Source completed-file closure differs")
    require(audit.get("passed") is True and type(audit.get("failureCount")) is int and audit["failureCount"] == 0 and
            audit.get("protocolHash") == protocol["protocolHash"] and audit.get("verifierSha256") == spec["independentVerifierSha256"], "Independent source verification is not accepted")
    require(audit.get("completionSha256") == pins["completion.json"] and
            encoded(audit.get("implementation")) == encoded(protocol["implementation"]), "Source audit completion or implementation binding differs")
    require(encoded(audit.get("resultFileHashes")) == encoded(completion["files"]) and
            encoded(audit.get("sourceHashes")) == encoded(protocol["inputFilesSha256"]), "Source audit input/output closure differs")
    require(protocol["implementation"].get("scripts/verify-signal-supplemental-source.py") == spec["independentVerifierSha256"], "Source verifier not bound in source execution closure")
    require(manifest.get("prefixNumericQualityBefore2023Verified") is False, "Earlier raw prices cannot be newly certified")
    fixed, sample = document["fixedPopulation"], document["sample"]
    require(manifest["fixedSymbols"] == fixed["symbols"] == protocol["registration"]["fixedPopulation"]["symbols"] and
            manifest["benchmarkSymbol"] == sample["selection"]["benchmarkSymbol"] and manifest["cutoff"] == sample["signalEnd"], "Materialized sample or cutoff differs")
    rows = manifest["sourceFiles"]
    symbols = sorted([*fixed["symbols"], manifest["benchmarkSymbol"]])
    require([row["symbol"] for row in rows] == symbols, "Materialized source dropped, replaced or duplicated a symbol")
    expected_days = set()
    for row in rows:
        symbol = row["symbol"]
        path = "tdx/vipdoc/" + symbol[:2] + "/lday/" + symbol + ".day"
        require(row["path"] == path and pins.get(path) == row["prefixSha256"] and
                type(row["prefixRecords"]) is int and row["prefixRecords"] > 0 and row["prefixBytes"] == row["prefixRecords"] * 32 and
                row["firstDate"] <= row["lastDate"] <= sample["signalEnd"], "Invalid pinned raw prefix identity")
        expected_days.add(path)
    expected_receipt = expected_days | {"workspace/market.sqlite3", "protocol.json", "attempt.json", "manifest.json", "execution-sources.zip"}
    require(set(completion["files"]) == expected_receipt, "Source receipt includes unexpected source/caches/catalogues")
    require(manifest["workspace"]["path"] == "workspace/market.sqlite3" and
            manifest["workspace"]["sha256"] == pins["workspace/market.sqlite3"] and
            manifest["workspace"]["tdxPath"] == str((directory / "tdx").resolve()), "Source workspace points outside isolated prefixes")
    _file_pins(protocol["inputFilesSha256"])
    _archive_matches(directory / "execution-sources.zip", protocol)
    return {"directory": directory, "protocol": protocol, "manifest": manifest,
            "completion": completion, "audit": audit, "files": pins,
            "identity": {"protocolHash": spec["protocolHash"], "registrationSha256": spec["registrationSha256"],
                         "files": pins, "inputFilesSha256": protocol["inputFilesSha256"]}}


def validate_sample(dataset, document, calendar):
    _original_validate_sample(dataset, document, calendar)
    require(sorted(row["symbol"] for row in dataset["securities"]) == document["fixedPopulation"]["symbols"], "Loaded symbols differ from the exact frozen population")


def source_file_hashes(workspace, document):
    before = load_source_binding(document)
    require(Path(workspace).resolve() == (before["directory"] / "workspace").resolve(), "Wrong isolated workspace")
    physical = _original_source_file_hashes(workspace, document)
    expected = {str((before["directory"] / name).resolve()): expected for name, expected in before["files"].items()
                if name.startswith("tdx/") or name == "workspace/market.sqlite3"}
    require(physical == expected, "Loader source inventory differs from materialized prefixes/database")
    after = load_source_binding(document)
    require(encoded(before["identity"]) == encoded(after["identity"]), "Complete source binding changed during source enumeration")
    return physical


def execution_hashes(document):
    require(file_sha(__file__) == LOADED_SOURCE_SHA256 and file_sha(runtime.__file__) == LOADED_BUILDER_SHA256, "Loaded adapter/builder code changed")
    _file_pins(document["frozenFeatureImplementation"])
    binding = load_source_binding(document)
    result = _original_execution_hashes(document)
    paths = {Path(__file__).resolve(), Path(REGISTRATION).resolve(),
             _relative(document["frozenFeatureRegistration"]["path"]), _relative(document["frozenIndependentVerifier"]["path"]),
             ROOT / "scripts/verify-signal-supplemental-leadership-panel.py",
             ROOT / "tests/test_signal_supplemental_leadership_panel.py"}
    for name in set(document["frozenFeatureImplementation"]) | set(binding["protocol"]["implementation"]) | set(binding["protocol"]["metadata"]):
        paths.add(_relative(name))
    for path in paths:
        result[path.relative_to(ROOT).as_posix()] = file_sha(path)
    return dict(sorted(result.items()))


def _configure():
    runtime.REGISTRATION = REGISTRATION
    runtime.REGISTRATION_SHA256 = REGISTRATION_SHA256
    runtime.SOURCE_WORKSPACE = SOURCE_WORKSPACE
    runtime.registration = registration
    runtime.saved_calendar = saved_calendar
    runtime.validate_sample = validate_sample
    runtime.source_file_hashes = source_file_hashes
    runtime.execution_hashes = execution_hashes


def execute(args):
    document = registration()
    binding = load_source_binding(document)
    output, workspace = Path(args.output).resolve(), Path(args.workspace).resolve()
    require(workspace == SOURCE_WORKSPACE.resolve(), "Use only the registered supplemental workspace")
    source = binding["directory"]
    require(not output.is_relative_to(source) and not source.is_relative_to(output), "Output overlaps the full materialized source directory")
    require(not any(_relative(name).is_relative_to(output) for name in binding["protocol"]["inputFilesSha256"]), "Output contains a frozen upstream input")
    _configure()
    return runtime.execute(args)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return execute(args)
    except (ValueError, OSError, KeyError, sqlite3.Error) as error:
        print("Supplemental feature panel stopped: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
