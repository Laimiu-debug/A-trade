"""Synthetic supplementary-period replication; no real market files are read.

The feature audit is a deliberately synthetic input contract. Raw price
features are calculated by the existing fixture, while archived ranks are
hand-authored to exercise ordering and lifecycle, not to certify performance.
"""
from argparse import Namespace
from contextlib import closing, contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import date, timedelta
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import struct
import unittest
from unittest.mock import patch
import zipfile

import test_signal_persistent_leader_runner as old


REPO = Path(__file__).resolve().parents[1]
SOURCE = 'scripts/run-signal-supplemental-persistent-leader.py'
VERIFIER = 'scripts/verify-signal-supplemental-persistent-leader-result.py'
REGISTRATION = 'docs/signal-supplemental-persistent-leader-v1.json'
sha, digest, write_json, write_lines, lines = old.file_hash, old.digest, old.write_json, old.write_lines, old.lines


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class SupplementalPersistentLeaderTests(unittest.TestCase):
    """Reusable complete hash-chain fixture for the independent verifier."""

    def setUp(self):
        self.runner = module('supplemental_persistent_test_target', SOURCE)
        self.parent = old.PersistentLeaderRunnerTests()
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.root, self.panel, self.output = self.parent.root, self.parent.panel, self.parent.output
        self.document = read(REPO / REGISTRATION)
        self.registration = self.root / REGISTRATION
        self.symbols = list(self.parent.symbols)
        self.dates, day = [], date(2024, 7, 1)
        while len(self.dates) < 190:
            if day.weekday() < 5:
                self.dates.append(day.isoformat())
            day += timedelta(days=1)
        self.calendar = self.dates[:170]
        self.fixed = {'symbols': self.symbols, 'count': len(self.symbols), 'sha256': digest(self.symbols)}
        self.document['fixedPopulation'] = deepcopy(self.fixed)
        self.document['sample'].update(signalStart=self.dates[130], signalEnd=self.dates[169],
            latestPermittedObservation=self.dates[169], warmupStart=self.dates[0],
            expectedFullSecurities=9, expectedScoredSessions=40, expectedFeatureRows=360,
            expectedWarmupSessions=130)
        self.document['sample']['selection'].update(mode='explicit', symbols=self.symbols)
        files = set(self.document['frozenMethodImplementation']) | {SOURCE, VERIFIER,
            self.document['frozenIndependentVerifier']['path'], self.document['sourcePanelVerifier']['path'],
            'scripts/build-signal-supplemental-leadership-panel.py',
            'scripts/verify-supplemental-panel-closure.py', 'scripts/verify-signal-supplemental-source.py',
            'service/signal_leadership_features.py', 'tests/test_signal_supplemental_persistent_leader.py'}
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((REPO / name).read_bytes())
        self.source = self.root / 'synthetic-materialized-source'
        self.workspace = self.source / 'workspace'
        self.workspace.mkdir(parents=True)
        with closing(sqlite3.connect(self.workspace / 'market.sqlite3')) as db, db:
            db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE securities(symbol TEXT PRIMARY KEY, name TEXT NOT NULL)')
            db.execute('INSERT INTO meta VALUES (?,?)', ('path', json.dumps(str((self.source / 'tdx').resolve()))))
        for symbol in sorted(['sh000300', *self.symbols]):
            original = self.parent.source / 'vipdoc' / symbol[:2] / 'lday' / (symbol + '.day')
            records = list(struct.iter_unpack('<IIIIIfII', original.read_bytes()))[:170]
            path = self.source / 'tdx/vipdoc' / symbol[:2] / 'lday' / (symbol + '.day')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b''.join(struct.pack('<IIIIIfII', int(day.replace('-', '')), *record[1:])
                for day, record in zip(self.calendar, records)))
        self.build_source_contract()
        tools = old.runner.source_tools
        self.data = old.runner._base_bound_snapshot(tools.snapshot_universe(self.workspace,
            self.document['sample']['selection'], self.dates[169], self.dates[130]), self.dates[130], self.dates[169])
        self.document['sample'].update(expectedFullSnapshotFingerprint=self.data['fingerprint'],
            expectedFullBars=sum(len(security['bars']) for security in self.data['securities']))
        self.parent.workspace, self.parent.source = self.workspace, self.source / 'tdx'
        self.parent.dates, self.parent.data = self.dates, self.data
        self.parent.doc['sample'] = deepcopy(self.document['sample'])
        self.parent.build_panel()
        self.rows = self.parent.rows
        self.build_panel_contract()

    def archive(self, path, pins):
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
            for name in sorted(pins):
                stream.writestr(name, (self.root / name).read_bytes())

    def build_source_contract(self):
        """Synthetic upstream attestation, with genuine bytes and exact closure."""
        verifier = 'scripts/verify-signal-supplemental-source.py'
        implementation = {verifier: sha(self.root / verifier)}
        metadata_path = self.root / 'docs/synthetic-source-input.json'
        write_json(metadata_path, {'synthetic': True, 'fixedPopulation': self.fixed})
        metadata = {metadata_path.relative_to(self.root).as_posix(): sha(metadata_path)}
        self.archive(self.source / 'execution-sources.zip', {**implementation, **metadata})
        flags = {'returnsCalculated': False, 'featuresCalculated': False, 'certified': False, 'targetAchieved': False}
        protocol = {'schema': 'supplemental-source-protocol-v1', 'registrationSha256': sha(metadata_path),
            'registration': {'fixedPopulation': deepcopy(self.fixed), 'excludedOtherPinnedSymbols': []}, 'implementation': implementation,
            'metadata': metadata, 'inputFilesSha256': metadata,
            'executionArchiveSha256': sha(self.source / 'execution-sources.zip'), **flags}
        protocol['protocolHash'] = digest(protocol)
        write_json(self.source / 'protocol.json', protocol)
        rows = []
        for path in sorted((self.source / 'tdx').rglob('*.day'), key=lambda p: p.stem):
            rows.append({'symbol': path.stem, 'path': path.relative_to(self.source).as_posix(),
                'prefixSha256': sha(path), 'prefixBytes': path.stat().st_size,
                'prefixRecords': path.stat().st_size // 32, 'firstDate': self.calendar[0], 'lastDate': self.calendar[-1]})
        manifest = {'schema': 'supplemental-source-manifest-v1', 'protocolHash': protocol['protocolHash'],
            'prefixNumericQualityBefore2023Verified': False, 'fixedSymbols': self.symbols,
            'benchmarkSymbol': 'sh000300', 'cutoff': self.calendar[-1], 'sourceFiles': rows, 'excludedSources': [],
            'workspace': {'path': 'workspace/market.sqlite3', 'sha256': sha(self.workspace / 'market.sqlite3'),
                          'tdxPath': str((self.source / 'tdx').resolve())}, **flags}
        write_json(self.source / 'manifest.json', manifest)
        write_json(self.source / 'attempt.json', {'synthetic': True})
        completion = {'schema': 'supplemental-source-completion-v1', 'protocolHash': protocol['protocolHash'],
            'files': {p.relative_to(self.source).as_posix(): sha(p) for p in self.source.rglob('*') if p.is_file()}, **flags}
        write_json(self.source / 'completion.json', completion)
        audit = {'passed': True, 'failureCount': 0, 'synthetic': True, 'excludedOtherPinnedSymbols': 0, 'protocolHash': protocol['protocolHash'],
            'verifierSha256': implementation[verifier], 'completionSha256': sha(self.source / 'completion.json'),
            'implementation': implementation, 'resultFileHashes': completion['files'], 'sourceHashes': metadata, **flags}
        write_json(self.source / 'independent-verification.json', audit)
        self.document['sourceMaterialization'] = {'directory': self.source.relative_to(self.root).as_posix(),
            'files': {p.relative_to(self.source).as_posix(): sha(p) for p in self.source.rglob('*') if p.is_file()},
            'protocolHash': protocol['protocolHash'], 'registrationSha256': protocol['registrationSha256'],
            'independentVerifierSha256': implementation[verifier]}

    def build_panel_contract(self):
        self.panel_registration = self.root / 'docs/synthetic-supplemental-panel.json'
        self.panel_document = {'featureVersion': old.FEATURE_VERSION, 'fixedPopulation': self.fixed,
            'sample': deepcopy(self.document['sample']), 'sourceMaterialization': deepcopy(self.document['sourceMaterialization']),
            'synthetic': True}
        write_json(self.panel_registration, self.panel_document)
        self.document['sourcePanelRegistration'] = {'path': self.panel_registration.relative_to(self.root).as_posix(),
                                                     'sha256': sha(self.panel_registration)}
        self.manifest = read(self.panel / 'manifest.json')
        self.manifest.update(registration=deepcopy(self.panel_document), registrationSha256=sha(self.panel_registration),
            certified=False, targetAchieved=False, futureLabelsAccessed=False)
        names = ['scripts/build-signal-supplemental-leadership-panel.py', self.document['sourcePanelVerifier']['path'],
                 'service/signal_leadership_features.py']
        self.manifest['implementation'] = {name: sha(self.root / name) for name in names}
        self.archive(self.panel / 'execution-sources.zip', self.manifest['implementation'])
        self.manifest['executionArchiveSha256'] = sha(self.panel / 'execution-sources.zip')
        write_json(self.panel / 'manifest.json', self.manifest)
        self.manifest_hash = sha(self.panel / 'manifest.json')
        for row in self.rows:
            row['sampleManifestHash'] = self.manifest_hash
        write_lines(self.panel / 'warmup-coverage.jsonl.gz', [{'date': day, 'rows': 9} for day in self.calendar[:130]])
        write_json(self.panel / 'summary.json', {'featureRows': 360, 'targetSessions': 40, 'warmupSessions': 130,
            'sampleManifestHash': self.manifest_hash, 'certified': False, 'targetAchieved': False})
        self.audit = {'schema': 'supplemental-leadership-panel-independent-verification-v1', 'passed': True,
            'failureCount': 0, 'featureRows': 360, 'targetSessions': 40, 'warmupSessions': 130,
            'sampleManifestHash': self.manifest_hash, 'registrationSha256': sha(self.panel_registration),
            'verifierSha256': self.document['sourcePanelVerifier']['sha256'],
            'implementation': deepcopy(self.manifest['implementation']), 'certified': False, 'targetAchieved': False,
            'strategyReturnsCalculated': False, 'forwardReturnsCalculated': False, 'backwardPriceReturnsCalculated': True,
            'synthetic': True}
        self.closure_path = self.root / 'source-panel-closure.json'
        self.closure = {'passed': True, 'failureCount': 0, 'sampleManifestHash': self.manifest_hash,
            'targetSessions': 40, 'featureRows': 360, 'verifierPath': 'scripts/verify-supplemental-panel-closure.py',
            'verifierSha256': sha(self.root / 'scripts/verify-supplemental-panel-closure.py'), 'synthetic': True}
        self.refresh_panel()

    def rebind(self):
        write_json(self.registration, self.document)
        self.registration_hash = sha(self.registration)

    def refresh_panel(self, *, write_features=True):
        if write_features:
            write_lines(self.panel / 'features.jsonl.gz', self.rows)
        names = old.runner.PANEL_FILES
        completion = {'schema': 'raw-leadership-panel-completion-v1', 'sampleManifestHash': self.manifest_hash,
            'registrationSha256': sha(self.panel_registration), 'featureRows': 360, 'targetSessions': 40,
            'files': {name: {'path': name, 'sha256': sha(self.panel / name)} for name in names}}
        write_json(self.panel / 'completion.json', completion)
        self.audit['resultFileHashes'] = {name: sha(self.panel / name) for name in names | {'completion.json'}}
        write_json(self.panel / 'independent-verification.json', self.audit)
        self.closure['completionSha256'] = sha(self.panel / 'completion.json')
        write_json(self.closure_path, self.closure)
        self.document['sourcePanelClosureAudit'] = {'path': self.closure_path.relative_to(self.root).as_posix(),
                                                    'sha256': sha(self.closure_path)}
        self.document['registrationPrerequisites'].update(panelDirectory=self.panel.relative_to(self.root).as_posix(),
            panelManifestSha256=self.manifest_hash, panelCompletionSha256=sha(self.panel / 'completion.json'),
            actualPanelAuditSha256=sha(self.panel / 'independent-verification.json'),
            panelFilesSha256={name: sha(self.panel / name) for name in names | {'independent-verification.json'}})
        self.rebind()

    @contextmanager
    def context(self):
        with patch.object(self.runner, 'ROOT', self.root), patch.object(self.runner, 'REGISTRATION', self.registration), \
                patch.object(self.runner, 'REGISTRATION_SHA256', self.registration_hash), \
                patch.object(self.runner, 'SOURCE_WORKSPACE', self.workspace), \
                patch.object(self.runner, '__file__', str(self.root / SOURCE)), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield

    def args(self, **changes):
        values = dict(workspace=self.workspace, output=self.output, start=self.dates[130], end=self.dates[169],
            max_symbols=None, diagnostic_unverified_history=True)
        values.update(changes)
        return Namespace(**values)

    def freeze(self):
        with self.context():
            return self.runner.freeze(self.args())

    def execute(self):
        with self.context():
            return self.runner.execute(self.args())

    def complete(self):
        self.freeze()
        return self.execute()

    def attempt(self):
        return self.output / 'attempts/attempt-0001'

    def test_nonempty_explicit_sample_preserves_lexical_rank_control_seed_and_sources(self):
        before = {p: sha(p) for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(self.complete(), 0)
        self.assertEqual(before, {p: sha(p) for p in before})
        protocol = read(self.output / 'protocol.json')
        self.assertEqual(protocol['selection']['mode'], 'explicit')
        self.assertEqual(protocol['selection']['symbols'], self.symbols)
        self.assertEqual(protocol['strategyOrder'], ['rawPersistentPriceLeaderBreakoutDiagnostic'])
        selections = lines(self.attempt() / 'selection.jsonl.gz')
        expected = ['sh600006', 'sh600007', 'sh600005', 'sh600004', 'sh600003', 'sh600002', 'sh600001']
        self.assertEqual([row['symbol'] for row in selections[0]['fullRanking']], expected)
        self.assertEqual(selections[0]['selectedTop5Symbols'], expected[:5])
        pool = set(self.symbols) - set(expected[:5])
        controls = sorted(pool, key=lambda symbol: (hashlib.sha256(
            f'{self.runner.runtime.CONTROL_SEED}:{self.dates[130]}:{self.runner.CANDIDATE_ID}:{symbol}'.encode()).hexdigest(), symbol))
        self.assertEqual(selections[0]['controlMatching']['selectedSymbols'], controls)
        self.assertEqual(len(lines(self.attempt() / 'predicates.jsonl.gz')), 360)
        result = read_gzip(self.attempt() / 'result.json.gz')
        self.assertGreater(result['eventCount'], 0)
        self.assertGreater(result['overlapSuppressedCount'], 0)
        self.assertFalse(result['targetAchieved'])
        self.assertTrue(all(event['symbol'] not in expected[5:] for event in result['events']
            if event['strategyId'] == self.runner.CANDIDATE_ID))

    def test_cross_year_active_state_is_continuous_and_group_specific_and_exit_releases_at_close(self):
        first = {'strategyId': self.runner.CANDIDATE_ID, 'symbol': self.symbols[0],
                 'signalDate': self.dates[130], 'rank': 1, 'score': .95, 'rawScore': .95}
        config = self.runner.settings(self.args())
        initial = self.runner.evaluate_signals(self.data, config, [first])['events'][0]
        exit_day = initial['outcomes']['nextOpenNet30']['hitDate']
        self.assertEqual(first['signalDate'][:4], '2024')
        self.assertEqual(self.dates[132][:4], '2025')
        self.assertGreater(exit_day, self.dates[132])
        signals = [first, {**first, 'signalDate': self.dates[132]},
            {**first, 'signalDate': self.dates[132], 'strategyId': 'control_' + self.runner.CANDIDATE_ID},
            {**first, 'signalDate': exit_day}]
        result = self.runner.evaluate_signals(self.data, config, signals)
        self.assertEqual(result['overlapSuppressedCount'], 1)
        self.assertEqual(result['overlapSuppressed'][0]['signalDate'], self.dates[132])
        self.assertEqual([(e['strategyId'], e['signalDate']) for e in result['events']],
            [(signals[i]['strategyId'], signals[i]['signalDate']) for i in (0, 2, 3)])
        self.assertEqual(result['events'][-1]['entry']['date'], self.dates[self.dates.index(exit_day) + 1])
        # A separate replay starts empty, never importing holdings from a prior run.
        fresh = self.runner.evaluate_signals(self.data, config, [signals[1]])
        self.assertEqual((len(fresh['events']), fresh['overlapSuppressedCount']), (1, 0))

    def test_unfilled_entry_is_one_attempt_and_does_not_reserve_active_slot(self):
        data = deepcopy(self.data)
        security = next(row for row in data['securities'] if row['symbol'] == self.symbols[0])
        next(bar for bar in security['bars'] if bar['date'] == self.dates[131]).update(open=14., high=14., low=14., close=14.)
        signals = [{'strategyId': self.runner.CANDIDATE_ID, 'symbol': self.symbols[0],
                    'signalDate': day, 'rank': 1, 'score': .95, 'rawScore': .95} for day in self.dates[130:132]]
        result = self.runner.evaluate_signals(data, self.runner.settings(self.args()), signals)
        self.assertEqual(result['overlapSuppressedCount'], 0)
        self.assertEqual([event['entry']['status'] for event in result['events']], ['unfilled', 'filled'])
        self.assertEqual(result['events'][0]['entry']['retryPolicy'], 'noDeferredEntry')
        self.assertEqual(result['events'][1]['entry']['date'], self.dates[132])

    def test_fixed_population_selection_fingerprint_and_metadata_cannot_drift(self):
        for change in (lambda data: data['securities'][0].update(symbol='sh600999'),
                       lambda data: data['securities'].pop(),
                       lambda data: data.update(fingerprint='0' * 64)):
            data = deepcopy(self.data)
            change(data)
            with self.context(), self.assertRaises(ValueError):
                self.runner.validate_sample(data, self.dates[130], self.dates[169])
        data = deepcopy(self.data)
        data['securities'][0]['name'] = 'synthetic changed metadata'
        attempt = self.root / 'changed-metadata'
        attempt.mkdir()
        with self.context(), self.assertRaisesRegex(ValueError, 'snapshot content'):
            self.runner.collect_signals(data, self.runner.settings(self.args()), attempt)
        original = deepcopy(self.document['sample']['selection'])
        self.document['sample']['selection']['mode'] = 'all'
        self.rebind()
        with self.context(), self.assertRaises(ValueError):
            self.runner.registration()
        self.document['sample']['selection'] = original
        self.rebind()
        (self.source / 'tdx/vipdoc/sh/lday/sh600001.day').unlink()
        with self.context(), self.assertRaises((ValueError, FileNotFoundError)):
            self.runner.load_source_binding()

    def test_registration_two_panel_audits_and_source_inventory_are_mandatory(self):
        original = self.registration.read_bytes()
        self.registration.write_bytes(original + b' ')
        with self.context(), self.assertRaises(ValueError):
            self.runner.registration()
        self.registration.write_bytes(original)
        original_audit = deepcopy(self.audit)
        for change in ({'passed': False}, {'verifierSha256': '0' * 64}, {'implementation': {}}):
            self.audit = {**deepcopy(original_audit), **change}
            self.refresh_panel()
            with self.subTest(change=change), self.context(), self.assertRaises(ValueError):
                self.runner.verify_panel_binding(self.document)
        self.audit = original_audit
        self.refresh_panel()
        original_closure = deepcopy(self.closure)
        for change in ({'passed': False}, {'featureRows': 359}, {'completionSha256': '0' * 64}):
            self.closure = {**deepcopy(original_closure), **change}
            write_json(self.closure_path, self.closure)
            self.document['sourcePanelClosureAudit']['sha256'] = sha(self.closure_path)
            self.rebind()
            with self.subTest(change=change), self.context(), self.assertRaises(ValueError):
                self.runner.verify_panel_binding(self.document)
        self.closure = original_closure
        self.refresh_panel()
        (self.source / 'unexpected.tnf').write_bytes(b'not an admitted source')
        with self.context(), self.assertRaises(ValueError):
            self.runner.load_source_binding()

    def test_stream_requires_all_rows_and_eof_even_with_rehashed_synthetic_input(self):
        original = deepcopy(self.rows)
        for mutation in (lambda rows: rows.pop(), lambda rows: rows.__setitem__(1, deepcopy(rows[0])),
                         lambda rows: rows.append(deepcopy(rows[-1]))):
            self.rows = deepcopy(original)
            mutation(self.rows)
            self.refresh_panel()
            with self.context(), self.assertRaises(ValueError):
                list(self.runner.iter_panel_days(self.runner.verify_panel_binding(self.document)))
        self.rows = original
        self.refresh_panel()
        path = self.panel / 'features.jsonl.gz'
        path.write_bytes(path.read_bytes()[:-5])
        self.refresh_panel(write_features=False)
        self.freeze()  # freeze binds bytes without evaluating predicates or outcomes
        with self.assertRaises((ValueError, EOFError, OSError)):
            self.execute()
        self.assertFalse((self.attempt() / 'completion.json').exists())

    def test_freeze_is_exclusive_failure_is_preserved_and_never_restarted(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.execute()
        self.assertFalse(self.output.exists())
        with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('freeze must not classify')):
            self.freeze()
        with self.assertRaises((ValueError, FileExistsError)):
            self.freeze()
        with patch.object(self.runner, 'evaluate_signals', side_effect=RuntimeError('synthetic failure')) as evaluate:
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                self.execute()
            self.assertEqual(evaluate.call_count, 1)
        preserved = {path: sha(path) for path in self.output.rglob('*') if path.is_file()}
        self.assertTrue((self.output / 'failure.json').is_file())
        with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('failed runs must not restart')):
            with self.assertRaises(ValueError):
                self.execute()
        self.assertEqual(preserved, {path: sha(path) for path in preserved})
        self.assertEqual([path.name for path in (self.output / 'attempts').iterdir()], ['attempt-0001'])

    def test_success_only_verifies_complete_archive_and_source_binding(self):
        self.complete()
        receipt = read(self.attempt() / 'completion.json')
        self.assertEqual(set(receipt['files']), {'candidates', 'coverage', 'signals', 'result', 'summary',
            'predicates', 'selections', 'executionSources'})
        self.assertEqual(set(receipt['frozenFiles']), {'protocol', 'selection', 'snapshot', 'executionSources'})
        protocol = read(self.output / 'protocol.json')
        with zipfile.ZipFile(self.output / 'execution-sources.zip') as archive:
            self.assertEqual(set(archive.namelist()), set(protocol['implementation']))
            for name, expected in protocol['implementation'].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
        with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('completed run must not classify')), \
                patch.object(self.runner, 'evaluate_signals', side_effect=AssertionError('completed run must not evaluate')):
            self.assertEqual(self.execute(), 0)
        path = self.output / 'execution-sources.zip'
        original = path.read_bytes()
        path.write_bytes(original + b'changed')
        with self.assertRaises(ValueError):
            self.execute()
        path.write_bytes(original)
        path = self.source / 'tdx/vipdoc/sh/lday/sh600001.day'
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            self.execute()

    def test_zero_candidates_keep_full_coverage_and_complete_both_empty_groups(self):
        for row in self.rows:
            row.update(trailing20DailyStrengthPercentiles=[.5] * 20, relativeReturn20Percentile=.5,
                meanStrengthPercentile20=.5, minimumStrengthPercentile20=.5)
        self.refresh_panel()
        self.complete()
        self.assertEqual(lines(self.attempt() / 'signals.jsonl.gz'), [])
        self.assertEqual(len(lines(self.attempt() / 'predicates.jsonl.gz')), 360)
        result = read_gzip(self.attempt() / 'result.json.gz')
        self.assertEqual(result['eventCount'], 0)
        self.assertEqual(len(result['groups']), 2)

    def test_independent_replay_and_rehashed_rank_semantic_tamper(self):
        self.complete()
        verifier = module('supplemental_persistent_reference', VERIFIER)
        with patch.object(verifier, 'REGISTRATION', self.registration), \
                patch.object(verifier, 'REGISTRATION_SHA256', self.registration_hash):
            passed = verifier.verify(self.output, self.panel, root=self.root)
            self.assertTrue(passed['passed'], passed.get('issues', passed.get('failures')))
            verifier.save_report(self.output, passed)
            with patch.object(self.runner, 'collect_signals', side_effect=AssertionError('audited success must not classify')), \
                    patch.object(self.runner, 'evaluate_signals', side_effect=AssertionError('audited success must not evaluate')):
                self.assertEqual(self.execute(), 0)
            path = self.attempt() / 'selection.jsonl.gz'
            selections = lines(path)
            selections[0]['fullRanking'][0], selections[0]['fullRanking'][1] = selections[0]['fullRanking'][1], selections[0]['fullRanking'][0]
            write_lines(path, selections)
            receipt = read(self.attempt() / 'completion.json')
            receipt['files']['selections']['sha256'] = sha(path)
            write_json(self.attempt() / 'completion.json', receipt)
            # Rehash both copies of the synthetic receipt to reach independent
            # ranking replay; the corrupted semantics, not stale SHA, must fail.
            journal = self.output / 'events.jsonl'
            events = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
            for event in events:
                if event['event'] == 'study_succeeded':
                    event['receipt'] = receipt
            journal.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
            failed = verifier.verify(self.output, self.panel, root=self.root)
            self.assertFalse(failed['passed'])
            self.assertGreater(failed['failureCount'], 0)
            self.assertTrue(any(item['check'] == 'selection-full-ranking' for item in failed['issues']), failed['issues'])


def read_gzip(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


if __name__ == '__main__':
    unittest.main()
