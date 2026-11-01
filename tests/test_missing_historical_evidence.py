"""Offline tests only; never contact the provider."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("missing_history_probe", ROOT / "scripts" / "supplement-missing-historical-evidence.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class MissingHistoryEvidenceTests(unittest.TestCase):
    def test_selection_uses_exact_undotted_symbol_and_no_order(self):
        symbols = ["sz000606", "sz002699", "sh600321", "sh600767", "sh600290", "sh600297", "sz000150", "sz002610", "sh600001"]
        expected = sorted(symbols, key=lambda s: (hashlib.sha256((probe.SEED + ":" + s).encode()).hexdigest(), s))[:8]
        self.assertEqual(probe.selected_symbols(list(reversed(symbols))), expected)
        with self.assertRaises(ValueError):
            probe.selected_symbols(symbols + [symbols[0]])
        with self.assertRaises(ValueError):
            probe.selected_symbols(["sh.600001"])

    def test_exact_48_requests_with_raw_fields_and_no_membership_request(self):
        source = {"auditSha256": "test", "missingUnionSymbols": [f"sh600{i:03d}" for i in range(125)]}
        frozen = probe.registration(source)
        self.assertEqual(len(frozen["requests"]), 48)
        self.assertEqual(len({r["id"] for r in frozen["requests"]}), 48)
        self.assertFalse(frozen["automaticReplacementAllowed"])
        for item in frozen["selected"]:
            requests = [r for r in frozen["requests"] if r["parameters"]["code"] == item["code"]]
            self.assertEqual([r["api"] for r in requests], ["query_stock_basic", "query_history_k_data_plus", "query_adjust_factor"] + ["query_dividend_data"] * 3)
            self.assertEqual(requests[1]["parameters"], {"code": item["code"], "fields": probe.DAILY_FIELDS, "start_date": "2021-08-02", "end_date": "2023-12-31", "frequency": "d", "adjustflag": "3"})
            self.assertEqual([r["parameters"]["year"] for r in requests[3:]], ["2021", "2022", "2023"])

    @unittest.skipUnless(probe.AUDIT.exists(), "Optional local frozen source evidence is absent")
    def test_frozen_source_audit_reconstructs_all_missing_sets_offline(self):
        source = probe.verify_sources()
        self.assertEqual(source["missingUnionCount"], 125)
        self.assertEqual([s["missing"] for s in source["dailySnapshots"]], [125, 95, 90, 56])
        self.assertEqual(probe.selected_symbols(source["missingUnionSymbols"]), ["sz000606", "sz002699", "sh600321", "sh600767", "sh600290", "sh600297", "sz000150", "sz002610"])

    def test_freeze_does_not_execute_and_refuses_overwrite_or_source_drift(self):
        source = {"auditSha256": "test", "missingUnionSymbols": [f"sh600{i:03d}" for i in range(125)]}
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "batch"), patch.object(probe, "verify_sources", return_value=source):
            probe.freeze()
            self.assertEqual({p.name for p in probe.OUTPUT.iterdir()}, {"protocol.json", "request-manifest.json"})
            self.assertEqual(len(probe.verify_frozen()["requests"]), 48)
            with self.assertRaises(ValueError):
                probe.freeze()
            altered = copy.deepcopy(source)
            altered["auditSha256"] = "changed"
            with patch.object(probe, "verify_sources", return_value=altered), self.assertRaises(ValueError):
                probe.verify_frozen()

    def fixture(self, output, *, complete=True):
        request = probe.requests_for("sh.600001")[0]
        fields = ["code", "outDate", "status"]
        rows = [["sh.600001", "2022-05-01", "0"]] if complete else []
        page = {"api": request["api"], "parameters": request["parameters"], "fields": fields, "rows": rows, "sdkPageNumber": "1"}
        output.mkdir()
        page_hash = probe.write(output / "page.json", page, frozen=True)
        pages = [{"path": "page.json", "sha256": page_hash, "rowCount": len(rows)}]
        payload = {**page, "pages": pages, "complete": complete, "errorCode": "0" if complete else "provider-blocked"}
        aggregate_hash = probe.write(output / "raw.json", payload, frozen=True)
        protocol_hash = probe.write(output / "protocol.json", {"limitations": ["unverified"]}, frozen=True)
        registered = {"sourceAuditSha256": "source", "selected": [{"symbol": "sh600001", "code": "sh.600001"}], "requests": probe.requests_for("sh.600001")}
        probe.write(output / "request-manifest.json", registered, frozen=True)
        record = {**request, "pages": pages, "path": "raw.json", "sha256": aggregate_hash, "complete": complete, "rowCount": len(rows)}
        manifest = {"protocol": {"sha256": protocol_hash}, "wireSends": 3, "requests": [record], "status": "stopped", "auth": {"logout": {"errorCode": "0"}}}
        probe.write(output / "manifest.json", manifest, frozen=True)
        return registered

    def test_validation_preserves_basic_outdate_and_unattempted_requests(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "batch"):
            registered = self.fixture(probe.OUTPUT)
            with patch.object(probe, "verify_frozen", return_value=registered):
                result = probe.validate_output()
            self.assertEqual(result["details"][0]["basicRows"], [{"code": "sh.600001", "outDate": "2022-05-01", "status": "0"}])
            self.assertEqual(result["pageFilesVerified"], 1)
            self.assertEqual(len(result["unattemptedQueries"]), 5)
            self.assertIsNone(result["details"][0]["dailyComplete"])
            self.assertFalse(result["certified"])

    def test_provider_failure_is_preserved_without_substitution(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "batch"):
            registered = self.fixture(probe.OUTPUT, complete=False)
            with patch.object(probe, "verify_frozen", return_value=registered):
                result = probe.validate_output()
            self.assertEqual(result["completeQueries"], 0)
            self.assertEqual(result["details"][0]["allRequests"][0]["errorCode"], "provider-blocked")
            self.assertEqual(result["selectedSymbols"], ["sh600001"])

    def test_corrupted_page_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(probe, "OUTPUT", Path(temp) / "batch"):
            registered = self.fixture(probe.OUTPUT)
            (probe.OUTPUT / "page.json").write_bytes(b"{}")
            with patch.object(probe, "verify_frozen", return_value=registered), self.assertRaisesRegex(ValueError, "Page hash mismatch"):
                probe.validate_output()


if __name__ == "__main__":
    unittest.main()
