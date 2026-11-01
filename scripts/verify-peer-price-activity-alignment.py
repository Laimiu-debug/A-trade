"""Independent audit of the frozen-peer price/activity-alignment experiment.

No production alignment function is imported and no account is executed.
Previously audited group/amount evidence is inherited through exact hashes;
eleven-slot source projections, all new arithmetic and schedules are checked
here. The unchanged independent bookkeeping auditor checks saved accounts.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/peer-price-activity-alignment-v1'
PARENT = ROOT / 'artifacts/strategy-search/peer-amount-persistence-v1'
PARENT_VERIFIER = 'scripts/verify-peer-amount-persistence.py'
PARENT_VERIFIER_SHA = 'cfaabb0262a33c7c6a5776f4fb54b7d0298df104abf8374ac87313ec6cccd026'
PARENT_VALIDATION_SHA = 'cd06d5cb09b045a88c1489399fb9d8c6ed816f314b6e721fdf4cb082558b0851'
ALIGNMENT_MODULE = 'service/research_peer_activity_alignment.py'
ALIGNMENT_MODULE_SHA = '1537b837762f34a789479f52e74e1bf050269fb638de2ae009098dc33db640c7'
REGISTERED_PLAN_SHA = '95ed93d9929b3eeaeb5dd8cf7c251f84f0a2cd5f1b7484eb41a908a1fced72ab'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('currentActivity', 'priceProgress', 'activityAligned')
SIGNAL_FIELDS = ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')
SID = 'fixedPeerAmountPersistenceV1'
DIAGNOSTIC_FIELDS = ('absolutePeerLogAdvance', 'benchmarkLogAdvance', 'covActivityPeer',
    'covActivityBenchmark', 'covDecompositionResidual', 'lastCenteredProduct',
    'lastAbsContributionShare', 'covWithoutLastDay')
BASIS = {
    'activity': 'Prior audited daily fixed-peer amount share; same U(T); no market rebuild here.',
    'return': 'Mean of twenty peer close log returns minus benchmark close log return, each F+1..T session.',
    'netR': 'fsum(ten daily relative log returns); descriptive price progression, not portfolio PnL.',
    'covariance': 'fsum((a-meanA)*(r-meanR))/10; population covariance, no variance normalization.',
    'constantSeries': 'Exact constant input series center to zero; no epsilon or near-zero snapping.',
    'policies': 'currentActivity=original control; priceProgress=currentActivity and netR>0; activityAligned=priceProgress and cov>0.',
    'completeness': 'All three policies require the same full original amount and eleven-slot price evidence.',
    'interpretation': 'Same-day co-movement, not investor identity, net inflow, or causal price impact.',
    'timing': 'Computed after T close using F..T only; no future or intraday execution evidence.'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def same(actual, expected, label='root'):
    """Tight new-metric comparison; policy booleans are always exact."""
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-14),
                f'{label}: {actual!r} != {expected!r}')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'{label}: keys differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f'{label}: length differs')
        for i, (a, b) in enumerate(zip(actual, expected)):
            same(a, b, f'{label}[{i}]')
    else:
        require(type(actual) is type(expected) and actual == expected, f'{label}: literal differs')


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz')
          else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def local(relative):
    candidate = Path(relative)
    require(not candidate.is_absolute() and '..' not in candidate.parts, 'Expected repository-relative path')
    candidate = (ROOT / candidate).resolve()
    require(candidate.is_relative_to(ROOT.resolve()), 'Outside repository path')
    return candidate


def pinned(reference):
    require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'Expected path/hash reference')
    path = local(reference['path'])
    require(sha(path) == reference['sha256'], 'Pinned file changed: ' + reference['path'])
    return path


def calendar_check(days, count=None):
    require(isinstance(days, list) and days == sorted(set(days)), 'Calendar is not ordered and unique')
    require(count is None or len(days) == count, 'Wrong calendar slot count')
    for day in days:
        require(type(day) is str and date.fromisoformat(day).isoformat() == day, 'Noncanonical date')


def parent_verifier():
    path = local(PARENT_VERIFIER)
    require(sha(path) == PARENT_VERIFIER_SHA, 'Pinned prior independent verifier changed')
    spec = importlib.util.spec_from_file_location('_alignment_prior_independent_auditor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def window_quality(bars, days):
    """Independent implementation of the frozen OHLCV/window contract."""
    if not isinstance(bars, (list, tuple)) or len(bars) != len(days):
        return 'insufficientAlignedRawHistory', None
    for i in range(len(days)):
        bar = bars[i]
        if bar is None:
            return 'missingAlignedRawBar', i
        if not isinstance(bar, dict) or bar.get('date') != days[i]:
            return 'rawHistoryDateMismatch', i
        if bar.get('priceBasisId') != 'raw-unadjusted':
            return 'rawPriceBasisUnknown', i
        if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
            return 'invalidKnownSuspensionFlag', i
        if bar.get('knownSuspended') is True:
            return ('suspensionContradictsPositiveVolume' if finite(bar.get('volume')) and bar['volume'] > 0
                    else 'knownSuspension'), i
        if any(not finite(bar.get(k)) or bar[k] <= 0 for k in ('open', 'high', 'low', 'close', 'volume')):
            return 'nonpositiveOrUnknownRawOHLCV', i
        if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            return 'invalidRawOHLCEnvelope', i
    return None, None


def log_changes(bars):
    values = []
    try:
        for i in range(1, len(bars)):
            values.append(math.log(bars[i]['close'] / bars[i - 1]['close']))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return values if all(finite(x) for x in values) else None


def stable_sum(values):
    try:
        value = math.fsum(values)
    except (OverflowError, ValueError):
        return None
    return value if finite(value) else None


def paired_stats(activity, returns):
    """Population covariance; exact constants are mathematical zero only."""
    require(len(activity) == len(returns) and len(activity) > 0, 'Unpaired covariance observations')
    require(all(finite(v) for v in activity + returns), 'Covariance input is not finite')
    n = len(activity)
    total_a, total_r = stable_sum(activity), stable_sum(returns)
    if total_a is None or total_r is None:
        return None, 'nonfiniteAggregate'
    mean_a, mean_r = total_a / n, total_r / n
    centered_a = [0.] * n if len(set(activity)) == 1 else [v - mean_a for v in activity]
    centered_r = [0.] * n if len(set(returns)) == 1 else [v - mean_r for v in returns]
    products = [a * r for a, r in zip(centered_a, centered_r)]
    numerator = stable_sum(products)
    if numerator is None:
        return None, 'nonfiniteCovariance'
    return {'meanA': mean_a, 'meanR': mean_r, 'netR': total_r,
            'centeredProducts': products, 'cov': numerator / n}, None


def reference_alignment(anchor, members, days, raw, benchmark, amount):
    calendar_check(days, 11)
    require(type(anchor) is str and anchor.strip() == anchor and bool(anchor), 'Invalid anchor')
    require(isinstance(members, list) and len(members) <= 20 and len(set(members)) == len(members)
            and anchor not in members and all(type(s) is str and s.strip() == s and s for s in members),
            'Invalid frozen group identity')
    require(isinstance(raw, dict), 'Price projection is not a mapping')
    reasons, amount_basis = [], None
    if amount is None:
        control, shares = None, [None] * 10
        reasons.append('missingAmountEvidence')
    else:
        require(amount['version'] == 'research-peer-amount-share-v1' and amount['anchor'] == anchor,
                'Original amount identity differs')
        require(isinstance(amount['members'], list) and len(amount['members']) == len(set(amount['members']))
                and set(amount['members']) == set(members), 'Original amount group differs')
        calendar_check(amount['windowDates'], 30)
        require(amount['windowDates'][19:] == days and amount['formationDate'] == days[0]
                and amount['signalDate'] == days[-1] and amount['observationDates'] == days[1:], 'Amount/price date join')
        require(len(amount['dailyRows']) == 30 and [r['date'] for r in amount['dailyRows']] == amount['windowDates'],
                'Original amount rows lost or reordered')
        control = amount['control']
        require(control is None or type(control) is bool, 'Invalid original control decision')
        require(type(amount['complete']) is bool, 'Original amount completeness missing')
        original_shares = [r.get('share') for r in amount['dailyRows'][-10:]]
        shares = [v if finite(v) and 0 <= v <= 1 else None for v in original_shares]
        if control is None or not amount['complete']:
            reasons.append('originalAmountUnknown')
        if any(v is None for v in shares):
            reasons.append('invalidActivityShare')
        amount_basis = {k: amount.get(k) for k in ('version', 'marketDigest', 'universeSha256')}
    if len(members) != 20:
        reasons.append('incompleteFrozenPeerGroup')
    quality, changes = [], {}
    for symbol in sorted(members):
        reason, slot = window_quality(raw.get(symbol), days)
        series = None if reason else log_changes(raw[symbol])
        if reason is None and series is None:
            reason = 'nonfiniteLogReturn'
        quality.append({'symbol': symbol, 'reason': reason, 'invalidSlot': slot})
        if reason is None:
            changes[symbol] = series
    benchmark_reason, benchmark_slot = window_quality(benchmark, days)
    benchmark_changes = None if benchmark_reason else log_changes(benchmark)
    if benchmark_reason is None and benchmark_changes is None:
        benchmark_reason = 'nonfiniteLogReturn'
    if any(r['reason'] is not None for r in quality):
        reasons.append('incompletePeerPriceWindow')
    if benchmark_reason is not None:
        reasons.append('incompleteBenchmarkPriceWindow')
    daily = [{'date': days[i + 1], 'previousDate': days[i], 'activityShare': shares[i],
              'peerMeanLogReturn': None, 'benchmarkLogReturn': None, 'relativeLogReturn': None,
              'centeredProduct': None, 'status': 'unknown'} for i in range(10)]
    stats = dict.fromkeys(('meanA', 'meanR', 'netR', 'cov', 'centeredProducts'))
    if not reasons:
        averages, relative = [], []
        for i in range(10):
            total = stable_sum(changes[s][i] for s in sorted(members))
            average = total / 20 if total is not None else None
            rel = average - benchmark_changes[i] if average is not None else None
            if not finite(rel):
                reasons.append('nonfiniteRelativeLogReturn')
                break
            averages.append(average)
            relative.append(rel)
        if not reasons:
            computed, issue = paired_stats(shares, relative)
            if issue:
                reasons.append(issue)
            else:
                stats = computed
                for i, row in enumerate(daily):
                    row.update(peerMeanLogReturn=averages[i], benchmarkLogReturn=benchmark_changes[i],
                               relativeLogReturn=relative[i], centeredProduct=stats['centeredProducts'][i], status='known')
    complete = not reasons
    current = control if complete else None
    progress = bool(current and stats['netR'] > 0) if complete else None
    aligned = bool(progress and stats['cov'] > 0) if complete else None
    return {'version': 'research-peer-activity-alignment-v1', 'anchor': anchor, 'members': members,
        'windowDates': days, 'formationDate': days[0], 'signalDate': days[-1], 'observationDates': days[1:],
        'amountBasis': amount_basis, 'originalAmountControl': control, 'dailyRows': daily, **stats,
        'currentActivity': current, 'priceProgress': progress, 'activityAligned': aligned,
        'complete': complete, 'kind': ('unknown' if not complete else 'notActive' if not current else
            'noPositiveProgress' if not progress else 'nonPositiveAlignment' if not aligned else 'aligned'),
        'unknownReasons': reasons, 'priceQuality': {'members': quality,
            'benchmark': {'reason': benchmark_reason, 'invalidSlot': benchmark_slot},
            'helper': 'service.signal_leader_peer_expansion._window/_logs; unchanged'},
        'basis': BASIS, 'certified': False, 'targetAchieved': False}


def reference_diagnostics(alignment):
    if not alignment['complete']:
        return dict.fromkeys(DIAGNOSTIC_FIELDS)
    daily = alignment['dailyRows']
    activity = [r['activityShare'] for r in daily]
    peer = [r['peerMeanLogReturn'] for r in daily]
    benchmark = [r['benchmarkLogReturn'] for r in daily]
    relative = [r['relativeLogReturn'] for r in daily]
    cov_peer = paired_stats(activity, peer)[0]['cov']
    cov_benchmark = paired_stats(activity, benchmark)[0]['cov']
    products = alignment['centeredProducts']
    absolute_products = math.fsum(abs(v) for v in products)
    return {'absolutePeerLogAdvance': math.fsum(peer), 'benchmarkLogAdvance': math.fsum(benchmark),
        'covActivityPeer': cov_peer, 'covActivityBenchmark': cov_benchmark,
        'covDecompositionResidual': alignment['cov'] - (cov_peer - cov_benchmark),
        'lastCenteredProduct': products[-1],
        'lastAbsContributionShare': abs(products[-1]) / absolute_products if absolute_products > 0 else None,
        'covWithoutLastDay': paired_stats(activity[:-1], relative[:-1])[0]['cov']}


def source_index(dataset, selected):
    calendar_check(dataset['calendar'])
    securities = {s['symbol']: s for s in dataset['securities']}
    require(len(securities) == len(dataset['securities']), 'Duplicate cached security')
    require(selected <= set(securities), 'Selected frozen security missing from cached dataset')
    sources = {s: securities[s] for s in selected}
    sources['__benchmark__'] = dataset['benchmark']
    bars = {}
    for symbol, security in sources.items():
        days = [r['date'] for r in security['bars']]
        calendar_check(days)
        bars[symbol] = {r['date']: r for r in security['bars']}

    def raw(symbol, day):
        bar = bars[symbol].get(day)
        if bar is None:
            return None
        status = sources[symbol].get('tradeStatusByDate', {}).get(day)
        suspended = status is False or (type(status) is int and status == 0) or status == '0'
        return {**bar, 'priceBasisId': 'raw-unadjusted', 'knownSuspended': suspended}
    return bars, raw


def verify_comparison_row(row, summary, mode, period, count, prior):
    closed, account, whole = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    labels = {'currentActivity': '当日同伴成交活跃', 'priceProgress': '成交活跃且十日相对价格推进',
              'activityAligned': '成交活跃、相对价格推进且日度量价同步'}
    expected = {'id': mode, 'label': labels[mode], 'signals': count, 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'],
        'meanLossPct': closed['meanLossReturnPct'], 'closedMeanPct': closed['meanReturnPct'],
        'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'meanExposurePct': whole['exposurePct']['mean'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': r['id'], 'closed': r['closedEpisodes']['count'], 'closedMeanPct': r['closedEpisodes']['meanReturnPct'],
            'accountReturnPct': r['returnPct'], 'maxDrawdownPct': r['maxDrawdownPct']}
            for r in summary['periods'] if r['kind'] == 'calendarYear'],
        'phases': [{'id': r['id'], 'start': r['observedStart'], 'end': r['observedEnd'],
            'closed': r['closedEpisodes']['count'], 'accountReturnPct': r['returnPct'],
            'maxDrawdownPct': r['maxDrawdownPct'], 'meanExposurePct': r['exposurePct']['mean']}
            for r in summary['periods'] if r['kind'] == 'retrospectiveNarrativePhase'], 'period': period}
    prior.same(row, expected, 'Every comparison field')
    require(summary['policyId'] == mode and summary['policyLabel'] == labels[mode], 'Summary policy identity')


def verify_registration(directory):
    plan_path = directory / 'plan.json'
    require(sha(plan_path) == REGISTERED_PLAN_SHA, 'Plan is not the preregistered frozen version')
    plan = read(plan_path)
    require(plan['schema'] == 'peer-price-activity-alignment-v1' and plan['modes'] == list(MODES)
            and list(plan['periods']) == list(PERIODS) and plan['strategyId'] == SID, 'Plan identities')
    require(plan['newCandidateVariants'] == 2 and plan['newAccounts'] == 6 and plan['candidateAccounts'] == 4
            and plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False,
            'Experiment counts or selection scope')
    require(plan['certified'] is False and plan['targetAchieved'] is False, 'Unsupported certification')
    require(plan['sourcePins'][ALIGNMENT_MODULE] == ALIGNMENT_MODULE_SHA, 'Alignment module not registered version')
    pins = {}
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for path, expected in mapping.items():
            require(sha(local(path)) == expected, 'Frozen input/code changed: ' + path)
            pins[path] = expected
    proof_path = PARENT / 'validation.json'
    require(sha(proof_path) == PARENT_VALIDATION_SHA, 'Prior independent evidence changed')
    proof = read(proof_path)
    require(proof['passed'] is True and proof['verifierSha256'] == PARENT_VERIFIER_SHA,
            'Prior independent verification did not pass or verifier changed')
    require(proof['planSha256'] == sha(PARENT / 'plan.json') and proof['comparisonSha256'] == sha(PARENT / 'comparison.json'),
            'Prior independent audit no longer binds original plan/results')
    parent_plan = read(PARENT / 'plan.json')
    require(plan['periods'] == parent_plan['periods'], 'Period source identity or account config changed')
    require(plan['comparisons'] == {'primary': 'activityAligned minus priceProgress',
            'secondary': 'priceProgress minus currentActivity'}, 'Primary comparison changed')
    return plan, proof, pins


def verify_period_inputs(directory, period, plan, proof):
    folder, old = directory / period, PARENT / period
    spec, receipt = plan['periods'][period], read(folder / 'input-completion.json')
    require(set(receipt) == {'planSha256', 'completedAt', 'period', 'counts', 'accountData', 'files'}, 'Period receipt schema')
    require(receipt['planSha256'] == REGISTERED_PLAN_SHA and receipt['period'] == period, 'Period input receipt binding')
    expected_files = {'source.json', 'parent-selection.json.gz', 'source-binding.json', 'price-inputs.jsonl.gz',
        'opportunities.jsonl.gz', 'input-counts.json', *(mode + '.signals.json' for mode in MODES)}
    require(set(receipt['files']) == expected_files, 'Period input file closure differs')
    for name, reference in receipt['files'].items():
        require(pinned(reference) == (folder / name).resolve(), 'Period input path mismatch: ' + name)
    parent_refs = proof['inputArtifacts'][period]
    expected_binding = {'accountData': parent_refs['account-data.json.gz'],
        'sourceIdentity': parent_refs['source.json'], 'parentOpportunities': parent_refs['opportunities.jsonl.gz'],
        'parentValidation': {'path': (PARENT / 'validation.json').relative_to(ROOT).as_posix(), 'sha256': PARENT_VALIDATION_SHA}}
    require(read(folder / 'source-binding.json') == expected_binding, 'Inherited source binding differs from prior audit')
    for reference in expected_binding.values():
        pinned(reference)
    require(receipt['accountData'] == expected_binding['accountData'], 'Receipt account data not original cache')
    for name in ('source.json', 'parent-selection.json.gz'):
        require(sha(folder / name) == parent_refs[name]['sha256'], 'Copied original evidence changed: ' + name)
    source = read(folder / 'source.json')
    require(source == spec['sourceIdentity'], 'Original account source identity differs')
    originals = records(old / 'opportunities.jsonl.gz')
    projected = records(folder / 'price-inputs.jsonl.gz')
    slots = records(folder / 'opportunities.jsonl.gz')
    require(len(slots) == len(projected) == len(originals), 'Original opportunity dropped or duplicated')
    identities = [r['signalId'] for r in originals]
    require(len(identities) == len(set(identities)), 'Duplicate original signal')
    require([r['signalId'] for r in slots] == identities == [r['signalId'] for r in projected], 'Original identity/order changed')
    print(json.dumps({'phase': 'cached-source-load', 'period': period, 'opportunities': len(originals)}), flush=True)
    dataset = read(pinned(receipt['accountData']))
    require(digest(dataset) == source['accountDatasetDigest'], 'Full saved account dataset digest differs')
    selected = {r['symbol'] for r in originals} | {s for r in originals for s in r['amount']['members']}
    bars, raw = source_index(dataset, selected)
    calendar, day_index = dataset['calendar'], {d: i for i, d in enumerate(dataset['calendar'])}
    schedules, kinds = {mode: [] for mode in MODES}, Counter()
    cells, paired_days = 0, 0
    original_fields = (*SIGNAL_FIELDS, 'formationDate', 'groupSha256')
    for previous, capture, slot in zip(originals, projected, slots):
        require(previous['strategyId'] == SID, 'Original strategy identity differs')
        original_signal = {k: previous[k] for k in SIGNAL_FIELDS}
        require({k: slot[k] for k in original_fields} == {k: previous[k] for k in original_fields}, 'Original slot fields changed')
        require(slot['parentAmount'] == previous['amount'], 'Original amount evidence changed')
        index = day_index[previous['signalDate']]
        require(index >= 10, 'Insufficient calendar before original signal')
        dates = calendar[index - 10:index + 1]
        amount, members = previous['amount'], previous['amount']['members']
        require(dates == amount['windowDates'][19:] and dates[0] == previous['formationDate'], 'Exact F=T-10 calendar join')
        expected_raw = {s: [raw(s, d) for d in dates] for s in members}
        expected_benchmark = [raw('__benchmark__', d) for d in dates]
        expected_capture = {k: previous[k] for k in ('signalId', 'symbol', 'signalDate', 'originalRank', 'formationDate', 'groupSha256')}
        expected_capture.update(sourceOpportunitySha256=digest(previous), members=members, windowDates=dates,
            rawBySymbol=expected_raw, benchmark=expected_benchmark, originalAmountSha256=digest(amount))
        require(capture == expected_capture, 'Exact original-cache price projection differs: ' + previous['signalId'])
        expected_alignment = reference_alignment(previous['symbol'], members, dates, expected_raw, expected_benchmark, amount)
        expected_slot = {**original_signal, 'formationDate': dates[0], 'groupSha256': previous['groupSha256'],
            'sourceOpportunitySha256': digest(previous), 'parentAmount': amount, 'priceAlignment': expected_alignment,
            'alignmentDiagnostics': reference_diagnostics(expected_alignment)}
        same(slot, expected_slot, 'Full opportunity/evidence schema ' + previous['signalId'])
        require(expected_alignment['currentActivity'] is amount['control'], 'Common price cohort changed old control decision')
        kinds[expected_alignment['kind']] += 1
        cells += (len(members) + 1) * 11
        paired_days += 10 if expected_alignment['complete'] else 0
        for mode in MODES:
            if expected_alignment[mode] is True:
                schedules[mode].append(original_signal)
    old_schedule_ref = parent_refs['currentActivity.signals.json']
    old_schedule = read(pinned(old_schedule_ref))
    require(schedules['currentActivity'] == old_schedule, 'Control schedule not exactly original control')
    for mode in MODES:
        require(read(folder / (mode + '.signals.json')) == schedules[mode], 'Incorrect nested schedule: ' + mode)
    require({r['signalId'] for r in schedules['activityAligned']} <= {r['signalId'] for r in schedules['priceProgress']}
            <= {r['signalId'] for r in schedules['currentActivity']}, 'Schedules not nested')
    counts = {'originalTop5': len(originals), 'days': len({r['signalDate'] for r in originals}), 'outcomes': dict(kinds),
        'known': len(originals) - kinds['unknown'], 'unknown': kinds['unknown'],
        'schedules': {mode: len(rows) for mode, rows in schedules.items()}, 'controlScheduleExactlyMatchesParent': True}
    require(read(folder / 'input-counts.json') == counts == receipt['counts'], 'Input classifications/counts differ')
    expected_n, expected_unknown = {'2022-2023': (148, 4), '2024-2025': (249, 10)}[period]
    require(counts['originalTop5'] == expected_n and counts['unknown'] == expected_unknown, 'Common full-price cohort changed')
    print(json.dumps({'phase': 'independent-inputs-passed', 'period': period, **counts}), flush=True)
    metadata = {'counts': counts, 'sourceBinding': expected_binding, 'inputArtifacts': receipt['files'],
        'priceProjectionSlotsCompared': cells, 'completePairedDaysRecomputed': paired_days,
        'accountCalendar': {'count': len(calendar), 'first': calendar[0], 'last': calendar[-1]},
        'accountDatasetDigest': source['accountDatasetDigest']}
    return metadata, dataset, source, bars, schedules


def verify(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Refusing to overwrite validation.json')
    plan, proof, pins = verify_registration(directory)
    comparison_path = directory / 'comparison.json'
    comparison = read(comparison_path)
    require(comparison['schema'] == 'peer-price-activity-alignment-comparison-v1'
            and comparison['planSha256'] == REGISTERED_PLAN_SHA, 'Comparison schema/plan binding')
    require(comparison['newCandidateVariants'] == 2 and comparison['newAccounts'] == 6
            and comparison['certified'] is False and comparison['targetAchieved'] is False, 'Comparison counts/certification')
    require(comparison['limitations'] == plan['limitations'], 'Comparison limitations changed')
    require(comparison['controlReplayExactMatches'] == {p: True for p in PERIODS}, 'Control replay claims missing')
    require([(r['period'], r['id']) for r in comparison['rows']] == [(p, m) for p in PERIODS for m in MODES],
            'Six-account comparison matrix incomplete or reordered')
    comparison_rows = {(r['period'], r['id']): r for r in comparison['rows']}
    require(set(comparison['artifacts']) == set(PERIODS), 'Comparison artifact period closure')
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and set(completion['outputs']) == set(PERIODS), 'Both-period pre-account freeze receipt')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve(), 'Run-start input receipt binding')
    require(datetime.fromisoformat(completion['completedAt']) <= datetime.fromisoformat(start['startedAt']),
            'Accounts started before input freeze')
    prior = parent_verifier()
    output = {'schema': 'peer-price-activity-alignment-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': REGISTERED_PLAN_SHA, 'comparisonSha256': sha(comparison_path),
        'frozenCodeAndInputs': pins, 'parentValidation': {'path': (PARENT / 'validation.json').relative_to(ROOT).as_posix(),
            'sha256': PARENT_VALIDATION_SHA},
        'independentReferences': {PARENT_VERIFIER: PARENT_VERIFIER_SHA,
            prior.COST_REFERENCE: prior.COST_REFERENCE_SHA, **prior.cost_reference().REFERENCES},
        'inputArtifacts': {}, 'accountArtifacts': comparison['artifacts'], 'periods': {},
        'controlReplayExactMatches': {}, 'certified': False, 'targetAchieved': False}
    for period in PERIODS:
        folder, spec = directory / period, plan['periods'][period]
        require(pinned(completion['outputs'][period]) == (folder / 'input-completion.json').resolve(), 'Period receipt path')
        metadata, dataset, source, bars, schedules = verify_period_inputs(directory, period, plan, proof)
        output['inputArtifacts'][period] = metadata.pop('inputArtifacts')
        expected_files = {m + suffix for m in MODES for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')}
        require(set(comparison['artifacts'][period]) == expected_files, 'Nine period account artifacts not fully bound')
        for name, reference in comparison['artifacts'][period].items():
            require(pinned(reference) == (folder / name).resolve(), 'Account artifact path differs: ' + name)
        account_checks = {}
        for mode in MODES:
            result, checkpoint, summary = (read(folder / (mode + suffix))
                for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
            schedule = schedules[mode]
            closes = {(r['symbol'], r['signalDate']): bars[r['symbol']][r['signalDate']]['close'] for r in schedule}
            account_checks[mode] = prior.verify_saved_account(result, summary, schedule, spec['config'], source,
                dataset['calendar'], closes, period, SID)
            prior.verify_checkpoint(checkpoint, result)
            for fill in result['trades']:
                prior.same(fill['openingPrice'], bars[fill['symbol']][fill['date']]['open'], 'Cached opening fill price')
            verify_comparison_row(comparison_rows[period, mode], summary, mode, period, len(schedule), prior)
            if mode == 'currentActivity':
                original_result_ref = proof['accountArtifacts'][period]['currentActivity.result.json.gz']
                original_result = read(pinned(original_result_ref))
                require(result == original_result, 'Full currentActivity result differs from parent; no metadata exclusions')
                output['controlReplayExactMatches'][period] = True
                del original_result
            print(json.dumps({'phase': 'independent-account-passed', 'period': period, 'mode': mode,
                'signals': len(schedule), 'fills': account_checks[mode]['fills']}), flush=True)
            del result, checkpoint, summary
        metadata['accounts'] = account_checks
        output['periods'][period] = metadata
        del dataset, bars
        gc.collect()
    output['counts'] = {'originalOpportunities': sum(r['counts']['originalTop5'] for r in output['periods'].values()),
        'known': sum(r['counts']['known'] for r in output['periods'].values()),
        'unknown': sum(r['counts']['unknown'] for r in output['periods'].values()),
        'accounts': 6, 'priceProjectionSlots': sum(r['priceProjectionSlotsCompared'] for r in output['periods'].values()),
        'completePairedDays': sum(r['completePairedDaysRecomputed'] for r in output['periods'].values())}
    require(output['counts']['originalOpportunities'] == 397 and output['counts']['known'] == 383
            and output['counts']['unknown'] == 14, 'Full original opportunity/completeness totals')
    output['checks'] = [
        'Preregistered plan and frozen source/input/output hashes, inherited independent group/amount proof',
        'All 397 original identities/ranks/order; exact original amount evidence and no group replacement',
        'Eleven exact F..T source-calendar slots and every projected raw/status field versus the bound full cached account dataset',
        'Independent raw OHLCV/date/basis/envelope/suspension validation; no shortened or silently renormalized peer/price windows',
        'All daily group/benchmark/relative log returns, activity shares, centering, exact-constant zero handling and population covariance',
        'All diagnostic covariance decomposition, last-day products/contribution and independently recentered nine-day covariance',
        'All common-completeness/null states; strict-zero policies and three exact nested schedules, including unfilled opportunities',
        'Six saved full accounts: cash/shares, fees/slippage, T+1, budgets, fills/exits, closed episodes/open holdings, curves/checkpoints/summaries',
        'Both currentActivity parsed result objects exactly equal the prior full results, including all metadata and research fields']
    output['scope'] = {'candidateVariants': 2, 'newAccountsExecutedByRunner': 6, 'accountsExecutedByVerifier': 0,
        'newAlignmentModuleImported': False, 'originalRawMarketReloaded': False, 'oldGroupsOrAmountRecomputed': False,
        'limitations': ['Original population selection, formation and amount arithmetic inherit exact prior independent evidence; this audit checks its immutable bindings and new source projections.',
            'Cached-source provenance inherits prior capture evidence; this verifier does not reload original market files.',
            'Account arithmetic uses pinned independent verifiers; no separate resimulation of every eligibility/capacity/order gate.',
            'Diagnostics are descriptive, not added rules; development histories do not certify stable returns or investor identities.']}
    output['elapsedSeconds'] = time.perf_counter() - started
    path = directory / 'validation.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'path': str(path), 'sha256': sha(path), 'counts': output['counts'],
                      'elapsedSeconds': output['elapsedSeconds']}), flush=True)


def synthetic_inputs(returns=None, activity=None, benchmark_returns=None, control=True):
    days30 = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    days = days30[19:]
    members = [f'p{i:02}' for i in range(20)]
    returns = returns if returns is not None else [.001 * i for i in range(1, 11)]
    activity = activity if activity is not None else [.01 * i for i in range(1, 11)]
    benchmark_returns = benchmark_returns if benchmark_returns is not None else [0.] * 10

    def series(changes):
        closes = [100.]
        for value in changes:
            closes.append(closes[-1] * math.exp(value))
        return [{'date': day, 'open': close, 'high': close * 1.01, 'low': close * .99,
            'close': close, 'volume': 100., 'priceBasisId': 'raw-unadjusted', 'knownSuspended': False}
            for day, close in zip(days, closes)]

    amount = {'version': 'research-peer-amount-share-v1', 'anchor': 'anchor', 'members': members,
        'windowDates': days30, 'formationDate': days[0], 'signalDate': days[-1], 'observationDates': days[1:],
        'control': control, 'complete': True,
        'dailyRows': [{'date': day, 'share': value} for day, value in zip(days30, [.001] * 20 + activity)],
        'marketDigest': 'synthetic-market', 'universeSha256': 'synthetic-universe'}
    return 'anchor', members, days, {s: series(returns) for s in members}, series(benchmark_returns), amount


def self_test():
    checks = []
    a = [.01 * i for i in range(1, 11)]
    r = [.001 * i for i in range(1, 11)]
    result, reason = paired_stats(a, r)
    require(reason is None, 'Synthetic paired statistics unavailable')
    same(result['meanA'], .055)
    same(result['netR'], .055)
    same(result['cov'], .0000825)
    checks.append('manual ten-observation population covariance and means')

    reverse, _ = paired_stats(a, list(reversed(r)))
    require(reverse['netR'] == result['netR'] and reverse['cov'] < 0 < result['cov'], 'Re-pairing must change covariance sign')
    same(reverse['cov'], -result['cov'])
    checks.append('same-marginal re-pairing changes alignment without changing progression')

    for ca, cr in (([.1] * 10, r), (a, [.1] * 10), ([.1] * 10, [.1] * 10)):
        constant, _ = paired_stats(ca, cr)
        require(constant['cov'] == 0 and constant['centeredProducts'] == [0.] * 10, 'Exact constant not zero')
    near, _ = paired_stats([1. + 1e-13 * i for i in range(10)], r)
    require(near['cov'] > 0, 'Near constant must not be snapped to zero')
    checks.append('exact constants zero; near constants retain nonzero covariance')

    args = synthetic_inputs()
    aligned = reference_alignment(*args)
    require(aligned['complete'] and all(aligned[m] for m in MODES) and aligned['kind'] == 'aligned', 'Synthetic complete policy')
    same(aligned['netR'], .055)
    same(aligned['cov'], .0000825)
    require([d['previousDate'] for d in aligned['dailyRows']] == args[2][:-1], 'Daily start/end pairing')
    checks.append('all twenty members and ten exact daily log returns; three nested flags')

    wrong_pair = reference_alignment(*synthetic_inputs(activity=list(reversed(a))))
    require(wrong_pair['currentActivity'] and wrong_pair['priceProgress'] and wrong_pair['activityAligned'] is False
            and wrong_pair['kind'] == 'nonPositiveAlignment', 'Negative covariance rule')
    no_price = reference_alignment(*synthetic_inputs(returns=[0.] * 10))
    require(no_price['netR'] == 0 and no_price['cov'] == 0 and no_price['priceProgress'] is False, 'Zero price threshold not strict')
    constant_a = reference_alignment(*synthetic_inputs(activity=[.1] * 10))
    require(constant_a['priceProgress'] is True and constant_a['activityAligned'] is False and constant_a['cov'] == 0,
            'Zero covariance threshold not strict')
    checks.append('strict positive thresholds; zero progression or covariance is rejected')

    not_active = reference_alignment(*synthetic_inputs(control=False))
    require(not_active['complete'] and not_active['kind'] == 'notActive'
            and all(not_active[m] is False for m in MODES) and not_active['netR'] is not None, 'Inactive known observations lost')
    checks.append('known inactive opportunities keep continuous statistics')

    falling = reference_alignment(*synthetic_inputs(returns=[-.01] * 10, benchmark_returns=[-.02] * 10))
    falling_diag = reference_diagnostics(falling)
    require(falling['priceProgress'] and falling_diag['absolutePeerLogAdvance'] < 0
            and falling_diag['benchmarkLogAdvance'] < falling_diag['absolutePeerLogAdvance'], 'Relative/absolute distinction')
    checks.append('relative progression may coexist with absolute group loss')

    diagnostic = reference_diagnostics(aligned)
    same(diagnostic['covDecompositionResidual'], 0.)
    same(diagnostic['lastCenteredProduct'], (.1 - .055) * (.01 - .0055))
    same(diagnostic['lastAbsContributionShare'], 20.25 / 82.5)
    same(diagnostic['covWithoutLastDay'], .00006666666666666667)
    flat_diag = reference_diagnostics(constant_a)
    require(flat_diag['lastAbsContributionShare'] is None and flat_diag['covWithoutLastDay'] == 0, 'Zero contribution denominator')
    checks.append('covariance decomposition, final-day contribution and nine-day recentering')

    days, bars = args[2], args[4]
    mutations = [('missingAlignedRawBar', lambda values: values.__setitem__(3, None)),
        ('rawHistoryDateMismatch', lambda values: values[3].update(date=days[2])),
        ('rawPriceBasisUnknown', lambda values: values[3].pop('priceBasisId')),
        ('invalidKnownSuspensionFlag', lambda values: values[3].update(knownSuspended=0)),
        ('suspensionContradictsPositiveVolume', lambda values: values[3].update(knownSuspended=True)),
        ('knownSuspension', lambda values: values[3].update(knownSuspended=True, volume=0)),
        ('nonpositiveOrUnknownRawOHLCV', lambda values: values[3].update(volume=0)),
        ('nonpositiveOrUnknownRawOHLCV', lambda values: values[3].update(close=float('nan'))),
        ('nonpositiveOrUnknownRawOHLCV', lambda values: values[3].update(volume=True)),
        ('invalidRawOHLCEnvelope', lambda values: values[3].update(low=101.))]
    for expected, mutate in mutations:
        changed = deepcopy(bars)
        mutate(changed)
        require(window_quality(changed, days) == (expected, 3), 'Window quality case ' + expected)
    require(window_quality(bars[:-1], days) == ('insufficientAlignedRawHistory', None), 'Short window admitted')
    checks.append('all frozen OHLCV, suspension, basis, envelope, date and length failures')

    bad = deepcopy(args)
    bad[3][bad[1][0]][5] = None
    unknown = reference_alignment(*bad)
    require(unknown['kind'] == 'unknown' and all(unknown[m] is None for m in MODES)
            and unknown['netR'] is None and unknown['cov'] is None
            and all(d['relativeLogReturn'] is None for d in unknown['dailyRows']), 'Missing member partially aggregated')
    require(reference_diagnostics(unknown) == dict.fromkeys(DIAGNOSTIC_FIELDS), 'Unknown diagnostics must all remain null')
    checks.append('one missing peer slot nulls every policy, total and diagnostic')

    incomplete = list(deepcopy(args))
    incomplete[1] = incomplete[1][:-1]
    incomplete[3].pop('p19')
    incomplete[5]['members'] = incomplete[1]
    missing_group = reference_alignment(*incomplete)
    require(missing_group['kind'] == 'unknown' and 'incompleteFrozenPeerGroup' in missing_group['unknownReasons'],
            'Incomplete group silently renormalized')
    checks.append('fewer than twenty frozen members never renormalized or replaced')

    unknown_amount = list(deepcopy(args))
    unknown_amount[5]['complete'] = False
    unknown_amount[5]['control'] = None
    unknown_amount[5]['dailyRows'][-2]['share'] = None
    value = reference_alignment(*unknown_amount)
    require(value['unknownReasons'] == ['originalAmountUnknown', 'invalidActivityShare']
            and value['dailyRows'][-2]['activityShare'] is None and value['netR'] is None, 'Unknown amount imputed')
    checks.append('unknown original amount and missing share retained without zero imputation')

    for which in ('dates', 'member'):
        malformed = list(deepcopy(args))
        if which == 'dates':
            malformed[5]['windowDates'][19] = malformed[5]['windowDates'][18]
        else:
            malformed[5]['members'] = malformed[1][:-1]
        try:
            reference_alignment(*malformed)
        except ValueError:
            pass
        else:
            raise ValueError('Malformed amount/price identity join admitted')
    checks.append('malformed date/member identity joins fail instead of becoming unknown')

    hostile = deepcopy(aligned)
    hostile['cov'] *= -1
    try:
        same(hostile, aligned)
    except ValueError:
        pass
    else:
        raise ValueError('Altered covariance not detected')
    hostile = deepcopy(aligned)
    hostile['currentActivity'] = 1
    try:
        same(hostile, aligned)
    except ValueError:
        pass
    else:
        raise ValueError('Boolean policy identity is not strict')
    checks.append('tampered covariance and numeric-for-boolean substitutions rejected')

    synthetic_data = {'calendar': days, 'benchmark': {'bars': bars}, 'securities': [
        {'symbol': 'sample', 'bars': bars, 'tradeStatusByDate': {days[0]: False, days[1]: 0, days[2]: '0', days[3]: True}}]}
    _, raw = source_index(synthetic_data, {'sample'})
    require([raw('sample', d)['knownSuspended'] for d in days[:4]] == [True, True, True, False], 'Cached status projection')
    require(raw('sample', '1999-01-01') is None, 'Missing cached bar fabricated')
    checks.append('cached trade-status projection and missing raw bars')

    require(paired_stats([1e308, 1e308], [1., 2.])[1] == 'nonfiniteAggregate', 'Overflow falsely usable')
    require(log_changes([{'close': 1e308}, {'close': 1e-308}]) is None, 'Nonfinite price ratio falsely usable')
    checks.append('nonfinite aggregates and log returns are unavailable')
    print(json.dumps({'passed': True, 'groups': len(checks), 'checks': checks,
        'newAlignmentModuleImported': False, 'accountsExecuted': 0}), flush=True)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--inputs-only', choices=PERIODS)
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        attempt_dir = args.directory / 'validation-attempts'
        attempt_dir.mkdir(exist_ok=True)
        try:
            if args.inputs_only:
                plan, proof, _ = verify_registration(args.directory.resolve())
                result, dataset, source, bars, schedules = verify_period_inputs(args.directory.resolve(), args.inputs_only, plan, proof)
                result.update(passed=True, scope='inputsOnly:' + args.inputs_only, verifierSha256=sha(__file__),
                              timestamp=stamp, planSha256=REGISTERED_PLAN_SHA, accountsExecuted=0)
                path = attempt_dir / (stamp + '-inputs-' + args.inputs_only + '.json')
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                print(json.dumps({'passed': True, 'attempt': str(path), 'counts': result['counts']}), flush=True)
            else:
                verify(args.directory.resolve())
        except Exception as exc:
            failure = {'passed': False, 'scope': 'inputsOnly:' + args.inputs_only if args.inputs_only else 'fullAudit',
                'timestamp': stamp, 'verifierSha256': sha(__file__), 'error': str(exc),
                'traceback': traceback.format_exc(), 'changedProductionArtifacts': False}
            for filename in ('plan.json', 'comparison.json'):
                path = args.directory / filename
                if path.exists():
                    failure[filename + 'Sha256'] = sha(path)
            path = attempt_dir / (stamp + '-failed.json')
            with path.open('x', encoding='utf-8') as stream:
                json.dump(failure, stream, ensure_ascii=False, indent=2, allow_nan=False)
            print(json.dumps({'passed': False, 'attempt': str(path), 'error': str(exc)}), flush=True)
            raise
