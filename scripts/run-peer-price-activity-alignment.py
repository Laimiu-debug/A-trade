"""Frozen fixed-peer activity / relative-price alignment; research accounts only."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
import gc
import gzip
import importlib.util
import math
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('_prior_peer_runner', ROOT / 'scripts/run-peer-amount-persistence.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
now, sha, digest, read, records, write, write_record, ref, emit, module = (
    prior.now, prior.sha, prior.digest, prior.read, prior.records, prior.write,
    prior.write_record, prior.ref, prior.emit, prior.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'peer-amount-persistence-v1'
OUT = BASE / 'peer-price-activity-alignment-v1'
PERIODS = prior.PERIODS
SID = prior.SID
MODES = ('currentActivity', 'priceProgress', 'activityAligned')
LABELS = {'currentActivity': '当日同伴成交活跃', 'priceProgress': '成交活跃且十日相对价格推进',
          'activityAligned': '成交活跃、相对价格推进且日度量价同步'}
SOURCE_FILES = (*prior.SOURCE_FILES, 'scripts/run-peer-price-activity-alignment.py',
                'service/research_peer_activity_alignment.py', 'tests/test_research_peer_activity_alignment.py')


def verify_plan():
    plan = read(OUT / 'plan.json')
    for key in ('sourcePins', 'frozenInputs'):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    assert sha(PARENT / 'plan.json') == 'cef2279b5582bb06cc525c37c2df100f366adef4e809870818561bfd689537c7'
    assert sha(PARENT / 'validation.json') == 'cd06d5cb09b045a88c1489399fb9d8c6ed816f314b6e721fdf4cb082558b0851'
    old = read(PARENT / 'plan.json')
    validation = read(PARENT / 'validation.json')
    assert validation['passed']
    inputs = [PARENT / name for name in ('plan.json', 'validation.json', 'input-completion.json', 'comparison.json')]
    inputs.append(BASE / 'market-phase-atlas-v1/atlas.json')
    for period in PERIODS:
        inputs.extend(PARENT / period / name for name in ('source.json', 'account-data.json.gz',
            'opportunities.jsonl.gz', 'parent-selection.json.gz', 'input-completion.json',
            'currentActivity.signals.json', 'currentActivity.result.json.gz', 'currentActivity.summary.json'))
    plan = {'schema': 'peer-price-activity-alignment-v1', 'createdAt': now(),
        'sourcePins': {p: sha(ROOT / p) for p in SOURCE_FILES},
        'frozenInputs': {p.relative_to(ROOT).as_posix(): sha(p) for p in inputs},
        'periods': deepcopy(old['periods']), 'modes': list(MODES), 'strategyId': SID,
        'strategyIdMeaning': 'Retain batch28 namespace and exact account config to require entire control result equality.',
        'newCandidateVariants': 2, 'newAccounts': 6, 'candidateAccounts': 4,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'rules': {
            'parentAndAmount': 'Reuse all397 original temporalTop5 opportunities, original ranks, F=T-10 frozen20 peers and entire audited30-slot amount evidence from batch28. No new ranking, backfill or raw reload.',
            'priceWindow': 'F..T exact11 source-calendar slots; ten log returns F+1..T. Use unchanged peer-expansion _window/_logs raw-basis OHLCV/envelope/suspension/exact-date checks.',
            'activity': 'a_d = original peer20 amount / U(T) amount for each last10 amount rows; U(T) is the same complete-case original sample over30slots.',
            'price': 'g_d=fsum(twenty sorted-peer close log returns)/20; b_d=benchmark close log return; r_d=g_d-b_d; netR=fsum(r_d).',
            'covariance': 'meanA=fsum(a)/10; meanR=fsum(r)/10; cov=fsum((a-meanA)*(r-meanR))/10. Exactly constant sequences center0. No variance division or epsilon.',
            'currentActivity': 'C: original amount control S_T>B; all three policies share full amount+price completeness.',
            'priceProgress': 'P: C AND netR strictly >0.',
            'activityAligned': 'PC: P AND cov strictly >0.',
            'unknown': 'Any unknown amount, incomplete group or invalid11-slot price window => all three flags null, including original C=false. No zero imputation or shortened window.',
            'execution': old['rules']['execution'], 'account': old['rules']['account'],
            'entryTiming': 'Unchanged original T close decision / next eligible attempt. This experiment does not test earlier entry.'},
        'comparisons': {'primary': 'activityAligned minus priceProgress', 'secondary': 'priceProgress minus currentActivity'},
        'diagnostics': ['Absolute peer and benchmark log advances; cov(a,g) and cov(a,b) decomposition.',
            'Final-day centered product and absolute contribution share; covariance recentered without last day.',
            'Diagnostics are descriptive only, never extra filters, thresholds, or post-hoc sign reversal.',
            'Same-marginal re-pairing synthetic test distinguishes covariance from total price progression.'],
        'evaluation': ['Freeze both-period schedules before any account result; C schedules and full C results must exactly equal batch28.',
            'Count both P and PC as attempted filters. Six complete fresh accounts; no choosing a winner by arbitrary return hurdles.',
            'Report net account return/drawdown, completed payoff distribution, exposure/open holdings and every year/halfyear/16 retrospective phases.',
            'Retain all397 opportunities and unknowns; no causal interpretation of filtered-account differences.'],
        'limitations': old['limitations'] + [
            'Same-day amount contains price, creating a mechanical component; covariance does not identify main-force ownership, control, inflow or future price impact.',
            'Relative advance can result from a falling benchmark; show absolute components.',
            'Amount weights and equal-peer price weights differ; a few large-amount members may dominate activity.',
            'Ten paired days and selection on final-day activity can make the final day influential; this is not evidence of stable accumulation.',
            'U(T) retrospectively reconstructs past shares using information available by T, not the universe known on each earlier day.',
            'Original strong-price and correlation selection remain; this is not an independent causal factor or an entry-timing repair.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'phase': 'registered', **ref(OUT / 'plan.json')})


def covariance(a, b):
    assert len(a) == len(b) and a
    if all(x == a[0] for x in a) or all(x == b[0] for x in b):
        return 0.
    ma, mb = math.fsum(a)/len(a), math.fsum(b)/len(b)
    return math.fsum((x-ma)*(y-mb) for x, y in zip(a, b))/len(a)


def diagnostics(result):
    names = ('absolutePeerLogAdvance', 'benchmarkLogAdvance', 'covActivityPeer',
             'covActivityBenchmark', 'covDecompositionResidual', 'lastCenteredProduct',
             'lastAbsContributionShare', 'covWithoutLastDay')
    if not result['complete']:
        return dict.fromkeys(names)
    rows = result['dailyRows']
    a, g, b, r = ([row[key] for row in rows] for key in
        ('activityShare', 'peerMeanLogReturn', 'benchmarkLogReturn', 'relativeLogReturn'))
    cg, cb = covariance(a, g), covariance(a, b)
    products = result['centeredProducts']
    denominator = math.fsum(abs(x) for x in products)
    return dict(zip(names, (math.fsum(g), math.fsum(b), cg, cb, result['cov']-(cg-cb),
        products[-1], abs(products[-1])/denominator if denominator else None, covariance(a[:-1], r[:-1]))))


def prepare_period(period, plan):
    from service.research_peer_activity_alignment import evaluate_peer_activity_alignment
    folder, old = OUT / period, PARENT / period
    folder.mkdir(exist_ok=False)
    data, source = read(old / 'account-data.json.gz'), read(old / 'source.json')
    assert source == plan['periods'][period]['sourceIdentity']
    assert digest(data) == source['accountDatasetDigest']
    original = list(records(old / 'opportunities.jsonl.gz'))
    sources = {s['symbol']: s for s in data['securities']}
    sources['__benchmark__'] = data['benchmark']
    bars = {s: {b['date']: b for b in sec['bars']} for s, sec in sources.items()}
    calendar = data['calendar']
    index = {d: i for i, d in enumerate(calendar)}
    def raw_bar(symbol, day):
        bar = bars[symbol].get(day)
        return None if bar is None else {**bar, 'priceBasisId': 'raw-unadjusted',
            'knownSuspended': prior.halted(sources[symbol].get('tradeStatusByDate', {}).get(day))}
    for name in ('source.json', 'parent-selection.json.gz'):
        shutil.copyfile(old / name, folder / name)
    write(folder / 'source-binding.json', {'accountData': ref(old / 'account-data.json.gz'),
        'sourceIdentity': ref(old / 'source.json'), 'parentOpportunities': ref(old / 'opportunities.jsonl.gz'),
        'parentValidation': ref(PARENT / 'validation.json')})
    schedules, counts = {mode: [] for mode in MODES}, Counter()
    with gzip.open(folder / 'price-inputs.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as pw, \
         gzip.open(folder / 'opportunities.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as ow:
        for old_row in original:
            day, anchor = old_row['signalDate'], old_row['symbol']
            at = index[day]
            dates = calendar[at-10:at+1]
            amount = old_row['amount']
            assert dates == amount['windowDates'][19:]
            members = amount['members']
            raw = {s: [raw_bar(s, d) for d in dates] for s in members}
            benchmark = [raw_bar('__benchmark__', d) for d in dates]
            alignment = evaluate_peer_activity_alignment(anchor, members, dates, raw, benchmark, amount)
            signal = {k: old_row[k] for k in ('signalId', 'strategyId', 'symbol', 'signalDate', 'originalRank')}
            common = {'formationDate': dates[0], 'groupSha256': old_row['groupSha256'],
                      'sourceOpportunitySha256': digest(old_row)}
            write_record(pw, {**{k: signal[k] for k in ('signalId', 'symbol', 'signalDate', 'originalRank')},
                **common, 'members': members, 'windowDates': dates, 'rawBySymbol': raw,
                'benchmark': benchmark, 'originalAmountSha256': digest(amount)})
            write_record(ow, {**signal, **common, 'parentAmount': amount,
                             'priceAlignment': alignment, 'alignmentDiagnostics': diagnostics(alignment)})
            counts[alignment['kind']] += 1
            for mode in MODES:
                if alignment[mode] is True:
                    schedules[mode].append(signal)
    assert schedules['currentActivity'] == read(old / 'currentActivity.signals.json')
    for mode in MODES:
        write(folder / (mode + '.signals.json'), schedules[mode])
    value = {'originalTop5': len(original), 'days': len({r['signalDate'] for r in original}),
        'outcomes': dict(counts), 'known': len(original)-counts['unknown'], 'unknown': counts['unknown'],
        'schedules': {m: len(schedules[m]) for m in MODES}, 'controlScheduleExactlyMatchesParent': True}
    write(folder / 'input-counts.json', value)
    write(folder / 'input-completion.json', {'planSha256': sha(OUT / 'plan.json'), 'completedAt': now(),
        'period': period, 'counts': value, 'accountData': ref(old / 'account-data.json.gz'),
        'files': {p.name: ref(p) for p in folder.iterdir() if p.is_file()}})
    emit({'phase': 'period-inputs-complete', 'period': period, **value})
    del data, bars, sources
    gc.collect()


def prepare():
    plan = verify_plan()
    for period in PERIODS:
        prepare_period(period, plan)
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
    compact = module('_alignment_compact', 'run-supplemental-force-context.py').compact
    loader = module('_alignment_fee_loader', 'load-market-context-screen.py')
    rows, artifacts, exact = [], {}, {}
    for period in PERIODS:
        folder = OUT / period
        assert sha(folder / 'input-completion.json') == completion['outputs'][period]['sha256']
        receipt = read(folder / 'input-completion.json')
        for r in receipt['files'].values():
            assert sha(ROOT / r['path']) == r['sha256']
        cache = receipt['accountData']
        assert sha(ROOT / cache['path']) == cache['sha256']
        data, source = read(ROOT / cache['path']), read(folder / 'source.json')
        assert digest(data) == source['accountDatasetDigest'] == plan['periods'][period]['sourceIdentity']['accountDatasetDigest']
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
            if mode == 'currentActivity':
                assert result == read(PARENT / period / 'currentActivity.result.json.gz'), 'Entire control result differs'
                exact[period] = True
            summary = summarize_account(result, checkpoint, policy_id=mode, policy_label=LABELS[mode])
            assert not summary['quality']['integrityIssues']
            for suffix, value in (('.result.json.gz', result), ('.checkpoint.json.gz', checkpoint), ('.summary.json', summary)):
                write(folder / (mode + suffix), value)
            row = compact(mode, LABELS[mode], signals, summary)
            row.update(period=period)
            rows.append(row)
            emit({'phase': 'account-complete', **{k: row[k] for k in ('period', 'id', 'closed', 'accountReturnPct', 'maxDrawdownPct', 'openPositions')}})
            del engine, result, checkpoint, summary
            gc.collect()
        artifacts[period] = {p.name: ref(p) for p in folder.iterdir() if p.is_file()
            and any(tag in p.name for tag in ('.result.', '.summary.', '.checkpoint.'))}
        del data
        gc.collect()
    write(OUT / 'comparison.json', {'schema': 'peer-price-activity-alignment-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newCandidateVariants': 2, 'newAccounts': 6, 'controlReplayExactMatches': exact,
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
