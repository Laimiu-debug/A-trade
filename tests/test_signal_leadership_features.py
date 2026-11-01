import copy
from datetime import date, timedelta
import json
import math
from statistics import mean
import unittest

from service.signal_leadership_features import (
    FEATURE_VERSION, PersistenceTracker, add_cross_section_ranks, daily_features,
)


def sessions(count=100):
    result, day = [], date(2021, 8, 2)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def bar(day, close=100., amount=1000.):
    return {"date": day, "priceBasisId": "raw-unadjusted", "open": close,
            "high": close, "low": close, "close": close, "volume": 10., "amount": amount}


def fixture(count=61):
    days = sessions(count)
    stock = [bar(day, 100. + i, 1000. + i) for i, day in enumerate(days)]
    bench = [bar(day, 200. + i) for i, day in enumerate(days)]
    return days, stock, bench


def evaluate(days, stock, bench, **kwargs):
    return daily_features("sh600001", days[-1], days, stock, bench, **kwargs)


class LeadershipDailyFeatureTests(unittest.TestCase):
    def test_complete_features_have_independently_calculable_values(self):
        days, stock, bench = fixture()
        row = evaluate(days, stock, bench)
        self.assertEqual(FEATURE_VERSION, "daily-leadership-raw-v1")
        self.assertAlmostEqual(row["return20"], 160 / 140 - 1)
        self.assertAlmostEqual(row["return60"], .6)
        self.assertAlmostEqual(row["relativeReturn20"], (160 / 140) / (260 / 240) - 1)
        self.assertAlmostEqual(row["relativeReturn60"], 1.6 / 1.3 - 1)
        self.assertEqual(row["positiveCloseReturnSessions20"], 20)
        self.assertEqual(row["closePathMaxDrawdown20"], 0)
        self.assertEqual(row["signedPathEfficiency20"], 1)
        self.assertEqual(row["closeAboveMA20Sessions20"], 20)
        self.assertEqual(row["prior20MeanAmount"], 1049.5)
        self.assertEqual(row["knownAmountSessions20"], 20)
        self.assertAlmostEqual(row["amountRelativeToOwnPrior20"], 1060 / 1049.5)
        self.assertTrue(all(c["complete"] for c in row["completeness"].values()))
        self.assertEqual(row["featureReasons"], {})
        self.assertFalse(row["certified"])

    def test_drawdown_is_positive_proportion_and_efficiency_is_signed(self):
        days, stock, bench = fixture()
        for i in range(21):
            stock[-21+i] = bar(days[-21+i], 120. - i)
        row = evaluate(days, stock, bench)
        self.assertAlmostEqual(row["closePathMaxDrawdown20"], 20 / 120)
        self.assertEqual(row["signedPathEfficiency20"], -1)
        self.assertEqual(row["positiveCloseReturnSessions20"], 0)
        values = [100., 110., 99.] + [100.] * 18
        for i, value in enumerate(values):
            stock[-21+i] = bar(days[-21+i], value)
        row = evaluate(days, stock, bench)
        self.assertAlmostEqual(row["closePathMaxDrawdown20"], .1)
        self.assertEqual(row["signedPathEfficiency20"], 0)
        self.assertEqual(row["positiveCloseReturnSessions20"], 2)

    def test_flat_path_has_undefined_efficiency_and_strict_above_ma(self):
        days, stock, bench = fixture()
        stock = [bar(day) for day in days]
        row = evaluate(days, stock, bench)
        self.assertEqual(row["return20"], 0)
        self.assertEqual(row["positiveCloseReturnSessions20"], 0)
        self.assertEqual(row["closeAboveMA20Sessions20"], 0)
        self.assertIsNone(row["signedPathEfficiency20"])
        self.assertEqual(row["featureReasons"]["signedPathEfficiency20"], ["zeroPathDistance"])

    def test_old_missing_slot_blocks_sixty_but_not_independent_short_groups(self):
        days, stock, bench = fixture()
        stock[5] = None
        row = evaluate(days, stock, bench)
        self.assertIsNone(row["return60"])
        self.assertIsNone(row["relativeReturn60"])
        for key in ("return20", "relativeReturn20", "signedPathEfficiency20", "closeAboveMA20Sessions20", "prior20MeanAmount"):
            self.assertIsNotNone(row[key], key)
        self.assertEqual(row["missingDates"], [days[5]])
        self.assertEqual(row["completeness"]["security60"]["validSessions"], 60)

    def test_interior_price_gap_is_not_repaired_by_known_endpoints(self):
        days, stock, bench = fixture()
        stock[-10] = None
        row = evaluate(days, stock, bench)
        self.assertIsNone(row["return20"])
        self.assertIsNone(row["signedPathEfficiency20"])
        self.assertIsNone(row["prior20MeanAmount"])
        self.assertEqual(row["knownAmountSessions20"], 19)
        self.assertEqual(row["amountToday"], 1060)

    def test_missing_benchmark_does_not_erase_stock_path_or_own_return(self):
        days, stock, bench = fixture()
        bench[-3] = None
        row = evaluate(days, stock, bench)
        self.assertIsNotNone(row["return20"])
        self.assertIsNotNone(row["return60"])
        self.assertEqual(row["positiveCloseReturnSessions20"], 20)
        self.assertIsNone(row["relativeReturn20"])
        self.assertIsNone(row["relativeReturn60"])
        self.assertEqual(row["benchmarkQuality"]["missingDates"], [days[-3]])

    def test_benchmark_uses_full_ohlcv_not_close_only(self):
        for key, value in (("volume", 0), ("high", 1), ("open", float("nan"))):
            with self.subTest(key=key):
                days, stock, bench = fixture()
                bench[-3][key] = value
                row = evaluate(days, stock, bench)
                self.assertIsNone(row["relativeReturn20"])
                self.assertIsNotNone(row["return20"])
                self.assertEqual(row["benchmarkQuality"]["invalidDates"], [days[-3]])

    def test_known_halt_and_positive_volume_conflict_block_required_price(self):
        days, stock, bench = fixture()
        statuses = [None] * 61
        statuses[-2] = "0"
        row = evaluate(days, stock, bench, trade_status61=statuses)
        self.assertIsNone(row["return20"])
        self.assertEqual(row["haltDates"], [days[-2]])
        self.assertIn("suspensionContradictsPositiveVolume", row["completeness"]["security20"]["reasons"])
        stock[-2]["volume"] = 0
        row = evaluate(days, stock, bench, trade_status61=statuses)
        self.assertIn("knownSuspension", row["completeness"]["security20"]["reasons"])
        statuses[-2] = None
        row = evaluate(days, stock, bench, trade_status61=statuses)
        self.assertEqual(row["haltDates"], [])
        self.assertIn(days[-2], row["unknownTradeStatusDates"])

    def test_benchmark_explicit_halt_blocks_only_relative_features(self):
        days, stock, bench = fixture()
        statuses = [None] * 61
        statuses[-2] = False
        row = evaluate(days, stock, bench, benchmark_trade_status61=statuses)
        self.assertIsNone(row["relativeReturn20"])
        self.assertIsNotNone(row["return20"])
        self.assertEqual(row["benchmarkQuality"]["haltDates"], [days[-2]])

    def test_unknown_identity_and_status_are_not_silently_certified_or_filtered(self):
        days, stock, bench = fixture()
        stock[-1].update(name="ST CURRENT", currentStatus="delisted", futureDelistDate="2025-01-01")
        row = evaluate(days, stock, bench)
        self.assertIsNotNone(row["relativeReturn20"])
        self.assertEqual(row["unknownTradeStatusDates"], days)
        self.assertEqual(row["historicalStEvidence"], "unknown")
        self.assertEqual(row["comparableActionEvidence"], "unverified")

    def test_twenty_ma_comparisons_need_exactly_thirty_nine_slots(self):
        days, stock, bench = fixture(39)
        row = evaluate(days, stock, bench)
        self.assertEqual(row["closeAboveMA20Sessions20"], 20)
        row = evaluate(days[1:], stock[1:], bench[1:])
        self.assertIsNone(row["closeAboveMA20Sessions20"])
        self.assertIsNotNone(row["return20"])
        stock[0] = None
        row = evaluate(days, stock, bench)
        self.assertIsNone(row["closeAboveMA20Sessions20"])
        self.assertIsNotNone(row["signedPathEfficiency20"])

    def test_short_warmup_preserves_available_independent_features(self):
        days, stock, bench = fixture(1)
        row = evaluate(days, stock, bench)
        self.assertEqual(row["amountToday"], 1000)
        self.assertEqual(row["knownAmountSessions20"], 0)
        self.assertIsNone(row["prior20MeanAmount"])
        days, stock, bench = fixture(21)
        row = evaluate(days, stock, bench)
        self.assertIsNotNone(row["return20"])
        self.assertIsNotNone(row["prior20MeanAmount"])
        self.assertIsNone(row["return60"])
        self.assertIsNone(row["closeAboveMA20Sessions20"])

    def test_amount_never_fabricated_and_ohlc_damage_is_independent(self):
        days, stock, bench = fixture()
        stock[-4].pop("amount")
        row = evaluate(days, stock, bench)
        self.assertEqual(row["knownAmountSessions20"], 19)
        self.assertIsNone(row["prior20MeanAmount"])
        self.assertIsNotNone(row["return20"])
        stock[-4]["amount"] = 1057
        stock[-4]["high"] = .1
        row = evaluate(days, stock, bench)
        self.assertIsNone(row["return20"])
        self.assertEqual(row["knownAmountSessions20"], 20)
        self.assertEqual(row["prior20MeanAmount"], 1049.5)

    def test_amount_window_excludes_today_and_current_amount_stands_alone(self):
        days, stock, bench = fixture()
        stock[-1]["amount"] = 1e12
        row = evaluate(days, stock, bench)
        self.assertEqual(row["prior20MeanAmount"], 1049.5)
        stock[-1]["amount"] = None
        row = evaluate(days, stock, bench)
        self.assertEqual(row["prior20MeanAmount"], 1049.5)
        self.assertIsNone(row["amountToday"])
        self.assertIsNone(row["amountRelativeToOwnPrior20"])

    def test_raw_basis_date_alignment_and_envelope_are_explicit(self):
        for change in ({"priceBasisId": "reference-adjusted"}, {"date": "2025-01-01"}, {"low": 999.}):
            with self.subTest(change=change):
                days, stock, bench = fixture()
                stock[-2].update(change)
                row = evaluate(days, stock, bench)
                self.assertIsNone(row["return20"])
                self.assertIn(days[-2], row["invalidDates"])

    def test_input_not_mutated_and_future_source_changes_cannot_change_t(self):
        days, stock, bench = fixture(80)
        before = copy.deepcopy((days, stock, bench))
        baseline = evaluate(days[:61], stock[:61], bench[:61])
        self.assertEqual((days, stock, bench), before)
        for i in range(61, 80):
            stock[i] = bar(days[i], 1e30, 1e30)
            bench[i] = None
        stock.append(bar("2026-01-01", 1e-10))
        self.assertEqual(evaluate(days[:61], stock[:61], bench[:61]), baseline)
        with self.assertRaises(ValueError):
            daily_features("x", days[59], days[:61], stock[:61], bench[:61])

    def test_calendar_shape_errors_do_not_repair_or_truncate_slots(self):
        days, stock, bench = fixture()
        invalid = [days[::-1], days[:-2] + [days[-3], days[-1]]]
        for calendar in invalid:
            with self.assertRaises(ValueError):
                evaluate(calendar, stock, bench)
        with self.assertRaises(ValueError):
            evaluate(days, stock[:-1], bench)
        with self.assertRaises(ValueError):
            evaluate(days, stock, bench, trade_status61=[])
        with self.assertRaises(ValueError):
            evaluate(*fixture(62))

    def test_invalid_finite_boundaries_stay_json_safe(self):
        for value in (float("nan"), float("inf"), 0, -1, True, 10 ** 500):
            with self.subTest(kind=str(value)[:20]):
                days, stock, bench = fixture()
                stock[-2]["close"] = value
                stock[-2]["amount"] = value
                row = evaluate(days, stock, bench)
                self.assertIsNone(row["return20"])
                self.assertIsNone(row["prior20MeanAmount"])
                json.dumps(row, allow_nan=False)

    def test_derived_overflow_underflow_and_distance_overflow_are_null(self):
        for first, last in ((5e-324, 1e308), (1e308, 5e-324)):
            days, stock, bench = fixture()
            stock[-21] = bar(days[-21], first)
            stock[-1] = bar(days[-1], last)
            row = evaluate(days, stock, bench)
            self.assertIsNone(row["return20"])
            self.assertIsNone(row["relativeReturn20"])
            json.dumps(row, allow_nan=False)
        days, stock, bench = fixture()
        for i in range(21):
            stock[-21+i] = bar(days[-21+i], 1e308 if i % 2 else 1., 5e-324)
        stock[-1]["amount"] = 1e308
        row = evaluate(days, stock, bench)
        self.assertIsNone(row["signedPathEfficiency20"])
        self.assertIsNone(row["amountRelativeToOwnPrior20"])
        json.dumps(row, allow_nan=False)


def rank_rows(day, values20, values60=None):
    values60 = values60 if values60 is not None else [None] * len(values20)
    return [{"symbol": f"s{i}", "asOfDate": day, "relativeReturn20": x,
             "relativeReturn60": values60[i]} for i, x in enumerate(values20)]


class LeadershipRankPersistenceTests(unittest.TestCase):
    def test_midrank_ties_separate_denominators_no_eligibility_filter(self):
        rows = rank_rows("2022-01-04", [-1, 2, 2, 3, None], [None, 8, None, 8, None])
        rows[3]["eligible"] = False
        rows[3]["prior20MeanAmount"] = None
        self.assertIs(add_cross_section_ranks(rows), rows)
        self.assertEqual([r["relativeReturn20Percentile"] for r in rows], [.125, .5, .5, .875, None])
        self.assertEqual([r["relativeReturn60Percentile"] for r in rows], [None, .5, None, .5, None])
        self.assertEqual({r["relativeReturn20CrossSectionCount"] for r in rows}, {4})
        self.assertEqual({r["relativeReturn60CrossSectionCount"] for r in rows}, {2})

    def test_singleton_empty_and_all_unknown_cross_sections(self):
        self.assertEqual(add_cross_section_ranks([]), [])
        rows = add_cross_section_ranks(rank_rows("2022-01-04", [None, 1]))
        self.assertEqual(rows[1]["relativeReturn20Percentile"], .5)
        self.assertEqual(rows[0]["relativeReturn60CrossSectionCount"], 0)
        self.assertIsNone(rows[0]["relativeReturn60Percentile"])

    def test_rank_rejects_duplicate_mixed_dates_and_nonfinite_before_mutation(self):
        rows = rank_rows("2022-01-04", [1, 2])
        rows[1]["relativeReturn60"] = float("inf")
        with self.assertRaises(ValueError):
            add_cross_section_ranks(rows)
        self.assertNotIn("relativeReturn20Percentile", rows[0])
        rows[1]["relativeReturn60"] = None
        rows[1]["asOfDate"] = "2022-01-05"
        with self.assertRaises(ValueError):
            add_cross_section_ranks(rows)
        rows[1] = dict(rows[0])
        with self.assertRaises(ValueError):
            add_cross_section_ranks(rows)

    def test_tracker_archives_actual_daily_rank_and_denominator_not_t_cohort(self):
        tracker, days, archived = PersistenceTracker(), sessions(21), []
        for i, day in enumerate(days[:20]):
            # s1 disappears from the evaluable cohort, never from fixed rows.
            rows = add_cross_section_ranks(rank_rows(day, [1, None if i % 2 else 2]))
            tracker.update(day, rows)
            archived.append(copy.deepcopy(rows[0]))
        row = rows[0]
        self.assertEqual(row["trailing20DailyStrengthPercentiles"], [.25, .5] * 10)
        self.assertEqual(row["trailing20StrengthCrossSectionCounts"], [2, 1] * 10)
        self.assertEqual(row["knownStrengthRankSessions20"], 20)
        self.assertEqual(row["meanStrengthPercentile20"], .375)
        self.assertEqual(row["minimumStrengthPercentile20"], .25)
        self.assertIsNone(row["topQuantileSessionCount20"])
        first = copy.deepcopy(archived[0])
        tracker.update(days[20], add_cross_section_ranks(rank_rows(days[20], [-100, 999])))
        self.assertEqual(archived[0], first)
        self.assertIsNone(archived[18]["meanStrengthPercentile20"])

    def test_tracker_warmup_missing_rank_and_empty_day_do_not_shrink_window(self):
        tracker, days = PersistenceTracker(), sessions(22)
        for i, day in enumerate(days):
            rows = add_cross_section_ranks(rank_rows(day, [None if i == 1 else 1]))
            tracker.update(day, rows)
            row = rows[0]
            if i == 0:
                self.assertEqual(row["trailing20DailyStrengthPercentiles"], [None] * 19 + [.5])
                self.assertEqual(row["trailing20StrengthCrossSectionCounts"], [None] * 19 + [1])
            if i == 19:
                self.assertEqual(row["knownStrengthRankSessions20"], 19)
                self.assertIsNone(row["meanStrengthPercentile20"])
                self.assertEqual(row["trailing20StrengthCrossSectionCounts"][1], 0)
            if i == 21:
                self.assertEqual(row["knownStrengthRankSessions20"], 20)
                self.assertEqual(row["meanStrengthPercentile20"], .5)
                self.assertEqual(row["strengthRankSessionDates20"], days[2:22])

    def test_tracker_rejects_changed_population_repeat_day_and_unranked_rows(self):
        tracker = PersistenceTracker()
        day = "2022-01-04"
        rows = rank_rows(day, [1, 2])
        with self.assertRaises(ValueError):
            tracker.update(day, rows)
        tracker.update(day, add_cross_section_ranks(rows))
        with self.assertRaises(ValueError):
            tracker.update(day, rows)
        with self.assertRaises(ValueError):
            tracker.update("2022-01-05", add_cross_section_ranks(rank_rows("2022-01-05", [1])))
        valid = add_cross_section_ranks(rank_rows("2022-01-05", [1, 2]))
        tracker.update("2022-01-05", valid)
        self.assertEqual(valid[0]["availableStrengthRankSessions20"], 2)

    def test_tracker_copies_output_arrays_and_keeps_unregistered_threshold_null(self):
        tracker, days = PersistenceTracker(), sessions(2)
        rows = add_cross_section_ranks(rank_rows(days[0], [1]))
        tracker.update(days[0], rows)
        rows[0]["trailing20DailyStrengthPercentiles"][-1] = .99
        rows[0]["trailing20StrengthCrossSectionCounts"][-1] = 999
        next_rows = add_cross_section_ranks(rank_rows(days[1], [1]))
        tracker.update(days[1], next_rows)
        self.assertEqual(next_rows[0]["trailing20DailyStrengthPercentiles"][-2:], [.5, .5])
        self.assertEqual(next_rows[0]["trailing20StrengthCrossSectionCounts"][-2:], [1, 1])
        self.assertIsNone(next_rows[0]["topQuantileSessionCount20"])


if __name__ == "__main__":
    unittest.main()
