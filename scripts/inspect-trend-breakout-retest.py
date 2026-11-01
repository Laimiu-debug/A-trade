"""Small executed audit companion and native Sources receipt, no new trial."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/trend-breakout-retest-v1"


def read(path):
    return json.loads(path.read_text("utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def write(path, obj):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    proof, result = read(OUT / "validation.json"), read(OUT / "readout.json")
    if not proof["passed"] or proof["readout"] != ref(OUT / "readout.json") or len(result["rows"]) != 9:
        raise ValueError("Completed, independently audited9-account readout required")
    rows = result["rows"]
    # This environment has no nbformat/nbclient/IPython. Explicitly execute the
    # standard notebook's code cells in one CPython namespace and save streams.
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": ["# 连续牛市区间入场比较\n", "2024-09-24 至2026-10-15；三个固定板块、三种入场、同一退出规则。年度切片继承账户权益。\n", "这是未认证的原始价模型研究，历史已被观察；高盈亏比不等于高期望或超额收益。送转分红扩展研究已按用户要求停止。\n", "本文件代码按顺序在 CPython 执行，非 Jupyter 内核执行记录。\n"]},
        {"cell_type": "code", "metadata": {}, "source": [
            "from pathlib import Path\nimport json, hashlib, math\nroot=Path.cwd().resolve()\nwhile not (root/'service').is_dir() and root.parent!=root: root=root.parent\nassert (root/'service').is_dir()\nstudy=root/'artifacts/strategy-search/trend-breakout-retest-v1'\n",
            "def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))\ndef checksum(p): return hashlib.sha256(p.read_bytes()).hexdigest()\nproof=load(study/'validation.json'); readout=load(study/'readout.json')\nassert proof['passed'] and proof['readout']['sha256']==checksum(study/'readout.json')\nrows=readout['rows']; assert len(rows)==9\nprint('Source and independent-account proof bound:', len(rows), 'accounts')\n"]},
        {"cell_type": "code", "metadata": {}, "source": [
            "keys=['board','policy','closed','payoffRatio','profitFactor','meanLossReturnPct','netReturnPct','maxDrawdownPct','boardExcessPercentagePoints','valuationUnreliableDays']\nfor row in rows: print(json.dumps({k:row[k] for k in keys}, ensure_ascii=False))\n"]},
        {"cell_type": "code", "metadata": {}, "source": [
            "paired=[]\nfor board in ['main','chinext','star']:\n a=next(r for r in rows if r['board']==board and r['policy']=='immediateLiquid')\n b=next(r for r in rows if r['board']==board and r['policy']=='retestLiquid')\n paired.append({'board':board,'returnDeltaPercentagePoints':b['netReturnPct']-a['netReturnPct'],'drawdownDeltaPercentagePoints':b['maxDrawdownPct']-a['maxDrawdownPct'],'PFDelta':None if a['profitFactor'] is None or b['profitFactor'] is None else b['profitFactor']-a['profitFactor'],'exposureDeltaPercentagePoints':b['averageExposurePct']-a['averageExposurePct']})\nprint(json.dumps(paired,indent=2))\nprint('Whole-account comparisons; not additive or episode-subset causal attribution.')\n"]},
        {"cell_type": "code", "metadata": {}, "source": [
            "for row in rows:\n if row['policy']=='retestLiquid':\n  print(row['board'], json.dumps([p for p in row['periods'] if p['id'] in ['2024','2025','2026']],indent=2))\nprint('2024 observed from Sep24;2026 observed through Oct15. Equity carried; closed episodes assigned by exit date.')\n"]},
        {"cell_type": "code", "metadata": {}, "source": [
            "market=load(study/'market.json')['amountByDate']; days=sorted(market); liquidity={}\nfor i,d in enumerate(days):\n if not '2024-09-24'<=d<='2026-10-15': continue\n v=[market[x] for x in days[i-20:i+1]]; assert len(v)==21\n prior=math.fsum(v[:20])/20; recent=math.fsum(v[-5:])/5\n g=liquidity.setdefault(d[:4],{'sessions':0,'absoluteFloor':0,'moderateGate':0,'oldExpansionGate':0})\n g['sessions']+=1; g['absoluteFloor']+=int(v[-1]>=1e12); g['moderateGate']+=int(v[-1]>=1e12 and recent>=.8*prior); g['oldExpansionGate']+=int(v[-1]>=1e12 and recent>=prior)\nprint(json.dumps(liquidity,indent=2))\nprint('Turnover qualification is not evidence of positive expected stock-entry return.')\n"]},
    ]
    namespace, executions = {"__name__": "__notebook__"}, []
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        stream = io.StringIO()
        with redirect_stdout(stream):
            exec(compile("".join(cell["source"]), str(OUT / "audit.ipynb"), "exec"), namespace)
        cell.update(execution_count=len(executions) + 1, outputs=[{"output_type": "stream", "name": "stdout", "text": stream.getvalue().splitlines(keepends=True)}])
        executions.append({"executionCount": cell["execution_count"], "outputCharacters": len(stream.getvalue())})
    notebook = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": sys.version.split()[0]},
        "executionMode": "Sequential CPython execution; nbformat/nbclient/IPython unavailable; not a Jupyter kernel claim"}, "nbformat": 4, "nbformat_minor": 5}
    for i, cell in enumerate(cells):
        cell["id"] = f"retest-audit-{i:02d}"
        if cell["cell_type"] == "code" and len(cell["outputs"]) != 1:
            raise ValueError("Notebook execution output missing")
    write(OUT / "audit.ipynb", notebook)
    board_labels, policy_labels = {"main": "主板", "chinext": "创业板", "star": "科创板"}, {
        "immediate": "直接突破", "immediateLiquid": "直接突破+流动性", "retestLiquid": "首次回踩+流动性"}
    table = []
    for row in rows:
        table.append({"板块": board_labels[row["board"]], "入场": policy_labels[row["policy"]], "闭仓数": row["closed"],
            "盈亏比": row["payoffRatio"], "PF": row["profitFactor"], "平均盈利%": row["meanWinReturnPct"],
            "平均亏损%": row["meanLossReturnPct"], "账户收益%": row["netReturnPct"], "最大回撤%": row["maxDrawdownPct"],
            "超基准百分点": row["boardExcessPercentagePoints"], "估值异常天数": row["valuationUnreliableDays"]})
    source = {"label": "53批连续账户：冻结日程、完整成交与独立重建", "files": [
        "docs/trend-breakout-retest-v1.json", "artifacts/strategy-search/trend-breakout-retest-v1/plan.execution-v2.json",
        "artifacts/strategy-search/trend-breakout-retest-v1/input-validation.json", "artifacts/strategy-search/trend-breakout-retest-v1/readout.json",
        "artifacts/strategy-search/trend-breakout-retest-v1/validation.json", "artifacts/strategy-search/trend-breakout-retest-v1/audit.ipynb"],
        "metricDefinitions": [{"label": "盈亏比与PF", "definition": "盈亏比=已完全闭仓扣费后平均盈利百分比/平均亏损百分比绝对值；PF按实际盈亏金额求和。未平仓收益单列。"},
            {"label": "入场比较", "definition": "同一20%仓位、5%初始收盘止损及3倍过去20日归一真实波幅跟踪退出。首次回踩须在突破后3至20日、原突破线附近收复，且趋势一致。"},
            {"label": "流动性", "definition": "当日沪深综合指数成交额代理合计至少1万亿元；含当日5日均额至少为严格过去20日均额的80%。"},
            {"label": "账户和超额", "definition": "从9.24连续运行，无逐年资金重置；相对同日期沪深300/创业板指/科创50原始价格涨幅的百分点差，非风险调整alpha。"}],
        "caveats": ["已按用户要求停止送转分红扩展研究，保留原始价模型的估值异常标记。数值是未认证模型研究证据。",
            "历史已被观察，不是纯净样本外。数据止于2026-10-15，缺2026-10-16交易日。",
            "高盈亏比可能同时伴随低胜率、PF不足及高回撤；不能单凭盈亏比认定达标。数值筛查门槛尚未由用户确认。"],
        "evidenceFlow": [{"kind": "validation", "title": "执行前冻结", "body": "91项相关检查通过；所有4999个固定成员的突破、回踩及状态日程独立重建一致，然后执行9个账户。"},
            {"kind": "calculation", "title": "执行后独立核验", "body": "逐持仓日重建风险阈值和退出；独立从交易与权益曲线计算盈亏比、PF、收益和回撤，并核对现金/费用/盈亏桥。"}]}
    receipt = {"schemaVersion": 1, "items": [{"id": "continuous-entry-comparison", "title": "9·24后连续账户：盈亏比要与PF、收益和回撤一起看", "queries": [{
        "id": "all-nine-accounts", "source": source, "rows": table, "columns": list(table[0]), "reportingPeriod": "2024-09-24 至2026-10-15",
        "preview": {"kind": "aggregate", "note": "三板块三政策全部展示；初始资金50万元，费用及持仓规则一致。", "totalRows": 9}}]}]}
    write(OUT / "sources.json", receipt)
    write(OUT / "presentation.json", {"completedAt": datetime.now(timezone.utc).isoformat(), "builder": ref(Path(__file__)),
        "readout": ref(OUT / "readout.json"), "validation": ref(OUT / "validation.json"), "notebook": ref(OUT / "audit.ipynb"),
        "sequentialCPythonCellsExecuted": executions, "sources": ref(OUT / "sources.json"), "newAccounts": 0, "newStrategyTrials": 0})
    print(json.dumps({"presentationComplete": True, "sourceRows": len(table), "executedCells": len(executions)}))


if __name__ == "__main__":
    main()
