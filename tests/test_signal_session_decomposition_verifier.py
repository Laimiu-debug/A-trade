"""Independent reference and temporary completed-run audits; no real data."""
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

import test_signal_session_decomposition_runner as fixture_module

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'scripts/verify-signal-session-decomposition-result.py'


def module():
    spec = importlib.util.spec_from_file_location('independent_session_decomposition_test', SOURCE)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class SessionDecompositionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = module()

    @contextmanager
    def completed_fixture(self):
        f = fixture_module.SessionDecompositionRunnerTests()
        f.setUp()
        try:
            # This synthetic eligibility audit explicitly archives its own
            # contract text; materialize the identical original for byte checks.
            (f.root/'synthetic-contract.txt').write_bytes(b'Eligibility fixture only; no actual market audit.')
            self.assertEqual(f.run_complete(), 0)
            with patch.object(self.verifier, 'REGISTRATION', f.registration), \
                    patch.object(self.verifier, 'REGISTRATION_SHA256', f.registration_hash):
                yield f
        finally:
            f.doCleanups()

    def verify(self, fixture):
        with redirect_stdout(io.StringIO()):
            return self.verifier.verify(fixture.output, root=fixture.root)

    def resign(self, fixture):
        """Deliberate test-only forged receipt to reach semantic verification."""
        receipt = fixture.runner.read_json(fixture.attempt()/'completion.json')
        for item in receipt['files'].values():
            item['sha256'] = fixture.runner.sha(fixture.output/item['path'])
        fixture_module.write(fixture.attempt()/'completion.json', receipt)
        path = fixture.output/'events.jsonl'
        events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        events[-1]['receipt'] = receipt
        path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in events), encoding='utf-8')

    def test_independent_component_formula_window_and_signed_shares(self):
        days = [(date(2022,1,1)+timedelta(days=i)).isoformat() for i in range(21)]
        raw = []
        previous = 10.
        for i, day in enumerate(days):
            close = previous*1.02 if i else previous
            raw.append({'date': day, 'open': previous, 'close': close, 'high': close,
                'low': previous, 'volume': 1000, 'amount': 100_000_000, 'priceBasisId': 'raw-unadjusted'})
            previous = close
        eligibility = {'eligible': True, 'reason': 'eligible', 'unknown': []}
        intraday = self.verifier.reference_path('sh600001', days[-1], days, raw, eligibility)
        self.assertEqual(intraday['kind'], 'intraday')
        self.assertEqual(intraday['features']['positiveIntradaySessions'], 20)
        self.assertAlmostEqual(intraday['features']['intradaySignedShare'], 1)
        overnight_raw = deepcopy(raw)
        for bar in overnight_raw:
            bar['open'] = bar['close']
        overnight_raw[-1]['amount'] = None
        overnight = self.verifier.reference_path('sh600002', days[-1], days, overnight_raw, eligibility)
        self.assertEqual(overnight['kind'], 'overnight')
        self.assertEqual(overnight['features']['positiveOvernightSessions'], 20)
        self.assertEqual(overnight['features']['prior20MeanAmount'], 100_000_000)
        selected = self.verifier.reference_selection(days[-1], [intraday['signal'], overnight['signal']])
        self.assertEqual(selected['matchedCount'], 1)
        self.assertFalse(selected['signals'][1]['selectedTop5'])
        missing = deepcopy(raw)
        missing[4] = None
        self.assertEqual(self.verifier.reference_path('sh600001', days[-1], days, missing, eligibility)['kind'], 'unevaluable')

    def test_nonempty_original_pairs_and_saved_audit_allow_verify_only_reuse(self):
        with self.completed_fixture() as f:
            report = self.verify(f)
            self.assertTrue(report['passed'], report)
            self.assertEqual(report['evaluatedSecuritySessions'], 360)
            self.assertEqual(report['pairAccounting']['anchorSlots'], 200)
            self.assertEqual(report['pairAccounting']['matchedPairs'], 120)
            self.assertEqual([g['originalMatchedSignals'] for g in report['matchedAtSignalHorizonArithmetic']], [120, 120])
            self.assertTrue(all(len(g['horizons']) == 7 for g in report['matchedAtSignalHorizonArithmetic']))
            self.verifier.save_report(f.output, report)
            with patch.object(f.runner, 'load_dataset', side_effect=AssertionError('no second execution')):
                self.assertEqual(f.execute(), 0)

    def test_rehashed_matching_and_pair_dispositions_are_independently_rejected(self):
        with self.completed_fixture() as f:
            selection = fixture_module.lines(f.attempt()/'selection.jsonl.gz')
            original = deepcopy(selection)
            selection[0]['pairs'][0]['controlSymbol'] = f.symbols[-1]
            fixture_module.write_lines(f.attempt()/'selection.jsonl.gz', selection)
            self.resign(f)
            report = self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('independent-greedy-selection', {i['check'] for i in report['issues']})
            fixture_module.write_lines(f.attempt()/'selection.jsonl.gz', original)
            pairs = fixture_module.lines(f.attempt()/'pair-outcomes.jsonl.gz')
            pairs[0]['controlDisposition']['status'] = 'unmatched'
            fixture_module.write_lines(f.attempt()/'pair-outcomes.jsonl.gz', pairs)
            self.resign(f)
            report = self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('independent-original-pair-dispositions', {i['check'] for i in report['issues']})

    def test_full_population_missing_duplicate_and_extra_predicates_are_rejected(self):
        with self.completed_fixture() as f:
            original = fixture_module.lines(f.attempt()/'predicates.jsonl.gz')
            cases = [original[:-1], [original[0], original[0], *original[2:]], [*original, original[-1]]]
            for rows in cases:
                with self.subTest(rows=len(rows)):
                    fixture_module.write_lines(f.attempt()/'predicates.jsonl.gz', rows)
                    self.resign(f)
                    report = self.verify(f)
                    self.assertFalse(report['passed'])
                    self.assertTrue(any(i['check'] in ('independent-raw-predicate-row', 'output-exact-EOF:predicates')
                                        for i in report['issues']), report)

    def test_rehashed_horizon_numerator_is_not_accepted_as_complete_success(self):
        with self.completed_fixture() as f:
            result = f.result()
            result['groups'][0]['horizons'][0]['proxies']['nextOpenNet30']['hits'] += 1
            with gzip.open(f.attempt()/'result.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(result, stream)
            summary = f.runner.read_json(f.attempt()/'summary.json')
            summary['groups'] = deepcopy(result['groups'])
            fixture_module.write(f.attempt()/'summary.json', summary)
            self.resign(f)
            report = self.verify(f)
            self.assertFalse(report['passed'])
            self.assertIn('target-fraction', {i['check'] for i in report['issues']})


if __name__ == '__main__':
    unittest.main()
