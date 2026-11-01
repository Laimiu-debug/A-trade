"""Synthetic conservation, timing and independent focused-rebuild checks."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/verify-leader-stage-coverage.py"
SPEC = importlib.util.spec_from_file_location("stage_coverage_independent_tests", PATH)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def fixture():
    return V.old_reference().synthetic_source()


def parent_focus(data, first=100, last=105):
    start, end = data["calendar"][first], data["calendar"][last]
    focused = V.focus_states_reference(data, start=start, end=end)
    daily, _ = V.project_focus_reference(focused)
    # Archived rows contain unrelated downstream evidence; that evidence must be
    # included in the archive digest but not used to rebuild the raw predicates.
    for row in daily:
        row["counts"]["rawEdges"] = 0
    return daily, start, end


def chain_fixture():
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(28)]
    targets = calendar[20:]
    members = [{"symbol": f"s{i:03d}", "frozenMemberRank": i} for i in range(1, 21)]
    evidence = {"values": {V.FIELDS[0]: .95, V.FIELDS[2]: .96}}
    daily, events = [], []
    for at in range(20, 28):
        day, f = calendar[at], calendar[at - 10]
        event_id = f"leaderLagFollowersV1:{day}:anchor"
        selected = at in (20, 21, 26)
        candidate = {"symbol": "anchor", "formationDate": day, "peerFormationDate": f,
                     "currentEvidence": evidence, "formationEvidence": evidence,
                     "formationStatus": "passed", "originalLeaderRank": 1, "selectedTop5": True}
        daily.append({"formationDate": day, "peerFormationDate": f,
            "activationCounts": {"candidate": int(selected), "notTriggered": 30 - int(selected), "unknown": 0},
            "formationCounts": {"passed": int(selected), "failed": 0, "unknown": 0},
            "leaderCandidates": [candidate] if selected else [],
            "leaderRanking": [{"symbol": "anchor", "originalLeaderRank": 1, "selectedTop5": True}] if selected else [],
            "leaderEventIds": [event_id] if selected else [],
            "counts": {name: int(selected) for name in V.COUNT_FIELDS}})
        if selected:
            events.append({"eventId": event_id, "formationDate": day, "peerFormationDate": f,
                           "anchorSymbol": "anchor", "originalLeaderRank": 1,
                           "currentEvidence": evidence, "formationEvidence": evidence,
                           "groupSha256": "synthetic-group", "groupSummary": {
                               "kind": "incomplete" if at == 20 else "formed",
                               "members": members[:3] if at == 20 else members,
                               "selectedCount": 3 if at == 20 else 20}})
    forms = []
    for i, e in enumerate(events):
        forms.append({"parentEventId": e["eventId"], "formationDate": e["formationDate"],
            "peerFormationDate": e["peerFormationDate"], "anchorSymbol": "anchor", "originalLeaderRank": 1,
            "groupSha256": e["groupSha256"], "groupKind": e["groupSummary"]["kind"], "members": e["groupSummary"]["members"],
            "status": ("notFormed", "locked", "formed")[i],
            "lockOwnerEventId": events[0]["eventId"] if i < 2 else e["eventId"],
            "lockThroughIndex": 25 if i < 2 else 31, "createdMemberEpisodes": 20 if i == 2 else 0})
    rows = []
    form = forms[-1]
    for member in members:
        fit = form["parentEventId"] + ":" + member["symbol"]
        for offset in range(5):
            rows.append({**{k: form[k] for k in ("parentEventId", "formationDate", "peerFormationDate", "anchorSymbol", "originalLeaderRank")},
                         "rowId": fit + ":" + str(offset), "fitId": fit, "memberSymbol": member["symbol"],
                         "frozenMemberRank": member["frozenMemberRank"], "offset": offset, "fitStatus": "known",
                         "forecastDate": calendar[26 + offset] if offset < 2 else None,
                         "targetDate": calendar[27] if offset == 0 else None})
    return calendar, targets, 30, daily, events, forms, rows


class FocusedStateReferenceTests(unittest.TestCase):
    def test_focus_rank_history_equals_full_causal_prefix(self):
        data = fixture()
        first, last = data["calendar"][100], data["calendar"][105]
        focused = V.focus_states_reference(data, start=first, end=last)
        old = V.old_reference()
        full = old.source_reference(data, data["calendar"][85], last)
        by_date = {}
        for at, population, states in old.chronological_strength(full):
            day = full["calendar"][at]
            if day in focused["states"]:
                by_date[day] = {s: old.leadership_record(states[s], full["indexed"][s], full["indexed"]["__benchmark__"], at, full["calendar"])
                               for s in full["symbols"]}
        for day, states in focused["states"].items():
            self.assertEqual({s: v["evidence"] for s, v in states.items()}, by_date[day])
        self.assertEqual(focused["rankRows"][0]["date"], data["calendar"][71])
        self.assertEqual(focused["states"][data["calendar"][90]]["s039"]["evidence"]["rankDates20"], data["calendar"][71:91])

    def test_future_prices_and_calendar_do_not_change_focus(self):
        data = fixture(); first, last = data["calendar"][100], data["calendar"][105]
        expected = V.focus_states_reference(data, start=first, end=last)
        for security in data["securities"] + [data["benchmark"]]:
            for bar in security["bars"][106:]:
                bar["close"] = float("nan")
        self.assertEqual(expected, V.focus_states_reference(data, start=first, end=last))
        data["calendar"] = data["calendar"][:106]
        for security in data["securities"] + [data["benchmark"]]:
            security["bars"] = security["bars"][:106]
        self.assertEqual(expected, V.focus_states_reference(data, start=first, end=last))

    def test_rank21_valid_does_not_imply_leadership61_valid(self):
        data = fixture(); first, last = data["calendar"][100], data["calendar"][105]
        original = deepcopy(data)
        del data["securities"][-1]["bars"][50]
        result = V.focus_states_reference(data, start=first, end=last)
        state = result["states"][first]["s039"]
        self.assertIsNotNone(state["relativeReturn20"])
        self.assertEqual(state["evidence"]["status"], "unknown")
        self.assertIn("stock:missingAlignedRawBar", state["evidence"]["reasons"])
        self.assertEqual(len(data["securities"][-1]["bars"]), len(original["calendar"]) - 1)

    def test_complete_projection_and_original_parent_digest(self):
        data = fixture(); parents, start, end = parent_focus(data)
        original = deepcopy((data, parents))
        result = V.reference_build(data, parents, start=start, end=end)
        self.assertEqual((data, parents), original)
        self.assertEqual(result["counts"]["symbolDayCount"], 240)
        self.assertEqual(result["counts"]["top20RowCount"], 120)
        self.assertEqual(sum(result["counts"]["endToEndCounts"].values()), 240)
        self.assertEqual(result["verification"]["parentDailyChecks"][0]["parentDailySha256"], V.digest(parents[0]))
        self.assertEqual(result["counts"]["formationCounts"]["passed"], result["counts"]["temporalCandidateCount"])

    def test_parent_missing_duplicate_or_changed_candidate_is_rejected(self):
        data = fixture(); parents, start, end = parent_focus(data)
        changed = deepcopy(parents)
        changed[0]["activationCounts"]["unknown"] += 1
        for rows in (parents[1:], [parents[0]] + parents, changed):
            with self.subTest(case=len(rows)):
                with self.assertRaises(AssertionError):
                    V.reference_build(data, rows, start=start, end=end)

    def test_top20_does_not_prefilter_61_day_unknown(self):
        data = fixture()
        del data["securities"][-1]["bars"][50]
        parents, start, end = parent_focus(data)
        result = V.reference_build(data, parents, start=start, end=end)
        top = next(r for r in result["dailyRows"][0]["rs20Top20"] if r["symbol"] == "s039")
        self.assertIsNotNone(top["relativeReturn20"])
        self.assertEqual(top["currentEvidence"]["status"], "unknown")

    def test_equal_rs_ties_keep_all_in_rank_population_and_symbol_order(self):
        data = fixture()
        for security in data["securities"]:
            security["bars"] = deepcopy(data["securities"][0]["bars"])
        parents, start, end = parent_focus(data)
        result = V.reference_build(data, parents, start=start, end=end)
        top = result["dailyRows"][0]["rs20Top20"]
        self.assertEqual([r["symbol"] for r in top], sorted(s["symbol"] for s in data["securities"])[:20])
        self.assertTrue(all(r["percentile20"] == .5 for r in top))
        self.assertEqual(result["counts"]["leaderEventCount"], 0)
        self.assertTrue(all(r["populations"]["20"]["count"] == 40 for r in result["rankRows"]))


class FailureAndChainTests(unittest.TestCase):
    def test_unknown_precedes_false_but_masks_keep_both(self):
        e = {"status": "unknown", "predicates": dict.fromkeys(V.FIELDS, True), "breakout": False}
        e["predicates"][V.FIELDS[0]] = False
        e["predicates"][V.FIELDS[2]] = None
        first, mask = V.failure_reference(e, include_breakout=True)
        self.assertEqual(first, "unknown")
        self.assertEqual(mask["false"], [V.FIELDS[0], "breakout"])
        self.assertEqual(mask["unknown"], [V.FIELDS[2]])

    def test_f_ignores_breakout_and_first_failure_respects_frozen_order(self):
        e = {"status": "passed", "predicates": dict.fromkeys(V.FIELDS, True), "breakout": False}
        self.assertEqual(V.failure_reference(e, include_breakout=False)[0], "passed")
        self.assertEqual(V.failure_reference(e, include_breakout=True)[0], "breakout")
        e["predicates"][V.FIELDS[1]] = e["predicates"][V.FIELDS[4]] = False
        first, mask = V.failure_reference(e, include_breakout=True)
        self.assertEqual(first, V.FIELDS[1])
        self.assertEqual(mask["false"], [V.FIELDS[1], V.FIELDS[4], "breakout"])

    def test_incomplete_group_locks_without_extension_and_boundary_slots_stay(self):
        result = V.coverage_chain_reference(*chain_fixture())
        self.assertEqual(result["formation43StatusCounts"], {"formed": 1, "locked": 1, "notFormed": 1})
        self.assertEqual(result["plannedSlots"], 100)
        self.assertEqual(result["unassignedForecastSlots"], 60)

    def test_lock_cannot_extend_or_become_formed_while_suppressed(self):
        for field, value in (("lockThroughIndex", 26), ("status", "formed")):
            values = list(chain_fixture()); values[5][1][field] = value
            with self.assertRaises(AssertionError):
                V.coverage_chain_reference(*values)

    def test_missing_duplicate_or_redated_prediction_slot_rejected(self):
        for variant in ("missing", "duplicate", "redate"):
            values = list(chain_fixture())
            if variant == "missing": values[6].pop()
            elif variant == "duplicate": values[6][-1] = deepcopy(values[6][0])
            else: values[6][0]["forecastDate"] = values[0][27]
            with self.subTest(variant=variant):
                with self.assertRaises(AssertionError):
                    V.coverage_chain_reference(*values)

    def test_missing_daily_and_duplicate_parent_event_rejected(self):
        values = list(chain_fixture()); values[3].pop(2)
        with self.assertRaises(AssertionError): V.coverage_chain_reference(*values)
        values = list(chain_fixture()); values[4].append(deepcopy(values[4][0]))
        with self.assertRaises(AssertionError): V.coverage_chain_reference(*values)

    def test_zero_event_day_count_is_not_missing_day(self):
        values = list(chain_fixture())
        self.assertEqual(len(values[3]), 8)
        result = V.coverage_chain_reference(*values)
        self.assertEqual(result["targetDays"], 8)
        self.assertEqual(result["parentLeaderEvents"], 3)

    def test_pretest_chronology_and_hashes(self):
        dates = [f"2026-10-17T00:0{i}:00+00:00" for i in range(4)]
        plan = {"registeredAt": dates[0], "newAccounts": 0, "reusedAccounts": 0}
        names = ("test_research_leader_stage_coverage.py", "test_leader_stage_coverage_verifier.py", "test_leader_stage_coverage_readout.py")
        tests = {"passed": True, "planSha256": "p", "completedAt": dates[1], "realDataReads": 0, "accountExecutions": 0,
                 "checks": [{"exitCode": 0, "argv": ["python", "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"], "stderr": "\nOK\n"} for name in names]}
        run = {"planSha256": "p", "preExecutionTestsSha256": "t", "startedAt": dates[2], "newAccounts": 0}
        completion = {"schema": "leader-stage-coverage-completion-v1", "passed": True, "planSha256": "p", "preExecutionTestsSha256": "t", "runStartSha256": "r",
                      "completedAt": dates[3], "newAccounts": 0, "reusedAccounts": 0}
        hashes = {"plan": "p", "tests": "t", "run": "r"}
        V.lifecycle_reference(plan, tests, run, completion, hashes)
        run["startedAt"] = dates[0]
        with self.assertRaises(AssertionError): V.lifecycle_reference(plan, tests, run, completion, hashes)
        run["startedAt"] = dates[2]; completion["runStartSha256"] = "different"
        with self.assertRaises(AssertionError): V.lifecycle_reference(plan, tests, run, completion, hashes)


if __name__ == "__main__":
    unittest.main()
