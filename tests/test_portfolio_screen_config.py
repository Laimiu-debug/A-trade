"""Pool screening validates only dates and pool rules, independently of trading editors."""
from copy import deepcopy
import unittest

from service.portfolio_config import normalize_config, normalize_screen_config


class PoolScreenConfigTests(unittest.TestCase):
    def test_minimal_pool_only_request_has_stable_internal_compatibility_config(self):
        raw = {"endDate": "2026-10-09", "lookbackDays": 130, "universe": {"minAmount20": 25000000},
               "poolSettings": {"trend": {"window": 30}, "combined": {"pools": ["volume"], "mode": "union"}}}
        before = deepcopy(raw)
        config = normalize_screen_config(raw)
        self.assertEqual(raw, before)
        self.assertEqual(config["endDate"], raw["endDate"])
        self.assertEqual(config["universe"]["minAmount20"], 25000000)
        self.assertEqual(config["poolSettings"]["combined"], {"pools": ["volume"], "mode": "union"})
        self.assertEqual(normalize_screen_config(config), config)
        self.assertEqual(normalize_config(config), config, "legacy storage consumers still accept normalized metadata")
        config["poolSettings"]["trend"]["window"] = 50
        self.assertEqual(raw, before)

    def test_user_strategy_funds_and_allocation_values_cannot_gate_or_change_pool_config(self):
        pools = {"universe": {"minListingDays": 0}, "poolSettings": {"volume": {"minRatio": 2}}}
        expected = normalize_screen_config(pools)
        variants = [
            {"strategies": [], "combinations": [], "allocation": {"mode": "single", "strategyIds": []}},
            {"strategies": [{"kind": "forceGoldenCross", "params": {"invalid": object()}}], "initialCapital": None},
            {"strategies": "unfinished", "allocation": None, "combinations": [{"unknown": True}]},
            {"topN": 1.5, "maxPositions": -100, "rotation": "unfinished", "study": [], "weighting": "unknown"},
        ]
        for values in variants:
            with self.subTest(values=list(values)):
                self.assertEqual(normalize_screen_config({**pools, **values}), expected)

    def test_invalid_pool_rules_dates_and_unknown_fields_are_still_rejected(self):
        for raw in (["invalid"], {"lookbackDays": 1.5}, {"endDate": "2026-13-09"},
                    {"universe": {"minAmount20": -1}}, {"poolSettings": {"trend": {"window": 1}}},
                    {"poolSettings": {"combined": {"pools": ["momentum"]}}}, {"poolSetings": {}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_screen_config(raw)

    def test_backtests_continue_to_validate_strategy_and_capital_errors(self):
        for raw in ({"strategies": []}, {"allocation": {"strategyIds": []}}, {"initialCapital": None}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_config(raw)


if __name__ == "__main__":
    unittest.main()
