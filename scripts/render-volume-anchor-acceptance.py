"""Presentation only for the frozen volume-anchor acceptance experiment.

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
OUT = ROOT / "artifacts/strategy-search/volume-anchor-acceptance-v1"
METHOD = "service/research_volume_anchor.py"
RUNNER = "scripts/run-volume-anchor-acceptance.py"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("immediate", "delay3", "accept3")
BOARD_LABELS = dict(zip(BOARDS, ("主板", "创业", "科创")))
MODE_LABELS = dict(zip(MODES, ("启动即入", "等待三日", "三日承接")))
VALUE = "累计净收益差（百分点）"
TABLE_ROWS = [[12, 18, 18, 6, 18, 36, 72, 144, 18]]
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
    ("unknownCount", "身份未知"), ("setupCount", "全部启动候选次数"), ("formationTop5", "形成日Top5机会"),
    ("uniqueSymbols", "不同股票数"), ("anchorUnknownCount", "锚定条件未知股票日"),
    ("passed", "三日均承接"), ("failed", "完整但未承接"), ("unknown", "证据未知"), ("censored", "期末截尾"),
    ("knownFailedStepsInsideCensored", "截尾中已知失败步骤"), ("unknownStepsInsideCensored", "截尾中未知步骤")]
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
    "主比较是三日承接减等待三日的完整账户差，同时列出平均仓位差和两账户估值不可靠日；正收益差不能单独证明信号优势、因果收益或稳定性。",
    "三日承接与等待三日使用同一完整三日证据时钟；次比较等待三日减启动即入同时包含等待、证据可用性与资金执行路径变化。",
    "成交均价是启动日至当日的累计成交额除以成交股数，不是持仓成本、主力净流入或控盘身份。",
    "全部18个账户独立从50万元起步，保留费用、每边10基点滑点及期末估值；两期不能拼接成四年账户，三板块也不是共享资金组合。",
    "估值不可靠日是原账户质量标记；期末未实现盈亏单列，已闭仓均值和尾部不含未平仓、未成交或未安排机会。",
    "形成日先按前20日平均成交额取Top5，再应用三个入场时钟；期末不足三日优先记截尾，部分失败和未知步骤仍保留，不推断后续结果。",
    "原5%收盘止损、10%跟踪与第五日无进展退出不变，后续可成交开盘执行；跳空和退出受限可使亏损超过名义止损。",
    "各期期初至少61个有效既往观测日的本地固定身份会遗漏新股和缺失文件；历史成员、ST与公司行为资料不完整。minListingDays120和minAmount20保留为兼容配置，未作为额外固定信号准入。",
    "年份、半年、全部16个事后阶段与2024年9月24日前后均继承原净值及跨期持仓，未重建账户，也未作为入场筛选。阶段分布只支持历史描述。",
    "本轮只有2022—2025年，2026未纳入；两期已用于研究，不是留出样本。板块不等于科技行业；账户若列超额收益，其共同基准为沪深300而非板块匹配指数。",
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
            "terminalReasons": json.dumps(c["terminalReasonCounts"], ensure_ascii=False, sort_keys=True)})
    for c in by_formation.values():
        require(sum(c["acceptanceStateCounts"].values()) == c["formationTop5"], "Acceptance states do not conserve")
        formation.append({**c, **labels(c), **{k: c["sourceCohort"][k] for k in ("includedCount", "excludedCount", "unknownCount")},
            "setupCount": c["counts"]["setupCount"], "anchorUnknownCount": c["formationEvidenceTotals"]["anchorUnknownCount"],
            **c["acceptanceStateCounts"]})
    require(sum(c["formationTop5"] for c in formation) == data["opportunityRowsArtifact"]["count"], "Original opportunity count differs")
    identities = set()
    for row in data["comparisons"]:
        key = (row["period"], row["board"], row["id"])
        require(key not in identities and row["id"] in ("primary", "secondary"), "Duplicated/unknown contrast")
        identities.add(key)
        control, candidate = ("delay3", "accept3") if row["id"] == "primary" else ("immediate", "delay3")
        require((row["control"], row["candidate"]) == (control, candidate), "Contrast direction differs")
        delta, c, k = row["candidateMinusControl"], row["candidateMetrics"], row["controlMetrics"]
        contrasts.append({**labels(row), "contrast": row["id"], "contrastLabel": MODE_LABELS[candidate] + "−" + MODE_LABELS[control],
            "returnDifferencePp": delta["accountReturnPct"], "drawdownDifferencePp": delta["maxDrawdownPct"],
            "exposureDifferencePp": delta["meanExposurePct"], "candidateReturnPct": c["accountReturnPct"], "controlReturnPct": k["accountReturnPct"],
            "candidateValuationDays": c["unreliableValuationDays"], "controlValuationDays": k["unreliableValuationDays"],
            "closedPnlDifference": delta["closedPnlAmount"], "openPnlDifference": delta["openUnrealizedPnlAmount"]})
    require(identities == {(p, b, x) for p in PERIODS for b in BOARDS for x in ("primary", "secondary")}, "All twelve comparisons required")
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
    return contrasts, accounts, formation, coverage, intervals


def payloads(data, method):
    contrasts, accounts, formation, coverage, intervals = tables(data)
    artifact = data["opportunityRowsArtifact"]
    src = {"label": "成交均价承接的完整账户对照（2022—2025）",
        "files": [{"label": x} for x in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json",
            "research_volume_anchor.py", "market-phase-atlas-v1/atlas.json")]
            + [{"label": f"opportunity-dispositions.jsonl.gz：{artifact['count']}条；SHA256 {artifact['sha256']}"}],
        "metricDefinitions": [{"label": "主比较", "definition": "六个板块与时期组合各自比较三日承接减等待三日的累计净收益百分点差，同时保留暴露变化和估值质量。"},
            {"label": "入场承接", "definition": "放量突破日形成锚点，随后三天收盘均不低于从锚点开始的累计成交均价才安排承接政策信号。"},
            {"label": "资金与交易", "definition": "累计净收益包括费用、滑点及期末持仓；平仓分布与未实现盈亏分开，未安排和安排但未成交分开。"},
            {"label": "区间归因", "definition": "每个区间从之前最后收盘净值起算，继承跨期仓位；金额可相加，收益率复利连接。"}],
        "filters": ["两个历史时期、三个固定板块群体、三个独立入场政策，共18账户全量展示",
            "形成日收盘突破前20日最高价且成交量超过前20日最高量", "形成日按前20日平均成交额排序取Top5，之后不回填",
            "三日承接和等待三日均要求完整三日证据", "2024年9月24日只作连续资金归因分界"],
        "caveats": LIMITS,
        "evidenceFlow": [{"kind": "validation", "title": "账户与汇总核验", "detail": "独立复核18个账户、270个完整子区间、12个有方向差值，以及每个原始形成机会的三种政策处置。"}]}
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
        query("policy-coverage", coverage, POLICY_ID + COVERAGE_FIELDS)]
    queries.extend(query(key, rows, POLICY_ID + INTERVAL_FIELDS) for key, rows in zip(("years", "half-years", "phases", "event-epochs"), intervals))
    sources = {"schemaVersion": 1, "items": [{"id": "acceptance-versus-waiting", "title": "三日承接相对单纯等待：收益、暴露与质量", "queries": queries}]}
    primary = [r for r in contrasts if r["contrast"] == "primary"]
    chart = {"schemaVersion": 1, "id": "volume-anchor-acceptance-difference", "queryId": "acceptance-primary-contrasts",
        "title": "三日承接减单纯等待：累计净收益差", "height": 360,
        "chart": {"type": "horizontalBar", "x": "boardLabel", "y": VALUE, "series": "period", "stackable": False,
            "startAtZero": True, "sortOrder": "original", "valueDecimals": 2, "xLabel": VALUE,
            "showXAxisLabel": True, "showYAxisLabel": False,
            "showValues": False, "categoryOrder": list(BOARD_LABELS.values()), "seriesOrder": list(PERIODS),
            "colors": {p: f"var(--chart-{i+1})" for i, p in enumerate(PERIODS)}},
        "rows": [{"period": r["period"], "boardLabel": r["boardLabel"], VALUE: r["returnDifferencePp"],
            "平均仓位差（百分点）": r["exposureDifferencePp"], "承接估值不可靠日": r["candidateValuationDays"],
            "等待估值不可靠日": r["controlValuationDays"]} for r in primary],
        "columns": ["period", "boardLabel", VALUE, "平均仓位差（百分点）", "承接估值不可靠日", "等待估值不可靠日"],
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
    for k, n in (("newAccounts", 18), ("newSetupMechanisms", 1), ("newEntryPolicies", 3), ("newUniverseVariants", 0), ("referenceAccountsReused", 0)):
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
    for kind, name in (("chart", "volume-anchor-acceptance-difference.html"), ("sources", "volume-anchor-acceptance-sources.html")):
        renderer, target = TOOLS / f"render-inline-{kind}.mjs", directory / "inline" / name
        run = subprocess.run([NODE, str(renderer), "--input", str(directory / (kind + ".json")), "--output", str(target)], text=True, capture_output=True, timeout=60)
        write(directory / f"{kind}-render-log.json", {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr})
        require(run.returncode == 0, run.stdout + run.stderr)
        require(target.stat().st_size < 1_000_000, "Oversized fragment")
        fragments[kind] = {"path": target.relative_to(directory).as_posix(), "sha256": sha(target), "rendererSha256": sha(renderer)}
    require(all(sha(directory / n) == v for n, v in pins.items()), "Input modified while rendering")
    write(directory / "render-receipt.json", {"schema": "volume-anchor-acceptance-render-v1", "inputPins": pins,
        "builderSha256": sha(__file__), "chartPayloadSha256": sha(directory / "chart.json"), "payloadSha256": sha(directory / "sources.json"),
        "chartSha256": fragments["chart"]["sha256"], "sourceSha256": fragments["sources"]["sha256"], "fragments": fragments,
        "cards": 1, "tableRows": TABLE_ROWS, "chartRows": 6, "opportunityRowsArtifact": artifact,
        "dataPeriod": "2022-2025", "includes2026": False, "historicalSampleAlreadyObserved": True,
        "holdout": False, "causalClaim": False, "boardIsIndustry": False, "browserChecked": False,
        "actualMarketRuns": 0, "analysisArtifactsModified": False})
    return {"fragments": fragments, "tableRows": TABLE_ROWS, "renderReceiptSha256": sha(directory / "render-receipt.json")}


def self_test():
    data = {k: [] for k in ("accounts", "coverageRows", "formationCoverageRows", "comparisons", "yearRows", "halfYearRows", "phaseRows", "curveEpochRows")}
    data["opportunityRowsArtifact"] = {"count": 48, "sha256": "a" * 64}
    for p in PERIODS:
        for b in BOARDS:
            data["formationCoverageRows"].append({"period": p, "board": b, "sourceCohort": {"includedCount": 8, "excludedCount": 1, "unknownCount": 1},
                "formationTop5": 8, "counts": {"setupCount": 8}, "uniqueSymbols": 3, "formationEvidenceTotals": {"anchorUnknownCount": 2},
                "acceptanceStateCounts": {"passed": 2, "failed": 3, "unknown": 2, "censored": 1},
                "knownFailedStepsInsideCensored": 1, "unknownStepsInsideCensored": 1})
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
            for kind, c, k in (("primary", "accept3", "delay3"), ("secondary", "delay3", "immediate")):
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
    for kind in ("duplicateAccount", "missingPhase", "sidecarCount", "countPartition", "flippedContrast"):
        changed = deepcopy(data)
        if kind == "duplicateAccount": changed["accounts"][-1] = deepcopy(changed["accounts"][0])
        elif kind == "missingPhase": changed["phaseRows"].pop()
        elif kind == "sidecarCount": changed["opportunityRowsArtifact"]["count"] = 1
        elif kind == "countPartition": changed["coverageRows"][0]["entered"] = 8
        else: changed["comparisons"][0]["candidate"] = "immediate"
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
    return {"passed": True, "checks": ["six signed primary differences", "18 unique accounts", "nine complete tables", "null preservation",
        "no input mutation", "exposure and valuation explicit", "all invalid cases rejected", "shared payload validators"], "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False))
