"""Independent full-gap formations, frozen schedules and account audit.

The production gap detector and execution runner are never imported. Every
cross-sectional rank and temporal decision is reconstructed from frozen cached
observations. Existing independently audited cash/fee/risk helpers are reused
under immutable hashes; no account is executed by this program.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gc
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/full-gap-retention-v1'
PARENT_AUDITOR = 'scripts/verify-all-opportunity-feedback.py'
PARENT_AUDITOR_SHA = 'd5f1d1f611e38c0460a6ce1c03a8229b44f34fd310a6c59d9fb974335bc26857'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
EXITS = ('trail10', 'volatilityTrail')
POLICIES = ('control', 'retained')
SID = 'fullGapRetentionV1'
BASIS = {
    'price': 'raw-unadjusted; positive finite OHLCV, envelope and no known suspension; exact source slots',
    'rank': 'all price-complete cohort members; RS60 versus sh000300; ascending tied midrank (i+j)/(2N)',
    'history': 'at least120 valid OHLCV observations through F-1; not listing age or120 consecutive sessions',
    'st': 'exclude only explicit stByDate[F-1] is True; unknown historical status retained',
    'activity': 'F volume > mean prior20 share volumes; each prior amount positive; mean amount>=50000000 CNY',
    'gap': 'raw lowF > raw highF-1; fixed boundary highF-1; no additional price-high breakout',
    'lock': 'every qualified setup, including non-Top5, owns F..F+2; locked setups do not extend or replace',
    'selection': 'unlocked qualified setups sort prior20 mean amount descending then symbol; original Top5; no refill',
    'clock': 'both policies decide at F+2; F+1 and F+2 must have complete OHLCV/status; no earlier signal',
    'retention': 'both subsequent lows strictly greater than the frozen boundary; equality fails',
    'reference': 'F..F+2 explicit preclose mismatch uses portfolio_engine._observe_actions: abs(reference-previousClose)>max(.005,previousClose*1e-7); unknown for both policies; no reconstruction',
    'limitations': 'fixed local surviving cohorts, unknown historic eligibility and undeclared corporate actions; development history, no certification',
}


def load_parent():
    path = ROOT / PARENT_AUDITOR
    with path.open('rb') as stream:
        observed = hashlib.file_digest(stream, 'sha256').hexdigest()
    if observed != PARENT_AUDITOR_SHA:
        raise AssertionError('Frozen independent ledger helper changed')
    spec = importlib.util.spec_from_file_location('_gap_independent_parent', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P = load_parent()
require, finite, same, sha, digest, read, records, ref, bound = (
    getattr(P, k) for k in ('require', 'finite', 'same', 'sha', 'digest', 'read', 'records', 'ref', 'bound'))
DEPENDENCIES = {PARENT_AUDITOR: PARENT_AUDITOR_SHA, **P.DEPENDENCIES}


def same(actual, expected, label='value'):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + ': fields differ')
        for key in expected: same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': rows differ')
        for n, value in enumerate(expected): same(actual[n], value, f'{label}[{n}]')
    elif type(expected) is int:
        require(type(actual) is int and actual == expected, label + ': integer differs')
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-11), label + ': arithmetic differs')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': literal differs')


def positive(value):
    return finite(value) and value > 0


def calendar_reference(days):
    require(isinstance(days, list) and bool(days) and days == sorted(set(days)), 'Unique ordered calendar')
    require(all(type(d) is str and date.fromisoformat(d).isoformat() == d for d in days), 'Canonical calendar dates')


def midrank_reference(values):
    """All valid price observations enter N before liquidity or eligibility."""
    require(all(type(s) is str and finite(v) for s, v in values.items()), 'Finite rank population')
    ordered = sorted(values, key=lambda s: (values[s], s))
    n, start, answer = len(ordered), 0, {}
    while start < n:
        stop = start + 1
        while stop < n and values[ordered[stop]] == values[ordered[start]]:
            stop += 1
        for symbol in ordered[start:stop]:
            answer[symbol] = (start + stop) / (2 * n)
        start = stop
    return answer


def bar_reason(bar, day, status=None):
    if bar is None:
        return 'missingAlignedRawBar'
    if not isinstance(bar, dict) or bar.get('date') != day:
        return 'rawHistoryDateMismatch'
    if bar.get('priceBasisId', 'raw-unadjusted') != 'raw-unadjusted':
        return 'rawPriceBasisUnknown'
    if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
        return 'invalidKnownSuspensionFlag'
    if (bar.get('knownSuspended') is True or status is False
            or type(status) in (int, float) and status == 0 or status == '0'):
        return 'knownSuspension'
    if not all(positive(bar.get(k)) for k in ('open', 'high', 'low', 'close', 'volume')):
        return 'nonpositiveOrUnknownRawOHLCV'
    if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
        return 'invalidRawOHLCEnvelope'
    return None


def source_reference(dataset, start, end):
    require(start <= end and dataset.get('priceBasisId') == 'raw-unadjusted', 'Ordered dates and explicit raw basis')
    calendar_reference(dataset['calendar'])
    calendar = [d for d in dataset['calendar'] if d <= end]
    require(calendar and any(start <= d <= end for d in calendar), 'Nonempty target source calendar')
    securities = dataset['securities']
    require(securities and len({s['symbol'] for s in securities}) == len(securities), 'Unique full cohort')
    by_symbol = {s['symbol']: s for s in securities}
    require(all(type(s) is str and s for s in by_symbol), 'Nonempty source symbols')
    require(not dataset.get('corporateActions') and all(not s.get('corporateActions') for s in securities),
            'No declared corporate actions in inherited source scope')
    sources = {**by_symbol, '__benchmark__': dataset['benchmark']}
    prepared, permitted = {}, set(calendar)
    for symbol, source in sources.items():
        dates = [b['date'] for b in source['bars']]
        require(dates == sorted(set(dates)), 'Unique ascending bar dates')
        mapping = {b['date']: b for b in source['bars'] if b['date'] <= end}
        require(set(mapping) <= permitted, 'No off-calendar historical bar')
        statuses = source.get('tradeStatusByDate', {})
        require(isinstance(statuses, dict), 'Historical status mapping')
        require(isinstance(source.get('stByDate', {}), dict), 'ST mapping')
        bars = [mapping.get(day) for day in calendar]
        reasons = [bar_reason(bar, day, statuses.get(day)) for day, bar in zip(calendar, bars)]
        bad, good = [0], [0]
        for reason in reasons:
            bad.append(bad[-1] + (reason is not None))
            good.append(good[-1] + (reason is None))
        prepared[symbol] = dict(source=source, bars=bars, reasons=reasons, bad=bad, good=good)
    return dict(calendar=calendar, targetDates=[d for d in calendar if start <= d <= end],
                symbols=sorted(by_symbol), sources=prepared)


def window_reason(source, at, slots):
    first = at - slots + 1
    if first < 0:
        return 'insufficientCalendarHistory'
    if source['bad'][at + 1] == source['bad'][first]:
        return None
    return next(r for r in source['reasons'][first:at + 1] if r is not None)


def relative_reference(stock, benchmark, at):
    for prefix, source in (('stock:', stock), ('benchmark:', benchmark)):
        issue = window_reason(source, at, 61)
        if issue is not None:
            return None, None, prefix + issue
    ratio = stock['bars'][at]['close'] / stock['bars'][at - 60]['close']
    benchmark_ratio = benchmark['bars'][at]['close'] / benchmark['bars'][at - 60]['close']
    if not positive(ratio) or not positive(benchmark_ratio):
        return None, None, 'nonfiniteOrUnderflowIntermediateRatio'
    relative = ratio / benchmark_ratio
    if not positive(relative) or not finite(relative - 1):
        return None, None, 'nonfiniteOrUnderflowRelativeRatio'
    return ratio - 1, relative - 1, None


def mean_positive(values):
    try:
        value = math.fsum(values) / len(values)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return value if positive(value) else None


def safe(value):
    return value if finite(value) else None


def confirmation_reference(source, calendar, index, boundary, target):
    visible = [i for i in (index + 1, index + 2) if i < len(calendar) and calendar[i] in target]
    bars, dates = source['bars'], [calendar[i] for i in visible]
    lows = [safe(bars[i].get('low')) if bars[i] else None for i in visible]
    bar_reasons = [{'date': calendar[i], 'reason': source['reasons'][i]} for i in visible]
    references = source['source'].get('precloseByDate', {})
    require(isinstance(references, dict), 'Explicit preclose mapping')
    checks = []
    for i in [index, *visible]:
        day = calendar[i]
        previous = bars[i - 1] if i else None
        close = previous.get('close') if previous else None
        explicit, value = day in references, references.get(day)
        threshold = max(.005, close * 1e-7) if positive(close) else None
        if not explicit: status = 'notDeclared'
        elif not positive(value): status = 'invalidReference'
        elif i == 0 or source['reasons'][i - 1] is not None: status = 'unknownPreviousBar'
        elif abs(value - close) > threshold: status = 'changedReference'
        else: status = 'unchangedReference'
        checks.append(dict(date=day, status=status, explicit=explicit, previousClose=safe(close),
                           preclose=safe(value), threshold=threshold))
    if len(visible) < 2:
        status, retained, reasons = 'censored', None, ['decisionOutsideTarget']
    else:
        reasons = [r['date'] + ':' + r['reason'] for r in bar_reasons if r['reason']]
        reasons += [r['date'] + ':' + r['status'] for r in checks if r['status'] not in ('notDeclared', 'unchangedReference')]
        status = 'unknown' if reasons else 'known'
        retained = all(low > boundary for low in lows) if not reasons else None
    return dict(status=status, dates=dates, lows=lows, barReasons=bar_reasons, referenceChecks=checks,
                reasons=reasons, retained=retained)


def qualification_reference(indexed, index):
    """Build the full population first, then independent local qualification."""
    sources, symbols, days = indexed['sources'], indexed['symbols'], indexed['calendar']
    own, relative, rank_reasons = {}, {}, {}
    for symbol in symbols:
        o, r, issue = relative_reference(sources[symbol], sources['__benchmark__'], index - 1)
        if issue is None and not finite(o):
            o, r, issue = None, None, 'nonfiniteOwnReturn'
        own[symbol], relative[symbol], rank_reasons[symbol] = o, r, issue
    ranks = midrank_reference({s: v for s, v in relative.items() if v is not None})
    population = [{'symbol': s, 'value': relative[s], 'percentile': ranks[s]} for s in symbols if s in ranks]
    rows = []
    for symbol in symbols:
        source = sources[symbol]
        bar = source['bars'][index]
        previous = source['bars'][index - 1] if index else None
        st = source['source'].get('stByDate', {}).get(days[index - 1]) if index else None
        activity_reason = window_reason(source, index - 1, 20)
        window = source['bars'][index - 20:index] if index >= 20 else []
        volume = mean_positive([b['volume'] for b in window]) if activity_reason is None else None
        amount, amount_reason = None, None
        if len(window) != 20: amount_reason = 'insufficientCalendarHistory'
        elif any(b is None or not positive(b.get('amount')) for b in window):
            amount_reason = 'nonpositiveOrUnknownPriorAmount'
        else:
            amount = mean_positive([b['amount'] for b in window])
            if amount is None: amount_reason = 'nonfinitePriorMeanAmount'
        issues = []
        if rank_reasons[symbol]: issues.append('rank:' + rank_reasons[symbol])
        if activity_reason: issues.append('volume:' + activity_reason)
        elif volume is None: issues.append('volume:nonfinitePriorMeanVolume')
        if amount_reason: issues.append('amount:' + amount_reason)
        if source['reasons'][index]: issues.append('formation:' + source['reasons'][index])
        failed = []
        if source['good'][index] < 120: failed.append('insufficientObservedHistory120')
        if st is True: failed.append('knownPriorST')
        if own[symbol] is not None and own[symbol] <= 0: failed.append('nonpositivePriorOwn60')
        if ranks.get(symbol) is not None and ranks[symbol] < .8: failed.append('priorRankBelowTop20Percent')
        if amount is not None and amount < 50_000_000: failed.append('priorMeanAmountBelowMinimum')
        eligible = not issues and not failed
        gap, active = (bar['low'] > previous['high'], bar['volume'] > volume) if eligible else (None, None)
        if eligible and not gap: failed.append('notFullUpwardGap')
        if eligible and not active: failed.append('formationVolumeNotAbovePriorMean')
        rows.append(dict(symbol=symbol, priorValidObservations=source['good'][index],
            knownStPrior=st if type(st) is bool else None, priorOwn60=own[symbol], priorRelative60=relative[symbol],
            priorRank60=ranks.get(symbol), priorMeanAmount20=amount, priorMeanVolume20=volume,
            formationLow=safe(bar.get('low')) if bar else None,
            formationVolume=safe(bar.get('volume')) if bar else None,
            gapBoundary=safe(previous.get('high')) if previous else None,
            qualityReasons=issues, failureReasons=failed, eligibilityKnown=not issues, eligible=eligible,
            rawSetup=bool(eligible and gap and active), selectionStatus='notSetup', originalRank=None, lockOwner=None))
    return rows, dict(count=len(population), sha256=digest(population)), dict(sorted(Counter(v for v in rank_reasons.values() if v).items()))


def reference_build(dataset, *, start, end, progress=None, on_evidence=None):
    indexed = source_reference(dataset, start, end)
    calendar, targets = indexed['calendar'], indexed['targetDates']
    target_set = set(targets)
    owners, daily, formations, opportunities = {}, [], [], []
    schedules = {p: [] for p in POLICIES}
    all_issues = Counter()
    confirmations = Counter(dict.fromkeys(('known', 'unknown', 'censored'), 0))
    outcomes = Counter(dict.fromkeys(('retained', 'filled', 'unknown', 'censored'), 0))
    for index, day in enumerate(calendar):
        if day not in target_set: continue
        rows, population, rank_reasons = qualification_reference(indexed, index)
        for row in rows: all_issues.update(row['qualityReasons'])
        today, unlocked = [], []
        for row in rows:
            if not row['rawSetup']: continue
            symbol = row['symbol']
            identity = f'{SID}:{day}:{symbol}'
            owner = owners.get(symbol)
            locked = owner is not None and index <= owner['lockUntilIndex']
            row['lockOwner'] = owner['formationId'] if locked else None
            formation = dict(formationId=identity, symbol=symbol, formationDate=day, priorDate=calendar[index - 1],
                **deepcopy({k: v for k, v in row.items() if k != 'symbol'}),
                lockUntilDate=calendar[min(index + 2, len(calendar) - 1)], lockUntilIndex=index + 2, lockApplied=not locked)
            if locked:
                formation.update(selectionStatus='locked', lockUntilDate=owner['lockUntilDate'], lockUntilIndex=owner['lockUntilIndex'])
                row['selectionStatus'] = 'locked'
            else:
                owners[symbol] = formation
                unlocked.append((formation, row))
            today.append(formation)
        unlocked.sort(key=lambda pair: (-pair[0]['priorMeanAmount20'], pair[0]['symbol']))
        for rank, (formation, row) in enumerate(unlocked, 1):
            status = 'selected' if rank <= 5 else 'rankExcluded'
            row.update(originalRank=rank, selectionStatus=status)
            formation.update(originalRank=rank, selectionStatus=status)
            if rank > 5: continue
            symbol, identity = formation['symbol'], formation['formationId']
            confirm = confirmation_reference(indexed['sources'][symbol], calendar, index, formation['gapBoundary'], target_set)
            decision = calendar[index + 2] if index + 2 < len(calendar) and calendar[index + 2] in target_set else None
            signals = {p: None for p in POLICIES}
            if confirm['status'] == 'known':
                for policy in POLICIES:
                    if policy == 'retained' and not confirm['retained']: continue
                    signals[policy] = dict(signalId=identity, strategyId=SID, symbol=symbol, signalDate=decision, originalRank=rank)
                    schedules[policy].append(deepcopy(signals[policy]))
            opportunities.append(dict(**deepcopy(formation), signalId=identity, decisionDate=decision,
                confirmation=confirm, control=True if confirm['status'] == 'known' else None,
                retained=confirm['retained'], signals=signals))
            confirmations[confirm['status']] += 1
            outcome = ('retained' if confirm['retained'] else 'filled') if confirm['status'] == 'known' else confirm['status']
            outcomes[outcome] += 1
        formations.extend(today)
        daily_row = dict(formationDate=day, priorDate=calendar[index - 1] if index else None,
            priorWindowDates61=calendar[max(0, index - 61):index], activityWindowDates20=calendar[max(0, index - 20):index],
            rankPopulation=population, rankReasonCounts=rank_reasons, fixedPopulation=len(indexed['symbols']),
            qualityUnknownCount=sum(not r['eligibilityKnown'] for r in rows), eligibleCount=sum(r['eligible'] for r in rows),
            rawSetupCount=len(today), lockedSetupCount=sum(f['selectionStatus'] == 'locked' for f in today),
            rankedSetupCount=len(unlocked), selectedCount=min(5, len(unlocked)),
            candidates=[dict(formationId=f['formationId'], symbol=f['symbol'], originalRank=f['originalRank'],
                priorMeanAmount20=f['priorMeanAmount20'], selected=f['selectionStatus'] == 'selected') for f, _ in unlocked],
            symbolEvidenceDigest=digest(rows))
        daily.append(daily_row)
        if on_evidence:
            on_evidence(dict(formationDate=day, priorDate=daily_row['priorDate'], rankPopulation=population,
                             rows=rows, rowsDigest=daily_row['symbolEvidenceDigest']))
        if progress and (len(daily) % 20 == 0 or day == targets[-1]):
            progress(dict(checkedDays=len(daily), targetDays=len(targets)))
    return dict(version='full-gap-retention-v1', strategyId=SID, modes=list(POLICIES), calendar=calendar,
        targetDates=targets, dailyRows=daily, formations=formations, opportunities=opportunities, schedules=schedules,
        basis=deepcopy(BASIS), counts=dict(sourceSymbols=len(indexed['symbols']), targetDays=len(targets),
            symbolDays=len(indexed['symbols']) * len(targets), rawSetups=len(formations),
            lockedSetups=sum(f['selectionStatus'] == 'locked' for f in formations),
            rankExcludedSetups=sum(f['selectionStatus'] == 'rankExcluded' for f in formations),
            opportunities=len(opportunities), confirmation=dict(confirmations), outcomes=dict(outcomes),
            signals={p: len(schedules[p]) for p in POLICIES}, qualityReasonCounts=dict(sorted(all_issues.items()))),
        certified=False, targetAchieved=False)


def source_bindings_reference(plan):
    """Inherit the original byte-audited six caches, not a new raw-data audit."""
    expected_proof = 'fb666f86c8f78cd8c1b569bb8a44030e567b56c22db4d91b4880afb91152c649'
    expected_plan = 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa'
    require(list(plan['sourceData']) == list(PERIODS), 'Both original source periods')
    datasets, proofs = {}, {}
    for period in PERIODS:
        require(list(plan['sourceData'][period]) == list(BOARDS), 'All three original board cohorts')
        for board in BOARDS:
            item = plan['sourceData'][period][board]
            source_proof = read(bound(item['sourceValidation']))
            require(item['sourceValidation']['sha256'] == expected_proof and source_proof['passed'] is True
                    and source_proof['planSha256'] == expected_plan, 'Original independently audited source identity')
            cache, manifest_path = bound(item['accountData']), bound(item['sourceManifest'])
            require(source_proof['datasetPins'][item['accountData']['path']] == item['accountData']['sha256']
                    and source_proof['manifestPins'][item['sourceManifest']['path']] == item['sourceManifest']['sha256'],
                    'Source cache and manifest are inside original proof')
            manifest = read(manifest_path)
            require(manifest['planSha256'] == expected_plan and manifest['period'] == period
                    and manifest['boards'][board]['includedCount'] == item['members']
                    and manifest['boards'][board]['accountData']['datasetDigest'] == item['accountDatasetDigest'],
                    'Fixed source population and parsed dataset digest')
            source_completion = cache.parent.parent / 'source-completion.json'
            require(sha(source_completion) == source_proof['sourceCompletionSha256'], 'Original source completion')
            for optional in ('sourceCompletion', 'upstreamInputReceipt'):
                if optional in item: bound(item[optional])
            datasets[item['accountData']['path']] = item['accountData']['sha256']
            proofs[item['sourceValidation']['path']] = item['sourceValidation']['sha256']
    require(len(datasets) == 6 and len(proofs) == 1, 'Six distinct caches and one inherited source proof')
    return dict(datasetPins=datasets, inheritedSourceProofPins=proofs)


def account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas):
    board = P.independent('scripts/verify-board-coverage-reclaim.py')
    accounting = P.independent('scripts/verify-peer-amount-persistence.py')
    cost = accounting.cost_reference()
    prior = P.independent('scripts/verify-online-feedback-entry.py')
    risk = P.independent('scripts/verify-reclaim-exit-transfer.py' if exit_name == 'trail10'
                         else 'scripts/verify-reclaim-volatility-trail.py')
    # The helper is mathematical code shared with older experiments. Rebinding
    # its input SID in memory does not change any frozen source or account.
    risk.SID = SID
    _, _, account = cost.reference_modules(period)
    account.SID = SID
    summary_checks = account.verify_summary(result, summary, prior.interval_reference(period, atlas))
    P.typed_fee_check(result, period)
    projected = deepcopy(summary)
    projected['periods'] = summary['periods'][:7]
    report = risk.audit_account(result, checkpoint, projected, schedule, config, dataset, period,
                                exit_name, board, accounting)
    report['fullSummaryIntervals'] = summary_checks
    identity = result['research']['identity']
    require(identity['signalSchedule'] == schedule and identity['signalScheduleSha256'] == digest(schedule),
            'Full independently reconstructed schedule is actual account input')
    by_id = {s['signalId']: s for s in schedule}
    for trade in result['trades']:
        if trade['side'] == 'buy':
            require(trade['signalId'] in by_id and trade['signalDate'] == by_id[trade['signalId']]['signalDate']
                    and trade['symbol'] == by_id[trade['signalId']]['symbol'], 'Actual buy has exact admitted formation')
    return report, cost.REFERENCES


def registered_plan(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'full-gap-retention-v1' and plan['strategyId'] == SID
            and plan['boards'] == list(BOARDS) and plan['exits'] == list(EXITS)
            and plan['policies'] == list(POLICIES) and list(plan['periods']) == list(PERIODS), 'Registered source/policy matrix')
    expected_trials = dict(newSetupMechanisms=1, newEntryPolicies=2, newExitMechanisms=0,
                           newAccounts=24, referenceAccountsReused=0, candidateTrialCountBefore=41)
    same(plan['rules']['trialAccounting'], expected_trials, 'Exact preregistered experiment scope')
    for key, value in expected_trials.items(): require(plan[key] == value, 'Plan trial accounting ' + key)
    for key in ('parameterSearch', 'holdoutClaim', 'certified', 'targetAchieved'):
        require(plan[key] is False, 'No unsupported claim: ' + key)
    require(read(bound(plan['protocol'])) == plan['rules'] and plan['rules']['candidateIds'] == list(POLICIES),
            'Protocol exact frozen bytes')
    for group in ('sourcePins', 'inputPins'):
        for path, value in plan[group].items(): bound(dict(path=path, sha256=value))
    require(plan['sourcePins'].get(Path(__file__).relative_to(ROOT).as_posix()) == sha(__file__), 'Frozen main verifier identity')
    for name, expected in DEPENDENCIES.items():
        require(plan['sourcePins'].get(name) == expected and sha(ROOT / name) == expected, 'Frozen independent helper ' + name)
    accounting = P.independent('scripts/verify-peer-amount-persistence.py')
    for name, expected in accounting.cost_reference().REFERENCES.items():
        require(plan['sourcePins'].get(name) == expected and sha(ROOT / name) == expected, 'Independent dynamic accounting helper ' + name)
    require(plan['previousDelivery']['sha256'] == 'd7517568052ab89a58f7174439e4e325f84e12046b488418eab8535a04ac42db',
            'Completed predecessor identity')
    parent = read(bound(plan['parentPlan']))
    proof, delivery = read(bound(plan['parentValidation'])), read(bound(plan['previousDelivery']))
    require(proof['passed'] is True and proof['planSha256'] == plan['parentPlan']['sha256']
            and delivery['candidateTrialCount'] == 41, 'Previously completed parent evidence')
    inventory = {r['path']: r['sha256'] for r in delivery['files']}
    for key in ('parentPlan', 'parentValidation'):
        require(inventory.get(plan[key]['path']) == plan[key]['sha256'], 'Parent final delivery binds ' + key)
    require(plan['previousProgram']['sha256'] == '4879c3881f9ce60f93f8b8f797105cbd82201da3cd6e4f3cb33f23c1eabf9bc8'
            and sha(directory / 'program-before.json') == plan['previousProgram']['sha256'], 'Immutable pre-experiment registry snapshot')
    require(plan['sourceData'] == parent['sourceData'] and plan['atlas'] == parent['atlas'], 'Sources and descriptive phases unchanged')
    require(plan['sourcePlan']['sha256'] == 'a911df3c26aa6e662ce8c62d6374e856427c311200b2dc99bcf409c2725ffefa', 'Original six-cohort plan')
    bound(plan['sourcePlan'])
    require(plan['accountContract'] == P.FEE_BINDING, 'Original dated-fee contract')
    contract = read(bound(plan['accountContract']))
    same(plan['feePolicy'], contract['feePolicy'], 'Exact typed fee settings')
    require(type(plan['feePolicy']['minCommission']) is int and plan['feePolicy']['minCommission'] == 5, 'Minimum commission provenance retains integer 5')
    def renamed(value):
        if isinstance(value, dict): return {k: renamed(v) for k, v in value.items()}
        if isinstance(value, list): return [renamed(v) for v in value]
        return SID if value == 'strongUndercutReclaimV1' else deepcopy(value)
    for period in PERIODS:
        expected = {k: parent['periods'][period][k] for k in ('startDate', 'endDate')}
        expected['policyConfigs'] = {e: renamed(parent['periods'][period]['policyConfigs'][e]) for e in EXITS}
        same(plan['periods'][period], expected, 'Only strategy identity may change')
        for exit_name, config in expected['policyConfigs'].items():
            require(config['initialCapital'] == 500000 and config['stopLossPct'] == .05
                    and config['trailingStopPct'] == (.1 if exit_name == 'trail10' else 0)
                    and config['takeProfitPct'] == 0 and config['maxHoldingDays'] == 0
                    and config['maxPositions'] == 5 and config['slippageBps'] == 10, 'Fixed account/exit parameters')
    return plan


def frozen_groups(directory, plan):
    completion = read(directory / 'input-completion.json')
    require(completion['schema'] == 'full-gap-retention-v1-inputs' and completion['planSha256'] == sha(directory / 'plan.json')
            and completion['preAccountTestsSha256'] == sha(directory / 'pre-account-tests.json')
            and completion['allSchedulesFrozenBeforeAccounts'] is True and completion['newAccountsExecuted'] == 0,
            'Complete source-wide pre-account schedule freeze')
    groups = completion['groups']
    require([(g['period'], g['board']) for g in groups] == [(p, b) for p in PERIODS for b in BOARDS], 'All six inputs in original order')
    names = dict(metadata='metadata.json', daily='daily-evidence.jsonl.gz', symbolEvidence='symbol-evidence.jsonl.gz',
                 formations='formations.jsonl.gz', opportunities='opportunities.jsonl.gz', inputReceipt='input-completion.json')
    for group in groups:
        period, board = group['period'], group['board']
        source, folder = plan['sourceData'][period][board], directory / period / board
        require(group['accountData'] == source['accountData'] and group['accountDatasetDigest'] == source['accountDatasetDigest'], 'Prepared original dataset binding')
        bound(group['accountData'])
        for key, filename in names.items(): require(bound(group[key]) == folder / filename, 'Exact input location ' + key)
        require(list(group['schedules']) == list(POLICIES), 'Both original-policy schedules')
        for policy, item in group['schedules'].items():
            require(bound(item) == folder / (policy + '.signals.json'), 'Frozen schedule location')
        receipt = read(bound(group['inputReceipt']))
        require(receipt['planSha256'] == sha(directory / 'plan.json'), 'Per-source input plan')
        same({k: v for k, v in receipt.items() if k not in ('planSha256', 'completedAt')},
             {k: v for k, v in group.items() if k != 'inputReceipt'}, 'Exact per-source input closure')
        require(datetime.fromisoformat(plan['registeredAt']) <= datetime.fromisoformat(receipt['completedAt'])
                <= datetime.fromisoformat(completion['completedAt']), 'Formation generation after registration')
    expected = dict(sourceGroups=6, preparedSchedules=12, accountScheduleBindings=24,
        rawSetups=sum(g['counts']['rawSetups'] for g in groups), opportunities=sum(g['counts']['opportunities'] for g in groups),
        signals={p: sum(g['counts']['signals'][p] for g in groups) for p in POLICIES})
    same(completion['counts'], expected, 'Whole-batch input totals')
    return completion, groups


def pretests_reference(directory, plan, completion):
    tests = read(directory / 'pre-account-tests.json')
    require(tests['passed'] is True and tests['planSha256'] == sha(directory / 'plan.json')
            and tests['sourcePins'] == plan['sourcePins'] and tests['allChecksBeforeFirstNewAccount'] is True,
            'Actual pre-account tests exact source binding')
    require(len(tests['commands']) == 4 and all(r['exitCode'] == 0 for r in tests['commands']), 'Four completed successful commands')
    timestamp = datetime.fromisoformat
    require(timestamp(plan['registeredAt']) <= timestamp(tests['startedAt']) <= timestamp(tests['completedAt'])
            <= timestamp(completion['completedAt']), 'Registration, tests, full inputs chronology')
    return tests


def iter_records(path):
    import gzip
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def verify_inputs(directory):
    require(not (directory / 'input-validation.json').exists(), 'Input proof already exists; never overwrite')
    plan = registered_plan(directory)
    source_proofs = source_bindings_reference(plan)
    completion, groups = frozen_groups(directory, plan)
    pretests_reference(directory, plan, completion)
    counts = dict(sourceGroups=0, sourceDays=0, sourceSymbolDays=0, sourceFormationCount=0, rawSetups=0,
        lockedSetups=0, rankExcludedSetups=0, preparedSchedules=0, accountScheduleBindings=0,
        signals={p: 0 for p in POLICIES}, accountsExecuted=0)
    reports = []
    for group in groups:
        period, board = group['period'], group['board']
        data = read(bound(group['accountData']))
        require(digest(data) == group['accountDatasetDigest'] and data['period'] == period and data['board'] == board
                and len(data['securities']) == plan['sourceData'][period][board]['members'], 'Exact fixed cohort cache contents')
        evidence = iter_records(bound(group['symbolEvidence']))
        evidence_count = 0
        def compare_evidence(expected):
            nonlocal evidence_count
            actual = next(evidence, None)
            require(actual is not None, 'Missing full-population evidence day')
            same(actual, expected, 'Every stock/eligibility/rank/window/lock observation')
            require(digest(actual['rows']) == expected['rowsDigest'], 'Canonical full-symbol digest')
            evidence_count += 1
        spec = plan['periods'][period]
        expected = reference_build(data, start=spec['startDate'], end=spec['endDate'], on_evidence=compare_evidence)
        require(next(evidence, None) is None and evidence_count == expected['counts']['targetDays'], 'Exact full-symbol stream EOF')
        for key, field in [('daily', 'dailyRows'), ('formations', 'formations'), ('opportunities', 'opportunities')]:
            same(records(bound(group[key])), expected[field], 'Entire source ' + field)
        same(read(bound(group['metadata'])), {k: v for k, v in expected.items() if k not in ('dailyRows', 'formations', 'opportunities', 'schedules')},
             'Full metadata and counts independently reconstructed')
        same(group['counts'], expected['counts'], 'Input receipt independent counters')
        for policy in POLICIES:
            schedule = read(bound(group['schedules'][policy]))
            require(schedule == expected['schedules'][policy], 'Signal identity/rank/clock/order exact')
            require(schedule == sorted(schedule, key=lambda s: (s['signalDate'], s['originalRank'], s['symbol']))
                    and len({s['signalId'] for s in schedule}) == len(schedule), 'Unique ordered delayed schedules')
            counts['signals'][policy] += len(schedule)
        require({s['signalId'] for s in expected['schedules']['retained']} <= {s['signalId'] for s in expected['schedules']['control']},
                'Only the retention mask differs within common evidence')
        require(digest(data) == group['accountDatasetDigest'], 'Independent audit preserves source data')
        counts['sourceGroups'] += 1; counts['preparedSchedules'] += 2; counts['accountScheduleBindings'] += 4
        counts['sourceDays'] += evidence_count; counts['sourceSymbolDays'] += expected['counts']['symbolDays']
        counts['sourceFormationCount'] += len(expected['opportunities'])
        for key in ('rawSetups', 'lockedSetups', 'rankExcludedSetups'): counts[key] += expected['counts'][key]
        reports.append(dict(period=period, board=board, counts=expected['counts'], sourceBinding=group['accountData'],
                            fullPopulationEvidenceDays=evidence_count, sourceFormationCount=len(expected['opportunities'])))
        print(json.dumps(dict(checkedSource=[period, board], opportunities=len(expected['opportunities']), passed=True)), flush=True)
        del data, expected; gc.collect()
    require((counts['sourceGroups'], counts['preparedSchedules'], counts['accountScheduleBindings']) == (6, 12, 24), 'Complete input matrix')
    require(counts['sourceFormationCount'] == completion['counts']['opportunities'], 'Original Top5 opportunity denominator')
    registered_plan(directory)
    return P.write_proof(directory, 'input-validation.json', dict(passed=True, verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory / 'plan.json'), inputCompletionSha256=sha(directory / 'input-completion.json'),
        preAccountTestsSha256=sha(directory / 'pre-account-tests.json'), verifierSha256=sha(__file__),
        sourcePins=DEPENDENCIES, inputPins=plan['inputPins'], preparedGroups=groups, counts=counts, groups=reports,
        **source_proofs, checks=[
            'Six byte-bound inherited cohorts; no raw source reload or claimed historical eligibility certification',
            'All stock-days independently rank before eligibility, with exact prior windows and full denominators',
            'All raw setups, non-Top5 locks, unchanged owners, original Top5 and no-refill masks independently reconstructed',
            'Two-session common unknown/censor/basis handling and strict lows checked; F ranking never uses later evidence',
            'T-close signals preserve formation IDs and ranks for identical next-session account execution'],
        holdoutClaim=False, certified=False, targetAchieved=False))


def verify_accounts(directory):
    require(not (directory / 'validation.json').exists(), 'Account proof already exists; never overwrite')
    plan = registered_plan(directory)
    source_proofs = source_bindings_reference(plan)
    completion, groups = frozen_groups(directory, plan)
    pretests_reference(directory, plan, completion)
    inputs = read(directory / 'input-validation.json')
    hashes = {key: sha(directory / filename) for key, filename in (
        ('planSha256', 'plan.json'), ('inputCompletionSha256', 'input-completion.json'),
        ('inputValidationSha256', 'input-validation.json'), ('preAccountTestsSha256', 'pre-account-tests.json'),
        ('runStartSha256', 'run-start.json'))}
    require(inputs['passed'] is True and inputs['verifierSha256'] == sha(__file__), 'Same frozen independent input auditor')
    for key in ('planSha256', 'inputCompletionSha256', 'preAccountTestsSha256'):
        require(inputs[key] == hashes[key], 'Exact independent input proof ' + key)
    same(inputs['preparedGroups'], groups, 'All independently rebuilt source inputs unchanged')
    for key, value in source_proofs.items(): same(inputs[key], value, 'Inherited source proof ' + key)
    require(inputs['counts']['sourceGroups'] == 6 and inputs['counts']['preparedSchedules'] == 12
            and inputs['counts']['accountScheduleBindings'] == 24
            and inputs['counts']['sourceFormationCount'] == completion['counts']['opportunities'], 'Original Top5 population independently counted')
    start = read(directory / 'run-start.json')
    for key, filename in (('inputValidation', 'input-validation.json'), ('inputCompletion', 'input-completion.json'),
                          ('tests', 'pre-account-tests.json')):
        same(start[key], ref(directory / filename), 'Actual account start binding ' + key)
    stamp = datetime.fromisoformat
    require(start['planSha256'] == hashes['planSha256']
            and stamp(completion['completedAt']) <= stamp(inputs['verifiedAt']) <= stamp(start['startedAt']),
            'All signal generation and its independent audit precede accounts')
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'full-gap-retention-v1-comparison'
            and all(comparison[k] is False for k in ('holdoutClaim', 'certified', 'targetAchieved')), 'Comparison scope and claims')
    for key, value in hashes.items(): require(comparison[key] == value, 'Comparison dependency ' + key)
    for key, value in plan['rules']['trialAccounting'].items(): require(comparison[key] == value, 'Comparison trial scope ' + key)
    rows = comparison['rows']
    expected_keys = [(g['period'], g['board'], e, p) for g in groups for e in EXITS for p in POLICIES]
    require([tuple(r[k] for k in ('period', 'board', 'exit', 'policy')) for r in rows] == expected_keys,
            'All 24 new accounts, exact policy/exit/source ordering')
    require(len(comparison['groupCompletions']) == 12, 'All twelve terminal exit-group receipts')
    for (group, exit_name), item in zip([(g, e) for g in groups for e in EXITS], comparison['groupCompletions']):
        period, board = group['period'], group['board']
        folder = directory / period / board / exit_name
        require(bound(item) == folder / 'group-completion.json', 'Exact terminal group path')
        terminal = read(bound(item))
        require(tuple(terminal[k] for k in ('period', 'board', 'exit')) == (period, board, exit_name)
                and terminal['completed'] is True and terminal['newAccounts'] == 2
                and terminal['referenceAccountsReused'] == 0, 'Actual terminal account matrix')
        for key, value in hashes.items(): require(terminal[key] == value, 'Terminal binding ' + key)
        expected_rows = [r for r in rows if (r['period'], r['board'], r['exit']) == (period, board, exit_name)]
        same(terminal['rows'], expected_rows, 'Full terminal artifact descriptors')
        group_start = read(folder / 'group-run-start.json')
        require(group_start['planSha256'] == hashes['planSha256'] and group_start['runStartSha256'] == hashes['runStartSha256']
                and stamp(start['startedAt']) <= stamp(group_start['startedAt']) <= stamp(terminal['completedAt'])
                <= stamp(comparison['completedAt']), 'Per-group start/terminal/collection chronology')
    atlas = read(bound(plan['atlas']))
    counts = dict(accounts=0, newAccounts=0, reusedAccounts=0, sourceGroups=6,
        sourceFormationCount=inputs['counts']['sourceFormationCount'], preparedSchedules=12,
        accountScheduleBindings=24, newFills=0, heldCloseChecks=0, summaryIntervals=0)
    reports, dependency_pins = [], dict(DEPENDENCIES)
    data, data_key = None, None
    for row in rows:
        period, board, exit_name, policy = (row[k] for k in ('period', 'board', 'exit', 'policy'))
        group = next(g for g in groups if (g['period'], g['board']) == (period, board))
        folder = directory / period / board / exit_name
        require(set(row) == {'period', 'board', 'exit', 'policy', 'schedule', 'result', 'checkpoint', 'summary', 'reusedAccount'}
                and row['reusedAccount'] is False, 'Every account is new with complete descriptor')
        same(row['schedule'], group['schedules'][policy], 'Same policy schedule across both exits')
        for field, suffix in (('result', '.result.json.gz'), ('checkpoint', '.checkpoint.json.gz'), ('summary', '.summary.json')):
            require(bound(row[field]) == folder / (policy + suffix), 'Exact account artifact path ' + field)
        if data_key != (period, board):
            if data is not None: del data; gc.collect()
            data = read(bound(group['accountData'])); data_key = (period, board)
            require(digest(data) == group['accountDatasetDigest'], 'Original source dataset contents')
        result, checkpoint, summary, schedule = [read(bound(row[k])) for k in ('result', 'checkpoint', 'summary', 'schedule')]
        require(summary['policyId'] == policy and summary['policyLabel'] == policy, 'Unchanged policy summary identity')
        report, more_pins = account_reference(result, checkpoint, summary, schedule,
            plan['periods'][period]['policyConfigs'][exit_name], data, period, exit_name, atlas)
        dependency_pins.update(more_pins)
        counts['accounts'] += 1; counts['newAccounts'] += 1
        counts['newFills'] += len(result['trades']); counts['heldCloseChecks'] += report['dailyRisk']['heldDays']
        counts['summaryIntervals'] += len(summary['periods'])
        reports.append(dict(period=period, board=board, exit=exit_name, policy=policy, artifactRefs=row, checks=report))
        require(digest(data) == group['accountDatasetDigest'], 'Independent accounting audit preserves source')
        print(json.dumps(dict(checkedAccount=[period, board, exit_name, policy], passed=True)), flush=True)
        del result, checkpoint, summary, schedule; gc.collect()
    require((counts['accounts'], counts['newAccounts'], counts['reusedAccounts']) == (24, 24, 0), 'Actual audited account totals')
    registered_plan(directory)
    return P.write_proof(directory, 'validation.json', dict(passed=True, verifiedAt=datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in hashes.items() if key != 'runStartSha256'},
        comparisonSha256=sha(directory / 'comparison.json'), verifierSha256=sha(__file__),
        sourcePins=dependency_pins, inputPins=plan['inputPins'], runStart=ref(directory / 'run-start.json'),
        counts=counts, accounts=reports, accountArtifacts=rows, **source_proofs,
        checks=['All six source populations and twelve independently rebuilt schedules retain exact hashes before all 24 accounts',
                'All new fee ledgers, cash/shares, full equity paths, closed/open episodes, terminal marks and summaries independently reconciled',
                'Every held close replays initial stop, fixed trail or volatility ratchet, unknown carry and exit priority without production risk functions',
                'Every buy belongs to the exact admitted F-formed, T-close schedule; next-session, lots, price limits and volume caps audited',
                'Historical commission integer 5 identity retained; no account reruns or baseline reuse'],
        holdoutClaim=False, certified=False, targetAchieved=False))


def self_test():
    import unittest
    spec = importlib.util.spec_from_file_location('_gap_verifier_tests', ROOT / 'tests/test_full_gap_retention_verifier.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)
    require(result.wasSuccessful(), 'Independent synthetic tests failed')
    report = dict(passed=True, syntheticGroups=result.testsRun, realSourceReads=0, accountExecutions=0,
                  verifierSha256=sha(__file__))
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('inputs', 'accounts'), default='accounts')
    parser.add_argument('--inputs', '--inputs-only', dest='inputs', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--out', type=Path, default=DEFAULT)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    directory = args.out.resolve()
    try:
        return verify_inputs(directory) if args.inputs or args.mode == 'inputs' else verify_accounts(directory)
    except Exception as error:
        if directory.exists():
            failures = directory / 'validation-attempts'
            failures.mkdir(exist_ok=True)
            filename = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json'
            with (failures / filename).open('x', encoding='utf-8') as stream:
                json.dump(dict(passed=False, at=datetime.now(timezone.utc).isoformat(),
                    mode='inputs' if args.inputs else args.mode, verifierSha256=sha(__file__),
                    errorType=type(error).__name__, message=str(error), traceback=traceback.format_exc()),
                    stream, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__':
    main()
