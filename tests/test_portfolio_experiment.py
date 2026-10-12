import copy
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from service.portfolio_experiment import (PortfolioExperiment, apply_parameters, neighborhood_statistics,
                                         normalize_experiment)


def fixture(count=85):
    days = [(date(2022, 1, 1) + timedelta(days=index)).isoformat() for index in range(count)]
    data = {"fingerprint": "frozen-source", "calendar": days,
            "securities": [{"symbol": "sh600000", "bars": [{"date": day, "open": 10., "close": 10.,
                "high": 11., "low": 9., "volume": 100000., "amount": 1000000.} for day in days]}]}
    config = {"startDate": days[0], "endDate": days[-1], "initialCapital": 100000,
              "strategies": [{"id": key, "kind": "momentum", "params": {"lookback": 3 + index}}
                             for index, key in enumerate(("alpha", "beta", "gamma"))],
              "combinations": [{"id": "combo", "method": "weighted", "children": [{"id": "alpha"}, {"id": "beta"}]}],
              "allocation": {"mode": "equal", "strategyIds": ["alpha", "beta", "gamma", "combo"]},
              "study": {"persistenceWindow": 2, "permutationCount": 99}}
    experiment = {"kind": "walkForward", "trainDays": 14, "testDays": 10, "stepDays": 10, "maxFolds": 2, "axes": []}
    return data, config, experiment


class FakeEngine:
    calls = []
    certified = False
    losing_rotation = False
    bad_quality = False
    bad_oos_baseline = False
    missing_regime_candidate = False

    def __init__(self, dataset, config, state=None):
        self.dataset, self.config = dataset, copy.deepcopy(config)
        self.days = [day for day in dataset["calendar"] if config["startDate"] <= day <= config["endDate"]]
        self.cursor = (state or {}).get("cursor", 0)
        self.__class__.calls.append(self)

    def step(self, days=5):
        self.cursor = min(len(self.days), self.cursor + days)
        return self.cursor == len(self.days)

    def checkpoint(self):
        return {"cursor": self.cursor}

    def result(self):
        mode = self.config["allocation"]["mode"]
        ids = self.config["allocation"]["strategyIds"]
        rate = sum({"alpha": .0012, "beta": .0008, "gamma": -.0004, "combo": .0006}[key] for key in ids) / len(ids)
        if mode in ("soft_rotation", "hard_rotation"):
            rate = .0003 if self.losing_rotation else .0017
            rate += self.config["topN"] * .000001
            rate += self.config["rotation"]["lookbackDays"] * .0000001
            rate += self.config["strategies"][0]["params"]["lookback"] * .000001
            rate += .000001 if self.config["combinations"][0]["method"] == "weighted" else 0
        if self.dataset["securities"][0]["bars"][-1]["close"] == 20:
            rate *= -3
        curve = []
        for index, day in enumerate(self.days[:self.cursor]):
            regime = {"status": "observed", "trend": "up" if index % 2 else "down", "volatility": "low"}
            if self.missing_regime_candidate and self.config["topN"] == 4 and index % 2:
                regime = {"status": "unknown"}
            curve.append({"date": day, "equity": self.config["initialCapital"] * (1 + rate)**(index + 1),
                          "dailyReturn": rate, "regimeForReturn": regime})
        equity = curve[-1]["equity"] if curve else self.config["initialCapital"]
        return {"status": "complete" if self.cursor == len(self.days) else "running", "config": self.config,
                "fingerprint": self.dataset["fingerprint"], "curve": curve, "trades": [], "daily": [], "rotationLog": [],
                "quality": {"certified": self.certified,
                            "quarantinedSymbols": ["sh600000"] if self.bad_quality or self.bad_oos_baseline and mode == "equal" and len(self.days) == 365 else [],
                            "staleValuationDays": 0, "unaccountedActionExposure": False},
                "metrics": {"totalReturnPct": (equity / self.config["initialCapital"] - 1) * 100, "maxDrawdownPct": 0,
                            "totalFees": 40, "totalSlippage": 20, "finalEquity": equity}}


def finish(runner, days=1000):
    for _ in range(10000):
        if runner.step(days):
            return runner.result()
    raise AssertionError("experiment did not terminate")


def four_axes():
    return [{"path": "strategies.alpha.params.lookback", "values": [3, 4]},
            {"path": "combinations.combo.method", "values": ["union", "weighted"]},
            {"path": "rotation.lookbackDays", "values": [5, 10]},
            {"path": "topN", "values": [4, 5]}]


class ExperimentValidationTests(unittest.TestCase):
    def test_strict_axes_four_layers_limits_and_reachability(self):
        _, config, experiment = fixture()
        for bad in ({**experiment, "unexpected": True}, {**experiment, "stepDays": 2},
                    {**experiment, "kind": "systemPlateau"}, {**experiment, "maxTrials": 82},
                    {**experiment, "axes": [{"path": "initialCapital", "values": [10000, 20000]}]},
                    {**experiment, "axes": [{"path": "topN", "values": [True, 3]}]},
                    {**experiment, "axes": [{"path": "topN", "values": [3, 3.]}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_experiment(bad, config)
        plateau = {**experiment, "kind": "systemPlateau", "axes": four_axes()}
        self.assertEqual(len(normalize_experiment(plateau, config)["axes"]), 4)
        config["allocation"]["strategyIds"].remove("combo")
        with self.assertRaisesRegex(ValueError, "不影响"):
            normalize_experiment(plateau, config)

    def test_grid_interactions_and_volume_are_rejected_before_run(self):
        _, config, experiment = fixture()
        axes = four_axes()
        axes[0]["values"] = [3, 4, 5, 6, 7]
        axes[2]["values"] = [5, 10, 15, 20, 25]
        with self.assertRaisesRegex(ValueError, "100"):
            normalize_experiment({**experiment, "axes": axes}, config)
        config["strategies"].append({"id": "pull", "kind": "trendPullback"})
        config["allocation"]["strategyIds"].append("pull")
        with self.assertRaises(ValueError):
            normalize_experiment({**experiment, "axes": [
                {"path": "strategies.pull.params.minDrawdown", "values": [.01, .1]},
                {"path": "strategies.pull.params.maxDrawdown", "values": [.05, .15]}]}, config)

    def test_neighborhood_uses_every_value_and_rejects_a_peak(self):
        _, config, experiment = fixture()
        settings = normalize_experiment(experiment, config)
        self.assertTrue(neighborhood_statistics([2, 3, 4], settings)["passed"])
        self.assertFalse(neighborhood_statistics([100, -2, -3], settings)["passed"])
        self.assertFalse(neighborhood_statistics([2, 3, None], settings)["passed"])
        self.assertEqual(neighborhood_statistics([2, 3, None], settings)["positiveFraction"], 1)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("service.portfolio_experiment.PortfolioEngine", FakeEngine)
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeEngine.calls = []
        FakeEngine.certified = False
        FakeEngine.losing_rotation = False
        FakeEngine.bad_quality = False
        FakeEngine.bad_oos_baseline = False
        FakeEngine.missing_regime_candidate = False

    def test_frozen_selection_has_no_holdout_dependency_and_preserves_costs(self):
        data, config, experiment = fixture()
        experiment.update(maxFolds=1, axes=[{"path": "topN", "values": [4, 5, 6]}])
        normal = finish(PortfolioExperiment(data, config, experiment))
        changed = copy.deepcopy(data)
        for row in changed["securities"][0]["bars"][14:]:
            row["close"] = 20
        modified = finish(PortfolioExperiment(changed, config, experiment))
        self.assertEqual(normal["folds"][0]["selection"], modified["folds"][0]["selection"])
        self.assertEqual(normal["folds"][0]["training"], modified["folds"][0]["training"])
        self.assertNotEqual(normal["folds"][0]["comparison"], modified["folds"][0]["comparison"])
        self.assertFalse(normal["protocol"]["testUsedForSelection"])
        self.assertEqual(set(normal["folds"][0]["testing"]), {"softRotation", "hardRotation", "fixed", "equalWeight"})
        for engine in FakeEngine.calls:
            self.assertLessEqual(engine.dataset["calendar"][-1], engine.config["endDate"])
            self.assertEqual(engine.config["initialCapital"], 100000)
            self.assertEqual(engine.config["commissionRate"], .0003)
            self.assertEqual(engine.config["minCommission"], 5)

    def test_checkpoint_resume_exactly_matches_and_rejects_tampering(self):
        data, config, experiment = fixture()
        reference = finish(PortfolioExperiment(data, config, experiment))
        runner = PortfolioExperiment(data, config, experiment)
        for _ in range(4):
            self.assertFalse(runner.step(3))
        checkpoint = json.loads(json.dumps(runner.checkpoint()))
        resumed = PortfolioExperiment(data, config, experiment, checkpoint)
        self.assertEqual(finish(resumed), reference)
        checkpoint["foldIndex"] = 2
        with self.assertRaises(ValueError):
            PortfolioExperiment(data, config, experiment, checkpoint)
        changed = copy.deepcopy(data)
        changed["securities"][0]["bars"][-1]["close"] = 12
        with self.assertRaises(ValueError):
            PortfolioExperiment(changed, config, experiment, runner.checkpoint())

    def test_insufficient_persistence_skips_rotation_without_relaxing_gate(self):
        data, config, experiment = fixture()
        config["allocation"]["strategyIds"] = ["alpha"]
        config["allocation"]["mode"] = "single"
        result = finish(PortfolioExperiment(data, config, experiment))
        self.assertEqual(len(result["folds"]), 2)
        for fold in result["folds"]:
            self.assertEqual(set(fold["testing"]), {"fixed", "equalWeight"})
            self.assertFalse(fold["persistence"]["passed"])
            self.assertEqual(fold["rotationGate"]["status"], "skipped")
        self.assertTrue(all(engine.config["allocation"]["mode"] not in ("soft_rotation", "hard_rotation") for engine in FakeEngine.calls))
        self.assertFalse(result["plateau"]["certified"])

    def test_losing_rotation_is_final_no_value_without_adaptive_trials(self):
        FakeEngine.losing_rotation = True
        data, config, experiment = fixture()
        result = finish(PortfolioExperiment(data, config, experiment))
        for fold in result["folds"]:
            self.assertEqual(fold["rotationGate"]["status"], "no_value")
            self.assertIn("轮动没有创造价值", fold["rotationGate"]["conclusion"])
        self.assertEqual(result["trialCount"], 1)
        self.assertEqual(result["actualCandidateEvaluations"], 2)
        self.assertEqual(result["engineRunCount"], result["protocol"]["maximumEngineRunCount"])

    def test_quality_failure_cannot_select_candidate_or_produce_oos(self):
        FakeEngine.bad_quality = True
        data, config, experiment = fixture()
        result = finish(PortfolioExperiment(data, config, experiment))
        self.assertEqual(result["status"], "complete")
        for fold in result["folds"]:
            self.assertEqual(fold["selection"]["status"], "invalid_training")
            self.assertEqual(fold["testing"], {})
        self.assertEqual(result["plateau"]["recommendedRanges"], [])

    def test_full_system_grid_reports_training_oos_and_no_premature_certification(self):
        data, config, experiment = fixture()
        experiment.update(kind="systemPlateau", axes=four_axes())
        result = finish(PortfolioExperiment(data, config, experiment))
        self.assertEqual(result["trialCount"], 16)
        self.assertEqual(result["actualCandidateEvaluations"], 32)
        self.assertEqual(len(result["plateau"]["heatmap"]), 16)
        self.assertTrue(all(row["oosMeanNetReturnPct"] is not None for row in result["plateau"]["heatmap"]))
        self.assertTrue(all(row["rotationExecuted"] for row in result["plateau"]["heatmap"]))
        self.assertEqual(result["plateau"]["recommendedRanges"], [])
        self.assertIn("insufficient_out_of_sample_years", result["plateau"]["reasons"])
        self.assertIn("data_quality_not_certified", result["plateau"]["reasons"])
        self.assertFalse(result["plateau"]["selectionUsesOos"])
        self.assertIn("independent", result["protocol"]["capitalConvention"])

    def test_certification_requires_each_frozen_neighbor_in_every_regime(self):
        data, config, experiment = fixture(800)
        experiment.update(kind="systemPlateau", axes=four_axes(), trainDays=60, testDays=365, stepDays=365,
                          maxNeighborhoodStdPct=100)
        FakeEngine.certified = True
        result = finish(PortfolioExperiment(data, config, experiment))
        self.assertTrue(result["plateau"]["statisticalValidation"]["passed"], result["plateau"]["reasons"])
        self.assertTrue(result["plateau"]["certified"], result["plateau"]["reasons"])
        self.assertEqual(len(result["plateau"]["recommendedRanges"]), 4)
        FakeEngine.missing_regime_candidate = True
        broken = finish(PortfolioExperiment(data, config, experiment))
        self.assertFalse(broken["plateau"]["certified"])
        self.assertIn("frozen_neighborhood_failed_regimes", broken["plateau"]["reasons"])
        self.assertEqual(broken["plateau"]["recommendedRanges"], [])

    def test_oos_baseline_integrity_failure_blocks_statistical_plateau(self):
        data, config, experiment = fixture(800)
        experiment.update(kind="systemPlateau", axes=four_axes(), trainDays=60, testDays=365, stepDays=365,
                          maxNeighborhoodStdPct=100)
        FakeEngine.certified = True
        FakeEngine.bad_oos_baseline = True
        result = finish(PortfolioExperiment(data, config, experiment))
        self.assertIn("evidence_integrity_failed", result["plateau"]["reasons"])
        self.assertFalse(result["plateau"]["statisticalValidation"]["passed"])
        self.assertEqual(result["plateau"]["recommendedRanges"], [])

    def test_bad_candidate_outside_training_frozen_region_does_not_veto_valid_region(self):
        class OuterFailureEngine(FakeEngine):
            def result(self):
                result = super().result()
                if self.config["strategies"][0]["params"]["lookback"] == 6:
                    result["quality"]["quarantinedSymbols"] = ["sh600000"]
                return result
        data, config, experiment = fixture(800)
        axes = four_axes()
        axes[0]["values"] = [3,4,5,6]
        experiment.update(kind="systemPlateau", axes=axes, trainDays=60, testDays=365, stepDays=365,
                          maxNeighborhoodStdPct=100)
        FakeEngine.certified = True
        with patch("service.portfolio_experiment.PortfolioEngine", OuterFailureEngine):
            result = finish(PortfolioExperiment(data, config, experiment))
        self.assertTrue(result["plateau"]["certified"], result["plateau"]["reasons"])
        self.assertEqual(result["plateau"]["recommendedRanges"][0]["values"], [3,4])
        self.assertEqual(len(result["plateau"]["frozenRegionCandidateIds"]), 16)
        self.assertTrue(any(row["qualityFailures"] for row in result["folds"][0]["training"].values()))


class ExperimentIntegrationTests(unittest.TestCase):
    def test_real_engine_short_single_strategy_runs_both_independent_folds(self):
        data, config, experiment = fixture()
        config["allocation"] = {"mode": "single", "strategyIds": ["alpha"]}
        experiment["trainDays"] = 35
        result = finish(PortfolioExperiment(data, config, experiment), days=5)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["folds"]), 2)
        for fold in result["folds"]:
            self.assertEqual(fold["testing"]["equalWeight"]["metrics"]["initialCapital"], 100000)
            self.assertEqual(fold["rotationGate"]["status"], "skipped")
            self.assertEqual(fold["testing"]["fixed"]["curve"][0]["date"], fold["testStart"])


if __name__ == "__main__":
    unittest.main()
