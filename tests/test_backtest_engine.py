import datetime as dt
import json
import unittest
from copy import deepcopy

from service.backtest_engine import BacktestEngine, execution_limit, lot_rule, normalize_config
from service.strategies import catalogue, evaluate


def bars(prices, volumes=None, start="2024-01-02"):
    day = dt.date.fromisoformat(start)
    result = []
    for i, close in enumerate(prices):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        result.append({"date": day.isoformat(), "open": close, "high": close, "low": close,
                       "close": close, "volume": volumes[i] if volumes else 1000000, "amount": close * 1000000})
        day += dt.timedelta(days=1)
    return result


def dataset(rows, symbol="sh600001", **extra):
    return {"securities": [{"symbol": symbol, "name": "测试", "bars": rows, **extra}], "fingerprint": "fixture"}


def config(**values):
    return {"strategyIds": ["momentum"], "strategyParams": {"momentum": {"lookback": 2, "trendWindow": 2, "minReturn": .005}},
            "initialCapital": 100000, "maxPositions": 1, "positionFraction": 1, "commissionRate": 0,
            "minCommission": 0, "stampDutyRate": 0, "transferFeeRate": 0, "slippageBps": 0,
            "stopLossPct": 0, "takeProfitPct": 0, "partialTakeProfitPct": 0, "trailingStopPct": 0,
            "maxHoldingDays": 2500, **values}


def run(data, settings=None):
    engine = BacktestEngine(data, settings or config())
    engine.step(20000)
    return engine.result()


class StrategyConfigurationTests(unittest.TestCase):
    def test_catalogue_is_independent_and_validation_is_strict(self):
        first = catalogue()
        first[0]["parameters"][0]["default"] = -99
        self.assertEqual(catalogue()[0]["parameters"][0]["default"], 40)
        for bad in ({"initialCapital": float("nan")}, {"maxPositions": True}, {"maxHoldingDays": 2.5},
                    {"strategyIds": ["unknown"]}, {"typo": 2}, {"startDate": "2024-01-99"},
                    {"strategyParams": {"momentum": {"futureReturn": 4}}}, {"slippageBps": float("inf")},
                    {"initialCapital": 10**1000}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_config(bad)

    def test_intersection_is_not_a_vote_and_lookback_excludes_today(self):
        history = bars([10 + i*.1 for i in range(15)])
        settings = normalize_config(config())
        self.assertIsNotNone(evaluate(history, 10, settings))
        settings["strategyIds"].append("trendBreakout")
        settings["strategyParams"]["trendBreakout"] = {"lookback": 3, "trendWindow": 3, "volumeRatio": 1.2}
        self.assertIsNone(evaluate(history, 10, settings))
        history[10]["volume"] = 2000000
        self.assertIsNotNone(evaluate(history, 10, settings))

    def test_indicators_cannot_see_future(self):
        history = bars([10 + i*.1 for i in range(15)])
        settings = normalize_config(config())
        expected = evaluate(history, 9, settings)
        for bar in history[10:]:
            bar["close"], bar["volume"] = 999, 1
        self.assertEqual(evaluate(history, 9, settings), expected)


class BacktestExecutionTests(unittest.TestCase):
    def test_next_open_t1_and_terminal_mark_to_market(self):
        history = bars([10+i*.1 for i in range(15)])
        result = run(dataset(history))
        buy = result["trades"][0]
        self.assertEqual(buy["signalDate"], history[5]["date"])
        self.assertEqual(buy["date"], history[6]["date"])
        self.assertEqual(buy["price"], history[6]["open"])
        self.assertEqual(len(result["positions"]), 1)
        self.assertTrue(all(t["side"] == "buy" for t in result["trades"]))
        self.assertAlmostEqual(result["metrics"]["finalEquity"], result["curve"][-1]["cash"] + result["positions"][0]["quantity"] * history[-1]["close"])
        immediate = run(dataset(history), config(maxHoldingDays=1))
        self.assertGreater(immediate["trades"][1]["date"], immediate["trades"][0]["date"])
        self.assertEqual(immediate["trades"][1]["date"], history[7]["date"])

    def test_resume_matches_uninterrupted_exactly_and_rejects_changed_data(self):
        data = dataset(bars([10+i*.04 for i in range(85)]))
        settings = config(maxHoldingDays=8, commissionRate=.0003, minCommission=5, slippageBps=10)
        expected = run(data, settings)
        engine = BacktestEngine(data, settings)
        engine.step(13)
        saved = json.loads(json.dumps(engine.checkpoint(), allow_nan=False))
        restored = BacktestEngine(data, settings, saved)
        while not restored.step(3):
            restored = BacktestEngine(data, settings, restored.checkpoint())
        self.assertEqual(restored.result(), expected)
        changed = deepcopy(data)
        changed["securities"][0]["bars"][-1]["amount"] += 1
        with self.assertRaises(ValueError):
            BacktestEngine(changed, settings, saved)
        saved["cash"] = float("nan")
        with self.assertRaises(ValueError):
            BacktestEngine(data, settings, saved)

    def test_start_date_retains_indicator_warmup_and_never_executes_before_start(self):
        history = bars([10+i*.03 for i in range(100)])
        settings = config(startDate=history[70]["date"], endDate=history[80]["date"], strategyParams={"momentum": {"lookback": 30, "trendWindow": 60, "minReturn": .02}})
        result = run(dataset(history), settings)
        self.assertEqual(len(result["curve"]), 11)
        self.assertEqual(result["trades"][0]["signalDate"], history[70]["date"])
        self.assertEqual(result["trades"][0]["date"], history[71]["date"])

    def test_open_limit_refuses_even_if_intraday_low_would_fill(self):
        history = bars([10, 10, 10, 10, 10, 10.1, 10.61, 10.6])
        history[6].update(open=10.61, low=10.2, close=10.3, high=10.61)
        result = run(dataset(history))
        self.assertFalse(any(t["date"] == history[6]["date"] for t in result["trades"]))
        self.assertTrue(any(d["reason"] == "openAtUpperLimitOrUncertainST" for d in result["decisions"]))

    def test_open_buy_quantity_never_uses_execution_close_or_execution_volume(self):
        original = bars([10+i*.1 for i in range(12)])
        modified = deepcopy(original)
        modified[6].update(close=11.3, high=11.3, volume=10000000)
        first = run(dataset(original))["trades"][0]
        second = run(dataset(modified))["trades"][0]
        self.assertEqual(first, second)

    def test_gap_stop_executes_next_open_not_stop_trigger_or_same_day_low(self):
        history = bars([10, 10, 10, 10, 10, 10.1, 10.2, 9.9, 9.8, 9.8])
        history[7].update(open=10.15, high=10.15, low=9.7)
        result = run(dataset(history), config(stopLossPct=.02))
        sale = next(t for t in result["trades"] if t["side"] == "sell")
        self.assertEqual(sale["signalDate"], history[7]["date"])
        self.assertEqual(sale["date"], history[8]["date"])
        self.assertEqual(sale["price"], 9.8)

    def test_partial_stage_only_changes_after_fill_and_remaining_shares_are_retained(self):
        history = bars([10, 10, 10, 10, 10, 10.1, 10.2, 10.6, 10.07, 10.2, 10.21])
        settings = config(partialTakeProfitPct=.02, partialSellFraction=.5)
        engine = BacktestEngine(dataset(history), settings)
        engine.step(9)
        self.assertFalse(engine.state["positions"]["sh600001"]["partialStage"])
        self.assertTrue(any(d["reason"] == "openAtLowerLimitOrUncertainST" for d in engine.state["decisions"]))
        engine.step(1)
        self.assertTrue(engine.state["positions"]["sh600001"]["partialStage"])
        result = engine.result()
        bought = sum(t["quantity"] for t in result["trades"] if t["side"] == "buy")
        sold = sum(t["quantity"] for t in result["trades"] if t["side"] == "sell")
        self.assertEqual(bought - sold, result["positions"][0]["quantity"])
        self.assertGreater(result["positions"][0]["quantity"], 0)
        self.assertEqual(result["metrics"]["closedTradeCount"], 0)

    def test_partial_star_remainder_below_minimum_is_cancelled_without_losing_shares(self):
        history = bars([10, 10, 10, 10, 10, 10.1, 10.2, 10.6, 10.61, 10.62, 10.63],
                       [1000000]*7 + [22000]*4)
        result = run(dataset(history, "sh688001"), config(initialCapital=10200, partialTakeProfitPct=.02))
        self.assertEqual([t["quantity"] for t in result["trades"]], [1000, 220, 220])
        self.assertEqual(result["positions"][0]["quantity"], 560)
        self.assertTrue(result["positions"][0]["partialStage"])
        self.assertEqual(result["pendingOrders"]["sells"], [])
        self.assertTrue(any(d["reason"] == "partialRemainingBelowMinimum" and d["unfilledQuantity"] == 60 for d in result["decisions"]))

    def test_slippage_must_not_push_fill_beyond_limit(self):
        history = bars([10, 10, 10, 10, 10, 10.1, 11.1, 11.1])
        # Known ordinary stock has a 10% boundary of 11.11, but the slipped
        # 11.1 open would cost 11.1222 and is therefore conservatively rejected.
        history[6].update(low=10.5, close=10.6, high=11.1)
        data = dataset(history, stByDate={history[6]["date"]: False})
        result = run(data, config(slippageBps=20))
        self.assertFalse(any(t["date"] == history[6]["date"] for t in result["trades"]))
        self.assertTrue(any(d["reason"] == "slippageBeyondLimitOrUncertainST" for d in result["decisions"]))

    def test_missing_bar_defers_sale_and_marks_stale_value(self):
        history = bars([10+i*.1 for i in range(12)])
        second = bars([20]*12)
        removed = history.pop(7)
        data = dataset(history)
        data["securities"].append({"symbol": "sz000001", "bars": second})
        result = run(data, config(maxHoldingDays=1))
        mark = next(p for p in result["curve"] if p["date"] == removed["date"])
        self.assertEqual(mark["staleSymbols"], ["sh600001"])
        self.assertTrue(any(d["status"] == "deferred" and d["reason"] == "missingBar" for d in result["decisions"]))
        sale = next(t for t in result["trades"] if t["side"] == "sell")
        self.assertGreater(sale["date"], removed["date"])

    def test_flat_no_trade_metrics_are_null_and_serializable(self):
        result = run(dataset(bars([10]*30)))
        self.assertIsNone(result["metrics"]["sharpe"])
        self.assertIsNone(result["metrics"]["calmar"])
        self.assertIsNone(result["metrics"]["winRatePct"])
        self.assertEqual(result["metrics"]["maxDrawdownPct"], 0)
        json.dumps(result, allow_nan=False)

    def test_board_minimum_and_increment(self):
        self.assertEqual(lot_rule("sh688001"), (200, 1))
        self.assertEqual(lot_rule("bj920001"), (100, 1))
        self.assertEqual(lot_rule("sh600001"), (100, 100))
        history = bars([10+i*.1 for i in range(10)])
        main = run(dataset(history), config(initialCapital=3450))["trades"][0]["quantity"]
        star = run(dataset(history, "sh688001"), config(initialCapital=3450))["trades"][0]["quantity"]
        self.assertEqual(main, 300)
        self.assertEqual(star, 325)
        self.assertFalse(run(dataset(history, "sh688001"), config(initialCapital=1900))["trades"])

    def test_prior_volume_caps_fills_and_cash_never_goes_negative(self):
        history = bars([10+i*.1 for i in range(10)], [25000]*10)
        result = run(dataset(history), config(initialCapital=3000, commissionRate=.01, minCommission=50))
        self.assertEqual(result["trades"][0]["quantity"], 200)
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))

    def test_extreme_configured_sell_fees_cannot_create_negative_cash(self):
        history = bars([.09, .09, .09, .09, .09, .091, .092, .095, .095, .095])
        result = run(dataset(history), config(initialCapital=1000, minCommission=900, partialTakeProfitPct=.02))
        self.assertTrue(any(d["reason"] == "insufficientCashForSellFees" for d in result["decisions"]))
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))
        self.assertFalse(result["positions"][0]["partialStage"])

    def test_automatic_historical_stamp_tax_and_cost_stress(self):
        engine = BacktestEngine(dataset(bars([10]*10)), config(stampDutyRate=None))
        self.assertEqual(engine._fees(100000, "sell", "2023-08-25"), 100)
        self.assertEqual(engine._fees(100000, "sell", "2023-08-28"), 50)
        stressed = BacktestEngine(dataset(bars([10]*10)), config(stampDutyRate=None, costMultiplier=2, slippageBps=10))
        self.assertEqual(stressed._fees(100000, "sell", "2023-08-25"), 200)
        self.assertAlmostEqual(stressed._price(10, "buy"), 10.02)

    def test_historical_st_does_not_use_present_name_and_rules_are_date_specific(self):
        sec = {"symbol": "sh600001", "name": "普通股票"}
        self.assertEqual(execution_limit(sec, "2026-07-03"), .05)
        self.assertEqual(execution_limit(sec, "2026-07-06"), .1)
        sec["stByDate"] = {"2020-01-01": False}
        self.assertEqual(execution_limit(sec, "2020-01-01"), .1)
        self.assertEqual(execution_limit({"symbol": "sz300001"}, "2020-08-21"), .05)
        self.assertEqual(execution_limit({"symbol": "sz300001"}, "2020-08-24"), .2)

    def test_raw_discontinuity_quarantines_and_invalidates_held_value(self):
        history = bars([10, 10, 10, 10, 10, 10.1, 10.2, 5.1, 5.2, 5.3])
        result = run(dataset(history))
        self.assertIn("sh600001", result["quality"]["quarantinedSymbols"])
        self.assertFalse(result["curve"][-1]["valuationReliable"])
        self.assertEqual(len(result["trades"]), 1)

    def test_invalid_ohlc_nan_duplicates_adjusted_input_rejected(self):
        base = dataset(bars([10]*10))
        for bad in ("ohlc", "nan", "duplicate", "adjustment"):
            data = deepcopy(base)
            if bad == "ohlc":
                data["securities"][0]["bars"][2]["high"] = 9
            elif bad == "nan":
                data["securities"][0]["bars"][2]["close"] = float("nan")
            elif bad == "duplicate":
                data["securities"][0]["bars"][2]["date"] = data["securities"][0]["bars"][1]["date"]
            else:
                data["adjustment"] = "qfq"
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                BacktestEngine(data, config())


if __name__ == "__main__":
    unittest.main()
