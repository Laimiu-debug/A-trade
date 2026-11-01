import datetime as dt
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from service.sector_daily import SectorDaily


class SectorDailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "vipdoc/sh/lday").mkdir(parents=True)
        owner = type("Owner", (), {"_get": lambda _, key: str(self.root) if key == "path" else None})()
        self.daily = SectorDaily(owner)
        # Non-calendar-day windows, including weekends, must use trading dates.
        self.days = [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(45)
                     if (dt.date(2026, 1, 1) + dt.timedelta(days=i)).weekday() < 5][:25]
        self.names = patch("service.sector_daily.tdx.read_names", return_value=({
            "sh881115": "林业", "sh881116": "较旧板块", "sh881117": "缺失基准", "sh881118": "坏文件"}, []))
        self.names.start()
        self.write("sh881115", list(enumerate(self.days)))

    def tearDown(self):
        self.names.stop()
        self.tmp.cleanup()

    def write(self, symbol, records):
        raw = b""
        for index, day in records:
            close = 10000 + index * 100
            raw += struct.pack("<IIIIIfII", int(day.replace("-", "")), close, close + 100,
                               close - 100, close, 100000 + index, 1234, 0)
        (self.root / f"vipdoc/sh/lday/{symbol}.day").write_bytes(raw)

    def snapshot(self, **kwargs):
        return self.daily.snapshot({"basis": "daily", **kwargs}, "received")

    def test_list_and_history_use_identical_cutoff_and_five_trading_day_base(self):
        day = self.days[21]
        result = self.snapshot(asOf=day)
        item = result["items"][0]
        history = self.daily.history({"id": "sh881115", "asOf": day, "limit": 21}, "received")
        self.assertEqual(result["asOf"], history["dataAsOf"])
        self.assertEqual(item["price"], history["rows"][-1]["close"])
        self.assertAlmostEqual(item["return5"], (121 / 116 - 1) * 100)
        self.assertAlmostEqual(item["return20"], (121 / 101 - 1) * 100)
        self.assertTrue(all(row["date"] <= day for row in history["rows"]))
        self.assertEqual(result["provider"], "tdx_local")
        self.assertEqual(result["sourceRoot"], history["sourceRoot"])

    def test_date_switches_do_not_reuse_latest_or_another_date(self):
        for index in (24, 18, 23, 15):
            result = self.snapshot(asOf=self.days[index])
            self.assertEqual(result["asOf"], self.days[index])
            self.assertEqual(result["items"][0]["price"], 100 + index)

    def test_current_membership_catalogue_excludes_legacy_same_name_index_by_code(self):
        self.write("sh880363", list(enumerate(self.days)))
        with patch("service.sector_daily.tdx.read_names", return_value=({"sh880363":"林业", "sh881115":"林业"}, [])):
            result=self.daily.snapshot({"basis":"daily"}, "received", member_catalogue=[{"symbol":"sh881115", "name":"林业"}])
        self.assertEqual([row["symbol"] for row in result["items"]], ["sh881115"])
        self.assertTrue(result["items"][0]["membershipAvailable"])
        self.assertEqual(result["coverage"]["outsideCurrentCatalogue"], 1)

    def test_offline_indices_keep_bars_but_cannot_claim_available_membership(self):
        result=self.snapshot()
        self.assertEqual(len(result["items"]), 1)
        self.assertFalse(result["items"][0]["membershipAvailable"])
        self.assertFalse(result["membershipCatalogueAvailable"])

    def test_requested_day_without_bars_discloses_actual_day(self):
        result = self.snapshot(asOf="2026-02-14")
        self.assertEqual(result["requestedAsOf"], "2026-02-14")
        self.assertEqual(result["asOf"], self.days[-1])
        self.assertTrue(any(self.days[-1] in warning for warning in result["warnings"]))

    def test_stale_sector_and_missing_window_baseline_do_not_enter_ranking(self):
        self.write("sh881116", list(enumerate(self.days[:-1])))
        self.write("sh881117", [(i, day) for i, day in enumerate(self.days) if i != 19])
        result = self.snapshot()
        items = {item["symbol"]: item for item in result["items"]}
        stale = items["sh881116"]
        self.assertTrue(stale["older"])
        self.assertEqual(stale["date"], self.days[-2])
        for key in ("changePercent", "return5", "return20", "amount", "price"):
            self.assertIsNone(stale[key])
        self.assertIsNone(items["sh881117"]["return5"])
        self.assertEqual(result["coverage"]["older"], 1)

    def test_invalid_file_is_excluded_and_never_replaced_with_mcp_or_members(self):
        (self.root / "vipdoc/sh/lday/sh881118.day").write_bytes(b"bad")
        result = self.snapshot()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["coverage"]["invalid"], 1)
        data = self.daily.history({"id": "sh881118", "limit": 250}, "received")
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["status"], "unavailable")

    def test_empty_prehistory_remains_empty(self):
        result = self.snapshot(asOf="2025-01-01")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["asOf"])
        self.assertEqual(result["status"], "empty")

    def test_request_dates_and_mode_are_validated(self):
        for request in ({"basis": "daily", "asOf": "2200-01-01"},
                        {"basis": "daily", "asOf": "2026-02-30"},
                        {"basis": "daily", "asOf": True},
                        {"basis": "daily", "refresh": 1}, {"basis": "live"}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.daily.snapshot(request, "received")


if __name__ == "__main__":
    unittest.main()
