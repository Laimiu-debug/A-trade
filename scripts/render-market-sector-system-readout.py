"""Source-bound inline tables for the first complete system, without reruns."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "artifacts/strategy-search/market-sector-stock-accounts-v1/attempt-001"
OUT = STUDY / "presentation-001"
VIS = Path("C:/Users/25647/.codex/visualizations/2026/10/18/01a14cab-4be4-7482-8a1e-a5c2037db5f5")
RUNTIME = Path("C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
RENDERER = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs")
LABELS = {"completeSystem":"完整体系","withoutMarketAdmission":"取消市场入场限制","withoutSectorAdmission":"取消行业入场限制",
    "withoutCatchupEntry":"取消补涨入场","sectorLiquidRepresentative":"优选行业的流动性代表股"}
REASONS = {"stopLoss":"止损","systemMarketRisk":"市场风险退出","systemSectorRisk":"行业风险退出","systemStructuralFailure":"结构失效","volatilityTrail":"波动跟踪保护"}
PATTERNS = {"leaderBreakout":"领涨突破","leaderReclaim":"领涨回踩收复","catchup":"行业内补涨"}


def read(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p): return {"path":Path(p).relative_to(ROOT).as_posix(),"sha256":sha(p)}
def write(p,v):
    with Path(p).open("x",encoding="utf-8") as f:
        json.dump(v,f,ensure_ascii=False,allow_nan=False,indent=2);f.write("\n")
def bound(v):
    p=(ROOT/v["path"]).resolve()
    if not p.is_relative_to(ROOT) or sha(p)!=v["sha256"]: raise ValueError("Changed source: "+v["path"])
    return p


def build():
    validation=read(STUDY/"validation.json")
    if not validation["passed"] or validation["targetAchieved"]: raise ValueError("Expected audited failed first system")
    result=read(bound(validation["readout"]))
    bound(validation["fullAccountAudit"])
    diagroot=STUDY/"mechanism-diagnostic-001"
    proof=read(diagroot/"validation.json")
    bound(proof["source"]);bound(proof["episodes"])
    diag=read(bound(proof["readout"]))
    main=next(r for r in diag["rows"] if (r["period"],r["policy"])==("primary","completeSystem"))
    table=[]
    for r in result["rows"]:
        table.append({"account":LABELS[r["policy"]],"period":"主区间" if r["period"]=="primary" else "弱市压力对照",
            "closed":r["closed"],"returnPct":r["netReturnPct"],"ddPct":r["maxDrawdownPct"],"payoff":r["payoffRatio"],
            "pf":r["profitFactor"],"meanLossPct":r["meanLossReturnPct"],"excessPp":r["excessPercentagePoints"],
            "unreliableMarkDays":r["valuationUnreliableDays"]})
    source={"label":"统一来源的完整体系连续账户与逐笔复核","files":[{"label":str(p.relative_to(ROOT)).replace('\\','/')} for p in
        (STUDY/"plan.json",STUDY/"input-validation.json",STUDY/"readout.json",STUDY/"audit-001/validation.json",diagroot/"readout.json",diagroot/"validation.json")],
        "metricDefinitions":[{"label":"闭仓平均盈亏比","definition":"完全闭仓扣费盈利百分比的均值，除以亏损百分比均值的绝对值；持仓浮盈亏不进入该比值。"},
            {"label":"PF","definition":"完全闭仓盈利金额之和除以亏损金额之和的绝对值。"},
            {"label":"连续账户收益与回撤","definition":"同一50万元账户每日现金与持仓估值形成权益；不按年重置，期末持仓保留，费用与滑点已计入。"},
            {"label":"超额百分点","definition":"账户累计收益百分比减同区间沪深300累计涨幅24.2876%；不作风险调整或行业指数复制。"},
            {"label":"失效距离与波动","definition":"实际模型入场价至信号固定失效位的距离，除以信号日20日均归一真实波幅乘入场价。"}],
        "filters":["主区间2024-09-24至2026-10-15；弱市对照2024-04-08至2024-09-23","固定4999只来源股票；使用当日已公布行业归属","T收盘判断，下一交易日开盘尝试；不利用当日事后成交量扩大买入"],
        "caveats":["五个主区间账户全部亏损，第一版未实现小亏大赚目标。","弱市仅1笔闭仓，不能据此证明稳定防守。",
            "行业对照使用CAPCO行业名单；不能冒称完整历史主题板块指数。","原始价、当前幸存股票池与历史可交易性限制保留；历史已反复研究，不能视作未见样本。",
            "条件分组只描述实际闭仓，不是删除某类交易或更换退出后的策略收益。","本地截止2026-10-15，缺少2026-10-16；研究数值门槛尚未由用户确认。"],
        "evidenceFlow":[{"kind":"validation","title":"源数据与信号时序","detail":"独立重建611个决策日的市场、行业、领涨关系、排序和全部账户信号；六套信号先于账户执行固定。"},
            {"kind":"validation","title":"连续账户独立复核","detail":"六个账户均核对逐持仓日退出、现金、费用、开仓预算和完全闭仓盈亏；112份用户工作区JSON保持不变。"},
            {"kind":"calculation","title":"实际持仓路径诊断","detail":"725笔完全闭仓与首次持续退出指令、成交记录和卖出前已观察价格路径逐笔绑定；新增账户和策略试验均为零。"}]}
    def query(identity,title,rows,columns):
        return {"id":identity,"title":title,"source":source,"rows":rows,"columns":[{"field":k,"label":label} for k,label in columns],
            "reportingPeriod":"2024-09-24至2026-10-15；弱市对照单列2024-04-08至2024-09-23",
            "preview":{"kind":"aggregate","note":"固定账户完整列示；分组仅作描述","totalRows":len(rows)}}
    exitrows=[{"reason":REASONS[r["label"]],**{k:v for k,v in r.items() if k!="label"}} for r in main["exitReason"]]
    patternrows=[{"pattern":PATTERNS[r["label"]],**{k:v for k,v in r.items() if k!="label"}} for r in main["pattern"]]
    payload={"schemaVersion":1,"items":[{"id":"whole-market-sector-stock-system-v1","title":"第一版完整体系的验证结果","queries":[
        query("six-accounts","连续账户",table,[("account","账户规则"),("period","区间"),("closed","闭仓笔数"),("returnPct","收益 %"),("ddPct","最大回撤 %"),("payoff","平均盈亏比"),("pf","PF"),("meanLossPct","平均亏损 %"),("excessPp","超额百分点"),("unreliableMarkDays","估值不可靠日数")]),
        query("complete-exits","完整体系的实际退出分组",exitrows,[("reason","退出原因"),("closed","闭仓笔数"),("netPnl","净盈亏 元"),("meanWinReturnPct","平均盈利 %"),("meanLossReturnPct","平均亏损 %"),("medianHoldingSessions","持仓交易日中位"),("closedWithin5Sessions","5日内闭仓笔数"),("signalNtr20MedianPct","入场信号波动中位 %"),("initialFailureDistanceMedianNtr","失效距离 波动倍数中位")]),
        query("complete-patterns","完整体系的实际入场分组",patternrows,[("pattern","入场分支"),("closed","闭仓笔数"),("netPnl","净盈亏 元"),("meanWinReturnPct","平均盈利 %"),("meanLossReturnPct","平均亏损 %"),("medianHoldingSessions","持仓交易日中位"),("signalNtr20MedianPct","入场信号波动中位 %"),("initialFailureDistanceMedianNtr","失效距离 波动倍数中位")])]}]}
    if len(table)!=6 or sum(r["closed"] for r in main["exitReason"])!=122: raise ValueError("Presentation population mismatch")
    OUT.mkdir()
    write(OUT/"sources.json",payload)
    target=VIS/"whole-system-account-v1-sources.html"
    if target.exists(): raise ValueError("Preserve prior rendered result")
    ran=subprocess.run([str(RUNTIME),str(RENDERER),"--input",str(OUT/"sources.json"),"--output",str(target)],capture_output=True,text=True,encoding="utf-8",timeout=60)
    write(OUT/"render-log.json",{"exitCode":ran.returncode,"stdout":ran.stdout,"stderr":ran.stderr})
    if ran.returncode or not target.exists(): raise ValueError("Inline Sources renderer failed")
    write(OUT/"presentation.json",{"passed":True,"builder":ref(Path(__file__)),"sources":ref(OUT/"sources.json"),
        "validation":ref(STUDY/"validation.json"),"heldPathValidation":ref(diagroot/"validation.json"),"inlinePath":str(target),"inlineSha256":sha(target),
        "reviewedRows":14,"newAccounts":0,"newStrategyTrials":0,"targetAchieved":False,
        "conclusion":"完整体系及四个主区间对照全部亏损。首要待检验机制为入场结构与正常波动不匹配；不能从分组证明删除某分支或放宽退出能改善账户。"})
    print(json.dumps({"presentation":ref(OUT/"presentation.json"),"inlinePath":str(target),"rows":14},ensure_ascii=False))


if __name__=="__main__":build()
