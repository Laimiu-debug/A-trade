"""Independent leader/member traded-amount handoff and saved-account audit.

The new builder and execution runner are never imported. Frozen parent evidence
is inherited by exact hashes; new per-member lifecycles are reconstructed from
source bars. No account is executed by this verifier.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/leader-amount-handoff-v1'
SID = 'leaderAmountHandoffV1'
POLICIES = ('control', 'handoff')
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
EXITS = ('trail10', 'volatilityTrail')
GAP_PATH = 'scripts/verify-full-gap-retention.py'
GAP_SHA = '394729cbb46bd71d7920d0ba30c361e6153204e7c522856ed281d35d3bf3833e'
PARENT_PLAN_SHA = '91eace6b78d655fbeb3ff10abc4f4c69f9a61c273a1d9447dc3bc38788f3d161'
PARENT_INPUT_PROOF_SHA = 'e3269accef17c880c4ef69102b2632e2763648c6a5213435c469a712873aaf61'
BASIS = {
    'source': 'unchanged six board-local cohorts and audited parent leader events; no new group or rank calculation',
    'price': 'frozen research_leader_lag._Series exact raw positive OHLCV/envelope/status validity',
    'amount': 'M3=math.fsum(explicit positive CNY amount on d-2..d); all three price/status bars also valid',
    'formation': 'A=parent formationDate; F=parent peerFormationDate=A-10; fixed20 ordered peers',
    'lock': 'earliest anchor event owns A..A+5 even if group incomplete; suppressed events cannot extend',
    'initial': 'jointly known member M3(A)<=anchor M3(A); equality admitted',
    'firstBreakout': 'first close above preceding five exact highs during A+1..A+5; unknown price terminates',
    'handoff': 'member M3(T-1)<=anchor M3(T-1) and member M3(T)>anchor M3(T)',
    'selection': 'all raw first breakouts, earliest formation/leader/member order, first owner per member, Top5 before amount masks',
    'diagnostics': 'M3(T)/(3*mean amount T-20..T-1); diagnostics never affect admission',
    'limitations': 'traded-value activity proxy, not turnover, actor identity or causal capital transfer; raw surviving-cohort development history',
}


def load_gap():
    path = ROOT / GAP_PATH
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != GAP_SHA:
            raise AssertionError('Frozen independent account helper changed')
    spec = importlib.util.spec_from_file_location('_handoff_independent_gap', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = load_gap()
require, finite, same, sha, digest, read, records, ref, bound = (
    getattr(G, key) for key in ('require', 'finite', 'same', 'sha', 'digest', 'read', 'records', 'ref', 'bound'))
DEPENDENCIES = {GAP_PATH: GAP_SHA, **G.DEPENDENCIES}
P = G.P


def sum_finite(values):
    try:
        result = math.fsum(values)
    except (ValueError, OverflowError):
        return None
    return result if finite(result) else None


def amount_reference(source, calendar, at, slots=3):
    dates = calendar[max(0, at - slots + 1):at + 1] if at >= 0 else []
    amounts, reasons = [], []
    if len(dates) != slots:
        reasons.append('insufficientCalendarHistory')
    for pos in range(max(0, at - slots + 1), at + 1):
        day = calendar[pos]
        bar = source['bars'][pos]
        issue = source['reasons'][pos]
        value = bar.get('amount') if bar else None
        amounts.append(value if finite(value) else None)
        if issue: reasons.append(day + ':' + issue)
        if not finite(value) or value <= 0: reasons.append(day + ':nonpositiveOrUnknownAmount')
    total = None if reasons else sum_finite(amounts)
    if not reasons and (total is None or total <= 0): reasons.append('nonfiniteAmountSum')
    return dict(date=calendar[at] if at >= 0 else None, dates=dates, amounts=amounts,
        total=None if reasons else total, status='unknown' if reasons else 'known', reasons=reasons)


def price_reference(source, calendar, at):
    dates = calendar[max(0, at - 5):at + 1]
    reason = G.window_reason(source, at, 6)
    close = source['bars'][at].get('close') if source['bars'][at] else None
    close = close if finite(close) else None
    high = None if reason else max(b['high'] for b in source['bars'][at - 5:at])
    return dict(date=calendar[at], windowDates6=dates, prior5High=high, close=close,
        breakout=None if reason else close > high, status='unknown' if reason else 'known', reason=reason)


def crossing_reference(member, anchor, calendar, at):
    previous = dict(date=calendar[at - 1], member=amount_reference(member, calendar, at - 1),
                    anchor=amount_reference(anchor, calendar, at - 1))
    current = dict(date=calendar[at], member=amount_reference(member, calendar, at),
                   anchor=amount_reference(anchor, calendar, at))
    reasons = []
    for when, record in (('previous', previous), ('current', current)):
        for side in ('member', 'anchor'):
            reasons.extend(when + ':' + side + ':' + issue for issue in record[side]['reasons'])
    crossing = None if reasons else (previous['member']['total'] <= previous['anchor']['total']
                                     and current['member']['total'] > current['anchor']['total'])
    return dict(previous=previous, current=current, status='unknown' if reasons else 'known', reasons=reasons, crossing=crossing)


def replay_member(member, anchor, calendar, formation_at):
    """Independent per-episode timeline, without global-day mutable states."""
    initial_member = amount_reference(member, calendar, formation_at)
    initial_anchor = amount_reference(anchor, calendar, formation_at)
    observations = []
    if initial_member['status'] != 'known' or initial_anchor['status'] != 'known':
        terminal, ended = 'initialUnknown', formation_at
    elif initial_member['total'] > initial_anchor['total']:
        terminal, ended = 'initialAbove', formation_at
    else:
        terminal, ended = 'censored', len(calendar) - 1
        for at in range(formation_at + 1, min(formation_at + 6, len(calendar))):
            price = price_reference(member, calendar, at)
            if price['status'] != 'known':
                status = 'priceUnknown'
            elif price['breakout']:
                status = 'firstBreakout'
            elif at == formation_at + 5:
                status = 'expired'
            else:
                status = 'observing'
            observations.append(dict(at=at, status=status, price=price,
                amount=crossing_reference(member, anchor, calendar, at) if status == 'firstBreakout' else None))
            if status != 'observing':
                terminal, ended = status, at
                break
    return dict(initialMember=initial_member, initialAnchor=initial_anchor,
                terminal=terminal, endedAt=ended, observations=observations)


def event_reference(events, indexed, start, end):
    require(isinstance(events, list), 'Parent event list')
    calendar, symbols = indexed['calendar'], set(indexed['symbols'])
    positions = {d: i for i, d in enumerate(calendar)}
    previous, ids, ranks, result = None, set(), set(), []
    for event in events:
        require(isinstance(event, dict), 'Parent event object')
        day = event.get('formationDate')
        require(type(day) is str and datetime.fromisoformat(day).date().isoformat() == day, 'Canonical event date')
        if day > end: continue
        require(start <= day <= end and day in positions, 'Event on target calendar')
        symbol, rank = event.get('anchorSymbol'), event.get('originalLeaderRank')
        require(symbol in symbols and type(rank) is int and 1 <= rank <= 5, 'Original leader identity/rank')
        key = (day, rank, symbol)
        require(previous is None or previous < key, 'Parent event original ordering')
        previous = key
        require(event.get('eventId') == f'leaderLagFollowersV1:{day}:{symbol}'
                and event['eventId'] not in ids and (day, rank) not in ranks, 'Original unique parent identities')
        ids.add(event['eventId']); ranks.add((day, rank))
        at = positions[day]
        require(at >= 10 and event.get('peerFormationDate') == calendar[at - 10], 'F exactly A minus ten sessions')
        group = event.get('groupSummary')
        require(isinstance(group, dict) and group.get('anchorSymbol') == symbol
                and group.get('peerFormationDate') == calendar[at - 10], 'Parent group identity')
        members, count, kind = group.get('members'), group.get('selectedCount'), group.get('kind')
        require(isinstance(members, list) and type(count) is int and count == len(members)
                and 0 <= count <= 20 and group.get('requiredCount') == 20
                and kind in ('formed', 'incomplete', 'unknown') and (kind != 'formed' or count == 20), 'Parent group scope')
        seen = set()
        for n, member in enumerate(members, 1):
            require(member.get('symbol') in symbols and member['symbol'] != symbol and member['symbol'] not in seen
                    and member.get('frozenMemberRank') == n, 'Frozen peer identity/order')
            seen.add(member['symbol'])
        require(isinstance(event.get('groupSha256'), str) and len(event['groupSha256']) == 64, 'Inherited full-group digest retained')
        result.append(event)
    return result


def raw_order(row):
    return tuple(row[k] for k in ('formationDate', 'originalLeaderRank', 'anchorSymbol', 'frozenMemberRank', 'memberSymbol'))


def ratio_reference(numerator, denominator):
    if not finite(numerator) or numerator <= 0 or not finite(denominator) or denominator <= 0: return None
    value = numerator / denominator
    return value if finite(value) else None


def diagnostics_reference(row, sources, calendar, at):
    result = {}
    for role in ('member', 'anchor'):
        prior = amount_reference(sources[row[role + 'Symbol']], calendar, at - 1, 20)
        average = prior['total'] / 20 if prior['total'] is not None else None
        result[role] = dict(prior20Dates=prior['dates'], prior20MeanAmount=average,
            ownRelativeAmount=ratio_reference(row['amountEvidence']['current'][role]['total'], 3 * average if average is not None else None),
            reasons=prior['reasons'])
    result['pairRatios'] = {name: ratio_reference(pair['member']['total'], pair['anchor']['total']) for name, pair in (
        ('initial', row['initialAmountEvidence']), ('previous', row['amountEvidence']['previous']), ('current', row['amountEvidence']['current']))}
    return result


def reference_build(dataset, leader_events, *, start, end, progress=None, on_evidence=None):
    """Build each episode independently, then merge by decision day and owner."""
    indexed = G.source_reference(dataset, start, end)
    calendar, targets, sources = indexed['calendar'], indexed['targetDates'], indexed['sources']
    positions = {day: at for at, day in enumerate(calendar)}
    events = event_reference(leader_events, indexed, start, end)
    locks, formations, episodes, raw_by_day, observations_by_day = {}, [], [], {}, {}
    forms_by_day = {d: [] for d in targets}
    for event in events:
        day, anchor = event['formationDate'], event['anchorSymbol']
        at, parent, group = positions[day], event['eventId'], event['groupSummary']
        lock = locks.get(anchor)
        blocked = lock is not None and at <= lock[1]
        status = 'locked' if blocked else 'formed' if group['kind'] == 'formed' else 'notFormed'
        owner, through = lock if blocked else (parent, at + 5)
        if not blocked: locks[anchor] = (owner, through)
        formation = dict(parentEventId=parent, formationDate=day, peerFormationDate=event['peerFormationDate'],
            anchorSymbol=anchor, originalLeaderRank=event['originalLeaderRank'], groupSha256=event['groupSha256'],
            groupKind=group['kind'], members=deepcopy(group['members']), status=status,
            lockOwnerEventId=owner, lockThroughIndex=through, createdMemberEpisodes=20 if status == 'formed' else 0)
        formations.append(formation); forms_by_day[day].append(formation)
        if status != 'formed': continue
        for member in group['members']:
            symbol = member['symbol']
            replay = replay_member(sources[symbol], sources[anchor], calendar, at)
            initial = dict(date=day, member=replay['initialMember'], anchor=replay['initialAnchor'])
            initial_state = replay['terminal'] if replay['terminal'] in ('initialUnknown', 'initialAbove') else 'observing'
            identity = f'{SID}:{day}:{anchor}:{symbol}'
            episode = dict(episodeId=identity, parentEventId=parent, formationDate=day, peerFormationDate=event['peerFormationDate'],
                anchorSymbol=anchor, memberSymbol=symbol, originalLeaderRank=event['originalLeaderRank'],
                frozenMemberRank=member['frozenMemberRank'], initialAmountEvidence=initial, initialStatus=initial_state,
                observedSessions=len(replay['observations']),
                lastObservationDate=calendar[replay['observations'][-1]['at']] if replay['observations'] else None,
                terminalStatus=replay['terminal'], terminalDate=calendar[replay['endedAt']])
            episodes.append(episode)
            for observation in replay['observations']:
                decision = calendar[observation['at']]
                item = dict(episodeId=identity, date=decision, offset=observation['at'] - at,
                            priceEvidence=observation['price'], state=observation['status'])
                if observation['status'] == 'firstBreakout':
                    item['amountEvidence'] = deepcopy(observation['amount'])
                    raw = {k: deepcopy(episode[k]) for k in ('episodeId', 'parentEventId', 'formationDate', 'peerFormationDate',
                        'anchorSymbol', 'memberSymbol', 'originalLeaderRank', 'frozenMemberRank', 'initialAmountEvidence')}
                    raw.update(formationId=identity, signalId=identity, symbol=symbol, decisionDate=decision, signalDate=decision,
                        priceEvidence=observation['price'], amountEvidence=observation['amount'], ownershipStatus=None,
                        ownerEpisodeId=None, originalRank=None, selectionStatus=None)
                    raw_by_day.setdefault(decision, []).append(raw)
                observations_by_day.setdefault(decision, []).append(item)
    # Per-episode construction retains creation order, matching chronological
    # observation queues without allowing a later formation to own an earlier one.
    daily, confirmations, opportunities = [], [], []
    schedules = {p: [] for p in POLICIES}
    for day in targets:
        raw_today = sorted(raw_by_day.get(day, []), key=raw_order)
        owners = {}
        for raw in raw_today:
            symbol = raw['memberSymbol']
            if symbol in owners:
                raw.update(ownershipStatus='duplicateSuppressed', ownerEpisodeId=owners[symbol]['episodeId'], selectionStatus='duplicateSuppressed')
            else:
                owners[symbol] = raw
                rank = len(owners)
                raw.update(ownershipStatus='owner', ownerEpisodeId=raw['episodeId'], originalRank=rank,
                           selectionStatus='selected' if rank <= 5 else 'rankExcluded')
                if rank <= 5:
                    selected = deepcopy(raw)
                    selected.update(diagnostics=diagnostics_reference(raw, sources, calendar, positions[day]),
                        control=True if raw['amountEvidence']['status'] == 'known' else None,
                        handoff=raw['amountEvidence']['crossing'], signals={})
                    for policy in POLICIES:
                        signal = dict(signalId=raw['signalId'], strategyId=SID, symbol=symbol, signalDate=day, originalRank=rank) if selected[policy] is True else None
                        selected['signals'][policy] = signal
                        if signal is not None: schedules[policy].append(deepcopy(signal))
                    opportunities.append(selected)
            confirmations.append(deepcopy(raw))
        observed = observations_by_day.get(day, [])
        censored = [e['episodeId'] for e in episodes if e['terminalStatus'] == 'censored'] if day == targets[-1] else []
        evidence = dict(date=day, formations=deepcopy(forms_by_day[day]), memberObservations=observed,
                        rawConfirmationIds=[r['episodeId'] for r in raw_today], censoredEpisodeIds=censored)
        daily.append(dict(date=day, sourceEvents=len(forms_by_day[day]),
            formationStatusCounts=dict(sorted(Counter(f['status'] for f in forms_by_day[day]).items())),
            memberObservationCounts=dict(sorted(Counter(o['state'] for o in observed).items())),
            rawConfirmations=len(raw_today), duplicateSuppressed=len(raw_today) - len(owners), uniqueOwners=len(owners),
            selectedTop5=min(5, len(owners)), rankExcluded=max(0, len(owners) - 5), censored=len(censored), evidenceSha256=digest(evidence)))
        if on_evidence: on_evidence(deepcopy(evidence))
        if progress and (len(daily) % 20 == 0 or day == targets[-1]): progress(dict(checkedDays=len(daily), targetDays=len(targets)))
    initial, terminal = Counter(e['initialStatus'] for e in episodes), Counter(e['terminalStatus'] for e in episodes)
    known, crossed = sum(o['control'] is True for o in opportunities), sum(o['handoff'] is True for o in opportunities)
    counts = dict(sourceSymbols=len(indexed['symbols']), targetDays=len(targets), sourceEvents=len(formations),
        lockedEvents=sum(f['status'] == 'locked' for f in formations), unlockedFormed=sum(f['status'] == 'formed' for f in formations),
        unlockedNotFormed=sum(f['status'] == 'notFormed' for f in formations), memberEpisodes=len(episodes),
        initialUnknown=initial['initialUnknown'], initialAbove=initial['initialAbove'], observedMembers=initial['observing'],
        **{key: terminal[key] for key in ('priceUnknown', 'firstBreakout', 'expired', 'censored')},
        duplicateSuppressed=sum(r['ownershipStatus'] == 'duplicateSuppressed' for r in confirmations),
        uniqueOwners=sum(r['ownershipStatus'] == 'owner' for r in confirmations), selectedTop5=len(opportunities),
        rankExcluded=sum(r['selectionStatus'] == 'rankExcluded' for r in confirmations), jointKnown=known,
        jointUnknown=len(opportunities) - known, crossingTrue=crossed, crossingFalse=known - crossed,
        opportunities=len(opportunities), signals={p: len(schedules[p]) for p in POLICIES},
        notScheduled={p: len(opportunities) - len(schedules[p]) for p in POLICIES})
    return dict(version='leader-amount-handoff-v1', strategyId=SID, modes=list(POLICIES), calendar=calendar,
        targetDates=targets, basis=deepcopy(BASIS), counts=counts, dailyRows=daily, formations=formations,
        memberEpisodes=episodes, rawConfirmations=confirmations, opportunities=opportunities, schedules=schedules,
        certified=False, targetAchieved=False)


def account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas):
    # Change the helper's accepted input SID only in this isolated Python module.
    G.SID = SID
    return G.account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas)


def source_bindings_reference(plan):
    answer = G.source_bindings_reference(plan)
    require(plan['eventParentPlan']['sha256'] == PARENT_PLAN_SHA
            and plan['eventParentInputValidation']['sha256'] == PARENT_INPUT_PROOF_SHA
            and plan['eventParentInputCompletion']['sha256'] ==
            'b32cf3c5fcb4e6b3473b6aad983d5894e8839907874b9d36166eda0f3743746e',
            'Exact previously audited original leader-event family')
    parent, completion, proof = [read(bound(plan[k])) for k in
        ('eventParentPlan', 'eventParentInputCompletion', 'eventParentInputValidation')]
    require(proof['passed'] is True and proof['planSha256'] == PARENT_PLAN_SHA
            and completion['planSha256'] == PARENT_PLAN_SHA
            and proof['inputCompletionSha256'] == plan['eventParentInputCompletion']['sha256'],
            'Original leader and peer proof covers frozen inputs')
    require(list(plan['eventData']) == list(PERIODS), 'Both event periods retained')
    pins, event_counts = {}, {}
    for key in ('eventParentPlan', 'eventParentInputCompletion', 'eventParentInputValidation'):
        pins[plan[key]['path']] = plan[key]['sha256']
    for period in PERIODS:
        require(list(plan['eventData'][period]) == list(BOARDS), 'All board-local event populations retained')
        event_counts[period] = {}
        for board in BOARDS:
            event, source = plan['eventData'][period][board], plan['sourceData'][period][board]
            same(event['inputCompletion'], completion['outputs'][period][board], 'Original source receipt in full completion')
            receipt = read(bound(event['inputCompletion']))
            require(receipt['planSha256'] == PARENT_PLAN_SHA
                    and (receipt['period'], receipt['board']) == (period, board), 'Event source identity')
            for key in ('accountData', 'accountDatasetDigest', 'sourceManifest', 'sourceValidation', 'members'):
                same(receipt[key], source[key], 'Event receipt uses current original source ' + key)
                same(parent['sourceData'][period][board][key], source[key], 'Original event plan source ' + key)
            same(event['leaderEvents'], receipt['files']['leader-events.jsonl.gz'], 'Original complete event stream')
            same(event['leaderEvents'], proof['inputArtifacts'][period][board]['leader-events.jsonl.gz'],
                 'Original event stream was included in prior independent proof')
            require(proof['datasetPins'][source['accountData']['path']] == source['accountData']['sha256']
                    and event['sourceEventCount'] == receipt['counts']['leaderEventCount'], 'Original cohort and event denominator')
            for key in ('inputCompletion', 'leaderEvents'):
                bound(event[key]); pins[event[key]['path']] = event[key]['sha256']
            event_counts[period][board] = event['sourceEventCount']
    answer.update(eventParentPins=pins, eventParentCounts=event_counts)
    return answer


def registered_plan(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'leader-amount-handoff-v1' and plan['strategyId'] == SID
            and plan['boards'] == list(BOARDS) and plan['exits'] == list(EXITS)
            and plan['policies'] == list(POLICIES) and list(plan['periods']) == list(PERIODS), 'Registered source/policy matrix')
    expected_trials = dict(newSetupMechanisms=1, newEntryPolicies=2, newAccounts=24, referenceAccountsReused=0, newUniverseVariants=0)
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
    require(plan['previousDelivery']['sha256'] == 'a10cd2116f91046cb6d3879a66349833b4ff13dc0ea856ad1380afa1eede4e87',
            'Completed predecessor identity')
    parent = read(bound(plan['parentPlan']))
    proof, delivery = read(bound(plan['parentValidation'])), read(bound(plan['previousDelivery']))
    require(proof['passed'] is True and proof['planSha256'] == plan['parentPlan']['sha256']
            and delivery['candidateTrialsAfter'] == 42, 'Previously completed parent evidence')
    inventory = {r['path']: r['sha256'] for r in delivery['files']}
    for key in ('parentPlan', 'parentValidation'):
        require(inventory.get(plan[key]['path']) == plan[key]['sha256'], 'Parent final delivery binds ' + key)
    require(plan['previousProgram']['sha256'] == '88cedb4dd48abd25e34b1c0c4d8e3ede903095e2d606e073819d2729171d69d2'
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
        return SID if value == 'fullGapRetentionV1' else deepcopy(value)
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
    require(completion['schema'] == 'leader-amount-handoff-v1-inputs' and completion['planSha256'] == sha(directory / 'plan.json')
            and completion['preAccountTestsSha256'] == sha(directory / 'pre-account-tests.json')
            and completion['allSchedulesFrozenBeforeAccounts'] is True and completion['newAccountsExecuted'] == 0,
            'Complete source-wide pre-account schedule freeze')
    groups = completion['groups']
    require([(g['period'], g['board']) for g in groups] == [(p, b) for p in PERIODS for b in BOARDS], 'All six inputs in original order')
    names = dict(metadata='metadata.json', daily='daily-evidence.jsonl.gz', memberEvidence='member-evidence.jsonl.gz',
                 formations='formations.jsonl.gz', memberEpisodes='member-episodes.jsonl.gz',
                 rawConfirmations='raw-confirmations.jsonl.gz', opportunities='opportunities.jsonl.gz', inputReceipt='input-completion.json')
    for group in groups:
        period, board = group['period'], group['board']
        source, folder = plan['sourceData'][period][board], directory / period / board
        require(group['accountData'] == source['accountData'] and group['accountDatasetDigest'] == source['accountDatasetDigest'], 'Prepared original dataset binding')
        bound(group['accountData'])
        require(group['parentLeaderEvents'] == plan['eventData'][period][board]['leaderEvents']
                and group['parentInputReceipt'] == plan['eventData'][period][board]['inputCompletion'], 'Original event input references')
        bound(group['parentLeaderEvents']); bound(group['parentInputReceipt'])
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
        sourceEvents=sum(g['counts']['sourceEvents'] for g in groups),
        memberEpisodes=sum(g['counts']['memberEpisodes'] for g in groups),
        firstBreakout=sum(g['counts']['firstBreakout'] for g in groups), opportunities=sum(g['counts']['opportunities'] for g in groups),
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
    counts = dict(sourceGroups=0, sourceDays=0, sourceEvents=0, memberEpisodes=0, rawBreakouts=0,
        sourceOpportunityCount=0, preparedSchedules=0, accountScheduleBindings=0,
        signals={p: 0 for p in POLICIES}, accountsExecuted=0)
    reports = []
    for group in groups:
        period, board = group['period'], group['board']
        data = read(bound(group['accountData']))
        require(digest(data) == group['accountDatasetDigest'] and data['period'] == period and data['board'] == board
                and len(data['securities']) == plan['sourceData'][period][board]['members'], 'Exact fixed cohort cache contents')
        parent_events = records(bound(group['parentLeaderEvents']))
        require(len(parent_events) == plan['eventData'][period][board]['sourceEventCount'], 'All original leader events retained')
        evidence = iter_records(bound(group['memberEvidence']))
        evidence_count = 0
        def compare_evidence(expected):
            nonlocal evidence_count
            actual = next(evidence, None)
            require(actual is not None, 'Missing full member lifecycle evidence day')
            same(actual, expected, 'Every lock, initial member, price transition, amount trigger and ownership observation')
            require(digest(actual) == digest(expected), 'Exact canonical lifecycle evidence digest')
            evidence_count += 1
        spec = plan['periods'][period]
        expected = reference_build(data, parent_events, start=spec['startDate'], end=spec['endDate'], on_evidence=compare_evidence)
        require(next(evidence, None) is None and evidence_count == expected['counts']['targetDays'], 'Exact member evidence stream EOF')
        row_fields = ('dailyRows', 'formations', 'memberEpisodes', 'rawConfirmations', 'opportunities', 'schedules')
        for key, field in [('daily', 'dailyRows'), ('formations', 'formations'), ('memberEpisodes', 'memberEpisodes'),
                           ('rawConfirmations', 'rawConfirmations'), ('opportunities', 'opportunities')]:
            same(records(bound(group[key])), expected[field], 'Entire independently reconstructed ' + field)
        same(read(bound(group['metadata'])), {k: v for k, v in expected.items() if k not in row_fields},
             'Full metadata and counters independently reconstructed')
        same(group['counts'], expected['counts'], 'Per-source input receipt counters')
        c = expected['counts']
        require(c['sourceEvents'] == len(parent_events) == c['lockedEvents'] + c['unlockedFormed'] + c['unlockedNotFormed']
                and c['memberEpisodes'] == 20 * c['unlockedFormed'] == c['initialUnknown'] + c['initialAbove'] + c['observedMembers']
                and c['observedMembers'] == c['priceUnknown'] + c['firstBreakout'] + c['expired'] + c['censored']
                and c['firstBreakout'] == c['duplicateSuppressed'] + c['uniqueOwners']
                and c['uniqueOwners'] == c['selectedTop5'] + c['rankExcluded']
                and c['selectedTop5'] == c['opportunities'] == c['jointKnown'] + c['jointUnknown']
                and c['jointKnown'] == c['crossingTrue'] + c['crossingFalse'], 'All lifecycle population partitions close')
        for policy in POLICIES:
            schedule = read(bound(group['schedules'][policy]))
            require(schedule == expected['schedules'][policy], 'Signal identity/rank/clock/order exact')
            require(schedule == sorted(schedule, key=lambda s: (s['signalDate'], s['originalRank'], s['symbol'], s['signalId']))
                    and len({s['signalId'] for s in schedule}) == len(schedule), 'Unique ordered original owner slots')
            require(len(schedule) + c['notScheduled'][policy] == c['opportunities'], 'Unscheduled original slots preserved')
            counts['signals'][policy] += len(schedule)
        require({s['signalId'] for s in expected['schedules']['handoff']} <= {s['signalId'] for s in expected['schedules']['control']},
                'Only the crossover mask differs within common amount completeness')
        require(digest(data) == group['accountDatasetDigest'], 'Independent audit preserves source data')
        counts['sourceGroups'] += 1; counts['preparedSchedules'] += 2; counts['accountScheduleBindings'] += 4
        counts['sourceDays'] += evidence_count; counts['sourceEvents'] += len(parent_events)
        counts['memberEpisodes'] += c['memberEpisodes']; counts['rawBreakouts'] += c['firstBreakout']
        counts['sourceOpportunityCount'] += len(expected['opportunities'])
        reports.append(dict(period=period, board=board, counts=c, sourceBinding=group['accountData'],
            eventBinding=group['parentLeaderEvents'], fullMemberEvidenceDays=evidence_count,
            sourceOpportunityCount=len(expected['opportunities'])))
        print(json.dumps(dict(checkedSource=[period, board], opportunities=len(expected['opportunities']), passed=True)), flush=True)
        del data, expected, parent_events; gc.collect()
    require((counts['sourceGroups'], counts['preparedSchedules'], counts['accountScheduleBindings']) == (6, 12, 24), 'Complete input matrix')
    require(counts['sourceOpportunityCount'] == completion['counts']['opportunities'], 'Original member Top5 denominator')
    registered_plan(directory)
    return P.write_proof(directory, 'input-validation.json', dict(passed=True, verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory / 'plan.json'), inputCompletionSha256=sha(directory / 'input-completion.json'),
        preAccountTestsSha256=sha(directory / 'pre-account-tests.json'), verifierSha256=sha(__file__),
        sourcePins=DEPENDENCIES, inputPins=plan['inputPins'], preparedGroups=groups, counts=counts, groups=reports,
        **source_proofs, checks=[
            'Six byte-bound original cohorts and all previously audited leader events and fixed peers; no inherited outcome selection',
            'All new anchor locks, initial amounts, exact price windows and first-terminal states independently reconstructed',
            'All raw first breakouts and duplicate ownership sorted before amount completeness and Top5 masks; no refill',
            'Adjacent member/anchor three-session sums and strict crossover recomputed; diagnostic ratios never gate',
            'All source events, member episodes, terminal states, raw breakouts, original Top5 and policy schedules reconcile',
            'A formation and T-close decision are distinct, and final-day signals remain scheduled'],
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
            and inputs['counts']['sourceOpportunityCount'] == completion['counts']['opportunities'], 'Original Top5 population independently counted')
    start = read(directory / 'run-start.json')
    for key, filename in (('inputValidation', 'input-validation.json'), ('inputCompletion', 'input-completion.json'),
                          ('tests', 'pre-account-tests.json')):
        same(start[key], ref(directory / filename), 'Actual account start binding ' + key)
    stamp = datetime.fromisoformat
    require(start['planSha256'] == hashes['planSha256']
            and stamp(completion['completedAt']) <= stamp(inputs['verifiedAt']) <= stamp(start['startedAt']),
            'All signal generation and its independent audit precede accounts')
    comparison = read(directory / 'comparison.json')
    require(comparison['schema'] == 'leader-amount-handoff-v1-comparison'
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
        sourceOpportunityCount=inputs['counts']['sourceOpportunityCount'],
        sourceEvents=inputs['counts']['sourceEvents'], memberEpisodes=inputs['counts']['memberEpisodes'],
        rawBreakouts=inputs['counts']['rawBreakouts'], preparedSchedules=12,
        accountScheduleBindings=24, newFills=0, heldCloseChecks=0, summaryIntervals=0)
    reports, dependency_pins = [], dict(DEPENDENCIES)
    data, data_key = None, None
    for row in rows:
        period, board, exit_name, policy = (row[k] for k in ('period', 'board', 'exit', 'policy'))
        group = next(g for g in groups if (g['period'], g['board']) == (period, board))
        folder = directory / period / board / exit_name
        require(set(row) == {'period', 'board', 'exit', 'policy', 'candidate', 'schedule', 'result', 'checkpoint', 'summary', 'reusedAccount'}
                and row['reusedAccount'] is False and row['candidate'] == policy, 'Every account is new with complete descriptor')
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
                'Every buy belongs to the exact admitted A-formed, T-close schedule; next-session, lots, price limits and volume caps audited',
                'Historical commission integer 5 identity retained; no account reruns or baseline reuse'],
        holdoutClaim=False, certified=False, targetAchieved=False))


def self_test():
    import unittest
    spec = importlib.util.spec_from_file_location('_handoff_verifier_tests', ROOT / 'tests/test_leader_amount_handoff_verifier.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    require(result.wasSuccessful(), 'Independent synthetic checks failed')
    report = dict(passed=True, syntheticGroups=result.testsRun, realSourceReads=0, accountExecutions=0, verifierSha256=sha(__file__))
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('inputs', 'accounts'), default='accounts')
    parser.add_argument('--inputs', '--inputs-only', dest='inputs', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--out', type=Path, default=DEFAULT)
    args = parser.parse_args()
    if args.self_test: return self_test()
    directory = args.out.resolve(); mode = 'inputs' if args.inputs or args.mode == 'inputs' else 'accounts'
    try:
        return (verify_inputs if mode == 'inputs' else verify_accounts)(directory)
    except Exception as error:
        if directory.exists():
            failures = directory / 'validation-attempts'; failures.mkdir(exist_ok=True)
            filename = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json'
            with (failures / filename).open('x', encoding='utf-8') as stream:
                json.dump(dict(passed=False, at=datetime.now(timezone.utc).isoformat(), mode=mode,
                    verifierSha256=sha(__file__), errorType=type(error).__name__, message=str(error),
                    traceback=traceback.format_exc()), stream, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__': main()
