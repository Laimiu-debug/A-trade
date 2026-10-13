"""Adversarial execution, timing and plateau regressions from the code audit."""
from copy import deepcopy
import datetime as dt
import math
import unittest
from unittest.mock import patch

from service.backtest_engine import BacktestEngine
from service.plateau import normalize_scan, score_plateaus
from service.portfolio_engine import PortfolioEngine
from service.portfolio_experiment import PortfolioExperiment, neighborhood_statistics, normalize_experiment
from service.portfolio_history import normalize_package
from service.portfolio_actions import normalize_actions
from service.portfolio_study import _quality_failures
from service.research_pipeline import SinglePlateau
from service.strategies import normalize_config as legacy_config
from test_portfolio_engine import dataset, config, FixtureScorer, SYMBOLS
from test_portfolio_experiment import fixture, four_axes, FakeEngine
from test_portfolio_refinements import package, action


def finish(runner):
    for _ in range(20000):
        if runner.step(1000):
            return runner.result()
    raise AssertionError("runner did not finish")


class CertifiedScorer(FixtureScorer):
    def daily(self, day):
        result = super().daily(day)
        result["coverage"]["pointInTimeComplete"] = True
        return result


@patch("service.portfolio_engine.DailyScorer", CertifiedScorer)
class OpeningCapacityTests(unittest.TestCase):
    def test_impossible_daily_volume_invalidates_path_without_looking_ahead_to_resize(self):
        data = dataset()
        original = finish(PortfolioEngine(data, config(data)))
        data["securities"][0]["bars"][6].update(volume=100, amount=1000)
        result = finish(PortfolioEngine(data, config(data)))
        self.assertEqual(result["trades"][0]["quantity"], original["trades"][0]["quantity"])
        self.assertEqual(result["trades"][0]["quantity"], 1000)
        self.assertFalse(result["trades"][0]["executionValid"])
        self.assertIn("invalid_execution_capacity", _quality_failures(result, data["fingerprint"]))
        self.assertFalse(result["quality"]["certified"])
        self.assertFalse(result["curve"][-1]["valuationReliable"])

    def test_known_opening_volume_bounds_fills_independently_of_later_volume_and_close(self):
        data = dataset()
        day = data["calendar"][6]
        data["securities"][0]["openingVolumeByDate"] = {day: 2000}
        settings = config(data, endDate=day)
        expected = finish(PortfolioEngine(data, settings))["trades"][0]
        changed = deepcopy(data)
        changed["securities"][0]["bars"][6].update(close=10.8, high=10.8, volume=30000000)
        actual = finish(PortfolioEngine(changed, settings))["trades"][0]
        self.assertEqual(actual, expected)
        self.assertEqual(actual["quantity"], 200)
        self.assertTrue(actual["openingCapacityVerified"])

    def test_daily_only_package_cannot_certify_execution_but_opening_evidence_can(self):
        data = dataset()
        raw = package(data)
        for security in raw["dataset"]["securities"]:
            security.pop("openingVolumeByDate")
        imported = normalize_package(raw)
        approximate = finish(PortfolioEngine(imported, config(imported)))
        self.assertTrue(imported["dataQuality"]["certified"])
        self.assertFalse(approximate["quality"]["certified"])
        self.assertFalse(approximate["quality"]["openingCapacityVerified"])
        self.assertTrue(approximate["quality"]["unverifiedOpeningFills"])
        evidenced = normalize_package(package(data))
        result = finish(PortfolioEngine(evidenced, config(evidenced)))
        self.assertTrue(result["quality"]["certified"], result["quality"])

    def test_contradictory_opening_evidence_is_an_execution_failure(self):
        data = dataset()
        data["securities"][0]["openingVolumeByDate"] = {data["calendar"][6]: 2000000}
        result = finish(PortfolioEngine(data, config(data)))
        self.assertTrue(result["quality"]["executionCapacityIssues"])
        self.assertFalse(result["trades"][0]["executionValid"])

    def test_invalid_execution_audit_survives_restart_exactly(self):
        data = dataset()
        data["securities"][0]["bars"][6]["volume"] = 100
        engine = PortfolioEngine(data, config(data))
        engine.step(2)
        restored = PortfolioEngine(data, config(data), engine.checkpoint())
        self.assertEqual(finish(engine), finish(restored))


@patch("service.portfolio_engine.DailyScorer", FixtureScorer)
class CashTimingTests(unittest.TestCase):
    def ledger(self, stamp=None):
        data = dataset(length=14)
        days = data["calendar"]
        row = action(data, cashNetPerShare=1, cashPayDate=days[8])
        if stamp is not None:
            row["cashAvailableAt"] = days[8] + stamp
        security = data["securities"][0]
        security.update(corporateActions=[row], corporateActionsComplete=True)
        for index, bar in enumerate(security["bars"]):
            if index >= 8:
                bar.update(open=9, close=9, high=9, low=9)
                security["precloseByDate"][bar["date"]] = 9
        settings = config(data, rebalanceMode="risk", maxPositions=2)
        engine = PortfolioEngine(data, settings)
        engine.step(3)  # Through registration close; all cash is invested.
        engine.state["pendingTarget"] = {"signalDate": days[7], "signalEquity": 10000,
            "weights": {SYMBOLS[0]: 1, SYMBOLS[1]: .1}, "quantities": {SYMBOLS[0]: 1000},
            "budgets": {SYMBOLS[0]: 10000, SYMBOLS[1]: 1000}, "signalDates": {},
            "priorityBySymbol": {symbol: {"budgetWeight": weight, "weightedScore": 1} for symbol, weight in ((SYMBOLS[0], 1), (SYMBOLS[1], .1))},
            "reason": "targetRebalance"}
        return data, settings, engine

    def test_date_only_cash_cannot_buy_at_payday_open_and_releases_after_weekend(self):
        data, settings, engine = self.ledger()
        self.assertEqual(dt.date.fromisoformat(data["calendar"][8]).weekday(), 4)
        engine.step(1)
        self.assertEqual(engine.state["cashReceivables"][0]["amount"], 1000)
        self.assertFalse(any(fill["symbol"] == SYMBOLS[1] for fill in engine.state["trades"]))
        self.assertEqual(engine.state["curve"][-1]["equity"], 10000)
        resumed = PortfolioEngine(data, settings, engine.checkpoint())
        resumed.step(1)
        fill = next(fill for fill in resumed.state["trades"] if fill["symbol"] == SYMBOLS[1])
        self.assertEqual(fill["date"], data["calendar"][9])
        self.assertEqual(finish(resumed), finish(engine))

    def test_source_timestamp_distinguishes_before_and_after_open(self):
        for stamp, can_buy in (("T08:00:00+08:00", True), ("T12:00:00+08:00", False)):
            with self.subTest(stamp=stamp):
                data, _, engine = self.ledger(stamp)
                engine.step(1)
                self.assertEqual(any(fill["symbol"] == SYMBOLS[1] and fill["date"] == data["calendar"][8]
                                     for fill in engine.state["trades"]), can_buy)

    def test_cash_timestamp_requires_timezone_and_matching_payment_date(self):
        data = dataset()
        for stamp in (data["calendar"][10] + "T08:00:00", data["calendar"][9] + "T08:00:00+08:00"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                normalize_actions([action(data, cashAvailableAt=stamp)])


class StabilityTests(unittest.TestCase):
    def test_small_absolute_dispersion_cannot_hide_a_large_relative_peak(self):
        settings = {"minNeighborhoodSize": 3, "minNeighborPositiveFraction": .7, "maxNeighborhoodStdPct": 10}
        spike = neighborhood_statistics([1, 20, 1], settings)
        self.assertLess(spike["stdNetReturnPct"], 10)
        self.assertFalse(spike["passed"])
        self.assertIn("neighborhood_relative_dispersion", spike["failures"])
        self.assertIn("neighborhood_peak_not_retained", spike["failures"])
        self.assertTrue(neighborhood_statistics([2, 3, 4], settings)["passed"])

    @patch("service.portfolio_engine.DailyScorer", FixtureScorer)
    def test_identical_real_trade_paths_do_not_establish_top_n_width(self):
        data = dataset(prices=[[10 * 1.01**i for i in range(24)]], symbols=SYMBOLS[:1])
        days = data["calendar"]
        data["scoreSchedule"] = {}
        for index in (5, 8, 11):
            data["scoreSchedule"][days[index]] = {"a": [(SYMBOLS[0], 1)]}
            data["scoreSchedule"][days[index + 1]] = {"a": []}
        result = finish(SinglePlateau(data, config(data), {"axes": [{"path": "topN", "values": [1, 2, 3]}]}))
        self.assertTrue(all(row["metrics"]["closedTradeCount"] == 3 for row in result["candidates"]))
        self.assertEqual(result["parameterIdentifiability"]["distinctBehaviorCount"], 1)
        self.assertEqual(result["parameterIdentifiability"]["inactiveAxes"], ["topN"])
        self.assertFalse(any(row["neighborhood"]["passed"] for row in result["candidates"]))
        self.assertEqual(result["regions"], [])

    @patch("service.portfolio_engine.DailyScorer", FixtureScorer)
    def test_inactive_axis_is_not_hidden_by_another_active_axis(self):
        data = dataset(prices=[[10 * 1.01**i for i in range(40)]], symbols=SYMBOLS[:1])
        result = finish(SinglePlateau(data, config(data, rebalanceMode="risk"), {"axes": [
            {"path": "topN", "values": [1, 2]}, {"path": "maxHoldingDays", "values": [2, 3, 4]}]}))
        self.assertGreater(result["parameterIdentifiability"]["distinctBehaviorCount"], 1)
        self.assertIn("topN", result["parameterIdentifiability"]["inactiveAxes"])
        self.assertEqual(result["regions"], [])

    def test_legacy_l_shape_is_not_projected_to_a_full_rectangle(self):
        axes = [{"path": "stopLossPct", "values": [.05, .08]}, {"path": "maxHoldingDays", "values": [10, 20]}]
        scan = normalize_scan({"axes": axes}, legacy_config({}))
        rows = []
        for index, coordinates in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
            good = coordinates != (1, 1)
            rows.append({"id": str(index), "coordinates": list(coordinates), "config": {},
                "params": {axis["path"]: axis["values"][coordinates[i]] for i, axis in enumerate(axes)},
                "blocks": [{"score": 10 if good else -1, "passes": good, "failures": []} for _ in range(3)]})
        regions, _, _ = score_plateaus(rows, axes, scan)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["parameterRanges"], {})
        self.assertFalse(regions[0]["intervalEligible"])
        self.assertEqual(regions[0]["intervalCoverageFailure"], "projected_intervals_contain_unverified_combinations")


class SystemCertificationTests(unittest.TestCase):
    def setUp(self):
        fields = ("certified", "bad_quality", "bad_oos_baseline", "losing_rotation", "missing_regime_candidate")
        previous = {key: getattr(FakeEngine, key) for key in fields}
        self.addCleanup(lambda: [setattr(FakeEngine, key, value) for key, value in previous.items()])
        for key in fields:
            setattr(FakeEngine, key, key == "certified")

    def short_cross_year(self):
        data, cfg, exp = fixture(105)
        days, day = [], dt.date(2023, 9, 11)
        while len(days) < 105:
            if day.weekday() < 5:
                days.append(day.isoformat())
            day += dt.timedelta(days=1)
        data["calendar"] = days
        for bar, day in zip(data["securities"][0]["bars"], days):
            bar["date"] = day
        cfg.update(startDate=days[0], endDate=days[-1])
        exp.update(kind="systemPlateau", axes=four_axes(), trainDays=60, testDays=45, stepDays=45, maxFolds=1)
        return data, cfg, exp

    @patch("service.portfolio_experiment.PortfolioEngine", FakeEngine)
    def test_45_sessions_across_new_year_are_not_two_years_of_validation(self):
        data, cfg, exp = self.short_cross_year()
        result = finish(PortfolioExperiment(data, cfg, exp))
        self.assertEqual(result["plateau"]["oosCoverage"]["tradingDays"], 45)
        self.assertEqual(len(result["plateau"]["yearValidation"]), 2)
        self.assertIn("insufficient_oos_trading_days", result["plateau"]["reasons"])
        self.assertIn("insufficient_oos_calendar_span", result["plateau"]["reasons"])
        self.assertFalse(result["plateau"]["certified"])
        self.assertEqual(result["plateau"]["recommendedRanges"], [])

    def test_risk_or_sample_failure_blocks_training_selection(self):
        data, cfg, exp = self.short_cross_year()
        for bad_metrics, reason in (({"maxDrawdownPct": 80}, "drawdown_limit_exceeded_or_missing"),
                                    ({"closedTradeCount": 0}, "insufficient_closed_trades"),
                                    ({"investedDays": 0}, "insufficient_invested_days")):
            class StressEngine(FakeEngine):
                def result(self):
                    result = super().result()
                    result["metrics"].update(bad_metrics)
                    return result
            with self.subTest(metrics=bad_metrics), patch("service.portfolio_experiment.PortfolioEngine", StressEngine):
                result = finish(PortfolioExperiment(data, cfg, exp))
            fold = result["folds"][0]
            self.assertEqual(fold["selection"]["status"], "invalid_training")
            self.assertEqual(fold["testing"], {})
            self.assertTrue(all(reason in row["qualityFailures"] for row in fold["training"].values()))
            self.assertFalse(result["plateau"]["certified"])

    def test_long_term_minima_cannot_be_replaced_by_a_short_cross_year_window(self):
        _, cfg, exp = self.short_cross_year()
        for change in ({"minOosTradingDays": 45}, {"minOosCalendarDays": 60}, {"minOosDaysPerYear": 20}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_experiment({**exp, **change}, cfg)

    def test_oos_risk_failure_rejects_frozen_region_without_reselecting(self):
        data, cfg, exp = self.short_cross_year()
        with patch("service.portfolio_experiment.PortfolioEngine", FakeEngine):
            reference = finish(PortfolioExperiment(data, cfg, exp))
        class BadHoldoutEngine(FakeEngine):
            def result(self):
                result = super().result()
                if len(self.days) == 45:
                    result["metrics"]["maxDrawdownPct"] = 80
                return result
        with patch("service.portfolio_experiment.PortfolioEngine", BadHoldoutEngine):
            adverse = finish(PortfolioExperiment(data, cfg, exp))
        self.assertEqual(reference["folds"][0]["selection"], adverse["folds"][0]["selection"])
        self.assertIn("drawdown_limit_exceeded_or_missing", adverse["plateau"]["reasons"])
        self.assertEqual(len(adverse["folds"][0]["oosCandidates"]), 16)
        self.assertEqual(adverse["plateau"]["recommendedRanges"], [])


class FutureIsolationTests(unittest.TestCase):
    def test_legacy_engine_rejects_invalid_capacity_evidence_from_plateau_gates(self):
        from service.plateau import _metrics_gate
        data = dataset(prices=[[10 * 1.01**i for i in range(80)]], symbols=SYMBOLS[:1])
        days = data["calendar"]
        cfg = {"startDate": days[60], "endDate": days[61], "positionFraction": 1,
               "strategyIds": ["momentum"], "strategyParams": {"momentum": {"lookback": 5, "trendWindow": 10}}}
        expected = finish(BacktestEngine(data, cfg))
        self.assertTrue(expected["trades"])
        data["securities"][0]["bars"][61]["volume"] = 20
        adverse = finish(BacktestEngine(data, cfg))
        self.assertEqual(expected["trades"][0]["quantity"], adverse["trades"][0]["quantity"])
        self.assertFalse(adverse["trades"][0]["executionValid"])
        scan = normalize_scan(None, legacy_config(cfg))
        _, failures, _ = _metrics_gate(adverse["metrics"], scan, adverse["quality"])
        self.assertIn("dataQuality:invalid_execution_capacity", failures)

    def test_real_signals_fills_and_account_prefix_survive_future_market_changes(self):
        prices = [[10 + .025 * i + .6 * math.sin(i / 5 + j) for i in range(180)] for j in range(3)]
        data = dataset(prices=prices, symbols=SYMBOLS[:3])
        days, changed = data["calendar"], deepcopy(data)
        cutoff = days[120]
        for security in changed["securities"]:
            for bar in security["bars"]:
                if bar["date"] > cutoff:
                    for key in ("open", "high", "low", "close"):
                        bar[key] *= 2
                    bar["volume"] *= 7
                    security["precloseByDate"][bar["date"]] *= 2
                    security["stByDate"][bar["date"]] = True
        settings = config(data, startDate=days[60], endDate=days[160], topN=2, maxPositions=3,
            maxStockWeight=.5, rebalanceMode="hybrid", stopLossPct=.04, takeProfitPct=.06, maxHoldingDays=8,
            strategies=[{"id": "a", "kind": "momentum", "params": {"lookback": 5, "trendWindow": 10, "minReturn": 0}},
                        {"id": "b", "kind": "forceGoldenCross"}], allocation={"mode": "equal", "strategyIds": ["a", "b"]})
        for cls, cfg in ((PortfolioEngine, settings), (BacktestEngine, {"startDate": days[60], "endDate": days[160],
                "strategyIds": ["momentum"], "strategyParams": {"momentum": {"lookback": 5, "trendWindow": 10}}, "maxHoldingDays": 8})):
            with self.subTest(engine=cls.__name__):
                expected, actual = finish(cls(data, cfg)), finish(cls(changed, cfg))
                for key in ("curve", "trades", "decisions"):
                    self.assertEqual([row for row in expected[key] if row["date"] <= cutoff],
                                     [row for row in actual[key] if row["date"] <= cutoff])
        single = {**settings, "endDate": cutoff, "strategies": [settings["strategies"][0]],
                  "allocation": {"mode": "single", "strategyIds": ["a"]}}
        scan = {"axes": [{"path": "maxHoldingDays", "values": [5, 8, 12]}]}
        self.assertEqual(finish(SinglePlateau(data, single, scan)), finish(SinglePlateau(changed, single, scan)))


if __name__ == "__main__":
    unittest.main()
