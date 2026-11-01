"""Synthetic 32-byte TDX records only; no source files or research outcomes."""
from copy import deepcopy
import hashlib
import struct
import unittest

from service.signal_interval_quality import scan_daily


CALENDAR = ["2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03", "2025-01-06"]
STATES = ("validPositiveActivity", "validZeroVolumeOrAmount", "invalidRecord", "duplicateDate", "missingRecord")


def record(day, *, opening=1000, high=1100, low=900, close=1050, amount=100000., volume=10000):
    encoded = int(day.replace("-", "")) if isinstance(day, str) else day
    return struct.pack("<IIIIIfII", encoded, opening, high, low, close, amount, volume, 0)


def scan(raw, calendar=None):
    return scan_daily(raw, list(CALENDAR if calendar is None else calendar), start=CALENDAR[0], end=CALENDAR[-1])


class SignalIntervalQualityTests(unittest.TestCase):
    def assert_conserved(self, result, expected):
        self.assertEqual([row["date"] for row in result["records"]], CALENDAR)
        counts = {state: expected.count(state) for state in STATES}
        self.assertEqual([row["state"] for row in result["records"]], expected)
        self.assertEqual(result["summary"]["calendarDates"], len(CALENDAR))
        self.assertEqual(result["summary"]["counts"], counts)
        self.assertEqual(result["counts"], counts)
        self.assertTrue(result["summary"]["calendarAccountingComplete"])
        for year in ("2024", "2025"):
            selected = [state for day, state in zip(CALENDAR, expected) if day.startswith(year)]
            self.assertEqual(result["byYear"][year], {state: selected.count(state) for state in STATES})
        self.assertEqual(sum(sum(row.values()) for row in result["byYear"].values()), len(CALENDAR))
        for state in STATES:
            self.assertEqual(sum(row[state] for row in result["byYear"].values()), counts[state])

    def test_positive_zero_activity_and_missing_are_distinct_and_conserved(self):
        raw = (record(CALENDAR[0])+record(CALENDAR[1], volume=0)+
               record(CALENDAR[2], amount=0)+record(CALENDAR[3], volume=0, amount=0))
        result = scan(raw)
        self.assert_conserved(result, [STATES[0], STATES[1], STATES[1], STATES[1], STATES[4]])
        self.assertIn("zeroVolume", result["records"][1]["flags"])
        self.assertIn("zeroAmount", result["records"][2]["flags"])
        self.assertTrue({"zeroVolume", "zeroAmount"} <= set(result["records"][3]["flags"]))
        self.assertEqual(result["rawRecordCount"], 4)
        self.assertEqual(result["interval"]["observedCalendarDateCount"], 4)

    def test_empty_raw_and_empty_calendar_have_explicit_zero_denominators(self):
        result = scan(b"")
        self.assert_conserved(result, [STATES[4]]*5)
        self.assertEqual(result["rawRecordCount"], 0)
        self.assertEqual(result["interval"]["observedCalendarDateCount"], 0)
        self.assertIsNone(result["interval"]["firstObservedDate"])
        self.assertIsNone(result["interval"]["lastObservedDate"])
        result = scan_daily(b"", [], start="2024-12-29", end="2025-01-07")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["summary"]["calendarDates"], 0)
        self.assertEqual(result["summary"]["counts"], dict.fromkeys(STATES, 0))
        self.assertEqual(result["byYear"], {})

    def test_identical_or_conflicting_duplicate_cannot_count_as_valid(self):
        for other in (record(CALENDAR[0]), record(CALENDAR[0], close=1100),
                      record(CALENDAR[0], low=0, amount=float("nan"), volume=0)):
            with self.subTest(other=other.hex()):
                result = scan(record(CALENDAR[0])+other)
                self.assert_conserved(result, [STATES[3]]+[STATES[4]]*4)
                self.assertEqual(result["rawRecordCount"], 2)
                self.assertEqual(result["interval"]["recordCount"], 2)
                self.assertEqual(result["interval"]["observedCalendarDateCount"], 1)
                self.assertIn("duplicateDate", result["records"][0]["flags"])
        self.assertTrue({"duplicateDate", "nonPositiveOhlc", "nonfiniteAmount", "zeroVolume"}
                        <= set(result["records"][0]["flags"]))

    def test_invalid_prices_amounts_and_simultaneous_flags_remain_invalid(self):
        cases = [({"low": 0}, "nonPositiveOhlc"), ({"opening": 1200}, "inconsistentOhlc"),
                 ({"close": 800}, "inconsistentOhlc"), ({"low": 1200}, "inconsistentOhlc"),
                 ({"amount": -1}, "negativeAmount"), ({"amount": float("nan")}, "nonfiniteAmount"),
                 ({"amount": float("inf")}, "nonfiniteAmount")]
        for values, flag in cases:
            with self.subTest(values=values):
                result = scan(record(CALENDAR[0], **values))
                self.assert_conserved(result, [STATES[2]]+[STATES[4]]*4)
                self.assertIn(flag, result["records"][0]["flags"])
        result = scan(record(CALENDAR[0], low=0, amount=float("nan"), volume=0))
        self.assertEqual(result["records"][0]["state"], STATES[2])
        self.assertTrue({"nonPositiveOhlc", "nonfiniteAmount", "zeroVolume"}
                        <= set(result["records"][0]["flags"]))

    def test_invalid_encoded_dates_are_unassignable_not_a_year_or_missing_slot(self):
        result = scan(record(20241301)+record(CALENDAR[2])+record(20250230)+record(0))
        self.assertEqual(result["invalidDateRecords"], 3)
        self.assertEqual(result["invalidDateRecordIndices"], [0, 2, 3])
        self.assertEqual(result["rawRecordCount"], 4)
        self.assertEqual(result["interval"]["recordCount"], 1)
        self.assertEqual(result["interval"]["firstObservedDate"], CALENDAR[2])
        self.assert_conserved(result, [STATES[4], STATES[4], STATES[0], STATES[4], STATES[4]])

    def test_outside_interval_bad_content_does_not_change_inside_classification(self):
        inside = b"".join(record(day) for day in CALENDAR)
        original = scan(inside)
        result = scan(record("2024-12-27", low=0, amount=float("nan"))+inside+
                      record("2026-01-02", opening=0, amount=-1))
        for key in ("records", "summary", "byYear", "counts", "offCalendarDates", "offCalendarRecords"):
            self.assertEqual(result[key], original[key])
        self.assertEqual({k: v for k, v in result["interval"].items() if k != "outsideIntervalDateRecords"},
                         {k: v for k, v in original["interval"].items() if k != "outsideIntervalDateRecords"})
        self.assertEqual(result["interval"]["outsideIntervalDateRecords"], 2)
        self.assertEqual(result["rawRecordCount"], 7)
        self.assertEqual(result["invalidDateRecords"], 0)

    def test_nonincreasing_order_and_nonadjacent_duplicates_are_counted(self):
        result = scan(record(CALENDAR[2])+record(CALENDAR[1])+record(20250230)+
                      record(CALENDAR[2])+record(CALENDAR[0]))
        # The unassignable date is skipped in the source-order comparison.
        self.assertEqual(result["nonIncreasingDateTransitions"], 2)
        self.assertEqual(result["invalidDateRecordIndices"], [2])
        self.assert_conserved(result, [STATES[0], STATES[0], STATES[3], STATES[4], STATES[4]])
        adjacent = scan(record(CALENDAR[0])+record(CALENDAR[0]))
        self.assertEqual(adjacent["nonIncreasingDateTransitions"], 1)
        self.assertTrue(result["summary"]["structureIssue"])
        self.assertIn("nonIncreasingDateTransitions", result["summary"]["structureReasons"])

    def test_incomplete_tail_kept_separate_from_complete_record_population(self):
        result = scan(record(CALENDAR[0])+b"\xff"*7)
        self.assertEqual(result["trailingBytes"], 7)
        self.assertEqual(result["rawRecordCount"], 1)
        self.assertEqual(result["invalidDateRecords"], 0)
        self.assert_conserved(result, [STATES[0]]+[STATES[4]]*4)
        result = scan(b"\xff"*31)
        self.assertEqual(result["trailingBytes"], 31)
        self.assertEqual(result["rawRecordCount"], 0)
        self.assert_conserved(result, [STATES[4]]*5)

    def test_off_calendar_rows_are_audited_without_entering_expected_day_denominator(self):
        result = scan(record(CALENDAR[0])+record("2025-01-04", low=0, amount=float("nan")))
        self.assert_conserved(result, [STATES[0]]+[STATES[4]]*4)
        self.assertEqual(result["offCalendarDates"], ["2025-01-04"])
        self.assertEqual(result["interval"]["offCalendarRecordCount"], 1)
        self.assertEqual(result["interval"]["recordCount"], 2)
        self.assertEqual(result["interval"]["lastObservedDate"], "2025-01-04")
        self.assertEqual(result["interval"]["lastObservedCalendarDate"], CALENDAR[0])
        self.assertEqual(result["offCalendarRecords"][0]["recordIndex"], 1)
        self.assertEqual(result["offCalendarRecords"][0]["date"], "2025-01-04")
        self.assertTrue({"nonPositiveOhlc", "nonfiniteAmount"} <= set(result["offCalendarRecords"][0]["flags"]))

    def test_inputs_remain_unchanged_and_no_prior_returned_state_leaks(self):
        raw = b"".join(record(day) for day in CALENDAR)
        calendar = list(CALENDAR)
        result = scan_daily(raw, calendar, start=CALENDAR[0], end=CALENDAR[-1])
        original = deepcopy(result)
        self.assertEqual(calendar, CALENDAR)
        result["records"][0]["flags"].append("callerMutation")
        result["summary"]["counts"][STATES[0]] = -1
        self.assertEqual(scan_daily(raw, calendar, start=CALENDAR[0], end=CALENDAR[-1]), original)

    def test_raw_prefix_identity_preserves_bytes_and_rejects_untrustworthy_structure(self):
        prefix = record("2024-12-27", low=0)+record(CALENDAR[0])+record(CALENDAR[-1])
        future = record("2025-01-07", low=0, amount=float("nan"))
        result = scan(prefix+future)
        self.assertEqual(result["prefixThroughEndSha256"], hashlib.sha256(prefix).hexdigest())
        self.assertEqual(result["prefixThroughEndBytes"], len(prefix))
        self.assertEqual(result["prefixThroughEndRecords"], 3)
        self.assertTrue(result["prefixIdentityUsable"])
        self.assertEqual(scan(prefix)["prefixThroughEndSha256"], result["prefixThroughEndSha256"])
        bad_cases = [record(CALENDAR[0])+record(20250230)+record(CALENDAR[-1]),
                     record(CALENDAR[1])+record(CALENDAR[0]),
                     record(CALENDAR[0])+record(CALENDAR[0]),
                     record("2025-01-07")+record(CALENDAR[-1]),
                     prefix+future+b"\xff"]
        for raw in bad_cases:
            with self.subTest(raw=raw.hex()):
                self.assertFalse(scan(raw)["prefixIdentityUsable"])
        empty_prefix = scan(future)
        self.assertEqual(empty_prefix["prefixThroughEndSha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(empty_prefix["prefixThroughEndBytes"], 0)
        self.assertEqual(empty_prefix["prefixThroughEndRecords"], 0)

    def test_calendar_duplicates_order_outside_or_invalid_dates_are_rejected(self):
        cases = [CALENDAR[:2]+[CALENDAR[1]]+CALENDAR[2:],
                 [CALENDAR[1], CALENDAR[0], *CALENDAR[2:]],
                 ["2024-12-27", *CALENDAR], [*CALENDAR, "2025-01-07"],
                 [CALENDAR[0], "2024-12-32", *CALENDAR[2:]]]
        for calendar in cases:
            with self.subTest(calendar=calendar), self.assertRaises(ValueError):
                scan(b"", calendar)


if __name__ == "__main__":
    unittest.main()
