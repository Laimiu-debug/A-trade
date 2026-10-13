"""Full saved-pool set operations, distinct from account or strategy execution."""
from copy import deepcopy
import unittest

from service.portfolio_screen import PortfolioScreen, paginate_screen
from test_portfolio_service import dataset_fixture, CONFIG
from test_portfolio_combined import fixture as combined_fixture, settings as combined_settings
from service.portfolio_signals import DailyScorer


def report_fixture():
    rows = [{"symbol": f"sh{600000 + index}", "name": f"样本{index:03d}", "amount": index * 1000,
             "eligible": True, "reason": "eligible", "matchedStrategies": []} for index in range(120)]
    excluded = [{"symbol": "sh699998", "name": "停牌样本", "eligible": False, "reason": "suspendedOrZeroVolume"},
                {"symbol": "sh699999", "name": "资格样本", "eligible": False, "reason": "strictHistoryMissing"}]
    pools = {"base": rows, "trend": rows[:90], "structure": rows[30:], "volume": rows[::2], "excluded": excluded}
    return {"type": "poolPreview", "screenPools": pools, "poolCounts": {key: len(value) for key, value in pools.items()},
            "coverage": {"totalSecurities": 122, "eligibleSecurities": 120}, "protocol": {"ordersGenerated": False}}


class PortfolioScreenCombinationTests(unittest.TestCase):
    def test_intersection_applies_to_full_pools_before_search_and_pagination(self):
        report = report_fixture()
        first = paginate_screen(report, {"pools": ["trend", "volume"], "poolPageSize": 20})
        self.assertEqual(first["poolSelection"]["matchedCount"], 45)
        self.assertEqual(first["poolPagination"]["combined"]["total"], 45)
        self.assertEqual(len(first["screenPools"]["combined"]), 20)
        self.assertNotIn("sh600004", [row["symbol"] for row in first["screenPools"]["combined"]])
        second = paginate_screen(report, {"pools": ["trend", "volume"], "poolOffset": 20, "poolPageSize": 20})
        self.assertFalse(set(row["symbol"] for row in first["screenPools"]["combined"]) & set(row["symbol"] for row in second["screenPools"]["combined"]))
        found = paginate_screen(report, {"pools": ["trend", "volume"], "poolQuery": "样本004"})
        self.assertEqual(found["poolSelection"]["matchedCount"], 45)
        self.assertEqual(found["poolSelection"]["filteredCount"], 1)
        self.assertEqual(found["poolPagination"]["combined"]["unfilteredTotal"], 45)
        self.assertEqual(found["screenPools"]["combined"][0]["symbol"], "sh600004")
        self.assertEqual(found["screenPools"]["combined"][0]["memberships"], ["base", "trend", "volume"])
        self.assertTrue(found["screenPools"]["combined"][0]["poolMatch"]["matched"])

    def test_active_independent_pool_search_and_paging_preserve_full_combination_and_funnel(self):
        report = report_fixture()
        for pool in ("base", "trend", "excluded"):
            with self.subTest(pool=pool):
                target = report["screenPools"][pool][-1]
                found = paginate_screen(report, {"pools": ["trend", "volume"], "pool": pool,
                                                 "poolQuery": target["symbol"], "poolPageSize": 20})
                self.assertEqual(found["poolPagination"][pool]["total"], 1)
                self.assertEqual(found["screenPools"][pool][0]["symbol"], target["symbol"])
                self.assertEqual(found["poolSelection"]["matchedCount"], 45)
                self.assertEqual(found["poolPagination"]["combined"]["total"], 45)
                self.assertEqual(found["poolSelection"]["funnelStages"][-1]["after"], 30)
                paged = paginate_screen(report, {"pools": ["trend", "volume"], "pool": pool,
                                                 "poolOffset": 1, "poolPageSize": 1})
                expected_second = {"base": "sh600118", "trend": "sh600088", "excluded": "sh699999"}[pool]
                self.assertEqual(paged["screenPools"][pool][0]["symbol"], expected_second)
                self.assertEqual(paged["poolPagination"]["combined"]["offset"], 0)

    def test_saved_and_query_combinations_match_actual_daily_scorer_for_both_modes(self):
        data, day, groups = combined_fixture(34)
        for mode, expected in (("intersection", groups[0]), ("union", groups[0] + groups[1] + groups[2])):
            with self.subTest(mode=mode):
                config = combined_settings(["trend", "volume"], mode)
                screen = PortfolioScreen(data, config)
                screen.step()
                report = screen.result()
                queried = paginate_screen(report, {"pools": ["trend", "volume"], "poolMode": mode, "poolPageSize": 200})
                actual = set(DailyScorer(data, config).daily(day)["pools"]["combined"])
                self.assertEqual(actual, set(expected))
                self.assertEqual({row["symbol"] for row in report["screenPools"]["combined"]}, actual)
                self.assertEqual({row["symbol"] for row in queried["screenPools"]["combined"]}, actual)

    def test_union_deduplicates_and_selected_base_has_documented_set_semantics(self):
        report = report_fixture()
        union = paginate_screen(report, {"pools": ["trend", "structure", "trend"], "poolMode": "union", "poolPageSize": 200})
        self.assertEqual(union["poolSelection"]["pools"], ["trend", "structure"])
        self.assertEqual(union["poolSelection"]["matchedCount"], 120)
        self.assertEqual(len({row["symbol"] for row in union["screenPools"]["combined"]}), 120)
        only_trend = next(row for row in union["screenPools"]["combined"] if row["symbol"] == "sh600000")
        self.assertTrue(only_trend["poolMatch"]["matched"])
        self.assertEqual(only_trend["poolMatch"]["missingPools"], ["structure"])
        self.assertEqual(paginate_screen(report, {"pools": ["base", "trend"]})["poolSelection"]["matchedCount"], 90)
        self.assertEqual(paginate_screen(report, {"pools": ["base", "trend"], "poolMode": "union"})["poolSelection"]["matchedCount"], 120)
        self.assertEqual(paginate_screen(report, {"pools": []})["poolSelection"]["pools"], ["base"])

    def test_funnel_is_a_separate_fixed_cumulative_intersection_not_parallel_pool_counts(self):
        result = paginate_screen(report_fixture(), {"pools": ["trend", "volume"], "poolMode": "union", "poolPageSize": 200})
        self.assertEqual(result["poolSelection"]["matchedCount"], 105)
        self.assertEqual(result["poolSelection"]["rejectedCount"], 15)
        self.assertEqual(result["poolSelection"]["missingCounts"], {"trend": 30, "volume": 60})
        self.assertTrue(result["poolSelection"]["missingCountsOverlap"])
        self.assertEqual([(row["pool"], row["before"], row["after"], row["removed"])
                          for row in result["poolSelection"]["funnelStages"]],
                         [("base", 122, 120, 2), ("trend", 120, 90, 30), ("structure", 90, 60, 30), ("volume", 60, 30, 30)])
        failed = next(row for row in result["screenPools"]["base"] if row["symbol"] == "sh600091")
        self.assertFalse(failed["poolMatch"]["matched"])
        self.assertEqual(failed["poolMatch"]["missingPools"], ["trend", "volume"])
        self.assertEqual(failed["reason"], "eligible", "pool membership must not invent a strategy failure")

    def test_missing_saved_pool_is_unknown_and_not_a_false_failure(self):
        report = report_fixture()
        report["screenPools"].pop("structure")
        result = paginate_screen(report, {"pools": ["trend", "structure"], "poolPageSize": 200})
        self.assertEqual(result["poolSelection"]["matchedCount"], 0)
        self.assertEqual(result["poolSelection"]["unknownCount"], 90)
        self.assertEqual(result["poolSelection"]["rejectedCount"], 30)
        self.assertEqual(result["poolSelection"]["missingCounts"]["structure"], None)
        self.assertEqual(result["poolSelection"]["unavailablePools"], ["structure"])
        row = next(row for row in result["screenPools"]["base"] if row["symbol"] == "sh600004")
        self.assertIsNone(row["membershipStatus"]["structure"])
        self.assertIsNone(row["poolMatch"]["matched"])
        self.assertEqual(row["poolMatch"]["missingPools"], [])
        self.assertEqual(row["poolMatch"]["unknownPools"], ["structure"])
        self.assertIsNone(result["poolSelection"]["funnelStages"][2]["after"])
        self.assertEqual(paginate_screen(report, {"pools": ["trend", "structure"], "poolMode": "union"})["poolSelection"]["matchedCount"], 90)

    def test_legacy_single_pool_and_frozen_report_remain_unchanged(self):
        report = report_fixture()
        before = deepcopy(report)
        result = paginate_screen(report, {"pool": "structure", "poolOffset": 20, "poolPageSize": 10})
        self.assertNotIn("poolSelection", result)
        self.assertNotIn("combined", result["screenPools"])
        self.assertEqual(result["screenPools"]["structure"][0]["symbol"], "sh600099")
        self.assertEqual(result["poolPagination"]["structure"]["total"], 90)
        self.assertEqual(result["poolPagination"]["base"]["offset"], 0)
        self.assertEqual(paginate_screen(report, {"pool": "excluded"})["screenPools"]["excluded"][0]["reason"], "suspendedOrZeroVolume")
        self.assertEqual(report, before)
        self.assertNotIn("matchedStrategies", result["screenPools"]["structure"][0])
        result["screenPools"]["structure"][0]["memberships"].append("not-saved")
        self.assertEqual(report, before)

    def test_saved_combined_is_not_an_independent_membership_and_query_can_override(self):
        report = report_fixture()
        report["screenPools"]["combined"] = report["screenPools"]["base"][:5]
        report["poolCounts"]["combined"] = 5
        report["config"] = {"poolSettings": {"combined": {"pools": ["trend", "structure", "volume"], "mode": "intersection"}}}
        legacy = paginate_screen(report, {"pool": "combined"})
        self.assertEqual(legacy["poolPagination"]["combined"]["total"], 5)
        self.assertNotIn("combined", legacy["screenPools"]["combined"][0]["memberships"])
        selected = paginate_screen(report, {"pools": ["trend", "volume"], "poolMode": "union"})
        self.assertEqual(selected["poolSelection"]["matchedCount"], 105)
        self.assertEqual(report["poolCounts"]["combined"], 5)
        self.assertEqual(len(report["screenPools"]["combined"]), 5)

    def test_amount20_is_unscaled_mean_including_cutoff_and_never_future_bars(self):
        data = dataset_fixture(symbol_count=2, bar_count=85)
        cutoff = data["calendar"][60]
        for security in data["securities"]:
            for index, bar in enumerate(security["bars"]):
                bar["amount"] = (index + 1) * 10000 if index <= 60 else 1e12
        data["securities"][1]["bars"] = data["securities"][1]["bars"][46:61]
        config = deepcopy(CONFIG)
        config["endDate"] = cutoff
        screen = PortfolioScreen(data, config)
        self.assertTrue(screen.step())
        report = screen.result()
        rows = report["screenPools"]["base"] + report["screenPools"]["excluded"]
        row = next(row for row in rows if row["symbol"] == "sh600000")
        self.assertEqual(report["asOf"], cutoff)
        self.assertEqual(row["amount"], 610000)
        self.assertEqual(row["amount20"], 515000)
        short = next(row for row in rows if row["symbol"] == "sh600001")
        self.assertIsNone(short["amount20"], "fewer than 20 observations is missing, not a shorter-window average")

    def test_names_recover_before_full_combination_search_and_sort_is_stable(self):
        report = report_fixture()
        report["screenPools"]["base"][4]["name"] = None
        result = paginate_screen(report, {"pools": ["trend", "volume"], "poolQuery": "冻结名称"}, {"sh600004": "冻结名称"})
        self.assertEqual(result["poolPagination"]["combined"]["total"], 1)
        self.assertEqual(result["screenPools"]["combined"][0]["name"], "冻结名称")
        first = paginate_screen(report, {"pools": ["trend", "structure"], "poolMode": "union"})
        reversed_selection = paginate_screen(report, {"pools": ["structure", "trend"], "poolMode": "union"})
        self.assertEqual(first["screenPools"]["combined"], reversed_selection["screenPools"]["combined"])

    def test_invalid_combination_or_page_is_rejected(self):
        for params in ({"pools": "trend"}, {"pools": ["excluded"]}, {"pools": [None]},
                       {"pools": ["trend"], "poolMode": "serial"}, {"pools": ["trend"], "poolOffset": True},
                       {"pools": ["trend"], "poolPageSize": 201}, {"pool": []}, {"poolQuery": "x" * 101}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                paginate_screen(report_fixture(), params)


if __name__ == "__main__":
    unittest.main()
