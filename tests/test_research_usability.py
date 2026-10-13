"""Full-pool browsing and durable task CRUD using isolated data."""
from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch
from service.research_service import ResearchService, _pack, _unpack
from test_portfolio_service import dataset_fixture, CONFIG


class ResearchUsabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = dataset_fixture(symbol_count=136, bar_count=85)
        for i, s in enumerate(self.data["securities"]):
            s["name"] = "隔离样本" + str(i)
        self.service = ResearchService(self.temp.name, autostart=False, universe_loader=lambda *args: deepcopy(self.data))
        self.addCleanup(self.service.close)

    def request(self, **changes):
        return {"kind": "portfolio", "name": "待运行研究", "config": deepcopy(CONFIG), **changes}

    def test_effective_settings_preview_does_not_create_or_load_a_dataset(self):
        loads = []
        self.service.universe_loader = lambda *args: loads.append(args)
        request = self.request(name="")
        request["config"]["stopLossPct"] = 0
        request["config"]["commissionRate"] = 0
        request["config"]["stampDutyRate"] = None
        preview = self.service.dispatch("research.previewSettings", request)
        self.assertEqual(loads, [])
        self.assertEqual(self.service.list()["tasks"], [])
        self.assertIn("组合回测", preview["name"])
        self.assertNotIn("momentum", preview["name"])
        self.assertEqual(preview["config"]["stopLossPct"], 0)
        self.assertEqual(preview["config"]["commissionRate"], 0)
        self.assertIsNone(preview["config"]["stampDutyRate"])
        self.assertEqual(preview["execution"]["fill"], "下一交易日开盘")
        accepted = self.service.create(request)
        self.assertEqual(preview["config"], accepted["config"])
        self.assertEqual(preview["selection"], accepted["selection"])
        self.assertEqual(preview["name"], accepted["name"])

    def test_preview_and_creation_share_validation_and_leave_no_invalid_task(self):
        invalid = self.request()
        invalid["config"]["topN"] = 1.5
        for method in ("research.previewSettings", "research.create"):
            with self.assertRaisesRegex(ValueError, "整数"):
                self.service.dispatch(method, invalid)
        self.assertEqual(self.service.list()["tasks"], [])
        invalid = self.request(kind="comparison")
        for method in ("research.previewSettings", "research.create"):
            with self.assertRaisesRegex(ValueError, "study"):
                self.service.dispatch(method, invalid)
        self.assertEqual(self.service.list()["tasks"], [])

    def test_full_pool_screen_paging_and_search_do_not_restrict_ranking_or_membership(self):
        task = self.service.create(self.request(kind="poolPreview"))
        self.service.run_next()
        report = self.service.get({"id": task["id"], "poolPageSize": 25})
        self.assertEqual(report["task"]["status"], "completed", report["task"]["error"])
        self.assertEqual(report["result"]["poolCounts"]["base"], 136)
        self.assertEqual(len(report["result"]["screenPools"]["base"]), 25)
        found = self.service.get({"id": task["id"], "poolQuery": "隔离样本135"})["result"]
        self.assertEqual(found["poolPagination"]["base"]["total"], 1)
        self.assertEqual(found["screenPools"]["base"][0]["symbol"], "sh600135")
        self.assertEqual(found["poolCounts"]["base"], 136)
        self.assertFalse(found["protocol"]["ordersGenerated"])
        changed = self.request(kind="poolPreview")
        changed["config"]["poolSettings"] = {"trend": {"window": 5, "minReturn": .5}}
        other = self.service.create(changed)
        self.service.run_next()
        self.assertEqual(self.service.get({"id": other["id"]})["result"]["poolCounts"]["trend"], 0)

    def test_pool_only_preview_create_and_run_ignore_unfinished_strategy_and_trading_inputs(self):
        from service.portfolio_screen import PortfolioScreen
        minimal = {"kind": "poolPreview", "config": {"lookbackDays": 39,
                   "universe": {"minListingDays": 0, "minAmount20": 0},
                   "poolSettings": {"trend": {"window": 5}}}}
        preview = self.service.preview_settings(minimal)
        self.assertNotIn("策略", preview["name"])
        self.assertNotIn("fill", preview["execution"])
        self.assertFalse(preview["execution"]["ordersGenerated"])
        self.assertFalse(preview["execution"]["strategyScoring"])
        changed = deepcopy(minimal)
        changed["config"].update(strategies=[], combinations=[{"incomplete": True}],
            allocation={"mode": "single", "strategyIds": []}, initialCapital=None, topN=1.5)
        second_preview = self.service.preview_settings(changed)
        self.assertEqual(second_preview["config"], preview["config"])
        self.assertEqual(PortfolioScreen(self.data, preview["config"]).signature,
                         PortfolioScreen(self.data, second_preview["config"]).signature)
        self.assertEqual(PortfolioScreen(self.data, minimal["config"]).signature,
                         PortfolioScreen(self.data, changed["config"]).signature,
                         "screen signatures cannot depend on raw strategy or capital settings")
        results = []
        with patch("service.portfolio_signals.DailyScorer.daily", side_effect=AssertionError("pool-only tasks must not score strategies")):
            for request in (minimal, changed):
                task = self.service.create(request)
                self.assertEqual(task["config"], preview["config"])
                self.service.run_next()
                actual = self.service.get({"id": task["id"], "poolPageSize": 200})
                self.assertEqual(actual["task"]["status"], "completed", actual["task"]["error"])
                results.append(actual["result"])
        self.assertEqual(results[0]["poolCounts"]["base"], 136)
        self.assertEqual(results[0]["screenPools"], results[1]["screenPools"])
        expected = [row["symbol"] for row in sorted(self.data["securities"],
                    key=lambda row: (-row["bars"][-1]["amount"], row["symbol"]))]
        self.assertEqual([row["symbol"] for row in results[0]["screenPools"]["base"]], expected)

    def test_pending_pool_task_update_ignores_unrelated_errors_but_validates_pool_rules(self):
        task = self.service.create({"kind": "poolPreview", "config": {}})
        updated = self.service.update({"id": task["id"], "kind": "poolPreview", "name": "仅按股票池条件",
                    "config": {"strategies": [], "initialCapital": None, "universe": {"minAmount20": 35000000}}})["task"]
        self.assertEqual(updated["config"]["universe"]["minAmount20"], 35000000)
        with self.assertRaises(ValueError):
            self.service.update({"id": task["id"], "kind": "poolPreview", "name": "无效池条件",
                                 "config": {"strategies": [], "poolSettings": {"trend": {"window": 1}}}})

    def test_pending_configuration_edit_is_atomic_and_finished_result_is_immutable(self):
        task = self.service.create(self.request())
        self.assertTrue(self.service.list()["tasks"][0]["configEditable"])
        self.service.control({"ids": [task["id"]], "action": "pause"})
        modified = self.request(id=task["id"], name="修改后的任务")
        modified["config"]["topN"] = 3
        self.service.update(modified)
        result = self.service.get({"id": task["id"]})
        self.assertEqual(result["task"]["status"], "paused")
        self.assertEqual(result["task"]["config"]["topN"], 3)
        self.service.control({"ids": [task["id"]], "action": "resume"})
        self.service.run_next()
        with self.service._db() as db:
            before = db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "原结果不能"):
            self.service.update(modified)
        self.service.rename({"id": task["id"], "name": "新报告名称"})
        self.assertEqual(self.service.dispatch("research.list", {"query": "新报告名称"})["tasks"][0]["id"], task["id"])
        with self.service._db() as db:
            self.assertEqual(before, db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0])
        self.assertFalse(self.service.get({"id": task["id"]})["task"]["configEditable"])

    def test_pool_combination_query_uses_complete_saved_memberships_without_rewriting_or_rescoring(self):
        task = self.service.create(self.request(kind="poolPreview"))
        self.service.run_next()
        with self.service._db() as db:
            report = _unpack(db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0])
            base = report["screenPools"]["base"]
            report["screenPools"]["trend"] = base[:100]
            report["screenPools"]["structure"] = base[50:]
            report["screenPools"]["volume"] = base[::2]
            report["poolCounts"] = {key: len(value) for key, value in report["screenPools"].items()}
            saved = _pack(report)
            db.execute("UPDATE jobs SET result=? WHERE id=?", (saved, task["id"]))
        self.service.universe_loader = lambda *args: self.fail("result selection must not reload or rescore the universe")
        selected = self.service.dispatch("research.get", {"id": task["id"], "pools": ["trend", "structure", "volume"],
                                                         "poolMode": "intersection", "poolPageSize": 7})["result"]
        self.assertEqual(selected["poolSelection"]["matchedCount"], 25)
        self.assertEqual(len(selected["screenPools"]["combined"]), 7)
        self.assertEqual(selected["poolPagination"]["combined"]["total"], 25)
        target = base[98]
        found = self.service.get({"id": task["id"], "pools": ["trend", "structure", "volume"],
                                  "poolQuery": target["symbol"], "poolPageSize": 7})["result"]
        self.assertEqual(found["poolPagination"]["combined"]["total"], 1)
        self.assertEqual(found["poolSelection"]["matchedCount"], 25)
        self.assertEqual(found["screenPools"]["combined"][0]["symbol"], target["symbol"])
        self.assertEqual(found["screenPools"]["combined"][0]["memberships"], ["base", "trend", "structure", "volume"])
        self.assertFalse(found["protocol"]["ordersGenerated"])
        self.assertEqual(found["poolCounts"]["base"], 136)
        with self.service._db() as db:
            self.assertEqual(db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0], saved)

    def test_delete_rejects_inflight_and_preserves_holdout_usage_and_shared_snapshots(self):
        task = self.service.create(self.request())
        self.service.inflight.add(task["id"])
        self.assertFalse(self.service.delete({"ids": [task["id"]]})["outcomes"][0]["ok"])
        self.service.inflight.clear()
        with self.service._db() as db:
            db.execute("INSERT INTO validation_usage VALUES (?,?,?,?,?,?)", (task["id"], "scope", "2024-01-01", "2024-02-01", "config", "data"))
        self.assertTrue(self.service.delete({"ids": [task["id"]]})["outcomes"][0]["ok"])
        self.assertEqual(self.service.list()["tasks"], [])
        with self.service._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM validation_usage").fetchone()[0], 1)

    def test_daily_operations_are_bound_to_each_day_and_date_filter_reads_beyond_first_page(self):
        task = self.service.create(self.request())
        self.service.run_next()
        data = self.service.get({"id": task["id"], "dailyPageSize": 200})["result"]
        for day in data["daily"]:
            self.assertTrue(all(row["date"] == day["date"] for row in day["fills"]+day["activities"]))
        fabricated = {"decisions": [{"date": "2024-01-02", "symbol": "sh600000"} for _ in range(230)]+[{"date": "2024-02-01", "symbol": "sh600135", "reason": "stopLoss"}], "trades": []}
        with self.service._db() as db:
            db.execute("UPDATE jobs SET result=? WHERE id=?", (_pack(fabricated), task["id"]))
        selected = self.service.get({"id": task["id"], "eventDate": "2024-02-01", "pageSize": 20})["result"]
        self.assertEqual(selected["pagination"]["decisions"]["total"], 1)
        self.assertEqual(selected["decisions"][0]["symbol"], "sh600135")

    def test_older_report_recovers_frozen_names_without_current_cache_or_rewriting_report(self):
        from service.research_names import snapshot_names
        self.assertEqual(snapshot_names(_pack(self.data)), {s["symbol"]: s["name"] for s in self.data["securities"]})
        nested = {"aMetadata": {"securities": [{"symbol": "sh600000", "name": "Wrong nested label"}]}, **self.data}
        self.assertEqual(snapshot_names(_pack(nested))["sh600000"], "隔离样本0")
        task = self.service.create(self.request(kind="poolPreview"))
        self.service.run_next()
        with self.service._db() as db:
            result = _unpack(db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0])
            result.pop("securityNames", None)
            blob = _pack(result)
            db.execute("UPDATE jobs SET result=? WHERE id=?", (blob, task["id"]))
        # A broken current catalogue must not prevent frozen labels recovering.
        (self.service.directory / "market.sqlite3").write_bytes(b"invalid isolated catalogue")
        recovered = self.service.get({"id": task["id"]})["result"]
        self.assertEqual(recovered["securityNames"]["sh600135"], "隔离样本135")
        with self.service._db() as db:
            self.assertEqual(db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0], blob)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dataset_names").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
