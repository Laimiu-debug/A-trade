"""Bind unchanged reclaim streams and run one preregistered volatility exit.

Register reads only inherited evidence. Actual accounts require independently
verified inputs and a receipt from real pre-account tests on the exact sources.
"""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('_breakout_vol_trail_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'board-coverage-reclaim-v1'
PREVIOUS = BASE / 'reclaim-volatility-trail-v1'
LAST = BASE / 'reclaim-exit-mechanism-v1'
REFERENCE_MODES = ('trail10', 'volatilityTrail')
OUT = BASE / 'reclaim-breakout-volatility-trail-v1'
PROTOCOL = ROOT / 'docs/reclaim-breakout-volatility-trail-v1.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('breakoutVolatilityTrail',)
REPORT_MODES = (*REFERENCE_MODES, *MODES)
SID = 'strongUndercutReclaimV1'
PROGRAM_SHA = 'e60f3e1a244afdf60de0c526514ba738814f028f080e110a6a9a4e4e68c9a218'
DELIVERY_SHA = '7568310306dfc2bb88b801311ea20e7840d15072d4b973f21e054ba2bf6c2ad9'
LAST_DELIVERY_SHA = 'a1ad45563984e79997178328a17d942d13be597a490a498fe224e8c79e8ed92a'
PREVIOUS_PLAN_SHA = '4edd15d2b074ff81992c61e7bfac652e6e77780bbd4f930fb2e7332014fbe1d5'
VERIFIER = 'scripts/verify-reclaim-breakout-volatility-trail.py'
NEW_UNIT_TESTS = 20
TRIALS = {'experimentBatches': 1, 'newAccounts': 6, 'referenceAccountsReused': 12,
          'newSetupMechanisms': 0, 'newUniverseVariants': 0, 'newEntryPolicies': 0,
          'newExitDefinitions': 1, 'transferredExitPolicies': 0}
OLD_RUNNER = ROOT / 'scripts/run-reclaim-exit-transfer.py'
OLD_RUNNER_SHA = '749c83f3db2f982e35731173daab2b8c7f15f538eeb6b59ad797329d10f8faf0'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned(item):
    path = (ROOT / item['path']).resolve()
    require(not Path(item['path']).is_absolute() and path.is_relative_to(ROOT) and path.is_file(), 'Invalid repository evidence reference')
    require(sha(path) == item['sha256'], 'Changed evidence: ' + item['path'])
    if 'bytes' in item:
        require(path.stat().st_size == item['bytes'], 'Evidence byte size differs')
    return path


def validate_schedule(signals, target_dates):
    require(sha(OLD_RUNNER) == OLD_RUNNER_SHA, 'Frozen schedule validator changed')
    previous = module('_breakout_vol_trail_frozen_schedule', 'run-reclaim-exit-transfer.py')
    previous.validate_schedule(signals, target_dates)


def policy_configs(base):
    require(base.get('allocation') == {'mode': 'single', 'strategyIds': [SID]}
            and len(base.get('strategies', [])) == 1 and base['strategies'][0]['id'] == SID, 'Wrong original signal identity')
    expected = {'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0, 'maxHoldingDays': 0,
                'initialCapital': 500000, 'maxPositions': 5, 'maxStockWeight': .2}
    require(all(type(base.get(k)) in (int, float) and base[k] == v for k, v in expected.items()), 'Original risk/capital config differs')
    result = {m: deepcopy(base) for m in REPORT_MODES}
    for mode in ('volatilityTrail', *MODES):
        result[mode]['trailingStopPct'] = 0.
    return result


def verify_plan():
    plan = read(OUT / 'plan.json')
    require(plan['schema'] == 'reclaim-breakout-volatility-trail-v1' and plan['strategyId'] == SID, 'Wrong research plan')
    for mapping in (plan['sourcePins'], plan['inputPins']):
        for name, expected in mapping.items():
            pinned({'path': name, 'sha256': expected})
    require(all(type(plan[k]) is int and plan[k] == v for k, v in TRIALS.items()), 'Trial scope changed')
    return plan


def register():
    require(NEW_UNIT_TESTS > 0, 'Freeze the actual new test count before registration')
    require(sha(BASE / 'program.json') == PROGRAM_SHA and sha(PREVIOUS / 'delivery.json') == DELIVERY_SHA
            and sha(PREVIOUS / 'plan.json') == PREVIOUS_PLAN_SHA, 'Registry or completed predecessor changed')
    previous, old, protocol = read(PREVIOUS / 'plan.json'), read(PREVIOUS / 'comparison.json'), read(PROTOCOL)
    require(protocol['strategyId'] == SID and tuple(protocol['modes']) == MODES
            and tuple(protocol['reportModes']) == REPORT_MODES and protocol['evidence']['trialAccounting'] == TRIALS, 'Protocol scope differs')
    for mapping in (previous['sourcePins'], previous['inputPins']):
        for name, expected in mapping.items():
            pinned({'path': name, 'sha256': expected})
    delivery = read(PREVIOUS / 'delivery.json')
    latest = read(LAST / 'delivery.json')
    require(sha(LAST / 'delivery.json') == LAST_DELIVERY_SHA and latest['programAfterSha256'] == PROGRAM_SHA, 'Latest diagnostic changed')
    for name, expected in latest['files'].items():
        pinned({'path': name, 'sha256': expected})
    require(not delivery['certified']
            and delivery['goalStatus'] == 'active', 'Previous completion state differs')
    for name, expected in delivery['files'].items():
        pinned({'path': name, 'sha256': expected})
    for name in ('input-validation.json', 'validation.json', 'readout-validation.json'):
        require(read(PREVIOUS / name)['passed'] is True, 'Previous audit did not pass')
    periods, artifacts, prepared = {}, {}, {}
    sources = deepcopy(previous['sourceData'])
    schedules, inputs = deepcopy(previous['baselineSchedules']), deepcopy(previous['baselineInputs'])
    input_paths = [PROTOCOL, PREVIOUS / 'plan.json', PREVIOUS / 'delivery.json', PREVIOUS / 'comparison.json',
                   PREVIOUS / 'input-completion.json', PREVIOUS / 'input-validation.json', PREVIOUS / 'validation.json',
                   PREVIOUS / 'readout.json', PREVIOUS / 'readout-validation.json',
                   LAST / 'delivery.json', LAST / 'plan.json', LAST / 'readout.json', LAST / 'research-decision.json']
    for period in PERIODS:
        config = deepcopy(previous['periods'][period]['policyConfigs']['trail10'])
        periods[period] = {'startDate': config['startDate'], 'endDate': config['endDate'],
                           'config': config, 'policyConfigs': policy_configs(config)}
        artifacts[period], prepared[period] = {}, {}
        for board in BOARDS:
            receipt = read(pinned(inputs[period][board]))
            for item in receipt['files'].values():
                pinned(item)
            schedule = read(pinned(schedules[period][board]))
            metadata = read(PARENT / period / board / 'signal-metadata.json')
            validate_schedule(schedule, set(metadata['targetDates']))
            require(len(schedule) == metadata['counts']['signals']['immediate'], 'Original source signal count differs')
            artifacts[period][board] = {}
            for mode in REFERENCE_MODES:
                baseline = deepcopy(old['artifacts'][period][board][mode])
                require(set(baseline) == {mode + suffix for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')}, 'Incomplete reference account')
                for item in baseline.values():
                    pinned(item)
                artifacts[period][board][mode] = baseline
            prepared[period][board] = {'files': deepcopy(receipt['files']), 'signalCount': len(schedule),
                                       'signalScheduleSha256': digest(schedule)}
    new_sources = ['scripts/run-reclaim-breakout-volatility-trail.py', VERIFIER,
        'scripts/summarize-reclaim-breakout-volatility-trail.py', 'scripts/verify-reclaim-breakout-volatility-trail-readout.py',
        'service/research_breakout_volatility_trail.py', 'tests/test_research_breakout_volatility_trail.py',
        'scripts/record-reclaim-breakout-volatility-trail-tests.py',
        'scripts/crosscheck-reclaim-breakout-volatility-trail.py',
        'tests/test_research_fixed_signal_account.py', 'tests/test_research_structural_exits.py',
        'tests/test_research_account_outcomes.py', 'tests/test_research_account_fees.py']
    source_pins = deepcopy(previous['sourcePins'])
    source_pins.update({name: sha(ROOT / name) for name in new_sources})
    input_pins = deepcopy(previous['inputPins'])
    input_pins.update({path.relative_to(ROOT).as_posix(): sha(path) for path in input_paths})
    for boards in artifacts.values():
        for policies in boards.values():
            for files in policies.values():
                input_pins.update({item['path']: item['sha256'] for item in files.values()})
    plan = {'schema': 'reclaim-breakout-volatility-trail-v1', 'registeredAt': now(), 'strategyId': SID,
        'protocol': ref(PROTOCOL), 'rules': protocol, 'modes': list(MODES), 'reportModes': list(REPORT_MODES),
        'boards': list(BOARDS), 'labels': protocol['labels'], 'boardLabels': previous['boardLabels'],
        'periods': periods, 'policySpecs': protocol['policies'], 'sourceData': sources,
        'baselineArtifacts': artifacts, 'baselineSchedules': schedules, 'baselineInputs': inputs,
        'baselinePlan': ref(PREVIOUS / 'plan.json'), 'baselineComparison': ref(PREVIOUS / 'comparison.json'),
        'sourcePlan': deepcopy(previous['sourcePlan']), 'sourceComparison': deepcopy(previous['sourceComparison']),
        'sourcePins': source_pins, 'inputPins': input_pins, **TRIALS,
        'preAccountTestSpec': {'inheritedUnitTests': 77, 'newUnitTests': NEW_UNIT_TESTS, 'commandCount': 6},
        'noSignalsGenerated': True, 'productionChanges': True, 'parameterSearch': False,
        'profitHurdles': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False,
        'previousTurn': {'classification': 'progress', 'evidence': ref(LAST / 'delivery.json'),
            'meaning': 'Completed exit-mechanism diagnostic showed heterogeneous return and capital contributions. Test one staged breakout-activated exit across all six sources, without selecting boards, dates or hindsight winners.'},
        'limitations': list(dict.fromkeys(previous['limitations'] + protocol['limitations']))}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    outputs = {}
    for period in PERIODS:
        outputs[period] = {}
        for board in BOARDS:
            folder = OUT / period / board
            folder.mkdir(parents=True, exist_ok=False)
            write(folder / 'input-completion.json', {'schema': 'reclaim-breakout-volatility-trail-input-v1',
                'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'), 'period': period, 'board': board,
                **sources[period][board], 'baselineSchedule': schedules[period][board],
                'baselineInput': inputs[period][board], 'baselineArtifacts': artifacts[period][board],
                **prepared[period][board], 'noSignalsGenerated': True})
            outputs[period][board] = ref(folder / 'input-completion.json')
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': outputs, 'baselineInputCompletion': ref(PREVIOUS / 'input-completion.json'),
        'allSchedulesFrozenBeforeAccounts': True, 'noSignalsGenerated': True,
        'newAccounts': 6, 'referenceAccountsReused': 12})
    verify_plan()
    emit({'registered': True, **ref(OUT / 'plan.json'), 'inputCompletion': ref(OUT / 'input-completion.json'),
          'sourceSignals': sum(r['signalCount'] for boards in prepared.values() for r in boards.values()),
          'newSignals': 0, 'newAccountsExecuted': 0})


def validate_prerequisites(pretests, validation, completion, *, plan_sha, completion_sha, verifier_sha, test_spec):
    require(pretests['schema'] == 'reclaim-breakout-volatility-trail-pre-account-tests-v1' and pretests['passed'] is True
            and pretests['planSha256'] == plan_sha and pretests['allChecksBeforeFirstNewAccount'] is True, 'Actual preregistered tests required')
    require(pretests['productionUnitIncrement'] == test_spec['newUnitTests'] > 0
            and pretests['unitTests'] == test_spec['inheritedUnitTests'] + test_spec['newUnitTests'], 'Synthetic unit test accounting differs')
    require(len(pretests['commands']) == test_spec['commandCount']
            and all(row['exitCode'] == 0 for row in pretests['commands']), 'A pre-account command failed')
    require(validation['passed'] is True and validation['planSha256'] == plan_sha
            and validation['inputCompletionSha256'] == completion_sha and validation['verifierSha256'] == verifier_sha, 'Input validation differs')
    require(completion['planSha256'] == plan_sha and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['noSignalsGenerated'] is True, 'Frozen input receipt differs')


def make_account(mode, data, signals, config, fee, *, account_class=None):
    require(mode == 'breakoutVolatilityTrail', 'Reused reference must not execute')
    if account_class is None:
        from service.research_breakout_volatility_trail import BreakoutVolatilityTrailAccount
        account_class = BreakoutVolatilityTrailAccount
    return account_class(data, signals, deepcopy(config), entry_weight=.2, fee_policy=fee)


def run():
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan()
    completion, validation, pretests = (read(OUT / n) for n in ('input-completion.json', 'input-validation.json', 'pre-account-tests.json'))
    validate_prerequisites(pretests, validation, completion, plan_sha=sha(OUT / 'plan.json'),
        completion_sha=sha(OUT / 'input-completion.json'), verifier_sha=plan['sourcePins'][VERIFIER], test_spec=plan['preAccountTestSpec'])
    for name, expected in pretests['pins'].items():
        pinned({'path': name, 'sha256': expected})
    write(OUT / 'run-start.json', {'startedAt': now(), 'inputCompletion': ref(OUT / 'input-completion.json'),
        'inputValidation': ref(OUT / 'input-validation.json'), 'preAccountTests': ref(OUT / 'pre-account-tests.json')})
    compact = module('_breakout_vol_trail_compact', 'run-supplemental-force-context.py').compact
    loader = module('_breakout_vol_trail_fee_loader', 'load-market-context-screen.py')
    old, rows, artifacts = read(pinned(plan['baselineComparison'])), [], {}
    for period in PERIODS:
        artifacts[period] = {}
        if period == '2022-2023':
            helper = loader._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        else:
            fee = LegacyFeePolicy()
        for board in BOARDS:
            folder = OUT / period / board
            receipt = read(pinned(completion['outputs'][period][board]))
            require(receipt['planSha256'] == sha(OUT / 'plan.json') and receipt['noSignalsGenerated'] is True, 'Bound input identity differs')
            for item in receipt['files'].values():
                pinned(item)
            artifacts[period][board] = deepcopy(plan['baselineArtifacts'][period][board])
            for reference_mode in REFERENCE_MODES:
                baseline = deepcopy(next(r for r in old['rows'] if (r['period'], r['board'], r['id']) == (period, board, reference_mode)))
                baseline.update(label=plan['labels'][reference_mode])
                rows.append(baseline)
            data, signals = read(pinned(receipt['accountData'])), read(pinned(receipt['baselineSchedule']))
            require(digest(data) == receipt['accountDatasetDigest'], 'Frozen account data differs')
            require(len(signals) == receipt['signalCount'] and digest(signals) == receipt['signalScheduleSha256'], 'Original schedule differs')
            mode = 'breakoutVolatilityTrail'
            emit({'phase': 'account-start', 'period': period, 'board': board, 'mode': mode, 'signals': len(signals)})
            engine = make_account(mode, data, signals, plan['periods'][period]['policyConfigs'][mode], fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            require(result['status'] == 'completed' and result['research']['identity']['signalSchedule'] == signals, 'Incomplete or changed account schedule')
            summary = summarize_account(result, checkpoint, policy_id=mode, policy_label=plan['labels'][mode])
            require(not summary['quality']['integrityIssues'], 'Account reconciliation failed')
            saved = {}
            for suffix, value in (('.result.json.gz', result), ('.checkpoint.json.gz', checkpoint), ('.summary.json', summary)):
                target = folder / (mode + suffix)
                write(target, value)
                saved[target.name] = ref(target)
            row = compact(mode, plan['labels'][mode], signals, summary)
            row.update(period=period, board=board, policy=mode)
            rows.append(row)
            artifacts[period][board][mode] = saved
            emit({'phase': 'account-complete', **{key: row[key] for key in ('period', 'board', 'policy', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
            require(digest(data) == receipt['accountDatasetDigest'] and digest(signals) == receipt['signalScheduleSha256'], 'Inputs mutated by account execution')
            del engine, result, checkpoint, summary, data, signals
            gc.collect()
    verify_plan()
    write(OUT / 'comparison.json', {'schema': 'reclaim-breakout-volatility-trail-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        **TRIALS, 'noSignalsGenerated': True, 'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
    emit({'phase': 'all-accounts-complete', **ref(OUT / 'comparison.json')})


def self_test():
    base = {'allocation': {'mode': 'single', 'strategyIds': [SID]}, 'strategies': [{'id': SID}],
        'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0., 'maxHoldingDays': 0,
        'initialCapital': 500000, 'maxPositions': 5, 'maxStockWeight': .2, 'nested': {'x': 1}}
    before, configs = deepcopy(base), policy_configs(base)
    require(base == before == configs['trail10'], 'Reference config changed')
    require({k for k in base if base[k] != configs['breakoutVolatilityTrail'][k]} == {'trailingStopPct'}, 'Unexpected new config changes')
    configs['breakoutVolatilityTrail']['nested']['x'] = 2
    require(base == before and configs['trail10']['nested']['x'] == 1, 'Shared mutable configuration')
    calls = []
    def fake(*args, **kwargs):
        calls.append((args, kwargs)); return 'new'
    require(make_account('breakoutVolatilityTrail', {}, [], configs['breakoutVolatilityTrail'], 'fee', account_class=fake) == 'new', 'Wrong dispatch')
    require(calls[0][1] == {'entry_weight': .2, 'fee_policy': 'fee'}, 'Unexpected exit/entry argument')
    try:
        make_account('trail10', {}, [], base, 'fee', account_class=fake)
    except ValueError:
        pass
    else:
        raise AssertionError('Reference execution accepted')
    test_spec = {'inheritedUnitTests': 77, 'newUnitTests': 7, 'commandCount': 6}
    pre = {'schema': 'reclaim-breakout-volatility-trail-pre-account-tests-v1', 'passed': True, 'planSha256': 'p',
           'allChecksBeforeFirstNewAccount': True, 'productionUnitIncrement': 7, 'unitTests': 84, 'commands': [{'exitCode': 0} for _ in range(6)]}
    proof = {'passed': True, 'planSha256': 'p', 'inputCompletionSha256': 'c', 'verifierSha256': 'v'}
    completion = {'planSha256': 'p', 'allSchedulesFrozenBeforeAccounts': True, 'noSignalsGenerated': True}
    args = {'plan_sha': 'p', 'completion_sha': 'c', 'verifier_sha': 'v', 'test_spec': test_spec}
    validate_prerequisites(pre, proof, completion, **args)
    for bad in ({**pre, 'passed': False}, {**pre, 'allChecksBeforeFirstNewAccount': False},
                {**pre, 'productionUnitIncrement': 0}, {**pre, 'commands': pre['commands'][:5]}):
        try:
            validate_prerequisites(bad, proof, completion, **args)
        except ValueError:
            pass
        else:
            raise AssertionError('Bad prerequisites accepted')
    return {'passed': True, 'checks': 10, 'actualDataRead': False, 'accountExecutions': 0, 'filesWritten': 0}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', nargs='?', choices=('register', 'run'))
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        emit(self_test())
    elif args.mode is None:
        parser.error('Choose register/run or --self-test')
    else:
        try:
            globals()[args.mode]()
        except Exception as error:
            if OUT.exists():
                failures = OUT / 'failures'
                failures.mkdir(exist_ok=True)
                write(failures / (str(time.time_ns()) + '.json'), {'at': now(), 'mode': args.mode, 'type': type(error).__name__, 'message': str(error)})
            raise
