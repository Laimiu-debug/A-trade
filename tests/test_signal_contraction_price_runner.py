"""Synthetic end-to-end contraction runner, source lock and receipt checks."""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import test_strategy_search_runner as fixtures


SPEC = importlib.util.spec_from_file_location("raw_contraction_runner", Path(__file__).resolve().parents[1] / "scripts/run-signal-contraction-price-proxy.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
REAL_REGISTRATION = runner.registration


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(row) for row in stream]


class ContractionRunnerTests(unittest.TestCase):
    write_source = fixtures.StrategySearchRunnerTests.write_source

    def setUp(self):
        fixtures.StrategySearchRunnerTests.setUp(self)
        self.symbols = [f"sh60000{i}" for i in range(1, 9)] + ["sz000001"]
        for symbol in self.symbols:
            self.write_pattern(symbol, pattern=symbol in self.symbols[:7])

    def write_pattern(self, symbol, *, pattern=True, current_amount=None, future_change=False):
        result = bytearray()
        for i, day in enumerate(self.dates):
            o = h = l = c = 1000
            volume = 10_000_000
            amount = 100_000_000 + min(int(symbol[-1]), 6) * 10_000_000
            if pattern:
                if 100 <= i < 110:
                    o, h, l, c, volume = 1100, 1200, 1000, 1100, 20_000_000
                elif 110 <= i < 120:
                    o, h, l, c, volume = 1120, 1180, 1050, 1120, 15_000_000
                elif 120 <= i < 130:
                    o, h, l, c = 1130, 1160, 1080, 1130
                elif i == 130:
                    o, h, l, c, volume = 1205, 1240, 1190, 1220, 30_000_000
                    if current_amount is not None:
                        amount = current_amount
                elif i >= 131:
                    c = (1230, 1320, 1430, 1550, 1650)[i - 131] if i < 136 else 1650
                    o = h = l = c
            if future_change and i == 185:
                o = h = l = c = 5000
            result.extend(struct.pack("<IIIIIfII", int(day.replace("-", "")), o, h, l, c, amount, volume, 0))
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
        return runner._base_bound_snapshot(data, args.start, args.end)

    @contextmanager
    def registered(self, args=None, data=None):
        args = args or self.args()
        data = data or self.dataset(args)
        doc = deepcopy(REAL_REGISTRATION())
        doc["sample"].update(signalStart=args.start, signalEnd=args.end,
            expectedFullSnapshotFingerprint=data["fingerprint"], expectedFullSecurities=len(data["securities"]),
            expectedFullBars=sum(len(s["bars"]) for s in data["securities"]),
            expectedScoredSessions=sum(args.start <= d <= args.end for d in data["calendar"]))
        with patch.object(runner, "registration", return_value=doc):
            yield data

    def execute(self, args=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.execute(args or self.args())

    def attempt(self, number=1):
        return self.output / "attempts" / f"attempt-{number:04d}"

    def collect(self, data, destination=None):
        destination = destination or self.output
        destination.mkdir()
        with self.registered(data=data):
            return runner.collect_signals(data, runner.settings(self.args()), destination)

    def test_raw_features_ranking_ties_all_stock_coverage_and_controls(self):
        data = self.dataset()
        before = deepcopy(data)
        signals, performance = self.collect(data)
        self.assertEqual(data, before)
        candidates = lines(self.output / "candidates.jsonl.gz")
        self.assertEqual(len(candidates), 7)
        expected = ["sh600006", "sh600007", "sh600005", "sh600004", "sh600003", "sh600002", "sh600001"]
        self.assertEqual([r["symbol"] for r in candidates], expected)
        self.assertTrue(all(r["score"] == r["rawScore"] == r["prior20MeanAmount"] for r in candidates))
        self.assertTrue(all(len(r["features"]["contractionBlocks"]) == 3 for r in candidates))
        self.assertTrue(all(r["features"]["rankingWindow"]["endDate"] == self.dates[129] for r in candidates))
        self.assertTrue(all(r["actionEvidence"] == "unverified" and not r["certified"] for r in candidates))
        selections = lines(self.output / "selection.jsonl.gz")
        self.assertEqual(len(selections), 40)
        selection = selections[0]
        self.assertEqual(selection["selectedTop5Symbols"], expected[:5])
        self.assertEqual(selection["rawCandidateCount"], 7)
        self.assertEqual([r["symbol"] for r in selection["fullRanking"]], expected)
        pool = set(self.symbols) - set(expected[:5])
        control = sorted(pool, key=lambda symbol: (hashlib.sha256(f"{runner.runtime.CONTROL_SEED}:{self.args().start}:{runner.CANDIDATE_ID}:{symbol}".encode()).hexdigest(), symbol))[:5]
        self.assertEqual(selection["controlMatching"]["selectedSymbols"], control)
        self.assertEqual(selection["controlMatching"]["shortfall"], 1)
        self.assertTrue({"sh600001", "sh600002"} <= set(control))
        self.assertEqual(performance["evaluatedSecuritySessions"], 40 * 9)
        self.assertEqual(sum(performance["evaluatedReasons"].values()), 40 * 9)
        for coverage in lines(self.output / "coverage.jsonl.gz"):
            self.assertEqual(sum(coverage["evaluatedKinds"].values()), 9)
            self.assertEqual(sum(coverage["evaluatedReasons"].values()), 9)
            self.assertEqual(coverage["evaluatedSecurities"], 9)
        self.assertEqual(len([s for s in signals if s["strategyId"] == runner.CANDIDATE_ID]), 5)

    def test_current_amount_does_not_leak_into_prior20_ranking(self):
        self.write_pattern("sh600001", current_amount=10_000_000_000)
        signals, _ = self.collect(self.dataset())
        candidates = lines(self.output / "candidates.jsonl.gz")
        bottom = next(r for r in candidates if r["symbol"] == "sh600001")
        self.assertEqual(bottom["rank"], 7)
        self.assertEqual(bottom["prior20MeanAmount"], 110_000_000)
        self.assertNotIn("sh600001", [s["symbol"] for s in signals if s["strategyId"] == runner.CANDIDATE_ID])

    def test_missing_slots_amount_and_halt_still_receive_exclusive_daily_evaluations(self):
        data = self.dataset()
        missing, amount_missing, halted = data["securities"][:3]
        missing["bars"] = [b for b in missing["bars"] if b["date"] != self.dates[120]]
        next(b for b in amount_missing["bars"] if b["date"] == self.dates[129]).pop("amount")
        halted["tradeStatusByDate"] = {self.args().start: False}
        next(b for b in halted["bars"] if b["date"] == self.args().start)["volume"] = 0
        self.collect(data)
        candidates = lines(self.output / "candidates.jsonl.gz")
        self.assertFalse({missing["symbol"], amount_missing["symbol"], halted["symbol"]} & {c["symbol"] for c in candidates})
        coverage = lines(self.output / "coverage.jsonl.gz")[0]
        self.assertEqual(coverage["evaluatedReasons"]["missingAlignedRawBar"], 1)
        self.assertEqual(coverage["evaluatedReasons"]["prior20AmountUnknown"], 1)
        self.assertEqual(coverage["evaluatedReasons"]["knownSuspension"], 1)
        self.assertEqual(sum(coverage["evaluatedReasons"].values()), 9)

    def test_raw_series_not_reference_adjusted_and_input_is_immutable(self):
        data = self.dataset()
        security = data["securities"][0]
        security["precloseByDate"] = {self.dates[100]: 5.}
        scorer = runner.DailyScorer(data, runner.settings(self.args()), pools_only=True)
        series = scorer.series[security["symbol"]]
        self.assertNotEqual(series.signal_bars[-1]["close"], series.bars[-1]["close"])
        before = deepcopy(data)
        self.collect(data)
        row = next(r for r in lines(self.output / "candidates.jsonl.gz") if r["symbol"] == security["symbol"])
        self.assertEqual(row["features"]["prior40High"], 12.)
        self.assertEqual(row["features"]["current"]["close"], 12.2)
        self.assertEqual(data, before)

    def test_real_helper_next_day_fees_path_risk_and_active_has_no_backfill(self):
        data = self.dataset()
        signals, _ = self.collect(data)
        selected = [s for s in signals if s["strategyId"] == runner.CANDIDATE_ID]
        duplicates = [{**s, "signalDate": self.dates[131]} for s in selected]
        result = runner.evaluate_signals(data, runner.settings(self.args()), [*signals, *duplicates])
        events = [e for e in result["events"] if e["strategyId"] == runner.CANDIDATE_ID]
        self.assertEqual(len(events), 5)
        self.assertEqual(result["overlapSuppressedCount"], 5)
        self.assertEqual({e["symbol"] for e in events}, {s["symbol"] for s in selected})
        self.assertTrue(all(e["entry"]["status"] == "filled" and e["entry"]["date"] == self.dates[131] and e["entry"]["fee"] > 0 for e in events))
        self.assertTrue(all(e["outcomes"]["nextOpenNet30"]["hitDate"] > e["entry"]["date"] for e in events))
        self.assertEqual(len(result["pathRiskEvents"]), sum(e["entry"]["status"] == "filled" for e in result["events"]))
        self.assertEqual({o["horizonSessions"] for g in result["pathRiskSummary"]["groups"] for o in g["observations"]}, {None, *runner.runtime.HORIZONS})
        self.assertFalse(result["pathRiskSummary"]["certified"])

    def test_lifecycle_source_read_only_all_six_artifacts_and_compact_summary(self):
        before = {p: runner.source_tools.file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        with self.registered():
            self.assertEqual(self.execute(), 0)
        self.assertEqual(before, {p: runner.source_tools.file_hash(p) for p in before})
        receipt = runner.source_tools.read_json(self.attempt() / "completion.json")
        self.assertEqual(set(receipt["files"]), {"candidates", "coverage", "signals", "result", "summary", "selections"})
        summary = runner.source_tools.read_json(self.attempt() / "summary.json")
        self.assertNotIn("pathRiskEvents", summary)
        self.assertIn("pathRiskSummary", summary)
        self.assertFalse(summary["certified"])
        self.assertFalse(summary["targetAchieved"])

    def test_success_receipt_recovers_without_recompute_and_future_source_is_bounded(self):
        with self.registered():
            self.assertEqual(self.execute(), 0)
            self.write_pattern(self.symbols[0], future_change=True)
            log = self.output / "events.jsonl"
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            log.write_text("".join(json.dumps(r) + "\n" for r in rows if r["event"] != "study_succeeded"), encoding="utf-8")
            with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("Must not recompute success")):
                self.assertEqual(self.execute(), 0)
        self.assertEqual(len(list((self.output / "attempts").iterdir())), 1)

    def test_changed_selection_log_or_missing_receipt_entry_refuses_recompute(self):
        with self.registered():
            self.assertEqual(self.execute(), 0)
            receipt = runner.source_tools.read_json(self.attempt() / "completion.json")
            missing = deepcopy(receipt)
            del missing["files"]["selections"]
            with self.assertRaisesRegex(ValueError, "incomplete"):
                runner.verify_completion(self.output, missing, receipt["protocolHash"], receipt["snapshotFingerprint"])
            (self.attempt() / "selection.jsonl.gz").write_bytes(b"changed")
            with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("Must not rerun")), self.assertRaisesRegex(ValueError, "artifact missing or changed"):
                self.execute()

    def test_failed_attempt_preserves_selection_audit_and_retry_is_separate(self):
        with self.registered():
            with patch.object(runner.runtime, "evaluate_signals", side_effect=RuntimeError("synthetic failure")), self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.execute()
            self.assertTrue((self.attempt() / "selection.jsonl.gz").exists())
            self.assertFalse((self.attempt() / "completion.json").exists())
            self.assertEqual(self.execute(), 0)
        self.assertTrue((self.attempt(2) / "completion.json").exists())

    def test_fingerprint_bar_count_session_count_and_sample_flag_cannot_relax(self):
        data = self.dataset()
        for mutation in (lambda d: d.update(fingerprint="wrong"), lambda d: d["securities"][0]["bars"].pop(), lambda d: d["calendar"].pop()):
            with self.registered(data=data):
                changed = deepcopy(data)
                mutation(changed)
                with self.assertRaisesRegex(ValueError, "frozen snapshot/counts"):
                    runner.collect_signals(changed, runner.settings(self.args()), self.output)
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.execute(self.args(diagnostic_unverified_history=False))
        with self.registered(), self.assertRaisesRegex(ValueError, "sample limit"):
            self.execute(self.args(max_symbols=64))
        with self.assertRaisesRegex(ValueError, "preregistered signal window"):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_cli_exposes_no_sample_or_window_parameter(self):
        for args in (["--max-symbols", "64"], ["--start", "2023-01-01"], ["--end", "2024-12-31"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["--workspace", str(self.workspace), "--output", str(self.output), *args])
        with patch.object(runner, "execute", return_value=0) as execute:
            self.assertEqual(runner.main(["--workspace", str(self.workspace), "--output", str(self.output), "--diagnostic-unverified-history"]), 0)
            args = execute.call_args.args[0]
            self.assertEqual((args.start, args.end, args.max_symbols), ("2022-01-01", "2023-12-31", None))

    def test_protocol_hashes_registration_and_all_added_execution_dependencies(self):
        with self.registered():
            protocol = runner.make_protocol(self.args())
        self.assertEqual(protocol["registrationSha256"], runner.REGISTRATION_SHA256)
        self.assertEqual(runner.source_tools.file_hash(runner.REGISTRATION), runner.REGISTRATION_SHA256)
        self.assertEqual(protocol["targetNetReturnPct"], 30)
        self.assertEqual(protocol["strategyOrder"], [runner.CANDIDATE_ID])
        for path in ("scripts/run-signal-contraction-price-proxy.py", "service/signal_contraction_price_proxy.py",
                     "service/signal_path_risk.py", "service/research_target.py", "docs/signal-contraction-price-proxy-v1.json"):
            self.assertEqual(protocol["implementation"][path], runner.source_tools.file_hash(runner.ROOT / path))
        self.assertGreaterEqual(len(protocol["implementation"]), 25)


if __name__ == "__main__":
    unittest.main()
