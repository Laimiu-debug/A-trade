"""Synthetic target-only market observations; no real panel or strategy input."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_signal_market_state_runner as old_fixture


REPO = Path(__file__).resolve().parents[1]
SOURCE = 'scripts/build-signal-supplemental-market-state-panel.py'
VERIFIER = 'scripts/verify-signal-supplemental-market-state-panel.py'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module('supplemental_market_target', SOURCE)
sha, digest = old_fixture.sha, old_fixture.digest
write_json, write_lines, lines = old_fixture.write_json, old_fixture.write_lines, old_fixture.lines


class SupplementalMarketStateTests(unittest.TestCase):
    """The input audit is an explicit synthetic contract, not a real feature audit."""

    def setUp(self):
        self.parent = old_fixture.MarketStateRunnerTests()
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.root, self.panel, self.output = self.parent.root, self.parent.panel, self.parent.output
        self.registration = self.root / 'docs/supplemental-market-registration.json'
        self.symbols = list(self.parent.symbols)
        start = date(2024, 1, 2)
        preceding, day = [], start - timedelta(days=1)
        while len(preceding) < 60:
            if day.weekday() < 5:
                preceding.append(day.isoformat())
            day -= timedelta(days=1)
        targets, day = [], start
        while len(targets) < 7:
            if day.weekday() < 5:
                targets.append(day.isoformat())
            day += timedelta(days=1)
        self.calendar, self.dates = list(reversed(preceding)) + targets, targets
        date_map = dict(zip(self.parent.calendar, self.calendar))

        def redated(value):
            if isinstance(value, dict):
                return {date_map.get(key, key): redated(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redated(item) for item in value]
            return date_map.get(value, value) if isinstance(value, str) else value

        self.rows = redated(deepcopy(self.parent.rows))
        # Exercise all three Q classes and all four next-day observed roles.
        self.rows[2]['signedPathEfficiency20'] = .2
        self.rows[3]['signedPathEfficiency20'] = None
        self.rows[3]['featureReasons']['signedPathEfficiency20'] = ['missingPath']
        self.rows[4]['signedPathEfficiency20'] = None
        self.rows[4]['featureReasons']['signedPathEfficiency20'] = ['missingPath']
        self.rows[4]['closePathMaxDrawdown20'] = .2
        self.rows[5]['signedPathEfficiency20'] = .2

        original = json.loads((REPO / 'docs/signal-market-state-panel-v1.json').read_text(encoding='utf-8'))
        self.doc = deepcopy(self.parent.doc)
        self.doc['design'] = deepcopy(original['design'])
        self.doc['sample'].update(start=self.dates[0], end=self.dates[-1])
        self.fixed = {'symbols': self.symbols, 'count': 4, 'sha256': digest(self.symbols)}
        self.doc['fixedPopulation'] = deepcopy(self.fixed)
        dependencies = [SOURCE, VERIFIER, 'service/signal_market_state.py',
            'scripts/build-signal-market-state-panel.py', 'scripts/verify-signal-market-state-panel.py',
            'docs/signal-market-state-panel-v1.json', self.doc['design']['path'],
            'scripts/build-signal-supplemental-leadership-panel.py',
            'scripts/verify-signal-supplemental-leadership-panel.py',
            'scripts/verify-supplemental-panel-closure.py', 'service/signal_leadership_features.py',
            'tests/test_signal_supplemental_market_state.py']
        for name in dependencies:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((REPO / name).read_bytes())
        self.local_runner = self.root / SOURCE
        old_members = ['scripts/build-signal-market-state-panel.py', 'service/signal_market_state.py',
                       'docs/signal-market-state-panel-v1.json']
        self.doc['frozenObservationImplementation'] = {name: sha(self.root / name) for name in old_members}
        for key, name in (
            ('frozenBuilder', 'scripts/build-signal-market-state-panel.py'),
            ('frozenIndependentVerifier', 'scripts/verify-signal-market-state-panel.py'),
            ('sourcePanelVerifier', 'scripts/verify-signal-supplemental-leadership-panel.py')):
            self.doc[key] = {'path': name, 'sha256': sha(self.root / name)}
        self.panel_registration = self.root / 'docs/synthetic-supplemental-panel.json'
        self.panel_document = {'schema': 'raw-leadership-panel-registration-v1',
            'featureVersion': self.doc['sourceFeatureVersion'], 'fixedPopulation': deepcopy(self.fixed),
            'sample': {'signalStart': self.dates[0], 'signalEnd': self.dates[-1],
                'warmupStart': self.calendar[0], 'expectedWarmupSessions': 60,
                'expectedScoredSessions': 7, 'expectedFeatureRows': 28, 'expectedFullSecurities': 4,
                'expectedFullSnapshotFingerprint': self.doc['sample']['snapshotFingerprint']},
            'synthetic': True}
        write_json(self.panel_registration, self.panel_document)
        self.doc['sourcePanelRegistration'] = {'path': self.panel_registration.relative_to(self.root).as_posix(),
                                               'sha256': sha(self.panel_registration)}
        self.doc['exposure'] = {'pristineHoldout': False, 'synthetic': True}
        self.doc['downstreamPolicy'] = {'marketRoleHistory': 'start at first target date; no warmup roles'}
        self.manifest = deepcopy(self.parent.manifest)
        self.manifest.update(schema='raw-leadership-panel-manifest-v1', registration=self.panel_document,
            registrationSha256=sha(self.panel_registration), certified=False, targetAchieved=False,
            futureLabelsAccessed=False)
        self.manifest['inputs']['calendar'] = {'dates': self.calendar, 'sha256': digest(self.calendar)}
        self.manifest['implementation'] = {name: sha(self.root / name) for name in (
            'scripts/build-signal-supplemental-leadership-panel.py',
            'scripts/verify-signal-supplemental-leadership-panel.py', 'service/signal_leadership_features.py')}
        with zipfile.ZipFile(self.panel / 'execution-sources.zip', 'w') as archive:
            for name in self.manifest['implementation']:
                archive.writestr(name, (self.root / name).read_bytes())
        self.manifest['executionArchiveSha256'] = sha(self.panel / 'execution-sources.zip')
        write_json(self.panel / 'manifest.json', self.manifest)
        self.manifest_hash = sha(self.panel / 'manifest.json')
        for row in self.rows:
            row.update(sampleManifestHash=self.manifest_hash, calendarHash=digest(self.calendar))
        write_lines(self.panel / 'daily-coverage.jsonl.gz', [{'date': day, 'rows': 4} for day in self.dates])
        write_lines(self.panel / 'warmup-coverage.jsonl.gz', [{'date': day, 'rows': 4} for day in self.calendar[:60]])
        write_json(self.panel / 'summary.json', {'featureRows': 28, 'targetSessions': 7, 'warmupSessions': 60,
            'certified': False, 'targetAchieved': False})
        self.audit = {'schema': 'supplemental-leadership-panel-independent-verification-v1',
            'passed': True, 'failureCount': 0, 'featureRows': 28, 'targetSessions': 7, 'warmupSessions': 60,
            'sampleManifestHash': self.manifest_hash, 'registrationSha256': sha(self.panel_registration),
            'verifierSha256': self.doc['sourcePanelVerifier']['sha256'],
            'implementation': deepcopy(self.manifest['implementation']), 'certified': False, 'targetAchieved': False,
            'strategyReturnsCalculated': False, 'forwardReturnsCalculated': False,
            'backwardPriceReturnsCalculated': True, 'synthetic': True}
        self.closure_path = self.root / 'source-panel-closure.json'
        self.closure = {'passed': True, 'failureCount': 0, 'sampleManifestHash': self.manifest_hash,
            'targetSessions': 7, 'featureRows': 28, 'verifierPath': 'scripts/verify-supplemental-panel-closure.py',
            'verifierSha256': sha(self.root / 'scripts/verify-supplemental-panel-closure.py'), 'synthetic': True}
        self.refresh_panel()

    def refresh_registration(self):
        write_json(self.registration, self.doc)
        self.registration_hash = sha(self.registration)

    def refresh_panel(self, *, write_features=True):
        """Rebind intentionally altered synthetic contracts, never real audit reports."""
        if write_features:
            write_lines(self.panel / 'features.jsonl.gz', self.rows)
        names = old_fixture.runner.PANEL_FILES - {'completion.json', 'independent-verification.json'}
        self.completion = {'schema': 'raw-leadership-panel-completion-v1',
            'sampleManifestHash': self.manifest_hash, 'registrationSha256': sha(self.panel_registration),
            'featureRows': 28, 'targetSessions': 7,
            'files': {name: {'path': name, 'sha256': sha(self.panel / name)} for name in names}}
        write_json(self.panel / 'completion.json', self.completion)
        self.audit['resultFileHashes'] = {name: sha(self.panel / name) for name in names | {'completion.json'}}
        write_json(self.panel / 'independent-verification.json', self.audit)
        self.doc['input']['filesSha256'] = {name: sha(self.panel / name) for name in old_fixture.runner.PANEL_FILES}
        self.closure['completionSha256'] = sha(self.panel / 'completion.json')
        write_json(self.closure_path, self.closure)
        self.doc['sourcePanelClosureAudit'] = {'path': self.closure_path.relative_to(self.root).as_posix(),
                                              'sha256': sha(self.closure_path)}
        self.refresh_registration()

    @contextmanager
    def context(self):
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'REGISTRATION', self.registration), \
                patch.object(runner, 'REGISTRATION_SHA256', self.registration_hash), \
                patch.object(runner, '__file__', str(self.local_runner)), redirect_stdout(io.StringIO()):
            yield

    def freeze(self, output=None):
        with self.context():
            return runner.freeze(output or self.output)

    def execute(self, output=None):
        with self.context():
            return runner.execute(output or self.output)

    def run_complete(self):
        self.freeze()
        return self.execute()

    def test_new_target_start_keeps_all_roles_and_resets_previous_and_five_day_history(self):
        before = {path: sha(path) for path in self.root.rglob('*') if path.is_file()}
        original_open = gzip.open

        def no_warmup_read(filename, *args, **kwargs):
            if Path(filename).name == 'warmup-coverage.jsonl.gz':
                raise AssertionError('target observation must not consume feature warmup history')
            return original_open(filename, *args, **kwargs)

        self.freeze()
        with patch.object(gzip, 'open', side_effect=no_warmup_read):
            receipt = self.execute()
        self.assertEqual(before, {path: sha(path) for path in before})
        self.assertEqual((receipt['dailyRows'], receipt['roleRows']), (7, 28))
        daily, roles = lines(self.output / 'daily.jsonl.gz'), lines(self.output / 'roles.jsonl.gz')
        self.assertEqual(daily[0]['date'], '2024-01-02')
        self.assertEqual([(row['date'], row['symbol']) for row in roles], [(day, symbol) for day in self.dates for symbol in self.symbols])
        self.assertTrue(all(row['priorQState'] is None and not row['previousRecordAvailable'] for row in roles[:4]))
        self.assertFalse(daily[0]['yesterdayCohort']['previousCohortAvailable'])
        self.assertTrue(all(not row['matched5']['available'] and row['matched5']['pairedCount'] is None for row in daily[:5]))
        self.assertTrue(daily[5]['matched5']['available'])
        self.assertEqual(daily[5]['matched5']['earlierDate'], self.dates[0])
        self.assertEqual([row['transition'] for row in roles[4:8]], ['unknown', 'observedFailure', 'knownNewChallenger', 'priorStateUnknownEntrant'])
        self.assertEqual({row['qState'] for row in roles}, {'true', 'false', 'unknown'})
        self.assertTrue(all(row['baseEligibility']['eligible'] is False for row in roles))

    def test_registration_and_exact_fixed_symbols_cannot_be_replaced_by_same_count(self):
        original = self.registration.read_bytes()
        self.registration.write_bytes(original + b' ')
        with self.context(), self.assertRaises(ValueError):
            runner.load_binding()
        self.registration.write_bytes(original)
        self.doc['fixedPopulation']['symbols'] = ['sh600001', 'sh600002', 'sh600999', 'sz000001']
        self.doc['fixedPopulation']['sha256'] = digest(self.doc['fixedPopulation']['symbols'])
        self.refresh_registration()
        with self.context(), self.assertRaises(ValueError):
            runner.load_binding()

    def test_source_panel_requires_new_verifier_registration_and_passed_audit(self):
        original = deepcopy(self.audit)
        for values in ({'passed': False}, {'verifierSha256': '0' * 64},
                       {'registrationSha256': '0' * 64}, {'warmupSessions': 59}):
            with self.subTest(change=values):
                self.audit = {**deepcopy(original), **values}
                self.refresh_panel()
                with self.context(), self.assertRaises(ValueError):
                    runner.load_binding()

    def test_external_closure_audit_counts_and_completion_chain_are_required(self):
        original = deepcopy(self.closure)
        for values in ({'passed': False}, {'targetSessions': 6}, {'featureRows': 27},
                       {'sampleManifestHash': '0' * 64}, {'completionSha256': '0' * 64}):
            with self.subTest(change=values):
                self.closure = {**deepcopy(original), **values}
                write_json(self.closure_path, self.closure)
                self.doc['sourcePanelClosureAudit']['sha256'] = sha(self.closure_path)
                self.refresh_registration()
                with self.context(), self.assertRaises(ValueError):
                    runner.load_binding()

    def test_full_target_stream_rejects_missing_duplicate_and_warmup_records(self):
        original = deepcopy(self.rows)
        changes = (
            lambda rows: rows.pop(),
            lambda rows: rows.__setitem__(1, deepcopy(rows[0])),
            lambda rows: rows.append(deepcopy(rows[-1])),
            lambda rows: rows[0].update(asOfDate=self.calendar[59]),
        )
        for number, change in enumerate(changes):
            with self.subTest(change=number):
                self.rows = deepcopy(original)
                change(self.rows)
                self.refresh_panel()
                with self.context(), self.assertRaises(ValueError):
                    list(runner.iter_days(runner.load_binding()))

    def test_truncated_gzip_is_terminal_even_after_all_expected_json_rows(self):
        path = self.panel / 'features.jsonl.gz'
        path.write_bytes(path.read_bytes()[:-5])
        self.refresh_panel(write_features=False)
        self.freeze()
        with self.assertRaises((ValueError, EOFError, OSError)):
            self.execute()
        self.assertTrue((self.output / 'failure.json').is_file())
        self.assertFalse((self.output / 'completion.json').exists())
        before = (self.output / 'failure.json').read_bytes()
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual((self.output / 'failure.json').read_bytes(), before)

    def test_verified_success_only_checks_receipt_and_input_hashes(self):
        first = self.run_complete()
        saved = {path: sha(path) for path in self.output.iterdir()}
        with patch.object(runner.runtime, 'MarketStateTracker', side_effect=AssertionError('cannot recompute')):
            self.assertEqual(self.execute(), first)
        self.assertEqual(saved, {path: sha(path) for path in saved})
        path = self.panel / 'summary.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            self.execute()

    def test_independent_replay_rejects_rehashed_role_tampering(self):
        self.run_complete()
        verifier = module('supplemental_market_reference', VERIFIER)
        with patch.object(verifier, 'REGISTRATION', self.registration), patch.object(verifier, 'REGISTRATION_SHA256', self.registration_hash):
            report = verifier.verify(self.output, panel=self.panel, root=self.root)
            self.assertTrue(report['passed'], report.get('issues', report.get('failures')))
            role_path = self.output / 'roles.jsonl.gz'
            original = lines(role_path)
            receipt = json.loads((self.output / 'completion.json').read_text(encoding='utf-8'))
            duplicate = deepcopy(original)
            duplicate[1] = deepcopy(duplicate[0])
            for label, rows in (('missing', original[:-1]), ('duplicate', duplicate)):
                with self.subTest(change=label):
                    write_lines(role_path, rows)
                    receipt['files'][role_path.name]['sha256'] = sha(role_path)
                    write_json(self.output / 'completion.json', receipt)
                    result = verifier.verify(self.output, panel=self.panel, root=self.root)
                    self.assertFalse(result['passed'])


if __name__ == '__main__':
    unittest.main()
