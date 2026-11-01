"""Temporary source/complete-opportunity/account lifecycle integration only."""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import unittest
from unittest.mock import patch
import zipfile

from tests import test_signal_early_strength_emergence_runner as source_fixture

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "scripts/run-account-exit-comparison.py"
REGISTRATION = "docs/account-exit-comparison-v1.json"


def module():
    spec = importlib.util.spec_from_file_location("account_exit_runner_synthetic", ROOT / SOURCE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_lines(path, values):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in values:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def read_gzip(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


class AccountExitComparisonRunnerTests(unittest.TestCase):
    """Public fixture for independent verifier. Source audit is a labelled contract.

    Raw .day -> old frozen source runner -> full pre-active files are real
    synthetic execution. The old panel/audit acceptance is a synthetic proof
    contract, never a claim that its cross-sectional ranks were market-audited.
    """
    def setUp(self):
        self.runner = module()
        self.parent_fixture = source_fixture.EarlyStrengthEmergenceRunnerTests()
        self.parent_fixture.setUp()
        self.addCleanup(self.parent_fixture.doCleanups)
        parent = self.parent_fixture
        self.root, self.workspace, self.source, self.panel = parent.root, parent.workspace, parent.source, parent.panel
        self.source_output = parent.output
        self.output = self.root / "comparison"
        self.symbols, self.target_dates = parent.symbols, parent.target_dates
        # Two causal drawdown closes after early entries give actual closed
        # episodes in both exits, without changing any registered exit rule.
        for symbol in self.symbols:
            raw, previous = bytearray(), 1000
            for i, day in enumerate(parent.dates):
                ratio = .94 if 137 <= i <= 139 else 1.02 if i >= 110 else 1.
                closing = round(previous * ratio)
                raw.extend(struct.pack("<IIIIIfII", int(day.replace("-", "")), previous,
                    max(previous, closing)+1, min(previous, closing)-1, closing, 100000000., 10000000, 0))
                previous = closing
            (self.source / "vipdoc" / symbol[:2] / "lday" / (symbol + ".day")).write_bytes(raw)
        parent.data = parent.dataset()
        parent.doc["sample"].update(expectedFullSnapshotFingerprint=parent.data["fingerprint"],
            expectedFullBars=sum(len(s["bars"]) for s in parent.data["securities"]))
        parent.build_panel()
        # Independent saved calendar is bound separately from the original
        # observed-union snapshot; do not mutate the old source provenance.
        self.calendar_path = self.root / "synthetic-calendar.json"
        first, last = date.fromisoformat(parent.doc["sample"]["warmupStart"]), date.fromisoformat(parent.doc["sample"]["signalEnd"])
        natural = [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
        write(self.calendar_path, {"provider":"baostock", "api":"query_trade_dates", "errorCode":"0", "rowBoundReached":False,
            "parameters":{"start_date":first.isoformat(), "end_date":last.isoformat()},
            "fields":["calendar_date", "is_trading_day"], "rows":[[d, "1" if d in parent.data["calendar"] else "0"] for d in natural]})
        spec = {"path":"synthetic-calendar.json", "sha256":self.runner.sha(self.calendar_path), "provider":"baostock",
                "api":"query_trade_dates", "start":first.isoformat(), "end":last.isoformat()}
        parent.manifest["registration"] = {"calendar":spec}
        parent.manifest["inputs"]["calendar"].update(originalSource=parent.data["calendarSource"], savedProvider=spec,
            originalRestrictedDatesEqualSavedExchangeDates=True)
        write(self.panel / "manifest.json", parent.manifest)
        parent.manifest_hash = self.runner.sha(self.panel / "manifest.json")
        parent.audit["sampleManifestHash"] = parent.manifest_hash
        for row in parent.rows:
            row["sampleManifestHash"] = parent.manifest_hash
        parent.rebind()
        parent.run_complete()
        self.data = parent.data
        self.doc = self.runner.read_json(ROOT / REGISTRATION)
        self.doc["registeredAt"] = "synthetic-only"
        self.doc["sourceStudy"]["directory"] = "output"
        self.doc["sourceBinding"] = deepcopy(parent.doc["sourceBinding"])
        self.doc["sourceRegistration"] = {"path":"docs/signal-early-strength-emergence-v1.json", "sha256":parent.registration_hash}
        self.doc["sample"].update(startDate=self.target_dates[0], endDate=self.target_dates[-1],
            sourceSignalStart=parent.doc["sample"]["signalStart"], sourceSignalEnd=parent.doc["sample"]["signalEnd"],
            warmupStart=parent.doc["sample"]["warmupStart"], expectedSecurities=len(self.symbols),
            expectedBars=sum(len(s["bars"]) for s in self.data["securities"]), expectedStudySessions=len(self.target_dates),
            expectedSourceCalendarSessions=len(self.data["calendar"]), snapshotFingerprint=self.data["fingerprint"],
            fullDatasetContentSha256=self.runner.digest(self.data))
        self.doc["baseConfig"].update(startDate=self.target_dates[0], endDate=self.target_dates[-1])
        for policy in self.doc["policies"]:
            policy["config"].update(startDate=self.target_dates[0], endDate=self.target_dates[-1])
        self.registration = self.root / REGISTRATION
        self.registration.parent.mkdir(exist_ok=True)
        closure = set(self.doc["frozenImplementation"]) | set(self.doc["engineeringEvidence"]) | set(self.runner.NEW_FILES)
        for name in closure:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.rebind_source()

    def rebind_source(self):
        parent = self.parent_fixture
        protocol = self.runner.read_json(self.source_output / "protocol.json")
        completion_path = parent.attempt() / "completion.json"
        completion = self.runner.read_json(completion_path)
        for key in ("signals", "selections"):
            completion["files"][key]["sha256"] = self.runner.sha(self.source_output / completion["files"][key]["path"])
        write(completion_path, completion)
        audit = {"passed":True, "verificationCompleted":True, "failureCount":0,
            "protocolHash":protocol["protocolHash"], "completionSha256":self.runner.sha(completion_path),
            "registrationSha256":parent.registration_hash, "snapshotFingerprint":self.data["fingerprint"],
            "targetSessions":len(self.target_dates), "implementation":protocol["implementation"],
            "certified":False, "targetAchieved":False, "testOnly":True,
            "meaning":"synthetic upstream proof contract, not an actual market audit"}
        write(self.source_output / "independent-result-verification.json", audit)
        self.doc["sourceStudy"]["protocolHash"] = protocol["protocolHash"]
        self.doc["sourceStudy"]["files"] = {name:self.runner.sha(self.source_output / name)
            for name in self.doc["sourceStudy"]["files"]}
        source_rows = list(self.runner.records(parent.attempt() / "signals.jsonl.gz"))
        self.doc["sourceStudy"]["expectedSelectedOpportunities"] = sum(row["strategyId"] == self.runner.SOURCE_ID for row in source_rows)
        write(self.registration, self.doc)
        self.registration_hash = self.runner.sha(self.registration)
        self.prerequisites()

    def prerequisites(self):
        files = {name:self.runner.sha(self.root / name) for name in self.doc["reviewedFiles"]}
        scoped, review = (self.root / self.doc["runPrerequisites"][key] for key in ("scopedTests", "prerunReview"))
        scoped.parent.mkdir(parents=True, exist_ok=True)
        write(scoped, {"schema":"account-exit-comparison-scoped-tests-v1", "passed":True,
            "testsRun":1, "failures":0, "errors":0, "skipped":0, "uniqueTestIds":["synthetic.fixture.contract"],
            "files":files, "sourcesStableDuringTests":True, "certified":False, "targetAchieved":False, "testOnly":True})
        write(review, {"schema":"account-exit-comparison-prerun-review-v1", "passed":True, "failureCount":0,
            "scopedTestsSha256":self.runner.sha(scoped), "files":files, "certified":False, "targetAchieved":False, "testOnly":True})

    @contextmanager
    def registered(self):
        with self.parent_fixture.registered(), patch.object(self.runner, "ROOT", self.root), \
                patch.object(self.runner, "__file__", str(self.root / SOURCE)), \
                patch.object(self.runner, "source_runner", self.parent_fixture.runner), \
                patch.object(self.runner, "REGISTRATION", self.registration), \
                patch.object(self.runner, "REGISTRATION_SHA256", self.registration_hash):
            yield

    def args(self, **changes):
        return Namespace(**{"output":self.output, **changes})

    def freeze(self):
        with self.registered(), redirect_stdout(io.StringIO()):
            return self.runner.freeze(self.args())

    def execute(self, verify_only=False):
        with self.registered(), redirect_stdout(io.StringIO()):
            return self.runner.execute(self.args(), verify_only=verify_only)

    def run_complete(self):
        self.freeze()
        return self.execute()

    def attempt(self):
        return self.output / "attempts/attempt-0001"

    def result(self):
        return read_gzip(self.attempt() / "result.json.gz")

    def test_freeze_full_stream_readonly_exclusive_and_source_archive(self):
        original = self.runner.digest(self.data)
        with patch.object(self.runner, "run_accounts", side_effect=AssertionError("no account during freeze")):
            protocol = self.freeze()
        self.assertEqual(self.runner.digest(self.data), original)
        self.assertEqual(protocol["accountDataset"]["sourceDatasetDigest"], original)
        self.assertNotEqual(protocol["accountDataset"]["accountDatasetDigest"], original)
        self.assertFalse((self.output / "attempts").exists())
        self.assertGreater(protocol["sourceAccounting"]["opportunityCount"], 0)
        self.assertEqual(protocol["sourceAccounting"]["targetSessions"], 40)
        self.assertEqual(set(protocol["prerequisites"]), {"scopedTests", "prerunReview"})
        with zipfile.ZipFile(self.output / "execution-sources.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(protocol["implementation"]))
        with self.assertRaisesRegex(ValueError, "Exclusive freeze"):
            self.freeze()

    def test_complete_stream_not_prior_fills_three_independent_accounts(self):
        self.run_complete()
        result = self.result()
        opportunities = list(self.runner.records(self.attempt() / "opportunities.jsonl.gz"))
        self.assertEqual(len(opportunities), self.doc["sourceStudy"]["expectedSelectedOpportunities"])
        self.assertEqual({row["source"]["earlyMask"] for row in opportunities}, {"true", "false", "unknown"})
        self.assertEqual(result["policyOrder"], ["hold", "trail10", "stop5trail10"])
        for policy in self.runner.POLICY_IDS:
            account = read_gzip(self.attempt() / (policy + ".result.json.gz"))
            state = read_gzip(self.attempt() / (policy + ".checkpoint.json.gz"))
            self.assertEqual(account["status"], "completed")
            self.assertEqual(account["config"]["initialCapital"], 500000)
            self.assertEqual(account["research"]["identity"]["signalSchedule"], [r["projected"] for r in opportunities])
            self.assertEqual(account["trades"], state["engineState"]["trades"])
            self.assertEqual(len(account["curve"]), 40)
            self.assertGreater(len(account["trades"]), 0)
            self.assertFalse(account["certified"])
            self.assertEqual(account["dataDigest"], result["accountDataset"]["accountDatasetDigest"])
            if policy != "hold":
                self.assertGreater(len(account["closedTrades"]), 0)
        self.assertFalse(result["automaticWinnerSelection"])

    def test_success_verifies_only_and_corrupt_artifact_never_recomputed(self):
        self.run_complete()
        write(self.output / "independent-result-verification.json", {"testOnly":True, "passed":True})
        with patch.object(self.runner, "load_dataset", side_effect=AssertionError("no reload")), \
                patch.object(self.runner, "run_accounts", side_effect=AssertionError("no recompute")):
            self.assertEqual(self.execute(), 0)
            self.assertEqual(self.execute(verify_only=True), 0)
        path = self.attempt() / "trail10.result.json.gz"
        path.write_bytes(path.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.execute()

    def test_missing_or_duplicated_source_signal_caught_after_rehash(self):
        path = self.parent_fixture.attempt() / "signals.jsonl.gz"
        original = list(self.runner.records(path))
        for changed in (original[:-1], original + original[-1:]):
            write_lines(path, changed)
            self.rebind_source()
            with self.registered(), self.assertRaisesRegex(ValueError, "Signal stream|Trailing source"):
                self.runner.load_opportunities(self.runner.load_binding())
        write_lines(path, original)

    def test_selection_dates_slot_order_and_source_binding_caught(self):
        path = self.parent_fixture.attempt() / "selection.jsonl.gz"
        original = list(self.runner.records(path))
        variants = []
        missing = deepcopy(original); missing.pop(2); variants.append(missing)
        source = deepcopy(original); source[0]["source"]["targetIndex"] = 1; variants.append(source)
        duplicate = deepcopy(original); duplicate.append(duplicate[-1]); variants.append(duplicate)
        slot = deepcopy(original); first = next(row for row in slot if row["selection"]["slots"])
        first["selection"]["slots"][0]["rank"] = 4; variants.append(slot)
        for changed in variants:
            write_lines(path, changed)
            self.rebind_source()
            with self.registered(), self.assertRaises(ValueError):
                self.runner.load_opportunities(self.runner.load_binding())

    def test_prerequisite_and_failed_source_audit_refused(self):
        audit_path = self.source_output / "independent-result-verification.json"
        audit = self.runner.read_json(audit_path)
        audit["passed"] = False
        write(audit_path, audit)
        self.doc["sourceStudy"]["files"]["independent-result-verification.json"] = self.runner.sha(audit_path)
        write(self.registration, self.doc); self.registration_hash = self.runner.sha(self.registration); self.prerequisites()
        with self.assertRaisesRegex(ValueError, "independent audit"):
            self.freeze()
        self.rebind_source()
        scoped = self.root / self.doc["runPrerequisites"]["scopedTests"]
        proof = self.runner.read_json(scoped); proof["files"][SOURCE] = "0" * 64; write(scoped, proof)
        with self.assertRaisesRegex(ValueError, "synthetic prerequisite"):
            self.freeze()

    def test_failed_attempt_retained_no_automatic_retry(self):
        with self.assertRaisesRegex(ValueError, "Freeze"):
            self.execute()
        self.freeze()
        with self.assertRaisesRegex(ValueError, "No completed"):
            self.execute(verify_only=True)
        with patch.object(self.runner, "run_accounts", side_effect=RuntimeError("synthetic injected failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                self.execute()
        self.assertTrue((self.output / "failure.json").is_file())
        self.assertTrue((self.attempt() / "failure.json").is_file())
        self.assertFalse((self.attempt() / "completion.json").exists())
        with self.assertRaisesRegex(ValueError, "restart forbidden|retry"):
            self.execute()

    def test_code_archive_and_source_runtime_drift_rejected(self):
        self.freeze()
        archive = self.output / "execution-sources.zip"; original = archive.read_bytes()
        archive.write_bytes(original + b"tail")
        with self.assertRaisesRegex(ValueError, "archive drift"):
            self.execute()
        archive.write_bytes(original)
        run = self.runner.run_accounts
        def changed(*args):
            result = run(*args)
            path = self.source / "vipdoc/sh/lday/sh600001.day"
            path.write_bytes(path.read_bytes() + b"drift")
            return result
        with patch.object(self.runner, "run_accounts", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Physical source inventory"):
                self.execute()
        self.assertFalse((self.attempt() / "completion.json").exists())

    def test_protocol_drift_during_run_refused(self):
        self.freeze()
        run = self.runner.run_accounts
        def changed(*args):
            result = run(*args)
            path = self.output / "protocol.json"; protocol = self.runner.read_json(path)
            protocol["createdAt"] = "changed-after-freeze"
            protocol["protocolHash"] = self.runner.digest({k:v for k,v in protocol.items() if k != "protocolHash"})
            write(path, protocol)
            return result
        with patch.object(self.runner, "run_accounts", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Post-run source/protocol drift"):
                self.execute()

    def test_declared_action_guard_and_overlap_refuse_without_filtering(self):
        with self.registered():
            binding = self.runner.load_binding()
            changed = deepcopy(self.data)
            changed["securities"][0]["corporateActions"] = [{"id":"declared-unhandled"}]
            with patch.object(self.runner.source_runner, "load_dataset", return_value=changed), \
                    patch.object(self.runner.source_runner, "validate_dataset", return_value=None):
                with self.assertRaisesRegex(ValueError, "Declared action instructions"):
                    self.runner.load_dataset(binding)
            for output in (self.source, self.workspace / "nested", self.panel / "nested", self.source_output / "nested", self.root):
                with self.assertRaisesRegex(ValueError, "overlaps|contains"):
                    self.runner.freeze(self.args(output=output))


if __name__ == "__main__":
    unittest.main()
