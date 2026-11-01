"""Full synthetic event/order paths and static-copy equivalence, no market fit."""
import ast
from copy import deepcopy
import inspect
import textwrap
import unittest
from unittest.mock import patch

from service.portfolio_engine import PortfolioEngine
from service.research_account_fees import LegacyFeePolicy
from service.research_action_account import ActionAwareVolatilityAccount
from service.research_action_account_day import action_step_day
from service.research_volatility_trail import VolatilityTrailAccount
from tests.test_research_volatility_trail import fixture, schedule, configuration, SYMBOL


def case(*, cash="0", ratio="1", ex=30, listing=None, pay=None, fraction="exact", tax="0"):
    data = fixture()
    days = data["calendar"]
    data["researchActionCoverage"] = {"scope": "synthetic-fixture", "complete": True}
    data["researchActionPolicy"] = {"fractionPolicy": fraction, "taxReserveRate": tax, "cashCredit": "afterPayDateOpen"}
    data["researchDistributions"] = [{"id": "synthetic-ex", "symbol": SYMBOL,
        "publicDate": days[20], "recordDate": days[ex - 1], "exDate": days[ex],
        "cashPayDate": (days[pay if pay is not None else ex] if float(cash) else None),
        "shareListingDate": (days[listing if listing is not None else ex] if float(ratio) else None),
        "cashGrossPerShare": cash, "additionalSharesPerShare": ratio,
        "referenceCashPerShare": cash, "referenceAdditionalSharesPerShare": ratio,
        "sourceSha256": "b" * 64, "sourceUrl": "https://example.test/synthetic-source.pdf"}]
    reference = round((10 - float(cash)) / (1 + float(ratio)) + 1e-10, 2)
    for row in data["securities"][0]["bars"][ex:]:
        row.update(open=reference, close=reference, high=reference * 1.005, low=reference * .995)
    return data


def engine(data, signals=None):
    return ActionAwareVolatilityAccount(data, schedule(data) if signals is None else signals,
        configured(data), entry_weight=.2, fee_policy=LegacyFeePolicy())


def configured(data):
    return configuration(data, commissionRate=.001, minCommission=0, transferFeeRate=0, stampDutyRate=0)


def finish(account):
    while account.state["cursor"] < len(account.dates):
        account.step(7)
    return account.result()


class ActionAccountTests(unittest.TestCase):
    def test_daily_flow_copy_changes_only_four_declared_action_seams(self):
        old = textwrap.dedent(inspect.getsource(PortfolioEngine._step_day))
        expected = old.replace("def _step_day(self, day):", "def action_step_day(self, day):")
        expected = expected.replace("apply_actions(self, day)", "self._apply_before_open(day)")
        expected = expected.replace("asset_adjustment(s)", "self._action_asset_adjustment()")
        expected = expected.replace("capture_rights(self, day)", "self._capture_record_close(day)")
        expected = expected.replace("self._reference(symbol, index)", "self._continuity_reference(symbol, index)")
        self.assertEqual(ast.dump(ast.parse(expected)), ast.dump(ast.parse(inspect.getsource(action_step_day))))

    def test_frozen_volatility_and_order_rule_methods_are_unchanged(self):
        for name in ("_execute_order", "_risk_exits", "_queue_trail", "_transaction_snapshot", "_transaction_restore"):
            self.assertEqual(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(getattr(VolatilityTrailAccount, name))))),
                             ast.dump(ast.parse(textwrap.dedent(inspect.getsource(getattr(ActionAwareVolatilityAccount, name))))))

    def test_legacy_constructor_rejection_is_retained_and_not_mutated(self):
        data = case()
        data["securities"][0]["corporateActions"] = [{"id": "legacy"}]
        saved = deepcopy(data)
        for cls in (VolatilityTrailAccount, ActionAwareVolatilityAccount):
            with self.assertRaises(ValueError):
                cls(data, schedule(data), configured(data), entry_weight=.2, fee_policy=LegacyFeePolicy())
        self.assertEqual(data, saved)

    def test_market_input_is_rejected_until_source_and_features_are_ready(self):
        data = case()
        for field, value in (("calendarSource", "sse-szse-real"),
                             ("researchActionCoverage", {"scope": "current-provider", "complete": True})):
            wrong = deepcopy(data)
            wrong[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                engine(wrong)

    def test_no_action_account_matches_old_frozen_raw_rule(self):
        data = case()
        data["researchDistributions"] = []
        # Restore a flat raw series, rather than silently explaining a split.
        for row in data["securities"][0]["bars"]:
            row.update(open=10., close=10., high=10.05, low=9.95)
        old = VolatilityTrailAccount(data, schedule(data), configured(data), entry_weight=.2, fee_policy=LegacyFeePolicy())
        new = engine(data)
        a, b = finish(old), finish(new)
        for key in ("curve", "trades", "closedTrades", "forcedExits"):
            self.assertEqual(a[key], b[key])
        self.assertEqual(a["research"]["volatilityTrail"]["observations"], b["research"]["volatilityTrail"]["observations"])

    def test_split_updates_held_shares_risk_and_mark_once(self):
        data = case()
        saved = deepcopy(data)
        account = engine(data)
        result = finish(account)
        self.assertEqual(data, saved)
        position = account.state["positions"][SYMBOL]
        # 20% of50000 /10 =>1000 shares planned; fee budget gives900.
        self.assertEqual(result["trades"][0]["quantity"], 900)
        self.assertEqual(position["quantity"], 1800)
        self.assertEqual(position["riskEntryPrice"], 5)
        self.assertEqual(position["closePeak"], 5)
        self.assertAlmostEqual(position["costBasis"], 9009)
        self.assertAlmostEqual(result["curve"][-1]["equity"], 49991)
        self.assertEqual(result["closedTrades"], [])
        self.assertEqual(account.state["quarantined"], {})
        self.assertEqual(account.state["unaccountedActionExposure"], [])
        self.assertEqual(position["appliedReferenceEvents"], ["synthetic-ex"])
        self.assertFalse(result["research"]["actionAccounting"]["marketUsePermitted"])

    def test_missing_ex_bar_marks_stale_without_doubling_equity(self):
        data = case()
        ex = data["calendar"][30]
        data["securities"][0]["bars"] = [row for row in data["securities"][0]["bars"] if row["date"] != ex]
        account = engine(data)
        result = finish(account)
        row = next(r for r in result["curve"] if r["date"] == ex)
        self.assertAlmostEqual(row["equity"], 49991)
        self.assertEqual(row["staleSymbols"], [SYMBOL])
        self.assertFalse(row["valuationReliable"])
        self.assertEqual(account.state["positions"][SYMBOL]["riskEntryPrice"], 5)
        self.assertEqual(result["closedTrades"], [])
        self.assertEqual(account.state["quarantined"], {})

    def test_missing_ex_bar_does_not_authorize_trading_with_a_synthetic_prior_quote(self):
        data = case()
        days = data["calendar"]
        data["securities"][0]["bars"] = [r for r in data["securities"][0]["bars"] if r["date"] != days[30]]
        for row in data["securities"][0]["bars"]:
            if row["date"] >= days[31]:
                row.update(open=4.7, close=4.7, high=4.72, low=4.68)
        account = engine(data)
        # Force an existing saved exit on record close, before applying rights.
        while account.dates[account.state["cursor"]] < days[30]:
            account.step(1)
        account.state["forcedExits"][SYMBOL] = {"signalDate": days[29], "reason": "syntheticSavedExit"}
        result = finish(account)
        sells = [row for row in result["trades"] if row["side"] == "sell"]
        self.assertEqual([(r["date"], r["quantity"]) for r in sells], [(days[32], 1800)])
        self.assertTrue(any(r.get("reason") == "missingPreviousTradingBar" and r["date"] == days[31] for r in result["decisions"]))
        self.assertEqual(account.state["quarantined"], {})

    def test_ex_day_pending_buy_keeps_frozen_quantity_no_key_error_or_free_shares(self):
        data = case()
        result = finish(engine(data, schedule(data, indexes=(29,))))
        self.assertEqual(len(result["trades"]), 1)
        buy = result["trades"][0]
        self.assertEqual((buy["date"], buy["quantity"], buy["price"]), (data["calendar"][30], 900, 5))
        self.assertEqual(result["research"]["actionAccounting"]["cashSummary"]["incomeByOriginalEpisode"], {})

    def test_cash_receivable_preserves_equity_and_is_not_spendable_on_pay_open(self):
        data = case(cash="1", ratio="0", pay=32, tax="0.20")
        result = finish(engine(data))
        days = {row["date"]: row for row in result["daily"]}
        ex = days[data["calendar"][30]]["researchCashBook"]
        self.assertEqual(float(ex["grossReceivable"]), 900)
        self.assertEqual(float(ex["taxReserveLiability"]), 180)
        self.assertEqual(float(ex["netAssetAdjustment"]), 720)
        before_cash = days[data["calendar"][29]]["cash"]
        self.assertEqual(days[data["calendar"][32]]["cash"], before_cash)
        self.assertAlmostEqual(days[data["calendar"][33]]["cash"], before_cash + 720)
        self.assertAlmostEqual(result["curve"][-1]["equity"], 49991 - 180)

    def test_real_price_fall_still_triggers_and_persists_through_locked_grants(self):
        data = case(listing=33)
        rows = data["securities"][0]["bars"]
        for row in rows[31:]:
            row.update(open=4.7, close=4.7, high=4.72, low=4.68)
        account = engine(data)
        result = finish(account)
        sells = [row for row in result["trades"] if row["side"] == "sell"]
        self.assertEqual([(r["date"], r["quantity"]) for r in sells], [(data["calendar"][32], 900), (data["calendar"][33], 900)])
        self.assertTrue(all(row["signalDate"] == data["calendar"][31] for row in sells))
        self.assertEqual(result["forcedExits"], {})
        # 1800*4.7 - sale fees8.46 - initial paid9009 = -557.46.
        self.assertAlmostEqual(result["closedTrades"][0]["pnlAmount"], -557.46)
        self.assertAlmostEqual(result["curve"][-1]["equity"], 50000 - 557.46)

    def test_late_cash_attribution_and_reentry_does_not_change_old_episode(self):
        data = case(cash="1", ratio="0", pay=37, tax="0.20")
        rows = data["securities"][0]["bars"]
        for row in rows[31:]:
            row.update(open=8.45, close=8.45, high=8.47, low=8.43)
        account = engine(data, schedule(data, indexes=(25, 34)))
        result = finish(account)
        closed = result["closedTrades"][0]
        # Original episode:900*8.45 -7.605 sale fee -9009 entry +720 dividend.
        self.assertAlmostEqual(closed["pnlAmount"], -691.605)
        episode = account.state["positions"][SYMBOL]
        self.assertEqual(episode["realizedPnl"], 0)
        income = result["research"]["actionAccounting"]["cashSummary"]["incomeByOriginalEpisode"]
        self.assertEqual(income, {result["trades"][0]["fillId"]: "720.00"})

    def test_unexplained_price_gap_remains_quarantined(self):
        data = case()
        data["researchDistributions"] = []
        account = engine(data)
        result = finish(account)
        self.assertEqual(account.state["quarantined"][SYMBOL], data["calendar"][30])
        self.assertTrue(any(not row["valuationReliable"] for row in result["curve"]))

    def test_mid_ex_day_failure_restores_cash_rights_lots_and_queues(self):
        data = case(cash="0.1")
        account = engine(data)
        while account.dates[account.state["cursor"]] < data["calendar"][30]:
            account.step(1)
        before = account.checkpoint()
        with patch.object(account, "_execute_target", side_effect=RuntimeError("fixture failure after action")):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                account.step(1)
        self.assertEqual(before, account.checkpoint())
        account.step(1)
        self.assertEqual(account.state["positions"][SYMBOL]["quantity"], 1800)
        self.assertEqual(account.applied_action_ids, {"synthetic-ex"})

    def test_conflicting_reference_and_mutated_policy_are_rejected(self):
        data = case()
        data["securities"][0]["precloseByDate"] = {data["calendar"][30]: 10.}
        with self.assertRaises(ValueError):
            engine(data)
        account = engine(case())
        account.action_policy["taxReserveRate"] = "0.1"
        with self.assertRaises(ValueError):
            account.step(1)


if __name__ == "__main__":
    unittest.main()
