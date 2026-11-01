import copy
import json
import unittest

from service.research_market_context import FILTER_IDS, filter_market_context


def signal(day="2024-01-10", rank=1):
    return {"signalId": f"contraction:{day}:{rank}", "signalDate": day,
            "strategyId": "original", "symbol": f"stock{rank}",
            "originalRank": rank, "metadata": {"kept": [rank]}}


def daily(day="2024-01-10"):
    return {"date": day, "qUnknownCount": 99,
            "price20": {"positiveCount": 6, "evaluableCount": 10},
            "matched5": {"available": True, "earlierDate": "2024-01-03",
                         "pairedCount": 10, "matchedBreadthChange5": 1},
            "yesterdayCohort": {"previousCohortAvailable": True,
                                "previousDate": "2024-01-09", "eligibleCount": 5,
                                "continuedCount": 3, "observedFailureCount": 2,
                                "unknownCount": 0}}


def pressure(source, **changes):
    values = {"asOfDate": "2024-01-09", "previousCalendarDate": "2024-01-09",
              "pressure5": .2, "pressure20": .1}
    values.update(changes)
    return {source["signalId"]: values}


class ResearchMarketContextTests(unittest.TestCase):
    def test_all_four_independent_masks_and_evidence(self):
        source, row = signal(), daily()
        result = filter_market_context([source], [row], prior_pressure=pressure(source))
        for key in FILTER_IDS:
            self.assertEqual(result["schedules"][key], [source])
            self.assertEqual(result["counts"][key], {"included": 1, "excluded": 0, "unknown": 0})
            self.assertEqual(result["evidence"][key][0]["signalId"], source["signalId"])
        row["price20"]["positiveCount"] = 5
        result = filter_market_context([source], [row], prior_pressure=pressure(source))
        self.assertEqual(result["schedules"]["breadthMajority"], [])
        for key in FILTER_IDS[1:]:
            self.assertEqual(result["schedules"][key], [source])

    def test_strict_boundaries_not_equal(self):
        source, row = signal(), daily()
        row["price20"]["positiveCount"] = 5
        row["matched5"]["matchedBreadthChange5"] = 0
        row["yesterdayCohort"].update(eligibleCount=4, continuedCount=2, observedFailureCount=2)
        for changes in ({"pressure20": 0}, {"pressure5": .1}):
            result = filter_market_context([source], [row], prior_pressure=pressure(source, **changes))
            for key in FILTER_IDS:
                self.assertEqual(result["evidence"][key][0]["status"], "excluded")

    def test_empty_denominators_and_first_day_are_not_fabricated(self):
        source, row = signal(), daily()
        row["price20"].update(positiveCount=0, evaluableCount=0)
        row["matched5"].update(pairedCount=0, matchedBreadthChange5=None)
        row["yesterdayCohort"].update(eligibleCount=0, continuedCount=0,
                                      observedFailureCount=0, unknownCount=0)
        result = filter_market_context([source], [row])
        self.assertEqual([result["evidence"][key][0]["status"] for key in FILTER_IDS],
                         ["unknown", "unknown", "excluded", "unknown"])
        row["yesterdayCohort"].update(previousCohortAvailable=False, previousDate=None)
        row["matched5"].update(available=False, earlierDate=None)
        result = filter_market_context([source], [row])
        self.assertEqual(result["evidence"]["strongCohort"][0]["status"], "unknown")

    def test_cohort_unknown_has_priority_and_is_not_global_q_unknown(self):
        row, source = daily(), signal()
        row["yesterdayCohort"].update(continuedCount=0, observedFailureCount=4, unknownCount=1)
        result = filter_market_context([source], [row])
        self.assertEqual(result["evidence"]["strongCohort"][0]["status"], "unknown")
        row["yesterdayCohort"].update(continuedCount=3, observedFailureCount=2, unknownCount=0)
        row["qUnknownCount"] = 3000
        self.assertEqual(filter_market_context([source], [row])["schedules"]["strongCohort"], [source])

    def test_missing_invalid_and_nonfinite_required_values_are_unknown(self):
        source = signal()
        for value in (None, True, "1", float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                row = daily()
                row["price20"]["positiveCount"] = value
                row["matched5"]["matchedBreadthChange5"] = value
                row["yesterdayCohort"]["unknownCount"] = value
                result = filter_market_context([source], [row], prior_pressure=pressure(source, pressure5=value))
                self.assertTrue(all(result["evidence"][key][0]["status"] == "unknown" for key in FILTER_IDS))
                json.dumps(result, allow_nan=False)
        result = filter_market_context([source], [])
        self.assertTrue(all(result["evidence"][key][0]["status"] == "unknown" for key in FILTER_IDS))

    def test_pressure_requires_exact_preceding_calendar_identity(self):
        source = signal()
        for changes in ({"asOfDate": "2024-01-10"},
                        {"previousCalendarDate": "2024-01-11"},
                        {"asOfDate": "2024-01-08"},
                        {"asOfDate": "not-a-date"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                filter_market_context([source], [daily()], prior_pressure=pressure(source, **changes))
        for changes in ({"asOfDate": None}, {"previousCalendarDate": None}):
            result = filter_market_context([source], [daily()], prior_pressure=pressure(source, **changes))
            self.assertEqual(result["evidence"]["preBreakoutPressure"][0]["status"], "unknown")
        # Weekend is not counted as a session: the caller provides Friday for Monday.
        monday = signal("2024-01-15")
        result = filter_market_context([monday], [], prior_pressure=pressure(
            monday, asOfDate="2024-01-12", previousCalendarDate="2024-01-12"))
        self.assertEqual(result["schedules"]["preBreakoutPressure"], [monday])

    def test_future_append_input_isolation_and_rank_gaps_no_backfill(self):
        rows = [daily(), daily("2024-01-11")]
        rows[1]["price20"]["positiveCount"] = 4
        sources = [signal("2024-01-10", 5), signal("2024-01-11", 3), signal("2024-01-10", 2)]
        prior = {key: value for item in sources for key, value in pressure(item).items()}
        snapshot = copy.deepcopy((sources, rows, prior))
        expected = filter_market_context(sources, rows, prior_pressure=prior)
        self.assertEqual((sources, rows, prior), snapshot)
        self.assertEqual([x["originalRank"] for x in expected["schedules"]["breadthMajority"]], [5, 2])
        self.assertEqual([x["originalRank"] for x in expected["evidence"]["breadthMajority"]], [5, 3, 2])
        future = daily("2024-01-12")
        future["price20"]["positiveCount"] = float("nan")
        self.assertEqual(filter_market_context(sources, rows + [future], prior_pressure=prior), expected)
        expected["schedules"]["breadthMajority"][0]["metadata"]["kept"].append(999)
        expected["evidence"]["breadthMajority"][0]["inputs"]["positiveCount"] = 0
        self.assertEqual((sources, rows, prior), snapshot)

    def test_structural_contradictions_fail_closed_and_empty_input(self):
        source = signal()
        for mutation in ("positiveExceedsN", "cohortSum", "futureMatched", "futureCohort", "badSection"):
            row = daily()
            if mutation == "positiveExceedsN": row["price20"]["positiveCount"] = 11
            elif mutation == "cohortSum": row["yesterdayCohort"]["continuedCount"] = 4
            elif mutation == "futureMatched": row["matched5"]["earlierDate"] = source["signalDate"]
            elif mutation == "futureCohort": row["yesterdayCohort"]["previousDate"] = "2024-01-11"
            else: row["matched5"] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                filter_market_context([source], [row])
        with self.assertRaises(ValueError): filter_market_context([source], [daily(), daily()])
        with self.assertRaises(ValueError): filter_market_context([source, source], [daily()])
        empty = filter_market_context([], [])
        self.assertEqual(empty["inputSignalCount"], 0)
        for key in FILTER_IDS:
            self.assertEqual(empty["schedules"][key], [])
            self.assertEqual(sum(empty["counts"][key].values()), 0)


if __name__ == "__main__":
    unittest.main()
