import copy
from datetime import date, timedelta
import itertools
import json
import unittest

from service import research_strength_feedback as model


def dates(size):
    day, result = date(2022, 1, 3), []
    while len(result) < size:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def bar(day, close):
    return {"date": day, "open": close, "high": close * 1.001,
            "low": close * .999, "close": close, "volume": 1000., "amount": None}


def source(stocks=30, size=30):
    calendar = dates(size)
    return {"priceBasisId": "raw-unadjusted", "calendar": calendar,
        "securities": [{"symbol": f"sz{n:06}", "bars": [
            bar(day, 100. + n * min(at, 20) / 20) for at, day in enumerate(calendar)]}
            for n in range(stocks)],
        "benchmark": {"symbol": "sh000300", "bars": [bar(day, 100.) for day in calendar]}}


def set_close(data, number, at, close):
    data["securities"][number]["bars"][at] = bar(data["calendar"][at], close)


def signals(data, indexes=(25,), ranks=(1, 3)):
    return [{"signalId": f"leaderLagFollowersV1:{data['calendar'][at]}:anchor:sz{rank:06}",
        "strategyId": "leaderLagFollowersV1", "symbol": f"sz{rank:06}",
        "signalDate": data["calendar"][at], "originalRank": rank}
        for at in indexes for rank in ranks]


def build(data=None, first=25, last=None, supplied=None, **kwargs):
    data = source() if data is None else data
    last = first if last is None else last
    supplied = signals(data, range(first, last + 1)) if supplied is None else supplied
    return model.build_strength_feedback(data, data["calendar"][first], data["calendar"][last], supplied, **kwargs)


def rising(data, at=25):
    for n in range(len(data["securities"]) - 3, len(data["securities"])):
        set_close(data, n, at, (100. + n) * 1.1)


class StrengthFeedbackTests(unittest.TestCase):
    def test_exact_calendar_warmup_and_full_board_rank_population(self):
        data, members = source(), []
        result = build(data, on_members=members.append)
        row = result["dailyRows"][0]
        self.assertEqual(row["cohortDate"], data["calendar"][20])
        self.assertEqual(row["rankWindowDates"], data["calendar"][:21])
        self.assertEqual(row["feedbackWindowDates"], data["calendar"][20:26])
        self.assertEqual(row["rankPopulation"]["count"], 30)
        self.assertEqual([m["symbol"] for m in members[0]["members"]], ["sz000027", "sz000028", "sz000029"])
        self.assertLess(row["cohortDate"], result["targetDates"][0])
        self.assertEqual(row["feedbackCounts"]["flat"], 3)

    def test_exact_boundary_tie_is_inclusive_and_not_truncated(self):
        data, members = source(stocks=10), []
        set_close(data, 8, 20, 109.)
        result = build(data, on_members=members.append)
        self.assertEqual(result["dailyRows"][0]["cohortSize"], 2)
        self.assertEqual([m["percentile"] for m in members[0]["members"]], [.9, .9])
        self.assertEqual([m["symbol"] for m in members[0]["members"]], ["sz000008", "sz000009"])

    def test_relative_rank_does_not_require_positive_absolute_prior_return(self):
        data, members = source(stocks=10), []
        for n in range(10):
            set_close(data, n, 20, 50. + n)
        build(data, on_members=members.append)
        self.assertEqual([m["symbol"] for m in members[0]["members"]], ["sz000009"])
        self.assertLess(members[0]["members"][0]["relativeReturn20"], 0)

    def test_majority_bounds_match_every_possible_unknown_assignment(self):
        for size in range(7):
            for p in range(size + 1):
                for u in range(size - p + 1):
                    counts = {"positive": p, "flat": size - p - u, "negative": 0, "unknown": u}
                    actual = model._majority(counts)[0]
                    possible = {sum([True] * p + [False] * (size - p - u) + list(bits)) / size > .5
                                for bits in itertools.product((False, True), repeat=u)} if size else set()
                    expected = "pass" if possible == {True} else "fail" if possible == {False} else "unknown"
                    self.assertEqual(actual, expected, counts)

    def test_flat_and_negative_are_separate_and_half_does_not_pass(self):
        data, members = source(stocks=20), []
        set_close(data, 18, 25, 130.)
        result = build(data, on_members=members.append)
        self.assertEqual(result["dailyRows"][0]["feedbackCounts"], {"positive": 1, "flat": 1, "negative": 0, "unknown": 0})
        self.assertEqual(result["dailyRows"][0]["state"], "fail")
        set_close(data, 19, 25, 100.)
        result = build(data)
        self.assertEqual(result["dailyRows"][0]["feedbackCounts"]["negative"], 1)
        self.assertEqual(result["dailyRows"][0]["state"], "fail")

    def test_unknown_members_remain_in_frozen_denominator(self):
        data = source()
        rising(data)
        data["securities"][29]["bars"].pop(23)
        row = build(data)["dailyRows"][0]
        self.assertEqual(row["cohortSize"], 3)
        self.assertEqual(row["feedbackCounts"], {"positive": 2, "flat": 0, "negative": 0, "unknown": 1})
        self.assertEqual(row["state"], "pass")
        self.assertEqual(row["positiveFractionLower"], 2 / 3)
        self.assertEqual(row["positiveFractionUpper"], 1.)
        set_close(data, 28, 25, 100.)
        self.assertEqual(build(data)["dailyRows"][0]["state"], "unknown")
        set_close(data, 27, 25, 100.)
        self.assertEqual(build(data)["dailyRows"][0]["state"], "fail")

    def test_missing_interior_slot_is_not_compressed_into_endpoint_return(self):
        data, members = source(), []
        rising(data)
        data["securities"][29]["bars"].pop(22)
        build(data, on_members=members.append)
        row = members[0]["members"][-1]
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["reason"], "stock:missingAlignedRawBar")
        self.assertIsNone(row["returnSinceCohort"])

    def test_historical_zero_and_explicit_bad_raw_quality_are_unknown(self):
        for mutation, expected in (
            (lambda sec, day: sec.update(tradeStatusByDate={day: 0.0}), "knownSuspension"),
            (lambda sec, day: sec["bars"][22].update(knownSuspended=True), "knownSuspension"),
            (lambda sec, day: sec["bars"][22].update(volume=True), "nonpositiveOrUnknownRawOHLCV"),
            (lambda sec, day: sec["bars"][22].update(high=1.), "invalidRawOHLCEnvelope"),
            (lambda sec, day: sec["bars"][22].update(priceBasisId="adjusted"), "rawPriceBasisUnknown"),
        ):
            with self.subTest(expected=expected):
                data, members = source(), []
                mutation(data["securities"][29], data["calendar"][22])
                build(data, on_members=members.append)
                self.assertEqual(members[0]["members"][-1]["reason"], "stock:" + expected)

    def test_benchmark_required_only_for_f_ranking_not_a_absolute_feedback(self):
        data = source()
        rising(data)
        data["benchmark"]["bars"][23]["volume"] = 0
        self.assertEqual(build(data)["dailyRows"][0]["state"], "pass")
        data["benchmark"]["bars"][19]["volume"] = 0
        row = build(data)["dailyRows"][0]
        self.assertEqual(row["rankPopulation"]["count"], 0)
        self.assertEqual(row["rankReasonCounts"], {"benchmark:nonpositiveOrUnknownRawOHLCV": 30})
        self.assertEqual(row["state"], "unknown")

    def test_new_a_winners_cannot_replace_f_frozen_members(self):
        data, members = source(), []
        for n in range(30):
            set_close(data, n, 25, 1000. if n < 27 else 90.)
        result = build(data, on_members=members.append)
        self.assertEqual([m["symbol"] for m in members[0]["members"]], ["sz000027", "sz000028", "sz000029"])
        self.assertEqual(result["dailyRows"][0]["state"], "fail")
        self.assertEqual(len(result["schedules"]["known"]), 2)
        self.assertEqual(result["schedules"]["positive"], [])

    def test_empty_cohort_and_insufficient_history_stay_unknown(self):
        tied = source(stocks=10)
        for n in range(10):
            set_close(tied, n, 20, 100.)
        row = build(tied)["dailyRows"][0]
        self.assertEqual(row["rankPopulation"]["count"], 10)
        self.assertEqual(row["reason"], "emptyFrozenCohort")
        self.assertIsNone(row["positiveFractionLower"])
        for at, reason in ((3, "insufficientCohortCalendarHistory"), (24, "emptyFrozenCohort")):
            row = build(source(), first=at)["dailyRows"][0]
            self.assertEqual(row["state"], "unknown")
            self.assertEqual(row["reason"], reason)
            self.assertEqual(row["rankPopulation"]["count"], 0)

    def test_future_append_and_future_numeric_mutation_do_not_change_output(self):
        short, long = source(size=26), source(size=40)
        rising(short)
        rising(long)
        for sec in long["securities"] + [long["benchmark"]]:
            for item in sec["bars"][26:]:
                item.update(close=float("nan"), volume=None, priceBasisId="future-invalid")
        self.assertEqual(build(short), build(long))

    def test_extending_end_preserves_prior_daily_and_opportunity_prefix(self):
        data = source(size=35)
        rising(data)
        earlier = build(data)
        later = build(data, last=29)
        self.assertEqual(earlier["dailyRows"], later["dailyRows"][:1])
        self.assertEqual(earlier["opportunities"], later["opportunities"][:2])
        self.assertEqual(earlier["schedules"]["positive"], later["schedules"]["positive"][:2])

    def test_queue_masks_preserve_original_ids_ranks_dates_and_all_opportunities(self):
        data = source()
        rising(data, 25)
        for n in (27, 28, 29):
            set_close(data, n, 26, 100.)
        set_close(data, 27, 27, 140.)
        set_close(data, 28, 27, 100.)
        data["securities"][29]["bars"].pop(27)
        original = signals(data, (25, 26, 27))
        result = build(data, last=27, supplied=original)
        self.assertEqual([r["state"] for r in result["dailyRows"]], ["pass", "fail", "unknown"])
        self.assertEqual(result["counts"]["policyCounts"], {
            "known": {"included": 4, "excluded": 0, "unknown": 2},
            "positive": {"included": 2, "excluded": 2, "unknown": 2}})
        self.assertEqual([o["formationId"] for o in result["opportunities"]], [s["signalId"] for s in original])
        for mode, expected in (("known", original[:4]), ("positive", original[:2])):
            self.assertEqual(result["schedules"][mode], [{**s, "strategyId": model.STRATEGY_ID} for s in expected])
            self.assertEqual({s["originalRank"] for s in result["schedules"][mode]}, {1, 3})

    def test_final_session_signal_stays_on_a_for_account_next_open_handling(self):
        data = source(size=26)
        rising(data)
        result = build(data)
        self.assertEqual(len(result["schedules"]["positive"]), 2)
        self.assertEqual({s["signalDate"] for s in result["schedules"]["positive"]}, {data["calendar"][-1]})

    def test_inputs_and_stream_callbacks_are_detached(self):
        data = source()
        rising(data)
        original = signals(data)
        saved = copy.deepcopy((data, original))
        captured = []
        def sink(record):
            captured.append(copy.deepcopy(record))
            record["rankPopulation"]["count"] = -1
            record["members"][0]["symbol"] = "mutated"
        result = build(data, supplied=original, on_members=sink)
        self.assertEqual((data, original), saved)
        self.assertEqual(result["dailyRows"][0]["rankPopulation"]["count"], 30)
        self.assertEqual(result["dailyRows"][0]["memberDigest"], model._digest(captured[0]["members"]))
        result["schedules"]["known"][0]["symbol"] = "changed"
        self.assertNotEqual(result["schedules"]["positive"][0]["symbol"], "changed")
        self.assertEqual(original, saved[1])
        json.dumps(result, allow_nan=False)

    def test_source_signal_identity_schema_order_and_population_are_strict(self):
        data = source()
        original = signals(data)
        bad = []
        bad.append(list(reversed(original)))
        bad.append([original[0], original[0]])
        for field, value in (("strategyId", "other"), ("symbol", "foreign"), ("originalRank", True),
                             ("signalDate", data["calendar"][24]), ("signalId", "")):
            bad.append([{**original[0], field: value}])
        bad.append([{**original[0], "extra": True}])
        bad.append([{k: v for k, v in original[0].items() if k != "symbol"}])
        bad.append([original[0], {**original[1], "signalId": "unique", "symbol": original[0]["symbol"]}])
        bad.append([original[0], {**original[1], "signalId": "unique", "originalRank": 1}])
        for supplied in bad:
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                build(data, supplied=supplied)

    def test_calendar_raw_basis_and_duplicate_bars_are_rejected(self):
        for mutate in (
            lambda d: d.update(priceBasisId="adjusted"),
            lambda d: d["calendar"].insert(20, d["calendar"][20]),
            lambda d: d["securities"].append(copy.deepcopy(d["securities"][0])),
            lambda d: d["securities"][0]["bars"].insert(20, copy.deepcopy(d["securities"][0]["bars"][20])),
            lambda d: d["benchmark"].update(symbol="sh000001"),
        ):
            data = source()
            mutate(data)
            with self.assertRaises(ValueError):
                build(data)

    def test_feedback_nonfinite_and_underflow_are_unknown_not_wins_or_losses(self):
        for f_price, prior_price, a_price in ((1e-300, 1e-301, 1e300), (1e300, 1e299, 1e-300)):
            data, members = source(stocks=10), []
            set_close(data, 9, 0, prior_price)
            set_close(data, 9, 20, f_price)
            set_close(data, 9, 25, a_price)
            result = build(data, on_members=members.append)
            member = members[0]["members"][0]
            self.assertEqual(member["reason"], "nonfiniteOrUnderflowFeedbackRatio")
            self.assertEqual(member["status"], "unknown")
            self.assertEqual(result["dailyRows"][0]["cohortSize"], 1)
            json.dumps(result, allow_nan=False)

    def test_no_signal_days_and_bounded_progress_are_still_complete(self):
        data, messages, members = source(size=40), [], []
        result = build(data, first=25, last=39, supplied=[], progress=messages.append, on_members=members.append)
        self.assertEqual(len(result["dailyRows"]), 15)
        self.assertEqual(len(members), 15)
        self.assertEqual([r["completedDays"] for r in messages], [10, 15])
        self.assertEqual(sum(result["counts"]["dayStates"].values()), 15)
        self.assertEqual(result["counts"]["inputSignalCount"], 0)
        self.assertEqual(result["schedules"], {"known": [], "positive": []})


if __name__ == "__main__":
    unittest.main()
