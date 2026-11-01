"""Bind unchanged reclaim signals, then transfer two frozen exit definitions.

Register only binds existing audited inputs; it never builds a signal or reads
new account results. Run requires input validation and actual pre-account tests.
Each new account receives the entire original schedule with its original SID.
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
spec = importlib.util.spec_from_file_location('_exit_transfer_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'board-coverage-reclaim-v1'
PREVIOUS = BASE / 'strength-feedback-v1'
OUT = BASE / 'reclaim-exit-transfer-v1'
PROTOCOL = ROOT / 'docs/reclaim-exit-transfer-v1.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
MODES = ('trail10', 'structure10')
REPORT_MODES = ('baseline', *MODES)
SID = 'strongUndercutReclaimV1'
SIGNAL_FIELDS = {'signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank'}
PROGRAM_SHA = 'd45409394d468511dd2acf999099d81ab34de9bf4f49b3a25bdd9c672610bc87'
DELIVERY_SHA = 'dc81211297a383465ec9f2e79a0818a5d0bb76fb0eacff4b9b982445f35ddd14'
PARENT_PLAN_SHA = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
VERIFIER = 'scripts/verify-reclaim-exit-transfer.py'
TRIALS = {'experimentBatches': 1, 'newAccounts': 12, 'referenceAccountsReused': 6, 'newSetupMechanisms': 0,
          'newUniverseVariants': 0, 'newEntryPolicies': 0, 'newExitDefinitions': 0, 'transferredExitPolicies': 2}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned(item):
    path = (ROOT / item['path']).resolve()
    require(not Path(item['path']).is_absolute() and path.is_relative_to(ROOT) and path.is_file(), 'Invalid repository input reference')
    require(sha(path) == item['sha256'], 'Changed input: ' + item['path'])
    return path


def verify_plan():
    plan = read(OUT / 'plan.json')
    require(plan['schema'] == 'reclaim-exit-transfer-v1' and plan['strategyId'] == SID, 'Wrong exit-transfer plan')
    for mapping in (plan['sourcePins'], plan['inputPins']):
        for name, expected in mapping.items():
            pinned({'path': name, 'sha256': expected})
    require(all(plan[key] == value for key, value in TRIALS.items()), 'Trial scope changed')
    return plan


def policy_configs(base):
    require(isinstance(base, dict) and base.get('allocation') == {'mode': 'single', 'strategyIds': [SID]}
            and len(base.get('strategies', [])) == 1 and base['strategies'][0]['id'] == SID, 'Unchanged original strategy identity required')
    for key, value in {'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0, 'maxHoldingDays': 0,
                       'initialCapital': 500000, 'maxPositions': 5, 'maxStockWeight': .2}.items():
        require(type(base.get(key)) in (int, float) and base[key] == value, 'Unexpected original account config: ' + key)
    answer = {mode: deepcopy(base) for mode in REPORT_MODES}
    answer['structure10']['trailingStopPct'] = 0.
    return answer


def validate_schedule(signals, target_dates):
    require(isinstance(signals, list), 'Complete original signal list required')
    ids, pairs, ranks, ordering = set(), set(), set(), []
    for row in signals:
        require(isinstance(row, dict) and set(row) == SIGNAL_FIELDS, 'Original five signal fields required')
        sid, day, symbol, rank = (row[key] for key in ('signalId', 'signalDate', 'symbol', 'originalRank'))
        require(row['strategyId'] == SID and isinstance(sid, str) and bool(sid)
                and isinstance(symbol, str) and bool(symbol) and day in target_dates, 'Unexpected signal identity/calendar')
        require(type(rank) is int and 1 <= rank <= 5, 'Original Top5 rank required')
        require(sid not in ids and (day, symbol) not in pairs and (day, rank) not in ranks, 'Duplicate original signal')
        ids.add(sid); pairs.add((day, symbol)); ranks.add((day, rank))
        ordering.append((day, rank, symbol, sid))
    require(ordering == sorted(ordering), 'Original schedule must already be ordered; do not repair ranking')


def register():
    require(sha(BASE / 'program.json') == PROGRAM_SHA, 'Registry changed before registration')
    require(sha(PREVIOUS / 'delivery.json') == DELIVERY_SHA and sha(PARENT / 'plan.json') == PARENT_PLAN_SHA,
            'Completed predecessor or source plan changed')
    parent, previous, protocol = read(PARENT / 'plan.json'), read(PREVIOUS / 'plan.json'), read(PROTOCOL)
    require(protocol['strategyId'] == SID and tuple(protocol['modes']) == MODES
            and tuple(protocol['reportModes']) == REPORT_MODES and protocol['evidence']['trialAccounting'] == TRIALS,
            'Protocol scope differs')
    for mapping in (parent['sourcePins'], parent['inputPins']):
        for name, expected in mapping.items():
            pinned({'path': name, 'sha256': expected})
    inherited_delivery = (PARENT / 'delivery.json').relative_to(ROOT).as_posix()
    require(sha(PARENT / 'delivery.json') == previous['inputPins'][inherited_delivery], 'Source delivery differs from prior registered lineage')
    for filename in ('source-validation.json', 'input-validation.json', 'validation.json', 'readout-validation.json'):
        require(read(PARENT / filename)['passed'] is True, 'Inherited audit failed: ' + filename)
    source_proof, old = read(PARENT / 'source-validation.json'), read(PARENT / 'comparison.json')
    periods, source_data, baseline_artifacts, baseline_schedules, baseline_inputs, prepared = {}, {}, {}, {}, {}, {}
    inputs = [PROTOCOL, PREVIOUS / 'plan.json', PREVIOUS / 'delivery.json', PARENT / 'plan.json', PARENT / 'delivery.json',
        PARENT / 'source-completion.json', PARENT / 'source-validation.json', PARENT / 'input-completion.json',
        PARENT / 'input-validation.json', PARENT / 'validation.json', PARENT / 'comparison.json',
        PARENT / 'readout.json', PARENT / 'readout-validation.json', BASE / 'market-phase-atlas-v1/atlas.json']
    for period in PERIODS:
        config = deepcopy(parent['periods'][period]['config'])
        periods[period] = {'startDate': config['startDate'], 'endDate': config['endDate'],
                           'config': config, 'policyConfigs': policy_configs(config)}
        source_data[period], baseline_artifacts[period], baseline_schedules[period], baseline_inputs[period], prepared[period] = {}, {}, {}, {}, {}
        manifest = read(PARENT / period / 'source-manifest.json')
        for board in BOARDS:
            folder = PARENT / period / board
            prior_input = read(folder / 'input-completion.json')
            for item in prior_input['files'].values():
                pinned(item)
            schedule_path = folder / 'immediate.signals.json'
            schedule = read(schedule_path)
            metadata = read(folder / 'signal-metadata.json')
            validate_schedule(schedule, set(metadata['targetDates']))
            require(sha(schedule_path) == prior_input['files'][schedule_path.name]['sha256'], 'Original immediate schedule changed')
            require(metadata['strategyId'] == SID and metadata['counts']['signals']['immediate'] == len(schedule), 'Original signal metadata differs')
            cache = prior_input['accountData']
            require(cache['sha256'] == source_proof['datasetPins'][cache['path']], 'Frozen source proof differs')
            source_data[period][board] = {
                'accountData': cache, 'accountDatasetDigest': prior_input['accountDatasetDigest'],
                'sourceManifest': ref(PARENT / period / 'source-manifest.json'),
                'sourceValidation': ref(PARENT / 'source-validation.json'),
                'sourceCompletion': ref(PARENT / 'source-completion.json'),
                'upstreamInputReceipt': ref(folder / 'input-completion.json'),
                'members': manifest['boards'][board]['includedCount']}
            files = deepcopy(old['artifacts'][period][board])
            require(set(files) == {'immediate' + suffix for suffix in ('.result.json.gz', '.checkpoint.json.gz', '.summary.json')},
                    'Complete unchanged baseline artifact set required')
            baseline_artifacts[period][board] = files
            baseline_schedules[period][board], baseline_inputs[period][board] = ref(schedule_path), ref(folder / 'input-completion.json')
            prepared[period][board] = {'files': deepcopy(prior_input['files']), 'signalCount': len(schedule),
                                       'signalScheduleSha256': digest(schedule)}
            inputs += [ROOT / item['path'] for item in files.values()]
            inputs += [ROOT / item['path'] for item in prior_input['files'].values()]
            inputs += [ROOT / cache['path'], folder / 'input-completion.json', PARENT / period / 'source-manifest.json']
    files = list(parent['sourcePins']) + ['scripts/run-reclaim-exit-transfer.py', VERIFIER,
        'scripts/summarize-reclaim-exit-transfer.py', 'scripts/verify-reclaim-exit-transfer-readout.py',
        'scripts/summarize-volume-anchor-acceptance.py', 'scripts/verify-reclaim-context-diagnostic.py',
        'service/research_structural_exits.py', 'service/research_fixed_signal_account.py',
        'service/research_account_outcomes.py', 'service/research_account_fees.py',
        'scripts/verify-board-coverage-reclaim.py', 'scripts/verify-board-coverage-readout.py',
        'scripts/summarize-board-coverage-reclaim.py', 'scripts/verify-relative-repair-volatility-stop.py',
        'scripts/verify-account-exit-comparison.py', 'scripts/run-peer-amount-persistence.py',
        'scripts/run-supplemental-force-context.py', 'scripts/load-market-context-screen.py']
    input_pins = dict(parent['inputPins'])
    input_pins.update({path.relative_to(ROOT).as_posix(): sha(path) for path in inputs})
    plan = {'schema': 'reclaim-exit-transfer-v1', 'registeredAt': now(), 'strategyId': SID,
        'protocol': ref(PROTOCOL), 'rules': protocol, 'modes': list(MODES), 'reportModes': list(REPORT_MODES),
        'boards': list(BOARDS), 'labels': protocol['labels'], 'boardLabels': parent['labels'], 'periods': periods,
        'policySpecs': protocol['policies'], 'sourceData': source_data, 'baselineArtifacts': baseline_artifacts,
        'baselineSchedules': baseline_schedules, 'baselineInputs': baseline_inputs,
        'baselinePlan': ref(PARENT / 'plan.json'), 'baselineComparison': ref(PARENT / 'comparison.json'),
        'sourcePins': {name: sha(ROOT / name) for name in dict.fromkeys(files)}, 'inputPins': input_pins,
        **TRIALS, 'noSignalsGenerated': True, 'productionChanges': False,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'previousTurn': {'classification': 'progress', 'evidence': ref(PREVIOUS / 'delivery.json'),
            'meaning': 'Completed feedback admission comparison. This exit transfer returns to all original reclaim signals and changes no entry definition.'},
        'limitations': list(dict.fromkeys(parent['limitations'] + protocol['limitations'])),
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    outputs = {}
    for period in PERIODS:
        outputs[period] = {}
        for board in BOARDS:
            folder = OUT / period / board
            folder.mkdir(parents=True, exist_ok=False)
            write(folder / 'input-completion.json', {'schema': 'reclaim-exit-transfer-input-v1',
                'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'), 'period': period, 'board': board,
                **source_data[period][board], 'baselineSchedule': baseline_schedules[period][board],
                'baselineInput': baseline_inputs[period][board], 'baselineArtifacts': baseline_artifacts[period][board],
                **prepared[period][board], 'noSignalsGenerated': True})
            outputs[period][board] = ref(folder / 'input-completion.json')
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': outputs, 'baselineInputCompletion': ref(PARENT / 'input-completion.json'),
        'allSchedulesFrozenBeforeAccounts': True, 'noSignalsGenerated': True,
        'newAccounts': 12, 'referenceAccountsReused': 6})
    verify_plan()
    emit({'registered': True, **ref(OUT / 'plan.json'), 'inputCompletion': ref(OUT / 'input-completion.json'),
          'sourceSignals': sum(row['signalCount'] for boards in prepared.values() for row in boards.values()),
          'newSignals': 0, 'newAccountsExecuted': 0})


def validate_prerequisites(pretests, validation, completion, *, plan_sha, completion_sha, verifier_sha):
    require(pretests['schema'] == 'reclaim-exit-transfer-pre-account-tests-v1' and pretests['passed'] is True
            and pretests['planSha256'] == plan_sha and pretests['allChecksBeforeFirstNewAccount'] is True,
            'Actual pre-account tests must pass on this plan')
    require(pretests['productionUnitIncrement'] == 0 and pretests['unitTests'] == 57, 'Inherited test accounting differs')
    require(len(pretests['commands']) == 5 and all(row['exitCode'] == 0 for row in pretests['commands']), 'Failed pre-account command')
    require(validation['passed'] is True and validation['planSha256'] == plan_sha
            and validation['inputCompletionSha256'] == completion_sha and validation['verifierSha256'] == verifier_sha,
            'Independent input validation must bind the exact plan and inputs')
    require(completion['planSha256'] == plan_sha and completion['allSchedulesFrozenBeforeAccounts'] is True
            and completion['noSignalsGenerated'] is True, 'Bound input completion differs')


def make_account(mode, data, signals, config, fee, *, fixed_class=None, structural_class=None):
    require(mode in MODES, 'Reused baseline must never execute')
    if fixed_class is None or structural_class is None:
        from service.research_fixed_signal_account import FixedSignalAccount
        from service.research_structural_exits import ResearchExitAccount
        fixed_class, structural_class = FixedSignalAccount, ResearchExitAccount
    kwargs = {'entry_weight': .2, 'fee_policy': fee}
    if mode == 'trail10':
        return fixed_class(data, signals, deepcopy(config), **kwargs)
    return structural_class(data, signals, deepcopy(config), exit_rule='structure10', **kwargs)


def run():
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan()
    completion, validation, pretests = (read(OUT / name) for name in
        ('input-completion.json', 'input-validation.json', 'pre-account-tests.json'))
    validate_prerequisites(pretests, validation, completion, plan_sha=sha(OUT / 'plan.json'),
        completion_sha=sha(OUT / 'input-completion.json'), verifier_sha=plan['sourcePins'][VERIFIER])
    for name, expected in pretests['pins'].items():
        pinned({'path': name, 'sha256': expected})
    write(OUT / 'run-start.json', {'startedAt': now(), 'inputCompletion': ref(OUT / 'input-completion.json'),
        'inputValidation': ref(OUT / 'input-validation.json'), 'preAccountTests': ref(OUT / 'pre-account-tests.json')})
    compact = module('_exit_transfer_compact', 'run-supplemental-force-context.py').compact
    loader = module('_exit_transfer_fee_loader', 'load-market-context-screen.py')
    old = read(pinned(plan['baselineComparison']))
    rows, artifacts = [], {}
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
            require(receipt['planSha256'] == sha(OUT / 'plan.json') and receipt['noSignalsGenerated'] is True,
                    'Input receipt identity differs')
            for item in receipt['files'].values():
                pinned(item)
            artifacts[period][board] = {'baseline': deepcopy(plan['baselineArtifacts'][period][board])}
            baseline = deepcopy(next(r for r in old['rows'] if (r['period'], r['board']) == (period, board)))
            require(baseline['id'] == 'immediate', 'Wrong baseline account policy')
            baseline.update(id='baseline', policy='baseline', label=plan['labels']['baseline'])
            rows.append(baseline)
            data = read(pinned(receipt['accountData']))
            require(digest(data) == receipt['accountDatasetDigest'], 'Account source digest differs')
            signals = read(pinned(receipt['baselineSchedule']))
            require(len(signals) == receipt['signalCount'] and digest(signals) == receipt['signalScheduleSha256'], 'Original source schedule differs')
            for mode in MODES:
                emit({'phase': 'account-start', 'period': period, 'board': board, 'mode': mode, 'signals': len(signals)})
                config = plan['periods'][period]['policyConfigs'][mode]
                engine = make_account(mode, data, signals, config, fee)
                engine.step(20000)
                result, checkpoint = engine.result(), engine.checkpoint()
                require(result['status'] == 'completed' and result['research']['identity']['signalSchedule'] == signals,
                        'Account did not consume the unchanged complete schedule')
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
                emit({'phase': 'account-complete', **{key: row[key] for key in
                    ('period', 'board', 'policy', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
                del engine, result, checkpoint, summary
                gc.collect()
            require(digest(data) == receipt['accountDatasetDigest'] and digest(signals) == receipt['signalScheduleSha256'],
                    'Account execution changed frozen data or signals')
            del data, signals
            gc.collect()
    verify_plan()
    write(OUT / 'comparison.json', {'schema': 'reclaim-exit-transfer-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        **TRIALS, 'noSignalsGenerated': True, 'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
    emit({'phase': 'all-accounts-complete', **ref(OUT / 'comparison.json')})


def self_test():
    base = {'allocation': {'mode': 'single', 'strategyIds': [SID]}, 'strategies': [{'id': SID}],
        'stopLossPct': .05, 'trailingStopPct': .1, 'takeProfitPct': 0., 'maxHoldingDays': 0,
        'initialCapital': 500000, 'maxPositions': 5, 'maxStockWeight': .2, 'untouched': {'x': 1}}
    before = deepcopy(base)
    configs = policy_configs(base)
    require(base == before and configs['baseline'] == configs['trail10'] == base, 'Only extra exit differs in primary comparison')
    require({k for k in base if base[k] != configs['structure10'][k]} == {'trailingStopPct'}, 'Unexpected structural config changes')
    configs['structure10']['untouched']['x'] = 2
    require(base == before and configs['baseline']['untouched']['x'] == 1, 'Config copies must be independent')
    signal = {'signalId': 'original-id', 'strategyId': SID, 'symbol': 'sz000001', 'signalDate': '2022-02-01', 'originalRank': 3}
    signals = [deepcopy(signal)]
    validate_schedule(signals, {'2022-02-01'})
    require(signals == [signal], 'Source signal bytes/values must remain unchanged')
    for bad in ([{**signal, 'strategyId': 'new'}], [signal, signal], [{**signal, 'extra': 1}], [{**signal, 'originalRank': True}]):
        try:
            validate_schedule(bad, {'2022-02-01'})
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid signal accepted')
    calls = []
    def fake_fixed(*args, **kwargs): calls.append(('fixed', args, kwargs)); return 'fixed'
    def fake_structure(*args, **kwargs): calls.append(('structure', args, kwargs)); return 'structure'
    require(make_account('trail10', {}, signals, configs['trail10'], 'fee', fixed_class=fake_fixed, structural_class=fake_structure) == 'fixed', 'trail10 must omit extra exit')
    require(make_account('structure10', {}, signals, configs['structure10'], 'fee', fixed_class=fake_fixed, structural_class=fake_structure) == 'structure', 'structure10 engine')
    require('exit_rule' not in calls[0][2] and calls[1][2]['exit_rule'] == 'structure10', 'Exit dispatch differs')
    try:
        make_account('baseline', {}, signals, base, 'fee', fixed_class=fake_fixed, structural_class=fake_structure)
    except ValueError:
        pass
    else:
        raise AssertionError('Baseline execution allowed')
    pre = {'schema': 'reclaim-exit-transfer-pre-account-tests-v1', 'passed': True, 'planSha256': 'p',
           'allChecksBeforeFirstNewAccount': True, 'productionUnitIncrement': 0, 'unitTests': 57, 'commands': [{'exitCode': 0} for _ in range(5)]}
    proof = {'passed': True, 'planSha256': 'p', 'inputCompletionSha256': 'c', 'verifierSha256': 'v'}
    completion = {'planSha256': 'p', 'allSchedulesFrozenBeforeAccounts': True, 'noSignalsGenerated': True}
    validate_prerequisites(pre, proof, completion, plan_sha='p', completion_sha='c', verifier_sha='v')
    for changed in ({**pre, 'passed': False}, {**pre, 'allChecksBeforeFirstNewAccount': False},
                    {**pre, 'productionUnitIncrement': 57}, {**pre, 'commands': pre['commands'][:4]}):
        try:
            validate_prerequisites(changed, proof, completion, plan_sha='p', completion_sha='c', verifier_sha='v')
        except ValueError:
            pass
        else:
            raise AssertionError('Prerequisite bypass accepted')
    return {'passed': True, 'checks': ['unchanged primary config', 'one structural config difference', 'independent config copies',
        'unchanged original signal identity', 'strict schedule schema', 'dispatch without fifth-session exit',
        'structure10 dispatch', 'baseline execution forbidden', 'bound prerequisites', 'failed or miscounted prerequisites rejected'],
        'actualDataRead': False, 'signalsGenerated': 0, 'accountExecutions': 0, 'persistentFilesWritten': 0, 'productionUnitIncrement': 0}


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
                write(failures / (str(time.time_ns()) + '.json'), {'at': now(), 'mode': args.mode,
                    'type': type(error).__name__, 'message': str(error)})
            raise
