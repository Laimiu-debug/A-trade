"""Synthetic membership/maturity/readout boundaries; never load historical data."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_label_readout", ROOT / "scripts/summarize-independent-shadow-labels.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def synthetic_fixture(length=55, old_count=19):
    days, cursor = [], date(2024, 9, 2)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    env = [{"formationDate": d, "asOfDate": days[i - 1] if i else None,
            "price20": {"positiveReturn20Breadth": .5 if i else None},
            "amount": {"medianRelativeToOwnPrior20": 1. if i else None},
            "retention": {"continuous": {"retentionLower": .6 if i else None}}} for i, d in enumerate(days)]
    args = {"key": {"period": "2024-2025", "board": "main", "exit": "trail10"},
            "calendar": days, "target_dates": days, "environment_rows": env,
            "path_rows": [], "schedule": [], "old_labels": []}
    for i in range(1, 21):
        add_path(args, i, old=i <= old_count)
    return refresh(args)


def add_path(args, formation, *, old=False, entered=True, exit_at=23, y=.1, fid=None):
    days = args["calendar"]
    fid = fid or "f" + str(formation)
    symbol = "s" + fid
    signal = {"signalId": fid, "strategyId": "strongUndercutReclaimV1", "symbol": symbol,
              "signalDate": days[formation], "originalRank": 1}
    entry = days[formation + 1]
    label = {"formationId": fid, "formationDate": days[formation], "entryDate": entry,
             "exitDate": days[exit_at] if exit_at is not None else None, "netReturn": y if exit_at is not None else None}
    if old:
        args["old_labels"].append(deepcopy(label))
    closed = entered and exit_at is not None
    outcome = {"entered": entered, "positionClosed": closed, "fullyClosed": closed, "labelEligible": closed,
               "entryDate": entry if entered else None, "finalExitDate": days[exit_at] if closed else None,
               "labelAvailableDate": days[exit_at] if closed else None, "investedAmount": 100000. if entered else None,
               "netPnlAmount": 100000. * y if closed else None, "netReturn": y if closed else None}
    args["schedule"].append(signal)
    args["path_rows"].append({**args["key"], "formationId": fid, "formationDate": days[formation], "symbol": symbol,
        "originalEntered": old, "originalDisposition": {"filledQuantity": 100 if old else 0, "entryDate": entry if old else None},
        "path": {"signal": deepcopy(signal), "exitPolicy": args["key"]["exit"],
                 "status": "closed" if closed else "open" if entered else "unfilled",
                 "label": label if entered else None, "outcome": outcome}})


def refresh(args):
    args["schedule"].sort(key=lambda r: (r["signalDate"], r["signalId"]))
    order = {r["signalId"]: i for i, r in enumerate(args["schedule"])}
    args["path_rows"].sort(key=lambda r: order[r["formationId"]])
    args["old_decisions"] = M.build_online_feedback(args["calendar"], args["target_dates"],
        args["environment_rows"], args["old_labels"])["dailyRows"]
    return args


class IndependentShadowReadoutTests(unittest.TestCase):
    def test_same_library_twenty_formation_boundary(self):
        args = synthetic_fixture()
        got = M.summarize_group(**args)
        a = got["decisionLibraries"]["allExecutable"][24]
        b = got["decisionLibraries"]["originalEntered"][24]
        self.assertEqual((a["support"]["eligibleFormationDayCount"], b["support"]["eligibleFormationDayCount"]), (20, 19))
        self.assertEqual((a["models"]["ridge"]["status"], b["models"]["ridge"]["status"]), ("fitted", "fallback"))
        self.assertEqual(got["dailyComparisonRows"][24]["sameLibrarySelection"]["fitState"], "candidateOnlyFitted")
        self.assertEqual(got["groupReadout"]["populations"]["allExecutable"]["episodes"], 20)

    def test_old_entry_new_unfilled_stays_in_bridge_not_zero_label(self):
        args = synthetic_fixture(old_count=20)
        row = args["path_rows"][0]
        row["path"].update(status="unfilled", label=None)
        row["path"]["outcome"].update(entered=False, positionClosed=False, fullyClosed=False, labelEligible=False,
            entryDate=None, finalExitDate=None, labelAvailableDate=None, investedAmount=None, netPnlAmount=None, netReturn=None)
        got = M.summarize_group(**args)
        self.assertEqual(len(got["bridgeRows"]), 20)
        self.assertEqual(got["groupReadout"]["bridge"]["missingIndependentEntries"], 1)
        self.assertEqual(got["bridgeRows"][0]["category"], "oldEnteredNewNotEntered")
        self.assertIsNone(got["bridgeRows"][0]["netReturnDeltaPct"])
        self.assertEqual(got["decisionLibraries"]["allExecutable"][24]["support"]["eligibleEpisodeCount"], 19)

    def test_formation_equal_weights_recomputed_without_changing_shared_labels(self):
        args = synthetic_fixture(old_count=20)
        add_path(args, 1, old=False, fid="extra", y=.9)
        refresh(args)
        got = M.summarize_group(**args)
        a, b = (got["decisionLibraries"][s][24] for s in M.SAMPLES)
        self.assertEqual(a["trainingSummary"]["weightSum"], 20.)
        self.assertAlmostEqual(a["trainingSummary"]["labelMean"], .12)
        self.assertAlmostEqual(b["trainingSummary"]["labelMean"], .1)
        self.assertAlmostEqual(a["trainingPopulation"]["normalizedEpisodeWeight"]["minimum"], .025)
        self.assertAlmostEqual(a["trainingPopulation"]["maximumFormationDayWeightShare"], .05)

    def test_future_label_values_do_not_change_past_decisions_or_immature_evidence(self):
        args = synthetic_fixture()
        add_path(args, 21, fid="future", exit_at=40, y=.7)
        refresh(args)
        before = M.summarize_group(**args)
        later = deepcopy(args)
        future = next(r["path"] for r in later["path_rows"] if r["formationId"] == "future")
        future["label"]["netReturn"] = -2.
        future["outcome"].update(netReturn=-2., netPnlAmount=-200000.)
        after = M.summarize_group(**later)
        for sample in M.SAMPLES:
            self.assertEqual(before["decisionLibraries"][sample][:41], after["decisionLibraries"][sample][:41])
        row = before["decisionLibraries"]["allExecutable"][25]
        self.assertEqual(row["immaturePopulation"]["episodeCount"], 1)
        self.assertNotIn("trainingLabels", row)
        self.assertNotIn("immature", row)

    def test_epoch_uses_formation_day_not_previous_environment_day(self):
        args = synthetic_fixture()
        got = M.summarize_group(**args)
        all_rows = got["coverageRows"] + got["oldActualCoverageRows"]
        for sample in (*M.SAMPLES, "oldActual"):
            pre = next(r for r in all_rows if r["sample"] == sample and r["id"] == "before20240924")
            post = next(r for r in all_rows if r["sample"] == sample and r["id"] == "from20240924")
            self.assertEqual(post["startDate"], "2024-09-24")
            self.assertEqual(pre["dayCount"] + post["dayCount"], len(args["target_dates"]))
            self.assertEqual(pre["originalOpportunities"] + post["originalOpportunities"], len(args["schedule"]))

    def test_open_label_is_immature_and_bridge_reports_maturity_change(self):
        args = synthetic_fixture()
        row = args["path_rows"][0]["path"]
        row.update(status="open")
        row["label"].update(exitDate=None, netReturn=None)
        row["outcome"].update(positionClosed=False, fullyClosed=False, labelEligible=False,
            finalExitDate=None, labelAvailableDate=None, netPnlAmount=None, netReturn=None)
        got = M.summarize_group(**args)
        self.assertEqual(got["bridgeRows"][0]["category"], "oldClosedNewUnmatured")
        self.assertIsNone(got["bridgeRows"][0]["netReturnDeltaPct"])
        self.assertEqual(got["decisionLibraries"]["originalEntered"][-1]["support"]["immatureCount"], 1)

    def test_bridge_exit_clock_and_return_differences_are_separate(self):
        args = synthetic_fixture()
        row = args["path_rows"][0]["path"]
        row["label"].update(exitDate=args["calendar"][26], netReturn=.3)
        row["outcome"].update(finalExitDate=args["calendar"][26], labelAvailableDate=args["calendar"][26], netReturn=.3, netPnlAmount=30000.)
        got = M.summarize_group(**args)["bridgeRows"][0]
        self.assertTrue(got["sameEntryDate"])
        self.assertFalse(got["sameFinalExitDate"])
        self.assertEqual(got["finalExitDeltaSlots"], 3)
        self.assertAlmostEqual(got["netReturnDeltaPct"], 20.)

    def test_empty_samples_keep_days_and_null_distribution(self):
        args = synthetic_fixture()
        args["schedule"], args["path_rows"], args["old_labels"] = [], [], []
        refresh(args)
        got = M.summarize_group(**args)
        self.assertEqual(got["groupReadout"]["bridge"]["oldActualEntryCount"], 0)
        self.assertIsNone(got["groupReadout"]["bridge"]["netReturnDeltaPct"]["mean"])
        for row in got["coverageRows"]:
            self.assertEqual(row["fittedDays"], 0)
            self.assertEqual(row["fallbackDays"], row["dayCount"])
            self.assertIsNone(row["fittedOriginalOpportunityPct"])

    def test_input_immutable_and_invalid_identity_or_order_rejected(self):
        args = synthetic_fixture()
        saved = deepcopy(args)
        M.summarize_group(**args)
        self.assertEqual(args, saved)
        for kind in ("duplicate", "reorder", "mask"):
            bad = deepcopy(args)
            if kind == "duplicate": bad["path_rows"].append(deepcopy(bad["path_rows"][0]))
            elif kind == "reorder": bad["path_rows"].reverse()
            else: bad["path_rows"][0]["originalEntered"] = False
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                M.summarize_group(**bad)

    def test_late_settlement_and_fabricated_zero_label_rejected(self):
        args = synthetic_fixture()
        args["path_rows"][0]["path"]["outcome"]["labelAvailableDate"] = args["calendar"][24]
        with self.assertRaisesRegex(ValueError, "Delayed settlement"):
            M.summarize_group(**args)
        args = synthetic_fixture()
        args["path_rows"][0]["path"]["status"] = "open"
        args["path_rows"][0]["path"]["outcome"].update(fullyClosed=False, labelEligible=False)
        with self.assertRaisesRegex(ValueError, "Unmatured path"):
            M.summarize_group(**args)

    def test_symbol_concentration_is_not_episode_count(self):
        args = synthetic_fixture(old_count=20)
        for r, signal in zip(args["path_rows"], args["schedule"]):
            r["symbol"] = signal["symbol"] = r["path"]["signal"]["symbol"] = "same-symbol"
        got = M.summarize_group(**args)
        population = got["decisionLibraries"]["allExecutable"][24]["trainingPopulation"]
        self.assertEqual((population["episodeCount"], population["formationDayCount"], population["symbolCount"]), (20, 20, 1))
        self.assertAlmostEqual(population["maximumSymbolWeightShare"], 1.)

    def test_old_unknown_features_remain_unknown_under_sample_expansion(self):
        args = synthetic_fixture(old_count=20)
        args["environment_rows"][1]["amount"]["medianRelativeToOwnPrior20"] = None
        refresh(args)
        got = M.summarize_group(**args)
        for sample in M.SAMPLES:
            row = got["decisionLibraries"][sample][24]
            self.assertEqual(row["support"]["labelXUnknownCount"], 1)
            self.assertEqual(row["support"]["eligibleFormationDayCount"], 19)
            self.assertEqual(row["models"]["ridge"]["status"], "fallback")


if __name__ == "__main__":
    unittest.main()
