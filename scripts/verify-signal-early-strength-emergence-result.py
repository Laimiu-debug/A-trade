"""Independent early-strength temporal rule and registered-event audit.

The audited feature panel is a bound input; the new projection, crossing,
retention, ranking and masks are reconstructed without importing production
rule or runner. Frozen event arithmetic is reused explicitly, not presented as
an independent certification of opening feasibility or historical broker fees.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / 'docs/signal-early-strength-emergence-v1.json'
REGISTRATION_SHA256 = '2fa9f3164206b0b50a4468f4759be3ab283cf89579c777d6e5907fed23b3f356'
VERSION = 'raw-early-strength-emergence-v1'
CANDIDATE_ID = 'rawEarlyStrengthEmergence'
CONTROL_ID = 'control_' + CANDIDATE_ID
GROUPS = (CANDIDATE_ID, CONTROL_ID)
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
OLD_REFERENCE = 'scripts/verify-signal-market-repair-result.py'
SOURCE_REFERENCE = 'scripts/verify-signal-session-decomposition-result.py'
AUDIT_DEPENDENCIES = {
    OLD_REFERENCE: '922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf',
    SOURCE_REFERENCE: 'fd71659842d12ed716305b35cef7f23d8f6f42d82906d4faae5918636f62e10a',
    'scripts/verify-signal-persistent-leader-result.py': '3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222'}
NEW_FILES = {'scripts/run-signal-early-strength-emergence.py', 'service/signal_early_strength_emergence.py',
    'scripts/verify-signal-early-strength-emergence-result.py', 'tests/test_signal_early_strength_emergence.py',
    'tests/test_signal_early_strength_emergence_runner.py', 'tests/test_signal_early_strength_emergence_verifier.py'}
PANEL_FILES = {'manifest.json', 'features.jsonl.gz', 'daily-coverage.jsonl.gz', 'warmup-coverage.jsonl.gz',
               'summary.json', 'execution-sources.zip', 'attempt.json'}
ARTIFACTS = {'projections':'projections.jsonl.gz', 'predicates':'predicates.jsonl.gz',
    'formations':'formations.jsonl.gz', 'transitions':'transitions.jsonl.gz',
    'episodeTerminals':'episode-terminals.jsonl.gz', 'confirmations':'confirmations.jsonl.gz',
    'selections':'selection.jsonl.gz', 'coverage':'coverage.jsonl.gz', 'signals':'signals.jsonl.gz',
    'slotOutcomes':'slot-outcomes.jsonl.gz', 'result':'result.json.gz', 'summary':'summary.json', 'attempt':'attempt.json'}



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


def private_reference(root):
    for name, expected in AUDIT_DEPENDENCIES.items():
        if sha(root/name) != expected:
            raise ValueError('Frozen independent reference differs: '+name)
    spec = importlib.util.spec_from_file_location('_early_strength_audited_event_reference', root/OLD_REFERENCE)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    reference.CANDIDATE_ID, reference.GROUPS = CANDIDATE_ID, GROUPS
    return reference


def bind(reference, output, root, audit):
    """Independent immutable input/output closure; no production runner import."""
    audit.pin(REGISTRATION, REGISTRATION_SHA256)
    doc = read(REGISTRATION)
    audit.require(doc.get('candidateId') == CANDIDATE_ID and doc.get('controlId') == CONTROL_ID,
                  'registered-early-strength-candidate')
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
    bind_prerequisites(protocol, root, audit)
    audit.require(protocol.get('schema') == 'early-strength-emergence-development-v1'
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
    audit.require(protocol['implementation']['scripts/verify-signal-early-strength-emergence-result.py'] == VERIFIER_SHA256,
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
    audit.pin(root / SOURCE_REFERENCE, AUDIT_DEPENDENCIES[SOURCE_REFERENCE])
    spec = importlib.util.spec_from_file_location('_early_strength_frozen_source_audit', root / SOURCE_REFERENCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_source(reference, binding, root, audit)


def bind_prerequisites(protocol, root, audit):
    names = {'scopedTests': 'artifacts/strategy-search/early-strength-emergence-scoped-tests-v1.json',
             'prerunReview': 'artifacts/strategy-search/early-strength-emergence-prerun-review-v1.json'}
    expected_files = {name: audit.pin(root/name) for name in NEW_FILES | {REGISTRATION.resolve().relative_to(root).as_posix()}}
    refs = protocol.get('prerequisites')
    audit.require(isinstance(refs, dict) and set(refs) == set(names), 'exact-pre-execution-proof-set')
    for key, name in names.items():
        audit.require(refs[key] == {'path': name, 'sha256': audit.pin(root/name)}, 'prerequisite-file:'+key)
    scoped, review = (read(root/names[k]) for k in ('scopedTests','prerunReview'))
    ids = scoped.get('uniqueTestIds')
    audit.require(scoped.get('schema') == 'early-strength-emergence-scoped-tests-v1' and scoped.get('passed') is True
        and type(scoped.get('testsRun')) is int and scoped['testsRun'] > 0
        and isinstance(ids, list) and len(ids) == len(set(ids)) == scoped['testsRun']
        and all(isinstance(x, str) and x for x in ids)
        and all(type(scoped.get(k)) is int and scoped[k] == 0 for k in ('failures','errors','skipped'))
        and scoped.get('sourcesStableDuringTests') is True and scoped.get('files') == expected_files
        and scoped.get('certified') is False and scoped.get('targetAchieved') is False, 'scoped-synthetic-proof')
    audit.require(review.get('schema') == 'early-strength-emergence-prerun-review-v1' and review.get('passed') is True
        and type(review.get('failureCount')) is int and review['failureCount'] == 0
        and review.get('scopedTestsSha256') == refs['scopedTests']['sha256'] and review.get('files') == expected_files
        and review.get('certified') is False and review.get('targetAchieved') is False, 'independent-prerun-proof')


def bounded(value):
    return number(value) and 0 <= value <= 1


@lru_cache(maxsize=8192)
def canonical_day(value):
    if not isinstance(value, str) or datetime.strptime(value, '%Y-%m-%d').date().isoformat() != value:
        raise ValueError('Noncanonical date')
    return value


def dates(values, day, maximum):
    if not isinstance(values, (list, tuple)) or not 0 < len(values) <= maximum:
        raise ValueError('Invalid causal window size')
    result = list(values)
    if result[-1] != canonical_day(day) or any(canonical_day(x) != x for x in result) or result != sorted(set(result)):
        raise ValueError('Invalid causal window identity/order')
    return result


def string_list(values):
    return isinstance(values, list) and all(isinstance(v, str) and bool(v) for v in values) and len(set(values)) == len(values)


def complete_quality(group, window, required):
    if not isinstance(group, dict) or len(window) != required or group.get('complete') is not True:
        return False
    if any(type(group.get(k)) is not int or group[k] != required for k in
           ('requiredSessions','availableCalendarSessions','observedSessions','validSessions')):
        return False
    if any(group.get(k) != [] for k in ('missingDates','invalidDates','haltDates','reasons')):
        return False
    unknown = group.get('unknownTradeStatusDates')
    return string_list(unknown) and unknown == sorted(unknown) and set(unknown) <= set(window)


def reference_qualification(value):
    obj = value if isinstance(value, dict) else {}
    reason, unknown = obj.get('reason'), obj.get('unknown')
    valid = type(obj.get('eligible')) is bool and isinstance(reason, str) and bool(reason) and string_list(unknown)
    valid = valid and all(k not in obj or obj[k] is False for k in ('certified','affectsFeaturesOrRanks'))
    state = 'unknown'
    if valid and obj['eligible'] and reason == 'eligible':
        state = 'true'
    elif valid and not obj['eligible'] and reason in {'delisted','notHistoricalMember','notListed','historicalSt',
            'suspendedOrZeroVolume','insufficientListingHistory','amount20BelowMinimum','amplitude20BelowMinimum'}:
        state = 'false'
    return {'state': state, 'eligible': obj['eligible'] if valid else None,
        'reason': reason if isinstance(reason, str) else None, 'unknown': deepcopy(unknown) if string_list(unknown) else [],
        'evidenceValid': valid, 'certified': False}


def reference_project(symbol, day, calendar61, row):
    """Independent field-by-field evidence checks, preserving unrelated knowns."""
    window = dates(calendar61, day, 61)
    if not isinstance(symbol, str) or not symbol.strip() or not isinstance(row, dict) or (
            row.get('symbol'), row.get('asOfDate'), row.get('featureVersion')) != (symbol, day, 'daily-leadership-raw-v1'):
        raise ValueError('Full source panel identity/version differs')
    quality = row.get('completeness') if isinstance(row.get('completeness'), dict) else {}
    date_lists = [row.get('strengthRankSessionDates20')]
    for group in [row, row.get('benchmarkQuality'), *quality.values()]:
        if isinstance(group, dict):
            date_lists.extend(group.get(key) for key in ('missingDates','invalidDates','haltDates','unknownTradeStatusDates'))
    for values in date_lists:
        if isinstance(values, list):
            for value in values:
                try:
                    valid = isinstance(value, str) and canonical_day(value) == value
                except ValueError:
                    valid = False
                if valid and value > day:
                    raise ValueError('Future dated panel evidence')
    reasons = row.get('featureReasons')
    def clean(fields):
        return isinstance(reasons, dict) and all(field not in reasons or reasons[field] == [] for field in fields)
    basis = row.get('rawPriceBasis') == 'raw-unadjusted'
    result = {'version': VERSION, 'sourceFeatureVersion': 'daily-leadership-raw-v1', 'symbol': symbol,
        'date': day, 'calendar61': window, 'qualification': reference_qualification(row.get('baseEligibility'))}
    for h in (20, 60):
        prefix = 'relativeReturn'+str(h)
        percentile, denominator = row.get(prefix+'Percentile'), row.get(prefix+'CrossSectionCount')
        numeric = all(number(row.get(field)) and row[field] >= -1 for field in ('return'+str(h), prefix))
        evidence = [complete_quality(quality.get(prefix2+str(h)), window[-h-1:], h+1) for prefix2 in ('security','benchmark')]
        known = basis and bounded(percentile) and type(denominator) is int and 1 <= denominator <= 3141
        known = known and numeric and clean(('return'+str(h), prefix, prefix+'Percentile')) and all(evidence)
        result['rank'+str(h)] = {'known': known, 'value': percentile if bounded(percentile) else None,
            'count': denominator if type(denominator) is int and 0 <= denominator <= 3141 else None,
            'reasons': [] if known else ['requiredRankEvidenceUnavailable']}
    history = row.get('trailing20DailyStrengthPercentiles')
    counts = row.get('trailing20StrengthCrossSectionCounts')
    times = row.get('strengthRankSessionDates20')
    valid_history = isinstance(history, list) and len(history) == 20 and all(bounded(v) for v in history)
    valid_counts = isinstance(counts, list) and len(counts) == 20 and all(type(v) is int and 1 <= v <= 3141 for v in counts)
    history_known = (result['rank20']['known'] and len(window) >= 20 and valid_history and valid_counts
        and times == window[-20:] and all(type(row.get(k)) is int and row[k] == 20
            for k in ('knownStrengthRankSessions20','availableStrengthRankSessions20'))
        and history[-1] == result['rank20']['value'] and counts[-1] == result['rank20']['count']
        and bounded(row.get('meanStrengthPercentile20')) and row['meanStrengthPercentile20'] == mean(history)
        and bounded(row.get('minimumStrengthPercentile20')) and row['minimumStrengthPercentile20'] == min(history)
        and clean(('meanStrengthPercentile20','minimumStrengthPercentile20')))
    result['history20'] = {'known': bool(history_known),
        'values': [v if bounded(v) else None for v in history] if isinstance(history, list) else None,
        'counts': [v if type(v) is int and 0 <= v <= 3141 else None for v in counts] if isinstance(counts, list) else None,
        'dates': deepcopy(times) if isinstance(times, list) and all(v is None or isinstance(v, str) for v in times) else None,
        'mean': row.get('meanStrengthPercentile20') if bounded(row.get('meanStrengthPercentile20')) else None,
        'minimum': row.get('minimumStrengthPercentile20') if bounded(row.get('minimumStrengthPercentile20')) else None,
        'reasons': [] if history_known else ['requiredHistoricalRankEvidenceUnavailable']}
    amount = row.get('prior20MeanAmount')
    amount_known = (basis and positive(amount) and type(row.get('knownAmountSessions20')) is int
        and row['knownAmountSessions20'] == 20 and complete_quality(quality.get('amountPrior20'), window[-21:-1], 20)
        and clean(('prior20MeanAmount',)))
    result['amountPrior20'] = {'known': amount_known, 'value': amount if positive(amount) else None,
        'reasons': [] if amount_known else ['requiredExplicitAmountEvidenceUnavailable']}
    result.update(certified=False, targetAchieved=False)
    return deepcopy(result)


def all_three(states):
    return 'unknown' if 'unknown' in states else 'false' if 'false' in states else 'true'


def predicate(known, value, threshold, operator):
    passed = None if not known else value > threshold if operator == '>' else value <= threshold
    return {'known': known, 'value': value, 'operator': operator, 'threshold': threshold,
            'state': 'unknown' if passed is None else 'true' if passed else 'false'}


def raw_quality(window, bars):
    if not isinstance(bars, (list, tuple)) or len(bars) != len(window):
        return {'known': False, 'reasons': ['rawWindowLengthMismatch'], 'unknownTradeStatusDates': []}
    problems = set()
    unknown = []
    if len(window) != 6:
        problems.add('insufficientSixSessionHistory')
    for day, row in zip(window, bars):
        flag = row.get('knownSuspended') if isinstance(row, dict) else None
        status = row.get('tradeStatusState') if isinstance(row, dict) else None
        if status == 'unknown' or status is None and flag is not True:
            unknown.append(day)
        if row is None:
            problems.add('missingRawBar')
        elif not isinstance(row, dict) or row.get('date') != day:
            problems.add('rawDateMismatch')
        elif row.get('priceBasisId') != 'raw-unadjusted':
            problems.add('rawBasisUnknown')
        else:
            if not all(positive(row.get(k)) for k in ('open','high','low','close','volume')):
                problems.add('invalidRawOHLCV')
            elif not row['low'] <= min(row['open'],row['close']) <= max(row['open'],row['close']) <= row['high']:
                problems.add('invalidRawEnvelope')
            if flag is True or status == 'halt':
                problems.add('suspensionContradictsPositiveVolume' if positive(row.get('volume')) else 'knownSuspension')
            elif flag is not None and type(flag) is not bool:
                problems.add('invalidSuspensionEvidence')
            if status is not None and status not in ('unknown', 'trading', 'halt'):
                problems.add('invalidTradeStatusEvidence')
    return {'known': not problems, 'reasons': sorted(problems), 'unknownTradeStatusDates': unknown}


def reference_advance(symbol, day, calendar6, stock, benchmark):
    window = dates(calendar6, day, 6)
    sq, bq = raw_quality(window, stock), raw_quality(window, benchmark)
    own = relative = ratio = benchmark_ratio = None
    if sq['known']:
        ratio = stock[-1]['close'] / stock[0]['close']
        if positive(ratio) and number(ratio-1):
            own = ratio-1
        else:
            sq['known'] = False; sq['reasons'].append('nonfiniteOrUnderflowDerivedRatio')
    if bq['known']:
        benchmark_ratio = benchmark[-1]['close'] / benchmark[0]['close']
        if not positive(benchmark_ratio):
            bq['known'] = False; bq['reasons'].append('nonfiniteOrUnderflowDerivedRatio')
    if sq['known'] and bq['known']:
        multiple = ratio/benchmark_ratio
        if positive(multiple) and number(multiple-1):
            relative = multiple-1
    checks = {'own5': predicate(own is not None, own, 0, '>'), 'excess5': predicate(relative is not None, relative, 0, '>')}
    return {'symbol': symbol, 'date': day, 'window': window, 'own5': own, 'excess5': relative,
        'stockQuality': sq, 'benchmarkQuality': bq, 'predicates': checks,
        'state': all_three([p['state'] for p in checks.values()]), 'certified': False}


def reference_selection(day, confirmations):
    rows = [deepcopy(row) for row in confirmations if row['selectionEligible'] is True]
    rows.sort(key=lambda r: (-r['prior20MeanAmount'], r['symbol']))
    if len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError('Duplicate confirmation')
    ranking = [{**row, 'rank': i+1, 'selectedTop5': i < 5} for i, row in enumerate(rows)]
    slots = [{**row, 'candidateAdmitted': row['earlyMask'] == 'true', 'baselineAdmitted': True} for row in ranking[:5]]
    signals = []
    for policy, other in ((CANDIDATE_ID, CONTROL_ID), (CONTROL_ID, CANDIDATE_ID)):
        for slot in slots:
            if policy == CONTROL_ID or slot['candidateAdmitted']:
                signals.append({**deepcopy(slot), 'version': VERSION, 'strategyId': policy, 'pairedStrategyId': other,
                    'signalDate': day, 'eligible': True, 'certified': False, 'targetAchieved': False})
    return {'date': day, 'candidateId': CANDIDATE_ID, 'controlId': CONTROL_ID, 'fullRanking': ranking,
        'slots': slots, 'signals': signals, 'policyCounts': {g: sum(s['strategyId'] == g for s in signals) for g in GROUPS}}


class ReferenceStudy:
    """Independent oracle: each episode is derived from its short observed prefix.

    No production transitions are consumed. A stored immutable formation and
    at most two observations suffice; each snapshot is rebuilt from the prefix.
    """
    def __init__(self, calendar, symbols, full_calendar):
        self.calendar = dates(calendar, calendar[-1], len(calendar))
        self.full = dates(full_calendar, full_calendar[-1], len(full_calendar))
        self.symbols = list(symbols)
        if self.symbols != sorted(set(symbols)) or not self.symbols:
            raise ValueError('Fixed sorted symbols required')
        first = self.full.index(calendar[0])
        if self.full[first:first+len(calendar)] != list(calendar):
            raise ValueError('Noncontiguous target calendar')
        self.previous, self.index, self.finished = None, -1, False
        self.initial, self.observations, self.current = [], {}, {}
        self.latest = {}

    def counts(self, index):
        return {'activatedEpisodes': len(self.initial),
            'lockedSymbols': sum(index <= row['lockThroughIndex'] for row in self.latest.values()),
            'stateCounts': dict(sorted(Counter(r['state'] for r in self.current.values()).items()))}

    def derive(self, original, observations):
        episode = deepcopy(original)
        transitions, confirmation = [], None
        if episode['state'] != 'observing':
            return episode, transitions, confirmation
        for index, row in observations:
            if episode['state'] != 'observing':
                break
            day = row['date']; rank = row['rank20']; qualification = row['qualification']
            offset = index-episode['formationIndex']
            if not 1 <= offset <= 2:
                raise ValueError('Episode observation beyond its fixed clock')
            if not rank['known']:
                state, reason = 'rankUnknown', 'requiredRankBecameUnknown'
            elif rank['value'] < .9:
                state, reason = 'rankLost', 'rankBelowThreshold'
            elif offset == 2:
                state = {'true':'confirmed','false':'qualificationRejected','unknown':'qualificationUnknown'}[qualification['state']]
                reason = 'firstTwoSessionConfirmation'
                episode['confirmationDate'] = day
                confirmation = {'date': day, 'symbol': episode['symbol'], 'episodeId': episode['episodeId'],
                    'formationDate': episode['formationDate'], 'earlyMask': episode['earlyMask'],
                    'prior20MeanAmount': episode['prior20MeanAmount'], 'qualification': deepcopy(qualification),
                    'selectionEligible': qualification['state'] == 'true'}
            else:
                state, reason = 'observing', 'rankRetained'
            transitions.append({'date': day, 'index': index, 'symbol': episode['symbol'], 'episodeId': episode['episodeId'],
                'offset': offset, 'fromState': 'observing', 'toState': state, 'reason': reason,
                'evidence': {'rank20': deepcopy(rank), 'qualification': deepcopy(qualification)}})
            episode.update(state=state, reason=reason, lastProcessedDate=day)
            episode['observationDates'].append(day)
            if state != 'observing':
                episode.update(terminalDate=day, terminalIndex=index)
        return episode, transitions, confirmation

    def step(self, day, index, projections, raw, benchmark):
        if self.finished or type(index) is not int or index != self.index+1 or index >= len(self.calendar) or day != self.calendar[index]:
            raise ValueError('Noncausal target call order')
        if set(projections) != set(self.symbols) or set(raw) != set(self.symbols):
            raise ValueError('Incomplete population')
        position = self.full.index(day)
        expected_calendar = self.full[max(0,position-60):position+1]
        for symbol, row in projections.items():
            if (row['symbol'], row['date'], row['version'], row['calendar61']) != (symbol, day, VERSION, expected_calendar):
                raise ValueError('Projection identity/calendar differs')
        evaluations, formations, transitions, confirmations = [], [], [], []
        # Only the historical prefix of currently open episodes is extended.
        for original in self.latest.values():
            key = original['episodeId']
            if self.current[key]['state'] != 'observing':
                continue
            self.observations[key].append((index, deepcopy(projections[original['symbol']])))
            snapshot, history, confirmation = self.derive(original, self.observations[key])
            self.current[key] = snapshot
            transitions.append(history[-1])
            if confirmation is not None:
                confirmations.append(confirmation)
        for symbol in self.symbols:
            row = projections[symbol]
            previous = self.previous[symbol] if self.previous is not None else None
            crossing, reason = 'unknown', 'previousFullPanelUnavailable'
            if previous is not None:
                if not previous['rank20']['known'] or not row['rank20']['known']:
                    reason = 'requiredAdjacentRankUnknown'
                else:
                    crossing = 'true' if previous['rank20']['value'] < .9 <= row['rank20']['value'] else 'false'
                    reason = 'knownUpcross' if crossing == 'true' else 'noUpcross'
            lock = self.latest.get(symbol)
            if lock is not None and index > lock['lockThroughIndex']:
                lock = None
            evaluation = {'date': day, 'index': index, 'symbol': symbol,
                'previousDate': previous['date'] if previous is not None else None,
                'previousRank20': deepcopy(previous['rank20']) if previous is not None else None,
                'rank20': deepcopy(row['rank20']), 'crossing': crossing, 'crossingReason': reason,
                'lockedByEpisodeId': lock['episodeId'] if lock else None,
                'lockThroughIndex': lock['lockThroughIndex'] if lock else None, 'formationDecision':'notFormed'}
            if crossing == 'true' and lock is not None:
                evaluation['formationDecision'] = 'formationSuppressedByLock'
                formations.append({'date': day, 'index': index, 'symbol': symbol, 'kind':'formationSuppressedByLock',
                    'episodeId':None, 'lockedByEpisodeId':lock['episodeId'], 'lockThroughIndex':lock['lockThroughIndex'], 'episode':None})
            elif crossing == 'true':
                advance = reference_advance(symbol, day, self.full[max(0,position-5):position+1], raw[symbol], benchmark)
                tests = {'priorHistoryMean': predicate(previous['history20']['known'], previous['history20']['mean'], .5, '<='),
                    'rank60AtFormation': predicate(row['rank60']['known'], row['rank60']['value'], .8, '<=')}
                early = {'state': all_three([p['state'] for p in tests.values()]), 'predicates':tests,
                    'historyDate':previous['date'], 'rank60Date':day}
                common = {'qualification':row['qualification']['state'],
                    'amountPrior20':'true' if row['amountPrior20']['known'] else 'unknown',
                    'own5':advance['predicates']['own5']['state'], 'excess5':advance['predicates']['excess5']['state']}
                gate = all_three(list(common.values()))
                state = {'true':'observing','false':'formationRejected','unknown':'formationUnknown'}[gate]
                key = day+':'+symbol
                episode = {'episodeId':key, 'symbol':symbol, 'formationDate':day, 'formationIndex':index,
                    'lockThroughIndex':index+2, 'deadlineDate':self.calendar[index+2] if index+2 < len(self.calendar) else None,
                    'state':state, 'reason':'formationCommon'+gate.title(), 'earlyMask':early['state'], 'earlyMaskEvidence':early,
                    'formationCommon':{'state':gate,'predicates':common}, 'prior20MeanAmount':row['amountPrior20']['value'],
                    'formationEvidence':{'previousRank20':deepcopy(previous['rank20']), 'rank20':deepcopy(row['rank20']),
                        'qualification':deepcopy(row['qualification']), 'amountPrior20':deepcopy(row['amountPrior20']), 'advance':advance},
                    'lastProcessedDate':day, 'terminalDate':day if state != 'observing' else None,
                    'terminalIndex':index if state != 'observing' else None, 'confirmationDate':None, 'observationDates':[]}
                self.initial.append(deepcopy(episode)); self.current[key] = deepcopy(episode); self.observations[key] = []
                self.latest[symbol] = episode
                formations.append({'date':day,'index':index,'symbol':symbol,'kind':state,'episodeId':key,
                    'lockedByEpisodeId':None,'lockThroughIndex':index+2,'episode':deepcopy(episode)})
                evaluation['formationDecision'] = state
            evaluations.append(evaluation)
        self.previous = deepcopy(projections); self.index = index
        selection = reference_selection(day, confirmations)
        return {'evaluations':evaluations,'formations':formations,'transitions':transitions,'rawConfirmations':confirmations,
            'selection':selection,'signals':selection['signals'],'episodeCounts':self.counts(index)}

    def finish(self, cutoff):
        if self.finished or self.index < 0 or cutoff != self.calendar[self.index]:
            raise ValueError('Finish once at current cutoff')
        transitions = []
        for original in self.latest.values():
            episode = self.current[original['episodeId']]
            if episode['state'] == 'observing':
                if self.index >= episode['lockThroughIndex']:
                    raise ValueError('Unobserved expired confirmation')
                transitions.append({'date':cutoff,'index':self.index,'symbol':episode['symbol'],'episodeId':episode['episodeId'],
                    'offset':self.index-episode['formationIndex'],'fromState':'observing','toState':'censoredAtStudyCutoff',
                    'reason':'studyEndedBeforeConfirmation','evidence':{}})
                episode.update(state='censoredAtStudyCutoff',reason='studyEndedBeforeConfirmation',lastProcessedDate=cutoff,
                               terminalDate=cutoff,terminalIndex=self.index)
        self.finished = True
        return {'transitions':transitions,'episodes':[deepcopy(self.current[e['episodeId']]) for e in self.initial],
                'episodeCounts':self.counts(self.index)}


def replay(reference, binding, dataset, audit):
    """One date-ordered full-population pass; no preselected-signal source."""
    inputs = binding['manifest']['inputs']
    calendar = dataset['calendar']
    indices = {day: i for i, day in enumerate(calendar)}
    securities = {s['symbol']: s for s in dataset['securities']}
    securities['sh000300'] = dataset['benchmark']
    bars = {s: {r['date']: r for r in sec['bars']} for s, sec in securities.items()}
    def raw(symbol, day):
        bar = bars[symbol].get(day)
        status = securities[symbol].get('tradeStatusByDate', {}).get(day)
        halt = status is False or type(status) is int and status == 0 or status == '0'
        trading = status is True or type(status) is int and status == 1 or status == '1'
        return {**bar, 'priceBasisId': 'raw-unadjusted', 'knownSuspended': halt,
            'tradeStatusState': 'halt' if halt else 'trading' if trading else 'unknown'} if bar is not None else None
    feature_stream = iter(records(binding['panel'] / 'features.jsonl.gz'))
    log_keys = ('projections', 'predicates', 'formations', 'transitions', 'episodeTerminals',
                'confirmations', 'selections', 'coverage', 'signals')
    streams = {key: iter(records(binding['attempt'] / ARTIFACTS[key])) for key in log_keys}
    def compare(key, expected, label, **details):
        audit.check(reference.exact(next(streams[key], None), expected), label, **details)
    state = ReferenceStudy(binding['dates'], binding['symbols'], calendar)
    signals, slots = [], []
    totals = Counter({key: 0 for key in ('formations', 'transitions', 'confirmations', 'sourceFeatureRows',
        'scoredSessions', 'globalTop5Slots', 'selectionEligibleConfirmations')})
    reasons, masks, initial_states = Counter(), Counter(), Counter()
    notice = time.monotonic()
    for index, day in enumerate(binding['dates']):
        position = indices[day]
        days61, days6 = calendar[max(0, position-60):position+1], calendar[max(0, position-5):position+1]
        source = {'panelManifestSha256': binding['identity']['panelManifestSha256'],
            'featureFileSha256': binding['document']['sourceBinding']['files']['features.jsonl.gz'],
            'calendarHash': binding['identity']['calendarHash'], 'targetIndex': index}
        projections = {}
        for symbol in binding['symbols']:
            row = next(feature_stream, None)
            hashes = {**inputs['securities'][symbol], 'benchmarkRawBarsSha256': inputs['benchmark']['rawBarsSha256']}
            audit.require(isinstance(row, dict) and row.get('asOfDate') == day and row.get('symbol') == symbol
                and row.get('featureVersion') == 'daily-leadership-raw-v1'
                and row.get('sampleManifestHash') == source['panelManifestSha256']
                and row.get('calendarHash') == source['calendarHash'] and row.get('sourceHashes') == hashes,
                'independent-source-feature-identity')
            projection = reference_project(symbol, day, days61, row)
            projections[symbol] = projection
            compare('projections', {'date': day, 'symbol': symbol, 'source': {**source,
                'sourceRowSha256': digest(row), 'sourceHashes': hashes}, 'projection': projection},
                'independent-panel-projection', date=day, symbol=symbol)
        actual = state.step(day, index, projections,
            {s: [raw(s, d) for d in days6] for s in binding['symbols']}, [raw('sh000300', d) for d in days6])
        for row in actual['evaluations']:
            compare('predicates', {'date': day, 'symbol': row['symbol'], 'source': source, 'record': row},
                    'independent-crossing-lock-evaluation', date=day, symbol=row['symbol'])
        for key, name in (('formations', 'formations'), ('transitions', 'transitions'), ('confirmations', 'rawConfirmations')):
            for row in actual[name]:
                compare(key, {'date': day, 'source': source, 'record': row}, 'independent-'+key, date=day)
                totals[key] += 1
        selection = actual['selection']
        compare('selections', {'date': day, 'source': source, 'selection': selection}, 'independent-ranking-mask', date=day)
        for signal in selection['signals']:
            compare('signals', signal, 'independent-selected-signal', date=day, symbol=signal['symbol'])
        signals.extend(selection['signals']); slots.extend(selection['slots'])
        day_reasons = Counter(r['crossingReason'] for r in actual['evaluations'])
        day_masks = Counter(r['episode']['earlyMask'] for r in actual['formations'] if r['episode'] is not None)
        day_states = Counter(r['kind'] for r in actual['formations'])
        eligible = sum(r['selectionEligible'] is True for r in actual['rawConfirmations'])
        daily = {'date': day, 'targetIndex': index, 'source': source, 'sourceFeatureRows': len(projections),
            'evaluationReasonCounts': dict(day_reasons), 'formations': len(actual['formations']),
            'formationKinds': dict(day_states), 'activatedEarlyMaskCounts': {v: day_masks[v] for v in ('true','false','unknown')},
            'transitions': len(actual['transitions']), 'rawConfirmations': len(actual['rawConfirmations']),
            'selectionEligibleConfirmations': eligible, 'globalTop5Slots': len(selection['slots']),
            'selectedSignals': {g: sum(s['strategyId'] == g for s in selection['signals']) for g in GROUPS},
            'episodeCounts': actual['episodeCounts'], 'certified': False, 'targetAchieved': False}
        compare('coverage', daily, 'independent-full-population-coverage', date=day)
        totals.update(sourceFeatureRows=len(projections), scoredSessions=1, globalTop5Slots=len(selection['slots']),
                      selectionEligibleConfirmations=eligible)
        reasons.update(day_reasons); masks.update(day_masks); initial_states.update(day_states)
        if time.monotonic()-notice >= 30:
            print(f"Independently checked {index+1}/{len(binding['dates'])} early-strength days", flush=True)
            notice = time.monotonic()
    audit.require(next(feature_stream, None) is None, 'source-panel-exact-EOF')
    terminal = state.finish(binding['dates'][-1])
    for row in terminal['transitions']:
        compare('transitions', {'date': binding['dates'][-1], 'source': None, 'phase': 'cutoff', 'record': row},
                'independent-cutoff-transition')
        totals['transitions'] += 1
    for episode in terminal['episodes']:
        compare('episodeTerminals', episode, 'independent-episode-terminal')
    for key, stream in streams.items():
        audit.require(next(stream, None) is None, 'output-exact-EOF:'+key)
    accounting = {'formations': totals['formations'], 'formationKinds': dict(initial_states),
        'activatedEarlyMaskCounts': {s: masks[s] for s in ('true','false','unknown')},
        'episodes': len(terminal['episodes']), 'suppressedByLock': initial_states['formationSuppressedByLock'],
        'terminalStateCounts': dict(Counter(e['state'] for e in terminal['episodes'])),
        'rawConfirmations': totals['confirmations'], 'selectionEligibleConfirmations': totals['selectionEligibleConfirmations'],
        'globalTop5Slots': totals['globalTop5Slots'], 'cutoffEpisodeCounts': terminal['episodeCounts']}
    performance = {**dict(totals), 'episodeAccounting': accounting, 'evaluationReasonCounts': dict(reasons),
        'selectedSignals': {g: sum(s['strategyId'] == g for s in signals) for g in GROUPS},
        'sourceFeatureMathematicsRecomputed': False, 'parentCandidateStreamUsed': False}
    return signals, slots, performance


def audit_slots(reference, audit, result, signals, slots, attempt):
    identify = lambda row: (row['strategyId'], row['symbol'], row['signalDate'])
    selected = {identify(r): r for r in signals}
    events = {identify(r): r for r in result['events']}
    suppressed = {identify(r): r for r in result['overlapSuppressed']}
    audit.require(len(selected) == len(signals) and len(events) == len(result['events'])
        and len(suppressed) == len(result['overlapSuppressed']) and not set(events) & set(suppressed)
        and set(selected) == set(events) | set(suppressed), 'exact-policy-disposition-sets')
    consumed = set()
    def disposition(slot, policy):
        if policy == CANDIDATE_ID and slot['earlyMask'] != 'true':
            return {'status': 'filteredEarlyUnknown' if slot['earlyMask'] == 'unknown' else 'filteredEarlyFalse',
                    'eventId': None, 'entryStatus': None}
        key = policy, slot['symbol'], slot['date']
        audit.require(key in selected and key not in consumed, 'unique-policy-slot')
        consumed.add(key)
        event = events.get(key)
        if event is None:
            block = suppressed[key]
            return {'status': 'activeSuppressed', 'eventId': None, 'entryStatus': None, 'reason': block['reason'],
                    'blockedByEventId': block['blockedByEventId'], 'activeUntil': block['activeUntil']}
        return {'status': 'attempted', 'eventId': event['eventId'], 'entryStatus': event['entry']['status'],
            'entryReason': event['entry'].get('reason'), 'entryDate': event['entry'].get('date'),
            'targetDate': event['outcomes']['nextOpenNet30']['hitDate'], 'terminal': deepcopy(event['terminal'])}
    stream = iter(records(attempt / ARTIFACTS['slotOutcomes']))
    expected = []
    for slot in slots:
        row = {**slot, 'candidateDisposition': disposition(slot, CANDIDATE_ID),
            'baselineDisposition': disposition(slot, CONTROL_ID),
            'interpretation': 'original-common-Top5;two-independent-active-policies;not-randomized-or-fill-matched'}
        audit.check(reference.exact(next(stream, None), row), 'independent-original-slot-dispositions')
        expected.append(row)
    audit.require(next(stream, None) is None and consumed == set(selected), 'slot-outcomes-exact-EOF')
    accounting = {'globalTop5Slots': len(slots), 'candidateAdmitted': sum(s['earlyMask'] == 'true' for s in slots),
        'baselineAdmitted': len(slots), 'earlyMaskCounts': {v: sum(s['earlyMask'] == v for s in slots) for v in ('true','false','unknown')}}
    for name in ('candidate','baseline'):
        sides = [r[name+'Disposition'] for r in expected]
        accounting[name+'Dispositions'] = dict(Counter(s['status'] for s in sides))
        accounting[name+'EntryStatuses'] = dict(Counter(s['entryStatus'] for s in sides if s['entryStatus'] is not None))
    audit.check(reference.exact(result.get('slotAccounting'), accounting), 'independent-slot-accounting')
    return accounting


def verify(output: Path, root: Path = ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    started, audit = time.monotonic(), None
    report = {'schema': 'early-strength-emergence-independent-verification-v1',
        'checkedAt': datetime.now(timezone.utc).isoformat(), 'passed': False, 'verificationCompleted': False,
        'candidateId': CANDIDATE_ID, 'registrationSha256': REGISTRATION_SHA256, 'verifierSha256': VERIFIER_SHA256,
        'certified': False, 'targetAchieved': False, 'actualOpeningExecutionVerified': False,
        'sourceFeatureMathematicsRecomputed': False, 'parentCandidateStreamUsed': False,
        'scope': 'Independent new projection/crossing/episode/mask/ranking/full-stream closure and inherited registered-event arithmetic.',
        'limitations': ['Prior feature mathematics is inherited only through its bound completed audit; it is not recomputed here.',
            'Frozen event/path audit does not certify maximal order size, every opening eligibility/capacity gate, or first feasible target fill.',
            'Legacy static transfer fees and assumed commission are not certified historical broker charges.',
            'Raw prices, historical membership, corporate actions and reused dependent development events do not establish future net30 probability.']}
    try:
        reference = private_reference(root)
        audit = reference.Audit()
        binding = bind(reference, output, root, audit)
        dataset = load_source(reference, binding, root, audit)
        before = digest(dataset)
        signals, slots, performance = replay(reference, binding, dataset, audit)
        with gzip.open(binding['attempt'] / ARTIFACTS['result'], 'rt', encoding='utf-8') as stream:
            result = parse(stream.read())
        summary = read(binding['attempt'] / ARTIFACTS['summary'])
        audit.require(result.get('protocolHash') == binding['protocol']['protocolHash']
            and result.get('snapshotFingerprint') == binding['identity']['snapshotFingerprint']
            and result.get('strategyOrder') == [CANDIDATE_ID] and result.get('diagnosticUnverifiedHistory') is True,
            'result-registered-identity')
        for value in (result, summary):
            audit.require(value.get('certified') is False and value.get('targetAchieved') is False, 'result-diagnostic-only')
        expected_summary = {k: v for k, v in result.items() if k not in ('events','overlapSuppressed','pathRiskEvents')}
        audit.check(reference.exact({k: v for k, v in summary.items() if k not in ('performance','ranking','selectionEligible')},
                    expected_summary), 'summary-mirrors-result')
        audit.check(summary.get('ranking') is None and summary.get('selectionEligible') is False, 'no-ranked-certification')
        for key, value in performance.items():
            audit.check(reference.exact(summary.get('performance', {}).get(key), value), 'performance:'+key)
            audit.check(reference.exact(binding['journal'][1].get(key), value), 'journal-scoring:'+key)
        audit.check(reference.exact(result.get('episodeAccounting'), performance['episodeAccounting']), 'independent-episode-accounting')
        for field in ('scoringSeconds', 'eventEvaluationSeconds'):
            audit.check(number(summary.get('performance', {}).get(field), 0), 'nonnegative-timing:'+field)
        adapted = {'originalRawSourcePanelBinding': {'targetDates': binding['dates']}, 'frozenExecutionConfig': binding['document']['config']}
        report['eventsChecked'] = reference.event_audit(audit, result, signals, dataset, adapted, root)
        accounting = audit_slots(reference, audit, result, signals, slots, binding['attempt'])
        report.update(slotAccounting=accounting, episodeAccounting=performance['episodeAccounting'],
            evaluatedSecuritySessions=performance['sourceFeatureRows'], targetSessions=len(binding['dates']),
            protocolHash=binding['protocol']['protocolHash'], snapshotFingerprint=dataset['fingerprint'],
            completionSha256=sha(binding['attempt']/'completion.json'), resultSha256=sha(binding['attempt']/ARTIFACTS['result']),
            implementation=binding['protocol']['implementation'])
        audit.require(digest(dataset) == before, 'source-object-unmodified')
        for path, expected in audit.bound.items():
            audit.require(sha(path) == expected, 'bound-file-unchanged:'+path.name)
        report['verificationCompleted'] = True
        report['passed'] = audit.failure_count == 0
    except Exception as error:
        report.update(errorType=type(error).__name__, error=str(error))
    report.update(checks=audit.checks if audit else 0, failureCount=audit.failure_count if audit else 1,
        issues=audit.issues if audit else [], elapsedSeconds=time.monotonic()-started)
    if not report['verificationCompleted'] and not report['failureCount']:
        report['failureCount'] = 1
    return report


def save_report(output, report):
    name = 'independent-result-verification.json' if report['passed'] else 'independent-result-verification-failed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'
    path = Path(output) / name
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        stream.write('\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/strategy-search/signal-early-strength-emergence-v1')
    args = parser.parse_args(argv)
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'failureCount': report['failureCount'], 'report': str(path)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

