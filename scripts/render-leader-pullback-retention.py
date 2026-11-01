"""Present the preregistered first-down member-retention comparison after audit."""
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-pullback-retention-v1"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
POLICIES = {"control": "下跌后首次突破", "retained": "守住参考价后突破"}
CONTRASTS = {"retainedVsControl": "守位条件减同事件突破"}
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
        values.update({"退出": EXITS[row["exit"]], "规则": POLICIES[row["policy"]]})
    return values


def payloads(report):
    contrasts = []
    for row in report["primaryComparisons"]:
        assert row["id"] == "retainedVsControl"
        delta = row["candidateMinusControl"]["accountReturnPct"]
        assert math.isfinite(delta)
        assert math.isclose(delta, row["candidateMetrics"]["accountReturnPct"] - row["controlMetrics"]["accountReturnPct"], abs_tol=1e-9)
        contrasts.append({"板块与退出": category(row["board"], row["exit"]), "时期": row["period"].replace("-", "—"),
                          "账户收益差（百分点）": delta, "无差异基准": 0,
                          "完整板块": BOARDS[row["board"]], "完整退出": EXITS[row["exit"]]})
    assert len(contrasts) == 12
    source = {"label": "首次下跌后的同伴守位对照",
        "files": [(OUT / n).relative_to(ROOT).as_posix() for n in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")],
        "metricDefinitions": [
            {"label": "收益差", "definition": "同板块、同历史区间、同退出方式，增加守位条件的账户净收益减去同事件普通突破账户净收益。正值也可能只是亏损较少。"},
            {"label": "事件顺序", "definition": "原领涨形成后，第一次收盘低于前一源日收盘，必须早于同伴原定的第一次价格突破；不延后突破，不更换首次下跌。"},
            {"label": "守位", "definition": "以原领涨首次下跌前一日的同伴收盘为固定参考，同伴从下跌日到突破前一日，每天收盘均不低于该价。盘中跌破不在此条件中，曾有收盘跌破后恢复仍不通过。"},
            {"label": "简称", "definition": "主、创、科分别为主板、创业板、科创板；固定为10%跟踪退出，波动为3倍前20日真实波幅跟踪退出。"}],
        "caveats": [
            "24个新账户覆盖两期、三板、两种退出及两条入场规则。每账户50万元、最多五仓、单次20%预算，含既定费用与双边各10基点滑点。",
            "沿用第43批全部原始Top5、归属与第一次突破，包括原来突破日金额未知的机会；形成日成员成交额不高于原领涨股的资格仍被继承。",
            "两规则共用严格先下跌后突破的事件及完整价格路径。先后不符、未知、未放行和未买入均保留，不补位；旧控制账户只解释样本变化。",
            "首次相邻收盘下跌不一定代表趋势反转或失去领导权。条件也不要求同伴逐日上涨；共同市场变化、自身强势和参与度差异仍可能解释结果。",
            "账户收益含未平仓浮盈亏；年度、阶段及9·24切片延续持仓与资金，不重新开始账户。保留原始价格、历史样本覆盖及估值限制。",
            "2022—2025已反复用于开发，无独立样本外或稳定收益认证；不设单笔30%、平均盈利、盈亏比或持有时间门槛。"],
        "evidenceFlow": [
            {"kind": "calculation", "title": "完整对照", "body": "主图保留12组守位条件的收益增量，同时给出24账户绝对表现、全部预设时间分段和12组新旧控制样本变化背景。"},
            {"kind": "validation", "title": "记录核对", "body": "逐机会重建截至突破日的首次下跌及成员价格路径后才执行账户；现金、持仓、费用、原始机会分母和全部汇总再次独立核对。"}]}
    title = "原领涨股回落后，同伴守住价位有用吗？"
    chart = {"schemaVersion": 1, "id": "pullback-retention-comparison", "title": title, "height": 500, "theme": "codex-classic",
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
    all_contrasts = [{"时期":r["period"],"板块":BOARDS[r["board"]],"退出":EXITS[r["exit"]],
        "对照":CONTRASTS[r["id"]],"收益差（百分点）":r["candidateMinusControl"]["accountReturnPct"],
        "候选收益（%）":r["candidateMetrics"]["accountReturnPct"],"比较对象收益（%）":r["controlMetrics"]["accountReturnPct"]}
        for r in report["comparisons"]]
    queries = [query("pullback-contrasts", "全部守位对照", all_contrasts), query("pullback-accounts", "全部账户结果", accounts, columns)]
    for field,label in (("yearRows","年度"),("halfYearRows","半年"),("phaseRows","叙事阶段"),("curveEpochRows","9·24前后")):
        selected = [{**dimensions(r), "区间": r.get("phaseTitle", {"before20240924":"9·24前", "from20240924":"9·24起"}.get(r["id"],r["id"])),
                     "开始":r["observedStart"], "结束":r["observedEnd"], "收益（%）":r["returnPct"], "回撤（%）":r["maxDrawdownPct"]} for r in report[field]]
        queries.append(query("pullback-"+field.lower(), label+"连续账户", selected))
    sequence = []
    for r in report["sequenceCoverageRows"]:
        counts = r["commonStatusCounts"]
        sequence.append({**dimensions(r,False),"父形成事件":r["parentFormations"],"已形成群体":r["formed"],
            "原机会":r["originalOpportunities"],"首次下跌未知":counts["firstDownUnknown"],
            "截至突破日无下跌":counts["noDownThroughT"],"下跌与突破同日":counts["sameDayDown"],
            "成员路径未知":counts["memberPathUnknown"],"共同可比":counts["commonKnown"],
            "守住参考价":r["retainedKnown"],"曾跌破参考价":r["lostKnown"]})
    coverage = [{**dimensions(r), "原机会":r["originalOpportunities"], "信号数":r["scheduledSignals"],
                 "未放行":r["notScheduled"], "放行未买入":r["notEntered"], "买入":r["entered"], "闭仓":r["closed"],
                 "未平":r["openAtCutoff"], "估值受限日":r["valuationUnreliableDays"], "陈旧估值持仓":r["openStaleMarks"]} for r in report["coverageRows"]]
    population = [{**dimensions(r,False),"退出":EXITS[r["exit"]],
        "旧控制收益（%）":r["oldMetrics"]["accountReturnPct"],"新共同控制收益（%）":r["newMetrics"]["accountReturnPct"],
        "旧信号数":r["oldMetrics"]["signals"],"新信号数":r["newMetrics"]["signals"],
        "旧平均仓位（%）":r["oldMetrics"]["meanExposurePct"],"新平均仓位（%）":r["newMetrics"]["meanExposurePct"],
        "旧闭仓数":r["oldMetrics"]["closed"],"新闭仓数":r["newMetrics"]["closed"]} for r in report["populationContextRows"]]
    queries.extend([query("pullback-sequence", "事件顺序及守位分母", sequence),
        query("pullback-coverage", "信号与成交分母", coverage),query("pullback-population", "新旧控制样本变化背景", population)])
    assert [len(q["rows"]) for q in queries] == [12,24,48,96,192,24,6,24,12]
    return {"comparisonChart": chart, "comparisonSources": {"schemaVersion":1,"items":[{"id":"pullback-results","title":title,"queries":queries}]}}


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
        payload=OUT/("pullback-retention-"+kind+".json"); fragment=target/("pullback-retention-"+kind+".html")
        write(payload,values[key])
        subprocess.run([NODE,str(TOOLS/("render-inline-"+kind+".mjs")),"--input",str(payload),"--output",str(fragment)],check=True)
        assert fragment.stat().st_size < 1_000_000
        payload_refs[key]={"path":payload.relative_to(OUT).as_posix(),"sha256":sha(payload)}
        fragments[key]={"path":fragment.relative_to(OUT).as_posix(),"sha256":sha(fragment)}
    assert all(sha(OUT/n)==v for n,v in pins.items())
    write(OUT/"render-receipt.json",{"schema":"leader-pullback-retention-render-v1","builderSha256":sha(__file__),"inputPins":pins,
        "contextSource":ref(ROOT/"docs/leader-pullback-retention-context.json"),
        "presentationHelpers":[{"path":str(p),"sha256":sha(p)} for p in (TOOLS/"render-inline-chart.mjs",TOOLS/"render-inline-sources.mjs",WRAPPER)],
        "deliveryOrder":ORDER,"payloads":payload_refs,"fragments":fragments,"chartRows":[12],
        "tableRows":[len(q["rows"]) for q in values["comparisonSources"]["items"][0]["queries"]],
        "newAccountExecutions":0,"analysisArtifactsModified":False})
    print(json.dumps({"passed":True,"receiptSha256":sha(OUT/"render-receipt.json")}))


if __name__ == "__main__":
    main()
