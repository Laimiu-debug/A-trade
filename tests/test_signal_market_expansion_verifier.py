"""Synthetic independent result replay; never reads actual trial outcomes."""
from contextlib import contextmanager
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_signal_market_expansion_runner as fixtures


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_market_expansion_verifier_tests",
    ROOT / "scripts/verify-signal-market-expansion-result.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class MarketExpansionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MarketExpansionRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_complete()
        self.attempt = self.fixture.output / "attempts/attempt-0001"

    @contextmanager
    def context(self, module=verifier):
        f = self.fixture
        with patch.object(module, "REGISTRATION", f.registration.relative_to(f.root).as_posix()), \
             patch.object(module, "REGISTRATION_SHA256", fixtures.file_hash(f.registration)):
            yield

    def verify(self, module=verifier):
        f = self.fixture
        with self.context(module):
            return module.verify(f.output, f.root, dataset=deepcopy(f.data))

    def rehash(self):
        receipt = fixtures.read(self.attempt / "completion.json")
        for entry in receipt["files"].values():
            entry["sha256"] = fixtures.file_hash(self.fixture.output / entry["path"])
        fixtures.write_json(self.attempt / "completion.json", receipt)

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

    def rejected(self):
        report = self.verify()
        self.assertIs(report["passed"], False)
        self.assertGreater(report["failureCount"], 0)
        return report

    def test_nonempty_end_to_end_independent_replay_is_read_only(self):
        f = self.fixture
        before = {str(p): fixtures.file_hash(p) for p in f.output.rglob("*") if p.is_file()}
        report = self.verify()
        self.assertTrue(report["passed"], report["issues"])
        self.assertEqual(report["dailyRows"], 40)
        self.assertGreater(report["admittedSignals"], 0)
        self.assertGreater(report["filledEvents"], 0)
        self.assertEqual(report["verifierSha256"], fixtures.file_hash(verifier.VERIFIER_SOURCE_PATH))
        self.assertEqual(before, {str(p): fixtures.file_hash(p) for p in f.output.rglob("*") if p.is_file()})
        self.assertFalse((f.output / "independent-result-verification.json").exists())

    def test_rehashed_gate_unknown_priority_predicates_and_types_are_rejected(self):
        path = self.attempt / "market-gates.jsonl.gz"
        original = fixtures.lines(path)
        for mutate in (lambda r: r[0].update(marketGate="false"),
                       lambda r: r[5]["predicates"]["price20.coverage"].update(passed=1),
                       lambda r: r[5].update(sourceMarketRowSha256="0"*64),
                       lambda r: r[7].update(unknownReasons=[])):
            rows = deepcopy(original)
            mutate(rows)
            fixtures.write_lines(path, rows)
            self.rehash()
            self.rejected()

    def test_missing_duplicate_reordered_or_mapped_parent_decisions_are_rejected(self):
        path = self.attempt / "filter-decisions.jsonl.gz"
        original = fixtures.lines(path)
        variants = [original[:-1], original+[original[-1]], [original[1], original[0], *original[2:]]]
        changed = deepcopy(original)
        changed[0]["sourceSignalKey"][0] = verifier.CANDIDATE_ID
        variants.append(changed)
        for rows in variants:
            fixtures.write_lines(path, rows)
            self.rehash()
            self.rejected()

    def test_control_signals_with_false_topfive_flag_must_be_retained(self):
        path = self.attempt / "signals.jsonl.gz"
        original = fixtures.lines(path)
        self.assertTrue(any(r["strategyId"].startswith("control_") and r["selectedTop5"] is False for r in original))
        fixtures.write_lines(path, [r for r in original if r["selectedTop5"]])
        self.rehash()
        self.rejected()

    def test_full_parent_candidate_and_daily_logs_are_compared_after_rehash(self):
        for filename in ("candidates.jsonl.gz", "selection.jsonl.gz", "coverage.jsonl.gz"):
            path = self.attempt / filename
            original = fixtures.lines(path)
            fixtures.write_lines(path, original[1:])
            self.rehash()
            self.rejected()
            fixtures.write_lines(path, original)
            self.rehash()

    def test_coherent_result_and_summary_event_fee_target_path_cohort_changes_fail(self):
        original = verifier.gz_json(self.attempt / "result.json.gz")
        filled_index = next(i for i, e in enumerate(original["events"]) if e["entry"]["status"] == "filled")
        variants = []
        for mutate in (lambda r: r["events"][filled_index]["entry"].update(fee=9999.),
                       lambda r: r["events"].append(deepcopy(r["events"][filled_index])),
                       lambda r: r["pathRiskEvents"][0].update(grossMaePct=-99.),
                       lambda r: r["groups"][0]["horizons"][0].update(completeCalendarCohort=999),
                       lambda r: r["events"][filled_index]["signal"].update(parentStrategyId="wrong")):
            result = deepcopy(original)
            mutate(result)
            variants.append(result)
        for result in variants:
            self.replace_result(result)
            self.rejected()

    def test_missing_proxy_and_extra_event_cannot_hide_in_summary(self):
        original = verifier.gz_json(self.attempt / "result.json.gz")
        for mutate in (lambda r: r["groups"][0]["horizons"][0]["proxies"].pop("netHigh30"),
                       lambda r: r["events"][0]["outcomes"].pop("rawHigh30"),
                       lambda r: r["groups"].append(deepcopy(r["groups"][0]))):
            result = deepcopy(original)
            mutate(result)
            self.replace_result(result)
            self.rejected()

    def test_whole_zip_tail_and_truncated_gzip_are_not_receipt_only_checks(self):
        path = self.fixture.output / "execution-sources.zip"
        original = path.read_bytes()
        path.write_bytes(original+b"trailing drift")
        self.rehash()
        self.rejected()
        path.write_bytes(original)
        stream = self.attempt / "filter-decisions.jsonl.gz"
        stream.write_bytes(stream.read_bytes()[:-8])
        self.rehash()
        self.rejected()

    def test_duplicate_json_key_is_rejected(self):
        path = self.attempt / "market-gates.jsonl.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            original = stream.read()
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(original.replace('"marketGate":', '"marketGate":"true","marketGate":', 1))
        self.rehash()
        self.rejected()

    def test_failure_reports_never_replace_success_and_verify_does_not_write(self):
        success = {"passed": True, "checks": 1}
        output = self.fixture.output
        first = verifier.save_report(output, success)
        frozen = first.read_bytes()
        failure = verifier.save_report(output, {"passed": False, "failureCount": 1})
        repeated = verifier.save_report(output, success)
        self.assertNotEqual(first, failure)
        self.assertNotEqual(first, repeated)
        self.assertEqual(first.read_bytes(), frozen)
        self.assertIn("failed", failure.name)

    def test_verifier_and_shared_audit_source_drift_are_rejected(self):
        f = self.fixture
        source = f.root / "scripts/isolated-expansion-verifier.py"
        source.write_bytes(verifier.VERIFIER_SOURCE_PATH.read_bytes())
        spec = importlib.util.spec_from_file_location("isolated_expansion_verifier_test", source)
        isolated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated)
        original = isolated.event_audit
        def mutate_after_audit(*args, **kwargs):
            result = original(*args, **kwargs)
            source.write_bytes(source.read_bytes()+b"\n# drift\n")
            return result
        with patch.object(isolated, "event_audit", mutate_after_audit):
            report = self.verify(isolated)
        self.assertFalse(report["passed"])
        self.assertTrue(any(i["check"] == "verifier-source-at-end" for i in report["issues"]))
        shared = f.root / verifier.OLD_AUDITOR
        shared.write_bytes(shared.read_bytes()+b"\n# changed shared audit\n")
        self.rejected()


if __name__ == "__main__":
    unittest.main()

