"""Synthetic audited cutoff source integration; never extract real features."""
from argparse import Namespace
from collections import Counter
from contextlib import redirect_stdout
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_signal_leadership_panel_runner as old_panel
import test_signal_supplemental_source as source_fixture


ROOT = Path(__file__).resolve().parents[1]
runner = source_fixture.module('supplemental_leadership_target', 'scripts/build-signal-supplemental-leadership-panel.py')


class SupplementalLeadershipPanelTests(unittest.TestCase):
    """A reusable nonempty fixture with independently verified upstream bytes."""

    def setUp(self):
        self.parent = old_panel.LeadershipPanelRunnerTests()
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.source = source_fixture.SupplementalSourceTests()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.root = self.source.base
        self.output = self.root / 'supplemental-panel'
        self.registration = self.root / 'panel-registration.json'
        self.calendar = list(self.parent.calendar)
        self.fixed = ['sh600001', 'sh600002', 'sz000001']
        self.prepare_source()
        self.workspace = self.source.output / 'workspace'
        self.document = json.loads((ROOT / 'docs/signal-supplemental-leadership-panel-v1.json').read_text(encoding='utf-8'))
        self.document['fixedPopulation'] = deepcopy(self.source.document['fixedPopulation'])
        self.document['sample'] = deepcopy(self.parent.document['sample'])
        self.document['sample']['selection'].update(mode='explicit', symbols=list(self.fixed))
        tools = old_panel.runner.source_tools
        self.data = tools.bound_snapshot(tools.snapshot_universe(self.workspace,
            self.document['sample']['selection'], self.calendar[-1], self.calendar[130]),
            self.calendar[130], self.calendar[-1])
        self.document['sample']['expectedFullSnapshotFingerprint'] = self.data['fingerprint']
        self.document['calendar'].update(path=self.source.parent.calendar.relative_to(ROOT).as_posix(),
            sha256=self.digest(self.source.parent.calendar), schema='exchange-notice-derived-calendar-v1',
            start=self.calendar[0], end=self.calendar[-1])
        receipt = self.read(self.source.output / 'completion.json')
        protocol = self.read(self.source.output / 'protocol.json')
        audit = self.read(self.source.output / 'independent-verification.json')
        self.document['sourceMaterialization'] = {
            'directory': self.source.output.relative_to(ROOT).as_posix(),
            'files': {**receipt['files'], **{name: self.digest(self.source.output / name)
                for name in ('completion.json', 'independent-verification.json')}},
            'protocolHash': protocol['protocolHash'], 'registrationSha256': protocol['registrationSha256'],
            'independentVerifierSha256': audit['verifierSha256']}
        self.put(self.registration, self.document)
        self.enterContext(patch.object(runner, 'REGISTRATION', self.registration))
        self.enterContext(patch.object(runner, 'REGISTRATION_SHA256', self.digest(self.registration)))
        self.enterContext(patch.object(runner, 'SOURCE_WORKSPACE', self.workspace))

    digest = staticmethod(source_fixture.SupplementalSourceTests.digest)
    put = staticmethod(source_fixture.SupplementalSourceTests.put)

    @staticmethod
    def read(path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def prepare_source(self):
        source, quality = self.source, self.source.parent
        source.fixed = list(self.fixed)
        source.selected = sorted(['sh000300', *self.fixed])
        quality.fixed = list(self.fixed)
        quality.document['fixedPopulation'] = {'symbols': list(self.fixed), 'count': 3,
            'sha256': source_fixture.quality_tests.runner.sha(source_fixture.quality_tests.runner.encoded(self.fixed))}
        manifest = self.read(quality.manifest)
        manifest['inputs']['symbols'] = list(self.fixed)
        self.put(quality.manifest, manifest)
        quality.paths['sh600002'] = quality.paths['sh600001'].with_name('sh600002.day')
        for symbol in quality.paths:
            exemplar = 'sh600001' if symbol == 'sh601001' else symbol
            raw = (self.parent.source / 'vipdoc' / exemplar[:2] / 'lday' / (exemplar + '.day')).read_bytes()
            source.rewrite_source(symbol, raw)
        quality.dates = list(self.calendar)
        self.calendar_payload = {'schema': 'exchange-notice-derived-calendar-v1',
            'start': self.calendar[0], 'end': self.calendar[-1], 'dates': list(self.calendar),
            'sessionsByYear': dict(Counter(day[:4] for day in self.calendar)),
            'closureRanges': {year: [] for year in sorted({day[:4] for day in self.calendar})},
            'meaning': 'Synthetic weekdays only; not an actual exchange calendar claim.'}
        self.put(quality.calendar, self.calendar_payload)
        quality.document.update(auditStart=self.calendar[0], auditEnd=self.calendar[-1])
        quality.document['calendar']['sha256'] = self.digest(quality.calendar)
        quality.rebind()
        source.document.update(cutoff=self.calendar[-1], signalStart=self.calendar[130])
        source.document['featureCalendar']['warmupStart'] = self.calendar[0]
        source.prepare()
        source.document['expectedMaterializedFiles'] = 4
        source.document['featureCalendar'].update(expectedWarmupSessions=130, expectedTargetSessions=40)
        source.rebind()
        source.complete()
        verifier = source_fixture.module('panel_source_reference', 'scripts/verify-signal-supplemental-source.py')
        with patch.object(verifier, 'REGISTRATION', source.registration), patch.object(verifier, 'REGISTRATION_SHA', self.digest(source.registration)):
            report = verifier.verify(source.output, root=ROOT)
        self.assertTrue(report['passed'], report.get('issues'))
        verifier.save_report(source.output, report)

    def rebind(self):
        self.put(self.registration, self.document)
        runner.REGISTRATION_SHA256 = self.digest(self.registration)

    def execute(self, output=None):
        with redirect_stdout(io.StringIO()):
            return runner.execute(Namespace(workspace=self.workspace, output=output or self.output))

    def source_pins(self):
        return {path: self.digest(path) for path in self.source.output.rglob('*') if path.is_file()}

    def test_nonempty_full_panel_matches_unchanged_extract_and_preserves_source(self):
        before = self.source_pins()
        self.assertEqual(self.execute(), 0)
        self.assertEqual(before, self.source_pins())
        rows = old_panel.lines(self.output / 'features.jsonl.gz')
        expected_identity = [(day, symbol) for day in self.calendar[130:] for symbol in self.fixed]
        self.assertEqual([(row['asOfDate'], row['symbol']) for row in rows], expected_identity)
        self.assertEqual(len(rows), 120)
        self.assertEqual(len(old_panel.lines(self.output / 'daily-coverage.jsonl.gz')), 40)
        self.assertEqual(len(old_panel.lines(self.output / 'warmup-coverage.jsonl.gz')), 130)
        reference = self.root / 'old-extract-reference'
        reference.mkdir()
        identity = old_panel.runner.input_identity(self.data, self.calendar, self.document)
        old_panel.runner.extract(deepcopy(self.data), self.document, self.calendar,
            {'inputs': identity}, 'synthetic-reference-manifest', reference)
        clean = lambda records: [{key: value for key, value in row.items()
            if key not in ('sampleManifestHash', 'sourceHashes')} for row in records]
        self.assertEqual(clean(rows), clean(old_panel.lines(reference / 'features.jsonl.gz')))
        receipt = self.read(self.output / 'completion.json')
        self.assertEqual(receipt['featureRows'], 120)
        for name, info in receipt['files'].items():
            self.assertEqual(info['sha256'], self.digest(self.output / name))
        self.assertFalse(self.read(self.output / 'summary.json')['forwardReturnsCalculated'])

    def test_derived_calendar_rejects_order_duplicate_count_and_out_of_range(self):
        self.assertEqual(runner.saved_calendar(self.document), self.calendar)
        path = self.root / 'altered-calendar.json'
        mutations = (
            lambda value: value['dates'].pop(),
            lambda value: value['dates'].insert(0, value['dates'][0]),
            lambda value: value['dates'].__setitem__(slice(0, 2), value['dates'][:2][::-1]),
            lambda value: value['dates'].__setitem__(-1, '2099-01-01'),
            lambda value: value['sessionsByYear'].update({'2022': 171}),
        )
        for number, mutate in enumerate(mutations):
            with self.subTest(mutation=number):
                payload = deepcopy(self.calendar_payload)
                mutate(payload)
                self.put(path, payload)
                doc = deepcopy(self.document)
                doc['calendar'].update(path=path.relative_to(ROOT).as_posix(), sha256=self.digest(path))
                with self.assertRaises(ValueError):
                    runner.saved_calendar(doc)

    def test_exact_symbols_missing_stock_and_source_cutoff_are_enforced(self):
        changes = (
            lambda data: data['securities'][0].update(symbol='sh600999'),
            lambda data: data['securities'].pop(),
            lambda data: data['securities'][0]['bars'][-1].update(date='2099-01-01'),
        )
        for number, mutate in enumerate(changes):
            with self.subTest(mutation=number):
                data = deepcopy(self.data)
                mutate(data)
                with self.assertRaises(ValueError):
                    runner.validate_sample(data, self.document, self.calendar)

    def test_missing_materialized_stock_is_failure_never_a_smaller_panel(self):
        (self.source.output / 'tdx/vipdoc/sh/lday/sh600001.day').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.execute()
        self.assertFalse((self.output / 'completion.json').exists())

    def test_materialized_inventory_and_receipt_hashes_are_binding(self):
        alias = self.source.output / 'tdx/vipdoc/sh/lday/600001.day'
        alias.write_bytes((alias.with_name('sh600001.day')).read_bytes())
        with self.assertRaises(ValueError):
            runner.load_source_binding(self.document)
        alias.unlink()
        receipt_path = self.source.output / 'completion.json'
        receipt = self.read(receipt_path)
        receipt['files'].pop('workspace/market.sqlite3')
        self.put(receipt_path, receipt)
        self.document['sourceMaterialization']['files']['completion.json'] = self.digest(receipt_path)
        self.rebind()
        with self.assertRaises(ValueError):
            runner.load_source_binding(self.document)

    def test_failed_independent_source_verification_cannot_be_reclassified_as_passed(self):
        path = self.source.output / 'independent-verification.json'
        audit = self.read(path)
        audit.update(passed=False, failureCount=1)
        self.put(path, audit)
        self.document['sourceMaterialization']['files'][path.name] = self.digest(path)
        self.rebind()
        with self.assertRaises(ValueError):
            runner.load_source_binding(self.document)

    def test_input_output_overlap_is_rejected_without_changing_source(self):
        before = self.source_pins()
        for output in (self.source.output / 'nested-panel', self.source.output.parent):
            with self.subTest(output=str(output)), self.assertRaises(ValueError):
                self.execute(output)
        self.assertEqual(before, self.source_pins())

    def test_extraction_failure_is_preserved_and_cannot_restart(self):
        with patch.object(runner.runtime, 'extract', side_effect=RuntimeError('synthetic panel failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic panel failure'):
                self.execute()
        self.assertTrue((self.output / 'failure.json').is_file())
        self.assertFalse((self.output / 'completion.json').exists())
        before = (self.output / 'failure.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual((self.output / 'failure.json').read_bytes(), before)

    def test_frozen_feature_implementation_must_match_original_bytes(self):
        self.document['frozenFeatureImplementation']['service/signal_leadership_features.py'] = '0' * 64
        self.rebind()
        with self.assertRaises(ValueError):
            runner.registration()

    def test_independent_replay_passes_without_feature_code_and_rejects_row_tampering(self):
        self.assertEqual(self.execute(), 0)
        verifier = source_fixture.module('supplemental_panel_reference', 'scripts/verify-signal-supplemental-leadership-panel.py')
        import service.signal_leadership_features as feature_core
        with patch.object(verifier, 'REGISTRATION', self.registration), \
                patch.object(verifier, 'REGISTRATION_SHA', self.digest(self.registration)), \
                patch.object(runner.runtime, 'extract', side_effect=AssertionError('production extract forbidden')), \
                patch.object(feature_core, 'daily_features', side_effect=AssertionError('production features forbidden')):
            successful = verifier.verify(self.output, root=ROOT)
            self.assertTrue(successful['passed'], successful.get('issues'))
            self.assertEqual(successful['featureRows'], 120)
            path = self.output / 'features.jsonl.gz'
            original = old_panel.lines(path)
            receipt = self.read(self.output / 'completion.json')
            duplicate = deepcopy(original)
            duplicate[1] = deepcopy(duplicate[0])
            for label, rows in (('empty', []), ('duplicate', duplicate), ('missing-last', original[:-1])):
                with self.subTest(kind=label):
                    with gzip.open(path, 'wt', encoding='utf-8') as stream:
                        stream.write(''.join(json.dumps(row, allow_nan=False) + '\n' for row in rows))
                    receipt['files'][path.name]['sha256'] = self.digest(path)
                    self.put(self.output / 'completion.json', receipt)
                    report = verifier.verify(self.output, root=ROOT)
                    self.assertFalse(report['passed'])
                    self.assertGreater(report['failureCount'], 0)
                    self.assertNotIn('panel-output-hash', {issue.get('check') for issue in report['issues']})


if __name__ == '__main__':
    unittest.main()
