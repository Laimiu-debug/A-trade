"""Temporary synthetic input contracts, never production panels or outcomes.

Reuse a frozen fixture's real raw-source/parent hash chain. Replacement Q roles
are explicitly hand-authored test contracts, linked to the original feature
values; their synthetic audit is not a certification of actual market Q.
"""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_signal_market_repair_runner as previous


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "scripts/run-signal-leadership-replacement.py"
REGISTRATION = "docs/signal-leadership-replacement-v1.json"
CLARIFICATION = "artifacts/strategy-search/leadership-replacement-registration-clarification-v1.json"
sha, digest = previous.file_hash, previous.digest
read, write_json, write_lines, lines, ref = previous.read, previous.write_json, previous.write_lines, previous.lines, previous.ref


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class LeadershipReplacementRunnerTests(unittest.TestCase):
    """Public fixture for independent-verifier integration tests."""
    def setUp(self):
        self.runner = module("replacement_runner_under_test", SOURCE)
        inherited = self.inherited = previous.MarketRepairRunnerTests()
        inherited.setUp()
        self.addCleanup(inherited.doCleanups)
        for key in ("root", "workspace", "data", "symbols", "target_dates", "parent", "market", "market_protocol"):
            setattr(self, key, getattr(inherited, key))
        self.output = self.root / "replacement-output"
        self.registration = self.root / REGISTRATION
        self.clarification = self.root / CLARIFICATION
        self.document = read(ROOT / REGISTRATION)
        self.doc = self.document
        for key in ("sample", "frozenExecutionConfig", "eventPolicy", "auditAndEvaluation"):
            self.document[key] = deepcopy(inherited.doc[key])
        self.document["executionConfigHash"] = digest(self.document["frozenExecutionConfig"])
        self.document["sourceImplementation"] = deepcopy(inherited.doc["parent"]["sourceImplementation"])
        self.document["inputBindingRegistration"] = {"path": inherited.registration.relative_to(self.root).as_posix(), "sha256": sha(inherited.registration)}
        names = set(self.document["sourceImplementation"]) | set(self.document["inputBindingImplementation"])
        names.update(self.runner.AUDIT_REFERENCES)
        names.update([SOURCE, self.document["independentVerifier"]["path"], *self.document["implementationFreeze"]["tests"]])
        names.update(self.document[key]["path"] for key in ("proposal", "pureRule", "prototypeReview", "registrationGenerator"))
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.market_rows = inherited.market_rows
        raw = {(row["asOfDate"], row["symbol"]): row for row in self.parent.rows}
        self.role_rows = inherited.role_rows
        for index, market in enumerate(self.market_rows):
            market["qDefinition"] = "persistent-price-strength-five-gates-v1"
            market["matched5"]["matchedBreadthChange5"] = None if index < 5 else 0.
        for role in self.role_rows:
            index = self.target_dates.index(role["date"])
            symbol_index = self.symbols.index(role["symbol"])
            role["qState"] = ("true" if index % 5 == 3 else "false") if symbol_index >= 7 else (
                "false" if index % 5 == 3 else "true")
            role["criterionValues"] = self.runner.criterion_values(raw[role["date"], role["symbol"]])
        self.rebind()

    def rebind(self):
        self.inherited.market_rows, self.inherited.role_rows = self.market_rows, self.role_rows
        self.inherited.rebind()
        self.document["inputBindingRegistration"] = {"path": self.inherited.registration.relative_to(self.root).as_posix(), "sha256": sha(self.inherited.registration)}
        write_json(self.registration, self.document)
        self.registration_hash = sha(self.registration)
        explanation = read(ROOT / CLARIFICATION)
        explanation['newRegistration'] = {'path': REGISTRATION, 'sha256': self.registration_hash}
        for key in ('inputBindingRegistration', 'proposal', 'pureRule', 'prototypeReview'):
            explanation[key] = deepcopy(self.document[key])
        explanation['preservedSourceSections']['sourceRegistration'] = deepcopy(self.document['inputBindingRegistration'])
        explanation['preservedSourceSections']['newRegistrationWholeFileSha256BeforeAndAfter'] = self.registration_hash
        write_json(self.clarification, explanation)
        self.clarification_hash = sha(self.clarification)

    def rebind_features(self):
        """Re-sign only a deliberately changed synthetic feature/input chain."""
        parent, inherited = self.parent, self.inherited
        parent.rebind_panel()
        panel_files = {p.name: sha(p) for p in parent.panel.iterdir() if p.is_file()}
        raw = inherited.doc["originalRawSourcePanelBinding"]
        raw["files"] = {name: ref(parent.panel/name) for name in panel_files}
        old_protocol = read(parent.output / "protocol.json")
        old_protocol["panelBinding"]["panelFilesSha256"] = {name: panel_files[name] for name in old_protocol["panelBinding"]["panelFilesSha256"]}
        old_protocol["panelBinding"]["panelCompletionSha256"] = panel_files["completion.json"]
        write_json(parent.output / "protocol.json", old_protocol)
        receipt = read(parent.attempt() / "completion.json")
        receipt["protocolHash"] = digest(old_protocol)
        receipt["panelBinding"] = deepcopy(old_protocol["panelBinding"])
        write_json(parent.attempt() / "completion.json", receipt)
        inherited.doc["parent"]["protocolHash"] = digest(old_protocol)
        raw["identity"] = deepcopy(old_protocol["panelBinding"])
        self.market_protocol["inputFilesSha256"] = panel_files
        self.market_protocol.pop("protocolHash", None)
        self.market_protocol["protocolHash"] = digest(self.market_protocol)
        write_json(self.market/"protocol.json", self.market_protocol)
        for row in self.market_rows+self.role_rows:
            row["protocolHash"] = self.market_protocol["protocolHash"]
            row["inputFeatureFileSha256"] = panel_files["features.jsonl.gz"]
        self.rebind()

    def args(self, **changes):
        value = self.inherited.args(workspace=self.workspace, output=self.output)
        for key, item in changes.items():
            setattr(value, key, item)
        return value

    @contextmanager
    def registered(self):
        with patch.object(self.runner, "ROOT", self.root), patch.object(self.runner, "__file__", str(self.root/SOURCE)), \
                patch.object(self.runner, "REGISTRATION", self.registration), \
                patch.object(self.runner, "REGISTRATION_SHA256", self.registration_hash), \
                patch.object(self.runner, "CLARIFICATION", self.clarification), \
                patch.object(self.runner, "CLARIFICATION_SHA256", self.clarification_hash):
            yield

    def freeze(self):
        with self.registered(), redirect_stdout(io.StringIO()):
            return self.runner.freeze(self.args())

    def execute(self):
        with self.registered(), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return self.runner.execute(self.args())

    def run_complete(self):
        self.freeze()
        return self.execute()

    def attempt(self):
        return self.output / "attempts/attempt-0001"

    def collect(self, destination=None):
        destination = destination or self.root / "collected"
        destination.mkdir()
        with self.registered(), redirect_stdout(io.StringIO()):
            return self.runner.collect_signals(self.runner.load_binding(), destination)

    def test_freeze_is_exclusive_metadata_only_and_dependency_archive_is_complete(self):
        with patch.object(self.runner, "collect_signals", side_effect=AssertionError("no candidate evaluation")), \
                patch.object(self.runner, "iter_role_days", side_effect=AssertionError("no role iteration")), \
                patch.object(self.runner, "iter_market_days", side_effect=AssertionError("no market iteration")), \
                patch.object(self.runner, "iter_feature_days", side_effect=AssertionError("no feature iteration")):
            protocol = self.freeze()
        self.assertFalse((self.output/"attempts").exists())
        self.assertTrue(set(self.runner.AUDIT_REFERENCES) <= set(protocol["implementation"]))
        self.assertTrue(set(self.document["implementationFreeze"]["tests"]) <= set(protocol["implementation"]))
        self.assertEqual(protocol['clarification']['sha256'], self.clarification_hash)
        self.assertEqual(protocol['implementation'][CLARIFICATION], self.clarification_hash)
        with self.assertRaises(ValueError): self.freeze()
        with self.registered(): self.runner.legacy._check_archive(self.output/"execution-sources.zip", protocol["implementation"])

    def test_all_stock_streams_and_first_five_unknown_form_new_ranked_signals(self):
        with patch.object(self.runner.legacy, "iter_parent_days", side_effect=AssertionError("parent slots forbidden")), \
                patch.object(self.runner.legacy, "collect_signals", side_effect=AssertionError("old filter forbidden")):
            signals, performance = self.collect()
        folder = self.root/"collected"
        predicates, daily = lines(folder/"predicates.jsonl.gz"), lines(folder/"daily-gates.jsonl.gz")
        self.assertEqual(len(predicates), 360)
        self.assertEqual([r["date"] for r in daily], self.target_dates)
        self.assertTrue(all(r["candidateGate"] == "unknown" for r in predicates[:45]))
        self.assertEqual(performance["sourceRoleRows"], 360)
        self.assertEqual(sum(performance["candidateGateCounts"].values()), 360)
        first = next(r for r in lines(folder/"selection.jsonl.gz") if r["rawCandidateCount"])
        self.assertEqual(first["date"], self.target_dates[5])
        self.assertEqual(first["rawCandidateCount"], 7)
        self.assertEqual(first["selectedTop5Symbols"], [self.symbols[i] for i in (5, 6, 4, 3, 2)])
        self.assertEqual(first["controlMatching"]["shortfall"], 1)
        self.assertTrue(signals)
        self.assertEqual(lines(folder/"signals.jsonl.gz"), signals)
        self.assertEqual(set(predicates[0]["source"]), {"roleFileSha256", "featureFileSha256", "marketProtocolHash", "panelManifestSha256",
                         "calendarHash", "marketRowSha256", "roleRowSha256", "featureRowSha256"})

    def test_missing_duplicate_extra_roles_and_feature_mismatch_fail_before_events(self):
        original = deepcopy(self.role_rows)
        for number, change in enumerate((lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[-1])),
                                        lambda rows: rows.__setitem__(1, deepcopy(rows[0])),
                                        lambda rows: rows[0]["criterionValues"].update(meanStrengthPercentile20=.01),
                                        lambda rows: rows[0]["baseEligibility"].update(eligible=False))):
            self.role_rows = deepcopy(original)
            change(self.role_rows)
            self.rebind()
            with self.subTest(case=number), self.assertRaises(ValueError): self.collect(self.root/f"invalid-{number}")
        self.freeze()
        with patch.object(self.runner, "evaluate_signals", side_effect=AssertionError("EOF must precede returns")), self.assertRaises(ValueError):
            self.execute()
        self.assertFalse((self.attempt()/"completion.json").exists())

    def test_exact_market_calendar_and_invalid_identity_cannot_become_unknown(self):
        original = deepcopy(self.market_rows)
        for number, change in enumerate((lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[-1])),
                                        lambda rows: rows[5]["matched5"].update(earlierDate=self.target_dates[1]),
                                        lambda rows: rows[0].update(featureVersion="wrong"))):
            self.market_rows = deepcopy(original)
            change(self.market_rows)
            self.rebind()
            with self.subTest(case=number), self.assertRaises(ValueError): self.collect(self.root/f"market-invalid-{number}")

    def test_legitimate_criterion_null_projection_is_accepted_without_rewriting_feature(self):
        row = self.parent.rows[0]
        row["signedPathEfficiency20"] = 1.0000000000000002
        self.role_rows[0]["criterionValues"]["signedPathEfficiency20"] = None
        self.role_rows[0]["qState"] = "unknown"
        self.rebind_features()
        feature_hash = sha(self.parent.panel/"features.jsonl.gz")
        signals, performance = self.collect()
        self.assertEqual(performance["sourceFeatureRows"], 360)
        self.assertTrue(signals)
        self.assertEqual(sha(self.parent.panel/"features.jsonl.gz"), feature_hash)
        self.assertEqual(self.parent.rows[0]["signedPathEfficiency20"], 1.0000000000000002)

    def test_new_active_groups_no_backfill_and_single_attempt_unfilled_release(self):
        signals, _ = self.collect()
        config = self.document["frozenExecutionConfig"]
        selected = [r for r in signals if r["strategyId"] == self.runner.CANDIDATE_ID]
        first = selected[0]
        waiting = {**first, "signalDate": self.target_dates[6]}
        control = {**waiting, "strategyId": "control_"+self.runner.CANDIDATE_ID}
        result = self.runner.evaluate_signals(self.data, config, [first, waiting, control])
        self.assertEqual(result["overlapSuppressedCount"], 1)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual({r["strategyId"] for r in result["events"]}, set(self.runner.GROUPS))
        self.assertEqual(len(self.runner.evaluate_signals(self.data, config, [waiting])["events"]), 1)
        self.assertTrue(all(r["rank"] <= 5 for r in selected))
        bad = deepcopy(self.data)
        next(b for s in bad["securities"] if s["symbol"] == first["symbol"] for b in s["bars"]
             if b["date"] == self.target_dates[6]).update(open=20., high=20.)
        # The following synthetic open must itself be below the unknown-ST
        # conservative limit; the source fixture otherwise jumps 12.3 -> 13.2.
        next(b for s in bad["securities"] if s["symbol"] == first["symbol"] for b in s["bars"]
             if b["date"] == self.target_dates[7]).update(open=12.4, high=12.4, low=12.4, close=12.4)
        unfilled = self.runner.evaluate_signals(bad, config, [first, waiting])
        self.assertEqual([e["entry"]["status"] for e in unfilled["events"]], ["unfilled", "filled"])
        self.assertEqual(unfilled["overlapSuppressedCount"], 0)

    def test_complete_read_only_success_reuses_only_verification_and_all_eleven_receipts(self):
        before = {p: sha(p) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.run_complete(), 0)
        self.assertEqual(before, {p: sha(p) for p in before})
        with patch.object(self.runner, "collect_signals", side_effect=AssertionError("no recompute")), \
                patch.object(self.runner, "load_dataset", side_effect=AssertionError("no reload")):
            self.assertEqual(self.execute(), 0)
        receipt = read(self.attempt()/"completion.json")
        self.assertEqual(set(receipt["files"]), self.runner.RECEIPT_KEYS)
        self.assertEqual(len(receipt["files"]), 11)
        with gzip.open(self.attempt()/"result.json.gz", 'rt', encoding='utf-8') as stream:
            result = json.load(stream)
        self.assertGreater(result["eventCount"], 0)
        self.assertFalse(result["targetAchieved"])
        self.assertEqual([len(g["horizons"]) for g in result["groups"]], [7, 7])
        (self.attempt()/"predicates.jsonl.gz").write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.execute()

    def test_failed_attempt_missing_freeze_and_source_drift_never_restart(self):
        with self.assertRaises(ValueError): self.execute()
        self.freeze()
        with patch.object(self.runner, "evaluate_signals", side_effect=RuntimeError("synthetic terminal error")):
            with self.assertRaisesRegex(RuntimeError, "synthetic terminal error"): self.execute()
        saved = {p: sha(p) for p in self.output.rglob('*') if p.is_file()}
        with patch.object(self.runner, "collect_signals", side_effect=AssertionError("no restart")), self.assertRaises(ValueError): self.execute()
        self.assertEqual(saved, {p: sha(p) for p in saved})
        self.assertEqual([p.name for p in (self.output/'attempts').iterdir()], ['attempt-0001'])
        (self.market/'daily.jsonl.gz').write_bytes(b'changed')
        with self.registered(), self.assertRaises(ValueError): self.runner.load_binding()

    def test_post_execution_dataset_or_code_drift_cannot_write_success(self):
        self.freeze()
        original = self.runner.evaluate_signals
        def changed(data, config, signals):
            result = original(data, config, signals)
            data['securities'][0]['bars'][0]['close'] += 1
            return result
        with patch.object(self.runner, 'evaluate_signals', side_effect=changed), self.assertRaises(ValueError): self.execute()
        self.assertFalse((self.attempt()/'completion.json').exists())
        self.assertTrue((self.attempt()/'failure.json').exists())

    def test_zero_candidates_preserve_all_rows_and_cli_has_no_sample_or_date_override(self):
        for row in self.role_rows:
            row['qState'] = 'false'
        self.rebind()
        self.assertEqual(self.run_complete(), 0)
        self.assertEqual(lines(self.attempt()/'signals.jsonl.gz'), [])
        self.assertEqual(len(lines(self.attempt()/'predicates.jsonl.gz')), 360)
        summary = read(self.attempt()/'summary.json')
        self.assertEqual((summary['eventCount'], len(summary['groups'])), (0, 2))
        for flag in ('--start', '--end', '--max-symbols'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): self.runner.main([flag, '1'])


if __name__ == '__main__':
    unittest.main()
