import copy
import json
import unittest
from unittest.mock import patch

from service import market_research as research
from service.market_style import profile, STREAK_CAP
from tests.test_market_research import security


def metrics(**changes):
    result = {"sampleCount": 1000, "expectedCount": 1100, "coverage": 1000 / 1100,
              "medianReturn20": 6, "medianReturn5": 2, "medianChangePercent": .5,
              "breadth": .65, "advanceRatio": .6, "declineRatio": .35,
              "breadthComparableCount": 900, "breadthChange5": 6,
              "medianAmountRatio20": 1.3, "amountRatio20Count": 950,
              "preferenceAmountCount": 1000,
              "limitUpApprox": 40, "limitDownApprox": 0,
              "leadership": {"promotion": {"all": {"eligible": 20, "observed": 20, "missing": 0, "rate": .6}},
                             "premium": {"eligible": 40, "observed": 40, "missing": 0, "median": 1.2},
                             "trend": {"eligible": 1000, "amountShare": .5},
                             "highElasticity": {"count": 30, "amountShare": .2},
                             "highBoardPromotion": {"eligible": 5, "observed": 5, "rate": .2}}}
    result.update(changes)
    return result


def dimensions(result):
    return {row["id"]: row for row in result["dimensions"]}


class ProfileRules(unittest.TestCase):
    def test_six_dimensions_coexist_and_dont_invent_partitions_or_identity(self):
        result = profile(metrics())
        rows = dimensions(result)
        self.assertEqual(set(rows), {"trend", "breadth", "activity", "relay", "preference", "risk"})
        self.assertEqual(rows["trend"]["key"], "uptrend")
        self.assertEqual(rows["breadth"]["key"], "expanding")
        self.assertEqual(rows["activity"]["key"], "expanding_up")
        self.assertEqual(rows["preference"]["key"], "parallel")
        self.assertIn("可能重叠", rows["preference"]["reason"])
        self.assertEqual(result["status"], "observed")
        self.assertEqual(rows["risk"]["key"], "low", "small high-board cohort cannot create broad market pressure")

    def test_weak_repair_and_strong_trend_pullback_are_different(self):
        repair = dimensions(profile(metrics(medianReturn20=-5, medianReturn5=2)))
        pullback = dimensions(profile(metrics(medianReturn20=6, medianReturn5=-2)))
        self.assertEqual(repair["trend"]["key"], "weak_rebound")
        self.assertEqual(pullback["trend"]["key"], "trend_pullback")

    def test_contraction_is_not_automatically_positive(self):
        result = dimensions(profile(metrics(medianAmountRatio20=.65, medianChangePercent=-.8, declineRatio=.65)))
        self.assertEqual(result["activity"]["key"], "contracting_down")
        self.assertEqual(result["activity"]["tone"], "warning")
        self.assertEqual(result["risk"]["key"], "rising")

    def test_flat_stocks_are_not_inferred_to_be_decliners(self):
        flat = dimensions(profile(metrics(advanceRatio=0, declineRatio=0, breadth=.2, breadthChange5=0,
                                          medianChangePercent=0, medianReturn5=0)))
        declining = dimensions(profile(metrics(advanceRatio=.05, declineRatio=.9, breadth=.2, breadthChange5=0,
                                               medianChangePercent=-2)))
        self.assertEqual(flat["breadth"]["key"], "narrow_base")
        self.assertNotEqual(flat["risk"]["key"], "high")
        self.assertEqual(declining["breadth"]["key"], "broad_weakness")
        self.assertEqual(declining["risk"]["key"], "high")

    def test_no_sample_is_not_zero_or_negative_evidence(self):
        global_missing = profile(metrics(expectedCount=999, coverage=1))
        self.assertEqual(global_missing["status"], "insufficient")
        self.assertTrue(all(row["status"] == "insufficient" for row in global_missing["dimensions"]))
        missing = dimensions(profile(metrics(medianAmountRatio20=None, amountRatio20Count=0)))
        self.assertEqual(missing["activity"]["status"], "insufficient")
        self.assertIsNone(missing["activity"]["evidence"][0]["value"])
        self.assertEqual(missing["trend"]["status"], "observed")

    def test_tiny_or_incomplete_relay_cohort_stays_insufficient(self):
        data = metrics()
        for bucket in ({"eligible": 1, "observed": 1, "rate": 1},
                       {"eligible": 100, "observed": 10, "rate": 1}):
            data["leadership"]["promotion"]["all"] = bucket
            relay = dimensions(profile(data))["relay"]
            self.assertEqual(relay["status"], "insufficient")
            self.assertEqual(relay["evidence"][0]["value"], 1, "preserve observed rate without extrapolating")
            evidence_map = {item["label"]: item["value"] for item in relay["evidence"]}
            self.assertEqual(evidence_map["昨日连板应观察"], bucket["eligible"])
            self.assertEqual(evidence_map["连板核验覆盖"], bucket["observed"] / bucket["eligible"])

    def test_preference_requires_coverage_not_just_absolute_number(self):
        data = metrics(sampleCount=5000, expectedCount=5000, coverage=1)
        result = dimensions(profile(data))["preference"]
        self.assertEqual(result["status"], "insufficient", "1000 continuous stocks must not stand in for 5000 valid stocks")
        self.assertEqual(next(item["value"] for item in result["evidence"] if item["label"] == "偏好样本覆盖"), .2)

    def test_every_risk_trigger_has_inspectable_evidence(self):
        result = dimensions(profile(metrics(medianReturn5=-4)))["risk"]
        self.assertEqual(result["key"], "rising")
        self.assertEqual(next(item["value"] for item in result["evidence"] if item["label"] == "5 日中位涨幅"), -4)
        self.assertEqual(next(item["value"] for item in result["evidence"] if item["label"] == "高标应观察样本"), 5)
        self.assertEqual(next(item["value"] for item in result["evidence"] if item["label"] == "高标核验覆盖"), 1)

    def test_changes_and_durations_never_bridge_missing_days(self):
        current = None
        for _ in range(50):
            current = profile(metrics(), current)
        self.assertEqual(dimensions(current)["trend"]["streakDays"], STREAK_CAP)
        self.assertTrue(dimensions(current)["trend"]["durationCapped"])
        changed = profile(metrics(medianReturn5=-2), current)
        self.assertEqual(dimensions(changed)["trend"]["streakDays"], 1)
        self.assertIn({"id": "trend", "title": "趋势阶段", "from": "上行延续", "to": "趋势回调"}, changed["changes"])
        missing = profile(metrics(expectedCount=1), changed)
        restored = profile(metrics(), missing)
        self.assertEqual(restored["changes"], [])
        self.assertEqual(dimensions(restored)["trend"]["streakDays"], 1)


class ProfileAggregation(unittest.TestCase):
    def snapshot(self, sources, **kwargs):
        with patch.object(research, "MIN_MARKET_SYMBOLS", 1):
            return research.build_snapshot(sources, total=len(sources), **kwargs)

    def test_amount_median_uses_each_stock_prior_twenty_excludes_current(self):
        sources = [security(f"sh{600001+i}", amount=amount) for i, amount in enumerate((1000, 100000, 100000000))]
        for source, ratio in zip(sources, (.5, 1, 2)):
            day, close, volume, amount = source["rows"][-1]
            source["rows"][-1] = (day, close, volume, amount * ratio)
        result = self.snapshot(sources)
        current = result["style"]["metrics"]
        self.assertEqual(current["medianAmountRatio20"], 1)
        self.assertEqual(current["amountRatio20Count"], 3)
        self.assertEqual(dimensions(result["style"]["profile"])["activity"]["key"], "balanced")
        changed = copy.deepcopy(sources)
        day, close, volume, _ = changed[0]["rows"][-2]
        changed[0]["rows"][-2] = (day, close, volume, 0)
        invalid = self.snapshot(changed)["style"]
        self.assertEqual(invalid["metrics"]["amountRatio20Count"], 2)
        self.assertEqual(dimensions(invalid["profile"])["activity"]["status"], "insufficient")

    def test_five_day_breadth_uses_common_qualified_securities(self):
        sources = [security(f"sh{600001+i}") for i in range(3)]
        new = security("sh600004", prices=[30-i/10 for i in range(90)])
        new["rows"] = new["rows"][-60:]
        result = self.snapshot(sources + [new])
        current, previous = result["style"]["metrics"], result["timeline"][-6]["metrics"]
        self.assertEqual(current["breadth"], .75)
        self.assertEqual(previous["breadth"], 1)
        self.assertEqual(current["breadthComparableCount"], 3)
        self.assertEqual(current["breadthChange5"], 0, "newly eligible weak stock must not fabricate deterioration in a fixed cohort")

    def test_zero_amount_majority_cannot_fabricate_market_preference(self):
        sources = [security(f"sh{600000+i}", count=65, amount=0 if i < 950 else 100000) for i in range(1000)]
        result = research.build_snapshot(sources, total=1000)
        current = result["style"]
        self.assertEqual(current["metrics"]["leadership"]["trend"]["eligible"], 1000)
        self.assertEqual(current["metrics"]["leadership"]["trend"]["amountShare"], 1)
        self.assertEqual(current["metrics"]["preferenceAmountCount"], 50)
        self.assertEqual(dimensions(current["profile"])["preference"]["status"], "insufficient")

    def test_future_bars_do_not_rewrite_past_profile(self):
        source = security(count=110)
        past = copy.deepcopy(source)
        past["rows"] = source["rows"][:90]
        snapshot = self.snapshot([past])
        extended = self.snapshot([source], history_days=110)
        historical = next(row for row in extended["timeline"] if row["date"] == snapshot["asOf"])
        self.assertEqual(snapshot["style"]["profile"], historical["profile"])
        source["rows"][-1] = (*source["rows"][-1][:1], 2, 1000, 5000000)
        mutated = self.snapshot([source], history_days=110)
        self.assertEqual(historical["profile"], next(row for row in mutated["timeline"] if row["date"] == snapshot["asOf"])["profile"])

    def test_history_window_has_same_states_changes_and_capped_durations(self):
        price, prices = 20., []
        for i in range(300):
            price *= 1.004 if i // 40 % 2 == 0 else .996
            prices.append(price)
        source = security(prices=prices)
        full = self.snapshot([source], history_days=250, history_bars=350)
        short_source = copy.deepcopy(source)
        short_source["rows"] = short_source["rows"][-(60 + research.WARMUP_RECORDS):]
        short = self.snapshot([short_source], history_days=60, history_bars=60 + research.WARMUP_RECORDS)
        self.assertEqual([row["profile"] for row in full["timeline"][-60:]], [row["profile"] for row in short["timeline"]])

    def test_small_and_empty_snapshots_serialize_without_nan(self):
        small = research.build_snapshot([security()], total=1)
        empty = research.build_snapshot([], total=0)
        for result in (small, empty):
            self.assertEqual(result["style"]["profile"]["status"], "insufficient")
            self.assertEqual(len(result["style"]["profile"]["dimensions"]), 6)
            json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
