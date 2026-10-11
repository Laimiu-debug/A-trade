"""Causal formula parity and explicit composition for FINAL-TRADE force signals.

Frozen numbers were calculated from the source ths_volume_signal.py, not from
this module. Tests have no runtime dependency on the other local repository.
"""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from service.strategies import (FORCE_VERSION, catalogue, evaluate, normalize_config,
                                prepare_bars, required_history, strategy_conflicts)


PRICES = [10, 11, 10, 9, 10, 11, 10, 9, 12, 13, 12, 11, 10, 9, 10, 12, 13, 12, 11, 12]
VOLUMES = [1000, 1200, 900, 1600, 1900, 1100, 1000, 2100, 3000, 1800, 900, 800, 3000, 900, 1900, 2100, 1200, 1900, 2300, 2900]


def rows(prices=PRICES, volumes=None):
    return [{"date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(), "open": close,
             "high": close, "low": close, "close": close, "volume": (volumes or [1000]*len(prices))[i]}
            for i, close in enumerate(prices)]


def config(ids=None, params=None):
    return normalize_config({"strategyIds": ids or ["forceGoldenCross"], "strategyParams": params or {}})


class ForceFormulaTests(unittest.TestCase):
    def test_frozen_original_formula_vectors_and_events(self):
        prepared = prepare_bars(rows(PRICES, VOLUMES))
        expected = [
            (2, 3.5, .545455, False, False), (3, 3.75, 1.961433, False, False),
            (4, 5.041667, 3.11996, False, False), (5, 7.520833, 3.522391, False, False),
            (6, 8.760417, 3.488017, False, False), (7, 6.213542, 4.73262, False, False),
            (8, 8.106771, 5.750932, True, False), (9, 12.053385, 5.978035, False, False),
            (10, 14.026693, 5.436574, False, False), (13, 2.503337, 8.846428, False, False),
            (14, 4.418335, 9.601623, True, False), (15, 8.875834, 8.401328, False, True),
            (19, 8.846406, 9.163794, True, False),
        ]
        for index, main, retail, turn, cross in expected:
            feature = prepared["force"][index]
            with self.subTest(index=index):
                self.assertAlmostEqual(feature["mainForce"], main, delta=.00000051)
                self.assertAlmostEqual(feature["retailForce"], retail, delta=.00000051)
                self.assertEqual(feature["purpleToYellow"], turn)
                self.assertEqual(feature["goldenCross"], cross)

    def test_frozen_original_composite_scores(self):
        features = prepare_bars(rows(PRICES, VOLUMES))["force"]
        expected = {
            8: (8.22, 16.78, 83.22, 1.0828, 1.4096, 43.31),
            14: (17.45, 0., 100., 1.1767, .4602, 42.37),
            15: (27.63, 2.7, 97.3, 2.5647, 1.0565, 50.13),
            19: (2.58, 0., 100., .8812, .9654, 35.24),
        }
        keys = ["mainForcePowerScore", "forceGapScore", "retailPressureScore", "mainForceExplosionRatio", "mainVsRetailRatio", "signalScore"]
        for index, values in expected.items():
            self.assertEqual(tuple(features[index][key] for key in keys), values)

    def test_warmup_mask_matches_chart_and_trading_uses_fixed_30_bars(self):
        data = rows(PRICES*4, VOLUMES*4)
        prepared = prepare_bars(data)
        self.assertIsNone(prepared["force"][0]["mainForce"])
        self.assertIsNone(prepared["force"][1]["retailForce"])
        self.assertFalse(prepared["force"][2]["goldenCross"])
        self.assertFalse(prepared["force"][3]["purpleToYellow"])
        settings = config()
        self.assertEqual(required_history(settings), 30)
        self.assertTrue(all(evaluate(data, i, settings, prepared) is None for i in range(30)))
        self.assertIsNotNone(evaluate(data, 36, settings, prepared))

    def test_prefix_and_future_append_preserve_all_precomputed_values_and_signals(self):
        data = rows(PRICES*5, VOLUMES*5)
        original = deepcopy(data)
        full = prepare_bars(data)
        settings = config()
        for end in (5, 19, 37, 58, 80):
            prefix = data[:end]
            prepared = prepare_bars(prefix)
            self.assertEqual(prepared["force"], full["force"][:end])
            for index in range(end):
                self.assertEqual(evaluate(prefix, index, settings, prepared), evaluate(data, index, settings, full))
        changed = deepcopy(data)
        for i in range(60, len(changed)):
            changed[i]["close"] = 40 + i
            changed[i]["volume"] = 5000000
        self.assertEqual(prepare_bars(changed)["force"][:60], full["force"][:60])
        self.assertEqual(data, original)

    def test_zero_volume_and_flat_closes_do_not_invent_force_events(self):
        for data in (rows([10]*80), rows(PRICES*4, [0]*80)):
            prepared = prepare_bars(data)
            for feature in prepared["force"]:
                self.assertFalse(feature["goldenCross"])
                self.assertFalse(feature["purpleToYellow"])
                self.assertIsNone(feature["forceDominance"])
            self.assertTrue(all(evaluate(data, i, config(), prepared) is None for i in range(len(data))))

    def test_units_are_converted_from_shares_once_and_events_are_scale_invariant(self):
        data = rows(PRICES*4, VOLUMES*4)
        scaled = deepcopy(data)
        for bar in scaled:
            bar["volume"] *= 100
        a, b = prepare_bars(data)["force"], prepare_bars(scaled)["force"]
        for first, second in zip(a[2:], b[2:]):
            self.assertAlmostEqual(second["mainForce"], first["mainForce"] * 100)
            self.assertAlmostEqual(second["retailForce"], first["retailForce"] * 100)
            self.assertEqual(first["goldenCross"], second["goldenCross"])
            self.assertEqual(first["purpleToYellow"], second["purpleToYellow"])
            self.assertAlmostEqual(first["forceDominance"], second["forceDominance"])

    def test_prepare_rejects_invalid_numeric_inputs_and_date_order(self):
        for value in (-1, float("nan"), float("inf"), True, 10**1000):
            data = rows()
            data[5]["volume"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare_bars(data)
        data = rows()
        data[3]["date"] = data[2]["date"]
        with self.assertRaises(ValueError):
            prepare_bars(data)


class ForceCandidateTests(unittest.TestCase):
    def test_catalogue_and_parameters_do_not_claim_real_fund_flows(self):
        strategies = {item["id"]: item for item in catalogue()}
        self.assertIn("不是真实", strategies["forceGoldenCross"]["caveats"])
        self.assertIn("简化候选", strategies["forceTrend"]["caveats"])
        self.assertEqual(config()["strategyParams"]["forceGoldenCross"], {"minDominance": 0})
        self.assertEqual(config()["strategyParams"]["forceTrend"], {"confirmDays": 3, "minDominance": .1})
        for values in ({"confirmDays": 0}, {"confirmDays": 2.5}, {"confirmDays": True}, {"minDominance": float("nan")}, {"minDominance": -1}, {"emaWindow": 10}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                config(["forceTrend"], {"forceTrend": values})

    def test_golden_cross_uses_force_not_price_moving_average(self):
        data = rows(PRICES*4, VOLUMES*4)
        prepared = prepare_bars(data)
        settings = config()
        for index in range(30, len(data)):
            expected = prepared["force"][index]["goldenCross"]
            self.assertEqual(evaluate(data, index, settings, prepared) is not None, expected)
        signal = evaluate(data, 36, settings, prepared)
        self.assertTrue(signal["strategies"][0]["goldenCross"])
        self.assertEqual(signal["strategies"][0]["formulaVersion"], FORCE_VERSION)
        self.assertNotIn("trendAverage", signal["strategies"][0])

    def test_force_trend_requires_consecutive_dominance_and_current_rise(self):
        data = rows([10-i*.01 for i in range(30)] + [9.7+i*.02 for i in range(50)])
        prepared = prepare_bars(data)
        settings = config(["forceTrend"])
        triggered = []
        for index in range(30, len(data)):
            current = prepared["force"][index]
            recent = prepared["force"][index-2:index+1]
            expected = current["mainForceState"] == "rising" and all(item["mainForce"] > item["retailForce"] and item["forceDominance"] >= .1 for item in recent)
            signal = evaluate(data, index, settings, prepared)
            self.assertEqual(signal is not None, expected)
            if signal:
                triggered.append(index)
        self.assertGreater(len(triggered), 10)
        self.assertTrue(any(not prepared["force"][index]["purpleToYellow"] for index in triggered))

    def test_all_selected_rules_remain_and_including_provable_force_conflict(self):
        data = rows(PRICES*4, VOLUMES*4)
        prepared = prepare_bars(data)
        both = config(["forceGoldenCross", "forceTrend"])
        self.assertTrue(strategy_conflicts(both))
        self.assertTrue(all(evaluate(data, i, both, prepared) is None for i in range(len(data))))
        compatible = config(["forceGoldenCross", "forceTrend"], {"forceTrend": {"confirmDays": 1, "minDominance": 0}})
        self.assertFalse(strategy_conflicts(compatible))
        self.assertIsNotNone(evaluate(data, 36, compatible, prepared))
        stricter = config(["forceGoldenCross", "trendBreakout"])
        self.assertIsNone(evaluate(data, 36, stricter, prepared))
        self.assertEqual(required_history(stricter), 80)

    def test_no_hidden_ema_recomputation_in_daily_evaluation(self):
        data = rows(PRICES*20, VOLUMES*20)
        prepared = prepare_bars(data)
        settings = config()
        with patch("service.strategies.prepare_bars", side_effect=AssertionError("EMA must be prepared once")):
            signals = [evaluate(data, i, settings, prepared) for i in range(len(data))]
        self.assertTrue(any(signals))
        with self.assertRaisesRegex(ValueError, "prepare_bars"):
            evaluate(data, 36, settings)
        broken = deepcopy(prepared)
        broken["force"][36]["date"] = "1999-01-01"
        with self.assertRaises(ValueError):
            evaluate(data, 36, settings, broken)

    def test_zero_volume_inside_warmup_blocks_trade_without_rewriting_formula(self):
        data = rows(PRICES*4, VOLUMES*4)
        data[35]["volume"] = 0
        prepared = prepare_bars(data)
        for index in range(35, 66):
            self.assertIsNone(evaluate(data, index, config(), prepared))


if __name__ == "__main__":
    unittest.main()
