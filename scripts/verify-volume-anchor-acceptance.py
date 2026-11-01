"""Independent frozen-source, volume-anchor signal and saved-account audit.

The new signal builder and runner are never imported. Source identity and all
event arithmetic are independently reconstructed. Previously frozen independent
board-aware cash/share/fee audit helpers are reused under exact hashes.
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
DEFAULT = ROOT / 'artifacts/strategy-search/volume-anchor-acceptance-v1'
PARENT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
BOARD_REFERENCE = 'scripts/verify-board-coverage-reclaim.py'
BOARD_REFERENCE_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
PARENT_PLAN_SHA = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
PARENT_SOURCE_VALIDATION_SHA = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
REGISTERED_PLAN_SHA = '587d465c580cee69fae99d667c27971f58fd8b286a67d4c92e7a3ca235a3e221'
SID = 'volumeAnchorAcceptanceV1'
MODES = ('immediate', 'delay3', 'accept3')
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
LABELS = {'immediate': '启动即入', 'delay3': '等待三日', 'accept3': '三日承接'}


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


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz') else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def reference(path, expected):
    require(sha(ROOT / path) == expected, 'Frozen independent helper changed: ' + path)
    spec = importlib.util.spec_from_file_location('_volume_anchor_independent_' + Path(path).stem.replace('-', '_'), ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same(actual, expected, label='value'):
    if type(expected) is int:
        require(type(actual) is int and actual == expected, label + ': integer differs')
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8), label + ': number differs')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + ': fields differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': length differs')
        for n, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f'{label}[{n}]')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': literal differs')


def local(path):
    value = (ROOT / path).resolve()
    require(not Path(path).is_absolute() and value.is_relative_to(ROOT), 'Nonlocal evidence path')
    return value


def pinned(ref):
    require(isinstance(ref, dict) and {'path', 'sha256'} <= set(ref), 'Missing evidence reference')
    path = local(ref['path'])
    require(sha(path) == ref['sha256'], 'Frozen artifact changed: ' + ref['path'])
    if 'bytes' in ref:
        require(path.stat().st_size == ref['bytes'], 'Frozen artifact byte count changed')
    return path


def calendar_check(days):
    require(isinstance(days, list) and bool(days) and all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in days)
            and days == sorted(set(days)), 'Calendar must contain unique increasing ISO dates')


def source_reference(dataset, start, end):
    """Inspect only the declared historical prefix, not future numerical bars."""
    require(dataset.get('priceBasisId') == 'raw-unadjusted', 'Explicit raw dataset required')
    require(date.fromisoformat(start).isoformat() == start and date.fromisoformat(end).isoformat() == end and start <= end,
            'Invalid target bounds')
    calendar_check(dataset['calendar'])
    calendar = [day for day in dataset['calendar'] if day <= end]
    require(calendar, 'Empty historical calendar')
    target = [day for day in calendar if start <= day <= end]
    require(target, 'Empty target period')
    sources = {row['symbol']: row for row in dataset['securities']}
    require(len(sources) == len(dataset['securities']) and bool(sources), 'Duplicate/empty security population')
    require(not dataset.get('corporateActions') and all(not row.get('corporateActions') for row in sources.values()),
            'Unregistered corporate action instructions')
    available = set(calendar)
    maps, issues = {}, {}
    for symbol in sorted(sources):
        source = sources[symbol]
        prefix = [bar for bar in source['bars'] if bar['date'] <= end]
        days = [bar['date'] for bar in prefix]
        if days:
            calendar_check(days)
        require(set(days) <= available, 'Off-calendar historical source bar')
        statuses = source.get('tradeStatusByDate', {})
        require(isinstance(statuses, dict), 'Invalid trade-status mapping')
        require(all(day in available for day in statuses if day <= end), 'Off-calendar historical status')
        maps[symbol] = {bar['date']: bar for bar in prefix}
        issues[symbol] = {day: observation_reasons(source, maps[symbol].get(day), statuses.get(day)) for day in calendar}
    return {'calendar': calendar, 'targetDates': target, 'symbols': sorted(sources), 'sources': sources,
            'bars': maps, 'issues': issues}


def observation_reasons(source, bar, status=None):
    """Independent conjunction of unit, price, status and traded-price checks."""
    if bar is None:
        return ('missingAlignedRawBar',)
    reasons = []
    if bar.get('priceBasisId', 'raw-unadjusted') != 'raw-unadjusted':
        reasons.append('rawPriceBasisUnknown')
    if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
        reasons.append('invalidKnownSuspensionFlag')
    if status is False or (type(status) is int and status == 0) or status == '0' or bar.get('knownSuspended') is True:
        reasons.append('suspensionContradictsPositiveVolume' if positive(bar.get('volume')) else 'knownSuspension')
    volume_ok = source.get('volumeUnit') == bar.get('volumeUnit', 'shares') == 'shares'
    amount_ok = source.get('amountUnit') == bar.get('amountUnit', 'CNY') == 'CNY'
    if not volume_ok:
        reasons.append('volumeUnitNotShares')
    if not amount_ok:
        reasons.append('amountUnitNotCNY')
    prices_ok = all(positive(bar.get(key)) for key in ('open', 'high', 'low', 'close'))
    if not prices_ok or not positive(bar.get('volume')):
        reasons.append('nonpositiveOrUnknownRawOHLCV')
    envelope_ok = prices_ok and bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']
    if prices_ok and not envelope_ok:
        reasons.append('invalidRawOHLCEnvelope')
    if not positive(bar.get('amount')):
        reasons.append('nonpositiveOrUnknownAmount')
    if volume_ok and amount_ok and envelope_ok and positive(bar.get('amount')) and positive(bar.get('volume')):
        average = bar['amount'] / bar['volume']
        if not finite(average):
            reasons.append('nonfiniteTradedAverage')
        elif not bar['low'] - .01 <= average <= bar['high'] + .01:
            reasons.append('tradedAverageOutsideOHLC')
    return tuple(reasons)


def cumulative_reference(bars):
    """Recompute each prefix from original operands, with no rolling reuse."""
    amount = math.fsum(bar['amount'] for bar in bars)
    shares = math.fsum(bar['volume'] for bar in bars)
    product = bars[-1]['close'] * shares
    require(positive(amount) and positive(shares) and finite(product), 'Nonfinite cumulative arithmetic')
    return amount, shares, amount / shares, product, product >= amount


def make_signal(formation, symbol, decision, rank):
    return {'signalId': f'{SID}:{formation}:{symbol}', 'strategyId': SID, 'symbol': symbol,
            'signalDate': decision, 'originalRank': rank}


def acceptance_reference(indexed, symbol, anchor_index):
    days, bars, quality = indexed['calendar'], indexed['bars'][symbol], indexed['issues'][symbol]
    anchor_day = days[anchor_index]
    visible = days[anchor_index + 1:anchor_index + 4]
    steps, broken_prefix = [], False
    for offset, day in enumerate(visible, 1):
        bar = bars.get(day)
        defects = list(quality[day])
        reasons = defects.copy()
        total_amount = total_shares = q = product = accepted = None
        if defects:
            broken_prefix = True
        elif broken_prefix:
            reasons.append('incompleteCumulativeEvidence')
        else:
            contributing = [bars[d] for d in days[anchor_index:anchor_index + offset + 1]]
            # Independently sum every original prefix rather than copying a
            # running total or the producer's derived state.
            try:
                total_amount = math.fsum(row['amount'] for row in contributing)
                total_shares = math.fsum(row['volume'] for row in contributing)
            except (OverflowError, ValueError):
                total_amount = total_shares = None
            if not positive(total_amount) or not positive(total_shares):
                total_amount = total_shares = None
                reasons.append('nonfiniteCumulativeTotals')
                broken_prefix = True
            else:
                q, product = total_amount / total_shares, bar['close'] * total_shares
                if not finite(q) or not finite(product):
                    q = q if finite(q) else None
                    product = product if finite(product) else None
                    reasons.append('nonfiniteAcceptanceComparison')
                else:
                    accepted = product >= total_amount
                    if not accepted:
                        reasons.append('closeBelowAnchoredAverage')
        visible_numbers = {key: bar.get(key) if isinstance(bar, dict) and finite(bar.get(key)) else None
                           for key in ('close', 'amount', 'volume')}
        steps.append({'date': day, 'offset': offset, 'barStatus': 'unknown' if defects else 'valid',
            'barReasons': defects, **visible_numbers, 'cumulativeAmount': total_amount, 'cumulativeShares': total_shares,
            'q': q, 'closeTimesCumulativeShares': product, 'accepted': accepted, 'reasons': reasons})
    incomplete = len(visible) < 3
    tally = Counter('unknown' if row['accepted'] is None else 'pass' if row['accepted'] else 'fail' for row in steps)
    state = 'censored' if incomplete else 'unknown' if tally['unknown'] else 'failed' if tally['fail'] else 'passed'
    reasons = ['decisionOutsideTarget'] if incomplete else []
    reasons += [row['date'] + ':' + reason for row in steps for reason in row['reasons']]
    return {'status': state, 'scheduledDate': None if incomplete else visible[-1], 'dates': visible,
        'requiredSessions': 3, 'availableSessions': len(visible), 'complete': not incomplete and not tally['unknown'],
        'knownPassCount': tally['pass'], 'knownFailCount': tally['fail'], 'unknownStepCount': tally['unknown'],
        'steps': steps, 'reasons': reasons}


def reference_build(dataset, start, end):
    """Independently recompute every symbol/date before selection or clocks."""
    indexed = source_reference(dataset, start, end)
    calendar, symbols = indexed['calendar'], indexed['symbols']
    lookup = {day: i for i, day in enumerate(calendar)}
    daily, opportunities = [], []
    schedules = {mode: [] for mode in MODES}
    acceptance_counts = Counter({state: 0 for state in ('passed', 'failed', 'unknown', 'censored')})
    for day in indexed['targetDates']:
        at = lookup[day]
        prior_days = calendar[max(0, at - 20):at]
        counts = {'sampleCount': len(symbols), 'priorWindowUnknownCount': 0, 'formationBarUnknownCount': 0,
            'anchorUnknownCount': 0, 'anchorKnownFalseCount': 0, 'setupCount': 0, 'selectedCount': 0}
        unknowns, candidates = Counter(), []
        for symbol in symbols:
            quality, bars = indexed['issues'][symbol], indexed['bars'][symbol]
            prior_bad = len(prior_days) != 20 or any(quality[d] for d in prior_days)
            current_bad = bool(quality[day])
            if prior_bad or current_bad:
                counts['priorWindowUnknownCount'] += int(prior_bad)
                counts['formationBarUnknownCount'] += int(current_bad)
                counts['anchorUnknownCount'] += 1
                reasons = set(reason for d in [*prior_days, day] for reason in quality[d])
                if len(prior_days) != 20:
                    reasons.add('insufficientPriorCalendarHistory')
                unknowns.update(reasons)
                continue
            previous, current = [bars[d] for d in prior_days], bars[day]
            high = max(row['high'] for row in previous)
            volume_high = max(row['volume'] for row in previous)
            if current['close'] <= high or current['volume'] <= volume_high:
                counts['anchorKnownFalseCount'] += 1
                continue
            try:
                mean_amount = math.fsum(row['amount'] for row in previous) / 20
            except (OverflowError, ValueError):
                mean_amount = None
            if not positive(mean_amount):
                counts['anchorUnknownCount'] += 1
                unknowns['nonfinitePriorMeanAmount'] += 1
                continue
            candidates.append({'symbol': symbol, 'priorAmount20': mean_amount, 'prior20High': high,
                'prior20VolumeHigh': volume_high, 'formationClose': current['close'],
                'formationVolume': current['volume'], 'formationAmount': current['amount']})
        candidates.sort(key=lambda row: (-row['priorAmount20'], row['symbol']))
        selected_ids = []
        for rank, candidate in enumerate(candidates, 1):
            candidate['originalRank'], candidate['selected'] = rank, rank <= 5
            if rank > 5:
                continue
            symbol = candidate['symbol']
            formation_id = f'{SID}:{day}:{symbol}'
            selected_ids.append(formation_id)
            acceptance = acceptance_reference(indexed, symbol, at)
            acceptance_counts[acceptance['status']] += 1
            decisions = {'immediate': day, 'delay3': acceptance['scheduledDate'], 'accept3': acceptance['scheduledDate']}
            signals = {'immediate': make_signal(day, symbol, day, rank), 'delay3': None, 'accept3': None}
            if acceptance['complete']:
                signals['delay3'] = make_signal(day, symbol, acceptance['scheduledDate'], rank)
            if acceptance['status'] == 'passed':
                signals['accept3'] = make_signal(day, symbol, acceptance['scheduledDate'], rank)
            for mode in MODES:
                if signals[mode] is not None:
                    schedules[mode].append(deepcopy(signals[mode]))
            opportunities.append({k: v for k, v in candidate.items() if k != 'selected'} | {
                'formationId': formation_id, 'formationDate': day, 'priorDate': calendar[at - 1],
                'decisionDates': decisions, 'acceptance': acceptance, 'signals': signals})
        counts.update(setupCount=len(candidates), selectedCount=len(selected_ids))
        require(counts['anchorUnknownCount'] + counts['anchorKnownFalseCount'] + counts['setupCount'] == len(symbols),
                'Independent daily population partition')
        daily.append({'formationDate': day, 'priorDate': calendar[at - 1] if at else None,
            'priorWindowDates': prior_days, 'counts': counts, 'unknownReasonCounts': dict(sorted(unknowns.items())),
            'candidates': candidates, 'selectedFormationIds': selected_ids})
    for values in schedules.values():
        values.sort(key=lambda row: (row['signalDate'], row['originalRank'], row['symbol'], row['signalId']))
    counts = {'sampleCount': len(symbols), 'targetDayCount': len(daily),
        'setupCount': sum(row['counts']['setupCount'] for row in daily), 'opportunityCount': len(opportunities),
        'daysWithSetups': sum(bool(row['candidates']) for row in daily),
        'daysWithSelected': sum(bool(row['selectedFormationIds']) for row in daily),
        'acceptance': dict(acceptance_counts), 'signals': {mode: len(schedules[mode]) for mode in MODES}}
    basis = {'priceBasisId': 'raw-unadjusted', 'volumeUnit': 'shares', 'amountUnit': 'CNY',
        'priorSlots': 20, 'delaySlots': 3, 'topN': 5, 'tradedAverageEnvelopeTolerance': .01,
        'anchor': 'close_A > prior20 max high AND volume_A > prior20 max volume; no relative-strength screen',
        'ranking': 'prior20 mean explicit amount descending, then symbol ascending; original Top5 fixed at A',
        'acceptance': 'at every A+1..A+3 close: close_d * fsum(shares_A..d) >= fsum(amount_A..d)',
        'unknownPriority': 'censored > unknown > failed > passed; observed partial failures and unknowns retained',
        'unknownReasonCountUnit': 'each symbol/reason at most once per formation day; prior/current incomplete counts can overlap',
        'eligibility': 'unknown historical status retained, not a certified trading-history claim',
        'meaning': 'observed event-anchored traded average; not holder cost or investor identity',
        'future': 'numeric bars after registered end ignored; no after-end evidence in windows or schedules'}
    return {'version': 'volume-anchor-acceptance-v1', 'strategyId': SID, 'modes': list(MODES), 'calendar': calendar,
        'targetDates': indexed['targetDates'], 'dailyRows': daily, 'opportunities': opportunities, 'schedules': schedules,
        'counts': counts, 'basis': basis, 'certified': False, 'targetAchieved': False}


def fixture_source(symbol_count=6):
    """Small complete artificial source; it contains no actual market data."""
    days = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(24)]
    securities = []
    for n in range(symbol_count):
        symbol = f'sh60{n:04d}'
        bars = []
        for i, day in enumerate(days):
            price, volume = (10., 100.) if i < 20 else (11., 200. if i == 20 else 100.)
            bars.append({'date': day, 'open': price, 'high': price + .1, 'low': price - .1,
                'close': price, 'volume': volume, 'amount': price * volume, 'priceBasisId': 'raw-unadjusted'})
        securities.append({'symbol': symbol, 'bars': bars, 'volumeUnit': 'shares', 'amountUnit': 'CNY'})
    return {'calendar': days, 'securities': securities, 'priceBasisId': 'raw-unadjusted'}, days[20], days[-1]


def self_test():
    checks = []
    data, start, end = fixture_source()
    before = digest(data)
    original = reference_build(data, start, end)
    require(digest(data) == before, 'Reference mutated source')
    require(original['counts']['setupCount'] == 6 and original['counts']['opportunityCount'] == 5,
            'Full anchor candidate population/Top5 differs')
    require(all(len(original['schedules'][mode]) == 5 for mode in MODES), 'Exact equality acceptance or schedules differ')
    require(all(row['acceptance']['status'] == 'passed' for row in original['opportunities']), 'Equality does not pass')
    checks.append('causal original Top5, equal-rank tie ordering, three-step equality and source immutability')
    modified = deepcopy(data)
    modified['securities'][0]['bars'][20]['volume'] = 100.
    modified['securities'][0]['bars'][20]['amount'] = 1100.
    equal_volume = reference_build(modified, start, end)
    require(equal_volume['counts']['setupCount'] == 5, 'Equal prior maximum volume accepted')
    modified = deepcopy(data)
    bar = modified['securities'][0]['bars'][20]
    bar.update(open=10.1, low=10., close=10.1, high=10.2, amount=2020.)
    require(reference_build(modified, start, end)['counts']['setupCount'] == 5, 'Equal prior high close accepted')
    checks.append('strict anchor high and volume boundaries')
    modified = deepcopy(data)
    del modified['securities'][0]['bars'][21]
    missing = reference_build(modified, start, end)
    require(missing['schedules']['immediate'] == original['schedules']['immediate']
            and len(missing['schedules']['delay3']) == len(missing['schedules']['accept3']) == 4,
            'Missing future data deletes immediate or backfills sixth anchor')
    require(missing['opportunities'][0]['acceptance']['status'] == 'unknown'
            and missing['opportunities'][0]['acceptance']['unknownStepCount'] == 3,
            'Missing cumulative slot skipped or filled')
    checks.append('future missingness preserves immediate and prevents cumulative gap skipping or rank-six backfill')
    modified = deepcopy(data)
    modified['securities'][0]['bars'][21].update(open=9., low=8.9, high=9.1, close=9., amount=900.)
    del modified['securities'][0]['bars'][22]
    mixed = reference_build(modified, start, end)['opportunities'][0]['acceptance']
    require(mixed['status'] == 'unknown' and mixed['knownFailCount'] == 1 and mixed['unknownStepCount'] == 2,
            'A known failure hides later unknown evidence')
    censored = reference_build(modified, start, data['calendar'][22])['opportunities'][0]['acceptance']
    require(censored['status'] == 'censored' and censored['knownFailCount'] == 1 and censored['unknownStepCount'] == 1,
            'Right censoring loses visible failure/unknown evidence')
    checks.append('censored > unknown > failed priority with retained visible-step evidence')
    modified = deepcopy(data)
    modified['securities'][0]['volumeUnit'] = 'lots'
    invalid_units = reference_build(modified, start, end)
    require(invalid_units['counts']['setupCount'] == 5, 'Lots silently treated as shares')
    modified = deepcopy(data)
    modified['securities'][0]['bars'][20]['amount'] *= 100
    require(reference_build(modified, start, end)['counts']['setupCount'] == 5, 'Inconsistent traded-price units silently repaired')
    checks.append('strict explicit source units and amount/volume price consistency')
    modified = deepcopy(data)
    next_day = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    modified['calendar'].append(next_day)
    for security in modified['securities']:
        security['bars'].append({'date': next_day, 'open': float('nan'), 'volume': 0})
    require(reference_build(modified, start, end) == original, 'Future numerical data changes historical result')
    checks.append('future numerical append invariance')
    # A new legitimate record day starts another event without replacing A.
    modified = deepcopy(data)
    for security in modified['securities']:
        security['bars'][21].update(open=12., close=12., high=12.1, low=11.9, volume=300., amount=3600.)
    repeated = reference_build(modified, start, end)
    require(repeated['counts']['opportunityCount'] == 10
            and [r['formationId'] for r in repeated['opportunities'][:5]] == [r['formationId'] for r in original['opportunities']],
            'Repeated record day suppresses or reanchors earlier events')
    checks.append('repeated anchors remain independent identities with no position suppression or reanchoring')
    board = reference(BOARD_REFERENCE, BOARD_REFERENCE_SHA)
    accounting = reference(board.ACCOUNT_REFERENCE, board.ACCOUNT_REFERENCE_SHA)
    for name, expected in {accounting.COST_REFERENCE: accounting.COST_REFERENCE_SHA, **accounting.cost_reference().REFERENCES}.items():
        require(sha(local(name)) == expected, 'Independent helper dependency hash differs')
    checks.append('frozen independent board/fee/account helper dependency closure')
    print(json.dumps({'passed': True, 'groups': len(checks), 'checks': checks, 'accountsExecuted': 0}), flush=True)


def registered_plan(directory):
    require(REGISTERED_PLAN_SHA is not None and sha(directory / 'plan.json') == REGISTERED_PLAN_SHA,
            'Plan is not registered or changed')
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'volume-anchor-acceptance-v1' and plan['strategyId'] == SID
            and plan['modes'] == list(MODES) and plan['boards'] == list(BOARDS)
            and list(plan['periods']) == list(PERIODS) and plan['labels'] == LABELS, 'Registered identities differ')
    for name, number in {'newAccounts': 18, 'newSetupMechanisms': 1, 'newEntryPolicies': 3,
                         'newUniverseVariants': 0, 'referenceAccountsReused': 0}.items():
        require(type(plan[name]) is int and plan[name] == number, 'Registered trial counts differ: ' + name)
    require(plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False, 'Unregistered optimization or certification')
    for path, expected in {**plan['sourcePins'], **plan['inputPins']}.items():
        require(sha(local(path)) == expected, 'Registered artifact changed: ' + path)
    require(read(pinned(plan['protocol'])) == plan['rules'], 'Plan does not contain exact protocol')
    require(sha(PARENT / 'plan.json') == PARENT_PLAN_SHA, 'Inherited source plan changed')
    parent = read(PARENT / 'plan.json')
    for period in PERIODS:
        config = deepcopy(parent['periods'][period]['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        require(plan['periods'][period] == {'startDate': config['startDate'], 'endDate': config['endDate'], 'config': config},
                'Account configuration changed beyond registered signal identity')
    return plan


def inherited_sources(plan):
    require(sha(PARENT / 'source-validation.json') == PARENT_SOURCE_VALIDATION_SHA, 'Inherited source audit changed')
    prior = read(PARENT / 'source-validation.json')
    require(prior['passed'] is True and prior['planSha256'] == PARENT_PLAN_SHA
            and prior['sourceCompletionSha256'] == sha(PARENT / 'source-completion.json')
            and prior['verifierSha256'] == BOARD_REFERENCE_SHA, 'Inherited source proof is stale')
    require(len(prior['datasetPins']) == 6 and list(plan['sourceData']) == list(PERIODS), 'Exactly six original source datasets required')
    for path, expected in {**prior['datasetPins'], **prior['manifestPins']}.items():
        require(sha(local(path)) == expected, 'Inherited source evidence changed: ' + path)
    for period in PERIODS:
        require(list(plan['sourceData'][period]) == list(BOARDS), 'Original source board matrix changed')
        manifest_path = PARENT / period / 'source-manifest.json'
        manifest = read(manifest_path)
        for board in BOARDS:
            binding = plan['sourceData'][period][board]
            require(set(binding) == {'accountData', 'accountDatasetDigest', 'sourceManifest', 'sourceValidation', 'members'},
                    'Source binding fields differ')
            cache = pinned(binding['accountData'])
            require(cache == (PARENT / period / (board + '.account-data.json.gz')).resolve()
                    and prior['datasetPins'][cache.relative_to(ROOT).as_posix()] == binding['accountData']['sha256'],
                    'Dataset is not original independently audited cache')
            require(pinned(binding['sourceManifest']) == manifest_path.resolve()
                    and pinned(binding['sourceValidation']) == (PARENT / 'source-validation.json').resolve(), 'Source proof link differs')
            require(binding['accountDatasetDigest'] == manifest['boards'][board]['accountData']['datasetDigest']
                    and binding['members'] == manifest['boards'][board]['includedCount'], 'Inherited cohort/dataset digest differs')
    return prior


def input_receipts(directory, plan):
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccounts'] == 18 and list(completion['outputs']) == list(PERIODS), 'Incomplete eighteen-schedule receipt')
    receipts = {}
    for period in PERIODS:
        require(list(completion['outputs'][period]) == list(BOARDS), 'Input board matrix changed')
        receipts[period] = {}
        for board in BOARDS:
            folder = directory / period / board
            path = pinned(completion['outputs'][period][board])
            require(path == (folder / 'input-completion.json').resolve(), 'Input receipt outside original folder')
            receipt = read(path)
            require(receipt['planSha256'] == REGISTERED_PLAN_SHA and receipt['period'] == period and receipt['board'] == board,
                    'Input receipt identity changed')
            require({k: receipt[k] for k in plan['sourceData'][period][board]} == plan['sourceData'][period][board],
                    'Inputs no longer bind all original source facts')
            expected_files = {'opportunities.jsonl.gz', 'daily-evidence.jsonl.gz', 'signal-metadata.json', 'input-counts.json'} | {
                mode + '.signals.json' for mode in MODES}
            require(set(receipt['files']) == expected_files, 'Exact signal input closure missing')
            for name, ref in receipt['files'].items():
                require(pinned(ref) == (folder / name).resolve(), 'Input artifact outside original folder')
            require(datetime.fromisoformat(plan['registeredAt']) <= datetime.fromisoformat(receipt['completedAt'])
                    <= datetime.fromisoformat(completion['completedAt']), 'Inputs prepared before protocol registration')
            receipts[period][board] = receipt
    return completion, receipts


def write_proof(directory, filename, proof):
    with (directory / filename).open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'artifact': filename, 'sha256': sha(directory / filename), 'counts': proof['counts']}), flush=True)


def verify_inputs(directory):
    started = time.perf_counter()
    require(not (directory / 'input-validation.json').exists(), 'Input proof already exists')
    plan = registered_plan(directory)
    source = inherited_sources(plan)
    completion, receipts = input_receipts(directory, plan)
    artifacts, reports = {}, {}
    opportunities = days = evaluations = 0
    schedule_counts = Counter({mode: 0 for mode in MODES})
    for period in PERIODS:
        artifacts[period], reports[period] = {}, {}
        for board in BOARDS:
            receipt, folder = receipts[period][board], directory / period / board
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'] and data['period'] == period and data['board'] == board
                    and len(data['securities']) == receipt['members'], 'Input source contents or fixed cohort changed')
            config = plan['periods'][period]['config']
            print(json.dumps({'phase': 'independent-anchor-input-start', 'period': period, 'board': board}), flush=True)
            rebuilt = reference_build(data, config['startDate'], config['endDate'])
            same(records(folder / 'daily-evidence.jsonl.gz'), rebuilt['dailyRows'], 'Every board day candidate/rank/unknown count')
            same(records(folder / 'opportunities.jsonl.gz'), rebuilt['opportunities'], 'Every anchor/three-step numerator/decision')
            same(read(folder / 'signal-metadata.json'), {k: v for k, v in rebuilt.items() if k not in ('dailyRows', 'opportunities', 'schedules')},
                 'All signal metadata')
            require(read(folder / 'input-counts.json') == receipt['counts'] == rebuilt['counts'], 'All signal counts differ')
            for mode in MODES:
                require(read(folder / (mode + '.signals.json')) == rebuilt['schedules'][mode], 'Exact five-field schedule/order differs: ' + mode)
                schedule_counts[mode] += len(rebuilt['schedules'][mode])
            n, d = len(rebuilt['opportunities']), len(rebuilt['dailyRows'])
            opportunities += n
            days += d
            evaluations += len(data['securities']) * d
            reports[period][board] = {'members': len(data['securities']), 'counts': rebuilt['counts'],
                'anchorOpportunities': n, 'targetSessions': d, 'symbolDayEvaluations': len(data['securities']) * d,
                'accountDatasetDigest': receipt['accountDatasetDigest']}
            artifacts[period][board] = receipt['files']
            print(json.dumps({'phase': 'independent-anchor-input-passed', 'period': period, 'board': board,
                'opportunities': n, 'signals': rebuilt['counts']['signals']}), flush=True)
            del data, rebuilt
            gc.collect()
    registered_plan(directory)
    proof = {'schema': 'volume-anchor-acceptance-input-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'inputCompletionSha256': sha(directory / 'input-completion.json'),
        'parentSourceValidationSha256': PARENT_SOURCE_VALIDATION_SHA, 'parentSourceCompletionSha256': sha(PARENT / 'source-completion.json'),
        'inputArtifacts': artifacts, 'datasetPins': source['datasetPins'], 'periods': reports,
        'counts': {'boardPeriodInputs': 6, 'schedules': 18, 'anchorOpportunities': opportunities,
            'dailyRows': days, 'symbolDayEvaluations': evaluations, 'signals': dict(schedule_counts)},
        'checks': ['All six unchanged source caches and prior independent raw-source proof bound by exact hashes',
            'All per-board original anchor predicates, full candidate ranks, Top5 identities and no future-based replacement',
            'Every three-day cumulative amount/shares numerator/denominator, strict or equality boundary and unknown/censor priority',
            'All eighteen original ordered five-field schedules independently recomputed before accounts'],
        'scope': {'productionSignalBuilderImported': False, 'rawSourceReloaded': False, 'accountsExecuted': 0,
            'sourceAuthenticity': 'Inherited exact-bound prior independent source audit; this stage reads frozen caches only.'},
        'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'input-validation.json', proof)


def verify_accounts(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Full proof already exists')
    plan = registered_plan(directory)
    source = inherited_sources(plan)
    completion, receipts = input_receipts(directory, plan)
    inputs = read(directory / 'input-validation.json')
    require(inputs['passed'] is True and inputs['planSha256'] == REGISTERED_PLAN_SHA and inputs['verifierSha256'] == sha(__file__)
            and inputs['inputCompletionSha256'] == sha(directory / 'input-completion.json')
            and inputs['parentSourceValidationSha256'] == PARENT_SOURCE_VALIDATION_SHA
            and inputs['datasetPins'] == source['datasetPins'], 'Independent input proof stale')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve()
            and pinned(start['inputValidation']) == (directory / 'input-validation.json').resolve()
            and datetime.fromisoformat(completion['completedAt']) <= datetime.fromisoformat(inputs['verifiedAt'])
            <= datetime.fromisoformat(start['startedAt']), 'Account started before complete independent signal validation')
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'volume-anchor-acceptance-comparison-v1' and comparison['planSha256'] == REGISTERED_PLAN_SHA,
            'Comparison identity differs')
    for key in ('newAccounts', 'newSetupMechanisms', 'newEntryPolicies', 'newUniverseVariants', 'referenceAccountsReused',
                'limitations', 'certified', 'targetAchieved'):
        require(comparison[key] == plan[key], 'Comparison scope differs: ' + key)
    require([(r['period'], r['board'], r['id'], r['policy']) for r in comparison['rows']]
            == [(p, b, m, m) for p in PERIODS for b in BOARDS for m in MODES], 'Complete eighteen-account matrix/order')
    board_audit = reference(BOARD_REFERENCE, BOARD_REFERENCE_SHA)
    # Only the configurable identity of this newly imported independent helper
    # changes in memory; its frozen on-disk arithmetic and dependencies do not.
    board_audit.SID = SID
    accounting = reference(board_audit.ACCOUNT_REFERENCE, board_audit.ACCOUNT_REFERENCE_SHA)
    dependencies = {BOARD_REFERENCE: BOARD_REFERENCE_SHA, board_audit.ACCOUNT_REFERENCE: board_audit.ACCOUNT_REFERENCE_SHA,
                    accounting.COST_REFERENCE: accounting.COST_REFERENCE_SHA, **accounting.cost_reference().REFERENCES}
    for name, expected in dependencies.items():
        require(sha(local(name)) == expected, 'Independent accounting dependency changed')
    reports = {}
    require(list(comparison['artifacts']) == list(PERIODS), 'Account artifact period closure')
    for period in PERIODS:
        reports[period] = {}
        require(list(comparison['artifacts'][period]) == list(BOARDS), 'Account artifact board closure')
        for board in BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            require(inputs['inputArtifacts'][period][board] == receipt['files'], 'Frozen inputs changed since independent audit')
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'], 'Account source content digest changed')
            require(list(comparison['artifacts'][period][board]) == list(MODES), 'Account mode closure')
            reports[period][board] = {}
            for mode in MODES:
                files = comparison['artifacts'][period][board][mode]
                require(set(files) == {mode + s for s in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')},
                        'Full saved account closure missing')
                for name, ref in files.items():
                    require(pinned(ref) == (folder / name).resolve(), 'Account artifact outside original folder')
                result, checkpoint, summary = (read(folder / (mode + suffix))
                    for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
                schedule = read(folder / (mode + '.signals.json'))
                require(len(schedule) == inputs['periods'][period][board]['counts']['signals'][mode], 'Schedule count drift')
                checked = board_audit.verify_board_account(result, checkpoint, summary, schedule,
                    plan['periods'][period]['config'], data, period, accounting)
                require(summary['policyId'] == mode and summary['policyLabel'] == LABELS[mode], 'Account summary policy differs')
                expected = board_audit.comparison_reference(summary, period, board, LABELS[mode], len(schedule))
                expected.update(id=mode, policy=mode)
                row = next(r for r in comparison['rows'] if (r['period'], r['board'], r['id']) == (period, board, mode))
                same(row, expected, 'All comparison statistics')
                reports[period][board][mode] = checked
                print(json.dumps({'phase': 'independent-anchor-account-passed', 'period': period, 'board': board,
                                  'mode': mode, 'fills': checked['fills']}), flush=True)
                del result, checkpoint, summary
                gc.collect()
            del data
            gc.collect()
    registered_plan(directory)
    proof = {'schema': 'volume-anchor-acceptance-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'comparisonSha256': sha(directory / 'comparison.json'), 'inputValidationSha256': sha(directory / 'input-validation.json'),
        'inputCompletionSha256': sha(directory / 'input-completion.json'), 'parentSourceValidationSha256': PARENT_SOURCE_VALIDATION_SHA,
        'datasetPins': source['datasetPins'], 'inputArtifacts': inputs['inputArtifacts'], 'accountArtifacts': comparison['artifacts'],
        'independentReferences': dependencies, 'periods': reports,
        'counts': {'accounts': 18, 'reusedAccounts': 0, 'anchorOpportunities': inputs['counts']['anchorOpportunities'],
            'signals': inputs['counts']['signals'], 'fills': sum(r['fills'] for p in reports.values() for b in p.values() for r in b.values())},
        'checks': ['All eighteen independently validated schedules frozen before account execution',
            'All complete saved ledgers: original dated fees, raw opening/slippage, board lots/limits, cash/shares, T+1 and reserve budgets',
            'Closed/open episodes, noProgress5 and risk-exit evidence, checkpoints, summaries and every reported comparison statistic'],
        'scope': {'productionSignalBuilderImported': False, 'accountsExecutedByVerifier': 0,
            'ledgerAudit': 'Exact-bound independent board-aware accounting helpers; only the new strategy identity is configured in memory.',
            'limitations': ['No separate resimulation of every eligibility/capacity/order decision.',
                'Inherited current-survivor/raw-price/status/rights/opening-capacity limitations remain.']},
        'certified': False, 'targetAchieved': False, 'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'validation.json', proof)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--inputs-only', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    directory = args.directory.resolve()
    stage = 'inputs' if args.inputs_only else 'accounts'
    try:
        (verify_inputs if args.inputs_only else verify_accounts)(directory)
    except Exception as error:
        folder = directory / 'validation-attempts'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        failure = {'passed': False, 'stage': stage, 'at': stamp, 'verifierSha256': sha(__file__),
            'planSha256': sha(directory / 'plan.json') if (directory / 'plan.json').exists() else None,
            'error': str(error), 'traceback': traceback.format_exc(), 'accountsExecuted': 0}
        path = folder / (stamp + '-' + stage + '-failed.json')
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}), flush=True)
        raise


if __name__ == '__main__':
    main()
