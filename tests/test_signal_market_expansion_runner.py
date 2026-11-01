"""Temporary nonempty parent replay; no production panel or result reads.

The parent logs use real code over synthetic TDX bars. Market daily values are
hand-authored gate contracts with test-only audit receipts, not observations
or an audit of the actual registered market panel.
"""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import zipfile

import test_signal_persistent_leader_runner as parent_fixtures


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("market_expansion_runner_tests", ROOT / "scripts/run-signal-market-expansion.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
digest, file_hash = parent_fixtures.digest, parent_fixtures.file_hash
write_json, write_lines, lines = parent_fixtures.write_json, parent_fixtures.write_lines, parent_fixtures.lines


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ref(path):
    return {"path": str(Path(path).resolve()), "sha256": file_hash(path)}


class MarketExpansionRunnerTests(unittest.TestCase):
    def setUp(self):
        parent = self.parent = parent_fixtures.PersistentLeaderRunnerTests()

        def parent_args(**changes):
            values = dict(workspace=parent.workspace, output=parent.output, start=parent.dates[125], end=parent.dates[164],
                          max_symbols=None, diagnostic_unverified_history=True)
            values.update(changes)
            return Namespace(**values)

        parent.args = parent_args
        parent.setUp()
        self.addCleanup(parent.doCleanups)
        self.root, self.workspace, self.data, self.symbols = parent.root, parent.workspace, parent.data, parent.symbols
        self.output = self.root / "market-expansion-output"
        self.market = self.root / "synthetic-market"
        self.market.mkdir()
        self.registration = self.root / "synthetic-expansion-registration.json"
        self.target_dates = parent.dates[125:165]
        self.assertEqual(parent.execute(), 0)
        self.parent_protocol = read(parent.output / "protocol.json")
        self.parent_receipt = read(parent.attempt() / "completion.json")
        self.doc = read(ROOT / "docs/signal-market-expansion-price-proxy-v1.json")
        # Build a complete project-local byte closure so binding/path/archive
        # checks run normally, without access to production artifact paths.
        records = [self.doc["designSource"], self.doc["registrationGenerator"],
                   self.doc["parent"]["actualAuditVerifier"], self.doc["marketInput"]["registration"],
                   self.doc["marketInput"]["verifier"], *self.doc["sourceIntegrityPins"].values()]
        names = set(self.parent_protocol["implementation"]) | set(self.doc["marketInput"]["implementation"])
        names.update(r["path"] for r in records)
        names.update(("scripts/run-signal-market-expansion.py", "service/signal_market_expansion.py"))
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.doc["sample"] = deepcopy(parent.doc["sample"])
        self.doc["unchangedExecutionAndSample"].update(fixedSymbols=len(self.symbols),
            fixedRawBars=sum(len(s["bars"]) for s in self.data["securities"]), targetSessions=len(self.target_dates),
            signalStart=self.target_dates[0], signalEnd=self.target_dates[-1], snapshotFingerprint=self.data["fingerprint"])
        self.doc["frozenExecutionConfig"] = deepcopy(self.parent_protocol["config"])
        self.doc["executionConfigHash"] = digest(self.doc["frozenExecutionConfig"])
        self.doc["parent"].update(completedArtifactDirectory=str(parent.output), protocolHash=digest(self.parent_protocol),
            sourceImplementation=deepcopy(self.parent_protocol["implementation"]))
        self.doc["originalRawSourcePanelBinding"] = {
            "identity": deepcopy(self.parent_protocol["panelBinding"]),
            "files": {p.name: ref(p) for p in parent.panel.iterdir() if p.is_file()},
            "snapshot": ref(parent.output / "snapshot.json"), "sourceSelection": ref(parent.output / "selection.json"),
            "calendarHash": parent.manifest["inputs"]["calendar"]["sha256"],
            "symbolsSha256": digest(self.symbols), "targetDates": self.target_dates}
        self.parent_audit = {"passed": True, "failureCount": 0, "sourceFingerprint": self.data["fingerprint"],
            "evaluatedRows": len(self.symbols)*len(self.target_dates), "resultSha256": file_hash(parent.attempt()/"result.json.gz"),
            "verifierSha256": self.doc["parent"]["actualAuditVerifier"]["sha256"], "synthetic": True}
        write_json(parent.output / "independent-result-verification.json", self.parent_audit)
        self.market_protocol = {"schema": "market-state-observation-protocol-v1",
            "registrationSha256": self.doc["marketInput"]["registration"]["sha256"],
            "implementation": deepcopy(self.doc["marketInput"]["implementation"]),
            "inputManifestSha256": parent.manifest_hash,
            "inputFilesSha256": {p.name: file_hash(p) for p in parent.panel.iterdir() if p.is_file()},
            "calendarHash": parent.manifest["inputs"]["calendar"]["sha256"],
            "symbols": self.symbols, "dates": self.target_dates, "newStrategyTrials": 0,
            "returnsCalculated": False, "certified": False, "targetAchieved": False}
        with zipfile.ZipFile(self.market / "execution-sources.zip", "w") as archive:
            for name in self.market_protocol["implementation"]:
                archive.writestr(name, (ROOT/name).read_bytes())
        self.market_protocol["executionArchiveSha256"] = file_hash(self.market / "execution-sources.zip")
        self.market_protocol["protocolHash"] = digest(self.market_protocol)
        write_json(self.market / "protocol.json", self.market_protocol)
        write_json(self.market / "attempt.json", {"protocolHash": self.market_protocol["protocolHash"], "synthetic": True})
        write_lines(self.market / "roles.jsonl.gz", [])
        write_json(self.market / "summary.json", {"protocolHash": self.market_protocol["protocolHash"],
            "dailyRows": len(self.target_dates), "roleRows": len(self.target_dates)*len(self.symbols), "synthetic": True})
        self.market_rows = [{"date": day, "featureVersion": "market-state-observations-v1",
            "fixedPopulation": len(self.symbols), "inputManifestSha256": parent.manifest_hash,
            "inputFeatureFileSha256": file_hash(parent.panel / "features.jsonl.gz"),
            "calendarHash": self.market_protocol["calendarHash"], "protocolHash": self.market_protocol["protocolHash"],
            "price20": {"coverage": 1., "medianAbsoluteReturn20": .1},
            "matched5": {"available": index >= 5, "earlierDate": self.target_dates[index-5] if index >= 5 else None,
                "pairedCoverageFixed": 1. if index >= 5 else None, "matchedBreadthChange5": 10. if index >= 5 else None}}
            for index, day in enumerate(self.target_dates)]
        self.market_rows[6]["price20"]["medianAbsoluteReturn20"] = 0.  # observed false with parent signals
        self.market_rows[7]["price20"]["medianAbsoluteReturn20"] = None  # unknown with parent signals
        self.rebind()

    def rebind(self):
        """Re-register changed synthetic bytes only, before a new test freeze."""
        parent = self.parent
        receipt = read(parent.attempt() / "completion.json")
        for key, record in receipt["files"].items():
            record["sha256"] = file_hash(parent.output / record["path"])
        write_json(parent.attempt() / "completion.json", receipt)
        self.parent_audit["resultSha256"] = file_hash(parent.attempt() / "result.json.gz")
        write_json(parent.output / "independent-result-verification.json", self.parent_audit)
        self.doc["parent"]["completedInputs"] = {name: ref(parent.output / name) for name in
            ("protocol.json", "snapshot.json", "selection.json", "independent-result-verification.json")}
        self.doc["parent"]["completedInputs"]["completion.json"] = ref(parent.attempt()/"completion.json")
        self.doc["parent"]["completedOutputs"] = {key: ref(parent.output / record["path"]) for key, record in receipt["files"].items()}
        write_lines(self.market / "daily.jsonl.gz", self.market_rows)
        names = ("attempt.json", "daily.jsonl.gz", "execution-sources.zip", "protocol.json", "roles.jsonl.gz", "summary.json")
        completion = {"schema": "market-state-observation-completion-v1", "protocolHash": self.market_protocol["protocolHash"],
            "dailyRows": len(self.target_dates), "roleRows": len(self.target_dates)*len(self.symbols),
            "newStrategyTrials": 0, "returnsCalculated": False,
            "files": {name: {"path": name, "sha256": file_hash(self.market/name)} for name in names}}
        write_json(self.market / "completion.json", completion)
        write_json(self.market / "independent-verification.json", {"passed": True, "failureCount": 0,
            "protocolHash": self.market_protocol["protocolHash"], "dailyRows": len(self.target_dates),
            "roleRows": len(self.target_dates)*len(self.symbols), "inputManifestSha256": self.parent.manifest_hash,
            "verifierSha256": self.doc["marketInput"]["verifier"]["sha256"], "synthetic": True,
            "resultFileHashes": {name: file_hash(self.market / name) for name in names},
            "returnsCalculated": False, "newStrategyTrials": 0})
        self.doc["marketInput"].update(directory=str(self.market), protocolHash=self.market_protocol["protocolHash"],
            outputs={name: ref(self.market / name) for name in names}, completion=ref(self.market / "completion.json"),
            actualAudit=ref(self.market / "independent-verification.json"))
        write_json(self.registration, self.doc)

    def args(self, **changes):
        values = dict(workspace=self.workspace, output=self.output, diagnostic_unverified_history=True)
        values.update(changes)
        return Namespace(**values)

    @contextmanager
    def registered(self):
        with patch.object(runner, "ROOT", self.root), \
             patch.object(runner, "__file__", str(self.root / "scripts/run-signal-market-expansion.py")), \
             patch.object(runner, "REGISTRATION", self.registration), patch.object(runner, "REGISTRATION_SHA256", file_hash(self.registration)):
            yield

    def freeze(self):
        with self.registered(), redirect_stdout(io.StringIO()):
            return runner.freeze(self.args())

    def execute(self):
        with self.registered(), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.execute(self.args())

    def run_complete(self):
        self.freeze()
        return self.execute()

    def collect(self, destination=None):
        destination = destination or self.root / "collected"
        destination.mkdir()
        with self.registered():
            return runner.collect_signals(runner.load_binding(), destination)

    def test_binding_and_freeze_do_not_evaluate_market_or_candidates(self):
        with self.registered():
            binding = runner.load_binding()
        self.assertEqual(binding["dates"], self.target_dates)
        self.assertEqual(binding["symbols"], self.symbols)
        with patch.object(runner, "iter_market_days", side_effect=AssertionError("must not read market values while freezing")), \
             patch.object(runner, "iter_parent_days", side_effect=AssertionError("must not inspect candidate counts while freezing")):
            protocol = self.freeze()
        self.assertTrue(protocol)
        self.assertTrue((self.output / "execution-sources.zip").is_file())
        self.assertFalse((self.output / "attempts").exists())

    def test_parent_controls_false_topfive_and_all_market_states_retained(self):
        signals, performance = self.collect()
        self.assertTrue(signals)
        controls = [s for s in signals if s["strategyId"].startswith("control_")]
        self.assertTrue(controls)
        self.assertTrue(all(s["selectedTop5"] is False for s in controls))
        self.assertFalse(set(self.target_dates[:5]+self.target_dates[6:8]) & {s["signalDate"] for s in signals})
        destination = self.root / "collected"
        gates, decisions = lines(destination / "market-gates.jsonl.gz"), lines(destination / "filter-decisions.jsonl.gz")
        self.assertEqual([g["date"] for g in gates], self.target_dates)
        self.assertEqual([g["marketGate"] for g in gates[:8]], ["unknown"]*5+["true", "false", "unknown"])
        self.assertTrue(all(len(g["predicates"]) == 5 for g in gates))
        parent = lines(self.parent.attempt() / "signals.jsonl.gz")
        self.assertEqual([r["parentSignal"] for r in decisions], parent)
        self.assertEqual([r["sourceSignalKey"] for r in decisions],
                         [[r["strategyId"], r["symbol"], r["signalDate"]] for r in parent])
        self.assertEqual(len(decisions), len(parent))
        for new_group, count in performance["filterAccounting"].items():
            group_rows = [d for d in decisions if d["newStrategyId"] == new_group]
            self.assertEqual(count["parentSignals"], len(group_rows))
            self.assertEqual(count["parentSignals"], sum(count[k] for k in
                             ("marketTrueAdmitted", "marketFalseFiltered", "marketUnknownFiltered")))
            self.assertGreater(count["marketFalseFiltered"], 0)
            self.assertGreater(count["marketUnknownFiltered"], 0)
        for actual, decision in zip(signals, (d for d in decisions if d["admitted"])):
            self.assertEqual(actual["symbol"], decision["parentSignal"]["symbol"])
            self.assertEqual(actual["rank"], decision["parentSignal"]["rank"])
            self.assertEqual(actual["parentSignalSha256"], digest(decision["parentSignal"]))
            if actual["strategyId"].startswith("control_"):
                self.assertEqual(actual["pairedStrategyId"], self.doc["candidateId"])
        self.assertEqual(len(lines(destination / "selection.jsonl.gz")), len(self.target_dates))
        for original, emitted in zip(lines(self.parent.attempt()/"selection.jsonl.gz"), lines(destination/"selection.jsonl.gz")):
            self.assertEqual({k: emitted[k] for k in original}, original)
        self.assertEqual(len(lines(destination / "coverage.jsonl.gz")), len(self.target_dates))
        self.assertEqual(len(lines(destination / "candidates.jsonl.gz")), len(lines(self.parent.attempt()/"candidates.jsonl.gz")))

    def test_exact_prior_date_current_identity_and_stream_eof_fail_closed(self):
        original = deepcopy(self.market_rows)
        for change in (lambda r: r[5]["matched5"].update(earlierDate=self.target_dates[1]),
                       lambda r: r[0].update(date=self.target_dates[1]), lambda r: r.pop(),
                       lambda r: r.append(deepcopy(r[-1])), lambda r: r[0].update(featureVersion="wrong")):
            self.market_rows = deepcopy(original)
            change(self.market_rows)
            self.rebind()
            with self.registered(), self.assertRaises((ValueError, EOFError)):
                list(runner.iter_market_days(runner.load_binding()))

    def test_parent_signals_missing_duplicate_reordered_or_false_identity_refused(self):
        path = self.parent.attempt() / "signals.jsonl.gz"
        original = lines(path)
        self.assertGreater(len(original), 10)
        changes = (lambda r: r.pop(), lambda r: r.insert(1, deepcopy(r[0])),
                   lambda r: r.__setitem__(slice(0, 2), list(reversed(r[:2]))),
                   lambda r: r[0].update(strategyId="other"))
        for change in changes:
            records = deepcopy(original)
            change(records)
            write_lines(path, records)
            self.rebind()
            with self.registered(), self.assertRaises(ValueError):
                list(runner.iter_parent_days(runner.load_binding()))

    def test_fresh_active_target_release_unfilled_and_no_backfill(self):
        signals, _ = self.collect()
        config = self.doc["frozenExecutionConfig"]
        result = runner.evaluate_signals(self.data, config, signals)
        self.assertTrue(result["events"])
        self.assertTrue(result["overlapSuppressed"])
        for group in result["groups"]:
            selected = [s for s in signals if s["strategyId"] == group["strategyId"]]
            self.assertEqual(group["distinctSignalSymbols"], len({s["symbol"] for s in selected}))
            self.assertEqual(group["distinctSignalDates"], len({s["signalDate"] for s in selected}))
        strategy = self.doc["candidateId"]
        events = [e for e in result["events"] if e["strategyId"] == strategy]
        top = {"sh600006", "sh600007", "sh600005", "sh600004", "sh600003"}
        self.assertEqual({e["symbol"] for e in events}, top)
        first = events[0]
        self.assertEqual(first["entry"]["date"], self.parent.dates[131])
        self.assertEqual(first["entry"]["status"], "filled")
        exit_day = first["outcomes"]["nextOpenNet30"]["hitDate"]
        self.assertIsNotNone(exit_day)
        self.assertTrue(any(e["symbol"] == first["symbol"] and e["signalDate"] == exit_day for e in events))
        # Calling the fresh evaluator again cannot carry active state from this run.
        repeat = runner.evaluate_signals(self.data, config, signals)
        self.assertEqual(result["events"], repeat["events"])
        data = deepcopy(self.data)
        signal = next(s for s in signals if s["strategyId"] == strategy)
        bar = next(b for s in data["securities"] if s["symbol"] == signal["symbol"]
                   for b in s["bars"] if b["date"] == self.parent.dates[131])
        bar.update(open=14., high=14., low=14., close=14.)
        second = {**signal, "signalDate": self.parent.dates[131]}
        unfilled = runner.evaluate_signals(data, config, [signal, second])
        self.assertEqual(unfilled["overlapSuppressedCount"], 0)
        self.assertEqual([e["entry"]["status"] for e in unfilled["events"]], ["unfilled", "filled"])
        self.assertEqual(unfilled["events"][0]["entry"]["retryPolicy"], "noDeferredEntry")

    def test_success_only_verifies_and_input_hashes_remain_unchanged(self):
        before = {p: file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.run_complete(), 0)
        self.assertEqual(before, {p: file_hash(p) for p in before})
        with patch.object(runner, "collect_signals", side_effect=AssertionError("completed run must not score")), \
             patch.object(runner, "evaluate_signals", side_effect=AssertionError("completed run must not evaluate")):
            self.assertEqual(self.execute(), 0)
        attempt = self.output / "attempts/attempt-0001"
        receipt = read(attempt / "completion.json")
        self.assertEqual(set(receipt["files"]), runner.RECEIPT_KEYS)
        self.assertTrue({"marketGates", "filterDecisions", "selections", "executionSources", "protocol"} <= set(receipt["files"]))
        for entry in receipt["files"].values():
            self.assertEqual(file_hash(self.output/entry["path"]), entry["sha256"])
        summary = read(attempt / "summary.json")
        for count in summary["filterAccounting"].values():
            self.assertEqual(count["marketTrueAdmitted"], count["newAttemptedEvents"]+count["newActiveSuppressed"])
        with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
            result = json.load(stream)
        self.assertEqual(len(result["pathRiskEvents"]), sum(e["entry"]["status"] == "filled" for e in result["events"]))
        for group in result["groups"]:
            filled = [e for e in result["events"] if e["strategyId"] == group["strategyId"] and e["entry"]["status"] == "filled"]
            for horizon in group["horizons"]:
                self.assertEqual(horizon["completeCalendarCohort"],
                                 sum(e["observation"]["availableTradingDays"] >= horizon["tradingDays"] for e in filled))
        corrupt = attempt / "filter-decisions.jsonl.gz"
        corrupt.write_bytes(corrupt.read_bytes()+b"tampered")
        with patch.object(runner, "collect_signals", side_effect=AssertionError("corrupt success cannot recompute")), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual([p.name for p in (self.output/"attempts").iterdir()], ["attempt-0001"])

    def test_unfrozen_run_and_failed_attempt_refuse_automatic_retry(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.execute()
        self.freeze()
        with patch.object(runner, "evaluate_signals", side_effect=RuntimeError("synthetic terminal failure")), self.assertRaises(RuntimeError):
            self.execute()
        saved = {p: file_hash(p) for p in self.output.rglob("*") if p.is_file()}
        with patch.object(runner, "evaluate_signals", side_effect=AssertionError("must not retry")), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(saved, {p: file_hash(p) for p in saved})

    def test_zero_admitted_signals_preserves_all_filtered_rows_and_empty_groups(self):
        for row in self.market_rows[5:]:
            row["price20"]["medianAbsoluteReturn20"] = 0.
        self.rebind()
        self.assertEqual(self.run_complete(), 0)
        attempt = self.output / "attempts/attempt-0001"
        self.assertEqual(lines(attempt/"signals.jsonl.gz"), [])
        self.assertEqual(len(lines(attempt/"filter-decisions.jsonl.gz")), len(lines(self.parent.attempt()/"signals.jsonl.gz")))
        summary = read(attempt/"summary.json")
        self.assertEqual(len(summary["groups"]), 2)
        self.assertEqual(summary["eventCount"], 0)
        self.assertFalse(summary["targetAchieved"])
        for counts in summary["filterAccounting"].values():
            self.assertEqual(counts["marketTrueAdmitted"], 0)
            self.assertEqual(counts["newAttemptedEvents"], 0)

    def test_binding_rejects_failed_audit_bad_pin_and_physical_source_change(self):
        self.parent_audit["passed"] = False
        self.rebind()
        with self.registered(), self.assertRaises(ValueError):
            runner.load_binding()
        self.parent_audit["passed"] = True
        self.rebind()
        self.doc["marketInput"]["actualAudit"]["sha256"] = "0"*64
        write_json(self.registration, self.doc)
        with self.registered(), self.assertRaises(ValueError):
            runner.load_binding()
        self.rebind()
        path = self.parent.source / "vipdoc/sh/lday/sh600001.day"
        path.write_bytes(path.read_bytes()+b"unexpected physical source change")
        with self.registered(), self.assertRaises(ValueError):
            runner.load_binding()
        self.assertFalse(self.output.exists())

    def test_earlier_legal_raw_warmup_keeps_full_digest_and_exact_bound_calendar(self):
        with self.registered():
            binding = runner.load_binding()
        # A retained raw snapshot may start before the independently bound
        # panel's warmup calendar. Its entire content hash must still match.
        binding["document"]["sample"]["warmupStart"] = self.data["calendar"][2]
        inputs = binding["manifest"]["inputs"]
        inputs["calendar"]["dates"] = self.data["calendar"][2:]
        inputs["calendar"]["sha256"] = digest(inputs["calendar"]["dates"])
        runner.validate_dataset(self.data, binding)
        for change in (lambda d: d["securities"][0]["bars"][0].update(close=.01),
                       lambda d: d["calendar"].__setitem__(slice(0, 2), list(reversed(d["calendar"][:2]))),
                       lambda d: d["calendar"].pop(3), lambda d: d["calendar"].append("2099-01-01")):
            data = deepcopy(self.data)
            change(data)
            with self.assertRaises(ValueError):
                runner.validate_dataset(data, binding)
        for change in (lambda d: d["calendar"].pop(3), lambda d: d["calendar"].append("2099-01-01")):
            data = deepcopy(self.data)
            change(data)
            revised = deepcopy(binding)
            revised["manifest"]["inputs"]["fullDatasetContentSha256"] = digest(data)
            with self.assertRaises(ValueError):
                runner.validate_dataset(data, revised)

    def test_inflight_protocol_archive_code_input_or_dataset_drift_cannot_complete(self):
        actual_evaluate = runner.evaluate_signals
        for kind in ("protocol", "archive", "code", "input", "dataset"):
            self.output = self.root / ("drift-"+kind)
            self.freeze()
            paths = {"protocol": self.output/"protocol.json", "archive": self.output/"execution-sources.zip",
                     "code": self.root/"service/signal_market_expansion.py", "input": self.market/"daily.jsonl.gz"}
            changed = paths.get(kind)
            original = changed.read_bytes() if changed else None

            def evaluate(data, config, signals):
                result = actual_evaluate(data, config, signals)
                if changed:
                    changed.write_bytes(changed.read_bytes()+b" ")
                else:
                    data["securities"][0]["bars"][0]["close"] += .01
                return result

            try:
                with self.subTest(kind=kind), patch.object(runner, "evaluate_signals", side_effect=evaluate), self.assertRaises(ValueError):
                    self.execute()
                attempt = self.output / "attempts/attempt-0001"
                self.assertTrue((attempt / "failure.json").is_file())
                self.assertTrue((attempt / "filter-decisions.jsonl.gz").is_file())
                self.assertFalse((attempt / "completion.json").exists())
            finally:
                if changed:
                    changed.write_bytes(original)

    def test_cli_cannot_change_sample_or_silently_waive_diagnostic_flag(self):
        for flags in (["--start", "2022-02-01"], ["--end", "2023-12-30"], ["--max-symbols", "2"], ["--label", "future"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["--workspace", str(self.workspace), "--output", str(self.output), *flags])
        with self.registered(), self.assertRaises(ValueError):
            runner.freeze(self.args(diagnostic_unverified_history=False))
        with self.registered(), self.assertRaises(ValueError):
            runner.freeze(self.args(output=self.parent.output))
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
