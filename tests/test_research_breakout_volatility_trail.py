"""Synthetic anchor and full-account transition checks; no historical inputs."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from service.research_account_fees import LegacyFeePolicy
from service.research_fixed_signal_account import FixedSignalAccount
from service.research_breakout_volatility_trail import BreakoutVolatilityTrailAccount, build_formation_anchor

SYMBOL = "sh600001"


def fixture(length=48):
    days, cursor = [], date(2023, 6, 1)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return {"calendar": days, "calendarSource": "synthetic-independent", "adjustment": "raw",
            "securities": [{"symbol": SYMBOL, "bars": [
                {"date": day, "open": 10., "high": 10.05, "low": 9.95, "close": 10.,
                 "volume": 1_000_000, "amount": 10_000_000.} for day in days]}]}


def bar(data, index, *, close=None, opening=None, volume=None, high=None, low=None):
    row = data["securities"][0]["bars"][index]
    for field, value in (("close", close), ("open", opening), ("volume", volume)):
        if value is not None:
            row[field] = value
    row["high"] = max(row["high"], row["close"], row["open"]) if high is None else high
    row["low"] = min(row["low"], row["close"], row["open"]) if low is None else low


def config(data, **changes):
    return {"startDate": data["calendar"][25], "endDate": data["calendar"][-1],
        "initialCapital": 50000, "topN": 5, "maxPositions": 5, "maxStockWeight": .2,
        "rebalanceMode": "risk", "rebalanceEvery": 1, "stopLossPct": .05,
        "trailingStopPct": 0, "takeProfitPct": 0, "maxHoldingDays": 0,
        "commissionRate": .0003, "minCommission": 5, "transferFeeRate": .00001,
        "stampDutyRate": .001, "slippageBps": 0, "maxParticipationRate": .1,
        "strategies": [{"id": "fixed", "kind": "momentum", "pool": "base"}],
        "allocation": {"mode": "single", "strategyIds": ["fixed"]},
        "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0}, **changes}


def signals(data, indexes=(25,)):
    return [{"signalId": f"synthetic:{i}", "strategyId": "fixed", "symbol": SYMBOL,
             "signalDate": data["calendar"][i], "originalRank": 1} for i in indexes]


def account(data, indexes=(25,), **changes):
    return BreakoutVolatilityTrailAccount(data, signals(data, indexes), config(data, **changes),
                                          entry_weight=.2, fee_policy=LegacyFeePolicy())


def anchor(data, formation=25, entry=26):
    security = data["securities"][0]
    return build_formation_anchor(data["calendar"][formation], data["calendar"][entry], data["calendar"],
                                  {r["date"]: r for r in security["bars"]}, security.get("precloseByDate", {}))


def activated_path():
    data = fixture()
    bar(data, 26, close=10.3)
    bar(data, 27, close=10.2)
    return data


class BreakoutVolatilityTrailTests(unittest.TestCase):
    def test_anchor_uses_twenty_slots_strictly_before_formation(self):
        data = fixture(); bar(data, 5, close=10.8); bar(data, 4, close=20)
        bar(data, 25, close=15); bar(data, 26, close=16)
        before = deepcopy(data)
        got = anchor(data)
        self.assertEqual(got["priorDates"], data["calendar"][5:25])
        self.assertEqual(got["rawPrior20MaxClose"], 10.8)
        self.assertEqual(got["entryAnchorPrice"], 10.8)
        self.assertEqual(data, before)

    def test_missing_slot_short_history_and_invalid_quality_stay_unknown(self):
        for index in (5, 15, 24):
            data = fixture(); data["securities"][0]["bars"].pop(index)
            got = anchor(data)
            self.assertEqual(got["status"], "unknown")
            self.assertEqual(len(got["priorDates"]), 20)
            self.assertIsNone(got["entryAnchorPrice"])
        self.assertEqual(anchor(fixture(), 10, 11)["status"], "unknown")
        for field, value in (("close", True), ("volume", 0), ("high", float("inf")), ("low", 11)):
            data = fixture(); data["securities"][0]["bars"][15][field] = value
            self.assertEqual(anchor(data)["status"], "unknown")

    def test_internal_reference_change_is_unknown_but_first_slot_ignored(self):
        data = fixture(); days = data["calendar"]
        data["securities"][0]["precloseByDate"] = {days[5]: 0, days[10]: 10.001}
        self.assertEqual(anchor(data)["status"], "known")
        data["securities"][0]["precloseByDate"][days[10]] = 5
        got = anchor(data)
        self.assertEqual(got["status"], "unknown")
        self.assertIn("internalReferenceChange", [r["reason"] for r in got["issues"]])
        for bad in (None, 0, True, float("nan")):
            data["securities"][0]["precloseByDate"][days[10]] = bad
            self.assertEqual(anchor(data)["status"], "unknown")

    def test_formation_and_entry_reference_transport_is_chained(self):
        data = fixture(); days = data["calendar"]
        bar(data, 25, opening=5, close=5, high=5.1, low=4.9)
        bar(data, 26, opening=2.5, close=2.5, high=2.6, low=2.4)
        data["securities"][0]["precloseByDate"] = {days[25]: 5, days[26]: 2.5}
        got = anchor(data)
        self.assertEqual(got["entryAnchorPrice"], 2.5)
        self.assertEqual([r["scaleFactor"] for r in got["conversionSteps"]], [.5, .5])
        self.assertEqual(got["conversionDates"], days[25:27])

    def test_conversion_requires_current_previous_slots_and_valid_references(self):
        for missing in (24, 25, 26):
            data = fixture(); data["securities"][0]["bars"].pop(missing)
            self.assertEqual(anchor(data)["status"], "unknown")
        for value in (None, 0, -1, True, float("inf")):
            data = fixture(); data["securities"][0]["precloseByDate"] = {data["calendar"][26]: value}
            self.assertEqual(anchor(data)["status"], "unknown")
        data = fixture(); bar(data, 26, volume=0)
        self.assertEqual(anchor(data)["status"], "unknown")
        with self.assertRaises(ValueError):
            build_formation_anchor(data["calendar"][25], data["calendar"][24], data["calendar"], {}, {})
        with self.assertRaises(ValueError):
            build_formation_anchor(data["calendar"][25], data["calendar"][26], data["calendar"][::-1], {}, {})

    def test_entry_close_activates_with_fixed_seed_and_next_day_switches(self):
        data = activated_path(); engine = account(data); engine.step(3)
        first, second = engine._trail_observations
        self.assertTrue(first["activatedToday"])
        self.assertEqual((first["phaseBefore"], first["phaseAfter"]), ("fixed", "volatility"))
        self.assertEqual(first["activationDate"], data["calendar"][26])
        self.assertAlmostEqual(first["ratchetRatio"], .927)
        self.assertIsNone(first["volatilityCandidateRatio"])
        self.assertFalse(first["volatilityCandidateEvaluated"])
        self.assertTrue(second["volatilityCandidateEvaluated"])
        self.assertIsNone(second["fixedCandidateRatio"])
        self.assertGreater(second["ratchetRatio"], first["ratchetRatio"])

    def test_equal_anchor_never_activates_and_no_day_five_exit(self):
        data = fixture(); engine = account(data); engine.step(20000)
        detail = engine.result()["research"]["breakoutVolatilityTrail"]
        self.assertEqual(detail["activatedEntryCount"], 0)
        self.assertEqual(detail["entryCount"], 1)
        self.assertEqual(len(detail["observations"]), len(data["calendar"]) - 26)
        self.assertTrue(all(r["phaseAfter"] == "fixed" for r in detail["observations"]))
        self.assertFalse(engine.state["closedTrades"])

    def test_unknown_anchor_is_permanent_and_does_not_delete_entry(self):
        data = fixture(); bar(data, 6, volume=0)
        for i in range(26, len(data["calendar"])):
            bar(data, i, close=10.3)
        engine = account(data); engine.step(20000)
        self.assertEqual(len(engine.state["trades"]), 1)
        self.assertEqual(engine._entry_bindings[0]["anchor"]["status"], "unknown")
        self.assertTrue(all(r["phaseAfter"] == "fixed" and r["anchorRatio"] is None for r in engine._trail_observations))
        self.assertEqual(engine.result()["research"]["breakoutVolatilityTrail"]["unknownAnchorEntryCount"], 1)

    def test_fixed_stage_preserves_legacy_floating_equality_expression(self):
        data = fixture(); bar(data, 6, close=11)
        bar(data, 25, close=9.4); bar(data, 26, opening=9.4, close=10)
        bar(data, 27, close=9); bar(data, 28, close=8.99)
        engine = account(data); engine.step(3)
        boundary = engine._trail_observations[-1]
        self.assertEqual(boundary["observedClose"], 9)
        self.assertEqual(boundary["decisionStatus"], "notTriggered")
        engine.step(1)
        crossed = engine._trail_observations[-1]
        self.assertEqual(crossed["exitReason"], "trailingStop")
        self.assertEqual(crossed["activationStatus"], "skipped")
        self.assertFalse(crossed["activatedToday"])

    def test_inactive_trades_curve_fees_match_existing_fixed_trail(self):
        data = fixture(); bar(data, 6, close=11.5); bar(data, 26, close=10.8)
        for i in range(27, len(data["calendar"])):
            bar(data, i, opening=9.7, close=9.7)
        baseline = FixedSignalAccount(deepcopy(data), signals(data), config(data, trailingStopPct=.1),
                                      entry_weight=.2, fee_policy=LegacyFeePolicy())
        trial = account(deepcopy(data)); baseline.step(20000); trial.step(20000)
        for key in ("trades", "curve", "closedTrades", "positions", "cash", "totalFees", "totalSlippage"):
            self.assertEqual(trial.state[key], baseline.state[key], key)
        # The configs deliberately differ in ownership of trailing protection;
        # compare every financial quote/payment, excluding only config identity.
        left, right = trial.fee_ledger.checkpoint(), baseline.fee_ledger.checkpoint()
        for ledger in (left, right):
            ledger.pop("checkpointDigest")
            ledger["identity"].pop("configSha256")
            for quote in ledger["quotes"]:
                quote.pop("ledgerIdentitySha256")
            for paid in ledger["paidLedger"]:
                paid["quote"].pop("ledgerIdentitySha256")
        self.assertEqual(left, right)
        self.assertTrue(all(not r["activatedToday"] for r in trial._trail_observations))

    def test_unknown_volatility_carries_established_floor_and_can_trigger(self):
        data = activated_path(); bar(data, 28, volume=0, close=10.2); bar(data, 29, close=9.7)
        engine = account(data); engine.step(5)
        last = engine._trail_observations[-1]
        self.assertEqual(last["window"]["windowStatus"], "unknown")
        self.assertTrue(last["usedCarriedThreshold"])
        self.assertEqual(last["decisionStatus"], "triggered")
        self.assertEqual(last["exitReason"], "breakoutVolatilityTrail")

    def test_invalid_current_close_volume_or_missing_bar_freezes_state(self):
        for missing in (False, True):
            data = activated_path()
            if missing:
                data["securities"][0]["bars"].pop(28)
            else:
                bar(data, 28, close=10.9, volume=0)
            engine = account(data); engine.step(4)
            row = engine._trail_observations[-1]
            self.assertEqual(row["decisionStatus"], "unknown")
            self.assertEqual(row["ratchetRatio"], row["previousRatio"])
            self.assertFalse(row["volatilityCandidateEvaluated"])

    def test_standard_and_existing_risk_take_priority_without_switch(self):
        for kind, reason in (("stop", "stopLoss"), ("member", "baseEligibilityLost"), ("delist", "delisted"), ("st", "historicalST")):
            data = fixture(); day = data["calendar"][27]
            bar(data, 27, close=10.3)
            if kind == "stop": bar(data, 27, close=9.4)
            if kind == "member": data["securities"][0]["memberByDate"] = {day: False}
            if kind == "delist": data["securities"][0]["delistDate"] = day
            if kind == "st": data["securities"][0]["stByDate"] = {day: True}
            engine = account(data); engine.step(3)
            row = engine._trail_observations[-1]
            self.assertEqual((row["decisionStatus"], row["exitReason"]), ("skippedStandardExit", reason))
            self.assertFalse(row["activatedToday"])
            self.assertEqual(row["ratchetRatio"], row["previousRatio"])
            self.assertEqual(len(engine._entry_bindings), 1)
        # Even an entry-close initial stop retains the immutable anchor evidence.
        data = fixture(); bar(data, 26, close=9.4)
        engine = account(data); engine.step(2)
        self.assertEqual(len(engine._entry_bindings), 1)
        self.assertEqual(engine._trail_observations[-1]["decisionStatus"], "skippedStandardExit")
        self.assertIsNone(engine._trail_observations[-1]["ratchetRatio"])

    def test_active_exit_is_next_open_with_gap_and_t_plus_one(self):
        data = activated_path(); bar(data, 28, close=9.7); bar(data, 29, opening=9.6, close=9.6)
        engine = account(data); engine.step(4)
        self.assertEqual([r["side"] for r in engine.state["trades"]], ["buy"])
        self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], "breakoutVolatilityTrail")
        engine.step(1)
        sell = engine.state["trades"][-1]
        self.assertEqual((sell["date"], sell["signalDate"], sell["price"]), (data["calendar"][29], data["calendar"][28], 9.6))
        self.assertFalse(engine._trail_positions)

    def test_blocked_partial_exit_retries_preserve_original_order_and_phase(self):
        data = activated_path(); bar(data, 28, close=9.7)
        for i in range(26, 36): bar(data, i, volume=3000)
        bar(data, 29, volume=0)
        engine = account(data); engine.step(4)
        original = deepcopy(engine.state["forcedExits"][SYMBOL])
        engine.step(3)
        self.assertEqual(engine.state["trades"][-1]["positionAction"], "reduce")
        self.assertEqual(engine.state["forcedExits"][SYMBOL], original)
        self.assertEqual(engine._trail_observations[-1]["decisionStatus"], "skippedExistingExit")
        self.assertEqual(engine._trail_positions[SYMBOL]["activationDate"], data["calendar"][26])
        engine.step(20000)
        sells = [r for r in engine.state["trades"] if r["side"] == "sell"]
        self.assertGreater(len(sells), 1)
        self.assertTrue(all(r["riskEvidence"] == original["riskEvidence"] and r["signalDate"] == original["signalDate"] for r in sells))

    def test_inherited_reference_conversion_scales_anchor_and_floor_together(self):
        data = activated_path(); days = data["calendar"]
        bar(data, 27, close=10.3, volume=0)
        bar(data, 28, opening=5.15, close=5.15, high=5.175, low=5.125)
        data["securities"][0]["precloseByDate"] = {days[28]: 5.15}
        engine = account(data); engine.step(4)
        first, current = engine._trail_observations[0], engine._trail_observations[-1]
        self.assertEqual(current["anchorRatio"], first["anchorRatio"])
        self.assertEqual(current["ratchetRatio"], first["ratchetRatio"])
        self.assertAlmostEqual(current["anchorPrice"], first["anchorPrice"] / 2)
        self.assertAlmostEqual(current["threshold"], first["threshold"] / 2)
        self.assertTrue(engine.state["unaccountedActionExposure"])
        self.assertFalse(engine.state["forcedExits"])

    def test_reentry_gets_new_anchor_and_resets_phase_and_floor(self):
        data = activated_path(); bar(data, 28, close=9.7)
        engine = account(data, (25, 35)); engine.step(12)
        self.assertEqual(len(engine._entry_bindings), 2)
        row = engine._trail_observations[-1]
        self.assertEqual(row["signalId"], "synthetic:35")
        self.assertEqual(row["entryDate"], data["calendar"][36])
        self.assertEqual(row["phaseBefore"], "fixed")
        self.assertIsNone(row["previousRatio"])
        self.assertIsNone(row["activationDate"])

    def test_transaction_rollback_covers_binding_switch_logs_fees_and_fills(self):
        data = activated_path(); engine = account(data); engine.step(1)
        before = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("after risk")):
            with self.assertRaises(RuntimeError): engine.step(1)
        self.assertEqual(engine.checkpoint(), before)
        self.assertFalse(engine._entry_bindings)
        engine.step(1); held = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("held failure")):
            with self.assertRaises(RuntimeError): engine.step(1)
        self.assertEqual(engine.checkpoint(), held)

    def test_future_changes_and_extension_preserve_earlier_evidence(self):
        full = activated_path(); before = deepcopy(full); short = deepcopy(full)
        short["calendar"] = short["calendar"][:30]; short["securities"][0]["bars"] = short["securities"][0]["bars"][:30]
        for i in range(30, len(full["calendar"])): bar(full, i, close=20)
        self.assertEqual(anchor(full), anchor(short))
        a, b = account(short), account(full); a.step(20000); b.step(5)
        self.assertEqual(a._trail_observations, b._trail_observations)
        self.assertEqual(a.state["trades"], b.state["trades"])
        self.assertEqual(a.state["curve"], b.state["curve"])
        clean = account(before); clean.step(3)
        self.assertEqual(before, activated_path())

    def test_config_mutation_declared_actions_and_restore_rejected(self):
        for changes in ({"stopLossPct": .06}, {"trailingStopPct": .1}, {"takeProfitPct": .2}, {"maxHoldingDays": 5}):
            with self.assertRaises(ValueError): account(fixture(), **changes)
        engine = account(fixture()); engine.config["stopLossPct"] = .06
        with self.assertRaises(ValueError): engine.step(1)
        engine = account(fixture())
        with patch("service.research_breakout_volatility_trail.FORMATION_SLOTS", 19):
            with self.assertRaises(ValueError): engine.step(1)
        for top in (False, True):
            data = fixture(); target = data if top else data["securities"][0]
            target["corporateActions"] = [{"notExecuted": True}]
            with self.assertRaisesRegex(ValueError, "corporateActions"): account(data)
        data = fixture(); engine = account(data)
        with self.assertRaisesRegex(ValueError, "restore is not supported"):
            BreakoutVolatilityTrailAccount(data, signals(data), config(data), entry_weight=.2,
                                           fee_policy=LegacyFeePolicy(), state=engine.checkpoint())


if __name__ == "__main__":
    unittest.main()
