from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from tests.test_research_prepared_base_system import account_case
from tests.test_research_market_sector_account import setup

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_test_prepared_base_open_oracle',ROOT/'scripts/audit-prepared-base-system.py')
V=importlib.util.module_from_spec(spec);sys.modules[spec.name]=V;spec.loader.exec_module(V)


class PreparedOpeningOracleTests(unittest.TestCase):
    def test_passed_opening_and_corrupt_evidence_detected(self):
        engine,result=account_case();data=setup()[0]
        proof=V.audit_openings(result,engine.checkpoint(),data,engine.system_inputs)
        self.assertEqual(proof['preparedRiskChecks'],1)
        bad=deepcopy(result);bad['research']['system']['preparedBaseOpeningGuards']=[]
        with self.assertRaises(ValueError):V.audit_openings(bad,engine.checkpoint(),data,engine.system_inputs)
        bad=deepcopy(result);bad['research']['system']['preparedBaseOpeningGuards'][0]['failurePrice']+=.1
        with self.assertRaises(ValueError):V.audit_openings(bad,engine.checkpoint(),data,engine.system_inputs)

    def test_both_rejected_openings_and_missing_rejection_detected(self):
        for opening,floor in ((10.1,9.5),(9.8,9.7)):
            engine,result=account_case(opening,floor);data=setup()[0]
            # Exact same synthetic source modification as the executed case.
            data['securities'][0]['bars'][26]['open']=opening
            self.assertEqual(V.audit_openings(result,engine.checkpoint(),data,engine.system_inputs)['rejectedRiskChecks'],1)
            bad=deepcopy(result);bad['research']['system']['preparedBaseOpeningGuards'][0].update(status='passed',reason=None)
            with self.assertRaises(ValueError):V.audit_openings(bad,engine.checkpoint(),data,engine.system_inputs)


if __name__=='__main__':unittest.main()
