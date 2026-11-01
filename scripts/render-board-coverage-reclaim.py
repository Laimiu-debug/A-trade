"""Render verified board-coverage results with the shared Data chart/Sources tools.

Only presentation is produced. Synthetic --self-test never loads market data;
real rendering requires both account and readout validation and is exclusive.
"""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/board-coverage-reclaim-v1"
METHOD = "service/research_undercut_reclaim.py"
RUNNER = "scripts/run-board-coverage-reclaim.py"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
LABELS = dict(zip(BOARDS, ("主板", "创业", "科创")))
VALUE = "累计净收益 (%)"
TABLE_ROWS = [[6, 6, 6], [12, 24, 48, 6]]
IDENTITY = [("period", "时期"), ("boardLabel", "板块")]
ACCOUNT_FIELDS = [("accountReturnPct", "累计净收益（%）"), ("maxDrawdownPct", "最大回撤（%）"),
    ("meanExposurePct", "平均仓位（%）"), ("signals", "信号数"), ("closed", "完整平仓"),
    ("openPositions", "期末持仓"), ("closedMeanPct", "平仓平均净收益（%）"),
    ("meanWinPct", "平均盈利（%）"), ("meanLossPct", "平均亏损（%）"), ("profitFactor", "利润因子")]
TAIL_FIELDS = [("winRatePct", "平仓胜率（%）"), ("payoffRatio", "平均盈亏比"),
    ("worstClosedPct", "最差平仓（%）"), ("p05ClosedPct", "平仓5分位（%）"),
    ("medianHoldingSessions", "持仓交易日中位数"), ("maxConsecutiveLosses", "最大连续亏损次数"),
    ("top5ShareOfGrossProfitPct", "前五笔占总盈利（%）")]
COVERAGE_FIELDS = [("discoveredIdentityCount", "本地发现身份"), ("includedCount", "期初纳入"),
    ("excludedCount", "期初排除"), ("unknownCount", "身份未知"),
    ("formationTop5", "原始形成机会"), ("selectedSignals", "账户信号"),
    ("entered", "实际入场"), ("notEntered", "未入场"),
    ("benchmarkUnavailableDays", "基准窗口不完整日"), ("valuationUnreliableDays", "估值不可靠日")]
INTERVAL_FIELDS = [("intervalLabel", "分期"), ("requestedStart", "开始"), ("requestedEnd", "结束"),
    ("returnPct", "区间净收益（%）"), ("maxDrawdownPct", "区间最大回撤（%）"),
    ("netPnlAmount", "区间净盈亏（元）"), ("meanExposurePct", "平均仓位（%）")]
LIMITS = [
    "本次实际行情期为2022—2025年，2026年未纳入；两期历史均已用于研究，不是样本外检验或稳定收益认证。",
    "本地枚举5261个主板、创业板和科创板身份，各期只纳入期初至少有61个有效既往观测日的股票。观测起点不是上市日，目标期新股不覆盖；当前本地文件也可能遗漏已退市股票。",
    "每次形成仍须有连续61个事前日历槽的合格价格，执行原强股跌破支撑后收回的立即入场规则。保留的minListingDays120配置未在固定信号账户中实际执行，不能称为额外120日门槛。",
    "六个账户全部重新执行，各自从50万元起步、按板块内排名取Top5，不是三个板块共享资金的合并组合。与旧3141样本的源和身份口径不同，不能把结果称为单纯扩围。",
    "累计净收益含费用、每边10基点滑点及期末持仓估值；两期不能拼接成四年账户。板块差异同时包含样本、排序、成交制度和资金路径差异，不能解释为板块的因果效应。",
    "原5%止损、10%跟踪及第五日无进展退出保留；收盘触发后在可成交开盘执行，跳空或退出受限可使损失超过名义阈值。创业板、科创板的20%涨跌幅与手数差异由原账户处理。",
    "已闭仓分布不含未平仓和未成交机会；前五笔盈利占比只描述盈利集中度。完整机会流水另存为带哈希的压缩文件，所有机会保留，未在图内截取赢家。",
    "年度、半年、全部16个历史阶段与2024年9月24日前后均保留，分期继承净值和持仓。日期与事后情绪描述未用于准入，9月24日之后也不能概括为单一普涨或量化风格。",
    "板块不等于科技行业：主板有科技公司，创业板和科创板也不是纯科技样本。历史ST、成员资格、上市日期与公司行为资料仍不完整，未知状态没有被当成已认证的可交易资格。",
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


def source(definitions, data):
    artifact = data["opportunityRowsArtifact"]
    return {"label": "强股支撑收回的板块覆盖比较（2022—2025）",
        "files": [{"label": name} for name in ("plan.json", "comparison.json", "validation.json", "readout.json",
            "readout-validation.json", "source-manifest.json", "research_undercut_reclaim.py", "market-phase-atlas-v1/atlas.json")]
            + [{"label": f"opportunity-dispositions.jsonl.gz：{artifact['count']}条；SHA256 {artifact['sha256']}"}],
        "metricDefinitions": [{"label": label, "definition": definition} for label, definition in definitions],
        "filters": ["两个历史时期、三个板块、六个独立新账户，全量展示", "各期期初至少61个有效既往观测日，固定身份",
            "相对强度统一以沪深300（sh000300）为基准，不是各板块指数；图中为账户净收益，并非超额收益",
            "原UR机制，板块内相对强度排名与流动性Top5；没有按未来赢家筛样本", "立即入场，20%入场预算与五个持仓上限不变"],
        "caveats": LIMITS, "executedAt": data["generatedAt"],
        "evidenceFlow": [{"kind": "validation", "title": "来源、信号与六账户复核",
            "detail": "独立核验本地截止日前缀、身份纳入、日历和冻结计划，重算UR形成与原Top5，再核对全部六个新账户。"},
            {"kind": "validation", "title": "汇总与完整分期复核",
             "detail": "单独验证全部账户、12年表行、24半年表行、48阶段表行与6事件分界表行；盈亏额加总、收益率复利连接，并与完整账户对账。"}]}


def query(key, rows, columns, src, method=None):
    require(all(all(field in row for field, _ in columns) for row in rows), "Missing displayed column: " + key)
    value = {"id": key, "rows": [{field: row[field] for field, _ in columns} for row in rows],
        "columns": [{"field": field, "label": label} for field, label in columns], "source": src,
        "reportingPeriod": "2022—2023与2024—2025，两期独立账户；2026未纳入"}
    if method:
        value["methods"] = [{"language": "python", "code": method}]
    return value


def indexed(rows):
    result = {(r["period"], r["board"]): r for r in rows}
    require(len(rows) == len(result) == 6 and set(result) == {(p, b) for p in PERIODS for b in BOARDS},
            "Exactly six unique board/period accounts required")
    return result


def tables(data):
    by_account, by_coverage = indexed(data["accounts"]), indexed(data["coverageRows"])
    accounts, coverage = [], []
    for period in PERIODS:
        for board in BOARDS:
            a, c = by_account[(period, board)], by_coverage[(period, board)]
            require(a["policy"] == c["policy"] == "immediate", "Unexpected entry policy")
            row, curve = a["comparisonRow"], a["curveAttribution"]
            require((row["period"], row["board"], row["id"]) == (period, board, "immediate"), "Account row identity differs")
            closed = curve["closedEpisodesByExitDate"]
            flat = {"period": period, "board": board, "boardLabel": LABELS[board],
                **{k: row[k] for k, _ in ACCOUNT_FIELDS + TAIL_FIELDS if k not in
                   ("worstClosedPct", "p05ClosedPct", "medianHoldingSessions")},
                "worstClosedPct": closed["returnPct"]["min"], "p05ClosedPct": closed["returnPct"]["p05"],
                "medianHoldingSessions": row["holdingSessions"]["median"]}
            require(all(v is None or finite(v) for k, v in flat.items() if k not in ("period", "board", "boardLabel")),
                    "Nonfinite account metric")
            require(finite(flat["accountReturnPct"]) and finite(flat["maxDrawdownPct"]), "Return/drawdown unavailable")
            accounts.append(flat)
            cov = {"period": period, "boardLabel": LABELS[board],
                **{k: c["sourceCohort"][k] for k in ("discoveredIdentityCount", "includedCount", "excludedCount", "unknownCount")},
                **{k: c[k] for k, _ in COVERAGE_FIELDS if k not in c["sourceCohort"]}}
            require(cov["discoveredIdentityCount"] == sum(cov[k] for k in ("includedCount", "excludedCount", "unknownCount")),
                    "Identity coverage does not conserve")
            require(cov["formationTop5"] == cov["selectedSignals"] == cov["entered"] + cov["notEntered"],
                    "Opportunity/entry counts do not conserve")
            coverage.append(cov)
    require(data["opportunityRowsArtifact"]["count"] == sum(c["formationTop5"] for c in coverage), "Sidecar opportunity count differs")
    intervals = []
    for key, count in (("yearRows", 12), ("halfYearRows", 24), ("phaseRows", 48), ("curveEpochRows", 6)):
        require(len(data[key]) == count, "Incomplete " + key)
        values, identities = [], set()
        for r in data[key]:
            require(r["period"] in PERIODS and r["board"] in BOARDS and r["policy"] == "immediate", "Unknown interval account")
            label = r.get("phaseTitle") or r.get("phaseId") or r.get("id")
            identity = (r["period"], r["board"], r["requestedStart"], r["requestedEnd"])
            require(label is not None and identity not in identities, "Missing or duplicated interval identity")
            identities.add(identity)
            values.append({**r, "boardLabel": LABELS[r["board"]], "intervalLabel": label,
                "meanExposurePct": r["exposurePct"]["mean"]})
        intervals.append(values)
    return accounts, coverage, intervals


def payloads(data, method):
    accounts, coverage, intervals = tables(data)
    account_source = source([("累计净收益", "完整账户现金与截止日股票估值相对初始50万元的变化，包含实际模拟费用和滑点。"),
        ("最大回撤", "完整账户净值从历史峰值至后续谷值的最大降幅；与累计收益、暴露和交易尾部一起观察。"),
        ("已闭仓分布", "平均盈亏、胜率和尾部只描述截止日已完整退出的持仓；期末未平仓另列。"),
        ("身份与机会覆盖", "身份计数来自每期固定群体；形成机会是逐日Top5次数，不能当成不同股票数。")], data)
    phase_source = source([("区间净收益", "从区间前最后一个收盘账户净值计算至区间末，包含跨期持仓变化；不是在每个区间重新建仓。"),
        ("历史分期", "全16个历史阶段及2024年9月24日前后均保留；金额可相加，收益率须复利连接。")], data)
    sources = {"schemaVersion": 1, "items": [
        {"id": "board-accounts", "title": "六个账户、交易尾部与覆盖", "queries": [
            query("accounts", accounts, IDENTITY + ACCOUNT_FIELDS, account_source),
            query("trade-tails", accounts, IDENTITY + TAIL_FIELDS, account_source),
            query("coverage", coverage, IDENTITY + COVERAGE_FIELDS, account_source, method)]},
        {"id": "complete-periods", "title": "全部年份、阶段与事件分界", "queries": [
            query(key, rows, IDENTITY + INTERVAL_FIELDS, phase_source)
            for key, rows in zip(("years", "half-years", "phases", "event-epochs"), intervals)]}]}
    chart = {"schemaVersion": 1, "id": "board-coverage-returns", "queryId": "board-coverage-accounts",
        "title": "板块覆盖比较：两期累计净收益", "height": 360,
        "chart": {"type": "horizontalBar", "x": "boardLabel", "y": VALUE, "series": "period",
            "stackable": False, "startAtZero": True, "sortOrder": "original", "valueDecimals": 2,
            "showXAxisLabel": False, "showYAxisLabel": False, "showValues": False,
            "categoryOrder": list(LABELS.values()), "seriesOrder": list(PERIODS),
            "colors": {period: f"var(--chart-{i+1})" for i, period in enumerate(PERIODS)}},
        "rows": [{"period": r["period"], "boardLabel": r["boardLabel"], VALUE: r["accountReturnPct"]} for r in accounts],
        "columns": ["period", "boardLabel", VALUE], "source": account_source, "theme": "codex-classic"}
    require([[len(q["rows"]) for q in item["queries"]] for item in sources["items"]] == TABLE_ROWS, "Receipt row coverage differs")
    for value in (chart, sources):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        require(len(encoded.encode()) < 1_000_000 and "G:/" not in encoded and "C:/" not in encoded, "Oversized or private-path payload")
    return chart, sources


def build(directory=OUT):
    directory = Path(directory).resolve()
    names = ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    values = {name: read(directory / name) for name in names}
    pins = {name: sha(directory / name) for name in names}
    plan, comparison, validation, data, readout_validation = (values[name] for name in names)
    require(validation.get("passed") is True and data["verification"]["passed"] is True
            and validation.get("failureCount", 0) == 0, "Passed account/readout verification required")
    require(validation["planSha256"] == pins["plan.json"] and validation["comparisonSha256"] == pins["comparison.json"], "Account validation binding differs")
    require(data["verification"]["sha256"] == pins["validation.json"] and data["planSha256"] == pins["plan.json"], "Readout account/plan binding differs")
    require(readout_validation.get("passed") is True and readout_validation["readoutSha256"] == pins["readout.json"], "Independent readout verification missing or stale")
    require(readout_validation["planSha256"] == pins["plan.json"]
            and readout_validation["comparisonSha256"] == pins["comparison.json"]
            and readout_validation["accountValidationSha256"] == pins["validation.json"], "Readout validation parent bindings differ")
    for name in ("plan.json", "comparison.json", "validation.json"):
        require(data["inputPins"][(directory / name).relative_to(ROOT).as_posix()] == pins[name], "Readout input changed: " + name)
    require(indexed(data["accountRows"]) == indexed(comparison["rows"]), "Readout/comparison account rows differ")
    require(plan["newAccounts"] == 6 and plan["newUniverseVariants"] == 3 and plan["referenceAccountsReused"] == 0
            and plan["newSetupMechanisms"] == plan["newEntryPolicies"] == 0, "Registered experiment shape differs")
    for name in (METHOD, RUNNER):
        require(sha(ROOT / name) == plan["sourcePins"][name], "Frozen method changed: " + name)
    artifact = data["opportunityRowsArtifact"]
    artifact_path = (ROOT / artifact["path"]).resolve()
    require(artifact_path.parent == directory and artifact["format"] == "jsonl+gzip", "Opportunity artifact must be in this output family")
    pins[artifact_path.name] = sha(artifact_path)
    require(pins[artifact_path.name] == artifact["sha256"] == readout_validation["opportunityRowsArtifactSha256"], "Opportunity sidecar binding differs")
    chart, sources = payloads(data, (ROOT / METHOD).read_text("utf-8"))
    destinations = ("chart.json", "sources.json", "inline", "render-receipt.json", "chart-render-log.json", "sources-render-log.json")
    require(not any((directory / name).exists() for name in destinations), "Render outputs already exist; do not overwrite")
    write(directory / "chart.json", chart)
    write(directory / "sources.json", sources)
    (directory / "inline").mkdir()
    fragments = {}
    for kind, filename in (("chart", "board-coverage-returns.html"), ("sources", "board-coverage-sources.html")):
        renderer = TOOLS / f"render-inline-{kind}.mjs"
        target = directory / "inline" / filename
        result = subprocess.run([NODE, str(renderer), "--input", str(directory / (kind + ".json")), "--output", str(target)],
                                text=True, capture_output=True, timeout=60)
        write(directory / f"{kind}-render-log.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, result.stdout + result.stderr)
        require(target.stat().st_size < 1_000_000, "Fragment exceeds 1 MB")
        fragments[kind] = {"path": target.relative_to(directory).as_posix(), "sha256": sha(target), "rendererSha256": sha(renderer)}
    for name, expected in pins.items():
        require(sha(directory / name) == expected, "Render input changed: " + name)
    write(directory / "render-receipt.json", {"schema": "board-coverage-reclaim-render-v1", "inputPins": pins,
        "builderSha256": sha(__file__), "chartPayloadSha256": sha(directory / "chart.json"),
        "payloadSha256": sha(directory / "sources.json"), "chartSha256": fragments["chart"]["sha256"],
        "sourceSha256": fragments["sources"]["sha256"], "fragments": fragments,
        "cards": 2, "tableRows": TABLE_ROWS, "chartRows": 6, "opportunityRowsArtifact": artifact,
        "dataPeriod": "2022-2025", "includes2026": False, "historicalSampleAlreadyObserved": True,
        "holdout": False, "boardIsIndustry": False, "browserChecked": False,
        "actualMarketRuns": 0, "analysisArtifactsModified": False})
    return {"fragments": fragments, "tableRows": TABLE_ROWS, "renderReceiptSha256": sha(directory / "render-receipt.json")}


def self_test():
    data = {"generatedAt": "2025-12-31T00:00:00+00:00", "accounts": [], "coverageRows": [],
        "opportunityRowsArtifact": {"count": 48, "sha256": "a" * 64},
        **{key: [] for key in ("yearRows", "halfYearRows", "phaseRows", "curveEpochRows")}}
    for period in PERIODS:
        for i, board in enumerate(BOARDS):
            row = {key: None for key, _ in ACCOUNT_FIELDS + TAIL_FIELDS}
            row.update(period=period, board=board, id="immediate", accountReturnPct=float(i-1),
                maxDrawdownPct=20., meanExposurePct=40., signals=8, closed=2, openPositions=1, holdingSessions={"median": None})
            data["accounts"].append({"period": period, "board": board, "policy": "immediate", "comparisonRow": row,
                "curveAttribution": {"closedEpisodesByExitDate": {"returnPct": {"min": None, "p05": None}}}})
            data["coverageRows"].append({"period": period, "board": board, "policy": "immediate",
                "sourceCohort": {"discoveredIdentityCount": 10, "includedCount": 8, "excludedCount": 1, "unknownCount": 1},
                "formationTop5": 8, "selectedSignals": 8, "entered": 3, "notEntered": 5,
                "benchmarkUnavailableDays": 0, "valuationUnreliableDays": 0})
            for key, n in (("yearRows", 2), ("halfYearRows", 4), ("phaseRows", 8), ("curveEpochRows", 2 if period == PERIODS[1] else 0)):
                for j in range(n):
                    data[key].append({"period": period, "board": board, "policy": "immediate", "id": str(j),
                        "requestedStart": f"{period[:4]}-{j+1:02d}-01", "requestedEnd": f"{period[:4]}-{j+1:02d}-28",
                        "returnPct": float(i-1), "maxDrawdownPct": 20., "netPnlAmount": 0., "exposurePct": {"mean": 40.}})
    before = deepcopy(data)
    chart, sources = payloads(data, "# Synthetic method; no market data.")
    require(data == before and len(chart["rows"]) == 6, "Input mutation or chart coverage")
    require([r[VALUE] for r in chart["rows"][:3]] == [-1., 0., 1.], "Signed/zero returns changed")
    require(sources["items"][0]["queries"][0]["rows"][0]["closedMeanPct"] is None, "Unknown filled as zero")
    for error in ("missingAccount", "duplicateAccount", "missingPhase", "opportunityCount", "identityCount"):
        changed = deepcopy(data)
        if error == "missingAccount": changed["accounts"].pop()
        elif error == "duplicateAccount": changed["accounts"][-1] = deepcopy(changed["accounts"][0])
        elif error == "missingPhase": changed["phaseRows"].pop()
        elif error == "opportunityCount": changed["opportunityRowsArtifact"]["count"] = 1
        else: changed["coverageRows"][0]["sourceCohort"]["unknownCount"] = 0
        try:
            payloads(changed, "# synthetic")
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid synthetic accepted: " + error)
    code = """import fs from 'node:fs';
import {normalizeInlineChartInput} from %s;
import {normalizeInlineSourcesInput} from %s;
const [chart, sources] = JSON.parse(fs.readFileSync(0, 'utf8'));
normalizeInlineChartInput(chart); normalizeInlineSourcesInput(sources);
console.log(JSON.stringify({passed:true,filesWritten:0}));""" % (
        json.dumps((TOOLS / "inline-chart-input.mjs").as_uri()), json.dumps((TOOLS / "inline-sources-input.mjs").as_uri()))
    validation = subprocess.run([NODE, "--input-type=module", "-e", code],
        input=json.dumps([chart, sources], ensure_ascii=False), text=True, capture_output=True, timeout=30)
    require(validation.returncode == 0, validation.stderr)
    return {"passed": True, "checks": ["six independent accounts", "seven complete source tables", "signed and zero returns",
        "unknown metrics preserved", "input immutability", "invalid coverage rejected", "shared payload validators"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
