"""Compare four causal exit/market policies on the existing contraction signals."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'contraction-policy-screen-v1'
SID = 'rawThreeStageContractionBreakoutDiagnostic'
POLICIES = (
    ('baseline', '原退出基线', None, False, .1),
    ('structure10', '跌破前10日结构', 'structure10', False, 0.),
    ('noProgress5', '第5日未走强退出', 'noProgress5', False, .1),
    ('market60', '增加市场趋势过滤', None, True, .1),
)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loader():
    spec = importlib.util.spec_from_file_location('_direction_reuse', ROOT / 'scripts/run-direction-account-screen.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def progress(status):
    path = BASE / 'program.json'
    before = path.read_bytes()
    data = json.loads(before)
    with (OUT / ('program-before-' + status + '.json')).open('xb') as handle:
        handle.write(before)
    data['progress']['contractionPolicyScreen'] = {
        'status': status, 'directory': OUT.relative_to(BASE).as_posix(),
        'configurations': 4, 'newPolicyVariants': 3,
        'plan': 'contraction-policy-screen-v1/plan.json',
        'newIndividualStockInvestigation': False, 'certified': False, 'targetAchieved': False}
    if status == 'completed':
        data['progress']['completedFixedCandidateTrials'] += 1
        data['progress']['contractionPolicyScreen']['readout'] = 'contraction-policy-screen-v1/comparison.json'
        data['progress']['nextStage'] = '比较收缩突破退出与市场过滤的实际盈亏分布、两年分段和资金使用，再选下一项有机制依据的改进；同时推进简化早期跟随规则。保持批量策略研究，不逐股排查断点。'
    else:
        data['progress']['nextStage'] = '完成收缩突破四种策略规则比较：原基线、结构退出、走强失败退出、市场趋势过滤；不设盈利门槛。'
    data['updatedAt'] = now()
    temporary = path.with_name('program.contraction-policy.pending.json')
    write(temporary, data)
    assert path.read_bytes() == before
    temporary.replace(path)


def compact(pid, title, count, result, summary):
    closed, account = summary['closedEpisodes'], summary['account']
    return {'id': pid, 'label': title, 'signals': count, 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'],
        'meanLossPct': closed['meanLossReturnPct'], 'closedMeanPct': closed['meanReturnPct'],
        'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'medianReturnPct': closed['medianReturnPct'], 'returnQuantilesPct': closed['returnQuantilesPct'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'feesPaid': account['feesPaid'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'exitReasons': {reason: sum(row.get('reason') == reason for row in result['trades'] if row['side'] == 'sell')
                        for reason in sorted({row.get('reason', '') for row in result['trades'] if row['side'] == 'sell'})},
        'years': [{'id': p['id'], 'closed': p['closedEpisodes']['count'],
            'closedMeanPct': p['closedEpisodes']['meanReturnPct'], 'accountReturnPct': p['returnPct'],
            'maxDrawdownPct': p['maxDrawdownPct'], 'exposurePct': p['exposurePct']['mean']}
            for p in summary['periods'] if p['id'] in ('2022', '2023')]}


def main():
    from service.portfolio_config import normalize_config
    from service.research_account_outcomes import summarize_account
    from service.research_structural_exits import ResearchExitAccount
    from service.research_market_filter import filter_by_benchmark_ma60
    OUT.mkdir(exist_ok=False)
    parent = loader()
    helper = parent.load_helper()
    signals, source = parent.opportunities(SID, 'signal-contraction-price-v1', 131)
    files = ['scripts/run-contraction-policy-screen.py', 'service/research_structural_exits.py',
             'tests/test_research_structural_exits.py', 'service/research_market_filter.py',
             'tests/test_research_market_filter.py']
    plan = {'schema': 'contraction-policy-screen-v1', 'createdAt': now(), 'source': source,
        'sourcePins': {name: digest(ROOT / name) for name in files},
        'sample': '2022-2023; previously used development sample',
        'policies': [{'id': pid, 'label': title, 'extraExit': rule, 'marketFilter': market,
                      'stopLossPct': .05, 'trailingStopPct': trail, 'takeProfitPct': 0, 'maxHoldingDays': 0}
                     for pid, title, rule, market, trail in POLICIES],
        'rules': {
            'structure10': 'After close, close strictly below the low of the preceding ten independent calendar sessions; current day excluded. Require valid positive-volume OHLC observations throughout. Queue next eligible opening exit. Retain ordinary 5-percent stop, disable percent trailing.',
            'noProgress5': 'On holding calendar session five only (entry is one), close <= riskEntryPrice queues next eligible opening exit. Missing/nontrading fifth day is unknown and not retested later. Retain 5-percent stop and 10-percent trailing.',
            'market60': 'Keep only signals whose same-day CSI300 close is strictly above its arithmetic mean over the last 60 calendar sessions including today. Missing/invalid required close is unknown and excluded; no rank backfill.',
            'standardExitPrecedence': 'Existing forced exits and standard stop/trailing rules take precedence over added rules.'},
        'account': {'initialCapital': 500000, 'maxPositions': 5, 'entryWeight': .2,
                    'cash': 'Independent continuous account for each policy; open holdings retained at cutoff.'},
        'fees': 'Same dated fee policy and 10bps slippage per side as the prior direction comparison.',
        'profitHurdles': None, 'parameterSearch': 'Three mechanism changes, one fixed version each; no grid.',
        'scope': 'Exploratory broad strategy comparison; no individual-stock data repair.',
        'certified': False, 'targetAchieved': False}
    write(OUT / 'plan.json', plan)
    progress('running')
    print(json.dumps({'phase': 'load-once', 'policies': 4}), flush=True)
    binding = helper.load_binding()
    raw = helper.load_dataset(binding)
    data, identity = helper.account_view(raw, binding)
    write(OUT / 'source.json', identity)
    filtered = filter_by_benchmark_ma60(data, signals)
    write(OUT / 'market-filter.json', filtered)
    print(json.dumps({'phase': 'market-filter', 'counts': filtered['counts']}), flush=True)
    rows = []
    for pid, title, rule, market, trail in POLICIES:
        start = time.monotonic()
        schedule = filtered['selected'] if market else signals
        config = deepcopy(binding['document']['baseConfig'])
        config.update(stopLossPct=.05, trailingStopPct=trail, takeProfitPct=0., maxHoldingDays=0)
        config['strategies'] = [{'id': SID, 'kind': 'momentum', 'pool': 'base'}]
        config['allocation'] = {'mode': 'single', 'strategyIds': [SID]}
        config = normalize_config(config)
        print(json.dumps({'phase': 'start', 'policy': pid, 'signals': len(schedule)}), flush=True)
        kwargs = {'entry_weight': .2, 'fee_policy': helper.fee_policy(binding['document'])}
        engine = ResearchExitAccount(data, schedule, config, exit_rule=rule, **kwargs) if rule else helper.ProgressAccount(data, schedule, config, **kwargs)
        engine._policy_label = title
        engine.step(20000)
        result, checkpoint = engine.result(), engine.checkpoint()
        assert result['status'] == 'completed'
        summary = summarize_account(result, checkpoint, policy_id=pid, policy_label=title)
        assert not summary['quality']['integrityIssues'], summary['quality']['integrityIssues']
        if pid == 'baseline':
            previous = read(BASE / 'direction-account-screen-v1' / (SID + '.summary.json'))
            assert summary['closedEpisodes']['count'] == previous['closedEpisodes']['count']
            for key in ('netReturnPct', 'maxDrawdownPct', 'finalEquity', 'feesPaid'):
                assert math.isclose(summary['account'][key], previous['account'][key], abs_tol=1e-8), key
        helper.write_gzip(OUT / (pid + '.result.json.gz'), result)
        helper.write_gzip(OUT / (pid + '.checkpoint.json.gz'), checkpoint)
        write(OUT / (pid + '.summary.json'), summary)
        row = compact(pid, title, len(schedule), result, summary)
        row['elapsedSeconds'] = time.monotonic() - start
        rows.append(row)
        print(json.dumps({'phase': 'completed', **{key: row[key] for key in ('id', 'closed', 'closedMeanPct', 'accountReturnPct', 'maxDrawdownPct', 'elapsedSeconds')}}, ensure_ascii=False), flush=True)
        del engine, result, checkpoint, summary
    write(OUT / 'comparison.json', {'schema': 'contraction-policy-comparison-v1', 'completedAt': now(),
        'rows': rows, 'planSha256': digest(OUT / 'plan.json'),
        'limitations': ['Development data and a fixed current-survivor sample; not independent validation.',
            'Same inherited historical-price and execution assumptions; no individual-stock repair in this batch.',
            'Closed-trade averages exclude open positions, whose value remains in the continuous account.',
            'A positive selected development result is not proof of stable profitability.'],
        'certified': False, 'targetAchieved': False})
    assert all(digest(ROOT / name) == value for name, value in plan['sourcePins'].items())
    progress('completed')
    print(json.dumps({'phase': 'all-completed', 'policies': len(rows)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUT.exists() and not (OUT / 'failure.json').exists():
            write(OUT / 'failure.json', {'at': now(), 'error': type(error).__name__, 'message': str(error)})
        raise
