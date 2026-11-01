from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

from tests.test_research_sector_long_system import account_case

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_test_sector_long_open_oracle',ROOT/'scripts/audit-sector-long-system.py')
V=importlib.util.module_from_spec(spec);sys.modules[spec.name]=V;spec.loader.exec_module(V)


class SectorLongOpeningOracleTests(unittest.TestCase):
    def test_long_branch_uses_common_risk_without_inventing_catchup_rejection(self):
        engine,result=account_case('longLeaderBreakout',.08,10.1,9.5)
        proof=V.audit_openings(result,engine.checkpoint(),engine.dataset,engine.system_inputs)
        self.assertEqual(proof['preparedRiskChecks'],0)
        self.assertEqual(proof['longEntriesCoveredByCommonLedger'],1)
        false=deepcopy(result)
        false['research']['system']['catchupOpeningGuards']=[{'signalId':'fake'}]
        with self.assertRaises(ValueError):V.audit_openings(false,engine.checkpoint(),engine.dataset,engine.system_inputs)

    def test_catchup_pass_and_rejection_missing_evidence_detected(self):
        for opening,floor in ((10.,9.7),(10.1,9.5)):
            engine,result=account_case('catchup',.02,opening,floor)
            proof=V.audit_openings(result,engine.checkpoint(),engine.dataset,engine.system_inputs)
            self.assertEqual(proof['preparedRiskChecks'],1)
            self.assertEqual(proof['rejectedRiskChecks'],int(opening==10.1))
            false=deepcopy(result);false['research']['system']['catchupOpeningGuards']=[]
            with self.assertRaises(ValueError):V.audit_openings(false,engine.checkpoint(),engine.dataset,engine.system_inputs)


if __name__=='__main__':unittest.main()
