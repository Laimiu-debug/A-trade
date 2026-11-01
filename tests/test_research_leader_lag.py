import copy
from datetime import date, timedelta
import json
import math
import unittest
from unittest.mock import patch

from service import research_leader_lag as model


def dates(size):
    day, result = date(2022, 1, 3), []
    while len(result) < size:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def bar(day, close):
    return {"date": day, "open": close, "high": close * 1.00001,
            "low": close * .99999, "close": close, "volume": 1000., "amount": 1.}


def source(size=100, stocks=25):
    calendar = dates(size)
    securities = []
    for number in range(stocks):
        close, bars = 10., []
        for at, day in enumerate(calendar):
            close *= math.exp(.001 + number * .0001 + .0002 * math.sin(at * .7))
            bars.append(bar(day, close))
        securities.append({"symbol": f"sz{number:06}", "bars": bars})
    return {"priceBasisId": "raw-unadjusted", "calendar": calendar, "securities": securities,
            "benchmark": {"symbol": "sh000300", "bars": [bar(day, 100.) for day in calendar]}}


def build(data=None, first=85, last=88, **kwargs):
    data = source() if data is None else data
    return model.build_leader_lag_followers(data, data["calendar"][first], data["calendar"][last], **kwargs)


def edge(anchor, member, leader_rank, peer_rank):
    return {"formationId": anchor + ":" + member, "symbol": member, "anchorSymbol": anchor,
            "originalLeaderRank": leader_rank, "frozenMemberRank": peer_rank}


class LeaderLagTests(unittest.TestCase):
    def test_separate_cross_sections_and_midrank_ties(self):
        ranks, evidence = model._rank({"a": 1., "b": 1., "c": 2., "d": None})
        self.assertEqual(ranks, {"a": 1 / 3, "b": 1 / 3, "c": 5 / 6, "d": None})
        self.assertEqual(evidence["count"], 3)
        data = source()
        data["securities"][-1]["bars"].pop(45)  # Outside21, inside61 at A=85.
        result = build(data, last=85)
        populations = result["dailyRows"][0]["rankPopulations"]
        self.assertEqual(populations["20"]["count"], 25)
        self.assertEqual(populations["60"]["count"], 24)

    def test_rank_population_precedes_price_strength_or_liquidity(self):
        data = source()
        for security in data["securities"]:
            for item in security["bars"]:
                item["amount"] = None  # No inherited amount eligibility.
        result = build(data, last=85)
        self.assertEqual(result["dailyRows"][0]["rankPopulations"]["60"]["count"], 25)
        self.assertGreater(result["counts"]["leaderEventCount"], 0)
        self.assertFalse(result["basis"]["legacyEligibilityApplied"])

    def test_f_warmup_and_historical_rank_clock(self):
        data = source()
        result = build(data, first=70, last=70)
        self.assertEqual(result["basis"]["rankClockStart"], data["calendar"][41])
        self.assertGreater(result["counts"]["leaderEventCount"], 0)
        for event in result["leaderEvents"]:
            self.assertEqual(event["peerFormationDate"], data["calendar"][60])
            prior = event["formationEvidence"]
            self.assertEqual(prior["rankDates20"], data["calendar"][41:61])
            self.assertEqual(prior["knownRankCount20"], 20)
            self.assertLess(event["peerFormationDate"], result["targetDates"][0])

    def test_historical_ranks_are_not_recomputed_from_current_population(self):
        data = source()
        data["securities"][0]["bars"].pop(80)
        result = build(data, first=85, last=85)
        event = result["leaderEvents"][0]
        ranks_by_day = {row["date"]: row for row in result["rankRows"]}
        self.assertEqual(ranks_by_day[data["calendar"][79]]["populations"]["20"]["count"], 25)
        self.assertEqual(ranks_by_day[data["calendar"][80]]["populations"]["20"]["count"], 24)
        self.assertEqual(event["formationEvidence"]["rankHistory20"][-1], .98)
        self.assertNotEqual(event["currentEvidence"]["rankHistory20"][-1], .98)

    def test_f_filter_precedes_leader_ranking(self):
        actual = model._leadership
        rejected = "sz000024"
        data = source()
        rejected_stock = None

        def controlled(stock, benchmark, at, calendar, history, rank60):
            nonlocal rejected_stock
            state = actual(stock, benchmark, at, calendar, history, rank60)
            # Track highest-price security by its final observed close. Only
            # alter its pre-event evidence, never ranking fields or A evidence.
            if stock.bars[75]["close"] == data["securities"][-1]["bars"][75]["close"]:
                rejected_stock = stock
            if stock is rejected_stock and at == 75:
                state["status"] = "failed"
                state["values"]["signedPathEfficiency20"] = .34
                state["predicates"]["signedPathEfficiency20"] = False
            return state

        with patch.object(model, "_leadership", side_effect=controlled):
            result = build(data, last=85)
        candidates = {row["symbol"]: row for row in result["dailyRows"][0]["leaderCandidates"]}
        self.assertEqual(candidates[rejected]["formationStatus"], "failed")
        self.assertIsNone(candidates[rejected]["originalLeaderRank"])
        self.assertEqual(result["leaderEvents"][0]["originalLeaderRank"], 1)
        self.assertNotEqual(result["leaderEvents"][0]["anchorSymbol"], rejected)

    def test_inclusive_five_boundaries_and_strict_breakout(self):
        data = source()
        calendar = data["calendar"]
        security = data["securities"][0]
        stock = model._Series(security, calendar, calendar[-1])
        benchmark = model._Series(data["benchmark"], calendar, calendar[-1])
        state = model._leadership(stock, benchmark, 85, calendar, [.9] * 20, .9)
        self.assertEqual(state["status"], "passed")
        self.assertTrue(state["breakout"])
        lower = model._leadership(stock, benchmark, 85, calendar, [.9] * 20, math.nextafter(.9, 0))
        self.assertEqual(lower["status"], "failed")
        security["bars"][84]["high"] = security["bars"][85]["close"]
        stock = model._Series(security, calendar, calendar[-1])
        equal = model._leadership(stock, benchmark, 85, calendar, [.9] * 20, .9)
        self.assertEqual(equal["status"], "passed")
        self.assertFalse(equal["breakout"])

    def test_drawdown_formula_and_zero_path_are_explicit(self):
        data = source()
        calendar = data["calendar"]
        security = data["securities"][0]
        closes = [100., 90.] + [90. + 70. * index / 19 for index in range(1, 20)]
        for at, close in enumerate(closes, 65):
            security["bars"][at] = bar(calendar[at], close)
        stock = model._Series(security, calendar, calendar[-1])
        benchmark = model._Series(data["benchmark"], calendar, calendar[-1])
        state = model._leadership(stock, benchmark, 85, calendar, [.9] * 20, .9)
        self.assertEqual(state["values"]["closePathMaxDrawdown20"], .1)
        self.assertTrue(state["predicates"]["closePathMaxDrawdown20"])
        for at in range(65, 86):
            security["bars"][at] = bar(calendar[at], 100.)
        state = model._leadership(model._Series(security, calendar, calendar[-1]), benchmark,
                                  85, calendar, [.9] * 20, .9)
        self.assertEqual(state["status"], "unknown")
        self.assertIn("zeroPathDistance", state["reasons"])

    def test_pearson_exact_constant_before_centering_and_overflow(self):
        self.assertEqual(model._pearson([.1] * 60, list(range(60)))["reason"], "x:exactConstantInput")
        self.assertAlmostEqual(model._pearson([1, 2, 3], [3, 2, 1])["correlation"], -1.)
        result = model._pearson([1e308, -1e308, 1e308], [1., 2., 3.])
        self.assertFalse(result["known"])
        json.dumps(result, allow_nan=False)

    def test_direction_timing_differs_from_synchronous_and_reverse(self):
        x = [math.sin(index * 1.7) + .2 * math.cos(index * .2) for index in range(60)]
        y = [.03] + x[:-1]
        window = dates(61)
        forward = model._relationship(x, y, window)
        self.assertTrue(forward["directionPassed"])
        self.assertFalse(model._relationship(y, x, window)["directionPassed"])
        self.assertFalse(model._relationship(x, x, window)["directionPassed"])
        coefficient = forward["coefficients"]["forward59"]
        self.assertEqual(coefficient["count"], 59)
        self.assertEqual(coefficient["xDates"], window[1:60])
        self.assertEqual(coefficient["yDates"], window[2:61])

    def test_constant_lag_slice_is_common_unknown(self):
        relation = model._relationship([.1] * 59 + [.2], list(range(60)), dates(61))
        self.assertEqual(relation["status"], "unknown")
        self.assertIsNone(relation["directionPassed"])
        self.assertIn("forward59:x:exactConstantInput", relation["reasons"])

    def test_interleaving_ownership_and_top5_precede_masks(self):
        rows = [edge("a", "same", 1, 1), edge("b", "same", 2, 1),
                edge("a", "a2", 1, 2), edge("b", "b2", 2, 2),
                edge("c", "c1", 3, 1), edge("d", "d1", 4, 1),
                edge("e", "e1", 5, 1), edge("a", "a3", 1, 3)]
        ordered, selected = model._select_edges(list(reversed(rows)))
        self.assertEqual([row["symbol"] for row in selected], ["same", "c1", "d1", "e1", "a2"])
        duplicate = next(row for row in ordered if row["formationId"] == "b:same")
        self.assertEqual(duplicate["ownerFormationId"], "a:same")
        self.assertEqual(duplicate["disposition"], "duplicateSuppressed")
        self.assertEqual(duplicate["relationshipStatus"], "notEvaluated")
        self.assertEqual(next(row for row in ordered if row["symbol"] == "b2")["disposition"], "dailyRankExcluded")

    def test_insufficient_group_retains_evidence_without_edges(self):
        groups = []
        result = build(source(stocks=20), last=85, on_group=groups.append)
        self.assertGreater(len(groups), 0)
        self.assertTrue(all(row["group"]["selectedCount"] == 19 for row in groups))
        self.assertEqual(result["counts"]["leaderGroupKinds"]["incomplete"], len(groups))
        self.assertEqual(result["memberEdges"], [])
        self.assertEqual(result["opportunities"], [])

    def test_relation_unknown_consumes_slot_and_no_sixth_backfill(self):
        actual = model._relationship
        calls = []

        def unknown_first(*args):
            relation = actual(*args)
            calls.append(relation)
            if len(calls) == 1:
                relation.update(status="unknown", reasons=["syntheticMissingRelationship"], directionPassed=None)
            return relation

        with patch.object(model, "_relationship", side_effect=unknown_first):
            result = build(last=85)
        self.assertEqual(len(calls), 5)
        self.assertEqual(result["counts"]["selectedTop5"], 5)
        self.assertEqual(result["opportunities"][0]["decisions"]["control"]["status"], "unknown")
        self.assertEqual([row["originalRank"] for row in result["schedules"]["control"]], [2, 3, 4, 5])
        self.assertTrue(all(row["relationshipStatus"] == "notEvaluated"
            for row in result["memberEdges"] if not row["selectedTop5"]))

    def test_all_conservation_identities_and_five_signal_fields(self):
        result = build()
        totals = result["counts"]
        self.assertEqual(totals["rawEdges"], 20 * totals["leaderGroupKinds"]["formed"])
        self.assertEqual(totals["rawEdges"], totals["uniqueOwners"] + totals["duplicateSuppressed"])
        self.assertEqual(totals["uniqueOwners"], totals["selectedTop5"] + totals["dailyRankExcluded"])
        self.assertEqual(totals["selectedTop5"], totals["knownRelations"] + totals["unknownRelations"])
        self.assertEqual(totals["knownRelations"], totals["directionPass"] + totals["directionFail"])
        for mode in model.MODES:
            self.assertEqual(totals["selectedTop5"], totals["signals"][mode] + totals["notScheduled"][mode])
            self.assertEqual(totals["signals"][mode], len(result["schedules"][mode]))
            for signal in result["schedules"][mode]:
                self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
        self.assertTrue(all(max(row["relationship"]["windowDates61"]) == row["peerFormationDate"]
                            < row["formationDate"] for row in result["opportunities"]))

    def test_repeated_events_and_final_day_signal_retained(self):
        result = build(first=85, last=86)
        first = {event["anchorSymbol"] for event in result["leaderEvents"] if event["formationDate"] == result["targetDates"][0]}
        last = {event["anchorSymbol"] for event in result["leaderEvents"] if event["formationDate"] == result["targetDates"][-1]}
        self.assertTrue(first.intersection(last))
        self.assertTrue(any(signal["signalDate"] == result["targetDates"][-1] for signal in result["schedules"]["control"]))
        ids = [event["eventId"] for event in result["leaderEvents"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_future_numbers_and_prefix_append_do_not_change_past(self):
        data = source()
        early = build(data, first=85, last=86)
        changed = copy.deepcopy(data)
        for security in changed["securities"] + [changed["benchmark"]]:
            for row in security["bars"][87:]:
                row.update(close=float("nan"), volume=-1, priceBasisId="future-corrupt")
        self.assertEqual(early, build(changed, first=85, last=86))
        later = build(data, first=85, last=89)
        cutoff = data["calendar"][86]
        for key in ("dailyRows", "leaderEvents", "memberEdges", "opportunities"):
            self.assertEqual(early[key], [row for row in later[key] if row["formationDate"] <= cutoff])
        for mode in model.MODES:
            self.assertEqual(early["schedules"][mode], [row for row in later["schedules"][mode] if row["signalDate"] <= cutoff])

    def test_observation_quality_basis_and_numeric_zero_status(self):
        day = dates(1)[0]
        valid = bar(day, 10.)
        self.assertIsNone(model._bar_reason(valid, day, None))
        for status in (False, 0, 0., "0"):
            self.assertEqual(model._bar_reason(valid, day, status), "knownSuspension")
        self.assertEqual(model._bar_reason({**valid, "knownSuspended": None}, day, None), "invalidKnownSuspensionFlag")
        self.assertEqual(model._bar_reason({**valid, "priceBasisId": "adjusted"}, day, None), "rawPriceBasisUnknown")
        self.assertEqual(model._bar_reason({**valid, "volume": True}, day, None), "nonpositiveOrUnknownRawOHLCV")
        self.assertEqual(model._bar_reason({**valid, "high": 9.}, day, None), "invalidRawOHLCEnvelope")

    def test_missing_bar_is_not_compressed_and_future_status_does_not_cancel(self):
        data = source()
        data["benchmark"]["bars"].pop(40)
        result = build(data, last=85)
        self.assertEqual(result["counts"]["leaderEventCount"], 0)
        self.assertEqual(result["dailyRows"][0]["activationCounts"]["unknown"], 25)
        clean = source()
        expected = build(clean, last=85)
        clean["securities"][-1]["tradeStatusByDate"] = {clean["calendar"][90]: 0.}
        self.assertEqual(expected, build(clean, last=85))

    def test_source_and_callback_evidence_are_detached(self):
        data = source()
        before = copy.deepcopy(data)
        groups = []

        def consumer(record):
            self.assertEqual(record["groupSha256"], model._digest(record["group"]))
            groups.append(record["eventId"])
            record["group"]["members"].clear()
            record["group"]["candidateEvaluations"].clear()

        result = build(data, last=85, on_group=consumer)
        self.assertEqual(data, before)
        self.assertEqual(len(groups), result["counts"]["leaderEventCount"])
        self.assertTrue(all(len(event["groupSummary"]["members"]) == 20 for event in result["leaderEvents"]))
        json.dumps(result, allow_nan=False)

    def test_structural_errors_and_progress_are_bounded(self):
        bad = source()
        bad["calendar"][5] = bad["calendar"][4]
        with self.assertRaises(ValueError):
            build(bad)
        bad = source()
        bad["securities"][0]["bars"].append(copy.deepcopy(bad["securities"][0]["bars"][-1]))
        with self.assertRaises(ValueError):
            build(bad)
        bad = source()
        bad.pop("priceBasisId")
        with self.assertRaises(ValueError):
            build(bad)
        progress = []
        result = build(source(size=110), first=85, last=106, progress=progress.append)
        self.assertEqual([item["processedTargetDays"] for item in progress], [10, 20, 22])
        self.assertEqual(progress[-1]["groupCount"], result["counts"]["leaderEventCount"])


if __name__ == "__main__":
    unittest.main()
