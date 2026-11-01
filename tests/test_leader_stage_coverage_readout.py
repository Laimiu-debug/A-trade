from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stage_readout_test_module", ROOT / "scripts/summarize-leader-stage-coverage.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def fixture():
    targets = ["2024-09-20", "2024-09-23", "2024-09-24", "2024-09-25", "2024-09-26", "2024-09-27", "2024-09-30", "2024-10-08"]
    warmup = [(date(2023, 11, 1) + timedelta(days=i)).isoformat() for i in range(40)]
    calendar = warmup + targets
    md = {"calendar": calendar, "targetDates": targets, "counts": {"sampleCount": 30}}
    daily, env, events, formations, forecasts = [], [], [], [], []
    configurations = {targets[1]: ("formed", "formed"), targets[2]: ("locked", "incomplete"),
                      targets[3]: ("notFormed", "unknown"), targets[-1]: ("formed", "formed")}
    for i, day in enumerate(targets):
        pos = calendar.index(day)
        peer_day = calendar[pos - 10]
        active = day in configurations
        eventid = "event:" + day
        candidates = [{"symbol": "anchor", "formationStatus": "passed" if active else "failed"}]
        ranking = [{"symbol": "anchor", "originalLeaderRank": 1, "selectedTop5": True}] if active else []
        g = configurations[day][1] if active else None
        daily.append({"formationDate": day, "peerFormationDate": peer_day,
            "rankPopulations": {"20": {"count": 29, "sha256": "rank20"}, "60": {"count": 28, "sha256": "rank60"}},
            "rankReasonCounts": {"20": {"unknown": 1}, "60": {"unknown": 2}},
            "activationCounts": {"candidate": 1, "notTriggered": 28, "unknown": 1},
            "formationCounts": {"passed": int(active), "failed": int(not active), "unknown": 0},
            "leaderCandidates": candidates, "leaderRanking": ranking, "leaderEventIds": [eventid] if active else [],
            "counts": {"currentCandidateCount": 1, "temporalCandidateCount": int(active), "leaderEventCount": int(active),
                       "leaderGroupKinds": {k: int(g == k) for k in M.GROUP_KINDS}}})
        env.append({"formationDate": day, "asOfDate": calendar[pos - 1], "fixedPopulation": 30,
            "price20": {"positiveReturn20Breadth": 0 if i == 0 else .6, "medianAbsoluteReturn20": None if i == 0 else -.02,
                        "evaluableCount": 29, "coverage": 29 / 30},
            "amount": {"medianRelativeToOwnPrior20": None if i == 0 else 1.2, "evaluableCount": 27, "coverage": .9},
            "matched5": {"matchedBreadthChange5": None if i < 5 else .1, "pairedCount": None if i < 5 else 28,
                         "pairedCoverageFixed": None if i < 5 else 28 / 30},
            "retention": {"cohortSize": 3,
                          "endpoint": {"retentionLower": 1 / 3, "retentionUpper": 2 / 3},
                          "continuous": {"retentionLower": 0, "retentionUpper": 1 / 3, "unknownCount": 1}}})
        if not active:
            continue
        status, groupkind = configurations[day]
        members = [{"symbol": f"m{j:02d}"} for j in range(20)] if groupkind == "formed" else []
        events.append({"eventId": eventid, "formationDate": day, "peerFormationDate": peer_day,
                       "anchorSymbol": "anchor", "groupSummary": {"kind": groupkind}})
        formations.append({"parentEventId": eventid, "formationDate": day, "peerFormationDate": peer_day,
                           "anchorSymbol": "anchor", "groupKind": groupkind, "status": status, "members": members})
        if status == "formed":
            for member in members:
                for offset in range(5):
                    at = i + offset
                    d = targets[at] if at < len(targets) else None
                    t = targets[at + 1] if at + 1 < len(targets) else None
                    score = "forecastBeyondEnd" if d is None else "targetBeyondEnd" if t is None else "scored"
                    forecasts.append({"rowId": f"{eventid}:{member['symbol']}:{offset}", "parentEventId": eventid,
                        "formationDate": day, "peerFormationDate": peer_day, "anchorSymbol": "anchor",
                        "memberSymbol": member["symbol"], "offset": offset, "forecastDate": d, "targetDate": t,
                        "scoreStatus": score})
    atlas = {"phases": [
        {"id": "before", "title": "之前", "startDate": "2024-01-01", "endDate": "2024-09-23"},
        {"id": "after", "title": "启动", "startDate": "2024-09-24", "endDate": "2024-09-27"},
        {"id": "empty", "title": "无源日", "startDate": "2024-09-28", "endDate": "2024-09-29"},
        {"id": "last", "title": "之后", "startDate": "2024-09-30", "endDate": "2024-12-31"}]}
    return md, daily, events, formations, forecasts, env, atlas


def prepared(values=None):
    md, ds, es, fs, ps, env, _ = values or fixture()
    return M.prepare_group("2024-01-01", "2024-12-31", md, ds, es, fs, ps, env)


class LeaderStageCoverageReadoutTests(unittest.TestCase):
    def test_complete_calendar_keeps_zero_event_days(self):
        days = prepared()
        self.assertEqual(len(days), 8)
        self.assertEqual(sum(d["leaderAdmission"]["zeroLeaderEventDays"] for d in days), 4)
        self.assertEqual(days[0]["predictionByForecast"]["plannedSlots"], 0)

    def test_unknown_activation_is_not_absence(self):
        d = prepared()[0]["leaderAdmission"]
        self.assertEqual(d["activationCounts"], {"candidate": 1, "notTriggered": 28, "unknown": 1})
        self.assertEqual(d["formationCounts"], {"passed": 0, "failed": 1, "unknown": 0})
        self.assertEqual(d["symbolDayCount"], 30)

    def test_missing_leader_day_rejected(self):
        v = list(fixture()); v[1].pop(0)
        with self.assertRaisesRegex(AssertionError, "absent is not zero"):
            prepared(v)

    def test_missing_environment_day_rejected(self):
        v = list(fixture()); v[5].pop(0)
        with self.assertRaisesRegex(AssertionError, "absent is not zero"):
            prepared(v)

    def test_duplicate_days_rejected(self):
        v = list(fixture()); v[1].append(deepcopy(v[1][0]))
        with self.assertRaisesRegex(AssertionError, "Duplicate leader day"):
            prepared(v)

    def test_formation_clock_and_forecast_clock_are_different(self):
        v = fixture(); days = prepared(v)
        before = M.aggregate_slice(days, v[4], "phase", "2024-01-01", "2024-09-23")
        after = M.aggregate_slice(days, v[4], "phase", "2024-09-24", "2024-09-27")
        self.assertEqual(before["predictionByFormation"]["plannedSlots"], 100)
        self.assertEqual(before["predictionByForecast"]["plannedSlots"], 20)
        self.assertEqual(before["crossBoundary"]["formationSlotsForecastAfterSlice"], 80)
        self.assertEqual(after["predictionByFormation"]["plannedSlots"], 0)
        self.assertEqual(after["predictionByForecast"]["plannedSlots"], 80)
        self.assertEqual(after["crossBoundary"]["forecastSlotsFromEarlierFormation"], 80)

    def test_null_forecasts_only_in_overall_forecast_denominator(self):
        v = fixture(); days = prepared(v)
        all_rows = M.aggregate_slice(days, v[4], "overall", "2024-01-01", "2024-12-31")
        part = M.aggregate_slice(days, v[4], "phase", "2024-09-30", "2024-12-31")
        self.assertEqual(all_rows["predictionByForecast"]["unassignedForecastSlots"], 80)
        self.assertEqual(part["predictionByForecast"]["unassignedForecastSlots"], 0)
        self.assertEqual(part["predictionByFormation"]["unassignedForecastSlots"], 80)
        self.assertEqual(part["crossBoundary"]["formationSlotsNullForecast"], 80)

    def test_locked_incomplete_and_unknown_groups_remain_separate(self):
        v = fixture(); r = M.aggregate_slice(prepared(v), v[4], "overall", "2024-01-01", "2024-12-31")
        matrix = r["leaderAdmission"]["formation43StatusByGroupKind"]
        self.assertEqual(matrix["locked"]["incomplete"], 1)
        self.assertEqual(matrix["notFormed"]["unknown"], 1)
        self.assertEqual(sum(sum(x.values()) for x in matrix.values()), 4)

    def test_prediction_design_does_not_apply_member_entry_filters(self):
        v = list(fixture())
        for f in v[3]:
            f["initialAmountEligibleCount"] = 0
            f["memberBreakouts"] = 0
        d = prepared(v)
        self.assertEqual(sum(r["predictionByFormation"]["plannedSlots"] for r in d), 200)

    def test_duplicate_forecast_cannot_replace_missing_design_slot(self):
        v = list(fixture()); v[4][-1] = deepcopy(v[4][-2])
        with self.assertRaisesRegex(AssertionError, "Unique forecast row"):
            prepared(v)

    def test_forecast_missing_offset_rejected(self):
        v = list(fixture()); v[4].pop()
        with self.assertRaisesRegex(AssertionError, "20 by 5"):
            prepared(v)

    def test_forecast_dates_must_follow_source_calendar(self):
        v = list(fixture()); v[4][0]["forecastDate"] = "2024-09-24"
        with self.assertRaisesRegex(AssertionError, "source-slot"):
            prepared(v)

    def test_unknown_scores_retained_without_any_loss_calculation(self):
        v = list(fixture()); v[4][0]["scoreStatus"] = "targetUnknown"
        result = M.aggregate_slice(prepared(v), v[4], "overall", "2024-01-01", "2024-12-31")
        self.assertEqual(result["predictionByForecast"]["scoreStatusCounts"]["targetUnknown"], 1)
        self.assertEqual(result["predictionByForecast"]["scoredSlots"], 99)

    def test_environment_has_previous_close_clock(self):
        d = prepared()[2]
        self.assertEqual(d["formationDate"], "2024-09-24")
        self.assertEqual(d["environmentAsOfDate"], "2024-09-23")
        v = list(fixture()); v[5][2]["asOfDate"] = "2024-09-24"
        with self.assertRaisesRegex(AssertionError, "previous close"):
            prepared(v)

    def test_environment_zero_is_known_and_unknown_is_not_zero(self):
        v = fixture(); r = M.aggregate_slice(prepared(v), v[4], "phase", "2024-09-20", "2024-09-20")
        self.assertEqual(r["environment"]["priorClosePositiveReturn20Breadth"]["knownDays"], 1)
        self.assertEqual(r["environment"]["priorClosePositiveReturn20Breadth"]["mean"], 0)
        self.assertIsNone(r["environment"]["priorCloseRelativeAmount20"]["mean"])
        self.assertEqual(r["environment"]["priorCloseRelativeAmount20"]["unknownDays"], 1)

    def test_empty_slice_retains_full_zero_counters_and_null_environment(self):
        v = fixture(); r = M.aggregate_slice(prepared(v), v[4], "phase", "2024-09-28", "2024-09-29")
        self.assertEqual(r["targetDays"], 0)
        self.assertEqual(r["predictionByForecast"]["plannedSlots"], 0)
        self.assertEqual(set(r["predictionByForecast"]["scoreStatusCounts"]), set(M.SCORE))
        self.assertIsNone(r["observedStart"])
        self.assertTrue(all(x["mean"] is None for x in r["environment"].values()))

    def test_unique_member_day_is_not_number_of_event_views(self):
        rows = [dict(parentEventId="a", memberSymbol="m", forecastDate="2024-01-01", scoreStatus="scored"),
                dict(parentEventId="b", memberSymbol="m", forecastDate="2024-01-01", scoreStatus="scored")]
        r = M.forecast_counts(rows)
        self.assertEqual((r["plannedSlots"], r["eventCount"], r["uniqueMemberDates"]), (2, 2, 1))

    def test_independent_source_aggregation_matches_all_slices(self):
        v = fixture(); md, raw, _, fs, ps, env, atlas = v
        days = prepared(v)
        for _, kind, sid, _, a, b in M.slices("2024-2025", "2024-01-01", "2024-12-31", atlas):
            M.same(M.aggregate_slice(days, ps, kind, a, b), M.reference_slice(md, raw, fs, ps, env, kind, a, b), sid)

    def test_cross_year_is_forecast_dated_not_formation_dated(self):
        row = {"formationDate": "2023-12-29", "forecastDate": "2024-01-02", "parentEventId": "e"}
        self.assertEqual(M.select_forecasts([row], "forecastDate", "2024-01-01", "2024-12-31"), [row])
        self.assertEqual(M.select_forecasts([row], "formationDate", "2024-01-01", "2024-12-31"), [])
        self.assertEqual(M.boundary_counts([row], "2024-01-01", "2024-12-31")["forecastSlotsFromEarlierFormation"], 1)

    def test_no_mutation(self):
        v = fixture(); before = deepcopy(v)
        M.summarize_group({"period": "2024-2025", "board": "main"}, "2024-01-01", "2024-12-31", *v[:-1], v[-1])
        self.assertEqual(v, before)

    def test_focal_first_failure_uses_parent_candidate_denominator(self):
        v = fixture(); days = prepared(v); raw = deepcopy(v[1][0])
        raw["firstFailureCounts"] = {"current": {"unknown": 1, "breakout": 28, "passed": 1},
            "formationAll": {"failed": 30}, "formationEvaluatedByParent": {"failed": 1}}
        raw["endToEndCounts"] = {"currentUnknown": 1, "currentNoBreakout": 28, "formationPredicateFailed": 1}
        raw["rs20Top20"] = [{"symbol": "unknown60", "relativeReturn20": .1, "percentile20": .99,
                            "currentEvidence": {"status": "unknown"}, "formationEvidence": None}]
        out = M.focus_rows({"period": "2024-2025", "board": "main"}, {"targetDates": [raw["formationDate"]]}, [raw], days)
        self.assertEqual(out[0]["top20"], raw["rs20Top20"])
        self.assertEqual(sum(out[0]["firstFailureCounts"]["formationEvaluatedByParent"].values()), 1)
        self.assertEqual(sum(out[0]["firstFailureCounts"]["formationAll"].values()), 30)

    def test_tuple_key_diagnostic_regression(self):
        M.same({("period", "board"): {"n": 1}}, {("period", "board"): {"n": 1}})
        with self.assertRaises(AssertionError):
            M.same({("period", "board"): {"n": 2}}, {("period", "board"): {"n": 1}})


if __name__ == "__main__":
    unittest.main()
