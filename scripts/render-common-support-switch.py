"""Render audited population contrasts with native chart and Sources helpers."""
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/common-support-switch-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
MODELS = {"intercept": "近期均值", "ridge": "环境模型"}
POPULATIONS = {"originalEntered": "原入场样本", "allExecutable": "全部可成交", "commonSupport": "共同可拟合才切换"}
ORDER = ["oldChart", "oldSources", "newChart", "newSources"]
VALUE = "账户收益差（百分点）"
MAPPING = "主固定表示主板的固定10%跟踪，主波动表示主板的波动幅度跟踪；创固定表示创业板的固定10%跟踪，创波动表示创业板的波动幅度跟踪；科固定表示科创板的固定10%跟踪，科波动表示科创板的波动幅度跟踪。"
IDS = [("period", "时期"), ("boardLabel", "板块"), ("exitLabel", "退出规则")]
ACCOUNT_IDS = IDS + [("candidateLabel", "策略")]
LIMITS = [
    "图为共同可拟合才切换减去全部可成交反馈的完整账户收益差；正值也可能只是亏损减少。不是单笔、年化收益或逐笔因果效应。图轴范围按时期独立，比较数值而非跨图柱长。",
    "同一模型在原入场样本与全部可成交样本下均可拟合时，采用扩大样本的已有判断；其余日期沿用原入场样本已有判断，原回退保持不变。没有新预测、重新训练或日期买入开关。",
    "每个新账户初始50万元、最多五仓、每次20%预算；沿用手续费、双边各10基点滑点、成交与退出约束。账户收益包含未平浮盈亏，闭仓盈亏另列。",
    "9.24前后延续原账户权益和持仓，时段长度不同。年度、半年和阶段切片均保留，不能把后期所有阶段视作同一市场。",
    "原模型只使用A=T−1可见的126个源交易日已成熟标签，至少20个有效形成日；两侧都有预测的日期与仅一侧有预测的日期分别计数。",
    "24个新账户、60个复用账户和48组新对照全部列出；资金及持仓路径有关联，不能把两组对照的收益差当作单笔可加总的因果贡献。",
    "2022—2025是已反复查看的开发样本；2026不在本轮内。不设平均盈利或盈亏比门槛，本轮不能证明稳定收益，也不能从价格成交量识别主力、散户或量化资金。",
]



def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def candidate_label(candidate):
    if candidate == "baseline":
        return "原信号基准"
    population, model = candidate.split("__")
    return POPULATIONS[population] + "·" + MODELS[model]


def labels(row):
    value = {"period": row["period"], "boardLabel": BOARDS[row["board"]], "exitLabel": EXITS[row["exit"]]}
    if "candidate" in row and row["candidate"] in ("baseline", *(p+"__"+m for p in POPULATIONS for m in MODELS)):
        value["candidateLabel"] = candidate_label(row["candidate"])
    return value


def category(board, exit_name):
    return {"main": "主", "chinext": "创", "star": "科"}[board] + ("固定" if exit_name == "trail10" else "波动")


SWITCH_FIELDS = [
    ("targetDayCount", "目标日"), ("originalOpportunities", "原机会"),
    ("scheduledSignals", "放行机会"), ("notScheduledSignals", "拒绝机会"),
    ("bothFittedDays", "共同可拟合日"), ("bothFittedSignals", "共同可拟合机会"),
    ("originalOnlyFittedDays", "仅原可拟合日"), ("originalOnlyFittedSignals", "仅原可拟合机会"),
    ("allOnlyFittedDays", "仅扩大可拟合日"), ("allOnlyFittedSignals", "仅扩大可拟合机会"),
    ("neitherFittedDays", "两侧回退日"), ("neitherFittedSignals", "两侧回退机会"),
    ("addedSignals", "较原新增放行"), ("removedSignals", "较原减少放行"),
    ("commonVsAllAddedSignals", "较扩大新增放行"),
    ("commonVsAllRemovedSignals", "较扩大减少放行"),
]


def flatten_switch(row):
    return {key: row[key] for key, _ in SWITCH_FIELDS}


def payloads(report, files):
    require(report["counts"]["accounts"] == 84 and report["counts"]["comparisons"] == 48
            and report["newAccounts"] == 24 and report["targetAchieved"] is False, "Complete audited matrix required")
    outputs = {}
    source = {"label": "共同可拟合日期切换对照", "files": files,
        "metricDefinitions": [{"label": VALUE, "definition": "同板块、同退出、同模型，共同可拟合才切换的账户减去全程采用扩大样本判断的账户；正值表示保留原判断后收益更高。"},
            {"label": "板块与退出简称", "definition": MAPPING}], "caveats": LIMITS,
        "evidenceFlow": [{"kind": "calculation", "title": "固定对照", "body": "新候选分别减原入场样本、全部可成交反馈账户，共48组完整对照；图只展示后者24组。"},
            {"kind": "validation", "title": "已记录检查", "body": "24个日程在首个新账户前冻结并独立核对；24个新账户完整审计，60个已有账户证据原样复用；收益读数和切换分母独立核对。"}]}
    for period, slug in zip(PERIODS, ("old", "new")):
        select = lambda name: [r for r in report[name] if r["period"] == period]
        primary = {(r["board"], r["exit"], r["candidate"].split("__")[1]): r for r in [r for r in select("comparisons") if r["control"].startswith("allExecutable__")]}
        require(len(primary) == 12, "All six source/exit pairs and two models required")
        chart_rows = []
        for board in BOARDS:
            for exit_name in EXITS:
                for model in MODELS:
                    row = primary[board, exit_name, model]
                    difference = row["candidateMinusControl"]["accountReturnPct"]
                    require(type(difference) in (int, float) and math.isfinite(difference), "Missing contrast value")
                    require(math.isclose(difference, row["candidateMetrics"]["accountReturnPct"] - row["controlMetrics"]["accountReturnPct"], abs_tol=1e-10), "Return difference mismatch")
                    chart_rows.append({"板块与退出": category(board, exit_name), "模型": MODELS[model], VALUE: difference, "无差异基准": 0,
                        "完整板块": BOARDS[board], "完整退出规则": EXITS[exit_name]})
        title = period.replace("-", "—") + "：保留原判断后的账户收益变化"
        outputs[slug+"Chart"] = {"schemaVersion": 1, "id": "common-switch-"+slug, "height": 500, "theme": "codex-classic",
            "title": title, "rows": chart_rows, "columns": ["板块与退出", "模型", VALUE, "完整板块", "完整退出规则", "无差异基准"],
            "chart": {"type": "horizontalBar",
            "x": "板块与退出", "y": VALUE, "series": "模型", "stackable": False, "startAtZero": True,
            "showLegend": True, "showValues": False, "showXAxisLabel": True, "showYAxisLabel": False,
            "xLabel": VALUE, "yLabel": "板块与退出", "seriesOrder": list(MODELS.values()),
            "annotations": [{"id": "zero-return-difference", "kind": "benchmark", "label": "0：收益相同", "field": "无差异基准", "measure": "环境模型"}],
            "categoryOrder": [category(b,e) for b in BOARDS for e in EXITS]}, "source": source}
        account_rows = [{**labels(r), **r["metrics"]} for r in select("accountRows")]
        contrasts = [{**labels(r), "id": r["id"], "controlLabel": candidate_label(r["control"]),
            "candidateLabel": candidate_label(r["candidate"]), "returnDeltaPp": r["candidateMinusControl"]["accountReturnPct"],
            "drawdownDeltaPp": r["candidateMinusControl"]["maxDrawdownPct"], "exposureDeltaPp": r["candidateMinusControl"]["meanExposurePct"]} for r in select("comparisons")]
        tables = [("accounts", "完整账户", account_rows, ACCOUNT_IDS + [(k,v) for k,v in [
            ("accountReturnPct","净收益（%）"),("maxDrawdownPct","最大回撤（%）"),("meanExposurePct","平均仓位（%）"),
            ("closed","闭仓数"),("closedMeanPct","闭仓均值（%）"),("meanWinPct","平均盈利（%）"),("meanLossPct","平均亏损（%）"),
            ("payoffRatio","盈亏比"),("profitFactor","利润因子"),("maxConsecutiveLosses","最长连亏"),
            ("openPositions","未平仓数"),("openUnrealizedPnlAmount","未平浮盈亏（元）"),("feesPaid","手续费（元）"),
            ("openRealizedPnlAmount","未平仓已实现损益（元）"),("notScheduled","未放行机会"),("notEntered","放行未成交"),
            ("unreliableValuationDays","估值受限日"),("openStaleMarks","期末陈旧估值持仓"),
            ("top5ShareOfGrossProfitPct","前五盈利占比（%）")]]),
            ("contrasts", "全部固定对照", contrasts, IDS+[("id","对照"),("controlLabel","对照账户"),("candidateLabel","候选账户"),
                ("returnDeltaPp","收益差（百分点）"),("drawdownDeltaPp","回撤差（百分点）"),("exposureDeltaPp","仓位差（百分点）")])]
        for field, name in (("yearRows","年度"),("halfYearRows","半年"),("phaseRows","叙事阶段"),("curveEpochRows","9·24前后")):
            rows = [{**labels(r), "id": r["id"], "observedStart": r["observedStart"], "observedEnd": r["observedEnd"],
                "returnPct": r["returnPct"], "maxDrawdownPct": r["maxDrawdownPct"], "meanExposurePct": r["exposurePct"]["mean"],
                "closed": r["closedEpisodes"]["count"], "closedMeanPct": r["closedEpisodes"]["meanReturnPct"]} for r in select(field)]
            if rows:
                tables.append((field.lower(), name+"连续账户", rows, ACCOUNT_IDS+[("id","区间"),("observedStart","起日"),("observedEnd","止日"),
                    ("returnPct","区间收益（%）"),("maxDrawdownPct","区间回撤（%）"),("meanExposurePct","平均仓位（%）"),
                    ("closed","闭仓数"),("closedMeanPct","闭仓均值（%）")]))
        switch_rows = []
        for row in select("switchCoverageRows"):
            switch_rows.append({**labels(row), **flatten_switch(row)})
        tables.append(("switch-support", "切换日期与原机会分母", switch_rows, ACCOUNT_IDS + SWITCH_FIELDS))
        queries = [{"id": name,"source": {**source,"label": label},"rows": rows,
            "columns": [{"field": k,"label": v} for k,v in fields],"reportingPeriod": period,
            "preview": {"kind":"aggregate","note":"本时期全部已审汇总行，展示指定指标；完整分布、逐机会与逐日证据见来源文件。","totalRows":len(rows)}}
            for name,label,rows,fields in tables]
        outputs[slug+"Sources"] = {"schemaVersion":1,"items":[{"id":"common-switch-"+slug,"title":title,"queries":queries}]}
    require(list(outputs) == ORDER, "Chart/Sources sequence differs")
    return outputs


def main():
    names = ("plan.json","comparison.json","validation.json","readout.json","readout-validation.json","presentation-source-pins.json")
    pins = {name:sha(OUT/name) for name in names}
    plan, comparison, validation, report, audit, freeze = [read(OUT/name) for name in names]
    require(validation["passed"] is True and audit["passed"] is True and freeze["passed"] is True, "Independent checks and presentation freeze required")
    for proof in (comparison,validation,report,audit,freeze):
        require(proof["planSha256"] == pins["plan.json"], "Stale plan")
    require(audit["readoutSha256"] == pins["readout.json"] and audit["validationSha256"] == pins["validation.json"]
            and audit["comparisonSha256"] == validation["comparisonSha256"] == pins["comparison.json"], "Readout audit differs")
    require(audit["verifierSha256"] == sha(ROOT/"scripts/verify-common-support-switch-readout.py"), "Readout verifier changed")
    for name,value in freeze["sourcePins"].items():
        require(sha(ROOT/name) == value, "Presentation source changed")
    files = [(OUT/name).relative_to(ROOT).as_posix() for name in names[:5]]
    for key in ("opportunityRowsArtifact", "candidateEntryRowsArtifact",
                "inheritedOpportunityRowsArtifact", "inheritedCandidateEntryRowsArtifact"):
        item = report[key]
        require(sha(ROOT/item["path"]) == item["sha256"], "Readout sidecar changed")
        files.append(item["path"])
    for key in ("parentReadout", "parentComparison"):
        item = plan[key]
        require(sha(ROOT/item["path"]) == item["sha256"], "Inherited evidence changed")
        files.append(item["path"])
    values = payloads(report,files)
    target = OUT/"inline"; target.mkdir(exist_ok=True)
    fragments, payload_refs = {}, {}
    helpers = [{"path":str(path),"sha256":sha(path)} for path in (TOOLS/"render-inline-chart.mjs",TOOLS/"render-inline-sources.mjs",WRAPPER)]
    for key in ORDER:
        slug = "old" if key.startswith("old") else "new"
        kind = "chart" if key.endswith("Chart") else "sources"
        source = OUT/(slug+"."+kind+".json")
        fragment = target/("common-switch-"+slug+"-"+kind+".html")
        require(not fragment.exists(), "Never overwrite rendered evidence")
        write(source,values[key])
        subprocess.run([NODE,str(TOOLS/("render-inline-"+kind+".mjs")),"--input",str(source),"--output",str(fragment)],check=True)
        require(fragment.stat().st_size < 1_000_000, "Oversized inline fragment")
        payload_refs[key] = {"path":source.relative_to(OUT).as_posix(),"sha256":sha(source)}
        fragments[key] = {"path":fragment.relative_to(OUT).as_posix(),"sha256":sha(fragment)}
    require(all(sha(OUT/name) == value for name,value in pins.items()), "Audited inputs changed during render")
    receipt = {"schema":"common-support-switch-render-v1","builderSha256":sha(__file__),"inputPins":pins,
        "presentationHelpers":helpers,"deliveryOrder":ORDER,"fragments":fragments,"payloads":payload_refs,"chartRows":24,
        "tableRows":[[len(q["rows"]) for item in values[k]["items"] for q in item["queries"]] for k in ("oldSources","newSources")],
        "newAccountExecutions":0,"analysisArtifactsModified":False}
    write(OUT/"render-receipt.json",receipt)
    print(json.dumps({"passed":True,"receiptSha256":sha(OUT/"render-receipt.json")},ensure_ascii=False))


if __name__ == "__main__":
    main()
