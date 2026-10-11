import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from service import regulatory_watch as watch


def em(code="600825", market="1", start="2026-09-30", end="2026-10-14", name="监控样例"):
    return {"MARKET": market, "STKCODE": code, "STKNAME": name, "VALIDATESTARTDATE": start, "VALIDATEENDDATE": end}


def gf(code="600825", market="上海", start="2026-09-30", end="2026-10-14", title="交易所交易监管中对市场公开的重点监控证券"):
    return {"stock_code": code, "stock_name": "监控样例", "exchange_type_desc": market,
            "key_monitor_stock_type": title, "monitor_start_date": start, "monitor_end_date": end,
            "data_status": "有效", "dataStatus": "有效"}


def gf_response(rows):
    return {"retcode": 0, "msg": "succ", "data": rows}


class ParsingTests(unittest.TestCase):
    def test_exact_gf_category_allowlist_keeps_severe_separate(self):
        rows = [gf(), gf(title="上海严重异常波动的股票"), gf(title="深圳严重异常波动的股票", code="000001", market="深圳"),
                gf(title="异常波动的股票"), gf(title="退市整理期股票"), gf(title="风险警示股票、退市整理股票、其他存在退市风险的股票"),
                gf(title="未知新增类别")]
        items, messages = watch.parse_source("gf_securities", gf_response(rows))
        self.assertEqual([row["category"] for row in items], ["focus", "severe", "severe"])
        self.assertTrue(any("4 条" in message for message in messages))

    def test_funds_and_st_names_remain_when_source_explicitly_lists_them(self):
        items, _ = watch.parse_source("eastmoney_securities", [em("513300", name="纳指ETF"), em("920575", "B", name="*ST样例")])
        self.assertEqual(items[0]["securityType"], "fund")
        self.assertEqual(items[1]["symbol"], "bj920575")
        self.assertEqual(items[1]["securityType"], "stock")
        self.assertTrue(all(row["endDateKind"] == "expected" for row in items))
        self.assertEqual(watch.parse_source("gf_securities", gf_response([gf()]))[0][0]["endDateKind"], "disclosed")

    def test_placeholder_dates_are_unknown_not_73_year_countdowns(self):
        items, messages = watch.parse_source("gf_securities", gf_response([gf(end="2099-12-31")]))
        self.assertIsNone(items[0]["endDate"])
        self.assertTrue(messages)
        item = watch.countdown({**items[0], "listed": True}, "2026-10-11")
        self.assertEqual(item["status"], "unknown")
        self.assertIsNone(item["remainingSessions"])

    def test_malformed_dates_market_identity_or_conflicting_rows_reject_source(self):
        for rows in ([em(market="0")], [em(code="60082")], [em(end="2026-02-30")], [em(start="2026-10-20", end="2026-10-14")],
                     [em(), em(end="2026-10-15")], [em() | {"LINK_URL": "javascript:alert(1)"}], [em() | {"STKNAME": "<script>"}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                watch.parse_source("eastmoney_securities", rows)
        with self.assertRaises(ValueError):
            watch.parse_source("gf_securities", {"retcode": True, "data": []})

    def test_identity_preserves_types_and_multiple_periods(self):
        rows = [gf(), gf(start="2026-09-29"), gf(title="交易所监管提示函")]
        items, _ = watch.parse_source("gf_securities", gf_response(rows + [copy.deepcopy(rows[0])]))
        self.assertEqual(len(items), 3)
        self.assertEqual(len({item["id"] for item in items}), 3)
        first = watch.parse_source("eastmoney_securities", [em()])[0][0]
        changed = watch.parse_source("eastmoney_securities", [em(end="2026-10-16")])[0][0]
        self.assertEqual(first["id"], changed["id"])


class CountdownTests(unittest.TestCase):
    def record(self, start="2026-09-30", end="2026-10-14", listed=True):
        return {"startDate": start, "endDate": end, "listed": listed}

    def test_golden_week_and_weekend_are_not_ten_workdays(self):
        item = watch.countdown(self.record(), "2026-10-09")
        self.assertEqual(item["totalSessions"], 6)  # Sep30, Oct8/9/12/13/14.
        self.assertEqual(item["elapsedSessions"], 3)
        self.assertEqual(item["remainingSessions"], 3)
        self.assertEqual(item["status"], "expiring")
        self.assertEqual(watch.countdown(self.record(), "2026-10-11")["remainingSessions"], 3)

    def test_expiry_day_remains_in_period_and_following_day_only_elapsed(self):
        same = watch.countdown(self.record(), "2026-10-14")
        self.assertEqual((same["status"], same["remainingSessions"]), ("expiring", 0))
        after = watch.countdown(self.record(), "2026-10-15")
        self.assertEqual((after["status"], after["remainingSessions"]), ("elapsed", 0))
        removed = watch.countdown(self.record(listed=False), "2026-10-11")
        self.assertEqual(removed["status"], "unlisted")
        self.assertNotIn("released", (same["status"], after["status"], removed["status"]))

    def test_upcoming_does_not_count_sessions_before_its_start(self):
        item = watch.countdown(self.record("2026-10-12", "2026-10-16"), "2026-10-09")
        self.assertEqual((item["status"], item["remainingSessions"], item["elapsedSessions"], item["totalSessions"]), ("upcoming", 5, 0, 5))

    def test_unknown_calendar_does_not_fabricate_remaining_sessions(self):
        item = watch.countdown(self.record("2026-12-30", "2027-01-08"), "2026-12-31")
        self.assertIsNone(item["remainingSessions"])
        self.assertIsNone(item["totalSessions"])
        self.assertEqual(item["calendarStatus"], "unavailable")
        self.assertEqual(item["status"], "active")
        unknown = watch.countdown(self.record(None, "2026-10-14"), "2026-10-11")
        self.assertEqual(unknown["status"], "unknown")
        self.assertIsNone(unknown["remainingSessions"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.payloads = {"eastmoney_securities": [em()], "gf_securities": gf_response([gf()])}
        self.calls = []
        self.service = watch.RegulatoryWatch(SimpleNamespace(db=self.db), self.fetch)
        self.date_patch = patch.object(watch, "_today", return_value="2026-10-11")
        self.now_patch = patch.object(watch, "_now", return_value="2026-10-11T01:00:00+00:00")
        self.date_patch.start(); self.now_patch.start()

    def tearDown(self):
        self.date_patch.stop(); self.now_patch.stop()
        self.service.close()
        self.db.close()

    def fetch(self, provider):
        self.calls.append(provider)
        result = self.payloads[provider]
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    def test_independent_sources_cached_and_source_conflicts_preserved(self):
        self.payloads["gf_securities"] = gf_response([gf(end="2026-10-16")])
        first = self.service.list()
        self.assertEqual((first["total"], first["counts"]["securities"]), (2, 1))
        self.assertEqual({row["endDate"] for row in first["items"]}, {"2026-10-14", "2026-10-16"})
        self.assertFalse(first["cached"])
        second = self.service.list()
        self.assertTrue(second["cached"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(first["items"][0]["firstSeenAt"], second["items"][0]["firstSeenAt"])

    def test_partial_failure_preserves_only_failed_source_without_faking_empty(self):
        first = self.service.list()
        old = next(item for item in first["items"] if item["provider"] == "eastmoney_securities")
        self.payloads["eastmoney_securities"] = ValueError("来源离线")
        self.payloads["gf_securities"] = gf_response([gf(end="2026-10-15")])
        with patch.object(watch, "_now", return_value="2026-10-11T02:00:00+00:00"):
            result = self.service.list({"refresh": True})
        self.assertTrue(result["stale"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["errors"]), 1)
        stale = next(item for item in result["items"] if item["provider"] == "eastmoney_securities")
        self.assertEqual(stale["lastSeenAt"], old["lastSeenAt"])
        self.assertEqual(stale["endDate"], old["endDate"])
        self.assertTrue(stale["stale"])
        fresh = next(item for item in result["items"] if item["provider"] == "gf_securities")
        self.assertFalse(fresh["stale"])
        self.assertTrue(fresh["periodChanged"])
        self.assertEqual(fresh["previousEndDate"], "2026-10-14")

    def test_failed_manual_refresh_stays_stale_on_cache_hits_and_reopen(self):
        self.service.list()
        self.payloads["eastmoney_securities"] = ValueError("来源离线")
        self.assertTrue(self.service.list({"refresh": True})["stale"])
        self.assertTrue(self.service.list()["stale"])
        reopened = watch.RegulatoryWatch(SimpleNamespace(db=self.db), self.fetch)
        self.assertTrue(reopened.list()["stale"])
        self.payloads["eastmoney_securities"] = [em()]
        self.assertFalse(reopened.list({"refresh": True})["stale"])

    def test_first_failure_unavailable_then_empty_success_distinguished(self):
        self.payloads = {key: ValueError("离线") for key in watch.ENDPOINTS}
        result = self.service.list()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["receivedAt"])
        self.assertFalse(result["cached"])
        self.assertEqual(result["items"], [])
        self.payloads = {"eastmoney_securities": [], "gf_securities": gf_response([])}
        empty = self.service.list({"refresh": True})
        self.assertEqual(empty["status"], "ready")
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["errors"], [])

    def test_removal_retains_unlisted_then_ages_out_after_ninety_days(self):
        self.service.list()
        self.payloads = {"eastmoney_securities": [], "gf_securities": gf_response([])}
        removed = self.service.list({"refresh": True})
        self.assertEqual(removed["counts"]["unlisted"], 2)
        self.assertEqual(removed["counts"]["listed"], 0)
        with patch.object(watch, "_today", return_value="2027-01-10"), patch.object(watch, "_now", return_value="2027-01-10T01:00:00+00:00"):
            old = self.service.list({"refresh": True})
        self.assertEqual(old["items"], [])

    def test_malformed_refresh_is_atomic_and_date_change_recomputes_status(self):
        first = self.service.list()
        self.payloads["eastmoney_securities"] = [em(), em(code="000001", market="0", end="broken")]
        result = self.service.list({"refresh": True})
        self.assertEqual(result["total"], first["total"])
        self.assertTrue(next(item for item in result["items"] if item["provider"] == "eastmoney_securities")["listed"])
        with patch.object(watch, "_today", return_value="2026-10-15"):
            next_day = self.service.list()
        self.assertTrue(all(item["status"] == "elapsed" for item in next_day["items"]))

    def test_persistence_and_period_revision_keep_stable_identity_and_first_seen(self):
        first = self.service.list()
        old = next(item for item in first["items"] if item["provider"] == "eastmoney_securities")
        self.payloads["eastmoney_securities"] = [em(end="2026-10-16")]
        with patch.object(watch, "_now", return_value="2026-10-11T03:00:00+00:00"):
            revised = self.service.list({"refresh": True})
            reopened = watch.RegulatoryWatch(SimpleNamespace(db=self.db), self.fetch)
            cached = reopened.list()
        item = next(item for item in revised["items"] if item["provider"] == "eastmoney_securities")
        self.assertEqual(item["id"], old["id"])
        self.assertEqual(item["firstSeenAt"], old["firstSeenAt"])
        self.assertEqual(item["previousEndDate"], old["endDate"])
        self.assertEqual(cached["items"][0]["endDate"], revised["items"][0]["endDate"])
        self.assertTrue(cached["cached"])

    def test_on_disk_cache_survives_database_connection_restart(self):
        with tempfile.TemporaryDirectory(prefix="atrade-regulatory-test-") as directory:
            path = Path(directory) / "market.sqlite3"
            first_db = sqlite3.connect(path)
            first_db.row_factory = sqlite3.Row
            service = watch.RegulatoryWatch(SimpleNamespace(db=first_db), self.fetch)
            first = service.list()
            service.close()
            first_db.close()
            second_db = sqlite3.connect(path)
            second_db.row_factory = sqlite3.Row
            try:
                restored = watch.RegulatoryWatch(SimpleNamespace(db=second_db), lambda _: (_ for _ in ()).throw(ValueError("离线"))).list()
                self.assertTrue(restored["cached"])
                self.assertEqual(restored["receivedAt"], first["receivedAt"])
                self.assertEqual([row["id"] for row in restored["items"]], [row["id"] for row in first["items"]])
                self.assertEqual(restored["errors"], [])
            finally:
                second_db.close()

    def test_no_historical_asof_or_unbounded_payload_allowed(self):
        for payload in ({"asOf": "2026-10-01"}, {"refresh": 1}, {"source": "arbitrary"}, [], ""):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.list(payload)


class TransportTests(unittest.TestCase):
    def test_fixed_endpoint_headers_size_and_redirect_bounds(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "2"}
        opener = SimpleNamespace(open=lambda request, timeout: Response(b"[]"))
        with patch.object(watch.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(watch.fetch_json("eastmoney_securities"), [])
        with self.assertRaises(ValueError):
            watch.fetch_json("https://example.com")
        with self.assertRaises(ValueError):
            watch._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")
        Response.headers = {"Content-Length": str(watch.MAX_BYTES + 1)}
        with patch.object(watch.urllib.request, "build_opener", return_value=opener), self.assertRaises(ValueError):
            watch.fetch_json("eastmoney_securities")


if __name__ == "__main__":
    unittest.main()
