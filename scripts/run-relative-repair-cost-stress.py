"""Frozen-signal, full-account slippage sensitivity across both existing periods."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'relative-repair-screen-v1'
OUT = BASE / 'relative-repair-cost-stress-v1'
PERIODS = ('2022-2023', '2024-2025')
TIERS = (('control10', 10), ('control30', 30), ('control50', 50))
PARENT_PINS = {
    'plan.json': 'c0b649c81716ae32b2e3503404f2142a076dd41c05d1d409fc712250d0d252bd',
    'comparison.json': '36c659a0ebfd1b24d382420669493a2296b62b25164bb7fd0a830e448824c21a',
    'validation.json': 'a69150374f0c048e43ed9d5f487416af72606bde7bb189149cfc34ba00c57eab',
    'event-outcomes.json': 'fcb05454e30bed2d98bb97e6fd432f761f1986a691b383daad4f905f8d34daa8',
    'readout-v2.json': '85bc86c50714368db3c330d4315fd15087be7fabd376e5c700835953f4ba9573',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz')
          else path.open(encoding='utf-8')) as handle:
        return json.load(handle)


def write(path, value):
    with (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) if path.name.endswith('.gz')
          else path.open('x', encoding='utf-8')) as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False,
                  separators=(',', ':') if path.name.endswith('.gz') else None,
                  indent=None if path.name.endswith('.gz') else 2)
        handle.write('\n')


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def ref(path):
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def check_config(base, changed, bps):
    assert bps in (10, 30, 50)
    expected = deepcopy(base)
    expected['slippageBps'] = bps
    assert changed == expected, 'Only slippageBps may differ'


def bindings():
    frozen = {}
    for name, expected in PARENT_PINS.items():
        path = PARENT / name
        assert sha(path) == expected, name
        frozen[path.relative_to(ROOT).as_posix()] = expected
    plan, validation = read(PARENT / 'plan.json'), read(PARENT / 'validation.json')
    assert validation['passed'] and validation['failureCount'] == 0
    for name, expected in plan['sourcePins'].items():
        assert sha(ROOT / name) == expected, name
    periods, loaded = {}, {}
    for period in PERIODS:
        parent = PARENT / period
        paths = {'result': parent / 'matchedControl.result.json.gz',
                 'summary': parent / 'matchedControl.summary.json',
                 'signals': parent / 'matchedControl.signals.json', 'source': parent / 'source.json'}
        references = {key: ref(path) for key, path in paths.items()}
        for record in references.values():
            short = str(Path(record['path']).relative_to(PARENT.relative_to(ROOT))).replace('\\', '/')
            assert record['sha256'] == validation['inputArtifacts'][short], short
            frozen[record['path']] = record['sha256']
        projection, evidence = parent / 'signal-input.json.gz', parent / 'signals-evidence.json.gz'
        for path in (projection, evidence):
            assert sha(path) == validation['inputArtifacts'][path.relative_to(PARENT).as_posix()]
            frozen[path.relative_to(ROOT).as_posix()] = sha(path)
        result, summary, signals, identity = [read(paths[key]) for key in ('result', 'summary', 'signals', 'source')]
        config = deepcopy(result['config'])
        assert config == plan['periods'][period]['config']
        assert config['slippageBps'] == 10 and result['status'] == 'completed'
        assert signals == result['research']['identity']['signalSchedule']
        assert digest(signals) == result['research']['identity']['signalScheduleSha256']
        assert summary['inputResultSha256'] == digest(result)
        assert result['dataDigest'] == identity['accountDatasetDigest']
        assert result['fingerprint'] == identity['accountSnapshotFingerprint']
        assert result['research']['structuralExits']['rule'] == 'noProgress5'
        assert result['research']['identity']['entryWeight'] == .2
        configs = {}
        for key, bps in TIERS:
            configs[key] = deepcopy(config)
            configs[key]['slippageBps'] = bps
            check_config(config, configs[key], bps)
        periods[period] = {'baseline': references, 'config': config, 'configs': configs,
                           'sourceIdentity': identity, 'sourceProjection': ref(projection)}
        loaded[period] = {'result': result, 'summary': summary, 'signals': signals}
    return periods, loaded, frozen


def self_test():
    base = {'slippageBps': 10, 'stopLossPct': .05, 'allocation': {'strategyIds': ['same']}}
    original = deepcopy(base)
    for _, bps in TIERS:
        changed = deepcopy(base)
        changed['slippageBps'] = bps
        check_config(base, changed, bps)
    rejects = 0
    for key, value in (('stopLossPct', .07), ('allocation', {'strategyIds': ['other']})):
        changed = deepcopy(base)
        changed.update(slippageBps=30)
        changed[key] = value
        try:
            check_config(base, changed, 30)
        except AssertionError:
            rejects += 1
    assert rejects == 2 and base == original
    emit({'selfTestsPassed': True, 'validConfigs': 3, 'forbiddenConfigChangesRejected': rejects,
          'marketOutcomesRead': False})


def progress(status, completed=False):
    path = BASE / 'program.json'
    before = path.read_bytes()
    value = json.loads(before)
    assert value['goalStatus'] == 'active'
    with (OUT / ('program-before-' + status + '.json')).open('xb') as handle:
        handle.write(before)
    value['progress']['relativeRepairCostStress'] = {
        'status': status, 'directory': OUT.name, 'newEntryVariants': 0,
        'costStressLevels': [30, 50], 'configurationsNewlyExecuted': 4,
        'reusedBaselineAccounts': 2, 'plan': OUT.name + '/plan.json',
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    assert value['progress']['completedFixedCandidateTrials'] == 25
    if completed:
        value['progress']['completedFixedCandidateTrials'] += 1
    value['progress']['lastTurnClassification'] = 'progress: prior frozen repair comparison independently verified; now testing unchanged matched-control opportunities under two larger slippage assumptions in both periods.'
    value['progress']['nextStage'] = '完成两时期固定信号的成本压力比较、独立账户核算和全部事件损益拆分；不因成本表现调整信号规则。'
    value['updatedAt'] = now()
    pending = path.with_name('program.relative-repair-cost.pending.json')
    write(pending, value)
    assert path.read_bytes() == before
    pending.replace(path)


def main():
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    self_test()
    periods, loaded, frozen = bindings()
    loader = module('_repair_cost_loader', 'load-market-context-screen.py')
    compact = module('_repair_cost_compact', 'run-supplemental-force-context.py').compact
    source_names = ('scripts/run-relative-repair-cost-stress.py', 'scripts/load-market-context-screen.py',
        'scripts/load-supplemental-account-source.py', 'scripts/run-supplemental-force-context.py',
        'service/research_structural_exits.py', 'service/research_fixed_signal_account.py',
        'service/research_account_fees.py', 'service/research_account_outcomes.py',
        'service/signal_target_study_fee_profile.py', 'service/research_fee_profile.py',
        'service/portfolio_engine.py', 'service/portfolio_config.py')
    plan = {
        'schema': 'relative-repair-cost-stress-v1', 'createdAt': now(),
        'sourcePins': {name: sha(ROOT / name) for name in source_names}, 'frozenInputs': frozen,
        'periods': periods, 'tiers': [{'id': key, 'slippageBps': bps, 'reused': bps == 10} for key, bps in TIERS],
        'entryWeight': .2, 'exitRule': 'noProgress5', 'newAccounts': 4, 'reusedBaselineAccounts': 2,
        'newEntryVariants': 0, 'parameterSearch': False, 'profitHurdles': None,
        'previousGoalTurn': {'classification': 'progress', 'readout': 'relative-repair-screen-v1/readout-v2.json',
                            'sha256': PARENT_PINS['readout-v2.json']},
        'method': [
            'Use complete unchanged parent matchedControl opportunity schedules, preserving original identities, dates, ranks and order; do not regenerate signals or filter prior executed trades.',
            '10bps accounts are referenced, not rerun. At 30 and50bps each side, start each two-year account at500000 and run the original full engine; no cash/holding/risk-state reuse from another cost tier.',
            'Only slippageBps differs from the original per-period config. All exit, allocation, budget, liquidity gate and dated/Legacy fee assumptions remain fixed.',
            'Higher slippage can alter opening eligibility, lot size, risk-entry reference, exit timing and later capital; returns need not be monotone. This is path sensitivity, not subtracting fees from frozen fills.',
            'Keep both prior periods and all entry events, terminal open positions and valuation flags. No event or security removed after observing results.',
            '30/50bps are declared stress scenarios, not measured actual opening liquidity or a guarantee that this spans real execution costs.',
        ],
        'limitations': [
            'Already-inspected historical mainboard survivor sample, not pristine out of sample or all-A-share history.',
            'Raw unadjusted prices, historical member/ST/rights evidence and simulated opening capacity retain parent limitations.',
            'Two separate two-year capital paths; do not splice into a continuous four-year account.',
            'Control selected for follow-up after seeing prior comparison; passing cost stress alone cannot establish stable profitability.',
            'Source fees are inherited assumptions, not newly audited broker charges. No live trading, account modification or individual discontinuity investigation.',
        ], 'certified': False, 'targetAchieved': False,
    }
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    progress('running')
    emit({'phase': 'registered-cost-stress', 'planSha256': sha(OUT / 'plan.json'), 'newAccounts': 4})
    rows, artifacts = [], {}
    for period in PERIODS:
        destination = OUT / period
        destination.mkdir()
        binding, baseline = periods[period], loaded[period]
        signals = baseline['signals']
        signal_bytes = (ROOT / binding['baseline']['signals']['path']).read_bytes()
        with (destination / 'signals.json').open('xb') as handle:
            handle.write(signal_bytes)
        assert sha(destination / 'signals.json') == binding['baseline']['signals']['sha256']
        write(destination / 'baseline-reference.json', binding['baseline'])
        baseline_row = compact('control10', '原对照：每边10bps', signals, baseline['summary'])
        baseline_row.pop('phases', None)
        baseline_row.update(period=period, slippageBps=10, reused=True)
        rows.append(baseline_row)
        raw, data, identity, fee = loader.load_account_source(period, progress=emit)
        assert identity == binding['sourceIdentity']
        del raw
        write(destination / 'source.json', identity)
        for key, bps in TIERS[1:]:
            config = deepcopy(binding['configs'][key])
            check_config(binding['config'], config, bps)
            signal_digest = digest(signals)
            started = time.monotonic()
            emit({'phase': 'account-start', 'period': period, 'id': key, 'slippageBps': bps})
            engine = ResearchExitAccount(data, signals, config, exit_rule='noProgress5', entry_weight=.2, fee_policy=fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            assert result['status'] == 'completed'
            assert result['config'] == binding['configs'][key]
            assert digest(signals) == signal_digest
            assert result['research']['identity']['signalSchedule'] == signals
            assert result['dataDigest'] == identity['accountDatasetDigest']
            assert result['fingerprint'] == identity['accountSnapshotFingerprint']
            assert result['research']['feeLedger']['identity']['settings'] == baseline['result']['research']['feeLedger']['identity']['settings']
            label = f'原对照：每边{bps}bps'
            summary = summarize_account(result, checkpoint, policy_id=key, policy_label=label)
            assert not summary['quality']['integrityIssues']
            write(destination / (key + '.result.json.gz'), result)
            write(destination / (key + '.summary.json'), summary)
            row = compact(key, label, signals, summary)
            row.pop('phases', None)
            row.update(period=period, slippageBps=bps, reused=False, elapsedSeconds=time.monotonic() - started)
            rows.append(row)
            emit({'phase': 'account-completed', **{k: row[k] for k in
                ('period', 'id', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
            del engine, result, checkpoint, summary
        artifacts[period] = {path.name: sha(path) for path in destination.iterdir() if path.is_file()}
        del data
    assert len(rows) == 6
    for name, expected in plan['sourcePins'].items():
        assert sha(ROOT / name) == expected, name
    for name, expected in frozen.items():
        assert sha(ROOT / name) == expected, name
    write(OUT / 'comparison.json', {'schema': 'relative-repair-cost-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newAccounts': 4, 'reusedBaselineAccounts': 2, 'limitations': plan['limitations'],
        'profitHurdles': None, 'certified': False, 'targetAchieved': False})
    progress('completed-awaiting-independent-review', completed=True)
    emit({'phase': 'cost-stress-completed', 'comparisonSha256': sha(OUT / 'comparison.json'), 'rows': 6})


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test()
    else:
        assert not sys.argv[1:], 'Only --self-test is supported'
        main()
