"""Close out the fixed cap experiment without promoting it to a trading system."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/reclaim-entry-volatility-cap-v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    proof, readout = read(OUT / "validation.json"), read(OUT / "readout.json")
    if not proof["passed"] or proof["readout"] != ref(OUT / "readout.json"):
        raise ValueError("Bound completed account audit required")
    rows = readout["rows"]
    if len(rows) != 12 or sum(r["rowRole"] == "newAccount" for r in rows) != 6:
        raise ValueError("Six new accounts and six exact cached controls required")
    paired = []
    for row in rows:
        if row["rowRole"] != "newAccount":
            continue
        base = next(r for r in rows if r["board"] == row["board"] and r["policy"] == row["basePolicy"])
        paired.append({"board": row["board"], "policy": row["policy"], "control": row["basePolicy"],
            **{k + "Delta": None if row[k] is None or base[k] is None else row[k] - base[k]
               for k in ("meanLossReturnPct", "netReturnPct", "maxDrawdownPct", "profitFactor", "payoffRatio", "closed", "averageExposurePct")}})
    write(OUT / "paired-comparisons.json", {"rows": paired, "newAccounts": 0,
        "meaning": "Differences between complete continuous accounts, not a conditional subset of old trades.",
        "noCausalAttributionOfIndividualRejectedTrades": True})
    code = [
        "from pathlib import Path\nimport json,hashlib\nroot=Path.cwd().resolve()\nwhile not (root/'service').is_dir() and root.parent!=root:root=root.parent\nstudy=root/'artifacts/strategy-search/reclaim-entry-volatility-cap-v1'\ndef load(p):return json.loads(p.read_text(encoding='utf-8-sig'))\nproof=load(study/'validation.json');result=load(study/'readout.json')\nassert proof['passed']\nassert hashlib.sha256((study/'readout.json').read_bytes()).hexdigest()==proof['readout']['sha256']\nrows=result['rows'];assert len(rows)==12 and sum(r['rowRole']=='newAccount' for r in rows)==6\nprint('6 new accounts;6 reused controls;1 fixed candidate trial.')\n",
        "keys=['board','policy','rowRole','closed','payoffRatio','profitFactor','meanLossReturnPct','netReturnPct','maxDrawdownPct','boardExcessPercentagePoints','valuationUnreliableDays']\nfor r in rows:print(json.dumps({k:r[k] for k in keys}))\n",
        "paired=load(study/'paired-comparisons.json')['rows'];assert len(paired)==6\nprint(json.dumps(paired,indent=2))\nprint('Whole accounts; no annual reset or saved-winner backfill.')\n",
        "for r in rows:\n if r['rowRole']=='newAccount':print(r['board'],r['policy'],json.dumps([p for p in r['periods'] if p['id'] in ['2024','2025','2026']]))\nprint('2024 startsSep24;2026 endsOct15. Equity carries; closed episodes assigned by exit date.')\ninputs=load(study/'input-validation.json')\nprint(json.dumps([{'board':g['board'],**g['counts']} for g in inputs['groups']],indent=2))\n",
    ]
    cells = [{"cell_type": "markdown", "id": "cap-context", "metadata": {}, "source": [
        "# 入场前波动上限：固定假设归档\n",
        "2024-09-24至2026-10-15，6个新连续账户与6个复用对照。5%是20日归一真实波幅均值上限，不保证实际亏损小于5%。\n",
        "上市板分组不等于行业或题材。本实验只研究一个组件，不构成市场—行业—个股轮动体系。\n",
        "原始价模型未认证，历史已被观察。标准notebook代码按顺序在CPython执行，非Jupyter内核记录。\n"]}]
    namespace, executions = {"__name__": "__notebook__"}, []
    for index, source in enumerate(code, 1):
        stream = io.StringIO()
        with redirect_stdout(stream):
            exec(compile(source, str(OUT / "audit.ipynb"), "exec"), namespace)
        cells.append({"cell_type": "code", "id": f"cap-{index}", "metadata": {}, "source": source.splitlines(keepends=True),
            "execution_count": index, "outputs": [{"output_type": "stream", "name": "stdout", "text": stream.getvalue().splitlines(keepends=True)}]})
        executions.append({"cell": index, "outputCharacters": len(stream.getvalue())})
    write(OUT / "audit.ipynb", {"nbformat": 4, "nbformat_minor": 5, "cells": cells, "metadata": {
        "kernelspec": {"name": "python3", "display_name": "Python3", "language": "python"},
        "language_info": {"name": "python", "version": sys.version.split()[0]}, "executionMode": "sequential CPython;notJupyterKernel"}})
    boards = {"main": "主板样本", "chinext": "创业板样本", "star": "科创板样本"}
    labels = {"liquid": "成交额条件", "liquidTrend": "市场量价同向", "liquidNtr5": "成交额条件+波动上限", "liquidTrendNtr5": "市场量价同向+波动上限"}
    table = [{"股票池": boards[r["board"]], "准入": labels[r["policy"]], "证据": "本次新账户" if r["rowRole"] == "newAccount" else "复用旧对照",
        "闭仓数": r["closed"], "盈亏比": r["payoffRatio"], "PF": r["profitFactor"], "平均盈利%": r["meanWinReturnPct"],
        "平均亏损%": r["meanLossReturnPct"], "账户收益%": r["netReturnPct"], "最大回撤%": r["maxDrawdownPct"],
        "超基准百分点": r["boardExcessPercentagePoints"], "平均仓位%": r["averageExposurePct"], "估值异常天数": r["valuationUnreliableDays"]} for r in rows]
    files = ["docs/reclaim-entry-volatility-cap-v1.json", *[ref(OUT / n)["path"] for n in (
        "plan.json", "input-validation.json", "readout.json", "validation.json", "paired-comparisons.json", "audit.ipynb")]]
    source = {"label": "55批固定入场波动假设与完整账户复核", "files": files,
        "metricDefinitions": [
            {"label": "盈亏比与PF", "definition": "完全闭仓扣费后的平均盈利百分比/平均亏损百分比绝对值；PF为已实现盈利金额合计/亏损金额合计绝对值。"},
            {"label": "波动上限", "definition": "T收盘前已知的21根完整日线、20个归一真实波幅项的算术均值不超过5%，下一交易日尝试买入。固定原Top5不补位，市场准入和退出模型继承对照。"},
            {"label": "账户收益", "definition": "相同资金、费用、持仓约束和连续日期，权益无年度重置。超额为同日期价格基准的累计收益百分点差。"}],
        "caveats": ["6个对照是已完成旧账户，未重跑，不计为新增独立证据。",
            "上市板分组不是行业板块筛选，单个组件有效也不能证明整套体系有效。",
            "历史已被观察，原始价模型未认证且保留估值异常标记。截止2026-10-15，缺2026-10-16。",
            "数值筛查是研究提案，未经用户确认；5%波动上限不是5%实际亏损保证。"],
        "evidenceFlow": [{"kind": "validation", "title": "执行前", "body": "78项检查通过，所有原始机会的入场前波动由独立窗口算法重建，6份新日程在执行前冻结。"},
            {"kind": "calculation", "title": "执行后", "body": "逐持仓日独立复核风险阈值，独立计算交易及权益指标、核对现金费用；6个旧对照与新账户的来源、费用和引擎身份一致。"}]}
    write(OUT / "sources.json", {"schemaVersion": 1, "items": [{"id": "entry-volatility-closeout", "title": "入场波动上限的完整账户对照", "queries": [{
        "id": "six-new-six-reused", "source": source, "rows": table, "columns": list(table[0]), "reportingPeriod": "2024-09-24至2026-10-15",
        "preview": {"kind": "aggregate", "note": "保留全部6个新账户和6个复用对照，结果用于归档组件假设。", "totalRows": 12}}]}]})
    write(OUT / "presentation.json", {"completedAt": datetime.now(timezone.utc).isoformat(), "builder": ref(Path(__file__)),
        "validation": ref(OUT / "validation.json"), "readout": ref(OUT / "readout.json"), "paired": ref(OUT / "paired-comparisons.json"),
        "notebook": ref(OUT / "audit.ipynb"), "sequentialCPythonCellsExecuted": executions, "sources": ref(OUT / "sources.json"),
        "newAccounts": 0, "newStrategyTrials": 0})
    print(json.dumps({"presentationComplete": True, "sourceRows": len(table), "executedCells": len(executions)}))


if __name__ == "__main__":
    main()
