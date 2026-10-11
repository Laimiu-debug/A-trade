import datetime as dt
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from service import intraday


def record(day="2026-09-30", minute=571, opening=10, high=11, low=9,
           close=10.5, amount=105000, volume=10000):
    date = dt.date.fromisoformat(day)
    packed_date = ((date.year - 2004) << 11) + date.month * 100 + date.day
    return struct.pack("<HHfffffII", packed_date, minute, opening, high, low,
                       close, amount, volume, 0)


class IntradayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "chosen-terminal"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, contents=None, symbol="sh600900", bare=False):
        filename = symbol[2:] if bare else symbol
        path = self.root / "vipdoc" / symbol[:2] / "minline" / f"{filename}.lc1"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents if contents is not None else record() + record(minute=572))
        return path

    def read(self, symbol="sh600900", day="2026-09-30", root=None):
        return intraday.read_intraday(root or self.root, symbol, day)

    def test_actual_requested_day_units_dates_and_source_unchanged(self):
        content = record("2026-09-29") + record() + record(minute=900)
        path = self.write(content)
        before = path.stat()
        data = self.read()
        self.assertEqual(data["symbol"], "sh600900")
        self.assertEqual(data["code"], "600900")
        self.assertEqual(data["date"], "2026-09-30")
        self.assertEqual([row["time"] for row in data["rows"]], ["09:31", "15:00"])
        self.assertEqual(data["rows"][0], {"time": "09:31", "open": 10.0,
                         "high": 11.0, "low": 9.0, "close": 10.5,
                         "volume": 10000, "amount": 105000.0})
        self.assertEqual(data["provider"], "tdx_lc1")
        self.assertEqual(data["asOf"], "2026-09-30T15:00:00+08:00")
        self.assertEqual(data["volumeUnit"], "股")
        self.assertEqual(data["amountUnit"], "元")
        self.assertEqual(data["availableDates"], ["2026-09-29", "2026-09-30"])
        self.assertEqual(dt.datetime.fromisoformat(data["receivedAt"]).utcoffset(), dt.timedelta(0))
        self.assertFalse(data["cached"])
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_missing_day_reports_choices_without_substitution(self):
        self.write(record("2026-09-29") + record())
        with self.assertRaises(intraday.MissingIntraday) as error:
            self.read(day="2026-10-09")
        self.assertEqual(error.exception.available_dates, ["2026-09-29", "2026-09-30"])
        self.assertIn("2026-10-09", str(error.exception))

    def test_vipdoc_root_and_bare_code_filename(self):
        self.write(bare=True)
        data = self.read(root=self.root / "vipdoc", symbol="SH600900")
        self.assertEqual(data["symbol"], "sh600900")

    def test_exchange_keeps_same_code_separate_and_index_units_are_explicit(self):
        self.write(symbol="sh000001")
        self.write(record(close=10.75), symbol="sz000001")
        index, stock = self.read("sh000001"), self.read("sz000001")
        self.assertEqual(index["rows"][0]["close"], 10.5)
        self.assertEqual(stock["rows"][0]["close"], 10.75)
        self.assertEqual(index["volumeUnit"], "原始单位")
        self.assertTrue(index["warnings"])
        self.assertEqual(stock["volumeUnit"], "股")

    def test_invalid_symbol_source_date_and_unsupported_security_rejected(self):
        self.write()
        for symbol in ("600900", "sh../../secret", "sh510300", "sh600900/../../file"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.read(symbol=symbol)
        for day in ("20260930", "2026-02-30", "2026-9-30", None, 20260930):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.read(day=day)
        with self.assertRaisesRegex(ValueError, "完整路径"):
            self.read(root=Path("relative"))
        with self.assertRaisesRegex(ValueError, "没有.*分时文件"):
            self.read(symbol="sz000002")

    def test_empty_partial_and_large_file_rejected(self):
        for data in (b"", record()[:-1], record() + b"x"):
            self.write(data)
            with self.subTest(size=len(data)), self.assertRaisesRegex(ValueError, "不完整"):
                self.read()
        self.write()
        with patch.object(intraday, "MAX_SOURCE_BYTES", 32):
            with self.assertRaisesRegex(ValueError, "过大"):
                self.read()

    def test_invalid_prices_amount_and_clock_rejected(self):
        cases = ({"opening": 12}, {"high": 8}, {"low": 0}, {"close": 12},
                 {"opening": float("nan")}, {"amount": float("inf")},
                 {"amount": -1}, {"minute": 1440})
        for values in cases:
            self.write(record(**values))
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.read()
        invalid_date = bytearray(record())
        struct.pack_into("<H", invalid_date, 0, ((2026 - 2004) << 11) + 230)
        self.write(invalid_date)
        with self.assertRaisesRegex(ValueError, "日期"):
            self.read()

    def test_duplicates_order_and_day_count_rejected_in_all_dates(self):
        for data in (record() * 2, record(minute=572) + record(),
                     record() + record("2026-09-29")):
            self.write(data)
            with self.subTest(data=data[:4]), self.assertRaisesRegex(ValueError, "重复时间|时间递增"):
                self.read()
        self.write(b"".join(record(minute=n) for n in range(501)))
        with self.assertRaisesRegex(ValueError, "数量上限"):
            self.read()
        self.write(record("2026-09-29", amount=-1) + record())
        with self.assertRaisesRegex(ValueError, "成交额"):
            self.read()

    def test_source_changed_during_read_is_rejected(self):
        self.write()
        real_fstat = intraday.os.fstat
        count = 0

        def changing_stat(fd):
            nonlocal count
            stat = real_fstat(fd)
            count += 1
            if count == 2:
                from types import SimpleNamespace
                return SimpleNamespace(st_size=stat.st_size, st_mtime_ns=stat.st_mtime_ns + 1,
                                       st_ctime_ns=stat.st_ctime_ns, st_ino=stat.st_ino,
                                       st_dev=stat.st_dev)
            return stat

        with patch.object(intraday.os, "fstat", side_effect=changing_stat):
            with self.assertRaisesRegex(ValueError, "正在更新"):
                self.read()

    def test_link_outside_chosen_root_is_rejected(self):
        outside = self.base / "outside.lc1"
        outside.write_bytes(record())
        path = self.write()
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links unavailable")
        with self.assertRaisesRegex(ValueError, "超出所选目录"):
            self.read()


if __name__ == "__main__":
    unittest.main()
