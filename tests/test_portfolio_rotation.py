"""Causal, costed allocation and recovery regressions for the shared ledger."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from service.portfolio_config import normalize_config
from service.portfolio_engine import PortfolioEngine
from service.portfolio_rotation import decide_rotation
from test_portfolio_engine import SYMBOLS, FixtureScorer, config, dataset


def rotation_fixture(mode="hard_rotation", **extra):
    data = dataset(prices=[[10.] * 10 + [10 * 1.01 ** i for i in range(15)],
                           [10.] * 15 + [10 * 1.03 ** i for i in range(10)]])
    data["scoreSchedule"] = {data["calendar"][0]: {"a": [(SYMBOLS[0], 1)], "b": [(SYMBOLS[1], 1)]}}
    settings = config(data, maxPositions=2, initialCapital=100000, rebalanceMode="risk",
                      commissionRate=.0003, minCommission=5, stampDutyRate=.0005, slippageBps=10,
                      strategies=[{"id": "a", "kind": "momentum"}, {"id": "b", "kind": "momentum"}],
                      allocation={"mode": mode, "strategyIds": ["a", "b"]},
                      rotation={"lookbackDays": 3, "warmupDays": 0, "cooldownDays": 2, "minWeightChange": .1}, **extra)
    return data, settings


def finish(data, settings, state=None):
    engine = PortfolioEngine(data, settings, state)
    engine.step(20000)
    return engine.result()


class RotationDecisionTests(unittest.TestCase):
    def setUp(self):
        self.config = normalize_config({})["rotation"]
        self.config.update(lookbackDays=2, minWeightChange=.05, cooldownDays=3, regimeMinObservations=2)
        self.regime = {"status": "observed", "trend": "up", "volatility": "low"}

    def curves(self):
        return {"a": [{"date": f"2024-01-0{i + 1}", "dailyReturn": value, "regimeForReturn": self.regime}
                      for i, value in enumerate([0., .03, .03])],
                "b": [{"date": f"2024-01-0{i + 1}", "dailyReturn": value, "regimeForReturn": self.regime}
                      for i, value in enumerate([0., -.02, -.02])]}

    def test_soft_weights_are_normalized_and_use_net_portfolio_returns(self):
        result = decide_rotation("2024-01-03", 3, "soft_rotation", self.config, {"a": .5, "b": .5}, None, self.curves(), self.regime)
        self.assertEqual(result["status"], "adjusted")
        self.assertAlmostEqual(sum(result["after"].values()), 1)
        self.assertGreater(result["after"]["a"], .8)
        self.assertAlmostEqual(result["scores"]["a"]["recentNetReturn"], 1.03 ** 2 - 1)
        self.assertEqual(result["scores"]["a"]["sameRegimeObservations"], 3)

    def test_missing_history_cooldown_and_threshold_hold_without_loggable_change(self):
        for day, last, threshold, reason in (("2024-01-02", None, .05, "insufficientShadowHistory"),
                                             ("2024-01-03", 2, .05, "cooldown"),
                                             ("2024-01-03", None, 1, "belowWeightThreshold")):
            settings = {**self.config, "minWeightChange": threshold}
            result = decide_rotation(day, 3, "soft_rotation", settings, {"a": .5, "b": .5}, last, self.curves(), self.regime)
            self.assertEqual(result["reason"], reason)
            self.assertEqual(result["after"], {"a": .5, "b": .5})

    def test_future_curves_are_ignored_and_unknown_environment_does_not_invent_history(self):
        histories = self.curves()
        expected = decide_rotation("2024-01-03", 3, "soft_rotation", self.config, {"a": .5, "b": .5}, None, histories, None)
        histories["b"].append({"date": "2024-01-04", "dailyReturn": 100., "regimeForReturn": self.regime})
        actual = decide_rotation("2024-01-03", 3, "soft_rotation", self.config, {"a": .5, "b": .5}, None, histories, None)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["scores"]["a"]["regimeBlendApplied"], 0.)

    def test_regime_matching_uses_state_attached_to_historical_return(self):
        histories = self.curves()
        histories["a"][0]["regimeForReturn"] = {"status": "unknown"}
        histories["a"][1]["regimeForReturn"] = {"status": "observed", "trend": "down", "volatility": "high"}
        actual = decide_rotation("2024-01-03", 3, "soft_rotation", self.config, {"a": .5, "b": .5}, None, histories, self.regime)
        self.assertEqual(actual["scores"]["a"]["sameRegimeObservations"], 1)
        self.assertEqual(actual["scores"]["a"]["regimeBlendApplied"], 0.)

    def test_unreliable_recent_valuation_freezes_weights(self):
        histories = self.curves()
        histories["a"][-1]["valuationReliable"] = False
        result = decide_rotation("2024-01-03", 3, "soft_rotation", self.config, {"a": .5, "b": .5}, None, histories, self.regime)
        self.assertEqual(result["reason"], "unreliableShadowValuation")
        self.assertEqual(result["after"], {"a": .5, "b": .5})

    def test_bad_rotation_configuration_is_rejected(self):
        for bad in ({"temperature": 0}, {"warmupDays": -1}, {"warmupDays": 501}, {"lookbackDays": True}, {"minWeightChange": 1.1}, {"cooldownDays": .5}, {"unknown": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_config({"rotation": bad})
        with self.assertRaises(ValueError):
            normalize_config({"allocation": {"mode": "soft_rotation", "strategyIds": ["momentum"]}})


@patch("service.portfolio_engine.DailyScorer", FixtureScorer)
class RotationLedgerTests(unittest.TestCase):
    def test_risk_mode_rotation_changes_actual_holdings_next_open_and_charges_all_costs(self):
        data, settings = rotation_fixture()
        result = finish(data, settings)
        self.assertGreaterEqual(len(result["rotationLog"]), 2)
        rotated = [trade for trade in result["trades"] if trade["reason"] == "strategyRotation"]
        self.assertTrue(any(trade["side"] == "sell" for trade in rotated))
        self.assertTrue(any(trade["side"] == "buy" for trade in rotated))
        for trade in rotated:
            self.assertEqual(data["calendar"].index(trade["date"]), data["calendar"].index(trade["signalDate"]) + 1)
            self.assertGreater(trade["fee"], 0)
            self.assertGreater(trade["slippage"], 0)
        self.assertAlmostEqual(result["metrics"]["totalFees"], sum(t["fee"] for t in result["trades"]))
        self.assertAlmostEqual(result["metrics"]["totalSlippage"], sum(t["slippage"] for t in result["trades"]))
        self.assertEqual([p["symbol"] for p in result["positions"]], [SYMBOLS[1]])
        self.assertTrue(result["rotation"]["diagnosticOnly"])
        self.assertFalse(result["quality"]["certified"])
        self.assertTrue(all(row["cash"] >= 0 for row in result["curve"]))

    def test_soft_rotation_preserves_multiple_positive_strategy_budgets(self):
        data, settings = rotation_fixture("soft_rotation")
        result = finish(data, settings)
        self.assertTrue(result["rotationLog"])
        for row in result["daily"]:
            self.assertAlmostEqual(sum(row["strategyWeights"].values()), 1)
            self.assertTrue(all(0 < weight < 1 for weight in row["strategyWeights"].values()))
        self.assertTrue(any(t["side"] == "sell" and t["reason"] == "strategyRotation" for t in result["trades"]))

    def test_checkpoint_resume_matches_uninterrupted_and_shares_data_and_scorer(self):
        data, settings = rotation_fixture("soft_rotation")
        engine = PortfolioEngine(data, settings)
        self.assertTrue(all(shadow.scorer is engine.scorer and shadow.bars is engine.bars for shadow in engine.shadows.values()))
        engine.step(10)
        checkpoint = json.loads(json.dumps(engine.checkpoint()))
        self.assertEqual(finish(data, settings, checkpoint), finish(data, settings))

    def test_failed_parent_step_atomically_restores_all_shadow_ledgers(self):
        data, settings = rotation_fixture("soft_rotation")
        engine = PortfolioEngine(data, settings)
        engine.step(9)
        before = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                engine.step(1)
        self.assertEqual(engine.checkpoint(), before)
        engine.step(20000)
        self.assertEqual(engine.result(), finish(data, settings))

    def test_warmup_does_not_seed_actual_out_of_sample_positions_or_trades(self):
        data, settings = rotation_fixture()
        settings["startDate"] = data["calendar"][18]
        settings["rotation"]["warmupDays"] = 12
        engine = PortfolioEngine(data, settings)
        engine.step(1)
        self.assertEqual(engine.state["cursor"], 0)
        self.assertEqual(engine.progress["phase"], "warmingShadowAccounts")
        self.assertEqual(engine.progress["completedWarmupDays"], 1)
        engine = PortfolioEngine(data, settings, json.loads(json.dumps(engine.checkpoint())))
        while engine.state["cursor"] == 0:
            engine.step(1)
        self.assertEqual(engine.state["positions"], {})
        self.assertEqual(engine.state["trades"], [])
        self.assertEqual(engine.state["cash"], settings["initialCapital"])
        self.assertTrue(all(shadow.state["trades"] for shadow in engine.shadows.values()))
        self.assertTrue(engine.state["rotationLog"])
        self.assertEqual(engine.state["rotationLog"][0]["date"], settings["startDate"])

    def test_shadow_is_whole_top_n_portfolio_not_its_first_pick(self):
        data, settings = rotation_fixture(topN=2)
        data["scoreSchedule"][data["calendar"][0]]["a"].append((SYMBOLS[1], .8))
        engine = PortfolioEngine(data, settings)
        engine.step(20000)
        shadow = engine.shadows["a"]
        single = deepcopy(settings)
        single["allocation"] = {"mode": "single", "strategyIds": ["a"]}
        direct = finish(data, single)
        self.assertEqual(shadow.result()["metrics"], direct["metrics"])
        self.assertEqual(len(shadow.state["daily"][1]["holdings"]), 2)

    def test_future_prices_do_not_change_prior_weights_or_executions(self):
        data, settings = rotation_fixture("soft_rotation")
        expected = finish(data, settings)
        changed = deepcopy(data)
        boundary = data["calendar"][18]
        for security in changed["securities"]:
            for row in security["bars"]:
                if row["date"] > boundary:
                    for key in ("open", "close", "high", "low"):
                        row[key] *= 1.03
        actual = finish(changed, settings)
        self.assertEqual([row for row in expected["daily"] if row["date"] <= boundary], [row for row in actual["daily"] if row["date"] <= boundary])
        self.assertEqual([row for row in expected["trades"] if row["date"] <= boundary], [row for row in actual["trades"] if row["date"] <= boundary])

    def test_rotation_orders_retry_after_suspension_in_risk_mode(self):
        data, settings = rotation_fixture()
        baseline = finish(data, settings)
        change = baseline["rotationLog"][1]
        day_index = data["calendar"].index(change["date"]) + 1
        blocked_day = data["calendar"][day_index]
        data["securities"][0]["tradeStatusByDate"][blocked_day] = False
        result = finish(data, settings)
        sales = [t for t in result["trades"] if t["symbol"] == SYMBOLS[0] and t["side"] == "sell"]
        self.assertTrue(sales)
        self.assertEqual(sales[-1]["date"], data["calendar"][day_index + 1])
        self.assertEqual(sales[-1]["signalDate"], change["date"])

    def test_risk_exit_cancels_old_rotation_entry_during_another_blocked_sale(self):
        data, settings = rotation_fixture()
        settings["stopLossPct"] = .01
        settings["rotation"]["cooldownDays"] = 100
        for index, row in enumerate(data["securities"][0]["bars"]):
            price = 10. if index < 9 else 9.5
            row.update(open=price, close=price, high=price, low=price)
            data["securities"][0]["precloseByDate"][row["date"]] = 10. if index <= 9 else 9.5
        data["securities"][1]["tradeStatusByDate"][data["calendar"][9]] = False
        result = finish(data, settings)
        sells = [row for row in result["trades"] if row["symbol"] == SYMBOLS[0] and row["reason"] == "stopLoss"]
        self.assertTrue(sells)
        day = sells[0]["date"]
        self.assertEqual(day, data["calendar"][10])
        self.assertFalse(any(row["symbol"] == SYMBOLS[0] and row["side"] == "buy" and row["date"] == day for row in result["trades"]))
        self.assertFalse(any(row["symbol"] == SYMBOLS[0] and row["side"] == "buy" and row["reason"] == "strategyRotation" and row["date"] >= day for row in result["trades"]))

    def test_regime_and_benchmark_metrics_are_complete_and_no_index_is_fabricated(self):
        data, settings = rotation_fixture()
        absent = finish(data, settings)
        self.assertIsNone(absent["benchmarkMetrics"])
        data["benchmark"] = {"symbol": "sh000300", "bars": deepcopy(data["securities"][0]["bars"])}
        result = finish(data, settings)
        for key in ("annualizedReturnPct", "maxDrawdownPct", "sharpe", "winRatePct", "turnoverPct", "costRatioPct"):
            self.assertIn(key, result["regimeMetrics"][0])
        self.assertIn("dailyWinRatePct", result["benchmarkMetrics"])
        self.assertIsNone(result["benchmarkMetrics"]["winRatePct"])
        self.assertIsNone(result["benchmarkMetrics"]["costRatioPct"])
        data["benchmark"]["bars"].pop(10)
        self.assertIsNone(finish(data, settings)["benchmarkMetrics"])


if __name__ == "__main__":
    unittest.main()
