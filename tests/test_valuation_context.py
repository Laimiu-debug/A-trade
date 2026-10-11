import datetime as dt
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from service.market_service import MarketService
from service import valuation_context as context


NOW = dt.datetime(2026, 10, 11, 12, tzinfo=context.ZONE)


def bars(points=4090, day="2026-10-09", **kwargs):
    return {"symbol": "sh000001", "kind": "index", "rows": [{"date": day, "close": points}],
            "cached": False, "stale": False, "lastReadAt": "2026-10-11T04:00:00+00:00",
            "lastValidatedAt": "2026-10-09T08:00:00+00:00", "warnings": [], **kwargs}


class ValuationContextTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(context, "_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def query(self, data):
        owner = SimpleNamespace(bars=Mock(return_value=data))
        result = context.finance_context(owner)
        owner.bars.assert_called_once_with({"symbol": "sh000001", "limit": 10})
        return result

    def test_original_formula_preserves_raw_and_floor_without_rounding(self):
        result = self.query(bars())
        self.assertEqual(result["coefficient"], 4090 / 3000)
        self.assertEqual(result["rawCoefficient"], 4090 / 3000)
        self.assertEqual(result["baselinePoints"], 3000)
        self.assertEqual(result["floorCoefficient"], .5)
        self.assertEqual(result["asOf"], "2026-10-09")
        self.assertEqual(result["expectedAsOf"], "2026-10-09")
        self.assertFalse(result["stale"])
        self.assertIn("模型锚点", result["modelNote"])
        floor = self.query(bars(1000))
        self.assertEqual(floor["rawCoefficient"], 1 / 3)
        self.assertEqual(floor["coefficient"], .5)

    def test_unavailable_never_uses_neutral_one(self):
        owner = SimpleNamespace(bars=Mock(side_effect=ValueError("请先选择目录")))
        result = context.finance_context(owner)
        self.assertEqual(result["status"], "unavailable")
        for key in ("indexPoints", "rawCoefficient", "coefficient", "asOf"):
            self.assertIsNone(result[key])
        self.assertIn("请先选择目录", result["errors"][0]["message"])

    def test_rejects_wrong_identity_kind_nonfinite_and_future(self):
        fixtures = [bars(symbol="sz000001"), bars(kind="stock"), bars(day="2026-10-12"),
                    bars(day="2026-10-10"), bars(day="2026-10-09 15:00:00"), bars(rows=[])]
        fixtures += [bars(value) for value in (0, -1, True, "4090", None, float("nan"), float("inf"))]
        for data in fixtures:
            with self.subTest(data=data):
                result = self.query(data)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["coefficient"])

    def test_intraday_bar_excluded_but_close_accepted_after_three(self):
        data = bars(rows=[{"date": "2026-10-08", "close": 3900}, {"date": "2026-10-09", "close": 4090}])
        with patch.object(context, "_now", return_value=dt.datetime(2026, 10, 9, 14, 59, tzinfo=context.ZONE)):
            result = self.query(data)
            self.assertEqual(result["asOf"], "2026-10-08")
            self.assertEqual(result["coefficient"], 1.3)
            self.assertFalse(result["stale"])
            self.assertIn("未收盘", " ".join(result["warnings"]))
            self.assertEqual(self.query(bars())["status"], "unavailable")
        with patch.object(context, "_now", return_value=dt.datetime(2026, 10, 9, 15, 1, tzinfo=context.ZONE)):
            self.assertEqual(self.query(data)["asOf"], "2026-10-09")

    def test_holiday_weekend_and_missing_sessions_are_distinguished(self):
        stale = self.query(bars(day="2026-09-30"))
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["lagTradingDays"], 2)
        with patch.object(context, "_now", return_value=dt.datetime(2026, 10, 4, 16, tzinfo=context.ZONE)):
            fresh = self.query(bars(day="2026-09-30"))
            self.assertFalse(fresh["stale"])
            self.assertEqual(fresh["lagTradingDays"], 0)

    def test_source_cache_not_automatically_stale_but_inaccessible_source_is(self):
        self.assertFalse(self.query(bars(cached=True))["stale"])
        self.assertTrue(self.query(bars(cached=True, stale=True))["stale"])
        mcp = self.query(bars(provider="tdx_mcp"))
        self.assertIn("MCP", mcp["sourceName"])
        self.assertEqual(mcp["provider"], "tdx_mcp")

    def test_uncovered_calendar_never_claims_fresh(self):
        old = self.query(bars(day="2024-12-31"))
        self.assertTrue(old["stale"])
        self.assertIsNone(old["lagTradingDays"])
        with patch.object(context, "_now", return_value=dt.datetime(2027, 10, 11, 16, tzinfo=context.ZONE)):
            result = self.query(bars(day="2027-10-08"))
            self.assertTrue(result["stale"])
            self.assertIsNone(result["expectedAsOf"])
            self.assertIn("未覆盖", " ".join(result["warnings"]))

    def test_invalid_payloads_rejected_before_read(self):
        owner = SimpleNamespace(bars=Mock())
        for payload in ({"refresh": 1}, {"asOf": "2026-10-09"}, [], {"symbol": "sz000001"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                context.finance_context(owner, payload)
        owner.bars.assert_not_called()

    def test_dispatch_reads_only_user_selected_local_index_and_retains_file_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "user-chosen" / "vipdoc" / "sh" / "lday"
            source.mkdir(parents=True)
            file = source / "sh000001.day"
            file.write_bytes(struct.pack("<IIIIIfII", 20261009, 400000, 420000, 390000, 409000, 123_000_000., 10000, 0))
            owner = MarketService(base / "workspace")
            try:
                empty = owner.dispatch("market.finance.context", {})
                self.assertEqual(empty["status"], "unavailable")
                owner.configure({"path": str(base / "user-chosen")})
                owner.scan()
                first = owner.dispatch("market.finance.context", {"refresh": True})
                self.assertEqual(first["indexPoints"], 4090)
                self.assertEqual(first["provider"], "tdx_local")
                self.assertFalse(first["stale"])
                file.unlink()
                offline = owner.dispatch("market.finance.context", {})
                self.assertEqual(offline["indexPoints"], 4090)
                self.assertTrue(offline["cached"])
                self.assertTrue(offline["stale"])
            finally:
                owner.close()


if __name__ == "__main__":
    unittest.main()
