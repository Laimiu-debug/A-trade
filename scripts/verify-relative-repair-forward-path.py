"""Audit fixed-signal forward close paths independently of the analyzer.

Reads the saved, source-bound 60-session windows only. Never loads the full raw
market, calls an account engine, or imports the analyzer's calculations.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/relative-repair-forward-path-v1'
FEATURES = ('breadth20Pct', 'matchedBreadthChange5Pp', 'medianAmountRatio20',
            'qContinuationPct', 'benchmarkDrawdownFromAnchorPct', 'eventAgeSessions',
            'pressure20Prior', 'pressureAccelerationPrior')
STOCK_FEATURES = set(FEATURES[-2:])
HORIZONS = (5, 10, 20, 60)
COHORTS = ('all', 'entered', 'closed', 'open', 'notEntered')
PERIOD_COUNTS = {'2022-2023': 565, '2024-2025': 624}
METRICS = ('returnPct', 'closeMaxGainPct', 'closeMaxLossPct')


def require(value, message):
    if not value:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def equal(actual, expected, label):
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


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def local(name):
    p = Path(name)
    require(not p.is_absolute() and '..' not in p.parts, 'Unsafe repository-relative path')
    p = (ROOT / p).resolve()
    require(p.is_relative_to(ROOT.resolve()), 'Pinned path leaves repository')
    return p


def mean(values):
    return math.fsum(values) / len(values) if values else None


def distribution(values):
    require(all(v is None or finite(v) for v in values), 'Invalid distribution value')
    ordered = sorted(v for v in values if v is not None)

    def q(fraction):
        if not ordered:
            return None
        position = fraction * (len(ordered) - 1)
        before, after = math.floor(position), math.ceil(position)
        if before == after:
            return ordered[before]
        return ordered[before] * (after - position) + ordered[after] * (position - before)

    return {'count': len(values), 'known': len(ordered), 'unknown': len(values) - len(ordered),
            'minimum': ordered[0] if ordered else None, 'q25': q(.25), 'median': q(.5),
            'q75': q(.75), 'maximum': ordered[-1] if ordered else None, 'mean': mean(ordered)}


def average_ranks(values):
    return [1 + sum(v < item for v in values) + .5 * (sum(v == item for v in values) - 1)
            for item in values]


def spearman(points):
    paired = [p for p in points if finite(p['x']) and finite(p['y'])]
    answer = {'rows': len(points), 'paired': len(paired),
              'unknownExcluded': len(points) - len(paired), 'rho': None, 'reason': None}
    if len(paired) < 3:
        answer['reason'] = 'fewerThanThreePairs'
        return answer
    ranks_x = average_ranks([p['x'] for p in paired])
    ranks_y = average_ranks([p['y'] for p in paired])
    centre = (len(paired) + 1) / 2
    dx, dy = [v - centre for v in ranks_x], [v - centre for v in ranks_y]
    sxx, syy = math.fsum(v * v for v in dx), math.fsum(v * v for v in dy)
    if not sxx or not syy:
        answer['reason'] = 'constantRankVariable'
    else:
        answer['rho'] = math.fsum(a * b for a, b in zip(dx, dy)) / math.sqrt(sxx * syy)
    return answer


def in_cohort(row, cohort):
    require(cohort in COHORTS and row['accountState'] in COHORTS[2:], 'Unknown cohort/account state')
    return (cohort == 'all' or (cohort == 'entered' and row['accountState'] in ('closed', 'open'))
            or row['accountState'] == cohort)


def select(rows, cohort, event_id=None):
    return [r for r in rows if in_cohort(r, cohort) and (event_id is None or r['eventId'] == event_id)]


def complete(row, horizon):
    return row['horizons'][str(horizon)]['status'] == 'complete'


def association_counts(rows, horizon):
    usable = [r for r in rows if complete(r, horizon)]
    return {'opportunities': len(rows), 'usablePaths': len(usable),
            'uniqueSignalDates': len({(r['eventId'], r['signalDate']) for r in usable}),
            'contributingEvents': len({r['eventId'] for r in usable})}


def points_for(rows, feature, horizon):
    """Rows have already been restricted to one cohort; null x remains present."""
    if feature in STOCK_FEATURES:
        return [{'eventId': r['eventId'], 'signalDate': r['signalDate'], 'signalId': r['signalId'],
                 'x': r['features'][feature], 'y': r['horizons'][str(horizon)]['returnPct'],
                 'opportunities': 1, 'usablePaths': 1} for r in rows if complete(r, horizon)]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['eventId'], row['signalDate'])].append(row)
    answer = []
    for (event_id, day), group in sorted(grouped.items()):
        usable = [r for r in group if complete(r, horizon)]
        if not usable:
            continue
        x = group[0]['features'][feature]
        require(all(r['features'][feature] == x for r in group), 'Same-date market feature mismatch')
        answer.append({'eventId': event_id, 'signalDate': day, 'x': x,
                       'y': mean([r['horizons'][str(horizon)]['returnPct'] for r in usable]),
                       'opportunities': len(group), 'usablePaths': len(usable)})
    return answer


def association(rows, feature, horizon, cohort, events):
    cohort_rows = select(rows, cohort)
    points = points_for(cohort_rows, feature, horizon)
    by_event, omissions = [], []
    for event_id in events:
        event_rows = [r for r in cohort_rows if r['eventId'] == event_id]
        omitted_rows = [r for r in cohort_rows if r['eventId'] != event_id]
        by_event.append({'eventId': event_id, **association_counts(event_rows, horizon),
                         **spearman([p for p in points if p['eventId'] == event_id])})
        omissions.append({'omittedEventId': event_id, **association_counts(omitted_rows, horizon),
                          **spearman([p for p in points if p['eventId'] != event_id])})
    return {'feature': feature, 'horizon': horizon, 'cohort': cohort,
            'observationUnit': 'opportunity' if feature in STOCK_FEATURES else 'signalDateMeanForwardReturn',
            **association_counts(cohort_rows, horizon), 'pooled': spearman(points),
            'byEvent': by_event, 'leaveOneEventOut': omissions, 'points': points}


def distribution_row(rows, cohort, horizon, event_id):
    selected = select(rows, cohort, event_id)
    horizons = [r['horizons'][str(horizon)] for r in selected]
    counts = Counter(h['status'] for h in horizons)
    require(set(counts) <= {'complete', 'censored', 'unavailable'}, 'Bad path status')
    return {'eventId': event_id, 'cohort': cohort, 'horizon': horizon,
            'opportunities': len(selected), 'uniqueSignalDates': len({(r['eventId'], r['signalDate']) for r in selected}),
            'states': {k: counts[k] for k in ('complete', 'censored', 'unavailable')},
            **{metric: distribution([h[metric] for h in horizons]) for metric in METRICS}}


def valid_close(bar):
    return isinstance(bar, dict) and finite(bar.get('close')) and bar['close'] > 0


def horizon_metrics(row, horizon):
    require(horizon in HORIZONS, 'Unregistered horizon')
    reference = row['referenceBar']
    dates, bars = row['windowDates'][:horizon], row['windowBars'][:horizon]
    require(len(dates) == len(bars), 'Misaligned horizon slots')
    issues = []
    if not valid_close(reference):
        issues.append((row['signalDate'], 'missingReferenceBar' if reference is None else 'invalidReferenceClose'))
    for date, bar in zip(dates, bars):
        if not valid_close(bar):
            issues.append((date, 'missingPathBar' if bar is None else 'invalidPathClose'))
    enough = len(dates) == horizon
    state = 'censored' if not enough else 'unavailable' if issues else 'complete'
    result = {'horizonSessions': horizon, 'observedSlots': len(dates),
              'targetDate': dates[horizon - 1] if enough else None,
              'observedEndDate': dates[-1] if dates else None,
              'calendarComplete': enough, 'referenceValid': valid_close(reference),
              'missingSlots': sum(bar is None for bar in bars),
              'invalidCloseSlots': sum(bar is not None and not valid_close(bar) for bar in bars),
              'firstIssueDate': issues[0][0] if issues else None,
              'firstIssueReason': issues[0][1] if issues else None,
              'status': state, **{metric: None for metric in METRICS}}
    if state == 'complete':
        base = reference['close']
        changes = [(bar['close'] - base) * 100 / base for bar in bars]
        result.update(returnPct=changes[-1], closeMaxGainPct=max([0.] + changes),
                      closeMaxLossPct=min([0.] + changes))
    return result


def verify_forward_evidence(forward, binding, signals, spec, plan, plan_sha, forward_path, period):
    require(forward['schema'] == 'relative-repair-forward-bars-v1', 'Forward bars schema')
    require(binding['schema'] == 'relative-repair-forward-binding-v1', 'Forward binding schema')
    require(binding['period'] == period and binding['planSha256'] == plan_sha, 'Forward binding plan/period')
    equal(binding['inputPins'], plan['inputPins'], 'Forward input pins')
    equal(binding['codePins'], plan['codePins'], 'Forward code pins')
    require(binding['output']['sha256'] == sha(forward_path)
            and local(binding['output']['path']) == forward_path.resolve(), 'Forward output binding')
    require(binding['signalCount'] == len(signals) and binding['signalScheduleSha256'] == digest(signals), 'Forward schedule binding')
    for source_key, binding_key in (('signalsPath', 'signalFileSha256'), ('sourcePath', 'sourceIdentityFileSha256'),
                                    ('contextRowsPath', 'contextRowsFileSha256')):
        equal(binding[binding_key], plan['inputPins'][spec[source_key]], 'Forward bound ' + source_key)
    equal(binding['sourceIdentity'], read(local(spec['sourcePath'])), 'Forward source identity')
    require(binding['accountsExecuted'] == 0 and binding['returnsCalculated'] is False
            and binding['outcomeFiltering'] is False and binding['certified'] is False, 'Forward preparation scope')
    checks = ('passed', 'savedBarsComparedWithSource', 'allSignalsRetainedInOrder', 'contextSignalIdentityMatched',
              'calendarSlotsNotCompressed', 'referenceUsesSignalDay', 'windowsExcludeSignalDay',
              'windowsRespectOriginalCutoff', 'calendarCoverageCountsIncludeMissingBars')
    require(all(binding['verification'].get(k) is True for k in checks), 'Forward source verification incomplete')
    calendar = forward['sourceCalendar']
    require(calendar == sorted(set(calendar)), 'Forward source calendar order/uniqueness')
    require(forward['sourceCalendarSha256'] == binding['sourceCalendarSha256'] == digest(calendar), 'Forward source calendar hash')
    require(forward['sourceDatasetFingerprint'] == binding['sourceDatasetFingerprint'], 'Forward fingerprint')
    require(forward['periodEnd'] == binding['periodEnd'] == spec['cutoff'], 'Forward cutoff')
    require(forward['horizons'] == list(HORIZONS) and forward['maxHorizon'] == 60, 'Forward horizons')
    require(spec['cutoff'] in calendar, 'Cutoff not in source calendar')
    original_ids = [s['signalId'] for s in signals]
    require(len(set(original_ids)) == len(signals)
            and [r['signalId'] for r in forward['rows']] == original_ids, 'Forward row identity/order')
    counts = {'signalCount': len(signals), 'referencePresent': 0, 'referenceMissing': 0,
              'referenceInvalidClose': 0, 'complete60SessionWindows': 0, 'truncatedWindows': 0,
              'emptyWindows': 0, 'availableCalendarSlots': 0, 'missingForwardBars': 0,
              'invalidForwardClose': 0, 'windowsWithMissingBars': 0,
              'horizonCalendarCoverage': {str(h): {'sufficient': 0, 'insufficient': 0} for h in HORIZONS}}
    for signal, row in zip(signals, forward['rows']):
        for key in ('signalId', 'symbol', 'signalDate'):
            equal(row[key], signal[key], 'Forward identity ' + key)
        require(signal['signalDate'] in calendar and signal['signalDate'] <= spec['cutoff'], 'Signal outside source calendar/cutoff')
        dates = [d for d in calendar if signal['signalDate'] < d <= spec['cutoff']][:60]
        equal(row['windowDates'], dates, 'Forward strict calendar window')
        require(row['availableSessions'] == len(dates) and len(row['windowBars']) == len(dates), 'Forward slot count')
        require(row['sourceDatasetFingerprint'] == forward['sourceDatasetFingerprint'], 'Forward row fingerprint')
        require(row['windowSha256'] == digest(row['windowBars']), 'Forward saved window hash')
        reference = row['referenceBar']
        require(reference is None or reference['date'] == signal['signalDate'], 'Reference bar is not T')
        for day, bar in zip(dates, row['windowBars']):
            require(bar is None or bar['date'] == day, 'Forward bar date mismatch/compressed slots')
        counts['referencePresent' if reference is not None else 'referenceMissing'] += 1
        counts['referenceInvalidClose'] += reference is not None and not valid_close(reference)
        counts['complete60SessionWindows' if len(dates) == 60 else 'truncatedWindows'] += 1
        counts['emptyWindows'] += not dates
        counts['availableCalendarSlots'] += len(dates)
        missing = sum(b is None for b in row['windowBars'])
        counts['missingForwardBars'] += missing
        counts['invalidForwardClose'] += sum(b is not None and not valid_close(b) for b in row['windowBars'])
        counts['windowsWithMissingBars'] += missing > 0
        for h in HORIZONS:
            counts['horizonCalendarCoverage'][str(h)]['sufficient' if len(dates) >= h else 'insufficient'] += 1
    equal(binding['counts'], counts, 'Forward binding counts')
    return counts


def verify(directory):
    started = time.perf_counter()
    output = directory / 'validation.json'
    require(not output.exists(), 'Refusing to overwrite validation.json')
    plan_path, summary_path = directory / 'plan.json', directory / 'summary.json'
    plan, summary = read(plan_path), read(summary_path)
    plan_sha = sha(plan_path)
    require(plan['schema'] == 'relative-repair-forward-path-v1'
            and summary['schema'] == 'relative-repair-forward-path-summary-v1', 'Wrong experiment schema')
    pinned = {}
    for category in ('inputPins', 'codePins'):
        require(isinstance(plan[category], dict) and plan[category], 'Empty pin category')
        for name, expected in plan[category].items():
            require(sha(local(name)) == expected, 'Frozen input changed: ' + name)
            pinned[name] = expected
    require(summary['planSha256'] == plan_sha, 'Summary plan binding')
    equal(plan['horizons'], list(HORIZONS), 'Registered horizons')
    equal(plan['cohorts'], list(COHORTS), 'Registered cohorts')
    require(plan['maxHorizon'] == 60 and plan['primaryHorizon'] == 10, 'Registered primary horizon')
    require(set(plan['features']) == set(FEATURES), 'Registered feature set')
    for name in ('newEntryVariants', 'newAccountRuns'):
        require(plan[name] == summary[name] == 0, 'Unexpected new trading research')
    require(plan['thresholdSearch'] is False and plan['profitHurdles'] is None, 'Unexpected tuning/hurdle')
    require(plan['certified'] is False and plan['targetAchieved'] is False
            and summary['certified'] is False and summary['targetAchieved'] is False, 'Unsupported certification')
    require(set(plan['periods']) == set(summary['periods']) == set(PERIOD_COUNTS), 'Missing period')
    parent_base = 'artifacts/strategy-search/relative-repair-context-diagnostic-v1/'
    parent_required = [parent_base + filename for filename in ('plan.json', 'diagnostic.json', 'validation.json')]
    require(set(parent_required) <= set(plan['inputPins']), 'Parent proof pins missing')
    parent_plan, parent_validation = read(local(parent_required[0])), read(local(parent_required[2]))
    require(parent_validation['passed'] is True and parent_validation['planSha256'] == pinned[parent_required[0]]
            and parent_validation['diagnosticSha256'] == pinned[parent_required[1]], 'Parent proof chain mismatch')
    expected_artifacts = {(directory / period / filename).relative_to(ROOT).as_posix()
                          for period in PERIOD_COUNTS
                          for filename in ('forward-bars.json.gz', 'forward-binding.json', 'path-rows.json.gz')}
    require(set(summary['artifacts']) == expected_artifacts, 'Summary artifact coverage')
    for name, expected in summary['artifacts'].items():
        require(sha(local(name)) == expected, 'Summary artifact changed: ' + name)
    report = {'schema': 'relative-repair-forward-path-validation-v1', 'passed': True, 'status': 'passed',
              'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(Path(__file__)),
              'planSha256': plan_sha, 'summarySha256': sha(summary_path), 'inputPins': pinned,
              'artifactPins': summary['artifacts'], 'periods': {}, 'checks': [], 'certified': False,
              'targetAchieved': False, 'newAccountRuns': 0, 'newEntryVariants': 0}
    all_events = []
    for period, expected_count in PERIOD_COUNTS.items():
        spec = plan['periods'][period]
        require(spec['signalCount'] == expected_count, 'Period signal count')
        require(spec['cutoff'] == ('2023-12-29' if period == '2022-2023' else '2025-12-31'), 'Period cutoff changed')
        for field in ('signalsPath', 'sourcePath', 'contextRowsPath'):
            require(spec[field] in plan['inputPins'], 'Unpinned period source')
        equal(spec['signalsPath'], parent_plan['periods'][period]['signalsPath'], 'Original signal source')
        require(parent_validation['artifactPins'][spec['contextRowsPath']] == pinned[spec['contextRowsPath']], 'Context parent validation hash')
        signals, contexts = read(local(spec['signalsPath'])), read(local(spec['contextRowsPath']))
        require(len(signals) == len(contexts) == expected_count, 'Original signal/context counts')
        for signal, context in zip(signals, contexts):
            for field, value in signal.items():
                equal(context[field], value, 'Original context identity ' + field)
            require(context['period'] == period and set(context['features']) == set(FEATURES), 'Context period/features')
        forward_path = directory / period / 'forward-bars.json.gz'
        forward, binding = read(forward_path), read(directory / period / 'forward-binding.json')
        binding_counts = verify_forward_evidence(forward, binding, signals, spec, plan, plan_sha, forward_path, period)
        path_rows = read(directory / period / 'path-rows.json.gz')
        require(len(path_rows) == expected_count, 'Lost path rows')
        events = list(dict.fromkeys(row['eventId'] for row in contexts))
        require(len(events) == (4 if period == '2022-2023' else 5), 'Lost original event')
        all_events.extend(events)
        for context, raw, path_row in zip(contexts, forward['rows'], path_rows):
            state = context['outcomes']['control10']['state']
            require(state in ('closed', 'open', 'notEntered'), 'Unknown original account state')
            expected = {key: context[key] for key in ('signalId', 'symbol', 'signalDate', 'eventId', 'period', 'features')}
            expected.update(accountState=state, originalOutcome=context['outcomes']['control10'],
                            referenceClose=raw['referenceBar']['close'] if valid_close(raw['referenceBar']) else None,
                            availableSessions=len(raw['windowDates']),
                            horizons={str(h): horizon_metrics(raw, h) for h in HORIZONS})
            equal(path_row, expected, 'Forward path row ' + context['signalId'])
            for h in HORIZONS:
                result = path_row['horizons'][str(h)]
                if result['status'] == 'complete':
                    require(result['closeMaxGainPct'] >= 0 and result['closeMaxLossPct'] <= 0
                            and result['closeMaxLossPct'] <= result['returnPct'] <= result['closeMaxGainPct'],
                            'Path extrema must include reference zero and terminal close')
                else:
                    require(all(result[metric] is None for metric in METRICS), 'Incomplete path contains return')
        actual = summary['periods'][period]
        require(set(actual) == {'opportunities', 'eventIds', 'cohortCounts', 'distributions', 'associations', 'sourceBinding'}, 'Period summary fields')
        require(actual['opportunities'] == expected_count, 'Summary opportunities')
        equal(actual['eventIds'], events, 'All original event identities')
        expected_cohorts = {cohort: len(select(path_rows, cohort)) for cohort in COHORTS}
        equal(actual['cohortCounts'], expected_cohorts, 'Five cohort counts')
        require(expected_cohorts['all'] == expected_cohorts['entered'] + expected_cohorts['notEntered']
                and expected_cohorts['entered'] == expected_cohorts['closed'] + expected_cohorts['open'], 'Cohort partition bridge')
        equal(actual['sourceBinding'], binding, 'Full source binding in summary')
        expected_distributions = [distribution_row(path_rows, cohort, h, event)
                                  for event in [None] + events for cohort in COHORTS for h in HORIZONS]
        equal(actual['distributions'], expected_distributions, 'All event/cohort/horizon distributions')
        expected_order = [(cohort, h, f) for cohort in COHORTS for h in HORIZONS for f in FEATURES]
        require([(a['cohort'], a['horizon'], a['feature']) for a in actual['associations']] == expected_order,
                '160 associations not retained in fixed order')
        associations_report = []
        for item in actual['associations']:
            cohort, h, f = item['cohort'], item['horizon'], item['feature']
            expected = association(path_rows, f, h, cohort, events)
            equal(item, expected, f'Association {period}/{cohort}/{h}/{f}')
            associations_report.append({'cohort': cohort, 'horizon': h, 'feature': f,
                                        'opportunities': expected['opportunities'], 'usablePaths': expected['usablePaths'],
                                        'points': expected['pooled']['rows'], 'paired': expected['pooled']['paired'],
                                        'eventsRetained': len(events), 'omissions': len(events)})
        report['periods'][period] = {'rows': expected_count, 'events': events, 'cohortCounts': expected_cohorts,
            'sourceBindingCounts': binding_counts, 'horizonStatesAll': {
                str(h): distribution_row(path_rows, 'all', h, None)['states'] for h in HORIZONS},
            'distributionRows': len(expected_distributions), 'associations': associations_report}
        print(json.dumps({'period': period, 'passed': True, 'rows': expected_count,
                          'distributions': len(expected_distributions), 'associations': len(associations_report)}), flush=True)
    require(len(all_events) == len(set(all_events)) == 9, 'All nine events must remain')
    report['checks'] = ['Frozen input/code/output byte hashes and parent context audit chain',
        'All 1189 original identities, order, features and control10 outcomes retained',
        'Source-bound T reference and uncompressed T+1..T+60 calendar slots, original cutoff retained',
        'Positive finite close only; no OHLC/volume gate added',
        'Four horizons recomputed independently including T zero in close extrema',
        'Censored before unavailable; all anomaly counts/reasons retained; incomplete paths remain null',
        'Five cohort partition and all 220 period/event distribution rows independently recomputed',
        'All 320 feature/horizon/cohort associations, points, nine-event coverage and event omissions independently recomputed',
        'Market date means use available-path denominator and retain total stock opportunity count',
        'Null predictors remain points and are excluded only from complete rank pairs; ties averaged']
    report['scope'] = {'signals': 1189, 'events': 9, 'pathHorizons': len(HORIZONS), 'primaryAssociations': 16,
        'allAssociations': 320, 'distributionRows': 220, 'accountRunsExecuted': 0,
        'fullRawMarketReloaded': False, 'analyzerCalculationFunctionsImported': False,
        'inheritedProof': 'Pinned context validation supplies prior feature and original account-state arithmetic; source binding supplies raw-bar authenticity.',
        'limitations': ['Gross unadjusted close paths are descriptive outcomes, not executable trades or portfolio returns.',
            'All histories previously inspected; cohort selections and overlapping windows are not independent tests.',
            'No future-state cohort is an entry condition; no threshold, horizon, or feature selected by this audit.',
            'Only saved-window consistency and arithmetic audited here; full raw-source authenticity inherits preparer proof.']}
    report['elapsedSeconds'] = time.perf_counter() - started
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'output': output.relative_to(ROOT).as_posix(), 'sha256': sha(output),
                      'elapsedSeconds': report['elapsedSeconds']}, ensure_ascii=False))


def self_test():
    dates = [f'2020-01-{i:02d}' for i in range(2, 8)]
    window = {'signalDate': '2020-01-01', 'referenceBar': {'close': 100},
              'windowDates': dates, 'windowBars': [{'close': v} for v in (110, 90, 120, 95, 105, 900)]}
    answer = horizon_metrics(window, 5)
    equal({k: answer[k] for k in METRICS}, {'returnPct': 5., 'closeMaxGainPct': 20., 'closeMaxLossPct': -10.},
          'Manual five-session path')
    require(answer['targetDate'] == dates[4] and answer['status'] == 'complete', 'Horizon target')
    short = horizon_metrics(window, 10)
    require(short['status'] == 'censored' and short['targetDate'] is None and short['observedEndDate'] == dates[-1]
            and all(short[k] is None for k in METRICS), 'Incomplete horizon cannot contain returns')
    upward = dict(window, windowBars=[{'close': v, 'volume': 0, 'high': 0} for v in (101, 102, 103, 104, 105, 106)])
    require(horizon_metrics(upward, 5)['closeMaxLossPct'] == 0, 'Positive path must include reference zero')
    downward = dict(window, windowBars=[{'close': v} for v in (99, 98, 97, 96, 95, 94)])
    require(horizon_metrics(downward, 5)['closeMaxGainPct'] == 0, 'Negative path must include reference zero')
    require(horizon_metrics(upward, 5)['status'] == 'complete', 'OHLC/volume must not invalidate close')
    missing = dict(window, windowBars=[window['windowBars'][0], None] + window['windowBars'][2:])
    for h, status in ((5, 'unavailable'), (10, 'censored')):
        result = horizon_metrics(missing, h)
        require(result['status'] == status and result['missingSlots'] == 1 and result['invalidCloseSlots'] == 0
                and result['firstIssueDate'] == dates[1] and result['firstIssueReason'] == 'missingPathBar'
                and all(result[k] is None for k in METRICS), 'Missing slot and censoring precedence')
    invalid = dict(window, windowBars=[{'close': False}] + window['windowBars'][1:])
    invalid_result = horizon_metrics(invalid, 5)
    require(invalid_result['status'] == 'unavailable' and invalid_result['invalidCloseSlots'] == 1
            and invalid_result['firstIssueReason'] == 'invalidPathClose', 'Invalid close is not missing/zero')
    for ref, reason in ((None, 'missingReferenceBar'), ({'close': 0}, 'invalidReferenceClose'),
                        ({'close': math.nan}, 'invalidReferenceClose')):
        result = horizon_metrics(dict(missing, referenceBar=ref), 10)
        require(result['status'] == 'censored' and result['referenceValid'] is False
                and result['missingSlots'] == 1 and result['firstIssueDate'] == window['signalDate']
                and result['firstIssueReason'] == reason, 'Reference reason takes precedence while censored')
    changed = dict(window, windowBars=window['windowBars'][:5] + [{'close': .01}])
    equal(horizon_metrics(changed, 5), answer, 'T+6 must not affect five-session path')
    equal(average_ranks([8, 2, 8, 5]), [3.5, 1, 3.5, 2], 'Tied-rank hand calculation')
    equal(spearman([{'x': x, 'y': y} for x, y in [(1, 10), (1, 10), (3, 30)]])['rho'], 1., 'Tied Spearman')
    require(spearman([{'x': 2, 'y': i} for i in range(3)])['reason'] == 'constantRankVariable', 'Constant undefined')
    require(spearman([{'x': None, 'y': 1}, {'x': 2, 'y': 3}])['paired'] == 1, 'Unknown excluded')
    equal(distribution([None, 0, 2, 4, 6]), {'count': 5, 'known': 4, 'unknown': 1, 'minimum': 0,
          'q25': 1.5, 'median': 3., 'q75': 4.5, 'maximum': 6, 'mean': 3.}, 'Manual quartiles')
    rows = []
    for i, (day, event, state, status, value) in enumerate([
            ('d1', 'e1', 'closed', 'complete', 12), ('d1', 'e1', 'notEntered', 'complete', -4),
            ('d1', 'e1', 'open', 'unavailable', None), ('d2', 'e1', 'notEntered', 'censored', None),
            ('d3', 'e2', 'closed', 'complete', -2)]):
        rows.append({'signalId': str(i), 'signalDate': day, 'eventId': event, 'accountState': state,
                     'features': {'breadth20Pct': 50, 'pressure20Prior': i if i != 4 else None},
                     'horizons': {'10': {'status': status, 'returnPct': value,
                         'closeMaxGainPct': None if value is None else max(value, 0),
                         'closeMaxLossPct': None if value is None else min(value, 0)}}})
    equal({cohort: len(select(rows, cohort)) for cohort in COHORTS},
          {'all': 5, 'entered': 3, 'closed': 2, 'open': 1, 'notEntered': 2}, 'Five cohort membership')
    points = points_for(rows, 'breadth20Pct', 10)
    require(len(points) == 2 and points[0]['y'] == 4 and points[0]['opportunities'] == 3
            and points[0]['usablePaths'] == 2, 'Available-only same-date mean and denominator')
    individual = points_for(rows, 'pressure20Prior', 10)
    require(len(individual) == 3 and individual[-1]['x'] is None and spearman(individual)['paired'] == 2,
            'Unknown feature retained as point but excluded as pair')
    result = association(rows, 'breadth20Pct', 10, 'all', ['e1', 'e2', 'empty'])
    require(result['byEvent'][-1]['opportunities'] == 0 and result['byEvent'][-1]['rho'] is None,
            'Empty event must remain')
    require(result['leaveOneEventOut'][0]['opportunities'] == 1
            and result['leaveOneEventOut'][0]['paired'] == 1, 'Event omission denominator')
    summary = distribution_row(rows, 'all', 10, None)
    equal(summary['states'], {'complete': 3, 'censored': 1, 'unavailable': 1}, 'Separate path states')
    require(summary['returnPct']['count'] == 5 and summary['returnPct']['known'] == 3, 'Null denominators preserved')
    for wrong, expected in [(0, None), (False, 0), ({'a': 1}, {'a': 1, 'b': None}), ([1, 2], [2, 1])]:
        try:
            equal(wrong, expected, 'Intentional mutation')
        except ValueError:
            pass
        else:
            raise ValueError('Mutation accepted')
    print(json.dumps({'passed': True, 'checks': ['manual five-session path', 'horizon target/censoring',
        'zero included in rising/falling path extrema', 'OHLC/volume not validity gates',
        'missing slot retained', 'invalid close distinguished', 'censoring precedence with issue flags',
        'missing/invalid reference reason precedence', 'future outside horizon excluded',
        'tie ranks', 'tie Spearman', 'constant unknown', 'null pairs',
        'manual quartiles', 'five cohorts', 'available-only date denominator', 'null feature retained',
        'empty event retained', 'event omission', 'censored/missing states', 'null/bool/order/key mutation rejection']}))


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        verify(args.directory.resolve())
