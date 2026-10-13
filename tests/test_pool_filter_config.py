"""Optional pool conditions are strict, off by default, and hydrate saved configurations."""
from copy import deepcopy
import unittest

from service.portfolio_config import normalize_config, normalize_screen_config
from service.portfolio_engine import PortfolioEngine
from test_portfolio_service import CONFIG, dataset_fixture


NEW_FIELDS = {
    "trend": ("rankEnabled", "rankWindow", "rankStart", "rankEnd"),
    "structure": ("peakBasis", "minMa10Above20Days", "minMa5Above10Days", "maDaysMode", "maxPeakAgeDays", "maxPriceVsMa20", "requireAboveMa20"),
    "volume": ("minVolumeChange20", "minUpDownVolumeRatio", "maxDownVolumeRatio", "excludeBlowoff", "excludeDivergence", "excludeUpperShadow"),
}


def without_extensions(config):
    legacy = deepcopy(config)
    legacy["universe"].pop("minAmplitude20", None)
    for pool, keys in NEW_FIELDS.items():
        for key in keys:
            legacy["poolSettings"][pool].pop(key, None)
    return legacy


class OptionalPoolConfigTests(unittest.TestCase):
    def test_defaults_keep_all_new_filter_conditions_disabled_and_old_config_hydrates(self):
        current = normalize_config(CONFIG)
        self.assertIsNone(current["universe"]["minAmplitude20"])
        self.assertEqual({key: current["poolSettings"]["trend"][key] for key in NEW_FIELDS["trend"]},
                         {"rankEnabled": False, "rankWindow": 40, "rankStart": 1, "rankEnd": 500})
        self.assertEqual({key: current["poolSettings"]["structure"][key] for key in NEW_FIELDS["structure"]},
                         {"peakBasis": "close", "minMa10Above20Days": None, "minMa5Above10Days": None,
                          "maDaysMode": "count", "maxPeakAgeDays": None, "maxPriceVsMa20": None, "requireAboveMa20": False})
        self.assertEqual({key: current["poolSettings"]["volume"][key] for key in NEW_FIELDS["volume"]},
                         {"minVolumeChange20": None, "minUpDownVolumeRatio": None, "maxDownVolumeRatio": None,
                          "excludeBlowoff": False, "excludeDivergence": False, "excludeUpperShadow": False})
        legacy = without_extensions(current)
        before = deepcopy(legacy)
        self.assertEqual(normalize_config(legacy), current)
        self.assertEqual(legacy, before)
        self.assertEqual(normalize_screen_config(legacy)["poolSettings"], current["poolSettings"])

    def test_enabled_schema_round_trips_boundary_values_and_is_owned(self):
        raw = {"universe": {"minAmplitude20": 10}, "poolSettings": {
            "trend": {"rankEnabled": True, "rankWindow": 500, "rankStart": 100000, "rankEnd": 100000},
            "structure": {"window": 500, "peakBasis": "high", "minMa10Above20Days": 20,
                          "minMa5Above10Days": 0, "maDaysMode": "consecutive", "maxPeakAgeDays": 499,
                          "maxPriceVsMa20": 5, "requireAboveMa20": True},
            "volume": {"minVolumeChange20": -1, "minUpDownVolumeRatio": 100, "maxDownVolumeRatio": 0,
                       "excludeBlowoff": True, "excludeDivergence": True, "excludeUpperShadow": True}}}
        before = deepcopy(raw)
        config = normalize_config(raw)
        self.assertEqual(normalize_config(config), config)
        self.assertEqual(normalize_screen_config(raw)["poolSettings"], config["poolSettings"])
        self.assertEqual(raw, before)
        config["poolSettings"]["trend"]["rankEnd"] = 1
        self.assertEqual(raw, before)

    def test_optional_numbers_reject_booleans_strings_nonfinite_and_outside_ranges(self):
        fields = [("universe", "minAmplitude20", [False, True, "0.1", -0.1, 10.1]),
                  ("trend", "rankWindow", [None, False, 1, 501, 3.5]),
                  ("trend", "rankStart", [None, True, 0, 100001, 1.5]),
                  ("trend", "rankEnd", [None, False, 0, 100001, 2.5]),
                  ("structure", "minMa10Above20Days", [False, True, -1, 21, 1.5]),
                  ("structure", "minMa5Above10Days", [False, "3", -1, 21, 2.5]),
                  ("structure", "maxPeakAgeDays", [False, -1, 500, 1.5]),
                  ("structure", "maxPriceVsMa20", [False, -0.1, 5.1, "0.08"]),
                  ("volume", "minVolumeChange20", [False, -1.1, 20.1, "0.1"]),
                  ("volume", "minUpDownVolumeRatio", [False, -0.1, 100.1, "1.3"]),
                  ("volume", "maxDownVolumeRatio", [False, -0.1, 100.1, "0.9"])]
        for pool, key, values in fields:
            for value in [*values, float("nan"), float("inf"), -float("inf")]:
                raw = {"universe": {key: value}} if pool == "universe" else {"poolSettings": {pool: {key: value}}}
                with self.subTest(pool=pool, key=key, value=value), self.assertRaises(ValueError):
                    normalize_config(raw)

    def test_flags_choices_unknown_keys_and_dependent_ranges_are_strict(self):
        for pool, keys in {"trend": ["rankEnabled"], "structure": ["requireAboveMa20"],
                           "volume": ["excludeBlowoff", "excludeDivergence", "excludeUpperShadow"]}.items():
            for key in keys:
                for value in (None, 0, 1, "false", [], {}):
                    with self.subTest(pool=pool, key=key, value=value), self.assertRaises(ValueError):
                        normalize_config({"poolSettings": {pool: {key: value}}})
        for raw in ({"poolSettings": {"structure": {"peakBasis": None}}},
                    {"poolSettings": {"structure": {"maDaysMode": "rolling"}}},
                    {"poolSettings": {"trend": {"rankStart": 501, "rankEnd": 500}}},
                    {"poolSettings": {"structure": {"maxPeakAgeDays": 20, "window": 20}}},
                    {"poolSettings": {"structure": {"maxPeakAgeDays": 3, "window": 2}}},
                    {"poolSettings": {"volume": {"excludeDiveregence": True}}},
                    {"universe": {"minAmplitdue20": .03}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_config(raw)

    def test_checkpoint_without_new_defaults_resumes_identically_but_changed_filter_is_rejected(self):
        data = dataset_fixture()
        config = normalize_config({**CONFIG, "startDate": data["calendar"][40], "endDate": data["calendar"][-1]})
        engine = PortfolioEngine(data, config)
        engine.step(8)
        checkpoint = engine.checkpoint()
        checkpoint["config"] = without_extensions(checkpoint["config"])
        original = deepcopy(checkpoint)
        resumed = PortfolioEngine(data, without_extensions(config), state=checkpoint)
        self.assertEqual(checkpoint, original)
        changed = deepcopy(config)
        changed["poolSettings"]["trend"]["rankEnabled"] = True
        with self.assertRaisesRegex(ValueError, "不能恢复"):
            PortfolioEngine(data, changed, state=checkpoint)
        engine.step(200)
        resumed.step(200)
        self.assertEqual(resumed.result(), engine.result())


if __name__ == "__main__":
    unittest.main()
