"""Render audited population contrasts with native chart and Sources helpers."""
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/all-opportunity-feedback-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
PERIODS = ("2022-2023", "2024-2025")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
MODELS = {"intercept": "近期均值", "ridge": "环境模型"}
POPULATIONS = {"originalEntered": "原入场样本", "allExecutable": "全部可成交"}
ORDER = ["oldChart", "oldSources", "newChart", "newSources"]
VALUE = "账户收益差（百分点）"
MAPPING = "主固定表示主板的固定10%跟踪，主波动表示主板的波动幅度跟踪；创固定表示创业板的固定10%跟踪，创波动表示创业板的波动幅度跟踪；科固定表示科创板的固定10%跟踪，科波动表示科创板的波动幅度跟踪。"
IDS = [("period", "时期"), ("boardLabel", "板块"), ("exitLabel", "退出规则")]
ACCOUNT_IDS = IDS + [("candidateLabel", "策略")]
LIMITS = [
    "收益差是全部可成交反馈账户减去原入场样本反馈账户，两者均重新执行完整资金和持仓路径；差值为正也可能只是亏损减少。它不是单笔收益、年化收益或配对交易的因果效应。",
    "每个新账户初始50万元、最多五仓、每次20%预算，沿用手续费与双边各10基点滑点及成交限制；两个反馈样本只改变冻结模型的放行结果。回退时仍按原信号放行。",
    "账户收益包括未平浮盈亏与费用。闭仓均值、盈亏比和利润因子单列，不能替代连续账户；未平、未成交和拒绝机会均保留。",
    "2024年9月24日前后是描述分段，切片延续原账户权益和持仓，不清零本金、不作为买入开关。两个切片时长不同。",
    "预测比较只在两侧均可拟合且最终闭仓的同一机会上计算共同误差；每个形成日总权重为一。单侧可拟合覆盖单列，未平和未成交不填零。",
    "模型使用A=T−1可见的最近126个源交易日已成熟标签，至少20个有效形成日；未来最终结果只作事后评价，绝不改变冻结准入。预测改善不必然转成账户收益。",
    "2022—2025均为反复查看的开发样本；2026不在本轮内。原历史股票池、原始价格和估值限制继续适用，同日与同股机会有关联，本轮不能证明稳定盈利或识别主力、散户及量化资金。",
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


def payloads(report, files):
    require(report["counts"]["accounts"] == 60 and report["counts"]["comparisons"] == 96
            and report["newAccounts"] == 48 and report["targetAchieved"] is False, "Complete audited matrix required")
    outputs = {}
    source = {"label": "全部机会反馈账户对照", "files": files,
        "metricDefinitions": [{"label": VALUE, "definition": "同板块、同退出和同模型下，扩大反馈样本前后两个完整账户的净收益差；正值表示扩大样本的账户更高。"},
            {"label": "板块与退出简称", "definition": MAPPING}], "caveats": LIMITS,
        "evidenceFlow": [{"kind": "calculation", "title": "固定对照", "body": "全部可成交减原入场样本；环境模型减近期均值；四个候选各自减原信号基准。所有原定对照保留。"},
            {"kind": "validation", "title": "已记录检查", "body": "48个日程在首个新账户前冻结并独立核对；48个新账户与12个复用基准的账本和连续收益读数经过独立审计，预测评价独立复算。"}]}
    for period, slug in zip(PERIODS, ("old", "new")):
        select = lambda name: [r for r in report[name] if r["period"] == period]
        primary = {(r["board"], r["exit"], r["candidate"].split("__")[1]): r for r in select("primaryComparisons")}
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
        title = period.replace("-", "—") + "：扩大反馈样本后的账户收益变化"
        outputs[slug+"Chart"] = {"schemaVersion": 1, "id": "all-feedback-"+slug, "height": 500, "theme": "codex-classic",
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
        feedback = [{**labels(r), "targetDayCount": r["targetDayCount"], "fittedDays": r["statusCounts"].get("fitted",0),
            "fallbackDays": r["statusCounts"].get("fallback",0), "scheduledSignals": r["scheduledSignals"],
            "originalOpportunities": r["originalOpportunities"]} for r in select("feedbackCoverageRows")]
        shifts = [{**labels(r), "actualBuyCount": r["actualBuyCount"], "candidateOnlyBuyCount": r["candidateOnlyBuyCount"],
            "candidateOnlyBuyPct": r["candidateOnlyBuyPct"], "onlyClosedPnl": r["candidateOnly"]["closedPnlAmount"],
            "onlyOpenPnl": r["candidateOnly"]["openUnrealizedPnlAmount"]} for r in select("sampleShiftRows")]
        tables += [("feedback", "反馈覆盖与放行", feedback, ACCOUNT_IDS+[("targetDayCount","全部目标日"),("fittedDays","可拟合日"),
            ("fallbackDays","回退日"),("scheduledSignals","放行信号"),("originalOpportunities","原机会")]),
            ("sample-shift", "实际入场变化", shifts, ACCOUNT_IDS+[("actualBuyCount","实际入场"),("candidateOnlyBuyCount","基准未入场数"),
                ("candidateOnlyBuyPct","基准未入场占比（%）"),("onlyClosedPnl","该组闭仓损益（元）"),("onlyOpenPnl","该组未平浮盈亏（元）")])]
        errors, support = [], []
        for row in select("predictionEvaluationRows"):
            score = row["commonFitted"]["score"]
            ids = {**labels(row), "id": row["contrastId"]+"/"+row["id"],
                "controlLabel": candidate_label(row["controlCandidate"]), "candidateLabel": candidate_label(row["candidateCandidate"])}
            errors.append({**ids, "closed": score["episodeCount"], "formationDays": score["formationDayCount"],
                "controlMae": score["control"]["maePp"], "candidateMae": score["candidate"]["maePp"],
                "maeDelta": score["candidateMinusControl"]["maePp"], "mseDelta": score["candidateMinusControl"]["msePpSquared"]})
            support.append({**ids, **{key: row["fitCells"][key]["opportunityCount"] for key in
                ("bothFitted","controlOnlyFitted","candidateOnlyFitted","neitherFitted")},
                "openCount": row["coverage"]["openCount"], "unfilledCount": row["coverage"]["unfilledCount"]})
        prediction_ids = IDS+[("id","对照/形成区间"),("controlLabel","对照"),("candidateLabel","候选")]
        tables += [("prediction-errors","共同可拟合预测误差",errors,prediction_ids+[("closed","闭仓标签"),("formationDays","形成日"),
            ("controlMae","对照MAE（百分点）"),("candidateMae","候选MAE（百分点）"),("maeDelta","MAE差（百分点）"),("mseDelta","MSE差（百分点平方）")]),
            ("prediction-support","预测支持分母",support,prediction_ids+[("bothFitted","两侧可拟合"),("controlOnlyFitted","仅对照可拟合"),
                ("candidateOnlyFitted","仅候选可拟合"),("neitherFitted","两侧均不可拟合"),("openCount","原机会未平"),("unfilledCount","原机会未成交")])]
        expected = [30,48,60,120,240] + ([60] if slug == "new" else []) + [24,24] + ([120,120] if slug == "new" else [72,72])
        require([len(t[2]) for t in tables] == expected, "All source-period rows required")
        queries = [{"id": name,"source": {**source,"label": label},"rows": rows,
            "columns": [{"field": k,"label": v} for k,v in fields],"reportingPeriod": period,
            "preview": {"kind":"aggregate","note":"本时期全部已审汇总行，展示指定指标；完整分布、逐机会与逐日证据见来源文件。","totalRows":len(rows)}}
            for name,label,rows,fields in tables]
        outputs[slug+"Sources"] = {"schemaVersion":1,"items":[{"id":"all-feedback-"+slug,"title":title,"queries":queries}]}
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
    require(audit["verifierSha256"] == sha(ROOT/"scripts/verify-all-opportunity-feedback-readout.py"), "Readout verifier changed")
    for name,value in freeze["sourcePins"].items():
        require(sha(ROOT/name) == value, "Presentation source changed")
    files = [(OUT/name).relative_to(ROOT).as_posix() for name in names[:5]]
    for key in ("opportunityRowsArtifact","candidateEntryRowsArtifact","pairedPredictionRowsArtifact"):
        item = report[key]
        require(sha(ROOT/item["path"]) == item["sha256"], "Readout sidecar changed")
        files.append(item["path"])
    values = payloads(report,files)
    target = OUT/"inline"; target.mkdir(exist_ok=True)
    fragments, payload_refs = {}, {}
    helpers = [{"path":str(path),"sha256":sha(path)} for path in (TOOLS/"render-inline-chart.mjs",TOOLS/"render-inline-sources.mjs",WRAPPER)]
    for key in ORDER:
        slug = "old" if key.startswith("old") else "new"
        kind = "chart" if key.endswith("Chart") else "sources"
        source = OUT/(slug+"."+kind+".json")
        fragment = target/("all-feedback-"+slug+"-"+kind+".html")
        require(not fragment.exists(), "Never overwrite rendered evidence")
        write(source,values[key])
        subprocess.run([NODE,str(TOOLS/("render-inline-"+kind+".mjs")),"--input",str(source),"--output",str(fragment)],check=True)
        require(fragment.stat().st_size < 1_000_000, "Oversized inline fragment")
        payload_refs[key] = {"path":source.relative_to(OUT).as_posix(),"sha256":sha(source)}
        fragments[key] = {"path":fragment.relative_to(OUT).as_posix(),"sha256":sha(fragment)}
    require(all(sha(OUT/name) == value for name,value in pins.items()), "Audited inputs changed during render")
    receipt = {"schema":"all-opportunity-feedback-render-v1","builderSha256":sha(__file__),"inputPins":pins,
        "presentationHelpers":helpers,"deliveryOrder":ORDER,"fragments":fragments,"payloads":payload_refs,"chartRows":24,
        "tableRows":[[len(q["rows"]) for item in values[k]["items"] for q in item["queries"]] for k in ("oldSources","newSources")],
        "newAccountExecutions":0,"analysisArtifactsModified":False}
    write(OUT/"render-receipt.json",receipt)
    print(json.dumps({"passed":True,"receiptSha256":sha(OUT/"render-receipt.json")},ensure_ascii=False))


if __name__ == "__main__":
    main()
