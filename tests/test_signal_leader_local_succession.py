"""Synthetic local succession sequences; no market data or outcome reads."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import unittest

from service.signal_leader_local_succession import (
    ANCHOR_CONTROL_ID, CANDIDATE_ID, GROUPS, MEMBER_CONTROL_ID, PARENT_ID, PARENT_VERSION,
    PEER_VERSION, Q_FIELDS, RANK_FIELDS, LocalSuccessionStudy, project_parent,
)

DAYS = []
day = date(2022, 1, 3)
while len(DAYS) < 25:
    if day.weekday() < 5:
        DAYS.append(day.isoformat())
    day += timedelta(days=1)
ANCHORS = ["sh600000", "sh600001", "sh600002"]
MEMBERS = ["sz%06d" % (100+i) for i in range(32)]
SYMBOLS = sorted(ANCHORS+MEMBERS)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parent(symbol, index, state="true", breakout=False, rank=None, kind=None, rank_missing=False):
    rank = ((.98, .97) if symbol in ANCHORS else (.94, .93)) if rank is None else rank
    values = dict(zip(Q_FIELDS, (rank[0], .9, rank[1], .05, .5)))
    predicates = {f: {"value": values[f], "passed": True, "reason": None} for f in Q_FIELDS}
    if state == "false":
        predicates[Q_FIELDS[3]].update(value=.2, passed=False)
    if state == "unknown":
        kind = kind or "unevaluable"
    if rank_missing:
        predicates[RANK_FIELDS[0]].pop("reason")
    kind = kind or ("signaled" if state == "true" and breakout else "notTriggered")
    return {"version": PARENT_VERSION, "candidateId": PARENT_ID, "symbol": symbol, "date": DAYS[index],
        "kind": kind, "reason": "synthetic-"+kind,
        "predicates": {**predicates, "closeAbovePrior5High": {"passed": breakout}}, "signal": None}


def parents(index, changes=None):
    return {s: project_parent(parent(s, index, **((changes or {}).get(s, {"breakout": s in ANCHORS})))) for s in SYMBOLS}


def formation(index=10, anchor=ANCHORS[0], rank=1, members=None, kind="formed"):
    names = list(MEMBERS[:20] if members is None else members)
    group = {"version": PEER_VERSION, "anchor": anchor, "formationDate": DAYS[index-10],
        "kind": kind, "reason": "synthetic-"+kind, "members": names, "selectedCount": len(names),
        "requiredCount": 20, "correlations": {s: 1-r*.01 for r, s in enumerate(names)}}
    return {"date": DAYS[index], "symbol": anchor, "rank": rank, "formationDate": group["formationDate"],
            "groupSha256": digest(group), "group": group}


def before():
    study = LocalSuccessionStudy(DAYS, SYMBOLS)
    for i in range(10):
        study.step(DAYS[i], i, parents(i), [])
    return study


def activated(changes=None, forms=None):
    study = before()
    output = study.step(DAYS[10], 10, parents(10, changes), [formation()] if forms is None else forms)
    return study, output


def leading(index, members=MEMBERS[:1], anchors=ANCHORS[:1], anchor_state="false", breakout=True):
    changes = {s: {"state": anchor_state, "rank": (.95, .95)} for s in anchors}
    changes.update({s: {"rank": (.96, .96), "breakout": breakout} for s in members})
    return parents(index, changes)


def counts(output):
    return {sid: sum(s["strategyId"] == sid for s in output["signals"]) for sid in GROUPS}


class LocalSuccessionTests(unittest.TestCase):
    def test_projection_separates_Q_and_individually_known_numeric_ranks(self):
        for state, kind, q in (("true", None, "true"), ("false", None, "false"),
                ("true", "ineligible", "unknown"), ("true", "unevaluable", "unknown")):
            raw = parent(MEMBERS[0], 10, state, kind=kind)
            row = project_parent(raw)
            self.assertEqual(row["eligibleParentQ"], q)
            self.assertTrue(row["rankKnown"])
            self.assertEqual(row["outcomeSha256"], digest(raw))
        raw = parent(MEMBERS[0], 10)
        raw["predicates"][RANK_FIELDS[0]]["passed"] = False
        row = project_parent(raw)
        self.assertTrue(row["rankKnown"])
        self.assertEqual(row["eligibleParentQ"], "false")

    def test_reason_presence_and_both_fields_are_required_not_numeric_value_alone(self):
        for field in RANK_FIELDS:
            for mode in ("missing-reason", "non-null-reason", "missing-pass", "int-pass", "bool-value", "out-of-domain", "missing-value"):
                with self.subTest(field=field, mode=mode):
                    raw = parent(MEMBERS[0], 10)
                    item = raw["predicates"][field]
                    if mode == "missing-reason": item.pop("reason")
                    elif mode == "non-null-reason": item["reason"] = "unavailable"
                    elif mode == "missing-pass": item.pop("passed")
                    elif mode == "int-pass": item["passed"] = 1
                    elif mode == "bool-value": item["value"] = True
                    elif mode == "out-of-domain": item["value"] = 1.1
                    else: item.pop("value")
                    row = project_parent(raw)
                    self.assertFalse(row["rankKnown"])
                    self.assertIsNone(row["rankValues"][field])
                    if mode in ("missing-reason", "non-null-reason"):
                        self.assertIsInstance(row["rankEvidence"][field]["value"], float)
        # Nonfinite source tokens cannot exist in the bound strict JSON source.
        for value in (float("nan"), float("inf"), -float("inf")):
            raw = parent(MEMBERS[0], 10)
            raw["predicates"][RANK_FIELDS[0]]["value"] = value
            with self.assertRaises(ValueError): project_parent(raw)

    def test_initial_priority_and_exact_numeric_ties_ignore_symbol_order(self):
        overrides = {MEMBERS[0]: {"state": "unknown", "rank_missing": True},
            MEMBERS[1]: {"state": "false", "rank_missing": True}, MEMBERS[2]: {"rank_missing": True},
            MEMBERS[3]: {"rank": (.98, .97)}, MEMBERS[4]: {"rank": (.99, .91)},
            MEMBERS[5]: {"rank": (.98, .96)}}
        study, out = activated(overrides)
        members = study.episodes[0]["members"]
        self.assertEqual([m["state"] for m in members[:7]], ["initialMemberUnknown", "notInitiallyStrong",
            "initialRankUnknown", "notInitiallyBelow", "notInitiallyBelow", "observing", "observing"])
        self.assertEqual(members[1]["initialFlags"], ["memberQFalse", "memberRankUnknown"])
        self.assertEqual(members[3]["initialComparison"], "equal")
        self.assertEqual(out["rawConfirmations"], [])

    def test_A_clock_full_warmup_F_and_first_pair_no_earlier_than_A_plus_two(self):
        study = LocalSuccessionStudy(DAYS[10:], SYMBOLS, formation_calendar=DAYS)
        out = study.step(DAYS[10], 0, parents(10), [formation()])
        ep = out["formations"][0]["episode"]
        self.assertEqual((ep["formationDate"], ep["peerFormationDate"], ep["formationIndex"], ep["lockThroughIndex"]),
                         (DAYS[10], DAYS[0], 0, 5))
        self.assertEqual(study.step(DAYS[11], 1, leading(11), [])["rawConfirmations"], [])
        out = study.step(DAYS[12], 2, leading(12), [])
        self.assertEqual(out["rawConfirmations"][0]["offset"], 2)
        self.assertEqual(counts(out), {s: 1 for s in GROUPS})

    def test_first_pair_emits_three_policies_with_exact_pair_evidence_once(self):
        study, _ = activated()
        first = study.step(DAYS[11], 11, leading(11), [])
        self.assertEqual(first["rawConfirmations"], [])
        out = study.step(DAYS[12], 12, leading(12), [])
        raw = out["rawConfirmations"][0]
        self.assertEqual(raw["anchorPairGate"], "true")
        self.assertEqual([p["date"] for p in raw["pairEvidence"]], DAYS[11:13])
        self.assertEqual([s["symbol"] for s in out["signals"]], [MEMBERS[0], MEMBERS[0], ANCHORS[0]])
        self.assertEqual(study.episodes[0]["members"][0]["lastLeading"]["date"], DAYS[12])
        self.assertEqual(study.step(DAYS[13], 13, leading(13), [])["rawConfirmations"], [])

    def test_pair_gate_all_nine_Q_combinations_unknown_has_priority_over_true(self):
        for a in ("true", "false", "unknown"):
            for b in ("true", "false", "unknown"):
                with self.subTest(a=a, b=b):
                    study, _ = activated()
                    study.step(DAYS[11], 11, leading(11, anchor_state=a), [])
                    out = study.step(DAYS[12], 12, leading(12, anchor_state=b), [])
                    expected = "unknown" if "unknown" in (a, b) else "true" if a == b == "false" else "false"
                    row = out["rawConfirmations"][0]
                    self.assertEqual(row["anchorPairGate"], expected)
                    self.assertEqual(row["anchorPairFailedDates"], [d for d, q in zip(DAYS[11:13], (a, b)) if q == "true"])
                    self.assertEqual(counts(out)[CANDIDATE_ID], int(expected == "true"))
                    self.assertEqual(counts(out)[MEMBER_CONTROL_ID], 1)

    def test_earlier_anchor_Q_unknown_or_false_has_no_permanent_gate_effect(self):
        for old in ("unknown", "false"):
            study, _ = activated()
            study.step(DAYS[11], 11, parents(11, {ANCHORS[0]: {"state": old}}), [])
            study.step(DAYS[12], 12, leading(12), [])
            out = study.step(DAYS[13], 13, leading(13), [])
            self.assertEqual(out["rawConfirmations"][0]["anchorPairGate"], "true")
            self.assertEqual(counts(out)[CANDIDATE_ID], 1)
            self.assertNotIn("anchorGate", study.episodes[0])

    def test_daily_terminal_priority_preserves_simultaneous_missing_rank_flags(self):
        for q, kind, expected in (("unknown", None, "memberUnknown"), ("false", None, "memberLostStrength"),
                ("true", None, "rankUnknown"), ("false", "ineligible", "memberUnknown")):
            study, _ = activated()
            study.step(DAYS[11], 11, leading(11), [])
            changes = {MEMBERS[0]: {"state": q, "kind": kind, "rank_missing": True},
                       ANCHORS[0]: {"rank_missing": True}}
            out = study.step(DAYS[12], 12, parents(12, changes), [])
            member = study.episodes[0]["members"][0]
            self.assertEqual(member["state"], expected)
            self.assertIn("memberRankUnknown", member["terminalFlags"])
            self.assertIn("anchorRankUnknown", member["terminalFlags"])
            transition = next(t for t in out["transitions"] if t.get("memberSymbol") == MEMBERS[0])
            self.assertEqual((transition["previousLeadingStreak"], transition["leadingStreak"]), (1, 0))
            self.assertEqual(study.step(DAYS[13], 13, leading(13), [])["rawConfirmations"], [])

    def test_unknown_anchor_rank_terminates_member_even_when_member_strength_known(self):
        study, _ = activated()
        out = study.step(DAYS[11], 11, parents(11, {ANCHORS[0]: {"rank_missing": True}}), [])
        self.assertEqual(study.episodes[0]["members"][0]["state"], "rankUnknown")
        self.assertEqual(out["rawConfirmations"], [])
        study.step(DAYS[12], 12, leading(12), [])
        self.assertEqual(study.step(DAYS[13], 13, leading(13), [])["rawConfirmations"], [])

    def test_below_or_equal_resets_streak_without_terminating(self):
        for middle in ((.98, .97), (.98, .98)):
            study, _ = activated()
            study.step(DAYS[11], 11, leading(11), [])
            out = study.step(DAYS[12], 12, parents(12, {MEMBERS[0]: {"rank": (.98, .97)},
                ANCHORS[0]: {"rank": middle}}), [])
            m = study.episodes[0]["members"][0]
            self.assertEqual((m["state"], m["leadingStreak"]), ("observing", 0))
            self.assertEqual(out["rawConfirmations"], [])
            self.assertEqual(study.step(DAYS[13], 13, leading(13), [])["rawConfirmations"], [])
            out = study.step(DAYS[14], 14, leading(14), [])
            self.assertEqual(out["rawConfirmations"][0]["pairEvidence"][0]["date"], DAYS[13])

    def test_first_pair_without_breakout_is_terminal_and_never_waits_for_later(self):
        study, _ = activated()
        study.step(DAYS[11], 11, leading(11), [])
        out = study.step(DAYS[12], 12, leading(12, breakout=False), [])
        self.assertEqual(out["rawConfirmations"], [])
        self.assertEqual(study.episodes[0]["members"][0]["state"], "confirmedWithoutBreakout")
        for i in (13, 14, 15): self.assertEqual(study.step(DAYS[i], i, leading(i), [])["rawConfirmations"], [])

    def test_second_numeric_field_breaks_tie_but_code_does_not(self):
        study, _ = activated()
        changes = {MEMBERS[0]: {"rank": (.96, .95), "breakout": True},
                   ANCHORS[0]: {"state": "false", "rank": (.96, .94)}}
        study.step(DAYS[11], 11, parents(11, changes), [])
        out = study.step(DAYS[12], 12, parents(12, changes), [])
        self.assertEqual(counts(out)[CANDIDATE_ID], 1)

    def test_fifth_day_pair_before_expiry_and_sixth_day_does_not_trigger(self):
        study, _ = activated()
        for i in (11, 12, 13): study.step(DAYS[i], i, parents(i), [])
        study.step(DAYS[14], 14, leading(14), [])
        out = study.step(DAYS[15], 15, leading(15), [])
        self.assertEqual(out["rawConfirmations"][0]["offset"], 5)
        self.assertEqual(study.episodes[0]["members"][1]["state"], "expired")
        self.assertEqual(study.step(DAYS[16], 16, leading(16, MEMBERS[1:2]), [])["transitions"], [])

    def test_fixed_lock_survives_all_early_terminals_and_unlocks_only_A_plus_six(self):
        study, _ = activated({m: {"state": "false"} for m in MEMBERS[:20]})
        for i in range(11, 16):
            out = study.step(DAYS[i], i, parents(i), [formation(i)])
            self.assertEqual(out["formations"][0]["kind"], "formationSuppressedByLock")
        out = study.step(DAYS[16], 16, parents(16), [formation(16)])
        self.assertEqual(out["formations"][0]["kind"], "activated")
        self.assertEqual(len(study.episodes), 2)

    def test_cutoff_censors_observers_only_and_keeps_single_leading_evidence(self):
        study, _ = activated({MEMBERS[1]: {"state": "false"}})
        study.step(DAYS[11], 11, leading(11), [])
        out = study.finish(DAYS[11])
        self.assertEqual(len(out["transitions"]), 19)
        m = out["episodes"][0]["members"][0]
        self.assertEqual((m["state"], m["leadingStreak"]), ("censoredAtStudyCutoff", 1))
        self.assertEqual(m["lastLeading"]["date"], DAYS[11])
        self.assertEqual(out["episodes"][0]["members"][1]["state"], "notInitiallyStrong")
        with self.assertRaises(ValueError): study.finish(DAYS[11])
        with self.assertRaises(ValueError): study.step(DAYS[12], 12, parents(12), [])

    def test_unevaluable_formation_keeps_partial_members_and_fixed_lock(self):
        study, out = activated(forms=[formation(members=MEMBERS[:3], kind="unevaluable")])
        self.assertEqual(out["formations"][0]["kind"], "unevaluableFormation")
        self.assertEqual(len(study.episodes[0]["members"]), 3)
        self.assertTrue(all(m["state"] == "formationUnevaluable" for m in study.episodes[0]["members"]))
        out = study.step(DAYS[11], 11, parents(11), [formation(11)])
        self.assertEqual(out["formations"][0]["kind"], "formationSuppressedByLock")

    def test_global_top5_before_pair_mask_never_promotes_sixth(self):
        forms = [formation(), formation(anchor=ANCHORS[1], rank=2, members=MEMBERS[10:30])]
        study, _ = activated(forms=forms)
        changes = {s: {"rank": (.99, .99), "breakout": True} for s in MEMBERS[:5]+[MEMBERS[20]]}
        changes[ANCHORS[1]] = {"state": "false", "rank": (.92, .92)}
        study.step(DAYS[11], 11, parents(11, changes), [])
        out = study.step(DAYS[12], 12, parents(12, changes), [])
        self.assertEqual(len(out["selection"]["fullRanking"]), 6)
        self.assertEqual([s["memberSymbol"] for s in out["selection"]["slots"]], MEMBERS[:5])
        self.assertEqual(counts(out), {CANDIDATE_ID: 0, MEMBER_CONTROL_ID: 5, ANCHOR_CONTROL_ID: 0})
        self.assertEqual(out["selection"]["fullRanking"][5]["anchorPairGate"], "true")

    def test_multisource_owner_precedes_mask_and_all_sources_consume_pair(self):
        forms = [formation(), formation(anchor=ANCHORS[1], rank=2)]
        study, _ = activated(forms=forms)
        changes = {MEMBERS[0]: {"rank": (.99, .99), "breakout": True},
                   ANCHORS[1]: {"state": "false", "rank": (.92, .92)}}
        study.step(DAYS[11], 11, parents(11, changes), [])
        out = study.step(DAYS[12], 12, parents(12, changes), [])
        self.assertEqual(len(out["rawConfirmations"]), 2)
        self.assertEqual(out["selection"]["uniqueMemberCount"], 1)
        self.assertEqual(out["selection"]["ownership"][0]["ownerEpisodeId"], DAYS[10]+":"+ANCHORS[0])
        self.assertEqual(counts(out)[CANDIDATE_ID], 0)
        self.assertEqual(counts(out)[MEMBER_CONTROL_ID], 1)
        self.assertTrue(all(e["members"][0]["state"] == "confirmed" for e in study.episodes))

    def test_frozen_member_order_and_anchor_parent_rank_not_current_strength(self):
        forms = [formation(anchor=ANCHORS[1], rank=1, members=list(reversed(MEMBERS[:20]))),
                 formation(anchor=ANCHORS[0], rank=2)]
        study, _ = activated(forms=forms)
        changes = {MEMBERS[0]: {"rank": (.999, .99), "breakout": True},
                   MEMBERS[19]: {"rank": (.99, .99), "breakout": True}}
        study.step(DAYS[11], 11, parents(11, changes), [])
        out = study.step(DAYS[12], 12, parents(12, changes), [])
        self.assertEqual([s["memberSymbol"] for s in out["selection"]["slots"]], [MEMBERS[19], MEMBERS[0]])
        self.assertTrue(all(s["anchorSymbol"] == ANCHORS[1] for s in out["selection"]["slots"]))

    def test_anchor_collision_one_original_slot_and_no_backfill(self):
        study, _ = activated()
        study.step(DAYS[11], 11, leading(11, MEMBERS[:3]), [])
        out = study.step(DAYS[12], 12, leading(12, MEMBERS[:3]), [])
        self.assertEqual(counts(out), {CANDIDATE_ID: 3, MEMBER_CONTROL_ID: 3, ANCHOR_CONTROL_ID: 1})
        slots = out["selection"]["slots"]
        self.assertEqual([s["anchorControlDisposition"] for s in slots], ["admitted", "mappedAnchorCollision", "mappedAnchorCollision"])
        self.assertEqual([s["anchorControlRetainedRank"] for s in slots], [1, 1, 1])

    def test_first_pair_consumed_outside_top5_and_masked_unknown(self):
        study, _ = activated()
        study.step(DAYS[11], 11, leading(11, MEMBERS[:7], anchor_state="unknown"), [])
        out = study.step(DAYS[12], 12, leading(12, MEMBERS[:7]), [])
        self.assertEqual(len(out["rawConfirmations"]), 7)
        self.assertEqual(counts(out), {CANDIDATE_ID: 0, MEMBER_CONTROL_ID: 5, ANCHOR_CONTROL_ID: 0})
        self.assertEqual(study.step(DAYS[13], 13, leading(13, MEMBERS[:7]), [])["rawConfirmations"], [])

    def test_identity_members_hash_full_calendar_and_atomic_validation(self):
        def duplicate(row):
            row["group"]["members"][1] = row["group"]["members"][0]
            row["groupSha256"] = digest(row["group"])
        def self_member(row):
            row["group"]["members"][0] = ANCHORS[0]
            row["groupSha256"] = digest(row["group"])
        changes = [lambda r:r.update(groupSha256="0"*64), lambda r:r.update(formationDate=DAYS[1]),
                   lambda r:r.update(date=DAYS[9]), lambda r:r.update(rank=2), duplicate, self_member]
        for mutate in changes:
            study, row = before(), formation()
            mutate(row)
            with self.assertRaises(ValueError): study.step(DAYS[10], 10, parents(10), [row])
            self.assertEqual(study.episodes, [])
            self.assertEqual(study.step(DAYS[10], 10, parents(10), [formation()])["formations"][0]["kind"], "activated")

    def test_complete_population_compact_schema_and_calendar_integrity(self):
        for mode in ("missing", "future", "rank-value-bool", "rank-known-int", "q-contradiction", "rank-pass-contradiction"):
            study, rows = before(), parents(10)
            if mode == "missing": rows.pop(MEMBERS[0])
            elif mode == "future": rows[MEMBERS[0]]["date"] = DAYS[11]
            elif mode == "rank-value-bool": rows[MEMBERS[0]]["rankValues"][RANK_FIELDS[0]] = True
            elif mode == "rank-known-int": rows[MEMBERS[0]]["rankKnown"] = 1
            elif mode == "q-contradiction": rows[MEMBERS[0]]["eligibleParentQ"] = "false"
            else: rows[MEMBERS[0]]["rankEvidence"][RANK_FIELDS[0]]["passed"] = False
            with self.assertRaises(ValueError): study.step(DAYS[10], 10, rows, [])
        study = before()
        with self.assertRaises(ValueError): study.step(DAYS[11], 11, parents(11), [])
        with self.assertRaises(ValueError): study.step(DAYS[11], 10, parents(10), [])
        with self.assertRaises(ValueError): study.step(DAYS[10], True, parents(10), [])
        for days, symbols in ((DAYS[::-1], SYMBOLS), (DAYS, SYMBOLS[::-1]), ([], SYMBOLS)):
            with self.assertRaises(ValueError): LocalSuccessionStudy(days, symbols)
        with self.assertRaises(ValueError): LocalSuccessionStudy(DAYS[10:12]+DAYS[13:], SYMBOLS, formation_calendar=DAYS)

    def test_inputs_returned_snapshots_and_future_mutation_cannot_change_past(self):
        study, other = before(), before()
        rows, forms = parents(10), [formation()]
        original = deepcopy((rows, forms))
        out = study.step(DAYS[10], 10, rows, forms)
        other_out = other.step(DAYS[10], 10, rows, forms)
        stored = deepcopy(out)
        self.assertEqual((rows, forms), original)
        study.step(DAYS[11], 11, leading(11), [])
        other.step(DAYS[11], 11, parents(11, {MEMBERS[0]: {"state": "unknown"}}), [])
        self.assertEqual(out, stored)
        self.assertEqual(other_out, stored)
        out["formations"][0]["episode"]["members"][0]["state"] = "forged"
        exposed = study.episodes
        exposed[0]["members"][0]["initialParent"]["rankValues"][RANK_FIELDS[0]] = 0
        rows[MEMBERS[0]]["rankEvidence"][RANK_FIELDS[0]]["value"] = 0
        forms[0]["group"]["members"].reverse()
        self.assertEqual(study.episodes[0]["members"][0]["state"], "observing")
        self.assertEqual(study.episodes[0]["members"][0]["initialParent"]["rankValues"][RANK_FIELDS[0]], .94)

    def test_empty_stream_and_finish_at_A_remain_explicit_censoring(self):
        study = LocalSuccessionStudy(DAYS[:3], SYMBOLS)
        for i in range(3):
            out = study.step(DAYS[i], i, parents(i), [])
            self.assertEqual(out["selection"]["fullRanking"], [])
            self.assertEqual(counts(out), {sid: 0 for sid in GROUPS})
            self.assertTrue(out["selection"]["noBackfill"])
            self.assertFalse(out["selection"]["activeExclusionApplied"])
        self.assertEqual(study.finish(DAYS[2])["episodes"], [])
        study, _ = activated()
        with self.assertRaises(ValueError): study.finish(DAYS[11])
        final = study.finish(DAYS[10])
        self.assertEqual(len(final["transitions"]), 20)
        self.assertTrue(all(t["offset"] == 0 for t in final["transitions"]))


if __name__ == "__main__":
    unittest.main()
