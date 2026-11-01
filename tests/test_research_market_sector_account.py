from copy import deepcopy
import unittest

from service.research_account_fees import LegacyFeePolicy
from service.research_account_outcomes import summarize_account
from service.research_market_sector_account import MarketSectorStockAccount
from tests.test_research_volatility_trail import fixture as source_fixture, configuration, set_bar


def setup(signals_at=((25, 0),), count=3, policy="completeSystem", same_industry=False):
    data = source_fixture(48)
    original = data["securities"][0]
    data["securities"] = [{**deepcopy(original), "symbol": f"sh{600001+i:06d}"} for i in range(count)]
    days = data["calendar"]
    sectors = {s["symbol"]: "C39" if same_industry else f"C{30+i}" for i, s in enumerate(data["securities"])}
    daily = [{"date": day, "snapshotId": "synthetic", "market": {"admission": True, "riskExit": False, "crisisCount": 0},
        "selectedSectors": list(set(sectors.values())), "sectors": [{"sectorCode": code, "riskExit": False, "failCount": 0} for code in sorted(set(sectors.values()))]} for day in days]
    signals, metadata = [], {}
    for j, (index, row) in enumerate(signals_at):
        symbol = data["securities"][row]["symbol"]
        identifier = f"synthetic:{j}"
        signal = {"signalId": identifier, "strategyId": "fixed", "symbol": symbol, "signalDate": days[index], "originalRank": 1}
        signals.append(signal)
        metadata[identifier] = {**signal, "pattern": "leaderReclaim", "signalClose": 10., "failurePrice": 9.8, "meanNtr20": .02,
            "sectorCode": sectors[symbol], "snapshotId": "synthetic"}
    inputs = {"daily": daily, "metadata": metadata, "sectorMaps": {"synthetic": sectors}}
    return data, signals, inputs, policy


def run_case(case):
    data, signals, inputs, policy = case
    engine = MarketSectorStockAccount(data, signals, configuration(data), system_inputs=inputs, policy=policy, fee_policy=LegacyFeePolicy())
    engine.step(20000)
    return engine, engine.result()


class SystemAccountTests(unittest.TestCase):
    def test_opening_guard_expires_once_and_does_not_retry(self):
        case = setup()
        set_bar(case[0], 26, opening=10.5)
        engine, result = run_case(case)
        self.assertFalse(result["trades"])
        row = result["research"]["opportunities"][0]
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["reason"], "systemOpeningAboveVolatilityCeiling")
        self.assertEqual(len(result["research"]["system"]["openingGuards"]), 1)

    def test_structural_failure_queues_close_then_next_open(self):
        case = setup()
        set_bar(case[0], 27, close=9.79)
        set_bar(case[0], 28, opening=9.7)
        engine, result = run_case(case)
        sells = [r for r in result["trades"] if r["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["reason"], "systemStructuralFailure")
        self.assertEqual(sells[0]["signalDate"], case[0]["calendar"][27])
        self.assertEqual(sells[0]["date"], case[0]["calendar"][28])
        summary = summarize_account(result, engine.checkpoint(), policy_id="synthetic", policy_label="synthetic")
        self.assertFalse(summary["quality"]["integrityIssues"])

    def test_industry_capacity_restricts_third_position(self):
        case = setup(((25, 0), (27, 1), (29, 2)), same_industry=True)
        engine, result = run_case(case)
        plans = result["research"]["system"]["closePlans"]
        self.assertEqual(len([r for r in result["trades"] if r["side"] == "buy"]), 3)
        self.assertLess(plans[2]["plannedQuantity"], plans[0]["plannedQuantity"])
        self.assertLessEqual(plans[2]["plannedQuantity"] * plans[2]["reservePrice"] + plans[2]["industryValueBefore"], .4 * plans[2]["equity"] + 1e-8)

    def test_market_exit_persists_after_block_and_later_recovery(self):
        case = setup()
        context = case[2]["daily"][26]
        context["market"].update(admission=False, riskExit=True, crisisCount=3)
        set_bar(case[0], 27, volume=0)
        engine, result = run_case(case)
        sells = [r for r in result["trades"] if r["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        # The halt also makes the following session's prior-volume capacity0.
        self.assertEqual(sells[0]["date"], case[0]["calendar"][29])
        self.assertEqual(sells[0]["signalDate"], case[0]["calendar"][26])
        self.assertEqual(sells[0]["reason"], "systemMarketRisk")

    def test_cash_planning_does_not_spend_future_forced_sale_proceeds(self):
        case = setup(((25, 0), (27, 1), (29, 2), (31, 3), (33, 4), (35, 5)), count=6, policy="withoutMarketAdmission")
        case[2]["daily"][35]["market"].update(admission=False, riskExit=True, crisisCount=3)
        engine, result = run_case(case)
        last = result["research"]["system"]["closePlans"][-1]
        self.assertLess(last["plannedQuantity"], result["research"]["system"]["closePlans"][0]["plannedQuantity"])
        self.assertLessEqual(last["budget"], last["cashBeforeReservation"] + 1e-8)
        buy = next(r for r in result["trades"] if r["side"] == "buy" and r["signalId"] == last["signalId"])
        self.assertLessEqual(buy["quantity"], last["plannedQuantity"])

    def test_current_full_day_volume_does_not_enlarge_opening_quantity(self):
        a, b = setup(), setup()
        set_bar(a[0], 26, volume=2000000)
        set_bar(b[0], 26, volume=200000000)
        _, one = run_case(a)
        _, two = run_case(b)
        self.assertEqual(one["trades"][0]["quantity"], two["trades"][0]["quantity"])

    def test_injected_industry_or_mutated_frozen_inputs_rejected(self):
        case = setup()
        case[2]["metadata"]["synthetic:0"]["sectorCode"] = "fake"
        with self.assertRaises(ValueError):
            run_case(case)
        case = setup()
        engine = MarketSectorStockAccount(case[0], case[1], configuration(case[0]), system_inputs=case[2], policy=case[3], fee_policy=LegacyFeePolicy())
        engine.system_inputs["metadata"]["synthetic:0"]["failurePrice"] = 9.9
        with self.assertRaises(ValueError):
            engine.step(1)


if __name__ == "__main__":
    unittest.main()
