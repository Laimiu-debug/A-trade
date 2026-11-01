"""Repartition byte-bound existing outcomes by absolute top/bottom feedback.

This descriptive analysis never imports or runs an account engine. The existing
aggregate is reused verbatim; only the joint-state partition is new.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'reclaim-relative-feedback-context-v1'
OUT = BASE / 'joint-cohort-regime-v1'
PROTOCOL = ROOT / 'docs/joint-cohort-regime-v1.json'
HELPER = ROOT / 'scripts/diagnose-reclaim-relative-feedback-context.py'
HELPER_SHA = 'c9f862a369712736955fa94dbcffa017746cc88c84339667be2239c1ffa95dc2'
ATLAS = BASE / 'market-phase-atlas-v1/atlas.json'
STATES = ('bothPositiveMajority', 'topOnlyPositiveMajority', 'bottomOnlyPositiveMajority', 'neitherPositiveMajority', 'unknown')
PERIODS, BOARDS = ('2022-2023', '2024-2025'), ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')
LABELS = dict(zip(STATES, ('强弱两组均多数上涨', '仅原强组多数上涨', '仅原弱组多数上涨', '两组均未多数上涨', '证据不足')))
BOUNDARY = '2024-09-24'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    path = (ROOT / item['path']).resolve()
    assert path.is_relative_to(ROOT) and sha(path) == item['sha256'], item['path']
    return path


def majority(group):
    counts = group['feedbackCounts']
    assert set(counts) == {'positive', 'flat', 'negative', 'unknown'}
    assert all(type(v) is int and v >= 0 for v in counts.values())
    n, p, u = group['cohortSize'], counts['positive'], counts['unknown']
    assert type(n) is int and n == sum(counts.values())
    if not n:
        return 'unknown'
    if 2 * p > n:
        return 'pass'
    if 2 * (p + u) <= n:
        return 'fail'
    return 'unknown'


def joint(top, bottom):
    t, b = majority(top), majority(bottom)
    state = 'unknown' if 'unknown' in (t, b) else {
        ('pass', 'pass'): STATES[0], ('pass', 'fail'): STATES[1],
        ('fail', 'pass'): STATES[2], ('fail', 'fail'): STATES[3]}[(t, b)]
    return {'jointState': state, 'topMajority': t, 'bottomMajority': b}


def self_test():
    def g(p, z=0, l=0, u=0):
        return {'cohortSize': p+z+l+u, 'feedbackCounts': dict(positive=p, flat=z, negative=l, unknown=u)}
    values = [(g(3,l=2), 'pass'), (g(2,l=3), 'fail'), (g(2,l=2), 'fail'),
              (g(0), 'unknown'), (g(2,l=1,u=2), 'unknown'), (g(3,l=1,u=1), 'pass'),
              (g(1,l=3,u=1), 'fail'), (g(0,z=4), 'fail')]
    for value, expected in values:
        original = deepcopy(value)
        assert majority(value) == expected and value == original
    for t, b, expected in [(g(3,l=2),g(3,l=2),STATES[0]), (g(3,l=2),g(2,l=3),STATES[1]),
                           (g(2,l=3),g(3,l=2),STATES[2]), (g(2,l=3),g(2,l=3),STATES[3]),
                           (g(0),g(3,l=2),STATES[4])]:
        assert joint(t,b)['jointState'] == expected
    for invalid in [g(-1,l=4), {'cohortSize': 5, 'feedbackCounts': g(2,l=2)['feedbackCounts']}]:
        try:
            majority(invalid)
        except AssertionError:
            continue
        raise AssertionError('Invalid denominator accepted')
    return {'passed': True, 'syntheticCases': 15, 'newAccounts': 0}


def register():
    assert sha(HELPER) == HELPER_SHA
    parent, proof, protocol = read(PARENT/'readout.json'), read(PARENT/'validation.json'), read(PROTOCOL)
    assert proof['passed'] and proof['readoutSha256'] == sha(PARENT/'readout.json')
    assert proof['recordsSha256'] == parent['records']['sha256']
    assert tuple(protocol['states']) == STATES
    paths = [PARENT/'readout.json', PARENT/'validation.json', ATLAS, bound(parent['records'])]
    for context in parent['contexts']:
        paths.append(bound(context['daily-evidence.jsonl.gz']))
    plan = {'schema': OUT.name, 'registeredAt': now(), 'previousTurnClassification': 'progress',
            'previousTurnEvidence': ref(BASE/'program.json'), 'protocol': ref(PROTOCOL),
            'sourcePins': [ref(Path(__file__).resolve()), ref(HELPER), ref(PROTOCOL)],
            'inputPins': [ref(p) for p in paths], 'parentReadout': ref(PARENT/'readout.json'),
            'sourceValidation': ref(PARENT/'validation.json'), 'atlas': ref(ATLAS),
            'rules': protocol['rules'], 'expectedRows': protocol['expectedRows'],
            'trialAccounting': protocol['trialAccounting'], 'selfTests': self_test(),
            'states': list(STATES), 'labels': LABELS, 'existingCandidateTrials': 38,
            'registeredProductionTests': 1026, 'newProductionUnitTests': 0,
            'note': 'Synthetic analysis checks are not additional strategy or production-test counts.'}
    OUT.mkdir(exist_ok=False)
    write(OUT/'plan.json', plan)
    return {'registered': True, 'plan': ref(OUT/'plan.json')}


def verify_plan():
    plan = read(OUT/'plan.json')
    for item in plan['sourcePins'] + plan['inputPins']:
        bound(item)
    return plan


def phase_for(day, phases):
    found = [p for p in phases if p['startDate'] <= day <= p['endDate']]
    assert len(found) == 1, day
    return found[0]['id']


def epoch_for(day, period):
    return 'oldPeriod' if period == PERIODS[0] else 'before20240924' if day < BOUNDARY else 'from20240924'


def build():
    plan = verify_plan()
    parent = read(bound(plan['parentReadout']))
    phases = [{k:p[k] for k in ('id','startDate','endDate','title')} for p in read(ATLAS)['phases']]
    assert len(phases) == 16
    assert sha(HELPER) == HELPER_SHA
    spec = importlib.util.spec_from_file_location('_joint_aggregate', HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    aggregate = helper.aggregate
    daily, lookup = [], {}
    for context in parent['contexts']:
        period, board = context['period'], context['board']
        for row in records(bound(context['daily-evidence.jsonl.gz'])):
            assert row['cohortDate'] < row['asOfDate'] < row['formationDate']
            key = (period, board, row['formationDate'])
            assert key not in lookup
            value = {**row, 'period': period, 'board': board, **joint(row['top'], row['bottom']),
                     'phaseId': phase_for(row['formationDate'], phases),
                     'formationEpoch': epoch_for(row['formationDate'], period)}
            lookup[key] = value
            daily.append(value)
    source = records(bound(parent['records']))
    assert len(source) == 16310
    assert len({(r['period'],r['board'],r['policy'],r['formationId']) for r in source}) == len(source)
    all_rows = []
    for row in source:
        context = lookup[(row['period'],row['board'],row['formationDate'])]
        assert row['contextMemberDigest'] == context['memberDigest']
        assert row['asOfDate'] == context['asOfDate'] and row['cohortDate'] == context['cohortDate']
        assert row['state'] == context['state']
        all_rows.append({**row, **{k:context[k] for k in ('jointState','topMajority','bottomMajority','phaseId','formationEpoch')}})
    overall, yearly, epochs, phase_rows, phase_days, epoch_days, bridges, relative = [], [], [], [], [], [], [], []
    for period in PERIODS:
        applicable_phases = [p for p in phases if period[:4] <= p['startDate'][:4] <= period[-4:]]
        for board in BOARDS:
            days = [r for r in daily if (r['period'],r['board']) == (period,board)]
            for epoch in (('oldPeriod',) if period==PERIODS[0] else ('before20240924','from20240924')):
                subset = [d for d in days if d['formationEpoch']==epoch]
                for state in STATES:
                    count = sum(r['jointState']==state for r in subset)
                    epoch_days.append({'period':period,'board':board,'formationEpoch':epoch,'jointState':state,
                        'dayCount':count,'totalDays':len(subset),'daySharePct':count/len(subset)*100 if subset else None})
            for phase in applicable_phases:
                subset = [d for d in days if d['phaseId'] == phase['id']]
                for state in STATES:
                    phase_days.append({'period':period,'board':board,'phaseId':phase['id'],'jointState':state,
                        'days':sum(r['jointState'] == state for r in subset),'phaseDays':len(subset)})
            for old_state in ('stronger','weaker','equal','unknown'):
                for state in STATES:
                    relative.append({'period':period,'board':board,'relativeState':old_state,'jointState':state,
                        'days':sum(r['state']==old_state and r['jointState']==state for r in days)})
            for policy in POLICIES:
                rows = [r for r in all_rows if (r['period'],r['board'],r['policy']) == (period,board,policy)]
                key = {'period':period,'board':board,'policy':policy}
                total = aggregate(rows)
                old_bridge = next(r for r in parent['accountReconciliation'] if all(r[k]==v for k,v in key.items()))
                assert all(total[k] == old_bridge[k] for k in ('formationCount','entered','closed','open','notEntered'))
                assert math.isclose(total['totalPnlAmount']+old_bridge['unallocatedResidual'],old_bridge['accountNetPnlAmount'],abs_tol=1e-7)
                bridges.append(deepcopy(old_bridge))
                for state in STATES:
                    chosen = [r for r in rows if r['jointState']==state]
                    identity = {**key,'jointState':state}
                    overall.append({**identity,**aggregate(chosen)})
                    for year in period.split('-'):
                        yearly.append({**identity,'formationYear':year,**aggregate([r for r in chosen if r['formationDate'][:4]==year])})
                    for epoch in (('oldPeriod',) if period==PERIODS[0] else ('before20240924','from20240924')):
                        epochs.append({**identity,'formationEpoch':epoch,**aggregate([r for r in chosen if r['formationEpoch']==epoch])})
                    for phase in applicable_phases:
                        phase_rows.append({**identity,'phaseId':phase['id'],**aggregate([r for r in chosen if r['phaseId']==phase['id']])})
    for name, values in [('records.jsonl.gz',all_rows),('daily.jsonl.gz',daily)]:
        with gzip.open(OUT/name,'xt',encoding='utf-8',compresslevel=1) as stream:
            for row in values:
                stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    result = {'schema':OUT.name+'-readout','generatedAt':now(),'plan':ref(OUT/'plan.json'),
        'records':ref(OUT/'records.jsonl.gz'),'daily':ref(OUT/'daily.jsonl.gz'),'recordCount':len(all_rows),
        'formationCount':len(all_rows)//2,'dailyCount':len(daily),'states':list(STATES),'labels':LABELS,
        'phases':phases,'overallRows':overall,'formationYearRows':yearly,'epochStateRows':epochs,
        'phaseRows':phase_rows,'phaseDayRows':phase_days,'epochDayRows':epoch_days,'relativeJointRows':relative,
        'accountReconciliation':bridges,'curveEpochRows':deepcopy(parent['curveEpochRows']),
        'qualityRows':deepcopy(parent['qualityRows']),'trialAccounting':plan['trialAccounting'],
        'method':plan['rules'],'limitations':parent['limitations'], 'sourceReadout':plan['parentReadout'],
        'selectedState':None,'holdoutClaim':False,'certified':False,'targetAchieved':False}
    assert all(len(result[k])==n for k,n in plan['expectedRows'].items())
    verify_plan()
    write(OUT/'readout.json',result)
    return {'completed':True,'readout':ref(OUT/'readout.json'),'records':len(all_rows),'days':len(daily),**plan['expectedRows']}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode',choices=('self-test','register','build'))
    args = parser.parse_args()
    print(json.dumps({'self-test':self_test,'register':register,'build':build}[args.mode](),ensure_ascii=False))
