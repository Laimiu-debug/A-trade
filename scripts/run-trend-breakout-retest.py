"""Registered continuous post9.24 comparison; source/rules frozen before runs."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


U = module("_retest_frozen_util", "scripts/run-liquid-trend-breakout.py")
read, write, bound, ref, sha, now, emit = [getattr(U, k) for k in ("read", "write", "bound", "ref", "sha", "now", "emit")]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "trend-breakout-retest-v1"
PLAN = OUT / "plan.execution-v2.json"
PRIOR = BASE / "reclaim-2026-replication-v1"
PROTOCOL = ROOT / "docs/trend-breakout-retest-v1.json"
BOARDS, POLICIES = ("main", "chinext", "star"), ("immediate", "immediateLiquid", "retestLiquid")


def date_of(value):
    text = str(value)
    return text[:4] + "-" + text[4:6] + "-" + text[6:]


def verify_plan():
    plan = read(PLAN)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    for name in ("protocol", "previousPlan", "previousValidation", "previousInputs", "continuationSnapshot"):
        bound(plan[name])
    for value in plan["sources"].values():
        bound(value)
    for value in plan["indices"].values():
        bound(value)
    return plan


def register():
    if OUT.exists():
        raise ValueError("Study exists; preserve all previous attempts")
    old, proof, inputs = read(PRIOR / "plan.json"), read(PRIOR / "validation.json"), read(PRIOR / "input-validation.json")
    if not proof["passed"] or proof["readout"] != ref(PRIOR / "readout.json") or not inputs["passed"]:
        raise ValueError("Prior frozen source proof not bound")
    rules = read(PROTOCOL)
    pins = dict(old["sourcePins"])
    for path, digest in pins.items():
        bound({"path": path, "sha256": digest})
    for path in ("docs/trend-breakout-retest-v1.json", "service/research_trend_breakout_retest.py",
                 "tests/test_research_trend_breakout_retest.py", "scripts/verify-trend-breakout-retest.py",
                 "scripts/run-trend-breakout-retest.py"):
        pins[path] = sha(ROOT / path)
    sources = {row["board"]: row["source"] for row in inputs["groups"]}
    for source in sources.values():
        bound(source)
    config = deepcopy(old["config"])
    config.update(startDate=rules["scope"]["startDate"], endDate=rules["scope"]["endDate"])
    config["strategies"][0]["id"] = "trendBreakoutRetestV1"
    config["allocation"]["strategyIds"] = ["trendBreakoutRetestV1"]
    expected = {"initialCapital": 500000, "maxPositions": 5, "maxStockWeight": .2,
                "stopLossPct": .05, "trailingStopPct": 0, "takeProfitPct": 0, "maxHoldingDays": 0}
    if any(config[k] != v for k, v in expected.items()):
        raise ValueError("Frozen comparable account settings differ")
    OUT.mkdir()
    write(OUT / "continuation-before.json", read(BASE / "goal-continuation-20261018.json"))
    plan = {"schema": OUT.name, "registeredAt": now(), "rules": rules, "protocol": ref(PROTOCOL),
        "previousPlan": ref(PRIOR / "plan.json"), "previousValidation": ref(PRIOR / "validation.json"),
        "previousInputs": ref(PRIOR / "input-validation.json"), "sources": sources, "indices": old["indexInputs"],
        "config": config, "sourcePins": pins, "continuationSnapshot": ref(OUT / "continuation-before.json"),
        "workspaceJsonBefore": U.workspace_snapshot(), "newAccountsPlanned": 9, "newStrategyTrialsPlanned": 1,
        "actualCutoff": old["actualCutoff"], "missingLatestSessions": old["missingLatestSessions"],
        "corporateActionResearchExpansionStoppedByHuman": True, "certified": False, "holdoutClaim": False, "targetAchieved": False}
    write(PLAN, plan)
    for name in pins:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    emit({"registered": True, "newAccountsPlanned": 9, "plan": ref(PLAN)})


def prepare():
    from service.research_trend_breakout_retest import build_signals
    plan = verify_plan()
    names = ("test_research_trend_breakout_retest", "test_research_liquid_trend_breakout", "test_research_volatility_trail",
             "test_research_fixed_signal_account", "test_research_account_fees", "test_research_account_outcomes")
    argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", *["tests." + name for name in names]]
    completed = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
    write(OUT / "pre-account-tests.json", {"argv": argv, "exitCode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr, "passed": completed.returncode == 0, "completedAt": now()})
    if completed.returncode:
        raise ValueError("Pre-account tests failed")
    oracle = module("_retest_independent_full_schedule", "scripts/verify-trend-breakout-retest.py")
    indices = {symbol: {date_of(r[0]): {"amount": r[5], "close": r[4] / 100} for r in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes())}
               for symbol, item in plan["indices"].items()}
    market = {d: row["amount"] + indices["sz399106"][d]["amount"] for d, row in indices["sh000001"].items() if d in indices["sz399106"]}
    groups = []
    start, end = plan["config"]["startDate"], plan["config"]["endDate"]
    write(OUT / "market.json", {"amountByDate": market, "basis": "TDX broad SH composite plus SZ composite index amount; CNY; not all-A exact official series"})
    for board in BOARDS:
        emit({"prepareStart": board})
        data = read(bound(plan["sources"][board]))
        if data.get("adjustment") not in ("raw", "none", "unadjusted") or data.get("priceBasisId") != "raw-unadjusted" or data["calendar"][-1] != end:
            raise ValueError("Frozen raw source/cutoff differs")
        production = build_signals(data, market, start=start, end=end)
        expected, setups, seed_counts = oracle.oracle(data, market, start, end)
        actual_setups = sorted((r["symbol"], r["seedDate"], r["status"], r.get("endedAt")) for r in production["setups"])
        if production["schedules"] != expected or actual_setups != setups:
            raise ValueError("Full independent seed/retest schedule or setup-state replay differs")
        for row in production["seedEvidence"]["daily"]:
            if any(row[key] != seed_counts[row["date"]][key] for key in ("unknownWindow", "commonCandidates")):
                raise ValueError("Full population seed coverage differs")
        folder = OUT / board
        write(folder / "signal-evidence.json.gz", production)
        schedules = {}
        for policy in POLICIES:
            write(folder / (policy + ".signals.json"), production["schedules"][policy])
            schedules[policy] = ref(folder / (policy + ".signals.json"))
        groups.append({"board": board, "source": plan["sources"][board], "schedules": schedules,
            "evidence": ref(folder / "signal-evidence.json.gz"), "metadata": production["metadata"],
            "independentAllPopulationSeedAndRetestReplay": True, "independentSetupStatesEqual": True})
        emit({"prepareComplete": board, "signals": production["metadata"]["signals"], "population": len(data["securities"])})
        del data, production, expected, setups
        gc.collect()
    verify_plan()
    write(OUT / "input-validation.json", {"passed": True, "completedAt": now(), "planSha256": sha(PLAN),
        "all9SchedulesFrozenBeforeAccounts": True, "groups": groups, "market": ref(OUT / "market.json"),
        "pretests": ref(OUT / "pre-account-tests.json"), "independentSourceDecodeInherited": True,
        "sourceScope": "unchanged complete fixed cohorts from validated batch52; no expanded action research", "certified": False})
    emit({"inputsValidated": True, "boards": len(groups)})


def run(board):
    from service.research_volatility_trail import VolatilityTrailAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan, inputs = verify_plan(), read(OUT / "input-validation.json")
    if not inputs["passed"] or inputs["planSha256"] != sha(PLAN) or not inputs["all9SchedulesFrozenBeforeAccounts"]:
        raise ValueError("All pre-account input proofs required")
    group = next(g for g in inputs["groups"] if g["board"] == board)
    folder = OUT / board
    write(folder / "run-start.json", {"startedAt": now(), "plan": ref(PLAN), "inputs": ref(OUT / "input-validation.json")})
    data, rows = read(bound(group["source"])), []
    for policy in POLICIES:
        schedule = read(bound(group["schedules"][policy]))
        emit({"accountStart": [board, policy], "signals": len(schedule)})
        account = VolatilityTrailAccount(data, schedule, deepcopy(plan["config"]), entry_weight=.2, fee_policy=LegacyFeePolicy())
        account.step(20000)
        result, checkpoint = account.result(), account.checkpoint()
        summary = summarize_account(result, checkpoint, policy_id=policy, policy_label=policy)
        if result["status"] != "completed" or summary["quality"]["integrityIssues"] or result["research"]["identity"]["signalSchedule"] != schedule:
            raise ValueError("Account/binding/cash-PnL-fee reconciliation failed")
        for suffix, value in (("result.json.gz", result), ("checkpoint.json.gz", checkpoint), ("summary.json", summary)):
            write(folder / (policy + "." + suffix), value)
        rows.append({"board": board, "policy": policy, "schedule": group["schedules"][policy],
            **{k: ref(folder / (policy + "." + suffix)) for k, suffix in (("result", "result.json.gz"), ("checkpoint", "checkpoint.json.gz"), ("summary", "summary.json"))}})
        c = summary["closedEpisodes"]
        emit({"accountComplete": [board, policy], "returnPct": summary["account"]["netReturnPct"], "DD": summary["account"]["maxDrawdownPct"],
              "R": c["payoffRatio"], "PF": c["profitFactor"], "closed": c["count"], "meanLossPct": c["meanLossReturnPct"]})
        del account, result, checkpoint, summary
        gc.collect()
    verify_plan()
    write(OUT / (board + ".completion.json"), {"completedAt": now(), "accounts": 3, "rows": rows,
        "plan": ref(PLAN), "inputs": ref(OUT / "input-validation.json")})


def finalize():
    plan, inputs = verify_plan(), read(OUT / "input-validation.json")
    risk = module("_retest_risk_audit", "scripts/verify-reclaim-volatility-trail.py")
    numbers = module("_retest_outcome_oracle", "scripts/verify-liquid-trend-breakout.py")
    indices = {s: {date_of(r[0]): r[4] / 100 for r in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes())} for s, item in plan["indices"].items()}
    rows, audits = [], []
    for board in BOARDS:
        completion = read(OUT / (board + ".completion.json"))
        if completion["accounts"] != 3 or completion["plan"] != ref(PLAN) or completion["inputs"] != ref(OUT / "input-validation.json"):
            raise ValueError("Account completion binding differs")
        data = read(bound(plan["sources"][board]))
        for item in completion["rows"]:
            result, checkpoint, summary = [read(bound(item[k])) for k in ("result", "checkpoint", "summary")]
            if result["config"] != plan["config"] or result["research"]["identity"]["signalSchedule"] != read(bound(item["schedule"])):
                raise ValueError("Config/schedule changed after input freeze")
            trace = risk.daily_risk_reference(result, checkpoint, data, "volatilityTrail")
            independent = numbers.account_numbers(result)
            c = summary["closedEpisodes"]
            expected = {"closed": c["count"], "closedNetPnl": c["netPnlAmount"],
                **{k: c[k] for k in ("payoffRatio", "profitFactor", "meanWinReturnPct", "meanLossReturnPct")},
                **{k: summary["account"][k] for k in ("netReturnPct", "maxDrawdownPct")}}
            if not all(U.close(independent[k], value) for k, value in expected.items()):
                raise ValueError("Independent trade/curve arithmetic differs")
            benchmark = {"main": "sh000300", "chinext": "sz399006", "star": "sh000688"}[board]
            prices = indices[benchmark]
            benchmark_return = (prices[plan["config"]["endDate"]] / prices[plan["config"]["startDate"]] - 1) * 100
            periods = [{"id": p["id"], "start": p["requestedStart"], "end": p["requestedEnd"], "returnPct": p["returnPct"],
                "DD": p["maxDrawdownPct"], "closed": p["closedEpisodes"]["count"], "R": p["closedEpisodes"]["payoffRatio"],
                "PF": p["closedEpisodes"]["profitFactor"]} for p in summary["periods"]]
            screen = (independent["closed"] >= 30 and independent["payoffRatio"] is not None and independent["payoffRatio"] >= 3 and
                independent["profitFactor"] is not None and independent["profitFactor"] >= 1.5 and independent["meanLossReturnPct"] is not None and
                independent["meanLossReturnPct"] >= -5 and independent["maxDrawdownPct"] <= 20)
            rows.append({"board": board, "policy": item["policy"], **independent, "winRatePct": c["winRatePct"],
                "maxConsecutiveLosses": c["maxConsecutiveLosses"], "medianLossReturnPct": c["medianLossReturnPct"],
                "returnQuantilesPct": c["returnQuantilesPct"], "averageExposurePct": summary["periods"][0]["exposurePct"]["mean"],
                "top5ProfitSharePct": summary["concentration"]["top5ProfitSharePct"], "openCount": summary["openPositions"]["count"],
                "openUnrealizedPnl": summary["reconciliation"]["openPositionUnrealizedPnl"],
                "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]), "benchmarkSymbol": benchmark,
                "benchmarkReturnPct": benchmark_return, "boardExcessPercentagePoints": independent["netReturnPct"] - benchmark_return,
                "proposedNumericScreenPassed": screen, "screenHumanConfirmed": False, "periods": periods, "evidence": item})
            audits.append({"board": board, "policy": item["policy"], "everyHeldDayIndependentRisk": trace})
            del result, checkpoint, summary
            gc.collect()
        del data
        gc.collect()
    if len(rows) != 9 or len({(r["board"], r["policy"]) for r in rows}) != 9:
        raise ValueError("All9 distinct continuous accounts required")
    if U.workspace_snapshot() != plan["workspaceJsonBefore"]:
        raise ValueError("User workspace changed; do not claim it is preserved")
    write(OUT / "readout.json", {"schema": OUT.name + "-readout", "completedAt": now(), "plan": ref(PLAN),
        "rows": rows, "newAccounts": 9, "newStrategyTrials": 1, "continuousEquity": True, "annualReset": False,
        "bearProfitRequired": False, "actualCutoff": plan["actualCutoff"], "missingLatestSessions": plan["missingLatestSessions"],
        "corporateActionResearchExpansionStoppedByHuman": True, "rawModelLimitationsRetained": True,
        "holdoutClaim": False, "certified": False, "targetAchieved": False, "userJsonFilesUnchanged": True})
    write(OUT / "validation.json", {"passed": True, "completedAt": now(), "plan": ref(PLAN),
        "readout": ref(OUT / "readout.json"), "independentAllPopulationSeedRetestAndSetupReplay": True,
        "independentEveryHeldDayRiskAudits": audits, "independentTradeCurveArithmetic": True,
        "existingAuditedCashPnlFeeBridgesReconciled": True, "independentSecondAccountEngine": False, "userJsonFilesUnchanged": True})
    emit({"completed": True, "accounts": 9, "readout": ref(OUT / "readout.json"), "targetAchieved": False})


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("command", choices=("register", "prepare", "run", "finalize"))
    parser.add_argument("--board", choices=BOARDS)
    args = parser.parse_args()
    if args.command == "run":
        if args.board is None:
            parser.error("run requires --board")
        run(args.board)
    else:
        {"register": register, "prepare": prepare, "finalize": finalize}[args.command]()
