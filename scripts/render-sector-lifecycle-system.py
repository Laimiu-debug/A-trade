"""Source-bound lifecycle results with explicit original-entry/exit controls."""
from collections import Counter
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_lifecycle_render_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
STUDY=ROOT/'artifacts/strategy-search/sector-lifecycle-system-v3/attempt-001';OUT=STUDY/'presentation-001'
VIS=Path('C:/Users/25647/.codex/visualizations/2026/10/18/01a14cab-4be4-7482-8a1e-a5c2037db5f5')
NODE=Path('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
RENDERER=Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs')
LABEL={'completeSystem':'完整体系','withoutMarketAdmission':'取消市场入场限制','withoutSectorAdmission':'取消行业入场限制','withoutCatchupEntry':'取消补涨入场','v2AdmissionWithLifecycleExit':'第二版原信号 仅换行业退出'}


def metrics(r):
    return {'signals':r['signals'],'closed':r['closed'],'returnPct':r['netReturnPct'],'ddPct':r['maxDrawdownPct'],'R':r['payoffRatio'],'PF':r['profitFactor'],
        'meanWinPct':r['meanWinReturnPct'],'meanLossPct':r['meanLossReturnPct'],'exposurePct':r['averageExposurePct'],'excessPp':r['excessPercentagePoints']}


def build():
    validation=U.read(STUDY/'validation.json')
    if not validation['passed']:raise ValueError('Full independent six-account audit required')
    for key in ('plan','inputs','readout','fullAccountAudit','diagnostics'):U.bound(validation[key])
    result=U.read(U.bound(validation['readout']));parent=U.read(U.bound(result['reusedParentValidation']));U.bound(parent['readout'])
    numeric=U.read(U.bound(validation['numericBoundaryDiagnostic']))
    boundary_counts=Counter((r['period'],r['policy']) for r in numeric['rows'])
    diagnostic=U.read(STUDY/'opportunity-diagnostic-001/readout.json')
    for key in ('builder','inputs','originalFormation','newFormation','context'):U.bound(diagnostic[key])
    rows=[]
    for version,values in (('第二版 保留对照',result['reusedParentRows']),('第三版 行业生命周期',result['rows'])):
        for r in values:rows.append({'version':version,'account':LABEL[r['policy']],'period':'主区间' if r['period']=='primary' else '弱市压力对照',**metrics(r),
            'numericExceptions':boundary_counts[r['period'],r['policy']] if version.startswith('第三版') else None})
    comparisons=[]
    for current in result['rows']:
        if current['period']!='primary':continue
        match='completeSystem' if current['policy']=='v2AdmissionWithLifecycleExit' else current['policy']
        old=next(r for r in result['reusedParentRows'] if r['period']=='primary' and r['policy']==match)
        comparisons.append({'account':LABEL[current['policy']],'oldClosed':old['closed'],'newClosed':current['closed'],
            'returnDeltaPp':current['netReturnPct']-old['netReturnPct'],'ddDeltaPp':current['maxDrawdownPct']-old['maxDrawdownPct'],
            'oldR':old['payoffRatio'],'newR':current['payoffRatio'],'oldPF':old['profitFactor'],'newPF':current['profitFactor']})
    abc=[]
    for name,values,policy in (('A 第二版入场 原行业退出',result['reusedParentRows'],'completeSystem'),
            ('B 第二版原入场 健康度退出',result['rows'],'v2AdmissionWithLifecycleExit'),('C 生命周期入场 健康度退出',result['rows'],'completeSystem')):
        r=next(r for r in values if r['period']=='primary' and r['policy']==policy);abc.append({'method':name,**metrics(r)})
    overlaps=[{'account':LABEL[r['policy']],**{k:r[k] for k in ('oldSignals','newSignals','retainedStockCloseEvents','addedStockCloseEvents','lostStockCloseEvents')}} for r in diagnostic['rows']]
    formation=U.read(U.bound(diagnostic['newFormation']));coverage=[]
    for policy,values in formation['schedules'].items():
        for year in ('2024','2025','2026'):
            s=[r for r in values if r['signalDate']>='2024-09-24' and r['signalDate'].startswith(year)];roles=Counter(formation['metadata'][r['signalId']]['pattern'] for r in s)
            coverage.append({'account':LABEL[policy],'year':year,'signals':len(s),'leaders':roles['leaderBase'],'catchup':roles['catchup']})
    source={'label':'完整生命周期体系与固定原信号退出对照','files':[{'label':str(p.relative_to(ROOT)).replace('\\','/')} for p in
        (STUDY/'plan.json',STUDY/'input-validation.json',STUDY/'lifecycle-validation.json',STUDY/'entry-preparation-validation.json',STUDY/'readout.json',
            U.bound(validation['fullAccountAudit']),STUDY/'episode-diagnostics.json',STUDY/'opportunity-diagnostic-001/readout.json',U.bound(validation['numericBoundaryDiagnostic']),ROOT/'docs/sector-lifecycle-system-v3.json')],
        'metricDefinitions':[{'label':'闭仓平均盈亏比','definition':'完全闭仓扣费盈利百分比均值除以亏损百分比均值绝对值；无样本时为未知。'},
            {'label':'PF','definition':'完全闭仓盈利金额总和除以亏损金额总和绝对值。'},
            {'label':'收益与回撤','definition':'连续50万元现金与持仓账户，费用滑点已计入，不按年重置，期末未强制卖出。'},
            {'label':'超额百分点','definition':'同区间账户累计收益减沪深300累计涨幅；不作风险调整。'},
            {'label':'入场事件交集','definition':'以股票及信号收盘日匹配冻结候选；候选不是成交，新增或排除不代表盈利或亏损。'}],
        'filters':['主区间2024-09-24至2026-10-15；弱市2024-04-08至2024-09-23','固定4999只来源股票与当时已公布行业归属','第二版五账户复用，第三版六账户全部执行'],
        'caveats':['本批发现1处结构失效价浮点边界例外，冻结引擎算术已复核，但未通过严格数学失效位契约验收；修复后的同信号账户尚待运行。','目标尚未实现；少量或零笔闭仓不能证明稳定高盈亏比或防守能力。','原始价、幸存股票池及历史可交易性限制保留；历史已接触，不是未见样本。',
            '2024仅9月24日后、2026仅到10月15日；本地缺少10月16日。','A/B/C比较改变退出后的现金与持仓约束，差值不能相加作交易级因果贡献。',
            '行业生命周期增加持续资格，但前三名竞争会挤出原有合格候选；不能假设新增资格等于更高收益。'],
        'evidenceFlow':[{'kind':'validation','title':'逐日行业状态与入场证据','detail':'103项执行前测试；611个决策日、51441个行业状态由独立历史回溯重建，个股准备与信号由独立直接窗口核对。'},
            {'kind':'validation','title':'固定入场对照','detail':'B账户信号文件与第二版完整体系逐字节一致；仅行业退出采用新健康度状态。'},
            {'kind':'validation','title':'账户实际执行复核','detail':'六账户逐日执行上下文与已验证生命周期核对；逐持仓日退出、开盘风险、现金、费用及完全闭仓计算独立复核。'}]}
    def query(identity,values,columns):return {'id':identity,'rows':values,'columns':[{'field':k,'label':label} for k,label in columns],'source':source,
        'reportingPeriod':'2024-09-24至2026-10-15；弱市区间单列','preview':{'kind':'aggregate','note':'固定规则全部账户与来源候选完整列示','totalRows':len(values)}}
    payload={'schemaVersion':1,'items':[{'id':'sector-lifecycle-complete-system','title':'第三版冻结执行结果及数值边界例外','queries':[
        query('abc-method-control',abc,[('method','方法'),('closed','闭仓'),('returnPct','收益 %'),('ddPct','回撤 %'),('R','盈亏比'),('PF','PF'),('meanWinPct','平均盈利 %'),('meanLossPct','平均亏损 %'),('exposurePct','平均仓位 %')]),
        query('all-accounts',rows,[('version','版本'),('account','账户'),('period','区间'),('signals','信号'),('closed','闭仓'),('returnPct','收益 %'),('ddPct','回撤 %'),('R','盈亏比'),('PF','PF'),('meanLossPct','平均亏损 %'),('exposurePct','平均仓位 %'),('excessPp','超额百分点'),('numericExceptions','本次边界例外数')]),
        query('matched-controls',comparisons,[('account','账户'),('oldClosed','第二版闭仓'),('newClosed','第三版闭仓'),('returnDeltaPp','收益变化 百分点'),('ddDeltaPp','回撤变化 百分点'),('oldR','第二版盈亏比'),('newR','第三版盈亏比'),('oldPF','第二版PF'),('newPF','第三版PF')]),
        query('candidate-overlap',overlaps,[('account','账户'),('oldSignals','原信号'),('newSignals','新信号'),('retainedStockCloseEvents','保留'),('addedStockCloseEvents','新增'),('lostStockCloseEvents','排除')]),
        query('year-role-coverage',coverage,[('account','账户'),('year','年份范围见来源'),('signals','信号数'),('leaders','领涨蓄势'),('catchup','补涨蓄势')])]}]}
    if (len(rows),len(comparisons),len(abc),len(overlaps),len(coverage))!=(11,5,3,4,12):raise ValueError('Account/coverage population mismatch')
    OUT.mkdir();U.write(OUT/'sources.json',payload);target=VIS/'whole-system-lifecycle-v3-sources.html'
    if target.exists():raise ValueError('Preserve rendered evidence')
    ran=subprocess.run([str(NODE),str(RENDERER),'--input',str(OUT/'sources.json'),'--output',str(target)],capture_output=True,text=True,encoding='utf-8',timeout=60)
    U.write(OUT/'render-log.json',{'exitCode':ran.returncode,'stdout':ran.stdout,'stderr':ran.stderr})
    if ran.returncode or not target.exists():raise ValueError('Native Sources rendering failed')
    U.write(OUT/'presentation.json',{'passed':True,'builder':U.ref(Path(__file__)),'sources':U.ref(OUT/'sources.json'),'validation':U.ref(STUDY/'validation.json'),
        'opportunityDiagnostic':U.ref(STUDY/'opportunity-diagnostic-001/readout.json'),'inlinePath':str(target),'inlineSha256':U.sha(target),'reviewedRows':35,'newAccounts':0,'newStrategyTrials':0,'targetAchieved':False})
    U.emit({'presentation':U.ref(OUT/'presentation.json'),'inlinePath':str(target),'rows':35})


if __name__=='__main__':build()
