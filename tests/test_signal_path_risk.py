import unittest
from copy import deepcopy

from test_signal_target_study import fixture
from service.signal_target_study import SignalTargetStudy
from service.signal_path_risk import PathRiskStudy, classify, summarize_paths


class PathRiskTests(unittest.TestCase):
    def case(self, low=10.):
        data, config, signal = fixture([10., 10.8, 11.6, 12.5, 13.5, 13.5])
        data["securities"][0]["bars"][6]["low"] = low
        event = SignalTargetStudy(data, config).evaluate(signal)
        event.update(eventId="example", strategyId=signal["strategyId"], symbol=signal["symbol"])
        return data, config, event

    def test_entry_day_boundary_is_retained_despite_sell_lock(self):
        data, config, event = self.case(7.9)
        original = deepcopy(event)
        row = PathRiskStudy(data, config).evaluate(event)
        self.assertTrue(row["firstObservedBoundary"]["-20"]["entryDayTPlusOneLocked"])
        self.assertEqual(classify(row, -20), "boundaryThenTarget")
        self.assertEqual(event, original)

    def test_exit_day_later_low_and_post_exit_missing_are_ignored(self):
        data, config, event = self.case()
        data["securities"][0]["bars"][10]["low"] = 1.
        del data["securities"][0]["bars"][11]
        row = PathRiskStudy(data, config).evaluate(event)
        self.assertEqual(classify(row, -20), "targetWithoutObservedBoundary")
        self.assertFalse(row["missing"])

    def test_fixed_horizon_does_not_look_at_later_success(self):
        data, config, event = self.case(8.)
        row = PathRiskStudy(data, config).evaluate(event)
        self.assertEqual(classify(row, -20, 3), "boundaryStillWaiting")
        self.assertEqual(classify(row, -20, 5), "boundaryThenTarget")

    def test_missing_path_stays_unresolved_and_in_denominator(self):
        data, config, event = self.case(8.)
        del data["securities"][0]["bars"][7]
        row = PathRiskStudy(data, config).evaluate(event)
        self.assertEqual(classify(row, -20), "dataUnresolved")
        group = summarize_paths([row], horizons=(5,))["groups"][0]
        record = group["observations"][-1]
        self.assertEqual(record["cohortCount"], 1)
        self.assertEqual(record["counts"]["dataUnresolved"], 1)
        self.assertEqual(record["targetWithoutBoundaryMissingOutcomeBounds"]["upper"], 1.)

    def test_known_suspension_counts_time_but_is_not_missing(self):
        data, config, event = self.case(8.)
        security = data["securities"][0]
        security["tradeStatusByDate"][data["calendar"][7]] = False
        del security["bars"][7]
        row = PathRiskStudy(data, config).evaluate(event)
        self.assertFalse(row["missing"])
        self.assertEqual(row["targetTradingDays"], 5)

    def test_early_hit_cannot_enter_immature_horizon_cohort(self):
        data, config, event = self.case()
        row = PathRiskStudy(data, config).evaluate(event)
        output = summarize_paths([row], horizons=(20,))
        record = output["groups"][0]["observations"][-1]
        self.assertEqual(record["cohortCount"], 0)
        self.assertEqual(record["insufficientPotentialFollowupCount"], 1)

    def test_frozen_mae_disagreement_fails(self):
        data, config, event = self.case()
        data["securities"][0]["bars"][6]["low"] = 8.
        with self.assertRaisesRegex(ValueError, "MAE"):
            PathRiskStudy(data, config).evaluate(event)

    def test_a_later_execution_audit_does_not_change_earlier_horizon(self):
        data, config, event = self.case()
        event["quality"]["executionCapacityIssues"].append({"tradingDay": 5})
        row = PathRiskStudy(data, config).evaluate(event)
        self.assertEqual(classify(row, -20, 3), "neitherObserved")
        self.assertEqual(classify(row, -20, 5), "dataUnresolved")


if __name__ == "__main__":
    unittest.main()
