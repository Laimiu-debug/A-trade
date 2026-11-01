"""Scientific boundary/timing/unknown tests, not a market parameter search."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

from service.research_reclaim_entry_volatility import build_admissions, feature_at_close, SID

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_entry_ntr_test_oracle", ROOT / "scripts/verify-reclaim-entry-volatility.py")
REFERENCE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REFERENCE)


def fixture():
    days, cursor = [], date(2024, 1, 2)
    while len(days) < 35:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    securities = [{"symbol": symbol, "bars": [{"date": d, "open": 10., "close": 10., "high": 10.1, "low": 9.9, "volume": 1000000.} for d in days]}
                  for symbol in ("sh600001", "sh600002")]
    originals = [{"signalId": f"{d}:{rank}", "strategyId": SID, "symbol": symbol, "signalDate": d, "originalRank": rank}
                 for d in days[25:27] for symbol, rank in (("sh600001", 1), ("sh600002", 3))]
    bases = {"liquid": deepcopy(originals), "liquidTrend": deepcopy(originals[2:])}
    return {"calendar": days, "calendarSource": "synthetic-independent", "priceBasisId": "raw-unadjusted", "securities": securities}, originals, bases


class EntryVolatilityTests(unittest.TestCase):
    def build(self, f):
        return build_admissions(*f, start=f[0]["calendar"][25], end=f[0]["calendar"][-1])

    def compare(self, f, result):
        expected, schedules = REFERENCE.oracle(*f)
        self.assertEqual(result["schedules"], schedules)
        for a, b in zip(result["features"], expected):
            for key in ("signalId", "asOfDate", "windowDates", "meanTRPct20", "windowStatus", "admissionStatus"):
                self.assertEqual(a[key], b[key])

    def test_known_twopercent_range_preserves_original_rank_gaps_and_inputs(self):
        f, saved = fixture(), fixture()
        result = self.build(f)
        self.compare(f, result)
        self.assertAlmostEqual(result["features"][0]["meanTRPct20"], .02)
        self.assertEqual(result["schedules"]["liquidNtr5"], f[2]["liquid"])
        self.assertEqual(f, saved)

    def test_exact5_ceiling_passes_just_above_fails(self):
        f = fixture()
        for r in f[0]["securities"][0]["bars"]:
            r.update(high=10.25, low=9.75)
        result = self.build(f)
        self.compare(f, result)
        self.assertEqual(result["features"][0]["meanTRPct20"], .05)
        self.assertEqual(result["features"][0]["admissionStatus"], "passed")
        for r in f[0]["securities"][0]["bars"]:
            r["high"] = 10.2501
        result = self.build(f)
        self.compare(f, result)
        self.assertEqual([r["originalRank"] for r in result["schedules"]["liquidNtr5"]], [3, 3])

    def test_current_signal_close_is_included_but_next_session_is_not(self):
        f = fixture()
        d = f[0]["calendar"][25]
        lookup = {r["date"]: r for r in f[0]["securities"][0]["bars"]}
        before = feature_at_close(f[0]["calendar"], 25, lookup, {})
        lookup[f[0]["calendar"][26]].update(high=100., low=.1)
        self.assertEqual(before, feature_at_close(f[0]["calendar"], 25, lookup, {}))
        lookup[d].update(high=15., low=5.)
        after = feature_at_close(f[0]["calendar"], 25, lookup, {})
        self.assertEqual(after["admissionStatus"], "failed")
        self.assertEqual(after["asOfDate"], d)

    def test_missing_slot_unknown_not_compressed_or_replaced(self):
        f = fixture()
        del f[0]["securities"][0]["bars"][10]
        result = self.build(f)
        self.compare(f, result)
        self.assertIsNone(result["features"][0]["meanTRPct20"])
        self.assertEqual(result["features"][0]["windowStatus"], "unknown")
        self.assertEqual(len(result["features"][0]["windowDates"]), 21)

    def test_insufficient_history_and_invalid_ohlcv_are_unknown(self):
        f = fixture()
        lookup = {r["date"]: r for r in f[0]["securities"][0]["bars"]}
        self.assertEqual(feature_at_close(f[0]["calendar"], 19, lookup, {})["windowStatus"], "unknown")
        lookup[f[0]["calendar"][25]]["volume"] = 0.
        self.assertEqual(feature_at_close(f[0]["calendar"], 25, lookup, {})["windowStatus"], "unknown")

    def test_explicit_reference_is_used_and_invalid_explicit_is_not_filled(self):
        f = fixture()
        for r in f[0]["securities"][0]["bars"][:25]:
            r.update(open=20., close=20., high=20.2, low=19.8)
        f[0]["securities"][0]["precloseByDate"] = {f[0]["calendar"][25]: 10.}
        result = self.build(f)
        self.compare(f, result)
        self.assertAlmostEqual(result["features"][0]["meanTRPct20"], .02)
        f[0]["securities"][0]["precloseByDate"][f[0]["calendar"][25]] = None
        result = self.build(f)
        self.compare(f, result)
        self.assertEqual(result["features"][0]["windowStatus"], "unknown")

    def test_zero_range_is_known_not_missing_or_a_future_return(self):
        f = fixture()
        for r in f[0]["securities"][0]["bars"]:
            r.update(high=10., low=10.)
        result = self.build(f)
        self.compare(f, result)
        self.assertEqual(result["features"][0]["meanTRPct20"], 0.)
        self.assertEqual(result["features"][0]["admissionStatus"], "passed")

    def test_future_poison_preserves_earlier_opportunity_and_admission(self):
        f = fixture()
        result = self.build(f)
        for row in f[0]["securities"][0]["bars"][27:]:
            row.update(open=100., close=100., high=101., low=.1)
        self.assertEqual(result, self.build(f))

    def test_source_basis_and_parent_rank_changes_are_rejected(self):
        for kind in ("adjusted", "rank", "notOriginal", "observedCalendar"):
            f = fixture()
            if kind == "adjusted":
                f[0]["priceBasisId"] = "qfq"
            elif kind == "rank":
                f[2]["liquid"][1]["originalRank"] = 2
            elif kind == "notOriginal":
                f[2]["liquid"][0]["signalId"] = "injected-after-outcome"
            else:
                f[0]["calendarSource"] = "observed-only"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.build(f)


if __name__ == "__main__":
    unittest.main()
