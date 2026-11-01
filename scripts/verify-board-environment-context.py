"""Independent arithmetic replay for the descriptive six-source environment.

Imports only frozen independent references, never the new production panel or
runner. No account is executed and no original market/account source is edited.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean, median
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/board-environment-context-v1'
PARENT = ROOT / 'artifacts/strategy-search/reclaim-relative-feedback-context-v1'
PREVIOUS = ROOT / 'artifacts/strategy-search/joint-cohort-regime-v1'
LAG = 'scripts/verify-leader-lag-followers.py'
LAG_SHA = '3e0244282a82701dcc942f532673c4e17493543cfa335f9e2459da6a7a33333c'
RELATIVE = 'scripts/verify-reclaim-relative-feedback-context.py'
RELATIVE_SHA = '0d1f7ced85201f827b10e4ecf047124514bad56f726939e80fa7316fe756b34d'
PARENT_PROOF_SHA = '16207f17f381bebeb21046cf7d06adcf9ac7e3d387a4de3893c096714afcd7db'
VERSION = 'research-board-environment-v1'
PERIODS, BOARDS = ('2022-2023', '2024-2025'), ('main', 'chinext', 'star')
EXPECTED_ROWS = {'daily': 2907, 'phase': 48, 'epoch': 9, 'continuousAccounts': 96}
_MODULES = {}
BASIS = {
    'clock': 'Each target T uses A=T-1; F=T-6; no T numerical field enters a metric.',
    'price': 'Raw closeA/closeAminus20-1 on 21 complete slots, using signal_leadership_features._observations price validity; no benchmark or amount gate.',
    'amount': 'Independent aligned raw/positive volume/explicit positive amount/no explicit halt gate. Prior20 excludes A. statistics.mean then median of individual ratios; zero is unavailable, not imputed.',
    'matched5': 'Same valid-price intersection at A and the observation five target rows earlier. First five target rows unknown despite cached warmup; breadth change is percentage points.',
    'ranking': 'All known same-board RS20 relative to sh000300, exact tied midrank, no eligibility or amount gate; research_leader_lag._Series/_relative_return/_rank.',
    'gateDifference': 'Legacy price/amount flags recognize False, integer0 or string0; lag rank additionally recognizes float0 and knownSuspended. Missing bar basis inherits the pinned raw declaration, explicit conflicts stay invalid.',
    'retention': 'Original F top percentile>=0.90. Endpoint compares A membership; continuous requires top membership on all F+1..A five sessions. Any known exit dominates unknown days; otherwise unknown stays in original N.',
    'bounds': 'Observation-missingness bounds, not confidence intervals. Empty original cohort has null bounds.',
    'scope': 'Fixed period-start local survivor board cohort, not historical whole-market coverage or market-wide leader identity. Raw-price rights/status limitations are inherited; no reconstruction.',
    'signalsGenerated': False, 'accountExecutions': 0}


def require(value, message):
    if not value:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def positive(value):
    return finite(value) and value > 0


def cross_section_median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else mean(ordered[middle - 1:middle + 1])


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz'
          else path.open(encoding='utf-8-sig')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def ref(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def pinned(item):
    path = (ROOT / item['path']).resolve()
    require(path.is_relative_to(ROOT) and path.is_file() and sha(path) == item['sha256'], 'Artifact changed: ' + item['path'])
    if 'bytes' in item:
        require(path.stat().st_size == item['bytes'], 'Artifact byte count changed')
    return path


def reference(name, expected):
    if name not in _MODULES:
        path = pinned({'path': name, 'sha256': expected})
        spec = importlib.util.spec_from_file_location('_board_env_' + path.stem.replace('-', '_'), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


def lag():
    return reference(LAG, LAG_SHA)


def same(actual, expected, label):
    lag().same(actual, expected, label)


def legacy_observation(day, bar, status):
    """Historical feature-observation validity, independently expressed."""
    halted = status is False or type(status) is int and status == 0 or status == '0'
    aligned = isinstance(bar, dict) and bar.get('date') == day
    basis = aligned and bar.get('priceBasisId', 'raw-unadjusted') == 'raw-unadjusted'
    volume = basis and positive(bar.get('volume'))
    reasons = []
    if bar is None:
        reasons.append('missingDailyBar')
    elif not aligned:
        reasons.append('rawDateMismatch')
    elif not basis:
        reasons.append('rawPriceBasisUnknown')
    elif not all(positive(bar.get(k)) for k in ('open', 'high', 'low', 'close', 'volume')):
        reasons.append('nonpositiveOrUnknownRawOHLCV')
    elif not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
        reasons.append('invalidRawOHLCEnvelope')
    if halted:
        reasons.append('suspensionContradictsPositiveVolume' if volume else 'knownSuspension')
    amount_valid = bool(basis and volume and not halted and positive(bar.get('amount')))
    amount_reasons = []
    if not amount_valid:
        if not aligned or not basis:
            amount_reasons = reasons[:1]
        elif halted:
            amount_reasons = ['suspensionContradictsPositiveVolume' if volume else 'knownSuspension']
        else:
            amount_reasons = ['nonpositiveOrUnknownRawVolume' if not volume else 'explicitRawAmountUnknown']
    return reasons, amount_reasons


def invalid_prefix(reasons):
    count, values = 0, [0]
    for item in reasons:
        count += bool(item)
        values.append(count)
    return values


def window_reasons(item, name, end, slots):
    first = end - slots + 1
    if first < 0 or end < 0:
        return ['insufficientCalendarHistory']
    prefix = item[name + 'Prefix']
    if prefix[end + 1] == prefix[first]:
        return []
    return sorted(set(reason for group in item[name][first:end + 1] for reason in group))


def price_reference(item, at):
    reasons = window_reasons(item, 'price', at, 21)
    if reasons:
        return None, reasons
    ratio = item['bars'][at]['close'] / item['bars'][at - 20]['close']
    if not positive(ratio) or not finite(ratio - 1):
        return None, ['nonfiniteOrUnderflowReturnRatio']
    return ratio - 1, []


def amount_reference(item, at):
    if at < 0:
        return {'amountToday': None, 'prior20MeanAmount': None, 'knownAmountSessions20': 0,
                'amountRatio': None, 'amountReasons': ['insufficientCalendarHistory']}
    first = max(0, at - 20)
    known = at - first - (item['amountPrefix'][at] - item['amountPrefix'][first])
    before = window_reasons(item, 'amount', at - 1, 20)
    current = item['amount'][at]
    value = None if current else item['bars'][at]['amount']
    average = None if before else mean(row['amount'] for row in item['bars'][at - 20:at])
    reasons = sorted(set(before).union(current))
    ratio = None
    if not reasons:
        if not positive(average):
            reasons = ['nonfiniteOrNonpositivePriorMeanAmount']
        else:
            ratio = value / average
            if not positive(ratio):
                ratio, reasons = None, ['nonfiniteOrUnderflowDerivedRatio']
    return {'amountToday': value, 'prior20MeanAmount': average, 'knownAmountSessions20': known,
            'amountRatio': ratio, 'amountReasons': reasons}


def rank_at(indexed, symbols, at):
    old = lag()
    values, reasons = {}, {}
    for symbol in symbols:
        if at < 0:
            values[symbol], reasons[symbol] = None, 'insufficientCalendarHistory'
        else:
            values[symbol] = old.relative_value(indexed[symbol], indexed['__benchmark__'], at, 20)
            reasons[symbol] = old.relative_reason(indexed[symbol], indexed['__benchmark__'], at, 20)
    known, count = old.rank_reference(values)
    ranks = {s: known.get(s) for s in symbols}
    population = {'count': count, 'sha256': digest([
        {'symbol': s, 'value': values[s], 'percentile': ranks[s]} for s in symbols if s in known])}
    return {'values': values, 'ranks': ranks, 'reasons': reasons, 'population': population}


def path_state(path):
    endpoint = path[-1]['status']
    observed = {r['status'] for r in path}
    if 'knownExit' in observed:
        sustained = 'knownExit'
    elif 'unknown' in observed:
        sustained = 'unknown'
    else:
        sustained = 'confirmedRetained'
    return endpoint, sustained


def retention_counts(statuses, retained_label):
    sizes = Counter(statuses)
    n = len(statuses)
    require(set(sizes) <= {retained_label, 'knownExit', 'unknown'}, 'Bad retention status')
    r, e, u = sizes[retained_label], sizes['knownExit'], sizes['unknown']
    require(r + e + u == n, 'Retention denominator shrank')
    return {'retainedCount': r, 'knownExitCount': e, 'unknownCount': u,
            'retentionLower': r / n if n else None, 'retentionUpper': (r + u) / n if n else None}


def matched_reference(current, earlier, population):
    now = current['values']
    result = {'available': earlier is not None, 'earlierDate': earlier['asOfDate'] if earlier else None,
        'todayEvaluableCount': len(now), 'earlierEvaluableCount': None, 'pairedCount': None,
        'pairedCoverageToday': None, 'pairedCoverageEarlier': None, 'pairedCoverageFixed': None,
        'positiveBreadthToday': None, 'positiveBreadthEarlier': None, 'medianReturnToday': None,
        'medianReturnEarlier': None, 'matchedBreadthChange5': None, 'matchedMedianReturnChange5': None,
        'unpairedTodayCount': None, 'unpairedEarlierCount': None,
        'reason': 'earlierTargetSessionUnavailable' if earlier is None else None}
    if earlier is None:
        return result
    before = earlier['values']
    names = sorted(set(now).intersection(before))
    n = len(names)
    result.update(earlierEvaluableCount=len(before), pairedCount=n,
        pairedCoverageToday=n / len(now) if now else None,
        pairedCoverageEarlier=n / len(before) if before else None,
        pairedCoverageFixed=n / population, unpairedTodayCount=len(now) - n, unpairedEarlierCount=len(before) - n)
    if n == 0:
        result['reason'] = 'emptyMatchedPopulation'
        return result
    a, b = [now[s] for s in names], [before[s] for s in names]
    pa, pb = sum(x > 0 for x in a) / n, sum(x > 0 for x in b) / n
    ma, mb = cross_section_median(a), cross_section_median(b)
    change = ma - mb
    result.update(positiveBreadthToday=pa, positiveBreadthEarlier=pb, medianReturnToday=ma,
        medianReturnEarlier=mb, matchedBreadthChange5=100 * (pa - pb),
        matchedMedianReturnChange5=change if finite(change) else None)
    if not finite(change):
        result['reason'] = 'nonfiniteMedianDifference'
    return result


def reference_build(dataset, start, end, frozen_members_by_date, on_evidence=None, progress=None):
    source = lag().source_reference(dataset, start, end)
    calendar, symbols, indexed = source['calendar'], source['symbols'], source['indexed']
    require(set(frozen_members_by_date) == set(source['targetDates']), 'Frozen membership daily coverage')
    positions = {day: i for i, day in enumerate(calendar)}
    legacy = {}
    for symbol in symbols:
        statuses = source['sources'][symbol].get('tradeStatusByDate', {})
        observations = [legacy_observation(day, bar, statuses.get(day))
                        for day, bar in zip(calendar, indexed[symbol]['bars'])]
        p, a = [r[0] for r in observations], [r[1] for r in observations]
        legacy[symbol] = {'bars': indexed[symbol]['bars'], 'price': p, 'amount': a,
                          'pricePrefix': invalid_prefix(p), 'amountPrefix': invalid_prefix(a)}
    rank_cache, history, daily = {}, [], []
    totals = {'sampleCount': len(symbols), 'targetDayCount': len(source['targetDates']),
        'priceEvaluableObservations': 0, 'amountEvaluableObservations': 0, 'matchedAvailableDays': 0,
        'endpointCounts': dict.fromkeys(('retained', 'knownExit', 'unknown'), 0),
        'continuousCounts': dict.fromkeys(('confirmedRetained', 'knownExit', 'unknown'), 0)}
    for offset, day in enumerate(source['targetDates']):
        at, frozen_at = positions[day] - 1, positions[day] - 6
        needed = range(max(0, frozen_at), at + 1)
        for index in needed:
            if index not in rank_cache:
                rank_cache[index] = rank_at(indexed, symbols, index)
        empty = None
        if at < 0 or frozen_at < 0:
            empty = rank_at(indexed, symbols, -1)
        current, prior = rank_cache.get(at, empty), rank_cache.get(frozen_at, empty)
        as_of, cohort = (calendar[at] if at >= 0 else None), (calendar[frozen_at] if frozen_at >= 0 else None)
        original = frozen_members_by_date[day]
        require((original['formationDate'], original['asOfDate'], original['cohortDate']) == (day, as_of, cohort), 'Original member clock changed')
        members = original['topMembers']
        selected = [s for s in symbols if prior['ranks'][s] is not None and prior['ranks'][s] >= .90]
        require([r['symbol'] for r in members] == selected, 'Frozen top identities/order differ from full F rank')
        for member in members:
            s = member['symbol']
            require(member['relativeReturn20'] == prior['values'][s] and member['percentile'] == prior['ranks'][s], 'Frozen member rank/value changed')
        same(original['rankPopulation'], prior['population'], 'Frozen complete rank population')
        selected = set(selected)
        evidence, values, amounts, p_reasons, a_reasons, endpoints, continuous = [], {}, [], Counter(), Counter(), [], []
        for symbol in symbols:
            price, price_reasons = price_reference(legacy[symbol], at)
            amount = amount_reference(legacy[symbol], at)
            if price is not None:
                values[symbol] = price
            if amount['amountRatio'] is not None:
                amounts.append(amount['amountRatio'])
            p_reasons.update(price_reasons)
            a_reasons.update(amount['amountReasons'])
            retention = None
            if symbol in selected:
                path = []
                for k in range(frozen_at + 1, at + 1):
                    ranked = rank_cache[k]
                    percentile = ranked['ranks'][symbol]
                    status = 'unknown' if percentile is None else 'retained' if percentile >= .90 else 'knownExit'
                    path.append({'date': calendar[k], 'relativeReturn20': ranked['values'][symbol],
                                 'percentile': percentile, 'status': status, 'reason': ranked['reasons'][symbol]})
                require(len(path) == 5 and all(r['date'] < day for r in path), 'Retention must use five prior-only sessions')
                endpoint, sustained = path_state(path)
                retention = {'endpointStatus': endpoint, 'continuousStatus': sustained, 'path': path}
                endpoints.append(endpoint)
                continuous.append(sustained)
            evidence.append({'symbol': symbol, 'return20': price, 'priceReasons': price_reasons, **amount,
                'relativeReturn20': current['values'][symbol], 'currentRank': current['ranks'][symbol],
                'rankReason': current['reasons'][symbol], 'frozenMember': symbol in selected, 'retention': retention})
        n, m, population = len(values), len(amounts), len(symbols)
        current_prices = {'asOfDate': as_of, 'values': values}
        matched = matched_reference(current_prices, history[offset - 5] if offset >= 5 else None, population)
        if offset >= 5:
            require(matched['earlierDate'] == cohort, 'Matched source date is not exact F')
        row = {'formationDate': day, 'asOfDate': as_of, 'cohortDate': cohort, 'fixedPopulation': population,
            'price20': {'evaluableCount': n, 'coverage': n / population, 'unavailableCount': population - n,
                'positiveCount': sum(v > 0 for v in values.values()), 'zeroCount': sum(v == 0 for v in values.values()),
                'negativeCount': sum(v < 0 for v in values.values()),
                'positiveReturn20Breadth': sum(v > 0 for v in values.values()) / n if n else None,
                'medianAbsoluteReturn20': cross_section_median(values.values()),
                'reasonCounts': dict(sorted(p_reasons.items()))},
            'amount': {'evaluableCount': m, 'coverage': m / population, 'unavailableCount': population - m,
                'medianRelativeToOwnPrior20': cross_section_median(amounts),
                'knownAmountSessions20Distribution': dict(sorted(Counter(str(e['knownAmountSessions20']) for e in evidence).items())),
                'reasonCounts': dict(sorted(a_reasons.items()))},
            'matched5': matched, 'currentRankPopulation': current['population'],
            'rankReasonCounts': dict(sorted(Counter(r for r in current['reasons'].values() if r is not None).items())),
            'retention': {'cohortSize': len(members), 'endpoint': retention_counts(endpoints, 'retained'),
                          'continuous': retention_counts(continuous, 'confirmedRetained')},
            'frozenMemberDigest': digest(members), 'evidenceDigest': digest(evidence)}
        daily.append(row)
        totals['priceEvaluableObservations'] += n
        totals['amountEvaluableObservations'] += m
        totals['matchedAvailableDays'] += matched['available']
        for key, statuses in (('endpointCounts', endpoints), ('continuousCounts', continuous)):
            for status in statuses:
                totals[key][status] += 1
        if on_evidence:
            on_evidence({'formationDate': day, 'asOfDate': as_of, 'cohortDate': cohort,
                'fixedPopulation': population, 'frozenMemberDigest': row['frozenMemberDigest'],
                'evidenceDigest': row['evidenceDigest'], 'rows': evidence})
        history.append(current_prices)
        for key in list(rank_cache):
            if key < frozen_at + 1:
                del rank_cache[key]
        if progress and (offset % 50 == 49 or offset + 1 == len(source['targetDates'])):
            progress({'completedDays': offset + 1, 'totalDays': len(source['targetDates']), 'date': day})
    return {'version': VERSION, 'calendar': calendar, 'targetDates': source['targetDates'], 'dailyRows': daily,
            'counts': totals, 'basis': BASIS, 'certified': False, 'targetAchieved': False}


def describe_reference(rows):
    require(rows, 'Summary cell unexpectedly empty')
    fields = {'breadth20': ('price20', 'positiveReturn20Breadth'), 'priceCoverage': ('price20', 'coverage'),
        'amountRatio': ('amount', 'medianRelativeToOwnPrior20'), 'amountCoverage': ('amount', 'coverage'),
        'matchedBreadthChange5': ('matched5', 'matchedBreadthChange5'), 'matchedCoverage': ('matched5', 'pairedCoverageFixed'),
        'endpointLower': ('retention', 'endpoint', 'retentionLower'), 'endpointUpper': ('retention', 'endpoint', 'retentionUpper'),
        'continuousLower': ('retention', 'continuous', 'retentionLower'), 'continuousUpper': ('retention', 'continuous', 'retentionUpper'),
        'topPositiveLower': ('priorFeedback', 'top', 'positiveFractionLower'),
        'topPositiveUpper': ('priorFeedback', 'top', 'positiveFractionUpper')}
    result = {'observedStart': rows[0]['formationDate'], 'observedEnd': rows[-1]['formationDate'],
        'asOfStart': rows[0]['asOfDate'], 'asOfEnd': rows[-1]['asOfDate'], 'sessions': len(rows),
        'fixedPopulation': rows[0]['fixedPopulation']}
    require(len({r['fixedPopulation'] for r in rows}) == 1, 'Sample identity changed within phase')
    for key, path in fields.items():
        known = []
        for row in rows:
            value = row
            for step in path:
                value = value[step]
            if value is not None:
                require(finite(value), 'Nonfinite summary value')
                known.append(value)
        result[key] = {'knownDays': len(known), 'unknownDays': len(rows) - len(known),
            'median': median(known) if known else None, 'mean': math.fsum(known) / len(known) if known else None,
            'minimum': min(known) if known else None, 'maximum': max(known) if known else None}
    result['emptyFrozenCohortDays'] = sum(r['retention']['cohortSize'] == 0 for r in rows)
    return result


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, 'Timezone missing from receipt')
    return parsed


def registered_plan(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'board-environment-context-v1', 'Wrong analysis schema')
    same(plan['expectedRows'], EXPECTED_ROWS, 'Fixed six-source output scope')
    for mapping in ('sourcePins', 'inputPins'):
        for path, checksum in plan[mapping].items():
            pinned({'path': path, 'sha256': checksum})
    protocol_path = 'docs/board-environment-context-v1.json'
    require(plan['inputPins'].get(protocol_path) == sha(ROOT / protocol_path), 'Protocol not registered')
    same(plan['protocol'], read(ROOT / protocol_path), 'Frozen protocol content')
    require(plan['newAccounts'] == plan['newStrategyTrials'] == 0 and plan['holdoutClaim'] is False
            and plan['targetAchieved'] is False and plan['protocol']['parameterSearch'] is False, 'Unexpected strategy/parameter search scope')
    tests, started = read(directory / 'pre-extraction-tests.json'), read(directory / 'started.json')
    require(tests['passed'] is True and tests['exitCode'] == 0 and tests['planSha256'] == sha(directory / 'plan.json')
            and tests['sourcePins'] == plan['sourcePins'] and started['plan'] == ref(directory / 'plan.json'), 'Extraction test/start receipt mismatch')
    require(timestamp(plan['registeredAt']) <= timestamp(tests['completedAt']) <= timestamp(started['startedAt']), 'Registration/tests/extraction chronology')
    return plan, started


def inherited_inputs(plan):
    parent_plan, parent_proof = read(PARENT / 'plan.json'), read(PARENT / 'validation.json')
    require(sha(PARENT / 'validation.json') == PARENT_PROOF_SHA and parent_proof['passed'] is True
            and parent_proof['planSha256'] == sha(PARENT / 'plan.json'), 'Original six-source proof changed')
    for name in ('plan.json', 'validation.json'):
        path = PARENT / name
        require(plan['inputPins'].get(path.relative_to(ROOT).as_posix()) == sha(path), 'Parent input not registered')
    same(plan['sourceData'], parent_plan['sourceData'], 'All source identity bindings inherited')
    same(plan['periods'], parent_plan['periods'], 'Both original periods')
    for period in PERIODS:
        for board in BOARDS:
            for item in plan['frozenFeedback'][period][board].values():
                require(item['sha256'] == parent_proof['pins'].get(item['path']) == plan['inputPins'].get(item['path']), 'Feedback/member source not previously audited')
                pinned(item)
            cache = plan['sourceData'][period][board]['accountData']
            require(parent_proof['pins'].get(cache['path']) == cache['sha256'], 'Account source cache not previously bound')
    continuous = read(pinned(plan['continuousAccounts']))
    proof_path = PREVIOUS / 'continuous-phase-validation.json'
    proof = read(proof_path)
    require(plan['inputPins'].get(proof_path.relative_to(ROOT).as_posix()) == sha(proof_path)
            and proof['passed'] is True and proof['posthocFollowup'] is True
            and proof['contextSha256'] == plan['continuousAccounts']['sha256'], 'Continuous-account inherited proof mismatch')
    for field, ref_field in (('sourceReadoutSha256', 'sourceReadout'), ('sourceValidationSha256', 'sourceValidation')):
        require(proof[field] == continuous[ref_field]['sha256'], 'Continuous-account source chain mismatch')
    return parent_proof, continuous, proof


def validate(directory):
    require(not (directory / 'validation.json').exists(), 'Refuse to overwrite independent validation')
    plan, started = registered_plan(directory)
    parent_proof, continuous, continuous_proof = inherited_inputs(plan)
    report = read(directory / 'readout.json')
    require(timestamp(started['startedAt']) <= timestamp(report['completedAt']) <= datetime.now(timezone.utc), 'Readout completion chronology')
    phases = read(pinned(plan['phaseSource']))['phases']
    require(len(phases) == len({p['id'] for p in phases}) == 16, 'Wrong descriptive phase set')
    expected_artifacts, phase_rows, epoch_rows, source_counts = {}, [], [], []
    for period in PERIODS:
        expected_artifacts[period] = {}
        for board in BOARDS:
            folder = directory / period / board
            expected_artifacts[period][board] = {name: ref(folder / name) for name in ('daily.jsonl.gz', 'evidence.jsonl.gz', 'summary.json')}
            same(report['artifacts'][period][board], expected_artifacts[period][board], 'Output artifact refs')
            feeds = plan['frozenFeedback'][period][board]
            frozen_list = list(records(pinned(feeds['member-evidence.jsonl.gz'])))
            frozen = {r['formationDate']: r for r in frozen_list}
            feedback_list = list(records(pinned(feeds['daily-evidence.jsonl.gz'])))
            feedback = {r['formationDate']: r for r in feedback_list}
            require(len(frozen) == len(frozen_list) and len(feedback) == len(feedback_list) and set(frozen) == set(feedback), 'Original feedback day duplication/omission')
            for day, member_record in frozen.items():
                prior = feedback[day]
                require(digest(member_record['topMembers']) == prior['top']['memberDigest'], 'Top membership hash changed')
                require(digest({'topMembers': member_record['topMembers'], 'bottomMembers': member_record['bottomMembers']})
                        == member_record['memberDigest'] == prior['memberDigest'], 'Whole frozen-member record hash changed')
            dataset = read(pinned(plan['sourceData'][period][board]['accountData']))
            evidence_stream = records(folder / 'evidence.jsonl.gz')
            audited_evidence = 0
            symbol_days = 0
            def inspect_evidence(expected):
                nonlocal audited_evidence, symbol_days
                actual = next(evidence_stream, None)
                require(actual is not None, 'Evidence truncated before all source days')
                same(actual, expected, 'Independent whole-source evidence ' + expected['formationDate'])
                require(actual['evidenceDigest'] == digest(actual['rows']), 'Evidence digest mismatch')
                audited_evidence += 1
                symbol_days += len(expected['rows'])
            dates = plan['periods'][period]
            built = reference_build(dataset, dates['startDate'], dates['endDate'], frozen, on_evidence=inspect_evidence,
                progress=lambda value: print(json.dumps({'phase': 'independent-board-environment', 'period': period, 'board': board, **value}), flush=True))
            require(next(evidence_stream, None) is None, 'Unexpected trailing source evidence')
            expected_daily = []
            for row in built['dailyRows']:
                f = feedback[row['formationDate']]
                require((row['asOfDate'], row['cohortDate']) == (f['asOfDate'], f['cohortDate']), 'Original feedback join clock changed')
                expected_daily.append({**row, 'period': period, 'board': board,
                    'priorFeedback': {k: f[k] for k in ('top', 'bottom', 'state', 'difference')}})
            same(list(records(folder / 'daily.jsonl.gz')), expected_daily, 'Every day/statistic/feedback field')
            same(read(folder / 'summary.json'), {k: v for k, v in built.items() if k != 'dailyRows'}, 'Independent metadata/counts/basis')
            require(audited_evidence == len(expected_daily) == (484 if period == PERIODS[0] else 485), 'Source-day count')
            covered = []
            for phase in phases:
                chosen = [r for r in expected_daily if phase['startDate'] <= r['formationDate'] <= phase['endDate']]
                if chosen:
                    covered.extend(r['formationDate'] for r in chosen)
                    phase_rows.append({'period': period, 'board': board, 'phaseId': phase['id'],
                        'phaseTitle': phase['title'], **describe_reference(chosen)})
            require(covered == built['targetDates'], 'Descriptive phases omit/overlap original T dates')
            epochs = ('2022-2023',) if period == PERIODS[0] else ('before20240924', 'from20240924')
            for epoch in epochs:
                chosen = [r for r in expected_daily if epoch == '2022-2023' or
                    ((r['formationDate'] < '2024-09-24') == (epoch == 'before20240924'))]
                epoch_rows.append({'period': period, 'board': board, 'epoch': epoch, **describe_reference(chosen)})
            source_counts.append({'period': period, 'board': board, 'sourceDays': audited_evidence,
                                  'symbolDayRows': symbol_days, **built['counts']})
            print(json.dumps({'completedIndependentSource': [period, board], 'days': audited_evidence, 'symbolDayRows': symbol_days}), flush=True)
            del dataset, frozen, frozen_list, feedback, feedback_list, built, expected_daily
            gc.collect()
    require(sum(c['sourceDays'] for c in source_counts) == 2907 and len(phase_rows) == 48 and len(epoch_rows) == 9, 'Complete environment matrix counts')
    phase_lookup = {(r['period'], r['board'], r['phaseId']): r for r in phase_rows}
    for account_row in continuous['rows']:
        env = phase_lookup[(account_row['period'], account_row['board'], account_row['phaseId'])]
        require((env['observedStart'], env['observedEnd'], env['sessions']) ==
                (account_row['observedStart'], account_row['observedEnd'], account_row['observedSessions']), 'Environment-T and actual equity interval differ')
    expected = {'schema': 'board-environment-context-v1', 'completedAt': report['completedAt'],
        'plan': ref(directory / 'plan.json'), 'artifacts': expected_artifacts, 'dailyCount': 2907,
        'phaseRows': phase_rows, 'epochRows': epoch_rows, 'continuousAccountRows': continuous['rows'],
        'continuousAccountSource': plan['continuousAccounts'],
        'aggregation': 'Daily cross-sectional statistics summarized by equal-day mean and median. Missingness bounds are descriptive, not confidence intervals; phase attribution is by T with information through T-1.',
        'newStrategyTrials': 0, 'newAccounts': 0, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    same(report, expected, 'Complete independent environment readout')
    require(report['continuousAccountRows'] == continuous['rows'], 'Original continuous money/quality altered')
    registered_plan(directory)
    proof = {'schema': 'board-environment-context-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': sha(directory / 'plan.json'), 'readoutSha256': sha(directory / 'readout.json'),
        'parentValidationSha256': PARENT_PROOF_SHA,
        'continuousContextSha256': plan['continuousAccounts']['sha256'],
        'continuousValidationSha256': sha(PREVIOUS / 'continuous-phase-validation.json'),
        'sourcePins': [ref(Path(__file__).resolve()), {'path': LAG, 'sha256': LAG_SHA}],
        'inputPins': {**plan['sourcePins'], **plan['inputPins']}, 'artifacts': expected_artifacts,
        'counts': {'sourceDays': 2907, 'symbolDayRows': sum(r['symbolDayRows'] for r in source_counts),
                   'sources': 6, 'phaseRows': 48, 'epochRows': 9, 'continuousAccountRows': 96,
                   'existingAccountsReadViaAuditedSummary': 12, 'newAccounts': 0, 'newStrategyTrials': 0},
        'sourceCounts': source_counts,
        'auditImplementationTiming': 'Independent implementation pinned separately; no claim that it was part of the analysis preregistration.',
        'checks': ['Every frozen source and prior top-member file binds to its completed independent proof',
                   'All six source calendars and every symbol/day price/amount/rank/path evidence independently replayed',
                   'Price validity and amount validity retain distinct historical semantics and known-status behavior',
                   'Matched five-session change uses the same valid intersection; first five target observations unknown',
                   'Whole-board tied RS20 ranks independently rebuilt; F frozen identities and ranks remain exact',
                   'Endpoint/continuous paths retain original N and known exits dominate missing observations only for continuity',
                   'Only A=T-1 and earlier numerical observations; no T signal/output/account selection',
                   '48 phase and 9 epoch equal-day summaries retain missingness and complete T-date coverage',
                   'All 96 continuous account scalar rows inherited exactly with valuation flags'],
        'scope': {'marketSourceTruth': 'Original frozen covered-survivor source audit inherited; no current TDX reload or source repair',
                  'accountTruth': 'Already audited phase accounts inherited; no new ledger replay or account execution',
                  'interpretation': 'Descriptive exposed development histories; membership retention is not profit, fund identity or causal leadership'},
        'certified': False, 'targetAchieved': False}
    with (directory / 'validation.json').open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return {'passed': True, 'validation': ref(directory / 'validation.json'), 'counts': proof['counts']}


def synthetic_fixture():
    data = lag().synthetic_source()
    start, end = data['calendar'][100], data['calendar'][107]
    frozen = {}
    reference(RELATIVE, RELATIVE_SHA).reference_build(data, start, end,
        on_members=lambda row: frozen.update({row['formationDate']: row}))
    return data, start, end, frozen


def self_test():
    data, start, end, frozen = synthetic_fixture()
    evidence = []
    built = reference_build(data, start, end, frozen, on_evidence=evidence.append)
    require(len(built['dailyRows']) == len(evidence) == 8 and built['counts']['sampleCount'] == 40, 'Synthetic full cohort coverage')
    require(all(not r['matched5']['available'] for r in built['dailyRows'][:5])
            and all(r['matched5']['available'] for r in built['dailyRows'][5:]), 'No matched warmup backfill')
    require(all(r['asOfDate'] < r['formationDate'] for r in built['dailyRows']), 'Prior-only context')
    bar = deepcopy(data['securities'][0]['bars'][100])
    bar['high'] = -1
    price, amount = legacy_observation(bar['date'], bar, None)
    require(price and not amount, 'Invalid OHLC cannot erase independent amount')
    bar = deepcopy(data['securities'][0]['bars'][100])
    require(not legacy_observation(bar['date'], bar, 0.0)[0]
            and lag().bar_issue(bar, 0.0) == 'knownSuspension', 'Legacy/rank status semantics explicitly distinct')
    bar['priceBasisId'] = 'adjusted'
    require(legacy_observation(bar['date'], bar, None)[0] == ['rawPriceBasisUnknown'], 'Explicit price basis conflict kept')
    m = matched_reference({'asOfDate': 'B', 'values': {'x': .1, 'z': -.2}},
                          {'asOfDate': 'A', 'values': {'x': -.1, 'y': .2}}, 3)
    require(m['pairedCount'] == 1 and m['matchedBreadthChange5'] == 100
            and m['unpairedTodayCount'] == m['unpairedEarlierCount'] == 1, 'Same paired denominator and percentage points')
    require(retention_counts([], 'retained')['retentionLower'] is None, 'Empty frozen cohort is not zero retention')
    path = [{'status': s} for s in ('retained', 'knownExit', 'unknown', 'retained', 'retained')]
    require(path_state(path) == ('retained', 'knownExit'), 'Reentry differs from continuity; observed exit dominates unknown')
    require(path_state([{'status': s} for s in ('retained', 'unknown', 'retained', 'retained', 'retained')])
            == ('retained', 'unknown'), 'Missing path without observed exit remains unknown')
    require(retention_counts(['retained', 'knownExit', 'unknown'], 'retained') ==
            {'retainedCount': 1, 'knownExitCount': 1, 'unknownCount': 1, 'retentionLower': 1/3, 'retentionUpper': 2/3}, 'Fixed missingness denominator')
    altered = deepcopy(data)
    for security in altered['securities'] + [altered['benchmark']]:
        for row in security['bars']:
            if row['date'] >= start:
                row.update(open=1e4, high=1e4, low=1e4, close=1e4, amount=1e12, volume=1e8)
    prefix = reference_build(altered, start, start, {start: frozen[start]})
    same(prefix['dailyRows'][0], built['dailyRows'][0], 'T/current/future perturbation cannot alter prior context')
    for row, stored in zip(built['dailyRows'], evidence):
        require(row['evidenceDigest'] == digest(stored['rows']), 'Per-day full evidence canonical digest')
    return {'passed': True, 'syntheticGroups': 12, 'newAccounts': 0, 'realSourceReads': 0}


def main():
    parser = ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--directory', type=Path, default=OUT)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return
    try:
        result = validate(args.directory.resolve())
    except Exception as error:
        attempts = args.directory / 'validation-attempts'
        attempts.mkdir(exist_ok=True)
        path = attempts / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'passed': False, 'verifierSha256': sha(__file__), 'error': str(error),
                       'traceback': traceback.format_exc(), 'pins': {name: sha(args.directory / name) for name in
                        ('plan.json', 'readout.json') if (args.directory / name).is_file()}}, stream, ensure_ascii=False, indent=2)
        raise
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
