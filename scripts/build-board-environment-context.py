"""Build a causal, descriptive three-board panel; never run an account."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT/'artifacts/strategy-search'
OUT = BASE/'board-environment-context-v1'
PARENT = BASE/'reclaim-relative-feedback-context-v1'
PREVIOUS = BASE/'joint-cohort-regime-v1'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def bound(item):
    p = (ROOT/item['path']).resolve()
    assert p.is_relative_to(ROOT) and sha(p) == item['sha256'], item['path']
    return p


def line(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')


def register():
    previous = read(PREVIOUS/'delivery.json')
    assert sha(BASE/'program.json') == previous['programAfterSha256']
    for name, digest in previous['files'].items():
        assert sha(ROOT/name) == digest, name
    source = read(PARENT/'plan.json')
    proof = read(PARENT/'validation.json')
    assert proof['passed'] and proof['planSha256'] == sha(PARENT/'plan.json')
    paths = [ROOT/'docs/board-environment-context-v1.json', PARENT/'plan.json', PARENT/'validation.json',
        PREVIOUS/'delivery.json', PREVIOUS/'research-decision.json', PREVIOUS/'continuous-phase-context.json',
        PREVIOUS/'continuous-phase-validation.json', BASE/'market-phase-atlas-v1/atlas.json']
    feeds = {}
    for period in PERIODS:
        feeds[period] = {}
        for board in BOARDS:
            binding = source['sourceData'][period][board]
            for value in binding.values():
                if isinstance(value, dict) and 'path' in value:
                    paths.append(bound(value))
            folder = PARENT/period/board
            feeds[period][board] = {k: ref(folder/k) for k in ('daily-evidence.jsonl.gz', 'member-evidence.jsonl.gz')}
            for key, item in feeds[period][board].items():
                assert item['sha256'] == proof['pins'][item['path']], (period, board, key)
                paths.append(bound(item))
    source_names = ['scripts/build-board-environment-context.py', 'service/research_board_environment.py',
        'tests/test_research_board_environment.py', 'service/signal_leadership_features.py',
        'service/research_leader_lag.py', 'service/signal_market_state.py']
    protocol = read(ROOT/'docs/board-environment-context-v1.json')
    plan = {'schema': OUT.name, 'registeredAt': now(), 'previousTurnClassification': 'progress',
        'previousProgram': ref(BASE/'program.json'), 'protocol': protocol, 'sourceData': source['sourceData'],
        'frozenFeedback': feeds, 'periods': source['periods'],
        'sourcePins': {n: sha(ROOT/n) for n in source_names},
        'inputPins': {p.relative_to(ROOT).as_posix(): sha(p) for p in paths},
        'phaseSource': ref(BASE/'market-phase-atlas-v1/atlas.json'),
        'continuousAccounts': ref(PREVIOUS/'continuous-phase-context.json'),
        'expectedRows': {'daily': 2907, 'phase': 48, 'epoch': 9, 'continuousAccounts': 96},
        'newStrategyTrials': 0, 'newAccounts': 0, 'holdoutClaim': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT/'plan.json', plan)
    return {'registered': True, 'plan': ref(OUT/'plan.json')}


def validate_plan():
    plan = read(OUT/'plan.json')
    for mapping in ('sourcePins', 'inputPins'):
        for name, digest in plan[mapping].items():
            assert sha(ROOT/name) == digest, name
    return plan


def pretests():
    plan = validate_plan()
    run = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'unittest',
        'tests.test_research_board_environment', '-v'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    receipt = {'completedAt': now(), 'passed': run.returncode == 0, 'exitCode': run.returncode,
        'planSha256': sha(OUT/'plan.json'), 'sourcePins': plan['sourcePins'], 'stdout': run.stdout, 'stderr': run.stderr}
    write(OUT/'pre-extraction-tests.json', receipt)
    assert receipt['passed'], run.stdout+run.stderr
    return {'passed': True, 'stderr': run.stderr}


def describe(rows):
    """Equal-day descriptive statistics, retaining empty values and coverage."""
    def stat(accessor):
        values = [accessor(r) for r in rows]
        known = [v for v in values if v is not None]
        return {'knownDays': len(known), 'unknownDays': len(values)-len(known),
            'median': median(known) if known else None, 'mean': mean(known) if known else None,
            'minimum': min(known) if known else None, 'maximum': max(known) if known else None}
    return {'observedStart': rows[0]['formationDate'], 'observedEnd': rows[-1]['formationDate'],
        'asOfStart': rows[0]['asOfDate'], 'asOfEnd': rows[-1]['asOfDate'], 'sessions': len(rows),
        'fixedPopulation': rows[0]['fixedPopulation'],
        'breadth20': stat(lambda r: r['price20']['positiveReturn20Breadth']),
        'priceCoverage': stat(lambda r: r['price20']['coverage']),
        'amountRatio': stat(lambda r: r['amount']['medianRelativeToOwnPrior20']),
        'amountCoverage': stat(lambda r: r['amount']['coverage']),
        'matchedBreadthChange5': stat(lambda r: r['matched5']['matchedBreadthChange5']),
        'matchedCoverage': stat(lambda r: r['matched5']['pairedCoverageFixed']),
        'endpointLower': stat(lambda r: r['retention']['endpoint']['retentionLower']),
        'endpointUpper': stat(lambda r: r['retention']['endpoint']['retentionUpper']),
        'continuousLower': stat(lambda r: r['retention']['continuous']['retentionLower']),
        'continuousUpper': stat(lambda r: r['retention']['continuous']['retentionUpper']),
        'topPositiveLower': stat(lambda r: r['priorFeedback']['top']['positiveFractionLower']),
        'topPositiveUpper': stat(lambda r: r['priorFeedback']['top']['positiveFractionUpper']),
        'emptyFrozenCohortDays': sum(r['retention']['cohortSize'] == 0 for r in rows)}


def execute():
    from service.research_board_environment import build_board_environment
    plan = validate_plan()
    tests = read(OUT/'pre-extraction-tests.json')
    assert tests['passed'] and tests['planSha256'] == sha(OUT/'plan.json')
    write(OUT/'started.json', {'startedAt': now(), 'plan': ref(OUT/'plan.json')})
    phases = read(bound(plan['phaseSource']))['phases']
    artifacts, phase_rows, epoch_rows, daily_count = {}, [], [], 0
    for period in PERIODS:
        artifacts[period] = {}
        for board in BOARDS:
            print(json.dumps({'starting': [period, board]}, ensure_ascii=False), flush=True)
            binding = plan['sourceData'][period][board]
            dataset = read(bound(binding['accountData']))
            feed = plan['frozenFeedback'][period][board]
            members = {r['formationDate']: r for r in records(bound(feed['member-evidence.jsonl.gz']))}
            feedback = {r['formationDate']: r for r in records(bound(feed['daily-evidence.jsonl.gz']))}
            folder = OUT/period/board
            folder.mkdir(parents=True, exist_ok=False)
            dates = plan['periods'][period]
            with gzip.open(folder/'evidence.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as stream:
                built = build_board_environment(dataset, dates['startDate'], dates['endDate'], members,
                    on_evidence=lambda row: line(stream, row),
                    progress=lambda p: print(json.dumps({'period': period, 'board': board, **p}), flush=True))
            rows = built['dailyRows']
            assert len(rows) == len(members) == len(feedback)
            with gzip.open(folder/'daily.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as stream:
                for row in rows:
                    f = feedback[row['formationDate']]
                    assert row['asOfDate'] == f['asOfDate'] and row['cohortDate'] == f['cohortDate']
                    row.update(period=period, board=board, priorFeedback={k: f[k] for k in ('top', 'bottom', 'state', 'difference')})
                    line(stream, row)
            write(folder/'summary.json', {k: v for k, v in built.items() if k != 'dailyRows'})
            for phase in phases:
                selected = [r for r in rows if phase['startDate'] <= r['formationDate'] <= phase['endDate']]
                if selected:
                    phase_rows.append({'period': period, 'board': board, 'phaseId': phase['id'],
                        'phaseTitle': phase['title'], **describe(selected)})
            epochs = ('2022-2023',) if period == '2022-2023' else ('before20240924', 'from20240924')
            for epoch in epochs:
                selected = [r for r in rows if epoch == '2022-2023' or
                    ((r['formationDate'] < '2024-09-24') == (epoch == 'before20240924'))]
                epoch_rows.append({'period': period, 'board': board, 'epoch': epoch, **describe(selected)})
            artifacts[period][board] = {name: ref(folder/name) for name in ('daily.jsonl.gz', 'evidence.jsonl.gz', 'summary.json')}
            daily_count += len(rows)
            print(json.dumps({'completed': [period, board], 'days': len(rows)}, ensure_ascii=False), flush=True)
            del dataset, members, feedback, built, rows
            gc.collect()
    accounts = read(bound(plan['continuousAccounts']))['rows']
    lookup = {(r['period'], r['board'], r['phaseId']): r for r in phase_rows}
    assert len(lookup) == 48 and len(accounts) == 96 and daily_count == 2907 and len(epoch_rows) == 9
    for row in accounts:
        env = lookup[row['period'], row['board'], row['phaseId']]
        assert (env['observedStart'], env['observedEnd'], env['sessions']) == (row['observedStart'], row['observedEnd'], row['observedSessions'])
    report = {'schema': OUT.name, 'completedAt': now(), 'plan': ref(OUT/'plan.json'), 'artifacts': artifacts,
        'dailyCount': daily_count, 'phaseRows': phase_rows, 'epochRows': epoch_rows,
        'continuousAccountRows': accounts, 'continuousAccountSource': plan['continuousAccounts'],
        'aggregation': 'Daily cross-sectional statistics summarized by equal-day mean and median. Missingness bounds are descriptive, not confidence intervals; phase attribution is by T with information through T-1.',
        'newStrategyTrials': 0, 'newAccounts': 0, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    validate_plan()
    write(OUT/'readout.json', report)
    return {'completed': True, 'dailyCount': daily_count, 'phaseRows': len(phase_rows), 'readout': ref(OUT/'readout.json')}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('action', choices=('register', 'test', 'run'))
    args = parser.parse_args()
    print(json.dumps({'register': register, 'test': pretests, 'run': execute}[args.action](), ensure_ascii=False))
