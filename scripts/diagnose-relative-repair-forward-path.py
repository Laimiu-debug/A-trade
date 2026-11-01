"""Frozen all-opportunity close paths; research observations, never account returns."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'relative-repair-context-diagnostic-v1'
OUT = BASE / 'relative-repair-forward-path-v1'
PERIODS = ('2022-2023', '2024-2025')
HORIZONS = (5, 10, 20, 60)
COHORTS = ('all', 'entered', 'closed', 'open', 'notEntered')
METRICS = ('returnPct', 'closeMaxGainPct', 'closeMaxLossPct')


def module(filename):
    spec = importlib.util.spec_from_file_location('_forward_' + filename.replace('-', '_'), ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


stats = module('diagnose-relative-repair-context.py')
FEATURES = stats.FEATURES


def require(test, message):
    if not test:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else Path(path).open(encoding='utf-8')) as f:
        return json.load(f)


def write(path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    with Path(path).open('xb') as f:
        if str(path).endswith('.gz'):
            with gzip.GzipFile(fileobj=f, mode='wb', mtime=0) as z:
                z.write(payload)
        else:
            f.write(payload)


def relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def verify_pins(plan):
    for category in ('inputPins', 'codePins'):
        for name, pin in plan[category].items():
            require(sha(ROOT / name) == pin, 'Changed pinned file: ' + name)


def close_value(bar):
    value = bar.get('close') if isinstance(bar, dict) else None
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def path_metrics(row, horizon):
    dates, bars = row['windowDates'][:horizon], row['windowBars'][:horizon]
    require(len(dates) == len(bars), 'Misaligned future slots')
    reference = close_value(row['referenceBar'])
    values = [close_value(b) for b in bars]
    missing = sum(b is None for b in bars)
    invalid = sum(b is not None and v is None for b, v in zip(bars, values))
    issue_date = issue_reason = None
    if reference is None:
        issue_date = row['signalDate']
        issue_reason = 'missingReferenceBar' if row['referenceBar'] is None else 'invalidReferenceClose'
    else:
        for day, bar, value in zip(dates, bars, values):
            if value is None:
                issue_date, issue_reason = day, 'missingPathBar' if bar is None else 'invalidPathClose'
                break
    calendar_complete = len(dates) == horizon
    status = 'censored' if not calendar_complete else 'unavailable' if issue_reason else 'complete'
    metrics = dict.fromkeys(METRICS)
    if status == 'complete':
        changes = [0.] + [100 * (v / reference - 1) for v in values]
        metrics.update(returnPct=changes[-1], closeMaxGainPct=max(changes), closeMaxLossPct=min(changes))
    return {'horizonSessions': horizon, 'observedSlots': len(dates),
        'targetDate': dates[-1] if calendar_complete else None,
        'observedEndDate': dates[-1] if dates else None,
        'calendarComplete': calendar_complete, 'referenceValid': reference is not None,
        'missingSlots': missing, 'invalidCloseSlots': invalid,
        'firstIssueDate': issue_date, 'firstIssueReason': issue_reason,
        'status': status, **metrics}


def in_cohort(row, cohort):
    require(cohort in COHORTS, 'Unknown cohort')
    return cohort == 'all' or (cohort == 'entered' and row['accountState'] in ('closed', 'open')) or row['accountState'] == cohort


def group_counts(rows, horizon):
    usable = [r for r in rows if r['horizons'][str(horizon)]['status'] == 'complete']
    return {'opportunities': len(rows), 'usablePaths': len(usable),
        'uniqueSignalDates': len({(r['eventId'], r['signalDate']) for r in usable}),
        'contributingEvents': len({r['eventId'] for r in usable})}


def points_for(rows, feature, horizon):
    usable = [r for r in rows if r['horizons'][str(horizon)]['status'] == 'complete']
    if FEATURES[feature][1] == 'stock':
        return [{'eventId': r['eventId'], 'signalDate': r['signalDate'], 'signalId': r['signalId'],
            'x': r['features'][feature], 'y': r['horizons'][str(horizon)]['returnPct'],
            'opportunities': 1, 'usablePaths': 1} for r in usable]
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['eventId'], r['signalDate']].append(r)
    result = []
    for (event, day), values in grouped.items():
        outcomes = [r['horizons'][str(horizon)]['returnPct'] for r in values
                    if r['horizons'][str(horizon)]['status'] == 'complete']
        if not outcomes:
            continue
        x = values[0]['features'][feature]
        require(all(r['features'][feature] == x for r in values), 'Same-day market feature differs')
        result.append({'eventId': event, 'signalDate': day, 'x': x, 'y': mean(outcomes),
            'opportunities': len(values), 'usablePaths': len(outcomes)})
    return result


def associations(rows, events):
    result = []
    for cohort in COHORTS:
        selected = [r for r in rows if in_cohort(r, cohort)]
        for horizon in HORIZONS:
            for feature in FEATURES:
                points = points_for(selected, feature, horizon)
                by_event, omissions = [], []
                for event in events:
                    subset = [r for r in selected if r['eventId'] == event]
                    by_event.append({'eventId': event, **group_counts(subset, horizon),
                        **stats.spearman([p for p in points if p['eventId'] == event])})
                    subset = [r for r in selected if r['eventId'] != event]
                    omissions.append({'omittedEventId': event, **group_counts(subset, horizon),
                        **stats.spearman([p for p in points if p['eventId'] != event])})
                result.append({'feature': feature, 'horizon': horizon, 'cohort': cohort,
                    'observationUnit': 'signalDateMeanForwardReturn' if FEATURES[feature][1] == 'market' else 'opportunity',
                    **group_counts(selected, horizon), 'pooled': stats.spearman(points),
                    'byEvent': by_event, 'leaveOneEventOut': omissions, 'points': points})
    return result


def distributions(rows, events):
    result = []
    for event in [None] + events:
        event_rows = rows if event is None else [r for r in rows if r['eventId'] == event]
        for cohort in COHORTS:
            selected = [r for r in event_rows if in_cohort(r, cohort)]
            for horizon in HORIZONS:
                windows = [r['horizons'][str(horizon)] for r in selected]
                states = Counter(w['status'] for w in windows)
                result.append({'eventId': event, 'cohort': cohort, 'horizon': horizon,
                    'opportunities': len(selected),
                    'uniqueSignalDates': len({(r['eventId'], r['signalDate']) for r in selected}),
                    'states': {k: states[k] for k in ('complete', 'censored', 'unavailable')},
                    **{k: stats.distribution([w[k] for w in windows]) for k in METRICS}})
    return result


def register():
    require(not OUT.exists(), 'Already registered; retain previous attempts')
    parent_plan, parent_audit = read(PARENT / 'plan.json'), read(PARENT / 'validation.json')
    require(parent_audit['passed'] and parent_audit['planSha256'] == sha(PARENT / 'plan.json'), 'Parent not validated')
    require(parent_audit['diagnosticSha256'] == sha(PARENT / 'diagnostic.json'), 'Parent result changed')
    parent_readout, parent_delivery = read(PARENT / 'readout.json'), read(PARENT / 'delivery-completion.json')
    require(parent_delivery['readoutSha256'] == sha(PARENT / 'readout.json'), 'Parent delivery differs')
    require(parent_delivery['inputPins'] == parent_readout['inputPins'], 'Parent pin chain differs')
    for name, pin in parent_readout['inputPins'].items():
        require(sha(PARENT / name) == pin, 'Parent evidence changed: ' + name)
    files = [PARENT / n for n in ('plan.json', 'diagnostic.json', 'validation.json', 'readout.json', 'delivery-completion.json')]
    periods = {}
    for period, cutoff in zip(PERIODS, ('2023-12-29', '2025-12-31')):
        spec = parent_plan['periods'][period]
        context = PARENT / period / 'context-rows.json.gz'
        require(parent_audit['artifactPins'][relative(context)] == sha(context), 'Context rows changed')
        signals = read(ROOT / spec['signalsPath'])
        contexts = read(context)
        require([r['signalId'] for r in signals] == [r['signalId'] for r in contexts], 'Signal sequence mismatch')
        files += [context, ROOT / spec['signalsPath'], ROOT / spec['sourcePath']]
        periods[period] = {k: spec[k] for k in ('signalsPath', 'sourcePath', 'signalCount')}
        periods[period].update(contextRowsPath=relative(context), cutoff=cutoff)
    code = dict(parent_plan['codePins'])
    for name in code:
        require(sha(ROOT / name) == code[name], 'Parent helper changed: ' + name)
    for name in ('scripts/diagnose-relative-repair-forward-path.py', 'scripts/prepare-relative-repair-forward-paths.py'):
        code[name] = sha(ROOT / name)
    plan = {'schema': 'relative-repair-forward-path-v1', 'registeredAt': datetime.now(timezone.utc).isoformat(),
        'previousGoalTurn': {'classification': 'progress', 'evidence': relative(PARENT / 'delivery-completion.json'),
                            'sha256': sha(PARENT / 'delivery-completion.json')},
        'inputPins': {relative(p): sha(p) for p in files}, 'codePins': code, 'periods': periods,
        'horizons': list(HORIZONS), 'maxHorizon': 60, 'primaryHorizon': 10, 'cohorts': list(COHORTS),
        'features': parent_plan['features'],
        'design': {
            'reference': 'Signal-day T close, descriptive price reference only; not a tradable entry or account simulation.',
            'paths': 'Exactly h source-exchange-calendar slots T+1..T+h, bounded by each source period cutoff.',
            'validity': 'Reference and every required close finite and positive; no extra volume or OHLC gate.',
            'censoring': 'Fewer than h calendar slots => censored; else missing/invalid close => unavailable; else complete. All three metrics null unless complete. Preserve issue flags also for censored windows.',
            'outcomes': 'Gross final-close return, and maximum/minimum of all close changes including reference zero. No fees, intraday highs/lows, fill claims or compulsory holding horizon.',
            'primary': 'All opportunities, h=10, both periods, all eight features in fixed order. Other horizons and post-account cohorts are diagnostics, never competing optimizations.',
            'cohorts': 'all; entered=closed+open; closed/open/notEntered inherited from original control10. These are ex-post outcomes, not permissible entry conditions.',
            'marketUnit': 'One event/signalDate with at least one complete path; equally average available stock path returns, then each date contributes once. Keep contributing stock counts.',
            'stockUnit': 'Each complete opportunity; all overlapping stock/calendar/event dependencies remain.',
            'statistics': 'Report all distributions and all feature/horizon/cohort Spearman associations, event rows and each omitted-event sensitivity. No fitted thresholds, significance claims, winner selection or hyperparameter search.',
            'availability': 'Each horizon retains its own availability denominator; do not infer that longer holding is better from unpaired cross-horizon summaries.',
        },
        'limitations': [item for item in parent_plan['limitations'] if 'Closed-only association' not in item] + [
            'Only nine dependent market events; all opportunity outcomes remain conditional on the existing signal pool.',
            'Previously viewed fixed surviving-stock history, not independent holdout data.',
            'Price paths omit fees, execution, account capacity and corporate-action adjustments; no attainable-profit claim.',
            'Cohorts are selected by future account states, and overlapping opportunities are not independent observations.',
            'Window unavailability and end-of-period censoring may change the observation population; retain all missing denominators.'
        ], 'newEntryVariants': 0, 'newAccountRuns': 0, 'thresholdSearch': False,
        'profitHurdles': None, 'certified': False, 'targetAchieved': False}
    OUT.mkdir()
    for period in PERIODS:
        (OUT / period).mkdir()
    write(OUT / 'plan.json', plan)
    print(json.dumps({'registered': relative(OUT / 'plan.json'), 'sha256': sha(OUT / 'plan.json')}), flush=True)


def analyze():
    plan = read(OUT / 'plan.json')
    plan_sha = sha(OUT / 'plan.json')
    verify_pins(plan)
    require(plan['horizons'] == list(HORIZONS) and plan['cohorts'] == list(COHORTS), 'Design changed')
    result, artifacts = {}, {}
    for period in PERIODS:
        spec = plan['periods'][period]
        contexts = read(ROOT / spec['contextRowsPath'])
        bars_path, binding_path = OUT / period / 'forward-bars.json.gz', OUT / period / 'forward-binding.json'
        bars, binding = read(bars_path), read(binding_path)
        require(binding['planSha256'] == plan_sha and binding['verification']['passed'], 'Unverified forward inputs')
        require(binding['output']['sha256'] == sha(bars_path), 'Forward bar file changed')
        require(binding['sourceIdentity'] == read(ROOT / spec['sourcePath']), 'Source identity changed')
        require([r['signalId'] for r in contexts] == [r['signalId'] for r in bars['rows']], 'Forward identity/order changed')
        calendar = bars['sourceCalendar']
        require(calendar == sorted(set(calendar)), 'Invalid calendar')
        indices = {day: i for i, day in enumerate(calendar)}
        paths = []
        for c, b in zip(contexts, bars['rows']):
            require(c['symbol'] == b['symbol'] and c['signalDate'] == b['signalDate'], 'Forward row identity differs')
            at = indices[c['signalDate']]
            dates = [day for day in calendar[at + 1:at + 61] if day <= spec['cutoff']]
            require(b['windowDates'] == dates and b['availableSessions'] == len(dates), 'Wrong future calendar window')
            require(len(b['windowBars']) == len(dates), 'Wrong future slot count')
            require(b['referenceBar'] is None or b['referenceBar']['date'] == c['signalDate'], 'Reference date differs')
            require(all(x is None or x['date'] == day for x, day in zip(b['windowBars'], dates)), 'Future bar date differs')
            state = c['outcomes']['control10']['state']
            require(state in ('closed', 'open', 'notEntered'), 'Unexpected account state')
            paths.append({k: c[k] for k in ('signalId', 'symbol', 'signalDate', 'eventId', 'period', 'features')})
            paths[-1].update(accountState=state, originalOutcome=c['outcomes']['control10'],
                referenceClose=close_value(b['referenceBar']), availableSessions=len(dates),
                horizons={str(h): path_metrics(b, h) for h in HORIZONS})
        events = list(dict.fromkeys(r['eventId'] for r in contexts))
        result[period] = {'opportunities': len(paths), 'eventIds': events,
            'cohortCounts': {cohort: sum(in_cohort(r, cohort) for r in paths) for cohort in COHORTS},
            'distributions': distributions(paths, events), 'associations': associations(paths, events),
            'sourceBinding': binding}
        path = OUT / period / 'path-rows.json.gz'
        write(path, paths)
        for item in (path, bars_path, binding_path):
            artifacts[relative(item)] = sha(item)
        print(json.dumps({'period': period, 'opportunities': len(paths), 'events': len(events),
                          'associations': len(result[period]['associations'])}), flush=True)
    verify_pins(plan)
    require(sha(OUT / 'plan.json') == plan_sha, 'Plan changed during analysis')
    summary = {'schema': 'relative-repair-forward-path-summary-v1',
        'createdAt': datetime.now(timezone.utc).isoformat(), 'planSha256': plan_sha,
        'periods': result, 'artifacts': artifacts, 'newAccountRuns': 0, 'newEntryVariants': 0,
        'certified': False, 'targetAchieved': False}
    write(OUT / 'summary.json', summary)
    print(json.dumps({'summarySha256': sha(OUT / 'summary.json')}), flush=True)


def self_test():
    days = [f'2020-01-{i:02d}' for i in range(2, 8)]
    row = {'signalDate': '2020-01-01', 'referenceBar': {'close': 100.},
           'windowDates': days, 'windowBars': [{'close': c} for c in (110., 90., 120., 95., 105., 200.)]}
    m = path_metrics(row, 5)
    require(m['status'] == 'complete' and math.isclose(m['returnPct'], 5.)
            and math.isclose(m['closeMaxGainPct'], 20.) and math.isclose(m['closeMaxLossPct'], -10.), 'Hand path failed')
    short = path_metrics(row, 10)
    require(short['status'] == 'censored' and all(short[k] is None for k in METRICS), 'Censoring failed')
    row['windowBars'][1] = None
    gap = path_metrics(row, 5)
    require(gap['status'] == 'unavailable' and gap['firstIssueDate'] == days[1] and gap['missingSlots'] == 1, 'Gap compressed')
    short = path_metrics(row, 10)
    require(short['status'] == 'censored' and short['missingSlots'] == 1, 'Censored issue lost')
    row['windowBars'] = [{'close': v, 'volume': 0} for v in (90., 90., 90., 90., 90., 90.)]
    m = path_metrics(row, 5)
    require(m['status'] == 'complete' and m['closeMaxGainPct'] == 0, 'Reference zero or extra volume gate')
    row['referenceBar'] = {'close': True}
    require(path_metrics(row, 5)['firstIssueReason'] == 'invalidReferenceClose', 'Boolean close accepted')
    row['referenceBar'] = None
    require(path_metrics(row, 5)['firstIssueReason'] == 'missingReferenceBar', 'Reference missing lost')
    row['referenceBar'] = {'close': 100.}
    row['windowBars'][0] = {'close': 0.}
    require(path_metrics(row, 5)['invalidCloseSlots'] == 1, 'Invalid close counted as missing')
    def fake(identifier, state, ret, feature):
        return {'signalId': identifier, 'eventId': 'e', 'signalDate': 't', 'accountState': state,
            'features': {'breadth20Pct': feature, 'pressure20Prior': feature},
            'horizons': {'10': {'status': 'complete' if ret is not None else 'censored', 'returnPct': ret}}}
    rows = [fake('a', 'closed', 10., .5), fake('b', 'notEntered', 30., .5), fake('c', 'open', None, .5)]
    points = points_for(rows, 'breadth20Pct', 10)
    require(len(points) == 1 and points[0]['y'] == 20. and points[0]['opportunities'] == 3 and points[0]['usablePaths'] == 2, 'Date mean denominator')
    require(len(points_for(rows, 'pressure20Prior', 10)) == 2, 'Stock point unit')
    require(group_counts(rows, 10) == {'opportunities': 3, 'usablePaths': 2, 'uniqueSignalDates': 1, 'contributingEvents': 1}, 'Coverage counts')
    require(sum(in_cohort(r, 'entered') for r in rows) == 2 and sum(in_cohort(r, 'all') for r in rows) == 3, 'Cohort partition')
    require(stats.spearman([{'x': .5, 'y': 1.}] * 3)['rho'] is None, 'Constant rank must be null')
    require(stats.distribution([None, 1., 3.])['median'] == 2., 'Unknown distribution handling')
    print(json.dumps({'passed': True, 'checks': 14, 'realSourceLoaded': False, 'newAccountRuns': 0}))


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('register', 'analyze', 'self-test'))
    mode = parser.parse_args().mode
    {'register': register, 'analyze': analyze, 'self-test': self_test}[mode]()
