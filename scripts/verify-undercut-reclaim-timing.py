"""Independent full-population undercut/reclaim and entry-clock audit.

The production setup module is never imported and accounts are not rerun.
Cached original data is hash-bound to the earlier independent source audit.
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
DEFAULT = ROOT / 'artifacts/strategy-search/strong-undercut-reclaim-timing-v1'
PARENT = ROOT / 'artifacts/strategy-search/peer-amount-persistence-v1'
PARENT_VERIFIER = 'scripts/verify-peer-amount-persistence.py'
PARENT_VERIFIER_SHA = 'cfaabb0262a33c7c6a5776f4fb54b7d0298df104abf8374ac87313ec6cccd026'
PARENT_VALIDATION_SHA = 'cd06d5cb09b045a88c1489399fb9d8c6ed816f314b6e721fdf4cb082558b0851'
REGISTERED_PLAN_SHA = '756e8f90d444ba94af21f0d61a40a8cb169c0620902c1b61cdc86c207f731143'
SETUP_MODULE = 'service/research_undercut_reclaim.py'
SETUP_MODULE_SHA = 'a071f630bf4f3656fe3d8f74af33cc0966d9e21d5a2d49ca78af7cfb5499db3e'
SID = 'strongUndercutReclaimV1'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('immediate', 'delay2', 'confirm2')
LABELS = {'immediate': '支撑收回后立即决策', 'delay2': '无条件等待两日', 'confirm2': '等待两日且守住支撑'}
SIGNAL_FIELDS = ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def positive(value):
    return finite(value) and value > 0


def same(actual, expected, label='root'):
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-13),
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
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz') else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


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


def calendar_check(days):
    require(isinstance(days, list) and days == sorted(set(days)), 'Calendar is not ordered and unique')
    for day in days:
        require(type(day) is str and date.fromisoformat(day).isoformat() == day, 'Noncanonical date')


def parent_verifier():
    path = local(PARENT_VERIFIER)
    require(sha(path) == PARENT_VERIFIER_SHA, 'Pinned prior independent verifier changed')
    spec = importlib.util.spec_from_file_location('_undercut_prior_independent_auditor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_halted(value):
    return value is False or (type(value) is int and value == 0) or value == '0'


def bar_valid(bar, day, status=None):
    """Quality against the original cache with its declared raw basis."""
    return price_reason(bar, day, status) is None


def price_reason(bar, day, status=None):
    if bar is None:
        return 'missingAlignedRawBar'
    if not isinstance(bar, dict) or bar.get('date') != day:
        return 'rawHistoryDateMismatch'
    if bar.get('priceBasisId', 'raw-unadjusted') != 'raw-unadjusted':
        return 'rawPriceBasisUnknown'
    if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
        return 'invalidKnownSuspensionFlag'
    if is_halted(status) or bar.get('knownSuspended') is True:
        return 'suspensionContradictsPositiveVolume' if positive(bar.get('volume')) else 'knownSuspension'
    if any(not positive(bar.get(k)) for k in ('open', 'high', 'low', 'close', 'volume')):
        return 'nonpositiveOrUnknownRawOHLCV'
    if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
        return 'invalidRawOHLCEnvelope'
    return None


def source_index(dataset, end=None):
    calendar = dataset['calendar']
    calendar_check(calendar)
    securities = {s['symbol']: s for s in dataset['securities']}
    require(len(securities) == len(dataset['securities']), 'Duplicate cached security')
    require(not dataset.get('corporateActions') and all(not s.get('corporateActions') for s in securities.values()),
            'Unregistered corporate-action instructions')
    sources = {**securities, '__benchmark__': dataset['benchmark']}
    require(bool(calendar), 'Empty source calendar')
    end = end or calendar[-1]
    require(calendar[-1] <= end, 'Calendar includes evidence beyond target end')
    calendar_set = set(calendar)
    maps, vectors, invalid_prefix, statuses, reasons = {}, {}, {}, {}, {}
    for symbol, security in sources.items():
        dates = [b['date'] for b in security['bars']]
        calendar_check(dates)
        require(set(dates) <= calendar_set and all(d <= end for d in dates), 'Future/off-calendar cached bar')
        maps[symbol] = {b['date']: b for b in security['bars']}
        statuses[symbol] = security.get('tradeStatusByDate', {})
        require(isinstance(statuses[symbol], dict), 'Cached status evidence is not a mapping')
        calendar_check(sorted(statuses[symbol]))
        require(set(statuses[symbol]) <= calendar_set and all(d <= end for d in statuses[symbol]), 'Future/off-calendar status')
        vector = [maps[symbol].get(day) for day in calendar]
        prefix = [0]
        reasons[symbol] = []
        for day, bar in zip(calendar, vector):
            issue = price_reason(bar, day, statuses[symbol].get(day))
            reasons[symbol].append(issue)
            prefix.append(prefix[-1] + (issue is not None))
        vectors[symbol], invalid_prefix[symbol] = vector, prefix
    return {'calendar': calendar, 'symbols': sorted(securities), 'sources': sources, 'maps': maps,
            'vectors': vectors, 'invalidPrefix': invalid_prefix, 'statuses': statuses, 'reasons': reasons}


def rank_midpoints(values):
    """All computable price rows enter N, regardless of later screening."""
    require(all(type(symbol) is str and finite(value) for symbol, value in values.items()), 'Invalid rank population')
    ordered = sorted(values, key=lambda s: (values[s], s))
    ranks, size, index = {}, len(ordered), 0
    while index < size:
        stop = index + 1
        while stop < size and values[ordered[stop]] == values[ordered[index]]:
            stop += 1
        midpoint = (index + stop) / (2 * size)
        for symbol in ordered[index:stop]:
            ranks[symbol] = midpoint
        index = stop
    return ranks


def price_population(indexes, at):
    """Return every stock's own/relative prior sixty-session return."""
    symbols, vectors, prefixes = indexes['symbols'], indexes['vectors'], indexes['invalidPrefix']
    own, relative = {}, {}
    if at < 61 or prefixes['__benchmark__'][at] != prefixes['__benchmark__'][at - 61]:
        return own, relative, {}, None
    try:
        benchmark_ratio = vectors['__benchmark__'][at - 1]['close'] / vectors['__benchmark__'][at - 61]['close']
    except (ValueError, OverflowError, ZeroDivisionError):
        return own, relative, {}, None
    if not positive(benchmark_ratio):
        return own, relative, {}, None
    for symbol in symbols:
        if prefixes[symbol][at] != prefixes[symbol][at - 61]:
            continue
        try:
            ratio = vectors[symbol][at - 1]['close'] / vectors[symbol][at - 61]['close']
            own_value, relative_value = ratio - 1, ratio / benchmark_ratio - 1
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
        if finite(own_value) and finite(relative_value):
            own[symbol], relative[symbol] = own_value, relative_value
    return own, relative, rank_midpoints(relative), benchmark_ratio


def formation_candidates(indexes, at):
    """Independent causal setup set, before ranking or future clocks."""
    days, vectors = indexes['calendar'], indexes['vectors']
    own, relative, ranks, benchmark_ratio = price_population(indexes, at)
    candidates = []
    for symbol in indexes['symbols']:
        if symbol not in ranks or ranks[symbol] < .8 or own[symbol] <= 0:
            continue
        prior = vectors[symbol][at - 20:at]
        amounts = [bar.get('amount') if bar is not None else None for bar in prior]
        if len(amounts) != 20 or not all(positive(value) for value in amounts):
            continue
        try:
            amount = math.fsum(amounts) / 20
        except (ValueError, OverflowError):
            continue
        if not positive(amount):
            continue
        support = min(bar['low'] for bar in prior)
        current = vectors[symbol][at]
        if not bar_valid(current, days[at], indexes['statuses'][symbol].get(days[at])):
            continue
        if current['low'] < support < current['close']:
            candidates.append({'symbol': symbol, 'priorOwn60': own[symbol], 'priorRelative60': relative[symbol],
                'priorRank60': ranks[symbol], 'priorAmount20': amount, 'support': support,
                'setupLow': current['low'], 'setupClose': current['close']})
    ordered = sorted(candidates, key=lambda r: (-r['priorAmount20'], r['symbol']))
    return {'own': own, 'relative': relative, 'ranks': ranks, 'benchmarkRatio': benchmark_ratio,
            'candidates': ordered, 'top5': ordered[:5]}


def make_signal(formation, symbol, decision, rank):
    return {'signalId': f'{SID}:{formation}:{symbol}', 'strategyId': SID, 'symbol': symbol,
            'signalDate': decision, 'originalRank': rank}


def timing_reference(indexes, at, symbol, support, rank, target_end):
    """Do not permit future evidence to mutate the immediate opportunity."""
    calendar = indexes['calendar']
    formation = calendar[at]
    future = [d for d in calendar[at + 1:at + 3] if d <= target_end]
    immediate = make_signal(formation, symbol, formation, rank)
    known = []
    closes = []
    for day in future:
        bar = indexes['maps'][symbol].get(day)
        known.append(bar_valid(bar, day, indexes['statuses'][symbol].get(day)))
        closes.append(bar.get('close') if bar is not None and positive(bar.get('close')) else None)
    if len(future) < 2:
        status, delayed, confirmed, decision = 'censored', None, None, None
    else:
        decision = future[-1]
        delayed = make_signal(formation, symbol, decision, rank)
        status = 'unknown' if not all(known) else 'passed' if all(c >= support for c in closes) else 'failed'
        confirmed = deepcopy(delayed) if status == 'passed' else None
    return {'dates': future, 'closes': closes, 'quality': known, 'status': status,
            'decisionDate': decision, 'signals': {'immediate': immediate, 'delay2': delayed, 'confirm2': confirmed}}


def reference_build(indexes, start, end, *, progress=True):
    """Recompute all ranks and all setup opportunities from the original sample."""
    calendar, symbols, vectors, prefixes = (indexes[k] for k in ('calendar', 'symbols', 'vectors', 'invalidPrefix'))
    target_dates = [day for day in calendar if start <= day <= end]
    require(target_dates and calendar[-1] <= end, 'Empty target or future source calendar')
    target_set = set(target_dates)
    daily_rows, opportunities = [], []
    schedules = {mode: [] for mode in MODES}
    confirms = Counter({key: 0 for key in ('passed', 'failed', 'unknown', 'censored')})
    for at, day in enumerate(calendar):
        if day not in target_set:
            continue
        prior_days = calendar[max(0, at - 61):at]
        counts = dict.fromkeys(('sampleCount', 'priorPriceIncomplete', 'priorReturnInvalid', 'rankPopulationCount',
            'strongCount', 'amountUnknownCount', 'formationPriceUnknownCount', 'setupCount', 'selectedCount'), 0)
        counts['sampleCount'] = len(symbols)
        bench_reason = ('insufficientPriorCalendarHistory' if at < 61 else
            'incompleteBenchmarkPriorPrice' if prefixes['__benchmark__'][at] != prefixes['__benchmark__'][at - 61] else None)
        own, relative, ranks, benchmark_ratio = price_population(indexes, at)
        if bench_reason is None and benchmark_ratio is None:
            bench_reason = 'nonfiniteBenchmarkPriorReturn'
        bench_return = benchmark_ratio - 1 if benchmark_ratio is not None else None
        population = [{'symbol': s, 'priorOwn60': own[s], 'priorRelative60': relative[s], 'priorRank60': ranks[s]}
            for s in symbols if s in ranks]
        counts['rankPopulationCount'] = len(population)
        for symbol in symbols:
            if at < 61 or prefixes[symbol][at] != prefixes[symbol][at - 61]:
                counts['priorPriceIncomplete'] += 1
            elif bench_reason is None and symbol not in ranks:
                counts['priorReturnInvalid'] += 1
        candidates = []
        for row in population:
            if row['priorRank60'] < .8 or row['priorOwn60'] <= 0:
                continue
            counts['strongCount'] += 1
            symbol = row['symbol']
            prior20 = vectors[symbol][at - 20:at]
            amounts = [bar.get('amount') for bar in prior20]
            if not all(positive(value) for value in amounts):
                counts['amountUnknownCount'] += 1
                continue
            try:
                amount = math.fsum(amounts) / 20
            except (ValueError, OverflowError):
                amount = None
            if not positive(amount):
                counts['amountUnknownCount'] += 1
                continue
            if indexes['reasons'][symbol][at] is not None:
                counts['formationPriceUnknownCount'] += 1
                continue
            support = min(bar['low'] for bar in prior20)
            current = vectors[symbol][at]
            if current['low'] < support < current['close']:
                candidates.append({**row, 'priorAmount20': amount, 'support': support,
                    'formationLow': current['low'], 'formationClose': current['close']})
        candidates.sort(key=lambda row: (-row['priorAmount20'], row['symbol']))
        selected_ids = []
        for rank, row in enumerate(candidates, 1):
            row.update(originalRank=rank, selected=rank <= 5)
            if rank > 5:
                continue
            symbol = row['symbol']
            clock = timing_reference(indexes, at, symbol, row['support'], rank, end)
            formation_id = clock['signals']['immediate']['signalId']
            selected_ids.append(formation_id)
            status = clock['status']
            if status == 'censored':
                reasons = ['decisionOutsideTarget']
            elif status == 'unknown':
                reasons = [d + ':' + price_reason(indexes['maps'][symbol].get(d), d, indexes['statuses'][symbol].get(d))
                    for d, valid in zip(clock['dates'], clock['quality']) if not valid]
            elif status == 'failed':
                reasons = [d + ':closeBelowSupport' for d, close in zip(clock['dates'], clock['closes']) if close < row['support']]
            else:
                reasons = []
            confirmation = {'status': status, 'dates': clock['dates'], 'closes': clock['closes'], 'reasons': reasons}
            opportunity = {k: value for k, value in row.items() if k != 'selected'}
            opportunity.update(formationId=formation_id, formationDate=day, priorDate=calendar[at - 1],
                decisionDates={'immediate': day, 'delay2': clock['decisionDate'], 'confirm2': clock['decisionDate']},
                confirmation=confirmation, signals=clock['signals'])
            opportunities.append(opportunity)
            confirms[status] += 1
            for mode in MODES:
                if clock['signals'][mode] is not None:
                    schedules[mode].append(clock['signals'][mode])
        counts.update(setupCount=len(candidates), selectedCount=len(selected_ids))
        daily_rows.append({'formationDate': day, 'priorDate': calendar[at - 1] if at else None,
            'priorWindowDates': prior_days, 'prior20Dates': prior_days[-20:],
            'rankPopulationCount': len(population), 'rankPopulationSha256': digest(population),
            'benchmarkPrior60': {'complete': bench_reason is None, 'reason': bench_reason, 'return60': bench_return},
            'counts': counts, 'candidates': candidates, 'selectedFormationIds': selected_ids})
        if progress and (len(daily_rows) % 100 == 0 or day == target_dates[-1]):
            print(json.dumps({'phase': 'independent-ranks-and-formations', 'day': day,
                'days': len(daily_rows), 'opportunities': len(opportunities)}), flush=True)
    counts = {'sampleCount': len(symbols), 'targetDayCount': len(target_dates),
        'setupCount': sum(row['counts']['setupCount'] for row in daily_rows), 'opportunityCount': len(opportunities),
        'daysWithSetups': sum(bool(row['candidates']) for row in daily_rows),
        'daysWithSelected': sum(bool(row['selectedFormationIds']) for row in daily_rows),
        'confirmation': dict(confirms), 'signals': {mode: len(rows) for mode, rows in schedules.items()}}
    return {'version': 'strong-undercut-reclaim-timing-v1', 'strategyId': SID, 'modes': list(MODES),
        'calendar': calendar, 'targetDates': target_dates, 'dailyRows': daily_rows, 'opportunities': opportunities,
        'schedules': schedules, 'counts': counts, 'certified': False, 'targetAchieved': False}


def verify_comparison_row(row, summary, mode, period, count, prior):
    closed, account, whole = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    expected = {'id': mode, 'label': LABELS[mode], 'signals': count, 'closed': closed['count'],
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
    require(summary['policyId'] == mode and summary['policyLabel'] == LABELS[mode], 'Summary policy identity')


def verify_registration(directory):
    require(REGISTERED_PLAN_SHA is not None and SETUP_MODULE_SHA is not None, 'Prerun hashes not installed')
    require(sha(directory / 'plan.json') == REGISTERED_PLAN_SHA, 'Plan is not the preregistered frozen version')
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'strong-undercut-reclaim-timing-v1' and plan['modes'] == list(MODES)
            and list(plan['periods']) == list(PERIODS) and plan['strategyId'] == SID, 'Plan identities')
    require(plan['newSetupMechanisms'] == 1 and plan['newPolicyVariants'] == 3 and plan['newAccounts'] == 6
            and plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False,
            'Experiment counts or selection scope')
    require(plan['certified'] is False and plan['targetAchieved'] is False, 'Unsupported certification')
    require(plan['sourcePins'][SETUP_MODULE] == SETUP_MODULE_SHA, 'Setup module not registered version')
    pins = {}
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for path, expected in mapping.items():
            require(sha(local(path)) == expected, 'Frozen input/code changed: ' + path)
            pins[path] = expected
    require(sha(PARENT / 'validation.json') == PARENT_VALIDATION_SHA, 'Prior source audit changed')
    proof = read(PARENT / 'validation.json')
    require(proof['passed'] is True and proof['verifierSha256'] == PARENT_VERIFIER_SHA,
            'Prior independent verification did not pass or verifier changed')
    require(proof['planSha256'] == sha(PARENT / 'plan.json') and proof['comparisonSha256'] == sha(PARENT / 'comparison.json'),
            'Prior independent source audit no longer binds original plan/results')
    original_periods = read(PARENT / 'plan.json')['periods']
    expected_periods = deepcopy(original_periods)
    for period in PERIODS:
        expected_periods[period]['config']['strategies'][0]['id'] = SID
        expected_periods[period]['config']['allocation']['strategyIds'] = [SID]
    require(plan['periods'] == expected_periods, 'Source/config changed beyond registered strategy namespace')
    require(plan['comparisons'] == {'primary': 'immediate minus delay2', 'secondary': 'confirm2 minus delay2'},
            'Preregistered timing comparisons changed')
    return plan, proof, pins


def verify_period_inputs(directory, period, plan, proof):
    folder, spec = directory / period, plan['periods'][period]
    receipt = read(folder / 'input-completion.json')
    require(set(receipt) == {'planSha256', 'completedAt', 'period', 'counts', 'accountData', 'files'}, 'Period receipt schema')
    require(receipt['planSha256'] == REGISTERED_PLAN_SHA and receipt['period'] == period, 'Period receipt binding')
    expected_files = {'source.json', 'source-binding.json', 'opportunities.jsonl.gz', 'daily-evidence.jsonl.gz',
        'signal-metadata.json', 'input-counts.json', *(m + '.signals.json' for m in MODES)}
    require(set(receipt['files']) == expected_files, 'Incomplete/extra period input closure')
    for name, reference in receipt['files'].items():
        require(pinned(reference) == (folder / name).resolve(), 'Period input path mismatch: ' + name)
    parent_refs = proof['inputArtifacts'][period]
    expected_binding = {'accountData': parent_refs['account-data.json.gz'], 'sourceIdentity': parent_refs['source.json'],
        'parentValidation': {'path': (PARENT / 'validation.json').relative_to(ROOT).as_posix(), 'sha256': PARENT_VALIDATION_SHA},
        'projection': 'Complete cached account dataset plus explicit inherited raw-unadjusted priceBasisId; no later opportunity membership.'}
    require(read(folder / 'source-binding.json') == expected_binding, 'Source binding not original audited full sample')
    require(receipt['accountData'] == expected_binding['accountData'], 'Receipt uses different account source')
    for key in ('accountData', 'sourceIdentity', 'parentValidation'):
        pinned(expected_binding[key])
    require(sha(folder / 'source.json') == parent_refs['source.json']['sha256'], 'Original source identity changed')
    source = read(folder / 'source.json')
    require(source == spec['sourceIdentity'], 'Plan/source identity differs')
    print(json.dumps({'phase': 'independent-full-cache-load', 'period': period}), flush=True)
    dataset = read(pinned(receipt['accountData']))
    require(digest(dataset) == source['accountDatasetDigest'], 'Full original cached dataset digest differs')
    require(len(dataset['securities']) == 3141, 'Full original 3141 population was not supplied')
    indexes = source_index(dataset, spec['config']['endDate'])
    expected = reference_build(indexes, spec['config']['startDate'], spec['config']['endDate'])
    actual_daily = list(records(folder / 'daily-evidence.jsonl.gz'))
    actual_opportunities = list(records(folder / 'opportunities.jsonl.gz'))
    same(actual_daily, expected['dailyRows'], 'All daily full-population rank digests/counts/formation candidates/Top5')
    same(actual_opportunities, expected['opportunities'], 'All original Top5 opportunities and three causal clocks')
    require(len({r['formationId'] for r in actual_opportunities}) == len(actual_opportunities), 'Duplicate formation identity')
    metadata = {k: value for k, value in expected.items() if k not in ('dailyRows', 'opportunities', 'schedules')}
    same(read(folder / 'signal-metadata.json'), metadata, 'Complete signal metadata/calendar/counts')
    require(read(folder / 'input-counts.json') == expected['counts'] == receipt['counts'], 'All input counts differ')
    for mode in MODES:
        require(read(folder / (mode + '.signals.json')) == expected['schedules'][mode], 'Complete five-field schedule differs: ' + mode)
        schedule = expected['schedules'][mode]
        require(all(set(row) == set(SIGNAL_FIELDS) for row in schedule), 'Schedule has extra or missing fields')
        require(schedule == sorted(schedule, key=lambda r: (r['signalDate'], r['originalRank'], r['symbol'])), 'Schedule order differs')
        require(len({r['signalId'] for r in schedule}) == len(schedule), 'Duplicate signal identity within policy')
    ids = {mode: {r['signalId'] for r in expected['schedules'][mode]} for mode in MODES}
    require(ids['confirm2'] <= ids['delay2'] <= ids['immediate'], 'Confirmation/delayed identities not subsets of formations')
    require(len(ids['immediate']) == expected['counts']['opportunityCount'], 'Immediate setup filtered by subsequent evidence')
    require(len(ids['immediate'] - ids['delay2']) == expected['counts']['confirmation']['censored'],
            'Delay dropped opportunities for something other than calendar censoring')
    report = {'counts': expected['counts'], 'sourceBinding': expected_binding, 'inputArtifacts': receipt['files'],
        'accountDatasetDigest': source['accountDatasetDigest'], 'rankedSymbolDays': sum(len(indexes['symbols']) for _ in expected['dailyRows']),
        'computableRankRows': sum(r['rankPopulationCount'] for r in expected['dailyRows']),
        'calendar': {'count': len(indexes['calendar']), 'first': indexes['calendar'][0], 'last': indexes['calendar'][-1]},
        'originalCachedPriceSlotsChecked': (len(indexes['symbols']) + 1) * len(indexes['calendar']),
        'allSignalClockIdentitiesConserved': True, 'noFutureSelectionOfImmediate': True}
    print(json.dumps({'phase': 'independent-inputs-passed', 'period': period, 'counts': expected['counts']}), flush=True)
    return report, dataset, source, indexes, expected['schedules']


def verify(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Refusing to overwrite validation.json')
    plan, proof, pins = verify_registration(directory)
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'strong-undercut-reclaim-timing-comparison-v1'
            and comparison['planSha256'] == REGISTERED_PLAN_SHA, 'Comparison schema/plan binding')
    require(comparison['newSetupMechanisms'] == 1 and comparison['newPolicyVariants'] == 3 and comparison['newAccounts'] == 6
            and comparison['certified'] is False and comparison['targetAchieved'] is False, 'Comparison counts/certification')
    require(comparison['limitations'] == plan['limitations'], 'Comparison limitations changed')
    require([(r['period'], r['id']) for r in comparison['rows']] == [(p, m) for p in PERIODS for m in MODES],
            'Complete ordered six-account comparison matrix required')
    comparison_rows = {(r['period'], r['id']): r for r in comparison['rows']}
    require(set(comparison['artifacts']) == set(PERIODS), 'Comparison artifact period coverage')
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and set(completion['outputs']) == set(PERIODS), 'Both-period pre-account input freeze missing')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve(), 'Run-start input binding')
    require(datetime.fromisoformat(completion['completedAt']) <= datetime.fromisoformat(start['startedAt']),
            'Accounts started before input freeze')
    prior = parent_verifier()
    output = {'schema': 'strong-undercut-reclaim-timing-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': REGISTERED_PLAN_SHA, 'comparisonSha256': sha(directory / 'comparison.json'),
        'frozenCodeAndInputs': pins, 'parentValidation': {'path': (PARENT / 'validation.json').relative_to(ROOT).as_posix(),
            'sha256': PARENT_VALIDATION_SHA},
        'independentReferences': {PARENT_VERIFIER: PARENT_VERIFIER_SHA, prior.COST_REFERENCE: prior.COST_REFERENCE_SHA,
            **prior.cost_reference().REFERENCES}, 'inputArtifacts': {}, 'accountArtifacts': comparison['artifacts'],
        'periods': {}, 'certified': False, 'targetAchieved': False}
    for period in PERIODS:
        folder, spec = directory / period, plan['periods'][period]
        require(pinned(completion['outputs'][period]) == (folder / 'input-completion.json').resolve(), 'Period receipt path binding')
        metadata, dataset, source, indexes, schedules = verify_period_inputs(directory, period, plan, proof)
        output['inputArtifacts'][period] = metadata.pop('inputArtifacts')
        expected_files = {mode + suffix for mode in MODES for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')}
        require(set(comparison['artifacts'][period]) == expected_files, 'All nine period account artifacts must be bound')
        for name, reference in comparison['artifacts'][period].items():
            require(pinned(reference) == (folder / name).resolve(), 'Account artifact path changed: ' + name)
        accounts = {}
        for mode in MODES:
            result, checkpoint, summary = (read(folder / (mode + suffix))
                for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
            schedule = schedules[mode]
            closes = {}
            for row in schedule:
                bar = indexes['maps'][row['symbol']].get(row['signalDate'])
                close = bar.get('close') if isinstance(bar, dict) else None
                closes[row['symbol'], row['signalDate']] = close if positive(close) else None
            accounts[mode] = prior.verify_saved_account(result, summary, schedule, spec['config'], source,
                dataset['calendar'], closes, period, SID)
            prior.verify_checkpoint(checkpoint, result)
            buys = {row['signalId'] for row in result['trades'] if row['side'] == 'buy'}
            missing_count = 0
            for opportunity in result['research']['opportunities']:
                if closes[opportunity['symbol'], opportunity['signalDate']] is None:
                    missing_count += 1
                    require(opportunity['plannedQuantity'] is None and opportunity['signalId'] not in buys,
                            'Missing decision-close opportunity produced an entry plan/fill')
            accounts[mode]['missingDecisionCloseOpportunitiesRetained'] = missing_count
            for fill in result['trades']:
                source_bar = indexes['maps'][fill['symbol']].get(fill['date'])
                require(source_bar is not None, 'Fill lacks source-day bar')
                prior.same(fill['openingPrice'], source_bar['open'], 'Source-bound opening fill price')
            verify_comparison_row(comparison_rows[period, mode], summary, mode, period, len(schedule), prior)
            print(json.dumps({'phase': 'independent-account-passed', 'period': period, 'mode': mode,
                'signals': len(schedule), 'fills': accounts[mode]['fills'], 'missingDecisionCloses': missing_count}), flush=True)
            del result, checkpoint, summary
        metadata['accounts'] = accounts
        output['periods'][period] = metadata
        del dataset, indexes
        gc.collect()
    output['counts'] = {'accounts': 6, 'setupMechanisms': 1, 'policyVariants': 3,
        'fullSampleSymbolDays': sum(r['rankedSymbolDays'] for r in output['periods'].values()),
        'computableRankRows': sum(r['computableRankRows'] for r in output['periods'].values()),
        'allSetups': sum(r['counts']['setupCount'] for r in output['periods'].values()),
        'top5Opportunities': sum(r['counts']['opportunityCount'] for r in output['periods'].values()),
        'signals': {mode: sum(r['counts']['signals'][mode] for r in output['periods'].values()) for mode in MODES}}
    output['checks'] = [
        'Preregistered code/input/result hashes and full cached source dataset digests inherited from the exact independent source audit',
        'Every original sample symbol on every target date; exact prior61 stock/benchmark quality and full-sample midrank population digests',
        'Own-positive and rank threshold applied after ranking; prior20 strict positive explicit amount and frozen support, with all rejection counts',
        'Strict same-session low<prior support<close setup; all candidates ranked by prior amount and original global Top5 before future clocks',
        'All three clock records, shared formation identity/rank, full five-field schedules, unknown-first confirmation and cutoff censoring',
        'Immediate opportunities remain independent of future confirmation; unconditional delay preserves missing-price schedule entries',
        'Six saved complete accounts: source fills, fees/slippage, cash/shares, T+1, budgets, exits, episodes/open holdings, curves/checkpoints and every comparison statistic']
    output['scope'] = {'accountsExecutedByVerifier': 0, 'newSetupModuleImported': False, 'originalRawMarketReloaded': False,
        'rankPopulationFilteredByAmountOrEligibility': False, 'futureLeaderMembershipUsed': False,
        'limitations': ['Cached-source provenance and historical source limitations inherit pinned independent capture evidence; no new raw-market acquisition or rights certification.',
            'Formation and timing calculations are independent. Account arithmetic uses pinned independent auditors without separate resimulation of every capacity/eligibility/order gate.',
            'Previously inspected development histories do not establish a clean holdout, stable profits, investor control or an isolated causal timing effect.',
            'Phase readout is a later separately bound artifact; this validation covers all supplied account summaries and comparisons.']}
    output['elapsedSeconds'] = time.perf_counter() - started
    path = directory / 'validation.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'path': str(path), 'sha256': sha(path), 'counts': output['counts'],
                      'elapsedSeconds': output['elapsedSeconds']}), flush=True)


def synthetic_dataset(n=30, days_count=66):
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(days_count)]

    def bar(day, close, amount=1000.):
        return {'date': day, 'open': close, 'high': close * 1.01, 'low': close * .99,
                'close': close, 'volume': 1000., 'amount': amount}

    benchmark = {'bars': [bar(day, 100.) for day in calendar]}
    securities = []
    for i in range(n):
        symbol = 'sh' + str(600000 + i)
        bars = [bar(day, 100. * (1 + .001 * (i + 1) * j), 1000. + i) for j, day in enumerate(calendar)]
        if days_count > 61:
            support = min(row['low'] for row in bars[41:61])
            bars[61]['low'] = support * .99
        securities.append({'symbol': symbol, 'bars': bars, 'tradeStatusByDate': {}})
    return {'calendar': calendar, 'securities': securities, 'benchmark': benchmark}


def self_test():
    checks = []
    ranks = rank_midpoints({'a': -1., 'b': 0., 'c': 0., 'd': 2.})
    require(ranks == {'a': .125, 'b': .5, 'c': .5, 'd': .875}, 'Tie-aware full-population midrank')
    require(rank_midpoints({'a': 1.}) == {'a': .5} and rank_midpoints({}) == {}, 'Singleton/empty ranks')
    checks.append('full-population ascending midranks with exact ties, empty and singleton cases')

    data = synthetic_dataset()
    indexes = source_index(data)
    formation = formation_candidates(indexes, 61)
    require(len(formation['relative']) == 30 and len(formation['candidates']) == 6 and len(formation['top5']) == 5,
            'Expected synthetic rank/strong/setup populations')
    require([r['symbol'] for r in formation['top5']] == ['sh600029', 'sh600028', 'sh600027', 'sh600026', 'sh600025'],
            'Frozen amount ranking and Top5')
    same(formation['own']['sh600029'], 1.8)
    same(formation['ranks']['sh600029'], 29.5 / 30)
    checks.append('exact prior61 returns, strength mask, prior20 support/amount and Top5')

    mutated = deepcopy(data)
    mutated['securities'][-1]['bars'][50]['amount'] = None
    changed = formation_candidates(source_index(mutated), 61)
    require(changed['ranks'] == formation['ranks'] and len(changed['candidates']) == 5,
            'Missing amount changed price-rank population')
    mutated['securities'][-1]['bars'][50]['amount'] = 0.
    require(len(formation_candidates(source_index(mutated), 61)['candidates']) == 5, 'Zero amount admitted')
    checks.append('amount missing/zero only gates after full price ranking')

    mutated = deepcopy(data)
    for row in mutated['securities'][-1]['bars'][:61]:
        row['amount'] = 1.
    changed = formation_candidates(source_index(mutated), 61)
    require(len(changed['candidates']) == 6 and changed['candidates'][-1]['symbol'] == 'sh600029',
            'Liquidity gate/ranking not applied independently of strength ranks')
    checks.append('liquidity priority changes Top5 but not strength membership')

    mutated = deepcopy(data)
    mutated['securities'][0]['bars'][0]['volume'] = 0
    require(len(price_population(source_index(mutated), 61)[1]) == 29, 'Bad early prior61 slot ignored')
    mutated = deepcopy(data)
    mutated['securities'][-1]['bars'][61]['volume'] = 0
    changed = formation_candidates(source_index(mutated), 61)
    require(len(changed['relative']) == 30 and len(changed['candidates']) == 5, 'Current bar affected prior rank')
    checks.append('full61 quality changes denominator; current quality never changes prior ranks')

    require(price_population(indexes, 60)[:3] == ({}, {}, {}), 'Short prior history shortened')
    mutated = deepcopy(data)
    mutated['benchmark']['bars'][20]['low'] = 999.
    require(price_population(source_index(mutated), 61)[:3] == ({}, {}, {}), 'Invalid benchmark partially admitted')
    checks.append('short prior61 and invalid benchmark do not manufacture ranks')

    symbol, support = formation['top5'][0]['symbol'], formation['top5'][0]['support']
    for field in ('low', 'close'):
        mutated = deepcopy(data)
        row = mutated['securities'][-1]['bars'][61]
        row[field] = support
        if field == 'close':
            row['open'] = support
        require(len(formation_candidates(source_index(mutated), 61)['candidates']) == 5, 'Setup equality admitted')
    checks.append('formation requires strictly low < support < close')

    timing = timing_reference(indexes, 61, symbol, support, 1, data['calendar'][-1])
    require(timing['status'] == 'passed' and all(timing['signals'][mode] for mode in MODES), 'Three clocks not emitted')
    require(timing['signals']['immediate']['signalDate'] == data['calendar'][61]
            and timing['signals']['delay2']['signalDate'] == timing['signals']['confirm2']['signalDate'] == data['calendar'][63],
            'Wrong decision clocks')
    require(len({timing['signals'][m]['signalId'] for m in MODES}) == 1
            and all(set(timing['signals'][m]) == set(SIGNAL_FIELDS) for m in MODES), 'Clock identity/schema changed')
    checks.append('same formation ID and original rank, distinct T and T+2 decision dates')

    mutated = deepcopy(data)
    future = mutated['securities'][-1]['bars'][62]
    future.update(open=support, high=support * 1.01, low=support * .99, close=support)
    equal = timing_reference(source_index(mutated), 61, symbol, support, 1, data['calendar'][-1])
    require(equal['status'] == 'passed', 'Confirmation support equality should hold')
    future.update(open=support * .99, high=support, low=support * .98, close=support * .99)
    failed = timing_reference(source_index(mutated), 61, symbol, support, 1, data['calendar'][-1])
    require(failed['status'] == 'failed' and failed['signals']['confirm2'] is None
            and failed['signals']['delay2'] == timing['signals']['delay2'], 'Failed confirmation changed unconditional delay')
    mutated['securities'][-1]['bars'][63]['volume'] = 0
    unknown = timing_reference(source_index(mutated), 61, symbol, support, 1, data['calendar'][-1])
    require(unknown['status'] == 'unknown' and unknown['signals']['immediate'] == timing['signals']['immediate']
            and unknown['signals']['delay2'] == timing['signals']['delay2'], 'Unknown must dominate simultaneous failure')
    checks.append('confirmation equality, failure, and unknown-first handling never filter I or D')

    mutated = deepcopy(data)
    mutated['securities'][-1]['bars'] = [b for b in mutated['securities'][-1]['bars'] if b['date'] != data['calendar'][63]]
    altered_index = source_index(mutated)
    absent = timing_reference(altered_index, 61, symbol, support, 1, data['calendar'][-1])
    require(absent['status'] == 'unknown' and absent['signals']['delay2'] is not None and absent['closes'][-1] is None,
            'Unconditional delay deleted missing-price opportunity')
    require(formation_candidates(altered_index, 61) == formation, 'Future data changed formation/ranking')
    checks.append('missing delayed price keeps schedule and cannot alter formation/Top5')

    for end, count in ((data['calendar'][61], 0), (data['calendar'][62], 1)):
        censored = timing_reference(indexes, 61, symbol, support, 1, end)
        require(censored['status'] == 'censored' and len(censored['dates']) == count
                and censored['signals']['immediate'] == timing['signals']['immediate']
                and censored['signals']['delay2'] is censored['signals']['confirm2'] is None,
                'Cutoff deleted immediate or manufactured delayed date')
    censored_unknown = timing_reference(source_index(mutated), 61, symbol, support, 1, data['calendar'][62])
    require(censored_unknown['status'] == 'censored', 'Calendar censoring precedence')
    checks.append('last-two-session censoring preserves I and visible future evidence')

    require(is_halted(False) and is_halted(0) and is_halted('0') and not is_halted(None) and not is_halted(True),
            'Cached suspension flags')
    require(not bar_valid({'date': 'x', 'open': True, 'high': 2., 'low': 1., 'close': 1., 'volume': 1.}, 'x'),
            'Boolean price admitted')
    checks.append('explicit suspension and finite nonboolean OHLCV checks')

    complete = reference_build(indexes, data['calendar'][61], data['calendar'][-1], progress=False)
    first = complete['dailyRows'][0]
    require(first['counts'] == {'sampleCount': 30, 'priorPriceIncomplete': 0, 'priorReturnInvalid': 0,
        'rankPopulationCount': 30, 'strongCount': 6, 'amountUnknownCount': 0,
        'formationPriceUnknownCount': 0, 'setupCount': 6, 'selectedCount': 5}, 'Complete daily count schema')
    population = [{'symbol': s, 'priorOwn60': formation['own'][s], 'priorRelative60': formation['relative'][s],
                   'priorRank60': formation['ranks'][s]} for s in sorted(formation['ranks'])]
    require(first['rankPopulationSha256'] == digest(population), 'Population digest ordering/schema')
    require(len(complete['opportunities']) == 5 and complete['counts']['confirmation'] ==
            {'passed': 5, 'failed': 0, 'unknown': 0, 'censored': 0}, 'Complete opportunity/count schema')
    checks.append('complete daily/opportunity schema, full-population digest and explicit zero counts')

    prefix = deepcopy(data)
    prefix['calendar'] = prefix['calendar'][:62]
    for security in prefix['securities'] + [prefix['benchmark']]:
        security['bars'] = security['bars'][:62]
    early = reference_build(source_index(prefix), prefix['calendar'][61], prefix['calendar'][-1], progress=False)
    require(early['dailyRows'] == complete['dailyRows'][:1]
            and early['schedules']['immediate'] == complete['schedules']['immediate']
            and not early['schedules']['delay2'], 'Appending future changed formation/I')
    require(early['counts']['confirmation']['censored'] == 5, 'Prefix censoring count')
    checks.append('full-builder prefix invariance of formation evidence and immediate schedule')

    for kind in ('calendar', 'bar', 'status'):
        bad = deepcopy(prefix)
        if kind == 'calendar':
            bad['calendar'].append(data['calendar'][62])
        elif kind == 'bar':
            bad['securities'][0]['bars'].append(data['securities'][0]['bars'][62])
        else:
            bad['securities'][0]['tradeStatusByDate'][data['calendar'][62]] = True
        try:
            source_index(bad, prefix['calendar'][-1])
        except ValueError:
            pass
        else:
            raise ValueError('Future/off-calendar evidence admitted: ' + kind)
    checks.append('calendar, raw bar and status evidence cannot exceed registered cutoff')

    changed = deepcopy(data)
    for row in changed['securities'][0]['bars'][:61]:
        row.update(open=100., high=101., low=99., close=100.)
    computed = formation_candidates(source_index(changed), 61)
    require(len(computed['relative']) == 30 and computed['own']['sh600000'] == 0., 'Nonpositive own return excluded before ranking')
    changed = deepcopy(data)
    changed['securities'][-1]['bars'][50]['amount'] = 1e308
    changed['securities'][-1]['bars'][51]['amount'] = 1e308
    audit = reference_build(source_index(changed), data['calendar'][61], data['calendar'][-1], progress=False)
    require(audit['dailyRows'][0]['counts']['amountUnknownCount'] == 1
            and audit['dailyRows'][0]['rankPopulationSha256'] == first['rankPopulationSha256'], 'Amount overflow changed population')
    checks.append('nonpositive own return remains in denominator; overflowing amount stays unknown after ranking')
    print(json.dumps({'passed': True, 'groups': len(checks), 'checks': checks,
                      'newSetupModuleImported': False, 'accountsExecuted': 0}), flush=True)


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
                result, dataset, source, indexes, schedules = verify_period_inputs(args.directory.resolve(), args.inputs_only, plan, proof)
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
