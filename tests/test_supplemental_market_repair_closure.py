"""External schema correction tests; only temporary synthetic research data."""
from copy import deepcopy
import json
from pathlib import Path
import textwrap
import types
import unittest
from unittest.mock import patch

import test_signal_supplemental_market_repair as fixture


SOURCE = "scripts/verify-supplemental-market-repair-closure.py"


class SupplementalRepairClosureTests(unittest.TestCase):
    def test_actual_completion_schema_full_replay_and_semantic_tamper(self):
        f = fixture.SupplementalMarketRepairTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        summary = fixture.read(f.market / "summary.json")
        summary.update(schema="market-state-observation-summary-v1", returnsCalculated=False,
                       newStrategyTrials=0, certified=False, targetAchieved=False)
        fixture.write_json(f.market / "summary.json", summary)
        f.rebind()
        completion = fixture.read(f.market / "completion.json")
        completion.pop("certified")
        completion.pop("targetAchieved")
        fixture.write_json(f.market / "completion.json", completion)
        f.market_audit["resultFileHashes"]["completion.json"] = fixture.sha(f.market / "completion.json")
        fixture.write_json(f.market / "independent-verification.json", f.market_audit)
        f.doc["marketInput"]["completion"] = f.ref(f.market / "completion.json")
        f.doc["marketInput"]["actualAudit"] = f.ref(f.market / "independent-verification.json")
        fixture.write_json(f.registration, f.doc)
        f.registration_hash = fixture.sha(f.registration)
        self.assertEqual(f.run_complete(), 0)
        original = fixture.module("synthetic_original_schema_failure", fixture.VERIFIER)
        with patch.object(original, "REGISTRATION", f.registration), patch.object(original, "REGISTRATION_SHA256", f.registration_hash):
            failed = original.verify(f.output, root=f.root)
        self.assertFalse(failed["passed"])
        self.assertEqual({i["check"] for i in failed["issues"]},
                         {"market-observations-only-flags", "supplemental-repair-audit-exception"})
        failed_path = original.save_report(f.output, failed)
        failed_sha = fixture.sha(failed_path)
        wrapper = fixture.module("synthetic_external_schema_closure", SOURCE)
        with patch.object(wrapper, "REGISTRATION", f.registration), patch.object(wrapper, "REGISTRATION_SHA256", f.registration_hash):
            report = wrapper.verify(f.output, root=f.root, failed_report=failed_path)
            self.assertTrue(report["passed"], report.get("issues"))
            self.assertTrue(report["fullIndependentReplayCompleted"])
            self.assertGreater(report["admittedSignals"], 0)
            self.assertEqual(report["dailyRows"], 40)
            self.assertEqual(report["sourceRoleRows"], 360)
            self.assertEqual(report["verifierSha256"], fixture.sha(fixture.REPO / SOURCE))
            self.assertEqual(report["originalVerifierSha256"], fixture.sha(f.root / fixture.VERIFIER))
            self.assertNotEqual(report["verifierSha256"], report["originalVerifierSha256"])
            self.assertEqual(report["originalFailureReport"]["sha256"], failed_sha)
            self.assertEqual(report["schemaCorrection"]["replacementCount"], 1)
            destination = f.root / "external-closure.json"
            self.assertEqual(wrapper.save_report(destination, report), destination)
            with self.assertRaises(FileExistsError):
                wrapper.save_report(destination, report)
            self.assertEqual(fixture.sha(failed_path), failed_sha)
            # Bind the changed artifact honestly, reaching lineage semantics
            # rather than stopping at a stale completion/journal hash.
            path = f.attempt() / "filter-decisions.jsonl.gz"
            rows = fixture.lines(path)
            control = next(r for r in rows if r["parentSignal"]["strategyId"].startswith("control_") and r["admitted"])
            control["pairedParentRank"] = 99
            fixture.write_lines(path, rows)
            receipt = fixture.read(f.attempt() / "completion.json")
            receipt["files"]["filterDecisions"]["sha256"] = fixture.sha(path)
            fixture.write_json(f.attempt() / "completion.json", receipt)
            journal_path = f.output / "events.jsonl"
            journal = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
            next(r for r in journal if r["event"] == "study_succeeded")["receipt"] = receipt
            journal_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in journal), encoding="utf-8")
            rejected = wrapper.verify(f.output, root=f.root, failed_report=failed_path)
            self.assertFalse(rejected["passed"])
            self.assertTrue(any(i["check"] == "independent-filterDecisions-row" for i in rejected["issues"]), rejected["issues"])

    def test_exact_schema_flags_reject_every_true_and_nonboolean_optional_flag(self):
        wrapper = fixture.module("external_flags_reference", SOURCE)
        # Execute the exact correction text, not a second copied predicate.
        namespace = {}
        fragment = "def check_flags(protocol, receipt, proof, summary):\n    directory = Path('.')\n" + wrapper.CORRECTED_FRAGMENT + "    return True\n"
        def require(condition, label):
            if not condition:
                raise ValueError(label)
        namespace.update(Path=Path, audit=types.SimpleNamespace(require=require))
        exec(fragment, namespace)
        protocol = dict(protocolHash="x", returnsCalculated=False, newStrategyTrials=0, certified=False, targetAchieved=False)
        summary = dict(protocol, schema="market-state-observation-summary-v1")
        receipt = dict(schema="market-state-observation-completion-v1", returnsCalculated=False, newStrategyTrials=0)
        proof = dict(protocol)
        def run(objects):
            namespace["read"] = lambda path: objects["summary"]
            return namespace["check_flags"](objects["protocol"], objects["receipt"], objects["proof"], objects["summary"])
        originals = dict(protocol=protocol, summary=summary, receipt=receipt, proof=proof)
        self.assertTrue(run(originals))
        for name in ("protocol", "summary", "proof", "receipt"):
            for field in ("returnsCalculated", "newStrategyTrials", "certified", "targetAchieved"):
                with self.subTest(document=name, field=field):
                    objects = deepcopy(originals)
                    objects[name][field] = True
                    with self.assertRaises(ValueError):
                        run(objects)
        for optional in ("certified", "targetAchieved"):
            objects = deepcopy(originals)
            objects["receipt"][optional] = False
            self.assertTrue(run(objects))
            for value in (None, 0, "false"):
                objects["receipt"][optional] = value
                with self.assertRaises(ValueError):
                    run(objects)

    def test_patch_is_exact_single_fragment_in_frozen_source(self):
        wrapper = fixture.module("external_patch_identity", SOURCE)
        module, correction = wrapper.corrected_module(fixture.REPO)
        self.assertEqual(correction["replacementCount"], 1)
        self.assertEqual(correction["originalVerifierSha256"], fixture.sha(fixture.REPO / fixture.VERIFIER))
        self.assertNotEqual(correction["originalSourceTextSha256"], correction["effectiveSourceTextSha256"])
        source = (fixture.REPO / fixture.VERIFIER).read_text(encoding="utf-8")
        self.assertEqual(correction["effectiveSourceTextSha256"], wrapper.text_sha(source.replace(wrapper.ORIGINAL_FRAGMENT, wrapper.CORRECTED_FRAGMENT)))
        with patch.object(wrapper, "ORIGINAL_FRAGMENT", wrapper.ORIGINAL_FRAGMENT + "not present"):
            with self.assertRaisesRegex(ValueError, "exactly once"):
                wrapper.corrected_module(fixture.REPO)
        with patch.object(wrapper, "ORIGINAL_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                wrapper.corrected_module(fixture.REPO)


if __name__ == "__main__":
    unittest.main()
