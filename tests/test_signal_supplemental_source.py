"""Synthetic immutable archive -> cutoff source tests; no live market input."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import struct
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


quality_tests = module('supplemental_quality_fixture', 'tests/test_signal_interval_quality_runner.py')
runner = module('supplemental_source_target', 'scripts/materialize-signal-supplemental-source.py')


class SupplementalSourceTests(unittest.TestCase):
    """The public fixture is also reused by the independent verifier's tests."""

    def setUp(self):
        self.parent = quality_tests.IntervalQualityRunnerTests()
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.base = self.parent.base
        self.output = self.base / 'materialized'
        self.registration = self.base / 'source-registration.json'
        self.document = json.loads((ROOT / 'docs/signal-supplemental-source-v1.json').read_text(encoding='utf-8'))
        self.fixed = list(self.parent.fixed)
        self.selected = sorted(['sh000300', *self.fixed])
        self.raw = {}
        # Include a genuine pre-audit prefix. Its numeric quality is not newly certified.
        for symbol, path in self.parent.paths.items():
            raw = quality_tests.raw_record('2022-12-30', low=0) + quality_tests.raw_record('2023-12-29') + path.read_bytes()
            self.rewrite_source(symbol, raw)
        self.parent.dates.insert(0, '2023-12-29')
        calendar = quality_tests.runner.read(self.parent.calendar)
        calendar.update(start='2023-01-01', dates=self.parent.dates, sessionsByYear={'2023': 1, '2024': 2, '2025': 1})
        self.put(self.parent.calendar, calendar)
        self.parent.document['auditStart'] = '2023-01-01'
        self.parent.document['calendar']['sha256'] = self.digest(self.parent.calendar)
        self.parent.rebind()
        self.prepared = False

    @staticmethod
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def put(path, value):
        Path(path).write_bytes(quality_tests.runner.encoded(value) + b'\n')

    def rewrite_source(self, symbol, raw):
        path = self.parent.paths[symbol]
        if raw is None:
            path.unlink()
            return
        path.write_bytes(raw)
        self.raw[symbol] = raw
        manifest = quality_tests.runner.read(self.parent.manifest)
        manifest['physicalSourceHashes'][str(path)] = self.digest(path)
        self.put(self.parent.manifest, manifest)
        self.parent.rebind()

    def rebind(self):
        self.put(self.registration, self.document)
        runner.REGISTRATION_SHA = self.digest(self.registration)

    def prepare(self):
        if self.prepared:
            return
        self.parent.complete()
        verifier = quality_tests.module('supplemental_quality_reference', 'verify-signal-interval-quality.py')
        with patch.object(verifier, 'REGISTRATION', self.parent.registration), patch.object(verifier, 'REGISTRATION_SHA', quality_tests.runner.REGISTRATION_SHA):
            report = verifier.verify(self.parent.output, root=ROOT)
        self.assertTrue(report['passed'], report.get('failures'))
        verifier.save_report(self.parent.output, report)
        protocol = quality_tests.runner.read(self.parent.output / 'protocol.json')
        quality = self.document['quality']
        quality.update(directory=self.parent.output.relative_to(ROOT).as_posix(),
                       protocolHash=protocol['protocolHash'],
                       registrationSha256=quality_tests.runner.REGISTRATION_SHA,
                       independentVerifierSha256=report['verifierSha256'])
        quality['files'] = {name: self.digest(self.parent.output / name) for name in quality['files']}
        self.document['fixedPopulation'] = copy.deepcopy(protocol['registration']['fixedPopulation'])
        self.document.update(expectedMaterializedFiles=3, excludedOtherPinnedSymbols=['sh601001'])
        self.document['featureCalendar'].update(path=self.parent.calendar.relative_to(ROOT).as_posix(),
            sha256=self.digest(self.parent.calendar), expectedWarmupSessions=1, expectedTargetSessions=3)
        self.put(self.registration, self.document)
        self.addCleanup(patch.stopall)
        patch.object(runner, 'REGISTRATION', self.registration).start()
        patch.object(runner, 'REGISTRATION_SHA', self.digest(self.registration)).start()
        self.prepared = True

    def complete(self):
        self.prepare()
        return runner.materialize(self.output)

    def update_quality_file(self, name, value):
        """Rebind a deliberately corrupted upstream semantic claim, not its bytes."""
        self.put(self.parent.output / name, value)
        if name in ('symbol-summary.json', 'summary.json', 'daily-coverage.json'):
            receipt = quality_tests.runner.read(self.parent.output / 'completion.json')
            receipt['files'][name] = self.digest(self.parent.output / name)
            self.put(self.parent.output / 'completion.json', receipt)
        # Deliberately forged synthetic upstream attestation: reaching the
        # independent prefix check must not rely on an earlier stale-hash failure.
        audit = quality_tests.runner.read(self.parent.output / 'independent-verification.json')
        audit['sourceHashes'] = {
            filename: self.digest(self.parent.output / filename)
            for filename in self.document['quality']['files']
            if filename != 'independent-verification.json'}
        self.put(self.parent.output / 'independent-verification.json', audit)
        self.document['quality']['files'] = {
            filename: self.digest(self.parent.output / filename)
            for filename in self.document['quality']['files']}
        self.rebind()

    def test_materializes_exact_prefixes_without_live_sources_or_other_symbols(self):
        self.prepare()
        for path in self.parent.paths.values():
            path.unlink()
        result = runner.materialize(self.output)
        actual = sorted(p for p in (self.output / 'tdx').rglob('*.day'))
        self.assertEqual([p.stem for p in actual], self.selected)
        for path in actual:
            expected = self.raw[path.stem][:-32]
            self.assertEqual(path.read_bytes(), expected)
            dates = [record[0] for record in struct.iter_unpack('<IIIIIfII', path.read_bytes())]
            self.assertLessEqual(max(dates), 20251231)
        self.assertFalse(list(self.output.rglob('*.tnf')))
        self.assertFalse(list(self.output.rglob('*cache*')))
        self.assertTrue((self.output / 'completion.json').is_file())
        self.assertIsInstance(result, dict)

    def test_minimal_database_is_verified_readonly_and_no_names_are_copied(self):
        self.complete()
        database = self.output / 'workspace/market.sqlite3'
        before = self.digest(database)
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), [('meta',), ('securities',)])
            self.assertEqual(db.execute('SELECT symbol,name FROM securities').fetchall(), [])
            self.assertEqual(db.execute('SELECT key,value FROM meta').fetchall(), [('path', json.dumps(str((self.output / 'tdx').resolve())))])
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("INSERT INTO securities VALUES ('sh600001','forbidden current name')")
        runner.verify_completed(self.output, runner.load_binding())
        self.assertEqual(self.digest(database), before)

    def test_archive_tampering_rejected_before_output_creation(self):
        self.prepare()
        with (self.parent.output / 'input-sources.zip').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaises(ValueError):
            runner.materialize(self.output)
        self.assertFalse(self.output.exists())

    def test_false_prefix_pin_is_rejected_even_after_upstream_file_rehash(self):
        self.prepare()
        rows = quality_tests.runner.read(self.parent.output / 'symbol-summary.json')
        row = next(row for row in rows if row['symbol'] == self.fixed[0])
        row['prefixThroughEndSha256'] = '0' * 64
        self.update_quality_file('symbol-summary.json', rows)
        with self.assertRaisesRegex(ValueError, 'Independent prefix/structure differs'):
            runner.materialize(self.output)
        self.assertFalse((self.output / 'completion.json').exists())

    def test_prefix_length_cannot_extend_beyond_original_member(self):
        self.prepare()
        rows = quality_tests.runner.read(self.parent.output / 'symbol-summary.json')
        row = next(row for row in rows if row['symbol'] == self.fixed[0])
        row['prefixThroughEndBytes'] = len(self.raw[self.fixed[0]]) + 32
        row['prefixThroughEndRecords'] = row['prefixThroughEndBytes'] // 32
        self.update_quality_file('symbol-summary.json', rows)
        with self.assertRaisesRegex(ValueError, 'Independent prefix/structure differs'):
            runner.materialize(self.output)
        self.assertFalse((self.output / 'completion.json').exists())

    def test_missing_selected_source_cannot_be_dropped_or_replaced(self):
        self.rewrite_source(self.fixed[0], None)
        self.prepare()
        with self.assertRaises(ValueError):
            runner.materialize(self.output)
        self.assertFalse((self.output / 'completion.json').exists())

    def test_empty_selected_prefix_is_not_a_valid_fixed_population(self):
        self.rewrite_source(self.fixed[0], quality_tests.raw_record('2026-01-05'))
        self.prepare()
        with self.assertRaises(ValueError):
            runner.materialize(self.output)
        self.assertFalse((self.output / 'completion.json').exists())

    def test_unsafe_ordered_prefix_is_rejected_instead_of_repaired(self):
        self.rewrite_source(self.fixed[0], quality_tests.raw_record('2024-01-03') + quality_tests.raw_record('2024-01-02'))
        self.prepare()
        with self.assertRaises(ValueError):
            runner.materialize(self.output)
        self.assertFalse((self.output / 'completion.json').exists())

    def test_completed_output_is_never_overwritten(self):
        self.complete()
        before = self.digest(self.output / 'completion.json')
        with self.assertRaises(FileExistsError):
            runner.materialize(self.output)
        self.assertEqual(self.digest(self.output / 'completion.json'), before)

    def test_midwrite_failure_is_retained_and_cannot_be_restarted(self):
        self.prepare()
        with patch.object(runner, '_create_database', side_effect=OSError('synthetic database write failure')):
            with self.assertRaisesRegex(OSError, 'synthetic database write failure'):
                runner.materialize(self.output)
        failure = self.output / 'failure.json'
        self.assertTrue((self.output / 'attempt.json').is_file())
        self.assertTrue(failure.is_file())
        self.assertFalse((self.output / 'completion.json').exists())
        before = failure.read_bytes()
        with self.assertRaises(FileExistsError):
            runner.materialize(self.output)
        self.assertEqual(failure.read_bytes(), before)

    def test_corrupted_output_or_extra_database_table_fails_verification(self):
        self.complete()
        database = self.output / 'workspace/market.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE unregistered_cache(value TEXT)')
        with self.assertRaisesRegex(ValueError, 'Unexpected SQLite tables'):
            runner._database_info(self.output)
        with self.assertRaises(ValueError):
            runner.verify_completed(self.output, runner.load_binding())

    def test_output_alias_file_is_not_accepted_as_an_extra_security(self):
        self.complete()
        original = self.output / 'tdx/vipdoc/sh/lday/sh600001.day'
        original.with_name('600001.day').write_bytes(original.read_bytes())
        with self.assertRaises(ValueError):
            runner.verify_completed(self.output, runner.load_binding())

    def test_output_path_contract_cannot_escape_the_new_workspace(self):
        self.prepare()
        self.document['workspace']['tdxRelativePath'] = '../outside'
        self.rebind()
        with self.assertRaises(ValueError):
            runner.materialize(self.output)
        self.assertFalse((self.base / 'outside').exists())


if __name__ == '__main__':
    unittest.main()
