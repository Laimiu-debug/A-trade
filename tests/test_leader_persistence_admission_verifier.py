"""Independent synthetic checks for common queue before historical masks."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("_leader_persistence_verifier_tests", ROOT / "scripts/verify-leader-persistence-admission.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def fixture():
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(16)]
    symbols = [f"s{i:03d}" for i in range(8)]
    metadata = {"calendar": calendar, "targetDates": calendar[10:], "symbols": symbols,
                "counts": {"sampleCount": 8}, "version": "leader-lag-followers-v1", "strategyId": "leaderLagFollowersV1"}
    rows = []
    fields = ("meanStrengthPercentile20", "minimumStrengthPercentile20", "relativeReturn60Percentile", "closePathMaxDrawdown20", "signedPathEfficiency20")
    states = ("unknown", "failed", "passed", "failed", "passed", "passed", "failed", "passed")
    for i in range(10, 16):
        a, f = calendar[i], calendar[i - 10]
        candidates = []
        for j, symbol in enumerate(symbols):
            values = dict(zip(fields, (.99 - j * .005, .85, .99 - j * .004, .02, .8)))
            current = {"date": a, "status": "passed", "reasons": [], "values": values,
                       "predicates": dict.fromkeys(fields, True), "rankDates20": [], "rankHistory20": [],
                       "knownRankCount20": 20, "prior5High": 10., "close": 11., "breakout": True}
            prior = deepcopy(current)
            prior.update(date=f, status=states[j], breakout=False)
            if states[j] == "failed":
                prior["predicates"][fields[0]] = False
                prior["values"][fields[0]] = .5
            elif states[j] == "unknown":
                prior["values"][fields[0]] = None
                prior["predicates"][fields[0]] = None
                prior["reasons"] = ["syntheticUnknown"]
            candidates.append({"symbol": symbol, "formationDate": a, "peerFormationDate": f,
                "currentEvidence": current, "formationEvidence": prior, "formationStatus": states[j],
                "formationReason": {"unknown": "formationEvidenceUnknown", "failed": "formationFiveConditionsFailed", "passed": "formationFiveConditionsPassed"}[states[j]],
                "originalLeaderRank": None, "selectedTop5": False})
        # Old parent rank was F-filtered, intentionally different from new rank.
        passed = [c for c in candidates if c["formationStatus"] == "passed"]
        for rank, c in enumerate(passed, 1):
            c.update(originalLeaderRank=rank, selectedTop5=True)
        rows.append({"formationDate": a, "peerFormationDate": f,
                     "activationCounts": {"candidate": 8, "notTriggered": 0, "unknown": 0},
                     "formationCounts": {"passed": 4, "failed": 3, "unknown": 1},
                     "leaderCandidates": candidates,
                     "leaderRanking": [{"symbol": c["symbol"], "originalLeaderRank": c["originalLeaderRank"], "selectedTop5": True} for c in passed],
                     "leaderEventIds": [f"leaderLagFollowersV1:{a}:{c['symbol']}" for c in passed],
                     "counts": {"currentCandidateCount": 8, "temporalCandidateCount": 4, "leaderEventCount": 4}})
    return metadata, rows, calendar[10], calendar[-1]


class QueueAndMasksTests(unittest.TestCase):
    def test_full_reference_preserves_evidence_and_rank_holes(self):
        metadata, rows, start, end = fixture()
        before = deepcopy((metadata, rows))
        result = V.reference_build(metadata, rows, start=start, end=end)
        self.assertEqual((metadata, rows), before)
        self.assertEqual(result["counts"]["top5OpportunityCount"], 30)
        self.assertEqual(result["counts"]["selectedFormationCounts"], {"passed": 12, "failed": 12, "unknown": 6})
        self.assertEqual(result["counts"]["signals"], {"knownAll": 24, "persistent": 12, "nonpersistent": 12})
        self.assertEqual([s["originalRank"] for s in result["schedules"]["persistent"][:2]], [3, 5])
        self.assertEqual(result["opportunities"][0]["parentCandidate"], rows[0]["leaderCandidates"][0])
        V.check_schedule_partition(result)

    def test_full_calendar_zero_candidates_keeps_every_day(self):
        metadata, rows, start, end = fixture()
        for row in rows:
            row.update(leaderCandidates=[], leaderRanking=[], leaderEventIds=[],
                       activationCounts={"candidate": 0, "notTriggered": 8, "unknown": 0},
                       formationCounts=dict.fromkeys(V.STATES, 0),
                       counts=dict.fromkeys(("currentCandidateCount", "temporalCandidateCount", "leaderEventCount"), 0))
        result = V.reference_build(metadata, rows, start=start, end=end)
        self.assertEqual(len(result["dailyRows"]), 6)
        self.assertEqual(result["opportunities"], [])
        self.assertEqual(result["schedules"], dict.fromkeys(V.POLICIES, []))

    def test_missing_duplicate_date_or_wrong_cohort_denominator_rejected(self):
        for mode in ("missing", "duplicate", "universe", "order"):
            metadata, rows, start, end = fixture()
            if mode == "missing": rows.pop(1)
            elif mode == "duplicate": rows[1] = deepcopy(rows[0])
            elif mode == "universe": metadata["symbols"].pop()
            else: rows[0]["leaderCandidates"].reverse()
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, AssertionError)): V.reference_build(metadata, rows, start=start, end=end)

    def test_future_numeric_fields_cannot_change_past_schedule(self):
        metadata, rows, start, end = fixture()
        cutoff = rows[2]["formationDate"]
        expected = V.reference_build(metadata, rows, start=start, end=cutoff)
        for row in rows[3:]:
            row["leaderCandidates"] = [{"later": float("nan")}]
        self.assertEqual(V.reference_build(metadata, rows, start=start, end=cutoff), expected)

    def test_parent_evidence_and_old_provenance_tampering_rejected(self):
        for mode in ("predicate", "fClock", "oldRank", "oldEvent", "count", "unknownReason", "equalityBreakout"):
            metadata, rows, start, end = fixture(); c = rows[0]["leaderCandidates"]
            if mode == "predicate": c[0]["currentEvidence"]["predicates"][V.FIELDS[0]] = False
            elif mode == "fClock": rows[0]["peerFormationDate"] = metadata["calendar"][1]
            elif mode == "oldRank": c[2]["originalLeaderRank"] = 3
            elif mode == "oldEvent": rows[0]["leaderEventIds"].pop()
            elif mode == "count": rows[0]["formationCounts"]["unknown"] = 0
            elif mode == "unknownReason": c[0]["formationEvidence"]["reasons"] = []
            else: c[0]["currentEvidence"]["close"] = c[0]["currentEvidence"]["prior5High"]
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, AssertionError)): V.reference_build(metadata, rows, start=start, end=end)

    def test_final_source_day_signal_stays_for_account_censoring(self):
        metadata, rows, start, end = fixture()
        result = V.reference_build(metadata, rows, start=start, end=end)
        self.assertTrue(any(s["signalDate"] == end for s in result["schedules"]["knownAll"]))
        self.assertEqual(result["metadata"]["calendar"][-1], end)

    def test_common_top5_precedes_f_masks_with_unknown_slot(self):
        _, rows, _, _ = fixture()
        queue = V.candidate_queue_reference(rows[0]["leaderCandidates"])
        selected = queue[:5]
        self.assertEqual([c["symbol"] for c in selected], [f"s{i:03d}" for i in range(5)])
        self.assertEqual([i for i, c in enumerate(selected, 1) if V.masks_reference(c["formationStatus"])["persistent"]["status"] == "included"], [3, 5])
        self.assertNotIn("s005", [c["symbol"] for c in selected])

    def test_three_masks_partition_known_only(self):
        for state in V.STATES:
            masks = V.masks_reference(state)
            if state == "unknown": self.assertTrue(all(r["status"] == "unknown" for r in masks.values()))
            else:
                self.assertEqual(masks["knownAll"]["status"], "included")
                self.assertEqual(sum(masks[p]["status"] == "included" for p in ("persistent", "nonpersistent")), 1)

    def test_schedule_union_does_not_permit_rank_refill(self):
        metadata, rows, start, end = fixture()
        result = V.reference_build(metadata, rows, start=start, end=end)
        V.check_schedule_partition(result)
        result["schedules"]["persistent"][0]["originalRank"] = 6
        with self.assertRaises((ValueError, AssertionError)): V.check_schedule_partition(result)

    def test_actual_pretests_must_match_suites_pins_and_precede_inputs(self):
        names = ("test_research_leader_persistence_admission.py", "test_leader_persistence_admission_verifier.py",
                 "test_leader_persistence_admission_readout.py", "test_research_fixed_signal_account.py",
                 "test_research_account_fees.py", "test_research_account_outcomes.py")
        times = [f"2026-10-17T00:0{i}:00+00:00" for i in range(4)]
        plan = {"registeredAt": times[0], "sourcePins": {"fixture": "hash"}}
        tests = {"passed": True, "planSha256": "p", "sourcePins": plan["sourcePins"],
                 "allChecksBeforeFirstNewAccount": True, "startedAt": times[1], "completedAt": times[2],
                 "commands": [{"exitCode": 0, "argv": ["python", "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"], "stderr": "\nOK\n"} for name in names]}
        completion = {"completedAt": times[3]}
        with patch.object(V, "read", return_value=tests), patch.object(V, "sha", return_value="p"):
            V.pretests_reference(ROOT, plan, completion)
            tests["completedAt"] = "2026-10-17T00:05:00+00:00"
            with self.assertRaises((ValueError, AssertionError)): V.pretests_reference(ROOT, plan, completion)
            tests["completedAt"] = times[2]; tests["commands"][0]["exitCode"] = 1
            with self.assertRaises((ValueError, AssertionError)): V.pretests_reference(ROOT, plan, completion)

    def test_sort_uses_a_values_then_symbol_not_old_rank(self):
        _, rows, _, _ = fixture()
        candidates = rows[0]["leaderCandidates"]
        for c in candidates:
            c["currentEvidence"]["values"]["meanStrengthPercentile20"] = .95
            c["currentEvidence"]["values"]["relativeReturn60Percentile"] = .96
        candidates.reverse()
        before = deepcopy(candidates)
        self.assertEqual([r["symbol"] for r in V.candidate_queue_reference(candidates)], sorted(r["symbol"] for r in candidates))
        self.assertEqual(candidates, before)

    def test_duplicate_invalid_current_and_nonfinite_rank_rejected(self):
        for mode in ("duplicate", "notPassed", "noBreakout", "nonfinite"):
            _, rows, _, _ = fixture(); c = rows[0]["leaderCandidates"]
            if mode == "duplicate": c.append(deepcopy(c[0]))
            elif mode == "notPassed": c[0]["currentEvidence"]["status"] = "failed"
            elif mode == "noBreakout": c[0]["currentEvidence"]["breakout"] = False
            else: c[0]["currentEvidence"]["values"]["meanStrengthPercentile20"] = float("nan")
            with self.subTest(mode=mode):
                with self.assertRaises((AssertionError, ValueError)): V.candidate_queue_reference(c)


if __name__ == "__main__":
    unittest.main()
