import copy
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from service.plateau import PlateauRunner, normalize_scan, score_plateaus, _behavior_diagnostics
from service.backtest_engine import normalize_config


def dataset(length=150, future_close=10):
    start = date(2025, 1, 1)
    days = [(start + timedelta(days=i)).isoformat() for i in range(length)]
    bars = [{"date": day, "open": 10, "high": max(11, future_close), "low": 9,
             "close": future_close if i >= length * .8 else 10, "volume": 100000, "amount": 1000000}
            for i, day in enumerate(days)]
    return {"securities": [{"symbol": "sh600000", "name": "fixture", "bars": bars}],
            "fingerprint": f"fixture-{length}-{future_close}", "warnings": []}


class DeterministicEngine:
    """Test double encodes known training plateaus and a mutable future regime."""
    instances = []

    def __init__(self, data, config, state=None):
        self.dataset, self.config = data, config
        self.ticks = (state or {}).get("ticks", 0)
        self.days = [b["date"] for b in data["securities"][0]["bars"]
                     if config["startDate"] <= b["date"] <= config["endDate"]]
        self.__class__.instances.append(self)

    def step(self, days=5):
        self.ticks = min(len(self.days), self.ticks + days)
        return self.ticks == len(self.days)

    def checkpoint(self):
        return {"ticks": self.ticks}

    def result(self):
        close = self.dataset["securities"][0]["bars"][-1]["close"]
        multiplier = self.config.get("costMultiplier", 1)
        value = (8 if close <= 10 else -8) - (multiplier - 1)
        return {"metrics": {"totalReturnPct": value, "maxDrawdownPct": 1, "closedTradeCount": 4,
                            "tradeCount": 4}, "curve": [{"date": self.days[-1], "equity": 100000 + value * 1000}],
                "trades": [], "decisions": [], "warnings": [], "config": self.config,
                "versions": {"engine": "fixture"}}


SCAN = {"axes": [{"path": "stopLossPct", "values": [.05, .08, .11]}], "folds": 3,
        "minTrades": 3, "minBlockDays": 20, "minHoldoutDays": 20}


def finish(runner, days=7):
    for _ in range(10000):
        if runner.step(days):
            return runner.result()
    raise AssertionError("runner failed to finish")


class PlateauScoringTests(unittest.TestCase):
    def rows(self, scores, pass_flags=None):
        return [{"id": f"point-{i:03d}", "coordinates": [i], "params": {"stopLossPct": i / 100},
                 "config": {"stopLossPct": i / 100},
                 "blocks": [{"score": score, "passes": pass_flags[i] if pass_flags else score > 0,
                             "failures": [] if score > 0 else ["nonpositive_return"]} for _ in range(3)]}
                for i, score in enumerate(scores)]

    def setup_scan(self, n):
        axes = [{"path": "stopLossPct", "values": [i / 100 for i in range(n)]}]
        scan = {**normalize_scan(SCAN, normalize_config({})), "axes": axes}
        return axes, scan

    def test_isolated_peak_is_not_recommended_over_broad_plateau(self):
        rows = self.rows([80, -8, -8, 8, 8, 8, 8, 8])
        axes, scan = self.setup_scan(len(rows))
        regions, recommendation, peak = score_plateaus(rows, axes, scan)
        self.assertEqual(peak["candidateId"], "point-000")
        self.assertIsNotNone(recommendation)
        self.assertIn(recommendation["candidateId"], ("point-005", "point-006"))
        self.assertGreaterEqual(regions[0]["size"], 3)
        self.assertNotIn("point-000", regions[0]["pointIds"])

    def test_neighbor_scores_are_order_independent(self):
        original = self.rows([3, 4, 5, 5, 4, 3])
        forward, reverse = copy.deepcopy(original), list(reversed(copy.deepcopy(original)))
        axes, scan = self.setup_scan(len(original))
        a = score_plateaus(forward, axes, scan)
        b = score_plateaus(reverse, axes, scan)
        self.assertEqual(a, b)
        self.assertEqual(sorted(forward, key=lambda r: r["id"]), sorted(reverse, key=lambda r: r["id"]))

    def test_no_region_does_not_fall_back_to_peak(self):
        rows = self.rows([-2, 100, -2])
        axes, scan = self.setup_scan(len(rows))
        regions, recommendation, peak = score_plateaus(rows, axes, scan)
        self.assertEqual(regions, [])
        self.assertIsNone(recommendation)
        self.assertIsNotNone(peak)

    def test_one_bad_chronological_block_rejects_candidate(self):
        rows = self.rows([10, 10, 10])
        for row in rows:
            row["blocks"][2] = {"score": -1, "passes": False, "failures": ["nonpositive_return"]}
        axes, scan = self.setup_scan(len(rows))
        self.assertIsNone(score_plateaus(rows, axes, scan)[1])
        self.assertTrue(all(not row["passes"] for row in rows))

    def test_identifiability_distinguishes_inactive_axis_from_active_axis(self):
        axes = [{"path": "stopLossPct", "values": [.05, .08, .11]},
                {"path": "maxHoldingDays", "values": [5, 10, 20]}]
        candidates = [{"id": f"p-{i}-{j}", "coordinates": [i, j],
                       "blocks": [{"behaviorFingerprint": f"holding-{j}-block-{block}"} for block in range(3)]}
                      for i in range(3) for j in range(3)]
        diagnostics = _behavior_diagnostics(candidates, axes, 3)
        self.assertEqual(diagnostics["distinctBehaviorCount"], 3)
        self.assertEqual(diagnostics["inactiveAxes"], ["stopLossPct"])
        self.assertFalse(diagnostics["uninformative"])
        partial = _behavior_diagnostics(candidates[:-1], axes, 4)
        self.assertIsNone(partial["uninformative"])
        self.assertEqual(partial["status"], "incomplete")


class PlateauRunnerTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch("service.plateau.BacktestEngine", DeterministicEngine)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        DeterministicEngine.instances = []

    def test_final_holdout_mutation_cannot_change_selection(self):
        normal = finish(PlateauRunner(dataset(), {}, SCAN))
        adverse = finish(PlateauRunner(dataset(future_close=20), {}, SCAN))
        self.assertEqual(normal["recommendation"], adverse["recommendation"])
        self.assertEqual(normal["regions"], adverse["regions"])
        self.assertEqual(normal["candidates"], adverse["candidates"])
        self.assertTrue(normal["validation"]["passed"])
        self.assertFalse(adverse["validation"]["passed"])
        self.assertEqual(adverse["validation"]["costStress"]["config"]["costMultiplier"], 2)
        self.assertFalse(adverse["methodology"]["finalHoldoutUsedForSelection"])

    def test_training_engines_never_receive_future_bars(self):
        runner = PlateauRunner(dataset(), {}, SCAN)
        result = finish(runner)
        boundary = result["split"]["training"]["endDate"]
        training = DeterministicEngine.instances[:9]
        self.assertEqual(len(training), 9)
        for engine in training:
            self.assertTrue(all(bar["date"] <= engine.config["endDate"] <= boundary
                                for bar in engine.dataset["securities"][0]["bars"]))
        self.assertEqual(result["parameterTrialCount"], 3)
        self.assertEqual(result["backtestRunCount"], 11)
        self.assertEqual(len(result["candidates"]), 3)
        self.assertTrue(all(len(row["blocks"]) == 3 for row in result["candidates"]))

    def test_json_checkpoint_resume_identical_even_mid_engine(self):
        data = dataset()
        uninterrupted = finish(PlateauRunner(data, {}, SCAN))
        runner = PlateauRunner(data, {}, SCAN)
        for _ in range(17):
            runner.step(3)
        state = json.loads(json.dumps(runner.checkpoint(), allow_nan=False))
        resumed = PlateauRunner(data, {}, SCAN, state=state)
        self.assertEqual(finish(resumed), uninterrupted)
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            PlateauRunner(dataset(future_close=20), {}, SCAN, state=state)

    def test_holdout_checkpoint_keeps_recommendation_frozen(self):
        runner = PlateauRunner(dataset(), {}, SCAN)
        while runner.progress["stage"] == "training":
            runner.step(100)
        chosen = copy.deepcopy(runner.result()["recommendation"])
        self.assertLess(runner.progress["progress"], 1)
        restored = PlateauRunner(dataset(), {}, SCAN, state=runner.checkpoint())
        final = finish(restored)
        self.assertEqual(chosen, final["recommendation"])
        self.assertTrue(final["validation"]["selectionFrozen"])
        self.assertEqual(final["progress"]["progress"], 1)

    def test_empty_data_returns_insufficient_without_recommendation(self):
        runner = PlateauRunner({"securities": [], "fingerprint": "empty"}, {}, SCAN)
        self.assertTrue(runner.step())
        result = runner.result()
        self.assertEqual(result["status"], "insufficient-data")
        self.assertIsNone(result["recommendation"])
        self.assertIsNone(result["validation"])
        self.assertEqual(result["backtestRunCount"], 0)
        self.assertEqual(DeterministicEngine.instances, [])

    def test_scan_validation_rejects_invalid_or_excessive_search(self):
        for scan in ([], {"unknown": 1}, {"axes": [{"path": "startDate", "values": [1, 2, 3]}]},
                     {"axes": [{"path": "stopLossPct", "values": [float("nan"), .1, .2]}]},
                     {"axes": [{"path": "maxHoldingDays", "values": list(range(1, 127))}]},
                     {"axes": [{"path": "strategyParams.momentum.lookback", "values": [10, 20, 30]}]}):
            with self.subTest(scan=scan), self.assertRaises(ValueError):
                PlateauRunner(dataset(), {}, scan)

    def test_grid_sorting_and_default_percentage_units(self):
        runner = PlateauRunner(dataset(), {}, {"axes": [{"path": "stopLossPct", "values": [.11, .05, .08, .08]}]})
        self.assertEqual(runner.axes[0]["values"], [.05, .08, .11])
        default = PlateauRunner(dataset(), {})
        self.assertEqual(default.axes[0]["values"], [.05, .08, .11])

    def test_identical_grid_is_reported_uninformative_but_keeps_representative(self):
        result = finish(PlateauRunner(dataset(), {}, SCAN))
        self.assertIsNotNone(result["recommendation"])
        self.assertTrue(result["validation"]["passed"])
        self.assertTrue(result["diagnostics"]["uninformative"])
        self.assertEqual(result["diagnostics"]["distinctBehaviorCount"], 1)
        self.assertEqual(result["diagnostics"]["inactiveAxes"], ["stopLossPct"])
        self.assertEqual(result["validation"]["parameterIdentifiability"], result["diagnostics"])
        self.assertTrue(result["validation"]["interpretationWarnings"])
        self.assertTrue(any("uninformative" in warning for warning in result["warnings"]))

    def test_positive_returns_with_detected_data_defects_never_qualify(self):
        original = DeterministicEngine.result
        for defect in ("quarantine", "stale", "corporate-action"):
            def defective(engine):
                result = original(engine)
                if defect == "quarantine":
                    result["quality"] = {"quarantinedSymbols": {"sh600000": "price discontinuity"}}
                elif defect == "stale":
                    result["metrics"]["staleValuationDays"] = 1
                else:
                    result["quality"] = {"unaccountedActionExposure": True}
                return result
            with self.subTest(defect=defect), patch.object(DeterministicEngine, "result", defective):
                result = finish(PlateauRunner(dataset(), {}, SCAN))
                self.assertIsNone(result["recommendation"])
                for candidate in result["candidates"]:
                    self.assertFalse(candidate["passes"])
                    self.assertTrue(any(f.startswith("dataQuality:") for f in candidate["failures"]))
                    self.assertIn("quality", candidate["blocks"][0])

    def test_benchmark_only_corporate_action_does_not_reject_strategy(self):
        original = DeterministicEngine.result
        def benchmark_only(engine):
            result = original(engine)
            result["quality"] = {"unaccountedActionExposure": False,
                                 "unaccountedBenchmarkActionExposure": True, "excessReturnReliable": False}
            return result
        with patch.object(DeterministicEngine, "result", benchmark_only):
            result = finish(PlateauRunner(dataset(), {}, SCAN))
        self.assertIsNotNone(result["recommendation"])
        self.assertTrue(result["validation"]["passed"])
        self.assertFalse(result["validation"]["holdout"]["quality"]["excessReturnReliable"])
        self.assertTrue(all(not block["quality"]["unaccountedActionExposure"]
                            for row in result["candidates"] for block in row["blocks"]))

    def test_detected_holdout_defect_fails_validation_without_reselection(self):
        original = DeterministicEngine.result
        boundary = dataset()["securities"][0]["bars"][119]["date"]
        def defective(engine):
            result = original(engine)
            if engine.config["startDate"] > boundary:
                result["quality"] = {"quarantinedSymbols": {"sh600000": "price discontinuity"}}
            return result
        with patch.object(DeterministicEngine, "result", defective):
            result = finish(PlateauRunner(dataset(), {}, SCAN))
        self.assertIsNotNone(result["recommendation"])
        self.assertFalse(result["validation"]["passed"])
        self.assertIn("holdout:dataQuality:quarantined_symbols", result["validation"]["failures"])
        self.assertIn("costStress:dataQuality:quarantined_symbols", result["validation"]["failures"])


class RealEngineIntegrationTests(unittest.TestCase):
    def test_real_engine_scan_resumes_and_handles_a_later_listing(self):
        data = dataset(240)
        for i, bar in enumerate(data["securities"][0]["bars"]):
            price = 10 + i * .025
            bar.update(open=price, high=price + .02, low=price - .02, close=price + .01, volume=1000000)
        data["securities"].append({"symbol": "sz000001", "name": "later listing", "bars":
                                  copy.deepcopy(data["securities"][0]["bars"][120:])})
        config = {"strategyIds": ["momentum"], "strategyParams": {"momentum": {
                  "lookback": 5, "trendWindow": 10, "minReturn": 0}}, "maxHoldingDays": 5,
                  "partialTakeProfitPct": 0, "takeProfitPct": 0, "trailingStopPct": 0}
        scan = {**SCAN, "minTrades": 1}
        expected = finish(PlateauRunner(data, config, scan))
        runner = PlateauRunner(data, config, scan)
        for _ in range(17):
            runner.step(3)
        state = json.loads(json.dumps(runner.checkpoint(), allow_nan=False))
        actual = finish(PlateauRunner(data, config, scan, state))
        self.assertEqual(actual, expected)
        self.assertEqual(actual["status"], "complete")
        self.assertIsNotNone(actual["recommendation"])
        self.assertEqual(actual["backtestRunCount"], 11)
        self.assertEqual(actual["validation"]["costStress"]["config"]["costMultiplier"], 2)


if __name__ == "__main__":
    unittest.main()
