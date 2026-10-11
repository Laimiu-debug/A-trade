"""Source identity, stale-data and minute fallback tests without any network."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from service.intraday import MissingIntraday
from service.market_service import MarketService


DAY1, DAY2, DAY3 = "2026-09-28", "2026-09-29", "2026-09-30"
RECEIVED = "2026-10-01T00:00:00+00:00"


def quote(symbol="sh600900", price=10.5):
    return {"symbol": symbol, "code": symbol[2:], "name": "", "price": price,
            "previousClose": 10.0, "open": 10.0, "high": 11.0, "low": 9.0,
            "volume": 10, "amount": 10500, "volumeUnit": "手", "amountUnit": "元",
            "source": "tdx-mcp", "asOf": None, "receivedAt": RECEIVED,
            "warnings": ["来源未提供行情时间"]}


def mcp_bars(day=DAY2, symbol="sh600900", volume_unit="股"):
    return {"symbol": symbol, "code": symbol[2:], "name": "", "period": "1m",
            "rows": [{"date": day + " 09:31:00", "open": 10, "high": 11, "low": 9,
                      "close": 10.5, "volume": 100, "amount": 1050}],
            "dataAsOf": day + " 09:31:00", "volumeUnit": volume_unit, "amountUnit": "元",
            "adjustment": "none", "source": "tdx-mcp", "receivedAt": RECEIVED, "warnings": []}


def public_minutes(day=DAY3, symbol="sh600900", dates=None):
    return {"symbol": symbol, "code": symbol[2:], "name": "Source name", "date": day,
            "rows": [{"time": "09:31", "open": None, "high": None, "low": None,
                      "close": 10.5, "volume": 10, "amount": 10500, "average": 10.5}],
            "provider": "eastmoney", "volumeUnit": "手", "amountUnit": "元",
            "asOf": day + "T09:31:00+08:00", "receivedAt": RECEIVED,
            "warnings": ["部分开高低数据缺失"], "availableDates": dates or [day], "cached": False}


def minute_record(day=DAY1, minute=571, volume=1000, amount=10500):
    value = dt.date.fromisoformat(day)
    packed_date = ((value.year - 2004) << 11) + value.month * 100 + value.day
    return struct.pack("<HHfffffII", packed_date, minute, 10, 11, 9, 10.5, amount, volume, 0)


class FakeMCP:
    tools = ["tdx_status", "tdx_quotes", "tdx_bars", "tdx_sectors", "tdx_sector_stocks"]

    def __init__(self, owner, root):
        self.owner, self.root, self.closed = owner, str(root), False

    def call(self, tool, arguments):
        self.owner.calls.append((self.root, tool, copy.deepcopy(arguments)))
        value = self.owner.responses.get(tool, ValueError("fixture unavailable"))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value(arguments) if callable(value) else value)

    def close(self):
        self.closed = True


class OnlineMarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.local_root, self.other_root = self.base / "local-a", self.base / "online-b"
        for root in (self.local_root, self.other_root):
            for market in ("sh", "sz", "bj"):
                (root / "vipdoc" / market / "lday").mkdir(parents=True)
            folder = root / "PYPlugins" / "user"
            folder.mkdir(parents=True)
            (folder / "tqcenter.py").write_text("# Never executed by these tests", encoding="utf-8")
            (root / "PYPlugins" / "TPythClient.dll").write_bytes(b"fixture")
            for symbol in ("sh600900", "sh000001"):
                (root / "vipdoc" / "sh" / "lday" / f"{symbol}.day").write_bytes(
                    struct.pack("<IIIIIfII", 20260930, 1000, 1100, 900, 1050, 10500, 1000, 0))
        self.state = self.base / "state"
        self.market = MarketService(self.state)
        self.market.configure({"path": str(self.local_root)})
        self.market.scan()
        self.calls, self.instances = [], []
        self.responses = {"tdx_status": {"connected": True}, "tdx_quotes": {"items": [quote()], "errors": [], "warnings": []}}
        def factory(root, **kwargs):
            client = FakeMCP(self, root)
            self.instances.append(client)
            return client
        self.client_patch = patch("service.mcp_client.MCPClient", side_effect=factory)
        self.client_patch.start()
        # Fail closed if any test forgets to replace the public minute source.
        self.public_patch = patch("service.intraday_online.read_online_intraday", side_effect=ValueError("fixture public source offline"))
        self.public = self.public_patch.start()

    def tearDown(self):
        self.market.close()
        self.public_patch.stop()
        self.client_patch.stop()
        self.temp.cleanup()

    def enable(self, root=None):
        return self.market.online.configure({"enabled": True, "root": str(root or self.local_root), "refreshSeconds": 30})

    def write_minutes(self, *days, root=None, symbol="sh600900"):
        root = root or self.local_root
        path = root / "vipdoc" / symbol[:2] / "minline" / f"{symbol}.lc1"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(minute_record(day) for day in days))
        return path

    def test_online_directory_is_independent_and_persists(self):
        self.enable(self.other_root)
        self.market.close()
        self.market = MarketService(self.state)
        self.assertEqual(self.market.status()["path"], str(self.local_root))
        self.assertEqual(self.market.online.config()["root"], str(self.other_root))
        self.assertTrue(self.market.online.config()["enabled"])
        self.market.configure({"path": str(self.other_root)})
        self.market.configure({"path": str(self.local_root)})
        self.assertEqual(self.market.online.config()["root"], str(self.other_root))

    def test_follow_local_directory_survives_restart_and_partial_updates(self):
        result = self.market.online.configure({"enabled": True, "followLocal": True, "root": "", "refreshSeconds": 30})
        self.assertTrue(result["followLocal"])
        self.assertEqual(result["root"], str(self.local_root))
        self.assertEqual(result["configuredRoot"], "")
        self.assertEqual(self.market._get("onlineConfig")["root"], "")
        self.market.online.configure({"refreshSeconds": 60})
        self.market.close()
        self.market = MarketService(self.state)
        self.market.configure({"path": str(self.other_root)})
        result = self.market.online.status()
        self.assertTrue(result["followLocal"])
        self.assertEqual(result["root"], str(self.other_root))
        self.assertEqual(result["refreshSeconds"], 60)

    def test_legacy_fixed_directory_stays_fixed_and_empty_directory_follows(self):
        for stored_root, follows in ((str(self.other_root), False), ("", True)):
            with self.subTest(root=stored_root):
                with self.market.db:
                    self.market._set("onlineConfig", {"enabled": False, "root": stored_root, "refreshSeconds": 30})
                result = self.market.online.status()
                self.assertEqual(result["followLocal"], follows)
                self.market.online.configure({"refreshSeconds": 45})
                self.market.configure({"path": str(self.other_root)})
                self.market.configure({"path": str(self.local_root)})
                self.assertEqual(self.market.online.config()["root"], str(self.local_root) if follows else stored_root)
        result = self.market.online.configure({"root": ""})
        self.assertTrue(result["followLocal"])
        self.assertEqual(self.market._get("onlineConfig")["root"], "")

    def test_following_a_new_directory_closes_client_clears_check_and_isolates_cache(self):
        self.market.online.configure({"enabled": True, "followLocal": True, "refreshSeconds": 30})
        self.market.online.quotes({"symbols": ["sh600900"]})
        client = self.instances[-1]
        self.assertTrue(self.market.online.connected)
        self.assertIsNotNone(self.market.online.checked)
        self.market.configure({"path": str(self.other_root)})
        status = self.market.online.status()
        self.assertTrue(client.closed)
        self.assertFalse(status["connected"])
        self.assertIsNone(status["lastCheckedAt"])
        self.responses["tdx_quotes"] = ValueError("fixture offline")
        self.assertEqual(self.market.online.quotes({"symbols": ["sh600900"]})["items"], [])
        self.assertEqual(self.instances[-1].root, str(self.other_root))

    def test_explicit_fixed_directory_can_replace_follow_mode(self):
        self.market.online.configure({"followLocal": True})
        result = self.market.online.configure({"followLocal": False, "root": str(self.other_root)})
        self.assertFalse(result["followLocal"])
        self.assertEqual(result["configuredRoot"], str(self.other_root))
        self.market.configure({"path": str(self.other_root)})
        self.market.configure({"path": str(self.local_root)})
        self.assertEqual(self.market.online.config()["root"], str(self.other_root))

    def test_invalid_configuration_preserves_prior_configuration(self):
        before = self.enable()
        for payload in ({"refreshSeconds": True}, {"refreshSeconds": 5}, {"enabled": "yes"},
                        {"root": "relative/path"}, {"root": str(self.base / "missing")},
                        {"followLocal": "yes"}, {"followLocal": False, "root": ""}):
            with self.assertRaises(ValueError):
                self.market.online.configure(payload)
            self.assertEqual(self.market.online.config()["root"], before["root"])
            self.assertTrue(self.market.online.config()["enabled"])

    def test_quote_time_units_and_actual_change_are_preserved(self):
        self.enable()
        data = self.market.online.quotes({"symbols": ["SH600900", "sh600900"]})
        item = data["items"][0]
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(item["change"], 0.5)
        self.assertEqual(item["changePercent"], 5)
        self.assertIsNone(item["asOf"])
        self.assertEqual(item["receivedAt"], RECEIVED)
        self.assertEqual((item["volumeUnit"], item["amountUnit"], item["volume"]), ("手", "元", 10))
        self.assertFalse(item["cached"])

    def test_connection_failure_preserves_cache_time_and_marks_stale(self):
        self.enable()
        self.market.online.quotes({"symbols": ["sh600900"]})
        self.responses["tdx_quotes"] = TimeoutError("fixture timeout")
        result = self.market.online.quotes({"symbols": ["sh600900"]})
        item = result["items"][0]
        self.assertTrue(item["cached"])
        self.assertEqual(item["receivedAt"], RECEIVED)
        self.assertIsNone(item["asOf"])
        self.assertTrue(result["errors"])
        self.assertTrue(any("不是本次更新" in message for message in item["warnings"]))
        self.assertFalse(self.market.online.connected)

    def test_disabling_stops_queries_and_keeps_only_labelled_cache(self):
        self.enable()
        self.market.online.quotes({"symbols": ["sh600900"]})
        self.market.online.configure({"enabled": False})
        previous_calls = len(self.calls)
        result = self.market.online.quotes({"symbols": ["sh600900"]})
        self.assertEqual(len(self.calls), previous_calls)
        self.assertTrue(result["items"][0]["cached"])
        self.assertTrue(result["errors"])

    def test_quote_cache_never_crosses_selected_terminal(self):
        self.enable()
        self.market.online.quotes({"symbols": ["sh600900"]})
        self.enable(self.other_root)
        self.responses["tdx_quotes"] = ValueError("offline")
        self.assertEqual(self.market.online.quotes({"symbols": ["sh600900"]})["items"], [])
        self.enable(self.local_root)
        result = self.market.online.quotes({"symbols": ["sh600900"]})
        self.assertTrue(result["items"][0]["cached"])

    def test_partial_quote_failure_retains_only_failed_symbols_as_stale(self):
        self.enable()
        self.responses["tdx_quotes"] = {"items": [quote(), quote("sh000001")], "errors": [], "warnings": []}
        self.market.online.quotes({"symbols": ["sh600900", "sh000001"]})
        self.responses["tdx_quotes"] = {"items": [quote(price=11)], "errors": [{"symbol": "sh000001", "message": "unavailable"}], "warnings": []}
        result = self.market.online.quotes({"symbols": ["sh600900", "sh000001"]})
        indexed = {item["symbol"]: item for item in result["items"]}
        self.assertEqual(set(indexed), {"sh600900", "sh000001"})
        self.assertFalse(indexed["sh600900"]["cached"])
        self.assertEqual(indexed["sh600900"]["price"], 11)
        self.assertTrue(indexed["sh000001"]["cached"])
        self.assertTrue(result["errors"])

    def test_wrong_identity_does_not_partially_commit_or_duplicate_batch(self):
        self.enable()
        self.market.online.quotes({"symbols": ["sh600900"]})
        self.responses["tdx_quotes"] = {"items": [quote(price=11), quote("sz000001")], "warnings": []}
        result = self.market.online.quotes({"symbols": ["sh600900", "sh000001"]})
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["items"][0]["cached"])
        self.assertEqual(result["items"][0]["price"], 10.5)

    def test_historical_local_minutes_do_not_need_network(self):
        self.enable()
        self.write_minutes(DAY1, DAY2)
        data = self.market.intraday({"symbol": "sh600900", "date": DAY1})
        self.assertEqual(data["provider"], "tdx_lc1")
        self.assertEqual(data["date"], DAY1)
        self.assertEqual(data["volumeUnit"], "股")
        self.assertEqual(data["rows"][0]["volume"], 1000)
        self.assertEqual(data["rows"][0]["amount"], 10500)
        self.assertEqual(self.calls, [])
        self.public.assert_not_called()

    def test_missing_requested_day_never_silently_returns_a_different_day(self):
        self.enable()
        self.write_minutes(DAY1)
        self.responses["tdx_bars"] = mcp_bars(DAY2)
        self.public.side_effect = MissingIntraday("fixture missing", [DAY2])
        data = self.market.intraday({"symbol": "sh600900", "date": DAY3})
        self.assertEqual(data["date"], DAY3)
        self.assertEqual(data["rows"], [])
        self.assertIsNone(data["asOf"])
        self.assertEqual(data["availableDates"], [DAY1, DAY2])
        args = next(arguments for _, tool, arguments in self.calls if tool == "tdx_bars")
        self.assertEqual((args["start"], args["end"]), (DAY3, DAY3))

    def test_latest_minutes_choose_latest_real_source_and_preserve_units_and_nulls(self):
        self.enable()
        self.write_minutes(DAY1)
        self.responses["tdx_bars"] = mcp_bars(DAY2)
        self.public.side_effect = None
        self.public.return_value = public_minutes(DAY3)
        data = self.market.intraday({"symbol": "sh600900"})
        self.assertEqual((data["date"], data["provider"]), (DAY3, "eastmoney"))
        self.assertEqual(data["availableDates"], [DAY1, DAY2, DAY3])
        self.assertEqual((data["volumeUnit"], data["rows"][0]["volume"]), ("手", 10))
        self.assertIsNone(data["rows"][0]["open"])
        self.assertIsNone(data["rows"][0]["high"])

    def test_newer_local_minutes_are_not_replaced_by_older_network_data(self):
        self.enable()
        self.write_minutes(DAY3)
        self.responses["tdx_bars"] = mcp_bars(DAY1)
        self.public.side_effect = None
        self.public.return_value = public_minutes(DAY2)
        data = self.market.intraday({"symbol": "sh600900"})
        self.assertEqual((data["date"], data["provider"]), (DAY3, "tdx_lc1"))
        self.assertEqual(data["availableDates"], [DAY1, DAY2, DAY3])

    def test_cached_minutes_retain_new_available_date_choices(self):
        self.enable()
        source = self.write_minutes(DAY1)
        self.market.intraday({"symbol": "sh600900", "date": DAY1})
        source.write_bytes(minute_record(DAY2))
        self.responses["tdx_bars"] = mcp_bars(DAY3)
        self.public.side_effect = MissingIntraday("fixture missing", [DAY3])
        data = self.market.intraday({"symbol": "sh600900", "date": DAY1})
        self.assertTrue(data["cached"])
        self.assertEqual(data["date"], DAY1)
        self.assertEqual(data["availableDates"], [DAY1, DAY2, DAY3])

    def test_latest_request_prefers_newer_cache_over_old_local_download(self):
        self.enable()
        self.write_minutes(DAY1)
        self.responses["tdx_bars"] = mcp_bars(DAY3)
        first = self.market.intraday({"symbol": "sh600900", "date": DAY3})
        self.assertEqual(first["provider"], "tdx_mcp")
        self.responses["tdx_bars"] = ValueError("fixture offline")
        data = self.market.intraday({"symbol": "sh600900"})
        self.assertEqual(data["date"], DAY3)
        self.assertTrue(data["cached"])
        self.assertEqual(data["receivedAt"], RECEIVED)

    def test_same_day_older_local_and_network_cannot_regress_complete_minute_cache(self):
        self.enable()
        self.write_minutes(DAY1)
        self.responses["tdx_bars"] = mcp_bars(DAY2)
        complete = public_minutes(DAY3)
        complete["rows"][0]["time"] = "14:59"
        complete["asOf"] = DAY3 + "T14:59:00+08:00"
        self.public.side_effect = None
        self.public.return_value = complete
        original = self.market.intraday({"symbol": "sh600900"})
        self.write_minutes(DAY3)
        self.responses["tdx_bars"] = ValueError("fixture MCP offline")
        self.public.side_effect = ValueError("fixture public offline")
        fallback = self.market.intraday({"symbol": "sh600900"})
        self.assertTrue(fallback["cached"])
        self.assertEqual(fallback["asOf"], original["asOf"])
        self.assertEqual(fallback["receivedAt"], original["receivedAt"])
        self.assertTrue(any("offline" in value for value in fallback["warnings"]))
        saved = json.loads(self.market.db.execute("SELECT data FROM intraday_cache WHERE date=?", (DAY3,)).fetchone()[0])
        self.assertEqual(saved["asOf"], original["asOf"])
        # Even a successfully re-read older minute on the same day is not newer.
        self.responses["tdx_bars"] = mcp_bars(DAY3)
        self.public.side_effect = None
        self.public.return_value = public_minutes(DAY3)
        self.assertEqual(self.market.intraday({"symbol": "sh600900"})["asOf"], original["asOf"])
        historical = self.market.intraday({"symbol": "sh600900", "date": DAY3})
        self.assertTrue(historical["cached"])
        self.assertEqual(historical["asOf"], original["asOf"])

    def test_same_day_local_later_than_public_is_preserved_with_failure_warning(self):
        self.enable()
        path = self.write_minutes(DAY3)
        path.write_bytes(minute_record(DAY3, minute=14 * 60 + 59))
        self.responses["tdx_bars"] = ValueError("fixture MCP offline")
        self.public.side_effect = None
        self.public.return_value = public_minutes(DAY3)
        result = self.market.intraday({"symbol": "sh600900"})
        self.assertEqual(result["provider"], "tdx_lc1")
        self.assertTrue(result["asOf"].endswith("14:59:00+08:00"))
        self.assertTrue(any("未能返回" in value for value in result["warnings"]))

    def test_sparse_same_day_update_with_later_or_equal_last_minute_keeps_coverage(self):
        self.enable()
        self.write_minutes(DAY1)
        self.responses["tdx_bars"] = ValueError("fixture offline")
        complete = public_minutes(DAY3)
        times = [f"{minute // 60:02d}:{minute % 60:02d}" for minute in
                 [*range(9 * 60 + 31, 11 * 60 + 31), *range(13 * 60 + 1, 15 * 60)]]
        complete["rows"] = [{**complete["rows"][0], "time": stamp} for stamp in times]
        complete["asOf"] = DAY3 + "T14:59:00+08:00"
        self.public.side_effect = None
        self.public.return_value = complete
        original = self.market.intraday({"symbol": "sh600900"})
        self.assertEqual(len(original["rows"]), 239)
        for stamp in ("14:59", "15:00"):
            with self.subTest(last_minute=stamp):
                sparse = public_minutes(DAY3)
                sparse["rows"][0]["time"] = stamp
                sparse["asOf"] = DAY3 + "T" + stamp + ":00+08:00"
                self.public.return_value = sparse
                actual = self.market.intraday({"symbol": "sh600900"})
                self.assertTrue(actual["cached"])
                self.assertEqual(actual["rows"], original["rows"])
                self.assertTrue(any("记录较少" in value for value in actual["warnings"]))
                saved = json.loads(self.market.db.execute("SELECT data FROM intraday_cache WHERE date=?", (DAY3,)).fetchone()[0])
                self.assertEqual(len(saved["rows"]), 239)

    def test_scan_refreshes_name_cache_in_another_service_connection(self):
        from test_market import write_names
        write_names(self.local_root, "sh", {"600900": "旧名称"})
        self.market.scan()
        peer = MarketService(self.state)
        try:
            self.assertEqual(peer.online.security_name("sh600900"), "旧名称")
            write_names(self.local_root, "sh", {"600900": "新名称"})
            self.market.scan()
            self.assertEqual(peer.online.security_name("sh600900"), "新名称")
        finally:
            peer.close()

    def test_minute_cache_never_crosses_local_or_online_directory(self):
        self.enable()
        source = self.write_minutes(DAY1)
        self.market.intraday({"symbol": "sh600900", "date": DAY1})
        source.unlink()
        self.enable(self.other_root)
        result = self.market.intraday({"symbol": "sh600900", "date": DAY1})
        self.assertEqual(result["rows"], [])
        self.enable(self.local_root)
        self.assertTrue(self.market.intraday({"symbol": "sh600900", "date": DAY1})["cached"])
        self.market.configure({"path": str(self.other_root)})
        self.assertEqual(self.market.intraday({"symbol": "sh600900", "date": DAY1})["rows"], [])

    def test_disabled_online_uses_latest_local_and_never_calls_sources(self):
        self.write_minutes(DAY1, DAY2)
        data = self.market.intraday({"symbol": "sh600900"})
        self.assertEqual(data["date"], DAY2)
        self.assertEqual(data["provider"], "tdx_lc1")
        self.assertEqual(self.calls, [])
        self.public.assert_not_called()

    def test_index_minute_units_are_not_mixed_with_daily_units(self):
        self.write_minutes(DAY1, symbol="sh000001")
        minute = self.market.intraday({"symbol": "sh000001", "date": DAY1})
        daily = self.market.bars({"symbol": "sh000001"})
        self.assertEqual(minute["volumeUnit"], "原始单位")
        self.assertEqual(daily["volumeUnit"], "手")
        self.assertEqual(minute["rows"][0]["volume"], daily["rows"][0]["volume"])
        self.assertTrue(minute["warnings"])

    def test_unindexed_security_daily_bars_use_online_identity_and_units(self):
        self.enable()
        self.responses["tdx_bars"] = mcp_bars(symbol="sz000001")
        data = self.market.bars({"symbol": "sz000001", "limit": 12})
        self.assertEqual(data["symbol"], "sz000001")
        self.assertEqual(data["provider"], "tdx_mcp")
        self.assertEqual(data["volumeUnit"], "股")
        self.assertEqual(data["amountUnit"], "元")
        self.assertFalse(data["cached"])

    @unittest.skipUnless(os.name == "nt" and shutil.which("node") and importlib.util.find_spec("mcp"), "Windows MCP runtime required")
    def test_desktop_forced_market_parent_exit_kills_blocked_native_worker(self):
        from test_mcp_market import FIXTURE_VENDOR, process_alive

        vendor = self.local_root / "PYPlugins" / "user"
        (vendor / "tqcenter.py").write_text(FIXTURE_VENDOR, encoding="utf-8")
        script = """
const fs = require('node:fs');
const {BusinessService} = require('./desktop/business-service.cjs');
const service = new BusinessService(process.argv[1], {mode:'market'});
const root = process.argv[2], blocked = process.argv[3];
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
(async () => {
  try {
    for (const [method,payload] of [
      ['market.configure',{path:root}],
      ['market.online.configure',{root,enabled:true}],
      ['market.online.test',{}]]) {
      const result = await service.request(method,payload);
      if (!result.ok) throw new Error(result.error);
      if (method === 'market.online.test' && !result.data.connected) throw new Error('fixture did not connect');
    }
    const pending = service.request('market.quotes',{symbols:['sh600001']});
    const end = Date.now()+5000;
    while (!fs.existsSync(blocked) && Date.now()<end) await delay(20);
    if (!fs.existsSync(blocked)) throw new Error('native fixture was not blocked');
    const parentPid = service.child.pid;
    const started = Date.now();
    service.close();
    const result = await pending;
    console.log(JSON.stringify({parentPid,ok:result.ok,elapsed:Date.now()-started}));
  } catch (error) {
    service.close(); console.error(error.message); process.exitCode=1;
  }
})();
"""
        env = {**os.environ, "ATRADE_PYTHON": sys.executable, "PYTHONUTF8": "1"}
        result = subprocess.run([shutil.which("node"), "-e", script, str(self.base / "separate-market-state"),
                                 str(self.local_root), str(vendor / "blocked.txt")],
                                cwd=Path(__file__).resolve().parent.parent, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        worker_pid = int((vendor / "worker-pid.txt").read_text())
        self.assertFalse(report["ok"])
        self.assertLess(report["elapsed"], 6000)
        self.assertFalse(process_alive(report["parentPid"]))
        deadline = time.monotonic() + 3
        while process_alive(worker_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_alive(worker_pid), "MCP worker survived its desktop market parent")


if __name__ == "__main__":
    unittest.main()
