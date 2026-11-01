"""Streaming projection and information boundaries, with synthetic sources only."""
from copy import deepcopy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ignition_selection_runner", ROOT / "scripts/run-ignition-selection-review.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def source():
    days = ["2026-10-14", "2026-10-15"]
    bars = [{"date": day, "open": 12.34, "close": 12.4, "high": 12.5, "low": 12.2,
             "volume": 1000, "amount": 12340.} for day in days]
    return {"fraction": 1.2345e-12, "calendar": days, "calendarSource": "independent-synthetic",
        "fingerprint": "full-original", "securities": [
            {"symbol": symbol, "name": "名字，含转义\"与花括号{}", "bars": deepcopy(bars)}
            for symbol in ("sh600001", "sh600002", "sh600003")],
        "benchmark": {"symbol": "sh000300", "bars": deepcopy(bars)},
        "membershipByDate": {day: ["sh600001", "sh600002", "sh600003"] for day in days}}


class StreamingProjectionTests(unittest.TestCase):
    def project(self, value, wanted, chunk=7):
        with tempfile.TemporaryDirectory(dir="H:/codex/tmp") as folder:
            path = Path(folder) / "source.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
            return runner.project_source(path, wanted, expected_population=3, chunk_size=chunk)

    def test_tiny_chunks_preserve_exact_selected_observations_and_source_metadata(self):
        data = source()
        projected = self.project(data, ["sh600003", "sh600001"])
        self.assertEqual(projected["securities"], [data["securities"][0], data["securities"][2]])
        for key in ("calendar", "calendarSource", "fraction", "benchmark", "membershipByDate"):
            self.assertEqual(projected[key], data[key])
        self.assertEqual(projected["executionProjection"]["sourcePopulation"], 3)
        self.assertEqual(projected["executionProjection"]["sourceFingerprint"], "full-original")
        self.assertEqual(projected["executionProjection"]["executionSymbols"], 2)
        self.assertNotEqual(projected["fingerprint"], data["fingerprint"])

    def test_missing_selected_security_and_duplicate_source_fail(self):
        with self.assertRaisesRegex(ValueError, "omitted"):
            self.project(source(), ["sh600004"])
        data = source()
        data["securities"][1]["symbol"] = "sh600001"
        with self.assertRaisesRegex(ValueError, "Duplicate source security"):
            self.project(data, ["sh600001"])

    def test_unselected_future_price_also_fails(self):
        data = source()
        data["securities"][-1]["bars"][-1]["date"] = "2026-10-16"
        with self.assertRaisesRegex(ValueError, "Post-cutoff"):
            self.project(data, ["sh600001"])

    def test_scalar_parser_requires_number_delimiter_and_rejects_nonfinite(self):
        for text, expected in (("123.456e-10,", 123.456e-10), ("-0.0025]", -.0025),
                               ('"中文\\\"{}":[', '中文"{}')):
            parser = runner.JsonStream(io.StringIO(text), chunk_size=1)
            self.assertEqual(parser.value(), expected)
        with self.assertRaises(ValueError):
            runner.JsonStream(io.StringIO("NaN,"), chunk_size=1).value()

    def test_malformed_source_is_not_accepted(self):
        for text in ('{"securities":[],}', '{"securities":[{} ,]}', '{"securities": []} extra',
                     '{"calendar":[],"calendar":[],"securities":[]}'):
            with self.subTest(text=text), tempfile.TemporaryDirectory(dir="H:/codex/tmp") as folder:
                path = Path(folder) / "bad.json"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises((ValueError, KeyError)):
                    runner.project_source(path, ["sh600001"], expected_population=3, chunk_size=1)

    def test_write_scope_cannot_escape_new_experiment(self):
        for path in (ROOT / "docs/blocked.json", runner.OUT / "../blocked.json"):
            with self.assertRaises(ValueError):
                runner.output_path(path)


class FormationPrefixTests(unittest.TestCase):
    def test_matrix_prefix_shares_data_but_exposes_no_later_columns(self):
        original = {"calendar": np.asarray(["2025-12-31", "2026-01-05"]),
                    "symbols": np.asarray(["sh600001"]), "high": np.asarray([[11., 99.]]),
                    "priceValid": np.asarray([[True, True]])}
        prefix = runner.matrix_prefix(original, "2025-12-31")
        self.assertEqual(prefix["calendar"].tolist(), ["2025-12-31"])
        self.assertEqual(prefix["high"].tolist(), [[11.]])
        self.assertTrue(np.shares_memory(prefix["high"], original["high"]))
        self.assertEqual(original["high"].shape, (1, 2))

    def test_all_formation_date_collections_are_bounded(self):
        source_rows = [{"signalId": "a", "signalDate": "2025-12-31"},
                       {"signalId": "b", "signalDate": "2026-01-05"}]
        data = {"daily": [{"date": row["signalDate"], "snapshotId": row["signalId"]} for row in source_rows],
                "metadata": {row["signalId"]: row for row in source_rows},
                "allCandidates": source_rows, "schedules": {"complete": source_rows},
                "sectorMaps": {"a": {}, "b": {}}}
        original = deepcopy(data)
        result = runner.formation_prefix(data, "2025-12-31")
        self.assertEqual(result["allCandidates"], source_rows[:1])
        self.assertEqual(result["schedules"]["complete"], source_rows[:1])
        self.assertEqual(set(result["metadata"]), {"a"})
        self.assertEqual(set(result["sectorMaps"]), {"a"})
        self.assertEqual(data, original)

    def test_slot_audit_rejects_deleted_slots_and_changed_b_metadata(self):
        signal = {"signalId": "b", "signalDate": "2025-12-31", "symbol": "sh600001", "originalRank": 2}
        parent = {"metadata": {"b": {**signal, "sectorCode": "sector", "pattern": "B_corePullback", "signalClose": 10.}}}
        runner.validate_slots(parent, [signal], deepcopy(parent), [deepcopy(signal)])
        with self.assertRaisesRegex(ValueError, "slots changed"):
            runner.validate_slots(parent, [signal], parent, [])
        changed = deepcopy(parent)
        changed["metadata"]["b"]["signalClose"] = 11.
        with self.assertRaisesRegex(ValueError, "B metadata"):
            runner.validate_slots(parent, [signal], changed, [signal])


if __name__ == "__main__":
    unittest.main()
