"""Causal day explanations must describe the ledger without changing execution."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from service.portfolio_engine import PortfolioEngine
from service.portfolio_config import normalize_config
from service.portfolio_continuous import ContinuousRunner
from test_portfolio_engine import FixtureScorer, SYMBOLS, config, dataset, run


def day_row(result, day):
    return next(row for row in result["daily"] if row["date"] == day)


def plan(result, day, symbol=SYMBOLS[0]):
    return next(row for row in day_row(result, day)["nextSessionPlan"]["rows"] if row["symbol"] == symbol)


@patch("service.portfolio_engine.DailyScorer", FixtureScorer)
class DailyExplanationTests(unittest.TestCase):
    def test_close_budget_is_not_a_fabricated_opening_quantity_or_trade(self):
        data = dataset()
        result = run(data, config(data, rebalanceEvery=3))
        first = day_row(result, data["calendar"][5])
        row = first["nextSessionPlan"]["rows"][0]
        self.assertEqual((row["action"], row["status"], row["quantityBasis"]), ("open", "new", "nextOpen"))
        self.assertEqual((row["targetWeight"], row["targetBudget"], row["currentQuantity"]), (1, 10000, 0))
        self.assertIsNone(row["targetQuantity"])
        self.assertIsNone(row["quantityDelta"])
        self.assertTrue(row["directionEstimated"])
        self.assertTrue(first["nextSessionPlan"]["hasPendingOrders"])
        self.assertEqual(first["fills"], [])
        self.assertEqual(first["positionControl"]["nextScheduledRebalanceDate"], data["calendar"][8])
        self.assertEqual(first["positionControl"]["nextSessionDate"], data["calendar"][6])

    def test_partial_entry_remains_carried_after_signal_disappears_until_complete(self):
        data = dataset(length=15)
        days = data["calendar"]
        for bar in data["securities"][0]["bars"]:
            bar["volume"] = 2000
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[6]: {"a": []}}
        result = run(data, config(data, rebalanceMode="risk"))
        carried = plan(result, days[6])
        self.assertEqual((carried["action"], carried["status"], carried["executionStatus"]), ("add", "carried", "partial"))
        self.assertEqual((carried["currentQuantity"], carried["targetQuantity"], carried["quantityDelta"]), (200, 1000, 800))
        self.assertEqual(carried["signalDate"], days[5])
        self.assertEqual(carried["lastAttemptDate"], days[6])
        self.assertFalse(carried["directionEstimated"])
        self.assertTrue(carried["pending"])
        completed = plan(result, days[10])
        self.assertEqual((completed["action"], completed["status"], completed["quantityDelta"]), ("hold", "completed", 0))
        self.assertFalse(completed["pending"])
        self.assertEqual([row["quantityAfter"] for row in result["trades"]], [200, 400, 600, 800, 1000])
        self.assertEqual([row["positionAction"] for row in result["trades"]], ["open", "add", "add", "add", "add"])
        self.assertEqual(result["trades"][0]["unfilledQuantity"], 800)

    def test_forced_exit_owns_plan_and_preserves_original_risk_evidence_while_blocked(self):
        data = dataset(prices=[[10] * 7 + [9.4, 8.46, 8.5, 8.5, 8.5], [10] * 12])
        days = data["calendar"]
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[7]: {"a": [(SYMBOLS[1], 1)]}}
        result = run(data, config(data, rebalanceMode="risk", stopLossPct=.05, maxStockWeight=.5))
        fresh, deferred = plan(result, days[7]), plan(result, days[8])
        self.assertEqual((deferred["action"], deferred["targetQuantity"], deferred["targetWeight"]), ("exit", 0, 0))
        self.assertEqual((deferred["status"], deferred["executionStatus"]), ("carried", "deferred"))
        self.assertEqual(deferred["lastBlockReason"], "openAtLowerLimitOrUncertainST")
        self.assertEqual(deferred["signalDate"], days[7])
        evidence = fresh["riskEvidence"]
        self.assertEqual(evidence, deferred["riskEvidence"])
        self.assertEqual(evidence["observedDate"], days[7])
        self.assertEqual(evidence["observedClose"], 9.4)
        self.assertAlmostEqual(evidence["gainPct"], -.06)
        self.assertEqual(evidence["stopLossPct"], .05)
        fill = next(row for row in result["trades"] if row["reason"] == "stopLoss")
        self.assertEqual(fill["riskEvidence"], evidence)
        self.assertEqual((fill["quantityBefore"], fill["quantityAfter"], fill["positionAction"]), (500, 0, "exit"))
        queued = next(row for row in result["decisions"] if row["status"] == "queued" and row["reason"] == "stopLoss")
        self.assertEqual(queued["riskEvidence"], evidence)

    def test_risk_fixed_shares_do_not_turn_into_rebalance_from_price_weight_drift(self):
        data = dataset(prices=[[10] * 7 + [10.8] * 5, [10] * 12])
        days = data["calendar"]
        data["securities"][0]["bars"][7].update(open=10, low=10)
        result = run(data, config(data, rebalanceMode="risk", maxStockWeight=.5))
        row = plan(result, days[7])
        self.assertEqual((row["action"], row["targetQuantity"], row["quantityDelta"]), ("hold", 500, 0))
        self.assertGreater(row["currentWeight"], .5)
        self.assertFalse(row["pending"])
        control = day_row(result, days[7])["positionControl"]
        self.assertEqual(control["maxStockWeightMeaning"], "newTargetWeightCapNotHardHoldingLimit")
        self.assertEqual((control["equity"], control["stockMarketValue"], control["availableCash"]), (10400, 5400, 5000))
        ranked = run(data, config(data, rebalanceMode="rank", maxStockWeight=.5))
        estimated = plan(ranked, days[7])
        self.assertEqual(estimated["action"], "reduce")
        self.assertTrue(estimated["directionEstimated"])
        self.assertIsNone(estimated["quantityDelta"])

    def test_each_fill_has_its_own_before_after_shares_including_partial_reductions(self):
        data = dataset(length=18)
        days = data["calendar"]
        for bar in data["securities"][0]["bars"]:
            bar["volume"] = 2000
        data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)]}, days[10]: {"a": [(SYMBOLS[1], 1)]}}
        result = run(data)
        held, actions = {}, set()
        for fill in result["trades"]:
            symbol = fill["symbol"]
            self.assertEqual(fill["quantityBefore"], held.get(symbol, 0))
            held[symbol] = held.get(symbol, 0) + (fill["quantity"] if fill["side"] == "buy" else -fill["quantity"])
            self.assertEqual(fill["quantityAfter"], held[symbol])
            actions.add(fill["positionAction"])
        self.assertEqual(actions, {"open", "add", "reduce", "exit"})

    def test_close_holdings_cost_pnl_and_price_age_are_observed_facts(self):
        data = dataset()
        days = data["calendar"]
        data["securities"][0]["bars"][7].update(open=10.5, close=10.5, high=10.5, low=10.5)
        data["securities"][0]["bars"].pop(8)
        result = run(data, config(data, rebalanceMode="risk", maxStockWeight=.5, minCommission=5))
        filled = result["trades"][0]
        holding = day_row(result, days[7])["holdings"][0]
        self.assertEqual(holding["costPrice"], (filled["amount"] + filled["fee"]) / filled["quantity"])
        self.assertAlmostEqual(holding["unrealizedPnl"], holding["marketValue"] - holding["costBasis"])
        self.assertAlmostEqual(holding["unrealizedPnlPct"], holding["unrealizedPnl"] / holding["costBasis"] * 100)
        self.assertEqual(holding["holdingDays"], 2)
        stale = day_row(result, days[8])
        self.assertEqual(stale["holdings"][0]["priceDate"], days[7])
        self.assertEqual(plan(result, days[8])["valuationBasis"], "lastAvailable")
        self.assertFalse(stale["positionControl"]["valuationReliable"])

    def test_next_session_dates_require_calendar_evidence_and_never_assume_weekdays(self):
        data = dataset()
        days = data["calendar"]
        short = run(data, config(data, endDate=days[7]))
        last = short["daily"][-1]
        self.assertEqual(last["nextSessionPlan"]["nextSessionDate"], days[8])
        self.assertFalse(last["nextSessionPlan"]["nextSessionInBacktest"])
        complete = run(data)
        last = complete["daily"][-1]
        self.assertIsNone(last["nextSessionPlan"]["nextSessionDate"])
        self.assertFalse(last["nextSessionPlan"]["nextSessionInBacktest"])
        self.assertNotIn("nextSessionSellableQuantity", last["holdings"][0])

    def test_zero_lot_target_does_not_claim_an_outstanding_buy_or_hide_its_budget(self):
        data = dataset()
        result = run(data, config(data, maxStockWeight=.01, rebalanceEvery=20))
        row = plan(result, data["calendar"][6])
        self.assertEqual((row["currentQuantity"], row["targetQuantity"], row["targetBudget"]), (0, 0, 100))
        self.assertEqual((row["action"], row["status"]), ("hold", "completed"))
        self.assertFalse(row["pending"])
        self.assertEqual(result["trades"], [])

    def test_legacy_checkpoint_keeps_unknown_old_day_details_without_reconstruction(self):
        data = dataset()
        cfg = config(data, rebalanceMode="risk")
        engine = PortfolioEngine(data, cfg)
        engine.step(3)
        saved = deepcopy(engine.checkpoint())
        for day in saved["daily"]:
            for key in ("nextSessionPlan", "positionControl", "corporateActions"):
                day.pop(key, None)
            for holding in day["holdings"]:
                for key in set(holding) - {"symbol", "quantity", "costBasis", "marketValue", "weight"}:
                    holding.pop(key)
            for fill in day["fills"]:
                for key in ("quantityBefore", "quantityAfter", "positionAction", "unfilledQuantity"):
                    fill.pop(key, None)
        for fill in saved["trades"]:
            for key in ("quantityBefore", "quantityAfter", "positionAction", "unfilledQuantity"):
                fill.pop(key, None)
        resumed = run(data, cfg, saved)
        for day in resumed["daily"][:3]:
            self.assertNotIn("nextSessionPlan", day)
        self.assertIn("nextSessionPlan", resumed["daily"][3])
        expected = run(data, cfg)
        for key in ("metrics", "curve", "positions", "pendingTarget"):
            self.assertEqual(resumed[key], expected[key])

    def test_bonus_shares_are_actions_not_fills_and_locked_lots_remain_locked(self):
        data = dataset()
        days = data["calendar"]
        security = data["securities"][0]
        security["corporateActions"] = [{"id": "bonus", "announcementDate": days[2], "recordDate": days[7],
            "exDate": days[8], "bonusSharesPerShare": 1, "shareListingDate": days[10], "source": "fixture"}]
        for bar in security["bars"][8:]:
            bar.update(open=5, high=5, low=5, close=5)
            security["precloseByDate"][bar["date"]] = 5
        result = run(data, config(data, rebalanceMode="risk"))
        ex_day = day_row(result, days[8])
        self.assertEqual(ex_day["fills"], [])
        self.assertEqual(ex_day["holdings"][0]["quantity"], 2000)
        self.assertEqual(ex_day["holdings"][0]["nextSessionSellableQuantity"], 1000)
        self.assertEqual(day_row(result, days[9])["holdings"][0]["nextSessionSellableQuantity"], 2000)
        self.assertEqual(next(row for row in ex_day["corporateActions"] if row["stage"] == "ex")["bonusShares"], 1000)
        self.assertEqual(plan(result, days[8])["action"], "hold")

    def test_cash_control_distinguishes_reserved_cash_from_receivables(self):
        data = dataset()
        days = data["calendar"]
        security = data["securities"][0]
        security["corporateActions"] = [{"id": "rights", "announcementDate": days[2], "recordDate": days[7],
            "exDate": days[8], "cashNetPerShare": .5, "cashPayDate": days[10],
            "rightsSharesPerShare": .5, "rightsPrice": 2, "rightsPayDate": days[10],
            "shareListingDate": days[10], "source": "fixture"}]
        for bar in security["bars"][8:]:
            mark = 7
            bar.update(open=mark, high=mark, low=mark, close=mark)
            security["precloseByDate"][bar["date"]] = mark
        result = run(data, config(data, rebalanceMode="risk", maxStockWeight=.5, rightsPolicy="subscribe"))
        control = day_row(result, days[8])["positionControl"]
        self.assertEqual((control["cash"], control["reservedActionCash"], control["availableCash"]), (5000, 500, 4500))
        self.assertEqual((control["receivables"], control["payables"]), (250, 500))
        self.assertEqual(control["equity"], control["cash"] + control["stockMarketValue"] + control["receivables"] - control["payables"] + control["preExRightsValue"])

    def test_future_quotes_do_not_change_prior_close_plan_and_reporting_is_read_only(self):
        data = dataset(length=18)
        days = data["calendar"]
        for bar in data["securities"][0]["bars"]:
            bar["volume"] = 2000
        cfg = config(data, rebalanceMode="risk")
        full = run(data, cfg)
        changed = deepcopy(data)
        changed["securities"][0]["bars"][8].update(open=10.8, high=10.8, close=10.8)
        self.assertEqual(day_row(full, days[7])["nextSessionPlan"], day_row(run(changed, cfg), days[7])["nextSessionPlan"])
        with patch.object(PortfolioEngine, "_next_session_plan", return_value={}), patch.object(PortfolioEngine, "_position_control", return_value={}):
            plain = run(data, cfg)
        for key in ("trades", "closedTrades", "curve", "positions", "pendingTarget", "forcedExits", "metrics", "actionLedger"):
            self.assertEqual(full[key], plain[key], key)
        engine = PortfolioEngine(data, cfg)
        engine.step(3)
        saved = json.loads(json.dumps(engine.checkpoint(), sort_keys=True, allow_nan=False))
        self.assertEqual(run(data, cfg, saved), full)

    def test_continuous_policy_handoff_refreshes_prior_close_snapshot_and_keeps_risk_evidence(self):
        for stop_triggered in (False, True):
            with self.subTest(stop_triggered=stop_triggered):
                data = dataset(prices=[[10] * 7 + [9.4 if stop_triggered else 10] * 5, [10] * 12])
                days = data["calendar"]
                data["scoreSchedule"] = {days[5]: {"a": [(SYMBOLS[0], 1)], "b": [(SYMBOLS[1], 1)]}}
                definitions = [{"id": "a", "kind": "momentum"}, {"id": "b", "kind": "momentum"}]
                first = normalize_config(config(data, strategies=definitions, rebalanceMode="risk", stopLossPct=.05))
                second = normalize_config(config(data, strategies=definitions, rebalanceMode="risk", stopLossPct=.2,
                    allocation={"mode": "single", "strategyIds": ["b"]}, maxStockWeight=.5, rebalanceEvery=3))
                folds = [{"testStart": days[5], "testEnd": days[7], "selection": {"candidateId": "first"},
                          "training": {"first": {"selectedStrategyId": "a"}}, "persistence": {"passed": False}},
                         {"testStart": days[8], "testEnd": days[-1], "selection": {"candidateId": "second"},
                          "training": {"second": {"selectedStrategyId": "b"}}, "persistence": {"passed": False}}]
                runner = ContinuousRunner(data, first, [{"candidateId": "first", "config": first},
                                          {"candidateId": "second", "config": second}], folds)
                runner.step(20)
                carried = deepcopy(runner.state["carry"])
                self.assertIsNotNone(carried)
                runner.step(20)
                current = runner.engine.state
                last = current["daily"][-1]
                rows = {row["symbol"]: row for row in last["nextSessionPlan"]["rows"]}
                self.assertEqual(rows[SYMBOLS[0]]["action"], "exit")
                self.assertEqual(rows[SYMBOLS[1]]["action"], "open")
                self.assertEqual(rows[SYMBOLS[1]]["reason"], "walkForwardRetrain")
                self.assertEqual(rows[SYMBOLS[1]]["targetBudget"], current["pendingTarget"]["budgets"][SYMBOLS[1]])
                self.assertEqual(last["targetPriority"], [SYMBOLS[1]])
                self.assertEqual(last["selectedByStrategy"], {"b": [SYMBOLS[1]]})
                self.assertEqual(last["positionControl"]["maxStockWeight"], .5)
                self.assertEqual(last["positionControl"]["nextScheduledRebalanceDate"], days[8])
                self.assertEqual(last["nextSessionPlan"]["nextSessionDate"], days[8])
                self.assertTrue(last["nextSessionPlan"]["nextSessionInBacktest"])
                for key in ("cash", "positions", "trades", "curve", "forcedExits"):
                    self.assertEqual(current[key], carried[key], key)
                if stop_triggered:
                    self.assertEqual(rows[SYMBOLS[0]]["reason"], "stopLoss")
                    self.assertEqual(rows[SYMBOLS[0]]["riskEvidence"], carried["forcedExits"][SYMBOLS[0]]["riskEvidence"])
                    self.assertEqual(rows[SYMBOLS[0]]["riskEvidence"]["stopLossPct"], .05)


if __name__ == "__main__":
    unittest.main()
