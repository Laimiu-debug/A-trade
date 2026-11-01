"""Regression for numerical unknowns that broke the independent replay."""
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("leader_replay", Path(__file__).resolve().parents[1] / "scripts/verify-signal-leadership-panel.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class NumericUnknownTests(unittest.TestCase):
    def item(self, closes, amounts=None):
        days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(closes))]
        bars = [{"date": day, "open": c, "high": c, "low": c, "close": c, "volume": 1.,
                 "amount": (amounts or [1.]*len(closes))[i]} for i, (day, c) in enumerate(zip(days, closes))]
        return replay.inputs({"bars": bars}, days)

    def test_underflow_and_overflow_are_unknown_not_minus_one_or_infinity(self):
        benchmark = self.item([1.] * 21)
        for first, last in ((1e308, 5e-324), (5e-324, 1e308)):
            with self.subTest(first=first):
                item = self.item([first] + [1.] * 19 + [last])
                self.assertEqual(replay.returns(item, benchmark, 20, 20), (None, None))

    def test_path_distance_overflow_keeps_other_descriptors(self):
        item = self.item([1e308 if i % 2 else 1. for i in range(21)])
        result = replay.formulas(item, 20)
        self.assertIsNone(result["signedPathEfficiency20"])
        self.assertEqual(result["positiveCloseReturnSessions20"], 10)
        self.assertEqual(result["closePathMaxDrawdown20"], 1.)

    def test_amount_ratio_overflow_does_not_erase_known_amount(self):
        item = self.item([1.] * 21, [5e-324] * 20 + [1e308])
        result = replay.formulas(item, 20)
        self.assertEqual(result["amountToday"], 1e308)
        self.assertEqual(result["prior20MeanAmount"], 5e-324)
        self.assertIsNone(result["amountRelativeToOwnPrior20"])


if __name__ == "__main__":
    unittest.main()
