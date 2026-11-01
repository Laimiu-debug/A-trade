"""Hand-calculated aggregation cases; no source data, fit or account is loaded."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("incremental_prediction_readout", ROOT / "scripts/summarize-leader-incremental-prediction.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def row(event="e1", member="m1", day="2024-09-23", offset=0, baseline=.2, augmented=.1,
        actual=.1, status="scored", target="2024-09-24"):
    errors = {model: {"squared": None, "absolute": None} for model in S.MODELS}
    errors.update(mseDelta=None, maeDelta=None)
    if status == "scored":
        for model, prediction in (("baseline", baseline), ("augmented", augmented), ("zero", 0)):
            errors[model] = {"squared": (prediction - actual) ** 2, "absolute": abs(prediction - actual)}
        errors.update(mseDelta=errors["augmented"]["squared"] - errors["baseline"]["squared"],
                      maeDelta=errors["augmented"]["absolute"] - errors["baseline"]["absolute"])
    return {"parentEventId": event, "formationDate": "2024-09-20", "peerFormationDate": "2024-09-06",
        "anchorSymbol": "anchor-" + event, "memberSymbol": member, "fitId": event + ":" + member,
        "rowId": event + ":" + member + ":" + str(offset), "offset": offset, "forecastDate": day, "targetDate": target,
        "fitStatus": "fitUnknown" if status == "fitUnknown" else "known",
        "featureStatus": "forecastBeyondEnd" if day is None else "featureUnknown" if status == "featureUnknown" else "known",
        "predictionStatus": "known" if status in ("scored", "targetUnknown", "targetBeyondEnd") else status,
        "targetStatus": "targetBeyondEnd" if target is None else "targetUnknown" if status == "targetUnknown" else "known",
        "predictions": {"baseline": baseline, "augmented": augmented}, "actualReturn": actual,
        "scoreStatus": status, "errors": errors,
        "crosses20240924": day is not None and target is not None and day < "2024-09-24" <= target}


def complete_group():
    formation = {"parentEventId": "e1", "status": "formed", "formationDate": "2024-09-20",
        "peerFormationDate": "2024-09-06", "anchorSymbol": "anchor-e1"}
    rows = [row(member=f"m{member:02}", day=f"2024-09-{20 + offset:02}", offset=offset,
                target=f"2024-09-{21 + offset:02}") for member in range(20) for offset in range(5)]
    return [formation], rows


def atlas():
    phases = []
    for year in range(2022, 2026):
        for quarter, start, end in ((1, "01-01", "03-31"), (2, "04-01", "06-30"),
                                    (3, "07-01", "09-30"), (4, "10-01", "12-31")):
            phases.append({"id": f"{year}-Q{quarter}", "startDate": f"{year}-{start}", "endDate": f"{year}-{end}"})
    return {"phases": phases}


class PredictionReadoutTests(unittest.TestCase):
    def assert_reference(self, rows):
        actual, expected = S.aggregate(rows), S.independent_aggregate(rows)
        for mode, value in expected.items():
            S.same(actual[mode], value)
        return actual

    def test_three_levels_have_hand_calculated_unequal_weights(self):
        rows = [row("e1", "m1", "2024-09-23", baseline=.1, augmented=0, actual=0),
                row("e1", "m2", "2024-09-23", baseline=.3, augmented=0, actual=0),
                row("e1", "m1", "2024-09-24", offset=1, baseline=.5, augmented=0, actual=0),
                row("e2", "m1", "2024-09-23", baseline=.5, augmented=0, actual=0)]
        result = self.assert_reference(rows)
        self.assertAlmostEqual(result["primary"]["baselineMSE"], .2)
        self.assertAlmostEqual(result["overlap"]["baselineMSE"], .18)
        self.assertAlmostEqual(result["rowWeighted"]["baselineMSE"], .15)
        self.assertEqual(result["coverage"]["scoredEventDateCount"], 3)
        self.assertEqual(result["coverage"]["scoredUniqueMemberDateCount"], 3)

    def test_duplicate_member_date_averages_loss_not_prediction(self):
        rows = [row("e1", baseline=.2, augmented=0, actual=0), row("e2", baseline=-.2, augmented=0, actual=0)]
        result = self.assert_reference(rows)
        self.assertAlmostEqual(result["overlap"]["baselineMSE"], .04)
        self.assertNotEqual(result["overlap"]["baselineMSE"], 0)
        self.assertEqual(result["coverage"]["scoredSlots"], 2)
        self.assertEqual(result["coverage"]["scoredUniqueMemberDateCount"], 1)

    def test_empty_slice_is_null_not_zero(self):
        result = self.assert_reference([])
        self.assertTrue(all(value is None for value in result["primary"].values()))
        self.assertEqual(result["coverage"]["plannedSlots"], 0)
        self.assertEqual(result["eventImprovementCounts"]["mse"]["unknown"], 0)

    def test_unscored_event_stays_in_coverage_not_loss_denominator(self):
        rows = [row(), row("e2", status="fitUnknown")]
        result = self.assert_reference(rows)
        self.assertEqual(result["coverage"]["eventCount"], 2)
        self.assertEqual(result["coverage"]["scoredEventCount"], 1)
        self.assertAlmostEqual(result["primary"]["baselineMSE"], .01)
        self.assertEqual(result["eventImprovementCounts"]["mse"], {"improved": 1, "worse": 0, "tie": 0, "unknown": 1})

    def test_all_unknown_is_null_with_full_denominator(self):
        result = self.assert_reference([row(status="fitUnknown")])
        self.assertEqual(result["coverage"]["plannedSlots"], 1)
        self.assertIsNone(result["primary"]["baselineMSE"])
        self.assertEqual(result["eventImprovementCounts"]["mae"]["unknown"], 1)

    def test_zero_baseline_relative_improvement_is_null(self):
        result = self.assert_reference([row(baseline=.1, augmented=.2, actual=.1)])
        self.assertEqual(result["primary"]["baselineMSE"], 0)
        self.assertIsNone(result["primary"]["relativeMSEImprovement"])
        self.assertAlmostEqual(result["primary"]["improvementMSE"], -.01)

    def test_zero_forecast_is_scored_on_same_rows(self):
        result = self.assert_reference([row()])
        self.assertAlmostEqual(result["primary"]["zeroMSE"], .01)
        self.assertAlmostEqual(result["primary"]["zeroMAE"], .1)
        self.assertAlmostEqual(result["primary"]["baselineMSEBpsSquared"], 1e6)
        self.assertAlmostEqual(result["primary"]["baselineMAEBps"], 1000)
        self.assertAlmostEqual(result["primary"]["improvementMSEBpsSquared"], 1e6)
        self.assertAlmostEqual(result["primary"]["improvementMAEBps"], 1000)

    def test_tie_tolerance_only_classifies_does_not_clip_metrics(self):
        value = row(baseline=0, augmented=1e-8, actual=0)
        result = self.assert_reference([value])
        self.assertEqual(result["eventImprovementCounts"]["mse"]["tie"], 1)
        self.assertGreater(result["primary"]["deltaMSE"], 0)
        self.assertEqual(result["eventImprovementCounts"]["mae"]["worse"], 1)

    def test_target_unknown_preserves_prediction_status(self):
        value = row(status="targetUnknown", actual=None)
        result = self.assert_reference([value])
        self.assertEqual(result["coverage"]["predictionStatusCounts"]["known"], 1)
        self.assertEqual(result["coverage"]["targetStatusCounts"]["targetUnknown"], 1)
        self.assertEqual(result["coverage"]["scoredSlots"], 0)

    def test_null_forecast_overall_only(self):
        value = row(day=None, target=None, status="forecastBeyondEnd")
        self.assertEqual(len(S.select_slice([value], "overall", "2024-01-01", "2025-12-31")), 1)
        self.assertEqual(S.select_slice([value], "year", "2024-01-01", "2024-12-31"), [])
        self.assertEqual(S.aggregate([value])["coverage"]["unassignedForecastDateSlots"], 1)

    def test_target_censor_stays_in_known_forecast_slice(self):
        value = row(day="2025-12-31", target=None, status="targetBeyondEnd")
        selected = S.select_slice([value], "year", "2025-01-01", "2025-12-31")
        self.assertEqual(selected, [value])
        self.assertEqual(S.aggregate(selected)["coverage"]["targetStatusCounts"]["targetBeyondEnd"], 1)

    def test_september_boundary_uses_forecast_not_target(self):
        value = row(day="2024-09-23", target="2024-09-24")
        self.assertEqual(len(S.select_slice([value], "descriptiveEpoch", "2024-01-01", "2024-09-23")), 1)
        self.assertEqual(S.select_slice([value], "descriptiveEpoch", "2024-09-24", "2025-12-31"), [])
        self.assertEqual(S.aggregate([value])["coverage"]["scoredCrosses20240924Slots"], 1)

    def test_year_boundary_uses_forecast_not_formation_or_target(self):
        value = row(day="2024-12-31", target="2025-01-02")
        self.assertEqual(len(S.select_slice([value], "year", "2024-01-01", "2024-12-31")), 1)
        self.assertEqual(S.select_slice([value], "year", "2025-01-01", "2025-12-31"), [])

    def test_cross_slice_weights_recompute_without_additive_claim(self):
        rows = [row("e1", day="2024-09-23", baseline=.1, augmented=0, actual=0),
                row("e1", day="2024-09-24", offset=1, baseline=.3, augmented=0, actual=0),
                row("e2", day="2024-09-24", baseline=.5, augmented=0, actual=0)]
        before = self.assert_reference(S.select_slice(rows, "epoch", "2024-01-01", "2024-09-23"))
        after = self.assert_reference(S.select_slice(rows, "epoch", "2024-09-24", "2025-12-31"))
        whole = self.assert_reference(rows)
        self.assertAlmostEqual(before["primary"]["baselineMSE"], .01)
        self.assertAlmostEqual(after["primary"]["baselineMSE"], .17)
        self.assertAlmostEqual(whole["primary"]["baselineMSE"], .15)
        self.assertNotAlmostEqual(whole["primary"]["baselineMSE"], (before["primary"]["baselineMSE"] + after["primary"]["baselineMSE"]) / 2)

    def test_all_five_slots_and_twenty_members_conserve(self):
        formations, rows = complete_group()
        formed, fits = S.validate_forecasts(formations, rows)
        self.assertEqual((len(formed), len(fits), len(rows)), (1, 20, 100))

    def test_duplicate_row_rejected(self):
        formations, rows = complete_group()
        rows[-1] = deepcopy(rows[0])
        with self.assertRaisesRegex(AssertionError, "Unique planned"):
            S.validate_forecasts(formations, rows)

    def test_missing_planned_slot_rejected(self):
        formations, rows = complete_group()
        with self.assertRaisesRegex(AssertionError, "five planned"):
            S.validate_forecasts(formations, rows[:-1])

    def test_unscored_zero_loss_rejected(self):
        formations, rows = complete_group()
        rows[0]["scoreStatus"] = "targetUnknown"
        with self.assertRaisesRegex(AssertionError, "remain null"):
            S.validate_forecasts(formations, rows)

    def test_saved_loss_corruption_rejected(self):
        formations, rows = complete_group()
        rows[0]["errors"]["baseline"]["squared"] = .5
        with self.assertRaisesRegex(AssertionError, "Saved error"):
            S.validate_forecasts(formations, rows)

    def test_saved_delta_corruption_rejected(self):
        formations, rows = complete_group()
        rows[0]["errors"]["mseDelta"] = 1
        with self.assertRaisesRegex(AssertionError, "Paired squared delta"):
            S.validate_forecasts(formations, rows)

    def test_no_mutation(self):
        formations, rows = complete_group()
        original = deepcopy((formations, rows))
        self.assert_reference(rows)
        S.validate_forecasts(formations, rows)
        self.assertEqual((formations, rows), original)

    def test_tuple_key_diagnostics_do_not_raise_type_error(self):
        S.same({("p", "b"): 1}, {("p", "b"): 1})
        with self.assertRaisesRegex(AssertionError, "numeric"):
            S.same({("p", "b"): 1}, {("p", "b"): 2})

    def test_full_fixed_slice_matrix_includes_empty_slices(self):
        counts = {}
        for period in S.PERIODS:
            start, end = period.split("-")
            for _ in S.BOARDS:
                for table, _, _, _, _ in S.slices(period, start + "-01-01", end + "-12-31", atlas()):
                    counts[table] = counts.get(table, 0) + 1
        self.assertEqual(counts, {"primaryRows": 6, "yearRows": 12, "halfYearRows": 24, "phaseRows": 48, "epochRows": 6})

    def test_locked_records_remain_but_do_not_create_forecasts(self):
        formations, rows = complete_group()
        formations += [{"parentEventId": "locked", "status": "locked"}, {"parentEventId": "notformed", "status": "notFormed"}]
        counts = {"formations": 3, "formationStatusCounts": {"formed": 1, "locked": 1, "notFormed": 1},
            "fits": 20, "plannedRows": 100, "fitStatusCounts": {"known": 20, "fitUnknown": 0},
            "rowStatusCounts": {field: {state: sum(r[field] == state for r in rows) for state in states}
                                for field, states in S.STATUSES.items()},
            "unassignedForecastDates": 0, "crosses20240924": 20}
        tables, events, cover = S.summarize_group({"period": "2024-2025", "board": "main"}, "2024-01-01", "2025-12-31",
            formations, rows, {"targetDates": ["2024-09-20", "2024-09-23"]}, counts, atlas())
        self.assertEqual(cover["formationStatusCounts"], {"formed": 1, "locked": 1, "notFormed": 1})
        self.assertEqual((cover["sourceFormationCount"], cover["memberEpisodes"], cover["designedSlots"]), (3, 20, 100))
        self.assertEqual(len(events), 1)
        empty_year = next(r for r in tables["yearRows"] if r["id"] == "2025")
        self.assertEqual(empty_year["coverage"]["plannedSlots"], 0)
        self.assertIsNone(empty_year["primary"]["baselineMSE"])


if __name__ == "__main__":
    unittest.main()
