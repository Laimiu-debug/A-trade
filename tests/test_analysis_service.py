import datetime as dt
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from service.analysis_service import AnalysisService
from service.market_service import MarketService


def trading_days(count):
    result, day = [], dt.date(2021, 1, 4)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += dt.timedelta(days=1)
    return result


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "tdx"
        (self.root / "vipdoc/sh/lday").mkdir(parents=True)
        self.owner = MarketService(self.base / "state")
        self.analysis = AnalysisService(self.owner)

    def tearDown(self):
        self.analysis.close()
        self.owner.close()
        self.temp.cleanup()

    def write(self, symbol, prices, volumes=None, *, root=None):
        target = (root or self.root) / "vipdoc" / symbol[:2] / "lday" / f"{symbol}.day"
        target.parent.mkdir(parents=True, exist_ok=True)
        days = trading_days(len(prices))
        data = b""
        for i, (day, price) in enumerate(zip(days, prices)):
            cents = round(price * 100)
            volume = volumes[i] if volumes else 10000
            data += struct.pack("<IIIIIfII", int(day.replace("-", "")), cents, cents, cents, cents, price * volume, volume, 0)
        target.write_bytes(data)
        return target

    def scan(self, root=None):
        self.owner.configure({"path": str(root or self.root)})
        self.owner.scan()
        self.analysis.invalidate()

    def call(self, tool, **payload):
        return self.analysis.dispatch("market.analysis." + tool, payload)

    def watch(self, method, **payload):
        return self.analysis.dispatch("market.watchlist." + method, payload)

    def test_trend_conditions_real_returns_and_index_exclusion(self):
        self.write("sh600001", [10 + i / 10 for i in range(100)])
        self.write("sh600002", [20 - i / 10 for i in range(100)])
        self.write("sh000001", [10 + i for i in range(100)])
        self.scan()
        response = self.call("trends")
        self.assertEqual([row["symbol"] for row in response["items"]], ["sh600001"])
        item = response["items"][0]
        self.assertAlmostEqual(item["return60"], (19.9 / 13.9 - 1) * 100, places=5)
        self.assertEqual(item["ma60"], 16.95)
        self.assertTrue(item["aboveMA20"] and item["aboveMA60"])
        self.assertEqual(self.call("trends", minReturn20=200)["items"], [])
        self.assertNotIn("_previousCloseHigh", item)

    def test_moves_use_previous_volume_and_previous_close_high(self):
        prices, volumes = [10] * 80 + [11], [10000] * 80 + [30000]
        self.write("sh600001", prices, volumes)
        self.scan()
        item = self.call("moves")["items"][0]
        self.assertEqual(item["signals"], ["rise", "volume", "breakout"])
        self.assertEqual(item["volumeRatio5"], 3)
        self.assertEqual(item["breakoutHigh"], 10)
        self.assertEqual(self.call("moves", kind="fall")["items"], [])
        self.assertEqual(self.call("moves", kind="volume", volumeThreshold=4)["items"], [])

    def test_rebound_gap_and_prior_streak_are_causal(self):
        prices = [100] * 80 + [110, 121, 119, 117, 128.7, 141.57]
        self.write("sh600001", prices)
        self.scan()
        dates = trading_days(len(prices))
        item = self.call("rebounds")["items"][0]
        self.assertEqual(item["eventDate"], dates[84])
        self.assertEqual(item["priorEndDate"], dates[81])
        self.assertEqual(item["priorBoards"], 2)
        self.assertEqual(item["gapDays"], 2)
        self.assertEqual(self.call("rebounds", maxGap=1)["items"], [])
        self.assertEqual(self.call("rebounds", minBoards=3)["items"], [])
        self.assertEqual(self.call("rebounds", asOf=dates[83])["items"], [])
        self.assertEqual(self.call("rebounds", asOf=dates[84])["items"][0]["eventDate"], dates[84])
        self.assertEqual(self.call("rebounds", lookbackDays=1)["items"], [])

    def test_ladder_groups_and_history_no_future_influence(self):
        prices = [100] * 80 + [110, 121, 119, 117, 128.7, 141.57]
        self.write("sh600001", prices)
        self.scan()
        dates = trading_days(len(prices))
        current = self.call("ladder")
        self.assertEqual(current["groups"][0]["height"], 2)
        self.assertEqual(len(current["timeline"]), 60)
        prior = self.call("ladder", asOf=dates[84])
        self.assertEqual(prior["groups"][0]["height"], 1)
        self.assertEqual(prior["timeline"][-1], current["timeline"][-2])
        self.assertEqual(prior["asOf"], dates[84])

    def test_missed_day_breaks_confirmation_without_inventing_break_event(self):
        prices = [100] * 80 + [110, 121, 119, 117, 128.7]
        target = self.write("sh600001", prices)
        self.write("sh600002", [100] * len(prices))
        raw = target.read_bytes()
        target.write_bytes(raw[:82 * 32] + raw[83 * 32:])
        self.scan()
        self.assertEqual(self.call("rebounds")["items"], [])

    def test_asof_before_listing_and_after_trading_day_never_fills_future(self):
        self.write("sh600001", [100] * 80)
        self.scan()
        self.assertEqual(self.call("ladder", asOf="2020-12-31")["items"], [])
        self.assertIsNone(self.call("ladder", asOf="2020-12-31")["asOf"])
        result = self.call("ladder", asOf="2022-01-01")
        self.assertEqual(result["asOf"], trading_days(80)[-1])
        self.assertTrue(any("最近实际交易日" in message for message in result["warnings"]))

    def test_stale_and_zero_volume_do_not_enter_trend_or_current_ladder(self):
        self.write("sh600001", [10 + i / 10 for i in range(100)], [10000] * 99 + [0])
        self.write("sh600002", [10 + i / 10 for i in range(99)])
        self.scan()
        self.assertEqual(self.call("trends")["items"], [])
        self.assertEqual(self.call("ladder")["items"], [])
        self.assertEqual(self.call("trends")["coverage"]["stale"], 1)

    def test_source_isolation_refresh_and_read_only_files(self):
        target = self.write("sh600001", [100] * 80 + [110])
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        self.scan()
        self.assertEqual(self.call("moves")["total"], 1)
        self.assertTrue(self.call("overview")["cached"])
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        self.write("sh600001", [100] * 81)
        self.assertEqual(self.call("moves", refresh=True)["total"], 0)
        alternate = self.base / "other"
        self.write("sh600002", [100] * 80 + [90], root=alternate)
        self.scan(alternate)
        result = self.call("moves")
        self.assertEqual([row["symbol"] for row in result["items"]], ["sh600002"])

    def test_watchlist_persists_and_edits_are_independent_from_source(self):
        self.write("sh600001", [100] * 80)
        self.scan()
        added = self.watch("save", symbol="SH600001", note="计划观察", tags=["趋势", "趋势"], status="planned", name="ignored")
        self.assertEqual(added["symbol"], "sh600001")
        self.assertEqual(added["tags"], ["趋势"])
        self.assertNotEqual(added["name"], "ignored")
        edited = self.watch("save", symbol="sh600001", status="holding")
        self.assertEqual(edited["note"], "计划观察")
        self.assertEqual(edited["createdAt"], added["createdAt"])
        self.owner.close()
        self.owner = MarketService(self.base / "state")
        self.analysis = AnalysisService(self.owner)
        self.assertEqual(self.watch("list", status="holding")["total"], 1)
        self.analysis.invalidate()
        self.owner.research.invalidate()
        self.assertEqual(self.watch("list")["total"], 1)
        self.assertTrue(self.watch("delete", symbol="sh600001")["deleted"])
        self.assertFalse(self.watch("delete", symbol="sh600001")["deleted"])

    def test_watchlist_rejects_unknown_identity_and_invalid_mutations(self):
        self.write("sh600001", [100] * 80)
        self.scan()
        for payload in ({"symbol": "600001"}, {"symbol": "sh600099"}, {"symbol": "sh600001", "note": "x" * 2001},
                        {"symbol": "sh600001", "tags": ["x"] * 9}, {"symbol": "sh600001", "status": "buy_now"}):
            with self.subTest(payload=list(payload)), self.assertRaises(ValueError):
                self.watch("save", **payload)
        self.assertEqual(self.watch("list")["total"], 0)

    def test_quick_watch_atomically_appends_without_resetting_existing_fields(self):
        self.write("sh600001", [100] * 80)
        self.scan()
        old = self.watch("save", symbol="sh600001", note="原观察", tags=["趋势"], status="holding")
        result = self.watch("save", symbol="sh600001", appendNote="  等待量能确认  ")
        self.assertEqual(result["note"], "原观察\n\n等待量能确认")
        for key in ("status", "tags", "createdAt", "name"):
            self.assertEqual(result[key], old[key])
        self.assertEqual(self.watch("save", symbol="sh600001")["note"], result["note"])
        for payload in ({"appendNote": "x" * 2000}, {"appendNote": " "}, {"appendNote": "x", "note": "replace"}, {"appendNote": True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.watch("save", symbol="sh600001", **payload)
        self.assertEqual(self.watch("list")["items"][0]["note"], result["note"])

    def test_quick_watch_new_entry_uses_default_status_and_reason(self):
        self.write("sh600001", [100] * 80)
        self.scan()
        result = self.watch("save", symbol="sh600001", appendNote="日线突破观察")
        self.assertEqual(result["note"], "日线突破观察")
        self.assertEqual(result["status"], "watching")
        self.assertEqual(result["tags"], [])

    def test_parameters_are_bounded_and_boolean_not_numeric(self):
        for tool, payload in (("trends", {"minReturn20": True}), ("trends", {"requireAboveMA20": 1}),
                              ("moves", {"breakoutDays": 1000}), ("moves", {"kind": "invented"}),
                              ("rebounds", {"minGap": 8, "maxGap": 2}), ("ladder", {"historyDays": 2000}),
                              ("overview", {"asOf": "2099-01-01"}), ("overview", {"refresh": "yes"})):
            with self.subTest(tool=tool, payload=payload), self.assertRaises(ValueError):
                self.call(tool, **payload)
        json.dumps(self.call("overview"), allow_nan=False)

    def test_ladder_filters_candidates_but_keeps_market_evidence_and_two_board_fallback(self):
        self.write("sh600001", [100] * 80 + [100, 110, 121, 133.1])
        self.write("sz300001", [100] * 80 + [120, 144, 172.8, 207.36])
        self.write("sh600003", [100] * 80 + [105, 110.25, 115.76, 121.55])
        self.scan()
        self.owner.db.execute("UPDATE securities SET name='ST样本' WHERE symbol='sh600003'")
        self.owner.db.commit()
        all_rows = self.call("ladder", excludeST=False)
        main = self.call("ladder", board="main")
        self.assertEqual(main["timeline"], all_rows["timeline"])
        self.assertEqual(main["leadership"]["metrics"], all_rows["leadership"]["metrics"])
        self.assertEqual([item["symbol"] for item in main["leaders"]], ["sh600001"])
        self.assertEqual([item["symbol"] for item in main["yesterdayLeaders"]], ["sh600001"])
        self.assertEqual(main["yesterdayLeaders"][0]["previousHeight"], 2)
        with_st = self.call("ladder", board="main", excludeST=False)
        self.assertEqual([item["symbol"] for item in with_st["yesterdayLeaders"]], ["sh600003"])
        height_four = self.call("ladder", board="main", minHeight=4)
        self.assertEqual(height_four["leaders"], [])
        self.assertEqual([item["symbol"] for item in height_four["items"]], ["sh600001"])


if __name__ == "__main__":
    unittest.main()
