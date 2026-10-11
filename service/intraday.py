"""Read a requested trading day's real TDX one-minute bars without changing it.

Record layout follows pytdx's reader/lc_min_bar_reader.py. Stock amount is yuan
and volume is shares. Index minute volume does not share daily-file units, so
it is deliberately retained as source-native values rather than relabelled.
"""
from __future__ import annotations

import datetime as dt
import math
import os
from pathlib import Path
import re
import struct

from service.tdx import security_kind, symbol_parts, within


LC1_RECORD = struct.Struct("<HHfffffII")
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_POINTS_PER_DAY = 500


class MissingIntraday(ValueError):
    """The requested date is absent; available dates are choices, not fallback."""

    def __init__(self, message: str, available_dates: list[str]):
        super().__init__(message)
        self.available_dates = list(available_dates)


def _signature(stat):
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
            stat.st_ino, stat.st_dev)


def _file_identity(stat):
    # Windows Python may expose change-time through fstat but creation-time
    # through stat. Compare ctime only between two calls of the same kind.
    return stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev


def _read_stable(path: Path, root: Path) -> bytes:
    try:
        path = within(path, root)
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_SOURCE_BYTES:
                raise ValueError("分时文件过大，暂不支持读取")
            data = stream.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
            current = within(path, root).stat()
        if (_signature(before) != _signature(after)
                or _file_identity(after) != _file_identity(current)
                or len(data) != after.st_size):
            raise ValueError("通达信正在更新此分时文件，请更新完成后重试")
        return data
    except OSError:
        raise ValueError("分时文件无法读取，请检查目录和读取权限") from None


def _decode(record):
    packed_date, minute, opening, high, low, close, amount, volume, _ = record
    remainder = packed_date & 0x07FF
    try:
        day = dt.date((packed_date >> 11) + 2004, remainder // 100, remainder % 100)
    except ValueError:
        raise ValueError("分时文件含有无效交易日期") from None
    if minute >= 24 * 60:
        raise ValueError("分时文件含有无效时间")
    if not all(math.isfinite(value) for value in (opening, high, low, close, amount)):
        raise ValueError("分时文件含有无效数值")
    if not (0 < low <= opening <= high and low <= close <= high) or amount < 0:
        raise ValueError("分时文件含有无效开高低收价格或成交额")
    return day.isoformat(), {
        "time": f"{minute // 60:02d}:{minute % 60:02d}",
        "open": round(opening, 4), "high": round(high, 4),
        "low": round(low, 4), "close": round(close, 4),
        "volume": volume, "amount": amount,
    }


def read_intraday(root: Path, symbol: str, date: str) -> dict:
    """Return only the requested day; missing dates never silently change it."""
    market, code = symbol_parts(symbol)
    symbol = market + code
    kind = security_kind(market, code)
    if kind not in ("stock", "index"):
        raise ValueError("分时目前支持 A 股股票和指数")
    if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
        raise ValueError("分时日期须为 YYYY-MM-DD")
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise ValueError("分时日期无效") from None
    if root is None:
        raise ValueError("请先在设置中选择通达信目录")
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("行情目录必须是完整路径")
    root = root.resolve()
    if root.name.lower() == "vipdoc":
        root = root.parent
    folder = within(root / "vipdoc" / market / "minline", root)
    candidates = [within(folder / name, root)
                  for name in (f"{symbol}.lc1", f"{code}.lc1")]
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise ValueError(f"通达信目录中没有 {symbol} 的一分钟分时文件，请先下载分钟数据")
    contents = _read_stable(path, root)
    if not contents or len(contents) % LC1_RECORD.size:
        raise ValueError("分时文件为空或不完整（记录应为 32 字节），请完成下载后重试")

    rows, available_dates = [], []
    previous = None
    day_count = 0
    for record in LC1_RECORD.iter_unpack(contents):
        day, row = _decode(record)
        identity = (day, row["time"])
        if previous is not None and identity <= previous:
            raise ValueError("分时文件存在重复时间或记录未按时间递增")
        if not available_dates or available_dates[-1] != day:
            available_dates.append(day)
            day_count = 0
        day_count += 1
        if day_count > MAX_POINTS_PER_DAY:
            raise ValueError("分时文件同一天记录超过数量上限")
        if day == date:
            rows.append(row)
        previous = identity
    if not rows:
        raise MissingIntraday(f"没有 {date} 的真实分时记录", available_dates)
    warnings = []
    if kind == "index":
        warnings.append("指数分钟成交量保留通达信原始单位，与日线成交量口径不同，不据此计算均价")
    return {
        "symbol": symbol, "code": code, "date": date, "rows": rows,
        "provider": "tdx_lc1", "asOf": f"{date}T{rows[-1]['time']}:00+08:00",
        "receivedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "volumeUnit": "股" if kind == "stock" else "原始单位",
        "amountUnit": "元", "warnings": warnings, "cached": False,
        "availableDates": available_dates,
    }
