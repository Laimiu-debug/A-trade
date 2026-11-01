"""Independent frozen-leader/peer-expansion diagnostic audit.

No production detector, peer rule or trial runner is imported. Peer formation
and expansion are reconstructed from causal raw inputs. Pinned independent
auditors supply existing leader predicates and registered-cost event arithmetic.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean
import time

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT/'docs/signal-leader-peer-expansion-v1.json'
REGISTRATION_SHA256 = "80a781ced9fb746677a76f554e065be650a88edbe6f56f96bd522a45a77e873f"
OLD_REFERENCE = 'scripts/verify-signal-market-repair-result.py'
LEADER_REFERENCE = 'scripts/verify-signal-persistent-leader-result.py'
SOURCE_REFERENCE = 'scripts/verify-signal-session-decomposition-result.py'
DEPENDENCIES = {
    OLD_REFERENCE: '922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf',
    LEADER_REFERENCE: '3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222',
    SOURCE_REFERENCE: 'fd71659842d12ed716305b35cef7f23d8f6f42d82906d4faae5918636f62e10a'}
GATES = (('meanStrengthPercentile20', '>=', .9), ('minimumStrengthPercentile20', '>=', .8),
    ('relativeReturn60Percentile', '>=', .9), ('closePathMaxDrawdown20', '<=', .1),
    ('signedPathEfficiency20', '>=', .35))


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
        for key, value in items:
            if key in out:
                raise ValueError('Duplicate JSON key: '+key)
            out[key] = value
        return out
    def reject(token):
        raise ValueError('Nonfinite JSON: '+token)
    return json.loads(value, object_pairs_hook=unique, parse_constant=reject)


def read(path):
    return parse(Path(path).read_bytes())


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = parse(line)
            if not isinstance(row, dict):
                raise ValueError('A JSONL row must be an object')
            yield row


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def load_references(root):
    for name, expected in DEPENDENCIES.items():
        if sha(root/name) != expected:
            raise ValueError('Frozen independent auditor changed: '+name)
    def load(name, relative):
        spec = importlib.util.spec_from_file_location(name, root/relative)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value
    event = load('_peer_independent_events', OLD_REFERENCE)
    source = load('_peer_independent_source', SOURCE_REFERENCE)
    leader = event.ast_functions(root/LEADER_REFERENCE,
        {'expected_kind', 'finite', 'valid_feature', 'price_valid'}, {'math': math, 'GATES': GATES})
    return event, source, leader


CANDIDATE_ID = "rawLeaderPeerExpansion"
CONTROL_ID = "control_"+CANDIDATE_ID
GROUPS = (CANDIDATE_ID, CONTROL_ID)
VERSION = "raw-leader-peer-expansion-v1"
PARENT_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
HORIZONS = (5,10,20,40,60,120,250)
KINDS = ("ineligible","unevaluable","notTriggered","signaled")
EXPANSION_KINDS = ("expanded","notExpanded","unevaluable")
AUDIT_DEPENDENCIES = DEPENDENCIES
NEW_FILES = {'scripts/verify-signal-leader-peer-expansion-result.py', 'service/signal_leader_peer_expansion.py', 'scripts/run-signal-leader-peer-expansion.py', 'tests/test_signal_leader_peer_expansion_verifier.py', 'tests/test_signal_leader_peer_expansion_runner.py', 'tests/test_signal_leader_peer_expansion.py'}
PANEL_FILES = {'warmup-coverage.jsonl.gz', 'features.jsonl.gz', 'summary.json', 'attempt.json', 'daily-coverage.jsonl.gz', 'execution-sources.zip', 'manifest.json'}
ARTIFACTS = {'predicates': 'predicates.jsonl.gz', 'candidates': 'candidates.jsonl.gz', 'coverage': 'coverage.jsonl.gz', 'selections': 'selection.jsonl.gz', 'peerGroups': 'peer-groups.jsonl.gz', 'peerExpansion': 'peer-expansion.jsonl.gz', 'signals': 'signals.jsonl.gz', 'slotOutcomes': 'slot-outcomes.jsonl.gz', 'result': 'result.json.gz', 'summary': 'summary.json', 'attempt': 'attempt.json'}

def bind(reference, output, root, audit):
    """Independent immutable input/output closure; no production runner import."""
    audit.pin(REGISTRATION, REGISTRATION_SHA256)
    doc = read(REGISTRATION)
    audit.require(doc.get('candidateId') == CANDIDATE_ID and doc.get('controlId') == CONTROL_ID,
                  'registered-peer-candidate')
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
    audit.require(protocol.get('schema') == 'leader-peer-expansion-development-v1'
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
    audit.require(protocol['implementation']['scripts/verify-signal-leader-peer-expansion-result.py'] == VERIFIER_SHA256,
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


def eligibility(value):
    if not isinstance(value, dict) or type(value.get('eligible')) is not bool:
        return 'unevaluable', 'baseEligibilityUnknown'
    unknown = value.get('unknown')
    if not isinstance(unknown, list) or any(not isinstance(v,str) or not v.strip() for v in unknown) or len(set(unknown)) != len(unknown):
        return 'unevaluable', 'invalidBaseEligibilityUnknownFields'
    reason = value.get('reason')
    if not isinstance(reason,str) or not reason.strip():
        return 'unevaluable', 'invalidBaseEligibilityReason'
    if not value['eligible']:
        return 'ineligible', 'baseEligibilityRejected'
    return ('eligible','eligible') if reason == 'eligible' else ('unevaluable','inconsistentBaseEligibilityReason')


def window_issue(bars, days):
    if not isinstance(bars,(list,tuple)) or len(bars) != len(days):
        return 'insufficientAlignedRawHistory', None
    for i,(day,b) in enumerate(zip(days,bars)):
        if b is None: return 'missingAlignedRawBar', i
        if not isinstance(b,dict) or b.get('date') != day: return 'rawHistoryDateMismatch', i
        if b.get('priceBasisId') != 'raw-unadjusted': return 'rawPriceBasisUnknown', i
        if 'knownSuspended' in b and type(b['knownSuspended']) is not bool: return 'invalidKnownSuspensionFlag', i
        if b.get('knownSuspended'):
            return ('suspensionContradictsPositiveVolume' if finite(b.get('volume')) and b['volume'] > 0 else 'knownSuspension'), i
        if any(not finite(b.get(k)) or b[k] <= 0 for k in ('open','high','low','close','volume')):
            return 'nonpositiveOrUnknownRawOHLCV', i
        if not b['low'] <= min(b['open'],b['close']) <= max(b['open'],b['close']) <= b['high']:
            return 'invalidRawOHLCEnvelope', i
    return None,None


def logs(bars):
    try:
        values = [math.log(b['close']/a['close']) for a,b in zip(bars,bars[1:])]
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return values if all(finite(v) for v in values) else None


def centered(values):
    average = math.fsum(values)/len(values)
    residual = [v-average for v in values]
    return residual, math.fsum(v*v for v in residual)


def pearson(left,right):
    """Fixed two-pass operation order; return raw and projected rho separately."""
    if len(left) != 60 or len(right) != 60 or not all(finite(v) for v in [*left,*right]): return None
    x,vx = centered(left)
    y,vy = centered(right)
    if not finite(vx) or not finite(vy) or vx <= 0 or vy <= 0: return None
    try:
        rho = math.fsum(a*b for a,b in zip(x,y))/(math.sqrt(vx)*math.sqrt(vy))
    except (OverflowError,ZeroDivisionError,ValueError):
        return None
    return (rho,max(-1.,min(1.,rho))) if finite(rho) else None


def peer_quality():
    return {'priceBasisId':'raw-unadjusted','actionEvidence':'unverified','certified':False,
        'targetAchieved':False,'historicalEligibilityVerified':False,'historicalTradeStatusVerified':False,
        'comparableRightsVerified':False,
        'interpretation':'benchmark-excess-log-return association;not beta-adjusted or causal peer influence',
        'unknownTradeStatusPolicy':'No known suspension plus positive bars does not certify historical trading status'}


def reference_group(anchor, day, days, raw, benchmark, eligibility_rows):
    """Independent full F universe evaluation, with no response-data input."""
    calendar_reason = None if len(days) == 61 and days[-1] == day and days == sorted(set(days)) else 'insufficientAlignedHistory'
    anchor_state, anchor_reason = eligibility(eligibility_rows.get(anchor))
    br,bi = window_issue(benchmark,days) if calendar_reason is None else (calendar_reason,None)
    ar,ai = window_issue(raw.get(anchor),days) if calendar_reason is None else (calendar_reason,None)
    benchmark_logs = logs(benchmark) if br is None else None
    if br is None and benchmark_logs is None: br = 'nonfiniteBenchmarkLogReturn'
    problem = calendar_reason or ('anchor:'+anchor_reason if anchor_state != 'eligible' else None) or ('benchmark:'+br if br else None) or ('anchor:'+ar if ar else None)
    anchor_excess, av = None,None
    if problem is None:
        values = logs(raw[anchor])
        if values is None: problem = 'anchor:nonfiniteLogReturn'
        else:
            anchor_excess = [a-b for a,b in zip(values,benchmark_logs)]
            _,av = centered(anchor_excess)
            if not finite(av) or av <= 0: problem = 'anchor:zeroOrNonfiniteExcessVariance'
    evaluations = []
    for symbol in sorted(raw):
        row = {'symbol':symbol,'status':'unevaluable','reason':None,'rawCorrelation':None,'correlation':None,
            'excessVariance':None,'selected':False,'rank':None,'eligibility':deepcopy(eligibility_rows.get(symbol)),'invalidSlot':None}
        state,reason = eligibility(eligibility_rows.get(symbol))
        issue,slot = window_issue(raw[symbol],days) if calendar_reason is None else (calendar_reason,None)
        if symbol == anchor: row.update(status='excludedSelf',reason='excludedSelf')
        elif state != 'eligible': row.update(status=state,reason=reason)
        elif issue: row.update(reason=issue,invalidSlot=slot)
        elif problem: row['reason'] = problem
        else:
            values = logs(raw[symbol])
            if values is None: row['reason'] = 'nonfiniteLogReturn'
            else:
                excess = [a-b for a,b in zip(values,benchmark_logs)]
                _,variance = centered(excess)
                row['excessVariance'] = variance if finite(variance) else None
                rho = pearson(anchor_excess,excess)
                if not finite(variance) or variance <= 0: row['reason'] = 'zeroOrNonfiniteExcessVariance'
                elif rho is None: row['reason'] = 'nonfiniteCorrelation'
                else:
                    raw_rho,rho = rho
                    passed = rho >= .30
                    row.update(rawCorrelation=raw_rho,correlation=rho,status='qualified' if passed else 'belowThreshold',
                        reason='correlationAtLeast030' if passed else 'correlationBelow030')
        evaluations.append(row)
    ranked = sorted((r for r in evaluations if r['status']=='qualified'), key=lambda r:(-r['correlation'],r['symbol']))
    for i,row in enumerate(ranked,1): row.update(rank=i,selected=i<=20)
    members = [r['symbol'] for r in ranked[:20]]
    complete = len(members)==20 and problem is None
    return {'version':VERSION,'anchor':anchor,'formationDate':day,'kind':'formed' if complete else 'unevaluable',
        'reason':problem or ('fixedPeerGroupFormed' if len(members)==20 else 'insufficientCorrelatedPeers'),
        'window':{'startDate':days[0] if days else None,'endDate':day,'closeSessions':61,'returnSessions':60,'providedCalendarSessions':len(days)},
        'members':members,'correlations':{r['symbol']:r['correlation'] for r in ranked[:20]},'selectedCount':len(members),
        'requiredCount':20,'candidateEvaluations':evaluations,
        'counts':{'inputSymbols':len(raw),'candidateEvaluations':len(evaluations),'qualifiedPeers':len(ranked),'selectedCount':len(members),
            'statusCounts':dict(Counter(r['status'] for r in evaluations)),'reasonCounts':dict(Counter(r['reason'] for r in evaluations))},
        'anchorEvidence':{'eligibility':deepcopy(eligibility_rows.get(anchor)),'eligibilityState':anchor_state,'eligibilityReason':anchor_reason,
            'rawReason':ar,'invalidSlot':ai,'excessVariance':av},'benchmarkEvidence':{'reason':br,'invalidSlot':bi},'quality':peer_quality()}


def reference_expansion(group,days,raw,benchmark):
    """All eleven bars are required even though returns use three endpoints."""
    members = group['members']
    group_issue = 'formationGroupNotComplete' if group['kind'] != 'formed' or len(members)!=20 else None
    br,bi = window_issue(benchmark,days)
    base = None
    if br is None:
        try:
            base = (math.log(benchmark[5]['close']/benchmark[0]['close']),math.log(benchmark[10]['close']/benchmark[5]['close']))
            if not all(finite(v) for v in base): br = 'nonfiniteBenchmarkLogReturn'
        except (ValueError,OverflowError,ZeroDivisionError): br = 'nonfiniteBenchmarkLogReturn'
    problem = group_issue or ('benchmark:'+br if br else None)
    evaluations = []
    for symbol in members:
        row = {'symbol':symbol,'kind':'unevaluable','reason':None,'beforeRelativeLogReturn':None,'nowRelativeLogReturn':None,
            'beforePositive':None,'nowPositive':None,'invalidSlot':None}
        issue,slot = window_issue(raw.get(symbol),days)
        if issue: row.update(reason=issue,invalidSlot=slot)
        elif problem: row['reason'] = problem
        else:
            bars = raw[symbol]
            try:
                before = math.log(bars[5]['close']/bars[0]['close'])-base[0]
                now = math.log(bars[10]['close']/bars[5]['close'])-base[1]
                valid = finite(before) and finite(now)
            except (ValueError,OverflowError,ZeroDivisionError): valid = False
            if valid: row.update(kind='observed',reason='fixedMemberObserved',beforeRelativeLogReturn=before,nowRelativeLogReturn=now,beforePositive=before>0,nowPositive=now>0)
            else: row['reason'] = 'nonfiniteRelativeLogReturn'
        evaluations.append(row)
    observed = sum(r['kind']=='observed' for r in evaluations)
    complete = problem is None and observed == 20
    before = sum(r['beforePositive'] for r in evaluations) if complete else None
    now = sum(r['nowPositive'] for r in evaluations) if complete else None
    change = now-before if complete else None
    conditions = {'nowAtLeast12':now>=12 if complete else None,'increaseAtLeast4':change>=4 if complete else None}
    expanded = all(conditions.values()) if complete else None
    return {'version':VERSION,'anchor':group['anchor'],'formationDate':group['formationDate'],'date':days[-1],
        'kind':'expanded' if expanded else 'notExpanded' if complete else 'unevaluable',
        'reason':'peerExpansionObserved' if expanded else 'peerExpansionThresholdNotMet' if complete else (problem or 'memberPathUnevaluable'),
        'expanded':expanded,'members':deepcopy(members),'fixedDenominator':20,'observedMembers':observed,
        'beforeCount':before,'nowCount':now,'countChange':change,'conditions':conditions,
        'window':{'formationDate':group['formationDate'],'middleDate':days[5],'endDate':days[-1],'closeSessions':11,'beforeReturnSessions':5,'nowReturnSessions':5,'providedCalendarSessions':len(days)},
        'memberEvaluations':evaluations,'benchmarkEvidence':{'reason':br,'invalidSlot':bi},'quality':peer_quality()}


def reference_selection(day,top5,outcomes):
    candidate,baseline,decisions = [],[],[]
    for parent in top5:
        symbol = parent['symbol']; result = outcomes[symbol]
        kind,reason,formation = result['kind'],result['reason'],result['formationDate']
        decisions.append({'symbol':symbol,'rank':parent['rank'],'kind':kind,'reason':reason,'formationDate':formation,
            'baselineAdmitted':True,'candidateAdmitted':kind=='expanded'})
        b = deepcopy(parent)
        b.update(strategyId=CONTROL_ID,parentStrategyId=PARENT_ID,peerExpansionKind=kind,peerExpansionReason=reason,
            formationDate=formation,certified=False,targetAchieved=False)
        baseline.append(b)
        if kind == 'expanded':
            c = deepcopy(b); c['strategyId'] = CANDIDATE_ID; candidate.append(c)
    return {'version':VERSION,'date':day,'candidateId':CANDIDATE_ID,'controlId':CONTROL_ID,
        'signals':candidate+baseline,'candidateSignals':candidate,'baselineSignals':baseline,'decisions':decisions,
        'classificationCounts':dict(Counter(d['kind'] for d in decisions)),'parentTop5Count':len(baseline),
        'candidateCount':len(candidate),'baselineCount':len(baseline),'activeExclusionApplied':False,'certified':False,'targetAchieved':False}


def parent_reference(leader, reference, audit, symbol, day, days, bars, panel, recorded):
    """Rebuild admission with the pinned independent parent predicates.

    Full rank evidence is trusted only through the independently audited panel.
    Supplementary diagnostic reasons are not used to decide F/T qualification.
    """
    kind, passed = leader['expected_kind'](panel,days,bars)
    audit.require(isinstance(recorded,dict),'parent-outcome-object')
    audit.check(recorded.get('kind') == kind and recorded.get('symbol') == symbol and recorded.get('date') == day
        and recorded.get('version') == 'raw-persistent-price-leader-v1' and recorded.get('candidateId') == PARENT_ID,
        'independent-parent-identity-kind',date=day,symbol=symbol)
    predicates = recorded.get('predicates',{})
    audit.require(set(predicates) == {k for k,_,_ in GATES}|{'closeAbovePrior5High'},'parent-six-predicates')
    for field,op,threshold in GATES:
        value = panel.get(field)
        bounded = finite(value) and (-1 if field=='signedPathEfficiency20' else 0) <= value <= 1
        p = predicates[field]
        audit.check(p.get('value') == (value if bounded else None) and p.get('operator') == op and p.get('threshold') == threshold,
            'independent-parent-predicate-value',date=day,symbol=symbol,field=field)
    if passed is not None:
        for field,value in passed.items():
            audit.check(predicates[field].get('passed') is value and predicates[field].get('reason') is None,
                'independent-parent-predicate-truth',date=day,symbol=symbol,field=field)
        audit.check(predicates['closeAbovePrior5High'] == {'value':bars[-1]['close'],'operator':'>',
            'threshold':max(b['high'] for b in bars[-6:-1]),'passed':passed['closeAbovePrior5High'],'reason':None},'independent-parent-breakout')
        f = recorded['features']
        required = {k:panel[k] for k,_,_ in GATES}
        expected_features = {'baseEligibility':{k:deepcopy(panel['baseEligibility'][k]) for k in ('eligible','reason','unknown')},
            'historyStart':days[0],'historyEnd':day,'observations':61,'panelFeatureVersion':'daily-leadership-raw-v1',
            'archivedRankDates20':list(panel['strengthRankSessionDates20']),
            'archivedRankPercentiles20':list(panel['trailing20DailyStrengthPercentiles']),
            'archivedRankCrossSectionCounts20':list(panel['trailing20StrengthCrossSectionCounts']),
            'prior5High':max(b['high'] for b in bars[-6:-1]),'prior5Window':{'startDate':days[-6],'endDate':days[-2],'sessions':5},
            'breakoutBars':[{k:b[k] for k in ('date','open','high','low','close','volume')} for b in bars[-6:]],
            'requiredPanelValues':required,'failedPredicates':[k for k,v in passed.items() if v is False]}
        audit.check(reference.exact(f,expected_features),'independent-parent-complete-features',date=day,symbol=symbol)
        expected_reason = 'persistentPriceLeadershipBreakoutObserved' if kind=='signaled' else expected_features['failedPredicates'][0]+'NotMet'
        audit.check(recorded['reason']==expected_reason,'independent-parent-observed-reason')
    quality = {'certified':False,'targetAchieved':False,'priceBasisId':'raw-unadjusted','actionEvidence':'unverified',
        'historicalEligibilityVerified':False,'interpretation':'price-leadership-diagnostic;not-market-influence',
        'unknownHistoricalFields':deepcopy(panel['baseEligibility']['unknown'])}
    audit.check(reference.exact(recorded.get('quality'),quality),'parent-diagnostic-quality')
    five = {k:passed[k] if passed is not None else predicates[k]['passed'] for k,_,_ in GATES}
    summary = {'date':day,'kind':kind,'reason':recorded['reason'],'fiveThresholdsPass':five,
        'strong':kind in ('signaled','notTriggered') and all(v is True for v in five.values()),'outcomeSha256':digest(recorded)}
    signal = None
    if kind=='signaled':
        signal = {'version':'raw-persistent-price-leader-v1','strategyId':PARENT_ID,'symbol':symbol,'signalDate':day,
            'meanStrengthPercentile20':panel['meanStrengthPercentile20'],'relativeReturn60Percentile':panel['relativeReturn60Percentile'],
            'features':deepcopy(recorded['features']),'predicates':deepcopy(recorded['predicates']),'quality':quality,
            'certified':False,'targetAchieved':False,'actionEvidence':'unverified'}
    return kind,summary,signal


def replay(reference, leader, binding, data, audit):
    securities = {s['symbol']:s for s in data['securities']}
    sources = {**securities,'__benchmark__':data['benchmark']}
    bars = {symbol:{b['date']:b for b in security['bars']} for symbol,security in sources.items()}
    calendar,inputs = data['calendar'],binding['manifest']['inputs']
    indices = {d:i for i,d in enumerate(calendar)}
    def raw(symbol,days):
        statuses = sources[symbol].get('tradeStatusByDate',{})
        output = []
        for d in days:
            status = statuses.get(d)
            halted = status is False or type(status) is int and status==0 or status=='0'
            output.append({**bars[symbol][d],'priceBasisId':'raw-unadjusted','knownSuspended':halted} if d in bars[symbol] else None)
        return output
    features = iter(records(binding['panel']/'features.jsonl.gz'))
    streams = {key:iter(records(binding['attempt']/ARTIFACTS[key])) for key in
        ('predicates','candidates','coverage','selections','peerGroups','peerExpansion','signals')}
    history = deque(maxlen=10)
    signals,all_slots = [],[]
    totals,temporal_totals,expansion_totals = Counter(),Counter(),Counter()
    parent_candidates = peer_records = 0
    notice = time.monotonic()
    for ordinal,day in enumerate(binding['dates']):
        index = indices[day]; days = calendar[max(0,index-70):index+1]
        formation = calendar[index-10] if index>=10 else None
        available = ordinal>=10
        if available:
            audit.require(history[0][0]==formation,'exact-F-calendar-predecessor')
            formation_rows,previous = history[0][1:]
        current,rows,ranked = {},{},[]
        counts,temporal = Counter(),Counter()
        for symbol in binding['symbols']:
            row = next(features,None)
            hashes = {**inputs['securities'][symbol],'benchmarkRawBarsSha256':inputs['benchmark']['rawBarsSha256']}
            audit.require(isinstance(row,dict) and row.get('symbol')==symbol and row.get('asOfDate')==day
                and row.get('featureVersion')=='daily-leadership-raw-v1' and row.get('sourceHashes')==hashes
                and row.get('calendarHash')==binding['identity']['calendarHash']
                and row.get('sampleManifestHash')==binding['identity']['panelManifestSha256'],'source-panel-row-identity')
            rows[symbol] = row
            actual = next(streams['predicates'],None)
            audit.require(isinstance(actual,dict),'independent-all-population-predicate-row')
            kind,parent,signal = parent_reference(leader,reference,audit,symbol,day,days[-61:],raw(symbol,days[-61:]),row,actual.get('currentParent'))
            current[symbol] = parent
            prior = previous[symbol] if available else {'date':formation,'kind':'unevaluable','reason':'formationPanelUnavailable',
                'fiveThresholdsPass':{k:None for k,_,_ in GATES},'strong':None,'outcomeSha256':None}
            classification = 'formationUnavailable' if not available else 'currentRejected' if kind!='signaled' else 'formationRejected' if prior['strong'] is not True else 'temporalCandidate'
            expected = {'version':VERSION,'symbol':symbol,'date':day,'currentParent':actual['currentParent'],'formationParent':prior,
                'temporalClassification':classification,'panelManifestSha256':binding['identity']['panelManifestSha256'],
                'sourceHashes':hashes,'calendarHash':row['calendarHash'],'certified':False}
            audit.check(reference.exact(actual,expected),'independent-all-population-predicate-row',date=day,symbol=symbol)
            if classification=='temporalCandidate':
                signal.update(formationDate=formation,formationParent=deepcopy(prior),eligible=True)
                ranked.append(signal)
            counts[kind]+=1; temporal[classification]+=1
        history.append((day,rows,current))
        ranked.sort(key=lambda r:(-r['meanStrengthPercentile20'],-r['relativeReturn60Percentile'],r['symbol']))
        for rank,row in enumerate(ranked,1):
            row.update(rank=rank,selectedTop5=rank<=5,score=row['meanStrengthPercentile20'],rawScore=row['meanStrengthPercentile20'])
            audit.check(reference.exact(next(streams['candidates'],None),row),'independent-F-T-full-ranking',date=day,symbol=row['symbol'])
        top = ranked[:5]; outcomes,groups,sets = {},{},{}
        daily_expansion = Counter()
        if top:
            formation_raw = {s:raw(s,days[:-10]) for s in binding['symbols']}
            response_raw = {s:raw(s,days[-11:]) for s in binding['symbols']}
            eligibility_rows = {s:formation_rows[s]['baseEligibility'] for s in binding['symbols']}
            for row in top:
                symbol = row['symbol']
                group = reference_group(symbol,formation,days[:-10],formation_raw,raw('__benchmark__',days[:-10]),eligibility_rows)
                group_hash = digest(group); groups[symbol]=group_hash; sets[symbol]=set(group['members'])
                base = {'date':day,'symbol':symbol,'rank':row['rank'],'formationDate':formation,'groupSha256':group_hash}
                audit.check(reference.exact(next(streams['peerGroups'],None),{**base,'group':group}),'independent-frozen-peer-group',date=day,symbol=symbol)
                expansion = reference_expansion(group,days[-11:],response_raw,raw('__benchmark__',days[-11:]))
                audit.check(reference.exact(next(streams['peerExpansion'],None),{**base,'expansion':expansion}),'independent-fixed-member-expansion',date=day,symbol=symbol)
                outcomes[symbol]=expansion; daily_expansion[expansion['kind']]+=1; peer_records+=len(group['candidateEvaluations'])
        selected = reference_selection(day,top,outcomes)
        slots = [{'date':day,'symbol':r['symbol'],'rank':r['rank'],'formationDate':formation,'groupSha256':groups[r['symbol']],
            'expansionKind':outcomes[r['symbol']]['kind'],'expansionReason':outcomes[r['symbol']]['reason'],
            'candidateAdmitted':outcomes[r['symbol']]['kind']=='expanded','baselineAdmitted':True} for r in top]
        overlap = [{'left':a['symbol'],'right':b['symbol'],'sharedMembers':sorted(sets[a['symbol']] & sets[b['symbol']]),
            'sharedCount':len(sets[a['symbol']] & sets[b['symbol']])} for i,a in enumerate(top) for b in top[i+1:]]
        selection = {'date':day,'selection':selected,'slots':slots,'membershipOverlap':overlap,'activeExclusionApplied':False,'noBackfill':True}
        audit.check(reference.exact(next(streams['selections'],None),selection),'independent-nested-Top5-mask',date=day)
        for signal in selected['signals']:
            audit.check(reference.exact(next(streams['signals'],None),signal),'independent-selected-signal',date=day,symbol=signal['symbol'])
        daily = {'date':day,'evaluatedSecurities':len(rows),'currentParentCounts':{k:counts[k] for k in KINDS},
            'temporalCounts':dict(temporal),'parentCandidates':len(ranked),'parentTop5':len(top),
            'expansionCounts':{k:daily_expansion[k] for k in EXPANSION_KINDS},'candidateSignals':daily_expansion['expanded'],
            'baselineSignals':len(top),'certified':False}
        audit.check(reference.exact(next(streams['coverage'],None),daily),'independent-full-population-coverage',date=day)
        totals.update(counts); temporal_totals.update(temporal); expansion_totals.update(daily_expansion)
        parent_candidates+=len(ranked); signals.extend(selected['signals']); all_slots.extend(slots)
        if time.monotonic()-notice>=30:
            print(f'Independently checked {ordinal+1}/{len(binding["dates"])} leader-peer days',flush=True); notice=time.monotonic()
    audit.require(next(features,None) is None,'source-panel-exact-EOF')
    for key,stream in streams.items(): audit.require(next(stream,None) is None,'output-exact-EOF:'+key)
    performance = {'scoredSessions':len(binding['dates']),'evaluatedSecuritySessions':sum(totals.values()),
        'currentParentCounts':{k:totals[k] for k in KINDS},'temporalCounts':dict(temporal_totals),'parentCandidates':parent_candidates,
        'parentTop5':len(all_slots),'expansionCounts':{k:expansion_totals[k] for k in EXPANSION_KINDS},
        'sourcePeerCandidateRecords':peer_records,'candidateSignalCount':expansion_totals['expanded'],'controlSignalCount':len(all_slots)}
    return signals,all_slots,performance


def audit_slots(reference,result,slots,attempt,audit):
    key = lambda e:(e['strategyId'],e['symbol'],e['signalDate'])
    events = {key(e):e for e in result['events']}
    suppressed = {key(e):e for e in result['overlapSuppressed']}
    stream = iter(records(attempt/'slot-outcomes.jsonl.gz')); checked=[]
    def disposition(group,slot):
        if group==CANDIDATE_ID and not slot['candidateAdmitted']:
            return {'status':'filteredUnknown' if slot['expansionKind']=='unevaluable' else 'filteredNotExpanded','entryStatus':None,'eventId':None}
        identity = (group,slot['symbol'],slot['date'])
        if identity in suppressed:
            e=suppressed[identity]
            return {'status':'activeSuppressed','eventId':None,'entryStatus':None,'reason':e['reason'],'blockedByEventId':e['blockedByEventId'],'activeUntil':e['activeUntil']}
        e=events[identity]
        return {'status':'attempted','eventId':e['eventId'],'entryStatus':e['entry']['status'],'entryReason':e['entry'].get('reason'),
            'entryDate':e['entry'].get('date'),'targetDate':e['outcomes']['nextOpenNet30']['hitDate'],'terminal':deepcopy(e['terminal'])}
    for slot in slots:
        row = {**slot,'candidateDisposition':disposition(CANDIDATE_ID,slot),'baselineDisposition':disposition(CONTROL_ID,slot),
            'interpretation':'nested-intentions;separate-active-state;not-matched-or-independent-observations'}
        audit.check(reference.exact(next(stream,None),row),'independent-original-slot-dispositions',date=slot['date'],symbol=slot['symbol'])
        checked.append(row)
    audit.require(next(stream,None) is None,'slot-outcomes-exact-EOF')
    summary = {'parentTop5':len(slots),'candidateAdmitted':sum(s['candidateAdmitted'] for s in slots),'baselineAdmitted':len(slots),
        'expansionCounts':dict(Counter(s['expansionKind'] for s in slots))}
    for name in ('candidate','baseline'):
        sides=[r[name+'Disposition'] for r in checked]
        summary[name+'Dispositions']=dict(Counter(s['status'] for s in sides))
        summary[name+'EntryStatuses']=dict(Counter(s['entryStatus'] for s in sides if s['entryStatus'] is not None))
    audit.check(reference.exact(result.get('slotAccounting'),summary),'independent-slot-accounting')
    return summary


def verify(output:Path,root:Path=ROOT):
    root,output=Path(root).resolve(),Path(output).resolve(); started=time.monotonic(); audit=None
    report={'schema':'leader-peer-expansion-independent-verification-v1','checkedAt':datetime.now(timezone.utc).isoformat(),
        'passed':False,'verificationCompleted':False,'candidateId':CANDIDATE_ID,'registrationSha256':REGISTRATION_SHA256,
        'verifierSha256':VERIFIER_SHA256,'certified':False,'targetAchieved':False,'actualOpeningExecutionVerified':False,
        'comparableRightsVerified':False,'scope':'independent F/T parent predicates, frozen peer correlation and expansion, Top5 mask, source closure and registered-cost event arithmetic',
        'limitations':['Audited panel strength values are bound inputs; this auditor does not recompute their cross-sectional ranks.',
            'Recorded event arithmetic does not certify the first feasible auction fill, capacity or historical fee calendar.',
            'Candidate and temporal leader-only baseline have separate active state; comparison is descriptive, not causal.',
            'Benchmark-excess correlation does not identify themes, leader influence or control; overlapping groups are not independent evidence.']}
    try:
        reference,source,leader=load_references(root)
        reference.CANDIDATE_ID,reference.GROUPS=CANDIDATE_ID,GROUPS
        audit=reference.Audit(); binding=bind(reference,output,root,audit)
        data=source.load_source(reference,binding,root,audit); original_digest=digest(data)
        signals,slots,performance=replay(reference,leader,binding,data,audit)
        with gzip.open(binding['attempt']/'result.json.gz','rt',encoding='utf-8') as stream: result=parse(stream.read())
        summary=read(binding['attempt']/'summary.json')
        audit.require(result.get('protocolHash')==binding['protocol']['protocolHash'] and result.get('snapshotFingerprint')==data['fingerprint']
            and result.get('strategyOrder')==[CANDIDATE_ID] and result.get('diagnosticUnverifiedHistory') is True,'result-registered-identity')
        for flag in ('certified','targetAchieved'): audit.require(result.get(flag) is False and summary.get(flag) is False,'result-diagnostic:'+flag)
        base_summary={k:v for k,v in result.items() if k not in ('events','pathRiskEvents','overlapSuppressed')}
        audit.check(reference.exact({k:v for k,v in summary.items() if k not in ('performance','ranking','selectionEligible')},base_summary),'summary-mirrors-result')
        audit.check(summary.get('ranking') is None and summary.get('selectionEligible') is False,'no-ranked-certification')
        for key,value in performance.items():
            audit.check(reference.exact(summary['performance'].get(key),value),'performance:'+key)
            audit.check(reference.exact(binding['journal'][1].get(key),value),'journal-scoring:'+key)
        for key in ('currentParentCounts','temporalCounts','expansionCounts'):
            audit.check(reference.exact(result.get(key),performance[key]),'result:'+key)
        for key in ('scoringSeconds','eventEvaluationSeconds'):
            audit.check(finite(summary['performance'].get(key)) and summary['performance'][key]>=0,'nonnegative-timing:'+key)
        adapted={'originalRawSourcePanelBinding':{'targetDates':binding['dates']},'frozenExecutionConfig':binding['document']['config']}
        report['eventsChecked']=reference.event_audit(audit,result,signals,data,adapted,root)
        report['slotAccounting']=audit_slots(reference,result,slots,binding['attempt'],audit)
        for group in result['groups']:
            selected=[s for s in signals if s['strategyId']==group['strategyId']]
            audit.check(group.get('distinctSignalSymbols')==len({s['symbol'] for s in selected}) and group.get('distinctSignalDates')==len({s['signalDate'] for s in selected}),'group-distinct-signal-counts')
        report.update(targetSessions=len(binding['dates']),evaluatedSecuritySessions=performance['evaluatedSecuritySessions'],
            currentParentCounts=performance['currentParentCounts'],temporalCounts=performance['temporalCounts'],expansionCounts=performance['expansionCounts'],
            snapshotFingerprint=data['fingerprint'],protocolHash=binding['protocol']['protocolHash'],
            completionSha256=sha(binding['attempt']/'completion.json'),resultSha256=sha(binding['attempt']/'result.json.gz'),implementation=binding['protocol']['implementation'])
        audit.require(digest(data)==original_digest,'source-object-unmodified')
        for path,expected in audit.bound.items(): audit.require(sha(path)==expected,'bound-file-unchanged:'+path.name)
        report['verificationCompleted']=True;report['passed']=audit.failure_count==0
    except Exception as error:
        report.update(errorType=type(error).__name__,error=str(error))
    report.update(checks=audit.checks if audit else 0,failureCount=audit.failure_count if audit else 1,
        issues=audit.issues if audit else [],elapsedSeconds=time.monotonic()-started)
    if not report['verificationCompleted'] and report['failureCount']==0: report['failureCount']=1
    return report


def save_report(output,report):
    name='independent-result-verification.json' if report['passed'] else 'independent-result-verification-failed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'
    path=Path(output)/name
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,ensure_ascii=False,allow_nan=False,sort_keys=True,indent=2);stream.write('\n')
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/strategy-search/signal-leader-peer-expansion-v1')
    args=parser.parse_args(argv); report=verify(args.output); path=save_report(args.output,report)
    print(json.dumps({'passed':report['passed'],'checks':report['checks'],'failureCount':report['failureCount'],'report':str(path)}))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

