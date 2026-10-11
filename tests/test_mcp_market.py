"""Protocol/native-boundary checks use a local fixture, never a trading account."""
import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
import ctypes
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from service.mcp_client import MCPClient, TOOLS, _Connection, _IdleExpired, decode_result
from service.tq_bridge import TQBridge, bars_result, quote_record


HAS_MCP = importlib.util.find_spec("mcp") is not None and importlib.util.find_spec("pandas") is not None


class ConnectionIdleTests(unittest.TestCase):
    def connection(self):
        connection = _Connection.__new__(_Connection)
        connection.queue = asyncio.Queue()
        connection.idle_timeout = 0.001
        connection.idle_expired = threading.Event()
        connection.closed = threading.Event()
        connection.error = None
        return connection

    def test_request_admitted_at_idle_deadline_is_consumed_once(self):
        connection = self.connection()
        item = ("tdx_status", {}, Future())
        async def expired_get(pending, *, timeout):
            pending.close()
            connection.queue.put_nowait(item)
            raise TimeoutError
        with patch("service.mcp_client.asyncio.wait_for", expired_get):
            self.assertIs(asyncio.run(connection._next_request()), item)
        self.assertFalse(connection.idle_expired.is_set())
        self.assertTrue(connection.queue.empty())

    def test_request_callback_after_retirement_is_rejected_before_dispatch(self):
        connection = self.connection()
        self.assertIsNone(asyncio.run(connection._next_request()))
        self.assertTrue(connection.idle_expired.is_set())
        self.assertFalse(connection.closed.is_set())
        future = Future()
        connection._enqueue(("tdx_status", {}, future))
        with self.assertRaises(_IdleExpired):
            future.result(timeout=0.1)
        self.assertTrue(connection.queue.empty())

    def test_retirement_between_request_state_checks_is_reconnectable(self):
        connection = self.connection()
        connection.timeout = 1
        connection.ready = threading.Event()
        connection.ready.set()
        connection.loop = Mock()
        connection.error = RuntimeError("SDK stream closed during idle cleanup")
        def complete_retirement():
            # The request's first idle check already returned false. Retirement
            # and cleanup can finish on the event-loop thread before closed is read.
            connection.idle_expired.set()
            return True
        connection.closed = SimpleNamespace(is_set=complete_retirement)
        with self.assertRaises(_IdleExpired):
            connection.request("tdx_status", {})
        connection.loop.call_soon_threadsafe.assert_not_called()

    def test_idle_retry_is_bounded_and_protocol_failures_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            for failure, expected in ((_IdleExpired("idle"), 2), (RuntimeError("protocol failure"), 1)):
                connections = [SimpleNamespace(closed=threading.Event(), request=Mock(side_effect=failure),
                                               close=Mock(), tools=[]) for _ in range(2)]
                client = MCPClient(directory)
                with patch("service.mcp_client._Connection", side_effect=connections) as create:
                    with self.assertRaises(type(failure)):
                        client.call("tdx_status")
                self.assertEqual(create.call_count, expected)
                for connection in connections[:expected]:
                    connection.request.assert_called_once()
                    connection.close.assert_called_once_with(force=True)

    def test_explicit_close_during_idle_cleanup_prevents_reconnection(self):
        with tempfile.TemporaryDirectory() as directory:
            client = MCPClient(directory)
            connection = SimpleNamespace(closed=threading.Event(), request=Mock(side_effect=_IdleExpired("idle")),
                                         close=Mock(side_effect=lambda **kwargs: client.close()), tools=[])
            with patch("service.mcp_client._Connection", return_value=connection) as create:
                with self.assertRaisesRegex(RuntimeError, "连接已关闭"):
                    client.call("tdx_status")
            create.assert_called_once()


class TQBoundaryTests(unittest.TestCase):
    def test_snapshot_preserves_unknown_time_and_converts_only_known_units(self):
        row = quote_record({"Now": "28.99", "LastClose": "28.90", "Open": "28.98", "Max": "29.4",
                            "Min": "28.91", "Volume": "1331032", "Amount": "387907.63", "ErrorId": "0"}, "sh600900")
        self.assertEqual(row["amount"], 3879076300)
        self.assertEqual(row["volume"], 1331032)
        self.assertEqual(row["volumeUnit"], "手")
        self.assertIsNone(row["asOf"])
        self.assertTrue(row["receivedAt"])
        self.assertTrue(row["warnings"])

    def test_missing_or_nonfinite_quote_values_never_become_zero(self):
        with self.assertRaises(ValueError):
            quote_record({}, "sh600900")
        for bad in ("NaN", "Infinity", -1, None):
            with self.assertRaises(ValueError):
                quote_record({"Now": bad}, "sh600900")

    def test_invalid_queries_do_not_load_vendor_module(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = TQBridge(directory)
            for symbols in ([], ["sh600900"] * 25, ["sh600900", "SH600900"], [123], ["600900"]):
                with self.assertRaises(ValueError):
                    bridge.quotes(symbols)
            for args in ({"symbol": "sh600900", "period": "tick"}, {"symbol": "sh600900", "limit": True},
                         {"symbol": "sh600900", "start": "2026-10-09", "end": "2026-10-08"},
                         {"symbol": "../600900", "period": "1d"}):
                with self.assertRaises(ValueError):
                    bridge.bars(**args)
            self.assertIsNone(bridge._module)

    def test_missing_tq_is_a_disconnected_status(self):
        with tempfile.TemporaryDirectory() as directory:
            status = TQBridge(directory).status()
            self.assertFalse(status["connected"])
            self.assertTrue(status["lastError"])

    def test_client_rejects_non_read_tools_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            client = MCPClient(directory)
            with self.assertRaises(ValueError):
                client.call("place_order", {})
            self.assertIsNone(client._connection)

    def test_protocol_response_validation(self):
        def result(value, error=False):
            return SimpleNamespace(isError=error, content=[SimpleNamespace(type="text", text=value)])
        self.assertEqual(decode_result(result('{"success":true,"data":{"x":1}}')), {"x": 1})
        for value in ('{"success":true,"data":[]}', '{"success":1}', 'invalid'):
            with self.assertRaises(RuntimeError):
                decode_result(result(value))
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            decode_result(result('{"success":false,"error":"fixture"}'))


@unittest.skipUnless(HAS_MCP, "requires optional market MCP runtime")
class TQFrameTests(unittest.TestCase):
    def frames(self, symbol="600900.SH", indices=None):
        import pandas as pd
        indices = indices or [datetime(2026, 9, 30, 15, 0)]
        values = {"Open": 28.54, "High": 28.54, "Low": 28.54, "Close": 28.54, "Volume": 2292000, "Amount": 6541.24}
        return {key: pd.DataFrame({symbol: [value] * len(indices)}, index=indices) for key, value in values.items()}

    def test_minute_timestamps_and_amount_are_real_provider_values(self):
        result = bars_result(self.frames(), "sh600900", "1m")
        self.assertEqual(result["dataAsOf"], "2026-09-30 15:00:00")
        self.assertEqual(result["rows"][0]["amount"], 65412400)
        self.assertEqual(result["rows"][0]["volume"], 2292000)
        self.assertEqual(result["volumeUnit"], "股")
        self.assertEqual(bars_result(self.frames("000001.SH"), "sh000001", "1m")["volumeUnit"], "原始单位")
        self.assertEqual(bars_result(self.frames("000001.SH"), "sh000001", "1d")["volumeUnit"], "手")

    def test_duplicate_time_and_inconsistent_fields_are_rejected(self):
        duplicated = self.frames(indices=[datetime(2026, 9, 30), datetime(2026, 9, 30)])
        with self.assertRaises(ValueError):
            bars_result(duplicated, "sh600900", "1d")
        frames = self.frames()
        frames["Volume"].index = [datetime(2026, 9, 29)]
        with self.assertRaises(ValueError):
            bars_result(frames, "sh600900", "1m")

    def test_missing_or_invalid_bars_are_not_filled(self):
        frames = self.frames()
        frames["Close"].iloc[0, 0] = float("nan")
        result = bars_result(frames, "sh600900", "1m")
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["warnings"])
        self.assertIsNone(result["dataAsOf"])
        frames = self.frames()
        frames["High"].iloc[0, 0] = 1
        with self.assertRaises(ValueError):
            bars_result(frames, "sh600900", "1m")


FIXTURE_VENDOR = '''
from pathlib import Path
import os
import time
class tq:
    _initialized = False
    @classmethod
    def initialize(cls, value):
        print("fixture vendor output must not corrupt protocol")
        cls._initialized = True
        Path(__file__).with_name("worker-pid.txt").write_text(str(os.getpid()))
    @classmethod
    def close(cls):
        cls._initialized = False
    @classmethod
    def get_market_snapshot(cls, stock_code):
        if stock_code == "600001.SH":
            Path(__file__).with_name("blocked.txt").write_text("blocked")
            time.sleep(120)
        if stock_code == "600002.SH":
            return {}
        return dict(Now="10", LastClose="9", Open="9.5", Max="10.1", Min="9.4", Volume="10", Amount="1", ErrorId="0")
    @classmethod
    def get_market_data(cls, **kw):
        import pandas as pd
        return {field: pd.DataFrame({kw["stock_list"][0]: [value]}, index=["20261009150000"]) for field,value in dict(Open=10,High=11,Low=9,Close=10.5,Volume=100,Amount=1.05).items()}
    @classmethod
    def get_sector_list(cls, list_type):
        return [{"Code":"880001.SH","Name":"Fixture"}]
    @classmethod
    def get_stock_list_in_sector(cls, block_code, block_type):
        return ["600900.SH", "000001.SZ"]
'''


def process_alive(pid):
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@unittest.skipUnless(HAS_MCP, "requires optional market MCP runtime")
class MCPProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        folder = self.root / "PYPlugins" / "user"
        folder.mkdir(parents=True)
        (folder / "tqcenter.py").write_text(FIXTURE_VENDOR, encoding="utf-8")
        (self.root / "PYPlugins" / "TPythClient.dll").write_bytes(b"fixture")
        self.pid_file = folder / "worker-pid.txt"
        self.client = MCPClient(self.root, timeout=12, idle_timeout=90)

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def test_real_mcp_handshake_whitelist_reuse_and_safe_exit(self):
        self.assertTrue(self.client.call("tdx_status")["connected"])
        pid = int(self.pid_file.read_text())
        self.assertEqual(set(self.client.tools), TOOLS)
        result = self.client.call("tdx_quotes", {"symbols": ["sh600900", "sh600002"]})
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["errors"][0]["symbol"], "sh600002")
        bars = self.client.call("tdx_bars", {"symbol": "sh600900", "period": "1m"})
        self.assertEqual(bars["rows"][0]["date"], "2026-10-09 15:00:00")
        sectors = self.client.call("tdx_sectors")
        self.assertEqual(sectors["items"][0]["name"], "Fixture")
        stocks = self.client.call("tdx_sector_stocks", {"sector": "Fixture"})
        self.assertEqual(stocks["symbols"], ["sh600900", "sz000001"])
        self.assertEqual(int(self.pid_file.read_text()), pid)
        self.assertFalse((self.root / "PYPlugins" / "user" / "__pycache__").exists())
        self.client.close()
        self.assertFalse(process_alive(pid))

    def test_timeout_kills_native_worker_and_next_call_reconnects(self):
        self.client.call("tdx_status")
        pid = int(self.pid_file.read_text())
        started = time.monotonic()
        with self.assertRaises(Exception):
            self.client.call("tdx_quotes", {"symbols": ["sh600001"]})
        self.assertLess(time.monotonic() - started, 12)
        self.assertFalse(process_alive(pid))
        self.assertTrue(self.client.call("tdx_status")["connected"])
        self.assertNotEqual(int(self.pid_file.read_text()), pid)

    def test_idle_connection_exits_and_reconnects(self):
        self.client.idle_timeout = 0.2
        self.client.call("tdx_status")
        pid = int(self.pid_file.read_text())
        deadline = time.monotonic() + 5
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_alive(pid))
        self.assertTrue(self.client.call("tdx_status")["connected"])
        self.assertNotEqual(int(self.pid_file.read_text()), pid)

    def test_call_during_idle_sdk_cleanup_reconnects_without_sending_to_retired_worker(self):
        # Hold the first SDK context precisely between its idle decision and
        # final cleanup. A request here used to queue behind a departed reader.
        from mcp.client.stdio import stdio_client
        cleanup_started, release_cleanup, request_started = (threading.Event() for _ in range(3))
        calls = 0
        @asynccontextmanager
        async def delayed_stdio(*args, **kwargs):
            nonlocal calls
            calls += 1
            first = calls == 1
            async with stdio_client(*args, **kwargs) as streams:
                try:
                    yield streams
                finally:
                    if first:
                        cleanup_started.set()
                        while not release_cleanup.is_set():
                            await asyncio.sleep(0.01)
        self.client.idle_timeout = 0.05
        outcome = {}
        original_request = _Connection.request
        with patch("mcp.client.stdio.stdio_client", delayed_stdio):
            self.assertTrue(self.client.call("tdx_status")["connected"])
            old = self.client._connection
            old_pid = int(self.pid_file.read_text())
            self.assertTrue(cleanup_started.wait(5))
            self.assertFalse(old.closed.is_set())
            def observed_request(connection, *args):
                if connection is old:
                    request_started.set()
                return original_request(connection, *args)
            def request():
                try:
                    outcome["result"] = self.client.call("tdx_status")
                except Exception as exc:
                    outcome["error"] = exc
            with patch.object(_Connection, "request", observed_request):
                worker = threading.Thread(target=request, daemon=True)
                worker.start()
                try:
                    self.assertTrue(request_started.wait(3))
                finally:
                    release_cleanup.set()
                    worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertNotIn("error", outcome)
            self.assertTrue(outcome["result"]["connected"])
            self.assertNotEqual(int(self.pid_file.read_text()), old_pid)
            self.assertFalse(process_alive(old_pid))
            self.client.close()

    def test_close_unblocks_an_active_native_call(self):
        self.client.call("tdx_status")
        pid = int(self.pid_file.read_text())
        finished = threading.Event()
        failures = []
        def call():
            try:
                self.client.call("tdx_quotes", {"symbols": ["sh600001"]})
            except Exception as exc:
                failures.append(exc)
            finally:
                finished.set()
        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        blocked = self.pid_file.with_name("blocked.txt")
        deadline = time.monotonic() + 3
        while not blocked.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(blocked.exists())
        self.client.close()
        self.assertTrue(finished.wait(3))
        self.assertTrue(failures)
        self.assertFalse(process_alive(pid))


if __name__ == "__main__":
    unittest.main()
