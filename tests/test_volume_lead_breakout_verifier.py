"""Independent synthetic timing, arithmetic and provenance checks; no real data."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("_volume_lead_verifier_tests", ROOT / "scripts/verify-volume-lead-breakout.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def fixture(size=8, length=35):
    calendar = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(length)]
    securities = []
    for k in range(size):
        bars = []
        for i, day in enumerate(calendar):
            close = 11.0 if i == 21 else 13.0 if i == 22 else 10.0
            volume = 100.0
            if k % 2 == 0:
                if i == 1: close, volume = 11.0, 1000.0
                if i == 21: volume = 50.0
            bars.append({"date": day, "open": close, "high": max(12.0, close + .5), "low": min(9.0, close),
                         "close": close, "volume": volume, "amount": 100000000.0 - k * 1000000.0})
        securities.append({"symbol": f"s{k:03d}", "bars": bars})
    data = {"calendar": calendar, "securities": securities, "priceBasisId": "raw-unadjusted"}
    return data, calendar[22], calendar[22]


class VolumeLeadReferenceTests(unittest.TestCase):
    def test_exact_obv_windows_and_hand_calculation(self):
        data, start, end = fixture()
        result = V.reference_build(data, start=start, end=end)
        first, second = result["opportunities"][:2]
        fail, passed = first["evidence"]["obv"], second["evidence"]["obv"]
        self.assertEqual((fail["prior20Maximum"], fail["previousLevel"], fail["passed"]), (1000., 950., False))
        self.assertEqual((passed["prior20Maximum"], passed["previousLevel"], passed["passed"]), (0., 100., True))
        self.assertEqual(fail["comparisonDates"], data["calendar"][1:21])
        self.assertEqual(fail["levelDates"], data["calendar"][:22])
        self.assertEqual(first["evidence"]["priorAmountDates"], data["calendar"][2:22])
        self.assertEqual(first["evidence"]["windowDates"], data["calendar"][:23])

    def test_common_queue_precedes_gate_and_preserves_rank_holes(self):
        data, start, end = fixture()
        original = deepcopy(data)
        result = V.reference_build(data, start=start, end=end)
        self.assertEqual(data, original)
        self.assertEqual(result["counts"]["currentCandidateCount"], 8)
        self.assertEqual(result["counts"]["rankExcludedCount"], 3)
        self.assertEqual(result["counts"]["selectedFormationCounts"], {"passed": 2, "failed": 3, "unknown": 0})
        self.assertEqual([s["originalRank"] for s in result["schedules"]["volumeLead"]], [2, 4])
        self.assertEqual(result["counts"]["signals"], {"priceBreakout": 5, "volumeLead": 2})
        V.check_schedule_partition(result)

    def test_shared_previous_up_rejects_both_arms(self):
        data, start, end = fixture(size=1)
        data["securities"][0]["bars"][21]["close"] = 10.
        result = V.reference_build(data, start=start, end=end)
        self.assertEqual(result["schedules"], {p: [] for p in V.POLICIES})
        self.assertEqual(result["counts"]["commonStatusCounts"], {"candidate": 0, "notTriggered": 1, "commonUnknown": 0})
        self.assertEqual(result["counts"]["commonReasonCounts"]["previousUpFalse"], 1)

    def test_price_onset_strict_and_previous_rolling_window_distinct(self):
        for mode, reason in (("currentTie", "currentBreakoutFalse"), ("previousBreak", "previousBreakoutTrue")):
            data, start, end = fixture(size=1)
            bars = data["securities"][0]["bars"]
            if mode == "currentTie": bars[22]["close"] = 12.
            else:
                bars[21].update(close=12.5, high=12.75)
                bars[22].update(close=14., high=14.)
            result = V.reference_build(data, start=start, end=end)
            with self.subTest(mode=mode):
                self.assertEqual(result["opportunities"], [])
                self.assertEqual(result["counts"]["commonReasonCounts"][reason], 1)
        data, start, end = fixture(size=1)
        data["securities"][0]["bars"][1]["high"] = 100.
        result = V.reference_build(data, start=start, end=end)
        self.assertEqual(result["counts"]["currentCandidateCount"], 1)
        self.assertEqual(result["opportunities"][0]["evidence"]["previousPrior20High"], 100.)
        self.assertEqual(result["opportunities"][0]["evidence"]["currentPrior20High"], 12.)

    def test_obv_equal_is_failure_and_flat_close_increment_zero(self):
        data, start, end = fixture(size=1)
        bars = data["securities"][0]["bars"]
        bars[21]["volume"] = 100.
        bars[10]["volume"] = 1e200
        out = V.reference_build(data, start=start, end=end)["opportunities"][0]["evidence"]["obv"]
        self.assertEqual(out["previousLevel"], out["prior20Maximum"])
        self.assertFalse(out["passed"])
        self.assertEqual(out["signedVolumes"][10], 0)

    def test_baseline_volume_and_current_volume_do_not_enter_obv(self):
        data, start, end = fixture(size=1)
        expected = V.reference_build(data, start=start, end=end)
        for i in (0, 22):
            data["securities"][0]["bars"][i].update(volume=1e200, amount=1e200)
        self.assertEqual(V.reference_build(data, start=start, end=end), expected)

    def test_missing_slots_unknown_not_false_no_compression(self):
        data, start, end = fixture(size=2)
        bars = data["securities"][0]["bars"]
        bars[:] = [b for i, b in enumerate(bars) if i not in (0, 7)]
        result = V.reference_build(data, start=start, end=end)
        self.assertEqual(result["counts"]["commonStatusCounts"], {"candidate": 1, "notTriggered": 0, "commonUnknown": 1})
        self.assertEqual(result["counts"]["commonReasonCounts"]["missingAlignedBar"], 1)
        self.assertEqual(len(result["dailyRows"][0]["commonUnknowns"][0]["invalidSlots"]), 2)
        self.assertEqual(result["counts"]["selectedFormationCounts"]["unknown"], 0)

    def test_quality_reasons_are_nonexclusive_unique_per_symbol_day(self):
        data, start, end = fixture(size=1)
        bars = data["securities"][0]["bars"]
        bars[2].update(volume=0., amount=None)
        bars[3].update(volume=False, high=9.5)
        out = V.reference_build(data, start=start, end=end)
        self.assertEqual(out["dailyRows"][0]["commonUnknowns"][0]["reasons"],
                         ["nonpositiveOrUnknownVolume", "nonpositiveOrUnknownAmount", "invalidOHLCEnvelope"])
        self.assertEqual(out["counts"]["commonReasonCounts"]["nonpositiveOrUnknownVolume"], 1)

    def test_no_unregistered_status_basis_or_eligibility_filters(self):
        data, start, end = fixture(size=1)
        expected = V.reference_build(data, start=start, end=end)
        data["priceBasisId"] = "not-an-eligibility-filter"
        s = data["securities"][0]
        s["tradeStatusByDate"] = {start: 0.0}; s["stByDate"] = {start: True}; s["listingDate"] = start
        for b in s["bars"]:
            b.update(knownSuspended=True, priceBasisId="changed", preclose=-1)
        self.assertEqual(V.reference_build(data, start=start, end=end), expected)

    def test_derived_overflow_unknown_both_not_selected(self):
        for mode in ("amount", "obv", "both"):
            data, start, end = fixture(size=1)
            bars = data["securities"][0]["bars"]
            if mode in ("amount", "both"):
                for b in bars[2:22]: b["amount"] = 1e308
            if mode in ("obv", "both"):
                for i, b in enumerate(bars[:22]): b.update(close=10.+i*.01, open=10., high=12., volume=1e308)
            result = V.reference_build(data, start=start, end=end)
            with self.subTest(mode=mode):
                self.assertEqual(result["counts"]["commonStatusCounts"]["commonUnknown"], 1)
                self.assertEqual(result["opportunities"], [])
                self.assertEqual(result["dailyRows"][0]["commonUnknowns"][0]["invalidSlots"], [])

    def test_known_price_failure_does_not_require_unused_arithmetic(self):
        data, start, end = fixture(size=1)
        for b in data["securities"][0]["bars"]: b["amount"] = 1e308
        data["securities"][0]["bars"][22]["close"] = 10.
        out = V.reference_build(data, start=start, end=end)
        self.assertEqual(out["counts"]["commonStatusCounts"]["notTriggered"], 1)
        self.assertEqual(out["counts"]["commonReasonCounts"]["nonfinitePriorMeanAmount"], 0)

    def test_tie_order_full_zero_days_and_last_signal_retained(self):
        data, start, end = fixture()
        for s in data["securities"]:
            for b in s["bars"]: b["amount"] = 1e8
        data["securities"].reverse()
        result = V.reference_build(data, start=data["calendar"][20], end=end)
        self.assertEqual(len(result["dailyRows"]), 3)
        self.assertEqual([s["symbol"] for s in result["schedules"]["priceBreakout"]], [f"s{k:03d}" for k in range(5)])
        self.assertTrue(all(s["signalDate"] == end for s in result["schedules"]["priceBreakout"]))
        self.assertEqual(result["counts"]["commonStatusCounts"]["commonUnknown"], 16)

    def test_future_suffix_numeric_invariance_and_historical_identity_rejection(self):
        data, start, end = fixture()
        expected = V.reference_build(data, start=start, end=end)
        for s in data["securities"]:
            for b in s["bars"][23:]:
                for field in ("open", "high", "low", "close", "volume", "amount"): b[field] = float("nan")
        self.assertEqual(V.reference_build(data, start=start, end=end), expected)
        for mode in ("duplicateBar", "missingCalendar", "duplicateSymbol", "reversedBar"):
            bad, first, last = fixture()
            if mode == "duplicateBar": bad["securities"][0]["bars"].insert(1, deepcopy(bad["securities"][0]["bars"][0]))
            elif mode == "missingCalendar": bad["calendar"].pop(3)
            elif mode == "duplicateSymbol": bad["securities"][1]["symbol"] = bad["securities"][0]["symbol"]
            else: bad["securities"][0]["bars"][:2] = reversed(bad["securities"][0]["bars"][:2])
            with self.subTest(mode=mode), self.assertRaises((ValueError, AssertionError)):
                V.reference_build(bad, start=first, end=last)

    def test_account_oracle_sid_is_rebound_only_in_memory(self):
        with patch.object(V.G, "account_reference", return_value=({"independent": True}, {})) as call:
            args = ({}, {}, {}, [], {}, {}, "2022-2023", "trail10", {})
            self.assertEqual(V.account_reference(*args), ({"independent": True}, {}))
            call.assert_called_once_with(*args)
            self.assertEqual(V.G.SID, V.SID)
            with self.assertRaises((ValueError, AssertionError)): V.account_reference(*args[:7], "other", {})


if __name__ == "__main__": unittest.main()
