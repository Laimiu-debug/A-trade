"""One preregistered entry-frozen volatility stop; four complete account replays.

Modes register / prepare / run are deliberately separate. Nothing searches a
parameter grid or reads candidate outcomes before plan.json is written.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
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
OUT = BASE / 'relative-repair-volatility-stop-v1'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('fixed5', 'meanTR20x2')
LABELS = {'fixed5': '固定5%初始止损', 'meanTR20x2': '入场冻结2倍20日平均真实波幅'}
PINNED = {
    'relative-repair-cost-stress-v1/plan.json': '47b265b50ae7f3e1280bd66af8c7869e6343c63dbf4a84a7ffa18454448570c3',
    'relative-repair-cost-stress-v1/validation.json': '86ab6881b3c387316c0cd5de1ae1c3dbba746d20443f32522209b7710a5abaab',
    'relative-repair-forward-path-v1/plan.json': '4981db0a4240dd3bb9dd5e49efb51b1e8a668cc51e900119544cd8d92452b25c',
    'relative-repair-forward-path-v1/summary.json': 'c8c507225db3af56bca43315e30f62218d77bc9310ff2e95a49a446b4a5a8d8b',
    'relative-repair-forward-path-v1/validation.json': 'ba23eac76b87f012411a3cef3a959faf7f327bc01310b8f0f629e7dc54bb8e1e',
    'relative-repair-forward-path-v1/readout.json': '1a862bffb82006a2bde1e7dc46dce794d030d85cc8830e70fd26af6bbb44e982',
    'relative-repair-context-diagnostic-v1/2022-2023/prior-pressure.json.gz': 'ff5e5090c3e2f1bd449942389c9e535240f5b6017be01ceab2f0f567b11c4327',
    'relative-repair-context-diagnostic-v1/2024-2025/prior-pressure.json.gz': '3971e7d5f8b16c11fcafed66a190ae09edd0c931cb62bd8231ce5b542c8d2fc5',
}
SOURCES = ('scripts/run-relative-repair-volatility-stop.py', 'service/research_volatility_stop.py',
    'tests/test_research_volatility_stop.py', 'scripts/load-market-context-screen.py',
    'scripts/load-supplemental-account-source.py', 'scripts/run-supplemental-force-context.py',
    'scripts/summarize-relative-repair-events.py', 'scripts/summarize-relative-repair-cost-events.py',
    'service/research_structural_exits.py', 'service/research_fixed_signal_account.py',
    'service/research_account_fees.py', 'service/research_account_outcomes.py',
    'service/signal_target_study_fee_profile.py', 'service/research_fee_profile.py',
    'service/portfolio_engine.py', 'service/portfolio_config.py')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def write(path, value):
    with (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) if str(path).endswith('.gz') else Path(path).open('x', encoding='utf-8')) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  indent=None if str(path).endswith('.gz') else 2,
                  separators=(',', ':') if str(path).endswith('.gz') else None)
        stream.write('\n')


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def tr_input(signal, prior, calendar):
    index = calendar.index(signal['signalDate'])
    dates = calendar[max(0, index - 21):index]
    # The frozen pressure document stores signalId as its outer rows key.
    # prepare() checks that complete key order before selecting this record.
    assert prior['symbol'] == signal['symbol']
    assert prior['signalDate'] == signal['signalDate'] and prior['asOfDate'] == calendar[index - 1]
    assert prior['windowDates'] == calendar[max(0, index - 40):index]
    assert len(prior['windowDates']) == len(prior['windowBars'])
    bars = prior['windowBars'][-len(dates):] if dates else []
    issues = []
    if len(dates) != 21:
        issues.append({'date': signal['signalDate'], 'reason': 'insufficientCalendarSlots'})
    for i, (day, bar) in enumerate(zip(dates, bars)):
        if bar is None:
            issues.append({'date': day, 'reason': 'missingBar'})
            continue
        assert bar['date'] == day
        if not positive(bar.get('close')):
            issues.append({'date': day, 'reason': 'invalidClose'})
        if i and (not positive(bar.get('high')) or not positive(bar.get('low')) or bar['high'] < bar['low']):
            issues.append({'date': day, 'reason': 'invalidHighLow'})
    ranges = None if issues else [max(bars[i]['high'], bars[i - 1]['close']) - min(bars[i]['low'], bars[i - 1]['close']) for i in range(1, 21)]
    mean = math.fsum(ranges) / 20 if ranges is not None else None
    compact = {k: signal[k] for k in ('signalId', 'symbol', 'signalDate')}
    compact.update(asOfDate=calendar[index - 1], meanTrueRange20=mean)
    return {**compact, 'windowDates': dates, 'windowBars': bars, 'trueRanges': ranges,
            'inputValid': not issues, 'issues': issues, 'windowSha256': digest({'dates': dates, 'bars': bars})}, compact


def verify_plan():
    plan = read(OUT / 'plan.json')
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for name, expected in mapping.items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    pins = {}
    for name, expected in PINNED.items():
        path = BASE / name
        assert sha(path) == expected, name
        pins[path.relative_to(ROOT).as_posix()] = expected
    parent = read(BASE / 'relative-repair-cost-stress-v1/plan.json')
    for name, expected in parent['sourcePins'].items():
        assert sha(ROOT / name) == expected, name
    for name, expected in parent['frozenInputs'].items():
        assert sha(ROOT / name) == expected, name
        pins[name] = expected
    periods = {}
    for period in PERIODS:
        spec = deepcopy(parent['periods'][period])
        spec.pop('configs')
        prior = BASE / 'relative-repair-context-diagnostic-v1' / period
        for key, name in (('priorPressure', 'prior-pressure.json.gz'), ('pressureBinding', 'pressure-binding.json')):
            spec[key] = ref(prior / name)
            pins[spec[key]['path']] = spec[key]['sha256']
        spec['signalsEvidence'] = ref(BASE / 'relative-repair-screen-v1' / period / 'signals-evidence.json.gz')
        periods[period] = spec
    plan = {'schema': 'relative-repair-volatility-stop-v1', 'createdAt': now(),
        'sourcePins': {p: sha(ROOT / p) for p in SOURCES}, 'frozenInputs': pins, 'periods': periods,
        'modes': list(MODES), 'newCandidateVariants': 1, 'newCandidateAccounts': 2,
        'fixed5EquivalenceReplays': 2, 'accountsNewlyExecuted': 4, 'entryWeight': .2,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'rule': {'windowSlots': 21, 'trueRanges': 20, 'windowEnd': 'signalT-1',
            'mean': 'simpleArithmeticNotWilder', 'multiplier': 2, 'fallbackPct': .05,
            'validity': '21 finite positive closes; last20 finite positive high/low with high>=low; no volume/open/closeInsideRange gate',
            'entryFraction': '2*meanTrueRange20/actualSlippedEntryPrice; require0<pct<1 else original5%; never drop signal',
            'referenceUnits': 'Freeze entry percentage, not a new rolling indicator. Current risk distance=riskEntryPrice*entryPct; inherited preclose unit conversions scale distance.',
            'priority': 'preserve existing pending exit; eligibility/delist/ST > initial stop > trailing10 > exact day5 no progress',
            'otherRules': 'Full original signals/order/rank, 20% budget,5 slots,500000 perperiod,10bps eachside, inherited fees, no profit target or holding expiry.'},
        'execution': ['Freeze this plan before deriving new inputs or running accounts.',
            'All565+624 original opportunities, all nine entry events and both complete periods retained.',
            'Each fixed5 adapter replay must match the entire original result after removing only versions.researchVolatilityStop and research.volatilityStop before running its candidate.',
            'Load each original account source once; independently inspect saved signal-security risk projection later. Full source capture comparison is runner evidence, not independent reload.',
            'Record all held-day risk decisions, entry risk width and quantity*distance; missing volatility falls back to5%.'],
        'evaluation': ['Continuous account return/drawdown, closed payoff distribution and tails, open holdings, capital/exposure and holding times.',
            'All nine event attributions, yearly/half-year paths, exit causes, entry width distribution, fallback count and entry initial risk amount.',
            'Any improvement could be larger risk allowance rather than volatility adaptation itself; wider fixed risk comparison would be a separately registered experiment.'],
        'limitations': ['Both periods already used for development; no pristine holdout or stability certification.',
            'Raw-price fixed survivor mainboard sample, incomplete historic eligibility/rights and simulated opening fills retain earlier flags.',
            'Ten percent trailing operates from entry and may dominate a wider initial stop; day5 no progress remains.',
            'Close-triggered stops execute at a later eligible open; gaps and retry may exceed nominal loss.',
            'Two separate two-year capital paths, never spliced; no individual discontinuity investigation.'],
        'supersedesHypothesisWording': 'Prior next-experiment note fixed distance is clarified as frozen entry percentage under inherited risk-reference unit conversion.',
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'phase': 'registered', **ref(OUT / 'plan.json'), 'candidateVariants': 1, 'newAccounts': 4})


def prepare():
    plan = verify_plan()
    outputs = {}
    for period, spec in plan['periods'].items():
        destination = OUT / period
        destination.mkdir(exist_ok=False)
        signals = read(ROOT / spec['baseline']['signals']['path'])
        source = read(ROOT / spec['priorPressure']['path'])
        binding = read(ROOT / spec['pressureBinding']['path'])
        assert binding['verification']['passed'] and binding['output']['sha256'] == spec['priorPressure']['sha256']
        assert list(source['rows']) == [s['signalId'] for s in signals]
        rows, compact = [], {}
        for signal in signals:
            row, small = tr_input(signal, source['rows'][signal['signalId']], source['sourceCalendar'])
            rows.append(row)
            compact[signal['signalId']] = small
        output = {'schema': 'prior-mean-true-range20-v1', 'planSha256': sha(OUT / 'plan.json'),
            'source': spec['priorPressure'], 'sourceCalendarSha256': digest(source['sourceCalendar']),
            'rows': rows, 'compactInputs': compact,
            'counts': {'opportunities': len(rows), 'validWindows': sum(r['inputValid'] for r in rows),
                       'invalidWindows': sum(not r['inputValid'] for r in rows),
                       'zeroMean': sum(r['meanTrueRange20'] == 0 for r in rows)}}
        write(destination / 'volatility-input.json.gz', output)
        with (destination / 'signals.json').open('xb') as stream:
            stream.write((ROOT / spec['baseline']['signals']['path']).read_bytes())
        outputs[period] = ref(destination / 'volatility-input.json.gz')
        emit({'phase': 'inputs-prepared', 'period': period, **output['counts']})
    write(OUT / 'input-completion.json', {'planSha256': sha(OUT / 'plan.json'), 'outputs': outputs})


def fixed_equivalence(result, baseline):
    clean = deepcopy(result)
    assert clean['versions'].pop('researchVolatilityStop') == 'research-volatility-stop-v1'
    assert clean['research'].pop('volatilityStop')['mode'] == 'fixed5'
    assert clean == baseline, 'Fixed5 adapter changed original economic/decision result'
    return {'passed': True, 'completeResultEqual': True,
            'removedPathsOnly': ['versions.researchVolatilityStop', 'research.volatilityStop'],
            'originalDigest': digest(baseline), 'normalizedReplayDigest': digest(clean)}


def projection(data, signals):
    symbols = sorted({r['symbol'] for r in signals})
    source = {r['symbol']: r for r in data['securities']}
    fields = ('symbol', 'bars', 'listingDate', 'delistDate', 'stByDate', 'precloseByDate',
              'memberHistory', 'memberByDate', 'tradeStatusByDate', 'corporateActions')
    assert not data.get('corporateActions') and not any(s.get('corporateActions') for s in data['securities'])
    result = {'calendar': deepcopy(data['calendar']), 'securities': [
        {key: deepcopy(source[s][key]) for key in fields if key in source[s]} for s in symbols]}
    if 'membershipByDate' in data:
        result['membershipByDate'] = {day: [s for s in members if s in symbols]
            for day, members in data['membershipByDate'].items()}
        assert list(result['membershipByDate']) == list(data['membershipByDate'])
        for day, members in data['membershipByDate'].items():
            assert result['membershipByDate'][day] == [s for s in members if s in symbols]
    comparisons = []
    for item in result['securities']:
        for field in item:
            assert item[field] == source[item['symbol']][field]
            comparisons.append({'symbol': item['symbol'], 'field': field, 'equal': True,
                                'sha256': digest(item[field])})
    assert result['calendar'] == data['calendar']
    return result, comparisons


def distribution(values):
    values = sorted(values)
    def q(f):
        if not values:
            return None
        at = (len(values) - 1) * f
        lo, hi = int(math.floor(at)), int(math.ceil(at))
        return values[lo] + (values[hi] - values[lo]) * (at - lo)
    return {'count': len(values), 'mean': math.fsum(values) / len(values) if values else None,
            **{key: q(f) for key, f in (('min', 0), ('p05', .05), ('p25', .25), ('median', .5), ('p75', .75), ('p95', .95), ('max', 1))}}


def risk_stats(result, summary):
    bindings = result['research']['volatilityStop']['entryBindings']
    return {'actualEntries': len(bindings), 'entryStopPct': distribution([100 * r['effectivePct'] for r in bindings]),
        'entryInitialRiskAmount': distribution([r['entryInitialRiskAmount'] for r in bindings]),
        'tighterThan5': sum(r['effectivePct'] < .05 for r in bindings),
        'equalTo5': sum(r['effectivePct'] == .05 for r in bindings),
        'widerThan5': sum(r['effectivePct'] > .05 for r in bindings),
        'atLeastTrailing10': sum(r['effectivePct'] >= .1 for r in bindings),
        'fallbackCount': sum(r['fallback'] for r in bindings),
        'fallbackReasons': dict(Counter(r['fallbackReason'] for r in bindings if r['fallback'])),
        'queuedExitReasons': dict(Counter(r['reason'] for r in result['decisions'] if r.get('action') == 'sell' and r.get('status') == 'queued')),
        'closedReturnPct': distribution([r['netReturnPct'] for r in summary['closedEpisodes']['episodes']]),
        'lossReturnPct': distribution([r['netReturnPct'] for r in summary['closedEpisodes']['episodes'] if r['netReturnPct'] < 0]),
        'entryRiskMeaning': 'Quantity*initialDistance, excluding fees and gap risk; varying equity/budgets mean fixed20% capital is not fixed risk.'}


def run():
    from service.research_volatility_stop import VolatilityStopAccount
    from service.research_account_outcomes import summarize_account
    plan = verify_plan()
    inputs = read(OUT / 'input-completion.json')
    assert inputs['planSha256'] == sha(OUT / 'plan.json')
    write(OUT / 'run-start.json', {'startedAt': now(), 'planSha256': sha(OUT / 'plan.json')})
    loader = module('_vol_loader', 'load-market-context-screen.py')
    compact = module('_vol_compact', 'run-supplemental-force-context.py').compact
    attribution = module('_vol_attribution', 'summarize-relative-repair-events.py')
    cost_events = module('_vol_paths', 'summarize-relative-repair-cost-events.py')
    rows, event_periods, artifacts = [], {}, {}
    for period, spec in plan['periods'].items():
        destination = OUT / period
        input_path = ROOT / inputs['outputs'][period]['path']
        assert sha(input_path) == inputs['outputs'][period]['sha256']
        volatility = read(input_path)
        signals = read(destination / 'signals.json')
        assert sha(destination / 'signals.json') == spec['baseline']['signals']['sha256']
        baseline = read(ROOT / spec['baseline']['result']['path'])
        raw, data, identity, fee = loader.load_account_source(period, progress=emit)
        assert identity == spec['sourceIdentity']
        del raw
        captured, field_comparisons = projection(data, signals)
        write(destination / 'risk-source.json.gz', captured)
        write(destination / 'risk-source-binding.json', {'planSha256': sha(OUT / 'plan.json'),
            'sourceIdentity': identity, 'accountDatasetDigest': identity['accountDatasetDigest'],
            'output': ref(destination / 'risk-source.json.gz'), 'symbols': [s['symbol'] for s in captured['securities']],
            'calendarSha256': digest(data['calendar']), 'membershipProjectionCompared': True,
            'membershipSourceDigest': digest(data.get('membershipByDate', {})), 'declaredActions': 0,
            'fieldComparisons': field_comparisons, 'verification': {'passed': True,
                'scope': 'Runner compared every projected security field to currently loaded pinned account data; later auditor uses projection, not full reload.'}})
        write(destination / 'source.json', identity)
        del captured, field_comparisons
        evidence = read(ROOT / spec['signalsEvidence']['path'])
        events, membership = attribution.event_structure(evidence)
        event_ids = [e['eventId'] for e in events]
        event_periods[period] = {'events': events, 'accounts': {}}
        baseline_view = None
        for mode in MODES:
            emit({'phase': 'account-start', 'period': period, 'mode': mode})
            start = time.monotonic()
            engine = VolatilityStopAccount(data, signals, deepcopy(spec['config']),
                volatility_inputs=volatility['compactInputs'], mode=mode, entry_weight=.2, fee_policy=fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            assert result['status'] == 'completed' and result['config'] == spec['config']
            assert result['dataDigest'] == identity['accountDatasetDigest']
            assert result['research']['identity']['signalSchedule'] == signals
            if mode == 'fixed5':
                equivalent = fixed_equivalence(result, baseline)
                write(destination / 'fixed5-equivalence.json', equivalent)
            summary = summarize_account(result, checkpoint, policy_id=mode, policy_label=LABELS[mode])
            assert not summary['quality']['integrityIssues']
            for suffix, value in (('.result.json.gz', result), ('.checkpoint.json.gz', checkpoint), ('.summary.json', summary)):
                write(destination / (mode + suffix), value)
            stats = risk_stats(result, summary)
            write(destination / (mode + '.risk-statistics.json'), stats)
            row = compact(mode, LABELS[mode], signals, summary)
            row.pop('phases', None)
            row.update(period=period, riskStatistics=stats, elapsedSeconds=time.monotonic() - start)
            rows.append(row)
            attributed, combined = attribution.attribute_account(mode, signals, membership, event_ids, result, summary)
            event_periods[period]['accounts'][mode] = {'events': attributed, 'combined': combined}
            view = cost_events.execution_views(result, attributed, attribution)
            if mode == 'fixed5':
                baseline_view = view
            else:
                paths = cost_events.compare_execution(baseline_view, view, membership, event_ids, attribution)
                paths['baseline'] = 'fixed5'
                paths['meaning'] = 'Fixed signal slots; independent full accounts with only initial stop rule changed.'
                event_periods[period]['pathComparison'] = paths
            emit({'phase': 'account-complete', **{key: row[key] for key in ('period', 'id', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
            del engine, result, checkpoint, summary
        artifacts[period] = {p.name: sha(p) for p in destination.iterdir() if p.is_file()}
        del data, baseline, volatility
    write(OUT / 'event-outcomes.json', {'schema': 'volatility-stop-event-outcomes-v1', 'periods': event_periods,
        'planSha256': sha(OUT / 'plan.json'), 'eventMeaning': 'All original entry-event cohorts; means are closed episode returns, not event portfolio returns.', 'certified': False})
    write(OUT / 'comparison.json', {'schema': 'relative-repair-volatility-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'eventOutcomes': ref(OUT / 'event-outcomes.json'), 'candidateVariants': 1,
        'candidateAccounts': 2, 'fixed5EquivalenceReplays': 2, 'newAccounts': 4,
        'certified': False, 'targetAchieved': False})
    emit({'phase': 'all-accounts-complete', **ref(OUT / 'comparison.json')})


def self_test():
    calendar = [f'2020-01-{i:02d}' for i in range(1, 24)]
    signal = {'signalId': 's', 'symbol': 'x', 'signalDate': calendar[21]}
    prior = {'symbol': signal['symbol'], 'signalDate': signal['signalDate'], 'asOfDate': calendar[20], 'windowDates': calendar[:21],
             'windowBars': [{'date': d, 'close': 10., 'high': 11., 'low': 9., 'volume': 0} for d in calendar[:21]]}
    row, small = tr_input(signal, prior, calendar)
    assert row['inputValid'] and small['meanTrueRange20'] == 2 and len(row['trueRanges']) == 20
    prior['windowBars'][3] = None
    assert tr_input(signal, prior, calendar)[1]['meanTrueRange20'] is None
    assert distribution([1, 3])['median'] == 2
    base = {'versions': {'engine': 'x'}, 'research': {'identity': 1}, 'trades': [{'amount': 10}]}
    changed = deepcopy(base)
    changed['versions']['researchVolatilityStop'] = 'research-volatility-stop-v1'
    changed['research']['volatilityStop'] = {'mode': 'fixed5'}
    assert fixed_equivalence(changed, base)['passed']
    changed['trades'][0]['amount'] = 11
    try:
        fixed_equivalence(changed, base)
    except AssertionError:
        pass
    else:
        raise AssertionError('Changed economic result accepted')
    emit({'selfTestsPassed': True, 'checks': ['20 simple TR', 'missing retained', 'linear quantiles', 'only exact metadata removal', 'economic mutation rejected']})


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('self-test', 'register', 'prepare', 'run'))
    args = parser.parse_args()
    try:
        {'self-test': self_test, 'register': register, 'prepare': prepare, 'run': run}[args.mode]()
    except BaseException as exc:
        if args.mode == 'run' and OUT.is_dir() and not (OUT / 'failure.json').exists():
            write(OUT / 'failure.json', {'at': now(), 'type': type(exc).__name__, 'message': str(exc), 'preserved': True})
        raise
