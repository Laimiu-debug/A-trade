"""Audit chronological prefix, execute a compact notebook and Sources receipt."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/reclaim-market-alignment-v1"


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig") as handle:
        return json.load(handle)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(item):
    path = (ROOT / item["path"]).resolve()
    if not path.is_relative_to(ROOT) or sha(path) != item["sha256"]:
        raise ValueError("Foreign or changed source")
    return path


def write(path, obj):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def same_rows(actual, expected, keys, label):
    if len(actual) != len(expected):
        raise ValueError(label + " count differs")
    for index, (a, b) in enumerate(zip(actual, expected)):
        for key in keys:
            x, y = a.get(key), b.get(key)
            numeric = type(x) in (int, float) and type(y) in (int, float)
            okay = abs(x - y) <= 1e-7 + 1e-11 * max(1., abs(x), abs(y)) if numeric else x == y
            if not okay:
                raise ValueError(f"{label} row{index} key{key} differs")


def prefix_audit(result):
    plan = read(OUT / "plan.json")
    previous_proof = read(bound(plan["bullValidation"]))
    if not previous_proof["passed"]:
        raise ValueError("Original bull account proof missing")
    previous = read(bound(previous_proof["readout"]))
    cutoff = "2025-12-31"
    curve_keys = ("date", "equity", "cash", "marketValue", "dailyReturn", "drawdownPct", "valuationReliable")
    trade_keys = ("date", "signalDate", "symbol", "side", "quantity", "price", "amount", "fee", "slippage", "cashAfter",
                  "realizedPnl", "reason", "quantityBefore", "quantityAfter", "positionAction", "unfilledQuantity", "signalId", "originalRank")
    close_keys = ("symbol", "entryDate", "exitDate", "pnlAmount", "investedAmount", "returnPct", "holdingDays")
    rows = []
    for board in ("main", "chinext", "star"):
        old = next(r for r in previous["rows"] if r["board"] == board and r["policy"] == "original")
        new = next(r for r in result["rows"] if r["board"] == board and r["policy"] == "original")
        a, b = read(bound(new["evidence"]["result"])), read(bound(old["evidence"]["result"]))
        for name, date_key, keys in (("curve", "date", curve_keys), ("trades", "date", trade_keys), ("closedTrades", "exitDate", close_keys)):
            same_rows([r for r in a[name] if r[date_key] <= cutoff], b[name], keys, board + ":" + name)
        rows.append({"board": board, "through": cutoff, "old": old["evidence"]["result"], "new": new["evidence"]["result"],
            "curveDays": len(b["curve"]), "fills": len(b["trades"]), "closedEpisodes": len(b["closedTrades"]), "economicPrefixEqual": True})
    value = {"schema": "reclaim-chronological-account-prefix-audit-v1", "passed": True, "completedAt": datetime.now(timezone.utc).isoformat(),
        "rows": rows, "curveKeys": curve_keys, "tradeKeys": trade_keys, "closedKeys": close_keys,
        "numericAbsoluteTolerance": 1e-7, "numericRelativeTolerance": 1e-11,
        "scope": "Appending2026 observations/opportunities preserves previously validated2024-2025 original-account economics. Fee quote identity hashes and future plans are not compared because source/config end differ.",
        "independentSecondExecutionEngine": False, "marketHoldoutClaim": False, "newAccounts": 0}
    write(OUT / "historical-prefix-validation.json", value)


def main():
    proof, result = read(OUT / "validation.json"), read(OUT / "readout.json")
    if not proof["passed"] or proof["readout"] != ref(OUT / "readout.json") or len(result["rows"]) != 9:
        raise ValueError("Completed independently audited9-account readout required")
    prefix_audit(result)
    rows = result["rows"]
    code = [
        "from pathlib import Path\nimport json,gzip,hashlib\nroot=Path.cwd().resolve()\nwhile not (root/'service').is_dir() and root.parent!=root: root=root.parent\nassert (root/'service').is_dir()\nstudy=root/'artifacts/strategy-search/reclaim-market-alignment-v1'\ndef load(p):\n with (gzip.open(p,'rt',encoding='utf-8') if str(p).endswith('.gz') else p.open(encoding='utf-8')) as f:return json.load(f)\nproof=load(study/'validation.json'); readout=load(study/'readout.json'); prefix=load(study/'historical-prefix-validation.json')\nassert proof['passed'] and prefix['passed']\nassert hashlib.sha256((study/'readout.json').read_bytes()).hexdigest()==proof['readout']['sha256']\nrows=readout['rows'];assert len(rows)==9\nprint('9 complete continuous accounts;3 historical economic prefixes preserved.')\n",
        "keys=['board','policy','closed','payoffRatio','profitFactor','meanLossReturnPct','netReturnPct','maxDrawdownPct','boardExcessPercentagePoints','valuationUnreliableDays']\nfor r in rows:print(json.dumps({k:r[k] for k in keys}))\n",
        "paired=[]\nfor board in ['main','chinext','star']:\n a=next(r for r in rows if r['board']==board and r['policy']=='liquid')\n b=next(r for r in rows if r['board']==board and r['policy']=='liquidTrend')\n paired.append({'board':board,'returnDeltaPercentagePoints':b['netReturnPct']-a['netReturnPct'],'drawdownDeltaPercentagePoints':b['maxDrawdownPct']-a['maxDrawdownPct'],'PFDelta':None if a['profitFactor'] is None or b['profitFactor'] is None else b['profitFactor']-a['profitFactor'],'exposureDeltaPercentagePoints':b['averageExposurePct']-a['averageExposurePct']})\nprint(json.dumps(paired,indent=2))\nprint('Whole-account comparison, not additive or conditional-episode PnL attribution.')\n",
        "for r in rows:\n if r['policy']=='liquidTrend':print(r['board'],json.dumps([p for p in r['periods'] if p['id'] in ['2024','2025','2026']],indent=2))\nprint('2024 observed from Sep24;2026 through Oct15. Equity carried; episodes assigned by exit date.')\n",
        "e=load(study/'main/signal-evidence.json.gz');counts={}\nfor d in e['daily']:\n g=counts.setdefault(d['date'][:4],{'sessions':0,'liquid':0,'aligned':0})\n g['sessions']+=1;g['liquid']+=int(d['liquidity']['status']=='passed');g['aligned']+=int(d['combinedStatus']=='passed')\nprint(json.dumps(counts,indent=2))\nprint('Admission only; existing holdings are not liquidated by a later gate failure.')\n",
    ]
    cells = [{"cell_type": "markdown", "metadata": {}, "id": "alignment-context", "source": [
        "# 强势股支撑收回与市场量价同向\n", "2024-09-24至2026-10-15连续账户，同一退出、仓位和成本模型。只改变新开仓条件。\n",
        "原始价未认证模型，历史已被观察；不宣称纯净样本外或稳定达标。按用户指示停止送转分红扩展研究。\n",
        "标准notebook代码按顺序在CPython执行；当前环境无nbformat/nbclient/IPython，非Jupyter内核记录。\n"]}]
    namespace, executions = {"__name__": "__notebook__"}, []
    for index, source in enumerate(code, 1):
        stream = io.StringIO()
        with redirect_stdout(stream):
            exec(compile(source, str(OUT / "audit.ipynb"), "exec"), namespace)
        cells.append({"cell_type": "code", "id": f"alignment-{index:02d}", "metadata": {}, "source": source.splitlines(keepends=True),
            "execution_count": index, "outputs": [{"output_type": "stream", "name": "stdout", "text": stream.getvalue().splitlines(keepends=True)}]})
        executions.append({"cell": index, "outputCharacters": len(stream.getvalue())})
    write(OUT / "audit.ipynb", {"nbformat": 4, "nbformat_minor": 5, "cells": cells,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python3", "language": "python"},
            "language_info": {"name": "python", "version": sys.version.split()[0]}, "executionMode": "sequential CPython;notJupyterKernel"}})
    boards = {"main": "主板", "chinext": "创业板", "star": "科创板"}
    labels = {"original": "原强势收回", "liquid": "强势收回+成交额", "liquidTrend": "强势收回+量价同向"}
    table = [{"板块": boards[r["board"]], "入场": labels[r["policy"]], "闭仓数": r["closed"], "盈亏比": r["payoffRatio"],
        "PF": r["profitFactor"], "平均盈利%": r["meanWinReturnPct"], "平均亏损%": r["meanLossReturnPct"],
        "账户收益%": r["netReturnPct"], "最大回撤%": r["maxDrawdownPct"], "超基准百分点": r["boardExcessPercentagePoints"],
        "估值异常天数": r["valuationUnreliableDays"]} for r in rows]
    source = {"label": "54批冻结规则、完整账户与独立复核", "files": [
        "docs/reclaim-market-alignment-v1.json", "artifacts/strategy-search/reclaim-market-alignment-v1/plan.json",
        "artifacts/strategy-search/reclaim-market-alignment-v1/input-validation.json", "artifacts/strategy-search/reclaim-market-alignment-v1/readout.json",
        "artifacts/strategy-search/reclaim-market-alignment-v1/validation.json", "artifacts/strategy-search/reclaim-market-alignment-v1/historical-prefix-validation.json",
        "artifacts/strategy-search/reclaim-market-alignment-v1/audit.ipynb"],
        "metricDefinitions": [{"label": "盈亏比与PF", "definition": "完全闭仓扣费后平均盈利百分比/平均亏损百分比绝对值；PF按实际盈亏金额求和。未平仓收益单列。"},
            {"label": "个股入场", "definition": "正的过去60日涨幅、同板块强度前20%，当日跌破过去20日最低价后收回；按过去20日成交额取原Top5，不补位。"},
            {"label": "市场量价", "definition": "当日沪深综指成交额代理合计至少1万亿元，5日均额至少为前20日均额80%；沪深两个综指的收盘价及20日均价均高于60日均价。仅限制新开仓。"},
            {"label": "账户与超额", "definition": "50万元、20%单次预算、最多5股、5%初始收盘止损及3倍过去20日归一真实波幅跟踪退出。连续账户无年度重置。超额为相同日期板块价格指数涨幅的百分点差，非风险调整alpha。"}],
        "caveats": ["原始价未认证模型，保留估值异常标记；按用户指示停止送转分红扩展研究。",
            "历史已被观察，不是纯净样本外；数据截止2026-10-15，缺2026-10-16交易日。",
            "高盈亏比不能单独证明达标；同时查看PF、亏损、回撤、年度表现及样本量。筛查数字不是用户确认的硬门槛。"],
        "evidenceFlow": [{"kind": "validation", "title": "执行前", "body": "115项检查通过，全4999个成员原入场日程及市场窗口独立重建，再冻结9个账户日程。"},
            {"kind": "calculation", "title": "执行后", "body": "逐持仓日复核退出阈值，独立计算交易与权益指标、核对现金费用；追加2026数据后，原账户2024-2025经济路径与旧验证结果一致。"}]}
    write(OUT / "sources.json", {"schemaVersion": 1, "items": [{"id": "reclaim-market-alignment", "title": "强势股收回：成交额与趋势同向的准入效果", "queries": [{
        "id": "all-nine-continuous-accounts", "source": source, "rows": table, "columns": list(table[0]), "reportingPeriod": "2024-09-24至2026-10-15",
        "preview": {"kind": "aggregate", "note": "三板块三政策全部保留；比较完整现金路径，不用旧交易子集冒充新策略。", "totalRows": 9}}]}]})
    write(OUT / "presentation.json", {"completedAt": datetime.now(timezone.utc).isoformat(), "builder": ref(Path(__file__)),
        "readout": ref(OUT / "readout.json"), "validation": ref(OUT / "validation.json"), "prefixAudit": ref(OUT / "historical-prefix-validation.json"),
        "notebook": ref(OUT / "audit.ipynb"), "sequentialCPythonCellsExecuted": executions, "sources": ref(OUT / "sources.json"),
        "newMarketAccounts": 0, "newStrategyTrials": 0})
    print(json.dumps({"presentationComplete": True, "prefixesPreserved": 3, "sourceRows": len(table), "executedCells": len(executions)}))


if __name__ == "__main__":
    main()
