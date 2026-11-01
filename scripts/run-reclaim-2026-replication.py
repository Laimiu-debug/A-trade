"""Frozen later-period replication, with level and expansion disentangled.

Keep every old cohort member and all policies. Source creation and independent
full signal replay must finish before any new account starts.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import date
import gc
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import importlib.util
spec = importlib.util.spec_from_file_location("_replica51", ROOT / "scripts/run-bull-reclaim-liquidity.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
U = H.U
read, write, ref, bound, sha, now, emit = [getattr(U, k) for k in ("read", "write", "ref", "bound", "sha", "now", "emit")]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "reclaim-2026-replication-v1"
PRIOR = BASE / "bull-reclaim-liquidity-v1"
PROTOCOL = ROOT / "docs/reclaim-2026-replication-v1.json"
BOARDS, POLICIES = ("main", "chinext", "star"), ("original", "levelOnly", "expanding")
INDEX_FILES = (("sh", "sh000300"), ("sh", "sh000001"), ("sz", "sz399106"), ("sz", "sz399006"), ("sh", "sh000688"))


def module(path, name):
    return H.module(path, name)


def encoded_day(value):
    return date(value // 10000, value // 100 % 100, value % 100).isoformat()


def stable_bytes(path):
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while reading: " + str(path))
    if not raw or len(raw) % 32:
        raise ValueError("Invalid raw daily record size")
    return raw


def verify_plan():
    plan = read(OUT / "plan.json")
    for name, expected in plan["sourcePins"].items():
        bound({"path": name, "sha256": expected})
    for name in ("previousPlan", "previousReadout", "previousValidation", "protocol", "continuationSnapshot"):
        bound(plan[name])
    for value in plan["indexInputs"].values():
        bound(value)
    return plan


def register():
    from service.trading_calendar import trading_sessions
    if OUT.exists():
        raise ValueError("Study already registered")
    prior, proof, rules = read(PRIOR / "plan.json"), read(PRIOR / "validation.json"), read(PROTOCOL)
    if not proof["passed"] or proof["readout"] != ref(PRIOR / "readout.json"):
        raise ValueError("Batch51 proof not bound")
    source_pins = dict(prior["sourcePins"])
    for name, expected in source_pins.items():
        bound({"path": name, "sha256": expected})
    for name in ("docs/reclaim-2026-replication-v1.json", "scripts/run-reclaim-2026-replication.py",
                 "scripts/materialize-board-coverage-source.py", "scripts/verify-board-coverage-reclaim.py",
                 "scripts/verify-undercut-reclaim-timing.py", "tests/test_research_undercut_reclaim.py", "service/trading_calendar.py"):
        source_pins[name] = sha(ROOT / name)
    index_raw = {}
    latest = {}
    for market, symbol in INDEX_FILES:
        raw = stable_bytes(Path("E:/TDX/vipdoc") / market / "lday" / (symbol + ".day"))
        index_raw[symbol] = raw
        dates = [encoded_day(r[0]) for r in struct.iter_unpack("<IIIIIfII", raw) if encoded_day(r[0]) <= rules["scope"]["requestDate"]]
        latest[symbol] = dates[-1]
    if len(set(latest.values())) != 1:
        raise ValueError("Market/benchmark index cutoffs differ: " + str(latest))
    cutoff = next(iter(latest.values()))
    if cutoff < rules["scope"]["startDate"]:
        raise ValueError("No2026 data available")
    expected = trading_sessions("2026-01-01", rules["scope"]["requestDate"])
    if expected is None:
        raise ValueError("Independent2026 calendar unavailable")
    missing_recent = [d for d in expected if d > cutoff]
    OUT.mkdir()
    write(OUT / "continuation-before.json", read(BASE / "goal-continuation-20261018.json"))
    index_inputs = {}
    for symbol, raw in index_raw.items():
        size = sum(32 for offset in range(0, len(raw), 32) if encoded_day(struct.unpack_from("<I", raw, offset)[0]) <= cutoff)
        target = OUT / "indices" / (symbol + ".day")
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw[:size])
        index_inputs[symbol] = ref(target)
    config = deepcopy(prior["config"])
    config.update(startDate=rules["scope"]["startDate"], endDate=cutoff)
    plan = {"schema": OUT.name, "registeredAt": now(), "previousPlan": ref(PRIOR / "plan.json"),
            "previousReadout": ref(PRIOR / "readout.json"), "previousValidation": ref(PRIOR / "validation.json"),
            "protocol": ref(PROTOCOL), "rules": rules, "sourcePins": source_pins,
            "oldSources": prior["sourceData"], "indexInputs": index_inputs, "config": config,
            "appendedCalendar": [d for d in expected if d <= cutoff], "expectedLatest": expected[-1], "actualCutoff": cutoff,
            "missingLatestSessions": missing_recent, "continuationSnapshot": ref(OUT / "continuation-before.json"),
            "workspaceJsonBefore": U.workspace_snapshot(), "newAccounts": 9,
            "holdoutClaim": False, "chronologicalReplication": True, "certified": False, "targetAchieved": False}
    write(OUT / "plan.json", plan)
    for name in source_pins:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    emit({"registered": True, "actualCutoff": cutoff, "missingLatestSessions": missing_recent, "plan": ref(OUT / "plan.json")})


def tests_and_indices(plan):
    commands = []
    for pattern in ("test_research_undercut_reclaim.py", "test_research_volatility_trail.py", "test_research_liquid_trend_breakout.py",
                    "test_research_fixed_signal_account.py", "test_research_account_fees.py", "test_research_account_outcomes.py"):
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"]
        ran = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
        commands.append({"argv": argv, "exitCode": ran.returncode, "stdout": ran.stdout, "stderr": ran.stderr})
        if ran.returncode:
            write(OUT / "pretests-failed.json", {"commands": commands})
            raise ValueError("Production pretests failed")
    write(OUT / "pre-account-tests.json", {"passed": True, "completedAt": now(), "planSha256": sha(OUT / "plan.json"), "commands": commands})
    result = {}
    for symbol, item in plan["indexInputs"].items():
        result[symbol] = {encoded_day(r[0]): {"date": encoded_day(r[0]), "open": r[1]/100, "high": r[2]/100,
            "low": r[3]/100, "close": r[4]/100, "amount": r[5], "volume": r[6]} for r in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes())}
    return result


def prepare():
    from service.research_undercut_reclaim import build_undercut_reclaim
    from service.research_liquid_trend_breakout import liquidity_state
    plan = verify_plan()
    indices = tests_and_indices(plan)
    parser = module("scripts/materialize-board-coverage-source.py", "_2026_prefix_parser")
    independent_source = module("scripts/verify-board-coverage-reclaim.py", "_2026_independent_prefix")
    independent_signals = module("scripts/verify-undercut-reclaim-timing.py", "_2026_independent_UR")
    groups = []
    market = {d: r["amount"]+indices["sz399106"][d]["amount"] for d, r in indices["sh000001"].items() if d in indices["sz399106"]}
    for board in BOARDS:
        emit({"sourceStart": board})
        old = read(bound(plan["oldSources"][board]["accountData"]))
        calendar = [d for d in old["calendar"] if d <= "2025-12-31"] + plan["appendedCalendar"]
        if calendar != sorted(set(calendar)):
            raise ValueError("Independent merged calendar order")
        for symbol, rows in indices.items():
            if not set(rows) <= set(calendar) or any(d not in rows for d in calendar):
                raise ValueError("Index/independent calendar missingness or off-calendar dates: " + symbol)
        securities, proofs, missing = [], [], []
        for security in old["securities"]:
            symbol = security["symbol"]
            source_path = Path("E:/TDX/vipdoc") / symbol[:2] / "lday" / (symbol + ".day")
            if not source_path.exists():
                securities.append(deepcopy(security)); missing.append(symbol)
                proofs.append({"symbol": symbol, "status": "missingCurrentFileOldHistoryRetained"})
                continue
            raw = stable_bytes(source_path)
            old_bars, prefix = parser.parse_prefix(raw, "2025-12-31", calendar)
            if prefix["prefixSha256"] != security["rawPrefixSha256"] or old_bars != security["bars"]:
                write(OUT / (board+".historical-prefix-failure.json"), {"symbol": symbol, "at": now(), "newPrefix": prefix,
                     "expectedPrefixSha256": security["rawPrefixSha256"], "barEquality": old_bars == security["bars"]})
                raise ValueError("Historical raw prefix changed; inspect before new account: " + symbol)
            bars, current_prefix = parser.parse_prefix(raw, plan["actualCutoff"], calendar)
            decoded, independent_prefix = independent_source.decode_prefix(raw, plan["actualCutoff"])
            if bars != decoded or not parser.account_compatible(bars):
                raise ValueError("Independent source decoding or account envelope differs: " + symbol)
            new = deepcopy(security)
            new.update(bars=bars, rawPrefixSha256=current_prefix["prefixSha256"], sourceSha256=hashlib.sha256(raw).hexdigest())
            securities.append(new)
            proofs.append({"symbol": symbol, "status": "prefixVerified", "oldPrefix": prefix, "newPrefix": current_prefix,
                           "fullFileSha256": hashlib.sha256(raw).hexdigest(), "newObservedSessions": sum(b["date"]>="2026-01-01" for b in bars)})
        data = deepcopy(old)
        data.update(securities=securities, calendar=calendar, startDate=plan["config"]["startDate"], endDate=plan["actualCutoff"],
                    period="2026-replication", source="frozen2024cohort-2026-verified-prefix-extension-v1", calendarSource="frozen-independent-pre2026-plus-pinned2026-exchange-notice")
        data["benchmark"] = {**deepcopy(old["benchmark"]), "bars": [indices["sh000300"][d] for d in calendar]}
        data["universeSelection"] = {**data["universeSelection"], "mode": "unchanged2024cohort-through2026", "newMembersAdded": 0,
                                    "missingCurrentFilesRetained": missing}
        data["universeCoverage"].update(barCount=sum(len(s["bars"]) for s in securities))
        data.pop("fingerprint", None)
        data["fingerprint"] = parser.digest(data)
        folder = OUT / board
        write(folder / "source-proof.json", {"passed": True, "oldSource": plan["oldSources"][board]["accountData"],
               "calendarSessions": len(calendar), "members": len(securities), "missingCurrentFiles": missing, "prefixes": proofs,
               "everyExistingOldPrefixEqual": True, "independentRawDecode": True})
        write(folder / "account-data.json.gz", data)
        emit({"sourceComplete": board, "members": len(securities), "missing": len(missing)})
        evidence = build_undercut_reclaim(data, start=plan["config"]["startDate"], end=plan["actualCutoff"])
        indexes = independent_signals.source_index(data, plan["actualCutoff"])
        oracle = independent_signals.reference_build(indexes, plan["config"]["startDate"], plan["actualCutoff"], progress=False)
        independent_signals.same(evidence["schedules"]["immediate"], oracle["schedules"]["immediate"], "Every2026 original signal")
        independent_signals.same(evidence["dailyRows"], oracle["dailyRows"], "Every2026 full-population rank/setup/Top5")
        original = evidence["schedules"]["immediate"]
        passing, levels, daily_market = set(), set(), []
        for at, day in enumerate(calendar):
            if not plan["config"]["startDate"] <= day <= plan["actualCutoff"]:
                continue
            state = liquidity_state(calendar, at, market)
            value = market.get(day)
            level = type(value) in (int,float) and math.isfinite(value) and value >= 1e12
            values = [market.get(d) for d in calendar[max(0,at-20):at+1]]
            known = len(values)==21 and all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in values)
            expected = "unknown" if not known else "passed" if values[-1]>=1e12 and math.fsum(values[-5:])/5>=math.fsum(values[:-1])/20 else "failed"
            if state["status"] != expected:
                raise ValueError("Independent market gate differs")
            if level: levels.add(day)
            if expected=="passed": passing.add(day)
            daily_market.append({"date":day,**state,"levelOnlyPassed":level})
        schedules = {"original":original,"levelOnly":[r for r in original if r["signalDate"] in levels],
                     "expanding":[r for r in original if r["signalDate"] in passing]}
        refs = {}
        for policy, values in schedules.items():
            write(folder/(policy+".signals.json"),values);refs[policy]=ref(folder/(policy+".signals.json"))
        write(folder/"signal-evidence.json.gz", {k:v for k,v in evidence.items() if k!="schedules"})
        write(folder/"daily-market.json",daily_market)
        groups.append({"board":board,"source":ref(folder/"account-data.json.gz"),"sourceProof":ref(folder/"source-proof.json"),
                       "schedules":refs,"evidence":ref(folder/"signal-evidence.json.gz"),"market":ref(folder/"daily-market.json"),
                       "signals":{k:len(v) for k,v in schedules.items()},"targetSessions":len(daily_market),"levelSessions":len(levels),"expansionSessions":len(passing)})
        emit({"prepared":board,"signals":groups[-1]["signals"],"targetSessions":len(daily_market),"levelSessions":len(levels),"expandingSessions":len(passing)})
        del data, old, securities, evidence, indexes, oracle
        gc.collect()
    verify_plan()
    write(OUT/"input-validation.json",{"passed":True,"completedAt":now(),"planSha256":sha(OUT/"plan.json"),
          "tests":ref(OUT/"pre-account-tests.json"),"groups":groups,"all9SchedulesFrozenBeforeAccounts":True,
          "independentAllPopulationURSignals":True,"independentSourcePrefixes":True,"accountsRun":0})


def run(board):
    from service.research_volatility_trail import VolatilityTrailAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan, proof = verify_plan(),read(OUT/"input-validation.json")
    if not proof["passed"] or proof["planSha256"]!=sha(OUT/"plan.json") or not proof["all9SchedulesFrozenBeforeAccounts"]:
        raise ValueError("Nine frozen validated inputs required")
    group=next(g for g in proof["groups"] if g["board"]==board)
    folder=OUT/board
    write(folder/"run-start.json",{"startedAt":now(),"plan":ref(OUT/"plan.json"),"inputs":ref(OUT/"input-validation.json")})
    data,rows=read(bound(group["source"])),[]
    for policy in POLICIES:
        schedule=read(bound(group["schedules"][policy]));emit({"accountStart":[board,policy],"signals":len(schedule)})
        engine=VolatilityTrailAccount(data,schedule,deepcopy(plan["config"]),entry_weight=.2,fee_policy=LegacyFeePolicy())
        engine.step(20000);result,checkpoint=engine.result(),engine.checkpoint()
        summary=summarize_account(result,checkpoint,policy_id=policy,policy_label=policy)
        if result["status"]!="completed" or summary["quality"]["integrityIssues"] or result["research"]["identity"]["signalSchedule"]!=schedule:
            raise ValueError("Account status/binding/reconciliation failed")
        for suffix,value in (("result.json.gz",result),("checkpoint.json.gz",checkpoint),("summary.json",summary)):
            write(folder/(policy+"."+suffix),value)
        rows.append({"board":board,"policy":policy,"schedule":group["schedules"][policy],
                     **{k:ref(folder/(policy+"."+s)) for k,s in (("result","result.json.gz"),("checkpoint","checkpoint.json.gz"),("summary","summary.json"))}})
        c=summary["closedEpisodes"];emit({"accountComplete":[board,policy],"returnPct":summary["account"]["netReturnPct"],
          "DD":summary["account"]["maxDrawdownPct"],"R":c["payoffRatio"],"PF":c["profitFactor"],"closed":c["count"]})
        del engine,result,checkpoint,summary;gc.collect()
    verify_plan()
    write(OUT/(board+".completion.json"),{"completedAt":now(),"accounts":3,"rows":rows,"plan":ref(OUT/"plan.json"),"inputs":ref(OUT/"input-validation.json")})


def finalize():
    plan,rows,audits=verify_plan(),[],[]
    proof=read(OUT/"input-validation.json")
    risk=module("scripts/verify-reclaim-volatility-trail.py","_2026_final_risk")
    outcomes=module("scripts/verify-liquid-trend-breakout.py","_2026_final_numbers")
    indices={s:{encoded_day(r[0]):r[4]/100 for r in struct.iter_unpack("<IIIIIfII",bound(item).read_bytes())} for s,item in plan["indexInputs"].items()}
    for board in BOARDS:
        receipt=read(OUT/(board+".completion.json"))
        if receipt["accounts"]!=3 or receipt["plan"]!=ref(OUT/"plan.json") or receipt["inputs"]!=ref(OUT/"input-validation.json"):
            raise ValueError("Board account receipt binding")
        group=next(g for g in proof["groups"] if g["board"]==board);data=read(bound(group["source"]))
        for item in receipt["rows"]:
            result,checkpoint,summary=[read(bound(item[k])) for k in ("result","checkpoint","summary")]
            if result["config"]!=plan["config"] or result["research"]["identity"]["signalSchedule"]!=read(bound(item["schedule"])):
                raise ValueError("Final config/signal binding")
            trace=risk.daily_risk_reference(result,checkpoint,data,"volatilityTrail")
            numbers=outcomes.account_numbers(result);c=summary["closedEpisodes"]
            expected={"closed":c["count"],"closedNetPnl":c["netPnlAmount"],**{k:c[k] for k in ("payoffRatio","profitFactor","meanWinReturnPct","meanLossReturnPct")},
                      **{k:summary["account"][k] for k in ("netReturnPct","maxDrawdownPct")}}
            if not all(U.close(numbers[k],v) for k,v in expected.items()):raise ValueError("Independent outcome arithmetic differs")
            bsymbol={"main":"sh000300","chinext":"sz399006","star":"sh000688"}[board]
            prices=indices[bsymbol];br=(prices[plan["config"]["endDate"]]/prices[plan["config"]["startDate"]]-1)*100
            periods=[{"id":p["id"],"start":p["requestedStart"],"end":p["requestedEnd"],"returnPct":p["returnPct"],"DD":p["maxDrawdownPct"],
                      "closed":p["closedEpisodes"]["count"],"R":p["closedEpisodes"]["payoffRatio"],"PF":p["closedEpisodes"]["profitFactor"]} for p in summary["periods"]]
            rows.append({"board":board,"policy":item["policy"],**numbers,"winRatePct":c["winRatePct"],"medianWinReturnPct":c["medianWinReturnPct"],
                "medianLossReturnPct":c["medianLossReturnPct"],"returnQuantilesPct":c["returnQuantilesPct"],"maxConsecutiveLosses":c["maxConsecutiveLosses"],
                "averageExposurePct":summary["periods"][0]["exposurePct"]["mean"],"top5ProfitSharePct":summary["concentration"]["top5ProfitSharePct"],
                "openCount":summary["openPositions"]["count"],"openUnrealizedPnl":summary["reconciliation"]["openPositionUnrealizedPnl"],
                "valuationUnreliableDays":len(summary["quality"]["valuationUnreliableDates"]),"benchmarkSymbol":bsymbol,"benchmarkReturnPct":br,
                "boardExcessPercentagePoints":numbers["netReturnPct"]-br,"periods":periods,"evidence":item})
            audits.append({"board":board,"policy":item["policy"],"everyHeldDayIndependentRisk":trace})
            del result,checkpoint,summary;gc.collect()
        del data;gc.collect()
    if len(rows)!=9 or len({(r["board"],r["policy"]) for r in rows})!=9:raise ValueError("All9 distinct accounts required")
    if U.workspace_snapshot()!=plan["workspaceJsonBefore"]:raise ValueError("User workspace changed during study")
    value={"schema":OUT.name+"-readout","completedAt":now(),"plan":ref(OUT/"plan.json"),"rows":rows,"newAccounts":9,"referenceAccountsReused":0,
           "actualCutoff":plan["actualCutoff"],"missingLatestSessions":plan["missingLatestSessions"],"chronologicalReplication":True,
           "holdoutClaim":False,"certified":False,"targetAchieved":False,"userJsonFilesUnchanged":True}
    write(OUT/"readout.json",value)
    write(OUT/"validation.json",{"passed":True,"completedAt":now(),"plan":ref(OUT/"plan.json"),"readout":ref(OUT/"readout.json"),
       "independentEveryHeldDayRiskAudits":audits,"independentSourceDecodeAndPrefixes":True,"independentAllPopulationSignalReplay":True,
       "independentTradeCurveArithmetic":True,"existingAuditedCashPnlFeesReconciled":True,"independentSecondAccountEngine":False,"userJsonFilesUnchanged":True})
    emit({"completed":True,"readout":ref(OUT/"readout.json"),"accounts":9,"targetAchieved":False})


if __name__=="__main__":
    parser=ArgumentParser();parser.add_argument("command",choices=("register","prepare","run","finalize"));parser.add_argument("--board",choices=BOARDS);args=parser.parse_args()
    if args.command=="run":
        if args.board is None:parser.error("run requires --board")
        run(args.board)
    else:{"register":register,"prepare":prepare,"finalize":finalize}[args.command]()
