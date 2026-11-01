"""Only synthetic posthoc composition; no historical source or account IO."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("regime_maturity", ROOT / "scripts/summarize-independent-shadow-regime-maturity.py")
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)
FSPEC = importlib.util.spec_from_file_location("label_readout_fixtures", ROOT / "tests/test_independent_shadow_labels_readout.py")
F = importlib.util.module_from_spec(FSPEC); FSPEC.loader.exec_module(F)


def training(args, at):
    return M.build_online_feedback(args["calendar"], [args["calendar"][at]],
        args["environment_rows"], args["old_labels"])["dailyRows"][0]


class IndependentShadowRegimeMaturityTests(unittest.TestCase):
    def test_forming_exactly_on_cutoff_is_post_and_daily_weight_not_episode_share(self):
        args = F.synthetic_fixture(old_count=20)
        cut = args["calendar"].index(M.CUT)
        F.add_path(args, cut, old=True, fid="anotherPost", y=.2)
        row = training(args, 24)
        got = M.describe_day(row, deepcopy(row), "oldActual")
        post_days = 21 - cut
        self.assertEqual(got["postRegimeFormationDayCount"], post_days)
        self.assertEqual(got["postRegimeEpisodeCount"], post_days + 1)
        self.assertAlmostEqual(got["postRegimeWeightShare"], post_days / 20)
        self.assertNotEqual(got["postRegimeWeightShare"], (post_days + 1) / 21)

    def test_no_eligible_labels_is_null_not_zero(self):
        args = F.synthetic_fixture()
        row = training(args, 1)
        got = M.describe_day(row, row, "oldActual")
        self.assertIsNone(got["postRegimeWeightShare"])
        self.assertFalse(got["hasPostRegimeMaturedLabels"])
        self.assertEqual(got["weightSum"], 0.)

    def test_closed_on_current_day_not_visible_until_following_decision(self):
        args = F.synthetic_fixture(old_count=20)
        before, after = (training(args, at) for at in (23, 24))
        self.assertIsNone(M.describe_day(before, before, "oldActual")["postRegimeWeightShare"])
        self.assertGreater(M.describe_day(after, after, "oldActual")["postRegimeWeightShare"], 0)
        self.assertEqual(after["asOfDate"], args["calendar"][23])

    def test_compact_and_full_reference_match_and_tamper_rejected(self):
        args = F.synthetic_fixture(old_count=20)
        row = training(args, 24)
        symbols = {r["formationId"]: r["symbol"] for r in args["path_rows"]}
        compact = M.BASE.compact_decision(row, symbols)
        before = deepcopy(row)
        self.assertEqual(M.describe_day(row, compact, "allExecutable"), M.describe_day(row, row, "oldActual"))
        self.assertEqual(row, before)
        for change in ("digest", "support", "weight", "immature"):
            bad = deepcopy(compact)
            if change == "digest": bad["trainingPopulation"]["labelsDigest"] = "bad"
            elif change == "support": bad["support"]["eligibleEpisodeCount"] += 1
            elif change == "weight": bad["trainingSummary"]["weightSum"] += 1
            else: bad["immaturePopulation"]["digest"] = "bad"
            with self.subTest(change=change), self.assertRaises(ValueError):
                M.describe_day(row, bad, "allExecutable")

    def test_unknown_original_features_remain_excluded_from_era_weight(self):
        args = F.synthetic_fixture(old_count=20)
        cut = args["calendar"].index(M.CUT)
        args["environment_rows"][cut]["amount"]["medianRelativeToOwnPrior20"] = None
        row = training(args, 24)
        got = M.describe_day(row, row, "oldActual")
        self.assertEqual(got["labelXUnknownCount"], 1)
        self.assertEqual(got["eligibleFormationDayCount"], 19)
        self.assertAlmostEqual(got["postRegimeWeightShare"], (20 - cut) / 19)
        self.assertEqual(got["modelStatus"], "fallback")

    def test_first_post_label_and_epoch_coverage_do_not_imply_fit(self):
        args = F.synthetic_fixture(old_count=19)
        full = M.build_online_feedback(args["calendar"], args["target_dates"], args["environment_rows"], args["old_labels"])
        daily = [M.describe_day(r, r, "oldActual") for r in full["dailyRows"]]
        rows = M.summarize_days({**args["key"], "sample": "oldActual"}, daily)
        overall = rows[0]
        self.assertEqual(overall["firstPostRegimeMaturedLabelDate"], args["calendar"][24])
        self.assertEqual(overall["firstPostRegimeMaturedLabelAsOfDate"], args["calendar"][23])
        self.assertEqual(overall["fittedDays"], 0)
        self.assertGreater(overall["fallbackDaysWithPostRegimeMaturedLabels"], 0)
        self.assertEqual(sum(r["dayCount"] for r in rows[1:]), overall["dayCount"])
        self.assertEqual(rows[1]["daysWithPostRegimeMaturedLabels"], 0)

    def test_future_return_and_exit_perturbation_does_not_affect_prior_composition(self):
        args = F.synthetic_fixture(old_count=20)
        F.add_path(args, 21, old=True, fid="future", exit_at=45, y=.8)
        before = training(args, 25)
        changed = deepcopy(args)
        changed["old_labels"][-1].update(exitDate=changed["calendar"][46], netReturn=-.7)
        after = training(changed, 25)
        self.assertEqual(M.describe_day(before, before, "oldActual"), M.describe_day(after, after, "oldActual"))


if __name__ == "__main__":
    unittest.main()
