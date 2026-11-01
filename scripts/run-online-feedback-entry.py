"""One fixed online entry-permission experiment; isolated historical accounts."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'online-feedback-entry-v1'
ENV = BASE / 'board-environment-context-v1'
PARENT = BASE / 'reclaim-volatility-trail-v1'
PROTOCOL = ROOT / 'docs/online-feedback-entry-v1.json'
PROGRAM_SHA = 'b2c60b262b6b2275f098ade709da2202b37d261e2a8d2f9031acbcb7cc41a7c2'
PREVIOUS_DELIVERY_SHA = '9d40d2257dc71ac201a111e531825b4166f7180b2cdb8413e5d01a40dd04ccb8'
PERIODS, BOARDS = ('2022-2023', '2024-2025'), ('main', 'chinext', 'star')
EXITS, MODES = ('trail10', 'volatilityTrail'), ('intercept', 'ridge')
VERIFIER = 'scripts/verify-online-feedback-entry.py'


def now(): return datetime.now(timezone.utc).isoformat()


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def ref(path): return {'path': Path(path).resolve().relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    p = (ROOT/item['path']).resolve()
    require(p.is_relative_to(ROOT) and sha(p) == item['sha256'], 'Changed input: '+item['path'])
    return p


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as f:
        return json.load(f)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def write(path, value):
    with (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) if str(path).endswith('.gz') else Path(path).open('x', encoding='utf-8')) as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=None if str(path).endswith('.gz') else 2)
        f.write('\n')


def lines(path, values):
    with gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) as f:
        for value in values:
            f.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')


def module(name):
    spec = importlib.util.spec_from_file_location('_online_'+name.replace('-', '_').replace('.', '_'), ROOT/'scripts'/name)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def emit(value): print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def verify_plan():
    p = read(OUT/'plan.json')
    for key in ('sourcePins', 'inputPins'):
        for name, h in p[key].items(): bound({'path': name, 'sha256': h})
    return p


def register():
    require(sha(BASE/'program.json') == PROGRAM_SHA and sha(ENV/'delivery.json') == PREVIOUS_DELIVERY_SHA, 'Predecessor changed')
    delivery = read(ENV/'delivery.json')
    for n, h in delivery['files'].items(): bound({'path': n, 'sha256': h})
    previous, comparison = read(PARENT/'plan.json'), read(PARENT/'comparison.json')
    environment = read(ENV/'readout.json')
    require(read(PARENT/'validation.json')['passed'] and read(PARENT/'readout-validation.json')['passed'], 'Baseline audit missing')
    require(read(ENV/'validation.json')['passed'], 'Environment audit missing')
    protocol = read(PROTOCOL)
    sources = dict(previous['sourcePins'])
    files = ['scripts/run-online-feedback-entry.py', VERIFIER, 'service/research_online_feedback.py',
        'tests/test_research_online_feedback.py', 'tests/test_online_feedback_entry_runner.py', 'service/research_account_outcomes.py',
        'scripts/run-supplemental-force-context.py', 'scripts/load-market-context-screen.py',
        'scripts/run-direction-account-screen.py', 'scripts/verify-reclaim-exit-mechanism.py',
        'scripts/summarize-online-feedback-entry.py', 'scripts/summarize-undercut-reclaim-timing.py',
        'scripts/verify-peer-amount-persistence.py', 'scripts/verify-relative-repair-cost-stress.py',
        'scripts/verify-signal-leader-peer-expansion-result.py',
        'scripts/verify-relative-repair-screen.py', 'scripts/verify-market-context-screen.py',
        'scripts/verify-supplemental-force-context.py', 'scripts/verify-signal-market-repair-result.py',
        'scripts/verify-signal-persistent-leader-result.py', 'scripts/verify-signal-session-decomposition-result.py',
        'docs/online-feedback-entry-v1.json']
    sources.update({n: sha(ROOT/n) for n in files})
    inputs = dict(previous['inputPins'])
    paths = [PARENT/n for n in ('plan.json', 'comparison.json', 'validation.json', 'readout.json', 'readout-validation.json')]
    paths += [ENV/n for n in ('plan.json', 'delivery.json', 'readout.json', 'validation.json', 'research-decision.json')]
    paths += [BASE/'online-feedback-feasibility-v1/support-audit.json', BASE/'market-phase-atlas-v1/atlas.json']
    opportunities = read(PARENT/'readout.json')['opportunityRowsArtifact']
    paths.append(bound(opportunities))
    env_data, metadata = {}, {}
    for period in PERIODS:
        env_data[period], metadata[period] = {}, {}
        for board in BOARDS:
            env_source = read(ENV/'plan.json')['sourceData'][period][board]
            require(env_source['accountData'] == previous['sourceData'][period][board]['accountData'] and
                env_source['accountDatasetDigest'] == previous['sourceData'][period][board]['accountDatasetDigest'], 'Context/account source differs')
            env_data[period][board] = environment['artifacts'][period][board]['daily.jsonl.gz']
            metadata[period][board] = ref(BASE/'board-coverage-reclaim-v1'/period/board/'signal-metadata.json')
            paths += [bound(env_data[period][board]), bound(metadata[period][board]), bound(previous['baselineSchedules'][period][board])]
            paths.append(bound(previous['sourceData'][period][board]['accountData']))
            for exit_name in EXITS:
                for item in comparison['artifacts'][period][board][exit_name].values(): paths.append(bound(item))
    inputs.update({p.relative_to(ROOT).as_posix(): sha(p) for p in paths})
    plan = {'schema': OUT.name, 'registeredAt': now(), 'previousTurnClassification': 'progress',
        'previousProgram': ref(BASE/'program.json'), 'previousDelivery': ref(ENV/'delivery.json'),
        'protocol': ref(PROTOCOL), 'rules': protocol, 'sourcePins': sources, 'inputPins': inputs,
        'periods': deepcopy(previous['periods']), 'boards': list(BOARDS), 'exits': list(EXITS), 'candidates': list(MODES),
        'sourceData': deepcopy(previous['sourceData']), 'baselineSchedules': deepcopy(previous['baselineSchedules']),
        'accountArtifacts': deepcopy(comparison['artifacts']), 'environment': env_data, 'metadata': metadata,
        'opportunities': opportunities, 'atlas': ref(BASE/'market-phase-atlas-v1/atlas.json'),
        **protocol['trialAccounting'], 'parameterSearch': False, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT/'plan.json', plan)
    return {'registered': True, 'plan': ref(OUT/'plan.json')}


def pretests():
    p = verify_plan()
    started = now()
    commands = [[sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', 'tests.test_research_online_feedback',
                 'tests.test_online_feedback_entry_runner', '-v'],
        [sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', 'tests.test_research_fixed_signal_account',
         'tests.test_research_account_fees', 'tests.test_research_account_outcomes', 'tests.test_research_volatility_trail', '-v'],
        [sys.executable, '-X', 'utf8', '-B', VERIFIER, '--self-test'],
        [sys.executable, '-X', 'utf8', '-B', 'scripts/summarize-online-feedback-entry.py', '--self-test']]
    results = []
    for argv in commands:
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding='utf-8')
        results.append({'argv': argv, 'exitCode': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr})
        require(r.returncode == 0, 'Pre-account check failed: '+r.stdout+r.stderr)
    new_count = int(re.search(r'Ran (\d+) tests', results[0]['stderr']).group(1))
    inherited = int(re.search(r'Ran (\d+) tests', results[1]['stderr']).group(1))
    require(new_count > 0 and inherited > 0, 'Missing meaningful tests')
    verify_plan()
    write(OUT/'pre-account-tests.json', {'passed': True, 'startedAt': started, 'completedAt': now(), 'planSha256': sha(OUT/'plan.json'),
        'sourcePins': p['sourcePins'], 'commands': results, 'newUnitTests': new_count, 'inheritedUnitTests': inherited,
        'allChecksBeforeFirstNewAccount': True})
    return {'passed': True, 'newUnitTests': new_count, 'inheritedUnitTests': inherited}


def shadow_rows(original, exit_name, summary):
    closed = {(e['record']['symbol'], e['record']['entryDate'], e['record']['exitDate']): e for e in summary['closedEpisodes']['episodes']}
    opened = {(e['record']['symbol'], e['record']['entryDate']) for e in summary['openPositions']['positions']}
    require(len(closed) == summary['closedEpisodes']['count'], 'Duplicate baseline episode')
    seen, seen_open, output = set(), set(), []
    for item in original:
        d = item['accountDispositions'][exit_name]
        if not d['filledQuantity']: continue
        e = {'formationId': item['formationId'], 'formationDate': item['formationDate'],
             'entryDate': d['entryDate'], 'exitDate': None, 'netReturn': None}
        if d['positionState'] == 'closed':
            key = item['symbol'], d['entryDate'], d['exitDate']
            require(key in closed and key not in seen, 'Missing or repeated shadow closed identity')
            raw = closed[key]; seen.add(key)
            y = raw['record']['pnlAmount']/raw['record']['investedAmount']
            require(math.isclose(y*100, d['closedReturnPct'], abs_tol=1e-9) and
                    math.isclose(y*100, raw['netReturnPct'], abs_tol=1e-9), 'Net label differs')
            e.update(exitDate=d['exitDate'], netReturn=y)
        else:
            key = item['symbol'], d['entryDate']
            require(d['positionState'] == 'openAtCutoff' and key in opened and key not in seen_open, 'Unknown shadow open state')
            seen_open.add(key)
        output.append(e)
    require(seen == set(closed) and seen_open == opened, 'Unassigned shadow episode')
    return output


def select_schedule(schedule, decisions, mode):
    require(mode in MODES, 'Unknown feedback policy')
    by_day = {r['formationDate']: r for r in decisions}
    require(len(by_day) == len(decisions), 'Duplicate model decision date')
    require(all(s['signalDate'] in by_day for s in schedule), 'Signal has no decision')
    require(all(type(by_day[s['signalDate']]['models'][mode]['allow']) is bool for s in schedule), 'Admission must be Boolean')
    return [deepcopy(s) for s in schedule if by_day[s['signalDate']]['models'][mode]['allow']]


def prepare():
    from service.research_online_feedback import build_online_feedback
    p = verify_plan(); t = read(OUT/'pre-account-tests.json')
    require(t['passed'] and t['planSha256'] == sha(OUT/'plan.json'), 'Missing pretests')
    original = records(bound(p['opportunities'])); groups = []
    require(len(original) == 8155, 'Original formation count differs')
    for period in PERIODS:
        for board in BOARDS:
            rows = [r for r in original if r['period'] == period and r['board'] == board]
            schedule = read(bound(p['baselineSchedules'][period][board]))
            require(schedule == [r['signals']['immediate'] for r in rows], 'Baseline formation binding differs')
            md = read(bound(p['metadata'][period][board])); env = records(bound(p['environment'][period][board]))
            for exit_name in EXITS:
                folder = OUT/period/board/exit_name; folder.mkdir(parents=True, exist_ok=False)
                baseline = p['accountArtifacts'][period][board][exit_name]
                shadow = shadow_rows(rows, exit_name, read(bound(baseline[exit_name+'.summary.json'])))
                value = build_online_feedback(md['calendar'], md['targetDates'], env, shadow)
                decisions = value['dailyRows']; by_day = {r['formationDate']: r for r in decisions}
                require(len(by_day) == len(md['targetDates']), 'Decision calendar differs')
                write(folder/'shadow-episodes.json', shadow); lines(folder/'decisions.jsonl.gz', decisions)
                write(folder/'model-metadata.json', {k:v for k,v in value.items() if k != 'dailyRows'})
                schedules = {}
                for mode in MODES:
                    selected = select_schedule(schedule, decisions, mode)
                    write(folder/(mode+'.signals.json'), selected)
                    schedules[mode] = ref(folder/(mode+'.signals.json'))
                group = {'period': period, 'board': board, 'exit': exit_name, 'baseline': baseline,
                    'baselineSchedule': p['baselineSchedules'][period][board], 'accountData': p['sourceData'][period][board]['accountData'],
                    'decisions': ref(folder/'decisions.jsonl.gz'), 'shadow': ref(folder/'shadow-episodes.json'),
                    'modelMetadata': ref(folder/'model-metadata.json'), 'candidates': schedules}
                groups.append(group)
                emit({'prepared': [period, board, exit_name], 'shadowEpisodes': len(shadow),
                    'signals': {k:len(read(bound(v))) for k,v in schedules.items()}})
    verify_plan()
    write(OUT/'input-completion.json', {'planSha256': sha(OUT/'plan.json'), 'completedAt': now(),
        'groups': groups, 'allSchedulesFrozenBeforeAccounts': True, 'newAccountsExecuted': 0})
    return {'prepared': True, 'groups': len(groups), 'completion': ref(OUT/'input-completion.json')}


def intervals(period, atlas):
    first, last = map(int, period.split('-')); result = []
    for y in range(first, last+1):
        result += [{'id': str(y), 'start': f'{y}-01-01', 'end': f'{y}-12-31', 'kind': 'calendarYear'},
            {'id': f'{y}-H1', 'start': f'{y}-01-01', 'end': f'{y}-06-30', 'kind': 'calendarHalfYear'},
            {'id': f'{y}-H2', 'start': f'{y}-07-01', 'end': f'{y}-12-31', 'kind': 'calendarHalfYear'}]
    result += [{'id': x['id'], 'start': x['startDate'], 'end': x['endDate'],
                'kind': 'retrospectiveNarrativePhase', 'title': x['title']} for x in atlas['phases']
               if str(first) <= x['startDate'][:4] <= str(last)]
    if first == 2024:
        result += [{'id':'before20240924','start':'2024-01-02','end':'2024-09-23','kind':'descriptiveEpoch'},
                   {'id':'from20240924','start':'2024-09-24','end':'2025-12-31','kind':'descriptiveEpoch'}]
    return result


def run():
    from service.research_fixed_signal_account import FixedSignalAccount
    from service.research_volatility_trail import VolatilityTrailAccount
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    p = verify_plan(); completion = read(OUT/'input-completion.json'); validation = read(OUT/'input-validation.json')
    require(validation['passed'] and validation['planSha256'] == sha(OUT/'plan.json') and
        validation['inputCompletionSha256'] == sha(OUT/'input-completion.json') and
        validation['verifierSha256'] == sha(ROOT/VERIFIER), 'Input audit missing or changed')
    require(completion['allSchedulesFrozenBeforeAccounts'] and len(completion['groups']) == 12, 'Input completion differs')
    tests = read(OUT/'pre-account-tests.json')
    require(tests['passed'] and tests['planSha256'] == sha(OUT/'plan.json'), 'Pretests differ')
    write(OUT/'run-start.json', {'startedAt': now(), 'inputValidation': ref(OUT/'input-validation.json'),
        'inputCompletion': ref(OUT/'input-completion.json'), 'tests': ref(OUT/'pre-account-tests.json')})
    atlas = read(bound(p['atlas'])); all_rows = []
    cached_key, data = None, None
    for group in completion['groups']:
        period, board, exit_name = (group[k] for k in ('period', 'board', 'exit'))
        key = period, board
        if key != cached_key:
            if data is not None: del data; gc.collect()
            data = read(bound(group['accountData'])); cached_key = key
            require(digest(data) == p['sourceData'][period][board]['accountDatasetDigest'], 'Source digest differs')
        if period == '2022-2023':
            helper = module('load-market-context-screen.py')._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        else:
            fee = LegacyFeePolicy()
        folder = OUT/period/board/exit_name
        for mode in ('baseline', *MODES):
            schedule_ref = group['baselineSchedule'] if mode == 'baseline' else group['candidates'][mode]
            schedule = read(bound(schedule_ref))
            if mode == 'baseline':
                result_ref = group['baseline'][exit_name+'.result.json.gz']
                checkpoint_ref = group['baseline'][exit_name+'.checkpoint.json.gz']
                result, checkpoint = read(bound(result_ref)), read(bound(checkpoint_ref))
            else:
                emit({'accountStart': [period, board, exit_name, mode], 'signals': len(schedule)})
                cls = FixedSignalAccount if exit_name == 'trail10' else VolatilityTrailAccount
                engine = cls(data, schedule, deepcopy(p['periods'][period]['policyConfigs'][exit_name]), entry_weight=.2, fee_policy=fee)
                engine.step(20000); result, checkpoint = engine.result(), engine.checkpoint()
                require(result['status'] == 'completed' and result['research']['identity']['signalSchedule'] == schedule, 'Account incomplete')
                write(folder/(mode+'.result.json.gz'), result); write(folder/(mode+'.checkpoint.json.gz'), checkpoint)
                result_ref, checkpoint_ref = ref(folder/(mode+'.result.json.gz')), ref(folder/(mode+'.checkpoint.json.gz'))
                del engine
            summary = summarize_account(result, checkpoint, policy_id=mode, policy_label=mode,
                                        intervals=intervals(period, atlas))
            require(not summary['quality']['integrityIssues'], 'Accounting integrity failure')
            write(folder/(mode+'.summary.json'), summary)
            row = {'period': period, 'board': board, 'exit': exit_name, 'candidate': mode,
                'summary': ref(folder/(mode+'.summary.json')), 'result': result_ref, 'checkpoint': checkpoint_ref,
                'schedule': schedule_ref, 'reusedAccount': mode == 'baseline'}
            all_rows.append(row)
            emit({'accountComplete':[period,board,exit_name,mode], 'returnPct': summary['account']['netReturnPct'],
                  'drawdownPct':summary['account']['maxDrawdownPct'], 'closed':summary['closedEpisodes']['count']})
            del result, checkpoint, summary; gc.collect()
        require(digest(data) == p['sourceData'][period][board]['accountDatasetDigest'], 'Account mutated data')
    verify_plan()
    write(OUT/'comparison.json', {'schema': OUT.name+'-comparison', 'completedAt': now(), 'planSha256': sha(OUT/'plan.json'),
        'rows': all_rows, 'newAccounts': 24, 'referenceAccountsReused': 12,
        'newEntryPolicies': 2, 'experimentBatches': 1, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False})
    return {'completed': True, 'accounts':len(all_rows), 'comparison':ref(OUT/'comparison.json')}


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('mode', choices=('register','pretests','prepare','run'))
    args = parser.parse_args()
    try: emit(globals()[args.mode]())
    except Exception as error:
        if OUT.exists():
            failures = OUT/'failures'; failures.mkdir(exist_ok=True)
            write(failures/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'),
                  {'at':now(),'mode':args.mode,'type':type(error).__name__,'message':str(error)})
        raise
