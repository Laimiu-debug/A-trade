"""Prefix isolation and frozen job contract, without a market/account run."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dual_confirmation_runner", ROOT / "scripts/run-dual-confirmation-review.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fixture():
    days = ["2025-12-30", "2025-12-31", "2026-01-05"]
    bars = [{"date": d, "open": 10., "high": 11., "low": 9., "close": 10., "volume": 100,
             "amount": 1000.} for d in days]
    action = {"id": "known-pay-later", "announcementDate": "2025-12-30", "recordDate": "2025-12-31",
              "exDate": "2026-01-05", "cashPayDate": "2026-01-06", "cashNetPerShare": 1.}
    data = {"calendar": days, "calendarSource": "synthetic-independent", "fingerprint": "original",
            "membershipByDate": {d: ["sh600001"] for d in days},
            "securities": [{"symbol": "sh600001", "bars": bars,
                "tradeStatusByDate": {d: True for d in days}, "forceSeed": {"unused": True},
                "corporateActions": [action, {**action, "id": "not-yet-announced", "announcementDate": "2026-01-05"}]},
                {"symbol": "sh600002", "bars": [dict(bars[-1])]}],
            "benchmark": {"symbol": "sh000300", "bars": deepcopy(bars)}}
    signals = [{"signalId": "old:1", "strategyId": "marketSectorStockV1", "symbol": "sh600001",
                "signalDate": "2025-12-31", "originalRank": 3},
               {"signalId": "old:2", "strategyId": "marketSectorStockV1", "symbol": "sh600001",
                "signalDate": "2026-01-05", "originalRank": 2}]
    system = {"daily": [{"date": d, "snapshotId": "s1" if d <= "2025-12-31" else "s2"} for d in days],
              "sectorMaps": {"s1": {"sh600001": "sector1"}, "s2": {"sh600001": "sector2"}},
              "metadata": {row["signalId"]: deepcopy(row) for row in signals}}
    return data, system, signals


class PrefixTests(unittest.TestCase):
    def test_prefix_keeps_known_unpaid_rights_and_removes_future_prices_states(self):
        data, system, signals = fixture()
        original = deepcopy((data, system, signals))
        view, context, selected, proof = runner.prefix_inputs(data, system, signals, "2025-12-31", "2025-12-31")
        self.assertEqual((data, system, signals), original)
        self.assertEqual(view["calendar"], ["2025-12-30", "2025-12-31"])
        stock = view["securities"][0]
        self.assertEqual(stock["bars"][-1]["date"], "2025-12-31")
        self.assertEqual(set(stock["tradeStatusByDate"]), set(view["calendar"]))
        self.assertEqual(set(view["membershipByDate"]), set(view["calendar"]))
        self.assertEqual(view["benchmark"]["bars"][-1]["date"], "2025-12-31")
        self.assertNotIn("forceSeed", stock)
        self.assertEqual(len(stock["corporateActions"]), 1)
        self.assertEqual(stock["corporateActions"][0]["cashPayDate"], "2026-01-06")
        self.assertEqual(selected, [signals[0]])
        self.assertEqual(set(context["metadata"]), {"old:1"})
        self.assertEqual(set(context["sectorMaps"]), {"s1"})
        self.assertEqual(proof["emptyPrefixSymbolsOmitted"], ["sh600002"])
        self.assertNotEqual(view["fingerprint"], "original")

    def test_parent_opportunity_without_prefix_bars_fails_instead_of_disappearing(self):
        data, system, signals = fixture()
        signals[0]["symbol"] = "sh600002"
        with self.assertRaisesRegex(ValueError, "no prefix history"):
            runner.prefix_inputs(data, system, signals, "2025-12-31", "2025-12-31")

    def test_signal_before_period_does_not_cross_into_confirmation(self):
        data, system, signals = fixture()
        _, _, selected, proof = runner.prefix_inputs(data, system, signals, "2026-01-05", "2026-01-05")
        self.assertEqual(selected, [signals[1]])
        self.assertEqual(proof["parentOpportunityCount"], 1)
        self.assertFalse(proof["newSegmentConsumed"])

    def test_no_later_segment_or_output_escape_is_allowed(self):
        data, system, signals = fixture()
        with self.assertRaises(ValueError):
            runner.prefix_inputs(data, system, signals, "2026-10-16", "2026-10-27")
        for path in (ROOT / "docs/forbidden.json", runner.OUT / "../escape.json"):
            with self.assertRaises(ValueError):
                runner.output_path(path)
        self.assertEqual(runner.output_path(runner.OUT / "inputs/test.json"), runner.OUT / "inputs/test.json")

    def test_registered_hypothesis_contract_matches_exact_ten_case_budget(self):
        plan = runner.read(runner.HYPOTHESIS)
        runner.validate_hypothesis(plan)
        for key, value in (("initialCapitalYuan", 1000000), ("normalSlippageBpsEachSide", 0),
                           ("stressSlippageBpsEachSide", 10), ("resetAtPeriodStart", False)):
            changed = deepcopy(plan)
            changed["accounts"][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                runner.validate_hypothesis(changed)


if __name__ == "__main__":
    unittest.main()
