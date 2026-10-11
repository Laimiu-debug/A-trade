import datetime as dt
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from service.market_service import MarketService
from service import market_research as research


def dates(count=90):
    day = dt.date(2026, 1, 1)
    result = []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += dt.timedelta(days=1)
    return result


def security(symbol="sh600001", count=90, prices=None, *, name="股票", volume=1000, amount=100000):
    prices = prices or [10 + i / 10 for i in range(count)]
    return {"symbol": symbol, "code": symbol[2:], "name": name, "market": symbol[:2],
            "kind": "index" if symbol.startswith("sh000") else "stock",
            "rows": [(day, price, volume, amount) for day, price in zip(dates(len(prices)), prices)]}


def encode(rows):
    return b"".join(struct.pack("<IIIIIfII", int(day.replace("-", "")),
                     round(price * 100), round(price * 100), round(price * 100), round(price * 100),
                     amount, volume, 0) for day, price, volume, amount in rows)


class ResearchCalculations(unittest.TestCase):
    def test_real_amount_ranking_and_units_with_missing_values_last(self):
        items = [{"symbol": "b", "amount": None}, {"symbol": "c", "amount": 2},
                 {"symbol": "a", "amount": 2}, {"symbol": "d", "amount": 1}]
        self.assertEqual([row["symbol"] for row in research.sort_items(items)], ["a", "c", "d", "b"])
        self.assertEqual([row["symbol"] for row in research.sort_items(items, descending=False)], ["d", "a", "c", "b"])
        source = security()
        snapshot = research.build_snapshot([source], total=1)
        row = snapshot["items"][0]
        self.assertEqual(row["amount"], 100000)
        self.assertEqual(row["volumeUnit"], "股")
        self.assertAlmostEqual(row["return5"], (18.9 / 18.4 - 1) * 100, places=5)
        self.assertAlmostEqual(row["return20"], (18.9 / 16.9 - 1) * 100, places=5)
        self.assertAlmostEqual(row["changePercent"], (18.9 / 18.8 - 1) * 100, places=5)
        self.assertEqual(row["ma20"], 17.95)
        self.assertTrue(row["aboveMA20"])
        self.assertEqual(len(row["sparkline"]), 20)

    def test_uniform_date_excludes_stale_from_rank_and_style(self):
        old = security("sh600002", count=89)
        index = security("sh000001", amount=999999999)
        result = research.build_snapshot([security(), old, index], total=2)
        self.assertEqual(result["coverage"], {"total": 2, "valid": 1, "excluded": 0, "stale": 1, "indexCount": 1})
        self.assertEqual({item["symbol"] for item in result["items"]}, {"sh600001", "sh000001"})
        self.assertEqual(result["staleItems"][0]["date"], old["rows"][-1][0])
        self.assertTrue(result["staleItems"][0]["stale"])
        self.assertEqual(result["style"]["metrics"]["sampleCount"], 1)
        self.assertEqual(result["style"]["metrics"]["expectedCount"], 2)
        self.assertEqual(result["style"]["metrics"]["coverage"], .5)

    def test_missing_market_day_is_not_filled_or_treated_as_adjacent(self):
        missing = security("sh600002")
        missing["rows"].pop(-2)
        result = research.build_snapshot([security(), missing], total=2)
        item = next(row for row in result["items"] if row["symbol"] == "sh600002")
        self.assertIsNone(item["changePercent"])
        self.assertIsNone(item["ma20"])
        self.assertIsNone(item["volumeRatio5"])
        self.assertEqual(result["style"]["metrics"]["sampleCount"], 1)
        self.assertIsNone(item["sparkline"][-2])
        self.assertEqual(len(item["sparkline"]), len(result["sparklineDates"]))

    def test_light_snapshot_matches_full_features_without_style_or_leadership(self):
        fresh, missing, stale = security(), security("sh600002"), security("sh600003", count=85)
        missing["rows"].pop(-2)
        sources = [fresh, missing, stale, security("sh000001")]
        full = research.build_snapshot(sources, total=3)
        with patch.object(research, "Leadership", side_effect=AssertionError("light snapshots must not compute leadership")):
            light = research.build_current_snapshot(sources, total=3, source="fixture", calendar=dates(),
                                                     as_of=dates()[-1], computed_at="fixture")
        self.assertEqual(light["items"], full["items"])
        self.assertEqual(light["staleItems"], full["staleItems"])
        self.assertEqual(light["coverage"], full["coverage"])
        self.assertEqual(light["sparklineDates"], full["sparklineDates"])
        self.assertEqual(light["staleItems"][0]["sparkline"][-5:], [None] * 5)
        self.assertNotIn("style", light)
        self.assertNotIn("timeline", light)

    def test_volume_ratio_excludes_current_and_zero_denominator_is_unknown(self):
        source = security()
        day, close, _, amount = source["rows"][-1]
        source["rows"][-1] = (day, close, 3000, amount)
        item = research.build_snapshot([source], total=1)["items"][0]
        self.assertEqual(item["volumeRatio5"], 3)
        for i in range(-6, -1):
            d, c, _, a = source["rows"][i]
            source["rows"][i] = (d, c, 0, a)
        self.assertIsNone(research.build_snapshot([source], total=1)["items"][0]["volumeRatio5"])

    def test_board_and_index_identity_is_not_industry_inference(self):
        samples = [("sh688001", "star"), ("sz300001", "chinext"), ("bj920001", "bse"),
                   ("sz000001", "main"), ("sh000001", "index")]
        for symbol, board in samples:
            with self.subTest(symbol=symbol):
                result = research.build_snapshot([security(symbol)], total=int(board != "index"))
                self.assertEqual(result["items"][0]["board"], board)
                self.assertNotIn("industry", result["items"][0])
                self.assertNotIn("tech_proxy", result["style"]["key"])

    def test_small_sample_is_insufficient_not_fake_market_regime(self):
        snapshot = research.build_snapshot([security()], total=1)
        self.assertEqual(snapshot["style"]["key"], "insufficient")
        self.assertEqual(snapshot["rules"]["minMarketSymbols"], 1000)
        self.assertEqual(snapshot["style"]["metrics"]["breadth"], 1)

    def test_two_day_confirmation_and_no_future_leakage(self):
        with patch.object(research, "MIN_MARKET_SYMBOLS", 1):
            first = research.build_snapshot([security(count=60)], total=1)
            second = research.build_snapshot([security(count=61)], total=1)
            full = research.build_snapshot([security(count=70)], total=1)
        self.assertEqual(first["style"]["key"], "insufficient")
        self.assertEqual(first["style"]["rawKey"], "trend")
        self.assertEqual(first["style"]["pendingCount"], 1)
        self.assertEqual(second["style"]["key"], "trend")
        history = next(row for row in full["timeline"] if row["date"] == second["asOf"])
        self.assertEqual({k: v for k, v in history.items() if k != "date"}, second["style"])

    def test_defensive_style_takes_effect_immediately(self):
        prices = [10 + i / 10 for i in range(89)] + [10]
        with patch.object(research, "MIN_MARKET_SYMBOLS", 1):
            snapshot = research.build_snapshot([security(prices=prices)], total=1)
        self.assertEqual(snapshot["style"]["key"], "defensive")
        self.assertIsNone(snapshot["style"]["pendingKey"])

    def test_zero_volume_not_valid_style_sample_and_no_nan(self):
        result = research.build_snapshot([security(volume=0)], total=1)
        self.assertEqual(result["style"]["metrics"]["sampleCount"], 0)
        self.assertIsNone(result["style"]["metrics"]["medianReturn20"])
        json.dumps(result, allow_nan=False)

    def test_alternating_raw_styles_expire_old_confirmation_and_agree_across_windows(self):
        for prior in ("trend", "defensive"):
            raw = [prior] * 5 + ["trend", "rotation"] * 100
            full = list(research.confirm_styles(raw))
            short = list(research.confirm_styles(raw[-80:]))
            self.assertEqual(full[-40:], short[-40:])
            self.assertTrue(all(row["key"] == "unconfirmed" for row in full[-40:]))
            self.assertTrue(full[-1]["confirmationExpired"])
        renewed = list(research.confirm_styles(raw + ["trend", "trend"]))[-1]
        self.assertEqual(renewed["key"], "trend")
        self.assertFalse(renewed["confirmationExpired"])


class ResearchCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "tdx"
        (self.root / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        self.service = MarketService(self.base / "state")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def source(self, data=None, root=None):
        path = (root or self.root) / "vipdoc" / "sh" / "lday" / "sh600001.day"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode((data or security())["rows"]))
        return path

    def configure(self, root=None):
        self.service.configure({"path": str(root or self.root)})
        self.service.scan()

    def test_empty_configuration_is_actionable_and_has_no_generated_values(self):
        result = self.service.dispatch("market.research", {})
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["asOf"])
        self.assertTrue(any("选择" in warning for warning in result["warnings"]))

    def test_explicit_refresh_detects_same_length_revision_with_preserved_mtime(self):
        path = self.source()
        self.configure()
        original = self.service.dispatch("market.research", {})
        stat = path.stat()
        changed = security(prices=[10 + i / 10 for i in range(89)] + [19.99])
        self.source(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        cached = self.service.dispatch("market.research", {})
        refreshed = self.service.dispatch("market.research", {"refresh": True})
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["items"][0]["close"], original["items"][0]["close"])
        self.assertEqual(refreshed["items"][0]["close"], 19.99)
        self.assertFalse(refreshed["cached"])

    def test_cached_snapshot_survives_restart_and_source_directory_isolation(self):
        self.source()
        self.configure()
        original = self.service.dispatch("market.research", {})
        self.service.close()
        self.service = MarketService(self.base / "state")
        self.assertEqual(self.service.dispatch("market.research", {})["computedAt"], original["computedAt"])
        alternate = self.base / "other"
        self.source(security(prices=[40] * 90), alternate)
        self.configure(alternate)
        result = self.service.dispatch("market.research", {})
        self.assertEqual(result["items"][0]["close"], 40)
        self.assertEqual(result["source"], str(alternate.resolve()))
        self.assertFalse(result["cached"])

    def test_scan_invalidates_cached_snapshot(self):
        self.source()
        self.configure()
        self.service.dispatch("market.research", {})
        self.source(security(prices=[20] * 90))
        self.service.scan()
        self.assertEqual(self.service.dispatch("market.research", {})["items"][0]["close"], 20)

    def test_light_snapshot_cache_refresh_restart_and_no_full_research(self):
        path = self.source()
        self.configure()
        with patch.object(research, "Leadership", side_effect=AssertionError("unexpected history calculation")):
            initial = self.service.dispatch("market.snapshot", {})
            self.assertFalse(initial["cached"])
            stat = path.stat()
            self.source(security(prices=[20] * 90))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual(self.service.dispatch("market.snapshot", {})["items"], initial["items"])
            updated = self.service.dispatch("market.snapshot", {"refresh": True})
            self.assertEqual(updated["items"][0]["close"], 20)
            self.assertEqual(updated["sourceRevision"], initial["sourceRevision"])
            self.assertNotEqual(updated["dataRevision"], initial["dataRevision"])
        self.service.close()
        self.service = MarketService(self.base / "state")
        cached = self.service.dispatch("market.snapshot", {})
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["computedAt"], updated["computedAt"])
        path.write_bytes(b"partial")
        fallback = self.service.dispatch("market.snapshot", {"refresh": True})
        self.assertTrue(fallback["cached"])
        self.assertEqual(fallback["items"], updated["items"])
        self.assertTrue(any("未能读取" in value for value in fallback["warnings"]))

    def test_other_connection_scan_and_refresh_invalidate_resident_and_saved_caches(self):
        self.source()
        self.configure()
        peer = MarketService(self.base / "state")
        try:
            peer.research.universe()
            peer.dispatch("market.snapshot", {})
            peer.dispatch("market.research", {})
            self.source(security(prices=[20] * 90))
            self.service.scan()
            self.assertEqual(peer.dispatch("market.snapshot", {})["items"][0]["close"], 20)
            self.assertEqual(peer.dispatch("market.research", {})["items"][0]["close"], 20)
            self.source(security(prices=[30] * 90))
            self.service.dispatch("market.snapshot", {"refresh": True})
            self.assertEqual(peer.dispatch("market.research", {})["items"][0]["close"], 30)
            alternate = self.base / "alternate"
            self.source(security(prices=[40] * 90), alternate)
            self.configure(alternate)
            self.assertEqual(peer.dispatch("market.snapshot", {})["items"][0]["close"], 40)
        finally:
            peer.close()

    def test_independent_workers_and_larger_history_windows_do_not_evict_each_other(self):
        self.source()
        self.configure()
        revision = self.service.source_revision()
        peer = MarketService(self.base / "state")
        try:
            light = self.service.dispatch("market.snapshot", {})
            computed = {}
            for worker, days in ((peer, 60), (self.service, 250), (peer, 1000)):
                current = worker.dispatch("market.research", {"historyDays": days})
                computed[days] = current["computedAt"]
                self.assertEqual(current["sourceRevision"], revision)
                self.assertEqual(current["dataRevision"], light["dataRevision"])
                self.assertEqual(worker.source_revision(), revision)
            self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM research_cache_v2").fetchone()[0], 3)
            self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM market_snapshot_cache").fetchone()[0], 1)
            for days in (60, 250, 1000):
                cached = self.service.dispatch("market.research", {"historyDays": days})
                self.assertTrue(cached["cached"])
                self.assertEqual(cached["computedAt"], computed[days])
            self.assertEqual(peer.dispatch("market.snapshot", {})["computedAt"], light["computedAt"])
        finally:
            peer.close()

    def test_change_during_computation_cannot_commit_obsolete_snapshot(self):
        self.source()
        self.configure()
        builder = research.build_current_snapshot
        def changed(*args, **kwargs):
            result = builder(*args, **kwargs)
            self.source(security(prices=[20] * 90))
            self.service.scan()
            return result
        with patch.object(research, "build_current_snapshot", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "索引已更新"):
                self.service.dispatch("market.snapshot", {})
        self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM market_snapshot_cache").fetchone()[0], 0)
        self.assertEqual(self.service.dispatch("market.snapshot", {})["items"][0]["close"], 20)

    def test_snapshot_progress_and_request_boundaries(self):
        self.source()
        self.configure()
        for method in ("market.snapshot", "market.research"):
            progress = []
            self.service.research.progress = progress.append
            self.service.dispatch(method, {"refresh": True})
            percentages = [value["percent"] for value in progress]
            self.assertEqual(percentages, sorted(percentages))
            self.assertEqual(percentages[0], 0)
            self.assertEqual(percentages[-1], 100)
            self.assertTrue(all(0 <= value <= 100 for value in percentages))
        for payload in ({"refresh": 1}, {"historyDays": 250}, {"asOf": "2200-01-01"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.dispatch("market.snapshot", payload)

    def test_corrupted_refresh_preserves_verified_snapshot_and_warns(self):
        path = self.source()
        self.configure()
        original = self.service.dispatch("market.research", {})
        path.write_bytes(b"partial")
        result = self.service.dispatch("market.research", {"refresh": True})
        self.assertTrue(result["cached"])
        self.assertEqual(result["computedAt"], original["computedAt"])
        self.assertTrue(any("未能读取" in message for message in result["warnings"]))

    def test_invalid_one_file_is_excluded_not_substituted_by_cache(self):
        path = self.source()
        second = path.with_name("sh600002.day")
        second.write_bytes(path.read_bytes())
        self.configure()
        path.write_bytes(b"partial")
        result = self.service.dispatch("market.research", {"refresh": True})
        self.assertEqual(result["coverage"]["excluded"], 1)
        self.assertEqual([row["symbol"] for row in result["items"]], ["sh600002"])

    def test_tail_read_is_bounded_and_validates_dates_and_future(self):
        path = self.source(security(count=180))
        self.assertEqual(len(research.read_tail(path)), 120)
        raw = path.read_bytes()
        path.write_bytes(raw[:-32] + raw[-64:-32])
        with self.assertRaisesRegex(ValueError, "重复日期"):
            research.read_tail(path)
        path.write_bytes(struct.pack("<IIIIIfII", 21990101, 100, 100, 100, 100, 1, 1, 0))
        with self.assertRaisesRegex(ValueError, "未来日期"):
            research.read_tail(path)

    def test_invalid_refresh_argument_does_not_trigger_scan(self):
        for payload in ({"refresh": "yes"}, {"refresh": 1}, {"refresh": None}, {"source": "other"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.dispatch("market.research", payload)

    def test_history_ranges_have_separate_cache_and_sufficient_warmup(self):
        source = security()
        start = dt.date(2020, 1, 1)
        source["rows"] = [((start + dt.timedelta(days=i)).isoformat(), 10 + i / 100, 10000, 100000) for i in range(1150)]
        self.source(source)
        self.configure()
        default = self.service.dispatch("market.research", {})
        self.assertEqual(default["historyDays"], 250)
        self.assertEqual(len(default["timeline"]), 250)
        self.assertEqual(default["timeline"][0]["metrics"]["sampleCount"], 1, 'display range has additional warmup')
        for days in (60, 120, 500, 1000):
            with self.subTest(days=days):
                result = self.service.dispatch("market.research", {"historyDays": days})
                self.assertEqual(len(result["timeline"]), days)
                self.assertEqual(result["timeline"][-60:], default["timeline"][-60:])
                self.assertEqual(self.service.dispatch("market.research", {"historyDays": days})["computedAt"], result["computedAt"])
        self.assertEqual(len(self.service.dispatch("market.research", {})["timeline"]), 250)

    def test_cutoff_reads_historical_window_without_future_rows(self):
        source = security()
        start = dt.date(2020, 1, 1)
        source["rows"] = [((start + dt.timedelta(days=i)).isoformat(), 10 + i / 100, 10000, 100000) for i in range(1150)]
        target = self.source(source)
        self.configure()
        cutoff = source["rows"][499][0]
        prior = self.service.dispatch("market.research", {"asOf": cutoff, "historyDays": 250})
        self.assertEqual(prior["asOf"], cutoff)
        self.assertEqual(prior["items"][0]["close"], 14.99)
        self.assertEqual(len(prior["timeline"]), 250)
        changed = source["rows"][:500] + [(day, close * 2, volume, amount) for day, close, volume, amount in source["rows"][500:]]
        target.write_bytes(encode(changed))
        after = self.service.dispatch("market.research", {"asOf": cutoff, "historyDays": 250, "refresh": True})
        self.assertEqual(after["timeline"], prior["timeline"])
        self.assertEqual(after["items"], prior["items"])

    def test_unsupported_history_and_future_cutoff_rejected(self):
        for payload in ({"historyDays": 20}, {"historyDays": True}, {"historyDays": 5000}, {"asOf": "2099-01-01"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.dispatch("market.research", payload)


if __name__ == "__main__":
    unittest.main()
