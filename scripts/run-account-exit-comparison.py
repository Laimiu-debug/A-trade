"""Registered outcome-first comparison of three independently funded accounts.

This runner consumes the entire frozen pre-active opportunity stream. It never
recomputes signals or uses old first-hit events to select account opportunities.
Freeze is read-only with respect to source data; an explicit later run creates
one attempt. A failed/interrupted attempt is retained and cannot auto-restart.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_config import normalize_config
from service.research_fixed_signal_account import FixedSignalAccount, DatedFeePolicy

REGISTRATION = ROOT / "docs/account-exit-comparison-v1.json"
REGISTRATION_SHA256 = "1406190246a20ccb823b129f471bcd5c16ede59b78c56fe62947ff4c4f4cb21e"
SOURCE_RUNNER = "scripts/run-signal-early-strength-emergence.py"
SOURCE_ID = "control_rawEarlyStrengthEmergence"
POLICY_IDS = ("hold", "trail10", "stop5trail10")
EXITS = ((0., 0.), (0., .10), (.05, .10))
DEFAULT_OUTPUT = ROOT / "artifacts/strategy-search/account-exit-comparison-v1"
NEW_FILES = ("scripts/run-account-exit-comparison.py", "tests/test_account_exit_comparison_runner.py",
             "service/research_account_outcomes.py", "tests/test_research_account_outcomes.py",
             "scripts/verify-account-exit-comparison.py", "tests/test_account_exit_comparison_verifier.py")
BASE_FILES = {"opportunities": "opportunities.jsonl.gz", "result": "result.json.gz",
              "summary": "summary.json", "attempt": "attempt.json"}
ATTEMPT_FILES = {**BASE_FILES, **{policy + key.title(): policy + "." + filename
                 for policy in POLICY_IDS for key, filename in
                 (("result", "result.json.gz"), ("checkpoint", "checkpoint.json.gz"), ("summary", "summary.json"))}}
RUNTIME_SOURCE_GUARD = {"requiresNoDeclaredCorporateActionInstructions": True,
    "reason": "v1 pending-entry/action invalidation seam not validated; actual rights may exist but be missing",
    "verifiedSourceAttachmentPath": "local snapshot without history cache"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    result = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).iterencode(value):
        result.update(chunk.encode("utf-8"))
    return result.hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def parse(raw):
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: " + value)))


def read_json(path):
    return parse(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        stream.write("\n")


def write_gzip(path, value):
    with gzip.open(path, "xt", encoding="utf-8", compresslevel=1) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            require(bool(line.strip()), "Empty record in frozen stream")
            yield parse(line)


def same(left, right):
    return digest(left) == digest(right)


def local(name):
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT.resolve()) and path.is_file() and not path.is_symlink(), "Escaping or missing bound file: " + str(name))
    return path


def _now():
    return datetime.now(timezone.utc).isoformat()


def private_source_runner():
    # Only metadata/raw-loading functions are called on this isolated module.
    spec = importlib.util.spec_from_file_location("_account_exit_frozen_source_loader", ROOT / SOURCE_RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source_runner = private_source_runner()
_LOADED_SHA = sha(__file__)
_LOADED_SOURCE_SHA = sha(source_runner.__file__)


def registration():
    require(REGISTRATION_SHA256 is not None and sha(REGISTRATION) == REGISTRATION_SHA256, "Registration is not frozen or changed")
    doc = read_json(REGISTRATION)
    require(doc.get("studyId") == "account-exit-comparison-v1" and doc.get("certified") is False and
            doc.get("targetAchieved") is False and doc.get("registeredAt"), "Study identity/flags differ")
    require(doc["sourceStudy"]["strategyId"] == SOURCE_ID and tuple(row["id"] for row in doc["policies"]) == POLICY_IDS,
            "Registered source/policy order differs")
    require(doc["account"]["independentCashPerPolicy"] is True and doc["account"]["riskNormalizedSizing"] is False and
            doc["execution"]["noFixedTakeProfit"] is True and doc["execution"]["noForcedCutoffSale"] is True,
            "Registered account/execution policy differs")
    for key in ("minimumMeanWinnerPct", "minimumWinRatePct", "minimumPayoffRatio", "mandatoryNetTargetPct"):
        require(key in doc["evaluation"] and doc["evaluation"][key] is None, "No profitability hurdle is allowed")
    require(doc["evaluation"]["automaticWinnerSelection"] is False, "Automatic winner selection is forbidden")
    base = doc["baseConfig"]
    require(same(base, normalize_config(base)) and base["allocation"] == {"mode": "single", "strategyIds": [SOURCE_ID]} and
            len(base["strategies"]) == 1 and base["strategies"][0]["id"] == SOURCE_ID and
            base["startDate"] == doc["sample"]["startDate"] and base["endDate"] == doc["sample"]["endDate"] and
            base["rebalanceMode"] == "risk" and base["rebalanceEvery"] == 1 and base["topN"] == 5,
            "Normalized explicit account config differs")
    require(all(base[key] == doc["account"][key] for key in ("initialCapital", "maxPositions", "maxStockWeight")) and
            type(doc["account"]["entryWeight"]) in (int, float) and 0 < doc["account"]["entryWeight"] <= 1,
            "Account capital/limits differ")
    for policy, (stop, trail) in zip(doc["policies"], EXITS):
        expected = {**base, "stopLossPct": stop, "trailingStopPct": trail, "takeProfitPct": 0., "maxHoldingDays": 0}
        require(same(policy["config"], expected), "Unexpected policy/config variation")
    fee = doc["feePolicy"]
    require(fee["profileId"] == "dated-transfer-assumption-v1" and fee["commissionIncludesTransfer"] is False and
            fee["stampDutyOverride"] is None and fee["actualBrokerChargesVerified"] is False and
            fee["minimumCommissionUnit"] == "each-simulated-fill", "Fee profile differs")
    policy = fee_policy(doc)
    for settings in doc["policies"]:
        policy.settings(settings["config"])
    require(set(doc["reviewedFiles"]) == {*NEW_FILES, REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix()} and
            set(doc["runPrerequisites"]) == {"scopedTests", "prerunReview"}, "Review closure differs")
    return doc


def fee_policy(doc):
    fee = doc["feePolicy"]
    return DatedFeePolicy(commission_rate=fee["commissionRate"], min_commission=fee["minCommission"],
        commission_includes_transfer=fee["commissionIncludesTransfer"], stamp_duty_override=fee["stampDutyOverride"],
        security_type=fee["securityType"], trading_mode=fee["tradingMode"])


def load_binding():
    """Current complete source identities, using only the old read-only loader."""
    doc = registration()
    require(doc["frozenSourceRunner"] == {"path": SOURCE_RUNNER, "sha256": _LOADED_SOURCE_SHA} and
            sha(local(SOURCE_RUNNER)) == _LOADED_SOURCE_SHA, "Frozen source loader changed")
    reference = doc["sourceRegistration"]
    require(sha(local(reference["path"])) == reference["sha256"], "Source registration changed")
    parent = source_runner.load_binding()
    require(same(doc["sourceBinding"], parent["document"]["sourceBinding"]) and
            reference["sha256"] == parent["identity"]["registrationSha256"], "Source panel identity differs")
    sample = doc["sample"]
    require((sample["startDate"], sample["endDate"]) == (parent["dates"][0], parent["dates"][-1]) and
            (sample["sourceSignalStart"], sample["sourceSignalEnd"]) == (parent["sample"]["signalStart"], parent["sample"]["signalEnd"]) and
            sample["warmupStart"] == parent["sample"]["warmupStart"] and
            sample["expectedSecurities"] == len(parent["symbols"]) and sample["expectedStudySessions"] == len(parent["dates"]) and
            sample["expectedSourceCalendarSessions"] == len(parent["calendar"]) and
            sample["snapshotFingerprint"] == parent["identity"]["snapshotFingerprint"] and
            sample["fullDatasetContentSha256"] == parent["identity"]["fullDatasetContentSha256"] and
            sample["expectedBars"] == parent["manifest"]["inputs"]["retainedBarCount"], "Exact account/source population differs")
    source = doc["sourceStudy"]
    directory = (ROOT / source["directory"]).resolve()
    require(directory.is_relative_to(ROOT.resolve()) and directory.is_dir(), "Invalid source study directory")
    attempt_name = source["attemptDirectory"]
    require(attempt_name == "attempts/attempt-0001", "Only the frozen first source attempt is accepted")
    required_files = {"protocol.json", "independent-result-verification.json", attempt_name + "/completion.json",
                      attempt_name + "/selection.jsonl.gz", attempt_name + "/signals.jsonl.gz"}
    require(set(source["files"]) == required_files, "Source proof/signal file closure differs")
    files = dict(parent["files"])
    for name, expected in source["files"].items():
        path = (directory / name).resolve()
        require(path.is_relative_to(directory) and path.is_file() and not path.is_symlink() and sha(path) == expected,
                "Frozen source study file differs: " + name)
        files[str(path)] = expected
    protocol = read_json(directory / "protocol.json")
    completion = read_json(directory / attempt_name / "completion.json")
    audit = read_json(directory / "independent-result-verification.json")
    require(protocol.get("protocolHash") == source["protocolHash"] == digest({k:v for k,v in protocol.items() if k != "protocolHash"}) and
            same(protocol.get("binding"), parent["identity"]) and same(protocol.get("registration"), parent["document"]),
            "Source protocol identity differs")
    require(completion.get("protocolHash") == protocol["protocolHash"] and completion.get("attempt") == 1 and
            completion.get("snapshotFingerprint") == sample["snapshotFingerprint"] and
            completion.get("certified") is False and completion.get("targetAchieved") is False and
            set(completion.get("files", {})) == source_runner.RECEIPT_KEYS, "Source completion identity/closure differs")
    require(audit.get("passed") is True and audit.get("verificationCompleted") is True and
            type(audit.get("failureCount")) is int and audit["failureCount"] == 0 and
            audit.get("protocolHash") == protocol["protocolHash"] and
            audit.get("completionSha256") == source["files"][attempt_name + "/completion.json"] and
            audit.get("registrationSha256") == reference["sha256"] and
            audit.get("snapshotFingerprint") == sample["snapshotFingerprint"] and
            audit.get("targetSessions") == len(parent["dates"]) and same(audit.get("implementation"), protocol["implementation"]) and
            audit.get("certified") is False and audit.get("targetAchieved") is False,
            "Accepted completed source independent audit required")
    for key, name in (("signals", "signals.jsonl.gz"), ("selections", "selection.jsonl.gz")):
        expected_path = attempt_name + "/" + name
        require(completion["files"][key] == {"path": expected_path, "sha256": source["files"][expected_path]},
                "Source signal/selection receipt differs")
    require(completion["files"]["protocol"] == {"path":"protocol.json", "sha256":source["files"]["protocol.json"]},
            "Source protocol receipt differs")
    archive = directory / "execution-sources.zip"
    require(sha(archive) == protocol["executionArchiveSha256"] and
            completion["files"]["executionSources"] == {"path":"execution-sources.zip", "sha256":sha(archive)},
            "Source execution archive differs")
    _check_archive(archive, protocol["implementation"])
    files[str(archive)] = protocol["executionArchiveSha256"]
    for name, expected in {**protocol["implementation"], **doc["frozenImplementation"], **doc["engineeringEvidence"]}.items():
        path = local(name)
        require(sha(path) == expected, "Frozen implementation/evidence changed: " + name)
        files[str(path)] = expected
    files[str(REGISTRATION.resolve())] = REGISTRATION_SHA256
    calendar_evidence = verified_calendar(parent, files)
    identity = {"registrationSha256": REGISTRATION_SHA256, "inputFilesSha256": files,
                "sourceStudyProtocolHash": protocol["protocolHash"], "sourceStudyAuditSha256": source["files"]["independent-result-verification.json"],
                "sourceStudyCompletionSha256": source["files"][attempt_name + "/completion.json"],
                "snapshotFingerprint": sample["snapshotFingerprint"], "fullDatasetContentSha256": sample["fullDatasetContentSha256"],
                "symbols": parent["symbols"], "symbolsSha256": parent["identity"]["symbolsSha256"],
                "calendar": parent["calendar"], "calendarHash": parent["identity"]["calendarHash"],
                "targetDates": parent["dates"], "sourcePanelManifestSha256": parent["identity"]["panelManifestSha256"],
                "calendarEvidence": calendar_evidence}
    return {"document":doc, "parent":parent, "sourceDirectory":directory, "sourceProtocol":protocol,
            "sourceCompletion":completion, "sourceAudit":audit, "identity":identity,
            "workspace":parent["workspace"], "files":files}


def verified_calendar(parent, files):
    """Independently sourced dates; never relabel an unchecked observed union."""
    metadata = parent["manifest"]["inputs"]["calendar"]
    spec = parent["manifest"].get("registration", {}).get("calendar", {})
    require(metadata.get("originalRestrictedDatesEqualSavedExchangeDates") is True and
            same(metadata.get("savedProvider"), spec), "Saved independent calendar evidence missing")
    require(spec.get("provider") == "baostock" and spec.get("api") == "query_trade_dates" and
            spec.get("start") == parent["sample"]["warmupStart"] and spec.get("end") == parent["sample"]["signalEnd"],
            "Independent calendar source or interval differs")
    path = local(spec["path"])
    require(sha(path) == spec["sha256"], "Independent calendar source changed")
    payload = read_json(path)
    require(payload.get("provider") == spec["provider"] and payload.get("api") == spec["api"] and
            payload.get("errorCode") == "0" and payload.get("rowBoundReached") is False and
            payload.get("parameters") == {"start_date":spec["start"], "end_date":spec["end"]} and
            payload.get("fields") == ["calendar_date", "is_trading_day"], "Incomplete independent calendar response")
    first, last = date.fromisoformat(spec["start"]), date.fromisoformat(spec["end"])
    natural = [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    rows = payload.get("rows")
    require(isinstance(rows, list) and all(isinstance(row, list) and len(row) == 2 and row[1] in ("0", "1") for row in rows) and
            [row[0] for row in rows] == natural, "Independent calendar has missing, duplicate or invalid natural dates")
    days = [row[0] for row in rows if row[1] == "1"]
    require(days == parent["calendar"] and digest(days) == parent["identity"]["calendarHash"],
            "Independent calendar differs from frozen source dates")
    files[str(path.resolve())] = spec["sha256"]
    return {"path":spec["path"], "sha256":spec["sha256"], "provider":spec["provider"], "api":spec["api"],
            "start":spec["start"], "end":spec["end"], "calendarHash":digest(days),
            "originalSource":metadata["originalSource"], "originalRestrictedDatesEqualSavedExchangeDates":True}


def account_view(data, binding):
    """A shallow view changes provenance only; all raw source values stay intact."""
    evidence = binding["identity"]["calendarEvidence"]
    source_digest = digest(data)
    require(source_digest == binding["identity"]["fullDatasetContentSha256"] and
            data["calendar"] == binding["identity"]["calendar"] and data.get("calendarSource") == evidence["originalSource"],
            "Original source calendar/content differs before account view")
    identity = {"sourceDatasetDigest":source_digest, "sourceSnapshotFingerprint":data["fingerprint"],
                "calendarEvidence":deepcopy(evidence)}
    fingerprint = digest({"kind":"fixed-signal-account-calendar-view-v1", **identity})
    view = {**data, "calendarSource":"independently-verified-saved-trading-calendar", "calendarEvidence":deepcopy(evidence),
            "sourceDatasetDigest":source_digest, "sourceDatasetFingerprint":data["fingerprint"], "fingerprint":fingerprint}
    identity.update(accountDatasetDigest=digest(view), accountSnapshotFingerprint=fingerprint)
    return view, identity


def load_dataset(binding):
    args = argparse.Namespace(workspace=binding["workspace"])
    data = source_runner.load_dataset(args, binding["parent"])
    source_runner.validate_dataset(data, binding["parent"])
    require(not any(security.get("corporateActions") for security in data["securities"]),
            "Declared action instructions require a separately reviewed adapter version; no stock may be dropped")
    return data


def implementation_hashes(binding):
    require(sha(__file__) == _LOADED_SHA, "Runner changed after import")
    doc = binding["document"]
    frozen = {**binding["sourceProtocol"]["implementation"], **doc["frozenImplementation"]}
    for name, expected in frozen.items():
        require(sha(local(name)) == expected, "Frozen executable dependency changed: " + name)
    names = set(frozen) | set(NEW_FILES) | {REGISTRATION.resolve().relative_to(ROOT.resolve()).as_posix()}
    return {name:sha(local(name)) for name in sorted(names)}


def _archive(path, implementation):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, expected in sorted(implementation.items()):
            raw = local(name).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == expected, "Code drift during archive")
            archive.writestr(name, raw)


def _check_archive(path, implementation):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(implementation) and set(names) == set(implementation), "Archive exact closure differs")
        for name, expected in implementation.items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected, "Archive member differs: " + name)


class ProgressAccount(FixedSignalAccount):
    """Clock-only progress callback; all account state/math is inherited."""
    def _step_day(self, day):
        super()._step_day(day)
        now = time.monotonic()
        if now - getattr(self, "_last_progress", 0) >= 30:
            print(json.dumps({"phase": "account", "policy": getattr(self, "_policy_label", None),
                              "date": day, "completedSessions": self.state["cursor"] + 1,
                              "totalSessions": len(self.dates)}, ensure_ascii=False), flush=True)
            self._last_progress = now


def load_opportunities(binding):
    """Replay complete selection/signal *files*, never old fill/active subsets."""
    parent = binding["parent"]
    directory, receipt = binding["sourceDirectory"], binding["sourceCompletion"]
    selection_path = directory / receipt["files"]["selections"]["path"]
    signals_path = directory / receipt["files"]["signals"]["path"]
    signal_stream = iter(records(signals_path))
    selections = iter(records(selection_path))
    projected, lineage, ids, dates_symbols = [], [], set(), set()
    groups = (source_runner.CANDIDATE_ID, source_runner.CONTROL_ID)
    counts, mask_counts, day_counts = Counter(), Counter(), {}
    for index, day in enumerate(parent["dates"]):
        wrapper = next(selections, None)
        require(isinstance(wrapper, dict) and wrapper.get("date") == day, "Selection date/order/EOF differs")
        source = {"panelManifestSha256": parent["identity"]["panelManifestSha256"],
                  "featureFileSha256": parent["document"]["sourceBinding"]["files"]["features.jsonl.gz"],
                  "calendarHash": parent["identity"]["calendarHash"], "targetIndex": index}
        require(same(wrapper.get("source"), source), "Selection source linkage differs")
        selection = wrapper.get("selection", {})
        require(selection.get("date") == day and selection.get("candidateId") == groups[0] and
                selection.get("controlId") == groups[1], "Selection policy identity differs")
        ranked, slots, signals = (selection.get(key) for key in ("fullRanking", "slots", "signals"))
        require(all(isinstance(value, list) for value in (ranked, slots, signals)), "Selection arrays missing")
        require(len({row.get("symbol") for row in ranked}) == len(ranked), "Duplicate ranking symbol")
        for rank, row in enumerate(ranked, 1):
            require(type(row.get("rank")) is int and row["rank"] == rank and row.get("date") == day and
                    row.get("symbol") in parent["symbols"] and row.get("selectedTop5") is (rank <= 5),
                    "Original full ranking or selectedTop5 differs")
        require(len(slots) == min(5, len(ranked)), "Top5 slots omitted or backfilled")
        for rank, slot in enumerate(slots, 1):
            require(slot.get("baselineAdmitted") is True and type(slot.get("candidateAdmitted")) is bool and
                    same({key: slot.get(key) for key in ranked[rank - 1]}, ranked[rank - 1]),
                    "Slot differs from original ranked opportunity")
        require(all(row.get("strategyId") in groups for row in signals), "Unexpected source signal policy")
        expected_counts = {group: sum(row["strategyId"] == group for row in signals) for group in groups}
        require(same(selection.get("policyCounts"), expected_counts) and expected_counts[groups[1]] == len(slots),
                "Complete baseline opportunity count differs")
        expected_rows = []
        for group in groups:
            for slot in slots:
                admitted = slot["candidateAdmitted"] if group == groups[0] else slot["baselineAdmitted"]
                if not admitted:
                    continue
                expected_rows.append({**slot, "version": parent["document"]["featureVersion"],
                    "strategyId": group, "pairedStrategyId": groups[1] if group == groups[0] else groups[0],
                    "signalDate": day, "eligible": True, "certified": False, "targetAchieved": False})
        require(same(signals, expected_rows), "Selection signals differ from complete admitted Top5 slots")
        for row in signals:
            observed = next(signal_stream, None)
            require(same(observed, row), "Signal stream differs from complete selections or ended early")
            counts[row["strategyId"]] += 1
            if row["strategyId"] != SOURCE_ID:
                continue
            require(row.get("earlyMask") in ("true", "false", "unknown") and
                    isinstance(row.get("episodeId"), str) and bool(row["episodeId"]) and
                    isinstance(row.get("formationDate"), str) and row["formationDate"] < day,
                    "Source lineage/mask missing")
            signal_id = SOURCE_ID + ":" + row["episodeId"] + ":" + day
            new = {"signalId": signal_id, "strategyId": SOURCE_ID, "symbol": row["symbol"],
                   "signalDate": day, "originalRank": row["rank"]}
            require(signal_id not in ids and (day, row["symbol"]) not in dates_symbols,
                    "Duplicate frozen account opportunity")
            ids.add(signal_id); dates_symbols.add((day, row["symbol"]))
            projected.append(new)
            lineage.append({"source": deepcopy(row), "projected": deepcopy(new),
                            "sourceSignalSha256": digest(row), "sourceSelectionSha256": digest(wrapper)})
            mask_counts[row["earlyMask"]] += 1
        day_counts[day] = len(slots)
    require(next(selections, None) is None and next(signal_stream, None) is None, "Trailing source rows beyond frozen population")
    projected.sort(key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"]))
    require([row["projected"] for row in lineage] == projected, "Source signal order is not original rank order")
    require(len(projected) == binding["document"]["sourceStudy"]["expectedSelectedOpportunities"],
            "Registered complete opportunity count differs")
    return projected, lineage, {"targetSessions": len(parent["dates"]), "sourceSignalsByPolicy": dict(counts),
        "opportunityCount": len(projected), "opportunitiesByDate": day_counts,
        "earlyMaskCounts": {key: mask_counts[key] for key in ("true", "false", "unknown")},
        "signalScheduleSha256": digest(projected), "sourceLineageSha256": digest(lineage),
        "sourceStreamMeaning": "complete-pre-active-control-Top5;includes-all-earlyMask-states;not-prior-filled-subset"}


def load_prerequisites(binding):
    doc = binding["document"]
    files = {name:sha(local(name)) for name in doc["reviewedFiles"]}
    paths = {key:local(name) for key,name in doc["runPrerequisites"].items()}
    scoped, review = (read_json(paths[key]) for key in ("scopedTests", "prerunReview"))
    require(scoped.get("schema") == "account-exit-comparison-scoped-tests-v1" and scoped.get("passed") is True and
            type(scoped.get("testsRun")) is int and scoped["testsRun"] > 0 and
            all(type(scoped.get(key)) is int and scoped[key] == 0 for key in ("failures", "errors", "skipped")) and
            isinstance(scoped.get("uniqueTestIds"), list) and
            len(scoped["uniqueTestIds"]) == len(set(scoped["uniqueTestIds"])) == scoped["testsRun"] and
            all(isinstance(item, str) and item for item in scoped["uniqueTestIds"]) and
            scoped.get("sourcesStableDuringTests") is True and same(scoped.get("files"), files) and
            scoped.get("certified") is False and scoped.get("targetAchieved") is False,
            "Current scoped synthetic prerequisite required")
    require(review.get("schema") == "account-exit-comparison-prerun-review-v1" and review.get("passed") is True and
            type(review.get("failureCount")) is int and review["failureCount"] == 0 and same(review.get("files"), files) and
            review.get("scopedTestsSha256") == sha(paths["scopedTests"]) and
            review.get("certified") is False and review.get("targetAchieved") is False,
            "Current pre-run review prerequisite required")
    return {key:{"path":doc["runPrerequisites"][key], "sha256":sha(path)} for key,path in paths.items()}


def _safe_output(output, binding):
    output = Path(output).resolve()
    workspace = binding["workspace"]
    with closing(sqlite3.connect((workspace / "market.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        physical = Path(json.loads(db.execute("SELECT value FROM meta WHERE key='path'").fetchone()[0])).resolve()
    protected = (workspace, physical, binding["parent"]["directory"], binding["sourceDirectory"])
    require(all(not output.is_relative_to(path) and not path.is_relative_to(output) for path in protected),
            "Output overlaps protected source")
    require(all(not Path(name).is_relative_to(output) for name in binding["files"]) and
            all(not local(name).is_relative_to(output) for name in implementation_hashes(binding)),
            "Output contains bound input/code")
    return output


def _inventory(output, completed=False):
    allowed = {".run.lock", "freeze-attempt.json", "protocol.json", "execution-sources.zip", "events.jsonl"}
    if completed:
        allowed.add("attempts")
    for path in output.iterdir():
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "Output links forbidden")
        report = completed and path.is_file() and (path.name == "independent-result-verification.json" or
            path.name.startswith("independent-result-verification-failed-") and path.suffix == ".json")
        require(path.name in allowed or report, "Unexpected output or failed attempt; restart forbidden")
    if completed:
        attempts, attempt = output / "attempts", output / "attempts/attempt-0001"
        require([p.name for p in attempts.iterdir()] == ["attempt-0001"] and
                {p.name for p in attempt.iterdir()} == set(ATTEMPT_FILES.values()) | {"completion.json"},
                "Completed output inventory differs")
        require(all(not p.is_symlink() and not (hasattr(p, "is_junction") and p.is_junction())
                    for p in (attempts, attempt, *attempt.iterdir())), "Attempt links forbidden")


def _verify_protocol(output, binding):
    protocol = read_json(output / "protocol.json")
    require(protocol.get("schema") == "account-exit-comparison-protocol-v1" and
            protocol.get("protocolHash") == digest({k:v for k,v in protocol.items() if k != "protocolHash"}), "Protocol hash/schema differs")
    require(protocol.get("registrationSha256") == REGISTRATION_SHA256 and same(protocol.get("registration"), binding["document"]) and
            same(protocol.get("binding"), binding["identity"]) and protocol.get("policyOrder") == list(POLICY_IDS) and
            protocol.get("sourceStrategyId") == SOURCE_ID and same(protocol.get("runtimeSourceGuard"), RUNTIME_SOURCE_GUARD) and
            same(protocol.get("prerequisites"), load_prerequisites(binding)) and
            protocol.get("certified") is False and protocol.get("targetAchieved") is False,
            "Frozen protocol identity/config/guard differs")
    account = protocol.get("accountDataset", {})
    expected = {"sourceDatasetDigest":binding["identity"]["fullDatasetContentSha256"],
                "sourceSnapshotFingerprint":binding["identity"]["snapshotFingerprint"],
                "calendarEvidence":binding["identity"]["calendarEvidence"]}
    require(set(account) == set(expected) | {"accountDatasetDigest", "accountSnapshotFingerprint"} and
            all(same(account.get(key), value) for key,value in expected.items()) and
            account.get("accountSnapshotFingerprint") == digest({"kind":"fixed-signal-account-calendar-view-v1", **expected}) and
            isinstance(account.get("accountDatasetDigest"), str) and len(account["accountDatasetDigest"]) == 64,
            "Account calendar view identity differs")
    require(protocol.get("implementation") == implementation_hashes(binding) and
            sha(output / "execution-sources.zip") == protocol.get("executionArchiveSha256"), "Executable source/archive drift")
    _check_archive(output / "execution-sources.zip", protocol["implementation"])
    marker = read_json(output / "freeze-attempt.json")
    require(sha(output / "freeze-attempt.json") == protocol.get("freezeAttemptSha256") and
            marker.get("registrationSha256") == REGISTRATION_SHA256 and marker.get("studyId") == "account-exit-comparison-v1",
            "Freeze marker changed")
    return protocol


def _fail(output, phase, error):
    write_json(output / "failure.json", {"phase":phase, "errorType":type(error).__name__, "error":str(error),
                                       "failedAt":_now(), "automaticRetryAllowed":False})


def freeze(args):
    binding = load_binding()
    output = _safe_output(args.output, binding)
    prerequisites = load_prerequisites(binding)
    opportunities, _, accounting = load_opportunities(binding)
    source_data = load_dataset(binding)
    view, account_identity = account_view(source_data, binding)
    del view, source_data
    output.mkdir(parents=True, exist_ok=True)
    with source_runner.source_tools.output_lock(output):
        require({p.name for p in output.iterdir()} == {".run.lock"}, "Exclusive freeze requires a new empty output")
        write_json(output / "freeze-attempt.json", {"studyId":"account-exit-comparison-v1", "startedAt":_now(),
            "registrationSha256":REGISTRATION_SHA256, "certified":False, "targetAchieved":False})
        try:
            implementation = implementation_hashes(binding)
            _archive(output / "execution-sources.zip", implementation)
            protocol = {"schema":"account-exit-comparison-protocol-v1", "createdAt":_now(),
                "registration":binding["document"], "registrationSha256":REGISTRATION_SHA256,
                "binding":binding["identity"], "sourceStrategyId":SOURCE_ID, "policyOrder":list(POLICY_IDS),
                "signalScheduleSha256":digest(opportunities), "sourceAccounting":accounting,
                "accountDataset":account_identity,
                "runtimeSourceGuard":RUNTIME_SOURCE_GUARD, "prerequisites":prerequisites,
                "implementation":implementation, "executionArchiveSha256":sha(output / "execution-sources.zip"),
                "freezeAttemptSha256":sha(output / "freeze-attempt.json"), "pythonVersion":sys.version,
                "certified":False, "targetAchieved":False}
            protocol["protocolHash"] = digest(protocol)
            require(same(load_binding()["identity"], binding["identity"]) and implementation_hashes(binding) == implementation and
                    same(load_prerequisites(binding), prerequisites), "Input/source/prerequisite drift during freeze")
            write_json(output / "protocol.json", protocol)
            return protocol
        except Exception as error:
            _fail(output, "freeze", error)
            raise


def run_accounts(data, opportunities, binding, attempt):
    from service.research_account_outcomes import summarize_account
    doc = binding["document"]
    summaries, runtime = [], {}
    for policy in doc["policies"]:
        started = time.perf_counter()
        print(json.dumps({"phase":"account_start", "policy":policy["id"], "opportunities":len(opportunities)}), flush=True)
        engine = ProgressAccount(data, opportunities, policy["config"], entry_weight=doc["account"]["entryWeight"],
                                 fee_policy=fee_policy(doc))
        engine._policy_label = policy["id"]
        engine.step(20000)
        result = engine.result()
        checkpoint = engine.checkpoint()
        require(result["status"] == "completed" and result["research"]["identity"]["signalScheduleSha256"] == digest(opportunities),
                "Account did not consume the exact frozen schedule")
        summary = summarize_account(result, checkpoint, policy_id=policy["id"], policy_label=policy["title"])
        files = {"result":policy["id"] + ".result.json.gz", "checkpoint":policy["id"] + ".checkpoint.json.gz",
                 "summary":policy["id"] + ".summary.json"}
        write_gzip(attempt / files["result"], result)
        write_gzip(attempt / files["checkpoint"], checkpoint)
        write_json(attempt / files["summary"], summary)
        summaries.append({"policyId":policy["id"], "title":policy["title"], "role":policy["role"],
                          "config":policy["config"], "files":files, "outcomes":summary})
        runtime[policy["id"]] = time.perf_counter() - started
        print(json.dumps({"phase":"account_completed", "policy":policy["id"], "elapsedSeconds":runtime[policy["id"]]}), flush=True)
        del engine, result, checkpoint
    return summaries, runtime


def verify_completion(output, receipt, protocol):
    _inventory(output, completed=True)
    paths = {key:"attempts/attempt-0001/" + value for key,value in ATTEMPT_FILES.items()}
    paths.update(protocol="protocol.json", executionSources="execution-sources.zip")
    require(receipt.get("schema") == "account-exit-comparison-completion-v1" and receipt.get("attempt") == 1 and
            receipt.get("protocolHash") == protocol["protocolHash"] and receipt.get("policyOrder") == list(POLICY_IDS) and
            receipt.get("snapshotFingerprint") == protocol["binding"]["snapshotFingerprint"] and
            receipt.get("signalScheduleSha256") == protocol["signalScheduleSha256"] and
            receipt.get("freezeAttemptSha256") == protocol["freezeAttemptSha256"] and
            same(receipt.get("accountDataset"), protocol["accountDataset"]) and
            type(receipt.get("declaredActionInstructionsCount")) is int and receipt["declaredActionInstructionsCount"] == 0 and
            receipt.get("actualCorporateActionsKnown") is False and receipt.get("certified") is False and
            receipt.get("targetAchieved") is False and set(receipt.get("files", {})) == set(paths), "Completed receipt differs")
    for key, name in paths.items():
        require(receipt["files"][key] == {"path":name, "sha256":sha(output / name)}, "Completed artifact changed; no recompute")
    marker = read_json(output / "attempts/attempt-0001/attempt.json")
    require(marker.get("attempt") == 1 and marker.get("protocolHash") == protocol["protocolHash"] and
            marker.get("studyId") == "account-exit-comparison-v1", "Attempt marker changed")
    journal = [parse(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    require([row.get("event") for row in journal] == ["study_started", "opportunities_verified", "study_succeeded"] and
            all(row.get("protocolHash") == protocol["protocolHash"] for row in journal) and journal[-1].get("receipt") == receipt,
            "Completion journal differs")


def execute(args, *, verify_only=False):
    binding = load_binding()
    output = _safe_output(args.output, binding)
    require((output / "protocol.json").is_file(), "Freeze and review before execution")
    with source_runner.source_tools.output_lock(output):
        protocol = _verify_protocol(output, binding)
        protocol_sha = sha(output / "protocol.json")
        attempt = output / "attempts/attempt-0001"
        if (attempt / "completion.json").is_file():
            verify_completion(output, read_json(attempt / "completion.json"), protocol)
            return 0
        require(not verify_only, "No completed attempt to verify")
        _inventory(output)
        require(not (output / "attempts").exists(), "Failed/interrupted attempt retained; no retry")
        attempt.mkdir(parents=True)
        write_json(attempt / "attempt.json", {"studyId":"account-exit-comparison-v1", "attempt":1,
                   "protocolHash":protocol["protocolHash"], "startedAt":_now(), "certified":False, "targetAchieved":False})
        event = source_runner.source_tools.event
        event(output, protocol["protocolHash"], "study_started", attempt=1)
        try:
            opportunities, lineage, accounting = load_opportunities(binding)
            require(digest(opportunities) == protocol["signalScheduleSha256"] and same(accounting, protocol["sourceAccounting"]),
                    "Opportunity schedule changed after freeze")
            with gzip.open(attempt / "opportunities.jsonl.gz", "xt", encoding="utf-8", compresslevel=1) as stream:
                for row in lineage:
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            event(output, protocol["protocolHash"], "opportunities_verified", **accounting)
            print(json.dumps({"phase":"loading_frozen_raw"}), flush=True)
            data = load_dataset(binding)
            view, account_identity = account_view(data, binding)
            require(same(account_identity, protocol.get("accountDataset")), "Account view changed after freeze")
            summaries, runtime = run_accounts(view, opportunities, binding, attempt)
            result = {"schema":"account-exit-comparison-result-v1", "studyId":"account-exit-comparison-v1",
                "protocolHash":protocol["protocolHash"], "registrationSha256":REGISTRATION_SHA256,
                "snapshotFingerprint":data["fingerprint"], "signalScheduleSha256":protocol["signalScheduleSha256"],
                "sourceAccounting":accounting, "policyOrder":list(POLICY_IDS), "policies":summaries,
                "accountDataset":account_identity,
                "runtimeSourceGuard":RUNTIME_SOURCE_GUARD, "declaredActionInstructionsCount":0,
                "actualCorporateActionsKnown":False, "qualityLimits":binding["document"]["qualityLimits"],
                "automaticWinnerSelection":False, "certified":False, "targetAchieved":False}
            write_gzip(attempt / "result.json.gz", result)
            write_json(attempt / "summary.json", {**result, "performance":{"accountSeconds":runtime}})
            final = load_binding()
            require(same(final["identity"], binding["identity"]) and sha(output / "protocol.json") == protocol_sha,
                    "Post-run source/protocol drift")
            _verify_protocol(output, final)
            source_runner.validate_dataset(data, final["parent"])
            require(digest(view) == account_identity["accountDatasetDigest"], "Account view changed during run")
            paths = {key:"attempts/attempt-0001/" + value for key,value in ATTEMPT_FILES.items()}
            paths.update(protocol="protocol.json", executionSources="execution-sources.zip")
            receipt = {"schema":"account-exit-comparison-completion-v1", "attempt":1, "protocolHash":protocol["protocolHash"],
                "snapshotFingerprint":data["fingerprint"], "policyOrder":list(POLICY_IDS),
                "signalScheduleSha256":protocol["signalScheduleSha256"], "freezeAttemptSha256":protocol["freezeAttemptSha256"],
                "accountDataset":account_identity,
                "declaredActionInstructionsCount":0, "actualCorporateActionsKnown":False, "certified":False, "targetAchieved":False,
                "files":{key:{"path":name,"sha256":sha(output/name)} for key,name in paths.items()}}
            write_json(attempt / "completion.json", receipt)
            event(output, protocol["protocolHash"], "study_succeeded", receipt=receipt)
            return 0
        except Exception as error:
            _fail(output, "execute", error)
            write_json(attempt / "failure.json", {"protocolHash":protocol["protocolHash"], "errorType":type(error).__name__,
                "error":str(error), "failedAt":_now(), "automaticRetryAllowed":False})
            event(output, protocol["protocolHash"], "study_failed", errorType=type(error).__name__, error=str(error))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze-only", action="store_true")
    mode.add_argument("--run-once", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    mode.add_argument("--inspect-inputs", action="store_true")
    args = parser.parse_args(argv)
    if args.inspect_inputs:
        binding = load_binding()
        _, _, accounting = load_opportunities(binding)
        print(json.dumps({"studyId":"account-exit-comparison-v1", "accountCalculationPerformed":False,
                          "binding":binding["identity"], "sourceAccounting":accounting}, ensure_ascii=False))
        return 0
    if args.freeze_only:
        print(json.dumps({"protocolHash":freeze(args)["protocolHash"]}), flush=True)
        return 0
    return execute(args, verify_only=args.verify_only)


if __name__ == "__main__":
    raise SystemExit(main())
