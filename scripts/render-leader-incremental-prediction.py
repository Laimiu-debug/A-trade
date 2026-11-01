"""Present reviewed paired forecast errors, never simulated investment returns."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-incremental-prediction-v1"
NODE = Path("C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
PLUGIN = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11")
HELPERS = {"comparisonChart": PLUGIN / "skills/visualize-data/scripts/render-inline-chart.mjs",
           "comparisonSources": PLUGIN / "skills/visualize-data/scripts/render-inline-sources.mjs"}
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def payloads(report):
    source = {"label": "原领涨股的固定模型预测对照",
        "files": [(OUT / name).relative_to(ROOT).as_posix() for name in
                  ("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json")],
        "metricDefinitions": [
            {"label": "预测对象", "definition": "固定成员下一源交易日的未复权简单收盘收益。共同模型使用成员自身、其余19名固定同伴均值及沪深300当日收益；另一模型额外加入原领涨股当日收益。"},
            {"label": "误差改善", "definition": "共同模型均方误差减去加入原领涨股后的均方误差。正值表示预测误差较小，负值表示较大；单位是基点平方，不能解释为投资收益。"},
            {"label": "主权重", "definition": "每事件、每预测日内对可共同评分的成员等权，再在事件内日期等权，最后事件等权。缺失保留为未知。"},
            {"label": "重叠口径", "definition": "同一成员同一预测日在不同事件中的误差先等权，再日期内成员等权、日期等权；不平均预测后再算损失。"},
            {"label": "零预测", "definition": "恒定预测次日收益为零，仅作同一可评分人口中的描述比较。不是额外拟合或交易策略。"}],
        "caveats": [
            "本轮是零账户诊断，无交易收益、成交成本或稳定盈利认证。即使误差改善，也不能直接转成买卖信号。",
            "保留全部父形成记录；已形成事件使用固定20成员和五个预测槽，不先挑后来突破、买入或盈利者。群体和事件本身仍经过历史强势与相关性选择。",
            "每个成员两模型共用60个完整训练对，最后标签截止F；F比事件形成A早10个源交易日。固定正则化和标准化参数，之后不调参。",
            "主图仅采用预先固定的事件等权口径；来源中同时给出重叠敏感性和零预测误差，不择优呈现。",
            "分期按预测日，不按标签实现日；跨阶段事件可能进入两片。各片重新计算权重，均值和事件数不可直接相加。",
            "重复成员、同期事件和相邻预测日相关；事件数和预测槽数不等于独立样本量。2022—2025均为已反复研究的开发历史，2026未参与。",
            "原始价格、历史状态和本地固定样本限制仍在。不能据此识别主力、散户、量化身份或证明因果带动。",
            "岭回归增加相关列会改变惩罚分摊，重复已有信息也可能改变预测。因此误差改善只表示这套固定模型发生变化，不能单独证明原领涨股提供独立信息。"],
        "evidenceFlow": [
            {"kind": "calculation", "title": "固定条件预测", "body": "先冻结全部规则与代码，完成六个源群体的两模型拟合与逐槽预测。未知模型、当前特征、未来标签和截止越界分别保留。"},
            {"kind": "validation", "title": "独立重建", "body": "用独立收益构造与线性方程求解复算训练、预测和误差；另以显式行权重核对所有聚合及预设阶段。"}]}
    rows = []
    for row in report["primaryRows"]:
        value = row["primary"]["improvementMSEBpsSquared"]
        assert value is None or math.isfinite(value)
        rows.append({"板块": BOARDS[row["board"]], "时期": row["period"].replace("-", "—"),
                     "均方误差改善（基点平方）": value, "无差异基准": 0})
    assert len(rows) == 6
    title = "加入原领涨股信息，次日预测误差是否减小？"
    chart = {"schemaVersion": 1, "id": "leader-incremental-prediction", "title": title, "height": 390,
        "theme": "codex-classic", "chart": {"type": "horizontalBar", "x": "板块", "y": "均方误差改善（基点平方）",
            "series": "时期", "stackable": False, "startAtZero": True, "showLegend": True, "showValues": False,
            "showXAxisLabel": True, "showYAxisLabel": False, "xLabel": "均方误差改善（基点平方）",
            "categoryOrder": list(BOARDS.values()), "seriesOrder": ["2022—2023", "2024—2025"],
            "annotations": [{"id": "zero-difference", "kind": "benchmark", "label": "0：误差相同", "field": "无差异基准", "measure": "2024—2025"}]},
        "rows": rows, "columns": list(rows[0]), "source": source}

    def format_row(row):
        c, p, overlap = row["coverage"], row["primary"], row["overlap"]
        return {"时期": row["period"], "板块": BOARDS[row["board"]], "区间": row.get("title", row["id"]),
            "计划槽": c["plannedSlots"], "共同评分槽": c["scoredSlots"], "可评分事件": c["scoredEventCount"],
            "事件数": c["eventCount"], "唯一成员预测日": c["uniqueMemberDateCount"],
            "共同模型MSE": p["baselineMSEBpsSquared"], "加入领涨MSE": p["augmentedMSEBpsSquared"],
            "零预测MSE": p["zeroMSEBpsSquared"], "事件等权MSE改善": p["improvementMSEBpsSquared"],
            "重叠口径MSE改善": overlap["improvementMSEBpsSquared"],
            "共同模型MAE": p["baselineMAEBps"], "加入领涨MAE": p["augmentedMAEBps"], "零预测MAE": p["zeroMAEBps"],
            "事件等权MAE改善": p["improvementMAEBps"], "重叠口径MAE改善": overlap["improvementMAEBps"]}

    queries = []
    for key, label in (("primaryRows", "六个完整群体"), ("yearRows", "年度预测"), ("halfYearRows", "半年预测"),
                       ("phaseRows", "固定市场阶段"), ("epochRows", "9·24前后预测")):
        values = [format_row(row) for row in report[key]]
        queries.append({"id": key, "source": {**source, "label": label}, "rows": values, "columns": list(values[0]),
                        "reportingPeriod": "2022—2025", "preview": {"kind": "aggregate", "note": "MSE单位为基点平方，MAE单位为基点。全部预设切片保留。", "totalRows": len(values)}})
    coverage = []
    for row in report["coverageRows"]:
        c = row["coverage"]
        coverage.append({"时期": row["period"], "板块": BOARDS[row["board"]], "来源事件": row["sourceFormationCount"],
            "来源事件状态": json.dumps(row["formationStatusCounts"], ensure_ascii=False), "形成事件": row["formedEvents"],
            "成员模型对": row["memberEpisodes"], "计划槽": row["designedSlots"], "共同评分槽": c["scoredSlots"],
            "未评分槽": c["unscoredSlots"], "日期不可归属槽": c["unassignedForecastDateSlots"],
            "模型状态": json.dumps(row["fitCounts"], ensure_ascii=False),
            "评分状态": json.dumps(c["scoreStatusCounts"], ensure_ascii=False)})
    queries.append({"id": "coverage", "source": {**source, "label": "全部来源及未知分母"}, "rows": coverage,
                    "columns": list(coverage[0]), "reportingPeriod": "2022—2025",
                    "preview": {"kind": "aggregate", "note": "原始事件、模型和全部设计槽均保留。", "totalRows": len(coverage)}})
    assert [len(q["rows"]) for q in queries] == [6, 12, 24, 48, 6, 6]
    return {"comparisonChart": chart, "comparisonSources": {"schemaVersion": 1, "items": [{"id": "prediction-results", "title": title, "queries": queries}]}}


def main():
    names = ("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json")
    pins = {name: sha(OUT / name) for name in names}
    proof = read(OUT / "readout-validation.json")
    assert proof["passed"] and proof["planSha256"] == pins["plan.json"] and proof["readoutSha256"] == pins["readout.json"]
    assert proof["completionSha256"] == pins["completion.json"] and proof["sourceValidationSha256"] == pins["validation.json"]
    values = payloads(read(OUT / "readout.json"))
    directory = OUT / "inline"
    directory.mkdir(exist_ok=False)
    payload_refs, fragments = {}, {}
    for key, stem in (("comparisonChart", "leader-prediction-chart"), ("comparisonSources", "leader-prediction-sources")):
        payload, fragment = OUT / (stem + ".json"), directory / (stem + ".html")
        write(payload, values[key])
        subprocess.run([str(NODE), str(HELPERS[key]), "--input", str(payload), "--output", str(fragment)], check=True)
        assert fragment.stat().st_size < 1_000_000
        payload_refs[key] = {"path": payload.relative_to(OUT).as_posix(), "sha256": sha(payload)}
        fragments[key] = {"path": fragment.relative_to(OUT).as_posix(), "sha256": sha(fragment)}
    assert all(sha(OUT / name) == expected for name, expected in pins.items())
    receipt = {"passed": True, "createdAt": datetime.now(timezone.utc).isoformat(), "builderSha256": sha(__file__),
        "inputPins": pins, "presentationHelpers": [{"path": str(path), "sha256": sha(path)} for path in HELPERS.values()],
        "payloads": payload_refs, "fragments": fragments, "deliveryOrder": ["comparisonChart", "comparisonSources"],
        "sourceRows": 102, "meaning": "Prediction error diagnostic only; no portfolio return."}
    write(OUT / "render-receipt.json", receipt)
    print(json.dumps({"passed": True, "renderReceiptSha256": sha(OUT / "render-receipt.json")}))


if __name__ == "__main__":
    main()
