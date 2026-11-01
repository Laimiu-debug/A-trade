"""Frozen whole-cohort continuous comparison of reclaim market admission."""
from argparse import ArgumentParser
from copy import deepcopy
import ast
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


U = module("_alignment_frozen_util", "scripts/run-liquid-trend-breakout.py")
read, write, bound, ref, sha, now, emit = [getattr(U, k) for k in ("read", "write", "bound", "ref", "sha", "now", "emit")]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "reclaim-market-alignment-v1"
PLAN = OUT / "plan.json"
PREVIOUS = BASE / "trend-breakout-retest-v1"
PRIOR = BASE / "reclaim-2026-replication-v1"
BULL = BASE / "bull-reclaim-liquidity-v1"
PROTOCOL = ROOT / "docs/reclaim-market-alignment-v1.json"
BOARDS, POLICIES = ("main", "chinext", "star"), ("original", "liquid", "liquidTrend")


def date_of(value):
    text = str(value)
    return text[:4] + "-" + text[4:6] + "-" + text[6:]


def verify_plan():
    plan = read(PLAN)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    for name in ("protocol", "previousPlan", "previousValidation", "previousInputs", "continuationSnapshot", "bullPlan", "bullValidation", "laterInputs"):
        bound(plan[name])
    for item in plan["sources"].values():
        bound(item)
    for item in plan["indices"].values():
        bound(item)
    for pair in plan["originalSchedules"].values():
        for item in pair:
            bound(item)
    return plan


def controlled_execution_copy_check():
    old = ast.parse((ROOT / "scripts/run-trend-breakout-retest.py").read_text("utf-8"))
    new = ast.parse(Path(__file__).read_text("utf-8"))
    for name in ("run", "finalize"):
        a = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == name)
        b = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == name)
        if ast.dump(a) != ast.dump(b):
            raise ValueError("Execution/account audit copy differs: " + name)


def register():
    if OUT.exists():
        raise ValueError("Study exists; preserve prior attempts")
    prior_plan_path = PREVIOUS / "plan.execution-v2.json"
    previous, proof = read(prior_plan_path), read(PREVIOUS / "validation.json")
    bull, bull_proof, later_inputs = read(BULL / "plan.json"), read(BULL / "validation.json"), read(PRIOR / "input-validation.json")
    if (not proof["passed"] or proof["readout"] != ref(PREVIOUS / "readout.json") or
            not bull_proof["passed"] or bull_proof["readout"] != ref(BULL / "readout.json") or not later_inputs["passed"]):
        raise ValueError("Prior source/schedule/account proof binding differs")
    controlled_execution_copy_check()
    rules = read(PROTOCOL)
    pins = dict(previous["sourcePins"])
    for path, digest in pins.items():
        bound({"path": path, "sha256": digest})
    for path in ("docs/reclaim-market-alignment-v1.json", "scripts/run-reclaim-market-alignment.py",
                 "service/research_reclaim_market_alignment.py", "scripts/verify-reclaim-market-alignment.py",
                 "tests/test_research_reclaim_market_alignment.py"):
        pins[path] = sha(ROOT / path)
    schedules = {row["board"]: [bull["originalSchedules"][row["board"]], row["schedules"]["original"]] for row in later_inputs["groups"]}
    for pair in schedules.values():
        for item in pair:
            bound(item)
    config = deepcopy(previous["config"])
    config["strategies"][0]["id"] = "strongUndercutReclaimV1"
    config["allocation"]["strategyIds"] = ["strongUndercutReclaimV1"]
    if (config["startDate"], config["endDate"]) != (rules["scope"]["startDate"], rules["scope"]["endDate"]):
        raise ValueError("Account interval differs")
    OUT.mkdir()
    write(OUT / "continuation-before.json", read(BASE / "goal-continuation-20261018.json"))
    plan = {"schema": OUT.name, "registeredAt": now(), "rules": rules, "protocol": ref(PROTOCOL),
        "previousPlan": ref(prior_plan_path), "previousValidation": ref(PREVIOUS / "validation.json"),
        "previousInputs": ref(PREVIOUS / "input-validation.json"), "bullPlan": ref(BULL / "plan.json"),
        "bullValidation": ref(BULL / "validation.json"), "laterInputs": ref(PRIOR / "input-validation.json"),
        "sources": previous["sources"], "indices": previous["indices"], "originalSchedules": schedules,
        "config": config, "sourcePins": pins, "continuationSnapshot": ref(OUT / "continuation-before.json"),
        "executionAndFinalAuditStaticCopiesAstEqual": True, "workspaceJsonBefore": U.workspace_snapshot(),
        "newAccountsPlanned": 9, "newStrategyTrialsPlanned": 1, "actualCutoff": previous["actualCutoff"],
        "missingLatestSessions": previous["missingLatestSessions"], "corporateActionResearchExpansionStoppedByHuman": True,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    write(PLAN, plan)
    for name in pins:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    emit({"registered": True, "plan": ref(PLAN), "newAccountsPlanned": 9})


def prepare():
    from service.research_reclaim_market_alignment import build_admissions
    plan = verify_plan()
    names = ("test_research_reclaim_market_alignment", "test_research_undercut_reclaim", "test_research_liquid_trend_breakout",
        "test_research_trend_breakout_retest", "test_research_volatility_trail", "test_research_fixed_signal_account",
        "test_research_account_fees", "test_research_account_outcomes")
    argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", *["tests." + n for n in names]]
    tests = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
    write(OUT / "pre-account-tests.json", {"argv": argv, "exitCode": tests.returncode, "stdout": tests.stdout,
        "stderr": tests.stderr, "passed": tests.returncode == 0, "completedAt": now()})
    if tests.returncode:
        raise ValueError("Pre-account checks failed")
    stock_oracle = module("_alignment_stock_reference", "scripts/verify-undercut-reclaim-timing.py")
    gate_oracle = module("_alignment_macro_reference", "scripts/verify-reclaim-market-alignment.py")
    indices = {symbol: {date_of(r[0]): {"close": r[4] / 100, "amount": r[5]} for r in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes())}
        for symbol, item in plan["indices"].items()}
    amounts = {d: row["amount"] + indices["sz399106"][d]["amount"] for d, row in indices["sh000001"].items() if d in indices["sz399106"]}
    closes = {s: {d: row["close"] for d, row in indices[s].items()} for s in ("sh000001", "sz399106")}
    write(OUT / "market.json", {"amountByDate": amounts, "closesByIndex": closes,
        "basis": "TDX broad SH+SZ composite index amount CNY, and separate raw SH/SZ composite closes; not all-A exact official turnover"})
    start, end = plan["config"]["startDate"], plan["config"]["endDate"]
    groups = []
    for board in BOARDS:
        emit({"prepareStart": board})
        data = read(bound(plan["sources"][board]))
        original = [row for item in plan["originalSchedules"][board] for row in read(bound(item)) if start <= row["signalDate"] <= end]
        original.sort(key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"]))
        indexes = stock_oracle.source_index(data, end)
        stock = stock_oracle.reference_build(indexes, start, end, progress=False)
        stock_oracle.same(original, stock["schedules"]["immediate"], "Every continuous original rank/support/Top5 signal")
        production = build_admissions(data["calendar"], original, amounts, closes, start=start, end=end)
        expected, independent_days = gate_oracle.oracle(data["calendar"], original, amounts, closes, start, end)
        if production["schedules"] != expected:
            raise ValueError("Independent macro-admitted schedules differ")
        for a, b in zip(production["daily"], independent_days):
            if (a["date"] != b["date"] or a["combinedStatus"] != b["combinedStatus"] or a["liquidity"]["status"] != b["liquidityStatus"] or
                    a["priceTrend"]["indices"] != b["indexTerms"] or any(a["liquidity"][k] != b[k] for k in ("priorMean20Cny", "currentMean5Cny", "totalAmountCny"))):
                raise ValueError("Independent exact-calendar market windows differ")
        folder = OUT / board
        write(folder / "stock-signal-oracle.json.gz", {"schedules": {"immediate": stock["schedules"]["immediate"]}, "dailyRows": stock["dailyRows"],
            "source": plan["sources"][board], "scope": "full independent original stock rank/support/Top5 replay; not account execution"})
        write(folder / "signal-evidence.json.gz", production)
        schedule_refs = {}
        for policy in POLICIES:
            write(folder / (policy + ".signals.json"), production["schedules"][policy])
            schedule_refs[policy] = ref(folder / (policy + ".signals.json"))
        groups.append({"board": board, "source": plan["sources"][board], "schedules": schedule_refs,
            "evidence": ref(folder / "signal-evidence.json.gz"), "stockOracle": ref(folder / "stock-signal-oracle.json.gz"),
            "counts": production["counts"], "population": len(data["securities"]),
            "independentAllPopulationStockSignalReplay": True, "independentAllMacroWindowsEqual": True})
        emit({"prepareComplete": board, **production["counts"], "population": len(data["securities"])})
        del data, original, indexes, stock, production, expected, independent_days
        gc.collect()
    verify_plan()
    write(OUT / "input-validation.json", {"passed": True, "completedAt": now(), "planSha256": sha(PLAN),
        "all9SchedulesFrozenBeforeAccounts": True, "groups": groups, "market": ref(OUT / "market.json"),
        "pretests": ref(OUT / "pre-account-tests.json"), "independentSourceDecodeInherited": True,
        "sourceScope": "unchanged complete fixed cohorts; independent full stock and macro replay; no expanded action research", "certified": False})
    emit({"inputsValidated": True, "boards": len(groups)})


# The two execution/audit functions below are appended as static AST-equivalent
# copies of batch53's proven functions. They bind this module's explicit PLAN,
# OUT and POLICIES; no imported module globals are mutated or patched.


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
