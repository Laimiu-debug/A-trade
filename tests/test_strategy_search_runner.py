"""Runner acceptance tests use synthetic TDX files, never user market/account data."""
from argparse import Namespace
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("strategy_search_runner", Path(__file__).resolve().parents[1] / "scripts/run-strategy-search.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class StrategySearchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "source-workspace"
        self.workspace.mkdir()
        self.output = self.root / "research-output"
        self.source = self.root / "synthetic-tdx"
        for market in ("sh", "sz"):
            (self.source / "vipdoc" / market / "lday").mkdir(parents=True)
        with closing(sqlite3.connect(self.workspace / "market.sqlite3")) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO meta VALUES('path',?)", (json.dumps(str(self.source)),))
            db.execute("CREATE TABLE securities(symbol TEXT,name TEXT)")
        self.dates = []
        day = date(2022, 1, 3)
        while len(self.dates) < 190:
            if day.weekday() < 5:
                self.dates.append(day.isoformat())
            day += timedelta(days=1)
        for symbol in ("sh600001", "sh600002", "sz000001", "sh688001", "sz300001", "sh000300"):
            self.write_source(symbol)

    def write_source(self, symbol, changed_index=None):
        rows = bytearray()
        for i, day in enumerate(self.dates):
            price = 1000 + i * 3 + (1 if i == changed_index else 0)
            volume = 20000000 if i % 7 == 0 else 10000000
            rows.extend(struct.pack("<IIIIIfII", int(day.replace("-", "")), price, price + 2, price - 2, price,
                                    price / 100 * volume, volume, 0))
        (self.source / "vipdoc" / symbol[:2] / "lday" / (symbol + ".day")).write_bytes(rows)

    def args(self, **changes):
        return Namespace(workspace=self.workspace, output=self.output, start=self.dates[130], end=self.dates[169],
                         max_symbols=changes.pop("max_symbols", None),
                         diagnostic_unverified_history=changes.pop("diagnostic_unverified_history", True),
                         history_source_id=None, **changes)

    def execute(self, args=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.execute(args or self.args())

    def events(self):
        return [json.loads(line) for line in (self.output / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def result(self, strategy="momentum"):
        completion = runner.read_json(self.output / "trials" / strategy / "attempt-0001" / "completion.json")
        with gzip.open(self.output / completion["result"]["path"], "rt", encoding="utf-8") as stream:
            return json.load(stream)

    def test_real_engine_keeps_input_read_only_and_registers_before_loading(self):
        before = {p: runner.file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        load = runner.snapshot_universe
        def registered_load(*args, **kwargs):
            protocol = runner.read_json(self.output / "protocol.json")
            self.assertEqual([t["id"] for t in protocol["trials"]], list(runner.STRATEGIES))
            return load(*args, **kwargs)
        with patch.object(runner, "snapshot_universe", side_effect=registered_load):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(before, {p: runner.file_hash(p) for p in before})
        self.assertEqual({p.relative_to(self.workspace).as_posix() for p in self.workspace.rglob("*") if p.is_file()}, {"market.sqlite3"})
        result = self.result()
        self.assertGreater(result["metrics"]["buyCount"], 0)
        self.assertGreater(result["metrics"]["totalFees"], 0)
        self.assertGreater(result["metrics"]["totalSlippage"], 0)
        self.assertGreater(result["metrics"]["closedTradeCount"], 0)
        self.assertTrue(all(t["signalDate"] < t["date"] <= self.dates[169] for t in result["trades"]))
        self.assertEqual(result["config"]["maxHoldingDays"], 9)
        self.assertEqual(result["config"]["stopLossPct"], .05)
        self.assertEqual(result["config"]["maxPositions"], 5)
        self.assertEqual(result["config"]["maxStockWeight"], .1)
        self.assertEqual(result["config"]["universe"]["minAmount20"], 50000000)
        self.assertTrue(all(t["symbol"] in {"sh600001", "sh600002", "sz000001"} for t in result["trades"]))
        self.assertEqual(len(result["curve"]), 40)
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))
        summary = runner.read_json(self.output / "summary.json")
        self.assertEqual([t["trialId"] for t in summary["trials"]], list(runner.STRATEGIES))
        self.assertFalse(summary["targetAchieved"])
        self.assertIsNone(summary["ranking"])
        self.assertTrue(all(t["goalAssessment"]["targetAchieved"] is False for t in summary["trials"]))

    def test_strict_history_is_default_and_unknown_fields_are_not_waived(self):
        self.assertEqual(self.execute(self.args(diagnostic_unverified_history=False)), 0)
        result = self.result()
        self.assertTrue(result["config"]["universe"]["strictHistory"])
        self.assertEqual(result["metrics"]["tradeCount"], 0)
        self.assertGreater(result["daily"][0]["coverage"]["reasons"]["strictHistoryMissing"], 0)
        self.assertFalse(result["quality"]["certified"])

    def test_resume_skips_success_and_changes_after_end_do_not_enter_snapshot(self):
        self.assertEqual(self.execute(), 0)
        protocol = (self.output / "protocol.json").read_bytes()
        snapshot = (self.output / "snapshot.json").read_bytes()
        self.write_source("sh600001", changed_index=185)
        with patch.object(runner.PreparedCache, "create", side_effect=AssertionError("successful trial must not rerun")):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(protocol, (self.output / "protocol.json").read_bytes())
        self.assertEqual(snapshot, (self.output / "snapshot.json").read_bytes())
        self.assertEqual(sum(row["event"] == "trial_started" for row in self.events()), 3)

    def test_changed_source_or_protocol_cannot_resume(self):
        self.assertEqual(self.execute(), 0)
        with self.assertRaisesRegex(ValueError, "Existing protocol differs"):
            self.execute(self.args(diagnostic_unverified_history=False))
        self.write_source("sh600001", changed_index=150)
        with patch.object(runner.PreparedCache, "create", side_effect=AssertionError("changed input must not rerun")):
            with self.assertRaisesRegex(ValueError, "snapshot fingerprint or coverage changed"):
                self.execute()

    def test_missing_success_artifact_is_error_never_an_implicit_rerun(self):
        self.assertEqual(self.execute(), 0)
        (self.output / "trials/momentum/attempt-0001/result.json.gz").unlink()
        with patch.object(runner.PreparedCache, "create", side_effect=AssertionError("must not rerun")):
            with self.assertRaisesRegex(ValueError, "Successful trial artifact missing or changed"):
                self.execute()

    def test_failed_trial_is_logged_and_only_failed_trial_retries(self):
        create = runner.PreparedCache.create
        def fail_one(cache, engine_class, data, config):
            if config["allocation"]["strategyIds"] == ["trendBreakout"]:
                raise RuntimeError("synthetic interruption")
            return create(cache, engine_class, data, config)
        with patch.object(runner.PreparedCache, "create", new=fail_one):
            self.assertEqual(self.execute(), 1)
        self.assertEqual(sum(row["event"] == "trial_failed" for row in self.events()), 1)
        attempts = []
        def count_create(cache, engine_class, data, config):
            attempts.append(config["allocation"]["strategyIds"])
            return create(cache, engine_class, data, config)
        with patch.object(runner.PreparedCache, "create", new=count_create):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(attempts, [["trendBreakout"]])
        self.assertTrue((self.output / "trials/trendBreakout/attempt-0002/completion.json").is_file())

    def test_durable_completion_recovers_event_without_running_again(self):
        self.assertEqual(self.execute(), 0)
        rows = self.events()
        rows = [row for row in rows if not (row["event"] == "trial_succeeded" and row["receipt"]["trialId"] == "trendPullback")]
        (self.output / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        with patch.object(runner.PreparedCache, "create", side_effect=AssertionError("completion receipt prevents rerun")):
            self.assertEqual(self.execute(), 0)
        recovered = [r for r in self.events() if r.get("recovered")]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["receipt"]["trialId"], "trendPullback")

    def test_pilot_selects_symbol_hash_before_bars_and_never_replaces_bad_data(self):
        symbols = runner.discover_symbols(self.workspace)
        chosen = runner.pilot_symbols(symbols, 1)
        self.assertEqual(chosen, runner.pilot_symbols(list(reversed(symbols)), 1))
        corrupt = self.source / "vipdoc" / chosen[0][:2] / "lday" / (chosen[0] + ".day")
        corrupt.write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "No securities observed"):
            self.execute(self.args(max_symbols=1))
        self.assertEqual(runner.read_json(self.output / "selection.json")["symbols"], chosen)
        self.assertFalse(any(row["event"] == "trial_started" for row in self.events()))

    def test_output_cannot_write_into_input_workspace(self):
        args = self.args()
        args.output = self.workspace / "output"
        with self.assertRaisesRegex(ValueError, "outside the read-only input workspace"):
            self.execute(args)
        self.assertFalse(args.output.exists())

    def test_imported_history_snapshot_is_read_only_and_bounded(self):
        from service.portfolio_history import HistoryStore, VERSION
        source = runner.snapshot_universe(self.workspace, {"markets": ["sh", "sz"], "boards": ["main"]}, self.dates[-1], self.dates[0])
        symbols = [s["symbol"] for s in source["securities"]]
        for security in source["securities"]:
            bars = security["bars"]
            security.update(listingDate="2010-01-01", corporateActionsComplete=True, corporateActions=[],
                            stByDate={b["date"]: False for b in bars},
                            tradeStatusByDate={b["date"]: True for b in bars},
                            openingVolumeByDate={b["date"]: 1000000 for b in bars},
                            precloseByDate={b["date"]: bars[max(0, i - 1)]["close"] for i, b in enumerate(bars)})
        package = {"schema": VERSION,
                   "declaration": {"universe": "synthetic three stocks", "source": "synthetic fixture",
                                   "calendarSource": "synthetic independent calendar", "completeMembership": True, "completeActions": True},
                   "coverage": {"startDate": self.dates[0], "endDate": self.dates[-1]},
                   "dataset": {"securities": source["securities"], "calendar": self.dates,
                               "membershipByDate": {day: symbols for day in self.dates}}}
        imported = HistoryStore(self.workspace).import_package(package)
        before = {p: runner.file_hash(p) for p in self.workspace.iterdir() if p.is_file()}
        args = self.args(diagnostic_unverified_history=False)
        args.history_source_id = imported["id"]
        actual = runner.bound_snapshot
        def bounded(data, start, end):
            result = actual(data, start, end)
            self.assertTrue(all(row["date"] <= end for s in result["securities"] for row in s["bars"]))
            self.assertTrue(all(day <= end for day in result["membershipByDate"]))
            self.assertTrue(all(day <= end for s in result["securities"] for day in s["stByDate"]))
            return result
        with patch.object(runner, "bound_snapshot", side_effect=bounded):
            self.assertEqual(self.execute(args), 0)
        self.assertEqual(before, {p: runner.file_hash(p) for p in self.workspace.iterdir() if p.is_file()})
        self.assertGreater(self.result()["metrics"]["buyCount"], 0)
        self.assertTrue(self.result()["config"]["universe"]["strictHistory"])


if __name__ == "__main__":
    unittest.main()
