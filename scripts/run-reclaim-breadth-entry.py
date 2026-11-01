"""Freeze four market-breadth admission policies on unchanged reclaim setups."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import importlib.util
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('_breadth_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, write, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.write, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'strong-undercut-reclaim-timing-v1'
CONTEXT = BASE / 'undercut-reclaim-context-diagnostic-v1'
OUT = BASE / 'reclaim-breadth-entry-v1'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('knownContext', 'breadthMajority', 'breadthImproving', 'breadthBoth')
LABELS = {'knownContext': '环境完整，不加广度条件', 'breadthMajority': '上涨股票过半',
    'breadthImproving': '上涨范围扩大', 'breadthBoth': '上涨过半且范围扩大'}
PROGRAM_SHA = '1603cd564f8f136cfb12bf3ea91d969b37f6d5d5509adf86f90792ea7d25d3c5'
PARENT_VALIDATION_SHA = '74e1e2eea635291342d615e702ae212fcb427303f2328bec142b33edc6400126'
CONTEXT_VALIDATION_SHA = '9ecdabf3d86a40fed0ee03ecd6c2d79df5d7e717ae79115770f4502f607528f5'


def verify_plan():
    plan = read(OUT / 'plan.json')
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for name, expected in mapping.items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    assert sha(BASE / 'program.json') == PROGRAM_SHA
    assert sha(PARENT / 'validation.json') == PARENT_VALIDATION_SHA
    assert sha(CONTEXT / 'validation.json') == CONTEXT_VALIDATION_SHA
    assert read(PARENT / 'validation.json')['passed'] and read(CONTEXT / 'validation.json')['passed']
    parent, context = read(PARENT / 'plan.json'), read(CONTEXT / 'readout.json')
    inputs = dict(context['inputPins'])
    inputs.update(parent['frozenInputs'])
    for folder, names in ((PARENT, ('plan.json', 'comparison.json', 'validation.json', 'readout.json')),
                          (CONTEXT, ('plan.json', 'contexts.json', 'readout.json', 'validation.json', 'delivery.json'))):
        for name in names:
            path = folder / name
            inputs[path.relative_to(ROOT).as_posix()] = sha(path)
    periods = deepcopy(parent['periods'])
    for period in PERIODS:
        receipt = read(PARENT / period / 'input-completion.json')
        periods[period]['accountData'] = receipt['accountData']
        cache = ROOT / receipt['accountData']['path']
        assert sha(cache) == receipt['accountData']['sha256']
        inputs[receipt['accountData']['path']] = receipt['accountData']['sha256']
        files = {}
        for name in ('source.json', 'input-completion.json', 'signal-metadata.json', 'opportunities.jsonl.gz',
                     'immediate.signals.json', 'immediate.result.json.gz', 'immediate.summary.json', 'immediate.checkpoint.json.gz'):
            path = PARENT / period / name
            files[name] = ref(path)
            inputs[path.relative_to(ROOT).as_posix()] = sha(path)
        periods[period]['baselineFiles'] = files
        periods[period]['originalSignalCount'] = len(read(PARENT / period / 'immediate.signals.json'))
        periods[period]['contextBinding'] = context['sourceBindings'][period]
    names = list(parent['sourcePins']) + ['scripts/diagnose-reclaim-context.py',
        'service/research_reclaim_context.py', 'scripts/run-reclaim-breadth-entry.py',
        'service/research_reclaim_breadth.py', 'tests/test_research_reclaim_breadth.py']
    plan = {'schema': OUT.name, 'registeredAt': now(),
        'sourcePins': {p: sha(ROOT / p) for p in names}, 'frozenInputs': inputs,
        'previousTurn': {'classification': 'progress', 'evidence': ref(CONTEXT / 'delivery.json'),
            'meaning': 'Completed original3803 formations/T-1 context attribution and2024-09-24 event split; zero new candidate batches.'},
        'strategyId': parent['strategyId'], 'periods': periods, 'modes': list(MODES), 'labels': LABELS,
        'baselineMode': 'immediate', 'referenceAccountsReused': 2, 'newPolicyVariants': 4,
        'newAccounts': 8, 'newSetupMechanisms': 0, 'newIndicators': 0,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'novelty': 'Previously studied breadth ingredients applied to unchanged strong-stock undercut/reclaim immediate opportunities. Unlike prior contraction gates, all inputs use T-1 and one matched intersection. This is a development-stage mechanism replication, not discovery of a new indicator.',
        'rules': {
            'population': 'All3803 frozen immediate originalTop5 formations, before their original admission/execution/outcome. Retain signal IDs, strategy IDs, originalRank and chronological input order. No backfill or reranking.',
            'context': 'Reuse independently audited context. Rebuild all969 daily rows from pinned daily panels and exact source calendar before any candidate account. A=T-1; comparison E=T-6; both axes use identical matched5 paired sample.',
            'knownContext': 'K: Admit every known formation context. Unknown evidence stays separately counted and is not admitted in any of the four policies.',
            'breadthMajority': 'L: K AND paired positive20-session-return breadth at A strictly greater than0.5.',
            'breadthImproving': 'M: K AND paired breadth change from E to A strictly greater than0 percentage points.',
            'breadthBoth': 'B: K AND both L and M. Equality fails the applicable condition. No minimum coverage addition or threshold search.',
            'entry': 'Only new-entry eligibility changes. T-close decision and one T+1 source-session opening attempt. Keep original20-percent close-time budget,5 slots,cash,prior-volume participation and current holding constraints.',
            'exit': 'Unchanged noProgress5 ResearchExitAccount:5-percent close stop,10-percent close trailing,day-five no-progress exit from actual fill; later eligible-open execution. No environment-driven forced exit, profit cap or holding expiry.',
            'fees': 'Same policy as each parent: bound DatedFeePolicy old period; LegacyFeePolicy new period;10bps slippage each side. Broker-specific charges remain unverified.',
            'baseline': 'Reuse full immediate baseline without removing unknown setups. K-minus-original separates the evidence-availability policy change; primary L-minus-K and secondary M-minus-K/B-minus-L/B-minus-M use a common known opportunity domain.',
            'account': 'Start each candidate with500000 and no positions; replay entire period independently. Do not filter original executed trades or add grouped original PnL. Capital, holdings and later admissions can differ.',
            'eventBoundary': '2024-09-24 is descriptive only. New-period curve intervals preserve prior-day equity/carried positions; the date does not gate signals.',
            'comparisons': 'Report every policy, all four formation states and unknown counts, both full periods, all years/halves/16 atlas phases and the user event split. B-L-M+K is descriptive policy interaction, not randomized causal effect.',
            'interpretation': 'Evaluate net payoff distribution, open positions, concentration, drawdown and exposure together. Lower exposure or lower drawdown alone is not an improvement claim.'},
        'limitations': parent['limitations'] + [
            'Both periods and their context outcomes already informed research; neither is untouched validation.',
            'The complete four-policy design controls context availability across candidates, not stock/date dependence or all path effects.',
            'Shared price-volume proxies do not identify main-force, retail or quantitative-fund ownership.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'registered': True, **ref(OUT / 'plan.json')})


def prepare():
    from service.research_reclaim_context import build_context_rows
    from service.research_reclaim_breadth import filter_reclaim_breadth
    plan = verify_plan()
    audited = read(CONTEXT / 'contexts.json')
    diagnostic = module('_reclaim_context_binding', 'diagnose-reclaim-context.py')
    for period in PERIODS:
        folder = OUT / period
        folder.mkdir(exist_ok=False)
        metadata = read(PARENT / period / 'signal-metadata.json')
        daily, binding = diagnostic.market_binding(period)
        assert binding == plan['periods'][period]['contextBinding']
        contexts = build_context_rows(metadata['calendar'], daily, metadata['targetDates'])
        assert contexts == audited[period]
        signals = read(PARENT / period / 'immediate.signals.json')
        result = filter_reclaim_breadth(signals, contexts, calendar=metadata['calendar'])
        assert list(result['schedules']) == list(MODES)
        assert result['inputSignalCount'] == plan['periods'][period]['originalSignalCount']
        shutil.copyfile(PARENT / period / 'source.json', folder / 'source.json')
        write(folder / 'contexts.json', contexts)
        for mode in MODES:
            write(folder / (mode + '.signals.json'), result['schedules'][mode])
            write(folder / (mode + '.evidence.json'), result['evidence'][mode])
        write(folder / 'input-counts.json', result['counts'])
        write(folder / 'input-completion.json', {'completedAt': now(), 'period': period,
            'planSha256': sha(OUT / 'plan.json'), 'counts': result['counts'],
            'accountData': plan['periods'][period]['accountData'],
            'files': {p.name: ref(p) for p in folder.iterdir() if p.is_file()}})
        emit({'phase': 'inputs-complete', 'period': period, 'counts': result['counts']})
    verify_plan()
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': {p: ref(OUT / p / 'input-completion.json') for p in PERIODS},
        'allSchedulesFrozenBeforeAccounts': True})


def run():
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan()
    completion = read(OUT / 'input-completion.json')
    assert completion['planSha256'] == sha(OUT / 'plan.json') and completion['allSchedulesFrozenBeforeAccounts']
    write(OUT / 'run-start.json', {'startedAt': now(), 'inputCompletion': ref(OUT / 'input-completion.json')})
    compact = module('_breadth_compact', 'run-supplemental-force-context.py').compact
    loader = module('_breadth_fee_loader', 'load-market-context-screen.py')
    rows, artifacts = [], {}
    for period in PERIODS:
        folder = OUT / period
        assert sha(folder / 'input-completion.json') == completion['outputs'][period]['sha256']
        receipt = read(folder / 'input-completion.json')
        for r in receipt['files'].values():
            assert sha(ROOT / r['path']) == r['sha256']
        cache = receipt['accountData']
        assert sha(ROOT / cache['path']) == cache['sha256']
        data, source = read(ROOT / cache['path']), read(folder / 'source.json')
        assert source == plan['periods'][period]['sourceIdentity']
        assert digest(data) == source['accountDatasetDigest']
        if period == '2022-2023':
            helper = loader._module('run-direction-account-screen.py').load_helper()
            fee = helper.fee_policy(helper.load_binding()['document'])
        else:
            fee = LegacyFeePolicy()
        for mode in MODES:
            signals = read(folder / (mode + '.signals.json'))
            emit({'phase': 'account-start', 'period': period, 'mode': mode, 'signals': len(signals)})
            engine = ResearchExitAccount(data, signals, deepcopy(plan['periods'][period]['config']),
                exit_rule='noProgress5', entry_weight=.2, fee_policy=fee)
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            assert result['status'] == 'completed'
            summary = summarize_account(result, checkpoint, policy_id=mode, policy_label=LABELS[mode])
            assert not summary['quality']['integrityIssues']
            for suffix, value in (('.result.json.gz', result), ('.checkpoint.json.gz', checkpoint), ('.summary.json', summary)):
                write(folder / (mode + suffix), value)
            row = compact(mode, LABELS[mode], signals, summary)
            row.update(period=period)
            rows.append(row)
            emit({'phase': 'account-complete', **{k: row[k] for k in
                ('period', 'id', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
            del engine, result, checkpoint, summary
            gc.collect()
        artifacts[period] = {p.name: ref(p) for p in folder.iterdir() if p.is_file()
            and any(tag in p.name for tag in ('.result.', '.summary.', '.checkpoint.'))}
        del data
        gc.collect()
    verify_plan()
    write(OUT / 'comparison.json', {'schema': OUT.name + '-comparison', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newPolicyVariants': 4, 'newAccounts': 8, 'referenceAccountsReused': 2,
        'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
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
