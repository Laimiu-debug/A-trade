"""Pool screening uses price conditions, never strategy selection or scores."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from service.portfolio_screen import PortfolioScreen, paginate_screen
from service.portfolio_signals import DailyScorer
from test_portfolio_combined import fixture, settings


def screen(data, config):
    task = PortfolioScreen(data, config)
    task.step()
    return task.result()


def condition(row, pool, key):
    return next(item for item in row["poolEvidence"][pool]["conditions"] if item["key"] == key)


class PoolEvidenceTests(unittest.TestCase):
    def test_screen_never_calls_strategy_force_or_regime_and_ignores_strategy_parameters(self):
        data, _, _ = fixture()
        cfg = settings(["trend", "volume"])
        changed = deepcopy(cfg)
        changed.update(strategies=[{"kind": "not-a-strategy", "params": {"invalid": "ignored"}}],
                       combinations="ignored", allocation=None, initialCapital=-1)
        with patch.object(DailyScorer, "daily", side_effect=AssertionError("strategy daily called")), \
             patch.object(DailyScorer, "_signal", side_effect=AssertionError("strategy signal called")), \
             patch.object(DailyScorer, "_regime", side_effect=AssertionError("market regime called")), \
             patch("service.portfolio_signals._compact_force", side_effect=AssertionError("force strategy prepared")):
            first, second = screen(data, cfg), screen(data, changed)
        self.assertEqual(first, second)
        self.assertFalse(first["protocol"]["strategyScoring"])
        self.assertEqual(first["protocol"]["sortOrder"], "amount_desc_symbol_asc")
        for values in first["screenPools"].values():
            for row in values:
                self.assertNotIn("matchedStrategies", row)
                self.assertNotIn("score", row)

    def test_actual_values_thresholds_and_statuses_drive_memberships(self):
        data, day, groups = fixture()
        cfg = settings(["trend", "structure", "volume"])
        report = screen(data, cfg)
        scored = DailyScorer(data, cfg).daily(day)
        rows = {row["symbol"]: row for row in report["screenPools"]["base"]}
        row = rows[groups[0][0]]
        self.assertAlmostEqual(condition(row, "trend", "closeAboveAverage")["actual"], 14.8)
        self.assertAlmostEqual(condition(row, "trend", "closeAboveAverage")["threshold"], 14.4)
        self.assertAlmostEqual(condition(row, "trend", "averageRising")["threshold"], 14.2)
        self.assertAlmostEqual(condition(row, "trend", "windowReturn")["actual"], 14.8 / 13.8 - 1)
        self.assertEqual(condition(row, "trend", "windowReturn")["unit"], "ratio")
        self.assertEqual(condition(row, "structure", "drawdown")["operator"], "between")
        self.assertEqual(condition(row, "structure", "drawdown")["threshold"], [.02, .25])
        self.assertEqual(condition(row, "structure", "drawdown")["actual"], 0)
        self.assertEqual(row["poolEvidence"]["structure"]["status"], "fail")
        self.assertEqual(condition(row, "volume", "volumeRatio")["actual"], 2)
        self.assertEqual(condition(row, "volume", "volumeRatio")["threshold"], 1.2)
        self.assertEqual(condition(row, "base", "amount20")["actual"], 20000000)
        self.assertEqual(condition(row, "base", "amount20")["threshold"], 10000000)
        for symbol, stock in rows.items():
            for key, detail in stock["poolEvidence"].items():
                self.assertEqual(detail["inPool"], symbol in scored["pools"][key])
                self.assertEqual(detail["inPool"], symbol in {item["symbol"] for item in report["screenPools"][key]})

    def test_missing_history_is_unknown_and_optional_history_does_not_claim_failure(self):
        data, _, _ = fixture()
        cfg = settings(["trend", "volume"])
        cfg["universe"]["minAmount20"] = 0
        data["securities"] = data["securities"][:1]
        data["securities"][0]["bars"] = data["securities"][0]["bars"][-3:]
        report = screen(data, cfg)
        row = report["screenPools"]["base"][0]
        self.assertTrue(row["poolEvidence"]["base"]["passed"])
        for key in ("historicalMember", "listingDate", "historicalSt", "tradeStatus"):
            item = condition(row, "base", key)
            self.assertIsNone(item["actual"])
            self.assertFalse(item["required"])
            self.assertIn("不因此排除", item["note"])
        for key in ("trend", "structure", "volume", "combined"):
            self.assertIsNone(row["poolEvidence"][key]["passed"])
            self.assertEqual(row["poolEvidence"][key]["status"], "unknown")
            self.assertFalse(row["poolEvidence"][key]["inPool"])
        self.assertIsNone(row["trendReturnPct"])
        queried = paginate_screen(report, {"pools": ["trend", "volume"]})
        self.assertEqual(queried["poolSelection"]["unknownCount"], 1)
        self.assertEqual(queried["poolSelection"]["rejectedCount"], 0)
        cfg["universe"]["strictHistory"] = True
        excluded = screen(data, cfg)["screenPools"]["excluded"][0]
        self.assertEqual(excluded["poolEvidence"]["base"]["status"], "unknown")
        self.assertEqual(excluded["reason"], "strictHistoryMissing")
        self.assertTrue(condition(excluded, "base", "historicalMember")["required"])

    def test_failed_base_cannot_be_rescued_by_passing_conditions_and_query_updates_combined_evidence(self):
        data, _, groups = fixture()
        cfg = settings(["trend", "structure", "volume"])
        cfg["universe"]["minAmount20"] = 30000000
        report = screen(data, cfg)
        row = next(row for row in report["screenPools"]["excluded"] if row["symbol"] == groups[0][0])
        self.assertFalse(condition(row, "base", "amount20")["passed"])
        self.assertEqual(row["reason"], "amount20BelowMinimum")
        self.assertTrue(row["poolEvidence"]["trend"]["passed"])
        self.assertFalse(row["poolEvidence"]["trend"]["inPool"])
        cfg["universe"]["minAmount20"] = 10000000
        report = screen(data, cfg)
        self.assertEqual(report["screenPools"]["combined"], [])
        queried = paginate_screen(report, {"pools": ["trend", "volume"]})
        selected = queried["screenPools"]["combined"][0]["poolEvidence"]["combined"]
        self.assertEqual(selected["pools"], ["trend", "volume"])
        self.assertTrue(selected["passed"])
        self.assertTrue(selected["inPool"])
        self.assertEqual(report["screenPools"]["base"][0]["poolEvidence"]["combined"]["pools"], ["trend", "structure", "volume"])

    def test_old_report_strategy_scores_do_not_affect_sort_and_blob_stays_unchanged(self):
        rows = [{"symbol": "sh600001", "amount": 100, "matchedStrategies": [{"id": "winning", "score": 1}]},
                {"symbol": "sh600003", "amount": 200, "matchedStrategies": [{"id": "weak", "score": 0}]},
                {"symbol": "sh600002", "amount": 200, "matchedStrategies": []}]
        report = {"screenPools": {"base": rows, "trend": rows}, "protocol": {}}
        before = deepcopy(report)
        for payload in ({"pool": "base"}, {"pools": ["trend"]}):
            result = paginate_screen(report, payload)
            key = "combined" if "pools" in payload else "base"
            self.assertEqual([row["symbol"] for row in result["screenPools"][key]], ["sh600002", "sh600003", "sh600001"])
            self.assertTrue(all("matchedStrategies" not in row and "poolEvidence" not in row for row in result["screenPools"][key]))
            self.assertFalse(result["protocol"]["strategyScoring"])
        self.assertEqual(report, before)

    def test_zero_mean_volume_fails_while_missing_volume_window_remains_unknown(self):
        data, _, groups = fixture()
        for bar in data["securities"][0]["bars"][:-1]:
            bar["volume"] = 0
        row = next(row for row in screen(data, settings(["volume"]))["screenPools"]["base"] if row["symbol"] == groups[0][0])
        self.assertEqual(condition(row, "volume", "previousMeanPositive")["actual"], 0)
        self.assertFalse(condition(row, "volume", "previousMeanPositive")["passed"])
        self.assertIsNone(condition(row, "volume", "volumeRatio")["actual"])
        self.assertEqual(row["poolEvidence"]["volume"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
