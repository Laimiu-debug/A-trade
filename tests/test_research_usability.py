"""Full-pool browsing and durable task CRUD using isolated data."""
from copy import deepcopy
import tempfile
import unittest
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
