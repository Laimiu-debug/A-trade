import copy
import io
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from service import market_information as info


def report(day, profit, symbol="600900.SH", industry="电力", announced=None):
    year = int(day[:4])
    announced = announced or (f"{year + 1}-04-20" if day.endswith("12-31") else f"{year}-{int(day[5:7]) + 1:02d}-20")
    return {"SECUCODE": symbol, "SECURITY_CODE": symbol[:6], "SECURITY_NAME_ABBR": "长江电力" if symbol == "600900.SH" else "银行样例",
            "BOARD_NAME": industry, "REPORTDATE": day + " 00:00:00", "NOTICE_DATE": announced,
            "PARENT_NETPROFIT": profit}


def report_response(rows, count=None, pages=1):
    return {"success": True, "result": {"data": rows, "count": len(rows) if count is None else count, "pages": pages}}


REPORTS = [report("2025-06-30", 150), report("2024-12-31", 340), report("2024-06-30", 130)]


def quote():
    return {"rc": 0, "data": {"f57": "600900", "f43": 28.99, "f116": 700_000_000_000, "f164": 19.5, "f124": 1757055600}}


def daily_quote(symbol="600900.SH", day="2025-09-05"):
    return {"SECUCODE": symbol, "SECURITY_CODE": symbol[:6], "CLOSE_PRICE": 28.99,
            "TOTAL_MARKET_CAP": 700_000_000_000, "PE_TTM": 19.5, "TRADE_DATE": day + " 00:00:00"}


def news(code="202501010000000001", title="公司新闻", summary="来源摘要", stocks=None):
    return {"code": code, "title": title, "summary": summary, "showTime": "2025-01-01 12:34:56", "stockList": stocks or []}


def news_response(rows):
    return {"code": "1", "data": {"fastNewsList": rows}}


def announcement():
    return {"art_code": "AN202501010000000001", "title": "董事会公告", "display_time": "2025-01-01 18:12:34:123",
            "notice_date": "2025-01-02", "codes": [{"stock_code": "600900", "market_code": "1"}]}


def fund_row(code="BK1033"):
    return {"f12": code, "f14": "电池", "f124": 1757055600, "f3": 2.23, "f62": 5043652096,
            "f184": 5.1, "f66": 2272637440, "f72": 2771014656, "f78": -1264387072, "f84": -3593007616,
            "f109": -1.25, "f164": 9312816816, "f165": 4.2, "f166": 6, "f168": 7, "f170": -8, "f172": -9,
            "f160": 0, "f174": 6648629456, "f175": 3.2, "f176": 10, "f178": 20, "f180": -30, "f182": -40}


def funds_response(rows, total=None):
    return {"rc": 0, "data": {"total": len(rows) if total is None else total, "diff": rows}}


class TTMTests(unittest.TestCase):
    def test_cumulative_financial_bridge_not_quarter_sum_or_pe_inverse(self):
        rows, _, _ = info.parse_reports(report_response(REPORTS))
        result = info.ttm_snapshot("sh600900", rows)
        self.assertEqual(result["ttmNetProfit"], 360)
        self.assertEqual([row["sign"] for row in result["components"]], [1, 1, -1])
        self.assertEqual(result["reportDate"], "2025-06-30")
        self.assertEqual(result["units"]["ttmNetProfit"], "元")

    def test_actual_public_reference_vector(self):
        fixtures = [report("2026-06-30", 14755694774.53), report("2025-12-31", 34502809176.39), report("2025-06-30", 13056352473.55)]
        rows, _, _ = info.parse_reports(report_response(fixtures))
        self.assertEqual(info.ttm_snapshot("sh600900", rows)["ttmNetProfit"], 36202151477.37)

    def test_annual_zero_and_loss_are_valid_not_missing(self):
        for profit in [0, -12.5]:
            rows, _, _ = info.parse_reports(report_response([report("2024-12-31", profit)]))
            result = info.ttm_snapshot("sh600900", rows)
            self.assertEqual(result["ttmNetProfit"], profit)
            self.assertEqual(result["ttmMethod"], "annual")
            self.assertEqual(result["missing"], [])

    def test_missing_and_nonfinite_never_use_ytd_as_ttm(self):
        for profit in [None, "-", float("nan"), float("inf"), True]:
            raw = copy.deepcopy(REPORTS)
            raw[1]["PARENT_NETPROFIT"] = profit
            rows, _, _ = info.parse_reports(report_response(raw))
            result = info.ttm_snapshot("sh600900", rows)
            self.assertIsNone(result["ttmNetProfit"])
            self.assertTrue(result["missing"])
        rows, _, _ = info.parse_reports(report_response(REPORTS[:1]))
        self.assertIsNone(info.ttm_snapshot("sh600900", rows)["ttmNetProfit"])

    def test_latest_disclosed_revision_and_security_separation(self):
        raw = REPORTS + [report("2025-06-30", 160, announced="2025-08-01"), report("2025-09-30", 999, "000001.SZ")]
        rows, _, _ = info.parse_reports(report_response(raw))
        self.assertEqual(info.ttm_snapshot("sh600900", rows)["ttmNetProfit"], 370)

    def test_rejects_wrong_identity_future_and_nonquarter_report(self):
        for field, value in [("SECURITY_CODE", "000001"), ("SECUCODE", "600900.US"), ("REPORTDATE", "2025-05-31"),
                             ("NOTICE_DATE", "9999-12-31"), ("NOTICE_DATE", "2025-06-01")]:
            row = dict(REPORTS[0], **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                info.parse_reports(report_response([row]))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(info, "_today", return_value="2025-09-05")
        self.today.start()
        self.addCleanup(self.today.stop)
        self.db = sqlite3.connect(":memory:")
        self.calls = []
        self.handler = lambda endpoint, params: (_ for _ in ()).throw(ValueError("测试来源离线"))
        def fetch(endpoint, params):
            self.calls.append((endpoint, params))
            return self.handler(endpoint, params)
        self.service = info.MarketInformation(SimpleNamespace(db=self.db), fetcher=fetch)

    def tearDown(self):
        self.db.close()

    def test_news_sanitizes_source_and_links_to_actual_article_host(self):
        self.handler = lambda *_: news_response([news(title="<b>公司</b><script>evil()</script>", summary="<p>真实摘要</p><style>hide</style>")])
        result = self.service.news_list({"source": "eastmoney"})
        self.assertEqual(result["items"][0]["title"], "公司")
        self.assertEqual(result["items"][0]["summary"], "真实摘要")
        self.assertEqual(result["items"][0]["url"], "https://finance.eastmoney.com/a/202501010000000001.html")
        self.assertEqual(result["items"][0]["publishedAt"], "2025-01-01T12:34:56+08:00")
        self.assertEqual(self.calls[0][1]["pageSize"], 150)

    def test_news_symbol_keyword_pagination_filters_real_recent_window(self):
        self.handler = lambda *_: news_response([news(str(202501010000000000 + i), title="电力新闻", stocks=["1.600900"]) for i in range(25)] + [news("202501010000000099", title="其他新闻")])
        result = self.service.news_list({"source": "eastmoney", "symbol": "sh600900", "query": "电力", "page": 2})
        self.assertEqual((result["total"], len(result["items"]), result["hasMore"]), (25, 5, False))

    def test_announcements_keep_body_empty_and_true_notice_day(self):
        self.handler = lambda *_: {"success": 1, "data": {"list": [announcement()]}}
        result = self.service.news_list({"source": "announcements", "symbol": "sh600900"})
        row = result["items"][0]
        self.assertEqual(row["summary"], "")
        self.assertEqual(row["announcementDate"], "2025-01-02")
        self.assertEqual(row["publishedAt"], "2025-01-01T18:12:34+08:00")
        self.assertIn("/notices/detail/600900/AN202501010000000001.html", row["url"])

    def test_one_news_source_failure_does_not_hide_other(self):
        def handler(endpoint, _):
            if endpoint == "announcements":
                raise ValueError("公告不可用")
            return news_response([news()])
        self.handler = handler
        result = self.service.news_list({"source": "all"})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["sources"][1]["status"], "unavailable")
        self.assertEqual(result["errors"][0]["provider"], "announcements")

    def test_fresh_cache_and_failure_keep_original_capture(self):
        self.handler = lambda *_: news_response([news()])
        with patch.object(info, "_now", return_value="2025-01-01T00:00:00+00:00"):
            initial = self.service.news_list({"source": "eastmoney", "refresh": True})
        self.handler = lambda *_: (_ for _ in ()).throw(ValueError("离线"))
        result = self.service.news_list({"source": "eastmoney", "refresh": True})
        self.assertTrue(result["stale"])
        self.assertTrue(result["cached"])
        self.assertEqual(result["receivedAt"], initial["receivedAt"])
        self.assertEqual(result["items"], initial["items"])
        self.assertEqual(self.service._cache_depth, 0)

    def test_quote_fallback_is_daily_and_does_not_reverse_compute_earnings(self):
        def handler(endpoint, params):
            if endpoint == "quote":
                raise ValueError("即时来源不可用")
            if params["reportName"] == "RPT_LICO_FN_CPD":
                return report_response(REPORTS)
            return {"success": True, "result": {"data": [{"SECUCODE": "600900.SH", "SECURITY_CODE": "600900", "CLOSE_PRICE": 28.99,
                "TOTAL_MARKET_CAP": 700_000_000_000, "PE_TTM": 19.5, "TRADE_DATE": "2025-09-05 00:00:00"}]}}
        self.handler = handler
        result = self.service.finance_snapshot({"symbol": "sh600900"})
        self.assertEqual(result["ttmNetProfit"], 360)
        self.assertEqual(result["quoteProvider"], "eastmoney_daily_valuation")
        self.assertEqual(result["quoteAsOf"], "2025-09-05")
        self.assertEqual(result["marketCap"], 700_000_000_000)
        self.assertIn("不是实时", "".join(result["warnings"]))

    def test_quote_failure_retains_previous_quote_with_its_original_date(self):
        self.handler = lambda endpoint, params: quote() if endpoint == "quote" else report_response(REPORTS)
        first = self.service.finance_snapshot({"symbol": "sh600900"})
        def offline_quote(endpoint, params):
            if endpoint == "finance" and params["reportName"] == "RPT_LICO_FN_CPD":
                return report_response(REPORTS)
            raise ValueError("估值接口离线")
        self.handler = offline_quote
        second = self.service.finance_snapshot({"symbol": "sh600900", "refresh": True})
        self.assertTrue(second["quoteStale"])
        self.assertTrue(second["quoteCached"])
        self.assertEqual(second["marketCap"], first["marketCap"])
        self.assertEqual(second["quoteReceivedAt"], first["quoteReceivedAt"])
        self.assertEqual(second["ttmNetProfit"], 360)

    def test_missing_quote_still_returns_actual_financials_and_missing_values(self):
        def handler(endpoint, params):
            if endpoint == "finance" and params["reportName"] == "RPT_LICO_FN_CPD":
                return report_response(REPORTS)
            raise ValueError("无价格")
        self.handler = handler
        result = self.service.finance_snapshot({"symbol": "sh600900"})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["ttmNetProfit"], 360)
        self.assertIsNone(result["marketCap"])
        self.assertIsNone(result["price"])

    def test_financial_screen_filters_across_industries_and_computes_actual_ttm(self):
        current = [REPORTS[0], report("2025-06-30", 300, "000001.SZ", "银行")]
        def handler(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET":
                return report_response([daily_quote()])
            if "ISNEW" in params["filter"]:
                return report_response(current)
            self.assertIn("'2024-12-31'", params["filter"])
            return report_response(REPORTS[1:])
        self.handler = handler
        result = self.service.finance_screen({"query": "600900", "industry": "电力", "pageSize": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["industries"], ["电力", "银行"])
        self.assertEqual(result["items"][0]["ttmNetProfit"], 360)
        self.assertEqual(result["items"][0]["marketCap"], 700_000_000_000)
        self.assertEqual(result["items"][0]["quoteAsOf"], "2025-09-05")
        self.assertEqual(len([call for call in self.calls if call[1]["reportName"] == "RPT_VALUEANALYSIS_DET"]), 2)

    def test_screen_batch_valuation_is_same_day_and_keeps_missing_stock_missing(self):
        current = [REPORTS[0], report("2024-12-31", 300, "000001.SZ", "银行")]
        def handler(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET":
                if params["pageSize"] == 500:
                    self.assertIn("(TRADE_DATE='2025-09-05')", params["filter"])
                    self.assertNotIn("SECURITY_CODE in", params["filter"])
                return report_response([daily_quote()])
            return report_response(current if "ISNEW" in params["filter"] else REPORTS[1:])
        self.handler = handler
        result = self.service.finance_screen()
        rows = {row["symbol"]: row for row in result["items"]}
        self.assertEqual(rows["sh600900"]["marketCap"], 700_000_000_000)
        self.assertIsNone(rows["sz000001"]["marketCap"])
        self.assertEqual(rows["sz000001"]["ttmNetProfit"], 300)
        self.assertIn("收盘市值", "".join(rows["sz000001"]["missing"]))
        self.assertEqual(len(self.calls), 4)

    def test_screen_mixed_date_valuation_batch_is_not_adopted(self):
        def handler(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET":
                return report_response([daily_quote(day="2025-09-04" if params["pageSize"] == 500 else "2025-09-05")])
            return report_response(REPORTS[:1] if "ISNEW" in params["filter"] else REPORTS[1:])
        self.handler = handler
        result = self.service.finance_screen()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["scope"], "unavailable")
        self.assertIn("交易日不一致", result["errors"][0]["message"])

    def test_screen_keeps_previous_batch_valuation_on_refresh_failure(self):
        def handler(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET":
                return report_response([daily_quote()])
            return report_response(REPORTS[:1] if "ISNEW" in params["filter"] else REPORTS[1:])
        self.handler = handler
        first = self.service.finance_screen()
        def failed(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET":
                raise ValueError("日市值失败")
            return handler(endpoint, params)
        self.handler = failed
        result = self.service.finance_screen({"refresh": True})
        self.assertEqual(result["items"][0]["marketCap"], first["items"][0]["marketCap"])
        self.assertEqual(result["items"][0]["quoteAsOf"], first["items"][0]["quoteAsOf"])
        self.assertTrue(result["items"][0]["quoteStale"])

    def test_catalog_partial_page_does_not_claim_complete_universe(self):
        self.handler = lambda *_: report_response(REPORTS[:1], count=2, pages=1)
        result = self.service.finance_screen()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["items"], [])
        self.assertIn("分页缺口", result["errors"][0]["message"])

    def test_screen_history_identity_and_incomplete_page_rejected(self):
        for invalid in [report_response([report("2024-12-31", 999, "000001.SZ")]), report_response(REPORTS[1:], count=3),
                        report_response(REPORTS[1:] + REPORTS[1:2])]:
            self.handler = lambda _, params: report_response(REPORTS[:1]) if "ISNEW" in params["filter"] else invalid
            result = self.service.finance_screen({"refresh": True})
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["items"], [])

    def test_source_filters_are_local_not_injected_into_remote_expression(self):
        self.handler = lambda _, params: report_response([daily_quote()]) if params["reportName"] == "RPT_VALUEANALYSIS_DET" else report_response(REPORTS[:1] if "ISNEW" in params["filter"] else REPORTS[1:])
        result = self.service.finance_screen({"query": '\")(SECURITY_CODE="000001"'})
        self.assertEqual(result["total"], 0)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all("000001" not in params["filter"] for _, params in self.calls))

    def screen_fixture(self, count=502):
        financials = [report("2024-12-31", 1e8, f"{i:06d}.SZ", "银行") for i in range(1, count + 1)]
        quotes = [{**daily_quote(f"{i:06d}.SZ"), "TOTAL_MARKET_CAP": (100 if i < count else 5) * 1e8} for i in range(1, count + 1)]
        def handler(_, params):
            if params["reportName"] == "RPT_LICO_FN_CPD":
                rows = financials
            elif params["pageSize"] == 1:
                return report_response(quotes[:1], count=len(quotes), pages=len(quotes))
            else:
                rows = quotes
            start = (params["pageNumber"] - 1) * 500
            return report_response(rows[start:start + 500], count=len(rows), pages=(len(rows) + 499) // 500)
        self.handler = handler
        return financials, quotes

    def test_screen_filters_entire_universe_before_paginating_and_reuses_snapshot(self):
        self.screen_fixture()
        first = self.service.finance_screen({"pageSize": 1, "positive": True, "maxPe": 10,
            "below": True, "scenario": {"growth": 1, "pe": 10, "water": 1}, "sortBy": "scenarioGap", "sortOrder": "desc"})
        self.assertEqual((first["scope"], first["candidateTotal"], first["total"]), ("complete_catalog", 502, 1))
        self.assertEqual(first["items"][0]["symbol"], "sz000502")
        self.assertEqual((first["items"][0]["actualPe"], first["items"][0]["scenarioGap"]), (5, 100))
        self.assertEqual(first["coverage"]["comparable"], 502)
        network_count = len(self.calls)
        # A new worker on the same database must reuse the completed shared cache.
        worker = info.MarketInformation(SimpleNamespace(db=self.db), fetcher=lambda *_: self.fail("筛选变化不应重新联网"))
        second = worker.finance_screen({"pageSize": 1, "page": 2, "sortBy": "marketCap", "sortOrder": "asc"})
        self.assertEqual(second["items"][0]["symbol"], "sz000001")
        self.assertEqual(second["total"], 502)
        self.assertEqual(len(self.calls), network_count)
        self.assertTrue(second["cached"])

    def test_screen_missing_and_losses_never_pass_pe_filter_and_sort_last(self):
        financials, quotes = self.screen_fixture(4)
        financials[0]["PARENT_NETPROFIT"] = -1e8
        financials[1]["PARENT_NETPROFIT"] = None
        quotes[2]["TOTAL_MARKET_CAP"] = None
        result = self.service.finance_screen({"sortBy": "actualPe", "sortOrder": "desc", "scenario": {"pe": None}})
        self.assertEqual([row["symbol"] for row in result["items"]], ["sz000004", "sz000001", "sz000002", "sz000003"])
        self.assertEqual(result["coverage"], {"total": 4, "ttmAvailable": 3, "marketCapAvailable": 3, "comparable": 1, "excluded": 3})
        filtered = self.service.finance_screen({"positive": True, "maxPe": 10})
        self.assertEqual([row["symbol"] for row in filtered["items"]], ["sz000004"])

    def test_screen_failed_later_page_preserves_complete_snapshot_for_new_filters(self):
        self.screen_fixture()
        original = self.service.finance_screen({"sortBy": "marketCap", "sortOrder": "asc"})
        original_handler = self.handler
        def failed(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET" and params["pageNumber"] == 2:
                return report_response([], count=502, pages=2)
            return original_handler(endpoint, params)
        self.handler = failed
        result = self.service.finance_screen({"refresh": True, "maxPe": 10})
        self.assertTrue(result["stale"])
        self.assertEqual(result["receivedAt"], original["receivedAt"])
        self.assertEqual(result["candidateTotal"], 502)
        self.assertEqual([row["symbol"] for row in result["items"]], ["sz000502"])
        self.assertTrue(result["items"][0]["quoteStale"])
        self.assertIn("分页不完整", result["errors"][0]["message"])

    def test_screen_all_missing_quote_directory_cannot_become_complete_snapshot(self):
        _, quotes = self.screen_fixture(3)
        for row in quotes:
            row["TOTAL_MARKET_CAP"] = None
        result = self.service.finance_screen({"positive": True})
        self.assertEqual((result["scope"], result["status"], result["items"]), ("unavailable", "unavailable", []))
        self.assertIn("全部缺失", result["errors"][0]["message"])

    def test_screen_reports_progress_and_keeps_absent_financial_component_missing(self):
        stages = []
        self.service.owner.progress = lambda *args: stages.append(args)
        self.handler = lambda _, params: report_response([daily_quote()]) if params["reportName"] == "RPT_VALUEANALYSIS_DET" else report_response(REPORTS[:1] if "ISNEW" in params["filter"] else REPORTS[1:2])
        result = self.service.finance_screen()
        self.assertEqual(result["scope"], "complete_catalog")
        self.assertIsNone(result["items"][0]["ttmNetProfit"])
        self.assertIsNone(result["items"][0]["actualPe"])
        self.assertEqual(result["coverage"]["excluded"], 1)
        self.assertTrue(any("2024-06-30" in message for message in result["items"][0]["missing"]))
        self.assertEqual({row[0] for row in stages}, {"整理财务目录", "补充 TTM 财报", "读取全候选同日估值", "全候选整理完成"})

    def test_screen_invalid_filter_parameters_fail_before_network(self):
        for payload in [{"positive": 1}, {"below": "true"}, {"maxPe": -1}, {"maxPe": True},
                        {"scenario": {"pe": 0}}, {"scenario": {"pe": "10"}}, {"scenario": {"emotion": 1}},
                        {"below": True, "scenario": {"pe": None}}, {"sortBy": "not-a-column"}, {"sortOrder": "random"}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.finance_screen(payload)
        self.assertEqual(self.calls, [])

    def test_funds_primary_units_all_period_mappings_zero_and_negative(self):
        self.handler = lambda *_: funds_response([fund_row()])
        expected = [("today", 5043652096, 2.23), ("5d", 9312816816, -1.25), ("10d", 6648629456, 0)]
        for period, amount, change in expected:
            result = self.service.funds_sectors({"period": period})
            self.assertEqual(result["items"][0]["mainNetInflow"], amount)
            self.assertEqual(result["items"][0]["changePercent"], change)
            self.assertEqual(result["units"]["flow"], "元")
            self.assertEqual(self.calls[-1][1]["fs"], "m:90 s:4")

    def test_funds_official_chart_fallback_merges_by_id_not_rank_and_scales_only_percent(self):
        def handler(endpoint, params):
            if endpoint == "funds":
                raise ValueError("即时源离线")
            field = params["key"]
            value = {"f62": -1000, "f3": -223, "f184": 510, "f124": 1757055600}.get(field, 0)
            rows = [{"f12": "BK1033", "f14": "电池", field: value}, {"f12": "BK1034", "f14": "电力", field: 0}]
            return funds_response(rows[::-1] if field == "f3" else rows)
        self.handler = handler
        result = self.service.funds_sectors()
        self.assertEqual(result["adapter"], "official_chart")
        first = result["items"][0]
        self.assertEqual((first["mainNetInflow"], first["changePercent"], first["mainNetRatio"]), (-1000, -2.23, 5.1))
        self.assertEqual(first["smallNetInflow"], 0)
        self.assertEqual(len(self.calls), 9)

    def test_funds_missing_optional_metric_remains_null(self):
        def handler(endpoint, params):
            if endpoint == "funds":
                raise ValueError("离线")
            key = params["key"]
            if key == "f3":
                return funds_response([{"f12": "BK9999", "f14": "不匹配行业", key: 999}])
            return funds_response([{"f12": "BK1033", "f14": "电池", key: fund_row().get(key)}])
        self.handler = handler
        result = self.service.funds_sectors()
        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["items"][0]["changePercent"])
        self.assertIn("目录", "".join(result["warnings"]))

    def test_funds_failed_refresh_keeps_last_valid_amount_and_timestamp(self):
        self.handler = lambda *_: funds_response([fund_row()])
        original = self.service.funds_sectors()
        self.handler = lambda *_: funds_response([{**fund_row(), "f62": "-"}])
        result = self.service.funds_sectors({"refresh": True})
        self.assertTrue(result["stale"])
        self.assertEqual(result["items"], original["items"])
        self.assertEqual(result["receivedAt"], original["receivedAt"])

    def test_rejects_invalid_user_payload_before_network(self):
        cases = [("news_list", {"source": "https://example.com"}), ("news_list", {"page": True}),
                 ("finance_snapshot", {"symbol": "600900"}), ("finance_screen", {"pageSize": 1000}),
                 ("finance_screen", {"query": []}), ("funds_sectors", {"period": []}),
                 ("funds_sectors", {"period": "yesterday"}), ("funds_sectors", {"url": "http://localhost"})]
        for method, payload in cases:
            with self.subTest(method=method, payload=payload), self.assertRaises(ValueError):
                getattr(self.service, method)(payload)
        self.assertEqual(self.calls, [])

    def test_nested_cache_does_not_reset_network_budget(self):
        def load():
            self.service.calls = 30
            return self.service._catalog(True)
        result = self.service._cached("budget", "test", "test", "https://example.com", 0, True, load, {"items": []})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.calls, [])


def peer(symbol="600900.SH", pe=20, day="2025-09-05", industry_id="016007", industry="电力"):
    return {"SECUCODE": symbol, "SECURITY_CODE": symbol[:6], "SECURITY_NAME_ABBR": "样本 " + symbol[:6],
            "BOARD_CODE": industry_id, "BOARD_NAME": industry, "PE_TTM": pe, "TRADE_DATE": day}


def fund_history_response(rows, code="BK0428"):
    return {"rc": 0, "data": {"code": code, "market": 90, "name": "电力", "klines": rows}}


def fund_day(day="2025-09-05", main="-754667424.0"):
    return f"{day},{main},1560801536.0,-806134016.0,-490646784.0,-264020640.0,-2.25,4.65,-2.40,-1.46,-0.79,19323.79,0.02,0.00,0.00"


class BenchmarkAndHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.calls = []
        self.peers = [peer(pe=20), peer("600901.SH", 10), peer("600902.SH", 30), peer("600903.SH", 1000),
                      peer("600904.SH", -3), peer("600905.SH", 0), peer("600906.SH", None)]
        self.history = [{"BOARD_CODE": "016007", "BOARD_NAME": "电力", "TRADE_DATE": day, "PE_TTM": pe}
                        for day, pe in [("2025-09-05", 18), ("2025-09-04", 20), ("2025-09-03", 22)]]
        self.handler = self._finance_fixture
        def fetch(endpoint, params):
            self.calls.append((endpoint, dict(params)))
            return self.handler(endpoint, params)
        self.service = info.MarketInformation(SimpleNamespace(db=self.db), fetcher=fetch)

    def tearDown(self):
        self.db.close()

    def _finance_fixture(self, endpoint, params):
        self.assertEqual(endpoint, "finance")
        if params["reportName"] == "RPT_VALUEINDUSTRY_DET":
            return report_response(self.history)
        return report_response([self.peers[0]] if params["pageSize"] == 1 else self.peers)

    def test_benchmark_same_day_positive_ttm_quantiles_and_exclusions(self):
        result = self.service.finance_benchmark({"symbol": "sh600900"})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sampleCount"], 4)
        self.assertEqual(result["totalCount"], 7)
        self.assertEqual(result["excludedNonPositiveCount"], 2)
        self.assertEqual(result["excludedMissingCount"], 1)
        self.assertEqual((result["medianPE"], result["p25PE"], result["p75PE"], result["meanPE"]), (25, 17.5, 272.5, 265))
        self.assertEqual(result["asOf"], "2025-09-05")
        self.assertEqual(result["subjectPE"], 20)
        self.assertIn("TRADE_DATE='2025-09-05'", self.calls[1][1]["filter"])
        self.assertEqual(result["historical"]["medianPE"], 20)
        self.assertEqual(result["historical"]["startDate"], "2025-09-03")
        self.assertEqual(result["historical"]["endDate"], result["asOf"])
        self.assertEqual(len(self.calls), 3)

    def test_benchmark_subject_pe_uses_same_response_as_peer_distribution(self):
        def changed_single_quote(endpoint, params):
            if params["reportName"] == "RPT_VALUEANALYSIS_DET" and params["pageSize"] == 1:
                return report_response([{**self.peers[0], "PE_TTM": 99}])
            return self._finance_fixture(endpoint, params)
        self.handler = changed_single_quote
        for subject_pe in (20, None, 0, -3):
            with self.subTest(subject_pe=subject_pe):
                self.peers[0]["PE_TTM"] = subject_pe
                result = self.service.finance_benchmark({"symbol": "sh600900", "refresh": True})
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["subjectPE"], subject_pe)
                self.assertEqual(result["subjectPE"], result["items"][0]["peTTM"])
                self.assertEqual(result["medianPE"], 25 if subject_pe == 20 else 30)

    def test_histories_are_separate_and_cannot_include_future_relative_to_snapshot(self):
        self.history[0]["TRADE_DATE"] = "2025-09-06"
        result = self.service.finance_benchmark({"symbol": "sh600900"})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["medianPE"], 25)
        self.assertEqual(result["historical"]["status"], "unavailable")
        self.assertIsNone(result["historicalMedianPE"])
        self.assertTrue(result["errors"])

    def test_peers_reject_mixed_dates_industries_duplicate_and_incomplete_pages(self):
        for field, bad in [("TRADE_DATE", "2025-09-04"), ("BOARD_CODE", "016099"), ("SECUCODE", "600900.SH")]:
            with self.subTest(field=field):
                original = self.peers[1][field]
                self.peers[1][field] = bad
                result = self.service.finance_benchmark({"symbol": "sh600900", "refresh": True})
                self.assertEqual(result["status"], "unavailable")
                self.peers[1][field] = original
        self.handler = lambda e, p: report_response([self.peers[0]]) if p["pageSize"] == 1 else report_response(self.peers, count=8)
        self.assertEqual(self.service.finance_benchmark({"symbol": "sh600900"})["status"], "unavailable")

    def test_benchmark_offline_keeps_original_snapshot_time_and_statistics(self):
        original = self.service.finance_benchmark({"symbol": "sh600900"})
        self.handler = lambda *_: (_ for _ in ()).throw(ValueError("离线"))
        cached = self.service.finance_benchmark({"symbol": "sh600900", "refresh": True})
        self.assertTrue(cached["stale"])
        self.assertEqual(cached["receivedAt"], original["receivedAt"])
        self.assertEqual(cached["medianPE"], original["medianPE"])
        self.assertEqual(cached["asOf"], original["asOf"])

    def test_empty_positive_sample_stays_null_with_coverage(self):
        for index, row in enumerate(self.peers):
            row["PE_TTM"] = [0, -2, None][index % 3]
        result = self.service.finance_benchmark({"symbol": "sh600900"})
        self.assertEqual(result["sampleCount"], 0)
        self.assertIsNone(result["medianPE"])
        self.assertIsNone(result["meanPE"])
        self.assertEqual(result["excludedNonPositiveCount"] + result["excludedMissingCount"], result["totalCount"])

    def test_funds_history_official_field_order_yuan_sign_and_date_sort(self):
        self.handler = lambda *_: fund_history_response([fund_day(), fund_day("2025-09-03", "100")])
        result = self.service.funds_history({"id": "BK0428", "limit": 250})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["availableCount"], 2)
        self.assertEqual(result["requestedLimit"], 250)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["units"]["flow"], "元")
        self.assertEqual([row["date"] for row in result["items"]], ["2025-09-03", "2025-09-05"])
        row = result["items"][-1]
        self.assertEqual(row["mainNetInflow"], -754667424)
        self.assertEqual(row["superLargeNetInflow"], -264020640)
        self.assertEqual(row["largeNetInflow"], -490646784)
        self.assertEqual(row["mediumNetInflow"], -806134016)
        self.assertEqual(row["smallNetInflow"], 1560801536)
        self.assertEqual(row["close"], 19323.79)
        self.assertEqual(row["changePercent"], .02)
        self.assertTrue(any("少于" in value for value in result["warnings"]))

    def test_funds_missing_value_remains_null_and_malformed_refresh_preserves_cache(self):
        self.handler = lambda *_: fund_history_response([fund_day("2025-09-04", "-") , fund_day()])
        original = self.service.funds_history({"id": "BK0428"})
        self.assertIsNone(original["items"][0]["mainNetInflow"])
        for response in [fund_history_response([fund_day()], "BK9999"), fund_history_response([fund_day(), fund_day()]),
                         fund_history_response([fund_day("9999-12-31")]), fund_history_response([fund_day() + ",1"]),
                         fund_history_response([fund_day(main="-")])]:
            with self.subTest(response=response):
                self.handler = lambda *_: response
                result = self.service.funds_history({"id": "BK0428", "refresh": True})
                self.assertTrue(result["stale"])
                self.assertEqual(result["items"], original["items"])
                self.assertEqual(result["receivedAt"], original["receivedAt"])

    def test_funds_range_switch_reuses_source_and_truncates_latest_dates(self):
        days = [(info.dt.date(2025, 1, 1) + info.dt.timedelta(days=index)).isoformat() for index in range(120)]
        self.handler = lambda *_: fund_history_response([fund_day(day) for day in days])
        short = self.service.funds_history({"id": "BK0428", "limit": 30})
        long = self.service.funds_history({"id": "BK0428", "limit": 250})
        self.assertEqual(short["items"], long["items"][-30:])
        self.assertEqual(short["availableCount"], 120)
        self.assertEqual(long["total"], 120)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(long["cached"])

    def test_new_api_validation_happens_before_network(self):
        for payload in [{"id": "../x"}, {"id": "BK0428", "limit": True}, {"id": "BK0428", "limit": 500},
                        {"id": "BK0428", "refresh": "yes"}, {"id": "BK0428", "url": "https://example.com"}]:
            with self.assertRaises(ValueError):
                self.service.funds_history(payload)
        for payload in [{"symbol": "600900"}, {"symbol": "sh600900", "industry": "fake"}]:
            with self.assertRaises(ValueError):
                self.service.finance_benchmark(payload)
        self.assertEqual(self.calls, [])


class RequestBoundaryTests(unittest.TestCase):
    def test_fixed_endpoint_only(self):
        with self.assertRaises(ValueError):
            info.fetch_json("https://example.com", {})

    def test_redirects_are_never_followed(self):
        with self.assertRaises(ValueError):
            info._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "http://127.0.0.1/")

    def test_response_byte_cap_and_dictionary_schema(self):
        class Response(io.BytesIO):
            headers = {}
        for body in [b"[]", b"x" * (info.MAX_BYTES + 1)]:
            with self.subTest(size=len(body)), patch.object(info.urllib.request, "build_opener", return_value=SimpleNamespace(open=lambda *_args, **_kwargs: Response(body))):
                with self.assertRaises(ValueError):
                    info.fetch_json("news", {})

    def test_real_json_and_timeout_headers_without_network(self):
        seen = {}
        class Response(io.BytesIO):
            headers = {"Content-Length": "11"}
        def open_request(request, timeout):
            seen.update(url=request.full_url, timeout=timeout)
            return Response(b'{"ok":true}')
        with patch.object(info.urllib.request, "build_opener", return_value=SimpleNamespace(open=open_request)):
            self.assertEqual(info.fetch_json("news", {"pageSize": 10}), {"ok": True})
        self.assertTrue(seen["url"].startswith(info.ENDPOINTS["news"] + "?"))
        self.assertEqual(seen["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
