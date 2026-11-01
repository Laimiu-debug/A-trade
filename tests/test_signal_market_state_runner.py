"""Temporary panel/core integration and immutable observation-run lifecycle.

All feature/rank archives and audit receipts here are synthetic input contracts.
These tests never load the production feature panel, market files, or outcomes.
"""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from service.signal_leadership_features import FEATURE_VERSION, daily_features


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "scripts/build-signal-market-state-panel.py"
SPEC = importlib.util.spec_from_file_location("market_state_runner_tests", SOURCE)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_lines(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class MarketStateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.panel, self.output = self.root / "input-panel", self.root / "observations"
        for path in (self.panel, self.root / "scripts", self.root / "service", self.root / "docs"):
            path.mkdir()
        self.local_runner = self.root / "scripts" / SOURCE.name
        self.local_runner.write_bytes(SOURCE.read_bytes())
        self.local_core = self.root / "service/signal_market_state.py"
        self.local_core.write_bytes((REPO / "service/signal_market_state.py").read_bytes())
        self.registration = self.root / "docs/registration.json"
        self.design = self.root / "docs/design.json"
        write_json(self.design, {"synthetic": True, "newStrategyTrials": 0})
        self.symbols = ["sh600001", "sh600002", "sh600003", "sz000001"]
        self.calendar, day = [], date(2021, 10, 1)
        while len(self.calendar) < 67:
            if day.weekday() < 5:
                self.calendar.append(day.isoformat())
            day += timedelta(days=1)
        self.dates = self.calendar[-7:]
        self.doc = json.loads((REPO / "docs/signal-market-state-panel-v1.json").read_text(encoding="utf-8"))
        self.doc["design"] = {"path": "docs/design.json", "sha256": sha(self.design)}
        self.doc["sample"].update(start=self.dates[0], end=self.dates[-1], symbols=4, sessions=7,
                                  rows=28, snapshotFingerprint=digest("synthetic-only"))
        self.doc["input"]["directory"] = "input-panel"
        security_hashes = {symbol: {"retainedRawBarsSha256": digest([symbol, "bars"]),
            "sourceSha256": digest([symbol, "source"]), "datedEvidenceSha256": digest([symbol, "evidence"])}
            for symbol in self.symbols}
        self.manifest = {"featureVersion": FEATURE_VERSION, "inputs": {
            "symbols": self.symbols, "symbolCount": 4, "symbolsSha256": digest(self.symbols),
            "snapshotFingerprint": self.doc["sample"]["snapshotFingerprint"],
            "calendar": {"dates": self.calendar, "sha256": digest(self.calendar)},
            "securities": security_hashes, "benchmark": {"rawBarsSha256": digest("synthetic-benchmark")}}}
        write_json(self.panel / "manifest.json", self.manifest)
        self.manifest_hash = sha(self.panel / "manifest.json")
        self.rows = []
        for index, day in enumerate(self.calendar):
            if day not in self.dates:
                continue
            dates = self.calendar[index-60:index+1]
            for symbol in self.symbols:
                bars = [{"date": d, "priceBasisId": "raw-unadjusted", "open": 100.+i,
                    "high": 100.+i, "low": 100.+i, "close": 100.+i,
                    "volume": 1_000_000., "amount": 100_000_000.} for i, d in enumerate(dates)]
                bench = [{**bar, "open": 100., "high": 100., "low": 100., "close": 100.} for bar in bars]
                row = daily_features(symbol, day, dates, bars, bench)
                row.update(relativeReturn20Percentile=.9, relativeReturn60Percentile=.9,
                    relativeReturn20CrossSectionCount=4, relativeReturn60CrossSectionCount=4,
                    trailing20DailyStrengthPercentiles=[.9]*20, trailing20StrengthCrossSectionCounts=[4]*20,
                    strengthRankSessionDates20=dates[-20:], knownStrengthRankSessions20=20,
                    availableStrengthRankSessions20=20, meanStrengthPercentile20=.9,
                    minimumStrengthPercentile20=.9, topQuantileSessionCount20=None,
                    sampleManifestHash=self.manifest_hash, calendarHash=digest(self.calendar),
                    sourceHashes={**security_hashes[symbol], "benchmarkRawBarsSha256": digest("synthetic-benchmark")},
                    baseEligibility={"eligible": False, "reason": "insufficientListingHistory",
                        "unknown": ["historicalSt", "historicalMembership", "listingDate", "historicalTradeStatus"],
                        "certified": False, "affectsFeaturesOrRanks": False},
                    asOfEvidence={"historicalSt": {"status": "unknown"}, "tradeStatus": {"status": "unknown"}})
                self.rows.append(row)
        write_lines(self.panel / "daily-coverage.jsonl.gz", [{"date": d, "rows": 4} for d in self.dates])
        write_lines(self.panel / "warmup-coverage.jsonl.gz", [])
        write_json(self.panel / "summary.json", {"featureRows": 28, "targetSessions": 7})
        write_json(self.panel / "attempt.json", {"synthetic": True})
        with zipfile.ZipFile(self.panel / "execution-sources.zip", "w") as archive:
            archive.writestr("synthetic.txt", "Synthetic input contract, not an actual panel audit")
        self.audit = {"passed": True, "failureCount": 0, "featureRows": 28,
                      "sampleManifestHash": self.manifest_hash, "synthetic": True}
        self.refresh_panel()

    def refresh_registration(self):
        write_json(self.registration, self.doc)
        self.registration_hash = sha(self.registration)

    def refresh_panel(self):
        """Only used to bind altered synthetic inputs for rejection regressions."""
        write_lines(self.panel / "features.jsonl.gz", self.rows)
        write_json(self.panel / "independent-verification.json", self.audit)
        names = runner.PANEL_FILES - {"completion.json", "independent-verification.json"}
        self.completion = {"sampleManifestHash": self.manifest_hash, "featureRows": 28, "targetSessions": 7,
            "files": {name: {"path": name, "sha256": sha(self.panel / name)} for name in names}}
        write_json(self.panel / "completion.json", self.completion)
        self.doc["input"]["filesSha256"] = {name: sha(self.panel / name) for name in runner.PANEL_FILES}
        self.refresh_registration()

    @contextmanager
    def context(self):
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "REGISTRATION", self.registration), \
                patch.object(runner, "REGISTRATION_SHA256", self.registration_hash), \
                patch.object(runner, "__file__", str(self.local_runner)), redirect_stdout(io.StringIO()):
            yield

    def freeze(self, output=None):
        with self.context():
            return runner.freeze(output or self.output)

    def execute(self, output=None):
        with self.context():
            return runner.execute(output or self.output)

    def run_complete(self):
        self.freeze()
        return self.execute()

    def test_freeze_archives_exact_sources_without_evaluating_rows(self):
        with patch.object(runner, "MarketStateTracker", side_effect=AssertionError("freeze cannot evaluate")):
            protocol = self.freeze()
        self.assertEqual(set(p.name for p in self.output.iterdir()), {"protocol.json", "execution-sources.zip"})
        self.assertEqual(protocol["protocolHash"], digest({k: v for k, v in protocol.items() if k != "protocolHash"}))
        with zipfile.ZipFile(self.output / "execution-sources.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(protocol["implementation"]))
            for name, expected in protocol["implementation"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
        self.assertFalse(protocol["returnsCalculated"])
        self.assertEqual(protocol["newStrategyTrials"], 0)
        self.assertEqual(protocol["executionArchiveSha256"], sha(self.output / "execution-sources.zip"))

    def test_full_real_core_run_preserves_all_rows_lineage_and_eligibility_sidecar(self):
        before = {p: sha(p) for p in self.root.rglob("*") if p.is_file()}
        receipt = self.run_complete()
        self.assertEqual(before, {p: sha(p) for p in before})
        self.assertEqual((receipt["dailyRows"], receipt["roleRows"]), (7, 28))
        self.assertEqual(set(receipt["files"]), runner.OUTPUT_FILES)
        roles, daily = lines(self.output / "roles.jsonl.gz"), lines(self.output / "daily.jsonl.gz")
        self.assertEqual([(r["date"], r["symbol"]) for r in roles], [(d, s) for d in self.dates for s in self.symbols])
        self.assertEqual([r["date"] for r in daily], self.dates)
        for source, role in zip(self.rows, roles):
            self.assertEqual(role["qState"], "true")  # legacy rejection must not filter observation Q
            self.assertEqual(role["baseEligibility"], source["baseEligibility"])
            self.assertEqual(role["sourceHashes"], source["sourceHashes"])
            self.assertEqual(role["inputManifestSha256"], self.manifest_hash)
            self.assertEqual(role["inputFeatureFileSha256"], self.doc["input"]["filesSha256"]["features.jsonl.gz"])
        summary = runner.read(self.output / "summary.json")
        self.assertEqual(summary["qCounts"], {"true": 28})
        self.assertFalse(summary["returnsCalculated"] or summary["futureLabelsCreated"] or summary["certified"])
        self.assertEqual(summary["newStrategyTrials"], 0)

    def test_price_amount_and_legacy_qualification_have_independent_populations(self):
        first = self.rows[:4]
        first[0]["return20"] = None
        first[0]["completeness"]["security20"].update(complete=False, validSessions=20,
            invalidDates=[self.dates[0]], reasons=["invalidRawOHLC"])
        first[0]["featureReasons"]["return20"] = ["invalidRawOHLC"]
        first[1]["return20"] = 0.
        first[1]["amountToday"] = None
        first[1]["completeness"]["amountToday"].update(complete=False, validSessions=0,
            invalidDates=[self.dates[0]], reasons=["missingAmount"])
        first[1]["featureReasons"]["amountToday"] = ["missingAmount"]
        first[2]["return20"] = .2
        first[2]["completeness"]["benchmark20"].update(complete=False, validSessions=20,
            missingDates=[self.dates[0]], reasons=["missingDailyBar"])
        first[2]["featureReasons"]["relativeReturn20"] = ["missingDailyBar"]
        first[3]["return20"] = -.1
        for i, row in enumerate(first):
            if i != 1:
                row["amountToday"] = (i+1)*100_000_000.
            row["amountRelativeToOwnPrior20"] = float(i+1)
        self.refresh_panel()
        self.run_complete()
        daily = lines(self.output / "daily.jsonl.gz")[0]
        roles = lines(self.output / "roles.jsonl.gz")[:4]
        self.assertEqual(daily["price20"]["evaluableCount"], 3)
        self.assertEqual(daily["price20"]["medianAbsoluteReturn20"], 0.)
        self.assertEqual(daily["price20"]["positiveReturn20Breadth"], 1/3)
        self.assertEqual([daily["price20"][key] for key in ("positiveCount", "zeroCount", "negativeCount")], [1, 1, 1])
        self.assertEqual(daily["amount"]["evaluableCount"], 3)
        self.assertEqual(daily["amount"]["medianRelativeToOwnPrior20"], 3.)
        self.assertFalse(roles[0]["price20"]["evaluable"])
        self.assertTrue(roles[0]["amount"]["evaluable"])
        self.assertTrue(roles[1]["price20"]["evaluable"])
        self.assertFalse(roles[1]["amount"]["evaluable"])
        self.assertTrue(roles[2]["price20"]["evaluable"])
        self.assertEqual([r["qState"] for r in roles], ["unknown", "true", "unknown", "true"])
        self.assertTrue(all(r["baseEligibility"]["eligible"] is False for r in roles))

    def test_five_day_pairing_uses_fixed_intersection_and_no_warmup_roles(self):
        endpoints = {0: [.1, .2, .3, None], 5: [-.1, None, .2, 10.]}
        for day_index, values in endpoints.items():
            for ordinal, value in enumerate(values):
                row = self.rows[day_index*4+ordinal]
                row["return20"] = value
                if value is None:
                    row["completeness"]["security20"].update(complete=False, validSessions=20,
                        missingDates=[self.dates[day_index]], reasons=["missingDailyBar"])
                    row["featureReasons"]["return20"] = ["missingDailyBar"]
        self.refresh_panel()
        self.run_complete()
        daily = lines(self.output / "daily.jsonl.gz")
        self.assertTrue(all(not row["matched5"]["available"] and row["matched5"]["pairedCount"] is None for row in daily[:5]))
        matched = daily[5]["matched5"]
        self.assertEqual(matched["earlierDate"], self.dates[0])
        self.assertEqual((matched["todayEvaluableCount"], matched["earlierEvaluableCount"], matched["pairedCount"]), (3, 3, 2))
        self.assertEqual(matched["matchedBreadthChange5"], -50.)
        self.assertAlmostEqual(matched["matchedMedianReturnChange5"], -.15)
        self.assertEqual((matched["pairedCoverageToday"], matched["pairedCoverageEarlier"], matched["pairedCoverageFixed"]), (2/3, 2/3, .5))
        first = daily[0]["yesterdayCohort"]
        self.assertFalse(first["previousCohortAvailable"])
        self.assertIsNone(first["eligibleCount"])
        roles = lines(self.output / "roles.jsonl.gz")
        self.assertTrue(all(r["priorQState"] is None and not r["previousRecordAvailable"] for r in roles[:4]))
        self.assertEqual([r["matched5"]["paired"] for r in roles[20:24]], [True, False, True, False])

    def test_yesterday_strong_unknown_and_known_new_entrants_remain_distinct(self):
        # Yesterday: true/true/false/unknown. Today: unknown/false/true/true.
        self.rows[2]["signedPathEfficiency20"] = .2
        self.rows[3]["signedPathEfficiency20"] = None
        self.rows[3]["featureReasons"]["signedPathEfficiency20"] = ["missingPath"]
        self.rows[4]["signedPathEfficiency20"] = None
        self.rows[4]["featureReasons"]["signedPathEfficiency20"] = ["missingPath"]
        self.rows[4]["closePathMaxDrawdown20"] = .2  # known false must not override missing evidence
        self.rows[5]["signedPathEfficiency20"] = .2
        self.refresh_panel()
        self.run_complete()
        daily = lines(self.output / "daily.jsonl.gz")
        roles = lines(self.output / "roles.jsonl.gz")[4:8]
        self.assertEqual([r["transition"] for r in roles], ["unknown", "observedFailure", "knownNewChallenger", "priorStateUnknownEntrant"])
        self.assertEqual([r["qState"] for r in roles], ["unknown", "false", "true", "true"])
        self.assertIn("closePathMaxDrawdown20", roles[0]["failedPredicates"])
        cohort = daily[1]["yesterdayCohort"]
        self.assertEqual(cohort["members"], self.symbols[:2])
        self.assertEqual((cohort["eligibleCount"], cohort["continuedCount"], cohort["observedFailureCount"], cohort["unknownCount"]), (2, 0, 1, 1))
        self.assertEqual(cohort["conditionalContinuationRate"], 0.)
        self.assertEqual(cohort["observedCoverage"], .5)
        self.assertEqual((cohort["continuationLowerBound"], cohort["continuationUpperBound"]), (0., .5))
        self.assertEqual(sum(cohort["currentTrueRoles"].values()), daily[1]["qCounts"]["true"])

    def test_verified_success_reuse_does_not_reconstruct_tracker_or_rewrite_outputs(self):
        first = self.run_complete()
        saved = {p: sha(p) for p in self.output.iterdir()}
        with patch.object(runner, "MarketStateTracker", side_effect=AssertionError("cannot recompute success")):
            self.assertEqual(self.execute(), first)
        self.assertEqual(saved, {p: sha(p) for p in saved})
        with self.assertRaises(FileExistsError):
            self.freeze()

    def test_binding_rejects_missing_wrong_null_file_hash_or_failed_audit(self):
        original = deepcopy(self.doc)
        for change in (lambda d: d["input"]["filesSha256"].pop("features.jsonl.gz"),
                       lambda d: d["input"]["filesSha256"].update({"features.jsonl.gz": None}),
                       lambda d: d["input"]["filesSha256"].update({"features.jsonl.gz": "0"*64})):
            self.doc = deepcopy(original)
            change(self.doc)
            self.refresh_registration()
            with self.context(), self.assertRaises(ValueError):
                runner.load_binding()
        self.doc = original
        for values in ({"passed": False}, {"passed": 1}, {"failureCount": True}, {"failureCount": 1},
                       {"featureRows": 27}, {"sampleManifestHash": "0"*64}):
            saved = deepcopy(self.audit)
            self.audit.update(values)
            self.refresh_panel()
            with self.context(), self.assertRaises(ValueError):
                runner.load_binding()
            self.audit = saved

    def test_binding_rejects_incomplete_completion_or_unregistered_sample(self):
        self.completion["files"].pop("summary.json")
        write_json(self.panel / "completion.json", self.completion)
        self.doc["input"]["filesSha256"]["completion.json"] = sha(self.panel / "completion.json")
        self.refresh_registration()
        with self.context(), self.assertRaises(ValueError):
            runner.load_binding()
        self.refresh_panel()
        for key, value in (("symbols", 3), ("sessions", 6), ("rows", 27), ("snapshotFingerprint", "wrong")):
            original = self.doc["sample"][key]
            self.doc["sample"][key] = value
            self.refresh_registration()
            with self.context(), self.assertRaises(ValueError):
                runner.load_binding()
            self.doc["sample"][key] = original

    def test_exact_stream_population_and_order(self):
        with self.context():
            days = list(runner.iter_days(runner.load_binding()))
        self.assertEqual([d for d, _ in days], self.dates)
        self.assertTrue(all([r["symbol"] for r in rows] == self.symbols for _, rows in days))
        self.assertEqual(sum(len(rows) for _, rows in days), 28)

    def test_missing_duplicate_extra_reordered_and_wrong_source_rows_are_rejected(self):
        original = deepcopy(self.rows)
        changes = (lambda r: r.pop(), lambda r: r.insert(1, deepcopy(r[0])),
            lambda r: r.append(deepcopy(r[-1])), lambda r: r.__setitem__(slice(0, 2), list(reversed(r[:2]))),
            lambda r: r[0].update(featureVersion="wrong"), lambda r: r[0].update(sourceHashes={}),
            lambda r: r[0].update(sampleManifestHash="0"*64), lambda r: r[0].update(calendarHash="0"*64))
        for change in changes:
            self.rows = deepcopy(original)
            change(self.rows)
            self.refresh_panel()
            with self.context(), self.assertRaises(ValueError):
                list(runner.iter_days(runner.load_binding()))

    def test_gzip_eof_is_consumed_even_after_last_expected_row(self):
        path = self.panel / "features.jsonl.gz"
        path.write_bytes(path.read_bytes()[:-5])
        self.completion["files"][path.name]["sha256"] = sha(path)
        write_json(self.panel / "completion.json", self.completion)
        self.doc["input"]["filesSha256"].update({path.name: sha(path), "completion.json": sha(self.panel / "completion.json")})
        self.refresh_registration()
        self.freeze()
        with self.assertRaises((ValueError, EOFError, OSError)):
            self.execute()
        self.assertTrue((self.output / "failure.json").is_file())
        self.assertFalse((self.output / "completion.json").exists())

    def test_failed_partial_run_is_retained_and_never_automatically_restarted(self):
        actual = runner.MarketStateTracker

        class FailingTracker(actual):
            def update(inner, day, rows):
                if day == self.dates[2]:
                    raise RuntimeError("synthetic interruption")
                return super().update(day, rows)

        self.freeze()
        with patch.object(runner, "MarketStateTracker", FailingTracker), self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            self.execute()
        self.assertEqual(len(lines(self.output / "daily.jsonl.gz")), 2)
        self.assertEqual(len(lines(self.output / "roles.jsonl.gz")), 8)
        failed = runner.read(self.output / "failure.json")
        self.assertEqual((failed["dailyRowsWritten"], failed["roleRowsWritten"]), (2, 8))
        before = {p: sha(p) for p in self.output.iterdir()}
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.execute()
        self.assertEqual(before, {p: sha(p) for p in before})

    def test_corrupt_success_or_missing_receipt_entry_refuses_recomputation(self):
        self.run_complete()
        receipt_path = self.output / "completion.json"
        receipt = runner.read(receipt_path)
        for name in runner.OUTPUT_FILES:
            changed = deepcopy(receipt)
            changed["files"].pop(name)
            write_json(receipt_path, changed)
            with self.assertRaises(ValueError):
                self.execute()
        write_json(receipt_path, receipt)
        (self.output / "roles.jsonl.gz").write_bytes(b"changed")
        with patch.object(runner, "MarketStateTracker", side_effect=AssertionError("cannot recompute")), self.assertRaises(ValueError):
            self.execute()

    def test_pre_execution_code_protocol_archive_and_input_drift_are_rejected(self):
        self.freeze()
        paths = (self.local_core, self.output / "protocol.json", self.output / "execution-sources.zip",
                 self.panel / "features.jsonl.gz", self.registration, self.design)
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(path=path.name), self.assertRaises((ValueError, json.JSONDecodeError)):
                self.execute()
            path.write_bytes(original)
        self.assertFalse((self.output / "attempt.json").exists())

    def test_inflight_code_protocol_archive_and_input_drift_never_complete(self):
        actual = runner.MarketStateTracker
        for kind in ("code", "protocol", "archive", "input"):
            output = self.root / ("drift-" + kind)
            self.freeze(output)
            target = {"code": self.local_core, "protocol": output / "protocol.json",
                      "archive": output / "execution-sources.zip", "input": self.panel / "summary.json"}[kind]
            original = target.read_bytes()

            class ChangingTracker(actual):
                def update(inner, day, rows):
                    result = super().update(day, rows)
                    if day == self.dates[-1]:
                        target.write_bytes(original + b" ")
                    return result

            with self.subTest(kind=kind), patch.object(runner, "MarketStateTracker", ChangingTracker), self.assertRaises(ValueError):
                self.execute(output)
            self.assertTrue((output / "failure.json").is_file())
            self.assertFalse((output / "completion.json").exists())
            target.write_bytes(original)

    def test_wrong_output_row_count_order_or_day_is_preserved_as_failure(self):
        actual = runner.MarketStateTracker
        for kind in ("missing", "order", "day"):
            output = self.root / ("bad-output-" + kind)
            self.freeze(output)

            class BadTracker(actual):
                def update(inner, day, rows):
                    result = super().update(day, rows)
                    if kind == "missing":
                        result["roles"].pop()
                    elif kind == "order":
                        result["roles"].reverse()
                    else:
                        result["daily"]["date"] = self.dates[-1]
                    return result

            with self.subTest(kind=kind), patch.object(runner, "MarketStateTracker", BadTracker), self.assertRaises(ValueError):
                self.execute(output)
            self.assertTrue((output / "failure.json").is_file())
            self.assertFalse((output / "completion.json").exists())

    def test_constructor_failure_has_failure_receipt_and_cannot_restart(self):
        self.freeze()
        with patch.object(runner, "MarketStateTracker", side_effect=RuntimeError("synthetic construction failure")), self.assertRaisesRegex(RuntimeError, "construction failure"):
            self.execute()
        self.assertTrue((self.output / "failure.json").is_file())
        self.assertFalse((self.output / "completion.json").exists())
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.execute()

    def test_output_may_not_overlap_frozen_input(self):
        before = {p: sha(p) for p in self.panel.iterdir()}
        for output in (self.panel, self.panel / "child"):
            with self.assertRaises(ValueError):
                self.freeze(output)
        self.assertEqual(before, {p: sha(p) for p in before})


if __name__ == "__main__":
    unittest.main()
