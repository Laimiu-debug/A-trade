"""Four fixed causal context gates on the full frozen contraction opportunities.

Two separate, previously inspected historical intervals; no pristine holdout,
no retrospective regime labels, no threshold search and no target-return gate.
"""
from __future__ import annotations

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
OUT = BASE / 'market-context-screen-v1'
PERIODS = ('2022-2023', '2024-2025')
GATES = (
    ('breadthMajority', '上涨股票过半'),
    ('breadthImproving', '上涨范围扩大'),
    ('strongCohort', '强势队列延续'),
    ('preBreakoutPressure', '突破前压力转强'),
)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def write(path, value):
    with (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) if str(path).endswith('.gz')
          else path.open('x', encoding='utf-8')) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  indent=None if str(path).endswith('.gz') else 2)
        stream.write('\n')


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def emit(row):
    print(json.dumps(row, ensure_ascii=False), flush=True)


def prior_pressure(raw, signals):
    """Use T-1 in the supplied full independent calendar, never previous bar."""
    from service.force_statistics import prepare_force_statistics
    calendar = raw['calendar']
    assert calendar == sorted(set(calendar))
    index = {day: i for i, day in enumerate(calendar)}
    needed = {r['symbol'] for r in signals}
    bars = {s['symbol']: {b['date']: b for b in s['bars']}
            for s in raw['securities'] if s['symbol'] in needed}
    rows = {}
    for signal in signals:
        day = signal['signalDate']
        i = index[day] - 1
        previous = calendar[i] if i >= 0 else None
        dates = calendar[max(0, i - 39):i + 1] if i >= 0 else []
        window = [deepcopy(bars[signal['symbol']].get(d)) for d in dates]
        current = (prepare_force_statistics([b for b in window if b is not None], calendar=dates)['current']
                   if dates else {})
        assert not dates or dates[-1] == previous < day
        rows[signal['signalId']] = {'symbol': signal['symbol'], 'signalDate': day,
            'asOfDate': previous, 'previousCalendarDate': previous,
            'pressure5': current.get('pressure5'), 'pressure20': current.get('pressure20'),
            'windowDates': dates, 'windowBars': window, 'windowSha256': digest(window),
            'sourceDatasetFingerprint': raw['fingerprint']}
    return {'rows': rows, 'sourceCalendar': calendar, 'sourceCalendarSha256': digest(calendar),
            'meaning': 'Frozen pressure formula on last40 aligned raw slots ending at market-calendar T-1; no T bar.'}


def progress(status, *, comparison=None):
    path = BASE / 'program.json'
    before = path.read_bytes()
    (OUT / ('program-before-' + status + '.json')).write_bytes(before)
    value = json.loads(before)
    value['progress']['marketContextScreen'] = {'status': status, 'directory': OUT.name,
        'newEntryVariants': 4, 'configurationsNewlyExecuted': 8, 'referenceAccountsReused': 2,
        'plan': OUT.name + '/plan.json', 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    if comparison is not None:
        value['progress']['marketContextScreen']['readout'] = OUT.name + '/comparison.json'
        value['progress']['completedFixedCandidateTrials'] += 1
    value['progress']['nextStage'] = '检验收缩机会在事前可观察的广度、强势队列延续和突破前压力条件下的实际盈亏，独立复核两时期结果后决定后续成本压力或新机制。'
    value['updatedAt'] = now()
    pending = path.with_name('program.market-context.pending.json')
    write(pending, value)
    assert path.read_bytes() == before
    pending.replace(path)


def main():
    from service.research_market_context import filter_market_context
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    loader = module('_market_context_load', 'load-market-context-screen.py')
    compact = module('_market_context_compact', 'run-supplemental-force-context.py').compact
    baselines = {period: loader.load_baseline(period) for period in PERIODS}
    pin_names = ('scripts/run-market-context-screen.py', 'scripts/load-market-context-screen.py',
        'service/research_market_context.py', 'tests/test_research_market_context.py',
        'service/force_statistics.py', 'service/research_structural_exits.py',
        'service/research_fixed_signal_account.py', 'service/research_account_fees.py',
        'service/research_account_outcomes.py', 'service/signal_target_study_fee_profile.py',
        'service/research_fee_profile.py', 'service/portfolio_engine.py', 'service/portfolio_config.py',
        'scripts/run-account-exit-comparison.py', 'scripts/load-supplemental-account-source.py',
        'scripts/run-supplemental-force-context.py')
    pins = {name: sha(ROOT / name) for name in pin_names}
    configurations = {p: deepcopy(b['result']['config']) for p, b in baselines.items()}
    for config in configurations.values():
        assert (config['initialCapital'], config['maxPositions'], config['maxStockWeight'],
                config['stopLossPct'], config['trailingStopPct'], config['slippageBps'],
                config['takeProfitPct'], config['maxHoldingDays']) == (500000., 5, .2, .05, .1, 10., 0., 0)
    OUT.mkdir(exist_ok=False)
    plan = {'schema': 'market-context-screen-v1', 'createdAt': now(), 'sourcePins': pins,
        'previousGoalTurn': {'classification': 'progress',
            'evidence': 'supplemental-force-context-screen-v1/validation.json',
            'sha256': sha(BASE / 'supplemental-force-context-screen-v1/validation.json')},
        'periods': {p: {'config': configurations[p], 'signalCount': len(b['signals']),
            'baselineFiles': b['files'], 'sourceIdentity': b['sourceIdentity'], 'marketBinding': b['marketBinding'],
            'sampleClaim': 'Previously inspected development or supplemental history; not pristine out of sample.'}
            for p, b in baselines.items()},
        'rules': {
            'breadthMajority': 'At signal close T, positiveCount/evaluableCount > 0.5 in frozen price20; finite positive denominator; no all-3141 denominator substitution.',
            'breadthImproving': 'At T, matched5 available, pairedCount>0 and matchedBreadthChange5>0; comparison uses same intersection at T and T-5, not unpaired breadth.',
            'strongCohort': 'At T, yesterdayCohort available, eligibleCount>0, unknownCount==0, continuedCount>observedFailureCount. Empty available cohort is excluded; unavailable/partly unknown cohort is unknown.',
            'preBreakoutPressure': 'At calendar T-1 only, frozen pressure20>0 and pressure5>pressure20; 40 aligned raw calendar slots, no compression, no signal-day bar.'},
        'entry': 'Each gate independently filters complete original Top5; retain IDs, ranks, repeated dates and gaps; no backfill, no conjunction of gates.',
        'execution': 'Unchanged noProgress5 ResearchExitAccount, 20 percent close-time entry budget, one next-session opening attempt, five positions, T+1, prior-volume participation and cash limits.',
        'exits': '5 percent close stop, 10 percent close trailing, day-five no-progress exit; next eligible open. No profit cap or forced terminal sale.',
        'fees': 'Identical to each period reference: explicit DatedFeePolicy for 2022-2023; declared LegacyFeePolicy continuation for 2024-2025. Both 10bps slippage each side. Broker charges unverified.',
        'newEntryVariants': 4, 'newIndicators': 0, 'configurationsNewlyExecuted': 8,
        'referenceAccountsReused': 2, 'costStressNewlyExecuted': False,
        'evaluation': 'Each period starts a separate 500000 account; years and halves are descriptive continuous-account intervals inside each period. Do not combine into a continuous four-year curve.',
        'priorUse': 'Breadth change is a previously studied market-expansion ingredient on another parent rule; prior-day pressure is a lagged variant of the existing formula. No claim that these are new indicators.',
        'profitHurdles': None, 'parameterSearch': False, 'retrospectiveLabelsUsedAsSignals': False,
        'limitations': ['Fixed 3141 surviving SH/SZ mainboard securities, not the full historical A-share universe.',
            'Both intervals previously inspected; this is mechanism exploration, not independent validation.',
            'Strong price cohort persistence does not establish market influence or investor control.',
            'Missing history, raw-price/rights limitations and simulated opening execution are inherited; no individual-stock repair.',
            'This batch does not newly stress costs; positive results would still need that check and later untouched or prospective evidence.'],
        'certified': False, 'targetAchieved': False}
    write(OUT / 'plan.json', plan)
    progress('running')
    emit({'phase': 'registered-context-screen', 'accounts': 8, 'reused': 2, 'planSha256': sha(OUT / 'plan.json')})
    rows, source_identities = [], {}
    for period in PERIODS:
        destination = OUT / period
        destination.mkdir()
        base = baselines[period]
        config = configurations[period]
        write(destination / 'baseline.summary.json', base['summary'])
        write(destination / 'baseline.signals.json', base['signals'])
        row = compact('baseline', '原收缩机会基线', base['signals'], base['summary'])
        row.pop('phases', None)  # This batch compares only common calendar intervals.
        row.update(period=period, execution='reused-identical-existing-account')
        rows.append(row)
        emit({'phase': 'load-period-source', 'period': period})
        raw, data, identity, fee = loader.load_account_source(period, progress=emit)
        assert identity == base['sourceIdentity']
        source_identities[period] = identity
        write(destination / 'source.json', identity)
        prior = prior_pressure(raw, base['signals'])
        write(destination / 'prior-pressure.json.gz', prior)
        filtered = filter_market_context(base['signals'], base['marketRows'], prior_pressure=prior['rows'])
        assert set(filtered['schedules']) == {key for key, _ in GATES}
        write(destination / 'filter-evidence.json.gz', filtered)
        del raw, prior
        emit({'phase': 'context-schedules-ready', 'period': period,
              'signals': {key: len(value) for key, value in filtered['schedules'].items()}})
        for key, label in GATES:
            schedule = filtered['schedules'][key]
            write(destination / (key + '.signals.json'), schedule)
            emit({'phase': 'account-start', 'period': period, 'id': key})
            started = time.monotonic()
            engine = ResearchExitAccount(data, schedule, config, exit_rule='noProgress5', entry_weight=.2, fee_policy=fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            assert result['status'] == 'completed'
            summary = summarize_account(result, checkpoint, policy_id=key, policy_label=label)
            assert not summary['quality']['integrityIssues'], summary['quality']['integrityIssues']
            write(destination / (key + '.result.json.gz'), result)
            write(destination / (key + '.summary.json'), summary)
            row = compact(key, label, schedule, summary)
            row.pop('phases', None)
            row.update(period=period, execution='new-independent-account', elapsedSeconds=time.monotonic() - started)
            rows.append(row)
            emit({'phase': 'account-completed', **{k: row[k] for k in
                 ('period', 'id', 'signals', 'closed', 'accountReturnPct', 'maxDrawdownPct')}})
            del engine, result, checkpoint, summary
        del data, filtered
    assert all(sha(ROOT / name) == expected for name, expected in pins.items())
    # Rebind small, frozen reference/market files after all account runs.
    for period in PERIODS:
        current = loader.load_baseline(period)
        assert current['files'] == baselines[period]['files']
        assert current['marketBinding'] == baselines[period]['marketBinding']
    comparison = {'schema': 'market-context-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'sourceIdentities': source_identities,
        'limitations': plan['limitations'], 'profitHurdles': None, 'certified': False, 'targetAchieved': False}
    write(OUT / 'comparison.json', comparison)
    progress('completed-awaiting-independent-review', comparison=comparison)
    emit({'phase': 'context-all-completed', 'newAccounts': 8, 'reusedAccounts': 2})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUT.exists() and not (OUT / 'failure.json').exists():
            write(OUT / 'failure.json', {'at': now(), 'type': type(error).__name__, 'message': str(error)})
        raise
