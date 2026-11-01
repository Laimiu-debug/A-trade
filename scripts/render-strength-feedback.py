"""Presentation only for the frozen strength-feedback experiment.

Actual rendering requires independently verified accounts and readout. The
self-test constructs synthetic payloads in memory and never loads outcomes.
"""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/strength-feedback-v1"
METHOD = "service/research_strength_feedback.py"
RUNNER = "scripts/run-strength-feedback.py"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("baseline", "known", "positive")
BOARD_LABELS = dict(zip(BOARDS, ("主板", "创业板", "科创板")))
MODE_LABELS = dict(zip(MODES, ("原始跟随", "状态可判定", "强势延续过半")))
VALUE = "累计净收益差（百分点）"
TABLE_ROWS = [[18, 18, 18, 6, 6, 18, 36, 72, 144, 18]]
BASE_ID = [("period", "时期"), ("boardLabel", "板块")]
POLICY_ID = BASE_ID + [("policyLabel", "入场政策")]
CONTRAST_FIELDS = [("contrastLabel", "对照方向"), ("returnDifferencePp", "净收益差（百分点）"),
    ("drawdownDifferencePp", "回撤差（百分点）"), ("exposureDifferencePp", "平均仓位差（百分点）"),
    ("candidateReturnPct", "候选净收益（%）"), ("controlReturnPct", "对照净收益（%）"),
    ("candidateValuationDays", "候选估值不可靠日"), ("controlValuationDays", "对照估值不可靠日"),
    ("closedPnlDifference", "已闭仓盈亏差（元）"), ("openPnlDifference", "未实现盈亏差（元）")]
ACCOUNT_FIELDS = [("accountReturnPct", "累计净收益（%）"), ("maxDrawdownPct", "最大回撤（%）"),
    ("meanExposurePct", "平均仓位（%）"), ("signals", "入账户信号"), ("closed", "完整平仓"),
    ("openPositions", "期末持仓"), ("closedMeanPct", "平仓平均收益（%）"),
    ("meanWinPct", "平均盈利（%）"), ("meanLossPct", "平均亏损（%）"),
    ("unreliableValuationDays", "估值不可靠日")]
TAIL_FIELDS = [("winRatePct", "平仓胜率（%）"), ("payoffRatio", "平均盈亏比"), ("profitFactor", "利润因子"),
    ("worstClosedPct", "最差平仓（%）"), ("p05ClosedPct", "平仓5分位（%）"),
    ("medianHoldingSessions", "持仓交易日中位数"), ("maxHoldingSessions", "最长持仓交易日"),
    ("maxConsecutiveLosses", "最大连续亏损"), ("top5ShareOfGrossProfitPct", "前五笔占总盈利（%）"),
    ("closedPnlAmount", "已闭仓盈亏（元）"), ("openUnrealizedPnlAmount", "期末未实现盈亏（元）"),
    ("openRealizedPnlAmount", "未平仓已实现盈亏（元）"), ("feesPaid", "已付费用（元）"),
    ("slippageAlreadyInFillPrices", "成交价已含滑点（元）")]
FORMATION_FIELDS = [("includedCount", "固定纳入身份"), ("excludedCount", "期初排除"),
    ("unknownCount", "身份未知"), ("parentLeaderEventCount", "上游领涨事件"),
    ("parentOriginalTop5", "上游Top5成员机会"), ("parentControlSignals", "原跟随信号"),
    ("upstreamNotScheduled", "上游未安排"), ("formationTop5", "本轮完整机会"),
    ("pass", "正反馈机会"), ("fail", "非正反馈机会"), ("unknown", "反馈未知机会")]
FEEDBACK_FIELDS = [("targetDayCount", "全部目标交易日"), ("memberObservations", "冻结成员观察次数"),
    ("passDays", "正反馈日"), ("failDays", "非正反馈日"), ("unknownDays", "反馈未知日"),
    ("positiveMembers", "上涨成员观察"), ("flatMembers", "平盘成员观察"),
    ("negativeMembers", "下跌成员观察"), ("unknownMembers", "未知成员观察"),
    ("knownDecisionWithUnknownMembersDays", "含未知成员但可判定日"),
    ("emptyCohortDays", "冻结队列为空日"), ("medianCohortSize", "队列人数中位数"),
    ("rankUnknownReasons", "排名窗口未知原因")]
COVERAGE_FIELDS = [("formationTop5", "原始形成机会"), ("selectedSignals", "实际安排信号"),
    ("notScheduledFromFormation", "未安排"), ("entered", "成交入场"), ("notEntered", "安排但未成交"),
    ("closedCount", "完整平仓"), ("openCountAtCutoff", "期末持仓"),
    ("unresolvedExitCountAtCutoff", "未解决退出"), ("valuationUnreliableDays", "估值不可靠日"),
    ("qualityFlags", "源质量标记"), ("terminalReasons", "账户终态原因计数")]
INTERVAL_FIELDS = [("intervalLabel", "分期"), ("requestedStart", "开始"), ("requestedEnd", "结束"),
    ("returnPct", "区间净收益（%）"), ("maxDrawdownPct", "区间最大回撤（%）"),
    ("netPnlAmount", "区间净盈亏（元）"), ("meanExposurePct", "平均仓位（%）"),
    ("feesPaid", "费用（元）"), ("endingPositionCount", "末日持仓"), ("valuationDays", "估值不可靠日")]
LIMITS = [
    "主比较是强势延续过半减状态可判定的完整账户净收益差，同时展示平均仓位差与两账户估值不可靠日；正差不能单独证明稳定优势或因果收益。",
    "状态可判定同时保留正反馈与非正反馈；即使有未知成员，上下界仍可能给出确定状态，因此不等于全部成员数据齐全。",
    "五个交易日前按当时相对沪深300的20日强度冻结前10%成员，反馈是五个交易日的绝对收盘价格变化，不是扣费后可执行交易收益，也不证明主力或量化身份。",
    "固定人数不随未知成员缩减；只有上涨者严格过半才通过，最高可能上涨人数仍不过半则失败，其余保留未知。原Top5次序和归属不变，过滤后不补位。",
    "六个原始跟随账户直接复用已冻结账本，十二个筛选账户独立运行；每个账户原始资金50万元，两期不能拼成四年账户，三板块不是共享资金组合。",
    "状态可判定减原始跟随的差异包含证据可用性；新增准入还会改变仓位、资金占用、重复信号及可成交路径，不能把完整账户差视为独立资金因子的因果效果。",
    "已闭仓均值和尾部只包含实际完整平仓；期末未实现及未平仓已实现盈亏单列，未安排和安排但未成交分开，不能将这些机会当成零收益交易。",
    "估值不可靠日沿用原账户质量标记；原费用、每边10基点滑点、5%收盘止损、10%跟踪与第五日无进展退出保留，跳空或退出受限会突破名义止损。",
    "年份、半年、全部16个事后阶段与2024年9月24日前后均继承净值及跨期仓位，区间不等长且未重建账户，阶段标签未用于准入。",
    "各期期初61个有效既往观测的固定本地身份存在历史覆盖限制；minListingDays120和minAmount20仅兼容配置，未额外筛选固定信号。板块不等于科技行业。",
    "本轮仅2022—2025年，2026未纳入；历史已用于研究，不是留出样本。若列超额收益，共同基准为沪深300，非各板块匹配指数或beta中性收益。",
]



def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def indexed(rows, policy=True):
    keys = ("period", "board", "policy") if policy else ("period", "board")
    result = {tuple(r[k] for k in keys): r for r in rows}
    expected = {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES} if policy else {(p, b) for p in PERIODS for b in BOARDS}
    require(len(result) == len(rows) and set(result) == expected, "Incomplete or duplicated account/source identities")
    return result


def labels(row):
    return {"period": row["period"], "boardLabel": BOARD_LABELS[row["board"]],
        **({"policyLabel": MODE_LABELS[row["policy"]]} if "policy" in row else {})}


def tables(data):
    accounts, coverage, formation, feedback, contrasts = [], [], [], [], []
    by_account, by_coverage = indexed(data["accounts"]), indexed(data["coverageRows"])
    by_formation = indexed(data["formationCoverageRows"], False)
    by_feedback = indexed(data["feedbackCoverageRows"], False)
    for key, a in by_account.items():
        c, row, curve, bridge = by_coverage[key], a["comparisonRow"], a["curveAttribution"], a["reconciliation"]
        require((row["period"], row["board"], row["id"]) == key, "Account row identity mismatch")
        closed = curve["closedEpisodesByExitDate"]
        flat = {**labels(a), **{k: row[k] for k, _ in ACCOUNT_FIELDS},
            **{k: row[k] for k in ("winRatePct", "payoffRatio", "profitFactor", "maxConsecutiveLosses", "top5ShareOfGrossProfitPct")},
            "worstClosedPct": closed["returnPct"]["min"], "p05ClosedPct": closed["returnPct"]["p05"],
            "medianHoldingSessions": row["holdingSessions"]["median"], "maxHoldingSessions": row["holdingSessions"]["maximum"],
            "closedPnlAmount": bridge["closedEpisodePnl"], "openUnrealizedPnlAmount": bridge["openPositionUnrealizedPnl"],
            "openRealizedPnlAmount": bridge["openPositionRealizedPnl"],
            "feesPaid": curve["feesPaid"], "slippageAlreadyInFillPrices": curve["slippageAlreadyInFillPrices"]}
        require(all(v is None or finite(v) for k, v in flat.items() if k not in dict(POLICY_ID)), "Invalid account metric")
        require(finite(flat["accountReturnPct"]) and finite(flat["maxDrawdownPct"]), "Account outcome unavailable")
        require(c["formationTop5"] == c["selectedSignals"] + c["notScheduledFromFormation"]
            and c["selectedSignals"] == c["entered"] + c["notEntered"]
            and c["entered"] == c["closedCount"] + c["openCountAtCutoff"], "Policy count conservation differs")
        accounts.append(flat)
        coverage.append({**c, **labels(c), "qualityFlags": json.dumps(c["qualityScalarFlags"], ensure_ascii=False, sort_keys=True),
            "terminalReasons": json.dumps(c["terminalReasonCounts"], ensure_ascii=False, sort_keys=True)})
    for c in by_formation.values():
        require(sum(c["feedbackOpportunityStates"].values()) == c["formationTop5"], "Feedback opportunity states do not conserve")
        require(c["parentOriginalTop5"] == c["parentControlSignals"] + c["upstreamNotScheduled"]
            and c["parentControlSignals"] == c["formationTop5"], "Parent/new opportunity lineage differs")
        formation.append({**c, **labels(c), **{k: c["sourceCohort"][k] for k in ("includedCount", "excludedCount", "unknownCount")},
            **{k: c["feedbackOpportunityStates"].get(k, 0) for k in ("pass", "fail", "unknown")}})
    for c in by_feedback.values():
        require(sum(c["dayStateCounts"].values()) == c["targetDayCount"], "Feedback days do not conserve")
        require(sum(c["memberFeedbackCounts"].values()) == c["memberObservations"], "Frozen members do not conserve")
        require(c["opportunityStateCounts"] == by_formation[(c["period"], c["board"])]["feedbackOpportunityStates"], "Daily/opportunity linkage differs")
        feedback.append({**c, **labels(c),
            **{k+"Days": c["dayStateCounts"][k] for k in ("pass", "fail", "unknown")},
            **{k+"Members": c["memberFeedbackCounts"][k] for k in ("positive", "flat", "negative", "unknown")},
            "medianCohortSize": c["cohortSize"]["median"],
            "rankUnknownReasons": json.dumps(c["rankUnknownReasonCounts"], ensure_ascii=False, sort_keys=True)})
    require(sum(c["formationTop5"] for c in formation) == data["opportunityRowsArtifact"]["count"], "Original opportunity count differs")
    identities = set()
    for row in data["comparisons"]:
        key = (row["period"], row["board"], row["id"])
        require(key not in identities and row["id"] in ("knownVsBaseline", "positiveVsKnown", "positiveVsBaseline"), "Duplicated/unknown contrast")
        identities.add(key)
        control, candidate = {"knownVsBaseline": ("baseline", "known"), "positiveVsKnown": ("known", "positive"), "positiveVsBaseline": ("baseline", "positive")}[row["id"]]
        require((row["control"], row["candidate"]) == (control, candidate), "Contrast direction differs")
        delta, c, k = row["candidateMinusControl"], row["candidateMetrics"], row["controlMetrics"]
        contrasts.append({**labels(row), "contrast": row["id"], "contrastLabel": MODE_LABELS[candidate] + "−" + MODE_LABELS[control],
            "returnDifferencePp": delta["accountReturnPct"], "drawdownDifferencePp": delta["maxDrawdownPct"],
            "exposureDifferencePp": delta["meanExposurePct"], "candidateReturnPct": c["accountReturnPct"], "controlReturnPct": k["accountReturnPct"],
            "candidateValuationDays": c["unreliableValuationDays"], "controlValuationDays": k["unreliableValuationDays"],
            "closedPnlDifference": delta["closedPnlAmount"], "openPnlDifference": delta["openUnrealizedPnlAmount"]})
    require(identities == {(p, b, x) for p in PERIODS for b in BOARDS for x in ("knownVsBaseline", "positiveVsKnown", "positiveVsBaseline")}, "All eighteen comparisons required")
    intervals = []
    for key, count in (("yearRows", 36), ("halfYearRows", 72), ("phaseRows", 144), ("curveEpochRows", 18)):
        require(len(data[key]) == count, "Incomplete " + key)
        values, seen = [], set()
        for r in data[key]:
            identity = (r["period"], r["board"], r["policy"], r["requestedStart"], r["requestedEnd"])
            require(identity[:3] in by_account and identity not in seen, "Interval identity differs")
            seen.add(identity)
            label = r.get("phaseTitle") or r.get("phaseId") or r.get("id")
            require(label is not None, "Interval label absent")
            values.append({**r, **labels(r), "intervalLabel": label, "meanExposurePct": r["exposurePct"]["mean"],
                "valuationDays": len(r["valuationUnreliableDates"])})
        intervals.append(values)
    return contrasts, accounts, formation, feedback, coverage, intervals


def payloads(data, method, diagnostic=None):
    contrasts, accounts, formation, feedback, coverage, intervals = tables(data)
    artifact = data["opportunityRowsArtifact"]
    src = {"label": "强势价格反馈的完整账户对照（2022—2025）",
        "files": [{"label": x} for x in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json",
            "research_strength_feedback.py", "leader-lag-followers-v1/readout.json", "market-phase-atlas-v1/atlas.json")]
            + [{"label": f"opportunity-dispositions.jsonl.gz：{artifact['count']}条；SHA256 {artifact['sha256']}"}],
        "metricDefinitions": [{"label": "主比较", "definition": "六个板块与时期组合分别比较强势延续过半减状态可判定的累计净收益百分点差，同时保留仓位变化与估值质量。"},
            {"label": "五日价格反馈", "definition": "五个交易日前冻结的强势成员中，绝对收盘价格上涨者是否严格过半；未知成员仍占固定分母，以可证明的上下界决定状态。"},
            {"label": "资金与交易", "definition": "净收益包含费用、滑点及期末持仓；平仓分布与未实现盈亏分开，未安排和安排但未成交分开。"},
            {"label": "区间归因", "definition": "区间从前一收盘净值起算并继承持仓，金额可相加，收益率复利连接；日层、成员层与原机会层分母分别保留。"}],
        "filters": ["两个历史时期、三个固定板块群体、三种账户政策，共18账户全量展示",
            "六个原始跟随账户复用，十二个筛选账户新运行", "沿用原始跟随Top5成员机会与顺序，过滤后不回填",
            "冻结日按板块内相对沪深300的20日收益排序取前10%成员，边界同分全保留", "2024年9月24日只作连续资金归因分界"],
        "caveats": LIMITS,
        "evidenceFlow": [{"kind": "validation", "title": "账户与汇总核验", "detail": "独立复核18个账户、270个完整子区间、18个有方向差值，以及所有原机会的三种政策处置、每日冻结队列与成员计数。"}]}
    if diagnostic is not None:
        src["files"].append({"label": "selection-diagnostic.json：原始账户已平仓选择诊断；SHA256 " + diagnostic["sha256"]})
        src["caveats"] = [*LIMITS, "原始账户平仓按正反馈准入分组，仅描述实际已发生交易的选择构成；被排除组盈亏不是新政策可避免亏损或反事实收益。"]
    def query(key, rows, columns, include_method=False):
        require(all(all(k in r for k, _ in columns) for r in rows), "Missing preview column: " + key)
        q = {"id": key, "rows": [{k: r[k] for k, _ in columns} for r in rows],
            "columns": [{"field": k, "label": label} for k, label in columns], "source": src,
            "reportingPeriod": "2022—2023与2024—2025，两期独立账户；2026未纳入"}
        if include_method:
            q["methods"] = [{"language": "python", "code": method}]
        return q
    queries = [query("contrasts", contrasts, BASE_ID + CONTRAST_FIELDS), query("accounts", accounts, POLICY_ID + ACCOUNT_FIELDS),
        query("tails-and-capital", accounts, POLICY_ID + TAIL_FIELDS), query("formation-coverage", formation, BASE_ID + FORMATION_FIELDS, True),
        query("feedback-coverage", feedback, BASE_ID + FEEDBACK_FIELDS), query("policy-coverage", coverage, POLICY_ID + COVERAGE_FIELDS)]
    queries.extend(query(key, rows, POLICY_ID + INTERVAL_FIELDS) for key, rows in zip(("years", "half-years", "phases", "event-epochs"), intervals))
    sources = {"schemaVersion": 1, "items": [{"id": "positive-versus-known", "title": "强势延续过半相对状态可判定：收益、暴露与质量", "queries": queries}]}
    primary = [r for r in contrasts if r["contrast"] == "positiveVsKnown"]
    chart = {"schemaVersion": 1, "id": "strength-feedback-difference", "queryId": "strength-feedback-primary-contrasts",
        "title": "强势延续过半减状态可判定：净收益差", "height": 360,
        "chart": {"type": "horizontalBar", "x": "boardLabel", "y": VALUE, "series": "period", "stackable": False,
            "startAtZero": True, "sortOrder": "original", "valueDecimals": 2, "xLabel": VALUE,
            "showXAxisLabel": True, "showYAxisLabel": False,
            "showValues": True, "categoryOrder": list(BOARD_LABELS.values()), "seriesOrder": list(PERIODS),
            "colors": {p: f"var(--chart-{i+1})" for i, p in enumerate(PERIODS)}},
        "rows": [{"period": r["period"], "boardLabel": r["boardLabel"], VALUE: r["returnDifferencePp"],
            "平均仓位差（百分点）": r["exposureDifferencePp"], "正反馈估值不可靠日": r["candidateValuationDays"],
            "可判定估值不可靠日": r["controlValuationDays"]} for r in primary],
        "columns": ["period", "boardLabel", VALUE, "平均仓位差（百分点）", "正反馈估值不可靠日", "可判定估值不可靠日"],
        "source": src, "theme": "codex-classic"}
    require([[len(q["rows"]) for q in i["queries"]] for i in sources["items"]] == TABLE_ROWS, "Incomplete receipt")
    require(len(chart["rows"]) == 6, "Six primary differences required")
    for value in (chart, sources):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        require(len(encoded.encode()) < 1_000_000 and "G:/" not in encoded and "C:/" not in encoded, "Oversized/private-path payload")
    return chart, sources


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    values = {n: read(directory / n) for n in names}
    pins = {n: sha(directory / n) for n in names}
    plan, comparison, validation, data, audit = (values[n] for n in names)
    require(validation.get("passed") is True and data["verification"]["passed"] is True and audit.get("passed") is True, "Passed audits required")
    require(validation["planSha256"] == data["planSha256"] == audit["planSha256"] == pins["plan.json"], "Plan audit binding differs")
    require(validation["comparisonSha256"] == audit["comparisonSha256"] == pins["comparison.json"], "Comparison binding differs")
    require(data["verification"]["sha256"] == audit["accountValidationSha256"] == pins["validation.json"]
        and audit["readoutSha256"] == pins["readout.json"], "Readout audit binding differs")
    for n in ("plan.json", "comparison.json", "validation.json"):
        require(data["inputPins"][(directory / n).relative_to(ROOT).as_posix()] == pins[n], "Input changed: " + n)
    require(indexed(data["accountRows"]) == indexed(comparison["rows"]), "Readout account rows differ")
    for k, n in (("newAccounts", 12), ("newSetupMechanisms", 0), ("newEntryPolicies", 2), ("newUniverseVariants", 0), ("referenceAccountsReused", 6)):
        require(plan[k] == comparison[k] == n, "Experiment shape differs: " + k)
    for n in (METHOD, RUNNER):
        require(sha(ROOT / n) == plan["sourcePins"][n], "Frozen source changed: " + n)
    artifact = data["opportunityRowsArtifact"]
    artifact_path = (ROOT / artifact["path"]).resolve()
    require(artifact_path.parent == directory and artifact["format"] == "jsonl+gzip", "Opportunity sidecar outside family")
    pins[artifact_path.name] = sha(artifact_path)
    require(pins[artifact_path.name] == artifact["sha256"] == audit["opportunityRowsArtifactSha256"], "Opportunity binding differs")
    diagnostic_path = directory / "selection-diagnostic.json"
    diagnostic = read(diagnostic_path)
    require(diagnostic["passed"] is True and diagnostic["readoutSha256"] == pins["readout.json"]
        and diagnostic["readoutValidationSha256"] == pins["readout-validation.json"]
        and diagnostic["opportunityRowsArtifact"] == artifact, "Selection diagnostic binding differs")
    require(len(diagnostic["rows"]) == 18 and len(diagnostic["reconciliation"]) == 6
        and all(r["passed"] for r in diagnostic["reconciliation"])
        and all(diagnostic[k] == 0 for k in ("newSignals", "newAccounts", "parameterChanges")), "Selection diagnostic scope differs")
    require(diagnostic["scriptSha256"] == sha(ROOT / "scripts/diagnose-strength-feedback-selection.py"), "Diagnostic source changed")
    pins[diagnostic_path.name] = sha(diagnostic_path)
    chart, sources = payloads(data, (ROOT / METHOD).read_text("utf-8"), {"sha256": pins[diagnostic_path.name]})
    names = ("chart.json", "sources.json", "inline", "render-receipt.json", "chart-render-log.json", "sources-render-log.json")
    require(not any((directory / n).exists() for n in names), "Existing outputs; never overwrite")
    write(directory / "chart.json", chart); write(directory / "sources.json", sources)
    (directory / "inline").mkdir()
    fragments = {}
    for kind, name in (("chart", "strength-feedback-difference.html"), ("sources", "strength-feedback-sources.html")):
        renderer, target = TOOLS / f"render-inline-{kind}.mjs", directory / "inline" / name
        run = subprocess.run([NODE, str(renderer), "--input", str(directory / (kind + ".json")), "--output", str(target)], text=True, capture_output=True, timeout=60)
        write(directory / f"{kind}-render-log.json", {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr})
        require(run.returncode == 0, run.stdout + run.stderr)
        require(target.stat().st_size < 1_000_000, "Oversized fragment")
        fragments[kind] = {"path": target.relative_to(directory).as_posix(), "sha256": sha(target), "rendererSha256": sha(renderer)}
    require(all(sha(directory / n) == v for n, v in pins.items()), "Input modified while rendering")
    write(directory / "render-receipt.json", {"schema": "strength-feedback-render-v1", "inputPins": pins,
        "builderSha256": sha(__file__), "chartPayloadSha256": sha(directory / "chart.json"), "payloadSha256": sha(directory / "sources.json"),
        "chartSha256": fragments["chart"]["sha256"], "sourceSha256": fragments["sources"]["sha256"], "fragments": fragments,
        "cards": 1, "tableRows": TABLE_ROWS, "chartRows": 6, "opportunityRowsArtifact": artifact,
        "dataPeriod": "2022-2025", "includes2026": False, "historicalSampleAlreadyObserved": True,
        "holdout": False, "causalClaim": False, "boardIsIndustry": False, "browserChecked": False,
        "actualMarketRuns": 0, "analysisArtifactsModified": False})
    return {"fragments": fragments, "tableRows": TABLE_ROWS, "renderReceiptSha256": sha(directory / "render-receipt.json")}


def self_test():
    data = {k: [] for k in ("accounts", "coverageRows", "formationCoverageRows", "feedbackCoverageRows", "comparisons", "yearRows", "halfYearRows", "phaseRows", "curveEpochRows")}
    data["opportunityRowsArtifact"] = {"count": 48, "sha256": "a" * 64}
    for p in PERIODS:
        for b in BOARDS:
            data["formationCoverageRows"].append({"period": p, "board": b, "sourceCohort": {"includedCount": 8, "excludedCount": 1, "unknownCount": 1},
                "formationTop5": 8, "parentOriginalTop5": 10, "parentControlSignals": 8, "upstreamNotScheduled": 2,
                "parentLeaderEventCount": 4, "feedbackOpportunityStates": {"pass": 2, "fail": 4, "unknown": 2}})
            data["feedbackCoverageRows"].append({"period": p, "board": b, "targetDayCount": 10, "memberObservations": 30,
                "dayStateCounts": {"pass": 3, "fail": 4, "unknown": 3},
                "memberFeedbackCounts": {"positive": 10, "flat": 2, "negative": 15, "unknown": 3},
                "knownDecisionWithUnknownMembersDays": 2, "emptyCohortDays": 0, "cohortSize": {"median": 3},
                "rankUnknownReasonCounts": {"missingBar": 2}, "opportunityStateCounts": {"pass": 2, "fail": 4, "unknown": 2}})
            for m in MODES:
                identity = {"period": p, "board": b, "policy": m}
                row = {k: None for k, _ in ACCOUNT_FIELDS + TAIL_FIELDS}
                row.update(**identity, id=m, accountReturnPct=1., maxDrawdownPct=2., holdingSessions={"median": None, "maximum": None})
                data["accounts"].append({**identity, "comparisonRow": row,
                    "curveAttribution": {"closedEpisodesByExitDate": {"returnPct": {"min": None, "p05": None}}, "feesPaid": 0., "slippageAlreadyInFillPrices": 0.},
                    "reconciliation": {"closedEpisodePnl": 0., "openPositionUnrealizedPnl": 0., "openPositionRealizedPnl": 0.}})
                data["coverageRows"].append({**identity, "formationTop5": 8, "selectedSignals": 5, "notScheduledFromFormation": 3, "entered": 2, "notEntered": 3,
                    "closedCount": 1, "openCountAtCutoff": 1, "unresolvedExitCountAtCutoff": 0, "valuationUnreliableDays": 1, "qualityScalarFlags": {"unknown": True}, "terminalReasonCounts": {"noCash": 3}})
                for key, n in (("yearRows", 2), ("halfYearRows", 4), ("phaseRows", 8), ("curveEpochRows", 2 if p == PERIODS[1] else 0)):
                    for i in range(n):
                        data[key].append({**identity, "id": str(i), "requestedStart": f"{p[:4]}-{i+1:02d}-01", "requestedEnd": f"{p[:4]}-{i+1:02d}-28",
                            "returnPct": 1., "maxDrawdownPct": 2., "netPnlAmount": 3., "feesPaid": 0., "endingPositionCount": 1,
                            "valuationUnreliableDates": [], "exposurePct": {"mean": 40.}})
            for kind, c, k in (("knownVsBaseline", "known", "baseline"), ("positiveVsKnown", "positive", "known"), ("positiveVsBaseline", "positive", "baseline")):
                delta = {"accountReturnPct": float(BOARDS.index(b)-1), "maxDrawdownPct": None, "meanExposurePct": -10., "closedPnlAmount": 0., "openUnrealizedPnlAmount": None}
                data["comparisons"].append({"period": p, "board": b, "id": kind, "candidate": c, "control": k, "candidateMinusControl": delta,
                    "candidateMetrics": {"accountReturnPct": 1., "unreliableValuationDays": 2}, "controlMetrics": {"accountReturnPct": 0., "unreliableValuationDays": 3}})
    before = deepcopy(data)
    chart, sources = payloads(data, "# Synthetic example only; no actual market outcomes.")
    require(data == before, "Input mutated")
    require([r[VALUE] for r in chart["rows"][:3]] == [-1., 0., 1.], "Signed differences changed")
    require(chart["chart"]["xLabel"] == VALUE and chart["chart"]["showXAxisLabel"] is True,
        "Horizontal numeric axis must explicitly name its measure and percentage-point unit")
    require(sources["items"][0]["queries"][1]["rows"][0]["closedMeanPct"] is None, "Unknown filled")
    for kind in ("duplicateAccount", "missingPhase", "sidecarCount", "countPartition", "flippedContrast", "memberPartition", "dayPartition", "parentLineage"):
        changed = deepcopy(data)
        if kind == "duplicateAccount": changed["accounts"][-1] = deepcopy(changed["accounts"][0])
        elif kind == "missingPhase": changed["phaseRows"].pop()
        elif kind == "sidecarCount": changed["opportunityRowsArtifact"]["count"] = 1
        elif kind == "countPartition": changed["coverageRows"][0]["entered"] = 8
        elif kind == "flippedContrast": changed["comparisons"][0]["candidate"] = "baseline"
        elif kind == "memberPartition": changed["feedbackCoverageRows"][0]["memberObservations"] = 1
        elif kind == "dayPartition": changed["feedbackCoverageRows"][0]["targetDayCount"] = 1
        else: changed["formationCoverageRows"][0]["parentOriginalTop5"] = 1
        try:
            payloads(changed, "# synthetic")
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid synthetic accepted: " + kind)
    code = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const [chart,sources]=JSON.parse(fs.readFileSync(0,'utf8'));
normalizeInlineChartInput(chart);normalizeInlineSourcesInput(sources);
console.log(JSON.stringify({passed:true,filesWritten:0}));""" % (json.dumps((TOOLS / "inline-chart-input.mjs").as_uri()), json.dumps((TOOLS / "inline-sources-input.mjs").as_uri()))
    run = subprocess.run([NODE, "--input-type=module", "-e", code], input=json.dumps([chart, sources], ensure_ascii=False), text=True, capture_output=True, timeout=30)
    require(run.returncode == 0, run.stderr)
    return {"passed": True, "checks": ["six signed primary differences", "18 unique accounts", "ten complete tables", "null preservation",
        "no input mutation", "exposure and valuation explicit", "eight invalid cases rejected", "fixed-member and day denominators", "baseline lineage preserved", "shared payload validators"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
