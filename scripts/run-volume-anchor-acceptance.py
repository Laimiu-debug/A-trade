"""Freeze and compare three causal volume-anchor entry clocks on all six board sources."""
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
spec = importlib.util.spec_from_file_location('_anchor_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, write_record, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.write_record, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'board-coverage-reclaim-v1'
OUT = BASE / 'volume-anchor-acceptance-v1'
PROTOCOL = ROOT / 'docs/signal-volume-anchor-acceptance-v1.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('immediate', 'delay3', 'accept3')
SID = 'volumeAnchorAcceptanceV1'
PROGRAM_SHA = 'af0f67db9b7ef91cae7dda032351f7e1837b33e204d31e474d2950904dfc2276'
DELIVERY_SHA = 'fd6a973f5fe06be38316a371632efa3b7be2d7d2acec00e4b02d54be7975afed'


def verify_plan():
    plan = read(OUT / 'plan.json')
    for key in ('sourcePins', 'inputPins'):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    assert sha(BASE / 'program.json') == PROGRAM_SHA
    assert sha(PARENT / 'delivery.json') == DELIVERY_SHA
    parent, protocol = read(PARENT / 'plan.json'), read(PROTOCOL)
    assert protocol['strategyId'] == SID and tuple(protocol['modes']) == MODES
    source_proof = read(PARENT / 'source-validation.json')
    assert source_proof['passed'] and read(PARENT / 'validation.json')['passed']
    assert read(PARENT / 'readout-validation.json')['passed']
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
    files = list(parent['sourcePins']) + ['scripts/run-volume-anchor-acceptance.py',
        'service/research_volume_anchor.py', 'tests/test_research_volume_anchor.py']
    inputs = [PROTOCOL, PARENT / 'plan.json', PARENT / 'delivery.json', PARENT / 'source-completion.json',
        PARENT / 'source-validation.json', PARENT / 'validation.json', PARENT / 'readout-validation.json',
        BASE / 'market-phase-atlas-v1/atlas.json']
    inputs += [PARENT / p / 'source-manifest.json' for p in PERIODS]
    inputs += [PARENT / p / (b + '.account-data.json.gz') for p in PERIODS for b in BOARDS]
    plan = {'schema': 'volume-anchor-acceptance-v1', 'registeredAt': now(), 'strategyId': SID,
        'protocol': ref(PROTOCOL), 'rules': protocol, 'modes': list(MODES), 'boards': list(BOARDS),
        'labels': protocol['labels'], 'boardLabels': parent['labels'], 'periods': periods,
        'sourceData': source_data, 'sourcePins': {name: sha(ROOT / name) for name in dict.fromkeys(files)},
        'inputPins': {path.relative_to(ROOT).as_posix(): sha(path) for path in inputs},
        'newAccounts': 18, 'newSetupMechanisms': 1, 'newEntryPolicies': 3,
        'newUniverseVariants': 0, 'referenceAccountsReused': 0,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'previousTurn': {'classification': 'progress', 'evidence': ref(PARENT / 'delivery.json'),
            'meaning': 'Completed six board-transfer accounts and all time partitions. New setup retains all boards rather than selecting the previously profitable board.'},
        'limitations': list(parent['limitations']) + [
            'Same original raw-price/rights/execution limitations and valuation quality flags remain; arithmetic audit cannot certify economic validity.',
            'Cumulative traded average is not holder inventory cost, investor identity, net inflow, or proof of price support.',
            'All three policies rank at the same anchor A; delayed acceptance does not rerank or fill vacated slots.',
            'A three-session formation clock does not impose a holding deadline; the unchanged close-time exits can realize losses larger than their nominal triggers.',
            'accept3 versus delay3 is the registered primary policy contrast; immediate differs in clock and complete-data admission too.',
            'No board or policy is promoted from these already inspected historical periods.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'registered': True, **ref(OUT / 'plan.json')})


def prepare():
    from service.research_volume_anchor import build_volume_anchor
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
            evidence = build_volume_anchor(data, start=config['startDate'], end=config['endDate'],
                progress=lambda value: emit({'phase': 'signal-progress', 'period': period, 'board': board, **value}))
            assert evidence['strategyId'] == SID and tuple(evidence['modes']) == MODES
            for name, rows in (('opportunities.jsonl.gz', evidence['opportunities']),
                               ('daily-evidence.jsonl.gz', evidence['dailyRows'])):
                with gzip.open(folder / name, 'xt', encoding='utf-8', compresslevel=1) as stream:
                    for row in rows:
                        write_record(stream, row)
            write(folder / 'signal-metadata.json', {k: v for k, v in evidence.items()
                if k not in ('opportunities', 'dailyRows', 'schedules')})
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
        'outputs': outputs, 'allSchedulesFrozenBeforeAccounts': True, 'newAccounts': 18})


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
    compact = module('_anchor_compact', 'run-supplemental-force-context.py').compact
    loader = module('_anchor_fee_loader', 'load-market-context-screen.py')
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
    write(OUT / 'comparison.json', {'schema': 'volume-anchor-acceptance-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newSetupMechanisms': 1, 'newEntryPolicies': 3, 'newUniverseVariants': 0,
        'newAccounts': 18, 'referenceAccountsReused': 0, 'limitations': plan['limitations'],
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
