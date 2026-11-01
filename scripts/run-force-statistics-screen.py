"""Compare frozen main/retail proxies and executable entry hypotheses.

All variants share the existing source snapshot, eligibility and account model.
This run investigates observable price-volume pressure, not investor identity.
"""
from collections import Counter, defaultdict
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
OUT = BASE / 'force-statistics-screen-v1'
POLICIES = (
    ('legacyForceTrend', '旧版主散持续主导'),
    ('directionBreakout', '同口径涨跌量突破'),
    ('pressureBreakout', '量价压力突破'),
    ('pressurePullback', '缩量回踩再启动'),
)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def load_helper():
    spec = importlib.util.spec_from_file_location('_force_account_helper', ROOT / 'scripts/run-account-exit-comparison.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def progress(status):
    path = BASE / 'program.json'
    raw = path.read_bytes()
    data = json.loads(raw)
    with (OUT / ('program-before-' + status + '.json')).open('xb') as stream:
        stream.write(raw)
    data['progress']['forceStatisticsScreen'] = {
        'status': status, 'directory': OUT.name, 'configurations': len(POLICIES),
        'newEntryVariants': 3, 'plan': OUT.name + '/plan.json',
        'newIndividualStockInvestigation': False, 'certified': False, 'targetAchieved': False}
    data['researchPriority']['primary'] = 'Optimize observable main/retail price-volume statistics, then compare fixed strategy rules and actual payoff distributions.'
    data['latestUserDiscussion'] = {'date': '2026-10-16',
        'verbatim': '优化主散量能统计口径，然后优化这个策略',
        'status': 'force-statistics-and-strategy-comparison', 'formalGoalCompletionClaim': False}
    if status == 'completed':
        data['progress']['completedFixedCandidateTrials'] += 1
        data['progress']['forceStatisticsScreen']['readout'] = OUT.name + '/comparison.json'
    data['progress']['nextStage'] = '比较主散统计的四条固定策略路径，检查单笔盈亏、分年与账户表现；不把量价代理当成真实资金身份，不预设收益门槛。'
    data['updatedAt'] = now()
    pending = path.with_name('program.force-statistics.pending.json')
    write(pending, data)
    assert path.read_bytes() == raw
    pending.replace(path)


def collect(data, config):
    from service.force_statistics import prepare_force_statistics
    from service.force_statistics_signals import generate_force_stat_signals
    from service.portfolio_signals import DailyScorer

    new_ids = [sid for sid, _ in POLICIES[1:]]
    raw_by_day = {sid: defaultdict(list) for sid in new_ids}
    counts = {sid: Counter() for sid in new_ids}
    feature_coverage = Counter()
    calendar = data['calendar']
    start, end = config['startDate'], config['endDate']
    last_notice = time.monotonic()
    for ordinal, security in enumerate(data['securities'], 1):
        prepared = prepare_force_statistics(security['bars'], calendar=calendar, volume_unit='shares')
        rows = prepared['rows']
        generated = generate_force_stat_signals(rows)
        for row in rows:
            if start <= row['date'] <= end:
                feature_coverage['securitySessions'] += 1
                feature_coverage['pressure20Known' if row.get('pressure20') is not None else 'pressure20Unknown'] += 1
        for sid in new_ids:
            counts[sid].update(generated['counts'][sid])
            for signal in generated['signals'][sid]:
                if start <= signal['signalDate'] <= end:
                    raw_by_day[sid][signal['signalDate']].append({**signal, 'symbol': security['symbol']})
        if time.monotonic() - last_notice >= 25:
            print(json.dumps({'phase': 'statistics', 'securities': ordinal, 'total': len(data['securities'])}), flush=True)
            last_notice = time.monotonic()
    print(json.dumps({'phase': 'statistics-completed', 'coverage': feature_coverage}), flush=True)
    legacy_config = deepcopy(config)
    legacy_config['strategies'] = [{'id': 'legacyForceTrend', 'kind': 'forceTrend', 'pool': 'base'}]
    legacy_config['allocation'] = {'mode': 'single', 'strategyIds': ['legacyForceTrend']}
    scorer = DailyScorer(data, legacy_config)
    schedules = {sid: [] for sid, _ in POLICIES}
    candidate_counts = Counter()
    daily_coverage = []
    with gzip.open(OUT / 'candidates.jsonl.gz', 'xt', encoding='utf-8') as candidates:
        for day in calendar:
            if not start <= day <= end:
                continue
            universe = scorer._qualified_universe(day)
            eligible = {symbol for symbol, value in universe['eligibilityBySymbol'].items() if value.get('eligible') is True}
            legacy = []
            for symbol in sorted(eligible):
                series = scorer.series[symbol]
                result = scorer._signal(series, series.by_date[day], scorer.nodes['legacyForceTrend'])
                if result is not None:
                    legacy.append({'symbol': symbol, 'signalDate': day, 'score': result['score'], 'features': result['strategies']})
            daily_counts = {}
            for sid, _ in POLICIES:
                raw = legacy if sid == 'legacyForceTrend' else raw_by_day[sid].get(day, [])
                admitted = sorted((r for r in raw if r['symbol'] in eligible), key=lambda r: (-r['score'], r['symbol']))
                daily_counts[sid] = {'rawOnsetsOrLegacyDailyConditions': len(raw), 'eligibleCandidates': len(admitted), 'selectedTop5': min(5, len(admitted))}
                candidate_counts[sid] += len(admitted)
                for rank, signal in enumerate(admitted, 1):
                    candidate = {**signal, 'strategyId': sid, 'rank': rank, 'selectedTop5': rank <= 5}
                    candidates.write(json.dumps(candidate, ensure_ascii=False, allow_nan=False) + '\n')
                    if rank <= 5:
                        schedules[sid].append({'signalId': f'{sid}:{day}:{signal["symbol"]}',
                            'strategyId': sid, 'symbol': signal['symbol'], 'signalDate': day, 'originalRank': rank})
            daily_coverage.append({'date': day, 'baseEligible': len(eligible), 'strategies': daily_counts})
            if time.monotonic() - last_notice >= 25:
                print(json.dumps({'phase': 'rank', 'date': day, 'sessions': len(daily_coverage)}), flush=True)
                last_notice = time.monotonic()
    assert len(daily_coverage) == 484
    write(OUT / 'signal-coverage.json', {'featureCoverage': feature_coverage,
        'generatorCountsIncludingWarmup': counts, 'eligibleCandidateCounts': candidate_counts,
        'selectedCounts': {sid: len(rows) for sid, rows in schedules.items()}, 'daily': daily_coverage})
    for sid, rows in schedules.items():
        assert len({r['signalId'] for r in rows}) == len(rows)
        write(OUT / (sid + '.signals.json'), rows)
    print(json.dumps({'phase': 'signals-completed', 'counts': {sid: len(rows) for sid, rows in schedules.items()}}), flush=True)
    return schedules


def compact(sid, label, schedule, summary):
    closed, account = summary['closedEpisodes'], summary['account']
    overall = next(p for p in summary['periods'] if p['id'] == 'overall')
    return {'id': sid, 'label': label, 'signals': len(schedule), 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'],
        'meanLossPct': closed['meanLossReturnPct'], 'closedMeanPct': closed['meanReturnPct'],
        'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'meanExposurePct': overall['exposurePct']['mean'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': p['id'], 'closed': p['closedEpisodes']['count'],
            'closedMeanPct': p['closedEpisodes']['meanReturnPct'], 'accountReturnPct': p['returnPct'],
            'maxDrawdownPct': p['maxDrawdownPct']} for p in summary['periods'] if p['id'] in ('2022', '2023')]}


def main():
    from service.portfolio_config import normalize_config
    from service.research_account_outcomes import summarize_account
    OUT.mkdir(exist_ok=False)
    pins = {name: digest(ROOT / name) for name in (
        'scripts/run-force-statistics-screen.py', 'service/force_statistics.py',
        'service/force_statistics_signals.py', 'tests/test_force_statistics.py', 'tests/test_force_statistics_signals.py')}
    plan = {'schema': 'force-statistics-screen-v1', 'createdAt': now(), 'sourcePins': pins,
        'sample': 'Unchanged 3141-stock/484-session 2022-2023 development snapshot; same existing eligibility policy.',
        'statistics': {'pressure': '(close-previousClose)/(max(high,previousClose)-min(low,previousClose)); zero true range -> 0',
            'weight': 'min(volume/mean(previous 20 complete calendar-session volumes),3)',
            'pressure5And20': 'sum(weight*pressure)/sum(weight) over the same trailing 5/20 slots',
            'direction5And20': 'Same weights and windows, replacing pressure with sign(close-previousClose)',
            'proxyShares': 'support=(1+pressure)/2; opposition=(1-pressure)/2. Not actual account or aggressor flow.',
            'contextOnly': 'Close location, positive-pressure persistence and activity are exposed separately, not secretly added to a score.',
            'missing': 'No compression or zero-fill of missing required calendar slots; no individual-stock repair.'},
        'policies': [{'id': sid, 'label': label} for sid, label in POLICIES],
        'entryRules': {
            'legacyForceTrend': 'Existing unchanged default: main above retail with dominance>=0.1 for 3 observations, main rising; original daily candidate behavior.',
            'directionBreakout': 'direction20>0; last 3 direction5>0; direction5 rises today; close>prior10High.',
            'pressureBreakout': 'Identical to directionBreakout using pressure5/20; isolates magnitude-normalized pressure from sign-only pressure.',
            'pressurePullback': 'pressure20>0; last 3 pressure5>0; pressure5 rises; prior3CloseReturn<0; prior3MeanVolume<preceding10MeanVolume; yesterdayClose>=yesterdayMA20; close>yesterdayHigh.',
            'onset': 'New variants only emit first true row of a continuous condition; false/unknown rearms, while the next emission still requires every input window. Price onset precedes eligibility filtering.',
            'ranking': 'Daily full eligible candidate score descending then symbol ascending; top5 before existing positions, no backfill.',
            'execution': 'T close signal, one T+1 opening attempt with existing execution/participation/T+1 constraints.'},
        'account': {'initialCapital': 500000, 'maxPositions': 5, 'entryWeight': .2},
        'exits': {'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0, 'maxHoldingDays': 0},
        'costs': 'Existing dated fees and 10bps per-side slippage.', 'profitHurdles': None,
        'parameterSearch': 'One fixed implementation of each mechanism; no grid or post-result tuning.',
        'limitations': ['Reused development data and current-survivor universe; not independent validation.',
            'Inherited historical-price and execution assumptions; no individual-stock investigation.',
            'Old and new strategies differ in entry rules and event spacing; only the two breakout variants isolate the pressure formula.',
            'A price-volume proxy does not identify real main/retail accounts or prove accumulation or inevitable appreciation.'],
        'certified': False, 'targetAchieved': False}
    write(OUT / 'plan.json', plan)
    progress('running')
    helper = load_helper()
    print(json.dumps({'phase': 'load-source'}), flush=True)
    binding = helper.load_binding()
    raw = helper.load_dataset(binding)
    data, identity = helper.account_view(raw, binding)
    write(OUT / 'source.json', identity)
    base_config = deepcopy(binding['document']['baseConfig'])
    schedules = collect(data, base_config)
    rows = []
    for sid, label in POLICIES:
        started = time.monotonic()
        config = deepcopy(base_config)
        config.update(stopLossPct=.05, trailingStopPct=.1, takeProfitPct=0., maxHoldingDays=0)
        config['strategies'] = [{'id': sid, 'kind': 'momentum', 'pool': 'base'}]
        config['allocation'] = {'mode': 'single', 'strategyIds': [sid]}
        config = normalize_config(config)
        print(json.dumps({'phase': 'account-start', 'id': sid, 'signals': len(schedules[sid])}), flush=True)
        engine = helper.ProgressAccount(data, schedules[sid], config, entry_weight=.2, fee_policy=helper.fee_policy(binding['document']))
        engine._policy_label = label
        engine.step(20000)
        result, checkpoint = engine.result(), engine.checkpoint()
        assert result['status'] == 'completed'
        summary = summarize_account(result, checkpoint, policy_id=sid, policy_label=label)
        assert not summary['quality']['integrityIssues'], summary['quality']['integrityIssues']
        helper.write_gzip(OUT / (sid + '.result.json.gz'), result)
        write(OUT / (sid + '.summary.json'), summary)
        row = compact(sid, label, schedules[sid], summary)
        row['elapsedSeconds'] = time.monotonic() - started
        rows.append(row)
        print(json.dumps({'phase': 'account-completed', **{k: row[k] for k in ('id', 'closed', 'closedMeanPct', 'accountReturnPct', 'maxDrawdownPct')}}, ensure_ascii=False), flush=True)
        del engine, result, checkpoint, summary
    assert all(digest(ROOT / name) == expected for name, expected in pins.items())
    write(OUT / 'comparison.json', {'schema': 'force-statistics-comparison-v1', 'completedAt': now(),
        'rows': rows, 'planSha256': digest(OUT / 'plan.json'), 'limitations': plan['limitations'],
        'certified': False, 'targetAchieved': False})
    progress('completed')
    print(json.dumps({'phase': 'all-completed', 'policies': len(rows)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUT.exists() and not (OUT / 'failure.json').exists():
            write(OUT / 'failure.json', {'at': now(), 'error': type(error).__name__, 'message': str(error)})
        raise
