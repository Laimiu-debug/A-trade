"""Prepare ten frozen confirmation-review accounts; run only on explicit command.

Reads the historical projected opportunity universe ending 2026-10-15. All
writes stay below this experiment directory. No user workspace, old registry,
current-market loader, parameter search, or implicit account execution is used.
"""
from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from datetime import date, datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REVIEW = ROOT / "artifacts/strategy-search/profitability-review-20261031"
HYPOTHESIS = REVIEW / "hypothesis-plan.json"
OUT = REVIEW / "experiment"
VERSION = "dual-confirmation-review-runner-v1"
PERIODS = {"development": ("2024-09-24", "2025-12-31"),
           "reusedTemporalCheck": ("2026-01-05", "2026-10-15")}
CASES = ("complete", "Bonly", "confirmComplete", "confirmBonly")
CUTOFF = "2026-10-15"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ref(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("References must stay inside the project")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    if not path.is_relative_to(ROOT) or sha(path) != item["sha256"]:
        raise ValueError("Source changed or outside project: " + item["path"])
    return path


def read(path):
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def output_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(OUT.resolve()) or path == OUT.resolve():
        raise ValueError("Writes are restricted to the new experiment directory")
    return path


def write(path, value):
    path = output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.name.endswith(".gz") else open
    kwargs = {"compresslevel": 1} if path.name.endswith(".gz") else {}
    with opener(path, "xt", encoding="utf-8", **kwargs) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  separators=(",", ":") if kwargs else None, indent=None if kwargs else 2)
        stream.write("\n")


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_hypothesis(plan):
    require(plan["schema"] == "dual-confirmation-profitability-review-v1", "Unexpected hypothesis schema")
    require(plan["maxNewHypotheses"] == 2 and plan["newAccountsPlanned"] == 10,
            "Exactly two hypotheses and ten accounts are registered")
    require(plan["outcomeBasedParameterChangesAllowed"] is False, "Outcome-based changes must stay disabled")
    require({key: (row["start"], row["end"]) for key, row in plan["periods"].items()} == PERIODS,
            "Registered periods changed")
    accounts = plan["accounts"]
    require(accounts["policies"] == list(CASES) and accounts["initialCapitalYuan"] == 500000
            and accounts["resetAtPeriodStart"] is True
            and accounts["normalSlippageBpsEachSide"] == 10
            and accounts["stressSlippageBpsEachSide"] == 35,
            "Registered account/cost configuration changed")
    require([(row["id"], row["parentPolicy"]) for row in plan["hypotheses"]]
            == [("confirmComplete", "complete"), ("confirmBonly", "Bonly")], "Parent streams changed")
    require(plan["oos"]["exposedThrough"] == CUTOFF, "Historical exposure cutoff changed")


def load_sources(hypothesis):
    parent_path = bound(hypothesis["frozenParentPlan"])
    input_path = bound(hypothesis["parentInputs"])
    parent, inputs = read(parent_path), read(input_path)
    require(inputs["passed"] is True and inputs["plan"] == hypothesis["frozenParentPlan"],
            "Parent validated inputs do not bind the projected plan")
    require(parent["computationalProjection"]["executionSymbols"] == 1120
            and parent["computationalProjection"]["universeUsedForFormation"] == 4999,
            "Expected the full-opportunity execution projection")
    sources = {"parentPlan": ref(parent_path), "parentInputs": ref(input_path),
               "dataset": parent["sources"]["dataset"], "system": inputs["system"]}
    schedules = {}
    for policy in ("complete", "Bonly"):
        groups = [g for g in inputs["groups"] if g["period"] == "primary" and g["policy"] == policy]
        require(len(groups) == 1, "Ambiguous parent schedule")
        sources[policy + "Schedule"] = groups[0]["schedule"]
        schedules[policy] = read(bound(groups[0]["schedule"]))
    for index, item in enumerate(hypothesis["diagnosticSources"]):
        bound(item)
        sources[f"diagnostic{index + 1}"] = item
    data = read(bound(sources["dataset"]))
    system = read(bound(sources["system"]))
    require(len(data["securities"]) == 1120, "Projection population changed")
    require(data["calendar"] == sorted(set(data["calendar"])) and data["calendar"][-1] == CUTOFF,
            "Source must end at the already exposed cutoff")
    require(all(bar["date"] <= CUTOFF for s in data["securities"] for bar in s["bars"]),
            "Post-cutoff stock observations forbidden")
    require(all(bar["date"] <= CUTOFF for bar in data.get("benchmark", {}).get("bars", [])),
            "Post-cutoff benchmark observations forbidden")
    require(all(day["date"] <= CUTOFF for day in system["daily"]), "Post-cutoff context forbidden")
    symbols = {s["symbol"] for s in data["securities"]}
    require(all(row["symbol"] in symbols and row["signalDate"] <= CUTOFF
                for rows in schedules.values() for row in rows), "Projection omitted a parent opportunity")
    return parent, sources, data, system, schedules


def prefix_inputs(dataset, system, schedule, start, end):
    """New containers, unchanged raw observations, no later bars or dated states.

    An action announced by the cutoff stays available in full, even when its
    ex/payment/listing date is later. Such known future rights are not prices;
    deleting them can corrupt entitlements and receivables at the boundary.
    """
    from service.portfolio_engine import _digest
    require(date.fromisoformat(start).isoformat() == start and date.fromisoformat(end).isoformat() == end
            and start <= end <= CUTOFF, "Invalid historical prefix bounds")
    days = [day for day in dataset["calendar"] if day <= end]
    require(days and any(start <= day <= end for day in days), "Empty account period")
    allowed = set(days)
    omitted, removed_actions = [], 0

    def trim(value):
        nonlocal removed_actions
        result = dict(value)
        result.pop("forceSeed", None)
        result.pop("historyWindow", None)
        result["bars"] = [bar for bar in value["bars"] if bar["date"] <= end]
        require(all(bar["date"] in allowed for bar in result["bars"]), "Bar outside independent calendar")
        for key, item in value.items():
            if (key.endswith("ByDate") or key in ("stHistory", "memberHistory")) and isinstance(item, dict):
                result[key] = {day: record for day, record in item.items() if day <= end}
        if "corporateActions" in value:
            kept = []
            for action in value["corporateActions"]:
                known = action.get("announcementDate")
                if known is not None:
                    require(date.fromisoformat(known).isoformat() == known, "Malformed announcement date")
                    retain = known <= end
                else:
                    # Preserve already-observed legacy clues without inventing an announcement date.
                    retain = action.get("exDate", "9999-12-31") <= end
                if retain:
                    kept.append(deepcopy(action))
                else:
                    removed_actions += 1
            result["corporateActions"] = kept
        return result

    view = dict(dataset)
    view.pop("fingerprint", None)
    for key, value in dataset.items():
        if key.endswith("ByDate") and isinstance(value, dict):
            view[key] = {day: item for day, item in value.items() if day <= end}
    view["calendar"] = days
    view["securities"] = []
    for security in dataset["securities"]:
        item = trim(security)
        if item["bars"]:
            view["securities"].append(item)
        else:
            omitted.append(security["symbol"])
    if dataset.get("benchmark") is not None:
        view["benchmark"] = trim(dataset["benchmark"])
    view.update(startDate=start, endDate=end)
    if isinstance(view.get("historyCoverage"), dict):
        view["historyCoverage"] = deepcopy(view["historyCoverage"])
        if "endDate" in view["historyCoverage"]:
            view["historyCoverage"]["endDate"] = min(end, view["historyCoverage"]["endDate"])
    signals = [deepcopy(row) for row in schedule if start <= row["signalDate"] <= end]
    require(not set(omitted).intersection(row["symbol"] for row in signals),
            "A registered opportunity has no prefix history")
    daily = [row for row in system["daily"] if row["date"] <= end]
    snapshots = {row["snapshotId"] for row in daily}
    context = {"daily": daily,
               "sectorMaps": {key: value for key, value in system["sectorMaps"].items() if key in snapshots},
               "metadata": {row["signalId"]: system["metadata"][row["signalId"]] for row in signals}}
    proof = {"start": start, "end": end, "warmupStart": days[0], "calendarSessions": len(days),
             "accountSessions": sum(start <= d <= end for d in days),
             "sourceSymbols": len(dataset["securities"]), "retainedSymbols": len(view["securities"]),
             "emptyPrefixSymbolsOmitted": omitted, "parentOpportunityCount": len(signals),
             "postAnnouncementCutoffActionsRemoved": removed_actions,
             "knownFuturePaymentRightsRetained": True, "originalRanksAndFieldsUnchanged": True,
             "selectionUniverseRecomputed": False, "sourceEnd": dataset["calendar"][-1],
             "newSegmentConsumed": False, "pristineHoldout": False}
    view["reviewPrefix"] = {"version": VERSION, "start": start, "end": end,
                            "sourceFingerprint": dataset.get("fingerprint"), "priceRowsSharedReadOnly": True}
    view["fingerprint"] = _digest(view)
    return view, context, signals, proof


def implementation_pins(parent):
    from service.research_fixed_signal_account import _source_pins
    pins = dict(parent["sourcePins"])
    for path, expected in pins.items():
        bound({"path": path, "sha256": expected})
    pins.update(_source_pins())
    for path in ("scripts/run-dual-confirmation-review.py", "service/research_dual_confirmation.py",
                 "tests/test_research_dual_confirmation.py", "tests/test_dual_confirmation_runner.py",
                 "scripts/verify-dual-confirmation-inputs.py", "tests/test_dual_confirmation_oracle.py",
                 "scripts/verify-guiyin-dual-path-system.py", "service/research_account_outcomes.py"):
        pins[path] = sha(ROOT / path)
    return pins


def prepare():
    require(not OUT.exists(), "Preserve existing experiment; preparation never overwrites")
    hypothesis = read(HYPOTHESIS)
    validate_hypothesis(hypothesis)
    parent, sources, dataset, system, schedules = load_sources(hypothesis)
    from service.research_dual_confirmation import build_confirmation_inputs
    test_modules = ["tests.test_dual_confirmation_runner", "tests.test_research_dual_confirmation",
                    "tests.test_dual_confirmation_oracle"]
    testing = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", *test_modules, "-v"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "TEMP": "H:/codex/tmp", "TMP": "H:/codex/tmp", "PYTHONUTF8": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    require(testing.returncode == 0, "Pre-account synthetic checks failed: " + testing.stderr)
    pins = implementation_pins(parent)
    configs = deepcopy(parent["baseConfig"])
    require(configs["initialCapital"] == 500000 and configs["slippageBps"] == 10,
            "Frozen parent capital/cost differs")
    OUT.mkdir(parents=True)
    write(OUT / "synthetic-tests.json", {"passed": True, "modules": test_modules,
          "stdout": testing.stdout, "stderr": testing.stderr, "completedAt": now(), "marketAccountsRun": 0})
    manifest = {"schema": VERSION, "registeredAt": now(), "hypothesis": ref(HYPOTHESIS),
                "sourceRefs": sources, "sourcePins": pins, "baseConfig": configs,
                "syntheticTests": ref(OUT / "synthetic-tests.json"),
                "parentPopulationMeaning": "All original opportunity-stream symbols, not filled-only securities",
                "confirmationMeaning": "Filter/delay original ranks only; no ranking recalculation or backfill",
                "plannedAccounts": 10, "newAccountsRun": 0, "newHypotheses": 2,
                "newSegmentConsumed": False, "pristineHoldout": False, "periodInputs": {}, "jobs": []}
    for path in pins:
        target = output_path(OUT / "frozen-code" / path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write((ROOT / path).read_bytes())
    write(OUT / "hypothesis-plan.json", hypothesis)
    for period, (start, end) in PERIODS.items():
        emit({"phase": "preparePeriod", "period": period, "start": start, "end": end, "accountsRun": 0})
        directory = OUT / "inputs" / period
        # The source is read once; only containers are copied for the two chronological views.
        view, base_context, complete, prefix = prefix_inputs(dataset, system, schedules["complete"], start, end)
        write(directory / "account-data.json.gz", view)
        write(directory / "prefix-validation.json", prefix)
        period_refs = {"dataset": ref(directory / "account-data.json.gz"),
                       "prefixValidation": ref(directory / "prefix-validation.json"), "cases": {}}
        for case in CASES:
            parent_policy = "Bonly" if case in ("Bonly", "confirmBonly") else "complete"
            parent_signals = [deepcopy(row) for row in schedules[parent_policy] if start <= row["signalDate"] <= end]
            case_context = {**base_context, "metadata": {
                row["signalId"]: system["metadata"][row["signalId"]] for row in parent_signals}}
            if case.startswith("confirm"):
                case_context, signals, proof = build_confirmation_inputs(
                    view, case_context, parent_signals, case, start, end)
                write(directory / case / "confirmation-proof.json", proof)
                execution_policy = "complete"
            else:
                signals, execution_policy = parent_signals, case
            require(all(start <= row["signalDate"] <= end for row in signals), "Cross-period signal escaped")
            write(directory / case / "signals.json", signals)
            write(directory / case / "system-inputs.json.gz", case_context)
            case_refs = {"schedule": ref(directory / case / "signals.json"),
                         "system": ref(directory / case / "system-inputs.json.gz"),
                         "parentPolicy": parent_policy, "executionPolicy": execution_policy,
                         "parentOpportunities": len(parent_signals), "signals": len(signals)}
            if case.startswith("confirm"):
                case_refs["confirmationProof"] = ref(directory / case / "confirmation-proof.json")
            period_refs["cases"][case] = case_refs
            for bps in ([10, 35] if period == "reusedTemporalCheck" and case.startswith("confirm") else [10]):
                manifest["jobs"].append({"id": f"{period}__{case}__{bps}bps", "period": period,
                    "case": case, "start": start, "end": end, "slippageBps": bps,
                    "costCase": "normal" if bps == 10 else "stress", "initialCapital": 500000, "coldStart": True})
        manifest["periodInputs"][period] = period_refs
        del view, base_context, complete
        gc.collect()
    require(len(manifest["jobs"]) == 10, "Unexpected job count")
    for path, expected in pins.items():
        bound({"path": path, "sha256": expected})
    for item in sources.values():
        bound(item)
    require(ref(HYPOTHESIS) == manifest["hypothesis"], "Hypothesis changed during preparation")
    write(OUT / "execution-plan.json", manifest)
    write(OUT / "prepare-completion.json", {"passed": True, "completedAt": now(),
        "plan": ref(OUT / "execution-plan.json"), "newAccountsRun": 0,
        "allSchedulesFrozenBeforeAccounts": True, "newSegmentConsumed": False})
    emit({"prepared": True, "directory": str(OUT), "accountsRun": 0, "plannedAccounts": 10,
          "plan": ref(OUT / "execution-plan.json")})


def verified_plan():
    receipt = read(OUT / "prepare-completion.json")
    require(receipt["passed"] is True and receipt["newAccountsRun"] == 0, "Preparation was not completed")
    plan = read(bound(receipt["plan"]))
    hypothesis = read(bound(plan["hypothesis"]))
    validate_hypothesis(hypothesis)
    require(read(bound(plan["syntheticTests"]))["passed"] is True, "Synthetic checks are not bound")
    for path, expected in plan["sourcePins"].items():
        bound({"path": path, "sha256": expected})
    for item in plan["sourceRefs"].values():
        bound(item)
    require(len(plan["jobs"]) == 10 and plan["plannedAccounts"] == 10, "Unexpected frozen jobs")
    return plan


def oracle_module():
    spec = importlib.util.spec_from_file_location("_dual_confirmation_cash_oracle",
                                                ROOT / "scripts/verify-guiyin-dual-path-system.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def account_folder(job):
    return OUT / "accounts" / job["period"] / job["case"] / job["costCase"]


def run(job_id=None):
    from service.research_guiyin_dual_path import DualPathAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan = verified_plan()
    oracle = oracle_module()
    require(job_id is None or job_id in {job["id"] for job in plan["jobs"]}, "Unknown frozen job")
    selected = [job for job in plan["jobs"] if job_id is None or job["id"] == job_id]
    for index, job in enumerate(selected, 1):
        folder = account_folder(job)
        if (folder / "completion.json").exists():
            completion = read(folder / "completion.json")
            require(completion["plan"] == ref(OUT / "execution-plan.json") and completion["job"] == job,
                    "Completed job belongs to another frozen plan")
            for item in completion["artifacts"].values():
                bound(item)
            emit({"phase": "alreadyCompleted", "job": job["id"], "index": index, "total": len(selected)})
            continue
        require(not folder.exists(), "Preserve incomplete account for diagnosis: " + job["id"])
        period = plan["periodInputs"][job["period"]]
        case = period["cases"][job["case"]]
        data = read(bound(period["dataset"]))
        system = read(bound(case["system"]))
        signals = read(bound(case["schedule"]))
        config = deepcopy(plan["baseConfig"])
        config.update(startDate=job["start"], endDate=job["end"],
                      initialCapital=job["initialCapital"], slippageBps=job["slippageBps"])
        write(folder / "run-start.json", {"startedAt": now(), "plan": ref(OUT / "execution-plan.json"),
              "job": job, "dataset": period["dataset"], "schedule": case["schedule"], "system": case["system"]})
        emit({"phase": "accountStart", "job": job["id"], "index": index, "total": len(selected),
              "opportunities": len(signals), "initialCapital": config["initialCapital"]})
        try:
            engine = DualPathAccount(data, signals, config, system_inputs=system,
                                     policy=case["executionPolicy"], fee_policy=LegacyFeePolicy())
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            summary = summarize_account(result, checkpoint, policy_id=job["case"], policy_label=job["case"])
            require(result["status"] == "completed" and not summary["quality"]["integrityIssues"],
                    "Account did not complete or cash bridge failed")
            audit = oracle.account_audit(result, data, system)
            metrics = oracle.metrics(result)
            artifacts = {}
            for name, value in (("result.json.gz", result), ("checkpoint.json.gz", checkpoint),
                                ("summary.json", summary), ("validation.json", audit),
                                ("metrics.json", metrics)):
                write(folder / name, value)
                artifacts[name] = ref(folder / name)
            # Recheck the frozen identity after an execution before publishing completion.
            verified_plan()
            write(folder / "completion.json", {"completedAt": now(), "plan": ref(OUT / "execution-plan.json"),
                "job": job, "artifacts": artifacts, "independentCashAuditPassed": audit["passed"],
                "confirmationTimingIndependentReviewPending": job["case"].startswith("confirm"),
                "checkpointMeaning": "Audit evidence only; DualPath live exit state is not restart-restorable",
                "pristineHoldout": False, "certified": False, "targetAchieved": False})
            emit({"phase": "accountCompleted", "job": job["id"], "index": index, "total": len(selected),
                  "netReturnPct": metrics["netReturnPct"], "closed": metrics["closed"],
                  "maxDrawdownPct": metrics["maxDrawdownPct"], "independentCashAuditPassed": True})
            del engine, result, checkpoint, summary, audit, metrics
        except Exception as error:
            write(folder / "failure.json", {"failedAt": now(), "type": type(error).__name__,
                  "message": str(error), "job": job, "resultPublished": False})
            raise
        finally:
            del data, system, signals
            gc.collect()
    if all((account_folder(job) / "completion.json").exists() for job in plan["jobs"]):
        target = OUT / "all-accounts-completion.json"
        if not target.exists():
            rows = [{"job": job, "metrics": read(account_folder(job) / "metrics.json"),
                     "metricsRef": ref(account_folder(job) / "metrics.json"),
                     "summary": ref(account_folder(job) / "summary.json"),
                     "result": ref(account_folder(job) / "result.json.gz"),
                     "validation": ref(account_folder(job) / "validation.json"),
                     "completion": ref(account_folder(job) / "completion.json")} for job in plan["jobs"]]
            write(OUT / "account-index.json", {"plan": ref(OUT / "execution-plan.json"), "rows": rows})
            write(target, {"completedAt": now(), "plan": ref(OUT / "execution-plan.json"),
                "accounts": [row["completion"] for row in rows], "index": ref(OUT / "account-index.json"),
                "newAccountsRun": 10, "newHypotheses": 2, "independentConfirmationReviewPending": True,
                "pristineHoldout": False, "certified": False, "targetAchieved": False})


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    parser.add_argument("--job", help="Run one already frozen account id; never creates a new configuration")
    args = parser.parse_args()
    if args.command == "prepare":
        require(args.job is None, "Preparation cannot select individual jobs")
        prepare()
    elif args.command == "verify":
        plan = verified_plan()
        emit({"verified": True, "plannedAccounts": len(plan["jobs"]), "accountsExecutedByVerify": 0})
    else:
        run(args.job)
