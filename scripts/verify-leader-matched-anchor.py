"""Independent matched-anchor inputs and saved-account audit.

No new production signal builder or account runner is imported. The original
formation and owner queue is inherited through its prior audit, while matching
and all new common-support calculations are independently reconstructed.
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
DEFAULT = ROOT / 'artifacts/strategy-search/leader-matched-anchor-v1'
SID = 'leaderMatchedAnchorV1'
POLICIES = ('control', 'realCross', 'pseudoCross')
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
EXITS = ('trail10', 'volatilityTrail')
PARENT_PATH = 'scripts/verify-leader-amount-handoff.py'
PARENT_SHA = '3958cd6fda465591c9bb221f5f1dc88e326b99bd7d51d164b39b44411dd70ec2'
PARENT_PLAN_SHA = '5a12ff5ec8fd001680bc651de81f941d09960e67a8ebe8c75cea25a049063d08'
PARENT_INPUT_PROOF_SHA = '29eaad5ce4e5d749ebada51e3fd7dddea90ba4b8bed9a741e3f47b196bb9b298'
PARENT_ACCOUNT_PROOF_SHA = '96cad0c048ab8f5c65ccb3efaf6fa21f94eb7396b376399bb296d90a729e82c7'


def load_parent():
    path = ROOT / PARENT_PATH
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != PARENT_SHA:
            raise AssertionError('Frozen independent parent helper changed')
    spec = importlib.util.spec_from_file_location('_matched_independent_handoff', path)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


H = load_parent()
P = H.P
require, finite, same, sha, digest, read, records, ref, bound = (
    getattr(H, key) for key in ('require', 'finite', 'same', 'sha', 'digest', 'read', 'records', 'ref', 'bound'))
DEPENDENCIES = {PARENT_PATH: PARENT_SHA, **H.DEPENDENCIES}
COMMON_STATES = ('matchUnavailable', 'initialUnknown', 'initialAbove', 'decisionUnknown', 'commonKnown')
MATCH_STATES = ('matched', 'anchorScaleUnknown', 'noEligibleCandidate', 'inheritedOther')
OVERLAP_STATES = ('both', 'realOnly', 'pseudoOnly', 'neither')
BASIS = {
    'population': 'every immutable batch43 original member Top5; no parent owner, rank, lock or first-breakout recomputation',
    'matching': 'one per formed parent(A,anchor), including no eventual opportunity; same cohort excluding anchor and all20 peers',
    'scale': 'math.fsum exact positive amount A-20..A-1 divided by20; frozen raw OHLCV/status validity; A excluded',
    'distance': 'abs(log(candidateMean20)-log(anchorMean20)); exact distance then symbol; no caliper or fallback',
    'common': 'matchUnavailable > initialUnknown > initialAbove > decisionUnknown > commonKnown; independent flags retained',
    'initial': 'member M3(A)<=both real and pseudo; original parent membership remains a separate inherited condition',
    'crossing': 'member M3(T-1)<=anchor and member M3(T)>anchor, within commonKnown only; original T and ranks unchanged',
    'evidence': 'M3 uses exact3 positive amount and valid OHLCV/status slots; recomputed inherited member/real evidence must equal parent',
    'interpretation': 'outside this frozen group, not proven unrelated; scale matching does not balance theme, strength, news or investor identity',
    'scope': 'raw surviving-cohort development history; all36 new accounts, no old control population or account reuse',
}


def historical_scale(source, calendar, at):
    """Twenty exact slots ending before A; A cannot affect selection."""
    evidence = H.amount_reference(source, calendar, at - 1, slots=20)
    mean = evidence['total'] / 20 if evidence['status'] == 'known' else None
    if mean is not None and (not finite(mean) or mean <= 0):
        mean = None
    return mean, evidence


def select_match(scales, anchor_symbol, members):
    """Independent exhaustive minimization, without an A/T quality fallback."""
    anchor = scales.get(anchor_symbol)
    excluded = set(members) | {anchor_symbol}
    candidates = []
    for symbol in sorted(scales):
        if symbol in excluded:
            continue
        scale = scales[symbol]
        distance = (abs(math.log(scale) - math.log(anchor))
                    if scale is not None and anchor is not None else None)
        candidates.append((symbol, scale, distance))
    if anchor is None:
        return 'anchorScaleUnknown', None, candidates
    eligible = [(distance, symbol) for symbol, scale, distance in candidates if distance is not None]
    if not eligible:
        return 'noEligibleCandidate', None, candidates
    return 'matched', min(eligible)[1], candidates


def common_reference(evidence, matched):
    """Independent truth table; preserve unknown flags behind earlier failures."""
    initial_unknown = [side for side in ('member', 'real', 'pseudo') if evidence['initial'][side] is None]
    decision_unknown = [(when, side) for when in ('previous', 'current') for side in ('member', 'real', 'pseudo')
                        if evidence[when][side] is None]
    above = [side for side in ('real', 'pseudo') if evidence['initial']['member'] is not None
             and evidence['initial'][side] is not None and evidence['initial']['member'] > evidence['initial'][side]]
    state = ('matchUnavailable' if not matched else 'initialUnknown' if initial_unknown else
             'initialAbove' if above else 'decisionUnknown' if decision_unknown else 'commonKnown')
    crossings = {}
    for side in ('real', 'pseudo'):
        values = [evidence[when][s] for when in ('previous', 'current') for s in ('member', side)]
        crossings[side] = (values[0] <= values[1] and values[2] > values[3]) if all(v is not None for v in values) else None
    allowed = {'control': state == 'commonKnown',
               'realCross': state == 'commonKnown' and crossings['real'] is True,
               'pseudoCross': state == 'commonKnown' and crossings['pseudo'] is True}
    return dict(state=state, initialUnknown=initial_unknown, decisionUnknown=decision_unknown,
                initialAbove=above, crossings=crossings, allowed=allowed)


def scale_reference(source, calendar, at):
    mean, evidence = historical_scale(source, calendar, at)
    reasons = evidence['reasons'][:]
    if not reasons and mean is None: reasons.append('nonfiniteOrUnderflowMeanAmount20')
    return dict(status='unknown' if reasons else 'known', meanAmount20=mean,
                sumAmount20=evidence['total'], reasons=reasons)


def unavailable_reference(calendar, at):
    dates = calendar[max(0, at - 2):at + 1]
    return dict(date=calendar[at], dates=dates, amounts=[None] * len(dates), total=None,
                status='unknown', reasons=['pseudoAnchorUnavailable'])


def count_states(rows, field, states):
    counts = Counter(row[field] for row in rows)
    return {state: counts[state] for state in states}


def reference_build(dataset, parent_formations, parent_opportunities, *, start, end, progress=None, on_match_evidence=None):
    indexed = H.G.source_reference(dataset, start, end)
    calendar, symbols, sources = indexed['calendar'], indexed['symbols'], indexed['sources']
    positions = {day: i for i, day in enumerate(calendar)}
    targets = [d for d in calendar if start <= d <= end]
    require(targets and isinstance(parent_formations, list) and isinstance(parent_opportunities, list), 'Ordered inherited populations')
    formations, identities, previous = [], set(), None
    for parent in parent_formations:
        day = parent['formationDate']
        if day > end: continue
        anchor, rank = parent['anchorSymbol'], parent['originalLeaderRank']
        require(start <= day and day in positions and anchor in sources and type(rank) is int and 1 <= rank <= 5,
                'Parent formation source, date and rank')
        key, identity = (day, rank, anchor), parent['parentEventId']
        require(identity == f'leaderLagFollowersV1:{day}:{anchor}' and identity not in identities
                and (previous is None or previous < key), 'Original formation identity and ordering')
        identities.add(identity); previous = key
        at = positions[day]
        require(at >= 10 and parent['peerFormationDate'] == calendar[at - 10], 'Original peer formation clock')
        status, members = parent['status'], parent['members']
        require(status in ('formed', 'locked', 'notFormed') and isinstance(members, list), 'Original formation disposition')
        require(status != 'formed' or (parent['groupKind'] == 'formed' and len(members) == 20), 'Complete original formed group')
        seen = set()
        for rank, member in enumerate(members, 1):
            symbol = member['symbol']
            require(symbol in sources and symbol != anchor and symbol not in seen and member['frozenMemberRank'] == rank,
                    'Original frozen member ordering')
            seen.add(symbol)
        formations.append(parent)
    matches, by_event, scale_day, scales = [], {}, None, {}
    for parent in formations:
        day, anchor = parent['formationDate'], parent['anchorSymbol']; at = positions[day]
        members = [m['symbol'] for m in parent['members']]
        row = dict(parentEventId=parent['parentEventId'], formationDate=day, peerFormationDate=parent['peerFormationDate'],
            anchorSymbol=anchor, originalLeaderRank=parent['originalLeaderRank'], parentStatus=parent['status'],
            groupSha256=parent['groupSha256'], memberSymbols=members, windowDates20=calendar[max(0, at - 20):at],
            matchStatus='inheritedOther', pseudoSymbol=None, anchorScale=None, pseudoScale=None, distance=None,
            candidateCount=0, eligibleCandidateCount=0, unknownCandidateCount=0, excludedCandidateCount=0,
            candidateEvidenceSha256=None)
        if parent['status'] == 'formed':
            if scale_day != day:
                scales = {s: scale_reference(sources[s], calendar, at) for s in symbols}; scale_day = day
            selection, selected, outside = select_match({s: scales[s]['meanAmount20'] for s in symbols}, anchor, members)
            outside_map = {s: (mean, distance) for s, mean, distance in outside}
            candidates = []
            for s in symbols:
                if s not in outside_map:
                    status, reason, mean, distance = 'excluded', 'excludedAnchor' if s == anchor else 'excludedPeer', None, None
                else:
                    mean, distance = outside_map[s]
                    status, reason = ('unknown', 'candidateScaleUnknown') if mean is None else ('eligible', None)
                candidates.append(dict(symbol=s, status=status, reason=reason, meanAmount20=mean, distance=distance))
            counter = Counter(c['status'] for c in candidates)
            row.update(matchStatus=selection, pseudoSymbol=selected, anchorScale=deepcopy(scales[anchor]),
                pseudoScale=deepcopy(scales[selected]) if selected else None,
                distance=outside_map[selected][1] if selected else None, candidateCount=len(candidates),
                eligibleCandidateCount=counter['eligible'], unknownCandidateCount=counter['unknown'],
                excludedCandidateCount=counter['excluded'], candidateEvidenceSha256=digest(candidates))
            if on_match_evidence:
                on_match_evidence(dict(parentEventId=row['parentEventId'], formationDate=day, anchorSymbol=anchor,
                    windowDates20=row['windowDates20'], anchorScale=deepcopy(row['anchorScale']), candidates=candidates,
                    candidatesSha256=row['candidateEvidenceSha256'], selectedSymbol=selected, status=selection))
        matches.append(row); by_event[row['parentEventId']] = row
        if progress and (len(matches) % 20 == 0 or len(matches) == len(formations)):
            progress(dict(checkedFormations=len(matches), parentFormations=len(formations)))
    opportunities, schedules, identities, slots, members_by_day, previous = [], {p: [] for p in POLICIES}, set(), set(), set(), None
    for parent in parent_opportunities:
        day = parent['decisionDate']
        if day > end: continue
        member, anchor, formation, rank = parent['memberSymbol'], parent['anchorSymbol'], parent['formationDate'], parent['originalRank']
        old_id = parent['signalId']; key = (day, rank, member, old_id)
        require(day in positions and start <= day and type(rank) is int and 1 <= rank <= 5
                and member in sources and anchor in sources, 'Parent original Top5 source and clock')
        require(old_id == f'leaderAmountHandoffV1:{formation}:{anchor}:{member}' and old_id not in identities
                and (day, rank) not in slots and (day, member) not in members_by_day
                and (previous is None or previous < key), 'Original Top5 identity/rank/order')
        identities.add(old_id); slots.add((day, rank)); members_by_day.add((day, member)); previous = key
        match = by_event.get(parent['parentEventId'])
        require(match is not None and match['parentStatus'] == 'formed' and match['formationDate'] == formation
                and match['anchorSymbol'] == anchor and member in match['memberSymbols']
                and match['peerFormationDate'] == parent['peerFormationDate']
                and match['originalLeaderRank'] == parent['originalLeaderRank']
                and match['memberSymbols'].index(member) + 1 == parent['frozenMemberRank']
                and parent['signalDate'] == day and 1 <= positions[day] - positions[formation] <= 5,
                'Original selected first breakout belongs to immutable formation')
        pseudo, values = match['pseudoSymbol'], {}
        for clock, at in [('initial', positions[formation]), ('previous', positions[day] - 1), ('current', positions[day])]:
            values[clock] = dict(date=calendar[at], member=H.amount_reference(sources[member], calendar, at),
                real=H.amount_reference(sources[anchor], calendar, at),
                pseudo=H.amount_reference(sources[pseudo], calendar, at) if pseudo else unavailable_reference(calendar, at))
            inherited = parent['initialAmountEvidence'] if clock == 'initial' else parent['amountEvidence'][clock]
            require(inherited['date'] == values[clock]['date'] and inherited['member'] == values[clock]['member']
                    and inherited['anchor'] == values[clock]['real'], 'Recomputed inherited M3 exact equality')
        numeric = {clock: {s: values[clock][s]['total'] for s in ('member', 'real', 'pseudo')} for clock in values}
        assessed = common_reference(numeric, match['matchStatus'] == 'matched')
        status, crossing = assessed['state'], assessed['crossings']
        overlap = (('both' if crossing['pseudo'] else 'realOnly') if crossing['real'] else
                   ('pseudoOnly' if crossing['pseudo'] else 'neither')) if status == 'commonKnown' else None
        identity = f'{SID}:{formation}:{anchor}:{member}'
        row = dict(signalId=identity, formationId=identity, parentOpportunityId=old_id, parentEventId=parent['parentEventId'],
            formationDate=formation, peerFormationDate=parent['peerFormationDate'], decisionDate=day, signalDate=day,
            symbol=member, memberSymbol=member, anchorSymbol=anchor, pseudoSymbol=pseudo,
            originalLeaderRank=parent['originalLeaderRank'], frozenMemberRank=parent['frozenMemberRank'], originalRank=rank,
            priceEvidence=deepcopy(parent['priceEvidence']), parentInitialAmountEvidenceSha256=digest(parent['initialAmountEvidence']),
            parentAmountEvidenceSha256=digest(parent['amountEvidence']), matchStatus=match['matchStatus'], amountEvidence=values,
            commonStatus=status, flags=dict(matchUnavailable=match['matchStatus'] != 'matched',
                initialUnknownReasons=[s + ':' + reason for s in ('member', 'real', 'pseudo') for reason in values['initial'][s]['reasons']],
                initialAboveAnchors=assessed['initialAbove'],
                decisionUnknownReasons=[clock + ':' + s + ':' + reason for clock in ('previous', 'current')
                                        for s in ('member', 'real', 'pseudo') for reason in values[clock][s]['reasons']]),
            crossing=crossing, overlap=overlap,
            diagnostics=dict(prior20ScaleRatio=H.ratio_reference(match['pseudoScale']['meanAmount20'], match['anchorScale']['meanAmount20']) if pseudo else None,
                matchDistance=match['distance'], initialPseudoRealM3Ratio=H.ratio_reference(numeric['initial']['pseudo'], numeric['initial']['real']),
                initialMemberRealM3Ratio=H.ratio_reference(numeric['initial']['member'], numeric['initial']['real']),
                initialMemberPseudoM3Ratio=H.ratio_reference(numeric['initial']['member'], numeric['initial']['pseudo'])), signals={})
        for policy in POLICIES:
            row[policy] = assessed['allowed'][policy] if status == 'commonKnown' else False if status == 'initialAbove' else None
            signal = dict(signalId=identity, strategyId=SID, symbol=member, signalDate=day, originalRank=rank) if row[policy] is True else None
            row['signals'][policy] = signal
            if signal is not None: schedules[policy].append(deepcopy(signal))
        opportunities.append(row)
    daily = []
    for day in targets:
        ms = [r for r in matches if r['formationDate'] == day]
        os = [r for r in opportunities if r['decisionDate'] == day]
        daily.append(dict(date=day, parentFormations=len(ms), matchStatusCounts=count_states(ms, 'matchStatus', MATCH_STATES),
            originalTop5=len(os), commonStatusCounts=count_states(os, 'commonStatus', COMMON_STATES),
            overlapCounts=count_states(os, 'overlap', OVERLAP_STATES),
            signals={p: sum(r['signals'][p] is not None for r in os) for p in POLICIES}))
    mc = count_states(matches, 'matchStatus', MATCH_STATES)
    distinct = len({r['pseudoSymbol'] for r in matches if r['pseudoSymbol'] is not None})
    counts = dict(sourceSymbols=len(symbols), targetDays=len(targets), parentFormations=len(matches),
        formed=len(matches) - mc['inheritedOther'], **mc, originalTop5=len(opportunities), opportunities=len(opportunities),
        commonStatusCounts=count_states(opportunities, 'commonStatus', COMMON_STATES),
        overlapCounts=count_states(opportunities, 'overlap', OVERLAP_STATES), distinctPseudoAnchors=distinct,
        reusedPseudoAssignments=mc['matched'] - distinct, signals={p: len(schedules[p]) for p in POLICIES},
        notScheduled={p: len(opportunities) - len(schedules[p]) for p in POLICIES})
    return dict(version='leader-matched-anchor-v1', strategyId=SID, modes=list(POLICIES), calendar=calendar,
        targetDates=targets, basis=deepcopy(BASIS), counts=counts, dailyRows=daily, matches=matches,
        opportunities=opportunities, schedules=schedules, certified=False, targetAchieved=False)


def account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas):
    H.SID = SID
    return H.account_reference(result, checkpoint, summary, schedule, config, dataset, period, exit_name, atlas)


def source_bindings_reference(plan):
    answer = H.G.source_bindings_reference(plan)
    require(plan['parentPlan']['sha256'] == PARENT_PLAN_SHA
            and plan['parentInputValidation']['sha256'] == PARENT_INPUT_PROOF_SHA
            and plan['parentValidation']['sha256'] == PARENT_ACCOUNT_PROOF_SHA, 'Exact completed parent proofs')
    parent, complete, proof, accounts, comparison, delivery = [read(bound(plan[k])) for k in
        ('parentPlan', 'parentInputCompletion', 'parentInputValidation', 'parentValidation', 'parentComparison', 'previousDelivery')]
    require(proof['passed'] is True and accounts['passed'] is True
            and proof['planSha256'] == accounts['planSha256'] == complete['planSha256'] == PARENT_PLAN_SHA
            and proof['inputCompletionSha256'] == accounts['inputCompletionSha256'] == plan['parentInputCompletion']['sha256']
            and accounts['inputValidationSha256'] == plan['parentInputValidation']['sha256']
            and accounts['comparisonSha256'] == plan['parentComparison']['sha256'], 'Parent source and account receipt chain')
    same(accounts['accountArtifacts'], comparison['rows'], 'Parent context rows have exact audited account artifacts')
    inventory = {r['path']: r['sha256'] for r in delivery['files']}
    pins = {}
    for key in ('parentPlan', 'parentInputCompletion', 'parentInputValidation', 'parentValidation', 'parentComparison'):
        require(inventory.get(plan[key]['path']) == plan[key]['sha256'], 'Final parent delivery includes ' + key)
        pins[plan[key]['path']] = plan[key]['sha256']
    expected_context = [dict(period=r['period'], board=r['board'], exit=r['exit'], summary=r['summary'])
                        for r in comparison['rows'] if r['policy'] == 'control']
    same(plan['parentControlSummaries'], expected_context, 'All twelve parent control summaries are context only')
    require(len(expected_context) == 12, 'Complete original control context')
    for row in expected_context: bound(row['summary']); pins[row['summary']['path']] = row['summary']['sha256']
    require(list(plan['parentData']) == list(PERIODS), 'Both original periods')
    for period in PERIODS:
        require(list(plan['parentData'][period]) == list(BOARDS), 'All original boards')
        for board in BOARDS:
            selected = [g for g in complete['groups'] if (g['period'], g['board']) == (period, board)]
            verified = [g for g in proof['preparedGroups'] if (g['period'], g['board']) == (period, board)]
            require(len(selected) == len(verified) == 1, 'Unique parent group')
            group, inherited, source = selected[0], plan['parentData'][period][board], plan['sourceData'][period][board]
            same(group, verified[0], 'Entire parent formation and Top5 inputs covered by independent audit')
            same(source, parent['sourceData'][period][board], 'Parent raw source unchanged')
            require(group['accountData'] == source['accountData'] and group['accountDatasetDigest'] == source['accountDatasetDigest']
                    and proof['datasetPins'][source['accountData']['path']] == source['accountData']['sha256'], 'Parent dataset exact identity')
            expected = dict(inputCompletion=group['inputReceipt'], formations=group['formations'], opportunities=group['opportunities'],
                sourceFormationCount=group['counts']['sourceEvents'], sourceOpportunityCount=group['counts']['opportunities'])
            same(inherited, expected, 'All parent formations and all original Top5 retained')
            receipt = read(bound(inherited['inputCompletion']))
            require(receipt['planSha256'] == PARENT_PLAN_SHA, 'Original source receipt plan')
            same({k: v for k, v in receipt.items() if k not in ('planSha256', 'completedAt')},
                 {k: v for k, v in group.items() if k != 'inputReceipt'}, 'Original source receipt complete closure')
            for key in ('inputCompletion', 'formations', 'opportunities'):
                bound(inherited[key]); pins[inherited[key]['path']] = inherited[key]['sha256']
    answer.update(parentInputPins=pins, parentInputValidationSha256=plan['parentInputValidation']['sha256'],
                  parentValidationSha256=plan['parentValidation']['sha256'])
    return answer


def registered_plan(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == 'leader-matched-anchor-v1' and plan['strategyId'] == SID
            and plan['boards'] == list(BOARDS) and plan['exits'] == list(EXITS)
            and plan['policies'] == list(POLICIES) and list(plan['periods']) == list(PERIODS), 'Registered source/policy matrix')
    expected_trials = dict(newSetupMechanisms=1, newEntryPolicies=3, newAccounts=36, referenceAccountsReused=0, newUniverseVariants=0)
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
    require(plan['previousDelivery']['sha256'] == '9e59cfff71905056f90e8f3785f271fa211d5cd03fd275b1b76dbf3aa51c217c',
            'Completed predecessor identity')
    parent = read(bound(plan['parentPlan']))
    proof, delivery = read(bound(plan['parentValidation'])), read(bound(plan['previousDelivery']))
    require(proof['passed'] is True and proof['planSha256'] == plan['parentPlan']['sha256']
            and delivery['candidateTrialsAfter'] == 43, 'Previously completed parent evidence')
    inventory = {r['path']: r['sha256'] for r in delivery['files']}
    for key in ('parentPlan', 'parentValidation'):
        require(inventory.get(plan[key]['path']) == plan[key]['sha256'], 'Parent final delivery binds ' + key)
    require(plan['previousProgram']['sha256'] == '0b509b45e82230f7cb41ef544bae67fd0cf605df7439a4d9859fd77ef930073d'
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
        return SID if value == 'leaderAmountHandoffV1' else deepcopy(value)
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
    require(completion['schema'] == 'leader-matched-anchor-v1-inputs'
            and completion['planSha256'] == sha(directory / 'plan.json')
            and completion['preAccountTestsSha256'] == sha(directory / 'pre-account-tests.json')
            and completion['allSchedulesFrozenBeforeAccounts'] is True and completion['newAccountsExecuted'] == 0,
            'Complete pre-account input freeze')
    groups = completion['groups']
    require([(g['period'], g['board']) for g in groups] == [(p, b) for p in PERIODS for b in BOARDS], 'All six input sources in order')
    names = dict(metadata='metadata.json', daily='daily-evidence.jsonl.gz', matchEvidence='match-evidence.jsonl.gz',
        matches='matches.jsonl.gz', opportunities='opportunities.jsonl.gz', inputReceipt='input-completion.json')
    for group in groups:
        period, board = group['period'], group['board']; folder = directory / period / board
        source, parent = plan['sourceData'][period][board], plan['parentData'][period][board]
        require(group['accountData'] == source['accountData'] and group['accountDatasetDigest'] == source['accountDatasetDigest'],
                'Prepared original raw source')
        bound(group['accountData'])
        for key, parent_key in [('parentFormations', 'formations'), ('parentOpportunities', 'opportunities'), ('parentInputReceipt', 'inputCompletion')]:
            same(group[key], parent[parent_key], 'Original parent input reference ' + key); bound(group[key])
        for key, filename in names.items(): require(bound(group[key]) == folder / filename, 'Exact prepared input path ' + key)
        require(list(group['schedules']) == list(POLICIES), 'All three common-subset schedules')
        for policy in POLICIES: require(bound(group['schedules'][policy]) == folder / (policy + '.signals.json'), 'Exact policy schedule path')
        receipt = read(bound(group['inputReceipt']))
        require(receipt['planSha256'] == sha(directory / 'plan.json'), 'Per-source plan identity')
        same({k: v for k, v in receipt.items() if k not in ('planSha256', 'completedAt')},
             {k: v for k, v in group.items() if k != 'inputReceipt'}, 'Exact per-source input closure')
        require(datetime.fromisoformat(plan['registeredAt']) <= datetime.fromisoformat(receipt['completedAt'])
                <= datetime.fromisoformat(completion['completedAt']), 'Preparation after registration')
    expected = dict(sourceGroups=6, preparedSchedules=18, accountScheduleBindings=36,
        parentFormations=sum(g['counts']['parentFormations'] for g in groups), matched=sum(g['counts']['matched'] for g in groups),
        commonKnown=sum(g['counts']['commonStatusCounts']['commonKnown'] for g in groups),
        opportunities=sum(g['counts']['opportunities'] for g in groups),
        signals={p: sum(g['counts']['signals'][p] for g in groups) for p in POLICIES})
    same(completion['counts'], expected, 'Complete input counters')
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
    counts = dict(sourceGroups=0, sourceDays=0, parentFormations=0, formed=0, matched=0, candidateEvidenceRows=0,
        sourceOpportunityCount=0, preparedSchedules=0, accountScheduleBindings=0,
        commonStatusCounts={s: 0 for s in COMMON_STATES}, signals={p: 0 for p in POLICIES}, accountsExecuted=0)
    reports = []
    for group in groups:
        period, board = group['period'], group['board']; inherited = plan['parentData'][period][board]
        data = read(bound(group['accountData']))
        require(digest(data) == group['accountDatasetDigest'] and data['period'] == period and data['board'] == board
                and len(data['securities']) == plan['sourceData'][period][board]['members'], 'Original raw cache content')
        forms, opps = records(bound(group['parentFormations'])), records(bound(group['parentOpportunities']))
        require(len(forms) == inherited['sourceFormationCount'] and len(opps) == inherited['sourceOpportunityCount'], 'Full parent denominators')
        evidence = iter_records(bound(group['matchEvidence'])); evidence_count = 0; candidates = 0
        def compare_evidence(expected):
            nonlocal evidence_count, candidates
            actual = next(evidence, None)
            require(actual is not None, 'Missing complete candidate evidence')
            same(actual, expected, 'Independent complete matching pool and exact nearest selection')
            require(digest(actual['candidates']) == expected['candidatesSha256'], 'Exact canonical candidate evidence')
            evidence_count += 1; candidates += len(expected['candidates'])
        spec = plan['periods'][period]
        expected = reference_build(data, forms, opps, start=spec['startDate'], end=spec['endDate'], on_match_evidence=compare_evidence)
        require(next(evidence, None) is None and evidence_count == expected['counts']['formed'], 'Every formed event matched once, exact EOF')
        for key, field in [('daily', 'dailyRows'), ('matches', 'matches'), ('opportunities', 'opportunities')]:
            same(records(bound(group[key])), expected[field], 'Complete independently reconstructed ' + field)
        same(read(bound(group['metadata'])), {k: v for k, v in expected.items() if k not in ('dailyRows', 'matches', 'opportunities', 'schedules')},
             'Full metadata, basis and counters')
        c = expected['counts']; same(group['counts'], c, 'Input receipt independent counters')
        require(c['parentFormations'] == c['formed'] + c['inheritedOther'] == len(forms)
                and c['formed'] == c['matched'] + c['anchorScaleUnknown'] + c['noEligibleCandidate']
                and c['originalTop5'] == c['opportunities'] == sum(c['commonStatusCounts'].values()) == len(opps)
                and c['commonStatusCounts']['commonKnown'] == sum(c['overlapCounts'].values()), 'All source populations conserve')
        require(c['signals']['control'] == c['commonStatusCounts']['commonKnown']
                and c['signals']['realCross'] == c['overlapCounts']['both'] + c['overlapCounts']['realOnly']
                and c['signals']['pseudoCross'] == c['overlapCounts']['both'] + c['overlapCounts']['pseudoOnly'], 'All masks reconcile to overlap table')
        require([r['parentOpportunityId'] for r in expected['opportunities']] == [r['signalId'] for r in opps], 'Original Top5 order and identity retained')
        for policy in POLICIES:
            schedule = read(bound(group['schedules'][policy]))
            require(schedule == expected['schedules'][policy]
                    and schedule == sorted(schedule, key=lambda s: (s['signalDate'], s['originalRank'], s['symbol'], s['signalId']))
                    and len({s['signalId'] for s in schedule}) == len(schedule), 'Exact identity, clock, rank and schedule ordering')
            require(len(schedule) + c['notScheduled'][policy] == len(opps), 'Rejected and unknown slots retained')
            counts['signals'][policy] += len(schedule)
        require(digest(data) == group['accountDatasetDigest'], 'Audit preserves original source')
        counts['sourceGroups'] += 1; counts['sourceDays'] += c['targetDays']; counts['preparedSchedules'] += 3
        counts['accountScheduleBindings'] += 6; counts['candidateEvidenceRows'] += candidates
        for key in ('parentFormations', 'formed', 'matched'): counts[key] += c[key]
        counts['sourceOpportunityCount'] += len(opps)
        for state in COMMON_STATES: counts['commonStatusCounts'][state] += c['commonStatusCounts'][state]
        reports.append(dict(period=period, board=board, counts=c, sourceBinding=group['accountData'],
            parentFormations=group['parentFormations'], parentOpportunities=group['parentOpportunities'],
            matchEvidenceCount=evidence_count, candidateEvidenceRows=candidates, sourceOpportunityCount=len(opps)))
        print(json.dumps(dict(checkedSource=[period, board], opportunities=len(opps), matched=c['matched'], passed=True)), flush=True)
        del data, expected, forms, opps; gc.collect()
    require((counts['sourceGroups'], counts['preparedSchedules'], counts['accountScheduleBindings']) == (6, 18, 36)
            and counts['sourceOpportunityCount'] == completion['counts']['opportunities'], 'Complete independently audited input matrix')
    registered_plan(directory)
    return P.write_proof(directory, 'input-validation.json', dict(passed=True, verifiedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=sha(directory / 'plan.json'), inputCompletionSha256=sha(directory / 'input-completion.json'),
        preAccountTestsSha256=sha(directory / 'pre-account-tests.json'), verifierSha256=sha(__file__), sourcePins=DEPENDENCIES,
        inputPins=plan['inputPins'], preparedGroups=groups, counts=counts, groups=reports, **source_proofs,
        checks=['All original formations and member Top5 inherited through exact completed parent input proofs',
                'All same-board candidates independently assessed on twenty exact pre-A slots; exact log distance and lexical tie selection',
                'One immutable pseudo anchor per formed event, no A or later quality fallback and no outcome selection',
                'Every member/real/pseudo M3 at A and T-1/T recomputed; inherited member/real evidence exact',
                'Exclusive common status, independent reasons, crossing overlap, original slots and all eighteen schedules reconcile',
                'Thirty-six account bindings frozen before account execution; old control summaries are descriptive context only'],
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
    require(inputs['counts']['sourceGroups'] == 6 and inputs['counts']['preparedSchedules'] == 18
            and inputs['counts']['accountScheduleBindings'] == 36
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
    require(comparison['schema'] == 'leader-matched-anchor-v1-comparison'
            and all(comparison[k] is False for k in ('holdoutClaim', 'certified', 'targetAchieved')), 'Comparison scope and claims')
    for key, value in hashes.items(): require(comparison[key] == value, 'Comparison dependency ' + key)
    for key, value in plan['rules']['trialAccounting'].items(): require(comparison[key] == value, 'Comparison trial scope ' + key)
    rows = comparison['rows']
    expected_keys = [(g['period'], g['board'], e, p) for g in groups for e in EXITS for p in POLICIES]
    require([tuple(r[k] for k in ('period', 'board', 'exit', 'policy')) for r in rows] == expected_keys,
            'All 36 new accounts, exact policy/exit/source ordering')
    require(len(comparison['groupCompletions']) == 12, 'All twelve terminal exit-group receipts')
    for (group, exit_name), item in zip([(g, e) for g in groups for e in EXITS], comparison['groupCompletions']):
        period, board = group['period'], group['board']
        folder = directory / period / board / exit_name
        require(bound(item) == folder / 'group-completion.json', 'Exact terminal group path')
        terminal = read(bound(item))
        require(tuple(terminal[k] for k in ('period', 'board', 'exit')) == (period, board, exit_name)
                and terminal['completed'] is True and terminal['newAccounts'] == 3
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
        parentFormations=inputs['counts']['parentFormations'], matched=inputs['counts']['matched'],
        preparedSchedules=18, accountScheduleBindings=36, newFills=0, heldCloseChecks=0, summaryIntervals=0)
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
    require((counts['accounts'], counts['newAccounts'], counts['reusedAccounts']) == (36, 36, 0), 'Actual audited account totals')
    registered_plan(directory)
    return P.write_proof(directory, 'validation.json', dict(passed=True, verifiedAt=datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in hashes.items() if key != 'runStartSha256'},
        comparisonSha256=sha(directory / 'comparison.json'), verifierSha256=sha(__file__),
        sourcePins=dependency_pins, inputPins=plan['inputPins'], runStart=ref(directory / 'run-start.json'),
        counts=counts, accounts=reports, accountArtifacts=rows, **source_proofs,
        checks=['All six source populations and eighteen independently rebuilt schedules retain exact hashes before all 36 accounts',
                'All new fee ledgers, cash/shares, full equity paths, closed/open episodes, terminal marks and summaries independently reconciled',
                'Every held close replays initial stop, fixed trail or volatility ratchet, unknown carry and exit priority without production risk functions',
                'Every buy belongs to the exact admitted A-formed, T-close schedule; next-session, lots, price limits and volume caps audited',
                'Historical commission integer 5 identity retained; no account reruns or baseline reuse'],
        holdoutClaim=False, certified=False, targetAchieved=False))


def self_test():
    import unittest
    spec = importlib.util.spec_from_file_location('_matched_verifier_tests', ROOT / 'tests/test_leader_matched_anchor_verifier.py')
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
