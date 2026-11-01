"""Synthetic file/lifecycle/causality checks; never read the real source workspace."""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_strategy_search_runner as fixtures

SPEC = importlib.util.spec_from_file_location("leadership_panel_runner", Path(__file__).resolve().parents[1] / "scripts/build-signal-leadership-panel.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class LeadershipPanelRunnerTests(unittest.TestCase):
    write_source = fixtures.StrategySearchRunnerTests.write_source

    def setUp(self):
        fixtures.StrategySearchRunnerTests.setUp(self)
        self.document = runner.registration()
        self.calendar = self.dates[:170]
        data = self.dataset()
        self.document["sample"].update(signalStart=self.dates[130], signalEnd=self.dates[169],
            warmupStart=self.dates[0], expectedFullSnapshotFingerprint=data["fingerprint"],
            expectedFullSecurities=3, expectedFullBars=510, expectedScoredSessions=40,
            expectedWarmupSessions=130, expectedCalendarSessions=170, expectedFeatureRows=120)
        current, end = date.fromisoformat(self.dates[0]), date.fromisoformat(self.dates[169])
        rows = []
        while current <= end:
            rows.append([current.isoformat(), "1" if current.isoformat() in self.calendar else "0"])
            current += timedelta(days=1)
        self.calendar_path = self.root / "saved-calendar.json"
        self.calendar_payload = {"provider": "baostock", "api": "query_trade_dates", "errorCode": "0",
            "rowBoundReached": False, "fields": ["calendar_date", "is_trading_day"], "rows": rows,
            "parameters": {"start_date": self.dates[0], "end_date": self.dates[169]}}
        self.write_calendar()

    def write_calendar(self):
        self.calendar_path.write_text(json.dumps(self.calendar_payload), encoding="utf-8")
        self.document["calendar"].update(path=str(self.calendar_path),
            sha256=runner.source_tools.file_hash(self.calendar_path), start=self.dates[0], end=self.dates[169])

    def dataset(self):
        document = runner.source_tools.read_json(runner.REGISTRATION)
        data = runner.source_tools.snapshot_universe(self.workspace, document["sample"]["selection"], self.dates[169], self.dates[130])
        return runner.source_tools.bound_snapshot(data, self.dates[130], self.dates[169])

    @contextmanager
    def registered(self):
        with patch.object(runner, "registration", return_value=deepcopy(self.document)), patch.object(runner, "SOURCE_WORKSPACE", self.workspace):
            yield

    def execute(self):
        with self.registered(), redirect_stdout(io.StringIO()):
            return runner.execute(Namespace(workspace=self.workspace, output=self.output))

    def extract(self, data, name):
        output = self.root / name
        output.mkdir()
        identity = runner.input_identity(data, self.calendar, self.document)
        result = runner.extract(data, self.document, self.calendar, {"inputs": identity}, "synthetic-manifest", output)
        return lines(output / "features.jsonl.gz"), result, output

    def test_full_rows_warmup_persistence_manifest_receipt_and_source_immutable(self):
        before = {p: runner.source_tools.file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.execute(), 0)
        self.assertEqual(before, {p: runner.source_tools.file_hash(p) for p in before})
        rows = lines(self.output / "features.jsonl.gz")
        expected = [(day, symbol) for day in self.dates[130:170] for symbol in ("sh600001", "sh600002", "sz000001")]
        self.assertEqual([(r["asOfDate"], r["symbol"]) for r in rows], expected)
        self.assertEqual(len(set(expected)), 120)
        manifest = runner.source_tools.read_json(self.output / "manifest.json")
        self.assertEqual({r["sampleManifestHash"] for r in rows}, {runner.source_tools.file_hash(self.output / "manifest.json")})
        self.assertTrue(manifest["inputs"]["calendar"]["originalRestrictedDatesEqualSavedExchangeDates"])
        self.assertTrue(manifest["inputs"]["benchmark"]["datesExactlyMatchCalendar"])
        self.assertEqual(len(lines(self.output / "daily-coverage.jsonl.gz")), 40)
        warmup = lines(self.output / "warmup-coverage.jsonl.gz")
        self.assertEqual(len(warmup), 130)
        self.assertEqual(warmup[-1]["eligibilityReasons"], {"notEvaluatedDuringWarmup": 3})
        self.assertEqual(rows[0]["trailing20StrengthCrossSectionCounts"], [3] * 20)
        self.assertEqual(rows[0]["trailing20DailyStrengthPercentiles"], [.5] * 20)
        self.assertEqual(rows[0]["knownStrengthRankSessions20"], 20)
        receipt = runner.source_tools.read_json(self.output / "completion.json")
        self.assertEqual(set(receipt["files"]), {"manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz",
            "warmup-coverage.jsonl.gz", "summary.json", "execution-sources.zip", "attempt.json"})
        for name, row in receipt["files"].items():
            self.assertEqual(row["sha256"], runner.source_tools.file_hash(self.output / name))
        with zipfile.ZipFile(self.output / "execution-sources.zip") as archive:
            for name, digest in manifest["implementation"].items():
                self.assertEqual(runner.hashlib.sha256(archive.read(name)).hexdigest(), digest)
        summary = runner.source_tools.read_json(self.output / "summary.json")
        self.assertFalse(summary["strategyReturnsCalculated"])
        self.assertFalse(summary["forwardReturnsCalculated"])
        self.assertTrue(summary["backwardPriceReturnsCalculated"])
        self.assertFalse(summary["targetAchieved"])

    def test_registration_is_frozen_and_cli_has_no_labels_dates_sample_or_score(self):
        self.assertEqual(runner.source_tools.file_hash(runner.REGISTRATION), runner.REGISTRATION_SHA256)
        self.assertEqual(runner.registration()["featureVersion"], runner.FEATURE_VERSION)
        for option in ("--labels", "--label-path", "--start", "--end", "--max-symbols", "--strategy", "--score"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["--workspace", str(self.workspace), "--output", str(self.output), option, "x"])
        with patch.object(runner, "REGISTRATION_SHA256", "wrong"), self.assertRaisesRegex(ValueError, "not frozen"):
            runner.registration()

    def test_physical_hash_inventory_covers_alternate_source_filenames(self):
        canonical = self.source / "vipdoc/sh/lday/sh600001.day"
        alternate = canonical.with_name("600001.day")
        alternate.write_bytes(canonical.read_bytes())
        hashes = runner.source_file_hashes(self.workspace, self.document)
        self.assertIn(str(canonical.resolve()), hashes)
        self.assertIn(str(alternate.resolve()), hashes)
        alternate.write_bytes(alternate.read_bytes() + b"changed")
        self.assertNotEqual(runner.source_file_hashes(self.workspace, self.document), hashes)

    def test_fixed_counts_fingerprint_and_independent_calendar_matching(self):
        data = self.dataset()
        for mutation in (lambda d: d.update(fingerprint="wrong"), lambda d: d["securities"][0]["bars"].pop(),
                         lambda d: d["securities"].pop()):
            changed = deepcopy(data)
            mutation(changed)
            with self.assertRaisesRegex(ValueError, "frozen sample"):
                runner.validate_sample(changed, self.document, self.calendar)
        changed = deepcopy(data)
        changed["calendar"].remove(self.dates[90])
        with self.assertRaisesRegex(ValueError, "calendar differs"):
            runner.validate_sample(changed, self.document, self.calendar)
        with patch.object(runner, "SOURCE_WORKSPACE", self.root / "different"), self.assertRaisesRegex(ValueError, "isolated"):
            runner.execute(Namespace(workspace=self.workspace, output=self.output))
        self.assertFalse(self.output.exists())

    def test_saved_calendar_hash_success_natural_day_completeness_and_counts(self):
        self.assertEqual(runner.saved_calendar(self.document), self.calendar)
        with self.calendar_path.open("a", encoding="utf-8") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(ValueError, "hash"):
            runner.saved_calendar(self.document)
        self.write_calendar()
        original = deepcopy(self.calendar_payload)
        for mutation in (lambda p: p.update(errorCode="1"), lambda p: p.update(rowBoundReached=True),
                         lambda p: p["rows"].pop(), lambda p: p["rows"].insert(0, p["rows"][0])):
            self.calendar_payload = deepcopy(original)
            mutation(self.calendar_payload)
            self.write_calendar()
            with self.assertRaises(ValueError):
                runner.saved_calendar(self.document)

    def test_ineligible_missing_and_halt_rows_still_rank_independently(self):
        data = self.dataset()
        data["securities"][0]["stByDate"] = {self.dates[130]: True}
        data["securities"][1]["bars"] = [b for b in data["securities"][1]["bars"] if b["date"] != self.dates[130]]
        data["securities"][2]["tradeStatusByDate"] = {self.dates[131]: 0}
        before = deepcopy(data)
        rows, summary, output = self.extract(data, "quality")
        self.assertEqual(data, before)
        first = rows[:3]
        self.assertEqual(first[0]["baseEligibility"]["reason"], "historicalSt")
        self.assertEqual(first[0]["asOfEvidence"]["historicalSt"]["value"], True)
        self.assertEqual(first[0]["historicalStEvidence"], "provided-for-date-unverified")
        self.assertIsNotNone(first[0]["relativeReturn20Percentile"])
        self.assertIsNone(first[1]["relativeReturn20Percentile"])
        self.assertEqual({r["relativeReturn20CrossSectionCount"] for r in first}, {2})
        self.assertEqual(summary["featureRows"], 120)
        coverage = lines(output / "daily-coverage.jsonl.gz")
        self.assertEqual(coverage[0]["qualityCounts"]["missingDates:current"], 1)
        self.assertEqual(coverage[1]["qualityCounts"]["haltDates:current"], 1)
        for row in coverage:
            self.assertEqual(sum(row["eligibilityReasons"].values()), 3)

    def test_asof_source_evidence_preserves_known_values_without_current_name(self):
        data = self.dataset()
        security = data["securities"][0]
        day = self.dates[130]
        security.update(name="*ST current name", stHistory={day: False}, memberByDate={day: False},
                        listingDate="2020-01-01", tradeStatusByDate={day: "1"},
                        corporateActions=[{"exDate": day, "kind": "source-claim"}, {"exDate": self.dates[131]}])
        evidence = runner.as_of_evidence(data, security, day)
        self.assertFalse(evidence["historicalSt"]["value"])
        self.assertFalse(evidence["historicalMembership"]["value"])
        self.assertEqual(evidence["listingDate"]["value"], "2020-01-01")
        self.assertEqual(evidence["tradeStatus"]["value"], "1")
        self.assertEqual(len(evidence["actionEvidence"]["effectiveDateSourceObservations"]), 1)
        self.assertFalse(evidence["certified"])
        data["membershipByDate"] = {day: [security["symbol"]]}
        self.assertTrue(runner.as_of_evidence(data, security, day)["historicalMembership"]["value"])

    def test_unknown_history_counts_include_all_early_qualification_returns_and_warmup(self):
        data = self.dataset()
        keys = {"historicalSt", "historicalMembership", "listingDate", "historicalTradeStatus"}
        for mode in ("notObservedYet", "missingDailyBar", "invalidHistory"):
            changed = deepcopy(data)
            for security in changed["securities"]:
                if mode == "notObservedYet":
                    security["bars"] = []
                elif mode == "missingDailyBar":
                    security["bars"] = [b for b in security["bars"] if b["date"] != self.dates[130]]
                else:
                    security["bars"][0]["amount"] = "invalid"
            rows, summary, output = self.extract(changed, "unknown-" + mode)
            self.assertEqual({r["baseEligibility"]["reason"] for r in rows[:3]}, {mode})
            self.assertEqual(lines(output / "daily-coverage.jsonl.gz")[0]["unknownHistoricalFields"], dict.fromkeys(keys, 3))
            self.assertEqual(lines(output / "warmup-coverage.jsonl.gz")[0]["unknownHistoricalFields"], dict.fromkeys(keys, 3))
            self.assertEqual(summary["unknownHistoricalFields"], dict.fromkeys(keys, 120))

    def test_provided_invalid_evidence_is_unknown_and_distinct_from_missing(self):
        data = self.dataset()
        day = self.dates[130]
        security = data["securities"][0]
        security.update(stByDate={day: "unknown"}, memberHistory={day: 2}, tradeStatusByDate={day: "unknown"}, listingDate="bad-date")
        evidence = runner.as_of_evidence(data, security, day)
        for key in ("historicalSt", "historicalMembership", "tradeStatus", "listingDate"):
            self.assertEqual(evidence[key]["status"], "unknown")
            self.assertEqual(evidence[key]["sourceValueStatus"], "provided-invalid")
        blank = runner.as_of_evidence(data, data["securities"][1], day)
        self.assertEqual(blank["tradeStatus"]["sourceValueStatus"], "missing")

    def test_benchmark_hash_missing_slots_and_noncalendar_fail_closed(self):
        data = self.dataset()
        before = runner.input_identity(data, self.calendar, self.document)["benchmark"]
        data["benchmark"]["bars"] = [b for b in data["benchmark"]["bars"] if b["date"] != self.dates[130]]
        runner.validate_sample(data, self.document, self.calendar)
        after = runner.input_identity(data, self.calendar, self.document)["benchmark"]
        self.assertNotEqual(before["rawBarsSha256"], after["rawBarsSha256"])
        self.assertEqual(after["missingDates"], [self.dates[130]])
        rows, _, _ = self.extract(data, "missing-benchmark")
        self.assertTrue(all(r["return20"] is not None and r["relativeReturn20"] is None for r in rows[:3]))
        self.assertTrue(all(r["relativeReturn20CrossSectionCount"] == 0 for r in rows[:3]))
        data["benchmark"]["bars"].insert(5, {**data["benchmark"]["bars"][0], "date": "2022-01-08"})
        with self.assertRaisesRegex(ValueError, "non-calendar"):
            runner.validate_sample(data, self.document, self.calendar)

    def test_future_invalid_ohlc_amount_and_names_cannot_rewrite_earlier_features_or_eligibility(self):
        data = self.dataset()
        expected, _, _ = self.extract(data, "original")
        ignored = {"sourceHashes", "sampleManifestHash"}
        clean = lambda rows: [{k: v for k, v in r.items() if k not in ignored} for r in rows if r["asOfDate"] <= self.dates[140]]
        for number, mutation in enumerate((lambda s: s["bars"][150].update(high=1.),
                lambda s: s["bars"][150].update(amount="bad"), lambda s: s.update(name="*ST changed future name"),
                lambda s: s.update(bars=s["bars"][:141]))):
            altered = deepcopy(data)
            mutation(altered["securities"][0])
            actual, _, _ = self.extract(altered, f"future-{number}")
            self.assertEqual(clean(actual), clean(expected))

    def test_not_yet_observed_is_stable_with_or_without_future_first_bar(self):
        data = self.dataset()
        one = deepcopy(data)
        one["securities"][0]["bars"] = one["securities"][0]["bars"][150:]
        two = deepcopy(one)
        two["securities"][0]["bars"] = []
        left, _, _ = self.extract(one, "future-first")
        right, _, _ = self.extract(two, "no-first")
        self.assertEqual(left[0]["baseEligibility"], right[0]["baseEligibility"])
        self.assertEqual(left[0]["baseEligibility"]["reason"], "notObservedYet")

    def test_failure_and_success_directories_never_overwritten_or_resumed(self):
        with self.registered(), patch.object(runner, "extract", side_effect=RuntimeError("synthetic extraction failure")), self.assertRaisesRegex(RuntimeError, "synthetic"):
            runner.execute(Namespace(workspace=self.workspace, output=self.output))
        self.assertTrue((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "failure.json").exists())
        self.assertFalse((self.output / "completion.json").exists())
        before = {p: runner.source_tools.file_hash(p) for p in self.output.rglob("*") if p.is_file()}
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(before, {p: runner.source_tools.file_hash(p) for p in before})
        self.output = self.root / "successful"
        self.assertEqual(self.execute(), 0)
        with self.assertRaises(FileExistsError):
            self.execute()

    def test_post_extraction_detects_source_content_physical_and_execution_changes(self):
        original = runner.extract
        for kind in ("memory", "physical", "code"):
            self.output = self.root / kind
            def corrupted(data, *args, **kwargs):
                result = original(data, *args, **kwargs)
                if kind == "memory":
                    data["securities"][0]["bars"][0]["amount"] += 1
                elif kind == "physical":
                    self.write_source("sh600001", changed_index=130)
                return result
            before_hashes = runner.execution_hashes(self.document)
            with self.registered(), patch.object(runner, "extract", side_effect=corrupted):
                if kind == "code":
                    with patch.object(runner, "execution_hashes", side_effect=[before_hashes, {**before_hashes, "unexpected": "changed"}]), self.assertRaisesRegex(ValueError, "Execution source"):
                        runner.execute(Namespace(workspace=self.workspace, output=self.output))
                else:
                    with self.assertRaisesRegex(ValueError, "source changed"):
                        runner.execute(Namespace(workspace=self.workspace, output=self.output))
            self.assertFalse((self.output / "completion.json").exists())
            self.assertTrue((self.output / "failure.json").exists())
            self.write_source("sh600001")


if __name__ == "__main__":
    unittest.main()
