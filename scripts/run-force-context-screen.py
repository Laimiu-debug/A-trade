"""Test force statistics as entry context on a previously fixed structure rule."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'force-context-screen-v1'
PARENT = BASE / 'contraction-policy-screen-v1'
SID = 'rawThreeStageContractionBreakoutDiagnostic'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def main():
    from service.force_statistics import prepare_force_statistics
    from service.portfolio_config import normalize_config
    from service.research_account_outcomes import summarize_account
    from service.research_structural_exits import ResearchExitAccount

    first = module('_force_context_shared', ROOT / 'scripts/run-force-statistics-screen.py')
    prior = module('_force_context_sources', ROOT / 'scripts/run-direction-account-screen.py')
    OUT.mkdir(exist_ok=False)
    signals, source = prior.opportunities(SID, 'signal-contraction-price-v1', 131)
    parent_plan = first.read(PARENT / 'plan.json')
    parent_rule = next(p for p in parent_plan['policies'] if p['id'] == 'noProgress5')
    assert parent_rule['extraExit'] == 'noProgress5' and parent_rule['stopLossPct'] == .05 and parent_rule['trailingStopPct'] == .1
    assert parent_rule['takeProfitPct'] == 0 and parent_rule['maxHoldingDays'] == 0
    reference = first.read(PARENT / 'noProgress5.summary.json')
    assert reference['closedEpisodes']['count'] == 60
    names = ('scripts/run-force-context-screen.py', 'scripts/run-force-statistics-screen.py',
             'service/force_statistics.py', 'service/research_structural_exits.py')
    pins = {name: first.digest(ROOT / name) for name in names}
    plan = {'schema': 'force-context-screen-v1', 'createdAt': first.now(), 'source': source,
        'sourcePins': pins, 'reference': {'path': 'contraction-policy-screen-v1/noProgress5.summary.json',
            'sha256': first.digest(PARENT / 'noProgress5.summary.json'), 'mode': 'reuse-identical-frozen-account'},
        'context': 'Same complete contraction breakout source; previously fixed day-five no-progress exit.',
        'filters': {'direction': 'direction20 > 0 and direction5 > direction20',
                    'pressure': 'pressure20 > 0 and pressure5 > pressure20'},
        'interpretation': 'Positive medium-window pressure with stronger recent pressure; two fixed statistics, no threshold search.',
        'entry': 'Apply at original signal close; preserve original Top5 ranks without backfill; one next-opening attempt.',
        'exitRule': parent_rule, 'account': parent_plan['account'], 'fees': parent_plan['fees'],
        'executionClock': 'No-progress exit tests session five only and queues subsequent eligible open; unchanged T+1 and cash constraints.',
        'sample': parent_plan['sample'], 'profitHurdles': None,
        'configurationsNewlyExecuted': 2, 'referenceAccountsReused': 1,
        'limitations': ['Reused development sample; not independent validation.',
            'Conditional filters and account capacity alter future trades; the comparison does not prove an investor-control mechanism.',
            'Historical universe, raw prices and fill assumptions are inherited; no individual-stock repair.'],
        'certified': False, 'targetAchieved': False}
    first.write(OUT / 'plan.json', plan)

    def progress(status):
        path = BASE / 'program.json'
        data = first.read(path)
        data['progress']['forceContextScreen'] = {'status': status, 'directory': OUT.name,
            'configurationsNewlyExecuted': 2, 'referenceAccountsReused': 1,
            'plan': OUT.name + '/plan.json', 'certified': False, 'targetAchieved': False}
        if status == 'completed':
            data['progress']['completedFixedCandidateTrials'] += 1
            data['progress']['forceContextScreen']['readout'] = OUT.name + '/comparison.json'
        data['progress']['nextStage'] = '检验主散量价统计作为收缩突破的入场过滤是否有增益，按实际盈亏与分年结果决定保留方向；不逐股排查断点，不设盈利门槛。'
        data['updatedAt'] = first.now()
        pending = path.with_name('program.force-context.pending.json')
        first.write(pending, data)
        pending.replace(path)

    progress('running')
    print(json.dumps({'phase': 'load-source-context'}), flush=True)
    helper = first.load_helper()
    binding = helper.load_binding()
    raw = helper.load_dataset(binding)
    data, identity = helper.account_view(raw, binding)
    assert identity == first.read(PARENT / 'source.json')
    first.write(OUT / 'source.json', identity)
    needed = {r['symbol'] for r in signals}
    features = {}
    for security in data['securities']:
        if security['symbol'] in needed:
            prepared = prepare_force_statistics(security['bars'], calendar=data['calendar'])
            features[security['symbol']] = {r['date']: r for r in prepared['rows']}
    schedules, evidence = {}, []
    for kind in ('direction', 'pressure'):
        chosen = []
        for signal in signals:
            row = features[signal['symbol']][signal['signalDate']]
            short, long = row[kind + '5'], row[kind + '20']
            status = 'unknown' if short is None or long is None else 'included' if long > 0 and short > long else 'excluded'
            evidence.append({**signal, 'filter': kind, 'short5': short, 'long20': long, 'status': status})
            if status == 'included':
                chosen.append(signal)
        schedules[kind] = chosen
        first.write(OUT / (kind + '.signals.json'), chosen)
    first.write(OUT / 'filter-evidence.json', evidence)
    print(json.dumps({'phase': 'context-filtered', 'original': len(signals),
        'selected': {k: len(v) for k, v in schedules.items()}}), flush=True)
    rows = [first.compact('reference', '收缩突破＋第5日未走强退出', signals, reference)]
    first.write(OUT / 'reference.summary.json', reference)
    for kind, label in (('direction', '加同口径涨跌量过滤'), ('pressure', '加量价压力过滤')):
        config = deepcopy(binding['document']['baseConfig'])
        config.update(stopLossPct=.05, trailingStopPct=.1, takeProfitPct=0., maxHoldingDays=0)
        config['strategies'] = [{'id': SID, 'kind': 'momentum', 'pool': 'base'}]
        config['allocation'] = {'mode': 'single', 'strategyIds': [SID]}
        config = normalize_config(config)
        print(json.dumps({'phase': 'context-account-start', 'id': kind}), flush=True)
        engine = ResearchExitAccount(data, schedules[kind], config, exit_rule='noProgress5',
            entry_weight=.2, fee_policy=helper.fee_policy(binding['document']))
        engine.step(20000)
        result, checkpoint = engine.result(), engine.checkpoint()
        assert result['status'] == 'completed'
        summary = summarize_account(result, checkpoint, policy_id=kind, policy_label=label)
        assert not summary['quality']['integrityIssues']
        helper.write_gzip(OUT / (kind + '.result.json.gz'), result)
        first.write(OUT / (kind + '.summary.json'), summary)
        row = first.compact(kind, label, schedules[kind], summary)
        rows.append(row)
        print(json.dumps({'phase': 'context-account-completed', **{k: row[k] for k in
            ('id', 'closed', 'closedMeanPct', 'accountReturnPct', 'maxDrawdownPct')}}, ensure_ascii=False), flush=True)
        del engine, result, checkpoint, summary
    assert all(first.digest(ROOT / name) == expected for name, expected in pins.items())
    first.write(OUT / 'comparison.json', {'schema': 'force-context-comparison-v1', 'completedAt': first.now(),
        'rows': rows, 'planSha256': first.digest(OUT / 'plan.json'), 'limitations': plan['limitations'],
        'certified': False, 'targetAchieved': False})
    progress('completed')
    print(json.dumps({'phase': 'context-all-completed'}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUT.exists() and not (OUT / 'failure.json').exists():
            with (OUT / 'failure.json').open('x', encoding='utf-8') as stream:
                json.dump({'at': datetime.now(timezone.utc).isoformat(),
                    'error': type(error).__name__, 'message': str(error)}, stream, ensure_ascii=False)
        raise
