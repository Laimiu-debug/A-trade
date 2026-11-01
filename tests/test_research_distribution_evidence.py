"""Only hand-authored wire rows; no provider requests or actual response scans."""
from copy import deepcopy
import hashlib
import json
import unittest

from service.research_distribution_evidence import normalize_dividend_row


def fixture():
    return {"code": "sz.002460", "dividPreNoticeDate": "2022-03-31", "dividAgmPumDate": "2022-06-15",
            "dividPlanAnnounceDate": "2022-03-31", "dividPlanDate": "2022-06-24",
            "dividRegistDate": "2022-07-01", "dividOperateDate": "2022-07-04", "dividPayDate": "2022-07-04",
            "dividStockMarketDate": "2022-07-04", "dividCashPsBeforeTax": "0.3", "dividCashPsAfterTax": "0.27或0.3",
            "dividStocksPs": "0.000000", "dividCashStock": "synthetic: 10转4派3元", "dividReserveToStockPs": "0.400000"}


SOURCE = {"provider": "baostock", "url": "https://www.baostock.com/mainContent?file=dividInfo.md", "responseHash": "a" * 64}


def parse(row=None, **kwargs):
    return normalize_dividend_row(fixture() if row is None else row, symbol=kwargs.get("symbol", "sz002460"),
                                  source=kwargs.get("source", SOURCE))


class ResearchDistributionEvidenceTests(unittest.TestCase):
    def test_gross_stock_and_capitalization_remain_independent(self):
        value = parse()
        self.assertEqual(value["cashGrossPerShare"], .3)
        self.assertEqual(value["stockDividendPerShare"], 0.)
        self.assertEqual(value["capitalizationPerShare"], .4)
        self.assertEqual(value["status"], "parsed")
        self.assertEqual(value["planAnnouncementDate"], "2022-03-31")
        self.assertEqual(value["planDate"], "2022-06-24")
        self.assertEqual(value["shareListingDate"], "2022-07-04")
        self.assertFalse(value["issuerTermsVerified"])

    def test_blank_and_missing_are_unknown_not_zero_or_inferred_from_text(self):
        row = fixture()
        row.update(dividCashPsBeforeTax="1", dividReserveToStockPs="", dividStockMarketDate="", dividPreNoticeDate=" ")
        del row["dividPlanAnnounceDate"]
        value = parse(row)
        self.assertIsNone(value["capitalizationPerShare"])
        self.assertIsNone(value["shareListingDate"])
        self.assertIsNone(value["planAnnouncementDate"])
        self.assertEqual(value["stockDividendPerShare"], 0.)
        self.assertIn("capitalizationPerShare", value["unknownFields"])
        self.assertIn("dividPlanAnnounceDate", value["missingSourceFields"])
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["rawRow"]["dividCashStock"], row["dividCashStock"])

    def test_after_tax_alternatives_and_single_value_never_become_net_cash(self):
        for text in ("0.27或0.3", "0.9或1", " 0.27 或 0.3 ", "0.3"):
            row = fixture()
            row["dividCashPsAfterTax"] = text
            value = parse(row)
            self.assertEqual(value["cashAfterTaxText"], text)
            self.assertIsNone(value["cashNetPerShare"])
            self.assertIsNone(value["cashAvailableAt"])
            self.assertFalse(value["executionInstructionGenerated"])
            self.assertNotIn("cashTaxModel", value)

    def test_invalid_dates_are_retained_and_chronology_is_separate(self):
        for bad in ("2022-02-30", "2022-7-04", "20220704", 20220704, True):
            row = fixture()
            row["dividOperateDate"] = bad
            value = parse(row)
            self.assertEqual(value["status"], "invalid")
            self.assertIsNone(value["exDate"])
            self.assertIn("exDate", value["invalidFields"])
            self.assertEqual(value["rawRow"]["dividOperateDate"], bad)
        row = fixture()
        row.update(dividRegistDate="2022-07-04", dividStockMarketDate="2022-07-01")
        value = parse(row)
        self.assertEqual(len(value["chronologyIssues"]), 2)
        self.assertEqual(value["exDate"], "2022-07-04")
        self.assertEqual(value["invalidFields"], {})
        self.assertEqual(value["status"], "invalid")

    def test_invalid_numeric_values_not_repaired_or_dropped(self):
        for bad in ("NaN", "Infinity", "-0.1", "1e999", "1e-999", "4%", "--", True, [], {}):
            row = fixture()
            row["dividReserveToStockPs"] = bad
            value = parse(row)
            self.assertEqual(value["status"], "invalid")
            self.assertIsNone(value["capitalizationPerShare"])
            self.assertIn("capitalizationPerShare", value["invalidFields"])
            self.assertEqual(value["rawRow"]["dividReserveToStockPs"], bad)
        row = fixture()
        row.update(dividReserveToStockPs="0", dividStocksPs=".25", dividCashPsBeforeTax="3e-1")
        value = parse(row)
        self.assertEqual((value["capitalizationPerShare"], value["stockDividendPerShare"], value["cashGrossPerShare"]), (0., .25, .3))

    def test_security_identity_is_strict_and_never_assigned_to_missing_code(self):
        self.assertEqual(parse(symbol="sz.002460")["symbol"], "sz002460")
        for symbol in ("sh002460", "SZ002460", "002460", "sz002460 ", None, True):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                parse(symbol=symbol)
        for code in (None, "", "sz002460", "sz.002461", "sh.002460", "SZ.002460"):
            row = fixture()
            row["code"] = code
            with self.subTest(code=code), self.assertRaises(ValueError):
                parse(row)

    def test_source_hash_validation_and_source_identity_changes_content(self):
        for source in ({}, {"provider": "other"}, {"provider": "baostock", "responseHash": "short"},
                       {"provider": "baostock", "responseHash": "z" * 64},
                       {"provider": "baostock", "url": "file:///tmp/source"}, "", None):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse(source=source)
        first = parse()
        second = parse(source={**SOURCE, "responseHash": "b" * 64})
        self.assertEqual(first["rawRowSha256"], second["rawRowSha256"])
        self.assertNotEqual(first["sourceSha256"], second["sourceSha256"])
        self.assertNotEqual(first["contentSha256"], second["contentSha256"])
        self.assertEqual(parse(source="isolated-fixture-source")["source"], "isolated-fixture-source")
        self.assertFalse(first["responseHashVerified"])

    def test_input_and_returned_raw_source_do_not_alias(self):
        row, source = fixture(), {**SOURCE, "evidence": {"tags": ["fixture"]}}
        before_row, before_source = deepcopy(row), deepcopy(source)
        value = parse(row, source=source)
        self.assertEqual(row, before_row)
        self.assertEqual(source, before_source)
        value["rawRow"]["dividCashPsBeforeTax"] = "999"
        value["source"]["evidence"]["tags"].append("changed")
        self.assertEqual(row, before_row)
        self.assertEqual(source, before_source)

    def test_content_hash_is_deterministic_recomputable_and_sensitive_to_raw_text(self):
        first = parse()
        reordered = parse(dict(reversed(list(fixture().items()))), source=dict(reversed(list(SOURCE.items()))))
        self.assertEqual(first, reordered)
        payload = deepcopy(first)
        checksum = payload.pop("contentSha256")
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(checksum, expected)
        row = fixture()
        row["dividReserveToStockPs"] = "0.4"
        changed = parse(row)
        self.assertEqual(first["capitalizationPerShare"], changed["capitalizationPerShare"])
        self.assertNotEqual(first["rawRowSha256"], changed["rawRowSha256"])

    def test_extra_fields_preserved_and_non_json_identity_inputs_rejected(self):
        row = fixture()
        row["newProviderField"] = {"text": "unknown semantics"}
        value = parse(row)
        self.assertEqual(value["unexpectedSourceFields"], ["newProviderField"])
        self.assertEqual(value["rawRow"]["newProviderField"], row["newProviderField"])
        self.assertFalse(value["historicalCompletenessCertified"])
        self.assertFalse(value["economicLedgerReady"])
        self.assertFalse(value["pointInTimeTermsVerified"])
        self.assertIsNone(value["knownByDate"])
        with self.assertRaises(ValueError):
            parse(list(row.values()))
        row["dividCashPsBeforeTax"] = float("nan")
        with self.assertRaises(ValueError):
            parse(row)


if __name__ == "__main__":
    unittest.main()
