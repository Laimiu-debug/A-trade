import copy
from datetime import date, timedelta
import math
import json
import unittest

from service.research_target import summarize_result


def result_fixture(days=730, daily_growth=1.001):
    start = date(2022, 1, 1)
    rows, equity = [], 100000.0
    for i in range(days):
        day = start + timedelta(days=i)
        if day.weekday() < 5:
            equity *= daily_growth
            rows.append({"date": day.isoformat(), "equity": equity, "marketValue": equity / 2})
    return {"status": "completed", "fingerprint": "synthetic-target-fixture", "dataDigest": "synthetic-target-digest",
            "config": {"initialCapital": 100000, "startDate": start.isoformat(),
            "endDate": (start + timedelta(days=days - 1)).isoformat()},
            "metrics": {"initialCapital": 100000, "finalEquity": equity, "annualizedReturnPct": 999.0,
                        "totalFees": 500, "totalSlippage": 200}, "curve": rows,
            "closedTrades": [{"symbol": f"fixture-{i}", "entryDate": row["date"], "exitDate": rows[i + 4]["date"],
                              "pnlAmount": 10, "holdingDays": 5} for i, row in enumerate(rows[:min(100,max(0,len(rows)-4))])],
            "quality": {"certified": True, "calendarIndependent": True, "pointInTimeComplete": True,
                        "corporateActionsComplete": True, "openingCapacityVerified": True,
                        "staleValuationDays": 0, "quarantinedSymbols": [], "unaccountedActionExposure": [],
                        "executionCapacityIssues": [], "unverifiedOpeningFills": []}, "positions": []}


class ResearchTargetTests(unittest.TestCase):
    def test_recomputes_calendar_growth_not_reported_annualization_or_cost_deduction(self):
        result = result_fixture(daily_growth=1.002)
        summary = summarize_result(result)
        days = (date.fromisoformat(result["curve"][-1]["date"]) - date.fromisoformat(result["curve"][0]["date"])).days + 1
        expected = ((result["metrics"]["finalEquity"] / 100000) ** (365.2425 / days) - 1) * 100
        self.assertAlmostEqual(summary["calendarCagrPct"], expected)
        self.assertEqual(summary["finalEquity"], result["metrics"]["finalEquity"])
        self.assertEqual(summary["avgExposurePct"], 50)
        self.assertNotEqual(summary["calendarCagrPct"], 999)

    def test_high_short_run_never_meets_goal(self):
        summary = summarize_result(result_fixture(days=30, daily_growth=1.02), stage="final_candidate")
        self.assertTrue(summary["goalAssessment"]["numericalTargetReached"])
        self.assertIn("insufficient-calendar-span", summary["goalAssessment"]["blockers"])
        self.assertFalse(summary["goalAssessment"]["targetAchieved"])

    def test_development_pilot_and_incomplete_data_cannot_be_acceptance(self):
        result = result_fixture(days=1100, daily_growth=1.002)
        result["quality"]["pointInTimeComplete"] = False
        summary = summarize_result(result, pilot=True)
        blockers = summary["goalAssessment"]["blockers"]
        for blocker in ("pilot-universe-is-not-acceptance-evidence", "not-a-frozen-final-candidate", "historical-universe-incomplete"):
            self.assertIn(blocker, blockers)
        self.assertFalse(summary["goalAssessment"]["targetAchieved"])

    def test_even_clean_numerical_candidate_requires_independent_review(self):
        summary = summarize_result(result_fixture(days=1100, daily_growth=1.002), stage="final_candidate")
        self.assertTrue(summary["goalAssessment"]["eligibleForIndependentReview"])
        self.assertFalse(summary["goalAssessment"]["targetAchieved"])
        self.assertTrue(summary["goalAssessment"]["remainingEvidence"])

    def test_drawdown_includes_initial_capital_and_is_recomputed(self):
        result = result_fixture(days=10)
        result["curve"][0]["equity"] = 60000
        result["metrics"]["maxDrawdownPct"] = 0
        self.assertAlmostEqual(summarize_result(result)["maxDrawdownPct"], 40)

    def test_year_return_uses_prior_year_terminal_equity(self):
        summary = summarize_result(result_fixture(days=1100))
        first, second = summary["annualReturns"][:2]
        self.assertEqual(second["startingEquity"], first["finalEquity"])
        self.assertAlmostEqual(math.prod(1 + row["returnPct"] / 100 for row in summary["annualReturns"]),
                               summary["finalEquity"] / summary["initialCapital"])
        self.assertFalse(summary["annualReturns"][-1]["fullCalendarYear"])

    def test_bad_or_incomplete_curves_raise_instead_of_producing_metrics(self):
        original = result_fixture(days=20)
        for mutation in (lambda r: r["curve"].append(r["curve"][-1]),
                         lambda r: r["curve"].pop(),
                         lambda r: r["curve"][0].update(equity=float("nan"))):
            result = copy.deepcopy(original)
            mutation(result)
            with self.assertRaises(ValueError):
                summarize_result(result)

    def test_unobserved_calendar_cannot_claim_full_years(self):
        result = result_fixture(days=1100)
        result["quality"]["calendarIndependent"] = False
        summary = summarize_result(result)
        self.assertFalse(any(row["fullCalendarYear"] for row in summary["annualReturns"]))

    def test_exact_252_session_boundary_does_not_pass_calendar_cagr_target(self):
        result = result_fixture(days=733)
        first, last = date(2022, 1, 3), date(2024, 1, 2)
        weekdays = [first + timedelta(days=i) for i in range((last-first).days+1)
                    if (first+timedelta(days=i)).weekday()<5]
        selected = [weekdays[i*(len(weekdays)-1)//483] for i in range(484)]
        result["curve"] = [{"date": day.isoformat(), "equity": 100000*1.66**((i+1)/484),
                            "marketValue": 0} for i,day in enumerate(selected)]
        result["metrics"].update(finalEquity=166000, annualizedReturnPct=(1.66**(252/484)-1)*100)
        result["closedTrades"] = []
        summary = summarize_result(result,stage="final_candidate")
        self.assertAlmostEqual(summary["engineTradingDayAnnualizedPct"],30.197226178870864)
        self.assertEqual(summary["period"]["calendarDaysInclusive"],730)
        self.assertAlmostEqual(summary["calendarCagrPct"],(1.66**(365.2425/730)-1)*100)
        self.assertLess(summary["calendarCagrPct"],30)
        self.assertFalse(summary["goalAssessment"]["numericalTargetReached"])
        self.assertIn("insufficient-trading-days",summary["goalAssessment"]["blockers"])
        self.assertIn("return-or-drawdown-target-not-reached",summary["goalAssessment"]["blockers"])

    def test_declared_years_do_not_replace_actual_span_or_boundary_observations(self):
        short = result_fixture(days=30,daily_growth=1.02)
        short["config"]["endDate"] = "2026-12-31"
        summary = summarize_result(short,stage="final_candidate")
        self.assertLess(summary["period"]["calendarDaysInclusive"],30)
        self.assertIn("insufficient-calendar-span",summary["goalAssessment"]["blockers"])
        truncated = result_fixture(days=1461,daily_growth=1.002)
        truncated["curve"] = [row for row in truncated["curve"] if row["date"][5:7]>="03"]
        truncated["closedTrades"] = []
        summary = summarize_result(truncated,stage="final_candidate")
        self.assertTrue(all(row["tradingDays"]>=200 for row in summary["annualReturns"]))
        self.assertFalse(any(row["fullCalendarYear"] for row in summary["annualReturns"]))
        self.assertIn("insufficient-positive-full-calendar-years",summary["goalAssessment"]["blockers"])
        self.assertIn("notIndependentCalendarCompletenessProof",summary["fullCalendarYearScreenDefinition"])

    def test_missing_quality_checks_and_contradictory_opening_evidence_fail_closed(self):
        for key in ("staleValuationDays","quarantinedSymbols","unaccountedActionExposure",
                    "executionCapacityIssues","unverifiedOpeningFills"):
            result = result_fixture(days=1100,daily_growth=1.002)
            del result["quality"][key]
            summary = summarize_result(result,stage="final_candidate")
            self.assertIn("quality-checks-missing",summary["goalAssessment"]["blockers"])
            self.assertFalse(summary["goalAssessment"]["eligibleForIndependentReview"])
        result = result_fixture(days=1100,daily_growth=1.002)
        result["quality"]["unverifiedOpeningFills"] = [{"reason":"no-auction-volume"}]
        self.assertIn("unverified-opening-fills",summarize_result(result,stage="final_candidate")["goalAssessment"]["blockers"])

    def test_missing_source_or_cost_evidence_cannot_be_review_candidate(self):
        for location,key,blocker in (("metrics","totalFees","cost-metrics-missing"),
                                     (None,"dataDigest","source-identity-missing")):
            result = result_fixture(days=1100,daily_growth=1.002)
            del (result[location] if location else result)[key]
            summary = summarize_result(result,stage="final_candidate")
            self.assertIn(blocker,summary["goalAssessment"]["blockers"])
            self.assertFalse(summary["goalAssessment"]["eligibleForIndependentReview"])

    def test_initial_capital_and_trade_evidence_are_not_silently_inferred(self):
        original = result_fixture(days=1100)
        mutations = (lambda r:r["metrics"].update(initialCapital=10000),
                     lambda r:r["closedTrades"].append(copy.deepcopy(r["closedTrades"][0])),
                     lambda r:r["closedTrades"][0].update(entryDate="2021-01-01"),
                     lambda r:r["closedTrades"][0].update(holdingDays=99),
                     lambda r:r["closedTrades"][0].update(pnlAmount=float("nan")),
                     lambda r:r["quality"].update(staleValuationDays=False),
                     lambda r:r["metrics"].update(totalFees=float("inf")))
        for mutate in mutations:
            result = copy.deepcopy(original);mutate(result)
            with self.assertRaises(ValueError):
                summarize_result(result)
        result = result_fixture(days=20)
        result["metrics"]["initialCapital"] = .000001
        result["config"]["initialCapital"] = .009
        with self.assertRaises(ValueError):
            summarize_result(result)

    def test_missing_or_malformed_result_structures_raise_clear_value_errors(self):
        original = result_fixture(days=20)
        for field in ("curve","closedTrades","positions"):
            result = copy.deepcopy(original);del result[field]
            with self.assertRaises(ValueError):
                summarize_result(result)
        for field in ("metrics","config","quality"):
            result = copy.deepcopy(original);result[field]=[]
            with self.assertRaises(ValueError):
                summarize_result(result)
        for invalid in (None,[],{},True):
            with self.assertRaises(ValueError):
                summarize_result(invalid)

    def test_extreme_finite_equities_do_not_crash_or_serialize_infinity(self):
        for initial,final in ((1e308,5e-324),(.000001,1e308)):
            result = result_fixture(days=20)
            result["config"]["initialCapital"] = initial
            result["metrics"].update(initialCapital=initial,finalEquity=final)
            result["curve"] = [{"date":"2022-01-03","equity":initial,"marketValue":0},
                               {"date":"2022-01-04","equity":final,"marketValue":0}]
            result["closedTrades"] = []
            summary = summarize_result(result,stage="final_candidate")
            json.dumps(summary,allow_nan=False)
            self.assertFalse(summary["goalAssessment"]["eligibleForIndependentReview"])
            self.assertFalse(summary["goalAssessment"]["targetAchieved"])
        result = result_fixture(days=20);result["metrics"]["initialCapital"]=10**1000
        with self.assertRaises(ValueError):
            summarize_result(result)

    def test_input_is_not_mutated(self):
        result = result_fixture()
        before = copy.deepcopy(result)
        summarize_result(result)
        self.assertEqual(result, before)


if __name__ == "__main__":
    unittest.main()
