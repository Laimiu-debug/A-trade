"""Tests for master-trader delivery slip SQLite database and TDX reverse-engineering analyzer."""
from __future__ import annotations

import base64
import datetime as dt
import struct
import tempfile
import unittest
from pathlib import Path

from service import tdx
from service.intraday import LC1_RECORD
from service.market_service import MarketService


def _trading_dates(start_iso: str, count: int) -> list[str]:
    day = dt.date.fromisoformat(start_iso)
    result = []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += dt.timedelta(days=1)
    return result


def _write_day_file(path: Path, bars: list[tuple[str, float, float, float, float, float, int]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = bytearray()
    for day_str, o, h, l, c, amt, vol in bars:
        date_int = int(day_str.replace("-", ""))
        buf.extend(
            tdx.DAY.pack(
                date_int,
                int(round(o * 100)),
                int(round(h * 100)),
                int(round(l * 100)),
                int(round(c * 100)),
                float(amt),
                int(vol),
                0,
            )
        )
    path.write_bytes(bytes(buf))


def _write_lc1_file(path: Path, day_str: str, minutes: list[tuple[str, float, float, float, float, float, int]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = dt.date.fromisoformat(day_str)
    packed_date = ((d.year - 2004) << 11) + (d.month * 100 + d.day)
    buf = bytearray()
    for hhmm, o, h, l, c, amt, vol in minutes:
        hh, mm = map(int, hhmm.split(":"))
        buf.extend(LC1_RECORD.pack(packed_date, hh * 60 + mm, o, h, l, c, amt, vol, 0))
    path.write_bytes(bytes(buf))


class MasterBacktestTimingTests(unittest.TestCase):
    def test_closing_regime_cannot_change_that_mornings_fills(self):
        from service.master_backtest import _prepare_stock_features, _simulate, _strategy_params

        symbol = "sh600001"
        dates = ["2025-06-03", "2025-06-04"]
        bars = [
            {"date": day, "open": op, "high": close, "low": op, "close": close,
             "volume": 10_000_000, "amount": 500_000_000}
            for day, op, close in [("2025-06-02", 10, 10), (dates[0], 10, 11), (dates[1], 11, 11)]
        ]
        features = {symbol: _prepare_stock_features(symbol, bars)}
        style = "xiwangjiejie_1to2"
        def run(closing_stage):
            return _simulate(style, _strategy_params(style, {}), dates, features, {},
                             {dates[0]: {"stage": "advance"}, dates[1]: {"stage": closing_stage}}, 100_000)

        advance, decline = run("advance"), run("decline")
        self.assertTrue(advance["trades"])
        self.assertEqual(advance["trades"][0]["date"], dates[1])
        self.assertEqual(advance["trades"], decline["trades"])
        self.assertEqual(decline["curve"][-1]["stage"], "decline")


class MasterSlipsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws_dir = self.root / "workspace"
        self.tdx_dir = self.root / "tdx"
        self.dates = _trading_dates("2025-06-02", 90)
        self._build_tdx_fixture()
        self.market = MarketService(self.ws_dir)
        self.market.configure({"path": str(self.tdx_dir)})
        self.market.scan()
        # Give clean readable names in catalogue to test name -> code resolution
        with self.market.db:
            self.market.db.execute("UPDATE securities SET name='推土机龙头' WHERE symbol='sh600101'")
            self.market.db.execute("UPDATE securities SET name='趋势中军' WHERE symbol='sz000201'")
            self.market.db.execute("UPDATE securities SET name='止损样本' WHERE symbol='sh600102'")
            self.market.db.execute("UPDATE securities SET name='上证指数' WHERE symbol='sh000001'")

    def tearDown(self):
        self.market.close()
        self.tmp.cleanup()

    def _build_tdx_fixture(self):
        # Benchmark index sh000001
        bench = []
        px = 3100.0
        for i, d in enumerate(self.dates):
            px = round(px * (1.001 if i < 75 else 0.994), 2)
            bench.append((d, px * 0.998, px * 1.005, px * 0.995, px, 3.5e11, 2_000_000))
        _write_day_file(self.tdx_dir / "vipdoc/sh/lday/sh000001.day", bench)

        # Create 30 background stocks so market breadth / emotion cycle has >= 20 valid stocks
        for idx in range(30):
            sym = f"sh{600200 + idx}"
            bars = []
            c = 10.0 + (idx % 5)
            for i, d in enumerate(self.dates):
                # Days 50-65 strong advance (some limit-ups), days 75-85 decline
                if 50 <= i <= 65:
                    step = 1.10 if idx < 5 and i in (52, 53, 54, 55) else 1.012
                elif 75 <= i <= 85:
                    step = 0.90 if idx < 6 and i in (77, 78) else 0.985
                else:
                    step = 1.002 if idx % 2 == 0 else 0.999
                nxt = round(c * step, 2)
                o = round(c * 1.001, 2)
                h = round(max(o, nxt) * 1.01, 2)
                l = round(min(o, nxt) * 0.99, 2)
                amt = 2.0e8 if idx < 5 else 8.0e7
                bars.append((d, o, h, l, nxt, amt, int(amt / max(0.1, nxt))))
                c = nxt
            _write_day_file(self.tdx_dir / f"vipdoc/sh/lday/{sym}.day", bars)

        # Stock 1: sh600101 (推土机龙头) - limit up on day 52, 53, 54, then cools on 55-58
        bars1 = []
        c = 10.00
        for i, d in enumerate(self.dates):
            if i in (52, 53, 54):
                nxt = round(c * 1.10, 2)
                o, h, l = round(c * 1.03, 2), nxt, round(c * 1.01, 2)
                amt = 1.8e9
            elif i == 55:
                nxt = round(c * 1.05, 2)
                o, h, l = round(c * 1.04, 2), round(c * 1.09, 2), round(c * 1.02, 2)
                amt = 2.2e9
            elif i in (56, 57, 58):
                nxt = round(c * 0.96, 2)
                o, h, l = round(c * 0.99, 2), round(c * 1.00, 2), round(nxt * 0.99, 2)
                amt = 1.1e9
            else:
                nxt = round(c * 1.003, 2)
                o, h, l = c, round(nxt * 1.01, 2), round(c * 0.99, 2)
                amt = 4.0e8
            bars1.append((d, o, h, l, nxt, amt, int(amt / max(0.1, nxt))))
            c = nxt
        _write_day_file(self.tdx_dir / "vipdoc/sh/lday/sh600101.day", bars1)
        # Also write 1-minute .lc1 for sh600101 on day 53
        d53 = self.dates[53]
        p52 = bars1[52][4]
        _write_lc1_file(
            self.tdx_dir / "vipdoc/sh/minline/sh600101.lc1",
            d53,
            [
                ("09:31", p52 * 1.03, p52 * 1.05, p52 * 1.02, p52 * 1.04, 8e7, 700000),
                ("09:35", p52 * 1.04, p52 * 1.08, p52 * 1.04, p52 * 1.07, 1.2e8, 1000000),
                ("09:40", p52 * 1.07, p52 * 1.10, p52 * 1.07, round(p52 * 1.10, 2), 1.5e8, 1200000),
            ],
        )

        # Stock 2: sz000201 (趋势中军) - steady uptrend pullback on day 60, rises 61-65
        bars2 = []
        c = 20.00
        for i, d in enumerate(self.dates):
            if 35 <= i <= 59:
                nxt = round(c * 1.012, 2)
            elif i == 60:
                nxt = round(c * 0.985, 2)  # pullback entry
            elif 61 <= i <= 65:
                nxt = round(c * 1.035, 2)
            elif 66 <= i <= 70:
                nxt = round(c * 0.98, 2)
            else:
                nxt = round(c * 1.001, 2)
            o = round((c + nxt) / 2, 2)
            h = round(max(o, nxt) * 1.015, 2)
            l = round(min(o, nxt) * 0.985, 2)
            amt = 1.5e9
            bars2.append((d, o, h, l, nxt, amt, int(amt / max(0.1, nxt))))
            c = nxt
        _write_day_file(self.tdx_dir / "vipdoc/sz/lday/sz000201.day", bars2)

        # Stock 3: sh600102 (止损样本) - bought day 77 during decline, cut next day 78 at -4%
        bars3 = []
        c = 15.00
        for i, d in enumerate(self.dates):
            if i == 77:
                nxt = round(c * 0.99, 2)
            elif i == 78:
                nxt = round(c * 0.955, 2)
            elif i in (79, 80, 81):
                nxt = round(c * 0.94, 2)
            else:
                nxt = round(c * 1.001, 2)
            o = round(c, 2)
            h = round(max(o, nxt) * 1.01, 2)
            l = round(min(o, nxt) * 0.99, 2)
            amt = 6.0e8
            bars3.append((d, o, h, l, nxt, amt, int(amt / max(0.1, nxt))))
            c = nxt
        _write_day_file(self.tdx_dir / "vipdoc/sh/lday/sh600102.day", bars3)

    def test_multi_master_crud_name_resolution_dedupe_and_export(self):
        m1 = self.market.dispatch("market.masters.save", {
            "name": "推土机大佬甲",
            "styleTag": "连板主升+趋势低吸",
            "initialCapital": 1_000_000,
            "sourceDesc": "连续实盘截图",
        })["master"]
        m2 = self.market.dispatch("market.masters.save", {
            "name": "波段大佬乙",
            "styleTag": "纯趋势",
        })["master"]

        listed = self.market.dispatch("market.masters.list")["items"]
        self.assertEqual(len(listed), 2)

        # Test name-only OCR row auto-resolves to 6-digit code & symbol via TDX catalogue
        d53, d55 = self.dates[53], self.dates[55]
        parsed = self.market.dispatch("market.masters.import.parse", {
            "masterId": m1["id"],
            "source": "ocr",
            "rows": [
                {
                    "date": d53, "time": "09:35:00", "type": "buy",
                    "code": None, "name": "推土机龙头",
                    "quantity": 10000, "price": 12.80, "importKey": "ocr-batch-1:1",
                },
                {
                    "date": d55, "time": "10:12:00", "type": "sell",
                    "code": "600101", "name": "",
                    "quantity": 10000, "price": 15.90, "importKey": "ocr-batch-1:2",
                },
            ],
        })
        self.assertEqual(parsed["summary"]["valid"], 2)
        self.assertEqual(parsed["rows"][0]["record"]["code"], "600101")
        self.assertEqual(parsed["rows"][0]["record"]["symbol"], "sh600101")
        self.assertEqual(parsed["rows"][1]["record"]["name"], "推土机龙头")

        commit1 = self.market.dispatch("market.masters.import.commit", {
            "masterId": m1["id"],
            "source": "ocr",
            "imageCount": 2,
            "rows": [r["record"] for r in parsed["rows"]],
        })
        self.assertEqual(commit1["inserted"], 2)
        self.assertEqual(commit1["skipped"], 0)

        # Re-importing same importKey skips duplicates cleanly
        commit_dup = self.market.dispatch("market.masters.import.commit", {
            "masterId": m1["id"],
            "source": "ocr",
            "rows": [r["record"] for r in parsed["rows"]],
        })
        self.assertEqual(commit_dup["inserted"], 0)
        self.assertEqual(commit_dup["skipped"], 2)

        # Master 2 stays isolated
        m2_trades = self.market.dispatch("market.masters.trades.list", {"masterId": m2["id"]})
        self.assertEqual(m2_trades["total"], 0)

        # Save a report, export bundle, re-import into a brand-new master
        self.market.dispatch("market.masters.reports.save", {
            "masterId": m1["id"],
            "kind": "playbook",
            "title": "学习手册测试",
            "content": "## 核心规则\n- 截断亏损",
            "model": "test-model",
        })
        bundle = self.market.dispatch("market.masters.export", {"masterId": m1["id"]})
        self.assertEqual(bundle["schema"], "a-trade.master-slips.v1")
        self.assertEqual(len(bundle["trades"]), 2)
        self.assertEqual(len(bundle["reports"]), 1)

        imported = self.market.dispatch("market.masters.importDb", {"bundle": bundle})
        self.assertEqual(imported["insertedTrades"], 2)
        self.assertEqual(imported["insertedReports"], 1)

        # CSV import works seamlessly
        csv_text = f"成交日期,成交时间,买卖标志,证券代码,证券名称,成交数量,成交均价\n{self.dates[60]},14:30:00,买入,000201,趋势中军,5000,26.00\n"
        csv_parsed = self.market.dispatch("market.masters.import.parse", {
            "masterId": m2["id"],
            "filename": "slips.csv",
            "contentBase64": base64.b64encode(csv_text.encode("utf-8")).decode("ascii"),
        })
        self.assertEqual(csv_parsed["summary"]["valid"], 1)
        self.assertEqual(csv_parsed["rows"][0]["record"]["symbol"], "sz000201")

    def test_full_tdx_reverse_engineering_analysis_and_self_comparison(self):
        m = self.market.dispatch("market.masters.save", {
            "name": "推土机样本",
            "styleTag": "接力+趋势",
            "initialCapital": 1_000_000,
        })["master"]
        mid = m["id"]

        # Read real bar closes so prices match fixture realistically
        b1 = {r["date"]: r for r in tdx.parse_daily(tdx.read_stable(self.tdx_dir / "vipdoc/sh/lday/sh600101.day"))}
        b2 = {r["date"]: r for r in tdx.parse_daily(tdx.read_stable(self.tdx_dir / "vipdoc/sz/lday/sz000201.day"))}
        b3 = {r["date"]: r for r in tdx.parse_daily(tdx.read_stable(self.tdx_dir / "vipdoc/sh/lday/sh600102.day"))}

        d53, d54, d55 = self.dates[53], self.dates[54], self.dates[55]
        d60, d64, d65 = self.dates[60], self.dates[64], self.dates[65]
        d77, d78 = self.dates[77], self.dates[78]

        trades = [
            # Episode 1: Relay board win on sh600101 with winning add-on + sell near peak
            {"tradeDate": d53, "tradeTime": "09:35:00", "side": "buy", "code": "600101", "name": "推土机龙头", "quantity": 10000, "price": round(b1[d53]["close"], 2)},
            {"tradeDate": d54, "tradeTime": "09:42:00", "side": "buy", "code": "600101", "name": "推土机龙头", "quantity": 5000, "price": round(b1[d54]["close"], 2)},
            {"tradeDate": d55, "tradeTime": "10:15:00", "side": "sell", "code": "600101", "name": "推土机龙头", "quantity": 15000, "price": round(b1[d55]["high"] * 0.99, 2)},
            # Episode 2: Non-realtime friendly trend pullback on sz000201 with staged take-profit
            {"tradeDate": d60, "tradeTime": "14:40:00", "side": "buy", "code": "000201", "name": "趋势中军", "quantity": 10000, "price": round(b2[d60]["close"], 2)},
            {"tradeDate": d64, "tradeTime": "13:30:00", "side": "sell", "code": "000201", "name": "趋势中军", "quantity": 5000, "price": round(b2[d64]["close"], 2)},
            {"tradeDate": d65, "tradeTime": "14:20:00", "side": "sell", "code": "000201", "name": "趋势中军", "quantity": 5000, "price": round(b2[d65]["close"], 2)},
            # Episode 3: Fast next-day stop loss on sh600102 during decline
            {"tradeDate": d77, "tradeTime": "10:05:00", "side": "buy", "code": "600102", "name": "止损样本", "quantity": 10000, "price": round(b3[d77]["close"], 2)},
            {"tradeDate": d78, "tradeTime": "09:32:00", "side": "sell", "code": "600102", "name": "止损样本", "quantity": 10000, "price": round(b3[d78]["close"], 2)},
        ]
        self.market.dispatch("market.masters.import.commit", {"masterId": mid, "source": "manual", "rows": trades})

        analysis = self.market.dispatch("market.masters.analyze", {
            "masterId": mid,
            "selfSummary": {
                "accountName": "我的实盘",
                "tradeCount": 20,
                "closedCount": 10,
                "winRatePct": 40.0,
                "payoffRatio": 0.9,
                "avgHoldDaysWin": 2.0,
                "avgHoldDaysLoss": 6.5,
                "worstLossPct": -15.2,
                "avgHoldingCount": 4.2,
            },
        })
        self.assertFalse(analysis["empty"])
        self.assertEqual(analysis["summary"]["closedEpisodes"], 3)
        self.assertEqual(analysis["exits"]["winCount"], 2)
        self.assertEqual(analysis["exits"]["lossCount"], 1)
        self.assertAlmostEqual(analysis["exits"]["winRatePct"], 66.7, places=1)
        self.assertGreater(analysis["summary"]["totalPnl"], 0)
        self.assertEqual(analysis["position"]["addWinCount"], 1)
        self.assertEqual(analysis["position"]["addLossCount"], 0)
        self.assertEqual(analysis["exits"]["cutWithin1dPct"], 100.0)

        # Market coverage & 1-minute .lc1 enrichment
        self.assertTrue(analysis["coverage"]["tdxConfigured"])
        self.assertEqual(analysis["coverage"]["tradesWithDailyBar"], 8)
        self.assertGreaterEqual(analysis["coverage"]["tradesWithIntraday"], 1)
        self.assertTrue(analysis["environment"]["available"])
        self.assertGreater(len(analysis["curve"]), 10)

        # Cross-section control group ranks high-turnover leaders near top of market
        cg = analysis["selection"]["controlGroup"]
        self.assertIsNotNone(cg["medianAmountRankPct"])
        self.assertGreater(cg["medianAmountRankPct"], 80.0)

        # Dual-track non-realtime guidance & self comparison populated
        self.assertGreaterEqual(len(analysis["guidance"]["originalRules"]), 3)
        self.assertEqual(len(analysis["guidance"]["nonRealtimeChecklist"]), 4)
        self.assertGreaterEqual(analysis["guidance"]["nonRealtimeSubset"]["count"], 1)
        self.assertEqual(analysis["selfComparison"]["selfAccountName"], "我的实盘")
        self.assertEqual(len(analysis["selfComparison"]["rows"]), 6)

        # Cache hit on second call, invalidated after deleting a trade
        cached = self.market.dispatch("market.masters.analyze", {"masterId": mid})
        self.assertTrue(cached["cached"])
        trade_list = self.market.dispatch("market.masters.trades.list", {"masterId": mid})["items"]
        self.market.dispatch("market.masters.trades.delete", {"masterId": mid, "ids": [trade_list[0]["id"]]})
        fresh = self.market.dispatch("market.masters.analyze", {"masterId": mid})
        self.assertFalse(fresh["cached"])

    def test_master_backtest_tdx_excludes_bse_uses_local_index_and_runs_via_research_service(self):
        from service.research_service import ResearchService

        # Add 260 BSE (bj*) stocks that would previously crowd out ORDER BY symbol LIMIT 300
        for i in range(260):
            sym = f"bj{430001 + i}"
            bars = [(d, 10.0, 10.2, 9.8, 10.0, 8e7, 8_000_000) for d in self.dates]
            _write_day_file(self.tdx_dir / f"vipdoc/bj/lday/{sym}.day", bars)
        self.market.scan()

        # 1. Direct TDX backtest must exclude bj* and trigger real trades on sh/sz stocks
        res = self.market.dispatch("market.masters.backtest", {
            "styleKey": "wujiang_dynamic",
            "startDate": self.dates[30],
            "endDate": self.dates[-1],
            "dataSource": "tdx",
        })
        self.assertEqual(res["dataSource"], "tdx")
        self.assertGreaterEqual(res["universeCount"], 30)
        self.assertGreater(res["summary"]["tradeCount"], 0)
        self.assertTrue(all(not t["symbol"].startswith("bj") for t in res["trades"]))

        # 2. Durable ResearchService task queue path (kind='masterBacktest')
        rs = ResearchService(self.ws_dir, autostart=False)
        try:
            task = rs.create({
                "kind": "masterBacktest",
                "config": {
                    "styleKey": "wujiang_dynamic",
                    "startDate": self.dates[30],
                    "endDate": self.dates[-1],
                    "dataSource": "tdx",
                },
            })
            self.assertEqual(task["kind"], "masterBacktest")
            self.assertEqual(task["status"], "queued")
            self.assertTrue(rs.run_next())
            detail = rs.get({"id": task["id"], "pageSize": 250})
            self.assertEqual(detail["task"]["status"], "completed")
            self.assertEqual(detail["task"]["progress"], 1)
            self.assertIsNotNone(detail["task"].get("stats"))
            self.assertGreater(detail["task"]["stats"]["trade_count"], 0)
            self.assertEqual(detail["result"]["dataSource"], "tdx")
            self.assertGreater(detail["result"]["summary"]["tradeCount"], 0)

            # Control + export + delete on masterBacktest tasks
            exported = rs.export_report({"id": task["id"]})
            self.assertTrue(exported["completeAudit"])
            deleted = rs.delete({"ids": [task["id"]]})
            self.assertTrue(deleted["outcomes"][0]["ok"])
        finally:
            rs.close()

    def test_master_rules_enforce_caps_trims_adds_and_predicted_orders(self):
        from service.master_rules import plan_next_day_orders, strategy_params

        params_wj = strategy_params("wujiang_dynamic")

        # 1. Partial profit trim (50%), pyramiding on ZT winner within single cap, and cap-block on overweight ZT
        plan1 = plan_next_day_orders(
            style_key="wujiang_dynamic",
            params=params_wj,
            env={"stage": "advance", "zz1000ChangePct": 1.2, "kc50AboveMA20": True, "turnoverYi": 22000.0},
            equity=100000.0,
            cash=30000.0,
            holdings=[
                # +10% non-ZT -> must trim 50% (1000 -> 500)
                {"symbol": "sh600101", "code": "600101", "name": "冲高标", "qty": 1000, "avgCost": 10.0, "peakPrice": 11.2, "lastClose": 11.0, "heldDays": 1},
                # +8% ZT at 15% weight -> must emit add_win up to singleCap (35%)
                {"symbol": "sz000201", "code": "000201", "name": "封板龙", "qty": 1500, "avgCost": 9.26, "peakPrice": 10.0, "lastClose": 10.0, "heldDays": 1},
                # +9% ZT already at 34% weight -> must block add_win via CAP_BLOCK_ADD
                {"symbol": "sh600202", "code": "600202", "name": "重仓龙", "qty": 3400, "avgCost": 9.17, "peakPrice": 10.0, "lastClose": 10.0, "heldDays": 1},
            ],
            features_by_sym={
                "sh600101": {"close": 11.0, "high": 11.2, "pct": 5.0, "ma5": 10.4, "ma20": 9.8, "aboveMA5": True, "aboveMA20": True, "isZt": False, "isDt": False, "bombedZt": False, "consecZt": 0, "board": "main"},
                "sz000201": {"close": 10.0, "high": 10.0, "pct": 10.0, "ma5": 9.2, "ma20": 8.8, "aboveMA5": True, "aboveMA20": True, "isZt": True, "isDt": False, "bombedZt": False, "consecZt": 1, "board": "main"},
                "sh600202": {"close": 10.0, "high": 10.0, "pct": 10.0, "ma5": 9.2, "ma20": 8.8, "aboveMA5": True, "aboveMA20": True, "isZt": True, "isDt": False, "bombedZt": False, "consecZt": 2, "board": "main"},
            },
            candidates=[],
        )
        by_sym1 = {o["symbol"]: o for o in plan1["orders"]}
        self.assertEqual(by_sym1["sh600101"]["actionType"], "trim_profit")
        self.assertEqual(by_sym1["sh600101"]["quantity"], 500)
        self.assertEqual(by_sym1["sz000201"]["actionType"], "add_win")
        self.assertGreater(by_sym1["sz000201"]["quantity"], 0)
        self.assertLessEqual(by_sym1["sz000201"]["afterWeightPct"], 35.5)
        self.assertNotIn("sh600202", by_sym1)
        self.assertTrue(any(b["symbol"] == "sh600202" and b["ruleCode"] == "CAP_BLOCK_ADD" for b in plan1["blockedSignals"]))
        proj_by_sym1 = {h["symbol"]: h for h in plan1["projectedHoldings"]}
        self.assertEqual(proj_by_sym1["sh600101"]["quantity"], 500)
        self.assertEqual(proj_by_sym1["sz000201"]["statusTag"], "浮盈加仓")

        # 2. Decline regime forced de-risking & weak STAR rotation (Wujiang) + 0% shutoff (Songsong)
        plan_dec = plan_next_day_orders(
            style_key="wujiang_dynamic",
            params=params_wj,
            env={"stage": "decline", "zz1000ChangePct": -2.1, "kc50AboveMA20": False, "turnoverYi": 14500.0},
            equity=100000.0,
            cash=20000.0,
            holdings=[
                {"symbol": "sh688981", "code": "688981", "name": "旧科技", "qty": 400, "avgCost": 100.0, "peakPrice": 101.0, "lastClose": 100.0, "heldDays": 1},
                {"symbol": "sh600101", "code": "600101", "name": "主板仓", "qty": 4000, "avgCost": 9.8, "peakPrice": 10.2, "lastClose": 10.0, "heldDays": 1},
            ],
            features_by_sym={
                "sh688981": {"close": 100.0, "high": 101.0, "pct": -0.5, "ma5": 100.0, "ma20": 99.0, "aboveMA5": True, "aboveMA20": True, "isZt": False, "isDt": False, "bombedZt": False, "consecZt": 0, "board": "star"},
                "sh600101": {"close": 10.0, "high": 10.1, "pct": 0.5, "ma5": 9.9, "ma20": 9.5, "aboveMA5": True, "aboveMA20": True, "isZt": False, "isDt": False, "bombedZt": False, "consecZt": 0, "board": "main"},
            },
            candidates=[{"symbol": "sz000555", "code": "000555", "name": "新候选", "score": 82.0, "close": 10.0, "boardCode": "main"}],
        )
        by_sym_dec = {o["symbol"]: o for o in plan_dec["orders"]}
        self.assertEqual(by_sym_dec["sh688981"]["actionType"], "sell_rotate")
        self.assertEqual(by_sym_dec["sh688981"]["quantity"], 400)
        self.assertLessEqual(plan_dec["projectedSummary"]["afterPosPct"], 32.0)

        plan_ss = plan_next_day_orders(
            style_key="songsong_defensive",
            params=strategy_params("songsong_defensive"),
            env={"stage": "decline", "zz1000ChangePct": -1.5, "kc50AboveMA20": False, "turnoverYi": 14000.0},
            equity=100000.0,
            cash=60000.0,
            holdings=[{"symbol": "sh600101", "code": "600101", "name": "趋势股", "qty": 4000, "avgCost": 9.9, "peakPrice": 10.1, "lastClose": 10.0, "heldDays": 1}],
            features_by_sym={"sh600101": {"close": 10.0, "high": 10.1, "pct": 0.2, "ma5": 9.9, "ma20": 9.5, "aboveMA5": True, "aboveMA20": True, "isZt": False, "isDt": False, "bombedZt": False, "consecZt": 0, "board": "main"}},
            candidates=[{"symbol": "sz000555", "code": "000555", "name": "候选", "score": 80.0, "close": 10.0, "boardCode": "main"}],
        )
        self.assertEqual(plan_ss["projectedSummary"]["afterPosPct"], 0.0)
        self.assertEqual(len(plan_ss["projectedHoldings"]), 0)
        self.assertTrue(any(b["ruleCode"] == "DECLINE_SHUTOFF" for b in plan_ss["blockedSignals"]))

        # 3. Backtest & advisor end-to-end return concrete execution actions, holdings timeline, and next-day predicted orders
        bt = self.market.dispatch("market.masters.backtest", {
            "styleKey": "wujiang_dynamic",
            "startDate": self.dates[30],
            "endDate": self.dates[-1],
            "dataSource": "tdx",
            "initialCapital": 100000,
        })
        self.assertIn("activeRules", bt)
        self.assertIn("ruleExecutionStats", bt)
        self.assertIn("finalHoldings", bt)
        self.assertIn("positionTimeline", bt)
        self.assertGreater(len(bt["positionTimeline"]), 0)
        self.assertIn("nextDayOrders", bt)
        self.assertIsNotNone(bt["nextDayOrders"])
        if bt["trades"]:
            t0 = bt["trades"][0]
            self.assertIn("actionType", t0)
            self.assertIn("actionLabel", t0)
            self.assertIn("beforeStockWeightPct", t0)
            self.assertIn("afterStockWeightPct", t0)
            self.assertIn("afterTotalPosPct", t0)

        adv = self.market.dispatch("market.masters.advise", {
            "styleKey": "wujiang_dynamic",
            "targetDate": self.dates[-1],
            "positions": [
                {"code": "600101", "name": "推土机龙头", "quantity": 2000, "averageCost": 10.0},
                {"code": "000201", "name": "趋势中军", "quantity": 2000, "averageCost": 15.0},
            ],
            "accountSummary": {"equity": 100000.0},
        })
        self.assertIn("predictedOrders", adv)
        self.assertIn("projectedHoldings", adv)
        self.assertIn("projectedSummary", adv)
        self.assertIn("activeLimits", adv)
        self.assertEqual(len(adv["holdingActions"]), 2)
        for o in adv["predictedOrders"]:
            self.assertEqual(o["quantity"] % 100, 0)
            self.assertGreater(o["quantity"], 0)

    def test_stock_markers_and_single_stock_master_simulation(self):
        from service.research_service import ResearchService

        # Seed real master slips (open buy + add buy + sell) on sh600101
        created = self.market.dispatch("market.masters.save", {"name": "标点测试大佬", "styleTag": "情绪龙头"})
        mid = created["master"]["id"]
        self.market.dispatch("market.masters.import.commit", {
            "masterId": mid,
            "source": "manual",
            "rows": [
                {"tradeDate": self.dates[52], "tradeTime": "09:35:00", "side": "buy", "code": "600101", "name": "推土机龙头", "quantity": 1000, "price": 11.0},
                {"tradeDate": self.dates[53], "tradeTime": "09:40:00", "side": "buy", "code": "600101", "name": "推土机龙头", "quantity": 500, "price": 12.1},
                {"tradeDate": self.dates[56], "tradeTime": "10:05:00", "side": "sell", "code": "600101", "name": "推土机龙头", "quantity": 1500, "price": 13.5},
            ],
        })

        # Run a durable masterBacktest via ResearchService so research.sqlite3 holds completed master trades
        rs = ResearchService(self.ws_dir, autostart=False)
        try:
            task = rs.create({
                "kind": "masterBacktest",
                "config": {
                    "styleKey": "wujiang_dynamic",
                    "startDate": self.dates[30],
                    "endDate": self.dates[-1],
                    "dataSource": "tdx",
                },
            })
            self.assertTrue(rs.run_next())
            detail = rs.get({"id": task["id"], "pageSize": 250})
            traded_syms = [t["symbol"] for t in (detail["result"].get("trades") or [])]
        finally:
            rs.close()

        # 1. Verify market.masters.stockMarkers aggregates slips for sh600101
        m_res = self.market.dispatch("market.masters.stockMarkers", {"symbol": "sh600101"})
        self.assertEqual(m_res["symbol"], "sh600101")
        self.assertEqual(len(m_res["masterSlips"]), 3)
        leg_types = {s["legType"] for s in m_res["masterSlips"]}
        self.assertEqual(leg_types, {"slip_buy", "slip_add", "slip_sell"})
        self.assertEqual(len(m_res["availableStyles"]), 6)

        # Verify saved masterBacktest trades are aggregated for any symbol traded in the run
        if traded_syms:
            bt_sym = traded_syms[0]
            m_bt = self.market.dispatch("market.masters.stockMarkers", {"symbol": bt_sym})
            self.assertGreater(len(m_bt["masterBacktestTrades"]), 0)
            self.assertGreater(len(m_bt["masterBacktestRuns"]), 0)
            self.assertIn("actionType", m_bt["masterBacktestTrades"][0])
            self.assertIn("actionLabel", m_bt["masterBacktestTrades"][0])

        # 2. Verify single-stock instant master simulation (market.masters.stockSimulate)
        sim = self.market.dispatch("market.masters.stockSimulate", {
            "symbol": "sh600101",
            "styleKey": "wujiang_dynamic",
            "startDate": self.dates[30],
            "endDate": self.dates[-1],
            "initialCapital": 100000,
        })
        self.assertEqual(sim["symbol"], "sh600101")
        self.assertEqual(sim["styleKey"], "wujiang_dynamic")
        self.assertIn("summary", sim)
        self.assertIn("curve", sim)
        self.assertGreater(len(sim["curve"]), 10)
        self.assertIn("equity", sim["curve"][0])
        self.assertIn("returnPct", sim["curve"][0])
        self.assertIn("ruleExecutionStats", sim)
        self.assertIn("nextDayOrders", sim)
        self.assertGreater(sim["totalTrades"], 0)
        for tr in sim["trades"]:
            self.assertEqual(tr["symbol"], "sh600101")
            self.assertIn("actionType", tr)
            self.assertIn("actionLabel", tr)
            self.assertIn("afterStockWeightPct", tr)

    def test_custom_position_counts_curve_enrichment_and_comparison(self):
        from service.master_rules import resolve_position_limits, strategy_params
        from service.research_service import ResearchService

        # 1. Verify strictly restricted styles (e.g. dongshizhang_highboard=2, xiwangjiejie_1to2=3)
        # preserve native limits by default, and unlock cleanly when customMaxPositions=True
        dsz_default = strategy_params("dongshizhang_highboard")
        lim_def = resolve_position_limits("dongshizhang_highboard", "advance", 100000.0, dsz_default)
        self.assertEqual(lim_def["maxSlots"], 2)
        self.assertLessEqual(lim_def["totalPosCap"], 0.50 + 1e-6)

        dsz_custom6 = strategy_params("dongshizhang_highboard", {"maxPositions": 6, "customMaxPositions": True})
        lim_c6 = resolve_position_limits("dongshizhang_highboard", "advance", 100000.0, dsz_custom6)
        self.assertEqual(lim_c6["maxSlots"], 6)
        self.assertGreater(lim_c6["totalPosCap"], 0.70)

        wj_custom1 = strategy_params("wujiang_dynamic", {"maxPositions": 1, "customMaxPositions": True})
        lim_c1 = resolve_position_limits("wujiang_dynamic", "advance", 100000.0, wj_custom1)
        self.assertEqual(lim_c1["maxSlots"], 1)
        self.assertGreaterEqual(lim_c1["firstEntryWeight"], 0.50)

        # 2. Run backtest with custom maxPositions=1 vs maxPositions=6 + positionCountComparison
        bt1 = self.market.dispatch("market.masters.backtest", {
            "styleKey": "wujiang_dynamic",
            "startDate": self.dates[30],
            "endDate": self.dates[-1],
            "dataSource": "tdx",
            "initialCapital": 100000,
            "maxPositions": 1,
            "customMaxPositions": True,
            "comparePositionCounts": True,
        })
        self.assertTrue(bt1["config"]["customMaxPositions"])
        self.assertEqual(bt1["config"]["maxPositions"], 1)
        self.assertLessEqual(bt1["summary"]["maxHoldingCount"], 1)

        # Curve & metrics must carry both master fields and R04/R09/G03 compatible equity/benchmarkEquity/drawdownPct
        self.assertGreater(len(bt1["curve"]), 10)
        pt0 = bt1["curve"][0]
        for key in ("date", "equity", "ablationEquity", "benchmarkEquity", "returnPct", "drawdownPct", "masterRetPct", "ablationRetPct", "benchRetPct", "masterDdPct", "stageLabel"):
            self.assertIn(key, pt0)
        self.assertIn("metrics", bt1)
        self.assertEqual(bt1["metrics"]["totalReturnPct"], bt1["summary"]["totalReturnPct"])
        self.assertEqual(bt1["metrics"]["maxDrawdownPct"], bt1["summary"]["maxDrawdownPct"])

        # Position count comparison must include 1..10 slots and match current run at maxPositions=1
        comp = bt1.get("positionCountComparison") or []
        self.assertGreaterEqual(len(comp), 6)
        slots_seen = {r["maxPositions"] for r in comp}
        self.assertTrue({1, 2, 3, 4, 5, 6}.issubset(slots_seen))
        cur_rows = [r for r in comp if r.get("isCurrent")]
        self.assertEqual(len(cur_rows), 1)
        self.assertEqual(cur_rows[0]["maxPositions"], 1)
        self.assertEqual(cur_rows[0]["totalReturnPct"], bt1["summary"]["totalReturnPct"])
        for row in comp:
            self.assertLessEqual(row["maxHoldingCount"], row["maxPositions"])
            self.assertGreater(len(row["curve"]), 10)

        # 3. ResearchService paginated trade fetching supports large pageSize + tradeOffset for multi-page UI
        rs = ResearchService(self.ws_dir, autostart=False)
        try:
            task = rs.create({
                "kind": "masterBacktest",
                "config": {
                    "styleKey": "ruhan_halfway",
                    "startDate": self.dates[30],
                    "endDate": self.dates[-1],
                    "dataSource": "tdx",
                    "maxPositions": 8,
                    "customMaxPositions": True,
                },
            })
            self.assertIn("8仓", task["name"])
            self.assertTrue(rs.run_next())
            p1 = rs.get({"id": task["id"], "pageSize": 2, "tradeOffset": 0})
            total_tr = p1["result"]["pagination"]["trades"]["total"]
            if total_tr > 2:
                p2 = rs.get({"id": task["id"], "pageSize": 2, "tradeOffset": 2})
                self.assertGreater(len(p2["result"]["trades"]), 0)
                self.assertNotEqual(p1["result"]["trades"][0], p2["result"]["trades"][0])
        finally:
            rs.close()

        # 4. Advisor respects custom maxPositions & stopLossPct and outputs full scenario playbooks
        adv = self.market.dispatch("market.masters.advise", {
            "styleKey": "dongshizhang_highboard",
            "targetDate": self.dates[54],
            "maxPositions": 5,
            "customMaxPositions": True,
            "stopLossPct": -4.0,
            "accountSummary": {"equity": 100000.0, "cash": 60000.0},
            "positions": [
                {"code": "600101", "name": "推土机龙头", "quantity": 1000, "averageCost": 12.0},
                {"code": "000201", "name": "趋势中军", "quantity": 1000, "averageCost": 22.0},
            ],
        })
        self.assertEqual(adv["activeRules"]["maxPositions"], 5)
        self.assertTrue(adv["activeRules"]["customMaxPositions"])
        self.assertEqual(adv["activeLimits"]["stopLossPct"], -4.0)
        self.assertEqual(len(adv["timelineChecklist"]), 4)

        # Verify every holding has complete 4-scenario playbook (默认 / 涨停怎么做 / 冲高震荡怎么做 / 破位怎么做)
        self.assertEqual(len(adv["holdingActions"]), 2)
        for h in adv["holdingActions"]:
            self.assertGreater(h["limitUpPrice"], h["latestPrice"])
            self.assertAlmostEqual(h["stopPrice"], round(h["averageCost"] * 0.96, 2), places=2)
            sp = h["scenarioPlaybook"]
            self.assertIn("basePlan", sp)
            self.assertIn("limitUpPlan", sp)
            self.assertIn("bombFallback", sp["limitUpPlan"])
            self.assertIn("trimHoldPlan", sp)
            self.assertIn("breakdownPlan", sp)
            self.assertIn("无条件", sp["breakdownPlan"]["detail"])

        # Verify new-position plan explicitly describes open slots, cash, how to buy, abort rules, and post-buy stop
        npp = adv["newPositionPlan"]
        self.assertEqual(npp["maxSlots"], 5)
        self.assertGreaterEqual(npp["openSlots"], 3)
        self.assertGreater(npp["availableCashForNewBuys"], 0)
        if npp["candidatePlaybooks"]:
            c0 = npp["candidatePlaybooks"][0]
            self.assertGreater(c0["plannedQty"], 0)
            self.assertEqual(c0["plannedQty"] % 100, 0)
            self.assertTrue(c0["triggerZone"])
            self.assertTrue(c0["abortConditions"])
            self.assertTrue(c0["postBuyPlan"])


if __name__ == "__main__":
    unittest.main()
