"""Persistent MCP SDK session in an isolated, bounded native-worker process."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import threading
import time


TOOLS = frozenset({"tdx_status", "tdx_quotes", "tdx_bars", "tdx_sectors", "tdx_sector_stocks"})
TIMEOUT_MESSAGE = "通达信 MCP 连接超时，已关闭本次连接；请确认通达信已启动并登录后重试"


class _IdleExpired(RuntimeError):
    """The connection retired before this request was sent to a tool."""


def decode_result(result):
    if getattr(result, "isError", False):
        raise RuntimeError("通达信 MCP 工具调用失败")
    texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
    if len(texts) != 1 or len(texts[0]) > 16 * 1024 * 1024:
        raise RuntimeError("通达信 MCP 返回格式无效")
    try:
        envelope = json.loads(texts[0])
    except (ValueError, TypeError):
        raise RuntimeError("通达信 MCP 返回了无效数据") from None
    if not isinstance(envelope, dict) or type(envelope.get("success")) is not bool:
        raise RuntimeError("通达信 MCP 返回格式无效")
    if not envelope["success"]:
        error = envelope.get("error")
        raise RuntimeError(error if isinstance(error, str) and error else "通达信行情读取失败")
    if not isinstance(envelope.get("data"), dict):
        raise RuntimeError("通达信 MCP 数据格式无效")
    return envelope["data"]


class _Connection:
    def __init__(self, root, timeout, idle_timeout):
        self.root, self.timeout, self.idle_timeout = root, timeout, idle_timeout
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.idle_expired = threading.Event()
        self.loop = self.queue = self.task = None
        self.error = None
        self.tools = []
        self.thread = threading.Thread(target=self._run, daemon=True, name="atrade-mcp-connection")
        self.thread.start()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self.error = self.error or exc
        finally:
            self.closed.set()
            self.ready.set()

    async def _serve(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("行情运行环境缺少 MCP 依赖，请运行项目的 Python 环境安装步骤") from exc
        self.loop, self.queue = asyncio.get_running_loop(), asyncio.Queue()
        self.task = asyncio.current_task()
        root = Path(__file__).resolve().parent.parent
        # The child gets the SDK's limited platform environment, not arbitrary
        # API keys or executable options from the parent process environment.
        params = StdioServerParameters(command=sys.executable,
                                      args=["-u", "-m", "service.tdx_mcp_server", str(self.root)],
                                      env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
                                      cwd=str(root), encoding="utf-8")
        current = None
        failure = None
        operation_timeout = max(1, self.timeout - 8)
        try:
            with open(os.devnull, "w", encoding="utf-8") as log:
                async with stdio_client(params, errlog=log) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=operation_timeout)) as session:
                        async with asyncio.timeout(operation_timeout):
                            await session.initialize()
                            listed = await session.list_tools()
                        if {tool.name for tool in listed.tools} != TOOLS:
                            raise RuntimeError("MCP 工具列表不符合只读行情约定")
                        self.tools = sorted(tool.name for tool in listed.tools)
                        self.ready.set()
                        while True:
                            item = await self._next_request()
                            if item is None:
                                break
                            tool, arguments, current = item
                            try:
                                async with asyncio.timeout(operation_timeout):
                                    result = await session.call_tool(tool, arguments)
                            except Exception as exc:
                                failure = exc
                                break
                            if not current.done():
                                current.set_result(result)
                            current = None
        except BaseException as exc:
            failure = failure or exc
        finally:
            # stdio_client first closes stdin then terminates the process tree
            # after its grace interval, so native TQ calls cannot survive a
            # failed session or leave queued callers waiting indefinitely.
            self.error = failure
            self.closed.set()
            self.ready.set()
            reason = failure if isinstance(failure, Exception) else RuntimeError("MCP 连接已关闭")
            if current is not None and not current.done():
                current.set_exception(reason or RuntimeError("MCP 连接已关闭"))
            while not self.queue.empty():
                item = self.queue.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(reason or RuntimeError("MCP 连接已关闭"))

    async def _next_request(self):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=self.idle_timeout)
        except TimeoutError:
            # A put may win the same event-loop turn in which wait_for cancels
            # its getter. Consume that admitted request instead of retiring.
            try:
                return self.queue.get_nowait()
            except asyncio.QueueEmpty:
                # Retirement precedes SDK/process cleanup. closed only means
                # cleanup finished and therefore cannot be an admission gate.
                self.idle_expired.set()
                return None

    def _enqueue(self, item):
        # Both this callback and the idle decision run on the event loop, so a
        # request is either consumed or rejected before dispatch, never orphaned.
        if self.idle_expired.is_set():
            item[2].set_exception(_IdleExpired("MCP 空闲连接已退出"))
        elif self.closed.is_set():
            item[2].set_exception(self.error if isinstance(self.error, Exception) else RuntimeError("MCP 连接已关闭"))
        else:
            self.queue.put_nowait(item)

    def request(self, tool, arguments):
        deadline = time.monotonic() + self.timeout
        if not self.ready.wait(self.timeout):
            self.close(force=True)
            raise TimeoutError(TIMEOUT_MESSAGE)
        if self.idle_expired.is_set():
            raise _IdleExpired("MCP 空闲连接已退出")
        if self.closed.is_set():
            # Retirement may complete between the two cross-thread state reads.
            # This request was never sent, so preserve the bounded idle retry
            # even if SDK stream cleanup recorded an incidental exception.
            if self.idle_expired.is_set():
                raise _IdleExpired("MCP 空闲连接已退出")
            raise self.error if isinstance(self.error, Exception) else RuntimeError("MCP 连接已关闭")
        future = Future()
        try:
            self.loop.call_soon_threadsafe(self._enqueue, (tool, arguments, future))
        except RuntimeError:
            if self.idle_expired.is_set():
                raise _IdleExpired("MCP 空闲连接已退出") from None
            raise
        try:
            return future.result(timeout=max(0.01, deadline - time.monotonic()))
        except FutureTimeout:
            self.close(force=True)
            raise TimeoutError(TIMEOUT_MESSAGE) from None

    def close(self, force=False):
        if self.loop and self.queue and not self.closed.is_set():
            try:
                if force and self.task:
                    self.loop.call_soon_threadsafe(self.task.cancel)
                else:
                    self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
            except RuntimeError:
                pass
        self.thread.join(timeout=3)
        if self.thread.is_alive() and self.loop and self.task:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
            self.thread.join(timeout=4)


class MCPClient:
    """Only tools from our bundled worker can be requested, with lazy startup."""
    def __init__(self, root, timeout=35, idle_timeout=90):
        self.root = Path(root).resolve()
        if not Path(root).is_absolute():
            raise ValueError("通达信目录必须为绝对路径")
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self._connection = None
        self.tools = []
        self._state_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._generation = 0

    def call(self, tool, arguments=None):
        if tool not in TOOLS:
            raise ValueError("不支持的 MCP 工具；仅允许只读行情查询")
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("MCP 参数必须为对象")
        with self._request_lock:
            with self._state_lock:
                generation = self._generation
            for attempt in range(2):
                with self._state_lock:
                    if self._generation != generation:
                        raise RuntimeError("MCP 连接已关闭")
                    if self._connection is None or self._connection.closed.is_set():
                        self._connection = _Connection(self.root, self.timeout, self.idle_timeout)
                    connection = self._connection
                try:
                    result = decode_result(connection.request(tool, arguments or {}))
                    self.tools = list(connection.tools)
                    return result
                except Exception as exc:
                    with self._state_lock:
                        retry_idle = (isinstance(exc, _IdleExpired) and attempt == 0
                                      and self._connection is connection and self._generation == generation)
                        if self._connection is connection:
                            self._connection = None
                    connection.close(force=True)
                    if retry_idle:
                        continue
                    if isinstance(exc, TimeoutError) or "Timed out" in str(exc):
                        raise TimeoutError(TIMEOUT_MESSAGE) from None
                    raise

    def close(self):
        with self._state_lock:
            self._generation += 1
            connection = self._connection
            self._connection = None
        if connection is not None:
            connection.close()
