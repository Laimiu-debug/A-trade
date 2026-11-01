"""Independent observation-verifier tests using only isolated synthetic panels."""
from contextlib import contextmanager
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_signal_market_state_runner as fixtures


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("market_state_independent_verifier_tests",
    ROOT / "scripts/verify-signal-market-state-panel.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class MarketStateVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MarketStateRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    @contextmanager
    def context(self, verifier=None):
        f = self.fixture
        verifier = audit if verifier is None else verifier
        with patch.object(verifier, "REGISTRATION", "docs/registration.json"), \
                patch.object(verifier, "REGISTRATION_SHA256", f.registration_hash), \
                patch.object(verifier, "IMPLEMENTATION", {"docs/registration.json",
                    "scripts/build-signal-market-state-panel.py", "service/signal_market_state.py"}):
            yield

    def verify(self):
        f = self.fixture
        with self.context():
            return audit.verify(f.output, f.panel, f.root)

    def rehash_output(self):
        """Integrity hashes alone must not substitute for independent arithmetic."""
        f = self.fixture
        receipt = fixtures.runner.read(f.output / "completion.json")
        for name, entry in receipt["files"].items():
            entry["sha256"] = fixtures.sha(f.output / name)
        fixtures.write_json(f.output / "completion.json", receipt)

    def assert_rejected(self):
        report = self.verify()
        self.assertIs(report["passed"], False, report)
        self.assertGreater(report["failureCount"], 0)

    def test_nonempty_full_verify_replays_all_rows_and_leaves_sources_unchanged(self):
        f = self.fixture
        f.run_complete()
        before = {p: fixtures.sha(p) for p in f.root.rglob("*") if p.is_file()}
        report = self.verify()
        self.assertIs(report["passed"], True, report)
        self.assertEqual(report["failureCount"], 0)
        self.assertEqual(report["verifierSha256"], fixtures.sha(audit.VERIFIER_SOURCE_PATH))
        self.assertEqual(before, {p: fixtures.sha(p) for p in before})

    def test_missing_extra_quality_groups_and_missing_zero_count_fields_are_rejected(self):
        f = self.fixture
        f.run_complete()
        original = fixtures.lines(f.output / "daily.jsonl.gz")
        for change in (lambda d: d[0]["quality"]["groups"].pop("security20"),
                       lambda d: d[0]["quality"]["groups"].update(security20Duplicate=deepcopy(d[0]["quality"]["groups"]["security20"])),
                       lambda d: d[0]["qCounts"].pop("unknown"),
                       lambda d: d[0]["yesterdayCohort"]["currentTrueRoles"].pop("continued")):
            rows = deepcopy(original)
            change(rows)
            fixtures.write_lines(f.output / "daily.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()

    def test_literal_duplicate_json_group_is_not_silently_overwritten(self):
        f = self.fixture
        f.run_complete()
        rows = fixtures.lines(f.output / "daily.jsonl.gz")
        group = json.dumps(rows[0]["quality"]["groups"]["security20"], separators=(",", ":"))
        serialized = json.dumps(rows[0], separators=(",", ":"))
        needle = '"security20":' + group
        self.assertEqual(serialized.count(needle), 1)
        serialized = serialized.replace(needle, needle + ',' + needle, 1)
        with gzip.open(f.output / "daily.jsonl.gz", "wt", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
            for row in rows[1:]:
                stream.write(json.dumps(row) + "\n")
        self.rehash_output()
        self.assert_rejected()

    def test_rehashed_numeric_price_amount_and_matched_denominator_changes_are_rejected(self):
        f = self.fixture
        f.run_complete()
        original = fixtures.lines(f.output / "daily.jsonl.gz")
        changes = (lambda d: d[0]["price20"].update(positiveReturn20Breadth=.25),
            lambda d: d[0]["amount"].update(medianRelativeToOwnPrior20=2.),
            lambda d: d[5]["matched5"].update(pairedCount=3),
            lambda d: d[5]["matched5"].update(matchedBreadthChange5=10.),
            lambda d: d[0]["quality"]["groups"]["security20"].update(unknownTradeStatusCount=0))
        for change in changes:
            rows = deepcopy(original)
            change(rows)
            fixtures.write_lines(f.output / "daily.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()

    def test_coherent_rehashed_q_and_summary_change_still_fails_source_replay(self):
        f = self.fixture
        f.run_complete()
        roles = fixtures.lines(f.output / "roles.jsonl.gz")
        daily = fixtures.lines(f.output / "daily.jsonl.gz")
        summary = fixtures.runner.read(f.output / "summary.json")
        roles[0].update(qState="false", transition="initial:false")
        daily[0]["qCounts"].update(true=3, false=1)
        daily[0]["yesterdayCohort"]["currentTrueRoles"]["priorStateUnknownEntrant"] = 3
        summary["qCounts"].update(true=27, false=1)
        fixtures.write_lines(f.output / "roles.jsonl.gz", roles)
        fixtures.write_lines(f.output / "daily.jsonl.gz", daily)
        fixtures.write_json(f.output / "summary.json", summary)
        self.rehash_output()
        self.assert_rejected()

    def test_rehashed_missing_duplicate_or_reordered_role_rows_fail(self):
        f = self.fixture
        f.run_complete()
        original = fixtures.lines(f.output / "roles.jsonl.gz")
        for change in (lambda r: r.pop(), lambda r: r.insert(1, deepcopy(r[0])),
                       lambda r: r.__setitem__(1, deepcopy(r[0])),
                       lambda r: r.__setitem__(slice(0, 2), list(reversed(r[:2])))):
            rows = deepcopy(original)
            change(rows)
            fixtures.write_lines(f.output / "roles.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()

    def test_rehashed_missing_or_extra_daily_rows_and_truncated_gzip_fail(self):
        f = self.fixture
        f.run_complete()
        original = fixtures.lines(f.output / "daily.jsonl.gz")
        for rows in (original[:-1], [*original, deepcopy(original[-1])]):
            fixtures.write_lines(f.output / "daily.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()
        fixtures.write_lines(f.output / "daily.jsonl.gz", original)
        path = f.output / "daily.jsonl.gz"
        path.write_bytes(path.read_bytes()[:-5])
        self.rehash_output()
        self.assert_rejected()

    def test_first_day_missing_prior_is_not_an_empty_known_cohort(self):
        f = self.fixture
        f.run_complete()
        original = fixtures.lines(f.output / "daily.jsonl.gz")
        for change in (lambda r: r[0]["yesterdayCohort"].update(eligibleCount=0),
                       lambda r: r[0]["matched5"].update(pairedCount=0),
                       lambda r: r[0]["yesterdayCohort"].update(previousCohortAvailable=True),
                       lambda r: r[4]["matched5"].update(available=True, earlierDate=f.dates[0])):
            rows = deepcopy(original)
            change(rows)
            fixtures.write_lines(f.output / "daily.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()

    def test_nonempty_fixed_intersection_with_price_amount_independence_passes(self):
        f = self.fixture
        for ordinal, value in enumerate((.1, .2, .3, None)):
            row = f.rows[ordinal]
            row["return20"] = value
            if value is None:
                row["completeness"]["security20"].update(complete=False, validSessions=20,
                    missingDates=[f.dates[0]], reasons=["missingDailyBar"])
                row["featureReasons"]["return20"] = ["missingDailyBar"]
        for ordinal, value in enumerate((-.1, None, .2, 10.)):
            row = f.rows[20+ordinal]
            row["return20"] = value
            if value is None:
                row["completeness"]["security20"].update(complete=False, validSessions=20,
                    invalidDates=[f.dates[5]], reasons=["invalidOHLC"])
                row["featureReasons"]["return20"] = ["invalidOHLC"]
        f.rows[0]["amountToday"] = None
        f.rows[0]["completeness"]["amountToday"].update(complete=False, validSessions=0,
            invalidDates=[f.dates[0]], reasons=["missingAmount"])
        f.rows[0]["featureReasons"]["amountToday"] = ["missingAmount"]
        f.refresh_panel()
        f.run_complete()
        self.assertTrue(self.verify()["passed"])
        daily = fixtures.lines(f.output / "daily.jsonl.gz")
        self.assertEqual(daily[5]["matched5"]["pairedCount"], 2)
        self.assertEqual(daily[5]["matched5"]["matchedBreadthChange5"], -50.)
        daily[5]["matched5"]["positiveBreadthEarlier"] = 2/3  # wrong, unmatched population
        fixtures.write_lines(f.output / "daily.jsonl.gz", daily)
        self.rehash_output()
        self.assert_rejected()

    def test_unknown_source_entrant_is_not_a_known_challenger(self):
        f = self.fixture
        f.rows[0]["signedPathEfficiency20"] = None
        f.rows[0]["featureReasons"]["signedPathEfficiency20"] = ["missingPath"]
        f.rows[1]["signedPathEfficiency20"] = .2
        f.refresh_panel()
        f.run_complete()
        self.assertTrue(self.verify()["passed"])
        roles = fixtures.lines(f.output / "roles.jsonl.gz")
        self.assertEqual(roles[4]["transition"], "priorStateUnknownEntrant")
        self.assertEqual(roles[5]["transition"], "knownNewChallenger")
        roles[4]["transition"] = "knownNewChallenger"
        fixtures.write_lines(f.output / "roles.jsonl.gz", roles)
        self.rehash_output()
        self.assert_rejected()

    def test_eligibility_and_evidence_sidecar_cannot_be_silently_rewritten(self):
        f = self.fixture
        f.run_complete()
        original = fixtures.lines(f.output / "roles.jsonl.gz")
        for change in (lambda r: r[0]["baseEligibility"].update(eligible=True, unknown=[]),
                       lambda r: r[0].update(sourceHashes={}),
                       lambda r: r[0].update(priorQState="true"),
                       lambda r: r[0]["evidence"].update(knownStrengthRankSessions20=19)):
            rows = deepcopy(original)
            change(rows)
            fixtures.write_lines(f.output / "roles.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()

    def test_archive_whole_hash_is_checked_beyond_zip_member_bytes(self):
        f = self.fixture
        f.run_complete()
        path = f.output / "execution-sources.zip"
        path.write_bytes(path.read_bytes() + b"changed trailing bytes")
        self.rehash_output()
        self.assert_rejected()

    def test_rehashed_integer_boolean_substitutions_are_rejected(self):
        f = self.fixture
        f.run_complete()
        original_roles = fixtures.lines(f.output / "roles.jsonl.gz")
        for change in (lambda r: r[0]["baseEligibility"].update(eligible=0),
                       lambda r: r[0].update(previousRecordAvailable=0),
                       lambda r: r[4].update(previousRecordAvailable=1)):
            rows = deepcopy(original_roles)
            change(rows)
            fixtures.write_lines(f.output / "roles.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()
        fixtures.write_lines(f.output / "roles.jsonl.gz", original_roles)
        original_daily = fixtures.lines(f.output / "daily.jsonl.gz")
        for change in (lambda r: r[0]["qCounts"].update(unknown=False),
                       lambda r: r[0]["yesterdayCohort"].update(previousCohortAvailable=0)):
            rows = deepcopy(original_daily)
            change(rows)
            fixtures.write_lines(f.output / "daily.jsonl.gz", rows)
            self.rehash_output()
            self.assert_rejected()

    def test_verifier_source_drift_during_replay_is_rejected(self):
        f = self.fixture
        f.run_complete()
        source = f.root / "scripts/isolated-verifier.py"
        source.write_bytes(audit.VERIFIER_SOURCE_PATH.read_bytes())
        spec = importlib.util.spec_from_file_location("isolated_market_state_verifier", source)
        isolated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated)
        original_hash = fixtures.sha(source)
        original_replay = isolated.audit_rows

        def replay_then_change_source(*args, **kwargs):
            result = original_replay(*args, **kwargs)
            source.write_bytes(source.read_bytes() + b"\n# source drift during verification\n")
            return result

        with self.context(isolated), patch.object(isolated, "audit_rows", replay_then_change_source):
            report = isolated.verify(f.output, f.panel, f.root)
        self.assertIs(report["passed"], False)
        self.assertEqual(report["verifierSha256"], original_hash)
        self.assertTrue(any(issue["check"] == "verifier-source-hash-at-end" for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
