"""Synthetic first-confirmation sequences only; no market inputs or outcomes."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import unittest

from service.signal_leader_follower_confirmation import (
    ANCHOR_CONTROL_ID, CANDIDATE_ID, GROUPS, MEMBER_CONTROL_ID, PARENT_ID, PARENT_VERSION,
    PEER_VERSION, Q_FIELDS, VERSION, FollowerConfirmationStudy, project_parent,
)


DAYS = []
day = date(2022, 1, 3)
while len(DAYS) < 24:
    if day.weekday() < 5:
        DAYS.append(day.isoformat())
    day += timedelta(days=1)
ANCHORS = ["sh600000", "sh600001", "sh600002"]
MEMBERS = ["sz%06d" % (100+i) for i in range(32)]
SYMBOLS = sorted(ANCHORS+MEMBERS)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parent(symbol, index, state="false", breakout=False, kind=None):
    passed = {key: True for key in Q_FIELDS}
    if state == "false":
        passed[Q_FIELDS[0]] = False
    if state == "unknown":
        passed[Q_FIELDS[0]] = None
    kind = kind or ("unevaluable" if state == "unknown" else "signaled" if state == "true" and breakout else "notTriggered")
    return {"version": PARENT_VERSION, "candidateId": PARENT_ID, "symbol": symbol, "date": DAYS[index],
        "kind": kind, "reason": "synthetic-"+kind,
        "predicates": {**{key: {"passed": value} for key, value in passed.items()},
                       "closeAbovePrior5High": {"passed": breakout}}, "signal": None}


def parents(index, changes=None):
    values = {symbol: project_parent(parent(symbol, index, "true", True)) if symbol in ANCHORS
        else project_parent(parent(symbol, index)) for symbol in SYMBOLS}
    for symbol, args in (changes or {}).items():
        values[symbol] = project_parent(parent(symbol, index, *args))
    return values


def formation(index=10, anchor=ANCHORS[0], rank=1, members=None, kind="formed"):
    names = list(MEMBERS[:20] if members is None else members)
    group = {"version": PEER_VERSION, "anchor": anchor, "formationDate": DAYS[index-10],
        "kind": kind, "reason": "synthetic-"+kind, "members": names, "selectedCount": len(names),
        "requiredCount": 20, "correlations": {symbol: 1-rank*.01 for rank,symbol in enumerate(names)}}
    return {"date": DAYS[index], "symbol": anchor, "rank": rank, "formationDate": group["formationDate"],
            "groupSha256": digest(group), "group": group}


def before(index=10):
    study = FollowerConfirmationStudy(DAYS, SYMBOLS)
    for i in range(index):
        study.step(DAYS[i], i, parents(i), [])
    return study


def activated(changes=None, forms=None):
    study = before()
    output = study.step(DAYS[10], 10, parents(10, changes), [formation()] if forms is None else forms)
    return study, output


def signal_count(output):
    return {sid: sum(s["strategyId"] == sid for s in output["signals"]) for sid in GROUPS}


class LeaderFollowerConfirmationTests(unittest.TestCase):
    def test_exact_projection_does_not_turn_ineligible_or_partial_evidence_into_false(self):
        for state, br, kind, expected in (("true", True, None, "true"), ("true", False, None, "true"),
            ("false", False, None, "false"), ("false", False, "ineligible", "unknown"),
            ("true", False, "unevaluable", "unknown"), ("true", False, "invalid", "unknown")):
            with self.subTest(state=state, kind=kind):
                raw = parent(MEMBERS[0], 10, state, br, kind)
                row = project_parent(raw)
                self.assertEqual(row["eligibleParentQ"], expected)
                self.assertEqual(row["breakout"], raw["kind"] == "signaled")
                self.assertEqual(row["reason"], raw["reason"])
                self.assertEqual(row["outcomeSha256"], digest(raw))
        for invalid in (None, 1, 0, "true", [], {}):
            raw = parent(MEMBERS[0], 10, "true", True)
            raw["predicates"][Q_FIELDS[2]]["passed"] = invalid
            self.assertEqual(project_parent(raw)["eligibleParentQ"], "unknown")
        raw = parent(MEMBERS[0], 10, "false")
        raw.pop("predicates")
        self.assertEqual(project_parent(raw)["eligibleParentQ"], "unknown")

    def test_parent_identity_is_hard_error_and_projection_is_detached(self):
        for field, value in (("version", "other"), ("candidateId", "other"), ("date", "2022-1-3"), ("symbol", "")):
            raw = parent(MEMBERS[0], 10)
            raw[field] = value
            with self.assertRaises(ValueError):
                project_parent(raw)
        raw = parent(MEMBERS[0], 10)
        projected = project_parent(raw)
        projected["fivePassed"][Q_FIELDS[0]] = True
        self.assertIs(raw["predicates"][Q_FIELDS[0]]["passed"], False)

    def test_activation_is_A_not_F_and_all_twenty_initial_states_remain(self):
        study, output = activated({MEMBERS[0]: ("true", True), MEMBERS[1]: ("unknown",)})
        self.assertEqual(output["signals"], [])
        self.assertEqual(output["transitions"], [])
        episode = output["formations"][0]["episode"]
        self.assertEqual(episode["formationDate"], DAYS[10])
        self.assertEqual(episode["peerFormationDate"], DAYS[0])
        self.assertEqual(episode["lockThroughIndex"], 15)
        self.assertEqual(len(episode["members"]), 20)
        self.assertEqual([m["state"] for m in episode["members"][:3]],
                         ["notInitiallyWeak", "initialMemberUnknown", "observing"])
        r = study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("true", True), MEMBERS[1]: ("true", True)}), [])
        self.assertEqual(r["rawConfirmations"], [])

    def test_first_target_day_can_use_F_in_source_warmup_without_prestarting_episode(self):
        study = FollowerConfirmationStudy(DAYS[10:], SYMBOLS, formation_calendar=DAYS)
        r = study.step(DAYS[10], 0, parents(10), [formation(10)])
        episode = r["formations"][0]["episode"]
        self.assertEqual(episode["formationIndex"], 0)
        self.assertEqual(episode["formationDate"], DAYS[10])
        self.assertEqual(episode["peerFormationDate"], DAYS[0])
        self.assertEqual(episode["lockThroughIndex"], 5)
        self.assertEqual(r["transitions"], [])
        r = study.step(DAYS[11], 1, parents(11, {MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(r["rawConfirmations"][0]["offset"], 1)
        self.assertEqual(signal_count(r), {sid: 1 for sid in GROUPS})
        with self.assertRaises(ValueError):
            FollowerConfirmationStudy(DAYS[10:12]+DAYS[13:], SYMBOLS, formation_calendar=DAYS)

    def test_first_true_breakout_emits_three_policies_next_signal_close_only_once(self):
        study, _ = activated()
        r = study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(signal_count(r), {sid: 1 for sid in GROUPS})
        self.assertEqual([s["strategyId"] for s in r["signals"]], list(GROUPS))
        self.assertEqual([s["symbol"] for s in r["signals"]], [MEMBERS[0], MEMBERS[0], ANCHORS[0]])
        self.assertEqual(r["rawConfirmations"][0]["offset"], 1)
        self.assertTrue(all(s["signalDate"] == DAYS[11] and s["rank"] == 1 for s in r["signals"]))
        later = study.step(DAYS[12], 12, parents(12, {MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(later["rawConfirmations"], [])
        self.assertEqual(study.episodes[0]["members"][0]["state"], "confirmed")

    def test_same_day_anchor_false_precedes_member_confirmation_but_shadow_survives(self):
        study, _ = activated()
        r = study.step(DAYS[11], 11, parents(11, {ANCHORS[0]: ("false",), MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(signal_count(r), {CANDIDATE_ID: 0, MEMBER_CONTROL_ID: 1, ANCHOR_CONTROL_ID: 0})
        self.assertEqual(r["transitions"][0]["type"], "anchor")
        self.assertEqual(r["selection"]["slots"][0]["anchorControlDisposition"], "anchorCancelled")
        later = study.step(DAYS[12], 12, parents(12, {MEMBERS[1]: ("true", True)}), [])
        self.assertEqual(signal_count(later)[MEMBER_CONTROL_ID], 1)
        self.assertEqual(later["rawConfirmations"][0]["anchorGate"], "cancelled")

    def test_anchor_first_terminal_wins_in_both_directions_and_cannot_repair(self):
        for first, second, expected in (("unknown", "false", "unknown"), ("false", "unknown", "cancelled")):
            with self.subTest(first=first):
                study, _ = activated()
                study.step(DAYS[11], 11, parents(11, {ANCHORS[0]: (first,)}), [])
                terminal = study.episodes[0]["anchorGateTerminal"]
                study.step(DAYS[12], 12, parents(12, {ANCHORS[0]: (second,)}), [])
                r = study.step(DAYS[13], 13, parents(13, {MEMBERS[0]: ("true", True)}), [])
                self.assertEqual(r["rawConfirmations"][0]["anchorGate"], expected)
                self.assertEqual(study.episodes[0]["anchorGateTerminal"], terminal)
                self.assertEqual(signal_count(r)[CANDIDATE_ID], 0)

    def test_intermediate_member_unknown_including_ineligible_consumes_episode(self):
        for state, kind in (("unknown", None), ("false", "ineligible")):
            with self.subTest(kind=kind):
                study, _ = activated()
                study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: (state, False, kind)}), [])
                r = study.step(DAYS[12], 12, parents(12, {MEMBERS[0]: ("true", True)}), [])
                self.assertEqual(r["rawConfirmations"], [])
                self.assertEqual(study.episodes[0]["members"][0]["state"], "memberUnknown")

    def test_first_true_without_breakout_never_waits_for_later_breakout(self):
        study, _ = activated()
        r = study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("true", False)}), [])
        self.assertEqual(r["rawConfirmations"], [])
        r = study.step(DAYS[12], 12, parents(12, {MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(r["rawConfirmations"], [])
        self.assertEqual(study.episodes[0]["members"][0]["state"], "confirmedWithoutBreakout")

    def test_fifth_day_evaluates_before_expiry_and_no_sixth_day_trigger(self):
        study, _ = activated()
        for i in range(11, 15):
            study.step(DAYS[i], i, parents(i), [])
        r = study.step(DAYS[15], 15, parents(15, {MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(len(r["rawConfirmations"]), 1)
        self.assertEqual(r["rawConfirmations"][0]["offset"], 5)
        self.assertEqual(study.episodes[0]["members"][1]["state"], "expired")
        late = study.step(DAYS[16], 16, parents(16, {MEMBERS[1]: ("true", True)}), [])
        self.assertEqual(late["rawConfirmations"], [])
        self.assertEqual(late["transitions"], [])

    def test_cutoff_censors_only_observing_and_retains_earlier_terminal_states(self):
        study, _ = activated()
        study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("true", False), MEMBERS[1]: ("unknown",)}), [])
        final = study.finish(DAYS[11])
        self.assertEqual(len(final["transitions"]), 18)
        self.assertEqual([m["state"] for m in final["episodes"][0]["members"][:3]],
                         ["confirmedWithoutBreakout", "memberUnknown", "censoredAtStudyCutoff"])
        self.assertIsNone(final["transitions"][0]["parent"])
        with self.assertRaises(ValueError):
            study.finish(DAYS[11])
        with self.assertRaises(ValueError):
            study.step(DAYS[12], 12, parents(12), [])

    def test_fixed_six_day_lock_survives_early_member_and_anchor_termination(self):
        study, _ = activated({m: ("true", True) for m in MEMBERS[:20]})
        study.step(DAYS[11], 11, parents(11, {ANCHORS[0]: ("false",)}), [])
        for i in range(12, 16):
            r = study.step(DAYS[i], i, parents(i), [formation(i)])
            self.assertEqual(r["formations"][0]["kind"], "formationSuppressedByLock")
            self.assertEqual(r["formations"][0]["lockedByEpisodeId"], DAYS[10]+":"+ANCHORS[0])
        r = study.step(DAYS[16], 16, parents(16), [formation(16)])
        self.assertEqual(r["formations"][0]["kind"], "activated")
        self.assertEqual(len(study.episodes), 2)
        self.assertEqual(study.episodes[1]["formationIndex"], 16)

    def test_unevaluable_group_is_retained_and_locks_without_manufacturing_members(self):
        study, r = activated(forms=[formation(members=MEMBERS[:3], kind="unevaluable")])
        self.assertEqual(r["formations"][0]["kind"], "unevaluableFormation")
        self.assertEqual(len(study.episodes[0]["members"]), 3)
        self.assertTrue(all(m["state"] == "formationUnevaluable" for m in study.episodes[0]["members"]))
        r = study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("true", True)}), [formation(11)])
        self.assertEqual(r["formations"][0]["kind"], "formationSuppressedByLock")
        self.assertEqual(r["rawConfirmations"], [])

    def test_source_identity_hash_calendar_rank_and_members_fail_before_state_mutation(self):
        def wrong_hash(row): row.update(groupSha256="0"*64)
        def future_F(row): row.update(formationDate=DAYS[1])
        def wrong_A(row): row.update(date=DAYS[9])
        def wrong_rank(row): row.update(rank=2)
        def duplicate_member(row):
            row["group"]["members"][1] = row["group"]["members"][0]
            row["groupSha256"] = digest(row["group"])
        def own_member(row):
            row["group"]["members"][0] = ANCHORS[0]
            row["groupSha256"] = digest(row["group"])
        for mutate in (wrong_hash, future_F, wrong_A, wrong_rank, duplicate_member, own_member):
            with self.subTest(mutation=mutate.__name__):
                study, row = before(), formation()
                mutate(row)
                with self.assertRaises(ValueError):
                    study.step(DAYS[10], 10, parents(10), [row])
                self.assertEqual(study.episodes, [])
                self.assertEqual(study.step(DAYS[10], 10, parents(10), [formation()])["formations"][0]["kind"], "activated")

    def test_full_population_and_sequential_calendar_are_required(self):
        for mode in ("missing", "wrong-symbol", "future-date", "q-contradiction", "breakout-int"):
            with self.subTest(mode=mode):
                study, rows = before(), parents(10)
                if mode == "missing": rows.pop(MEMBERS[0])
                elif mode == "wrong-symbol": rows[MEMBERS[0]]["symbol"] = MEMBERS[1]
                elif mode == "future-date": rows[MEMBERS[0]]["date"] = DAYS[11]
                elif mode == "q-contradiction": rows[MEMBERS[0]]["eligibleParentQ"] = "true"
                else: rows[MEMBERS[0]]["breakout"] = 0
                with self.assertRaises(ValueError):
                    study.step(DAYS[10], 10, rows, [])
        study = before()
        with self.assertRaises(ValueError):
            study.step(DAYS[11], 11, parents(11), [])
        with self.assertRaises(ValueError):
            study.step(DAYS[11], 10, parents(10), [])
        with self.assertRaises(ValueError):
            study.step(DAYS[10], True, parents(10), [])

    def test_global_top5_precedes_anchor_mask_and_does_not_promote_sixth(self):
        forms = [formation(), formation(anchor=ANCHORS[1], rank=2, members=MEMBERS[10:30])]
        study, _ = activated(forms=forms)
        changes = {m: ("true", True) for m in MEMBERS[:5]+[MEMBERS[20]]}
        changes[ANCHORS[0]] = ("false",)
        r = study.step(DAYS[11], 11, parents(11, changes), [])
        self.assertEqual(len(r["selection"]["fullRanking"]), 6)
        self.assertEqual([s["memberSymbol"] for s in r["selection"]["slots"]], MEMBERS[:5])
        self.assertEqual(signal_count(r), {CANDIDATE_ID: 0, MEMBER_CONTROL_ID: 5, ANCHOR_CONTROL_ID: 0})
        self.assertFalse(r["selection"]["fullRanking"][5]["selectedTop5"])
        self.assertEqual(r["selection"]["fullRanking"][5]["anchorGate"], "alive")

    def test_multisource_ownership_keeps_earliest_even_if_later_anchor_is_alive(self):
        study, _ = activated()
        study.step(DAYS[11], 11, parents(11, {ANCHORS[0]: ("false",)}), [formation(11, anchor=ANCHORS[1])])
        r = study.step(DAYS[12], 12, parents(12, {MEMBERS[0]: ("true", True)}), [])
        self.assertEqual(len(r["rawConfirmations"]), 2)
        self.assertEqual(r["selection"]["uniqueMemberCount"], 1)
        owner = r["selection"]["ownership"][0]
        self.assertEqual(owner["ownerEpisodeId"], DAYS[10]+":"+ANCHORS[0])
        self.assertEqual(len(owner["discardedRawConfirmationIds"]), 1)
        self.assertEqual(signal_count(r)[CANDIDATE_ID], 0)
        self.assertEqual(signal_count(r)[MEMBER_CONTROL_ID], 1)
        self.assertTrue(all(e["members"][0]["state"] == "confirmed" for e in study.episodes))

    def test_frozen_member_rank_and_parent_rank_control_order_not_latest_strength(self):
        reverse = list(reversed(MEMBERS[:20]))
        forms = [formation(anchor=ANCHORS[1], rank=1, members=reverse), formation(anchor=ANCHORS[0], rank=2)]
        study, _ = activated(forms=forms)
        changes = {m: ("true", True) for m in (MEMBERS[0], MEMBERS[19])}
        r = study.step(DAYS[11], 11, parents(11, changes), [])
        self.assertEqual([s["memberSymbol"] for s in r["selection"]["slots"]], [MEMBERS[19], MEMBERS[0]])
        self.assertTrue(all(s["anchorSymbol"] == ANCHORS[1] for s in r["selection"]["slots"]))
        self.assertEqual([r["frozenMemberRank"] for r in r["selection"]["fullRanking"]], [1, 20])

    def test_anchor_control_collision_retains_first_original_slot_without_backfill(self):
        study, _ = activated()
        r = study.step(DAYS[11], 11, parents(11, {m: ("true", True) for m in MEMBERS[:3]}), [])
        self.assertEqual(signal_count(r), {CANDIDATE_ID: 3, MEMBER_CONTROL_ID: 3, ANCHOR_CONTROL_ID: 1})
        slots = r["selection"]["slots"]
        self.assertEqual([s["anchorControlDisposition"] for s in slots], ["admitted", "mappedAnchorCollision", "mappedAnchorCollision"])
        self.assertEqual([s["anchorControlRetainedRank"] for s in slots], [1, 1, 1])
        control = next(s for s in r["signals"] if s["strategyId"] == ANCHOR_CONTROL_ID)
        self.assertEqual(control["memberSymbol"], MEMBERS[0])
        self.assertEqual(control["rank"], 1)

    def test_first_trigger_is_consumed_even_below_global_top5_and_never_delayed(self):
        study, _ = activated()
        r = study.step(DAYS[11], 11, parents(11, {m: ("true", True) for m in MEMBERS[:7]}), [])
        self.assertEqual(len(r["rawConfirmations"]), 7)
        self.assertEqual(signal_count(r)[MEMBER_CONTROL_ID], 5)
        r = study.step(DAYS[12], 12, parents(12, {m: ("true", True) for m in MEMBERS[:7]}), [])
        self.assertEqual(r["rawConfirmations"], [])
        self.assertTrue(all(m["state"] == "confirmed" for m in study.episodes[0]["members"][:7]))

    def test_inputs_outputs_and_later_mutations_cannot_rewrite_prior_evidence(self):
        study, other = before(), before()
        values, source = parents(10), [formation()]
        snapshot = deepcopy((values, source))
        past = study.step(DAYS[10], 10, values, source)
        same_past = other.step(DAYS[10], 10, values, source)
        self.assertEqual((values, source), snapshot)
        archived = deepcopy(past)
        study.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("true", True)}), [])
        other.step(DAYS[11], 11, parents(11, {MEMBERS[0]: ("unknown",), ANCHORS[0]: ("false",)}), [])
        self.assertEqual(past, archived)
        self.assertEqual(same_past, archived)
        past["formations"][0]["episode"]["members"][1]["state"] = "forged"
        exposed = study.episodes
        exposed[0]["members"][1]["state"] = "forged"
        values[MEMBERS[1]]["fivePassed"][Q_FIELDS[0]] = True
        source[0]["group"]["members"].reverse()
        self.assertEqual(study.episodes[0]["members"][1]["state"], "observing")
        self.assertFalse(study.episodes[0]["members"][1]["initialParent"]["fivePassed"][Q_FIELDS[0]])

    def test_finish_at_A_has_zero_observations_censored_not_expired_and_wrong_cutoff_rejected(self):
        study, _ = activated()
        with self.assertRaises(ValueError):
            study.finish(DAYS[11])
        final = study.finish(DAYS[10])
        self.assertEqual(len(final["transitions"]), 20)
        self.assertTrue(all(t["offset"] == 0 and t["toState"] == "censoredAtStudyCutoff" for t in final["transitions"]))
        final["episodes"][0]["members"][0]["state"] = "forged"
        self.assertEqual(study.episodes[0]["members"][0]["state"], "censoredAtStudyCutoff")

    def test_empty_formation_stream_retains_full_daily_selection_contract(self):
        study = FollowerConfirmationStudy(DAYS[:3], SYMBOLS)
        for i in range(3):
            r = study.step(DAYS[i], i, parents(i), [])
            self.assertEqual(r["selection"]["fullRanking"], [])
            self.assertEqual(signal_count(r), {sid: 0 for sid in GROUPS})
            self.assertFalse(r["selection"]["activeExclusionApplied"])
            self.assertTrue(r["selection"]["noBackfill"])
        self.assertEqual(study.finish(DAYS[2])["episodes"], [])
        for days, symbols in ((DAYS[::-1], SYMBOLS), (DAYS+[DAYS[-1]], SYMBOLS), (DAYS, SYMBOLS[::-1]), ([], SYMBOLS)):
            with self.assertRaises(ValueError):
                FollowerConfirmationStudy(days, symbols)


if __name__ == "__main__":
    unittest.main()
