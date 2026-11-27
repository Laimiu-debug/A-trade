"""Unit tests for the Dynamic Multi-Axis Market Style v2 engine."""
import copy
import json
import unittest
from unittest.mock import patch

from service import market_research as research
from service.market_style_v2 import DynamicStyleTracker, compute_dynamic_timeline
from tests.test_market_research import security


def _sample_metrics(**overrides):
    base = {
        "sampleCount": 2000,
        "expectedCount": 2100,
        "coverage": 2000 / 2100,
        "breadth": 0.62,
        "advanceRatio": 0.64,
        "declineRatio": 0.30,
        "medianChangePercent": 0.65,
        "medianReturn5": 2.4,
        "medianReturn20": 5.5,
        "medianAmountRatio20": 1.18,
        "amountRatio20Count": 1900,
        "preferenceAmountCount": 1950,
        "breadthComparableCount": 1900,
        "breadthChange5": 5.0,
        "limitUpApprox": 55,
        "limitDownApprox": 2,
        "multiBoardApprox": 12,
        "maxBoardHeight": 4,
        "totalAmountYi": 18500.0,
        "boardStats": {
            "growth20Count": 700,
            "growth20Breadth": 0.72,
            "growth20MedianRet5": 4.2,
            "growth20MedianRet20": 9.5,
            "growth20AmountShare": 0.42,
            "main10Count": 1300,
            "main10Breadth": 0.56,
            "main10MedianRet5": 1.2,
            "main10MedianRet20": 3.2,
            "main10AmountShare": 0.58,
            "coreTop10Ret5": 4.5,
            "coreTop10Ret20": 10.2,
            "coreTop10AmountShare": 0.48,
        },
        "leadership": {
            "promotion": {"all": {"eligible": 18, "observed": 18, "missing": 0, "rate": 0.44}},
            "premium": {"eligible": 45, "observed": 45, "missing": 0, "median": 1.8},
            "trend": {"eligible": 2000, "amountShare": 0.52},
            "highElasticity": {"count": 40, "amountShare": 0.16},
            "highBoardPromotion": {"eligible": 8, "observed": 8, "rate": 0.50},
        },
    }
    for k, v in overrides.items():
        if k == "boardStats" and isinstance(v, dict):
            base["boardStats"] = {**base["boardStats"], **v}
        elif k == "leadership" and isinstance(v, dict):
            base["leadership"] = {**base["leadership"], **v}
        else:
            base[k] = v
    return base


class DynamicStyleV2Tests(unittest.TestCase):
    def test_20cm_growth_bull_and_track_shift_to_10cm_mainboard(self):
        tracker = DynamicStyleTracker()
        # First 6 sessions: 20cm tech bull
        for _ in range(6):
            res = tracker.step(_sample_metrics())
        self.assertEqual(res["regimeKey"], "growth_20cm_bull")
        self.assertGreater(res["boardAxis"], 20.0)
        self.assertIn("wujiang_mainline_v1", [s["id"] for s in res["strategyGuide"]["matchedStrategies"]])

        # Suddenly 20cm tech fades while 10cm mainboard themes surge (Wujiang Aug/Oct switch!)
        shifted = tracker.step(_sample_metrics(
            breadth=0.46,
            advanceRatio=0.48,
            medianReturn5=-0.4,
            medianReturn20=0.8,
            maxBoardHeight=6,
            multiBoardApprox=24,
            boardStats={
                "growth20Breadth": 0.30,
                "growth20MedianRet5": -3.8,
                "growth20MedianRet20": -2.0,
                "growth20AmountShare": 0.32,
                "main10Breadth": 0.55,
                "main10MedianRet5": 2.4,
                "main10MedianRet20": 4.0,
                "main10AmountShare": 0.68,
            },
        ))
        self.assertLess(shifted["boardAxis"], -15.0)
        self.assertEqual(shifted["regimeKey"], "main_10cm_relay")
        alert_ids = [a["id"] for a in shifted["shiftAlerts"]]
        self.assertIn("track_shift_to_10cm", alert_ids)
        self.assertIn("first_to_second_v1", [s["id"] for s in shifted["strategyGuide"]["matchedStrategies"]])

    def test_top_divergence_early_warning_fires_before_collapse(self):
        tracker = DynamicStyleTracker()
        for _ in range(5):
            tracker.step(_sample_metrics())
        div = tracker.step(_sample_metrics(
            breadth=0.58,
            advanceRatio=0.32,
            medianChangePercent=-0.85,
            medianAmountRatio20=1.32,
            breadthChange5=-8.5,
            medianReturn5=-0.8,
        ))
        self.assertEqual(div["regimeKey"], "top_divergence")
        self.assertIn("top_divergence_warn", [a["id"] for a in div["shiftAlerts"]])

    def test_ice_point_repair_triggers_trial_window(self):
        tracker = DynamicStyleTracker()
        # 5 ice-point defense sessions
        for _ in range(5):
            cold = tracker.step(_sample_metrics(
                breadth=0.18,
                advanceRatio=0.20,
                declineRatio=0.78,
                medianChangePercent=-1.8,
                medianReturn5=-5.5,
                medianReturn20=-9.0,
                limitUpApprox=15,
                limitDownApprox=35,
                medianAmountRatio20=0.72,
            ))
        self.assertEqual(cold["regimeKey"], "full_defense")
        self.assertLess(cold["offenseScore"], 30.0)

        # Sharp reversal day out of ice point
        repair = tracker.step(_sample_metrics(
            breadth=0.35,
            advanceRatio=0.78,
            declineRatio=0.18,
            medianChangePercent=1.9,
            medianReturn5=-1.0,
            medianReturn20=-6.5,
            limitUpApprox=65,
            limitDownApprox=2,
            medianAmountRatio20=1.08,
        ))
        self.assertEqual(repair["regimeKey"], "ice_repair")
        self.assertIn("ice_point_window", [a["id"] for a in repair["shiftAlerts"]])

    def test_insufficient_coverage_never_fabricates_regime(self):
        res = compute_dynamic_timeline([_sample_metrics(expectedCount=500, sampleCount=450)])[0]
        self.assertEqual(res["status"], "insufficient")
        self.assertEqual(res["regimeKey"], "insufficient")
        self.assertIsNone(res["offenseScore"])
        self.assertEqual(res["shiftAlerts"], [])

    def test_build_snapshot_dynamic_has_no_future_leakage_and_serializes_cleanly(self):
        # Mix 20cm and 10cm symbols so boardAxis is actively computed too
        s_main = [security(f"sh{600001 + i}", count=120, prices=[10 + 0.08 * j for j in range(120)], amount=2e8) for i in range(4)]
        s_20cm = [security(f"sh{688001 + i}", count=120, prices=[20 + 0.25 * j for j in range(120)], amount=3e8) for i in range(4)]
        sources = s_main + s_20cm

        past = copy.deepcopy(sources)
        for item in past:
            item["rows"] = item["rows"][:90]

        with patch.object(research, "MIN_MARKET_SYMBOLS", 4):
            snap_past = research.build_snapshot(past, total=len(past), history_days=30, history_bars=120)
            snap_full = research.build_snapshot(sources, total=len(sources), history_days=60, history_bars=120)

        hist_at_past = next(row for row in snap_full["timeline"] if row["date"] == snap_past["asOf"])
        self.assertEqual(snap_past["style"]["dynamic"], hist_at_past["dynamic"])
        self.assertIsNotNone(snap_full["style"]["dynamic"]["boardAxis"])
        self.assertGreater(snap_full["style"]["dynamic"]["boardAxis"], 0)
        json.dumps(snap_full, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
