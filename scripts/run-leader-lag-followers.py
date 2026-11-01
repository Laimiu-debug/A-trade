"""Freeze and compare pre-event leader/follower direction on all six board sources."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import gzip
import importlib.util
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('_lag_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, write_record, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.write_record, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'board-coverage-reclaim-v1'
PREVIOUS = BASE / 'volume-anchor-acceptance-v1'
OUT = BASE / 'leader-lag-followers-v1'
PROTOCOL = ROOT / 'docs/signal-leader-lag-followers-v1.json'
CONTEXT = ROOT / 'docs/leader-lag-followers-context.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('control', 'direction')
SID = 'leaderLagFollowersV1'
PROGRAM_SHA = 'd5419d718bc2592dc0e6aa9b5600a9b07a87970c93118bc243e812be475d1625'
DELIVERY_SHA = '96689caa29b167b32c8d3a242b74102d8da0c517c8ab1dec02b5f0a383847890'


def verify_plan():
    plan = read(OUT / 'plan.json')
    for key in ('sourcePins', 'inputPins'):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    assert sha(BASE / 'program.json') == PROGRAM_SHA
    assert sha(PREVIOUS / 'delivery.json') == DELIVERY_SHA
    parent, protocol = read(PARENT / 'plan.json'), read(PROTOCOL)
    assert protocol['strategyId'] == SID and tuple(protocol['modes']) == MODES
    source_proof = read(PARENT / 'source-validation.json')
    assert source_proof['passed'] and read(PARENT / 'validation.json')['passed']
    assert read(PARENT / 'readout-validation.json')['passed']
    assert read(PREVIOUS / 'validation.json')['passed']
    assert read(PREVIOUS / 'readout-validation.json')['passed']
    periods, source_data = {}, {}
    for period in PERIODS:
        config = deepcopy(parent['periods'][period]['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        periods[period] = {'startDate': config['startDate'], 'endDate': config['endDate'], 'config': config}
        manifest = read(PARENT / period / 'source-manifest.json')
        source_data[period] = {}
        for board in BOARDS:
            cache = PARENT / period / (board + '.account-data.json.gz')
            assert sha(cache) == source_proof['datasetPins'][cache.relative_to(ROOT).as_posix()]
            source_data[period][board] = {'accountData': ref(cache),
                'accountDatasetDigest': manifest['boards'][board]['accountData']['datasetDigest'],
                'sourceManifest': ref(PARENT / period / 'source-manifest.json'),
                'sourceValidation': ref(PARENT / 'source-validation.json'),
                'members': manifest['boards'][board]['includedCount']}
    files = list(parent['sourcePins']) + ['scripts/run-leader-lag-followers.py',
        'service/research_leader_lag.py', 'tests/test_research_leader_lag.py',
        'scripts/verify-leader-lag-followers.py', 'scripts/summarize-leader-lag-followers.py',
        'scripts/verify-board-coverage-reclaim.py', 'scripts/verify-undercut-reclaim-timing.py',
        'scripts/verify-peer-amount-persistence.py', 'scripts/summarize-volume-anchor-acceptance.py',
        'scripts/summarize-board-coverage-reclaim.py', 'scripts/summarize-undercut-reclaim-timing.py']
    inputs = [PROTOCOL, CONTEXT, PARENT / 'plan.json', PARENT / 'delivery.json', PARENT / 'source-completion.json',
        PARENT / 'source-validation.json', PARENT / 'validation.json', PARENT / 'readout-validation.json',
        BASE / 'market-phase-atlas-v1/atlas.json', PREVIOUS / 'delivery.json',
        PREVIOUS / 'validation.json', PREVIOUS / 'readout-validation.json']
    inputs += [PARENT / p / 'source-manifest.json' for p in PERIODS]
    inputs += [PARENT / p / (b + '.account-data.json.gz') for p in PERIODS for b in BOARDS]
    plan = {'schema': 'leader-lag-followers-v1', 'registeredAt': now(), 'strategyId': SID,
        'protocol': ref(PROTOCOL), 'marketContext': ref(CONTEXT), 'rules': protocol, 'modes': list(MODES), 'boards': list(BOARDS),
        'labels': protocol['labels'], 'boardLabels': parent['labels'], 'periods': periods,
        'sourceData': source_data, 'sourcePins': {name: sha(ROOT / name) for name in dict.fromkeys(files)},
        'inputPins': {path.relative_to(ROOT).as_posix(): sha(path) for path in inputs},
        'newAccounts': 12, 'newSetupMechanisms': 1, 'newEntryPolicies': 2,
        'newUniverseVariants': 0, 'referenceAccountsReused': 0,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'previousTurn': {'classification': 'progress', 'evidence': ref(PREVIOUS / 'delivery.json'),
            'meaning': 'Completed18 volume-anchor accounts and their independent checks without promotion. New lag test keeps all six sources and the common execution rules.'},
        'limitations': list(parent['limitations']) + [
            'Same original raw-price/rights/execution limitations and valuation quality flags remain; arithmetic audit cannot certify economic validity.',
            *protocol['limitations'],
            'Direction versus control uses the same original member Top5, common complete relation evidence and next-open execution; there is no replacement of masked slots.',
            'The 2024-09-24 event boundary is a descriptive cut of continuous equity and positions, never an entry gate.',
            'No board or policy is promoted from these already inspected historical periods.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'registered': True, **ref(OUT / 'plan.json')})


def prepare():
    from service.research_leader_lag import build_leader_lag_followers
    plan, outputs = verify_plan(), {}
    for period in PERIODS:
        outputs[period] = {}
        for board in BOARDS:
            folder = OUT / period / board
            folder.mkdir(parents=True, exist_ok=False)
            binding = plan['sourceData'][period][board]
            data = read(ROOT / binding['accountData']['path'])
            assert digest(data) == binding['accountDatasetDigest']
            config = plan['periods'][period]['config']
            emit({'phase': 'signal-start', 'period': period, 'board': board, 'stocks': len(data['securities'])})
            with gzip.open(folder / 'group-evidence.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as group_stream:
                evidence = build_leader_lag_followers(data, start=config['startDate'], end=config['endDate'],
                    progress=lambda value: emit({'phase': 'signal-progress', 'period': period, 'board': board, **value}),
                    on_group=lambda value: write_record(group_stream, value))
            assert evidence['strategyId'] == SID and tuple(evidence['modes']) == MODES
            for name, rows in (('opportunities.jsonl.gz', evidence['opportunities']),
                               ('daily-evidence.jsonl.gz', evidence['dailyRows']),
                               ('leader-events.jsonl.gz', evidence['leaderEvents']),
                               ('rank-evidence.jsonl.gz', evidence['rankRows']),
                               ('member-edges.jsonl.gz', evidence['memberEdges'])):
                with gzip.open(folder / name, 'xt', encoding='utf-8', compresslevel=1) as stream:
                    for row in rows:
                        write_record(stream, row)
            write(folder / 'signal-metadata.json', {k: v for k, v in evidence.items()
                if k not in ('opportunities', 'dailyRows', 'leaderEvents', 'rankRows', 'memberEdges', 'schedules')})
            for mode in MODES:
                write(folder / (mode + '.signals.json'), evidence['schedules'][mode])
            write(folder / 'input-counts.json', evidence['counts'])
            write(folder / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
                'period': period, 'board': board, 'counts': evidence['counts'], **binding,
                'files': {path.name: ref(path) for path in folder.iterdir() if path.is_file()}})
            outputs[period][board] = ref(folder / 'input-completion.json')
            emit({'phase': 'signal-complete', 'period': period, 'board': board,
                'opportunities': len(evidence['opportunities']),
                'signals': {mode: len(evidence['schedules'][mode]) for mode in MODES}})
            assert digest(data) == binding['accountDatasetDigest'], 'Signal generation mutated frozen data'
            del data, evidence
            gc.collect()
    verify_plan()
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': outputs, 'allSchedulesFrozenBeforeAccounts': True, 'newAccounts': 12})


def run():
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan()
    completion, validation = read(OUT / 'input-completion.json'), read(OUT / 'input-validation.json')
    assert validation['passed'] and validation['planSha256'] == sha(OUT / 'plan.json')
    assert validation['inputCompletionSha256'] == sha(OUT / 'input-completion.json')
    assert completion['allSchedulesFrozenBeforeAccounts']
    write(OUT / 'run-start.json', {'startedAt': now(), 'inputCompletion': ref(OUT / 'input-completion.json'),
        'inputValidation': ref(OUT / 'input-validation.json')})
    compact = module('_lag_compact', 'run-supplemental-force-context.py').compact
    loader = module('_lag_fee_loader', 'load-market-context-screen.py')
    rows, artifacts = [], {}
    for period in PERIODS:
        artifacts[period] = {}
        if period == '2022-2023':
            helper = loader._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        else:
            fee = LegacyFeePolicy()
        for board in BOARDS:
            artifacts[period][board] = {}
            folder = OUT / period / board
            receipt = read(folder / 'input-completion.json')
            assert sha(folder / 'input-completion.json') == completion['outputs'][period][board]['sha256']
            for item in receipt['files'].values():
                assert sha(ROOT / item['path']) == item['sha256']
            assert sha(ROOT / receipt['accountData']['path']) == receipt['accountData']['sha256']
            data = read(ROOT / receipt['accountData']['path'])
            assert digest(data) == receipt['accountDatasetDigest']
            for mode in MODES:
                signals = read(folder / (mode + '.signals.json'))
                emit({'phase': 'account-start', 'period': period, 'board': board, 'mode': mode, 'signals': len(signals)})
                engine = ResearchExitAccount(data, signals, deepcopy(plan['periods'][period]['config']),
                    exit_rule='noProgress5', entry_weight=.2, fee_policy=fee)
                engine.step(20000)
                result, checkpoint = engine.result(), engine.checkpoint()
                assert result['status'] == 'completed'
                summary = summarize_account(result, checkpoint, policy_id=mode, policy_label=plan['labels'][mode])
                assert not summary['quality']['integrityIssues']
                saved = {}
                for suffix, value in (('.result.json.gz', result), ('.checkpoint.json.gz', checkpoint), ('.summary.json', summary)):
                    target = folder / (mode + suffix)
                    write(target, value)
                    saved[target.name] = ref(target)
                row = compact(mode, plan['labels'][mode], signals, summary)
                row.update(period=period, board=board, policy=mode)
                rows.append(row)
                artifacts[period][board][mode] = saved
                emit({'phase': 'account-complete', **{k: row[k] for k in ('period', 'board', 'policy',
                    'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
                del engine, result, checkpoint, summary, signals
                gc.collect()
            assert digest(data) == receipt['accountDatasetDigest'], 'Accounts mutated frozen source'
            del data
            gc.collect()
    verify_plan()
    write(OUT / 'comparison.json', {'schema': 'leader-lag-followers-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newSetupMechanisms': 1, 'newEntryPolicies': 2, 'newUniverseVariants': 0,
        'newAccounts': 12, 'referenceAccountsReused': 0, 'limitations': plan['limitations'],
        'certified': False, 'targetAchieved': False})
    emit({'phase': 'all-accounts-complete', **ref(OUT / 'comparison.json')})


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'prepare', 'run'))
    args = parser.parse_args()
    try:
        globals()[args.mode]()
    except Exception as error:
        if OUT.exists():
            failures = OUT / 'failures'
            failures.mkdir(exist_ok=True)
            write(failures / (str(time.time_ns()) + '.json'), {'at': now(), 'mode': args.mode,
                'type': type(error).__name__, 'message': str(error)})
        raise
