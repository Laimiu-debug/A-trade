"""Hand-computed descriptive accounting fixtures; no market or strategy run."""
from copy import deepcopy
import json
import math
import unittest

from service.research_account_outcomes import summarize_account


def empty_result():
    dates = ["2022-06-30", "2022-07-01", "2022-12-30", "2023-01-03", "2023-06-30", "2023-07-03", "2023-12-29"]
    return {"status": "completed", "config": {"initialCapital": 10000., "startDate": "2022-01-04", "endDate": "2023-12-29"},
            "curve": [{"date": day, "equity": 10000., "cash": 10000., "marketValue": 0., "valuationReliable": True,
                       "staleSymbols": []} for day in dates],
            "daily": [{"date": day, "holdings": []} for day in dates], "trades": [], "closedTrades": [],
            "positions": [], "forcedExits": {}, "pendingTarget": None, "cashReceivables": [], "actionPayables": [],
            "preExRights": {}, "actionLedger": [], "reservedActionCash": 0., "metrics": {},
            "quality": {"certified": False, "calendarIndependent": True, "pointInTimeComplete": False,
                        "corporateActionsComplete": False, "unaccountedActionExposure": []},
            "warnings": ["syntheticOnly"], "research": {"opportunities": [], "opportunityCounts": {}}}


def episode(symbol, entry, exit_day, pnl, invested, held):
    return {"symbol": symbol, "entryDate": entry, "exitDate": exit_day, "pnlAmount": pnl,
            "investedAmount": invested, "returnPct": pnl / invested * 100 if invested and pnl is not None else None,
            "holdingDays": held}


def fill(day, symbol, side, amount, fee=0., realized=None, quantity=100):
    return {"date": day, "signalDate": "2022-01-04", "symbol": symbol, "side": side,
            "quantity": quantity, "price": amount / quantity, "amount": amount, "fee": fee,
            "slippage": 0., "realizedPnl": realized}


def closed_result():
    result = empty_result()
    result["closedTrades"] = [episode("sh600001", "2022-01-05", "2022-06-30", 100., 1000., 2),
                              episode("sh600002", "2022-07-01", "2022-12-30", -40., 200., 3),
                              episode("sh600003", "2023-01-03", "2023-06-30", 0., 100., 4),
                              episode("sh600004", "2023-07-03", "2023-12-29", 60., 300., 5)]
    # Cash ledger matches net episode profit; fees are already included in basis/PnL.
    result["trades"] = [fill("2022-06-30", "sh600001", "buy", 1560., 40.),
                        fill("2023-12-29", "sh600001", "sell", 1760., 40., 120.)]
    for row, equity in zip(result["curve"], (10100., 10100., 10060., 10060., 10060., 10060., 10120.)):
        row.update(equity=equity, cash=equity)
    return result


class ResearchAccountOutcomesTests(unittest.TestCase):
    def test_closed_all_signs_amount_weighted_pf_and_linear_quantiles(self):
        result = closed_result()
        before = deepcopy(result)
        report = summarize_account(result)
        closed = report["closedEpisodes"]
        self.assertEqual((closed["count"], closed["wins"], closed["losses"], closed["zeros"]), (4, 2, 1, 1))
        self.assertEqual(closed["winRatePct"], 50.)
        self.assertEqual(closed["expectancyReturnPct"], 2.5)
        self.assertEqual(closed["expectancyPnlAmount"], 30.)
        self.assertEqual(closed["meanWinReturnPct"], 15.)
        self.assertEqual(closed["medianLossReturnPct"], -20.)
        self.assertEqual(closed["payoffRatio"], .75)
        self.assertEqual(closed["profitFactor"], 4.)  # 160 / 40, not positive rate sum / negative rate sum.
        self.assertEqual(closed["returnQuantilesPct"]["p25"], -5.)
        self.assertEqual(closed["returnQuantilesPct"]["p50"], 5.)
        self.assertEqual(closed["returnQuantilesPct"]["p75"], 12.5)
        self.assertEqual(closed["holdingSessions"]["mean"], 3.5)
        self.assertEqual(report["account"]["feesPaid"], 80.)
        self.assertAlmostEqual(report["account"]["netReturnPct"], 1.2)
        self.assertEqual(report["reconciliation"]["cashFlow"]["status"], "reconciled")
        self.assertEqual(report["reconciliation"]["episodePnlBridge"]["residual"], 0.)
        self.assertEqual(result, before)
        report["sourceQuality"]["certified"] = True
        self.assertFalse(result["quality"]["certified"])

    def test_sorting_loss_streak_zero_resets_and_unknown_does_not_bridge(self):
        result = empty_result()
        rows = [episode(f"sh60000{i}", "2022-01-05", day, pnl, 100., 1)
                for i, (day, pnl) in enumerate(zip([r["date"] for r in result["curve"]], (-1, -2, 0, -3, -4, -5, 1)), 1)]
        result["closedTrades"] = list(reversed(rows))
        closed = summarize_account(result)["closedEpisodes"]
        self.assertEqual(closed["maxConsecutiveLosses"], 3)
        self.assertEqual([r["record"]["exitDate"] for r in closed["episodes"]], sorted(r["exitDate"] for r in rows))
        result["closedTrades"][2]["pnlAmount"] = None
        result["closedTrades"][2]["returnPct"] = None
        unknown = summarize_account(result)["closedEpisodes"]
        self.assertIsNone(unknown["maxConsecutiveLosses"])
        self.assertEqual(unknown["observedConsecutiveLossesLowerBound"], 2)
        self.assertIsNone(unknown["winRatePct"])

    def test_empty_no_loss_and_no_winner_denominators_are_not_infinity(self):
        result = empty_result()
        report = summarize_account(result)
        for field in ("profitFactor", "payoffRatio", "winRatePct", "expectancyReturnPct", "maxConsecutiveLosses"):
            self.assertIsNone(report["closedEpisodes"][field])
        self.assertEqual(report["closedEpisodes"]["status"], "noClosedEpisodes")
        self.assertIsNone(report["concentration"]["top1ProfitSharePct"])
        result["closedTrades"] = [episode("sh600001", "2022-01-05", "2022-06-30", 1., 10., 1)]
        closed = summarize_account(result)["closedEpisodes"]
        self.assertIsNone(closed["profitFactor"])
        self.assertEqual(closed["metricStatus"]["profitFactor"], "noLossDenominator")
        result["closedTrades"][0].update(pnlAmount=-1., returnPct=-10.)
        closed = summarize_account(result)["closedEpisodes"]
        self.assertEqual(closed["profitFactor"], 0.)
        self.assertIsNone(closed["payoffRatio"])
        json.dumps(closed, allow_nan=False)

    def test_partial_sales_are_not_closed_and_open_values_are_kept(self):
        result = empty_result()
        result["trades"] = [fill("2022-06-30", "sh600001", "buy", 1000., 10., quantity=100),
                            fill("2022-12-30", "sh600001", "sell", 450., 5., 41., quantity=40)]
        result["positions"] = [{"symbol": "sh600001", "quantity": 60, "costBasis": 606., "realizedPnl": 41.,
                                "investedAmount": 1010., "entryDate": "2022-06-30", "marketValue": 660.,
                                "markPrice": 11., "priceDate": "2023-12-29", "lots": []}]
        result["curve"][-1].update(cash=9435., marketValue=660., equity=10095.)
        result["forcedExits"] = {"sh600001": {"signalDate": "2023-12-29", "reason": "trailingStop", "quantity": 60}}
        result["daily"][-1]["holdings"] = [{"symbol": "sh600001", "holdingDays": 7}]
        report = summarize_account(result)
        self.assertEqual(report["closedEpisodes"]["count"], 0)
        self.assertEqual(report["account"]["sellFillCount"], 1)
        self.assertEqual(report["openPositions"]["positions"][0]["unrealizedPnlAmount"], 54.)
        self.assertEqual(report["openPositions"]["positions"][0]["realizedPnlWhileOpen"], 41.)
        self.assertEqual(report["openPositions"]["positions"][0]["observedHoldingSessions"], 7)
        self.assertEqual(report["unresolvedExits"]["orders"], result["forcedExits"])
        self.assertEqual(report["reconciliation"]["episodePnlBridge"]["residual"], 0.)
        self.assertEqual(report["reconciliation"]["cashFlow"]["residual"], 0.)
        self.assertTrue(report["account"]["noForcedLiquidation"])

    def test_periods_carry_prior_close_and_drawdown_including_entry_base(self):
        result = empty_result()
        values = [12000., 10800., 13200., 11880., 14520., 13068., 15972.]
        for row, equity in zip(result["curve"], values):
            row.update(equity=equity, cash=equity / 2, marketValue=equity / 2)
        report = summarize_account(result)
        periods = {r["id"]: r for r in report["periods"]}
        self.assertEqual(periods["2023"]["enteringEquity"], 13200.)
        self.assertEqual(periods["2023"]["enteringEquityDate"], "2022-12-30")
        self.assertAlmostEqual(periods["2023"]["returnPct"], 21.)
        self.assertAlmostEqual(periods["2023"]["maxDrawdownPct"], 10.)
        self.assertAlmostEqual(periods["2022-H2"]["returnPct"], 10.)
        self.assertEqual(periods["overall"]["exposurePct"]["mean"], 50.)
        self.assertEqual(periods["overall"]["investedSessions"], 7)
        self.assertFalse(periods["2023"]["calendarCompletenessProven"])
        custom = summarize_account(result, intervals=[{"id": "later", "start": "2024-01-01", "end": "2024-06-30"}])["periods"]
        self.assertEqual(len(custom), 2)
        self.assertEqual(custom[1]["status"], "noObservations")
        self.assertIsNone(custom[1]["returnPct"])

    def test_closed_episodes_assigned_whole_to_exit_window(self):
        result = closed_result()
        periods = {r["id"]: r for r in summarize_account(result)["periods"]}
        self.assertEqual(periods["2022"]["closedEpisodes"]["count"], 2)
        self.assertEqual(periods["2022"]["closedEpisodes"]["netPnlAmount"], 60.)
        self.assertEqual(periods["2023-H1"]["closedEpisodes"]["zeros"], 1)
        self.assertEqual(periods["2023"]["closedEpisodes"]["count"], 2)

    def test_rights_cash_balance_and_unallocated_attribution_not_episode_profit(self):
        result = empty_result()
        result["actionLedger"] = [{"date": "2022-06-30", "stage": "cashReceived", "amount": 30.},
                                  {"date": "2022-07-01", "stage": "subscriptionPaid", "amount": 20.},
                                  {"date": "2022-12-30", "stage": "cashPaid", "amount": 5.}]
        result["cashReceivables"] = [{"amount": 10., "eventKey": "dividend"}]
        result["actionPayables"] = [{"amount": 7., "eventKey": "rights"}]
        result["preExRights"] = {"prepaid": {"amount": 20.}}
        result["reservedActionCash"] = 7.
        result["curve"][-1].update(cash=10005., equity=10028.)
        report = summarize_account(result)
        self.assertEqual(report["rights"]["assetAdjustment"], 23.)
        self.assertEqual(report["reconciliation"]["cashFlow"]["residual"], 0.)
        self.assertEqual(report["reconciliation"]["balanceSheet"]["residual"], 0.)
        self.assertEqual(report["reconciliation"]["rightsAttributionGapAmount"], 28.)
        self.assertEqual(report["closedEpisodes"]["netPnlAmount"], 0.)
        self.assertIn("episodePnlBridgeResidual", report["quality"]["integrityIssues"])
        self.assertEqual(report["rights"]["reservedActionCash"], 7.)

    def test_unknown_stale_data_is_not_cleaned_or_counted_zero(self):
        result = closed_result()
        result["closedTrades"][0]["investedAmount"] = 0.
        result["closedTrades"][0]["returnPct"] = None
        result["curve"][2]["equity"] = None
        result["curve"][2]["valuationReliable"] = False
        result["quality"]["unaccountedActionExposure"] = [{"symbol": "sh600001", "reason": "unknown"}]
        result["actionLedger"] = [{"date": "2022-06-30", "stage": "unrecognizedFutureStage", "amount": 9.}]
        report = summarize_account(result)
        self.assertEqual(report["closedEpisodes"]["unknownReturnCount"], 1)
        self.assertIsNone(report["closedEpisodes"]["expectancyReturnPct"])
        self.assertIsNone(report["account"]["netReturnPct"])
        self.assertIsNone(report["account"]["maxDrawdownPct"])
        self.assertEqual(report["sourceQuality"], result["quality"])
        self.assertEqual(report["reconciliation"]["cashFlow"]["status"], "unknown")
        self.assertEqual(len(report["reconciliation"]["unknownActionStages"]), 1)
        self.assertIn("2022-12-30", report["quality"]["valuationUnreliableDates"])
        self.assertFalse(report["certified"])
        self.assertFalse(report["targetAchieved"])

    def test_concentration_removes_only_largest_positive_closed_pnl(self):
        result = empty_result()
        result["closedTrades"] = [episode(f"sh600{i:03d}", "2022-01-05", "2023-12-29", pnl, 100., 2)
                                  for i, pnl in enumerate([10, 20, 30, 40, 50, 60, -100, 0])]
        report = summarize_account(result)
        c = report["concentration"]
        self.assertAlmostEqual(c["top1ProfitSharePct"], 60 / 210 * 100)
        self.assertAlmostEqual(c["top5ProfitSharePct"], 200 / 210 * 100)
        self.assertEqual(c["excludingTop5PositiveEpisodes"]["remainingCount"], 3)
        self.assertEqual(c["excludingTop5PositiveEpisodes"]["netPnlAmount"], -90.)
        self.assertEqual(c["excludingTop5PositiveEpisodes"]["meanReturnPct"], -30.)
        self.assertEqual(report["closedEpisodes"]["secondaryClosedNet30"]["denominator"], 8)
        self.assertEqual(report["closedEpisodes"]["secondaryClosedNet30"]["hits"], 4)

    def test_state_and_fee_ledger_reconciliation_detects_inconsistency(self):
        result = closed_result()
        state = {key: deepcopy(result[key]) for key in ("curve", "trades", "closedTrades", "forcedExits", "cashReceivables",
                  "actionPayables", "preExRights", "actionLedger", "reservedActionCash")}
        state.update(cash=10120., positions={})
        result["research"]["feeLedger"] = {"paidLedger": [{"quote": {"breakdown": {"total": 40.}}} for _ in range(2)]}
        report = summarize_account(result, {"engineState": state})
        self.assertTrue(all(row["matches"] for row in report["reconciliation"]["stateChecks"]))
        self.assertEqual(report["reconciliation"]["paidFees"]["residual"], 0.)
        state["cash"] += 1
        result["research"]["feeLedger"]["paidLedger"][0]["quote"]["breakdown"]["total"] = 39.
        report = summarize_account(result, state)
        self.assertIn("stateCashMismatch", report["quality"]["integrityIssues"])
        self.assertIn("paidFeesMismatch", report["quality"]["integrityIssues"])

    def test_malformed_integrity_rejected_and_empty_curve_explicit(self):
        result = empty_result()
        mutations = [lambda r: r["curve"].append(deepcopy(r["curve"][-1])),
                     lambda r: r["curve"].reverse(),
                     lambda r: r["curve"][0].update(date="2022-6-30"),
                     lambda r: r["curve"][0].update(equity=math.nan),
                     lambda r: r["curve"][0].update(equity=math.inf),
                     lambda r: r["config"].update(initialCapital=True),
                     lambda r: r["trades"].append(fill("2024-01-01", "sh600001", "buy", 1))]
        for mutate in mutations:
            bad = deepcopy(result)
            mutate(bad)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                summarize_account(bad)
        bad = closed_result()
        bad["closedTrades"].append(deepcopy(bad["closedTrades"][0]))
        with self.assertRaises(ValueError):
            summarize_account(bad)
        with self.assertRaises(ValueError):
            summarize_account(result, intervals=[{"id": "overall", "start": "2022-01-01", "end": "2023-01-01"}])
        result["curve"], result["daily"] = [], []
        report = summarize_account(result)
        self.assertIsNone(report["account"]["netReturnPct"])
        self.assertIsNone(report["account"]["cutoff"])
        self.assertEqual(report["account"]["finalEquity"], 10000.)

    def test_actual_adapter_small_synthetic_result_and_checkpoint_contract(self):
        from tests.test_research_fixed_signal_account import account, complete, fixture, set_prices, settings
        data = fixture(length=12)
        for security in data["securities"]:
            set_prices(data, security["symbol"], [10.] * 7 + [11.] * 5)
        engine = account(data, config=settings(data, takeProfitPct=.05))
        result = complete(engine)
        report = summarize_account(result, engine.checkpoint())
        self.assertEqual(report["closedEpisodes"]["count"], 1)
        self.assertAlmostEqual(report["closedEpisodes"]["netPnlAmount"], 1000.)
        self.assertEqual(report["openPositions"]["count"], 0)
        self.assertEqual(report["account"]["finalEquity"], 11000.)
        self.assertEqual(report["quality"]["integrityIssues"], [])
        self.assertEqual(report["reconciliation"]["paidFees"]["status"], "reconciled")
        self.assertTrue(all(row["matches"] for row in report["reconciliation"]["stateChecks"]))


if __name__ == "__main__":
    unittest.main()
