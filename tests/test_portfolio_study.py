import copy
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from service.portfolio_study import PortfolioStudy, _slice, normalize_study, persistence_diagnostic, spearman, rotation_comparison


def fixture():
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(180)]
    data = {"fingerprint": "same-source", "securities": [{"symbol": "sh600000", "bars": [
        {"date": day, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100000,
         "amount": 1000000} for day in days]}]}
    config = {"initialCapital": 100000, "startDate": days[0], "endDate": days[-1],
              "strategies": [{"id": key} for key in ("alpha", "beta", "gamma")],
              "allocation": {"mode": "equal", "strategyIds": ["alpha", "beta", "gamma"]},
              "commissionRate": .0003, "study": {"trainEnd": days[139], "testStart": days[140],
              "testEnd": days[-1], "persistenceWindow": 20, "permutationCount": 99}}
    return data, config


class FakeEngine:
    calls = []
    issue = None

    def __init__(self, dataset, config, state=None):
        self.dataset, self.config = dataset, config
        self.days = [row["date"] for row in dataset["securities"][0]["bars"]
                     if config["startDate"] <= row["date"] <= config["endDate"]]
        self.cursor = (state or {}).get("cursor", 0)
        self.__class__.calls.append(self)

    def step(self, days=5):
        self.cursor = min(len(self.days), self.cursor + days)
        return self.cursor == len(self.days)

    def checkpoint(self):
        return {"cursor": self.cursor}

    def result(self):
        ids = self.config["allocation"]["strategyIds"]
        rate = sum({"alpha": .003, "beta": .002, "gamma": -.001}[key] for key in ids) / len(ids)
        # A future regime mutation only affects the testing engine because the
        # training engine must never receive those future bars.
        if self.dataset["securities"][0]["bars"][-1]["close"] == 20:
            rate *= -3
        curve = [{"date": day, "equity": self.config["initialCapital"] * (1 + rate)**(i + 1)}
                 for i, day in enumerate(self.days[:self.cursor])]
        quality = {"certified": False, "quarantinedSymbols": [], "staleValuationDays": 0,
                   "unaccountedActionExposure": False}
        if self.issue == "quarantine":
            quality["quarantinedSymbols"] = ["sh600000"]
        elif self.issue == "stale":
            quality["staleValuationDays"] = 1
        elif self.issue == "action":
            quality["unaccountedActionExposure"] = True
        elif self.issue == "missing":
            quality = {}
        equity = curve[-1]["equity"] if curve else self.config["initialCapital"]
        actual_config = copy.deepcopy(self.config)
        if self.issue == "cost":
            actual_config["commissionRate"] = 0
        return {"status": "complete" if self.cursor == len(self.days) else "running", "config": actual_config,
                "fingerprint": "other" if self.issue == "fingerprint" else self.dataset["fingerprint"],
                "curve": curve, "quality": quality, "trades": [], "daily": [], "warnings": [],
                "metrics": {"totalReturnPct": (equity / self.config["initialCapital"] - 1) * 100,
                            "maxDrawdownPct": 0, "totalFees": 30, "totalSlippage": 20, "finalEquity": equity}}


def finish(runner, days=9):
    for _ in range(10000):
        if runner.step(days):
            return runner.result()
    raise AssertionError("study did not finish")


class PersistenceTests(unittest.TestCase):
    def test_training_slice_clips_all_dated_sources_without_copying_bar_rows(self):
        data, config = fixture()
        bars = data["securities"][0]["bars"]
        days = [bar["date"] for bar in bars]
        data["calendar"] = days
        data["benchmark"] = {"symbol": "sh000300", "bars": bars}
        data["membershipByDate"] = {day: ["sh600000"] for day in days}
        for key in ("stByDate", "precloseByDate", "stHistory", "memberHistory"):
            data["securities"][0][key] = {day: False for day in days}
        before = copy.deepcopy(data)
        subset = _slice(data, config["study"]["trainEnd"])
        self.assertEqual(len(subset["calendar"]), 140)
        self.assertEqual(len(subset["benchmark"]["bars"]), 140)
        self.assertEqual(len(subset["membershipByDate"]), 140)
        self.assertIs(subset["securities"][0]["bars"][0], bars[0])
        self.assertIsNot(subset["securities"][0]["bars"], bars)
        for key in ("stByDate", "precloseByDate", "stHistory", "memberHistory"):
            self.assertEqual(len(subset["securities"][0][key]), 140)
        self.assertEqual(data, before)

    def test_spearman_average_ties_and_constant_sections(self):
        self.assertAlmostEqual(spearman([1, 1, 3], [1, 2, 3]), 3**.5 / 2)
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))
        self.assertEqual(spearman([1, 2, 3], [3, 2, 1]), -1)

    def test_persistence_uses_nonoverlapping_compounded_returns_and_fixed_seed(self):
        data, config = fixture()
        settings = normalize_study(config)
        values = {}
        for key, rate in (("a", .003), ("b", .002), ("c", -.001)):
            values[key] = {"curve": [{"date": row["date"], "equity": 100000 * (1 + rate)**(i + 1)}
                                      for i, row in enumerate(data["securities"][0]["bars"][:143])]}
        actual = persistence_diagnostic(values, 100000, settings)
        self.assertEqual(actual, persistence_diagnostic(values, 100000, settings))
        self.assertEqual(actual["validPairCount"], 6)
        self.assertEqual(actual["excludedTrailingDays"], 3)
        self.assertTrue(actual["passed"])
        self.assertAlmostEqual(actual["windows"][1]["returnsPct"]["a"], ((1.003)**20 - 1) * 100)
        for left, right in zip(actual["windows"], actual["windows"][1:]):
            self.assertLess(left["endDate"], right["startDate"])

    def test_ties_short_history_and_calendar_mismatch_do_not_pass(self):
        data, config = fixture()
        settings = normalize_study(config)
        curve = [{"date": row["date"], "equity": 100000} for row in data["securities"][0]["bars"]]
        values = {key: {"curve": copy.deepcopy(curve)} for key in "abc"}
        tied = persistence_diagnostic(values, 100000, settings)
        self.assertFalse(tied["passed"])
        self.assertEqual(tied["validPairCount"], 0)
        self.assertIn("fewer_than_three_distinct_strategy_behaviors", tied["failures"])
        values["a"]["curve"].pop()
        self.assertIn("missing_or_misaligned_daily_equity", persistence_diagnostic(values, 100000, settings)["failures"])
        self.assertFalse(persistence_diagnostic({key: values[key] for key in "ab"}, 100000, settings)["passed"])

    def test_dates_are_required_only_when_running_study(self):
        _, config = fixture()
        for key in ("trainEnd", "testStart", "testEnd"):
            candidate = copy.deepcopy(config)
            candidate["study"].pop(key)
            with self.assertRaises(ValueError):
                normalize_study(candidate)
        config["study"]["testStart"] = config["study"]["trainEnd"]
        with self.assertRaises(ValueError):
            normalize_study(config)


class PortfolioStudyTests(unittest.TestCase):
    def setUp(self):
        self.engine_patch = patch("service.portfolio_study.PortfolioEngine", FakeEngine)
        self.config_patch = patch("service.portfolio_study.normalize_config", lambda value: copy.deepcopy(value))
        self.engine_patch.start()
        self.config_patch.start()
        self.addCleanup(self.engine_patch.stop)
        self.addCleanup(self.config_patch.stop)
        FakeEngine.calls, FakeEngine.issue = [], None

    def test_holdout_cannot_change_training_selection_or_persistence(self):
        data, config = fixture()
        normal = finish(PortfolioStudy(data, config))
        changed = copy.deepcopy(data)
        for row in changed["securities"][0]["bars"][140:]:
            row["close"] = 20
        alternative = finish(PortfolioStudy(changed, config))
        self.assertEqual(normal["training"], alternative["training"])
        self.assertEqual(normal["training"]["selectedStrategyId"], "alpha")
        self.assertNotEqual(normal["comparison"]["fixed"], alternative["comparison"]["fixed"])
        self.assertEqual(len(normal["training"]["runs"]), 4)
        self.assertEqual(set(normal["testing"]), {"fixed", "equalWeight", "softRotation", "hardRotation"})
        self.assertFalse(normal["protocol"]["testUsedForSelection"])
        self.assertFalse(normal["rotationGate"]["passed"])
        self.assertEqual(normal["rotationGate"]["estimatedRotationGain"], 0)
        self.assertEqual(normal["rotationGate"]["status"], "no_value")
        self.assertIn("轮动没有创造价值", normal["rotationGate"]["conclusion"])
        self.assertIn("data_quality_not_certified", normal["rotationGate"]["reasons"])
        for engine in FakeEngine.calls:
            self.assertLessEqual(engine.dataset["securities"][0]["bars"][-1]["date"], engine.config["endDate"])
            self.assertEqual(engine.config["initialCapital"], config["initialCapital"])
            self.assertEqual(engine.config["commissionRate"], config["commissionRate"])

    def test_resume_matches_uninterrupted_and_progress_never_finishes_early(self):
        data, config = fixture()
        reference = finish(PortfolioStudy(data, config))
        runner = PortfolioStudy(data, config)
        for _ in range(12):
            self.assertFalse(runner.step(13))
        self.assertLess(runner.progress["progress"], 1)
        state = json.loads(json.dumps(runner.checkpoint()))
        resumed = PortfolioStudy(data, config, state=state)
        self.assertEqual(finish(resumed), reference)
        self.assertEqual(resumed.progress["progress"], 1)
        changed = copy.deepcopy(config)
        changed["commissionRate"] *= 2
        with self.assertRaises(ValueError):
            PortfolioStudy(data, changed, state=state)

    def test_disabled_rotation_still_runs_prespecified_baselines(self):
        data, config = fixture()
        config["study"]["rotationEnabled"] = False
        result = finish(PortfolioStudy(data, config))
        self.assertEqual(set(result["testing"]), {"fixed", "equalWeight"})
        self.assertTrue(result["training"]["persistence"]["passed"])
        self.assertEqual(result["rotationGate"]["status"], "skipped")
        self.assertIn("rotation_disabled", result["rotationGate"]["reasons"])

    def test_independent_comparison_never_passes_without_gate_and_completion(self):
        testing = {key: {"metrics": {"totalReturnPct": value}, "quality": {"certified": True}}
                   for key, value in (("softRotation", 8), ("fixed", 6), ("equalWeight", 4))}
        for enabled, passed, complete, expected in ((False, True, True, "skipped"),
                                                    (True, False, True, "skipped"),
                                                    (True, True, False, "running")):
            _, gate = rotation_comparison(testing, 100000, {"passed": passed}, enabled, complete)
            self.assertFalse(gate["passed"])
            self.assertEqual(gate["status"], expected)
        _, gate = rotation_comparison(testing, 100000, {"passed": True})
        self.assertTrue(gate["passed"])
        testing["softRotation"]["metrics"]["totalReturnPct"] = 4
        _, gate = rotation_comparison(testing, 100000, {"passed": True})
        self.assertEqual(gate["status"], "no_value")
        self.assertIn("轮动没有创造价值", gate["conclusion"])

    def test_missing_quality_bad_data_and_unfair_baselines_block_selection(self):
        data, config = fixture()
        for issue in ("quarantine", "stale", "action", "missing", "fingerprint", "cost"):
            with self.subTest(issue=issue):
                FakeEngine.issue = issue
                result = finish(PortfolioStudy(data, config))
                self.assertIsNone(result["training"]["selectedStrategyId"])
                self.assertFalse(result["training"]["persistence"]["passed"])
                self.assertTrue(result["quality"]["failures"])
                self.assertEqual(result["testing"], {})

    def test_partial_result_and_empty_data_are_explicit(self):
        data, config = fixture()
        runner = PortfolioStudy(data, config)
        partial = runner.result()
        self.assertEqual(partial["status"], "running")
        self.assertEqual(partial["comparison"], {})
        self.assertIsNone(partial["training"]["persistence"])
        with self.assertRaises(ValueError):
            PortfolioStudy({"securities": []}, config)

    def test_corrupt_checkpoint_does_not_claim_completed_research(self):
        data, config = fixture()
        runner = PortfolioStudy(data, config)
        runner.step(3)
        state = runner.checkpoint()
        state["cursor"] = 6
        with self.assertRaises(ValueError):
            PortfolioStudy(data, config, state=state)


class PortfolioIntegrationTests(unittest.TestCase):
    def test_one_nested_combination_is_a_single_unit_with_insufficient_persistence(self):
        data, original = fixture()
        config = {key: original[key] for key in ("initialCapital", "startDate", "endDate", "study")}
        config.update(strategies=[{"id": "fast", "kind": "momentum", "params": {"lookback": 3}},
                                  {"id": "slow", "kind": "momentum", "params": {"lookback": 8}}],
                      combinations=[{"id": "either", "method": "union", "children": [{"id": "fast"}, {"id": "slow"}]},
                                    {"id": "nested", "method": "weighted", "children": [{"id": "either", "weight": 2}, {"id": "slow"}]}],
                      allocation={"mode": "single", "strategyIds": ["nested"]})
        result = finish(PortfolioStudy(data, config))
        self.assertEqual(set(result["training"]["runs"]), {"single:nested", "equalWeight"})
        self.assertEqual(result["training"]["selectedStrategyId"], "nested")
        self.assertIn("at_least_three_strategies_required", result["training"]["persistence"]["failures"])
        for run in list(result["training"]["runs"].values()) + list(result["testing"].values()):
            self.assertEqual(run["config"]["allocation"]["strategyIds"], ["nested"])
            self.assertEqual(len(run["config"]["strategies"]), 2)
            self.assertEqual(len(run["config"]["combinations"]), 2)

    def test_real_ledger_and_normalizer_preserve_empty_signal_baselines(self):
        data, original = fixture()
        config = {key: original[key] for key in ("initialCapital", "startDate", "endDate", "study")}
        result = finish(PortfolioStudy(data, config))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["quality"]["failures"], [])
        self.assertEqual(len(result["training"]["runs"]), 4)
        self.assertEqual(result["comparison"]["fixed"]["tradeCount"], 0)
        self.assertEqual(result["comparison"]["equalWeight"]["finalEquity"], 100000)
        self.assertFalse(result["training"]["persistence"]["passed"])
        self.assertIsNone(result["rotationGate"]["estimatedRotationGain"])


if __name__ == "__main__":
    unittest.main()
