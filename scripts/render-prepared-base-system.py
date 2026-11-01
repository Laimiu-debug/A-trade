"""Bind full V2 results, retained V1 accounts and signal coverage to Sources."""
from collections import Counter
from pathlib import Path
import importlib.util
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_prepared_base_sources_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
read,write,ref,bound,sha=(getattr(U,k) for k in ('read','write','ref','bound','sha'))
STUDY=ROOT/'artifacts/strategy-search/market-sector-stock-base-v2/attempt-001'
OUT=STUDY/'presentation-001'
VIS=Path('C:/Users/25647/.codex/visualizations/2026/10/18/01a14cab-4be4-7482-8a1e-a5c2037db5f5')
NODE=Path('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
RENDERER=Path('C:/Users/25647/.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11/skills/visualize-data/scripts/render-inline-sources.mjs')
LABEL={'completeSystem':'完整体系','withoutMarketAdmission':'取消市场入场限制','withoutSectorAdmission':'取消行业入场限制','withoutCatchupEntry':'取消补涨入场','sectorLiquidRepresentative':'行业流动性代表股'}


def build():
    validation=read(STUDY/'validation.json')
    if not validation['passed']:raise ValueError('Independent complete-account audit required')
    for k in ('plan','inputs','fullAccountAudit','diagnostics'):bound(validation[k])
    result=read(bound(validation['readout']));parent=read(bound(result['reusedParentValidation']))
    bound(parent['readout']);formation=read(STUDY/'formation.candidate.json.gz')
    gateproof=read(STUDY/'entry-coverage-001/readout.json')
    bound(gateproof['builder']);bound(gateproof['formation']);bound(gateproof['context'])
    rows=[]
    for version,values in (('第一版 保留对照',result['reusedParentRows']),('第二版 蓄势体系',result['rows'])):
        for r in values:
            rows.append({'version':version,'account':LABEL[r['policy']],'period':'主区间' if r['period']=='primary' else '弱市压力对照',
                'signals':r['signals'],'closed':r['closed'],'returnPct':r['netReturnPct'],'ddPct':r['maxDrawdownPct'],'R':r['payoffRatio'],'PF':r['profitFactor'],
                'lossPct':r['meanLossReturnPct'],'excessPp':r['excessPercentagePoints'],'unreliableDays':r['valuationUnreliableDays']})
    comparisons=[]
    for current in result['rows']:
        if current['period']!='primary':continue
        old=next(r for r in result['reusedParentRows'] if (r['period'],r['policy'])==(current['period'],current['policy']))
        comparisons.append({'account':LABEL[current['policy']],'oldClosed':old['closed'],'newClosed':current['closed'],
            'returnChangePp':current['netReturnPct']-old['netReturnPct'],'ddChangePp':current['maxDrawdownPct']-old['maxDrawdownPct'],
            'oldR':old['payoffRatio'],'newR':current['payoffRatio'],'oldPF':old['profitFactor'],'newPF':current['profitFactor']})
    coverage=[]
    for policy,schedule in formation['schedules'].items():
        for year in ('2024','2025','2026'):
            signals=[s for s in schedule if s['signalDate']>='2024-09-24' and s['signalDate'].startswith(year)]
            roles=Counter(formation['metadata'][s['signalId']]['pattern'] for s in signals)
            coverage.append({'account':LABEL[policy],'year':year,'signals':len(signals),'leaders':roles['leaderBase'],'catchup':roles['catchup']})
    gates=[]
    for g in gateproof['rows']:
        s=g['mutuallyExclusiveStageCounts']
        gates.append({'role':'领涨蓄势' if g['role']=='leaderBase' else '补涨蓄势',**{k:s.get(k,0) for k in ('allPreparedStockEvents','marketNotAdmitted','industryNotCurrentlyRawStrong','industryNotThreeConsecutive','outsideIndustryTopThree','marketAndIndustryAdmitted')}})
    source={'label':'第二版完整体系及保留的第一版账户','files':[{'label':str(p.relative_to(ROOT)).replace('\\','/')} for p in
        (STUDY/'plan.json',STUDY/'input-validation.json',STUDY/'formation-validation.json',STUDY/'readout.json',bound(validation['fullAccountAudit']),STUDY/'episode-diagnostics.json',STUDY/'entry-coverage-001/readout.json',ROOT/'docs/market-sector-stock-base-v2.json')],
        'metricDefinitions':[{'label':'平均盈亏比','definition':'完全闭仓扣费盈利百分比的均值除以亏损百分比均值的绝对值。无盈利或亏损样本时为未知。'},
            {'label':'PF','definition':'完全闭仓盈利金额之和除以亏损金额之和绝对值。'},
            {'label':'收益和回撤','definition':'连续50万元账户的每日现金加持仓估值，费用和滑点已扣除，不按年重置。'},
            {'label':'超额百分点','definition':'同区间账户累计收益减沪深300累计涨幅。'},
            {'label':'信号','definition':'T收盘冻结的行业内排序候选，尚须经过现金、仓位、开盘支撑距离和原有交易资格检查。'},
            {'label':'入场覆盖事件','definition':'满足个股角色和蓄势的股票乘收盘日事件，先按市场、行业当日强度、连续确认、前三排序分类；分类互斥，同股多日不等于独立交易。'}],
        'filters':['主区间2024-09-24至2026-10-15；弱市对照2024-04-08至2024-09-23','固定4999只来源股票与已公布行业归属','第一版六账户原样保留复用，第二版新运行五账户'],
        'caveats':['研究目标尚未实现；少量闭仓或空仓不能作为稳定高盈亏比和防守能力的证据。',
            '原始价格、幸存股票池、历史可交易性和开盘容量资料限制保留；已接触历史不是独立未见样本。',
            '2024仅统计9月24日后；2026仅到10月15日，本地缺少10月16日。',
            '账户差值包含准备过程、真实支撑、股票排序与开盘风险规则的共同变化，不能拆成单一指标的因果贡献。',
            '信号覆盖在观察账户结果前已冻结；本轮不根据盈亏放宽条件或删除失败账户。'],
        'evidenceFlow':[{'kind':'validation','title':'信号先于结果固定','detail':'95项执行前测试通过；611个决策日的准备窗口、角色、支撑、排序和全部信号由直接来源窗口独立重建。'},
            {'kind':'validation','title':'独立账户和新增开盘风险复核','detail':'五账户逐持仓日退出、现金费用、仓位和闭仓计算均复核；两侧开盘风险拒绝由原始开盘价重新计算，保留失败和未成交。'}]}
    def query(identity,values,cols):
        return {'id':identity,'rows':values,'columns':[{'field':k,'label':v} for k,v in cols],'source':source,
            'reportingPeriod':'主区间2024-09-24至2026-10-15；弱市对照单列','preview':{'kind':'aggregate','note':'固定账户与信号覆盖完整列示','totalRows':len(values)}}
    payload={'schemaVersion':1,'items':[{'id':'prepared-base-whole-system-v2','title':'第二版蓄势轮动体系的完整验证','queries':[
        query('all-accounts',rows,[('version','方法'),('account','账户'),('period','区间'),('signals','信号'),('closed','闭仓'),('returnPct','收益 %'),('ddPct','回撤 %'),('R','盈亏比'),('PF','PF'),('lossPct','平均亏损 %'),('excessPp','超额百分点'),('unreliableDays','估值不可靠日')]),
        query('matched-method-change',comparisons,[('account','账户'),('oldClosed','第一版闭仓'),('newClosed','第二版闭仓'),('returnChangePp','收益变化 百分点'),('ddChangePp','回撤变化 百分点'),('oldR','第一版盈亏比'),('newR','第二版盈亏比'),('oldPF','第一版PF'),('newPF','第二版PF')]),
        query('frozen-signal-coverage',coverage,[('account','账户'),('year','年份范围见来源'),('signals','信号数'),('leaders','领涨蓄势'),('catchup','补涨蓄势')]),
        query('prepared-event-exclusions',gates,[('role','角色'),('allPreparedStockEvents','全部蓄势事件'),('marketNotAdmitted','市场未通过'),('industryNotCurrentlyRawStrong','行业当日不强'),('industryNotThreeConsecutive','行业确认不足'),('outsideIndustryTopThree','行业未入前三'),('marketAndIndustryAdmitted','市场行业均通过')])]}]}
    if len(rows)!=11 or len(comparisons)!=4 or len(coverage)!=12:raise ValueError('Complete account/coverage population required')
    OUT.mkdir();write(OUT/'sources.json',payload)
    target=VIS/'whole-system-base-v2-sources.html'
    if target.exists():raise ValueError('Preserve rendered evidence')
    ran=subprocess.run([str(NODE),str(RENDERER),'--input',str(OUT/'sources.json'),'--output',str(target)],capture_output=True,text=True,encoding='utf-8',timeout=60)
    write(OUT/'render-log.json',{'exitCode':ran.returncode,'stdout':ran.stdout,'stderr':ran.stderr})
    if ran.returncode or not target.exists():raise ValueError('Sources render failed')
    write(OUT/'presentation.json',{'passed':True,'builder':ref(Path(__file__)),'sources':ref(OUT/'sources.json'),'validation':ref(STUDY/'validation.json'),
        'inlinePath':str(target),'inlineSha256':sha(target),'reviewedRows':29,'newAccounts':0,'newStrategyTrials':0,'targetAchieved':False})
    U.emit({'presentation':ref(OUT/'presentation.json'),'inlinePath':str(target),'reviewedRows':29})


if __name__=='__main__':build()
