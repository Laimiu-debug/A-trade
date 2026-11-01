"""Frozen contraction/pressure comparison on previously inspected 2024-2025 data.

This extends historical evidence, not a pristine holdout or a profit promise.
No old implementation, registered output, account state or data is modified.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
OUT = BASE / 'supplemental-force-context-screen-v1'
SID = 'rawThreeStageContractionBreakoutDiagnostic'
VARIANTS = (
    ('baseline10', 'baseline', 10, '收缩突破基线'),
    ('pressure10', 'pressure', 10, '加量价压力过滤'),
    ('baseline30', 'baseline', 30, '基线：较高滑点'),
    ('pressure30', 'pressure', 30, '压力过滤：较高滑点'),
)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz')
          else path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def write(path, value):
    with (gzip.open(path, 'xt', encoding='utf-8') if str(path).endswith('.gz')
          else path.open('x', encoding='utf-8')) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  indent=None if str(path).endswith('.gz') else 2)
        stream.write('\n')


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def configurations():
    from service.portfolio_config import normalize_config
    parent = read(ROOT / 'docs/account-exit-comparison-v1.json')['baseConfig']
    configs = {}
    for key, _, bps, _ in VARIANTS:
        config = deepcopy(parent)
        config.update(startDate='2024-01-02', endDate='2025-12-31',
                      stopLossPct=.05, trailingStopPct=.1, takeProfitPct=0.,
                      maxHoldingDays=0, slippageBps=bps)
        config['strategies'] = [{'id': SID, 'kind': 'momentum', 'pool': 'base'}]
        config['allocation'] = {'mode': 'single', 'strategyIds': [SID]}
        configs[key] = normalize_config(config)
    return configs


def cost_parity(config):
    """Check old late-2023 formula against the explicit 2024-25 continuation."""
    from service.signal_target_study_fee_profile import DatedFeePolicy, LegacyFeePolicy
    assert (config['commissionRate'], config['minCommission'], config['transferFeeRate'],
            config['stampDutyRate']) == (.0003, 5., .00001, None)
    old = DatedFeePolicy(commission_rate=.0003, min_commission=5., commission_includes_transfer=False)
    new = LegacyFeePolicy()
    checks = []
    for market in ('SH', 'SZ'):
        context = SimpleNamespace(config=config, market=market)
        for amount in (0., 1., 10000., 100000., 1000000.):
            for side in ('buy', 'sell'):
                expected = max(5., amount * .0003) + amount * .00001 + (amount * .0005 if side == 'sell' else 0.)
                before = old.calculation(context, amount, side, '2023-12-29')
                for day in ('2024-01-02', '2024-12-31', '2025-01-02', '2025-12-31'):
                    after = new.calculation(context, amount, side, day)
                    assert math.isclose(after['breakdown']['total'], expected, abs_tol=1e-10)
                    assert all(value == before['rates'][key] for key, value in after['rates'].items())
                    assert after['breakdown'] == before['breakdown']
                    checks.append({'market': market, 'amount': amount, 'side': side,
                                   'date': day, 'fee': expected, 'matchesLate2023Formula': True})
    return {'checks': checks, 'count': len(checks), 'failures': 0,
            'meaning': 'Formula equivalence only; not broker-charge or full legal fee verification.'}


def intervals():
    result = []
    for year in (2024, 2025):
        result.extend([
            {'id': str(year), 'start': f'{year}-01-01', 'end': f'{year}-12-31', 'kind': 'calendarYear'},
            {'id': f'{year}-H1', 'start': f'{year}-01-01', 'end': f'{year}-06-30', 'kind': 'calendarHalfYear'},
            {'id': f'{year}-H2', 'start': f'{year}-07-01', 'end': f'{year}-12-31', 'kind': 'calendarHalfYear'},
        ])
    for phase in read(BASE / 'market-phase-atlas-v1/atlas.json')['phases']:
        if phase['startDate'] >= '2024-01-01':
            result.append({'id': phase['id'], 'start': phase['startDate'], 'end': phase['endDate'],
                           'kind': 'retrospectiveNarrativePhase', 'title': phase['title']})
    return result


def compact(key, label, schedule, summary):
    closed, account, overall = summary['closedEpisodes'], summary['account'], summary['periods'][0]
    return {'id': key, 'label': label, 'signals': len(schedule), 'closed': closed['count'],
        'winRatePct': closed['winRatePct'], 'meanWinPct': closed['meanWinReturnPct'],
        'meanLossPct': closed['meanLossReturnPct'], 'closedMeanPct': closed['meanReturnPct'],
        'payoffRatio': closed['payoffRatio'], 'profitFactor': closed['profitFactor'],
        'holdingSessions': closed['holdingSessions'], 'maxConsecutiveLosses': closed['maxConsecutiveLosses'],
        'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
        'meanExposurePct': overall['exposurePct']['mean'], 'openPositions': summary['openPositions']['count'],
        'unreliableValuationDays': len(summary['quality']['valuationUnreliableDates']),
        'top5ShareOfGrossProfitPct': summary['concentration']['top5ProfitSharePct'],
        'years': [{'id': p['id'], 'closed': p['closedEpisodes']['count'],
            'closedMeanPct': p['closedEpisodes']['meanReturnPct'], 'accountReturnPct': p['returnPct'],
            'maxDrawdownPct': p['maxDrawdownPct']} for p in summary['periods'] if p['kind'] == 'calendarYear'],
        'phases': [{'id': p['id'], 'start': p['observedStart'], 'end': p['observedEnd'],
            'closed': p['closedEpisodes']['count'], 'accountReturnPct': p['returnPct'],
            'maxDrawdownPct': p['maxDrawdownPct'], 'meanExposurePct': p['exposurePct']['mean']}
            for p in summary['periods'] if p['kind'] == 'retrospectiveNarrativePhase']}


def main():
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    from service.signal_target_study_fee_profile import LegacyFeePolicy
    loader = module('_supplemental_account_source', 'load-supplemental-account-source.py')
    builder = module('_supplemental_force_schedule', 'build-supplemental-contraction-schedule.py')
    configs, periods = configurations(), intervals()
    pin_names = ('scripts/run-supplemental-force-context.py',
        'scripts/load-supplemental-account-source.py', 'scripts/build-supplemental-contraction-schedule.py',
        'scripts/run-signal-contraction-price-proxy.py', 'service/signal_contraction_price_proxy.py',
        'service/force_statistics.py', 'service/research_fixed_signal_account.py',
        'service/research_structural_exits.py', 'service/research_account_fees.py',
        'service/research_account_outcomes.py', 'service/signal_target_study_fee_profile.py',
        'service/research_fee_profile.py', 'service/portfolio_engine.py', 'service/portfolio_signals.py',
        'service/portfolio_config.py', 'docs/account-exit-comparison-v1.json',
        'docs/signal-contraction-price-proxy-v1.json', 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json')
    pins = {name: digest(ROOT / name) for name in pin_names}
    OUT.mkdir(exist_ok=False)
    plan = {'schema': 'supplemental-force-context-screen-v1', 'createdAt': now(),
        'sourcePins': pins, 'sample': {'start': '2024-01-02', 'end': '2025-12-31',
            'expectedSessions': 485, 'expectedSecurities': 3141, 'holdoutClaim': False,
            'meaning': 'Previously inspected supplemental historical validation; current-survivor SH/SZ mainboard sample.'},
        'variants': [{'id': key, 'schedule': kind, 'slippageBpsEachSide': bps, 'label': label}
                     for key, kind, bps, label in VARIANTS],
        'configs': configs, 'entryWeight': .2, 'exitRule': 'noProgress5',
        'signalRules': {'baseline': 'Unchanged stateless contraction detector and eligibility; original daily amount-ranked Top5.',
            'pressure': 'Apply pressure20>0 and pressure5>pressure20 to original Top5; unknown excluded and logged; no backfill.',
            'entry': 'Close-time fixed budget and quantity; one subsequent independent-session opening attempt; no add.',
            'exit': '5 percent stop/10 percent trailing stop at close, followed by eligible open; day-five close<=entry triggers exit.',
            'time': 'Entry session counts as one; day-five unknown is not rechecked later; no profit cap or forced cutoff liquidation.'},
        'fees': {'policyClass': 'LegacyFeePolicy', 'policyId': 'legacy-engine-delegate-v1',
            'commissionRate': .0003, 'minimumPerFill': 5., 'transferRateSeparatelyAdded': .00001,
            'sellStampDutyRate': .0005, 'stampDutyConfig': None,
            'modelChange': 'Prior DatedFeePolicy only supports 2022-2023. Explicitly delegate unchanged late-2023 rate arithmetic for 2024-2025; do not extend or modify the dated class.',
            'officialStampSource': 'https://fgk.chinatax.gov.cn/zcfgk/c102416/c5211343/content.html',
            'transferAndCommission': 'Continued registered assumptions; broker charges and the full historical fee schedule are unverified.',
            'rounding': 'Inherited floating arithmetic; no newly inferred broker rounding.'},
        'costStress': 'Both schedules at fixed 10bps and 30bps per side; extra cost can also change sizes, entries and exits. No search.',
        'evaluationIntervals': periods, 'profitHurdles': None, 'parameterSearch': False,
        'phaseInterpretation': 'Existing retrospective narrative phases are descriptive account intervals only; never used as entry features.',
        'limitations': ['Previously used data, not pristine out-of-sample validation.',
            'Current-survivor mainboard universe omits ChiNext, STAR and Beijing; not all-A-share evidence.',
            'Raw prices, incomplete historical identity/rights and simulated opening execution remain assumptions; no individual-stock repair.',
            'Pressure is a price-volume proxy, not evidence of investor identity, control or inevitable gains.',
            'Retrospective phase boundaries are not a causal trading rule.'],
        'certified': False, 'targetAchieved': False}
    write(OUT / 'plan.json', plan)
    write(OUT / 'fee-parity.json', cost_parity(configs['baseline10']))
    emit({'phase': 'registered', 'accounts': len(VARIANTS), 'planSha256': digest(OUT / 'plan.json')})
    raw, binding, document = loader.load_source(progress=emit)
    emit({'phase': 'source-loaded', 'securities': len(raw['securities'])})
    built = builder.build_schedule(raw, binding, progress=emit)
    assert built['metadata']['scoredSessions'] == 485 and built['metadata']['securities'] == 3141
    write(OUT / 'schedule-evidence.json.gz', built)
    for kind, schedule in built['schedules'].items():
        write(OUT / (kind + '.signals.json'), schedule)
    data, identity = loader.account_view(raw, binding)
    write(OUT / 'source.json', identity)
    del raw
    emit({'phase': 'account-source-ready', 'signalCounts': {k: len(v) for k, v in built['schedules'].items()}})
    rows = []
    for key, kind, bps, label in VARIANTS:
        emit({'phase': 'account-start', 'id': key})
        engine = ResearchExitAccount(data, built['schedules'][kind], configs[key], exit_rule='noProgress5',
                                     entry_weight=.2, fee_policy=LegacyFeePolicy())
        engine.step(20000)
        result, checkpoint = engine.result(), engine.checkpoint()
        assert result['status'] == 'completed' and len(result['curve']) == 485
        summary = summarize_account(result, checkpoint, policy_id=key, policy_label=label, intervals=periods)
        assert not summary['quality']['integrityIssues'], summary['quality']['integrityIssues']
        write(OUT / (key + '.result.json.gz'), result)
        write(OUT / (key + '.summary.json'), summary)
        row = compact(key, label, built['schedules'][kind], summary)
        row['slippageBpsEachSide'] = bps
        rows.append(row)
        emit({'phase': 'account-completed', **{k: row[k] for k in ('id', 'closed', 'accountReturnPct', 'maxDrawdownPct')}})
        del engine, result, checkpoint, summary
    assert all(digest(ROOT / name) == expected for name, expected in pins.items())
    write(OUT / 'comparison.json', {'schema': 'supplemental-force-context-comparison-v1', 'completedAt': now(),
        'rows': rows, 'planSha256': digest(OUT / 'plan.json'), 'schedule': built['metadata'],
        'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
    path = BASE / 'program.json'
    before = path.read_bytes()
    (OUT / 'program-before-update.json').write_bytes(before)
    program = json.loads(before)
    program['progress']['supplementalForceContextScreen'] = {'status': 'completed-awaiting-independent-review',
        'directory': OUT.name, 'configurationsNewlyExecuted': 4, 'newEntryVariants': 0,
        'plan': OUT.name + '/plan.json', 'readout': OUT.name + '/comparison.json',
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    program['progress']['completedFixedCandidateTrials'] += 1
    program['progress']['nextStage'] = '复核未改规则的2024—2025年补充历史与成本压力结果，再按盈亏分布、分年和阶段稳定性决定方向；不按事后阶段标签选股。'
    program['updatedAt'] = now()
    pending = path.with_name('program.supplemental-force-context.pending.json')
    write(pending, program)
    assert path.read_bytes() == before
    pending.replace(path)
    emit({'phase': 'all-completed', 'accounts': len(rows)})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if OUT.exists() and not (OUT / 'failure.json').exists():
            write(OUT / 'failure.json', {'at': now(), 'type': type(error).__name__, 'message': str(error)})
        raise
