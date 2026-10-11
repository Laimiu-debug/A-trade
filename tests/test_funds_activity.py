import copy
import datetime as dt
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from service.funds_activity import FundsActivity


def records(amounts, start=dt.date(2025, 1, 1)):
    return b"".join(struct.pack("<IIIIIfII", int((start + dt.timedelta(days=i)).strftime("%Y%m%d")),
                                1000 + i, 1200 + i, 900 + i, 1100 + i, amount, 100, 0)
                    for i, amount in enumerate(amounts))


def write_names(root, names):
    path = root / "T0002/hq_cache/shs.tnf"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(50)
    for code, name in names.items():
        row = bytearray(360)
        row[:6] = code.encode("ascii")
        text = name.encode("gbk")
        row[31:31 + len(text)] = text
        data.extend(row)
    path.write_bytes(data)


class Online:
    def __init__(self, root):
        self.root, self.calls, self.result = str(root), [], None

    def source(self):
        return self.root

    def sectors(self):
        self.calls.append("sectors")
        return {"items": [{"symbol": "sh881376", "name": "数字媒体"}]}

    def bars(self, payload):
        self.calls.append(payload)
        if self.result is None:
            raise ValueError("终端未连接")
        return copy.deepcopy(self.result)


class Owner:
    def __init__(self, root):
        self.root = str(root)
        self.db = sqlite3.connect(":memory:")
        self.online = Online(root)

    def _get(self, key):
        return self.root if key == "path" else None


class FundsActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "selected"
        (self.root / "vipdoc/sh/lday").mkdir(parents=True)
        write_names(self.root, {"881376": "数字媒体", "880301": "煤炭", "600900": "长江电力"})
        self.owner = Owner(self.root)
        self.service = FundsActivity(self.owner)

    def tearDown(self):
        self.service.close()
        self.owner.db.close()
        self.temp.cleanup()

    def day(self, symbol="sh881376", amounts=None, start=dt.date(2025, 1, 1), root=None):
        path = (root or self.root) / "vipdoc" / symbol[:2] / "lday" / (symbol + ".day")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(records(amounts if amounts is not None else [100, 200, 300, 400, 500, 600], start))
        return path

    def history(self, **kwargs):
        return self.service.history({"symbol": "sh881376", **kwargs})

    def test_local_valid_rows_need_no_online_access_and_keep_units(self):
        self.day()
        value = self.history()
        self.assertEqual(value["status"], "ready")
        self.assertEqual(value["provider"], "tdx_local")
        self.assertEqual(value["name"], "数字媒体")
        self.assertEqual(value["sourceRoot"], str(self.root.resolve()))
        self.assertEqual(value["units"]["amount"], "元")
        self.assertEqual(value["items"][-1]["amount"], 600)
        self.assertEqual(self.owner.online.calls, [])

    def test_activity_includes_today_and_requires_complete_window(self):
        self.day()
        rows = self.history()["items"]
        self.assertIsNone(rows[0]["changePercent"])
        self.assertIsNone(rows[0]["amountDelta"])
        self.assertTrue(all(row["activityPercent"] is None for row in rows[:4]))
        self.assertEqual(rows[4]["amountAverage"], 300)
        self.assertAlmostEqual(rows[4]["activityPercent"], (500 / 300 - 1) * 100)
        self.assertEqual(rows[5]["amountAverage"], 400)
        self.assertEqual(rows[5]["activityPercent"], 50)
        self.assertEqual(rows[5]["amountDelta"], 100)
        self.assertEqual(rows[0]["returnPercent"], 0)

    def test_zero_amount_is_valid_and_zero_average_remains_unknown(self):
        self.day(amounts=[0, 0, 0, 0, 0, 10, 0])
        rows = self.history()["items"]
        self.assertEqual(rows[4]["amountAverage"], 0)
        self.assertIsNone(rows[4]["activityPercent"])
        self.assertEqual(rows[-1]["activityPercent"], -100)
        self.assertEqual(rows[-1]["amountDelta"], -10)

    def test_long_history_reads_warmup_then_slices_and_rebases(self):
        self.day(amounts=list(range(1, 901)), start=dt.date(2023, 1, 1))
        value = self.history(limit=750, window=20)
        self.assertEqual(value["total"], 750)
        self.assertEqual(value["availableCount"], 900)
        self.assertEqual(value["items"][0]["amount"], 151)
        self.assertEqual(value["items"][0]["amountAverage"], sum(range(132, 152)) / 20)
        self.assertEqual(value["items"][0]["returnPercent"], 0)
        shorter = self.history(limit=30, window=5)
        self.assertTrue(shorter["cached"])
        self.assertEqual(shorter["items"][0]["amount"], 871)
        self.assertEqual(shorter["items"][0]["returnPercent"], 0)

    def test_asof_truncates_before_metrics_and_cache_does_not_leak_latest(self):
        self.day(amounts=list(range(1, 101)))
        latest = self.history()
        value = self.history(asOf="2025-02-19", limit=30)
        self.assertEqual(value["endDate"], "2025-02-19")
        self.assertEqual(value["availableCount"], 50)
        self.assertEqual(value["items"][-1]["amount"], 50)
        self.assertEqual(value["items"][-1]["amountAverage"], 48)
        self.assertNotEqual(value["asOf"], latest["asOf"])
        self.assertEqual(self.history()["items"][-1]["amount"], 100)
        self.assertEqual(self.history(asOf="2025-02-19")["items"][-1]["amount"], 50)

    def test_asof_can_read_earlier_than_latest_bounded_tail(self):
        self.day(amounts=list(range(1, 901)), start=dt.date(2023, 1, 1))
        value = self.history(asOf="2023-02-19", limit=30)
        self.assertEqual(value["availableCount"], 50)
        self.assertEqual(value["items"][-1]["amount"], 50)
        self.assertEqual(value["endDate"], "2023-02-19")

    def test_refresh_bypasses_cache_and_failed_refresh_keeps_original_date(self):
        path = self.day()
        old = self.history()
        path.write_bytes(records([700, 800, 900, 1000, 1100, 1200, 1300]))
        self.assertEqual(self.history()["asOf"], old["asOf"])
        new = self.history(refresh=True)
        self.assertEqual(new["items"][-1]["amount"], 1300)
        path.unlink()
        stale = self.history(refresh=True)
        self.assertTrue(stale["cached"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["asOf"], new["asOf"])
        self.assertEqual(stale["receivedAt"], new["receivedAt"])

    def test_different_source_never_uses_old_cache_or_old_terminal(self):
        self.day()
        self.history()
        self.owner.root = str(Path(self.temp.name) / "other")
        value = self.history()
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(value["items"], [])
        self.assertEqual(self.owner.online.calls, [])

    def test_catalogue_excludes_statistics_missing_names_and_bad_files(self):
        self.day()
        self.day("sh880301", amounts=[100] * 6)
        self.day("sh880001")
        self.day("sh880101")
        self.day("sh881999")
        self.day("sh881377").write_bytes(b"bad")
        write_names(self.root, {"881376": "数字媒体", "880301": "煤炭", "881377": "坏日线"})
        value = self.service.catalogue({})
        self.assertEqual([item["id"] for item in value["items"]], ["sh881376", "sh880301"])
        self.assertEqual(value["coverage"]["excluded"], {"statistics": 2, "unnamed": 1, "invalid": 1})
        self.assertEqual(value["coverage"]["candidates"], 4)
        self.assertEqual(self.owner.online.calls, [])

    def test_catalogue_puts_older_high_activity_after_latest_date(self):
        self.day(amounts=[0, 0, 0, 0, 100], start=dt.date(2024, 1, 1))
        self.day("sh880301", amounts=[100] * 6)
        value = self.service.catalogue({})
        self.assertEqual(value["items"][0]["symbol"], "sh880301")
        self.assertFalse(value["items"][0]["stale"])
        self.assertTrue(value["items"][1]["stale"])

    def test_catalogue_failed_refresh_preserves_same_source_snapshot(self):
        path = self.day()
        old = self.service.catalogue({})
        path.unlink()
        result = self.service.catalogue({"refresh": True})
        self.assertTrue(result["stale"])
        self.assertEqual(result["asOf"], old["asOf"])

    def test_stock_validation_and_local_history(self):
        self.day("sh600900")
        value = self.service.history({"symbol": "sh600900", "kind": "stock"})
        self.assertEqual(value["name"], "长江电力")
        self.assertEqual(value["kind"], "stock")
        self.assertEqual(value["provider"], "tdx_local")

    def test_same_source_mcp_whole_series_fallback_requires_sector_membership(self):
        self.owner.online.result = {"symbol": "sh881376", "rows": [
            {"date": f"2025-01-{day:02d}", "open": 10, "high": 12, "low": 9, "close": 11, "amount": 100 * day}
            for day in range(1, 7)]}
        value = self.history()
        self.assertEqual(value["provider"], "tdx_mcp")
        self.assertEqual(value["items"][-1]["amount"], 600)
        value = self.service.history({"symbol": "sh881999"})
        self.assertEqual(value["status"], "unavailable")

    def test_mcp_wrong_symbol_nonfinite_duplicate_future_are_rejected(self):
        valid = {"symbol": "sh881376", "rows": [
            {"date": f"2025-01-{day:02d}", "open": 10, "high": 12, "low": 9, "close": 11, "amount": 100 * day}
            for day in range(1, 7)]}
        for modify in (lambda data: data.update(symbol="sh600900"),
                       lambda data: data["rows"][0].update(amount=float("nan")),
                       lambda data: data["rows"][0].update(amount=True),
                       lambda data: data["rows"].append(data["rows"][-1]),
                       lambda data: data["rows"][-1].update(date="2200-01-01")):
            self.owner.online.result = copy.deepcopy(valid)
            modify(self.owner.online.result)
            self.assertEqual(self.history(refresh=True)["status"], "unavailable")

    def test_local_future_duplicate_truncated_and_nonfinite_rejected(self):
        path = self.day()
        for data in (records([1], dt.date(2200, 1, 1)), records([1]) * 2, b"bad", records([float("nan")])):
            path.write_bytes(data)
            self.assertEqual(self.history(refresh=True)["status"], "unavailable")

    def test_request_boundaries(self):
        for request in ({}, {"symbol": "../sh881376"}, {"symbol": "sh880001"}, {"symbol": "sh880101"},
                        {"symbol": "sh600900"}, {"symbol": "sh000001", "kind": "stock"},
                        {"symbol": "sh881376", "limit": True}, {"symbol": "sh881376", "window": 3},
                        {"symbol": "sh881376", "refresh": 1}, {"symbol": "sh881376", "asOf": "2200-01-01"},
                        {"symbol": "sh881376", "asOf": "2025-02-30"}, {"symbol": "sh881376", "other": 1}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.service.history(request)
        for request in ({"refresh": 1}, {"other": 1}, []):
            with self.assertRaises(ValueError):
                self.service.catalogue(request)


if __name__ == "__main__":
    unittest.main()
