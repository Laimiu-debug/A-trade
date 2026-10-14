import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from service.market_service import MarketService
from service import tdx


def record(date=20261008, opening=1000, high=1100, low=900, close=1050, amount=105000.0, volume=10000):
    return struct.pack("<IIIIIfII", date, opening, high, low, close, amount, volume, 0)


def write_names(root, market, names, width=360, initials=None):
    target = root / "T0002" / "hq_cache" / f"{market}s.tnf"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = bytearray(50)
    for code, name in names.items():
        row = bytearray(width)
        row[:6] = code.encode("ascii")
        encoded = name.encode("gbk")
        offset = 31 if width == 360 else 23
        row[offset:offset + len(encoded)] = encoded
        if initials and code in initials:
            alias = initials[code].encode("ascii")
            alias_offset = 329 if width == 360 else 285
            row[alias_offset:alias_offset + len(alias)] = alias
        content.extend(row)
    target.write_bytes(content)
    return target


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "chosen-tdx"
        for market in tdx.MARKETS:
            (self.root / "vipdoc" / market / "lday").mkdir(parents=True)
        self.state = self.base / "app-state"
        self.service = MarketService(self.state)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def day(self, symbol="sh600900", data=None, root=None):
        path = (root or self.root) / "vipdoc" / symbol[:2] / "lday" / f"{symbol}.day"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if data is not None else record() + record(20261009))
        return path

    def scan(self):
        self.service.configure({"path": str(self.root)})
        return self.service.scan()

    def bars(self, symbol="sh600900", **query):
        return self.service.bars({"symbol": symbol, **query})

    def test_empty_install_has_no_machine_default_or_demo(self):
        status = self.service.status()
        self.assertFalse(status["configured"])
        self.assertFalse(status["available"])
        self.assertEqual(status["path"], "")
        self.assertEqual(status["symbolCount"], 0)
        self.assertEqual(self.service.search({})["items"], [])
        with self.assertRaisesRegex(ValueError, "先.*选择"):
            self.bars()

    def test_question_resolves_exact_names_and_market_codes_without_reading_bars(self):
        write_names(self.root, "sh", {"600900": "长江电力", "600001": "龙头甲"})
        self.day()
        self.scan()
        with patch.object(self.service, "bars", side_effect=AssertionError("resolve must not read bars")):
            result = self.service.dispatch("market.resolve", {"question": "比较长江电力与sh600001，截至2026-10-08"})
            self.assertEqual({row["symbol"] for row in result["items"]}, {"sh600900", "sh600001"})
            self.assertEqual(result["unknown"], [])
            self.assertEqual(result["ambiguous"], [])
            self.assertNotIn(str(self.root), json.dumps(result))
            self.assertEqual(self.service.dispatch("market.resolve", {"question": "解释当前情绪"})["items"], [])
            self.assertEqual(self.service.dispatch("market.resolve", {"question": "分析999999"})["unknown"], ["999999"])

    def test_indexed_stock_search_ignores_catalogue_names_without_downloaded_bars(self):
        write_names(self.root, "sh", {**{str(600001+i): f"样本{i}" for i in range(300)}, "600900": "长江电力"})
        self.day()
        self.scan()
        self.assertGreater(self.service.search({"kind": "stock"})["total"], 200)
        result = self.service.search({"kind": "stock", "indexedOnly": True, "limit": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["symbol"], "sh600900")
        with self.assertRaisesRegex(ValueError, "布尔值"):
            self.service.search({"indexedOnly": "true"})

    def test_question_does_not_guess_ambiguous_names_or_six_digit_codes(self):
        write_names(self.root, "sh", {"600001": "同名股票", "600002": "新同名股票"})
        write_names(self.root, "sz", {"000001": "同名股票"})
        self.service.configure({"path": str(self.root)})
        for question in ("同名股票怎么样",):
            result = self.service.dispatch("market.resolve", {"question": question})
            self.assertEqual(len(result["ambiguous"]), 1)
            self.assertEqual(len(result["ambiguous"][0]["items"]), 2)
        result = self.service.dispatch("market.resolve", {"question": "分析新同名股票"})
        self.assertEqual([row["symbol"] for row in result["items"]], ["sh600002"])
        self.assertEqual(result["ambiguous"], [])
        result = self.service.dispatch("market.resolve", {"question": "同名股票sh600001怎么看"})
        self.assertEqual([row["symbol"] for row in result["items"]], ["sh600001"])
        self.assertEqual(result["ambiguous"], [])
        with self.assertRaisesRegex(ValueError, "4000"):
            self.service.dispatch("market.resolve", {"question": "长" * 4001})

    def test_select_root_and_vipdoc_normalize_and_survive_restart(self):
        self.day()
        self.scan()
        status = self.service.configure({"path": str(self.root / "vipdoc")})
        self.assertEqual(status["path"], str(self.root.resolve()))
        self.assertEqual(status["symbolCount"], 1)
        self.service.close()
        self.service = MarketService(self.state)
        self.assertEqual(self.service.status()["path"], str(self.root.resolve()))
        self.assertEqual(self.service.search({"query": "600900"})["total"], 1)

    def test_invalid_path_does_not_overwrite_valid_configuration(self):
        self.scan()
        for invalid in ("", "relative/path", str(self.base), str(self.base / "missing"), None, 42):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.service.configure({"path": invalid})
            self.assertEqual(self.service.status()["path"], str(self.root.resolve()))

    def test_empty_valid_directory_is_saved_and_has_actionable_warning(self):
        status = self.scan()
        self.assertTrue(status["configured"])
        self.assertTrue(status["available"])
        self.assertEqual(status["symbolCount"], 0)
        self.assertTrue(any("下载日线" in value for value in status["warnings"]))

    def test_binary_record_prices_stock_volume_and_amount_are_not_adjusted(self):
        self.day(data=record(opening=2898, high=2940, low=2891, close=2899, amount=3879076352.0, volume=133103264))
        self.scan()
        data = self.bars()
        self.assertEqual(data["adjustment"], "none")
        self.assertEqual(data["volumeUnit"], "股")
        self.assertEqual(data["amountUnit"], "元")
        self.assertEqual(data["rows"], [{"date": "2026-10-08", "open": 28.98, "high": 29.40,
                                       "low": 28.91, "close": 28.99, "volume": 133103264, "amount": 3879076352.0}])

    def test_same_code_in_different_markets_keeps_separate_names_and_bars(self):
        self.day("sh000001", record(close=1001))
        self.day("sz000001", record(close=1099))
        write_names(self.root, "sh", {"000001": "上证指数"})
        write_names(self.root, "sz", {"000001": "平安银行"})
        self.scan()
        results = self.service.search({"query": "000001"})
        self.assertEqual(results["total"], 2)
        self.assertEqual([item["symbol"] for item in results["items"]], ["sh000001", "sz000001"])
        self.assertEqual(self.bars("sh000001")["volumeUnit"], "手")
        self.assertEqual(self.bars("sz000001")["volumeUnit"], "股")
        self.assertEqual(self.bars("sh000001")["rows"][0]["close"], 10.01)
        self.assertEqual(self.bars("sz000001")["rows"][0]["close"], 10.99)

    def test_current_and_legacy_name_file_formats(self):
        self.day("sh600900")
        self.day("sz000001")
        write_names(self.root, "sh", {"600900": "长江电力"}, 360)
        write_names(self.root, "sz", {"000001": "平安银行"}, 314)
        self.scan()
        self.assertEqual(self.service.search({"query": "长江"})["items"][0]["symbol"], "sh600900")
        self.assertEqual(self.service.search({"query": "银行"})["items"][0]["symbol"], "sz000001")

    def test_absent_or_bad_names_do_not_invent_name_or_block_prices(self):
        self.day()
        name_file = write_names(self.root, "sh", {"600900": "长江电力"})
        name_file.write_bytes(b"invalid")
        status = self.scan()
        self.assertTrue(any("名称未加载" in warning for warning in status["warnings"]))
        self.assertEqual(self.bars()["name"], "名称待补全")

    def test_unsupported_fund_and_bond_files_are_not_mispriced(self):
        self.day("sh510300")
        self.day("sh113666")
        self.day("bj810011")
        self.day("bj920000")
        status = self.scan()
        self.assertEqual(status["symbolCount"], 1)
        self.assertEqual(self.service.search({})["items"][0]["symbol"], "bj920000")
        self.assertTrue(any("3 个" in warning for warning in status["warnings"]))

    def test_search_literal_wildcards_exact_priority_and_pagination(self):
        for symbol in ("sh600900", "sh600901", "sh600902"):
            self.day(symbol)
        self.scan()
        self.assertEqual(self.service.search({"query": "%"})["total"], 0)
        self.assertEqual(self.service.search({"query": "6009", "offset": 1, "limit": 1})["items"][0]["symbol"], "sh600901")
        self.assertEqual(self.service.search({"query": "SH600902"})["items"][0]["symbol"], "sh600902")

    def test_stock_search_names_codes_initials_without_rescanning_or_daily_bars(self):
        self.day("sh000001")
        self.day("sz000001")
        self.scan()
        write_names(self.root, "sh", {"000001": "上证指数", "600900": "长江电力"}, initials={"600900": "CJDL"})
        write_names(self.root, "sz", {"000001": "平安银行"}, width=314, initials={"000001": "PAYH"})
        for query in ("长江", "600900", "sh600900", "cjdl", "CJ"):
            with self.subTest(query=query):
                item = self.service.search({"query": query, "kind": "stock"})["items"][0]
                self.assertEqual(item["symbol"], "sh600900")
                self.assertEqual(item["initials"], "CJDL")
                self.assertIsNone(item["latestDate"])
        self.assertEqual(self.service.search({"query": "PAYH", "kind": "stock"})["items"][0]["symbol"], "sz000001")
        self.assertEqual([item["symbol"] for item in self.service.search({"query": "000001", "kind": "stock"})["items"]], ["sz000001"])
        self.assertEqual(self.service.search({})["total"], 2)
        self.assertEqual(self.service.search({"query": "000001", "kind": "index"})["items"][0]["symbol"], "sh000001")
        for kind in ("other", "", False, [], {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.service.search({"kind": kind})

    def test_search_alias_changes_restart_and_source_switch(self):
        self.scan()
        metadata = write_names(self.root, "sh", {"600900": "长江电力"}, initials={"600900": "CJDL"})
        self.assertEqual(self.service.search({"query": "CJDL", "kind": "stock"})["total"], 1)
        write_names(self.root, "sh", {"600900": "长江电力", "600519": "贵州茅台"}, initials={"600900": "CJDL", "600519": "GZMT"})
        self.assertEqual(self.service.search({"query": "gzmt", "kind": "stock"})["total"], 1)
        self.service.close()
        self.service = MarketService(self.state)
        metadata.unlink()
        cached = self.service.search({"query": "gzmt", "kind": "stock"})
        self.assertEqual(cached["total"], 1)
        self.assertTrue(cached["cached"])
        self.assertTrue(cached["warnings"])
        new_root = self.base / "second-tdx"
        self.day("sz000001", root=new_root)
        write_names(new_root, "sz", {"000001": "平安银行"}, initials={"000001": "PAYH"})
        self.service.configure({"path": str(new_root)})
        self.assertEqual(self.service.search({"query": "gzmt", "kind": "stock"})["total"], 0)
        self.assertEqual(self.service.search({"query": "payh", "kind": "stock"})["total"], 1)

    def test_stock_search_uses_chosen_mcp_names_without_local_configuration(self):
        write_names(self.root, "sh", {"600900": "长江电力"}, initials={"600900": "CJDL"})
        with patch.object(self.service.online, "config", return_value={"root": str(self.root), "enabled": False}):
            self.assertEqual(self.service.search({"query": "cjdl", "kind": "stock"})["items"][0]["symbol"], "sh600900")
        self.assertEqual(self.service.search({"query": "cjdl", "kind": "stock"})["items"], [])

    def test_stock_search_exact_name_and_initials_take_priority_with_literal_query(self):
        self.scan()
        write_names(self.root, "sh", {"600001": "长江电力甲", "600900": "长江电力"}, initials={"600001": "CJDLJ", "600900": "CJDL"})
        for query in ("长江电力", "cjdl"):
            results = self.service.search({"query": query, "kind": "stock", "limit": 1})
            self.assertEqual(results["total"], 2)
            self.assertEqual(results["items"][0]["symbol"], "sh600900")
            self.assertEqual(self.service.search({"query": query, "kind": "stock", "limit": 1, "offset": 1})["items"][0]["symbol"], "sh600001")
        for query in ("%", "_", "\\"):
            self.assertEqual(self.service.search({"query": query, "kind": "stock"})["total"], 0)

    def test_source_switch_clears_previous_catalogue_and_cache(self):
        self.day()
        self.scan()
        self.bars()
        new_root = self.base / "other-tdx"
        self.day("sz000001", root=new_root)
        status = self.service.configure({"path": str(new_root)})
        self.assertEqual(status["symbolCount"], 0)
        self.assertIsNone(status["lastScannedAt"])
        self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM bars_cache").fetchone()[0], 0)
        self.service.scan()
        self.assertEqual(self.service.search({})["items"][0]["symbol"], "sz000001")

    def test_cache_survives_restart_and_source_offline_is_explicit(self):
        path = self.day()
        self.scan()
        first = self.bars()
        self.assertFalse(first["cached"])
        self.service.close()
        self.service = MarketService(self.state)
        second = self.bars()
        self.assertTrue(second["cached"])
        self.assertEqual(first["dataVersion"], second["dataVersion"])
        path.unlink()
        offline = self.bars()
        self.assertEqual(first["rows"], offline["rows"])
        self.assertTrue(any("尚未更新" in warning for warning in offline["warnings"]))

    def test_history_revision_detected_even_when_size_and_mtime_unchanged(self):
        path = self.day()
        self.scan()
        first = self.bars()
        stat = path.stat()
        path.write_bytes(record(close=1099) + record(20261009))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        second = self.bars()
        self.assertNotEqual(first["dataVersion"], second["dataVersion"])
        self.assertFalse(second["cached"])
        self.assertEqual(second["rows"][0]["close"], 10.99)

    def test_truncated_update_raises_and_preserves_old_cache(self):
        path = self.day()
        self.scan()
        first = self.bars()
        path.write_bytes(record() + b"partial")
        with self.assertRaisesRegex(ValueError, "不完整"):
            self.bars()
        stored = self.service.db.execute("SELECT digest FROM bars_cache").fetchone()[0]
        self.assertEqual(stored, first["dataVersion"])

    def test_invalid_middle_record_is_rejected_before_cache_write(self):
        path = self.day(data=record(20261007) + record(20261008) + record(20261009))
        self.scan()
        first = self.bars()
        path.write_bytes(record(20261007) + record(20261008, amount=float("nan")) + record(20261009))
        with self.assertRaisesRegex(ValueError, "成交额"):
            self.bars()
        self.assertEqual(self.service.db.execute("SELECT digest FROM bars_cache").fetchone()[0], first["dataVersion"])

    def test_invalid_binary_dates_prices_and_order_are_rejected(self):
        for data, phrase in [(record(20260230), "日期"), (record(high=500), "价格"),
                             (record(amount=float("inf")), "成交额"), (record(amount=-1), "成交额"),
                             (record(20261009) + record(20261008), "日期"),
                             (record() + record(), "重复日期"), (b"", "为空")]:
            with self.subTest(phrase=phrase), self.assertRaisesRegex(ValueError, phrase):
                tdx.parse_daily(data)

    def test_bad_file_does_not_abort_other_securities_scan(self):
        self.day()
        self.day("sz000001", b"broken")
        status = self.scan()
        self.assertEqual(status["symbolCount"], 1)
        self.assertTrue(any("sz000001" in warning for warning in status["warnings"]))

    def test_file_change_during_read_rejected(self):
        path = self.day()
        before = path.stat()
        after = type("ChangedStat", (), {"st_size": before.st_size, "st_mtime_ns": before.st_mtime_ns + 1,
                                          "st_ctime_ns": before.st_ctime_ns, "st_ino": before.st_ino})()
        with patch.object(Path, "stat", side_effect=[before, after]):
            with self.assertRaisesRegex(ValueError, "正在更新"):
                tdx.read_stable(path)

    def test_path_traversal_and_symlink_escape_are_rejected(self):
        self.scan()
        for symbol in ("../secret", "sh600900/../../file", "600900", "sh000001.day", None):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.bars(symbol)
        outside = self.base / "outside.day"
        outside.write_bytes(record())
        link = self.root / "vipdoc" / "sh" / "lday" / "sh600900.day"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("This account cannot create symbolic links")
        with self.assertRaisesRegex(ValueError, "超出"):
            tdx.daily_path(self.root, "sh600900")

    def test_limit_dates_and_result_metadata(self):
        self.day(data=record(20261007) + record(20261008) + record(20261009))
        self.scan()
        result = self.bars(start="2026-10-07", end="2026-10-08", limit=1)
        self.assertEqual(result["rows"][0]["date"], "2026-10-08")
        self.assertEqual(result["dataAsOf"], "2026-10-09")
        self.assertEqual(len(result["dataVersion"]), 64)
        self.assertEqual(self.bars(start="2026-11-01")["rows"], [])
        for params in ({"limit": 0}, {"limit": True}, {"start": "20261001"},
                       {"start": "2026-10-09", "end": "2026-10-08"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.bars(**params)

    def test_read_only_source_files_remain_byte_identical(self):
        path = self.day()
        name_file = write_names(self.root, "sh", {"600900": "长江电力"})
        original_day, original_names = path.read_bytes(), name_file.read_bytes()
        self.scan()
        self.bars()
        self.assertEqual(path.read_bytes(), original_day)
        self.assertEqual(name_file.read_bytes(), original_names)
        self.assertFalse((self.root / "market.sqlite3").exists())

    def test_market_database_failure_does_not_prevent_account_rpc(self):
        self.service.close()
        (self.state / "market.sqlite3").write_bytes(b"broken-market-file")
        requests = "\n".join(json.dumps(value) for value in (
            {"id": 1, "method": "market.status", "payload": {}},
            {"id": 2, "method": "account.save", "payload": {"name": "Independent", "kind": "paper", "startDate": "2026-01-01", "openingCash": 0}})) + "\n"
        run = subprocess.run([sys.executable, "service/entry.py", str(self.state)], input=requests,
                             text=True, encoding="utf-8", capture_output=True, cwd=Path(__file__).resolve().parents[1])
        result = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(result[0]["ok"])
        self.assertTrue(result[1]["ok"], result[1])
        # tearDown expects an open independent service.
        self.service = MarketService(self.base / "unused-state")


if __name__ == "__main__":
    unittest.main()
