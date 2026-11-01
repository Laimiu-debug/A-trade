"""Bind source progress and original-method notes without claiming alpha."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_public_progress_utils', ROOT / 'scripts/run-liquid-trend-breakout.py')
U = importlib.util.module_from_spec(spec); sys.modules[spec.name] = U; spec.loader.exec_module(U)
RESEARCH = ROOT / 'artifacts/strategy-search/public-trading-evidence-v1'
OUT = RESEARCH / 'progress-002'


def main():
    if OUT.exists(): raise ValueError('Preserve previous progress receipt')
    sse = RESEARCH / 'sse-main-2024q4-003'
    validation = U.read(sse / 'validation.json'); scope = U.read(sse / 'scope-validation.json')
    for record in (validation['plan'], scope['normalizationValidation'], scope['exclusions'], validation['eventsOutput'], validation['seatsOutput']): U.bound(record)
    if not validation['passed'] or not scope['passed'] or validation['daysChecked'] != 66:
        raise ValueError('Complete configured source window required')
    flow = U.read(RESEARCH / 'organizer-cashflow-review-001/readout.json')
    if sum(v['signedAmountYuan'] for v in flow['cashFlows']) != flow['visibleAnnualNetFlowYuan']:
        raise ValueError('Cashflow arithmetic drift')
    for item in flow['images']:
        if U.sha(ROOT / item['file']) != item['sha256']: raise ValueError('Original image drift')
    if not flow['subtotalsAndVisibleTotalAgree'] or flow['accountReturnCalculated']:
        raise ValueError('Do not turn a cashflow subtotal into account profit')
    OUT.mkdir()
    registry_path = ROOT / 'artifacts/strategy-search/goal-continuation-20261018.json'
    registry = U.read(registry_path)
    if (registry['completedFixedCandidateTrialsTotalIncludingPrior'], registry['newAccountsThisContinuation']) != (59, 86):
        raise ValueError('Do not alter existing strategy/account counts')
    (OUT / 'previous-registry.json').write_bytes(registry_path.read_bytes())
    sources = [
        {'id': 'organizer-annual-ledger-share', 'url': 'https://www.tgb.cn/dialog/2eYbNKW3IVz_83172956_1',
         'authority': 'competition-organizer', 'observation': '主办方2025-01-21提供二池及归因2024交割单链接，尚未取得文件内容。'},
        {'id': 'annual-organizer-verification', 'url': 'https://m.tgb.cn/a/2eTfRp1TS2c',
         'authority': 'competition-organizer', 'observation': '主办方发布账户验证声明；后续读者有链接失效疑问，不能据此认定当前文件有效。'},
        {'id': 'half-year-top3-ledgers', 'url': 'https://m.tgb.cn/a/2eJDqpyCdSK-3?type=new',
         'authority': 'competition-organizer', 'observation': '定位归因、ElpMis、LBFLBF前三名及各自文档；取得归因资金流截图，交易账本仍待核验。'},
        {'id': 'half-year-other-ledgers', 'url': 'https://m.tgb.cn/a/2eJDqpyCdSK-2?type=new',
         'authority': 'competition-organizer', 'observation': '同届其他账户及文档链接，能帮助避免仅看冠军。'},
        {'id': 'mangfu-original-notes', 'url': 'https://m.tgb.cn/a/1LOJjiwIO3y',
         'authority': 'trader-first-person', 'observation': '本人将市场周期、龙头角色、预期反馈和仓位联系起来；属于早期经验解释，非主区间收益证据。'},
        {'id': 'mangfu-original-entry', 'url': 'https://m.tgb.cn/a/1G487Wvm8GQ',
         'authority': 'trader-first-person', 'observation': '本人弱转强原帖；尚未形成或验证可执行策略。'},
    ]
    candidate_files = [
        {'name': '归因', 'competitionRank': 1, 'competition': '第六届王者百万杯半年赛', 'url': 'https://kdocs.cn/l/cm5xnCnUZCIZ'},
        {'name': 'ElpMis', 'competitionRank': 2, 'competition': '第六届王者百万杯半年赛', 'url': 'https://kdocs.cn/l/cf05osthzXS4'},
        {'name': 'LBFLBF', 'competitionRank': 3, 'competition': '第六届王者百万杯半年赛', 'url': 'https://kdocs.cn/l/chRGA9ZPSVUJ'},
        {'name': '二池', 'competitionRank': 6, 'competition': '第六届王者百万杯半年赛', 'url': 'https://kdocs.cn/l/cntVsLnAM9Kn'},
    ]
    for row in candidate_files:
        row['contentsAcquired'] = False
        row['currentPermissionAndCompletenessVerified'] = False
    payload = {'schema': 'public-trading-evidence-progress-v2', 'completedAt': U.now(), 'builder': U.ref(Path(__file__)),
        'sourceAdditions': sources, 'candidateLedgerFiles': candidate_files,
        'originalMethodNotes': U.ref(ROOT / 'docs/public-trading-method-evidence-notes-v1.md'),
        'cashflowReview': U.ref(RESEARCH / 'organizer-cashflow-review-001/readout.json'),
        'cashflowRowsInternallyReconciled': len(flow['cashFlows']), 'cashflowTotalYuan': flow['computedVisibleNetFlowYuan'],
        'cashflowTotalIsTradingProfit': False,
        'sseValidation': U.ref(sse / 'validation.json'), 'sseScopeValidation': U.ref(sse / 'scope-validation.json'),
        'sseDays': validation['daysChecked'], 'sseNativeRows': scope['nativeResponseRows'], 'sseStockBrokerageEvents': validation['events'],
        'sseSeatRows': validation['seats'], 'sseExcludedMixedRows': scope['excludedRows'],
        'independentDetailEventChecks': len(validation['crossEndpointChecks']),
        'independentDetailSeatChecks': sum(v['seatsMatched'] for v in validation['crossEndpointChecks']),
        'sourceSchemaFailedAttemptsPreserved': [U.ref(RESEARCH / 'sse-main-2024q4-001/plan.json'), U.ref(RESEARCH / 'sse-main-2024q4-002/plan.json')],
        'browserTriage': {'sharedNodeAndCuaKernelsFailBeforeCodeExecution': True, 'error': 'failed to write kernel assets: path not found (os error 3)',
            'authenticatedPagesRead': False, 'appRestarted': False, 'existingUserLoginsPreserved': True,
            'officialTriageReference': 'https://developers.openai.com/plugins/deploy/troubleshooting'},
        'newStrategyTrials': 0, 'newStrategyAccounts': 0, 'completeLiveAccountLedgersAcquired': 0, 'verifiedClosedAccountEpisodes': 0,
        'newTradingSignalDeployed': False, 'targetAchieved': False,
        'nextActions': ['优先取得主办方原始连续账本，核对现金/持仓/费用/资金流。',
                        '按原帖的完整决策链整理可检验方法，禁止把未经说明的数值阈值写成本人规则。',
                        '营业部仅分析披露之后可知的参与条件和价格路径；后续扩展深交所、科创板。',
                        '完善有日期的主线/题材证据与盘中资料后，才冻结新系统并验证盈亏比。']}
    U.write(OUT / 'readout.json', payload)
    registry['updatedAt'] = U.now(); registry['goalStatus'] = 'active'; registry['targetAchieved'] = False
    registry['activeStudy'].update({'status': 'Progress: organizer top3 ledger links located,12 cashflow rows internally reconciled;66-day SSE-main raw/native schema census validated. No full live ledger or profitable system established.',
        'latestProgress': U.ref(OUT / 'readout.json'), 'goalAchieved': False})
    registry['nextStage'].update({'status': 'Acquire/reconcile organizer original live ledgers and causal decision evidence; continue source-backed whole-system and post-publication brokerage research.',
        'latestProgress': U.ref(OUT / 'readout.json'), 'sseSourceDays': 66, 'sseStockBrokerageEvents': validation['events'],
        'sseSeatRows': validation['seats'], 'completeLiveLedgersAcquired': 0})
    registry['lastGoalTurnClassification'] = 'progress: actual primary organizer links, cashflow image arithmetic and66 official daily source responses acquired;2169 stock brokerage events/21667 ranked rows normalized under official table types with70 rows cross-checked via detail endpoint. Original failed scope assumptions preserved. First-person market-cycle/stock-role/position evidence recorded. No new account or alpha claim; shared browser kernel failure persists, but public source progress continues.'
    pending = registry_path.with_name('goal-continuation-20261018.evidence-progress-pending.json')
    U.write(pending, registry); pending.replace(registry_path)
    U.emit({'readout': U.ref(OUT / 'readout.json'), 'sources': len(sources), 'cashflowRows': len(flow['cashFlows']),
        'sseDays': validation['daysChecked'], 'stockEvents': validation['events'], 'seatRows': validation['seats'],
        'wholeAccountLedgers': 0, 'goalStatus': 'active'})


if __name__ == '__main__': main()
