from copy import deepcopy
import datetime as dt
import json
import unittest

from service.signal_sequence import SignalSequence, rank_signals


def fixture(length=100):
    day, calendar = dt.date(2022, 1, 3), []
    while len(calendar) < length:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += dt.timedelta(days=1)
    rows = [{"date": day, "open": 10., "high": 10., "low": 10., "close": 10.,
             "volume": 100., "priceBasisId": "verified-raw"} for day in calendar]
    rows[80].update(open=10.4, high=10.6, low=10.4, close=10.5, volume=200.)
    for row in rows[81:]:
        row.update(open=10.3, high=10.5, low=10.25, close=10.45)
    return calendar, rows


def proof(calendar, index, *, suspended=False):
    day = calendar[index]
    return {
        "calendarEvidence": {"independent": True, "asOf": day, "sourceRefs": ["synthetic:exchange-calendar"]},
        "historicalState": {"date": day, "eligible": True, "suspended": suspended, "asOf": day, "sourceRefs": ["synthetic:historical-identity"]},
        "actionToday": {"status": "noEventsProven", "start": day, "end": day, "asOf": day,
                        "sourceRefs": ["synthetic:complete-action-register"], "events": []},
        "actionWindow": {"status": "noEventsProven", "start": calendar[max(0, index - 80)], "end": day,
                         "asOf": day, "sourceRefs": ["synthetic:complete-action-register"],
                         "priceBasisId": "verified-raw", "events": []},
    }


def retest(row, **changes):
    row.update(open=10.2, high=10.3, low=10.1, close=10.2, volume=100., **changes)


def renewal(row):
    row.update(open=10.4, high=10.6, low=10.2, close=10.5, volume=200.)


class SignalSequenceTests(unittest.TestCase):
    def setUp(self):
        self.calendar, self.rows = fixture()
        self.sequence = SignalSequence("sh600001")

    def step(self, index, *, evidence=None, rows=None):
        values = self.rows if rows is None else rows
        return self.sequence.step(self.calendar[index], self.calendar[:index + 1],
                                  [row for row in values if row["date"] <= self.calendar[index]],
                                  proof(self.calendar, index) if evidence is None else evidence)

    def test_breakout_retest_renewal_order_and_full_transition_evidence(self):
        original = deepcopy(self.rows)
        anchored = self.step(80)
        self.assertEqual(anchored["state"]["name"], "waitingForRetest")
        self.assertEqual(anchored["transitions"][0]["facts"]["observations"], 81)
        self.assertEqual(anchored["state"]["anchor"], 10.)
        retest(self.rows[81])
        repaired = self.step(81)
        self.assertIsNone(repaired["signal"])
        self.assertEqual(repaired["state"]["name"], "waitingForRenewal")
        renewal(self.rows[82])
        confirmed = self.step(82)
        self.assertEqual(confirmed["signal"]["retestDate"], self.calendar[81])
        self.assertEqual(confirmed["signal"]["signalDate"], self.calendar[82])
        self.assertEqual(confirmed["signal"]["entryRule"], "oneAttemptNextExchangeSessionOpen;TPlusOneSellLock")
        self.assertAlmostEqual(confirmed["signal"]["distanceFromAnchor"], .05)
        self.assertEqual([event["kind"] for event in self.sequence.events], ["anchored", "retested", "signaled"])
        self.assertTrue(all(event["inputEvidence"]["actionToday"]["sourceRefs"] for event in self.sequence.events))
        self.assertEqual(original[:81], self.rows[:81])
        json.dumps(self.sequence.events, allow_nan=False)

    def test_idle_unknown_is_unevaluable_not_a_completed_pattern_failure(self):
        first = self.step(79)
        self.assertEqual(first["transitions"][0]["kind"], "unevaluable")
        self.assertEqual(first["transitions"][0]["reason"], "insufficient81SessionHistory")
        evidence = proof(self.calendar, 80)
        evidence["actionWindow"]["status"] = "unknown"
        second = self.step(80, evidence=evidence)
        self.assertEqual(second["state"], {"name": "idle"})
        self.assertEqual(second["transitions"][0]["kind"], "unevaluable")

    def test_flat_ma_is_not_an_uptrend_even_if_price_breaks_out(self):
        self.rows[0].update(open=10.5, high=10.5, low=10.5, close=10.5)
        result = self.step(80)
        self.assertEqual(result["transitions"][0]["kind"], "notTriggered")
        facts = result["transitions"][0]["facts"]
        self.assertEqual(facts["ma80"], facts["previousMa80"])

    def test_first_retest_anchor_is_never_replaced_by_a_later_better_trough(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        self.rows[82].update(open=10.1, high=10.22, low=10.05, close=10.15, volume=80.)
        self.step(82)
        self.rows[83].update(open=10.2, high=10.3, low=10.1, close=10.25, volume=200.)
        result = self.step(83)
        self.assertIsNone(result["signal"])
        self.assertEqual(result["state"]["retestDate"], self.calendar[81])
        self.assertEqual(result["state"]["retestHigh"], 10.3)
        self.rows[84].update(open=10.3, high=10.4, low=10.2, close=10.35, volume=200.)
        self.assertIsNotNone(self.step(84)["signal"])

    def test_confirmation_on_tenth_session_is_allowed(self):
        self.step(80)
        for index in range(81, 89):
            self.step(index)
        retest(self.rows[89])
        self.step(89)
        renewal(self.rows[90])
        result = self.step(90)
        self.assertEqual(result["transitions"][0]["elapsedSessions"], 10)
        self.assertIsNotNone(result["signal"])

    def test_first_retest_on_tenth_session_expires_without_same_day_confirmation(self):
        self.step(80)
        for index in range(81, 90):
            self.step(index)
        retest(self.rows[90])
        result = self.step(90)
        self.assertIsNone(result["signal"])
        self.assertEqual(result["transitions"][0]["kind"], "expired")
        self.assertEqual(result["state"], {"name": "idle"})

    def test_waiting_renewal_deadline_is_not_extended_by_additional_retests(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        for index in range(82, 91):
            retest(self.rows[index])
            result = self.step(index)
        self.assertEqual(result["transitions"][0]["kind"], "expired")
        self.assertFalse(self.sequence.signals)

    def test_anchor_break_has_priority_and_cannot_reanchor_on_same_bar(self):
        self.step(80)
        self.rows[81].update(open=10.5, high=11., low=9.9, close=11., volume=400.)
        result = self.step(81)
        self.assertEqual(result["transitions"][0]["reason"], "anchorBrokenBeforeEntry")
        self.assertEqual(result["state"], {"name": "idle"})
        self.assertIsNone(result["signal"])
        self.rows[82].update(open=11.2, high=11.5, low=11.2, close=11.5, volume=400.)
        self.assertEqual(self.step(82)["state"]["anchorDate"], self.calendar[82])

    def test_later_breakout_does_not_replace_anchor_or_restart_deadline(self):
        self.step(80)
        self.rows[81].update(open=10.6, high=11., low=10.4, close=11., volume=400.)
        result = self.step(81)
        self.assertEqual(result["state"]["anchor"], 10.)
        self.assertEqual(result["state"]["breakoutVolume"], 200.)
        self.assertEqual(result["state"]["anchorDate"], self.calendar[80])

    def test_known_suspensions_consume_deadline_without_inventing_missing_bars(self):
        self.step(80)
        for index in range(81, 91):
            result = self.step(index, evidence=proof(self.calendar, index, suspended=True), rows=self.rows[:81])
        self.assertEqual(result["transitions"][0]["kind"], "expired")
        self.assertEqual(result["transitions"][0]["reason"], "deadlineDuringKnownSuspension")
        self.assertEqual(result["transitions"][0]["elapsedSessions"], 10)

    def test_missing_trading_bar_aborts_as_unknown_instead_of_pattern_failure(self):
        self.step(80)
        result = self.step(81, rows=self.rows[:81])
        self.assertEqual(result["transitions"][0]["kind"], "abortedUnknown")
        self.assertEqual(result["transitions"][0]["reason"], "currentTradingBarUnknown")

    def test_suspension_with_positive_volume_is_conflicting_evidence_not_a_known_halt(self):
        self.step(80)
        result = self.step(81, evidence=proof(self.calendar, 81, suspended=True))
        self.assertEqual(result["transitions"][0]["kind"], "abortedUnknown")
        self.assertEqual(result["transitions"][0]["reason"], "suspensionContradictsPositiveVolume")

    def test_action_and_historical_status_unknown_are_distinct_from_known_invalidation(self):
        cases = [
            ("unknown-action", "abortedUnknown", "currentActionCompletenessUnknown"),
            ("effective-action", "invalidated", "effectiveCorporateAction"),
            ("unknown-state", "abortedUnknown", "historicalStateUnknown"),
            ("ineligible", "invalidated", "historicalSecurityIneligible"),
        ]
        for name, kind, reason in cases:
            with self.subTest(name=name):
                self.sequence = SignalSequence("sh600001")
                self.step(80)
                evidence = proof(self.calendar, 81)
                if name == "unknown-action":
                    evidence["actionToday"]["status"] = "unknown"
                elif name == "effective-action":
                    evidence["actionToday"].update(status="effective", events=[{"effectiveDate": self.calendar[81]}])
                elif name == "unknown-state":
                    evidence["historicalState"]["eligible"] = None
                else:
                    evidence["historicalState"]["eligible"] = False
                result = self.step(81, evidence=evidence)
                self.assertEqual(result["transitions"][0]["kind"], kind)
                self.assertEqual(result["transitions"][0]["reason"], reason)

    def test_pre_anchor_corporate_action_requires_explicit_comparable_bridge(self):
        evidence = proof(self.calendar, 80)
        action = {"effectiveDate": self.calendar[20], "knownByDate": self.calendar[19]}
        evidence["actionWindow"].update(status="bridged", events=[action])
        result = self.step(80, evidence=evidence)
        self.assertEqual(result["transitions"][0]["reason"], "historicalActionBridgeUnknown")
        self.sequence = SignalSequence("sh600001")
        action.update(bridgeVerified=True, bridgeSourceRefs=["synthetic:explicit-price-bridge"])
        self.assertEqual(self.step(80, evidence=evidence)["state"]["name"], "waitingForRetest")
        self.sequence = SignalSequence("sh600001")
        self.rows[20]["priceBasisId"] = "incompatible-raw"
        self.assertEqual(self.step(80, evidence=evidence)["transitions"][0]["reason"], "historicalPriceBasisUnknown")

    def test_price_factor_presence_never_proves_no_action(self):
        evidence = proof(self.calendar, 80)
        evidence["actionWindow"] = {"status": "unknown", "factorByDate": {day: 1. for day in self.calendar[:81]}}
        self.assertEqual(self.step(80, evidence=evidence)["transitions"][0]["kind"], "unevaluable")

    def test_future_or_stale_evidence_cannot_qualify_today(self):
        for variant in ("future-asof", "future-proof-end", "stale-state", "no-sources"):
            with self.subTest(variant=variant):
                self.sequence = SignalSequence("sh600001")
                evidence = proof(self.calendar, 80)
                if variant == "future-asof":
                    evidence["actionWindow"]["asOf"] = self.calendar[81]
                elif variant == "future-proof-end":
                    evidence["actionWindow"]["end"] = self.calendar[81]
                elif variant == "stale-state":
                    evidence["historicalState"]["date"] = self.calendar[79]
                else:
                    evidence["actionWindow"]["sourceRefs"] = []
                self.assertEqual(self.step(80, evidence=evidence)["transitions"][0]["kind"], "unevaluable")

    def test_future_append_and_caller_mutation_cannot_change_emitted_signal(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        renewal(self.rows[82])
        result = self.step(82)
        issued = deepcopy(self.sequence.signals)
        for row in self.rows[83:]:
            row.update(open=1., high=1., low=1., close=1., volume=9999.)
        self.step(83)
        result["signal"]["anchor"] = 999.
        result["transitions"][0]["before"]["anchor"] = 999.
        self.assertEqual(self.sequence.signals, issued)
        self.assertEqual(self.sequence.events[2]["before"]["anchor"], 10.)

    def test_input_future_bars_and_skipped_exchange_session_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "never future dated"):
            self.sequence.step(self.calendar[80], self.calendar[:81], self.rows[:82], proof(self.calendar, 80))
        self.step(80)
        with self.assertRaisesRegex(ValueError, "exactly the next exchange session"):
            self.step(82)

    def test_active_holdings_cannot_block_raw_patterns_or_backfill_rank_six(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        renewal(self.rows[82])
        self.step(82)
        self.assertEqual(self.sequence.state, {"name": "idle"})
        self.rows[83].update(open=10.8, high=11., low=10.8, close=11., volume=400.)
        self.step(83)
        self.assertEqual(self.sequence.state["anchorDate"], self.calendar[83])
        self.rows[84].update(open=10.7, high=10.8, low=10.65, close=10.7, volume=160.)
        self.step(84)
        self.rows[85].update(open=10.8, high=11., low=10.75, close=10.9, volume=400.)
        second = self.step(85)["signal"]
        self.assertIsNotNone(second)
        day = self.calendar[85]
        others = [{"symbol": f"sh60000{i}", "signalDate": day, "eligible": True,
                   "anchor": 10., "confirmedClose": 10.5 + i * .1} for i in range(2, 7)]
        ranked = rank_signals([second, *others])
        self.assertEqual(ranked[0]["symbol"], "sh600001")
        selected = [row for row in ranked if row["selectedTop5"]]
        active = {"sh600001": {"hitDate": None}}
        admitted = [row for row in selected if row["symbol"] not in active]
        self.assertEqual(len(admitted), 4)
        self.assertNotIn("sh600006", [row["symbol"] for row in admitted])
        # The external gate releases a known opening exit before the close;
        # the same immutable raw Top5 can then admit that symbol, without a
        # different candidate enumeration or ranking pass.
        active["sh600001"]["hitDate"] = day
        admitted_after_open_exit = [row for row in selected if row["symbol"] not in active or active[row["symbol"]]["hitDate"] == day]
        self.assertEqual(len(admitted_after_open_exit), 5)
        self.assertEqual(admitted_after_open_exit[0]["symbol"], "sh600001")
        self.assertEqual(len(self.sequence.signals), 2)

    def test_filter_precedes_ranking_and_ties_use_symbol(self):
        day = self.calendar[82]
        rows = [{"symbol": f"sh60000{number}", "eligible": True, "signalDate": day,
                 "anchor": 10., "confirmedClose": 10.2 + number * .1} for number in range(1, 7)]
        rows[0]["eligible"] = False
        rows.extend([{"symbol": "sh600009", "eligible": True, "signalDate": day, "anchor": 10., "confirmedClose": 10.4},
                     {"symbol": "unknown", "eligible": None, "confirmedClose": 1.}])
        ranked = rank_signals(reversed(rows))
        self.assertEqual([row["symbol"] for row in ranked[:2]], ["sh600002", "sh600009"])
        self.assertEqual(len(ranked), 6)
        self.assertEqual(sum(row["selectedTop5"] for row in ranked), 5)
        self.assertEqual([row["rank"] for row in ranked], [1, 2, 3, 4, 5, 6])
        self.assertNotIn("rank", rows[1])


if __name__ == "__main__":
    unittest.main()
