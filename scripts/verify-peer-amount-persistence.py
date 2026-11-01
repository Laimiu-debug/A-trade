"""Independent fixed-peer amount-persistence audit.

This verifier never imports the new amount policy module or executes an
account. It recomputes amount arithmetic from saved aligned source inputs,
then delegates unchanged account bookkeeping to pinned independent auditors.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/peer-amount-persistence-v1'
COST_REFERENCE = 'scripts/verify-relative-repair-cost-stress.py'
COST_REFERENCE_SHA = 'df00a1573933b4624563b4db0f451a731449c55c180b8b6793f5967b147e762a'
PEER_REFERENCE = 'scripts/verify-signal-leader-peer-expansion-result.py'
PEER_REFERENCE_SHA = 'e57fbf05c066b04eb616de3c927c39d929aad3313c5101110ddb4a9e22fee5d2'
REGISTERED_PLAN_SHA = 'cef2279b5582bb06cc525c37c2df100f366adef4e809870818561bfd689537c7'
RUNNER_SHA = '0487ac397c26a26cd6f09fb27c5acf67e27c59fc9dc3e03539249dc729535c37'
PERIODS = ('2022-2023', '2024-2025')
POLICIES = ('currentActivity', 'persistent8')
SID = 'fixedPeerAmountPersistenceV1'
OLD = 'artifacts/strategy-search/signal-leader-peer-expansion-v1/attempts/attempt-0001'
NEW = 'artifacts/strategy-search/signal-supplemental-persistent-leader-v1/attempts/attempt-0001'
GATES = (('meanStrengthPercentile20', '>=', .9), ('minimumStrengthPercentile20', '>=', .8),
         ('relativeReturn60Percentile', '>=', .9), ('closePathMaxDrawdown20', '<=', .1),
         ('signedPathEfficiency20', '>=', .35))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def same(actual, expected, label='root'):
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8),
                f'{label}: {actual!r} != {expected!r}')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'{label}: keys differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f'{label}: length differs')
        for index, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f'{label}[{index}]')
    else:
        require(type(actual) is type(expected) and actual == expected, f'{label}: literal differs')


def read(path):
    path = Path(path)
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz')
          else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            value = json.loads(line)
            require(isinstance(value, dict), 'JSONL row must be an object')
            yield value


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


def require_calendar(calendar):
    require(isinstance(calendar, list) and calendar == sorted(set(calendar)), 'Calendar must be unique and ordered')
    for day in calendar:
        require(type(day) is str and date.fromisoformat(day).isoformat() == day, 'Noncanonical calendar date')


def amount_issue(value):
    # Missing and invalid values are never silently converted to zero.
    if value is None:
        return 'missingAmount'
    if not finite(value):
        return 'nonfiniteOrInvalidAmount'
    if value < 0:
        return 'negativeAmount'
    return None


def reference_universe(calendar, signal_date, universe, dates, amounts_by_symbol):
    """Reconstruct U(T) using every original symbol and exactly 30 slots.

    Amount inputs are raw scalar slots (including explicit zero and null),
    already extracted from separately hash-bound saved input artifacts.
    """
    require_calendar(calendar)
    require(signal_date in calendar, 'Signal date absent from source calendar')
    index = calendar.index(signal_date)
    expected_dates = calendar[max(0, index - 29):index + 1]
    same(dates, expected_dates, 'Exact 30 source-calendar slots through T')
    require(isinstance(universe, list) and universe == sorted(set(universe))
            and all(type(symbol) is str and symbol for symbol in universe), 'Invalid original universe')
    require(isinstance(amounts_by_symbol, dict) and set(amounts_by_symbol) == set(universe),
            'Complete original-universe source amounts required, not just retained U(T)')
    included, excluded = [], []
    issue_counts = Counter()
    for symbol in universe:
        values = amounts_by_symbol[symbol]
        require(isinstance(values, list) and len(values) == len(dates), 'Amount slot count differs: ' + symbol)
        issues = [{'date': day, 'reason': reason} for day, value in zip(dates, values)
                  if (reason := amount_issue(value)) is not None]
        if issues:
            excluded.append({'symbol': symbol, 'issues': issues})
            issue_counts.update(row['reason'] for row in issues)
        else:
            included.append(symbol)
    denominator = []
    for index in range(len(dates)):
        try:
            value = math.fsum(amounts_by_symbol[symbol][index] for symbol in included)
        except OverflowError:
            value = None
        denominator.append(value if finite(value) else None)
    complete = len(dates) == 30
    reasons = []
    if not complete:
        reasons.append('insufficientCalendarSlots')
    if not included:
        reasons.append('emptyCompleteCaseUniverse')
    bad_denominators = [dates[i] for i, value in enumerate(denominator) if not finite(value) or value <= 0]
    if bad_denominators:
        reasons.append('nonpositiveOrNonfiniteDenominator')
    return {'signalDate': signal_date, 'formationDate': dates[-11] if len(dates) >= 11 else None,
        'dates': dates, 'sourceUniverse': universe, 'members': included,
        'membersSha256': digest(included), 'originalCount': len(universe), 'includedCount': len(included),
        'excludedCount': len(excluded), 'excluded': excluded, 'invalidSlotCounts': dict(issue_counts),
        'denominatorAmounts': denominator, 'invalidDenominatorDates': bad_denominators,
        'completeCalendar': complete, 'evaluable': not reasons, 'reasons': reasons}


def reference_group_amount(group, day_universe, amounts_by_symbol):
    """Use the frozen F members throughout the 30 slots; never replace peers."""
    require(isinstance(group, dict), 'Missing frozen peer group')
    members, anchor = group.get('members'), group.get('anchor')
    require(type(anchor) is str and anchor, 'Missing peer anchor')
    require(isinstance(members, list) and all(type(s) is str and s for s in members)
            and len(members) == len(set(members)) and anchor not in members,
            'Frozen peer members must be unique and exclude the anchor')
    require(group.get('formationDate') == day_universe['formationDate'], 'Group not formed exactly at T minus ten')
    reasons = list(day_universe['reasons'])
    if group.get('kind') != 'formed' or len(members) != 20:
        reasons.append('formationGroupNotComplete')
    missing = [symbol for symbol in members if symbol not in day_universe['members']]
    if missing:
        reasons.append('frozenMemberOutsideCompleteCaseUniverse')
    numerator, shares = None, None
    if not reasons:
        try:
            numerator = [math.fsum(amounts_by_symbol[symbol][i] for symbol in members) for i in range(30)]
        except OverflowError:
            reasons.append('nonfiniteNumerator')
        if numerator is not None and not all(finite(value) for value in numerator):
            reasons.append('nonfiniteNumerator')
        if not reasons:
            shares = [amount / total for amount, total in zip(numerator, day_universe['denominatorAmounts'])]
            require(all(finite(value) and 0 <= value <= 1 + 1e-12 for value in shares), 'Invalid group share')
    baseline = math.fsum(shares[:20]) / 20 if shares is not None else None
    comparisons = [value > baseline for value in shares[20:]] if shares is not None else None
    count = sum(comparisons) if comparisons is not None else None
    active = shares[-1] > baseline if shares is not None else None
    return {'anchor': anchor, 'signalDate': day_universe['signalDate'], 'formationDate': group['formationDate'],
        'members': list(members), 'missingMembers': missing, 'numeratorAmounts': numerator,
        'shares': shares, 'baselineShare': baseline, 'currentShare': shares[-1] if shares is not None else None,
        'aboveBaseline': comparisons, 'aboveBaselineCount': count, 'evaluable': not reasons, 'reasons': reasons,
        'currentActivity': active, 'persistent8': active and count >= 8 if active is not None else None}


def verify_inputs(calendar, universe, day_inputs, slots):
    """Generic independent arithmetic API, mapped from final runner schema later.

    day_inputs maps T to {dates, amountsBySymbol, expectedUniverse?}; slots
    retain original order and contain {signalId, symbol, signalDate,
    originalRank, group, groupSha256?, expectedAmount?}. Optional expected
    records use this verifier's normalized schema, not production functions.
    """
    require_calendar(calendar)
    needed_days = sorted({slot['signalDate'] for slot in slots})
    require(sorted(day_inputs) == needed_days, 'Day input coverage differs from complete Top5 slots')
    day_answers = {}
    for day in needed_days:
        item = day_inputs[day]
        answer = reference_universe(calendar, day, universe, item['dates'], item['amountsBySymbol'])
        if 'expectedUniverse' in item:
            same(item['expectedUniverse'], answer, 'Independent U30 ' + day)
        day_answers[day] = answer
    ids, seen_ranks, group_answers = set(), {}, []
    for slot in slots:
        sid, day = slot['signalId'], slot['signalDate']
        require(type(sid) is str and sid and sid not in ids, 'Duplicate/invalid original opportunity')
        ids.add(sid)
        rank = slot['originalRank']
        require(type(rank) is int and 1 <= rank <= 5 and rank not in seen_ranks.setdefault(day, set()), 'Invalid/reused original Top5 rank')
        seen_ranks[day].add(rank)
        group = slot['group']
        require(slot['symbol'] == group['anchor'] and slot['symbol'] in universe, 'Anchor identity differs')
        if 'groupSha256' in slot:
            require(slot['groupSha256'] == digest(group), 'Frozen group hash differs')
        answer = reference_group_amount(group, day_answers[day], day_inputs[day]['amountsBySymbol'])
        if 'expectedAmount' in slot:
            same(slot['expectedAmount'], answer, 'Independent amount arithmetic ' + sid)
        group_answers.append({'signalId': sid, **answer})
    return {'days': day_answers, 'slots': group_answers,
        'counts': {'originalSlots': len(slots), 'uniqueSignalDates': len(needed_days),
            'evaluable': sum(row['evaluable'] for row in group_answers),
            'unknown': sum(not row['evaluable'] for row in group_answers),
            **{policy: sum(row[policy] is True for row in group_answers) for policy in POLICIES}}}


def normalized_amount_cell(value):
    """Independent serialization of missing/invalid scalar evidence."""
    if value is None:
        return {'state': 'missing', 'value': None, 'token': None, 'reason': 'missingExplicitAmount'}
    if type(value) not in (int, float):
        token = value if type(value) in (str, bool) else {'type': type(value).__name__}
        return {'state': 'invalid', 'value': None, 'token': token, 'reason': 'nonnumericExplicitAmount'}
    if not finite(value):
        return {'state': 'invalid', 'value': None, 'token': str(value), 'reason': 'nonfiniteExplicitAmount'}
    if value < 0:
        return {'state': 'invalid', 'value': None, 'token': value, 'reason': 'negativeExplicitAmount'}
    return {'state': 'valid', 'value': value, 'token': value, 'reason': None}


def expected_market_record(reference, amounts):
    """Map independent U(T) arithmetic into the frozen evidence schema."""
    dates, symbols = reference['dates'], reference['sourceUniverse']
    require(len(dates) == 30, 'Production market record needs complete calendar')
    normalized = {symbol: [normalized_amount_cell(v) for v in amounts[symbol]] for symbol in symbols}
    coverage = []
    for index, day in enumerate(dates):
        cells = [normalized[symbol][index] for symbol in symbols]
        coverage.append({'date': day, 'validPositive': sum(c['state'] == 'valid' and c['value'] > 0 for c in cells),
            'validZero': sum(c['state'] == 'valid' and c['value'] == 0 for c in cells),
            'missing': sum(c['state'] == 'missing' for c in cells), 'invalid': sum(c['state'] == 'invalid' for c in cells)})
    exclusions = []
    retained = set(reference['members'])
    for symbol in symbols:
        if symbol in retained:
            continue
        cells = normalized[symbol]
        exclusions.append({'symbol': symbol,
            'missingDates': [d for d, c in zip(dates, cells) if c['state'] == 'missing'],
            'invalidSlots': [{'date': d, 'reason': c['reason']} for d, c in zip(dates, cells) if c['state'] == 'invalid'],
            'validZeroDates': [d for d, c in zip(dates, cells) if c['state'] == 'valid' and c['value'] == 0]})
    denominators = reference['denominatorAmounts']
    result = {'version': 'research-peer-amount-share-v1', 'windowDates': dates, 'formationDate': dates[19],
        'signalDate': dates[-1], 'universeAsOfDate': dates[-1], 'fixedPopulationCount': len(symbols),
        'fixedPopulationSha256': digest(symbols), 'universe': reference['members'],
        'universeSha256': reference['membersSha256'], 'denominators': denominators,
        'exclusions': exclusions, 'coverageByDate': coverage,
        'counts': {'includedSymbols': len(retained), 'excludedSymbols': len(exclusions),
            'totalSlots': 30 * len(symbols), **{k: sum(r[k] for r in coverage) for k in ('validPositive', 'validZero', 'missing', 'invalid')},
            'zeroDenominatorDays': sum(v == 0 for v in denominators),
            'nonfiniteDenominatorDays': sum(v is None for v in denominators)},
        'amountsSha256': digest(normalized),
        'digestMeaning': 'Normalized amount values and missing/invalid classifications; raw file bytes are separately pinned by caller.',
        'denominatorMeaning': 'Same U(T) on every one of the thirty sessions; membership observed over F-19..T, not frozen at F.',
        'amountUnit': 'same explicit raw currency unit across stocks; caller binds yuan source',
        'zeroPolicy': 'Finite explicit zero retained; unknown is neither zero nor an inferred halt/IPO.'}
    result['digest'] = digest(result)
    return result


def expected_amount_record(group, reference, amounts, market):
    dates, members, anchor = reference['dates'], group['members'], group['anchor']
    require(len(members) <= 20 and len(members) == len(set(members)) and anchor not in members,
            'Invalid frozen twenty-peer identity')
    require(group['formationDate'] == dates[19] and anchor in amounts, 'Amount group formation/anchor identity')
    outside = [s for s in members if s not in reference['members']]
    reasons = []
    if len(members) != 20:
        reasons.append('incompleteFrozenPeerGroup')
    if outside:
        reasons.append('frozenPeerOutsideBalancedUniverse')
    membership_known = not reasons
    daily = []
    for index, day in enumerate(dates):
        denominator = reference['denominatorAmounts'][index]
        numerator = None
        if membership_known:
            try:
                value = math.fsum(amounts[s][index] for s in members)
                numerator = value if finite(value) else None
            except OverflowError:
                pass
        problem = None
        if not membership_known:
            problem = 'incompleteFrozenPeerObservation'
        elif denominator is None:
            problem = 'nonfiniteMarketAmount'
        elif denominator <= 0:
            problem = 'nonpositiveMarketAmount'
        elif numerator is None:
            problem = 'nonfinitePeerAmount'
        share = numerator / denominator if problem is None else None
        if share is not None and not finite(share):
            problem, share = 'nonfinitePeerShare', None
        daily.append({'date': day, 'peerAmount': numerator, 'marketAmount': denominator, 'share': share,
                      'status': 'known' if share is not None else 'unknown', 'reason': problem})
    history = [r['share'] for r in daily[:20]]
    baseline = math.fsum(history) / 20 if all(v is not None for v in history) else None
    comparisons = [r['share'] > baseline if baseline is not None and r['share'] is not None else None for r in daily[20:]]
    count = sum(comparisons) if all(v is not None for v in comparisons) else None
    complete = membership_known and all(r['share'] is not None for r in daily)
    current = daily[-1]['share']
    control = current > baseline if complete else None
    candidate = control and count >= 8 if complete else None
    counts = dict(Counter(r['reason'] for r in daily if r['reason'] is not None))
    reasons.extend(r for r in counts if r not in reasons)
    return {'version': 'research-peer-amount-share-v1', 'anchor': anchor, 'members': list(members),
        'windowDates': dates, 'formationDate': dates[19], 'signalDate': dates[-1], 'marketDigest': market['digest'],
        'universeSha256': market['universeSha256'], 'universeCount': len(reference['members']),
        'frozenPeerCount': len(members), 'peersOutsideUniverse': outside, 'dailyRows': daily,
        'baseline': baseline, 'current': current, 'observationDates': dates[20:],
        'observationsAboveBaseline': comparisons, 'shareAboveBaselineCount': count, 'requiredAboveBaselineCount': 8,
        'kind': 'unknown' if not complete else 'persistent' if candidate else 'currentOnly' if control else 'notElevated',
        'control': control, 'candidate': candidate, 'complete': complete, 'unknownReasons': reasons,
        'dailyUnknownReasonCounts': counts,
        'rules': {'baseline': 'arithmetic mean of daily shares F-19..F (twenty sessions)',
            'control': 'S_T > baseline', 'candidate': 'control and count(S_d > baseline,F+1..T)>=8',
            'comparison': 'strict >; no epsilon; current day must pass independently',
            'population': 'Fixed twenty peers exclude anchor; anchor may contribute only to market denominator.'},
        'certified': False, 'targetAchieved': False}


def peer_reference():
    path = local(PEER_REFERENCE)
    require(sha(path) == PEER_REFERENCE_SHA, 'Independent peer verifier changed')
    spec = importlib.util.spec_from_file_location('_peer_amount_independent_peer_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_cached_bars(dataset):
    """Build read-only indexes from the saved account cache, never raw reload."""
    require_calendar(dataset['calendar'])
    securities = {s['symbol']: s for s in dataset['securities']}
    require(len(securities) == len(dataset['securities']), 'Duplicate cached security')
    require(not dataset.get('corporateActions') and all(not s.get('corporateActions') for s in securities.values()),
            'Unregistered corporate action instructions')
    benchmark = dataset['benchmark']
    all_sources = {**securities, '__benchmark__': benchmark}
    bar_maps = {}
    for symbol, security in all_sources.items():
        dates = [b['date'] for b in security['bars']]
        require(dates == sorted(set(dates)), 'Cached bar dates unordered/duplicate')
        bar_maps[symbol] = {b['date']: b for b in security['bars']}

    def raw(symbol, day):
        bar = bar_maps[symbol].get(day)
        if bar is None:
            return None
        status = all_sources[symbol].get('tradeStatusByDate', {}).get(day)
        suspended = status is False or type(status) is int and status == 0 or status == '0'
        return {**bar, 'priceBasisId': 'raw-unadjusted', 'knownSuspended': suspended}

    return securities, bar_maps, raw


def verify_peer_records(dataset, group_rows, opportunities, eligibility_by_date=None, *, reuse_original=None):
    """Verify membership freezing and independent new-period full-pool Pearson.

    Old-period groups may inherit their pinned full independent original audit;
    new-period groups use every cached security and formation eligibility row.
    Every priceExpansion record is independently recomputed in both periods.
    """
    reference = peer_reference()
    securities, _bars, raw = prepare_cached_bars(dataset)
    calendar = dataset['calendar']
    indices = {d: i for i, d in enumerate(calendar)}
    by_key = {(row['date'], row['symbol']): row for row in group_rows}
    require(len(by_key) == len(group_rows) == len(opportunities), 'Peer group/opportunity coverage')
    require([(r['date'], r['symbol']) for r in group_rows] == [(r['signalDate'], r['symbol']) for r in opportunities],
            'Peer group order differs from frozen Top5 slots')
    if reuse_original is not None:
        same(group_rows, reuse_original, 'Original audited group records reused exactly')
    cached_day = None
    raw_formation = benchmark_formation = None
    for slot in opportunities:
        day, symbol = slot['signalDate'], slot['symbol']
        group_row, index = by_key[day, symbol], indices[day]
        require(index >= 70, 'Insufficient cached calendar for complete formation/observation windows')
        formation, days = calendar[index - 10], calendar[index - 70:index - 9]
        group = group_row['group']
        require(group_row['formationDate'] == slot['formationDate'] == group['formationDate'] == formation,
                'Formation not exactly T minus ten source sessions')
        require(group_row['rank'] == slot['originalRank'] and group['anchor'] == symbol
                and group_row['groupSha256'] == slot['groupSha256'] == digest(group), 'Frozen group identity/hash/rank')
        if reuse_original is None:
            require(eligibility_by_date is not None and formation in eligibility_by_date, 'Missing full formation eligibility')
            require(set(eligibility_by_date[formation]) == set(securities), 'Formation eligibility lost original population')
            if cached_day != day:
                raw_formation = {s: [raw(s, d) for d in days] for s in sorted(securities)}
                benchmark_formation = [raw('__benchmark__', d) for d in days]
                cached_day = day
            expected_group = reference.reference_group(symbol, formation, days, raw_formation,
                                                       benchmark_formation, eligibility_by_date[formation])
            same(group, expected_group, 'Independent full-universe formation Pearson ' + day + '/' + symbol)
        observed_days = calendar[index - 10:index + 1]
        expected_expansion = reference.reference_expansion(group, observed_days,
            {s: [raw(s, d) for d in observed_days] for s in group['members']},
            [raw('__benchmark__', d) for d in observed_days])
        same(slot['priceExpansion'], expected_expansion, 'Independent original price expansion')
    return {'groups': len(group_rows), 'reusedAuditedGroups': len(group_rows) if reuse_original is not None else 0,
        'independentlyRecomputedGroups': len(group_rows) if reuse_original is None else 0,
        'priceExpansionComparisons': len(opportunities)}


def cost_reference():
    path = local(COST_REFERENCE)
    require(sha(path) == COST_REFERENCE_SHA, 'Independent accounting reference changed')
    spec = importlib.util.spec_from_file_location('_peer_amount_cost_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_saved_account(result, summary, schedule, config, source, calendar, signal_closes, period, strategy_id):
    """Wrap the unchanged 500k / 20% / 10bps / fixed5 account audit."""
    require(period in PERIODS, 'Unknown registered period')
    require(type(strategy_id) is str and strategy_id and all(row['strategyId'] == strategy_id for row in schedule),
            'Account strategy identity changed')
    require(config['initialCapital'] == 500000 and config['maxPositions'] == 5 and config['maxStockWeight'] == .2
            and config['stopLossPct'] == .05 and config['trailingStopPct'] == .1
            and config['takeProfitPct'] == 0 and config['maxHoldingDays'] == 0 and config['slippageBps'] == 10,
            'Unregistered account configuration')
    module = cost_reference()
    references = module.reference_modules(period)
    # reference_modules constructs a fresh private instance each call. The
    # strategy binding changes no persisted/shared dependency or risk function.
    references[2].SID = strategy_id
    return module.verify_account(result, summary, schedule, config, source, calendar, period, 10,
                                 signal_closes, references)


def verify_parent_selection(selection, candidates, calendar, config, *, formation=None, original_selection=None):
    target = [d for d in calendar if config['startDate'] <= d <= config['endDate']]
    same(selection['targetDates'], target, 'All original target sessions')
    require(selection['allCurrentCandidatesUsedBeforeFormationFilter'] is True, 'Parent selection omitted current candidates')
    source_ids = [(r['signalDate'], r['symbol']) for r in candidates]
    require(len(source_ids) == len(set(source_ids)), 'Duplicate source parent candidate')
    if formation is None:
        same(selection['rankedCandidates'], candidates, 'Original audited temporal candidate ranking')
        same(selection['exclusions'], {'meaning': 'Full temporal selection inherited from pinned original independent audit.'},
             'Old parent source scope')
        top5 = [r for r in candidates if r['selectedTop5']]
        old_slots = [slot for day in original_selection for slot in day['slots']]
        same([(r['signalDate'], r['symbol'], r['rank'], r['formationDate']) for r in top5],
             [(r['date'], r['symbol'], r['rank'], r['formationDate']) for r in old_slots], 'Original Top5 slot ledger')
    else:
        indices, target_set = {d: i for i, d in enumerate(calendar)}, set(target)
        keys = {calendar[indices[r['signalDate']] - 10] + ':' + r['symbol'] for r in candidates
                if calendar[indices[r['signalDate']] - 10] in target_set}
        require(set(formation['parentsByKey']) == keys, 'Formation parent capture keys do not cover all current candidates')
        require(formation['dates'] == sorted({key[:10] for key in keys}), 'Formation date coverage')
        require(set(formation['eligibilityByDate']) == set(formation['dates']), 'Formation eligibility date coverage')
        require(formation['completeRowsScanned'] == len(target) * 3141, 'Incomplete original predicate source scan')
        pools, exclusions = {}, Counter()
        for candidate in candidates:
            day, symbol = candidate['signalDate'], candidate['symbol']
            require(day in target_set and indices[day] >= 10, 'Parent outside account period/calendar')
            f = calendar[indices[day] - 10]
            if f not in target_set:
                exclusions['formationUnavailable'] += 1
                continue
            parent = formation['parentsByKey'][f + ':' + symbol]
            require(parent['date'] == f and parent['symbol'] == symbol, 'Formation parent identity mismatch')
            same(parent['features']['baseEligibility'], formation['eligibilityByDate'][f][symbol],
                 'Parent and peer-universe formation eligibility from same captured row')
            flags = {}
            for name, operator, threshold in GATES:
                predicate = parent['predicates'][name]
                require(predicate['operator'] == operator and predicate['threshold'] == threshold, 'Parent strength threshold changed')
                value = predicate['value']
                flag = (value >= threshold if operator == '>=' else value <= threshold) if predicate['reason'] is None and finite(value) else None
                same(predicate['passed'], flag, 'Formation predicate arithmetic ' + name)
                flags[name] = flag
            if parent['kind'] not in ('signaled', 'notTriggered') or not all(v is True for v in flags.values()):
                exclusions['formationRejected'] += 1
                continue
            row = deepcopy(candidate)
            row.update(formationDate=f, eligible=True, formationParent={'date': f, 'kind': parent['kind'],
                'reason': parent['reason'], 'fiveThresholdsPass': flags, 'strong': True, 'outcomeSha256': digest(parent)})
            pools.setdefault(day, []).append(row)
        ranked = []
        for day in sorted(pools):
            rows = sorted(pools[day], key=lambda r: (-r['meanStrengthPercentile20'], -r['relativeReturn60Percentile'], r['symbol']))
            for rank, row in enumerate(rows, 1):
                row.update(rank=rank, selectedTop5=rank <= 5, score=row['meanStrengthPercentile20'], rawScore=row['meanStrengthPercentile20'])
                ranked.append(row)
        same(selection['rankedCandidates'], ranked, 'All-current candidates -> F strength filter -> full rerank')
        same(selection['exclusions'], dict(exclusions), 'All rejected parent candidates retained in counts')
        top5 = [r for r in ranked if r['selectedTop5']]
    require(selection['top5Count'] == len(top5), 'Top5 count differs')
    return top5


def verify_amount_source(source, dataset, identity, top5):
    same(source['sourceIdentity'], identity, 'Amount source identity')
    calendar, dates, symbols = source['sourceCalendar'], source['dates'], source['symbols']
    require_calendar(calendar)
    require_calendar(dates)
    require_calendar(dataset['calendar'])
    same(dataset['calendar'], [d for d in calendar if dataset['calendar'][0] <= d <= dataset['calendar'][-1]],
         'Account calendar is exact contiguous raw-calendar range')
    same(source['missingPolicy'], 'Missing bar or missing amount = null; no zero imputation.', 'Missing amount policy')
    securities = {s['symbol']: s for s in dataset['securities']}
    same(symbols, sorted(securities), 'Complete fixed source universe')
    require(len(symbols) == 3141 and set(source['amountsBySymbol']) == set(symbols), 'Full 3141 original amount symbols')
    indices = {d: i for i, d in enumerate(calendar)}
    expected = sorted({d for r in top5 for d in calendar[indices[r['signalDate']] - 29:indices[r['signalDate']] + 1]})
    same(dates, expected, 'Amount dates exact union of used 30-slot windows')
    used = set(dates)
    require(used <= set(dataset['calendar']), 'Account cache does not cover amount inputs')
    for symbol in symbols:
        mapping = {b['date']: b.get('amount') for b in securities[symbol]['bars'] if b['date'] in used}
        same(source['amountsBySymbol'][symbol], [mapping.get(d) for d in dates], 'All cached-source amount slots ' + symbol)
    return {'symbols': len(symbols), 'dates': len(dates), 'amountSlotsCompared': len(symbols) * len(dates)}


def verify_group_source_binding(binding, dataset, identity, top5, raw_calendar):
    same(binding['sourceIdentity'], identity, 'Group source original identity')
    require(binding['tradeStatusCompared'] is True, 'Raw/cache suspension capture not compared')
    fields = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount']
    same(binding['fields'], fields, 'Source comparable OHLCV/amount fields')
    indices = {d: i for i, d in enumerate(raw_calendar)}
    expected_dates = sorted({d for r in top5 for d in raw_calendar[indices[r['signalDate']] - 70:indices[r['signalDate']] + 1]})
    same(binding['dates'], expected_dates, 'Full union of exact F-60 through T windows')
    sources = {s['symbol']: s for s in dataset['securities']}
    sources['__benchmark__'] = dataset['benchmark']
    require([r['symbol'] for r in binding['fieldComparisons']] == sorted(sources), 'Group field source coverage/order')
    for observed in binding['fieldComparisons']:
        symbol = observed['symbol']
        mapping = {b['date']: b for b in sources[symbol]['bars']}
        values = [None if d not in mapping else [mapping[d].get(k) for k in fields] for d in expected_dates]
        same(observed, {'symbol': symbol, 'rawAndAccountEqual': True, 'sha256': digest(values)}, 'Cached group window capture hash')
    # The original raw/cache equality assertion belongs to the pinned capture
    # runner. This auditor proves the actual saved cache matches that receipt.
    return {'securitiesAndBenchmark': len(sources), 'priceDates': len(expected_dates),
            'cachedFieldHashesChecked': len(sources), 'rawReloaded': False}


def verify_checkpoint(checkpoint, result):
    require(checkpoint['digest'] == digest({k: v for k, v in checkpoint.items() if k != 'digest'}), 'Checkpoint digest differs')
    for key in ('trades', 'closedTrades', 'curve', 'daily', 'decisions', 'forcedExits', 'pendingTarget',
                'actionLedger', 'cashReceivables', 'actionPayables', 'preExRights', 'reservedActionCash'):
        same(checkpoint['engineState'][key], result[key], 'Checkpoint/result ' + key)
    same(checkpoint['researchState']['opportunities'], result['research']['opportunities'], 'Checkpoint opportunities')
    same(checkpoint['feeState'], result['research']['feeLedger'], 'Checkpoint fee ledger')
    same(checkpoint['structuralExits'], result['research']['structuralExits'], 'Checkpoint exit observation ledger')
    same(checkpoint['engineState']['cash'], result['curve'][-1]['cash'], 'Checkpoint final cash')


def verify_comparison_row(row, summary, mode, period, count):
    c, a, p = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    labels = {'currentActivity': '当日同伴成交额份额高于基准', 'persistent8': '当日活跃且最近10日中至少8日保持'}
    expected = {'id': mode, 'label': labels[mode], 'signals': count, 'closed': c['count'], 'winRatePct': c['winRatePct'],
        'meanWinPct': c['meanWinReturnPct'], 'meanLossPct': c['meanLossReturnPct'], 'closedMeanPct': c['meanReturnPct'],
        'payoffRatio': c['payoffRatio'], 'profitFactor': c['profitFactor'], 'holdingSessions': c['holdingSessions'],
        'maxConsecutiveLosses': c['maxConsecutiveLosses'], 'accountReturnPct': a['netReturnPct'],
        'maxDrawdownPct': a['maxDrawdownPct'], 'meanExposurePct': p['exposurePct']['mean'],
        'openPositions': summary['openPositions']['count'], 'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': r['id'], 'closed': r['closedEpisodes']['count'], 'closedMeanPct': r['closedEpisodes']['meanReturnPct'],
                   'accountReturnPct': r['returnPct'], 'maxDrawdownPct': r['maxDrawdownPct']}
                  for r in summary['periods'] if r['kind'] == 'calendarYear'],
        'phases': [{'id': r['id'], 'start': r['observedStart'], 'end': r['observedEnd'],
                    'closed': r['closedEpisodes']['count'], 'accountReturnPct': r['returnPct'],
                    'maxDrawdownPct': r['maxDrawdownPct'], 'meanExposurePct': r['exposurePct']['mean']}
                   for r in summary['periods'] if r['kind'] == 'retrospectiveNarrativePhase'], 'period': period}
    same(row, expected, 'All reported comparison statistics')


def verify(directory):
    started = time.perf_counter()
    require(not (directory / 'validation.json').exists(), 'Refusing to overwrite validation.json')
    plan_path, comparison_path = directory / 'plan.json', directory / 'comparison.json'
    plan, comparison = read(plan_path), read(comparison_path)
    plan_sha = sha(plan_path)
    require(plan_sha == REGISTERED_PLAN_SHA and plan['sourcePins']['scripts/run-peer-amount-persistence.py'] == RUNNER_SHA,
            'Plan/runner is not the registered pre-run version')
    require(plan['schema'] == 'peer-amount-persistence-v1' and comparison['schema'] == 'peer-amount-persistence-comparison-v1', 'Study schema')
    require(plan['modes'] == list(POLICIES) and list(plan['periods']) == list(PERIODS) and plan['strategyId'] == SID, 'Frozen mode/period/strategy identities')
    require(plan['newCandidateVariants'] == comparison['candidateVariants'] == 1 and plan['newAccounts'] == comparison['newAccounts'] == 4
            and plan['candidateAccounts'] == 2 and plan['parameterSearch'] is False and plan['profitHurdles'] is None
            and plan['holdoutClaim'] is False, 'Study run counts/selection scope')
    require(comparison['planSha256'] == plan_sha and plan['certified'] is comparison['certified'] is False
            and plan['targetAchieved'] is comparison['targetAchieved'] is False, 'Plan binding or certification differs')
    same(comparison['limitations'], plan['limitations'], 'Inherited comparison limitations')
    pins = {}
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for path, expected in mapping.items():
            require(sha(local(path)) == expected, 'Frozen code/input changed: ' + path)
            pins[path] = expected
    for name in ('signal-leader-peer-expansion-v1', 'signal-supplemental-persistent-leader-v1'):
        proof = read(local('artifacts/strategy-search/' + name + '/independent-result-verification.json'))
        require(proof['passed'] is True and proof['failureCount'] == 0, 'Inherited independent source audit did not pass')
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == plan_sha and completion['allSchedulesFrozenBeforeAccounts'] is True, 'Both-period inputs not frozen before accounts')
    require(set(completion['outputs']) == set(PERIODS), 'Period input receipt coverage')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve(), 'Run-start input receipt binding')
    require([(r['period'], r['id']) for r in comparison['rows']] == [(p, m) for p in PERIODS for m in POLICIES], 'Complete four-account comparison matrix')
    comparison_rows = {(r['period'], r['id']): r for r in comparison['rows']}
    output = {'schema': 'peer-amount-persistence-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': plan_sha, 'comparisonSha256': sha(comparison_path), 'frozenCodeAndInputs': pins,
        'independentReferences': {COST_REFERENCE: COST_REFERENCE_SHA, PEER_REFERENCE: PEER_REFERENCE_SHA,
                                  **cost_reference().REFERENCES},
        'inputArtifacts': {}, 'accountArtifacts': comparison['artifacts'], 'periods': {}, 'certified': False, 'targetAchieved': False}
    for period in PERIODS:
        folder, spec = directory / period, plan['periods'][period]
        require(pinned(completion['outputs'][period]) == (folder / 'input-completion.json').resolve(), 'Period completion path')
        receipt = read(folder / 'input-completion.json')
        require(receipt['planSha256'] == plan_sha and receipt['period'] == period, 'Period receipt plan/identity')
        expected_files = {'parent-selection.json.gz', 'amount-source.json.gz', 'group-source-binding.json', 'source.json',
            'account-data.json.gz', 'opportunities.jsonl.gz', 'peer-groups.jsonl.gz', 'amount-windows.jsonl.gz',
            'currentActivity.signals.json', 'persistent8.signals.json', 'input-counts.json'}
        if period == '2024-2025':
            expected_files.add('formation-panel.json.gz')
        require(set(receipt['files']) == expected_files, 'Incomplete/extra period input closure')
        for name, reference in receipt['files'].items():
            require(pinned(reference) == (folder / name).resolve(), 'Period input path binding ' + name)
        output['inputArtifacts'][period] = receipt['files']
        source, dataset = read(folder / 'source.json'), read(folder / 'account-data.json.gz')
        same(source, spec['sourceIdentity'], 'Original account source identity')
        require(digest(dataset) == source['accountDatasetDigest'], 'Complete cached account dataset digest')
        amount_source, selection = read(folder / 'amount-source.json.gz'), read(folder / 'parent-selection.json.gz')
        calendar = amount_source['sourceCalendar']
        formation = None
        if period == '2022-2023':
            candidates = list(records(local(OLD + '/candidates.jsonl.gz')))
            top5 = verify_parent_selection(selection, candidates, calendar, spec['config'], original_selection=list(records(local(OLD + '/selection.jsonl.gz'))))
            original_groups = list(records(local(OLD + '/peer-groups.jsonl.gz')))
        else:
            candidates = list(records(local(NEW + '/candidates.jsonl.gz')))
            formation = read(folder / 'formation-panel.json.gz')
            require(pinned(formation['candidateSourceBinding']) == local(NEW + '/candidates.jsonl.gz')
                    and pinned(formation['predicateSourceBinding']) == local(NEW + '/predicates.jsonl.gz'), 'Formation original capture source bindings')
            top5 = verify_parent_selection(selection, candidates, calendar, spec['config'], formation=formation)
            original_groups = None
        slots, groups, markets = list(records(folder / 'opportunities.jsonl.gz')), list(records(folder / 'peer-groups.jsonl.gz')), list(records(folder / 'amount-windows.jsonl.gz'))
        require(len(slots) == len(top5), 'Original Top5 slots omitted')
        for slot, parent in zip(slots, top5):
            expected_signal = {'signalId': f"{SID}:{parent['signalDate']}:{parent['symbol']}", 'strategyId': SID,
                'symbol': parent['symbol'], 'signalDate': parent['signalDate'], 'originalRank': parent['rank']}
            same({k: slot[k] for k in expected_signal}, expected_signal, 'Every original temporal Top5 signal identity/order/rank')
            same(slot['parent'], parent, 'Complete original parent features retained')
        source_checks = verify_amount_source(amount_source, dataset, source, top5)
        binding_checks = verify_group_source_binding(read(folder / 'group-source-binding.json'), dataset, source, top5, calendar)
        peer_checks = verify_peer_records(dataset, groups, slots, formation['eligibilityByDate'] if formation else None, reuse_original=original_groups)
        day_index = {d: i for i, d in enumerate(amount_source['dates'])}
        calendar_index = {d: i for i, d in enumerate(calendar)}
        signal_days = sorted({r['signalDate'] for r in slots})
        require([r['date'] for r in markets] == signal_days, 'Market denominator exactly once per signal day')
        groups_by_key = {(r['date'], r['symbol']): r['group'] for r in groups}
        slots_by_day = {day: [] for day in signal_days}
        for slot in slots:
            slots_by_day[slot['signalDate']].append(slot)
        schedules, kinds, coverage = {m: [] for m in POLICIES}, Counter(), []
        for market_row in markets:
            day = market_row['date']
            index = calendar_index[day]
            dates = calendar[index - 29:index + 1]
            amounts = {s: [amount_source['amountsBySymbol'][s][day_index[d]] for d in dates] for s in amount_source['symbols']}
            universe = reference_universe(calendar, day, amount_source['symbols'], dates, amounts)
            market = expected_market_record(universe, amounts)
            same(market_row['market'], market, 'Independent U30 / 30 denominators / coverage / hashes ' + day)
            coverage.append({'date': day, **market['counts']})
            for slot in slots_by_day[day]:
                expected_amount = expected_amount_record(groups_by_key[day, slot['symbol']], universe, amounts, market)
                same(slot['amount'], expected_amount, 'Every opportunity amount computation ' + slot['signalId'])
                kinds[expected_amount['kind']] += 1
                for mode, flag in (('currentActivity', 'control'), ('persistent8', 'candidate')):
                    if expected_amount[flag] is True:
                        schedules[mode].append({k: slot[k] for k in ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')})
        counts = {'originalTop5': len(slots), 'days': len(signal_days), 'outcomes': dict(kinds),
                  'schedules': {mode: len(rows) for mode, rows in schedules.items()}}
        same(read(folder / 'input-counts.json'), counts, 'Input classification/schedule counts')
        same(receipt['counts'], counts, 'Input completion counts')
        require({r['signalId'] for r in schedules['persistent8']} <= {r['signalId'] for r in schedules['currentActivity']}, 'Candidate is not nested in control')
        expected_account_files = {mode + suffix for mode in POLICIES for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')}
        require(set(comparison['artifacts'][period]) == expected_account_files, 'Six period account artifacts not fully bound')
        for name, reference in comparison['artifacts'][period].items():
            require(pinned(reference) == (folder / name).resolve(), 'Account artifact path changed')
        accounts = {}
        securities, bars, _raw = prepare_cached_bars(dataset)
        for mode in POLICIES:
            schedule = read(folder / (mode + '.signals.json'))
            same(schedule, schedules[mode], 'Complete policy schedule; no filtering by filled trades')
            result, checkpoint, summary = (read(folder / (mode + suffix)) for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
            closes = {(r['symbol'], r['signalDate']): bars[r['symbol']][r['signalDate']]['close'] for r in schedule}
            accounts[mode] = verify_saved_account(result, summary, schedule, spec['config'], source, dataset['calendar'], closes, period, SID)
            verify_checkpoint(checkpoint, result)
            for fill in result['trades']:
                same(fill['openingPrice'], bars[fill['symbol']][fill['date']]['open'], 'Source-bound actual opening price')
            require(summary['policyId'] == mode, 'Summary policy identity')
            verify_comparison_row(comparison_rows[period, mode], summary, mode, period, len(schedule))
            print(json.dumps({'period': period, 'mode': mode, 'passed': True, 'signals': len(schedule), 'fills': accounts[mode]['fills']}), flush=True)
        output['periods'][period] = {'counts': counts, 'amountSource': source_checks, 'groupSource': binding_checks,
            'peerFormation': peer_checks, 'dailyCoverage': coverage, 'accounts': accounts}
    output['checks'] = ['Frozen code/input/account-output hashes and complete cached source dataset digests',
        'All temporal parent candidates filtered at exact F before original full rerank; complete Top5 opportunity ledgers',
        'Original independently audited old groups retained and new full-population Pearson groups independently recomputed',
        'Both periods original price-expansion diagnostics independently recomputed without affecting admission',
        'All original-symbol amount slots, common U30, 30 denominators, fixed peers and all continuous share inputs',
        'Strict current endpoint plus 8-of-10 conditions; unknown/rejected opportunities retained and exact nested schedules',
        'Four full accounts: fees, slippage, cash/shares, T+1, frozen budgets, fills, exits, episodes, curve, terminal positions and summaries']
    output['scope'] = {'newAccountsExecutedByRunner': 4, 'candidateVariants': 1, 'accountsExecutedByVerifier': 0,
        'newAmountModuleImported': False, 'originalRawMarketReloaded': False, 'conditionalRankDiagnosticAdded': False,
        'limitations': ['Original raw/cache field equality and formation predicate extraction inherit pinned capture and previously audited source evidence; saved caches and their arithmetic are independently checked.',
            'Account arithmetic uses pinned independent verifiers; no separate resimulation of every eligibility/capacity/order gate.',
            'Previously inspected survivor-sample periods remain development data; no whole-market, investor-control, isolated-factor or stable-return claim.']}
    output['elapsedSeconds'] = time.perf_counter() - started
    path = directory / 'validation.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'path': str(path), 'sha256': sha(path), 'elapsedSeconds': output['elapsedSeconds']}), flush=True)


def verify_inputs_only(directory, period):
    """Read-only staging audit while the other period/accounts are preparing."""
    plan = read(directory / 'plan.json')
    require(sha(directory / 'plan.json') == REGISTERED_PLAN_SHA, 'Unregistered staging plan')
    folder, spec = directory / period, plan['periods'][period]
    receipt = read(folder / 'input-completion.json')
    require(receipt['planSha256'] == REGISTERED_PLAN_SHA and receipt['period'] == period, 'Staging input receipt identity')
    for name, reference in receipt['files'].items():
        require(pinned(reference) == (folder / name).resolve(), 'Staging input reference path')
    dataset, source, selection = read(folder / 'account-data.json.gz'), read(folder / 'source.json'), read(folder / 'parent-selection.json.gz')
    same(source, spec['sourceIdentity'], 'Staging source identity')
    require(digest(dataset) == source['accountDatasetDigest'], 'Staging complete dataset digest')
    amount_source = read(folder / 'amount-source.json.gz')
    calendar = amount_source['sourceCalendar']
    formation = None
    if period == '2022-2023':
        candidates = list(records(local(OLD + '/candidates.jsonl.gz')))
        top5 = verify_parent_selection(selection, candidates, calendar, spec['config'], original_selection=list(records(local(OLD + '/selection.jsonl.gz'))))
        original_groups = list(records(local(OLD + '/peer-groups.jsonl.gz')))
    else:
        candidates = list(records(local(NEW + '/candidates.jsonl.gz')))
        formation = read(folder / 'formation-panel.json.gz')
        top5 = verify_parent_selection(selection, candidates, calendar, spec['config'], formation=formation)
        original_groups = None
    slots, groups, markets = list(records(folder / 'opportunities.jsonl.gz')), list(records(folder / 'peer-groups.jsonl.gz')), list(records(folder / 'amount-windows.jsonl.gz'))
    require(len(slots) == len(top5), 'Staging Top5 slot count')
    for slot, parent in zip(slots, top5):
        same(slot['parent'], parent, 'Staging original parent order')
        same({k: slot[k] for k in ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')},
             {'signalId': f"{SID}:{parent['signalDate']}:{parent['symbol']}", 'strategyId': SID,
              'symbol': parent['symbol'], 'signalDate': parent['signalDate'], 'originalRank': parent['rank']}, 'Staging original Top5 identity')
    source_checks = verify_amount_source(amount_source, dataset, source, top5)
    binding_checks = verify_group_source_binding(read(folder / 'group-source-binding.json'), dataset, source, top5, calendar)
    peers = verify_peer_records(dataset, groups, slots, formation['eligibilityByDate'] if formation else None, reuse_original=original_groups)
    day_index, calendar_index = {d: i for i, d in enumerate(amount_source['dates'])}, {d: i for i, d in enumerate(calendar)}
    require([r['date'] for r in markets] == sorted({r['signalDate'] for r in slots}), 'Staging unique market dates')
    groups = {(r['date'], r['symbol']): r['group'] for r in groups}
    kinds, schedules = Counter(), {m: [] for m in POLICIES}
    for observed in markets:
        day = observed['date']
        index = calendar_index[day]
        dates = calendar[index - 29:index + 1]
        values = {s: [amount_source['amountsBySymbol'][s][day_index[d]] for d in dates] for s in amount_source['symbols']}
        u = reference_universe(calendar, day, amount_source['symbols'], dates, values)
        market = expected_market_record(u, values)
        same(observed['market'], market, 'Staging independent market arithmetic ' + day)
        for slot in (r for r in slots if r['signalDate'] == day):
            amount = expected_amount_record(groups[day, slot['symbol']], u, values, market)
            same(slot['amount'], amount, 'Staging independent amount arithmetic')
            kinds[amount['kind']] += 1
            for mode, key in (('currentActivity', 'control'), ('persistent8', 'candidate')):
                if amount[key] is True:
                    schedules[mode].append({k: slot[k] for k in ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')})
    for mode, schedule in schedules.items():
        same(read(folder / (mode + '.signals.json')), schedule, 'Staging complete schedule')
    counts = {'originalTop5': len(slots), 'days': len(markets), 'outcomes': dict(kinds),
              'schedules': {m: len(s) for m, s in schedules.items()}}
    same(receipt['counts'], counts, 'Staging receipt counts')
    same(read(folder / 'input-counts.json'), counts, 'Staging input counts')
    return {'passed': True, 'scope': 'inputsOnly; no account checks or account execution', 'period': period,
            'counts': counts, 'sourceChecks': source_checks, 'groupSourceChecks': binding_checks, 'peerChecks': peers}


def self_test():
    from datetime import timedelta
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(31)]
    dates, day = calendar[:30], calendar[29]
    peers = [f'p{i:02d}' for i in range(20)]
    universe = ['anchor', *peers]
    amounts = {'anchor': [80.] * 30, **{s: [1.] * 20 + [2., 2., 2., 2., 2., 2., 2., 1., 1., 2.] for s in peers}}
    group = {'anchor': 'anchor', 'formationDate': dates[19], 'kind': 'formed', 'members': peers}
    base = reference_universe(calendar, day, universe, dates, amounts)
    answer = reference_group_amount(group, base, amounts)
    same(answer['baselineShare'], .2, 'Manual baseline')
    same(answer['currentShare'], 1 / 3, 'Manual current share')
    require(answer['aboveBaselineCount'] == 8 and answer['currentActivity'] and answer['persistent8'], 'Eight strict-above days')
    require(answer['aboveBaseline'][7:9] == [False, False], 'Equality does not pass')
    changed = deepcopy(amounts)
    for symbol in peers:
        changed[symbol][26] = 1.
    less = reference_group_amount(group, reference_universe(calendar, day, universe, dates, changed), changed)
    require(less['aboveBaselineCount'] == 7 and less['currentActivity'] and not less['persistent8'], 'Seven days remains control-only')
    changed = deepcopy(amounts)
    for symbol in peers:
        changed[symbol][-1] = 1.
    inactive = reference_group_amount(group, reference_universe(calendar, day, universe, dates, changed), changed)
    require(not inactive['currentActivity'] and not inactive['persistent8'], 'Current activity required independently')
    changed = deepcopy(amounts)
    changed['p00'][4] = None
    missing = reference_group_amount(group, reference_universe(calendar, day, universe, dates, changed), changed)
    require(not missing['evaluable'] and missing['persistent8'] is None and missing['shares'] is None
            and missing['missingMembers'] == ['p00'], 'Missing frozen peer not dropped or made zero')
    changed = deepcopy(amounts)
    changed['anchor'][4] = None
    narrowed = reference_universe(calendar, day, universe, dates, changed)
    require(narrowed['members'] == peers and narrowed['denominatorAmounts'][0] == 20.
            and narrowed['denominatorAmounts'][20] == 40., 'One missing slot excludes symbol on all 30 dates')
    zeros = {symbol: [0.] * 30 for symbol in universe}
    zero_universe = reference_universe(calendar, day, universe, dates, zeros)
    require(zero_universe['includedCount'] == len(universe) and not zero_universe['evaluable'], 'Zeros valid amounts but zero denominator unknown')
    for value, reason in ((False, 'nonfiniteOrInvalidAmount'), (-1., 'negativeAmount'), (float('inf'), 'nonfiniteOrInvalidAmount'),
                           (None, 'missingAmount'), (0., None)):
        require(amount_issue(value) == reason, 'Distinct invalid/zero amount behavior')
    for bad_dates in (calendar[1:], calendar[:29]):
        try:
            reference_universe(calendar, day, universe, bad_dates, amounts)
        except ValueError:
            pass
        else:
            raise ValueError('Shifted/compressed amount window accepted')
    short = reference_universe(calendar, calendar[10], universe, calendar[:11], {s: v[:11] for s, v in amounts.items()})
    require(not short['evaluable'] and 'insufficientCalendarSlots' in short['reasons'], 'Insufficient calendar preserved')
    slots = [{'signalId': 's1', 'symbol': 'anchor', 'signalDate': day, 'originalRank': 1,
              'group': group, 'groupSha256': digest(group), 'expectedAmount': answer}]
    result = verify_inputs(calendar, universe, {day: {'dates': dates, 'amountsBySymbol': amounts, 'expectedUniverse': base}}, slots)
    same(result['counts'], {'originalSlots': 1, 'uniqueSignalDates': 1, 'evaluable': 1, 'unknown': 0,
                            'currentActivity': 1, 'persistent8': 1}, 'Complete slot counts')
    market = expected_market_record(base, amounts)
    require(market['digest'] == digest({k: v for k, v in market.items() if k != 'digest'}), 'Market evidence digest')
    record = expected_amount_record(group, base, amounts, market)
    same(record['baseline'], .2, 'Production schema independent baseline')
    require(record['kind'] == 'persistent' and record['shareAboveBaselineCount'] == 8, 'Production schema exact admission')
    partial = deepcopy(amounts)
    for symbol in universe:
        partial[symbol][0] = 0.
    partial_u = reference_universe(calendar, day, universe, dates, partial)
    partial_market = expected_market_record(partial_u, partial)
    partial_record = expected_amount_record(group, partial_u, partial, partial_market)
    require(partial_record['baseline'] is None and partial_record['control'] is None
            and partial_record['dailyRows'][0]['share'] is None and partial_record['current'] == 1 / 3,
            'One zero denominator preserves other known daily values without admitting policy')
    corrupt = deepcopy(slots)
    corrupt[0]['expectedAmount']['baselineShare'] = .3
    try:
        verify_inputs(calendar, universe, {day: {'dates': dates, 'amountsBySymbol': amounts}}, corrupt)
    except ValueError:
        pass
    else:
        raise ValueError('Corrupted summary accepted')
    self_test_parent_rerank()
    print(json.dumps({'passed': True, 'checks': ['manual shares/baseline', 'strict comparison and eight-day boundary',
        'seven days control-only', 'current endpoint required', 'fixed missing peer unknown/no replacement',
        'same complete-case denominator on all dates', 'zero versus missing/invalid amount',
        'zero denominator unknown', 'exact calendar and insufficient window', 'full slot/hash coverage',
        'corrupted arithmetic rejected', 'full production-schema hashes and admission',
        'partly unknown path retains known daily shares', 'F filter precedes rerank; original sixth can enter',
        'F strength allows notTriggered without F breakout']}))


def self_test_parent_rerank():
    from datetime import timedelta
    calendar = [(date(2020, 2, 1) + timedelta(days=i)).isoformat() for i in range(11)]
    f, t = calendar[0], calendar[-1]
    candidates, parents = [], {}
    for index in range(6):
        symbol = f's{index}'
        candidates.append({'symbol': symbol, 'signalDate': t, 'strategyId': 'rawPersistentPriceLeaderBreakoutDiagnostic',
            'meanStrengthPercentile20': .99 - .01 * index, 'relativeReturn60Percentile': .95,
            'rank': index + 1, 'selectedTop5': index < 5, 'score': .99 - .01 * index, 'rawScore': .99 - .01 * index})
        predicates = {name: {'value': limit if index else (limit - .1 if op == '>=' else limit + .1),
                            'operator': op, 'threshold': limit, 'passed': bool(index), 'reason': None}
                      for name, op, limit in GATES}
        parents[f + ':' + symbol] = {'date': f, 'symbol': symbol, 'kind': 'notTriggered',
            'reason': 'currentBreakoutMissing', 'predicates': predicates,
            'features': {'baseEligibility': {'eligible': True, 'reason': 'eligible', 'unknown': []}}}
    formation = {'parentsByKey': parents, 'dates': [f],
                 'eligibilityByDate': {f: {r['symbol']: r['features']['baseEligibility'] for r in parents.values()}},
                 'completeRowsScanned': 11 * 3141}
    expected = []
    for new_rank, original in enumerate(candidates[1:], 1):
        row = deepcopy(original)
        parent = parents[f + ':' + row['symbol']]
        row.update(formationDate=f, eligible=True, formationParent={'date': f, 'kind': 'notTriggered',
            'reason': 'currentBreakoutMissing', 'fiveThresholdsPass': {name: True for name, _op, _limit in GATES},
            'strong': True, 'outcomeSha256': digest(parent)}, rank=new_rank, selectedTop5=True)
        expected.append(row)
    selection = {'targetDates': calendar, 'allCurrentCandidatesUsedBeforeFormationFilter': True,
        'rankedCandidates': expected, 'exclusions': {'formationRejected': 1}, 'top5Count': 5}
    top = verify_parent_selection(selection, candidates, calendar, {'startDate': f, 'endDate': t}, formation=formation)
    require(top[-1]['symbol'] == 's5' and top[-1]['rank'] == 5, 'Original sixth candidate lost before F filtering')
    corrupted = deepcopy(selection)
    corrupted['rankedCandidates'] = expected[:-1]
    corrupted['top5Count'] = 4
    try:
        verify_parent_selection(corrupted, candidates, calendar, {'startDate': f, 'endDate': t}, formation=formation)
    except ValueError:
        pass
    else:
        raise ValueError('Premature Top5 cut accepted')


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
                result = verify_inputs_only(args.directory.resolve(), args.inputs_only)
                result.update(verifierSha256=sha(__file__), timestamp=stamp, planSha256=sha(args.directory / 'plan.json'))
                path = attempt_dir / (stamp + '-inputs-' + args.inputs_only + '.json')
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                print(json.dumps({**result, 'attempt': str(path)}), flush=True)
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
