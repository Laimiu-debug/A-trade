"""Native Sources for actual numerical-repair reruns and portfolio effects."""
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_precision_sources_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
STUDY=ROOT/'artifacts/strategy-search/sector-lifecycle-system-v3/price-boundary-repair-v1/attempt-001';OUT=STUDY/'presentation-001'
VIS=Path('C:/Users/25647/.codex/visualizations/2026/10/18/01a14cab-4be4-7482-8a1e-a5c2037db5f5')
NODE=Path('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
RENDERER=Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs')
LABEL={'completeSystem':'完整体系','withoutMarketAdmission':'取消市场入场限制','withoutSectorAdmission':'取消行业入场限制','withoutCatchupEntry':'取消补涨入场','v2AdmissionWithLifecycleExit':'第二版原信号 健康度退出'}


def build():
    validation=U.read(STUDY/'validation.json')
    if not validation['passed'] or not validation['numericalContractConformancePassed'] or validation['numericBoundaryExceptionCount']:raise ValueError('Corrected complete audit required')
    result=U.read(U.bound(validation['readout']));change=U.read(STUDY/'matched-change-analysis.json')
    for key in ('builder','newValidation','originalValidation','originalBoundaryDiagnostic'):U.bound(change[key])
    rows=[]
    for label,values in (('原冻结算术 有边界例外',result['reusedParentRows']),('数值修复后 同信号',result['rows'])):
        for r in values:rows.append({'version':label,'account':LABEL[r['policy']],'period':'主区间' if r['period']=='primary' else '弱市压力对照',
            'closed':r['closed'],'returnPct':r['netReturnPct'],'ddPct':r['maxDrawdownPct'],'R':r['payoffRatio'],'PF':r['profitFactor'],
            'meanWinPct':r['meanWinReturnPct'],'meanLossPct':r['meanLossReturnPct'],'exposurePct':r['averageExposurePct']})
    delta=[{'account':LABEL[r['policy']],'period':'主区间' if r['period']=='primary' else '弱市压力对照',
        **{k:r[k] for k in ('oldClosed','newClosed','oldReturnPct','newReturnPct','returnDeltaPp','economicFillsIdentical','entireEquityCurveIdentical')}} for r in change['rows']]
    events=[{'symbol':r['symbol'],'entryDate':r['entryDate'],'boundaryDate':r['sourceBoundaryDate'],'sourceClose':r['sourceObservedClose'],
        'oldExit':r['oldExitDate'],'newExit':r['newExitDate'],'oldReturnPct':r['oldEpisodeReturnPct'],'newReturnPct':r['newEpisodeReturnPct'],
        'oldPnl':r['oldEpisodeNetPnl'],'newPnl':r['newEpisodeNetPnl']} for r in change['boundaryEpisodes']]
    source={'label':'结构失效价修复的完全匹配账户','files':[{'label':str(p.relative_to(ROOT)).replace('\\','/')} for p in
        (STUDY/'plan.json',STUDY/'input-validation.json',STUDY/'readout.json',U.bound(validation['fullAccountAudit']),STUDY/'matched-change-analysis.json',
            ROOT/'docs/sector-lifecycle-price-boundary-repair-v1.json')],
        'metricDefinitions':[{'label':'平均盈亏比','definition':'完全闭仓扣费盈利百分比均值除以亏损百分比均值绝对值；无盈利或亏损样本则未知。'},
            {'label':'PF','definition':'完全闭仓盈利金额之和除以亏损金额之和绝对值。'},
            {'label':'账户收益和回撤','definition':'连续50万元账户的每日现金加持仓估值，已计费用和滑点，不按年重置。'},
            {'label':'相同经济成交','definition':'比较实际买卖时间、数量、价格、费用、现金、退出原因等经济字段；仅新增的数值容差记录不算成交差异。'},
            {'label':'个案盈亏','definition':'两个独立执行账户中同一股票和首次入场日的完全闭仓实际结果；不以此替代完整账户变化。'}],
        'filters':['同一4999股票原始来源、公布日行业归属、市场板块判断、信号与执行上下文','主区间2024-09-24至2026-10-15；弱市2024-04-08至2024-09-23','只修复结构失效价比较的浮点边界；不算新增策略假设'],
        'caveats':['修复通过执行核对不代表策略达标；全部指标仍须联合评价。','原始价格、幸存股票池、历史可交易性与开盘容量资料限制保留。',
            '历史已接触，不是未见样本；本地缺少2026-10-16。','零交易、少量闭仓或低仓位不能证明稳定防守和超额收益。','提早卖出会改变后续现金与持仓；展示的是完整重跑，不是手工补差。'],
        'evidenceFlow':[{'kind':'validation','title':'同一信号与上下文','detail':'108项执行前检查；六套信号与两套执行上下文逐字节一致。'},
            {'kind':'validation','title':'修复与完整账户复核','detail':'独立重建初始风险成本与修复后的结构价格比较，核对逐持仓日退出、开盘风险、数量、现金、费用和闭仓收益；原版完整保存。'},
            {'kind':'calculation','title':'实际路径对照','detail':f"{change['unchangedAccounts']}个账户经济成交与整条权益曲线完全一致；{change['changedAccounts']}个账户发生变化，范围与原先独立识别的边界账户一致。"}]}
    def query(identity,values,columns):return {'id':identity,'rows':values,'columns':[{'field':k,'label':v} for k,v in columns],'source':source,
        'reportingPeriod':'主区间2024-09-24至2026-10-15；弱市单列','preview':{'kind':'aggregate','note':'全部匹配账户和边界个案','totalRows':len(values)}}
    payload={'schemaVersion':1,'items':[{'id':'lifecycle-price-repair-matched','title':'结构失效价数值修复的匹配重跑结果','queries':[
        query('matched-accounts',rows,[('version','执行版本'),('account','账户'),('period','区间'),('closed','闭仓'),('returnPct','收益 %'),('ddPct','回撤 %'),('R','盈亏比'),('PF','PF'),('meanWinPct','平均盈利 %'),('meanLossPct','平均亏损 %'),('exposurePct','平均仓位 %')]),
        query('actual-account-changes',delta,[('account','账户'),('period','区间'),('oldClosed','原闭仓'),('newClosed','修复闭仓'),('oldReturnPct','原收益 %'),('newReturnPct','修复收益 %'),('returnDeltaPp','变化 百分点'),('economicFillsIdentical','经济成交相同'),('entireEquityCurveIdentical','整条权益相同')]),
        query('source-boundary-episode',events,[('symbol','股票'),('entryDate','入场日'),('boundaryDate','失效位相等日'),('sourceClose','当日收盘'),('oldExit','原退出日'),('newExit','修复退出日'),('oldReturnPct','原闭仓收益 %'),('newReturnPct','修复闭仓收益 %'),('oldPnl','原净盈亏 元'),('newPnl','修复净盈亏 元')])]}]}
    if len(rows)!=12 or len(delta)!=6 or len(events)!=1:raise ValueError('Matched account/event population differs')
    OUT.mkdir();U.write(OUT/'sources.json',payload);target=VIS/'whole-system-price-repair-sources.html'
    if target.exists():raise ValueError('Preserve existing Sources artifact')
    ran=subprocess.run([str(NODE),str(RENDERER),'--input',str(OUT/'sources.json'),'--output',str(target)],capture_output=True,text=True,encoding='utf-8',timeout=60)
    U.write(OUT/'render-log.json',{'exitCode':ran.returncode,'stdout':ran.stdout,'stderr':ran.stderr})
    if ran.returncode or not target.exists():raise ValueError('Native Sources render failed')
    U.write(OUT/'presentation.json',{'passed':True,'builder':U.ref(Path(__file__)),'sources':U.ref(OUT/'sources.json'),'validation':U.ref(STUDY/'validation.json'),
        'comparison':U.ref(STUDY/'matched-change-analysis.json'),'inlinePath':str(target),'inlineSha256':U.sha(target),'reviewedRows':19,'newAccounts':0,'newStrategyTrials':0,'targetAchieved':False})
    U.emit({'presentation':U.ref(OUT/'presentation.json'),'inlinePath':str(target),'rows':19})


if __name__=='__main__':build()
