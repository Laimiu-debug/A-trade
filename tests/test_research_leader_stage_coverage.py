import copy
from datetime import date, timedelta
import math
import unittest
from unittest.mock import patch

from service import research_leader_lag as lag
from service import research_leader_stage_coverage as model


def synthetic_fixture(size=100, stocks=30):
    day, calendar = date(2024, 1, 2), []
    while len(calendar) < size:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)

    def bar(day, close):
        return {"date": day, "open": close, "high": close * 1.00001,
                "low": close * .99999, "close": close, "volume": 1000., "amount": 1.}

    securities = []
    for number in range(stocks):
        close, bars = 10., []
        for at, day in enumerate(calendar):
            close *= math.exp(.001 + number * .0001 + .0002 * math.sin(at * .7))
            bars.append(bar(day, close))
        securities.append({"symbol": f"sz{number:06}", "bars": bars})
    return {"priceBasisId": "raw-unadjusted", "calendar": calendar, "securities": securities,
            "benchmark": {"symbol": "sh000300", "bars": [bar(day, 100.) for day in calendar]}}


def parent(data, first=70, last=90):
    return lag.build_leader_lag_followers(data, data["calendar"][first], data["calendar"][last])


def build(data=None, first=85, last=87, saved=None):
    data = synthetic_fixture() if data is None else data
    if saved is None:
        saved = parent(data, first=max(0, first - 10), last=last)["dailyRows"]
    return model.build_leader_stage_coverage(data, saved, start=data["calendar"][first], end=data["calendar"][last])


def evidence(predicates=None, status="passed", breakout=True):
    return {"status": status, "predicates": {name: True for name in model.FIELDS} | (predicates or {}),
            "breakout": breakout}


class LeaderStageCoverageTests(unittest.TestCase):
    def test_original_upstream_and_every_rank_digest_reconcile(self):
        data = synthetic_fixture()
        old = parent(data)
        actual = build(data, saved=old["dailyRows"])
        ranks = {row["date"]: row for row in old["rankRows"]}
        for row in actual["rankRows"]:
            self.assertEqual(row, ranks[row["date"]])
        self.assertEqual(actual["counts"]["symbolDayCount"], 3 * 30)
        self.assertEqual(actual["counts"]["parentReconciledDayCount"], 3)
        for row in actual["dailyRows"]:
            self.assertEqual(sum(row["activationCounts"].values()), 30)
            self.assertEqual(sum(row["formationCounts"].values()), row["counts"]["currentCandidateCount"])
            self.assertEqual(sum(row["endToEndCounts"].values()), 30)

    def test_warmup_includes_f_minus19_and_earlier_raw_history(self):
        data = synthetic_fixture()
        actual = build(data, last=85)
        self.assertEqual(actual["metadata"]["rankClockStart"], data["calendar"][56])
        self.assertEqual(actual["counts"]["rankDayCount"], 30)
        strong = actual["dailyRows"][0]["leaderCandidates"][-1]
        self.assertEqual(strong["peerFormationDate"], data["calendar"][75])
        self.assertEqual(strong["formationEvidence"]["rankDates20"], data["calendar"][56:76])
        self.assertEqual(strong["formationEvidence"]["knownRankCount20"], 20)

    def test_rs20_display_does_not_require_rs60_complete(self):
        data = synthetic_fixture()
        security = data["securities"][-1]
        security["bars"].pop(40)  # At85 outside21, inside61.
        actual = build(data, last=85)
        row = actual["dailyRows"][0]
        self.assertEqual(row["rankPopulations"]["20"]["count"], 30)
        self.assertEqual(row["rankPopulations"]["60"]["count"], 29)
        self.assertEqual(row["rs20Top20"][0]["symbol"], security["symbol"])
        self.assertEqual(row["rs20Top20"][0]["currentEvidence"]["status"], "unknown")
        self.assertIsNotNone(row["rs20Top20"][0]["relativeReturn20"])

    def test_amount_and_current_name_do_not_prefilter_ranks(self):
        data = synthetic_fixture()
        for security in data["securities"]:
            security["name"] = "ST-current-name"
            for bar in security["bars"]:
                bar["amount"] = None
        actual = build(data, last=85)
        self.assertEqual(actual["dailyRows"][0]["rankPopulations"]["60"]["count"], 30)
        self.assertGreater(actual["counts"]["leaderEventCount"], 0)

    def test_rs20_ties_use_symbol_and_do_not_select_future_leaders(self):
        data = synthetic_fixture(stocks=25)
        common = copy.deepcopy(data["securities"][0]["bars"])
        for security in data["securities"]:
            security["bars"] = copy.deepcopy(common)
        actual = build(data, last=85)
        display = actual["dailyRows"][0]["rs20Top20"]
        self.assertEqual([row["symbol"] for row in display], sorted(s["symbol"] for s in data["securities"])[:20])
        self.assertTrue(all(row["percentile20"] == .5 for row in display))
        self.assertEqual(actual["counts"]["leaderEventCount"], 0)

    def test_unknown_has_priority_over_other_false_predicates(self):
        state = evidence({model.FIELDS[0]: False, model.FIELDS[2]: None}, status="unknown", breakout=False)
        first, mask = model.condition_failure(state, include_breakout=True)
        self.assertEqual(first, "unknown")
        self.assertEqual(mask["false"], [model.FIELDS[0], "breakout"])
        self.assertEqual(mask["unknown"], [model.FIELDS[2]])
        self.assertEqual(len(mask["true"]), 3)

    def test_first_failure_order_retains_all_overlapping_failures(self):
        state = evidence({model.FIELDS[1]: False, model.FIELDS[3]: False}, status="failed", breakout=False)
        first, mask = model.condition_failure(state, include_breakout=True)
        self.assertEqual(first, model.FIELDS[1])
        self.assertEqual(mask["false"], [model.FIELDS[1], model.FIELDS[3], "breakout"])
        unknown_raw = evidence(status="unknown")
        first, mask = model.condition_failure(unknown_raw, include_breakout=True)
        self.assertEqual(first, "unknown")
        self.assertEqual(len(mask["true"]), 6)

    def test_a_breakout_is_strict_and_reported_after_five_passes(self):
        data = synthetic_fixture()
        security = data["securities"][-1]
        security["bars"][84]["high"] = security["bars"][85]["close"]
        actual = build(data, last=85)
        row = next(row for row in actual["symbolRows"] if row["symbol"] == security["symbol"])
        self.assertEqual(row["currentEvidence"]["status"], "passed")
        self.assertEqual(row["currentFirstFailure"], "breakout")
        self.assertEqual(row["endToEndDisposition"], "currentNoBreakout")
        self.assertFalse(row["formationEvaluatedByParent"])

    def test_f_breakout_is_not_an_admission_condition(self):
        data = synthetic_fixture()
        security = data["securities"][-1]
        security["bars"][74]["high"] = security["bars"][75]["close"]
        actual = build(data, last=85)
        row = next(row for row in actual["symbolRows"] if row["symbol"] == security["symbol"])
        self.assertFalse(row["formationEvidence"]["breakout"])
        self.assertEqual(row["formationFirstFailure"], "passed")
        self.assertTrue(row["selectedTop5"])
        self.assertNotIn("breakout", sum(row["failureMask"]["formation"].values(), []))

    def test_f_filter_applies_before_leader_top5_ranking(self):
        data = synthetic_fixture(stocks=80)
        original = lag._leadership
        high = data["securities"][-1]["bars"][75]["close"]

        def fail_f(stock, benchmark, at, calendar, history, rank60):
            state = original(stock, benchmark, at, calendar, history, rank60)
            if at == 75 and stock.bars[75]["close"] == high:
                state["status"] = "failed"
                state["values"][model.FIELDS[-1]] = .34
                state["predicates"][model.FIELDS[-1]] = False
            return state

        with patch.object(lag, "_leadership", side_effect=fail_f):
            actual = build(data, last=85)
        row = next(row for row in actual["symbolRows"] if row["symbol"] == data["securities"][-1]["symbol"])
        self.assertIsNone(row["originalLeaderRank"])
        self.assertEqual(row["endToEndDisposition"], "formationPredicateFailed")
        self.assertEqual(actual["dailyRows"][0]["leaderRanking"][0]["symbol"], data["securities"][-2]["symbol"])

    def test_top5_exclusion_is_explicit_without_backfill(self):
        data = synthetic_fixture(stocks=80)
        actual = build(data, last=85)
        daily = actual["dailyRows"][0]
        self.assertGreater(daily["counts"]["temporalCandidateCount"], 5)
        self.assertEqual(daily["endToEndCounts"]["selectedLeader"], 5)
        self.assertEqual(daily["endToEndCounts"]["leaderRankExcluded"], daily["counts"]["temporalCandidateCount"] - 5)
        small = synthetic_fixture(stocks=8)
        old = parent(small, first=85, last=85)
        self.assertEqual(old["leaderEvents"][0]["groupSummary"]["kind"], "incomplete")
        result = build(small, last=85, saved=old["dailyRows"])
        self.assertEqual(result["dailyRows"][0]["leaderEventIds"], old["dailyRows"][0]["leaderEventIds"])

    def test_all_symbol_f_context_is_not_parent_evaluated_population(self):
        actual = build(last=85)
        daily = actual["dailyRows"][0]
        self.assertEqual(sum(daily["firstFailureCounts"]["formationAll"].values()), 30)
        self.assertEqual(sum(daily["firstFailureCounts"]["formationEvaluatedByParent"].values()),
                         daily["counts"]["currentCandidateCount"])
        other = [row for row in actual["symbolRows"] if not row["formationEvaluatedByParent"]]
        self.assertTrue(other)
        self.assertTrue(all(row["formationEvidence"] is not None and row["formationStatus"] is None for row in other))

    def test_zero_event_day_keeps_all_symbols_and_zero_categories(self):
        data = synthetic_fixture()
        for security in data["securities"]:
            for bar in security["bars"]:
                bar.update(open=10., high=10., low=10., close=10.)
        actual = build(data, last=85)
        self.assertEqual(actual["counts"]["leaderEventCount"], 0)
        self.assertEqual(actual["counts"]["activationCounts"]["unknown"], 30)
        self.assertEqual(actual["counts"]["endToEndCounts"]["selectedLeader"], 0)
        self.assertEqual(len(actual["symbolRows"]), 30)

    def test_short_calendar_preserves_unavailable_f_and_unknown(self):
        data = synthetic_fixture(size=15, stocks=3)
        actual = build(data, first=0, last=2)
        self.assertEqual(actual["metadata"]["rankClockStart"], data["calendar"][0])
        self.assertTrue(all(row["peerFormationDate"] is None and row["formationEvidence"] is None for row in actual["symbolRows"]))
        self.assertTrue(all(row["formationFirstFailure"] == "unknown" for row in actual["symbolRows"]))

    def test_missing_parent_focus_day_is_rejected(self):
        data = synthetic_fixture()
        saved = parent(data)["dailyRows"]
        saved = [row for row in saved if row["formationDate"] != data["calendar"][86]]
        with self.assertRaisesRegex(ValueError, "Missing focus"):
            build(data, saved=saved)

    def test_duplicate_and_misaligned_parent_dates_are_rejected(self):
        data = synthetic_fixture()
        saved = parent(data, first=85, last=87)["dailyRows"]
        with self.assertRaisesRegex(ValueError, "unique"):
            build(data, saved=[saved[0], saved[0], *saved[1:]])
        saved[0]["formationDate"] = "1900-01-01"
        with self.assertRaisesRegex(ValueError, "aligned"):
            build(data, saved=saved)

    def test_parent_counts_and_full_candidate_evidence_cannot_be_repaired(self):
        data = synthetic_fixture()
        saved = parent(data, first=85, last=85)["dailyRows"]
        mutated = copy.deepcopy(saved)
        mutated[0]["counts"]["leaderEventCount"] += 1
        with self.assertRaisesRegex(ValueError, "counts.leaderEventCount"):
            build(data, last=85, saved=mutated)
        mutated = copy.deepcopy(saved)
        mutated[0]["leaderCandidates"][0]["currentEvidence"]["rankHistory20"][0] += .01
        with self.assertRaisesRegex(ValueError, "leaderCandidates"):
            build(data, last=85, saved=mutated)

    def test_source_structure_rejects_calendar_and_bar_order_changes(self):
        data = synthetic_fixture()
        saved = parent(data, first=85, last=85)["dailyRows"]
        duplicate = copy.deepcopy(data)
        duplicate["calendar"].insert(3, duplicate["calendar"][3])
        with self.assertRaisesRegex(ValueError, "unique"):
            build(duplicate, last=85, saved=saved)
        data["securities"][0]["bars"][0:2] = data["securities"][0]["bars"][1::-1]
        with self.assertRaisesRegex(ValueError, "unique and increasing"):
            build(data, last=85, saved=saved)

    def test_known_numeric_zero_halt_stays_unknown_and_is_not_compressed(self):
        data = synthetic_fixture()
        security = data["securities"][-1]
        security["tradeStatusByDate"] = {data["calendar"][80]: 0.0}
        actual = build(data, last=85)
        row = next(row for row in actual["symbolRows"] if row["symbol"] == security["symbol"])
        self.assertIn("stock:knownSuspension", row["currentEvidence"]["reasons"])
        self.assertEqual(row["currentFirstFailure"], "unknown")
        self.assertEqual(row["currentEvidence"]["rankDates20"], data["calendar"][66:86])

    def test_inputs_immutable_and_evidence_detached(self):
        data = synthetic_fixture()
        saved = parent(data, first=85, last=85)["dailyRows"]
        before = copy.deepcopy((data, saved))
        actual = build(data, last=85, saved=saved)
        self.assertEqual((data, saved), before)
        actual["symbolRows"][-1]["currentEvidence"]["values"][model.FIELDS[0]] = -1
        self.assertEqual((data, saved), before)
        self.assertNotEqual(actual["dailyRows"][0]["leaderCandidates"][-1]["currentEvidence"]["values"][model.FIELDS[0]], -1)

    def test_future_numeric_perturbation_and_appending_do_not_change_focus(self):
        data = synthetic_fixture()
        saved = parent(data, first=70, last=90)["dailyRows"]
        expected = build(data, last=85, saved=saved)
        changed = copy.deepcopy(data)
        for security in [*changed["securities"], changed["benchmark"]]:
            for bar in security["bars"][86:]:
                bar.update(close="future not read", high=None, volume=float("nan"))
        self.assertEqual(build(changed, last=85, saved=saved), expected)
        prefix = copy.deepcopy(data)
        prefix["calendar"] = prefix["calendar"][:86]
        for security in [*prefix["securities"], prefix["benchmark"]]:
            security["bars"] = security["bars"][:86]
        saved_prefix = [row for row in saved if row["formationDate"] <= data["calendar"][85]]
        self.assertEqual(build(prefix, last=85, saved=saved_prefix), expected)

    def test_progress_and_metadata_do_not_create_trading_outputs(self):
        data = synthetic_fixture()
        saved = parent(data, first=85, last=87)["dailyRows"]
        progress = []
        actual = model.build_leader_stage_coverage(data, saved, start=data["calendar"][85], end=data["calendar"][87],
                                                  progress=progress.append)
        self.assertEqual(progress[-1]["symbolDayCount"], 90)
        self.assertEqual(actual["metadata"]["newSignals"], 0)
        self.assertEqual(actual["metadata"]["accountExecutions"], 0)
        self.assertNotIn("schedules", actual)
        self.assertTrue(actual["verification"]["passed"])


if __name__ == "__main__":
    unittest.main()
