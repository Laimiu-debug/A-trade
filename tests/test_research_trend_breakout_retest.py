"""Causal signal boundaries and setup consumption; synthetic prices only."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

from service.research_trend_breakout_retest import build_signals, liquid_state

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_retest_test_oracle", ROOT / "scripts/verify-trend-breakout-retest.py")
REFERENCE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REFERENCE)


def fixture(symbols=1, length=190):
    days, current = [], date(2024, 1, 2)
    while len(days) < length:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    securities = []
    for n in range(symbols):
        rows = []
        for t, d in enumerate(days):
            p = 10 + .01 * t
            rows.append({"date": d, "open": p, "high": p + .1, "low": p - .1, "close": p,
                "volume": 10000000, "amount": 100000000 + n * 1000000})
        for t in range(160, length):
            # Original level at seed160: prior159 high11.69. First valid
            # recovery at163 stays within5% and above an aligned rising trend.
            c = 12. if t == 160 else 11.9
            rows[t].update(open=c, high=c + .1, low=c - .1, close=c)
        rows[163].update(open=11.85, low=11.7, high=12.05, close=12.)
        securities.append({"symbol": f"sh60{n:04d}", "bars": rows})
    return {"calendar": days, "calendarSource": "synthetic-independent", "adjustment": "raw", "securities": securities}, {d: 2e12 for d in days}


class RetestTests(unittest.TestCase):
    def build(self, data, market, start=160, end=180):
        return build_signals(data, market, start=data["calendar"][start], end=data["calendar"][end])

    def compare(self, data, market, result, start=160, end=180):
        expected, setups, _ = REFERENCE.oracle(data, market, data["calendar"][start], data["calendar"][end])
        self.assertEqual(result["schedules"], expected)
        actual = sorted((r["symbol"], r["seedDate"], r["status"], r.get("endedAt")) for r in result["setups"])
        self.assertEqual(actual, setups)

    def test_first_retest_is_later_than_seed_and_not_same_day_chasing(self):
        data, market = fixture()
        r = self.build(data, market)
        self.compare(data, market, r)
        self.assertEqual([x["signalDate"] for x in r["schedules"]["immediate"]], [data["calendar"][160]])
        self.assertEqual([x["signalDate"] for x in r["schedules"]["retestLiquid"]], [data["calendar"][163]])

    def test_future_poison_does_not_change_earlier_schedules(self):
        data, market = fixture()
        before = self.build(data, market, end=163)
        poison = deepcopy(data)
        for row in poison["securities"][0]["bars"][164:]:
            row.update(open=1., high=100., low=.1, close=100.)
        after = self.build(poison, market, end=163)
        self.assertEqual(before, after)
        self.assertEqual(self.build(data, market)["schedules"]["retestLiquid"], before["schedules"]["retestLiquid"])

    def test_missing_session_invalidates_setup_instead_of_compressing_window(self):
        data, market = fixture()
        del data["securities"][0]["bars"][162]
        r = self.build(data, market)
        self.compare(data, market, r)
        self.assertEqual(r["schedules"]["retestLiquid"], [])
        self.assertEqual(r["setups"][0]["status"], "unknownAlignedBar")

    def test_close_breaking_support_invalidates_before_later_recovery(self):
        data, market = fixture()
        data["securities"][0]["bars"][161].update(open=11.5, high=11.6, low=11.4, close=11.5)
        r = self.build(data, market)
        self.compare(data, market, r)
        self.assertEqual(r["schedules"]["retestLiquid"], [])
        self.assertEqual(r["setups"][0]["status"], "closeBelowBreakoutLevel")

    def test_first_retest_consumed_when_liquidity_fails_no_later_retry(self):
        data, market = fixture()
        market[data["calendar"][163]] = 5e11
        data["securities"][0]["bars"][164].update(open=11.9, high=12.1, low=11.7, close=12.05)
        r = self.build(data, market)
        self.compare(data, market, r)
        self.assertEqual(r["schedules"]["retestLiquid"], [])
        self.assertEqual(r["setups"][0]["endedAt"], data["calendar"][163])

    def test_seed_top5_is_frozen_before_filter_and_retest_has_no_backfill(self):
        data, market = fixture(symbols=6)
        r = self.build(data, market)
        self.compare(data, market, r)
        self.assertEqual(len(r["schedules"]["immediate"]), 5)
        self.assertEqual(len(r["schedules"]["retestLiquid"]), 5)
        self.assertNotIn("sh600000", [x["symbol"] for x in r["schedules"]["retestLiquid"]])

    def test_seed_before_start_can_retest_inside_bull_interval(self):
        data, market = fixture()
        r = self.build(data, market, start=162)
        self.compare(data, market, r, start=162)
        self.assertEqual(r["schedules"]["immediate"], [])
        self.assertEqual(len(r["schedules"]["retestLiquid"]), 1)

    def test_current_amount_floor_and_moderate_contraction_are_distinct(self):
        data, market = fixture()
        days = data["calendar"]
        for d in days[156:161]:
            market[d] = 1.7e12
        self.assertEqual(liquid_state(days, 160, market)["status"], "passed")
        market[days[160]] = .9e12
        self.assertEqual(liquid_state(days, 160, market)["status"], "failed")
        del market[days[150]]
        self.assertEqual(liquid_state(days, 160, market)["status"], "unknown")

    def test_expiry_and_censoring_are_not_fabricated_trades(self):
        data, market = fixture()
        for row in data["securities"][0]["bars"][161:]:
            row.update(open=12., close=12., high=12.1, low=11.9)
        r = self.build(data, market)
        self.compare(data, market, r)
        self.assertEqual(r["setups"][0]["status"], "expired20Sessions")
        short = self.build(data, market, end=170)
        self.assertEqual(short["setups"][0]["status"], "activeAtSourceEnd")
        self.assertEqual(short["schedules"]["retestLiquid"], [])

    def test_raw_basis_is_explicit(self):
        data, market = fixture()
        data["adjustment"] = "qfq"
        with self.assertRaises(ValueError):
            self.build(data, market)
        data["adjustment"] = "none"
        with self.assertRaises(ValueError):
            self.build(data, market)
        data["priceBasisId"] = "raw-unadjusted"
        self.assertEqual(len(self.build(data, market)["schedules"]["immediate"]), 1)


if __name__ == "__main__":
    unittest.main()
