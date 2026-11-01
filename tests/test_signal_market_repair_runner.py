"""Nonempty temporary repair replay; no production market/result reads.

The inherited fixture generates a real parent replay from synthetic TDX bars.
Market/role observations and their audit receipts are hand-authored test
contracts, not a claim about the registered production observations.
"""
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

import test_signal_market_expansion_runner as previous


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("market_repair_runner_tests", ROOT / "scripts/run-signal-market-repair.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
digest, file_hash = previous.digest, previous.file_hash
write_json, write_lines, lines = previous.write_json, previous.write_lines, previous.lines
read, ref = previous.read, previous.ref


class MarketRepairRunnerTests(unittest.TestCase):
    """Public fixture methods intentionally match the preceding runner tests."""

    def setUp(self):
        inherited = previous.MarketExpansionRunnerTests()
        inherited.setUp()
        self.addCleanup(inherited.doCleanups)
        for key in ("parent", "root", "workspace", "data", "symbols", "market", "target_dates",
                    "parent_protocol", "parent_receipt", "parent_audit", "market_protocol"):
            setattr(self, key, getattr(inherited, key))
        self.output = self.root / "market-repair-output"
        self.registration = self.root / "synthetic-repair-registration.json"
        self.doc = read(ROOT / "docs/signal-market-repair-resilience-price-proxy-v1.json")
        for key in ("sample", "unchangedExecutionAndSample", "frozenExecutionConfig", "executionConfigHash",
                    "parent", "originalRawSourcePanelBinding", "marketInput"):
            self.doc[key] = deepcopy(inherited.doc[key])
        records = [self.doc["designSource"], self.doc["registrationGenerator"],
                   *self.doc["sourceIntegrityPins"].values()]
        for record in records:
            destination = self.root / record["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            # The registration pins prior real readouts only as decision
            # provenance. These tests never read those production results.
            if record["path"].startswith("artifacts/") and "register_" not in record["path"]:
                write_json(destination, {"synthetic": True, "provenanceOnly": record["path"]})
                record["sha256"] = file_hash(destination)
            elif not destination.exists():
                shutil.copyfile(ROOT / record["path"], destination)
        for name in ("scripts/run-signal-market-repair.py", "service/signal_market_repair.py"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.market_rows = deepcopy(inherited.market_rows)
        for index, row in enumerate(self.market_rows):
            row["price20"]["medianAbsoluteReturn20"] = -.2  # no inherited positive-level gate
            row["matched5"]["matchedBreadthChange5"] = None if index < 5 else (-10. if index != 10 else 10.)
        top = {"sh600006", "sh600007", "sh600005", "sh600004", "sh600003"}
        raw = {(row["asOfDate"], row["symbol"]): row for row in self.parent.rows}
        self.role_rows = []
        for index, day in enumerate(self.target_dates):
            for symbol in self.symbols:
                source = raw[day, symbol]
                state = "true" if symbol in top else "false"
                if index == 9 and symbol == "sh600007":
                    state = "false"
                if index == 9 and symbol == "sh600005":
                    state = "unknown"
                self.role_rows.append({"date": day, "symbol": symbol,
                    "featureVersion": "market-state-observations-v1",
                    "qDefinition": "persistent-price-strength-five-gates-v1", "qState": state,
                    "unknownReasons": ["syntheticUnknown"] if state == "unknown" else [],
                    "failedPredicates": ["syntheticObservedFailure"] if state == "false" else [],
                    "criterionValues": {"synthetic": True}, "baseEligibility": deepcopy(source["baseEligibility"]),
                    "sourceHashes": deepcopy(source["sourceHashes"]), "sampleManifestHash": self.parent.manifest_hash,
                    "inputManifestSha256": self.parent.manifest_hash,
                    "inputFeatureFileSha256": file_hash(self.parent.panel / "features.jsonl.gz"),
                    "calendarHash": self.market_protocol["calendarHash"],
                    "protocolHash": self.market_protocol["protocolHash"],
                    "certified": False, "targetAchieved": False, "actionEvidence": "unverified"})
        self.rebind()

    def rebind(self):
        """Re-register deliberately changed synthetic bytes before test freeze."""
        write_lines(self.market / "roles.jsonl.gz", self.role_rows)
        previous.MarketExpansionRunnerTests.rebind(self)

    args = previous.MarketExpansionRunnerTests.args

    @contextmanager
    def registered(self):
        with patch.object(runner, "ROOT", self.root), \
             patch.object(runner, "__file__", str(self.root / "scripts/run-signal-market-repair.py")), \
             patch.object(runner, "REGISTRATION", self.registration), \
             patch.object(runner, "REGISTRATION_SHA256", file_hash(self.registration)):
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

    def test_freeze_binds_without_evaluating_any_stream(self):
        with self.registered():
            binding = runner.load_binding()
        self.assertEqual(binding["dates"], self.target_dates)
        self.assertEqual(binding["symbols"], self.symbols)
        with patch.object(runner, "iter_market_days", side_effect=AssertionError("freeze must not inspect market")), \
             patch.object(runner, "iter_role_days", side_effect=AssertionError("freeze must not inspect roles")), \
             patch.object(runner, "iter_parent_days", side_effect=AssertionError("freeze must not inspect candidates")):
            self.assertTrue(self.freeze())
        self.assertTrue((self.output / "execution-sources.zip").is_file())
        self.assertFalse((self.output / "attempts").exists())

    def test_warmup_full_candidate_logs_and_rank_mirrored_controls(self):
        signals, performance = self.collect()
        self.assertTrue(signals)
        destination = self.root / "collected"
        gates = lines(destination / "market-gates.jsonl.gz")
        self.assertEqual([row["date"] for row in gates], self.target_dates)
        self.assertEqual([row["marketGate"] for row in gates[:12]], ["unknown"]*10+["true", "false"])
        self.assertEqual(performance["marketGateCounts"], {"true": 1, "false": 29, "unknown": 10})
        self.assertEqual({s["signalDate"] for s in signals}, {self.target_dates[10]})
        strategy = [s for s in signals if s["strategyId"] == self.doc["candidateId"]]
        controls = [s for s in signals if s["strategyId"].startswith("control_")]
        self.assertEqual([s["rank"] for s in strategy], [1, 4, 5])
        self.assertEqual([s["rank"] for s in controls], [1, 4])
        self.assertTrue(all(s["selectedTop5"] is False for s in controls))
        self.assertTrue(all(next(r for r in self.role_rows if r["date"] == self.target_dates[9]
                                and r["symbol"] == s["symbol"])["qState"] == "false" for s in controls))
        parent_rows = lines(self.parent.attempt() / "signals.jsonl.gz")
        decisions = lines(destination / "filter-decisions.jsonl.gz")
        self.assertEqual([d["parentSignal"] for d in decisions], parent_rows)
        self.assertEqual(len(decisions), len(parent_rows))
        by_key = {tuple(d["sourceSignalKey"]): d for d in decisions}
        for row in decisions:
            parent = row["parentSignal"]
            self.assertEqual(row["sourceSignalKey"], [parent["strategyId"], parent["symbol"], parent["signalDate"]])
            self.assertEqual(row["pairedParentRank"], parent["rank"])
            paired = by_key[tuple(row["pairedParentSignalKey"])]
            self.assertEqual(row["strategyGate"], paired["strategyGate"])
            self.assertEqual(row["strategyGateSha256"], digest(row["strategyGate"]))
            self.assertEqual(row["admitted"], paired["admitted"])
            self.assertEqual(row["strategyGate"]["symbol"], paired["parentSignal"]["symbol"])
        originals = lines(self.parent.attempt() / "candidates.jsonl.gz")
        candidates = lines(destination / "candidates.jsonl.gz")
        self.assertEqual(len(candidates), len(originals))
        for original, actual in zip(originals, candidates):
            self.assertEqual({k: actual[k] for k in original}, original)
            self.assertEqual(actual["compoundGateSha256"], digest(actual["compoundGate"]))
        states = [row["signalGate"] for row in candidates]
        self.assertEqual(performance["candidateGateCounts"], {state: states.count(state) for state in ("true", "false", "unknown")})
        self.assertEqual(performance["unmatchedTrueStrategySlots"], 1)
        for original, actual in zip(lines(self.parent.attempt()/"selection.jsonl.gz"), lines(destination/"selection.jsonl.gz")):
            self.assertEqual({k: actual[k] for k in original}, original)
        for new_group, counts in performance["filterAccounting"].items():
            actual = [d for d in decisions if d["newStrategyId"] == new_group]
            self.assertEqual(counts["parentSignals"], len(actual))
            self.assertEqual(counts["parentSignals"], sum(counts[k] for k in
                             ("gateTrueAdmitted", "gateFalseFiltered", "gateUnknownFiltered")))

    def test_role_population_identity_q_and_eof_fail_closed(self):
        original = deepcopy(self.role_rows)
        changes = {
            "missing": lambda r: r.pop(), "extra": lambda r: r.append(deepcopy(r[-1])),
            "duplicate": lambda r: r.__setitem__(1, deepcopy(r[0])),
            "reordered": lambda r: r.__setitem__(slice(0, 2), list(reversed(r[:2]))),
            "date": lambda r: r[0].update(date=self.target_dates[1]),
            "symbol": lambda r: r[0].update(symbol="sh999999"),
            "version": lambda r: r[0].update(featureVersion="different"),
            "qDefinition": lambda r: r[0].update(qDefinition="different"),
            "qEnum": lambda r: r[0].update(qState=True),
            "manifest": lambda r: r[0].update(inputManifestSha256="0"*64),
            "source": lambda r: r[0]["sourceHashes"].update(retainedRawBarsSha256="0"*64),
            "protocol": lambda r: r[0].update(protocolHash="0"*64),
        }
        for label, change in changes.items():
            self.role_rows = deepcopy(original)
            change(self.role_rows)
            self.rebind()
            with self.subTest(label=label), self.registered(), self.assertRaises((ValueError, EOFError)):
                list(runner.iter_role_days(runner.load_binding()))

    def test_market_exact_prior_date_and_full_parent_stream_order(self):
        original = deepcopy(self.market_rows)
        for change in (lambda r: r[5]["matched5"].update(earlierDate=self.target_dates[1]),
                       lambda r: r[0].update(date=self.target_dates[1]), lambda r: r.pop(),
                       lambda r: r.append(deepcopy(r[-1]))):
            self.market_rows = deepcopy(original)
            change(self.market_rows)
            self.rebind()
            with self.registered(), self.assertRaises((ValueError, EOFError)):
                list(runner.iter_market_days(runner.load_binding()))
        self.market_rows = original
        path = self.parent.attempt() / "signals.jsonl.gz"
        original_signals = lines(path)
        for change in (lambda r: r.pop(), lambda r: r.insert(1, deepcopy(r[0])),
                       lambda r: r.__setitem__(slice(0, 2), list(reversed(r[:2]))),
                       lambda r: r[0].update(strategyId="other")):
            rows = deepcopy(original_signals)
            change(rows)
            write_lines(path, rows)
            self.rebind()
            with self.registered(), self.assertRaises(ValueError):
                list(runner.iter_parent_days(runner.load_binding()))

    def test_all_role_rows_are_validated_before_event_evaluation(self):
        self.role_rows.append(deepcopy(self.role_rows[-1]))
        self.rebind()
        self.freeze()
        with patch.object(runner, "evaluate_signals", side_effect=AssertionError("must reject roles before outcomes")), \
             self.assertRaises(ValueError):
            self.execute()
        attempt = self.output / "attempts/attempt-0001"
        self.assertTrue((attempt / "failure.json").is_file())
        self.assertFalse((attempt / "completion.json").exists())

    def test_fresh_active_release_unfilled_and_rank_six_never_backfilled(self):
        signals, _ = self.collect()
        self.assertTrue(signals)
        strategy = self.doc["candidateId"]
        selected = [s for s in signals if s["strategyId"] == strategy]
        self.assertEqual([s["rank"] for s in selected], [1, 4, 5])
        first, second, third = self.target_dates[10], self.target_dates[11], self.target_dates[14]
        # Keep original admitted rank holes, and replay later synthetic closes
        # solely to exercise the unchanged event helper's active lifecycle.
        replay = [dict(s, signalDate=d) for d in (first, second, third) for s in selected]
        data = deepcopy(self.data)
        for security in data["securities"]:
            if security["symbol"] not in {s["symbol"] for s in selected}:
                continue
            for bar in security["bars"]:
                price = {self.target_dates[12]: 18.1, self.target_dates[13]: 19.9}.get(bar["date"])
                if bar["date"] >= third:
                    price = 21.8
                if price is not None:
                    bar.update(open=price, high=price, low=price, close=price)
        result = runner.evaluate_signals(data, self.doc["frozenExecutionConfig"], replay)
        self.assertEqual(result["overlapSuppressedCount"], 3)
        events = result["events"]
        self.assertEqual({e["symbol"] for e in events}, {s["symbol"] for s in selected})
        self.assertTrue(all(e["signalDate"] != second for e in events))
        initial = next(e for e in events if e["signalDate"] == first)
        self.assertEqual(initial["entry"]["date"], second)
        self.assertEqual(initial["outcomes"]["nextOpenNet30"]["hitDate"], third)
        self.assertTrue(any(e["symbol"] == initial["symbol"] and e["signalDate"] == third for e in events))
        again = runner.evaluate_signals(data, self.doc["frozenExecutionConfig"], replay)
        self.assertEqual(events, again["events"])
        data = deepcopy(self.data)
        signal = selected[0]
        bar = next(b for s in data["securities"] if s["symbol"] == signal["symbol"]
                   for b in s["bars"] if b["date"] == second)
        bar.update(open=20., high=20., low=20., close=20.)
        following = next(b for s in data["securities"] if s["symbol"] == signal["symbol"]
                         for b in s["bars"] if b["date"] == self.target_dates[12])
        following.update(open=19., high=19., low=19., close=19.)
        unfilled = runner.evaluate_signals(data, self.doc["frozenExecutionConfig"],
            [signal, {**signal, "signalDate": second}])
        self.assertEqual(unfilled["overlapSuppressedCount"], 0)
        self.assertEqual([e["entry"]["status"] for e in unfilled["events"]], ["unfilled", "filled"])
        self.assertEqual(unfilled["events"][0]["entry"]["retryPolicy"], "noDeferredEntry")

    def test_success_is_read_only_full_receipt_and_verify_only_reuse(self):
        before = {p: file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.run_complete(), 0)
        self.assertEqual(before, {p: file_hash(p) for p in before})
        with patch.object(runner, "collect_signals", side_effect=AssertionError("no rescore")), \
             patch.object(runner, "evaluate_signals", side_effect=AssertionError("no outcome replay")):
            self.assertEqual(self.execute(), 0)
        attempt = self.output / "attempts/attempt-0001"
        receipt = read(attempt / "completion.json")
        self.assertEqual(set(receipt["files"]), runner.RECEIPT_KEYS)
        self.assertTrue({"marketGates", "filterDecisions", "selections", "executionSources", "protocol"} <= set(receipt["files"]))
        for record in receipt["files"].values():
            self.assertEqual(file_hash(self.output / record["path"]), record["sha256"])
        summary = read(attempt / "summary.json")
        for counts in summary["filterAccounting"].values():
            self.assertEqual(counts["gateTrueAdmitted"], counts["newAttemptedEvents"]+counts["newActiveSuppressed"])
        with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
            result = json.load(stream)
        self.assertEqual(len(result["pathRiskEvents"]), sum(e["entry"]["status"] == "filled" for e in result["events"]))
        for group in result["groups"]:
            filled = [e for e in result["events"] if e["strategyId"] == group["strategyId"] and e["entry"]["status"] == "filled"]
            for horizon in group["horizons"]:
                self.assertEqual(horizon["completeCalendarCohort"],
                                 sum(e["observation"]["availableTradingDays"] >= horizon["tradingDays"] for e in filled))
        damaged = attempt / "filter-decisions.jsonl.gz"
        damaged.write_bytes(damaged.read_bytes()+b"tampered")
        with patch.object(runner, "collect_signals", side_effect=AssertionError("no replacement success")), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual([p.name for p in (self.output / "attempts").iterdir()], ["attempt-0001"])

    def test_unfrozen_and_failed_runs_do_not_automatically_retry(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.execute()
        self.freeze()
        with patch.object(runner, "evaluate_signals", side_effect=RuntimeError("synthetic terminal failure")), self.assertRaises(RuntimeError):
            self.execute()
        saved = {p: file_hash(p) for p in self.output.rglob("*") if p.is_file()}
        with patch.object(runner, "evaluate_signals", side_effect=AssertionError("must not retry")), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(saved, {p: file_hash(p) for p in saved})

    def test_zero_admission_keeps_all_original_rows_and_empty_groups(self):
        self.market_rows[10]["matched5"]["matchedBreadthChange5"] = 0.
        self.rebind()
        self.assertEqual(self.run_complete(), 0)
        attempt = self.output / "attempts/attempt-0001"
        self.assertEqual(lines(attempt / "signals.jsonl.gz"), [])
        self.assertEqual(len(lines(attempt / "filter-decisions.jsonl.gz")), len(lines(self.parent.attempt()/"signals.jsonl.gz")))
        self.assertEqual(len(lines(attempt / "candidates.jsonl.gz")), len(lines(self.parent.attempt()/"candidates.jsonl.gz")))
        summary = read(attempt / "summary.json")
        self.assertEqual(len(summary["groups"]), 2)
        self.assertEqual(summary["eventCount"], 0)
        self.assertFalse(summary["targetAchieved"])

    def test_source_audit_and_inflight_archive_input_code_drift_refused(self):
        self.parent_audit["passed"] = False
        self.rebind()
        with self.registered(), self.assertRaises(ValueError):
            runner.load_binding()
        self.parent_audit["passed"] = True
        self.rebind()
        actual = runner.evaluate_signals
        for kind in ("protocol", "archive", "code", "input", "dataset"):
            self.output = self.root / ("drift-"+kind)
            self.freeze()
            paths = {"protocol": self.output/"protocol.json", "archive": self.output/"execution-sources.zip",
                     "code": self.root/"service/signal_market_repair.py", "input": self.market/"roles.jsonl.gz"}
            changed = paths.get(kind)
            original = changed.read_bytes() if changed else None

            def evaluate(data, config, signals):
                result = actual(data, config, signals)
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
                self.assertFalse((attempt / "completion.json").exists())
            finally:
                if changed:
                    changed.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
