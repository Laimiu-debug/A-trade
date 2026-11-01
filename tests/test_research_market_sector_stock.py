from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest
import numpy as np

from service.research_market_sector_stock import advance_streak, build_inputs, midranks, stock_features


def fixture(groups=4, length=95):
    dates, day = [], date(2024, 1, 1)
    while len(dates) < length:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    t = np.arange(length)
    closes = np.array([10 * (1 + (.003 + (r % 5) * .001) * t) for r in range(groups * 5)])
    matrix = {"calendar": np.array(dates), "symbols": np.array([f"sh{600000+r:06d}" for r in range(groups * 5)]),
        "close": closes.copy(), "open": closes.copy(), "high": closes * 1.02, "low": closes * .98,
        "volume": np.full(closes.shape, 10000000.), "amount": np.broadcast_to(1e8 * (1 + .01 * t), closes.shape).copy(),
        "priceValid": np.ones(closes.shape, dtype=bool)}
    joins = [{"snapshotId": "source:first", "availableFrom": dates[60], "publicationDate": dates[59],
        "sectors": [{"sectorCode": f"C{30+g}", "sectorName": f"行业{g}", "cohortRowIndices": list(range(5*g, 5*g+5))} for g in range(groups)]}]
    indices = {s: {"close": 1000 * (1 + .0001 * t), "amount": np.full(length, 5e11)} for s in ("sh000001", "sz399106", "sh000300")}
    return matrix, joins, indices


def reclaim(matrix, row=4, index=85):
    matrix["low"][row, index] = matrix["low"][row, index-20:index].min() * .99


class HierarchyTests(unittest.TestCase):
    def test_independent_direct_window_rebuild_and_injected_error_detection(self):
        spec = importlib.util.spec_from_file_location("_test_system_input_reference", Path(__file__).resolve().parents[1] / "scripts/verify-market-sector-stock-inputs.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        matrix, joins, indices = fixture()
        reclaim(matrix)
        result = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        proof = module.DirectWindowReference(matrix, joins, indices).audit(result)
        self.assertTrue(proof["allMarketIndustryRoleAndScheduleChecks"])
        corrupted = deepcopy(result)
        corrupted["daily"][-1]["sectors"][0]["confirmCount"] += 1
        with self.assertRaises(ValueError):
            module.DirectWindowReference(matrix, joins, indices).audit(corrupted)
        corrupted = deepcopy(result)
        identifier = next(iter(corrupted["metadata"]))
        corrupted["metadata"][identifier]["failurePrice"] *= .99
        with self.assertRaises(ValueError):
            module.DirectWindowReference(matrix, joins, indices).audit(corrupted)

    def test_exact_midrank_ties_and_unknown(self):
        np.testing.assert_allclose(midranks([1, 1, 3, np.nan]), [1/3, 1/3, 5/6, np.nan], equal_nan=True)

    def test_full_windows_missing_calendar_slot_not_compressed(self):
        matrix, _, _ = fixture()
        matrix["priceValid"][0, 40] = False
        result = stock_features(matrix)
        self.assertTrue(np.isnan(result["return60"][0, 85]))
        self.assertTrue(np.isnan(result["ma60"][0, 85]))
        self.assertTrue(np.isfinite(result["return20"][0, 85]))
        self.assertTrue(np.isfinite(result["return60"][1, 85]))

    def test_prior_extremes_exclude_current_but_ntr_includes_it(self):
        matrix, _, _ = fixture()
        before = stock_features(matrix)
        matrix["high"][0, 85] *= 2
        after = stock_features(matrix)
        self.assertEqual(before["priorHigh20"][0, 85], after["priorHigh20"][0, 85])
        self.assertGreater(after["meanNtr20"][0, 85], before["meanNtr20"][0, 85])

    def test_unknown_interrupts_confirmation_without_negative_vote(self):
        self.assertEqual(advance_streak(None, 2, 0), (0, 0))
        self.assertEqual(advance_streak(None, 0, 2), (0, 0))
        self.assertEqual(advance_streak(False, 0, 0), (0, 1))

    def test_market_gate_and_top3_industry_control_have_actual_effect(self):
        matrix, joins, indices = fixture()
        for row in (4, 9, 14, 19):
            reclaim(matrix, row)
        result = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        day = matrix["calendar"][85]
        full = [r for r in result["schedules"]["completeSystem"] if r["signalDate"] == day]
        no_sector = [r for r in result["schedules"]["withoutSectorAdmission"] if r["signalDate"] == day]
        self.assertEqual(len(full), 3)
        self.assertEqual(len(no_sector), 4)
        for symbol in ("sh000001", "sz399106"):
            indices[symbol]["amount"] *= .7
        blocked = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        self.assertFalse(blocked["schedules"]["completeSystem"])
        self.assertTrue(blocked["schedules"]["withoutMarketAdmission"])

    def test_confirmation_resets_at_snapshot_change(self):
        matrix, joins, indices = fixture()
        joins.append({**deepcopy(joins[0]), "snapshotId": "source:later", "publicationDate": str(matrix["calendar"][83]), "availableFrom": str(matrix["calendar"][84])})
        result = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        before, after = [next(d for d in result["daily"] if d["date"] == matrix["calendar"][i]) for i in (83, 84)]
        self.assertTrue(before["selectedSectors"])
        self.assertFalse(after["selectedSectors"])
        self.assertTrue(all(s["confirmCount"] == 1 for s in after["sectors"]))

    def test_catchup_requires_prior_distinct_leader_and_branch_control(self):
        matrix, joins, indices = fixture()
        row, i = 0, 85
        matrix["close"][row, i] = matrix["high"][row, i-5:i].max() * 1.001
        matrix["high"][row, i] = matrix["close"][row, i] * 1.02
        result = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        found = [m for m in result["metadata"].values() if m["signalDate"] == matrix["calendar"][i] and m["symbol"] == matrix["symbols"][row] and m["pattern"] == "catchup"]
        self.assertTrue(found)
        self.assertTrue(all(m["priorLeaderRows"] and row not in m["priorLeaderRows"] for m in found))
        self.assertFalse(any(r["symbol"] == matrix["symbols"][row] and r["signalDate"] == matrix["calendar"][i] for r in result["schedules"]["withoutCatchupEntry"]))

    def test_future_poison_cannot_rewrite_earlier_decisions(self):
        matrix, joins, indices = fixture()
        reclaim(matrix)
        before = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        for field in ("open", "high", "low", "close", "amount"):
            matrix[field][:, 87:] *= 10
        after = build_inputs(matrix, joins, indices, matrix["calendar"][60])
        cutoff = matrix["calendar"][86]
        self.assertEqual([r for r in before["daily"] if r["date"] <= cutoff], [r for r in after["daily"] if r["date"] <= cutoff])
        for policy in before["schedules"]:
            self.assertEqual([r for r in before["schedules"][policy] if r["signalDate"] <= cutoff], [r for r in after["schedules"][policy] if r["signalDate"] <= cutoff])


if __name__ == "__main__":
    unittest.main()
