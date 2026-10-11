"""Read-only Tongdaxin local daily files; prices are unadjusted source values.

No paths in this module are defaults: the caller must supply a chosen directory.
Stock/index coefficients and the 32-byte record are cross-checked against
https://github.com/rainx/pytdx/blob/master/pytdx/reader/daily_bar_reader.py .
We retain stock volume as shares and index volume as lots, without conversion.
Fund/bond coefficients differ and are deliberately excluded by this first adapter.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
import re
import struct

DAY = struct.Struct("<IIIIIfII")
MARKETS = ("sh", "sz", "bj")
SYMBOL = re.compile(r"(sh|sz|bj)([0-9]{6})", re.ASCII)
MAX_DAY_BYTES = 16 * 1024 * 1024


def within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("行情文件超出所选目录，请检查目录链接")
    return resolved


def normalize_directory(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 32767:
        raise ValueError("请选择通达信安装目录或 vipdoc 目录")
    chosen = Path(value.strip())
    if not chosen.is_absolute():
        raise ValueError("行情目录必须是完整路径")
    try:
        chosen = chosen.resolve(strict=True)
        root = chosen.parent if chosen.name.lower() == "vipdoc" else chosen
        vipdoc = within(root / "vipdoc", root)
        if not root.is_dir() or not vipdoc.is_dir():
            raise ValueError("所选目录中没有 vipdoc 日线数据目录")
        found = False
        for market in MARKETS:
            folder = within(vipdoc / market / "lday", vipdoc)
            if folder.is_dir():
                found = True
        if not found:
            raise ValueError("所选目录中没有 sh、sz 或 bj 的 lday 日线文件夹")
        return root
    except OSError:
        raise ValueError("行情目录无法访问，请检查路径和读取权限") from None


def symbol_parts(value):
    if not isinstance(value, str) or not (match := SYMBOL.fullmatch(value.lower())):
        raise ValueError("证券标识须包含市场和六位代码，例如 sh600900")
    return match[1], match[2]


def security_kind(market, code):
    if (market == "sh" and code.startswith("000")) or (market == "sz" and code.startswith("399")) or (market == "bj" and code.startswith("899")):
        return "index"
    if (market == "sh" and code.startswith(("60", "68"))) or (market == "sz" and code.startswith(("00", "30"))) or (market == "bj" and code.startswith(("43", "83", "87", "88", "92"))):
        return "stock"
    return "other"


def daily_path(root, symbol):
    market, _ = symbol_parts(symbol)
    vipdoc = within(Path(root) / "vipdoc", Path(root))
    return within(vipdoc / market / "lday" / f"{symbol.lower()}.day", vipdoc)


def _signature(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


def read_stable(path):
    """Read one bounded file; never accept an in-progress rewrite as a snapshot."""
    try:
        with path.open("rb") as handle:
            before = path.stat()
            if before.st_size > MAX_DAY_BYTES:
                raise ValueError("日线文件过大，暂不支持读取")
            data = handle.read(MAX_DAY_BYTES + 1)
            after = path.stat()
        if _signature(before) != _signature(after) or len(data) != after.st_size:
            raise ValueError("通达信正在更新此日线文件，请更新完成后重试")
        return data
    except OSError:
        raise ValueError("日线文件无法读取，请检查通达信目录是否仍可访问") from None


def decode_record(record):
    date, opening, high, low, close, amount, volume, _reserved = DAY.unpack(record)
    try:
        trading_date = dt.date(date // 10000, date // 100 % 100, date % 100)
        if not 1990 <= trading_date.year <= 2200:
            raise ValueError()
    except ValueError:
        raise ValueError("日线文件含有无效日期") from None
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("日线文件含有无效成交额")
    if not (0 < low <= opening <= high and low <= close <= high):
        raise ValueError("日线文件含有无效开高低收价格")
    return {"date": trading_date.isoformat(), "open": opening / 100, "high": high / 100,
            "low": low / 100, "close": close / 100, "volume": volume, "amount": amount}


def validate_size(size):
    if not size:
        raise ValueError("日线文件为空，请先在通达信下载日线数据")
    if size % DAY.size:
        raise ValueError("日线文件不完整（记录应为 32 字节），请完成下载后重试")
    if size > MAX_DAY_BYTES:
        raise ValueError("日线文件过大，暂不支持读取")


def read_range(path):
    """Fast catalogue dates. Full record validation occurs when bars are opened."""
    try:
        with path.open("rb") as handle:
            before = path.stat()
            validate_size(before.st_size)
            first = handle.read(DAY.size)
            handle.seek(-DAY.size, 2)
            last = handle.read(DAY.size)
            after = path.stat()
        if _signature(before) != _signature(after):
            raise ValueError("通达信正在更新此日线文件，请更新完成后重试")
        if len(first) != DAY.size or len(last) != DAY.size:
            raise ValueError("日线文件读取不完整，请重试")
        earliest, latest = decode_record(first)["date"], decode_record(last)["date"]
        if earliest > latest or (before.st_size > DAY.size and earliest == latest):
            raise ValueError("日线文件日期未按时间递增")
        return earliest, latest
    except OSError:
        raise ValueError("日线文件无法读取") from None


def parse_daily(data):
    validate_size(len(data))
    result = []
    previous = ""
    for offset in range(0, len(data), DAY.size):
        row = decode_record(data[offset:offset + DAY.size])
        if row["date"] <= previous:
            raise ValueError("日线文件存在重复日期或日期未按时间递增")
        result.append(row)
        previous = row["date"]
    return result


def read_name_catalogue(root):
    """Known legacy/current TNF layouts only; malformed metadata is optional."""
    names, warnings = {}, []
    for market in MARKETS:
        try:
            path = within(Path(root) / "T0002" / "hq_cache" / f"{market}s.tnf", Path(root))
            if not path.is_file():
                continue
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("证券名称文件过大")
            with path.open("rb") as handle:
                before = path.stat()
                data = handle.read(64 * 1024 * 1024 + 1)
                after = path.stat()
            if _signature(before) != _signature(after) or len(data) != after.st_size:
                raise ValueError("证券名称文件正在更新")
            payload_size = len(data) - 50
            if payload_size <= 0:
                raise ValueError("证券名称文件为空")
            parsed = None
            # Validate every identity instead of guessing layout from file size alone.
            for width, name_offset, name_width, initials_offset in ((360, 31, 48, 329), (314, 23, 8, 285)):
                if payload_size % width:
                    continue
                candidate = {}
                for offset in range(50, len(data), width):
                    row = data[offset:offset + width]
                    raw_code = row[:6]
                    if not re.fullmatch(rb"[0-9]{6}", raw_code):
                        break
                    try:
                        name = row[name_offset:name_offset + name_width].split(b"\0", 1)[0].decode("gbk").strip()
                    except UnicodeError:
                        continue
                    if name and not any(ord(char) < 32 for char in name):
                        raw_initials = row[initials_offset:initials_offset + 16].split(b"\0", 1)[0].strip()
                        initials = raw_initials.decode("ascii").upper() if re.fullmatch(rb"[A-Za-z0-9*]{1,16}", raw_initials) else ""
                        candidate[market + raw_code.decode("ascii")] = {"name": name, "initials": initials}
                else:
                    parsed = candidate
                    break
            if parsed is None:
                raise ValueError("证券名称文件格式暂不支持")
            names.update(parsed)
        except (ValueError, OSError) as error:
            reason = str(error) if isinstance(error, ValueError) else "无法读取证券名称文件"
            warnings.append(f"{market.upper()} 名称未加载：{reason}，仍可按代码查看日线")
    return names, warnings


def read_names(root):
    catalogue, warnings = read_name_catalogue(root)
    return {symbol: item["name"] for symbol, item in catalogue.items()}, warnings
