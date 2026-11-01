"""Freeze and attribute existing accounts to causal strong/weak feedback context.

No account runner is imported and no signals, trades or source files are changed.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'reclaim-volatility-trail-v1'
OUT = BASE / 'reclaim-relative-feedback-context-v1'
PROTOCOL = ROOT / 'docs/reclaim-relative-feedback-context-v1.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')
STATES = ('stronger', 'weaker', 'equal', 'unknown')
BOUNDARY = '2024-09-24'
PROGRAM_SHA = '79abc50e3b1ac8c5c3363812f7ea3e5941d9285eb44c89387dc82f70a7997496'
DELIVERY_SHA = '7568310306dfc2bb88b801311ea20e7840d15072d4b973f21e054ba2bf6c2ad9'
MODULE = 'service/research_relative_feedback_context.py'
TESTS = 'tests/test_research_relative_feedback_context.py'
VERIFIER = 'scripts/verify-reclaim-relative-feedback-context.py'
TRIALS = {'diagnosticBatches': 1, 'newStrategyTrials': 0, 'newAccounts': 0,
          'existingAccountsRead': 12, 'newEntryPolicies': 0, 'newExitDefinitions': 0, 'newContextDefinitions': 1}


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def line(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')


def ref(path):
    path = Path(path)
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    path = (ROOT / item['path']).resolve()
    require(not Path(item['path']).is_absolute() and path.is_relative_to(ROOT) and path.is_file(), 'Invalid repository reference')
    require(sha(path) == item['sha256'], 'Changed reference: ' + item['path'])
    return path


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def near(a, b):
    require(finite(a) and finite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7), f'Money/value mismatch: {a!r}, {b!r}')


def verify_plan():
    plan = read(OUT / 'plan.json')
    require(plan['schema'] == OUT.name and plan['trialAccounting'] == TRIALS, 'Wrong plan/scope')
    for mapping in (plan['sourcePins'], plan['inputPins']):
        for name, expected in mapping.items():
            bound({'path': name, 'sha256': expected})
    return plan


def register():
    require(sha(BASE / 'program.json') == PROGRAM_SHA and sha(PARENT / 'delivery.json') == DELIVERY_SHA, 'Previous state differs')
    delivery, previous, comparison, report = [read(PARENT / name) for name in ('delivery.json', 'plan.json', 'comparison.json', 'readout.json')]
    for name, expected in delivery['files'].items():
        bound({'path': name, 'sha256': expected})
    proof = read(PARENT / 'readout-validation.json')
    require(proof['passed'] and proof['readoutSha256'] == sha(PARENT / 'readout.json'), 'Previous readout not audited')
    require(read(PARENT / 'validation.json')['passed'], 'Previous account audit failed')
    protocol = read(PROTOCOL)
    require(protocol['trialAccounting'] == TRIALS and tuple(protocol['states']) == STATES, 'Protocol differs')
    feedback_plan = read(BASE / 'strength-feedback-v1/plan.json')
    feedback_proof = read(BASE / 'strength-feedback-v1/validation.json')
    require(feedback_proof['passed'] and feedback_proof['planSha256'] == sha(BASE / 'strength-feedback-v1/plan.json'), 'Old top-reference audit differs')
    paths = [PROTOCOL] + [PARENT / name for name in ('plan.json', 'delivery.json', 'comparison.json', 'readout.json', 'validation.json', 'readout-validation.json')]
    paths += [bound(report['opportunityRowsArtifact']), BASE / 'strength-feedback-v1/plan.json', BASE / 'strength-feedback-v1/validation.json']
    metadata, top_refs = {}, {}
    for period in PERIODS:
        metadata[period], top_refs[period] = {}, {}
        for board in BOARDS:
            binding = previous['sourceData'][period][board]
            require(binding['accountDatasetDigest'] == feedback_plan['sourceData'][period][board]['accountDatasetDigest'], 'Old top-reference source differs')
            for value in binding.values():
                if isinstance(value, dict) and 'path' in value:
                    paths.append(bound(value))
            metadata[period][board] = ref(BASE / 'board-coverage-reclaim-v1' / period / board / 'signal-metadata.json')
            top_refs[period][board] = ref(BASE / 'strength-feedback-v1' / period / board / 'daily-evidence.jsonl.gz')
            require(top_refs[period][board] == feedback_proof['inputArtifacts'][period][board]['daily-evidence.jsonl.gz'], 'Old top-reference bytes differ from its audit')
            paths += [bound(metadata[period][board]), bound(top_refs[period][board])]
            for policy in POLICIES:
                for value in comparison['artifacts'][period][board][policy].values():
                    paths.append(bound(value))
    sources = [MODULE, TESTS, VERIFIER, 'scripts/diagnose-reclaim-relative-feedback-context.py',
               'service/research_leader_lag.py', 'service/research_strength_feedback.py',
               'scripts/verify-leader-lag-followers.py', 'scripts/verify-strength-feedback.py',
               'scripts/verify-reclaim-context-diagnostic.py']
    plan = {'schema': OUT.name, 'registeredAt': now(), 'previousTurnClassification': 'progress',
        'protocol': ref(PROTOCOL), 'rules': protocol['rules'], 'limitations': protocol['limitations'],
        'sourcePins': {name: sha(ROOT / name) for name in sources},
        'inputPins': {path.relative_to(ROOT).as_posix(): sha(path) for path in paths},
        'sourceData': deepcopy(previous['sourceData']), 'sourceMetadata': metadata, 'topReference': top_refs,
        'accountArtifacts': deepcopy(comparison['artifacts']), 'accountReadout': ref(PARENT / 'readout.json'),
        'opportunities': deepcopy(report['opportunityRowsArtifact']), 'parentDelivery': ref(PARENT / 'delivery.json'),
        'periods': {p: {k: previous['periods'][p][k] for k in ('startDate', 'endDate')} for p in PERIODS},
        'boards': list(BOARDS), 'policies': list(POLICIES), 'states': list(STATES),
        'trialAccounting': TRIALS, 'expectedFormationCount': 8155, 'expectedRecordCount': 16310,
        'preExtractionTestSpec': {'newUnitTests': 16, 'commandCount': 3, 'predecessorUnitTests': 978},
        'expectedRows': {'overallRows': 48, 'formationYearRows': 96, 'epochStateRows': 72,
            'comparisonRows': 24, 'accountReconciliation': 12, 'curveEpochRows': 12,
            'contextDayCounts': 6, 'qualityRows': 12},
        'parameterSearch': False, 'profitHurdles': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    return {'registered': True, **ref(OUT / 'plan.json')}


def pretests():
    plan = verify_plan()
    commands = [
        [sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', TESTS.replace('/', '.').removesuffix('.py'), '-v'],
        [sys.executable, '-X', 'utf8', '-B', str(Path(__file__)), 'self-test'],
        [sys.executable, '-X', 'utf8', '-B', VERIFIER, '--self-test'],
    ]
    results, started = [], now()
    for command in commands:
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        results.append({'argv': command, 'exitCode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr})
        require(run.returncode == 0, 'Pre-extraction test failed: ' + run.stdout + run.stderr)
    require(re.search(r'Ran (\d+) tests', results[0]['stderr']).group(1) == str(plan['preExtractionTestSpec']['newUnitTests']), 'Production test count differs')
    verify_plan()
    receipt = {'startedAt': started, 'completedAt': now(), 'passed': True, 'planSha256': sha(OUT / 'plan.json'),
        'sourcePins': plan['sourcePins'], 'commands': results, 'unitTests': plan['preExtractionTestSpec']['newUnitTests'],
        'newAccounts': 0, 'realContextExtractionStarted': False}
    write(OUT / 'pre-extraction-tests.json', receipt)
    return {'passed': True, 'commands': len(results), **ref(OUT / 'pre-extraction-tests.json')}


def prepare():
    from service.research_relative_feedback_context import build_relative_feedback_context
    plan, tests = verify_plan(), read(OUT / 'pre-extraction-tests.json')
    require(tests['passed'] and tests['planSha256'] == sha(OUT / 'plan.json') and tests['sourcePins'] == plan['sourcePins'], 'Missing/stale pretests')
    write(OUT / 'extraction-start.json', {'startedAt': now(), 'preExtractionTests': ref(OUT / 'pre-extraction-tests.json')})
    outputs = {}
    for period in PERIODS:
        outputs[period] = {}
        for board in BOARDS:
            folder = OUT / period / board
            folder.mkdir(parents=True, exist_ok=False)
            binding = plan['sourceData'][period][board]
            data = read(bound(binding['accountData']))
            require(digest(data) == binding['accountDatasetDigest'], 'Source semantic digest differs')
            dates = plan['periods'][period]
            print(json.dumps({'phase': 'context-start', 'period': period, 'board': board}), flush=True)
            with gzip.open(folder / 'member-evidence.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as stream:
                value = build_relative_feedback_context(data, dates['startDate'], dates['endDate'], on_members=lambda row: line(stream, row))
            metadata = read(bound(plan['sourceMetadata'][period][board]))
            require(value['calendar'] == metadata['calendar'] and value['targetDates'] == metadata['targetDates'], 'Source calendar differs')
            with gzip.open(folder / 'daily-evidence.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as stream:
                for row in value['dailyRows']:
                    line(stream, row)
            write(folder / 'metadata.json', {k: v for k, v in value.items() if k != 'dailyRows'})
            require(digest(data) == binding['accountDatasetDigest'], 'Source mutated')
            write(folder / 'completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
                'period': period, 'board': board, 'source': binding,
                'files': {p.name: ref(p) for p in folder.iterdir() if p.is_file()}})
            outputs[period][board] = ref(folder / 'completion.json')
            print(json.dumps({'phase': 'context-complete', 'period': period, 'board': board, 'days': len(value['dailyRows'])}), flush=True)
            del data, value
            gc.collect()
    verify_plan()
    write(OUT / 'input-completion.json', {'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'outputs': outputs, 'newAccounts': 0, 'preExtractionTests': ref(OUT / 'pre-extraction-tests.json')})
    return {'completed': True, **ref(OUT / 'input-completion.json')}


def aggregate(rows):
    closed = [r for r in rows if r['closed']]
    opened = [r for r in rows if r['open']]
    wins = [r for r in closed if r['closedPnlAmount'] > 0]
    losses = [r for r in closed if r['closedPnlAmount'] < 0]
    amounts = {k: math.fsum(r[k] for r in rows) for k in ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount')}
    profit, loss = math.fsum(r['closedPnlAmount'] for r in wins), -math.fsum(r['closedPnlAmount'] for r in losses)
    values, holds = [r['closedReturnPct'] for r in closed], [r['holdingSessions'] for r in closed]
    mean_of = lambda group, key: math.fsum(r[key] for r in group) / len(group) if group else None
    return {'formationCount': len(rows), 'entered': len(closed) + len(opened), 'closed': len(closed), 'open': len(opened),
        'notEntered': sum(not r['entered'] for r in rows), 'uniqueFormationDays': len({r['formationDate'] for r in rows}),
        'uniqueSymbols': len({r['symbol'] for r in rows}), 'wins': len(wins), 'losses': len(losses),
        'zeros': len(closed) - len(wins) - len(losses), 'openMarkStaleCount': sum(r['openMarkStale'] for r in opened),
        'dispositionCounts': dict(sorted(Counter(r['positionState'] for r in rows).items())),
        'reasonCounts': dict(sorted(Counter(r['reason'] for r in rows).items())), **amounts,
        'grossProfitAmount': profit, 'grossLossAmount': loss, 'totalPnlAmount': math.fsum(amounts.values()),
        'closedContributionPpOfInitialCapital': amounts['closedPnlAmount'] / 500000 * 100,
        'totalContributionPpOfInitialCapital': math.fsum(amounts.values()) / 500000 * 100,
        'closedMeanPct': mean_of(closed, 'closedReturnPct'), 'closedMedianPct': median(values) if values else None,
        'winRatePct': len(wins) / len(closed) * 100 if closed else None,
        'meanWinPct': mean_of(wins, 'closedReturnPct'), 'meanLossPct': mean_of(losses, 'closedReturnPct'),
        'profitFactor': profit / loss if loss else None,
        'bestClosedPct': max(values) if values else None, 'worstClosedPct': min(values) if values else None,
        'meanHoldingSessions': math.fsum(holds) / len(holds) if holds else None,
        'medianHoldingSessions': median(holds) if holds else None,
        'top5ShareOfGrossProfitPct': math.fsum(sorted((r['closedPnlAmount'] for r in wins), reverse=True)[:5]) / profit * 100 if profit else None}


def build_records(opportunities, contexts, summary, period, board, policy):
    closed = {(r['record']['symbol'], r['record']['entryDate'], r['record']['exitDate']): r for r in summary['closedEpisodes']['episodes']}
    opened = {(r['record']['symbol'], r['record']['entryDate']): r for r in summary['openPositions']['positions']}
    require(len(closed) == summary['closedEpisodes']['count'] and len(opened) == summary['openPositions']['count'], 'Duplicate episode identity')
    seen_closed, seen_open, output = set(), set(), []
    for opportunity in opportunities:
        d = opportunity['accountDispositions'][policy]
        day = opportunity['formationDate']
        require(d['signalDate'] == day and d['formationId'] == opportunity['formationId'], 'Disposition formation changed')
        require(opportunity['signals']['immediate']['signalDate'] == day, 'Original immediate signal differs')
        is_closed, is_open = d['positionState'] == 'closed', d['positionState'] == 'openAtCutoff'
        row = {'period': period, 'board': board, 'policy': policy, 'formationId': opportunity['formationId'],
            'formationDate': day, 'symbol': opportunity['symbol'], 'state': contexts[day]['state'],
            'asOfDate': contexts[day]['asOfDate'], 'cohortDate': contexts[day]['cohortDate'],
            'contextMemberDigest': contexts[day]['memberDigest'], 'signalDate': d['signalDate'],
            'entryDate': d['entryDate'], 'exitDate': d['exitDate'], 'positionState': d['positionState'],
            'entered': d['filledQuantity'] > 0, 'closed': is_closed, 'open': is_open,
            'closedReturnPct': None, 'closedPnlAmount': 0., 'openRealizedPnlAmount': 0., 'openUnrealizedPnlAmount': 0.,
            'openMarkStale': False, 'holdingSessions': None, 'reason': d['reason']}
        require(row['entered'] == (is_closed or is_open), 'Entered disposition lacks episode')
        if is_closed:
            key = (row['symbol'], row['entryDate'], row['exitDate'])
            require(key not in seen_closed, 'Closed episode assigned twice')
            e = closed[key]
            seen_closed.add(key)
            near(e['netReturnPct'], d['closedReturnPct']); near(e['pnlAmount'], d['closedPnlAmount'])
            row.update(closedReturnPct=e['netReturnPct'], closedPnlAmount=e['pnlAmount'], holdingSessions=e['holdingSessions'])
        if is_open:
            key = (row['symbol'], row['entryDate'])
            require(key not in seen_open, 'Open episode assigned twice')
            e = opened[key]
            seen_open.add(key)
            row.update(openRealizedPnlAmount=e['realizedPnlWhileOpen'], openUnrealizedPnlAmount=e['unrealizedPnlAmount'],
                openMarkStale=e['markStaleAtCutoff'], holdingSessions=e['observedHoldingSessions'])
        require(all(finite(row[k]) for k in ('closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount')), 'Nonfinite episode money')
        if is_closed:
            require(finite(row['closedReturnPct']) and type(row['holdingSessions']) is int and row['holdingSessions'] >= 0, 'Unknown closed outcome')
        output.append(row)
    require(seen_closed == set(closed) and seen_open == set(opened), 'Unassigned account episode')
    return output


def summarize():
    plan, completion = verify_plan(), read(OUT / 'input-completion.json')
    require(completion['planSha256'] == sha(OUT / 'plan.json'), 'Stale context extraction')
    parent = read(bound(plan['accountReadout']))
    opportunities = records(bound(plan['opportunities']))
    require(len(opportunities) == plan['expectedFormationCount'] and len({(r['period'], r['board'], r['formationId']) for r in opportunities}) == len(opportunities), 'Original opportunity population differs')
    all_records, overall, yearly, epochs, bridges, day_counts, qualities, context_refs = [], [], [], [], [], [], [], []
    for period in PERIODS:
        for board in BOARDS:
            receipt = read(bound(completion['outputs'][period][board]))
            for item in receipt['files'].values():
                bound(item)
            daily_ref = receipt['files']['daily-evidence.jsonl.gz']
            context_refs.append({'period': period, 'board': board, **receipt['files']})
            daily = records(bound(daily_ref))
            contexts = {r['formationDate']: r for r in daily}
            require(len(contexts) == len(daily) and all(r['state'] in STATES for r in daily), 'Invalid context identities')
            source = [r for r in opportunities if (r['period'], r['board']) == (period, board)]
            member_unknown = lambda r: r['top']['feedbackCounts']['unknown'] + r['bottom']['feedbackCounts']['unknown']
            day_counts.append({'period': period, 'board': board, 'targetDays': len(daily), 'formationCount': len(source),
                'dayCountByState': {s: sum(r['state'] == s for r in daily) for s in STATES},
                'opportunityCountByState': {s: sum(contexts[r['formationDate']]['state'] == s for r in source) for s in STATES},
                'completeMemberDays': sum(member_unknown(r) == 0 and r['top']['cohortSize'] > 0 and r['bottom']['cohortSize'] > 0 for r in daily),
                'boundedKnownWithUnknownMembersDays': sum(member_unknown(r) > 0 and r['state'] != 'unknown' for r in daily),
                'emptyCohortDays': sum(r['top']['cohortSize'] == 0 or r['bottom']['cohortSize'] == 0 for r in daily)})
            for policy in POLICIES:
                summary_ref = plan['accountArtifacts'][period][board][policy][policy + '.summary.json']
                summary = read(bound(summary_ref))
                rows = build_records(source, contexts, summary, period, board, policy)
                all_records.extend(rows)
                account = next(r for r in parent['accounts'] if (r['period'], r['board'], r['policy']) == (period, board, policy))
                original = aggregate(rows)
                residual = summary['reconciliation']['rightsAttributionGapAmount']
                near(original['totalPnlAmount'] + residual, summary['account']['netPnlAmount'])
                bridges.append({'period': period, 'board': board, 'policy': policy, 'formationCount': len(rows),
                    'entered': original['entered'], 'closed': original['closed'], 'open': original['open'], 'notEntered': original['notEntered'],
                    'closedPnlAmount': original['closedPnlAmount'], 'openRealizedPnlAmount': original['openRealizedPnlAmount'],
                    'openUnrealizedPnlAmount': original['openUnrealizedPnlAmount'], 'assignedPnlAmount': original['totalPnlAmount'],
                    'unallocatedResidual': residual, 'accountNetPnlAmount': summary['account']['netPnlAmount'],
                    'accountReturnPct': summary['account']['netReturnPct'], 'passed': True})
                qualities.append({'period': period, 'board': board, 'policy': policy,
                    'unreliableValuationDays': account['comparisonRow']['unreliableValuationDays'],
                    'unresolvedExitCount': summary['unresolvedExits']['count'], 'openMarkStaleCount': original['openMarkStaleCount'],
                    'numericUnknownCount': len(summary['quality']['numericUnknownPaths']),
                    'integrityIssueCount': len(summary['quality']['integrityIssues']),
                    'sourceQualityScalarFlags': deepcopy(account['sourceQualityScalarFlags']), 'summary': summary_ref})
                for state in STATES:
                    selected = [r for r in rows if r['state'] == state]
                    identity = {'period': period, 'board': board, 'policy': policy, 'state': state}
                    overall.append({**identity, **aggregate(selected)})
                    for year in period.split('-'):
                        yearly.append({**identity, 'formationYear': year, **aggregate([r for r in selected if r['formationDate'][:4] == year])})
                    cuts = [('oldPeriod', None, None)] if period == '2022-2023' else [('before20240924', None, BOUNDARY), ('from20240924', BOUNDARY, None)]
                    for epoch, start, end in cuts:
                        epochs.append({**identity, 'formationEpoch': epoch, **aggregate([r for r in selected if (start is None or r['formationDate'] >= start) and (end is None or r['formationDate'] < end)])})
    comparisons = []
    for period in PERIODS:
        for board in BOARDS:
            for state in STATES:
                get = lambda policy: next(r for r in overall if (r['period'], r['board'], r['state'], r['policy']) == (period, board, state, policy))
                control, candidate = get('trail10'), get('volatilityTrail')
                comparisons.append({'period': period, 'board': board, 'state': state, 'control': 'trail10', 'candidate': 'volatilityTrail',
                    **{k + 'Delta': candidate[k] - control[k] for k in ('entered', 'closed', 'open', 'notEntered', 'closedPnlAmount', 'openRealizedPnlAmount', 'openUnrealizedPnlAmount', 'totalPnlAmount', 'closedContributionPpOfInitialCapital', 'totalContributionPpOfInitialCapital')}})
    require(len(all_records) == plan['expectedRecordCount'], 'Missing opportunity dispositions')
    with gzip.open(OUT / 'records.jsonl.gz', 'xt', encoding='utf-8', compresslevel=1) as stream:
        for row in all_records:
            line(stream, row)
    report = {'schema': OUT.name + '-readout', 'generatedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
        'inputCompletion': ref(OUT / 'input-completion.json'), 'inputPins': {**plan['sourcePins'], **plan['inputPins']},
        'contexts': context_refs, 'records': ref(OUT / 'records.jsonl.gz'),
        'formationCount': len(opportunities), 'recordCount': len(all_records),
        'overallRows': overall, 'formationYearRows': yearly, 'epochStateRows': epochs,
        'comparisonRows': comparisons, 'accountReconciliation': bridges, 'contextDayCounts': day_counts,
        'qualityRows': qualities, 'curveEpochRows': deepcopy(parent['curveEpochRows']),
        'accountRows': deepcopy(parent['accountRows']), 'sourceAccountReadout': plan['accountReadout'],
        'trialAccounting': TRIALS, 'method': plan['rules'], 'limitations': plan['limitations'],
        'selectedState': None, 'automaticNextStrategy': None, 'profitHurdles': None,
        'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    require(all(len(report[k]) == v for k, v in plan['expectedRows'].items()), 'Incomplete diagnostic matrix')
    verify_plan()
    write(OUT / 'readout.json', report)
    return {'completed': True, **ref(OUT / 'readout.json'), 'formationCount': len(opportunities), 'recordCount': len(all_records), **plan['expectedRows']}


def self_test():
    empty = aggregate([])
    require(empty['formationCount'] == 0 and empty['closedMeanPct'] is None and empty['closedPnlAmount'] == 0, 'Empty group semantics')
    def row(value, state='closed'):
        return {'closed': state == 'closed', 'open': state == 'openAtCutoff', 'entered': state != 'notEntered',
            'formationDate': '2024-01-02', 'symbol': 'x', 'positionState': state, 'reason': 'fixture',
            'closedPnlAmount': value if state == 'closed' else 0., 'closedReturnPct': value / 100 if state == 'closed' else None,
            'openRealizedPnlAmount': 3. if state == 'openAtCutoff' else 0., 'openUnrealizedPnlAmount': -8. if state == 'openAtCutoff' else 0.,
            'openMarkStale': state == 'openAtCutoff', 'holdingSessions': 7 if state != 'notEntered' else None}
    values = [row(120), row(-30), row(0), row(0, 'notEntered'), row(0, 'openAtCutoff')]
    a = aggregate(values)
    require((a['formationCount'], a['entered'], a['closed'], a['open'], a['notEntered']) == (5, 4, 3, 1, 1), 'Opportunity denominator')
    require(a['wins'] == a['losses'] == a['zeros'] == 1, 'Zero return classification')
    near(a['closedPnlAmount'], 90); near(a['totalPnlAmount'], 85)
    near(a['closedContributionPpOfInitialCapital'], .018); near(a['profitFactor'], 4)
    near(a['meanWinPct'], 1.2); near(a['meanLossPct'], -.3)
    require(a['openMarkStaleCount'] == 1 and a['uniqueSymbols'] == a['uniqueFormationDays'] == 1, 'Maturity/unique count')
    return {'passed': True, 'checks': 10, 'realFilesRead': 0, 'newAccounts': 0}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'pretests', 'prepare', 'summarize', 'self-test'))
    args = parser.parse_args()
    print(json.dumps({'register': register, 'pretests': pretests, 'prepare': prepare, 'summarize': summarize, 'self-test': self_test}[args.mode](), ensure_ascii=False), flush=True)
