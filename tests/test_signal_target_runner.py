"""Synthetic acceptance of signal enumeration, controls, admission and durable runs."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
import gzip
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_strategy_search_runner as fixtures

SPEC = importlib.util.spec_from_file_location("signal_target_runner", Path(__file__).resolve().parents[1] / "scripts/run-signal-target-study.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def read_lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class SignalTargetRunnerTests(unittest.TestCase):
    write_source = fixtures.StrategySearchRunnerTests.write_source

    def setUp(self):
        fixtures.StrategySearchRunnerTests.setUp(self)
        for number in range(3, 10):
            self.write_source(f"sh60000{number}")

    def args(self, **changes):
        values = dict(workspace=self.workspace, output=self.output, start=self.dates[130], end=self.dates[169],
                      max_symbols=None, diagnostic_unverified_history=True)
        values.update(changes)
        return Namespace(**values)

    def execute(self, args=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.execute(args or self.args())

    def attempt(self, number=1):
        return self.output / "attempts" / f"attempt-{number:04d}"

    def events(self):
        return [json.loads(line) for line in (self.output / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_one_scoring_pass_stable_top_five_and_same_day_hash_controls(self):
        symbols = [f"sh60000{i}" for i in range(1, 9)]
        days = self.dates[130:132]
        calls = []
        class Scorer:
            def __init__(self, dataset, config):
                calls.append("init")
            def daily(self, day):
                calls.append(day)
                rows = [{"symbol": symbol, "score": 1., "rawScore": 10.} for symbol in reversed(symbols[:6])]
                return {"strategies": {"momentum": rows, "trendBreakout": rows[:3], "trendPullback": []},
                        "eligibilityBySymbol": {s: {"eligible": True} for s in symbols}, "coverage": {}}
        self.output.mkdir()
        with patch.object(runner, "DailyScorer", Scorer):
            signals, performance = runner.collect_signals({"calendar": days}, runner.settings(self.args()), self.output)
        self.assertEqual(calls, ["init", *days])
        self.assertEqual(performance["scoredSessions"], 2)
        candidates = read_lines(self.output / "candidates.jsonl.gz")
        self.assertEqual(len(candidates), 18)
        first = [row for row in signals if row["strategyId"] == "momentum" and row["signalDate"] == days[0]]
        self.assertEqual([row["symbol"] for row in first], symbols[:5])
        controls = [row for row in signals if row["strategyId"] == "control_momentum" and row["signalDate"] == days[0]]
        self.assertEqual({row["symbol"] for row in controls}, set(symbols[5:]))
        matching = read_lines(self.output / "coverage.jsonl.gz")[0]["controlMatching"]["momentum"]
        self.assertEqual((matching["requestedCount"], matching["matchedCount"], matching["shortfall"]), (5, 3, 2))
        self.assertFalse(any(row["strategyId"] == "control_trendPullback" for row in signals))

    def test_active_events_release_on_target_open_and_missed_entries_do_not_roll(self):
        days = self.dates[130:134]
        calls = []
        class Evaluator:
            def __init__(self, *args, **kwargs):
                pass
            def evaluate(self, signal):
                calls.append((signal["strategyId"], signal["symbol"], signal["signalDate"]))
                missed = signal["symbol"] == "sh600002" and signal["signalDate"] == days[0]
                hit = days[2] if signal["signalDate"] == days[0] and not missed else None
                return {"entry": {"status": "unfilled" if missed else "filled"},
                        "outcomes": {"nextOpenNet30": {"hitDate": hit}}, "activeUntil": hit or days[-1]}
        signals = []
        for day in days:
            for symbol in ("sh600001", "sh600002"):
                signals.append({"strategyId": "momentum", "symbol": symbol, "signalDate": day})
        helper = SimpleNamespace(SignalTargetStudy=Evaluator, summarize_events=lambda rows, **kwargs: {"eventCount": len(rows)})
        with patch.dict(sys.modules, {"service.signal_target_study": helper}):
            result = runner.evaluate_signals({}, {}, signals)
        self.assertEqual(calls, [("momentum", "sh600001", days[0]), ("momentum", "sh600002", days[0]),
                                 ("momentum", "sh600002", days[1]), ("momentum", "sh600001", days[2])])
        self.assertEqual(result["overlapSuppressedCount"], 4)
        self.assertEqual([group["strategyId"] for group in result["groups"]], list(runner.GROUPS))
        self.assertTrue(all(group["targetAchieved"] is False for group in result["groups"]))

    def test_real_source_engine_helper_read_only_cutoff_and_no_annual_summary(self):
        before = {p: runner.source_tools.file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        actual_loader = runner.snapshot_universe
        def registered_load(*args, **kwargs):
            protocol = runner.source_tools.read_json(self.output / "protocol.json")
            self.assertEqual(protocol["targetNetReturnPct"], 30)
            self.assertIsNone(protocol["holdingDeadline"])
            return actual_loader(*args, **kwargs)
        with patch.object(runner, "snapshot_universe", side_effect=registered_load):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(before, {p: runner.source_tools.file_hash(p) for p in before})
        with gzip.open(self.attempt() / "result.json.gz", "rt", encoding="utf-8") as stream:
            result = json.load(stream)
        summary = runner.source_tools.read_json(self.attempt() / "summary.json")
        signals = read_lines(self.attempt() / "signals.jsonl.gz")
        self.assertGreater(len(signals), 0)
        self.assertEqual({r["strategyId"] for r in result["groups"]}, set(runner.GROUPS))
        self.assertGreater(result["overlapSuppressedCount"], 0)
        filled = [event for event in result["events"] if event["entry"]["status"] == "filled"]
        self.assertTrue(filled)
        self.assertTrue(all(event["entry"]["fee"] > 0 and event["entry"]["investedCapital"] <= 100000 for event in filled))
        self.assertFalse(result["certified"])
        self.assertFalse(result["targetAchieved"])
        self.assertNotIn("calendarCagrPct", summary)
        self.assertNotIn("events", summary)
        self.assertIsNone(summary["ranking"])
        self.assertTrue(all(row["signalDate"] <= self.dates[169] for row in signals))
        self.assertTrue(all(event["signalDate"] <= self.dates[169] for event in result["events"]))

    def test_successful_resume_ignores_future_bars_and_never_recomputes(self):
        self.assertEqual(self.execute(), 0)
        snapshot = (self.output / "snapshot.json").read_bytes()
        protocol = (self.output / "protocol.json").read_bytes()
        self.write_source("sh600001", changed_index=185)
        with patch.object(runner, "collect_signals", side_effect=AssertionError("successful study must not rerun")):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(snapshot, (self.output / "snapshot.json").read_bytes())
        self.assertEqual(protocol, (self.output / "protocol.json").read_bytes())
        self.assertEqual(sum(row["event"] == "study_started" for row in self.events()), 1)

    def test_changed_protocol_and_completed_artifact_refuse_rerun(self):
        self.assertEqual(self.execute(), 0)
        with self.assertRaisesRegex(ValueError, "Existing protocol differs"):
            self.execute(self.args(diagnostic_unverified_history=False))
        (self.attempt() / "signals.jsonl.gz").write_bytes(b"corrupt")
        with patch.object(runner, "collect_signals", side_effect=AssertionError("must not rerun")):
            with self.assertRaisesRegex(ValueError, "artifact missing or changed"):
                self.execute()

    def test_failed_attempt_is_preserved_and_retry_is_logged(self):
        with patch.object(runner, "evaluate_signals", side_effect=RuntimeError("synthetic helper interruption")):
            with self.assertRaisesRegex(RuntimeError, "synthetic helper interruption"):
                self.execute()
        self.assertTrue((self.attempt() / "signals.jsonl.gz").is_file())
        self.assertEqual(sum(row["event"] == "study_failed" for row in self.events()), 1)
        self.assertEqual(self.execute(), 0)
        self.assertTrue((self.attempt(2) / "completion.json").is_file())

    def test_development_cutoff_cannot_be_extended_to_unseen_2024(self):
        with self.assertRaisesRegex(ValueError, "restricted to 2022-01-01 through 2023-12-31"):
            self.execute(self.args(end="2024-01-02"))
        self.assertFalse(self.output.exists())

    def test_hash_pilot_never_backfills_a_failed_selected_security(self):
        selected = runner.source_tools.pilot_symbols(runner.source_tools.discover_symbols(self.workspace), 1)
        (self.source / "vipdoc" / selected[0][:2] / "lday" / (selected[0] + ".day")).write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "No securities observed"):
            self.execute(self.args(max_symbols=1))
        self.assertEqual(runner.source_tools.read_json(self.output / "selection.json")["symbols"], selected)
        self.assertFalse(any(row["event"] == "study_started" for row in self.events()))

    def test_strict_default_keeps_unknown_history_out_of_signal_pool(self):
        self.assertEqual(self.execute(self.args(diagnostic_unverified_history=False)), 0)
        self.assertEqual(read_lines(self.attempt() / "signals.jsonl.gz"), [])
        coverage = read_lines(self.attempt() / "coverage.jsonl.gz")
        self.assertTrue(all(row["strictHistory"] for row in coverage))
        self.assertGreater(coverage[0]["reasons"]["strictHistoryMissing"], 0)

    def test_completion_receipt_recovers_lost_log_without_recomputing(self):
        self.assertEqual(self.execute(), 0)
        rows = [row for row in self.events() if row["event"] != "study_succeeded"]
        (self.output / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        with patch.object(runner, "collect_signals", side_effect=AssertionError("durable result must not rerun")):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(sum(row.get("recovered", False) for row in self.events()), 1)


if __name__ == "__main__":
    unittest.main()
