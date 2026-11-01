"""Execute a small portable audit notebook and render reviewed evidence.

Notebook cells execute sequentially under CPython (not a Jupyter kernel).
This presentation helper cannot change inputs, signals, exits or accounts.
"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/liquid-trend-breakout-v1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell(kind, source):
    result = {"cell_type": kind, "id": uuid.uuid4().hex[:12], "metadata": {}, "source": source.splitlines(True)}
    if kind == "code":
        result.update(execution_count=None, outputs=[])
    return result


def main():
    proof = json.loads((OUT / "validation.json").read_text("utf-8"))
    if not proof["passed"] or proof["readout"]["sha256"] != digest(OUT / "readout.json"):
        raise ValueError("Validated readout required")
    benchmark_dir = OUT / "benchmarks"
    benchmark_dir.mkdir(exist_ok=False)
    benchmark_pins = []
    for market, symbol in (("sh", "sh000300"), ("sz", "sz399006"), ("sh", "sh000688")):
        source = Path("E:/TDX/vipdoc") / market / "lday" / (symbol + ".day")
        target = benchmark_dir / (symbol + ".day")
        target.write_bytes(source.read_bytes())
        benchmark_pins.append({"symbol": symbol, "path": target.relative_to(ROOT).as_posix(), "sha256": digest(target)})
    cells = [cell("markdown", "# 9·24 后的盈亏比与市场流动性\n\n"
                  "这是第 50 批开发实验的审阅伴随笔记。牛市考核从 2024-09-24 空仓开始，至 2025-12-31；熊市仅作压力对照。"
                  "主指标为完全平仓、扣费后的平均盈利百分比除以平均亏损百分比绝对值。PF 按实际金额统计。"
                  "沪深成交额使用通达信市场总量代理，经过可获取交易所记录抽核；还需完整官方数据复验。"
                  "3 倍盈亏比等数值为助手研究筛查提案，并非用户已确认门槛；所有历史均为已暴露开发数据。\n"),
             cell("markdown", "## 数据、计算与边界\n\n原始信号、输入核验、18 个账户和独立算术复核均可沿 readout 的 evidence 字段追溯。"
                  "本笔记从保存结果重新读取仓位与年度切片，补充读出中未投影的字段；不改动研究文件。"
                  "运行环境：Python 3、pandas、matplotlib、numpy；ROOT 指向仓库。\n"),
             cell("code", "import json, hashlib\nfrom pathlib import Path\nimport pandas as pd\nimport struct\n"
                  "pd.set_option('display.unicode.east_asian_width', True)\n"
                  "ROOT = Path(r'G:/CODE/A-trade')\nOUT = ROOT / 'artifacts/strategy-search/liquid-trend-breakout-v1'\n"
                  "readout = json.loads((OUT / 'readout.json').read_text('utf-8'))\n"
                  "validation = json.loads((OUT / 'validation.json').read_text('utf-8'))\n"
                  "assert validation['passed']\nassert hashlib.sha256((OUT/'readout.json').read_bytes()).hexdigest() == validation['readout']['sha256']\n"
                  "labels = {'breakout120':'120日首次突破','trendEarly':'趋势且未过热','trendEarlyLiquid':'趋势且未过热＋流动性'}\n"
                  "boards = {'main':'主板','chinext':'创业板','star':'科创板'}\nrows = []\n"
                  "benchmark_symbols = {'main':'sh000300','chinext':'sz399006','star':'sh000688'}\nbenchmarks = {}\n"
                  "for board,symbol in benchmark_symbols.items():\n"
                  "    values = {}\n"
                  "    for v in struct.iter_unpack('<IIIIIfII',(OUT/'benchmarks'/(symbol+'.day')).read_bytes()):\n"
                  "        d=str(v[0]); values[d[:4]+'-'+d[4:6]+'-'+d[6:]]=v[4]/100\n"
                  "    benchmarks[board]=values\n"
                  "for r in readout['rows']:\n"
                  "    s = json.loads((ROOT / r['evidence']['summary']['path']).read_text('utf-8'))\n"
                  "    assert not s['quality']['integrityIssues']\n"
                  "    prices=benchmarks[r['board']]\n"
                  "    benchmark_return=(prices[s['account']['cutoff']]/prices[s['account']['configuredStart']]-1)*100\n"
                  "    rows.append({'时期':r['period'],'板块':boards[r['board']],'信号':labels[r['policy']],"
                  "'盈亏比':r['payoffRatio'],'PF':r['profitFactor'],'平均盈利%':r['meanWinReturnPct'],"
                  "'平均亏损%':r['meanLossReturnPct'],'净收益%':r['netReturnPct'],'最大回撤%':r['maxDrawdownPct'],"
                  "'闭仓数':r['closed'],'平均仓位%':s['periods'][0]['exposurePct']['mean'],"
                  "'前五盈利占比%':r['top5ProfitSharePct'],'未平浮盈元':r['openUnrealizedPnl'],"
                  "'估值不可靠日':r['valuationUnreliableDays'],'板块基准收益%':benchmark_return,"
                  "'相对板块基准百分点':r['netReturnPct']-benchmark_return})\n"
                  "table = pd.DataFrame(rows)\nbull = table[table['时期']=='bull924'].copy()\n"
                  "print(bull[['板块','信号','盈亏比','PF','净收益%','最大回撤%','闭仓数','相对板块基准百分点']].round(2).to_string(index=False))\n"),
             cell("markdown", "## 牛市区间结果\n\n横向比较应在同一板块内完成。盈亏比改善必须与 PF、回撤、交易数和仓位一起判断。"
                  "图上的参考线只是预先登记的筛查提案。\n"),
             cell("code", "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport numpy as np\n"
                  "from matplotlib.font_manager import FontProperties\n"
                  "font = FontProperties(fname=r'C:/Windows/Fonts/msyh.ttc')\n"
                  "fig, axes = plt.subplots(2,2,figsize=(12,8))\naxes=axes.ravel()\n"
                  "metrics = [('盈亏比',3),('PF',1.5),('最大回撤%',20),('相对板块基准百分点',0)]\n"
                  "names = list(labels.values())\ncolors = ['#7f8c9b','#1874c3','#de8a21']\n"
                  "for ax,(metric,screen) in zip(axes,metrics):\n"
                  "    for k,(name,color) in enumerate(zip(names,colors)):\n"
                  "        values = [bull[(bull['板块']==b)&(bull['信号']==name)][metric].iloc[0] for b in boards.values()]\n"
                  "        ax.bar(np.arange(3)+(k-1)*.24,values,width=.23,label=name,color=color)\n"
                  "    ax.axhline(screen,color='#a52b38',linestyle='--',linewidth=1)\n"
                  "    ax.set_title(metric,fontproperties=font,fontsize=13)\n"
                  "    ax.set_xticks(np.arange(3),list(boards.values()),fontproperties=font)\n"
                  "    ax.grid(axis='y',alpha=.2)\n    ax.set_axisbelow(True)\n"
                  "axes[0].legend(prop=font,fontsize=9,loc='upper left')\n"
                  "fig.suptitle('2024-09-24—2025-12-31：费用后账户开发结果',fontproperties=font,fontsize=15)\n"
                  "fig.text(.02,.02,'固定原 Top5，不补位；市场成交额为代理；参考线为研究提案，尚非稳定盈利证明。',fontproperties=font,fontsize=10)\n"
                  "fig.tight_layout(rect=(0,.07,1,.93))\nfig.savefig(OUT/'bull-payoff.png',dpi=150)\n"
                  "print('Saved bull-payoff.png; three boards and all three entry policies are visible.')\n"),
             cell("markdown", "![牛市盈亏比与板块基准比较](bull-payoff.png)\n\n板块基准依次为沪深300、创业板指、科创50。"
                  "使用相同起止日收盘价格涨幅，未计指数分红和基准交易费用；不代表可直接成交的全板块组合。"
                  "相对收益是策略累计收益减基准涨幅的百分点差，不是风险调整 alpha。\n"),
             cell("markdown", "## 熊市压力与年度切片\n\n熊市不作为收益达标要求。年度结果来自同一牛市连续账户，不重新分配 50 万元，"
                  "也不把已有持仓浮盈当成新入场信号胜率。\n"),
             cell("code", "print(table[table['时期']=='bear2024'][['板块','信号','盈亏比','PF','净收益%','最大回撤%','闭仓数']].round(2).to_string(index=False))\n"
                  "print('\\n牛市连续账户的年度切片：')\n"
                  "for r in readout['rows']:\n"
                  "    if r['period'] != 'bull924': continue\n"
                  "    s=json.loads((ROOT/r['evidence']['summary']['path']).read_text('utf-8'))\n"
                  "    for p in s['periods']:\n"
                  "        if p['kind']=='calendarYear':\n"
                  "            print(boards[r['board']],labels[r['policy']],p['id'],round(p['returnPct'],2),p['closedEpisodes']['count'])\n"),
             cell("markdown", "## 如何继续\n\n"
                  "先判断流动性门控相对同口径对照的增量，再诊断实际亏损超出收盘止损阈值的原因。"
                  "如有候选，只能登记为开发候选；官方成交额、公司行动、历史股池、真实可成交性和未使用后续数据仍需验证。"
                  "不根据这张表自动选择最佳板块、重新调阈值或认定 Goal 已完成。\n")]
    state = {"__name__": "__notebook__"}
    count = 0
    for item in cells:
        if item["cell_type"] == "code":
            count += 1
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exec(compile("".join(item["source"]), f"audit-cell-{count}", "exec"), state)
            item["execution_count"] = count
            item["outputs"] = [{"output_type": "stream", "name": "stdout", "text": stdout.getvalue().splitlines(True)}]
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "version": "3.13"}, "execution": {"engine": "CPython sequential exec, not a Jupyter kernel", "completedAt": datetime.now(timezone.utc).isoformat()}}, "cells": cells}
    # Structural checks for raw JSON scaffolding when notebook libraries are absent.
    assert all(c["id"] and c["cell_type"] in ("code", "markdown") and isinstance(c["source"], list) for c in cells)
    assert [c["execution_count"] for c in cells if c["cell_type"] == "code"] == list(range(1, count + 1))
    with (OUT / "audit.ipynb").open("x", encoding="utf-8") as f:
        json.dump(notebook, f, ensure_ascii=False, indent=2)
    table = state["table"]
    supplementary = {"schema": "liquid-trend-breakout-presentation-v1", "readoutSha256": digest(OUT / "readout.json"),
                     "rows": json.loads(table.to_json(orient="records", force_ascii=False)), "notebookSha256": digest(OUT / "audit.ipynb"),
                     "plotSha256": digest(OUT / "bull-payoff.png"), "notebookCellsExecuted": count, "jupyterKernelUsed": False,
                     "benchmarkSources": benchmark_pins,
                     "presentationFieldsRecoveredFromOriginalSummaries": ["exposurePct.mean", "requestedStart", "requestedEnd"]}
    (OUT / "presentation.json").write_text(json.dumps(supplementary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    queries = [{"id": "bull-accounts", "source": {"label": "9·24 后独立起始的九个账户", "files": ["docs/liquid-trend-breakout-v1.json",
                "artifacts/strategy-search/liquid-trend-breakout-v1/plan.json", "artifacts/strategy-search/liquid-trend-breakout-v1/input-validation.json",
                "artifacts/strategy-search/liquid-trend-breakout-v1/readout.json", "artifacts/strategy-search/liquid-trend-breakout-v1/validation.json"],
                "metricDefinitions": [{"label": "盈亏比", "definition": "完全平仓、扣费后交易的平均盈利百分比除以平均亏损百分比绝对值。"},
                                      {"label": "PF", "definition": "完全平仓交易总盈利金额除以总亏损金额绝对值，保留实际资金大小差异。"},
                                      {"label": "板块基准", "definition": "主板沪深300、创业板创业板指、科创板科创50；相同首末收盘价格涨幅，未计分红或基准交易费。差值为累计收益百分点差，不是风险调整alpha。"},
                                      {"label": "账户", "definition": "各自50万元，从2024年9月24日空仓起始；收盘信号次开尝试；5%收盘止损、10%跟踪、每侧10基点滑点。净收益包含未平估值。"}],
                "caveats": ["全部为已暴露开发历史；未证明稳定盈利。固定存续样本、原始价格、历史身份和权益处理仍有局限。",
                            "市场总量为通达信沪深代理，交易所记录抽核存在小幅口径差异；官方总量复验尚未完成。",
                            "3倍盈亏比、PF1.5和20%回撤等为研究提案，并非用户确认门槛。熊市不要求达标。"],
                "evidenceFlow": [{"kind": "validation", "title": "账户前验证", "body": "61项生产和账户测试通过；18份信号日程先冻结，再由独立原始窗口实现对全股池重建核对。"},
                                 {"kind": "calculation", "title": "结果核算", "body": "18份结果按交易和逐日权益独立重算盈亏比、PF、收益和回撤；沿用已审计汇总器核对现金、费用与持仓桥。没有运行第二个独立成交引擎。"}]},
                "rows": json.loads(state["bull"].round(4).to_json(orient="records", force_ascii=False))}]
    queries[0].update(columns=["时期", "板块", "信号", "盈亏比", "PF", "平均盈利%", "平均亏损%", "净收益%", "最大回撤%",
                              "闭仓数", "平均仓位%", "前五盈利占比%", "未平浮盈元", "估值不可靠日", "板块基准收益%", "相对板块基准百分点"],
                      reportingPeriod="2024-09-24至2025-12-31，用户指定牛市，九个独立起始账户",
                      preview={"kind": "aggregate", "note": "三个板块和全部三个政策均保留，空值保持未知。", "totalRows": 9})
    source_payload = {"schemaVersion": 1, "items": [{"id": "liquid-trend-breakout", "title": "流动性是否改善牛市入场盈亏比？", "queries": queries}]}
    (OUT / "sources.json").write_text(json.dumps(source_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
