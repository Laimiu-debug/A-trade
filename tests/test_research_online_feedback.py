"""Synthetic online timing, weighting, ridge algebra and missingness tests."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from service.research_online_feedback import build_online_feedback, _solve_3x3


def fixture(length=180):
    days, cursor = [], date(2024, 1, 2)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    rows = [{"formationDate": d, "asOfDate": days[i - 1] if i else None,
             "price20": {"positiveReturn20Breadth": .5 if i else None},
             "amount": {"medianRelativeToOwnPrior20": 2. if i else None},
             "retention": {"continuous": {"retentionLower": .5 if i else None}}} for i, d in enumerate(days)]
    return days, rows


def episode(days, formation, exit_at, value=.25, identity=None, entry=None):
    return {"formationId": identity or "e%d" % formation, "formationDate": days[formation],
            "entryDate": days[formation + 1 if entry is None else entry],
            "exitDate": days[exit_at] if exit_at is not None else None,
            "netReturn": value if exit_at is not None else None}


def run(days, rows, episodes, *targets):
    return build_online_feedback(days, [days[i] for i in targets], rows, episodes)


def training_fixture():
    days, rows = fixture()
    episodes = []
    for i in range(1, 21):
        rows[i]["price20"]["positiveReturn20Breadth"] = 0. if i <= 10 else 1.
        episodes.append(episode(days, i, 22, -.25 if i <= 10 else .75))
    rows[24]["price20"]["positiveReturn20Breadth"] = 1.
    return days, rows, episodes


class OnlineFeedbackTests(unittest.TestCase):
    def test_empty_support_retains_signals_with_null_predictions(self):
        days, rows = fixture()
        got = run(days, rows, [], 0, 25)
        for row in got["dailyRows"]:
            self.assertEqual(row["trainingLabels"], [])
            self.assertEqual(row["support"]["eligibleFormationDayCount"], 0)
            for model in row["models"].values():
                self.assertTrue(model["allow"])
                self.assertIsNone(model["prediction"])
                self.assertEqual(model["status"], "fallback")
        self.assertEqual(got["dailyRows"][0]["window"]["availableSlots"], 0)
        self.assertIsNone(got["dailyRows"][0]["asOfDate"])

    def test_known_ridge_solution_and_unpenalized_intercept(self):
        days, rows, episodes = training_fixture()
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        summary = got["trainingSummary"]
        self.assertEqual(summary["featureMeans"], [.5, 2., .5])
        self.assertEqual(summary["featureStdDevs"], [.5, 0., 0.])
        self.assertEqual(summary["normalMatrix"], [[2., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
        self.assertEqual(summary["rhs"], [.5, 0., 0.])
        self.assertEqual(summary["slopes"], [.25, 0., 0.])
        self.assertAlmostEqual(got["models"]["intercept"]["prediction"], .25)
        self.assertAlmostEqual(got["models"]["ridge"]["prediction"], .5)
        self.assertTrue(got["window"]["historyTruncated"])

    def test_twenty_distinct_days_not_twenty_or_more_episodes(self):
        days, rows, episodes = training_fixture()
        episodes = episodes[:19] + [episode(days, 1, 22, .25, "extra%d" % i) for i in range(30)]
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertEqual(got["trainingSummary"]["formationDayCount"], 19)
        self.assertEqual(got["trainingSummary"]["episodeCount"], 49)
        self.assertIsNone(got["trainingSummary"]["slopes"])
        self.assertTrue(all(model["status"] == "fallback" for model in got["models"].values()))

    def test_exact_zero_prediction_is_rejected_without_epsilon(self):
        days, rows, episodes = training_fixture()
        for ep in episodes: ep["netReturn"] = 0.
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        for model in got["models"].values():
            self.assertEqual(model["prediction"], 0.)
            self.assertFalse(model["allow"])

    def test_intercept_and_ridge_can_disagree_on_identical_support(self):
        days, rows, episodes = training_fixture()
        for i in range(1, 21):
            rows[i]["price20"]["positiveReturn20Breadth"] = .25 if i <= 10 else .75
            episodes[i - 1]["netReturn"] = -.75 if i <= 10 else .25
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertAlmostEqual(got["models"]["intercept"]["prediction"], -.25)
        self.assertAlmostEqual(got["models"]["ridge"]["prediction"], .25)
        self.assertFalse(got["models"]["intercept"]["allow"])
        self.assertTrue(got["models"]["ridge"]["allow"])

    def test_inclusive_exit_window_uses_calendar_slots_not_formation_dates(self):
        days, rows = fixture()
        episodes = [episode(days, 1, 23, identity="tooOld"), episode(days, 2, 24, identity="first"),
                    episode(days, 3, 149, identity="last"), episode(days, 4, 150, identity="future")]
        got = run(days, rows, episodes, 150)["dailyRows"][0]
        self.assertEqual(got["window"], {"requestedSlots": 126, "availableSlots": 126,
            "startDate": days[24], "endDate": days[149], "historyTruncated": False})
        self.assertEqual([r["formationId"] for r in got["trainingLabels"]], ["first", "last"])
        self.assertEqual(got["support"]["closedBeforeWindowCount"], 1)
        self.assertEqual(got["immature"][0]["formationId"], "future")

    def test_late_maturity_enters_only_after_final_exit_and_uses_original_x(self):
        days, rows = fixture()
        rows[1]["amount"]["medianRelativeToOwnPrior20"] = 7.
        ep = episode(days, 1, 135, -.5)
        got = run(days, rows, [ep], 135, 136)["dailyRows"]
        self.assertEqual(got[0]["trainingLabels"], [])
        self.assertEqual(got[0]["immature"][0]["ageSlots"], 133)
        self.assertEqual(got[1]["trainingLabels"][0]["features"], [.5, 7., .5])
        self.assertEqual(got[1]["trainingLabels"][0]["netReturn"], -.5)

    def test_same_formation_day_total_weight_is_one(self):
        days, rows, episodes = training_fixture()
        episodes += [episode(days, 1, 22, -.25, "replica%d" % i) for i in range(9)]
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        first = [row for row in got["trainingLabels"] if row["formationDate"] == days[1]]
        self.assertEqual(len(first), 10)
        self.assertTrue(all(row["weight"] == .1 and row["formationDayCount"] == 10 for row in first))
        self.assertAlmostEqual(got["trainingSummary"]["weightSum"], 20.)
        self.assertAlmostEqual(got["models"]["ridge"]["prediction"], .5)

    def test_day_weight_denominator_is_recomputed_after_an_exit_leaves_window(self):
        days, rows = fixture()
        episodes = [episode(days, 1, 23, -.25, "old"), episode(days, 1, 148, .75, "new")]
        a, b = run(days, rows, episodes, 149, 150)["dailyRows"]
        self.assertEqual({r["formationId"] for r in a["trainingLabels"]}, {"old", "new"})
        self.assertTrue(all(r["weight"] == .5 for r in a["trainingLabels"]))
        self.assertEqual(b["trainingLabels"][0]["formationId"], "new")
        self.assertEqual(b["trainingLabels"][0]["weight"], 1.)

    def test_unknown_training_x_is_reported_and_both_models_share_rejection(self):
        days, rows, episodes = training_fixture()
        rows[1]["retention"]["continuous"]["retentionLower"] = None
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertEqual(got["support"]["closedInWindowCount"], 20)
        self.assertEqual(got["support"]["eligibleFormationDayCount"], 19)
        self.assertEqual([r["formationId"] for r in got["labelXUnknown"]], ["e1"])
        self.assertEqual(got["labelXUnknown"][0]["reasons"], ["continuousRetentionLowerUnavailable"])
        self.assertTrue(all(m["status"] == "fallback" for m in got["models"].values()))

    def test_missing_environment_row_keeps_the_closed_label_in_unknown_denominator(self):
        days, rows, episodes = training_fixture()
        rows = [r for r in rows if r["formationDate"] != days[1]]
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertEqual(got["labelXUnknown"][0]["reasons"], ["missingEnvironmentRow"])
        self.assertEqual(got["support"]["labelXUnknownCount"], 1)

    def test_unknown_current_x_preserves_training_support_but_retains_all_signals(self):
        days, rows, episodes = training_fixture()
        rows[24]["amount"]["medianRelativeToOwnPrior20"] = None
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertEqual(got["trainingSummary"]["formationDayCount"], 20)
        self.assertAlmostEqual(got["trainingSummary"]["labelMean"], .25)
        self.assertIsNone(got["trainingSummary"]["normalMatrix"])
        self.assertTrue(all(m["allow"] and m["reason"] == "currentFeaturesUnknown" for m in got["models"].values()))

    def test_immature_keeps_long_held_entries_and_excludes_not_yet_entered(self):
        days, rows = fixture()
        episodes = [episode(days, 1, None, identity="oldOpen"), episode(days, 3, 170, .25, "laterExit"),
                    episode(days, 149, None, identity="notEnteredByA")]
        got = run(days, rows, episodes, 150)["dailyRows"][0]
        self.assertEqual([r["formationId"] for r in got["immature"]], ["oldOpen", "laterExit"])
        self.assertEqual(got["support"]["immatureAgeSlots"], {"minimum": 146, "mean": 147., "maximum": 148})
        self.assertTrue(all(set(r) == {"formationId", "formationDate", "entryDate", "ageSlots"} for r in got["immature"]))

    def test_future_exit_dates_returns_and_environment_values_do_not_change_prior_decisions(self):
        days, rows, episodes = training_fixture()
        episodes.append(episode(days, 21, 30, .2, "futureExit"))
        before = run(days, rows, episodes, 24)
        changed_rows, changed_eps = deepcopy(rows), deepcopy(episodes)
        for row in changed_rows[25:]:
            row["price20"]["positiveReturn20Breadth"] = .99
            row["amount"]["medianRelativeToOwnPrior20"] = 900.
        changed_eps[-1].update(exitDate=days[100], netReturn=-.9)
        changed_eps.append(episode(days, 50, 60, 5., "futureEntry"))
        self.assertEqual(before, run(days, changed_rows, changed_eps, 24))

    def test_future_calendar_append_and_sparse_requested_targets_do_not_change_decision(self):
        days, rows, episodes = training_fixture()
        first = build_online_feedback(days[:30], [days[24]], rows[:30], episodes)
        second = run(days, rows, episodes, 24)
        self.assertEqual(first, second)
        sparse = run(days, rows, episodes, 24, 150)["dailyRows"][1]
        self.assertEqual(sparse, run(days, rows, episodes, 150)["dailyRows"][0])

    def test_fractionally_weighted_exact_constant_columns_have_zero_scale(self):
        days, rows, episodes = training_fixture()
        for row in rows[1:]:
            row["price20"]["positiveReturn20Breadth"] = .1
            row["amount"]["medianRelativeToOwnPrior20"] = .1
            row["retention"]["continuous"]["retentionLower"] = .1
        episodes.extend(episode(days, 1, 22, -.25, "extra%d" % i) for i in range(6))
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertEqual(got["trainingSummary"]["featureStdDevs"], [0., 0., 0.])
        self.assertEqual(got["trainingSummary"]["slopes"], [0., 0., 0.])
        self.assertEqual(got["models"]["ridge"]["prediction"], got["models"]["intercept"]["prediction"])

    def test_known_nondiagonal_three_by_three_system(self):
        matrix = [[4., 1., 2.], [1., 3., 0.], [2., 0., 5.]]
        rhs = [3., -5., 4.5]
        before = deepcopy((matrix, rhs))
        actual = _solve_3x3(matrix, rhs)
        for value, expected in zip(actual, [1., -2., .5]): self.assertAlmostEqual(value, expected)
        self.assertEqual((matrix, rhs), before)

    def test_all_three_features_use_training_standardization_and_fixed_unit_penalty(self):
        days, rows = fixture()
        episodes = []
        for i in range(24):
            signs = [1 if (i % 8) & (1 << k) else -1 for k in range(3)]
            rows[i + 1]["price20"]["positiveReturn20Breadth"] = .5 + .25 * signs[0]
            rows[i + 1]["amount"]["medianRelativeToOwnPrior20"] = 2. + signs[1]
            rows[i + 1]["retention"]["continuous"]["retentionLower"] = .5 + .25 * signs[2]
            value = .125 + .5 * signs[0] - .25 * signs[1] + .125 * signs[2]
            episodes.append(episode(days, i + 1, 26, value))
        rows[28]["price20"]["positiveReturn20Breadth"] = .75
        rows[28]["amount"]["medianRelativeToOwnPrior20"] = 1.
        rows[28]["retention"]["continuous"]["retentionLower"] = .75
        got = run(days, rows, episodes, 28)["dailyRows"][0]
        summary = got["trainingSummary"]
        for actual, expected in zip(summary["featureMeans"], [.5, 2., .5]): self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(summary["featureStdDevs"], [.25, 1., .25]): self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(summary["slopes"], [.25, -.125, .0625]): self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(got["models"]["intercept"]["prediction"], .125)
        self.assertAlmostEqual(got["models"]["ridge"]["prediction"], .5625)

    def test_inputs_are_unchanged_and_outputs_detached(self):
        days, rows, episodes = training_fixture()
        before = deepcopy((days, rows, episodes))
        got = run(days, rows, episodes, 24)
        got["dailyRows"][0]["trainingLabels"][0]["features"][0] = 99
        got["targetDates"].append("changed")
        self.assertEqual((days, rows, episodes), before)

    def test_input_order_does_not_select_training_or_change_numerical_sum_order(self):
        days, rows, episodes = training_fixture()
        self.assertEqual(run(days, rows, episodes, 24), run(days, list(reversed(rows)), list(reversed(episodes)), 24))

    def test_calendar_and_target_identity_validation(self):
        days, rows = fixture()
        for calendar, targets in ((list(reversed(days)), [days[24]]), (days + [days[-1]], [days[24]]),
                                  (days, [days[24], days[23]]), (days, [days[24], days[24]]), (days, ["2099-01-01"])):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                build_online_feedback(calendar, targets, rows, [])

    def test_environment_duplicate_clock_and_value_validity(self):
        days, rows = fixture()
        for kind in ("duplicate", "clock", "nan", "bool", "percent", "zeroAmount"):
            bad = deepcopy(rows)
            if kind == "duplicate": bad.append(deepcopy(bad[24]))
            elif kind == "clock": bad[24]["asOfDate"] = days[24]
            elif kind == "zeroAmount": bad[24]["amount"]["medianRelativeToOwnPrior20"] = 0.
            else: bad[24]["price20"]["positiveReturn20Breadth"] = {"nan": float("nan"), "bool": True, "percent": 50.}[kind]
            with self.subTest(kind=kind), self.assertRaises(ValueError): run(days, bad, [], 24)

    def test_episode_identity_dates_and_decimal_label_validation(self):
        days, rows = fixture()
        valid = episode(days, 1, 22)
        variants = []
        for edit in ({"entryDate": days[0]}, {"entryDate": days[1]}, {"exitDate": days[1]},
                     {"exitDate": days[2]}, {"formationDate": "2099-01-01"},
                     {"netReturn": "25%"}, {"netReturn": float("nan")}, {"netReturn": True}, {"netReturn": None}):
            variants.append([{**valid, **edit}])
        variants += [[valid, deepcopy(valid)], [{**valid, "exitDate": None}]]
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError): run(days, rows, bad, 24)

    def test_same_day_heterogeneous_returns_are_averaged_without_win_selection(self):
        days, rows, episodes = training_fixture()
        episodes.append(episode(days, 1, 22, .75, "e1second"))
        got = run(days, rows, episodes, 24)["dailyRows"][0]
        self.assertAlmostEqual(got["trainingSummary"]["labelMean"], .275)
        self.assertEqual(len([r for r in got["trainingLabels"] if r["formationDate"] == days[1]]), 2)
        self.assertEqual(got["trainingSummary"]["formationDayCount"], 20)


if __name__ == "__main__":
    unittest.main()
