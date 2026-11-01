"""Isolated, fixed all-opportunity label diagnostic; not a portfolio search."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'independent-shadow-labels-v1'
PARENT = BASE / 'online-feedback-entry-v1'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
EXITS = ('trail10', 'volatilityTrail')
PARENT_PLAN_SHA = 'c9abd252f3b017948c61332b500150ceaa30862d8291c097f16938678100d53c'
PARENT_DELIVERY_SHA = 'e74d322942c5b24f90e9394bbb661b6276b614e3349edb2eb7e58d271db4e564'
PROGRAM_SHA = '382199a9664acf750b38a160a40c557076fe28aa804da663df5dc4789f1eab7d'


def now(): return datetime.now(timezone.utc).isoformat()
def require(ok, message):
    if not ok: raise ValueError(message)
def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()
def ref(path): return {'path': Path(path).resolve().relative_to(ROOT).as_posix(), 'sha256': sha(path)}
def read(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream: return json.load(stream)
def records(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream: return [json.loads(line) for line in stream if line.strip()]
def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
def bound(item):
    path = ROOT / item['path']; require(sha(path) == item['sha256'], 'Changed input: '+item['path']); return path
def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value
def emit(value): print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def register():
    require(not OUT.exists(), 'Diagnostic already registered')
    require(sha(PARENT/'plan.json') == PARENT_PLAN_SHA, 'Parent plan changed')
    require(sha(PARENT/'delivery.json') == PARENT_DELIVERY_SHA, 'Parent delivery changed')
    require(sha(BASE/'program.json') == PROGRAM_SHA, 'Registry advanced')
    parent = module('run-online-feedback-entry.py').verify_plan()
    added = ['docs/independent-shadow-labels-v1.json', 'service/research_independent_paths.py',
             'tests/test_research_independent_paths.py', 'scripts/run-independent-shadow-labels.py']
    pins = {**parent['sourcePins'], **{name: sha(ROOT/name) for name in added}}
    OUT.mkdir(parents=True)
    write(OUT/'plan.json', {'schema': OUT.name+'-plan', 'registeredAt': now(),
        'protocol': read(ROOT/added[0]), 'parentPlan': ref(PARENT/'plan.json'),
        'parentDelivery': ref(PARENT/'delivery.json'), 'registryBefore': ref(BASE/'program.json'),
        'parentInputCompletion': ref(PARENT/'input-completion.json'),
        'parentInputValidation': ref(PARENT/'input-validation.json'),
        'sourcePins': pins, 'opportunities': parent['opportunities'],
        'sourceData': parent['sourceData'], 'metadata': parent['metadata'],
        'environment': parent['environment'], 'baselineSchedules': parent['baselineSchedules'],
        'periods': parent['periods'], 'expectedFormations': 8155, 'expectedPaths': 16310,
        'newCandidateAccounts': 0, 'candidateTrialCountUnchanged': 39,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False})
    shutil.copy2(BASE/'program.json', OUT/'program-before.json')
    for name in pins:
        target = OUT/'sources'/name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT/name, target)
    emit({'registered': ref(OUT/'plan.json')})


def verify_plan():
    p = read(OUT/'plan.json')
    bound(p['parentPlan']); bound(p['parentDelivery'])
    for name, expected in p['sourcePins'].items():
        require(sha(ROOT/name) == expected, 'Changed frozen source: '+name)
    return p


def pretests():
    verify_plan()
    names = ['tests.test_research_independent_paths', 'tests.test_research_fixed_signal_account',
             'tests.test_research_account_fees', 'tests.test_research_volatility_trail',
             'tests.test_research_online_feedback']
    command = [sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', *names]
    started = now()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    log = result.stdout + result.stderr
    (OUT/'pre-path-tests.log').write_text(log, encoding='utf-8')
    match = re.search(r'Ran (\d+) tests?', log)
    receipt = {'startedAt': started, 'completedAt': now(), 'planSha256': sha(OUT/'plan.json'),
        'command': command, 'modules': names, 'testsRun': int(match.group(1)) if match else None,
        'exitCode': result.returncode, 'passed': result.returncode == 0 and match is not None,
        'log': ref(OUT/'pre-path-tests.log')}
    write(OUT/'pre-path-tests.json', receipt)
    require(receipt['passed'], 'Synthetic or inherited test failure; see pre-path-tests.log')
    emit(receipt)


def run(only_group=None):
    from service.research_independent_paths import run_independent_path
    from service.research_account_fees import LegacyFeePolicy
    p = verify_plan()
    tests = read(OUT/'pre-path-tests.json')
    require(tests['passed'] and tests['planSha256'] == sha(OUT/'plan.json'), 'Pre-path tests absent or changed')
    original = records(bound(p['opportunities']))
    require(len(original) == p['expectedFormations'], 'Formation count differs')
    groups = []
    for period in PERIODS:
        fee = LegacyFeePolicy()
        if period == '2022-2023':
            helper = module('load-market-context-screen.py')._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        for board in BOARDS:
            requested = [(period, board, e) for e in EXITS if only_group is None or
                         only_group in (f'{period}/{board}', f'{period}/{board}/{e}')]
            if not requested: continue
            incomplete = [e for _,_,e in requested if not (OUT/period/board/e/'completion.json').exists()]
            if incomplete:
                data = read(bound(p['sourceData'][period][board]['accountData']))
                by_symbol = {s['symbol']: s for s in data['securities']}
            rows = [r for r in original if r['period'] == period and r['board'] == board]
            schedule = read(bound(p['baselineSchedules'][period][board]))
            require(schedule == [r['signals']['immediate'] for r in rows], 'Signal binding mismatch')
            for _,_,exit_name in requested:
                folder = OUT/period/board/exit_name
                if (folder/'completion.json').exists():
                    completed = read(folder/'completion.json'); bound(completed['paths']); groups.append(completed); continue
                require(not folder.exists(), 'Incomplete prior path group; retain it and investigate before replay')
                folder.mkdir(parents=True)
                write(folder/'started.json', {'startedAt': now(), 'planSha256': sha(OUT/'plan.json'), 'paths': len(rows)})
                counts = Counter(); started = time.monotonic()
                with gzip.open(folder/'paths.jsonl.gz', 'wt', encoding='utf-8', compresslevel=6) as stream:
                    for index, r in enumerate(rows, 1):
                        # One security is a source view, never a shortened history.
                        view = {**data, 'securities': [by_symbol[r['symbol']]]}
                        value = run_independent_path(view, r['signals']['immediate'],
                            deepcopy(p['periods'][period]['policyConfigs'][exit_name]), exit_policy=exit_name, fee_policy=fee)
                        outcome = value['outcome']
                        require(not outcome['labelEligible'] or outcome['labelAvailableDate'] == outcome['finalExitDate'],
                                'Delayed settlement is outside this final-exit-clock diagnostic')
                        original_disposition = r['accountDispositions'][exit_name]
                        row = {'period': period, 'board': board, 'exit': exit_name,
                            'formationId': r['formationId'], 'formationDate': r['formationDate'], 'symbol': r['symbol'],
                            'originalEntered': original_disposition['filledQuantity'] > 0,
                            'originalDisposition': original_disposition, 'path': value}
                        stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')
                        counts[value['status']] += 1
                        if index % 100 == 0 or index == len(rows):
                            emit({'progress': [period, board, exit_name], 'paths': index, 'total': len(rows),
                                  'elapsedSeconds': round(time.monotonic()-started, 2), 'counts': dict(counts)})
                completed = {'period': period, 'board': board, 'exit': exit_name,
                    'completedAt': now(), 'planSha256': sha(OUT/'plan.json'), 'paths': ref(folder/'paths.jsonl.gz'),
                    'pathCount': len(rows), 'counts': dict(counts), 'newCandidateAccounts': 0}
                write(folder/'completion.json', completed); groups.append(completed)
            if incomplete:
                del by_symbol, data; gc.collect()
    verify_plan()
    if only_group is None:
        require(len(groups) == 12 and sum(g['pathCount'] for g in groups) == 16310, 'Incomplete library')
        write(OUT/'library-completion.json', {'schema': OUT.name+'-library', 'completedAt': now(),
            'planSha256': sha(OUT/'plan.json'), 'groups': groups, 'independentPathExecutions': 16310,
            'newCandidateAccounts': 0, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False})
    emit({'completedGroups': len(groups), 'newCandidateAccounts': 0})


if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('mode', choices=('register', 'pretests', 'run'))
    parser.add_argument('--group'); args = parser.parse_args()
    try:
        run(args.group) if args.mode == 'run' else globals()[args.mode]()
    except Exception as error:
        if OUT.exists():
            write(OUT/'failures'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'),
                {'at': now(), 'mode': args.mode, 'type': type(error).__name__, 'message': str(error)})
        raise
