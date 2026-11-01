"""Render the independently reviewed reclaim-context attribution, without accounts.

The receipt preserves every state/year/epoch row and never interprets grouped
contributions as a separately rerun filtering policy. --self-test writes nothing.
"""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/undercut-reclaim-context-diagnostic-v1"
METHOD = "service/research_reclaim_context.py"
RUNNER = "scripts/diagnose-reclaim-context.py"
EPOCH_FIELD = "period"
POLICIES = ("immediate", "delay2", "confirm2")
LABELS = {"immediate": "I｜立即决策", "delay2": "D｜无条件等待两日",
          "confirm2": "C｜等待两日且守住支撑"}
STATE_LABELS = {
    "majorityImproving": "20日上涨过半、五日广度增加",
    "majorityNotImproving": "20日上涨过半、五日广度未增加",
    "notMajorityImproving": "20日上涨未过半、五日广度增加",
    "notMajorityNotImproving": "20日上涨未过半、五日广度未增加",
    "unknown": "市场环境证据不足",
}
EPOCH_LABELS = {"2022-2023": "2022—2023旧期",
                "2024-2025-before20240924": "2024年9月24日之前",
                "2024-2025-from20240924": "2024年9月24日起"}
COUNTS = ("formationCount", "selectedSignals", "entered", "closed", "open",
          "notScheduled", "notEntered", "uniqueFormationDays", "uniqueSymbols")
METRICS = ("closedMeanPct", "winRatePct", "meanWinPct", "meanLossPct", "profitFactor",
           "minClosedPct", "top5ShareOfGrossProfitPct", "closedPnlAmount",
           "openUnrealizedPnlAmount", "openRealizedPnlAmount", "totalPnlAmount",
           "contributionPpOfInitialCapital")
STATE_COLUMNS = [
    ("formationCount", "原始形成机会"), ("selectedSignals", "实际决策信号"),
    ("entered", "实际入场"), ("closed", "已闭仓"), ("open", "期末未平"),
    ("closedPnlAmount", "已闭仓净盈亏（元）"),
    ("openUnrealizedPnlAmount", "未平仓浮盈亏（元）"),
    ("openRealizedPnlAmount", "未平仓中的已实现盈亏（元）"),
    ("totalPnlAmount", "形成环境合计贡献（元）"),
    ("contributionPpOfInitialCapital", "贡献占初始资金（百分点）"),
    ("closedMeanPct", "已闭仓等权均值（%）"), ("winRatePct", "已闭仓胜率（%）"),
    ("meanWinPct", "已闭仓平均盈利（%）"), ("meanLossPct", "已闭仓平均亏损（%）"),
    ("profitFactor", "已闭仓利润因子"), ("minClosedPct", "最差闭仓（%）"),
    ("top5ShareOfGrossProfitPct", "前五笔占已闭仓总盈利（%）"),
    ("notScheduled", "形成后未发信号"), ("notEntered", "决策后未入场"),
    ("uniqueFormationDays", "不同形成日期"), ("uniqueSymbols", "不同股票数"),
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_tables(data):
    expected = {"overallRows": 30, "formationYearRows": 60,
                "epochStateRows": 45, "curveEpochRows": 6}
    for key, count in expected.items():
        require(isinstance(data.get(key), list) and len(data[key]) == count,
                "Complete table required: " + key)
    states = {row["state"] for row in data["overallRows"]}
    require(states == set(STATE_LABELS), "All four registered known states plus unknown required")
    for table, extra in (("overallRows", ()), ("formationYearRows", ("formationYear",)),
                         ("epochStateRows", ())):
        keys, groups = set(), {}
        for row in data[table]:
            require(row["policy"] in POLICIES and row["state"] in states, "Unexpected policy/state")
            key = tuple(row[field] for field in ("period", "policy", *extra, "state"))
            require(key not in keys, "Duplicate row identity: " + table)
            keys.add(key)
            groups.setdefault(key[:-1], set()).add(row["state"])
            for field in COUNTS:
                require(type(row[field]) is int and row[field] >= 0, "Invalid count: " + field)
            for field in METRICS:
                require(row[field] is None or finite(row[field]), "Invalid numeric metric: " + field)
            require(row["formationCount"] == row["selectedSignals"] + row["notScheduled"],
                    "Formation/scheduled counts do not conserve")
            require(row["selectedSignals"] == row["entered"] + row["notEntered"],
                    "Scheduled/entered counts do not conserve")
            require(row["entered"] == row["closed"] + row["open"], "Closed/open counts do not conserve")
        require(all(group == states for group in groups.values()), "Missing zero/unknown state rows")
    require({row["period"] for row in data["epochStateRows"]} == set(EPOCH_LABELS),
            "Registered formation epochs differ")
    keys = [(row["policy"], row["phaseId"]) for row in data["curveEpochRows"]]
    require(len(set(keys)) == 6 and {p for p, _ in keys} == set(POLICIES), "Curve epoch identities differ")
    require(all(sum(p == mode for p, _ in keys) == 2 for mode in POLICIES), "Two curve epochs per policy required")
    require({phase for _, phase in keys} == {"before20240924", "from20240924"}, "Curve split identities differ")
    return expected


def display_rows(rows):
    result = deepcopy(rows)
    for row in result:
        row["policyLabel"] = LABELS[row["policy"]]
        if "state" in row:
            row["stateLabel"] = STATE_LABELS.get(row["state"], row["state"])
        if EPOCH_FIELD in row:
            row["epochLabel"] = EPOCH_LABELS.get(row[EPOCH_FIELD], row[EPOCH_FIELD])
    return result


LIMITS = [
    "本轮只给已有六个账户连接事前市场环境并完整归因；没有新增交易账户、过滤策略、收益门槛或参数搜索。",
    "2024年9月24日由用户指定为观察分界。分界前后是同一新期账户的连续净值区间，包含之前买入的持仓；此日期不是自动判定繁荣的交易信号。",
    "形成环境使用T−1已知的同一配对样本：20日上涨广度是否严格大于50%，以及相对五个源交易日前的广度变化是否严格大于0；相等归入非大于一侧，资料不满足固定口径保留未知。",
    "配对数量、覆盖比例及其原始分母说明保留；本轮没有新增覆盖率筛选门槛。覆盖率缺失本身不会抹掉其他完整广度证据。",
    "广度是固定主板样本中20日收益为正的比例，不能替代每日上涨家数、完整A股市场或量化交易者/主力资金身份。",
    "各政策使用同一个形成日环境。I于T决策；D和C于T+2决策；不能把等待期间新出现的信息倒填为原形成日已知。",
    "形成环境贡献是原账户实际发生的交易及期末持仓的归属，并非只交易该环境后重跑的独立账户收益。未入场的形成机会不是零收益交易。",
    "已闭仓均值只覆盖实际成交且在截止日前完整退出的交易，受资金和仓位选择、退出时点与期末删失影响；不能直接当成该环境中所有信号的期望收益。",
    "未平仓浮盈亏与已闭仓净盈亏单列。持仓可能跨过日期分界或市场环境，整笔交易按形成日归属不等于它在该环境持续期间赚到的金额。",
    "贡献百分点使用原账户初始50万元作为共同分母，不是环境子账户收益率；两个时期各自独立起步，不拼接为四年账户。",
    "同日多个形成机会、重复股票与重叠持仓具有依赖性；日期数和股票数均保留，不能把每条机会当作独立市场实验。",
    "全部四个已知状态和未知状态、全部年份与三个年代段均保留。原16个事后市场标签未用于筛选。",
    "已有历史已用于多轮研究；存续主板样本、原始价格、历史资格/权益、开盘成交容量与账户估值标记的限制继续存在，独立复核不代表收益认证。",
]


def receipt(data, method_text):
    validate_tables(data)
    executed_at = data.get("generatedAt", data.get("createdAt"))

    def source(definitions):
        return {"label": "支撑收回策略的事前环境与2024年9月24日分界诊断",
            "files": [{"label": name} for name in ("plan.json", "readout.json", "validation.json", "contexts.json", "records.jsonl.gz",
                "strong-undercut-reclaim-timing-v1/readout.json", Path(METHOD).name,
                "market-state-panel-v1/daily.jsonl.gz", "supplemental-market-state-panel-v1/daily.jsonl.gz")],
            "metricDefinitions": [{"label": label, "definition": definition} for label, definition in definitions],
            "filters": ["保留全部3803个原始形成机会及三种政策终态", "所有状态包含空组和未知组",
                "形成环境只读取T−1已知的配对样本广度", "不改变原信号、实际交易、成本或退出规则",
                "日期分界为2024-09-24；不选择事后表现较好的市场阶段"],
            "caveats": LIMITS, "executedAt": executed_at,
            "evidenceFlow": [{"kind": "validation", "title": "输入与归因绑定复核",
                "detail": "独立验证绑定冻结计划、已有六账户及诊断结果；全部形成机会保留，状态覆盖、交易身份、开平仓贡献和曲线区间分别对账。"},
                {"kind": "validation", "title": "完整展示",
                 "detail": "6条分界曲线、45条年代段与状态、30条全期与状态、60条形成年度与状态记录全部展示，不省略未知、空组或不利结果。"}]}

    curve_source = source([("分界区间收益", "同一新期账户从区间开始前一交易日净值至区间末的收益；无前日时用原初始资金。包含携带持仓。"),
        ("区间回撤", "以区间进入净值为初始峰值，覆盖区间全部账户收盘净值。")])
    state_source = source([("形成环境贡献", "按原形成日环境分配已闭仓整笔净盈亏、未平仓已实现盈亏及截止日浮盈亏；不是状态过滤账户收益。"),
        ("贡献百分点", "形成环境合计贡献除以原账户初始50万元再乘100；不能替代净值区间收益。"),
        ("已闭仓均值", "已完成交易净回报的等权均值，与资金加权净盈亏分开；未平与未入场不放入该均值。"),
        ("观测数量", "形成、发出信号、入场、闭仓、未平、未发信号及未入场完整保留；独立形成日期和股票数单列。")])

    def query(key, rows, columns, src, with_method=False):
        value = {"id": key, "reportingPeriod": "2022—2023与2024—2025；新期再按2024-09-24分界",
            "rows": deepcopy(rows), "columns": [{"field": k, "label": label} for k, label in columns], "source": src}
        require(all(all(k in row for k, _ in columns) for row in rows), "Display column missing")
        if with_method:
            value["methods"] = [{"language": "python", "code": method_text}]
        return value

    identity = [("period", "原账户时期"), ("policyLabel", "入场政策")]
    state_identity = identity + [("stateLabel", "形成时环境")]
    return {"schemaVersion": 1, "items": [
        {"id": "epoch-attribution", "title": "2024年9月24日前后：账户曲线与形成贡献", "queries": [
            query("curve-epochs", display_rows(data["curveEpochRows"]), [("policyLabel", "入场政策"),
                ("requestedStart", "区间开始"), ("requestedEnd", "区间结束"),
                ("enteringEquity", "进入净值（元）"), ("endingEquity", "结束净值（元）"),
                ("returnPct", "区间收益（%）"), ("maxDrawdownPct", "区间最大回撤（%）"),
                ("netPnlAmount", "区间净盈亏（元）")], curve_source),
            query("epoch-states", display_rows(data["epochStateRows"]), [("epochLabel", "形成所属年代段"), ("policyLabel", "入场政策"),
                ("stateLabel", "形成时环境")] + STATE_COLUMNS, state_source, True)]},
        {"id": "complete-state-coverage", "title": "全部环境与全部形成年度", "queries": [
            query("overall-states", display_rows(data["overallRows"]), state_identity + STATE_COLUMNS, state_source),
            query("formation-years", display_rows(data["formationYearRows"]), identity + [("formationYear", "形成年度"),
                ("stateLabel", "形成时环境")] + STATE_COLUMNS, state_source)]},
    ]}


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ("plan.json", "readout.json", "validation.json")
    values = {name: json.loads((directory / name).read_text("utf-8")) for name in names}
    pins = {name: sha(directory / name) for name in names}
    plan, data, validation = (values[name] for name in names)
    require(validation.get("passed") is True, "Independent validation must pass before rendering")
    require(validation.get("planSha256") == pins["plan.json"], "Validation plan binding differs")
    require(validation.get("readoutSha256") == pins["readout.json"], "Validation readout binding differs")
    require(data.get("planSha256") == pins["plan.json"], "Diagnostic plan binding differs")
    require(sha(ROOT / METHOD) == plan["sourcePins"][METHOD], "Frozen method changed")
    require(sha(ROOT / RUNNER) == plan["sourcePins"][RUNNER], "Frozen diagnostic runner changed")
    require(data["inputPins"][METHOD] == plan["sourcePins"][METHOD]
            and data["inputPins"][RUNNER] == plan["sourcePins"][RUNNER], "Method input binding differs")
    require(data.get("newAccounts") == data.get("newPolicyVariants") == 0,
            "This receipt is exclusively for a zero-account diagnostic")
    for field, name in (("contexts", "contexts.json"), ("records", "records.jsonl.gz")):
        pins[name] = sha(directory / name)
        require(data[field]["sha256"] == pins[name], "Derived diagnostic evidence changed: " + name)
    payload = receipt(data, (ROOT / METHOD).read_text("utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    require("G:/" not in serialized and "C:/" not in serialized and len(serialized.encode()) < 2_000_000,
            "Payload contains a local path or exceeds the compact receipt budget")

    def write(name, value):
        with (directory / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")

    write("sources.json", payload)
    (directory / "inline").mkdir(exist_ok=False)
    node = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    renderer = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs")
    fragment = directory / "inline/reclaim-context-sources.html"
    result = subprocess.run([node, str(renderer), "--input", str(directory / "sources.json"), "--output", str(fragment)],
                            text=True, capture_output=True, timeout=60)
    write("sources-render-log.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    require(result.returncode == 0, result.stdout + result.stderr)
    for name, expected in pins.items():
        require(sha(directory / name) == expected, "Input changed during rendering: " + name)
    require(sha(ROOT / METHOD) == plan["sourcePins"][METHOD], "Method changed during rendering")
    require(sha(ROOT / RUNNER) == plan["sourcePins"][RUNNER], "Runner changed during rendering")
    write("render-receipt.json", {"inputPins": pins, "builderSha256": sha(Path(__file__)),
        "payloadSha256": sha(directory / "sources.json"), "fragmentSha256": sha(fragment),
        "rendererSha256": sha(renderer), "methodModuleSha256": sha(ROOT / METHOD), "diagnosticRunnerSha256": sha(ROOT / RUNNER),
        "cards": 2, "tableRows": [6, 45, 30, 60], "newAccounts": 0, "browserChecked": False})
    return {"fragmentSha256": sha(fragment), "bytes": fragment.stat().st_size, "tableRows": [6, 45, 30, 60]}


def self_test():
    states = tuple(STATE_LABELS)
    def row(period, policy, state):
        value = {"period": period, "policy": policy, "state": state,
            **{key: 0 for key in COUNTS}, **{key: None for key in METRICS}}
        value.update(formationCount=1, selectedSignals=1, notEntered=1,
            uniqueFormationDays=1, uniqueSymbols=1, closedPnlAmount=0.,
            openUnrealizedPnlAmount=0., openRealizedPnlAmount=0., totalPnlAmount=0., contributionPpOfInitialCapital=0.)
        return value
    data = {"generatedAt": "synthetic", "overallRows": [], "formationYearRows": [], "epochStateRows": [], "curveEpochRows": []}
    for period in ("2022-2023", "2024-2025"):
        for policy in POLICIES:
            for state in states:
                base = row(period, policy, state)
                data["overallRows"].append(base)
                for year in map(int, period.split("-")):
                    data["formationYearRows"].append({**base, "formationYear": year})
                for epoch in (["2022-2023"] if period == "2022-2023" else ["2024-2025-before20240924", "2024-2025-from20240924"]):
                    data["epochStateRows"].append({**base, EPOCH_FIELD: epoch})
    for policy in POLICIES:
        for phase, start, end in (("before20240924", "2024-01-02", "2024-09-23"), ("from20240924", "2024-09-24", "2025-12-31")):
            data["curveEpochRows"].append({"policy": policy, "phaseId": phase,
                "requestedStart": start, "requestedEnd": end, "enteringEquity": 500000., "endingEquity": 500000.,
                "returnPct": 0., "maxDrawdownPct": 0., "netPnlAmount": 0.})
    payload = receipt(data, "# synthetic method")
    tables = [query for card in payload["items"] for query in card["queries"]]
    require([len(table["rows"]) for table in tables] == [6, 45, 30, 60], "Complete four-table shape")
    require(set(COUNTS + METRICS) <= set(tables[1]["rows"][0]), "State fields discarded")
    require({field for field, _ in STATE_COLUMNS} == set(COUNTS + METRICS), "State fields not inspectable")
    require(tables[1]["rows"][0]["closedMeanPct"] is None, "Missing returns converted to zero")
    for case in ("dropUnknown", "badCounts", "badNumeric", "duplicate"):
        bad = deepcopy(data)
        if case == "dropUnknown": bad["overallRows"].pop()
        elif case == "badCounts": bad["overallRows"][0]["entered"] = 1
        elif case == "badNumeric": bad["overallRows"][0]["closedMeanPct"] = float("nan")
        else: bad["overallRows"][-1] = deepcopy(bad["overallRows"][0])
        try: validate_tables(bad)
        except ValueError: pass
        else: raise AssertionError("Malformed diagnostic accepted: " + case)
    return {"passed": True, "checks": ["four complete tables", "all state fields inspectable",
        "null metrics retained", "counts conserved", "unknown and duplicate identities checked", "finite numeric values"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
