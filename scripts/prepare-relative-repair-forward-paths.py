"""Save fixed forward raw-bar evidence for every original control opportunity.

This is outcome evidence, never a feature available at signal time. No returns,
signal selection, accounts or trading decisions are calculated here. Windows
retain missing calendar slots and stop at each original period's cutoff.
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
PERIODS = {
    "2022-2023": {"signalCount": 565, "start": "2022-01-04", "end": "2023-12-29"},
    "2024-2025": {"signalCount": 624, "start": "2024-01-02", "end": "2025-12-31"},
}
HORIZONS = [5, 10, 20, 60]
MAX_HORIZON = 60
COST_ROOT = "artifacts/strategy-search/relative-repair-cost-stress-v1"
CONTEXT_ROOT = "artifacts/strategy-search/relative-repair-context-diagnostic-v1"
REQUIRED_CODE = ("scripts/prepare-relative-repair-forward-paths.py",
                 "scripts/load-market-context-screen.py")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local(name):
    require(type(name) is str and bool(name), "Expected repository-relative file path")
    candidate = Path(name)
    require(not candidate.is_absolute() and ".." not in candidate.parts,
            "Pinned paths must be repository relative without parent traversal")
    path = (ROOT / candidate).resolve()
    require(path.is_relative_to(ROOT.resolve()), "Pinned path leaves repository")
    return path


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


def load_module(filename):
    spec = importlib.util.spec_from_file_location("_forward_path_frozen_source", ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_pins(plan):
    for category in ("inputPins", "codePins"):
        require(type(plan.get(category)) is dict and bool(plan[category]), f"Missing {category}")
        for name, expected in plan[category].items():
            require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
                    f"Invalid pinned SHA-256: {name}")
            require(sha(local(name)) == expected, f"Pinned file changed: {name}")
    require(set(REQUIRED_CODE) <= set(plan["codePins"]), "Required preparation/loader code pins missing")


def check_signals(signals, calendar=None, cutoff=None):
    require(type(signals) is list, "Signals must be a list")
    days = None if calendar is None else set(calendar)
    ids = []
    for signal in signals:
        require(type(signal) is dict, "Signal must be an object")
        for key in ("signalId", "symbol", "signalDate"):
            require(type(signal.get(key)) is str and bool(signal[key]), f"Missing signal field: {key}")
        day = signal["signalDate"]
        require(date.fromisoformat(day).isoformat() == day, "Invalid signal date")
        require(days is None or day in days, "Signal date absent from source calendar")
        require(cutoff is None or day <= cutoff, "Signal lies beyond period cutoff")
        ids.append(signal["signalId"])
    require(len(set(ids)) == len(ids), "Duplicate signal IDs")
    return ids


def check_context_identity(context_rows, signals):
    require(type(context_rows) is list and len(context_rows) == len(signals), "Context row count differs")
    require(check_signals(context_rows) == check_signals(signals), "Context signal order differs")
    for row, signal in zip(context_rows, signals):
        require(all(row.get(key) == value for key, value in signal.items()), "Context original signal identity differs")
    # Additional context fields, including any account outcomes, are never used.


def source_bars(raw, signals, cutoff):
    calendar = raw["calendar"]
    require(type(calendar) is list and calendar == sorted(set(calendar)), "Invalid source calendar order")
    require(all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in calendar),
            "Invalid source calendar dates")
    require(cutoff in calendar, "Original period cutoff is absent from source calendar")
    check_signals(signals, calendar, cutoff)
    require(type(raw.get("fingerprint")) is str and bool(raw["fingerprint"]), "Missing source fingerprint")
    needed = {signal["symbol"] for signal in signals}
    indexed = {}
    calendar_set = set(calendar)
    for security in raw["securities"]:
        symbol = security["symbol"]
        if symbol not in needed:
            continue
        require(symbol not in indexed, "Duplicate source security")
        bars = security["bars"]
        dates = [bar["date"] for bar in bars]
        require(dates == sorted(set(dates)), "Duplicate or unordered source bars")
        require(set(dates) <= calendar_set, "Source bar date absent from source calendar")
        indexed[symbol] = {bar["date"]: bar for bar in bars}
    require(set(indexed) == needed, "Signal security absent from frozen source")
    return indexed


def valid_close(bar):
    if type(bar) is not dict:
        return False
    value = bar.get("close")
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def collect_forward(raw, signals, cutoff):
    bars = source_bars(raw, signals, cutoff)
    calendar = raw["calendar"]
    index = {day: position for position, day in enumerate(calendar)}
    cutoff_index = index[cutoff]
    rows = []
    for signal in signals:
        position = index[signal["signalDate"]]
        dates = calendar[position + 1:min(position + 1 + MAX_HORIZON, cutoff_index + 1)]
        security_bars = bars[signal["symbol"]]
        window = [deepcopy(security_bars.get(day)) for day in dates]
        rows.append({"signalId": signal["signalId"], "symbol": signal["symbol"],
                     "signalDate": signal["signalDate"],
                     "referenceBar": deepcopy(security_bars.get(signal["signalDate"])),
                     "windowDates": dates,
                     "windowBars": window, "windowSha256": digest(window),
                     "availableSessions": len(dates),
                     "sourceDatasetFingerprint": raw["fingerprint"]})
    return {"schema": "relative-repair-forward-bars-v1", "sourceCalendar": deepcopy(calendar),
            "sourceCalendarSha256": digest(calendar), "sourceDatasetFingerprint": raw["fingerprint"],
            "periodEnd": cutoff, "horizons": list(HORIZONS), "maxHorizon": MAX_HORIZON,
            "meaning": "Outcome evidence only: unadjusted raw T reference and T+1..T+60 source-calendar slots, clipped at original cutoff.",
            "rows": rows}


def verify_saved(value, signals, raw, cutoff):
    bars = source_bars(raw, signals, cutoff)
    calendar = raw["calendar"]
    require(value.get("schema") == "relative-repair-forward-bars-v1", "Unknown forward evidence schema")
    require(value["sourceCalendar"] == calendar and value["sourceCalendarSha256"] == digest(calendar),
            "Saved source calendar differs")
    require(value["sourceDatasetFingerprint"] == raw["fingerprint"] and value["periodEnd"] == cutoff,
            "Saved source or cutoff differs")
    require(value["horizons"] == HORIZONS and value["maxHorizon"] == MAX_HORIZON,
            "Saved observation horizons differ")
    require(type(value["rows"]) is list and len(value["rows"]) == len(signals), "Forward row count differs")
    require([row["signalId"] for row in value["rows"]] == check_signals(signals), "Forward signal order differs")
    counts = {"signalCount": len(signals), "referencePresent": 0, "referenceMissing": 0,
              "referenceInvalidClose": 0, "complete60SessionWindows": 0, "truncatedWindows": 0,
              "emptyWindows": 0, "availableCalendarSlots": 0, "missingForwardBars": 0,
              "invalidForwardClose": 0, "windowsWithMissingBars": 0,
              "horizonCalendarCoverage": {str(h): {"sufficient": 0, "insufficient": 0} for h in HORIZONS}}
    for signal, row in zip(signals, value["rows"]):
        require(all(row[key] == signal[key] for key in ("signalId", "symbol", "signalDate")),
                "Saved signal identity differs")
        require(row["sourceDatasetFingerprint"] == raw["fingerprint"], "Saved row source differs")
        # Independently enumerate chronological eligible dates rather than reuse the collector slice.
        future_dates = [day for day in calendar if signal["signalDate"] < day <= cutoff]
        expected_dates = future_dates[:MAX_HORIZON]
        require(row["windowDates"] == expected_dates, "Forward dates compressed, include T, or cross cutoff")
        require(type(row["availableSessions"]) is int and row["availableSessions"] == len(expected_dates),
                "Available-session count differs from calendar slots")
        expected_reference = bars[signal["symbol"]].get(signal["signalDate"])
        require(row["referenceBar"] == expected_reference, "Saved reference is not original T bar")
        window = row["windowBars"]
        require(type(window) is list and len(window) == len(expected_dates), "Missing aligned forward slots")
        require(row["windowSha256"] == digest(window), "Saved forward window hash differs")
        for day, bar in zip(expected_dates, window):
            require(bar == bars[signal["symbol"]].get(day), "Saved forward bar differs from original source")
            require(bar is None or bar["date"] == day, "Saved forward bar date differs")
        counts["referenceMissing" if expected_reference is None else "referencePresent"] += 1
        counts["referenceInvalidClose"] += expected_reference is not None and not valid_close(expected_reference)
        counts["complete60SessionWindows" if len(window) == MAX_HORIZON else "truncatedWindows"] += 1
        counts["emptyWindows"] += not window
        counts["availableCalendarSlots"] += len(window)
        missing = sum(bar is None for bar in window)
        counts["missingForwardBars"] += missing
        counts["invalidForwardClose"] += sum(bar is not None and not valid_close(bar) for bar in window)
        counts["windowsWithMissingBars"] += missing > 0
        for horizon in HORIZONS:
            counts["horizonCalendarCoverage"][str(horizon)]["sufficient" if len(window) >= horizon else "insufficient"] += 1
    return counts


def prepare(plan_path):
    plan_path = plan_path.resolve()
    require(plan_path.is_relative_to(ROOT.resolve()), "Plan must be inside repository")
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    verify_pins(plan)
    require(plan.get("horizons") == HORIZONS and all(type(h) is int for h in plan["horizons"])
            and type(plan.get("maxHorizon")) is int and plan["maxHorizon"] == MAX_HORIZON,
            "Plan must freeze horizons 5/10/20/60 and maximum 60")
    require(type(plan.get("periods")) is dict and set(plan["periods"]) == set(PERIODS),
            "Plan must bind both original periods")
    schedules, identities = {}, {}
    for period, fixed in PERIODS.items():
        specification = plan["periods"][period]
        required_paths = {"signalsPath": f"{COST_ROOT}/{period}/signals.json",
                          "sourcePath": f"{COST_ROOT}/{period}/source.json",
                          "contextRowsPath": f"{CONTEXT_ROOT}/{period}/context-rows.json.gz"}
        for field, expected_path in required_paths.items():
            require(specification.get(field) == expected_path and expected_path in plan["inputPins"],
                    f"Missing or substituted frozen {field}")
        require(type(specification.get("signalCount")) is int
                and specification["signalCount"] == fixed["signalCount"], "Frozen opportunity count differs")
        require(specification.get("cutoff") == fixed["end"], "Plan cutoff differs from original period")
        signals = read(local(specification["signalsPath"]))
        check_signals(signals)
        require(len(signals) == fixed["signalCount"]
                and all(fixed["start"] <= signal["signalDate"] <= fixed["end"] for signal in signals),
                "Signals differ from original period/count")
        check_context_identity(read(local(specification["contextRowsPath"])), signals)
        schedules[period] = signals
        identities[period] = read(local(specification["sourcePath"]))
        for filename in ("forward-bars.json.gz", "forward-binding.json"):
            require(not (plan_path.parent / period / filename).exists(),
                    f"Refusing to overwrite existing {period}/{filename}")
    loader = load_module("load-market-context-screen.py")
    for period, fixed in PERIODS.items():
        print(json.dumps({"phase": "forward-source-load", "period": period}), flush=True)
        raw, account_data, identity, fee_policy = loader.load_account_source(period)
        require(identity == identities[period], "Loaded source identity differs from original cost-stress source")
        del account_data, fee_policy
        evidence = collect_forward(raw, schedules[period], fixed["end"])
        verify_saved(evidence, schedules[period], raw, fixed["end"])
        require(plan_path.read_bytes() == plan_bytes, "Frozen plan changed during preparation")
        verify_pins(plan)
        directory = plan_path.parent / period
        directory.mkdir(exist_ok=True)
        output_path = directory / "forward-bars.json.gz"
        write_exclusive(output_path, evidence)
        saved = read(output_path)
        require(saved == evidence, "Saved forward evidence changed during serialization")
        counts = verify_saved(saved, schedules[period], raw, fixed["end"])
        specification = plan["periods"][period]
        binding = {"schema": "relative-repair-forward-binding-v1", "period": period,
                   "preparedAt": datetime.now(timezone.utc).isoformat(),
                   "plan": plan_path.relative_to(ROOT).as_posix(), "planSha256": plan_sha,
                   "inputPins": plan["inputPins"], "codePins": plan["codePins"],
                   "sourceIdentity": identity, "sourceDatasetFingerprint": raw["fingerprint"],
                   "sourceCalendarSha256": saved["sourceCalendarSha256"], "periodEnd": fixed["end"],
                   "signalCount": len(schedules[period]), "signalScheduleSha256": digest(schedules[period]),
                   "signalFileSha256": plan["inputPins"][specification["signalsPath"]],
                   "sourceIdentityFileSha256": plan["inputPins"][specification["sourcePath"]],
                   "contextRowsFileSha256": plan["inputPins"][specification["contextRowsPath"]],
                   "output": {"path": output_path.relative_to(ROOT).as_posix(), "sha256": sha(output_path)},
                   "counts": counts,
                   "verification": {"passed": True, "savedBarsComparedWithSource": True,
                                    "allSignalsRetainedInOrder": True, "contextSignalIdentityMatched": True,
                                    "calendarSlotsNotCompressed": True, "referenceUsesSignalDay": True,
                                    "windowsExcludeSignalDay": True, "windowsRespectOriginalCutoff": True,
                                    "calendarCoverageCountsIncludeMissingBars": True},
                   "scope": "Raw outcome evidence only; not executable returns, not signal-time features.",
                   "invalidCountDefinition": "A present bar has invalidClose only if close is not a finite positive number; OHLC order and volume are not validity conditions for these close-only paths.",
                   "truncationMeaning": "Insufficient observation length; never imputed zero return or extended history.",
                   "accountsExecuted": 0, "returnsCalculated": False, "outcomeFiltering": False, "certified": False}
        write_exclusive(directory / "forward-binding.json", binding)
        print(json.dumps({"phase": "forward-period-bound", "period": period, "counts": counts,
                          "outputSha256": binding["output"]["sha256"]}), flush=True)
        del raw, evidence, saved
    require(plan_path.read_bytes() == plan_bytes, "Frozen plan changed during preparation")
    verify_pins(plan)


def self_test():
    days = [(date(2023, 10, 20) + timedelta(days=i)).isoformat() for i in range(75)]
    bars = [{"date": day, "open": 100. + i, "high": 101. + i, "low": 99. + i,
             "close": 100. + i, "volume": 1000. + i, "extraRawField": i} for i, day in enumerate(days)]
    raw = {"calendar": days, "fingerprint": "synthetic-only", "securities": [{"symbol": "TEST", "bars": bars}]}
    cutoff = days[70]  # 2023-12-29; source deliberately continues into the next year.
    signals = [{"signalId": identifier, "symbol": "TEST", "signalDate": days[index]}
               for identifier, index in (("z-full", 5), ("a-short", 65), ("m-empty", 70))]
    cases = []

    def calculate(value, schedule=signals):
        result = collect_forward(value, schedule, cutoff)
        saved = json.loads(json.dumps(result, allow_nan=False))
        counts = verify_saved(saved, schedule, value, cutoff)
        return saved, counts

    reference, counts = calculate(raw)
    require([row["signalId"] for row in reference["rows"]] == [row["signalId"] for row in signals],
            "Original nonlexicographic order changed")
    require([row["availableSessions"] for row in reference["rows"]] == [60, 5, 0], "Expected 60/5/0 calendar slots")
    require(reference["rows"][0]["windowDates"][0] == days[6]
            and reference["rows"][0]["windowDates"][-1] == days[65]
            and reference["rows"][1]["windowDates"][-1] == "2023-12-29", "Wrong T+1 or fixed cutoff")
    require(counts["horizonCalendarCoverage"]["5"] == {"sufficient": 2, "insufficient": 1}
            and counts["horizonCalendarCoverage"]["10"] == {"sufficient": 1, "insufficient": 2},
            "Observation insufficiency confused with complete window")
    cases.append("original-order; T+1-start; 60-slot-cap; fixed-period-cutoff; 60/5/0-lengths")
    changed = deepcopy(raw)
    del changed["securities"][0]["bars"][8]
    missing, missing_counts = calculate(changed)
    require(missing["rows"][0]["windowBars"][2] is None
            and missing["rows"][0]["availableSessions"] == 60
            and missing["rows"][0]["windowDates"] == reference["rows"][0]["windowDates"]
            and missing_counts["missingForwardBars"] == 1, "Missing future bar compressed or filled")
    cases.append("missing-forward-bar-preserves-null-calendar-slot-and-available-length")
    changed = deepcopy(raw)
    del changed["securities"][0]["bars"][5]
    missing_reference, _ = calculate(changed)
    require(missing_reference["rows"][0]["referenceBar"] is None
            and missing_reference["rows"][0]["windowBars"] == reference["rows"][0]["windowBars"],
            "Missing T reference replaced or forward evidence filtered")
    cases.append("missing-T-reference-retained-with-full-forward-evidence")
    changed = deepcopy(raw)
    changed["securities"][0]["bars"][8]["volume"] = 0
    invalid, invalid_counts = calculate(changed)
    require(invalid["rows"][0]["windowBars"][2]["volume"] == 0
            and invalid_counts["invalidForwardClose"] == 0, "Volume incorrectly invalidated close-only evidence")
    changed["securities"][0]["bars"][8]["close"] = 0
    invalid, invalid_counts = calculate(changed)
    require(invalid["rows"][0]["windowBars"][2]["close"] == 0
            and invalid_counts["invalidForwardClose"] == 1, "Invalid close was altered, discarded, or missed")
    cases.append("raw-bars-preserved; close-only-invalid-counts-do-not-impose-volume-or-OHLC-conditions")
    changed = deepcopy(raw)
    for bar in changed["securities"][0]["bars"][71:]:
        bar["close"] = 999999.
    require(calculate(changed)[0] == reference, "Post-cutoff bars leaked into saved windows")
    changed = deepcopy(raw)
    changed["securities"][0]["bars"][66]["close"] = 999999.
    require(calculate(changed)[0]["rows"][0] == reference["rows"][0], "T+61 bar leaked into full window")
    cases.append("post-cutoff-and-T+61-bar-mutations-do-not-affect-bounded-window")
    context = [{**signal, "outcomes": {"ignored": "arbitrary"}} for signal in signals]
    check_context_identity(context, signals)
    context[0]["outcomes"] = {"ignored": [-999999., 999999.]}
    check_context_identity(context, signals)
    cases.append("context-identity-binding-independent-of-outcome-fields")
    require(calculate(raw, [])[0]["rows"] == [], "Empty schedule not retained")
    try:
        calculate(raw, signals + [signals[0]])
    except ValueError as error:
        require("Duplicate signal IDs" in str(error), "Unexpected duplicate rejection")
    else:
        raise ValueError("Duplicate signal accepted")
    cases.append("empty-schedule-preserved-and-duplicate-signals-rejected")
    for mutation in ("includeT", "dropSlot", "reference", "dropSignal", "extendCutoff", "reorder"):
        damaged = deepcopy(reference)
        if mutation == "includeT":
            damaged["rows"][0]["windowDates"][0] = days[5]
        elif mutation == "dropSlot":
            damaged["rows"][0]["windowBars"].pop(2)
        elif mutation == "reference":
            damaged["rows"][0]["referenceBar"] = deepcopy(bars[4])
        elif mutation == "dropSignal":
            damaged["rows"].pop()
        elif mutation == "extendCutoff":
            damaged["rows"][1]["windowDates"].append(days[71])
        else:
            damaged["rows"].reverse()
        try:
            verify_saved(damaged, signals, raw, cutoff)
        except ValueError:
            pass
        else:
            raise ValueError(f"Verifier accepted corrupted {mutation}")
    cases.append("verifier-rejects-T-inclusion-gap-compression-wrong-reference-drop-cutoff-extension-reorder")
    print(json.dumps({"passed": True, "caseGroups": len(cases), "selfTests": cases,
                      "realSourceLoaded": False, "accountsExecuted": 0, "returnsCalculated": False,
                      "scriptSha256": sha(Path(__file__).resolve())}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--plan", type=Path)
    selection.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        self_test()
    else:
        prepare(arguments.plan)


if __name__ == "__main__":
    main()
