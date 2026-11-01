"""Synthetic raw-sequence runner, immutable completion and causal-source checks."""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import test_strategy_search_runner as fixtures

SPEC = importlib.util.spec_from_file_location("raw_sequence_runner", Path(__file__).resolve().parents[1] / "scripts/run-signal-sequence-price-proxy.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
REAL_REGISTRATION = runner.registration


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(row) for row in stream]


class PriceSequenceRunnerTests(unittest.TestCase):
    write_source = fixtures.StrategySearchRunnerTests.write_source

    def setUp(self):
        fixtures.StrategySearchRunnerTests.setUp(self)
        self.symbols = [f"sh60000{i}" for i in range(1, 9)] + ["sz000001"]
        for symbol in self.symbols:
            self.write_pattern(symbol, pattern=symbol in self.symbols[:7])

    def write_pattern(self, symbol, *, pattern=True, future_change=False):
        result = bytearray()
        for i, day in enumerate(self.dates):
            o = h = l = c = 1000
            volume = 10_000_000
            if pattern:
                if i == 130:
                    o, h, l, c, volume = 1040, 1060, 1040, 1050, 20_000_000
                elif i == 131:
                    o, h, l, c = 1020, 1030, 1010, 1020
                elif i == 132:
                    o, h, l, c, volume = 1040, 1080, 1020, 1050 + int(symbol[-1]), 20_000_000
                elif i >= 133:
                    c = (1060, 1150, 1240, 1340)[i - 133] if i < 137 else 1400
                    o = h = l = c
            if future_change and i == 185:
                o = h = l = c = 5000
            result.extend(struct.pack("<IIIIIfII", int(day.replace("-", "")), o, h, l, c,
                                      c / 100 * volume, volume, 0))
        (self.source / "vipdoc" / symbol[:2] / "lday" / (symbol + ".day")).write_bytes(result)

    def args(self, **changes):
        result = dict(workspace=self.workspace, output=self.output, start=self.dates[130], end=self.dates[169],
                      max_symbols=None, diagnostic_unverified_history=True)
        result.update(changes)
        return Namespace(**result)

    def dataset(self, args=None):
        args = args or self.args()
        data = runner.runtime.snapshot_universe(self.workspace, {"mode": "all", "source": "local", "markets": ["sh", "sz"],
            "boards": ["main"], "benchmarkSymbol": "sh000300"}, args.end, args.start)
        return runner.source_tools.bound_snapshot(data, args.start, args.end)

    @contextmanager
    def registered(self, args=None, data=None):
        args = args or self.args()
        data = data or self.dataset(args)
        doc = deepcopy(REAL_REGISTRATION())
        doc["sample"].update(signalStart=args.start, signalEnd=args.end,
                              expectedFullSnapshotFingerprint=data["fingerprint"], expectedFullSecurities=len(data["securities"]))
        with patch.object(runner, "registration", return_value=doc):
            yield data

    def execute(self, args=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.execute(args or self.args())

    def attempt(self, number=1):
        return self.output / "attempts" / f"attempt-{number:04d}"

    def test_full_cross_section_raw_facts_top_five_controls_and_input_immutable(self):
        self.output.mkdir()
        with self.registered() as data:
            original = deepcopy(data)
            signals, performance = runner.collect_signals(data, runner.settings(self.args()), self.output)
        self.assertEqual(data, original)
        candidates = lines(self.output / "candidates.jsonl.gz")
        self.assertEqual(len(candidates), 7)
        self.assertEqual([c["symbol"] for c in candidates], self.symbols[:7])
        selected = [s for s in signals if s["strategyId"] == runner.CANDIDATE_ID]
        self.assertEqual([s["symbol"] for s in selected], self.symbols[:5])
        self.assertTrue(all(s["score"] == -s["distanceFromAnchor"] == s["rawScore"] for s in selected))
        self.assertTrue(all(set(s["formationFacts"]) == {"B", "R", "T"} for s in candidates))
        self.assertTrue(all(set(s["formation"]) == {"B", "R", "T"} for s in candidates))
        controls = [s for s in signals if s["strategyId"].startswith("control_")]
        self.assertEqual({s["symbol"] for s in controls}, set(self.symbols[5:]))
        self.assertEqual(performance["candidateCounts"][runner.CANDIDATE_ID], 7)
        self.assertEqual(performance["sequenceSteps"], 40 * len(self.symbols))
        self.assertEqual(performance["sequenceSteps"], performance["transitionsWritten"] + performance["transitionsOmitted"])
        for day in lines(self.output / "coverage.jsonl.gz"):
            self.assertEqual(sum(day["transitionCounts"].values()), len(self.symbols))
            self.assertEqual(day["sequenceSteps"], day["transitionsWritten"] + day["transitionsOmitted"])
        first = lines(self.output / "transitions.jsonl.gz")[0]
        self.assertEqual(first["date"], self.args().start)
        self.assertEqual(first["before"], {"name": "idle"})

    def test_reference_adjusted_series_are_not_used_and_future_does_not_change_signals(self):
        data = self.dataset()
        security = data["securities"][0]
        rows = security["bars"]
        day = self.dates[100]
        security["precloseByDate"] = {day: 5.}
        scorer = runner.DailyScorer(data, runner.settings(self.args()), pools_only=True)
        series = scorer.series[security["symbol"]]
        self.assertNotEqual(series.signal_bars[-1]["close"], series.bars[-1]["close"])
        original = deepcopy(data)
        self.output.mkdir()
        with self.registered(data=data):
            first, _ = runner.collect_signals(data, runner.settings(self.args()), self.output)
        self.assertEqual(data, original)
        self.assertEqual(next(s for s in first if s["strategyId"] == runner.CANDIDATE_ID)["anchor"], 10.)
        future = deepcopy(data)
        for security in future["securities"]:
            security["bars"].append({"date": self.dates[185], "open": 999., "high": 999., "low": 999., "close": 999., "volume": 1., "amount": 999.})
        destination = self.root / "future-output"
        destination.mkdir()
        with self.registered(data=future):
            second, _ = runner.collect_signals(future, runner.settings(self.args()), destination)
        self.assertEqual(first, second)

    def test_every_symbol_advances_on_missing_and_known_halt(self):
        data = self.dataset()
        missing, halted, zero = data["securities"][:3]
        day = self.dates[131]
        missing["bars"] = [bar for bar in missing["bars"] if bar["date"] != day]
        halted["tradeStatusByDate"] = {day: False}
        next(bar for bar in halted["bars"] if bar["date"] == day)["volume"] = 0
        next(bar for bar in zero["bars"] if bar["date"] == day)["volume"] = 0
        self.output.mkdir()
        with self.registered(data=data):
            runner.collect_signals(data, runner.settings(self.args()), self.output)
        transitions = {(row["symbol"], row["date"]): row for row in lines(self.output / "transitions.jsonl.gz")}
        self.assertEqual(transitions[missing["symbol"], day]["kind"], "abortedUnknown")
        self.assertEqual(transitions[zero["symbol"], day]["kind"], "abortedUnknown")
        self.assertEqual(transitions[halted["symbol"], day]["kind"], "held")
        self.assertTrue(transitions[halted["symbol"], day]["knownSuspended"])

    def test_warmup_does_not_carry_prestart_pattern_and_pending_is_saved(self):
        args = self.args(start=self.dates[131], end=self.dates[132])
        self.output.mkdir()
        with self.registered(args) as data:
            signals, performance = runner.collect_signals(data, runner.settings(args), self.output)
        self.assertEqual(signals, [])
        self.assertEqual(performance["pendingAtCutoff"], 0)
        end_args = self.args(end=self.dates[130])
        another = self.root / "pending-output"
        another.mkdir()
        with self.registered(end_args) as data:
            _, performance = runner.collect_signals(data, runner.settings(end_args), another)
        self.assertEqual(performance["pendingAtCutoff"], 7)
        final = lines(another / "final-state.jsonl.gz")
        self.assertEqual(sum(x["pendingAtCutoff"] for x in final), 7)
        self.assertTrue(all(x["formation"]["B"] for x in final if x["pendingAtCutoff"]))

    def test_real_helper_fees_t_plus_one_path_risk_and_no_active_backfill(self):
        self.output.mkdir()
        with self.registered() as data:
            signals, _ = runner.collect_signals(data, runner.settings(self.args()), self.output)
        selected = [s for s in signals if s["strategyId"] == runner.CANDIDATE_ID]
        duplicates = [{**s, "signalDate": self.dates[133]} for s in selected]
        result = runner.evaluate_signals(data, runner.settings(self.args()), [*signals, *duplicates])
        candidate_events = [e for e in result["events"] if e["strategyId"] == runner.CANDIDATE_ID]
        self.assertEqual(len(candidate_events), 5)
        self.assertEqual(result["overlapSuppressedCount"], 5)
        self.assertEqual({e["symbol"] for e in candidate_events}, set(self.symbols[:5]))
        self.assertTrue(all(e["entry"]["status"] == "filled" and e["entry"]["date"] == self.dates[133] and e["entry"]["fee"] > 0 for e in candidate_events))
        self.assertTrue(all(e["outcomes"]["nextOpenNet30"]["hitDate"] > e["entry"]["date"] for e in candidate_events))
        self.assertEqual(len(result["pathRiskEvents"]), sum(e["entry"]["status"] == "filled" for e in result["events"]))
        self.assertEqual({o["horizonSessions"] for g in result["pathRiskSummary"]["groups"] for o in g["observations"]}, {None, *runner.runtime.HORIZONS})
        self.assertFalse(result["pathRiskSummary"]["certified"])

    def test_lifecycle_read_only_all_seven_hashes_and_compact_summary(self):
        before = {path: runner.source_tools.file_hash(path) for path in self.root.rglob("*") if path.is_file()}
        with self.registered():
            self.assertEqual(self.execute(), 0)
        self.assertEqual(before, {path: runner.source_tools.file_hash(path) for path in before})
        receipt = runner.source_tools.read_json(self.attempt() / "completion.json")
        self.assertEqual(set(receipt["files"]), {"candidates", "coverage", "signals", "result", "summary", "transitions", "finalStates"})
        summary = runner.source_tools.read_json(self.attempt() / "summary.json")
        self.assertNotIn("pathRiskEvents", summary)
        self.assertIn("pathRiskSummary", summary)
        self.assertFalse(summary["certified"])
        self.assertFalse(summary["targetAchieved"])

    def test_success_resume_recovers_log_without_recomputing_or_future_leak(self):
        with self.registered():
            self.assertEqual(self.execute(), 0)
            self.write_pattern(self.symbols[0], future_change=True)
            log = self.output / "events.jsonl"
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            log.write_text("".join(json.dumps(r) + "\n" for r in rows if r["event"] != "study_succeeded"), encoding="utf-8")
            with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("Do not rerun successful study")):
                self.assertEqual(self.execute(), 0)
        self.assertEqual(len(list((self.output / "attempts").iterdir())), 1)

    def test_success_extra_log_corruption_and_manifest_omission_refuse_rerun(self):
        with self.registered():
            self.assertEqual(self.execute(), 0)
            receipt = runner.source_tools.read_json(self.attempt() / "completion.json")
            missing = deepcopy(receipt)
            del missing["files"]["finalStates"]
            with self.assertRaisesRegex(ValueError, "incomplete"):
                runner.verify_completion(self.output, missing, receipt["protocolHash"], receipt["snapshotFingerprint"])
            (self.attempt() / "transitions.jsonl.gz").write_bytes(b"changed")
            with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("must not rerun")):
                with self.assertRaisesRegex(ValueError, "artifact missing or changed"):
                    self.execute()

    def test_failed_attempt_keeps_audit_logs_and_retry_uses_new_directory(self):
        with self.registered():
            with patch.object(runner.runtime, "evaluate_signals", side_effect=RuntimeError("synthetic failure")):
                with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                    self.execute()
            self.assertTrue((self.attempt() / "transitions.jsonl.gz").exists())
            self.assertFalse((self.attempt() / "completion.json").exists())
            self.assertEqual(self.execute(), 0)
        self.assertTrue((self.attempt(2) / "completion.json").exists())

    def test_production_window_sample_flag_and_snapshot_cannot_be_relaxed(self):
        args = self.args(start="2022-01-01", end="2023-12-31", diagnostic_unverified_history=False)
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.execute(args)
        args.diagnostic_unverified_history = True
        args.max_symbols = 64
        with self.assertRaisesRegex(ValueError, "max-symbols"):
            self.execute(args)
        with self.assertRaisesRegex(ValueError, "preregistered signal window"):
            self.execute(self.args())
        self.assertFalse(self.output.exists())
        with self.registered() as data:
            data["fingerprint"] = "different"
            with self.assertRaisesRegex(ValueError, "frozen snapshot"):
                runner.collect_signals(data, runner.settings(self.args()), self.output)

    def test_protocol_binds_registration_new_code_and_all_frozen_dependencies(self):
        with self.registered():
            protocol = runner.make_protocol(self.args())
        self.assertEqual(protocol["registrationSha256"], runner.REGISTRATION_SHA256)
        self.assertEqual(runner.source_tools.file_hash(runner.REGISTRATION), runner.REGISTRATION_SHA256)
        self.assertEqual(protocol["targetNetReturnPct"], 30)
        self.assertEqual(protocol["strategyOrder"], [runner.CANDIDATE_ID])
        for path in ("scripts/run-signal-sequence-price-proxy.py", "service/signal_sequence_price_proxy.py",
                     "service/signal_path_risk.py", "docs/signal-sequence-price-proxy-v1.json"):
            self.assertEqual(protocol["implementation"][path], runner.source_tools.file_hash(runner.ROOT / path))
        self.assertGreaterEqual(len(protocol["implementation"]), 24)
        self.assertFalse(runner.settings(self.args())["universe"]["strictHistory"])
        self.assertTrue(runner.settings(self.args(diagnostic_unverified_history=False))["universe"]["strictHistory"])


if __name__ == "__main__":
    unittest.main()
