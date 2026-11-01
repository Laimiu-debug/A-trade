"""Independent raw-price decomposition, matching and recorded-outcome audit.

Never imports the new rule or runner. Source loading uses the frozen read-only
universe loader; inherited event arithmetic is an explicitly pinned auditor.
This verifies a registered-cost daily-price diagnostic, not economic execution.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import gzip
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean, median
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / 'docs/signal-session-decomposition-v1.json'
REGISTRATION_SHA256 = '7b833df10d24aa37edd64eaa923c5858d59ba785e3fa7bca78cf3a28440c4cbc'
CANDIDATE_ID = 'rawRepeatedIntradayAdvance'
CONTROL_ID = 'control_' + CANDIDATE_ID
GROUPS = (CANDIDATE_ID, CONTROL_ID)
VERSION = 'raw-session-decomposition-v1'
KINDS = ('ineligible', 'unevaluable', 'notTriggered', 'intraday', 'overnight')
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
OLD_REFERENCE = 'scripts/verify-signal-market-repair-result.py'
AUDIT_DEPENDENCIES = {
    OLD_REFERENCE: '922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf',
    'scripts/verify-signal-persistent-leader-result.py': '3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222'}
NEW_FILES = {'scripts/run-signal-session-decomposition.py', 'service/signal_session_decomposition.py',
    'scripts/verify-signal-session-decomposition-result.py', 'tests/test_signal_session_decomposition.py',
    'tests/test_signal_session_decomposition_runner.py', 'tests/test_signal_session_decomposition_verifier.py'}
PANEL_FILES = {'manifest.json', 'features.jsonl.gz', 'daily-coverage.jsonl.gz', 'warmup-coverage.jsonl.gz',
               'summary.json', 'execution-sources.zip', 'attempt.json'}
ARTIFACTS = {'predicates': 'predicates.jsonl.gz', 'candidates': 'candidates.jsonl.gz', 'coverage': 'coverage.jsonl.gz',
    'selections': 'selection.jsonl.gz', 'matches': 'matches.jsonl.gz', 'signals': 'signals.jsonl.gz',
    'pairOutcomes': 'pair-outcomes.jsonl.gz', 'result': 'result.json.gz', 'summary': 'summary.json', 'attempt': 'attempt.json'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


VERIFIER_PATH = Path(__file__).resolve()
VERIFIER_SHA256 = sha(VERIFIER_PATH)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def parse(value):
    def unique(items):
        out = {}
        for key, item in items:
            if key in out:
                raise ValueError('Duplicate JSON key: '+key)
            out[key] = item
        return out
    def nonfinite(value):
        raise ValueError('Nonfinite JSON: '+value)
    return json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)


def read(path):
    return parse(Path(path).read_bytes())


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = parse(line)
            if not isinstance(row, dict):
                raise ValueError('A JSONL row must be an object')
            yield row


def number(value, minimum=None):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (minimum is None or value >= minimum)
    except OverflowError:
        return False


def positive(value):
    return number(value) and value > 0


def reference_path(symbol, day, days, raw, eligibility, halted=False):
    """Independent 21-slot formula reference, including classification reasons."""
    if not isinstance(eligibility, dict) or type(halted) is not bool:
        raise ValueError('Invalid eligibility/halt contract')
    quality = {'priceBasisId': 'raw-unadjusted', 'certified': False, 'targetAchieved': False,
        'actionEvidence': 'unverified', 'historicalEligibilityVerified': False, 'comparableRightsVerified': False,
        'unknownHistoricalFields': deepcopy(eligibility.get('unknown', []))}
    features = {}
    def done(kind, reason):
        signal = None
        if kind in ('intraday', 'overnight'):
            signal = {'version': VERSION, 'strategyId': CANDIDATE_ID if kind == 'intraday' else CONTROL_ID,
                'symbol': symbol, 'signalDate': day, 'eligible': True, 'decompositionClass': kind,
                'prior20MeanAmount': features['prior20MeanAmount'], 'features': deepcopy(features),
                'quality': deepcopy(quality), 'certified': False, 'targetAchieved': False}
        return {'version': VERSION, 'symbol': symbol, 'date': day, 'kind': kind, 'reason': reason,
            'features': features, 'quality': quality, 'eligibility': deepcopy(eligibility),
            'knownSuspended': halted, 'signal': signal}
    allowed, reason = eligibility.get('eligible'), eligibility.get('reason')
    hard = {'delisted', 'notListed', 'notHistoricalMember', 'historicalSt', 'insufficientListingHistory',
            'amount20BelowMinimum', 'amplitude20BelowMinimum'}
    if type(allowed) is not bool:
        return done('unevaluable', 'baseEligibilityUnknown')
    missing_amount = reason == 'amount20BelowMinimum' and not number(eligibility.get('amount20'), 0)
    missing_amplitude = reason == 'amplitude20BelowMinimum' and not number(eligibility.get('amplitude20'), 0)
    if allowed is False and reason in hard and not (missing_amount or missing_amplitude):
        features['baseReason'] = reason
        return done('ineligible', 'baseEligibilityRejected')
    if missing_amount or missing_amplitude:
        return done('unevaluable', 'baseAmountUnknown' if missing_amount else 'baseAmplitudeUnknown')
    if reason in {'missingDailyBar', 'notObservedYet', 'invalidHistory', 'strictHistoryMissing'} and not (halted and reason == 'missingDailyBar'):
        return done('unevaluable', reason)
    if halted:
        return done('unevaluable', 'suspensionContradictsPositiveVolume' if raw and isinstance(raw[-1], dict)
                    and positive(raw[-1].get('volume')) else 'knownSuspension')
    if reason == 'suspendedOrZeroVolume':
        return done('unevaluable', 'suspensionStatusUnknown')
    if not allowed:
        return done('unevaluable', 'baseEligibilityReasonUnknown')
    if len(days) != 21 or len(raw) != 21:
        return done('unevaluable', 'insufficient21SessionHistory')
    if days[-1] != day or days != sorted(set(days)):
        return done('unevaluable', 'invalidExchangeCalendarAlignment')
    for d, bar in zip(days, raw):
        if bar is None:
            return done('unevaluable', 'missingAlignedRawBar')
        if not isinstance(bar, dict) or bar.get('date') != d:
            return done('unevaluable', 'rawHistoryDateMismatch')
        if bar.get('priceBasisId') != 'raw-unadjusted':
            return done('unevaluable', 'rawPriceBasisUnknown')
        if 'knownSuspended' in bar and type(bar['knownSuspended']) is not bool:
            return done('unevaluable', 'invalidHistoricalHaltFlag')
        if bar.get('knownSuspended') is True:
            return done('unevaluable', 'historicalHaltContradictsPositiveVolume' if positive(bar.get('volume')) else 'historicalHaltInWindow')
        if any(not positive(bar.get(key)) for key in ('open', 'high', 'low', 'close', 'volume')):
            return done('unevaluable', 'nonpositiveOrUnknownRawOHLCV')
        if not (bar['low'] <= bar['open'] <= bar['high'] and bar['low'] <= bar['close'] <= bar['high']):
            return done('unevaluable', 'invalidRawOHLCEnvelope')
    if any(not positive(bar.get('amount')) for bar in raw[:20]):
        return done('unevaluable', 'prior20AmountUnknown')
    closes = [bar['close'] for bar in raw]
    try:
        returns = [math.log(closes[i]/closes[i-1]) for i in range(1, 21)]
        inside = [math.log(raw[i]['close']/raw[i]['open']) for i in range(1, 21)]
        gaps = [math.log(raw[i]['open']/closes[i-1]) for i in range(1, 21)]
        total = math.log(closes[-1]/closes[0])
        if not all(math.isfinite(value) for value in [total, *returns, *inside, *gaps]):
            return done('unevaluable', 'nonfiniteDerivedFeature')
        iv, ov, variation = math.fsum(inside), math.fsum(gaps), math.fsum(map(abs, returns))
    except (ValueError, OverflowError):
        return done('unevaluable', 'nonfiniteDerivedFeature')
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        peak, drawdown = Decimal(str(closes[0])), Decimal(0)
        for close in closes:
            value = Decimal(str(close))
            peak = max(peak, value)
            drawdown = max(drawdown, (peak-value)/peak)
        return_gate = Decimal(str(closes[-1])) >= Decimal('1.10')*Decimal(str(closes[0]))
    drawdown = float(drawdown)
    efficiency = total/variation if variation else 0.
    positive_gaps = sorted((g for g in gaps if g > 0), reverse=True)
    gap_sum = math.fsum(positive_gaps)
    ip = sum(raw[i]['close'] > raw[i]['open'] for i in range(1, 21))
    op = sum(raw[i]['open'] > closes[i-1] for i in range(1, 21))
    features.update(historyStart=days[0], historyEnd=day, returnStart=days[1], returnSessions=20,
        priorAmountEnd=days[-2], prior20MeanAmount=mean(b['amount'] for b in raw[:20]), startClose=closes[0],
        endClose=closes[-1], totalLogReturn20=total, simpleReturn20=closes[-1]/closes[0]-1,
        intradayLogReturn20=iv, overnightLogReturn20=ov, decompositionResidual=math.fsum((iv, ov, -total)),
        closeMaxDrawdown20=drawdown, signedEfficiency20=efficiency, positiveIntradaySessions=ip,
        positiveOvernightSessions=op, intradaySignedShare=iv/total if total else None,
        overnightSignedShare=ov/total if total else None,
        largestPositiveGapShare=positive_gaps[0]/gap_sum if positive_gaps else None,
        topThreePositiveGapShare=math.fsum(positive_gaps[:3])/gap_sum if positive_gaps else None)
    if any(not math.isfinite(v) for v in features.values() if type(v) in (int, float)):
        features.clear()
        return done('unevaluable', 'nonfiniteDerivedFeature')
    features['conditions'] = {'returnAtLeast10Pct': return_gate, 'drawdownAtMost15Pct': drawdown <= .15,
                              'efficiencyAtLeast035': efficiency >= .35}
    if not all(features['conditions'].values()):
        return done('notTriggered', 'commonUpwardPathNotMet')
    if iv >= (2/3)*total and ip >= 12:
        return done('intraday', 'repeatedIntradayAdvance')
    if ov >= (2/3)*total and op >= 12:
        return done('overnight', 'repeatedOvernightAdvance')
    return done('notTriggered', 'noDominantRepeatedComponent')


def reference_selection(day, signals):
    """Rank first, then deterministic greedy calipers; no future/active input."""
    ranked = {}
    for kind in ('intraday', 'overnight'):
        ranked[kind] = sorted((deepcopy(s) for s in signals if s['decompositionClass'] == kind),
                              key=lambda s: (-s['prior20MeanAmount'], s['symbol']))
        for rank, row in enumerate(ranked[kind], 1):
            row.update(rank=rank, selectedTop5=kind == 'intraday' and rank <= 5,
                       score=row['prior20MeanAmount'], rawScore=row['prior20MeanAmount'])
    selected, matches, used = [], [], set()
    for anchor in ranked['intraday'][:5]:
        feasible = []
        for control in ranked['overnight']:
            if control['symbol'] in used:
                continue
            a, b = anchor['features'], control['features']
            deltas = {key: abs(a[key]-b[key]) for key in ('totalLogReturn20', 'closeMaxDrawdown20', 'signedEfficiency20')}
            ratio = max(anchor['prior20MeanAmount'], control['prior20MeanAmount'])/min(anchor['prior20MeanAmount'], control['prior20MeanAmount'])
            deltas['logPrior20MeanAmount'] = math.log(ratio)
            if (deltas['totalLogReturn20'] <= .05 and deltas['closeMaxDrawdown20'] <= .05
                    and deltas['signedEfficiency20'] <= .15 and ratio <= 2):
                distance = math.fsum((deltas['totalLogReturn20']/.05, deltas['closeMaxDrawdown20']/.05,
                    deltas['signedEfficiency20']/.15, deltas['logPrior20MeanAmount']/math.log(2)))
                feasible.append((distance, control['symbol'], control, deltas, ratio))
        feasible.sort(key=lambda item: (item[0], item[1]))
        chosen = feasible[0] if feasible else None
        pair_id = hashlib.sha256(f"{VERSION}:{day}:{anchor['symbol']}".encode()).hexdigest()[:24]
        pair = {'pairId': pair_id, 'date': day, 'anchorSymbol': anchor['symbol'], 'anchorRank': anchor['rank'],
            'controlSymbol': chosen[1] if chosen else None, 'matched': chosen is not None,
            'distance': chosen[0] if chosen else None, 'absoluteFeatureDeltas': chosen[3] if chosen else None,
            'amountRatio': chosen[4] if chosen else None, 'feasibleUnusedControls': len(feasible),
            'reason': 'matched' if chosen else 'noUnusedControlWithinCalipers'}
        selected.append({**deepcopy(anchor), 'pairId': pair_id, 'pairedSymbol': pair['controlSymbol'], 'matchedAtSignal': bool(chosen)})
        if chosen:
            other = deepcopy(chosen[2])
            used.add(other['symbol'])
            other.update(rank=anchor['rank'], sourceClassRank=other['rank'], pairId=pair_id,
                pairedSymbol=anchor['symbol'], pairedStrategyId=CANDIDATE_ID, matchedAtSignal=True, selectedTop5=False)
            selected.append(other)
        matches.append(pair)
    return {'date': day, 'rankedIntraday': ranked['intraday'], 'rankedOvernight': ranked['overnight'],
        'signals': selected, 'pairs': matches, 'requestedCount': len(matches), 'matchedCount': len(used),
        'shortfall': len(matches)-len(used), 'activeExclusionApplied': False}


def private_reference(root):
    for name, expected in AUDIT_DEPENDENCIES.items():
        if sha(root/name) != expected:
            raise ValueError('Frozen independent reference differs: '+name)
    spec = importlib.util.spec_from_file_location('_session_audited_event_reference', root/OLD_REFERENCE)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    reference.CANDIDATE_ID, reference.GROUPS = CANDIDATE_ID, GROUPS
    return reference


def bind(reference, output, root, audit):
    """Independent immutable input/output closure; no production runner import."""
    audit.pin(REGISTRATION, REGISTRATION_SHA256)
    doc = read(REGISTRATION)
    audit.require(doc.get('candidateId') == CANDIDATE_ID and doc.get('controlId') == CONTROL_ID,
                  'registered-session-candidate')
    source, sample = doc['sourceBinding'], doc['sample']
    panel = (root/source['panelDirectory']).resolve()
    workspace = (root/source['workspace']).resolve()
    names = PANEL_FILES | {'completion.json', 'independent-verification.json'}
    audit.require(set(source['files']) == names and {p.name for p in panel.iterdir()} == names,
                  'source-panel-exact-inventory')
    files = {}
    for name in names:
        path = panel/name
        audit.require(path.is_file() and not path.is_symlink(), 'source-panel-regular-file')
        files[str(path)] = audit.pin(path, source['files'][name])
    manifest, receipt, proof = (read(panel/name) for name in ('manifest.json', 'completion.json', 'independent-verification.json'))
    inputs = manifest['inputs']
    symbols, calendar = inputs['symbols'], inputs['calendar']['dates']
    dates = [day for day in calendar if sample['signalStart'] <= day <= sample['signalEnd']]
    audit.require(symbols == sorted(set(symbols)) and len(symbols) == sample['expectedFullSecurities']
        and inputs['symbolCount'] == len(symbols) and inputs['symbolsSha256'] == digest(symbols)
        and set(inputs['securities']) == set(symbols), 'panel-fixed-symbol-identity')
    audit.require(calendar == sorted(set(calendar)) and inputs['calendar']['sha256'] == digest(calendar)
        and len(dates) == sample['expectedScoredSessions'] and len(calendar) == sample['expectedCalendarSessions']
        and len(calendar)-len(dates) == sample['expectedWarmupSessions']
        and all(datetime.strptime(day, '%Y-%m-%d').date().isoformat() == day
                and sample['warmupStart'] <= day <= sample['signalEnd'] for day in calendar), 'panel-calendar-identity')
    count = len(symbols)*len(dates)
    audit.require(manifest.get('featureVersion') == 'daily-leadership-raw-v1'
        and inputs['snapshotFingerprint'] == sample['expectedFullSnapshotFingerprint']
        and inputs['retainedBarCount'] == sample['expectedFullBars']
        and count == sample['expectedFeatureRows'], 'panel-sample-identity')
    audit.require(proof.get('passed') is True and type(proof.get('failureCount')) is int and proof['failureCount'] == 0
        and proof.get('sampleManifestHash') == receipt.get('sampleManifestHash') == source['files']['manifest.json']
        and proof.get('featureRows') == receipt.get('featureRows') == count
        and receipt.get('targetSessions') == len(dates), 'accepted-complete-panel-audit')
    audit.require(set(receipt['files']) == PANEL_FILES, 'panel-exact-receipt')
    for name in PANEL_FILES:
        audit.require(receipt['files'][name] == {'path': name, 'sha256': source['files'][name]}, 'panel-receipt:'+name)
    audit.require(manifest['executionArchiveSha256'] == source['files']['execution-sources.zip'], 'panel-whole-archive')
    reference.archive(audit, panel/'execution-sources.zip', manifest['implementation'], root)
    for name, expected in manifest['physicalSourceHashes'].items():
        path = Path(name)
        audit.require(path.is_absolute() and path.is_file(), 'physical-source-present')
        files[str(path.resolve())] = audit.pin(path, expected)
    audit.require(bool(manifest['physicalSourceHashes']) and str(workspace/'market.sqlite3') in files,
                  'physical-database-bound')
    files[str(REGISTRATION.resolve())] = REGISTRATION_SHA256
    identity = {'registrationSha256': REGISTRATION_SHA256, 'inputFilesSha256': files,
        'panelManifestSha256': source['files']['manifest.json'], 'panelCompletionSha256': source['files']['completion.json'],
        'actualPanelAuditSha256': source['files']['independent-verification.json'],
        'snapshotFingerprint': inputs['snapshotFingerprint'], 'fullDatasetContentSha256': inputs['fullDatasetContentSha256'],
        'symbolsSha256': inputs['symbolsSha256'], 'calendarHash': inputs['calendar']['sha256']}
    protocol = read(output/'protocol.json')
    audit.pin(output/'protocol.json')
    audit.require(protocol.get('schema') == 'session-decomposition-development-v1'
        and protocol.get('protocolHash') == digest({k:v for k,v in protocol.items() if k != 'protocolHash'})
        and protocol.get('registration') == doc and protocol.get('registrationSha256') == REGISTRATION_SHA256
        and protocol.get('binding') == identity and protocol.get('config') == doc['config']
        and protocol.get('workspace') == str(workspace) and protocol.get('strategyOrder') == [CANDIDATE_ID]
        and protocol.get('groups') == list(GROUPS), 'execution-protocol-input-identity')
    expected_implementation = set(doc['sourceImplementation']) | NEW_FILES | {REGISTRATION.resolve().relative_to(root).as_posix()}
    expected_implementation |= set(AUDIT_DEPENDENCIES) | {p.relative_to(root).as_posix() for p in (root/'service').glob('portfolio_*.py')}
    expected_implementation |= set(doc.get('implementationFreeze', {}).get('files', []))
    audit.require(set(protocol['implementation']) == expected_implementation, 'exact-execution-dependency-set')
    for name, expected in {**doc['sourceImplementation'], **AUDIT_DEPENDENCIES}.items():
        audit.require(protocol['implementation'][name] == expected, 'registered-dependency:'+name)
    audit.require(protocol['implementation']['scripts/verify-signal-session-decomposition-result.py'] == VERIFIER_SHA256,
                  'current-independent-verifier-archived')
    audit.pin(output/'execution-sources.zip', protocol['executionArchiveSha256'])
    reference.archive(audit, output/'execution-sources.zip', protocol['implementation'], root)
    audit.pin(output/'freeze-attempt.json', protocol['freezeAttemptSha256'])
    freeze = read(output/'freeze-attempt.json')
    audit.require(freeze.get('candidateId') == CANDIDATE_ID and freeze.get('registrationSha256') == REGISTRATION_SHA256,
                  'freeze-marker-identity')
    attempt = output/'attempts/attempt-0001'
    audit.require({p.name for p in (output/'attempts').iterdir()} == {'attempt-0001'} and
        {p.name for p in attempt.iterdir()} == set(ARTIFACTS.values()) | {'completion.json'}, 'single-complete-attempt')
    allowed = {'freeze-attempt.json', 'protocol.json', 'execution-sources.zip', 'events.jsonl', 'attempts'}
    for path in output.iterdir():
        report = path.name == 'independent-result-verification.json' or path.name.startswith('independent-result-verification-failed-') and path.suffix == '.json'
        audit.require(path.name in allowed or report, 'completed-output-inventory')
        audit.require(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()), 'output-not-linked')
    complete = read(attempt/'completion.json')
    audit.pin(attempt/'completion.json')
    mapping = {key: 'attempts/attempt-0001/'+name for key,name in ARTIFACTS.items()}
    mapping.update(protocol='protocol.json', executionSources='execution-sources.zip')
    audit.require(set(complete['files']) == set(mapping) and complete.get('attempt') == 1
        and complete.get('protocolHash') == protocol['protocolHash']
        and complete.get('snapshotFingerprint') == inputs['snapshotFingerprint']
        and complete.get('freezeAttemptSha256') == protocol['freezeAttemptSha256'], 'complete-exact-receipt-identity')
    for key,name in mapping.items():
        audit.require(complete['files'][key] == {'path': name, 'sha256': audit.pin(output/name)}, 'result-artifact:'+key)
    marker = read(attempt/'attempt.json')
    audit.require(marker.get('candidateId') == CANDIDATE_ID and marker.get('attempt') == 1
        and marker.get('protocolHash') == protocol['protocolHash'], 'attempt-marker-identity')
    journal = [parse(line) for line in (output/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    audit.pin(output/'events.jsonl')
    audit.require([row.get('event') for row in journal] == ['study_started', 'scoring_completed', 'study_succeeded']
        and all(row.get('protocolHash') == protocol['protocolHash'] for row in journal)
        and journal[-1].get('receipt') == complete, 'success-journal-receipt')
    for obj in (doc, protocol, freeze, marker, complete):
        audit.require(obj.get('certified') is False and obj.get('targetAchieved') is False, 'diagnostic-only-flags')
    return {'document': doc, 'panel': panel, 'manifest': manifest, 'symbols': symbols, 'calendar': calendar,
        'dates': dates, 'workspace': workspace, 'identity': identity, 'protocol': protocol, 'attempt': attempt,
        'journal': journal, 'completion': complete}


def load_source(reference, binding, root, audit):
    """Read the original source once, using only frozen loader/cutoff functions."""
    doc, sample = binding['document'], binding['document']['sample']
    sys.path.insert(0, str(root))
    universe = importlib.import_module('service.portfolio_universe')
    audit.pin(Path(universe.__file__), doc['sourceImplementation']['service/portfolio_universe.py'])
    helper = root/'scripts/run-strategy-search.py'
    audit.pin(helper, doc['sourceImplementation']['scripts/run-strategy-search.py'])
    namespace = reference.ast_functions(helper, {'bound_snapshot'}, {'board_of': universe.board_of,
        '_retain_causal_window': universe._retain_causal_window, '_digest': digest})
    raw = universe.snapshot_universe(binding['workspace'], sample['selection'], sample['signalEnd'], sample['signalStart'])
    data = namespace['bound_snapshot'](raw, sample['signalStart'], sample['signalEnd'])
    inputs = binding['manifest']['inputs']
    audit.require(data['fingerprint'] == sample['expectedFullSnapshotFingerprint']
        and digest(data) == inputs['fullDatasetContentSha256']
        and sorted(s['symbol'] for s in data['securities']) == binding['symbols']
        and len(data['securities']) == len(binding['symbols'])
        and sum(len(s['bars']) for s in data['securities']) == sample['expectedFullBars'], 'independently-loaded-raw-identity')
    calendar = data['calendar']
    audit.require(calendar == sorted(set(calendar)) and all(day <= sample['signalEnd'] for day in calendar)
        and [d for d in calendar if sample['warmupStart'] <= d <= sample['signalEnd']] == binding['calendar'], 'raw-calendar-identity')
    for security in [*data['securities'], data['benchmark']]:
        days = [bar['date'] for bar in security['bars']]
        audit.require(days == sorted(set(days)) and all(d <= sample['signalEnd'] for d in days), 'raw-unique-causal-dates')
    # Enumerate actual loader inputs independently so a newly added file cannot
    # hide behind the unchanged hashes of the previously present files.
    tdx = importlib.import_module('service.tdx')
    workspace = binding['workspace']
    with closing(sqlite3.connect((workspace/'market.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
        source = tdx.normalize_directory(json.loads(db.execute("SELECT value FROM meta WHERE key='path'").fetchone()[0]))
    paths = {workspace/'market.sqlite3'}
    for name in ('market.sqlite3-wal', 'research-history.sqlite3', 'research-history.sqlite3-wal'):
        if (workspace/name).is_file(): paths.add(workspace/name)
    for market in sample['selection']['markets']:
        for path in (source/'vipdoc'/market/'lday').glob('*.day'):
            stem = path.stem.lower()
            symbol = market+stem if len(stem) == 6 and stem.isdigit() else stem
            try:
                if tdx.symbol_parts(symbol)[0] == market and universe.board_of(symbol) == 'main':
                    paths.add(tdx.within(path, source))
            except ValueError:
                continue
    benchmark = tdx.daily_path(source, sample['selection']['benchmarkSymbol'])
    if benchmark.is_file(): paths.add(benchmark)
    for market in tdx.MARKETS:
        path = source/'T0002/hq_cache'/(market+'s.tnf')
        if path.is_file(): paths.add(path)
    audit.require({str(p.resolve()): audit.pin(p) for p in paths} == binding['manifest']['physicalSourceHashes'],
                  'exact-physical-input-inventory')
    return data


def replay(reference, binding, data, audit):
    """One causal pass over the full panel and each audit stream, through EOF."""
    securities = {s['symbol']: s for s in data['securities']}
    bars = {s: {b['date']: b for b in security['bars']} for s,security in securities.items()}
    calendar, inputs = data['calendar'], binding['manifest']['inputs']
    indices = {day:i for i,day in enumerate(calendar)}
    features = iter(records(binding['panel']/'features.jsonl.gz'))
    streams = {key: iter(records(binding['attempt']/ARTIFACTS[key]))
               for key in ('predicates', 'candidates', 'coverage', 'selections', 'matches', 'signals')}
    signals, matches, totals, reasons = [], [], Counter(), Counter()
    notice = time.monotonic()
    for ordinal, day in enumerate(binding['dates'], 1):
        slots = calendar[max(0, indices[day]-20):indices[day]+1]
        classified, daily_counts, daily_reasons = [], Counter(), Counter()
        for symbol in binding['symbols']:
            row = next(features, None)
            source_hashes = {**inputs['securities'][symbol], 'benchmarkRawBarsSha256': inputs['benchmark']['rawBarsSha256']}
            audit.require(isinstance(row, dict) and row.get('asOfDate') == day and row.get('symbol') == symbol
                and row.get('featureVersion') == 'daily-leadership-raw-v1'
                and row.get('sampleManifestHash') == binding['identity']['panelManifestSha256']
                and row.get('calendarHash') == binding['identity']['calendarHash']
                and row.get('sourceHashes') == source_hashes, 'independent-panel-row-identity')
            statuses = securities[symbol].get('tradeStatusByDate', {})
            def halt(d):
                value = statuses.get(d)
                return value is False or type(value) is int and value == 0 or value == '0'
            raw = [{**bars[symbol][d], 'priceBasisId': 'raw-unadjusted', 'knownSuspended': halt(d)}
                   if d in bars[symbol] else None for d in slots]
            expected = reference_path(symbol, day, slots, raw, row.get('baseEligibility'), halt(day))
            daily_counts[expected['kind']] += 1
            daily_reasons[expected['reason']] += 1
            if expected['signal'] is not None:
                classified.append(expected['signal'])
            record = {key:value for key,value in expected.items() if key != 'signal'}
            record.update(panelManifestSha256=binding['identity']['panelManifestSha256'],
                          sourceHashes=source_hashes, calendarHash=binding['identity']['calendarHash'])
            audit.check(reference.exact(next(streams['predicates'], None), record),
                        'independent-raw-predicate-row', date=day, symbol=symbol)
        selected = reference_selection(day, classified)
        for kind, key in (('intraday', 'rankedIntraday'), ('overnight', 'rankedOvernight')):
            for candidate in selected[key]:
                audit.check(reference.exact(next(streams['candidates'], None), {**candidate, 'classification': kind}),
                            'independent-class-ranking', date=day, symbol=candidate['symbol'])
        audit.check(reference.exact(next(streams['selections'], None), selected), 'independent-greedy-selection', date=day)
        for pair in selected['pairs']:
            audit.check(reference.exact(next(streams['matches'], None), pair), 'independent-original-pair', date=day)
        for signal in selected['signals']:
            audit.check(reference.exact(next(streams['signals'], None), signal), 'independent-selected-signal', date=day)
        daily = {'date': day, 'evaluatedSecurities': len(binding['symbols']),
            'classificationCounts': {k:daily_counts[k] for k in KINDS}, 'reasonCounts': dict(daily_reasons),
            'intradayTop5': selected['requestedCount'], 'matchedPairs': selected['matchedCount'],
            'unmatchedAnchors': selected['shortfall'], 'certified': False}
        audit.check(reference.exact(next(streams['coverage'], None), daily), 'independent-full-population-coverage', date=day)
        signals.extend(selected['signals'])
        matches.extend(selected['pairs'])
        totals.update(daily_counts)
        reasons.update(daily_reasons)
        if time.monotonic()-notice >= 30:
            print(f"Independently checked {ordinal}/{len(binding['dates'])} session-decomposition days", flush=True)
            notice = time.monotonic()
    audit.require(next(features, None) is None, 'source-panel-exact-EOF')
    for key, stream in streams.items():
        audit.require(next(stream, None) is None, 'output-exact-EOF:'+key)
    matched = sum(pair['matched'] for pair in matches)
    totals = {'scoredSessions': len(binding['dates']), 'evaluatedSecuritySessions': sum(totals.values()),
        'classificationCounts': {k:totals[k] for k in KINDS}, 'reasonCounts': dict(reasons),
        'intradayTop5': len(matches), 'matchedPairs': matched, 'unmatchedAnchors': len(matches)-matched,
        'controlSignalCount': sum(s['strategyId'] == CONTROL_ID for s in signals)}
    return signals, matches, totals


def audit_pairs(reference, result, signals, matches, attempt, audit):
    identity = lambda row: (row['strategyId'], row['symbol'], row['signalDate'])
    events = {identity(e): e for e in result['events']}
    suppressed = {identity(e): e for e in result['overlapSuppressed']}
    stream = iter(records(attempt/'pair-outcomes.jsonl.gz'))
    expected_rows = []
    def side(group, symbol, day):
        if symbol is None:
            return {'status': 'unmatched', 'symbol': None, 'eventId': None, 'entryStatus': None}
        key = group, symbol, day
        if key in suppressed:
            s = suppressed[key]
            return {'status': 'activeSuppressed', 'symbol': symbol, 'eventId': None, 'entryStatus': None,
                'reason': s['reason'], 'blockedByEventId': s['blockedByEventId'], 'activeUntil': s['activeUntil']}
        event = events[key]
        return {'status': 'attempted', 'symbol': symbol, 'eventId': event['eventId'],
            'entryStatus': event['entry']['status'], 'entryReason': event['entry'].get('reason'),
            'entryDate': event['entry'].get('date'), 'targetDate': event['outcomes']['nextOpenNet30']['hitDate']}
    for pair in matches:
        a = side(CANDIDATE_ID, pair['anchorSymbol'], pair['date'])
        c = side(CONTROL_ID, pair['controlSymbol'], pair['date'])
        expected = {**pair, 'anchorDisposition': a, 'controlDisposition': c,
            'bothFilled': a['entryStatus'] == c['entryStatus'] == 'filled',
            'pairingInterpretation': 'original-pre-active-pair;all-dispositions-retained;not-independent-observations'}
        audit.check(reference.exact(next(stream, None), expected), 'independent-original-pair-dispositions', pairId=pair['pairId'])
        expected_rows.append(expected)
    audit.require(next(stream, None) is None, 'pair-outcomes-exact-EOF')
    accounting = {'anchorSlots': len(matches), 'matchedPairs': sum(p['matched'] for p in matches),
        'unmatchedAnchors': sum(not p['matched'] for p in matches), 'bothFilled': sum(p['bothFilled'] for p in expected_rows)}
    for role in ('anchor', 'control'):
        sides = [r[role+'Disposition'] for r in expected_rows]
        accounting[role+'Dispositions'] = dict(Counter(s['status'] for s in sides))
        accounting[role+'EntryStatuses'] = dict(Counter(s['entryStatus'] for s in sides if s['entryStatus'] is not None))
    audit.check(reference.exact(result.get('pairAccounting'), accounting), 'independent-pair-accounting')
    matched_keys = {(group, p['anchorSymbol'] if group == CANDIDATE_ID else p['controlSymbol'], p['date'])
                    for p in matches if p['matched'] for group in GROUPS}
    audit.require(len(matched_keys) == 2*accounting['matchedPairs'], 'original-matched-universe-not-fill-filtered')
    return accounting, matched_keys


def matched_horizons(result, signals, matched_keys, dates):
    """Keep original signal dispositions alongside each mature filled cohort."""
    index = {d:i for i,d in enumerate(dates)}
    ident = lambda r: (r['strategyId'], r['symbol'], r['signalDate'])
    output = []
    for group in GROUPS:
        selected = [s for s in signals if s['strategyId'] == group and ident(s) in matched_keys]
        events = [e for e in result['events'] if e['strategyId'] == group and ident(e) in matched_keys]
        entered = [e for e in events if e['entry']['status'] == 'filled']
        row = {'strategyId': group, 'originalMatchedSignals': len(selected), 'attemptedEvents': len(events),
            'activeSuppressed': len(selected)-len(events), 'filledEvents': len(entered), 'unfilledEvents': len(events)-len(entered),
            'horizons': []}
        for horizon in HORIZONS:
            mature = [e for e in entered if len(dates)-index[e['entry']['date']] >= horizon]
            valid = []
            for e in mature:
                stop = min(horizon, e['outcomes']['nextOpenNet30']['tradingDays'] or horizon)
                if e['entry']['executionValid'] is True and not any(g['tradingDay'] <= stop for g in e['observation']['missingDates']) and not any(
                        q.get('tradingDay', 1) <= stop for q in e['quality']['executionCapacityIssues']):
                    valid.append(e)
            times = [e['outcomes']['nextOpenNet30']['tradingDays'] for e in valid
                     if e['outcomes']['nextOpenNet30']['tradingDays'] is not None and e['outcomes']['nextOpenNet30']['tradingDays'] <= horizon]
            unknown = len(mature)-len(valid)
            row['horizons'].append({'tradingDays': horizon, 'completeCalendarCohort': len(mature),
                'eligibleEntryCohort': len(valid), 'missingOrInvalidDataExcluded': unknown,
                'rightCensoredBeforeHorizon': len(entered)-len(mature), 'hits': len(times),
                'hitRate': len(times)/len(valid) if valid else None, 'medianHitTradingDays': median(times) if times else None,
                'missingOutcomeBounds': {'lower': len(times)/len(mature) if mature else None,
                    'upper': (len(times)+unknown)/len(mature) if mature else None, 'denominator': len(mature)}})
        output.append(row)
    return output


def verify(output: Path, root: Path = ROOT):
    output, root = Path(output).resolve(), Path(root).resolve()
    started = time.monotonic()
    report = {'schema': 'session-decomposition-independent-verification-v1', 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'passed': False, 'verificationCompleted': False, 'candidateId': CANDIDATE_ID, 'registrationSha256': REGISTRATION_SHA256,
        'verifierSha256': VERIFIER_SHA256, 'certified': False, 'targetAchieved': False,
        'actualOpeningExecutionVerified': False, 'comparableRightsVerified': False,
        'scope': 'independent raw formula/full population/greedy selection/dispositions/registered-cost outcome arithmetic',
        'limitations': ['Recorded exit arithmetic and path ordering do not certify first feasible auction fill or capacity.',
            'Matched presignal subsets are descriptive; overlapping same-stock/date events are not independent trials.']}
    audit = None
    try:
        reference = private_reference(root)
        audit = reference.Audit()
        binding = bind(reference, output, root, audit)
        data = load_source(reference, binding, root, audit)
        original_digest = digest(data)
        signals, matches, totals = replay(reference, binding, data, audit)
        with gzip.open(binding['attempt']/'result.json.gz', 'rt', encoding='utf-8') as stream:
            result = parse(stream.read())
        summary = read(binding['attempt']/'summary.json')
        for flag in ('certified', 'targetAchieved'):
            audit.require(result.get(flag) is False and summary.get(flag) is False, 'result-diagnostic-only:'+flag)
        audit.require(result.get('protocolHash') == binding['protocol']['protocolHash']
            and result.get('snapshotFingerprint') == binding['identity']['snapshotFingerprint']
            and result.get('strategyOrder') == [CANDIDATE_ID] and result.get('diagnosticUnverifiedHistory') is True,
            'result-registered-identity')
        audit.check(reference.exact(result.get('classificationCounts'), totals['classificationCounts']), 'result-classification-counts')
        base_summary = {k:v for k,v in result.items() if k not in ('events', 'overlapSuppressed', 'pathRiskEvents')}
        audit.check(reference.exact({k:v for k,v in summary.items() if k not in ('performance', 'ranking', 'selectionEligible')}, base_summary),
                    'summary-mirrors-result')
        audit.check(summary.get('ranking') is None and summary.get('selectionEligible') is False, 'no-ranked-certification')
        for key, value in totals.items():
            audit.check(reference.exact(summary['performance'].get(key), value), 'performance:'+key)
            audit.check(reference.exact(binding['journal'][1].get(key), value), 'journal-scoring:'+key)
        for key in ('scoringSeconds', 'eventEvaluationSeconds'):
            audit.check(number(summary['performance'].get(key), 0), 'nonnegative-timing:'+key)
        adapted = {'originalRawSourcePanelBinding': {'targetDates': binding['dates']},
                   'frozenExecutionConfig': binding['document']['config']}
        report['eventsChecked'] = reference.event_audit(audit, result, signals, data, adapted, root)
        accounting, matched_keys = audit_pairs(reference, result, signals, matches, binding['attempt'], audit)
        report.update(pairAccounting=accounting, matchedAtSignalHorizonArithmetic=matched_horizons(result, signals, matched_keys, binding['dates']),
            classificationCounts=totals['classificationCounts'], evaluatedSecuritySessions=totals['evaluatedSecuritySessions'],
            targetSessions=len(binding['dates']), snapshotFingerprint=data['fingerprint'], protocolHash=binding['protocol']['protocolHash'],
            completionSha256=sha(binding['attempt']/'completion.json'), resultSha256=sha(binding['attempt']/'result.json.gz'),
            implementation=binding['protocol']['implementation'])
        audit.require(digest(data) == original_digest, 'source-object-unmodified')
        for path, expected in audit.bound.items():
            audit.require(sha(path) == expected, 'bound-file-unchanged:'+path.name)
        report['verificationCompleted'] = True
        report['passed'] = audit.failure_count == 0
    except Exception as exc:
        report.update(errorType=type(exc).__name__, error=str(exc))
    report.update(checks=audit.checks if audit else 0, failureCount=audit.failure_count if audit else 1,
        issues=audit.issues if audit else [], elapsedSeconds=time.monotonic()-started)
    if not report['verificationCompleted'] and report['failureCount'] == 0:
        report['failureCount'] = 1
    return report


def save_report(output, report):
    output = Path(output)
    name = 'independent-result-verification.json' if report['passed'] else 'independent-result-verification-failed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'
    path = output/name
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        stream.write('\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/strategy-search/signal-session-decomposition-v1')
    args = parser.parse_args(argv)
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'failureCount': report['failureCount'], 'report': str(path)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
