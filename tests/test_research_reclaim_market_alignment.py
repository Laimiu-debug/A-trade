"""Causal macro windows, exact ranks and unknowns; no market outcomes."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

from service.research_reclaim_market_alignment import build_admissions, price_trend_state, SID

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_alignment_test_reference", ROOT / "scripts/verify-reclaim-market-alignment.py")
REFERENCE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REFERENCE)


def fixture():
    days, value = [], date(2024, 1, 2)
    while len(days) < 90:
        if value.weekday() < 5:
            days.append(value.isoformat())
        value += timedelta(days=1)
    amounts = {d: 2e12 for d in days}
    closes = {code: {d: 100. + i for i, d in enumerate(days)} for code in ("sh000001", "sz399106")}
    original = [{"signalId": f"s:{i}:{r}", "strategyId": SID, "symbol": f"sh60000{r}", "signalDate": days[i], "originalRank": r}
                for i in (65, 66, 67) for r in (1, 3)]
    return days, original, amounts, closes


class AlignmentTests(unittest.TestCase):
    def build(self, f, start=65, end=70):
        days, original, amounts, closes = f
        return build_admissions(days[:end + 1], [r for r in original if days[start] <= r["signalDate"] <= days[end]],
            amounts, closes, start=days[start], end=days[end])

    def compare(self, f, result):
        days, original, amounts, closes = f
        expected, rows = REFERENCE.oracle(days[:71], original, amounts, closes, days[65], days[70])
        self.assertEqual(result["schedules"], expected)
        for a, b in zip(result["daily"], rows):
            self.assertEqual(a["liquidity"]["status"], b["liquidityStatus"])
            self.assertEqual(a["combinedStatus"], b["combinedStatus"])
            self.assertEqual(a["priceTrend"]["indices"], b["indexTerms"])

    def test_full_pass_preserves_original_ids_and_rank_gaps_without_mutation(self):
        f = fixture()
        saved = deepcopy(f)
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(r["schedules"]["liquidTrend"], f[1])
        self.assertEqual(f, saved)
        self.assertEqual([r["originalRank"] for r in r["schedules"]["liquidTrend"]], [1, 3] * 3)

    def test_both_broad_indices_must_align_even_when_volume_is_large(self):
        f = fixture()
        f[3]["sz399106"][f[0][65]] = 90.
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(len(r["schedules"]["liquid"]), 6)
        self.assertEqual(len(r["schedules"]["liquidTrend"]), 4)
        self.assertNotIn(f[0][65], [r["signalDate"] for r in r["schedules"]["liquidTrend"]])

    def test_current_close_above_mean60_is_not_sufficient_if_mean20_lags(self):
        f = fixture()
        for code in f[3]:
            for i in range(46, 65):
                f[3][code][f[0][i]] = 70.
            f[3][code][f[0][65]] = 170.
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(r["daily"][0]["priceTrend"]["status"], "failed")

    def test_equality_does_not_pass_strict_trend(self):
        f = fixture()
        for code in f[3]:
            f[3][code] = {d: 100. for d in f[0]}
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(r["schedules"]["liquidTrend"], [])

    def test_missing_price_window_is_unknown_and_not_compressed(self):
        f = fixture()
        del f[3]["sh000001"][f[0][10]]
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(r["daily"][0]["priceTrend"]["status"], "unknown")
        self.assertEqual(r["schedules"]["liquidTrend"], [])
        self.assertEqual(price_trend_state(f[0], 58, f[3])["status"], "unknown")

    def test_missing_amount_is_unknown_while_original_signal_survives(self):
        f = fixture()
        del f[2][f[0][60]]
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(r["schedules"]["original"], f[1])
        self.assertEqual(r["schedules"]["liquid"], [])
        self.assertEqual(r["counts"]["unknownCombinedSessions"], 6)

    def test_future_price_and_amount_cannot_change_current_gate_or_signal(self):
        f = fixture()
        before = self.build(f, end=65)
        for d in f[0][66:]:
            f[2][d] = 1.
            for code in f[3]:
                f[3][code][d] = 1.
        self.assertEqual(before, self.build(f, end=65))

    def test_rejected_signal_is_never_delayed_to_a_later_pass(self):
        f = fixture()
        f[2][f[0][65]] = .5e12
        r = self.build(f)
        self.compare(f, r)
        self.assertEqual(len(r["schedules"]["liquidTrend"]), 4)
        self.assertEqual([r["signalId"] for r in r["schedules"]["liquidTrend"]], [r["signalId"] for r in f[1][2:]])

    def test_input_rejects_duplicate_rank_future_field_and_off_calendar_signal(self):
        for kind in ("duplicateRank", "futureField", "offCalendar"):
            f = fixture()
            if kind == "duplicateRank":
                f[1][1]["originalRank"] = 1
            elif kind == "futureField":
                f[1][0]["futureReturn"] = 100
            else:
                f[1][0]["signalDate"] = "2024-01-01"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build_admissions(f[0][:71], f[1], f[2], f[3], start=f[0][65], end=f[0][70])


if __name__ == "__main__":
    unittest.main()
