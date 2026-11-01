"""Independent frozen-source, strength-feedback signal and saved-account audit.

The new signal builder and runner are never imported. Source identity and all
event arithmetic are independently reconstructed. Previously frozen independent
board-aware cash/share/fee audit helpers are reused under exact hashes.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter, deque
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from statistics import mean
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/strength-feedback-v1'
PARENT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
BOARD_REFERENCE = 'scripts/verify-board-coverage-reclaim.py'
BOARD_REFERENCE_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
PARENT_PLAN_SHA = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
PARENT_SOURCE_VALIDATION_SHA = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
REGISTERED_PLAN_SHA = None
PROTOCOL_SHA = '73f123858324bbd2f6d11bb3079e445d748b4cf1347512b9fb966cd9c17ca851'
SID = 'strengthFeedbackV1'
BASELINE_SID = 'leaderLagFollowersV1'
PREVIOUS = ROOT / 'artifacts/strategy-search/leader-lag-followers-v1'
LAG_REFERENCE = 'scripts/verify-leader-lag-followers.py'
LAG_REFERENCE_SHA = '3e0244282a82701dcc942f532673c4e17493543cfa335f9e2459da6a7a33333c'
PREVIOUS_PLAN_SHA = '91eace6b78d655fbeb3ff10abc4f4c69f9a61c273a1d9447dc3bc38788f3d161'
PREVIOUS_VALIDATION_SHA = 'bfce3184ad74b2cb10f2d0ec8777346566aa8d2a23c496d43518b9bf93977743'
PREVIOUS_INPUT_VALIDATION_SHA = 'e3269accef17c880c4ef69102b2632e2763648c6a5213435c469a712873aaf61'
PREVIOUS_DELIVERY_SHA = 'e28109bb88bcf6811716d417863a2200260038ac86b4cbefacc57b31868a93d2'
MODES = ('known', 'positive')
REPORT_MODES = ('baseline', 'known', 'positive')
BOARDS = ('main', 'chinext', 'star')
PERIODS = ('2022-2023', '2024-2025')
LABELS = {'baseline': '原始跟随', 'known': '状态可判定', 'positive': '强势延续过半'}


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


_LAG = None


def lag_reference():
    global _LAG
    if _LAG is None:
        _LAG = reference(LAG_REFERENCE, LAG_REFERENCE_SHA)
    return _LAG


def robust_majority(positive_count, flat, negative, unknown):
    require(all(type(value) is int and value >= 0 for value in (positive_count, flat, negative, unknown)),
            'Invalid four-state fixed population counts')
    size = positive_count + flat + negative + unknown
    if not size:
        return 'unknown'
    if 2 * positive_count > size:
        return 'pass'
    if 2 * (positive_count + unknown) <= size:
        return 'fail'
    return 'unknown'


def feedback_value(item, index):
    issue = lag_reference().window_reason(item, index, 6)
    if issue:
        return None, 'stock:' + issue
    ratio = item['bars'][index]['close'] / item['bars'][index - 5]['close']
    if not positive(ratio) or not finite(ratio - 1):
        return None, 'nonfiniteOrUnderflowFeedbackRatio'
    return ratio - 1, None


def source_signals_reference(signals, symbols, target):
    require(isinstance(signals, list), 'Original control schedule is not a list')
    fields = {'signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank'}
    ids, pairs, ranks = set(), set(), set()
    for row in signals:
        require(isinstance(row, dict) and set(row) == fields and row['strategyId'] == BASELINE_SID
                and type(row['signalId']) is str and row['signalId'].strip(),
                'Original five-field control identity changed')
        require(row['symbol'] in symbols and row['signalDate'] in target and type(row['originalRank']) is int
                and 1 <= row['originalRank'] <= 5, 'Original source signal outside frozen population/rank')
        identity, pair, rank = row['signalId'], (row['signalDate'], row['symbol']), (row['signalDate'], row['originalRank'])
        require(identity not in ids and pair not in pairs and rank not in ranks, 'Duplicate source signal identity/member/rank')
        ids.add(identity); pairs.add(pair); ranks.add(rank)
    require(signals == sorted(signals, key=lambda row: (row['signalDate'], row['originalRank'], row['symbol'], row['signalId'])),
            'Original control schedule order changed')


def reference_build(dataset, start, end, source_signals, on_members=None):
    """Rebuild feedback from frozen independent raw-window and rank arithmetic."""
    old = lag_reference()
    source = old.source_reference(dataset, start, end)
    calendar, symbols, indexed = source['calendar'], source['symbols'], source['indexed']
    source_signals_reference(source_signals, set(symbols), set(source['targetDates']))
    positions = {day: n for n, day in enumerate(calendar)}
    daily, contexts = [], {}
    day_states = {key: 0 for key in ('pass', 'fail', 'unknown')}
    for day in source['targetDates']:
        at = positions[day]
        frozen_at = at - 5
        frozen_day = calendar[frozen_at] if frozen_at >= 0 else None
        values, reasons = {}, Counter()
        for symbol in symbols:
            if frozen_at < 0:
                value, reason = None, 'insufficientCohortCalendarHistory'
            else:
                value = old.relative_value(indexed[symbol], indexed['__benchmark__'], frozen_at, 20)
                reason = old.relative_reason(indexed[symbol], indexed['__benchmark__'], frozen_at, 20)
            values[symbol] = value
            if reason is not None:
                reasons[reason] += 1
        ranks, population_count = old.rank_reference(values)
        population = {'count': population_count, 'sha256': digest([
            {'symbol': symbol, 'value': values[symbol], 'percentile': ranks[symbol]}
            for symbol in symbols if symbol in ranks])}
        members = []
        counts = {key: 0 for key in ('positive', 'flat', 'negative', 'unknown')}
        for symbol in symbols:
            if symbol not in ranks or ranks[symbol] < .90:
                continue
            value, reason = feedback_value(indexed[symbol], at)
            status = 'unknown' if value is None else 'positive' if value > 0 else 'negative' if value < 0 else 'flat'
            members.append({'symbol': symbol, 'relativeReturn20': values[symbol], 'percentile': ranks[symbol],
                            'returnSinceCohort': value, 'status': status, 'reason': reason})
            counts[status] += 1
        n = len(members)
        state = robust_majority(counts['positive'], counts['flat'], counts['negative'], counts['unknown'])
        reason = ('insufficientCohortCalendarHistory' if frozen_at < 0 else 'emptyFrozenCohort' if n == 0
                  else 'confirmedPositiveMajority' if state == 'pass' else 'positiveMajorityImpossible'
                  if state == 'fail' else 'ambiguousPositiveMajority')
        row = {'formationDate': day, 'cohortDate': frozen_day,
            'rankWindowDates': calendar[max(0, frozen_at - 20):frozen_at + 1] if frozen_at >= 0 else [],
            'feedbackWindowDates': calendar[max(0, frozen_at):at + 1], 'rankPopulation': population,
            'rankReasonCounts': dict(sorted(reasons.items())), 'cohortSize': n, 'feedbackCounts': counts,
            'positiveFractionLower': counts['positive'] / n if n else None,
            'positiveFractionUpper': (counts['positive'] + counts['unknown']) / n if n else None,
            'state': state, 'reason': reason, 'memberDigest': digest(members)}
        daily.append(row)
        contexts[day] = row
        day_states[state] += 1
        if on_members is not None:
            on_members(deepcopy({'version': 'strength-feedback-v1', 'formationDate': day, 'cohortDate': frozen_day,
                'rankPopulation': population, 'cohortSize': n, 'memberDigest': row['memberDigest'], 'members': members}))
        if len(daily) % 50 == 0:
            print(json.dumps({'phase': 'independent-feedback-days', 'date': day, 'days': len(daily)}), flush=True)
    schedules, opportunities = {mode: [] for mode in MODES}, []
    policy_counts = {mode: {status: 0 for status in ('included', 'excluded', 'unknown')} for mode in MODES}
    for original in source_signals:
        context = contexts[original['signalDate']]
        signal = {**deepcopy(original), 'strategyId': SID}
        decisions, signals = {}, {}
        for mode in MODES:
            state = context['state']
            admitted = state == 'pass' or (mode == 'known' and state == 'fail')
            status = 'unknown' if state == 'unknown' else 'included' if admitted else 'excluded'
            decisions[mode] = {'status': status, 'reason': context['reason'] if state == 'unknown' or mode == 'positive'
                              else 'knownFeedbackState'}
            signals[mode] = deepcopy(signal) if admitted else None
            policy_counts[mode][status] += 1
            if admitted:
                schedules[mode].append(deepcopy(signal))
        opportunities.append({**signal, 'formationId': original['signalId'], 'sourceStrategyId': BASELINE_SID,
            **{key: deepcopy(context[key]) for key in ('formationDate', 'cohortDate', 'state', 'cohortSize', 'feedbackCounts', 'memberDigest')},
            'decisions': decisions, 'signals': signals})
    return {'version': 'strength-feedback-v1', 'strategyId': SID, 'modes': list(MODES), 'calendar': calendar,
        'targetDates': source['targetDates'], 'dailyRows': daily, 'opportunities': opportunities, 'schedules': schedules,
        'counts': {'sampleCount': len(symbols), 'targetDayCount': len(daily), 'inputSignalCount': len(source_signals),
            'opportunityCount': len(opportunities), 'dayStates': day_states,
            'signals': {m: len(schedules[m]) for m in MODES}, 'notScheduled': {m: len(source_signals)-len(schedules[m]) for m in MODES},
            'policyCounts': policy_counts},
        'basis': {'helperModule': 'service/research_leader_lag.py',
            'helperSha256': '1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e',
            'benchmark': 'sh000300', 'priceBasisId': 'raw-unadjusted', 'sourceStrategyId': BASELINE_SID,
            'rankHorizon': 20, 'formationLag': 5, 'cohortPercentile': .90,
            'rankDefinition': '(stockF/stockFminus20)/(benchmarkF/benchmarkFminus20)-1',
            'midrankDefinition': '(zeroBasedFirst+exclusiveStop)/(2*validPopulationSize)',
            'feedbackDefinition': 'closeA/closeF-1; all six exact source slots must be valid',
            'majorityDefinition': 'N=0 unknown; 2P>N pass; 2(P+U)<=N fail; otherwise unknown',
            'denominator': 'All members frozen at F; unknown feedback remains in N',
            'knownDecisionIsCompleteCohort': False, 'sourceSignalOrderingPreserved': True, 'top5Refill': False,
            'execution': 'Decision at A close; account executes next open',
            'historicalStatus': 'Absent status is unverified, not a tradability guarantee',
            'scope': 'Same-board fixed historical survivor cohort; no all-market or holdout claim'},
        'certified': False, 'targetAchieved': False}


def self_test():
    cases = [((0, 0, 0, 0), 'unknown'), ((3, 0, 0, 2), 'pass'), ((2, 0, 3, 0), 'fail'),
             ((2, 0, 1, 2), 'unknown'), ((2, 2, 0, 0), 'fail'), ((0, 2, 1, 1), 'fail')]
    for counts, state in cases:
        require(robust_majority(*counts) == state, 'Fixed-denominator strict majority and bounds')
    reference = lag_reference()
    data = reference.synthetic_source()
    source = reference.source_reference(data, data['calendar'][100], data['calendar'][109])
    item = source['indexed']['s039']
    value, reason = feedback_value(item, 100)
    require(value > 0 and reason is None, 'Exact six-slot absolute price feedback')
    item['issues'][98] = 'missingAlignedRawBar'
    item['validPrefix'] = [0]
    for issue in item['issues']:
        item['validPrefix'].append(item['validPrefix'][-1] + (issue is None))
    value, reason = feedback_value(item, 100)
    require(value is None and reason == 'stock:missingAlignedRawBar', 'Missing interior slot cannot become endpoint-only feedback')
    start, end = data['calendar'][100], data['calendar'][109]
    signals = [{'signalId': 'original:' + day, 'strategyId': BASELINE_SID, 'symbol': 's005',
                'signalDate': day, 'originalRank': 3} for day in data['calendar'][100:110]]
    before = digest(data)
    members = []
    full = reference_build(data, start, end, signals, on_members=members.append)
    require(digest(data) == before and len(members) == 10 and all(r['cohortSize'] == 4 for r in members),
            'Frozen top-decile cohort and detached member evidence')
    require(all(r['state'] == 'pass' for r in full['dailyRows']) and full['schedules']['known'] == full['schedules']['positive'],
            'Absolute continuations and nested masks')
    require([r['signalId'] for r in full['schedules']['positive']] == [r['signalId'] for r in signals]
            and all(r['strategyId'] == SID and r['originalRank'] == 3 for r in full['schedules']['positive'])
            and full['schedules']['positive'][-1]['signalDate'] == end, 'Retain IDs, holes, original order and cutoff')
    changed = deepcopy(data)
    for security in [*changed['securities'], changed['benchmark']]:
        for bar in security['bars']:
            if bar['date'] > end:
                bar['close'] = None
    require(reference_build(changed, start, end, signals) == full, 'Future numeric suffix cannot change evidence')
    changed = deepcopy(data)
    for bar in changed['benchmark']['bars']:
        if data['calendar'][95] < bar['date'] <= start:
            bar['close'] = None
    single = reference_build(changed, start, start, signals[:1])
    require(single['dailyRows'][0] == full['dailyRows'][0], 'Benchmark after F cannot change absolute feedback')
    changed = deepcopy(data)
    for security in changed['securities'][-2:]:
        security['bars'] = [bar for bar in security['bars'] if bar['date'] != data['calendar'][98]]
    partial = reference_build(changed, start, start, signals[:1])
    require(partial['dailyRows'][0]['cohortSize'] == 4 and partial['dailyRows'][0]['feedbackCounts']['unknown'] == 2
            and partial['dailyRows'][0]['state'] == 'unknown' and not any(partial['schedules'].values()),
            'Frozen denominator retains missing interior members with no replacement')
    short = reference_build(data, data['calendar'][0], data['calendar'][5], [])
    require(short['dailyRows'][0]['reason'] == 'insufficientCohortCalendarHistory'
            and short['dailyRows'][5]['reason'] == 'emptyFrozenCohort', 'Early calendar and insufficient rank window')
    ranks, count = reference.rank_reference({'a': 0., 'b': 0., 'c': 1., 'd': 1., 'x': None})
    require(count == 4 and ranks == {'a': .25, 'b': .25, 'c': .75, 'd': .75}, 'Exact ties and valid rank population')
    try:
        source_signals_reference(list(reversed(signals)), set(source['symbols']), set(source['targetDates']))
    except ValueError:
        pass
    else:
        raise ValueError('Unordered parent queue accepted')
    print(json.dumps({'passed': True, 'selfTests': 17, 'realSourceRead': False}), flush=True)


def registered_plan(directory):
    global REGISTERED_PLAN_SHA
    current = sha(directory / 'plan.json')
    require(REGISTERED_PLAN_SHA is None or current == REGISTERED_PLAN_SHA, 'Plan changed within audit process')
    REGISTERED_PLAN_SHA = current
    plan = read(directory / 'plan.json')
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix()) == sha(__file__), 'Verifier not frozen in plan')
    require(plan['protocol']['sha256'] == PROTOCOL_SHA and read(pinned(plan['protocol'])) == plan['rules'], 'Protocol changed')
    require(plan['schema'] == 'strength-feedback-v1' and plan['strategyId'] == SID and plan['modes'] == list(MODES)
            and plan['reportModes'] == list(REPORT_MODES) and plan['boards'] == list(BOARDS)
            and list(plan['periods']) == list(PERIODS) and plan['labels'] == LABELS, 'Registered identities differ')
    for key, value in {'newAccounts': 12, 'referenceAccountsReused': 6, 'newSetupMechanisms': 0,
                       'newEntryPolicies': 2, 'newUniverseVariants': 0}.items():
        require(type(plan[key]) is int and plan[key] == value, 'Registered trial scope differs: ' + key)
    require(plan['parameterSearch'] is False and plan['profitHurdles'] is None and plan['holdoutClaim'] is False
            and plan['certified'] is False and plan['targetAchieved'] is False, 'Unregistered optimization or certification')
    for path, expected in {**plan['sourcePins'], **plan['inputPins']}.items():
        require(sha(local(path)) == expected, 'Registered artifact changed: ' + path)
    require(pinned(plan['baselinePlan']) == (PREVIOUS / 'plan.json').resolve()
            and plan['baselinePlan']['sha256'] == PREVIOUS_PLAN_SHA, 'Baseline source plan changed')
    previous = read(PREVIOUS / 'plan.json')
    require(plan['sourceData'] == previous['sourceData'], 'The six exact source bindings changed')
    for group in ('sourcePins', 'inputPins'):
        require(all(plan[group].get(path) == value for path, value in previous[group].items()),
                'Inherited frozen artifact pin changed: ' + group)
    for group in ('baselineArtifacts', 'baselineSchedules', 'baselineInputs'):
        require(list(plan[group]) == list(PERIODS) and all(list(plan[group][p]) == list(BOARDS) for p in PERIODS),
                'Original baseline matrix changed: ' + group)
    for period in PERIODS:
        config = deepcopy(previous['periods'][period]['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        require(plan['periods'][period] == {'startDate': config['startDate'], 'endDate': config['endDate'], 'config': config},
                'Account configuration changed beyond strategy identity')
    return plan


def inherited_sources(plan):
    """Bind raw authenticity plus all original queue and baseline proofs."""
    source = lag_reference().inherited_sources(plan)
    require(sha(PREVIOUS / 'plan.json') == PREVIOUS_PLAN_SHA
            and sha(PREVIOUS / 'validation.json') == PREVIOUS_VALIDATION_SHA
            and sha(PREVIOUS / 'input-validation.json') == PREVIOUS_INPUT_VALIDATION_SHA
            and sha(PREVIOUS / 'delivery.json') == PREVIOUS_DELIVERY_SHA, 'Completed baseline proof changed')
    previous = read(PREVIOUS / 'plan.json')
    validation, inputs = read(PREVIOUS / 'validation.json'), read(PREVIOUS / 'input-validation.json')
    completion, delivery = read(PREVIOUS / 'input-completion.json'), read(PREVIOUS / 'delivery.json')
    require(pinned(plan['baselineComparison']) == (PREVIOUS / 'comparison.json').resolve(), 'Baseline comparison link changed')
    comparison = read(PREVIOUS / 'comparison.json')
    require(validation['passed'] is True and inputs['passed'] is True
            and validation['planSha256'] == inputs['planSha256'] == completion['planSha256'] == PREVIOUS_PLAN_SHA
            and validation['verifierSha256'] == inputs['verifierSha256'] == LAG_REFERENCE_SHA
            and validation['inputValidationSha256'] == PREVIOUS_INPUT_VALIDATION_SHA
            and validation['inputCompletionSha256'] == inputs['inputCompletionSha256'] == sha(PREVIOUS / 'input-completion.json')
            and validation['comparisonSha256'] == sha(PREVIOUS / 'comparison.json')
            and validation['datasetPins'] == inputs['datasetPins'] == source['datasetPins']
            and validation['accountArtifacts'] == comparison['artifacts'], 'Baseline validation links stale')
    for name in ('plan.json', 'input-completion.json', 'input-validation.json', 'validation.json', 'comparison.json'):
        path = PREVIOUS / name
        require(delivery['files'][path.relative_to(ROOT).as_posix()] == sha(path), 'Baseline delivery proof link stale')
    for period in PERIODS:
        for board in BOARDS:
            folder = PREVIOUS / period / board
            require(pinned(plan['baselineInputs'][period][board]) == (folder / 'input-completion.json').resolve()
                    and plan['baselineInputs'][period][board] == completion['outputs'][period][board], 'Original input receipt replaced')
            receipt = read(folder / 'input-completion.json')
            require(receipt['files'] == inputs['inputArtifacts'][period][board] == validation['inputArtifacts'][period][board],
                    'Original full opportunity lineage changed')
            for ref in receipt['files'].values():
                pinned(ref)
            require({k: receipt[k] for k in previous['sourceData'][period][board]} == plan['sourceData'][period][board],
                    'Baseline did not use exact same source')
            require(plan['baselineSchedules'][period][board] == receipt['files']['control.signals.json']
                    and pinned(plan['baselineSchedules'][period][board]) == (folder / 'control.signals.json').resolve(),
                    'Source schedule is not original audited control')
            files = plan['baselineArtifacts'][period][board]
            require(files == comparison['artifacts'][period][board]['control']
                    and set(files) == {'control' + s for s in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')},
                    'Baseline saved account closure replaced')
            for name, ref in files.items():
                require(pinned(ref) == (folder / name).resolve(), 'Baseline file is not original artifact')
    return source


def input_receipts(directory, plan):
    completion = read(directory / 'input-completion.json')
    require(completion['planSha256'] == REGISTERED_PLAN_SHA and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['newAccounts'] == 12 and completion['referenceAccountsReused'] == 6
            and list(completion['outputs']) == list(PERIODS), 'Incomplete schedule freeze receipt')
    receipts = {}
    for period in PERIODS:
        require(list(completion['outputs'][period]) == list(BOARDS), 'Input board matrix changed')
        receipts[period] = {}
        for board in BOARDS:
            folder = directory / period / board
            path = pinned(completion['outputs'][period][board])
            require(path == (folder / 'input-completion.json').resolve(), 'Input receipt location differs')
            receipt = read(path)
            require(receipt['planSha256'] == REGISTERED_PLAN_SHA and receipt['period'] == period and receipt['board'] == board,
                    'Input receipt identity changed')
            require({key: receipt[key] for key in plan['sourceData'][period][board]} == plan['sourceData'][period][board]
                    and receipt['baselineSchedule'] == plan['baselineSchedules'][period][board]
                    and receipt['baselineInput'] == plan['baselineInputs'][period][board], 'Input source/control lineage differs')
            expected = {'daily-evidence.jsonl.gz', 'member-evidence.jsonl.gz', 'opportunities.jsonl.gz',
                        'signal-metadata.json', 'input-counts.json'} | {m + '.signals.json' for m in MODES}
            require(set(receipt['files']) == expected, 'Exact input artifact closure missing')
            for name, ref in receipt['files'].items():
                require(pinned(ref) == (folder / name).resolve(), 'Input artifact outside original folder')
            require(datetime.fromisoformat(plan['registeredAt']) <= datetime.fromisoformat(receipt['completedAt'])
                    <= datetime.fromisoformat(completion['completedAt']), 'Inputs prepared before registration')
            receipts[period][board] = receipt
    return completion, receipts


def write_proof(directory, filename, proof):
    with (directory / filename).open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': True, 'artifact': filename, 'sha256': sha(directory / filename), 'counts': proof['counts']}), flush=True)


def parent_proof_fields(plan):
    return {'parentValidationSha256': PREVIOUS_VALIDATION_SHA, 'parentInputValidationSha256': PREVIOUS_INPUT_VALIDATION_SHA,
            'parentSourceValidationSha256': PARENT_SOURCE_VALIDATION_SHA, 'parentDeliverySha256': PREVIOUS_DELIVERY_SHA,
            'baselineArtifacts': plan['baselineArtifacts'], 'baselineSchedules': plan['baselineSchedules'],
            'baselineInputs': plan['baselineInputs'], 'baselineComparison': plan['baselineComparison'], 'baselinePlan': plan['baselinePlan']}


def verify_inputs(directory):
    started = time.perf_counter()
    require(not (directory / 'input-validation.json').exists(), 'Input proof already exists')
    plan = registered_plan(directory)
    source = inherited_sources(plan)
    _, receipts = input_receipts(directory, plan)
    artifacts, reports = {}, {}
    opportunities = days = evaluations = member_count = 0
    schedule_counts = Counter({mode: 0 for mode in MODES})
    for period in PERIODS:
        artifacts[period], reports[period] = {}, {}
        for board in BOARDS:
            receipt, folder = receipts[period][board], directory / period / board
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'] and data['period'] == period and data['board'] == board
                    and len(data['securities']) == receipt['members'], 'Frozen source or cohort contents changed')
            config = plan['periods'][period]['config']
            schedule = read(pinned(receipt['baselineSchedule']))
            original = records(PREVIOUS / period / board / 'opportunities.jsonl.gz')
            require(schedule == [r['signals']['control'] for r in original if r['decisions']['control']['status'] == 'included'],
                    'Original control was changed or upstream unknown resurrected')
            print(json.dumps({'phase': 'independent-feedback-input-start', 'period': period, 'board': board}), flush=True)
            checked_days = checked_members = 0
            with gzip.open(folder / 'member-evidence.jsonl.gz', 'rt', encoding='utf-8') as stream:
                def check_members(expected):
                    nonlocal checked_days, checked_members
                    line = next(stream, None)
                    require(line is not None, 'Missing frozen cohort day evidence')
                    # Hash equality also enforces exact rank values/order and all unknown identities.
                    actual = json.loads(line)
                    same(actual, expected, 'Entire frozen daily member cohort')
                    require(actual['memberDigest'] == digest(actual['members']) == expected['memberDigest'], 'Member digest mismatch')
                    checked_days += 1
                    checked_members += len(expected['members'])
                rebuilt = reference_build(data, config['startDate'], config['endDate'], schedule, on_members=check_members)
                require(next(stream, None) is None and checked_days == len(rebuilt['dailyRows']), 'Cohort stream count/order differs')
            for filename, key in (('daily-evidence.jsonl.gz', 'dailyRows'), ('opportunities.jsonl.gz', 'opportunities')):
                same(records(folder / filename), rebuilt[key], 'All independent rows: ' + filename)
            same(read(folder / 'signal-metadata.json'), {k: v for k, v in rebuilt.items()
                 if k not in ('dailyRows', 'opportunities', 'schedules')}, 'Full metadata including rank/feedback semantics')
            require(read(folder / 'input-counts.json') == receipt['counts'] == rebuilt['counts'], 'Input count mismatch')
            for mode in MODES:
                require(read(folder / (mode + '.signals.json')) == rebuilt['schedules'][mode], 'Ordered five-field schedule mismatch')
                schedule_counts[mode] += len(rebuilt['schedules'][mode])
            count, d = len(schedule), len(rebuilt['dailyRows'])
            opportunities += count; days += d; evaluations += len(data['securities']) * d; member_count += checked_members
            reports[period][board] = {'members': len(data['securities']), 'counts': rebuilt['counts'],
                'sourceControlOpportunities': count, 'parentRawTop5Opportunities': len(original),
                'targetSessions': d, 'symbolDayEvaluations': len(data['securities']) * d,
                'frozenMemberObservations': checked_members, 'accountDatasetDigest': receipt['accountDatasetDigest']}
            artifacts[period][board] = receipt['files']
            print(json.dumps({'phase': 'independent-feedback-input-passed', 'period': period, 'board': board,
                'opportunities': count, 'signals': rebuilt['counts']['signals']}), flush=True)
            del data, rebuilt, original
            gc.collect()
    registered_plan(directory)
    proof = {'schema': 'strength-feedback-input-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'inputCompletionSha256': sha(directory / 'input-completion.json'), **parent_proof_fields(plan),
        'inputArtifacts': artifacts, 'datasetPins': source['datasetPins'], 'periods': reports,
        'independentReferences': {LAG_REFERENCE: LAG_REFERENCE_SHA},
        'counts': {'boardPeriodInputs': 6, 'schedules': 12, 'sourceControlOpportunities': opportunities, 'dailyRows': days,
            'symbolDayEvaluations': evaluations, 'frozenMemberObservations': member_count, 'signals': dict(schedule_counts)},
        'checks': ['Six unchanged raw caches and baseline full opportunity/input/account proof chain',
            'Every causal F=A-5 RS20 population and tied midrank; all frozen top-decile members and exact six-slot absolute feedback',
            'Fixed denominator, strict majority bounds, all four member outcomes and retained unknown evidence',
            'Both ordered masks retain original source IDs/ranks/dates; no upstream resurrection, replacement or outcome filtering'],
        'scope': {'productionSignalBuilderImported': False, 'rawSourceReloaded': False, 'accountsExecuted': 0,
            'sourceAuthenticity': 'Inherited exact-bound independent raw-source validation; frozen caches independently read.'},
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
            and inputs['datasetPins'] == source['datasetPins']
            and {k: inputs[k] for k in parent_proof_fields(plan)} == parent_proof_fields(plan), 'Independent input proof stale')
    start = read(directory / 'run-start.json')
    require(pinned(start['inputCompletion']) == (directory / 'input-completion.json').resolve()
            and pinned(start['inputValidation']) == (directory / 'input-validation.json').resolve()
            and datetime.fromisoformat(completion['completedAt']) <= datetime.fromisoformat(inputs['verifiedAt'])
            <= datetime.fromisoformat(start['startedAt']), 'Accounts started before independent input audit')
    comparison = read(directory / 'comparison.json')
    previous = read(pinned(plan['baselineComparison']))
    require(comparison['schema'] == 'strength-feedback-comparison-v1' and comparison['planSha256'] == REGISTERED_PLAN_SHA,
            'Comparison identity differs')
    for key in ('newAccounts', 'referenceAccountsReused', 'newSetupMechanisms', 'newEntryPolicies', 'newUniverseVariants',
                'limitations', 'certified', 'targetAchieved'):
        require(comparison[key] == plan[key], 'Comparison scope differs: ' + key)
    require([(r['period'], r['board'], r['id'], r['policy']) for r in comparison['rows']]
            == [(p, b, m, m) for p in PERIODS for b in BOARDS for m in REPORT_MODES], 'Full 18-account matrix/order differs')
    board_audit = reference(BOARD_REFERENCE, BOARD_REFERENCE_SHA)
    board_audit.SID = SID  # In-memory configuration only; frozen arithmetic stays untouched.
    accounting = reference(board_audit.ACCOUNT_REFERENCE, board_audit.ACCOUNT_REFERENCE_SHA)
    dependencies = {LAG_REFERENCE: LAG_REFERENCE_SHA, BOARD_REFERENCE: BOARD_REFERENCE_SHA,
        board_audit.ACCOUNT_REFERENCE: board_audit.ACCOUNT_REFERENCE_SHA,
        accounting.COST_REFERENCE: accounting.COST_REFERENCE_SHA, **accounting.cost_reference().REFERENCES}
    for name, expected in dependencies.items():
        require(sha(local(name)) == expected, 'Independent accounting dependency changed')
    reports, reused = {}, {}
    require(list(comparison['artifacts']) == list(PERIODS), 'Account artifact period closure')
    for period in PERIODS:
        reports[period], reused[period] = {}, {}
        require(list(comparison['artifacts'][period]) == list(BOARDS), 'Account artifact board closure')
        for board in BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            require(inputs['inputArtifacts'][period][board] == receipt['files'], 'Inputs changed after independent audit')
            files = comparison['artifacts'][period][board]
            require(list(files) == list(REPORT_MODES) and files['baseline'] == plan['baselineArtifacts'][period][board],
                    'Baseline artifacts or mixed account matrix changed')
            baseline = deepcopy(next(r for r in previous['rows']
                if (r['period'], r['board'], r['policy']) == (period, board, 'control')))
            baseline.update(id='baseline', policy='baseline', label=LABELS['baseline'])
            actual = next(r for r in comparison['rows'] if (r['period'], r['board'], r['id']) == (period, board, 'baseline'))
            require(actual == baseline, 'Reused baseline row changed beyond report identity/label')
            oldsummary = read(pinned(files['baseline']['control.summary.json']))
            original_schedule = read(pinned(plan['baselineSchedules'][period][board]))
            require(oldsummary['policyId'] == 'control', 'Reused baseline policy metadata changed')
            expected = board_audit.comparison_reference(oldsummary, period, board, LABELS['baseline'], len(original_schedule))
            expected.update(id='baseline', policy='baseline')
            same(actual, expected, 'Reused baseline comparison statistics')
            reused[period][board] = {'reused': True, 'sourcePolicy': 'control', 'sourceStrategyId': BASELINE_SID,
                'sourceControlOpportunities': len(original_schedule), 'artifactIdentity': 'byte-for-byte parent artifacts',
                'parentValidationSha256': PREVIOUS_VALIDATION_SHA}
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'], 'Account dataset content digest changed')
            reports[period][board] = {}
            for mode in MODES:
                account_files = files[mode]
                require(set(account_files) == {mode + s for s in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')},
                        'Full saved account closure missing')
                for name, ref in account_files.items():
                    require(pinned(ref) == (folder / name).resolve(), 'New account artifact outside folder')
                result, checkpoint, summary = (read(folder / (mode + suffix))
                    for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json'))
                schedule = read(folder / (mode + '.signals.json'))
                require(len(schedule) == inputs['periods'][period][board]['counts']['signals'][mode], 'Schedule count drift')
                checked = board_audit.verify_board_account(result, checkpoint, summary, schedule,
                    plan['periods'][period]['config'], data, period, accounting)
                require(summary['policyId'] == mode and summary['policyLabel'] == LABELS[mode], 'Account summary policy differs')
                expected = board_audit.comparison_reference(summary, period, board, LABELS[mode], len(schedule))
                expected.update(id=mode, policy=mode)
                actual = next(r for r in comparison['rows'] if (r['period'], r['board'], r['id']) == (period, board, mode))
                same(actual, expected, 'All new comparison statistics')
                reports[period][board][mode] = checked
                print(json.dumps({'phase': 'independent-feedback-account-passed', 'period': period, 'board': board,
                                  'mode': mode, 'fills': checked['fills']}), flush=True)
                del result, checkpoint, summary
                gc.collect()
            del data
            gc.collect()
    registered_plan(directory)
    proof = {'schema': 'strength-feedback-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__), 'planSha256': REGISTERED_PLAN_SHA,
        'comparisonSha256': sha(directory / 'comparison.json'), 'inputValidationSha256': sha(directory / 'input-validation.json'),
        'inputCompletionSha256': sha(directory / 'input-completion.json'), **parent_proof_fields(plan),
        'datasetPins': source['datasetPins'], 'inputArtifacts': inputs['inputArtifacts'], 'accountArtifacts': comparison['artifacts'],
        'independentReferences': dependencies, 'periods': reports, 'reusedAccounts': reused,
        'counts': {'accounts': 18, 'newAccounts': 12, 'reusedAccounts': 6,
            'sourceControlOpportunities': inputs['counts']['sourceControlOpportunities'], 'signals': inputs['counts']['signals'],
            'newAccountFills': sum(r['fills'] for p in reports.values() for b in p.values() for r in b.values())},
        'checks': ['Twelve exact independently validated schedules frozen before new account execution',
            'Six original baseline accounts byte-bound to completed independent validation, with unchanged original control identity',
            'Twelve saved ledgers: dated fees, raw opening/slippage, board lots/limits, cash/shares, T+1 and reserve budgets',
            'Closed/open episodes, noProgress5 and risk exits, checkpoints, summaries and all 18 comparison rows'],
        'scope': {'productionSignalBuilderImported': False, 'accountsExecutedByVerifier': 0,
            'newLedgerAudits': 12, 'inheritedBaselineAudits': 6,
            'limitations': ['No separate resimulation of every eligibility/capacity/order decision.',
                'Current-survivor, raw-price, historical-status, rights and opening-capacity limitations remain.']},
        'certified': False, 'targetAchieved': False, 'elapsedSeconds': time.perf_counter() - started}
    write_proof(directory, 'validation.json', proof)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--inputs', '--inputs-only', dest='inputs', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    directory, stage = args.directory.resolve(), 'inputs' if args.inputs else 'accounts'
    try:
        (verify_inputs if args.inputs else verify_accounts)(directory)
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


