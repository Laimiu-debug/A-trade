"""Synthetic fixed-slot tests only; no market files or trial outcomes."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
import unittest

from service.signal_market_repair import (CANDIDATE_ID, Q_DEFINITION, SOURCE_FEATURE_VERSION, VERSION,
                                         evaluate_market_repair, evaluate_resilient_gate)


DAYS = [(date(2022, 1, 3)+timedelta(days=i)).isoformat() for i in range(20)]
SYMBOL = "sh600000"


def market_rows(index=10, differences=None):
    differences = [-1., -2., -3., 2., 0., 1.] if differences is None else differences
    result = []
    start = max(0, index-5)
    for i in range(start, index+1):
        result.append({"date": DAYS[i], "featureVersion": SOURCE_FEATURE_VERSION,
            "price20": {"coverage": .9, "medianAbsoluteReturn20": -.8},
            "matched5": {"earlierDate": DAYS[i-5] if i >= 5 else None, "available": i >= 5,
                         "pairedCoverageFixed": .9 if i >= 5 else None,
                         "matchedBreadthChange5": differences[i-start] if i >= 5 else None}})
    return result


def roles(index=10):
    return [{"date": DAYS[i], "symbol": SYMBOL, "featureVersion": SOURCE_FEATURE_VERSION,
             "qDefinition": Q_DEFINITION, "qState": "true"} for i in range(max(0, index-5), index)]


class MarketRepairTests(unittest.TestCase):
    def market(self, rows=None, index=10):
        return evaluate_market_repair(DAYS, index, market_rows(index) if rows is None else rows)

    def compound(self, rows=None, gate=None, index=10):
        return evaluate_resilient_gate(SYMBOL, DAYS, index, roles(index) if rows is None else rows,
                                      self.market(index=index) if gate is None else gate)

    def test_complete_nonconsecutive_three_contractions_and_exact_coverage_pass(self):
        value = self.market(market_rows(differences=[-1, 2, -1, -1, 0, 1]))
        self.assertEqual(value["marketGate"], "true")
        self.assertTrue(value["newEntryAllowed"])
        self.assertEqual(value["predicates"]["priorFiveNegativeCount"]["value"], 3)
        self.assertEqual(len(value["predicates"]), 27)
        self.assertEqual(len(value["history"]), 6)
        self.assertEqual(self.compound(gate=value)["signalGate"], "true")
        self.assertFalse(value["certified"])
        self.assertFalse(value["targetAchieved"])

    def test_zero_not_negative_and_preceding_zero_is_allowed(self):
        for values, state, count in (([-1, -1, 0, 2, 0, 1], "false", 2),
                                      ([-1, -1, -1, 0, 0, 1], "true", 3),
                                      ([-1, -1, -1, 0, 0, 0], "false", 3)):
            with self.subTest(values=values):
                result = self.market(market_rows(differences=values))
                self.assertEqual(result["marketGate"], state)
                self.assertEqual(result["predicates"]["priorFiveNegativeCount"]["value"], count)

    def test_prior_nonpositive_and_current_positive_are_separate_requirements(self):
        for values, failure in (([-1, -1, -1, 1, 1, 1], "previousNonPositive"),
                                ([-1, -1, -1, 1, 0, -1], "currentPositive")):
            result = self.market(market_rows(differences=values))
            self.assertEqual(result["marketGate"], "false")
            self.assertIn(failure, result["failedPredicates"])

    def test_all_six_days_require_both_coverage_thresholds_without_epsilon(self):
        for position in range(6):
            for group, name in (("price20", "coverage"), ("matched5", "pairedCoverageFixed")):
                value = market_rows()
                value[position][group][name] = math.nextafter(.9, -math.inf)
                result = self.market(value)
                self.assertEqual(result["marketGate"], "false")
                self.assertIn(DAYS[position+5]+":"+group+"."+name, result["failedPredicates"])
                value[position][group][name] = math.nextafter(.9, math.inf)
                self.assertEqual(self.market(value)["marketGate"], "true")

    def test_missing_nonfinite_boolean_and_nonnumeric_numbers_are_unknown(self):
        for group, name in (("price20", "coverage"), ("matched5", "pairedCoverageFixed"),
                            ("matched5", "matchedBreadthChange5")):
            for bad in (None, True, False, "0.9", [], {}, float("nan"), float("inf"), -float("inf"), 10**1000):
                with self.subTest(field=name, value_type=type(bad).__name__):
                    value = market_rows()
                    value[1][group][name] = bad
                    result = self.market(value)
                    self.assertEqual(result["marketGate"], "unknown")
                    json.dumps(result, allow_nan=False)
            value = market_rows()
            del value[1][group][name]
            self.assertEqual(self.market(value)["marketGate"], "unknown")

    def test_numeric_domains_are_not_clipped_and_diff_is_percentage_points(self):
        for group, name, value in (("price20", "coverage", -0.1), ("price20", "coverage", 1.01),
                                   ("matched5", "pairedCoverageFixed", -0.1), ("matched5", "pairedCoverageFixed", 1.01),
                                   ("matched5", "matchedBreadthChange5", -100.01),
                                   ("matched5", "matchedBreadthChange5", 100.01)):
            rows = market_rows()
            rows[2][group][name] = value
            self.assertEqual(self.market(rows)["marketGate"], "unknown")
        self.assertEqual(self.market(market_rows(differences=[-100, -100, -100, 0, 0, 100]))["marketGate"], "true")

    def test_available_false_or_not_boolean_is_unknown_on_every_day(self):
        for position in range(6):
            for bad in (False, None, 0, 1, "true", [], {}):
                value = market_rows()
                value[position]["matched5"]["available"] = bad
                result = self.market(value)
                self.assertEqual(result["marketGate"], "unknown")
                self.assertFalse(result["newEntryAllowed"])

    def test_unknown_overrides_market_failure_but_retains_known_evidence(self):
        value = market_rows()
        value[0]["price20"]["coverage"] = .8
        value[1]["matched5"]["matchedBreadthChange5"] = None
        result = self.market(value)
        self.assertEqual(result["marketGate"], "unknown")
        self.assertIn(DAYS[5]+":price20.coverage", result["failedPredicates"])
        self.assertTrue(result["predicates"]["currentPositive"]["passed"])
        self.assertIsNone(result["predicates"]["priorFiveNegativeCount"]["value"])

    def test_first_ten_target_indices_are_unknown_and_index_ten_can_pass(self):
        for index in range(10):
            result = self.market(index=index)
            self.assertEqual(result["marketGate"], "unknown")
            self.assertIn("firstTenTargetSessionsUnavailable", result["unknownReasons"])
            self.assertEqual(len(result["history"]), min(6, index+1))
            self.assertEqual(self.compound(index=index)["signalGate"], "unknown")
        self.assertEqual(self.market(index=10)["marketGate"], "true")

    def test_whole_row_gaps_extra_duplicate_and_wrong_order_are_integrity_failures(self):
        value = market_rows()
        variants = [value[:-1], value[1:], value+[deepcopy(value[-1])],
                    [value[0], value[0]]+value[2:], value[::-1], [None]+value[1:]]
        for rows in variants:
            with self.assertRaises(ValueError):
                self.market(rows)
        with self.assertRaises(ValueError):
            self.market([], index=0)

    def test_exact_earlier_dates_and_unavailable_start_cannot_be_forged(self):
        for invalid in (None, DAYS[1], DAYS[5], "20220103"):
            value = market_rows()
            value[0]["matched5"]["earlierDate"] = invalid
            with self.assertRaises(ValueError):
                self.market(value)
        value = market_rows()
        del value[0]["matched5"]["earlierDate"]
        with self.assertRaises(ValueError):
            self.market(value)
        value = market_rows(index=0)
        value[0]["matched5"]["available"] = True
        with self.assertRaises(ValueError):
            self.market(value, index=0)

    def test_calendar_index_date_and_feature_version_errors_are_not_unknown(self):
        for index in (-1, True, 1., "10", None, len(DAYS)):
            with self.assertRaises(ValueError):
                evaluate_market_repair(DAYS, index, market_rows())
        for calendar in ([], DAYS[::-1], DAYS[:5]+[DAYS[4]]+DAYS[6:], ["20220103"]+DAYS[1:]):
            with self.assertRaises(ValueError):
                evaluate_market_repair(calendar, 10, market_rows())
        for key, invalid in (("date", DAYS[0]), ("featureVersion", "other")):
            value = market_rows()
            value[0][key] = invalid
            with self.assertRaises(ValueError):
                self.market(value)

    def test_no_median_amount_current_q_or_future_feature_gate(self):
        rows = market_rows()
        saved = self.market(rows)
        for row in rows:
            row["price20"]["medianAbsoluteReturn20"] = None
            row.update(amount=None, qCounts={"true": 0}, futureReturn=-999, baseEligibility=False)
        self.assertEqual(self.market(rows), saved)

    def test_every_prior_role_must_be_true_even_on_noncontraction_days(self):
        for position in range(5):
            value = roles()
            value[position]["qState"] = "false"
            result = self.compound(value)
            self.assertEqual(result["signalGate"], "false")
            self.assertIn(DAYS[position+5]+":qState", result["failedPredicates"])
            self.assertEqual(result["marketGate"], "true")

    def test_role_unknown_overrides_other_role_false_and_market_false(self):
        value = roles()
        value[0]["qState"], value[1]["qState"] = "false", "unknown"
        market = market_rows()
        market[-1]["matched5"]["matchedBreadthChange5"] = 0
        result = self.compound(value, self.market(market))
        self.assertEqual(result["signalGate"], "unknown")
        self.assertIn(DAYS[5]+":qState", result["failedPredicates"])
        self.assertIn("currentPositive", result["failedPredicates"])
        self.assertIsNone(result["roleHistory"][1]["passed"])

    def test_market_unknown_overrides_complete_role_false(self):
        market = market_rows()
        market[2]["price20"]["coverage"] = None
        value = roles()
        value[0]["qState"] = "false"
        result = self.compound(value, self.market(market))
        self.assertEqual(result["signalGate"], "unknown")
        self.assertIn(DAYS[5]+":qState", result["failedPredicates"])

    def test_role_row_identity_state_enum_and_current_day_inclusion_fail(self):
        for key, invalid in (("symbol", "sz000001"), ("qDefinition", "other"), ("qState", True),
                             ("qState", None), ("qState", "TRUE"), ("featureVersion", "other")):
            value = roles()
            value[1][key] = invalid
            with self.assertRaises(ValueError):
                self.compound(value)
        value = roles()
        for rows in (value[:-1], value[1:], value[::-1], value+[dict(value[-1], date=DAYS[10])],
                     [value[0], value[0]]+value[2:]):
            with self.assertRaises(ValueError):
                self.compound(rows)

    def test_market_gate_identity_and_contradictory_state_fail(self):
        for key, invalid in (("date", DAYS[9]), ("targetIndex", True), ("targetIndex", 9),
                             ("version", "other"), ("candidateId", "other"), ("marketGate", False),
                             ("marketGate", "false"), ("newEntryAllowed", 1), ("certified", True),
                             ("targetAchieved", True), ("unknownReasons", ["invented"])):
            gate = self.market()
            gate[key] = invalid
            with self.assertRaises(ValueError):
                self.compound(gate=gate)

    def test_inputs_and_returned_nested_evidence_are_isolated(self):
        market, history = market_rows(), roles()
        original_market, original_history = deepcopy(market), deepcopy(history)
        gate = self.market(market)
        original_gate = deepcopy(gate)
        compound = self.compound(history, gate)
        compound["roleHistory"][0]["qState"] = "false"
        compound["failedPredicates"].append("mutated")
        self.assertEqual(gate, original_gate)
        gate["history"][0]["predicates"]["price20.coverage"]["value"] = 0
        gate["predicates"][DAYS[5]+":price20.coverage"]["value"] = .1
        self.assertEqual(market, original_market)
        self.assertEqual(history, original_history)
        self.assertEqual(self.compound()["signalGate"], "true")

    def test_appending_or_removing_future_calendar_preserves_past_result(self):
        full = evaluate_market_repair(DAYS, 10, market_rows())
        shorter = evaluate_market_repair(DAYS[:11], 10, market_rows())
        self.assertEqual(full, shorter)
        self.assertEqual(evaluate_resilient_gate(SYMBOL, DAYS, 10, roles(), full),
                         evaluate_resilient_gate(SYMBOL, DAYS[:11], 10, roles(), shorter))
        self.assertEqual((full["version"], full["candidateId"]), (VERSION, CANDIDATE_ID))


if __name__ == "__main__":
    unittest.main()
