import datetime as dt
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from service.portfolio_universe import snapshot_universe, UniversePreparationInterrupted


class PortfolioUniverseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = self.directory / "chosen-tdx"
        for market in ("sh", "sz", "bj"):
            (self.root / "vipdoc" / market / "lday").mkdir(parents=True)
        with closing(sqlite3.connect(self.directory / "market.sqlite3")) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO meta VALUES('path',?)", (json.dumps(str(self.root)),))
            db.execute("CREATE TABLE securities(symbol TEXT,name TEXT)")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, symbol, count=70, filename=None):
        day = dt.date(2024, 1, 2)
        data, dates = bytearray(), []
        while len(dates) < count:
            if day.weekday() < 5:
                price = 1000 + len(dates)
                data.extend(struct.pack("<IIIIIfII", int(day.strftime("%Y%m%d")), price, price + 2, price - 2, price + 1, 10000000., 1000000, 0))
                dates.append(day.isoformat())
            day += dt.timedelta(days=1)
        (self.root / "vipdoc" / symbol[:2] / "lday" / (filename or (symbol + ".day"))).write_bytes(data)
        return dates

    def test_all_more_than_120_batch_invariant_and_default_all_markets(self):
        for i in range(133):
            self.write(f"sh{600000+i:06d}")
        self.write("sz300001")
        self.write("sh688001")
        self.write("bj920001")
        self.write("sh000300")
        first = snapshot_universe(self.directory, {"batchSize": 7}, "2024-12-31")
        second = snapshot_universe(self.directory, {"batchSize": 128}, "2024-12-31")
        self.assertEqual(len(first["securities"]), 136)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["universeCoverage"]["boardCounts"], {"main": 133, "chinext": 1, "star": 1, "bse": 1})
        self.assertIsNone(first["benchmark"])
        self.assertFalse(first["dataQuality"]["certified"])
        self.assertTrue(first["dataQuality"]["reasons"])

    def test_explicit_missing_and_corrupt_daily_are_coverage_failures(self):
        self.write("sh600001")
        (self.root / "vipdoc/sh/lday/sh600002.day").write_bytes(b"broken")
        result = snapshot_universe(self.directory, {"mode": "explicit", "symbols": ["sh600001", "sh600002", "sh600003"]}, "2024-12-31")
        self.assertEqual(len(result["securities"]), 1)
        self.assertEqual({f["reason"] for f in result["universeCoverage"]["failures"]}, {"dailyReadFailed", "dailyFileMissing"})
        self.assertFalse(result["universeCoverage"]["coverageComplete"])

    def test_preparation_progress_and_cancellation_never_return_partial_snapshot(self):
        for i in range(5):
            self.write(f"sh{600000 + i}")
        progress = []
        result = snapshot_universe(self.directory, {"batchSize": 2}, "2024-12-31",
                                   progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(progress, [(0, 5), (2, 5), (4, 5), (5, 5)])
        self.assertEqual(len(result["securities"]), 5)
        stopped = []
        with self.assertRaises(UniversePreparationInterrupted):
            snapshot_universe(self.directory, {"batchSize": 2}, "2024-12-31",
                              progress=lambda done, total: stopped.append(done),
                              cancelled=lambda: bool(stopped and stopped[-1] >= 2))
        self.assertEqual(stopped, [0, 2])

    def test_missing_names_table_uses_tnf_and_never_writes_real_db(self):
        self.write("sh600001")
        with closing(sqlite3.connect(self.directory / "market.sqlite3")) as db, db:
            db.execute("DROP TABLE securities")
        tnf = self.root / "T0002/hq_cache/shs.tnf"
        tnf.parent.mkdir(parents=True)
        record = bytearray(314)
        record[:6] = b"600001"
        name = "测试股".encode("gbk")
        record[23:23 + len(name)] = name
        tnf.write_bytes(bytes(50) + record)
        path = self.directory / "market.sqlite3"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result = snapshot_universe(self.directory, end_date="2024-12-31")
        self.assertEqual(result["securities"][0]["name"], "测试股")
        self.assertTrue(any("名称表读取失败" in w for w in result["warnings"]))
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse((self.directory / "research-history.sqlite3").exists())

    def test_window_seed_full_calendar_and_historical_snapshot_no_future_content(self):
        dates = self.write("sh600001", 800)
        result = snapshot_universe(self.directory, {"lookbackDays": 39}, dates[-1])
        s = result["securities"][0]
        self.assertEqual(len(s["bars"]), 539)
        self.assertEqual(len(result["calendar"]), 800)
        self.assertEqual(s["forceSeed"]["observationsBefore"], 261)
        historical = snapshot_universe(self.directory, end_date=dates[650], start_date=dates[620])
        self.write("sh600001", 850)
        appended = snapshot_universe(self.directory, end_date=dates[650], start_date=dates[620])
        self.assertEqual(historical["fingerprint"], appended["fingerprint"])

    def test_real_benchmark_requires_explicit_selection_and_board_filter(self):
        self.write("sh600001")
        self.write("sz300001")
        self.write("sh000300")
        result = snapshot_universe(self.directory, {"boards": ["main"], "benchmarkSymbol": "sh000300"}, "2024-12-31")
        self.assertEqual([s["symbol"] for s in result["securities"]], ["sh600001"])
        self.assertEqual(result["benchmark"]["symbol"], "sh000300")
        self.assertEqual(result["universeCoverage"]["exclusions"][0]["reason"], "selectionMarketOrBoard")
        with self.assertRaises(ValueError):
            snapshot_universe(self.directory, {"benchmarkSymbol": "sh600001"}, "2024-12-31")

    def test_cached_facts_matched_to_local_raw_prices_no_network_or_price_replacement(self):
        dates = self.write("sh600001")
        day = dates[-1]
        payload = {"symbol": "sh600001", "rawBars": [{"date": day, "open": 10.69, "high": 10.71, "low": 10.67, "close": 10.70}],
                   "stByDate": {day: False}, "tradeStatusByDate": {day: 1}, "precloseByDate": {day: 10.5}, "factorByDate": {day: 2},
                   "corporateActions": [], "provenance": [{"provider": "fixture"}]}
        encoded = json.dumps(payload, ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        key = json.dumps(["research-data-v1", "baostock", "history", "sh600001", dates[0], day])
        path = self.directory / "research-history.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE snapshots(digest TEXT,request_key TEXT,content TEXT)")
            db.execute("CREATE TABLE current_snapshots(request_key TEXT,digest TEXT)")
            db.execute("INSERT INTO snapshots VALUES(?,?,?)", (digest, key, encoded))
            db.execute("INSERT INTO current_snapshots VALUES(?,?)", (key, digest))
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = snapshot_universe(self.directory, end_date=day)
        s = result["securities"][0]
        self.assertEqual(s["stByDate"], {day: False})
        self.assertEqual(s["bars"][-1]["close"], 10.70)
        self.assertEqual(s["factorByDate"][day], 2)
        self.assertNotIn("listingDate", s)
        self.assertNotIn("memberHistory", s)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
