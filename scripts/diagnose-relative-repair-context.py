"""Describe pre-entry context of all frozen controls; never select or trade.

Register before running. The primary observation for market variables is a
unique signal date, with equal-weight mean net return of its closed entries.
Stock-specific pressure uses individual closed episodes. Neither is an IID
sample; event omissions are association sensitivity, not portfolio replays.
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
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'relative-repair-context-diagnostic-v1'
COST = BASE / 'relative-repair-cost-stress-v1'
PARENT = BASE / 'relative-repair-screen-v1'
PERIODS = ('2022-2023', '2024-2025')
GROUPS = ('control10', 'control30', 'control50')
FEATURES = {
    'breadth20Pct': ('20日上涨广度（%）', 'market', '100 * positiveCount / evaluableCount at T'),
    'matchedBreadthChange5Pp': ('5日同群广度变化（百分点）', 'market', 'Frozen matched5 change at T; unavailable stays null'),
    'medianAmountRatio20': ('市场相对成交额', 'market', 'Median stock amount(T) / own prior20 mean; frozen daily aggregate'),
    'qContinuationPct': ('昨日强势队列延续率（%）', 'market', '100 * continued / eligible only when eligible>0, prior cohort available and unknownCount=0'),
    'benchmarkDrawdownFromAnchorPct': ('指数距冻结锚点（%）', 'market', '100 * (benchmark close(T) / event peakClose - 1)'),
    'eventAgeSessions': ('事件已进行交易日', 'market', 'Index(T) - index(event trigger); no completed-event length'),
    'pressure20Prior': ('T-1二十日压力', 'stock', 'Existing weighted pressure20 on 40 aligned slots ending T-1'),
    'pressureAccelerationPrior': ('T-1短长压力差', 'stock', 'pressure5(T-1) - pressure20(T-1); both must be known'),
}


def require(test, message):
    if not test:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as f:
        return json.load(f)


def write(path, value):
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    with Path(path).open('xb') as f:
        if str(path).endswith('.gz'):
            with gzip.GzipFile(fileobj=f, mode='wb', mtime=0) as z:
                z.write(encoded)
        else:
            f.write(encoded)


def module(name):
    spec = importlib.util.spec_from_file_location('_ctx_' + name.replace('-', '_'), ROOT / 'scripts' / name)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def quantile(values, p):
    if not values:
        return None
    values = sorted(values)
    at = (len(values) - 1) * p
    lo = int(at)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (at - lo)


def distribution(values):
    require(all(x is None or finite(x) for x in values), 'Nonfinite diagnostic input')
    known = [x for x in values if x is not None]
    return {'count': len(values), 'known': len(known), 'unknown': len(values) - len(known),
            'minimum': min(known) if known else None, 'q25': quantile(known, .25),
            'median': median(known) if known else None, 'q75': quantile(known, .75),
            'maximum': max(known) if known else None, 'mean': mean(known) if known else None}


def ranks(values):
    ordered = sorted(enumerate(values), key=lambda x: x[1])
    result, i = [None] * len(values), 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        for at, _ in ordered[i:j]:
            result[at] = (i + 1 + j) / 2
        i = j
    return result


def spearman(points):
    paired = [r for r in points if finite(r['x']) and finite(r['y'])]
    result = {'rows': len(points), 'paired': len(paired), 'unknownExcluded': len(points) - len(paired),
              'rho': None, 'reason': None}
    if len(paired) < 3:
        result['reason'] = 'fewerThanThreePairs'
        return result
    x, y = ranks([r['x'] for r in paired]), ranks([r['y'] for r in paired])
    a, b = mean(x), mean(y)
    xx = math.fsum((v - a) ** 2 for v in x)
    yy = math.fsum((v - b) ** 2 for v in y)
    if xx == 0 or yy == 0:
        result['reason'] = 'constantRankVariable'
        return result
    result['rho'] = math.fsum((v - a) * (w - b) for v, w in zip(x, y)) / math.sqrt(xx * yy)
    return result


def context_values(signal, market, event_day, event, pressure, calendar):
    day = signal['signalDate']
    require(market['date'] == event_day['date'] == pressure['signalDate'] == day, 'As-of day mismatch')
    require(pressure['symbol'] == signal['symbol'], 'Pressure stock mismatch')
    require(event_day['eventId'] == event['eventId'], 'Event mismatch')
    require(event['peakDate'] <= event['triggerDate'] < day, 'Noncausal event dates')
    age = calendar.index(day) - calendar.index(event['triggerDate'])
    require(age == event_day['age'] and age > 0, 'Event clock mismatch')
    require(pressure['asOfDate'] == pressure['previousCalendarDate'] < day, 'Noncausal pressure date')
    p, m, a, q = market['price20'], market['matched5'], market['amount'], market['yesterdayCohort']
    require(p['positiveCount'] + p['zeroCount'] + p['negativeCount'] == p['evaluableCount'], 'Breadth counts')
    breadth = 100 * p['positiveCount'] / p['evaluableCount'] if p['evaluableCount'] else None
    require(breadth is None or math.isclose(breadth / 100, p['positiveReturn20Breadth'], abs_tol=1e-12), 'Breadth formula')
    if m['available']:
        require(m['earlierDate'] < day and m['pairedCount'] > 0, 'Invalid matched breadth')
    total = q['eligibleCount']
    require(total == q['continuedCount'] + q['observedFailureCount'] + q['unknownCount'], 'Cohort counts')
    if q['previousCohortAvailable']:
        require(q['previousDate'] < day, 'Noncausal cohort')
    continuation = (100 * q['continuedCount'] / total
                    if q['previousCohortAvailable'] and total > 0 and q['unknownCount'] == 0 else None)
    short, long = pressure['pressure5'], pressure['pressure20']
    values = {
        'breadth20Pct': breadth,
        'matchedBreadthChange5Pp': m['matchedBreadthChange5'] if m['available'] else None,
        'medianAmountRatio20': a['medianRelativeToOwnPrior20'],
        'qContinuationPct': continuation,
        'benchmarkDrawdownFromAnchorPct': 100 * (event_day['benchmarkClose'] / event['peakClose'] - 1),
        'eventAgeSessions': age,
        'pressure20Prior': long,
        'pressureAccelerationPrior': short - long if short is not None and long is not None else None,
    }
    require(all(v is None or finite(v) for v in values.values()), 'Nonfinite feature')
    supporting = {
        'breadthEvaluable': p['evaluableCount'], 'breadthCoverage': p['coverage'],
        'matchedPairedCount': m['pairedCount'], 'amountEvaluable': a['evaluableCount'],
        'qCounts': market['qCounts'], 'previousCohort': {k: q[k] for k in
          ('eligibleCount', 'continuedCount', 'observedFailureCount', 'unknownCount',
           'observedCoverage', 'continuationLowerBound', 'continuationUpperBound')},
        'pressure5Prior': short, 'pressureAsOfDate': pressure['asOfDate'],
        'marketAsOfDate': day, 'benchmarkClose': event_day['benchmarkClose'],
        'peakDate': event['peakDate'], 'peakClose': event['peakClose'],
    }
    return values, supporting


def points_for(rows, feature, group):
    closed = [r for r in rows if r['outcomes'][group]['state'] == 'closed']
    if FEATURES[feature][1] == 'stock':
        return [{'eventId': r['eventId'], 'signalDate': r['signalDate'], 'signalId': r['signalId'],
                 'x': r['features'][feature], 'y': r['outcomes'][group]['netReturnPct'], 'closedTrades': 1}
                for r in closed]
    dates = defaultdict(list)
    for r in closed:
        dates[(r['eventId'], r['signalDate'])].append(r)
    result = []
    for (event, day), group_rows in sorted(dates.items()):
        xs = [r['features'][feature] for r in group_rows]
        require(all(x == xs[0] for x in xs), 'Market feature differs within date')
        result.append({'eventId': event, 'signalDate': day, 'x': xs[0],
                       'y': mean(r['outcomes'][group]['netReturnPct'] for r in group_rows),
                       'closedTrades': len(group_rows)})
    return result


def associations(rows, events):
    result = []
    for group in GROUPS:
        for feature, (label, scope, _) in FEATURES.items():
            points = points_for(rows, feature, group)
            event_rows = [{'eventId': e, **spearman([p for p in points if p['eventId'] == e])} for e in events]
            omissions = [{'omittedEventId': e, **spearman([p for p in points if p['eventId'] != e])} for e in events]
            result.append({'account': group, 'feature': feature, 'label': label,
                'observationUnit': 'signalDateMeanClosedNetReturn' if scope == 'market' else 'closedEpisode',
                'pooled': spearman(points), 'byEvent': event_rows, 'leaveOneEventOut': omissions,
                'eventRhoDistribution': distribution([r['rho'] for r in event_rows]),
                'leaveOneEventOutRhoDistribution': distribution([r['rho'] for r in omissions]),
                'points': points})
    return result


def summarize_event(rows, event, group):
    closed = [r for r in rows if r['outcomes'][group]['state'] == 'closed']
    opened = [r for r in rows if r['outcomes'][group]['state'] == 'open']
    entered = closed + opened
    returns = [r['outcomes'][group]['netReturnPct'] for r in closed]
    dates = {r['signalDate']: r for r in rows}
    return {'eventId': event['eventId'], 'triggerDate': event['triggerDate'], 'account': group,
        'opportunities': len(rows), 'uniqueSignalDates': len(dates), 'actualEntries': len(entered),
        'closed': len(closed), 'open': len(opened),
        'closedNetPnlAmount': math.fsum(r['outcomes'][group]['pnlAmount'] for r in closed),
        'openUnrealizedPnlAmount': math.fsum(r['outcomes'][group]['unrealizedPnlAmount'] for r in opened),
        'returnDistribution': distribution(returns),
        'wins': sum(v > 0 for v in returns), 'losses': sum(v < 0 for v in returns),
        'meanWinnerPct': mean([v for v in returns if v > 0]) if any(v > 0 for v in returns) else None,
        'meanLoserPct': mean([v for v in returns if v < 0]) if any(v < 0 for v in returns) else None,
        'holdingSessions': distribution([r['outcomes'][group]['holdingSessions'] for r in closed]),
        'contextDistributions': {feature: {
            'allOpportunities': distribution([r['features'][feature] for r in rows]),
            'uniqueSignalDates': distribution([r['features'][feature] for r in dates.values()]) if scope == 'market' else None,
            'actualEntries': distribution([r['features'][feature] for r in entered]),
            'closedEntries': distribution([r['features'][feature] for r in closed]),
            'openEntries': distribution([r['features'][feature] for r in opened]),
        } for feature, (_, scope, _) in FEATURES.items()}}


def register():
    require(not OUT.exists(), 'Diagnostic already registered; do not overwrite')
    require(read(COST / 'validation.json')['passed'], 'Cost audit not passed')
    loader = module('load-market-context-screen.py')
    baselines = {p: loader.load_baseline(p) for p in PERIODS}
    files = [COST / n for n in ('plan.json', 'comparison.json', 'validation.json', 'event-outcomes.json', 'readout.json', 'delivery-completion.json')]
    files += [BASE / 'market-context-screen-v1/readout.json', BASE / 'market-phase-atlas-v1/research-protocol.json']
    periods = {}
    for p, base in baselines.items():
        source, signals = COST / p / 'source.json', COST / p / 'signals.json'
        require(read(source) == base['sourceIdentity'], 'Market and account source identity differs')
        refs = read(COST / p / 'baseline-reference.json')
        accounts = {'control10': {k: refs[k]['path'] for k in ('result', 'summary')}}
        for g in ('control30', 'control50'):
            accounts[g] = {k: relative(COST / p / (g + ('.result.json.gz' if k == 'result' else '.summary.json')))
                           for k in ('result', 'summary')}
        evidence = PARENT / p / 'signals-evidence.json.gz'
        files += [source, signals, evidence, COST / p / 'baseline-reference.json']
        files += [ROOT / item['path'] for item in base['files']]
        files += [ROOT / path for paths in accounts.values() for path in paths.values()]
        periods[p] = {'signalsPath': relative(signals), 'sourcePath': relative(source),
            'signalCount': len(read(signals)), 'evidencePath': relative(evidence),
            'marketDailyPath': base['marketBinding']['directory'] + '/daily.jsonl.gz',
            'marketBinding': base['marketBinding'], 'accounts': accounts}
    code = ['scripts/diagnose-relative-repair-context.py', 'scripts/prepare-relative-repair-context-pressure.py',
            'scripts/load-market-context-screen.py', 'scripts/run-market-context-screen.py',
            'scripts/summarize-relative-repair-events.py', 'scripts/load-supplemental-account-source.py',
            'scripts/run-account-exit-comparison.py', 'scripts/run-supplemental-force-context.py', 'service/force_statistics.py']
    plan = {'schema': 'relative-repair-context-diagnostic-v1', 'registeredAt': now(),
        'previousGoalTurn': {'classification': 'progress', 'evidence': relative(COST / 'delivery-completion.json'),
                            'sha256': sha(COST / 'delivery-completion.json')},
        'inputPins': {relative(p): sha(p) for p in sorted(set(files))},
        'codePins': {p: sha(ROOT / p) for p in code}, 'periods': periods,
        'features': {k: {'label': v[0], 'scope': v[1], 'definition': v[2]} for k, v in FEATURES.items()},
        'design': {
            'population': 'All 1189 original matchedControl opportunities and all nine events. No gate, rank or account modification.',
            'knownAtEntry': 'Market and index at signal close T; stock pressure at exchange-calendar T-1. Entry outcomes occur later.',
            'qUnknown': 'Continuation is null if empty, unavailable or partly unknown. Preserve cohort sizes and missingness bounds.',
            'outcomes': 'Actual original account episodes at all three existing cost levels. Not-entered has no hypothetical outcome; open remains separate.',
            'marketAssociation': 'One observation per event/signalDate with at least one closed entry; y=equal-weight mean closed return of that date. Same-day stocks do not multiply market observations.',
            'stockAssociation': 'One closed episode per observation for stock-specific prior pressure; shared events and capital remain dependencies.',
            'statistics': 'Continuous medians, quartiles, average-tie Spearman, within-event associations and each single-event omission. No fitted cutpoint, p-value, prediction, optimization or counterfactual filtered account.',
            'costs': '10bps is primary; 30/50 sensitivity reflects changed execution cohorts and exits, not independent evidence. All associations reported.',
            'eventSummaries': 'All opportunity and distinct-date medians describe the observed event window ex post, never an entry feature.',
            'priorUse': 'Breadth/continuation/prior-pressure gates already tested on contraction. This is a different fixed signal pool, not a newly invented indicator.',
        },
        'limitations': ['Already inspected fixed 3141 surviving mainboard history; no pristine holdout claim.',
            'Only nine dependent events. Closed-only association excludes censored open positions and unfilled opportunities.',
            'Source market aggregates inherit their pinned audits; no full feature/role mathematical re-audit.',
            'This pool already conditions on an index drawdown and prior strength; do not extrapolate to all stocks or regimes.',
            'Price-volume pressure does not identify investors, holdings or causal market leadership.',
            'No retrospective phase titles or final leader names used as entry features.'],
        'newEntryVariants': 0, 'newAccountRuns': 0, 'thresholdSearch': False, 'profitHurdles': None,
        'certified': False, 'targetAchieved': False}
    OUT.mkdir()
    write(OUT / 'plan.json', plan)
    print(json.dumps({'registered': relative(OUT / 'plan.json'), 'sha256': sha(OUT / 'plan.json'),
                      'signals': {p: s['signalCount'] for p, s in periods.items()}}, ensure_ascii=False))


def analyze():
    plan = read(OUT / 'plan.json')
    for pins in ('inputPins', 'codePins'):
        for path, expected in plan[pins].items():
            require(sha(ROOT / path) == expected, 'Frozen input changed: ' + path)
    require(not (OUT / 'diagnostic.json').exists(), 'Diagnostic already exists')
    source = read(COST / 'event-outcomes.json')
    loader = module('load-market-context-screen.py')
    event_helper = module('summarize-relative-repair-events.py')
    payload = {'schema': 'relative-repair-context-results-v1', 'createdAt': now(), 'planSha256': sha(OUT / 'plan.json'),
               'periods': {}, 'newEntryVariants': 0, 'newAccountRuns': 0, 'targetAchieved': False, 'certified': False}
    artifacts = {}
    for period, spec in plan['periods'].items():
        base = loader.load_baseline(period)
        market = {r['date']: r for r in base['marketRows']}
        calendar = sorted(market)
        signals = read(ROOT / spec['signalsPath'])
        evidence = read(ROOT / spec['evidencePath'])
        _, membership = event_helper.event_structure(evidence)
        require(evidence['schedules']['matchedControl'] == signals, 'Original schedule differs')
        days = {r['date']: r for r in evidence['dailyEvidence']}
        events = {r['eventId']: r for r in evidence['events']}
        cost_events = {r['eventId']: r for r in source['periods'][period]['events']}
        require(set(events) == set(cost_events), 'Event coverage differs')
        prior_path = OUT / period / 'prior-pressure.json.gz'
        binding_path = OUT / period / 'pressure-binding.json'
        pressure = read(prior_path)
        binding = read(binding_path)
        # The independent pressure preparer certifies this exact saved window set.
        require(binding.get('verification', {}).get('passed') is True, 'Pressure binding did not pass')
        require(binding.get('planSha256') == payload['planSha256'], 'Pressure plan differs')
        require(binding.get('output', {}).get('sha256') == sha(prior_path), 'Pressure output differs')
        require(set(pressure['rows']) == {s['signalId'] for s in signals}, 'Pressure opportunity coverage differs')
        outcomes = {s['signalId']: {} for s in signals}
        for g, paths in spec['accounts'].items():
            account = read(ROOT / paths['result'])
            require(account['research']['identity']['signalSchedule'] == signals, 'Account opportunity schedule differs')
            opportunities = {r['signalId']: r for r in account['research']['opportunities']}
            require(set(opportunities) == set(outcomes), 'Opportunity coverage differs')
            for sid, op in opportunities.items():
                outcomes[sid][g] = {'state': 'notEntered', 'opportunityStatus': op['status'],
                                    'filledQuantity': op.get('filledQuantity', 0)}
            joined = set()
            for event_id, event in cost_events.items():
                original = event['groups'][g]
                for kind, rows in (('closed', original['closedEpisodes']), ('open', original['openPositions'])):
                    for row in rows:
                        sid = row['signalId']
                        require(sid in outcomes and sid not in joined and membership[sid] == event_id, 'Episode mapping differs')
                        require(opportunities[sid].get('filledQuantity', 0) > 0, 'Episode without fill')
                        joined.add(sid)
                        rec = row['record']
                        item = outcomes[sid][g]
                        item.update(state=kind, entryDate=rec['entryDate'])
                        if kind == 'closed':
                            item.update(exitDate=rec['exitDate'], netReturnPct=row['netReturnPct'],
                                        pnlAmount=row['pnlAmount'], holdingSessions=row['holdingSessions'])
                        else:
                            item.update(unrealizedPnlAmount=row['unrealizedPnlAmount'],
                                        realizedPnlWhileOpen=row['realizedPnlWhileOpen'],
                                        markStaleAtCutoff=row['markStaleAtCutoff'])
            require(joined == {sid for sid, op in opportunities.items() if op.get('filledQuantity', 0) > 0}, 'Lost filled entry')
        rows = []
        for signal in signals:
            sid, day = signal['signalId'], signal['signalDate']
            eid = membership[sid]
            features, supporting = context_values(signal, market[day], days[day], events[eid], pressure['rows'][sid], calendar)
            rows.append({**signal, 'period': period, 'eventId': eid, 'features': features,
                         'supportingEvidence': supporting, 'outcomes': outcomes[sid]})
        row_path = OUT / period / 'context-rows.json.gz'
        write(row_path, rows)
        summaries = []
        for eid, event in events.items():
            selected = [r for r in rows if r['eventId'] == eid]
            for g in GROUPS:
                summary = summarize_event(selected, event, g)
                original = cost_events[eid]['groups'][g]
                for key, old in [('opportunities', 'opportunities'), ('actualEntries', 'actualEntries'), ('closed', 'closedCount'), ('open', 'openCountAtCutoff')]:
                    require(summary[key] == original[old], 'Event outcome count mismatch')
                for key in ('closedNetPnlAmount', 'openUnrealizedPnlAmount'):
                    require(math.isclose(summary[key], original[key], abs_tol=1e-7), 'Event PnL mismatch')
                summaries.append(summary)
        payload['periods'][period] = {'opportunities': len(rows), 'eventCount': len(events),
            'uniqueSignalDates': len({r['signalDate'] for r in rows}),
            'contextMissingness': {f: distribution([r['features'][f] for r in rows]) for f in FEATURES},
            'events': summaries, 'associations': associations(rows, list(events)),
            'marketBinding': spec['marketBinding']}
        for path in (row_path, prior_path, binding_path):
            artifacts[relative(path)] = sha(path)
        print(json.dumps({'period': period, 'joined': len(rows), 'events': len(events)}, ensure_ascii=False), flush=True)
    payload['artifacts'] = artifacts
    write(OUT / 'diagnostic.json', payload)
    print(json.dumps({'diagnostic': relative(OUT / 'diagnostic.json'), 'sha256': sha(OUT / 'diagnostic.json')}))


def self_test():
    require(ranks([10, 10, 20]) == [1.5, 1.5, 3], 'Average ties')
    require(spearman([{'x': x, 'y': -x} for x in (1, 2, 3)])['rho'] == -1, 'Inverse ranks')
    require(spearman([{'x': 1, 'y': x} for x in (1, 2, 3)])['rho'] is None, 'Constant unknown')
    require(distribution([None, 1, 2, 3])['q25'] == 1.5, 'Quantiles or unknown handling')
    rows = [{'signalDate': day, 'signalId': str(i), 'eventId': 'e',
             'features': {'breadth20Pct': 30, 'pressure20Prior': i},
             'outcomes': {'control10': {'state': state, 'netReturnPct': value}}}
            for i, (day, state, value) in enumerate([('d1', 'closed', 10), ('d1', 'closed', -4),
                                                    ('d2', 'open', 99), ('d3', 'notEntered', 0)])]
    points = points_for(rows, 'breadth20Pct', 'control10')
    require(len(points) == 1 and points[0]['y'] == 3 and points[0]['closedTrades'] == 2, 'Date aggregation/censoring')
    require(len(points_for(rows, 'pressure20Prior', 'control10')) == 2, 'Stock observation unit')
    signal = {'symbol': 's', 'signalDate': '2022-01-05'}
    market = {'date': signal['signalDate'], 'price20': {'positiveCount': 1, 'zeroCount': 0,
        'negativeCount': 3, 'evaluableCount': 4, 'positiveReturn20Breadth': .25, 'coverage': 1},
        'matched5': {'available': False, 'pairedCount': 0, 'matchedBreadthChange5': None},
        'amount': {'medianRelativeToOwnPrior20': .8, 'evaluableCount': 4},
        'qCounts': {'true': 0, 'false': 4, 'unknown': 0},
        'yesterdayCohort': {'eligibleCount': 0, 'continuedCount': 0, 'observedFailureCount': 0,
            'unknownCount': 0, 'previousCohortAvailable': True, 'previousDate': '2022-01-04',
            'observedCoverage': None, 'continuationLowerBound': None, 'continuationUpperBound': None}}
    ed = {'date': signal['signalDate'], 'eventId': 'e', 'age': 1, 'benchmarkClose': 98}
    event = {'eventId': 'e', 'peakDate': '2021-12-30', 'triggerDate': '2022-01-04', 'peakClose': 100}
    pressure = {'symbol': 's', 'signalDate': signal['signalDate'], 'asOfDate': '2022-01-04',
        'previousCalendarDate': '2022-01-04', 'pressure5': .1, 'pressure20': None}
    values, _ = context_values(signal, market, ed, event, pressure, ['2022-01-04', '2022-01-05'])
    require(values['breadth20Pct'] == 25 and values['eventAgeSessions'] == 1
            and math.isclose(values['benchmarkDrawdownFromAnchorPct'], -2), 'Manual context values')
    require(values['qContinuationPct'] is None and values['matchedBreadthChange5Pp'] is None
            and values['pressureAccelerationPrior'] is None, 'Unknown context must not become zero')
    invalid = dict(pressure, asOfDate='2022-01-05', previousCalendarDate='2022-01-05')
    try:
        context_values(signal, market, ed, event, invalid, ['2022-01-04', '2022-01-05'])
    except ValueError:
        pass
    else:
        raise ValueError('Signal-day pressure was accepted')
    print(json.dumps({'passed': True, 'checks': ['average ties', 'inverse ranks', 'constant undefined',
          'unknown-preserving quartiles', 'unique date aggregation', 'open/unfilled excluded', 'stock unit retained',
          'manual context values', 'empty cohort/missing context remain unknown', 'signal-day pressure rejected']}))


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'analyze', 'self-test'))
    args = parser.parse_args()
    {'register': register, 'analyze': analyze, 'self-test': self_test}[args.mode]()
