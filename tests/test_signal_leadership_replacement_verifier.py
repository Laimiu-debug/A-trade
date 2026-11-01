"""Independent verifier tests on synthetic data only."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from contextlib import ExitStack
import json
import zipfile

import test_signal_leadership_replacement as pure_fixture

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "scripts/verify-signal-leadership-replacement-result.py"


def load():
    spec = importlib.util.spec_from_file_location("_replacement_verifier_test", ROOT / SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReplacementVerifierTests(unittest.TestCase):
    def completed_fixture(self):
        import test_signal_leadership_replacement_runner as runner_fixture
        f = runner_fixture.LeadershipReplacementRunnerTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        # The synthetic period changes the execution dates and therefore the
        # canonical config hash; bind that test contract before freezing.
        f.document["executionConfigHash"] = runner_fixture.digest(f.document["frozenExecutionConfig"])
        # Older producer fixtures intentionally used a small source marker and
        # terse observation receipts. Supply the full real-schema contract in
        # this temporary fixture, without weakening the independent verifier.
        manifest = runner_fixture.read(f.parent.panel / "manifest.json")
        with zipfile.ZipFile(f.parent.panel / "execution-sources.zip") as archive:
            for name, expected in manifest["implementation"].items():
                path = f.root / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(name))
                self.assertEqual(runner_fixture.sha(path), expected)
        market = f.inherited.market
        summary = runner_fixture.read(market / "summary.json")
        summary.update(returnsCalculated=False, newStrategyTrials=0,
                       certified=False, targetAchieved=False)
        runner_fixture.write_json(market / "summary.json", summary)
        original_rebind = f.inherited.rebind

        def complete_source_contract():
            original_rebind()
            path = market / "independent-verification.json"
            proof = runner_fixture.read(path)
            proof.update(certified=False, targetAchieved=False)
            runner_fixture.write_json(path, proof)
            f.inherited.doc["marketInput"]["actualAudit"]["sha256"] = runner_fixture.sha(path)
            runner_fixture.write_json(f.inherited.registration, f.inherited.doc)

        f.inherited.rebind = complete_source_contract
        f.rebind()
        self.assertEqual(f.run_complete(), 0)
        v = load()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(v, "REGISTRATION", f.registration))
        stack.enter_context(patch.object(v, "REGISTRATION_SHA256", f.registration_hash))
        stack.enter_context(patch.object(v, "CLARIFICATION", f.clarification))
        stack.enter_context(patch.object(v, "CLARIFICATION_SHA256", f.clarification_hash))
        return f, v, runner_fixture

    @staticmethod
    def rehash_result(f, helpers, key):
        receipt = helpers.read(f.attempt()/"completion.json")
        receipt["files"][key]["sha256"] = helpers.sha(f.output/receipt["files"][key]["path"])
        helpers.write_json(f.attempt()/"completion.json", receipt)
        path = f.output/"events.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[-1]["receipt"] = receipt
        path.write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows), encoding="utf-8")

    def test_complete_nonempty_independent_replay_and_rank_semantic_tamper(self):
        f, v, helpers = self.completed_fixture()
        with patch.object(f.runner, "collect_signals", side_effect=AssertionError("verifier cannot call producer")), \
                patch.object(f.runner, "evaluate_signals", side_effect=AssertionError("verifier cannot calculate events")):
            report = v.verify(f.output, root=f.root)
        self.assertTrue(report["passed"], report.get("issues"))
        self.assertGreater(report["admittedSignals"], 0)
        self.assertEqual(report["dailyRows"], len(f.target_dates))
        self.assertEqual(report["sourceRoleRows"], len(f.target_dates)*len(f.symbols))
        self.assertEqual(report["sourceFeatureRows"], report["sourceRoleRows"])
        self.assertEqual(report["verifierSha256"], helpers.sha(ROOT/SOURCE))
        v.save_report(f.output, report)
        self.assertEqual(f.execute(), 0)
        path = f.attempt()/"selection.jsonl.gz"
        rows = helpers.lines(path)
        target = next(row for row in rows if len(row["fullRanking"]) > 1)
        target["fullRanking"][0], target["fullRanking"][1] = target["fullRanking"][1], target["fullRanking"][0]
        helpers.write_lines(path, rows)
        self.rehash_result(f, helpers, "selections")
        rejected = v.verify(f.output, root=f.root)
        self.assertFalse(rejected["passed"])
        self.assertTrue(any(item["check"] == "independent-selections-row" for item in rejected["issues"]), rejected["issues"])

    def test_whole_population_predicates_duplicates_truncation_and_extra_eof(self):
        f, v, helpers = self.completed_fixture()
        path = f.attempt()/"predicates.jsonl.gz"
        original = helpers.lines(path)
        for name, rows in (("missing", original[:-1]), ("duplicate", original[:1]+original[:-1]), ("extra", original+[original[-1]])):
            with self.subTest(change=name):
                helpers.write_lines(path, rows)
                self.rehash_result(f, helpers, "predicates")
                report = v.verify(f.output, root=f.root)
                self.assertFalse(report["passed"])
                self.assertTrue(any(item["check"] in {"independent-predicates-row", "complete-EOF:predicates"} for item in report["issues"]), report["issues"])

    def test_false_unknown_cohort_evidence_and_control_pair_tamper(self):
        f, v, helpers = self.completed_fixture()
        day_path = f.attempt()/"daily-gates.jsonl.gz"
        original_days = helpers.lines(day_path)
        days = deepcopy(original_days)
        selected = next(r for r in days if r["oldCohort"]["endpoints"])
        selected["oldCohort"]["endpoints"][0]["counts"]["unknown"] += 1
        helpers.write_lines(day_path, days)
        self.rehash_result(f, helpers, "dailyGates")
        rejected = v.verify(f.output, root=f.root)
        self.assertFalse(rejected["passed"])
        self.assertTrue(any(i["check"] == "independent-dailyGates-row" for i in rejected["issues"]), rejected["issues"])
        helpers.write_lines(day_path, original_days)
        self.rehash_result(f, helpers, "dailyGates")
        path = f.attempt()/"signals.jsonl.gz"
        signals = helpers.lines(path)
        control = next(s for s in signals if s["strategyId"].startswith("control_"))
        control["pairedStrategyRank"] = 99
        helpers.write_lines(path, signals)
        self.rehash_result(f, helpers, "signals")
        rejected = v.verify(f.output, root=f.root)
        self.assertFalse(rejected["passed"])
        self.assertTrue(any(i["check"] == "independent-signals-row" for i in rejected["issues"]), rejected["issues"])

    def test_independent_reference_cohort_bounds_and_nonempty_controls(self):
        v = load()
        rows, market = pure_fixture.fixture()
        result = v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES, 5, rows, market)
        self.assertEqual(result["daily"]["oldCohort"]["members"], pure_fixture.SYMBOLS[:4])
        self.assertEqual(result["daily"]["oldCohort"]["threshold"], 2)
        self.assertEqual(result["daily"]["oldCohort"]["state"], "true")
        self.assertEqual([r["symbol"] for r in result["rankedCandidates"]],
                         [pure_fixture.SYMBOLS[i] for i in (5, 6, 7, 8, 9, 10, 4)])
        self.assertEqual(len(result["signals"]), 10)
        self.assertEqual([s["pairedStrategyRank"] for s in result["signals"][5:]], [1, 2, 3, 4, 5])
        cases = [(["false", "false", "unknown", "unknown"], "true"),
                 (["true", "true", "true", "unknown"], "false"),
                 (["false", "true", "true", "unknown"], "unknown")]
        for states, state in cases:
            for cross in rows[1:]:
                for i, q in enumerate(states):
                    cross[i]["qState"] = q
            result = v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES, 5, rows, market)
            self.assertEqual(result["daily"]["oldCohort"]["state"], state)
            self.assertEqual(result["daily"]["oldCohort"]["denominator"], 4)
        for r in rows[0]:
            r["qState"] = "unknown"
        result = v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES, 5, rows, market)
        self.assertEqual(result["daily"]["oldCohort"]["state"], "false")
        self.assertEqual(result["daily"]["oldCohort"]["denominator"], 0)
        self.assertEqual(result["signals"], [])

    def test_independent_reference_unknown_and_causal_history(self):
        v = load()
        for index in range(5):
            rows, market = pure_fixture.fixture(index)
            result = v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES, index, rows, market)
            self.assertEqual(result["signals"], [])
            self.assertEqual(result["daily"]["candidateGateCounts"]["unknown"], len(pure_fixture.SYMBOLS))
        rows, market = pure_fixture.fixture()
        before = deepcopy((rows, market))
        baseline = v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES, 5, rows, market)
        self.assertEqual(baseline, v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES+["2024-01-09"], 5, rows, market))
        self.assertEqual((rows, market), before)
        rows[0][5]["qState"] = "unknown"
        market["price20"]["coverage"] = .1
        changed = v.reference_day(pure_fixture.SYMBOLS, pure_fixture.DATES, 5, rows, market)
        self.assertEqual(changed["evaluations"][5]["candidateGate"], "unknown")
        self.assertIn("price20.coverage", changed["evaluations"][5]["failedPredicates"])
        self.assertTrue(changed["evaluations"][5]["unknownReasons"])


if __name__ == "__main__":
    unittest.main()
