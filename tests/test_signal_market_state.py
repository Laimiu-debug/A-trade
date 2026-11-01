"""Synthetic panel contracts only: no saved research data or future labels."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from statistics import mean
import unittest

from service.signal_market_state import (FEATURE_VERSION, GROUPS, MarketStateTracker,
                                       SOURCE_FEATURE_VERSION, THRESHOLDS, evaluate_q)


def days(count=8, start="2022-01-03"):
    result, current = [], date.fromisoformat(start)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def panel_row(symbol, day, population_size=6, state="true"):
    current, history = date.fromisoformat(day), []
    while len(history) < 20:
        if current.weekday() < 5:
            history.append(current.isoformat())
        current -= timedelta(days=1)
    history.reverse()
    ranks = [.95] * 20
    groups = {key: {"complete": True, "requiredSessions": required, "availableCalendarSessions": required,
                   "observedSessions": required, "validSessions": required,
                   "missingDates": [], "invalidDates": [], "haltDates": [], "reasons": [],
                   "unknownTradeStatusDates": [day]} for key, required in GROUPS.items()}
    row = {"symbol": symbol, "asOfDate": day, "featureVersion": SOURCE_FEATURE_VERSION,
        "rawPriceBasis": "raw-unadjusted", "completeness": groups, "featureReasons": {},
        "trailing20DailyStrengthPercentiles": ranks, "trailing20StrengthCrossSectionCounts": [population_size]*20,
        "strengthRankSessionDates20": history, "knownStrengthRankSessions20": 20,
        "availableStrengthRankSessions20": 20, "meanStrengthPercentile20": mean(ranks),
        "minimumStrengthPercentile20": min(ranks), "relativeReturn20Percentile": ranks[-1],
        "relativeReturn20CrossSectionCount": population_size, "relativeReturn60Percentile": .95,
        "relativeReturn60CrossSectionCount": population_size,
        "closePathMaxDrawdown20": .05, "signedPathEfficiency20": .5,
        "return20": .2, "return60": .3, "relativeReturn20": .15, "relativeReturn60": .2,
        "amountToday": 200., "prior20MeanAmount": 100., "amountRelativeToOwnPrior20": 2.,
        "knownAmountSessions20": 20, "baseEligibility": {"eligible": False, "reason": "historicalSt",
            "unknown": ["historicalMembership", "historicalTradeStatus"], "certified": False},
        "asOfEvidence": {"historicalSt": {"status": "unknown", "value": None}},
        "sampleManifestHash": "synthetic-not-real", "calendarHash": "synthetic-calendar",
        "sourceHashes": {"retainedRawBarsSha256": "synthetic-bar-source"}}
    if state == "false":
        row["signedPathEfficiency20"] = .2
    elif state == "unknown":
        row["completeness"]["benchmark60"]["complete"] = False
    elif state != "true":
        raise ValueError("Unknown synthetic state")
    return row


def panel_rows(symbols, day, states=None):
    states = states or ["true"] * len(symbols)
    return [panel_row(symbol, day, len(symbols), state) for symbol, state in zip(symbols, states)]


def invalidate(row, group, reason="missingDailyBar"):
    evidence = row["completeness"][group]
    evidence.update(complete=False, validSessions=evidence["validSessions"]-1,
                    missingDates=[row["asOfDate"]], reasons=[reason])


class MarketStateTests(unittest.TestCase):
    def setUp(self):
        self.symbols = ["s" + str(i) for i in range(6)]
        self.dates = days(8)

    def q(self, row):
        return evaluate_q(row, target_dates=self.dates, day_index=0, population_size=6)

    def test_q_evidence_before_false_and_no_eligibility_filter(self):
        row = panel_row("s0", self.dates[0])
        self.assertEqual(self.q(row)["qState"], "true")
        row["signedPathEfficiency20"] = .2
        self.assertEqual(self.q(row)["qState"], "false")
        invalidate(row, "benchmark60")
        result = self.q(row)
        self.assertEqual(result["qState"], "unknown")
        self.assertEqual(result["failedPredicates"], ["signedPathEfficiency20"])
        self.assertIsNone(result["predicates"]["relativeReturn60Percentile"]["passed"])
        self.assertFalse(result["predicates"]["signedPathEfficiency20"]["passed"])

    def test_exact_thresholds_and_next_representable_values(self):
        # Mean/min are independently bound to the archive; test their boundaries
        # with consistent archives, not by merely editing the derived statistic.
        for field, op, threshold in THRESHOLDS:
            with self.subTest(field=field):
                row = panel_row("s0", self.dates[0])
                if field == "meanStrengthPercentile20":
                    row["trailing20DailyStrengthPercentiles"] = [threshold]*20
                elif field == "minimumStrengthPercentile20":
                    row["trailing20DailyStrengthPercentiles"][0] = threshold
                else:
                    row[field] = threshold
                self.sync_archive(row)
                self.assertEqual(self.q(row)["qState"], "true")
                below = math.nextafter(threshold, -math.inf if op == ">=" else math.inf)
                if field == "meanStrengthPercentile20":
                    row["trailing20DailyStrengthPercentiles"] = [below]*20
                elif field == "minimumStrengthPercentile20":
                    row["trailing20DailyStrengthPercentiles"][0] = below
                else:
                    row[field] = below
                self.sync_archive(row)
                self.assertFalse(self.q(row)["predicates"][field]["passed"])

    @staticmethod
    def sync_archive(row):
        ranks = row["trailing20DailyStrengthPercentiles"]
        row.update(meanStrengthPercentile20=mean(ranks), minimumStrengthPercentile20=min(ranks),
                   relativeReturn20Percentile=ranks[-1])

    def test_archive_and_nonfinite_evidence_fail_closed(self):
        mutations = [
            lambda r: r["trailing20StrengthCrossSectionCounts"].__setitem__(0, 0),
            lambda r: r["trailing20StrengthCrossSectionCounts"].__setitem__(0, 7),
            lambda r: r["strengthRankSessionDates20"].__setitem__(-1, "2029-01-01"),
            lambda r: r["strengthRankSessionDates20"].__setitem__(0, r["strengthRankSessionDates20"][1]),
            lambda r: r.__setitem__("knownStrengthRankSessions20", True),
            lambda r: r.__setitem__("meanStrengthPercentile20", .99),
            lambda r: r.__setitem__("return60", float("inf")),
            lambda r: r.__setitem__("signedPathEfficiency20", float("nan")),
            lambda r: r.__setitem__("relativeReturn60Percentile", 1.01),
            lambda r: r.__setitem__("relativeReturn60CrossSectionCount", 0),
            lambda r: r["featureReasons"].__setitem__("signedPathEfficiency20", ["zeroPathDistance"]),
            lambda r: r.__setitem__("rawPriceBasis", "adjusted"),
            lambda r: r["completeness"]["path20"].__setitem__("validSessions", 20),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(case=i):
                row = panel_row("s0", self.dates[0])
                mutate(row)
                self.assertEqual(self.q(row)["qState"], "unknown")

    def test_price_amount_independent_populations_and_zero_breadth(self):
        rows = panel_rows(self.symbols, self.dates[0])
        for row, value in zip(rows, [-.2, 0, .4, .8, .9, 1.]):
            row["return20"] = value
        for i in (3, 4, 5):
            invalidate(rows[i], "security20")
        for i in (0, 1):
            invalidate(rows[i], "amountToday")
        rows[2]["amountRelativeToOwnPrior20"] = 3.  # contradictory amount ratio is unavailable
        result = MarketStateTracker(self.symbols, self.dates).update(self.dates[0], rows)
        price, amount = result["daily"]["price20"], result["daily"]["amount"]
        self.assertEqual((price["evaluableCount"], price["medianAbsoluteReturn20"], price["positiveReturn20Breadth"]), (3, 0, 1/3))
        self.assertEqual((price["negativeCount"], price["zeroCount"], price["positiveCount"]), (1, 1, 1))
        self.assertEqual((amount["evaluableCount"], amount["medianRelativeToOwnPrior20"]), (3, 2))
        self.assertTrue(result["roles"][5]["amount"]["evaluable"])
        self.assertFalse(result["roles"][5]["price20"]["evaluable"])

    def test_first_day_then_full_yesterday_cohort_and_challengers(self):
        tracker = MarketStateTracker(self.symbols, self.dates)
        first = tracker.update(self.dates[0], panel_rows(self.symbols, self.dates[0], ["true"]*3+["false", "unknown", "false"]))
        prior = first["daily"]["yesterdayCohort"]
        self.assertFalse(prior["previousCohortAvailable"])
        for key in ("members", "eligibleCount", "continuedCount", "observedFailureCount", "unknownCount", "observedCount", "conditionalContinuationRate"):
            self.assertIsNone(prior[key])
        self.assertEqual(first["roles"][0]["transition"], "priorStateUnknownEntrant")
        second = tracker.update(self.dates[1], panel_rows(self.symbols, self.dates[1], ["true", "false", "unknown", "true", "true", "false"]))
        c = second["daily"]["yesterdayCohort"]
        self.assertEqual(c["members"], self.symbols[:3])
        self.assertEqual((c["eligibleCount"], c["continuedCount"], c["observedFailureCount"], c["unknownCount"]), (3, 1, 1, 1))
        self.assertEqual((c["observedCount"], c["conditionalContinuationRate"], c["observedCoverage"]), (2, .5, 2/3))
        self.assertEqual((c["continuationLowerBound"], c["continuationUpperBound"]), (1/3, 2/3))
        self.assertEqual([r["transition"] for r in second["roles"]],
                         ["continued", "observedFailure", "unknown", "knownNewChallenger", "priorStateUnknownEntrant", "falseToFalse"])

    def test_empty_and_all_unknown_cohorts_are_not_market_states(self):
        tracker = MarketStateTracker(self.symbols, self.dates)
        first = tracker.update(self.dates[0], panel_rows(self.symbols, self.dates[0], ["unknown"]*6))
        self.assertTrue(first["daily"]["confirmedStrongEmpty"])
        self.assertEqual(first["daily"]["qUnknownCount"], 6)
        self.assertIsNone(first["daily"]["marketRegime"])
        second = tracker.update(self.dates[1], panel_rows(self.symbols, self.dates[1], ["false"]*6))
        c = second["daily"]["yesterdayCohort"]
        self.assertTrue(c["previousCohortAvailable"])
        self.assertEqual((c["members"], c["eligibleCount"], c["observedCount"]), ([], 0, 0))
        self.assertIsNone(c["conditionalContinuationRate"])
        self.assertIsNone(c["continuationUpperBound"])

    def test_fifth_prior_session_uses_fixed_intersection(self):
        tracker = MarketStateTracker(self.symbols, self.dates)
        for i, day in enumerate(self.dates[:6]):
            rows = panel_rows(self.symbols, day)
            if i == 0:
                for row, value in zip(rows, [.1, -.2, 0, 4, 4, 4]):
                    row["return20"] = value
                for j in (3, 4, 5):
                    invalidate(rows[j], "security20")
            if i == 5:
                for row, value in zip(rows, [-.1, .4, .5, .8, .8, .8]):
                    row["return20"] = value
                invalidate(rows[0], "security20")
            result = tracker.update(day, rows)
            if i < 5:
                self.assertFalse(result["daily"]["matched5"]["available"])
                self.assertIsNone(result["daily"]["matched5"]["pairedCount"])
        pair = result["daily"]["matched5"]
        self.assertEqual(pair["earlierDate"], self.dates[0])
        self.assertEqual((pair["todayEvaluableCount"], pair["earlierEvaluableCount"], pair["pairedCount"]), (5, 3, 2))
        self.assertEqual((pair["positiveBreadthToday"], pair["positiveBreadthEarlier"], pair["matchedBreadthChange5"]), (1, 0, 100))
        self.assertAlmostEqual(pair["matchedMedianReturnChange5"], .55)
        self.assertEqual((pair["pairedCoverageToday"], pair["pairedCoverageEarlier"], pair["pairedCoverageFixed"]), (2/5, 2/3, 2/6))
        self.assertEqual((pair["unpairedTodayCount"], pair["unpairedEarlierCount"]), (3, 1))
        self.assertFalse(result["roles"][0]["matched5"]["paired"])

    def test_empty_intersection_is_distinct_from_absent_endpoint(self):
        tracker = MarketStateTracker(self.symbols, self.dates)
        for i, day in enumerate(self.dates[:6]):
            rows = panel_rows(self.symbols, day)
            for j in (range(3) if i == 0 else range(3, 6)):
                invalidate(rows[j], "security20")
            result = tracker.update(day, rows)
        pair = result["daily"]["matched5"]
        self.assertTrue(pair["available"])
        self.assertEqual(pair["pairedCount"], 0)
        self.assertEqual(pair["pairedCoverageFixed"], 0)
        self.assertIsNone(pair["matchedBreadthChange5"])
        self.assertEqual(pair["reason"], "emptyMatchedPopulation")

    def test_order_constraints_and_failed_update_is_transactional(self):
        with self.assertRaises(ValueError):
            MarketStateTracker(["b", "a"], self.dates)
        with self.assertRaises(ValueError):
            MarketStateTracker(self.symbols, [self.dates[0]]*2)
        tracker = MarketStateTracker(self.symbols, self.dates)
        rows = panel_rows(self.symbols, self.dates[0])
        for day, bad_rows in [(self.dates[1], rows), (self.dates[0], rows[:-1]),
                              (self.dates[0], list(reversed(rows))), (self.dates[0], rows[:-1]+[rows[0]])]:
            with self.assertRaises(ValueError):
                tracker.update(day, bad_rows)
        result = tracker.update(self.dates[0], rows)
        self.assertEqual(len(result["roles"]), 6)
        with self.assertRaises(ValueError):
            tracker.update(self.dates[0], rows)
        future = panel_rows(self.symbols, self.dates[1])
        future[3]["asOfDate"] = self.dates[2]
        with self.assertRaises(ValueError):
            tracker.update(self.dates[1], future)
        self.assertEqual(tracker.update(self.dates[1], panel_rows(self.symbols, self.dates[1]))["daily"]["date"], self.dates[1])

    def test_input_output_isolation_and_future_extension_invariance(self):
        original = panel_rows(self.symbols, self.dates[0])
        saved = deepcopy(original)
        short, long = MarketStateTracker(self.symbols, self.dates[:6]), MarketStateTracker(self.symbols, self.dates)
        first = short.update(self.dates[0], original)
        self.assertEqual(first, long.update(self.dates[0], deepcopy(original)))
        self.assertEqual(original, saved)
        first["roles"][0]["criterionValues"]["signedPathEfficiency20"] = -1
        first["roles"][0]["price20"]["return20"] = 100
        first["roles"][0]["evidence"]["windowComplete"]["security20"] = False
        first["roles"][0]["baseEligibility"]["unknown"].clear()
        original[0]["return20"] = 200
        for i, day in enumerate(self.dates[1:6], 1):
            a = short.update(day, panel_rows(self.symbols, day))
            b = long.update(day, panel_rows(self.symbols, day))
            self.assertEqual(a, b)
        self.assertEqual(a["roles"][0]["matched5"]["earlier"]["return20"], .2)
        self.assertEqual(a["roles"][0]["priorCriterionValues"]["signedPathEfficiency20"], .5)

    def test_quality_reasons_overlap_and_independent_amount_survives(self):
        rows = panel_rows(self.symbols, self.dates[0])
        group = rows[0]["completeness"]["security20"]
        group.update(complete=False, validSessions=20, missingDates=[self.dates[0]],
                     invalidDates=[self.dates[0]], haltDates=[self.dates[0]], reasons=["missingDailyBar", "knownSuspension"])
        result = MarketStateTracker(self.symbols, self.dates).update(self.dates[0], rows)
        q = result["daily"]["quality"]["groups"]["security20"]
        self.assertEqual((q["completeCount"], q["missingCount"], q["invalidCount"], q["haltCount"], q["unknownTradeStatusCount"]), (5, 1, 1, 1, 6))
        self.assertEqual(result["daily"]["amount"]["evaluableCount"], 6)
        self.assertTrue(result["daily"]["quality"]["countsMayOverlap"])
        self.assertEqual(result["roles"][0]["baseEligibility"], rows[0]["baseEligibility"])

    def test_empty_metrics_and_safe_large_finite_median(self):
        rows = panel_rows(self.symbols, self.dates[0])
        for row in rows:
            invalidate(row, "security20")
            invalidate(row, "amountToday")
        empty = MarketStateTracker(self.symbols, self.dates).update(self.dates[0], rows)["daily"]
        self.assertEqual(empty["price20"]["evaluableCount"], 0)
        self.assertIsNone(empty["price20"]["medianAbsoluteReturn20"])
        self.assertIsNone(empty["price20"]["positiveReturn20Breadth"])
        self.assertIsNone(empty["amount"]["medianRelativeToOwnPrior20"])
        rows = panel_rows(self.symbols, self.dates[0])
        for row in rows:
            row.update(return20=1e308, amountToday=1e308, prior20MeanAmount=1., amountRelativeToOwnPrior20=1e308)
        large = MarketStateTracker(self.symbols, self.dates).update(self.dates[0], rows)
        self.assertEqual(large["daily"]["price20"]["medianAbsoluteReturn20"], 1e308)
        self.assertEqual(large["daily"]["amount"]["medianRelativeToOwnPrior20"], 1e308)
        json.dumps(large, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
