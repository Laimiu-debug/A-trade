"""Independent temporal follower confirmation and recorded-event audit.

Parent feature/correlation calculations are inherited only through the pinned
successful parent audit.  The new episode state, ownership, three policies and
their active dispositions are independently reconstructed.  No new production
rule or runner is imported.  This does not certify auction feasibility, rights,
broker charges, whole-market membership or a future return probability.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / 'docs/signal-leader-follower-confirmation-v1.json'
REGISTRATION_SHA256 = 'b6f382b3edea4ae91ac197ac6b3286d24f2e6a0fb220e3b15e410c0d4b30ae3a'
VERSION = 'raw-leader-follower-confirmation-v1'
CANDIDATE_ID = 'rawLeaderFollowerConfirmation'
MEMBER_CONTROL_ID = 'control_rawLeaderFollowerConfirmationMember'
ANCHOR_CONTROL_ID = 'control_rawLeaderFollowerConfirmationAnchor'
GROUPS = (CANDIDATE_ID, MEMBER_CONTROL_ID, ANCHOR_CONTROL_ID)
PARENT_VERSION = 'raw-persistent-price-leader-v1'
PARENT_ID = 'rawPersistentPriceLeaderBreakoutDiagnostic'
PARENT_REGISTRATION = 'docs/signal-leader-peer-expansion-v1.json'
PEER_REFERENCE = 'scripts/verify-signal-leader-peer-expansion-result.py'
EVENT_REFERENCE = 'scripts/verify-signal-market-repair-result.py'
LEADER_REFERENCE = 'scripts/verify-signal-persistent-leader-result.py'
SOURCE_REFERENCE = 'scripts/verify-signal-session-decomposition-result.py'
DEPENDENCIES = {
    PEER_REFERENCE: 'e57fbf05c066b04eb616de3c927c39d929aad3313c5101110ddb4a9e22fee5d2',
    EVENT_REFERENCE: '922f9872264fd27a2b3c09e2f8586e5698119b75c2fdc6ba569f953c46f7b1bf',
    LEADER_REFERENCE: '3e9a02ba8f48a34cd81af0f30108d94a0a1b30b60396905f185e37f491785222',
    SOURCE_REFERENCE: 'fd71659842d12ed716305b35cef7f23d8f6f42d82906d4faae5918636f62e10a',
}
GATE_FIELDS = ('meanStrengthPercentile20', 'minimumStrengthPercentile20',
               'relativeReturn60Percentile', 'closePathMaxDrawdown20', 'signedPathEfficiency20')
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
ARTIFACTS = {
    'formations': 'formations.jsonl.gz', 'transitions': 'transitions.jsonl.gz',
    'episodeTerminals': 'episode-terminals.jsonl.gz', 'confirmations': 'confirmations.jsonl.gz',
    'selections': 'selection.jsonl.gz', 'coverage': 'coverage.jsonl.gz',
    'signals': 'signals.jsonl.gz', 'slotOutcomes': 'slot-outcomes.jsonl.gz',
    'result': 'result.json.gz', 'summary': 'summary.json', 'attempt': 'attempt.json',
}
NEW_FILES = {
    'service/signal_leader_follower_confirmation.py',
    'scripts/run-signal-leader-follower-confirmation.py',
    'scripts/verify-signal-leader-follower-confirmation-result.py',
    'tests/test_signal_leader_follower_confirmation.py',
    'tests/test_signal_leader_follower_confirmation_runner.py',
    'tests/test_signal_leader_follower_confirmation_verifier.py',
}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


VERIFIER_PATH = Path(__file__).resolve()
VERIFIER_SHA256 = sha(VERIFIER_PATH)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def parse(value):
    def unique(items):
        output = {}
        for key, item in items:
            if key in output:
                raise ValueError('Duplicate JSON key: ' + key)
            output[key] = item
        return output
    def nonfinite(value):
        raise ValueError('Nonfinite JSON: ' + value)
    return json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)


def read(path):
    return parse(Path(path).read_bytes())


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = parse(line)
            if not isinstance(row, dict):
                raise ValueError('JSONL row must be an object')
            yield row


def project_parent(outcome):
    """Independent three-valued projection; false requires fully known gates."""
    if not isinstance(outcome, dict):
        raise ValueError('Parent outcome must be an object')
    predicates = outcome.get('predicates', {})
    values = {}
    for key in GATE_FIELDS:
        item = predicates.get(key) if isinstance(predicates, dict) else None
        value = item.get('passed') if isinstance(item, dict) else None
        values[key] = value if type(value) is bool else None
    state = 'unknown'
    if outcome.get('kind') in ('signaled', 'notTriggered') and all(type(v) is bool for v in values.values()):
        state = 'true' if all(values.values()) else 'false'
    return {'version': VERSION, 'parentVersion': outcome.get('version'),
            'parentCandidateId': outcome.get('candidateId'), 'symbol': outcome.get('symbol'),
            'date': outcome.get('date'), 'kind': outcome.get('kind'), 'reason': outcome.get('reason'),
            'eligibleParentQ': state, 'breakout': outcome.get('kind') == 'signaled',
            'fivePassed': values, 'outcomeSha256': digest(outcome)}


def load_references(root):
    for name, expected in DEPENDENCIES.items():
        if sha(root / name) != expected:
            raise ValueError('Frozen independent dependency changed: ' + name)
    def module(name, relative):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value
    event = module('_follower_independent_events', EVENT_REFERENCE)
    peer = module('_follower_inherited_peer_binding', PEER_REFERENCE)
    source = module('_follower_independent_source', SOURCE_REFERENCE)
    event.CANDIDATE_ID, event.GROUPS = CANDIDATE_ID, GROUPS
    original_extract = event.ast_functions
    adaptation = {}

    def extract(path, names, namespace):
        if Path(path).resolve() != (root / LEADER_REFERENCE).resolve() or 'audit_outcomes' not in names:
            return original_extract(path, names, namespace)
        tree = ast.parse(Path(path).read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        if {node.name for node in functions} != set(names):
            raise ValueError('Frozen event function set changed')
        function = next(node for node in functions if node.name == 'audit_outcomes')
        # These are the only two schema adaptations. All event/path equations
        # and lifecycle logic are left in the hash-pinned original AST.
        expected = ast.parse("expected_groups = {CANDIDATE_ID, 'control_' + CANDIDATE_ID}").body[0]
        if ast.dump(function.body[0]) != ast.dump(expected):
            raise ValueError('Frozen two-group declaration changed')
        function.body[0] = ast.parse('expected_groups = set(GROUPS)').body[0]
        check = function.body[2]
        if not isinstance(check, ast.Expr) or not isinstance(check.value, ast.Call) or check.value.args[1].value != 'complete-unique-target-groups':
            raise ValueError('Frozen two-group completeness check changed')
        function.body[2] = ast.parse("check(len(group_ids) == len(GROUPS) and set(group_ids) == expected_groups, 'complete-unique-target-groups')").body[0]
        modified = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
        adaptation['effectiveAstSha256'] = hashlib.sha256(ast.dump(modified, include_attributes=False).encode()).hexdigest()
        namespace['GROUPS'] = GROUPS
        exec(compile(modified, str(path), 'exec'), namespace)
        return namespace

    event.ast_functions = extract
    return event, peer, source, adaptation


def bind_parent(reference, peer, root, audit):
    audit.pin(REGISTRATION, REGISTRATION_SHA256)
    doc = read(REGISTRATION)
    audit.require(doc.get('candidateId') == CANDIDATE_ID and doc.get('controlIds') == list(GROUPS[1:]),
                  'registered-three-policies')
    source = doc['sourceBinding']
    directory = (root / source['parentDirectory']).resolve()
    audit.require(directory.is_relative_to(root), 'parent-directory-contained')
    files = {}
    for name, expected in source['files'].items():
        path = reference.within(root, name)
        audit.require(not path.is_symlink(), 'parent-source-not-linked')
        files[str(path)] = audit.pin(path, expected)
    peer.REGISTRATION = root / PARENT_REGISTRATION
    peer.REGISTRATION_SHA256 = source['files'][PARENT_REGISTRATION]
    inherited = peer.bind(reference, directory, root, audit)
    protocol, receipt = inherited['protocol'], inherited['completion']
    proof_path = directory / 'independent-result-verification.json'
    proof = read(proof_path)
    audit.require(protocol['protocolHash'] == source['parentProtocolHash']
        and reference.exact(doc['sample'], inherited['document']['sample'])
        and reference.exact(doc['config'], inherited['document']['config']), 'inherited-sample-config-protocol')
    expected_files = {PARENT_REGISTRATION,
        (directory / 'attempts/attempt-0001/completion.json').relative_to(root).as_posix(),
        proof_path.relative_to(root).as_posix()}
    expected_files.update((directory / row['path']).resolve().relative_to(root).as_posix()
                          for row in receipt['files'].values())
    audit.require(set(source['files']) == expected_files, 'parent-complete-receipt-and-proof-pins')
    audit.require(proof.get('passed') is True and proof.get('verificationCompleted') is True
        and type(proof.get('failureCount')) is int and proof['failureCount'] == 0
        and type(proof.get('checks')) is int and proof['checks'] > 0
        and proof.get('schema') == 'leader-peer-expansion-independent-verification-v1'
        and proof.get('candidateId') == 'rawLeaderPeerExpansion'
        and proof.get('registrationSha256') == protocol['registrationSha256']
        and proof.get('protocolHash') == protocol['protocolHash']
        and proof.get('completionSha256') == sha(directory / 'attempts/attempt-0001/completion.json')
        and proof.get('resultSha256') == receipt['files']['result']['sha256']
        and proof.get('implementation') == protocol['implementation']
        and proof.get('verifierSha256') == DEPENDENCIES[PEER_REFERENCE], 'accepted-inherited-independent-proof')
    files.update(inherited['identity']['inputFilesSha256'])
    for name in ('freeze-attempt.json', 'events.jsonl'):
        path = directory / name
        files[str(path)] = audit.pin(path)
    files[str(REGISTRATION.resolve())] = REGISTRATION_SHA256
    identity = {'registrationSha256': REGISTRATION_SHA256, 'parentProtocolHash': protocol['protocolHash'],
        'parentCompletionSha256': proof['completionSha256'], 'parentAuditSha256': sha(proof_path),
        'parentPredicatesSha256': receipt['files']['predicates']['sha256'],
        'parentSelectionsSha256': receipt['files']['selections']['sha256'],
        'parentPeerGroupsSha256': receipt['files']['peerGroups']['sha256'],
        'snapshotFingerprint': inherited['identity']['snapshotFingerprint'],
        'calendarHash': inherited['identity']['calendarHash'], 'symbolsSha256': inherited['identity']['symbolsSha256'],
        'inputFilesSha256': files}
    return {'document': doc, 'parent': inherited, 'parentAudit': proof,
            'parentDirectory': directory, 'identity': identity,
            **{key: inherited[key] for key in ('dates', 'symbols', 'calendar', 'manifest', 'workspace')}}


def inherited_days(binding, reference, audit):
    """Consume the complete inherited population; never inspect old returns."""
    parent = binding['parent']
    inputs = binding['manifest']['inputs']
    symbols = binding['symbols']
    streams = {key: iter(records(parent['attempt'] / peer_name)) for key, peer_name in
        {'predicates': 'predicates.jsonl.gz', 'selections': 'selection.jsonl.gz', 'groups': 'peer-groups.jsonl.gz'}.items()}
    positions = {day: i for i, day in enumerate(binding['calendar'])}
    line = opportunities = 0
    for index, day in enumerate(binding['dates']):
        rows, start = {}, line + 1
        for symbol in symbols:
            row = next(streams['predicates'], None)
            line += 1
            expected_sources = {**inputs['securities'][symbol],
                'benchmarkRawBarsSha256': inputs['benchmark']['rawBarsSha256']}
            audit.require(isinstance(row, dict) and row.get('date') == day and row.get('symbol') == symbol
                and row.get('version') == 'raw-leader-peer-expansion-v1' and row.get('certified') is False
                and row.get('sourceHashes') == expected_sources and row.get('calendarHash') == binding['identity']['calendarHash']
                and row.get('panelManifestSha256') == parent['identity']['panelManifestSha256'], 'inherited-all-row-identity-order')
            outcome = row.get('currentParent')
            audit.require(isinstance(outcome, dict) and outcome.get('symbol') == symbol and outcome.get('date') == day
                and outcome.get('version') == PARENT_VERSION and outcome.get('candidateId') == PARENT_ID
                and outcome.get('kind') in ('ineligible', 'unevaluable', 'notTriggered', 'signaled'), 'inherited-current-parent-identity')
            rows[symbol] = project_parent(outcome)
        selection = next(streams['selections'], None)
        audit.require(isinstance(selection, dict) and selection.get('date') == day
            and selection.get('activeExclusionApplied') is False and selection.get('noBackfill') is True,
            'inherited-selection-order')
        slots = selection.get('slots')
        audit.require(isinstance(slots, list) and len(slots) <= 5, 'inherited-Top5-slots')
        groups, seen = [], set()
        for rank, slot in enumerate(slots, 1):
            anchor = slot['symbol']
            audit.require(slot.get('date') == day and type(slot.get('rank')) is int and slot['rank'] == rank
                and anchor in rows and anchor not in seen and positions[day] >= 10
                and slot.get('formationDate') == binding['calendar'][positions[day]-10], 'inherited-exact-original-Top5')
            audit.require(rows[anchor]['kind'] == 'signaled' and rows[anchor]['eligibleParentQ'] == 'true',
                          'inherited-strong-anchor-at-activation')
            group = next(streams['groups'], None)
            audit.require(isinstance(group, dict) and group.get('date') == day and group.get('symbol') == anchor
                and type(group.get('rank')) is int and group['rank'] == rank
                and group.get('formationDate') == slot['formationDate']
                and group.get('groupSha256') == slot.get('groupSha256') == digest(group.get('group'))
                and group['group'].get('anchor') == anchor and group['group'].get('formationDate') == slot['formationDate'],
                'inherited-group-source-linkage')
            value, members = group['group'], group['group'].get('members')
            audit.require(value.get('version') == 'raw-leader-peer-expansion-v1'
                and value.get('kind') in ('formed', 'unevaluable') and isinstance(members, list)
                and all(isinstance(s, str) and s in symbols and s != anchor for s in members)
                and len(members) == len(set(members)) <= 20
                and type(value.get('selectedCount')) is int and value['selectedCount'] == len(members)
                and type(value.get('requiredCount')) is int and value['requiredCount'] == 20,
                'inherited-group-membership-structure')
            if value['kind'] == 'formed':
                rho = value.get('correlations')
                audit.require(len(members) == 20 and isinstance(rho, dict) and set(rho) == set(members)
                    and all(type(v) in (int, float) and math.isfinite(v) and .3 <= v <= 1 for v in rho.values()),
                    'inherited-formed-group-domain')
            seen.add(anchor)
            groups.append(group)
            opportunities += 1
        baseline = selection.get('selection', {}).get('baselineSignals', [])
        audit.require([(row.get('symbol'), row.get('rank')) for row in baseline] ==
                      [(row['symbol'], row['rank']) for row in slots], 'inherited-unmasked-parent-baseline')
        source = {'parentProtocolHash': binding['identity']['parentProtocolHash'],
            'predicatesFileSha256': binding['identity']['parentPredicatesSha256'],
            'selectionsFileSha256': binding['identity']['parentSelectionsSha256'],
            'peerGroupsFileSha256': binding['identity']['parentPeerGroupsSha256'],
            'predicateStartLine': start, 'predicateEndLine': line}
        yield day, index, rows, groups, source
    for name, stream in streams.items():
        audit.require(next(stream, None) is None, 'inherited-exact-EOF:' + name)
    audit.require(line == len(symbols) * len(binding['dates']) and
        opportunities == binding['parentAudit']['slotAccounting']['parentTop5'], 'inherited-complete-population-opportunities')


def reference_selection(day, confirmations):
    ordered = sorted(deepcopy(confirmations), key=lambda row: (row['formationDate'], row['formationParentRank'],
                     row['anchorSymbol'], row['frozenMemberRank'], row['memberSymbol']))
    members = list(dict.fromkeys(row['memberSymbol'] for row in ordered))
    owners, ownership = [], []
    for member in members:
        support = [row for row in ordered if row['memberSymbol'] == member]
        owners.append(support[0])
        ownership.append({'memberSymbol': member, 'ownerRawConfirmationId': support[0]['rawConfirmationId'],
            'ownerEpisodeId': support[0]['episodeId'], 'allRawConfirmationIds': [r['rawConfirmationId'] for r in support],
            'discardedRawConfirmationIds': [r['rawConfirmationId'] for r in support[1:]]})
    ranking = [{**row, 'rank': i, 'selectedTop5': i <= 5} for i, row in enumerate(owners, 1)]
    slots, anchor_slots = [], {}
    for row in ranking[:5]:
        alive = row['anchorGate'] == 'alive'
        anchor = row['anchorSymbol']
        disposition = ('anchorCancelled' if row['anchorGate'] == 'cancelled' else 'anchorUnknown') if not alive else (
            'mappedAnchorCollision' if anchor in anchor_slots else 'admitted')
        if disposition == 'admitted':
            anchor_slots[anchor] = row['rank']
        keys = ('date', 'memberSymbol', 'anchorSymbol', 'episodeId', 'rawConfirmationId', 'formationDate',
                'formationIndex', 'formationParentRank', 'peerFormationDate', 'frozenMemberRank', 'groupSha256',
                'anchorGate', 'anchorGateTerminal', 'rank')
        slot = {key: deepcopy(row[key]) for key in keys}
        slot.update(candidateAdmitted=alive, memberBaselineAdmitted=True,
            anchorControlAdmitted=disposition == 'admitted', anchorControlDisposition=disposition,
            anchorControlRetainedRank=anchor_slots.get(anchor) if alive else None)
        slots.append(slot)
    signals = []
    for sid, field in zip(GROUPS, ('candidateAdmitted', 'memberBaselineAdmitted', 'anchorControlAdmitted')):
        for slot in slots:
            if slot[field]:
                signals.append({**deepcopy(slot), 'version': VERSION, 'strategyId': sid,
                    'symbol': slot['anchorSymbol'] if sid == ANCHOR_CONTROL_ID else slot['memberSymbol'],
                    'signalDate': day, 'selectedTop5': True, 'eligible': True,
                    'certified': False, 'targetAchieved': False, 'actionEvidence': 'unverified'})
    return ordered, {'date': day, 'rawConfirmationCount': len(ordered), 'uniqueMemberCount': len(owners),
        'fullRanking': ranking, 'ownership': ownership, 'slots': slots, 'signals': signals,
        'policyCounts': {sid: sum(r['strategyId'] == sid for r in signals) for sid in GROUPS},
        'activeExclusionApplied': False, 'noBackfill': True, 'certified': False, 'targetAchieved': False}


class ReferenceEpisodes:
    """Prefix oracle: rederive terminal states from each episode's observations.

    This does not reproduce the production state mutation algorithm. Each
    active episode retains at most five observed daily compact-parent maps;
    first terminal indexes are searched anew in that causal prefix.
    """
    def __init__(self, dates):
        self.dates = list(dates)
        self.entries = []
        self.index = -1

    def snapshot(self, entry):
        initial, observations = entry
        result = deepcopy(initial)
        anchor = result['anchorSymbol']
        terminal = next(((index, rows[anchor]) for index, rows in observations
                         if rows[anchor]['eligibleParentQ'] != 'true'), None)
        if terminal:
            index, row = terminal
            result['anchorGate'] = 'cancelled' if row['eligibleParentQ'] == 'false' else 'unknown'
            result['anchorGateTerminal'] = {'date': self.dates[index], 'index': index,
                'eligibleParentQ': row['eligibleParentQ'], 'reason': row['reason'],
                'parentKind': row['kind'], 'parentOutcomeSha256': row['outcomeSha256']}
        for member in result['members']:
            if member['state'] != 'observing':
                continue
            first = next(((index, rows[member['symbol']]) for index, rows in observations
                          if rows[member['symbol']]['eligibleParentQ'] != 'false'), None)
            if first:
                index, row = first
                if row['eligibleParentQ'] == 'unknown':
                    state, reason = 'memberUnknown', 'firstMemberUnknown'
                elif row['breakout']:
                    state, reason = 'confirmed', 'firstTrueWithBreakout'
                else:
                    state, reason = 'confirmedWithoutBreakout', 'firstTrueWithoutBreakout'
            elif observations and observations[-1][0] - result['formationIndex'] == 5:
                index, rows = observations[-1]
                row, state, reason = rows[member['symbol']], 'expired', 'observationWindowExpired'
            else:
                continue
            member.update(state=state, terminalDate=self.dates[index], terminalIndex=index,
                          terminalReason=reason, terminalParent=deepcopy(row))
        return result

    def step(self, day, index, parents, sources):
        if index != self.index + 1 or self.dates[index] != day:
            raise ValueError('Independent replay must consume consecutive dates')
        changes, raw, formations = [], [], []
        for entry in self.entries:
            base, observations = entry
            offset = index - base['formationIndex']
            if offset < 1 or offset > 5:
                continue
            before = self.snapshot(entry)
            involved = [base['anchorSymbol']] + [m['symbol'] for m in base['members']]
            observations.append((index, {symbol: deepcopy(parents[symbol]) for symbol in involved}))
            after = self.snapshot(entry)
            anchor = parents[base['anchorSymbol']]
            reason = 'continuousTrue' if after['anchorGate'] == 'alive' else (
                'firstAnchorTerminal' if before['anchorGate'] == 'alive' else 'terminalRetained')
            changes.append({'type': 'anchor', 'date': day, 'index': index, 'offset': offset,
                'episodeId': base['episodeId'], 'anchorSymbol': base['anchorSymbol'],
                'fromState': before['anchorGate'], 'toState': after['anchorGate'], 'reason': reason,
                'parent': deepcopy(anchor), 'anchorGateTerminal': deepcopy(after['anchorGateTerminal'])})
            for old, member in zip(before['members'], after['members']):
                parent = parents[member['symbol']]
                reason = 'terminalRetained' if old['state'] != 'observing' else (
                    'stillFalse' if member['state'] == 'observing' else member['terminalReason'])
                changes.append({'type': 'member', 'date': day, 'index': index, 'offset': offset,
                    'episodeId': base['episodeId'], 'anchorSymbol': base['anchorSymbol'],
                    'memberSymbol': member['symbol'], 'frozenMemberRank': member['frozenMemberRank'],
                    'fromState': old['state'], 'toState': member['state'], 'reason': reason,
                    'parent': deepcopy(parent), 'anchorGate': after['anchorGate']})
                if old['state'] == 'observing' and member['state'] == 'confirmed':
                    raw.append({'version': VERSION, 'date': day, 'episodeId': base['episodeId'],
                        'rawConfirmationId': ':'.join((base['episodeId'], member['symbol'], day)),
                        'anchorSymbol': base['anchorSymbol'], 'memberSymbol': member['symbol'],
                        **{key: deepcopy(base[key]) for key in ('formationDate', 'formationIndex', 'formationParentRank', 'peerFormationDate', 'groupSha256')},
                        'frozenMemberRank': member['frozenMemberRank'], 'anchorGate': after['anchorGate'],
                        'anchorGateTerminal': deepcopy(after['anchorGateTerminal']),
                        'memberInitialParent': deepcopy(member['initialParent']), 'memberParent': deepcopy(parent),
                        'anchorParent': deepcopy(anchor), 'offset': offset, 'certified': False, 'targetAchieved': False})
        for row in sources:
            anchor, group = row['symbol'], row['group']
            identity = {'date': day, 'index': index, 'anchorSymbol': anchor, 'sourceParentRank': row['rank'],
                        'peerFormationDate': row['formationDate'], 'groupSha256': row['groupSha256']}
            previous = next((entry[0] for entry in reversed(self.entries) if entry[0]['anchorSymbol'] == anchor), None)
            if previous and index <= previous['lockThroughIndex']:
                formations.append({**identity, 'kind': 'formationSuppressedByLock', 'episodeId': None,
                    'lockedByEpisodeId': previous['episodeId'], 'lockThroughIndex': previous['lockThroughIndex']})
                continue
            episode = {'episodeId': day + ':' + anchor, 'formationDate': day, 'formationIndex': index,
                'formationParentRank': row['rank'], 'anchorSymbol': anchor, 'peerFormationDate': row['formationDate'],
                'groupSha256': row['groupSha256'], 'formationKind': 'activated' if group['kind'] == 'formed' else 'unevaluableFormation',
                'sourceGroupKind': group['kind'], 'sourceGroupReason': group.get('reason'), 'lockThroughIndex': index + 5,
                'anchorGate': 'alive', 'anchorGateTerminal': None, 'anchorInitialParent': deepcopy(parents[anchor]), 'members': []}
            for rank, symbol in enumerate(group['members'], 1):
                parent = parents[symbol]
                if group['kind'] != 'formed':
                    state = 'formationUnevaluable'
                else:
                    state = {'false': 'observing', 'true': 'notInitiallyWeak', 'unknown': 'initialMemberUnknown'}[parent['eligibleParentQ']]
                pending = state == 'observing'
                episode['members'].append({'symbol': symbol, 'frozenMemberRank': rank, 'initialParent': deepcopy(parent),
                    'state': state, 'terminalDate': None if pending else day, 'terminalIndex': None if pending else index,
                    'terminalReason': None if pending else state, 'terminalParent': None if pending else deepcopy(parent)})
            self.entries.append((episode, []))
            formations.append({**identity, 'kind': episode['formationKind'], 'episodeId': episode['episodeId'],
                'lockThroughIndex': index + 5, 'episode': deepcopy(episode)})
        self.index = index
        states = [self.snapshot(entry) for entry in self.entries]
        latest = {episode['anchorSymbol']: episode for episode in states}
        counts = {'activatedEpisodes': len(states), 'lockedAnchors': sum(index <= e['lockThroughIndex'] for e in latest.values()),
            'anchorGateCounts': dict(Counter(e['anchorGate'] for e in states)),
            'memberStateCounts': dict(Counter(m['state'] for e in states for m in e['members']))}
        ordered, selected = reference_selection(day, raw)
        return {'formations': formations, 'transitions': changes, 'rawConfirmations': ordered,
                'selection': selected, 'signals': selected['signals'], 'episodeCounts': counts}

    def finish(self):
        episodes = [self.snapshot(entry) for entry in self.entries]
        transitions, day = [], self.dates[self.index]
        for episode in episodes:
            offset = self.index - episode['formationIndex']
            for member in episode['members']:
                if member['state'] != 'observing':
                    continue
                if offset >= 5:
                    raise ValueError('Unexpired independent episode past fixed observation window')
                member.update(state='censoredAtStudyCutoff', terminalDate=day, terminalIndex=self.index,
                              terminalReason='studyEndedBeforeFifthObservation', terminalParent=None)
                transitions.append({'type': 'member', 'date': day, 'index': self.index, 'offset': offset,
                    'episodeId': episode['episodeId'], 'anchorSymbol': episode['anchorSymbol'], 'memberSymbol': member['symbol'],
                    'frozenMemberRank': member['frozenMemberRank'], 'fromState': 'observing', 'toState': 'censoredAtStudyCutoff',
                    'reason': 'studyEndedBeforeFifthObservation', 'parent': None, 'anchorGate': episode['anchorGate']})
        return episodes, transitions


def bind_execution(output, root, binding, reference, audit):
    protocol = read(output / 'protocol.json')
    audit.pin(output / 'protocol.json')
    doc = binding['document']
    audit.require(protocol.get('schema') == 'leader-follower-confirmation-development-v1'
        and protocol.get('protocolHash') == digest({k: v for k, v in protocol.items() if k != 'protocolHash'})
        and reference.exact(protocol.get('registration'), doc) and protocol.get('registrationSha256') == REGISTRATION_SHA256
        and reference.exact(protocol.get('binding'), binding['identity']) and reference.exact(protocol.get('config'), doc['config'])
        and protocol.get('workspace') == str(binding['workspace']) and protocol.get('strategyOrder') == [CANDIDATE_ID]
        and protocol.get('groups') == list(GROUPS), 'new-execution-protocol-identity')
    expected = set(binding['parent']['protocol']['implementation']) | NEW_FILES | set(DEPENDENCIES)
    expected.add(REGISTRATION.resolve().relative_to(root).as_posix())
    audit.require(set(protocol['implementation']) == expected, 'new-exact-implementation-closure')
    for name, value in {**binding['parent']['protocol']['implementation'], **DEPENDENCIES}.items():
        audit.require(protocol['implementation'].get(name) == value, 'inherited-code-unchanged:' + name)
    audit.require(protocol['implementation']['scripts/verify-signal-leader-follower-confirmation-result.py'] == VERIFIER_SHA256,
                  'current-independent-auditor-archived')
    audit.pin(output / 'execution-sources.zip', protocol['executionArchiveSha256'])
    reference.archive(audit, output / 'execution-sources.zip', protocol['implementation'], root)
    audit.pin(output / 'freeze-attempt.json', protocol['freezeAttemptSha256'])
    freeze = read(output / 'freeze-attempt.json')
    audit.require(freeze.get('candidateId') == CANDIDATE_ID and freeze.get('registrationSha256') == REGISTRATION_SHA256,
                  'exclusive-freeze-marker')
    attempt = output / 'attempts/attempt-0001'
    audit.require({p.name for p in (output / 'attempts').iterdir()} == {'attempt-0001'}
        and {p.name for p in attempt.iterdir()} == set(ARTIFACTS.values()) | {'completion.json'}, 'single-complete-attempt')
    allowed = {'freeze-attempt.json', 'protocol.json', 'execution-sources.zip', 'events.jsonl', 'attempts'}
    for path in output.iterdir():
        report = path.is_file() and (path.name == 'independent-result-verification.json' or
                 path.name.startswith('independent-result-verification-failed-') and path.suffix == '.json')
        audit.require(path.name in allowed or report, 'complete-root-inventory')
    for path in (output, *output.iterdir(), attempt, *attempt.iterdir()):
        audit.require(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()), 'no-linked-output')
    completion = read(attempt / 'completion.json')
    audit.pin(attempt / 'completion.json')
    paths = {key: 'attempts/attempt-0001/' + name for key, name in ARTIFACTS.items()}
    paths.update(protocol='protocol.json', executionSources='execution-sources.zip')
    audit.require(set(completion['files']) == set(paths) and type(completion.get('attempt')) is int and completion['attempt'] == 1
        and completion.get('protocolHash') == protocol['protocolHash']
        and completion.get('snapshotFingerprint') == binding['identity']['snapshotFingerprint']
        and completion.get('freezeAttemptSha256') == protocol['freezeAttemptSha256'], 'strict-thirteen-artifact-receipt')
    for key, name in paths.items():
        audit.require(completion['files'][key] == {'path': name, 'sha256': audit.pin(output / name)}, 'output-artifact:' + key)
    marker = read(attempt / 'attempt.json')
    audit.require(marker.get('candidateId') == CANDIDATE_ID and type(marker.get('attempt')) is int and marker['attempt'] == 1
        and marker.get('protocolHash') == protocol['protocolHash'], 'single-attempt-marker')
    journal = [parse(line) for line in (output / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    audit.pin(output / 'events.jsonl')
    audit.require([r.get('event') for r in journal] == ['study_started', 'scoring_completed', 'study_succeeded']
        and all(row.get('protocolHash') == protocol['protocolHash'] for row in journal)
        and reference.exact(journal[-1].get('receipt'), completion), 'ordered-success-journal')
    for value in (doc, protocol, freeze, marker, completion):
        audit.require(value.get('certified') is False and value.get('targetAchieved') is False, 'diagnostic-only')
    for key in ('inheritedParentPredicatesRecomputed', 'inheritedPeerCorrelationsRecomputed'):
        audit.require(protocol.get(key) is False, 'inherited-proof-scope:' + key)
    binding.update(protocol=protocol, attempt=attempt, completion=completion, journal=journal)


def replay(binding, reference, audit):
    streams = {key: iter(records(binding['attempt'] / ARTIFACTS[key])) for key in
        ('formations', 'transitions', 'episodeTerminals', 'confirmations', 'selections', 'coverage', 'signals')}
    oracle = ReferenceEpisodes(binding['dates'])
    signals, slots = [], []
    counts = Counter({key: 0 for key in ('formations', 'transitions', 'confirmations',
                                        'sourcePredicateRows', 'sourceFormations', 'scoredSessions')})
    q_counts, kind_counts, formation_kinds, ownership_counts = Counter(), Counter(), Counter(), Counter()
    last_notice = time.monotonic()
    for day, index, parents, formations, source in inherited_days(binding, reference, audit):
        expected = oracle.step(day, index, parents, formations)
        formation_kinds.update(row['kind'] for row in expected['formations'])
        ownership_counts.update(rawConfirmations=expected['selection']['rawConfirmationCount'],
            uniqueMembers=expected['selection']['uniqueMemberCount'],
            duplicateOwnedConfirmations=sum(len(r['discardedRawConfirmationIds']) for r in expected['selection']['ownership']),
            membersWithMultipleSources=sum(len(r['allRawConfirmationIds']) > 1 for r in expected['selection']['ownership']))
        for key, field in (('formations', 'formations'), ('transitions', 'transitions'), ('confirmations', 'rawConfirmations')):
            for record in expected[field]:
                wrapper = {'date': day, 'source': source, 'record': record}
                audit.check(reference.exact(next(streams[key], None), wrapper), 'independent-' + key, date=day)
                counts[key] += 1
        audit.check(reference.exact(next(streams['selections'], None),
            {'date': day, 'source': source, 'selection': expected['selection']}), 'independent-ownership-three-policies', date=day)
        for row in expected['signals']:
            audit.check(reference.exact(next(streams['signals'], None), row), 'independent-policy-signal', date=day)
        q = Counter(row['eligibleParentQ'] for row in parents.values())
        kinds = Counter(row['kind'] for row in parents.values())
        daily = {'date': day, 'targetIndex': index, 'source': source, 'sourcePredicateRows': len(parents),
            'sourceFormations': len(formations), 'parentQCounts': {key: q[key] for key in ('true', 'false', 'unknown')},
            'parentKindCounts': dict(kinds), 'formations': len(expected['formations']), 'transitions': len(expected['transitions']),
            'rawConfirmations': len(expected['rawConfirmations']), 'selectedSignals': expected['selection']['policyCounts'],
            'episodeCounts': expected['episodeCounts'], 'certified': False, 'targetAchieved': False}
        audit.check(reference.exact(next(streams['coverage'], None), daily), 'independent-complete-daily-coverage', date=day)
        signals.extend(expected['signals'])
        slots.extend(expected['selection']['slots'])
        counts.update(sourcePredicateRows=len(parents), sourceFormations=len(formations), scoredSessions=1)
        q_counts.update(q)
        kind_counts.update(kinds)
        if time.monotonic() - last_notice >= 30:
            print(f'Independently checked {index + 1}/{len(binding["dates"])} inherited follower days', flush=True)
            last_notice = time.monotonic()
    episodes, censored = oracle.finish()
    for row in censored:
        expected = {'date': binding['dates'][-1], 'source': None, 'phase': 'cutoff', 'record': row}
        audit.check(reference.exact(next(streams['transitions'], None), expected), 'independent-cutoff-censor')
        counts['transitions'] += 1
    for episode in episodes:
        audit.check(reference.exact(next(streams['episodeTerminals'], None), episode), 'independent-all-episode-terminals')
    for key, stream in streams.items():
        audit.require(next(stream, None) is None, 'output-exact-EOF:' + key)
    episode_accounting = {'sourceFormations': counts['sourceFormations'], 'formationKinds': dict(formation_kinds),
        'episodes': len(episodes), 'anchorGateCounts': dict(Counter(e['anchorGate'] for e in episodes)),
        'memberStateCounts': dict(Counter(m['state'] for e in episodes for m in e['members'])),
        'ownership': {key: ownership_counts[key] for key in ('rawConfirmations', 'uniqueMembers',
                                                           'duplicateOwnedConfirmations', 'membersWithMultipleSources')}}
    performance = {**dict(counts), 'terminalEpisodes': len(episodes), 'episodeAccounting': episode_accounting,
        'parentQCounts': {key: q_counts[key] for key in ('true', 'false', 'unknown')}, 'parentKindCounts': dict(kind_counts),
        'selectedSignals': {sid: sum(row['strategyId'] == sid for row in signals) for sid in GROUPS},
        'inheritedParentPredicatesRecomputed': False, 'inheritedPeerCorrelationsRecomputed': False}
    return signals, slots, performance


def audit_slots(result, signals, slots, binding, reference, audit):
    ident = lambda row: (row['strategyId'], row['symbol'], row['signalDate'])
    events = {ident(row): row for row in result['events']}
    blocked = {ident(row): row for row in result['overlapSuppressed']}
    selected = {ident(row) for row in signals}
    used, rows = set(), []
    stream = iter(records(binding['attempt'] / ARTIFACTS['slotOutcomes']))
    def status(sid, slot):
        if sid == CANDIDATE_ID and not slot['candidateAdmitted'] or sid == ANCHOR_CONTROL_ID and not slot['anchorControlAdmitted']:
            if sid == ANCHOR_CONTROL_ID and slot['anchorControlDisposition'] == 'mappedAnchorCollision':
                return {'status': 'mappedAnchorCollision', 'eventId': None, 'entryStatus': None,
                        'retainedRank': slot['anchorControlRetainedRank']}
            return {'status': 'filteredAnchorUnknown' if slot['anchorGate'] == 'unknown' else 'filteredAnchorCancelled',
                    'eventId': None, 'entryStatus': None}
        symbol = slot['anchorSymbol'] if sid == ANCHOR_CONTROL_ID else slot['memberSymbol']
        key = (sid, symbol, slot['date'])
        audit.require(key in selected and key not in used, 'one-selected-signal-per-original-slot')
        used.add(key)
        if key in blocked:
            event = blocked[key]
            return {'status': 'activeSuppressed', 'eventId': None, 'entryStatus': None, 'reason': event['reason'],
                    'blockedByEventId': event['blockedByEventId'], 'activeUntil': event['activeUntil']}
        event = events[key]
        return {'status': 'attempted', 'eventId': event['eventId'], 'entryStatus': event['entry']['status'],
            'entryReason': event['entry'].get('reason'), 'entryDate': event['entry'].get('date'),
            'targetDate': event['outcomes']['nextOpenNet30']['hitDate'], 'terminal': deepcopy(event['terminal'])}
    for slot in slots:
        row = {**slot, 'candidateDisposition': status(CANDIDATE_ID, slot),
            'memberBaselineDisposition': status(MEMBER_CONTROL_ID, slot),
            'anchorControlDispositionDetails': status(ANCHOR_CONTROL_ID, slot),
            'interpretation': 'original-global-member-Top5;three-independent-active-policies;not-randomized-or-fill-matched'}
        audit.check(reference.exact(next(stream, None), row), 'independent-three-policy-slot-dispositions')
        rows.append(row)
    audit.require(used == selected and next(stream, None) is None, 'complete-slot-signal-and-EOF')
    accounting = {'globalTop5Slots': len(slots), 'candidateAdmitted': sum(s['candidateAdmitted'] for s in slots),
        'memberBaselineAdmitted': len(slots), 'anchorControlAdmitted': sum(s['anchorControlAdmitted'] for s in slots),
        'mappedAnchorCollisions': sum(s['anchorControlDisposition'] == 'mappedAnchorCollision' for s in slots)}
    for name, field in (('candidate', 'candidateDisposition'), ('memberBaseline', 'memberBaselineDisposition'),
                        ('anchorControl', 'anchorControlDispositionDetails')):
        values = [row[field] for row in rows]
        accounting[name + 'Dispositions'] = dict(Counter(v['status'] for v in values))
        accounting[name + 'EntryStatuses'] = dict(Counter(v['entryStatus'] for v in values if v['entryStatus'] is not None))
    audit.check(reference.exact(result.get('slotAccounting'), accounting), 'independent-slot-accounting')
    return accounting


def verify(output: Path, root: Path = ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    started, audit = time.monotonic(), None
    report = {'schema': 'leader-follower-confirmation-independent-verification-v1',
        'checkedAt': datetime.now(timezone.utc).isoformat(), 'passed': False, 'verificationCompleted': False,
        'candidateId': CANDIDATE_ID, 'registrationSha256': REGISTRATION_SHA256, 'verifierSha256': VERIFIER_SHA256,
        'certified': False, 'targetAchieved': False, 'actualOpeningExecutionVerified': False,
        'inheritedParentPredicatesRecomputed': False, 'inheritedPeerCorrelationsRecomputed': False,
        'scope': 'Inherited hash-bound parent population and grouping proof; independent eligibleParentQ, A-based fixed clocks, first terminal state, ownership, global Top5, three policy masks, slots and registered-cost event/path arithmetic.',
        'limitations': ['Parent cross-sectional features, parent predicate mathematics and correlations are inherited from the separately pinned successful parent audit, not recalculated here.',
            'Recorded entry/exit arithmetic and lifecycle do not prove first executable opening, actual auction capacity, full historical cost schedule, comparable rights or a broker tariff.',
            'Three policy groups and repeated/group-overlapping members are dependent descriptive observations, not randomized causal evidence.']}
    try:
        reference, peer, source, adaptation = load_references(root)
        audit = reference.Audit()
        audit.pin(VERIFIER_PATH, VERIFIER_SHA256)
        binding = bind_parent(reference, peer, root, audit)
        bind_execution(output, root, binding, reference, audit)
        data = source.load_source(reference, binding['parent'], root, audit)
        original_digest = digest(data)
        signals, slots, performance = replay(binding, reference, audit)
        with gzip.open(binding['attempt'] / ARTIFACTS['result'], 'rt', encoding='utf-8') as stream:
            result = parse(stream.read())
        summary = read(binding['attempt'] / ARTIFACTS['summary'])
        audit.require(result.get('protocolHash') == binding['protocol']['protocolHash']
            and result.get('snapshotFingerprint') == data['fingerprint'] and result.get('strategyOrder') == [CANDIDATE_ID]
            and result.get('diagnosticUnverifiedHistory') is True and result.get('pilot') is False,
            'result-registered-identity')
        for flag in ('certified', 'targetAchieved', 'inheritedParentPredicatesRecomputed', 'inheritedPeerCorrelationsRecomputed'):
            audit.require(result.get(flag) is False and summary.get(flag) is False, 'result-scope:' + flag)
        expected_summary = {k: v for k, v in result.items() if k not in ('events', 'overlapSuppressed', 'pathRiskEvents')}
        audit.check(reference.exact({k: v for k, v in summary.items() if k not in ('performance', 'ranking', 'selectionEligible')},
                                   expected_summary), 'summary-exact-result-projection')
        audit.check(summary.get('ranking') is None and summary.get('selectionEligible') is False, 'no-performance-selection')
        audit.require(set(summary['performance']) == set(performance) | {'scoringSeconds', 'eventEvaluationSeconds'}, 'exact-performance-fields')
        for key, value in performance.items():
            audit.check(reference.exact(summary['performance'].get(key), value), 'independent-performance:' + key)
            audit.check(reference.exact(binding['journal'][1].get(key), value), 'independent-journal:' + key)
        audit.check(reference.exact(result.get('episodeAccounting'), performance['episodeAccounting']), 'independent-episode-accounting')
        for key in ('scoringSeconds', 'eventEvaluationSeconds'):
            value = summary['performance'][key]
            audit.check(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'valid-duration:' + key)
        ids = [group.get('strategyId') for group in result['groups']]
        audit.require(len(ids) == 3 and set(ids) == set(GROUPS), 'exact-three-result-groups')
        adapted = {'originalRawSourcePanelBinding': {'targetDates': binding['dates']},
                   'frozenExecutionConfig': binding['document']['config']}
        report['eventsChecked'] = reference.event_audit(audit, result, signals, data, adapted, root)
        report['slotAccounting'] = audit_slots(result, signals, slots, binding, reference, audit)
        report.update(protocolHash=binding['protocol']['protocolHash'], snapshotFingerprint=data['fingerprint'],
            targetSessions=len(binding['dates']), evaluatedSecuritySessions=performance['sourcePredicateRows'],
            sourceFormations=performance['sourceFormations'], terminalEpisodes=performance['terminalEpisodes'],
            parentQCounts=performance['parentQCounts'], selectedSignals=performance['selectedSignals'],
            episodeAccounting=performance['episodeAccounting'],
            parentAuditSha256=binding['identity']['parentAuditSha256'],
            completionSha256=sha(binding['attempt'] / 'completion.json'), resultSha256=sha(binding['attempt'] / ARTIFACTS['result']),
            implementation=binding['protocol']['implementation'], eventAuditAdaptation={
                'sourcePath': LEADER_REFERENCE, 'sourceSha256': DEPENDENCIES[LEADER_REFERENCE],
                'scope': 'Only fixed group set and count assertions adapted from two to three policy IDs; event, active, cost, cohort and path formula AST is unchanged.', **adaptation})
        audit.require(digest(data) == original_digest, 'source-object-unmodified')
        for path, expected in audit.bound.items():
            audit.require(sha(path) == expected, 'bound-file-unchanged:' + path.name)
        report['verificationCompleted'] = True
        report['passed'] = audit.failure_count == 0
    except Exception as error:
        report.update(errorType=type(error).__name__, error=str(error))
    report.update(checks=audit.checks if audit else 0, failureCount=audit.failure_count if audit else 1,
                  issues=audit.issues if audit else [], elapsedSeconds=time.monotonic() - started)
    if not report['verificationCompleted'] and report['failureCount'] == 0:
        report['failureCount'] = 1
    return report


def save_report(output, report):
    name = 'independent-result-verification.json' if report['passed'] else (
        'independent-result-verification-failed-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
    path = Path(output) / name
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/strategy-search/signal-leader-follower-confirmation-v1')
    args = parser.parse_args(argv)
    report = verify(args.output)
    path = save_report(args.output, report)
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'failureCount': report['failureCount'], 'report': str(path)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
