"""Presentation-only, complete online-feedback account comparisons by exit.

Synthetic checks never read market results. Real rendering requires account and
readout audit bindings and uses only the shipped Data chart/Sources renderer.
"""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/online-feedback-entry-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
SLUGS = {"trail10": "fixed", "volatilityTrail": "volatility"}
MODES = {"baseline": "原始准入", "intercept": "历史均值", "ridge": "环境回归"}
CONTRASTS = {"interceptVsBaseline": ("baseline", "intercept"),
             "ridgeVsIntercept": ("intercept", "ridge"), "ridgeVsBaseline": ("baseline", "ridge")}
TABLE_ROWS = [[18, 18, 18, 18, 12, 12, 36, 72, 144, 18]] * 2
VALUE = "全期账户净收益（%）"
ZERO_REFERENCE = "数学零收益（%）"
IDENTITY = [("period", "时期"), ("boardLabel", "板块"), ("candidateLabel", "准入政策")]
BASE_ID = IDENTITY[:2]
ACCOUNT_FIELDS = [(k, label) for k, label in (
    ("accountReturnPct", "全期净收益（%）"), ("maxDrawdownPct", "最大回撤（%）"),
    ("meanExposurePct", "平均仓位（%）"), ("closed", "完整平仓"), ("openPositions", "期末持仓"),
    ("unreliableValuationDays", "估值不可靠日"))]
TAIL_FIELDS = [(k, label) for k, label in (
    ("closedMeanPct", "闭仓均收益（%）"), ("meanWinPct", "平均盈利（%）"), ("meanLossPct", "平均亏损（%）"),
    ("payoffRatio", "盈亏比"), ("profitFactor", "利润因子"), ("p05ClosedPct", "闭仓5%分位（%）"),
    ("medianHoldingSessions", "闭仓持有中位日"), ("maxHoldingSessions", "闭仓最长持有日"),
    ("top5ShareOfGrossProfitPct", "前五笔占毛利（%）"), ("closedPnlAmount", "闭仓净额（元）"),
    ("openRealizedPnlAmount", "未平已实现（元）"), ("openUnrealizedPnlAmount", "未平浮盈（元）"),
    ("feesPaid", "费用（元）"), ("slippageAlreadyInFillPrices", "已含成交价滑点（元）"), ("openStaleMarks", "期末陈旧估值持仓"))]
INTERVAL_FIELDS = [(k, label) for k, label in (
    ("intervalLabel", "区间"), ("requestedStart", "起始"), ("requestedEnd", "结束"),
    ("returnPct", "连续区间收益（%）"), ("maxDrawdownPct", "区间回撤（%）"),
    ("meanExposurePct", "平均仓位（%）"), ("enteringEquity", "承接权益（元）"),
    ("endingEquity", "末期权益（元）"), ("buyFillCount", "买入成交"), ("sellFillCount", "卖出成交"),
    ("closedCount", "本段最终平仓"), ("feesPaid", "本段费用（元）"), ("valuationDays", "估值不可靠日"))]
LIMITS = [
    "每图保留两时期、三板块、三种准入政策的18个完整账户。全期收益不是年化，各账户独立50万元，不能相加组成同一资金组合；三板也不等于三个行业。",
    "图上的0%线只是全期账户净收益的数学盈亏分界，不是预设收益目标或研究成功门槛。",
    "原始准入账户复用既有结果，历史均值与环境回归分别重新执行现金、持仓及交易限制；改变准入会改变后续可用资金和实际买入集合，账户差不是删除原交易后的收益或因果效应。",
    "两套退出均沿用5%初始收盘止损，不含第5日退出。固定10%跟踪按收盘峰值回撤10%；波动幅度跟踪按此前20项归一化真实波幅均值的3倍确定距离，保护水平只上移。此次只改变准入。",
    "每天形成信号T仅使用A=T−1之前已完全闭仓的固定影子账户标签，按最近126个源交易日内的最终退出日期选择；同形成日的有效样本合计权重为一。部分退出和未平仓浮盈不能充当标签。",
    "环境回归只用20日上涨广度、各股成交额相对自身前20日均额的中位数、原强势队列五日连续在榜下界。训练内标准化，岭惩罚固定为1；没有阈值搜索、环境交互或事后阶段标签。",
    "不足20个有效形成日或当前三特征不全时，两候选均保留全部原信号，属于回退放行。达到支持条件才以预测净收益严格大于零决定准入；零是固定决策边界，不是研究成功门槛。",
    "训练只覆盖影子账户实际买入且已经成熟的持仓；快速亏损可能先入样本，慢赢家尚未闭仓。同日等权不能消除成熟度、连续日期依赖或原账户资金选择。",
    "候选独有入场表示同一原始形成机会从未被固定影子账户买入；这些交易的闭仓和未平金额均为事后描述，未用于反向筛选训练数据，也不能证明模型对所有机会有效。",
    "训练标签与未成熟量的逐日累计是持仓段×观察日，不能当独立交易数；缺失特征标签另计，未成交不是零收益交易，无闭仓样本时盈亏统计为空。",
    "所有年度、半年、16个事后阶段和9月24日前后区间沿用原曲线权益及仓位，不重启账户；区间长短不同，已有持仓重估并不等于该阶段新信号盈利。",
    "闭仓净额、期末未平已实现与浮盈分开保留。费用及每边10基点滑点继承原规则，收盘退出最早后续可成交开盘执行；估值不可靠和长期陈旧持仓不能解释为趋势成功。",
    "2022—2025已反复用于研究，2026此前已暴露但本次未使用，没有未触碰的留出样本。固定历史来源身份、公司行为与交易状态限制继续保留；量价特征不能确认主力、散户或量化身份。",
]


def require(value, message):
    if not value:
        raise ValueError(message)


def finite(value): return type(value) in (int, float) and math.isfinite(value)
def read(path): return json.loads(Path(path).read_text("utf-8"))
def encoded(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def identity(row): return tuple(row[k] for k in ("period", "board", "exit", "candidate"))


def labels(row, candidate=True):
    return {"period": row["period"], "boardLabel": BOARDS[row["board"]],
            **({"candidateLabel": MODES[row["candidate"]]} if candidate else {})}


def category(period, board):
    return period[2:4] + "–" + period[-2:] + " " + {"main": "主板", "chinext": "创业", "star": "科创"}[board]


def matrix(rows, modes=tuple(MODES)):
    expected = {(p, b, e, m) for p in PERIODS for b in BOARDS for e in EXITS for m in modes}
    got = {identity(row): row for row in rows}
    require(len(got) == len(rows) and set(got) == expected, "Complete unique account/source matrix required")
    return got


def payloads(data, method):
    accounts, flat, coverage = matrix(data["accounts"]), matrix(data["accountRows"]), matrix(data["coverageRows"])
    feedback, shifts = matrix(data["feedbackCoverageRows"], ("intercept", "ridge")), matrix(data["sampleShiftRows"], ("intercept", "ridge"))
    require(not data["holdoutClaim"] and not data["certified"] and not data["targetAchieved"], "Developmental scope required")
    for key, a in accounts.items():
        require(a["metrics"] == flat[key]["metrics"], "Account scalar projection differs")
        require(finite(a["metrics"]["accountReturnPct"]), "Net account return must remain a known number")
        c = coverage[key]
        require(c["originalOpportunities"] == c["notScheduled"] + c["notEntered"] + c["entered"]
                and c["scheduledSignals"] == c["notEntered"] + c["entered"]
                and c["entered"] == c["closed"] + c["openAtCutoff"], "Opportunity denominator does not conserve")
    contrast_index = {(r["period"], r["board"], r["exit"], r["id"]): r for r in data["comparisons"]}
    require(len(contrast_index) == len(data["comparisons"]) == 36 and set(contrast_index) ==
            {(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in CONTRASTS}, "All contrasts required")
    for key, row in contrast_index.items():
        require((row["control"], row["candidate"]) == CONTRASTS[row["id"]], "Contrast direction changed")
        a, b = (accounts[key[:3] + (m,)]["metrics"] for m in (row["control"], row["candidate"]))
        require(row["controlMetrics"] == a and row["candidateMetrics"] == b, "Contrast account binding differs")
        for field in ("accountReturnPct", "maxDrawdownPct", "meanExposurePct"):
            expected = b[field] - a[field] if finite(a[field]) and finite(b[field]) else None
            require(row["candidateMinusControl"][field] == expected, "Signed contrast differs")
    for key, f in feedback.items():
        require(sum(f["statusCounts"].values()) == f["targetDayCount"] == f["allowDayCount"] + f["rejectDayCount"]
                and f["fittedOriginalOpportunities"] + f["fallbackOriginalOpportunities"] == f["originalOpportunities"], "Training/fallback coverage differs")
        s = shifts[key]
        require(s["candidateOnlyBuyCount"] == s["candidateOnly"]["buyCount"]
                and s["actualBuyCount"] == s["candidateOnlyBuyCount"] + s["shadowAlsoEntered"]["buyCount"], "Candidate-only entry partition differs")
    interval_sets = []
    for name, total in (("yearRows", 72), ("halfYearRows", 144), ("phaseRows", 288), ("curveEpochRows", 36)):
        values = data[name]
        keys = [identity(r) + (r["requestedStart"], r["requestedEnd"]) for r in values]
        require(len(values) == len(set(keys)) == total and all(identity(r) in accounts for r in values), "Incomplete/duplicate " + name)
        interval_sets.append(values)
    result = {}
    for exit_policy, exit_label in EXITS.items():
        def selected(mapping):
            return [mapping[(p, b, exit_policy, m)] for p in PERIODS for b in BOARDS
                    for m in MODES if (p, b, exit_policy, m) in mapping]
        account_rows, tail_rows, coverage_rows, fit_rows, shift_rows, contrasts = ([] for _ in range(6))
        for a in selected(accounts):
            key, m = identity(a), a["metrics"]
            account_rows.append({**labels(a), **m})
            tail_rows.append({**labels(a), **m, "p05ClosedPct": a["closedEpisodes"]["returnQuantilesPct"]["p05"],
                "medianHoldingSessions": a["closedEpisodes"]["holdingSessions"]["median"],
                "maxHoldingSessions": a["closedEpisodes"]["holdingSessions"]["maximum"]})
            c = coverage[key]
            coverage_rows.append({**labels(c), **c, "qualityFlags": encoded(a["sourceQualityScalarFlags"]),
                                  "accountReasons": encoded(c["accountOpportunityReasons"])})
        for p in PERIODS:
            for b in BOARDS:
                for name in CONTRASTS:
                    r = contrast_index[(p, b, exit_policy, name)]
                    d, a, c = (r[k] for k in ("candidateMinusControl", "candidateMetrics", "controlMetrics"))
                    contrasts.append({**labels(r, False), "contrastLabel": MODES[r["candidate"]] + "−" + MODES[r["control"]],
                        "returnDifferencePp": d["accountReturnPct"], "drawdownDifferencePp": d["maxDrawdownPct"],
                        "exposureDifferencePp": d["meanExposurePct"], "candidateReturnPct": a["accountReturnPct"], "controlReturnPct": c["accountReturnPct"],
                        "candidateValuationDays": a["unreliableValuationDays"], "controlValuationDays": c["unreliableValuationDays"]})
        for f in selected(feedback):
            fit_rows.append({**labels(f), **f, "fittedDays": f["statusCounts"].get("fitted", 0), "fallbackDays": f["statusCounts"].get("fallback", 0),
                "trainingDaysMedian": f["eligibleFormationDaysPerDay"]["median"], "trainingDaysMax": f["eligibleFormationDaysPerDay"]["max"],
                "immatureAgeMedian": f["immatureAgeSlots"]["median"], "reasons": encoded(f["reasonCounts"])})
        for s in selected(shifts):
            own, common = s["candidateOnly"], s["shadowAlsoEntered"]
            shift_rows.append({**labels(s), "actualBuyCount": s["actualBuyCount"], "candidateOnlyBuyCount": s["candidateOnlyBuyCount"],
                "candidateOnlyBuyPct": s["candidateOnlyBuyPct"], "closedCount": own["closedCount"], "openCount": own["openCount"],
                "closedMeanPct": own["closedReturnPct"]["mean"], "closedPnlAmount": own["closedPnlAmount"],
                "openRealizedPnlAmount": own["openRealizedPnlAmount"], "openUnrealizedPnlAmount": own["openUnrealizedPnlAmount"],
                "openStaleMarkCount": own["openStaleMarkCount"], "shadowAlsoEnteredCount": common["buyCount"],
                "shadowAlsoEnteredClosedPnl": common["closedPnlAmount"]})
        intervals = [[{**r, **labels(r), "intervalLabel": r.get("phaseTitle", r["id"]),
            "meanExposurePct": r["exposurePct"]["mean"], "closedCount": r["closedEpisodes"]["count"],
            "valuationDays": len(r["valuationUnreliableDates"])} for r in rows if r["exit"] == exit_policy] for rows in interval_sets]
        prefix = "artifacts/strategy-search/online-feedback-entry-v1/"
        src = {"label": exit_label + "下的在线准入完整账户", "files": [{"label": prefix + name} for name in
            ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json",
             "opportunity-dispositions.jsonl.gz", "candidate-entry-episodes.jsonl.gz")] + [{"label": "service/research_online_feedback.py"}],
            "metricDefinitions": [
                {"label": "全期账户净收益", "definition": "同一退出规则下，原始准入、历史均值准入和三环境特征回归准入的完整现金与持仓净值收益，包含费用、滑点和期末未平估值。"},
                {"label": "训练与回退", "definition": "每个时期、板块和退出规则各自使用固定影子账户已成熟标签；缺少训练支持时保留原信号，训练充分后才应用模型准入。"},
                {"label": "候选独有入场", "definition": "新账户实际买入但固定影子账户没有买入的原始形成机会；其结果仅用于描述模型应用样本的变化。"}],
            "filters": ["固定退出规则：" + exit_label, "两时期、三板块、三种准入政策全部保留", "原Top5身份与顺序不变，无替补或重新排名"],
            "caveats": LIMITS, "evidenceFlow": [{"kind": "validation", "title": "账户与来源核对",
                "detail": "独立核对固定影子标签的可用日期、两种模型、原信号准入、实际账户和汇总绑定；来源表保留本退出规则的全部18账户、18对照及全部连续分期。"}]}
        def query(name, rows, columns, exact_method=False):
            require(all(all(key in row for key, _ in columns) for row in rows), "Missing preview field: " + name)
            q = {"id": name, "source": src, "reportingPeriod": "2022—2023及2024—2025独立账户；2026未纳入",
                 "rows": [{key: row[key] for key, _ in columns} for row in rows], "columns": [{"field": key, "label": label} for key, label in columns]}
            if exact_method: q["methods"] = [{"language": "python", "code": method}]
            return q
        qs = [query("accounts", account_rows, IDENTITY + ACCOUNT_FIELDS),
            query("contrasts", contrasts, BASE_ID + [("contrastLabel", "候选−对照"), ("returnDifferencePp", "净收益差（百分点）"),
                ("drawdownDifferencePp", "回撤差（百分点）"), ("exposureDifferencePp", "仓位差（百分点）"),
                ("candidateReturnPct", "候选净收益（%）"), ("controlReturnPct", "对照净收益（%）"),
                ("candidateValuationDays", "候选估值不可靠日"), ("controlValuationDays", "对照估值不可靠日")]),
            query("payoff-and-capital", tail_rows, IDENTITY + TAIL_FIELDS),
            query("opportunity-coverage", coverage_rows, IDENTITY + [("originalOpportunities", "原始机会"), ("scheduledSignals", "准入信号"),
                ("notScheduled", "未准入"), ("notEntered", "准入但未成交"), ("entered", "实际入场"), ("closed", "完整平仓"),
                ("openAtCutoff", "期末未平"), ("valuationUnreliableDays", "估值不可靠日"), ("qualityFlags", "来源质量标记"), ("accountReasons", "账户机会终态原因")]),
            query("training-and-admission", fit_rows, IDENTITY + [("targetDayCount", "观察日"), ("fittedDays", "模型启用日"), ("fallbackDays", "回退放行日"),
                ("allowDayCount", "允许日"), ("rejectDayCount", "拒绝日"), ("fittedOriginalOpportunities", "模型启用原机会"),
                ("fallbackOriginalOpportunities", "回退原机会"), ("trainingDaysMedian", "训练形成日数中位"), ("trainingDaysMax", "训练形成日数最高"),
                ("uniqueLabelXUnknownFormationIds", "特征未知标签身份"), ("immatureEpisodeDayObservations", "未成熟持仓段×日"),
                ("immatureAgeMedian", "未成熟年龄中位日"), ("reasons", "启用与回退原因")], True),
            query("candidate-only-entry", shift_rows, IDENTITY + [("actualBuyCount", "候选实际入场"), ("candidateOnlyBuyCount", "候选独有入场"),
                ("candidateOnlyBuyPct", "独有入场占比（%）"), ("closedCount", "独有完整平仓"), ("openCount", "独有期末未平"),
                ("closedMeanPct", "独有闭仓均收益（%）"), ("closedPnlAmount", "独有闭仓净额（元）"),
                ("openRealizedPnlAmount", "独有未平已实现（元）"), ("openUnrealizedPnlAmount", "独有未平浮盈（元）"),
                ("openStaleMarkCount", "独有陈旧估值持仓"), ("shadowAlsoEnteredCount", "影子亦入场数"), ("shadowAlsoEnteredClosedPnl", "影子亦入场闭仓净额（元）")])]
        qs.extend(query(name, rows, IDENTITY + INTERVAL_FIELDS) for name, rows in zip(("years", "half-years", "phases", "epochs"), intervals))
        source = {"schemaVersion": 1, "items": [{"id": "online-feedback-" + SLUGS[exit_policy],
            "title": exit_label + "：三种准入政策的收益、风险与完整分期", "queries": qs}]}
        require([len(q["rows"]) for q in qs] == TABLE_ROWS[0], "Complete exit-specific source tables required")
        chart = {"schemaVersion": 1, "id": "online-feedback-" + SLUGS[exit_policy], "queryId": "accounts",
            "title": exit_label + "：三种准入的全期净收益", "height": 610, "theme": "codex-classic",
            "chart": {"type": "horizontalBar", "x": "category", "y": VALUE, "series": "candidateLabel", "stackable": False,
                "startAtZero": True, "sortOrder": "original", "valueDecimals": 2, "xLabel": VALUE,
                "showXAxisLabel": True, "showYAxisLabel": False, "showValues": False,
                "annotations": [{"id": "zero-return-origin", "kind": "benchmark", "label": "0% 盈亏分界",
                    "field": ZERO_REFERENCE, "measure": MODES["baseline"]}],
                "categoryOrder": [category(p, b) for p in PERIODS for b in BOARDS], "seriesOrder": list(MODES.values()),
                "colors": {label: "var(--chart-%d)" % (i + 1) for i, label in enumerate(MODES.values())}},
            "rows": [{**labels(a), "category": category(a["period"], a["board"]), VALUE: a["metrics"]["accountReturnPct"], ZERO_REFERENCE: 0.,
                "平均仓位（%）": a["metrics"]["meanExposurePct"], "最大回撤（%）": a["metrics"]["maxDrawdownPct"],
                "估值不可靠日": a["metrics"]["unreliableValuationDays"]} for a in selected(accounts)],
            "columns": ["period", "boardLabel", "candidateLabel", "category", VALUE, ZERO_REFERENCE, "平均仓位（%）", "最大回撤（%）", "估值不可靠日"], "source": src}
        for value in (chart, source):
            raw = encoded(value)
            require(len(raw.encode("utf-8")) < 1_000_000 and "G:/" not in raw and "C:/" not in raw, "Oversized/private presentation")
        result[exit_policy + "Chart"] = chart
        result[exit_policy + "Sources"] = source
    return result


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    documents, pins = {n: read(directory / n) for n in names}, {n: sha(directory / n) for n in names}
    plan, comparison, proof, data, audit = (documents[n] for n in names)
    require(proof["passed"] and data["verification"]["passed"] and audit["passed"], "Independent account/readout audits required")
    require(proof["planSha256"] == data["planSha256"] == audit["planSha256"] == pins["plan.json"], "Plan binding differs")
    require(proof["comparisonSha256"] == data["comparisonSha256"] == audit["comparisonSha256"] == pins["comparison.json"], "Comparison binding differs")
    require(data["verification"]["sha256"] == audit["accountValidationSha256"] == pins["validation.json"]
            and audit["readoutSha256"] == pins["readout.json"], "Readout binding differs")
    require(len(comparison["rows"]) == 36 and data["newAccounts"] == 24 and data["referenceAccountsReused"] == 12, "Trial/account matrix differs")
    for n in ("plan.json", "comparison.json", "validation.json"):
        require(data["inputPins"][(directory / n).relative_to(ROOT).as_posix()] == pins[n], "Readout input changed")
    method = ROOT / "service/research_online_feedback.py"
    require(plan["sourcePins"][method.relative_to(ROOT).as_posix()] == sha(method), "Frozen online module changed")
    for field in ("opportunityRowsArtifact", "candidateEntryRowsArtifact"):
        ref = data[field]; target = (ROOT / ref["path"]).resolve()
        require(target.parent == directory and sha(target) == ref["sha256"], "Readout sidecar binding differs")
        pins[target.name] = ref["sha256"]
    outputs = payloads(data, method.read_text("utf-8"))
    helpers = [{"path": p.resolve().as_posix(), "sha256": sha(p)} for p in
               (TOOLS / "render-inline-chart.mjs", TOOLS / "render-inline-sources.mjs", WRAPPER)]
    require(not (directory / "render-receipt.json").exists() and not (directory / "inline").exists(), "Never overwrite an earlier presentation")
    (directory / "inline").mkdir()
    fragments, projected = {}, {}
    for key, payload in outputs.items():
        exit_policy = "trail10" if key.startswith("trail10") else "volatilityTrail"
        kind = "chart" if key.endswith("Chart") else "sources"
        stem = "online-feedback-" + SLUGS[exit_policy] + "-" + kind
        source, target = directory / (stem + ".json"), directory / "inline" / (stem + ".html")
        write(source, payload)
        renderer = TOOLS / ("render-inline-" + kind + ".mjs")
        ran = subprocess.run([NODE, str(renderer), "--input", str(source), "--output", str(target)], text=True, capture_output=True, timeout=60)
        write(directory / (stem + "-render-log.json"), {"returncode": ran.returncode, "stdout": ran.stdout, "stderr": ran.stderr})
        require(ran.returncode == 0 and target.stat().st_size < 1_000_000, ran.stdout + ran.stderr)
        fragments[key] = {"path": target.relative_to(directory).as_posix(), "sha256": sha(target), "rendererSha256": sha(renderer)}
        projected[key] = {"path": source.name, "sha256": sha(source)}
    require(all(sha(directory / n) == value for n, value in pins.items()), "Analytical inputs changed")
    require(all(sha(r["path"]) == r["sha256"] for r in helpers), "Presentation helper changed")
    write(directory / "render-receipt.json", {"schema": "online-feedback-entry-render-v1", "inputPins": pins,
        "builderSha256": sha(__file__), "fragments": fragments, "payloads": projected, "deliveryOrder": list(outputs),
        "presentationHelpers": helpers,
        "tableRows": TABLE_ROWS, "chartRows": 36, "chartRowsByExit": {e: 18 for e in EXITS}, "cards": 2,
        "actualMarketRuns": 0, "analysisArtifactsModified": False, "holdoutClaim": False, "includes2026": False,
        "browserChecked": False, "causalClaim": False})
    return {"renderReceiptSha256": sha(directory / "render-receipt.json"), "fragments": fragments, "tableRows": TABLE_ROWS}


def self_test():
    data = {k: [] for k in ("accounts", "accountRows", "coverageRows", "feedbackCoverageRows", "sampleShiftRows", "comparisons",
                            "yearRows", "halfYearRows", "phaseRows", "curveEpochRows")}
    data.update(holdoutClaim=False, certified=False, targetAchieved=False)
    for p in PERIODS:
        for b in BOARDS:
            for e in EXITS:
                metrics = {}
                for i, m in enumerate(MODES):
                    key = dict(period=p, board=b, exit=e, candidate=m)
                    values = {k: None for k, _ in ACCOUNT_FIELDS + TAIL_FIELDS}
                    values.update(accountReturnPct=float(i - 1), maxDrawdownPct=2., meanExposurePct=40. - i,
                                  closed=1, openPositions=1, unreliableValuationDays=3, openStaleMarks=1)
                    metrics[m] = values
                    data["accounts"].append({**key, "metrics": deepcopy(values), "sourceQualityScalarFlags": {"stale": True},
                        "closedEpisodes": {"returnQuantilesPct": {"p05": None}, "holdingSessions": {"median": None, "maximum": None}}})
                    data["accountRows"].append({**key, "metrics": deepcopy(values)})
                    data["coverageRows"].append({**key, "originalOpportunities": 5, "scheduledSignals": 4, "notScheduled": 1,
                        "entered": 2, "closed": 1, "openAtCutoff": 1, "notEntered": 2, "valuationUnreliableDays": 3, "accountOpportunityReasons": {"filled": 2}})
                    if m != "baseline":
                        data["feedbackCoverageRows"].append({**key, "statusCounts": {"fitted": 2, "fallback": 3}, "targetDayCount": 5,
                            "allowDayCount": 4, "rejectDayCount": 1, "fittedOriginalOpportunities": 2, "fallbackOriginalOpportunities": 3,
                            "originalOpportunities": 5, "eligibleFormationDaysPerDay": {"median": 2., "max": 20}, "immatureAgeSlots": {"median": None},
                            "reasonCounts": {"insufficientTrainingFormationDays": 3}, "uniqueLabelXUnknownFormationIds": 1, "immatureEpisodeDayObservations": 4})
                        group = {"buyCount": 1, "closedCount": 1, "openCount": 0, "closedReturnPct": {"mean": None},
                            "closedPnlAmount": 0., "openRealizedPnlAmount": 0., "openUnrealizedPnlAmount": 0., "openStaleMarkCount": 0}
                        data["sampleShiftRows"].append({**key, "candidateOnlyBuyCount": 1, "actualBuyCount": 2, "candidateOnlyBuyPct": 50.,
                            "candidateOnly": deepcopy(group), "shadowAlsoEntered": deepcopy(group)})
                    for name, count in (("yearRows", 2), ("halfYearRows", 4), ("phaseRows", 8), ("curveEpochRows", 2 if p == PERIODS[1] else 0)):
                        for n in range(count):
                            data[name].append({**key, "id": str(n), "requestedStart": p[:4] + f"-{n + 1:02d}-01", "requestedEnd": p[:4] + f"-{n + 1:02d}-28",
                                "returnPct": -.5, "maxDrawdownPct": 1., "enteringEquity": 100., "endingEquity": 99.5,
                                "exposurePct": {"mean": 40.}, "closedEpisodes": {"count": 0}, "valuationUnreliableDates": [],
                                "buyFillCount": 0, "sellFillCount": 0, "feesPaid": 0.})
                for name, (a, c) in CONTRASTS.items():
                    data["comparisons"].append(dict(period=p, board=b, exit=e, id=name, control=a, candidate=c,
                        controlMetrics=deepcopy(metrics[a]), candidateMetrics=deepcopy(metrics[c]),
                        candidateMinusControl={field: metrics[c][field] - metrics[a][field] for field in ("accountReturnPct", "maxDrawdownPct", "meanExposurePct")}))
    before = deepcopy(data); got = payloads(data, "# Synthetic only; no market outcome read.")
    require(data == before and len(got) == 4, "Inputs changed or fragments omitted")
    for e in EXITS:
        chart, source = got[e + "Chart"], got[e + "Sources"]
        require(len(chart["rows"]) == 18 and [r[VALUE] for r in chart["rows"][:3]] == [-1., 0., 1.], "Signed/zero accounts changed")
        require(chart["chart"]["showValues"] is False and chart["chart"]["xLabel"] == VALUE, "Readable native axis configuration required")
        require(all(r[ZERO_REFERENCE] == 0. for r in chart["rows"]) and chart["chart"]["annotations"][0]["field"] == ZERO_REFERENCE,
                "Mathematical zero reference missing")
        require([len(q["rows"]) for q in source["items"][0]["queries"]] == TABLE_ROWS[0], "Source matrix incomplete")
        require(source["items"][0]["queries"][2]["rows"][0]["p05ClosedPct"] is None, "Unknown tail converted to zero")
    for kind in ("account", "contrast", "phase", "coverage", "support", "shift"):
        bad = deepcopy(data)
        if kind == "account": bad["accounts"].pop()
        elif kind == "contrast": bad["comparisons"][0]["candidate"] = "baseline"
        elif kind == "phase": bad["phaseRows"].pop()
        elif kind == "coverage": bad["coverageRows"][0]["entered"] = 4
        elif kind == "support": bad["feedbackCoverageRows"][0]["statusCounts"]["fitted"] = 0
        else: bad["sampleShiftRows"][0]["candidateOnlyBuyCount"] = 2
        try: payloads(bad, "# synthetic")
        except ValueError: pass
        else: raise AssertionError("Invalid synthetic accepted: " + kind)
    js = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const x=JSON.parse(fs.readFileSync(0,'utf8'));
for(const [key,value] of Object.entries(x))(key.endsWith('Chart')?normalizeInlineChartInput:normalizeInlineSourcesInput)(value);""" % (
        json.dumps((TOOLS / "inline-chart-input.mjs").as_uri()), json.dumps((TOOLS / "inline-sources-input.mjs").as_uri()))
    ran = subprocess.run([NODE, "--input-type=module", "-e", js], input=json.dumps(got, ensure_ascii=False), text=True, capture_output=True, timeout=30)
    require(ran.returncode == 0, ran.stderr)
    return {"passed": True, "checks": ["36 account returns and36 contrasts", "two exits andfour ordered fragments", "all540 continuous subintervals",
        "training/fallback andcandidate-only sample coverage", "signed zero andnull retained", "six malformed matrices rejected", "nonmutating projection", "shared native validators"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--directory", type=Path, default=OUT); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
