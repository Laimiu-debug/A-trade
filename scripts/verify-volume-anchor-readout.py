"""Independent volume-anchor report audit; no signal or account execution."""
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
DEFAULT = ROOT / 'artifacts/strategy-search/volume-anchor-acceptance-v1'
MAIN = 'scripts/verify-volume-anchor-acceptance.py'
MAIN_SHA = 'dc769b4d74c4b6a407d1e3c3ee2af5a0f8cbc20763c7b66a09be447eff9af23f'
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
    policies = Counter('scheduled' if row['formationId'] in account else row['acceptance']['status'] for row in formed)
    return {'formationTop5': len(formed), 'acceptanceStates': ctx.labels(r['acceptance']['status'] for r in formed),
        'policyDispositionCounts': {key: policies[key] for key in ('scheduled', 'failed', 'unknown', 'censored')},
        'selectedSignalsFromFormation': len(admitted), 'notScheduledFromFormation': len(formed) - len(admitted),
        'enteredFromFormation': entered, 'notEnteredFromFormation': len(admitted) - entered,
        'uniqueFormationDays': len({r['formationDate'] for r in formed}), 'uniqueSymbols': len({r['symbol'] for r in formed}),
        'terminalStatusCounts': ctx.labels(r.get('status', 'missing') for r in admitted),
        'terminalReasonCounts': ctx.labels(r.get('reason', 'missing') for r in admitted),
        'meaning': 'Cohort date is original A, including later decisions and cutoff dispositions; notScheduled differs from scheduled-but-notEntered.'}


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
                'positionState': 'notScheduled', 'status': 'notScheduled', 'reason': row['acceptance']['status'],
                'filledQuantity': 0, 'attemptDate': None, 'closedReturnPct': None, 'closedPnlAmount': None,
                'accountOpportunity': None, 'openPositionAtCutoff': None}
        else:
            detail = details[sid]
            position = opened.get((row['symbol'], detail['entryDate']))
            audit.require((position is not None) == (detail['positionState'] == 'openAtCutoff'), 'Open episode linkage mismatch')
            detail = {**detail, 'accountOpportunity': account[sid], 'openPositionAtCutoff': position}
        output[sid] = {'formationId': sid, 'formationDate': row['formationDate'],
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
            for name, control, candidate in (('primary', 'delay3', 'accept3'), ('secondary', 'immediate', 'delay3')):
                a, b = indexed[(period, board, control)], indexed[(period, board, candidate)]
                audit.require(a.keys() == b.keys(), 'Comparison metric key coverage')
                output.append({'period': period, 'board': board, 'id': name, 'control': control, 'candidate': candidate,
                    'controlMetrics': a, 'candidateMetrics': b,
                    'candidateMinusControl': {key: b[key] - a[key] if audit.finite(a[key]) and audit.finite(b[key]) else None for key in sorted(a)},
                    'meaning': 'Acceptance within the same complete-data delayed clock; full historical policy difference, not causal proof.'
                        if name == 'primary' else 'Combined waiting, known-data admission and account execution/path difference; not an isolated acceptance effect.'})
    return output


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
    require(report['schema'] == 'volume-anchor-acceptance-readout-v1' and report['planSha256'] == audit.REGISTERED_PLAN_SHA
            and report['verification']['passed'] is True
            and audit.pinned(report['verification']) == (directory / 'validation.json').resolve(), 'Report binding stale')
    for path, expected in report['inputPins'].items():
        require(audit.sha(audit.local(path)) == expected, 'Report input pin changed: ' + path)
    lengths = {'accounts': 18, 'accountRows': 18, 'comparisons': 12, 'primaryComparisons': 6, 'secondaryComparisons': 6,
        'yearRows': 36, 'halfYearRows': 72, 'phaseRows': 144, 'curveEpochRows': 18,
        'coverageRows': 18, 'formationCoverageRows': 6, 'partitionReconciliation': 18}
    require(all(len(report[key]) == count for key, count in lengths.items()), 'Complete 18-account/time/contrast matrix')
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
            == proof['counts']['anchorOpportunities'], 'Complete unique original anchors required')
    require(audit.sha(ROOT / ATLAS) == ATLAS_SHA, 'Original descriptive phase atlas changed')
    phases = read(ROOT / ATLAS)['phases']
    by_account = {(r['period'], r['board'], r['policy']): r for r in report['accounts']}
    by_coverage = {(r['period'], r['board'], r['policy']): r for r in report['coverageRows']}
    by_partition = {(r['period'], r['board'], r['policy']): r for r in report['partitionReconciliation']}
    require(len(by_account) == len(by_coverage) == len(by_partition) == 18, 'Repeated account/coverage/partition identity')
    checked, intervals = 0, 0
    order = []
    for period in audit.PERIODS:
        for board in audit.BOARDS:
            folder, receipt = directory / period / board, receipts[period][board]
            original, daily = (audit.records(folder / name) for name in ('opportunities.jsonl.gz', 'daily-evidence.jsonl.gz'))
            config = plan['periods'][period]['config']
            rows = [row for row in observations if row['period'] == period and row['board'] == board]
            require([r['formationId'] for r in rows] == [r['formationId'] for r in original], 'Original anchor sidecar order')
            for formed, saved in zip(original, rows):
                same({k: v for k, v in saved.items() if k != 'accountDispositions'}, {**formed, 'period': period, 'board': board},
                     'All original anchor evidence/steps/failures retained')
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
            totals = {key: sum(r['counts'][key] for r in daily) for key in daily[0]['counts']}
            unknowns = Counter()
            for day in daily:
                unknowns.update(day['unknownReasonCounts'])
            expected_formation = {'period': period, 'board': board, 'sourceCohort': cohort, 'formationTop5': len(original),
                'counts': receipt['counts'], 'formationEvidenceTotals': totals, 'unknownReasonCounts': dict(sorted(unknowns.items())),
                'uniqueFormationDays': len({r['formationDate'] for r in original}), 'uniqueSymbols': len({r['symbol'] for r in original}),
                'acceptanceStateCounts': dict(receipt['counts']['acceptance']),
                'knownFailedStepsInsideCensored': sum(r['acceptance']['knownFailCount'] for r in original if r['acceptance']['status'] == 'censored'),
                'unknownStepsInsideCensored': sum(r['acceptance']['unknownStepCount'] for r in original if r['acceptance']['status'] == 'censored'),
                'knownFailedStepsInsideUnknown': sum(r['acceptance']['knownFailCount'] for r in original if r['acceptance']['status'] == 'unknown'),
                'meaning': 'One source/anchor cohort per board-period; daily counts are symbol-day evaluations. Censoring takes precedence while partial failure/unknown evidence remains retained.'}
            formation_row = next(r for r in report['formationCoverageRows'] if r['period'] == period and r['board'] == board)
            same(formation_row, expected_formation, 'All original source/anchor/partial-state coverage')
            same(report['inputCountsByPeriodBoard'][period][board], receipt['counts'], 'All input counts retained')
            same(report['sourceBindings'][period][board], {**plan['sourceData'][period][board],
                'inputCompletion': completion['outputs'][period][board], 'dailyEvidence': receipt['files']['daily-evidence.jsonl.gz'],
                'originalOpportunities': receipt['files']['opportunities.jsonl.gz']}, 'Complete source evidence links')
    require(order == [(r['period'], r['board'], r['formationId']) for r in observations]
            and checked == len(observations) * 3 and intervals == 270, 'Original sidecar and interval coverage')
    contrasts = contrast_reference(report['accounts'], audit)
    same(report['comparisons'], contrasts, 'All twelve full metric policy contrasts')
    same(report['primaryComparisons'], [r for r in contrasts if r['id'] == 'primary'], 'All primary contrasts')
    same(report['secondaryComparisons'], [r for r in contrasts if r['id'] == 'secondary'], 'All secondary contrasts')
    require(report['decision'] == {'selectedBoard': None, 'selectedPolicy': None, 'automaticNextStrategy': None, 'profitHurdle': None}
            and report['certified'] is False and report['targetAchieved'] is False, 'Unsupported promotion/certification')
    same(report['trialAccounting'], {'experimentBatches': 1, 'newSetupMechanisms': 1, 'newEntryPolicies': 3,
        'newUniverseVariants': 0, 'newAccountExecutions': 18, 'referenceAccountsReused': 0, 'summarizerAccountExecutions': 0}, 'Trial accounting')
    output = {'schema': 'volume-anchor-acceptance-readout-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': audit.sha(__file__),
        'planSha256': audit.REGISTERED_PLAN_SHA, 'comparisonSha256': audit.sha(directory / 'comparison.json'),
        'accountValidationSha256': audit.sha(directory / 'validation.json'), 'readoutSha256': audit.sha(directory / 'readout.json'),
        'opportunityRowsArtifactSha256': audit.sha(sidecar_path),
        'independentReferences': {MAIN: MAIN_SHA, CONTEXT: CONTEXT_SHA, BOARD_READOUT: BOARD_READOUT_SHA},
        'inputPins': report['inputPins'], 'counts': {'fullCurveIntervals': 18, 'subIntervals': intervals,
            'originalAnchors': len(observations), 'originalPolicyDispositions': checked, **lengths},
        'checks': ['All original ordered anchors and three distinct policy schedule/entry/closed/open dispositions',
            'Independent full equity curves and all270 time intervals, carrying prior close equity and positions',
            'All sources, missing/censor/failed evidence, formation versus decision denominators and complete PnL bridges',
            'All12 registered primary/secondary full-metric policy contrasts, with null preservation'],
        'scope': {'productionSummarizerImported': False, 'accountDataParsed': False, 'accountsExecuted': 0, 'certified': False}}
    audit.write_proof(directory, 'readout-validation.json', output)


def self_test(audit, ctx, board_readout):
    data, start, end = audit.fixture_source(1)
    built = audit.reference_build(data, start, end)
    original = built['opportunities']
    dates = built['targetDates']
    schedule = built['schedules']['delay3']
    result = {'config': {'initialCapital': 100.},
        'curve': [{'date': d, 'equity': value, 'cash': value, 'marketValue': 0., 'valuationReliable': True}
                  for d, value in zip(dates, (100., 80., 90., 99.))],
        'daily': [{'date': d, 'holdings': []} for d in dates], 'trades': [], 'closedTrades': [],
        'decisions': [{'date': dates[1], 'action': action, 'status': status, 'reason': reason} for action, status, reason in
                      (('sell', 'queued', 'count'), ('exit', 'queued', 'skip'), ('sell', 'rejected', 'skip'))],
        'research': {'identity': {'signalSchedule': schedule}, 'opportunities': [{**r, 'filledQuantity': 0, 'status': 'notPlanned', 'reason': 'capacity'} for r in schedule]}}
    left = interval_reference(result, original, 'delay3', dates[0], dates[0], audit, ctx)
    right = interval_reference(result, original, 'delay3', dates[1], dates[-1], audit, ctx)
    audit.require(left['opportunitiesByFormationDate']['selectedSignalsFromFormation'] == 1
        and left['opportunitiesByDecisionDate']['selectedSignals'] == 0
        and right['opportunitiesByFormationDate']['formationTop5'] == 0
        and right['opportunitiesByDecisionDate']['selectedSignals'] == 1, 'Formation and actual decision cohorts conflated')
    audit.require(right['enteringEquity'] == 100. and right['maxDrawdownPct'] == 20.
        and math.isclose(right['returnPct'], -1.) and right['queuedExitReasons'] == {'count': 1}, 'Carried curve/queued sells differs')
    details = dispositions_reference(original, result, {'openPositions': {'count': 0, 'positions': []}}, 'delay3', audit, board_readout)
    audit.require(next(iter(details.values()))['positionState'] == 'notEntered', 'Unfilled scheduled anchor lost')
    unknown = deepcopy(original)
    unknown[0]['signals']['delay3'] = None
    unknown[0]['acceptance']['status'] = 'unknown'
    empty = deepcopy(result)
    empty['research'] = {'identity': {'signalSchedule': []}, 'opportunities': []}
    details = dispositions_reference(unknown, empty, {'openPositions': {'count': 0, 'positions': []}}, 'delay3', audit, board_readout)
    audit.require(next(iter(details.values()))['positionState'] == 'notScheduled'
        and next(iter(details.values()))['closedReturnPct'] is None, 'Unscheduled anchor becomes closed zero')
    audit.require(audit.pinned({'path': BOARD_READOUT, 'sha256': BOARD_READOUT_SHA, 'passed': True}) == ROOT / BOARD_READOUT,
                  'Expanded descriptor rejected')
    print(json.dumps({'passed': True, 'groups': 5, 'accountsExecuted': 0, 'actualResultsRead': False}), flush=True)


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
