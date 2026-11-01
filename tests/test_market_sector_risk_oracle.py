from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from tests.test_research_market_sector_account import setup, run_case
from tests.test_research_volatility_trail import set_bar

SPEC = importlib.util.spec_from_file_location("_test_whole_system_risk_oracle", Path(__file__).resolve().parents[1] / "scripts/verify-market-sector-stock-risk.py")
REFERENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFERENCE)


class SystemRiskOracleTests(unittest.TestCase):
    def test_rebuilds_structural_exit_and_rejects_changed_evidence(self):
        case = setup()
        set_bar(case[0], 27, close=9.79)
        set_bar(case[0], 28, opening=9.7)
        engine, result = run_case(case)
        proof = REFERENCE.daily_system_risk_reference(result, engine.checkpoint(), case[0], case[2])
        self.assertEqual(proof["exitReasons"]["systemStructuralFailure"], 1)
        corrupt = deepcopy(result)
        corrupt["research"]["system"]["riskObservations"][0]["failureThreshold"] *= .99
        with self.assertRaises(ValueError):
            REFERENCE.daily_system_risk_reference(corrupt, engine.checkpoint(), case[0], case[2])

    def test_reconstructs_persistent_market_exit_and_detects_missing_queue(self):
        case = setup()
        case[2]["daily"][26]["market"].update(admission=False, riskExit=True, crisisCount=3)
        set_bar(case[0], 27, volume=0)
        engine, result = run_case(case)
        proof = REFERENCE.daily_system_risk_reference(result, engine.checkpoint(), case[0], case[2])
        self.assertEqual(proof["exitReasons"]["systemMarketRisk"], 1)
        corrupt = deepcopy(result)
        corrupt["decisions"] = [r for r in corrupt["decisions"] if not (r["action"] == "sell" and r["status"] == "queued")]
        with self.assertRaises(ValueError):
            REFERENCE.daily_system_risk_reference(corrupt, engine.checkpoint(), case[0], case[2])


if __name__ == "__main__":
    unittest.main()
