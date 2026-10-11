"""Read-only local TQ adapter; no vendor binaries or trading APIs are bundled.

Connection design adapted from TDX-local-MCP, with attribution in
licenses/TDX-local-MCP.txt. Only the explicitly listed market read calls below
are reachable. Native calls run inside the separately terminated MCP worker.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import importlib.util
from importlib.machinery import SourceFileLoader
import math
import os
from pathlib import Path
import re
import threading

from .tdx import security_kind, symbol_parts


FIELDS = ("Open", "High", "Low", "Close", "Volume", "Amount")
PERIODS = ("1d", "1w", "1mon", "1m")
UNKNOWN_QUOTE_TIME = "通达信快照未提供行情时间；接收时间不代表行情更新时间"


def received_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def provider_code(symbol):
    market, code = symbol_parts(symbol)
    return f"{code}.{market.upper()}"


def canonical_symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", value):
        raise ValueError("通达信返回了无效的证券标识")
    return value[7:].lower() + value[:6]


def number(value, field):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"通达信行情缺少有效的 {field}")
    try:
        parsed = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"通达信行情的 {field} 无效") from None
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"通达信行情的 {field} 无效")
    return parsed


def date_input(value):
    if not isinstance(value, str):
        raise ValueError("日期必须为 YYYY-MM-DD 字符串")
    if not value:
        return ""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期必须为 YYYY-MM-DD")
    date.fromisoformat(value)
    return value.replace("-", "")


def bar_date(value, period):
    if isinstance(value, (datetime, date)):
        parsed = value
    else:
        text = str(value)
        parsed = None
        for pattern in ("%Y%m%d%H%M%S", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                pass
        if parsed is None:
            raise ValueError("通达信返回了无法识别的 K 线时间")
    if period == "1m":
        if not isinstance(parsed, datetime):
            raise ValueError("分时数据缺少实际分钟时间")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    return parsed.strftime("%Y-%m-%d")


def quote_record(snapshot, symbol):
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("终端未返回此证券的快照")
    if str(snapshot.get("ErrorId", "0")) != "0":
        raise ValueError("终端返回了行情错误")
    market, code = symbol_parts(symbol)
    record = {"symbol": symbol.lower(), "code": code, "name": "", "market": market}
    for target, source in (("price", "Now"), ("previousClose", "LastClose"), ("open", "Open"), ("high", "Max"), ("low", "Min")):
        value = number(snapshot.get(source), source)
        record[target] = value or None
    record.update(volume=number(snapshot.get("Volume"), "Volume"),
                  amount=number(snapshot.get("Amount"), "Amount") * 10000,
                  volumeUnit="手", amountUnit="元", source="tdx-mcp",
                  asOf=None, receivedAt=received_now(), warnings=[UNKNOWN_QUOTE_TIME])
    if record["price"] is None:
        record["warnings"].append("终端未提供有效现价")
    return record


def bars_result(data, symbol, period):
    import pandas as pd

    code = provider_code(symbol)
    market, digits = symbol_parts(symbol)
    warnings = []
    rows = []
    if data is not None and data != {}:
        if not isinstance(data, dict):
            raise ValueError("通达信 K 线返回格式无效")
        frames = {}
        for field in FIELDS:
            frame = data.get(field)
            if not isinstance(frame, pd.DataFrame):
                raise ValueError(f"通达信 K 线缺少字段 {field}")
            if frame.index.has_duplicates or frame.columns.has_duplicates:
                raise ValueError("通达信 K 线含重复时间或证券列")
            if code not in frame.columns and not frame.empty:
                raise ValueError("通达信 K 线缺少请求证券")
            frames[field] = frame
        first = frames[FIELDS[0]]
        if len(first.index) > 2400:
            raise ValueError("通达信返回的 K 线超过数量上限")
        if any(not first.index.equals(frame.index) for frame in frames.values()):
            raise ValueError("通达信 K 线各字段时间不一致")
        skipped = 0
        for index in first.index:
            row = {"date": bar_date(index, period)}
            if any(pd.isna(frame.at[index, code]) for frame in frames.values()):
                skipped += 1
                continue
            for field, frame in frames.items():
                row[field.lower()] = number(frame.at[index, code], field)
            if any(row[field] == 0 for field in ("open", "high", "low", "close")):
                skipped += 1
                continue
            if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]):
                raise ValueError("通达信 K 线价格区间不一致")
            row["amount"] *= 10000
            rows.append(row)
        if skipped:
            warnings.append(f"跳过 {skipped} 条价格缺失或停牌记录，未补造数据")
    rows.sort(key=lambda row: row["date"])
    if len({row["date"] for row in rows}) != len(rows):
        raise ValueError("通达信 K 线含重复时间")
    kind = security_kind(market, digits)
    volume_unit = "股" if kind == "stock" else "手" if kind == "index" and period != "1m" else "原始单位"
    if volume_unit == "原始单位":
        warnings.append("此类证券的成交量保留终端原始单位")
    return {"symbol": symbol.lower(), "code": digits, "name": "", "market": market,
            "period": period, "adjustment": "none", "rows": rows,
            "volumeUnit": volume_unit, "amountUnit": "元", "source": "tdx-mcp",
            "dataAsOf": rows[-1]["date"] if rows else None,
            "receivedAt": received_now(), "warnings": warnings}


class _ReadOnlySourceLoader(SourceFileLoader):
    def set_data(self, path, data, *, _mode=0o666):
        # Import must not create a cache inside the user's vendor install.
        pass


class TQBridge:
    def __init__(self, root):
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("通达信目录必须为绝对路径")
        self.root = path.resolve()
        self.source = self.root / "PYPlugins" / "user" / "tqcenter.py"
        self.dll = self.root / "PYPlugins" / "TPythClient.dll"
        self.init_path = f"atrade_market_readonly_{os.getpid()}"
        self._module = None
        self._lock = threading.RLock()
        self._initialized_here = False

    def _load_module(self):
        if self._module is not None:
            return self._module
        if not self.source.is_file() or not self.dll.is_file():
            raise FileNotFoundError("所选目录缺少 TQ 接口；需要支持 TQ 策略的通达信客户端")
        if any(not p.resolve().is_relative_to(self.root) for p in (self.source, self.dll)):
            raise ValueError("TQ 接口文件超出所选通达信目录")
        loader = _ReadOnlySourceLoader("_atrade_tqcenter", str(self.source))
        spec = importlib.util.spec_from_file_location("_atrade_tqcenter", self.source, loader=loader)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载通达信 TQ 接口")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "tq"):
            raise RuntimeError("通达信 TQ 接口缺少 tq 类")
        self._module = module
        return module

    def _connect(self):
        tq = self._load_module().tq
        if not getattr(tq, "_initialized", False):
            try:
                tq.initialize(self.init_path)
                self._initialized_here = bool(getattr(tq, "_initialized", False))
            except Exception as exc:
                raise RuntimeError("通达信连接失败，请启动并登录支持 TQ 策略的客户端") from exc
        if not getattr(tq, "_initialized", False):
            raise RuntimeError("通达信终端未连接，请确认已启动并登录")
        return tq

    def status(self):
        with self._lock:
            try:
                self._connect()
                error = ""
            except Exception as exc:
                error = str(exc)
            tq = getattr(self._module, "tq", None)
            return {"connected": bool(getattr(tq, "_initialized", False)),
                    "path": str(self.root), "readOnly": True, "transport": "stdio",
                    "provider": "tdx-local-tq", "lastError": error,
                    "receivedAt": received_now()}

    def quotes(self, symbols):
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 24:
            raise ValueError("快照每次支持 1 至 24 个证券")
        codes = [(symbol.lower(), provider_code(symbol)) for symbol in symbols if isinstance(symbol, str)]
        if len(codes) != len(symbols):
            raise ValueError("证券标识必须是含市场的字符串")
        if len({symbol for symbol, _ in codes}) != len(symbols):
            raise ValueError("快照请求不能包含重复证券")
        items, errors = [], []
        with self._lock:
            tq = self._connect()
            for symbol, code in codes:
                try:
                    items.append(quote_record(tq.get_market_snapshot(stock_code=code), symbol))
                except Exception as exc:
                    errors.append({"symbol": symbol, "message": str(exc)})
        return {"items": items, "errors": errors, "receivedAt": received_now(),
                "warnings": [UNKNOWN_QUOTE_TIME]}

    def bars(self, symbol, period="1d", limit=240, start="", end=""):
        code = provider_code(symbol)
        if period not in PERIODS:
            raise ValueError("仅支持日、周、月和 1 分钟 K 线")
        if type(limit) is not int or not 1 <= limit <= 2400:
            raise ValueError("K 线数量必须为 1 至 2400 的整数")
        start_time, end_time = date_input(start), date_input(end)
        if start_time and end_time and start_time > end_time:
            raise ValueError("开始日期不能晚于结束日期")
        # A requested end day includes all its minutes, not just midnight.
        if period == "1m" and end_time:
            end_time += "235959"
        with self._lock:
            tq = self._connect()
            data = tq.get_market_data(stock_list=[code], period=period, count=limit,
                                      start_time=start_time, end_time=end_time,
                                      dividend_type="none", field_list=list(FIELDS), fill_data=False)
            result = bars_result(data, symbol, period)
            result["rows"] = [row for row in result["rows"] if (not start or row["date"][:10] >= start) and (not end or row["date"][:10] <= end)][-limit:]
            result["dataAsOf"] = result["rows"][-1]["date"] if result["rows"] else None
            return result

    def sectors(self):
        with self._lock:
            data = self._connect().get_sector_list(list_type=1)
            if not isinstance(data, list) or len(data) > 5000:
                raise ValueError("通达信板块目录返回格式无效")
            items = []
            for row in data:
                if not isinstance(row, dict) or not isinstance(row.get("Name"), str):
                    raise ValueError("通达信板块目录缺少名称")
                symbol = canonical_symbol(row.get("Code"))
                items.append({"symbol": symbol, "code": symbol[2:], "name": row["Name"]})
            return {"items": items, "receivedAt": received_now(), "source": "tdx-mcp",
                    "warnings": ["板块列表来自终端，尚未提供行业与概念分类标记"]}

    def sector_stocks(self, sector):
        if not isinstance(sector, str) or not sector or len(sector) > 80 or any(ord(c) < 32 for c in sector):
            raise ValueError("请选择有效的通达信板块名称")
        with self._lock:
            # Validate names against the terminal catalogue rather than
            # interpolating UI strings into vendor-specific expressions.
            if sector not in {item["name"] for item in self.sectors()["items"]}:
                raise ValueError("所选板块不在通达信板块目录中")
            data = self._connect().get_stock_list_in_sector(block_code=sector, block_type=0)
            if not isinstance(data, list) or len(data) > 20000:
                raise ValueError("通达信板块成分返回格式无效")
            symbols = list(dict.fromkeys(canonical_symbol(value) for value in data))
            return {"symbols": symbols, "sector": sector, "receivedAt": received_now(),
                    "source": "tdx-mcp", "warnings": []}

    def close(self):
        with self._lock:
            tq = getattr(self._module, "tq", None)
            disconnect = getattr(tq, "close", None)
            if self._initialized_here and callable(disconnect):
                try:
                    disconnect()
                except Exception:
                    pass
            self._initialized_here = False
