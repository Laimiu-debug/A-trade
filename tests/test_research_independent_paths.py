"""Synthetic isolated paths; no historical files or accounts are loaded."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from service.portfolio_engine import PortfolioEngine, _digest
from service.research_account_fees import LegacyFeePolicy
from service.research_fixed_signal_account import FixedSignalAccount
from service.research_volatility_trail import VolatilityTrailAccount
from service.research_independent_paths import run_independent_path, single_security_dataset

SYMBOL = "sh600001"


def fixture(length=45):
    dates, cursor = [], date(2023, 6, 1)
    while len(dates) < length:
        if cursor.weekday() < 5:
            dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    bars = [{"date": day, "open": 10., "close": 10., "high": 10.05, "low": 9.95,
             "volume": 1_000_000, "amount": 10_000_000.} for day in dates]
    security = {"symbol": SYMBOL, "bars": bars, "listingDate": "2010-01-01",
        "stByDate": {day: False for day in dates}, "tradeStatusByDate": {day: True for day in dates},
        "precloseByDate": {day: 10. for day in dates},
        "openingVolumeByDate": {day: 1_000_000 for day in dates}, "priceBasis": "raw-synthetic"}
    return {"calendar": dates, "calendarSource": "independent-synthetic", "adjustment": "raw",
        "securities": [security], "fingerprint": "test-independent-full-prefix",
        "membershipByDate": {day: [SYMBOL, "sz000001"] for day in dates},
        "benchmark": {"symbol": "sh000300", "bars": [{"date": d, "close": 4000.} for d in dates]},
        "dataQuality": {"certified": False, "note": "synthetic-only"}}


def config(data, exit_policy="trail10", **changes):
    return {"startDate": data["calendar"][5], "endDate": data["calendar"][-1],
        "initialCapital": 500000, "topN": 5, "maxPositions": 5, "maxStockWeight": .2,
        "rebalanceMode": "risk", "rebalanceEvery": 1, "stopLossPct": .05,
        "trailingStopPct": .1 if exit_policy == "trail10" else 0, "takeProfitPct": 0, "maxHoldingDays": 0,
        "commissionRate": .0003, "minCommission": 5, "transferFeeRate": .00001,
        "stampDutyRate": .001, "slippageBps": 10, "maxParticipationRate": .1,
        "strategies": [{"id": "fixed", "kind": "momentum", "pool": "base"}],
        "allocation": {"mode": "single", "strategyIds": ["fixed"]},
        "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 120, "minAmount20": 50000000},
        **changes}


def signal(data, index=25):
    return {"signalId": "fixed:" + data["calendar"][index] + ":" + SYMBOL,
            "strategyId": "fixed", "symbol": SYMBOL, "signalDate": data["calendar"][index], "originalRank": 4}


def prices(data, changes):
    security = data["securities"][0]
    for i, row in enumerate(security["bars"]):
        close, opening = changes.get(i, (10., 10.))
        row.update(close=close, open=opening, high=max(close, opening) + .05, low=min(close, opening) - .05)
        security["precloseByDate"][row["date"]] = security["bars"][max(0, i - 1)]["close"]


def closing_fixture():
    data = fixture()
    prices(data, {27: (10.8, 10.2), 28: (10.1, 10.3), 29: (9.6, 9.8), 30: (9.7, 9.7)})
    return data


def run(data, exit_policy="trail10", *, index=25, full=False, settings=None):
    return run_independent_path(data, signal(data, index), settings or config(data, exit_policy),
        exit_policy=exit_policy, fee_policy=LegacyFeePolicy(), stop_when_resolved=not full)


def direct(data, exit_policy="trail10", *, index=25, settings=None):
    cls = FixedSignalAccount if exit_policy == "trail10" else VolatilityTrailAccount
    engine = cls(single_security_dataset(data, SYMBOL), [signal(data, index)], settings or config(data, exit_policy),
                 entry_weight=.2, fee_policy=LegacyFeePolicy())
    engine.step(20000)
    return engine, engine.result()


class IndependentPathsTests(unittest.TestCase):
    def test_projection_preserves_full_security_calendar_and_metadata_without_mutation(self):
        data = fixture(); second = deepcopy(data["securities"][0]); second["symbol"] = "sz000001"
        data["securities"].append(second); data["historyCoverage"] = {"startDate": data["calendar"][0]}
        before = deepcopy(data); projected = single_security_dataset(data, SYMBOL)
        self.assertEqual(projected["securities"], [before["securities"][0]])
        for key in data.keys() - {"securities"}:
            self.assertEqual(projected[key], before[key])
        projected["securities"][0]["bars"][0]["close"] = 1
        projected["calendar"].pop(); projected["dataQuality"]["note"] = "changed"
        self.assertEqual(data, before)

    def test_projection_and_exact_policy_configuration_reject_ambiguous_inputs(self):
        data = fixture()
        for symbol in (None, "sh600999"):
            with self.assertRaises(ValueError): single_security_dataset(data, symbol)
        duplicate = deepcopy(data); duplicate["securities"].append(deepcopy(duplicate["securities"][0]))
        with self.assertRaises(ValueError): single_security_dataset(duplicate, SYMBOL)
        for changes in ({"initialCapital": 100000}, {"maxStockWeight": 1}, {"stopLossPct": 0},
                        {"trailingStopPct": 0}, {"maxHoldingDays": 5}, {"initialCapital": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run(data, settings=config(data, **changes))
        with self.assertRaises(ValueError):
            run_independent_path(data, signal(data), config(data), exit_policy="other", fee_policy=LegacyFeePolicy())

    def test_original_signal_date_rank_and_schema_validation_stays_inherited(self):
        data = fixture()
        for changes in ({"originalRank": True}, {"originalRank": 6}, {"signalDate": data["calendar"][4]},
                        {"strategyId": "other"}, {"extra": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_independent_path(data, {**signal(data), **changes}, config(data), exit_policy="trail10", fee_policy=LegacyFeePolicy())

    def test_fixed_full_run_matches_original_account_evidence_and_cash(self):
        data = closing_fixture(); got = run(data, full=True); engine, expected = direct(data)
        for key in ("trades", "closedTrades", "decisions", "quality", "warnings"):
            self.assertEqual(got["evidence"][key], expected[key])
        self.assertEqual(got["evidence"]["feeLedger"], expected["research"]["feeLedger"])
        self.assertEqual(got["evidence"]["terminal"]["cash"], engine.state["cash"])
        self.assertEqual(got["sourceProjection"]["datasetDigest"], engine.data_digest)
        self.assertEqual(got["run"]["processedSessions"], len(engine.dates))
        self.assertEqual(got["status"], "closed")

    def test_volatility_full_run_matches_original_adapter_risk_and_fills(self):
        data = closing_fixture(); got = run(data, "volatilityTrail", full=True); engine, expected = direct(data, "volatilityTrail")
        for key in ("trades", "closedTrades", "decisions", "quality"):
            self.assertEqual(got["evidence"][key], expected[key])
        self.assertEqual(got["evidence"]["volatilityTrail"], expected["research"]["volatilityTrail"])
        self.assertEqual(got["evidence"]["currentPositionTrails"], engine._trail_positions)
        self.assertEqual(got["evidence"]["terminal"]["cash"], engine.state["cash"])

    def test_resolved_early_stop_keeps_original_end_and_same_completed_label(self):
        for mode in ("trail10", "volatilityTrail"):
            with self.subTest(mode=mode):
                data = closing_fixture(); early = run(data, mode); full = run(data, mode, full=True)
                self.assertEqual(early["label"], full["label"])
                self.assertEqual(early["evidence"]["trades"], full["evidence"]["trades"])
                self.assertEqual(early["evidence"]["feeLedger"], full["evidence"]["feeLedger"])
                self.assertTrue(early["run"]["stoppedEarly"])
                self.assertTrue(early["run"]["notWholePeriodInvestmentAccount"])
                self.assertEqual(early["run"]["requestedEndDate"], data["calendar"][-1])
                self.assertEqual(early["run"]["processedThrough"], early["label"]["exitDate"])
                self.assertTrue(early["evidence"]["terminal"]["emptyPendingTargetEnvelope"])
                self.assertNotIn("metrics", early); self.assertNotIn("daily", early["evidence"])

    def test_cash_label_includes_entry_exit_fees_and_embedded_slippage(self):
        data = closing_fixture(); got = run(data); fills = got["evidence"]["trades"]
        bought = sum(row["amount"] + row["fee"] for row in fills if row["side"] == "buy")
        received = sum(row["amount"] - row["fee"] for row in fills if row["side"] == "sell")
        self.assertLessEqual(bought, 100000)
        self.assertEqual(got["evidence"]["opportunity"]["budget"], 100000)
        self.assertAlmostEqual(got["label"]["netReturn"], (received - bought) / bought)
        self.assertAlmostEqual(got["outcome"]["netPnlAmount"], received - bought)
        self.assertNotEqual(got["label"]["netReturn"], got["outcome"]["netPnlAmount"] / 500000)

    def test_missing_next_open_is_unfilled_not_a_zero_label_or_later_retry(self):
        data = fixture(); data["securities"][0]["bars"].pop(26)
        got = run(data)
        self.assertEqual(got["status"], "unfilled"); self.assertIsNone(got["label"])
        self.assertIsNone(got["outcome"]["netReturn"])
        self.assertEqual(got["evidence"]["opportunity"]["status"], "expired")
        self.assertEqual(got["run"]["processedThrough"], data["calendar"][26])
        self.assertEqual(got["evidence"]["trades"], [])

    def test_final_session_opportunity_stays_censored_unfilled(self):
        data = fixture(); got = run(data, index=44)
        self.assertEqual(got["evidence"]["opportunity"]["status"], "censored")
        self.assertEqual(got["status"], "unfilled"); self.assertIsNone(got["label"])
        self.assertFalse(got["run"]["stoppedEarly"])
        self.assertEqual(got["run"]["processedThrough"], data["calendar"][-1])

    def test_open_cutoff_has_no_mature_label_or_forced_final_liquidation(self):
        for mode in ("trail10", "volatilityTrail"):
            with self.subTest(mode=mode):
                data = fixture(); got = run(data, mode)
                self.assertEqual(got["status"], "open")
                self.assertIsNone(got["label"]["exitDate"]); self.assertIsNone(got["label"]["netReturn"])
                self.assertFalse(got["run"]["stoppedEarly"])
                self.assertEqual(got["run"]["processedThrough"], data["calendar"][-1])
                self.assertEqual(len(got["evidence"]["trades"]), 1)
                self.assertGreater(got["evidence"]["terminal"]["positions"][0]["marketValue"], 0)

    def test_partial_entry_uses_actual_investment_and_only_one_buy_attempt(self):
        data = closing_fixture(); data["securities"][0]["openingVolumeByDate"][data["calendar"][26]] = 10000
        got = run(data)
        self.assertEqual(got["evidence"]["opportunity"]["status"], "partial")
        self.assertEqual(sum(row["side"] == "buy" for row in got["evidence"]["trades"]), 1)
        self.assertEqual(got["evidence"]["trades"][0]["quantity"], 1000)
        self.assertEqual(got["status"], "closed")
        self.assertLess(got["outcome"]["investedAmount"], 100000)

    def test_partial_sell_and_pending_exit_at_cutoff_are_not_closed(self):
        data = closing_fixture(); end = data["calendar"][30]
        data["securities"][0]["openingVolumeByDate"][end] = 1000
        got = run(data, settings=config(data, endDate=end))
        self.assertEqual(got["status"], "open")
        self.assertIsNone(got["label"]["netReturn"])
        self.assertEqual(got["evidence"]["trades"][-1]["positionAction"], "reduce")
        self.assertTrue(got["evidence"]["terminal"]["forcedExits"])
        self.assertEqual(got["evidence"]["closedTrades"], [])

    def test_blocked_exit_keeps_original_queue_and_later_retries(self):
        data = closing_fixture(); data["securities"][0]["bars"][30]["volume"] = 0
        got = run(data); fills = got["evidence"]["trades"]
        self.assertEqual(got["status"], "closed")
        self.assertEqual(fills[-1]["date"], data["calendar"][32])
        self.assertEqual(fills[-1]["signalDate"], data["calendar"][29])
        deferred = [r for r in got["evidence"]["decisions"] if r["action"] == "sell" and r["status"] == "deferred"]
        self.assertEqual([r["reason"] for r in deferred], ["suspended", "missingPreviousTradingBar"])

    def test_original_start_quarantine_is_not_skipped_before_signal(self):
        data = fixture()
        prices(data, {i: (20., 20.) for i in range(10, len(data["calendar"]))})
        got = run(data)
        self.assertEqual(got["run"]["firstProcessedDate"], data["calendar"][5])
        self.assertEqual(got["status"], "unfilled")
        self.assertEqual(got["evidence"]["terminal"]["quarantined"][SYMBOL], data["calendar"][10])
        self.assertEqual(got["evidence"]["opportunity"]["reason"], "soldTodayOrQuarantined")
        _, incorrectly_shortened = direct(data, settings=config(data, startDate=data["calendar"][25]))
        self.assertTrue(incorrectly_shortened["trades"])

    def test_declared_receivable_delays_early_stop_and_label_availability(self):
        data = closing_fixture(); days = data["calendar"]
        data["securities"][0]["corporateActions"] = [{"id": "cash", "source": "synthetic",
            "announcementDate": days[24], "recordDate": days[27], "exDate": days[28],
            "cashNetPerShare": .1, "cashPayDate": days[34]}]
        got = run(data)
        self.assertEqual(got["status"], "closed")
        self.assertEqual(got["outcome"]["finalExitDate"], days[30])
        self.assertEqual(got["outcome"]["labelAvailableDate"], days[35])
        self.assertEqual(got["run"]["processedThrough"], days[35])
        self.assertFalse(got["evidence"]["terminal"]["cashReceivables"])
        self.assertGreater(got["evidence"]["terminal"]["actionCashNet"], 0)
        pending = run(data, settings=config(data, endDate=days[32]))
        self.assertEqual(pending["status"], "unresolved")
        self.assertTrue(pending["outcome"]["positionClosed"])
        self.assertIsNone(pending["label"]["exitDate"]); self.assertIsNone(pending["label"]["netReturn"])

    def test_input_immutability_and_loop_boundary_guards_without_step_rehash(self):
        data = closing_fixture(); settings = config(data); row = signal(data)
        before = deepcopy((data, settings, row)); guards = []
        original = FixedSignalAccount._identity_stable
        def guarded(engine):
            guards.append(engine.state["cursor"])
            return original(engine)
        with patch.object(FixedSignalAccount, "_identity_stable", guarded), patch.object(FixedSignalAccount, "step", side_effect=AssertionError("per-step adapter rehash")):
            got = run_independent_path(data, row, settings, exit_policy="trail10", fee_policy=LegacyFeePolicy())
        self.assertEqual((data, settings, row), before)
        self.assertEqual(guards, [0, got["run"]["processedSessions"], got["run"]["processedSessions"]])
        self.assertEqual(got["sourceProjection"]["calendarDigest"], _digest(data["calendar"]))

    def test_inherited_transaction_restores_partial_day_after_failure(self):
        data = closing_fixture(); engines = []
        original_step, original_day = PortfolioEngine.step, FixedSignalAccount._step_day
        def observed_step(engine, days):
            if not engines: engines.append(engine)
            return original_step(engine, days)
        def failing_day(engine, day):
            original_day(engine, day)
            if day == data["calendar"][26]: raise RuntimeError("synthetic post-fill fault")
        with patch.object(PortfolioEngine, "step", observed_step), patch.object(FixedSignalAccount, "_step_day", failing_day):
            with self.assertRaisesRegex(RuntimeError, "synthetic post-fill"):
                run(data)
        engine = engines[0]
        self.assertEqual(engine.dates[engine.state["cursor"]], data["calendar"][26])
        self.assertEqual(engine.state["trades"], [])
        self.assertEqual(engine.research_state["opportunities"][0]["status"], "planned")
        self.assertEqual(engine.fee_ledger.checkpoint()["paidLedger"], [])
        engine._identity_stable(); engine._validate_research_state()


if __name__ == "__main__":
    unittest.main()
