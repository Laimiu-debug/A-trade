"""Synthetic fixed-method repair replication; never reads research outcomes.

The parent really runs on temporary raw bytes and is independently replayed.
The market observations are explicit hand-authored gate input contracts with
synthetic audit receipts, not assertions about an actual market-state panel.
"""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import test_signal_supplemental_persistent_leader as parent_fixture


REPO = Path(__file__).resolve().parents[1]
SOURCE = 'scripts/run-signal-supplemental-market-repair.py'
VERIFIER = 'scripts/verify-signal-supplemental-market-repair-result.py'
REGISTRATION = 'docs/signal-supplemental-market-repair-v1.json'
module, sha, digest = parent_fixture.module, parent_fixture.sha, parent_fixture.digest
read, write_json, write_lines, lines = parent_fixture.read, parent_fixture.write_json, parent_fixture.write_lines, parent_fixture.lines


def new_parent_fixture():
    class ModifiedSource(parent_fixture.SupplementalPersistentLeaderTests):
        def build_source_contract(self):
            # Extend synthetic breakouts beyond ten target sessions, before
            # any source hash, feature calculation or outcome is frozen.
            for path in (self.source / 'tdx').rglob('*.day'):
                if path.stem == 'sh000300':
                    continue
                records = list(struct.iter_unpack('<IIIIIfII', path.read_bytes()))
                for index in range(140, len(records)):
                    price = 1700 + (index - 140) * 5
                    records[index] = (records[index][0], price, price, price, price, 100_000_000., 10_000_000, 0)
                path.write_bytes(b''.join(struct.pack('<IIIIIfII', *record) for record in records))
            super().build_source_contract()
    return ModifiedSource()


class SupplementalMarketRepairTests(unittest.TestCase):
    """Public nonempty fixture also used by the independent verifier."""

    def setUp(self):
        self.runner = module('supplemental_repair_test_target', SOURCE)
        self.parent = new_parent_fixture()
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.root, self.workspace, self.data = self.parent.root, self.parent.workspace, self.parent.data
        self.symbols, self.target_dates = self.parent.symbols, self.parent.dates[130:170]
        self.output = self.root / 'supplemental-repair-output'
        self.registration = self.root / REGISTRATION
        self.market = self.root / 'synthetic-supplemental-market'
        self.market.mkdir()
        self.parent.complete()
        verifier = module('supplemental_repair_parent_reference', parent_fixture.VERIFIER)
        with patch.object(verifier, 'REGISTRATION', self.parent.registration), \
                patch.object(verifier, 'REGISTRATION_SHA256', self.parent.registration_hash):
            report = verifier.verify(self.parent.output, self.parent.panel, root=self.root)
        self.assertTrue(report['passed'], report.get('issues'))
        verifier.save_report(self.parent.output, report)
        self.parent_protocol = read(self.parent.output / 'protocol.json')
        self.parent_receipt = read(self.parent.attempt() / 'completion.json')
        self.parent_audit = report
        if (REPO / REGISTRATION).exists():
            self.doc = read(REPO / REGISTRATION)
        else:
            # Before the real parent's audit finishes, construct only this
            # temporary registration using the documented generator schema.
            self.doc = read(REPO / 'docs/signal-market-repair-resilience-price-proxy-v1.json')
            self.doc.update(status='rules-frozen-before-first-supplemental-repair-outcomes',
                stage='qualified-supplemental-historical-replication;not-untouched-holdout',
                registeredAt='synthetic-test-only', defaultOutputDirectory='synthetic-repair-output')
            for key, name in (
                ('frozenRunner', 'scripts/run-signal-market-repair.py'),
                ('frozenRegistration', 'docs/signal-market-repair-resilience-price-proxy-v1.json'),
                ('frozenIndependentVerifier', 'scripts/verify-signal-market-repair-result.py'),
                ('registrationGenerator', 'artifacts/strategy-search/register_supplemental_market_repair.py')):
                self.doc[key] = {'path': name, 'sha256': sha(REPO / name)}
            self.doc['frozenMethodImplementation'] = {**self.parent.document['frozenMethodImplementation'],
                **{name: sha(REPO / name) for name in ('scripts/run-signal-market-repair.py',
                    'service/signal_market_repair.py', 'docs/signal-market-repair-resilience-price-proxy-v1.json')}}
            self.doc['runPolicy'] = {'freshOwnActiveGroupsAtStart': True, 'carryAcrossYearBoundary': True,
                'targetOnlyGateHistory': True, 'firstTenTargetSessionsUnknown': True, 'automaticRetry': False,
                'completeSourceAndExecutableBeforeFirstGate': True, 'completedOnlyVerifies': True,
                'exclusiveFreeze': True}
            self.doc['implementationRequirements']['runner'] = SOURCE
        for key in ('fixedPopulation', 'sourceMaterialization', 'sourcePanelRegistration', 'sourcePanelVerifier',
                    'sourcePanelClosureAudit', 'exposure', 'downstreamPolicy', 'sample', 'eventPolicy', 'auditAndEvaluation'):
            self.doc[key] = deepcopy(self.parent.document[key])
        self.doc['frozenExecutionConfig'] = deepcopy(self.parent_protocol['config'])
        self.doc['executionConfigHash'] = digest(self.doc['frozenExecutionConfig'])
        self.doc['unchangedExecutionAndSample'].update(fixedSymbols=9, fixedRawBars=1530, targetSessions=40,
            signalStart=self.target_dates[0], signalEnd=self.target_dates[-1], snapshotFingerprint=self.data['fingerprint'])
        refs = [self.doc['designSource'], self.doc['registrationGenerator'], self.doc['frozenRunner'],
                self.doc['frozenRegistration'], self.doc['frozenIndependentVerifier'],
                *self.doc['sourceIntegrityPins'].values()]
        names = set(self.doc['frozenMethodImplementation']) | {SOURCE, VERIFIER,
            'scripts/run-signal-supplemental-persistent-leader.py', 'scripts/build-signal-supplemental-market-state-panel.py',
            'scripts/verify-signal-supplemental-market-state-panel.py', 'service/signal_market_state.py',
            'service/signal_market_repair.py', 'tests/test_signal_supplemental_market_repair.py'}
        names.update(record['path'] for record in refs)
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                if name.startswith('artifacts/') and 'register_' not in name:
                    write_json(destination, {'synthetic': True, 'provenanceOnly': name})
                else:
                    destination.write_bytes((REPO / name).read_bytes())
        for record in refs:
            record['sha256'] = sha(self.root / record['path'])
        self.doc['frozenMethodImplementation'] = {name: sha(self.root / name) for name in self.doc['frozenMethodImplementation']}
        self.doc['parent'].update(registration=self.parent.registration.relative_to(self.root).as_posix(),
            registrationSha256=self.parent.registration_hash,
            completedArtifactDirectory=self.parent.output.relative_to(self.root).as_posix(),
            protocolHash=digest(self.parent_protocol), sourceImplementation=deepcopy(self.parent_protocol['implementation']),
            actualAuditVerifier=self.ref(self.root / parent_fixture.VERIFIER))
        self.doc['originalRawSourcePanelBinding'] = {'identity': deepcopy(self.parent_receipt['panelBinding']),
            'files': {path.name: self.ref(path) for path in self.parent.panel.iterdir() if path.is_file()},
            'snapshot': self.ref(self.parent.output / 'snapshot.json'),
            'sourceSelection': self.ref(self.parent.output / 'selection.json'),
            'calendarHash': self.parent.manifest['inputs']['calendar']['sha256'],
            'symbolsSha256': digest(self.symbols), 'targetDates': self.target_dates}
        self.build_market()
        self.rebind()

    def ref(self, path):
        return {'path': Path(path).relative_to(self.root).as_posix(), 'sha256': sha(path)}

    def build_market(self):
        self.market_registration = self.root / 'docs/synthetic-supplemental-market.json'
        self.market_doc = {'featureVersion': 'market-state-observations-v1', 'sourceFeatureVersion': 'daily-leadership-raw-v1',
            'fixedPopulation': deepcopy(self.doc['fixedPopulation']), 'synthetic': True,
            'sample': {'start': self.target_dates[0], 'end': self.target_dates[-1], 'symbols': 9, 'sessions': 40,
                'rows': 360, 'snapshotFingerprint': self.data['fingerprint']},
            **{key: deepcopy(self.doc[key]) for key in ('sourcePanelRegistration', 'sourcePanelVerifier', 'sourcePanelClosureAudit')}}
        write_json(self.market_registration, self.market_doc)
        implementation = {name: sha(self.root / name) for name in (
            'scripts/build-signal-supplemental-market-state-panel.py', 'scripts/verify-signal-supplemental-market-state-panel.py',
            'service/signal_market_state.py', self.market_registration.relative_to(self.root).as_posix())}
        self.parent.archive(self.market / 'execution-sources.zip', implementation)
        raw_pins = {path.name: sha(path) for path in self.parent.panel.iterdir() if path.is_file()}
        self.market_protocol = {'schema': 'market-state-observation-protocol-v1', 'registration': self.market_doc,
            'registrationSha256': sha(self.market_registration), 'implementation': implementation,
            'inputManifestSha256': self.parent.manifest_hash, 'inputFilesSha256': raw_pins,
            'calendarHash': self.doc['originalRawSourcePanelBinding']['calendarHash'],
            'symbols': self.symbols, 'dates': self.target_dates, 'newStrategyTrials': 0, 'returnsCalculated': False,
            'certified': False, 'targetAchieved': False, 'executionArchiveSha256': sha(self.market / 'execution-sources.zip')}
        self.market_protocol['protocolHash'] = digest(self.market_protocol)
        write_json(self.market / 'protocol.json', self.market_protocol)
        write_json(self.market / 'attempt.json', {'protocolHash': self.market_protocol['protocolHash'], 'synthetic': True})
        write_json(self.market / 'summary.json', {'protocolHash': self.market_protocol['protocolHash'], 'dailyRows': 40,
            'roleRows': 360, 'synthetic': True, 'certified': False, 'targetAchieved': False})
        self.market_rows = []
        self.role_rows = []
        top = {'sh600006', 'sh600007', 'sh600005', 'sh600004', 'sh600003'}
        source_rows = {(row['asOfDate'], row['symbol']): row for row in self.parent.rows}
        for index, day in enumerate(self.target_dates):
            shared = {'date': day, 'featureVersion': 'market-state-observations-v1',
                'inputManifestSha256': self.parent.manifest_hash,
                'inputFeatureFileSha256': raw_pins['features.jsonl.gz'],
                'calendarHash': self.market_protocol['calendarHash'], 'protocolHash': self.market_protocol['protocolHash']}
            self.market_rows.append({**shared, 'fixedPopulation': 9, 'price20': {'coverage': 1., 'medianAbsoluteReturn20': -.2},
                'matched5': {'available': index >= 5, 'earlierDate': self.target_dates[index - 5] if index >= 5 else None,
                    'pairedCoverageFixed': 1. if index >= 5 else None,
                    'matchedBreadthChange5': None if index < 5 else (10. if index == 10 else -10.)}})
            for symbol in self.symbols:
                state = 'true' if symbol in top else 'false'
                if index == 9 and symbol == 'sh600007':
                    state = 'false'
                if index == 9 and symbol == 'sh600005':
                    state = 'unknown'
                self.role_rows.append({**shared, 'symbol': symbol,
                    'qDefinition': 'persistent-price-strength-five-gates-v1', 'qState': state,
                    'unknownReasons': ['syntheticUnknown'] if state == 'unknown' else [],
                    'failedPredicates': ['syntheticObservedFailure'] if state == 'false' else [],
                    'criterionValues': {'synthetic': True}, 'sampleManifestHash': self.parent.manifest_hash,
                    'sourceHashes': deepcopy(source_rows[day, symbol]['sourceHashes']),
                    'baseEligibility': deepcopy(source_rows[day, symbol]['baseEligibility']),
                    'certified': False, 'targetAchieved': False, 'actionEvidence': 'unverified'})
        self.doc['marketInput'].update(directory=self.market.relative_to(self.root).as_posix(),
            protocolHash=self.market_protocol['protocolHash'], registration=self.ref(self.market_registration),
            verifier=self.ref(self.root / 'scripts/verify-signal-supplemental-market-state-panel.py'), implementation=implementation)

    def rebind(self):
        """Rebind intentionally changed synthetic contracts before freezing."""
        self.doc['parent']['completedInputs'] = {name: self.ref(self.parent.output / name) for name in
            ('protocol.json', 'snapshot.json', 'selection.json', 'independent-result-verification.json')}
        self.doc['parent']['completedInputs']['completion.json'] = self.ref(self.parent.attempt() / 'completion.json')
        self.doc['parent']['completedOutputs'] = {key: self.ref(self.parent.output / record['path'])
            for key, record in self.parent_receipt['files'].items()}
        self.doc['parent']['additionalInputs'] = {key: self.ref(self.parent.output / name) for key, name in {
            'rootExecutionSources': 'execution-sources.zip', 'freezeAttempt': 'freeze-attempt.json',
            'events': 'events.jsonl', 'attempt': 'attempts/attempt-0001/attempt.json'}.items()}
        write_lines(self.market / 'daily.jsonl.gz', self.market_rows)
        write_lines(self.market / 'roles.jsonl.gz', self.role_rows)
        names = {'attempt.json', 'daily.jsonl.gz', 'execution-sources.zip', 'protocol.json', 'roles.jsonl.gz', 'summary.json'}
        completion = {'schema': 'market-state-observation-completion-v1', 'protocolHash': self.market_protocol['protocolHash'],
            'dailyRows': 40, 'roleRows': 360, 'newStrategyTrials': 0, 'returnsCalculated': False,
            'certified': False, 'targetAchieved': False,
            'files': {name: {'path': name, 'sha256': sha(self.market / name)} for name in names}}
        write_json(self.market / 'completion.json', completion)
        self.market_audit = {'schema': 'supplemental-market-state-independent-verification-v1',
            'implementation': deepcopy(self.market_protocol['implementation']),
            'sourcePanelClosureAudit': deepcopy(self.doc['sourcePanelClosureAudit']),
            'passed': True, 'failureCount': 0, 'protocolHash': self.market_protocol['protocolHash'],
            'dailyRows': 40, 'roleRows': 360, 'inputManifestSha256': self.parent.manifest_hash,
            'verifierSha256': self.doc['marketInput']['verifier']['sha256'], 'synthetic': True,
            'resultFileHashes': {name: sha(self.market / name) for name in names | {'completion.json'}},
            'returnsCalculated': False, 'newStrategyTrials': 0, 'certified': False, 'targetAchieved': False}
        write_json(self.market / 'independent-verification.json', self.market_audit)
        self.doc['marketInput'].update(outputs={name: self.ref(self.market / name) for name in names},
            completion=self.ref(self.market / 'completion.json'), actualAudit=self.ref(self.market / 'independent-verification.json'))
        write_json(self.registration, self.doc)
        self.registration_hash = sha(self.registration)

    @contextmanager
    def context(self):
        with patch.object(self.runner, 'ROOT', self.root), patch.object(self.runner, 'REGISTRATION', self.registration), \
                patch.object(self.runner, 'REGISTRATION_SHA256', self.registration_hash), \
                patch.object(self.runner, 'SOURCE_WORKSPACE', self.workspace), \
                patch.object(self.runner, '__file__', str(self.root / SOURCE)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield

    def args(self):
        return Namespace(workspace=self.workspace, output=self.output, diagnostic_unverified_history=True)

    def freeze(self):
        with self.context():
            return self.runner.freeze(self.args())

    def execute(self):
        with self.context():
            return self.runner.execute(self.args())

    def run_complete(self):
        self.freeze()
        return self.execute()

    def attempt(self):
        return self.output / 'attempts/attempt-0001'

    def collect(self, destination=None):
        destination = destination or self.root / 'collected'
        destination.mkdir()
        with self.context():
            return self.runner.collect_signals(self.runner.load_binding(), destination)

    def test_binding_freezes_without_evaluating_streams_and_rejects_source_audit_drift(self):
        with self.context():
            binding = self.runner.load_binding()
        self.assertEqual(binding['dates'], self.target_dates)
        self.assertEqual(binding['symbols'], self.symbols)
        with patch.object(self.runner, 'iter_market_days', side_effect=AssertionError('no market decisions at freeze')), \
                patch.object(self.runner, 'iter_role_days', side_effect=AssertionError('no roles at freeze')), \
                patch.object(self.runner, 'iter_parent_days', side_effect=AssertionError('no parent signals at freeze')):
            self.freeze()
        self.assertFalse((self.output / 'attempts').exists())
        with self.assertRaises((ValueError, FileExistsError)):
            self.freeze()
        original = self.registration.read_bytes()
        self.registration.write_bytes(original + b' ')
        with self.context(), self.assertRaises(ValueError):
            self.runner.load_binding()
        self.registration.write_bytes(original)
        path = self.parent.source / 'tdx/vipdoc/sh/lday/sh600001.day'
        path.write_bytes(path.read_bytes() + b'changed')
        with self.context(), self.assertRaises(ValueError):
            self.runner.load_binding()

    def test_first_ten_unknown_complete_logs_and_original_rank_control_mirroring(self):
        signals, performance = self.collect()
        destination = self.root / 'collected'
        self.assertTrue(signals)
        gates = lines(destination / 'market-gates.jsonl.gz')
        self.assertEqual([r['date'] for r in gates], self.target_dates)
        self.assertEqual([r['marketGate'] for r in gates], ['unknown'] * 10 + ['true'] + ['false'] * 29)
        self.assertEqual(performance['marketGateCounts'], {'true': 1, 'false': 29, 'unknown': 10})
        strategy = [s for s in signals if s['strategyId'] == self.doc['candidateId']]
        controls = [s for s in signals if s['strategyId'].startswith('control_')]
        self.assertEqual([s['rank'] for s in strategy], [1, 4, 5])
        self.assertEqual([s['rank'] for s in controls], [1, 4])
        self.assertEqual({s['signalDate'] for s in signals}, {self.target_dates[10]})
        self.assertTrue(all(s['selectedTop5'] is False for s in controls))
        self.assertTrue(all(next(r for r in self.role_rows if r['date'] == self.target_dates[9]
            and r['symbol'] == s['symbol'])['qState'] == 'false' for s in controls))
        decisions = lines(destination / 'filter-decisions.jsonl.gz')
        self.assertEqual([r['parentSignal'] for r in decisions], lines(self.parent.attempt() / 'signals.jsonl.gz'))
        keyed = {tuple(r['sourceSignalKey']): r for r in decisions}
        for row in decisions:
            pair = keyed[tuple(row['pairedParentSignalKey'])]
            self.assertEqual(row['pairedParentRank'], row['parentSignal']['rank'])
            self.assertEqual(row['strategyGate'], pair['strategyGate'])
            self.assertEqual(row['admitted'], pair['admitted'])
            self.assertEqual(row['strategyGateSha256'], digest(row['strategyGate']))
        for filename in ('selection.jsonl.gz', 'candidates.jsonl.gz'):
            original, emitted = lines(self.parent.attempt() / filename), lines(destination / filename)
            self.assertEqual(len(original), len(emitted))
            self.assertEqual(original, [{key: row[key] for key in source} for source, row in zip(original, emitted)])
        self.assertEqual(performance['sourceRoleRows'], 360)
        self.assertEqual(performance['unmatchedTrueStrategySlots'], 1)
        for counts in performance['filterAccounting'].values():
            self.assertEqual(counts['parentSignals'], sum(counts[key] for key in
                ('gateTrueAdmitted', 'gateFalseFiltered', 'gateUnknownFiltered')))

    def test_role_population_and_exact_t_minus_five_fail_closed_before_outcomes(self):
        original = deepcopy(self.role_rows)
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[-1])),
                       lambda rows: rows.__setitem__(1, deepcopy(rows[0])),
                       lambda rows: rows[0].update(qState=True), lambda rows: rows[0].update(protocolHash='0' * 64)):
            self.role_rows = deepcopy(original)
            change(self.role_rows)
            self.rebind()
            with self.context(), self.assertRaises((ValueError, EOFError)):
                list(self.runner.iter_role_days(self.runner.load_binding()))
        self.role_rows = original
        self.market_rows[5]['matched5']['earlierDate'] = self.target_dates[1]
        self.rebind()
        with self.context(), self.assertRaises(ValueError):
            list(self.runner.iter_market_days(self.runner.load_binding()))
        self.market_rows[5]['matched5']['earlierDate'] = self.target_dates[0]
        self.role_rows.append(deepcopy(self.role_rows[-1]))
        self.rebind()
        self.freeze()
        with patch.object(self.runner, 'evaluate_signals', side_effect=AssertionError('reject trailing role before outcomes')), \
                self.assertRaises((ValueError, EOFError)):
            self.execute()
        self.assertFalse((self.attempt() / 'completion.json').exists())

    def test_fresh_group_active_state_crosses_year_without_backfill_and_unfilled_does_not_hold(self):
        signals, _ = self.collect()
        chosen = next(s for s in signals if s['strategyId'] == self.doc['candidateId'])
        # Deliberately replay the admitted shape at earlier synthetic closes to
        # isolate unchanged lifecycle behavior across the year boundary.
        first = {**chosen, 'signalDate': self.parent.dates[130]}
        config = self.doc['frozenExecutionConfig']
        initial = self.runner.evaluate_signals(self.data, config, [first])['events'][0]
        hit = initial['outcomes']['nextOpenNet30']['hitDate']
        self.assertEqual(first['signalDate'][:4], '2024')
        waiting = {**first, 'signalDate': self.parent.dates[132]}
        control = {**waiting, 'strategyId': 'control_' + self.doc['candidateId']}
        result = self.runner.evaluate_signals(self.data, config, [first, waiting, control, {**first, 'signalDate': hit}])
        self.assertEqual(result['overlapSuppressedCount'], 1)
        self.assertEqual(result['overlapSuppressed'][0]['signalDate'][:4], '2025')
        self.assertEqual([(e['strategyId'], e['signalDate']) for e in result['events']],
            [(first['strategyId'], first['signalDate']), (control['strategyId'], control['signalDate']), (first['strategyId'], hit)])
        fresh = self.runner.evaluate_signals(self.data, config, [waiting])
        self.assertEqual((len(fresh['events']), fresh['overlapSuppressedCount']), (1, 0))
        data = deepcopy(self.data)
        next(b for s in data['securities'] if s['symbol'] == first['symbol'] for b in s['bars']
            if b['date'] == self.parent.dates[131]).update(open=14., high=14., low=14., close=14.)
        unfilled = self.runner.evaluate_signals(data, config, [first, {**first, 'signalDate': self.parent.dates[131]}])
        self.assertEqual([e['entry']['status'] for e in unfilled['events']], ['unfilled', 'filled'])
        self.assertEqual(unfilled['overlapSuppressedCount'], 0)
        self.assertEqual(unfilled['events'][0]['entry']['retryPolicy'], 'noDeferredEntry')
        self.assertEqual({s['rank'] for s in signals if s['strategyId'] == self.doc['candidateId']}, {1, 4, 5})

    def test_complete_is_read_only_verify_only_and_preserves_all_receipts(self):
        before = {path: sha(path) for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(self.run_complete(), 0)
        self.assertEqual(before, {path: sha(path) for path in before})
        with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('no repeated filtering')), \
                patch.object(self.runner, 'evaluate_signals', side_effect=AssertionError('no repeated outcomes')):
            self.assertEqual(self.execute(), 0)
        receipt = read(self.attempt() / 'completion.json')
        self.assertEqual(set(receipt['files']), self.runner.RECEIPT_KEYS)
        for item in receipt['files'].values():
            self.assertEqual(sha(self.output / item['path']), item['sha256'])
        result = parent_fixture.read_gzip(self.attempt() / 'result.json.gz')
        self.assertGreater(result['eventCount'], 0)
        self.assertFalse(result['targetAchieved'])
        for counts in result['filterAccounting'].values():
            self.assertEqual(counts['gateTrueAdmitted'], counts['newAttemptedEvents'] + counts['newActiveSuppressed'])
        (self.attempt() / 'filter-decisions.jsonl.gz').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.execute()

    def test_missing_freeze_and_failed_attempt_never_restart(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.execute()
        self.freeze()
        with patch.object(self.runner, 'evaluate_signals', side_effect=RuntimeError('synthetic terminal failure')) as evaluate:
            with self.assertRaisesRegex(RuntimeError, 'synthetic terminal failure'):
                self.execute()
            self.assertEqual(evaluate.call_count, 1)
        saved = {path: sha(path) for path in self.output.rglob('*') if path.is_file()}
        with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('no automatic retry')), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(saved, {path: sha(path) for path in saved})
        self.assertEqual([path.name for path in (self.output / 'attempts').iterdir()], ['attempt-0001'])

    def test_zero_admission_keeps_full_parent_and_roles_accounting(self):
        self.market_rows[10]['matched5']['matchedBreadthChange5'] = 0.
        self.rebind()
        self.assertEqual(self.run_complete(), 0)
        self.assertEqual(lines(self.attempt() / 'signals.jsonl.gz'), [])
        self.assertEqual(len(lines(self.attempt() / 'filter-decisions.jsonl.gz')),
                         len(lines(self.parent.attempt() / 'signals.jsonl.gz')))
        summary = read(self.attempt() / 'summary.json')
        self.assertEqual(summary['sourceRoleRows'], 360)
        self.assertEqual(summary['eventCount'], 0)
        self.assertEqual(len(summary['groups']), 2)

    def test_independent_nonempty_replay_and_rehashed_control_pair_tamper(self):
        self.run_complete()
        verifier = module('supplemental_repair_reference', VERIFIER)
        with patch.object(verifier, 'REGISTRATION', self.registration), \
                patch.object(verifier, 'REGISTRATION_SHA256', self.registration_hash):
            report = verifier.verify(self.output, root=self.root)
            self.assertTrue(report['passed'], report.get('issues', report.get('failures')))
            verifier.save_report(self.output, report)
            with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('audited run stays complete')):
                self.assertEqual(self.execute(), 0)
            path = self.attempt() / 'filter-decisions.jsonl.gz'
            decisions = lines(path)
            control = next(row for row in decisions if row['parentSignal']['strategyId'].startswith('control_') and row['admitted'])
            control['pairedParentRank'] = 99
            write_lines(path, decisions)
            receipt = read(self.attempt() / 'completion.json')
            receipt['files']['filterDecisions']['sha256'] = sha(path)
            write_json(self.attempt() / 'completion.json', receipt)
            journal_path = self.output / 'events.jsonl'
            journal = [json.loads(line) for line in journal_path.read_text(encoding='utf-8').splitlines()]
            next(row for row in journal if row['event'] == 'study_succeeded')['receipt'] = receipt
            journal_path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in journal), encoding='utf-8')
            failed = verifier.verify(self.output, root=self.root)
            self.assertFalse(failed['passed'])
            self.assertGreater(failed['failureCount'], 0)
            self.assertTrue(any(issue['check'] == 'independent-filterDecisions-row' for issue in failed['issues']), failed['issues'])


if __name__ == '__main__':
    unittest.main()
