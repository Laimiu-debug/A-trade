"""Synthetic causal clocks, independent amount and fixed-cohort retention."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from service.research_board_environment import build_board_environment
from service.research_relative_feedback_context import build_relative_feedback_context


def fixture(count=20, length=46):
    days, cursor = [], date(2024, 1, 2)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    def bars(slope):
        return [{"date": d, "open": 100 + i * slope, "high": 101 + i * slope,
                 "low": 99 + i * slope, "close": 100 + i * slope,
                 "volume": 1000., "amount": 100000.} for i, d in enumerate(days)]
    return {"priceBasisId": "raw-unadjusted", "calendar": days,
            "benchmark": {"symbol": "sh000300", "bars": bars(0)},
            "securities": [{"symbol": "s%02d" % s, "bars": bars(s / 10)} for s in range(count)]}


def change_price(data, symbol, index, close):
    row = data["securities"][symbol]["bars"][index]
    row.update(open=close, close=close, high=close * 1.01, low=close * .99)


def frozen(data, first=31, last=31):
    records = []
    build_relative_feedback_context(data, data["calendar"][first], data["calendar"][last], on_members=records.append)
    return {row["formationDate"]: row for row in records}


def build(data, first=31, last=31, members=None, **kwargs):
    return build_board_environment(data, data["calendar"][first], data["calendar"][last],
                                   frozen(data, first, last) if members is None else members, **kwargs)


class BoardEnvironmentTests(unittest.TestCase):
    def test_hand_calculated_returns_amount_and_previous_session_clock(self):
        data = fixture(1)
        for i in range(10, 30):
            data["securities"][0]["bars"][i]["amount"] = 2 * (i - 9)
        data["securities"][0]["bars"][30]["amount"] = 42
        change_price(data, 0, 30, 120)
        change_price(data, 0, 31, 500)
        rows = []; got = build(data, on_evidence=rows.append)["dailyRows"][0]
        self.assertEqual((got["asOfDate"], got["cohortDate"]), (data["calendar"][30], data["calendar"][25]))
        self.assertAlmostEqual(got["price20"]["medianAbsoluteReturn20"], .2)
        self.assertEqual(got["amount"]["medianRelativeToOwnPrior20"], 2)
        self.assertEqual(rows[0]["rows"][0]["prior20MeanAmount"], 21)
        self.assertEqual(got["retention"]["cohortSize"], 0)

    def test_bad_ohlc_does_not_erase_amount_and_bad_amount_does_not_erase_price(self):
        data = fixture(); data["securities"][0]["bars"][30]["high"] = 50
        data["securities"][1]["bars"][30]["amount"] = None
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        self.assertEqual((got["price20"]["evaluableCount"], got["amount"]["evaluableCount"]), (19, 19))
        first, second = evidence[0]["rows"][:2]
        self.assertEqual((first["return20"], first["amountRatio"]), (None, 1))
        self.assertIsNotNone(second["return20"])
        self.assertIsNone(second["amountRatio"])

    def test_amount_zero_missing_volume_and_explicit_halt_are_not_imputed(self):
        data = fixture()
        data["securities"][0]["bars"][30]["amount"] = 0
        del data["securities"][1]["bars"][30]["amount"]
        data["securities"][2]["bars"][30]["volume"] = 0
        data["securities"][3]["tradeStatusByDate"] = {data["calendar"][30]: False}
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        self.assertEqual(got["amount"]["evaluableCount"], 16)
        self.assertTrue(all(r["amountRatio"] is None for r in evidence[0]["rows"][:4]))
        self.assertEqual(got["price20"]["evaluableCount"], 18)
        self.assertEqual(evidence[0]["rows"][0]["knownAmountSessions20"], 20)

    def test_matched_first_five_unknown_then_exact_common_population(self):
        data = fixture()
        data["securities"][1]["bars"][32]["volume"] = 0
        got = build(data, last=36)["dailyRows"]
        self.assertTrue(all(not row["matched5"]["available"] for row in got[:5]))
        matched = got[5]["matched5"]
        self.assertTrue(matched["available"])
        self.assertEqual(matched["earlierDate"], data["calendar"][30])
        self.assertEqual((matched["todayEvaluableCount"], matched["earlierEvaluableCount"], matched["pairedCount"]), (19, 20, 19))
        self.assertAlmostEqual(matched["positiveBreadthToday"], 18 / 19)
        self.assertEqual(matched["positiveBreadthToday"], matched["positiveBreadthEarlier"])
        self.assertEqual(matched["matchedBreadthChange5"], 0)

    def test_ties_are_not_truncated_and_empty_frozen_queue_has_null_bounds(self):
        data = fixture()
        for source in data["securities"]:
            source["bars"] = deepcopy(data["benchmark"]["bars"])
        got = build(data)["dailyRows"][0]
        self.assertEqual(got["currentRankPopulation"]["count"], 20)
        self.assertEqual(got["price20"]["zeroCount"], 20)
        self.assertEqual(got["retention"]["cohortSize"], 0)
        self.assertIsNone(got["retention"]["endpoint"]["retentionLower"])
        self.assertIsNone(got["retention"]["continuous"]["retentionUpper"])

    def test_endpoint_recovery_does_not_erase_an_earlier_known_exit(self):
        data = fixture(); change_price(data, 19, 26, 80)
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        row = evidence[0]["rows"][19]
        self.assertEqual(row["retention"]["endpointStatus"], "retained")
        self.assertEqual(row["retention"]["continuousStatus"], "knownExit")
        self.assertEqual([r["date"] for r in row["retention"]["path"]], data["calendar"][26:31])
        self.assertEqual(got["retention"]["endpoint"]["retainedCount"], 2)
        self.assertEqual(got["retention"]["continuous"]["retainedCount"], 1)

    def test_continuous_known_exit_dominates_later_unknown(self):
        data = fixture(); change_price(data, 19, 26, 80)
        data["securities"][19]["bars"][27]["volume"] = 0
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        self.assertEqual(evidence[0]["rows"][19]["retention"]["endpointStatus"], "unknown")
        self.assertEqual(evidence[0]["rows"][19]["retention"]["continuousStatus"], "knownExit")
        self.assertEqual(got["retention"]["cohortSize"], 2)
        self.assertEqual(got["retention"]["continuous"]["knownExitCount"], 1)

    def test_unknown_retains_original_denominator_and_missing_bounds(self):
        data = fixture(); data["securities"][19]["bars"].pop(26)
        got = build(data)["dailyRows"][0]
        for mode in ("endpoint", "continuous"):
            result = got["retention"][mode]
            self.assertEqual((result["retainedCount"], result["knownExitCount"], result["unknownCount"]), (1, 0, 1))
            self.assertEqual((result["retentionLower"], result["retentionUpper"]), (.5, 1))

    def test_frozen_dates_population_and_full_membership_must_match(self):
        data = fixture(); original = frozen(data); day = data["calendar"][31]
        for kind in ("date", "member", "rank", "population"):
            bad = deepcopy(original)
            if kind == "date": bad[day]["cohortDate"] = data["calendar"][24]
            if kind == "member": bad[day]["topMembers"].pop()
            if kind == "rank": bad[day]["topMembers"][0]["percentile"] = .91
            if kind == "population": bad[day]["rankPopulation"]["count"] -= 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build(data, members=bad)

    def test_current_future_values_and_extra_future_members_do_not_change_prior_row(self):
        data = fixture(); original = deepcopy(data); members = frozen(data)
        got = build(data, members=members)
        changed = deepcopy(data)
        for source in changed["securities"]:
            for row in source["bars"][31:]:
                row.update(close=float("nan"), amount=None, volume=0)
        for row in changed["benchmark"]["bars"][31:]: row["close"] = float("nan")
        extra = deepcopy(members); extra[data["calendar"][35]] = {"unreadFuture": True}
        other = build(changed, members=extra)
        self.assertEqual(got, other)
        self.assertEqual(data, original)

    def test_raw_basis_inheritance_preserves_conflicting_explicit_basis(self):
        data = fixture(); data["securities"][0]["bars"][30]["priceBasisId"] = "adjusted"
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        row = evidence[0]["rows"][0]
        self.assertIn("rawPriceBasisUnknown", row["priceReasons"])
        self.assertIsNone(row["amountRatio"])
        self.assertEqual(got["price20"]["evaluableCount"], 19)
        data["priceBasisId"] = "unknown"
        with self.assertRaises(ValueError): build(data)

    def test_price_and_rank_historical_flag_semantics_are_explicitly_distinct(self):
        data = fixture()
        data["securities"][0]["tradeStatusByDate"] = {data["calendar"][30]: 0.0}
        data["securities"][1]["bars"][30]["knownSuspended"] = True
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        self.assertEqual((got["price20"]["evaluableCount"], got["amount"]["evaluableCount"]), (20, 20))
        self.assertEqual(got["currentRankPopulation"]["count"], 18)
        self.assertTrue(all(row["rankReason"] == "stock:knownSuspension" for row in evidence[0]["rows"][:2]))

    def test_amount_median_is_of_individual_ratios_and_money_scale_invariant(self):
        data = fixture(2)
        for row in data["securities"][0]["bars"]: row["amount"] = 10
        for row in data["securities"][1]["bars"]: row["amount"] = 1000
        data["securities"][0]["bars"][30]["amount"] = 20
        data["securities"][1]["bars"][30]["amount"] = 1000
        first = build(data)["dailyRows"][0]
        self.assertEqual(first["amount"]["medianRelativeToOwnPrior20"], 1.5)
        self.assertNotEqual(first["amount"]["medianRelativeToOwnPrior20"], 1020 / 1010)
        for source in data["securities"]:
            for row in source["bars"]: row["amount"] *= 7
        self.assertEqual(build(data)["dailyRows"][0]["amount"], first["amount"])

    def test_benchmark_failure_does_not_shrink_absolute_breadth_or_amount(self):
        data = fixture(); data["benchmark"]["bars"][30]["volume"] = 0
        got = build(data)["dailyRows"][0]
        self.assertEqual((got["price20"]["evaluableCount"], got["amount"]["evaluableCount"]), (20, 20))
        self.assertEqual(got["currentRankPopulation"]["count"], 0)
        self.assertEqual(got["retention"]["endpoint"]["unknownCount"], 2)

    def test_short_calendar_missing_slots_and_callback_isolation(self):
        data = fixture(); got = build(data, first=0, last=4)
        self.assertTrue(all(row["price20"]["evaluableCount"] == 0 for row in got["dailyRows"]))
        self.assertTrue(all(row["retention"]["cohortSize"] == 0 for row in got["dailyRows"]))
        baseline = build(data, last=33)
        observations, updates = [], []
        def mutate(row):
            observations.append(row["formationDate"])
            row["rows"][0]["return20"] = "external mutation"
        other = build(data, last=33, on_evidence=mutate, progress=updates.append)
        self.assertEqual(other, baseline)
        self.assertEqual(len(observations), 3)
        self.assertEqual(updates[-1]["completedDays"], 3)
        self.assertNotIn("evidence", other)
        data["calendar"][1] = data["calendar"][0]
        with self.assertRaises(ValueError): build(data)

    def test_nonfinite_derived_ratio_stays_unknown_not_nan(self):
        data = fixture()
        change_price(data, 0, 10, 1e-300); change_price(data, 0, 30, 1e300)
        for row in data["securities"][1]["bars"][10:30]: row["amount"] = 1e-300
        data["securities"][1]["bars"][30]["amount"] = 1e300
        evidence = []; got = build(data, on_evidence=evidence.append)["dailyRows"][0]
        self.assertEqual(evidence[0]["rows"][0]["priceReasons"], ["nonfiniteOrUnderflowReturnRatio"])
        self.assertEqual(evidence[0]["rows"][1]["amountReasons"], ["nonfiniteOrUnderflowDerivedRatio"])
        self.assertEqual((got["price20"]["evaluableCount"], got["amount"]["evaluableCount"]), (19, 19))


if __name__ == "__main__":
    unittest.main()
