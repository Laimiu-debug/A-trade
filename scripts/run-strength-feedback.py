"""Freeze a causal strong-cohort feedback experiment; reuse six exact controls."""
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
spec = importlib.util.spec_from_file_location('_feedback_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, write_record, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.write_record, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PREVIOUS = BASE / 'leader-lag-followers-v1'
OUT = BASE / 'strength-feedback-v1'
PROTOCOL = ROOT / 'docs/signal-strength-feedback-v1.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('known', 'positive')
REPORT_MODES = ('baseline', *MODES)
SID = 'strengthFeedbackV1'
PROGRAM_SHA = '339b14546548c49230e97720fd433d0d7669a156371ffa3d2d39fb1dbc0b4c82'
DELIVERY_SHA = 'e28109bb88bcf6811716d417863a2200260038ac86b4cbefacc57b31868a93d2'


def verify_plan():
    plan = read(OUT / 'plan.json')
    for key in ('sourcePins', 'inputPins'):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    assert sha(BASE / 'program.json') == PROGRAM_SHA
    assert sha(PREVIOUS / 'delivery.json') == DELIVERY_SHA
    previous, protocol = read(PREVIOUS / 'plan.json'), read(PROTOCOL)
    for name, expected in {**previous['sourcePins'], **previous['inputPins']}.items():
        assert sha(ROOT / name) == expected, 'Inherited registered artifact changed: ' + name
    assert protocol['strategyId'] == SID and tuple(protocol['modes']) == MODES
    assert read(PREVIOUS / 'validation.json')['passed'] and read(PREVIOUS / 'readout-validation.json')['passed']
    old = read(PREVIOUS / 'comparison.json')
    periods, baseline_artifacts, baseline_schedules, baseline_inputs = {}, {}, {}, {}
    inputs = [PROTOCOL, PREVIOUS / 'plan.json', PREVIOUS / 'delivery.json', PREVIOUS / 'comparison.json',
              PREVIOUS / 'input-completion.json', PREVIOUS / 'input-validation.json', PREVIOUS / 'validation.json',
              PREVIOUS / 'readout.json', PREVIOUS / 'readout-validation.json', BASE / 'market-phase-atlas-v1/atlas.json']
    for period in PERIODS:
        config = deepcopy(previous['periods'][period]['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        periods[period] = {'startDate': config['startDate'], 'endDate': config['endDate'], 'config': config}
        baseline_artifacts[period], baseline_schedules[period], baseline_inputs[period] = {}, {}, {}
        for board in BOARDS:
            folder = PREVIOUS / period / board
            baseline_artifacts[period][board] = deepcopy(old['artifacts'][period][board]['control'])
            baseline_schedules[period][board] = ref(folder / 'control.signals.json')
            baseline_inputs[period][board] = ref(folder / 'input-completion.json')
            inputs += [ROOT / item['path'] for item in baseline_artifacts[period][board].values()]
            inputs += [folder / name for name in ('control.signals.json', 'input-completion.json',
                                                'opportunities.jsonl.gz', 'input-counts.json', 'signal-metadata.json')]
    files = list(previous['sourcePins']) + [
        'scripts/run-strength-feedback.py', 'service/research_strength_feedback.py',
        'tests/test_research_strength_feedback.py', 'scripts/verify-strength-feedback.py',
        'scripts/summarize-strength-feedback.py', 'scripts/verify-strength-feedback-readout.py',
        'scripts/verify-reclaim-context-diagnostic.py', 'scripts/verify-board-coverage-readout.py',
        'scripts/verify-leader-lag-readout.py']
    input_pins = dict(previous['inputPins'])
    input_pins.update({path.relative_to(ROOT).as_posix(): sha(path) for path in inputs})
    trial = protocol['evidence']['trialAccounting']
    plan = {'schema': 'strength-feedback-v1', 'registeredAt': now(), 'strategyId': SID,
        'protocol': ref(PROTOCOL), 'rules': protocol, 'modes': list(MODES), 'reportModes': list(REPORT_MODES),
        'boards': list(BOARDS), 'labels': protocol['labels'], 'boardLabels': previous['boardLabels'], 'periods': periods,
        'sourceData': deepcopy(previous['sourceData']), 'baselineArtifacts': baseline_artifacts,
        'baselineSchedules': baseline_schedules, 'baselineInputs': baseline_inputs,
        'baselinePlan': ref(PREVIOUS / 'plan.json'), 'baselineComparison': ref(PREVIOUS / 'comparison.json'),
        'sourcePins': {name: sha(ROOT / name) for name in dict.fromkeys(files)}, 'inputPins': input_pins,
        **trial, 'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'previousTurn': {'classification': 'progress', 'evidence': ref(PREVIOUS / 'delivery.json'),
            'meaning': 'Twelve leader-lag accounts completed; the lag-dominance rule had zero signals. The new experiment retains the exact executable control setup and tests a separately frozen causal board-state admission policy.'},
        'limitations': [*previous['limitations'],
            'Known feedback state is a robust majority decision, not complete observation of every frozen member.',
            'Five-session absolute close-price feedback is an environment proxy, not a net realized trade return.',
            'Six exact baseline accounts are referenced without execution; twelve new accounts use the same costs and exits.',
            protocol['validation']['limitations']],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'registered': True, **ref(OUT / 'plan.json')})


def prepare():
    from service.research_strength_feedback import build_strength_feedback
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
            signals = read(ROOT / plan['baselineSchedules'][period][board]['path'])
            emit({'phase': 'feedback-start', 'period': period, 'board': board, 'stocks': len(data['securities'])})
            with gzip.open(folder / 'member-evidence.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as stream:
                evidence = build_strength_feedback(data, start=config['startDate'], end=config['endDate'], source_signals=signals,
                    progress=lambda value: emit({'phase': 'feedback-progress', 'period': period, 'board': board, **value}),
                    on_members=lambda value: write_record(stream, value))
            assert evidence['strategyId'] == SID and tuple(evidence['modes']) == MODES
            for name, rows in (('opportunities.jsonl.gz', evidence['opportunities']), ('daily-evidence.jsonl.gz', evidence['dailyRows'])):
                with gzip.open(folder / name, 'xt', encoding='utf-8', compresslevel=1) as stream:
                    for row in rows:
                        write_record(stream, row)
            write(folder / 'signal-metadata.json', {k: v for k, v in evidence.items() if k not in ('opportunities', 'dailyRows', 'schedules')})
            for mode in MODES:
                write(folder / (mode + '.signals.json'), evidence['schedules'][mode])
            write(folder / 'input-counts.json', evidence['counts'])
            write(folder / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
                'period': period, 'board': board, 'counts': evidence['counts'], **binding,
                'baselineSchedule': plan['baselineSchedules'][period][board], 'baselineInput': plan['baselineInputs'][period][board],
                'files': {path.name: ref(path) for path in folder.iterdir() if path.is_file()}})
            outputs[period][board] = ref(folder / 'input-completion.json')
            emit({'phase': 'feedback-complete', 'period': period, 'board': board, 'counts': evidence['counts']})
            assert digest(data) == binding['accountDatasetDigest'], 'Feedback generation mutated frozen source'
            del data, evidence, signals
            gc.collect()
    verify_plan()
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': outputs, 'allSchedulesFrozenBeforeAccounts': True, 'newAccounts': 12, 'referenceAccountsReused': 6})


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
    compact = module('_feedback_compact', 'run-supplemental-force-context.py').compact
    loader = module('_feedback_fee_loader', 'load-market-context-screen.py')
    old = read(ROOT / plan['baselineComparison']['path'])
    rows, artifacts = [], {}
    for period in PERIODS:
        artifacts[period] = {}
        if period == '2022-2023':
            helper = loader._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        else:
            fee = LegacyFeePolicy()
        for board in BOARDS:
            artifacts[period][board] = {'baseline': deepcopy(plan['baselineArtifacts'][period][board])}
            baseline = deepcopy(next(row for row in old['rows'] if (row['period'], row['board'], row['policy']) == (period, board, 'control')))
            baseline.update(id='baseline', policy='baseline', label=plan['labels']['baseline'])
            rows.append(baseline)
            folder = OUT / period / board
            receipt = read(folder / 'input-completion.json')
            assert sha(folder / 'input-completion.json') == completion['outputs'][period][board]['sha256']
            for item in receipt['files'].values():
                assert sha(ROOT / item['path']) == item['sha256']
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
                emit({'phase': 'account-complete', **{k: row[k] for k in ('period', 'board', 'policy', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
                del engine, result, checkpoint, summary, signals
                gc.collect()
            assert digest(data) == receipt['accountDatasetDigest'], 'Accounts mutated frozen source'
            del data
            gc.collect()
    verify_plan()
    write(OUT / 'comparison.json', {'schema': 'strength-feedback-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        **plan['rules']['evidence']['trialAccounting'], 'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
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
            write(failures / (str(time.time_ns()) + '.json'), {'at': now(), 'mode': args.mode, 'type': type(error).__name__, 'message': str(error)})
        raise
