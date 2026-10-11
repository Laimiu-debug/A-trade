import datetime as dt
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from service.portfolio_engine import PortfolioEngine, execution_limit, lot_rule


SYMBOLS = ("sh600001", "sz000001", "sh600002", "sh688001", "bj920001")


def bars(prices, start="2024-01-02", volume=1_000_000):
    day = dt.date.fromisoformat(start)
    result = []
    for price in prices:
        while day.weekday() > 4:
            day += dt.timedelta(days=1)
        result.append({"date": day.isoformat(), "open": price, "high": price, "low": price,
                       "close": price, "volume": volume, "amount": volume * price})
        day += dt.timedelta(days=1)
    return result


def dataset(prices=None, symbols=SYMBOLS[:2], length=12):
    securities = []
    for index, symbol in enumerate(symbols):
        rows = bars(prices[index] if prices else [10.] * length)
        securities.append({"symbol": symbol, "name": symbol, "bars": rows, "listingDate": "2010-01-01",
                           "stByDate": {row["date"]: False for row in rows},
                           "tradeStatusByDate": {row["date"]: True for row in rows},
                           "precloseByDate": {row["date"]: rows[max(0, i - 1)]["close"] for i, row in enumerate(rows)}})
    return {"securities": securities, "calendar": [row["date"] for row in securities[0]["bars"]], "fingerprint": "fixture"}


def config(data, **extra):
    return {"startDate": data["calendar"][5], "endDate": data["calendar"][-1],
            "initialCapital": 10000, "topN": 1, "maxPositions": 1, "maxStockWeight": 1,
            "rebalanceMode": "rank", "rebalanceEvery": 1, "switchThreshold": 0,
            "weighting": "equal", "commissionRate": 0, "minCommission": 0, "transferFeeRate": 0,
            "stampDutyRate": 0, "slippageBps": 0, "maxParticipationRate": .1,
            "stopLossPct": 0, "trailingStopPct": 0, "takeProfitPct": 0, "maxHoldingDays": 0,
            "strategies": [{"id": "a", "kind": "momentum", "pool": "base"}],
            "allocation": {"mode": "single", "strategyIds": ["a"]},
            "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0}, **extra}


class FixtureScorer:
    def __init__(self, data, settings):
        self.data, self.settings = data, settings

    def daily(self, day):
        schedules = self.data.get("scoreSchedule", {})
        available = [key for key in schedules if key <= day]
        scores = schedules[max(available)] if available else {"a": [(SYMBOLS[0], 1.)]}
        return {"strategies": {key: [{"symbol": symbol, "rawScore": score * 100, "score": score,
                                      "eligible": True, "evidence": {"fixture": True}} for symbol, score in rows]
                               for key, rows in scores.items()},
                "coverage": {"complete": True}, "regime": "fixture"}


def run(data, settings=None, state=None):
    engine = PortfolioEngine(data, settings or config(data), state)
    engine.step(20000)
    return engine.result()


@patch("service.portfolio_engine.DailyScorer", FixtureScorer)
class PortfolioEngineTests(unittest.TestCase):
    def test_next_open_and_terminal_positions_are_not_liquidated(self):
        data = dataset()
        result = run(data)
        self.assertEqual(result["trades"][0]["signalDate"], data["calendar"][5])
        self.assertEqual(result["trades"][0]["date"], data["calendar"][6])
        self.assertEqual(result["trades"][0]["quantity"], 1000)
        self.assertTrue(all(trade["side"] == "buy" for trade in result["trades"]))
        self.assertEqual(result["positions"][0]["quantity"], 1000)
        self.assertEqual(result["metrics"]["finalEquity"], 10000)
        self.assertIsNone(result["metrics"]["sharpe"])
        self.assertFalse(result["quality"]["certified"])

    def test_top_n_is_per_strategy_and_equal_allocation_uses_shared_cash(self):
        data = dataset(symbols=SYMBOLS[:3])
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(SYMBOLS[0], 1), (SYMBOLS[2], .5)], "b": [(SYMBOLS[1], 1)]}}
        result = run(data, config(data, maxPositions=2, strategies=[{"id": "a", "kind": "momentum"}, {"id": "b", "kind": "forceGoldenCross"}],
                                  allocation={"mode": "equal", "strategyIds": ["a", "b"]}))
        self.assertEqual({p["symbol"]: p["quantity"] for p in result["positions"]}, {SYMBOLS[0]: 500, SYMBOLS[1]: 500})
        self.assertEqual(result["daily"][0]["strategyWeights"], {"a": .5, "b": .5})
        self.assertEqual(len(result["daily"][0]["topNByStrategy"]["a"]), 1)
        self.assertTrue(all(row["cash"] >= 0 for row in result["curve"]))

    def test_aggregate_position_cap_discards_weight_without_redistributing(self):
        data = dataset()
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(SYMBOLS[0], 1)], "b": [(SYMBOLS[1], 1)]}}
        result = run(data, config(data, strategies=[{"id": "a", "kind": "momentum"}, {"id": "b", "kind": "forceGoldenCross"}],
                                  allocation={"mode": "equal", "strategyIds": ["a", "b"]}))
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["positions"][0]["quantity"], 500)
        self.assertEqual(result["curve"][-1]["cash"], 5000)
        self.assertEqual(result["daily"][0]["clippedTargets"][0]["targetWeight"], .5)

    def test_rank_weighting_cap_leaves_unallocated_cash(self):
        data = dataset()
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(SYMBOLS[0], .75), (SYMBOLS[1], .25)]}}
        result = run(data, config(data, topN=2, maxPositions=2, weighting="rank", maxStockWeight=.5))
        self.assertEqual(result["daily"][0]["targetWeights"], {SYMBOLS[0]: .5, SYMBOLS[1]: .25})
        self.assertEqual(result["curve"][-1]["cash"], 3000)  # board lots leave another 500

    def test_stock_cap_does_not_erase_aggregate_strategy_budget_priority(self):
        data = dataset()
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(SYMBOLS[1], .6), (SYMBOLS[0], .4)]}}
        result = run(data, config(data, topN=2, maxPositions=1, weighting="rank", maxStockWeight=.2))
        self.assertEqual(result["daily"][0]["targetWeights"], {SYMBOLS[1]: .2})
        self.assertEqual(result["positions"][0]["symbol"], SYMBOLS[1])
        self.assertEqual(result["daily"][0]["clippedTargets"][0]["preCapWeight"], .4)
        self.assertEqual(result["curve"][-1]["cash"], 8000)

    def test_equal_budget_position_cap_preserves_normalized_score_order(self):
        data = dataset(symbols=SYMBOLS)
        ranked = [SYMBOLS[1], SYMBOLS[3], SYMBOLS[4], SYMBOLS[2], SYMBOLS[0]]
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(symbol, .9 - i * .1) for i, symbol in enumerate(ranked)]}}
        result = run(data, config(data, topN=5, maxPositions=3, maxStockWeight=.2))
        self.assertEqual(result["daily"][0]["targetPriority"], ranked[:3])
        self.assertEqual({p["symbol"] for p in result["positions"]}, set(ranked[:3]))
        self.assertEqual([row["symbol"] for row in result["daily"][0]["clippedTargets"]], ranked[3:])
        self.assertTrue(all(row["budgetWeight"] == .2 for row in result["daily"][0]["targetPriorityBySymbol"].values()))

    def test_risk_one_free_slot_uses_score_priority_after_equal_budget_cap(self):
        data = dataset(symbols=SYMBOLS[:3])
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], .5)]},
                                 days[6]: {"a": [(SYMBOLS[1], .9), (SYMBOLS[2], .6), (SYMBOLS[0], .2)]}}
        result = run(data, config(data, rebalanceMode="risk", topN=3, maxPositions=2, maxStockWeight=.5))
        self.assertEqual({p["symbol"] for p in result["positions"]}, {SYMBOLS[0], SYMBOLS[1]})
        self.assertEqual(next(t for t in result["trades"] if t["symbol"] != SYMBOLS[0])["symbol"], SYMBOLS[1])

    def test_partial_sale_cash_uses_frozen_priority_after_sorted_json_resume(self):
        data = dataset(symbols=SYMBOLS[:3])
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]},
                                 days[7]: {"a": [(SYMBOLS[1], .9), (SYMBOLS[2], .3)]}}
        data["securities"][0]["bars"][7]["volume"] = 5000  # next open can sell only half
        settings = config(data, topN=2, maxPositions=3, endDate=days[8])
        expected = run(data, settings)
        engine = PortfolioEngine(data, settings)
        engine.step(3)
        saved = json.loads(json.dumps(engine.checkpoint(), sort_keys=True, allow_nan=False))
        self.assertEqual(list(saved["pendingTarget"]["weights"]), [SYMBOLS[2], SYMBOLS[1]])
        restored = run(data, settings, saved)
        self.assertEqual(restored, expected)
        buys = [trade for trade in restored["trades"] if trade["date"] == days[8] and trade["side"] == "buy"]
        self.assertEqual([(trade["symbol"], trade["quantity"]) for trade in buys], [(SYMBOLS[1], 500)])
        self.assertEqual(restored["curve"][-1]["cash"], 0)
        del saved["pendingTarget"]["priorityBySymbol"][SYMBOLS[1]]
        with self.assertRaisesRegex(ValueError, "优先级"):
            PortfolioEngine(data, settings, saved)

    def test_switch_threshold_and_schedule_do_not_rotate_early(self):
        data = dataset(length=14)
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], .8), (SYMBOLS[1], .7)]},
                                 days[6]: {"a": [(SYMBOLS[1], .85), (SYMBOLS[0], .8)]},
                                 days[8]: {"a": [(SYMBOLS[1], .99), (SYMBOLS[0], .8)]}}
        result = run(data, config(data, rebalanceEvery=3, switchThreshold=.1))
        sale = next(trade for trade in result["trades"] if trade["side"] == "sell")
        self.assertEqual(sale["signalDate"], days[8])
        self.assertEqual(sale["date"], days[9])

    def test_risk_mode_holds_after_single_day_entry_signal_disappears(self):
        data = dataset()
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(SYMBOLS[0], 1)]}, data["calendar"][6]: {"a": []}}
        result = run(data, config(data, rebalanceMode="risk"))
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["positions"][0]["quantity"], 1000)

    def test_close_stop_next_open_ignores_intraday_threshold(self):
        data = dataset(prices=[[10] * 7 + [9.4, 9.5, 9.5, 9.5, 9.5], [10] * 12])
        rows, days = data["securities"][0]["bars"], data["calendar"]
        rows[6].update(low=8, high=11)  # neither intraday stop nor take executes
        rows[7].update(open=10, high=10)
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[7]: {"a": [(SYMBOLS[1], 1)]}}
        result = run(data, config(data, rebalanceMode="risk", stopLossPct=.05, takeProfitPct=.05))
        sale = next(trade for trade in result["trades"] if trade["side"] == "sell")
        self.assertEqual((sale["signalDate"], sale["date"], sale["price"], sale["reason"]), (days[7], days[8], 9.5, "stopLoss"))
        self.assertEqual(result["positions"][0]["symbol"], SYMBOLS[1])

    def test_failed_risk_sale_keeps_slot_and_retries_before_replacement(self):
        data = dataset(prices=[[10] * 7 + [9.4, 8.46, 8.5, 8.5, 8.5], [10] * 12])
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[7]: {"a": [(SYMBOLS[1], 1)]}}
        result = run(data, config(data, rebalanceMode="risk", stopLossPct=.05, maxStockWeight=.5))
        replacement = next(trade for trade in result["trades"] if trade["symbol"] == SYMBOLS[1])
        self.assertEqual(replacement["date"], days[9])
        self.assertTrue(any(row["date"] == days[8] and row["reason"] == "positionSlotsOccupied" for row in result["decisions"]))
        self.assertTrue(any(row["date"] == days[8] and row["reason"] == "openAtLowerLimitOrUncertainST" for row in result["decisions"]))
        self.assertLessEqual(max(len(row["holdings"]) for row in result["daily"]), 1)

    def test_take_profit_trailing_and_max_holding_use_daily_close(self):
        for risk, prices, expected_signal in (({"takeProfitPct": .05}, [10] * 7 + [10.6] * 5, 7),
                                              ({"trailingStopPct": .05}, [10] * 7 + [10.8, 10.2, 10.2, 10.2, 10.2], 8),
                                              ({"maxHoldingDays": 2}, [10] * 12, 7)):
            with self.subTest(risk=risk):
                data = dataset(prices=[prices, [10] * 12])
                result = run(data, config(data, rebalanceMode="risk", **risk))
                sale = next(trade for trade in result["trades"] if trade["side"] == "sell")
                self.assertEqual(sale["signalDate"], data["calendar"][expected_signal])
                self.assertEqual(sale["date"], data["calendar"][expected_signal + 1])

    def test_execution_close_and_current_volume_cannot_change_open_quantity(self):
        original = dataset()
        changed = deepcopy(original)
        changed["securities"][0]["bars"][6].update(close=10.8, high=10.8, volume=30_000_000)
        first = run(original)["trades"][0]
        second = run(changed)["trades"][0]
        self.assertEqual(first, second)

    def test_open_limit_rejects_despite_lower_intraday_prices(self):
        data = dataset()
        data["securities"][0]["bars"][6].update(open=11, high=11, low=10, close=10)
        result = run(data)
        self.assertFalse(any(trade["date"] == data["calendar"][6] for trade in result["trades"]))
        self.assertTrue(any(row["reason"] == "openAtUpperLimitOrUncertainST" for row in result["decisions"]))

    def test_previous_volume_capacity_and_multiple_partial_sells_preserve_shares(self):
        data = dataset(length=18)
        days = data["calendar"]
        for row in data["securities"][0]["bars"]:
            row["volume"] = 2000
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[10]: {"a": [(SYMBOLS[1], 1)]}}
        result = run(data)
        a_trades = [trade for trade in result["trades"] if trade["symbol"] == SYMBOLS[0]]
        bought = sum(trade["quantity"] for trade in a_trades if trade["side"] == "buy")
        sold = sum(trade["quantity"] for trade in a_trades if trade["side"] == "sell")
        held = sum(position["quantity"] for position in result["positions"] if position["symbol"] == SYMBOLS[0])
        self.assertEqual(bought - sold, held)
        self.assertTrue(all(trade["quantity"] <= 200 for trade in a_trades))
        self.assertEqual(held, 0)
        self.assertEqual(result["metrics"]["closedTradeCount"], 1)
        self.assertEqual(len([trade for trade in a_trades if trade["side"] == "sell"]), 5)

    def test_risk_partial_entry_retries_original_signal_after_event_disappears(self):
        data = dataset(length=15)
        days = data["calendar"]
        for row in data["securities"][0]["bars"]:
            row["volume"] = 2000
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[6]: {"a": []}}
        result = run(data, config(data, rebalanceMode="risk"))
        self.assertEqual(result["positions"][0]["quantity"], 1000)
        self.assertEqual(len(result["trades"]), 5)
        self.assertEqual({trade["signalDate"] for trade in result["trades"]}, {days[5]})
        self.assertFalse(any(row["status"] == "superseded" for row in result["decisions"]))

    def test_risk_entire_unfilled_entry_retries_after_event_disappears(self):
        data = dataset()
        days = data["calendar"]
        data["securities"][0]["bars"][6].update(open=11, high=11)
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[6]: {"a": []}}
        result = run(data, config(data, rebalanceMode="risk"))
        self.assertEqual(result["trades"][0]["date"], days[7])
        self.assertEqual(result["trades"][0]["signalDate"], days[5])

    def test_top_n_output_includes_unallocated_strategies_and_pre_threshold_rank(self):
        data = dataset()
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], .8), (SYMBOLS[1], .7)], "b": [(SYMBOLS[1], .5)]},
                                 days[6]: {"a": [(SYMBOLS[1], .85), (SYMBOLS[0], .8)], "b": [(SYMBOLS[1], .5)]}}
        result = run(data, config(data, switchThreshold=.1, strategies=[{"id": "a", "kind": "momentum"}, {"id": "b", "kind": "forceGoldenCross"}]))
        day = result["daily"][1]
        self.assertEqual(day["topNByStrategy"]["a"][0]["symbol"], SYMBOLS[1])
        self.assertEqual(day["selectedByStrategy"]["a"], [SYMBOLS[0]])
        self.assertIn("b", day["topNByStrategy"])

    def test_board_minimums_and_single_share_increments(self):
        self.assertEqual(lot_rule(SYMBOLS[3]), (200, 1))
        self.assertEqual(lot_rule(SYMBOLS[4]), (100, 1))
        for symbol, expected in ((SYMBOLS[0], 200), (SYMBOLS[3], 255), (SYMBOLS[4], 255)):
            data = dataset(symbols=[symbol])
            data["scoreSchedule"] = {data["calendar"][5]: {"a": [(symbol, 1)]}}
            result = run(data, config(data, initialCapital=2555))
            self.assertEqual(result["positions"][0]["quantity"], expected)

    def test_fees_stamp_history_and_cash_identity(self):
        data = dataset()
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[7]: {"a": [(SYMBOLS[1], 1)]}}
        result = run(data, config(data, commissionRate=.0003, minCommission=5, transferFeeRate=.00001, stampDutyRate=None, slippageBps=10))
        cash = 10000
        for trade in result["trades"]:
            tax = .0005 * trade["amount"] if trade["side"] == "sell" else 0
            self.assertAlmostEqual(trade["fee"], max(5, .0003 * trade["amount"]) + trade["amount"] * .00001 + tax)
            cash += trade["amount"] - trade["fee"] if trade["side"] == "sell" else -trade["amount"] - trade["fee"]
            self.assertAlmostEqual(cash, trade["cashAfter"])
            self.assertGreaterEqual(cash, 0)
        self.assertAlmostEqual(result["curve"][-1]["cash"], cash)
        self.assertAlmostEqual(result["metrics"]["finalEquity"], cash + sum(position["marketValue"] for position in result["positions"]))
        self.assertAlmostEqual(result["metrics"]["costRatioPct"], (result["metrics"]["totalFees"] + result["metrics"]["totalSlippage"]) / 100)
        engine = PortfolioEngine(data, config(data, commissionRate=0, minCommission=0, transferFeeRate=0, stampDutyRate=None))
        self.assertEqual(engine._fees(10000, "sell", "2023-08-25"), 10)
        self.assertEqual(engine._fees(10000, "sell", "2023-08-28"), 5)

    def test_resume_is_exact_and_changed_data_or_bad_state_is_rejected(self):
        data = dataset()
        settings = config(data, rebalanceMode="hybrid", maxHoldingDays=2)
        expected = run(data, settings)
        engine = PortfolioEngine(data, settings)
        engine.step(3)
        saved = json.loads(json.dumps(engine.checkpoint(), allow_nan=False))
        restored = PortfolioEngine(data, settings, saved)
        while not restored.step(2):
            restored = PortfolioEngine(data, settings, restored.checkpoint())
        self.assertEqual(expected, restored.result())
        changed = deepcopy(data)
        changed["securities"][0]["bars"][-1]["amount"] += 1
        with self.assertRaises(ValueError):
            PortfolioEngine(changed, settings, saved)
        saved["cash"] = float("nan")
        with self.assertRaises(ValueError):
            PortfolioEngine(data, settings, saved)

    def test_suspended_missing_and_unaccounted_actions_are_explicit(self):
        data = dataset()
        days = data["calendar"]
        security = data["securities"][0]
        security["bars"][7]["volume"] = 0
        security["tradeStatusByDate"][days[7]] = False
        security["precloseByDate"][days[9]] = 9.8
        result = run(data, config(data, rebalanceMode="risk"))
        self.assertGreater(result["quality"]["staleValuationDays"], 0)
        self.assertEqual(result["quality"]["unaccountedActionExposure"][0]["date"], days[9])
        self.assertFalse(result["curve"][-1]["valuationReliable"])
        self.assertEqual(result["curve"][-1]["cash"], 0)  # no invented dividend

    def test_history_rules_and_zero_return_metrics_are_honest(self):
        self.assertEqual(execution_limit({"symbol": SYMBOLS[0]}, "2024-01-02"), .05)
        self.assertEqual(execution_limit({"symbol": SYMBOLS[0]}, "2026-07-06"), .1)
        data = dataset()
        data["scoreSchedule"] = {data["calendar"][5]: {"a": []}}
        result = run(data)
        self.assertEqual(result["metrics"]["totalReturnPct"], 0)
        self.assertIsNone(result["metrics"]["winRatePct"])
        self.assertIsNone(result["metrics"]["sharpe"])
        json.dumps(result, allow_nan=False)

    def test_external_benchmark_uses_same_interval_closes_without_fabrication(self):
        data = dataset()
        index = bars([100 + i for i in range(12)])
        data["benchmark"] = {"symbol": "sh000300", "bars": index, "source": "fixture index"}
        result = run(data)
        expected = (111 / 105 - 1) * 100
        self.assertAlmostEqual(result["metrics"]["benchmarkReturnPct"], expected)
        self.assertAlmostEqual(result["metrics"]["excessReturnPct"], -expected)
        self.assertEqual(result["curve"][0]["benchmarkEquity"], 10000)
        self.assertIn("noncontiguous", result["regimeMetrics"][0]["definition"])
        data.pop("benchmark")
        result = run(data)
        self.assertIsNone(result["metrics"]["benchmarkReturnPct"])
        self.assertTrue(all(row["benchmarkEquity"] is None for row in result["curve"]))

    def test_more_than_120_securities_are_not_code_truncated(self):
        symbols = ["sh" + str(600000 + i) for i in range(161)]
        data = dataset(symbols=symbols, length=8)
        data["scoreSchedule"] = {data["calendar"][5]: {"a": [(symbols[-1], 1)]}}
        result = run(data)
        self.assertEqual(result["positions"][0]["symbol"], symbols[-1])

    def test_failed_day_rolls_back_ledger_before_retry(self):
        data = dataset()
        engine = PortfolioEngine(data, config(data))
        engine.step(1)
        saved = engine.checkpoint()
        with patch.object(engine.scorer, "daily", side_effect=ValueError("fixture failure")):
            with self.assertRaises(ValueError):
                engine.step(1)
        self.assertEqual(engine.checkpoint(), saved)
        engine.step(1)
        self.assertEqual(len(engine.state["trades"]), 1)

    def test_membership_loss_forces_exit_independently_of_entry_signal(self):
        data = dataset()
        days = data["calendar"]
        data["securities"][0]["memberHistory"] = {day: i < 7 for i, day in enumerate(days)}
        result = run(data, config(data, rebalanceMode="risk"))
        sale = next(trade for trade in result["trades"] if trade["side"] == "sell")
        self.assertEqual((sale["date"], sale["reason"]), (days[8], "baseEligibilityLost"))
        self.assertFalse(result["positions"])

    def test_execution_day_membership_loss_blocks_prior_close_buy(self):
        data = dataset()
        days = data["calendar"]
        data["membershipByDate"] = {day: [SYMBOLS[1]] for day in days[6:]}
        result = run(data, config(data, rebalanceMode="risk"))
        self.assertFalse(result["trades"])
        self.assertTrue(any(row["reason"] == "notHistoricalMember" for row in result["decisions"]))

    def test_t_plus_one_only_sells_older_lots(self):
        data = dataset()
        engine = PortfolioEngine(data, config(data))
        day = data["calendar"][7]
        engine.state["positions"][SYMBOLS[0]] = {
            "symbol": SYMBOLS[0], "quantity": 1000, "costBasis": 10000., "lots": [
                {"date": data["calendar"][6], "quantity": 500, "cost": 5000.},
                {"date": day, "quantity": 500, "cost": 5000.}],
            "entryDate": data["calendar"][6], "entryCursor": 1, "riskEntryPrice": 10., "closePeak": 10.,
            "realizedPnl": 0., "investedAmount": 10000.}
        engine._execute_order(day, SYMBOLS[0], "sell", 1000, {"signalDate": data["calendar"][6], "weights": {}})
        self.assertEqual(engine.state["trades"][0]["quantity"], 500)
        self.assertEqual(engine.state["positions"][SYMBOLS[0]]["quantity"], 500)
        self.assertFalse(engine.state["closedTrades"])


class RealScorerIntegrationTests(unittest.TestCase):
    def test_actual_daily_scorer_and_quality_contract(self):
        data = dataset(prices=[[10 + i * .02 for i in range(80)], [10] * 80])
        for security in data["securities"]:
            security["memberHistory"] = {day: True for day in data["calendar"]}
        settings = config(data, startDate=data["calendar"][60], rebalanceMode="risk", universe={"strictHistory": True, "excludeSt": True, "minListingDays": 0, "minAmount20": 0},
                          strategies=[{"id": "a", "kind": "momentum", "params": {"lookback": 5, "trendWindow": 10, "minReturn": 0}}])
        result = run(data, settings)
        self.assertTrue(result["trades"])
        self.assertEqual(result["daily"][0]["coverage"]["totalSecurities"], 2)
        self.assertEqual(result["quality"]["historicalStCoverage"], 1)
        self.assertFalse(result["quality"]["certified"])
        self.assertIn("evidence", result["daily"][0]["topNByStrategy"]["a"][0])


if __name__ == "__main__":
    unittest.main()
