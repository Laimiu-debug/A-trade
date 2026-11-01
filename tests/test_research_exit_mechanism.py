"""Synthetic algebra, execution and maturity boundaries; no saved accounts read."""
from copy import deepcopy
import math
import unittest

from service.research_exit_mechanism import COMPONENTS, GROUPS, aggregate, pair_episode


def closed(capital=100., pnl=20., **overrides):
    return {"entered": True, "entryDate": "2024-01-03", "entryPrice": 10.,
            "exitDate": "2024-01-10", "positionState": "closed", "investedAmount": capital,
            "closedPnlAmount": pnl, "openRealizedPnlAmount": 0., "openUnrealizedPnlAmount": 0.,
            "holdingSessions": 6, **overrides}


def opened(capital=100., realized=10., floating=-30., **overrides):
    values = {"exitDate": None, "positionState": "openAtCutoff",
              "openRealizedPnlAmount": realized, "openUnrealizedPnlAmount": floating, **overrides}
    return closed(capital, 0., **values)


def absent():
    return {"entered": False, "entryDate": None, "entryPrice": None, "exitDate": None,
            "positionState": "notEntered", "investedAmount": 0., "closedPnlAmount": 0.,
            "openRealizedPnlAmount": 0., "openUnrealizedPnlAmount": 0., "holdingSessions": None}


class ExitMechanismTests(unittest.TestCase):
    def assert_bridge(self, row):
        self.assertAlmostEqual(math.fsum(row[key] for key in COMPONENTS), row["deltaPnl"])
        self.assertAlmostEqual(row["candidatePnl"] - row["controlPnl"], row["deltaPnl"])

    def test_hand_calculated_symmetric_capital_and_return_terms(self):
        row = pair_episode(closed(100, 20), closed(200, 60))
        self.assertEqual(row["group"], "bothSameEntry")
        self.assertAlmostEqual(row["returnProfileComponent"], 15)
        self.assertAlmostEqual(row["committedCapitalComponent"], 25)
        self.assertEqual((row["controlReturnPct"], row["candidateReturnPct"]), (20, 30))
        self.assert_bridge(row)

    def test_negative_returns_and_swapping_sides_reverse_components(self):
        left, right = closed(100, -20), closed(50, -5)
        a, b = pair_episode(left, right), pair_episode(right, left)
        self.assertAlmostEqual(a["returnProfileComponent"], 7.5)
        self.assertAlmostEqual(a["committedCapitalComponent"], 7.5)
        for key in (*COMPONENTS, "deltaPnl"):
            self.assertAlmostEqual(a[key], -b[key])
        self.assert_bridge(a)

    def test_single_side_losses_and_neither_have_no_invented_return(self):
        control = pair_episode(closed(pnl=-15), absent())
        candidate = pair_episode(absent(), closed(pnl=-15))
        neither = pair_episode(absent(), absent())
        self.assertEqual((control["controlOnlyComponent"], candidate["candidateOnlyComponent"]), (15, -15))
        self.assertIsNone(control["candidateReturnPct"])
        self.assertIsNone(candidate["controlReturnPct"])
        self.assertEqual(neither["group"], "neither")
        for row in (control, candidate, neither):
            self.assertEqual(row["returnProfileComponent"], 0)
            self.assertEqual(row["committedCapitalComponent"], 0)
            self.assertIsNone(row["holdingDeltaSessions"])
            self.assert_bridge(row)

    def test_same_entry_requires_exact_date_and_actual_price(self):
        for other in (closed(entryDate="2024-01-04"), closed(entryPrice=10.000000000000002)):
            row = pair_episode(closed(), other)
            self.assertEqual(row["group"], "bothDifferentEntry")
            self.assertIsNone(row["closedPairTiming"])
            self.assertIsNone(row["baselineOutcome"])
            self.assert_bridge(row)

    def test_open_episode_uses_realized_plus_remaining_unrealized(self):
        a = pair_episode(closed(100, 20), opened(100, 10, -30, holdingSessions=None))
        self.assertEqual(a["candidatePnl"], -20)
        self.assertEqual(a["candidateReturnPct"], -20)
        self.assertEqual(a["maturity"], "closed__openAtCutoff")
        self.assertIsNone(a["holdingDeltaSessions"])
        self.assertIsNone(a["closedPairTiming"])
        b = pair_episode(opened(100, 10, -30), opened(200, -5, 55))
        self.assertEqual((b["controlPnl"], b["candidatePnl"]), (-20, 50))
        self.assert_bridge(a)
        self.assert_bridge(b)

    def test_closed_pair_timing_and_original_outcome(self):
        for end, expected in (("2024-01-09", "earlier"), ("2024-01-10", "same"), ("2024-01-11", "later")):
            for pnl, outcome in ((20, "win"), (-20, "loss"), (0, "zero")):
                row = pair_episode(closed(pnl=pnl), closed(exitDate=end, holdingSessions=9))
                self.assertEqual((row["closedPairTiming"], row["baselineOutcome"], row["holdingDeltaSessions"]),
                                 (expected, outcome, 3))

    def test_invalid_financial_values_are_rejected_not_zero_filled(self):
        for field, value in (("investedAmount", 0), ("investedAmount", -1), ("investedAmount", True),
                             ("investedAmount", float("nan")), ("entryPrice", 0), ("entryPrice", float("inf")),
                             ("closedPnlAmount", None), ("closedPnlAmount", False)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                pair_episode(closed(**{field: value}), closed())
        with self.assertRaises(ValueError):
            pair_episode(opened(floating=float("nan")), closed())

    def test_inconsistent_maturity_dates_and_missing_fields_rejected(self):
        cases = [closed(entryDate="20240103"), closed(entryDate="2024-02-30"),
                 closed(exitDate="2024-01-02"), closed(entered=1), closed(positionState="pending"),
                 closed(holdingSessions=0), closed(holdingSessions=True), closed(openRealizedPnlAmount=1),
                 opened(exitDate="2024-01-10"), opened(closedPnlAmount=1),
                 {**absent(), "investedAmount": 1}, {**absent(), "entryPrice": 10}]
        incomplete = closed(); del incomplete["investedAmount"]; cases.append(incomplete)
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                pair_episode(row, closed())

    def test_pair_and_aggregation_preserve_inputs_and_nested_evidence(self):
        c, v = closed(note={"fills": ["a"]}), opened(note={"fills": ["b"]})
        before = deepcopy((c, v))
        row = pair_episode(c, v)
        frozen = deepcopy(row)
        aggregate([row])
        self.assertEqual((c, v), before)
        self.assertEqual(row, frozen)
        row["control"]["note"]["fills"].append("changed")
        self.assertEqual(c, before[0])

    def test_aggregate_complete_partition_and_money_bridge(self):
        rows = [pair_episode(closed(100, 20), closed(200, 60)),
                pair_episode(opened(100, 10, -30), closed(100, 10, entryDate="2024-01-04")),
                pair_episode(closed(100, -5), absent()), pair_episode(absent(), opened(100, 2, 3)),
                pair_episode(absent(), absent())]
        summary = aggregate(rows)
        self.assertEqual(summary["groupCounts"], dict.fromkeys(GROUPS, 1))
        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["commonEnteredCount"], 2)
        self.assertEqual((summary["controlPnl"], summary["candidatePnl"], summary["deltaPnl"]), (-5, 75, 80))
        self.assertEqual((summary["controlClosedCount"], summary["candidateClosedCount"], summary["controlOpenCount"], summary["candidateOpenCount"]), (2, 2, 1, 1))
        self.assertEqual(summary["controlClosedPnlAmount"], 15)
        self.assertEqual(summary["candidateOpenRealizedPnlAmount"], 2)
        self.assertEqual(summary["candidateOpenUnrealizedPnlAmount"], 3)
        self.assertAlmostEqual(summary["meanReturnDeltaPct"], 20)
        self.assertAlmostEqual(summary["medianReturnDeltaPct"], 20)
        self.assertEqual(sum(summary["maturityCounts"].values()), 5)
        self.assert_bridge(summary)
        rows[0]["deltaPnl"] = 99
        with self.assertRaises(ValueError):
            aggregate(rows)

    def test_empty_aggregate_and_single_side_do_not_create_distribution(self):
        summary = aggregate([])
        self.assertEqual(summary["count"], 0)
        self.assertEqual(summary["groupCounts"], dict.fromkeys(GROUPS, 0))
        self.assertEqual(len(summary["maturityCounts"]), 9)
        self.assertIsNone(summary["meanReturnDeltaPct"])
        self.assertIsNone(summary["medianReturnDeltaPct"])
        self.assert_bridge(summary)
        self.assertIsNone(aggregate([pair_episode(absent(), closed())])["meanReturnDeltaPct"])

    def test_uniform_money_scaling_and_nonfinite_arithmetic(self):
        a = pair_episode(closed(100, 20), closed(200, 60))
        b = pair_episode(closed(700, 140), closed(1400, 420))
        for key in (*COMPONENTS, "deltaPnl"):
            self.assertAlmostEqual(b[key], 7 * a[key])
        self.assertEqual(b["controlReturnPct"], a["controlReturnPct"])
        self.assertEqual(b["candidateReturnPct"], a["candidateReturnPct"])
        with self.assertRaises(ValueError):
            pair_episode(closed(1e-300, 1e300), closed())
        with self.assertRaises(ValueError):
            pair_episode(opened(1e308, 1e308, 1e308), closed())


if __name__ == "__main__":
    unittest.main()
