"""Read the frozen supplemental source once; make an independent account view.

The original snapshot remains the input for signal calculation. The account
view retains only the independently checked 2023-2025 calendar and its raw
observations. No signal, fee, position, trade or account is computed here.
"""
from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_engine import _digest

VERSION = "supplemental-account-source-view-v1"
LOADER_PATH = ROOT / "scripts/run-signal-supplemental-persistent-leader.py"
WARMUP_START = "2023-01-03"
TARGET_START = "2024-01-02"
CUTOFF = "2025-12-31"
_LOADER = None


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _day(value):
    _require(type(value) is str and date.fromisoformat(value).isoformat() == value,
             "Expected a canonical calendar date")
    return value


def _loader():
    global _LOADER
    if _LOADER is None:
        spec = importlib.util.spec_from_file_location("_supplemental_account_frozen_source", LOADER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADER = module
    return _LOADER


def _saved_calendar(binding):
    """Validate the saved notice-derived calendar, not the observed prefix."""
    manifest = binding["manifest"]
    document = manifest["registration"]
    spec, sample = document["calendar"], document["sample"]
    path = (ROOT / spec["path"]).resolve()
    _require(path.is_relative_to(ROOT.resolve()) and _sha(path) == spec["sha256"],
             "Supplemental saved calendar changed")
    saved = _read(path)
    _require(saved.get("schema") == spec.get("schema") == "exchange-notice-derived-calendar-v1",
             "Expected the supplemental notice-derived calendar")
    _require(saved["start"] == spec["start"] == sample["warmupStart"] == "2023-01-01"
             and saved["end"] == spec["end"] == sample["signalEnd"] == CUTOFF,
             "Supplemental calendar bounds differ")
    days = saved["dates"]
    _require(isinstance(days, list) and days == sorted(set(days)) and days,
             "Calendar must be ordered, unique and nonempty")
    for day in days:
        _require(saved["start"] <= _day(day) <= saved["end"], "Out-of-bound calendar date")
    yearly = {year: sum(d.startswith(year) for d in days) for year in ("2023", "2024", "2025")}
    _require(yearly == saved["sessionsByYear"] == {"2023": 242, "2024": 242, "2025": 243}
             and len(days) == 727 and days[0] == WARMUP_START and days[-1] == CUTOFF,
             "Supplemental calendar session counts differ")
    ranges = []
    for year, entries in saved["closureRanges"].items():
        for left, right in entries:
            _require(_day(left) <= _day(right) and left[:4] == right[:4] == year,
                     "Invalid saved exchange closure")
            ranges.append((left, right))
    first, last = date.fromisoformat(saved["start"]), date.fromisoformat(saved["end"])
    rebuilt = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        if day.weekday() < 5 and not any(a <= day.isoformat() <= b for a, b in ranges):
            rebuilt.append(day.isoformat())
    _require(days == rebuilt, "Saved calendar and closure arithmetic differ")
    inputs = manifest["inputs"]
    _require(days == inputs["calendar"]["dates"] and _digest(days) == inputs["calendar"]["sha256"],
             "Saved independent calendar differs from source panel")
    return list(days), {"path": spec["path"], "sha256": spec["sha256"], "schema": saved["schema"],
        "start": saved["start"], "end": saved["end"], "sessionsByYear": yearly,
        "calendarHash": _digest(days), "originalSource": inputs["calendar"]["originalSource"],
        "meaning": "Saved official-notice-derived date sequence; no new external verification."}


def load_source(progress=None):
    """Return (raw, binding, document); call once, then share these inputs.

    This preserves the existing explicit sample and cutoff loader. Binding
    physically hashes its frozen raw source and feature evidence; it does not
    recalculate features or run the old persistent-leader strategy.
    ``progress``, when supplied, receives phase dictionaries.
    """
    if progress is not None:
        _require(callable(progress), "progress must be callable")
    notify = progress or (lambda _row: None)
    loader = _loader()
    notify({"phase": "supplemental-source-binding"})
    document = loader.registration()
    binding = loader.verify_panel_binding(document)
    _saved_calendar(binding)
    sample = document["sample"]
    _require(sample["signalStart"] == "2024-01-01" and sample["signalEnd"] == CUTOFF,
             "The supplemental signal interval changed")
    args = Namespace(workspace=loader.SOURCE_WORKSPACE, start=sample["signalStart"], end=sample["signalEnd"])
    notify({"phase": "supplemental-raw-snapshot", "workspace": str(args.workspace)})
    raw = loader._load_dataset(args, document, binding)
    _require(not any(s.get("corporateActions") for s in raw["securities"]),
             "Declared action instructions require a separate reviewed adapter; no stocks are dropped")
    notify({"phase": "supplemental-source-ready", "securities": len(raw["securities"]),
            "bars": sum(len(s["bars"]) for s in raw["securities"]), "fingerprint": raw["fingerprint"]})
    return raw, binding, document


def _date_map(value, allowed):
    _require(isinstance(value, dict), "Expected a dated evidence mapping")
    for day in value:
        _day(day)
    return {day: deepcopy(item) for day, item in value.items() if day in allowed}


def account_view(raw, binding):
    """Make a 2023-warmup/2024-2025 execution view without altering ``raw``.

    Container lists/maps are new. Raw bar dictionaries are shared read-only;
    FixedSignalAccount consumes them without mutation. Its frozen schedule
    replaces indicator scoring, so old forceSeed/historyWindow are omitted.
    Empty securities fail instead of being silently removed.
    """
    inputs = binding["manifest"]["inputs"]
    source_digest = _digest(raw)
    _require(source_digest == inputs["fullDatasetContentSha256"]
             and raw.get("fingerprint") == inputs["snapshotFingerprint"],
             "Original raw snapshot content or fingerprint changed")
    days, evidence = _saved_calendar(binding)
    _require(raw["calendar"] == sorted(set(raw["calendar"]))
             and [d for d in raw["calendar"] if days[0] <= d <= days[-1]] == days,
             "Raw calendar differs from the independently checked account interval")
    symbols = sorted(s["symbol"] for s in raw["securities"])
    _require(symbols == inputs["symbols"] and len(symbols) == len(set(symbols)),
             "Account sample cannot omit or replace securities")
    allowed = set(days)

    def trim_security(security):
        _require(not security.get("corporateActions"),
                 "Declared actions are unsupported in this account view")
        result = {}
        for key, value in security.items():
            if key in ("forceSeed", "historyWindow"):
                continue
            if key == "bars":
                result[key] = [bar for bar in value if days[0] <= bar["date"] <= days[-1]]
                _require(result[key] and all(bar["date"] in allowed for bar in result[key]),
                         "Empty account history or raw bar outside saved calendar")
            elif key.endswith("ByDate") or key in ("stHistory", "memberHistory"):
                result[key] = _date_map(value, allowed)
            else:
                result[key] = deepcopy(value)
        return result

    securities = [trim_security(s) for s in raw["securities"]]
    benchmark = trim_security(raw["benchmark"])
    _require([b["date"] for b in benchmark["bars"]] == days,
             "Benchmark must cover every saved account calendar date")
    view = {}
    for key, value in raw.items():
        if key in ("securities", "benchmark", "calendar", "fingerprint"):
            continue
        view[key] = _date_map(value, allowed) if key.endswith("ByDate") else deepcopy(value)
    view.update(securities=securities, benchmark=benchmark, calendar=days,
        startDate=TARGET_START, endDate=CUTOFF,
        calendarSource="independently-verified-saved-exchange-notice-derived-calendar",
        calendarEvidence=evidence, sourceDatasetDigest=source_digest,
        sourceDatasetFingerprint=raw["fingerprint"])
    if isinstance(view.get("historyCoverage"), dict):
        coverage = view["historyCoverage"]
        if "startDate" in coverage:
            coverage["startDate"] = max(days[0], coverage["startDate"])
        if "endDate" in coverage:
            coverage["endDate"] = min(days[-1], coverage["endDate"])
    count = sum(len(s["bars"]) for s in securities)
    # Preserve original coverage as provenance, rather than silently retaining
    # the full-prefix bar count as the cropped account-view bar count.
    view["sourceUniverseCoverage"] = deepcopy(raw.get("universeCoverage"))
    if isinstance(view.get("universeCoverage"), dict):
        view["universeCoverage"].update(barCount=count)
    source_pins = deepcopy(binding["manifest"].get("physicalSourceHashes", {}))
    transform = {"version": VERSION, "sourceDatasetDigest": source_digest,
        "sourceDatasetFingerprint": raw["fingerprint"], "calendarEvidence": evidence,
        "sourcePhysicalFileHashes": source_pins, "symbolsSha256": _digest(symbols),
        "sourceRetainedBarCount": sum(len(s["bars"]) for s in raw["securities"]),
        "accountRetainedBarCount": count, "accountWarmupStart": days[0],
        "accountStartDate": TARGET_START, "accountEndDate": CUTOFF,
        "sourceOnlyFieldsOmitted": ["forceSeed", "historyWindow"],
        "sourceImmutable": True, "rawPriceAdjustment": "none", "certified": False}
    view["accountView"] = deepcopy(transform)
    view["fingerprint"] = _digest(view)
    identity = {**transform, "accountSnapshotFingerprint": view["fingerprint"],
        "accountDatasetDigest": _digest(view), "sourceBindingIdentity": deepcopy(binding.get("identity", {}))}
    return view, identity
