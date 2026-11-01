"""Hand-calculated forecast/ledger boundary checks; no historical artifacts."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("all_opportunity_readout_tests_subject", ROOT / "scripts/summarize-all-opportunity-feedback.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
KEY = {"period": "2024-2025", "board": "main", "exit": "trail10"}


def prediction(value=.1, status="fitted"):
    if status == "fitted":
        return {"status": status, "prediction": value, "allow": value > 0,
                "reason": "positivePrediction" if value > 0 else "nonPositivePrediction"}
    return {"status": "fallback", "prediction": None, "allow": True, "reason": "insufficientTrainingFormationDays"}


def point(fid, day, y, a=.0, b=.15, status="closed"):
    return {**KEY, "formationId": fid, "formationDate": day, "symbol": "s" + fid,
        "pathStatus": status, "pathEntered": status != "unfilled", "originalEntered": True,
        "entryDate": "2024-09-25" if status != "unfilled" else None,
        "finalExitDate": "2025-01-02" if status == "closed" else None,
        "labelNetReturn": y if status == "closed" else None, "sourceCutoff": "2025-12-31",
        "control": prediction(a), "candidate": prediction(b), "fitCell": "bothFitted"}


def forecast_fixture():
    dates = ["2024-09-23", "2024-09-24", "2024-09-25", "2025-01-02"]
    paths = [point("a", dates[0], .1), point("b", dates[0], .3), point("c", dates[1], -.2),
             point("d", dates[2], None, status="unfilled"), point("e", dates[3], None, status="open")]
    decisions = {}
    for pop in M.POPULATIONS:
        decisions[pop] = [{"formationDate": day, "asOfDate": ["2024-09-20", *dates][i],
            "models": {"intercept": prediction(.01), "ridge": prediction(.02)},
            "support": {"eligibleEpisodeCount": 30, "eligibleFormationDayCount": 20,
                "labelXUnknownCount": 2, "immatureCount": 3, "immatureAgeSlots": {"mean": 5.}},
            "currentFeatureStatus": "complete"} for i, day in enumerate(dates)]
    return dates, paths, decisions


def full_path_fixture():
    signal = {"signalId": "a", "strategyId": "strongUndercutReclaimV1", "symbol": "sz000001",
              "signalDate": "2024-12-30", "originalRank": 1}
    label = {"formationId": "a", "formationDate": "2024-12-30", "entryDate": "2024-12-31",
             "exitDate": "2025-01-02", "netReturn": .1}
    outcome = {"entered": True, "labelEligible": True, "fullyClosed": True, "entryDate": label["entryDate"],
        "finalExitDate": label["exitDate"], "labelAvailableDate": label["exitDate"],
        "netReturn": .1, "netPnlAmount": 10000., "investedAmount": 100000.}
    row = {**KEY, "formationId": "a", "formationDate": signal["signalDate"], "symbol": signal["symbol"],
        "originalEntered": False, "path": {"signal": signal, "exitPolicy": "trail10", "status": "closed",
        "outcome": outcome, "label": label}}
    return [signal], [row]


class AllOpportunityFeedbackReadoutTests(unittest.TestCase):
    def test_known_day_equal_mae_mse_percentage_units(self):
        rows = [point("a", "2024-09-23", .1), point("b", "2024-09-23", .3),
                point("c", "2024-09-24", -.2, b=-.1)]
        got = M.weighted_score(rows)
        self.assertEqual((got["episodeCount"], got["formationDayCount"], got["weightSum"]), (3, 2, 2.))
        self.assertAlmostEqual(got["meanNetReturnPct"], 0.)
        self.assertAlmostEqual(got["control"]["maePp"], 20.)
        self.assertAlmostEqual(got["control"]["msePpSquared"], 450.)
        self.assertAlmostEqual(got["candidate"]["maePp"], 10.)
        self.assertAlmostEqual(got["candidate"]["msePpSquared"], 112.5)
        self.assertAlmostEqual(got["candidate"]["meanPredictionPct"], 2.5)
        self.assertAlmostEqual(got["candidateMinusControl"]["maePp"], -10.)

    def test_empty_cells_preserve_unknown_not_zero_scores(self):
        got = M.evaluate_cell([])
        self.assertEqual(got["opportunityCount"], 0)
        self.assertEqual(got["score"]["weightSum"], 0.)
        self.assertIsNone(got["score"]["meanNetReturnPct"])
        self.assertIsNone(got["score"]["control"]["maePp"])
        self.assertEqual(got["pathStatusCounts"], dict.fromkeys(M.PATH_STATES, 0))

    def test_open_partial_unfilled_never_become_zero_labels(self):
        rows = [point("a", "2024-09-23", .2), point("b", "2024-09-23", None, status="open"),
                point("c", "2024-09-24", None, status="unfilled"), point("d", "2024-09-25", None, status="unresolved")]
        got = M.evaluate_cell(rows)
        self.assertEqual((got["opportunityCount"], got["enteredCount"], got["immatureEnteredCount"], got["unfilledCount"]), (4, 3, 2, 1))
        self.assertEqual(got["score"]["episodeCount"], 1)
        self.assertAlmostEqual(got["score"]["meanNetReturnPct"], 20.)

    def test_new_coverage_kept_separate_and_no_pool_error(self):
        dates, paths, decisions = forecast_fixture()
        decisions["originalEntered"][0]["models"] = {m: prediction(status="fallback") for m in M.MODELS}
        paired = M.paired_records(KEY, paths, decisions)
        result = next(r for r in M.evaluate_pairs(KEY, paired, dates)
                      if r["contrastId"] == "allVsOriginalIntercept" and r["id"] == "overall")
        self.assertNotIn("score", result["coverage"])
        cell = result["fitCells"]["candidateOnlyFitted"]
        self.assertEqual((cell["opportunityCount"], cell["score"]["episodeCount"]), (2, 2))
        self.assertAlmostEqual(cell["score"]["meanNetReturnPct"], 20.)
        self.assertIsNone(cell["score"]["control"]["maePp"])
        self.assertIsNotNone(cell["score"]["candidate"]["maePp"])
        self.assertEqual(result["commonFitted"]["closedCount"], 1)

    def test_missing_prediction_retains_all_identity_and_no_fabrication(self):
        dates, paths, decisions = forecast_fixture()
        decisions["allExecutable"].pop(0)
        paired = M.paired_records(KEY, paths, decisions)
        self.assertEqual(len(paired), 4 * len(paths))
        rows = [r for r in paired if r["contrastId"] == "allVsOriginalRidge" and r["formationDate"] == dates[0]]
        self.assertEqual({r["fitCell"] for r in rows}, {"controlOnlyFitted"})
        self.assertTrue(all(r["candidate"]["status"] == "missing" and r["candidate"]["allow"] is None for r in rows))
        self.assertEqual(M.evaluate_cell(rows)["candidateStatusCounts"]["missing"], 2)

    def test_exact_zero_prediction_rejects_no_epsilon(self):
        self.assertFalse(M.model_observation({"models": {"ridge": prediction(0.)}}, "ridge")["allow"])
        self.assertTrue(M.model_observation({"models": {"ridge": prediction(1e-15)}}, "ridge")["allow"])
        bad = prediction(.1); bad["allow"] = False
        with self.assertRaises(ValueError):
            M.model_observation({"models": {"ridge": bad}}, "ridge")

    def test_fallback_is_allowed_but_never_scored_as_prediction(self):
        good = M.model_observation({"models": {"ridge": prediction(status="fallback")}}, "ridge")
        self.assertTrue(good["allow"]); self.assertIsNone(good["prediction"])
        bad = deepcopy(good); bad["prediction"] = 0.
        with self.assertRaises(ValueError):
            M.model_observation({"models": {"ridge": bad}}, "ridge")

    def test_allow_four_cells_and_each_cell_reweights_dates(self):
        dates, paths, decisions = forecast_fixture()
        for i, (a, b) in enumerate(((True, True), (True, False), (False, True), (False, False))):
            decisions["originalEntered"][i]["models"]["intercept"] = prediction(.01 if a else -.01)
            decisions["allExecutable"][i]["models"]["intercept"] = prediction(.01 if b else -.01)
        rows = M.evaluate_pairs(KEY, M.paired_records(KEY, paths, decisions), dates)
        got = next(r for r in rows if r["contrastId"] == "allVsOriginalIntercept" and r["id"] == "overall")
        self.assertEqual({k: v["opportunityCount"] for k, v in got["commonFittedAllowCells"].items()}, {"00": 1, "01": 1, "10": 1, "11": 2})
        self.assertEqual(got["commonFittedAllowCells"]["11"]["score"]["weightSum"], 1.)
        self.assertAlmostEqual(got["commonFittedAllowCells"]["11"]["score"]["meanNetReturnPct"], 20.)
        self.assertEqual(got["commonFittedAllowMargins"]["control"]["allow"]["score"]["formationDayCount"], 2)

    def test_formation_slice_keeps_later_exit_label(self):
        original, rows = full_path_fixture()
        paths = M.normalize_evaluation_paths(rows, original, KEY, "2025-12-31")
        dates = ["2024-12-30", "2024-12-31", "2025-01-02"]
        decisions = {p: [{"formationDate": d, "asOfDate": "2024-12-27", "models": {m: prediction(.1) for m in M.MODELS}} for d in dates] for p in M.POPULATIONS}
        got = next(r for r in M.evaluate_pairs(KEY, M.paired_records(KEY, paths, decisions), dates)
                   if r["contrastId"] == "allVsOriginalIntercept" and r["id"] == "2024")
        self.assertEqual(got["commonFitted"]["closedCount"], 1)
        self.assertAlmostEqual(got["commonFitted"]["score"]["meanNetReturnPct"], 10.)

    def test_source_cutoff_not_period_slice_is_maturity_boundary(self):
        original, rows = full_path_fixture()
        with self.assertRaises(ValueError):
            M.normalize_evaluation_paths(rows, original, KEY, "2024-12-31")
        rows[0]["path"]["outcome"]["labelAvailableDate"] = "2025-01-03"
        with self.assertRaises(ValueError):
            M.normalize_evaluation_paths(rows, original, KEY, "2025-12-31")

    def test_original_entry_membership_does_not_filter_evaluation(self):
        original, rows = full_path_fixture()
        got = M.normalize_evaluation_paths(rows, original, KEY, "2025-12-31")
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0]["originalEntered"])
        self.assertEqual(got[0]["pathStatus"], "closed")

    def test_duplicates_unknown_identity_and_asof_mismatch_rejected(self):
        dates, paths, decisions = forecast_fixture()
        with self.assertRaises(ValueError):
            M.paired_records(KEY, paths + [paths[0]], decisions)
        altered = deepcopy(decisions); altered["originalEntered"].append(altered["originalEntered"][0])
        with self.assertRaises(ValueError):
            M.paired_records(KEY, paths, altered)
        decisions["allExecutable"][0]["asOfDate"] = "2024-09-19"
        with self.assertRaises(ValueError):
            M.paired_records(KEY, paths, decisions)
        original, full = full_path_fixture()
        full[0]["formationId"] = "other"
        with self.assertRaises(ValueError):
            M.normalize_evaluation_paths(full, original, KEY, "2025-12-31")

    def test_future_labels_do_not_mutate_prediction_or_allow(self):
        dates, paths, decisions = forecast_fixture()
        saved = deepcopy((paths, decisions))
        a = M.paired_records(KEY, paths, decisions)
        altered = deepcopy(paths); altered[0]["labelNetReturn"] = -9.
        b = M.paired_records(KEY, altered, decisions)
        self.assertEqual([(r["control"], r["candidate"]) for r in a], [(r["control"], r["candidate"]) for r in b])
        M.evaluate_pairs(KEY, a, dates)
        self.assertEqual((paths, decisions), saved)

    def test_all_sixty_accounts_ninety_six_signed_contrasts(self):
        rows = [{"period": p, "board": b, "exit": e, **M.candidate_identity(c),
                 "metrics": {"netPnlAmount": float(i * 10), "payoffRatio": None}}
                for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for i, c in enumerate(M.CANDIDATES)]
        got = M.compare_accounts(rows)
        self.assertEqual(len(got), 96)
        self.assertEqual(sum(r["comparisonFamily"] == "population" for r in got), 24)
        self.assertEqual(sum(r["comparisonFamily"] == "baseline" for r in got), 48)
        self.assertEqual(got[0]["candidateMinusControl"], {"netPnlAmount": 20., "payoffRatio": None})
        with self.assertRaises(ValueError):
            M.compare_accounts(rows[:-1])

    def test_full_prediction_slice_matrix_192_and_924_boundary(self):
        output = []
        for p in M.PERIODS:
            dates = ["2022-01-04", "2023-12-29"] if p == "2022-2023" else ["2024-09-23", "2024-09-24", "2025-12-31"]
            for b in M.BOARDS:
                for e in M.EXITS:
                    output.extend(M.evaluate_pairs({"period": p, "board": b, "exit": e}, [], dates))
        self.assertEqual(len(output), 192)
        epochs = [r for r in output if r["kind"] == "descriptiveEpoch"]
        self.assertEqual(len(epochs), 48)
        self.assertTrue(all(r["endDate"] < M.CUT for r in epochs if r["id"] == "before20240924"))
        self.assertTrue(all(r["startDate"] == M.CUT for r in epochs if r["id"] == "from20240924"))

    def test_actual_cash_open_pnl_and_carried_intervals_not_closed_only(self):
        original, schedule, result, summary = M.ACCOUNT.synthetic_fixture()
        key = {**KEY, **M.candidate_identity("originalEntered__ridge")}
        detail, entries = M.ACCOUNT.attach_episodes(key, original, schedule, result, summary, {"a", "c"})
        got = M.ACCOUNT.metrics(summary, len(schedule), len(original))
        self.assertEqual((got["closedPnlAmount"], got["openUnrealizedPnlAmount"], got["netPnlAmount"]), (0., 100., 100.))
        self.assertEqual(detail[-1]["positionState"], "notScheduled")
        shift = M.ACCOUNT.sample_shift(key, entries, summary)
        self.assertEqual(shift["candidateOnly"]["closedPnlAmount"], -100.)
        measured = M.interval_rows(key, summary, result, original, schedule, [])
        self.assertTrue(all(r["candidate"] == "originalEntered__ridge" and r["population"] == "originalEntered" for r in measured))
        self.assertTrue(all(r["passed"] for r in M.ACCOUNT.partition_intervals(measured, summary, result["curve"])))
        second = next(r for r in measured if r["id"] == "part2")
        self.assertEqual(second["enteringEquity"], 500100.)

    def test_frozen_schedule_mask_and_full_day_support(self):
        dates, paths, decisions = forecast_fixture()
        original = [{"signalId": r["formationId"], "symbol": r["symbol"], "signalDate": r["formationDate"],
                     "strategyId": "s", "originalRank": 1} for r in paths]
        decisions["originalEntered"][0]["models"] = {m: prediction(status="fallback") for m in M.MODELS}
        schedules = {"originalEntered__" + m: deepcopy(original) for m in M.MODELS}
        got = M.feedback_coverage(KEY, "originalEntered", decisions["originalEntered"], original, schedules, dates)
        self.assertEqual(got[0]["targetDayCount"], 4)
        self.assertEqual(got[0]["fallbackOriginalOpportunities"], 2)
        self.assertEqual(got[0]["immatureEpisodeDayObservations"], 12)
        schedules["originalEntered__ridge"].pop()
        with self.assertRaises(ValueError):
            M.feedback_coverage(KEY, "originalEntered", decisions["originalEntered"], original, schedules, dates)

    def test_unfilled_path_and_invalid_cash_label_rejected(self):
        original, rows = full_path_fixture()
        rows[0]["path"]["outcome"]["netPnlAmount"] = 5000.
        with self.assertRaises(ValueError):
            M.normalize_evaluation_paths(rows, original, KEY, "2025-12-31")
        original, rows = full_path_fixture()
        rows[0]["path"]["status"] = "unfilled"
        rows[0]["path"]["outcome"].update(labelEligible=False, fullyClosed=False)
        with self.assertRaises(ValueError):
            M.normalize_evaluation_paths(rows, original, KEY, "2025-12-31")


if __name__ == "__main__":
    unittest.main()
