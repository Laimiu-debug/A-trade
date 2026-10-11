import copy
import sqlite3
import unittest

from service.sector_history import SectorHistory


ROWS = [{"date": f"2026-01-{i:02d}", "open": 10 + i, "high": 12 + i, "low": 9 + i,
         "close": 11 + i, "volume": 123, "amount": 456} for i in range(1, 9)]


class Online:
    root = "source-one"
    failure = False

    def __init__(self):
        self.calls = 0
        self.catalogue_calls = 0
        self.result = {"symbol": "sh881376", "rows": copy.deepcopy(ROWS), "volumeUnit": "原始单位"}

    def source(self):
        return self.root

    def sectors(self):
        self.catalogue_calls += 1
        if self.failure:
            raise ValueError("终端不可用")
        return {"items": [{"symbol": "sh881376", "name": "数字媒体"}]}

    def bars(self, payload):
        self.calls += 1
        if self.failure:
            raise ValueError("终端不可用")
        return copy.deepcopy(self.result)


class Owner:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.online = Online()


class SectorHistoryTests(unittest.TestCase):
    def setUp(self):
        self.owner = Owner()
        self.history = SectorHistory(self.owner)

    def tearDown(self):
        self.history.close()
        self.owner.db.close()

    def test_real_index_rows_keep_dates_values_and_source_then_reuse_bounded_cache(self):
        data = self.history.history({"id": "sh881376", "limit": 3})
        self.assertEqual(data["rows"], ROWS[-3:])
        self.assertEqual(data["name"], "数字媒体")
        self.assertEqual(data["sourceRoot"], "source-one")
        self.assertEqual(data["dataAsOf"], "2026-01-08")
        self.assertFalse(data["cached"])
        self.assertEqual(self.owner.online.calls, 1)
        cache = self.history.history({"id": "sh881376", "limit": 8})
        self.assertTrue(cache["cached"])
        self.assertFalse(cache["stale"])
        self.assertEqual(cache["rows"], ROWS)
        self.assertEqual(self.owner.online.calls, 1)

    def test_failed_refresh_retains_only_same_source_history(self):
        self.history.history({"id": "sh881376"})
        self.owner.online.failure = True
        data = self.history.history({"id": "sh881376", "refresh": True})
        self.assertTrue(data["stale"])
        self.assertEqual(data["rows"], ROWS)
        self.owner.online.root = "source-two"
        data = self.history.history({"id": "sh881376"})
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(data["rows"], [])

    def test_forced_bar_refreshes_share_catalogue_until_expiry(self):
        from unittest.mock import patch
        with patch("service.sector_history.time.monotonic", return_value=100):
            for _ in range(5):
                result = self.history.history({"id": "sh881376", "refresh": True})
                self.assertFalse(result["cached"])
        self.assertEqual(self.owner.online.calls, 5)
        self.assertEqual(self.owner.online.catalogue_calls, 1)
        with patch("service.sector_history.time.monotonic", return_value=401):
            self.history.history({"id": "sh881376", "refresh": True})
        self.assertEqual(self.owner.online.catalogue_calls, 2)

    def test_published_catalogue_is_shared_and_new_source_is_revalidated(self):
        self.history.remember("source-one", [{"symbol": "sh881376", "name": "数字媒体"}])
        self.history.history({"id": "sh881376", "refresh": True})
        self.assertEqual(self.owner.online.catalogue_calls, 0)
        self.owner.online.root = "source-two"
        self.history.history({"id": "sh881376", "refresh": True})
        self.assertEqual(self.owner.online.catalogue_calls, 1)

    def test_symbol_must_be_member_of_sector_catalogue(self):
        data = self.history.history({"id": "sh600900"})
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(self.owner.online.calls, 0)

    def test_invalid_and_wrong_identity_data_never_replace_good_cache(self):
        self.history.history({"id": "sh881376"})
        for transform in [lambda r: r.update(symbol="sh600900"),
                          lambda r: r["rows"].append(r["rows"][-1]),
                          lambda r: r["rows"][0].update(close=float("nan")),
                          lambda r: r["rows"][0].update(close=100),
                          lambda r: r["rows"][0].update(date="2200-01-01")]:
            self.owner.online.result = {"symbol": "sh881376", "rows": copy.deepcopy(ROWS)}
            transform(self.owner.online.result)
            data = self.history.history({"id": "sh881376", "refresh": True})
            self.assertTrue(data["stale"])
            self.assertEqual(data["rows"], ROWS)

    def test_empty_result_stays_empty_without_fabricated_member_average(self):
        self.owner.online.result["rows"] = []
        data = self.history.history({"id": "sh881376"})
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(data["rows"], [])
        self.assertIsNone(data["dataAsOf"])
        self.assertEqual(self.owner.db.execute("SELECT COUNT(*) FROM sector_history_cache").fetchone()[0], 0)

    def test_request_boundary(self):
        for request in [{}, {"id": "../sh881376"}, {"id": "sh881376", "limit": True},
                        {"id": "sh881376", "limit": 1001}, {"id": "sh881376", "refresh": "true"},
                        {"id": "sh881376", "extra": 1}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.history.history(request)


if __name__ == "__main__":
    unittest.main()
