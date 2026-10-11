"""Configuration contracts for instances, ordered composition DAGs and allocation."""
import copy
import unittest

from service.portfolio_config import default_config, normalize_config, normalize_selection


def nested_config():
    return {"strategies": [
        {"id": "fast", "kind": "momentum", "params": {"lookback": 3}, "pool": "base"},
        {"id": "slow", "kind": "momentum", "params": {"lookback": 40}, "pool": "trend"}],
        "combinations": [
            {"id": "either", "method": "union", "children": [{"id": "fast"}, {"id": "slow"}]},
            {"id": "nested", "method": "weighted", "children": [{"id": "either", "weight": 2}, {"id": "slow"}], "threshold": .3}],
        "allocation": {"mode": "single", "strategyIds": ["nested"]}}


class PortfolioConfigTests(unittest.TestCase):
    def test_defaults_round_trip_without_inventing_research_dates(self):
        config = default_config()
        self.assertIsNone(config["startDate"])
        self.assertIsNone(config["endDate"])
        self.assertEqual(config["lookbackDays"], 39)
        self.assertEqual(config["rebalanceMode"], "risk")
        self.assertEqual(config["maxHoldingDays"], 0)
        self.assertEqual(normalize_config(config), config)
        config["study"] = {}
        self.assertEqual(normalize_config(config)["study"], {})

    def test_same_kind_instances_and_nested_single_composition_remain_distinct(self):
        raw = nested_config()
        before = copy.deepcopy(raw)
        config = normalize_config(raw)
        self.assertEqual(raw, before)
        self.assertEqual(config["allocation"], {"mode": "single", "strategyIds": ["nested"]})
        self.assertEqual([row["params"]["lookback"] for row in config["strategies"]], [3, 40])
        self.assertEqual([row["pool"] for row in config["strategies"]], ["base", "trend"])
        self.assertEqual(config["combinations"][1]["children"][0], {"id": "either", "weight": 2})
        self.assertEqual(normalize_config(config), config)
        raw["strategies"][0]["params"]["lookback"] = 99
        self.assertEqual(config["strategies"][0]["params"]["lookback"], 3)
        config["strategies"][0]["params"]["lookback"] = 5
        self.assertEqual(config["strategies"][1]["params"]["lookback"], 40)

    def test_duplicate_dangling_self_cycle_and_forward_edges_are_rejected(self):
        base = nested_config()
        cases = []
        duplicate = copy.deepcopy(base)
        duplicate["strategies"][1]["id"] = "fast"
        cases.append(duplicate)
        for child in ("missing", "either", "nested"):
            invalid = copy.deepcopy(base)
            invalid["combinations"][0]["children"][0]["id"] = child
            cases.append(invalid)
        repeated_child = copy.deepcopy(base)
        repeated_child["combinations"][0]["children"][1]["id"] = "fast"
        cases.append(repeated_child)
        for invalid in cases:
            with self.subTest(config=invalid):
                with self.assertRaises(ValueError):
                    normalize_config(invalid)

    def test_allocation_and_unknown_or_nonfinite_parameters_fail_closed(self):
        cases = [{"allocation": {"mode": "rotation"}}, {"allocation": {"mode": "single"}},
                 {"allocation": {"strategyIds": ["missing"]}}, {"lookbackDays": True},
                 {"topN": float("nan")}, {"maxPositions": float("inf")},
                 {"stopLossPtc": .1}, {"poolSettings": {"structure": {"minDrawdown": .4, "maxDrawdown": .2}}},
                 {"strategies": [{"id": "one", "kind": "momentum", "params": {"lookbak": 10}}]}]
        for invalid in cases:
            with self.subTest(config=invalid):
                with self.assertRaises(ValueError):
                    normalize_config(invalid)

    def test_explicit_universe_is_not_limited_by_batch_size(self):
        symbols = [f"sh{600000 + i:06d}" for i in range(129)]
        selected = normalize_selection({"mode": "explicit", "symbols": symbols, "batchSize": 1})
        self.assertEqual(selected["symbols"], symbols)
        self.assertEqual(len(selected["symbols"]), 129)
        self.assertEqual(selected["batchSize"], 1)


if __name__ == "__main__":
    unittest.main()
