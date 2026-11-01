"""Small synthetic exit paths only; no real market or strategy is evaluated."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from service.research_fixed_signal_account import LegacyFeePolicy
from service.research_structural_exits import ResearchExitAccount, _structure_check

SYMBOL = "sh600001"


def fixture(length=22):
    days, day = [], date(2023, 6, 1)
    while len(days) < length:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars = [{"date": d, "open": 10., "high": 10.2, "low": 9.8, "close": 10.,
             "volume": 1_000_000, "amount": 10_000_000.} for d in days]
    return {"calendar": days, "calendarSource": "synthetic-independent", "adjustment": "raw",
            "securities": [{"symbol": SYMBOL, "bars": bars,
                             "precloseByDate": {d: 10. for d in days}}]}


def account(data, rule="structure10", signal_index=10, **overrides):
    config = {"startDate": data["calendar"][signal_index], "endDate": data["calendar"][-1],
              "initialCapital": 10000, "topN": 5, "maxPositions": 1, "maxStockWeight": 1,
              "rebalanceMode": "risk", "rebalanceEvery": 1, "stopLossPct": .05,
              "trailingStopPct": 0 if rule == "structure10" else .1, "takeProfitPct": 0, "maxHoldingDays": 0,
              "commissionRate": 0, "minCommission": 0, "transferFeeRate": 0, "stampDutyRate": 0,
              "slippageBps": 0, "maxParticipationRate": .1,
              "strategies": [{"id": "fixed", "kind": "momentum", "pool": "base"}],
              "allocation": {"mode": "single", "strategyIds": ["fixed"]},
              "universe": {"strictHistory": False, "excludeSt": True, "minListingDays": 0, "minAmount20": 0}, **overrides}
    signals = [{"signalId": "synthetic", "strategyId": "fixed", "symbol": SYMBOL,
                "signalDate": data["calendar"][signal_index], "originalRank": 1}]
    return ResearchExitAccount(data, signals, config, exit_rule=rule, entry_weight=1., fee_policy=LegacyFeePolicy())


def prices(data, index, *, close=None, opening=None, low=None, volume=None):
    bar = data["securities"][0]["bars"][index]
    if close is not None:
        bar["close"] = close
    if opening is not None:
        bar["open"] = opening
    bar["high"] = max(bar["high"], bar["close"], bar["open"])
    bar["low"] = min(bar["low"], bar["close"], bar["open"]) if low is None else low
    if volume is not None:
        bar["volume"] = volume


class StructuralExitTests(unittest.TestCase):
    def test_structure_prior_ten_excludes_today_and_sell_is_next_open(self):
        data = fixture()
        prices(data, 11, close=9.7, low=9.0)
        engine = account(data)
        engine.step(2)
        self.assertEqual([r["side"] for r in engine.state["trades"]], ["buy"])
        pending = engine.state["forcedExits"][SYMBOL]
        self.assertEqual(pending["reason"], "structure10")
        self.assertEqual(pending["riskEvidence"]["prior10Low"], 9.8)
        self.assertEqual(pending["riskEvidence"]["priorDates"], data["calendar"][1:11])
        engine.step(1)
        self.assertEqual(engine.state["trades"][-1]["date"], data["calendar"][12])
        self.assertEqual(engine.state["trades"][-1]["signalDate"], data["calendar"][11])

    def test_strict_boundary_and_existing_stop_have_priority(self):
        for close, expected in ((9.8, None), (9.4, "stopLoss")):
            data = fixture()
            prices(data, 11, close=close)
            engine = account(data); engine.step(2)
            self.assertEqual(engine.state["forcedExits"].get(SYMBOL, {}).get("reason"), expected)
            record = engine.result()["research"]["structuralExits"]["observations"][-1]
            self.assertEqual(record["status"], "notTriggered" if expected is None else "skippedExistingExit")
        data = fixture(); prices(data, 11, close=11.); prices(data, 12, close=9.85)
        data["securities"][0]["precloseByDate"][data["calendar"][12]] = 11.
        engine = account(data, "noProgress5"); engine.step(3)
        self.assertEqual(engine.state["forcedExits"][SYMBOL]["reason"], "trailingStop")

    def test_missing_calendar_slot_zero_volume_and_invalid_low_are_unknown(self):
        for missing in (True, False):
            data = fixture(); prices(data, 11, close=9.7)
            if missing:
                data["securities"][0]["bars"].pop(5)
            else:
                prices(data, 5, volume=0)
            engine = account(data); engine.step(2)
            record = engine.result()["research"]["structuralExits"]["observations"][-1]
            self.assertEqual(record["status"], "unknown")
            self.assertIsNone(record["prior10Low"])
            self.assertFalse(engine.state["forcedExits"])
        data = fixture(); bars = deepcopy(data["securities"][0]["bars"][1:11])
        for bad in (None, 0, -1, float("nan"), True):
            bars[0]["low"] = bad
            check = _structure_check(data["calendar"][11], data["calendar"][1:11], data["securities"][0]["bars"][11], bars)
            self.assertEqual(check["status"], "unknown")
        # Whole-account source validation still rejects invalid OHLC at load.
        data["securities"][0]["bars"][5]["low"] = 0
        with self.assertRaises(ValueError):
            account(data)

    def test_future_low_append_does_not_change_current_exit(self):
        short, extended = fixture(13), fixture(24)
        for data in (short, extended):
            prices(data, 11, close=9.7)
        for i in range(12, 24):
            prices(extended, i, low=1.)
        a, b = account(short), account(extended)
        a.step(2); b.step(2)
        self.assertEqual(a.state["forcedExits"], b.state["forcedExits"])
        self.assertEqual(a._extra_risk_observations, b._extra_risk_observations)

    def test_no_progress_exact_fifth_day_equality_and_no_later_recheck(self):
        data = fixture(); engine = account(data, "noProgress5")
        engine.step(5)  # Entry at index11; this reaches index14, holding day4.
        self.assertFalse(engine.state["forcedExits"])
        engine.step(1)
        order = engine.state["forcedExits"][SYMBOL]
        self.assertEqual(order["signalDate"], data["calendar"][15])
        self.assertEqual(order["riskEvidence"]["holdingDays"], 5)
        self.assertEqual(order["riskEvidence"]["riskEntryPrice"], 10.)
        engine.step(1)
        self.assertEqual(engine.state["trades"][-1]["date"], data["calendar"][16])
        data = fixture(); prices(data, 15, close=10.01); prices(data, 16, close=9.9)
        engine = account(data, "noProgress5"); engine.step(20000)
        self.assertFalse(engine.state["closedTrades"])
        self.assertEqual(len(engine._extra_risk_observations), 1)
        self.assertEqual(engine._extra_risk_observations[0]["status"], "notTriggered")
        data = fixture(); prices(data, 15, close=10.005)
        engine = account(data, "noProgress5", slippageBps=10, commissionRate=.001, minCommission=5)
        engine.step(6)
        record = engine._extra_risk_observations[0]
        self.assertEqual(record["status"], "triggered")
        self.assertAlmostEqual(record["riskEntryPrice"], 10.01)
        position = engine.state["positions"][SYMBOL]
        self.assertGreater(position["costBasis"] / position["quantity"], record["riskEntryPrice"])

    def test_fifth_day_missing_or_zero_volume_is_not_deferred_to_day_six(self):
        for missing in (True, False):
            data = fixture(); prices(data, 16, close=9.9)
            if missing:
                data["securities"][0]["bars"].pop(15)
            else:
                prices(data, 15, volume=0)
            engine = account(data, "noProgress5"); engine.step(20000)
            self.assertEqual(len(engine._extra_risk_observations), 1)
            self.assertEqual(engine._extra_risk_observations[0]["status"], "unknown")
            self.assertEqual(engine._extra_risk_observations[0]["holdingDays"], 5)
            self.assertFalse(engine.state["closedTrades"])

    def test_queued_order_survives_blocked_open_and_keeps_original_reason(self):
        data = fixture(); prices(data, 11, close=9.7)
        prices(data, 12, volume=0)
        engine = account(data); engine.step(3)
        self.assertEqual(len(engine.state["trades"]), 1)
        original = deepcopy(engine.state["forcedExits"][SYMBOL])
        engine.step(1)  # Previous-volume capacity is zero, so still pending.
        self.assertEqual(engine.state["forcedExits"][SYMBOL], original)
        engine.step(1)
        self.assertEqual(engine.state["trades"][-1]["date"], data["calendar"][14])
        self.assertEqual(engine.state["trades"][-1]["reason"], "structure10")
        self.assertEqual(engine.result()["research"]["structuralExits"]["observationCounts"]["triggered"], 1)

    def test_explicit_modes_descriptive_checkpoint_and_day_rollback(self):
        data = fixture(); prices(data, 11, close=9.7)
        for kwargs in ({"trailingStopPct": .1}, {"stopLossPct": 0}, {"takeProfitPct": .3}, {"maxHoldingDays": 5}):
            with self.assertRaises(ValueError):
                account(data, **kwargs)
        engine = account(data); engine.step(1)
        before = engine.checkpoint()
        with patch.object(engine, "_select_targets", side_effect=RuntimeError("synthetic failure after risk")):
            with self.assertRaises(RuntimeError):
                engine.step(1)
        self.assertEqual(engine.checkpoint(), before)
        self.assertFalse(before["checkpointRestoreSupported"])
        with self.assertRaisesRegex(ValueError, "restore is not supported"):
            ResearchExitAccount(data, engine._schedule, engine.config, exit_rule="structure10", entry_weight=1.,
                                fee_policy=LegacyFeePolicy(), state=before)
        engine.step(1)
        self.assertEqual(engine.result()["research"]["structuralExits"]["observationCounts"]["triggered"], 1)


if __name__ == "__main__":
    unittest.main()
