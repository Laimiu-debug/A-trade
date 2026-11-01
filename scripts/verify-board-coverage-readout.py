"""Independent board-reclaim readout audit; no source or account execution."""
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
DEFAULT = ROOT / 'artifacts/strategy-search/board-coverage-reclaim-v1'
MAIN = 'scripts/verify-board-coverage-reclaim.py'
MAIN_SHA = '8d95ec533c901c7e47b6c23beb6567ae6760900d7be9e6038fff0d58853b6c29'
CONTEXT = 'scripts/verify-reclaim-context-diagnostic.py'
CONTEXT_SHA = '9b899e4c84ca90d2d26be4ef3a2ae2910029fa726441e23bed7ccbf6f48c67c9'


def load():
    spec = importlib.util.spec_from_file_location('board_readout_independent', ROOT / MAIN)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    audit.require(audit.sha(ROOT / MAIN) == MAIN_SHA, 'Frozen board source/input/account auditor changed')
    return audit, audit.reference(CONTEXT, CONTEXT_SHA)


def formation_reference(original, result, start, end, audit, ctx):
    opportunities = {r['signalId']: r for r in result['research']['opportunities']}
    audit.require(len(opportunities) == len(original) and set(opportunities) == {r['formationId'] for r in original},
                  'Readout complete admitted original formation stream')
    cohort = [r for r in original if start <= r['formationDate'] <= end]
    decisions = [opportunities[r['formationId']] for r in cohort]
    entered = sum(audit.finite(r.get('filledQuantity')) and r['filledQuantity'] > 0 for r in decisions)
    return {'formationTop5': len(cohort), 'selectedSignalsFromFormation': len(cohort), 'notScheduledFromFormation': 0,
        'enteredFromFormation': entered, 'notEnteredFromFormation': len(cohort) - entered,
        'uniqueFormationDays': len({r['formationDate'] for r in cohort}), 'uniqueSymbols': len({r['symbol'] for r in cohort}),
        'terminalStatusCounts': ctx.labels(r.get('status', 'missing') for r in decisions),
        'terminalReasonCounts': ctx.labels(r.get('reason', 'missing') for r in decisions),
        'meaning': 'formationDate cohort; terminal opportunity status is observed at account cutoff, not interval end'}


def interval_reference(result, original, start, end, audit, ctx):
    measured = ctx.curve_interval(result, start, end)
    queued = [r for r in result['decisions'] if start <= r['date'] <= end and r.get('action') == 'sell' and r.get('status') == 'queued']
    measured['queuedExitReasons'] = ctx.labels(r.get('reason', 'missing') for r in queued)
    measured['queuedExitDecisionCount'] = len(queued)
    measured['queuedExitDefinition'] = 'action=sell AND status=queued; queued decisions, partial fills and closed episodes differ'
    measured['opportunitiesByFormationDate'] = formation_reference(original, result, start, end, audit, ctx)
    return measured


def disposition_reference(result, audit):
    buys = {r['signalId']: r for r in result['trades'] if r['side'] == 'buy'}
    closed = {(r['symbol'], r['entryDate']): r for r in result['closedTrades']}
    audit.require(len(buys) == sum(r['side'] == 'buy' for r in result['trades']) and len(closed) == len(result['closedTrades']),
                  'Unique entry/closed episode identities')
    output = {}
    for r in result['research']['opportunities']:
        sid = r['signalId']
        buy = buys.get(sid)
        episode = closed.get((buy['symbol'], buy['date'])) if buy else None
        audit.require(sid not in output and (buy is not None) == (audit.finite(r.get('filledQuantity')) and r['filledQuantity'] > 0),
                      'Readout opportunity entry identity')
        output[sid] = {'signalId': sid, 'signalDate': r['signalDate'], 'entryDate': buy['date'] if buy else None,
            'exitDate': episode['exitDate'] if episode else None, 'positionState': 'closed' if episode else 'openAtCutoff' if buy else 'notEntered',
            'status': r['status'], 'reason': r['reason'], 'filledQuantity': r.get('filledQuantity'), 'attemptDate': r.get('attemptDate'),
            'closedReturnPct': episode.get('returnPct') if episode else None, 'closedPnlAmount': episode.get('pnlAmount') if episode else None}
    return output


def verify(directory, audit, ctx):
    require, same, read = audit.require, audit.same, audit.read
    require(not (directory / 'readout-validation.json').exists(), 'Readout validation already exists')
    plan = audit.registered_plan(directory)
    proof, report = read(directory / 'validation.json'), read(directory / 'readout.json')
    comparison, completion = read(directory / 'comparison.json'), read(directory / 'input-completion.json')
    require(proof['passed'] is True and proof['verifierSha256'] == MAIN_SHA
            and proof['planSha256'] == audit.REGISTERED_PLAN_SHA and proof['comparisonSha256'] == audit.sha(directory / 'comparison.json'), 'Account validation stale')
    require(report['schema'] == 'board-coverage-reclaim-readout-v1' and report['planSha256'] == audit.REGISTERED_PLAN_SHA
            and report['verification']['passed'] is True
            and audit.pinned(report['verification']) == (directory / 'validation.json').resolve(), 'Readout exact account validation binding')
    for name, expected in report['inputPins'].items():
        require(audit.sha(audit.local(name)) == expected, 'Readout input pin changed: ' + name)
    matrix = [(p, b) for p in audit.PERIODS for b in audit.BOARDS]
    require([(r['period'], r['board']) for r in report['accounts']] == matrix, 'Complete ordered six-account report')
    same(report['accountRows'], comparison['rows'], 'All board comparison rows retained')
    require(all(r['id'] == r['policy'] == 'immediate' for r in report['accounts']), 'Unregistered report policy')
    source_proof = audit.source_proof(directory)
    _, receipts = audit.input_receipts(directory, plan, source_proof)
    sidecar = report['opportunityRowsArtifact']
    require(sidecar['format'] == 'jsonl+gzip' and sidecar['identity'] == ['period', 'board', 'formationId'], 'Opportunity sidecar format/identity')
    opportunity_path = audit.pinned(sidecar)
    require(opportunity_path == (directory / 'opportunity-dispositions.jsonl.gz').resolve(), 'Readout sidecar path')
    observations = audit.records(opportunity_path)
    require(sidecar['count'] == len(observations) == len({(r['period'], r['board'], r['formationId']) for r in observations}), 'Complete unique opportunity sidecar')
    atlas_path = ROOT / 'artifacts/strategy-search/market-phase-atlas-v1/atlas.json'
    require(audit.sha(atlas_path) == 'ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8', 'Frozen hindsight atlas changed')
    atlas = read(atlas_path)['phases']
    expected_lengths = {'yearRows': 12, 'halfYearRows': 24, 'phaseRows': 48, 'curveEpochRows': 6, 'coverageRows': 6}
    require(all(len(report[k]) == v for k, v in expected_lengths.items()), 'Complete board period matrices')
    checked_rows, intervals = 0, 0
    for saved_account in report['accounts']:
        period, board = saved_account['period'], saved_account['board']
        folder, config = directory / period / board, plan['periods'][period]['config']
        receipt = receipts[period][board]
        original, daily = (audit.records(folder / n) for n in ('opportunities.jsonl.gz', 'daily-evidence.jsonl.gz'))
        result, summary, schedule, metadata = (read(folder / n) for n in
            ('immediate.result.json.gz', 'immediate.summary.json', 'immediate.signals.json', 'signal-metadata.json'))
        require(schedule == [r['signals']['immediate'] for r in original], 'Original immediate schedule lost or reordered')
        actual_rows = [r for r in observations if r['period'] == period and r['board'] == board]
        require([r['formationId'] for r in actual_rows] == [r['formationId'] for r in original], 'Sidecar original opportunity order')
        dispositions = disposition_reference(result, audit)
        account_opps = {r['signalId']: r for r in result['research']['opportunities']}
        opened = {(r['record']['symbol'], r['record']['entryDate']): r for r in summary['openPositions']['positions']}
        for formed, signal, actual in zip(original, schedule, actual_rows):
            sid = formed['formationId']
            detail = dispositions[sid]
            open_position = opened.get((formed['symbol'], detail['entryDate']))
            require((open_position is not None) == (detail['positionState'] == 'openAtCutoff'), 'Open episode linkage')
            expected = {'period': period, 'board': board, 'policy': 'immediate', 'formationId': sid,
                'formationDate': formed['formationDate'], 'symbol': formed['symbol'], 'originalRank': formed['originalRank'],
                'formationEvidence': formed, 'signal': signal, 'accountOpportunity': account_opps[sid],
                'accountDisposition': detail, 'openPositionAtCutoff': open_position}
            same(actual, expected, 'Every original formation/entry/exit/open disposition')
            checked_rows += 1
        overall = interval_reference(result, original, config['startDate'], config['endDate'], audit, ctx)
        same(saved_account['curveAttribution'], overall, 'Independent full board equity curve')
        same(saved_account['closedDistribution'], overall['closedEpisodesByExitDate'], 'Independent closed return/holding distribution')
        for target, source in (('account', 'account'), ('closedEpisodes', 'closedEpisodes'), ('unresolvedExits', 'unresolvedExits'),
                ('concentration', 'concentration'), ('reconciliation', 'reconciliation'), ('quality', 'quality'), ('rights', 'rights'), ('sourceQuality', 'sourceQuality')):
            same(saved_account[target], summary[source], 'Complete summary retained: ' + target)
        same(saved_account['configuredUniverseValues'], config['universe'], 'Compatibility configuration retained')
        same(saved_account['opportunityStatusCounts'], summary['opportunityDispositions']['opportunityCounts'], 'All terminal status categories')
        same(saved_account['sourceQualityScalarFlags'], {k: v for k, v in summary['sourceQuality'].items() if not isinstance(v, (dict, list))}, 'Source quality flags')
        same(saved_account['sourceQualityCoverage'], summary['sourceQuality'].get('coverage'), 'Source quality coverage')
        require(saved_account['warningCount'] == len(summary.get('warnings', [])) and saved_account['execution'] == 'new-independent-account', 'Warning count/execution mode')
        row = next(r for r in comparison['rows'] if (r['period'], r['board']) == (period, board))
        same(saved_account['comparisonRow'], row, 'Board comparison row exact copy')
        same(saved_account['artifactRefs'], comparison['artifacts'][period][board], 'Account full artifact references')
        terminal = deepcopy(summary['openPositions'])
        terminal['unrealizedPnlAmount'] = math.fsum(r['unrealizedPnlAmount'] for r in terminal['positions'])
        terminal['realizedPnlWhileOpen'] = math.fsum(r['realizedPnlWhileOpen'] for r in terminal['positions'])
        same(saved_account['openPositionsAtCutoff'], terminal, 'Complete terminal open positions')
        same(math.fsum([overall['closedEpisodesByExitDate']['netPnlAmount'], terminal['unrealizedPnlAmount'],
                       terminal['realizedPnlWhileOpen'], summary['reconciliation']['rightsAttributionGapAmount']]),
             overall['netPnlAmount'], 'Closed/open/rights total monetary bridge')
        for group in ('yearRows', 'halfYearRows', 'phaseRows', 'curveEpochRows'):
            rows = [r for r in report[group] if r['period'] == period and r['board'] == board]
            if group == 'curveEpochRows' and period == audit.PERIODS[0]:
                require(not rows, 'Old period contains unexpected user-event partition')
                continue
            windows = ([(r['requestedStart'], r['requestedEnd']) for r in summary['periods']
                if r['kind'] == ('calendarYear' if group == 'yearRows' else 'calendarHalfYear')]
                if group in ('yearRows', 'halfYearRows') else
                [(r['startDate'], r['endDate']) for r in atlas if r['startDate'] <= config['endDate'] and r['endDate'] >= config['startDate']]
                if group == 'phaseRows' else [('2024-01-02', '2024-09-23'), ('2024-09-24', '2025-12-31')])
            require([(r['requestedStart'], r['requestedEnd']) for r in rows] == windows, 'Complete original interval set: ' + group)
            for r in rows:
                expected = interval_reference(result, original, r['requestedStart'], r['requestedEnd'], audit, ctx)
                same({k: r[k] for k in expected}, expected, 'Independent interval full arithmetic: ' + group)
                intervals += 1
            same(math.fsum(r['netPnlAmount'] for r in rows), overall['netPnlAmount'], 'Calendar interval PnL addition')
            same((math.prod(1 + r['returnPct'] / 100 for r in rows) - 1) * 100, overall['returnPct'], 'Calendar interval compounding')
            require(sum(r['observedSessions'] for r in rows) == overall['observedSessions']
                    and sum(r['opportunitiesByFormationDate']['formationTop5'] for r in rows) == len(original), 'Complete interval source/formation partition')
        manifest = read(directory / period / 'source-manifest.json')
        cohort = deepcopy(manifest['boards'][board])
        cohort['accountData'] = {**cohort['accountData'], **receipt['accountData']}
        members = [r for r in manifest['records'] if r['board'] == board]
        cohort.update(selectionStatusCounts=ctx.labels(r['selectionStatus'] for r in members),
                      selectionReasonCounts=ctx.labels(r['reason'] for r in members), discoveredIdentityCount=len(members))
        same(saved_account['sourceCohort'], cohort, 'Board fixed cohort disclosure')
        totals = {key: sum(r['counts'][key] for r in daily) for key in daily[0]['counts']}
        same(saved_account['formationEvidenceTotals'], totals, 'Daily sequential formation-screen counts')
        coverage = next(r for r in report['coverageRows'] if r['period'] == period and r['board'] == board)
        expected_coverage = {'period': period, 'board': board, 'id': 'immediate', 'policy': 'immediate',
            **overall['opportunitiesByFormationDate'], **overall['opportunitiesByDecisionDate'],
            'buyFillCount': overall['buyFillCount'], 'sellFillCount': overall['sellFillCount'],
            'closedCount': len(result['closedTrades']), 'openCountAtCutoff': terminal['count'],
            'unresolvedExitCountAtCutoff': summary['unresolvedExits']['count'], 'curveSessions': len(metadata['targetDates']),
            'valuationUnreliableDays': len(overall['valuationUnreliableDates']), 'qualityScalarFlags': saved_account['sourceQualityScalarFlags'],
            'qualifiedMeaning': 'Observed execution states/reasons and inherited historical uncertainty, not certified exchange eligibility.',
            'sourceCohort': cohort, 'formationEvidenceTotals': totals,
            'benchmarkUnavailableDays': sum(not r['benchmarkPrior60']['complete'] for r in daily),
            'benchmarkReasonCounts': ctx.labels(r['benchmarkPrior60']['reason'] for r in daily),
            'formationEvidenceMeaning': 'Counts are symbol-day observations at each sequential formation screen; different fields have different denominators and must not be added as unique stocks.'}
        same(coverage, expected_coverage, 'Complete source/setup/execution coverage row')
        same(report['inputCountsByPeriodBoard'][period][board], receipt['counts'], 'All signal counts retained')
        same(report['sourceBindings'][period][board], {'inputCompletion': completion['outputs'][period][board],
            **{k: receipt[k] for k in ('accountData', 'accountDatasetDigest', 'sourceManifest', 'sourceValidation')},
            'dailyEvidence': receipt['files']['daily-evidence.jsonl.gz'], 'originalOpportunities': receipt['files']['opportunities.jsonl.gz']},
            'Every source binding retained')
    require(checked_rows == len(observations) == proof['counts']['signals'] and intervals == 90, 'Full readout coverage')
    require(report['decision'] == {'selectedBoard': None, 'selectedPolicy': None, 'automaticNextStrategy': None, 'profitHurdle': None}
            and report['certified'] is False and report['targetAchieved'] is False, 'Unsupported best-board or certification claim')
    output = {'schema': 'board-coverage-reclaim-readout-independent-validation-v1', 'passed': True,
        'verifiedAt': datetime.now(timezone.utc).isoformat(), 'verifierSha256': audit.sha(__file__),
        'planSha256': audit.REGISTERED_PLAN_SHA, 'comparisonSha256': audit.sha(directory / 'comparison.json'),
        'accountValidationSha256': audit.sha(directory / 'validation.json'), 'readoutSha256': audit.sha(directory / 'readout.json'),
        'opportunityRowsArtifactSha256': audit.sha(opportunity_path), 'independentReferences': {MAIN: MAIN_SHA, CONTEXT: CONTEXT_SHA},
        'inputPins': report['inputPins'], 'counts': {'accounts': 6, 'fullCurveIntervals': 6, 'subIntervals': intervals,
            'originalOpportunityDispositions': checked_rows, **expected_lengths},
        'checks': ['Six unique board/period accounts and all original ordered formation/signal/account/open-position dispositions',
            'Independent whole curves and all90 year/half/phase/event intervals including carried equity, exposure, duration and queued sells',
            'Closed/open/rights PnL, partition amount addition/compounding and complete terminal-state/quality disclosure',
            'All fixed cohort counts, sequential formation-screen denominators and source/artifact bindings'],
        'scope': {'productionSummarizerImported': False, 'accountDataParsed': False, 'accountsExecuted': 0, 'certified': False}}
    audit.write_proof(directory, 'readout-validation.json', output)


def self_test(audit, ctx):
    days = ['2024-01-02', '2024-01-03', '2024-01-04']
    result = {'config': {'initialCapital': 100.},
        'curve': [{'date': d, 'equity': eq, 'cash': eq / 2, 'marketValue': eq / 2, 'valuationReliable': True} for d, eq in zip(days, (100., 80., 90.))],
        'daily': [{'date': d, 'holdings': []} for d in days], 'trades': [], 'closedTrades': [],
        'research': {'identity': {'signalSchedule': []}, 'opportunities': []},
        'decisions': [{'date': days[1], 'action': a, 'status': s, 'reason': r} for a, s, r in
                      (('sell', 'queued', 'count'), ('sell', 'rejected', 'skip'), ('exit', 'queued', 'skip'))]}
    measured = interval_reference(result, [], days[1], days[2], audit, ctx)
    audit.require(measured['maxDrawdownPct'] == 20. and math.isclose(measured['returnPct'], -10.)
        and measured['enteringEquity'] == 100. and measured['queuedExitReasons'] == {'count': 1}, 'Independent carried equity/drawdown/queued-sell fixture')
    ref = {'path': MAIN, 'sha256': MAIN_SHA, 'passed': True, 'meaning': 'synthetic extended descriptor'}
    audit.require(audit.pinned(ref) == (ROOT / MAIN).resolve(), 'Extended verification descriptor supported')
    original = [{'formationId': 'a', 'formationDate': days[1], 'symbol': 'sh688001'}]
    result['research']['opportunities'] = [{'signalId': 'a', 'filledQuantity': 0, 'status': 'notPlanned', 'reason': 'slots'}]
    counted = formation_reference(original, result, days[0], days[-1], audit, ctx)
    audit.require(counted['formationTop5'] == counted['notEnteredFromFormation'] == counted['uniqueSymbols'] == 1, 'Unentered original opportunity denominator')
    print(json.dumps({'passed': True, 'groups': 3, 'accountsExecuted': 0}), flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    audit, ctx = load()
    if args.self_test:
        self_test(audit, ctx)
        return
    directory = args.directory.resolve()
    try:
        verify(directory, audit, ctx)
    except Exception as error:
        folder = directory / 'validation-attempts'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        failure = {'passed': False, 'stage': 'readout', 'verifierSha256': audit.sha(__file__), 'at': stamp,
                   'error': str(error), 'traceback': traceback.format_exc()}
        path = folder / (stamp + '-readout-failed.json')
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'passed': False, 'failure': str(path), 'error': str(error)}), flush=True)
        raise


if __name__ == '__main__':
    main()
