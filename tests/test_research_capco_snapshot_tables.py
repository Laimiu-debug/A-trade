import unittest
from service.research_capco_snapshot_tables import industry_assignments, reconcile_assignments, reconcile_label, stock_assignments


def pages(row):
    return [{"page": 1, "tables": [[row]]}]


class PublisherTableTests(unittest.TestCase):
    def test_visible_source_clipping_requires_exact_major_and_compatible_label(self):
        stock, taxonomy = stock_assignments(pages(["003035", "南网能源", "M", "科学研究和技术服务业", "", "", "75", "科技推广和应用服务业"]))
        industry, repairs = industry_assignments(pages(["科学研究和技术服", "无", "75", "科技推广和应用服务业", "003035", "南网能源"]), taxonomy)
        members, notes = reconcile_assignments(stock, industry)
        self.assertEqual(members[0]["sectorCode"], "M75")
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["exactMajorCode"], "75")

    def test_wrong_explicit_code_or_label_is_not_silently_corrected(self):
        with self.assertRaises(ValueError):
            reconcile_label("金融业(K)", "J", "金融业", "section")
        with self.assertRaises(ValueError):
            reconcile_label("金融业", "M", "科学研究和技术服务业", "section")

    def test_unknown_major_cannot_borrow_a_nearby_group(self):
        _, taxonomy = stock_assignments(pages(["000001", "甲", "J", "金融业", "", "", "66", "货币金融服务"]))
        with self.assertRaises(ValueError):
            industry_assignments(pages(["金融业(J)", "无", "67", "资本市场服务", "000001", "甲"]), taxonomy)

    def test_display_truncation_is_retained_and_does_not_change_membership(self):
        stock = {"000729": {"code": "000729", "sectorCode": "C15", "sectorName": "酒、饮料和精制茶制造", "subclassCode": "CA"}}
        industry = {"000729": {**stock["000729"], "sectorName": "酒、饮料和精制茶制造业"}}
        members, notes = reconcile_assignments(stock, industry)
        self.assertEqual(members[0]["sectorName"], "酒、饮料和精制茶制造业")
        self.assertEqual(len(notes["rawLabelDifferences"]), 1)
        self.assertTrue(notes["displayLabelAlternatives"][0]["allShorterLabelsArePrefixes"])

    def test_real_assignment_difference_still_fails(self):
        stock = {"000001": {"code": "000001", "sectorCode": "J66", "sectorName": "货币金融服务", "subclassCode": ""}}
        with self.assertRaises(ValueError):
            reconcile_assignments(stock, {"000001": {**stock["000001"], "sectorCode": "J67"}})


if __name__ == "__main__":
    unittest.main()
