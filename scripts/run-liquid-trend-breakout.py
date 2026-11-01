"""Frozen, isolated 18-account liquidity/payoff development experiment.

Stages: register -> prepare (tests + full separate schedule oracle) -> run per
board -> finalize (independent trade/curve arithmetic and immutable receipts).
Never adjusts parameters from observed outcomes; never writes user workspace.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts/strategy-search/liquid-trend-breakout-v1"
PRIOR = ROOT / "artifacts/strategy-search/volume-lead-breakout-v1"
PROTOCOL = ROOT / "docs/liquid-trend-breakout-v1.json"
MARKET = ROOT / "artifacts/strategy-search/market-turnover-proxy-v1/completion.json"
REGISTRY = ROOT / "artifacts/strategy-search/program.json"
SID = "liquidTrendBreakoutV1"
POLICIES = ("breakout120", "trendEarly", "trendEarlyLiquid")
BOARDS = ("main", "chinext", "star")
PERIODS = ("bear2024", "bull924")


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ref(path):
    return {"path": Path(path).relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    if not path.is_relative_to(ROOT) or sha(path) != item["sha256"]:
        raise ValueError("Source drift or foreign path: " + item["path"])
    return path


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path).endswith(".gz"):
        with gzip.open(path, "xt", encoding="utf-8", compresslevel=1) as stream:
            json.dump(obj, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    else:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, allow_nan=False), flush=True)


def oracle_module():
    spec = importlib.util.spec_from_file_location("_liquid_independent", ROOT / "scripts/verify-liquid-trend-breakout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workspace_snapshot():
    folder = Path(os.environ["APPDATA"]) / "A-trade/workspace"
    # Hash only. Do not archive user documents, credentials, or account contents.
    return {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob("*.json")} if folder.is_dir() else {}


def register():
    if OUT.exists():
        raise ValueError("Study already registered; do not overwrite a trial")
    prior, protocol, market = read(PRIOR / "plan.json"), read(PROTOCOL), read(MARKET)
    if not market["passed"] or market["failures"] or len(market["rows"]) != market["expectedDates"]:
        raise ValueError("Complete official market source required")
    for name in ("validation.json", "readout-validation.json"):
        proof = read(PRIOR / name)
        if not proof["passed"] or proof["planSha256"] != sha(PRIOR / "plan.json"):
            raise ValueError("Prior source/account audit not bound")
    # Retain the inherited source closure; a changed old implementation is not
    # silently passed off as the frozen validated account engine.
    source_pins = dict(prior["sourcePins"])
    for name, expected in source_pins.items():
        if sha(ROOT / name) != expected:
            raise ValueError("Inherited source changed: " + name)
    for name in ("docs/liquid-trend-breakout-v1.json", "scripts/fetch-market-turnover-official.py",
                 "scripts/build-market-turnover-proxy.py", "scripts/run-liquid-trend-breakout.py", "scripts/verify-liquid-trend-breakout.py",
                 "service/research_liquid_trend_breakout.py", "tests/test_research_liquid_trend_breakout.py"):
        source_pins[name] = sha(ROOT / name)
    sources = deepcopy(prior["sourceData"]["2024-2025"])
    for source in sources.values():
        for value in source.values():
            if isinstance(value, dict) and set(value) >= {"path", "sha256"}:
                bound(value)
    for row in market["rows"]:
        for item in row["sources"].values():
            bound(item)
    config = deepcopy(prior["periods"]["2024-2025"]["policyConfigs"]["trail10"])
    config["strategies"][0]["id"] = SID
    config["allocation"]["strategyIds"] = [SID]
    if not (config["stopLossPct"] == .05 and config["trailingStopPct"] == .1 and config["takeProfitPct"] == config["maxHoldingDays"] == 0):
        raise ValueError("Exit settings differ")
    plan = {"schema": OUT.name, "registeredAt": now(), "priorPlan": ref(PRIOR / "plan.json"),
            "priorValidation": ref(PRIOR / "validation.json"), "priorReadoutValidation": ref(PRIOR / "readout-validation.json"),
            "programBefore": ref(REGISTRY), "protocol": ref(PROTOCOL), "rules": protocol,
            "sourceData": sources, "market": ref(MARKET), "configTemplate": config,
            "sourcePins": source_pins, "workspaceJsonBefore": workspace_snapshot(),
            "newAccountsPlanned": 18, "referenceAccountsReused": 0, "parameterSearch": False,
            "holdoutClaim": False, "certified": False, "targetAchieved": False}
    OUT.mkdir()
    write(OUT / "plan.json", plan)
    write(OUT / "program-before.json", read(REGISTRY))
    for name in source_pins:
        path = OUT / "frozen-sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as f:
            f.write((ROOT / name).read_bytes())
    emit({"registered": True, "plan": ref(OUT / "plan.json"), "newAccountsPlanned": 18})


def verify_plan():
    plan = read(OUT / "plan.json")
    for name, expected in plan["sourcePins"].items():
        bound({"path": name, "sha256": expected})
    for key in ("priorPlan", "priorValidation", "priorReadoutValidation", "programBefore", "protocol", "market"):
        bound(plan[key])
    return plan


def prepare():
    from service.research_liquid_trend_breakout import build_signals
    plan = verify_plan()
    commands = []
    for pattern in ("test_research_liquid_trend_breakout.py", "test_research_fixed_signal_account.py",
                    "test_research_account_fees.py", "test_research_account_outcomes.py"):
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"]
        ran = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
        commands.append({"argv": argv, "exitCode": ran.returncode, "stdout": ran.stdout, "stderr": ran.stderr})
        if ran.returncode:
            write(OUT / "pretests-failed.json", {"commands": commands, "at": now()})
            raise ValueError("Pre-account tests failed")
    write(OUT / "pre-account-tests.json", {"passed": True, "completedAt": now(), "commands": commands,
                                          "planSha256": sha(OUT / "plan.json")})
    raw_market = read(bound(plan["market"]))
    market = {row["date"]: row["totalAmountCny"] for row in raw_market["rows"]}
    verifier, groups = oracle_module(), []
    for board in BOARDS:
        source = plan["sourceData"][board]["accountData"]
        dataset = read(bound(source))
        for period in PERIODS:
            spec = plan["rules"]["periods"][period]
            start, end = spec["startDate"], spec["endDate"]
            emit({"prepareStart": [period, board]})
            result = build_signals(dataset, market, start=start, end=end)
            independent, counts = verifier.oracle(dataset, market, start, end)
            if independent != result["schedules"]:
                raise ValueError("Independent full-population schedules differ")
            for row in result["daily"]:
                if any(row[k] != counts[row["date"]][k] for k in ("commonCandidates", "unknownWindow")):
                    raise ValueError("Independent full-population counts differ")
                if row["knownFalse"] + row["unknownWindow"] + row["commonCandidates"] != row["fixedPopulation"]:
                    raise ValueError("Population not conserved")
            folder = OUT / period / board
            write(folder / "evidence.json.gz", {k: v for k, v in result.items() if k != "schedules"})
            schedules = {}
            for policy in POLICIES:
                path = folder / (policy + ".signals.json")
                write(path, result["schedules"][policy])
                schedules[policy] = ref(path)
            groups.append({"period": period, "board": board, "source": source,
                           "schedules": schedules, "evidence": ref(folder / "evidence.json.gz"),
                           "metadata": result["metadata"], "independentScheduleReplayPassed": True})
            emit({"prepared": [period, board], "signals": result["metadata"]["signals"]})
            del result, independent
            gc.collect()
        del dataset
        gc.collect()
    verify_plan()
    write(OUT / "input-validation.json", {"passed": True, "completedAt": now(), "planSha256": sha(OUT / "plan.json"),
        "tests": ref(OUT / "pre-account-tests.json"), "groups": groups, "all18SchedulesFrozenBeforeAccounts": True,
        "oracle": ref(ROOT / "scripts/verify-liquid-trend-breakout.py"), "newAccountsExecuted": 0})


def run(board):
    from service.research_fixed_signal_account import FixedSignalAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan = verify_plan()
    proof = read(OUT / "input-validation.json")
    if not proof["passed"] or proof["planSha256"] != sha(OUT / "plan.json") or not proof["all18SchedulesFrozenBeforeAccounts"]:
        raise ValueError("Input validation not frozen")
    data = read(bound(plan["sourceData"][board]["accountData"]))
    rows = []
    for group in [g for g in proof["groups"] if g["board"] == board]:
        period = group["period"]
        config = deepcopy(plan["configTemplate"])
        config.update({k: plan["rules"]["periods"][period][k] for k in ("startDate", "endDate")})
        folder = OUT / period / board
        write(folder / "run-start.json", {"startedAt": now(), "planSha256": sha(OUT / "plan.json"),
                                         "inputs": ref(OUT / "input-validation.json")})
        for policy in POLICIES:
            schedule = read(bound(group["schedules"][policy]))
            emit({"accountStart": [period, board, policy], "signals": len(schedule)})
            engine = FixedSignalAccount(data, schedule, config, entry_weight=.2, fee_policy=LegacyFeePolicy())
            engine.step(20_000)
            result, checkpoint = engine.result(), engine.checkpoint()
            if result["status"] != "completed" or result["research"]["identity"]["signalSchedule"] != schedule:
                raise ValueError("Account status or schedule binding differs")
            summary = summarize_account(result, checkpoint, policy_id=policy, policy_label=policy)
            if summary["quality"]["integrityIssues"]:
                raise ValueError("Account reconciliation issue")
            write(folder / (policy + ".result.json.gz"), result)
            write(folder / (policy + ".checkpoint.json.gz"), checkpoint)
            write(folder / (policy + ".summary.json"), summary)
            rows.append({"period": period, "board": board, "policy": policy, "schedule": group["schedules"][policy],
                         **{key: ref(folder / (policy + "." + filename)) for key, filename in
                            (("result", "result.json.gz"), ("checkpoint", "checkpoint.json.gz"), ("summary", "summary.json"))}})
            emit({"accountComplete": [period, board, policy], "returnPct": summary["account"]["netReturnPct"],
                  "payoff": summary["closedEpisodes"]["payoffRatio"], "PF": summary["closedEpisodes"]["profitFactor"],
                  "closed": summary["closedEpisodes"]["count"]})
            del engine, result, checkpoint, summary
            gc.collect()
    verify_plan()
    write(OUT / (board + ".completion.json"), {"completedAt": now(), "rows": rows,
          "planSha256": sha(OUT / "plan.json"), "inputs": ref(OUT / "input-validation.json"), "accounts": len(rows)})


def close(a, b):
    return a is None and b is None or (a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8))


def finalize():
    plan, verifier, rows, refs = verify_plan(), oracle_module(), [], []
    for board in BOARDS:
        receipt = read(OUT / (board + ".completion.json"))
        if receipt["accounts"] != 6 or receipt["planSha256"] != sha(OUT / "plan.json") or receipt["inputs"] != ref(OUT / "input-validation.json"):
            raise ValueError("Board completion binding differs")
        refs.append(ref(OUT / (board + ".completion.json")))
        for item in receipt["rows"]:
            result = read(bound(item["result"]))
            summary = read(bound(item["summary"]))
            bound(item["checkpoint"])
            schedule = read(bound(item["schedule"]))
            if result["research"]["identity"]["signalSchedule"] != schedule:
                raise ValueError("Final schedule binding differs")
            numbers = verifier.account_numbers(result)
            expected = {"closed": summary["closedEpisodes"]["count"], "closedNetPnl": summary["closedEpisodes"]["netPnlAmount"],
                        **{k: summary["closedEpisodes"][k] for k in ("payoffRatio", "profitFactor", "meanWinReturnPct", "meanLossReturnPct")},
                        **{k: summary["account"][k] for k in ("netReturnPct", "maxDrawdownPct")}}
            if not all(close(numbers[k], v) for k, v in expected.items()):
                raise ValueError("Independent trade/curve arithmetic differs")
            t = plan["rules"]["screeningTargets"]
            checks = {"payoff": numbers["payoffRatio"] is not None and numbers["payoffRatio"] >= t["netClosedMeanWinOverAbsoluteMeanLossAtLeast"],
                      "PF": numbers["profitFactor"] is not None and numbers["profitFactor"] >= t["closedCashProfitFactorAtLeast"],
                      "ordinaryLoss": numbers["meanLossReturnPct"] is not None and -numbers["meanLossReturnPct"] <= t["absoluteMeanLossReturnPctAtMost"],
                      "drawdown": numbers["maxDrawdownPct"] <= t["continuousMaxDrawdownPctAtMost"],
                      "sample": numbers["closed"] >= t["closedEpisodesAtLeast"], "accountPositive": numbers["netReturnPct"] > 0}
            period_rows = [{k: p.get(k) for k in ("id", "start", "end", "returnPct", "maxDrawdownPct", "averageExposurePct")}
                           for p in summary["periods"]]
            rows.append({"period": item["period"], "board": board, "policy": item["policy"], **numbers,
                         "winRatePct": summary["closedEpisodes"]["winRatePct"], "returnQuantilesPct": summary["closedEpisodes"]["returnQuantilesPct"],
                         "maxConsecutiveLosses": summary["closedEpisodes"]["maxConsecutiveLosses"],
                         "averageExposurePct": summary["periods"][0].get("averageExposurePct"),
                         "top5ProfitSharePct": summary["concentration"]["top5ProfitSharePct"],
                         "openCount": summary["openPositions"]["count"],
                         "openUnrealizedPnl": summary["reconciliation"]["openPositionUnrealizedPnl"],
                         "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]),
                         "proposedTargetChecks": checks, "allProposedBullScreensPass": item["period"] == "bull924" and all(checks.values()),
                         "continuousSubperiods": period_rows, "evidence": item})
            del result, summary
            gc.collect()
    if len(rows) != 18 or len({(r["period"], r["board"], r["policy"]) for r in rows}) != 18:
        raise ValueError("All18 distinct accounts required")
    snapshot_after = workspace_snapshot()
    if snapshot_after != plan["workspaceJsonBefore"]:
        raise ValueError("User workspace JSON changed during study; inspect before claiming isolation")
    readout = {"schema": OUT.name + "-readout", "completedAt": now(), "plan": ref(OUT / "plan.json"),
               "inputs": ref(OUT / "input-validation.json"), "accountCompletions": refs, "rows": rows,
               "newAccounts": 18, "primaryEvaluation": "bull924", "bearProfitTargetRequired": False,
               "proposedScreenPasses": [{k: r[k] for k in ("board", "policy")} for r in rows if r["allProposedBullScreensPass"]],
               "numericalTargetsHumanConfirmed": False, "userJsonFilesUnchanged": True,
               "holdoutClaim": False, "certified": False, "targetAchieved": False,
               "interpretation": "Historical screen passes, if any, remain adaptive development and require unseen validation. No profit or future-market certification."}
    write(OUT / "readout.json", readout)
    write(OUT / "validation.json", {"passed": True, "completedAt": now(), "readout": ref(OUT / "readout.json"),
        "independentFullPopulationSchedules": 18, "independentTradeCurveSummaries": 18,
        "cashPnlFeesReconciledWithExistingAuditedSummarizer": True, "independentSecondEngineReplay": False,
        "userJsonFilesUnchanged": True, "sourcePins": plan["sourcePins"]})
    emit({"completed": True, "readout": ref(OUT / "readout.json"), "proposedScreenPasses": readout["proposedScreenPasses"], "targetAchieved": False})


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
