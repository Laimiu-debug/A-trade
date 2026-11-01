from copy import deepcopy
import datetime as dt
import json
import unittest

from service.signal_sequence_price_proxy import CANDIDATE_ID, RawPriceSequence, rank_price_signals


def fixture(length=100):
    calendar, day = [], dt.date(2022, 1, 3)
    while len(calendar) < length:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += dt.timedelta(days=1)
    rows = [{"date": day, "open": 10., "high": 10., "low": 10., "close": 10., "volume": 100.,
             "priceBasisId": "raw-unadjusted"} for day in calendar]
    rows[80].update(open=10.4, high=10.6, low=10.4, close=10.5, volume=200.)
    for row in rows[81:]:
        row.update(open=10.3, high=10.5, low=10.25, close=10.45)
    return calendar, rows


def retest(row):
    row.update(open=10.2, high=10.3, low=10.1, close=10.2, volume=100.)


def renewal(row):
    row.update(open=10.4, high=10.6, low=10.2, close=10.5, volume=200.)


class RawPriceSequenceTests(unittest.TestCase):
    def setUp(self):
        self.calendar, self.rows = fixture()
        self.detector = RawPriceSequence("sh600001")

    def step(self, index, *, eligibility=None, known_suspended=False, slots=None):
        if slots is None:
            slots = [None] * max(0, 80 - index) + self.rows[max(0, index - 80):index + 1]
        return self.detector.step(self.calendar[index], index, slots[-1], slots,
            {"eligible": True, "reason": "eligible", "unknown": ["historicalMembership", "historicalSt"]}
            if eligibility is None else eligibility, known_suspended=known_suspended)

    def complete(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        renewal(self.rows[82])
        return self.step(82)

    def test_raw_three_stage_signal_keeps_all_unknowns_without_certifying_evidence(self):
        self.step(80)
        retest(self.rows[81])
        self.assertIsNone(self.step(81)["signal"])
        renewal(self.rows[82])
        result = self.step(82)
        signal = result["signal"]
        self.assertEqual(signal["strategyId"], CANDIDATE_ID)
        self.assertEqual((signal["anchorDate"], signal["retestDate"], signal["signalDate"]), tuple(self.calendar[80:83]))
        self.assertFalse(signal["certified"])
        self.assertFalse(signal["targetAchieved"])
        self.assertEqual(signal["actionEvidence"], "unverified")
        self.assertEqual(signal["quality"]["interpretation"], "raw-price-only")
        self.assertFalse(signal["quality"]["comparableRightsVerified"])
        self.assertEqual(signal["stageUnknownHistoricalFields"]["B"], ["historicalMembership", "historicalSt"])
        self.assertEqual(signal["entryRule"], "oneAttemptNextExchangeSessionOpen;TPlusOneSellLock")
        formation = signal["formationFacts"]
        self.assertEqual(formation["B"]["prior40High"], 10.)
        self.assertEqual(formation["B"]["prior40MeanVolume"], 100.)
        self.assertEqual(formation["R"]["low"], 10.1)
        self.assertEqual(formation["R"]["close"], 10.2)
        self.assertEqual(formation["R"]["volume"], 100.)
        self.assertEqual(formation["T"]["volume"], 200.)
        self.assertEqual(formation["T"]["priorSessionClose"], 10.2)
        self.assertEqual([row["kind"] for row in self.detector.events], ["anchored", "retested", "signaled"])
        self.assertEqual(self.detector.state, {"name": "idle"})
        json.dumps(self.detector.events, allow_nan=False)

    def test_incomplete_or_nonraw_anchor_window_is_unknown(self):
        for kind in ("missing", "zero-volume", "adjusted", "missing-raw-label"):
            with self.subTest(kind=kind):
                self.detector = RawPriceSequence("sh600001")
                slots = deepcopy(self.rows[:81])
                if kind == "missing":
                    slots[20] = None
                elif kind == "zero-volume":
                    slots[20]["volume"] = 0
                elif kind == "adjusted":
                    slots[20]["priceBasisId"] = "reference-adjusted"
                else:
                    del slots[20]["priceBasisId"]
                result = self.step(80, slots=slots)
                self.assertEqual(result["transition"]["kind"], "unevaluable")
                self.assertEqual(result["transition"]["reason"], "incomplete81SessionRawHistory")

    def test_insufficient_warmup_is_not_a_complete_pattern_failure(self):
        result = self.step(79)
        self.assertEqual(result["transition"]["kind"], "unevaluable")
        self.assertEqual(self.detector.state, {"name": "idle"})

    def test_future_dates_order_last_slot_and_daily_index_are_rejected(self):
        for kind in ("future", "reversed", "last-slot", "too-short"):
            with self.subTest(kind=kind):
                slots, current = deepcopy(self.rows[:81]), deepcopy(self.rows[80])
                if kind == "future":
                    slots[30]["date"] = self.calendar[90]
                elif kind == "reversed":
                    slots[30], slots[31] = slots[31], slots[30]
                elif kind == "last-slot":
                    slots[-1]["close"] = 10.45
                else:
                    slots.pop(0)
                with self.assertRaises(ValueError):
                    self.detector.step(self.calendar[80], 80, current, slots, {"eligible": True})
        self.step(80)
        with self.assertRaisesRegex(ValueError, "exactly one exchange session"):
            self.step(82)

    def test_future_append_or_mutation_does_not_change_issued_signal(self):
        result = self.complete()
        issued = self.detector.signals
        for row in self.rows[83:]:
            row.update(open=1., high=1., low=1., close=1., volume=9999.)
        self.step(83)
        result["signal"]["anchor"] = 999
        result["transition"]["before"]["retestHigh"] = 999
        self.assertEqual(self.detector.signals, issued)
        self.assertEqual(self.detector.events[2]["before"]["retestHigh"], 10.3)

    def test_waiting_uses_frozen_anchor_and_current_bar_not_rewritten_past_prices(self):
        self.step(80)
        retest(self.rows[81])
        slots = deepcopy(self.rows[1:82])
        for row in slots[:-1]:
            row.update(open=100., high=100., low=100., close=100., volume=99999.)
        result = self.step(81, slots=slots)
        self.assertEqual(result["transition"]["kind"], "retested")
        self.assertEqual(self.detector.state["anchor"], 10.)
        self.assertEqual(self.detector.state["breakoutVolume"], 200.)

    def test_first_retest_stays_fixed(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        self.rows[82].update(open=10.1, high=10.22, low=10.05, close=10.15, volume=80.)
        self.step(82)
        self.rows[83].update(open=10.2, high=10.3, low=10.1, close=10.25, volume=200.)
        self.assertIsNone(self.step(83)["signal"])
        self.assertEqual(self.detector.state["retestDate"], self.calendar[81])
        self.assertEqual(self.detector.state["retestHigh"], 10.3)

    def test_retest_nine_renewal_ten_is_valid_but_first_retest_ten_expires(self):
        self.step(80)
        for index in range(81, 89):
            self.step(index)
        retest(self.rows[89])
        self.step(89)
        renewal(self.rows[90])
        self.assertIsNotNone(self.step(90)["signal"])
        self.calendar, self.rows = fixture()
        self.detector = RawPriceSequence("sh600001")
        self.step(80)
        for index in range(81, 90):
            self.step(index)
        retest(self.rows[90])
        result = self.step(90)
        self.assertIsNone(result["signal"])
        self.assertEqual(result["transition"]["kind"], "expired")

    def test_repeated_retests_never_extend_deadline(self):
        self.step(80)
        for index in range(81, 91):
            retest(self.rows[index])
            result = self.step(index)
        self.assertEqual(result["transition"]["kind"], "expired")
        self.assertEqual(result["transition"]["elapsedSessions"], 10)

    def test_known_rejection_invalidates_and_does_not_reanchor_same_day(self):
        self.step(80)
        self.rows[81].update(open=10.8, high=11., low=10.6, close=11., volume=400.)
        result = self.step(81, eligibility={"eligible": False, "reason": "amount20BelowMinimum", "unknown": []})
        self.assertEqual(result["transition"]["kind"], "invalidated")
        self.assertEqual(self.detector.state, {"name": "idle"})
        self.assertIsNone(result["signal"])

    def test_unknown_eligibility_reasons_abort_instead_of_known_failure(self):
        for reason in ("missingDailyBar", "notObservedYet", "invalidHistory", "strictHistoryMissing"):
            with self.subTest(reason=reason):
                self.detector = RawPriceSequence("sh600001")
                self.step(80)
                result = self.step(81, eligibility={"eligible": False, "reason": reason})
                self.assertEqual(result["transition"]["kind"], "abortedUnknown")
                self.assertEqual(result["transition"]["reason"], reason)

    def test_zero_volume_without_known_halt_is_unknown_even_with_generic_suspension_reason(self):
        self.step(80)
        self.rows[81]["volume"] = 0
        result = self.step(81, eligibility={"eligible": False, "reason": "suspendedOrZeroVolume"})
        self.assertEqual(result["transition"]["kind"], "abortedUnknown")

    def test_known_halt_explains_generic_missing_bar_and_consumes_ten_day_deadline(self):
        self.step(80)
        for index in range(81, 91):
            slots = deepcopy(self.rows[index - 80:index + 1])
            slots[-1] = None
            result = self.step(index, slots=slots, known_suspended=True,
                               eligibility={"eligible": False, "reason": "missingDailyBar"})
        self.assertEqual(result["transition"]["kind"], "expired")
        self.assertEqual(result["transition"]["reason"], "deadlineDuringKnownSuspension")
        self.detector = RawPriceSequence("sh600001")
        slots = deepcopy(self.rows[:81])
        slots[-1] = None
        result = self.step(80, slots=slots, known_suspended=True, eligibility={"eligible": False, "reason": "suspendedOrZeroVolume"})
        self.assertEqual(result["transition"]["kind"], "notTriggered")

    def test_halt_does_not_override_known_rejection_or_unknown_eligibility(self):
        for reason in ("delisted", "historicalSt", "notHistoricalMember", "notListed", "amount20BelowMinimum", "strictHistoryMissing"):
            for waiting in (False, True):
                with self.subTest(reason=reason, waiting=waiting):
                    self.detector = RawPriceSequence("sh600001")
                    if waiting:
                        self.step(80)
                    index = 81 if waiting else 80
                    slots = deepcopy(self.rows[index - 80:index + 1])
                    slots[-1] = None
                    result = self.step(index, slots=slots, known_suspended=True,
                                       eligibility={"eligible": False, "reason": reason})
                    expected = ("abortedUnknown" if waiting else "unevaluable") if reason == "strictHistoryMissing" else ("invalidated" if waiting else "ineligible")
                    self.assertEqual(result["transition"]["kind"], expected)
                    self.assertEqual(self.detector.state, {"name": "idle"})

    def test_known_halt_positive_volume_conflict_is_unknown(self):
        self.step(80)
        result = self.step(81, known_suspended=True)
        self.assertEqual(result["transition"]["kind"], "abortedUnknown")
        self.assertEqual(result["transition"]["reason"], "suspensionContradictsPositiveVolume")

    def test_anchor_break_and_flat_trend_are_not_triggers(self):
        self.step(80)
        self.rows[81].update(open=10.5, high=11., low=9.9, close=11., volume=400.)
        self.assertEqual(self.step(81)["transition"]["reason"], "rawAnchorBrokenBeforeEntry")
        self.detector = RawPriceSequence("sh600001")
        self.rows[0].update(open=10.5, high=10.5, low=10.5, close=10.5)
        self.assertEqual(self.step(80)["transition"]["kind"], "notTriggered")

    def test_completed_pattern_resets_independently_of_external_position(self):
        self.complete()
        self.rows[83].update(open=10.8, high=11., low=10.8, close=11., volume=400.)
        self.step(83)
        self.rows[84].update(open=10.7, high=10.8, low=10.65, close=10.7, volume=160.)
        self.step(84)
        self.rows[85].update(open=10.8, high=11., low=10.75, close=10.9, volume=400.)
        signal = self.step(85)["signal"]
        self.assertIsNotNone(signal)
        self.assertEqual(len(self.detector.signals), 2)
        others = [{"symbol": f"sh60000{i}", "eligible": True, "signalDate": self.calendar[85],
                   "anchor": 10., "confirmedClose": 10.5 + i * .1} for i in range(2, 7)]
        ranked = rank_price_signals([signal, *others, {"eligible": False}])
        selected = [row for row in ranked if row["selectedTop5"]]
        self.assertEqual(selected[0]["symbol"], "sh600001")
        active = {"sh600001"}
        admitted = [row for row in selected if row["symbol"] not in active]
        self.assertEqual(len(admitted), 4)
        self.assertNotIn("sh600006", [row["symbol"] for row in admitted])

    def test_ranking_uses_distance_then_symbol_and_keeps_all_raw_candidates(self):
        signals = [{"symbol": symbol, "eligible": True, "signalDate": self.calendar[82],
                    "anchor": 10., "confirmedClose": close} for symbol, close in
                   (("sh600003", 10.5), ("sh600002", 10.3), ("sh600001", 10.3))]
        result = rank_price_signals(signals)
        self.assertEqual([row["symbol"] for row in result], ["sh600001", "sh600002", "sh600003"])
        self.assertNotIn("rank", signals[0])

    def test_pending_shape_is_visible_at_cutoff_without_fabricated_failure(self):
        self.step(80)
        retest(self.rows[81])
        self.step(81)
        self.assertEqual(self.detector.state["name"], "waitingForRenewal")
        self.assertEqual(self.detector.state["anchorDate"], self.calendar[80])
        self.assertFalse(self.detector.signals)
        self.assertEqual(self.detector.events[-1]["kind"], "retested")

    def test_streaming_mode_preserves_every_return_without_retaining_history(self):
        streaming = RawPriceSequence("sh600001", keep_history=False)
        retest(self.rows[81])
        renewal(self.rows[82])
        for index in range(80, 84):
            expected = self.step(index)
            slots = self.rows[index - 80:index + 1]
            actual = streaming.step(self.calendar[index], index, slots[-1], slots,
                {"eligible": True, "reason": "eligible", "unknown": ["historicalMembership", "historicalSt"]})
            self.assertEqual(actual, expected)
            self.assertEqual(streaming.state, self.detector.state)
        self.assertTrue(self.detector.events)
        self.assertTrue(self.detector.signals)
        self.assertEqual(streaming.events, [])
        self.assertEqual(streaming.signals, [])

    def test_frozen_formation_facts_do_not_alias_inputs_or_returned_state(self):
        anchored = self.step(80)
        anchored["transition"]["after"]["breakoutFacts"]["close"] = 999.
        self.assertEqual(self.detector.state["breakoutFacts"]["close"], 10.5)
        retest(self.rows[81])
        repaired = self.step(81)
        repaired["transition"]["after"]["retestFacts"]["low"] = 999.
        self.assertEqual(self.detector.state["retestFacts"]["low"], 10.1)
        self.rows[81]["low"] = 10.05
        renewal(self.rows[82])
        signal = self.step(82)["signal"]
        self.assertEqual(signal["formationFacts"]["R"]["low"], 10.1)
        signal["formationFacts"]["B"]["close"] = 999.
        self.assertEqual(self.detector.signals[0]["formationFacts"]["B"]["close"], 10.5)


if __name__ == "__main__":
    unittest.main()
