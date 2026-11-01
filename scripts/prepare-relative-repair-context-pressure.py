"""Bind frozen relative-repair opportunities to calendar-T-1 pressure evidence.

This prepares inputs only: no account is started and no realized outcome is
used to choose signals. The existing source loader and pressure implementation
are reused, then the saved calendar slots are independently recalculated.
Run --self-test without a plan to exercise synthetic inputs only.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIODS = {"2022-2023": 565, "2024-2025": 624}
REQUIRED_CODE = (
    "scripts/prepare-relative-repair-context-pressure.py",
    "scripts/load-market-context-screen.py",
    "scripts/run-market-context-screen.py",
    "service/force_statistics.py",
)
SOURCE_ROOT = "artifacts/strategy-search/relative-repair-cost-stress-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local(name):
    require(type(name) is str and bool(name), "Expected a nonempty repository-relative path")
    path = Path(name)
    require(not path.is_absolute() and ".." not in path.parts,
            "Pinned paths must be repository relative without parent traversal")
    resolved = (ROOT / path).resolve()
    require(resolved.is_relative_to(ROOT.resolve()), "Pinned path leaves repository")
    return resolved


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def write_exclusive(path, value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8") + b"\n"
    with path.open("xb") as stream:
        if path.suffix == ".gz":
            with gzip.GzipFile(filename="", mode="wb", compresslevel=1,
                               fileobj=stream, mtime=0) as compressed:
                compressed.write(payload)
        else:
            stream.write(payload)


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify_pins(plan):
    for category in ("inputPins", "codePins"):
        pins = plan.get(category)
        require(type(pins) is dict and bool(pins), f"Missing {category}")
        for name, expected in pins.items():
            require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
                    f"Invalid SHA-256 for {name}")
            require(sha(local(name)) == expected, f"Pinned file changed: {name}")
    require(set(REQUIRED_CODE) <= set(plan["codePins"]), "Required implementation code pins missing")


def check_signals(signals, calendar=None):
    require(type(signals) is list, "Signal schedule must be a list")
    ids = []
    calendar_set = None if calendar is None else set(calendar)
    for signal in signals:
        require(type(signal) is dict, "Signal must be an object")
        for field in ("signalId", "symbol", "signalDate"):
            require(type(signal.get(field)) is str and bool(signal[field]),
                    f"Missing signal identity field: {field}")
        day = signal["signalDate"]
        require(date.fromisoformat(day).isoformat() == day, "Invalid signal date")
        require(calendar_set is None or day in calendar_set, "Signal date absent from source calendar")
        ids.append(signal["signalId"])
    require(len(ids) == len(set(ids)), "Duplicate signal IDs")
    return ids


def source_bars(raw, signals):
    days = raw["calendar"]
    require(type(days) is list and days == sorted(set(days)), "Invalid source calendar order")
    require(all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in days),
            "Invalid source calendar date")
    check_signals(signals, days)
    needed = {row["symbol"] for row in signals}
    found = {}
    for security in raw["securities"]:
        symbol = security["symbol"]
        if symbol not in needed:
            continue
        require(symbol not in found, "Duplicate source security")
        bars = security["bars"]
        dates = [bar["date"] for bar in bars]
        require(dates == sorted(set(dates)), "Duplicate or unordered source bars")
        found[symbol] = {bar["date"]: bar for bar in bars}
    require(set(found) == needed, "Signal security absent from frozen source")
    return found


def valid_bar(bar):
    if type(bar) is not dict:
        return False
    for field in ("open", "high", "low", "close", "volume"):
        value = bar.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            return False
    return bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]


def independent_pressure(window, length):
    """Recompute one aggregate directly from raw slots, without force helpers."""
    require(length in (5, 20), "Unsupported pressure horizon")
    if len(window) < 20 + length:
        return None
    weighted, weights = [], []
    for index in range(len(window) - length, len(window)):
        baseline = window[index - 20:index]
        current = window[index]
        if len(baseline) != 20 or not all(valid_bar(bar) for bar in baseline + [current]):
            return None
        # Scale the arithmetic mean to preserve finite arithmetic at large volumes.
        volumes = [bar["volume"] for bar in baseline]
        scale = max(volumes)
        average = scale * (math.fsum(volume / scale for volume in volumes) / 20)
        weight = min(current["volume"] / average, 3.)
        previous_close = window[index - 1]["close"]
        true_range = max(current["high"], previous_close) - min(current["low"], previous_close)
        pressure = (current["close"] - previous_close) / true_range if true_range else 0.
        weighted.append(weight * pressure)
        weights.append(weight)
    denominator = math.fsum(weights)
    return math.fsum(weighted) / denominator if denominator > 0 else None


def same_number(actual, expected):
    if expected is None:
        return actual is None
    return (type(actual) in (int, float) and math.isfinite(actual)
            and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12))


def validate_pressure(prior, signals, raw):
    bars = source_bars(raw, signals)
    days = raw["calendar"]
    require(prior["sourceCalendar"] == days and prior["sourceCalendarSha256"] == digest(days),
            "Pressure source calendar differs")
    ids = check_signals(signals, days)
    require(type(prior["rows"]) is dict and list(prior["rows"]) == ids,
            "Pressure rows lost, duplicated, added or reordered signals")
    positions = {day: index for index, day in enumerate(days)}
    counts = {"signalCount": len(ids), "pressure5": {"known": 0, "unknown": 0},
              "pressure20": {"known": 0, "unknown": 0}, "bothKnown": 0, "anyUnknown": 0,
              "emptyWindows": 0, "shortWindows": 0, "windowsWithMissingSlots": 0,
              "windowsWithInvalidBars": 0, "missingSlots": 0, "invalidBars": 0}
    for signal in signals:
        row = prior["rows"][signal["signalId"]]
        position = positions[signal["signalDate"]]
        expected_dates = days[max(0, position - 40):position]
        previous = days[position - 1] if position else None
        require(row["symbol"] == signal["symbol"] and row["signalDate"] == signal["signalDate"],
                "Pressure row signal identity differs")
        require(row["asOfDate"] == row["previousCalendarDate"] == previous,
                "Pressure row is not market-calendar T-1")
        require(row["windowDates"] == expected_dates and len(expected_dates) <= 40
                and all(day < signal["signalDate"] for day in expected_dates),
                "Pressure window includes T/future data or compresses source calendar")
        expected_bars = [bars[signal["symbol"]].get(day) for day in expected_dates]
        window = row["windowBars"]
        require(window == expected_bars and len(window) == len(expected_dates),
                "Saved raw calendar slots differ from source")
        require(row["windowSha256"] == digest(window), "Saved pressure window hash differs")
        require(row["sourceDatasetFingerprint"] == raw["fingerprint"], "Pressure source fingerprint differs")
        for index, bar in enumerate(window):
            require(bar is None or bar["date"] == expected_dates[index], "Saved bar calendar date differs")
        for horizon in (5, 20):
            key = f"pressure{horizon}"
            expected = independent_pressure(window, horizon)
            require(same_number(row[key], expected), f"Independent {key} differs: {signal['signalId']}")
            counts[key]["unknown" if expected is None else "known"] += 1
        both_known = row["pressure5"] is not None and row["pressure20"] is not None
        counts["bothKnown" if both_known else "anyUnknown"] += 1
        missing = sum(bar is None for bar in window)
        invalid = sum(bar is not None and not valid_bar(bar) for bar in window)
        counts["emptyWindows"] += not window
        counts["shortWindows"] += len(window) < 40
        counts["windowsWithMissingSlots"] += missing > 0
        counts["windowsWithInvalidBars"] += invalid > 0
        counts["missingSlots"] += missing
        counts["invalidBars"] += invalid
    return counts


def prepare(plan_path):
    plan_path = plan_path.resolve()
    require(plan_path.is_relative_to(ROOT.resolve()), "Plan must reside in repository")
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    verify_pins(plan)
    require(type(plan.get("periods")) is dict and set(plan["periods"]) == set(PERIODS),
            "Plan must bind exactly the two frozen periods")
    schedules, identities = {}, {}
    for period, expected_count in PERIODS.items():
        specification = plan["periods"][period]
        require(specification.get("signalsPath") == f"{SOURCE_ROOT}/{period}/signals.json"
                and specification.get("sourcePath") == f"{SOURCE_ROOT}/{period}/source.json",
                "Plan must use the original cost-stress signal and source files")
        require(type(specification.get("signalCount")) is int
                and specification["signalCount"] == expected_count, "Frozen opportunity count differs")
        for field in ("signalsPath", "sourcePath"):
            require(specification[field] in plan["inputPins"], f"Missing period input pin: {field}")
        signals = read(local(specification["signalsPath"]))
        check_signals(signals)
        require(len(signals) == expected_count, "Frozen signal schedule is incomplete")
        schedules[period] = signals
        identities[period] = read(local(specification["sourcePath"]))
        for filename in ("prior-pressure.json.gz", "pressure-binding.json"):
            require(not (plan_path.parent / period / filename).exists(),
                    f"Refusing to overwrite existing {period}/{filename}")
    loader = module("_relative_context_pressure_loader", "load-market-context-screen.py")
    helper = module("_relative_context_pressure_helper", "run-market-context-screen.py")
    for period in PERIODS:
        print(json.dumps({"phase": "pressure-source-load", "period": period}), flush=True)
        raw, account_data, identity, fee_policy = loader.load_account_source(period)
        require(identity == identities[period], "Loaded source identity differs from cost-stress source")
        del account_data, fee_policy  # No account construction or execution.
        source_bars(raw, schedules[period])
        prior = helper.prior_pressure(raw, schedules[period])
        validate_pressure(prior, schedules[period], raw)
        require(plan_path.read_bytes() == plan_bytes, "Frozen plan changed during preparation")
        verify_pins(plan)
        directory = plan_path.parent / period
        directory.mkdir(exist_ok=True)
        pressure_path = directory / "prior-pressure.json.gz"
        write_exclusive(pressure_path, prior)
        saved = read(pressure_path)
        require(saved == prior, "Saved pressure evidence differs after serialization")
        counts = validate_pressure(saved, schedules[period], raw)
        binding = {"schema": "relative-repair-context-pressure-binding-v1", "period": period,
                   "preparedAt": datetime.now(timezone.utc).isoformat(),
                   "plan": plan_path.relative_to(ROOT).as_posix(), "planSha256": plan_sha,
                   "inputPins": plan["inputPins"], "codePins": plan["codePins"],
                   "sourceIdentity": identity, "sourceDatasetFingerprint": raw["fingerprint"],
                   "signalPath": plan["periods"][period]["signalsPath"],
                   "signalFileSha256": plan["inputPins"][plan["periods"][period]["signalsPath"]],
                   "signalScheduleSha256": digest(schedules[period]),
                   "sourceIdentityFileSha256": plan["inputPins"][plan["periods"][period]["sourcePath"]],
                   "sourceCalendarSha256": saved["sourceCalendarSha256"],
                   "signalCount": len(schedules[period]), "counts": counts,
                   "output": {"path": pressure_path.relative_to(ROOT).as_posix(), "sha256": sha(pressure_path)},
                   "verification": {"passed": True, "savedWindowMathRecomputed": True,
                                    "allSignalsRetainedInOrder": True, "calendarSlotsNotCompressed": True,
                                    "signalDayOrFutureBarsUsed": False},
                   "definition": {"cutoff": "market-calendar T-1", "maximumCalendarSlots": 40,
                                  "dailyPressure": "(close-prevClose)/(max(high,prevClose)-min(low,prevClose)); zeroTR=0",
                                  "weight": "min(volume/mean(previous20 calendar-slot volumes),3)",
                                  "aggregation": "sum(weight*pressure)/sum(weight), last5 or last20",
                                  "unknown": "Missing or invalid required OHLCV slots remain null; no gap compression."},
                   "scope": "Frozen price-volume proxies only; not observed participant holdings or money flows.",
                   "accountsExecuted": 0, "outcomeFiltering": False, "certified": False}
        write_exclusive(directory / "pressure-binding.json", binding)
        print(json.dumps({"phase": "pressure-period-bound", "period": period, "counts": counts,
                          "outputSha256": binding["output"]["sha256"]}), flush=True)
        del raw, prior, saved
    require(plan_path.read_bytes() == plan_bytes, "Frozen plan changed during preparation")
    verify_pins(plan)


def self_test():
    helper = module("_relative_context_pressure_self_test", "run-market-context-screen.py")
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(45)]
    bars = [{"date": day, "open": 100., "high": 100., "low": 100., "close": 100., "volume": 100.}
            for day in days]
    bars[39].update(high=103., low=99., close=102., volume=1000.)
    raw = {"calendar": days, "fingerprint": "synthetic-only", "securities": [{"symbol": "TEST", "bars": bars}]}
    signals = [{"signalId": "test:40", "signalDate": days[40], "symbol": "TEST"}]
    cases = []

    def calculate(value, schedule=signals):
        source_bars(value, schedule)
        result = helper.prior_pressure(value, schedule)
        # A JSON roundtrip exercises the saved-value validation path without files.
        saved = json.loads(json.dumps(result, allow_nan=False))
        validate_pressure(saved, schedule, value)
        return saved

    reference = calculate(raw)
    current = reference["rows"]["test:40"]
    require(math.isclose(current["pressure5"], 3 / 14, abs_tol=1e-12)
            and math.isclose(current["pressure20"], 3 / 44, abs_tol=1e-12), "Hand-computed capped-weight example failed")
    cases.append("prior20-excludes-current-volume; weight-cap3; zero-TR; hand-values3/14-and3/44")
    changed = deepcopy(raw)
    for bar in changed["securities"][0]["bars"][40:]:
        bar.update(open=1., low=1., high=100000., close=99999., volume=1e12)
    require(calculate(changed)["rows"] == reference["rows"], "Signal-day or future OHLCV leaked")
    cases.append("signal-day-and-future-mutations-do-not-change-prior-pressure")
    changed = deepcopy(raw)
    del changed["securities"][0]["bars"][30]
    gap = calculate(changed)["rows"]["test:40"]
    require(len(gap["windowBars"]) == 40 and gap["windowBars"][30] is None
            and gap["pressure5"] is None and gap["pressure20"] is None, "Missing calendar slot compressed")
    cases.append("missing-required-row-retains-null-slot-and-null-pressure")
    changed = deepcopy(raw)
    del changed["securities"][0]["bars"][12]
    gap = calculate(changed)["rows"]["test:40"]
    require(gap["pressure5"] == current["pressure5"] and gap["pressure20"] is None,
            "Unknown propagated outside dependent pressure horizon")
    cases.append("missing-early-slot-only-invalidates-dependent20-horizon")
    for field, value in (("volume", 0.), ("low", 1000.), ("volume", True)):
        changed = deepcopy(raw)
        changed["securities"][0]["bars"][39][field] = value
        invalid = calculate(changed)["rows"]["test:40"]
        require(invalid["pressure5"] is None and invalid["pressure20"] is None, "Invalid bar treated as observed zero")
    cases.append("invalid-volume-ohlc-and-boolean-retain-null")
    early = [{"signalId": f"early:{i}", "signalDate": days[i], "symbol": "TEST"} for i in (0, 10)]
    early_rows = calculate(raw, early)["rows"]
    require([len(row["windowBars"]) for row in early_rows.values()] == [0, 10]
            and all(row["pressure5"] is None and row["pressure20"] is None for row in early_rows.values()),
            "Empty or short windows did not retain unknown")
    require(calculate(raw, [])["rows"] == {}, "Empty schedule was not preserved")
    cases.append("empty-short-windows-and-empty-schedule-preserved")
    ordered = [dict(signals[0], signalId=identifier) for identifier in ("z-last-lexically", "a-first-lexically")]
    require(list(calculate(raw, ordered)["rows"]) == [row["signalId"] for row in ordered],
            "Original nonlexicographic opportunity order changed")
    cases.append("all-signals-retained-in-original-nonlexicographic-order")
    try:
        calculate(raw, signals + signals)
    except ValueError as error:
        require("Duplicate signal IDs" in str(error), "Duplicate rejection reason differs")
    else:
        raise ValueError("Duplicate signals accepted")
    cases.append("duplicate-signal-id-rejected-before-helper")
    for mutation in ("pressure", "calendar", "drop"):
        damaged = deepcopy(reference)
        if mutation == "pressure":
            damaged["rows"]["test:40"]["pressure5"] += .01
        elif mutation == "calendar":
            damaged["rows"]["test:40"]["windowDates"][-1] = days[40]
        else:
            damaged["rows"].clear()
        try:
            validate_pressure(damaged, signals, raw)
        except ValueError:
            pass
        else:
            raise ValueError(f"Independent verifier accepted corrupted {mutation}")
    cases.append("verifier-rejects-numeric-drift-signal-day-slot-and-dropped-signal")
    print(json.dumps({"passed": True, "selfTests": cases, "caseGroups": len(cases),
                      "realSourceLoaded": False, "accountsExecuted": 0,
                      "scriptSha256": sha(Path(__file__).resolve())}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path)
    mode.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        self_test()
    else:
        prepare(arguments.plan)


if __name__ == "__main__":
    main()
