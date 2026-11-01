"""Synthetic panel-bound integration and immutable lifecycle checks.

The tiny panel's strength archives are hand-authored detector input contracts,
not real cross-sectional research results. Its test-only audit receipt exists
solely to exercise the runner's binding/stream boundary. No production panel,
market source, network, or user account is read by these tests.
"""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from statistics import mean
import struct
import unittest
from unittest.mock import patch
import zipfile

import test_strategy_search_runner as fixtures
from service.signal_leadership_features import FEATURE_VERSION, daily_features


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("persistent_leader_runner_tests", ROOT / "scripts/run-signal-persistent-leader.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
        indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_lines(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(row) for row in stream]


class PersistentLeaderRunnerTests(unittest.TestCase):
    write_source = fixtures.StrategySearchRunnerTests.write_source

    def setUp(self):
        fixtures.StrategySearchRunnerTests.setUp(self)
        self.symbols = [f"sh60000{i}" for i in range(1, 9)] + ["sz000001"]
        self.panel = self.root / "synthetic-panel"
        self.panel.mkdir()
        for symbol in self.symbols:
            self.write_pattern(symbol)
        self.data = self.dataset()
        self.doc = json.loads((ROOT / "docs/signal-persistent-leader-candidate-v1.json").read_text(encoding="utf-8"))
        args = self.args()
        self.doc.update(status="registered-development-diagnostic", registeredAt="synthetic-test-only")
        self.doc["sample"].update(signalStart=args.start, signalEnd=args.end,
            latestPermittedObservation=args.end, warmupStart=self.dates[0],
            expectedFullSnapshotFingerprint=self.data["fingerprint"], expectedFullSecurities=len(self.symbols),
            expectedFullBars=sum(len(s["bars"]) for s in self.data["securities"]),
            expectedScoredSessions=40, expectedFeatureRows=40 * len(self.symbols))
        self.build_panel()

    def write_pattern(self, symbol, *, future_change=False):
        result = bytearray()
        for i, day in enumerate(self.dates):
            price = 1000 + min(i, 129)
            if i >= 130:
                price = (1220, 1230, 1320, 1430, 1550, 1650)[i - 130] if i < 136 else 1650
            if future_change and i == 185:
                price = 5000
            result.extend(struct.pack("<IIIIIfII", int(day.replace("-", "")), price, price, price, price,
                100_000_000., 10_000_000, 0))
        (self.source / "vipdoc" / symbol[:2] / "lday" / (symbol + ".day")).write_bytes(result)

    def args(self, **changes):
        result = dict(workspace=self.workspace, output=self.output, start=self.dates[130], end=self.dates[169],
            max_symbols=None, diagnostic_unverified_history=True)
        result.update(changes)
        return Namespace(**result)

    def dataset(self):
        args = self.args()
        data = runner.runtime.snapshot_universe(self.workspace, {"mode": "all", "source": "local", "markets": ["sh", "sz"],
            "boards": ["main"], "benchmarkSymbol": "sh000300"}, args.end, args.start)
        return runner._base_bound_snapshot(data, args.start, args.end)

    def build_panel(self):
        data, calendar = self.data, self.data["calendar"]
        security_identity = {}
        for security in data["securities"]:
            evidence = {k: v for k, v in security.items() if k not in ("bars", "name", "sourceSha256", "forceSeed", "historyWindow")}
            security_identity[security["symbol"]] = {
                "retainedRawBarsSha256": digest(security["bars"]), "sourceSha256": security.get("sourceSha256"),
                "datedEvidenceSha256": digest(evidence)}
        identity = {"snapshotFingerprint": data["fingerprint"], "fullDatasetContentSha256": digest(data),
            "symbols": self.symbols, "symbolCount": len(self.symbols), "symbolsSha256": digest(self.symbols),
            "retainedBarCount": sum(len(s["bars"]) for s in data["securities"]), "securities": security_identity,
            "calendar": {"dates": calendar, "sha256": digest(calendar)},
            "benchmark": {"symbol": "sh000300", "rawBarsSha256": digest(data["benchmark"]["bars"])}}
        with zipfile.ZipFile(self.panel / "execution-sources.zip", "w") as archive:
            archive.writestr("synthetic-fixture.txt", b"Not a real panel extraction or audit")
        self.manifest = {"schema": "raw-leadership-panel-manifest-v1", "featureVersion": FEATURE_VERSION,
            "inputs": identity, "physicalSourceHashes": {str(p.resolve()): file_hash(p)
                for p in [self.workspace / "market.sqlite3", *sorted(self.source.rglob("*.day"))]},
            "registration": {"sample": deepcopy(self.doc["sample"])},
            "implementation": {"synthetic-fixture.txt": hashlib.sha256(b"Not a real panel extraction or audit").hexdigest()},
            "executionArchiveSha256": file_hash(self.panel / "execution-sources.zip"), "certified": False}
        write_json(self.panel / "manifest.json", self.manifest)
        self.manifest_hash = file_hash(self.panel / "manifest.json")
        by_symbol = {s["symbol"]: {b["date"]: b for b in s["bars"]} for s in data["securities"]}
        benchmark = {b["date"]: b for b in data["benchmark"]["bars"]}
        self.rows = []
        for i, day in enumerate(calendar):
            if not self.args().start <= day <= self.args().end:
                continue
            dates = calendar[i - 60:i + 1]
            for ordinal, symbol in enumerate(self.symbols):
                raw = [{**by_symbol[symbol][d], "priceBasisId": "raw-unadjusted"} for d in dates]
                bench = [{**benchmark[d], "priceBasisId": "raw-unadjusted"} for d in dates]
                row = daily_features(symbol, day, dates, raw, bench)
                strength = .95 if ordinal < 7 else .5
                relative60 = [.90, .91, .92, .93, .94, .96, .96, .5, .5][ordinal]
                row.update(baseEligibility={"eligible": True, "reason": "eligible", "unknown": ["historicalSt", "membership"],
                        "certified": False, "affectsFeaturesOrRanks": False},
                    relativeReturn20Percentile=strength, relativeReturn60Percentile=relative60,
                    relativeReturn20CrossSectionCount=len(self.symbols), relativeReturn60CrossSectionCount=len(self.symbols),
                    trailing20DailyStrengthPercentiles=[strength]*20,
                    trailing20StrengthCrossSectionCounts=[len(self.symbols)]*20,
                    strengthRankSessionDates20=dates[-20:], knownStrengthRankSessions20=20,
                    availableStrengthRankSessions20=20, meanStrengthPercentile20=mean([strength]*20),
                    minimumStrengthPercentile20=strength, topQuantileSessionCount20=None,
                    sampleManifestHash=self.manifest_hash, calendarHash=identity["calendar"]["sha256"],
                    sourceHashes={**security_identity[symbol], "benchmarkRawBarsSha256": identity["benchmark"]["rawBarsSha256"]},
                    certified=False, comparableActionEvidence="unverified", historicalEligibilityVerified=False)
                self.rows.append(row)
        write_lines(self.panel / "daily-coverage.jsonl.gz", [
            {"asOfDate": d, "sampleManifestHash": self.manifest_hash, "rows": len(self.symbols)}
            for d in calendar if self.args().start <= d <= self.args().end])
        write_lines(self.panel / "warmup-coverage.jsonl.gz", [])
        write_json(self.panel / "summary.json", {"featureRows": len(self.rows), "targetSessions": 40,
            "sampleManifestHash": self.manifest_hash, "certified": False})
        write_json(self.panel / "attempt.json", {"synthetic": True})
        self.audit = {"passed": True, "failureCount": 0, "featureRows": len(self.rows),
            "sampleManifestHash": self.manifest_hash, "testOnly": True}
        self.rebind_panel()

    def rebind_panel(self):
        """Test-only registration of deliberately changed synthetic evidence."""
        write_lines(self.panel / "features.jsonl.gz", self.rows)
        write_json(self.panel / "independent-verification.json", self.audit)
        names = ("manifest.json", "features.jsonl.gz", "daily-coverage.jsonl.gz", "warmup-coverage.jsonl.gz",
            "summary.json", "execution-sources.zip", "attempt.json")
        self.completion = {"schema": "raw-leadership-panel-completion-v1", "sampleManifestHash": self.manifest_hash,
            "featureRows": 40 * len(self.symbols), "targetSessions": 40,
            "files": {name: {"path": name, "sha256": file_hash(self.panel / name)} for name in names}}
        write_json(self.panel / "completion.json", self.completion)
        self.doc["registrationPrerequisites"].update(panelDirectory=str(self.panel),
            panelManifestSha256=self.manifest_hash, panelCompletionSha256=file_hash(self.panel / "completion.json"),
            actualPanelAuditSha256=file_hash(self.panel / "independent-verification.json"),
            panelFilesSha256={name: file_hash(self.panel / name) for name in (*names, "independent-verification.json")})

    @contextmanager
    def registered(self):
        with patch.object(runner, "registration", return_value=self.doc):
            yield

    def collect(self, destination=None):
        destination = destination or self.output
        destination.mkdir(parents=True)
        with self.registered():
            return runner.collect_signals(self.data, runner.settings(self.args()), destination)

    def execute(self, args=None):
        with self.registered(), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.execute(args or self.args())

    def attempt(self, number=1):
        return self.output / "attempts" / f"attempt-{number:04d}"

    def test_binding_rejects_absent_or_wrong_hashes_before_any_evaluation(self):
        for key in ("panelManifestSha256", "panelCompletionSha256", "actualPanelAuditSha256"):
            for value in (None, "", "0"*64):
                with self.subTest(key=key, value=value):
                    doc = deepcopy(self.doc)
                    doc["registrationPrerequisites"][key] = value
                    with self.assertRaises(ValueError):
                        runner.verify_panel_binding(doc)
        doc = deepcopy(self.doc)
        del doc["registrationPrerequisites"]["panelFilesSha256"]["features.jsonl.gz"]
        with self.assertRaises(ValueError):
            runner.verify_panel_binding(doc)

    def test_binding_rejects_failed_audit_even_when_new_hashes_match(self):
        for changed in ({"passed": False}, {"failureCount": 1}, {"passed": 1}, {"failureCount": None},
                        {"featureRows": 359}, {"sampleManifestHash": "0"*64}):
            with self.subTest(changed=changed):
                original = deepcopy(self.audit)
                self.audit.update(changed)
                self.rebind_panel()
                with self.assertRaises(ValueError):
                    runner.verify_panel_binding(self.doc)
                self.audit = original

    def test_stream_is_complete_ordered_and_exactly_bound(self):
        with self.registered():
            binding = runner.verify_panel_binding(self.doc)
            days = list(runner.iter_panel_days(binding))
        self.assertEqual(len(days), 40)
        self.assertEqual([day for day, _ in days], self.dates[130:170])
        self.assertTrue(all([row["symbol"] for row in rows] == self.symbols for _, rows in days))
        self.assertEqual(sum(len(rows) for _, rows in days), 360)

    def test_stream_rejects_missing_duplicate_extra_and_reordered_rows(self):
        original = deepcopy(self.rows)
        mutations = (lambda r: r.pop(), lambda r: r.insert(1, deepcopy(r[0])),
            lambda r: r.append(deepcopy(r[-1])), lambda r: r.__setitem__(slice(0, 2), list(reversed(r[:2]))))
        for change in mutations:
            self.rows = deepcopy(original)
            change(self.rows)
            self.rebind_panel()
            with self.subTest(count=len(self.rows)), self.registered(), self.assertRaises(ValueError):
                list(runner.iter_panel_days(runner.verify_panel_binding(self.doc)))

    def test_stream_rejects_wrong_row_identity_version_or_provenance(self):
        original = deepcopy(self.rows[0])
        for key, value in (("asOfDate", self.dates[129]), ("symbol", "sh600999"), ("featureVersion", "wrong"),
                           ("sampleManifestHash", "0"*64), ("calendarHash", "0"*64), ("sourceHashes", {})):
            self.rows[0] = {**deepcopy(original), key: value}
            self.rebind_panel()
            with self.subTest(key=key), self.registered(), self.assertRaises(ValueError):
                list(runner.iter_panel_days(runner.verify_panel_binding(self.doc)))

    def test_gzip_tail_corruption_is_rejected_after_last_expected_row(self):
        path = self.panel / "features.jsonl.gz"
        path.write_bytes(path.read_bytes()[:-5])
        self.completion["files"][path.name]["sha256"] = file_hash(path)
        write_json(self.panel / "completion.json", self.completion)
        binding = self.doc["registrationPrerequisites"]
        binding["panelFilesSha256"][path.name] = file_hash(path)
        binding["panelCompletionSha256"] = file_hash(self.panel / "completion.json")
        with self.registered(), self.assertRaises((ValueError, EOFError, OSError)):
            list(runner.iter_panel_days(runner.verify_panel_binding(self.doc)))

    def test_full_lexicographic_ranking_precedes_top_five_and_hash_controls(self):
        before = deepcopy(self.data)
        signals, performance = self.collect()
        self.assertEqual(self.data, before)
        expected = ["sh600006", "sh600007", "sh600005", "sh600004", "sh600003", "sh600002", "sh600001"]
        candidates = [r for r in lines(self.output / "candidates.jsonl.gz") if r["signalDate"] == self.args().start]
        self.assertEqual([r["symbol"] for r in candidates], expected)
        self.assertTrue(all(r["score"] == r["rawScore"] == .95 for r in candidates))
        selection = lines(self.output / "selection.jsonl.gz")[0]
        self.assertEqual(selection["selectedTop5Symbols"], expected[:5])
        self.assertEqual([r["symbol"] for r in selection["fullRanking"]], expected)
        seed = self.doc["selection"]["controls"]["seed"]
        pool = set(self.symbols) - set(expected[:5])
        controls = sorted(pool, key=lambda symbol: (hashlib.sha256(
            f"{seed}:{self.args().start}:{runner.CANDIDATE_ID}:{symbol}".encode()).hexdigest(), symbol))[:5]
        self.assertEqual(selection["controlMatching"]["selectedSymbols"], controls)
        self.assertEqual(selection["controlMatching"]["shortfall"], 1)
        selected = [s for s in signals if s["strategyId"] == runner.CANDIDATE_ID and s["signalDate"] == self.args().start]
        self.assertEqual([s["symbol"] for s in selected], expected[:5])
        self.assertEqual(performance["evaluatedSecuritySessions"], 360)

    def test_every_status_retains_knowable_predicates_and_uses_panel_eligibility(self):
        self.rows[0]["baseEligibility"] = {"eligible": False, "reason": "historicalSt", "unknown": []}
        self.rows[1]["baseEligibility"] = {"eligible": False, "reason": "invalidHistory", "unknown": []}
        self.rebind_panel()
        self.collect()
        logged = lines(self.output / "predicates.jsonl.gz")
        self.assertEqual(len(logged), 360)
        first = {r["symbol"]: r for r in logged if r["date"] == self.args().start}
        self.assertEqual(first[self.symbols[0]]["kind"], "ineligible")
        self.assertEqual(first[self.symbols[1]]["kind"], "unevaluable")
        self.assertEqual(first[self.symbols[7]]["kind"], "notTriggered")
        self.assertEqual(first[self.symbols[2]]["kind"], "signaled")
        for symbol in self.symbols[:2]:
            self.assertTrue(all(p["passed"] is not None for p in first[symbol]["predicates"].values()))
        selection = lines(self.output / "selection.jsonl.gz")[0]
        self.assertFalse(set(self.symbols[:2]) & set(selection["controlMatching"]["selectedSymbols"]))
        self.assertEqual(selection["controlMatching"]["baseEligibleCount"], 7)
        for row in lines(self.output / "coverage.jsonl.gz"):
            self.assertEqual(sum(row["evaluatedKinds"].values()), 9)
            self.assertEqual(sum(row["evaluatedReasons"].values()), 9)

    def test_real_event_helpers_keep_t_plus_one_fees_cohorts_and_no_backfill(self):
        signals, _ = self.collect()
        result = runner.evaluate_signals(self.data, runner.settings(self.args()), signals)
        events = [e for e in result["events"] if e["strategyId"] == runner.CANDIDATE_ID]
        first = [e for e in events if e["signalDate"] == self.args().start]
        self.assertEqual([e["symbol"] for e in first], ["sh600006", "sh600007", "sh600005", "sh600004", "sh600003"])
        self.assertTrue(all(e["entry"]["date"] == self.dates[131] and e["entry"]["status"] == "filled" for e in first))
        self.assertTrue(all(e["entry"]["fee"] > 0 and e["entry"]["quantity"] % 100 == 0 for e in first))
        self.assertTrue(all(e["outcomes"]["nextOpenNet30"]["hitDate"] > e["entry"]["date"] for e in first))
        for event in first:
            entry, terminal = event["entry"], event["terminal"]
            self.assertEqual(entry["investedCapital"], entry["quantity"] * entry["price"] + entry["fee"])
            self.assertEqual(entry["remainingCash"], 100000 - entry["investedCapital"])
            self.assertGreaterEqual(terminal["proceeds"] / entry["investedCapital"] - 1, .30)
            self.assertGreater(terminal["fee"], 0)
        suppressed = [e for e in result["overlapSuppressed"] if e["strategyId"] == runner.CANDIDATE_ID]
        self.assertTrue(suppressed)
        self.assertFalse({"sh600001", "sh600002"} & {e["symbol"] for e in events})
        self.assertEqual(len(result["pathRiskEvents"]), sum(e["entry"]["status"] == "filled" for e in result["events"]))
        self.assertEqual({o["horizonSessions"] for g in result["pathRiskSummary"]["groups"] for o in g["observations"]},
            {None, *runner.runtime.HORIZONS})
        self.assertFalse(result["pathRiskSummary"]["certified"])
        group = next(g for g in result["groups"] if g["strategyId"] == runner.CANDIDATE_ID)
        for horizon in group["horizons"]:
            filled = [e for e in events if e["entry"]["status"] == "filled"]
            mature = sum(e["observation"]["availableTradingDays"] >= horizon["tradingDays"] for e in filled)
            self.assertEqual(horizon["completeCalendarCohort"], mature)
            self.assertEqual(horizon["rightCensoredBeforeHorizon"], len(filled) - mature)
        h40 = next(h for h in group["horizons"] if h["tradingDays"] == 40)
        self.assertEqual(h40["completeCalendarCohort"], 0)  # early success cannot admit an immature cohort
        self.assertIsNone(h40["proxies"]["nextOpenNet30"]["hitRate"])

    def test_unfilled_attempt_is_not_deferred_or_treated_as_an_active_holding(self):
        data = deepcopy(self.data)
        symbol = self.symbols[0]
        bar = next(b for s in data["securities"] if s["symbol"] == symbol for b in s["bars"] if b["date"] == self.dates[131])
        bar.update(open=14., high=14., low=14., close=14.)  # T+1 above the prior signal's limit
        signals = [{"strategyId": runner.CANDIDATE_ID, "symbol": symbol, "signalDate": day,
                    "rank": 1, "score": .95, "rawScore": .95} for day in self.dates[130:132]]
        result = runner.evaluate_signals(data, runner.settings(self.args()), signals)
        self.assertEqual(result["overlapSuppressedCount"], 0)
        self.assertEqual(len(result["events"]), 2)
        first, second = result["events"]
        self.assertEqual(first["entry"]["status"], "unfilled")
        self.assertEqual(first["entry"]["retryPolicy"], "noDeferredEntry")
        self.assertEqual(second["entry"]["status"], "filled")
        self.assertEqual(second["entry"]["date"], self.dates[132])

    def test_target_exit_at_open_releases_the_symbol_for_that_days_close_signal(self):
        first = {"strategyId": runner.CANDIDATE_ID, "symbol": self.symbols[0], "signalDate": self.dates[130],
                 "rank": 1, "score": .95, "rawScore": .95}
        initial = runner.evaluate_signals(self.data, runner.settings(self.args()), [first])["events"][0]
        exit_day = initial["outcomes"]["nextOpenNet30"]["hitDate"]
        self.assertIsNotNone(exit_day)
        result = runner.evaluate_signals(self.data, runner.settings(self.args()), [first,
            {**first, "signalDate": self.dates[131]}, {**first, "signalDate": exit_day}])
        self.assertEqual(result["overlapSuppressedCount"], 1)
        self.assertEqual([e["signalDate"] for e in result["events"]], [self.dates[130], exit_day])
        self.assertEqual(result["events"][1]["entry"]["date"], self.dates[self.dates.index(exit_day) + 1])

    def test_zero_candidates_still_writes_full_predicate_population_and_completes(self):
        for row in self.rows:
            row.update(trailing20DailyStrengthPercentiles=[.5]*20, relativeReturn20Percentile=.5,
                meanStrengthPercentile20=.5, minimumStrengthPercentile20=.5)
        self.rebind_panel()
        self.assertEqual(self.execute(), 0)
        self.assertEqual(lines(self.attempt() / "candidates.jsonl.gz"), [])
        self.assertEqual(lines(self.attempt() / "signals.jsonl.gz"), [])
        self.assertEqual(len(lines(self.attempt() / "predicates.jsonl.gz")), 360)
        with gzip.open(self.attempt() / "result.json.gz", "rt", encoding="utf-8") as stream:
            result = json.load(stream)
        self.assertEqual(result["eventCount"], 0)
        self.assertFalse(result["targetAchieved"])

    def test_success_receipt_covers_all_eight_artifacts_and_never_recomputes(self):
        before = {p: file_hash(p) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.execute(), 0)
        self.assertEqual(before, {p: file_hash(p) for p in before})
        receipt = json.loads((self.attempt() / "completion.json").read_text(encoding="utf-8"))
        self.assertEqual(set(receipt["files"]), {"candidates", "coverage", "signals", "result", "summary",
            "predicates", "selections", "executionSources"})
        with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("success must not recompute")):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(len(list((self.output / "attempts").iterdir())), 1)
        summary = json.loads((self.attempt() / "summary.json").read_text(encoding="utf-8"))
        self.assertNotIn("pathRiskEvents", summary)
        self.assertIn("pathRiskSummary", summary)

    def test_missing_extra_receipt_key_and_corrupt_completed_artifact_refuse_recompute(self):
        self.assertEqual(self.execute(), 0)
        receipt = json.loads((self.attempt() / "completion.json").read_text(encoding="utf-8"))
        for key in ("predicates", "selections", "executionSources"):
            changed = deepcopy(receipt)
            del changed["files"][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.verify_completion(self.output, changed, receipt["protocolHash"], receipt["snapshotFingerprint"])
        (self.attempt() / "predicates.jsonl.gz").write_bytes(b"corrupt")
        with patch.object(runner.runtime, "collect_signals", side_effect=AssertionError("must not rerun")), self.assertRaises(ValueError):
            self.execute()

    def test_panel_and_physical_source_drift_refuse_success_reuse(self):
        self.assertEqual(self.execute(), 0)
        archive = self.panel / "execution-sources.zip"
        original = archive.read_bytes()
        archive.write_bytes(original + b"changed")
        with self.assertRaises(ValueError):
            self.execute()
        archive.write_bytes(original)
        self.write_pattern(self.symbols[0], future_change=True)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(len(list((self.output / "attempts").iterdir())), 1)

    def test_changed_source_content_cannot_hide_behind_unchanged_snapshot_fingerprint(self):
        original = deepcopy(self.data)
        mutations = (lambda d: d["securities"][0]["bars"][0].update(close=1.),
            lambda d: d["securities"][0].update(name="ST synthetic current label"),
            lambda d: d["securities"][0].update(precloseByDate={self.dates[100]: 5.}))
        for number, change in enumerate(mutations):
            self.data = deepcopy(original)
            change(self.data)
            with self.subTest(number=number), self.assertRaisesRegex(ValueError, "snapshot content"):
                self.collect(self.root / f"changed-source-{number}")

    def test_inflight_protocol_archive_or_dependency_drift_never_writes_success(self):
        actual_evaluate, actual_implementation = runner.runtime.evaluate_signals, runner.implementation_hashes
        for kind in ("protocol", "archive", "dependency"):
            changed = False
            output = self.root / ("drift-" + kind)

            def evaluate(*args):
                nonlocal changed
                result = actual_evaluate(*args)
                attempt = output / "attempts" / "attempt-0001"
                if kind == "protocol":
                    path = output / "protocol.json"
                    path.write_bytes(path.read_bytes() + b" ")
                elif kind == "archive":
                    with zipfile.ZipFile(attempt / "execution-sources.zip", "a") as archive:
                        archive.writestr("unexpected.py", b"not registered")
                changed = True
                return result

            def implementation():
                values = actual_implementation()
                if changed and kind == "dependency":
                    values["service/signal_persistent_leader.py"] = "0"*64
                return values

            with self.subTest(kind=kind), patch.object(runner.runtime, "evaluate_signals", side_effect=evaluate), \
                    patch.object(runner, "implementation_hashes", side_effect=implementation), self.assertRaises(ValueError):
                self.execute(self.args(output=output))
            attempt = output / "attempts" / "attempt-0001"
            self.assertTrue((attempt / "predicates.jsonl.gz").is_file())
            self.assertFalse((attempt / "completion.json").exists())
            journal = [json.loads(r) for r in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(r["event"] == "study_failed" for r in journal))
            self.assertFalse(any(r["event"] == "study_succeeded" for r in journal))

    def test_failed_attempt_logs_are_preserved_and_explicit_resume_is_separate(self):
        with patch.object(runner.runtime, "evaluate_signals", side_effect=RuntimeError("synthetic failure")), self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            self.execute()
        failed = {p: file_hash(p) for p in self.attempt().iterdir() if p.is_file()}
        self.assertTrue((self.attempt() / "predicates.jsonl.gz").is_file())
        self.assertFalse((self.attempt() / "completion.json").exists())
        self.assertEqual(self.execute(), 0)
        self.assertEqual(failed, {p: file_hash(p) for p in failed})
        self.assertTrue((self.attempt(2) / "completion.json").is_file())

    def test_protocol_binds_new_detector_runner_panel_and_transitive_code(self):
        with self.registered():
            protocol = runner.make_protocol(self.args())
        self.assertEqual(protocol["strategyOrder"], [runner.CANDIDATE_ID])
        self.assertEqual(protocol["targetNetReturnPct"], 30)
        for path in ("scripts/run-signal-persistent-leader.py", "service/signal_persistent_leader.py",
                     "service/signal_target_study.py", "service/signal_path_risk.py", "service/research_target.py"):
            self.assertEqual(protocol["implementation"][path], file_hash(ROOT / path))
        self.assertIn(self.manifest_hash, json.dumps(protocol))

    def test_no_period_subset_pilot_or_implicit_diagnostic_execution(self):
        for changes in ({"start": self.dates[131]}, {"end": self.dates[168]},
                        {"max_symbols": 2}, {"diagnostic_unverified_history": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.execute(self.args(**changes))
        self.assertFalse(self.output.exists())
        for flags in (["--start", "2022-02-01"], ["--end", "2023-12-30"], ["--max-symbols", "64"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["--workspace", str(self.workspace), "--output", str(self.output), *flags])


if __name__ == "__main__":
    unittest.main()
