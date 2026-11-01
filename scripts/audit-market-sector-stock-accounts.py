"""Read-only complete-account audits and full-system development readout."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
import gc
from pathlib import Path
import re
from statistics import mean, median
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import importlib.util


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


U = module("_whole_system_readonly_audit_utils", "scripts/run-liquid-trend-breakout.py")
read, write, ref, bound, sha, emit = (getattr(U, k) for k in ("read", "write", "ref", "bound", "sha", "emit"))


def grouped_episode_diagnostics(result, metadata):
    closed = {(r["symbol"], r["entryDate"]): r for r in result["closedTrades"]}
    entries, groups = {}, {"exitReason": defaultdict(list), "entryPattern": defaultdict(list)}
    for fill in result["trades"]:
        symbol = fill["symbol"]
        if fill["side"] == "buy":
            entries[symbol] = fill
        elif fill["positionAction"] == "exit":
            entry = entries.pop(symbol)
            episode = closed[symbol, entry["date"]]
            groups["exitReason"][fill["reason"]].append(episode)
            groups["entryPattern"][metadata[entry["signalId"]]["pattern"]].append(episode)
    value = {}
    for dimension, groups_by_label in groups.items():
        rows = []
        for label, episodes in sorted(groups_by_label.items()):
            wins = [r for r in episodes if r["pnlAmount"] > 0]
            losses = [r for r in episodes if r["pnlAmount"] < 0]
            rows.append({"label": label, "closed": len(episodes), "netPnl": sum(r["pnlAmount"] for r in episodes),
                "meanReturnPct": mean(r["returnPct"] for r in episodes), "meanWinReturnPct": mean(r["returnPct"] for r in wins) if wins else None,
                "meanLossReturnPct": mean(r["returnPct"] for r in losses) if losses else None,
                "medianHoldingSessions": median(r["holdingDays"] for r in episodes),
                "closedWithin5Sessions": sum(r["holdingDays"] <= 5 for r in episodes)})
        value[dimension] = rows
    value["meaning"] = "Descriptive conditioning of actual closed episodes; not the return of a strategy rerun with another exit or branch."
    value["opportunityReasons"] = dict(Counter(r["reason"] for r in result["research"]["opportunities"]))
    value["newAccounts"] = 0
    return value


def audit(study, audit_id):
    folder = study / ("audit-" + audit_id)
    if folder.exists():
        raise ValueError("Audit attempt exists; preserve it and use another explicit audit identity")
    folder.mkdir()
    plan_path, input_path = study / "plan.json", study / "input-validation.json"
    plan, inputs = read(plan_path), read(input_path)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    for value in plan["bindings"]:
        bound(value)
    if not inputs["passed"] or inputs["planSha256"] != sha(plan_path) or not inputs["allSixSchedulesFrozenBeforeAccounts"]:
        raise ValueError("Complete frozen whole-system input proof required")
    source_pins = {path: sha(ROOT / path) for path in ("scripts/audit-market-sector-stock-accounts.py", "scripts/verify-market-sector-stock-risk.py",
        "scripts/verify-market-sector-stock-ledger.py", "tests/test_market_sector_risk_oracle.py", "tests/test_market_sector_ledger_oracle.py")}
    write(folder / "start.json", {"plan": ref(plan_path), "inputs": ref(input_path), "startedAt": U.now(), "auditorSourcePins": source_pins})
    try:
        tests = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_market_sector_risk_oracle", "tests.test_market_sector_ledger_oracle"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        write(folder / "tests.json", {"passed": tests.returncode == 0, "exitCode": tests.returncode, "stdout": tests.stdout, "stderr": tests.stderr})
        if tests.returncode:
            raise ValueError("Read-only risk/ledger oracle sensitivity checks failed")
        risk = module("_whole_system_actual_fill_risk_replay", "scripts/verify-market-sector-stock-risk.py")
        book = module("_whole_system_independent_cash_and_sizing", "scripts/verify-market-sector-stock-ledger.py")
        numbers = module("_whole_system_independent_outcome_numbers", "scripts/verify-liquid-trend-breakout.py")
        from service.portfolio_engine import _digest
        from service.portfolio_config import normalize_config
        data, system = read(bound(inputs["dataset"])), read(bound(inputs["systemInputs"]))
        data_digest = _digest(data)
        system_digest = _digest(system)
        benchmark = {r["date"]: r["close"] for r in data["benchmark"]["bars"]}
        rows, audited, diagnostic_rows, common_identity = [], [], [], None
        for expected in plan["plannedAccounts"]:
            period, policy = expected["period"], expected["policy"]
            source_folder = study / period / policy
            completion = read(source_folder / "completion.json")
            if completion["plan"] != ref(plan_path) or completion["inputs"] != ref(input_path):
                raise ValueError("Account completion receipt binds different study")
            result, checkpoint, summary = [read(bound(completion[k])) for k in ("result", "checkpoint", "summary")]
            config = deepcopy(plan["baseConfig"])
            dates = plan["rules"]["periods"][period]
            config.update(startDate=dates["startDate"], endDate=dates["endDate"], initialCapital=dates["initialCapital"])
            if result["config"] != normalize_config(config) or result["research"]["identity"]["signalSchedule"] != read(bound(completion["schedule"])):
                raise ValueError("Account configuration or frozen schedule drift")
            identity = result["research"]["identity"]
            if identity["datasetDigest"] != data_digest or identity["datasetFingerprint"] != data["fingerprint"]:
                raise ValueError("Account source identity differs from frozen full dataset")
            comparison = {k: identity[k] for k in ("datasetDigest", "datasetFingerprint", "feePolicy", "sourcePins")}
            if period == "primary":
                if common_identity is None:
                    common_identity = comparison
                elif comparison != common_identity:
                    raise ValueError("Matched primary-account data/engine/fee identity differs")
            system_identity = result["research"]["system"]["identity"]
            if system_identity["inputDigest"] != system_digest or system_identity["policy"] != policy or any(plan["sourcePins"][p] != h for p,h in system_identity["sourcePins"].items()):
                raise ValueError("System decision context, policy or execution source differs")
            emit({"auditStart": [period, policy]})
            trace = risk.daily_system_risk_reference(result, checkpoint, data, system)
            close_state = trace.pop("dailyCloseRiskState")
            money = book.audit_ledger_and_entries(result, data, system, close_state)
            arithmetic = numbers.account_numbers(result)
            c = summary["closedEpisodes"]
            expected_numbers = {"closed": c["count"], "closedNetPnl": c["netPnlAmount"],
                **{k: c[k] for k in ("payoffRatio", "profitFactor", "meanWinReturnPct", "meanLossReturnPct")},
                **{k: summary["account"][k] for k in ("netReturnPct", "maxDrawdownPct")}}
            if not all(U.close(arithmetic[k], v) for k,v in expected_numbers.items()) or summary["quality"]["integrityIssues"]:
                raise ValueError("Independent trade/curve arithmetic or reconciliation differs")
            diag = grouped_episode_diagnostics(result, system["metadata"])
            diagnostic_rows.append({"period": period, "policy": policy, **diag})
            audit_path = folder / (period + "." + policy + ".json")
            write(audit_path, {"passed": True, "completion": ref(source_folder / "completion.json"), "everyHeldDayRisk": trace, "cashFeesEpisodesAndEntryCaps": money,
                "independentOutcomeNumbers": arithmetic, "cashFeeBridgeIntegrityIssues": summary["quality"]["integrityIssues"]})
            audited.append({"period": period, "policy": policy, "proof": ref(audit_path)})
            benchmark_return = (benchmark[config["endDate"]] / benchmark[config["startDate"]] - 1) * 100
            proposed_pass = (arithmetic["closed"] >= 30 and arithmetic["payoffRatio"] is not None and arithmetic["payoffRatio"] >= 3 and
                arithmetic["profitFactor"] is not None and arithmetic["profitFactor"] >= 1.5 and arithmetic["meanLossReturnPct"] is not None and
                arithmetic["meanLossReturnPct"] >= -5 and arithmetic["maxDrawdownPct"] <= 20)
            rows.append({"period": period, "policy": policy, **arithmetic, "winRatePct": c["winRatePct"], "maxConsecutiveLosses": c["maxConsecutiveLosses"],
                "medianLossReturnPct": c["medianLossReturnPct"], "returnQuantilesPct": c["returnQuantilesPct"],
                "averageExposurePct": summary["periods"][0]["exposurePct"]["mean"], "top5ProfitSharePct": summary["concentration"]["top5ProfitSharePct"],
                "openCount": summary["openPositions"]["count"], "openUnrealizedPnl": summary["reconciliation"]["openPositionUnrealizedPnl"],
                "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]), "benchmarkSymbol": "sh000300", "benchmarkReturnPct": benchmark_return,
                "excessPercentagePoints": arithmetic["netReturnPct"]-benchmark_return, "proposedNumericScreenPassed": proposed_pass if period == "primary" else None,
                "screenHumanConfirmed": False, "weakProfitTargetRequired": False, "signals": len(result["research"]["opportunities"]),
                "entries": sum(f["side"] == "buy" for f in result["trades"]), "simulatedPaidFeesCny": sum(f["fee"] for f in result["trades"]),
                "periods": [{"id":p["id"],"start":p["requestedStart"],"end":p["requestedEnd"],"returnPct":p["returnPct"],"DD":p["maxDrawdownPct"],
                    "closed":p["closedEpisodes"]["count"],"R":p["closedEpisodes"]["payoffRatio"],"PF":p["closedEpisodes"]["profitFactor"]} for p in summary["periods"]],
                "evidence": completion, "independentProof": ref(audit_path)})
            emit({"auditPassed": [period, policy], "heldDays": trace["heldDays"], "closed": arithmetic["closed"]})
            del result, checkpoint, summary, trace, close_state
            gc.collect()
        if len(rows) != 6 or U.workspace_snapshot() != plan["workspaceJsonBefore"]:
            raise ValueError("All six audited accounts and unchanged user workspace required")
        for path,digest in {**plan["sourcePins"],**source_pins}.items():
            if sha(ROOT/path) != digest:
                raise ValueError("Source drift during full audit")
        write(folder / "validation.json", {"passed":True,"completedAt":U.now(),"plan":ref(plan_path),"inputs":ref(input_path),"auditorSourcePins":source_pins,
            "accounts":audited,"allFivePrimaryIdentitiesMatched":True,"independentEveryHeldDayRiskAndCashSizingPassed":True,"newExecutionEngine":False,
            "newAccounts":0,"userJsonFilesUnchanged":True,"targetAchieved":False})
        write(study / "episode-diagnostics.json", {"rows":diagnostic_rows,"newAccounts":0,"newStrategyTrials":0,
            "scope":"Actual completely closed episode groups only; do not treat conditional old trades as strategy returns."})
        write(study / "readout.json", {"schema":"market-sector-stock-accounts-v1-readout","completedAt":U.now(),"plan":ref(plan_path),"inputValidation":ref(input_path),
            "audit":ref(folder/"validation.json"),"rows":rows,"newAccounts":6,"newStrategyTrials":1,"population":4999,"continuousEquity":True,"annualReset":False,
            "actualCutoff":"2026-10-15","missingLatestSessions":["2026-10-16"],"certified":False,"holdoutClaim":False,"targetAchieved":False,
            "userJsonFilesUnchanged":True,"modelLimitsRetained":True,"completeSystemBehaviorVerifiedUnderModel":True,
            "profitGoalAchieved":False})
        write(study / "validation.json", {"passed":True,"plan":ref(plan_path),"inputs":ref(input_path),"readout":ref(study/"readout.json"),
            "fullAccountAudit":ref(folder/"validation.json"),"diagnostics":ref(study/"episode-diagnostics.json"),"allSixAccountsRetained":True,
            "userJsonFilesUnchanged":True,"targetAchieved":False})
        emit({"fullAuditPassed":True,"newAccounts":6,"readout":ref(study/"readout.json"),"profitGoalAchieved":False})
    except Exception as error:
        write(folder/"failure.json",{"type":type(error).__name__,"message":str(error),"traceback":traceback.format_exc(),"sourcePins":source_pins,"marketResultsUnmodified":True})
        raise


if __name__ == "__main__":
    parser=ArgumentParser()
    parser.add_argument("--attempt",default="001")
    parser.add_argument("--audit",default="001")
    args=parser.parse_args()
    if not re.fullmatch(r"[0-9]{3}",args.attempt) or not re.fullmatch(r"[0-9]{3}",args.audit):
        raise ValueError("Explicit numeric execution/audit attempt identities required")
    audit(ROOT/"artifacts/strategy-search/market-sector-stock-accounts-v1"/("attempt-"+args.attempt),args.audit)
