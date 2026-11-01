"""Presentation only for the frozen leader-lag followers experiment.

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
OUT = ROOT / "artifacts/strategy-search/leader-lag-followers-v1"
METHOD = "service/research_leader_lag.py"
RUNNER = "scripts/run-leader-lag-followers.py"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("control", "direction")
BOARD_LABELS = dict(zip(BOARDS, ("主板", "创业", "科创")))
MODE_LABELS = dict(zip(MODES, ("同期同伴", "先动后随")))
VALUE = "区间净收益（%）"
EPOCH_LABELS = {"before20240924": "9·24前", "from20240924": "9·24起"}
SHORT_MODES = {"control": "同期", "direction": "先随"}
CHART_BOARD_LABELS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
TABLE_ROWS = [[12, 6, 12, 12, 6, 12, 24, 48, 96]]
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
    ("unknownCount", "身份未知"), ("leaderEventCount", "原领涨Top5事件"),
    ("formed", "完整20同伴群"), ("incomplete", "不足20同伴群"), ("unknown", "未知群"),
    ("rawEdgeCount", "原始成员边"), ("uniqueOwnershipCount", "同日唯一成员"),
    ("duplicateSuppressed", "重复归属抑制"), ("dailyRankExcluded", "同日排名排除"),
    ("formationTop5", "成员Top5机会"), ("uniqueSymbols", "不同成员股票"),
    ("passed", "方向通过"), ("failed", "方向未通过"), ("relationUnknown", "共同关系未知")]
COVERAGE_FIELDS = [("formationTop5", "原始成员Top5"), ("selectedSignals", "实际安排信号"),
    ("notScheduledFromFormation", "未安排"), ("entered", "成交入场"), ("notEntered", "安排但未成交"),
    ("closedCount", "完整平仓"), ("openCountAtCutoff", "期末持仓"),
    ("unresolvedExitCountAtCutoff", "未解决退出"), ("valuationUnreliableDays", "估值不可靠日"),
    ("qualityFlags", "源质量标记"), ("terminalReasons", "账户终态原因计数"), ("admissionCounts", "安排/排除/未知计数")]
INTERVAL_FIELDS = [("intervalLabel", "分期"), ("requestedStart", "开始"), ("requestedEnd", "结束"),
    ("returnPct", "区间净收益（%）"), ("maxDrawdownPct", "区间最大回撤（%）"),
    ("netPnlAmount", "区间净盈亏（元）"), ("meanExposurePct", "平均仓位（%）"),
    ("enteringEquity", "区间起点净值（元）"), ("endingEquity", "区间末净值（元）"),
    ("buyFillCount", "买入成交次数"), ("feesPaid", "费用（元）"), ("endingPositionCount", "末日持仓"), ("valuationDays", "估值不可靠日")]
LIMITS = [
    "图中9·24前后区间不等长，账户权益和持仓连续跨界；区间收益未年化、未重启资金，日期不是买入条件。",
    "六个完整时期与板块对照均列方向政策减同期政策的收益、回撤和平均仓位差；筛选改变资金路径，差值不是因果收益或稳定优势。",
    "没有入场的现金账户净收益为真实0%，但平仓胜率、盈亏比和持仓分布无样本；空仓不能证明小亏大赚。",
    "全部12账户分别从50万元起步，包含费用、每边10基点滑点和期末持仓估值；两期不能拼接，三板块不是共享资金组合。",
    "估值不可靠日沿用原质量标记；期末未实现盈亏与已闭仓盈亏分开，闭仓均值不代表全部机会或未平仓结果。",
    "领涨事件、原始成员边、同日去重成员和Top5是不同分母；不足20成员的群不入队但保留，共同关系未知在Top5后排除且不回填。",
    "关系截至F=A前10个源交易日冻结；A收盘发成员信号，最早次日开盘尝试成交。相关与滞后关系不能识别主力、散户、量化身份或证明领涨因果。",
    "同伴先按同期60日相关挑选，再与同期59日相关比较先动后随，存在群体选择性；零通过不能推广为否定全部领涨扩散机制。",
    "原5%收盘止损、10%跟踪及第五日无进展退出不变；后续可成交开盘执行，跳空和退出受限可令亏损超过名义止损。",
    "固定身份依赖期初61个有效既往观测，历史成员和公司行为资料不完整；120日上市和成交额兼容配置未成为额外固定信号准入。",
    "各年度、半年和16个事后市场阶段沿原资金曲线归因，只支持历史描述；上市板块不是科技行业，统一相对基准是沪深300而非板块匹配指数。",
    "本轮只有2022—2025年，2026未纳入；既有历史期已参与研究，不是留出样本，不能据局部较好阶段推广策略。",
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
    accounts, coverage, formation, contrasts = [], [], [], []
    by_account, by_coverage = indexed(data["accounts"]), indexed(data["coverageRows"])
    by_formation = indexed(data["formationCoverageRows"], False)
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
            "terminalReasons": json.dumps(c["terminalReasonCounts"], ensure_ascii=False, sort_keys=True),
            "admissionCounts": json.dumps(c["policyDispositionCounts"], ensure_ascii=False, sort_keys=True)})
    for c in by_formation.values():
        states, layers = c["relationshipStateCounts"], c["layerCounts"]
        require(sum(states.values()) == c["formationTop5"], "Relationship states do not conserve")
        require(c["leaderEventCount"] == sum(c["leaderGroupKinds"].values())
            and c["rawEdgeCount"] == c["leaderGroupKinds"]["formed"] * 20
            and c["rawEdgeCount"] == c["uniqueOwnershipCount"] + layers["duplicateSuppressed"]
            and c["uniqueOwnershipCount"] == c["formationTop5"] + layers["dailyRankExcluded"], "Four-layer count conservation differs")
        formation.append({**c, **labels(c), **{k: c["sourceCohort"][k] for k in ("includedCount", "excludedCount", "unknownCount")},
            **c["leaderGroupKinds"], **{k: layers[k] for k in ("duplicateSuppressed", "dailyRankExcluded")},
            "passed": states["passed"], "failed": states["failed"], "relationUnknown": states["unknown"]})
    require(sum(c["formationTop5"] for c in formation) == data["opportunityRowsArtifact"]["count"], "Original opportunity count differs")
    identities = set()
    for row in data["comparisons"]:
        key = (row["period"], row["board"], row["id"])
        require(key not in identities and row["id"] == "primary", "Duplicated/unknown contrast")
        identities.add(key)
        control, candidate = ("control", "direction")
        require((row["control"], row["candidate"]) == (control, candidate), "Contrast direction differs")
        delta, c, k = row["candidateMinusControl"], row["candidateMetrics"], row["controlMetrics"]
        contrasts.append({**labels(row), "contrast": row["id"], "contrastLabel": MODE_LABELS[candidate] + "−" + MODE_LABELS[control],
            "returnDifferencePp": delta["accountReturnPct"], "drawdownDifferencePp": delta["maxDrawdownPct"],
            "exposureDifferencePp": delta["meanExposurePct"], "candidateReturnPct": c["accountReturnPct"], "controlReturnPct": k["accountReturnPct"],
            "candidateValuationDays": c["unreliableValuationDays"], "controlValuationDays": k["unreliableValuationDays"],
            "closedPnlDifference": delta["closedPnlAmount"], "openPnlDifference": delta["openUnrealizedPnlAmount"]})
    require(identities == {(p, b, x) for p in PERIODS for b in BOARDS for x in ("primary",)}, "All six comparisons required")
    intervals = []
    for key, count in (("yearRows", 24), ("halfYearRows", 48), ("phaseRows", 96), ("curveEpochRows", 12)):
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
    return contrasts, accounts, formation, coverage, intervals


def payloads(data, method):
    contrasts, accounts, formation, coverage, intervals = tables(data)
    artifact = data["opportunityRowsArtifact"]
    epochs = intervals[-1]
    expected_epochs = {(b, m, e) for b in BOARDS for m in MODES for e in EPOCH_LABELS}
    require({(r["board"], r["policy"], r["phaseId"]) for r in epochs} == expected_epochs
        and all(r["period"] == PERIODS[1] for r in epochs), "Complete newer-period epoch matrix required")
    for r in epochs:
        require(finite(r["returnPct"]), "Epoch return unavailable; never zero-fill missing outcome")
    relation_totals = {key: sum(r[key] for r in formation) for key in ("passed", "failed", "relationUnknown")}
    selection_caveat = (f"六个源的原成员Top5中：方向通过{relation_totals['passed']}个、未通过{relation_totals['failed']}个、"
        f"共同未知{relation_totals['relationUnknown']}个；共同未知与方向不通过分开保留，不能混称数据不足。")
    src = {"label": "先动后随与同期同伴：9·24前后连续账户",
        "files": [{"label": x} for x in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json",
            "research_leader_lag.py", "leader-lag-followers-context.json", "market-phase-atlas-v1/atlas.json")]
            + [{"label": f"opportunity-dispositions.jsonl.gz：{artifact['count']}条；SHA256 {artifact['sha256']}"}],
        "metricDefinitions": [{"label": "分界归因", "definition": "2024—2025账户在2024年9月24日前后连续归因，起点采用区间前最后收盘净值，跨段仓位继续保留。"},
            {"label": "政策对照", "definition": "同一原成员Top5、三种相关均已知时，同期政策入场；方向政策额外要求历史正向滞后相关大于0、同期相关及反向相关。"},
            {"label": "风险与资金", "definition": "账户收益包括费用、滑点和期末估值，同时列仓位、回撤、估值质量及已闭仓和未实现盈亏。"}],
        "filters": ["两个历史时期、三个固定上市板块、两个独立政策，12账户全量保留",
            "持续强势领涨事件在A日激活，F=A前10个源交易日冻结20同伴",
            "固定成员序号优先交错，重叠成员先确定唯一归属，再同日取Top5",
            "共同未知不回填，方向条件不改变同日排序和归属",
            "2024年9月24日只作连续账户归因分界"],
        "caveats": LIMITS + [selection_caveat],
        "evidenceFlow": [{"kind": "validation", "title": "账户与完整汇总复核", "detail": "独立复核12账户、180个完整子区间、六个方向政策减同期政策差值，以及原始成员机会的两种政策处置。"}]}
    def query(key, rows, columns, include_method=False):
        require(all(all(k in r for k, _ in columns) for r in rows), "Missing preview column: " + key)
        q = {"id": key, "rows": [{k: r[k] for k, _ in columns} for r in rows],
            "columns": [{"field": k, "label": label} for k, label in columns], "source": src,
            "reportingPeriod": "2022—2023与2024—2025，两期独立；事件区间为2024年初至9月23日、9月24日至2025年末"}
        if include_method:
            q["methods"] = [{"language": "python", "code": method}]
        return q
    queries = [query("event-epochs", epochs, POLICY_ID + INTERVAL_FIELDS),
        query("contrasts", contrasts, BASE_ID + CONTRAST_FIELDS), query("accounts", accounts, POLICY_ID + ACCOUNT_FIELDS),
        query("tails-and-capital", accounts, POLICY_ID + TAIL_FIELDS), query("formation-coverage", formation, BASE_ID + FORMATION_FIELDS, True),
        query("policy-coverage", coverage, POLICY_ID + COVERAGE_FIELDS)]
    queries.extend(query(key, rows, POLICY_ID + INTERVAL_FIELDS) for key, rows in zip(("years", "half-years", "phases"), intervals[:3]))
    sources = {"schemaVersion": 1, "items": [{"id": "leader-lag-boundary", "title": "9·24前后：先动后随与同期同伴的连续账户", "queries": queries}]}
    plotted = [r for r in epochs if r["policy"] == "control"]
    entry_totals = {m: sum(r["entered"] for r in coverage if r["period"] == PERIODS[1] and r["policy"] == m) for m in MODES}
    chart = {"schemaVersion": 1, "id": "leader-lag-epochs", "queryId": "leader-lag-event-epochs",
        "title": "直接跟随策略：9·24前后区间收益", "height": 360,
        "description": f"2024—2025三板合计：同期{entry_totals['control']}次入场，先随{entry_totals['direction']}次入场；前后区间不等长，账户连续。",
        "chart": {"type": "horizontalBar", "x": "accountLabel", "y": VALUE, "series": "epochLabel", "stackable": False,
            "startAtZero": True, "sortOrder": "original", "valueDecimals": 2, "xLabel": VALUE,
            "showXAxisLabel": True, "showYAxisLabel": False, "showValues": True,
            "categoryOrder": list(CHART_BOARD_LABELS.values()),
            "seriesOrder": list(EPOCH_LABELS.values()),
            "colors": {e: f"var(--chart-{i+1})" for i, e in enumerate(EPOCH_LABELS.values())}},
        "rows": [{"accountLabel": CHART_BOARD_LABELS[r["board"]],
            "epochLabel": EPOCH_LABELS[r["phaseId"]], VALUE: r["returnPct"],
            "平均仓位（%）": r["meanExposurePct"], "估值不可靠日": r["valuationDays"],
            "区间买入成交次数": r["buyFillCount"], "区间最大回撤（%）": r["maxDrawdownPct"], "区间起点净值（元）": r["enteringEquity"],
            "开始": r["requestedStart"], "结束": r["requestedEnd"]} for r in plotted],
        "columns": ["accountLabel", "epochLabel", VALUE, "平均仓位（%）", "估值不可靠日", "区间买入成交次数", "区间最大回撤（%）", "区间起点净值（元）", "开始", "结束"],
        "source": src, "theme": "codex-classic"}
    require([[len(q["rows"]) for q in i["queries"]] for i in sources["items"]] == TABLE_ROWS, "Incomplete receipt")
    require(len(chart["rows"]) == 6, "All six control event intervals required; all twelve retained in Sources")
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
    for k, n in (("newAccounts", 12), ("newSetupMechanisms", 1), ("newEntryPolicies", 2), ("newUniverseVariants", 0), ("referenceAccountsReused", 0)):
        require(plan[k] == comparison[k] == n, "Experiment shape differs: " + k)
    for n in (METHOD, RUNNER):
        require(sha(ROOT / n) == plan["sourcePins"][n], "Frozen source changed: " + n)
    artifact = data["opportunityRowsArtifact"]
    artifact_path = (ROOT / artifact["path"]).resolve()
    require(artifact_path.parent == directory and artifact["format"] == "jsonl+gzip", "Opportunity sidecar outside family")
    pins[artifact_path.name] = sha(artifact_path)
    require(pins[artifact_path.name] == artifact["sha256"] == audit["opportunityRowsArtifactSha256"], "Opportunity binding differs")
    chart, sources = payloads(data, (ROOT / METHOD).read_text("utf-8"))
    names = ("chart.json", "sources.json", "inline", "render-receipt.json", "chart-render-log.json", "sources-render-log.json")
    require(not any((directory / n).exists() for n in names), "Existing outputs; never overwrite")
    write(directory / "chart.json", chart); write(directory / "sources.json", sources)
    (directory / "inline").mkdir()
    fragments = {}
    for kind, name in (("chart", "leader-lag-epochs.html"), ("sources", "leader-lag-sources.html")):
        renderer, target = TOOLS / f"render-inline-{kind}.mjs", directory / "inline" / name
        run = subprocess.run([NODE, str(renderer), "--input", str(directory / (kind + ".json")), "--output", str(target)], text=True, capture_output=True, timeout=60)
        write(directory / f"{kind}-render-log.json", {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr})
        require(run.returncode == 0, run.stdout + run.stderr)
        require(target.stat().st_size < 1_000_000, "Oversized fragment")
        fragments[kind] = {"path": target.relative_to(directory).as_posix(), "sha256": sha(target), "rendererSha256": sha(renderer)}
    require(all(sha(directory / n) == v for n, v in pins.items()), "Input modified while rendering")
    write(directory / "render-receipt.json", {"schema": "leader-lag-followers-render-v1", "inputPins": pins,
        "builderSha256": sha(__file__), "chartPayloadSha256": sha(directory / "chart.json"), "payloadSha256": sha(directory / "sources.json"),
        "chartSha256": fragments["chart"]["sha256"], "sourceSha256": fragments["sources"]["sha256"], "fragments": fragments,
        "cards": 1, "tableRows": TABLE_ROWS, "chartRows": 6, "opportunityRowsArtifact": artifact,
        "dataPeriod": "2022-2025", "includes2026": False, "historicalSampleAlreadyObserved": True,
        "holdout": False, "causalClaim": False, "boardIsIndustry": False, "browserChecked": False,
        "actualMarketRuns": 0, "analysisArtifactsModified": False})
    return {"fragments": fragments, "tableRows": TABLE_ROWS, "renderReceiptSha256": sha(directory / "render-receipt.json")}


def self_test():
    data = {k: [] for k in ("accounts", "coverageRows", "formationCoverageRows", "comparisons", "yearRows", "halfYearRows", "phaseRows", "curveEpochRows")}
    data["opportunityRowsArtifact"] = {"count": 30, "sha256": "a" * 64}
    for p in PERIODS:
        for b in BOARDS:
            data["formationCoverageRows"].append({"period": p, "board": b,
                "sourceCohort": {"includedCount": 8, "excludedCount": 1, "unknownCount": 1},
                "formationTop5": 5, "leaderEventCount": 3, "rawEdgeCount": 40, "uniqueOwnershipCount": 39,
                "leaderGroupKinds": {"formed": 2, "incomplete": 1, "unknown": 0}, "uniqueSymbols": 3,
                "layerCounts": {"duplicateSuppressed": 1, "dailyRankExcluded": 34},
                "relationshipStateCounts": {"passed": 0, "failed": 4, "unknown": 1}})
            for m in MODES:
                identity = {"period": p, "board": b, "policy": m}
                row = {k: None for k, _ in ACCOUNT_FIELDS + TAIL_FIELDS}
                cash = m == "direction"
                row.update(**identity, id=m, accountReturnPct=0. if cash else -1., maxDrawdownPct=0. if cash else 2.,
                    meanExposurePct=0. if cash else 20., signals=0 if cash else 4, closed=0 if cash else 1, openPositions=0,
                    holdingSessions={"median": None, "maximum": None})
                data["accounts"].append({**identity, "comparisonRow": row,
                    "curveAttribution": {"closedEpisodesByExitDate": {"returnPct": {"min": None, "p05": None}}, "feesPaid": 0., "slippageAlreadyInFillPrices": 0.},
                    "reconciliation": {"closedEpisodePnl": 0., "openPositionUnrealizedPnl": 0., "openPositionRealizedPnl": 0.}})
                data["coverageRows"].append({**identity, "formationTop5": 5, "selectedSignals": 0 if cash else 4,
                    "notScheduledFromFormation": 5 if cash else 1, "entered": 0 if cash else 1, "notEntered": 0 if cash else 3,
                    "closedCount": 0 if cash else 1, "openCountAtCutoff": 0, "unresolvedExitCountAtCutoff": 0,
                    "valuationUnreliableDays": 0, "qualityScalarFlags": {"unknown": True}, "terminalReasonCounts": {},
                    "policyDispositionCounts": {"scheduled": 0 if cash else 4, "excluded": 4 if cash else 0, "unknown": 1}})
                for key, n in (("yearRows", 2), ("halfYearRows", 4), ("phaseRows", 8), ("curveEpochRows", 2 if p == PERIODS[1] else 0)):
                    for i in range(n):
                        r = {**identity, "id": str(i), "requestedStart": f"{p[:4]}-{i+1:02d}-01", "requestedEnd": f"{p[:4]}-{i+1:02d}-28",
                            "returnPct": 0. if cash else float(i-1), "maxDrawdownPct": 0. if cash else 2., "netPnlAmount": 0., "feesPaid": 0.,
                            "enteringEquity": 500000., "endingEquity": 500000., "endingPositionCount": 0, "buyFillCount": 0 if cash else 1,
                            "valuationUnreliableDates": [], "exposurePct": {"mean": 0. if cash else 40.}}
                        if key == "curveEpochRows": r["phaseId"] = list(EPOCH_LABELS)[i]
                        data[key].append(r)
            delta = {"accountReturnPct": 1., "maxDrawdownPct": -2., "meanExposurePct": -20., "closedPnlAmount": 0., "openUnrealizedPnlAmount": None}
            data["comparisons"].append({"period": p, "board": b, "id": "primary", "candidate": "direction", "control": "control", "candidateMinusControl": delta,
                "candidateMetrics": {"accountReturnPct": 0., "unreliableValuationDays": 0}, "controlMetrics": {"accountReturnPct": -1., "unreliableValuationDays": 3}})
    before = deepcopy(data)
    chart, sources = payloads(data, "# Synthetic example only; no actual market outcomes.")
    chart["title"] = "Synthetic example — " + chart["title"]
    require(data == before, "Input mutated")
    require([r[VALUE] for r in chart["rows"][:4]] == [-1., 0., -1., 0.], "Signed or observed-zero values changed")
    require(chart["chart"]["xLabel"] == VALUE and chart["chart"]["showXAxisLabel"] is True, "Numeric axis must name its measure")
    require(sources["items"][0]["queries"][2]["rows"][1]["closedMeanPct"] is None, "Empty account sample filled")
    for kind in ("duplicateAccount", "missingPhase", "sidecarCount", "countPartition", "flippedContrast", "missingEpoch", "layerDenominator", "missingEpochReturn"):
        changed = deepcopy(data)
        if kind == "duplicateAccount": changed["accounts"][-1] = deepcopy(changed["accounts"][0])
        elif kind == "missingPhase": changed["phaseRows"].pop()
        elif kind == "sidecarCount": changed["opportunityRowsArtifact"]["count"] = 1
        elif kind == "countPartition": changed["coverageRows"][0]["entered"] = 8
        elif kind == "flippedContrast": changed["comparisons"][0]["candidate"] = "control"
        elif kind == "missingEpoch": changed["curveEpochRows"][0]["phaseId"] = "unknown"
        elif kind == "layerDenominator": changed["formationCoverageRows"][0]["rawEdgeCount"] = 41
        else: changed["curveEpochRows"][0]["returnPct"] = None
        try: payloads(changed, "# synthetic")
        except ValueError: pass
        else: raise AssertionError("Invalid synthetic accepted: " + kind)
    code = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const [chart,sources]=JSON.parse(fs.readFileSync(0,'utf8'));
normalizeInlineChartInput(chart);normalizeInlineSourcesInput(sources);
console.log(JSON.stringify({passed:true,filesWritten:0}));""" % (json.dumps((TOOLS / "inline-chart-input.mjs").as_uri()), json.dumps((TOOLS / "inline-sources-input.mjs").as_uri()))
    run = subprocess.run([NODE, "--input-type=module", "-e", code], input=json.dumps([chart, sources], ensure_ascii=False), text=True, capture_output=True, timeout=30)
    require(run.returncode == 0, run.stderr)
    return {"passed": True, "checks": ["six control epochs with all12 Source epochs", "12 unique accounts", "nine complete tables", "cash zero versus undefined trade samples",
        "no input mutation", "exposure and valuation explicit", "eight invalid cases rejected", "shared payload validators"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
