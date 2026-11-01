"""Synthetic admission, provenance and continuous-account boundary checks."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("common_support_readout_subject", ROOT / "scripts/summarize-common-support-switch.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
KEY = {"period": "2024-2025", "board": "main", "exit": "trail10"}


def prediction(value):
    return {"status": "fallback", "prediction": None, "allow": True, "reason": "insufficientTrainingFormationDays"} if value is None else {
        "status": "fitted", "prediction": value, "allow": value > 0, "reason": "positivePrediction" if value > 0 else "nonPositivePrediction"}


def fixture():
    dates = ["2024-01-" + str(i).zfill(2) for i in range(2, 8)]
    pairs = [(.1, -.1), (-.1, None), (None, -.1), (None, None), (-.1, .1), (.1, .1)]
    decisions = {p: [{"formationDate": d, "asOfDate": "2024-01-" + str(i + 1).zfill(2),
        "models": {m: prediction(pair[j]) for m in M.MODELS}}
        for i, (d, pair) in enumerate(zip(dates, pairs))] for j, p in enumerate(M.POPULATIONS)}
    original = [{"signalId": str(i), "symbol": "s" + str(i), "signalDate": dates[j],
        "strategyId": "strongUndercutReclaimV1", "originalRank": 1}
        for i, j in enumerate((0, 0, 1, 2, 3, 4))]
    return dates, original, decisions, [deepcopy(original[i]) for i in (3, 4, 5)]


def parent_fixture():
    refs = [{"period": p, "board": b, "exit": e, **M.candidate_identity(c),
        "reusedAccount": c == "baseline", "result": {"path": c, "sha256": "x"}}
        for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for c in M.INHERITED]
    parent = {"accounts": [], "accountRows": []}
    for i, descriptor in enumerate(refs):
        key = {k: descriptor[k] for k in (*M.KEYS, "population", "model")}
        metrics = {"accountReturnPct": float(i - 30), "profitFactor": None}
        parent["accounts"].append({**key, "artifacts": deepcopy(descriptor), "metrics": deepcopy(metrics),
            "openPositions": [{"unrealizedPnlAmount": -12.}], "quality": {"unreliableDays": 3}})
        parent["accountRows"].append({**key, "reusedAccount": descriptor["reusedAccount"], "metrics": metrics})
    for field, multiplier in (("yearRows", 2), ("halfYearRows", 4), ("phaseRows", 8),
                              ("curveEpochRows", 1), ("coverageRows", 1), ("partitionReconciliation", 1)):
        parent[field] = [{**{k: r[k] for k in M.KEYS}, "id": str(i), "value": -2.5}
                         for r in refs for i in range(multiplier)]
    return parent, refs


class CommonSupportSwitchReadoutTests(unittest.TestCase):
    def test_four_states_and_exact_common_only_switch(self):
        dates, original, decisions, schedule = fixture()
        got, rows = M.switch_coverage(KEY, "ridge", original, decisions, schedule, dates)
        self.assertEqual([r["supportState"] for r in rows[:4]], list(M.STATES))
        self.assertEqual(got["supportStateDays"], dict(zip(M.STATES, (3, 1, 1, 1))))
        self.assertEqual(got["supportStateSignals"], dict(zip(M.STATES, (3, 1, 1, 1))))
        self.assertEqual([r["selectedAllow"] for r in rows], [False, False, True, True, True, True])
        self.assertEqual((got["selectedAllDays"], got["selectedOriginalDays"]), (3, 3))

    def test_day_denominator_includes_dates_without_original_signals(self):
        dates, original, decisions, schedule = fixture()
        original = original[2:]; schedule = [r for r in schedule if r in original]
        got, _ = M.switch_coverage(KEY, "intercept", original, decisions, schedule, dates)
        self.assertEqual((got["targetDayCount"], got["originalOpportunities"]), (6, 4))
        self.assertEqual((got["bothFittedDays"], got["bothFittedSignals"]), (3, 1))

    def test_changes_vs_original_and_vs_all_have_distinct_signs(self):
        dates, original, decisions, schedule = fixture()
        got, _ = M.switch_coverage(KEY, "intercept", original, decisions, schedule, dates)
        self.assertEqual((got["changedAllowDays"], got["changedAllowSignals"], got["addedSignals"], got["removedSignals"]), (2, 3, 1, 2))
        self.assertEqual((got["commonVsAllChangedAllowDays"], got["commonVsAllChangedAllowSignals"],
                          got["commonVsAllAddedSignals"], got["commonVsAllRemovedSignals"]), (2, 2, 1, 1))
        self.assertNotIn("commonVsAllChangedAllowDays", got["preparedCounts"])

    def test_fallback_preserved_on_all_only_and_neither_dates(self):
        dates, original, decisions, schedule = fixture()
        got, rows = M.switch_coverage(KEY, "ridge", original, decisions, schedule, dates)
        self.assertTrue(rows[2]["selectedAllow"])
        self.assertEqual(rows[2]["models"]["ridge"]["status"], "fallback")
        self.assertIsNone(rows[2]["models"]["ridge"]["prediction"])
        self.assertEqual(got["modelStatusCounts"], {"fallback": 2, "fitted": 4})

    def test_model_specific_allow_not_borrowed_from_other_model(self):
        dates, original, decisions, schedule = fixture()
        decisions["allExecutable"][0]["models"]["ridge"] = prediction(.2)
        ridge_schedule = original[:2] + schedule
        a, _ = M.switch_coverage(KEY, "intercept", original, decisions, schedule, dates)
        b, _ = M.switch_coverage(KEY, "ridge", original, decisions, ridge_schedule, dates)
        self.assertEqual((a["scheduledSignals"], b["scheduledSignals"]), (3, 5))

    def test_zero_prediction_rejects_and_tiny_positive_kept(self):
        self.assertFalse(M.model_value({"models": {"ridge": prediction(0.)}}, "ridge")["allow"])
        self.assertTrue(M.model_value({"models": {"ridge": prediction(1e-30)}}, "ridge")["allow"])
        bad = prediction(1.); bad["allow"] = False
        with self.assertRaises(ValueError):
            M.model_value({"models": {"ridge": bad}}, "ridge")
        bad = prediction(None); bad["prediction"] = 0.
        with self.assertRaises(ValueError):
            M.model_value({"models": {"ridge": bad}}, "ridge")

    def test_frozen_schedule_order_fields_and_duplicates_checked(self):
        dates, original, decisions, schedule = fixture()
        for bad in (list(reversed(schedule)), schedule[:-1], [dict(schedule[0], originalRank=5), *schedule[1:]]):
            with self.assertRaises(ValueError):
                M.switch_coverage(KEY, "ridge", original, decisions, bad, dates)
        with self.assertRaises(ValueError):
            M.switch_coverage(KEY, "ridge", original + [original[0]], decisions, schedule, dates)

    def test_missing_date_and_asof_mismatch_fail_closed(self):
        dates, original, decisions, schedule = fixture()
        bad = deepcopy(decisions); bad["allExecutable"].pop()
        with self.assertRaises(ValueError):
            M.switch_coverage(KEY, "ridge", original, bad, schedule, dates)
        bad = deepcopy(decisions); bad["allExecutable"][0]["asOfDate"] = "2023-12-29"
        with self.assertRaises(ValueError):
            M.switch_coverage(KEY, "ridge", original, bad, schedule, dates)

    def test_prepared_counts_exact_and_inputs_unchanged(self):
        values = fixture(); frozen = deepcopy(values)
        dates, original, decisions, schedule = values
        got, _ = M.switch_coverage(KEY, "ridge", original, decisions, schedule, dates)
        M.switch_coverage(KEY, "ridge", original, decisions, schedule, dates, deepcopy(got["preparedCounts"]))
        bad = deepcopy(got["preparedCounts"]); bad["addedSignals"] += 1
        with self.assertRaises(ValueError):
            M.switch_coverage(KEY, "ridge", original, decisions, schedule, dates, bad)
        self.assertEqual(values, frozen)

    def test_sixty_inherited_accounts_keep_numbers_and_original_descriptor_flags(self):
        parent, refs = parent_fixture(); frozen = deepcopy((parent, refs))
        accounts, flat, tables = M.reuse_tables(parent, refs)
        self.assertEqual(len(accounts), 60)
        self.assertTrue(all(r["reusedAccount"] and r["executionOrigin"] == "reused" for r in flat))
        self.assertEqual(sum(r["sourceReusedAccount"] for r in flat), 12)
        self.assertEqual(sum(r["artifacts"]["reusedAccount"] for r in accounts), 12)
        self.assertEqual([r["metrics"] for r in flat], [r["metrics"] for r in parent["accountRows"]])
        self.assertEqual([len(tables[n]) for n in tables], [120, 240, 480, 60, 60, 60])
        self.assertEqual((parent, refs), frozen)

    def test_inherited_missing_identity_and_changed_descriptor_rejected(self):
        parent, refs = parent_fixture()
        with self.assertRaises(ValueError):
            M.reuse_tables(parent, refs[:-1])
        parent["accounts"][0]["artifacts"]["result"]["sha256"] = "bad"
        with self.assertRaises(ValueError):
            M.reuse_tables(parent, refs)

    def test_all_84_accounts_and_48_signed_new_contrasts(self):
        rows = [{"period": p, "board": b, "exit": e, **M.candidate_identity(c),
            "metrics": {"accountReturnPct": float(i * 10 - 60), "payoffRatio": None}}
            for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for i, c in enumerate(M.CANDIDATES)]
        got = M.compare_accounts(rows)
        self.assertEqual(len(got), 48)
        self.assertEqual(sum(r["comparisonFamily"] == "extraCoverageRemoved" for r in got), 24)
        self.assertEqual(got[0]["candidateMinusControl"], {"accountReturnPct": 40., "payoffRatio": None})
        self.assertEqual(got[1]["candidateMinusControl"]["accountReturnPct"], 20.)
        with self.assertRaises(ValueError):
            M.compare_accounts(rows[:-1])

    def test_baseline_membership_closed_open_and_unfilled_remain_distinct(self):
        rows = [{**KEY, "candidate": "baseline", "formationId": str(i), "scheduled": True, "positionState": state}
                for i, state in enumerate(("closed", "openAtCutoff", "notEntered"))]
        rows.append({**KEY, "candidate": "allExecutable__ridge", "formationId": "4", "scheduled": False, "positionState": "notScheduled"})
        members, counts, n = M.baseline_membership(rows)
        self.assertEqual(members[tuple(KEY.values())], {"0", "1"})
        self.assertEqual(n, 4)
        self.assertEqual(counts[(*KEY.values(), "baseline")], 3)
        with self.assertRaises(ValueError):
            M.baseline_membership(rows + [rows[0]])

    def test_cash_open_pnl_and_continuous_carry_not_closed_only(self):
        original, schedule, result, summary = M.ACCOUNT.synthetic_fixture()
        key = {**KEY, **M.candidate_identity("commonSupport__ridge")}
        detail, entries = M.ACCOUNT.attach_episodes(key, original, schedule, result, summary, {"a", "c"})
        scalar = M.ACCOUNT.metrics(summary, len(schedule), len(original))
        self.assertEqual((scalar["closedPnlAmount"], scalar["openUnrealizedPnlAmount"], scalar["netPnlAmount"]), (0., 100., 100.))
        self.assertEqual(detail[-1]["positionState"], "notScheduled")
        self.assertEqual(M.ACCOUNT.sample_shift(key, entries, summary)["candidateOnly"]["closedPnlAmount"], -100.)
        rows = M.interval_rows(key, summary, result, original, schedule, [])
        self.assertTrue(all(r["candidate"] == "commonSupport__ridge" and r["population"] == "commonSupport" for r in rows))
        self.assertEqual(next(r for r in rows if r["id"] == "part2")["enteringEquity"], 500100.)
        self.assertTrue(all(r["passed"] for r in M.ACCOUNT.partition_intervals(rows, summary, result["curve"])))

    def test_interval_support_counts_follow_formation_dates_not_exit_dates(self):
        original, schedule, result, summary = M.ACCOUNT.synthetic_fixture()
        key = {**KEY, **M.candidate_identity("commonSupport__intercept")}
        selected = [{"formationDate": row["date"], "supportState": "bothFitted", "selectedPopulation": "allExecutable",
            "models": {"intercept": prediction(.1)}} for row in result["curve"]]
        rows = M.interval_rows(key, summary, result, original, schedule, selected)
        phases = [r for r in rows if r["kind"] == "retrospectiveNarrativePhase"]
        self.assertEqual([r["switchSupportStateCounts"]["bothFitted"] for r in phases], [4, 3])
        self.assertEqual([r["closedEpisodes"]["count"] for r in phases], [1, 1])


if __name__ == "__main__":
    unittest.main()
