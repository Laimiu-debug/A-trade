"""Synthetic behavior checks only; no real historical source/account is run."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from service.research_account_fees import DatedFeePolicy, LegacyFeePolicy
from service.research_structural_exits import ResearchExitAccount
from service.research_volatility_stop import VolatilityStopAccount

SYMBOL = "sh600001"


def fixture(length=42):
    days, cursor = [], date(2023, 6, 1)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    bars = [{"date": day, "open": 10., "high": 10.2, "low": 9.8, "close": 10.,
             "volume": 1_000_000, "amount": 10_000_000.} for day in days]
    return {"calendar": days, "calendarSource": "synthetic-independent", "adjustment": "raw",
            "securities": [{"symbol": SYMBOL, "bars": bars,
                             "precloseByDate": {day: 10. for day in days}}]}


def set_bar(data, index, *, close=None, opening=None, volume=None):
    bar = data["securities"][0]["bars"][index]
    if close is not None:
        bar["close"] = close
    if opening is not None:
        bar["open"] = opening
    if volume is not None:
        bar["volume"] = volume
    bar["high"] = max(bar["high"], bar["close"], bar["open"])
    bar["low"] = min(bar["low"], bar["close"], bar["open"])


def inputs(data, signals, value=.2):
    return {row["signalId"]: {"signalId": row["signalId"], "symbol": row["symbol"],
             "signalDate": row["signalDate"],
             "asOfDate": data["calendar"][data["calendar"].index(row["signalDate"]) - 1],
             "meanTrueRange20": value} for row in signals}


def configuration(data, **changes):
    return {"startDate": data["calendar"][25], "endDate": data["calendar"][-1],
            "initialCapital": 50000, "topN": 5, "maxPositions": 5, "maxStockWeight": .2,
            "rebalanceMode": "risk", "rebalanceEvery": 1, "stopLossPct": .05,
            "trailingStopPct": .1, "takeProfitPct": 0, "maxHoldingDays": 0,
            "commissionRate": .0003, "minCommission": 5, "transferFeeRate": .00001,
            "stampDutyRate": .001, "slippageBps": 10, "maxParticipationRate": .1,
            "strategies": [{"id": "fixed", "kind": "momentum", "pool": "base"}],
            "allocation": {"mode": "single", "strategyIds": ["fixed"]},
            "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0},
            **changes}


def schedule(data, indexes=(25,)):
    return [{"signalId": f"synthetic:{index}", "strategyId": "fixed", "symbol": SYMBOL,
             "signalDate": data["calendar"][index], "originalRank": 1} for index in indexes]


def account(data, *, mode="meanTR20x2", tr=.2, signals=None, supplied=None, **config_changes):
    signals = schedule(data) if signals is None else signals
    values = inputs(data, signals, tr) if supplied is None else supplied
    return VolatilityStopAccount(data, signals, configuration(data, **config_changes),
        volatility_inputs=values, mode=mode, entry_weight=.2, fee_policy=LegacyFeePolicy())


class VolatilityStopTests(unittest.TestCase):
    def test_effective_five_percent_dynamic_branch_matches_original_economics(self):
        def strip_new_evidence(value):
            if isinstance(value, dict):
                return {key: strip_new_evidence(item) for key, item in value.items()
                        if key not in ("volatilityStop", "configuredFallbackStopLossPct")}
            if isinstance(value, list):
                return [strip_new_evidence(item) for item in value]
            return value
        for scenario in ("noProgress", "initial", "trailing", "partial", "unknown"):
            with self.subTest(scenario=scenario):
                data = fixture()
                if scenario in ("initial", "partial"):
                    set_bar(data, 26, close=9.4)
                if scenario == "trailing":
                    set_bar(data, 26, close=11.)
                    set_bar(data, 27, close=9.85)
                    data["securities"][0]["precloseByDate"][data["calendar"][27]] = 11.
                if scenario == "partial":
                    for index in range(26, 30):
                        set_bar(data, index, volume=3000)
                if scenario == "unknown":
                    data["securities"][0]["bars"].pop(30)
                config = configuration(data, slippageBps=0)
                original = ResearchExitAccount(deepcopy(data), schedule(data), config,
                    exit_rule="noProgress5", entry_weight=.2, fee_policy=LegacyFeePolicy())
                adapted = account(deepcopy(data), tr=10. * .025, slippageBps=0)
                original.step(20000)
                adapted.step(20000)
                self.assertEqual(adapted._entry_stop_bindings[0]["effectivePct"], .05)
                self.assertFalse(adapted._entry_stop_bindings[0]["fallback"])
                self.assertEqual(strip_new_evidence(adapted.state), original.state)
                actual_fees = adapted.fee_ledger.checkpoint()
                original_fees = original.fee_ledger.checkpoint()
                # The ledger snapshots full fills including the additional risk
                # evidence; their outer hashes consequently differ. Compare all
                # fee identity, quotes, paid entries and totals after removing
                # only that added evidence and the dependent outer checksum.
                actual_fees.pop("checkpointDigest")
                original_fees.pop("checkpointDigest")
                self.assertEqual(strip_new_evidence(actual_fees), original_fees)
                self.assertEqual(strip_new_evidence(adapted.result()["research"]["structuralExits"]),
                                 original.result()["research"]["structuralExits"])

    def test_fixed_mode_dated_fee_account_is_identical(self):
        data = fixture()
        config = configuration(data, stampDutyRate=None)
        signals = schedule(data)
        def fee():
            return DatedFeePolicy(commission_rate=config["commissionRate"],
                min_commission=config["minCommission"], commission_includes_transfer=False,
                stamp_duty_override=None)
        original = ResearchExitAccount(deepcopy(data), signals, config,
            exit_rule="noProgress5", entry_weight=.2, fee_policy=fee())
        adapted = VolatilityStopAccount(deepcopy(data), signals, config,
            volatility_inputs=inputs(data, signals), mode="fixed5", entry_weight=.2, fee_policy=fee())
        original.step(20000)
        adapted.step(20000)
        self.assertEqual(adapted.state, original.state)
        self.assertEqual(adapted.fee_ledger.checkpoint(), original.fee_ledger.checkpoint())

    def test_fixed_mode_matches_original_whole_account_with_fees_and_risk_evidence(self):
        for scenario in ("noProgress", "initial", "trailing", "partial", "unknown"):
            with self.subTest(scenario=scenario):
                data = fixture()
                if scenario in ("initial", "partial"):
                    set_bar(data, 26, close=9.4)
                if scenario == "trailing":
                    set_bar(data, 26, close=11.)
                    set_bar(data, 27, close=9.85)
                    data["securities"][0]["precloseByDate"][data["calendar"][27]] = 11.
                if scenario == "partial":
                    for index in range(26, 30):
                        set_bar(data, index, volume=3000)
                if scenario == "unknown":
                    data["securities"][0]["bars"].pop(30)
                original = ResearchExitAccount(deepcopy(data), schedule(data), configuration(data),
                    exit_rule="noProgress5", entry_weight=.2, fee_policy=LegacyFeePolicy())
                adapted = account(deepcopy(data), mode="fixed5")
                original.step(20000)
                adapted.step(20000)
                self.assertEqual(adapted.state, original.state)
                self.assertEqual(adapted.fee_ledger.checkpoint(), original.fee_ledger.checkpoint())
                self.assertEqual(adapted.result()["research"]["structuralExits"],
                                 original.result()["research"]["structuralExits"])
                self.assertTrue(adapted.result()["research"]["volatilityStop"]["observations"])

    def test_dynamic_freezes_entry_fraction_including_slippage_and_quantity_risk(self):
        data = fixture()
        engine = account(data)
        engine.step(2)
        entry = engine._entry_stop_bindings[0]
        self.assertAlmostEqual(entry["entryRiskPrice"], 10.01)
        self.assertAlmostEqual(entry["entryDistance"], .4)
        self.assertAlmostEqual(entry["effectivePct"], .4 / 10.01)
        self.assertAlmostEqual(entry["entryInitialRiskAmount"], entry["entryQuantity"] * .4)
        self.assertAlmostEqual(engine._stop_observations[0]["threshold"], 9.61)
        self.assertEqual(entry["asOfDate"], data["calendar"][24])
        self.assertFalse(entry["fallback"])
        self.assertEqual(engine.config["stopLossPct"], .05)
        self.assertAlmostEqual(engine.state["positions"][SYMBOL]["riskEntryPrice"], 10.01)

    def test_close_trigger_next_open_gap_and_t_plus_one_remain_inherited(self):
        data = fixture()
        set_bar(data, 26, close=9.5)
        set_bar(data, 27, opening=9.1, close=9.1)
        data["securities"][0]["precloseByDate"][data["calendar"][27]] = 9.5
        engine = account(data, slippageBps=0)
        engine.step(2)
        self.assertEqual([trade["side"] for trade in engine.state["trades"]], ["buy"])
        self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], "stopLoss")
        self.assertAlmostEqual(engine._stop_observations[-1]["threshold"], 9.6)
        engine.step(1)
        sale = engine.state["trades"][-1]
        self.assertEqual(sale["side"], "sell")
        self.assertEqual(sale["date"], data["calendar"][27])
        self.assertEqual(sale["signalDate"], data["calendar"][26])
        self.assertEqual(sale["price"], 9.1)
        self.assertLess(sale["price"], sale["riskEvidence"]["volatilityStop"]["threshold"])

    def test_initial_trailing_and_exact_fifth_day_priority(self):
        for close, tr, peak, expected in ((9.5, .2, None, "stopLoss"),
                                          (9.8, 1., 11., "trailingStop"),
                                          (10., 1., None, "noProgress5")):
            with self.subTest(expected=expected):
                data = fixture()
                if peak:
                    set_bar(data, 29, close=peak)
                    data["securities"][0]["precloseByDate"][data["calendar"][30]] = peak
                set_bar(data, 30, close=close)
                engine = account(data, tr=tr, slippageBps=0)
                engine.step(6)
                self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], expected)
                self.assertEqual(engine._stop_observations[-1]["holdingDays"], 5)
                self.assertEqual(engine._stop_observations[-1]["exitReason"], expected)

    def test_membership_delist_and_st_have_priority(self):
        for kind, expected in (("membership", "baseEligibilityLost"), ("delist", "delisted"), ("st", "historicalST")):
            with self.subTest(kind=kind):
                data = fixture()
                day = data["calendar"][27]
                if kind == "membership":
                    data["securities"][0]["memberByDate"] = {day: False}
                elif kind == "delist":
                    data["securities"][0]["delistDate"] = day
                else:
                    data["securities"][0]["stByDate"] = {day: True}
                set_bar(data, 27, close=9.)
                engine = account(data, slippageBps=0)
                engine.step(3)
                self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], expected)

    def test_unknown_invalid_and_too_wide_inputs_fall_back_without_dropping_signal(self):
        for value in (None, 0, -1, True, float("nan"), float("inf"), 5., 100.):
            with self.subTest(value=value):
                data = fixture()
                set_bar(data, 26, close=9.4)
                engine = account(data, tr=value, slippageBps=0)
                engine.step(2)
                entry = engine._entry_stop_bindings[0]
                self.assertTrue(entry["fallback"])
                self.assertEqual(entry["effectivePct"], .05)
                self.assertEqual(len(engine.research_state["opportunities"]), 1)
                self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], "stopLoss")
                self.assertEqual(engine.result()["research"]["volatilityStop"]["fallbackEntryCount"], 1)
        engine = account(fixture(), supplied={})
        engine.step(2)
        self.assertEqual(engine._entry_stop_bindings[0]["fallbackReason"], "missingVolatilityInput")

    def test_repeat_entry_of_same_symbol_binds_current_signal(self):
        data = fixture()
        set_bar(data, 26, close=9.4)
        signals = schedule(data, (25, 30))
        values = inputs(data, signals)
        values[signals[1]["signalId"]]["meanTrueRange20"] = .1
        engine = account(data, signals=signals, supplied=values, slippageBps=0)
        engine.step(7)
        self.assertEqual([row["signalId"] for row in engine._entry_stop_bindings],
                         [row["signalId"] for row in signals])
        self.assertAlmostEqual(engine._entry_stop_bindings[0]["entryDistance"], .4)
        self.assertAlmostEqual(engine._entry_stop_bindings[1]["entryDistance"], .2)
        self.assertEqual(engine._stop_observations[-1]["signalId"], signals[1]["signalId"])
        self.assertEqual(engine._position_stops[SYMBOL]["entryDate"], data["calendar"][31])

    def test_partial_forced_sells_retry_original_order_without_rebinding(self):
        data = fixture()
        set_bar(data, 26, close=9.4)
        for index in range(26, 30):
            set_bar(data, index, volume=3000)
        engine = account(data, slippageBps=0)
        engine.step(2)
        original = deepcopy(engine.state["forcedExits"][SYMBOL])
        engine.step(1)
        self.assertEqual(engine.state["trades"][-1]["positionAction"], "reduce")
        self.assertEqual(engine.state["forcedExits"][SYMBOL], original)
        self.assertEqual(engine._stop_observations[-1]["status"], "skippedExistingExit")
        self.assertEqual(engine._stop_observations[-1]["existingExitReason"], "stopLoss")
        engine.step(20000)
        sells = [row for row in engine.state["trades"] if row["side"] == "sell"]
        self.assertGreater(len(sells), 1)
        self.assertTrue(all(row["signalDate"] == original["signalDate"] and row["riskEvidence"] == original["riskEvidence"] for row in sells))
        self.assertEqual(len(engine._entry_stop_bindings), 1)
        self.assertFalse(engine._position_stops)

    def test_missing_fifth_day_not_retried_on_sixth_and_every_held_day_logged(self):
        for missing in (True, False):
            data = fixture()
            if missing:
                data["securities"][0]["bars"].pop(30)
            else:
                set_bar(data, 30, volume=0)
            set_bar(data, 31, close=9.9)
            engine = account(data, tr=1., slippageBps=0)
            engine.step(20000)
            self.assertFalse(engine.state["closedTrades"])
            fifth = [row for row in engine._stop_observations if row["holdingDays"] == 5][0]
            self.assertEqual(fifth["status"], "unknown")
            self.assertEqual(len(engine._stop_observations), len(data["calendar"]) - 26)
            self.assertEqual(len(engine._extra_risk_observations), 1)

    def test_preclose_reference_conversion_scales_frozen_fraction_not_fresh_tr(self):
        data = fixture()
        day = data["calendar"][27]
        data["securities"][0]["precloseByDate"][day] = 5.
        set_bar(data, 27, opening=5., close=5.)
        engine = account(data, tr=.2, slippageBps=0)
        engine.step(3)
        entry, current = engine._entry_stop_bindings[0], engine._stop_observations[-1]
        self.assertAlmostEqual(entry["entryRiskPrice"], 10.)
        self.assertAlmostEqual(entry["entryDistance"], .4)
        self.assertAlmostEqual(current["currentRiskEntryPrice"], 5.)
        self.assertAlmostEqual(current["effectivePct"], .04)
        self.assertAlmostEqual(current["currentRiskDistance"], .2)
        self.assertAlmostEqual(current["threshold"], 4.8)
        self.assertFalse(engine.state["forcedExits"])
        self.assertTrue(engine.state["warnings"])

    def test_future_asof_wrong_identity_and_window_rejected(self):
        data = fixture()
        signals = schedule(data)
        for field, value in (("asOfDate", data["calendar"][25]), ("asOfDate", data["calendar"][26]),
                             ("symbol", "sh600002"), ("signalId", "wrong"),
                             ("windowDates", data["calendar"][5:26])):
            values = inputs(data, signals)
            values[signals[0]["signalId"]][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                account(data, signals=signals, supplied=values)

    def test_mode_config_and_input_mutations_rejected(self):
        for mutation in ("config", "input", "mode"):
            engine = account(fixture())
            if mutation == "config":
                engine.config["stopLossPct"] = .06
            elif mutation == "input":
                engine.volatility_inputs["synthetic:25"]["meanTrueRange20"] = .3
            else:
                engine.mode = "fixed5"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                engine.step(1)
        for changed in ({"stopLossPct": .06}, {"trailingStopPct": .2}, {"takeProfitPct": .3},
                        {"maxHoldingDays": 5}, {"maxStockWeight": .3}):
            with self.assertRaises(ValueError):
                account(fixture(), **changed)

    def test_dynamic_declared_actions_rejected(self):
        data = fixture()
        data["securities"][0]["corporateActions"] = [{"notExecuted": True}]
        with self.assertRaisesRegex(ValueError, "corporateActions"):
            account(data)

    def test_day_rollback_covers_bindings_all_observations_fees_and_restore_rejected(self):
        data = fixture()
        set_bar(data, 26, close=9.4)
        engine = account(data)
        engine.step(1)
        before = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("after risk synthetic failure")):
            with self.assertRaises(RuntimeError):
                engine.step(1)
        self.assertEqual(engine.checkpoint(), before)
        self.assertFalse(engine._entry_stop_bindings)
        self.assertFalse(engine._stop_observations)
        self.assertFalse(engine._position_stops)
        self.assertFalse(before["checkpointRestoreSupported"])
        with self.assertRaisesRegex(ValueError, "restore is not supported"):
            VolatilityStopAccount(data, schedule(data), configuration(data), volatility_inputs=inputs(data, schedule(data)),
                mode="meanTR20x2", entry_weight=.2, fee_policy=LegacyFeePolicy(), state=before)
        engine.step(1)
        self.assertEqual(len(engine._entry_stop_bindings), 1)
        self.assertEqual(len(engine._stop_observations), 1)


if __name__ == "__main__":
    unittest.main()
