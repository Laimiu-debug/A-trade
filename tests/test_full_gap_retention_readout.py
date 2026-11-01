"""Synthetic clocks, maturity, account cash and reporting-denominator checks."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gap_readout_subject", ROOT / "scripts/summarize-full-gap-retention.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
KEY = {"period": "2024-2025", "board": "main", "exit": "trail10", "policy": "retained"}


def fixture():
    original, schedule, result, summary = M.ACCOUNT.synthetic_fixture()
    formations = ["2023-12-29", "2024-01-01", "2024-01-03", "2024-01-04"]
    opportunities = [{"formationId": s["signalId"], "symbol": s["symbol"],
        "originalRank": s["originalRank"], "formationDate": f, "decisionDate": s["signalDate"],
        "confirmation": {"status": "known"}, "retained": i < 3,
        "signals": {"control": deepcopy(s), "retained": deepcopy(s) if i < 3 else None}}
        for i, (s, f) in enumerate(zip(original, formations))]
    for status, sid in (("unknown", "e"), ("censored", "f")):
        opportunities.append({"formationId": sid, "symbol": "symbol" + sid, "originalRank": 1,
            "formationDate": "2024-01-07", "decisionDate": "2024-01-08" if status == "unknown" else None,
            "confirmation": {"status": status}, "retained": None, "signals": {"control": None, "retained": None}})
    counts = {"sourceSymbols": 6, "targetDays": 7, "symbolDays": 42,
        "rawSetups": 9, "lockedSetups": 1, "rankExcludedSetups": 2, "opportunities": 6,
        "confirmation": {"known": 4, "unknown": 1, "censored": 1},
        "outcomes": {"retained": 3, "filled": 1, "unknown": 1, "censored": 1},
        "signals": {"control": 4, "retained": 3}, "qualityReasonCounts": {"missing": 1}}
    return opportunities, {"control": original, "retained": schedule}, result, summary, counts


class GapReadoutTests(unittest.TestCase):
    def test_top5_is_distinct_from_raw_locked_ranked_denominator(self):
        opps, schedules, _, _, counts = fixture()
        row = M.formation_coverage({"period": "2024-2025", "board": "main"}, opps, counts, schedules)
        self.assertEqual((row["rawSetups"], row["opportunities"]), (9, 6))
        self.assertEqual(row["confirmation"], {"known": 4, "unknown": 1, "censored": 1})

    def test_broken_setup_conservation_rejected(self):
        opps, schedules, _, _, counts = fixture(); counts["lockedSetups"] = 0
        with self.assertRaises(ValueError): M.formation_coverage({}, opps, counts, schedules)

    def test_schedule_reorder_rejected(self):
        opps, schedules, _, _, counts = fixture(); schedules["retained"].reverse()
        with self.assertRaises(ValueError): M.formation_coverage({}, opps, counts, schedules)

    def test_unknown_and_censored_remain_distinct_not_zero_returns(self):
        opps, schedules, result, summary, _ = fixture()
        detail, entries = M.join_episodes(KEY, opps, schedules["retained"], result, summary)
        self.assertEqual(len(detail), 6); self.assertEqual(len(entries), 3)
        self.assertEqual([r["admissionStatus"] for r in detail[-3:]], ["excluded", "unknown", "censored"])
        self.assertTrue(all(r["accountDisposition"] is None for r in detail[-3:]))

    def test_same_symbol_reentry_does_not_use_null_sell_signal_id(self):
        opps, schedules, result, summary, _ = fixture()
        _, entries = M.join_episodes(KEY, opps, schedules["retained"], result, summary)
        self.assertEqual([r["formationId"] for r in entries], ["a", "b", "c"])
        self.assertEqual([r["positionState"] for r in entries], ["closed", "closed", "openAtCutoff"])
        self.assertEqual([r["closedPnlAmount"] for r in entries], [100., -100., 0.])
        self.assertIsNone(entries[-1]["closedReturnPct"])

    def test_formation_decision_entry_clocks_kept_separately(self):
        opps, schedules, result, summary, _ = fixture()
        detail, entries = M.join_episodes(KEY, opps, schedules["retained"], result, summary)
        self.assertEqual((entries[0]["formationDate"], entries[0]["decisionDate"], entries[0]["entryDate"]),
                         ("2023-12-29", "2024-01-02", "2024-01-03"))
        self.assertIsNone(detail[-1]["signalDate"])

    def test_same_day_entry_rejected(self):
        opps, schedules, result, summary, _ = fixture(); opps[0]["formationDate"] = "2024-01-02"
        with self.assertRaises(ValueError): M.join_episodes(KEY, opps, schedules["retained"], result, summary)

    def test_episode_inputs_unchanged(self):
        args = fixture(); before = deepcopy(args)
        M.join_episodes(KEY, args[0], args[1]["retained"], args[2], args[3])
        self.assertEqual(args, before)

    def test_all_opportunities_reconcile_entered_and_not_scheduled(self):
        opps, schedules, result, summary, _ = fixture()
        detail, entries = M.join_episodes(KEY, opps, schedules["retained"], result, summary)
        row = M.account_coverage(KEY, opps, schedules["retained"], detail, entries, summary)
        self.assertEqual((row["entered"], row["closed"], row["openAtCutoff"], row["notScheduled"]), (3, 2, 1, 3))
        self.assertEqual(row["admissionCounts"], {"included": 3, "excluded": 1, "unknown": 1, "censored": 1})

    def test_account_equity_is_not_closed_pnl(self):
        opps, schedules, _, summary, _ = fixture()
        metrics = M.ACCOUNT.metrics(summary, len(schedules["retained"]), len(opps))
        self.assertEqual(metrics["closedPnlAmount"], 0.)
        self.assertEqual(metrics["openUnrealizedPnlAmount"], 100.)
        self.assertAlmostEqual(metrics["accountReturnPct"], .02)
        self.assertEqual(metrics["meanWinPct"], 10.); self.assertEqual(metrics["meanLossPct"], -10.)

    def test_interval_carry_capital_and_final_exit_attribution(self):
        opps, schedules, result, summary, _ = fixture()
        rows = M.interval_rows(KEY, summary, result, opps, schedules["retained"])
        parts = [r for r in rows if r["kind"] == "retrospectiveNarrativePhase"]
        self.assertEqual(parts[1]["enteringEquity"], 500100.)
        self.assertEqual(parts[1]["closedEpisodes"]["count"], 1)
        self.assertTrue(all(r["passed"] for r in M.ACCOUNT.partition_intervals(rows, summary, result["curve"])))

    def test_interval_formation_and_decision_counts_are_different(self):
        opps, schedules, result, summary, _ = fixture()
        row = M.interval_rows(KEY, summary, result, opps, schedules["retained"])[0]
        self.assertEqual(row["originalFormations"], 5)
        self.assertEqual(row["scheduledFromFormations"], 2)
        self.assertEqual(row["decisionSignals"], 3)

    def test_queued_exit_uses_sell_not_exit_action(self):
        opps, schedules, result, summary, _ = fixture()
        result["decisions"] = [{"date": "2024-01-04", "action": a, "status": "queued", "reason": a}
                               for a in ("sell", "exit")]
        row = M.interval_rows(KEY, summary, result, opps, schedules["retained"])[0]
        self.assertEqual(row["queuedExitReasons"], {"sell": 1})

    def test_all_six_sources_and_two_exits_signed_contrasts(self):
        rows = [{"period": p, "board": b, "exit": e, "policy": m,
                 "metrics": {"accountReturnPct": -5. if m == "control" else -8., "profitFactor": None}}
                for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for m in M.POLICIES]
        comparisons = M.compare_accounts(rows)
        self.assertEqual(len(comparisons), 12)
        self.assertTrue(all(r["candidateMinusControl"]["accountReturnPct"] == -3. for r in comparisons))
        self.assertTrue(all(r["candidateMinusControl"]["profitFactor"] is None for r in comparisons))
        with self.assertRaises(ValueError): M.compare_accounts(rows[:-1])


if __name__ == "__main__":
    unittest.main()
