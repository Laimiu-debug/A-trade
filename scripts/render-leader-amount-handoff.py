"""Present all preregistered member/leader activity comparisons after audit."""
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-amount-handoff-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
ORDER = ["comparisonChart", "comparisonSources"]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def ref(path):
    return {"path": Path(path).relative_to(ROOT).as_posix(), "sha256": sha(path)}


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def category(board, exit_name):
    return {"main": "主", "chinext": "创", "star": "科"}[board] + ("固定" if exit_name == "trail10" else "波动")


def dimensions(row, account=True):
    values = {"时期": row["period"], "板块": BOARDS[row["board"]]}
    if account:
        values.update({"退出": EXITS[row["exit"]], "规则": "成交额交叉" if row["policy"] == "handoff" else "价格突破"})
    return values


def payloads(report):
    contrasts = []
    for row in report["comparisons"]:
        delta = row["candidateMinusControl"]["accountReturnPct"]
        assert math.isfinite(delta)
        assert math.isclose(delta, row["candidateMetrics"]["accountReturnPct"] - row["controlMetrics"]["accountReturnPct"], abs_tol=1e-9)
        contrasts.append({"板块与退出": category(row["board"], row["exit"]), "时期": row["period"].replace("-", "—"),
                          "账户收益差（百分点）": delta, "无差异基准": 0,
                          "完整板块": BOARDS[row["board"]], "完整退出": EXITS[row["exit"]]})
    assert len(contrasts) == 12
    source = {"label": "同伴成交额交叉对照",
        "files": [(OUT / n).relative_to(ROOT).as_posix() for n in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")],
        "metricDefinitions": [
            {"label": "账户收益差", "definition": "同板块、同历史区间、同退出方式，增加成交额交叉条件的账户收益减去只看同伴价格突破的账户收益。正值可能只是亏损较少或交易更少。"},
            {"label": "成交额交叉", "definition": "固定同伴第一次突破自身前五日高点当天，其近三日成交额之和从前一日不高于原领涨股、变为高于原领涨股。两规则均收盘决策，随后开盘尝试。"},
            {"label": "简称", "definition": "主、创、科分别为主板、创业板、科创板；固定为10%跟踪退出，波动为3倍前20日真实波幅跟踪退出。"}],
        "caveats": [
            "24个新账户覆盖两期、三板、两种退出及两个入场规则。每账户50万元、最多五仓、单次20%预算，含既定费用与双边各10基点滑点。",
            "领涨事件和统计同伴沿用事先冻结的群体。每锚观察五日，第一次价格突破即消费；所有权与Top5先于金额筛选，未知不补位。",
            "账户收益含未平仓浮盈亏；年度、阶段及9·24分段延续持仓与资金，不重新开始账户。保留估值与历史样本覆盖限制。",
            "成交额属于交易活跃代理。股票自身放量、规模差异和共同消息都可能解释结果，不能据此识别主力换仓或因果带动。",
            "2022—2025已反复用于开发，无独立样本外或稳定收益认证；不设单笔30%、平均盈利或盈亏比门槛。"],
        "evidenceFlow": [
            {"kind": "calculation", "title": "完整对照", "body": "全12组收益差均为候选减同钟价格突破对照，包含不交易、亏损和未知证据对应的完整账户。"},
            {"kind": "validation", "title": "已记录核对", "body": "新状态机和信号日程独立重建后才执行账户；现金、持仓、成交费用与全部汇总再次核对。"}]}
    title = "成交额超过原领涨股，是否改善收益？"
    chart = {"schemaVersion": 1, "id": "amount-handoff-comparison", "title": title, "height": 500, "theme": "codex-classic",
        "chart": {"type": "horizontalBar", "x": "板块与退出", "y": "账户收益差（百分点）", "series": "时期",
            "stackable": False, "startAtZero": True, "showLegend": True, "showValues": False,
            "showXAxisLabel": True, "showYAxisLabel": False, "xLabel": "账户收益差（百分点）",
            "categoryOrder": [category(b,e) for b in BOARDS for e in EXITS], "seriesOrder": ["2022—2023", "2024—2025"],
            "annotations": [{"id": "zero-difference", "kind": "benchmark", "label": "0：收益相同", "field": "无差异基准", "measure": "2024—2025"}]},
        "rows": contrasts, "columns": list(contrasts[0]), "source": source}

    def query(identity, label, rows, columns=None):
        return {"id": identity, "source": {**source, "label": label}, "rows": rows,
                "columns": columns or list(rows[0]), "reportingPeriod": "2022—2025",
                "preview": {"kind": "aggregate", "note": "全部预设分组的汇总读数。", "totalRows": len(rows)}}

    accounts = [{**dimensions(r), **r["metrics"]} for r in report["accountRows"]]
    fields = [("accountReturnPct","账户净收益（%）"),("maxDrawdownPct","最大回撤（%）"),("closed","闭仓数"),
              ("meanWinPct","平均盈利（%）"),("meanLossPct","平均亏损（%）"),("profitFactor","利润因子"),
              ("closedMeanPct","闭仓平均收益（%）"),("payoffRatio","盈亏比"),("meanExposurePct","平均仓位（%）"),
              ("feesPaid","费用（元）"),("openPositions","未平仓数"),("openUnrealizedPnlAmount","未平浮盈亏（元）"),
              ("top5ShareOfGrossProfitPct","前五盈利占比（%）"),("unreliableValuationDays","估值受限日"),("openStaleMarks","陈旧估值持仓数")]
    columns = ["时期","板块","退出","规则"] + [{"field": f,"label": label} for f,label in fields]
    queries = [query("handoff-contrasts", "全部收益差", contrasts), query("handoff-accounts", "全部账户结果", accounts, columns)]
    for field,label in (("yearRows","年度"),("halfYearRows","半年"),("phaseRows","叙事阶段"),("curveEpochRows","9·24前后")):
        selected = [{**dimensions(r), "区间": r.get("phaseTitle", {"before20240924":"9·24前", "from20240924":"9·24起"}.get(r["id"],r["id"])),
                     "开始":r["observedStart"], "结束":r["observedEnd"], "收益（%）":r["returnPct"], "回撤（%）":r["maxDrawdownPct"]} for r in report[field]]
        queries.append(query("handoff-"+field.lower(), label+"连续账户", selected))
    formations = [{**dimensions(r,False), "原领涨事件":r["sourceEvents"], "锁内事件":r["lockedEvents"],
                   "可观察群体":r["unlockedFormed"], "成员事件":r["memberEpisodes"], "进入观察":r["observedMembers"],
                   "首次突破":r["firstBreakout"], "价格未知":r["priceUnknown"], "到期":r["expired"], "期末截尾":r["censored"],
                   "原Top5":r["selectedTop5"], "金额完整":r["jointKnown"], "金额未知":r["jointUnknown"],
                   "交叉通过":r["crossingTrue"], "交叉未通过":r["crossingFalse"]} for r in report["formationCoverageRows"]]
    coverage = [{**dimensions(r), "原机会":r["originalOpportunities"], "信号数":r["scheduledSignals"],
                 "未放行":r["notScheduled"], "放行未买入":r["notEntered"], "买入":r["entered"], "闭仓":r["closed"],
                 "未平":r["openAtCutoff"], "估值受限日":r["valuationUnreliableDays"], "陈旧估值持仓":r["openStaleMarks"]} for r in report["coverageRows"]]
    diagnostics = []
    for row in report["diagnosticRows"]:
        item = {**dimensions(row,False), "条件": {"crossing":"交叉通过", "nonCrossing":"交叉未通过", "jointUnknown":"金额未知"}[row["group"]], "机会数":row["opportunityCount"]}
        for key,label in (("memberOwnActivity","成员自身量能"),("anchorOwnActivity","原领涨股自身量能"),("pairRatioInitial","初始金额比"),("pairRatioPrevious","前日金额比"),("pairRatioCurrent","当日金额比")):
            value = row[key]; distribution = value["distribution"] or {}
            item.update({label+"可算数":value["knownCount"],label+"未知数":value["unknownCount"],
                         label+"均值":distribution.get("mean"),label+"中位数":distribution.get("median")})
        diagnostics.append(item)
    queries.extend([query("handoff-formations", "形成与确认分母", formations), query("handoff-coverage", "信号与成交分母", coverage),
                    query("handoff-activity", "自身量能与配对金额比", diagnostics)])
    assert [len(q["rows"]) for q in queries] == [12,24,48,96,192,24,6,24,18]
    return {"comparisonChart": chart, "comparisonSources": {"schemaVersion":1,"items":[{"id":"handoff-results","title":title,"queries":queries}]}}


def main():
    names = ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    pins = {name:sha(OUT/name) for name in names}; audit = read(OUT/"readout-validation.json")
    assert audit["passed"] and read(OUT/"validation.json")["passed"]
    assert audit["readoutSha256"] == pins["readout.json"] and audit["comparisonSha256"] == pins["comparison.json"]
    assert audit["accountValidationSha256"] == pins["validation.json"]
    values = payloads(read(OUT/"readout.json")); target=OUT/"inline"; target.mkdir(exist_ok=False)
    fragments, payload_refs = {}, {}
    for key in ORDER:
        kind = "chart" if key.endswith("Chart") else "sources"
        payload=OUT/("amount-handoff-"+kind+".json"); fragment=target/("amount-handoff-"+kind+".html")
        write(payload,values[key])
        subprocess.run([NODE,str(TOOLS/("render-inline-"+kind+".mjs")),"--input",str(payload),"--output",str(fragment)],check=True)
        assert fragment.stat().st_size < 1_000_000
        payload_refs[key]={"path":payload.relative_to(OUT).as_posix(),"sha256":sha(payload)}
        fragments[key]={"path":fragment.relative_to(OUT).as_posix(),"sha256":sha(fragment)}
    assert all(sha(OUT/n)==v for n,v in pins.items())
    write(OUT/"render-receipt.json",{"schema":"leader-amount-handoff-render-v1","builderSha256":sha(__file__),"inputPins":pins,
        "contextSource":ref(ROOT/"docs/leader-amount-handoff-context.json"),
        "presentationHelpers":[{"path":str(p),"sha256":sha(p)} for p in (TOOLS/"render-inline-chart.mjs",TOOLS/"render-inline-sources.mjs",WRAPPER)],
        "deliveryOrder":ORDER,"payloads":payload_refs,"fragments":fragments,"chartRows":[12],
        "tableRows":[len(q["rows"]) for q in values["comparisonSources"]["items"][0]["queries"]],
        "newAccountExecutions":0,"analysisArtifactsModified":False})
    print(json.dumps({"passed":True,"receiptSha256":sha(OUT/"render-receipt.json")}))


if __name__ == "__main__":
    main()
