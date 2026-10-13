"""Formula/boundary tests for optional price ranking, MA persistence and volume risks."""
from copy import deepcopy
import datetime as dt
import unittest

from service.portfolio_config import normalize_screen_config
from service.portfolio_screen import PortfolioScreen, paginate_screen
from service.portfolio_signals import DailyScorer


def security(symbol="sh600001", closes=None, volumes=None):
    closes = [10.] * 60 if closes is None else closes
    start = dt.date(2025, 1, 1)
    rows = []
    for index, close in enumerate(closes):
        rows.append({"date": (start + dt.timedelta(days=index)).isoformat(), "open": close,
                     "high": close + .1, "low": close - .1, "close": close,
                     "volume": volumes[index] if volumes else 1000000, "amount": 20000000})
    return {"symbol": symbol, "bars": rows}


def config():
    return normalize_screen_config({"universe": {"minListingDays": 0, "minAmount20": 0},
        "poolSettings": {"trend": {"window": 2, "minReturn": -1},
                         "structure": {"window": 20, "minDrawdown": 0, "maxDrawdown": .99},
                         "volume": {"window": 5, "minRatio": .01}}})


def run(rows, cfg):
    task = PortfolioScreen({"securities": rows}, cfg)
    task.step()
    return task.result()


def row_for(report, symbol="sh600001"):
    return next(row for pool in ("base", "excluded") for row in report["screenPools"][pool] if row["symbol"] == symbol)


def check(row, pool, key):
    return next(item for item in row["poolEvidence"][pool]["conditions"] if item["key"] == key)


class AdvancedPoolCalculationTests(unittest.TestCase):
    def test_ranking_full_base_before_trend_filters_ties_and_pagination(self):
        highest = security("sh600003", [10.] * 57 + [20., 30., 25.])  # ranked first but fails current MA trend
        tied_a = security("sh600001", [10.] * 57 + [18., 19., 20.])
        tied_b = security("sh600002", [10.] * 57 + [18., 19., 20.])
        short = security("sh600004")
        short["bars"] = short["bars"][-30:]
        excluded = security("sh600005", [10.] * 59 + [200.])
        day = highest["bars"][-1]["date"]
        excluded["stByDate"] = {day: True}
        cfg = config()
        cfg["poolSettings"]["trend"].update(rankEnabled=True, rankWindow=40, rankStart=2, rankEnd=2)
        data = [highest, tied_b, short, excluded, tied_a]
        report = run(data, cfg)
        self.assertEqual([row["symbol"] for row in report["screenPools"]["trend"]], ["sh600001"])
        for symbol, rank in (("sh600003", 1), ("sh600001", 2), ("sh600002", 3)):
            row = row_for(report, symbol)
            self.assertEqual(check(row, "trend", "returnRank")["actual"], rank)
            self.assertEqual(row["poolEvidence"]["trend"]["rankUniverseCount"], 3)
            self.assertEqual(row["poolEvidence"]["trend"]["rankUnavailableCount"], 1)
        self.assertIsNone(check(row_for(report, "sh600004"), "trend", "returnRank")["actual"])
        self.assertEqual(run(list(reversed(data)), cfg)["screenPools"], report["screenPools"])
        searched = paginate_screen(report, {"pool": "base", "poolQuery": "sh600002", "poolPageSize": 1})
        self.assertEqual(check(searched["screenPools"]["base"][0], "trend", "returnRank")["actual"], 3)
        scorer = DailyScorer({"securities": data}, cfg)
        self.assertEqual(scorer.daily(day)["pools"]["trend"], ["sh600001"])

    def test_ma_count_and_trailing_consecutive_are_distinct_strict_and_require_39_bars(self):
        stock = security(closes=[10 + index * .2 for index in range(59)] + [1.])
        cfg = config()
        cfg["poolSettings"]["structure"].update(minMa10Above20Days=19, minMa5Above10Days=19)
        count = row_for(run([stock], cfg))
        self.assertEqual(check(count, "structure", "ma10Above20Days")["actual"], 19)
        self.assertEqual(check(count, "structure", "ma5Above10Days")["actual"], 19)
        self.assertTrue(count["poolEvidence"]["structure"]["inPool"])
        cfg["poolSettings"]["structure"]["maDaysMode"] = "consecutive"
        consecutive = row_for(run([stock], cfg))
        self.assertEqual(check(consecutive, "structure", "ma10Above20Days")["actual"], 0)
        self.assertFalse(consecutive["poolEvidence"]["structure"]["inPool"])
        flat = row_for(run([security()], cfg))
        self.assertEqual(check(flat, "structure", "ma10Above20Days")["actual"], 0, "equal MAs do not count")
        stock["bars"] = stock["bars"][-38:]
        missing = check(row_for(run([stock], cfg)), "structure", "ma10Above20Days")
        self.assertIsNone(missing["actual"])
        self.assertEqual(missing["status"], "unknown")

    def test_peak_basis_and_recent_equal_peak_age_use_the_same_selected_window(self):
        stock = security(closes=[10.] * 56 + [10., 12., 11., 12.])
        stock["bars"][-3]["high"] = 15
        cfg = config()
        cfg["poolSettings"]["structure"].update(window=4, maxPeakAgeDays=1)
        closing = row_for(run([stock], cfg))
        self.assertEqual(check(closing, "structure", "peakAgeDays")["actual"], 0)
        self.assertEqual(check(closing, "structure", "drawdown")["actual"], 0)
        cfg["poolSettings"]["structure"]["peakBasis"] = "high"
        high = row_for(run([stock], cfg))
        self.assertEqual(check(high, "structure", "peakAgeDays")["actual"], 2)
        self.assertAlmostEqual(check(high, "structure", "drawdown")["actual"], .2)
        self.assertFalse(high["poolEvidence"]["structure"]["inPool"])

    def test_absolute_ma_deviation_and_strict_price_above_boundary(self):
        cfg = config()
        cfg["poolSettings"]["structure"]["requireAboveMa20"] = True
        flat = row_for(run([security()], cfg))
        self.assertFalse(check(flat, "structure", "priceAboveMa20")["passed"])
        self.assertEqual(check(flat, "structure", "priceAboveMa20")["operator"], ">")
        cfg["poolSettings"]["structure"].update(requireAboveMa20=False, maxPriceVsMa20=.1)
        below = row_for(run([security(closes=[10.] * 59 + [5.])], cfg))
        self.assertAlmostEqual(check(below, "structure", "absPriceVsMa20")["actual"], abs(5 / 9.75 - 1))
        self.assertFalse(check(below, "structure", "absPriceVsMa20")["passed"])

    def test_decimal_flat_prices_do_not_create_ma_signals_or_nonzero_deviation(self):
        for price in (10.01, 10.03, 3.33):
            for size in (39, 600):
                for mode in ("count", "consecutive"):
                    with self.subTest(price=price, size=size, mode=mode):
                        stock = security(closes=[price] * size)
                        cfg = config()
                        cfg["poolSettings"]["structure"].update(
                            maDaysMode=mode, minMa10Above20Days=1, minMa5Above10Days=1,
                            requireAboveMa20=True, maxPriceVsMa20=0)
                        row = row_for(run([stock], cfg))
                        self.assertEqual(check(row, "structure", "ma10Above20Days")["actual"], 0)
                        self.assertEqual(check(row, "structure", "ma5Above10Days")["actual"], 0)
                        self.assertFalse(check(row, "structure", "priceAboveMa20")["passed"])
                        self.assertEqual(check(row, "structure", "absPriceVsMa20")["actual"], 0)
                        self.assertTrue(check(row, "structure", "absPriceVsMa20")["passed"])
                        self.assertFalse(row["poolEvidence"]["structure"]["inPool"])
                        # The legacy trend comparisons retain their previous exact semantics.
                        for key in ("closeAboveAverage", "averageRising"):
                            item = check(row, "trend", key)
                            self.assertEqual(item["passed"], item["actual"] > item["threshold"])
                        cfg["poolSettings"]["structure"].update(
                            minMa10Above20Days=0, minMa5Above10Days=0, requireAboveMa20=False)
                        self.assertTrue(row_for(run([stock], cfg))["poolEvidence"]["structure"]["inPool"])

    def test_real_small_price_changes_survive_ma_roundoff_tolerance(self):
        for mode in ("count", "consecutive"):
            for direction in (1, -1):
                with self.subTest(mode=mode, direction=direction):
                    stock = security(closes=[10.01 + direction * index * 1e-8 for index in range(600)])
                    cfg = config()
                    cfg["poolSettings"]["structure"].update(
                        maDaysMode=mode, minMa10Above20Days=20, minMa5Above10Days=20,
                        requireAboveMa20=True, maxPriceVsMa20=0)
                    row = row_for(run([stock], cfg))
                    expected = 20 if direction > 0 else 0
                    self.assertEqual(check(row, "structure", "ma10Above20Days")["actual"], expected)
                    self.assertEqual(check(row, "structure", "ma5Above10Days")["actual"], expected)
                    self.assertEqual(check(row, "structure", "priceAboveMa20")["passed"], direction > 0)
                    self.assertGreater(check(row, "structure", "absPriceVsMa20")["actual"], 0)
                    self.assertFalse(check(row, "structure", "absPriceVsMa20")["passed"])
                    cfg["poolSettings"]["structure"]["maxPriceVsMa20"] = 1e-7
                    screened = row_for(run([stock], cfg))
                    self.assertEqual(screened["poolEvidence"]["structure"]["inPool"], direction > 0)
                    day = stock["bars"][-1]["date"]
                    backtest = DailyScorer({"securities": [stock]}, cfg).daily(day)
                    self.assertEqual(backtest["pools"]["structure"], [stock["symbol"]] if direction > 0 else [])

    def test_mean_amplitude_uses_each_days_close_and_is_a_base_filter(self):
        stock = security()
        for bar in stock["bars"][-20:]:
            bar.update(high=11, low=9)
        cfg = config()
        cfg["universe"]["minAmplitude20"] = .21
        row = row_for(run([stock], cfg))
        self.assertAlmostEqual(check(row, "base", "amplitude20")["actual"], .2)
        self.assertEqual(row["reason"], "amplitude20BelowMinimum")
        self.assertFalse(row["poolEvidence"]["base"]["inPool"])
        stock["bars"] = stock["bars"][-19:]
        missing = row_for(run([stock], cfg))
        self.assertEqual(missing["poolEvidence"]["base"]["status"], "unknown")
        self.assertIsNone(missing["poolEvidence"]["base"]["passed"])
        self.assertIsNone(check(missing, "base", "amplitude20")["actual"])
        self.assertFalse(missing["eligible"])
        self.assertFalse(missing["poolEvidence"]["base"]["inPool"])

    def test_volume_endpoint_change_up_down_means_and_down_to_overall_mean(self):
        stock = security(closes=[10. if index % 2 == 0 else 11. for index in range(60)],
                         volumes=[100 if index % 2 == 0 else 200 for index in range(60)])
        cfg = config()
        cfg["poolSettings"]["volume"].update(minVolumeChange20=.5, minUpDownVolumeRatio=1.5, maxDownVolumeRatio=.8)
        row = row_for(run([stock], cfg))
        self.assertEqual(check(row, "volume", "volumeChange20")["actual"], 1)
        self.assertEqual(check(row, "volume", "upDownVolumeRatio")["actual"], 2)
        self.assertAlmostEqual(check(row, "volume", "downVolumeRatio")["actual"], 2 / 3)
        self.assertTrue(row["poolEvidence"]["volume"]["inPool"])
        for key, threshold in (("minVolumeChange20", 1.1), ("minUpDownVolumeRatio", 2.1), ("maxDownVolumeRatio", .5)):
            changed = deepcopy(cfg)
            changed["poolSettings"]["volume"][key] = threshold
            with self.subTest(key=key):
                self.assertFalse(row_for(run([stock], changed))["poolEvidence"]["volume"]["inPool"])

    def test_no_down_day_and_zero_denominators_stay_unknown_without_placeholders(self):
        cfg = config()
        cfg["poolSettings"]["volume"].update(minVolumeChange20=0, minUpDownVolumeRatio=1, maxDownVolumeRatio=1)
        stock = security(closes=[10 + index * .2 for index in range(60)])
        stock["bars"][-20]["volume"] = 0
        row = row_for(run([stock], cfg))
        for key in ("volumeChange20", "upDownVolumeRatio", "downVolumeRatio"):
            item = check(row, "volume", key)
            self.assertIsNone(item["actual"])
            self.assertEqual(item["status"], "unknown")
            self.assertTrue(item["note"])
        self.assertFalse(row["poolEvidence"]["volume"]["inPool"])

    def test_blowoff_uses_full_window_mean_strict_2point5_and_nonpositive_body(self):
        cfg = config()
        cfg["poolSettings"]["volume"]["excludeBlowoff"] = True
        stock = security(volumes=[100] * 40 + [130] + [90] * 18 + [250])
        boundary = row_for(run([stock], cfg))
        self.assertFalse(check(boundary, "volume", "blowoffRisk")["actual"])
        stock["bars"][-1]["volume"] = 251
        risk = row_for(run([stock], cfg))
        self.assertTrue(check(risk, "volume", "blowoffRisk")["actual"])
        self.assertFalse(risk["poolEvidence"]["volume"]["inPool"])
        stock["bars"][-1]["open"] = 9.99
        self.assertFalse(check(row_for(run([stock], cfg)), "volume", "blowoffRisk")["actual"])

    def test_divergence_uses_nonoverlapping_five_day_means_and_strict_boundaries(self):
        cfg = config()
        cfg["poolSettings"]["volume"]["excludeDivergence"] = True
        stock = security(closes=[10.] * 55 + [11.] * 5, volumes=[100] * 55 + [90] * 5)
        self.assertFalse(check(row_for(run([stock], cfg)), "volume", "divergenceRisk")["actual"])
        for bar in stock["bars"][-5:]:
            bar["volume"] = 89
        self.assertTrue(check(row_for(run([stock], cfg)), "volume", "divergenceRisk")["actual"])
        for bar in stock["bars"][-5:]:
            bar.update(open=10, high=10.1, low=9.9, close=10)
        self.assertFalse(check(row_for(run([stock], cfg)), "volume", "divergenceRisk")["actual"])

    def test_upper_shadow_uses_strict_half_range_and_requires_nonpositive_body(self):
        cfg = config()
        cfg["poolSettings"]["volume"]["excludeUpperShadow"] = True
        stock = security()
        stock["bars"][-1].update(high=11, low=9)
        self.assertFalse(check(row_for(run([stock], cfg)), "volume", "upperShadowRisk")["actual"])
        stock["bars"][-1]["high"] = 11.1
        self.assertTrue(check(row_for(run([stock], cfg)), "volume", "upperShadowRisk")["actual"])
        stock["bars"][-1]["open"] = 9.99
        self.assertFalse(check(row_for(run([stock], cfg)), "volume", "upperShadowRisk")["actual"])

    def test_historical_cutoff_and_reference_adjusted_prices_apply_to_new_features(self):
        stock = security()
        for bar in stock["bars"][50:]:
            for key in ("open", "high", "low", "close"):
                bar[key] /= 2
        stock["precloseByDate"] = {stock["bars"][50]["date"]: 5}
        cfg = config()
        cfg["poolSettings"]["trend"].update(rankEnabled=True, rankWindow=40)
        cfg["poolSettings"]["structure"].update(minMa10Above20Days=0, maxPriceVsMa20=.01)
        row = row_for(run([stock], cfg))
        self.assertEqual(row["rankReturnPct"], 0)
        self.assertEqual(check(row, "structure", "absPriceVsMa20")["actual"], 0)
        cutoff = stock["bars"][-2]["date"]
        cfg["endDate"] = cutoff
        full = run([stock], cfg)
        stock["bars"].pop()
        self.assertEqual(run([stock], cfg)["screenPools"], full["screenPools"])


if __name__ == "__main__":
    unittest.main()
