from copy import deepcopy
from datetime import date, timedelta
import json
import math
import unittest

from service.force_statistics import prepare_force_statistics


def bars(n=60):
    start = date(2022, 1, 1)
    return [{"date": (start + timedelta(days=i)).isoformat(), "open": 100. + i - 1,
             "high": 101. + i, "low": 98. + i, "close": 100. + i, "volume": 100.}
            for i in range(n)]


class ForceStatisticsTests(unittest.TestCase):
    def test_hand_computed_pressure_direction_shares_and_warmup(self):
        result = prepare_force_statistics(bars())
        rows = result["rows"]
        self.assertIsNone(rows[23]["pressure5"])
        self.assertAlmostEqual(rows[24]["pressure5"], 1/3)
        self.assertIsNone(rows[38]["pressure20"])
        row = rows[39]
        self.assertEqual(row["TR"], 3.)
        self.assertAlmostEqual(row["pressure20"], 1/3)
        self.assertEqual(row["direction20"], 1.)
        self.assertAlmostEqual(row["support20"], 2/3)
        self.assertAlmostEqual(row["opposition20"], 1/3)
        self.assertEqual(row["positiveShare20"], 1.)
        self.assertEqual(row["volumeRatio20"], 1.)
        self.assertEqual(row["status"], "complete")

    def test_neutral_range_zero_and_gap_use_previous_close(self):
        data = bars()
        for row in data:
            row.update(open=10., high=10., low=10., close=10.)
        result = prepare_force_statistics(data)["current"]
        self.assertEqual(result["TR"], 0)
        self.assertEqual(result["pressure20"], 0)
        self.assertEqual(result["direction20"], 0)
        self.assertEqual(result["closeLocation"], 0)
        self.assertEqual(result["support20"], .5)
        self.assertEqual(result["positiveShare20"], 0)
        data[-1].update(open=12., high=13., low=11., close=12.)
        row = prepare_force_statistics(data)["current"]
        self.assertEqual(row["TR"], 3.)
        self.assertAlmostEqual(row["pressure"], 2/3)
        self.assertEqual(row["closeLocation"], 0)

    def test_capped_weights_and_neutral_days_remain_in_denominator(self):
        data = bars(45)
        for row in data:
            row.update(open=10., high=12., low=8., close=10.)
        data[40].update(close=11., volume=1000.)
        data[41].update(close=10., volume=100.)
        rows = prepare_force_statistics(data)["rows"]
        self.assertEqual(rows[40]["volumeRatio20"], 10.)
        self.assertEqual(rows[40]["weight"], 3.)
        weights = [3., 100/145, 100/145, 100/145, 100/145]
        expected = (3*.25 - (100/145)*.25) / sum(weights)
        self.assertAlmostEqual(rows[44]["pressure5"], expected)
        self.assertAlmostEqual(rows[44]["direction5"], expected*4)
        self.assertEqual(rows[44]["positiveShare20"], 1/20)

    def test_calendar_missing_slots_are_not_compressed(self):
        data = bars(); calendar = [row["date"] for row in data]
        missing_day = data[30]["date"]
        del data[30]
        result = prepare_force_statistics(data, calendar=calendar)
        self.assertEqual(len(result["rows"]), 60)
        self.assertEqual(result["rows"][30]["date"], missing_day)
        self.assertEqual(result["rows"][30]["barStatus"], "missing")
        self.assertIsNone(result["rows"][30]["close"])
        self.assertIsNone(result["current"]["pressure20"])
        self.assertEqual(result["metadata"]["missingBarCount"], 1)
        self.assertEqual(prepare_force_statistics(data)["metadata"]["calendarBasis"], "observedBars")

    def test_zero_activity_and_invalid_ohlc_do_not_become_zero_pressure(self):
        for changes in ({"volume": 0}, {"volume": float("nan")}, {"close": None},
                        {"high": 1.}, {"low": -1.}, {"open": True}):
            data = bars(); data[-1].update(changes)
            row = prepare_force_statistics(data)["current"]
            self.assertEqual(row["barStatus"], "invalid")
            self.assertIsNone(row["pressure"])
            self.assertIsNone(row["pressure20"])
            self.assertIsNone(row["ma20"])
            self.assertIsNotNone(row["previousMa20"])

    def test_scale_invariance_and_optional_amount_is_not_used(self):
        data = bars(); scaled = deepcopy(data)
        for i, row in enumerate(scaled):
            row["volume"] /= 100
            row["amount"] = None if i % 2 else 999.
        a = prepare_force_statistics(data, volume_unit="shares")
        b = prepare_force_statistics(scaled, volume_unit="lots")
        self.assertEqual(a["rows"], b["rows"])
        self.assertFalse(a["metadata"]["amountUsed"])

    def test_past_only_pullback_high_and_moving_average_windows(self):
        data = bars(45)
        for i, row in enumerate(data):
            row["volume"] = float(i + 10)
        result = prepare_force_statistics(data)["current"]
        i = 44
        self.assertEqual(result["prior10High"], max(r["high"] for r in data[i-10:i]))
        self.assertEqual(result["previousHigh"], data[i-1]["high"])
        self.assertAlmostEqual(result["ma20"], sum(r["close"] for r in data[i-19:i+1])/20)
        self.assertAlmostEqual(result["previousMa20"], sum(r["close"] for r in data[i-20:i])/20)
        self.assertAlmostEqual(result["pullback3Return"], data[i-1]["close"]/data[i-4]["close"]-1)
        self.assertAlmostEqual(result["pullback3VolumeRatio"],
                               (sum(r["volume"] for r in data[i-3:i])/3)/(sum(r["volume"] for r in data[i-13:i-3])/10))
        changed = deepcopy(data); changed[-1].update(high=10000., volume=100000., close=9000.)
        after = prepare_force_statistics(changed)["current"]
        for key in ("prior10High", "previousHigh", "previousMa20", "pullback3Return", "pullback3VolumeRatio"):
            self.assertEqual(result[key], after[key])

    def test_causal_append_and_inputs_remain_unchanged(self):
        data = bars(); original = deepcopy(data); calendar = [r["date"] for r in data]
        full = prepare_force_statistics(data, calendar=calendar)
        prefix = prepare_force_statistics(data[:41], calendar=calendar[:41])
        self.assertEqual(full["rows"][:41], prefix["rows"])
        self.assertEqual(data, original)
        self.assertEqual(calendar, [r["date"] for r in original])
        json.dumps(full, allow_nan=False)

    def test_recent_120_and_full_500_match_after_finite_warmup(self):
        data = bars(500)
        for i, row in enumerate(data):
            row["volume"] = float(100 + (i * 37) % 211)
        full = prepare_force_statistics(data)["rows"]
        recent = prepare_force_statistics(data[-120:])["rows"]
        # Forty bars cover every field's longest dependency. No EMA seed leaks
        # from older history into the detail window or research window.
        self.assertEqual(recent[39:], full[-120:][39:])

    def test_date_order_identity_and_unit_errors_raise(self):
        data = bars(3)
        invalid = [data[::-1], [data[0], data[0]], [{**data[0], "date": "2022-02-30"}], [None]]
        for rows in invalid:
            with self.assertRaises(ValueError):
                prepare_force_statistics(rows)
        dates = [r["date"] for r in data]
        for calendar in (dates[::-1], [dates[0], dates[0]], dates[:-1], ["2022-1-1"], "bad"):
            with self.assertRaises(ValueError):
                prepare_force_statistics(data, calendar=calendar)
        with self.assertRaises(ValueError):
            prepare_force_statistics(data, volume_unit="money")

    def test_empty_short_and_extreme_finite_volume_are_json_safe(self):
        self.assertIsNone(prepare_force_statistics([])["current"])
        self.assertIsNone(prepare_force_statistics(bars(1))["current"]["prevClose"])
        data = bars()
        for row in data:
            row["volume"] = 1e308
        result = prepare_force_statistics(data)
        self.assertEqual(result["current"]["volumeRatio20"], 1.)
        self.assertTrue(math.isfinite(result["current"]["pressure20"]))
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
