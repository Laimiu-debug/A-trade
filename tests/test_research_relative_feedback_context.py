"""Synthetic boundaries for a descriptive context; no real inputs/accounts."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from service import research_relative_feedback_context as model
from service.research_strength_feedback import build_strength_feedback


def dates(size):
    cursor, result = date(2022, 1, 3), []
    while len(result) < size:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def bar(day, close):
    return {"date": day, "open": close, "high": close * 1.001,
            "low": close * .999, "close": close, "volume": 1000., "amount": None}


def source(stocks=30, size=40):
    calendar = dates(size)
    return {"priceBasisId": "raw-unadjusted", "calendar": calendar,
        "securities": [{"symbol": f"sz{n:06}", "bars": [
            bar(day, 100. + n * min(at, 20) / 20) for at, day in enumerate(calendar)]}
            for n in range(stocks)],
        "benchmark": {"symbol": "sh000300", "bars": [bar(day, 100.) for day in calendar]}}


def set_close(data, number, at, close):
    data["securities"][number]["bars"][at] = bar(data["calendar"][at], close)


def build(data=None, first=26, last=None, **kwargs):
    data = source() if data is None else data
    return model.build_relative_feedback_context(data, data["calendar"][first],
        data["calendar"][first if last is None else last], **kwargs)


def counts(size, positive, unknown=0):
    return {"positive": positive, "flat": size - positive - unknown,
            "negative": 0, "unknown": unknown}


class RelativeFeedbackContextTests(unittest.TestCase):
    def test_exact_prior_clock_full_population_and_symmetric_frozen_members(self):
        data, logs = source(), []
        result = build(data, on_members=logs.append)
        row = result["dailyRows"][0]
        self.assertEqual((row["formationDate"], row["asOfDate"], row["cohortDate"]),
            tuple(data["calendar"][i] for i in (26, 25, 20)))
        self.assertEqual(row["rankWindowDates"], data["calendar"][:21])
        self.assertEqual(row["feedbackWindowDates"], data["calendar"][20:26])
        self.assertEqual(row["rankPopulation"]["count"], 30)
        self.assertEqual([r["symbol"] for r in logs[0]["topMembers"]], ["sz000027", "sz000028", "sz000029"])
        self.assertEqual([r["symbol"] for r in logs[0]["bottomMembers"]], ["sz000000", "sz000001", "sz000002"])
        self.assertEqual(row["state"], "equal")
        self.assertFalse(any(k in result for k in ("signals", "schedules", "opportunities")))

    def test_top_group_exactly_matches_frozen_batch35_at_same_asof_date(self):
        data, current, earlier = source(), [], []
        set_close(data, 27, 25, 140.)
        data["securities"][29]["bars"].pop(23)
        got = build(data, on_members=current.append)["dailyRows"][0]
        old = build_strength_feedback(data, data["calendar"][25], data["calendar"][25], [],
            on_members=earlier.append)["dailyRows"][0]
        self.assertEqual(current[0]["topMembers"], earlier[0]["members"])
        self.assertEqual(got["rankPopulation"], old["rankPopulation"])
        self.assertEqual(got["rankReasonCounts"], old["rankReasonCounts"])
        for key in got["top"]:
            self.assertEqual(got["top"][key], old[key])

    def test_boundary_ties_are_inclusive_and_never_truncated(self):
        data, logs = source(stocks=10), []
        set_close(data, 1, 20, 100.)
        set_close(data, 8, 20, 109.)
        row = build(data, on_members=logs.append)["dailyRows"][0]
        self.assertEqual((row["top"]["cohortSize"], row["bottom"]["cohortSize"]), (2, 2))
        self.assertEqual([r["percentile"] for r in logs[0]["topMembers"]], [.9, .9])
        self.assertEqual([r["percentile"] for r in logs[0]["bottomMembers"]], [.1, .1])

    def test_unequal_group_sizes_use_separate_fixed_denominators(self):
        data = source(stocks=20)
        for n in (17, 18):
            set_close(data, n, 20, 119.)
        set_close(data, 19, 25, 130.)
        set_close(data, 0, 25, 110.)
        row = build(data)["dailyRows"][0]
        self.assertEqual((row["top"]["cohortSize"], row["bottom"]["cohortSize"]), (3, 2))
        self.assertEqual(row["difference"], {"lowerNumerator": -1, "upperNumerator": -1,
            "denominator": 6, "lower": -1 / 6, "upper": -1 / 6})
        self.assertEqual(row["state"], "weaker")

    def test_integer_bounds_agree_with_all_possible_missing_assignments(self):
        for nt in range(1, 5):
            for nb in range(1, 5):
                for pt in range(nt + 1):
                    for ut in range(nt - pt + 1):
                        for pb in range(nb + 1):
                            for ub in range(nb - pb + 1):
                                observed = model._compare_counts(counts(nt, pt, ut), counts(nb, pb, ub))[1]
                                signs = {(a * nb > b * nt) - (a * nb < b * nt)
                                    for a in range(pt, pt + ut + 1) for b in range(pb, pb + ub + 1)}
                                expected = {frozenset({1}): "stronger", frozenset({-1}): "weaker",
                                            frozenset({0}): "equal"}.get(frozenset(signs), "unknown")
                                self.assertEqual(observed, expected, (nt, nb, pt, ut, pb, ub))

    def test_unknown_members_can_still_leave_a_strictly_known_difference(self):
        data = source()
        for n in (27, 28):
            set_close(data, n, 25, 140.)
        for n in (0, 29):
            data["securities"][n]["bars"].pop(23)
        row = build(data)["dailyRows"][0]
        self.assertEqual(row["top"]["feedbackCounts"], counts(3, 2, 1))
        self.assertEqual(row["bottom"]["feedbackCounts"], counts(3, 0, 1))
        self.assertEqual(row["difference"]["lowerNumerator"], 3)
        self.assertEqual(row["state"], "stronger")

    def test_bound_touching_zero_is_unknown_unless_both_bounds_are_zero(self):
        for top, bottom in ((counts(3, 1, 1), counts(3, 1)),
                            (counts(3, 1), counts(3, 1, 1))):
            difference, state, _ = model._compare_counts(top, bottom)
            self.assertEqual(state, "unknown")
            self.assertIn(0, (difference["lowerNumerator"], difference["upperNumerator"]))
        self.assertEqual(model._compare_counts(counts(3, 1), counts(6, 2))[1], "equal")

    def test_empty_cohorts_and_history_shortfalls_remain_unknown(self):
        data = source(stocks=10)
        for n in range(10):
            set_close(data, n, 20, 100.)
        row = build(data)["dailyRows"][0]
        self.assertEqual((row["state"], row["reason"]), ("unknown", "emptyFrozenCohorts"))
        self.assertEqual(row["difference"]["denominator"], 0)
        self.assertIsNone(row["difference"]["lower"])
        for at, reason in ((0, "asOfSessionUnavailable"), (3, "insufficientCohortCalendarHistory"),
                           (25, "emptyRankPopulation")):
            row = build(source(), first=at)["dailyRows"][0]
            self.assertEqual((row["state"], row["reason"]), ("unknown", reason))
        self.assertEqual(model._compare_counts(counts(0, 0), counts(2, 1))[2], "emptyTopCohort")
        self.assertEqual(model._compare_counts(counts(2, 1), counts(0, 0))[2], "emptyBottomCohort")

    def test_missing_interior_feedback_slot_never_compresses_members_or_dates(self):
        data, logs = source(), []
        data["securities"][29]["bars"].pop(22)
        row = build(data, on_members=logs.append)["dailyRows"][0]
        member = logs[0]["topMembers"][-1]
        self.assertEqual(row["top"]["cohortSize"], 3)
        self.assertEqual(row["feedbackWindowDates"], data["calendar"][20:26])
        self.assertEqual((member["status"], member["reason"]), ("unknown", "stock:missingAlignedRawBar"))
        self.assertIsNone(member["returnSinceCohort"])

    def test_historical_zero_and_bad_raw_quality_use_inherited_unknown_semantics(self):
        for mutation, expected in (
            (lambda sec, day: sec.update(tradeStatusByDate={day: 0.0}), "knownSuspension"),
            (lambda sec, day: sec["bars"][22].update(knownSuspended=True), "knownSuspension"),
            (lambda sec, day: sec["bars"][22].update(volume=True), "nonpositiveOrUnknownRawOHLCV"),
            (lambda sec, day: sec["bars"][22].update(high=1.), "invalidRawOHLCEnvelope"),
            (lambda sec, day: sec["bars"][22].update(priceBasisId="adjusted"), "rawPriceBasisUnknown"),
        ):
            with self.subTest(expected=expected):
                data, logs = source(), []
                mutation(data["securities"][0], data["calendar"][22])
                build(data, on_members=logs.append)
                self.assertEqual(logs[0]["bottomMembers"][0]["reason"], "stock:" + expected)

    def test_benchmark_needed_for_f_rank_only_not_absolute_feedback(self):
        data = source()
        data["benchmark"]["bars"][23]["volume"] = 0
        self.assertEqual(build(data)["dailyRows"][0]["state"], "equal")
        data["benchmark"]["bars"][19]["volume"] = 0
        row = build(data)["dailyRows"][0]
        self.assertEqual(row["rankPopulation"]["count"], 0)
        self.assertEqual(row["rankReasonCounts"], {"benchmark:nonpositiveOrUnknownRawOHLCV": 30})

    def test_a_winners_cannot_replace_f_members_and_prior_gains_are_not_required(self):
        data, logs = source(stocks=10), []
        for n in range(10):
            set_close(data, n, 20, 50. + n)
            set_close(data, n, 25, 1000. if n < 9 else 40.)
        row = build(data, on_members=logs.append)["dailyRows"][0]
        self.assertEqual([r["symbol"] for r in logs[0]["topMembers"]], ["sz000009"])
        self.assertLess(logs[0]["topMembers"][0]["relativeReturn20"], 0)
        self.assertEqual(row["state"], "weaker")

    def test_current_t_and_future_numerical_pollution_do_not_change_context(self):
        short, long = source(size=27), source(size=40)
        for security in long["securities"] + [long["benchmark"]]:
            for item in security["bars"][26:]:
                item.update(close=float("nan"), volume=None, priceBasisId="future-invalid")
        self.assertEqual(build(short), build(long))

    def test_extending_end_keeps_the_already_observed_daily_prefix(self):
        data = source()
        early, late = build(data), build(data, last=32)
        self.assertEqual(early["dailyRows"], late["dailyRows"][:1])

    def test_strict_identity_calendar_and_off_calendar_bars_reject(self):
        for change in (
            lambda d: d["calendar"].insert(5, d["calendar"][5]),
            lambda d: d["securities"].append(deepcopy(d["securities"][0])),
            lambda d: d["securities"][0]["bars"][0].update(date="2022-01-02"),
            lambda d: d["benchmark"].update(symbol="sh000001"),
            lambda d: d.update(priceBasisId="adjusted"),
        ):
            data = source()
            change(data)
            with self.assertRaises(ValueError):
                build(data)

    def test_input_immutable_member_callback_detached_and_counts_complete(self):
        data, progress, logs = source(), [], []
        original = deepcopy(data)
        expected = build(data, last=35)
        def receive(row):
            logs.append(deepcopy(row))
            row["rankPopulation"]["count"] = -1
            row["topMembers"].clear()
        actual = build(data, last=35, progress=progress.append, on_members=receive)
        self.assertEqual(data, original)
        self.assertEqual(actual, expected)
        self.assertEqual(len(logs), 10)
        self.assertEqual(sum(actual["counts"]["dayStates"].values()), 10)
        self.assertEqual(progress[-1]["completedDays"], 10)
        self.assertEqual(logs[0]["memberDigest"], actual["dailyRows"][0]["memberDigest"])
        self.assertNotIn("topMembers", actual["dailyRows"][0])


if __name__ == "__main__":
    unittest.main()
