"""Synthetic checks for the independent leader diagnostic auditor only."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/verify-leader-incremental-prediction.py"
SPEC = importlib.util.spec_from_file_location("leader_prediction_independent_reference_tests", PATH)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def fixture(*args, **kwargs):
    return V.synthetic_fixture(*args, **kwargs)


def build(data, forms, start=None, end=None):
    return V.reference_build(data, forms, start=start or forms[0]["formationDate"], end=end or data["calendar"][-1])


class IndependentPredictionTests(unittest.TestCase):
    def test_sum_loss_orthogonal_hand_solution(self):
        x = [[1.0 if i % 2 else -1.0, 1.0 if i % 4 < 2 else -1.0, 7.0] for i in range(60)]
        y = [.1 + .2 * r[0] - .3 * r[1] for r in x]
        model = V.ridge_reference(x, y, V.FEATURES[:3])
        self.assertAlmostEqual(model["intercept"], .1)
        self.assertAlmostEqual(model["beta"][0], .2 * 60 / 61)
        self.assertAlmostEqual(model["beta"][1], -.3 * 60 / 61)
        self.assertEqual(model["beta"][2], 0)
        self.assertEqual(model["constantColumns"], ["index"])
        self.assertEqual(model["normalMatrix"][0][0], 61)
        self.assertNotAlmostEqual(model["beta"][0], .1)  # mean-loss lambda1 would halve this slope.

    def test_collinearity_and_pivoted_system(self):
        x = [[(-1.0) ** i, (-1.0) ** i, 2.0] for i in range(60)]
        model = V.ridge_reference(x, [.2 * row[0] for row in x], V.FEATURES[:3])
        self.assertAlmostEqual(model["beta"][0], .2 * 60 / 121)
        self.assertAlmostEqual(model["beta"][1], .2 * 60 / 121)
        self.assertEqual(V.solve_reference([[0., 1.], [2., 3.]], [2., 8.]), [1., 2.])

    def test_duplicate_own_column_changes_penalty_not_information(self):
        x = [[(-1.0) ** i, 2.0, 3.0] for i in range(60)]
        y = [.3 * row[0] for row in x]
        base = V.ridge_reference(x, y, V.FEATURES[:3])
        added = V.ridge_reference([row + [row[0]] for row in x], y, V.FEATURES)
        point = {"own": 1.0, "peerMean": 2.0, "index": 3.0, "anchor": 1.0}
        p0, p1 = V.prediction_reference(base, point), V.prediction_reference(added, point)
        self.assertAlmostEqual(p0, .3 * 60 / 61)
        self.assertAlmostEqual(p1, .3 * 120 / 121)
        self.assertGreater(p1, p0)
        self.assertLess((p1 - .3) ** 2, (p0 - .3) ** 2)

    def test_exact_constant_and_near_constant_are_different(self):
        x = [[2.0, 1.0 + (i % 2) * 1e-12, 4.0] for i in range(60)]
        model = V.ridge_reference(x, [0.] * 60, V.FEATURES[:3])
        self.assertEqual(model["constantColumns"], ["own", "index"])
        self.assertGreater(model["scales"][1], 0)
        self.assertEqual(V.prediction_reference(model, dict(zip(V.FEATURES, [1e9, 1., 1e9, 0.]))), 0)
        with self.assertRaises(AssertionError):
            V.ridge_reference(x[:-1], [0.] * 59, V.FEATURES[:3])

    def test_training_has_62_prices_and_60_pairs(self):
        data, forms, start, end = fixture()
        saved = deepcopy(data), deepcopy(forms)
        result = build(data, forms)
        fit = result["fits"][0]
        f = data["calendar"].index(forms[0]["peerFormationDate"])
        self.assertEqual(fit["trainingPriceDates"], data["calendar"][f - 61:f + 1])
        self.assertEqual(fit["trainingTargetDates"][-1], forms[0]["peerFormationDate"])
        self.assertEqual(fit["trainingFeatureDates"][-1], data["calendar"][f - 1])
        self.assertEqual(fit["knownPairs"], 60)
        self.assertEqual(len(result["fits"]), 20)
        self.assertEqual(len(result["rows"]), 100)
        self.assertEqual((data, forms), saved)

    def test_missing_first_required_price_invalidates_common_fit(self):
        data, forms, _, _ = fixture()
        del data["securities"][0]["bars"][4]  # F65 -> earliest required close4; anchor is common support.
        result = build(data, forms)
        self.assertTrue(all(f["fitStatus"] == "fitUnknown" for f in result["fits"]))
        self.assertEqual(result["fits"][0]["knownPairs"], 59)
        self.assertEqual(result["fits"][0]["models"], {"baseline": None, "augmented": None})
        self.assertTrue(result["rows"][0]["actualReturn"] is not None)

    def test_no_future_fit_update(self):
        data, forms, _, _ = fixture()
        expected = build(data, forms)
        for bar in data["securities"][0]["bars"][66:]:
            for field in ("open", "high", "low", "close"):
                bar[field] *= 2
        changed = build(data, forms)
        self.assertEqual(changed["fits"], expected["fits"])

    def test_fixed19_leaves_out_member_and_anchor(self):
        data, forms, _, _ = fixture()
        result = build(data, forms)
        row = result["rows"][0]
        at = data["calendar"].index(row["forecastDate"])
        peers = data["securities"][2:21]
        expected = math.fsum(s["bars"][at]["close"] / s["bars"][at - 1]["close"] - 1 for s in peers) / 19
        self.assertEqual(row["features"]["peerMean"], expected)
        del data["securities"][2]["bars"][at]
        missing = build(data, forms)["rows"][0]
        self.assertIsNone(missing["features"]["peerMean"])
        self.assertEqual(missing["predictionStatus"], "featureUnknown")

    def test_label_only_unknown_preserves_prediction(self):
        data, forms, _, _ = fixture()
        known = build(data, forms)["rows"][0]
        del data["securities"][1]["bars"][76]
        row = build(data, forms)["rows"][0]
        self.assertEqual(row["predictions"], known["predictions"])
        self.assertEqual(row["predictionStatus"], "known")
        self.assertEqual(row["targetStatus"], "targetUnknown")
        self.assertEqual(row["scoreStatus"], "targetUnknown")
        self.assertEqual(row["errors"], V.empty_errors())

    def test_future_target_perturbation_cannot_change_earlier_prediction(self):
        data, forms, _, _ = fixture()
        known = build(data, forms)["rows"][0]
        for field in ("open", "high", "low", "close"):
            data["securities"][1]["bars"][76][field] *= 1.1
        changed = build(data, forms)["rows"][0]
        self.assertEqual(changed["predictions"], known["predictions"])
        self.assertNotEqual(changed["actualReturn"], known["actualReturn"])

    def test_end_censor_and_numeric_future_invariance(self):
        data, forms, _, _ = fixture()
        end = data["calendar"][76]
        first = build(data, forms, end=end)
        rows = first["rows"][:5]
        self.assertEqual([r["scoreStatus"] for r in rows], ["scored", "targetBeyondEnd", "forecastBeyondEnd", "forecastBeyondEnd", "forecastBeyondEnd"])
        self.assertEqual(rows[1]["predictionStatus"], "known")
        for s in data["securities"] + [data["benchmark"]]:
            for bar in s["bars"][77:]:
                bar["close"] = float("nan")
        self.assertEqual(first, build(data, forms, end=end))
        truncated = deepcopy(data)
        truncated["calendar"] = truncated["calendar"][:77]
        for s in truncated["securities"] + [truncated["benchmark"]]:
            s["bars"] = s["bars"][:77]
        self.assertEqual(first, build(truncated, forms, end=end))

    def test_unknown_current_anchor_is_joint_not_baseline_only(self):
        data, forms, _, _ = fixture()
        data["securities"][0]["tradeStatusByDate"] = {data["calendar"][75]: 0.0}
        result = build(data, forms)
        self.assertEqual(result["fits"][0]["fitStatus"], "known")
        self.assertEqual(result["rows"][0]["predictions"], {"baseline": None, "augmented": None})
        self.assertIn("anchor:" + data["calendar"][75] + ":knownSuspension", result["rows"][0]["featureReasons"])

    def test_insufficient_calendar_preserves_all_slots(self):
        data, forms, _, _ = fixture(sessions=40, formation_index=20)
        result = build(data, forms)
        self.assertEqual(len(result["rows"]), 100)
        self.assertEqual(len(result["fits"][0]["trainingPairs"]), 60)
        self.assertIsNone(result["fits"][0]["trainingPriceDates"][0])
        self.assertTrue(all(f["fitStatus"] == "fitUnknown" for f in result["fits"]))

    def test_locked_notformed_denominator_and_original_order(self):
        data, forms, _, _ = fixture()
        for offset, state in ((1, "locked"), (2, "notFormed")):
            row = deepcopy(forms[0])
            row.update(formationDate=data["calendar"][75 + offset], peerFormationDate=data["calendar"][65 + offset], status=state)
            row["parentEventId"] = f"leaderLagFollowersV1:{row['formationDate']}:{row['anchorSymbol']}"
            forms.append(row)
        result = build(data, forms)
        self.assertEqual(result["formations"], forms)
        self.assertEqual(result["counts"]["formationStatusCounts"], {"formed": 1, "locked": 1, "notFormed": 1})
        self.assertEqual(len(result["fits"]), 20)
        bad = deepcopy(forms); bad[0]["members"][1]["frozenMemberRank"] = 1
        with self.assertRaises(AssertionError):
            build(data, bad)

    def test_loss_and_boundary_and_corruption_detection(self):
        loss = V.loss_reference({"baseline": .1, "augmented": .2}, .3)
        self.assertAlmostEqual(loss["mseDelta"], -.03)
        self.assertAlmostEqual(loss["maeDelta"], -.1)
        self.assertAlmostEqual(loss["zero"]["squared"], .09)
        data, forms, start, end = fixture()
        # fixture activation is September23, target24.
        actual = build(data, forms)
        self.assertTrue(actual["rows"][0]["crosses20240924"])
        self.assertEqual(actual["rows"][0]["forecastDate"], "2024-09-23")
        V.verify_group(data, forms, actual, start=start, end=end)
        actual["fits"][0]["models"]["augmented"]["beta"][0] += .01
        with self.assertRaises(AssertionError):
            V.verify_group(data, forms, actual, start=start, end=end)

    def test_execution_receipts_bind_tests_before_fit(self):
        hashes = {"plan": "plan", "tests": "tests", "run": "run"}
        plan = {"registeredAt": "2026-10-17T00:00:00+00:00", "newAccounts": 0, "reusedAccounts": 0}
        checks = [{"argv": ["python", "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"],
                   "exitCode": 0, "stdout": "", "stderr": "\nRan 1 test\n\nOK\n"}
                  for name in ("test_research_leader_incremental_prediction.py", "test_leader_incremental_prediction_verifier.py",
                               "test_leader_incremental_prediction_readout.py")]
        tests = {"passed": True, "completedAt": "2026-10-17T00:01:00+00:00", "planSha256": "plan", "checks": checks,
                 "realDataReads": 0, "accountExecutions": 0}
        run = {"startedAt": "2026-10-17T00:02:00+00:00", "planSha256": "plan", "preExecutionTestsSha256": "tests", "newAccounts": 0}
        completion = {"passed": True, "completedAt": "2026-10-17T00:03:00+00:00", "planSha256": "plan",
                      "preExecutionTestsSha256": "tests", "runStartSha256": "run", "newAccounts": 0, "reusedAccounts": 0}
        V.lifecycle_reference(plan, tests, run, completion, hashes)
        bad = deepcopy(completion); bad["runStartSha256"] = "other"
        with self.assertRaises(AssertionError):
            V.lifecycle_reference(plan, tests, run, bad, hashes)
        bad = deepcopy(run); bad["startedAt"] = "2026-10-17T00:00:30+00:00"
        with self.assertRaises(AssertionError):
            V.lifecycle_reference(plan, tests, bad, completion, hashes)


if __name__ == "__main__":
    unittest.main()
