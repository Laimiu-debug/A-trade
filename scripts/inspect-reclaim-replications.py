"""Read-only presentation of the validated bull and2026 reclaim replications.

The supporting notebook executes sequentially under CPython; a Jupyter kernel
is not installed in this environment. No source/result artifacts are changed.
"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/reclaim-replication-review-20261018"
STUDIES = ("bull-reclaim-liquidity-v1", "reclaim-2026-replication-v1")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell(kind, source):
    c = {"cell_type": kind, "id": uuid.uuid4().hex[:12], "metadata": {}, "source": source.splitlines(True)}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


def main():
    for name in STUDIES:
        folder = ROOT / "artifacts/strategy-search" / name
        proof = json.loads((folder / "validation.json").read_text("utf-8"))
        if not proof["passed"] or proof["readout"]["sha256"] != sha(folder / "readout.json"):
            raise ValueError("Validated immutable readout required: " + name)
    OUT.mkdir(exist_ok=False)
    cells = [cell("markdown", "# 支撑收回信号：牛市与2026年复验\n\n"
                  "目标是扣费后的已平仓盈亏比，并结合PF、回撤、资金占用和相对板块收益。"
                  "以2024-09-24为用户指定的主要分界，熊市不要求达到盈利目标。"
                  "两个实验都从50万元空仓起始，期末不强制卖出。"
                  "复核已找到未处理转增股导致的持仓隔离案例：以下收益和回撤仅为原始价模型诊断，不能据此认定真实经济收益或目标达成。"
                  "旧期与2026均为已暴露的历史数据；2026是后续区间复验，不是纯净样本外。\n"),
             cell("markdown", "## 冻结规则和数据\n\n"
                  "入场为原强股支撑收回：先看前60日正收益和板块内80%以上的相对排名，再看当日低点跌破此前20日支撑、收盘收回。"
                  "原Top5按此前20日均成交额排序，筛选后不补位。T收盘决策，次源交易日开盘尝试。"
                  "退出保留5%初始收盘止损和20日相对真实波幅均值乘3的只收紧跟踪线，未使用固定盈利上限。"
                  "流动性代理为通达信沪深市场成交额之和：水平条件是当日不低于1万亿元，扩张条件还要求近5日均额不低于此前20日均额。\n"),
             cell("code", "import json, hashlib\nfrom pathlib import Path\nimport pandas as pd\n"
                  "pd.set_option('display.unicode.east_asian_width', True)\n"
                  "ROOT=Path(r'G:/CODE/A-trade')\nOUT=ROOT/'artifacts/strategy-search/reclaim-replication-review-20261018'\n"
                  "studies=['bull-reclaim-liquidity-v1','reclaim-2026-replication-v1']\n"
                  "boards={'main':'主板','chinext':'创业板','star':'科创板'}\n"
                  "labels={'original':'原信号','liquid':'总量且扩张','levelOnly':'只要求总量','expanding':'总量且扩张'}\n"
                  "rows=[]\nreadouts={}\n"
                  "for name in studies:\n"
                  "    folder=ROOT/'artifacts/strategy-search'/name\n"
                  "    raw=(folder/'readout.json').read_bytes()\n"
                  "    proof=json.loads((folder/'validation.json').read_text('utf-8'))\n"
                  "    assert proof['passed'] and hashlib.sha256(raw).hexdigest()==proof['readout']['sha256']\n"
                  "    s=json.loads(raw);readouts[name]=s\n"
                  "    for r in s['rows']:\n"
                  "        rows.append({'时期':'牛市2024Q4—2025' if name==studies[0] else '2026至10月15日','板块':boards[r['board']],"
                  "'政策':labels[r['policy']],'盈亏比':r['payoffRatio'],'PF':r['profitFactor'],'平均盈利%':r['meanWinReturnPct'],"
                  "'平均亏损%':r['meanLossReturnPct'],'净收益%':r['netReturnPct'],'最大回撤%':r['maxDrawdownPct'],'闭仓数':r['closed'],"
                  "'平均仓位%':r['averageExposurePct'],'相对板块基准百分点':r['boardExcessPercentagePoints'],"
                  "'前五盈利占比%':r['top5ProfitSharePct'],'闭仓净盈亏元':r['closedNetPnl'],'未平浮盈元':r['openUnrealizedPnl'],"
                  "'估值不可靠日':r['valuationUnreliableDays'],'原始政策':r['policy']})\n"
                  "table=pd.DataFrame(rows)\n"
                  "for period in table['时期'].unique():\n"
                  "    print('\\n'+period)\n"
                  "    print(table[table['时期']==period][['板块','政策','盈亏比','PF','净收益%','最大回撤%','闭仓数','相对板块基准百分点']].round(2).to_string(index=False))\n"),
             cell("markdown", "## 所有板块和政策\n\n"
                  "下图完整保留每个板块与政策。最大回撤越低越好，其他三图需结合样本和仓位一起解释。"
                  "收益差为相同期首期末收盘的价格指数差：主板沪深300、创业板创业板指、科创板科创50，未计指数分红和基准交易费用；不是风险调整alpha。\n"),
             cell("code", "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport numpy as np\n"
                  "from matplotlib.font_manager import FontProperties\nfont=FontProperties(fname=r'C:/Windows/Fonts/msyh.ttc')\n"
                  "plot_files=[]\n"
                  "for i,period in enumerate(table['时期'].unique(),1):\n"
                  "    part=table[table['时期']==period]\n"
                  "    policies=list(part['政策'].drop_duplicates())\n"
                  "    fig,axes=plt.subplots(2,2,figsize=(12,8))\n"
                  "    for ax,metric in zip(axes.ravel(),['盈亏比','PF','最大回撤%','相对板块基准百分点']):\n"
                  "        for k,(policy,color) in enumerate(zip(policies,['#61788a','#c68a24','#186ead'])):\n"
                  "            values=[part[(part['板块']==b)&(part['政策']==policy)][metric].iloc[0] for b in boards.values()]\n"
                  "            ax.bar(np.arange(3)+(k-(len(policies)-1)/2)*.24,values,width=.23,label=policy,color=color)\n"
                  "        if metric=='PF': ax.axhline(1,color='#9c2934',linestyle='--',linewidth=1)\n"
                  "        if metric=='相对板块基准百分点': ax.axhline(0,color='#9c2934',linestyle='--',linewidth=1)\n"
                  "        ax.set_xticks(np.arange(3),list(boards.values()),fontproperties=font)\n"
                  "        ax.set_title(metric,fontproperties=font,fontsize=13)\n"
                  "        ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)\n"
                  "    axes.ravel()[0].legend(prop=font,loc='upper left')\n"
                  "    fig.suptitle(period+'：原始价模型诊断（权益未重建）',fontproperties=font,fontsize=15)\n"
                  "    fig.text(.02,.02,'市场成交额为代理；历史股池、权益和开盘成交证据仍不完整；高盈亏比不等于稳定盈利。',fontproperties=font,fontsize=10)\n"
                  "    fig.tight_layout(rect=(0,.07,1,.93))\n"
                  "    path=OUT/('study-'+str(i)+'.png');fig.savefig(path,dpi=150);plot_files.append(path)\n"
                  "    plt.close(fig)\n"
                  "print('Saved two complete comparison figures.')\n"),
             cell("markdown", "![牛市复验](study-1.png)\n\n![2026年复验](study-2.png)\n"),
             cell("markdown", "## 总成交额的水平\n\n绝对成交额用亿元/万亿元衡量市场活动规模，不是资金净流入。"
                  "下图使用同一冻结通达信指数金额字段，展示9·24前后和2026的总量背景；不能从总量较高直接推断某条入场信号有超额收益。\n"),
             cell("code", "import struct, math\n"
                  "plan52=json.loads((ROOT/'artifacts/strategy-search/reclaim-2026-replication-v1/plan.json').read_text('utf-8'))\n"
                  "amounts={}\n"
                  "for symbol in ['sh000001','sz399106']:\n"
                  "    item=plan52['indexInputs'][symbol];raw=(ROOT/item['path']).read_bytes()\n"
                  "    assert hashlib.sha256(raw).hexdigest()==item['sha256']\n"
                  "    amounts[symbol]={str(r[0])[:4]+'-'+str(r[0])[4:6]+'-'+str(r[0])[6:]:r[5] for r in struct.iter_unpack('<IIIIIfII',raw)}\n"
                  "assert set(amounts['sh000001'])==set(amounts['sz399106'])\n"
                  "days=sorted(amounts['sh000001'])\nvalues=[amounts['sh000001'][d]+amounts['sz399106'][d] for d in days]\n"
                  "volume=pd.DataFrame({'date':pd.to_datetime(days),'totalTrillion':[v/1e12 for v in values]})\n"
                  "volume['prior20Trillion']=[math.fsum(values[i-20:i])/20/1e12 if i>=20 else None for i in range(len(values))]\n"
                  "volume['expanding']=[i>=20 and values[i]>=1e12 and math.fsum(values[i-4:i+1])/5>=math.fsum(values[i-20:i])/20 for i in range(len(values))]\n"
                  "volume_rows=[]\n"
                  "for label,lo,hi in [('9·24前2024','2024-01-02','2024-09-23'),('9·24后2024','2024-09-24','2024-12-31'),('2025年','2025-01-01','2025-12-31'),('2026年至10月15日','2026-01-05','2026-10-15')]:\n"
                  "    part=volume[(volume['date']>=lo)&(volume['date']<=hi)]\n"
                  "    volume_rows.append({'时期':label,'交易日':len(part),'日总成交额中位数万亿元':part['totalTrillion'].median(),'不少于1万亿元天数':int((part['totalTrillion']>=1).sum()),'总量且扩张天数':int(part['expanding'].sum())})\n"
                  "volume_table=pd.DataFrame(volume_rows)\nprint(volume_table.round(3).to_string(index=False))\n"
                  "shown=volume[volume['date']>='2024-01-02']\nfig,ax=plt.subplots(figsize=(12,4.4))\n"
                  "ax.plot(shown['date'],shown['totalTrillion'],color='#91a4b6',linewidth=.9,label='日成交额代理')\n"
                  "ax.plot(shown['date'],shown['prior20Trillion'],color='#195f9a',linewidth=1.5,label='此前20交易日均额')\n"
                  "ax.axhline(1,color='#9c2934',linestyle='--',linewidth=1,label='本实验1万亿元门槛')\n"
                  "ax.axvline(pd.Timestamp('2024-09-24'),color='#49794e',linestyle='--',linewidth=1)\n"
                  "ax.set_title('沪深市场总成交额代理：2024—2026年10月15日',fontproperties=font,fontsize=14)\n"
                  "ax.set_ylabel('万亿元人民币',fontproperties=font)\nax.legend(prop=font,loc='upper left')\nax.grid(alpha=.18)\n"
                  "fig.tight_layout();fig.savefig(OUT/'market-turnover.png',dpi=150);plt.close(fig)\n"),
             cell("markdown", "![总成交额水平](market-turnover.png)\n"),
             cell("markdown", "## 已确认的权益数据缺口\n\n腾景科技和华峰测控的公司实施公告与被隔离日相符。"
                  "模型仍保留转增前数量，并把未解释的原始价格跳变送入风险和隔离逻辑，影响后续退出、现金和仓位。"
                  "逐日算术一致不等于经济收益正确。此处只作原因核对，没有手工修正账户或移除隔离。\n"),
             cell("code", "action_audit=json.loads((ROOT/'artifacts/strategy-search/reclaim-corporate-action-audit-v1/readout.json').read_text('utf-8'))\n"
                  "assert action_audit['priceDiscontinuityExplainedForTwoHoldings'] and not action_audit['economicFitnessForGoalProven']\n"
                  "for c in action_audit['cases']:\n"
                  "    print(c['name'],c['exDate'],'每股转增',c['actualShareIncrease'],'原始收盘变化%',round(c['rawCloseChangePct'],2),'按公告参考价变化%',round(c['closeVersusAnnouncementExReferencePct'],2))\n"
                  "print('未重建完整权益台账，未回放修正账户；零碎股分配、递延税和真实成交仍待验证。')\n"),
             cell("markdown", "## 期末浮盈、集中度与年度分段\n\n"
                  "闭仓PF只统计已全部平仓的实际净金额。账户收益还包含未平仓估值。"
                  "2026的水平政策与原政策在本段所有交易日都等价；重复执行并不增加独立市场样本。"
                  "保留估值不可靠日和大赢家集中度，不能只展示平均盈利/平均亏损这一种形状。\n"),
             cell("code", "print(table[['时期','板块','政策','平均盈利%','平均亏损%','平均仓位%','前五盈利占比%','闭仓净盈亏元','未平浮盈元','估值不可靠日']].round(2).to_string(index=False))\n"
                  "for name,s in readouts.items():\n"
                  "    print('\\n年度和半年切片：'+name)\n"
                  "    for r in s['rows']:\n"
                  "        if r['policy'] in ['levelOnly']:continue\n"
                  "        for p in r['periods']:\n"
                  "            if p['id'] in ['overall','2024-H1','2024-H2']:continue\n"
                  "            print(boards[r['board']],labels[r['policy']],p['id'],p['returnPct'],p['closed'],p['R'],p['PF'])\n"),
             cell("markdown", "## 验证范围\n\n"
                  "51批保留原归档信号，按研究区间截取并独立核对流动性门控；52批逐只核对历史原始前缀，按独立交易日历延长到2026，"
                  "并以不同实现重建每个交易日的全股池排名、形态和入场信号。两批逐持仓日重建波动跟踪退出，独立重算盈亏比/PF和逐日权益结果。"
                  "现金、持仓、费用采用已有审计汇总器核对，未运行第二个完全独立的成交引擎。"
                  "本地截止2026-10-15，较应有最近交易日2026-10-16缺1天。官方完整成交额、历史身份、权益及开盘队列证据仍待补齐。\n")]
    state={"__name__":"__notebook__"};count=0
    for c in cells:
        if c["cell_type"]!="code":continue
        count+=1;stdout=io.StringIO()
        with redirect_stdout(stdout):exec(compile("".join(c["source"]),f"replication-cell-{count}","exec"),state)
        c["execution_count"]=count;c["outputs"]=[{"output_type":"stream","name":"stdout","text":stdout.getvalue().splitlines(True)}]
    notebook={"nbformat":4,"nbformat_minor":5,"metadata":{"kernelspec":{"display_name":"Python3","language":"python","name":"python3"},
              "language_info":{"name":"python","version":"3.13"},"execution":{"engine":"CPython sequential exec; not a Jupyter kernel","completedAt":datetime.now(timezone.utc).isoformat()}},"cells":cells}
    assert all(isinstance(c["source"],list) and c["id"] for c in cells)
    with (OUT/"audit.ipynb").open("x",encoding="utf-8") as f:json.dump(notebook,f,ensure_ascii=False,indent=2)
    table=state["table"];review={"schema":"reclaim-replication-presentation-v1","rows":json.loads(table.to_json(orient="records",force_ascii=False)),
        "inputs":[{"study":n,"readoutSha256":sha(ROOT/"artifacts/strategy-search"/n/"readout.json"),"validationSha256":sha(ROOT/"artifacts/strategy-search"/n/"validation.json")} for n in STUDIES],
        "notebookSha256":sha(OUT/"audit.ipynb"),"builderSha256":sha(Path(__file__)),"executionCells":count,"jupyterKernelUsed":False}
    (OUT/"presentation.json").write_text(json.dumps(review,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    items=[]
    approved=["时期","板块","政策","盈亏比","PF","平均盈利%","平均亏损%","净收益%","最大回撤%","闭仓数","平均仓位%",
              "相对板块基准百分点","前五盈利占比%","闭仓净盈亏元","未平浮盈元","估值不可靠日"]
    for i,(name,period) in enumerate(zip(STUDIES,table['时期'].unique())):
        selected=table[table['时期']==period][approved]
        folder="artifacts/strategy-search/"+name
        source={"label":period+"的冻结原始价模型","files":[folder+"/plan.json",folder+"/input-validation.json",folder+"/readout.json",folder+"/validation.json"],
          "metricDefinitions":[{"label":"盈亏比","definition":"已全部平仓、扣费后交易的平均盈利百分比/平均亏损百分比绝对值。PF另按真实净盈亏金额计算。"},
            {"label":"入场与退出","definition":"原强股支撑收回Top5，不补位；收盘信号次开尝试，5%初始收盘止损和20日相对真实波幅均值乘3的只收紧跟踪。"},
            {"label":"相对收益","definition":"策略累计净收益减同首末收盘的沪深300/创业板指/科创50价格涨幅，单位百分点。未计指数分红和基准交易费，不是风险调整alpha。"}],
          "caveats":["已查实未处理转增股导致持仓隔离；相关账户数字只能作原始价模型诊断，不能作为经济收益或信号失效的证明。",
            "均为已暴露历史；2026是后续区间复验，不是纯净样本外。不能据此宣称稳定盈利或Goal达成。",
            "沪深成交额为通达信市场总量代理，完整官方复验未完成。历史身份、权益和开盘队列仍不完整，保留估值质量标记。",
            "2026全部187日超过1万亿元，水平政策与原政策等价；重复账户不是独立市场样本。"],
          "evidenceFlow":[{"kind":"validation","title":"执行前","body":"冻结完整机会日程和规则。51批独立核对原日程截取与流动性条件；52批逐只核对历史前缀、独立日历和全股池入场重建。"},
            {"kind":"calculation","title":"执行后","body":"逐持仓日独立重建退出；从闭仓和逐日权益重算盈亏比、PF、收益、回撤。已有审计汇总器核对现金、费用、持仓桥；未使用第二个独立成交引擎。"}]}
        query={"id":"accounts-"+str(i),"source":source,"rows":json.loads(selected.round(4).to_json(orient="records",force_ascii=False)),
               "columns":approved,"reportingPeriod":period,"preview":{"kind":"aggregate","note":"保留所有板块和全部政策，未知不补零。","totalRows":len(selected)}}
        items.append({"id":"reclaim-replication-"+str(i),"title":period+"：原始价模型诊断","queries":[query]})
    market_rows=state["volume_table"]
    market_columns=["时期","交易日","日总成交额中位数万亿元","不少于1万亿元天数","总量且扩张天数"]
    items.append({"id":"market-turnover-levels","title":"总成交额的历史水平","queries":[{"id":"volume-levels",
      "source":{"label":"冻结通达信沪深市场金额字段","files":["artifacts/strategy-search/reclaim-2026-replication-v1/plan.json"],
        "metricDefinitions":[{"label":"总量","definition":"sh000001与sz399106的日金额字段相加，人民币元除1万亿；区间按实际交易日计算日总额中位数。"},
          {"label":"扩张","definition":"当日总额不少于1万亿元，且近5交易日均额不低于此前20交易日均额；全部窗口按冻结原始日历，不填补缺失。"}],
        "caveats":["这是提供商市场总量代理，非认证官方全A总额，也非资金净流入。高成交额不自动构成入场超额收益。"]},
      "rows":json.loads(market_rows.round(5).to_json(orient="records",force_ascii=False)),"columns":market_columns,
      "reportingPeriod":"2024-01-02至2026-10-15，9·24为主要研究分界","preview":{"kind":"aggregate","totalRows":4,"note":"按实际交易日观察；2026截止10月15日。"}}]})
    audit=json.loads((ROOT/"artifacts/strategy-search/reclaim-corporate-action-audit-v1/readout.json").read_text('utf-8'))
    case_rows=[{"代码":c['symbol'],"公司":c['name'],"公告日期":c['publishDate'],"除权日":c['exDate'],"每股转增":float(c['actualShareIncrease']),
      "原始收盘变化%":c['rawCloseChangePct'],"按公告参考价变化%":c['closeVersusAnnouncementExReferencePct'],"模型股数":c['exDayModelShares']} for c in audit['cases']]
    items.insert(0,{"id":"verified-action-quality-gap","title":"先修复权益数据，再评价盈亏比","queries":[{"id":"issuer-action-cases",
      "source":{"label":"两份公司实施公告与冻结持仓","files":["artifacts/strategy-search/reclaim-corporate-action-audit-v1/readout.json"]+[c['issuerPdf']['path'] for c in audit['cases']],
        "metricDefinitions":[{"label":"价格变化","definition":"原始收盘变化按前一日原始收盘计算；另一列按公司公告的除权参考价公式并四舍五入至0.01元计算。两列都不是修正后的账户收益。"},
          {"label":"持仓","definition":"冻结模拟账户在登记日持有，除权后仍保留原股数，且进入价格断点隔离并留下待退出订单。"}],
        "caveats":["只解释两笔已定位持仓，不代表完整权益台账。不能把转增比例直接用于历史全部价格，不能手调期末权益或事后剔除股票。",
          "差异化分红的除权参考价比例与实际送转比例必须区分；零碎股份分配及递延税未重建。"],
        "evidenceFlow":[{"kind":"validation","title":"公司事实","body":"上交所公告接口核对证券代码、实施公告和披露日期；巨潮原始PDF文本及页面核对分派比例和生效日期。"},
          {"kind":"calculation","title":"模型影响","body":"定位同日原始价跳变、未增股数、隔离及持久待退出状态。没有执行修正账户或宣称新的盈亏比。"}]},
      "rows":case_rows,"columns":["代码","公司","公告日期","除权日","每股转增","原始收盘变化%","按公告参考价变化%","模型股数"],
      "reportingPeriod":"2026-05-29与2026-06-11两个已验证持仓案例","preview":{"kind":"aggregate","totalRows":2,"note":"仅两笔来源核对，未替代完整经济收益复验。"}}]})
    (OUT/"sources.json").write_text(json.dumps({"schemaVersion":1,"items":items},ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(table[["时期","板块","政策","盈亏比","PF","净收益%","最大回撤%","闭仓数","相对板块基准百分点"]].round(3).to_string(index=False))


if __name__=="__main__":main()
