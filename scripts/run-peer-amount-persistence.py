"""Preregister one fixed-peer amount-share persistence filter, then replay accounts.

Research only. Raw daily amount describes activity, not investor identity or net
inflow. Both historical periods are development data. No parameter search.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gc
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
OUT = BASE / 'peer-amount-persistence-v1'
SID = 'fixedPeerAmountPersistenceV1'
PERIODS = ('2022-2023', '2024-2025')
MODES = ('currentActivity', 'persistent8')
LABELS = {'currentActivity': '当日同伴成交额份额高于基准', 'persistent8': '当日活跃且最近10日中至少8日保持'}
OLD = BASE / 'signal-leader-peer-expansion-v1/attempts/attempt-0001'
NEW = BASE / 'signal-supplemental-persistent-leader-v1/attempts/attempt-0001'
SOURCE_FILES = ('scripts/run-peer-amount-persistence.py', 'service/research_peer_amount_share.py',
    'tests/test_research_peer_amount_share.py', 'service/signal_leader_peer_expansion.py',
    'service/signal_persistent_leader.py', 'scripts/load-market-context-screen.py',
    'scripts/load-supplemental-account-source.py', 'scripts/run-supplemental-force-context.py',
    'service/research_structural_exits.py', 'service/research_fixed_signal_account.py',
    'service/research_account_fees.py', 'service/research_account_outcomes.py',
    'service/signal_target_study_fee_profile.py', 'service/research_fee_profile.py',
    'service/portfolio_engine.py', 'service/portfolio_config.py')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def write(path, value):
    path = Path(path)
    with (gzip.open(path, 'xt', encoding='utf-8', compresslevel=1) if str(path).endswith('.gz') else path.open('x', encoding='utf-8')) as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def write_record(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify_plan():
    plan = read(OUT / 'plan.json')
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for name, expected in mapping.items():
            assert sha(ROOT / name) == expected, name
    return plan


def register():
    parent_path = BASE / 'relative-repair-cost-stress-v1/plan.json'
    assert sha(parent_path) == '47b265b50ae7f3e1280bd66af8c7869e6343c63dbf4a84a7ffa18454448570c3'
    parent = read(parent_path)
    inputs = [parent_path, OLD / 'candidates.jsonl.gz', OLD / 'peer-groups.jsonl.gz',
        OLD / 'selection.jsonl.gz', BASE / 'signal-leader-peer-expansion-v1/independent-result-verification.json',
        NEW / 'candidates.jsonl.gz', NEW / 'predicates.jsonl.gz',
        BASE / 'signal-supplemental-persistent-leader-v1/independent-result-verification.json',
        BASE / 'signal-supplemental-persistent-leader-v1/protocol.json',
        BASE / 'market-phase-atlas-v1/atlas.json']
    assert sha(OLD / 'peer-groups.jsonl.gz') == '4b8d1fd46a900ad71a68b84b52912e0512a74e0c601b1b4325dead9e7bcc44cf'
    for path in (inputs[4], inputs[7]):
        audit = read(path)
        assert audit['passed'] and audit['failureCount'] == 0
    periods = {}
    for period in PERIODS:
        spec = parent['periods'][period]
        config = deepcopy(spec['config'])
        config['strategies'][0]['id'] = SID
        config['allocation']['strategyIds'] = [SID]
        periods[period] = {'config': config, 'sourceIdentity': spec['sourceIdentity']}
    plan = {'schema': 'peer-amount-persistence-v1', 'createdAt': now(),
        'sourcePins': {p: sha(ROOT / p) for p in SOURCE_FILES},
        'frozenInputs': {p.relative_to(ROOT).as_posix(): sha(p) for p in inputs},
        'periods': periods, 'modes': list(MODES), 'strategyId': SID,
        'newCandidateVariants': 1, 'newAccounts': 4, 'candidateAccounts': 2,
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False,
        'rules': {
            'parent': 'Original persistent-price-leader trigger at T; original five strength predicates at F=T-10, kind signaled/notTriggered; F breakout not required. Filter every current candidate by F before reranking, Top5 by original meanStrength20/relativeReturn60 descending then symbol ascending. First10 target sessions formation unavailable.',
            'group': 'At F freeze original 20 highest benchmark-excess-log-return Pearson peers >=0.30 from 61 exact slots F-60..F and original F baseEligibility. Exclude anchor. Missing/incomplete groups unknown, no future replacements.',
            'window': 'Exactly F-19..T (30 slots); explicit raw amount, including valid zero, missing never zero.',
            'balancedUniverse': 'U(T): original3141 securities with finite nonnegative explicit amount in all30 slots. Same U on every slot and across groups at T. Determined as of T, not an F-frozen universe. Record excluded and zero counts. Every frozen peer must belong to U. Every daily denominator >0.',
            'dailyShare': 'fsum(peer20 amount)/fsum(U amount); anchor excluded numerator, allowed denominator.',
            'baseline': 'B=arithmetic mean of daily shares over F-19..F (20 slots).',
            'currentActivity': 'S_T strictly greater than B; same complete30 eligibility as candidate.',
            'persistent8': 'currentActivity AND at least8 of F+1..T ten daily shares strictly greater than B. Equal does not pass; no epsilon.',
            'execution': 'Buy anchor, not peers. Preserve originalTop5 rank; no backfill; same signalId across policies. Full fresh accounts, close-time signal/one next independent open attempt, no add.',
            'account': 'Each period500000,20% budget,5 slots,5% initial stop/10% close trailing/exactday5 noProgress,10bps eachside,inherited dated old/Legacy new fee assumptions,no profit cap,no holding expiry.'},
        'evaluation': ['Freeze complete both-period input schedules before account outcomes.',
            'Continuous account return/drawdown, net expectancy, all win/loss distributions and tails, year/halfyear and retrospective phase attribution, exposure, open/unfilled positions, holding duration and profit concentration.',
            'Retain every original temporalTop5 opportunity including unknown/rejected; record current share/B/count and price-expansion/parent features. This estimates an entire policy filter, not an independently causal amount factor.',
            'No conditional rank diagnostic, no threshold search or immediate new variant.'],
        'limitations': ['Previously inspected development periods; no pristine holdout or stable-return certification.',
            'Current-survivor3141 SH/SZ mainboard sample; incomplete historical eligibility/rights and simulated opening fills.',
            'Amount-share means activity, not turnover ratio, money flow, investor identity or control. Statistical peers are not actual theme or industry membership.',
            'U is an as-of-T complete-case fixed sample over30 sessions, not whole-market amount or F-time membership.',
            'Repeated anchors and overlapping groups create dependence; filtered schedules change cash and later fills.',
            'Stops execute at later eligible opens; gaps/retry may exceed nominal thresholds. Separate two-year accounts are never spliced.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    emit({'phase': 'registered', **ref(OUT / 'plan.json')})


def candidate_top5(candidates, parents, calendar, target_dates):
    from service.signal_persistent_leader import THRESHOLDS, rank_persistent_leader_signals
    index = {d: i for i, d in enumerate(calendar)}
    target_set = set(target_dates)
    by_day, excluded = defaultdict(list), Counter()
    for row in candidates:
        day, symbol = row['signalDate'], row['symbol']
        formation = calendar[index[day]-10]
        if formation not in target_set:
            excluded['formationUnavailable'] += 1
            continue
        past = parents[formation + ':' + symbol]
        five = {name: past['predicates'][name]['passed'] for name, _, _ in THRESHOLDS}
        strong = past['kind'] in ('signaled', 'notTriggered') and all(v is True for v in five.values())
        if not strong:
            excluded['formationRejected'] += 1
            continue
        item = deepcopy(row)
        item.update(formationDate=formation, eligible=True, formationParent={
            'date': formation, 'kind': past['kind'], 'reason': past['reason'],
            'fiveThresholdsPass': five, 'strong': True, 'outcomeSha256': digest(past)})
        by_day[day].append(item)
    ranked = [item for d in sorted(by_day) for item in rank_persistent_leader_signals(by_day[d])]
    return ranked, dict(excluded)


def extract_formation(candidates, calendar, symbols, target_dates, destination):
    index = {d: i for i, d in enumerate(calendar)}
    keys = {calendar[index[r['signalDate']]-10] + ':' + r['symbol'] for r in candidates
            if calendar[index[r['signalDate']]-10] in set(target_dates)}
    wanted_dates = {k[:10] for k in keys}
    eligibility, parents, last = {}, {}, time.monotonic()
    expected = ((day, symbol) for day in target_dates for symbol in symbols)
    count = 0
    for row in records(NEW / 'predicates.jsonl.gz'):
        day, symbol = next(expected)
        assert row['date'] == day and row['symbol'] == symbol
        count += 1
        if day in wanted_dates:
            eligibility.setdefault(day, {})[symbol] = row['features'].get('baseEligibility')
        if day + ':' + symbol in keys:
            parents[day + ':' + symbol] = row
        if time.monotonic() - last > 25:
            emit({'phase': 'formation-predicate-scan', 'rows': count, 'expected': len(target_dates)*len(symbols)})
            last = time.monotonic()
    assert next(expected, None) is None and set(parents) == keys
    assert all(sorted(v) == symbols for v in eligibility.values())
    value = {'dates': sorted(wanted_dates), 'eligibilityByDate': eligibility,
        'parentsByKey': parents, 'candidateSourceBinding': ref(NEW / 'candidates.jsonl.gz'),
        'predicateSourceBinding': ref(NEW / 'predicates.jsonl.gz'), 'completeRowsScanned': count}
    write(destination / 'formation-panel.json.gz', value)
    return value


def halted(status):
    return status is False or type(status) is int and status == 0 or status == '0'


def prepare_period(period, plan):
    from service.research_peer_amount_share import build_balanced_market, evaluate_peer_amount_share
    from service.signal_leader_peer_expansion import form_peer_group, evaluate_peer_expansion
    destination = OUT / period
    destination.mkdir(exist_ok=False)
    loader = module('_peer_source_loader', 'load-market-context-screen.py')
    raw, data, identity, _fee = loader.load_account_source(period, progress=emit)
    assert identity == plan['periods'][period]['sourceIdentity']
    symbols = sorted(s['symbol'] for s in raw['securities'])
    assert len(symbols) == 3141
    calendar = raw['calendar']
    index = {d: i for i, d in enumerate(calendar)}
    config = plan['periods'][period]['config']
    target_dates = [d for d in calendar if config['startDate'] <= d <= config['endDate']]
    if period == '2022-2023':
        ranked = list(records(OLD / 'candidates.jsonl.gz'))
        groups = {(r['date'], r['symbol']): r for r in records(OLD / 'peer-groups.jsonl.gz')}
        exclusions = {'meaning': 'Full temporal selection inherited from pinned original independent audit.'}
        formation = None
    else:
        current = list(records(NEW / 'candidates.jsonl.gz'))
        formation = extract_formation(current, calendar, symbols, target_dates, destination)
        ranked, exclusions = candidate_top5(current, formation['parentsByKey'], calendar, target_dates)
        groups = {}
    top5 = [r for r in ranked if r['selectedTop5']]
    assert len({(r['signalDate'], r['symbol']) for r in top5}) == len(top5)
    if period == '2022-2023':
        assert len(top5) == 148 and set(groups) == {(r['signalDate'], r['symbol']) for r in top5}
    write(destination / 'parent-selection.json.gz', {'rankedCandidates': ranked,
        'exclusions': exclusions, 'top5Count': len(top5), 'targetDates': target_dates,
        'allCurrentCandidatesUsedBeforeFormationFilter': True})
    all_sources = {s['symbol']: s for s in raw['securities']}
    all_sources['__benchmark__'] = raw['benchmark']
    bars = {s: {b['date']: b for b in sec['bars']} for s, sec in all_sources.items()}
    def raw_bar(symbol, day):
        bar = bars[symbol].get(day)
        return None if bar is None else {**bar, 'priceBasisId': 'raw-unadjusted',
            'knownSuspended': halted(all_sources[symbol].get('tradeStatusByDate', {}).get(day))}
    needed_dates = sorted({d for item in top5 for d in calendar[index[item['signalDate']]-29:index[item['signalDate']]+1]})
    amounts = {s: [bars[s].get(d, {}).get('amount') for d in needed_dates] for s in symbols}
    write(destination / 'amount-source.json.gz', {'sourceCalendar': calendar, 'dates': needed_dates,
        'symbols': symbols, 'amountsBySymbol': amounts, 'sourceIdentity': identity,
        'missingPolicy': 'Missing bar or missing amount = null; no zero imputation.'})
    amount_index = {d: i for i, d in enumerate(needed_dates)}
    data_sources = {s['symbol']: s for s in data['securities']}
    data_sources['__benchmark__'] = data['benchmark']
    price_dates = sorted({d for item in top5 for d in calendar[index[item['signalDate']]-70:index[item['signalDate']]+1]})
    field_checks = []
    for s in sorted(all_sources):
        account_bars = {b['date']: b for b in data_sources[s]['bars']}
        original, cached = [], []
        for d in price_dates:
            a, b = bars[s].get(d), account_bars.get(d)
            original.append(None if a is None else [a.get(k) for k in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount')])
            cached.append(None if b is None else [b.get(k) for k in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount')])
            assert halted(all_sources[s].get('tradeStatusByDate', {}).get(d)) == halted(data_sources[s].get('tradeStatusByDate', {}).get(d))
        assert original == cached, s
        field_checks.append({'symbol': s, 'rawAndAccountEqual': True, 'sha256': digest(original)})
    write(destination / 'group-source-binding.json', {'dates': price_dates, 'fields': ['date','open','high','low','close','volume','amount'],
        'fieldComparisons': field_checks, 'tradeStatusCompared': True, 'sourceIdentity': identity,
        'meaning': 'Runner compares all original/account OHLCV amount and suspension flags on every group window; auditor uses this pinned complete account cache, not another source reload.'})
    write(destination / 'source.json', identity)
    write(destination / 'account-data.json.gz', data)
    emit({'phase': 'causal-source-ready', 'period': period, 'temporalTop5': len(top5), 'signalDays': len({r['signalDate'] for r in top5})})
    schedules = {mode: [] for mode in MODES}
    stats = Counter()
    by_day = defaultdict(list)
    for item in top5:
        by_day[item['signalDate']].append(item)
    with gzip.open(destination / 'opportunities.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as ow, \
         gzip.open(destination / 'peer-groups.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as gw, \
         gzip.open(destination / 'amount-windows.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as mw:
        last = time.monotonic()
        for ordinal, (day, items) in enumerate(sorted(by_day.items()), 1):
            at = index[day]
            window = calendar[at-29:at+1]
            formation_day = calendar[at-10]
            window_amounts = {s: [amounts[s][amount_index[d]] for d in window] for s in symbols}
            market = build_balanced_market(window, window_amounts)
            write_record(mw, {'date': day, 'market': market})
            formation_dates = calendar[at-70:at-9]
            formation_raw = {s: [raw_bar(s, d) for d in formation_dates] for s in symbols} if formation is not None else None
            expansion_dates = calendar[at-10:at+1]
            for item in items:
                anchor = item['symbol']
                assert item['formationDate'] == formation_day
                if formation is not None:
                    group = form_peer_group(anchor, formation_day, formation_dates, formation_raw,
                        [raw_bar('__benchmark__', d) for d in formation_dates], formation['eligibilityByDate'][formation_day])
                    group_record = {'date': day, 'symbol': anchor, 'rank': item['rank'],
                        'formationDate': formation_day, 'groupSha256': digest(group), 'group': group}
                else:
                    group_record = groups[(day, anchor)]
                    group = group_record['group']
                    assert group_record['rank'] == item['rank'] and digest(group) == group_record['groupSha256']
                write_record(gw, group_record)
                assert (group['kind'] == 'formed') == (len(group['members']) == 20)
                amount = evaluate_peer_amount_share(anchor, group['members'], window, window_amounts, market)
                expansion = evaluate_peer_expansion(group, expansion_dates,
                    {s: [raw_bar(s, d) for d in expansion_dates] for s in group['members']},
                    [raw_bar('__benchmark__', d) for d in expansion_dates])
                signal = {'signalId': f'{SID}:{day}:{anchor}', 'strategyId': SID, 'symbol': anchor,
                    'signalDate': day, 'originalRank': item['rank']}
                opportunity = {**signal, 'formationDate': formation_day, 'groupSha256': group_record['groupSha256'],
                    'parent': item, 'amount': amount, 'priceExpansion': expansion}
                write_record(ow, opportunity)
                stats[amount['kind']] += 1
                for mode, field in (('currentActivity', 'control'), ('persistent8', 'candidate')):
                    if amount[field] is True:
                        schedules[mode].append(signal)
            if time.monotonic() - last > 25:
                emit({'phase': 'peer-amount-inputs', 'period': period, 'days': ordinal, 'totalDays': len(by_day)})
                last = time.monotonic()
    for mode, schedule in schedules.items():
        write(destination / (mode + '.signals.json'), schedule)
    assert set(s['signalId'] for s in schedules['persistent8']).issubset(s['signalId'] for s in schedules['currentActivity'])
    counts = {'originalTop5': len(top5), 'days': len(by_day), 'outcomes': dict(stats),
        'schedules': {mode: len(s) for mode, s in schedules.items()}}
    write(destination / 'input-counts.json', counts)
    write(destination / 'input-completion.json', {'planSha256': sha(OUT / 'plan.json'), 'completedAt': now(),
        'period': period, 'counts': counts, 'files': {p.name: ref(p) for p in destination.iterdir() if p.is_file()}})
    emit({'phase': 'period-inputs-complete', 'period': period, **counts})
    del raw, data, bars, all_sources, data_sources, amounts, formation
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
    compact = module('_peer_compact', 'run-supplemental-force-context.py').compact
    loader = module('_peer_fee_loader', 'load-market-context-screen.py')
    rows, artifacts = [], {}
    for period in PERIODS:
        folder = OUT / period
        assert sha(folder / 'input-completion.json') == completion['outputs'][period]['sha256']
        receipt = read(folder / 'input-completion.json')
        for name, r in receipt['files'].items():
            assert sha(folder / name) == r['sha256'], name
        data, source = read(folder / 'account-data.json.gz'), read(folder / 'source.json')
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
            emit({'phase': 'account-complete', **{k: row[k] for k in ('period','id','closed','accountReturnPct','maxDrawdownPct','openPositions')}})
            del engine, result, checkpoint, summary
            gc.collect()
        artifacts[period] = {p.name: ref(p) for p in folder.iterdir()
            if p.is_file() and any(tag in p.name for tag in ('.result.', '.summary.', '.checkpoint.'))}
        del data
        gc.collect()
    write(OUT / 'comparison.json', {'schema': 'peer-amount-persistence-comparison-v1', 'completedAt': now(),
        'planSha256': sha(OUT / 'plan.json'), 'rows': rows, 'artifacts': artifacts,
        'candidateVariants': 1, 'newAccounts': 4, 'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False})
    emit({'phase': 'all-accounts-complete', **ref(OUT / 'comparison.json')})


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'prepare', 'run'))
    args = parser.parse_args()
    try:
        globals()[args.mode]()
    except Exception as error:
        if OUT.exists():
            attempts = OUT / 'failures'
            attempts.mkdir(exist_ok=True)
            write(attempts / (str(time.time_ns()) + '.json'), {'at': now(), 'mode': args.mode,
                'type': type(error).__name__, 'message': str(error)})
        raise
