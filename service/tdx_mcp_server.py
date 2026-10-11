"""Internal stdio MCP server exposing only bounded, read-only market tools."""
from __future__ import annotations

import contextlib
import json
import sys

from .tq_bridge import TQBridge


class _ProtocolStdout:
    """MCP writes bytes; incidental vendor prints must go to stderr."""
    def __init__(self, raw):
        self._raw = raw
        self.buffer = raw.buffer

    def write(self, value):
        return sys.stderr.write(value)

    def flush(self):
        sys.stderr.flush()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def create_server(bridge):
    import anyio
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    server = FastMCP("atrade-tdx-readonly", instructions="A-trade 本地通达信只读行情接口。")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    async def invoke(method, *args):
        def run():
            with contextlib.redirect_stdout(sys.stderr):
                return method(*args)
        try:
            data = await anyio.to_thread.run_sync(run)
            return json.dumps({"success": True, "data": data}, ensure_ascii=False, allow_nan=False)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    @server.tool(annotations=annotations)
    async def tdx_status() -> str:
        """检查本地 TQ 连接；不启动交易、预警、通知或订阅。"""
        return await invoke(bridge.status)

    @server.tool(annotations=annotations)
    async def tdx_quotes(symbols: list[str]) -> str:
        """查询至多 24 个证券快照。证券含市场，如 sh600900。"""
        return await invoke(bridge.quotes, symbols)

    @server.tool(annotations=annotations)
    async def tdx_bars(symbol: str, period: str = "1d", limit: int = 240,
                       start: str = "", end: str = "") -> str:
        """证券日/周/月/一分钟 K 线；不复权、不补停牌，金额统一为元。"""
        return await invoke(bridge.bars, symbol, period, limit, start, end)

    @server.tool(annotations=annotations)
    async def tdx_sectors() -> str:
        """读取终端提供的板块代码和名称，不修改或创建板块。"""
        return await invoke(bridge.sectors)

    @server.tool(annotations=annotations)
    async def tdx_sector_stocks(sector: str) -> str:
        """按已存在的板块名称读取成分证券，不修改板块。"""
        return await invoke(bridge.sector_stocks, sector)

    return server


def main():
    if len(sys.argv) != 2:
        return 2
    bridge = TQBridge(sys.argv[1])
    original_stdout = sys.stdout
    sys.stdout = _ProtocolStdout(original_stdout)
    try:
        # Windows NumPy initialization must finish before MCP's stdin reader
        # starts; importing it later from a tool thread can stall its DLL load.
        import pandas  # noqa: F401
        create_server(bridge).run(transport="stdio")
        return 0
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            bridge.close()
        sys.stdout = original_stdout


if __name__ == "__main__":
    raise SystemExit(main())
