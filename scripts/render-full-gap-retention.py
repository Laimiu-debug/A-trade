"""Render the reviewed event context and the complete new policy comparison."""
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/full-gap-retention-v1"
CONTEXT = ROOT / "artifacts/strategy-search/board-environment-context-v1/readout.json"
NODE = "C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
TOOLS = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts")
WRAPPER = Path("C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
EXITS = {"trail10": "固定10%跟踪", "volatilityTrail": "波动幅度跟踪"}
ORDER = ["contextChart", "contextSources", "gapChart", "gapSources"]


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


def chart(identity, title, rows, category_field, measure, series, categories, series_order, source, *, zero=False):
    columns = list(rows[0])
    spec = {"type": "horizontalBar", "x": category_field, "y": measure, "series": series,
            "stackable": False, "startAtZero": True, "showLegend": True, "showValues": False,
            "showXAxisLabel": True, "showYAxisLabel": False, "xLabel": measure,
            "categoryOrder": categories, "seriesOrder": series_order}
    if zero:
        spec["annotations"] = [{"id": "zero-difference", "kind": "benchmark", "label": "0：收益相同",
                                 "field": "无差异基准", "measure": series_order[-1]}]
    return {"schemaVersion": 1, "id": identity, "title": title, "height": 500, "theme": "codex-classic",
            "chart": spec, "rows": rows, "columns": columns, "source": source}


def source_query(identity, label, rows, source, period, columns=None):
    return {"id": identity, "source": {**source, "label": label}, "rows": rows,
            "columns": columns or list(rows[0]), "reportingPeriod": period,
            "preview": {"kind": "aggregate", "note": "全部预设分组的汇总读数。", "totalRows": len(rows)}}


def payloads(report, context):
    output = {}
    rows = []
    for row in context["epochRows"]:
        if row["period"] != "2024-2025":
            continue
        rows.append({"板块": BOARDS[row["board"]], "时期": "9·24前" if row["epoch"] == "before20240924" else "9·24起",
                     "上涨覆盖均值（%）": 100 * row["breadth20"]["mean"],
                     "决策日起": row["observedStart"], "决策日止": row["observedEnd"], "决策日数": row["sessions"],
                     "信息日起": row["asOfStart"], "信息日止": row["asOfEnd"], "固定样本数": row["fixedPopulation"]})
    assert len(rows) == 6
    context_source = {"label": "9·24前后上涨覆盖", "files": [CONTEXT.relative_to(ROOT).as_posix()],
        "metricDefinitions": [{"label": "上涨覆盖", "definition": "每个决策日取上一源交易日已知的20日上涨股票比例，再在该时期内求平均；分母为对应板块当时有完整价格证据的固定样本成员。"}],
        "caveats": ["前段为2024年1月2日至9月23日的176个决策日；后段为2024年9月24日至2025年12月31日的309个决策日。区间长度不同，9月24日的环境信息截至9月23日。",
                    "固定本地样本存在历史覆盖限制；图不代表全部A股的无偏统计。它描述上涨覆盖变化，不识别量化资金、不认证唯一总龙头，也不说明后段每天都强势。"],
        "evidenceFlow": [{"kind": "calculation", "title": "已有分段读数", "body": "读取已审board-environment-context的六条epochRows，breadth20.mean乘100；没有重新划分有利日期。"}]}
    title = "9·24前后：上涨股票的覆盖范围"
    output["contextChart"] = chart("gap-context", title, rows, "板块", "上涨覆盖均值（%）", "时期", list(BOARDS.values()), ["9·24前", "9·24起"], context_source)
    output["contextSources"] = {"schemaVersion": 1, "items": [{"id": "gap-context", "title": title,
        "queries": [source_query("context-epochs", "市场环境分段", rows, context_source, "2024-01-02—2025-12-31")]}]}
    gaps = []
    for row in report["comparisons"]:
        delta = row["candidateMinusControl"]["accountReturnPct"]
        assert math.isfinite(delta)
        assert math.isclose(delta, row["candidateMetrics"]["accountReturnPct"] - row["controlMetrics"]["accountReturnPct"], abs_tol=1e-9)
        gaps.append({"板块与退出": category(row["board"], row["exit"]), "时期": row["period"].replace("-", "—"),
                     "账户收益差（百分点）": delta, "无差异基准": 0,
                     "完整板块": BOARDS[row["board"]], "完整退出": EXITS[row["exit"]]})
    assert len(gaps) == 12
    source = {"label": "强股完整缺口两日存续对照",
        "files": [(OUT / n).relative_to(ROOT).as_posix() for n in ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")],
        "metricDefinitions": [{"label": "账户收益差", "definition": "同板块、同历史区间、同退出方式，要求缺口两日未完全回补的账户减去同样等待两日的对照账户。正值也可能只是亏损较少。"},
                              {"label": "简称", "definition": "主、创、科分别指主板、创业板、科创板；固定指固定10%跟踪退出，波动指3倍前20日真实波幅的跟踪退出。"}],
        "caveats": ["24个新账户覆盖两期、三板、两种退出、两个入场规则。每账户50万元、最多五仓、单次20%预算，含既定费用及双边各10基点滑点。",
                    "两策略都在缺口形成后第2个交易日收盘决策，随后开盘尝试；保留条件是两天最低价都高于缺口形成前的最高价，不是从未进入缺口。",
                    "账户收益包含未平仓浮盈亏。9·24分段延续资金和持仓，区间长度不同；单笔均值、利润因子与账户结果分别保留。",
                    "2022—2025已被反复用于开发；没有独立样本外或稳定收益认证。未设单笔30%、平均盈利或盈亏比门槛。"],
        "evidenceFlow": [{"kind": "calculation", "title": "完整同钟对照", "body": "展示全部12组候选减对照收益差，未选择表现最好的板块或退出方式。"},
                         {"kind": "validation", "title": "已记录检查", "body": "新信号日程先冻结并由独立实现核验；24个账户完成费用、持仓、现金及退出核查，汇总读数再次独立核对。"}]}
    title = "守住缺口，是否改善账户收益？"
    output["gapChart"] = chart("gap-contrasts", title, gaps, "板块与退出", "账户收益差（百分点）", "时期",
        [category(b,e) for b in BOARDS for e in EXITS], ["2022—2023", "2024—2025"], source, zero=True)
    accounts = [{"时期": r["period"], "板块": BOARDS[r["board"]], "退出": EXITS[r["exit"]],
                 "规则": "守住缺口" if r["policy"] == "retained" else "同钟对照", **r["metrics"]} for r in report["accountRows"]]
    fields = [("accountReturnPct","账户净收益（%）"),("maxDrawdownPct","最大回撤（%）"),("closed","闭仓数"),
              ("meanWinPct","平均盈利（%）"),("meanLossPct","平均亏损（%）"),("profitFactor","利润因子"),
              ("payoffRatio","盈亏比"),("meanExposurePct","平均仓位（%）"),("openPositions","未平仓数"),
              ("openUnrealizedPnlAmount","未平浮盈亏（元）"),("top5ShareOfGrossProfitPct","前五盈利占比（%）"),
              ("unreliableValuationDays","估值受限日"),("openStaleMarks","陈旧估值持仓数")]
    columns = ["时期","板块","退出","规则"] + [{"field": f,"label": label} for f,label in fields if f in accounts[0]]
    queries = [source_query("gap-contrasts", "全部收益差", gaps, source, "2022—2025"),
               source_query("gap-accounts", "全部账户结果", accounts, source, "2022—2025", columns)]
    for field,label in (("yearRows","年度"),("halfYearRows","半年"),("phaseRows","叙事阶段"),("curveEpochRows","9·24前后")):
        selected = [{"时期": r["period"], "板块": BOARDS[r["board"]], "退出": EXITS[r["exit"]],
                     "规则": "守住缺口" if r["policy"] == "retained" else "同钟对照", "区间":r.get("phaseTitle",r["id"]),
                     "开始":r["observedStart"], "结束":r["observedEnd"], "收益（%）":r["returnPct"],
                     "回撤（%）":r["maxDrawdownPct"]} for r in report[field]]
        if selected:
            queries.append(source_query("gap-"+field.lower(), label+"连续账户", selected, source, "2022—2025"))
    formations = [{"时期":r["period"],"板块":BOARDS[r["board"]],"全部形成":r["rawSetups"],
        "锁内重复":r["lockedSetups"],"排名排除":r["rankExcludedSetups"],"原Top5机会":r["opportunities"],
        "已知确认":r["confirmation"]["known"],"未知确认":r["confirmation"]["unknown"],"期末截尾":r["confirmation"]["censored"],
        "对照信号":r["signals"]["control"],"守缺口信号":r["signals"]["retained"]} for r in report["formationCoverageRows"]]
    coverage = [{"时期":r["period"],"板块":BOARDS[r["board"]],"退出":EXITS[r["exit"]],
        "规则":"守住缺口" if r["policy"]=="retained" else "同钟对照", "原机会":r["originalOpportunities"],
        "信号数":r["scheduledSignals"],"未放行":r["notScheduled"],"放行未买入":r["notEntered"],
        "买入":r["entered"],"闭仓":r["closed"],"未平":r["openAtCutoff"],
        "估值受限日":r["valuationUnreliableDays"],"陈旧估值持仓":r["openStaleMarks"]} for r in report["coverageRows"]]
    queries.extend([source_query("gap-formation-coverage","形成与确认分母",formations,source,"2022—2025"),
                    source_query("gap-account-coverage","信号与成交分母",coverage,source,"2022—2025")])
    output["gapSources"] = {"schemaVersion":1,"items":[{"id":"gap-results","title":title,"queries":queries}]}
    return output


def main():
    names = ("plan.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json")
    pins = {name: sha(OUT/name) for name in names}
    audit = read(OUT / "readout-validation.json")
    assert audit["passed"] and read(OUT / "validation.json")["passed"]
    assert audit["readoutSha256"] == pins["readout.json"] and audit["comparisonSha256"] == pins["comparison.json"]
    values = payloads(read(OUT/"readout.json"), read(CONTEXT))
    target = OUT / "inline"
    target.mkdir(exist_ok=False)
    fragments, payload_refs = {}, {}
    for key in ORDER:
        kind = "chart" if key.endswith("Chart") else "sources"
        slug = "gap-context" if key.startswith("context") else "gap-comparison"
        payload = OUT / (slug+"-"+kind+".json")
        fragment = target / (slug+"-"+kind+".html")
        write(payload, values[key])
        subprocess.run([NODE,str(TOOLS/("render-inline-"+kind+".mjs")),"--input",str(payload),"--output",str(fragment)],check=True)
        assert fragment.stat().st_size < 1_000_000
        payload_refs[key] = {"path": payload.relative_to(OUT).as_posix(), "sha256": sha(payload)}
        fragments[key] = {"path": fragment.relative_to(OUT).as_posix(), "sha256": sha(fragment)}
    assert all(sha(OUT/n)==v for n,v in pins.items())
    receipt = {"schema":"full-gap-retention-render-v1", "builderSha256":sha(__file__), "inputPins":pins,
        "contextSource":ref(CONTEXT), "presentationHelpers":[{"path":str(p),"sha256":sha(p)} for p in (TOOLS/"render-inline-chart.mjs",TOOLS/"render-inline-sources.mjs",WRAPPER)],
        "deliveryOrder":ORDER,"payloads":payload_refs,"fragments":fragments,"chartRows":[6,12],
        "tableRows":[[len(q["rows"]) for i in values[k]["items"] for q in i["queries"]] for k in ("contextSources","gapSources")],
        "newAccountExecutions":0,"analysisArtifactsModified":False}
    write(OUT/"render-receipt.json",receipt)
    print(json.dumps({"passed":True,"receiptSha256":sha(OUT/"render-receipt.json")}))


if __name__ == "__main__":
    main()
