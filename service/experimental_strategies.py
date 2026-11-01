"""Exact research implementations exposed as explicitly experimental strategies.

No user workspace access, registration, parameter search or automatic execution.
The desktop uses SHA-bound historical schedules; explicit dataset callers may
rebuild those same rules. Neither route creates orders in a real account.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/experimental-strategies-v1.json"
VERSION = "experimental-strategies-v1"
IDS = ("contractionNoProgress5", "persistentLeaderTrail10", "strongReclaimVolatilityTrail")
SIDS = ("rawThreeStageContractionBreakoutDiagnostic", "leaderPersistenceAdmissionV1", "strongUndercutReclaimV1")


class ExecutionStopped(Exception):
    pass


def _check_stop(cancelled):
    if cancelled is not None and cancelled():
        raise ExecutionStopped("实验已停止；未完成结果不作为有效回测，不能从此保存点续跑。")


def _digest(value):
    h = hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                                 separators=(",", ":")).iterencode(value):
        h.update(chunk.encode("utf-8"))
    return h.hexdigest()


def _read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def _bound(ref, *, root=ROOT, cancelled=None):
    root = Path(root).resolve()
    if not isinstance(ref, dict) or not {"path", "sha256"} <= set(ref):
        raise ValueError("Missing frozen input reference")
    path = (root / ref["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError("冻结输入缺失或路径越界：" + str(ref.get("path")))
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check_stop(cancelled)
            h.update(chunk)
    if h.hexdigest() != ref["sha256"]:
        raise ValueError("冻结文件 SHA256 不匹配：" + ref["path"])
    return path


def _manifest(path=MANIFEST):
    data = _read(path)
    if data.get("schema") != VERSION or tuple(s["id"] for s in data.get("strategies", [])) != IDS:
        raise ValueError("实验策略目录不兼容")
    return data


def catalogue(*, manifest_path=MANIFEST, root=ROOT):
    """Small catalogue only. Large input hashes are checked when the user runs."""
    data = _manifest(manifest_path)
    rows = []
    for item in data["strategies"]:
        row = {k: deepcopy(v) for k, v in item.items() if k != "sources"}
        row["sources"] = []
        for source in item["sources"]:
            public = {k: deepcopy(source[k]) for k in ("id", "label", "period", "board", "start", "end",
                "members", "signalCount", "initialCapital", "fees", "researchOrigin")}
            refs = [source["accountData"], source["schedule"], *source["evidenceRefs"]]
            public["available"] = all((Path(root) / r["path"]).is_file() for r in refs)
            public["validation"] = "运行前逐项核对冻结 SHA256；此处仅检查文件存在"
            row["sources"].append(public)
        rows.append(row)
    return {"version": VERSION, "strategies": rows, "researchOverview": deepcopy(data["researchOverview"]),
        "historicalReplayOnly": True, "automaticExecution": False, "resumeSupported": False,
        "certified": False, "targetAchieved": False}


def source_spec(strategy_id, source_id, *, manifest_path=MANIFEST):
    data = _manifest(manifest_path)
    strategy = next((x for x in data["strategies"] if x["id"] == strategy_id), None)
    source = next((x for x in strategy["sources"] if x["id"] == source_id), None) if strategy else None
    if source is None:
        raise ValueError("请选择此实验策略已有的冻结历史来源")
    return deepcopy(strategy), deepcopy(source)


def _dataset(data, start, end):
    if not isinstance(start, str) or not isinstance(end, str) or date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end or start > end:
        raise ValueError("明确且有序的开始/结束日期必填")
    if not isinstance(data, dict) or not isinstance(data.get("calendar"), list) or not data["calendar"]:
        raise ValueError("需要完整独立交易日历")
    days = data["calendar"]
    if days != sorted(set(days)) or any(date.fromisoformat(d).isoformat() != d or d > end for d in days):
        raise ValueError("交易日历重复、乱序或超出截止日")
    if not data.get("calendarSource") or data["calendarSource"].startswith("observed"):
        raise ValueError("观察日线并集不能冒充独立交易日历")
    if not any(start <= d <= end for d in days):
        raise ValueError("日期区间没有交易会话")
    securities = data.get("securities")
    if not isinstance(securities, list) or not securities:
        raise ValueError("完整冻结股票群体必填")
    symbols = [s.get("symbol") for s in securities]
    if any(not isinstance(s, str) or not s for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("股票身份缺失或重复")
    day_set = set(days)
    for security in securities + ([data["benchmark"]] if data.get("benchmark") else []):
        bars = security.get("bars")
        if not isinstance(bars, list):
            raise ValueError("日线必须为完整列表")
        bar_days = [b.get("date") for b in bars]
        if any(d not in day_set for d in bar_days) or bar_days != sorted(set(bar_days)):
            raise ValueError("日线缺少合法日期、重复、乱序或超出独立日历")
    return days, sorted(symbols)


def _notify(progress, value, cancelled):
    _check_stop(cancelled)
    if progress is not None:
        progress(value)


def _contraction(data, start, end, progress, cancelled):
    # Exactly the existing diagnostic eligibility configuration; shape and
    # ranking arithmetic remain in the original frozen functions.
    from .portfolio_config import normalize_config
    from .portfolio_signals import DailyScorer
    from .signal_contraction_price_proxy import CANDIDATE_ID, evaluate_contraction, rank_contraction_signals
    config = normalize_config({"startDate": start, "endDate": end, "initialCapital": 1000000,
        "strategies": [{"id": "trendBreakout", "kind": "trendBreakout", "pool": "base"}],
        "combinations": [], "allocation": {"mode": "equal", "strategyIds": ["trendBreakout"]},
        "weighting": "equal", "topN": 5, "maxPositions": 5, "maxStockWeight": .1,
        "rebalanceMode": "risk", "maxHoldingDays": 0, "stopLossPct": 0, "takeProfitPct": 0, "trailingStopPct": 0,
        "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 120, "minAmount20": 50000000}})
    scorer = DailyScorer(data, config, pools_only=True)
    securities = {s["symbol"]: s for s in data["securities"]}
    calendar, symbols = data["calendar"], sorted(securities)
    indices = [i for i, d in enumerate(calendar) if start <= d <= end]

    def raw(symbol, day):
        series = scorer.series.get(symbol)
        at = series.by_date.get(day) if series is not None else None
        return {**series.bars[at], "priceBasisId": "raw-unadjusted"} if at is not None else None

    windows = {s: deque((raw(s, d) for d in calendar[max(0, indices[0] - 80):indices[0]]), maxlen=81) for s in symbols}
    signals, daily, totals = [], [], Counter()
    for ordinal, at in enumerate(indices):
        _check_stop(cancelled)
        day, candidates, kinds = calendar[at], [], Counter()
        eligibility = scorer._qualified_universe(day)["eligibilityBySymbol"]
        for symbol in symbols:
            windows[symbol].append(raw(symbol, day))
            status = securities[symbol].get("tradeStatusByDate", {}).get(day)
            # Preserve the original rule's precise historical-status semantics.
            halted = status is False or type(status) is int and status == 0 or status == "0"
            value = evaluate_contraction(symbol, day, calendar[max(0, at - 80):at + 1], list(windows[symbol]),
                eligibility.get(symbol, {"eligible": False, "reason": "invalidHistory", "unknown": []}), known_suspended=halted)
            kinds[value["kind"]] += 1
            if value["signal"] is not None:
                candidates.append(value["signal"])
        ranking = rank_contraction_signals(candidates)
        selected = [{"signalId": f"{CANDIDATE_ID}:{day}:{r['symbol']}", "strategyId": CANDIDATE_ID,
            "symbol": r["symbol"], "signalDate": day, "originalRank": r["rank"]} for r in ranking if r["selectedTop5"]]
        signals.extend(selected)
        daily.append({"date": day, "counts": dict(kinds), "ranking": ranking})
        totals.update(kinds)
        if ordinal % 10 == 0 or ordinal == len(indices) - 1:
            _notify(progress, {"phase": "signals", "date": day, "completed": ordinal + 1, "total": len(indices)}, cancelled)
    return {"schedule": signals, "evidence": {"dailyRows": daily, "counts": dict(totals), "source": "original-contraction-detector-and-ranking"}}


def build_signals(strategy_id, dataset, *, start, end, progress=None, cancelled=None):
    """Rebuild original rules on an explicit independent-calendar raw dataset."""
    if strategy_id not in IDS:
        raise ValueError("未知实验策略")
    _, symbols = _dataset(dataset, start, end)
    if dataset.get("priceBasisId") != "raw-unadjusted":
        raise ValueError("新输入必须明确 raw-unadjusted；不会自动修改价格基准标签")
    _check_stop(cancelled)
    if strategy_id == IDS[0]:
        return _contraction(dataset, start, end, progress, cancelled)
    callback = lambda value: _notify(progress, value, cancelled)
    if strategy_id == IDS[1]:
        from .research_leader_lag import build_leader_lag_followers
        from .research_leader_persistence_admission import build_leader_persistence_admission
        parent = build_leader_lag_followers(dataset, start, end, progress=callback)
        metadata = {k: parent[k] for k in ("strategyId", "calendar", "targetDates", "counts")}
        metadata["symbols"] = symbols
        evidence = build_leader_persistence_admission(metadata, parent["dailyRows"], start=start, end=end, progress=callback)
        return {"schedule": evidence["schedules"]["persistent"], "evidence": evidence}
    from .research_undercut_reclaim import build_undercut_reclaim
    evidence = build_undercut_reclaim(dataset, start=start, end=end, progress=callback)
    return {"schedule": evidence["schedules"]["immediate"], "evidence": evidence}


def _fees(spec):
    from .research_account_fees import DatedFeePolicy, LegacyFeePolicy
    if spec["profile"] == "legacy-engine-delegate-v1":
        return LegacyFeePolicy()
    if spec["profile"] != "dated-transfer-assumption-v1":
        raise ValueError("不支持的冻结费用口径")
    return DatedFeePolicy(**deepcopy(spec["parameters"]))


def make_account(strategy_id, dataset, schedule, config, fee_spec):
    """Keep each original SID, five-field schedule, ranking gaps and exit class."""
    if strategy_id not in IDS:
        raise ValueError("未知实验策略")
    sid = SIDS[IDS.index(strategy_id)]
    if config.get("allocation", {}).get("strategyIds") != [sid] or any(r.get("strategyId") != sid for r in schedule):
        raise ValueError("策略身份与原冻结日程不一致")
    required = {"stopLossPct": .05, "trailingStopPct": 0 if strategy_id == IDS[2] else .1,
        "takeProfitPct": 0, "maxHoldingDays": 0, "topN": 5, "maxPositions": 5, "maxStockWeight": .2}
    if any(config.get(k) != value for k, value in required.items()):
        raise ValueError("实验策略固定风险退出和资金规则不允许覆盖")
    from .research_fixed_signal_account import FixedSignalAccount
    from .research_structural_exits import ResearchExitAccount
    from .research_volatility_trail import VolatilityTrailAccount
    kwargs = {"entry_weight": .2, "fee_policy": _fees(fee_spec)}
    cls = {IDS[0]: ResearchExitAccount, IDS[1]: FixedSignalAccount, IDS[2]: VolatilityTrailAccount}[strategy_id]
    if strategy_id == IDS[0]:
        kwargs["exit_rule"] = "noProgress5"
    return cls(dataset, schedule, deepcopy(config), **kwargs)


def _run(strategy_id, dataset, schedule, config, fee_spec, *, progress=None, cancelled=None, provenance=None):
    from .research_account_outcomes import summarize_account
    _dataset(dataset, config["startDate"], config["endDate"])
    _notify(progress, {"phase": "account", "completed": 0, "total": 0}, cancelled)
    engine = make_account(strategy_id, dataset, schedule, config, fee_spec)
    total = len(engine.dates)
    # Each step uses the original transactional day logic and identity checks.
    while engine.state["cursor"] < total:
        _check_stop(cancelled)
        engine.step(20)
        _notify(progress, {"phase": "account", "completed": engine.state["cursor"], "total": total,
            "date": engine.dates[engine.state["cursor"] - 1]}, cancelled)
    result, checkpoint = engine.result(), engine.checkpoint()
    summary = summarize_account(result, checkpoint, policy_id=strategy_id, policy_label=strategy_id)
    if result["status"] != "completed" or summary["quality"]["integrityIssues"]:
        raise ValueError("实验账户未完成或金额对账失败")
    return {"version": VERSION, "strategyId": strategy_id, "schedule": deepcopy(schedule),
        "result": result, "checkpoint": checkpoint, "summary": summary,
        "provenance": deepcopy(provenance or {"mode": "explicit-dataset-rebuild"}),
        "certified": False, "targetAchieved": False}


def execute(strategy_id, dataset, *, start, end, config=None, fee_spec=None, initial_capital=500000,
            progress=None, cancelled=None):
    """Explicit-data API; never called implicitly by catalogue/list/get."""
    from .portfolio_config import normalize_config
    if strategy_id not in IDS:
        raise ValueError("未知实验策略")
    if config is None:
        sid = SIDS[IDS.index(strategy_id)]
        config = normalize_config({"startDate": start, "endDate": end, "initialCapital": initial_capital,
            "topN": 5, "maxPositions": 5, "maxStockWeight": .2, "rebalanceEvery": 1, "switchThreshold": 0,
            "stopLossPct": .05, "trailingStopPct": 0 if strategy_id == IDS[2] else .1, "takeProfitPct": 0,
            "maxHoldingDays": 0, "slippageBps": 10, "strategies": [{"id": sid, "kind": "momentum", "pool": "base"}],
            "allocation": {"mode": "single", "strategyIds": [sid]}})
    if (config["startDate"], config["endDate"]) != (start, end) or fee_spec is None:
        raise ValueError("显式数据执行必须指定对应区间配置及费用假设")
    built = build_signals(strategy_id, dataset, start=start, end=end, progress=progress, cancelled=cancelled)
    value = _run(strategy_id, dataset, built["schedule"], config, fee_spec, progress=progress, cancelled=cancelled)
    value["signalEvidence"] = built["evidence"]
    return value


def execute_source(strategy_id, source_id, *, progress=None, cancelled=None, manifest_path=MANIFEST, root=ROOT):
    """SHA-bound historical reproduction; no original file is written or updated."""
    manifest = _manifest(manifest_path)
    _, source = source_spec(strategy_id, source_id, manifest_path=manifest_path)
    _notify(progress, {"phase": "verifyInputs", "completed": 0, "total": 1}, cancelled)
    for path, sha in manifest["sourcePins"].items():
        _bound({"path": path, "sha256": sha}, root=root, cancelled=cancelled)
    for ref in source["evidenceRefs"]:
        _bound(ref, root=root, cancelled=cancelled)
    data_path = _bound(source["accountData"], root=root, cancelled=cancelled)
    schedule_path = _bound(source["schedule"], root=root, cancelled=cancelled)
    _notify(progress, {"phase": "loadInputs", "completed": 0, "total": 1}, cancelled)
    data, schedule = _read(data_path), _read(schedule_path)
    _check_stop(cancelled)
    if len(schedule) != source["signalCount"] or _digest(data) != source["accountDatasetDigest"]:
        raise ValueError("冻结日程数量或数据内容摘要不符")
    config = deepcopy(source["config"])
    if (config["startDate"], config["endDate"]) != (source["start"], source["end"]):
        raise ValueError("冻结区间配置不符")
    value = _run(strategy_id, data, schedule, config, source["feeSpec"], progress=progress, cancelled=cancelled,
        provenance={"mode": "frozen-historical-replay", "sourceId": source_id, "manifestSha256": hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
            "inputs": {k: deepcopy(source[k]) for k in ("accountData", "schedule", "evidenceRefs")},
            "sourcePins": deepcopy(manifest["sourcePins"]), "originalScheduleUnchanged": True,
            "newSignalGeneration": False, "newResearchTrial": False, "liveMarketClaim": False})
    return value


def display_summary(summary):
    """Small factual UI projection; absence stays null, never a fabricated zero."""
    account, closed = summary["account"], summary["closedEpisodes"]
    open_positions = summary["openPositions"]
    return {"accountReturnPct": account.get("netReturnPct"), "maxDrawdownPct": account.get("maxDrawdownPct"),
        "finalEquity": account.get("finalEquity"), "closed": closed.get("count"),
        "meanReturnPct": closed.get("meanReturnPct"), "winRatePct": closed.get("winRatePct"),
        "meanWinPct": closed.get("meanWinReturnPct"), "meanLossPct": closed.get("meanLossReturnPct"),
        "payoffRatio": closed.get("payoffRatio"), "profitFactor": closed.get("profitFactor"),
        "openPositions": open_positions.get("count"), "openUnrealizedPnlAmount": summary.get("reconciliation", {}).get("openPositionUnrealizedPnl"),
        "concentration": deepcopy(summary.get("concentration")), "quality": deepcopy(summary.get("quality")),
        "feesPaid": account.get("feesPaid"),
        "meanExposurePct": (summary.get("periods") or [{}])[0].get("exposurePct", {}).get("mean"),
        "certified": False, "targetAchieved": False}
