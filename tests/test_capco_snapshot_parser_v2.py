import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("_capco_parser_v2_tests", Path(__file__).resolve().parents[1] / "scripts/parse-capco-sector-snapshots-v2.py")
PARSER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PARSER)


class ParserRepairTests(unittest.TestCase):
    def test_joined_code_and_name_matches_spaced_rows(self):
        spaced = "2025年下半年\n000001 平安银行 J 金融业 66 货币金融服务\n000002 万科A K 房地产业 70 房地产业"
        joined = spaced.replace("000001 ", "000001").replace("000002 ", "000002")
        self.assertEqual(PARSER.stock_text_reference(spaced, 1), PARSER.stock_text_reference(joined, 1))
        self.assertEqual(PARSER.stock_text_reference(joined, 1), {"000001": "J66", "000002": "K70"})

    def test_source_omission_only_resolves_exact_complete_label(self):
        label = "居民服务、修理和其他服务业"
        self.assertEqual(PARSER.resolve_industry_section(label, {label: "O"}), ("O", True))
        with self.assertRaises(ValueError):
            PARSER.resolve_industry_section("居民服务", {label: "O"})
        with self.assertRaises(ValueError):
            PARSER.resolve_industry_section(label, {})

    def test_explicit_letter_contradiction_is_not_overridden(self):
        with self.assertRaises(ValueError):
            PARSER.resolve_industry_section("金融业(K)", {"金融业": "J"})
        self.assertEqual(PARSER.resolve_industry_section("金融业（J）", {"金融业": "J"}), ("J", False))

    def test_ambiguous_same_publication_label_cannot_choose_a_code(self):
        pages = [{"tables": [[["000001", "甲", "J", "相同名称", "", "", "66", "一"], ["000002", "乙", "K", "相同名称", "", "", "70", "二"]]]}]
        with self.assertRaises(ValueError):
            PARSER.section_dictionary(pages)

    def test_ambiguous_text_major_stays_failure(self):
        with self.assertRaises(ValueError):
            PARSER.stock_text_reference("000001平安银行 J 金融业 66 67 货币金融服务", 1)


if __name__ == "__main__":
    unittest.main()
