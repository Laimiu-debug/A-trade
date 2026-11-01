#!/usr/bin/env python3
"""Independently replay a frozen TDX interval-quality audit; no strategy inputs.

Only the standard library is used. Numeric fields are decoded solely for valid
record dates inside the registered audit interval. Full-file dates and bytes
are inspected for provenance, ordering and prefix identity, not for returns.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import struct
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/signal-supplemental-interval-quality-v1.json"
REGISTRATION_SHA = "7a60c771afb2338596564f43b9f28e2c119ce0f20206b33632af2459d7d9b2e8"
STATES = ("validPositiveActivity", "validZeroVolumeOrAmount", "invalidRecord", "duplicateDate", "missingRecord")
GROUPS = ("fixed", "otherPinned", "benchmark")
OUTPUTS = {"symbols.jsonl.gz", "symbol-summary.json", "daily-coverage.json", "summary.json"}
IMPLEMENTATION = {"scripts/audit-signal-interval-quality.py", "service/signal_interval_quality.py",
                  "tests/test_signal_interval_quality.py", "scripts/verify-signal-interval-quality.py",
                  "tests/test_signal_interval_quality_runner.py"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


VERIFIER_SOURCE_SHA256 = file_sha(__file__)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON value: " + value)


def loads(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)


def read(path):
    return loads(Path(path).read_bytes())


def _day(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Noncanonical ISO date")
    return value


def _calendar(calendar, start, end):
    _day(start)
    _day(end)
    if start > end or type(calendar) is not list:
        raise ValueError("Invalid audit interval/calendar")
    for day in calendar:
        _day(day)
    if calendar != sorted(set(calendar)) or any(not start <= d <= end for d in calendar):
        raise ValueError("Calendar is duplicated, unordered or outside the interval")


def _counts(values):
    counts = Counter(values)
    return {state: counts[state] for state in STATES}


def reference_scan(raw, calendar, *, start, end):
    """Independent complete reference output for synthetic tests and audit replay."""
    if type(raw) is not bytes:
        raise TypeError("Expected immutable raw bytes")
    _calendar(calendar, start, end)
    length, remainder = divmod(len(raw), 32)
    dated, invalid = [], []
    # Decode only the unsigned date word outside the permitted numeric window.
    for index in range(length):
        word = struct.unpack_from("<I", raw, index * 32)[0]
        try:
            day = date(word // 10000, word // 100 % 100, word % 100).isoformat()
        except ValueError:
            invalid.append(index)
        else:
            dated.append((index, day))
    transitions = []
    for (left_index, left), (right_index, right) in zip(dated, dated[1:]):
        if right <= left:
            transitions.append({"previousRecordIndex": left_index, "recordIndex": right_index,
                                "previousDate": left, "date": right,
                                "withinInterval": start <= left <= end and start <= right <= end})
    unordered_days = {item[key] for item in transitions if item["withinInterval"] for key in ("date", "previousDate")}
    date_counts = Counter(day for _, day in dated)
    duplicates = {day: date_counts[day] for day in sorted(date_counts) if date_counts[day] > 1}
    prefix_records = max((index + 1 for index, day in dated if day <= end), default=0)
    prefix_bytes = prefix_records * 32
    prefix_issues = []
    if any(index < prefix_records for index in invalid):
        prefix_issues.append("invalidDateInPrefix")
    if any(index < prefix_records and day > end for index, day in dated):
        prefix_issues.append("dateAfterEndInPrefix")
    if any(item["recordIndex"] < prefix_records for item in transitions):
        prefix_issues.append("nonIncreasingDateInPrefix")
    if remainder:
        prefix_issues.append("incompleteRawTailHasUnknownDate")

    expected = set(calendar)
    by_day, off_calendar = defaultdict(list), []
    interval_dates = []
    invalid_flags = {"nonPositiveOhlc", "inconsistentOhlc", "negativeAmount", "nonfiniteAmount", "negativeOrNonintegerVolume"}
    for index, day in dated:
        if day < start or day > end:
            continue
        interval_dates.append(day)
        opening, high, low, close, amount, volume = struct.unpack_from("<IIIIfI", raw, index * 32 + 4)
        flags = []
        if 0 in (opening, high, low, close):
            flags.append("nonPositiveOhlc")
        if low > opening or low > close or opening > high or close > high:
            flags.append("inconsistentOhlc")
        if math.isnan(amount) or math.isinf(amount):
            flags.append("nonfiniteAmount")
        elif amount < 0:
            flags.append("negativeAmount")
        # Volume is an unsigned 32-bit word, so negative/noninteger is impossible.
        if volume == 0:
            flags.append("zeroVolume")
        if amount == 0:
            flags.append("zeroAmount")
        item = {"recordIndex": index, "date": day, "flags": flags}
        if day in expected:
            by_day[day].append(item)
        else:
            off_calendar.append(item)
    records = []
    for day in calendar:
        items = by_day.get(day, [])
        flags = set().union(*(set(item["flags"]) for item in items))
        if len(items) >= 2:
            state = "duplicateDate"
            flags.add("duplicateDate")
        elif not items:
            state = "missingRecord"
        elif invalid_flags.intersection(flags):
            state = "invalidRecord"
        elif "zeroVolume" in flags or "zeroAmount" in flags:
            state = "validZeroVolumeOrAmount"
        else:
            state = "validPositiveActivity"
        if day in unordered_days:
            flags.add("nonIncreasingDateOrder")
        records.append({"date": day, "state": state, "flags": sorted(flags)})
    counts = _counts(row["state"] for row in records)
    observed = sorted(set(interval_dates))
    observed_calendar = sorted(by_day)
    duplicate_calendar = [day for day, items in by_day.items() if len(items) > 1]
    off_dates = sorted({item["date"] for item in off_calendar})
    structure_reasons = [name for present, name in (
        (invalid, "invalidDateRecords"), (remainder, "trailingBytes"),
        (transitions, "nonIncreasingDateTransitions"), (duplicates, "duplicateValidDates"),
        (off_dates, "offCalendarDates")) if present]
    return {"schema": "tdx-interval-quality-v1", "certified": False, "returnsCalculated": False, "strategyEvaluated": False,
        "recordSize": 32, "rawRecordCount": length, "validDateRecords": len(dated),
        "invalidDateRecords": len(invalid), "invalidDateRecordIndices": invalid, "trailingBytes": remainder,
        "nonIncreasingDateTransitions": len(transitions),
        "intervalNonIncreasingDateTransitions": sum(item["withinInterval"] for item in transitions),
        "dateOrderTransitions": transitions, "duplicateDateCounts": duplicates,
        "duplicateValidDateCount": len(duplicates), "duplicateValidDateRecords": sum(duplicates.values()),
        "duplicateExcessRecordCount": sum(count - 1 for count in duplicates.values()),
        "prefixThroughEndSha256": sha(raw[:prefix_bytes]), "prefixThroughEndBytes": prefix_bytes,
        "prefixThroughEndRecords": prefix_records, "prefixIdentityUsable": not prefix_issues,
        "prefixIdentityIssues": prefix_issues,
        "prefixIdentityMeaning": "Original bytes ending at the last valid date <= end, or empty. Identity-only, not a strategy input or certificate of interval completeness; invalid dates outside this prefix remain in the full diagnostic. Any incomplete raw tail makes this identity unusable.",
        "offCalendarDates": off_dates, "offCalendarRecords": off_calendar,
        "interval": {"start": start, "end": end, "firstObservedDate": observed[0] if observed else None,
            "lastObservedDate": observed[-1] if observed else None,
            "firstObservedCalendarDate": observed_calendar[0] if observed_calendar else None,
            "lastObservedCalendarDate": observed_calendar[-1] if observed_calendar else None,
            "observedCalendarDateCount": len(observed_calendar), "recordCount": len(interval_dates),
            "offCalendarRecordCount": len(off_calendar), "outsideIntervalDateRecords": len(dated) - len(interval_dates),
            "duplicateCalendarDateCount": len(duplicate_calendar),
            "duplicateCalendarRecordCount": sum(len(by_day[day]) for day in duplicate_calendar),
            "duplicateExcessRecordCount": sum(len(by_day[day]) - 1 for day in duplicate_calendar)},
        "counts": counts,
        "summary": {"calendarDates": len(calendar), "counts": dict(counts),
            "structureIssue": bool(structure_reasons), "structureReasons": structure_reasons,
            "structureCounts": {"invalidDateRecords": len(invalid), "trailingBytes": remainder,
                "nonIncreasingDateTransitions": len(transitions), "duplicateCalendarDates": len(duplicate_calendar),
                "duplicateValidDates": len(duplicates), "offCalendarDates": len(off_dates), "offCalendarRecords": len(off_calendar)},
            "contentStateMeaning": "Content-only classification; not historical eligibility, repaired source order, execution or certification.",
            "calendarAccountingComplete": sum(counts.values()) == len(calendar)},
        "byYear": {year: _counts(row["state"] for row in records if row["date"][:4] == year) for year in sorted({d[:4] for d in calendar})},
        "records": records}


class Audit:
    def __init__(self):
        self.checks, self.failures, self.messages = 0, 0, []

    def check(self, condition, message):
        self.checks += 1
        if not condition:
            self.failures += 1
            if len(self.messages) < 100:
                self.messages.append(message)
        return bool(condition)

    def equal(self, actual, expected, message):
        return self.check(encoded(actual) == encoded(expected), message)

    def require(self, condition, message):
        if not self.check(condition, message):
            raise ValueError(message)


def _relative(root, name):
    value = PurePosixPath(name)
    if value.is_absolute() or ".." in value.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe relative archive path")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Path escapes the repository root")
    return result


def _symbol(path):
    # Physical paths are provenance strings and may be Windows paths on any host.
    parts = str(path).replace("\\", "/").split("/")
    if len(parts) < 3:
        raise ValueError("Invalid physical source identity")
    market, filename = parts[-3].lower(), parts[-1]
    stem = filename[:-4].lower() if filename.lower().endswith(".day") else ""
    symbol = market + stem if len(stem) == 6 and stem.isdigit() else stem
    if market not in ("sh", "sz") or len(symbol) != 8 or symbol[:2] != market or not symbol[2:].isdigit():
        raise ValueError("Invalid physical source symbol")
    return symbol, filename


def _verify(output, root, audit, report):
    protocol = read(output / "protocol.json")
    receipt = read(output / "completion.json")
    report["protocolHash"] = protocol.get("protocolHash")
    audit.require(sha(encoded({k: v for k, v in protocol.items() if k != "protocolHash"})) == protocol.get("protocolHash"), "Protocol canonical hash")
    audit.require(protocol.get("schema") == "supplemental-interval-quality-protocol-v1", "Protocol schema")
    audit.require(protocol.get("registrationSha256") == REGISTRATION_SHA, "Frozen registration SHA")
    for item in (protocol, receipt):
        for flag in ("returnsCalculated", "certified", "targetAchieved"):
            audit.require(item.get(flag) is False, "False-only flag: " + flag)
    audit.require(protocol.get("archiveContainsPostCutoffBytes") is True and protocol.get("numericAuditBoundedByRegistration") is True, "Archive/numeric scope flags")
    audit.require(receipt.get("schema") == "supplemental-interval-quality-completion-v1" and receipt.get("protocolHash") == protocol["protocolHash"], "Completion identity")
    audit.require(set(receipt.get("files", {})) == OUTPUTS, "Complete output receipt set")
    source_hashes = {"protocol.json": file_sha(output / "protocol.json"), "completion.json": file_sha(output / "completion.json")}
    for name, field, expected in (("freeze-attempt.json", "registrationSha256", REGISTRATION_SHA),
                                   ("audit-attempt.json", "protocolHash", protocol["protocolHash"])):
        audit.require(read(output / name).get(field) == expected, "Attempt identity: " + name)
        source_hashes[name] = file_sha(output / name)
    audit.require(not any((output / name).exists() for name in ("freeze-failure.json", "audit-failure.json")), "Successful receipt has no failed attempt marker")
    for name in sorted(OUTPUTS):
        source_hashes[name] = file_sha(output / name)
        audit.require(source_hashes[name] == receipt["files"][name], "Output SHA: " + name)
    report["sourceHashes"] = source_hashes
    # Each archive file is read once; members are decompressed once from memory.
    execution_raw = (output / "execution-sources.zip").read_bytes()
    input_raw = (output / "input-sources.zip").read_bytes()
    for name, raw, key in (("execution-sources.zip", execution_raw, "executionArchiveSha256"),
                           ("input-sources.zip", input_raw, "inputArchiveSha256")):
        source_hashes[name] = sha(raw)
        audit.require(source_hashes[name] == protocol[key], "Whole archive SHA: " + name)
    implementation, metadata = protocol["implementation"], protocol["metadata"]
    audit.require(set(implementation) == IMPLEMENTATION and not set(implementation).intersection(metadata), "Exact executable closure")
    archived = {}
    with zipfile.ZipFile(io.BytesIO(execution_raw)) as archive:
        names = archive.namelist()
        pins = {**implementation, **metadata}
        audit.require(len(names) == len(set(names)) and set(names) == set(pins), "Execution ZIP member closure")
        for name in names:
            raw = archive.read(name)
            archived[name] = raw
            audit.require(sha(raw) == pins[name], "Archived execution/metadata SHA: " + name)
            audit.require(file_sha(_relative(root, name)) == pins[name], "Current execution/metadata SHA: " + name)
    registration_path = Path(REGISTRATION)
    if not registration_path.is_absolute():
        registration_path = root / registration_path
    registration_name = registration_path.resolve().relative_to(root.resolve()).as_posix()
    audit.require(metadata.get(registration_name) == REGISTRATION_SHA, "Registration included in metadata closure")
    document = loads(archived[registration_name])
    audit.equal(document, protocol["registration"], "Protocol embeds exact registered document")
    audit.require(document.get("schema") == "supplemental-interval-quality-registration-v1", "Registration schema")
    audit.equal(document.get("states"), list(STATES), "Registered five states")
    for flag in ("returnsCalculated", "certified", "targetAchieved"):
        audit.require(document.get(flag) is False, "Registration false-only flag: " + flag)
    expected_metadata = {registration_name: REGISTRATION_SHA,
        document["calendar"]["path"]: document["calendar"]["sha256"],
        document["sourceManifest"]["path"]: document["sourceManifest"]["sha256"],
        document["frozenCandidate"]["path"]: document["frozenCandidate"]["sha256"],
        "docs/strategy-backtesting.md": document["exposure"]["documentSha256"]}
    audit.require(encoded(metadata) == encoded(expected_metadata), "Exact metadata closure")
    calendar = loads(archived[document["calendar"]["path"]])
    manifest = loads(archived[document["sourceManifest"]["path"]])
    audit.equal(calendar, protocol["calendar"], "Protocol embeds exact calendar")
    start, end, dates = document["auditStart"], document["auditEnd"], calendar["dates"]
    _calendar(dates, start, end)
    audit.require(calendar["start"] == start and calendar["end"] == end, "Calendar interval")
    years = sorted({d[:4] for d in dates})
    sessions = dict(Counter(day[:4] for day in dates))
    audit.equal(calendar["sessionsByYear"], sessions, "Calendar year counts")
    if "closureRanges" in calendar:
        closures = []
        for year, ranges in calendar["closureRanges"].items():
            for left, right in ranges:
                _day(left)
                _day(right)
                audit.require(left <= right and left[:4] == right[:4] == year, "Calendar closure interval")
                closures.append((left, right))
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        rebuilt = [(first + timedelta(days=offset)).isoformat() for offset in range((last-first).days + 1)
                   if (first + timedelta(days=offset)).weekday() < 5 and not any(
                       left <= (first + timedelta(days=offset)).isoformat() <= right for left, right in closures)]
        audit.equal(dates, rebuilt, "Weekdays minus archived closure ranges")
    fixed = document["fixedPopulation"]["symbols"]
    audit.require(fixed == sorted(set(fixed)) and len(fixed) == document["fixedPopulation"]["count"], "Fixed population uniqueness/count")
    audit.equal(fixed, manifest["inputs"]["symbols"], "Original fixed population retained")
    audit.require(sha(encoded(fixed)) == document["fixedPopulation"]["sha256"], "Fixed symbol SHA")
    benchmark = manifest["inputs"]["benchmark"]["symbol"]
    expected_sources = []
    for physical, expected_hash in sorted(manifest["physicalSourceHashes"].items()):
        if not physical.lower().endswith(".day"):
            continue
        symbol, filename = _symbol(physical)
        group = "benchmark" if symbol == benchmark else "fixed" if symbol in set(fixed) else "otherPinned"
        if group != "benchmark":
            audit.require(symbol.startswith(("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")), "Pinned main-board identity")
        expected_sources.append((symbol, group, physical, expected_hash, "raw/" + symbol[:2] + "/" + filename))
    sources = protocol["sources"]
    audit.require(len(sources) == len(expected_sources), "Physical source population size")
    symbols = [item[0] for item in expected_sources]
    audit.require(len(symbols) == len(set(symbols)) and set(fixed) <= set(symbols) and symbols.count(benchmark) == 1, "Fixed/other/benchmark identity partition")
    groups = dict(Counter(item[1] for item in expected_sources))
    audit.equal(protocol["sourceGroupCounts"], groups, "Source group counts")
    report.update(sourceGroupCounts=groups, sessionsByYear=sessions, auditStart=start, auditEnd=end)
    daily = {group: {day: dict.fromkeys(STATES, 0) for day in dates} for group in GROUPS}
    by_year = {group: {year: dict.fromkeys(STATES, 0) for year in years} for group in GROUPS}
    compact_rows, unavailable, benchmark_audit = [], [], None
    with zipfile.ZipFile(io.BytesIO(input_raw)) as archive, gzip.open(output / "symbols.jsonl.gz", "rt", encoding="utf-8") as stream:
        expected_members = [item["member"] for item in sources if item.get("member") is not None]
        names = archive.namelist()
        audit.require(len(names) == len(set(names)) and len(expected_members) == len(set(expected_members)) and set(names) == set(expected_members), "Input ZIP exact member closure")
        for source, (symbol, group, physical, expected_hash, member) in zip(sources, expected_sources):
            audit.require(source.get("symbol") == symbol and source.get("group") == group and source.get("sourcePath") == physical and source.get("expectedSha256") == expected_hash, "Source row identity: " + symbol)
            status = source.get("sourceReadStatus")
            audit.require(status in ("available", "unavailable"), "Source availability enum: " + symbol)
            if status == "available":
                audit.require(source.get("member") == member and source.get("sha256") == expected_hash, "Available source binding: " + symbol)
                raw = archive.read(member)
                audit.require(type(source.get("byteLength")) is int and len(raw) == source["byteLength"] and sha(raw) == expected_hash, "Source bytes/size/SHA: " + symbol)
            else:
                audit.require(source.get("member") is None and "sha256" not in source and "byteLength" not in source and source.get("errorType") == "FileNotFoundError", "Unavailable source remains explicit: " + symbol)
                raw = b""
                unavailable.append(symbol)
            expected_audit = reference_scan(raw, dates, start=start, end=end)
            if group == "benchmark":
                benchmark_audit = expected_audit
            line = stream.readline()
            audit.require(bool(line), "Symbol journal prematurely ended: " + symbol)
            actual = loads(line)
            expected_row = {"symbol": symbol, "group": group, "sourceReadStatus": status,
                            "sourceSha256": source.get("sha256"), "audit": expected_audit}
            audit.equal(actual, expected_row, "Complete raw reference audit: " + symbol)
            compact_rows.append({"symbol": symbol, "group": group, "sourceReadStatus": status,
                                 **{key: value for key, value in expected_audit.items() if key != "records"}})
            for row in expected_audit["records"]:
                daily[group][row["date"]][row["state"]] += 1
                by_year[group][row["date"][:4]][row["state"]] += 1
        audit.require(stream.readline() == "", "Symbol journal trailing rows/EOF")
    audit.equal(read(output / "symbol-summary.json"), compact_rows, "Full symbol summary replay")
    audit.equal(read(output / "daily-coverage.json"), daily, "All group/day/state aggregates")
    benchmark_complete = groups.get("benchmark") == 1 and all(
        daily["benchmark"][day][STATES[0]] + daily["benchmark"][day][STATES[1]] == 1 for day in dates)
    summary_expected = {"schema": "supplemental-interval-quality-summary-v1", "protocolHash": protocol["protocolHash"],
        "auditStart": start, "auditEnd": end, "sessionsByYear": sessions, "sourceGroupCounts": groups,
        "byGroupYear": by_year, "unavailableSources": unavailable,
        "benchmarkCalendarCompleteValidContent": benchmark_complete,
        "benchmarkCalendarExactlyObserved": benchmark_complete and not benchmark_audit["offCalendarDates"] and
            benchmark_audit["invalidDateRecords"] == 0 and benchmark_audit["trailingBytes"] == 0,
        "fixedPopulationUnchanged": True, "missingnessChangesEligibility": False,
        "exposure": document["exposure"], "certificationGaps": document["certificationGaps"],
        "returnsCalculated": False, "strategyRanksCalculated": False, "candidateGatesCalculated": False,
        "certified": False, "targetAchieved": False,
        "scope": "Frozen physical files only. Content validity and presence are not PIT membership, tradability or economic-return evidence."}
    audit.equal(read(output / "summary.json"), summary_expected, "Complete summary including group/year aggregates")
    for group in GROUPS:
        audit.check(all(sum(counts.values()) == groups.get(group, 0) for counts in daily[group].values()), "Mutually exclusive daily population: " + group)
        audit.check(all(sum(by_year[group][year].values()) == groups.get(group, 0) * sessions[year] for year in years), "Mutually exclusive annual population: " + group)
    for name, expected in source_hashes.items():
        # The two immutable archives were already read once in full. Detect
        # subsequent replacement via descriptor metadata below, without reread.
        if name not in ("input-sources.zip", "execution-sources.zip"):
            audit.check(file_sha(output / name) == expected, "Output unchanged during verification: " + name)
    for name, expected in {**implementation, **metadata}.items():
        audit.check(file_sha(_relative(root, name)) == expected, "Execution/metadata unchanged during verification: " + name)
    report["symbolDateCellsVerified"] = len(sources) * len(dates)
    report["coreFieldsNotIndependentlyRecomputed"] = []


def verify(output, root=ROOT):
    """Return a report without writing it. Integrity failures never become passes."""
    output, root = Path(output).resolve(), Path(root).resolve()
    audit = Audit()
    report = {"schema": "supplemental-interval-quality-independent-verification-v1",
        "verifiedAt": datetime.now(timezone.utc).isoformat(), "verifierSha256": VERIFIER_SOURCE_SHA256,
        "returnsCalculated": False, "strategyRanksCalculated": False, "candidateGatesCalculated": False,
        "certified": False, "targetAchieved": False,
        "scope": "Every archived symbol/session classification, full structural and prefix diagnostics, and all symbol/day/year/group totals independently recomputed. Full physical bytes and record dates are inspected, but numeric fields outside the registered interval are not evaluated for content quality or returns. No strategy or economic-return validation.",
        "limitations": ["Calendar notice transcription is hash-bound and its weekday/closure arithmetic checked; the notices are not independently retrieved.",
            "Source-unavailable claims are frozen acquisition records, not a second attempt to open the original live file.",
            "Content presence/validity does not certify historical membership, tradability, corporate actions or execution."]}
    archive_stats = {}
    try:
        audit.require(file_sha(__file__) == VERIFIER_SOURCE_SHA256, "Verifier source matches imported implementation")
        for name in ("input-sources.zip", "execution-sources.zip"):
            info = (output / name).stat()
            archive_stats[name] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        _verify(output, root, audit, report)
        for name, expected in archive_stats.items():
            info = (output / name).stat()
            audit.check((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) == expected, "Archive identity unchanged during verification: " + name)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, EOFError, zipfile.BadZipFile) as error:
        audit.check(False, type(error).__name__ + ": " + str(error))
    audit.check(file_sha(__file__) == VERIFIER_SOURCE_SHA256, "Verifier source unchanged after verification")
    report.update(passed=audit.failures == 0, checks=audit.checks, failureCount=audit.failures, failures=audit.messages)
    return report


def save_report(output, report):
    path = Path(output) / "independent-verification.json"
    with path.open("xb") as stream:
        stream.write(encoded(report) + b"\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"],
                      "failureCount": report["failureCount"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
