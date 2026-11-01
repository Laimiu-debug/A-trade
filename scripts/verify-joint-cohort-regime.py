"""Independently audit a repartition of previously audited feedback and money.

No new classification implementation or account engine is imported. Historical
rank/member and account truths are inherited through the completed parent's
exact audit pins; this audit recalculates the new integer partition and tables.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import traceback


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/joint-cohort-regime-v1'
PARENT = ROOT / 'artifacts/strategy-search/reclaim-relative-feedback-context-v1'
VERSION = 'joint-cohort-regime-v1'
HELPER = 'scripts/verify-reclaim-relative-feedback-context.py'
HELPER_SHA = '0d1f7ced85201f827b10e4ecf047124514bad56f726939e80fa7316fe756b34d'
PARENT_VALIDATION_SHA = '16207f17f381bebeb21046cf7d06adcf9ac7e3d387a4de3893c096714afcd7db'
STATES = ('bothPositiveMajority', 'topOnlyPositiveMajority', 'bottomOnlyPositiveMajority',
          'neitherPositiveMajority', 'unknown')
RELATIVE_STATES = ('stronger', 'weaker', 'equal', 'unknown')
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')
LABELS = dict(zip(STATES, ('强弱两组均多数上涨', '仅原强组多数上涨', '仅原弱组多数上涨', '两组均未多数上涨', '证据不足')))
BOUNDARY = '2024-09-24'
ADDITIONS = ('jointState', 'topMajority', 'bottomMajority', 'phaseId', 'formationEpoch')
EXPECTED_ROWS = {'overallRows': 60, 'formationYearRows': 120, 'epochStateRows': 90,
                 'phaseRows': 480, 'phaseDayRows': 240, 'epochDayRows': 45,
                 'accountReconciliation': 12, 'curveEpochRows': 12, 'relativeJointRows': 120}
TRIALS = {'newStrategyTrials': 0, 'newAccounts': 0, 'existingAccountsRead': 12,
          'newJointContextDefinitions': 1}
_HELPER = None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def ref(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def pinned(item):
    require(isinstance(item, dict) and isinstance(item.get('path'), str)
            and isinstance(item.get('sha256'), str), 'Malformed evidence reference')
    path = (ROOT / item['path']).resolve()
    require(path.is_relative_to(ROOT) and path.is_file(), 'Evidence path outside repository or absent')
    require(sha(path) == item['sha256'], 'Changed artifact: ' + item['path'])
    if 'bytes' in item:
        require(path.stat().st_size == item['bytes'], 'Changed artifact byte size')
    return path


def helper():
    global _HELPER
    if _HELPER is None:
        path = pinned({'path': HELPER, 'sha256': HELPER_SHA})
        spec = importlib.util.spec_from_file_location('_joint_independent_parent', path)
        _HELPER = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_HELPER)
    return _HELPER


def same(actual, expected, label):
    helper().same(actual, expected, label)


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, 'Timestamp lacks timezone')
    return parsed


def majority_reference(group):
    """Compare the entire possible positive-count interval to floor(N/2)."""
    counts = group['feedbackCounts']
    require(set(counts) == {'positive', 'flat', 'negative', 'unknown'}, 'Invalid member-status domain')
    require(all(type(n) is int and n >= 0 for n in counts.values()), 'Noninteger or negative member count')
    total = group['cohortSize']
    require(type(total) is int and total == sum(counts.values()), 'Frozen cohort denominator mismatch')
    if total == 0:
        return 'unknown'
    required_positive = total // 2 + 1
    minimum, maximum = counts['positive'], counts['positive'] + counts['unknown']
    if minimum >= required_positive:
        return 'pass'
    if maximum < required_positive:
        return 'fail'
    return 'unknown'


def joint_reference(top, bottom):
    left, right = majority_reference(top), majority_reference(bottom)
    # An explicit table, independent of the production classifier.
    table = {('pass', 'pass'): STATES[0], ('pass', 'fail'): STATES[1],
             ('fail', 'pass'): STATES[2], ('fail', 'fail'): STATES[3]}
    return {'jointState': table.get((left, right), 'unknown'),
            'topMajority': left, 'bottomMajority': right}


def phase_reference(day, phases):
    matches = [phase['id'] for phase in phases if phase['startDate'] <= day <= phase['endDate']]
    require(len(matches) == 1, 'Missing or overlapping descriptive phase for ' + day)
    return matches[0]


def epoch_reference(day, period):
    require(period in PERIODS and period[:4] <= day[:4] <= period[-4:], 'Date outside period')
    if period == PERIODS[0]:
        return 'oldPeriod'
    return 'before20240924' if day < BOUNDARY else 'from20240924'


def classify_day(row, period, board, phases):
    require(not set(ADDITIONS).intersection(row), 'Original day already contains new classification fields')
    return {**row, 'period': period, 'board': board, **joint_reference(row['top'], row['bottom']),
            'phaseId': phase_reference(row['formationDate'], phases),
            'formationEpoch': epoch_reference(row['formationDate'], period)}


def augment_records(originals, daily):
    lookup = {(r['period'], r['board'], r['formationDate']): r for r in daily}
    require(len(lookup) == len(daily), 'Duplicate source context date')
    result, identities = [], set()
    for row in originals:
        identity = (row['period'], row['board'], row['policy'], row['formationId'])
        require(identity not in identities, 'Duplicate original formation-policy identity')
        identities.add(identity)
        require(not set(ADDITIONS).intersection(row), 'Original record already has new fields')
        context = lookup[(row['period'], row['board'], row['formationDate'])]
        for field, source_field in (('asOfDate', 'asOfDate'), ('cohortDate', 'cohortDate'),
                                    ('state', 'state'), ('contextMemberDigest', 'memberDigest')):
            require(row[field] == context[source_field], 'Original account/context join mismatch: ' + field)
        result.append({**row, **{k: context[k] for k in ADDITIONS}})
    return result


def aggregate_reference(records, daily, phases):
    """Build empty Cartesian cells, then independently fill each from records."""
    aggregate = helper().aggregate_group
    account_buckets, date_buckets = defaultdict(list), defaultdict(list)
    for row in records:
        account_buckets[(row['period'], row['board'], row['policy'])].append(row)
    for row in daily:
        date_buckets[(row['period'], row['board'])].append(row)
    output = {key: [] for key in ('overallRows', 'formationYearRows', 'epochStateRows', 'phaseRows',
                                 'phaseDayRows', 'epochDayRows', 'relativeJointRows')}
    for period in PERIODS:
        phase_ids = [p['id'] for p in phases if period[:4] <= p['startDate'][:4] <= period[-4:]]
        epochs = ('oldPeriod',) if period == PERIODS[0] else ('before20240924', 'from20240924')
        for board in BOARDS:
            dates = date_buckets[(period, board)]
            for epoch in epochs:
                in_epoch = [d for d in dates if d['formationEpoch'] == epoch]
                counts = Counter(d['jointState'] for d in in_epoch)
                for state in STATES:
                    output['epochDayRows'].append({'period': period, 'board': board, 'formationEpoch': epoch,
                        'jointState': state, 'dayCount': counts[state], 'totalDays': len(in_epoch),
                        'daySharePct': counts[state] / len(in_epoch) * 100 if in_epoch else None})
            for phase in phase_ids:
                in_phase = [d for d in dates if d['phaseId'] == phase]
                counts = Counter(d['jointState'] for d in in_phase)
                for state in STATES:
                    output['phaseDayRows'].append({'period': period, 'board': board, 'phaseId': phase,
                                                  'jointState': state, 'days': counts[state], 'phaseDays': len(in_phase)})
            combinations = Counter((r['state'], r['jointState']) for r in dates)
            for old_state in RELATIVE_STATES:
                for state in STATES:
                    output['relativeJointRows'].append({'period': period, 'board': board,
                        'relativeState': old_state, 'jointState': state, 'days': combinations[(old_state, state)]})
            for policy in POLICIES:
                account = account_buckets[(period, board, policy)]
                for state in STATES:
                    selected = [r for r in account if r['jointState'] == state]
                    key = {'period': period, 'board': board, 'policy': policy, 'jointState': state}
                    output['overallRows'].append({**key, **aggregate(selected)})
                    for year in period.split('-'):
                        output['formationYearRows'].append({**key, 'formationYear': year,
                            **aggregate([r for r in selected if r['formationDate'][:4] == year])})
                    for epoch in epochs:
                        output['epochStateRows'].append({**key, 'formationEpoch': epoch,
                            **aggregate([r for r in selected if r['formationEpoch'] == epoch])})
                    for phase in phase_ids:
                        output['phaseRows'].append({**key, 'phaseId': phase,
                            **aggregate([r for r in selected if r['phaseId'] == phase])})
    return output


def verify_bridges(records, grouped, parent_bridges):
    require(len(parent_bridges) == 12, 'Inherited money-bridge domain differs')
    by_account = {(r['period'], r['board'], r['policy']): r for r in parent_bridges}
    require(set(by_account) == {(p, b, m) for p in PERIODS for b in BOARDS for m in POLICIES}, 'Money-bridge identity differs')
    for identity, bridge in by_account.items():
        selected = [r for r in records if (r['period'], r['board'], r['policy']) == identity]
        total = helper().aggregate_group(selected)
        for key in ('formationCount', 'entered', 'closed', 'open', 'notEntered',
                    'closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount'):
            same(total[key], bridge[key], 'Original account bridge.' + key)
        same(total['totalPnlAmount'], bridge['assignedPnlAmount'], 'Assigned episode PnL')
        same(total['totalPnlAmount'] + bridge['unallocatedResidual'], bridge['accountNetPnlAmount'], 'Episode money plus residual')
        same(bridge['accountNetPnlAmount'] / 500000 * 100, bridge['accountReturnPct'], 'Original capital return')
        require(bridge['passed'] is True, 'Original bridge not passed')
        for name in ('overallRows', 'formationYearRows', 'epochStateRows', 'phaseRows'):
            cells = [r for r in grouped[name] if (r['period'], r['board'], r['policy']) == identity]
            for key in ('formationCount', 'entered', 'closed', 'open', 'notEntered', 'wins', 'losses', 'zeros'):
                same(sum(r[key] for r in cells), total[key], name + ' partition.' + key)
            for key in ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount', 'totalPnlAmount'):
                same(math.fsum(r[key] for r in cells), total[key], name + ' cash partition.' + key)


def registered_plan(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == VERSION, 'Wrong plan schema')
    same(plan['states'], list(STATES), 'Fixed five states')
    same(plan['labels'], LABELS, 'Labels do not imply whole-market conditions')
    same(plan['expectedRows'], EXPECTED_ROWS, 'Complete Cartesian matrices')
    same(plan['trialAccounting'], TRIALS, 'Zero new strategy/account scope')
    pins = {}
    for item in plan['sourcePins'] + plan['inputPins']:
        pinned(item)
        require(item['path'] not in pins or pins[item['path']] == item['sha256'], 'Conflicting plan pin')
        pins[item['path']] = item['sha256']
    protocol = read(pinned(plan['protocol']))
    require(protocol['schema'] == VERSION, 'Wrong protocol')
    for key in ('rules', 'states', 'expectedRows', 'trialAccounting'):
        same(plan[key], protocol[key], 'Protocol.' + key)
    require(protocol['profitHurdles'] is None and protocol['holdoutClaim'] is False
            and protocol['certified'] is False and protocol['targetAchieved'] is False, 'Unexpected certification or trading target')
    require(plan['existingCandidateTrials'] == 38 and plan['registeredProductionTests'] == 1026
            and plan['newProductionUnitTests'] == 0, 'Historical trial accounting changed')
    timestamp(plan['registeredAt'])
    return plan, pins


def inherited_inputs(plan, pins):
    require(plan['sourceValidation'] == {'path': (PARENT / 'validation.json').relative_to(ROOT).as_posix(),
            'sha256': PARENT_VALIDATION_SHA}, 'Not the completed parent independent proof')
    proof = read(pinned(plan['sourceValidation']))
    parent_path = pinned(plan['parentReadout'])
    parent = read(parent_path)
    require(parent_path == PARENT / 'readout.json' and proof['passed'] is True
            and proof['verifierSha256'] == HELPER_SHA and proof['readoutSha256'] == sha(parent_path), 'Parent proof/readout stale')
    parent_plan = PARENT / 'plan.json'
    require(sha(parent_plan) == proof['planSha256'] == proof['pins'][parent_plan.relative_to(ROOT).as_posix()], 'Parent registered plan mismatch')
    require(parent['planSha256'] == proof['planSha256'], 'Parent report plan mismatch')
    pinned(parent['inputCompletion'])
    require(parent['inputCompletion']['sha256'] == proof['inputCompletionSha256'], 'Parent extraction completion mismatch')
    for item in (plan['sourceValidation'], plan['parentReadout'], parent['records'], plan['atlas']):
        require(pins.get(item['path']) == item['sha256'], 'Missing registered consumed input: ' + item['path'])
    require(parent['records']['sha256'] == proof['recordsSha256']
            == proof['pins'][parent['records']['path']], 'Original record attribution not in parent audit')
    require(parent['formationCount'] == proof['counts']['formationCount'] == 8155
            and parent['recordCount'] == proof['counts']['recordCount'] == 16310, 'Parent population changed')
    domain = [(x['period'], x['board']) for x in parent['contexts']]
    require(len(domain) == 6 and set(domain) == {(p, b) for p in PERIODS for b in BOARDS}, 'Wrong six context sources')
    inherited_pins = {}
    for context in parent['contexts']:
        for name in ('daily-evidence.jsonl.gz', 'metadata.json'):
            item = context[name]
            require(proof['pins'].get(item['path']) == item['sha256'], 'Context artifact not previously audited: ' + name)
            pinned(item)
            inherited_pins[item['path']] = item['sha256']
        item = context['daily-evidence.jsonl.gz']
        require(pins.get(item['path']) == item['sha256'], 'Missing registered daily evidence')
    return parent, proof, inherited_pins


def reconstruct_daily(parent, phases):
    daily = []
    for context in parent['contexts']:
        metadata = read(pinned(context['metadata.json']))
        calendar, target = metadata['calendar'], metadata['targetDates']
        require(calendar == sorted(set(calendar)), 'Parent calendar duplicates/order')
        indexed = {date: index for index, date in enumerate(calendar)}
        original = rows(pinned(context['daily-evidence.jsonl.gz']))
        require([r['formationDate'] for r in original] == target, 'Parent daily target coverage')
        for row in original:
            index = indexed[row['formationDate']]
            as_of, cohort = index - 1, index - 6
            require(row['asOfDate'] == (calendar[as_of] if as_of >= 0 else None), 'Context not exact T-1')
            require(row['cohortDate'] == (calendar[cohort] if cohort >= 0 else None), 'Context not exact T-6')
            require(row['rankWindowDates'] == (calendar[max(0, cohort - 20):cohort + 1] if cohort >= 0 else []), 'Rank window changed')
            require(row['feedbackWindowDates'] == (calendar[max(0, cohort):as_of + 1] if as_of >= 0 else []), 'Feedback window changed')
            require(all(day < row['formationDate'] for day in row['rankWindowDates'] + row['feedbackWindowDates']), 'Current or future date entered context')
            for side in ('top', 'bottom'):
                group = row[side]
                majority_reference(group)
                n, c = group['cohortSize'], group['feedbackCounts']
                same(group['positiveFractionLower'], c['positive'] / n if n else None, 'Inherited positive lower bound')
                same(group['positiveFractionUpper'], (c['positive'] + c['unknown']) / n if n else None, 'Inherited positive upper bound')
            daily.append(classify_day(row, context['period'], context['board'], phases))
    return daily


def validate(directory):
    directory = directory.resolve()
    require(not (directory / 'validation.json').exists(), 'Refuse to replace prior independent validation')
    plan, pins = registered_plan(directory)
    parent, parent_proof, inherited_pins = inherited_inputs(plan, pins)
    atlas = read(pinned(plan['atlas']))
    phases = [{key: row[key] for key in ('id', 'startDate', 'endDate', 'title')} for row in atlas['phases']]
    require(len(phases) == len({p['id'] for p in phases}) == 16, 'Wrong descriptive phase domain')
    require(all(p['startDate'] <= p['endDate'] for p in phases), 'Reversed phase boundaries')
    originals = rows(pinned(parent['records']))
    daily = reconstruct_daily(parent, phases)
    require(len(daily) == parent_proof['counts']['sourceDays'] == 2907, 'Six-source complete daily coverage')
    expected_records = augment_records(originals, daily)
    require(len(expected_records) == 16310, 'Original formation-policy rows missing')
    formation_policies = defaultdict(list)
    for row in expected_records:
        formation_policies[(row['period'], row['board'], row['formationId'])].append(row['policy'])
    require(len(formation_policies) == 8155 and all(sorted(x) == sorted(POLICIES) for x in formation_policies.values()), 'Both policies per original formation required')
    actual_records, actual_daily = rows(directory / 'records.jsonl.gz'), rows(directory / 'daily.jsonl.gz')
    # Exact original values and order: no tolerant float comparison at this boundary.
    require(actual_records == expected_records, 'Record identity/order/original fields or added labels differ')
    require(actual_daily == daily, 'Daily source fields/order or added labels differ')
    grouped = aggregate_reference(expected_records, daily, phases)
    verify_bridges(expected_records, grouped, parent['accountReconciliation'])
    for period in PERIODS:
        for board in BOARDS:
            days = sum(r['period'] == period and r['board'] == board for r in daily)
            for table, count_key in (('relativeJointRows', 'days'), ('phaseDayRows', 'days'), ('epochDayRows', 'dayCount')):
                same(sum(r[count_key] for r in grouped[table] if (r['period'], r['board']) == (period, board)), days, table + ' date partition')
    readout = read(directory / 'readout.json')
    require(timestamp(plan['registeredAt']) <= timestamp(readout['generatedAt']) <= datetime.now(timezone.utc), 'Analysis registration/build chronology')
    expected = {'schema': VERSION + '-readout', 'generatedAt': readout['generatedAt'], 'plan': ref(directory / 'plan.json'),
        'records': ref(directory / 'records.jsonl.gz'), 'daily': ref(directory / 'daily.jsonl.gz'),
        'recordCount': len(expected_records), 'formationCount': len(formation_policies), 'dailyCount': len(daily),
        'states': list(STATES), 'labels': LABELS, 'phases': phases, **grouped,
        'accountReconciliation': parent['accountReconciliation'], 'curveEpochRows': parent['curveEpochRows'],
        'qualityRows': parent['qualityRows'], 'trialAccounting': TRIALS, 'method': plan['rules'],
        'limitations': parent['limitations'], 'sourceReadout': plan['parentReadout'],
        'selectedState': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    same(readout, expected, 'Every readout field and statistic')
    require(all(len(readout[key]) == count for key, count in EXPECTED_ROWS.items()), 'Expected matrix rows missing')
    for key in ('accountReconciliation', 'curveEpochRows', 'qualityRows', 'limitations'):
        require(readout[key] == parent[key], 'Inherited account/curve/quality changed: ' + key)
    registered_plan(directory)
    source_pins = {Path(__file__).resolve().relative_to(ROOT).as_posix(): sha(__file__), HELPER: HELPER_SHA}
    proof = {'schema': VERSION + '-independent-validation', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': sha(directory / 'plan.json'), 'readoutSha256': sha(directory / 'readout.json'),
        'recordsSha256': sha(directory / 'records.jsonl.gz'), 'dailySha256': sha(directory / 'daily.jsonl.gz'),
        'parentValidationSha256': PARENT_VALIDATION_SHA, 'parentReadoutSha256': plan['parentReadout']['sha256'],
        'sourcePins': source_pins, 'inputPins': {**pins, **inherited_pins},
        'auditImplementationFrozenAfterAnalysisRegistration': True,
        'counts': {'formationCount': len(formation_policies), 'recordCount': len(expected_records),
                   'sourceDays': len(daily), 'accounts': 12, 'newAccounts': 0, 'newSignals': 0,
                   'expectedRows': EXPECTED_ROWS},
        'checks': ['Exact completed-parent proof binds every daily table, calendar metadata and original money record',
                   'Strict T-1/T-6 and prior-only rank/feedback windows inherited and independently checked',
                   'Fixed N=P+Z+L+U; independent integer positive-majority bounds and five-way joint table',
                   'All 8155 formations and two policies retained in original order; original fields byte-value equivalent',
                   'All empty cells and distribution/holding/concentration statistics independently regrouped',
                   'Source days counted once, without policy or opportunity duplication',
                   'All 12 account cash bridges and every grouped count/cash partition reconcile',
                   'Original continuous curve epochs, quality flags and residuals retained exactly',
                   'Analysis registered before build; audit implementation separately pinned after registration'],
        'scope': {'sourceTruth': 'Prior independent whole-population rank/member audit inherited; no raw-market reread or reranking',
                  'accountTruth': 'Prior independent ledger/disposition audit inherited; no account executions or changed outcomes',
                  'newWork': 'Only integer joint-state classification, descriptive phase/date joins and money/statistic partitions',
                  'claim': 'Exposed development histories; neither an executable gate test nor a causal or holdout estimate'},
        'certified': False, 'targetAchieved': False}
    with (directory / 'validation.json').open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return {'passed': True, 'validation': ref(directory / 'validation.json'), 'counts': proof['counts']}


def self_test():
    def group(p=0, z=0, l=0, u=0):
        return {'cohortSize': p + z + l + u,
                'feedbackCounts': {'positive': p, 'flat': z, 'negative': l, 'unknown': u}}
    # Exhaustive small fixed cohorts compare integer implementation to exact fractions.
    for n in range(9):
        for p in range(n + 1):
            for u in range(n - p + 1):
                g = group(p=p, l=n - p - u, u=u)
                want = ('unknown' if n == 0 else 'pass' if Fraction(p, n) > Fraction(1, 2)
                        else 'fail' if Fraction(p + u, n) <= Fraction(1, 2) else 'unknown')
                original = deepcopy(g)
                require(majority_reference(g) == want and g == original, 'Small-cohort bounds or immutability')
    require(majority_reference(group(z=10)) == 'fail', 'Flat is not a positive majority')
    require(majority_reference(group(p=3, l=1, u=1)) == 'pass', 'Unknown does not erase certain majority')
    require(majority_reference(group(p=1, l=3, u=1)) == 'fail', 'Unknown does not erase certain non-majority')
    representatives = {'pass': group(p=3, l=2), 'fail': group(p=2, l=3), 'unknown': group(p=2, l=1, u=2)}
    expected_joint = {('pass', 'pass'): STATES[0], ('pass', 'fail'): STATES[1],
                      ('fail', 'pass'): STATES[2], ('fail', 'fail'): STATES[3]}
    for a, ga in representatives.items():
        for b, gb in representatives.items():
            require(joint_reference(ga, gb)['jointState'] == expected_joint.get((a, b), STATES[4]), 'Joint truth table')
    for malformed in [group(p=-1, l=3), {'cohortSize': 3, 'feedbackCounts': group(p=2)['feedbackCounts']},
                      {'cohortSize': True, 'feedbackCounts': group(p=1)['feedbackCounts']},
                      {'cohortSize': 1, 'feedbackCounts': {'positive': True, 'flat': 0, 'negative': 0, 'unknown': 0}}]:
        try:
            majority_reference(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed fixed denominator accepted')
    # A relative stronger result can remain absolutely non-majority on both sides.
    require(joint_reference(group(p=2, l=8), group(p=1, l=9))['jointState'] == STATES[3], 'Relative versus absolute distinction')
    require(joint_reference(group(p=4, l=3, u=3), group(p=1, l=9))['jointState'] == 'unknown', 'Relative-known must not force joint-known')
    phases = [{'id': 'pre', 'startDate': '2024-01-02', 'endDate': '2024-09-23', 'title': 'pre'},
              {'id': 'post', 'startDate': '2024-09-24', 'endDate': '2025-12-31', 'title': 'post'}]
    require(phase_reference('2024-09-24', phases) == 'post'
            and epoch_reference('2024-09-23', '2024-2025') == 'before20240924'
            and epoch_reference('2024-09-24', '2024-2025') == 'from20240924', '9.24 descriptive boundary')
    base_day = {'formationDate': '2024-09-24', 'asOfDate': '2024-09-23', 'cohortDate': '2024-09-12',
                'top': group(p=3, l=2), 'bottom': group(p=2, l=3), 'memberDigest': 'synthetic', 'state': 'stronger'}
    classified = classify_day(base_day, '2024-2025', 'main', phases)
    original_record = {'period': '2024-2025', 'board': 'main', 'policy': 'trail10', 'formationId': 'synthetic',
        'formationDate': base_day['formationDate'], 'asOfDate': base_day['asOfDate'], 'cohortDate': base_day['cohortDate'],
        'state': 'stronger', 'contextMemberDigest': 'synthetic', 'closedReturnPct': None, 'positionState': 'notEntered'}
    augmented = augment_records([original_record], [classified])[0]
    require({k: v for k, v in augmented.items() if k not in ADDITIONS} == original_record
            and augmented['asOfDate'] == '2024-09-23', 'Exact unchanged record, current-day response excluded')
    try:
        augment_records([original_record, original_record], [classified])
    except ValueError:
        pass
    else:
        raise AssertionError('Duplicate record accepted')
    empty = helper().aggregate_group([])
    require(empty['closedMeanPct'] is None and empty['formationCount'] == 0 and empty['totalPnlAmount'] == 0, 'Empty cell retained')
    common = {**augmented, 'symbol': 'synthetic', 'reason': 'test', 'entered': False, 'closed': False, 'open': False,
              'closedPnlAmount': 0.0, 'openRealizedPnlAmount': 0.0, 'openUnrealizedPnlAmount': 0.0,
              'openMarkStale': False, 'holdingSessions': None}
    open_row = {**common, 'formationId': 'open', 'entered': True, 'open': True, 'positionState': 'openAtCutoff',
                'openRealizedPnlAmount': 2.0, 'openUnrealizedPnlAmount': 8.0}
    closed = {**common, 'formationId': 'closed', 'entered': True, 'closed': True, 'positionState': 'closed',
              'closedReturnPct': -5.0, 'closedPnlAmount': -50.0, 'holdingSessions': 6}
    result = helper().aggregate_group([common, open_row, closed])
    require(result['closed'] == 1 and result['notEntered'] == 1 and result['open'] == 1
            and result['closedMeanPct'] == -5.0 and result['totalPnlAmount'] == -40.0, 'Open/unentered never become zero-return closed trades')
    matrices = aggregate_reference([common, open_row, closed], [classified], phases)
    selected = next(r for r in matrices['overallRows'] if (r['period'], r['board'], r['policy'], r['jointState'])
                    == ('2024-2025', 'main', 'trail10', STATES[1]))
    require(selected['formationCount'] == 3 and selected['totalPnlAmount'] == -40.0
            and sum(r['dayCount'] for r in matrices['epochDayRows']) == 1, 'Opportunity count distinct from daily state sample')
    require(len(matrices['overallRows']) == 60 and len(matrices['formationYearRows']) == 120
            and len(matrices['epochStateRows']) == 90 and len(matrices['relativeJointRows']) == 120
            and len(matrices['epochDayRows']) == 45, 'Cartesian zero cells')
    return {'passed': True, 'syntheticGroups': 15, 'newAccounts': 0, 'realSourceReads': 0}


def main():
    parser = ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return
    try:
        result = validate(args.directory)
    except Exception as error:
        directory = args.directory.resolve()
        attempts = directory / 'validation-attempts'
        attempts.mkdir(exist_ok=True)
        failure = {'passed': False, 'verifiedAt': datetime.now(timezone.utc).isoformat(),
                   'verifierSha256': sha(__file__), 'error': type(error).__name__ + ': ' + str(error),
                   'traceback': traceback.format_exc(),
                   'pins': {name: sha(directory / name) for name in
                            ('plan.json', 'readout.json', 'records.jsonl.gz', 'daily.jsonl.gz') if (directory / name).is_file()}}
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
        with (attempts / name).open('x', encoding='utf-8') as stream:
            json.dump(failure, stream, ensure_ascii=False, indent=2)
        raise
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
