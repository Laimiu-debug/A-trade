"""Synthetic causal, missingness and numerical boundaries; no real inputs."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

from service.research_leader_incremental_prediction import (
    build_leader_incremental_prediction, fit_ridge, simple_return, _Series,
)


def fixture(length=100, activation=85):
    days = [(date(2024, 6, 30) + timedelta(days=i)).isoformat() for i in range(length)]

    def security(symbol, offset):
        close, bars = 100.0, []
        for i, day in enumerate(days):
            value = .001 + .008 * math.sin(i * .31 + offset * .23) + .003 * math.cos(i * .17 + offset)
            close *= 1 + value
            bars.append({"date": day, "open": close, "high": close * 1.01,
                         "low": close * .99, "close": close, "volume": 1_000_000})
        return {"symbol": symbol, "bars": bars}

    stocks = [security("sz" + str(i + 1).zfill(6), i) for i in range(21)]
    data = {"priceBasisId": "raw-unadjusted", "calendar": days,
            "securities": stocks, "benchmark": security("sh000300", 30)}
    a, f = days[activation], days[activation - 10]
    parent = "leaderLagFollowersV1:" + a + ":" + stocks[0]["symbol"]
    formation = {"parentEventId": parent, "formationDate": a, "peerFormationDate": f,
                 "anchorSymbol": stocks[0]["symbol"], "originalLeaderRank": 1,
                 "groupSha256": "a" * 64, "groupKind": "formed", "status": "formed",
                 "lockOwnerEventId": parent, "lockThroughIndex": activation + 5,
                 "createdMemberEpisodes": 20,
                 "members": [{"symbol": s["symbol"], "frozenMemberRank": i}
                             for i, s in enumerate(stocks[1:], 1)]}
    return data, [formation]


def build(data=None, formations=None, *, activation=85, end_index=95, **kwargs):
    if data is None:
        data, formations = fixture(activation=activation)
    return build_leader_incremental_prediction(data, formations, start=data["calendar"][activation],
                                               end=data["calendar"][end_index], **kwargs)


def set_close(data, security, at, close):
    data["securities"][security]["bars"][at].update(open=close, high=close * 1.01, low=close * .99, close=close)


class IncrementalPredictionTests(unittest.TestCase):
    def test_exact_sixty_pairs_require_sixtytwo_prices_and_all_slots(self):
        data, forms = fixture(); result = build(data, forms)
        fit, row = result["fits"][0], result["rows"][0]
        self.assertEqual(fit["trainingPriceDates"], data["calendar"][14:76])
        self.assertEqual(fit["trainingFeatureDates"], data["calendar"][15:75])
        self.assertEqual(fit["trainingTargetDates"], data["calendar"][16:76])
        self.assertEqual(fit["knownPairs"], 60); self.assertEqual(fit["fitStatus"], "known")
        self.assertEqual(result["counts"]["fits"], 20); self.assertEqual(result["counts"]["plannedRows"], 100)
        self.assertEqual(result["counts"]["rowStatusCounts"]["scoreStatus"]["scored"], 100)
        self.assertEqual(row["targetDate"], data["calendar"][86])

    def test_missing_first_required_price_does_not_silently_train_fiftynine(self):
        data, forms = fixture(); del data["securities"][1]["bars"][14]
        result = build(data, forms)
        self.assertTrue(all(f["knownPairs"] == 59 and f["fitStatus"] == "fitUnknown" for f in result["fits"]))
        self.assertTrue(all(f["models"] == {"baseline": None, "augmented": None} for f in result["fits"]))
        row = result["rows"][0]
        self.assertEqual(row["featureStatus"], "known"); self.assertEqual(row["targetStatus"], "known")
        self.assertEqual(row["scoreStatus"], "fitUnknown")

    def test_missing_anchor_training_blocks_both_models_on_common_support(self):
        data, forms = fixture(); data["securities"][0]["bars"][50]["volume"] = 0
        fits = build(data, forms)["fits"]
        self.assertTrue(all(f["knownPairs"] == 58 for f in fits))
        pair = fits[0]["trainingPairs"][35]
        self.assertIsNotNone(pair["features"]["own"]); self.assertIsNone(pair["features"]["anchor"])
        self.assertTrue(any("anchor:" in reason for reason in pair["reasons"]))

    def test_fixed_nineteen_peer_denominator_and_leave_self_out(self):
        data, forms = fixture(); before = build(data, forms)["rows"][0]
        set_close(data, 1, 85, data["securities"][1]["bars"][85]["close"] * 1.3)
        result = build(data, forms)
        self.assertEqual(before["features"]["peerMean"], result["rows"][0]["features"]["peerMean"])
        self.assertNotEqual(before["features"]["own"], result["rows"][0]["features"]["own"])
        data["securities"][2]["bars"][85]["volume"] = 0
        row = build(data, forms)["rows"][0]
        self.assertIsNone(row["features"]["peerMean"]); self.assertEqual(row["featureStatus"], "featureUnknown")
        self.assertTrue(any("peer:sz000003:" in reason for reason in row["featureReasons"]))

    def test_raw_simple_return_is_not_log_return(self):
        data, _ = fixture(); set_close(data, 1, 84, 100); set_close(data, 1, 85, 110)
        source = _Series(data["securities"][1], data["calendar"], data["calendar"][-1])
        value, reasons = simple_return(source, data["calendar"], 85)
        self.assertEqual(reasons, []); self.assertAlmostEqual(value, .1)
        self.assertNotAlmostEqual(value, math.log(1.1), places=4)

    def test_f_plus_one_changes_do_not_refit_or_rescale(self):
        data, forms = fixture(); before = build(data, forms)
        set_close(data, 1, 76, 500)
        after = build(data, forms)
        self.assertEqual(before["fits"], after["fits"])
        self.assertEqual(before["rows"], after["rows"])

    def test_label_only_unknown_preserves_common_predictions(self):
        data, forms = fixture(); data["securities"][1]["bars"][86]["volume"] = 0
        row = build(data, forms)["rows"][0]
        self.assertEqual(row["predictionStatus"], "known"); self.assertEqual(row["targetStatus"], "targetUnknown")
        self.assertEqual(row["scoreStatus"], "targetUnknown")
        self.assertTrue(all(value is not None for value in row["predictions"].values()))
        self.assertIsNone(row["errors"]["zero"]["squared"])

    def test_target_perturbation_changes_error_not_earlier_prediction(self):
        data, forms = fixture(); before = build(data, forms)["rows"][0]
        set_close(data, 1, 86, data["securities"][1]["bars"][86]["close"] * 1.2)
        after = build(data, forms)["rows"][0]
        self.assertEqual(before["predictions"], after["predictions"])
        self.assertNotEqual(before["actualReturn"], after["actualReturn"])
        self.assertNotEqual(before["errors"], after["errors"])

    def test_current_anchor_missing_removes_baseline_prediction_too(self):
        data, forms = fixture(); data["securities"][0]["bars"][85]["knownSuspended"] = True
        row = build(data, forms)["rows"][0]
        self.assertEqual(row["fitStatus"], "known"); self.assertEqual(row["targetStatus"], "known")
        self.assertEqual(row["predictions"], {"baseline": None, "augmented": None})
        self.assertEqual(row["scoreStatus"], "featureUnknown")

    def test_period_end_preserves_five_design_slots_and_last_prediction(self):
        data, forms = fixture(); result = build(data, forms, end_index=85)
        first = result["rows"][:5]
        self.assertEqual([r["scoreStatus"] for r in first], ["targetBeyondEnd"] + ["forecastBeyondEnd"] * 4)
        self.assertEqual(first[0]["predictionStatus"], "known"); self.assertIsNone(first[0]["targetDate"])
        self.assertTrue(all(r["forecastDate"] is None for r in first[1:]))
        self.assertEqual(result["counts"]["unassignedForecastDates"], 80)

    def test_future_numeric_pollution_and_calendar_append_do_not_change_prefix(self):
        data, forms = fixture(); prefix = deepcopy(data)
        prefix["calendar"] = prefix["calendar"][:91]
        for security in [*prefix["securities"], prefix["benchmark"]]:
            security["bars"] = security["bars"][:91]
        expected = build(prefix, forms, end_index=90)
        for security in [*data["securities"], data["benchmark"]]:
            for bar in security["bars"][91:]: bar.update(close=float("nan"), volume=None)
        self.assertEqual(expected, build(data, forms, end_index=90))

    def test_exact_constants_collinearity_and_unpenalized_intercept(self):
        x = [[float(i), float(i), 7.] for i in range(60)]
        model = fit_ridge(x, [2. + .1 * i for i in range(60)], ["own", "peerMean", "index"])
        self.assertEqual(model["constantColumns"], ["index"]); self.assertEqual(model["scales"][2], 0)
        self.assertEqual(model["beta"][2], 0); self.assertAlmostEqual(model["beta"][0], model["beta"][1])
        self.assertAlmostEqual(model["intercept"], 4.95)

    def test_ridge_uses_sum_loss_not_mean_loss(self):
        x = [[-1. if i < 30 else 1., 0., 0.] for i in range(60)]
        y = [row[0] for row in x]
        model = fit_ridge(x, y, ["own", "peerMean", "index"])
        self.assertEqual(model["normalMatrix"][0][0], 61.)
        self.assertAlmostEqual(model["beta"][0], 60 / 61)
        self.assertNotAlmostEqual(model["beta"][0], .5)

    def test_duplicate_anchor_can_change_prediction_by_penalty_sharing(self):
        # No new information: anchor is exactly own. Two penalized copies can
        # nevertheless share the coefficient, reducing effective shrinkage.
        x = [[-1. if i < 30 else 1., 0., 0.] for i in range(60)]
        y = [row[0] for row in x]
        baseline = fit_ridge(x, y, ["own", "peerMean", "index"])
        augmented = fit_ridge([row + [row[0]] for row in x], y,
                              ["own", "peerMean", "index", "anchor"])
        baseline_prediction = baseline["intercept"] + baseline["beta"][0]
        augmented_prediction = augmented["intercept"] + augmented["beta"][0] + augmented["beta"][3]
        self.assertAlmostEqual(baseline_prediction, 60 / 61)
        self.assertAlmostEqual(augmented["beta"][0], 60 / 121)
        self.assertAlmostEqual(augmented["beta"][3], 60 / 121)
        self.assertAlmostEqual(augmented_prediction, 120 / 121)
        self.assertNotEqual(baseline_prediction, augmented_prediction)
        self.assertLess((1 - augmented_prediction) ** 2, (1 - baseline_prediction) ** 2)

    def test_exact_constant_training_maps_changed_forecast_column_to_zero(self):
        data, forms = fixture()
        for bar in data["securities"][0]["bars"]: bar.update(open=100., high=101., low=99., close=100.)
        before = build(data, forms)
        set_close(data, 0, 85, 150.)
        after = build(data, forms)
        self.assertIn("anchor", before["fits"][0]["models"]["augmented"]["constantColumns"])
        self.assertNotEqual(before["rows"][0]["features"]["anchor"], after["rows"][0]["features"]["anchor"])
        self.assertEqual(before["rows"][0]["predictions"], after["rows"][0]["predictions"])

    def test_near_constant_column_is_not_thresholded(self):
        values = [[1. + i * 1e-12, 0., 0.] for i in range(60)]
        fit = fit_ridge(values, [float(i) for i in range(60)], ["own", "peerMean", "index"])
        self.assertGreater(fit["scales"][0], 0); self.assertNotIn("own", fit["constantColumns"])

    def test_structural_solve_failure_marks_both_models_unknown(self):
        data, forms = fixture()
        with patch("service.research_leader_incremental_prediction.np.linalg.solve", side_effect=np.linalg.LinAlgError("synthetic")):
            result = build(data, forms)
        self.assertTrue(all(f["fitStatus"] == "fitUnknown" and f["reasons"] == ["numericFitFailure"] for f in result["fits"]))
        self.assertTrue(all(r["scoreStatus"] == "fitUnknown" for r in result["rows"]))

    def test_locked_and_notformed_preserve_parent_without_artificial_slots(self):
        data, forms = fixture(); original = forms[0]
        for offset, status in ((1, "locked"), (2, "notFormed")):
            row = deepcopy(original); day = data["calendar"][85 + offset]
            row.update(formationDate=day, peerFormationDate=data["calendar"][75 + offset],
                       parentEventId="leaderLagFollowersV1:" + day + ":" + row["anchorSymbol"], status=status)
            if status == "notFormed": row.update(members=[], groupKind="incomplete", createdMemberEpisodes=0)
            forms.append(row)
        result = build(data, forms)
        self.assertEqual(result["formations"], forms); self.assertEqual(result["counts"]["formationStatusCounts"], {"formed": 1, "locked": 1, "notFormed": 1})
        self.assertEqual(len(result["fits"]), 20); self.assertEqual(len(result["rows"]), 100)

    def test_no_formed_events_retains_empty_zero_status_domains(self):
        data, forms = fixture(); forms[0]["status"] = "locked"
        result = build(data, forms)
        self.assertEqual(result["fits"], []); self.assertEqual(result["rows"], [])
        self.assertTrue(all(v == 0 for v in result["counts"]["rowStatusCounts"]["scoreStatus"].values()))

    def test_parent_and_source_inputs_are_not_mutated_and_output_is_detached(self):
        data, forms = fixture(); prior = deepcopy((data, forms)); result = build(data, forms)
        self.assertEqual((data, forms), prior)
        result["formations"][0]["members"][0]["symbol"] = "changed"
        self.assertEqual((data, forms), prior)
        json.dumps(result, allow_nan=False)

    def test_insufficient_warmup_keeps_null_slots_and_all_fits(self):
        data, forms = fixture(activation=40)
        result = build(data, forms, activation=40, end_index=45)
        fit = result["fits"][0]
        self.assertEqual(len(fit["trainingPairs"]), 60); self.assertIsNone(fit["trainingPriceDates"][0])
        self.assertEqual(fit["fitStatus"], "fitUnknown"); self.assertEqual(result["counts"]["plannedRows"], 100)

    def test_raw_quality_and_exact_source_calendar_are_enforced(self):
        for problem in ("halt", "basis", "envelope", "missing", "bool"):
            data, forms = fixture(); stock = data["securities"][1]
            if problem == "halt": stock["tradeStatusByDate"] = {data["calendar"][50]: 0.0}
            elif problem == "basis": stock["bars"][50]["priceBasisId"] = "adjusted"
            elif problem == "envelope": stock["bars"][50]["high"] = 1
            elif problem == "missing": del stock["bars"][50]
            else: stock["bars"][50]["volume"] = True
            with self.subTest(problem=problem):
                self.assertEqual(build(data, forms)["fits"][0]["fitStatus"], "fitUnknown")
        data, forms = fixture(); data["calendar"].insert(5, data["calendar"][5])
        with self.assertRaises(ValueError): build(data, forms)

    def test_formation_identity_members_and_order_are_not_repaired(self):
        data, forms = fixture()
        for kind in ("duplicate", "member", "F", "anchor"):
            wrong = deepcopy(forms)
            if kind == "duplicate": wrong.append(deepcopy(wrong[0]))
            elif kind == "member": wrong[0]["members"][1] = deepcopy(wrong[0]["members"][0])
            elif kind == "F": wrong[0]["peerFormationDate"] = data["calendar"][74]
            else: wrong[0]["anchorSymbol"] = "unknown"
            with self.subTest(kind=kind), self.assertRaises(ValueError): build(data, wrong)

    def test_forecast_boundary_and_loss_units_with_zero_benchmark(self):
        result = build(); row = result["rows"][0]
        self.assertEqual(row["forecastDate"], "2024-09-23"); self.assertEqual(row["targetDate"], "2024-09-24")
        self.assertTrue(row["crosses20240924"])
        actual, pred = row["actualReturn"], row["predictions"]
        self.assertEqual(row["errors"]["zero"], {"squared": actual * actual, "absolute": abs(actual)})
        self.assertAlmostEqual(row["errors"]["mseDelta"], (pred["augmented"] - actual) ** 2 - (pred["baseline"] - actual) ** 2)
        self.assertEqual(row["errors"]["maeDelta"], abs(pred["augmented"] - actual) - abs(pred["baseline"] - actual))


if __name__ == "__main__":
    unittest.main()
