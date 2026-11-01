"""Presentation only: audited label-support coverage, never strategy returns."""
from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/independent-shadow-labels-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
SAMPLES = {"oldActual": "原账户标签", "originalEntered": "独立原入场", "allExecutable": "独立可成交"}
EPOCHS = {"before20240924": "9·24前", "from20240924": "9·24起"}
SLUGS = {"before20240924": "before", "from20240924": "after"}
ORDER = ["beforeChart", "beforeSources", "afterChart", "afterSources"]
TABLE_ROWS = [[18, 18, 18, 36, 72, 12, 12]] * 2
VISIBLE_MAPPING = "主固定表示主板的固定10%跟踪，主波动表示主板的波动幅度跟踪；创固定表示创业板的固定10%跟踪，创波动表示创业板的波动幅度跟踪；科固定表示科创板的固定10%跟踪，科波动表示科创板的波动幅度跟踪。"
VALUE = "可拟合日占比（%）"
ID_FIELDS = [("period", "时期"), ("boardLabel", "板块"), ("exitLabel", "退出规则")]
SAMPLE_FIELDS = ID_FIELDS + [("sampleLabel", "标签来源")]
LIMITS = [
    "可拟合日占比以该段全部目标交易日为分母，不以出现信号的日期为分母，也不是模型胜率或策略收益。零值保留，缺失值不替换为零。",
    "可拟合不等于已经适应新行情：9月24日之后的训练窗口仍可能主要包含此前形成的机会；只有后续新形成机会完成平仓，其结果才会成为可用标签。",
    "分段按信号形成日T：2024-01-02至2024-09-23，以及2024-09-24至2025-12-31。当前环境只取A=T−1，9月24日的环境信息截至9月23日。两个区间长短不同。",
    "可拟合要求最近126个源交易日内已完全闭仓、形成日三特征完整的标签覆盖至少20个不同形成日，且当前三特征完整。未达到条件仍是回退放行，不代表没有标签或没有信号。",
    "原账户标签来自既有受资金和持仓限制的影子账户；独立原入场与独立可成交复用同一逐机会路径库，前者仅保留原账户曾买入的机会。两组重新计算窗口内同形成日权重，不能视作简单追加等权交易。",
    "独立路径使用原始完整历史前缀、原交易与退出规则，每条初始50万元、名义入场预算10万元。路径可重叠，现金不共享；闭仓净损益不能相加为可投资账户收益，也不能把提前结束的路径称为整期账户。",
    "原账户与独立原入场之间还可能改变投入、费用、数量、成交容量及最终退出日期，桥接差异不能单独归因为资金约束；未成交不是零收益标签。",
    "未平、部分卖出及尚未结清的路径保持未成熟，只有最终平仓并完成结算才形成标签。当前无声明权益源限定下标签可用日等于最终退出日；不把未平浮盈提前用于训练。",
    "支持、未成熟量和权重集中度是每日快照；同一持仓会重复出现，不能累计为独立样本数。形成日、股票和路径数量分别保留。",
    "两套退出仍为5%初始收盘止损加固定10%跟踪，或此前20项归一化真实波幅均值3倍的只上移保护水平。此处没有重跑候选投资账户、没有新信号或收益门槛搜索。",
    "2022—2025是反复使用的开发样本，2026此前已暴露但本轮未使用，没有未触碰留出样本。原行情、估值、停牌和公司行为证据限制继续适用；标签数量增加不能证明小亏大赚或稳定盈利。",
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def finite(x): return type(x) in (int, float) and math.isfinite(x)
def read(path): return json.loads(Path(path).read_text("utf-8"))
def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def key(row): return tuple(row[k] for k in ("period", "board", "exit"))
def labels(row):
    return {"period": row["period"], "boardLabel": BOARDS[row["board"]], "exitLabel": EXITS[row["exit"]],
            **({"sampleLabel": SAMPLES[row["sample"]]} if "sample" in row else {})}


def category(board, exit_policy):
    return {"main": "主", "chinext": "创", "star": "科"}[board] + ("固定" if exit_policy == "trail10" else "波动")


def columns(fields): return [{"field": k, "label": label} for k, label in fields]


def verify_regime_gate(regime, audit, pins, verifier_sha):
    require(audit["passed"] is True and audit["posthoc"] is True and regime["posthoc"] is True,
            "Passing independent descriptive-maturity audit required")
    bindings = {"planSha256": "plan.json", "contextSha256": "regime-maturity-context.json",
        "readoutSha256": "readout.json", "readoutValidationSha256": "readout-validation.json",
        "pathValidationSha256": "validation.json", "libraryCompletionSha256": "library-completion.json"}
    for field, name in bindings.items():
        require(audit[field] == pins[name], "Independent maturity audit binding differs: " + field)
        if field != "contextSha256":
            require(regime[field] == pins[name], "Maturity context binding differs: " + field)
    require(audit["dailyRowsArtifactSha256"] == regime["dailyRowsArtifact"]["sha256"]
            and audit["verifierSha256"] == verifier_sha, "Maturity daily artifact/verifier differs")


def payloads(data, provenance, regime=None):
    require(data["signalsGenerated"] is False and data["candidateAccountExecutions"] == 0
            and data["certified"] is False and data["targetAchieved"] is False, "Diagnostic scope changed")
    groups = {key(r): r for r in data["groupReadout"]}
    expected = {(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS}
    require(len(groups) == len(data["groupReadout"]) == 12 and set(groups) == expected, "All twelve original groups required")
    coverage = data["oldActualCoverageRows"] + data["coverageRows"]
    index = {(key(r), r["sample"], r["kind"], r["id"]): r for r in coverage}
    require(len(index) == len(coverage) == 144, "Full coverage matrix required")
    for k in expected:
        for sample in SAMPLES:
            spans = [("overall", "overall")] + [("calendarYear", y) for y in k[0].split("-")]
            if k[0] == "2024-2025": spans += [("descriptiveEpoch", e) for e in EPOCHS]
            for kind, ident in spans:
                require((k, sample, kind, ident) in index, "Missing coverage identity")
    for r in coverage:
        n, fitted = r["dayCount"], r["fittedDays"]
        require(type(n) is int and n > 0 and type(fitted) is int and 0 <= fitted <= n
                and r["fallbackDays"] == n - fitted, "Fit/fallback denominator differs")
        require(finite(r["fittedDayPct"]) and math.isclose(r["fittedDayPct"], fitted / n * 100,
                rel_tol=1e-12, abs_tol=1e-12), "Fit share differs from all target days")
        for sample in SAMPLES:
            other = index[(key(r), sample, r["kind"], r["id"])]
            require((n, r["startDate"], r["endDate"]) == (other["dayCount"], other["startDate"], other["endDate"]),
                    "Label sources use different observation dates")
    for r in groups.values():
        require(sum(r["pathStatusCounts"].values()) == r["originalOpportunities"], "All original paths must remain")
        p, b = r["populations"], r["bridge"]
        require(p["allExecutable"]["episodes"] >= p["originalEntered"]["episodes"]
                and b["oldActualEntryCount"] == b["matchedIndependentEntries"] + b["missingIndependentEntries"], "Membership bridge does not conserve")
    def projected(rows):
        return [{**labels(r), **{k: r[k] for k in ("id", "dayCount", "fittedDays", "fallbackDays", "fittedDayPct",
            "firstFittedDate", "originalOpportunities", "fittedOriginalOpportunities", "fittedOriginalOpportunityPct")}} for r in rows]
    ordered = sorted(coverage, key=lambda r: (PERIODS.index(r["period"]), list(BOARDS).index(r["board"]),
        list(EXITS).index(r["exit"]), list(SAMPLES).index(r["sample"]), r["startDate"], r["kind"]))
    overall = projected([r for r in ordered if r["kind"] == "overall"])
    years = projected([r for r in ordered if r["kind"] == "calendarYear"])
    path_rows, bridge_rows = [], []
    for p in PERIODS:
        for b in BOARDS:
            for e in EXITS:
                r = groups[(p, b, e)]; bridge = r["bridge"]
                path_rows.append({**labels(r), "originalOpportunities": r["originalOpportunities"],
                    **{name: r["pathStatusCounts"].get(name, 0) for name in ("closed", "open", "unfilled", "unresolved")}})
                bridge_rows.append({**labels(r), **{k: bridge[k] for k in ("oldActualEntryCount", "matchedIndependentEntries", "missingIndependentEntries", "bothClosed", "differentFinalExitDate")},
                    "meanNetReturnDeltaPct": bridge["netReturnDeltaPct"]["mean"]})
    regime_index = None
    if regime is not None:
        require(regime["posthoc"] is True and regime["verification"]["passed"] is True
                and regime["newSignals"] == regime["newCandidateAccounts"] == regime["parameterChanges"] == 0,
                "Descriptive era composition cannot alter models or accounts")
        regime_index = {(key(r), r["sample"], r["id"]): r for r in regime["epochRows"]}
        require(len(regime_index) == len(regime["epochRows"]) == 36, "Complete era composition required")
        for b in BOARDS:
            for e in EXITS:
                for sample in SAMPLES:
                    for epoch in EPOCHS:
                        r = regime_index[(("2024-2025", b, e), sample, epoch)]
                        same = index[(key(r), sample, "descriptiveEpoch", epoch)]
                        require(all(r[k] == same[k] for k in ("dayCount", "fittedDays", "fallbackDays")), "Era support differs from main coverage")
                        require(r["fittedDays"] == r["fittedDaysWithPostRegimeMaturedLabels"] + r["fittedDaysWithoutPostRegimeMaturedLabels"], "Post-era fitted partition differs")
                        for value in (r["postRegimeWeightShareOnFittedDays"]["median"], r["postRegimeWeightShareOnFittedDays"]["mean"]):
                            require(value is None or finite(value) and 0 <= value <= 1, "Post-era weight must retain null or a proportion")
    outputs = {}
    for epoch, label in EPOCHS.items():
        rows = [index[(("2024-2025", b, e), sample, "descriptiveEpoch", epoch)] for b in BOARDS for e in EXITS for sample in SAMPLES]
        chart_rows = [{"板块与退出": category(r["board"], r["exit"]), "标签来源": SAMPLES[r["sample"]], VALUE: r["fittedDayPct"],
            "完整板块": BOARDS[r["board"]], "完整退出规则": EXITS[r["exit"]],
            "目标交易日": r["dayCount"], "可拟合日": r["fittedDays"], "首次可拟合日": r["firstFittedDate"]} for r in rows]
        title = label + "：三种标签来源的可拟合日占比"
        source = {"label": "独立机会标签覆盖诊断", "files": provenance["files"],
            "metricDefinitions": [{"label": "可拟合日占比", "definition": "具备至少20个有效形成日且当前特征完整的日期，占该段全部目标交易日的比例。"},
                {"label": "图中六类名称", "definition": VISIBLE_MAPPING}],
            "caveats": LIMITS, "filters": ["同日比较三种标签来源", "固定原信号和退出规则", "分段使用形成日T；特征截至T−1"],
            "evidenceFlow": provenance["evidenceFlow"]}
        chart = {"schemaVersion": 1, "id": "independent-shadow-" + SLUGS[epoch], "title": title,
            "height": 540, "theme": "codex-classic", "rows": chart_rows, "columns": list(chart_rows[0]),
            "chart": {"type": "horizontalBar", "x": "板块与退出", "y": VALUE, "series": "标签来源", "startAtZero": True,
                "stackable": False, "showLegend": True, "showValues": False, "showXAxisLabel": True, "showYAxisLabel": False,
                "xLabel": VALUE, "yLabel": "板块与退出", "seriesOrder": list(SAMPLES.values()),
                "categoryOrder": [category(b, e) for b in BOARDS for e in EXITS]}, "source": source}
        supports, immature = [], []
        for r in rows:
            supports.append({**labels(r), "eligibleFormationDaysMedian": r["support"]["eligibleFormationDayCount"]["median"],
                "eligibleEpisodesMedian": r["support"]["eligibleEpisodeCount"]["median"],
                "eligibleSymbolsMedian": r["eligibleSymbols"]["median"],
                "labelXUnknownMedian": r["support"]["labelXUnknownCount"]["median"]})
            immature.append({**labels(r), "immatureMedian": r["support"]["immatureCount"]["median"],
                "immatureAgeMedian": r["dailyMeanImmatureAgeSlots"]["median"], "maximumImmatureAgeSlots": r["maximumImmatureAgeSlots"],
                "symbolWeightMaximumPct": None if r["maximumSymbolWeightShare"]["maximum"] is None else r["maximumSymbolWeightShare"]["maximum"] * 100})
        coverage_fields = SAMPLE_FIELDS + [("dayCount", "全部目标日"), ("fittedDays", "可拟合日"), ("fittedDayPct", "可拟合日占比（%）"), ("fallbackDays", "回退日")]
        tables = [
            ("epoch-fit", "本段同日覆盖", projected(rows), coverage_fields),
            ("training-support", "本段训练支持", supports, SAMPLE_FIELDS + [("eligibleFormationDaysMedian", "有效形成日中位数"), ("eligibleEpisodesMedian", "有效标签中位数"), ("eligibleSymbolsMedian", "股票数中位数"), ("labelXUnknownMedian", "特征缺失标签中位数")]),
            ("immature-support", "本段未成熟与集中度", immature, SAMPLE_FIELDS + [("immatureMedian", "未成熟路径中位数"), ("immatureAgeMedian", "日均未成熟龄中位槽"), ("maximumImmatureAgeSlots", "最长未成熟槽"), ("symbolWeightMaximumPct", "最高单股权重（%）")]),
            ("overall-fit", "全12组完整时期覆盖", overall, coverage_fields),
            ("year-fit", "全部年度覆盖", years, SAMPLE_FIELDS + [("id", "年度"), ("dayCount", "全部目标日"), ("fittedDays", "可拟合日"), ("fittedDayPct", "可拟合日占比（%）")]),
            ("path-denominators", "全12组原机会分母", path_rows, ID_FIELDS + [("originalOpportunities", "原机会"), ("closed", "成熟闭仓"), ("open", "未平"), ("unfilled", "未成交"), ("unresolved", "未结清")]),
            ("actual-bridge", "全12组旧实际入场桥", bridge_rows, ID_FIELDS + [("oldActualEntryCount", "旧实际入场"), ("matchedIndependentEntries", "独立亦成交"), ("missingIndependentEntries", "独立未成交"), ("bothClosed", "两侧已闭仓"), ("differentFinalExitDate", "最终退出异日"), ("meanNetReturnDeltaPct", "两侧闭仓均差（百分点）")]),
        ]
        require([len(r) for _, _, r, _ in tables] == TABLE_ROWS[0], "Sources table matrix changed")
        queries = [{"id": name, "source": {**source, "label": table_label}, "rows": data_rows, "columns": columns(fields),
            "reportingPeriod": "2022—2025；本段图按2024年9月24日形成日分界",
            "preview": {"kind": "aggregate", "note": "完整已审汇总行；日级证据及全部路径保留在来源文件。", "totalRows": len(data_rows)}}
            for name, table_label, data_rows, fields in tables]
        outputs[SLUGS[epoch] + "Chart"] = chart
        outputs[SLUGS[epoch] + "Sources"] = {"schemaVersion": 1, "items": [{"id": "independent-shadow-" + SLUGS[epoch] + "-coverage", "title": title, "queries": queries}]}
        if regime_index is not None:
            era_rows = []
            for row in rows:
                r = regime_index[(key(row), row["sample"], epoch)]
                share = r["postRegimeWeightShareOnFittedDays"]
                era_rows.append({**labels(r), "fittedDays": r["fittedDays"],
                    "fittedDaysWithoutPostRegimeMaturedLabels": r["fittedDaysWithoutPostRegimeMaturedLabels"],
                    "firstPostRegimeMaturedLabelDate": r["firstPostRegimeMaturedLabelDate"],
                    "postWeightMedianPct": None if share["median"] is None else share["median"] * 100,
                    "postWeightMeanPct": None if share["mean"] is None else share["mean"] * 100})
            extra = {"id": "post-era-maturity-" + SLUGS[epoch], "title": "可拟合时，是否已看到9·24后新形成机会的成熟结果", "queries": [{
                "id": "post-era-weight", "rows": era_rows,
                "columns": columns(SAMPLE_FIELDS[1:] + [("fittedDays", "可拟合日"),
                    ("fittedDaysWithoutPostRegimeMaturedLabels", "可拟合但无后期标签日"),
                    ("firstPostRegimeMaturedLabelDate", "首次看到后期标签T"),
                    ("postWeightMedianPct", "可拟合日后期权重中位（%）"), ("postWeightMeanPct", "可拟合日后期权重均值（%）")]),
                "source": {"label": "形成时期与标签成熟度描述", "files": provenance.get("regimeFiles", []),
                    "metricDefinitions": [{"label": "后期标签权重", "definition": "已成熟、有效形成日中，形成日不早于2024年9月24日的占比；每个形成日总权重为一。表中只对可拟合日期取中位数与均值。"}],
                    "caveats": ["这是主研究之后追加的描述分段，没有更改模型、准入或退出。",
                        "首次看到后期标签是决策日T，标签实际可用信息截至A=T−1；它不是后期信号首次形成日或退出日。",
                        "无可拟合日时权重统计为空；有可拟合日但后期标签尚未成熟时权重可以为零，二者不同。",
                        "看到后期成熟结果不等于已经适应新行情，更不证明盈利；日快照重复使用相关标签。"],
                    "evidenceFlow": [{"kind": "validation", "title": "已记录一致性检查", "body": "附表逐日核对原冻结决策、训练标签摘要和形成日等权，分段支持数量与主读数一致。"}]},
                "reportingPeriod": "2024—2025；按形成日T分为9月24日前与当日起",
                "preview": {"kind": "aggregate", "note": "该段六组、三种标签来源全18行。", "totalRows": 18}}]}
            outputs[SLUGS[epoch] + "Sources"]["items"].append(extra)
    require(list(outputs) == ORDER, "Chart/receipt order changed")
    return outputs


def build(directory=OUT):
    directory = Path(directory).resolve()
    require(not (directory / "render-receipt.json").exists(), "Never overwrite presentation evidence")
    names = ("plan.json", "library-completion.json", "validation.json", "readout.json", "readout-validation.json")
    pins = {name: sha(directory / name) for name in names}
    plan, library, proof, data, audit = [read(directory / name) for name in names]
    require(proof["passed"] is True and proof["planSha256"] == pins["plan.json"]
            and proof["libraryCompletionSha256"] == pins["library-completion.json"], "Exact path audit required")
    require(data["planSha256"] == pins["plan.json"] and data["pathValidationSha256"] == pins["validation.json"]
            and data["libraryCompletionSha256"] == pins["library-completion.json"]
            and audit["passed"] is True and audit["planSha256"] == pins["plan.json"]
            and audit["readoutSha256"] == pins["readout.json"], "Exact readout audit required")
    require(audit["pathValidationSha256"] == pins["validation.json"]
            and audit["libraryCompletionSha256"] == pins["library-completion.json"], "Readout audit parent path binding differs")
    require(audit["dailyComparisonRowsArtifactSha256"] == data["dailyComparisonRowsArtifact"]["sha256"]
            and audit["oldActualBridgeRowsArtifactSha256"] == data["oldActualBridgeRowsArtifact"]["sha256"], "Audited sidecar bindings differ")
    refs = [*data["inputRefs"], data["dailyComparisonRowsArtifact"], data["oldActualBridgeRowsArtifact"],
            *[ref for g in data["decisionArtifacts"] for ref in g["samples"].values()]]
    for ref in refs: require(sha(ROOT / ref["path"]) == ref["sha256"], "Changed supporting input: " + ref["path"])
    files = [str((directory / name).relative_to(ROOT)).replace("\\", "/") for name in names]
    files += [data[name]["path"] for name in ("dailyComparisonRowsArtifact", "oldActualBridgeRowsArtifact")]
    for name in ("regime-maturity-context.json", "regime-maturity-validation.json", "presentation-source-pins-v4.json"):
        pins[name] = sha(directory / name)
    regime = read(directory / "regime-maturity-context.json")
    regime_audit = read(directory / "regime-maturity-validation.json")
    verify_regime_gate(regime, regime_audit, pins, sha(ROOT / "scripts/verify-independent-shadow-labels-readout.py"))
    for ref in [*regime["inputRefs"], regime["dailyRowsArtifact"]]:
        require(sha(ROOT / ref["path"]) == ref["sha256"], "Maturity supporting input changed")
    for name, expected in regime_audit["sourcePins"].items():
        require(sha(ROOT / name) == expected, "Maturity independent source changed")
    freeze = read(directory / "presentation-source-pins-v4.json")
    require(freeze["passed"] is True and freeze["planSha256"] == pins["plan.json"]
            and freeze["sourcePins"][Path(__file__).relative_to(ROOT).as_posix()] == sha(__file__), "Presentation v4 freeze differs")
    for name, expected in freeze["sourcePins"].items():
        require(sha(ROOT / name) == expected, "Presentation v4 source changed")
    regime_files = [(directory / name).relative_to(ROOT).as_posix() for name in
        ("regime-maturity-context.json", "regime-maturity-validation.json")]
    regime_files.append(regime["dailyRowsArtifact"]["path"])
    values = payloads(data, {"files": files, "regimeFiles": regime_files, "evidenceFlow": [
        {"kind": "calculation", "title": "覆盖口径", "body": data["basis"]["clocks"]},
        {"kind": "validation", "title": "已记录核验", "body": "独立路径与读数两层审计通过，并精确绑定此读数。展示只投影已审汇总，不运行账户或重算模型。"}]}, regime)
    target = directory / "inline"; target.mkdir(exist_ok=True)
    fragments, payload_refs = {}, {}
    helpers = [TOOLS / "render-inline-chart.mjs", TOOLS / "render-inline-sources.mjs", WRAPPER]
    helper_refs = [{"path": str(p), "sha256": sha(p)} for p in helpers]
    for name in ORDER:
        slug = "before" if name.startswith("before") else "after"
        kind = "chart" if name.endswith("Chart") else "sources"
        source_path = directory / (slug + "." + kind + ".json")
        fragment = target / ("independent-shadow-" + slug + "-" + kind + ".html")
        require(not source_path.exists() and not fragment.exists(), "Presentation output already exists")
        write(source_path, values[name])
        subprocess.run([NODE, str(TOOLS / ("render-inline-" + kind + ".mjs")), "--input", str(source_path), "--output", str(fragment)], check=True)
        require(fragment.stat().st_size < 1_000_000, "Oversized fragment")
        payload_refs[name] = {"path": source_path.relative_to(directory).as_posix(), "sha256": sha(source_path)}
        fragments[name] = {"path": fragment.relative_to(directory).as_posix(), "sha256": sha(fragment)}
    require(all(sha(directory / n) == s for n, s in pins.items()), "Audited input changed during render")
    receipt = {"schema": "independent-shadow-labels-render-v1", "builderSha256": sha(__file__), "inputPins": pins,
        "presentationHelpers": helper_refs, "deliveryOrder": ORDER, "fragments": fragments, "payloads": payload_refs,
        "chartRows": 36, "tableRows": [[len(q["rows"]) for i in values[k]["items"] for q in i["queries"]] for k in ("beforeSources", "afterSources")],
        "posthocMaturityIncluded": regime is not None, "newAccountExecutions": 0, "analysisArtifactsModified": False}
    write(directory / "render-receipt.json", receipt)
    return {"passed": True, "receipt": str(directory / "render-receipt.json"), "receiptSha256": sha(directory / "render-receipt.json")}


def synthetic():
    dist = lambda x: {"count": 5, "minimum": x, "mean": x, "median": x, "maximum": x}
    groups, cov, old = [], [], []
    for p in PERIODS:
        for b in BOARDS:
            for e in EXITS:
                identity = dict(period=p, board=b, exit=e)
                groups.append({**identity, "originalOpportunities": 5, "pathStatusCounts": {"closed": 2, "open": 1, "unfilled": 2},
                    "populations": {"allExecutable": {"episodes": 3}, "originalEntered": {"episodes": 2}},
                    "bridge": {"oldActualEntryCount": 3, "matchedIndependentEntries": 2, "missingIndependentEntries": 1,
                        "bothClosed": 1, "differentFinalExitDate": 1, "netReturnDeltaPct": dist(-2)}})
                for sample in SAMPLES:
                    spans = [("overall", "overall")] + [("calendarYear", y) for y in p.split("-")]
                    if p == "2024-2025": spans += [("descriptiveEpoch", k) for k in EPOCHS]
                    for kind, ident in spans:
                        fitted = {"oldActual": 0, "originalEntered": 2, "allExecutable": 5}[sample]
                        row = {**identity, "sample": sample, "kind": kind, "id": ident, "startDate": p[:4] + "-01-02", "endDate": p[-4:] + "-12-31",
                            "dayCount": 5, "fittedDays": fitted, "fallbackDays": 5-fitted, "fittedDayPct": fitted/5*100,
                            "firstFittedDate": None if not fitted else p[:4] + "-01-03", "originalOpportunities": 5,
                            "fittedOriginalOpportunities": fitted, "fittedOriginalOpportunityPct": fitted/5*100,
                            "support": {k: dist(2) for k in ("eligibleFormationDayCount", "eligibleEpisodeCount", "labelXUnknownCount", "immatureCount")},
                            "eligibleSymbols": dist(2), "dailyMeanImmatureAgeSlots": dist(3), "maximumImmatureAgeSlots": 7,
                            "maximumSymbolWeightShare": dist(.5)}
                        (old if sample == "oldActual" else cov).append(row)
    return {"groupReadout": groups, "coverageRows": cov, "oldActualCoverageRows": old,
            "signalsGenerated": False, "candidateAccountExecutions": 0, "certified": False, "targetAchieved": False}


def self_test():
    data = synthetic(); before = deepcopy(data)
    values = payloads(data, {"files": ["synthetic.json"], "evidenceFlow": []})
    checks = [data == before, list(values) == ORDER,
        all(len(values[k]["rows"]) == 18 for k in ORDER if k.endswith("Chart")),
        all([len(q["rows"]) for q in values[k]["items"][0]["queries"]] == TABLE_ROWS[0] for k in ORDER if k.endswith("Sources")),
        values["beforeChart"]["rows"][0][VALUE] == 0,
        values["beforeChart"]["rows"][2][VALUE] == 100,
        values["beforeChart"]["chart"]["categoryOrder"] == values["afterChart"]["chart"]["categoryOrder"],
        values["beforeChart"]["chart"]["categoryOrder"] == ["主固定", "主波动", "创固定", "创波动", "科固定", "科波动"],
        values["beforeChart"]["rows"][0]["完整板块"] == "主板" and values["beforeChart"]["rows"][0]["完整退出规则"] == "固定10%跟踪",
        all("label" in d and "definition" in d for d in values["beforeSources"]["items"][0]["queries"][0]["source"]["metricDefinitions"]),
        values["beforeSources"]["items"][0]["queries"][0]["source"]["metricDefinitions"][1]["definition"] == VISIBLE_MAPPING]
    for mutate in (lambda d: d["coverageRows"].pop(), lambda d: d["coverageRows"][0].update(fittedDayPct=None),
                   lambda d: d["coverageRows"][0].update(dayCount=6), lambda d: d.update(candidateAccountExecutions=1)):
        broken = deepcopy(data); mutate(broken)
        try: payloads(broken, {"files": [], "evidenceFlow": []})
        except (ValueError, KeyError): checks.append(True)
        else: checks.append(False)
    era = {"posthoc": True, "verification": {"passed": True}, "newSignals": 0, "newCandidateAccounts": 0, "parameterChanges": 0,
        "epochRows": [{**r, "fittedDaysWithPostRegimeMaturedLabels": 0, "fittedDaysWithoutPostRegimeMaturedLabels": r["fittedDays"],
            "firstPostRegimeMaturedLabelDate": None, "postRegimeWeightShareOnFittedDays": {"median": 0 if r["fittedDays"] else None, "mean": 0 if r["fittedDays"] else None}}
            for r in data["coverageRows"] + data["oldActualCoverageRows"] if r["kind"] == "descriptiveEpoch"]}
    extended = payloads(data, {"files": [], "evidenceFlow": []}, era)
    checks.append(all([len(q["rows"]) for i in extended[k]["items"] for q in i["queries"]] == TABLE_ROWS[0] + [18] for k in ("beforeSources", "afterSources")))
    checks.append(extended["beforeChart"] == payloads(data, {"files": [], "evidenceFlow": []})["beforeChart"])
    checks.append(extended["beforeSources"]["items"][1]["queries"][0]["rows"][0]["postWeightMedianPct"] is None)
    pins = {n: str(i) for i, n in enumerate(("plan.json", "regime-maturity-context.json", "readout.json", "readout-validation.json", "validation.json", "library-completion.json"))}
    gate_audit = {"passed": True, "posthoc": True, "verifierSha256": "verifier", "dailyRowsArtifactSha256": "daily",
        **{f: pins[n] for f, n in (("planSha256", "plan.json"), ("contextSha256", "regime-maturity-context.json"),
            ("readoutSha256", "readout.json"), ("readoutValidationSha256", "readout-validation.json"),
            ("pathValidationSha256", "validation.json"), ("libraryCompletionSha256", "library-completion.json"))}}
    gate_context = {**gate_audit, "dailyRowsArtifact": {"sha256": "daily"}}
    verify_regime_gate(gate_context, gate_audit, pins, "verifier"); checks.append(True)
    for change in ({"passed": False}, {"contextSha256": "stale"}, {"dailyRowsArtifactSha256": "changed"}, {"verifierSha256": "changed"}):
        try: verify_regime_gate(gate_context, {**gate_audit, **change}, pins, "verifier")
        except ValueError: checks.append(True)
        else: checks.append(False)
    require(all(checks), "Synthetic presentation checks failed")
    return {"passed": True, "syntheticChecks": len(checks), "filesWritten": 0, "realSourceReads": 0, "accountExecutions": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--directory", type=Path, default=OUT)
    parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False), flush=True)
