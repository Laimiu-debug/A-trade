"""Bull-scope cash-start replication of frozen reclaim/volatility entries.

This transfers one already frozen market-liquidity gate; no threshold sweep or
new exit fitting. Original and liquid accounts are both executed from cash.
"""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "bull-reclaim-liquidity-v1"
PARENT = BASE / "reclaim-volatility-trail-v1"
PREVIOUS = BASE / "liquid-trend-breakout-v1"
PROTOCOL = ROOT / "docs/bull-reclaim-liquidity-v1.json"
POLICIES, BOARDS = ("original", "liquid"), ("main", "chinext", "star")


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


U = module("scripts/run-liquid-trend-breakout.py", "_bull_frozen_util")
read, write, ref, bound, sha, now, emit = [getattr(U, k) for k in ("read", "write", "ref", "bound", "sha", "now", "emit")]


def verify_plan():
    plan = read(OUT / "plan.json")
    for name, expected in plan["sourcePins"].items():
        bound({"path": name, "sha256": expected})
    for name in ("parentPlan", "parentValidation", "parentReadoutValidation", "previousReadout", "previousValidation", "market", "protocol", "continuationSnapshot"):
        bound(plan[name])
    for item in plan["benchmarks"]:
        bound(item)
    return plan


def register():
    if OUT.exists():
        raise ValueError("Already registered; preserve any previous attempt")
    parent, previous, protocol = read(PARENT / "plan.json"), read(PREVIOUS / "plan.json"), read(PROTOCOL)
    for name in ("validation.json", "readout-validation.json"):
        proof = read(PARENT / name)
        if not proof["passed"] or proof["planSha256"] != sha(PARENT / "plan.json"):
            raise ValueError("Inherited reclaim proof not bound")
    previous_proof = read(PREVIOUS / "validation.json")
    if not previous_proof["passed"] or previous_proof["readout"] != ref(PREVIOUS / "readout.json"):
        raise ValueError("Batch50 outcome proof not bound")
    pins = dict(parent["sourcePins"])
    for name, value in previous["sourcePins"].items():
        if name in pins and pins[name] != value:
            raise ValueError("Conflicting inherited source pin: " + name)
        pins[name] = value
    for name, expected in pins.items():
        bound({"path": name, "sha256": expected})
    for name in ("docs/bull-reclaim-liquidity-v1.json", "scripts/run-bull-reclaim-liquidity.py",
                 "scripts/verify-reclaim-volatility-trail.py", "scripts/verify-liquid-trend-breakout.py",
                 "tests/test_research_volatility_trail.py"):
        pins[name] = sha(ROOT / name)
    OUT.mkdir()
    write(OUT / "continuation-before.json", read(BASE / "goal-continuation-20261018.json"))
    config = deepcopy(parent["periods"]["2024-2025"]["policyConfigs"]["volatilityTrail"])
    config.update({k: protocol["scope"][k] for k in ("startDate", "endDate")})
    if config["allocation"]["strategyIds"] != ["strongUndercutReclaimV1"]:
        raise ValueError("Original strategy identity changed")
    sources = deepcopy(parent["sourceData"]["2024-2025"])
    schedules = deepcopy(parent["baselineSchedules"]["2024-2025"])
    for board in BOARDS:
        if sources[board]["accountData"] != previous["sourceData"][board]["accountData"]:
            raise ValueError("Source cohort changed between batches")
        for value in list(sources[board].values()) + [schedules[board]]:
            if isinstance(value, dict) and set(value) >= {"path", "sha256"}:
                bound(value)
    market = read(bound(previous["market"]))
    if not market["passed"] or not market["proxy"]:
        raise ValueError("Declared batch50 proxy required")
    benchmark_refs = read(PREVIOUS / "presentation.json")["benchmarkSources"]
    for item in benchmark_refs:
        bound(item)
    plan = {"schema": OUT.name, "registeredAt": now(), "parentPlan": ref(PARENT / "plan.json"),
            "parentValidation": ref(PARENT / "validation.json"), "parentReadoutValidation": ref(PARENT / "readout-validation.json"),
            "previousReadout": ref(PREVIOUS / "readout.json"), "previousValidation": ref(PREVIOUS / "validation.json"),
            "continuationSnapshot": ref(OUT / "continuation-before.json"), "protocol": ref(PROTOCOL), "rules": protocol,
            "sourceData": sources, "originalSchedules": schedules, "config": config, "market": previous["market"],
            "benchmarks": benchmark_refs, "sourcePins": pins, "workspaceJsonBefore": U.workspace_snapshot(),
            "newAccounts": 6, "holdoutClaim": False, "certified": False, "targetAchieved": False}
    write(OUT / "plan.json", plan)
    for name in pins:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    emit({"registered": True, "plan": ref(OUT / "plan.json"), "accountsPlanned": 6})


def prepare():
    import math
    from service.research_liquid_trend_breakout import liquidity_state
    plan = verify_plan()
    commands = []
    for pattern in ("test_research_volatility_trail.py", "test_research_liquid_trend_breakout.py", "test_research_fixed_signal_account.py",
                    "test_research_account_fees.py", "test_research_account_outcomes.py"):
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"]
        ran = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
        commands.append({"argv": argv, "exitCode": ran.returncode, "stdout": ran.stdout, "stderr": ran.stderr})
        if ran.returncode:
            write(OUT / "pretests-failed.json", {"commands": commands, "at": now()})
            raise ValueError("Pre-account tests failed")
    risk = module("scripts/verify-reclaim-volatility-trail.py", "_bull_independent_risk")
    self_test = risk.self_test()
    write(OUT / "pre-account-tests.json", {"passed": True, "commands": commands, "riskOracleSelfTest": self_test,
                                          "completedAt": now(), "planSha256": sha(OUT / "plan.json")})
    market_rows = read(bound(plan["market"]))["rows"]
    market = {r["date"]: r["totalAmountCny"] for r in market_rows}
    groups = []
    for board in BOARDS:
        dataset = read(bound(plan["sourceData"][board]["accountData"]))
        calendar, start, end = dataset["calendar"], plan["config"]["startDate"], plan["config"]["endDate"]
        original = read(bound(plan["originalSchedules"][board]))
        baseline = [r for r in original if start <= r["signalDate"] <= end]
        if len({(r['signalDate'], r['symbol']) for r in original}) != len(original):
            raise ValueError("Duplicate original opportunity")
        daily, passing = [], set()
        for at, day in enumerate(calendar):
            if not start <= day <= end:
                continue
            state = liquidity_state(calendar, at, market)
            values = [market.get(d) for d in calendar[max(0, at-20):at+1]]
            known = len(values) == 21 and all(type(v) in (int,float) and math.isfinite(v) and v > 0 for v in values)
            expected = "unknown" if not known else "passed" if values[-1] >= 1e12 and math.fsum(values[-5:])/5 >= math.fsum(values[:-1])/20 else "failed"
            if state["status"] != expected:
                raise ValueError("Independent liquidity window differs")
            if expected == "passed":
                passing.add(day)
            daily.append({"date": day, **state})
        liquid = [r for r in baseline if r["signalDate"] in passing]
        # A second direct selection over the original file must retain identities,
        # original relative order and gaps, with no scorer or position filter.
        independent = [dict(r) for r in original if start <= r["signalDate"] <= end and r["signalDate"] in passing]
        if independent != liquid or any(not 1 <= r["originalRank"] <= 5 for r in baseline):
            raise ValueError("Frozen rank/date restriction differs")
        folder = OUT / board
        write(folder / "daily-market.json", daily)
        refs = {}
        for policy, rows in (("original", baseline), ("liquid", liquid)):
            write(folder / (policy + ".signals.json"), rows)
            refs[policy] = ref(folder / (policy + ".signals.json"))
        groups.append({"board": board, "source": plan["sourceData"][board]["accountData"],
                       "originalSourceSchedule": plan["originalSchedules"][board], "schedules": refs,
                       "marketEvidence": ref(folder / "daily-market.json"), "counts": {"original": len(baseline), "liquid": len(liquid)},
                       "targetSessions": len(daily), "liquidSessions": len(passing), "independentAdmissionVerified": True})
        emit({"prepared": board, "signals": groups[-1]["counts"]})
        del dataset
        gc.collect()
    verify_plan()
    write(OUT / "input-validation.json", {"passed": True, "completedAt": now(), "planSha256": sha(OUT / "plan.json"),
        "tests": ref(OUT / "pre-account-tests.json"), "groups": groups, "all6SchedulesFrozenBeforeAccounts": True, "accountsRun": 0})


def run(board):
    from service.research_volatility_trail import VolatilityTrailAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan, proof = verify_plan(), read(OUT / "input-validation.json")
    if not proof["passed"] or proof["planSha256"] != sha(OUT / "plan.json") or not proof["all6SchedulesFrozenBeforeAccounts"]:
        raise ValueError("Six frozen verified inputs required")
    group = next(g for g in proof["groups"] if g["board"] == board)
    folder = OUT / board
    write(folder / "run-start.json", {"startedAt": now(), "plan": ref(OUT / "plan.json"), "inputs": ref(OUT / "input-validation.json")})
    data, rows = read(bound(group["source"])), []
    for policy in POLICIES:
        schedule = read(bound(group["schedules"][policy]))
        emit({"accountStart": [board, policy], "signals": len(schedule)})
        engine = VolatilityTrailAccount(data, schedule, deepcopy(plan["config"]), entry_weight=.2, fee_policy=LegacyFeePolicy())
        engine.step(20_000)
        result, checkpoint = engine.result(), engine.checkpoint()
        summary = summarize_account(result, checkpoint, policy_id=policy, policy_label=policy)
        if result["status"] != "completed" or summary["quality"]["integrityIssues"] or result["research"]["identity"]["signalSchedule"] != schedule:
            raise ValueError("Account not complete or binding/reconciliation failed")
        for suffix, value in (("result.json.gz", result), ("checkpoint.json.gz", checkpoint), ("summary.json", summary)):
            write(folder / (policy + "." + suffix), value)
        rows.append({"board": board, "policy": policy, "schedule": group["schedules"][policy],
                     **{key: ref(folder / (policy + "." + suffix)) for key, suffix in
                        (("result", "result.json.gz"), ("checkpoint", "checkpoint.json.gz"), ("summary", "summary.json"))}})
        c = summary["closedEpisodes"]
        emit({"accountComplete": [board, policy], "returnPct": summary["account"]["netReturnPct"],
              "drawdownPct": summary["account"]["maxDrawdownPct"], "R": c["payoffRatio"], "PF": c["profitFactor"], "closed": c["count"]})
        del engine, result, checkpoint, summary
        gc.collect()
    verify_plan()
    write(OUT / (board + ".completion.json"), {"completedAt": now(), "accounts": 2, "rows": rows,
          "plan": ref(OUT / "plan.json"), "inputs": ref(OUT / "input-validation.json")})


def finalize():
    import struct
    plan, rows, audits = verify_plan(), [], []
    risk = module("scripts/verify-reclaim-volatility-trail.py", "_bull_risk_final")
    numbers = module("scripts/verify-liquid-trend-breakout.py", "_bull_numbers_final")
    indices = {}
    for item in plan["benchmarks"]:
        values = {}
        for r in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes()):
            d = str(r[0]); values[d[:4]+"-"+d[4:6]+"-"+d[6:]] = r[4]/100
        indices[item["symbol"]] = values
    for board in BOARDS:
        completion = read(OUT / (board + ".completion.json"))
        if completion["accounts"] != 2 or completion["plan"] != ref(OUT / "plan.json") or completion["inputs"] != ref(OUT / "input-validation.json"):
            raise ValueError("Board receipt binding differs")
        data = read(bound(plan["sourceData"][board]["accountData"]))
        for item in completion["rows"]:
            result, checkpoint, summary = [read(bound(item[k])) for k in ("result", "checkpoint", "summary")]
            schedule = read(bound(item["schedule"]))
            if result["config"] != plan["config"] or result["research"]["identity"]["signalSchedule"] != schedule:
                raise ValueError("Frozen config/schedule changed")
            independent_risk = risk.daily_risk_reference(result, checkpoint, data, "volatilityTrail")
            numerical = numbers.account_numbers(result)
            c = summary["closedEpisodes"]
            expected = {"closed": c["count"], "closedNetPnl": c["netPnlAmount"],
                        **{k: c[k] for k in ("payoffRatio", "profitFactor", "meanWinReturnPct", "meanLossReturnPct")},
                        **{k: summary["account"][k] for k in ("netReturnPct", "maxDrawdownPct")}}
            if not all(U.close(numerical[k], v) for k, v in expected.items()):
                raise ValueError("Independent outcomes differ")
            benchmark_symbol = {"main":"sh000300", "chinext":"sz399006", "star":"sh000688"}[board]
            prices = indices[benchmark_symbol]
            benchmark_return = (prices[plan["config"]["endDate"]]/prices[plan["config"]["startDate"]]-1)*100
            period_rows = [{"id": p["id"], "start": p["requestedStart"], "end": p["requestedEnd"], "returnPct": p["returnPct"],
                            "maxDrawdownPct": p["maxDrawdownPct"], "closed": p["closedEpisodes"]["count"],
                            "R": p["closedEpisodes"]["payoffRatio"], "PF": p["closedEpisodes"]["profitFactor"],
                            "averageExposurePct": p["exposurePct"]["mean"]} for p in summary["periods"]]
            audits.append({"board": board, "policy": item["policy"], "everyHeldDayIndependentRisk": independent_risk})
            rows.append({"board": board, "policy": item["policy"], **numerical, "winRatePct": c["winRatePct"],
                         "medianWinReturnPct": c["medianWinReturnPct"], "medianLossReturnPct": c["medianLossReturnPct"],
                         "maxConsecutiveLosses": c["maxConsecutiveLosses"], "returnQuantilesPct": c["returnQuantilesPct"],
                         "averageExposurePct": summary["periods"][0]["exposurePct"]["mean"],
                         "top5ProfitSharePct": summary["concentration"]["top5ProfitSharePct"],
                         "openCount": summary["openPositions"]["count"], "openUnrealizedPnl": summary["reconciliation"]["openPositionUnrealizedPnl"],
                         "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]),
                         "benchmarkSymbol": benchmark_symbol, "benchmarkReturnPct": benchmark_return,
                         "boardExcessPercentagePoints": numerical["netReturnPct"]-benchmark_return,
                         "periods": period_rows, "evidence": item})
            del result, checkpoint, summary
            gc.collect()
        del data
        gc.collect()
    if len(rows) != 6 or len({(r["board"], r["policy"]) for r in rows}) != 6:
        raise ValueError("All6 distinct accounts required")
    if U.workspace_snapshot() != plan["workspaceJsonBefore"]:
        raise ValueError("User workspace changed; inspect rather than claim unchanged")
    readout = {"schema": OUT.name + "-readout", "completedAt": now(), "plan": ref(OUT / "plan.json"), "rows": rows,
               "newAccounts": 6, "referenceAccountsReused": 0, "primaryScope": "post924 cash-start bull", "bearProfitRequired": False,
               "holdoutClaim": False, "certified": False, "targetAchieved": False, "userJsonFilesUnchanged": True}
    write(OUT / "readout.json", readout)
    write(OUT / "validation.json", {"passed": True, "completedAt": now(), "plan": ref(OUT / "plan.json"), "readout": ref(OUT / "readout.json"),
          "accounts": 6, "independentEveryHeldDayRiskAudits": audits, "independentTradeCurveArithmetic": True,
          "originalScheduleRestrictionAndLiquidityOracle": True, "cashPnlFeeBridges": "existing audited summarizer reconciled; no new second account execution",
          "userJsonFilesUnchanged": True, "independentSecondAccountEngine": False})
    emit({"completed": True, "readout": ref(OUT / "readout.json"), "accounts": 6, "targetAchieved": False})


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
