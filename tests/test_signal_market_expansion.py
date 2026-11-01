"""Only synthetic daily rows; no panel reads, counts or strategy outcomes."""
from copy import deepcopy
import json
import math
import unittest

from service.signal_market_expansion import SOURCE_FEATURE_VERSION, evaluate_market_gate


DAY = "2022-01-10"


def row():
    return {"date": DAY, "featureVersion": SOURCE_FEATURE_VERSION,
            "price20": {"coverage": .90, "medianAbsoluteReturn20": .01},
            "matched5": {"available": True, "earlierDate": "2022-01-03",
                         "pairedCoverageFixed": .90, "matchedBreadthChange5": 1.}}


class MarketExpansionTests(unittest.TestCase):
    def evaluate(self, value=None, index=5):
        return evaluate_market_gate(DAY, index, row() if value is None else value)

    def test_all_conditions_and_exact_boundaries(self):
        result = self.evaluate()
        self.assertEqual(result["marketGate"], "true")
        self.assertTrue(result["newEntryAllowed"])
        self.assertEqual(len(result["predicates"]), 5)
        for group, field, threshold in (("price20", "coverage", .9), ("matched5", "pairedCoverageFixed", .9),
                                        ("price20", "medianAbsoluteReturn20", 0), ("matched5", "matchedBreadthChange5", 0)):
            with self.subTest(field=field):
                value = row()
                value[group][field] = math.nextafter(threshold, -math.inf) if threshold else 0
                result = self.evaluate(value)
                self.assertEqual(result["marketGate"], "false")
                self.assertIn(group+"."+field, result["failedPredicates"])
                self.assertFalse(result["newEntryAllowed"])
                value[group][field] = math.nextafter(threshold, math.inf)
                self.assertEqual(self.evaluate(value)["marketGate"], "true")

    def test_unknown_precedes_known_failure_and_preserves_predicates(self):
        value = row()
        value["price20"]["coverage"] = .8
        del value["price20"]["medianAbsoluteReturn20"]
        result = self.evaluate(value)
        self.assertEqual(result["marketGate"], "unknown")
        self.assertEqual(result["failedPredicates"], ["price20.coverage"])
        self.assertTrue(result["predicates"]["matched5.pairedCoverageFixed"]["passed"])
        self.assertIsNone(result["predicates"]["price20.medianAbsoluteReturn20"]["passed"])

    def test_nonfinite_nonnumeric_bool_and_bad_groups_unknown(self):
        fields = (("price20", "coverage"), ("matched5", "pairedCoverageFixed"),
                  ("price20", "medianAbsoluteReturn20"), ("matched5", "matchedBreadthChange5"))
        for group, field in fields:
            for bad in (None, True, False, "0.99", [], {}, float("nan"), float("inf"), -float("inf"), 10**1000):
                with self.subTest(field=field, bad_type=type(bad).__name__):
                    value = row()
                    value[group][field] = bad
                    result = self.evaluate(value)
                    self.assertEqual(result["marketGate"], "unknown")
                    self.assertIsNone(result["predicates"][group+"."+field]["passed"])
                    json.dumps(result, allow_nan=False)
        for group in ("price20", "matched5"):
            for bad in (None, [], "unknown"):
                value = row()
                value[group] = bad
                self.assertEqual(self.evaluate(value)["marketGate"], "unknown")

    def test_available_requires_strict_boolean_and_false_means_unknown(self):
        for bad in (None, 0, 1, "true", "false", False):
            value = row()
            value["matched5"]["available"] = bad
            result = self.evaluate(value)
            self.assertEqual(result["marketGate"], "unknown")
            self.assertFalse(result["newEntryAllowed"])
            if bad is False:
                self.assertFalse(result["predicates"]["matched5.available"]["passed"])
                self.assertIn("matched5.available", result["failedPredicates"])

    def test_ranges_and_percentage_point_units(self):
        for group, field, bad in (("price20", "coverage", -0.01), ("price20", "coverage", 1.01),
                                 ("matched5", "pairedCoverageFixed", -0.01), ("matched5", "pairedCoverageFixed", 1.01),
                                 ("price20", "medianAbsoluteReturn20", math.nextafter(-1., -math.inf)),
                                 ("matched5", "matchedBreadthChange5", 100.01), ("matched5", "matchedBreadthChange5", -100.01)):
            value = row()
            value[group][field] = bad
            self.assertEqual(self.evaluate(value)["marketGate"], "unknown")
        for amount, expected in ((100, "true"), (2, "true"), (-100, "false")):
            value = row()
            value["matched5"]["matchedBreadthChange5"] = amount
            self.assertEqual(self.evaluate(value)["marketGate"], expected)
        value = row()
        value["price20"]["medianAbsoluteReturn20"] = -1
        self.assertEqual(self.evaluate(value)["marketGate"], "false")
        value["price20"]["medianAbsoluteReturn20"] = 1e308
        self.assertEqual(self.evaluate(value)["marketGate"], "true")

    def test_first_five_are_unknown_and_sixth_can_pass(self):
        for index in range(5):
            result = self.evaluate(index=index)
            self.assertEqual(result["marketGate"], "unknown")
            self.assertIn("firstFiveTargetSessionsUnavailable", result["unknownReasons"])
        value = row()
        value["matched5"].update(available=False, earlierDate=None, pairedCoverageFixed=None, matchedBreadthChange5=None)
        self.assertEqual(self.evaluate(value, index=0)["marketGate"], "unknown")
        self.assertEqual(self.evaluate(index=5)["marketGate"], "true")

    def test_date_version_and_caller_index_are_integrity_errors(self):
        for index in (-1, True, 1., "5", None):
            with self.assertRaises(ValueError):
                self.evaluate(index=index)
        for key, invalid in (("date", "2022-01-11"), ("date", None), ("featureVersion", "other")):
            value = row()
            value[key] = invalid
            with self.assertRaises(ValueError):
                self.evaluate(value)
        for prior in (DAY, "2022-01-11", "garbage", "20220103", None):
            value = row()
            value["matched5"]["earlierDate"] = prior
            with self.assertRaises(ValueError):
                self.evaluate(value)
        with self.assertRaises(ValueError):
            evaluate_market_gate("20220110", 5, row())

    def test_input_isolation_and_irrelevant_fields_do_not_gate(self):
        value = row()
        value.update(baseEligibility=False, amount=None, qCounts={"true": 0, "unknown": 3141}, futureReturn=-999)
        saved = deepcopy(value)
        result = self.evaluate(value)
        self.assertEqual(value, saved)
        self.assertEqual(result, self.evaluate())
        result["predicates"]["price20.coverage"]["value"] = 0
        result["unknownReasons"].append("tampered")
        self.assertEqual(value, saved)
        self.assertEqual(self.evaluate(value)["marketGate"], "true")


if __name__ == "__main__":
    unittest.main()
