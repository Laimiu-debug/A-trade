"""Combined pools filter full eligible universes and the actual strategy inputs."""
import copy
import datetime as dt
import unittest

from service.portfolio_config import default_config, normalize_config, pool_catalogue
from service.portfolio_engine import PortfolioEngine
from service.portfolio_signals import DailyScorer


def fixture(repeats=1):
    dates = [(dt.date(2026, 1, 1) + dt.timedelta(days=index)).isoformat() for index in range(25)]
    rows, groups = [], {}
    for group, (trend, active) in enumerate(((True, True), (True, False), (False, True), (False, False))):
        groups[group] = []
        for repeat in range(repeats):
            symbol = f"sh{600000 + group * repeats + repeat:06d}"
            groups[group].append(symbol)
            bars = []
            for index, day in enumerate(dates):
                close = 10 + index * .2 if trend else 10
                bars.append({"date": day, "open": close, "high": close + .1, "low": close - .1,
                             "close": close, "volume": 2000000 if active and index == 24 else 1000000,
                             "amount": 20000000})
            rows.append({"symbol": symbol, "bars": bars})
    return {"securities": rows}, dates[-1], groups


def settings(pools, mode="intersection"):
    return normalize_config({
        "strategies": [
            {"id": "baseline", "kind": "momentum", "params": {"lookback": 2, "trendWindow": 3, "minReturn": 0}},
            {"id": "filtered", "kind": "momentum", "pool": "combined",
             "params": {"lookback": 2, "trendWindow": 3, "minReturn": 0}}],
        "universe": {"minListingDays": 0, "minAmount20": 10000000},
        "poolSettings": {"trend": {"window": 5}, "volume": {"window": 5, "minRatio": 1.2},
                         "combined": {"pools": pools, "mode": mode}},
    })


class CombinedPoolConfigTests(unittest.TestCase):
    def test_old_defaults_keep_base_strategies_and_add_explicit_pool_contract(self):
        config = default_config()
        self.assertEqual({node["pool"] for node in config["strategies"]}, {"base"})
        self.assertEqual(config["poolSettings"]["combined"],
                         {"pools": ["trend", "structure", "volume"], "mode": "intersection"})
        old = copy.deepcopy(config)
        del old["poolSettings"]["combined"]
        self.assertEqual(normalize_config(old), config)
        self.assertEqual(normalize_config(config), config)
        self.assertIn({"id": "combined", "name": "叠加筛选池"}, pool_catalogue())

    def test_combined_config_is_canonical_owned_and_accepts_empty_filter(self):
        raw = {"poolSettings": {"combined": {"pools": ["volume", "trend"], "mode": "union"}}}
        before = copy.deepcopy(raw)
        config = normalize_config(raw)
        self.assertEqual(raw, before)
        self.assertEqual(config["poolSettings"]["combined"], {"pools": ["trend", "volume"], "mode": "union"})
        raw["poolSettings"]["combined"]["pools"].clear()
        self.assertEqual(config["poolSettings"]["combined"]["pools"], ["trend", "volume"])
        self.assertEqual(normalize_config(raw)["poolSettings"]["combined"]["pools"], [])

    def test_invalid_pools_modes_and_unknown_fields_are_rejected(self):
        invalid = [None, [], {"pools": "trend"}, {"pools": ["base"]}, {"pools": ["combined"]},
                   {"pools": ["excluded"]}, {"pools": ["trend", "trend"]}, {"pools": [None]},
                   {"pools": [{}]}, {"mode": "weighted"}, {"mode": None}, {"mode": []}, {"typo": 1}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_config({"poolSettings": {"combined": value}})


class CombinedPoolSignalTests(unittest.TestCase):
    def test_full_universe_intersection_union_and_daily_strategy_gate(self):
        data, day, groups = fixture(34)
        intersect = DailyScorer(data, settings(["trend", "volume"])).daily(day)
        union = DailyScorer(data, settings(["trend", "volume"], "union")).daily(day)
        self.assertEqual(len(intersect["pools"]["base"]), 136)
        self.assertEqual(set(intersect["pools"]["trend"]), set(groups[0] + groups[1]))
        self.assertEqual(set(intersect["pools"]["volume"]), set(groups[0] + groups[2]))
        self.assertEqual(intersect["pools"]["combined"], groups[0])
        self.assertEqual(set(union["pools"]["combined"]), set(groups[0] + groups[1] + groups[2]))
        self.assertEqual(len(union["pools"]["combined"]), 102)
        self.assertEqual({row["symbol"] for row in intersect["strategies"]["filtered"]}, set(groups[0]))
        self.assertEqual({row["symbol"] for row in intersect["strategies"]["baseline"]}, set(groups[0] + groups[1]))
        self.assertEqual(intersect["strategyEligibility"]["filtered"][groups[1][0]]["reason"], "poolFilter")
        self.assertEqual(intersect["strategies"]["baseline"], union["strategies"]["baseline"])
        for key in ("base", "trend", "structure", "volume"):
            self.assertEqual(intersect["pools"][key], union["pools"][key])

    def test_single_and_empty_filter_obey_the_same_base_eligibility(self):
        data, day, groups = fixture()
        rejected = copy.deepcopy(data["securities"][0])
        rejected.update(symbol="sh699999", stByDate={day: True})
        data["securities"].append(rejected)
        for mode in ("intersection", "union"):
            for selected, expected in (([], sum(groups.values(), [])), (["volume"], groups[0] + groups[2]),
                                       (["trend", "volume"], groups[0] if mode == "intersection" else groups[0] + groups[1] + groups[2])):
                with self.subTest(mode=mode, selected=selected):
                    result = DailyScorer(data, settings(selected, mode)).daily(day)
                    self.assertEqual(set(result["pools"]["combined"]), set(expected))
                    self.assertEqual(result["eligibilityBySymbol"]["sh699999"]["reason"], "historicalSt")
                    self.assertNotIn("sh699999", result["pools"]["combined"])
                    if not selected:
                        self.assertEqual([(row["symbol"], row["rawScore"], row["score"]) for row in result["strategies"]["filtered"]],
                                         [(row["symbol"], row["rawScore"], row["score"]) for row in result["strategies"]["baseline"]])

    def test_default_three_filter_intersection_is_real_not_just_a_label(self):
        data, day, groups = fixture()
        config = settings(["trend", "structure", "volume"])
        result = DailyScorer(data, config).daily(day)
        self.assertEqual(result["pools"]["structure"], [])
        self.assertEqual(result["pools"]["combined"], [])
        self.assertEqual(len(result["strategies"]["baseline"]), 2)
        self.assertEqual(result["strategies"]["filtered"], [])
        # The configured drawdown interval includes a zero drawdown now, so it passes.
        config["poolSettings"]["structure"]["minDrawdown"] = 0
        changed = DailyScorer(data, config).daily(day)
        self.assertEqual(changed["pools"]["combined"], groups[0])

    def test_future_changes_do_not_change_past_combined_candidates(self):
        data, _, _ = fixture()
        day = data["securities"][0]["bars"][-2]["date"]
        short = copy.deepcopy(data)
        for security in short["securities"]:
            security["bars"].pop()
        cfg = settings(["trend", "volume"])
        full = DailyScorer(data, cfg).daily(day)
        truncated = DailyScorer(short, cfg).daily(day)
        self.assertEqual(full["pools"], truncated["pools"])
        self.assertEqual(full["strategies"], truncated["strategies"])
        self.assertEqual(full["pools"]["combined"], [])


class CombinedPoolCheckpointTests(unittest.TestCase):
    def setup_engine(self):
        data, day, _ = fixture()
        config = settings(["trend", "structure", "volume"])
        # Existing users selected an ordinary pool before combined was available.
        for strategy in config["strategies"]:
            strategy["pool"] = "base"
        config.update(startDate=data["securities"][0]["bars"][5]["date"], endDate=day)
        return data, config

    def test_legacy_checkpoint_resumes_with_normalized_defaults_without_mutating_input(self):
        data, config = self.setup_engine()
        engine = PortfolioEngine(data, config)
        engine.step(8)
        legacy = engine.checkpoint()
        del legacy["config"]["poolSettings"]["combined"]
        before = copy.deepcopy(legacy)
        old_config = copy.deepcopy(config)
        del old_config["poolSettings"]["combined"]
        resumed = PortfolioEngine(data, old_config, legacy)
        self.assertEqual(legacy, before)
        self.assertEqual(resumed.checkpoint()["config"], config)
        engine.step(200)
        resumed.step(200)
        self.assertEqual(resumed.result(), engine.result())

    def test_normalization_does_not_allow_changed_parameters_versions_or_data(self):
        data, config = self.setup_engine()
        engine = PortfolioEngine(data, config)
        engine.step(4)
        legacy = engine.checkpoint()
        del legacy["config"]["poolSettings"]["combined"]
        changed_configs = []
        for path, value in ((["topN"], config["topN"] + 1),
                            (["poolSettings", "trend", "minReturn"], .5),
                            (["poolSettings", "combined", "mode"], "union")):
            changed = copy.deepcopy(config)
            current = changed
            for key in path[:-1]:
                current = current[key]
            current[path[-1]] = value
            changed_configs.append(changed)
        for changed in changed_configs:
            with self.subTest(config=changed), self.assertRaisesRegex(ValueError, "不能恢复"):
                PortfolioEngine(data, changed, legacy)
        for key in ("engineVersion", "rulesVersion", "scorerVersion", "dataDigest", "datasetFingerprint"):
            changed = copy.deepcopy(legacy)
            changed[key] = "different"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "不能恢复"):
                PortfolioEngine(data, config, changed)


if __name__ == "__main__":
    unittest.main()
