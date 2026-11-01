"""Render bound breadth-entry results through the shared Data chart/receipt tools.

No accounts or analysis are run here. --self-test uses only synthetic in-memory
rows and the shared input validators; real rendering requires passed validation.
"""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/reclaim-breadth-entry-v1"
METHOD = "service/research_reclaim_breadth.py"
RUNNER = "scripts/run-reclaim-breadth-entry.py"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
POLICIES = ("immediate", "knownContext", "breadthMajority", "breadthImproving", "breadthBoth")
PERIODS = ("2022-2023", "2024-2025")
LABELS = dict(zip(POLICIES, ("原始买点", "环境完整", "上涨过半", "范围扩大", "两者同时")))
CHART_LABELS = dict(zip(LABELS.values(), ("原始", "完整", "过半", "扩大", "同时")))
VALUE = "累计净收益 (%)"
TABLE_ROWS = [[10, 10, 10, 2, 10], [20, 40, 80, 10]]
IDENTITY = [("period", "时期"), ("policyLabel", "入场规则")]
ACCOUNT_FIELDS = [("accountReturnPct", "累计净收益（%）"), ("maxDrawdownPct", "最大回撤（%）"),
    ("meanExposurePct", "平均仓位（%）"), ("signals", "准入信号"), ("closed", "完整平仓"),
    ("openPositions", "期末持仓"), ("closedMeanPct", "平仓平均净收益（%）"),
    ("meanWinPct", "平均盈利（%）"), ("meanLossPct", "平均亏损（%）"), ("profitFactor", "利润因子")]
TAIL_FIELDS = [("winRatePct", "平仓胜率（%）"), ("payoffRatio", "平均盈亏比"),
    ("worstClosedPct", "最差平仓（%）"), ("p05ClosedPct", "平仓5分位（%）"),
    ("medianHoldingSessions", "持仓交易日中位数"), ("maxConsecutiveLosses", "最大连续亏损次数"),
    ("top5ShareOfGrossProfitPct", "前五笔占总盈利（%）")]
DELTA_FIELDS = [("accountReturnPct", "累计净收益差（百分点）"),
    ("maxDrawdownPct", "最大回撤差（百分点）"), ("meanExposurePct", "平均仓位差（百分点）"),
    ("closedMeanPct", "平仓均值差（百分点）"), ("profitFactor", "利润因子差")]
INTERVAL_FIELDS = [("intervalLabel", "分期"), ("requestedStart", "开始"), ("requestedEnd", "结束"),
    ("returnPct", "区间净收益（%）"), ("maxDrawdownPct", "区间最大回撤（%）"),
    ("netPnlAmount", "区间净盈亏（元）"), ("meanExposurePct", "平均仓位（%）")]
LIMITS = [
    "两期各从50万元独立起步，累计净收益含费用、每边10基点滑点及期末持仓估值；不能拼接为四年账户。",
    "原始立即账户保留未知环境；另四组共同排除未知。环境已知只控制资料完整性，上涨过半与广度增加分别及联合测试。",
    "上涨过半指T−1配对股票中20日收益为正的比例严格大于50%；广度增加指同一配对样本相对五个交易日前的比例上升。不是当天上涨家数，也不代表资金身份。",
    "广度条件仅控制新入场，保留原Top5顺序且不递补；现有持仓仍按原5%止损、10%跟踪及第五日无进展规则退出。收盘触发后在可成交开盘执行，损失可能超过名义阈值。",
    "筛选会改变资金、仓位与以后成交。差值和交互项描述完整账户路径，不能视为独立因子的因果效应；低仓位或低回撤本身不构成策略改善。",
    "已闭仓分布不含未平仓和未成交机会；前五笔盈利占比用于观察集中度，不是剔除大赢家后的另一个账户。",
    "年度、半年、全部16个历史阶段和2024年9月24日前后均保留。分期继承进入净值与持仓，日期和事后情绪标签没有用于准入。",
    "两期历史均已用于多轮研究。3141只存续主板样本、历史资格与权益资料、模拟成交和估值限制继续存在，本轮不是样本外或稳定收益认证。",
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


def source(definitions, generated_at):
    return {"label": "强股支撑收回的市场广度准入研究",
        "files": [{"label": name} for name in ("plan.json", "comparison.json", "readout.json", "validation.json",
            "research_reclaim_breadth.py", "market-phase-atlas-v1/atlas.json")],
        "metricDefinitions": [{"label": label, "definition": definition} for label, definition in definitions],
        "filters": ["两期全部原始立即入场Top5机会", "环境取形成日T−1，配对比较日为T−6",
            "未知环境在四种新规则中均不准入", "保持原排名、20%入场预算与五个持仓上限",
            "原始立即基线复用，四种规则分别完整重跑账户"],
        "caveats": LIMITS, "executedAt": generated_at,
        "evidenceFlow": [{"kind": "validation", "title": "信号与账户复核",
            "detail": "独立验证绑定冻结计划、原始信号和市场日历；重算事前广度准入，复核八个新账户，并核对两个原始立即账户的冻结引用。"},
            {"kind": "validation", "title": "全部分期对账",
             "detail": "汇总保留全部账户、年度、半年、16个历史阶段与事件分界；区间盈亏额相加、收益率复利连接，分别与完整账户核对。"}]}


def query(key, rows, columns, src, method=None):
    require(all(all(field in row for field, _ in columns) for row in rows), "Missing displayed column: " + key)
    value = {"id": key, "rows": [{field: row[field] for field, _ in columns} for row in rows],
        "columns": [{"field": field, "label": label} for field, label in columns], "source": src,
        "reportingPeriod": "2022—2023与2024—2025，两期独立账户；事件分界仅划分新期净值"}
    if method:
        value["methods"] = [{"language": "python", "code": method}]
    return value


def tables(data):
    require(len(data["accounts"]) == 10, "All ten policy accounts required")
    indexed = {(a["period"], a["policy"]): a for a in data["accounts"]}
    require(set(indexed) == {(p, m) for p in PERIODS for m in POLICIES} and len(indexed) == 10,
            "Account identities missing or duplicated")
    accounts, coverage = [], []
    for period in PERIODS:
        for policy in POLICIES:
            a = indexed[(period, policy)]
            row, curve = a["comparisonRow"], a["curveAttribution"]
            closed = curve["closedEpisodesByExitDate"]
            flat = {"period": period, "policyLabel": LABELS[policy], "policy": policy,
                **{k: row[k] for k, _ in ACCOUNT_FIELDS + TAIL_FIELDS if k not in
                   ("worstClosedPct", "p05ClosedPct", "medianHoldingSessions")},
                "worstClosedPct": closed["returnPct"]["min"], "p05ClosedPct": closed["returnPct"]["p05"],
                "medianHoldingSessions": row["holdingSessions"]["median"]}
            require(all(v is None or finite(v) for k, v in flat.items() if k not in ("period", "policyLabel", "policy")),
                    "Nonfinite account metric")
            require(finite(flat["accountReturnPct"]), "Account cumulative return unavailable")
            accounts.append(flat)
            f = curve["opportunitiesByFormationDate"]
            d = curve["opportunitiesByDecisionDate"]
            cov = {"period": period, "policyLabel": LABELS[policy], "formationTop5": f["formationTop5"],
                **f["gateStatusCounts"], "selectedSignals": d["selectedSignals"],
                "entered": d["entered"], "notEntered": d["notEntered"]}
            require(cov["formationTop5"] == sum(cov[k] for k in ("admitted", "rejected", "unknown"))
                    and cov["admitted"] == cov["selectedSignals"] == cov["entered"] + cov["notEntered"],
                    "Gate/entry coverage does not conserve")
            coverage.append(cov)
    require(len(data["comparisons"]) == 10 and len(data["descriptiveInteractionRows"]) == 2, "All registered contrasts required")
    contrast_order = ("majorityVsKnown", "improvingVsKnown", "bothVsMajority", "bothVsImproving", "knownVsOriginal")
    contrasts = [{"period": r["period"],
        "contrastType": "主比较" if r["id"] == "majorityVsKnown" else "资料完整性对照" if r["id"] == "knownVsOriginal" else "补充比较",
        "contrast": LABELS[r["candidate"]] + " − " + LABELS[r["control"]], **r["candidateMinusControl"]}
        for r in sorted(data["comparisons"], key=lambda r: (PERIODS.index(r["period"]), contrast_order.index(r["id"])))]
    interactions = [{"period": r["period"], "contrast": "两者同时 − 上涨过半 − 范围扩大 + 环境完整", **r["values"]}
                    for r in data["descriptiveInteractionRows"]]
    intervals = []
    for key, count in (("yearRows", 20), ("halfYearRows", 40), ("phaseRows", 80), ("curveEpochRows", 10)):
        require(len(data[key]) == count, "Incomplete " + key)
        values = []
        for r in data[key]:
            require(r["period"] in PERIODS and r["policy"] in POLICIES, "Unknown interval account")
            values.append({**r, "policyLabel": LABELS[r["policy"]],
                "intervalLabel": r.get("phaseTitle") or r.get("epochId") or r.get("phaseId") or r.get("id"),
                "meanExposurePct": r["exposurePct"]["mean"]})
        intervals.append(values)
    return accounts, contrasts, interactions, coverage, intervals


def payloads(data, method):
    accounts, contrasts, interactions, coverage, intervals = tables(data)
    generated = data.get("generatedAt", data.get("createdAt"))
    account_source = source([("累计净收益", "累计净收益按完整账户现金及期末股票估值计算，包含实际模拟费用和滑点。"),
        ("完整账户比较", "五种规则按相同股票机会和原入场时钟比较，实际资金与成交路径分别演化。"),
        ("已闭仓分布", "平均盈亏、胜率与尾部只描述截止日已完整退出的持仓；未平仓数另外保留。")], generated)
    phase_source = source([("区间净收益", "区间净收益从进入区间前的账户收盘净值计算至区间末，包含此前持仓的后续变化。"),
        ("历史分期", "年度、半年、全部16个历史阶段及2024年9月24日前后均为完整账户归因，不重新起算资金。")], generated)
    receipt = {"schemaVersion": 1, "items": [
        {"id": "account-paths", "title": "全部账户、准入对照与交易分布", "queries": [
            query("accounts", accounts, IDENTITY + ACCOUNT_FIELDS, account_source),
            query("contrasts", contrasts, [("period", "时期"), ("contrastType", "比较性质"), ("contrast", "差值（左减右）")] + DELTA_FIELDS, account_source),
            query("trade-tails", accounts, IDENTITY + TAIL_FIELDS, account_source),
            query("interactions", interactions, [("period", "时期"), ("contrast", "交互项定义")] + DELTA_FIELDS, account_source),
            query("coverage", coverage, IDENTITY + [("formationTop5", "原始形成机会"), ("admitted", "准入"),
                ("rejected", "条件不符"), ("unknown", "环境未知"), ("selectedSignals", "账户信号"),
                ("entered", "实际入场"), ("notEntered", "准入但未入场")], account_source, method)]},
        {"id": "complete-periods", "title": "全部年份、阶段与事件分界", "queries": [
            query(key, rows, IDENTITY + INTERVAL_FIELDS, phase_source)
            for key, rows in zip(("years", "half-years", "phases", "event-epochs"), intervals)]}]}
    chart = {"schemaVersion": 1, "id": "reclaim-breadth-returns", "queryId": "reclaim-breadth-accounts",
        "title": "市场广度准入：两期累计净收益", "height": 420,
        "chart": {"type": "horizontalBar", "x": "policyLabel", "y": VALUE, "series": "period",
            "stackable": False, "startAtZero": True, "sortOrder": "original", "valueDecimals": 2,
            "showXAxisLabel": False, "showYAxisLabel": False, "showValues": False,
            "categoryOrder": list(CHART_LABELS.values()), "seriesOrder": list(PERIODS),
            "colors": {period: f"var(--chart-{i+1})" for i, period in enumerate(PERIODS)}},
        "rows": [{"period": row["period"], "policyLabel": CHART_LABELS[row["policyLabel"]], VALUE: row["accountReturnPct"]}
                 for row in accounts],
        "columns": ["period", "policyLabel", VALUE],
        "source": account_source, "theme": "codex-classic"}
    require([[len(q["rows"]) for q in item["queries"]] for item in receipt["items"]] == TABLE_ROWS, "Receipt row coverage")
    for value in (chart, receipt):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        require(len(encoded.encode()) < 2_000_000 and "G:/" not in encoded and "C:/" not in encoded,
                "Oversized or private-path payload")
    return chart, receipt


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    values = {name: read(directory / name) for name in names}
    pins = {name: sha(directory / name) for name in names}
    plan, comparison, validation, data, readout_validation = (values[name] for name in names)
    require(validation.get("passed") is True and data["verification"]["passed"] is True, "Passed validation/readout required")
    require(validation["planSha256"] == pins["plan.json"] and validation["comparisonSha256"] == pins["comparison.json"],
            "Independent validation binding differs")
    require(data["verification"]["sha256"] == pins["validation.json"], "Readout validation binding differs")
    require(readout_validation.get("passed") is True and readout_validation["readoutSha256"] == pins["readout.json"],
            "Independent readout validation missing or stale")
    require(readout_validation["planSha256"] == pins["plan.json"]
            and readout_validation["comparisonSha256"] == pins["comparison.json"]
            and readout_validation["accountValidationSha256"] == pins["validation.json"],
            "Independent readout parent bindings differ")
    for name in ("plan.json", "comparison.json", "validation.json"):
        require(data["inputPins"][(directory / name).relative_to(ROOT).as_posix()] == pins[name], "Readout input changed: " + name)
    parent_name = "artifacts/strategy-search/strong-undercut-reclaim-timing-v1/comparison.json"
    parent_sha = sha(ROOT / parent_name)
    require(parent_sha == plan["frozenInputs"][parent_name] == data["inputPins"][parent_name],
            "Frozen immediate baseline comparison changed")
    expected_rows = comparison["rows"] + [row for row in read(ROOT / parent_name)["rows"] if row["id"] == "immediate"]
    expected = {(row["period"], row["id"]): row for row in expected_rows}
    actual = {(row["period"], row["id"]): row for row in data["accountRows"]}
    require(len(comparison["rows"]) == 8 and len(expected_rows) == len(expected) == len(data["accountRows"]) == 10
            and actual == expected, "Readout/new accounts or reused immediate baselines differ")
    require(plan["newAccounts"] == 8 and plan["newPolicyVariants"] == 4, "Registered experiment shape differs")
    for name in (METHOD, RUNNER):
        require(sha(ROOT / name) == plan["sourcePins"][name], "Frozen method changed: " + name)
    chart, receipt = payloads(data, (ROOT / METHOD).read_text("utf-8"))
    write(directory / "chart.json", chart)
    write(directory / "sources.json", receipt)
    (directory / "inline").mkdir(exist_ok=False)
    fragments = {}
    for kind, payload, filename in (("chart", "chart.json", "reclaim-breadth-returns.html"),
                                     ("sources", "sources.json", "reclaim-breadth-sources.html")):
        renderer = TOOLS / f"render-inline-{kind}.mjs"
        target = directory / "inline" / filename
        result = subprocess.run([NODE, str(renderer), "--input", str(directory / payload), "--output", str(target)],
                                text=True, capture_output=True, timeout=60)
        write(directory / f"{kind}-render-log.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, result.stdout + result.stderr)
        require(target.stat().st_size < 1_000_000, "Fragment exceeds 1 MB")
        fragments[kind] = {"path": target.relative_to(ROOT).as_posix(), "sha256": sha(target),
                           "rendererSha256": sha(renderer)}
    for name, expected in pins.items():
        require(sha(directory / name) == expected, "Render input changed: " + name)
    write(directory / "render-receipt.json", {"schema": "reclaim-breadth-entry-render-v1", "inputPins": pins,
        "builderSha256": sha(__file__), "chartPayloadSha256": sha(directory / "chart.json"),
        "payloadSha256": sha(directory / "sources.json"), "fragmentSha256": fragments["sources"]["sha256"],
        "chartFragmentSha256": fragments["chart"]["sha256"], "chartSha256": fragments["chart"]["sha256"],
        "sourceSha256": fragments["sources"]["sha256"], "fragments": fragments,
        "baselineComparison": {"path": parent_name, "sha256": parent_sha},
        "cards": 2, "tableRows": TABLE_ROWS, "chartRows": 10, "browserChecked": False,
        "actualMarketRuns": 0, "analysisArtifactsModified": False})
    return {"fragments": fragments, "tableRows": TABLE_ROWS}


def self_test():
    data = {"generatedAt": "2024-01-01T00:00:00+00:00", "accounts": [], "comparisons": [], "descriptiveInteractionRows": [],
            **{key: [] for key in ("yearRows", "halfYearRows", "phaseRows", "curveEpochRows")}}
    for period in PERIODS:
        for i, policy in enumerate(POLICIES):
            row = {key: None for key, _ in ACCOUNT_FIELDS + TAIL_FIELDS}
            row.update(accountReturnPct=float(i-2), maxDrawdownPct=20., meanExposurePct=40., signals=8,
                       closed=2, openPositions=1, holdingSessions={"median": 5.})
            data["accounts"].append({"period": period, "policy": policy, "comparisonRow": row,
                "curveAttribution": {"closedEpisodesByExitDate": {"returnPct": {"min": -8., "p05": -6.}},
                    "opportunitiesByFormationDate": {"formationTop5": 10, "gateStatusCounts": {"admitted": 8, "rejected": 1, "unknown": 1}},
                    "opportunitiesByDecisionDate": {"selectedSignals": 8, "entered": 3, "notEntered": 5}}})
            for key, n in (("yearRows", 2), ("halfYearRows", 4), ("phaseRows", 8),
                           ("curveEpochRows", 0 if period == "2022-2023" else 2)):
                for j in range(n):
                    data[key].append({"period": period, "policy": policy, "id": str(j),
                        "requestedStart": "2024-01-02", "requestedEnd": "2024-12-31", "returnPct": float(i-2),
                        "maxDrawdownPct": 20., "netPnlAmount": 0., "exposurePct": {"mean": 40.}})
        for contrast_id, candidate in zip(("knownVsOriginal", "majorityVsKnown", "improvingVsKnown", "bothVsMajority", "bothVsImproving"), POLICIES):
            data["comparisons"].append({"period": period, "id": contrast_id, "control": "immediate", "candidate": candidate,
                "candidateMinusControl": {k: None for k, _ in DELTA_FIELDS}})
        data["descriptiveInteractionRows"].append({"period": period, "values": {k: None for k, _ in DELTA_FIELDS}})
    before = deepcopy(data)
    chart, receipt = payloads(data, "# Synthetic admission method; no market data.")
    require(data == before and len(chart["rows"]) == 10, "Input mutation or chart coverage")
    require(chart["rows"][0][VALUE] == -2. and chart["rows"][2][VALUE] == 0., "Signed/zero return altered")
    require(receipt["items"][0]["queries"][0]["rows"][0]["closedMeanPct"] is None, "Unknown filled as zero")
    for error in ("missingAccount", "duplicateAccount", "missingPhase", "gateCount"):
        changed = deepcopy(data)
        if error == "missingAccount": changed["accounts"].pop()
        elif error == "duplicateAccount": changed["accounts"][-1] = deepcopy(changed["accounts"][0])
        elif error == "missingPhase": changed["phaseRows"].pop()
        else: changed["accounts"][0]["curveAttribution"]["opportunitiesByFormationDate"]["gateStatusCounts"]["unknown"] = 0
        try: payloads(changed, "# synthetic")
        except ValueError: pass
        else: raise AssertionError("Invalid synthetic accepted: " + error)
    code = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const [chart, sources] = JSON.parse(fs.readFileSync(0, 'utf8'));
normalizeInlineChartInput(chart); normalizeInlineSourcesInput(sources);
console.log(JSON.stringify({passed:true,filesWritten:0}));""" % (
        json.dumps((TOOLS / "inline-chart-input.mjs").as_uri()), json.dumps((TOOLS / "inline-sources-input.mjs").as_uri()))
    validation = subprocess.run([NODE, "--input-type=module", "-e", code],
        input=json.dumps([chart, receipt], ensure_ascii=False), text=True, capture_output=True, timeout=30)
    require(validation.returncode == 0, validation.stderr)
    return {"passed": True, "checks": ["all ten chart accounts", "all nine complete receipt tables",
        "signed and zero returns unchanged", "unknown metrics retained", "input immutability",
        "invalid coverage rejected", "shared chart and Sources payload validators"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
