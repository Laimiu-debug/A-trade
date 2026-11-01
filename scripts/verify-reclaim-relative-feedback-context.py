#!/usr/bin/env python3
"""Independent audit for the frozen relative-feedback context diagnostic.

Only the earlier, independently implemented rank/raw-window reference is reused.
This script never imports the new context producer, runner, or its aggregation.
Real execution only reads frozen evidence; it does not execute any account.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/reclaim-relative-feedback-context-v1'
LAG_REFERENCE = 'scripts/verify-leader-lag-followers.py'
LAG_REFERENCE_SHA = '3e0244282a82701dcc942f532673c4e17493543cfa335f9e2459da6a7a33333c'
FEEDBACK_REFERENCE = 'scripts/verify-strength-feedback.py'
FEEDBACK_REFERENCE_SHA = '9569d82ddacff78fed2e92dcc2cc45814cf9a8be9bfef6d627ef531fdc38cb91'
CONTEXT_REFERENCE = 'scripts/verify-reclaim-context-diagnostic.py'
CONTEXT_REFERENCE_SHA = '9b899e4c84ca90d2d26be4ef3a2ae2910029fa726441e23bed7ccbf6f48c67c9'
VERSION = 'reclaim-relative-feedback-context-v1'
STATES = ('stronger', 'weaker', 'equal', 'unknown')
MEMBER_STATES = ('positive', 'flat', 'negative', 'unknown')
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')
SPLIT = '2024-09-24'
PARENT = ROOT / 'artifacts/strategy-search/reclaim-volatility-trail-v1'
TOP_PARENT = ROOT / 'artifacts/strategy-search/strength-feedback-v1'
PARENT_DELIVERY_SHA = '7568310306dfc2bb88b801311ea20e7840d15072d4b973f21e054ba2bf6c2ad9'
TRIALS = {'diagnosticBatches': 1, 'newStrategyTrials': 0, 'newAccounts': 0,
          'existingAccountsRead': 12, 'newEntryPolicies': 0, 'newExitDefinitions': 0, 'newContextDefinitions': 1}
EXPECTED_ROWS = {'overallRows': 48, 'formationYearRows': 96, 'epochStateRows': 72,
                 'comparisonRows': 24, 'accountReconciliation': 12, 'curveEpochRows': 12,
                 'contextDayCounts': 6, 'qualityRows': 12}
_LAG = None
_CONTEXT = None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def records(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def local(name):
    path = (ROOT / name).resolve()
    require(path.is_relative_to(ROOT), 'Evidence path outside repository')
    return path


def pinned(ref):
    require(isinstance(ref, dict) and isinstance(ref.get('path'), str)
            and isinstance(ref.get('sha256'), str), 'Malformed evidence reference')
    path = local(ref['path'])
    require(path.is_file() and sha(path) == ref['sha256'], 'Evidence pin changed: ' + ref['path'])
    if 'bytes' in ref:
        require(path.stat().st_size == ref['bytes'], 'Evidence byte size changed: ' + ref['path'])
    return path


def reference(path, expected):
    source = local(path)
    require(sha(source) == expected, 'Independent reference changed: ' + path)
    spec = importlib.util.spec_from_file_location('_relative_context_' + source.stem.replace('-', '_'), source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lag_reference():
    global _LAG
    if _LAG is None:
        _LAG = reference(LAG_REFERENCE, LAG_REFERENCE_SHA)
    return _LAG


def context_reference():
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = reference(CONTEXT_REFERENCE, CONTEXT_REFERENCE_SHA)
    return _CONTEXT


def same(actual, expected, label='value'):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + ': keys differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': list length differs')
        for index, (got, want) in enumerate(zip(actual, expected)):
            same(got, want, label + '[' + str(index) + ']')
    elif type(expected) is float:
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-9), label + ': numeric difference')
    else:
        require(type(actual) is type(expected) and actual == expected, label + ': value differs')


def difference_reference(top_counts, bottom_counts):
    """Use exact integer numerators; unknown feedback never shrinks either N."""
    for counts in (top_counts, bottom_counts):
        require(set(counts) == set(MEMBER_STATES) and all(type(v) is int and v >= 0 for v in counts.values()),
                'Invalid fixed-cohort four-state counts')
    ns, nw = sum(top_counts.values()), sum(bottom_counts.values())
    if not ns or not nw:
        return {'lowerNumerator': None, 'upperNumerator': None, 'denominator': 0,
                'lower': None, 'upper': None}, 'unknown'
    ps, us = top_counts['positive'], top_counts['unknown']
    pw, uw = bottom_counts['positive'], bottom_counts['unknown']
    lower, upper, denominator = ps * nw - (pw + uw) * ns, (ps + us) * nw - pw * ns, ns * nw
    state = ('stronger' if lower > 0 else 'weaker' if upper < 0 else 'equal'
             if lower == upper == 0 else 'unknown')
    return {'lowerNumerator': lower, 'upperNumerator': upper, 'denominator': denominator,
            'lower': lower / denominator, 'upper': upper / denominator}, state


def feedback_reference(item, as_of):
    issue = lag_reference().window_reason(item, as_of, 6)
    if issue:
        return None, 'unknown', 'stock:' + issue
    ratio = item['bars'][as_of]['close'] / item['bars'][as_of - 5]['close']
    if not finite(ratio) or ratio <= 0 or not finite(ratio - 1):
        return None, 'unknown', 'nonfiniteOrUnderflowFeedbackRatio'
    value = ratio - 1
    return value, 'positive' if value > 0 else 'negative' if value < 0 else 'flat', None


def cohort_reference(symbols, values, ranks, indexed, as_of, side):
    selected = [s for s in symbols if s in ranks and (ranks[s] >= .90 if side == 'top' else ranks[s] <= .10)]
    members, counts = [], {status: 0 for status in MEMBER_STATES}
    for symbol in selected:
        value, status, reason = feedback_reference(indexed[symbol], as_of)
        members.append({'symbol': symbol, 'relativeReturn20': values[symbol], 'percentile': ranks[symbol],
                        'returnSinceCohort': value, 'status': status, 'reason': reason})
        counts[status] += 1
    n = len(members)
    return {'cohortSize': n, 'feedbackCounts': counts,
            'positiveFractionLower': counts['positive'] / n if n else None,
            'positiveFractionUpper': (counts['positive'] + counts['unknown']) / n if n else None,
            'memberDigest': digest(members)}, members


def reference_build(dataset, start, end, on_members=None, progress=None):
    """Recompute every formation T using A=T-1 and F=T-6, never T prices."""
    old = lag_reference()
    source = old.source_reference(dataset, start, end)
    calendar, symbols, indexed = source['calendar'], source['symbols'], source['indexed']
    positions = {day: index for index, day in enumerate(calendar)}
    daily = []
    for day in source['targetDates']:
        formation_at = positions[day]
        as_of, frozen_at = formation_at - 1, formation_at - 6
        as_of_day = calendar[as_of] if as_of >= 0 else None
        frozen_day = calendar[frozen_at] if frozen_at >= 0 else None
        values, reasons = {}, Counter()
        for symbol in symbols:
            if frozen_at < 0:
                value, reason = None, 'insufficientCohortCalendarHistory'
            else:
                value = old.relative_value(indexed[symbol], indexed['__benchmark__'], frozen_at, 20)
                reason = old.relative_reason(indexed[symbol], indexed['__benchmark__'], frozen_at, 20)
            values[symbol] = value
            if reason is not None:
                reasons[reason] += 1
        ranks, population_count = old.rank_reference(values)
        population = {'count': population_count, 'sha256': digest([
            {'symbol': symbol, 'value': values[symbol], 'percentile': ranks[symbol]}
            for symbol in symbols if symbol in ranks])}
        top, top_members = cohort_reference(symbols, values, ranks, indexed, as_of, 'top')
        bottom, bottom_members = cohort_reference(symbols, values, ranks, indexed, as_of, 'bottom')
        difference, state = difference_reference(top['feedbackCounts'], bottom['feedbackCounts'])
        reason = ('asOfSessionUnavailable' if as_of < 0 else 'insufficientCohortCalendarHistory'
                  if frozen_at < 0 else 'emptyRankPopulation' if population_count == 0
                  else 'emptyFrozenCohorts' if not top_members and not bottom_members
                  else 'emptyTopCohort' if not top_members else 'emptyBottomCohort' if not bottom_members
                  else 'confirmedStrongerFeedback' if state == 'stronger' else 'confirmedWeakerFeedback'
                  if state == 'weaker' else 'confirmedEqualFeedback' if state == 'equal' else 'ambiguousFeedbackDifference')
        member_digest = digest({'topMembers': top_members, 'bottomMembers': bottom_members})
        row = {'formationDate': day, 'asOfDate': as_of_day, 'cohortDate': frozen_day,
               'rankWindowDates': calendar[max(0, frozen_at - 20):frozen_at + 1] if frozen_at >= 0 else [],
               'feedbackWindowDates': calendar[max(0, frozen_at):as_of + 1] if as_of >= 0 else [],
               'rankPopulation': population, 'rankReasonCounts': dict(sorted(reasons.items())),
               'top': top, 'bottom': bottom, 'difference': difference, 'state': state, 'reason': reason,
               'memberDigest': member_digest}
        daily.append(row)
        if on_members is not None:
            on_members(deepcopy({'formationDate': day, 'asOfDate': as_of_day, 'cohortDate': frozen_day,
                'rankPopulation': population, 'topMembers': top_members, 'bottomMembers': bottom_members,
                'memberDigest': member_digest}))
        if progress is not None and len(daily) % 50 == 0:
            progress({'phase': 'independent-relative-feedback', 'date': day, 'completedDays': len(daily)})
    totals = {side: {status: sum(row[side]['feedbackCounts'][status] for row in daily)
                     for status in MEMBER_STATES} for side in ('top', 'bottom')}
    return {'version': VERSION, 'calendar': calendar, 'targetDates': source['targetDates'], 'dailyRows': daily,
        'counts': {'sampleCount': len(symbols), 'targetDayCount': len(daily),
            'dayStates': {state: sum(row['state'] == state for row in daily) for state in STATES},
            'cohortMemberObservations': {side: sum(values.values()) for side, values in totals.items()},
            'feedbackCounts': totals},
        'basis': {'helperModule': 'service/research_leader_lag.py',
            'helperSha256': '1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e',
            'benchmark': 'sh000300', 'priceBasisId': 'raw-unadjusted', 'asOfLag': 1,
            'rankHorizon': 20, 'feedbackLag': 5, 'topPercentile': .90, 'bottomPercentile': .10,
            'rankDefinition': '(stockF/stockFminus20)/(benchmarkF/benchmarkFminus20)-1',
            'midrankDefinition': '(zeroBasedFirst+exclusiveStop)/(2*validPopulationSize)',
            'feedbackDefinition': 'closeA/closeF-1; all six exact source slots must be valid',
            'clock': 'A=T-1; F=T-6; rank F-20..F; feedback F..A; current T excluded',
            'denominator': 'Each full cohort frozen at F, including unknown feedback; ties are not truncated',
            'differenceDefinition': 'top positive-fraction bounds minus bottom positive-fraction bounds; integer cross-products determine strict sign',
            'knownDecisionIsCompleteCohort': False,
            'historicalStatus': 'Absent status is unverified, not a tradability guarantee',
            'scope': 'Same-board fixed historical survivor cohort; descriptive context only, no signal, account, all-market or holdout claim'},
        'certified': False, 'targetAchieved': False}


def synthetic_fixture():
    data = lag_reference().synthetic_source()
    return data, data['calendar'][100], data['calendar'][109]


def record_reference(period, board, policy, formation, context, opportunity, buy, closed, opened):
    """Reconstruct identity and PnL from saved fills/episodes, not summary labels."""
    adapted = {**formation, 'signals': {policy: formation['signals']['immediate']}}
    row = context_reference().independent_record(period, policy, adapted, context['state'], opportunity, buy, closed, opened)
    for name in ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount'):
        row[name] = float(row[name])
    return {**row, 'board': board, 'asOfDate': context['asOfDate'], 'cohortDate': context['cohortDate'],
            'contextMemberDigest': context['memberDigest'],
            'positionState': 'closed' if closed else 'openAtCutoff' if opened else 'notEntered'}


def aggregate_group(rows):
    completed = [row for row in rows if row['closed']]
    open_rows = [row for row in rows if row['open']]
    profits = [row for row in completed if row['closedPnlAmount'] > 0]
    losses = [row for row in completed if row['closedPnlAmount'] < 0]
    cash = {name: math.fsum(row[name] for row in rows) for name in
            ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount')}
    sum_profit = math.fsum(row['closedPnlAmount'] for row in profits)
    sum_loss = -math.fsum(row['closedPnlAmount'] for row in losses)
    returns = [row['closedReturnPct'] for row in completed]
    holding = [row['holdingSessions'] for row in completed]
    avg = lambda values: math.fsum(values) / len(values) if values else None
    total = math.fsum(cash.values())
    return {'formationCount': len(rows), 'entered': sum(row['entered'] for row in rows),
        'closed': len(completed), 'open': len(open_rows), 'notEntered': sum(not row['entered'] for row in rows),
        'uniqueFormationDays': len({row['formationDate'] for row in rows}), 'uniqueSymbols': len({row['symbol'] for row in rows}),
        'wins': len(profits), 'losses': len(losses), 'zeros': len(completed) - len(profits) - len(losses),
        'openMarkStaleCount': sum(row['openMarkStale'] for row in open_rows),
        'dispositionCounts': dict(sorted(Counter(row['positionState'] for row in rows).items())),
        'reasonCounts': dict(sorted(Counter(row['reason'] for row in rows).items())),
        **cash, 'grossProfitAmount': sum_profit, 'grossLossAmount': sum_loss, 'totalPnlAmount': total,
        'closedContributionPpOfInitialCapital': cash['closedPnlAmount'] / 500000 * 100,
        'totalContributionPpOfInitialCapital': total / 500000 * 100,
        'closedMeanPct': avg(returns), 'closedMedianPct': median(returns) if returns else None,
        'winRatePct': len(profits) / len(completed) * 100 if completed else None,
        'meanWinPct': avg([row['closedReturnPct'] for row in profits]),
        'meanLossPct': avg([row['closedReturnPct'] for row in losses]),
        'profitFactor': sum_profit / sum_loss if sum_loss else None,
        'bestClosedPct': max(returns) if returns else None, 'worstClosedPct': min(returns) if returns else None,
        'meanHoldingSessions': avg(holding), 'medianHoldingSessions': median(holding) if holding else None,
        'top5ShareOfGrossProfitPct': math.fsum(sorted((row['closedPnlAmount'] for row in profits), reverse=True)[:5]) / sum_profit * 100
            if sum_profit else None}


def aggregate_reference(rows):
    """Return all four states, including empty cells, in each fixed account."""
    ctx = context_reference()
    identities = [(r['period'], r['board'], r['policy'], r['formationId']) for r in rows]
    require(len(identities) == len(set(identities)), 'Duplicate formation/account record')
    overall, yearly, epochs = [], [], []
    for period in PERIODS:
        years = period.split('-')
        period_epochs = ('oldPeriod',) if period == '2022-2023' else ('before20240924', 'from20240924')
        for board in BOARDS:
            for policy in POLICIES:
                account_rows = [r for r in rows if (r['period'], r['board'], r['policy']) == (period, board, policy)]
                ctx.check_records(account_rows, STATES)
                for state in STATES:
                    chosen = [r for r in account_rows if r['state'] == state]
                    identity = {'period': period, 'board': board, 'policy': policy, 'state': state}
                    overall.append({**identity, **aggregate_group(chosen)})
                    for year in years:
                        yearly.append({**identity, 'formationYear': year,
                            **aggregate_group([r for r in chosen if r['formationDate'][:4] == year])})
                    for epoch in period_epochs:
                        selected = [r for r in chosen if (epoch == 'oldPeriod' or
                            (r['formationDate'] < SPLIT if epoch == 'before20240924' else r['formationDate'] >= SPLIT))]
                        epochs.append({**identity, 'formationEpoch': epoch, **aggregate_group(selected)})
    return {'overallRows': overall, 'formationYearRows': yearly, 'epochStateRows': epochs}


def evidence_ref(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, 'Timestamp lacks timezone')
    return parsed


def registered_plan(directory):
    plan = read(directory / 'plan.json')
    require(plan['schema'] == VERSION, 'Wrong diagnostic plan')
    same(plan['trialAccounting'], TRIALS, 'Zero account/signal scope')
    same(plan['boards'], list(BOARDS), 'All original boards')
    same(plan['policies'], list(POLICIES), 'Both frozen exit policies')
    same(plan['states'], list(STATES), 'All context states')
    same(plan['expectedRows'], EXPECTED_ROWS, 'Complete diagnostic matrices')
    require(plan['expectedFormationCount'] == 8155 and plan['expectedRecordCount'] == 16310,
            'Original opportunity population changed')
    for mapping in (plan['sourcePins'], plan['inputPins']):
        for name, expected in mapping.items():
            pinned({'path': name, 'sha256': expected})
    require(plan['sourcePins'].get(Path(__file__).resolve().relative_to(ROOT).as_posix()) == sha(__file__),
            'This verifier is not the registered independent source')
    for name, expected in ((LAG_REFERENCE, LAG_REFERENCE_SHA), (FEEDBACK_REFERENCE, FEEDBACK_REFERENCE_SHA),
                           (CONTEXT_REFERENCE, CONTEXT_REFERENCE_SHA)):
        require(plan['sourcePins'].get(name) == expected, 'Missing independent helper pin: ' + name)
    protocol = read(pinned(plan['protocol']))
    for key in ('rules', 'limitations', 'trialAccounting', 'states'):
        same(plan[key], protocol[key], 'Registered protocol.' + key)
    require(plan['parameterSearch'] is False and plan['holdoutClaim'] is False
            and plan['profitHurdles'] is None and plan['certified'] is False and plan['targetAchieved'] is False,
            'Diagnostic must not silently become strategy selection/certification')
    return plan


def inherited_sources(plan):
    require(plan['parentDelivery'] == {'path': (PARENT / 'delivery.json').relative_to(ROOT).as_posix(),
            'sha256': PARENT_DELIVERY_SHA}, 'Wrong completed parent')
    delivery = read(pinned(plan['parentDelivery']))
    parent_plan, comparison, report, proof, readout_proof = [read(PARENT / name) for name in
        ('plan.json', 'comparison.json', 'readout.json', 'validation.json', 'readout-validation.json')]
    for name in ('plan.json', 'comparison.json', 'readout.json', 'validation.json', 'readout-validation.json'):
        path = PARENT / name
        rel = path.relative_to(ROOT).as_posix()
        require(plan['inputPins'].get(rel) == sha(path) == delivery['files'][rel], 'Parent receipt not frozen: ' + name)
    require(proof['passed'] is True and readout_proof['passed'] is True
            and proof['planSha256'] == sha(PARENT / 'plan.json') == readout_proof['planSha256']
            and proof['comparisonSha256'] == sha(PARENT / 'comparison.json') == readout_proof['comparisonSha256']
            and readout_proof['accountValidationSha256'] == sha(PARENT / 'validation.json')
            and readout_proof['readoutSha256'] == sha(PARENT / 'readout.json'), 'Parent audit chain stale')
    same(plan['sourceData'], parent_plan['sourceData'], 'Source identity inherited unchanged')
    same(plan['accountArtifacts'], comparison['artifacts'], 'Original 12 account byte references')
    same(proof['accountArtifacts'], comparison['artifacts'], 'Account artifacts in original independent proof')
    same(plan['accountReadout'], evidence_ref(PARENT / 'readout.json'), 'Parent report identity')
    same(plan['opportunities'], report['opportunityRowsArtifact'], 'Original all-opportunity sidecar')
    require(plan['opportunities']['sha256'] == readout_proof['opportunityRowsArtifactSha256'],
            'Parent opportunity sidecar not in readout audit')
    top_plan, top_proof = read(TOP_PARENT / 'plan.json'), read(TOP_PARENT / 'validation.json')
    require(top_proof['passed'] is True and top_proof['planSha256'] == sha(TOP_PARENT / 'plan.json'),
            'Top-reference independent audit stale')
    for period in PERIODS:
        same(plan['periods'][period], {k: parent_plan['periods'][period][k] for k in ('startDate', 'endDate')}, 'Original period')
        for board in BOARDS:
            binding = plan['sourceData'][period][board]
            require(binding['accountDatasetDigest'] == top_plan['sourceData'][period][board]['accountDatasetDigest'],
                    'Top-reference source semantic identity differs')
            same(plan['topReference'][period][board], top_proof['inputArtifacts'][period][board]['daily-evidence.jsonl.gz'],
                 'Top-reference daily is the original independently audited artifact')
            require(proof['datasetPins'][binding['accountData']['path']] == binding['accountData']['sha256'],
                    'Source cache not covered by original independent audit')
            source_receipt = read(pinned(binding['upstreamInputReceipt']))
            same(plan['sourceMetadata'][period][board], source_receipt['files']['signal-metadata.json'], 'Original source metadata')
            for policy in POLICIES:
                for artifact in plan['accountArtifacts'][period][board][policy].values():
                    pinned(artifact)
    return report, proof, readout_proof


def verify_contexts(directory, plan):
    plan_sha = sha(directory / 'plan.json')
    completion = read(directory / 'input-completion.json')
    tests = read(directory / 'pre-extraction-tests.json')
    extraction = read(directory / 'extraction-start.json')
    require(completion['planSha256'] == tests['planSha256'] == plan_sha
            and tests['passed'] is True and tests['newAccounts'] == completion['newAccounts'] == 0
            and tests['realContextExtractionStarted'] is False, 'Context extraction receipt stale or wrong scope')
    same(tests['sourcePins'], plan['sourcePins'], 'Tests used frozen sources')
    same(completion['preExtractionTests'], evidence_ref(directory / 'pre-extraction-tests.json'), 'Input receipt pretests')
    same(extraction['preExtractionTests'], completion['preExtractionTests'], 'Extraction start pretests')
    require(len(tests['commands']) == 3 and all(row['exitCode'] == 0 for row in tests['commands']), 'Missing successful preregistered commands')
    require(timestamp(plan['registeredAt']) <= timestamp(tests['startedAt']) <= timestamp(tests['completedAt'])
            <= timestamp(extraction['startedAt']) <= timestamp(completion['completedAt']), 'Tests were not before extraction')
    same(set(completion['outputs']), set(PERIODS), 'Complete period outputs')
    all_contexts, context_refs = {}, []
    counts = {'sourceDays': 0, 'memberObservations': 0, 'topReferenceDays': 0,
              'topMemberObservations': 0, 'bottomMemberObservations': 0}
    pins = {}
    for period in PERIODS:
        same(set(completion['outputs'][period]), set(BOARDS), 'Complete board outputs')
        all_contexts[period] = {}
        for board in BOARDS:
            receipt_ref = completion['outputs'][period][board]
            receipt = read(pinned(receipt_ref))
            require(receipt['planSha256'] == plan_sha and receipt['period'] == period and receipt['board'] == board,
                    'Per-source context receipt identity changed')
            require(timestamp(extraction['startedAt']) <= timestamp(receipt['completedAt']) <= timestamp(completion['completedAt']),
                    'Per-source completion time outside extraction')
            same(receipt['source'], plan['sourceData'][period][board], 'Per-source inherited binding')
            require(set(receipt['files']) == {'daily-evidence.jsonl.gz', 'member-evidence.jsonl.gz', 'metadata.json'},
                    'Missing or extra context source evidence')
            for item in receipt['files'].values():
                pinned(item); pins[item['path']] = item['sha256']
            pins[receipt_ref['path']] = receipt_ref['sha256']
            binding = receipt['source']
            data = read(pinned(binding['accountData']))
            require(digest(data) == binding['accountDatasetDigest'], 'Cached source semantic digest changed')
            member_stream = iter(records(local(receipt['files']['member-evidence.jsonl.gz']['path'])))
            observed = {'rows': 0, 'top': 0, 'bottom': 0}
            def inspect_members(expected):
                actual = next(member_stream, None)
                require(actual is not None, 'Missing member-evidence row')
                same(actual, expected, 'Independent full frozen member lists')
                observed['rows'] += 1
                observed['top'] += len(expected['topMembers'])
                observed['bottom'] += len(expected['bottomMembers'])
            dates = plan['periods'][period]
            expected = reference_build(data, dates['startDate'], dates['endDate'], on_members=inspect_members,
                progress=lambda v: print(json.dumps({**v, 'period': period, 'board': board}), flush=True))
            require(next(member_stream, None) is None, 'Extra member-evidence row')
            daily = list(records(local(receipt['files']['daily-evidence.jsonl.gz']['path'])))
            metadata = read(local(receipt['files']['metadata.json']['path']))
            same(daily, expected['dailyRows'], 'Independent daily contexts')
            same(metadata, {k: v for k, v in expected.items() if k != 'dailyRows'}, 'Independent metadata/counts')
            original_metadata = read(pinned(plan['sourceMetadata'][period][board]))
            for name in ('calendar', 'targetDates'):
                same(metadata[name], original_metadata[name], 'Original source ' + name)
            top_rows = list(records(pinned(plan['topReference'][period][board])))
            top_by_day = {row['formationDate']: row for row in top_rows}
            require(len(top_by_day) == len(top_rows), 'Duplicate original top reference day')
            for row in daily:
                if row['asOfDate'] is not None:
                    require(row['asOfDate'] < row['formationDate'], 'Current/future context date')
                if row['cohortDate'] is not None:
                    require(max(row['rankWindowDates']) == row['cohortDate'] < row['asOfDate']
                            and max(row['feedbackWindowDates']) == row['asOfDate'], 'Feature clock changed')
                original = top_by_day.get(row['asOfDate'])
                if original is not None:
                    for name in ('cohortDate', 'rankWindowDates', 'feedbackWindowDates', 'rankPopulation', 'rankReasonCounts'):
                        same(row[name], original[name], 'Old top clock/rank.' + name)
                    for name, value in row['top'].items():
                        same(value, original[name], 'Old top unchanged.' + name)
                    counts['topReferenceDays'] += 1
            require(observed['rows'] == len(daily), 'Member/day rows differ')
            counts['sourceDays'] += len(daily)
            for side in ('top', 'bottom'):
                require(observed[side] == metadata['counts']['cohortMemberObservations'][side], 'Member total differs')
                counts[side + 'MemberObservations'] += observed[side]
                counts['memberObservations'] += observed[side]
            all_contexts[period][board] = {row['formationDate']: row for row in daily}
            context_refs.append({'period': period, 'board': board, **receipt['files']})
            print(json.dumps({'phase': 'independent-context-source-passed', 'period': period, 'board': board,
                              'days': len(daily), 'memberObservations': observed['top'] + observed['bottom']}), flush=True)
            del data, expected, daily, metadata
    return all_contexts, context_refs, counts, pins


def comparisons_reference(overall):
    indexed = {(r['period'], r['board'], r['policy'], r['state']): r for r in overall}
    require(len(indexed) == 48, 'Incomplete/duplicate account-state matrix')
    rows = []
    fields = ('entered', 'closed', 'open', 'notEntered', 'closedPnlAmount', 'openRealizedPnlAmount',
              'openUnrealizedPnlAmount', 'totalPnlAmount', 'closedContributionPpOfInitialCapital', 'totalContributionPpOfInitialCapital')
    for period in PERIODS:
        for board in BOARDS:
            for state in STATES:
                before, after = (indexed[(period, board, policy, state)] for policy in POLICIES)
                rows.append({'period': period, 'board': board, 'state': state, 'control': 'trail10', 'candidate': 'volatilityTrail',
                             **{name + 'Delta': after[name] - before[name] for name in fields}})
    return rows


def verify(directory):
    directory = directory.resolve()
    require(not (directory / 'validation.json').exists(), 'Refuse to overwrite an existing independent validation')
    plan = registered_plan(directory)
    parent, parent_proof, parent_readout_proof = inherited_sources(plan)
    contexts, context_refs, counts, context_pins = verify_contexts(directory, plan)
    formations = list(records(pinned(plan['opportunities'])))
    require(len(formations) == 8155 and len({(r['period'], r['board'], r['formationId']) for r in formations}) == 8155,
            'Original formation identity/count differs')
    parent_accounts = {(r['period'], r['board'], r['policy']): r for r in parent['accounts']}
    require(set(parent_accounts) == {(p, b, m) for p in PERIODS for b in BOARDS for m in POLICIES}, 'Parent account domain differs')
    all_records, bridges, quality_rows, day_counts = [], [], [], []
    for period in PERIODS:
        for board in BOARDS:
            daily = contexts[period][board]
            original = [r for r in formations if (r['period'], r['board']) == (period, board)]
            unknown_count = lambda r: sum(r[side]['feedbackCounts']['unknown'] for side in ('top', 'bottom'))
            day_counts.append({'period': period, 'board': board, 'targetDays': len(daily), 'formationCount': len(original),
                'dayCountByState': {s: sum(r['state'] == s for r in daily.values()) for s in STATES},
                'opportunityCountByState': {s: sum(daily[r['formationDate']]['state'] == s for r in original) for s in STATES},
                'completeMemberDays': sum(unknown_count(r) == 0 and r['top']['cohortSize'] > 0 and r['bottom']['cohortSize'] > 0 for r in daily.values()),
                'boundedKnownWithUnknownMembersDays': sum(unknown_count(r) > 0 and r['state'] != 'unknown' for r in daily.values()),
                'emptyCohortDays': sum(r['top']['cohortSize'] == 0 or r['bottom']['cohortSize'] == 0 for r in daily.values())})
            for policy in POLICIES:
                artifacts = plan['accountArtifacts'][period][board][policy]
                result = read(pinned(artifacts[policy + '.result.json.gz']))
                summary_ref = artifacts[policy + '.summary.json']
                summary = read(pinned(summary_ref))
                require(result['config']['initialCapital'] == summary['account']['initialCapital'] == 500000,
                        'Contribution denominator must be original capital')
                expected_schedule = [row['signals']['immediate'] for row in original]
                same(result['research']['identity']['signalSchedule'], expected_schedule, 'All original immediate signals in saved account')
                buys, closed, opened = context_reference().episode_indexes(result, summary)
                opportunities = {r['signalId']: r for r in result['research']['opportunities']}
                require(len(opportunities) == len(result['research']['opportunities']) == len(original)
                        and set(opportunities) == {r['formationId'] for r in original}, 'Account opportunity population differs')
                for episode in summary['closedEpisodes']['episodes']:
                    raw = closed[(episode['record']['symbol'], episode['record']['entryDate'])]
                    same(episode['record'], raw, 'Closed summary record/raw episode')
                    same(episode['netReturnPct'], raw['returnPct'], 'Closed summary net return')
                    same(episode['pnlAmount'], raw['pnlAmount'], 'Closed summary cash PnL')
                    same(episode['holdingSessions'], raw['holdingDays'], 'Closed summary duration')
                require(summary['closedEpisodes']['count'] == len(closed), 'Closed count differs')
                assigned_closed, assigned_open, rows = set(), set(), []
                for formation in original:
                    sid = formation['formationId']
                    buy = buys.get(sid)
                    identity = (formation['symbol'], buy['date']) if buy else None
                    completed, open_position = closed.get(identity), opened.get(identity)
                    if completed:
                        require(identity not in assigned_closed, 'Closed episode counted twice'); assigned_closed.add(identity)
                    if open_position:
                        require(identity not in assigned_open, 'Open episode counted twice'); assigned_open.add(identity)
                    disposition = formation['accountDispositions'][policy]
                    same(disposition['accountOpportunity'], opportunities[sid], 'Parent full disposition opportunity')
                    same(disposition['openPositionAtCutoff'], open_position, 'Parent full open disposition')
                    row = record_reference(period, board, policy, formation, daily[formation['formationDate']],
                                           opportunities[sid], buy, completed, open_position)
                    rows.append(row)
                require(assigned_closed == set(closed) and assigned_open == set(opened), 'Unassigned original episode')
                context_reference().check_records(rows, STATES)
                totals = aggregate_group(rows)
                residual = summary['reconciliation']['rightsAttributionGapAmount']
                require(finite(residual), 'Unknown original account residual')
                same(totals['totalPnlAmount'] + residual, summary['account']['netPnlAmount'], 'All episode money plus residual')
                same(summary['account']['netPnlAmount'], result['curve'][-1]['equity'] - 500000, 'Original final equity cash bridge')
                same(summary['account']['netReturnPct'], (result['curve'][-1]['equity'] / 500000 - 1) * 100, 'Original account return')
                account = parent_accounts[(period, board, policy)]
                same(account['account'], summary['account'], 'Parent account summary')
                identity = {'period': period, 'board': board, 'policy': policy}
                bridges.append({**identity, **{key: totals[key] for key in ('formationCount', 'entered', 'closed', 'open', 'notEntered',
                    'closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount')},
                    'assignedPnlAmount': totals['totalPnlAmount'], 'unallocatedResidual': residual,
                    'accountNetPnlAmount': summary['account']['netPnlAmount'], 'accountReturnPct': summary['account']['netReturnPct'], 'passed': True})
                quality_rows.append({**identity, 'unreliableValuationDays': account['comparisonRow']['unreliableValuationDays'],
                    'unresolvedExitCount': summary['unresolvedExits']['count'], 'openMarkStaleCount': totals['openMarkStaleCount'],
                    'numericUnknownCount': len(summary['quality']['numericUnknownPaths']), 'integrityIssueCount': len(summary['quality']['integrityIssues']),
                    'sourceQualityScalarFlags': deepcopy(account['sourceQualityScalarFlags']), 'summary': summary_ref})
                all_records.extend(rows)
                print(json.dumps({'phase': 'independent-account-attribution-passed', **identity, 'formations': len(rows)}), flush=True)
    require(len(all_records) == 16310, 'Missing formation-policy records')
    saved_records = list(records(directory / 'records.jsonl.gz'))
    same(saved_records, all_records, 'Every original formation-policy attribution')
    grouped = aggregate_reference(all_records)
    for bridge in bridges:
        cells = [r for r in grouped['overallRows'] if all(r[k] == bridge[k] for k in ('period', 'board', 'policy'))]
        for key in ('formationCount', 'entered', 'closed', 'open', 'notEntered'):
            same(sum(r[key] for r in cells), bridge[key], 'State partition count.' + key)
        same(math.fsum(r['totalPnlAmount'] for r in cells), bridge['assignedPnlAmount'], 'State money reconciles account allocation')
    report = read(directory / 'readout.json')
    timestamp(report['generatedAt'])
    require(timestamp(read(directory / 'input-completion.json')['completedAt']) <= timestamp(report['generatedAt']), 'Readout predates extraction completion')
    expected = {'schema': VERSION + '-readout', 'generatedAt': report['generatedAt'], 'planSha256': sha(directory / 'plan.json'),
        'inputCompletion': evidence_ref(directory / 'input-completion.json'), 'inputPins': {**plan['sourcePins'], **plan['inputPins']},
        'contexts': context_refs, 'records': evidence_ref(directory / 'records.jsonl.gz'), 'formationCount': len(formations),
        'recordCount': len(all_records), **grouped, 'comparisonRows': comparisons_reference(grouped['overallRows']),
        'accountReconciliation': bridges, 'contextDayCounts': day_counts, 'qualityRows': quality_rows,
        'curveEpochRows': parent['curveEpochRows'], 'accountRows': parent['accountRows'], 'sourceAccountReadout': plan['accountReadout'],
        'trialAccounting': TRIALS, 'method': plan['rules'], 'limitations': plan['limitations'], 'selectedState': None,
        'automaticNextStrategy': None, 'profitHurdles': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    same(report, expected, 'Complete independent diagnostic readout')
    require(all(len(report[key]) == count for key, count in EXPECTED_ROWS.items()), 'Diagnostic matrix row counts differ')
    registered_plan(directory)
    proof = {'schema': VERSION + '-independent-validation', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': sha(__file__),
        'planSha256': sha(directory / 'plan.json'), 'readoutSha256': sha(directory / 'readout.json'),
        'inputCompletionSha256': sha(directory / 'input-completion.json'), 'recordsSha256': sha(directory / 'records.jsonl.gz'),
        'parentValidationSha256': sha(PARENT / 'validation.json'), 'parentReadoutValidationSha256': sha(PARENT / 'readout-validation.json'),
        'parentReadoutSha256': sha(PARENT / 'readout.json'), 'parentDeliverySha256': PARENT_DELIVERY_SHA,
        'topReferenceValidationSha256': sha(TOP_PARENT / 'validation.json'),
        'counts': {**counts, 'formationCount': len(formations), 'recordCount': len(all_records), 'expectedRows': EXPECTED_ROWS,
                   'accounts': 12, 'newAccounts': 0, 'newSignals': 0},
        'pins': {**plan['sourcePins'], **plan['inputPins'], **context_pins,
                 **{(directory / name).relative_to(ROOT).as_posix(): sha(directory / name) for name in
                    ('plan.json', 'pre-extraction-tests.json', 'extraction-start.json', 'input-completion.json', 'records.jsonl.gz', 'readout.json')}},
        'independentReferences': {LAG_REFERENCE: LAG_REFERENCE_SHA, FEEDBACK_REFERENCE: FEEDBACK_REFERENCE_SHA,
                                  CONTEXT_REFERENCE: CONTEXT_REFERENCE_SHA},
        'checks': ['Registered sources and exact parent account/readout proof chain',
                   'Pre-extraction tests completed after registration and before extraction',
                   'Every source-day independent whole-population rank, tied cohorts, member feedback and integer difference',
                   'No current-formation numeric input; strict T-1/T-6 calendar alignment and fixed unknown denominators',
                   'All overlapping top groups match the original batch35 independently audited daily hashes/counts',
                   'Every original formation and both account dispositions retained with raw buy/closed/open identities',
                   'Independent full empty-cell matrices, closed-only distributions, cash concentration and 24 signed contrasts',
                   '12 closed/open-realized/open-unrealized money bridges with original unallocated residual kept separate',
                   'All 12 continuous curve epochs and account rows exactly inherited from audited parent',
                   'Source/account quality flags inherited without new tradability or causal claims'],
        'scope': {'sourceTruth': 'Frozen cache and prior independently audited source bindings inherited; no raw source reread',
                  'accountTruth': 'Original account ledgers/proofs inherited; disposition and money attribution independently recomputed',
                  'claim': 'Descriptive formation-time context on exposed development histories; no tested entry gate or counterfactual account'},
        'certified': False, 'targetAchieved': False}
    with (directory / 'validation.json').open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(json.dumps({'passed': True, 'validation': evidence_ref(directory / 'validation.json'), 'counts': proof['counts']}), flush=True)
    return proof


def self_test():
    def counts(p, z=0, n=0, u=0):
        return dict(zip(MEMBER_STATES, (p, z, n, u)))
    cases = [
        (counts(0), counts(1), 'unknown'),
        (counts(3, u=1), counts(1, n=3), 'stronger'),
        (counts(1, n=3), counts(3, u=1), 'weaker'),
        (counts(1, n=2), counts(2, n=4), 'equal'),
        (counts(1, u=2), counts(1, n=2), 'unknown'),
        (counts(0, n=3), counts(0, n=2), 'equal'),
        (counts(0, z=3), counts(0, n=2), 'equal'),
        (counts(1, n=3), counts(1, n=2), 'weaker'),
    ]
    for top, bottom, wanted in cases:
        _, state = difference_reference(top, bottom)
        require(state == wanted, 'Exact robust sign classification')
    bound, _ = difference_reference(counts(1, n=2), counts(2, n=4))
    require(bound['lowerNumerator'] == bound['upperNumerator'] == 0 and bound['denominator'] == 18,
            'Unequal denominators exact rational equality')
    for ns in range(1, 5):
        for nw in range(1, 5):
            for ps in range(ns + 1):
                for us in range(ns - ps + 1):
                    for pw in range(nw + 1):
                        for uw in range(nw - pw + 1):
                            _, actual = difference_reference(counts(ps, n=ns-ps-us, u=us), counts(pw, n=nw-pw-uw, u=uw))
                            possibilities = {(ps + a) * nw - (pw + b) * ns
                                             for a in range(us + 1) for b in range(uw + 1)}
                            wanted = ('stronger' if min(possibilities) > 0 else 'weaker' if max(possibilities) < 0
                                      else 'equal' if possibilities == {0} else 'unknown')
                            require(actual == wanted, 'Bounds equal all possible assignments of unknown members')
    tied, _ = lag_reference().rank_reference({str(i): v for i, v in enumerate((0, 0, 1, 2, 3, 4, 5, 6, 7, 7))})
    require(sum(p <= .10 for p in tied.values()) == sum(p >= .90 for p in tied.values()) == 2,
            'Inclusive mirrored thresholds retain all boundary ties')
    constant, _ = lag_reference().rank_reference({'a': 1., 'b': 1., 'c': 1.})
    require(all(.10 < p < .90 for p in constant.values()), 'Constant population has no fabricated extreme members')
    data, start, end = synthetic_fixture()
    before, member_rows = digest(data), []
    full = reference_build(data, start, end, on_members=member_rows.append)
    require(digest(data) == before and len(member_rows) == 10, 'No source mutation and full member stream')
    require(all(len(r['topMembers']) == len(r['bottomMembers']) == 4 for r in member_rows),
            'Both deciles selected from same whole rank population')
    row = full['dailyRows'][0]
    require(row['asOfDate'] == data['calendar'][99] and row['cohortDate'] == data['calendar'][94]
            and len(row['rankWindowDates']) == 21 and len(row['feedbackWindowDates']) == 6,
            'Exact T-1/T-6 and complete source-calendar slots')
    changed = deepcopy(data)
    for security in [*changed['securities'], changed['benchmark']]:
        for bar in security['bars']:
            if bar['date'] >= start:
                bar['close'] = None
    same(reference_build(changed, start, start)['dailyRows'][0], row, 'T and later numeric suffix unused')
    changed = deepcopy(data)
    for bar in changed['benchmark']['bars']:
        if data['calendar'][94] < bar['date'] <= start:
            bar['close'] = None
    same(reference_build(changed, start, start)['dailyRows'][0], row, 'Post-F benchmark unused')
    changed = deepcopy(data)
    top_symbol = member_rows[0]['topMembers'][0]['symbol']
    for security in changed['securities']:
        if security['symbol'] == top_symbol:
            security['bars'] = [b for b in security['bars'] if b['date'] != data['calendar'][97]]
    partial_members = []
    partial = reference_build(changed, start, start, on_members=partial_members.append)['dailyRows'][0]
    require(partial['top']['cohortSize'] == 4 and partial['top']['feedbackCounts']['unknown'] == 1
            and [r['symbol'] for r in partial_members[0]['topMembers']] == [r['symbol'] for r in member_rows[0]['topMembers']],
            'Missing interior feedback stays in fixed N and fixed identity')
    short = reference_build(data, data['calendar'][0], data['calendar'][6])['dailyRows']
    require(short[0]['asOfDate'] is None and short[0]['feedbackWindowDates'] == []
            and short[5]['cohortDate'] is None and short[6]['cohortDate'] == data['calendar'][0]
            and all(r['state'] == 'unknown' for r in short), 'Insufficient calendar never future-filled')
    old = reference(FEEDBACK_REFERENCE, FEEDBACK_REFERENCE_SHA)
    old_members = []
    legacy = old.reference_build(data, data['calendar'][99], data['calendar'][108], [], on_members=old_members.append)
    for actual, prior, member, old_member in zip(full['dailyRows'], legacy['dailyRows'], member_rows, old_members):
        same(actual['rankPopulation'], prior['rankPopulation'], 'Inherited top rank population')
        for key in ('cohortSize', 'feedbackCounts', 'positiveFractionLower', 'positiveFractionUpper', 'memberDigest'):
            same(actual['top'][key], prior[key], 'Inherited top.' + key)
        same(member['topMembers'], old_member['members'], 'Inherited top member evidence')
    def attribution_fixture(number, cash, disposition):
        closed, opened = disposition == 'closed', disposition == 'openAtCutoff'
        return {'period': '2024-2025', 'board': 'main', 'policy': 'trail10', 'formationId': str(number),
            'formationDate': '2024-09-24', 'symbol': 'x' + str(number), 'state': 'stronger',
            'signalDate': '2024-09-24', 'entered': closed or opened, 'closed': closed, 'open': opened,
            'positionState': disposition, 'closedReturnPct': cash / 100 if closed else None,
            'closedPnlAmount': float(cash) if closed else 0., 'openRealizedPnlAmount': 3. if opened else 0.,
            'openUnrealizedPnlAmount': -8. if opened else 0., 'openMarkStale': opened,
            'holdingSessions': 7 if closed or opened else None, 'reason': 'fixture'}
    attributed = [attribution_fixture(1, 120, 'closed'), attribution_fixture(2, -30, 'closed'),
                  attribution_fixture(3, 0, 'closed'), attribution_fixture(4, 0, 'notEntered'),
                  attribution_fixture(5, 0, 'openAtCutoff')]
    metrics = aggregate_group(attributed)
    require((metrics['formationCount'], metrics['entered'], metrics['closed'], metrics['open'], metrics['notEntered']) == (5, 4, 3, 1, 1)
            and metrics['closedPnlAmount'] == 90 and metrics['totalPnlAmount'] == 85,
            'Full opportunity denominator and all three cash legs')
    require(metrics['closedMeanPct'] == .3 and metrics['wins'] == metrics['losses'] == metrics['zeros'] == 1
            and metrics['meanWinPct'] == 1.2 and metrics['meanLossPct'] == -.3,
            'Only actual closed episodes enter return distribution; zero closed kept')
    grouped = aggregate_reference(attributed)
    require([len(grouped[k]) for k in ('overallRows', 'formationYearRows', 'epochStateRows')] == [48, 96, 72]
            and len(comparisons_reference(grouped['overallRows'])) == 24, 'Empty states, boards, years and epochs retained')
    before = [r for r in grouped['epochStateRows'] if r['formationEpoch'] == 'before20240924']
    after = [r for r in grouped['epochStateRows'] if r['formationEpoch'] == 'from20240924']
    require(sum(r['formationCount'] for r in before) == 0 and sum(r['formationCount'] for r in after) == 5,
            'Formation cutoff assigns exact boundary without changing cash account')
    require(aggregate_group([])['closedMeanPct'] is None and aggregate_group([])['profitFactor'] is None,
            'Empty cells never become zero-return samples')
    print(json.dumps({'passed': True, 'selfTests': 26, 'realSourceRead': False, 'actualAccountRuns': 0}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    try:
        verify(args.out)
    except Exception as error:
        print(json.dumps({'passed': False, 'type': type(error).__name__, 'message': str(error),
                          'verifierSha256': sha(__file__), 'scope': 'Independent audit failure; frozen evidence unchanged'}), flush=True)
        raise


if __name__ == '__main__':
    main()
