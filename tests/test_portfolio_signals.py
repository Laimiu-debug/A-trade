import copy
import datetime as dt
import math
import unittest
from unittest.mock import patch

from service.portfolio_config import normalize_config
from service.portfolio_signals import DailyScorer, rank_normalize
from service.portfolio_universe import _retain_causal_window
from service.strategies import prepare_bars


def bars(count=100, multiplier=1, wave=False):
    rows, day = [], dt.date(2022, 1, 3)
    while len(rows) < count:
        if day.weekday() < 5:
            i = len(rows)
            close = multiplier * (20 + .035 * i + (math.sin(i * .75) if wave else 0))
            rows.append({"date": day.isoformat(), "open": close - .01, "high": close + .1,
                         "low": close - .1, "close": close, "volume": 123457 + i % 13 * 723,
                         "amount": close * 1000000})
        day += dt.timedelta(days=1)
    return rows


def security(symbol="sh600001", count=100, **kwargs):
    return {"symbol": symbol, "name": "*ST today is not historical state", "bars": bars(count, **kwargs)}


def config(strategies=None, **kwargs):
    return normalize_config({"strategies": strategies or [{"id": "m", "kind": "momentum", "params": {"lookback": 10, "trendWindow": 20, "minReturn": 0}}],
                             "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0}, **kwargs})


class PortfolioSignalTests(unittest.TestCase):
    def test_full_cross_section_no_top_n_or_batch_truncation_and_equal_scores(self):
        data = {"securities": [security(f"sh{600000+i:06d}") for i in range(151)]}
        result = DailyScorer(data, config(topN=1)).daily(data["securities"][0]["bars"][-1]["date"])
        self.assertEqual(len(result["strategies"]["m"]), 151)
        self.assertEqual({r["score"] for r in result["strategies"]["m"]}, {.5})
        self.assertEqual(result["coverage"]["eligibleSecurities"], 151)
        self.assertFalse(result["coverage"]["batchTruncation"])

    def test_midrank_ties_and_symbol_permutation(self):
        rows = [{"symbol": "a", "rawScore": 1}, {"symbol": "b", "rawScore": 1}, {"symbol": "c", "rawScore": 2}]
        expected = rank_normalize(copy.deepcopy(rows))
        self.assertEqual(expected, rank_normalize(list(reversed(copy.deepcopy(rows)))))
        self.assertEqual(expected[0]["score"], 2.5 / 3)
        self.assertEqual(expected[1]["score"], 1 / 3)

    def test_future_records_and_future_listing_do_not_change_past_candidates(self):
        s = security(count=100, wave=True)
        day = s["bars"][70]["date"]
        cfg = config([{"id": "m", "kind": "momentum", "params": {"lookback": 10, "trendWindow": 20, "minReturn": 0}},
                      {"id": "turn", "kind": "forcePurpleToYellow"}])
        truncated = copy.deepcopy(s)
        truncated["bars"] = truncated["bars"][:71]
        new_listing = security("sz000001", count=20)
        new_listing["bars"] = bars(140)[120:]
        early = DailyScorer({"securities": [truncated]}, cfg).daily(day)
        full = DailyScorer({"securities": [new_listing, s]}, cfg).daily(day)
        self.assertEqual(early["strategies"], full["strategies"])
        self.assertEqual(early["pools"], full["pools"])
        self.assertEqual(full["eligibilityBySymbol"]["sz000001"]["reason"], "notObservedYet")

    def test_current_name_not_st_history_and_strict_unknown_rejected(self):
        s = security()
        day = s["bars"][-1]["date"]
        self.assertTrue(DailyScorer({"securities": [s]}, config()).daily(day)["eligibilityBySymbol"][s["symbol"]]["eligible"])
        strict = config(universe={"strictHistory": True, "excludeSt": True, "minListingDays": 60, "minAmount20": 0})
        result = DailyScorer({"securities": [s]}, strict).daily(day)
        self.assertEqual(result["eligibilityBySymbol"][s["symbol"]]["reason"], "strictHistoryMissing")
        s.update(listingDate=s["bars"][0]["date"], stByDate={day: False}, memberHistory={day: True}, tradeStatusByDate={day: 1})
        self.assertTrue(DailyScorer({"securities": [s]}, strict).daily(day)["eligibilityBySymbol"][s["symbol"]]["eligible"])
        s["stByDate"][day] = True
        self.assertEqual(DailyScorer({"securities": [s]}, strict).daily(day)["eligibilityBySymbol"][s["symbol"]]["reason"], "historicalSt")

    def test_suspension_recent_listing_amount_and_missing_bar_separate(self):
        rows = [security(f"sh60000{i}") for i in range(1, 5)]
        day = rows[0]["bars"][-1]["date"]
        rows[0]["tradeStatusByDate"] = {day: 0}
        rows[1]["listingDate"] = day
        for row in rows[2]["bars"]:
            row["amount"] = 0
        rows[3]["bars"].pop()
        cfg = config(universe={"strictHistory": False, "excludeSt": True, "minListingDays": 60, "minAmount20": 100})
        result = DailyScorer({"securities": rows}, cfg).daily(day)
        self.assertEqual([result["eligibilityBySymbol"][r["symbol"]]["reason"] for r in rows],
                         ["suspendedOrZeroVolume", "insufficientListingHistory", "amount20BelowMinimum", "missingDailyBar"])

    def test_known_membership_delisting_and_string_suspension_remain_effective_without_new_prices(self):
        rows = [security(f"sh60000{i}") for i in range(1, 4)]
        day = rows[0]["bars"][-1]["date"]
        rows[0]["tradeStatusByDate"] = {day: "0"}
        rows[1]["delistDate"] = day
        rows[1]["bars"].pop()
        result = DailyScorer({"securities": rows, "membershipByDate": {day: [r["symbol"] for r in rows[:2]]}}, config()).daily(day)
        self.assertEqual([result["eligibilityBySymbol"][r["symbol"]]["reason"] for r in rows],
                         ["suspendedOrZeroVolume", "delisted", "notHistoricalMember"])

    def test_force_independent_single_day_events_and_explicit_combination(self):
        s = security(count=160, wave=True)
        prepared = prepare_bars(s["bars"])
        cross_day = next(f["date"] for i, f in enumerate(prepared["force"]) if i >= 35 and f["goldenCross"])
        cfg = config([{"id": "cross", "kind": "forceGoldenCross"}, {"id": "persist", "kind": "forceTrend"}],
                     combinations=[{"id": "both", "method": "intersection", "children": [{"id": "cross"}, {"id": "persist"}]},
                                   {"id": "either", "method": "union", "children": [{"id": "cross"}, {"id": "persist"}]},
                                   {"id": "nested", "method": "weighted", "children": [{"id": "both"}, {"id": "either"}]}])
        scorer = DailyScorer({"securities": [s]}, cfg)
        result = scorer.daily(cross_day)
        self.assertEqual(len(result["strategies"]["cross"]), 1)
        self.assertEqual(result["strategies"]["persist"], [])
        self.assertEqual(result["strategies"]["both"], [])
        self.assertEqual(result["strategies"]["either"][0]["rawScore"], .25)
        self.assertEqual(result["strategies"]["nested"][0]["rawScore"], .25)
        index = next(i for i, b in enumerate(s["bars"]) if b["date"] == cross_day)
        following = scorer.daily(s["bars"][index + 1]["date"])
        self.assertEqual(following["strategies"]["cross"], [])
        self.assertTrue(following["eligibilityBySymbol"][s["symbol"]]["eligible"])
        self.assertEqual(following["strategyEligibility"]["cross"][s["symbol"]]["reason"], "noSignal")

    def test_pool_gates_are_independent_not_cumulative(self):
        s = security(count=160, wave=True)
        day = next(f["date"] for i, f in enumerate(prepare_bars(s["bars"])["force"]) if i >= 60 and f["purpleToYellow"])
        cfg = config([{"id": "baseTurn", "kind": "forcePurpleToYellow", "pool": "base"},
                      {"id": "trendTurn", "kind": "forcePurpleToYellow", "pool": "trend"}],
                     poolSettings={"trend": {"window": 60, "minReturn": 9}})
        result = DailyScorer({"securities": [s]}, cfg).daily(day)
        self.assertEqual(len(result["strategies"]["baseTurn"]), 1)
        self.assertEqual(result["strategies"]["trendTurn"], [])

    def test_seeded_truncation_is_exact_and_features_precomputed_once(self):
        full = security(count=850, wave=True)
        full["precloseByDate"] = {full["bars"][i]["date"]: full["bars"][i - 1]["close"] + .2 for i in range(50, 850, 137)}
        trimmed = copy.deepcopy(full)
        start = full["bars"][790]["date"]
        _retain_causal_window(trimmed, start)
        self.assertEqual(len(trimmed["bars"]), 560)
        cfg = config([{"id": kind, "kind": kind} for kind in ("forceGoldenCross", "forcePurpleToYellow", "forceTrend")])
        a = DailyScorer({"securities": [full]}, cfg)
        b = DailyScorer({"securities": [trimmed]}, cfg)
        with patch("service.portfolio_signals.prepare_bars", side_effect=AssertionError("daily recomputed history")):
            for row in full["bars"][790:]:
                first, second = a.daily(row["date"]), b.daily(row["date"])
                self.assertEqual(first["strategies"], second["strategies"])

    def test_real_benchmark_only_regime_and_future_invariance(self):
        s = security()
        day = s["bars"][80]["date"]
        self.assertEqual(DailyScorer({"securities": [s]}, config()).daily(day)["regime"]["status"], "unknown")
        benchmark = {"symbol": "sh000300", "source": "test-real-index-fixture", "bars": bars()}
        result = DailyScorer({"securities": [s], "benchmark": benchmark}, config()).daily(day)
        self.assertEqual(result["regime"]["trend"], "up")
        self.assertFalse(result["regime"]["affectsWeights"])
        benchmark["bars"] = benchmark["bars"][:81]
        self.assertEqual(result["regime"], DailyScorer({"securities": [s], "benchmark": benchmark}, config()).daily(day)["regime"])

    def test_invalid_source_is_reported_and_dag_rejected(self):
        bad = security()
        bad["bars"][50]["close"] = float("nan")
        day = bad["bars"][-1]["date"]
        result = DailyScorer({"securities": [bad]}, config()).daily(day)
        self.assertEqual(result["eligibilityBySymbol"][bad["symbol"]]["reason"], "invalidHistory")
        self.assertIn(bad["symbol"], result["coverage"]["failedSecurities"])
        with self.assertRaises(ValueError):
            DailyScorer({"securities": [security()]}, {"strategies": [{"id": "m", "kind": "momentum"}],
                "combinations": [{"id": "loop", "children": [{"id": "loop"}, {"id": "m"}]}]})


if __name__ == "__main__":
    unittest.main()
