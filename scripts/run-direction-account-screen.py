"""Small multi-entry comparison using existing complete, pre-active signals.

Reuse the established account and fee models; no per-stock data investigation.
This is exploratory development screening, not a certified strategy ranking.
"""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
OUTPUT = BASE / 'direction-account-screen-v1'
STRATEGIES = (
    ('momentum', '动量基线', 'signal-baseline-v1', 2335),
    ('rawPersistentPriceLeaderBreakoutDiagnostic', '持续强势突破', 'signal-persistent-leader-v1', 856),
    ('rawLeaderFollowerConfirmation', '龙头跟随确认', 'signal-leader-follower-confirmation-v1', 10),
    ('rawLeaderLocalSuccession', '局部接位', 'signal-leader-local-succession-v1', 0),
    ('rawThreeStageContractionBreakoutDiagnostic', '波动收缩突破', 'signal-contraction-price-v1', 131),
    ('rawEarlyStrengthEmergence', '早期转强', 'signal-early-strength-emergence-v1', 423),
)
USER_STEERING = '跑偏了，不要关心个别股票断点这种问题，只要大方向没有问题就好了'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def load_helper():
    spec = importlib.util.spec_from_file_location('_direction_account_helpers', ROOT / 'scripts/run-account-exit-comparison.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def opportunities(sid, directory, expected):
    path = BASE / directory / 'attempts/attempt-0001/signals.jsonl.gz'
    rows = []
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            row = json.loads(line)
            if row['strategyId'] == sid:
                day, symbol = row['signalDate'], row['symbol']
                rows.append({'signalId': f'{sid}:{day}:{symbol}', 'strategyId': sid,
                             'signalDate': day, 'symbol': symbol, 'originalRank': row['rank']})
    rows.sort(key=lambda row: (row['signalDate'], row['originalRank'], row['symbol']))
    assert len(rows) == expected, (sid, len(rows), expected)
    assert len({(r['signalDate'], r['symbol']) for r in rows}) == len(rows)
    assert len({(r['signalDate'], r['originalRank']) for r in rows}) == len(rows)
    assert all(type(r['originalRank']) is int and 1 <= r['originalRank'] <= 5 for r in rows)
    return rows, {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'count': len(rows)}


def update_priority(plan, *, completed=False):
    path = BASE / 'program.json'
    raw = path.read_bytes()
    value = json.loads(raw)
    backup = OUTPUT / ('program-before-completion.json' if completed else 'program-before-priority-change.json')
    with backup.open('xb') as handle:
        handle.write(raw)
    value['latestUserDiscussion'] = {'date': '2026-10-16', 'verbatim': USER_STEERING,
        'status': 'strategy-direction-screening-first', 'formalGoalCompletionClaim': False}
    value['researchPriority'] = {
        'primary': 'Batch strategy directions, measure actual payoff distributions and account paths, then improve promising rules.',
        'individualStockDiscontinuityInvestigation': 'stopped-by-user-priority',
        'corporateActionLedgerDevelopment': 'deferred;not-a-prerequisite-for-exploratory-screening',
        'qualityScope': 'Retain causal signals, costs, capital and open positions. Preserve existing aggregate quality flags; investigate further only when a systemic effect could reverse broad conclusions.',
        'profitHurdles': None,
    }
    progress = value['progress']
    progress['directionAccountScreen'] = {'status': 'completed' if completed else 'running',
        'directory': OUTPUT.relative_to(BASE).as_posix(), 'strategies': len(STRATEGIES),
        'plan': 'direction-account-screen-v1/plan.json',
        'newCorporateActionWork': False, 'certified': False, 'targetAchieved': False}
    if completed:
        progress['completedFixedCandidateTrials'] += 1
        progress['directionAccountScreen']['readout'] = 'direction-account-screen-v1/comparison.json'
        progress['nextStage'] = '根据六方向同口径粗筛结果，优先改进入场结构和市场状态适配；低信号覆盖的龙头跟随/接位策略先简化必要条件。继续批量试验，不再逐股核查断点，不设平均盈利或单笔30%的门槛。'
    else:
        progress['nextStage'] = '立即运行六个入场方向的同口径账户比较；复用完整信号和现有回测，不开展个股断点与权益账本专项。'
    value['updatedAt'] = now()
    pending = path.with_name('program.direction-screen.pending.json')
    write(pending, value)
    assert path.read_bytes() == raw
    pending.replace(path)


def compact(sid, label, summary, count):
    closed, account = summary['closedEpisodes'], summary['account']
    return {'strategyId': sid, 'label': label, 'opportunities': count,
        **{key: closed[key] for key in ('count', 'winRatePct', 'meanWinReturnPct', 'meanLossReturnPct', 'meanReturnPct', 'payoffRatio', 'profitFactor', 'returnQuantilesPct')},
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'openPositions': summary['openPositions']['count'],
        'valuationUnreliableDays': len(summary['quality']['valuationUnreliableDates']),
        'periods': [{k: p[k] for k in ('id', 'start', 'end') if k in p} |
            {'account': p.get('account'), 'closedCount': p['closedEpisodes']['count'],
             'closedMeanReturnPct': p['closedEpisodes']['meanReturnPct']} for p in summary['periods']]}


def main():
    OUTPUT.mkdir(exist_ok=False)
    helper = load_helper()
    from service.portfolio_config import normalize_config
    from service.research_account_outcomes import summarize_account
    schedules, specifications = {}, []
    for sid, label, directory, expected in STRATEGIES:
        rows, source = opportunities(sid, directory, expected)
        schedules[sid] = rows
        specifications.append({'strategyId': sid, 'label': label, 'source': source})
    plan = {'schema': 'direction-account-screen-v1', 'createdAt': now(), 'userSteering': USER_STEERING,
        'strategies': specifications, 'sample': '2022-2023 previously used development data',
        'entry': 'Complete pre-active Top5; close signal, single next-session opening attempt; preserve rank gaps.',
        'account': {'initialCapital': 500000, 'maxPositions': 5, 'entryWeight': .2},
        'exits': {'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0, 'maxHoldingDays': 0},
        'exitMeaning': 'Common comparison rule, not a promised realized loss or profitability gate.',
        'fees': 'Reuse dated account fee policy and 10bps per-side slippage from prior account comparison.',
        'profitHurdles': None, 'sourceRunnerSha256': sha(Path(__file__)),
        'limitations': ['Exploratory relative screening on previously inspected data.',
            'Existing historical-universe, raw-price and opening-execution limitations remain; no per-stock repair is performed.',
            'Unclosed positions remain in account value. Empty or sparse signals do not establish a winning or losing strategy.'],
        'certified': False, 'targetAchieved': False}
    write(OUTPUT / 'plan.json', plan)
    update_priority(plan)
    print(json.dumps({'phase': 'load-once', 'strategies': len(STRATEGIES)}), flush=True)
    binding = helper.load_binding()
    raw_data = helper.load_dataset(binding)
    data, identity = helper.account_view(raw_data, binding)
    write(OUTPUT / 'source.json', identity)
    comparisons = []
    for sid, label, _, _ in STRATEGIES:
        start = time.monotonic()
        config = deepcopy(binding['document']['baseConfig'])
        config.update(stopLossPct=.05, trailingStopPct=.1, takeProfitPct=0., maxHoldingDays=0)
        config['strategies'] = [{'id': sid, 'kind': 'momentum', 'pool': 'base'}]
        config['allocation'] = {'mode': 'single', 'strategyIds': [sid]}
        config = normalize_config(config)
        print(json.dumps({'phase': 'start', 'strategy': sid, 'opportunities': len(schedules[sid])}), flush=True)
        engine = helper.ProgressAccount(data, schedules[sid], config, entry_weight=.2,
                                        fee_policy=helper.fee_policy(binding['document']))
        engine._policy_label = label
        engine.step(20000)
        result, state = engine.result(), engine.checkpoint()
        assert result['status'] == 'completed'
        summary = summarize_account(result, state, policy_id=sid, policy_label=label)
        assert not summary['quality']['integrityIssues'], summary['quality']['integrityIssues']
        helper.write_gzip(OUTPUT / (sid + '.result.json.gz'), result)
        write(OUTPUT / (sid + '.summary.json'), summary)
        row = compact(sid, label, summary, len(schedules[sid]))
        row['elapsedSeconds'] = time.monotonic() - start
        comparisons.append(row)
        print(json.dumps({'phase': 'completed', **{k: row[k] for k in ('label', 'count', 'meanReturnPct', 'accountReturnPct', 'elapsedSeconds')}}, ensure_ascii=False), flush=True)
        del engine, result, state, summary
    write(OUTPUT / 'comparison.json', {'schema': 'direction-account-comparison-v1', 'completedAt': now(),
        'rows': comparisons, 'planSha256': sha(OUTPUT / 'plan.json'),
        'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
    update_priority(plan, completed=True)
    print(json.dumps({'phase': 'all-completed', 'strategies': len(comparisons)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUTPUT.exists() and not (OUTPUT / 'failure.json').exists():
            write(OUTPUT / 'failure.json', {'at': now(), 'error': type(error).__name__, 'message': str(error)})
        raise
