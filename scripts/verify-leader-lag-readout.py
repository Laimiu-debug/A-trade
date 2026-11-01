"""Independent leader-lag report audit; no signal or account execution."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/strategy-search/leader-lag-followers-v1'
MAIN = 'scripts/verify-leader-lag-followers.py'
MAIN_SHA = '3e0244282a82701dcc942f532673c4e17493543cfa335f9e2459da6a7a33333c'
CONTEXT = 'scripts/verify-reclaim-context-diagnostic.py'
CONTEXT_SHA = '9b899e4c84ca90d2d26be4ef3a2ae2910029fa726441e23bed7ccbf6f48c67c9'
BOARD_READOUT = 'scripts/verify-board-coverage-readout.py'
BOARD_READOUT_SHA = '376ef50af09e1024e6ca578acc6dda5d4a5d39b9b67c3057eb67b330fe158650'
ATLAS = 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json'
ATLAS_SHA = 'ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8'


def load(self_test=False):
    spec = importlib.util.spec_from_file_location('_anchor_readout_independent_main', ROOT / MAIN)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    audit.require(self_test or MAIN_SHA is not None and audit.sha(ROOT / MAIN) == MAIN_SHA, 'Main independent verifier changed')
    return audit, audit.reference(CONTEXT, CONTEXT_SHA), audit.reference(BOARD_READOUT, BOARD_READOUT_SHA)


def scheduled(original, mode):
    return sorted([row['signals'][mode] for row in original if row['signals'][mode] is not None],
                  key=lambda row: (row['signalDate'], row['originalRank'], row['symbol'], row['signalId']))


def formation_reference(original, result, mode, start, end, audit, ctx):
    schedule = result['research']['identity']['signalSchedule']
    audit.require(schedule == scheduled(original, mode), 'Original formation-based schedule differs')
    account = {row['signalId']: row for row in result['research']['opportunities']}
    audit.require(len(account) == len(result['research']['opportunities']) and set(account) == {r['signalId'] for r in schedule},
                  'Complete admitted account opportunity population')
    formed = [row for row in original if start <= row['formationDate'] <= end]
    admitted = [account[row['formationId']] for row in formed if row['formationId'] in account]
    entered = sum(audit.finite(row.get('filledQuantity')) and row['filledQuantity'] > 0 for row in admitted)
    policies = Counter('scheduled' if row['formationId'] in account else row['decisions'][mode]['status'] for row in formed)
    return {'formationTop5': len(formed), 'selectedMemberTop5': len(formed), 'relationshipStates': ctx.labels(relationship_state(r) for r in formed),
        'policyDispositionCounts': {key: policies[key] for key in ('scheduled', 'excluded', 'unknown')},
        'selectedSignalsFromFormation': len(admitted), 'notScheduledFromFormation': len(formed) - len(admitted),
        'enteredFromFormation': entered, 'notEnteredFromFormation': len(admitted) - entered,
        'uniqueFormationDays': len({r['formationDate'] for r in formed}), 'uniqueSymbols': len({r['symbol'] for r in formed}),
        'uniqueOwnerEvents': len({r['eventId'] for r in formed}),
        'terminalStatusCounts': ctx.labels(r.get('status', 'missing') for r in admitted),
        'terminalReasonCounts': ctx.labels(r.get('reason', 'missing') for r in admitted),
        'meaning': 'Original member Top5 cohort uses activation A; F is historical relation cutoff. Terminal dispositions are measured at account cutoff, not interval end.'}


def relationship_state(row):
    control, direction = (row['decisions'][mode]['status'] for mode in ('control', 'direction'))
    if (control, direction) == ('unknown', 'unknown'):
        return 'unknown'
    if (control, direction) == ('included', 'included'):
        return 'passed'
    if (control, direction) == ('included', 'excluded'):
        return 'failed'
    raise ValueError('Invalid common relationship admission state')


def interval_reference(result, original, mode, start, end, audit, ctx):
    measured = ctx.curve_interval(result, start, end)
    queued = [r for r in result['decisions'] if start <= r['date'] <= end and r.get('action') == 'sell' and r.get('status') == 'queued']
    measured['queuedExitReasons'] = ctx.labels(r.get('reason', 'missing') for r in queued)
    measured['queuedExitDecisionCount'] = len(queued)
    measured['queuedExitDefinition'] = 'action=sell AND status=queued; queued decisions, partial fills and closed episodes differ'
    measured['opportunitiesByFormationDate'] = formation_reference(original, result, mode, start, end, audit, ctx)
    return measured


def dispositions_reference(original, result, summary, mode, audit, board_readout):
    details = board_readout.disposition_reference(result, audit)
    account = {row['signalId']: row for row in result['research']['opportunities']}
    opened = {(row['record']['symbol'], row['record']['entryDate']): row for row in summary['openPositions']['positions']}
    audit.require(len(opened) == summary['openPositions']['count'], 'Open episode identity collision')
    output = {}
    for row in original:
        sid, signal = row['formationId'], row['signals'][mode]
        audit.require((sid in details) == (signal is not None), 'Schedule/account identity mismatch')
        if signal is None:
            detail = {'signalId': sid, 'signalDate': None, 'entryDate': None, 'exitDate': None,
                'positionState': 'notScheduled', 'status': 'notScheduled', 'reason': row['decisions'][mode]['reason'],
                'admissionStatus': row['decisions'][mode]['status'],
                'filledQuantity': 0, 'attemptDate': None, 'closedReturnPct': None, 'closedPnlAmount': None,
                'accountOpportunity': None, 'openPositionAtCutoff': None}
        else:
            detail = details[sid]
            position = opened.get((row['symbol'], detail['entryDate']))
            audit.require((position is not None) == (detail['positionState'] == 'openAtCutoff'), 'Open episode linkage mismatch')
            detail = {**detail, 'admissionStatus': 'included', 'accountOpportunity': account[sid], 'openPositionAtCutoff': position}
        output[sid] = {'formationId': sid, 'eventId': row['eventId'], 'formationDate': row['formationDate'],
            'peerFormationDate': row['peerFormationDate'], 'plannedNextEntryDate': row.get('plannedNextEntryDate'),
            'nextEntryWithinPeriod': row.get('nextEntryWithinPeriod'),
            'decisionDate': signal['signalDate'] if signal else None, **detail}
    return output


def metric_reference(account, audit):
    fields = dict(account['comparisonRow'])
    book, curve, bridge = account['account'], account['curveAttribution'], account['reconciliation']
    fields.update(netPnlAmount=book['netPnlAmount'], finalEquity=book['finalEquity'],
        closedPnlAmount=bridge['closedEpisodePnl'], openRealizedPnlAmount=bridge['openPositionRealizedPnl'],
        openUnrealizedPnlAmount=bridge['openPositionUnrealizedPnl'], investedSessionPct=curve['investedSessionPct'],
        feesPaid=curve['feesPaid'], slippageAlreadyInFillPrices=curve['slippageAlreadyInFillPrices'],
        buyFillCount=curve['buyFillCount'], sellFillCount=curve['sellFillCount'],
        notEntered=curve['opportunitiesByDecisionDate']['notEntered'],
        notScheduled=curve['opportunitiesByFormationDate']['notScheduledFromFormation'],
        top1ShareOfGrossProfitPct=account['concentration']['top1ProfitSharePct'],
        top5ShareOfGrossProfitPct=account['concentration']['top5ProfitSharePct'])
    return {key: value for key, value in fields.items() if value is None or audit.finite(value)}


def contrast_reference(accounts, audit):
    indexed = {(row['period'], row['board'], row['policy']): metric_reference(row, audit) for row in accounts}
    output = []
    for period in audit.PERIODS:
        for board in audit.BOARDS:
            for name, control, candidate in (('primary', 'control', 'direction'),):
                a, b = indexed[(period, board, control)], indexed[(period, board, candidate)]
                audit.require(a.keys() == b.keys(), 'Comparison metric key coverage')
                output.append({'period': period, 'board': board, 'id': name, 'control': control, 'candidate': candidate,
                    'controlMetrics': a, 'candidateMetrics': b,
                    'candidateMinusControl': {key: b[key] - a[key] if audit.finite(a[key]) and audit.finite(b[key]) else None for key in sorted(a)},
                    'meaning': 'direction minus control within common original Top5 and jointly evaluable relationships; historical full-account policy difference, not causal influence.'})
    return output


def add_counts(rows):
    result = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, dict):
                result[key] = add_counts([result.get(key, {}), value])
            else:
                if type(value) is not int or value < 0:
                    raise ValueError('Nonnegative integer count required')
                result[key] = result.get(key, 0) + value
    return result


def layer_reference(folder, receipt, original, daily, audit):
    import gzip
    events = audit.records(folder / 'leader-events.jsonl.gz')
    edges = audit.records(folder / 'member-edges.jsonl.gz')
    ranks = audit.records(folder / 'rank-evidence.jsonl.gz')
    metadata = audit.read(folder / 'signal-metadata.json')
    aggregate = add_counts(row['counts'] for row in daily)
    audit.require([row['formationDate'] for row in daily] == metadata['targetDates'], 'Every original activation day retained')
    for rows, key, ids in ((events, 'eventId', 'leaderEventIds'), (edges, 'formationId', 'memberEdgeIds'),
                           (original, 'formationId', 'selectedFormationIds')):
        audit.require([row[key] for row in rows] == [value for day in daily for value in day[ids]], 'All layer identities and original order')
    known = {row['eventId']: row for row in events}
    group_rows, evaluations, unknowns, seen = 0, 0, Counter(), set()
    with gzip.open(folder / 'group-evidence.jsonl.gz', 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            group, event = row['group'], known[row['eventId']]
            audit.require(row['eventId'] not in seen and audit.digest(group) == row['groupSha256'] == event['groupSha256'],
                          'Full group identity/hash changed')
            seen.add(row['eventId'])
            audit.same({key: value for key, value in group.items() if key != 'candidateEvaluations'}, event['groupSummary'],
                       'Group summary/full candidate binding')
            candidates = group['candidateEvaluations']
            audit.require(audit.digest(candidates) == group['candidateEvaluationsSha256']
                          and len(candidates) == receipt['members'] - 1, 'All board peer candidate records retained')
            group_rows += 1
            evaluations += len(candidates)
            unknowns.update(row['reason'] for row in candidates if row['status'] == 'unknown')
    audit.require(seen == set(known) and aggregate['leaderEventCount'] == len(events)
                  and aggregate['rawEdges'] == len(edges) == aggregate['leaderGroupKinds']['formed'] * 20
                  and aggregate['rawEdges'] == aggregate['uniqueOwners'] + aggregate['duplicateSuppressed']
                  and aggregate['uniqueOwners'] == len(original) + aggregate['dailyRankExcluded']
                  and aggregate['selectedTop5'] == len(original), 'Four-layer population conservation')
    audit.require([row['date'] for row in ranks] == [day for day in metadata['calendar'] if day >= metadata['basis']['rankClockStart']],
                  'Warmup rank evidence retained')
    states = Counter(relationship_state(row) for row in original)
    return {'passed': True, 'counts': receipt['counts'], 'leaderEventCount': len(events), 'rawEdgeCount': len(edges),
        'uniqueOwnershipCount': aggregate['uniqueOwners'], 'formationTop5': len(original), 'selectedMemberTop5': len(original),
        'relationshipStateCounts': {key: states[key] for key in ('passed', 'failed', 'unknown')},
        'leaderGroupKinds': aggregate['leaderGroupKinds'], 'uniqueFormationDays': len({row['formationDate'] for row in original}),
        'uniqueSymbols': len({row['symbol'] for row in original}),
        'ownerEventSelectionCounts': dict(sorted(Counter(row['eventId'] for row in original).items())),
        'activationLayerRows': [{'formationDate': row['formationDate'], 'counts': row['counts']} for row in daily],
        'meaning': 'Four separate denominators; activation A is trade cohort, F only relationship cutoff. Incomplete groups preserved, no synthetic member edges; overlapping edges/events are not independent samples.',
        'layerCounts': aggregate, 'basis': metadata['basis'], 'rankDayCount': len(ranks),
        'groupCandidateEvaluationCount': evaluations, 'groupCandidateUnknownReasonCounts': dict(sorted(unknowns.items())),
        'fullGroupRecords': group_rows,
        'evidenceArtifacts': {name: {'path': ref['path'], 'sha256': ref['sha256']} for name, ref in receipt['files'].items() if name.endswith('.jsonl.gz')}}


def verify(directory, audit, ctx, board_readout):
    require, same, read = audit.require, audit.same, audit.read
    require(not (directory / 'readout-validation.json').exists(), 'Readout proof already exists')
    plan = audit.registered_plan(directory)
    source = audit.inherited_sources(plan)
    completion, receipts = audit.input_receipts(directory, plan)
    proof, report, comparison = (read(directory / name) for name in ('validation.json', 'readout.json', 'comparison.json'))
    require(proof['passed'] is True and proof['verifierSha256'] == MAIN_SHA and proof['planSha256'] == audit.REGISTERED_PLAN_SHA
            and proof['comparisonSha256'] == audit.sha(directory / 'comparison.json')
            and proof['inputCompletionSha256'] == audit.sha(directory / 'input-completion.json')
            and proof['inputValidationSha256'] == audit.sha(directory / 'input-validation.json')
            and proof['datasetPins'] == source['datasetPins'], 'Account verification binding stale')
    require(report['schema'] == 'leader-lag-followers-readout-v1' and report['planSha256'] == audit.REGISTERED_PLAN_SHA
            and report['verification']['passed'] is True
            and audit.pinned(report['verification']) == (directory / 'validation.json').resolve(), 'Report binding stale')
    for path, expected in report['inputPins'].items():
        require(audit.sha(audit.local(path)) == expected, 'Report input pin changed: ' + path)
    lengths = {'accounts': 12, 'accountRows': 12, 'comparisons': 6, 'primaryComparisons': 6, 'secondaryComparisons': 0,
        'yearRows': 24, 'halfYearRows': 48, 'phaseRows': 96, 'curveEpochRows': 12,
        'coverageRows': 12, 'formationCoverageRows': 6, 'partitionReconciliation': 12}
    require(all(len(report[key]) == count for key, count in lengths.items()), 'Complete 12-account/time/contrast matrix')
    matrix = [(p, b, m) for p in audit.PERIODS for b in audit.BOARDS for m in audit.MODES]
    require([(r['period'], r['board'], r['policy']) for r in report['accounts']] == matrix, 'Account order or identity differs')
    same(report['accountRows'], comparison['rows'], 'Complete original comparison rows')
    sidecar = report['opportunityRowsArtifact']
    require(sidecar['format'] == 'jsonl+gzip' and sidecar['identity'] == ['period', 'board', 'formationId']
            and sidecar['policiesPerRow'] == list(audit.MODES), 'Sidecar scope differs')
    sidecar_path = audit.pinned(sidecar)
    require(sidecar_path == (directory / 'opportunity-dispositions.jsonl.gz').resolve(), 'Sidecar output path differs')
    observations = audit.records(sidecar_path)
    require(len(observations) == sidecar['count'] == len({(r['period'], r['board'], r['formationId']) for r in observations})
            == proof['counts']['memberOpportunities'], 'Complete unique original members required')
    require(audit.sha(ROOT / ATLAS) == ATLAS_SHA, 'Original descriptive phase atlas changed')
    phases = read(ROOT / ATLAS)['phases']
    by_account = {(r['period'], r['board'], r['policy']): r for r in report['accounts']}
    by_coverage = {(r['period'], r['board'], r['policy']): r for r in report['coverageRows']}
    by_partition = {(r['period'], r['board'], r['policy']): r for r in report['partitionReconciliation']}
    require(len(by_account) == len(by_coverage) == len(by_partition) == 12, 'Repeated account/coverage/partition identity')
    checked, intervals = 0, 0
    order = []
    for period in audit.PERIODS:
        for board in audit.BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            original, daily = (audit.records(folder / name) for name in ('opportunities.jsonl.gz', 'daily-evidence.jsonl.gz'))
            metadata = read(folder / 'signal-metadata.json')
            day_index = {day: i for i, day in enumerate(metadata['calendar'])}
            for formed in original:
                next_index = day_index[formed['formationDate']] + 1
                next_date = metadata['calendar'][next_index] if next_index < len(metadata['calendar']) else None
                formed.update(plannedNextEntryDate=next_date, nextEntryWithinPeriod=next_date in metadata['targetDates'])
            config = plan['periods'][period]['config']
            rows = [row for row in observations if row['period'] == period and row['board'] == board]
            require([r['formationId'] for r in rows] == [r['formationId'] for r in original], 'Original member sidecar order')
            for formed, saved in zip(original, rows):
                same({k: v for k, v in saved.items() if k != 'accountDispositions'}, {**formed, 'period': period, 'board': board},
                     'All original member relation/evidence/failures retained')
                require(set(saved['accountDispositions']) == set(audit.MODES), 'Missing original policy disposition')
                order.append((period, board, formed['formationId']))
            for mode in audit.MODES:
                key = period, board, mode
                saved_account = by_account[key]
                require(saved_account['id'] == mode and saved_account['label'] == audit.LABELS[mode]
                        and saved_account['execution'] == 'new-independent-account', 'Account report identity changed')
                result, summary, schedule = (read(folder / (mode + suffix)) for suffix in ('.result.json.gz', '.summary.json', '.signals.json'))
                require(schedule == scheduled(original, mode) == result['research']['identity']['signalSchedule'], 'Original schedule changed')
                details = dispositions_reference(original, result, summary, mode, audit, board_readout)
                for actual in rows:
                    same(actual['accountDispositions'][mode], details[actual['formationId']], 'Every original policy entry/exit/open disposition')
                    checked += 1
                whole = interval_reference(result, original, mode, config['startDate'], config['endDate'], audit, ctx)
                same(saved_account['curveAttribution'], whole, 'Independent complete curve arithmetic')
                same(saved_account['closedDistribution'], whole['closedEpisodesByExitDate'], 'Closed return/holding distribution')
                for key in ('account', 'closedEpisodes', 'unresolvedExits', 'concentration', 'reconciliation', 'quality', 'rights', 'sourceQuality'):
                    same(saved_account[key], summary[key], 'Original summary field: ' + key)
                same(saved_account['configuredUniverseValues'], config['universe'], 'Configured values copied without new gate')
                same(saved_account['opportunityStatusCounts'], summary['opportunityDispositions']['opportunityCounts'], 'All terminal statuses')
                flags = {k: v for k, v in summary['sourceQuality'].items() if not isinstance(v, (dict, list))}
                same(saved_account['sourceQualityScalarFlags'], flags, 'Original quality flags')
                require(saved_account['warningCount'] == len(summary.get('warnings', [])), 'Original warning count')
                row = next(r for r in comparison['rows'] if (r['period'], r['board'], r['id']) == (period, board, mode))
                same(saved_account['comparisonRow'], row, 'Original policy comparison')
                same(saved_account['artifactRefs'], comparison['artifacts'][period][board][mode], 'Complete saved account refs')
                same(saved_account['sourceBinding'], plan['sourceData'][period][board], 'Original source binding')
                opened = deepcopy(summary['openPositions'])
                opened['unrealizedPnlAmount'] = math.fsum(r['unrealizedPnlAmount'] for r in opened['positions'])
                opened['realizedPnlWhileOpen'] = math.fsum(r['realizedPnlWhileOpen'] for r in opened['positions'])
                same(saved_account['openPositionsAtCutoff'], opened, 'All terminal open episodes')
                same(math.fsum([whole['closedEpisodesByExitDate']['netPnlAmount'], opened['unrealizedPnlAmount'],
                    opened['realizedPnlWhileOpen'], summary['reconciliation']['rightsAttributionGapAmount']]), whole['netPnlAmount'],
                    'Complete closed/open/rights monetary bridge')
                expected_coverage = {'period': period, 'board': board, 'id': mode, 'policy': mode,
                    **whole['opportunitiesByFormationDate'], **whole['opportunitiesByDecisionDate'],
                    'buyFillCount': whole['buyFillCount'], 'sellFillCount': whole['sellFillCount'],
                    'closedCount': len(result['closedTrades']), 'openCountAtCutoff': opened['count'],
                    'unresolvedExitCountAtCutoff': summary['unresolvedExits']['count'], 'curveSessions': len(result['curve']),
                    'valuationUnreliableDays': len(whole['valuationUnreliableDates']), 'qualityScalarFlags': flags}
                same(by_coverage[(period, board, mode)], expected_coverage, 'All-policy population/status coverage')
                partition_checks = []
                for group, label in (('yearRows', 'years'), ('halfYearRows', 'halfyears'), ('phaseRows', 'phases'), ('curveEpochRows', 'userEventEpochs')):
                    parts = [r for r in report[group] if (r['period'], r['board'], r['policy']) == (period, board, mode)]
                    if group == 'curveEpochRows' and period == audit.PERIODS[0]:
                        require(not parts, 'Unexpected older event-boundary intervals')
                        continue
                    windows = ([(r['requestedStart'], r['requestedEnd']) for r in summary['periods'] if r['kind'] ==
                        ('calendarYear' if group == 'yearRows' else 'calendarHalfYear')] if group in ('yearRows', 'halfYearRows') else
                        [(r['startDate'], r['endDate']) for r in phases if r['startDate'] <= config['endDate'] and r['endDate'] >= config['startDate']]
                        if group == 'phaseRows' else [('2024-01-02', '2024-09-23'), ('2024-09-24', '2025-12-31')])
                    require([(r['requestedStart'], r['requestedEnd']) for r in parts] == windows, 'Every original interval present: ' + group)
                    for part in parts:
                        expected = interval_reference(result, original, mode, part['requestedStart'], part['requestedEnd'], audit, ctx)
                        same({k: part[k] for k in expected}, expected, 'All interval curve/formation/decision arithmetic: ' + group)
                        same(part['selectionByActivationDate'], add_counts(r['counts'] for r in daily
                            if part['requestedStart'] <= r['formationDate'] <= part['requestedEnd']), 'Selection layer counts by activation A')
                        intervals += 1
                    amount = math.fsum(r['netPnlAmount'] for r in parts)
                    compounded = (math.prod(1 + r['returnPct'] / 100 for r in parts) - 1) * 100
                    same(amount, whole['netPnlAmount'], 'Complete partition PnL addition')
                    same(compounded, whole['returnPct'], 'Complete partition return compounding')
                    require(sum(r['observedSessions'] for r in parts) == whole['observedSessions']
                        and sum(r['opportunitiesByFormationDate']['formationTop5'] for r in parts) == len(original)
                        and sum(r['opportunitiesByFormationDate']['selectedSignalsFromFormation'] for r in parts) == len(schedule)
                        and sum(r['closedEpisodesByExitDate']['count'] for r in parts) == len(result['closedTrades']), 'Partition coverage bridge')
                    partition_checks.append({'kind': label, 'passed': True, 'intervals': len(parts),
                                             'netPnlAmount': amount, 'compoundedReturnPct': compounded})
                same(by_partition[(period, board, mode)], {'period': period, 'board': board, 'policy': mode,
                    'passed': True, 'checks': partition_checks}, 'Partition proof record')
            manifest = read(audit.pinned(receipt['sourceManifest']))
            cohort = deepcopy(manifest['boards'][board])
            cohort['accountData'] = {**cohort['accountData'], **receipt['accountData']}
            members = [r for r in manifest['records'] if r['board'] == board]
            cohort['selectionStatusCounts'] = ctx.labels(r['selectionStatus'] for r in members)
            cohort['selectionReasonCounts'] = ctx.labels(r['reason'] for r in members)
            layers = layer_reference(folder, receipt, original, daily, audit)
            expected_formation = {'period': period, 'board': board, 'sourceCohort': cohort, **layers}
            formation_row = next(r for r in report['formationCoverageRows'] if r['period'] == period and r['board'] == board)
            same(formation_row, expected_formation, 'All original source/leader/member/partial-state coverage')
            same(report['inputCountsByPeriodBoard'][period][board], receipt['counts'], 'All input counts retained')
            same(report['sourceBindings'][period][board], {**plan['sourceData'][period][board],
                'inputCompletion': completion['outputs'][period][board], 'dailyEvidence': receipt['files']['daily-evidence.jsonl.gz'],
                'originalOpportunities': receipt['files']['opportunities.jsonl.gz'], 'evidenceArtifacts': receipt['files']}, 'Complete source evidence links')
    require(order == [(r['period'], r['board'], r['formationId']) for r in observations]
            and checked == len(observations) * 2 and intervals == 180, 'Original sidecar and interval coverage')
    contrasts = contrast_reference(report['accounts'], audit)
    same(report['comparisons'], contrasts, 'All six full metric policy contrasts')
    same(report['primaryComparisons'], [r for r in contrasts if r['id'] == 'primary'], 'All primary contrasts')
    same(report['secondaryComparisons'], [r for r in contrasts if r['id'] == 'secondary'], 'All secondary contrasts')
    require(report['decision'] == {'selectedBoard': None, 'selectedPolicy': None, 'automaticNextStrategy': None, 'profitHurdle': None}
            and report['certified'] is False and report['targetAchieved'] is False, 'Unsupported promotion/certification')
    same(report['trialAccounting'], {'experimentBatches': 1, 'newSetupMechanisms': 1, 'newEntryPolicies': 2,
        'newUniverseVariants': 0, 'newAccountExecutions': 12, 'referenceAccountsReused': 0, 'summarizerAccountExecutions': 0}, 'Trial accounting')
    output = {'schema': 'leader-lag-followers-readout-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': audit.sha(__file__),
        'planSha256': audit.REGISTERED_PLAN_SHA, 'comparisonSha256': audit.sha(directory / 'comparison.json'),
        'accountValidationSha256': audit.sha(directory / 'validation.json'), 'readoutSha256': audit.sha(directory / 'readout.json'),
        'opportunityRowsArtifactSha256': audit.sha(sidecar_path),
        'independentReferences': {MAIN: MAIN_SHA, CONTEXT: CONTEXT_SHA, BOARD_READOUT: BOARD_READOUT_SHA},
        'inputPins': report['inputPins'], 'counts': {'fullCurveIntervals': 12, 'subIntervals': intervals,
            'originalMemberTop5': len(observations), 'originalPolicyDispositions': checked, **lengths},
        'checks': ['All original ordered members and two distinct policy schedule/entry/closed/open dispositions',
            'Independent full equity curves and all180 time intervals, carrying prior close equity and positions',
            'All sources, unknown/direction-failed evidence, formation versus decision denominators and complete PnL bridges',
            'All6 registered direction-minus-control full-metric policy contrasts, with null preservation'],
        'scope': {'productionSummarizerImported': False, 'accountDataParsed': False, 'accountsExecuted': 0, 'certified': False}}
    audit.write_proof(directory, 'readout-validation.json', output)


def self_test(audit, ctx, board_readout):
    dates = ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05']
    original = []
    for n, (day, state) in enumerate(zip(dates, ('passed', 'failed', 'unknown', 'passed'))):
        symbol, event = 'sh60000' + str(n), 'event:' + day
        sid = event + ':' + symbol
        decisions, signals = {}, {}
        for mode in audit.MODES:
            admitted = state != 'unknown' and (mode == 'control' or state == 'passed')
            decisions[mode] = {'status': 'unknown' if state == 'unknown' else 'included' if admitted else 'excluded',
                               'reason': 'test-' + state}
            signals[mode] = {'signalId': sid, 'strategyId': audit.SID, 'symbol': symbol,
                             'signalDate': day, 'originalRank': 1} if admitted else None
        original.append({'formationId': sid, 'eventId': event, 'symbol': symbol, 'formationDate': day,
            'peerFormationDate': '2023-12-19', 'originalRank': 1, 'decisions': decisions, 'signals': signals,
            'plannedNextEntryDate': dates[n + 1] if n < 3 else None, 'nextEntryWithinPeriod': n < 3})
    schedule = scheduled(original, 'control')
    result = {'config': {'initialCapital': 100.},
        'curve': [{'date': d, 'equity': value, 'cash': value, 'marketValue': 0., 'valuationReliable': True}
                  for d, value in zip(dates, (100., 80., 90., 99.))],
        'daily': [{'date': d, 'holdings': []} for d in dates], 'trades': [], 'closedTrades': [],
        'decisions': [{'date': dates[1], 'action': action, 'status': status, 'reason': reason} for action, status, reason in
                      (('sell', 'queued', 'count'), ('exit', 'queued', 'skip'), ('sell', 'rejected', 'skip'))],
        'research': {'identity': {'signalSchedule': schedule}, 'opportunities': [{**r, 'filledQuantity': 0,
            'status': 'notPlanned', 'reason': 'capacity'} for r in schedule]}}
    left = interval_reference(result, original, 'control', dates[0], dates[0], audit, ctx)
    right = interval_reference(result, original, 'control', dates[1], dates[-1], audit, ctx)
    audit.require(left['opportunitiesByFormationDate']['formationTop5'] == 1
        and left['opportunitiesByDecisionDate']['selectedSignals'] == 1
        and right['opportunitiesByFormationDate']['formationTop5'] == 3
        and right['opportunitiesByFormationDate']['selectedSignalsFromFormation'] == 2,
        'A cohort differs from prior F relation cutoff and unknown remains in denominator')
    audit.require(right['enteringEquity'] == 100. and right['maxDrawdownPct'] == 20.
        and math.isclose(right['returnPct'], -1.) and right['queuedExitReasons'] == {'count': 1}, 'Carried curve/queued sells differs')
    details = dispositions_reference(original, result, {'openPositions': {'count': 0, 'positions': []}}, 'control', audit, board_readout)
    audit.require(details[original[0]['formationId']]['positionState'] == 'notEntered'
        and details[original[2]['formationId']]['positionState'] == 'notScheduled'
        and details[original[2]['formationId']]['closedReturnPct'] is None
        and details[original[3]['formationId']]['nextEntryWithinPeriod'] is False, 'Unfilled, unknown, final cutoff distinguish')
    empty = deepcopy(result)
    empty['research'] = {'identity': {'signalSchedule': []}, 'opportunities': []}
    excluded = dispositions_reference([original[1]], empty, {'openPositions': {'count': 0, 'positions': []}}, 'direction', audit, board_readout)
    audit.require(next(iter(excluded.values()))['admissionStatus'] == 'excluded'
        and next(iter(excluded.values()))['closedPnlAmount'] is None, 'Filtered entry is not a closed zero')
    audit.require(add_counts([{'x': 1, 'nested': {'y': 2}}, {'x': 2, 'nested': {'y': 3}}])
        == {'x': 3, 'nested': {'y': 5}} and add_counts([]) == {}, 'Complete nested activation count aggregation')
    audit.require(audit.pinned({'path': BOARD_READOUT, 'sha256': BOARD_READOUT_SHA, 'passed': True}) == ROOT / BOARD_READOUT,
                  'Expanded descriptor rejected')
    import gzip
    from tempfile import TemporaryDirectory
    source = audit.synthetic_source()
    groups = []
    built = audit.reference_build(source, source['calendar'][100], source['calendar'][109], on_group=groups.append)
    with TemporaryDirectory(prefix='leader-lag-independent-readout-') as temp:
        folder = Path(temp)
        files = {}
        for filename, rows in (('rank-evidence.jsonl.gz', built['rankRows']), ('daily-evidence.jsonl.gz', built['dailyRows']),
                               ('leader-events.jsonl.gz', built['leaderEvents']), ('member-edges.jsonl.gz', built['memberEdges']),
                               ('opportunities.jsonl.gz', built['opportunities']), ('group-evidence.jsonl.gz', groups)):
            with gzip.open(folder / filename, 'wt', encoding='utf-8') as stream:
                for row in rows:
                    stream.write(json.dumps(row) + '\n')
            files[filename] = {'path': filename, 'sha256': audit.sha(folder / filename)}
        metadata = {key: value for key, value in built.items() if key not in ('rankRows', 'dailyRows', 'leaderEvents', 'memberEdges', 'opportunities', 'schedules')}
        (folder / 'signal-metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        layers = layer_reference(folder, {'counts': built['counts'], 'members': 40, 'files': files},
                                 built['opportunities'], built['dailyRows'], audit)
        audit.require(layers['fullGroupRecords'] == 40 and layers['groupCandidateEvaluationCount'] == 1560
            and layers['rawEdgeCount'] == 800 and layers['formationTop5'] == 50
            and layers['counts'] == built['counts'] and layers['rankDayCount'] == 39,
            'Four independent evidence layers, streamed candidates and warmup rank clock')
    print(json.dumps({'passed': True, 'groups': 7, 'accountsExecuted': 0, 'actualResultsRead': False}), flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    audit, ctx, board_readout = load(args.self_test)
    if args.self_test:
        self_test(audit, ctx, board_readout)
        return
    directory = args.directory.resolve()
    try:
        verify(directory, audit, ctx, board_readout)
    except Exception as error:
        folder = directory / 'validation-attempts'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = folder / (stamp + '-readout-failed.json')
        failure = {'passed': False, 'stage': 'readout', 'at': stamp, 'verifierSha256': audit.sha(__file__),
                   'error': str(error), 'traceback': traceback.format_exc(), 'accountsExecuted': 0}
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}), flush=True)
        raise


if __name__ == '__main__':
    main()
