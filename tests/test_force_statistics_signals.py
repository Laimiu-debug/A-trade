import copy
from datetime import date, timedelta
import unittest

from service.force_statistics_signals import STRATEGY_IDS, generate_force_stat_signals


def rows(count=8):
    start = date(2023, 1, 2)
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "direction5": .1 + i * .01, "direction20": .2,
             "pressure5": .1 + i * .01, "pressure20": .3,
             "close": 11., "prior10High": 10., "previousHigh": 10.5,
             "ma20": 10., "previousMa20": 10.,
             "pullback3Return": -.02, "pullback3VolumeRatio": .8,
             "positiveShare20": None, "volumeRatio20": None} for i in range(count)]


class ForceStatisticsSignalsTests(unittest.TestCase):
    def test_three_rules_emit_first_true_and_preserve_score(self):
        source = rows(5)
        result = generate_force_stat_signals(source)
        for sid in STRATEGY_IDS:
            self.assertEqual([s["signalDate"] for s in result["signals"][sid]], [source[2]["date"]])
            self.assertEqual(result["signals"][sid][0]["score"], .2 if sid == "directionBreakout" else .3)
            self.assertEqual(result["counts"][sid], {"evaluated": 5, "conditionTrue": 3,
                "conditionFalse": 0, "unknown": 2, "signals": 1, "suppressedConsecutive": 2})

    def test_direction_and_pressure_are_separate_with_no_extra_filters(self):
        source = rows(3)
        for row in source:
            row["pressure5"] = -.1
            row["positiveShare20"] = float("nan")
            row["volumeRatio20"] = float("inf")
        result = generate_force_stat_signals(source)
        self.assertEqual(len(result["signals"]["directionBreakout"]), 1)
        self.assertEqual(result["signals"]["pressureBreakout"], [])
        self.assertEqual(result["signals"]["pressurePullback"], [])
        self.assertEqual(result["counts"]["pressureBreakout"]["conditionFalse"], 1)

    def test_breakout_strict_boundaries_and_all_three_positive(self):
        mutations = ((2, "pressure20", 0.), (0, "pressure5", 0.),
                     (2, "pressure5", .11), (2, "close", 10.))
        for index, key, value in mutations:
            with self.subTest(key=key, value=value):
                source = rows(3)
                source[index][key] = value
                result = generate_force_stat_signals(source)
                self.assertEqual(result["signals"]["pressureBreakout"], [])
                self.assertEqual(result["counts"]["pressureBreakout"]["conditionFalse"], 1)

    def test_pullback_exact_previous_day_and_strict_return_volume_breakout(self):
        source = rows(3)
        source[1]["ma20"] = source[1]["close"]
        source[2]["ma20"] = 1000.  # Today's MA is not the registered gate.
        source[2]["prior10High"] = 1000.  # Pullback needs previousHigh instead.
        self.assertEqual(len(generate_force_stat_signals(source)["signals"]["pressurePullback"]), 1)
        for index, key, value in ((2, "pullback3Return", 0.), (2, "pullback3VolumeRatio", 1.),
                                  (2, "previousHigh", 11.), (1, "ma20", 11.01)):
            with self.subTest(key=key):
                changed = copy.deepcopy(source)
                changed[index][key] = value
                result = generate_force_stat_signals(changed)
                self.assertEqual(result["signals"]["pressurePullback"], [])
                self.assertEqual(result["counts"]["pressurePullback"]["conditionFalse"], 1)

    def test_required_unknown_nonfinite_and_bool_rearm(self):
        for value in (None, float("nan"), float("inf"), True, "0.3"):
            with self.subTest(value=value):
                source = rows(5)
                source[3]["pressure20"] = value
                result = generate_force_stat_signals(source)
                self.assertEqual([s["signalDate"] for s in result["signals"]["pressureBreakout"]],
                                 [source[2]["date"], source[4]["date"]])
                self.assertEqual(result["counts"]["pressureBreakout"]["unknown"], 3)
        source = rows(3)
        source[-1]["pressure20"] = -1  # Known false does not erase missing evidence.
        source[-1].pop("prior10High")
        self.assertEqual(generate_force_stat_signals(source)["counts"]["pressureBreakout"]["unknown"], 3)

    def test_false_rearms_and_missing_short_window_requires_three_new_rows(self):
        source = rows(7)
        source[3]["close"] = source[3]["prior10High"]
        result = generate_force_stat_signals(source)
        self.assertEqual([s["signalDate"] for s in result["signals"]["pressureBreakout"]],
                         [source[2]["date"], source[4]["date"]])
        source = rows(7)
        source[3]["pressure5"] = None
        result = generate_force_stat_signals(source)
        self.assertEqual([s["signalDate"] for s in result["signals"]["pressureBreakout"]],
                         [source[2]["date"], source[6]["date"]])
        self.assertEqual(result["counts"]["pressureBreakout"]["unknown"], 5)

    def test_future_append_input_isolation_and_date_identity(self):
        source = rows(8)
        original = copy.deepcopy(source)
        prefix = generate_force_stat_signals(source[:5])
        source[6]["pressure20"] = None
        complete = generate_force_stat_signals(source)
        for sid in STRATEGY_IDS:
            self.assertEqual([s for s in complete["signals"][sid] if s["signalDate"] <= source[4]["date"]],
                             prefix["signals"][sid])
        self.assertEqual(source[:6], original[:6])
        prefix["signals"]["pressureBreakout"][0]["features"]["pressure5History"].append(99)
        self.assertEqual(source[:6], original[:6])
        for bad in ([source[0], source[0]], [source[1], source[0]], [{"date": "2023-1-2"}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                generate_force_stat_signals(bad)

    def test_empty_short_prefix_and_count_conservation(self):
        empty = generate_force_stat_signals([])
        for sid in STRATEGY_IDS:
            self.assertEqual(empty["signals"][sid], [])
            self.assertTrue(all(value == 0 for value in empty["counts"][sid].values()))
        source = rows(9)
        source[4]["pressure20"] = None
        source[6]["close"] = 9.
        for length in (1, 2, 9):
            result = generate_force_stat_signals(source[:length])
            for sid in STRATEGY_IDS:
                tally = result["counts"][sid]
                self.assertEqual(tally["evaluated"], length)
                self.assertEqual(tally["conditionTrue"] + tally["conditionFalse"] + tally["unknown"], length)
                self.assertEqual(tally["signals"] + tally["suppressedConsecutive"], tally["conditionTrue"])
                self.assertEqual(tally["signals"], len(result["signals"][sid]))


if __name__ == "__main__":
    unittest.main()
