"""Render independently verified persistence-admission comparisons."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-persistence-admission-v1"
NODE = Path("C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
HELPER = Path("C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs")
BOARDS = {"main":"主板","chinext":"创业板","star":"科创板"}
POLICIES = {"knownAll":"全部可判定候选","persistent":"历史强势通过","nonpersistent":"历史强势未通过"}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text("utf-8"))
def ref(path): return {"path":Path(path).relative_to(ROOT).as_posix(),"sha256":sha(path)}
def write(path,value):
    with path.open("x",encoding="utf-8",newline="\n") as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write("\n")

def payload(report):
    source = {"label":"统一候选队列的历史强势门槛对照",
        "files":[(OUT/n).relative_to(ROOT).as_posix() for n in ("plan.json","input-validation.json","comparison.json","validation.json","readout.json","readout-validation.json")],
        "metricDefinitions":[
            {"label":"共同候选","definition":"当天强势且突破的完整候选按原强度排序取前五，再按十个源交易日前的强势状态分组；未知占位、不补位。"},
            {"label":"三组账户","definition":"全部可判定、历史强势通过、历史强势未通过分别独立使用50万元资金，持仓最多五只，初始单股预算20%。"},
            {"label":"账户收益","definition":"累计净收益和最大回撤来自连续账户权益，包含费用及期末未平仓估值；百分比单位均为%。"},
            {"label":"闭仓盈亏","definition":"平均盈利、平均亏损及期望收益只统计已完全平仓的交易，平均亏损保留负号；未平仓盈亏单列。"},
            {"label":"退出","definition":"沿用5%初始收盘止损与10%收盘跟踪止损，下一可成交时点执行，没有固定止盈或持有期限。"}
        ],
        "caveats":[
            "全部2022—2025数据已经反复用于开发；这18个新执行账户不是独立样本外盈利证明。",
            "共同前五先于历史条件筛选，与旧策略先筛历史条件再排名不同，不能称为旧策略的原样复跑。",
            "当天规则本身已有持续强势要求；历史条件未通过不等于刚启动的新龙头。",
            "三个账户受到各自现金、持仓和成交限制，历史条件未通过组的收益不能用另外两组账户收益相减得到。",
            "收益包含模拟费用和每侧10基点滑点；止损触发值不保证实际亏损幅度。未知历史状态、原始价格和固定存续样本的局限仍保留。",
            "阶段与9·24前后切片仅作描述，权益与持仓连续承接；没有把任何日期写成买卖开关。",
            "日线价格与成交额不能识别主力、散户或量化资金身份。"
        ],
        "evidenceFlow":[
            {"kind":"calculation","title":"统一队列与独立账户","body":"全部原候选先排序，三组只改变历史条件准入；所有18份日程在账户运行前固定，18个账户逐个重新执行。"},
            {"kind":"validation","title":"独立复核","body":"独立重建日程分组，并核对现金、持仓、费用、成交、退出及连续权益；另以独立闭仓和区间计算复核汇总。"}
        ]}
    queries = []
    def key(row,policy=True):
        value={"时期":row["period"],"板块":BOARDS[row["board"]]}
        if policy:value["策略"]=POLICIES[row["policy"]]
        return value
    def add(identity,label,rows,note):
        queries.append({"id":identity,"source":{**source,"label":label},"rows":rows,"columns":list(rows[0]),
            "reportingPeriod":"2022—2023与2024—2025的六个固定板块样本",
            "preview":{"kind":"aggregate","note":note,"totalRows":len(rows)}})
    rows=[]
    for r in report["accountRows"]:
        m=r["metrics"]
        rows.append({**key(r),"累计净收益%":m["accountReturnPct"],"最大回撤%":m["maxDrawdownPct"],
            "平均仓位%":m["meanExposurePct"],"闭仓数":m["closed"],"胜率%":m["winRatePct"],
            "平均盈利%":m["meanWinPct"],"平均亏损%":m["meanLossPct"],
            "盈利中位数%":m["medianWinPct"],"亏损中位数%":m["medianLossPct"],
            "每笔期望%":m["expectancyReturnPct"],"盈亏比":m["payoffRatio"],"利润因子":m["profitFactor"],
            "前五盈利占比%":m["top5ShareOfGrossProfitPct"],"未平仓数":m["openPositions"],
            "未平浮动盈亏元":m["openUnrealizedPnlAmount"],"费用元":m["feesPaid"]})
    add("account-rows","全部18个独立账户",rows,"每个时期、板块和策略全部保留；空比率保持未知，不当作零。")
    rows=[]
    for r in report["comparisons"]:
        d=r["candidateMinusControl"]
        rows.append({**key(r,False),"收益变化百分点":d["accountReturnPct"],"回撤变化百分点":d["maxDrawdownPct"],
            "仓位变化百分点":d["meanExposurePct"],"闭仓数量变化":d["closed"],
            "期望收益变化百分点":d["expectancyReturnPct"]})
    add("primary-comparisons","取消历史强势要求的主对照",rows,"全部可判定候选账户减历史强势通过账户；回撤正变化表示回撤更大。")
    rows=[]
    for r in report["formationCoverageRows"]:
        s=r["selectedFormationCounts"]
        rows.append({**key(r,False),"源日期数":r["targetDayCount"],"当前候选股票日":r["currentCandidateCount"],
            "共同前五股票日":r["top5OpportunityCount"],"排名排除":r["rankExcludedCount"],
            "历史通过":s["passed"],"历史未通过":s["failed"],"历史未知":s["unknown"]})
    add("formation-coverage","共同候选与历史状态",rows,"股票日含同一股票在不同日期重复出现，不等于独立股票或盈利机会。")
    rows=[]
    for r in report["coverageRows"]:
        rows.append({**key(r),"共同前五":r["originalOpportunities"],"入场日程":r["scheduledSignals"],
            "未纳入日程":r["notScheduled"],"未买入":r["notEntered"],"已买入":r["entered"],
            "已闭仓":r["closed"],"期末未平仓":r["openAtCutoff"],"历史未知":r["admissionCounts"]["unknown"],
            "估值不可靠日期数":r["valuationUnreliableDays"],"期末陈旧估值数":r["openStaleMarks"]})
    add("account-coverage","候选到实际成交",rows,"每个原候选在三个账户中的纳入、未买入、闭仓或未平仓状态均保留。")
    for table,identity,label in (("yearRows","year-rows","全部年度"),("halfYearRows","half-year-rows","全部半年度"),
        ("phaseRows","phase-rows","全部市场阶段"),("curveEpochRows","epoch-rows","9·24前后连续权益")):
        rows=[]
        for r in report[table]:
            rows.append({**key(r),"阶段":r.get("phaseTitle",r["id"]),"开始":r["requestedStart"],"结束":r["requestedEnd"],
                "区间收益%":r["returnPct"],"区间最大回撤%":r["maxDrawdownPct"],
                "平均仓位%":r["exposurePct"]["mean"],"买入次数":r["buyFillCount"],"日程信号":r["decisionSignals"]})
        add(identity,label,rows,"沿用进入区间前的真实权益与持仓，不重置资金；长短不同的区间不直接比较年化收益。")
    return {"schemaVersion":1,"items":[{"id":"persistence-admission","title":"历史强势门槛是否改善实际账户盈亏？","queries":queries}]}

def main():
    names=("plan.json","input-completion.json","input-validation.json","comparison.json","validation.json","readout.json","readout-validation.json")
    pins={name:sha(OUT/name) for name in names}
    proof=read(OUT/"readout-validation.json")
    assert proof["passed"] and proof["planSha256"]==pins["plan.json"] and proof["readoutSha256"]==pins["readout.json"]
    assert proof["comparisonSha256"]==pins["comparison.json"] and proof["accountValidationSha256"]==pins["validation.json"]
    report=read(OUT/"readout.json")
    value=payload(report);target=OUT/"persistence-admission-sources.json";write(target,value)
    directory=OUT/"inline";directory.mkdir(exist_ok=False)
    fragment=directory/"leader-persistence-admission-sources.html"
    result=subprocess.run([str(NODE),str(HELPER),"--input",str(target),"--output",str(fragment)],
        capture_output=True,text=True,encoding="utf-8")
    write(OUT/"sources-render-log.json",{"exitCode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
    assert result.returncode==0,result.stderr
    assert fragment.stat().st_size<1_000_000 and all(sha(OUT/n)==s for n,s in pins.items())
    receipt={"passed":True,"createdAt":datetime.now(timezone.utc).isoformat(),"builderSha256":sha(__file__),
        "inputPins":pins,"payload":ref(target),"fragment":ref(fragment),
        "presentationHelpers":[{"path":str(HELPER),"sha256":sha(HELPER)}],
        "sourceRows":sum(len(q["rows"]) for q in value["items"][0]["queries"]),"deliveryOrder":["sources"]}
    write(OUT/"render-receipt.json",receipt)
    print(json.dumps({"passed":True,"renderReceiptSha256":sha(OUT/"render-receipt.json"),"sourceRows":receipt["sourceRows"]}))
if __name__=="__main__":main()

