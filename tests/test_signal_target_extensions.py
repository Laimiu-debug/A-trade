from argparse import Namespace
from contextlib import redirect_stdout
from copy import deepcopy
import datetime as dt
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_strategy_search_runner as source_fixtures
from service.portfolio_signals import DailyScorer
from service.signal_target_extensions import ExtensionScorer


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("signal_extension_test_runner", ROOT / "scripts/run-signal-target-extensions.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def fixture(count=1, length=170):
    day, days = dt.date(2022, 1, 3), []
    while len(days) < length:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += dt.timedelta(days=1)
    securities = []
    for number in range(count):
        symbol = f"sh600{number + 1:03d}"
        bars = [{"date": date, "open": 10 + i * .01, "high": 10 + i * .01,
                 "low": 10 + i * .01, "close": 10 + i * .01, "volume": 1_000_000,
                 "amount": 100_000_000} for i, date in enumerate(days)]
        securities.append({"symbol": symbol, "bars": bars, "listingDate": "2000-01-01",
                           "stByDate": {date: False for date in days},
                           "tradeStatusByDate": {date: True for date in days}})
    data = {"securities": securities, "calendar": days, "fingerprint": "synthetic-extension",
            "benchmark": {"symbol": "sh000300", "bars": [{"date": date, "close": 100 + i} for i, date in enumerate(days)]}}
    args = Namespace(start=days[150], end=days[155], diagnostic_unverified_history=True)
    config = runner.settings(args)
    return data, config


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class ExtensionFilterTests(unittest.TestCase):
    def test_filter_is_before_top_five_and_preserves_original_score(self):
        data, config = fixture(count=7)
        day = data["calendar"][150]
        config["endDate"] = day
        rows = [{"symbol": security["symbol"], "score": 1. - i * .1, "rawScore": 10. - i}
                for i, security in enumerate(data["securities"])]
        for security in data["securities"][5:]:
            security["bars"][150]["volume"] = 2_000_000
        scored = {"strategies": {"trendPullback": rows, "trendBreakout": [], "momentum": []},
                  "eligibilityBySymbol": {row["symbol"]: {"eligible": True} for row in rows}, "coverage": {}}
        with tempfile.TemporaryDirectory() as directory, patch.object(DailyScorer, "daily", return_value=deepcopy(scored)):
            signals, _ = runner.collect_signals(data, config, Path(directory))
            selected = [row for row in signals if row["strategyId"] == "pullbackVolumeConfirm"]
            self.assertEqual([row["symbol"] for row in selected], [row["symbol"] for row in rows[5:]])
            self.assertEqual([row["rank"] for row in selected], [1, 2])
            self.assertEqual([row["score"] for row in selected], [row["score"] for row in rows[5:]])
            self.assertEqual(len(lines(Path(directory) / "candidates.jsonl.gz")), 2)
            controls = [row for row in signals if row["strategyId"] == "control_pullbackVolumeConfirm"]
            self.assertEqual(len(controls), 2)
            self.assertFalse({r["symbol"] for r in controls} & {r["symbol"] for r in selected})

    def test_volume_requires_all_twenty_previous_exchange_sessions(self):
        data, config = fixture()
        day, security = data["calendar"][150], data["securities"][0]
        security["bars"][150]["volume"] = 1_200_000
        scorer = ExtensionScorer(data, config)
        self.assertTrue(scorer._accept("pullbackVolumeConfirm", security["symbol"], day, {}))
        security["bars"] = [bar for bar in security["bars"] if bar["date"] != data["calendar"][140]]
        self.assertFalse(ExtensionScorer(data, config)._accept("pullbackVolumeConfirm", security["symbol"], day, {}))

    def test_first_breakout_rechecks_full_prior_conditions_not_prior_selected_signals(self):
        data, config = fixture()
        day = data["calendar"][150]
        data["securities"][0]["bars"][150]["volume"] = 2_000_000
        self.assertEqual(len(ExtensionScorer(data, config).daily(day)["strategies"]["firstBreakoutTenSessions"]), 1)
        # This past trigger is before the study start; it must still block T.
        data["securities"][0]["bars"][145]["volume"] = 2_000_000
        self.assertEqual(ExtensionScorer(data, config).daily(day)["strategies"]["firstBreakoutTenSessions"], [])

    def test_prior_breakout_history_gap_is_unknown_not_false(self):
        data, config = fixture()
        day = data["calendar"][150]
        security = data["securities"][0]
        security["bars"][150]["volume"] = 2_000_000
        security["bars"] = [bar for bar in security["bars"] if bar["date"] != data["calendar"][65]]
        self.assertEqual(len(DailyScorer(data, config).daily(day)["strategies"]["trendBreakout"]), 1)
        self.assertEqual(ExtensionScorer(data, config).daily(day)["strategies"]["firstBreakoutTenSessions"], [])

    def test_future_prices_volumes_and_benchmark_cannot_change_t_signal(self):
        data, config = fixture()
        day = data["calendar"][150]
        data["securities"][0]["bars"][150]["volume"] = 2_000_000
        before = ExtensionScorer(data, config).daily(day)["strategies"]
        for bar in data["securities"][0]["bars"][151:]:
            bar.update(open=99., high=99., low=99., close=99., volume=99999999)
        for bar in data["benchmark"]["bars"][151:]:
            bar["close"] = 1.
        self.assertEqual(before, ExtensionScorer(data, config).daily(day)["strategies"])

    def test_market_gate_rejects_missing_wrong_or_down_benchmark(self):
        data, config = fixture()
        day = data["calendar"][150]
        self.assertEqual(len(ExtensionScorer(data, config).daily(day)["strategies"]["momentumMarketUp"]), 1)
        variants = []
        missing = deepcopy(data)
        del missing["benchmark"]
        variants.append(missing)
        wrong = deepcopy(data)
        wrong["benchmark"]["symbol"] = "sh000001"
        variants.append(wrong)
        gap = deepcopy(data)
        gap["benchmark"]["bars"] = [bar for bar in gap["benchmark"]["bars"] if bar["date"] != data["calendar"][130]]
        self.assertEqual(DailyScorer(gap, config).daily(day)["regime"]["status"], "observed")
        variants.append(gap)
        down = deepcopy(data)
        for i, bar in enumerate(down["benchmark"]["bars"]):
            bar["close"] = 500 - i
        variants.append(down)
        for variant in variants:
            self.assertEqual(ExtensionScorer(variant, config).daily(day)["strategies"]["momentumMarketUp"], [])

    def test_extension_uses_actual_next_open_costs_and_active_suppression(self):
        data, config = fixture()
        with tempfile.TemporaryDirectory() as directory:
            signals, _ = runner.collect_signals(data, config, Path(directory))
        result = runner.evaluate_signals(data, config, signals)
        events = [event for event in result["events"] if event["strategyId"] == "momentumMarketUp"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["signalDate"], data["calendar"][150])
        self.assertEqual(event["entry"]["date"], data["calendar"][151])
        self.assertGreater(event["entry"]["fee"], 0)
        self.assertEqual(event["entry"]["status"], "filled")
        self.assertGreater(result["overlapSuppressedCount"], 0)
        self.assertFalse(result["riskBoundaryJointStatistics"]["implemented"])


class ExtensionRunnerTests(unittest.TestCase):
    write_source = source_fixtures.StrategySearchRunnerTests.write_source

    def setUp(self):
        source_fixtures.StrategySearchRunnerTests.setUp(self)

    def args(self):
        return Namespace(workspace=self.workspace, output=self.output, start=self.dates[130], end=self.dates[169],
                         max_symbols=None, diagnostic_unverified_history=True)

    def test_protocol_binds_registration_new_code_and_frozen_dependencies(self):
        protocol = runner.make_protocol(self.args())
        implementation = {path.replace("\\", "/"): digest for path, digest in protocol["implementation"].items()}
        for path in ("scripts/run-signal-target-extensions.py", "service/signal_target_extensions.py",
                     "docs/signal-target-candidates-v1.json", "scripts/run-signal-target-study.py",
                     "service/signal_target_study.py", "service/execution_evidence.py"):
            self.assertEqual(implementation[path], runner.source_tools.file_hash(ROOT / path))
        self.assertEqual(protocol["registrationSha256"], runner.REGISTRATION_SHA256)
        self.assertEqual(protocol["strategyOrder"], list(runner.STRATEGIES))
        self.assertEqual(protocol["registration"]["candidateCount"], 3)
        self.assertFalse(protocol["riskBoundaryJointStatistics"]["implemented"])
        self.assertIsNone(protocol["stopLoss"])
        self.assertFalse(protocol["targetAchieved"])

    def test_registered_document_changes_are_rejected(self):
        changed = self.root / "changed-registration.json"
        changed.write_bytes(runner.REGISTRATION.read_bytes() + b"\n")
        with patch.object(runner, "REGISTRATION", changed):
            with self.assertRaisesRegex(ValueError, "preregistered candidate document changed"):
                runner.make_protocol(self.args())

    def test_synthetic_full_run_is_read_only_and_successful_resume_never_recomputes(self):
        before = {path: runner.source_tools.file_hash(path) for path in self.root.rglob("*") if path.is_file()}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.execute(self.args()), 0)
        self.assertEqual(before, {path: runner.source_tools.file_hash(path) for path in before})
        attempt = self.output / "attempts/attempt-0001"
        summary = runner.source_tools.read_json(attempt / "summary.json")
        self.assertEqual(summary["strategyOrder"], list(runner.STRATEGIES))
        self.assertTrue(all(group["role"] in ("registered-extension", "control") for group in summary["groups"]))
        self.assertGreater(sum(group["filledEntryCount"] for group in summary["groups"]), 0)
        self.assertFalse(summary["riskBoundaryJointStatistics"]["implemented"])
        self.write_source("sh600001", changed_index=185)
        with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("must not rerun")), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.execute(self.args()), 0)
        self.assertEqual(len(list((self.output / "attempts").iterdir())), 1)
        (attempt / "signals.jsonl.gz").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact missing or changed"):
            runner.execute(self.args())

    def test_cli_defaults_preserve_registered_development_window(self):
        with patch.object(runner.runtime, "execute", return_value=0) as execute:
            self.assertEqual(runner.main(["--workspace", str(self.workspace), "--output", str(self.output)]), 0)
        args = execute.call_args.args[0]
        self.assertEqual((args.start, args.end), ("2022-01-01", "2023-12-31"))
        self.assertFalse(args.diagnostic_unverified_history)
        self.assertIsNone(args.max_symbols)


if __name__ == "__main__":
    unittest.main()
