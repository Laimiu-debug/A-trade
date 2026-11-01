"""Register, independently prepare, and execute the complete-system study."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import re
import struct
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


U = module("_whole_system_frozen_utils", "scripts/run-liquid-trend-breakout.py")
read, write, ref, bound, sha, now, emit = (getattr(U, name) for name in ("read", "write", "ref", "bound", "sha", "now", "emit"))
BASE = ROOT / "artifacts/strategy-search"
FOUNDATION = BASE / "market-sector-stock-system-v1"
STUDY = BASE / "market-sector-stock-accounts-v1"
OUT = STUDY / "attempt-001"
PLAN = OUT / "plan.json"
PROTOCOL = ROOT / "docs/market-sector-stock-account-v1.json"
POLICIES = ("completeSystem", "withoutMarketAdmission", "withoutSectorAdmission", "withoutCatchupEntry", "sectorLiquidRepresentative")
SID = "marketSectorStockV1"


def verify_plan():
    plan = read(PLAN)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    for value in plan["bindings"]:
        bound(value)
    return plan


def register():
    from service.research_fixed_signal_account import _source_pins
    if OUT.exists():
        raise ValueError("Attempt exists; preserve it and use a new explicit attempt number")
    foundation = read(FOUNDATION / "foundation-readout.json")
    prices = read(bound(foundation["priceMatrixProof"]))
    source_plan = read(bound(prices["plan"]))
    if not prices["passed"] or prices["population"] != 4999:
        raise ValueError("Whole-cohort verified price inputs required")
    if foundation["protocol"] != ref(PROTOCOL):
        raise ValueError("First complete-system hypothesis changed")
    rules = read(PROTOCOL)
    parent_path = BASE / "reclaim-market-alignment-v1/plan.json"
    parent = read(parent_path)
    config = deepcopy(parent["config"])
    config["strategies"][0]["id"] = SID
    config["allocation"]["strategyIds"] = [SID]
    pins = _source_pins()
    for path in ("service/research_volatility_trail.py", "service/research_account_outcomes.py", "service/research_market_sector_stock.py", "service/research_market_sector_account.py",
            "tests/test_research_market_sector_stock.py", "tests/test_research_market_sector_account.py", "scripts/verify-market-sector-stock-inputs.py",
            "scripts/run-market-sector-stock-account.py", "scripts/run-liquid-trend-breakout.py", "docs/market-sector-stock-account-v1.json"):
        pins[path] = sha(ROOT / path)
    bindings = [ref(FOUNDATION / "foundation-readout.json"), foundation["priceMatrixProof"], foundation["publishedSnapshotProof"],
        prices["prices"], prices["securities"], prices["snapshotCohortJoins"], ref(parent_path), source_plan["snapshots"], source_plan["calendar"],
        *source_plan["sources"].values(), *source_plan["indices"].values()]
    for value in bindings:
        bound(value)
    OUT.mkdir(parents=True)
    write(OUT / "continuation-before.json", read(BASE / "goal-continuation-20261018.json"))
    write(PLAN, {"schema": "market-sector-stock-accounts-v1-plan", "registeredAt": now(), "protocol": ref(PROTOCOL), "rules": rules,
        "sourcePins": pins, "bindings": bindings, "matrix": prices["prices"], "joins": prices["snapshotCohortJoins"],
        "sources": source_plan["sources"], "indices": source_plan["indices"], "baseConfig": config, "workspaceJsonBefore": U.workspace_snapshot(),
        "plannedAccounts": [{"period": "primary", "policy": p} for p in POLICIES] + [{"period": "weakMarketStress", "policy": "completeSystem"}],
        "implementationInterpretations": {
            "unknownStreak": "An unknown calendar observation resets both streaks without a negative vote; confirmation/failure requires consecutive known calendar slots",
            "priorLeader": "60-return midrank>=0.8,above industry mean,positive60-return and aboveMA60; entry overextension filter applies only to a candidate purchase",
            "newCandidate": "One new signal event per industry close; a frozen candidate already held is rejected without substitution or adding",
            "scoreTies": "Equal-rank integer numerators summed over common6*N denominator, then stable industry-code tie break",
            "exitPriority": "existingForced;inheritedEligibilityStop5;volatilityRatchet;structural;market;sector",
            "dataQuality": "At least95percent coverage separately for20-return,60-return,60-MA breadth and common5/prior20-amount input, plus at least5 cohort members"
        }, "newStrategyTrials": 1, "actualNewAccountsRun": 0, "holdoutClaim": False, "targetAchieved": False})
    for path in pins:
        target = OUT / "frozen-code" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write((ROOT / path).read_bytes())
    emit({"registered": True, "attempt": OUT.name, "plannedAccounts": 6, "plan": ref(PLAN)})


def index_data(plan, calendar):
    index = {day: i for i, day in enumerate(calendar)}
    arrays, benchmark_rows = {}, []
    for symbol, item in plan["indices"].items():
        close, amount = np.full(len(calendar), np.nan), np.full(len(calendar), np.nan)
        for row in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes()):
            value = str(row[0]); day = value[:4] + "-" + value[4:6] + "-" + value[6:]
            if day not in index:
                continue
            i = index[day]
            if np.isfinite(close[i]):
                raise ValueError("Duplicate frozen index date")
            close[i], amount[i] = row[4]/100, row[5]
            if symbol == "sh000300":
                benchmark_rows.append({"date": day, "open": row[1]/100, "high": row[2]/100, "low": row[3]/100,
                    "close": row[4]/100, "amount": row[5], "volume": row[6]})
        arrays[symbol] = {"close": close, "amount": amount}
    return arrays, {"symbol": "sh000300", "name": "沪深300", "bars": benchmark_rows, "amountUnit": "元", "adjustment": "none"}


def prepare():
    from service.research_market_sector_stock import build_inputs
    plan = verify_plan()
    write(OUT / "prepare-start.json", {"startedAt": now(), "plan": ref(PLAN)})
    tests = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_market_sector_stock", "tests.test_research_market_sector_account",
        "tests.test_research_volatility_trail", "tests.test_research_fixed_signal_account", "tests.test_research_account_fees", "tests.test_research_account_outcomes"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    write(OUT / "pre-account-tests.json", {"exitCode": tests.returncode, "stdout": tests.stdout, "stderr": tests.stderr, "passed": tests.returncode == 0})
    if tests.returncode:
        raise ValueError("Pre-account checks failed")
    with np.load(bound(plan["matrix"]), allow_pickle=False) as stored:
        matrix = {key: stored[key] for key in stored.files}
    joins = read(bound(plan["joins"]))
    calendar, symbols = matrix["calendar"].tolist(), matrix["symbols"].tolist()
    indices, benchmark = index_data(plan, calendar)
    start = plan["rules"]["periods"]["weakMarketStress"]["startDate"]
    emit({"inputBuildStart": True, "stocks": len(symbols), "calendarSessions": len(calendar)})
    formation = build_inputs(matrix, joins, indices, start)
    write(OUT / "formation.candidate.json.gz", formation)
    emit({"inputFormationBuilt": formation["counts"], "accountOutcomesObserved": False})
    oracle = module("_whole_system_direct_window_oracle", "scripts/verify-market-sector-stock-inputs.py")
    audit = oracle.DirectWindowReference(matrix, joins, indices).audit(formation)
    write(OUT / "formation-validation.json", audit)
    emit({"fullIndependentInputAuditPassed": True, **audit})
    sector_maps = {join["snapshotId"]: {symbols[r]: g["sectorCode"] for g in join["sectors"] for r in g["cohortRowIndices"]} for join in joins}
    contexts = [{"date": day["date"], "snapshotId": day["snapshotId"], "market": {key: day["market"][key] for key in ("admission", "riskExit", "crisisCount")},
        "selectedSectors": day["selectedSectors"], "sectors": [{key: s[key] for key in ("sectorCode", "riskExit", "failCount")} for s in day["sectors"]]} for day in formation["daily"]]
    system_inputs = {"daily": contexts, "metadata": formation["metadata"], "sectorMaps": sector_maps,
        "formationSource": ref(OUT / "formation.candidate.json.gz"), "independentProof": ref(OUT / "formation-validation.json")}
    write(OUT / "system-inputs.json.gz", system_inputs)
    groups = []
    for account in plan["plannedAccounts"]:
        period, policy = account["period"], account["policy"]
        dates = plan["rules"]["periods"][period]
        schedule = [r for r in formation["schedules"][policy] if dates["startDate"] <= r["signalDate"] <= dates["endDate"]]
        path = OUT / period / policy / "signals.json"
        write(path, schedule)
        groups.append({"period": period, "policy": policy, "schedule": ref(path), "signals": len(schedule)})
    del matrix, formation, indices, system_inputs
    gc.collect()
    securities, calendar_source, warnings = [], None, []
    for board, source in plan["sources"].items():
        data = read(bound(source))
        if data["calendar"] != calendar or data["priceBasisId"] != "raw-unadjusted" or data["adjustment"] != "none":
            raise ValueError("Merged engine source differs from validated raw matrix")
        if calendar_source is not None and data["calendarSource"] != calendar_source:
            raise ValueError("Source calendar definitions differ")
        calendar_source = data["calendarSource"]
        securities.extend(data["securities"])
        warnings.extend(data.get("warnings", []))
        emit({"mergedEngineSource": board, "stocksSoFar": len(securities)})
        del data
        gc.collect()
    if len(securities) != 4999 or {s["symbol"] for s in securities} != set(symbols):
        raise ValueError("Whole fixed cohort must survive merging")
    dataset = {"calendar": calendar, "calendarSource": calendar_source, "adjustment": "none", "priceBasisId": "raw-unadjusted",
        "startDate": calendar[0], "endDate": calendar[-1], "benchmark": benchmark, "securities": sorted(securities, key=lambda s: s["symbol"]),
        "dataQuality": {"certified": False, "currentSurvivingCohorts": True, "datedIndustrySnapshotsVerified": True},
        "source": "mergedExisting4999RawSources;noPriceMutation", "fingerprint": U.sha(bound(plan["matrix"])),
        "warnings": sorted(set(warnings)) + ["Industry publication-time source verified; historical universe/rights/tradability limitations remain."]}
    write(OUT / "account-data.json.gz", dataset)
    if U.workspace_snapshot() != plan["workspaceJsonBefore"]:
        raise ValueError("Protected workspace changed")
    verify_plan()
    write(OUT / "input-validation.json", {"passed": True, "completedAt": now(), "planSha256": sha(PLAN), "allSixSchedulesFrozenBeforeAccounts": True,
        "dataset": ref(OUT / "account-data.json.gz"), "systemInputs": ref(OUT / "system-inputs.json.gz"), "formation": ref(OUT / "formation.candidate.json.gz"),
        "formationValidation": ref(OUT / "formation-validation.json"), "tests": ref(OUT / "pre-account-tests.json"), "groups": groups,
        "population": 4999, "newAccountsRun": 0, "userJsonFilesUnchanged": True, "completeSystemValidated": False})
    emit({"inputsReady": True, "groups": [{k: g[k] for k in ("period", "policy", "signals")} for g in groups], "inputValidation": ref(OUT / "input-validation.json")})


def run_account(period, policy):
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    from service.research_market_sector_account import MarketSectorStockAccount
    plan, inputs = verify_plan(), read(OUT / "input-validation.json")
    if not inputs["passed"] or inputs["planSha256"] != sha(PLAN) or not inputs["allSixSchedulesFrozenBeforeAccounts"]:
        raise ValueError("All source/schedule/oracle checks required before account execution")
    group = next(g for g in inputs["groups"] if g["period"] == period and g["policy"] == policy)
    folder = OUT / period / policy
    write(folder / "run-start.json", {"startedAt": now(), "plan": ref(PLAN), "inputs": ref(OUT / "input-validation.json")})
    data, system = read(bound(inputs["dataset"])), read(bound(inputs["systemInputs"]))
    config = deepcopy(plan["baseConfig"])
    dates = plan["rules"]["periods"][period]
    config.update(startDate=dates["startDate"], endDate=dates["endDate"], initialCapital=dates["initialCapital"])
    schedule = read(bound(group["schedule"]))
    emit({"accountStart": [period, policy], "signals": len(schedule)})
    account = MarketSectorStockAccount(data, schedule, config, system_inputs=system, policy=policy, fee_policy=LegacyFeePolicy())
    account.step(20000)
    result, checkpoint = account.result(), account.checkpoint()
    summary = summarize_account(result, checkpoint, policy_id=policy, policy_label=policy)
    if result["status"] != "completed" or summary["quality"]["integrityIssues"] or result["research"]["identity"]["signalSchedule"] != schedule:
        raise ValueError("Whole-system account or cash/fee reconciliation failed")
    for suffix, value in (("result.json.gz", result), ("checkpoint.json.gz", checkpoint), ("summary.json", summary)):
        write(folder / suffix, value)
    verify_plan()
    write(folder / "completion.json", {"completedAt": now(), "plan": ref(PLAN), "inputs": ref(OUT / "input-validation.json"),
        "period": period, "policy": policy, "result": ref(folder / "result.json.gz"), "checkpoint": ref(folder / "checkpoint.json.gz"),
        "summary": ref(folder / "summary.json"), "schedule": group["schedule"], "independentAccountAuditPending": True, "targetAchieved": False})
    c = summary["closedEpisodes"]
    emit({"accountCompletedUnaudited": [period, policy], "returnPct": summary["account"]["netReturnPct"], "DD": summary["account"]["maxDrawdownPct"],
        "R": c["payoffRatio"], "PF": c["profitFactor"], "closed": c["count"], "meanLossPct": c["meanLossReturnPct"]})


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("command", choices=("register", "prepare", "run"))
    parser.add_argument("--attempt", default="001")
    parser.add_argument("--period", choices=("primary", "weakMarketStress"))
    parser.add_argument("--policy", choices=POLICIES)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{3}", args.attempt):
        raise ValueError("Explicit three-digit attempt identity required")
    OUT, PLAN = STUDY / ("attempt-" + args.attempt), STUDY / ("attempt-" + args.attempt) / "plan.json"
    register() if args.command == "register" else prepare() if args.command == "prepare" else run_account(args.period, args.policy)
