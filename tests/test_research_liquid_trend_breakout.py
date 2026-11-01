from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

from service.research_liquid_trend_breakout import build_signals, liquidity_state

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_liquid_oracle_test", ROOT / "scripts/verify-liquid-trend-breakout.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def fixture(n=1):
    calendar = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(220)]
    securities = []
    for j in range(n):
        bars = []
        for i, d in enumerate(calendar):
            price = 100 + i * .05 + (2 if i == 170 else 0)
            bars.append({"date": d, "open": price, "close": price, "high": price + .2,
                         "low": price - .2, "volume": 1e6, "amount": 100e6})
        securities.append({"symbol": f"sh{600000+j}", "bars": bars})
    return {"calendar": calendar, "securities": securities}, {d: 1.2e12 for d in calendar}


class LiquidTrendBreakoutTests(unittest.TestCase):
    def run_at(self, data, market, t=170):
        d = data["calendar"][t]
        return build_signals(data, market, start=d, end=d)

    def test_nominal_and_oracle(self):
        data, market = fixture()
        result = self.run_at(data, market)
        self.assertEqual([len(v) for v in result["schedules"].values()], [1, 1, 1])
        signals, counts = verifier.oracle(data, market, data["calendar"][170], data["calendar"][170])
        self.assertEqual(signals, result["schedules"])
        self.assertEqual(counts[data["calendar"][170]]["commonCandidates"], 1)

    def test_future_numeric_poison_ignored(self):
        data, market = fixture()
        before = self.run_at(data, market)
        data["securities"][0]["bars"][171].update(close=float("nan"), high=-1, amount=None)
        market[data["calendar"][171]] = -1
        self.assertEqual(before, self.run_at(data, market))

    def test_missing_calendar_slot_does_not_compress(self):
        data, market = fixture()
        del data["securities"][0]["bars"][100]
        result = self.run_at(data, market)
        self.assertEqual(result["daily"][0]["unknownWindow"], 1)
        self.assertFalse(result["schedules"]["breakout120"])

    def test_unknown_market_not_zero_and_not_block_other_policies(self):
        data, market = fixture()
        del market[data["calendar"][160]]
        result = self.run_at(data, market)
        self.assertEqual(result["daily"][0]["liquidity"]["status"], "unknown")
        self.assertEqual([len(v) for v in result["schedules"].values()], [1, 1, 0])

    def test_market_below_absolute_floor(self):
        data, market = fixture()
        market = {d: .9e12 for d in market}
        result = self.run_at(data, market)
        self.assertFalse(result["schedules"]["trendEarlyLiquid"])
        self.assertTrue(result["schedules"]["trendEarly"])

    def test_market_shrinking_blocks_even_if_above_floor(self):
        data, market = fixture()
        for d in data["calendar"][166:171]:
            market[d] = 1.1e12
        self.assertEqual(self.run_at(data, market)["daily"][0]["liquidity"]["status"], "failed")

    def test_exact_market_boundaries_inclusive(self):
        data, market = fixture()
        self.assertEqual(liquidity_state(data["calendar"], 170, {d: 1e12 for d in market})["status"], "passed")

    def test_rank_no_backfill_after_gate(self):
        data, market = fixture(6)
        # Stronger recent growth makes the original rank1 fail the early gate.
        for i in range(150, 171):
            p = 100 + (i - 149) * 2
            data["securities"][0]["bars"][i].update(open=p, close=p, high=p+.2, low=p-.2)
        data["securities"][0]["bars"][160]["high"] = 150
        data["securities"][0]["bars"][170].update(open=160, close=160, high=160.2, low=159.8)
        result = self.run_at(data, market)
        self.assertEqual([r["originalRank"] for r in result["schedules"]["trendEarly"]], [2, 3, 4, 5])
        self.assertTrue(all(r["symbol"] != "sh600005" for p in result["schedules"].values() for r in p))

    def test_prior_breakout_blocks_repeat(self):
        data, market = fixture()
        for t, price in ((169, 110), (170, 112)):
            data["securities"][0]["bars"][t].update(open=price, close=price, high=price+.2, low=price-.2)
        self.assertFalse(self.run_at(data, market)["schedules"]["breakout120"])

    def test_invalid_envelope_and_boolean_volume_are_unknown(self):
        for change in ({"low": 999}, {"volume": True}, {"amount": float("inf")}, {"suspended": True}):
            data, market = fixture()
            data["securities"][0]["bars"][160].update(change)
            self.assertEqual(self.run_at(data, market)["daily"][0]["unknownWindow"], 1)

    def test_duplicate_bar_and_symbol_rejected(self):
        data, market = fixture()
        data["securities"].append(deepcopy(data["securities"][0]))
        with self.assertRaises(ValueError):
            self.run_at(data, market)
        data, market = fixture()
        data["securities"][0]["bars"].insert(160, deepcopy(data["securities"][0]["bars"][160]))
        with self.assertRaises(ValueError):
            self.run_at(data, market)

    def test_full_window_oracle_and_input_immutability(self):
        data, market = fixture(6)
        before = deepcopy(data)
        result = build_signals(data, market, start=data["calendar"][139], end=data["calendar"][200])
        oracle, counts = verifier.oracle(data, market, data["calendar"][139], data["calendar"][200])
        self.assertEqual(oracle, result["schedules"])
        self.assertEqual(before, data)
        for r in result["daily"]:
            self.assertEqual(counts[r["date"]]["unknownWindow"], r["unknownWindow"])
            self.assertEqual(r["knownFalse"] + r["unknownWindow"] + r["commonCandidates"], r["fixedPopulation"])


if __name__ == "__main__":
    unittest.main()
