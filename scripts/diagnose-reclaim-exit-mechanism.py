"""Decompose unchanged saved exit accounts; no simulation or market-data reads."""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / 'artifacts/strategy-search'
PARENT = BASE / 'reclaim-volatility-trail-v1'
PREVIOUS = BASE / 'reclaim-relative-feedback-context-v1'
OUT = BASE / 'reclaim-exit-mechanism-v1'
PROTOCOL = ROOT / 'docs/reclaim-exit-mechanism-v1.json'
PERIODS = ('2022-2023', '2024-2025')
BOARDS = ('main', 'chinext', 'star')
POLICIES = ('trail10', 'volatilityTrail')
GROUPS = ('bothSameEntry', 'bothDifferentEntry', 'controlOnly', 'candidateOnly', 'neither')
STATES = ('closed', 'openAtCutoff', 'notEntered')
COMPONENTS = ('returnProfileComponent', 'committedCapitalComponent', 'candidateOnlyComponent', 'controlOnlyComponent')
BEFORE = 'd5c730ee782e319afcade4539944179c9d3157bf703a10c26582290c5e305c0e'
PREVIOUS_DELIVERY = 'dbb09b788e8165025da1f33c773e8abb1d1126734d82d4d6f8d3895b53c3307e'
SOURCES = ('scripts/diagnose-reclaim-exit-mechanism.py', 'service/research_exit_mechanism.py',
           'tests/test_research_exit_mechanism.py', 'scripts/verify-reclaim-exit-mechanism.py')


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8')) as stream:
        return json.load(stream)


def lines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def ref(path):
    return {'path': Path(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def bound(item):
    path = (ROOT / item['path']).resolve()
    require(not Path(item['path']).is_absolute() and path.is_relative_to(ROOT) and path.is_file(), 'Invalid reference')
    require(sha(path) == item['sha256'], 'Changed reference: ' + item['path'])
    return path


def near(a, b):
    require(type(a) in (int, float) and type(b) in (int, float) and math.isfinite(a) and math.isfinite(b)
            and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7), f'Numeric mismatch {a!r} {b!r}')


def verify_plan():
    plan = read(OUT / 'plan.json')
    require(plan['schema'] == OUT.name, 'Plan schema')
    for mapping in (plan['sourcePins'], plan['inputPins']):
        for name, digest in mapping.items():
            bound({'path': name, 'sha256': digest})
    return plan


def register():
    require(sha(BASE / 'program.json') == BEFORE and sha(PREVIOUS / 'delivery.json') == PREVIOUS_DELIVERY, 'Previous state differs')
    for directory in (PREVIOUS, PARENT):
        for name, digest in read(directory / 'delivery.json')['files'].items():
            bound({'path': name, 'sha256': digest})
    report = read(PARENT / 'readout.json')
    proof = read(PARENT / 'readout-validation.json')
    require(proof['passed'] and proof['readoutSha256'] == sha(PARENT / 'readout.json'), 'Saved readout audit')
    comparison = read(PARENT / 'comparison.json')
    protocol = read(PROTOCOL)
    paths = [PROTOCOL, PREVIOUS / 'delivery.json', PREVIOUS / 'validation.json', PREVIOUS / 'readout.json']
    paths += [PARENT / n for n in ('delivery.json', 'readout.json', 'comparison.json', 'readout-validation.json', 'validation.json')]
    paths += [bound(report['opportunityRowsArtifact'])]
    for period in PERIODS:
        for board in BOARDS:
            for policy in POLICIES:
                paths += [bound(item) for item in comparison['artifacts'][period][board][policy].values()]
    plan = {**protocol, 'registeredAt': now(), 'previousTurnClassification': 'progress',
            'programBeforeSha256': BEFORE, 'previousDelivery': ref(PREVIOUS / 'delivery.json'),
            'sourcePins': {name: sha(ROOT / name) for name in SOURCES},
            'inputPins': {p.relative_to(ROOT).as_posix(): sha(p) for p in paths},
            'accountArtifacts': comparison['artifacts'], 'accountReadout': ref(PARENT / 'readout.json'),
            'opportunities': report['opportunityRowsArtifact'], 'predecessorUnitTests': 994}
    OUT.mkdir(exist_ok=False)
    write(OUT / 'plan.json', plan)
    return ref(OUT / 'plan.json')


def pretests():
    verify_plan()
    started = now()
    commands = [[sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', 'tests.test_research_exit_mechanism', '-v'],
                [sys.executable, '-X', 'utf8', '-B', 'scripts/verify-reclaim-exit-mechanism.py', '--self-test']]
    results = []
    for i, command in enumerate(commands):
        result = subprocess.run(command, cwd=ROOT, text=True, encoding='utf-8', capture_output=True)
        log = OUT / f'pretest-{i+1}.log'
        with log.open('x', encoding='utf-8') as stream:
            stream.write(result.stdout + result.stderr)
        results.append({'command': command, 'exitCode': result.returncode, 'log': ref(log)})
    value = {'startedAt': started, 'completedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
             'commands': results, 'passed': all(r['exitCode'] == 0 for r in results)}
    write(OUT / 'pre-extraction-tests.json', value)
    require(value['passed'], 'Pretests failed')
    return {'passed': True}


def account_records(opportunities, summary, result, period, board, policy):
    require(summary['policyId'] == policy, 'Policy mismatch')
    require(not any(result.get(k) for k in ('actionLedger', 'cashReceivables', 'actionPayables', 'preExRights')),
            'Unexpected rights cashflow needs a different investment contract')
    buys = [r for r in result['trades'] if r['side'] == 'buy']
    buy_map = {r['signalId']: r for r in buys}
    require(len(buys) == len(buy_map) and all(r['positionAction'] == 'open' for r in buys), 'Multiple buys/top-ups')
    closed = {(e['record']['symbol'], e['record']['entryDate'], e['record']['exitDate']): e for e in summary['closedEpisodes']['episodes']}
    opened = {(e['record']['symbol'], e['record']['entryDate']): e for e in summary['openPositions']['positions']}
    require(len(closed) == summary['closedEpisodes']['count'] and len(opened) == summary['openPositions']['count'], 'Duplicate episode')
    used_closed, used_open, used_buys = set(), set(), set()
    output = {}
    for o in opportunities:
        sid, d = o['formationId'], o['accountDispositions'][policy]
        require(sid not in output and sid == d['signalId'] == o['signals']['immediate']['signalId'], 'Duplicate/different signal')
        require(o['formationDate'] == d['signalDate'] == o['signals']['immediate']['signalDate'], 'Signal clock mismatch')
        entered = d['filledQuantity'] > 0
        state = d['positionState'] if entered else 'notEntered'
        require(entered == (d['positionState'] in ('closed', 'openAtCutoff')), 'Filled maturity mismatch')
        n = {'entered': entered, 'entryDate': None, 'entryPrice': None, 'exitDate': None, 'positionState': state,
             'investedAmount': 0., 'closedPnlAmount': 0., 'openRealizedPnlAmount': 0., 'openUnrealizedPnlAmount': 0.,
             'holdingSessions': None, 'reason': d['reason'], 'originalPositionState': d['positionState'], 'openMarkStale': False}
        if entered:
            buy = buy_map[sid]
            require(buy['symbol'] == o['symbol'] and buy['date'] == d['entryDate'], 'Buy identity')
            near(buy['quantity'], d['filledQuantity'])
            used_buys.add(sid)
            if state == 'closed':
                key = (o['symbol'], d['entryDate'], d['exitDate'])
                require(key not in used_closed, 'Closed episode reused')
                e = closed[key]; used_closed.add(key)
                n.update(closedPnlAmount=e['pnlAmount'], holdingSessions=e['holdingSessions'], exitDate=d['exitDate'])
                near(e['pnlAmount'], d['closedPnlAmount'])
            else:
                key = (o['symbol'], d['entryDate'])
                require(key not in used_open, 'Open episode reused')
                e = opened[key]; used_open.add(key)
                n.update(openRealizedPnlAmount=e['realizedPnlWhileOpen'], openUnrealizedPnlAmount=e['unrealizedPnlAmount'],
                         holdingSessions=e['observedHoldingSessions'], openMarkStale=e['markStaleAtCutoff'])
            invested = e['record']['investedAmount']
            near(invested, buy['amount'] + buy['fee'])
            n.update(investedAmount=invested, entryDate=d['entryDate'], entryPrice=buy['price'])
        else:
            require(sid not in buy_map, 'Unentered signal has a fill')
        output[sid] = n
    require(used_buys == set(buy_map) and used_closed == set(closed) and used_open == set(opened), 'Unallocated original episode')
    assigned = math.fsum(r['closedPnlAmount'] + r['openRealizedPnlAmount'] + r['openUnrealizedPnlAmount'] for r in output.values())
    account = summary['account']
    residual = account['netPnlAmount'] - assigned
    near(residual, summary['reconciliation']['rightsAttributionGapAmount'])
    bridge = {'period': period, 'board': board, 'policy': policy, 'formationCount': len(output),
              'entered': len(buys), 'closed': len(closed), 'open': len(opened),
              'assignedPnl': assigned, 'unallocatedResidual': residual, 'netPnlAmount': account['netPnlAmount'],
              'initialCapital': account['initialCapital'], 'finalEquity': account['finalEquity'],
              'accountReturnPct': account['netReturnPct'], 'maxDrawdownPct': account['maxDrawdownPct'],
              'valuationUnreliableDays': len(summary['quality']['valuationUnreliableDates']),
              'numericUnknownCount': len(summary['quality']['numericUnknownPaths']),
              'integrityIssueCount': len(summary['quality']['integrityIssues']),
              'openMarkStaleCount': sum(r['openMarkStale'] for r in output.values()), 'passed': True}
    return output, bridge


def build():
    from service.research_exit_mechanism import pair_episode, aggregate
    plan = verify_plan()
    tests = read(OUT / 'pre-extraction-tests.json')
    require(tests['passed'] and tests['planSha256'] == sha(OUT / 'plan.json'), 'Tests not bound')
    write(OUT / 'extraction-start.json', {'startedAt': now(), 'preExtractionTests': ref(OUT / 'pre-extraction-tests.json')})
    original = lines(bound(plan['opportunities']))
    require(len(original) == 8155, 'Original denominator')
    all_pairs, bridges, differences = [], [], []
    matrices = {k: [] for k in ('groupRows', 'maturityRows', 'formationYearRows', 'formationEpochRows', 'closedPairTimingRows', 'closedPairBaselineOutcomeRows')}
    for period in PERIODS:
        for board in BOARDS:
            opportunities = [o for o in original if (o['period'], o['board']) == (period, board)]
            accounts, bs = {}, {}
            for policy in POLICIES:
                refs = plan['accountArtifacts'][period][board][policy]
                summary = read(bound(refs[policy + '.summary.json']))
                result_path = bound(refs[policy + '.result.json.gz'])
                result = read(result_path)
                semantic_digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'),
                                                ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
                require(summary['inputResultSha256'] == semantic_digest, 'Summary/result object mismatch')
                accounts[policy], bs[policy] = account_records(opportunities, summary, result, period, board, policy)
                bridges.append(bs[policy])
            selected = []
            for o in opportunities:
                sid, date = o['formationId'], o['formationDate']
                epoch = 'oldPeriod' if period == PERIODS[0] else ('before20240924' if date < '2024-09-24' else 'from20240924')
                paired = pair_episode(accounts['trail10'][sid], accounts['volatilityTrail'][sid])
                selected.append({'period': period, 'board': board, 'formationId': sid, 'symbol': o['symbol'],
                                 'formationDate': date, 'formationYear': date[:4], 'formationEpoch': epoch, **paired})
            all_pairs.extend(selected)
            identity = {'period': period, 'board': board}
            total = aggregate(selected)
            near(bs['trail10']['initialCapital'], bs['volatilityTrail']['initialCapital'])
            residual_delta = bs['volatilityTrail']['unallocatedResidual'] - bs['trail10']['unallocatedResidual']
            net_delta = bs['volatilityTrail']['netPnlAmount'] - bs['trail10']['netPnlAmount']
            near(total['deltaPnl'] + residual_delta, net_delta)
            differences.append({**identity, **total, 'residualDelta': residual_delta, 'accountNetPnlDelta': net_delta,
                                'controlNetPnl': bs['trail10']['netPnlAmount'], 'candidateNetPnl': bs['volatilityTrail']['netPnlAmount'],
                                'initialCapitalEach': bs['trail10']['initialCapital'], 'passed': True})
            specs = [('groupRows', 'group', GROUPS),
                     ('maturityRows', 'maturity', tuple(a + '__' + b for a in STATES for b in STATES)),
                     ('closedPairTimingRows', 'closedPairTiming', ('later', 'same', 'earlier')),
                     ('closedPairBaselineOutcomeRows', 'baselineOutcome', ('win', 'loss', 'zero'))]
            for name, key, values in specs:
                for value in values:
                    matrices[name].append({**identity, key: value, **aggregate([r for r in selected if r[key] == value])})
            for name, key, values in [('formationYearRows', 'formationYear', period.split('-')),
                                      ('formationEpochRows', 'formationEpoch', ('oldPeriod',) if period == PERIODS[0] else ('before20240924', 'from20240924'))]:
                for value in values:
                    for group in GROUPS:
                        matrices[name].append({**identity, key: value, 'group': group,
                                              **aggregate([r for r in selected if r[key] == value and r['group'] == group])})
            for name in matrices:
                parts = [r for r in matrices[name] if (r['period'], r['board']) == (period, board)]
                expected = aggregate([r for r in selected if r['closedPairTiming'] is not None]) if name in (
                    'closedPairTimingRows', 'closedPairBaselineOutcomeRows') else total
                require(sum(r['count'] for r in parts) == expected['count'], 'Partition count: ' + name)
                for money in (*COMPONENTS, 'controlPnl', 'candidatePnl', 'deltaPnl'):
                    near(math.fsum(r[money] for r in parts), expected[money])
    require(len(all_pairs) == 8155 and len(bridges) == 12 and len(differences) == 6, 'Account/opportunity matrix incomplete')
    for name, rows in matrices.items():
        require(len(rows) == plan['expectedRows'][name], 'Incomplete ' + name)
    pairs_path = OUT / 'pairs.jsonl.gz'
    with gzip.open(pairs_path, 'xt', encoding='utf-8', compresslevel=1) as stream:
        for r in all_pairs:
            stream.write(json.dumps(r, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    parent = read(bound(plan['accountReadout']))
    report = {'schema': OUT.name, 'generatedAt': now(), 'planSha256': sha(OUT / 'plan.json'),
              'inputPins': plan['inputPins'], 'pairs': ref(pairs_path), 'pairCount': len(all_pairs),
              'accountBridges': bridges, 'accountDifferenceRows': differences, **matrices,
              'curveEpochRows': deepcopy(parent['curveEpochRows']), 'sourceLimitations': parent['limitations'],
              'rules': plan['rules'], 'trialAccounting': plan['trialAccounting'], 'profitHurdles': None,
              'selectedRule': None, 'holdoutClaim': False, 'certified': False, 'targetAchieved': False}
    write(OUT / 'readout.json', report)
    return {'pairCount': len(all_pairs), 'readout': ref(OUT / 'readout.json')}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('mode', choices=('register', 'pretests', 'build'))
    mode = parser.parse_args().mode
    print(json.dumps({'register': register, 'pretests': pretests, 'build': build}[mode](), ensure_ascii=False))
