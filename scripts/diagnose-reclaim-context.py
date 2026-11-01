"""Freeze and run descriptive formation-context attribution, without account reruns."""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'strong-undercut-reclaim-timing-v1'
OUT = BASE / 'undercut-reclaim-context-diagnostic-v1'
PERIODS = ('2022-2023', '2024-2025')
POLICIES = ('immediate', 'delay2', 'confirm2')
BOUNDARY = '2024-09-24'
MODULE = 'service/research_reclaim_context.py'
TESTS = 'tests/test_research_reclaim_context.py'
PARENT_VALIDATION_SHA = '74e1e2eea635291342d615e702ae212fcb427303f2328bec142b33edc6400126'
PARENT_READOUT_SHA = 'b58f557029781de669ed7aa3c250c35b339c760250af7c3ec96109580cf25a90'
PROGRAM_SHA = 'ccbac55a5dd98b5e9028701e819e2e6775670a1172b192673c242d348291b727'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def ref(path):
    path = Path(path)
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def load(name):
    spec = importlib.util.spec_from_file_location('_context_' + name.replace('-', '_'), ROOT / 'scripts' / name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def near(a, b):
    assert math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6), (a, b)


def market_binding(period):
    """Verify existing small market artifacts against their source capture audit."""
    loader = load('load-market-context-screen.py')
    spec = loader.PERIODS[period]
    folder = BASE / spec['market']
    files = {}
    for name, expected in spec['marketPins'].items():
        path = folder / name
        assert sha(path) == expected, path
        files[path.relative_to(ROOT).as_posix()] = expected
    protocol, completion, audit = (read(folder / n) for n in
        ('protocol.json', 'completion.json', 'independent-verification.json'))
    source, metadata = (read(PARENT / period / n) for n in ('source.json', 'signal-metadata.json'))
    assert audit['passed'] and audit['failureCount'] == 0
    assert protocol['protocolHash'] == audit['protocolHash'] == completion['protocolHash']
    assert len(protocol['symbols']) == 3141 and protocol['symbols'] == sorted(set(protocol['symbols']))
    assert protocol['dates'] == metadata['targetDates']
    assert protocol['calendarHash'] == source['calendarEvidence']['calendarHash']
    for name, r in completion['files'].items():
        assert r['path'] == name and r['sha256'] == audit['resultFileHashes'][name]
    manifest_path = ROOT / protocol['registration']['input']['directory'] / 'manifest.json'
    assert sha(manifest_path) == protocol['inputManifestSha256'] == audit['inputManifestSha256']
    files[manifest_path.relative_to(ROOT).as_posix()] = sha(manifest_path)
    inputs = read(manifest_path)['inputs']
    assert inputs['symbols'] == protocol['symbols']
    assert inputs['fullDatasetContentSha256'] == source['sourceDatasetDigest']
    assert inputs['snapshotFingerprint'] == source.get('sourceSnapshotFingerprint', source.get('sourceDatasetFingerprint'))
    assert inputs['calendar']['sha256'] == protocol['calendarHash']
    with gzip.open(folder / 'daily.jsonl.gz', 'rt', encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream]
    assert [r['date'] for r in rows] == metadata['targetDates']
    for i, r in enumerate(rows):
        assert r['fixedPopulation'] == 3141 and r['protocolHash'] == protocol['protocolHash']
        assert r['calendarHash'] == protocol['calendarHash'] and r['inputManifestSha256'] == protocol['inputManifestSha256']
        assert r['inputFeatureFileSha256'] == protocol['inputFilesSha256']['features.jsonl.gz']
        assert r['matched5']['earlierDate'] == (rows[i - 5]['date'] if i >= 5 else None)
    return rows, {'files': files, 'rawSourceDatasetDigest': source['sourceDatasetDigest'],
        'rawSourceSnapshot': inputs['snapshotFingerprint'], 'calendarHash': protocol['calendarHash'],
        'fixedPopulation': 3141, 'dailyRows': len(rows), 'originalMarketAuditPassed': True,
        'rawFeatureAndRoleFilesReread': False, 'meaning': 'Daily values inherit pinned original independent feature/market audit.'}


def register():
    assert sha(BASE / 'program.json') == PROGRAM_SHA
    assert sha(PARENT / 'validation.json') == PARENT_VALIDATION_SHA
    assert sha(PARENT / 'readout.json') == PARENT_READOUT_SHA
    assert read(PARENT / 'validation.json')['passed']
    inputs = {str((PARENT / name).relative_to(ROOT).as_posix()): sha(PARENT / name)
        for name in ('plan.json', 'validation.json', 'readout.json', 'comparison.json', 'delivery.json')}
    bindings = {}
    for period in PERIODS:
        _, bindings[period] = market_binding(period)
        inputs.update(bindings[period]['files'])
        for name in ('source.json', 'signal-metadata.json'):
            path = PARENT / period / name
            inputs[path.relative_to(ROOT).as_posix()] = sha(path)
        for policy in POLICIES:
            for suffix in ('.summary.json', '.result.json.gz'):
                path = PARENT / period / (policy + suffix)
                inputs[path.relative_to(ROOT).as_posix()] = sha(path)
    source_files = ('scripts/diagnose-reclaim-context.py', MODULE, TESTS,
        'scripts/load-market-context-screen.py', 'scripts/summarize-undercut-reclaim-timing.py')
    plan = {'schema': OUT.name, 'registeredAt': datetime.now(timezone.utc).isoformat(),
        'previousTurnClassification': 'progress: batch30 completed three policies/six verified accounts; cross-period performance remains inconsistent.',
        'sourcePins': {p: sha(ROOT / p) for p in source_files}, 'frozenInputs': inputs, 'marketBindings': bindings,
        'parent': PARENT.name, 'policies': list(POLICIES), 'periods': list(PERIODS),
        'newAccounts': 0, 'newPolicyVariants': 0, 'newIndicators': 0, 'parameterSearch': False,
        'rules': {
            'population': 'All3803 existing frozen original formations and all three dispositions; no future outcome, current holding or execution-based deletion.',
            'clock': 'For formation T, use exact prior source-calendar day A=T-1. Same A for I/D/C regardless of their signal/fill dates.',
            'axes': 'Both axes use the same existing matched5 pairedCount intersection: positiveBreadthToday>0.5 and matchedBreadthChange5>0. Equality is nonmajority/nonimproving. Existing20-session positive-return breadth compared with five source sessions earlier, not a five-day stock return.',
            'unknown': 'No nearest-day or cross-period fill. Prior daily row missing, matched unavailable/empty or numeric evidence missing stays unknown. No extra minimum-coverage gate. First six target days lack usable prior matched data in these source artifacts.',
            'money': 'Assign each complete closed episode and terminal open-position realized/unrealized PnL to its original formation context. Sum context money to complete account money; report any unallocated residual separately, never assign it to a favored context.',
            'distributions': 'Closed distributions are conditional on both admission and completed exit. Open PnL/holding age and unentered formations remain separate. Profit factor and top-five share use net money; win/loss mean uses closed net return. No independent-observation significance claim.',
            'groups': 'Show all five states for every period/policy; also complete formation-year groups, including empty cells. These are cohort money contributions to initial500000, not filtered independent account returns.',
            'userBoundary': 'User-directed2024-09-24 policy turning point, supplied before this new diagnostic. Keep2022-2023 separate; split2024-2025 formation cohorts before/at-or-after boundary. No date-search or selection by resulting profitability.',
            'curveBoundary': 'For existing2024-2025 accounts, report unchanged continuous equity path2024-01-02..2024-09-23 and2024-09-24..2025-12-31. Later interval starts with actual09-23equity and carried holdings; no reset, liquidation or new account. Its PnL need not equal PnL of formations in that interval.',
            'followup': 'Use observations to select a question for a separately frozen future experiment; do not automatically promote any quadrant or use retrospective phase labels to gate trading.'},
        'policyEventSource': {'url': 'https://www.csrc.gov.cn/csrc/c106311/c7508374/content.shtml',
            'date': BOUNDARY, 'meaning': 'Official contemporaneous policy announcement supports an event boundary, not a proof of permanent prosperity or quantitative-fund identity.'},
        'limitations': ['All histories were previously inspected development data; no holdout claim or stable-profit certification.',
            'Fixed current-survivor mainboard sample, incomplete historical eligibility/corporate actions and simulated opening fills inherit original limitations.',
            'Attribution to an entry environment is descriptive: capacity, existing holdings, costs, exits and market paths interact.',
            'Closed-only and terminal open valuations have different maturity; neither constitutes unobserved future target probabilities.',
            'The2024-09-24boundary is an explanatory event cut, never a new signal or future-known market-state label.',
            'Do not combine separate2022-2023 and2024-2025 capital paths. Local price/volume does not identify institutional, retail or quantitative ownership.'],
        'certified': False, 'targetAchieved': False}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    print(json.dumps({'registered': True, **ref(OUT / 'plan.json')}), flush=True)


def run():
    from service.research_reclaim_context import build_context_rows, summarize_records
    plan = read(OUT / 'plan.json')
    for mapping in (plan['sourcePins'], plan['frozenInputs']):
        for name, expected in mapping.items():
            assert sha(ROOT / name) == expected, name
    parent = read(PARENT / 'readout.json')
    contexts, source_bindings, all_records, bridges, curves = {}, {}, [], [], []
    summaries = {}
    phase = load('summarize-undercut-reclaim-timing.py')
    for period in PERIODS:
        market, source_bindings[period] = market_binding(period)
        assert source_bindings[period] == plan['marketBindings'][period]
        metadata = read(PARENT / period / 'signal-metadata.json')
        contexts[period] = build_context_rows(metadata['calendar'], market, metadata['targetDates'])
        by_day = {row['formationDate']: row for row in contexts[period]}
        originals = [r for r in parent['opportunityRows'] if r['period'] == period]
        for policy in POLICIES:
            summary = read(PARENT / period / (policy + '.summary.json'))
            summaries[period + ':' + policy] = summary
            closed_map = {(e['record']['symbol'], e['record']['entryDate'], e['record']['exitDate']): e
                          for e in summary['closedEpisodes']['episodes']}
            open_map = {(e['record']['symbol'], e['record']['entryDate']): e for e in summary['openPositions']['positions']}
            assert len(closed_map) == summary['closedEpisodes']['count'] and len(open_map) == summary['openPositions']['count']
            seen_closed, seen_open, rows = set(), set(), []
            for formation in originals:
                d = formation['accountDispositions'][policy]
                is_closed, is_open = d['positionState'] == 'closed', d['positionState'] == 'openAtCutoff'
                record = {'period': period, 'policy': policy, 'formationId': formation['formationId'],
                    'formationDate': formation['formationDate'], 'symbol': formation['symbol'],
                    'state': by_day[formation['formationDate']]['state'], 'signalDate': d['signalDate'],
                    'entryDate': d['entryDate'], 'exitDate': d['exitDate'],
                    'entered': d['filledQuantity'] > 0, 'closed': is_closed, 'open': is_open,
                    'closedReturnPct': None, 'closedPnlAmount': 0., 'openUnrealizedPnlAmount': 0.,
                    'openRealizedPnlAmount': 0., 'openMarkStale': False, 'holdingSessions': None,
                    'reason': d['reason']}
                assert record['entered'] == (is_closed or is_open)
                if is_closed:
                    key = (formation['symbol'], d['entryDate'], d['exitDate'])
                    e = closed_map[key]
                    assert key not in seen_closed
                    seen_closed.add(key)
                    near(e['netReturnPct'], d['closedReturnPct'])
                    near(e['pnlAmount'], d['closedPnlAmount'])
                    record.update(closedReturnPct=e['netReturnPct'], closedPnlAmount=e['pnlAmount'], holdingSessions=e['holdingSessions'])
                if is_open:
                    key = (formation['symbol'], d['entryDate'])
                    e = open_map[key]
                    assert key not in seen_open
                    seen_open.add(key)
                    record.update(openUnrealizedPnlAmount=e['unrealizedPnlAmount'], openRealizedPnlAmount=e['realizedPnlWhileOpen'],
                        openMarkStale=e['markStaleAtCutoff'], holdingSessions=e['observedHoldingSessions'])
                rows.append(record)
            assert seen_closed == set(closed_map) and seen_open == set(open_map)
            money = math.fsum(r['closedPnlAmount'] + r['openRealizedPnlAmount'] + r['openUnrealizedPnlAmount'] for r in rows)
            net = summary['account']['netPnlAmount']
            near(money, net)
            bridges.append({'period': period, 'policy': policy, 'assignedPnlAmount': money, 'accountNetPnlAmount': net,
                'unallocatedResidual': net - money, 'formationCount': len(rows), 'entered': sum(r['entered'] for r in rows),
                'closed': len(seen_closed), 'open': len(seen_open), 'passed': True})
            all_records.extend(rows)
            if period == '2024-2025':
                result = read(PARENT / period / (policy + '.result.json.gz'))
                schedule = result['research']['identity']['signalSchedule']
                values = []
                for epoch, start, end in (('before20240924', '2024-01-02', '2024-09-23'),
                                         ('from20240924', BOUNDARY, '2025-12-31')):
                    row = phase.interval(result, start, end, 500000., originals, schedule)
                    row.update(period=period, policy=policy, phaseId=epoch)
                    values.append(row)
                    curves.append(row)
                near(sum(r['netPnlAmount'] for r in values), net)
                near((math.prod(1 + r['returnPct'] / 100 for r in values) - 1) * 100, summary['account']['netReturnPct'])
    aggregate = summarize_records(all_records, initial_capital=500000.)
    epoch_records = []
    for r in all_records:
        p = '2022-2023' if r['period'] == '2022-2023' else '2024-2025-before20240924' if r['formationDate'] < BOUNDARY else '2024-2025-from20240924'
        epoch_records.append({**r, 'period': p})
    epochs = summarize_records(epoch_records, initial_capital=500000.)['overallRows']
    assert len(all_records) == 3803 * 3 and len(aggregate['overallRows']) == 30
    assert len(aggregate['formationYearRows']) == 60 and len(epochs) == 45 and len(curves) == 6
    for bridge in bridges:
        grouped = [r for r in aggregate['overallRows'] if (r['period'], r['policy']) == (bridge['period'], bridge['policy'])]
        near(math.fsum(r['totalPnlAmount'] for r in grouped), bridge['accountNetPnlAmount'])
    write(OUT / 'contexts.json', contexts)
    with gzip.open(OUT / 'records.jsonl.gz', 'xt', encoding='utf-8') as stream:
        for row in all_records:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
    report = {'schema': OUT.name + '-readout', 'createdAt': datetime.now(timezone.utc).isoformat(),
        'planSha256': sha(OUT / 'plan.json'), 'sourceBindings': source_bindings,
        'inputPins': {**plan['frozenInputs'], **plan['sourcePins']}, 'contexts': ref(OUT / 'contexts.json'),
        'records': ref(OUT / 'records.jsonl.gz'), 'formationCount': 3803, 'recordCount': len(all_records),
        **aggregate, 'epochStateRows': epochs, 'curveEpochRows': curves, 'accountReconciliation': bridges,
        'sourceQualityByAccount': {key: {'sourceQuality': s['sourceQuality'], 'quality': s['quality'],
            'unresolvedExits': s['unresolvedExits'], 'concentration': s['concentration']} for key, s in summaries.items()},
        'newAccounts': 0, 'newPolicyVariants': 0, 'parameterSearch': False,
        'method': plan['rules'], 'limitations': plan['limitations'], 'certified': False, 'targetAchieved': False}
    for name, expected in report['inputPins'].items():
        assert sha(ROOT / name) == expected, name
    write(OUT / 'readout.json', report)
    print(json.dumps({'completed': True, **ref(OUT / 'readout.json'), 'formationCount': 3803,
        'recordCount': len(all_records), 'overallRows': 30, 'formationYearRows': 60, 'epochStateRows': 45, 'curveEpochRows': 6}), flush=True)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'run'))
    args = parser.parse_args()
    try:
        globals()[args.mode]()
    except Exception as error:
        if OUT.exists():
            path = OUT / ('failure-' + str(time.time_ns()) + '.json')
            write(path, {'mode': args.mode, 'type': type(error).__name__, 'message': str(error)})
        raise
