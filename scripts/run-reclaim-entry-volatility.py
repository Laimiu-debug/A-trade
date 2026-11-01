"""Six new complete accounts and six unchanged, hash-bound cached controls."""
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


U = module("_entry_vol_frozen_util", "scripts/run-liquid-trend-breakout.py")
read, write, bound, ref, sha, now, emit = [getattr(U, k) for k in ("read", "write", "bound", "ref", "sha", "now", "emit")]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "reclaim-entry-volatility-cap-v1"
PLAN = OUT / "plan.json"
PARENT = BASE / "reclaim-market-alignment-v1"
PROTOCOL = ROOT / "docs/reclaim-entry-volatility-cap-v1.json"
BOARDS = ("main", "chinext", "star")
POLICIES = ("liquidNtr5", "liquidTrendNtr5")
POLICY_MAP = {"liquidNtr5": "liquid", "liquidTrendNtr5": "liquidTrend"}


def date_of(value):
    text = str(value)
    return text[:4] + "-" + text[4:6] + "-" + text[6:]


def verify_plan():
    plan = read(PLAN)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    for name in ("protocol", "parentPlan", "parentValidation", "parentReadout", "parentInputs", "diagnostic", "continuationSnapshot"):
        bound(plan[name])
    for item in [*plan["sources"].values(), *plan["indices"].values()]:
        bound(item)
    for group in plan["signalInputs"].values():
        for item in group.values():
            bound(item)
    return plan


def execution_copy_check():
    old = (ROOT / "scripts/run-reclaim-market-alignment.py").read_text("utf-8")
    old_tree, new_tree = ast.parse(old), ast.parse(Path(__file__).read_text("utf-8"))
    node = next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    text = ("\n".join(old.splitlines()[node.lineno - 1:node.end_lineno]).replace('"accounts": 3', '"accounts": 2')
        .replace('not inputs["all9SchedulesFrozenBeforeAccounts"]', 'not inputs["all6NewSchedulesFrozenBeforeAccounts"]'))
    expected = ast.parse(text).body[0]
    actual = next(n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    if ast.dump(expected) != ast.dump(actual):
        raise ValueError("Execution copy changed beyond declared policy count")


def register():
    if OUT.exists():
        raise ValueError("Study already registered; retain old attempts")
    parent, proof, readout, inputs = [read(PARENT / n) for n in ("plan.json", "validation.json", "readout.json", "input-validation.json")]
    if not proof["passed"] or proof["readout"] != ref(PARENT / "readout.json") or not inputs["passed"]:
        raise ValueError("Parent control/source proof binding differs")
    execution_copy_check()
    controls = [deepcopy(r) for r in readout["rows"] if r["policy"] in POLICY_MAP.values()]
    if len(controls) != 6:
        raise ValueError("All6 unchanged controls required")
    for row in controls:
        for item in row["evidence"].values():
            if isinstance(item, dict) and set(item) >= {"path", "sha256"}:
                bound(item)
    rules, pins = read(PROTOCOL), dict(parent["sourcePins"])
    for name, digest in pins.items():
        bound({"path": name, "sha256": digest})
    for name in ("docs/reclaim-entry-volatility-cap-v1.json", "service/research_reclaim_entry_volatility.py",
                 "scripts/verify-reclaim-entry-volatility.py", "tests/test_research_reclaim_entry_volatility.py",
                 "scripts/run-reclaim-entry-volatility.py"):
        pins[name] = sha(ROOT / name)
    config = deepcopy(parent["config"])
    if config["stopLossPct"] != .05 or (config["startDate"], config["endDate"]) != (rules["scope"]["startDate"], rules["scope"]["endDate"]):
        raise ValueError("Existing stop/date contract differs")
    signal_inputs = {g["board"]: {key: g["schedules"][key] for key in ("original", "liquid", "liquidTrend")} for g in inputs["groups"]}
    OUT.mkdir()
    write(OUT / "continuation-before.json", read(BASE / "goal-continuation-20261018.json"))
    plan = {"schema": OUT.name, "registeredAt": now(), "rules": rules, "protocol": ref(PROTOCOL),
        "parentPlan": ref(PARENT / "plan.json"), "parentValidation": ref(PARENT / "validation.json"),
        "parentReadout": ref(PARENT / "readout.json"), "parentInputs": ref(PARENT / "input-validation.json"),
        "diagnostic": ref(PARENT / "entry-risk-diagnostic.json"), "sources": parent["sources"], "indices": parent["indices"],
        "signalInputs": signal_inputs, "controls": controls, "config": config, "sourcePins": pins,
        "continuationSnapshot": ref(OUT / "continuation-before.json"), "workspaceJsonBefore": U.workspace_snapshot(),
        "actualCutoff": parent["actualCutoff"], "missingLatestSessions": parent["missingLatestSessions"],
        "newAccountsPlanned": 6, "referenceAccountsReused": 6, "newStrategyTrialsPlanned": 1,
        "executionStaticCopySameExceptPolicyCount": True, "holdoutClaim": False, "targetAchieved": False}
    write(PLAN, plan)
    for name in pins:
        target = OUT / "frozen-sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    emit({"registered": True, "plan": ref(PLAN), "newAccountsPlanned": 6, "referenceAccountsReused": 6})


def prepare():
    from service.research_reclaim_entry_volatility import build_admissions, CEILING
    plan = verify_plan()
    if CEILING != plan["config"]["stopLossPct"]:
        raise ValueError("Feature ceiling must equal unchanged initial stop")
    names = ("test_research_reclaim_entry_volatility", "test_research_volatility_trail", "test_research_fixed_signal_account",
             "test_research_account_fees", "test_research_account_outcomes")
    argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", *["tests." + n for n in names]]
    tests = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
    write(OUT / "pre-account-tests.json", {"argv": argv, "exitCode": tests.returncode, "stdout": tests.stdout,
        "stderr": tests.stderr, "passed": tests.returncode == 0, "completedAt": now()})
    if tests.returncode:
        raise ValueError("Pre-account checks failed")
    oracle = module("_entry_vol_independent_oracle", "scripts/verify-reclaim-entry-volatility.py")
    groups = []
    start, end = plan["config"]["startDate"], plan["config"]["endDate"]
    for board in BOARDS:
        emit({"prepareStart": board})
        data = read(bound(plan["sources"][board]))
        original = read(bound(plan["signalInputs"][board]["original"]))
        bases = {key: read(bound(plan["signalInputs"][board][key])) for key in ("liquid", "liquidTrend")}
        production = build_admissions(data, original, bases, start=start, end=end)
        expected, schedules = oracle.oracle(data, original, bases)
        if production["schedules"] != schedules or len(production["features"]) != len(expected):
            raise ValueError("Independent volatility admissions differ")
        for a, b in zip(production["features"], expected):
            if any(a[k] != b[k] for k in ("signalId", "asOfDate", "windowDates", "meanTRPct20", "windowStatus", "admissionStatus")):
                raise ValueError("Independent raw feature/availability differs")
            if a["windowStatus"] == "known":
                for key, other in (("referenceValue", "references"), ("trueRange", "ranges"), ("trPct", "normalized")):
                    if [r[key] for r in a["trTerms"]] != b[other]:
                        raise ValueError("Independent reference/TR terms differ")
        folder = OUT / board
        write(folder / "entry-features.json.gz", production)
        refs = {}
        for policy in POLICIES:
            write(folder / (policy + ".signals.json"), production["schedules"][policy])
            refs[policy] = ref(folder / (policy + ".signals.json"))
        groups.append({"board": board, "source": plan["sources"][board], "schedules": refs,
            "features": ref(folder / "entry-features.json.gz"), "counts": production["counts"],
            "allOriginalOpportunitiesIndependentlyRebuilt": True, "unchangedParentStockAndMarketProofReused": True})
        emit({"prepareComplete": board, **production["counts"]})
        del data, original, bases, production, expected, schedules
        gc.collect()
    verify_plan()
    write(OUT / "input-validation.json", {"passed": True, "completedAt": now(), "planSha256": sha(PLAN),
        "all9SchedulesFrozenBeforeAccounts": False, "all6NewSchedulesFrozenBeforeAccounts": True,
        "groups": groups, "pretests": ref(OUT / "pre-account-tests.json"), "newAccountsRun": 0,
        "referenceControls": 6, "referenceProof": plan["parentValidation"], "certified": False})
    emit({"inputsValidated": True, "newSchedules": 6, "reusedControls": 6})


def finalize():
    plan, inputs = verify_plan(), read(OUT / "input-validation.json")
    risk = module("_entry_vol_risk_audit", "scripts/verify-reclaim-volatility-trail.py")
    numbers = module("_entry_vol_outcome_audit", "scripts/verify-liquid-trend-breakout.py")
    indices = {s: {date_of(r[0]): r[4] / 100 for r in struct.iter_unpack("<IIIIIfII", bound(item).read_bytes())} for s, item in plan["indices"].items()}
    rows, audits = [], []
    for board in BOARDS:
        receipt = read(OUT / (board + ".completion.json"))
        if receipt["accounts"] != 2 or receipt["plan"] != ref(PLAN) or receipt["inputs"] != ref(OUT / "input-validation.json"):
            raise ValueError("New-account receipt binding differs")
        data = read(bound(plan["sources"][board]))
        controls = [deepcopy(r) for r in plan["controls"] if r["board"] == board]
        control_identity = {}
        for control in controls:
            saved = read(bound(control["evidence"]["result"]))
            if saved["config"] != plan["config"] or saved["research"]["identity"]["signalSchedule"] != read(bound(plan["signalInputs"][board][control["policy"]])):
                raise ValueError("Cached control is not the exact unchanged comparison")
            control_identity[control["policy"]] = {k: saved["research"]["identity"][k] for k in ("datasetDigest", "datasetFingerprint", "feePolicy", "sourcePins")}
            control.update(rowRole="reusedControl", independentNewEvidence=False)
            rows.append(control)
            del saved
        for item in receipt["rows"]:
            result, checkpoint, summary = [read(bound(item[k])) for k in ("result", "checkpoint", "summary")]
            if result["config"] != plan["config"] or result["research"]["identity"]["signalSchedule"] != read(bound(item["schedule"])):
                raise ValueError("New config/schedule changed")
            parent = POLICY_MAP[item["policy"]]
            if {k: result["research"]["identity"][k] for k in control_identity[parent]} != control_identity[parent]:
                raise ValueError("New account source, engine or fee identity differs from cached control")
            trace = risk.daily_risk_reference(result, checkpoint, data, "volatilityTrail")
            independent = numbers.account_numbers(result)
            c = summary["closedEpisodes"]
            expected = {"closed": c["count"], "closedNetPnl": c["netPnlAmount"],
                **{k: c[k] for k in ("payoffRatio", "profitFactor", "meanWinReturnPct", "meanLossReturnPct")},
                **{k: summary["account"][k] for k in ("netReturnPct", "maxDrawdownPct")}}
            if not all(U.close(independent[k], v) for k, v in expected.items()):
                raise ValueError("Independent complete-account arithmetic differs")
            benchmark = {"main": "sh000300", "chinext": "sz399006", "star": "sh000688"}[board]
            prices = indices[benchmark]
            benchmark_return = (prices[plan["config"]["endDate"]] / prices[plan["config"]["startDate"]] - 1) * 100
            periods = [{"id": p["id"], "start": p["requestedStart"], "end": p["requestedEnd"], "returnPct": p["returnPct"],
                "DD": p["maxDrawdownPct"], "closed": p["closedEpisodes"]["count"], "R": p["closedEpisodes"]["payoffRatio"], "PF": p["closedEpisodes"]["profitFactor"]} for p in summary["periods"]]
            screen = (independent["closed"] >= 30 and independent["payoffRatio"] is not None and independent["payoffRatio"] >= 3 and
                independent["profitFactor"] is not None and independent["profitFactor"] >= 1.5 and independent["meanLossReturnPct"] is not None and
                independent["meanLossReturnPct"] >= -5 and independent["maxDrawdownPct"] <= 20)
            rows.append({"board": board, "policy": item["policy"], "basePolicy": parent, "rowRole": "newAccount", **independent,
                "winRatePct": c["winRatePct"], "maxConsecutiveLosses": c["maxConsecutiveLosses"], "medianLossReturnPct": c["medianLossReturnPct"],
                "returnQuantilesPct": c["returnQuantilesPct"], "averageExposurePct": summary["periods"][0]["exposurePct"]["mean"],
                "top5ProfitSharePct": summary["concentration"]["top5ProfitSharePct"], "openCount": summary["openPositions"]["count"],
                "openUnrealizedPnl": summary["reconciliation"]["openPositionUnrealizedPnl"], "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]),
                "benchmarkSymbol": benchmark, "benchmarkReturnPct": benchmark_return, "boardExcessPercentagePoints": independent["netReturnPct"] - benchmark_return,
                "proposedNumericScreenPassed": screen, "screenHumanConfirmed": False, "periods": periods, "evidence": item})
            audits.append({"board": board, "policy": item["policy"], "everyHeldDayIndependentRisk": trace})
            del result, checkpoint, summary
            gc.collect()
        del data
        gc.collect()
    if len(rows) != 12 or sum(r["rowRole"] == "newAccount" for r in rows) != 6:
        raise ValueError("All6 new and6 cached controls required")
    if U.workspace_snapshot() != plan["workspaceJsonBefore"]:
        raise ValueError("Protected user workspace changed")
    write(OUT / "readout.json", {"schema": OUT.name + "-readout", "completedAt": now(), "plan": ref(PLAN), "rows": rows,
        "newAccounts": 6, "referenceAccountsReused": 6, "newStrategyTrials": 1, "continuousEquity": True, "annualReset": False,
        "actualCutoff": plan["actualCutoff"], "missingLatestSessions": plan["missingLatestSessions"], "entryCeiling": .05,
        "corporateActionResearchExpansionStoppedByHuman": True, "rawModelLimitationsRetained": True,
        "holdoutClaim": False, "certified": False, "targetAchieved": False, "userJsonFilesUnchanged": True})
    write(OUT / "validation.json", {"passed": True, "completedAt": now(), "plan": ref(PLAN), "readout": ref(OUT / "readout.json"),
        "independentAllOriginalOpportunityFeatureReplay": True, "independentEveryHeldDayRiskAudits": audits,
        "independentTradeCurveArithmetic": True, "existingAuditedCashPnlFeeBridgesReconciled": True,
        "all6CachedControlIdentitiesUnchanged": True, "cachedControlProof": plan["parentValidation"],
        "independentSecondAccountEngine": False, "userJsonFilesUnchanged": True})
    emit({"completed": True, "newAccounts": 6, "cachedControls": 6, "readout": ref(OUT / "readout.json"), "targetAchieved": False})


# The run function is a controlled static copy of batch54, with only the
# declared account count and six-schedule precondition adjusted for this study.


def run(board):
    from service.research_volatility_trail import VolatilityTrailAccount
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan, inputs = verify_plan(), read(OUT / "input-validation.json")
    if not inputs["passed"] or inputs["planSha256"] != sha(PLAN) or not inputs["all6NewSchedulesFrozenBeforeAccounts"]:
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
    write(OUT / (board + ".completion.json"), {"completedAt": now(), "accounts": 2, "rows": rows,
        "plan": ref(PLAN), "inputs": ref(OUT / "input-validation.json")})


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
