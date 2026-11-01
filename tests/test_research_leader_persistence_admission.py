import copy
from datetime import date, timedelta
import unittest

from service import research_leader_persistence_admission as model


def synthetic_fixture(statuses=("unknown", "failed", "passed", "failed", "passed", "passed", "passed", "passed"), days=3):
    first, calendar = date(2024, 1, 2), []
    while len(calendar) < 55:
        if first.weekday() < 5:
            calendar.append(first.isoformat())
        first += timedelta(days=1)
    symbols = [f"sz{i:06}" for i in range(len(statuses))]
    targets = calendar[50:50 + days]
    metadata = {"strategyId": model.PARENT_STRATEGY_ID, "calendar": calendar, "targetDates": targets,
                "symbols": symbols, "counts": {"sampleCount": len(symbols)}}

    def evidence(day, state, index, current=False):
        values = {"meanStrengthPercentile20": .99 - index * .001 if current else .93,
                  "minimumStrengthPercentile20": .85, "relativeReturn60Percentile": .95,
                  "closePathMaxDrawdown20": .05, "signedPathEfficiency20": .7}
        reasons = []
        if state == "failed": values["signedPathEfficiency20"] = .2
        if state == "unknown":
            values["relativeReturn60Percentile"] = None
            reasons = ["relativeReturn60RankUnknown"]
        predicates = {name: None if values[name] is None else values[name] >= threshold if operator == ">=" else values[name] <= threshold
                      for name, operator, threshold in model.THRESHOLDS}
        return {"date": day, "status": state, "reasons": reasons, "values": values, "predicates": predicates,
                "breakout": True if current else False, "close": 11. if current else 10., "prior5High": 10.,
                "rankHistory20": [.93] * 20, "knownRankCount20": 20, "rankDates20": [],
                "extraOriginalEvidence": {"untouched": ["parent"]}}

    daily = []
    for day in targets:
        formation = calendar[calendar.index(day) - 10]
        candidates = []
        for i, (symbol, status) in enumerate(zip(symbols, statuses)):
            candidates.append({"symbol": symbol, "formationDate": day, "peerFormationDate": formation,
                "currentEvidence": evidence(day, "passed", i, current=True),
                "formationEvidence": evidence(formation, status, i), "formationStatus": status,
                "formationReason": {"passed": "formationFiveConditionsPassed", "failed": "formationFiveConditionsFailed",
                                     "unknown": "formationEvidenceUnknown"}[status],
                "originalLeaderRank": None, "selectedTop5": False})
        temporal = sorted((c for c in candidates if c["formationStatus"] == "passed"), key=lambda c: (
            -c["currentEvidence"]["values"]["meanStrengthPercentile20"],
            -c["currentEvidence"]["values"]["relativeReturn60Percentile"], c["symbol"]))
        for i, c in enumerate(temporal, 1): c.update(originalLeaderRank=i, selectedTop5=i <= 5)
        daily.append({"formationDate": day, "peerFormationDate": formation,
            "activationCounts": {"candidate": len(candidates), "notTriggered": 0, "unknown": 0},
            "formationCounts": {s: sum(c["formationStatus"] == s for c in candidates) for s in model.FORMATION_STATES},
            "leaderCandidates": candidates,
            "leaderRanking": [{"symbol": c["symbol"], "originalLeaderRank": c["originalLeaderRank"],
                               "selectedTop5": c["selectedTop5"]} for c in temporal],
            "leaderEventIds": [f"{model.PARENT_STRATEGY_ID}:{day}:{c['symbol']}" for c in temporal[:5]],
            "counts": {"currentCandidateCount": len(candidates), "temporalCandidateCount": len(temporal),
                       "leaderEventCount": min(5, len(temporal))}, "originalDownstream": ["preserved-in-digest"]})
    return metadata, daily


def build(metadata=None, daily=None, **kwargs):
    if metadata is None: metadata, daily = synthetic_fixture()
    return model.build_leader_persistence_admission(metadata, daily,
        start=kwargs.pop("start", metadata["targetDates"][0]), end=kwargs.pop("end", metadata["targetDates"][-1]), **kwargs)


class LeaderPersistenceAdmissionTests(unittest.TestCase):
    def test_common_top5_precedes_f_and_never_backfills(self):
        metadata, daily = synthetic_fixture(days=1)
        result = build(metadata, daily)
        self.assertEqual([o["symbol"] for o in result["opportunities"]], metadata["symbols"][:5])
        self.assertEqual([s["originalRank"] for s in result["schedules"]["knownAll"]], [2, 3, 4, 5])
        self.assertEqual([s["originalRank"] for s in result["schedules"]["persistent"]], [3, 5])
        self.assertEqual([s["originalRank"] for s in result["schedules"]["nonpersistent"]], [2, 4])
        self.assertTrue(daily[0]["leaderCandidates"][5]["selectedTop5"])
        self.assertFalse(result["dailyRows"][0]["currentCandidates"][5]["selectedTop5"])

    def test_common_rank_uses_second_field_then_symbol_tie(self):
        metadata, daily = synthetic_fixture(("failed",) * 7, days=1)
        for i, candidate in enumerate(daily[0]["leaderCandidates"]):
            candidate["currentEvidence"]["values"].update(meanStrengthPercentile20=.95,
                relativeReturn60Percentile=.99 if i in (4, 5) else .95)
        result = build(metadata, daily)
        self.assertEqual([o["symbol"] for o in result["opportunities"]],
                         [metadata["symbols"][i] for i in (4, 5, 0, 1, 2)])

    def test_f_unknown_is_unknown_in_all_three_policies(self):
        metadata, daily = synthetic_fixture(("unknown",) * 3, days=1)
        result = build(metadata, daily)
        self.assertEqual(len(result["opportunities"]), 3)
        for policy in model.POLICIES:
            self.assertEqual(result["schedules"][policy], [])
            self.assertEqual(result["counts"]["policyCounts"][policy], {"included": 0, "excluded": 0, "unknown": 3})
            self.assertTrue(all(o["policyAdmission"][policy]["reason"] == "formationEvidenceUnknown" for o in result["opportunities"]))

    def test_unknown_precedes_false_individual_predicate(self):
        metadata, daily = synthetic_fixture(("unknown",), days=1)
        prior = daily[0]["leaderCandidates"][0]["formationEvidence"]
        prior["values"]["signedPathEfficiency20"] = .2
        prior["predicates"]["signedPathEfficiency20"] = False
        result = build(metadata, daily)
        self.assertEqual(result["opportunities"][0]["formationStatus"], "unknown")
        self.assertEqual(result["schedules"]["nonpersistent"], [])

    def test_f_does_not_require_breakout(self):
        metadata, daily = synthetic_fixture(("passed",), days=1)
        self.assertFalse(daily[0]["leaderCandidates"][0]["formationEvidence"]["breakout"])
        result = build(metadata, daily)
        self.assertEqual(len(result["schedules"]["persistent"]), 1)

    def test_known_all_is_disjoint_union_in_original_order(self):
        result = build()
        left = result["schedules"]["persistent"]
        right = result["schedules"]["nonpersistent"]
        self.assertFalse({s["signalId"] for s in left} & {s["signalId"] for s in right})
        merged = sorted(left + right, key=lambda s: (s["signalDate"], s["originalRank"], s["symbol"]))
        self.assertEqual(result["schedules"]["knownAll"], merged)
        for p in model.POLICIES:
            self.assertEqual(sum(result["counts"]["policyCounts"][p].values()), result["counts"]["top5OpportunityCount"])

    def test_fewer_than_five_and_all_candidate_evidence_retained(self):
        metadata, daily = synthetic_fixture(("passed", "failed"), days=1)
        result = build(metadata, daily)
        self.assertEqual(result["counts"]["top5OpportunityCount"], 2)
        self.assertEqual(result["counts"]["rankExcludedCount"], 0)
        self.assertEqual([c["parentCandidate"] for c in result["dailyRows"][0]["currentCandidates"]], daily[0]["leaderCandidates"])

    def test_zero_candidate_day_retains_explicit_zero_categories(self):
        metadata, daily = synthetic_fixture(days=1)
        daily[0].update(leaderCandidates=[], leaderRanking=[], leaderEventIds=[],
            activationCounts={"candidate": 0, "notTriggered": len(metadata["symbols"]), "unknown": 0},
            formationCounts={s: 0 for s in model.FORMATION_STATES},
            counts={"currentCandidateCount": 0, "temporalCandidateCount": 0, "leaderEventCount": 0})
        result = build(metadata, daily)
        self.assertEqual(len(result["dailyRows"]), 1)
        self.assertEqual(result["opportunities"], [])
        self.assertEqual(result["counts"]["targetDayCount"], 1)
        self.assertEqual(result["counts"]["selectedFormationCounts"], {s: 0 for s in model.FORMATION_STATES})

    def test_new_identity_shared_across_arms_five_signal_fields_only(self):
        result = build()
        expected_fields = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}
        for row in result["opportunities"]:
            self.assertEqual(row["formationId"], row["signalId"])
            self.assertEqual(row["signalDate"], row["formationDate"])
            self.assertTrue(row["signalId"].startswith(model.STRATEGY_ID + ":"))
            for policy, signal in row["signals"].items():
                if signal:
                    self.assertEqual(set(signal), expected_fields)
                    self.assertEqual(signal["originalRank"], row["originalRank"])
                    self.assertEqual(signal["signalId"], row["signalId"])

    def test_last_source_day_signal_is_retained_for_account_censoring(self):
        metadata, daily = synthetic_fixture(("passed",), days=1)
        metadata["calendar"] = metadata["calendar"][:51]
        result = build(metadata, daily)
        self.assertEqual(result["schedules"]["persistent"][0]["signalDate"], metadata["calendar"][-1])
        self.assertEqual(result["counts"]["signals"]["knownAll"], 1)

    def test_unknown_missing_f_state_is_preserved(self):
        metadata, daily = synthetic_fixture(("unknown",), days=1)
        candidate = daily[0]["leaderCandidates"][0]
        candidate.update(formationEvidence=None, formationReason="formationStateUnavailable")
        result = build(metadata, daily)
        self.assertIsNone(result["opportunities"][0]["parentCandidate"]["formationEvidence"])
        self.assertEqual(result["schedules"]["knownAll"], [])

    def test_exact_f_clock_rejects_calendar_neighbor_substitution(self):
        metadata, daily = synthetic_fixture(days=1)
        daily[0]["peerFormationDate"] = metadata["calendar"][39]
        with self.assertRaisesRegex(ValueError, "minus10"):
            build(metadata, daily)

    def test_parent_day_gaps_duplicates_and_unsorted_input_rejected(self):
        metadata, daily = synthetic_fixture()
        for broken in (daily[:1] + daily[2:], [daily[0], daily[0], daily[2]], list(reversed(daily))):
            with self.assertRaisesRegex(ValueError, "daily rows"):
                build(metadata, broken)

    def test_cohort_identities_are_required_not_candidate_union(self):
        metadata, daily = synthetic_fixture(days=1)
        for symbols in (None, metadata["symbols"][:-1], list(reversed(metadata["symbols"])), metadata["symbols"] + [metadata["symbols"][-1]]):
            broken = copy.deepcopy(metadata); broken["symbols"] = symbols
            with self.assertRaises(ValueError): build(broken, daily)

    def test_outside_cohort_duplicate_and_reordered_candidates_rejected(self):
        metadata, daily = synthetic_fixture(days=1)
        for variant in ("outside", "duplicate", "reversed"):
            broken = copy.deepcopy(daily)
            candidates = broken[0]["leaderCandidates"]
            if variant == "outside": candidates[0]["symbol"] = "foreign"
            elif variant == "duplicate": candidates[1]["symbol"] = candidates[0]["symbol"]
            else: broken[0]["leaderCandidates"] = list(reversed(candidates))
            with self.assertRaisesRegex(ValueError, "source symbols"):
                build(metadata, broken)

    def test_nonfinite_and_inconsistent_current_evidence_rejected(self):
        metadata, daily = synthetic_fixture(days=1)
        for value in (float("nan"), True, .1):
            broken = copy.deepcopy(daily)
            broken[0]["leaderCandidates"][0]["currentEvidence"]["values"]["meanStrengthPercentile20"] = value
            with self.assertRaises(ValueError): build(metadata, broken)
        broken = copy.deepcopy(daily)
        broken[0]["leaderCandidates"][0]["currentEvidence"]["breakout"] = False
        with self.assertRaisesRegex(ValueError, "strong-plus-breakout"): build(metadata, broken)

    def test_parent_f_provenance_and_counts_not_silently_repaired(self):
        metadata, daily = synthetic_fixture(days=1)
        for variant in ("status", "rank", "count"):
            broken = copy.deepcopy(daily)
            if variant == "status": broken[0]["leaderCandidates"][0]["formationStatus"] = "failed"
            elif variant == "rank": broken[0]["leaderCandidates"][2]["originalLeaderRank"] = 99
            else: broken[0]["counts"]["currentCandidateCount"] += 1
            with self.assertRaises(ValueError): build(metadata, broken)

    def test_input_and_nested_provenance_are_immutable_and_detached(self):
        metadata, daily = synthetic_fixture(days=1)
        before = copy.deepcopy((metadata, daily))
        result = build(metadata, daily)
        self.assertEqual((metadata, daily), before)
        result["opportunities"][0]["parentCandidate"]["currentEvidence"]["extraOriginalEvidence"]["untouched"].append("changed")
        self.assertEqual((metadata, daily), before)
        self.assertEqual(result["dailyRows"][0]["currentCandidates"][0]["parentCandidate"]["currentEvidence"]["extraOriginalEvidence"]["untouched"], ["parent"])

    def test_future_numeric_changes_and_future_append_leave_past_identical(self):
        metadata, daily = synthetic_fixture()
        end = metadata["targetDates"][0]
        result = build(metadata, daily, end=end)
        changed = copy.deepcopy(daily)
        for row in changed[1:]: row["leaderCandidates"] = "future not inspected"
        self.assertEqual(result, build(metadata, changed, end=end))
        prefix = copy.deepcopy(metadata)
        prefix["calendar"] = [d for d in prefix["calendar"] if d <= end]
        prefix["targetDates"] = [end]
        self.assertEqual(result, build(prefix, daily[:1]))

    def test_progress_reports_complete_schedule_without_account_input(self):
        metadata, daily = synthetic_fixture()
        messages = []
        result = build(metadata, daily, progress=messages.append)
        self.assertEqual(messages[-1]["opportunityCount"], 15)
        self.assertEqual(messages[-1]["processedTargetDays"], 3)
        self.assertFalse(result["metadata"]["certified"])
        self.assertFalse(result["metadata"]["targetAchieved"])
        self.assertNotIn("account", result)


if __name__ == "__main__":
    unittest.main()
