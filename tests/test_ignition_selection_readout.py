"""Independent synthetic attribution/slot/bootstrap checks; no market accounts."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ignition_selection_readout", ROOT / "scripts/summarize-ignition-selection-review.py")
readout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readout)


def fixture():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
    metadata = {
        "a": {"symbol": "same", "signalDate": dates[0], "sectorCode": "sector", "originalRank": 1, "pattern": "A_ignition"},
        "unused": {"symbol": "other", "signalDate": dates[1], "sectorCode": "sector", "originalRank": 1, "pattern": "A_ignition"},
        "b": {"symbol": "same", "signalDate": dates[3], "sectorCode": "sector", "originalRank": 1, "pattern": "B_corePullback"}}
    proof = {"rows": [{"originSlotId": f'{m["signalDate"]}|sector|1', "selectedSignalId": sid,
                       "originSignalId": sid, "pattern": m["pattern"], "selectedSymbol": m["symbol"]} for sid, m in metadata.items()]}

    def fill(day, side, quantity, price, after, sid=None):
        value = {"date": day, "symbol": "same", "side": side, "quantity": quantity, "price": price,
                 "quantityAfter": after, "amount": quantity * price, "fee": 1., "slippage": 0.}
        if sid:
            value["signalId"] = sid
        return value

    a_buy = fill(dates[1], "buy", 10, 10., 10, "a")
    partial = fill(dates[2], "sell", 5, 12., 5)
    a_close = fill(dates[3], "sell", 5, 11., 0)
    b_buy = fill(dates[4], "buy", 4, 20., 4, "b")
    trade_days = [[], [a_buy], [partial], [a_close], [b_buy]]
    holding_days = [[], [{"symbol": "same", "marketValue": 110.}], [{"symbol": "same", "marketValue": 65.}], [], [{"symbol": "same", "marketValue": 88.}]]
    equities = [1000., 1009., 1023., 1012., 1019.]
    curve = [{"date": day, "equity": e, "marketValue": sum(h["marketValue"] for h in holdings),
              "drawdownPct": (1 - e / max([1000., *equities[:i+1]])) * 100} for i, (day, e, holdings) in enumerate(zip(dates, equities, holding_days))]
    result = {"status": "completed", "config": {"initialCapital": 1000.}, "curve": curve,
        "daily": [{"date": d, "fills": f, "holdings": h} for d, f, h in zip(dates, trade_days, holding_days)],
        "trades": [a_buy, partial, a_close, b_buy],
        "closedTrades": [{"symbol": "same", "entryDate": dates[1], "exitDate": dates[3],
                          "pnlAmount": 12., "investedAmount": 101., "returnPct": 12 / 101 * 100, "holdingDays": 3}],
        "positions": [{"symbol": "same", "entryDate": dates[4], "marketValue": 88., "costBasis": 81., "realizedPnl": 0., "priceDate": dates[4]}],
        "research": {"opportunities": [{"signalId": sid, "status": "expired" if sid == "unused" else "filled",
                                          "reason": "openingTooExtended" if sid == "unused" else "filled", "stopRiskBudget": 5.} for sid in metadata]}}
    return result, {"metadata": metadata}, proof


def normal_rule():
    return {"netReturnPositive": True, "beatMatchedBaselineNetReturn": True,
            "aMeanNetReturnPositiveAndAboveBaseline": True, "aProfitFactorAboveOneAndBaseline": True,
            "aTurnoverAndCapitalDayEfficiencyPositiveAndAboveBaseline": True,
            "minimumAClosedTrades": 30, "minimumFilledAEntryRatioToBaseline": .8,
            "minimumABuyAmountRatioToBaseline": .8, "minimumACapitalDayRatioToBaseline": .8,
            "maximumDrawdownPct": 20}


class AttributionTests(unittest.TestCase):
    def test_partial_sells_and_same_symbol_later_b_keep_entry_ownership(self):
        result, system, proof = fixture()
        original = deepcopy((result, system, proof))
        m = readout.derive_account(result, system, proof)
        self.assertEqual((result, system, proof), original)
        a, b = m["paths"]["A_ignition"], m["paths"]["B_corePullback"]
        self.assertEqual(a["closed"]["netPnl"], 12.)
        self.assertEqual(a["buyAmount"], 100.)
        self.assertEqual(a["sellAmount"], 115.)
        self.assertEqual(a["fees"], 3.)
        self.assertEqual(a["capitalDays"], 175.)
        self.assertNotEqual(a["capitalDays"], 101 * 3)
        self.assertEqual(b["capitalDays"], 88.)
        self.assertEqual(b["closed"]["count"], 0)
        self.assertEqual(b["endUnrealizedPnl"], 7.)
        self.assertEqual(b["netPnlIncludingEndMtm"], 7.)
        self.assertEqual(m["pnlBridge"]["unallocatedResidual"], 0.)

    def test_unfilled_slot_zero_never_in_closed_expectancy_or_profit_factor(self):
        m = readout.derive_account(*fixture())
        a = m["paths"]["A_ignition"]
        self.assertEqual(a["intendedSlots"], 2)
        self.assertEqual(a["filledEntries"], 1)
        self.assertEqual(a["closed"]["count"], 1)
        self.assertEqual(a["closed"]["meanNetReturnPct"], 12 / 101 * 100)
        self.assertEqual(a["closed"]["profitFactorStatus"], "positiveNoLoss")
        self.assertIsNone(a["closed"]["cashProfitFactor"])
        pair = readout.paired_slots(m, deepcopy(m))
        self.assertEqual(pair["slots"], 2)
        self.assertEqual(pair["bothUnfilled"], 1)
        self.assertEqual(pair["meanNetPnlDifferencePerIntendedSlot"], 0.)

    def test_changed_no_fill_slot_is_counted_and_b_identity_checked(self):
        baseline = readout.derive_account(*fixture())
        candidate = deepcopy(baseline)
        unused = next(s for s in candidate["slots"] if s["signalId"] == "unused")
        unused["symbol"] = "replacement"
        pair = readout.paired_slots(candidate, baseline)
        self.assertEqual(pair["changedSymbols"], 1)
        self.assertEqual(pair["bothUnfilled"], 1)
        self.assertEqual(pair["totalNetPnlDifference"], 0.)
        next(s for s in candidate["slots"] if s["pattern"] == "B_corePullback")["symbol"] = "wrong"
        with self.assertRaisesRegex(ValueError, "B frozen"):
            readout.paired_slots(candidate, baseline)

    def test_unallocated_terminal_residual_is_not_assigned_to_either_path(self):
        r, s, p = fixture()
        r["curve"][-1]["equity"] += 3.
        m = readout.derive_account(r, s, p)
        self.assertEqual(m["pnlBridge"]["unallocatedResidual"], 3.)
        self.assertEqual(m["paths"]["B_corePullback"]["netPnlIncludingEndMtm"], 7.)

    def test_unknown_owner_or_duplicate_closed_episode_fails(self):
        r, s, p = fixture()
        r["daily"][0]["holdings"] = [{"symbol": "unowned", "marketValue": 10.}]
        with self.assertRaisesRegex(ValueError, "owner"):
            readout.derive_account(r, s, p)
        r, s, p = fixture()
        r["closedTrades"].append(deepcopy(r["closedTrades"][0]))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            readout.derive_account(r, s, p)


class GatesAndBootstrapTests(unittest.TestCase):
    def test_all_participation_floors_and_positive_expectancy_required(self):
        baseline = readout.derive_account(*fixture())
        candidate = deepcopy(baseline)
        for obj, level in ((baseline, 1.), (candidate, 2.)):
            obj["account"].update(netReturnPct=level, maxDrawdownPct=10.)
            a = obj["paths"]["A_ignition"]
            a.update(filledEntries=40, buyAmount=1000., capitalDays=2000., turnoverEfficiencyBps=level, capitalDayEfficiencyBps=level)
            a["closed"].update(count=30, meanNetReturnPct=level, grossProfit=level * 200, grossLoss=100)
        self.assertTrue(readout.gates(candidate, baseline, normal_rule())["passed"])
        for metric, flag in (("filledEntries", "minimumFilledAEntryRatioToBaseline"), ("buyAmount", "minimumABuyAmountRatioToBaseline"), ("capitalDays", "minimumACapitalDayRatioToBaseline")):
            altered = deepcopy(candidate)
            altered["paths"]["A_ignition"][metric] = baseline["paths"]["A_ignition"][metric] * .799
            result = readout.gates(altered, baseline, normal_rule())
            self.assertFalse(result["checks"][flag])
            self.assertFalse(result["passed"])
        candidate["paths"]["A_ignition"]["closed"]["meanNetReturnPct"] = -.01
        self.assertFalse(readout.gates(candidate, baseline, normal_rule())["passed"])

    def test_stress_uses_same_cost_baseline_and_every_frozen_condition(self):
        baseline = readout.derive_account(*fixture())
        candidate = deepcopy(baseline)
        rule = {"netReturnPositive": True, "beatSameCostBaseline": True, "aMeanNetReturnPositive": True, "aProfitFactorAboveOne": True}
        candidate["account"]["netReturnPct"] = 5.
        self.assertTrue(readout.gates(candidate, baseline, rule, stress=True)["passed"])
        baseline["account"]["netReturnPct"] = 6.
        self.assertFalse(readout.gates(candidate, baseline, rule, stress=True)["checks"]["beatSameCostBaseline"])
        with self.assertRaisesRegex(ValueError, "gate"):
            readout.gates(candidate, baseline, {**rule, "unimplemented": True}, stress=True)

    def test_block_bootstrap_matches_independent_scalar_resampling_with_zero_days(self):
        returns_a = np.array([0., .001, -.002, 0., .003, -.001] * 7 + [0., 0.])
        returns_b = np.array([0., .0002, -.0005, 0., .001, -.002] * 7 + [0., 0.])
        def account(returns):
            equity = 1000.
            curve = []
            for i, value in enumerate(returns):
                equity *= 1 + value
                curve.append({"date": f"day-{i:03d}", "equity": equity})
            return {"config": {"initialCapital": 1000.}, "curve": curve}
        a, b = account(returns_a), account(returns_b)
        settings = {"movingBlockSessions": 20, "draws": 2000, "seed": 20261031,
                    "percentileInterval": [2.5, 97.5], "meaning": "synthetic"}
        observed = readout.block_interval(a, b, settings)
        delta = readout.daily_returns(a) - readout.daily_returns(b)
        rng = np.random.default_rng(20261031)
        samples = []
        for _ in range(2000):
            starts = rng.integers(0, len(delta) - 20 + 1, size=math.ceil(len(delta) / 20))
            sample = []
            for start in starts:
                sample.extend(delta[start:start+20])
            samples.append(sum(sample[:len(delta)]) / len(delta) * 10000)
        self.assertEqual(observed["pairedSessions"], len(returns_a))
        np.testing.assert_allclose(observed["percentileBoundsBps"], np.percentile(samples, [2.5, 97.5]), atol=1e-12)
        self.assertAlmostEqual(observed["meanDailyDifferenceBps"], float(delta.mean() * 10000))
        self.assertEqual(observed, readout.block_interval(a, b, settings))
        b["curve"].pop()
        with self.assertRaisesRegex(ValueError, "calendars"):
            readout.block_interval(a, b, settings)


if __name__ == "__main__":
    unittest.main()
