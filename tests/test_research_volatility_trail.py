"""Boundary and full synthetic-account checks; no historical data is loaded."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from service.research_account_fees import LegacyFeePolicy
from service.research_fixed_signal_account import FixedSignalAccount
from service.research_volatility_trail import VolatilityTrailAccount, prior_mean_tr_pct20

SYMBOL = "sh600001"


def fixture(length=48):
    days, cursor = [], date(2023, 6, 1)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    bars = [{"date": day, "open": 10., "high": 10.05, "low": 9.95, "close": 10.,
             "volume": 1_000_000, "amount": 10_000_000.} for day in days]
    return {"calendar": days, "calendarSource": "synthetic-independent", "adjustment": "raw",
            "securities": [{"symbol": SYMBOL, "bars": bars}]}


def set_bar(data, index, *, close=None, opening=None, volume=None, high=None, low=None):
    bar = data["securities"][0]["bars"][index]
    for field, value in (("close", close), ("open", opening), ("volume", volume)):
        if value is not None:
            bar[field] = value
    bar["high"] = max(bar["high"], bar["close"], bar["open"]) if high is None else high
    bar["low"] = min(bar["low"], bar["close"], bar["open"]) if low is None else low


def schedule(data, indexes=(25,)):
    return [{"signalId": f"synthetic:{i}", "strategyId": "fixed", "symbol": SYMBOL,
             "signalDate": data["calendar"][i], "originalRank": 1} for i in indexes]


def configuration(data, **changes):
    return {"startDate": data["calendar"][25], "endDate": data["calendar"][-1],
        "initialCapital": 50000, "topN": 5, "maxPositions": 5, "maxStockWeight": .2,
        "rebalanceMode": "risk", "rebalanceEvery": 1, "stopLossPct": .05,
        "trailingStopPct": 0, "takeProfitPct": 0, "maxHoldingDays": 0,
        "commissionRate": .0003, "minCommission": 5, "transferFeeRate": .00001,
        "stampDutyRate": .001, "slippageBps": 0, "maxParticipationRate": .1,
        "strategies": [{"id": "fixed", "kind": "momentum", "pool": "base"}],
        "allocation": {"mode": "single", "strategyIds": ["fixed"]},
        "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0},
        **changes}


def account(data, signals=None, **changes):
    return VolatilityTrailAccount(data, schedule(data) if signals is None else signals,
        configuration(data, **changes), entry_weight=.2, fee_policy=LegacyFeePolicy())


def window(data, index=26):
    dates = data["calendar"][max(0, index - 21):index]
    security = data["securities"][0]
    bars = {r["date"]: r for r in security["bars"]}
    return prior_mean_tr_pct20(data["calendar"][index], dates, [bars.get(d) for d in dates], security.get("precloseByDate", {}))


class VolatilityTrailTests(unittest.TestCase):
    def test_simple_normalized_mean_and_explicit_reference(self):
        data = fixture()
        data["securities"][0]["precloseByDate"] = {data["calendar"][25]: 9.}
        before = deepcopy(data)
        result = window(data)
        expected_last = max(10.05 - 9.95, abs(10.05 - 9.), abs(9.95 - 9.)) / 9.
        expected_other = (10.05 - 9.95) / 10.
        self.assertAlmostEqual(result["meanTRPct20"], (19 * expected_other + expected_last) / 20)
        self.assertEqual(result["windowStatus"], "known")
        self.assertEqual(result["trTerms"][-1]["referenceSource"], "precloseByDate")
        self.assertEqual(result["trTerms"][0]["referenceSource"], "previousCalendarClose")
        self.assertEqual(data, before)

    def test_all_twenty_one_slots_required_and_missing_not_compressed(self):
        for missing_index in (5, 15, 25):
            data = fixture()
            data["securities"][0]["bars"].pop(missing_index)
            got = window(data)
            self.assertEqual(got["windowStatus"], "unknown")
            self.assertEqual(got["windowDates"], data["calendar"][5:26])
            self.assertEqual(len(got["trTerms"]), 20)
            self.assertIsNone(got["meanTRPct20"])
        self.assertEqual(window(fixture(), 15)["windowStatus"], "unknown")

    def test_invalid_ohlcv_envelope_and_explicit_reference_do_not_fallback(self):
        for field, value in (("volume", 0), ("close", True), ("high", float("inf")), ("low", 11.)):
            data = fixture(); data["securities"][0]["bars"][10][field] = value
            self.assertEqual(window(data)["windowStatus"], "unknown")
        for value in (None, 0, -1, True, float("nan")):
            data = fixture(); data["securities"][0]["precloseByDate"] = {data["calendar"][25]: value}
            got = window(data)
            self.assertEqual(got["windowStatus"], "unknown")
            self.assertEqual(got["trTerms"][-1]["referenceSource"], "precloseByDate")
            self.assertIsNone(got["trTerms"][-1]["referenceValue"])

    def test_current_future_excluded_and_bad_date_alignment_rejected(self):
        data = fixture(); expected = window(data)
        for i in range(26, len(data["calendar"])):
            set_bar(data, i, close=20., high=21.)
        self.assertEqual(window(data), expected)
        days = data["calendar"][5:26]; bars = data["securities"][0]["bars"][5:26]
        for invalid in (days[::-1], days[:-1] + [days[-2]], days[:-1] + [data["calendar"][26]]):
            with self.assertRaises(ValueError):
                prior_mean_tr_pct20(data["calendar"][26], invalid, bars, {})

    def test_zero_or_excessive_width_is_invalid_distance(self):
        for low, high in ((10., 10.), (1., 19.)):
            data = fixture()
            for bar in data["securities"][0]["bars"]:
                bar.update(low=low, high=high)
            got = window(data)
            self.assertEqual(got["windowStatus"], "invalidDistance")
            self.assertIsNotNone(got["meanTRPct20"])

    def test_price_scale_invariance_of_normalized_window(self):
        data = fixture(); data["securities"][0]["precloseByDate"] = {data["calendar"][25]: 9.}
        scaled = deepcopy(data)
        for bar in scaled["securities"][0]["bars"]:
            for key in ("open", "high", "low", "close"):
                bar[key] *= 4
        for day in scaled["securities"][0]["precloseByDate"]:
            scaled["securities"][0]["precloseByDate"][day] *= 4
        self.assertAlmostEqual(window(data)["meanTRPct20"], window(scaled)["meanTRPct20"])

    def test_entry_binding_daily_logs_and_no_fifth_day_exit(self):
        data = fixture(); original = deepcopy(data)
        engine = account(data); engine.step(20000)
        evidence = engine.result()["research"]["volatilityTrail"]
        self.assertEqual(len(evidence["observations"]), len(data["calendar"]) - 26)
        self.assertEqual(evidence["entryBindings"][0]["signalId"], schedule(data)[0]["signalId"])
        self.assertFalse(engine.state["closedTrades"])
        self.assertEqual(sum(evidence["observationCounts"].values()), len(evidence["observations"]))
        self.assertEqual(sum(evidence["windowCounts"].values()), len(evidence["observations"]))
        self.assertEqual(data, original)

    def test_wider_volatility_cannot_loosen_established_ratchet(self):
        data = fixture(); set_bar(data, 26, close=10.8); set_bar(data, 27, close=10.7)
        engine = account(data); engine.step(3)
        first, second = engine._trail_observations
        self.assertGreater(second["window"]["meanTRPct20"], first["window"]["meanTRPct20"])
        self.assertLess(second["candidateRatio"], first["ratchetRatio"])
        self.assertEqual(second["ratchetRatio"], first["ratchetRatio"])

    def test_unknown_window_with_existing_floor_can_trigger(self):
        data = fixture(); set_bar(data, 27, volume=0); set_bar(data, 28, close=9.69)
        engine = account(data); engine.step(4)
        unavailable, crossed = engine._trail_observations[-2:]
        self.assertEqual(unavailable["decisionStatus"], "unknown")
        self.assertEqual(crossed["window"]["windowStatus"], "unknown")
        self.assertTrue(crossed["usedCarriedThreshold"])
        self.assertEqual(crossed["decisionStatus"], "triggered")
        self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], "volatilityTrail")

    def test_no_previous_floor_and_unknown_window_retains_initial_stop_only(self):
        data = fixture(); set_bar(data, 20, volume=0); set_bar(data, 26, close=9.6)
        engine = account(data); engine.step(2)
        row = engine._trail_observations[-1]
        self.assertEqual(row["decisionStatus"], "unknown")
        self.assertFalse(row["thresholdKnown"])
        self.assertIsNone(row["ratchetRatio"])
        self.assertFalse(engine.state["forcedExits"])

    def test_current_missing_or_zero_volume_neither_updates_nor_triggers(self):
        for missing in (False, True):
            data = fixture()
            if missing:
                data["securities"][0]["bars"].pop(27)
            else:
                set_bar(data, 27, close=10.8, volume=0)
            engine = account(data); engine.step(3)
            old, current = engine._trail_observations
            self.assertEqual(current["decisionStatus"], "unknown")
            self.assertEqual(current["ratchetRatio"], old["ratchetRatio"])
            self.assertIsNone(current["candidateRatio"])
            self.assertFalse(engine.state["forcedExits"])

    def test_initial_stop_and_mandatory_exits_take_priority(self):
        for kind, reason in (("stop", "stopLoss"), ("member", "baseEligibilityLost"), ("delist", "delisted"), ("st", "historicalST")):
            data = fixture(); day = data["calendar"][27]; set_bar(data, 27, close=9.4)
            if kind == "member": data["securities"][0]["memberByDate"] = {day: False}
            if kind == "delist": data["securities"][0]["delistDate"] = day
            if kind == "st": data["securities"][0]["stByDate"] = {day: True}
            engine = account(data); engine.step(3)
            row = engine._trail_observations[-1]
            self.assertEqual(row["decisionStatus"], "skippedStandardExit")
            self.assertEqual(row["exitReason"], reason)
            self.assertEqual(row["ratchetRatio"], row["previousRatio"])
            self.assertIsNone(row["candidateRatio"])

    def test_threshold_equality_triggers_and_next_open_t_plus_one_is_inherited(self):
        data = fixture(); threshold = 10. * (1 - window(data)["scaledDistancePct"])
        set_bar(data, 26, close=threshold); set_bar(data, 27, opening=9.6, close=9.6)
        engine = account(data); engine.step(2)
        self.assertEqual(engine._trail_observations[-1]["observedClose"], engine._trail_observations[-1]["threshold"])
        self.assertEqual([r["side"] for r in engine.state["trades"]], ["buy"])
        self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], "volatilityTrail")
        engine.step(1)
        sell = engine.state["trades"][-1]
        self.assertEqual((sell["date"], sell["signalDate"], sell["price"]), (data["calendar"][27], data["calendar"][26], 9.6))
        self.assertFalse(engine._trail_positions)

    def test_blocked_partial_retries_preserve_first_exit_and_binding(self):
        data = fixture(); set_bar(data, 26, close=9.69)
        for index in range(26, 33): set_bar(data, index, volume=3000)
        set_bar(data, 27, volume=0)
        engine = account(data); engine.step(2)
        order = deepcopy(engine.state["forcedExits"][SYMBOL])
        engine.step(3)
        self.assertEqual(engine.state["trades"][-1]["positionAction"], "reduce")
        self.assertEqual(engine.state["forcedExits"][SYMBOL], order)
        self.assertEqual(engine._trail_observations[-1]["decisionStatus"], "skippedExistingExit")
        self.assertEqual(len(engine._entry_bindings), 1)
        engine.step(20000)
        sells = [r for r in engine.state["trades"] if r["side"] == "sell"]
        self.assertGreater(len(sells), 1)
        self.assertTrue(all(r["riskEvidence"] == order["riskEvidence"] and r["signalDate"] == order["signalDate"] for r in sells))
        self.assertFalse(engine._trail_positions)

    def test_preclose_conversion_scales_price_floor_but_preserves_ratio(self):
        data = fixture(); day = data["calendar"][27]
        data["securities"][0]["precloseByDate"] = {day: 5.}
        set_bar(data, 27, close=5., opening=5., high=5.025, low=4.975)
        engine = account(data); engine.step(3)
        prior, current = engine._trail_observations
        self.assertAlmostEqual(current["riskEntryPrice"], 5.)
        self.assertEqual(current["ratchetRatio"], prior["ratchetRatio"])
        self.assertAlmostEqual(current["threshold"], prior["threshold"] / 2)
        self.assertFalse(engine.state["forcedExits"])
        self.assertTrue(engine.state["unaccountedActionExposure"])

    def test_reentry_resets_floor_and_uses_new_signal(self):
        data = fixture(); set_bar(data, 26, close=9.69)
        engine = account(data, schedule(data, (25, 31))); engine.step(8)
        self.assertEqual(len(engine._entry_bindings), 2)
        new = engine._trail_observations[-1]
        self.assertEqual(new["signalId"], "synthetic:31")
        self.assertEqual(new["entryDate"], data["calendar"][32])
        self.assertIsNone(new["previousRatio"])

    def test_day_rollback_restores_floor_logs_fills_and_fees_and_no_restore(self):
        data = fixture(); engine = account(data); engine.step(1)
        before = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("synthetic after-risk failure")):
            with self.assertRaises(RuntimeError): engine.step(1)
        self.assertEqual(engine.checkpoint(), before)
        self.assertFalse(engine._trail_positions)
        self.assertFalse(engine._entry_bindings)
        self.assertFalse(engine._trail_observations)
        engine.step(1)
        held = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("held rollback")):
            with self.assertRaises(RuntimeError): engine.step(1)
        self.assertEqual(engine.checkpoint(), held)
        with self.assertRaisesRegex(ValueError, "restore is not supported"):
            VolatilityTrailAccount(data, schedule(data), configuration(data), entry_weight=.2, fee_policy=LegacyFeePolicy(), state=held)

    def test_configuration_parameter_mutation_and_declared_actions_rejected(self):
        for changed in ({"stopLossPct": .06}, {"trailingStopPct": .1}, {"takeProfitPct": .2}, {"maxHoldingDays": 5}, {"maxStockWeight": .3}):
            with self.assertRaises(ValueError): account(fixture(), **changed)
        engine = account(fixture()); engine.config["stopLossPct"] = .06
        with self.assertRaises(ValueError): engine.step(1)
        engine = account(fixture())
        with patch("service.research_volatility_trail.MULTIPLIER", 4):
            with self.assertRaises(ValueError): engine.step(1)
        for top_level in (True, False):
            data = fixture(); target = data if top_level else data["securities"][0]
            target["corporateActions"] = [{"notExecuted": True}]
            with self.assertRaisesRegex(ValueError, "corporateActions"): account(data)

    def test_future_extension_preserves_earlier_observations_and_trades(self):
        full = fixture(); short = deepcopy(full)
        short["calendar"] = short["calendar"][:30]; short["securities"][0]["bars"] = short["securities"][0]["bars"][:30]
        a, b = account(short), account(full)
        a.step(20000); b.step(5)
        self.assertEqual(a._trail_observations, b._trail_observations)
        self.assertEqual(a.state["trades"], b.state["trades"])
        self.assertEqual(a.state["curve"], b.state["curve"])

    def test_no_trigger_economics_equal_frozen_stop_only_account(self):
        data = fixture(); config = configuration(data)
        baseline = FixedSignalAccount(deepcopy(data), schedule(data), config, entry_weight=.2, fee_policy=LegacyFeePolicy())
        trial = account(deepcopy(data)); baseline.step(20000); trial.step(20000)
        self.assertEqual(trial.state, baseline.state)
        self.assertEqual(trial.fee_ledger.checkpoint(), baseline.fee_ledger.checkpoint())


if __name__ == "__main__":
    unittest.main()
