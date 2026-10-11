import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from service.backtest_engine import BacktestEngine
from service.strategies import normalize_config, strategy_conflicts
from service.research_service import ResearchService, snapshot_local


def dataset_fixture(count=190):
    day, bars = dt.date(2024, 1, 2), []
    while len(bars) < count:
        if day.weekday() < 5:
            price = 10 + len(bars) * .025
            bars.append(dict(date=day.isoformat(), open=price, high=price+.02, low=price-.02,
                             close=price+.01, volume=1000000, amount=price*1000000))
        day += dt.timedelta(days=1)
    data = {"securities": [{"symbol": "sh600001", "name": "测试股票", "bars": bars}], "warnings": []}
    data["fingerprint"] = hashlib.sha256(json.dumps(data).encode()).hexdigest()
    return data


CONFIG = {"strategyIds": ["momentum"], "strategyParams": {"momentum": {"lookback": 10, "trendWindow": 20, "minReturn": 0}},
          "maxHoldingDays": 15, "partialTakeProfitPct": 0, "takeProfitPct": 0}


class ResearchServiceTests(unittest.TestCase):
    def test_conflicts_follow_actual_parameter_windows_not_names(self):
        config = normalize_config({"strategyIds": ["trendBreakout", "trendPullback"]})
        self.assertTrue(strategy_conflicts(config))
        config["strategyParams"]["trendBreakout"]["lookback"] = 2
        self.assertEqual(strategy_conflicts(config), [])
        config["strategyParams"]["trendBreakout"]["lookback"] = 40
        config["strategyParams"]["trendPullback"]["minDrawdown"] = 0
        self.assertEqual(strategy_conflicts(config), [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.dataset = dataset_fixture()
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def service(self, **kwargs):
        result = ResearchService(self.directory, snapshot_loader=lambda *_: copy.deepcopy(self.dataset), **kwargs)
        self.services.append(result)
        return result

    def create(self, service, **kwargs):
        return service.create({"symbols": ["sh600001"], "config": CONFIG, **kwargs})

    def test_idempotent_submission_snapshot_and_completed_report(self):
        service = self.service(autostart=False)
        first = self.create(service, requestId="one")
        self.assertEqual(first["id"], self.create(service, requestId="one")["id"])
        self.dataset["securities"][0]["bars"][-1]["close"] = 999
        self.assertTrue(service.run_next())
        report = service.get({"id": first["id"]})
        self.assertEqual(report["task"]["status"], "completed")
        self.assertTrue(report["result"]["complete"])
        self.assertEqual(report["task"]["progress"], 1)
        engine = BacktestEngine(dataset_fixture(), CONFIG)
        while not engine.step(20):
            pass
        self.assertEqual(report["result"]["metrics"], engine.result()["metrics"])
        self.assertFalse(service.run_next())

    def test_batch_control_truthful_outcomes_and_restart(self):
        service = self.service(autostart=False)
        first, second = self.create(service), self.create(service)
        results = service.control({"ids": [first["id"], "missing"], "action": "pause"})["outcomes"]
        self.assertEqual([r["ok"] for r in results], [True, False])
        service.control({"ids": [second["id"]], "action": "stop"})
        self.assertFalse(service.run_next())
        service.close()
        restored = self.service(autostart=False)
        self.assertEqual(restored.get({"id": first["id"]})["task"]["status"], "paused")
        self.assertEqual(restored.get({"id": second["id"]})["task"]["status"], "stopped")
        restored.control({"ids": [first["id"]], "action": "resume"})
        self.assertTrue(restored.run_next())

    def test_running_pause_reopen_continue_is_identical(self):
        entered, release = threading.Event(), threading.Event()
        original = BacktestEngine.step
        def slow_step(engine, days=5):
            done = original(engine, days)
            if not entered.is_set():
                entered.set()
                release.wait(4)
            return done
        with patch.object(BacktestEngine, "step", slow_step):
            service = self.service(workers=1)
            task = self.create(service)
            self.assertTrue(entered.wait(4))
            service.control({"ids": [task["id"]], "action": "pause"})
            self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "pausing")
            release.set()
            deadline = time.monotonic() + 5
            while service.get({"id": task["id"]})["task"]["status"] != "paused" and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "paused")
            service.close()
        restored = self.service(autostart=False)
        restored.control({"ids": [task["id"]], "action": "resume"})
        restored.run_next()
        report = restored.get({"id": task["id"], "pageSize": 1000})["result"]
        engine = BacktestEngine(self.dataset, CONFIG)
        while not engine.step(20):
            pass
        self.assertEqual(report["metrics"], engine.result()["metrics"])
        self.assertEqual(report["trades"], engine.result()["trades"])
        self.assertEqual(report["curve"], engine.result()["curve"])

    def test_changed_code_version_does_not_resume_old_checkpoint(self):
        service = self.service(autostart=False)
        task = self.create(service)
        service.control({"ids": [task["id"]], "action": "pause"})
        service.version = "different-code"
        response = service.control({"ids": [task["id"]], "action": "resume"})
        self.assertFalse(response["outcomes"][0]["ok"])
        self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "paused")

    def test_report_path_pagination_does_not_change_saved_result(self):
        service = self.service(autostart=False)
        task = self.create(service)
        service.run_next()
        first = service.get({"id": task["id"], "pageSize": 3})["result"]
        second = service.get({"id": task["id"], "pageSize": 3, "decisionOffset": 3})["result"]
        self.assertTrue(first["pagination"]["decisions"]["hasMore"])
        self.assertEqual(len(first["decisions"]), 3)
        self.assertNotEqual(first["decisions"], second["decisions"])
        self.assertEqual(first, service.get({"id": task["id"], "pageSize": 3})["result"])
        with self.assertRaises(ValueError):
            service.get({"id": task["id"], "pageSize": True})

    def test_unfinished_queue_is_paused_after_process_restart(self):
        old = self.service(autostart=False)
        task = self.create(old)
        old.close()
        restored = self.service(autostart=False)
        self.assertEqual(restored.get({"id": task["id"]})["task"]["status"], "paused")

    def test_corrupt_snapshot_fails_only_its_job_and_queue_continues(self):
        service = self.service(autostart=False)
        task = self.create(service)
        with service._db() as db:
            db.execute("UPDATE jobs SET checkpoint=? WHERE id=?", (b"broken checkpoint", task["id"]))
        next_task = self.create(service)
        self.assertTrue(service.run_next())
        self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "failed")
        self.assertEqual(service.get({"id": next_task["id"]})["task"]["status"], "completed")
        self.assertEqual(service.inflight, set())

    def test_plateau_candidate_replay_uses_parent_snapshot_and_training_range(self):
        service = self.service(autostart=False)
        task = self.create(service, kind="plateau", scan={"axes": [{"path": "maxHoldingDays", "values": [5, 10, 15]}],
                                                         "minTrades": 1})
        service.run_next()
        parent = service.get({"id": task["id"]})
        self.assertEqual(parent["task"]["status"], "completed")
        point = parent["result"]["candidates"][0]
        self.dataset["securities"][0]["bars"][-1]["close"] = 999
        replay = service.replay({"id": task["id"], "candidateId": point["id"]})
        self.assertEqual(replay["parentId"], task["id"])
        self.assertEqual(replay["config"]["endDate"], parent["result"]["split"]["training"]["endDate"])
        self.assertLess(replay["config"]["endDate"], parent["result"]["split"]["holdout"]["startDate"])
        service.run_next()
        self.assertEqual(service.get({"id": replay["id"]})["task"]["status"], "completed")
        self.assertEqual(service.get({"id": task["id"]}), parent)

    def test_nested_holdout_path_is_pageable(self):
        service = self.service(autostart=False)
        task = self.create(service, kind="plateau", scan={"axes": [{"path": "maxHoldingDays", "values": [5, 10, 15]}],
                                                         "minTrades": 1})
        service.run_next()
        result = service.get({"id": task["id"], "pageSize": 3})["result"]
        self.assertIsNotNone(result["recommendation"])
        next_page = service.get({"id": task["id"], "pageSize": 3, "decisionOffset": 3})["result"]
        self.assertNotEqual(result["validation"]["holdout"]["decisions"], next_page["validation"]["holdout"]["decisions"])
        self.assertEqual(next_page["validation"]["holdout"]["pagination"]["decisions"]["offset"], 3)

    def test_insufficient_data_plateau_cannot_replay_uncomputed_point(self):
        self.dataset = dataset_fixture(10)
        service = self.service(autostart=False)
        task = self.create(service, kind="plateau", scan={"axes": [{"path": "maxHoldingDays", "values": [5, 10, 15]}]})
        service.run_next()
        report = service.get({"id": task["id"]})["result"]
        with self.assertRaisesRegex(ValueError, "数据不足"):
            service.replay({"id": task["id"], "candidateId": report["candidates"][0]["id"]})

    def test_real_tdx_snapshot_uses_configured_source_only(self):
        root = self.directory / "chosen"
        target = root / "vipdoc/sh/lday/sh600001.day"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"".join(struct.pack("<IIIIIfII", int(row["date"].replace("-", "")),
                          round(row["open"]*100), round(row["high"]*100), round(row["low"]*100),
                          round(row["close"]*100), row["amount"], row["volume"], 0)
                          for row in self.dataset["securities"][0]["bars"]))
        db = sqlite3.connect(self.directory / "market.sqlite3")
        with db:
            db.executescript("CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE securities(symbol TEXT,name TEXT);")
            db.execute("INSERT INTO meta VALUES ('path',?)", (json.dumps(str(root)),))
            db.execute("INSERT INTO securities VALUES ('sh600001','测试股票')")
        db.close()
        snap = snapshot_local(self.directory, ["sh600001"], "2024-02-01")
        self.assertEqual(snap["securities"][0]["bars"][-1]["date"], "2024-02-01")
        self.assertEqual(snap["adjustment"], "none")
        with self.assertRaises(ValueError):
            snapshot_local(self.directory, ["sh600001", "sh600001"])
        with self.assertRaises(ValueError):
            snapshot_local(self.directory, ["sh000001"])


if __name__ == "__main__":
    unittest.main()
