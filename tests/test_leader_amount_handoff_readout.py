"""Synthetic layer conservation, clocks, missing diagnostics and account joins."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("handoff_readout_subject", ROOT / "scripts/summarize-leader-amount-handoff.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
KEY = dict(period="2024-2025", board="main", exit="trail10", policy="handoff")


def fixture():
    original, schedule, result, summary = M.ACCOUNT.synthetic_fixture()
    dates = ["2023-12-29", "2024-01-01", "2024-01-03", "2024-01-04"]
    opps = []
    for i, (signal, day) in enumerate(zip(original, dates)):
        opps.append(dict(formationId=signal["signalId"], symbol=signal["symbol"], originalRank=signal["originalRank"],
            formationDate=day, decisionDate=signal["signalDate"], peerFormationDate="2023-12-01", parentEventId="parent",
            anchorSymbol="anchor", frozenMemberRank=i + 1, amountEvidence={"status": "known"},
            control=True, handoff=i < 3, signals={"control": deepcopy(signal), "handoff": deepcopy(signal) if i < 3 else None}))
    for sid in ("e", "f"):
        opps.append(dict(formationId=sid, symbol="symbol" + sid, originalRank=1,
            formationDate="2024-01-07", decisionDate="2024-01-08", peerFormationDate="2023-12-01", parentEventId="parent",
            anchorSymbol="anchor", frozenMemberRank=5, amountEvidence={"status": "unknown"},
            control=None, handoff=None, signals={"control": None, "handoff": None}))
    for i, row in enumerate(opps):
        row["diagnostics"] = {"member": {"ownRelativeAmount": 1. + i if i != 1 else None},
            "anchor": {"ownRelativeAmount": 2.}, "pairRatios": {"initial": .8, "previous": 1., "current": 1.2 if i < 3 else None}}
    events = [dict(parentEventId=str(i), status=s) for i, s in enumerate(("formed", "locked", "notFormed"))]
    terminal = ["firstBreakout"] * 8 + ["initialUnknown", "initialAbove"] + ["priceUnknown"] * 2 + ["expired"] * 5 + ["censored"] * 3
    mids = [r["formationId"] for r in opps] + ["rank6", "duplicate"] + ["member" + str(i) for i in range(12)]
    members = [dict(episodeId=sid, initialStatus=t if t.startswith("initial") else "observing", terminalStatus=t) for sid, t in zip(mids, terminal)]
    raw = [dict(episodeId=sid, formationId=sid, ownershipStatus="duplicateSuppressed" if i == 7 else "owner",
        selectionStatus="selected" if i < 6 else "rankExcluded" if i == 6 else "duplicateSuppressed") for i, sid in enumerate(mids[:8])]
    counts = dict(sourceSymbols=21, targetDays=7, sourceEvents=3, lockedEvents=1, unlockedFormed=1, unlockedNotFormed=1,
        memberEpisodes=20, initialUnknown=1, initialAbove=1, observedMembers=18, priceUnknown=2, firstBreakout=8,
        expired=5, censored=3, duplicateSuppressed=1, uniqueOwners=7, selectedTop5=6, rankExcluded=1,
        jointKnown=4, jointUnknown=2, crossingTrue=3, crossingFalse=1, opportunities=6,
        signals=dict(control=4, handoff=3), notScheduled=dict(control=2, handoff=3))
    return opps, {"control": original, "handoff": schedule}, result, summary, counts, events, members, raw


class HandoffReadoutTests(unittest.TestCase):
    def test_four_layer_denominators_not_conflated(self):
        o, s, _, _, c, e, m, r = fixture()
        row = M.formation_coverage({}, o, c, s, e, m, r)
        self.assertEqual([row[k] for k in ("sourceEvents", "memberEpisodes", "firstBreakout", "selectedTop5")], [3, 20, 8, 6])
        self.assertTrue(row["conservationPassed"])

    def test_broken_member_conservation_rejected(self):
        o, s, _, _, c, e, m, r = fixture(); c["censored"] = 2
        with self.assertRaises(ValueError): M.formation_coverage({}, o, c, s, e, m, r)

    def test_duplicate_owner_cannot_enter_top5(self):
        o, s, _, _, c, e, m, r = fixture(); r[0]["selectionStatus"] = "duplicateSuppressed"
        with self.assertRaises(ValueError): M.formation_coverage({}, o, c, s, e, m, r)

    def test_original_schedule_order_required(self):
        o, s, _, _, c, e, m, r = fixture(); s["handoff"].reverse()
        with self.assertRaises(ValueError): M.formation_coverage({}, o, c, s, e, m, r)

    def test_unknown_top5_not_zero_and_censor_only_upstream(self):
        o, s, r, z, *_ = fixture(); d, a = M.join_episodes(KEY, o, s["handoff"], r, z)
        self.assertEqual([x["admissionStatus"] for x in d[-3:]], ["excluded", "unknown", "unknown"])
        self.assertTrue(all(x["accountDisposition"] is None for x in d[-3:]))
        self.assertEqual((len(d), len(a)), (6, 3))

    def test_same_symbol_reentry_and_partial_open_separate(self):
        o, s, r, z, *_ = fixture(); _, a = M.join_episodes(KEY, o, s["handoff"], r, z)
        self.assertEqual([x["formationId"] for x in a], ["a", "b", "c"])
        self.assertEqual([x["closedPnlAmount"] for x in a], [100., -100., 0.])
        self.assertIsNone(a[-1]["closedReturnPct"])

    def test_peer_activation_breakout_and_entry_are_separate(self):
        o, s, r, z, *_ = fixture(); _, a = M.join_episodes(KEY, o, s["handoff"], r, z)
        self.assertEqual(tuple(a[0][k] for k in ("peerFormationDate", "formationDate", "decisionDate", "entryDate")),
                         ("2023-12-01", "2023-12-29", "2024-01-02", "2024-01-03"))

    def test_same_day_activation_and_breakout_rejected(self):
        o, s, r, z, *_ = fixture(); o[0]["formationDate"] = "2024-01-02"
        with self.assertRaises(ValueError): M.join_episodes(KEY, o, s["handoff"], r, z)

    def test_inputs_unchanged(self):
        args = fixture(); before = deepcopy(args)
        M.join_episodes(KEY, args[0], args[1]["handoff"], args[2], args[3]); M.diagnostic_rows({}, args[0])
        self.assertEqual(args, before)

    def test_full_account_maturity_conserves_original_top5(self):
        o, s, r, z, *_ = fixture(); d, a = M.join_episodes(KEY, o, s["handoff"], r, z)
        c = M.account_coverage(KEY, o, s["handoff"], d, a, z)
        self.assertEqual((c["entered"], c["closed"], c["openAtCutoff"], c["notScheduled"]), (3, 2, 1, 3))
        self.assertEqual(c["admissionCounts"], dict(included=3, excluded=1, unknown=2))

    def test_account_pnl_includes_open_mark(self):
        o, s, _, z, *_ = fixture(); m = M.account_metrics(z, len(s["handoff"]), len(o))
        self.assertEqual(m["closedPnlAmount"], 0.); self.assertEqual(m["openUnrealizedPnlAmount"], 100.)
        self.assertAlmostEqual(m["accountReturnPct"], .02)

    def test_continuous_interval_not_capital_restart(self):
        o, s, r, z, *_ = fixture(); rows = M.interval_rows(KEY, z, r, o, s["handoff"])
        self.assertEqual([x for x in rows if x["kind"] == "retrospectiveNarrativePhase"][1]["enteringEquity"], 500100.)
        self.assertTrue(all(x["passed"] for x in M.ACCOUNT.partition_intervals(rows, z, r["curve"])))

    def test_activation_and_decision_slice_counts_differ(self):
        o, s, r, z, *_ = fixture(); row = M.interval_rows(KEY, z, r, o, s["handoff"])[0]
        self.assertEqual((row["originalFormations"], row["scheduledFromFormations"], row["decisionSignals"]), (5, 2, 3))

    def test_queued_sell_action_only(self):
        o, s, r, z, *_ = fixture(); r["decisions"] = [dict(date="2024-01-04", action=a, status="queued", reason=a) for a in ("sell", "exit")]
        self.assertEqual(M.interval_rows(KEY, z, r, o, s["handoff"])[0]["queuedExitReasons"], {"sell": 1})

    def test_complete_matrix_signed_contrasts(self):
        rows = [dict(period=p, board=b, exit=e, policy=m, metrics=dict(accountReturnPct=-5. if m == "control" else -8., profitFactor=None))
            for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for m in M.POLICIES]
        self.assertEqual(len(M.compare_accounts(rows)), 12)
        self.assertTrue(all(r["candidateMinusControl"] == dict(accountReturnPct=-3., profitFactor=None) for r in M.compare_accounts(rows)))
        with self.assertRaises(ValueError): M.compare_accounts(rows[:-1])

    def test_diagnostic_unknown_does_not_mask_signal(self):
        o, *_ = fixture(); rows = M.diagnostic_rows({}, o)
        self.assertEqual(rows[0]["memberOwnActivity"]["knownCount"], 2)
        self.assertEqual(rows[0]["memberOwnActivity"]["unknownCount"], 1)
        self.assertEqual(rows[0]["memberOwnActivity"]["distribution"]["mean"], 2.)
        self.assertIsNotNone(o[1]["signals"]["handoff"])

    def test_empty_diagnostic_cells_remain_present_and_null(self):
        rows = M.diagnostic_rows({}, [])
        self.assertEqual([x["group"] for x in rows], ["crossing", "nonCrossing", "jointUnknown"])
        self.assertTrue(all(x["memberOwnActivity"]["distribution"]["mean"] is None for x in rows))


if __name__ == "__main__": unittest.main()
