"""One frozen relative-repair rule, its matched subset, and contemporaneous control."""
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
OUT = BASE / 'relative-repair-screen-v1'
PERIODS = ('2022-2023', '2024-2025')
SID = 'relativeRepairEventV1'
GROUPS = (('allRepaired', '领先修复全部机会'), ('repaired', '领先修复配对部分'),
          ('matchedControl', '同期尚未修复对照'))


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write(path, value):
    with (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) if str(path).endswith('.gz')
          else path.open('x', encoding='utf-8')) as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False,
                  separators=(',', ':') if str(path).endswith('.gz') else None,
                  indent=None if str(path).endswith('.gz') else 2)
        handle.write('\n')


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def project(raw, data):
    """Only raw signal fields on the independently saved account calendar."""
    days = set(data['calendar'])
    def series(value):
        return {'symbol': value['symbol'], 'bars': [
            {key: bar.get(key) for key in ('date', 'close', 'volume', 'amount')}
            for bar in value['bars'] if bar['date'] in days]}
    return {'calendar': list(data['calendar']), 'benchmark': series(raw['benchmark']),
            'securities': [series(value) for value in raw['securities']],
            'sourceDatasetFingerprint': raw['fingerprint'],
            'priceBasis': 'raw-unadjusted;no-future-reference-normalization'}


def progress(status, completed=False):
    path = BASE / 'program.json'
    before = path.read_bytes()
    value = json.loads(before)
    with (OUT / ('program-before-' + status + '.json')).open('xb') as handle:
        handle.write(before)
    entry = {'status': status, 'directory': OUT.name, 'newEntryVariants': 1,
             'matchedSubsetDiagnostics': 1, 'controlVariants': 1, 'configurationsNewlyExecuted': 6,
             'plan': OUT.name + '/plan.json', 'holdoutClaim': False,
             'certified': False, 'targetAchieved': False}
    if completed:
        value['progress']['completedFixedCandidateTrials'] += 1
        entry['readout'] = OUT.name + '/comparison.json'
    value['progress']['relativeRepairScreen'] = entry
    value['progress']['nextStage'] = '固定共同指数回撤事件、冻结候选名单，检验领先修复及同期配对对照；完成两时期账户和独立核算后判断。'
    value['progress']['lastTurnClassification'] = 'progress: prior four-filter experiment completed and independently verified; its negative transfer evidence motivates this separately registered repair-event comparison.'
    value['updatedAt'] = now()
    pending = path.with_name('program.relative-repair.pending.json')
    write(pending, value)
    assert path.read_bytes() == before
    pending.replace(path)


def main():
    from service.research_relative_repair import build_relative_repair
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    tested_sources = {
        'service/research_relative_repair.py': 'b05722589474e96298d11491f5b848ae38f202fb6c5005080250e0995916e05b',
        'tests/test_research_relative_repair.py': 'b446f9dafab7d0eccd0960714ea4878bc2c9cc4e09ef13df26d83d4181c1711a',
    }
    assert all(sha(ROOT / name) == value for name, value in tested_sources.items())
    loader = module('_relative_repair_load', 'load-market-context-screen.py')
    compact = module('_relative_repair_compact', 'run-supplemental-force-context.py').compact
    baselines = {period: loader.load_baseline(period) for period in PERIODS}
    pins = {name: sha(ROOT / name) for name in (
        'scripts/run-relative-repair-screen.py', 'service/research_relative_repair.py',
        'tests/test_research_relative_repair.py', 'scripts/load-market-context-screen.py',
        'scripts/load-supplemental-account-source.py', 'scripts/run-supplemental-force-context.py',
        'service/research_structural_exits.py', 'service/research_fixed_signal_account.py',
        'service/research_account_fees.py', 'service/research_account_outcomes.py',
        'service/signal_target_study_fee_profile.py', 'service/research_fee_profile.py',
        'service/portfolio_engine.py', 'service/portfolio_config.py')}
    configurations = {}
    for period, baseline in baselines.items():
        config = deepcopy(baseline['result']['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        configurations[period] = config
    OUT.mkdir(exist_ok=False)
    rules = {
        'event': 'Start flat/armed at each target period. At T with 20 complete positive prior benchmark closes, latest tied maximum is P, anchor=close(P); trigger close(T)<=0.95*anchor. No trigger-day signal. No overlapping events.',
        'eventClock': 'Observe trigger+1 through trigger+40 inclusive. Benchmark close>=anchor ends event before stock tests. Day40 may emit stock signals then expires. Subsequent strict new20 high re-arms; an end day may re-arm but never trigger again that day. A local re-arm need not recover an earlier event peak.',
        'missingIndex': 'Active missing/nonpositive index close ends event as unknown; retain unarmed until a later known complete20 window plus strict new high. Missing flat history does not create an event.',
        'frozenPopulation': 'At event trigger use P-60..P61 aligned positive finite stock closes and P-19..P20 positive finite amounts. Rank all such valid stocks by return60 descending, symbol ascending; take ceil(validN*0.2), then retain positive return60. No outcome or present-day active holding filter.',
        'features': 'Reference=stock close(P), return60=close(P)/close(P-60)-1, meanAmount20 includes P. Freeze both ascending midrank percentiles over same full valid population: (count(values<x)+0.5*count(values==x))/N; N=1 gives0.5; ties not split by symbol.',
        'firstRepair': 'For each frozen stock, first adjacent-calendar close(previous)<reference<=close(T) after trigger, while benchmark still below anchor. Any missing/nonpositive stock close from trigger before first crossing makes that stock unknown for this event. Trigger-day above-reference prices do not retroactively count a prior repair.',
        'consume': 'Every first price crossing is consumed, including zero/unknown volume, non-Top5, unpaired, held or unfilled cases. Positive finite signal-day volume is required for entry eligibility. Do not delay or repeat a first crossing.',
        'ranking': 'Eligible first crossings ranked frozen meanAmount20 descending, symbol ascending. Original daily Top5 before matching or account state; no backfill.',
        'matching': 'In Top5 order choose an unused-in-this-event control from same frozen population, no prior first cross or stock-unknown termination, current close below reference and positive finite volume. Minimize absolute return60-percentile difference plus amount-percentile difference; tie symbol. No caliper. Consume control at pairing, not fill. A control may become a later repaired stock.',
        'accounts': 'allRepaired uses all original Top5; repaired uses only matched Top5 with original ranks; matchedControl uses corresponding controls at those ranks. Pairing shortfalls remain explicit. Actual fills are not guaranteed one-to-one pairs.',
    }
    plan = {'schema': 'relative-repair-screen-v1', 'createdAt': now(), 'sourcePins': pins,
        'preRunValidation': {'syntheticTestsPassed': 18, 'testedSources': tested_sources,
            'command': 'python -m unittest discover -s tests -p test_research_relative_repair.py -v',
            'methodReview': 'Independent pre-run review found no blocking runner mismatch; pure module synthetic cases cover clock, missing data, freeze, first-cross consumption, matching, future append and input immutability. No real outcome inspected for this rule.'},
        'strategyId': SID, 'periods': {p: {'config': configurations[p], 'sourceIdentity': b['sourceIdentity'],
            'baselineFiles': b['files'], 'sampleClaim': 'Previously inspected historical data, not pristine out of sample.'}
            for p, b in baselines.items()}, 'rules': rules,
        'previousGoalTurn': {'classification': 'progress', 'evidence': 'market-context-screen-v1/validation.json',
            'sha256': sha(BASE / 'market-context-screen-v1/validation.json')},
        'distinctness': 'Related repair/resilience hypothesis family has been tested. This version uses a fixed index peak/decline event, a frozen peak-date strength cohort and own-price repair while index remains unrepaired; it does not require old breadth-flip/Q history or a new breakout.',
        'newEntryVariants': 1, 'matchedSubsetDiagnostics': 1, 'controlVariants': 1,
        'configurationsNewlyExecuted': 6, 'parameterSearch': False, 'profitHurdles': None,
        'retrospectivePhaseLabelsUsed': False, 'costStressNewlyExecuted': False,
        'execution': 'Unchanged noProgress5 account: independent 500000 per group and period, max5 positions,20% close-time budget,next-session opening attempt,T+1,10bps each side,5% close stop,10% close trail,day5 no-progress. No fixed take-profit or holding expiry. Event40 observation limit is not a holding limit.',
        'fees': 'Existing dated assumptions2022-23 and Legacy continuation2024-25; no claim of actual broker charge verification.',
        'limitations': ['Fixed3141 surviving mainboard sample; historical listing/ST coverage and complete rights unverified.',
            'Raw-price signal and simulated opening execution inherit limitations; no individual-stock discontinuity investigation.',
            'Both historical periods have already been used; each starts separate capital, not one four-year equity path.',
            'Matching controls only two pre-event covariates, has no distance cap, and does not establish causal market leadership or investor control.',
            'One fixed exploratory rule; neither positive returns nor independent arithmetic verification alone establish stable future profitability.'],
        'certified': False, 'targetAchieved': False}
    write(OUT / 'plan.json', plan)
    progress('running')
    emit({'phase': 'registered-relative-repair', 'accounts': 6, 'planSha256': sha(OUT / 'plan.json')})
    rows, artifacts = [], {}
    for period in PERIODS:
        destination = OUT / period
        destination.mkdir()
        config = configurations[period]
        raw, data, identity, fee = loader.load_account_source(period, progress=emit)
        assert identity == baselines[period]['sourceIdentity']
        write(destination / 'source.json', identity)
        projected = project(raw, data)
        del raw
        assert projected['benchmark']['symbol'] == 'sh000300'
        assert len(projected['securities']) == 3141
        emit({'phase': 'save-signal-projection', 'period': period})
        write(destination / 'signal-input.json.gz', projected)
        write(destination / 'projection-binding.json', {'sourceIdentity': identity,
            'signalInputSha256': sha(destination / 'signal-input.json.gz'),
            'calendarSha256': digest(projected['calendar']),
            'securityCount': len(projected['securities']),
            'barCount': sum(len(s['bars']) for s in projected['securities']),
            'projection': 'Raw date/close/volume/amount restricted to independently saved account calendar; no values interpolated or adjusted.'})
        def signal_progress(value):
            if value['completed'] % 100 == 0 or value['completed'] == value['total']:
                emit({'phase': 'relative-repair-observations', 'period': period, **value})
        evidence = build_relative_repair(projected, start=config['startDate'], end=config['endDate'], progress=signal_progress)
        del projected
        assert set(evidence['schedules']) == {key for key, _ in GROUPS}
        write(destination / 'signals-evidence.json.gz', evidence)
        emit({'phase': 'relative-repair-signals-ready', 'period': period,
              'events': len(evidence['events']), 'signals': {k: len(v) for k, v in evidence['schedules'].items()}})
        for key, label in GROUPS:
            signals = evidence['schedules'][key]
            write(destination / (key + '.signals.json'), signals)
            emit({'phase': 'account-start', 'period': period, 'id': key})
            started = time.monotonic()
            engine = ResearchExitAccount(data, signals, config, exit_rule='noProgress5', entry_weight=.2, fee_policy=fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            assert result['status'] == 'completed'
            summary = summarize_account(result, checkpoint, policy_id=key, policy_label=label)
            assert not summary['quality']['integrityIssues'], summary['quality']['integrityIssues']
            write(destination / (key + '.result.json.gz'), result)
            write(destination / (key + '.summary.json'), summary)
            row = compact(key, label, signals, summary)
            row.pop('phases', None)
            row.update(period=period, elapsedSeconds=time.monotonic() - started)
            rows.append(row)
            emit({'phase': 'account-completed', **{k: row[k] for k in
                ('period', 'id', 'signals', 'closed', 'accountReturnPct', 'maxDrawdownPct')}})
            del engine, result, checkpoint, summary
        artifacts[period] = {p.name: sha(p) for p in destination.iterdir() if p.is_file()}
        del data, evidence
    assert all(sha(ROOT / name) == expected for name, expected in pins.items())
    comparison = {'schema': 'relative-repair-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'limitations': plan['limitations'], 'profitHurdles': None, 'certified': False, 'targetAchieved': False}
    write(OUT / 'comparison.json', comparison)
    progress('completed-awaiting-independent-review', completed=True)
    emit({'phase': 'relative-repair-all-completed', 'accounts': 6})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUT.exists() and not (OUT / 'failure.json').exists():
            write(OUT / 'failure.json', {'at': now(), 'type': type(error).__name__, 'message': str(error)})
        raise
