"""Offline tests for the fixed five-batch evidence series; never use network."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("missing_history_probe_v2", ROOT / "scripts" / "supplement-missing-historical-evidence-v2.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def plan_fixture():
    source = {"auditSha256": "source", "missingUnionSymbols": [f"sh600{i:03d}" for i in range(125)]}
    ordered = sorted(source["missingUnionSymbols"], key=lambda s: (hashlib.sha256((probe.v1.SEED + ":" + s).encode()).hexdigest(), s))
    completed = {"completedSymbols": ordered[:8]}
    return source, completed, probe.make_plan(source, completed)


class MissingHistorySeriesTests(unittest.TestCase):
    def test_117_order_excludes_only_verified_original_eight(self):
        source, completed, plan = plan_fixture()
        expected = sorted(source["missingUnionSymbols"], key=lambda s: (hashlib.sha256((probe.v1.SEED + ":" + s).encode()).hexdigest(), s))
        self.assertEqual([s["symbol"] for s in plan["selected"]], expected[8:])
        self.assertEqual([s["rank"] for s in plan["selected"]], list(range(9, 126)))
        self.assertEqual([len(b["selected"]) for b in plan["batches"]], [24, 24, 24, 24, 21])
        self.assertEqual([len(b["requests"]) for b in plan["batches"]], [144, 144, 144, 144, 126])
        self.assertEqual(len(plan["requests"]), 702)
        self.assertEqual(len({r["id"] for r in plan["requests"]}), 702)
        self.assertFalse(set(completed["completedSymbols"]) & {s["symbol"] for s in plan["selected"]})
        bad = copy.deepcopy(completed)
        bad["completedSymbols"][0] = expected[8]
        with self.assertRaises(ValueError):
            probe.make_plan(source, bad)

    def prepare_series(self, directory):
        _, _, plan = plan_fixture()
        directory.mkdir()
        probe.write(directory / "protocol.json", {}, frozen=True)
        probe.write(directory / "request-manifest.json", plan, frozen=True)
        for batch in plan["batches"]:
            current = directory / "batches" / batch["id"]
            current.mkdir(parents=True)
            probe.write(current / "request-manifest.json", batch, frozen=True)
        return plan

    def fake_run(self, directory, calls, *, fail_batch=None, timeout=False):
        def run(command, *, timeout: float, check):
            batch_id = command[-1]
            calls.append((batch_id, timeout))
            current = directory / "batches" / batch_id
            probe.write(current / "attempt.json", {}, frozen=True)
            if fail_batch == batch_id and timeout_mode:
                raise subprocess.TimeoutExpired(command, timeout)
            probe.write(current / "manifest.json", {"wireSends": 146, "status": "completed"}, frozen=True)
            return SimpleNamespace(returncode=0)
        timeout_mode = timeout
        return run

    def test_success_is_five_serial_batches_and_whole_series_cannot_resume(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "series"):
            plan = self.prepare_series(probe.OUTPUT)
            calls = []
            with patch.object(probe, "verify_frozen", return_value=plan), patch.object(probe, "validate_batch", return_value={"completeBatch": True}):
                result = probe.execute_series(run=self.fake_run(probe.OUTPUT, calls), clock=lambda: 0)
                self.assertEqual(result["status"], "completed")
                self.assertEqual([call[0] for call in calls], [b["id"] for b in plan["batches"]])
                self.assertTrue(all(t == 300 for _, t in calls))
                self.assertEqual(result["wireSends"], 730)
                with self.assertRaises(FileExistsError):
                    probe.execute_series(run=lambda *a, **k: self.fail("retry attempted"))

    def test_incomplete_or_logout_unconfirmed_stops_without_next_batch(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "series"):
            plan = self.prepare_series(probe.OUTPUT)
            calls = []
            validations = iter([{"completeBatch": True}, {"completeBatch": False}])
            with patch.object(probe, "verify_frozen", return_value=plan), patch.object(probe, "validate_batch", side_effect=lambda *a: next(validations)):
                result = probe.execute_series(run=self.fake_run(probe.OUTPUT, calls), clock=lambda: 0)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual([c[0] for c in calls], ["batch-01", "batch-02"])
            self.assertFalse((probe.OUTPUT / "batches" / "batch-03" / "process.json").exists())

    def test_timeout_consumes_attempt_and_never_starts_following_batch(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "series"):
            plan = self.prepare_series(probe.OUTPUT)
            calls = []
            with patch.object(probe, "verify_frozen", return_value=plan):
                result = probe.execute_series(run=self.fake_run(probe.OUTPUT, calls, fail_batch="batch-01", timeout=True), clock=lambda: 0)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(len(calls), 1)
            self.assertTrue(probe.read(probe.OUTPUT / "batches" / "batch-01" / "process.json")["timedOut"])
            self.assertTrue((probe.OUTPUT / "batches" / "batch-01" / "attempt.json").exists())

    def test_shared_deadline_reduces_worker_timeout_and_prevents_next_start(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "series"):
            plan = self.prepare_series(probe.OUTPUT)
            calls = []
            ticks = iter([0, 1490, 1501, 1501])
            with patch.object(probe, "verify_frozen", return_value=plan), patch.object(probe, "validate_batch", return_value={"completeBatch": True}):
                result = probe.execute_series(run=self.fake_run(probe.OUTPUT, calls), clock=lambda: next(ticks))
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(calls, [("batch-01", 10)])

    def test_direct_worker_is_rejected_without_active_parent(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "series"):
            plan = self.prepare_series(probe.OUTPUT)
            probe.write(probe.OUTPUT / "run.json", {"status": "stopped", "activeBatch": None}, frozen=True)
            with patch.object(probe, "verify_frozen", return_value=plan), self.assertRaisesRegex(ValueError, "active series"):
                probe.worker_batch("batch-02")
            self.assertFalse((probe.OUTPUT / "batches" / "batch-02" / "attempt.json").exists())

    @unittest.skipUnless(probe.V1_OUTPUT.exists(), "Optional local frozen v1 evidence is absent")
    def test_v1_readonly_integrity_and_full_source_evidence(self):
        paths = list(probe.V1_OUTPUT.rglob("*"))
        before = {p: probe.sha(p) for p in paths if p.is_file()}
        completed = probe.verified_v1()
        self.assertEqual(len(completed["completedSymbols"]), 8)
        self.assertEqual(len(completed["requests"]), 48)
        self.assertEqual(before, {p: probe.sha(p) for p in before})
        self.assertEqual(len(probe.make_plan(probe.v1.verify_sources(), completed)["requests"]), 702)

    @unittest.skipUnless(probe.V1_OUTPUT.exists(), "Optional local frozen v1 evidence is absent")
    def test_all_five_manifests_are_frozen_without_network_and_drift_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "series"):
            probe.freeze()
            plan = probe.verify_frozen()
            for batch in plan["batches"]:
                directory = probe.OUTPUT / "batches" / batch["id"]
                self.assertEqual({p.name for p in directory.iterdir()}, {"protocol.json", "request-manifest.json"})
            self.assertFalse((probe.OUTPUT / "run.json").exists())
            last = probe.OUTPUT / "batches" / "batch-05" / "request-manifest.json"
            data = probe.read(last)
            data["requests"].pop()
            probe.write(last, data)
            with self.assertRaisesRegex(ValueError, "Batch request drift"):
                probe.verify_frozen()


if __name__ == "__main__":
    unittest.main()
