"""Independent synthetic audit of fixed-window repair/resilience replay."""
from contextlib import contextmanager
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_signal_market_repair_runner as fixtures

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repair_independent_verifier_tests", ROOT / "scripts/verify-signal-market-repair-result.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class MarketRepairVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MarketRepairRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_complete()
        self.attempt = self.fixture.output / "attempts/attempt-0001"

    def verify(self):
        f = self.fixture
        with patch.object(verifier, "REGISTRATION", f.registration.relative_to(f.root).as_posix()), \
             patch.object(verifier, "REGISTRATION_SHA256", fixtures.file_hash(f.registration)):
            return verifier.verify(f.output, f.root, dataset=deepcopy(f.data))

    def rehash(self):
        receipt = fixtures.read(self.attempt / "completion.json")
        for entry in receipt["files"].values():
            entry["sha256"] = fixtures.file_hash(self.fixture.output / entry["path"])
        fixtures.write_json(self.attempt / "completion.json", receipt)

    def rejected(self):
        report = self.verify()
        self.assertFalse(report["passed"], report)
        self.assertGreater(report["failureCount"], 0)
        return report

    def replace_result(self, result):
        with gzip.open(self.attempt / "result.json.gz", "wt", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False)
        summary = fixtures.read(self.attempt / "summary.json")
        for key in list(summary):
            if key not in ("performance", "ranking", "selectionEligible"):
                del summary[key]
        summary.update({k: v for k, v in result.items() if k not in ("events", "overlapSuppressed", "pathRiskEvents")})
        fixtures.write_json(self.attempt / "summary.json", summary)
        self.rehash()

    def test_nonempty_full_role_replay_rank_mirror_and_read_only(self):
        f = self.fixture
        before = {str(p): fixtures.file_hash(p) for p in f.output.rglob("*") if p.is_file()}
        report = self.verify()
        self.assertTrue(report["passed"], report["issues"])
        self.assertEqual(report["sourceRoleRows"], 360)
        self.assertEqual(report["dailyRows"], 40)
        self.assertGreater(report["filledEvents"], 0)
        self.assertGreater(report["unmatchedTrueStrategySlots"], 0)
        controls = [s for s in fixtures.lines(self.attempt / "signals.jsonl.gz") if s["strategyId"].startswith("control_")]
        self.assertEqual([s["rank"] for s in controls], [1, 4])
        self.assertEqual([s["pairedParentRank"] for s in controls], [1, 4])
        self.assertEqual(before, {str(p): fixtures.file_hash(p) for p in f.output.rglob("*") if p.is_file()})
        self.assertFalse((f.output / "independent-result-verification.json").exists())

    def test_rehashed_market_window_and_unknown_precedence_tampering_fails(self):
        path = self.attempt / "market-gates.jsonl.gz"
        original = fixtures.lines(path)
        for mutate in (lambda r: r[0].update(marketGate="false"),
                       lambda r: r[10]["history"][0].update(earlierDate=self.fixture.target_dates[1]),
                       lambda r: r[10]["predicates"]["priorFiveNegativeCount"].update(value=2),
                       lambda r: r[10]["sourceMarketRowHashes"][0].update(sha256="0"*64)):
            rows = deepcopy(original)
            mutate(rows)
            fixtures.write_lines(path, rows)
            self.rehash()
            self.rejected()

    def test_candidate_role_window_source_hash_and_non_top_five_retention(self):
        path = self.attempt / "candidates.jsonl.gz"
        original = fixtures.lines(path)
        i = next(i for i, r in enumerate(original) if r["signalDate"] == self.fixture.target_dates[10])
        for mutate in (lambda r: r[i]["compoundGate"]["roleHistory"][0].update(date=self.fixture.target_dates[10]),
                       lambda r: r[i]["compoundGate"]["sourceRoleRowHashes"][0].update(sha256="0"*64),
                       lambda r: r[i]["compoundGate"].update(signalGate="false")):
            rows = deepcopy(original)
            mutate(rows)
            fixtures.write_lines(path, rows)
            self.rehash()
            self.rejected()
        self.assertTrue(any(not r["selectedTop5"] for r in original))
        fixtures.write_lines(path, [r for r in original if r["selectedTop5"]])
        self.rehash()
        self.rejected()

    def test_mirrored_controls_cannot_compress_rank_or_claim_own_q(self):
        path = self.attempt / "filter-decisions.jsonl.gz"
        original = fixtures.lines(path)
        i = next(i for i, r in enumerate(original) if r["newStrategyId"].startswith("control_") and r["pairedParentRank"] == 4)
        for mutate in (lambda r: r[i].update(pairedParentRank=2),
                       lambda r: r[i]["pairedParentSignalKey"].__setitem__(1, r[i]["parentSignal"]["symbol"]),
                       lambda r: r[i]["strategyGate"].update(symbol=r[i]["parentSignal"]["symbol"]),
                       lambda r: r.append(deepcopy(r[-1]))):
            rows = deepcopy(original)
            mutate(rows)
            fixtures.write_lines(path, rows)
            self.rehash()
            self.rejected()

    def test_missing_controls_and_changed_coverage_unmatched_accounting_fail(self):
        path = self.attempt / "signals.jsonl.gz"
        original = fixtures.lines(path)
        fixtures.write_lines(path, [r for r in original if r["selectedTop5"]])
        self.rehash()
        self.rejected()
        fixtures.write_lines(path, original)
        path = self.attempt / "coverage.jsonl.gz"
        rows = fixtures.lines(path)
        rows[10]["unmatchedTrueStrategySlots"] = 0
        fixtures.write_lines(path, rows)
        self.rehash()
        self.rejected()

    def test_coherent_result_summary_fee_path_cohort_and_population_tampering(self):
        original = verifier.gz_json(self.attempt / "result.json.gz")
        i = next(i for i, e in enumerate(original["events"]) if e["entry"]["status"] == "filled")
        for mutate in (lambda r: r["events"][i]["entry"].update(fee=9999.),
                       lambda r: r["events"].append(deepcopy(r["events"][i])),
                       lambda r: r["pathRiskEvents"][0].update(grossMaePct=-99.),
                       lambda r: r["groups"][0]["horizons"][0].update(completeCalendarCohort=99),
                       lambda r: r.update(sourceRoleRows=359)):
            result = deepcopy(original)
            mutate(result)
            self.replace_result(result)
            self.rejected()

    def test_whole_role_input_or_archive_drift_fails_before_outcome_claim(self):
        role_path = self.fixture.market / "roles.jsonl.gz"
        frozen = role_path.read_bytes()
        role_path.write_bytes(frozen+b"drift")
        self.rejected()
        role_path.write_bytes(frozen)
        archive = self.fixture.output / "execution-sources.zip"
        archive.write_bytes(archive.read_bytes()+b"drift")
        self.rehash()
        self.rejected()

    def test_duplicate_keys_and_truncated_output_are_not_accepted(self):
        path = self.attempt / "filter-decisions.jsonl.gz"
        frozen = path.read_bytes()
        path.write_bytes(frozen[:-8])
        self.rehash()
        self.rejected()
        path.write_bytes(frozen)
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            original = stream.read()
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(original.replace('"admitted":', '"admitted":true,"admitted":', 1))
        self.rehash()
        self.rejected()


if __name__ == "__main__":
    unittest.main()
