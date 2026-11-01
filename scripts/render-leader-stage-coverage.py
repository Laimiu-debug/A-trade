"""Render the reviewed coverage tables as an inline Sources receipt."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-stage-coverage-v1"
NODE = Path("C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
HELPER = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs")
BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板"}
FIELDS = {"meanStrengthPercentile20": "强度均值", "minimumStrengthPercentile20": "强度下限",
          "relativeReturn60Percentile": "长期排名", "closePathMaxDrawdown20": "路径回撤",
          "signedPathEfficiency20": "路径效率", "breakout": "未突破", "unknown": "未知", "passed": "通过"}
STATES = {"passed": "通过", "failed": "未通过", "unknown": "未知"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def ref(path):
    return {"path": Path(path).relative_to(ROOT).as_posix(), "sha256": sha(path)}


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def payload(report):
    source = {"label": "龙头事件的阶段覆盖与原筛选条件",
        "files": [(OUT / name).relative_to(ROOT).as_posix() for name in
                  ("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json")],
        "metricDefinitions": [
            {"label": "原候选门槛", "definition": "当天五项强势条件和突破共同通过，才检查十个源交易日前的五项条件；随后按原排序取最多五个领涨股。"},
            {"label": "形成与预测", "definition": "原领涨事件再经过同伴群体完整性和重复锚股锁定，形成事件才能创建20成员乘5个预测槽；后续成交额或成员突破不影响这层分母。"},
            {"label": "首失败", "definition": "先计未知，再按原五条件固定顺序归入首个失败条件，最后判断当天突破。它是互斥的程序筛选分解，不是市场因果重要性。"},
            {"label": "相对强度前20", "definition": "只按当天已知的20日相对沪深300收益排序，保留五项条件未通过者，不用后来的涨幅选择展示名单。"}],
        "caveats": [
            "全部六个固定板块样本和完整源日期均保留；这不是完整历史全A股覆盖，也不识别主力、散户或量化资金。",
            "环境按原formationDate连接，实际数值截至前一源交易日；来源表明确列出截至日，不把它称作当天收盘环境。",
            "事件形成日期与预测日期是两套口径。跨阶段预测和日期为空的期末设计槽分别保留，不能把所有计数串成同一个漏斗。",
            "完整逐股条件只在预先固定的2024-09-24至10-08窗口重建；其他阶段复用原保存证据，不虚构未触发股票的失败细项。",
            "2022—2025均为已使用的开发历史。没有新交易账户、收益评估或稳定盈利认证，也没有因此放宽任何条件。"],
        "evidenceFlow": [
            {"kind": "calculation", "title": "完整日期与筛选链", "body": "按冻结计划连接原逐日候选、领涨事件、锁定和形成状态，以及五天预测设计槽；零事件日仍计入目标日期。"},
            {"kind": "validation", "title": "独立重建与汇总", "body": "焦点窗口的全板历史排名及逐股条件由独立函数重建；另以不同聚合路径复核全部固定阶段、时点和分母。"}]}
    queries = []

    def add(identity, label, rows, columns, note):
        queries.append({"id": identity, "source": {**source, "label": label}, "rows": rows, "columns": columns,
            "reportingPeriod": "2022—2025；逐股条件焦点2024-09-24至10-08",
            "preview": {"kind": "aggregate", "note": note, "totalRows": len(rows)}})

    def segment(row):
        a, p = row["leaderAdmission"], row["predictionByForecast"]
        return {"时期": row["period"], "板块": BOARDS[row["board"]], "阶段": row["id"],
            "日期数": row["targetDays"], "股票日数": a["symbolDayCount"], "当天候选": a["currentCandidateCount"],
            "十日前通过": a["formationCounts"]["passed"], "十日前失败": a["formationCounts"]["failed"],
            "十日前未知": a["formationCounts"]["unknown"], "领涨事件": a["leaderEventCount"],
            "可形成事件": a["formation43StatusCounts"]["formed"], "锁定事件": a["formation43StatusCounts"]["locked"],
            "群体未形成": a["formation43StatusCounts"]["notFormed"], "预测日计划槽": p["plannedSlots"],
            "可评分槽": p["scoredSlots"], "预测日期为空": p["unassignedForecastSlots"]}
    for key, identity, label in (("overallRows", "overall-rows", "六个完整群体"),
                                  ("phaseRows", "phase-rows", "全部固定市场阶段"),
                                  ("epochRows", "epoch-rows", "9·24前后")):
        values = [segment(r) for r in report[key]]
        add(identity, label, values, list(values[0]), "事件计数按形成日；预测槽按预测日。不同分母不能直接相减。")
    daily, current, past, top = [], [], [], []
    for row in report["focusRows"]:
        identity = {"时期": row["period"], "板块": BOARDS[row["board"]], "日期": row["formationDate"]}
        a, e = row["leaderAdmission"], row["environment"]
        daily.append({**identity, "环境截至日": row["environmentAsOfDate"], "固定股票数": row["fixedPopulation"],
            "当天候选": a["currentCandidateCount"], "十日前通过": a["formationCounts"]["passed"],
            "领涨事件": a["leaderEventCount"], "可形成事件": a["formation43StatusCounts"]["formed"],
            "预测计划槽": row["predictionByForecast"]["plannedSlots"],
            "此前20日上涨占比": e["priorClosePositiveReturn20Breadth"],
            "此前相对成交额": e["priorCloseRelativeAmount20"]})
        current.append({**identity, **{FIELDS[k]: v for k, v in row["firstFailureCounts"]["current"].items()}})
        past.append({**identity, **{FIELDS[k]: v for k, v in row["firstFailureCounts"]["formationEvaluatedByParent"].items()}})
        for rank, item in enumerate(row["top20"], 1):
            state, prior = item["currentEvidence"], item["formationEvidence"]
            top.append({**identity, "股票": item["symbol"], "当日名次": rank,
                "20日相对收益百分比": item["relativeReturn20"] * 100,
                "当天五条件": STATES[state["status"]], "十日前五条件": STATES[prior["status"]] if prior else "未知",
                "当天突破": "未知" if state["breakout"] is None else "是" if state["breakout"] else "否"})
    add("focus-days", "启动窗口完整日期", daily, list(daily[0]), "环境占比单位为0至1的小数；相对成交额单位为倍。它们截至前一源交易日。")
    add("current-first-failure", "当天条件首失败", current, list(current[0]), "全固定股票池按原顺序互斥归因；完整并发失败留在逐股原证据。")
    add("formation-first-failure", "十日前条件首失败", past, list(past[0]), "只统计原流程实际检查的当天候选，不把全部重建F状态当原筛选分母。")
    columns = list(top[0]) if top else ["时期", "板块", "日期", "股票", "当日名次", "20日相对收益百分比", "当天五条件", "十日前五条件", "当天突破"]
    add("current-rs20-top", "当时相对强度前20", top, columns, "全部预定焦点日与板块的前20展示；不足20不填补。此表不构成买入名单。")
    return {"schemaVersion": 1, "items": [{"id": "leader-stage-coverage", "title": "原龙头规则在哪一层遗漏市场阶段？", "queries": queries}]}


def main():
    names = ("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json")
    pins = {name: sha(OUT / name) for name in names}
    proof = read(OUT / "readout-validation.json")
    assert proof["passed"] and proof["planSha256"] == pins["plan.json"] and proof["readoutSha256"] == pins["readout.json"]
    assert proof["sourceValidationSha256"] == pins["validation.json"] and proof["completionSha256"] == pins["completion.json"]
    value = payload(read(OUT / "readout.json"))
    target = OUT / "stage-coverage-sources.json"
    write(target, value)
    directory = OUT / "inline"
    directory.mkdir(exist_ok=False)
    fragment = directory / "leader-stage-coverage-sources.html"
    result = subprocess.run([str(NODE), str(HELPER), "--input", str(target), "--output", str(fragment)],
                            capture_output=True, text=True, encoding="utf-8")
    write(OUT / "sources-render-log.json", {"exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    assert result.returncode == 0, result.stderr
    assert fragment.stat().st_size < 1_000_000 and all(sha(OUT / n) == s for n, s in pins.items())
    receipt = {"passed": True, "createdAt": datetime.now(timezone.utc).isoformat(), "builderSha256": sha(__file__),
        "inputPins": pins, "payload": ref(target), "fragment": ref(fragment),
        "presentationHelpers": [{"path": str(HELPER), "sha256": sha(HELPER)}],
        "sourceRows": sum(len(q["rows"]) for q in value["items"][0]["queries"]), "deliveryOrder": ["sources"]}
    write(OUT / "render-receipt.json", receipt)
    print(json.dumps({"passed": True, "renderReceiptSha256": sha(OUT / "render-receipt.json"), "sourceRows": receipt["sourceRows"]}))


if __name__ == "__main__":
    main()
