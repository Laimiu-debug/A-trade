"""Style presets only filter candidate data; entry signals remain independent."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from service.portfolio_config import (apply_pool_style, default_config,
                                      normalize_config, normalize_screen_config,
                                      normalize_selection, pool_style_catalogue)
from service.portfolio_signals import DailyScorer
from test_portfolio_service import dataset_fixture


def fixture():
    data = dataset_fixture(symbol_count=3, bar_count=150)
    cutoff = data["calendar"][130]
    for group, security in enumerate(data["securities"]):
        for index, bar in enumerate(security["bars"]):
            close = 10 + index * .12
            if group == 1 and 125 <= index <= 130:
                close = (10 + 124 * .12) * (1 - .004 * (index - 124))
            if group == 2:
                close = 20 - index * .01
            volume = 500000 if group == 1 and index >= 125 else 1000000
            if group == 2 and index == 130:
                volume = 2000000
            bar.update(open=close - .01, high=close + .02, low=close - .02,
                       close=close, volume=volume, amount=20000000)
    return data, cutoff


def config(style):
    return normalize_config(apply_pool_style({
        "strategies": [{"id": "one", "kind": "trendBreakout", "pool": "combined"}],
        "allocation": {"mode": "single", "strategyIds": ["one"]},
    }, style))


class StylePresetContractTests(unittest.TestCase):
    def test_complete_snapshots_round_trip_and_do_not_change_old_defaults(self):
        old = default_config()
        rows = pool_style_catalogue()
        self.assertEqual([row["id"] for row in rows], ["base", "breakout", "pullback", "momentum", "volume"])
        for row in rows:
            with self.subTest(style=row["id"]):
                self.assertEqual(normalize_screen_config({"poolSettings": row["poolSettings"]})["poolSettings"], row["poolSettings"])
                self.assertEqual(row["poolSettings"]["combined"]["mode"], "intersection")
                self.assertEqual([stage["pool"] for stage in row["stages"]], ["base"] + row["poolSettings"]["combined"]["pools"])
                self.assertTrue(row["notes"])
        self.assertEqual(default_config(), old)
        self.assertEqual(old["poolSettings"]["structure"]["minDrawdown"], .02)
        self.assertEqual(normalize_selection()["mode"], "all")

    def test_apply_preserves_base_strategy_and_other_fields_and_clears_stale_filters(self):
        raw = default_config()
        raw["universe"].update(minAmount20=12340000, minListingDays=99, strictHistory=True)
        raw["poolSettings"]["volume"].update(excludeDivergence=True, minUpDownVolumeRatio=1.8)
        original = deepcopy(raw)
        applied = apply_pool_style(raw, "pullback")
        self.assertEqual(raw, original)
        self.assertEqual({key: value for key, value in applied.items() if key != "poolSettings"},
                         {key: value for key, value in raw.items() if key != "poolSettings"})
        self.assertFalse(applied["poolSettings"]["volume"]["excludeDivergence"])
        self.assertIsNone(applied["poolSettings"]["volume"]["minUpDownVolumeRatio"])
        second = apply_pool_style(applied, "volume")
        self.assertIsNone(second["poolSettings"]["structure"]["minMa10Above20Days"])
        self.assertFalse(second["poolSettings"]["structure"]["requireAboveMa20"])
        second["universe"]["minAmount20"] = 0
        self.assertEqual(raw, original)
        rows = pool_style_catalogue()
        rows[1]["poolSettings"]["structure"]["minDrawdown"] = .7
        rows[0]["stages"][0]["reason"] = "changed"
        self.assertNotEqual(rows[1]["stages"][0]["reason"], "changed")
        self.assertEqual(pool_style_catalogue()[1]["poolSettings"]["structure"]["minDrawdown"], 0)

    def test_unknown_style_does_not_silently_choose_a_different_filter(self):
        for value in (None, [], "pullbak"):
            with self.subTest(style=value), self.assertRaises(ValueError):
                apply_pool_style({}, value)


class StylePresetBehaviorTests(unittest.TestCase):
    def test_new_high_is_a_breakout_candidate_even_before_volume_entry_confirmation(self):
        data, day = fixture()
        settings = config("breakout")
        scorer = DailyScorer(data, settings)
        screen = scorer.screen(day)
        symbol = "sh600000"
        self.assertEqual(screen["poolValuesBySymbol"][symbol]["drawdownPct"], 0)
        self.assertIn(symbol, screen["pools"]["combined"])
        self.assertNotIn(symbol, screen["pools"]["volume"], "the pool must not require the later strategy's volume event")
        first = scorer.daily(day)
        self.assertEqual(first["strategyEligibility"]["one"][symbol]["reason"], "noSignal")
        # The original 2% minimum is the conflict being avoided, not a waived
        # strategy condition. The strategy itself still controls the entry.
        legacy = deepcopy(settings)
        legacy["poolSettings"]["structure"]["minDrawdown"] = .02
        self.assertNotIn(symbol, DailyScorer(data, legacy).screen(day)["pools"]["combined"])
        data["securities"][0]["bars"][130]["volume"] = 2000000
        second = DailyScorer(data, settings)
        self.assertIn(symbol, second.screen(day)["pools"]["combined"])
        self.assertEqual(second.daily(day)["strategyEligibility"]["one"][symbol]["reason"], "signal")

    def test_pullback_accepts_qualifying_contraction_and_volume_style_does_not_impose_trend(self):
        data, day = fixture()
        pullback = DailyScorer(data, config("pullback")).screen(day)
        self.assertIn("sh600001", pullback["pools"]["combined"])
        self.assertNotIn("sh600001", pullback["pools"]["volume"])
        self.assertNotIn("sh600000", pullback["pools"]["combined"], "zero drawdown does not belong to the pullback style")
        active = DailyScorer(data, config("volume")).screen(day)
        self.assertIn("sh600002", active["pools"]["combined"])
        self.assertNotIn("sh600002", active["pools"]["trend"])

    def test_all_style_screens_avoid_strategy_formulas_and_use_the_same_daily_candidate_gate(self):
        data, day = fixture()
        for preset in pool_style_catalogue():
            with self.subTest(style=preset["id"]):
                settings = config(preset["id"])
                with patch.object(DailyScorer, "_signal", side_effect=AssertionError("pool invoked signal")), \
                     patch("service.portfolio_signals._compact_force", side_effect=AssertionError("pool invoked force formula")):
                    observed = DailyScorer(data, settings, pools_only=True).screen(day)
                daily = DailyScorer(data, settings).daily(day)
                self.assertEqual(observed["pools"], daily["pools"])
                for symbol in set(observed["pools"]["base"]) - set(observed["pools"]["combined"]):
                    self.assertEqual(daily["strategyEligibility"]["one"][symbol]["reason"], "poolFilter")

    def test_future_changes_cannot_change_historical_membership_or_evidence_for_any_style(self):
        data, day = fixture()
        future = deepcopy(data)
        for security in future["securities"]:
            for bar in security["bars"]:
                if bar["date"] > day:
                    for key in ("open", "high", "low", "close", "volume", "amount"):
                        bar[key] *= 3
        for preset in pool_style_catalogue():
            with self.subTest(style=preset["id"]):
                settings = config(preset["id"])
                self.assertEqual(DailyScorer(data, settings, pools_only=True).screen(day),
                                 DailyScorer(future, settings, pools_only=True).screen(day))

    def test_short_history_is_unknown_not_fabricated_rank_or_passing_structure(self):
        data = dataset_fixture(symbol_count=1, bar_count=30)
        raw = apply_pool_style({"universe": {"minListingDays": 0}}, "momentum")
        result = DailyScorer(data, raw, pools_only=True).screen(data["calendar"][-1])
        self.assertEqual(result["pools"]["base"], ["sh600000"])
        self.assertEqual(result["pools"]["combined"], [])
        self.assertIsNone(result["poolValuesBySymbol"]["sh600000"]["returnRank"])
        self.assertIsNone(result["poolEvidenceBySymbol"]["sh600000"]["trend"]["passed"])


if __name__ == "__main__":
    unittest.main()
