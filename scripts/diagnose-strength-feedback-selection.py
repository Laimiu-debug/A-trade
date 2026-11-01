"""Describe observed baseline trades by the frozen positive-policy decision.

No counterfactual trade is fabricated and no account, signal or threshold is
changed. Baseline PnL assigned to excluded signals is not the new account's PnL.
"""
from argparse import ArgumentParser
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/strategy-search/strength-feedback-v1'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
STATES = ('included', 'excluded', 'unknown')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text('utf-8'))


def stats(rows):
    closed = [r for r in rows if r['positionState'] == 'closed']
    values = [r['closedReturnPct'] for r in closed]
    amounts = [r['closedPnlAmount'] for r in closed]
    assert all(type(v) in (int, float) and math.isfinite(v) for v in values + amounts)
    winning, losing = [v for v in values if v > 0], [v for v in values if v < 0]
    return {'originalOpportunities': len(rows), 'baselinePositionStates': dict(sorted(Counter(r['positionState'] for r in rows).items())),
        'baselineClosed': len(closed), 'baselineWinners': len(winning), 'baselineLosers': len(losing),
        'baselineFlat': len(values) - len(winning) - len(losing), 'baselineClosedPnlAmount': math.fsum(amounts),
        'baselineClosedMeanPct': mean(values) if values else None, 'baselineClosedMedianPct': median(values) if values else None,
        'baselineMeanWinnerPct': mean(winning) if winning else None, 'baselineMeanLoserPct': mean(losing) if losing else None,
        'baselineBestPct': max(values) if values else None, 'baselineWorstPct': min(values) if values else None}


def self_test():
    empty = stats([{'positionState': 'notEntered', 'closedReturnPct': None, 'closedPnlAmount': None}])
    assert empty['baselineClosed'] == 0 and empty['baselineClosedMeanPct'] is None
    rows = [{'positionState': 'closed', 'closedReturnPct': x, 'closedPnlAmount': x * 10} for x in (12, -3, 0)]
    result = stats(rows)
    assert result['baselineClosed'] == 3 and result['baselineClosedPnlAmount'] == 90
    assert result['baselineWinners'] == result['baselineLosers'] == result['baselineFlat'] == 1
    assert result['baselineClosedMeanPct'] == 3 and result['baselineClosedMedianPct'] == 0
    assert result['baselineMeanWinnerPct'] == 12 and result['baselineMeanLoserPct'] == -3
    return {'passed': True, 'checks': 5, 'actualResultsRead': False, 'persistentFilesWritten': 0}


def run():
    report, proof = read(OUT / 'readout.json'), read(OUT / 'readout-validation.json')
    assert proof['passed'] and proof['readoutSha256'] == sha(OUT / 'readout.json')
    ref = report['opportunityRowsArtifact']
    path = ROOT / ref['path']
    assert sha(path) == ref['sha256'] == proof['opportunityRowsArtifactSha256']
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        opportunities = [json.loads(line) for line in stream if line.strip()]
    assert len(opportunities) == ref['count']
    assert len({(r['period'], r['board'], r['formationId']) for r in opportunities}) == len(opportunities)
    groups, reconciliation = [], []
    for period in PERIODS:
        for board in BOARDS:
            source = [r for r in opportunities if (r['period'], r['board']) == (period, board)]
            assert all(r['decisions']['positive']['status'] in STATES for r in source)
            for state in STATES:
                selected = [r['accountDispositions']['baseline'] for r in source if r['decisions']['positive']['status'] == state]
                groups.append({'period': period, 'board': board, 'positiveAdmission': state, **stats(selected)})
            overall = stats([r['accountDispositions']['baseline'] for r in source])
            account = next(r for r in report['accounts'] if (r['period'], r['board'], r['policy']) == (period, board, 'baseline'))
            assert overall['baselineClosed'] == account['comparisonRow']['closed']
            assert math.isclose(overall['baselineClosedPnlAmount'], account['closedDistribution']['netPnlAmount'], rel_tol=1e-12, abs_tol=1e-7)
            parts = groups[-3:]
            for key in ('originalOpportunities', 'baselineClosed', 'baselineWinners', 'baselineLosers', 'baselineFlat'):
                assert sum(r[key] for r in parts) == overall[key]
            assert math.isclose(math.fsum(r['baselineClosedPnlAmount'] for r in parts), overall['baselineClosedPnlAmount'], rel_tol=1e-12, abs_tol=1e-7)
            reconciliation.append({'period': period, 'board': board, 'passed': True, **overall})
    result = {'schema': 'strength-feedback-selection-diagnostic-v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
        'passed': True, 'scriptSha256': sha(Path(__file__)), 'readoutSha256': sha(OUT / 'readout.json'),
        'readoutValidationSha256': sha(OUT / 'readout-validation.json'), 'opportunityRowsArtifact': ref,
        'rows': groups, 'reconciliation': reconciliation, 'newSignals': 0, 'newAccounts': 0, 'parameterChanges': 0,
        'meaning': 'Observed baseline closed episodes grouped by the already frozen positive admission. Excluded baseline PnL is a descriptive selection diagnostic, not an achievable avoided loss, new-policy return or independent causal estimate. Open and unentered opportunities remain separate. Overlapping dates/stocks are not independent samples.'}
    destination = OUT / 'selection-diagnostic.json'
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return {'passed': True, 'groups': len(groups), 'sourceReconciliations': len(reconciliation), 'sha256': sha(destination)}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else run(), ensure_ascii=False))
