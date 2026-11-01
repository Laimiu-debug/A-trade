"""Sensitivity checks for the separate raw-input and entry-clock oracle."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from tests.test_research_dual_confirmation import fixture, build

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dual_confirmation_oracle',
    ROOT / 'scripts/verify-dual-confirmation-inputs.py')
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)


class ConfirmationOracleTests(unittest.TestCase):
    def setUp(self):
        self.data, self.parents, self.parent_system = fixture(((25, 0), (25, 1)))
        self.candidate, self.schedule, self.proof = build((self.data, self.parents, self.parent_system))
        self.start, self.end = self.data['calendar'][25], self.data['calendar'][-1]

    def audit(self):
        return ORACLE.audit_confirmation(self.data, self.parent_system, self.parents,
            self.candidate, self.schedule, self.proof, self.start, self.end)

    def test_accepts_valid_source_and_reconstructs_all_parent_candidates(self):
        result = self.audit()
        self.assertTrue(result['passed'])
        self.assertEqual(result['parentOpportunitiesChecked'], 2)
        self.assertEqual(result['candidateSignalsChecked'], 2)

    def test_detects_selected_signal_when_confirmation_is_not_positive(self):
        bar = self.data['securities'][0]['bars'][26]
        bar['close'] = bar['open']
        with self.assertRaises(ValueError):
            self.audit()

    def test_detects_shifted_date_rank_refill_and_changed_risk_anchor(self):
        before = deepcopy((self.candidate, self.schedule, self.proof))
        for alteration in ('date', 'rank', 'risk'):
            with self.subTest(alteration=alteration):
                self.candidate, self.schedule, self.proof = deepcopy(before)
                if alteration == 'date':
                    self.schedule[0]['signalDate'] = self.data['calendar'][25]
                elif alteration == 'rank':
                    self.schedule[0]['originalRank'] = 5
                else:
                    self.candidate['metadata'][self.schedule[0]['signalId']]['failurePrice'] -= .1
                with self.assertRaises(ValueError):
                    self.audit()

    def test_detects_omitted_disposition_and_source_projection(self):
        self.proof['rows'].pop()
        with self.assertRaises(ValueError):
            self.audit()
        self.candidate, self.schedule, self.proof = build((self.data, self.parents, self.parent_system))
        self.data['securities'].pop(0)
        with self.assertRaises(ValueError):
            self.audit()

    def test_detects_wrong_opening_clock(self):
        trade = {'side': 'buy', 'signalId': self.schedule[0]['signalId'],
                 'date': self.data['calendar'][27]}
        result = {'trades': [trade]}
        self.assertEqual(ORACLE.audit_fill_clock(result, self.data, self.candidate)['buyFillClocksChecked'], 1)
        trade['date'] = self.data['calendar'][26]
        with self.assertRaises(ValueError):
            ORACLE.audit_fill_clock(result, self.data, self.candidate)


if __name__ == '__main__':
    unittest.main()
