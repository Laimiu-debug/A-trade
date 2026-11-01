"""Register a full long-leader/catchup system after the V3 audit completes."""
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_sector_long_breakout_registration_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
BASE=ROOT/'artifacts/strategy-search';ORIGINAL=BASE/'sector-lifecycle-system-v3/attempt-001'
PARENT=BASE/'sector-lifecycle-system-v3/price-boundary-repair-v1/attempt-001'
OUT=BASE/'sector-long-breakout-system-v4';CONTRACT=ROOT/'docs/sector-long-breakout-system-v4.json'
READABLE=ROOT/'docs/sector-long-breakout-system-v4.md'


def register():
    validation=U.read(PARENT/'validation.json')
    if not validation['passed']:raise ValueError('Complete V3 independent account audit required')
    results=U.read(U.bound(validation['readout']));diagnostic=U.read(ORIGINAL/'opportunity-diagnostic-001/readout.json')
    if not validation.get('numericalContractConformancePassed',False):raise ValueError('Repair and independently reexecute the structural numerical boundary before registering another entry hypothesis')
    for k in ('plan','inputs','fullAccountAudit','diagnostics'):U.bound(validation[k])
    for k in ('builder','inputs','originalFormation','newFormation','context'):U.bound(diagnostic[k])
    if len(results['rows'])!=6 or results['profitGoalAchieved']:raise ValueError('Expected complete failed third-system development evidence')
    if OUT.exists() or CONTRACT.exists() or READABLE.exists():raise ValueError('Preserve prior registered hypothesis')
    v2_validation_path=BASE/'market-sector-stock-base-v2/attempt-001/validation.json'
    v2_validation=U.read(v2_validation_path)
    if not v2_validation['passed']:raise ValueError('Retained V2 development readout required')
    v2_results=U.read(U.bound(v2_validation['readout']))
    a=next(r for r in v2_results['rows'] if r['period']=='primary' and r['policy']=='completeSystem')
    b=next(r for r in results['rows'] if r['period']=='primary' and r['policy']=='v2AdmissionWithLifecycleExit')
    c=next(r for r in results['rows'] if r['period']=='primary' and r['policy']=='completeSystem')
    def compact(r):return {k:r[k] for k in ('netReturnPct','maxDrawdownPct','payoffRatio','profitFactor','closed','meanWinReturnPct','meanLossReturnPct')}
    rules={'schema':'sector-long-breakout-complete-system-v4-hypothesis','status':'fixed-before-implementation-and-V4-account-outcomes',
        'researchUnit':'Whole strong-market/current-strong-industry/long-leader-and-catchup/portfolio/health-exit system',
        'parentLifecycleProtocol':U.ref(ROOT/'docs/sector-lifecycle-system-v3.json'),'parentAccountValidation':U.ref(PARENT/'validation.json'),
        'parentPrecisionCorrection':U.ref(ROOT/'docs/sector-lifecycle-price-boundary-repair-v1.json'),'additionalV2DevelopmentValidation':U.ref(v2_validation_path),
        'historicalLongBreakoutDefinition':U.ref(ROOT/'docs/liquid-trend-breakout-v1.json'),
        'historicalLongBreakoutReadout':U.ref(BASE/'liquid-trend-breakout-v1/readout.json'),
        'rationale':{'observedABC':{'A_originalV2_developmentOnly':compact(a),'B_correctedExactV2SignalsHealthExit':compact(b),'C_correctedActiveAdmissionHealthExit':compact(c)},
            'sourceOpportunityChanges':diagnostic['rows'][0],
            'inference':'Health-based industry exits may preserve larger winners, while active-industry top-three admission has not improved the full account. Five-session prepared leader opportunities remain too sparse. These development comparisons do not establish causality or future profitability.',
            'hypothesis':'Return entry industry selection to current confirmed strength, keep separate health-based holding exits, and seek established long-horizon leaders on their first120-session breakout before adding the unchanged prepared-catchup branch.',
            'historyLimit':'The older long-breakout study supplies a previously fixed entry family, not a matched current-period control or a pristine holdout. All4999 stocks stay eligible; no exchange board is chosen from historical profits.',
            'thresholdSearch':False},
        'marketAndIndustry':{
            'marketAdmission':'Unchanged V1 actual-close SH/SZ liquidity/trend/breadth rules; missing evidence blocks entry',
            'industryAdmission':'Unchanged V1 current raw strength3-consecutive confirmation and top-three score; do not use persistent active status to expand entry shortlist',
            'industryHoldingExit':'Retain V3 known feature-valid relative60>0 and breadth60>0.5 health definition;3 consecutive known failures queue persistent next-open exits. Unknown interrupts failure count; snapshot changes reset counters.',
            'marketExit':'Unchanged3-close composite crisis protection',
            'interpretation':'Fresh current industry strength qualifies new risk; holding continuation uses its distinct longer-trend and internal-breadth test.'},
        'longLeaderBranch':{
            'completeSourceWindow':'Exactly141 consecutive source-calendar sessions T-140 throughT, all positive valid raw OHLCV and amount, correct OHLC envelopes, no explicit suspension; do not compress missing sessions',
            'firstBreakout':'C_T>max(H_T-120..T-1) and C_T-1<=max(H_T-121..T-2)',
            'trend':'C_T>MA60_T>MA120_T>MA120_T-20',
            'earlyPosition':'C_T-1/C_T-21-1<=0.15; this is the prior fixed trendEarly definition, not a scanned new optimum',
            'liquidity':'Prior20 mean amount ending T-1>=50000000 CNY, as in the inherited long-breakout family',
            'industryLeaderRole':'Positive120-session return, industry120-return midrank>=0.8 and above current-industry mean120 return, using only current known membership and source-valid returns',
            'peerCoverage':'At least5 current cohort members and valid complete121-slot120-return observations for at least95percent of those members; otherwise this branch unknown/unavailable',
            'failurePrice':'max(prior10 source lows,0.95*signalClose), positive and strictly below signalClose. Explicitly a structural/protective combination; the95percent part is not represented as an observed support low.',
            'openingGuard':'Use existing V1 fixed-floor/raw-opening ceiling of signalClose*(1+inclusiveT NTR20), one next-session attempt, quantities only decrease. No new NTR<=5percent admission cap or one-NTR minimum failure distance for this long-breakout branch.',
            'reason':'The failed single volatility-cap and sparse-base experiments do not justify excluding every higher-volatility long leader. Portfolio risk and actual loss tails must still be evaluated; no guaranteed5percent realized loss is claimed.'},
        'catchupBranch':{
            'definition':'Run the unchanged V2 prepared-base candidate procedure under the corresponding current-market/current-industry admission policy. Retain only its catchup-pattern events; do not backfill a V2 leaderBase selection with another stock.',
            'anchorIdentity':'Hash-bind original V2 stock-close events and their evidence. New composed policy signal IDs/ranks use a distinct namespace, while source signal IDs/original ranks remain provenance. Reordering is limited to the declared industry/pattern composition, never observed outcomes.',
            'riskAndOpening':'Preserve the actual prior-five-low failure price, signal risk coherence and both-sided modeled-opening coherence from V2 for this branch.',
            'relationship':'Distinct healthy leader identified exactly5 calendar sessions earlier in the same published industry snapshot; all original role/relative-return/base/turnover evidence retained.'},
        'composition':{
            'priority':'For each allowed industry close choose longLeaderBreakout first; use the unchanged V2 catchup event only if that industry has no qualified long leader.',
            'longWithinIndustry':'Prior20 mean amount descending, industry120-return midrank descending, symbol ascending',
            'industryOrdering':'Unchanged exact V1 industry composite percentile score and code tie-break; at mostone event per industry andfive globally',
            'noSubstitution':'Already-held, unaffordable or unfilled candidates are rejected without adding or replacing them from a lower list. Candidate composition is frozen atT close.'},
        'account':{
            'same':'Continuous500000 CNY, same periods/raw4999 sources/calendar/fees/slippage/status and quality constraints. Stock20percent, industry40percent, planned price risk1percent of close equity, at mostfive positions, actual cash/fees/lot/capacity reservations.',
            'exits':'Inherited stop5, branch fixed failure, market crisis, health-based industry retirement and prior20NTR three-times profit ratchet; no fixed profit ceiling, persistent first sells, no forced terminal liquidation. Reuse the independently validated16ULP structural-price comparison correction for every branch.',
            'riskInterpretation':'A close-confirmed next-open exit can lose more than the planned5percent line; actual closed net payoff/PF, loss tails and account drawdown jointly determine evidence.'},
        'newAccountsPlanned':[
            {'period':'primary','policy':'completeSystem'},
            {'period':'primary','policy':'withoutMarketAdmission'},
            {'period':'primary','policy':'withoutSectorAdmission'},
            {'period':'primary','policy':'withoutCatchupEntry'},
            {'period':'primary','policy':'catchupOnly','enginePolicy':'completeSystem'},
            {'period':'weakMarketStress','policy':'completeSystem'}],
        'controlMeaning':'catchupOnly uses the exact corresponding V2 catchup events, with the same new-account common risk/fees/health exit, and no long-leader additions. withoutCatchupEntry is long-leader-only. Reuse the V3 exact-V2-entry/health-exit account as a historical full-method reference, not as the exact new catchupOnly control.',
        'validationPlan':[
            'Freeze new code and all6 complete schedules before any V4 account outcome; preserve all failures',
            'Independently rebuild141-slot source validity, first120-breakout, lagged prior20 return, moving averages, current peer120 ranks/coverage and one-per-industry composition from direct windows',
            'Hash-bind all inherited V2 catchup anchors and verify exact branch-specific opening guards; poison future data and test missing calendar slots, rank ties, signal-day and opening boundaries',
            'Independently project original current-industry admission plus V3 health exit to actual account inputs; replay every held day and all cash/fee/quantity/closed-episode calculations',
            'Evaluate post2024-09-24 full account return/excess, realized net payoff and PF, loss distribution, drawdown, sample size, exposure, annual persistence and concentration together; weak-market profit not required',
            'No sample shortage or a winning sub-board is accepted as a successful full method; do not relax within this frozen hypothesis after outcomes'],
        'numericTargetsHumanConfirmed':False,'numericScreen':'Inherited research proposal only, not a human-confirmed hard target',
        'holdoutClaim':False,'certified':False,'targetAchieved':False,'corporateActionResearchExpansion':False,
        'permissionBoundary':'Local isolated research only; no real orders, commits, pushes, packages or publication'}
    OUT.mkdir();U.write(CONTRACT,rules)
    text='''# 强势市场中的行业领涨与补涨方法

这是一套待验证的完整方法。研究目标是让连续账户中的失败交易可承受、趋势盈利有延续空间，并共同改善扣费闭仓盈亏比、PF、收益、回撤和超额收益。此前完整体系尚未达标；本版没有可宣称的账户结果。

主评价区间为2024年9月24日至2026年10月15日。此前的2024年4月8日至9月23日只作弱市压力对照，不要求获利；更早区间不冒充已有可靠行业归属覆盖。

| 顺序 | 固定方法 | 决策目的 |
| --- | --- | --- |
| 市场 | 沪深成交额代理至少1万亿元，5日均额不低于此前20日的80%，双综指趋势与全股票池广度共同通过。 | 先确认流动性和风险环境支持新增仓位。 |
| 主线行业 | 行业20日、60日表现强于沪深300，内部广度和相对成交参与度通过，连续确认3日，综合排序取前三。 | 新入场要求当下仍有行业共振。 |
| 长期领涨 | 个股120日表现位于当时行业前20%且超过行业均值；首次突破此前120日高点，60日和120日均线向上，突破前20日涨幅不超过15%，此前日均成交额至少5000万元。 | 寻找长期领先、近期尚未过度加速的领涨机会。 |
| 轮动补涨 | 沿用已验证的先行股关系、回踩与缩量整理、重新突破放量，以及真实前五日低点的风险规则。 | 参与有先行证据的行业内扩散。 |
| 候选选择 | 每行业每天最多一个候选，长期领涨优先，其次补涨；同行业内优先此前成交额较大的合格股票。无法成交不补选，持仓不加仓。 | 让选择次序、机会与拒绝原因可追溯。 |
| 资金与风险 | 最多五只；单股新增仓位不超过20%，同行业不超过40%，计划价格风险不超过收盘权益1%，先预留真实现金和费用。 | 控制失败对组合的损害，保留后续机会所需现金。 |
| 执行 | T收盘固定信号、价格边界和数量；下一交易日开盘只尝试一次，越过失效位或高开超过预定波动距离就放弃，数量只能减少。 | 保持可执行时序，不依赖当天事后信息。 |
| 持有与退出 | 个股保护与结构失效、市场危机退出继续执行；行业持仓只在中期相对趋势或内部广度连续3日失效时退出；盈利仓沿用3倍过去20日归一真实波幅跟踪保护，不设固定盈利上限。 | 新开仓要求新鲜强势，继续持有使用独立的健康判断。 |

长期领涨分支使用此前十日低点与5%保护线中较高者作为失效位，明确区分真实低点与保护线；补涨分支继续使用真实前五日低点及其波动匹配要求。结构失效价沿用已独立验证的浮点边界修复。收盘判断、次日尝试卖出的机制仍可能产生超过5%的实际亏损。

行业名单只从公布后的交易日起生效，未知归属和缺失观察不补造资格。已有盈利仓上涨超过新增仓位比例不自动止盈；已经发出的风险退出指令不因随后恢复而撤销。

验证保留完整体系、取消市场入场、取消行业入场、只做长期领涨、只做补涨四组入场对照与一个完整体系弱市账户。全部信号先冻结，再运行连续50万元账户。研究筛查暂用闭仓盈亏比至少3、PF至少1.5、平均亏损不超过5%、最大回撤不超过20%、闭仓至少30笔；这些数值仍是研究提案，不能把某一个指标通过、低仓位或少数大盈利当成整套体系成功。

所有已研究历史都属于开发证据。原始价、幸存股票池、历史可交易性及开盘容量资料限制继续保留；本轮不扩展分红转送研究，不进行实盘交易。精确定义、分支来源与独立验证要求见同目录的 `sector-long-breakout-system-v4.json`。
'''
    with READABLE.open('x',encoding='utf-8') as f:f.write(text)
    U.write(OUT/'hypothesis-registration.json',{'schema':'sector-long-breakout-system-v4-registration','registeredAt':U.now(),'builder':U.ref(Path(__file__)),
        'protocol':U.ref(CONTRACT),'readableRules':U.ref(READABLE),'parentValidation':U.ref(PARENT/'validation.json'),'sourceDiagnostic':U.ref(ORIGINAL/'opportunity-diagnostic-001/readout.json'),
        'nextCandidateTrialNumber':59,'plannedNewAccounts':6,'implementationFrozen':False,'newAccountsRun':0,'newStrategyTrialsCompleted':0,'targetAchieved':False,
        'noThresholdSearch':True,'holdoutClaim':False})
    U.emit({'registered':True,'protocol':U.ref(CONTRACT),'registration':U.ref(OUT/'hypothesis-registration.json'),'plannedNewAccounts':6})


if __name__=='__main__':register()
