"""Focused checks of independent classification, not market-performance tests."""
import importlib.util
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load("_persistent_leader_verifier_test", ROOT / "scripts/verify-signal-persistent-leader-result.py")
fixtures = load("_persistent_leader_fixture_only", ROOT / "tests/test_signal_persistent_leader.py")


class IndependentLeaderVerifierTests(unittest.TestCase):
    def test_inclusive_gates_but_strict_breakout(self):
        days, bars, row = fixtures.fixture()
        self.assertEqual(audit.expected_kind(row, days, bars)[0], "signaled")
        bars[-1].update(open=159., close=159., low=159., high=159.)
        kind, predicates = audit.expected_kind(row, days, bars)
        self.assertEqual(kind, "notTriggered")
        self.assertFalse(predicates["closeAbovePrior5High"])

    def test_only_prior_five_highs_enter_reference(self):
        days, bars, row = fixtures.fixture()
        bars[-1]["high"] = bars[-7]["high"] = 1e6
        self.assertEqual(audit.expected_kind(row, days, bars)[0], "signaled")
        bars[-6]["high"] = 1e6
        self.assertEqual(audit.expected_kind(row, days, bars)[0], "notTriggered")

    def test_legacy_rejection_and_unknown_are_distinct(self):
        days, bars, row = fixtures.fixture()
        row["baseEligibility"].update(eligible=False, reason="amount20BelowMinimum")
        self.assertEqual(audit.expected_kind(row, days, bars), ("ineligible", None))
        row["baseEligibility"]["reason"] = "invalidHistory"
        self.assertEqual(audit.expected_kind(row, days, bars), ("unevaluable", None))

    def test_missing_early_raw_slot_does_not_shorten_window(self):
        days, bars, row = fixtures.fixture()
        bars[0] = None
        self.assertEqual(audit.expected_kind(row, days, bars), ("unevaluable", None))

    def test_missing_benchmark_or_rank_history_cannot_pass(self):
        days, bars, row = fixtures.fixture()
        row["completeness"]["benchmark60"]["complete"] = False
        self.assertEqual(audit.expected_kind(row, days, bars), ("unevaluable", None))
        row["completeness"]["benchmark60"]["complete"] = True
        row["trailing20DailyStrengthPercentiles"][0] = None
        self.assertEqual(audit.expected_kind(row, days, bars), ("unevaluable", None))

    def test_unknown_path_is_not_an_observed_rejection(self):
        days, bars, row = fixtures.fixture()
        row["signedPathEfficiency20"] = None
        self.assertEqual(audit.expected_kind(row, days, bars), ("unevaluable", None))
        row["signedPathEfficiency20"] = math.nextafter(.35, -math.inf)
        kind, predicates = audit.expected_kind(row, days, bars)
        self.assertEqual(kind, "notTriggered")
        self.assertFalse(predicates["signedPathEfficiency20"])

    def test_out_of_domain_finite_feature_is_unknown(self):
        days, bars, row = fixtures.fixture()
        row["signedPathEfficiency20"] = math.nextafter(1., math.inf)
        self.assertFalse(audit.valid_feature("signedPathEfficiency20", row["signedPathEfficiency20"]))
        self.assertEqual(audit.expected_kind(row, days, bars), ("unevaluable", None))


class IndependentLeaderLifecycleTests(unittest.TestCase):
    """Run only the existing tiny source/panel fixture, never the fixed panel."""

    def setUp(self):
        import test_signal_persistent_leader_runner as integration
        self.integration = integration
        self.fixture = integration.PersistentLeaderRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def result(self):
        f, runner = self.fixture, self.integration.runner
        signals = [{"strategyId": sid, "symbol": symbol, "signalDate": f.dates[130],
                    "rank": 1, "score": .95, "rawScore": .95}
                   for sid, symbol in ((runner.CANDIDATE_ID, f.symbols[0]),
                                       ("control_" + runner.CANDIDATE_ID, f.symbols[1]))]
        return runner.evaluate_signals(f.data, runner.settings(f.args()), signals), signals

    def audit_result(self, result, signals):
        f, runner = self.fixture, self.integration.runner
        dates, calendar = f.dates[130:170], f.data["calendar"]
        raw = {s["symbol"]: {b["date"]: b for b in s["bars"]} for s in f.data["securities"]}
        securities = {s["symbol"]: s for s in f.data["securities"]}
        failed = []

        def check(ok, label, **details):
            if not ok:
                failed.append(label)

        audit.audit_outcomes(check, result, signals, dates, {d: i for i, d in enumerate(dates)},
            calendar, {d: i for i, d in enumerate(calendar)}, raw, securities, runner.settings(f.args()))
        return failed

    def test_nonempty_lifecycle_fee_and_path_replay_matches_real_helpers(self):
        result, signals = self.result()
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(len(result["pathRiskEvents"]), 2)
        self.assertTrue(all(e["entry"]["status"] == "filled" for e in result["events"]))
        self.assertEqual(self.audit_result(result, signals), [])
        changed = deepcopy(result)
        changed["events"][0]["entry"]["fee"] += 1
        self.assertIn("independent-buy-fees-and-capital", self.audit_result(changed, signals))
        changed = deepcopy(result)
        changed["pathRiskSummary"]["groups"][0]["observations"][0]["cohortCount"] += 1
        self.assertIn("independent-risk-cohort-classification", self.audit_result(changed, signals))

    def test_omitted_duplicate_or_truncated_summary_groups_are_not_silent_success(self):
        result, signals = self.result()
        for change in (
            lambda r: r.update(groups=[]),
            lambda r: r["groups"].append(deepcopy(r["groups"][0])),
            lambda r: r["groups"][0].update(horizons=[]),
            lambda r: r["groups"][0]["horizons"].append(deepcopy(r["groups"][0]["horizons"][0])),
            lambda r: r["pathRiskSummary"].update(groups=[]),
            lambda r: r["pathRiskSummary"]["groups"].append(deepcopy(r["pathRiskSummary"]["groups"][0])),
        ):
            changed = deepcopy(result)
            change(changed)
            with self.subTest(change=change):
                self.assertTrue(self.audit_result(changed, signals))

    def test_main_replays_nonempty_temporary_output_without_schema_or_scope_exceptions(self):
        f = self.fixture
        self.assertEqual(f.execute(), 0)
        with patch.object(audit, "OUTPUT", f.output), patch.object(audit, "PANEL", f.panel), redirect_stdout(io.StringIO()):
            status = audit.main()
        # The production verifier deliberately fixes 3141 stocks and 484 dates.
        # A 9-stock/40-date synthetic run must fail only these sample-size checks,
        # while still finishing hash/source/predicate/lifecycle/fee/path replay.
        self.assertEqual(status, 1)
        reports = list(f.output.glob("independent-result-verification-*.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertEqual(report["evaluatedRows"], 360)
        self.assertGreater(report["candidateCount"], 0)
        self.assertGreater(report["events"], 0)
        self.assertGreater(report["checks"], 3000)
        allowed = {"actual-panel-replay-passed", "484-development-dates", "source-counts",
                   "daily-all-row-accounting", "complete-panel-and-predicate-EOF"}
        self.assertTrue({item["check"] for item in report["issues"]} <= allowed, report["issues"])
        self.assertEqual(report["failureCount"], 44)


if __name__ == "__main__":
    unittest.main()
