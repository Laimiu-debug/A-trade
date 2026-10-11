"""Cross-tool cache, whole-universe paging and portable observation regressions."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from service import analysis_service, market_research
from service.market_service import MarketService
from tests import test_analysis_service as fixtures


class AnalysisOptimizations(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_query_and_sort_run_before_trend_limit_and_page_boundaries(self):
        f = self.fixture
        for index in range(205):
            f.write(f"sh{600000 + index}", [10 + day / 100 for day in range(90)])
        f.scan()
        default = f.call("trends")
        self.assertEqual((default["total"], len(default["items"])), (205, 200))
        selected = f.call("trends", query="600204", limit=20)
        self.assertEqual([row["symbol"] for row in selected["items"]], ["sh600204"])
        self.assertEqual((selected["candidateTotal"], selected["total"]), (205, 1))
        page = f.call("trends", query="60020", sort="code", order="desc", offset=2, limit=2)
        self.assertEqual([row["symbol"] for row in page["items"]], ["sh600202", "sh600201"])
        self.assertEqual((page["total"], page["offset"], page["limit"], page["hasMore"]), (5, 2, 2, True))
        empty = f.call("trends", query="60020", offset=5, limit=2)
        self.assertEqual((empty["total"], empty["items"], empty["hasMore"]), (5, [], False))

    def test_rebound_query_sort_and_offset_keep_event_dates(self):
        f = self.fixture
        for index in range(3):
            f.write(f"sh{600001 + index}", [100] * 80 + [110, 121, 119, 117, 128.7])
        f.scan()
        selected = f.call("rebounds", query="600003", limit=1)
        self.assertEqual((selected["candidateTotal"], selected["total"]), (3, 1))
        self.assertEqual(selected["items"][0]["eventDate"], fixtures.trading_days(85)[-1])
        page = f.call("rebounds", sort="code", order="asc", offset=1, limit=1)
        self.assertEqual(page["items"][0]["symbol"], "sh600002")

    def test_pagination_rejects_malformed_inputs_before_source_reads(self):
        for fields in ({"offset": True}, {"offset": -1}, {"sort": []}, {"sort": "__dict__"},
                       {"query": "x" * 101}, {"query": None}, {"order": "sideways"}, {"limit": 1001}):
            for tool in ("trends", "rebounds"):
                with self.subTest(tool=tool, fields=fields), self.assertRaises(ValueError):
                    self.fixture.call(tool, **fields)

    def test_feature_lru_reuses_alternating_windows_and_is_bounded(self):
        f = self.fixture
        f.write("sh600001", [10 + i / 100 for i in range(400)])
        f.scan()
        f.owner.research.universe(records=1080)
        with patch.object(analysis_service, "analyse_universe", wraps=analysis_service.analyse_universe) as compute:
            f.call("ladder", historyDays=250)
            f.call("trends")
            f.call("ladder", historyDays=250)
            self.assertEqual(compute.call_count, 2)
            for days in (120, 500, 1000):
                f.call("ladder", historyDays=days)
            self.assertEqual(len(f.analysis._features), analysis_service.FEATURE_CACHE_SIZE)
            before = compute.call_count
            f.call("trends")
            self.assertEqual(compute.call_count, before + 1, "least recently used entry was evicted")

    def test_breakout_prefix_max_preserves_missing_day_boundary(self):
        f = self.fixture
        target = f.write("sh600001", [10] * 89 + [11])
        f.write("sh600002", [10] * 90)
        raw = target.read_bytes()
        target.write_bytes(raw[:75 * 32] + raw[76 * 32:])
        f.scan()
        self.assertEqual(f.call("moves", kind="breakout", breakoutDays=13)["total"], 1)
        self.assertEqual(f.call("moves", kind="breakout", breakoutDays=14)["total"], 0)

    def test_changed_source_long_window_invalidates_other_worker_short_cache(self):
        f = self.fixture
        prices = [10 + i / 100 for i in range(500)]
        f.write("sh600001", prices)
        f.scan()
        first = f.owner.research.snapshot({"historyDays": 250})
        revision = f.owner.source_revision()
        other = MarketService(f.base / "state")
        self.addCleanup(other.close)
        f.write("sh600001", prices[:-1] + [16.48])
        ladder = other.analysis.analyse("ladder", {"historyDays": 1000})
        current = f.owner.research.snapshot({"historyDays": 250})
        self.assertEqual(current["asOf"], ladder["asOf"])
        self.assertEqual(current["items"][0]["close"], ladder["items"][0]["close"])
        self.assertEqual(current["style"]["metrics"]["limitUpApprox"], 1)
        self.assertNotEqual(first["dataRevision"], current["dataRevision"])
        self.assertEqual(f.owner.source_revision(), revision, "read refresh cannot invalidate fixed research source identity")

    def test_unchanged_windows_and_cutoffs_do_not_change_source_or_data_epoch(self):
        f = self.fixture
        f.write("sh600001", [10 + i / 100 for i in range(500)])
        f.scan()
        first = f.owner.research.snapshot({"historyDays": 60})
        other = MarketService(f.base / "state")
        self.addCleanup(other.close)
        second = other.research.snapshot({"historyDays": 250})
        historical = f.owner.research.snapshot({"historyDays": 120, "asOf": fixtures.trading_days(350)[-1]})
        self.assertEqual(first["dataRevision"], second["dataRevision"])
        self.assertEqual(first["dataRevision"], historical["dataRevision"])
        self.assertEqual(first["sourceRevision"], historical["sourceRevision"])
        self.assertEqual(f.owner.research.snapshot({"historyDays": 60})["computedAt"], first["computedAt"])

    def test_failed_refresh_stays_visible_after_cached_read_and_restart(self):
        f = self.fixture
        target = f.write("sh600001", [10 + i / 100 for i in range(90)])
        f.scan()
        original = f.owner.research.snapshot({"historyDays": 60})
        f.call("trends")
        raw = target.read_bytes()
        target.write_bytes(b"partial")
        self.assertTrue(f.call("trends", refresh=True)["stale"])
        repeated = f.call("trends")
        self.assertTrue(repeated["stale"])
        self.assertTrue(any("数据尚未更新" in warning for warning in repeated["warnings"]))
        other = MarketService(f.base / "state")
        self.addCleanup(other.close)
        restarted = other.research.snapshot({"historyDays": 60})
        self.assertTrue(restarted["stale"])
        self.assertEqual(restarted["computedAt"], original["computedAt"])
        target.write_bytes(raw)
        recovered = other.research.snapshot({"historyDays": 60, "refresh": True})
        self.assertFalse(recovered.get("stale", False))
        self.assertFalse(any("数据尚未更新" in warning for warning in recovered["warnings"]))
        self.assertFalse(f.call("trends")["stale"])

    def test_other_worker_refresh_discards_old_inflight_analysis_result(self):
        f = self.fixture
        f.write("sh600001", [10 + i / 100 for i in range(90)])
        f.scan()
        other = MarketService(f.base / "state")
        self.addCleanup(other.close)
        real = analysis_service.analyse_universe
        def interrupted(*args, **kwargs):
            result = real(*args, **kwargs)
            other.research.current_snapshot({"refresh": True})
            return result
        with patch.object(analysis_service, "analyse_universe", side_effect=interrupted), self.assertRaisesRegex(ValueError, "行情数据已更新"):
            f.call("trends")

    def test_watch_research_batch_add_preserves_existing_and_is_atomic(self):
        f = self.fixture
        for symbol in ("sh600001", "sh600002"):
            f.write(symbol, [10] * 80)
        f.scan()
        research = {"page": "T04", "asOf": fixtures.trading_days(80)[-1], "parameters": {"minReturn20": 5}, "source": str(f.root)}
        original = f.watch("save", symbol="sh600001", note="原记录", tags=["原标签"], research=research)
        batch = f.watch("save-many", items=[{"symbol": "sh600001", "note": "不得覆盖"},
                                            {"symbol": "sh600002", "research": research}, {"symbol": "sh600002"}])
        self.assertEqual((batch["added"], batch["skipped"], batch["total"]), (1, 2, 2))
        saved = {row["symbol"]: row for row in f.watch("list")["items"]}
        self.assertEqual(saved["sh600001"], original)
        self.assertEqual(saved["sh600002"]["research"], research)
        appended = f.watch("save", symbol="sh600001", appendNote="追加证据")
        self.assertEqual(appended["note"], "原记录\n\n追加证据")
        self.assertEqual(appended["research"], research)
        f.watch("delete", symbol="sh600002")
        with self.assertRaises(ValueError):
            f.watch("save-many", items=[{"symbol": "sh600002"}, {"symbol": "sh600999"}])
        self.assertEqual(f.watch("list")["total"], 1)

    def test_watch_export_merge_and_legacy_replace_use_one_schema(self):
        f = self.fixture
        f.write("sh600001", [10] * 80)
        f.scan()
        original = f.watch("save", symbol="sh600001", note="本地备注", research={"page": "T07", "asOf": None, "parameters": {}, "source": "fixture"})
        backup = f.watch("export")
        self.assertEqual((backup["format"], backup["schemaVersion"]), ("a-trade.watchlist", 1))
        incoming = copy.deepcopy(backup)
        incoming["items"][0]["note"] = "旧备份不能覆盖本地"
        detached = {**incoming["items"][0], "symbol": "bj920001", "code": "920001", "name": "原目录已移除"}
        incoming["items"].append(detached)
        merged = f.watch("import", backup=incoming, mode="merge")
        self.assertEqual((merged["added"], merged["skipped"], merged["total"]), (1, 1, 2))
        self.assertEqual(next(row for row in f.watch("list")["items"] if row["symbol"] == "sh600001"), original)
        self.assertEqual(f.watch("import", backup=incoming)["added"], 0)
        legacy = copy.deepcopy(backup)
        legacy["items"][0].pop("research")
        self.assertEqual(f.watch("backup.preview", backup=legacy)["total"], 1)
        f.watch("backup.restore", backup=legacy)
        self.assertIsNone(f.watch("list")["items"][0]["research"])

    def test_invalid_backup_and_provenance_leave_existing_records_unchanged(self):
        f = self.fixture
        f.write("sh600001", [10] * 80)
        f.scan()
        original = f.watch("save", symbol="sh600001")
        mutations = [lambda b: b.update(schemaVersion=True), lambda b: b.update(format="a-trade.accounts"),
                     lambda b: b["items"].append({"symbol": "sh600002"}),
                     lambda b: b["items"][0].update(research={"page": "T04", "parameters": {"x": float("nan")}}),
                     lambda b: b["items"][0].update(research={"page": "T04", "asOf": "2099-01-01"}),
                     lambda b: b["items"].extend(copy.deepcopy(b["items"]) * 5000)]
        for mutate in mutations:
            backup = f.watch("export")
            mutate(backup)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                f.watch("import", backup=backup)
            self.assertEqual(f.watch("list")["items"], [original])

    def test_merge_transaction_rolls_back_on_storage_failure(self):
        f = self.fixture
        for symbol in ("sh600001", "sh600002"):
            f.write(symbol, [10] * 80)
        f.scan()
        f.owner.db.execute("CREATE TRIGGER fixture_failure BEFORE INSERT ON analysis_watchlist WHEN NEW.symbol='sh600002' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        f.owner.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            f.watch("save-many", items=[{"symbol": "sh600001"}, {"symbol": "sh600002"}])
        self.assertEqual(f.watch("list")["total"], 0)

    def test_5000_record_import_boundary_and_merge_pool_limit(self):
        f = self.fixture
        stamp = "2021-01-01T00:00:00+00:00"
        backup = {"format": "a-trade.watchlist", "schemaVersion": 1, "exportedAt": stamp,
                  "items": [{"symbol": f"sh{600000 + i}", "code": str(600000 + i), "name": "备份证券",
                             "status": "watching", "note": "", "tags": [], "createdAt": stamp, "updatedAt": stamp}
                            for i in range(5000)]}
        self.assertEqual(f.watch("import", backup=backup)["added"], 5000)
        extra = {**backup, "items": [{**backup["items"][0], "symbol": "bj920001", "code": "920001"}]}
        with self.assertRaisesRegex(ValueError, "5000"):
            f.watch("import", backup=extra)
        self.assertEqual(f.watch("list")["total"], 5000)


if __name__ == "__main__":
    unittest.main()
