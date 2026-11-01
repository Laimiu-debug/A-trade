"""Independent arithmetic/join audit of the fixed context diagnostic.

Never imports the diagnostic's calculation functions, reloads the raw market,
or executes an account. The only reused calculation is the pressure preparer's
independent saved-window arithmetic, separate from the production indicator.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/relative-repair-context-diagnostic-v1'
GROUPS = ('control10', 'control30', 'control50')
FEATURES = ('breadth20Pct', 'matchedBreadthChange5Pp', 'medianAmountRatio20',
            'qContinuationPct', 'benchmarkDrawdownFromAnchorPct', 'eventAgeSessions',
            'pressure20Prior', 'pressureAccelerationPrior')
STOCK = set(FEATURES[-2:])
COUNTS = {'2022-2023': 565, '2024-2025': 624}


def require(value, label):
    if not value:
        raise ValueError(label)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def equal(actual, expected, label):
    """Check full shape, nulls and nonnumeric types; tolerate arithmetic roundoff."""
    if finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8),
                f'{label}: {actual!r} != {expected!r}')
    elif isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'{label}: keys differ')
        for key in expected:
            equal(actual[key], expected[key], f'{label}.{key}')
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f'{label}: length differs')
        for i, (a, b) in enumerate(zip(actual, expected)):
            equal(a, b, f'{label}[{i}]')
    else:
        require(type(actual) is type(expected) and actual == expected, f'{label}: literal differs')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local(name):
    p = Path(name)
    require(not p.is_absolute() and '..' not in p.parts, 'Unsafe repository path')
    p = (ROOT / p).resolve()
    require(p.is_relative_to(ROOT.resolve()), 'Path outside repository')
    return p


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def avg(values):
    return math.fsum(values) / len(values) if values else None


def describe(values):
    require(all(v is None or finite(v) for v in values), 'Invalid distribution value')
    ordered = sorted(v for v in values if v is not None)

    def percentile(fraction):
        if not ordered:
            return None
        position = fraction * (len(ordered) - 1)
        before, after = math.floor(position), math.ceil(position)
        return ordered[before] * (after - position) + ordered[after] * (position - before) if before != after else ordered[before]

    return {'count': len(values), 'known': len(ordered), 'unknown': len(values) - len(ordered),
            'minimum': ordered[0] if ordered else None, 'q25': percentile(.25),
            'median': percentile(.5), 'q75': percentile(.75),
            'maximum': ordered[-1] if ordered else None, 'mean': avg(ordered)}


def average_ranks(values):
    # Counting less-than/equal values avoids the diagnostic's sorting/tie walk.
    return [1 + sum(other < item for other in values)
            + .5 * (sum(other == item for other in values) - 1) for item in values]


def correlation(points):
    paired = [p for p in points if finite(p['x']) and finite(p['y'])]
    answer = {'rows': len(points), 'paired': len(paired),
              'unknownExcluded': len(points) - len(paired), 'rho': None, 'reason': None}
    if len(paired) < 3:
        answer['reason'] = 'fewerThanThreePairs'
        return answer
    x = average_ranks([p['x'] for p in paired])
    y = average_ranks([p['y'] for p in paired])
    centre = (len(paired) + 1) / 2
    dx, dy = [v - centre for v in x], [v - centre for v in y]
    ssx, ssy = math.fsum(v * v for v in dx), math.fsum(v * v for v in dy)
    if not ssx or not ssy:
        answer['reason'] = 'constantRankVariable'
    else:
        answer['rho'] = math.fsum(a * b for a, b in zip(dx, dy)) / math.sqrt(ssx * ssy)
    return answer


def observations(rows, feature, group):
    closed = [r for r in rows if r['outcomes'][group]['state'] == 'closed']
    if feature in STOCK:
        return [{'eventId': r['eventId'], 'signalDate': r['signalDate'], 'signalId': r['signalId'],
                 'x': r['features'][feature], 'y': r['outcomes'][group]['netReturnPct'],
                 'closedTrades': 1} for r in closed]
    grouped = defaultdict(list)
    for row in closed:
        grouped[(row['eventId'], row['signalDate'])].append(row)
    result = []
    for (event, day), members in sorted(grouped.items()):
        x = members[0]['features'][feature]
        require(all(r['features'][feature] == x for r in members), 'Inconsistent same-day market feature')
        result.append({'eventId': event, 'signalDate': day, 'x': x,
                       'y': avg([r['outcomes'][group]['netReturnPct'] for r in members]),
                       'closedTrades': len(members)})
    return result


def event_summary(rows, event, group):
    buckets = defaultdict(list)
    for row in rows:
        buckets[row['outcomes'][group]['state']].append(row)
    closed, opened = buckets['closed'], buckets['open']
    entered = closed + opened
    returns = [r['outcomes'][group]['netReturnPct'] for r in closed]
    by_day = {r['signalDate']: r for r in rows}
    result = {'eventId': event['eventId'], 'triggerDate': event['triggerDate'], 'account': group,
              'opportunities': len(rows), 'uniqueSignalDates': len(by_day), 'actualEntries': len(entered),
              'closed': len(closed), 'open': len(opened),
              'closedNetPnlAmount': math.fsum(r['outcomes'][group]['pnlAmount'] for r in closed),
              'openUnrealizedPnlAmount': math.fsum(r['outcomes'][group]['unrealizedPnlAmount'] for r in opened),
              'returnDistribution': describe(returns), 'wins': sum(v > 0 for v in returns),
              'losses': sum(v < 0 for v in returns), 'meanWinnerPct': avg([v for v in returns if v > 0]),
              'meanLoserPct': avg([v for v in returns if v < 0]),
              'holdingSessions': describe([r['outcomes'][group]['holdingSessions'] for r in closed]),
              'contextDistributions': {}}
    for feature in FEATURES:
        result['contextDistributions'][feature] = {
            'allOpportunities': describe([r['features'][feature] for r in rows]),
            'uniqueSignalDates': None if feature in STOCK else describe([r['features'][feature] for r in by_day.values()]),
            'actualEntries': describe([r['features'][feature] for r in entered]),
            'closedEntries': describe([r['features'][feature] for r in closed]),
            'openEntries': describe([r['features'][feature] for r in opened])}
    return result


def event_membership(evidence, signals):
    events = {e['eventId']: e for e in evidence['events']}
    require(len(events) == len(evidence['events']), 'Duplicate event')
    daily = evidence['dailyEvidence']
    require([d['date'] for d in daily] == sorted({d['date'] for d in daily}), 'Bad evidence date order')
    reconstructed, membership = [], {}
    for d in daily:
        for pair in d['pairs']:
            event, day = d['eventId'], d['date']
            require(event in events and pair['eventId'] == event and pair['date'] == day, 'Bad pair event')
            require(events[event]['triggerDate'] < day <= events[event]['endDate'], 'Signal outside event')
            symbol = pair['controlSymbol']
            sid = f'matchedControl:{event}:{day}:{symbol}'
            require(sid not in membership, 'Duplicate signal reconstruction')
            membership[sid] = event
            reconstructed.append({'signalId': sid, 'strategyId': 'relativeRepairEventV1', 'symbol': symbol,
                                  'signalDate': day, 'originalRank': pair['originalRank']})
    equal(signals, reconstructed, 'Reconstructed original signals')
    equal(signals, evidence['schedules']['matchedControl'], 'Evidence schedule')
    return events, membership, {d['date']: d for d in daily}


def saved_pressure(prior, binding, signals, plan, plan_sha, output_path):
    equal(binding['inputPins'], plan['inputPins'], 'Pressure input pins')
    equal(binding['codePins'], plan['codePins'], 'Pressure code pins')
    require(binding['planSha256'] == plan_sha and binding['output']['sha256'] == sha(output_path), 'Pressure binding hash')
    require(binding['verification']['passed'] is True, 'Pressure source binding not passed')
    require(binding['accountsExecuted'] == 0 and binding['outcomeFiltering'] is False, 'Pressure preparation scope')
    require(binding['signalScheduleSha256'] == digest(signals), 'Pressure schedule hash')
    calendar = prior['sourceCalendar']
    require(calendar == sorted(set(calendar)), 'Pressure calendar order')
    require(prior['sourceCalendarSha256'] == binding['sourceCalendarSha256'] == digest(calendar), 'Pressure calendar hash')
    ids = [s['signalId'] for s in signals]
    require(list(prior['rows']) == ids and len(ids) == len(set(ids)), 'Pressure rows missing or reordered')
    positions = {day: i for i, day in enumerate(calendar)}
    module_path = ROOT / 'scripts/prepare-relative-repair-context-pressure.py'
    spec = importlib.util.spec_from_file_location('_independent_context_pressure_math', module_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    counts = {'windows': len(signals), 'pressure5Known': 0, 'pressure20Known': 0, 'missingSlots': 0}
    for signal in signals:
        row = prior['rows'][signal['signalId']]
        day, symbol = signal['signalDate'], signal['symbol']
        i = positions[day]
        require(i > 0 and row['asOfDate'] == row['previousCalendarDate'] == calendar[i - 1], 'Pressure is not T-1')
        require(row['symbol'] == symbol and row['signalDate'] == day, 'Pressure signal identity')
        dates = calendar[max(0, i - 40):i]
        require(row['windowDates'] == dates and len(row['windowBars']) == len(dates), 'Compressed/future pressure window')
        require(row['windowSha256'] == digest(row['windowBars']), 'Pressure window hash')
        require(row['sourceDatasetFingerprint'] == binding['sourceDatasetFingerprint'], 'Pressure source fingerprint')
        for bar, expected_day in zip(row['windowBars'], dates):
            require(bar is None or bar['date'] == expected_day, 'Pressure saved bar date')
        counts['missingSlots'] += sum(bar is None for bar in row['windowBars'])
        for horizon in (5, 20):
            key = f'pressure{horizon}'
            equal(row[key], helper.independent_pressure(row['windowBars'], horizon), f'Pressure {signal["signalId"]} {key}')
            counts[key + 'Known'] += row[key] is not None
    return counts


def feature_values(signal, market, event_day, event, pressure, calendar_index):
    day = signal['signalDate']
    require(day == market['date'] == event_day['date'], 'Market join is not signal date')
    require(event_day['eventId'] == event['eventId'], 'Market event identity')
    require(event['peakDate'] <= event['triggerDate'] < day, 'Future event anchor')
    age = calendar_index[day] - calendar_index[event['triggerDate']]
    require(age == event_day['age'] and age > 0, 'Event age')
    price, matched, amount, cohort = (market[k] for k in ('price20', 'matched5', 'amount', 'yesterdayCohort'))
    require(price['evaluableCount'] == price['positiveCount'] + price['negativeCount'] + price['zeroCount'], 'Breadth denominator')
    breadth = 100 * price['positiveCount'] / price['evaluableCount'] if price['evaluableCount'] else None
    equal(None if breadth is None else breadth / 100, price['positiveReturn20Breadth'], 'Stored breadth')
    require(cohort['eligibleCount'] == cohort['continuedCount'] + cohort['observedFailureCount'] + cohort['unknownCount'], 'Continuation denominator')
    if matched['available']:
        require(matched['earlierDate'] < day and matched['pairedCount'] > 0, 'Matched earlier date')
    if cohort['previousCohortAvailable']:
        require(cohort['previousDate'] < day, 'Future cohort')
    q = (100 * cohort['continuedCount'] / cohort['eligibleCount']
         if cohort['previousCohortAvailable'] and cohort['eligibleCount'] > 0 and cohort['unknownCount'] == 0 else None)
    long, short = pressure['pressure20'], pressure['pressure5']
    values = dict(zip(FEATURES, (breadth,
        matched['matchedBreadthChange5'] if matched['available'] else None,
        amount['medianRelativeToOwnPrior20'], q,
        (event_day['benchmarkClose'] - event['peakClose']) / event['peakClose'] * 100,
        age, long, short - long if short is not None and long is not None else None)))
    support = {'breadthEvaluable': price['evaluableCount'], 'breadthCoverage': price['coverage'],
               'matchedPairedCount': matched['pairedCount'], 'amountEvaluable': amount['evaluableCount'],
               'qCounts': market['qCounts'], 'previousCohort': {k: cohort[k] for k in
                   ('eligibleCount', 'continuedCount', 'observedFailureCount', 'unknownCount',
                    'observedCoverage', 'continuationLowerBound', 'continuationUpperBound')},
               'pressure5Prior': short, 'pressureAsOfDate': pressure['asOfDate'], 'marketAsOfDate': day,
               'benchmarkClose': event_day['benchmarkClose'], 'peakDate': event['peakDate'], 'peakClose': event['peakClose']}
    return values, support


def account_outcomes(signals, membership, result, summary, cost_events, cost_account, group):
    require(result['status'] == summary['account']['status'] == 'completed', 'Incomplete source account')
    require(summary['inputResultSha256'] == digest(result), 'Source summary digest')
    require(summary['policyId'] == ('matchedControl' if group == 'control10' else group), 'Source summary policy')
    equal(result['research']['identity']['signalSchedule'], signals, 'Account original schedule')
    opportunities = result['research']['opportunities']
    require([op['signalId'] for op in opportunities] == [s['signalId'] for s in signals], 'Opportunity order differs')
    equal(opportunities, summary['opportunityDispositions']['opportunities'], 'Summary opportunity records')
    outcomes, entry_keys, buy_ids = {}, {}, set()
    buys = {t['fillId']: t for t in result['trades'] if t['side'] == 'buy'}
    require(len(buys) == sum(t['side'] == 'buy' for t in result['trades']), 'Duplicate buy fill')
    for signal, op in zip(signals, opportunities):
        for k, value in signal.items():
            equal(op[k], value, 'Opportunity identity ' + k)
        sid, quantity = signal['signalId'], op.get('filledQuantity', 0)
        require(finite(quantity) and quantity >= 0, 'Bad filled quantity')
        outcomes[sid] = {'state': 'notEntered', 'opportunityStatus': op['status'], 'filledQuantity': quantity}
        if quantity:
            key = (op['symbol'], op['attemptDate'])
            require(key not in entry_keys and op['fillId'] not in buy_ids, 'Duplicate entry')
            entry_keys[key], fill = sid, buys[op['fillId']]
            require((fill['symbol'], fill['date']) == key and fill['signalId'] == sid, 'Entry fill mapping')
            equal(fill['quantity'], quantity, 'Entry quantity')
            buy_ids.add(op['fillId'])
    require(buy_ids == set(buys), 'Unmapped buys')
    sessions = [r['date'] for r in result['curve']]
    require(sessions == sorted(set(sessions)), 'Account curve calendar')
    session_index = {d: i for i, d in enumerate(sessions)}
    consumed = set()
    raw_records = {'closed': result['closedTrades'], 'open': result['positions']}
    summary_records = {'closed': summary['closedEpisodes']['episodes'], 'open': summary['openPositions']['positions']}
    for kind in ('closed', 'open'):
        raw_map = {(r['symbol'], r['entryDate']): r for r in raw_records[kind]}
        require(len(raw_map) == len(raw_records[kind]), 'Duplicate raw episode')
        saved_map = {(r['record']['symbol'], r['record']['entryDate']): r for r in summary_records[kind]}
        require(raw_map.keys() == saved_map.keys() and len(saved_map) == len(summary_records[kind]), 'Raw-summary episode coverage')
        for key, record in raw_map.items():
            require(key in entry_keys and key not in consumed, 'Unmapped or duplicated episode')
            consumed.add(key)
            sid, saved = entry_keys[key], saved_map[key]
            equal(saved['record'], record, 'Source episode raw record')
            outcome = outcomes[sid]
            outcome.update(state=kind, entryDate=record['entryDate'])
            if kind == 'closed':
                invested, pnl = record['investedAmount'], record['pnlAmount']
                require(finite(invested) and invested > 0 and finite(pnl), 'Unknown closed outcome cannot become zero')
                holding = session_index[record['exitDate']] - session_index[record['entryDate']] + 1
                ret = pnl / invested * 100
                for name, value in (('pnlAmount', pnl), ('netReturnPct', ret), ('holdingSessions', holding)):
                    equal(saved[name], value, 'Source closed ' + name)
                outcome.update(exitDate=record['exitDate'], netReturnPct=ret, pnlAmount=pnl, holdingSessions=holding)
                source_key = 'closedEpisodes'
            else:
                unrealized = record['marketValue'] - record['costBasis']
                stale = record['priceDate'] != sessions[-1]
                for name, value in (('unrealizedPnlAmount', unrealized), ('realizedPnlWhileOpen', record['realizedPnl']), ('markStaleAtCutoff', stale)):
                    equal(saved[name], value, 'Source open ' + name)
                outcome.update(unrealizedPnlAmount=unrealized, realizedPnlWhileOpen=record['realizedPnl'], markStaleAtCutoff=stale)
                source_key = 'openPositions'
            attributed = cost_events[membership[sid]]['groups'][group][source_key]
            matches = [r for r in attributed if r['signalId'] == sid]
            require(len(matches) == 1, 'Missing/duplicate event outcome')
            equal(matches[0], dict(saved, signalId=sid, eventId=membership[sid],
                                   originalRank=next(s['originalRank'] for s in signals if s['signalId'] == sid)), 'Original event attribution')
    require(consumed == set(entry_keys), 'Filled entry lacks outcome')
    # Preserve identity, status and incomplete-state semantics, not only totals.
    by_id = {s['signalId']: s for s in signals}
    for eid, event in cost_events.items():
        group_saved = event['groups'][group]
        selected = [sid for sid in outcomes if membership[sid] == eid]
        entered = [sid for sid in selected if outcomes[sid]['state'] != 'notEntered']
        closed = [sid for sid in selected if outcomes[sid]['state'] == 'closed']
        opened = [sid for sid in selected if outcomes[sid]['state'] == 'open']
        equal(group_saved['opportunities'], len(selected), 'Event opportunity count')
        equal(group_saved['opportunityStatuses'], dict(Counter(outcomes[sid]['opportunityStatus'] for sid in selected)), 'Event statuses')
        for field, value in (('actualEntries', len(entered)), ('closedCount', len(closed)), ('openCountAtCutoff', len(opened)),
                             ('closedNetPnlAmount', math.fsum(outcomes[sid]['pnlAmount'] for sid in closed)),
                             ('openUnrealizedPnlAmount', math.fsum(outcomes[sid]['unrealizedPnlAmount'] for sid in opened))):
            equal(group_saved[field], value, 'Event original ' + field)
        require({r['signalId'] for r in group_saved['closedEpisodes']} == set(closed), 'Extra event closed episode')
        require({r['signalId'] for r in group_saved['openPositions']} == set(opened), 'Extra event open position')
        expected_entries = [{'signalId': sid, 'eventId': eid, 'originalRank': by_id[sid]['originalRank'],
                             'symbol': by_id[sid]['symbol'], 'entryDate': outcomes[sid]['entryDate']} for sid in entered]
        equal(group_saved['entryAttributions'], expected_entries, 'Event entry identities')
    closed_pnl = math.fsum(v['pnlAmount'] for v in outcomes.values() if v['state'] == 'closed')
    open_pnl = math.fsum(v['unrealizedPnlAmount'] for v in outcomes.values() if v['state'] == 'open')
    open_realized = math.fsum(v['realizedPnlWhileOpen'] for v in outcomes.values() if v['state'] == 'open')
    residual = summary['account']['netPnlAmount'] - closed_pnl - open_pnl - open_realized
    equal(closed_pnl, summary['closedEpisodes']['netPnlAmount'], 'Account closed PnL')
    equal(residual, summary['reconciliation']['episodePnlBridge']['residual'], 'Account PnL bridge')
    closed_values = [v for v in outcomes.values() if v['state'] == 'closed']
    open_values = [v for v in outcomes.values() if v['state'] == 'open']
    expected_account = {'opportunities': len(outcomes), 'actualEntries': len(entry_keys),
        'closedCount': len(closed_values), 'openCountAtCutoff': len(open_values),
        'opportunityStatuses': dict(Counter(v['opportunityStatus'] for v in outcomes.values())),
        'wins': sum(v['pnlAmount'] > 0 for v in closed_values),
        'losses': sum(v['pnlAmount'] < 0 for v in closed_values),
        'zeros': sum(v['pnlAmount'] == 0 for v in closed_values),
        'unknownPnlCount': 0, 'unknownReturnCount': 0,
        'meanClosedNetReturnPct': avg([v['netReturnPct'] for v in closed_values]),
        'closedNetPnlAmount': closed_pnl, 'openUnrealizedPnlAmount': open_pnl,
        'realizedPnlWhileStillOpen': open_realized,
        'openMarketValue': math.fsum(r['marketValue'] for r in result['positions']),
        'staleMarksAtCutoff': sum(v['markStaleAtCutoff'] for v in open_values),
        'accountNetPnlAmount': summary['account']['netPnlAmount'],
        'accountReturnPct': summary['account']['netReturnPct'],
        'unallocatedPnlBridgeResidual': residual, 'id': group, 'slippageBps': int(group[7:]),
        'group': 'matchedControl' if group == 'control10' else group,
        'reused': group == 'control10', 'reconciliation': 'passed'}
    for field, value in expected_account.items():
        equal(cost_account[field], value, 'Original event-account ' + field)
    return outcomes, {'opportunities': len(outcomes), 'actualEntries': len(entry_keys),
                      'closed': len(raw_records['closed']), 'open': len(raw_records['open']),
                      'notEntered': len(outcomes) - len(entry_keys), 'closedNetPnlAmount': closed_pnl,
                      'openUnrealizedPnlAmount': open_pnl, 'openRealizedPnlAmount': open_realized,
                      'pnlBridgeResidual': residual}


def verify(directory):
    started = time.perf_counter()
    output = directory / 'validation.json'
    require(not output.exists(), 'Refusing to overwrite validation.json')
    plan_path, result_path = directory / 'plan.json', directory / 'diagnostic.json'
    plan, diagnostic = read(plan_path), read(result_path)
    plan_sha = sha(plan_path)
    pinned = {}
    for category in ('inputPins', 'codePins'):
        require(plan[category], 'Empty pin category')
        for name, expected in plan[category].items():
            require(sha(local(name)) == expected, 'Frozen file changed: ' + name)
            pinned[name] = expected
    require(diagnostic['planSha256'] == plan_sha, 'Diagnostic plan hash')
    for field in ('newEntryVariants', 'newAccountRuns'):
        require(plan[field] == diagnostic[field] == 0, 'Unexpected experiment scope')
    require(plan['thresholdSearch'] is False and plan['profitHurdles'] is None, 'Unexpected tuning')
    require(diagnostic['certified'] is False and diagnostic['targetAchieved'] is False, 'Unsupported certification')
    require(set(plan['features']) == set(FEATURES), 'Feature set')
    require(set(plan['periods']) == set(diagnostic['periods']) == set(COUNTS), 'Period coverage')
    artifacts = diagnostic['artifacts']
    expected_artifacts = {str((directory / p / f).relative_to(ROOT)).replace('\\', '/')
                          for p in COUNTS for f in ('context-rows.json.gz', 'prior-pressure.json.gz', 'pressure-binding.json')}
    require(set(artifacts) == expected_artifacts, 'Diagnostic artifact coverage')
    for name, expected in artifacts.items():
        require(sha(local(name)) == expected, 'Diagnostic artifact changed: ' + name)
    source_path = 'artifacts/strategy-search/relative-repair-cost-stress-v1/event-outcomes.json'
    require(source_path in pinned, 'Cost attribution must be pinned')
    cost = read(local(source_path))
    report = {'schema': 'relative-repair-context-validation-v1', 'status': 'passed', 'passed': True,
              'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(Path(__file__)),
              'planSha256': plan_sha, 'diagnosticSha256': sha(result_path), 'inputPins': pinned,
              'artifactPins': artifacts, 'periods': {}, 'checks': [], 'newAccountRuns': 0,
              'newEntryVariants': 0, 'certified': False, 'targetAchieved': False}
    for period, n in COUNTS.items():
        spec = plan['periods'][period]
        require(spec['signalCount'] == n, 'Registered signal count')
        require(set(spec['accounts']) == set(GROUPS), 'Missing cost sensitivity account')
        for name in ('signalsPath', 'sourcePath', 'evidencePath', 'marketDailyPath'):
            require(spec[name] in pinned, 'Missing period pin: ' + name)
        signals = read(local(spec['signalsPath']))
        require(len(signals) == n, 'Signal cardinality')
        evidence = read(local(spec['evidencePath']))
        events, membership, event_days = event_membership(evidence, signals)
        require(len(events) == (4 if period == '2022-2023' else 5), 'Event count')
        cost_events = {e['eventId']: e for e in cost['periods'][period]['events']}
        require(set(events) == set(cost_events), 'Original event coverage')
        with gzip.open(local(spec['marketDailyPath']), 'rt', encoding='utf-8') as stream:
            market_rows = [json.loads(line) for line in stream if line.strip()]
        require([r['date'] for r in market_rows] == sorted({r['date'] for r in market_rows}), 'Market calendar duplicate/order')
        market = {r['date']: r for r in market_rows}
        calendar_index = {day: i for i, day in enumerate(market)}
        rows = read(directory / period / 'context-rows.json.gz')
        require(len(rows) == n, 'Lost diagnostic rows')
        prior = read(directory / period / 'prior-pressure.json.gz')
        binding = read(directory / period / 'pressure-binding.json')
        equal(binding['sourceIdentity'], read(local(spec['sourcePath'])), 'Pressure original source identity')
        pressure_counts = saved_pressure(prior, binding, signals, plan, plan_sha, directory / period / 'prior-pressure.json.gz')
        account_values, account_counts = {}, {}
        for group in GROUPS:
            paths = spec['accounts'][group]
            require(set(paths.values()) <= set(pinned), 'Unpinned source account')
            account_values[group], account_counts[group] = account_outcomes(signals, membership,
                read(local(paths['result'])), read(local(paths['summary'])), cost_events,
                cost['periods'][period]['accounts'][group], group)
        for original, row in zip(signals, rows):
            sid, day = original['signalId'], original['signalDate']
            eid = membership[sid]
            values, support = feature_values(original, market[day], event_days[day], events[eid], prior['rows'][sid], calendar_index)
            expected = {**original, 'period': period, 'eventId': eid, 'features': values,
                        'supportingEvidence': support, 'outcomes': {g: account_values[g][sid] for g in GROUPS}}
            equal(row, expected, 'Context row ' + sid)
        actual = diagnostic['periods'][period]
        require(actual['opportunities'] == n and actual['eventCount'] == len(events), 'Diagnostic dimensions')
        require(actual['uniqueSignalDates'] == len({s['signalDate'] for s in signals}), 'Diagnostic unique dates')
        equal(actual['marketBinding'], spec['marketBinding'], 'Market binding')
        equal(actual['contextMissingness'], {f: describe([r['features'][f] for r in rows]) for f in FEATURES}, 'Missingness')
        expected_summaries = [event_summary([r for r in rows if r['eventId'] == eid], event, group)
                              for eid, event in events.items() for group in GROUPS]
        equal(actual['events'], expected_summaries, 'All event summaries')
        associations = actual['associations']
        require([(a['account'], a['feature']) for a in associations] == [(g, f) for g in GROUPS for f in FEATURES], 'Association order/coverage')
        association_counts = []
        for association in associations:
            group, feature = association['account'], association['feature']
            points = observations(rows, feature, group)
            event_stats = [{'eventId': eid, **correlation([p for p in points if p['eventId'] == eid])} for eid in events]
            omitted = [{'omittedEventId': eid, **correlation([p for p in points if p['eventId'] != eid])} for eid in events]
            expected = {'account': group, 'feature': feature, 'label': plan['features'][feature]['label'],
                        'observationUnit': 'closedEpisode' if feature in STOCK else 'signalDateMeanClosedNetReturn',
                        'pooled': correlation(points), 'byEvent': event_stats, 'leaveOneEventOut': omitted,
                        'eventRhoDistribution': describe([r['rho'] for r in event_stats]),
                        'leaveOneEventOutRhoDistribution': describe([r['rho'] for r in omitted]), 'points': points}
            equal(association, expected, 'Association ' + group + ' ' + feature)
            association_counts.append({'account': group, 'feature': feature, 'points': len(points),
                                       'paired': expected['pooled']['paired'], 'eventsRetained': len(event_stats),
                                       'omissions': len(omitted)})
        report['periods'][period] = {'rows': n, 'events': len(events), 'marketRows': len(market_rows),
                                     'uniqueSignalDates': actual['uniqueSignalDates'], 'pressure': pressure_counts,
                                     'accounts': account_counts, 'associations': association_counts}
        print(json.dumps({'period': period, 'passed': True, 'rows': n, 'events': len(events)}, ensure_ascii=False), flush=True)
    report['checks'] = ['All input/code/artifact hashes pinned and verified',
        '1189 original opportunities reconstructed from daily matching pairs, identity and order retained',
        'Signal-date exact market join, frozen anchor and elapsed-session clock',
        'T-1 calendar-aligned saved pressure windows and independent pressure arithmetic',
        'Raw accounts, summaries and event identities reconciled for all six account observations',
        'Unfilled opportunities remain without returns; terminal open positions stay censored',
        'All nine events and all three costs retained; every event distribution recomputed',
        'Market predictors use unique event/signal dates; stock pressure uses closed episodes',
        'Average-tie Spearman independently recomputed including null, constant, within-event and leave-one-event cases']
    report['scope'] = {'allSignals': 1189, 'allEvents': 9, 'accountObservations': 6,
        'primaryCostBps': 10, 'sensitivityCostBps': [30, 50], 'sourceMarketRawReloaded': False,
        'diagnosticCalculationFunctionsImported': False,
        'pressureArithmeticReused': 'independent_pressure in pinned prepare-relative-repair-context-pressure.py',
        'limitations': ['Frozen raw bar authenticity and market aggregate mathematics inherit their earlier source audits.',
            'No new account simulation, trade executability re-audit, independent holdout, causality, or parameter selection.',
            'Closed-only associations are dependent observations and exclude open and unfilled outcomes.']}
    report['elapsedSeconds'] = time.perf_counter() - started
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'output': str(output.relative_to(ROOT)), 'sha256': sha(output),
                      'elapsedSeconds': report['elapsedSeconds']}, ensure_ascii=False))


def self_test():
    equal(average_ranks([4, 1, 4, 3]), [3.5, 1, 3.5, 2], 'Tied ranks hand calculation')
    equal(correlation([{'x': x, 'y': y} for x, y in [(1, 10), (1, 10), (3, 30)]]),
          {'rows': 3, 'paired': 3, 'unknownExcluded': 0, 'rho': 1., 'reason': None}, 'Tied identical ranks')
    equal(correlation([{'x': x, 'y': -x} for x in (1, 2, 3)])['rho'], -1., 'Reverse ranks')
    require(correlation([{'x': 1, 'y': i} for i in range(3)])['reason'] == 'constantRankVariable', 'Constant correlation')
    require(correlation([{'x': None, 'y': 1}, {'x': 2, 'y': 3}])['paired'] == 1, 'Unknown exclusion')
    equal(describe([None, 0, 2, 4, 6]), {'count': 5, 'known': 4, 'unknown': 1, 'minimum': 0,
          'q25': 1.5, 'median': 3., 'q75': 4.5, 'maximum': 6, 'mean': 3.}, 'Quartiles hand calculation')
    rows = [{'signalId': str(i), 'signalDate': day, 'eventId': event,
             'features': {'breadth20Pct': 50, 'pressure20Prior': i},
             'outcomes': {'control10': {'state': state, **({'netReturnPct': ret} if state == 'closed' else {})}}}
            for i, (day, event, state, ret) in enumerate([('d1', 'e1', 'closed', 12), ('d1', 'e1', 'closed', -4),
                ('d1', 'e1', 'open', 999), ('d2', 'e1', 'notEntered', 0), ('d3', 'e2', 'closed', -2)])]
    points = observations(rows, 'breadth20Pct', 'control10')
    require(len(points) == 2 and points[0]['y'] == 4 and points[0]['closedTrades'] == 2, 'Date weighting/censoring')
    require(len(observations(rows, 'pressure20Prior', 'control10')) == 3, 'Individual pressure observations')
    require(correlation([p for p in points if p['eventId'] != 'e1'])['paired'] == 1, 'Leave-event count')
    for wrong, expected in [(0, None), (False, 0), ({'a': 1}, {'a': 1, 'b': None}), ([1, 2], [2, 1])]:
        try:
            equal(wrong, expected, 'Expected mutation rejection')
        except ValueError:
            pass
        else:
            raise ValueError('Mutation was accepted')
    print(json.dumps({'passed': True, 'checks': ['manual average ties', 'tied Spearman', 'reverse ranks',
        'constant undefined', 'null exclusion', 'manual quartiles', 'unique market-date weighting',
        'censored states excluded', 'individual stock observations', 'event omission counts',
        'null-zero/bool-number/missing-key/order mutations rejected']}))


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    self_test() if args.self_test else verify(args.directory.resolve())
