from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from tests.test_research_market_sector_account import setup, run_case
from tests.test_research_volatility_trail import set_bar
from tests.test_market_sector_risk_oracle import REFERENCE as RISK

SPEC = importlib.util.spec_from_file_location("_test_whole_system_ledger", Path(__file__).resolve().parents[1] / "scripts/verify-market-sector-stock-ledger.py")
BOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOK)


class LedgerOracleTests(unittest.TestCase):
    def test_industry_limited_entry_and_all_money_fields_replay(self):
        case = setup(((25, 0), (27, 1), (29, 2)), same_industry=True)
        engine, result = run_case(case)
        risk = RISK.daily_system_risk_reference(result, engine.checkpoint(), case[0], case[2])
        proof = BOOK.audit_ledger_and_entries(result, case[0], case[2], risk["dailyCloseRiskState"])
        self.assertTrue(proof["allFillFeesCashAndEpisodePnlReplayed"])
        corrupt = deepcopy(result)
        corrupt["research"]["system"]["closePlans"][-1]["budget"] += 100
        with self.assertRaises(ValueError):
            BOOK.audit_ledger_and_entries(corrupt, case[0], case[2], risk["dailyCloseRiskState"])

    def test_closed_episode_and_changed_cash_detected(self):
        case = setup()
        set_bar(case[0], 27, close=9.79)
        set_bar(case[0], 28, opening=9.7)
        engine, result = run_case(case)
        risk = RISK.daily_system_risk_reference(result, engine.checkpoint(), case[0], case[2])
        proof = BOOK.audit_ledger_and_entries(result, case[0], case[2], risk["dailyCloseRiskState"])
        self.assertEqual(proof["closedEpisodesChecked"], 1)
        corrupt = deepcopy(result)
        corrupt["curve"][-1]["cash"] += 1
        with self.assertRaises(ValueError):
            BOOK.audit_ledger_and_entries(corrupt, case[0], case[2], risk["dailyCloseRiskState"])


if __name__ == "__main__":
    unittest.main()
