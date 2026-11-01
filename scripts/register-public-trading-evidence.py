"""Register public-source evidence and close audited V4 without new accounts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_public_evidence_utils', ROOT / 'scripts/run-liquid-trend-breakout.py')
U = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = U
spec.loader.exec_module(U)
OUT = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1'
V4 = ROOT / 'artifacts/strategy-search/sector-long-breakout-system-v4/attempt-001'


def main():
    if OUT.exists():
        raise ValueError('Preserve the existing registration; use a new attempt for new evidence')
    before = U.workspace_snapshot()
    validation = U.read(V4 / 'validation.json')
    readout_path = U.bound(validation['readout'])
    U.bound(validation['fullAccountAudit'])
    rows = U.read(readout_path)['rows']
    if not (validation['passed'] and validation['numericalContractConformancePassed'] and
            validation['numericBoundaryExceptionCount'] == 0 and validation['newAccounts'] == 6 and
            len(rows) == 6 and sum(r['closed'] for r in rows) == 311 and not validation['targetAchieved']):
        raise ValueError('V4 complete closeout must remain independently validated and unsuccessful')
    registry_path = ROOT / 'artifacts/strategy-search/goal-continuation-20261018.json'
    registry = U.read(registry_path)
    if (registry['completedFixedCandidateTrialsTotalIncludingPrior'], registry['newAccountsThisContinuation']) != (58, 80):
        raise ValueError('Unexpected registry predecessor; do not double count completed V4')
    U.bound(registry['previous49BatchRegistry'])
    OUT.mkdir()
    prior_bytes = registry_path.read_bytes()
    (OUT / 'previous-registry.json').write_bytes(prior_bytes)
    # Facts here are short, source-attributed observations, not copied articles or inferred fills.
    sources = [
        ('tgb-contests', '淘股吧实盘比赛入口', 'https://www.tgb.cn/spmatch/index/', 'platform', 'public-page',
         '可识别比赛与选手；部分账户明细另需登录。'),
        ('tgb-account-login', '淘股吧比赛账户明细访问', 'https://www.tgb.cn/spmatch/matchAll?tid=2nPcAoCrpCL', 'platform', 'redirected-to-login',
         '未登录读取跳转登录页；用户后续表示已登录，工具未核验登录后内容。'),
        ('erchi-profile', '二池本人博客', 'https://m.tgb.cn/blog/9435281', 'trader', 'public-page',
         '定位本人原帖，区分转载的战法总结。'),
        ('erchi-trial-comment', '二池关于试错与主要盈利来源的本人说明', 'https://m.tgb.cn/a/2dVOWdW2Lpm', 'trader', 'public-page',
         '2024-12-12 本人说明有很多试错与无效交易，全年主要盈利来自少数交易；这是本人说明，未由完整账本计算验证。'),
        ('erchi-photos', '二池二三月交割单照片原帖', 'https://m.tgb.cn/a/28kIGuZznWH', 'trader', 'public-page-image-links',
         '2024-05-17 本人发布；正文有六张公开原图链接。尚未转录或核验连续性，区间早于主要评价分界。'),
        ('erchi-sector-view', '二池算力板块与龙头切换原帖', 'https://m.tgb.cn/a/26Wd786WjpP', 'trader', 'public-page',
         '2024-03-26 本人表达板块及龙头切换观点；不能凭该帖推定成交价格、仓位或后续盈亏。'),
        ('erchi-year', '二池年赛结束本人原帖', 'https://m.tgb.cn/a/2err4uoNmhQ', 'trader', 'public-page',
         '本人年度成绩陈述和截图线索，不作为已核验的连续账户结果。'),
        ('host-20241029', '镀金梦想比赛当日点评', 'https://m.tgb.cn/a/2cKq8cYE5Co', 'organizer-commentary', 'public-page',
         '2024-10-29 点评包含买入被套及卖出操作，可用于定位得失并存的行为；无逐笔价格数量。'),
        ('host-20241122', '镀金梦想比赛当日点评', 'https://m.tgb.cn/a/2dofSlmCg1F', 'organizer-commentary', 'public-page',
         '另一日期的比赛复盘入口，不能用两个日期冒充完整交易序列。'),
        ('wanzhu-donggan', '顽主杯主办方：动感超人总结', 'https://xueqiu.com/1997137383/322316792', 'organizer', 'indexed-text-direct-challenge',
         '索引中主办方说明有历史交割单，日期精度有限；原图和完整区间待登录读取核验。'),
        ('wanzhu-wuxu', '顽主杯主办方：无需多言总结', 'https://xueqiu.com/1997137383/345633172', 'organizer', 'indexed-text-direct-challenge',
         '索引将其列为第66届千百万组冠军；尚未核验账户账本与具体打法。'),
        ('wanzhu-65', '顽主杯第65届闭幕总结', 'https://xueqiu.com/1997137383/337017244', 'organizer', 'indexed-text-direct-challenge',
         '用于获取全组排名、退赛和失败背景，保留杠杆与交易品种差异。'),
        ('wanzhu-68', '顽主杯第68届总结', 'https://xueqiu.com/1997137383/365793198', 'organizer', 'indexed-text-only',
         '跨届账户连续性线索；未视为已读取全部原文或明细。'),
        ('eastmoney-official', '东方财富证券官方软件下载页', 'https://www.18.cn/download', 'broker', 'public-index',
         '官网提供实盘比赛产品线索；不是指定账户的成交证据。'),
        ('ths-sim-labelled-real', '同花顺名为实盘杯的模拟比赛', 'https://moni.10jqka.com.cn/hezuo/bsid_229860', 'platform', 'public-page-simulation',
         '比赛公告及介绍明确为模拟炒股，不能仅凭比赛名称当成真实实盘。'),
        ('ths-sim-index', '同花顺模拟炒股平台', 'https://moni.10jqka.com.cn/index.shtml', 'platform', 'public-page-simulation',
         '模拟交易记录可作规则研究旁证，不能验证真实资金成交。'),
        ('9db-erchi', '9点半二池转录汇总', 'https://www.9db.com/detail/57E99E87-7F2A-4A02-BB70-10EB77FB1B67', 'secondary-transcription', 'public-summary-detail-unverified',
         '平台展示2024-02-19至12-31汇总及预估费用；原始单据、资金流和明细权限未核验，未采信其收益计算。'),
        ('sse-disclosure', '上交所每日交易公开信息原始入口', 'https://www.sse.com.cn/disclosure/diclosure/public/dailydata/detail.shtml?refType=11&secAbbr=%E4%BA%91%E8%B5%9B%E6%99%BA%E8%81%94&secCode=600602&tradeDate=20241230', 'exchange', 'public-page-dynamic-table',
         '页面说明只含竞价交易，排除大宗及盘后固定价格；未读取该日完整动态表。'),
        ('szse-disclosure-explanation', '深交所主板交易公开信息说明', 'https://www.szse.cn/www/investor/knowledge/stock/deal/t20230406_599703.html', 'exchange', 'public-page',
         '2023-04-06 官方说明披露营业部或交易单元及买卖金额；不是个人完整账本。后续按事件当时适用规则核验。'),
    ]
    source_rows = [dict(zip(('id', 'title', 'url', 'authority', 'access', 'observation'), row)) for row in sources]
    U.write(OUT / 'source-register.json', {'schema': 'public-trading-source-register-v1', 'checkedAt': U.now(),
        'sources': source_rows, 'completeLiveLedgersAcquired': 0, 'verifiedClosedEpisodes': 0,
        'candidates': [
            {'name': '二池', 'sourceIds': ['erchi-profile', 'erchi-trial-comment', 'erchi-photos', 'erchi-year', 'host-20241029'], 'status': 'original-posts-found-ledger-not-reconciled'},
            {'name': '无需多言', 'sourceIds': ['wanzhu-wuxu'], 'status': 'organizer-index-lead-pending-ledger'},
            {'name': '动感超人', 'sourceIds': ['wanzhu-donggan'], 'status': 'organizer-index-lead-pending-ledger-instrument-scope'},
        ],
        'brokerageBranchIndividualsIdentified': 0, 'newStrategyTrials': 0, 'newAccounts': 0,
        'userReportedLogin': {'taoguba': True, 'xueqiu': True, 'toolVerified': False},
        'browserIssue': {'error': 'failed to write kernel assets: path not found (os error 3)', 'sessionResetRetried': True,
                         'authenticatedContentRead': False, 'cookiesOrCredentialsCopied': False},
        'nextActions': ['核验已登录页读取能力；不反复要求用户登录', '先收集并核对连续交割单、净值、资金流和缺页',
                        '逐条分开保存事实、本人解释、主办方点评和研究推断', '龙虎榜建立披露后事件研究，不能伪造个人盈亏']})
    U.write(OUT / 'registration.json', {'schema': 'public-trading-evidence-research-v1', 'registeredAt': U.now(),
        'protocol': U.ref(ROOT / 'docs/public-trading-evidence-research-v1.md'), 'sourceRegister': U.ref(OUT / 'source-register.json'),
        'builder': U.ref(Path(__file__)), 'supersededPendingStage': registry['nextStage'],
        'humanDirection': ['公开实盘比赛账户及本人复盘优先于继续凭空组合指标', '淘股吧、雪球、东方财富、同花顺、顽主杯等',
                           '同时学习常上龙虎榜的营业部，但不混同营业部与个人账户'],
        'mainSplit': '2024-09-24', 'numericTargetsHumanConfirmed': False,
        'newAccounts': 0, 'newStrategyTrials': 0, 'targetAchieved': False})
    registry['previousLongLeaderCatchupStudy'] = {**registry['activeStudy'], 'status': 'All6 accounts and independent audit completed; all5 primary accounts unprofitable; method rejected.',
        'newAccountsRun': 6, 'newAccounts': 6, 'newStrategyTrials': 1, 'closedEpisodesAudited': 311,
        'readout': U.ref(readout_path), 'validation': U.ref(V4 / 'validation.json'), 'numericalContractConformancePassed': True,
        'numericBoundaryExceptions': 0}
    sessions = registry['previousLongLeaderCatchupStudy']['executionSessions']
    sessions['catchupOnlyCompleted'] = sessions.pop('catchupOnlyRunning')
    sessions['weakStressCompleted'] = sessions.pop('weakStressRunning')
    sessions['auditCompleted'] = 27425
    registry['completedFixedCandidateTrialsThisContinuation'] = 10
    registry['completedFixedCandidateTrialsTotalIncludingPrior'] = 59
    registry['newAccountsThisContinuation'] = 86
    registry['latestStudy'] = {'directory': V4.relative_to(ROOT).as_posix(), 'readout': U.ref(readout_path),
        'validation': U.ref(V4 / 'validation.json'), 'conclusion': 'Full method -29.424percent, realized R0.9492, PF0.1834, closed43. All5 primary accounts negative. New source/account audits passed, profitability goal failed.'}
    registry['latestHumanSteering'] = {'at': U.now(), 'direction': 'Empirical learning from original live competition records and public brokerage-branch disclosure.',
        'venues': ['taoguba', 'xueqiu', 'eastmoney', '10jqka', 'wanzhu'], 'corporateActionEngineeringDeferred': True,
        'humanReportedLogins': ['taoguba', 'xueqiu'], 'browserLoginReadVerified': False}
    registry['nextStage'] = {'status': 'Collect and reconcile public live-account records and original explanations before another strategy hypothesis.',
        'registration': U.ref(OUT / 'registration.json'), 'sourceRegister': U.ref(OUT / 'source-register.json'),
        'newAccountsPlanned': 0, 'newStrategyTrialsPlanned': 0, 'completeLiveLedgersAcquired': 0,
        'numericTargetsHumanConfirmed': False, 'completeSystemValidatedForProfit': False}
    registry['activeStudy'] = {'kind': 'publicLiveAccountAndBrokerageEvidenceResearch', 'directory': OUT.relative_to(ROOT).as_posix(),
        'status': 'Original sources and evidence hierarchy registered; actual ledger acquisition pending. Browser tool cannot yet read authenticated pages.',
        'goalAchieved': False, 'newAccountsRun': 0, 'newStrategyTrials': 0}
    registry['updatedAt'] = U.now()
    registry['goalStatus'] = 'active'
    registry['targetAchieved'] = False
    registry['lastGoalTurnClassification'] = 'Research direction changed by human: finish audited V4 failure; begin original competition/account evidence research and separate brokerage-event study.19 source leads,3 account candidates;0 complete new live ledgers/0 verified source episodes. Goal remains active.'
    if U.workspace_snapshot() != before:
        raise ValueError('Protected user workspace changed')
    registry['userJsonFilesUnchanged'] = True
    # Only this mutable research tracker is replaced. Preserve exact previous bytes above.
    pending = registry_path.with_name('goal-continuation-20261018.public-evidence-pending.json')
    U.write(pending, registry)
    pending.replace(registry_path)
    U.bound(registry['previous49BatchRegistry'])
    U.write(OUT / 'validation.json', {'passed': True, 'completedAt': U.now(), 'protocol': U.ref(ROOT / 'docs/public-trading-evidence-research-v1.md'),
        'registration': U.ref(OUT / 'registration.json'), 'sourceRegister': U.ref(OUT / 'source-register.json'),
        'updatedRegistry': U.ref(registry_path), 'previousRegistry': U.ref(OUT / 'previous-registry.json'),
        'sourceCount': len(source_rows), 'candidateCount': 3, 'completeLiveLedgersAcquired': 0, 'verifiedClosedEpisodes': 0,
        'v4ClosedEpisodes': sum(r['closed'] for r in rows), 'completedCandidatesTotal': 59, 'continuationAccountsTotal': 86,
        'newPublicEvidenceAccounts': 0, 'newPublicEvidenceStrategyTrials': 0, 'protectedJsonCount': len(before),
        'userJsonFilesUnchanged': U.workspace_snapshot() == before, 'historicalRegistryUnchanged': True, 'targetAchieved': False})
    U.emit({'registration': U.ref(OUT / 'registration.json'), 'validation': U.ref(OUT / 'validation.json'), 'sources': len(source_rows),
            'newLiveLedgers': 0, 'newStrategyAccounts': 0, 'goalStatus': 'active'})


if __name__ == '__main__':
    main()
