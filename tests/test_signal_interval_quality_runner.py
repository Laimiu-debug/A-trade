"""Synthetic integration tests for immutable interval-quality evidence."""
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module('interval_runner_tests_target','audit-signal-interval-quality.py')


def raw_record(day, *, amount=200000., volume=10000, low=900):
    return struct.pack('<IIIIIfII',int(day.replace('-','')),1000,1100,low,1050,amount,volume,0)


class IntervalQualityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interval-fixture-',dir=ROOT/'artifacts/strategy-search')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base/'output'
        self.dates = ['2024-01-02','2024-01-03','2025-01-02']
        self.fixed = ['sh600001','sz000001']
        self.paths = {}
        self.inputs = {}
        for symbol in ['sh000300','sh600001','sh601001','sz000001']:
            path = self.base/'tdx/vipdoc'/symbol[:2]/'lday'/(symbol+'.day')
            path.parent.mkdir(parents=True,exist_ok=True)
            records = [raw_record(d) for d in self.dates]
            if symbol == 'sh600001':
                records[1] = raw_record(self.dates[1],volume=0)
            if symbol == 'sz000001':
                records.pop(1)
            # Future bad OHLC is archived, but cannot contaminate interval content validity.
            path.write_bytes(b''.join(records)+raw_record('2026-01-05',low=0))
            self.paths[symbol] = path
            self.inputs[str(path)] = runner.file_sha(path)
        self.calendar = self.base/'calendar.json'
        self.manifest = self.base/'manifest.json'
        self.registration = self.base/'registration.json'
        self.put(self.calendar,{'start':'2024-01-01','end':'2025-12-31','dates':self.dates,
            'sessionsByYear':{'2024':2,'2025':1}})
        self.put(self.manifest,{'inputs':{'symbols':self.fixed,'benchmark':{'symbol':'sh000300'}},
            'physicalSourceHashes':self.inputs})
        self.document = {
            'schema':'supplemental-interval-quality-registration-v1',
            'auditStart':'2024-01-01','auditEnd':'2025-12-31',
            'calendar':{'path':self.calendar.relative_to(ROOT).as_posix(),'sha256':runner.file_sha(self.calendar)},
            'sourceManifest':{'path':self.manifest.relative_to(ROOT).as_posix(),'sha256':runner.file_sha(self.manifest)},
            'fixedPopulation':{'symbols':self.fixed,'count':2,'sha256':runner.sha(runner.encoded(self.fixed))},
            'frozenCandidate':{'path':'docs/signal-market-repair-resilience-price-proxy-v1.json',
                'sha256':runner.file_sha(ROOT/'docs/signal-market-repair-resilience-price-proxy-v1.json')},
            'exposure':{'documentSha256':runner.file_sha(ROOT/'docs/strategy-backtesting.md'),'pristineHoldout':False},
            'certificationGaps':['synthetic-fixture'],
            'states':list(runner.STATES),'returnsCalculated':False,'certified':False,'targetAchieved':False,
        }
        self.put(self.registration,self.document)
        self.addCleanup(patch.stopall)
        patch.object(runner,'REGISTRATION',self.registration).start()
        patch.object(runner,'REGISTRATION_SHA',runner.file_sha(self.registration)).start()

    def put(self,path,value):
        path.write_bytes(runner.encoded(value)+b'\n')

    def rebind(self):
        self.document['sourceManifest']['sha256'] = runner.file_sha(self.manifest)
        self.put(self.registration,self.document)
        runner.REGISTRATION_SHA = runner.file_sha(self.registration)

    def complete(self):
        runner.freeze(self.output)
        return runner.run(self.output)

    def test_complete_preserves_all_groups_and_missing_days(self):
        result = self.complete()
        self.assertEqual(result['sourceGroupCounts'],{'benchmark':1,'fixed':2,'otherPinned':1})
        counts = result['byGroupYear']['fixed']['2024']
        self.assertEqual(counts,{'validPositiveActivity':2,'validZeroVolumeOrAmount':1,
            'invalidRecord':0,'duplicateDate':0,'missingRecord':1})
        self.assertFalse(result['returnsCalculated'])
        self.assertTrue(result['benchmarkCalendarExactlyObserved'])
        self.assertTrue((self.output/'completion.json').is_file())

    def test_missing_physical_file_is_retained(self):
        self.paths['sz000001'].unlink()
        result = self.complete()
        self.assertEqual(result['unavailableSources'],['sz000001'])
        self.assertEqual(result['byGroupYear']['fixed']['2024']['missingRecord'],2)
        self.assertEqual(result['byGroupYear']['fixed']['2025']['missingRecord'],1)

    def test_hash_drift_is_terminal_freeze_failure(self):
        with self.paths['sh600001'].open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(ValueError,'Physical source changed'):
            runner.freeze(self.output)
        self.assertTrue((self.output/'freeze-failure.json').exists())
        self.assertFalse((self.output/'protocol.json').exists())
        with self.assertRaises(FileExistsError):
            runner.freeze(self.output)

    def test_ambiguous_alias_is_not_silently_chosen(self):
        alias = self.paths['sh600001'].with_name('600001.day')
        alias.write_bytes(self.paths['sh600001'].read_bytes())
        manifest = runner.read(self.manifest)
        manifest['physicalSourceHashes'][str(alias)] = runner.file_sha(alias)
        self.put(self.manifest,manifest)
        self.rebind()
        with self.assertRaisesRegex(ValueError,'Ambiguous physical files'):
            runner.freeze(self.output)

    def test_archive_is_immutable_source_after_freezing(self):
        runner.freeze(self.output)
        for path in self.paths.values():
            path.unlink()
        result = runner.run(self.output)
        self.assertEqual(result['unavailableSources'],[])
        self.assertTrue(result['benchmarkCalendarExactlyObserved'])

    def test_archive_drift_rejected_before_any_evaluation(self):
        runner.freeze(self.output)
        with (self.output/'input-sources.zip').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'Archive changed'):
            runner.run(self.output)
        self.assertFalse((self.output/'audit-attempt.json').exists())

    def test_completion_is_verify_only_and_detects_drift(self):
        self.complete()
        original = (self.output/'completion.json').read_bytes()
        result = runner.run(self.output)
        self.assertTrue(result['alreadyCompleted'])
        self.assertEqual((self.output/'completion.json').read_bytes(),original)
        with (self.output/'summary.json').open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(ValueError,'Completed output changed'):
            runner.run(self.output)

    def test_incomplete_receipt_is_never_accepted(self):
        self.complete()
        receipt = runner.read(self.output/'completion.json')
        receipt['files'] = {}
        self.put(self.output/'completion.json',receipt)
        with self.assertRaisesRegex(ValueError,'Different completed protocol'):
            runner.run(self.output)

    def test_embedded_calendar_cannot_be_rehashed_to_replace_registration(self):
        runner.freeze(self.output)
        protocol = runner.read(self.output/'protocol.json')
        protocol['calendar']['dates'].pop()
        protocol['protocolHash'] = runner.sha(runner.encoded({k:v for k,v in protocol.items() if k != 'protocolHash'}))
        self.put(self.output/'protocol.json',protocol)
        with self.assertRaisesRegex(ValueError,'Embedded registration/calendar'):
            runner.run(self.output)

    def test_extra_benchmark_date_is_not_exact_calendar_match(self):
        path = self.paths['sh000300']
        path.write_bytes(b''.join(raw_record(d) for d in ['2024-01-02','2024-01-03','2024-01-06','2025-01-02']))
        manifest = runner.read(self.manifest)
        manifest['physicalSourceHashes'][str(path)] = runner.file_sha(path)
        self.put(self.manifest,manifest)
        self.rebind()
        result = self.complete()
        self.assertTrue(result['benchmarkCalendarCompleteValidContent'])
        self.assertFalse(result['benchmarkCalendarExactlyObserved'])

    def test_failed_audit_never_restarts_implicitly(self):
        runner.freeze(self.output)
        original_scan = runner.scan_daily
        def fail_after_validation(raw,*args,**kwargs):
            if raw:
                raise ValueError('fixture failure')
            return original_scan(raw,*args,**kwargs)
        with patch.object(runner,'scan_daily',side_effect=fail_after_validation):
            with self.assertRaisesRegex(ValueError,'fixture failure'):
                runner.run(self.output)
        self.assertTrue((self.output/'audit-failure.json').exists())
        with self.assertRaises(FileExistsError):
            runner.run(self.output)

    def test_independent_verifier_matches_and_detects_changed_quality(self):
        self.complete()
        verifier = module('independent_interval_test_target','verify-signal-interval-quality.py')
        with patch.object(verifier,'REGISTRATION',self.registration),patch.object(verifier,'REGISTRATION_SHA',runner.REGISTRATION_SHA):
            result = verifier.verify(self.output,root=ROOT)
            self.assertTrue(result['passed'],str(result.get('failures'))[:2000])
            with gzip.open(self.output/'symbols.jsonl.gz','rt',encoding='utf-8') as stream:
                rows = [json.loads(line) for line in stream]
            rows[0]['audit']['records'][0]['state'] = 'missingRecord'
            with gzip.open(self.output/'symbols.jsonl.gz','wt',encoding='utf-8') as stream:
                stream.write(''.join(json.dumps(r)+'\n' for r in rows))
            receipt = runner.read(self.output/'completion.json')
            receipt['files']['symbols.jsonl.gz'] = runner.file_sha(self.output/'symbols.jsonl.gz')
            self.put(self.output/'completion.json',receipt)
            result = verifier.verify(self.output,root=ROOT)
            self.assertFalse(result['passed'])


if __name__ == '__main__':
    unittest.main()
