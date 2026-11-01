"""Freeze causal TDX prefixes for three separate board research scopes.

No downloads, user configuration changes, signals or accounts. --self-test reads
synthetic files only. Real creation is exclusive and requires a pinned --plan.
"""
from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION = "board-coverage-source-v1"
BOARDS = ("main", "chinext", "star")
DAY = struct.Struct("<IIIIIfII")
LIMITATIONS = [
    "Present-day local file coverage, not a complete historical point-in-time universe.",
    "Fixed period-start membership uses 61 valid observed bars, not an IPO/listing date.",
    "Stocks becoming observable during the target interval are not added to the fixed sample.",
    "Listing date, historical membership, ST, suspension and corporate-action ledgers remain unknown.",
    "Raw unadjusted prices and simulated accounts cannot certify total returns or executable fills.",
    "The original account configuration is inherited; minListingDays is not an executed gate in FixedSignalAccount.",
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, "Invalid ISO date")
    return value


def digest(value):
    h = hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                                  separators=(",", ":")).iterencode(value):
        h.update(chunk.encode("utf-8"))
    return h.hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def write_gzip(path, value):
    with Path(path).open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=1) as stream:
            pending, size = [], 0
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                                          separators=(",", ":")).iterencode(value):
                pending.append(chunk)
                size += len(chunk)
                if size >= 65536:
                    stream.write("".join(pending).encode("utf-8"))
                    pending, size = [], 0
            if pending:
                stream.write("".join(pending).encode("utf-8"))


def ref(path, directory):
    return {"path": Path(path).relative_to(directory).as_posix(), "sha256": sha(path),
            "bytes": Path(path).stat().st_size}


def board_of(symbol):
    if re.fullmatch(r"sh60\d{4}|sz00\d{4}", symbol):
        return "main"
    if re.fullmatch(r"sz30\d{4}", symbol):
        return "chinext"
    if re.fullmatch(r"sh68\d{4}", symbol):
        return "star"
    return None


def calendar_document(document):
    """Read the two already registered calendar schemas, with overlap proof."""
    if document.get("provider") == "baostock" and document.get("api") == "query_trade_dates":
        require(document.get("errorCode") == "0" and document.get("rowBoundReached") is False,
                "Unsuccessful or incomplete provider calendar")
        fields = document.get("fields")
        require(fields == ["calendar_date", "is_trading_day"], "Provider calendar fields differ")
        rows = document.get("rows")
        require(isinstance(rows, list) and rows, "Empty provider calendar")
        dates, sessions = [], []
        for row in rows:
            require(isinstance(row, list) and len(row) == 2 and row[1] in ("0", "1"), "Invalid provider calendar row")
            dates.append(day(row[0]))
            if row[1] == "1":
                sessions.append(row[0])
        require(dates == sorted(set(dates)), "Duplicate/unsorted provider calendar dates")
        require((date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days + 1 == len(dates),
                "Provider calendar has missing civil dates")
        return dates[0], dates[-1], sessions
    require(document.get("schema") == "exchange-notice-derived-calendar-v1", "Unsupported independent calendar schema")
    first, last = day(document.get("start")), day(document.get("end"))
    dates = document.get("dates")
    require(isinstance(dates, list) and dates and dates == sorted(set(dates)), "Duplicate/empty exchange calendar")
    require(first <= last and all(first <= day(d) <= last for d in dates), "Exchange calendar dates outside declared interval")
    return first, last, dates


def merge_calendars(documents):
    spans = sorted(calendar_document(item) for item in documents)
    require(spans, "Independent calendars required")
    for i, (first, last, sessions) in enumerate(spans):
        for other_first, other_last, other_sessions in spans[:i]:
            left, right = max(first, other_first), min(last, other_last)
            if left <= right:
                require([d for d in sessions if left <= d <= right] ==
                        [d for d in other_sessions if left <= d <= right], "Independent calendar overlap differs")
    covered_end = spans[0][1]
    for first, last, _ in spans[1:]:
        require((date.fromisoformat(first) - date.fromisoformat(covered_end)).days <= 1,
                "Gap between independent calendar declared intervals")
        covered_end = max(covered_end, last)
    return sorted({d for _, _, dates in spans for d in dates}), spans[0][0], covered_end


def valid_observation(bar):
    def positive(value):
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    return (all(positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume"))
            and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]
            and type(bar.get("amount")) in (int, float) and math.isfinite(bar["amount"]) and bar["amount"] >= 0)


def parse_prefix(raw, cutoff, calendar):
    """The first future date stops decoding: future OHLC/amount cannot leak in."""
    require(raw and len(raw) % DAY.size == 0, "Empty or non-32-byte-aligned daily source")
    allowed, bars, prefix_size, previous = set(calendar), [], 0, None
    for offset in range(0, len(raw), DAY.size):
        encoded_date = struct.unpack_from("<I", raw, offset)[0]
        observed = date(encoded_date // 10000, encoded_date // 100 % 100, encoded_date % 100).isoformat()
        if observed > cutoff:
            cutoff_number = int(cutoff.replace("-", ""))
            require(all(struct.unpack_from("<I", raw, later)[0] > cutoff_number
                        for later in range(offset, len(raw), DAY.size)),
                    "Historical observation occurs after future suffix; source is not a causal prefix")
            break
        require(observed in allowed, "Raw prefix date outside independent calendar: " + observed)
        require(previous is None or previous < observed, "Duplicate/unsorted raw prefix dates")
        _, opening, high, low, close, amount, volume, _ = DAY.unpack_from(raw, offset)
        require(math.isfinite(amount), "Raw prefix contains nonfinite amount")
        bars.append({"date": observed, "open": opening / 100, "high": high / 100,
                     "low": low / 100, "close": close / 100, "volume": volume, "amount": amount})
        previous, prefix_size = observed, offset + DAY.size
    return bars, {"prefixBytes": prefix_size, "prefixRecords": len(bars),
                  "prefixSha256": hashlib.sha256(raw[:prefix_size]).hexdigest(),
                  "firstDate": bars[0]["date"] if bars else None,
                  "lastDate": bars[-1]["date"] if bars else None}


def stat_identity(path):
    stat = Path(path).stat()
    return {"bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns,
            "ctimeNs": stat.st_ctime_ns, "inode": stat.st_ino}


def stable_bytes(path):
    before = stat_identity(path)
    raw = Path(path).read_bytes()
    require(before == stat_identity(path) and before["bytes"] == len(raw), "Source changed while reading")
    return raw, {**before, "sha256": hashlib.sha256(raw).hexdigest()}


def enumerate_sources(root):
    entries, ignored = [], []
    for market in ("sh", "sz"):
        folder = root / "vipdoc" / market / "lday"
        require(folder.is_dir(), "Missing source market directory: " + market)
        for path in sorted(folder.glob("*.day")):
            symbol = path.stem.lower()
            if re.fullmatch(r"\d{6}", symbol):
                symbol = market + symbol
            board = board_of(symbol)
            relative = path.relative_to(root).as_posix()
            require(path.resolve().is_relative_to(root.resolve()), "Source path escapes TDX root")
            if board is None or symbol[:2] != market:
                ignored.append(relative)
                continue
            entries.append({"symbol": symbol, "board": board, "path": relative})
    entries.sort(key=lambda r: (r["symbol"], r["path"]))
    require(len({r["symbol"] for r in entries}) == len(entries), "Duplicate source symbol identity")
    require(all(any(r["board"] == board for r in entries) for board in BOARDS), "One registered board has no local files")
    return entries, ignored


def observation_evidence(symbol, board, bars, start, threshold):
    prior = [bar for bar in bars if bar["date"] < start]
    valid_dates = [bar["date"] for bar in prior if valid_observation(bar)]
    included = len(valid_dates) >= threshold
    return {"symbol": symbol, "board": board, "priorObservationCount": len(prior),
            "priorValidObservationCount": len(valid_dates), "priorInvalidObservationCount": len(prior) - len(valid_dates),
            "priorValidDatesSha256": digest(valid_dates), "priorFirstDate": prior[0]["date"] if prior else None,
            "priorLastDate": prior[-1]["date"] if prior else None,
            "selectionStatus": "included" if included else "excluded",
            "reason": "atLeast61ValidPriorObservations" if included else "fewerThan61ValidPriorObservations"}


def account_compatible(bars):
    # Zero volume is retained as a source observation. It never establishes maturity.
    return all(all(type(b.get(k)) in (int, float) and math.isfinite(b[k]) and .000001 <= b[k] <= 1e8
                   for k in ("open", "high", "low", "close"))
               and b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]
               and math.isfinite(b["amount"]) and b["amount"] >= 0
               and 0 <= b["volume"] <= 1e15 for b in bars)


def dataset_for(period, board, securities, benchmark, calendar, start, end):
    symbols = [s["symbol"] for s in securities]
    require(symbols == sorted(set(symbols)) and securities, "Empty/duplicate board sample")
    data = {"source": VERSION, "priceBasisId": "raw-unadjusted", "adjustment": "none",
            "startDate": start, "endDate": end, "period": period, "board": board,
            "securities": securities, "benchmark": benchmark, "calendar": calendar,
            "calendarSource": "pinned-independent-exchange-calendars-merged",
            "universeSelection": {"mode": "periodStartObservedMaturity", "markets": ["sh", "sz"],
                                  "boards": [board], "minPriorValidObservations": 61, "membershipAsOf": start},
            "universeCoverage": {"scope": "fixed-period-start-local-board-observations", "loadedCount": len(securities),
                                 "barCount": sum(len(s["bars"]) for s in securities), "symbolsSha256": digest(symbols),
                                 "pointInTimeComplete": False, "boardCounts": {board: len(securities)}},
            "dataQuality": {"certified": False, "pointInTimeComplete": False, "listingDatesKnown": 0,
                            "historicalStKnownBars": 0, "historicalMemberKnownBars": 0,
                            "reasons": ["local-file-survivor-coverage", "historical-status-and-rights-unknown"]},
            "warnings": list(LIMITATIONS)}
    data["fingerprint"] = digest(data)
    return data


def pinned_plan(path):
    plan, pins = read(path), {}
    for name in ("inputPins", "sourcePins"):
        require(isinstance(plan.get(name), dict) and plan[name], "Missing plan " + name)
        for relative, expected in plan[name].items():
            target = (ROOT / relative).resolve()
            require(not Path(relative).is_absolute() and target.is_relative_to(ROOT), "Pin path outside repository")
            require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "Invalid SHA pin")
            require(sha(target) == expected, "Frozen pin changed: " + relative)
            pins[relative] = expected
    own = Path(__file__).relative_to(ROOT).as_posix()
    require(plan["sourcePins"].get(own) == sha(__file__), "Builder must be in plan.sourcePins")
    spec = plan.get("sourceSpec", {})
    require(spec.get("boards") == list(BOARDS) and spec.get("benchmarkSymbol") == "sh000300"
            and type(spec.get("minPriorValidObservations")) is int and spec["minPriorValidObservations"] == 61,
            "Registered source scope differs")
    require(isinstance(spec.get("calendarPaths"), list) and len(spec["calendarPaths"]) == 2
            and len(set(spec["calendarPaths"])) == 2
            and all(name in plan["inputPins"] for name in spec["calendarPaths"]), "Both calendar paths must be input-pinned")
    root = Path(spec.get("tdxRoot", "")).resolve()
    require(root.is_dir() and not path.parent.resolve().is_relative_to(root), "Invalid or overlapping TDX/output root")
    require(isinstance(plan.get("periods"), dict) and plan["periods"], "Registered periods required")
    for period, fields in plan["periods"].items():
        require(re.fullmatch(r"[a-zA-Z0-9-]+", period) is not None, "Unsafe period identity")
        require(day(fields.get("startDate")) <= day(fields.get("endDate")), "Invalid period interval")
    return plan, pins, root


def build(plan_path):
    plan_path = Path(plan_path).resolve()
    plan, pins, root = pinned_plan(plan_path)
    output, plan_sha = plan_path.parent, sha(plan_path)
    spec = plan["sourceSpec"]
    calendar, covered_start, covered_end = merge_calendars([read(ROOT / p) for p in spec["calendarPaths"]])
    require(all(covered_start <= p["startDate"] <= p["endDate"] <= covered_end for p in plan["periods"].values()),
            "Period outside independent calendar coverage")
    expected = [output / "source-completion.json", output / "source-attempt.json", output / "source-failure.json"]
    expected += [output / p / n for p in plan["periods"] for n in
                 ["source-manifest.json", *[b + ".account-data.json.gz" for b in BOARDS]]]
    require(not any(path.exists() for path in expected), "Exclusive source output already exists")
    write(output / "source-attempt.json", {"schema": VERSION + "-attempt", "startedAt": now(),
                                          "planSha256": plan_sha, "builderSha256": sha(__file__), "actualAccountRuns": 0})
    try:
        entries, ignored = enumerate_sources(root)
        catalog_digest = digest(entries)
        physical, outputs = {}, {}
        for period, fields in plan["periods"].items():
            start, end = fields["startDate"], fields["endDate"]
            dates = [d for d in calendar if d <= end]
            require(start in dates and end in dates, "Period boundaries must be independent trading sessions")
            folder = output / period
            folder.mkdir(exist_ok=True)
            records, boards, failures = [], {}, []
            benchmark_path = root / "vipdoc/sh/lday/sh000300.day"
            raw, identity = stable_bytes(benchmark_path)
            benchmark_bars, benchmark_prefix = parse_prefix(raw, end, dates)
            require(benchmark_bars and account_compatible(benchmark_bars), "Invalid benchmark prefix")
            key = benchmark_path.relative_to(root).as_posix()
            require(key not in physical or physical[key] == identity, "Benchmark changed between periods")
            physical[key] = identity
            benchmark = {"symbol": "sh000300", "bars": benchmark_bars, "source": "TDX raw local index",
                         "priceBasisId": "raw-unadjusted", "volumeUnit": "lots", "amountUnit": "CNY"}
            for board in BOARDS:
                securities = []
                board_failures = 0
                for entry in (r for r in entries if r["board"] == board):
                    record = dict(entry)
                    try:
                        raw, identity = stable_bytes(root / entry["path"])
                        require(entry["path"] not in physical or physical[entry["path"]] == identity,
                                "Source changed between periods")
                        physical[entry["path"]] = identity
                        bars, prefix = parse_prefix(raw, end, dates)
                        record.update(physicalSource=identity, prefix=prefix)
                        record.update(observation_evidence(entry["symbol"], board, bars, start, 61))
                        if record["selectionStatus"] == "included":
                            require(account_compatible(bars), "Selected source has invalid raw bar; no symbol may be dropped")
                            securities.append({"symbol": entry["symbol"], "name": entry["symbol"], "board": board,
                                               "bars": bars, "adjustment": "none", "localCoverageOnly": True,
                                               "volumeUnit": "shares", "amountUnit": "CNY",
                                               "historicalNameKnown": False, "corporateActionsComplete": False,
                                               "sourceSha256": digest(bars), "rawPrefixSha256": prefix["prefixSha256"]})
                    except (ValueError, OSError, OverflowError) as error:
                        board_failures += 1
                        record.setdefault("selectionStatus", "unknown")
                        record.setdefault("reason", "sourceReadOrStructureFailure")
                        record["readFailure"] = {"type": type(error).__name__, "message": str(error)}
                        failures.append({"symbol": entry["symbol"], **record["readFailure"]})
                    records.append(record)
                selected_symbols = [s["symbol"] for s in securities]
                evidence = {"includedCount": sum(r["board"] == board and r["selectionStatus"] == "included" for r in records),
                            "excludedCount": sum(r["board"] == board and r["selectionStatus"] == "excluded" for r in records),
                            "unknownCount": sum(r["board"] == board and r["selectionStatus"] == "unknown" for r in records),
                            "readFailureCount": board_failures, "symbolsSha256": digest(selected_symbols), "accountData": None}
                if not board_failures:
                    dataset = dataset_for(period, board, securities, benchmark, dates, start, end)
                    target = folder / (board + ".account-data.json.gz")
                    write_gzip(target, dataset)
                    evidence.update(accountData={**ref(target, output), "datasetDigest": digest(dataset),
                                                 "fingerprint": dataset["fingerprint"]},
                                    retainedBarCount=sum(len(s["bars"]) for s in securities))
                    del dataset
                boards[board] = evidence
                print(json.dumps({"phase": "board-source", "period": period, "board": board,
                                  "included": evidence["includedCount"], "excluded": evidence["excludedCount"],
                                  "failures": board_failures}), flush=True)
                del securities
            records.sort(key=lambda r: r["symbol"])
            manifest = {"schema": VERSION + "-manifest", "period": period, "passed": not failures,
                        "createdAt": now(), "planSha256": plan_sha, "builderSha256": sha(__file__),
                        "sourceSpec": spec, "startDate": start, "endDate": end,
                        "inputPins": pins, "inventorySha256": catalog_digest, "inventoryCount": len(entries),
                        "ignoredNonStudyFiles": ignored, "calendar": dates, "calendarSha256": digest(dates),
                        "calendarSources": {p: pins[p] for p in spec["calendarPaths"]},
                        "targetSessions": sum(start <= d <= end for d in dates), "boards": boards,
                        "benchmark": {"symbol": "sh000300", "path": key, "physicalSource": physical[key],
                                      "prefix": benchmark_prefix, "barsDigest": digest(benchmark_bars)},
                        "records": records, "readFailures": failures, "limitations": LIMITATIONS,
                        "signalsCalculated": False, "actualAccountRuns": 0, "certified": False}
            write(folder / "source-manifest.json", manifest)
            require(not failures, "Period source contains read/quality failures; manifest retained, no completion")
            outputs[period] = ref(folder / "source-manifest.json", output)
        require(enumerate_sources(root) == (entries, ignored), "Source file inventory changed during materialization")
        for relative, original in physical.items():
            _, current = stable_bytes(root / relative)
            require(current == original, "Physical source changed during materialization: " + relative)
        require(sha(plan_path) == plan_sha, "Plan changed during materialization")
        for relative, expected_sha in pins.items():
            require(sha(ROOT / relative) == expected_sha, "Frozen pin changed during materialization: " + relative)
        completion = {"schema": VERSION + "-completion", "passed": True, "completedAt": now(),
                      "planSha256": plan_sha, "builderSha256": sha(__file__), "periods": outputs,
                      "inventoryCount": len(entries), "inventorySha256": catalog_digest,
                      "physicalSourceCount": len(physical), "sourceFilesUnchanged": True,
                      "signalsCalculated": False, "actualAccountRuns": 0, "certified": False}
        write(output / "source-completion.json", completion)
        return completion
    except Exception as error:
        write(output / "source-failure.json", {"schema": VERSION + "-failure", "failedAt": now(),
              "planSha256": plan_sha, "builderSha256": sha(__file__), "type": type(error).__name__,
              "message": str(error), "partialOutputsRetained": True, "actualAccountRuns": 0})
        raise


def self_test():
    class Cases(unittest.TestCase):
        def test_prefix_future_invariance(self):
            a = DAY.pack(20220103, 100, 110, 90, 105, 1000., 100, 0)
            future = DAY.pack(20220104, 0, 0, 0, 0, float("nan"), 0, 0)
            self.assertEqual(parse_prefix(a, "2022-01-03", ["2022-01-03"]),
                             parse_prefix(a + future, "2022-01-03", ["2022-01-03"]))

        def test_future_suffix_cannot_hide_historical_rows(self):
            historical = DAY.pack(20220103, 100, 110, 90, 105, 1000., 100, 0)
            future = DAY.pack(20220104, 100, 110, 90, 105, 1000., 100, 0)
            with self.assertRaises(ValueError):
                parse_prefix(future + historical, "2022-01-03", ["2022-01-03"])

        def test_exact_dates_no_compression(self):
            raw = DAY.pack(20220103, 100, 110, 90, 105, 0., 100, 0)
            bars, _ = parse_prefix(raw, "2022-01-04", ["2022-01-03", "2022-01-04"])
            self.assertEqual(len(bars), 1)
            for data, cal in ((raw + raw, ["2022-01-03"]), (raw, ["2022-01-04"]), (raw + b"x", ["2022-01-03"])):
                with self.assertRaises(ValueError): parse_prefix(data, "2022-01-04", cal)

        def test_maturity_before_start_only(self):
            bars = [{"date": date(2021, 1, 1).fromordinal(date(2021, 1, 1).toordinal() + i).isoformat(),
                     "open": 1., "high": 2., "low": .5, "close": 1., "volume": 1, "amount": 0.} for i in range(62)]
            before = deepcopy(bars)
            self.assertEqual(observation_evidence("sh600001", "main", bars, bars[61]["date"], 61)["selectionStatus"], "included")
            self.assertEqual(observation_evidence("sh600001", "main", bars, bars[60]["date"], 61)["selectionStatus"], "excluded")
            self.assertEqual(bars, before)
            bars[0]["volume"] = 0
            self.assertEqual(observation_evidence("sh600001", "main", bars, bars[61]["date"], 61)["priorValidObservationCount"], 60)
            self.assertEqual(before[0]["amount"], 0.)

        def test_quality_zero_amount_not_zero_volume(self):
            bar = {"open": 1., "high": 2., "low": .5, "close": 1., "volume": 1, "amount": 0.}
            self.assertTrue(valid_observation(bar))
            for field, value in (("volume", 0), ("amount", -1), ("amount", float("nan")), ("low", 1.1), ("close", True)):
                self.assertFalse(valid_observation({**bar, field: value}))

        def test_calendar_overlap_and_gap(self):
            def doc(first, last, dates): return {"schema": "exchange-notice-derived-calendar-v1", "start": first, "end": last, "dates": dates}
            a = doc("2022-01-01", "2022-01-05", ["2022-01-03", "2022-01-04", "2022-01-05"])
            b = doc("2022-01-04", "2022-01-07", ["2022-01-04", "2022-01-05", "2022-01-07"])
            self.assertEqual(merge_calendars([a, b])[0], ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-07"])
            for bad in (doc("2022-01-04", "2022-01-07", ["2022-01-04", "2022-01-07"]), doc("2022-01-08", "2022-01-09", ["2022-01-09"])):
                with self.assertRaises(ValueError): merge_calendars([a, bad])

        def test_provider_calendar(self):
            item = {"provider": "baostock", "api": "query_trade_dates", "errorCode": "0", "rowBoundReached": False,
                    "fields": ["calendar_date", "is_trading_day"], "rows": [["2022-01-01", "0"], ["2022-01-02", "1"]]}
            self.assertEqual(calendar_document(item)[2], ["2022-01-02"])
            item["rows"].append(["2022-01-04", "1"])
            with self.assertRaises(ValueError): calendar_document(item)

        def test_board_identity_and_duplicates(self):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for symbol in ("sh600001", "sh688001", "sz300001", "sh000300", "sz399006"):
                    folder = root / "vipdoc" / symbol[:2] / "lday"; folder.mkdir(parents=True, exist_ok=True)
                    (folder / (symbol + ".day")).write_bytes(b"synthetic")
                rows, ignored = enumerate_sources(root)
                self.assertEqual([r["board"] for r in rows], ["main", "star", "chinext"])
                self.assertEqual(len(ignored), 2)
                (root / "vipdoc/sh/lday/600001.day").write_bytes(b"duplicate")
                with self.assertRaises(ValueError): enumerate_sources(root)

        def test_no_data_mutation_and_exclusive_gzip(self):
            bars = [{"date": "2022-01-03", "open": 1., "high": 1., "low": 1., "close": 1., "volume": 1, "amount": 0.}]
            securities = [{"symbol": "sh600001", "bars": bars}]
            original = deepcopy(securities)
            data = dataset_for("synthetic", "main", securities, {"symbol": "sh000300", "bars": bars}, ["2022-01-03"], "2022-01-03", "2022-01-03")
            self.assertEqual(securities, original)
            self.assertEqual(data["fingerprint"], digest({k: v for k, v in data.items() if k != "fingerprint"}))
            with tempfile.TemporaryDirectory() as temporary:
                p = Path(temporary) / "one.gz"; write_gzip(p, data)
                with gzip.open(p, "rt", encoding="utf-8") as stream: self.assertEqual(json.load(stream), data)
                with self.assertRaises(FileExistsError): write_gzip(p, data)

        def test_selected_invalid_not_silently_dropped(self):
            bars = [{"date": "2022-01-03", "open": 1., "high": 1., "low": 1., "close": 1., "volume": 0, "amount": 0.}]
            self.assertTrue(account_compatible(bars))
            self.assertFalse(account_compatible([{**bars[0], "close": 0.}]))
            self.assertFalse(account_compatible([{**bars[0], "amount": -1.}]))

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Cases))
    require(result.wasSuccessful(), "Synthetic source tests failed")
    return {"passed": True, "tests": result.testsRun, "syntheticOnly": True, "realDataRead": False,
            "actualAccountRuns": 0, "persistentOutputsWritten": False}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    require(args.self_test != bool(args.plan), "Specify exactly one of --plan or --self-test")
    print(json.dumps(self_test() if args.self_test else build(args.plan), ensure_ascii=False))
