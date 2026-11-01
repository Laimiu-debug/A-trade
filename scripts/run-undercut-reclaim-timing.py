"""Predeclare a prior-strong-stock support reclaim and three entry clocks."""
from argparse import ArgumentParser
from copy import deepcopy
import gc
import gzip
import importlib.util
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('_reclaim_util', ROOT / 'scripts/run-peer-amount-persistence.py')
util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(util)
now, sha, digest, read, records, write, write_record, ref, emit, module = (
    util.now, util.sha, util.digest, util.read, util.records, util.write,
    util.write_record, util.ref, util.emit, util.module)
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'peer-amount-persistence-v1'
OUT = BASE / 'strong-undercut-reclaim-timing-v1'
PERIODS = util.PERIODS
SID = 'strongUndercutReclaimV1'
MODES = ('immediate', 'delay2', 'confirm2')
LABELS = {'immediate': '支撑收回后立即决策', 'delay2': '无条件等待两日', 'confirm2': '等待两日且守住支撑'}
SOURCE_FILES = (*util.SOURCE_FILES, 'scripts/run-undercut-reclaim-timing.py',
    'service/research_undercut_reclaim.py', 'tests/test_research_undercut_reclaim.py')


def verify_plan():
    plan = read(OUT / 'plan.json')
    for key in ('sourcePins', 'frozenInputs'):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    old = read(PARENT / 'plan.json')
    assert sha(PARENT / 'validation.json') == 'cd06d5cb09b045a88c1489399fb9d8c6ed816f314b6e721fdf4cb082558b0851'
    assert read(PARENT / 'validation.json')['passed']
    inputs = [PARENT / 'plan.json', PARENT / 'validation.json',
        BASE / 'peer-price-activity-alignment-v1/delivery.json', BASE / 'market-phase-atlas-v1/atlas.json']
    periods = deepcopy(old['periods'])
    for period in PERIODS:
        inputs.extend(PARENT / period / n for n in ('account-data.json.gz', 'source.json'))
        periods[period]['config']['strategies'][0]['id'] = SID
        periods[period]['config']['allocation']['strategyIds'] = [SID]
    plan = {'schema': 'strong-undercut-reclaim-timing-v1', 'createdAt': now(),
        'sourcePins': {p: sha(ROOT / p) for p in SOURCE_FILES},
        'frozenInputs': {p.relative_to(ROOT).as_posix(): sha(p) for p in inputs},
        'periods': periods, 'strategyId': SID, 'modes': list(MODES),
        'newSetupMechanisms': 1, 'newPolicyVariants': 3, 'newAccounts': 6,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'comparisons': {'primary': 'immediate minus delay2', 'secondary': 'confirm2 minus delay2'},
        'priorGoalTurn': {'classification': 'progress', 'completedBatch': 29,
            'evidence': 'peer-price-activity-alignment-v1/delivery.json',
            'meaning': 'Two new filter policies and six complete independently verified accounts; improvement but no stable positive path.'},
        'novelty': 'A previously strong stock trades below its already-known twenty-session support and closes back above it. Unlike rank crossing, persistent-leader breakout, prior-peak market-event repair or a pullback required to stay above its old breakout level. This is renewed entry after a pullback, not proof of first identifying a future market leader.',
        'rules': {
            'source': 'Full original3141 cached surviving mainboard stocks, daily source calendar; no later persistent-leader/opportunity membership used. Raw unadjusted basis inherited from bound original source.',
            'clock': 'T is setup close. Freeze all ranking and support inputs at T-1. Price ranking needs exactly61 prior slots T-61..T-1, plus currentT for setup.',
            'quality': 'Every prior61 stock/benchmark OHLCV positive finite, valid envelope, no known suspension; missing stays unknown. Same explicit raw basis. Rank population is all complete-price stocks, before amount/strength gates.',
            'rank': 'own60=C(T-1)/C(T-61)-1; relative60=(C(T-1)/C(T-61))/(B(T-1)/B(T-61))-1; full-valid-population ascending midrank=(less+0.5*equal)/N. Prior rank>=0.80 AND own60>0; relative60 need not be positive.',
            'amount': 'Require all prior20 explicit amounts finite and strictly positive; priorAmount20=fsum/20. Amount gate follows price rank construction. It is a liquidity/priority description, not main-force identity.',
            'support': 'S=min(stock low over T-20..T-1), frozen before setup day. CurrentT must pass same single-bar OHLCV/status quality.',
            'setup': 'Strict low(T)<S AND close(T)>S. No candle-color, minimum return, volume-rise, market-phase, persistent-leader or additional price-progress filter.',
            'selection': 'Rank all same-day setups by priorAmount20 descending, symbol ascending; first5 only. Freeze originalRank and formationId. No future confirmation/account/holding filter or replacement. Repeated setup days are retained as distinct opportunities.',
            'immediate': 'Decision T close, one T+1 source-session open attempt through unchanged account engine.',
            'delay2': 'Decision T+2 close and one T+3 open attempt, unconditionally for originalTop5 if decision date within target. Even missing current stock price retains scheduled opportunity; account records eligibility/execution rejection.',
            'confirm2': 'Same T+2 decision clock; both T+1/T+2 bars must be full-quality and both close>=frozenS. Equality holds. Unknown evidence dominates a known false other day. Do not re-rank/backfill or restore failed setups.',
            'cutoff': 'If T+2 is outside target, delay/confirm are censored and unscheduled; retain setup and immediate signal. A decision on last target day remains an account opportunity even if no later open exists. No future extension.',
            'identities': 'Same formationId=signalId SID:T:symbol across policies; signalDate is each policy decision date. Each schedule has only five original signal fields. Formation, decision, fill and exit dates are distinct.',
            'account': old['rules']['account'],
            'exitClock': 'All stop/trailing/noProgress clocks start at actual fills; not at formation. Existing account handles holdings, same-day reentry, slots, cash and one-attempt failures independently per policy.'},
        'evaluation': ['Freeze both-period complete opportunity sets and all six schedules before running accounts.',
            'Primary I-D compares waiting policies; secondary C-D compares added confirmation at same delayed clock. I-C is not solely the effect of timing.',
            'Count all three new policies, including both comparisons, in trial log. No outcome-dependent parameter choice.',
            'Full net account/payoff/tails/drawdown/exposure/open holdings/unfilled signals plus all years, halfyears and sixteen retrospective phases.',
            'Keep unknown and cutoff-censored formations. Formation-date opportunity coverage and decision-date account dispositions remain separate. Do not delete late immediate setups to make a common mature account cohort.'],
        'limitations': [
            'Previously inspected development periods; no pristine holdout or stable-return certification.',
            'Current-survivor3141 SH/SZ mainboard sample; incomplete historical eligibility/rights and simulated opening fills.',
            'Stops execute at later eligible opens; gaps/retry may exceed nominal thresholds. Separate two-year accounts are never spliced.',
            'This daily low/close pattern does not establish intraday fill order, ownership, absorption or a manipulated washout. All entries occur at later simulated opens.',
            'Raw OHLCV and nominal source identities are inherited; no new corporate-action or full historical-universe certification.',
            'Earlier versus later budgets, holdings, fills, capacity and signals interact; full-policy differences are not a causally isolated waiting effect.',
            'Recurring setups and shared market days are dependent. Prior quarter strength and monthly support are one fixed specification, not optimized values.',
            'Historical sentiment and leader names are descriptive attribution only; no hindsight phase labels enter signals.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'phase': 'registered', **ref(OUT / 'plan.json')})


def prepare():
    from service.research_undercut_reclaim import build_undercut_reclaim
    plan = verify_plan()
    for period in PERIODS:
        folder, old = OUT / period, PARENT / period
        folder.mkdir(exist_ok=False)
        data, source = read(old / 'account-data.json.gz'), read(old / 'source.json')
        assert source == plan['periods'][period]['sourceIdentity'] and digest(data) == source['accountDatasetDigest']
        assert len(data['securities']) == 3141
        config = plan['periods'][period]['config']
        projection = {**data, 'priceBasisId': 'raw-unadjusted'}
        emit({'phase': 'setup-build-start', 'period': period})
        evidence = build_undercut_reclaim(projection, start=config['startDate'], end=config['endDate'],
            progress=lambda v: emit({'phase': 'setup-build-progress', 'period': period, **v}))
        assert evidence['strategyId'] == SID and list(evidence['modes']) == list(MODES)
        shutil.copyfile(old / 'source.json', folder / 'source.json')
        write(folder / 'source-binding.json', {'accountData': ref(old / 'account-data.json.gz'),
            'sourceIdentity': ref(old / 'source.json'), 'parentValidation': ref(PARENT / 'validation.json'),
            'projection': 'Complete cached account dataset plus explicit inherited raw-unadjusted priceBasisId; no later opportunity membership.'})
        for name, rows in (('opportunities.jsonl.gz', evidence['opportunities']), ('daily-evidence.jsonl.gz', evidence['dailyRows'])):
            with gzip.open(folder / name, 'xt', encoding='utf-8', compresslevel=1) as stream:
                for row in rows:
                    write_record(stream, row)
        write(folder / 'signal-metadata.json', {k: v for k, v in evidence.items() if k not in ('opportunities', 'dailyRows', 'schedules')})
        for mode in MODES:
            write(folder / (mode + '.signals.json'), evidence['schedules'][mode])
        write(folder / 'input-counts.json', evidence['counts'])
        write(folder / 'input-completion.json', {'planSha256': sha(OUT / 'plan.json'), 'completedAt': now(),
            'period': period, 'counts': evidence['counts'], 'accountData': ref(old / 'account-data.json.gz'),
            'files': {p.name: ref(p) for p in folder.iterdir() if p.is_file()}})
        emit({'phase': 'period-inputs-complete', 'period': period, 'counts': evidence['counts']})
        del data, projection, evidence
        gc.collect()
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': {p: ref(OUT / p / 'input-completion.json') for p in PERIODS}, 'allSchedulesFrozenBeforeAccounts': True})


def run():
    from service.research_structural_exits import ResearchExitAccount
    from service.research_account_outcomes import summarize_account
    from service.research_account_fees import LegacyFeePolicy
    plan = verify_plan()
    completion = read(OUT / 'input-completion.json')
    assert completion['planSha256'] == sha(OUT / 'plan.json') and completion['allSchedulesFrozenBeforeAccounts']
    write(OUT / 'run-start.json', {'startedAt': now(), 'inputCompletion': ref(OUT / 'input-completion.json')})
    compact = module('_reclaim_compact', 'run-supplemental-force-context.py').compact
    loader = module('_reclaim_fee_loader', 'load-market-context-screen.py')
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
    write(OUT / 'comparison.json', {'schema': 'strong-undercut-reclaim-timing-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'newSetupMechanisms': 1, 'newPolicyVariants': 3, 'newAccounts': 6,
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
