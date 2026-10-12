"""Synthetic end-to-end queue tests; no local market source or real study runs."""
import copy
from contextlib import closing
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from service.portfolio_engine import PortfolioEngine
from service.portfolio_universe import UniversePreparationInterrupted
from service.research_service import ResearchService, _pack, _unpack


def dataset_fixture(symbol_count=4, bar_count=85):
    days, day = [], date(2024, 1, 2)
    while len(days) < bar_count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    securities = []
    for index in range(symbol_count):
        symbol = f"sh{600000 + index:06d}"
        bars = []
        for j, day in enumerate(days):
            price = 10 + index * .02 + j * (.012 + index * .0001)
            bars.append({"date": day, "open": price, "high": price + .03, "low": price - .03,
                         "close": price + .01, "volume": 1000000, "amount": price * 1000000})
        securities.append({"symbol": symbol, "name": f"fixture-{index}", "bars": bars,
                           "listingDate": "2000-01-01", "stByDate": {day: False for day in days},
                           "memberByDate": {day: True for day in days}})
    data = {"securities": securities, "calendar": days, "adjustment": "raw", "warnings": [],
            "universeCoverage": {"loadedCount": symbol_count, "batchTruncation": False}}
    data["fingerprint"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


CONFIG = {"strategies": [{"id": "mom", "kind": "momentum", "params": {
    "lookback": 3, "trendWindow": 5, "minReturn": 0}}],
    "allocation": {"mode": "single", "strategyIds": ["mom"]}, "lookbackDays": 39,
    "universe": {"minListingDays": 0, "minAmount20": 0}, "topN": 2, "maxPositions": 2,
    "maxStockWeight": .5, "maxHoldingDays": 5, "takeProfitPct": 0, "trailingStopPct": 0}


class PortfolioServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.dataset = dataset_fixture()
        self.calls, self.services = [], []
        # Keep restart tests independent of simultaneous development writes.
        version = patch("service.research_service._version", return_value="portfolio-service-test-v1")
        version.start()
        self.addCleanup(version.stop)

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def loader(self, directory, selection, end_date, start_date):
        self.calls.append((copy.deepcopy(selection), end_date, start_date))
        return copy.deepcopy(self.dataset)

    def service(self, **kwargs):
        instance = ResearchService(self.directory, universe_loader=self.loader, **kwargs)
        self.services.append(instance)
        return instance

    def create(self, service, **kwargs):
        return service.create({"kind": "portfolio", "selection": {"mode": "all"},
                               "config": copy.deepcopy(CONFIG), **kwargs})

    def wait_status(self, service, task_id, status):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = service.get({"id": task_id})["task"]["status"]
            if current == status:
                return
            time.sleep(.01)
        self.fail(f"task did not become {status}: {current}")

    def pause_while_serialization_is_blocked(self, service, task_id, entered, release, worker):
        responses, errors = [], []
        returned = threading.Event()
        def control():
            try:
                responses.append(service.control({"ids": [task_id], "action": "pause"}))
            except Exception as exc:
                errors.append(exc)
            finally:
                returned.set()
        controller = threading.Thread(target=control)
        try:
            self.assertTrue(entered.wait(5), "serialization barrier was not reached")
            controller.start()
            self.assertTrue(returned.wait(3), "pause waited for serialization to release the service lock")
            self.assertFalse(release.is_set())
            self.assertEqual(errors, [])
            self.assertTrue(responses[0]["outcomes"][0]["ok"])
            task = next(item for item in responses[0]["tasks"] if item["id"] == task_id)
            self.assertEqual(task["status"], "pausing")
        finally:
            release.set()
            worker.join(8)
            if controller.ident is not None:
                controller.join(8)
        self.assertFalse(worker.is_alive())
        self.assertFalse(controller.is_alive())

    def test_pause_is_responsive_during_snapshot_and_result_packing(self):
        for phase in ("snapshot", "result"):
            with self.subTest(phase=phase):
                service = self.service(autostart=False)
                task = self.create(service)
                entered, release = threading.Event(), threading.Event()
                def blocked_pack(value):
                    matching = isinstance(value, dict) and (
                        "securities" in value if phase == "snapshot" else value.get("kind") == "portfolio")
                    if matching and not entered.is_set():
                        entered.set()
                        if not release.wait(10):
                            raise RuntimeError("packing barrier was not released")
                    return _pack(value)
                with patch("service.research_service._pack", blocked_pack):
                    worker = threading.Thread(target=service.run_next)
                    worker.start()
                    self.pause_while_serialization_is_blocked(service, task["id"], entered, release, worker)
                report = service.get({"id": task["id"]})
                self.assertEqual(report["task"]["status"], "paused")
                with service._db() as db:
                    saved = db.execute("SELECT checkpoint FROM jobs WHERE id=?", (task["id"],)).fetchone()
                self.assertEqual(_unpack(saved["checkpoint"])["cursor"], 0 if phase == "snapshot" else 5)
                if phase == "result":
                    self.assertEqual(report["result"]["taskStatus"], "paused")
                    self.assertFalse(report["result"]["complete"])
                expected = PortfolioEngine(copy.deepcopy(self.dataset), report["task"]["config"])
                while not expected.step(20):
                    pass
                reference = expected.result()
                self.assertGreater(len(reference["trades"]), 0)
                service.close()
                restored = self.service(autostart=False)
                restored.control({"ids": [task["id"]], "action": "resume"})
                restored.run_next()
                resumed = restored.get({"id": task["id"], "pageSize": 1000})
                self.assertEqual(resumed["task"]["status"], "completed")
                for key in ("metrics", "trades", "closedTrades", "curve"):
                    self.assertEqual(resumed["result"][key], reference[key])
                restored.close()

    def test_pause_is_responsive_during_restore_unpack_and_preserves_exact_checkpoint(self):
        service = self.service(autostart=False)
        task = self.create(service)
        original_step = PortfolioEngine.step
        def pause_after_first_chunk(engine, days=5):
            done = original_step(engine, days)
            service.control({"ids": [task["id"]], "action": "pause"})
            return done
        with patch.object(PortfolioEngine, "step", pause_after_first_chunk):
            service.run_next()
        config = service.get({"id": task["id"]})["task"]["config"]
        with service._db() as db:
            saved = db.execute("SELECT checkpoint,data FROM jobs JOIN datasets ON jobs.dataset_id=datasets.id WHERE jobs.id=?", (task["id"],)).fetchone()
        initial_checkpoint, dataset_blob = saved["checkpoint"], saved["data"]
        self.assertEqual(_unpack(initial_checkpoint)["cursor"], 5)
        service.close()
        restored = self.service(autostart=False)
        restored.control({"ids": [task["id"]], "action": "resume"})
        entered, release = threading.Event(), threading.Event()
        def blocked_unpack(value):
            if value == dataset_blob and not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("restore barrier was not released")
            return _unpack(value)
        with patch("service.research_service._unpack", blocked_unpack):
            worker = threading.Thread(target=restored.run_next)
            worker.start()
            self.pause_while_serialization_is_blocked(restored, task["id"], entered, release, worker)
        report = restored.get({"id": task["id"]})
        self.assertEqual(report["task"]["status"], "paused")
        self.assertEqual(report["result"]["taskStatus"], "paused")
        self.assertFalse(report["result"]["complete"])
        with restored._db() as db:
            unchanged = db.execute("SELECT checkpoint FROM jobs WHERE id=?", (task["id"],)).fetchone()[0]
        self.assertEqual(unchanged, initial_checkpoint, "pausing during decode must not advance the ledger")
        expected = PortfolioEngine(_unpack(dataset_blob), config)
        while not expected.step(20):
            pass
        reference = expected.result()
        self.assertGreater(len(reference["trades"]), 0)
        restored.control({"ids": [task["id"]], "action": "resume"})
        restored.run_next()
        resumed = restored.get({"id": task["id"], "pageSize": 1000})
        self.assertEqual(resumed["task"]["status"], "completed")
        for key in ("metrics", "trades", "closedTrades", "curve"):
            self.assertEqual(resumed["result"][key], reference[key])

    def test_accept_is_async_idempotent_and_whole_universe_exceeds_120(self):
        self.dataset = dataset_fixture(symbol_count=129)
        service = self.service(autostart=False)
        payload_config = copy.deepcopy(CONFIG)
        task = self.create(service, requestId="whole-pool", config=payload_config)
        payload_config["topN"] = 99
        self.assertEqual(self.calls, [])
        self.assertIsNone(task["fingerprint"])
        self.assertEqual(task["id"], self.create(service, requestId="whole-pool")["id"])
        self.assertTrue(service.run_next())
        report = service.get({"id": task["id"], "pageSize": 1000})
        self.assertEqual(report["task"]["status"], "completed")
        self.assertEqual(report["task"]["symbolCount"], 129)
        self.assertEqual(report["task"]["symbols"], [])
        self.assertEqual(len(report["result"]["curve"]), 39)
        self.assertEqual(report["task"]["config"]["topN"], CONFIG["topN"])
        self.assertEqual(report["task"]["config"]["startDate"], self.dataset["calendar"][-39])
        self.assertEqual(report["task"]["config"]["endDate"], self.dataset["calendar"][-1])
        self.assertTrue(all(row["coverage"]["totalSecurities"] == 129 for row in report["result"]["daily"]))
        self.assertTrue(all(row["coverage"]["batchTruncation"] is False for row in report["result"]["daily"]))
        with service._db() as db:
            row = db.execute("SELECT data FROM jobs JOIN datasets ON jobs.dataset_id=datasets.id WHERE jobs.id=?", (task["id"],)).fetchone()
        self.assertEqual(len(_unpack(row[0])["securities"]), 129)
        self.assertEqual(len(self.calls), 1)

    def test_pending_pause_resume_and_restart_preserve_deferred_loading(self):
        service = self.service(autostart=False)
        task = self.create(service)
        service.control({"ids": [task["id"]], "action": "pause"})
        self.assertFalse(service.run_next())
        self.assertEqual(self.calls, [])
        service.close()
        restored = self.service(autostart=False)
        self.assertEqual(restored.get({"id": task["id"]})["task"]["status"], "paused")
        restored.control({"ids": [task["id"]], "action": "resume"})
        restored.run_next()
        self.assertEqual(restored.get({"id": task["id"]})["task"]["status"], "completed")
        self.assertEqual(len(self.calls), 1)

    def test_pause_during_preparation_freezes_source_before_resume(self):
        entered, release = threading.Event(), threading.Event()
        original_loader = self.loader
        def slow_loader(*args):
            result = original_loader(*args)
            entered.set()
            release.wait(6)
            return result
        service = self.service(workers=1)
        service.universe_loader = slow_loader
        task = self.create(service)
        try:
            self.assertTrue(entered.wait(5))
            service.control({"ids": [task["id"]], "action": "pause"})
            self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "pausing")
        finally:
            release.set()
        self.wait_status(service, task["id"], "paused")
        frozen = service.get({"id": task["id"]})["task"]
        self.assertEqual(frozen["fingerprint"], self.dataset["fingerprint"])
        service.close()
        self.dataset = dataset_fixture(symbol_count=8)
        restored = self.service(autostart=False)
        restored.control({"ids": [task["id"]], "action": "resume"})
        restored.run_next()
        self.assertEqual(restored.get({"id": task["id"]})["task"]["symbolCount"], 4)
        self.assertEqual(len(self.calls), 1)

    def test_running_pause_resume_matches_same_frozen_ledger(self):
        entered, release = threading.Event(), threading.Event()
        original_step = PortfolioEngine.step
        def slow_step(engine, days=5):
            done = original_step(engine, days)
            if not entered.is_set():
                entered.set()
                release.wait(6)
            return done
        with patch.object(PortfolioEngine, "step", slow_step):
            service = self.service(workers=1)
            task = self.create(service)
            try:
                self.assertTrue(entered.wait(5))
                service.control({"ids": [task["id"]], "action": "pause"})
            finally:
                release.set()
            self.wait_status(service, task["id"], "paused")
            service.close()
        config = service.get({"id": task["id"]})["task"]["config"]
        expected = PortfolioEngine(copy.deepcopy(self.dataset), config)
        while not expected.step(20):
            pass
        self.dataset["securities"][0]["bars"][-1]["close"] = 999
        restored = self.service(autostart=False)
        restored.control({"ids": [task["id"]], "action": "resume"})
        restored.run_next()
        actual = restored.get({"id": task["id"], "pageSize": 1000})["result"]
        self.assertEqual(actual["metrics"], expected.result()["metrics"])
        self.assertEqual(actual["trades"], expected.result()["trades"])
        self.assertEqual(actual["curve"], expected.result()["curve"])
        self.assertEqual(len(self.calls), 1)

    def test_cancellable_snapshot_discards_partial_preparation_and_refreezes(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def cancellable_loader(directory, selection, end_date, start_date, *, progress, cancelled):
            calls.append(copy.deepcopy(self.dataset))
            if len(calls) == 1:
                progress(2, len(self.dataset["securities"]))
                entered.set()
                release.wait(6)
                if cancelled():
                    raise UniversePreparationInterrupted("synthetic batch interruption")
                self.fail("preparation did not receive cancellation")
            self.assertFalse(cancelled())
            return copy.deepcopy(self.dataset)
        with patch("service.portfolio_universe.snapshot_universe", cancellable_loader):
            service = ResearchService(self.directory, workers=1)
            self.services.append(service)
            task = self.create(service)
            try:
                self.assertTrue(entered.wait(5))
                service.control({"ids": [task["id"]], "action": "pause"})
            finally:
                release.set()
            self.wait_status(service, task["id"], "paused")
            paused = service.get({"id": task["id"]})["task"]
            self.assertIsNone(paused["fingerprint"])
            self.assertEqual(paused["preparation"]["processedFiles"], 2)
            with service._db() as db:
                pending = db.execute("SELECT checkpoint,data FROM jobs JOIN datasets ON jobs.dataset_id=datasets.id WHERE jobs.id=?", (task["id"],)).fetchone()
            self.assertIsNone(pending["checkpoint"])
            self.assertTrue(_unpack(pending["data"])["pendingUniverse"])
            service.close()
            self.dataset = dataset_fixture(symbol_count=8)
            restored = ResearchService(self.directory, autostart=False)
            self.services.append(restored)
            restored.control({"ids": [task["id"]], "action": "resume"})
            restored.run_next()
            completed = restored.get({"id": task["id"]})["task"]
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["symbolCount"], 8)
            self.assertEqual(completed["fingerprint"], self.dataset["fingerprint"])
            self.assertEqual(len(calls), 2)

    def test_two_workers_serialize_whole_universe_jobs(self):
        entered, release = threading.Event(), threading.Event()
        counter_lock = threading.Lock()
        counts = {"active": 0, "maximum": 0, "calls": 0}
        def blocking_loader(*args):
            with counter_lock:
                counts["active"] += 1
                counts["calls"] += 1
                counts["maximum"] = max(counts["maximum"], counts["active"])
                first = counts["calls"] == 1
            try:
                if first:
                    entered.set()
                    release.wait(6)
                return copy.deepcopy(self.dataset)
            finally:
                with counter_lock:
                    counts["active"] -= 1
        service = self.service(workers=2)
        service.universe_loader = blocking_loader
        tasks = [self.create(service) for _ in range(2)]
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(service.get({"id": tasks[0]["id"]})["task"]["status"], "running")
            self.assertEqual(service.get({"id": tasks[1]["id"]})["task"]["status"], "queued")
            self.assertIsNone(service._claim())
            self.assertEqual(counts["calls"], 1)
        finally:
            release.set()
        for task in tasks:
            self.wait_status(service, task["id"], "completed")
        self.assertEqual(counts["calls"], 2)
        self.assertEqual(counts["maximum"], 1)

    def test_same_public_fingerprint_different_batch_metadata_resume_exact_snapshots(self):
        service = self.service(autostart=False)
        def metadata_loader(directory, selection, end_date, start_date):
            data = self.loader(directory, selection, end_date, start_date)
            data["universeCoverage"]["batchSize"] = selection["batchSize"]
            data["universeSelection"] = copy.deepcopy(selection)
            return data
        service.universe_loader = metadata_loader
        tasks = [self.create(service, selection={"mode": "all", "batchSize": size}) for size in (1, 7)]
        original_step = PortfolioEngine.step
        def pause_first_chunk(engine, days=5):
            result = original_step(engine, days)
            service.control({"ids": list(service.inflight), "action": "pause"})
            return result
        with patch.object(PortfolioEngine, "step", pause_first_chunk):
            self.assertTrue(service.run_next())
            self.assertTrue(service.run_next())
        storage_ids = []
        for task, size in zip(tasks, (1, 7)):
            report = service.get({"id": task["id"]})
            self.assertEqual(report["task"]["status"], "paused")
            self.assertEqual(report["task"]["fingerprint"], self.dataset["fingerprint"])
            with service._db() as db:
                saved = db.execute("SELECT dataset_id,data FROM jobs JOIN datasets ON jobs.dataset_id=datasets.id WHERE jobs.id=?", (task["id"],)).fetchone()
            storage_ids.append(saved["dataset_id"])
            self.assertEqual(_unpack(saved["data"])["universeCoverage"]["batchSize"], size)
        self.assertNotEqual(*storage_ids)
        service.close()
        restored = self.service(autostart=False)
        restored.control({"ids": [task["id"] for task in tasks], "action": "resume"})
        self.assertTrue(restored.run_next())
        self.assertTrue(restored.run_next())
        reports = [restored.get({"id": task["id"]}) for task in tasks]
        self.assertTrue(all(report["task"]["status"] == "completed" for report in reports))
        self.assertEqual(reports[0]["result"]["metrics"], reports[1]["result"]["metrics"])
        self.assertNotEqual(reports[0]["result"]["dataDigest"], reports[1]["result"]["dataDigest"])
        self.assertEqual(len(self.calls), 2)

    def test_missing_or_corrupt_pending_snapshots_fail_only_their_jobs(self):
        service = self.service(autostart=False)
        broken, missing, good = [self.create(service) for _ in range(3)]
        with closing(sqlite3.connect(service.path)) as db, db:
            db.execute("UPDATE datasets SET data=? WHERE id=(SELECT dataset_id FROM jobs WHERE id=?)", (b"broken", broken["id"]))
            db.execute("DELETE FROM datasets WHERE id=(SELECT dataset_id FROM jobs WHERE id=?)", (missing["id"],))
        self.assertTrue(service.run_next())
        for task in (broken, missing):
            self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "failed")
        self.assertEqual(service.get({"id": good["id"]})["task"]["status"], "completed")
        self.assertEqual(service.inflight, set())

    def test_daily_and_trade_offsets_are_independent_and_results_immutable(self):
        service = self.service(autostart=False)
        task = self.create(service)
        service.run_next()
        first = service.get({"id": task["id"], "pageSize": 2})["result"]
        second = service.get({"id": task["id"], "pageSize": 2, "dailyOffset": 2, "tradeOffset": 1})["result"]
        self.assertEqual(first["pagination"]["daily"]["total"], 39)
        self.assertTrue(first["pagination"]["daily"]["hasMore"])
        self.assertNotEqual(first["daily"], second["daily"])
        self.assertEqual(second["pagination"]["trades"]["offset"], 1)
        self.assertEqual(second["pagination"]["decisions"]["offset"], 0)
        saved = service.get({"id": task["id"], "pageSize": 2})["result"]
        self.assertEqual(saved, first)
        first["daily"][0]["coverage"]["totalSecurities"] = 999
        self.assertEqual(saved, service.get({"id": task["id"], "pageSize": 2})["result"])
        for payload in ({"dailyOffset": -1}, {"tradeOffset": True}, {"pageSize": True}):
            with self.assertRaises(ValueError):
                service.get({"id": task["id"], **payload})

    def test_comparison_interface_and_nested_pageable_paths(self):
        service = self.service(autostart=False)
        days = self.dataset["calendar"]
        config = {"startDate": days[10], "endDate": days[-1],
                  "universe": {"minListingDays": 0, "minAmount20": 0},
                  "study": {"trainEnd": days[54], "testStart": days[55], "testEnd": days[-1],
                            "persistenceWindow": 5, "permutationCount": 99}}
        task = self.create(service, kind="comparison", config=config)
        self.assertEqual(self.calls, [])
        service.run_next()
        report = service.get({"id": task["id"], "pageSize": 2})
        self.assertEqual(report["task"]["status"], "completed")
        result = report["result"]
        self.assertTrue(result["complete"])
        self.assertEqual(set(result["testing"]), {"fixed", "equalWeight"})
        self.assertFalse(result["protocol"]["testUsedForSelection"])
        self.assertFalse(result["rotationGate"]["passed"])
        self.assertIn("insufficient_nonoverlapping_window_pairs", result["training"]["persistence"]["failures"])
        next_page = service.get({"id": task["id"], "pageSize": 2, "dailyOffset": 2})["result"]
        self.assertNotEqual(result["testing"]["fixed"]["daily"], next_page["testing"]["fixed"]["daily"])
        self.assertEqual(next_page["testing"]["fixed"]["pagination"]["daily"]["offset"], 2)
        self.assertEqual(result, service.get({"id": task["id"], "pageSize": 2})["result"])
        capabilities = service.catalogue()["portfolio"]["capabilities"]
        self.assertTrue(capabilities["wholeUniverse"])
        self.assertTrue(capabilities["rotation"])
        self.assertTrue(capabilities["systemPlateau"])
        self.assertTrue(capabilities["rotationRequiresTrainingPersistence"])

    def test_invalid_study_dates_rejected_without_loading_source(self):
        service = self.service(autostart=False)
        with self.assertRaises(ValueError):
            self.create(service, kind="comparison", config={"study": {}})
        self.assertEqual(self.calls, [])
        self.assertEqual(service.list()["tasks"], [])

    def test_nested_fold_audit_pagination_targets_only_selected_run(self):
        service = self.service(autostart=False)
        task = self.create(service)
        run = {"daily": [{"date": f"2024-01-{i + 1:02d}"} for i in range(9)],
               "rotationLog": [{"date": f"2024-01-{i + 1:02d}", "reason": "fixture"} for i in range(5)]}
        stored = {"folds": [{"testing": {"softRotation": copy.deepcopy(run), "equalWeight": copy.deepcopy(run)}}]}
        with service._db() as db:
            db.execute("UPDATE jobs SET result=? WHERE id=?", (_pack(stored), task["id"]))
        report = service.get({"id": task["id"], "resultPath": "folds.0.testing.softRotation",
                              "dailyOffset": 3, "rotationOffset": 2, "pageSize": 2})["result"]
        rotation, equal = (report["folds"][0]["testing"][key] for key in ("softRotation", "equalWeight"))
        self.assertEqual(rotation["daily"], run["daily"][3:5])
        self.assertEqual(rotation["rotationLog"], run["rotationLog"][2:4])
        self.assertEqual(equal["daily"], run["daily"][:2])
        self.assertEqual(rotation["pagination"]["path"], "folds.0.testing.softRotation")
        self.assertEqual(rotation["pagination"]["daily"]["total"], 9)
        with service._db() as db:
            original = _unpack(db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0])
        self.assertEqual(original, stored)
        for path in ("folds.99.testing", "folds.-1", "missing", [], 2):
            with self.subTest(path=path), self.assertRaises(ValueError):
                service.get({"id": task["id"], "resultPath": path})

    def test_walk_forward_queue_pause_restart_uses_frozen_source(self):
        from service.portfolio_experiment import PortfolioExperiment
        service = self.service(autostart=False)
        config = copy.deepcopy(CONFIG)
        config.update(startDate=self.dataset["calendar"][0], endDate=self.dataset["calendar"][-1])
        experiment = {"kind": "walkForward", "trainDays": 35, "testDays": 10, "stepDays": 10,
                      "maxFolds": 2, "axes": []}
        task = self.create(service, kind="walkForward", config=config, experiment=experiment)
        self.assertEqual(self.calls, [])
        original_step = PortfolioExperiment.step
        def pause_after_chunk(runner, days=5):
            done = original_step(runner, days)
            service.control({"ids": [task["id"]], "action": "pause"})
            return done
        with patch.object(PortfolioExperiment, "step", pause_after_chunk):
            service.run_next()
        paused = service.get({"id": task["id"]})
        self.assertEqual(paused["task"]["status"], "paused", paused["task"].get("error"))
        reference = PortfolioExperiment(copy.deepcopy(self.dataset), paused["task"]["config"], paused["task"]["experiment"])
        while not reference.step(days=20):
            pass
        service.close()
        self.dataset = dataset_fixture(symbol_count=9)
        restored = self.service(autostart=False)
        restored.control({"ids": [task["id"]], "action": "resume"})
        restored.run_next()
        report = restored.get({"id": task["id"]})
        self.assertEqual(report["task"]["status"], "completed", report["task"].get("error"))
        with restored._db() as db:
            stored = _unpack(db.execute("SELECT result FROM jobs WHERE id=?", (task["id"],)).fetchone()[0])
        stored.pop("kind", None)
        self.assertEqual(stored.pop("validationAudit")["status"], "fresh")
        self.assertEqual(stored.pop("securityNames"), {s["symbol"]: s["name"] for s in reference.dataset["securities"]})
        self.assertEqual(stored, reference.result())
        self.assertEqual(len(self.calls), 1)

    def test_invalid_system_experiment_rejected_before_source_read(self):
        service = self.service(autostart=False)
        config = {**copy.deepcopy(CONFIG), "startDate": self.dataset["calendar"][0], "endDate": self.dataset["calendar"][-1]}
        for experiment in ([], {"kind": "walkForward"}, {"axes": []}, {"axes": [{"path": "__class__", "values": [1, 2]}]}):
            with self.subTest(experiment=experiment), self.assertRaises(ValueError):
                self.create(service, kind="systemPlateau", config=config, experiment=experiment)
        self.assertEqual(self.calls, [])
        self.assertEqual(service.list()["tasks"], [])


if __name__ == "__main__":
    unittest.main()
