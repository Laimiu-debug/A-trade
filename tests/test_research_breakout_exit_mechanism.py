"""Boundary and conservation checks with invented episodes; no saved results."""
from copy import deepcopy
import math
import unittest

from service.research_exit_mechanism import COMPONENTS, aggregate, pair_episode
from service.research_breakout_exit_mechanism import (
    ACTIVATION_CLASSES, CANDIDATE_EXIT_GROUPS, EVIDENCE_STATUSES,
    aggregate_activation, pair_activation_episode,
)


def closed(pnl=20., capital=100., **changes):
    return {"entered": True, "entryDate": "2024-01-03", "entryPrice": 10.,
            "exitDate": "2024-01-12", "positionState": "closed", "investedAmount": capital,
            "closedPnlAmount": pnl, "openRealizedPnlAmount": 0., "openUnrealizedPnlAmount": 0.,
            "holdingSessions": 7, **changes}


def opened(**changes):
    return closed(0., **{"exitDate": None, "positionState": "openAtCutoff",
                         "openRealizedPnlAmount": 5., "openUnrealizedPnlAmount": -10., **changes})


def absent():
    return {"entered": False, "entryDate": None, "entryPrice": None, "exitDate": None,
            "positionState": "notEntered", "investedAmount": 0., "closedPnlAmount": 0.,
            "openRealizedPnlAmount": 0., "openUnrealizedPnlAmount": 0., "holdingSessions": None}


def evidence(activation=None, **changes):
    return {"complete": True, "activationDate": activation, "heldCloseCount": 7,
            "activatedCloseCount": int(activation is not None),
            "firstHeldCloseDate": "2024-01-03", "lastHeldCloseDate": "2024-01-11", **changes}


def pair(control=None, candidate=None, proof=None, reason="breakoutVolatilityTrail", **changes):
    return pair_activation_episode(control if control is not None else closed(),
                                   candidate if candidate is not None else closed(),
                                   cutoff_date="2024-01-31", activation_evidence=proof,
                                   candidate_episode_exit_reason=reason, **changes)


class BreakoutExitMechanismTests(unittest.TestCase):
    def test_before_same_day_after_and_open_control_are_distinct(self):
        control = closed(exitDate="2024-01-10")
        for day, expected in (("2024-01-09", "activatedBeforeControlFinalExit"),
                              ("2024-01-10", "activatedOnControlFinalExitDay"),
                              ("2024-01-11", "activatedAfterControlFinalExit")):
            row = pair(control, proof=evidence(day))
            self.assertEqual(row["activationClass"], expected)
            self.assertEqual(row["activationDate"], day)
            self.assertEqual(row["closedPairTiming"], "later")
        row = pair(opened(), opened(), evidence("2024-01-10"), reason=None)
        self.assertEqual(row["activationClass"], "controlStillOpenAtCutoff")
        self.assertEqual(row["maturity"], "openAtCutoff__openAtCutoff")
        self.assertIsNone(row["closedPairTiming"])

    def test_complete_nonactivation_including_unknown_anchor_is_never(self):
        proof = evidence(anchorStatus="unknown", diagnosticWindowKnown=False)
        for control in (closed(), opened()):
            row = pair(control, proof=proof, reason="trailingStop")
            self.assertEqual(row["activationClass"], "neverActivatedByCutoff")
            self.assertEqual(row["evidenceStatus"], "complete")
            self.assertEqual(row["candidateExitGroup"], "trailingStop")

    def test_missing_and_incomplete_never_masquerade_as_nonactivation(self):
        proofs = [None, evidence(complete=False), evidence("2024-01-08", complete=False),
                  evidence(complete=False, heldCloseCount=0, firstHeldCloseDate=None, lastHeldCloseDate=None)]
        rows = [pair(proof=proof) for proof in proofs]
        self.assertTrue(all(row["activationClass"] == "activationEvidenceUnknown" for row in rows))
        self.assertEqual([row["evidenceStatus"] for row in rows], ["missing", "incomplete", "incomplete", "incomplete"])
        result = aggregate_activation(rows)
        self.assertEqual(result["activationEvidenceUnknownCount"], 4)
        self.assertEqual(result["activationClassCounts"]["neverActivatedByCutoff"], 0)

    def test_noncomparable_entries_keep_groups_and_missing_evidence_counts(self):
        rows = [pair(closed(entryPrice=10.01)), pair(absent()),
                pair(candidate=absent(), reason=None), pair(absent(), absent(), reason=None)]
        self.assertEqual([r["group"] for r in rows], ["bothDifferentEntry", "candidateOnly", "controlOnly", "neither"])
        self.assertTrue(all(r["activationClass"] == "notComparableEntry" for r in rows))
        result = aggregate_activation(rows)
        self.assertEqual(result["activationEvidenceUnknownCount"], 2)
        self.assertEqual(result["candidateExitGroupCounts"]["notEntered"], 2)
        self.assertEqual(result["activationClassCounts"]["activationEvidenceUnknown"], 0)

    def test_own_final_open_exit_day_cannot_contain_activation_close(self):
        for proof in (evidence("2024-01-12", lastHeldCloseDate="2024-01-12"),
                      evidence("2024-01-12"), evidence("2024-01-02")):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                pair(proof=proof)
        # Entry-close activation is allowed; it occurs before a later final sell.
        row = pair(proof=evidence("2024-01-03"))
        self.assertEqual(row["activationClass"], "activatedBeforeControlFinalExit")

    def test_cutoff_and_evidence_date_boundaries_are_strict(self):
        cases = [evidence(lastHeldCloseDate="2024-02-01"),
                 evidence(firstHeldCloseDate="2024-01-02"),
                 evidence(firstHeldCloseDate="2024-01-04"),
                 evidence("20240105"), evidence(lastHeldCloseDate="2024-02-30")]
        for proof in cases:
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                pair(proof=proof)
        for side in ("control", "candidate"):
            kwargs = {side: closed(exitDate="2024-02-01")}
            with self.assertRaises(ValueError):
                pair(proof=evidence(), **kwargs)
        with self.assertRaises(ValueError):
            pair_activation_episode(opened(entryDate="2024-02-01"), opened(entryDate="2024-02-01"),
                cutoff_date="2024-01-31", activation_evidence=None, candidate_episode_exit_reason=None)

    def test_complete_counts_and_dates_cannot_silently_disagree(self):
        cases = [evidence(complete=1), evidence(heldCloseCount=True), evidence(heldCloseCount=-1),
                 evidence(heldCloseCount=1), evidence(heldCloseCount=2, lastHeldCloseDate="2024-01-03"),
                 evidence(activatedCloseCount=1), evidence("2024-01-05", activatedCloseCount=0),
                 evidence("2024-01-05", activatedCloseCount=2),
                 evidence(heldCloseCount=0, firstHeldCloseDate=None, lastHeldCloseDate=None)]
        for proof in cases:
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                pair(proof=proof)
        proof = evidence("2024-01-03", heldCloseCount=1, lastHeldCloseDate="2024-01-03")
        self.assertEqual(pair(proof=proof)["evidenceStatus"], "complete")
        with self.assertRaises(ValueError):
            pair(proof={"complete": False})

    def test_closed_final_reason_mapping_and_open_null_semantics(self):
        for reason, expected in (("stopLoss", "stopLoss"), ("trailingStop", "trailingStop"),
                                 ("breakoutVolatilityTrail", "breakoutVolatilityTrail"),
                                 ("historicalST", "otherClosedExit")):
            row = pair(proof=evidence(), reason=reason)
            self.assertEqual(row["candidateEpisodeExitReason"], reason)
            self.assertEqual(row["candidateExitGroup"], expected)
        for bad in (None, "", " stopLoss", 1):
            with self.assertRaises(ValueError):
                pair(proof=evidence(), reason=bad)
        for candidate in (opened(), absent()):
            with self.assertRaises(ValueError):
                pair(candidate=candidate, proof=None, reason="trailingStop")
        self.assertEqual(pair(candidate=opened(), proof=evidence(), reason=None)["candidateExitGroup"], "openAtCutoff")
        with self.assertRaises(ValueError):
            pair(candidate=absent(), proof=evidence(), reason=None)

    def test_all_seven_cells_preserve_money_groups_and_maturity(self):
        control = closed(exitDate="2024-01-10")
        rows = [pair(control, closed(pnl=-10), evidence(), reason="stopLoss"),
                pair(control, closed(pnl=30), evidence("2024-01-09")),
                pair(control, closed(pnl=40), evidence("2024-01-10")),
                pair(control, closed(pnl=50), evidence("2024-01-11")),
                pair(opened(), opened(openUnrealizedPnlAmount=15), evidence("2024-01-08"), reason=None),
                pair(absent(), closed(pnl=60), evidence()),
                pair(control, closed(pnl=-30), None, reason="historicalST")]
        result = aggregate_activation(rows)
        expected = aggregate([pair_episode(row["control"], row["candidate"]) for row in rows])
        self.assertEqual({key: result[key] for key in expected}, expected)
        self.assertEqual(result["activationClassCounts"], dict.fromkeys(ACTIVATION_CLASSES, 1))
        self.assertEqual(sum(result["candidateExitGroupCounts"].values()), 7)
        self.assertEqual(sum(result["maturityCounts"].values()), 7)
        for key in (*COMPONENTS, "controlPnl", "candidatePnl", "deltaPnl"):
            self.assertAlmostEqual(math.fsum(cell[key] for cell in result["activationRows"]), result[key])
        self.assertEqual((result["controlPnl"], result["candidatePnl"], result["deltaPnl"]), (95, 160, 65))
        self.assertAlmostEqual(math.fsum(result[key] for key in COMPONENTS), result["deltaPnl"])

    def test_empty_cells_are_complete_and_no_return_is_invented(self):
        result = aggregate_activation([])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["activationClassCounts"], dict.fromkeys(ACTIVATION_CLASSES, 0))
        self.assertEqual(result["evidenceStatusCounts"], dict.fromkeys(EVIDENCE_STATUSES, 0))
        self.assertEqual(result["candidateExitGroupCounts"], dict.fromkeys(CANDIDATE_EXIT_GROUPS, 0))
        self.assertEqual(len(result["activationRows"]), 7)
        self.assertTrue(all(cell["count"] == 0 and cell["meanReturnDeltaPct"] is None for cell in result["activationRows"]))
        self.assertEqual(sum(result[key] for key in COMPONENTS), 0)

    def test_input_immutability_and_derived_tampering_rejection(self):
        control, candidate, proof = closed(note={"sellFillIds": ["c1", "c2"]}), closed(), evidence()
        original = deepcopy((control, candidate, proof))
        row = pair(control, candidate, proof)
        saved = deepcopy(row)
        aggregate_activation([row])
        self.assertEqual((control, candidate, proof), original)
        self.assertEqual(row, saved)
        row["control"]["note"]["sellFillIds"].append("mutated")
        row["activationEvidence"]["complete"] = False
        self.assertEqual((control, candidate, proof), original)
        with self.assertRaises(ValueError):
            aggregate_activation([row])
        for field, value in (("activationClass", "notComparableEntry"), ("candidateExitGroup", "trailingStop"),
                             ("deltaPnl", 99), ("evidenceStatus", "missing")):
            bad = deepcopy(saved); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                aggregate_activation([bad])

    def test_different_capital_keeps_same_entry_but_price_or_date_does_not(self):
        row = pair(closed(capital=100, pnl=20), closed(capital=200, pnl=60), evidence("2024-01-05"))
        self.assertEqual(row["group"], "bothSameEntry")
        self.assertAlmostEqual(row["returnProfileComponent"], 15)
        self.assertAlmostEqual(row["committedCapitalComponent"], 25)
        for control in (closed(entryPrice=10.000000000000002), closed(entryDate="2024-01-04")):
            other = pair(control, proof=evidence("2024-01-05"))
            self.assertEqual(other["activationClass"], "notComparableEntry")


if __name__ == "__main__":
    unittest.main()
