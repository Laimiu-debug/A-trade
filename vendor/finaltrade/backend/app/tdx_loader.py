from __future__ import annotations

import csv
import hashlib
import json
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import TypedDict

from .models import CandlePoint, IntradayPoint, ScreenerResult, Stage, ThemeStage, TrendClass

DAY_RECORD = struct.Struct("<IIIIIfII")
LC1_RECORD = struct.Struct("<HHfffffII")
TNF_HEADER_SIZE = 50
TNF_RECORD_SIZE = 360
DBF_HEADER = struct.Struct("<BBBBIHH20x")
DBF_FIELD_SIZE = 32
DBF_TERMINATOR = 0x0D
_INPUT_POOL_RUNTIME_CACHE: dict[str, tuple[float, tuple[dict[str, object], ...], str | None]] = {}
_INPUT_POOL_RUNTIME_CACHE_LOCK = RLock()
_INPUT_POOL_RUNTIME_CACHE_MAX_KEYS = 16


class ParsedSeries(TypedDict):
    symbol: str
    total_bars: int
    dates: list[str]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    amount: list[float]
    volume: list[int]


class DbfField(TypedDict):
    offset: int
    length: int


def _safe_mean(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _resolve_tdx_base_dir(tdx_root: str) -> Path:
    base = Path(tdx_root)
    if base.name.lower() == "vipdoc":
        return base.parent
    return base


def _akshare_cache_root(akshare_cache_dir: str = "") -> Path:
    override = str(akshare_cache_dir).strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    override = os.getenv("AKSHARE_CACHE_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    return Path.home() / ".tdx-trend" / "akshare" / "daily"


def _normalize_symbol(stem: str, market: str) -> str | None:
    raw = stem.lower()
    if raw.startswith(("sh", "sz", "bj")) and len(raw) >= 8:
        symbol = raw[:8]
    elif len(raw) >= 6 and raw[:6].isdigit():
        symbol = f"{market}{raw[:6]}"
    else:
        return None

    code = symbol[2:]
    if len(code) != 6 or not code.isdigit():
        return None
    return symbol


def _tnf_file_for_market(market: str) -> str:
    if market == "sh":
        return "shs.tnf"
    if market == "sz":
        return "szs.tnf"
    if market == "bj":
        return "bjs.tnf"
    return ""


@lru_cache(maxsize=16)
def _cached_symbol_name_map_from_tnf(tdx_root: str, markets_key: tuple[str, ...]) -> dict[str, str]:
    return _load_symbol_name_map_from_tnf(tdx_root, list(markets_key))


def _load_symbol_name_map_from_tnf(tdx_root: str, markets: list[str]) -> dict[str, str]:
    base = _resolve_tdx_base_dir(tdx_root)
    root = base / "T0002" / "hq_cache"
    if not root.exists():
        return {}

    symbol_name_map: dict[str, str] = {}
    for market in markets:
        tnf_file = _tnf_file_for_market(market)
        if not tnf_file:
            continue
        path = root / tnf_file
        if not path.exists():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue

        if len(data) <= TNF_HEADER_SIZE + TNF_RECORD_SIZE:
            continue

        for offset in range(TNF_HEADER_SIZE, len(data) - TNF_RECORD_SIZE + 1, TNF_RECORD_SIZE):
            record = data[offset : offset + TNF_RECORD_SIZE]
            code = record[0:6].decode("ascii", "ignore").strip("\x00").strip()
            if len(code) != 6 or not code.isdigit():
                continue

            raw_name = record[31 : 31 + 16]
            name = raw_name.split(b"\x00", 1)[0].decode("gbk", "ignore").strip()
            if not name:
                continue

            symbol = f"{market}{code}"
            symbol_name_map[symbol] = name

    return symbol_name_map


def _decode_ascii_field(raw: bytes) -> str:
    return raw.decode("ascii", "ignore").strip("\x00").strip()


def _parse_dbf_numeric(raw: bytes) -> float | None:
    text = _decode_ascii_field(raw)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_dbf_fields(raw: bytes) -> dict[str, DbfField]:
    fields: dict[str, DbfField] = {}
    position = 1
    offset = DBF_HEADER.size

    while offset + DBF_FIELD_SIZE <= len(raw) and raw[offset] != DBF_TERMINATOR:
        descriptor = raw[offset : offset + DBF_FIELD_SIZE]
        name = descriptor[0:11].split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
        length = int(descriptor[16])
        if name:
            fields[name] = DbfField(offset=position, length=length)
        position += length
        offset += DBF_FIELD_SIZE
    return fields


def _market_from_dbf_sc(sc_value: str) -> str | None:
    if sc_value == "0":
        return "sz"
    if sc_value == "1":
        return "sh"
    if sc_value == "2":
        return "bj"
    return None


def _load_float_shares_from_base_dbf(
    tdx_root: str,
    markets: list[str],
) -> tuple[dict[str, float], str | None]:
    base = _resolve_tdx_base_dir(tdx_root)
    candidates = [base / "T0002" / "hq_cache" / "base.dbf", base / "base.dbf"]
    requested_markets = set(markets)
    last_error = "FLOAT_SHARES_DBF_NOT_FOUND"

    for file_path in candidates:
        if not file_path.exists():
            continue

        try:
            raw = file_path.read_bytes()
        except OSError:
            last_error = "FLOAT_SHARES_DBF_READ_FAILED"
            continue

        if len(raw) < DBF_HEADER.size:
            last_error = "FLOAT_SHARES_DBF_INVALID"
            continue

        try:
            _, _, _, _, record_count, header_len, record_len = DBF_HEADER.unpack(raw[: DBF_HEADER.size])
        except struct.error:
            last_error = "FLOAT_SHARES_DBF_INVALID"
            continue

        if record_count <= 0 or header_len <= DBF_HEADER.size or record_len <= 1:
            last_error = "FLOAT_SHARES_DBF_EMPTY"
            continue

        fields = _parse_dbf_fields(raw)
        sc_field = fields.get("SC")
        code_field = fields.get("GPDM")
        ltag_field = fields.get("LTAG")
        if not sc_field or not code_field or not ltag_field:
            last_error = "FLOAT_SHARES_FIELDS_MISSING"
            continue

        result: dict[str, float] = {}
        for i in range(record_count):
            start = header_len + i * record_len
            record = raw[start : start + record_len]
            if len(record) < record_len:
                break
            if record[0] == 0x2A:  # Deleted record marker.
                continue

            sc_value = _decode_ascii_field(
                record[sc_field["offset"] : sc_field["offset"] + sc_field["length"]]
            )
            market = _market_from_dbf_sc(sc_value)
            if market is None or market not in requested_markets:
                continue

            code = _decode_ascii_field(
                record[code_field["offset"] : code_field["offset"] + code_field["length"]]
            )
            if len(code) != 6 or not code.isdigit():
                continue

            float_shares_10k = _parse_dbf_numeric(
                record[ltag_field["offset"] : ltag_field["offset"] + ltag_field["length"]]
            )
            if float_shares_10k is None or float_shares_10k <= 0:
                continue

            symbol = f"{market}{code}"
            result[symbol] = float_shares_10k * 10000.0

        if result:
            return result, None

        last_error = "FLOAT_SHARES_DBF_EMPTY"

    return {}, last_error


@lru_cache(maxsize=16)
def _cached_float_shares_from_base_dbf(
    tdx_root: str,
    markets_key: tuple[str, ...],
) -> tuple[dict[str, float], str | None]:
    return _load_float_shares_from_base_dbf(tdx_root, list(markets_key))


def _is_a_share_symbol(symbol: str) -> bool:
    code = symbol[2:]
    market = symbol[:2]
    if market == "sh":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return code.startswith(("8", "4"))
    return False


def _parse_day_file(file_path: Path, symbol: str, *, max_bars: int = 360) -> ParsedSeries | None:
    try:
        size = file_path.stat().st_size
    except OSError:
        return None

    if size < DAY_RECORD.size * 60:
        return None

    total_bars = size // DAY_RECORD.size
    read_bars = min(total_bars, max(60, int(max_bars)))
    start_offset = (total_bars - read_bars) * DAY_RECORD.size

    dates: list[str] = []
    open_list: list[float] = []
    high_list: list[float] = []
    low_list: list[float] = []
    close_list: list[float] = []
    amount_list: list[float] = []
    volume_list: list[int] = []

    try:
        with file_path.open("rb") as fp:
            fp.seek(start_offset)
            raw = fp.read(read_bars * DAY_RECORD.size)
    except OSError:
        return None

    for offset in range(0, len(raw), DAY_RECORD.size):
        chunk = raw[offset : offset + DAY_RECORD.size]
        if len(chunk) < DAY_RECORD.size:
            continue
        day, open_raw, high_raw, low_raw, close_raw, amount_raw, volume_raw, _ = DAY_RECORD.unpack(chunk)
        if day <= 19900101 or close_raw <= 0 or high_raw <= 0 or low_raw <= 0:
            continue

        day_text = str(day)
        if len(day_text) != 8:
            continue
        date_text = f"{day_text[0:4]}-{day_text[4:6]}-{day_text[6:8]}"

        open_price = open_raw / 100.0
        high_price = high_raw / 100.0
        low_price = low_raw / 100.0
        close_price = close_raw / 100.0
        if high_price < low_price:
            high_price, low_price = low_price, high_price

        dates.append(date_text)
        open_list.append(round(open_price, 2))
        high_list.append(round(high_price, 2))
        low_list.append(round(low_price, 2))
        close_list.append(round(close_price, 2))
        amount_list.append(float(amount_raw))
        volume_list.append(int(volume_raw))

    if len(close_list) < 60:
        return None

    return ParsedSeries(
        symbol=symbol,
        total_bars=int(total_bars),
        dates=dates,
        open=open_list,
        high=high_list,
        low=low_list,
        close=close_list,
        amount=amount_list,
        volume=volume_list,
    )


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _load_candles_from_akshare_cache(
    symbol: str,
    window: int = 120,
    akshare_cache_dir: str = "",
) -> list[CandlePoint] | None:
    root = _akshare_cache_root(akshare_cache_dir)
    if len(symbol) < 8:
        return None
    symbol = symbol.lower()
    code = symbol[2:]
    candidates = [root / f"{symbol}.csv", root / f"{code}.csv"]
    target = next((path for path in candidates if path.exists()), None)
    if target is None:
        return None

    rows: list[CandlePoint] = []
    try:
        with target.open("r", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                date_text = str(row.get("date") or row.get("日期") or "").strip()
                if len(date_text) < 10:
                    continue
                date_text = date_text[:10]
                open_price = _to_float(row.get("open") or row.get("开盘"))
                high_price = _to_float(row.get("high") or row.get("最高"))
                low_price = _to_float(row.get("low") or row.get("最低"))
                close_price = _to_float(row.get("close") or row.get("收盘"))
                volume_value = _to_float(row.get("volume") or row.get("成交量"))
                amount_value = _to_float(row.get("amount") or row.get("成交额"))
                if (
                    open_price is None
                    or high_price is None
                    or low_price is None
                    or close_price is None
                    or open_price <= 0
                    or high_price <= 0
                    or low_price <= 0
                    or close_price <= 0
                ):
                    continue
                if high_price < low_price:
                    high_price, low_price = low_price, high_price
                volume = int(max(0.0, volume_value or 0.0))
                amount = float(amount_value) if amount_value is not None else float(close_price * max(volume, 1))
                rows.append(
                    CandlePoint(
                        time=date_text,
                        open=round(open_price, 2),
                        high=round(high_price, 2),
                        low=round(low_price, 2),
                        close=round(close_price, 2),
                        volume=volume,
                        amount=float(amount),
                        price_source="approx",
                    )
                )
    except OSError:
        return None

    if not rows:
        return None
    rows.sort(key=lambda item: item.time)
    start = max(0, len(rows) - max(window, 1))
    return rows[start:]


def _ma_at(prices: list[float], idx: int, period: int) -> float | None:
    if idx + 1 < period:
        return None
    window = prices[idx - period + 1 : idx + 1]
    return _safe_mean(window)


def _count_pullback_days(closes: list[float]) -> int:
    """从近20日最高收盘价到最后一天的交易日距离。"""
    if len(closes) < 2:
        return 0
    window = min(20, len(closes))
    recent = closes[-window:]
    peak_offset = max(range(len(recent)), key=lambda i: recent[i])
    # peak_offset 是 recent 内的索引，距离末尾的天数
    days = len(recent) - 1 - peak_offset
    return days


def _resolve_series_end_index(dates: list[str], as_of_date: str | None) -> int | None:
    if not dates:
        return None
    if not as_of_date:
        return len(dates) - 1
    for idx in range(len(dates) - 1, -1, -1):
        if dates[idx] <= as_of_date:
            return idx
    return None


def _resolve_day_parse_bars(return_window_days: int, as_of_date: str | None) -> int:
    if not as_of_date:
        return 360
    try:
        as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    except Exception:
        return 3000

    today = datetime.now()
    gap_days = max(0, (today.date() - as_of_dt.date()).days)
    approx_gap_bars = int(gap_days * 0.65)
    baseline = max(80, int(return_window_days) * 2 + 40)
    estimated = approx_gap_bars + baseline
    return max(320, min(3000, estimated))


def _build_row(
    series: ParsedSeries,
    return_window_days: int,
    float_shares: float | None,
    as_of_date: str | None = None,
) -> ScreenerResult | None:
    end_idx = _resolve_series_end_index(series["dates"], as_of_date)
    if end_idx is None:
        return None

    effective_end = end_idx + 1
    closes = series["close"][:effective_end]
    highs = series["high"][:effective_end]
    lows = series["low"][:effective_end]
    opens = series["open"][:effective_end]
    volumes = series["volume"][:effective_end]
    amounts = series["amount"][:effective_end]

    if len(closes) < max(return_window_days + 1, 40):
        return None
    history_bars = max(0, series["total_bars"] - len(series["dates"])) + effective_end
    if history_bars <= 250:
        return None

    window = min(return_window_days, len(closes) - 1)
    look20 = min(20, len(closes))
    start20 = len(closes) - look20

    latest = closes[-1]
    prev = closes[-2]
    start_close = closes[-window]
    ret_window = (latest / max(start_close, 0.01)) - 1

    high20 = highs[start20:]
    low20 = lows[start20:]
    close20 = closes[start20:]
    volume20 = volumes[start20:]
    amount20 = amounts[start20:]
    turnover20 = 0.0
    degraded = False
    degraded_reason: str | None = None
    if float_shares is not None and float_shares > 0:
        turnover20 = _safe_mean([max(0, volume) / float_shares for volume in volume20])
    else:
        degraded = True
        degraded_reason = "FLOAT_SHARES_NOT_FOUND"

    amplitude20 = _safe_mean([((h - l) / max(c, 0.01)) for h, l, c in zip(high20, low20, close20)])
    peak20 = max(high20) if high20 else latest
    retrace20 = (peak20 - latest) / max(peak20, 0.01)

    ma20_last = _safe_mean(closes[-20:])
    price_vs_ma20 = (latest - ma20_last) / max(ma20_last, 0.01)

    ma10_above_ma20_days = 0
    ma5_above_ma10_days = 0
    count_start = max(0, len(closes) - 20)
    for idx in range(count_start, len(closes)):
        ma10 = _ma_at(closes, idx, 10)
        ma20 = _ma_at(closes, idx, 20)
        ma5 = _ma_at(closes, idx, 5)
        if ma10 is not None and ma20 is not None and ma10 > ma20:
            ma10_above_ma20_days += 1
        if ma5 is not None and ma10 is not None and ma5 > ma10:
            ma5_above_ma10_days += 1

    vol_slope20 = 0.0
    if len(volume20) >= 2:
        vol_slope20 = (volume20[-1] - volume20[0]) / max(volume20[0], 1)

    up_volume: list[int] = []
    down_volume: list[int] = []
    for idx in range(max(1, len(closes) - 20), len(closes)):
        if closes[idx] >= closes[idx - 1]:
            up_volume.append(volumes[idx])
        else:
            down_volume.append(volumes[idx])

    mean_up = _safe_mean(up_volume)
    mean_down = _safe_mean(down_volume)
    up_down_volume_ratio = mean_up / max(mean_down, 1.0)
    pullback_volume_ratio = (mean_down / max(_safe_mean(volume20), 1.0)) if down_volume else 0.6

    limit_up_days = 0
    for idx in range(max(1, len(closes) - 20), len(closes)):
        pct = (closes[idx] - closes[idx - 1]) / max(closes[idx - 1], 0.01)
        if pct >= 0.095:
            limit_up_days += 1

    trend_class: TrendClass
    if limit_up_days >= 2:
        trend_class = "B"
    elif ret_window >= 0.5:
        trend_class = "A_B"
    elif ret_window > 0:
        trend_class = "A"
    else:
        trend_class = "Unknown"

    stage: Stage
    if ret_window < 0.30:
        stage = "Early"
    elif ret_window <= 0.80:
        stage = "Mid"
    else:
        stage = "Late"

    theme_stage: ThemeStage
    if ret_window < 0.30:
        theme_stage = "发酵中"
    elif ret_window < 0.80 and up_down_volume_ratio >= 1.0:
        theme_stage = "高潮"
    else:
        theme_stage = "退潮"

    avg_volume20 = _safe_mean(volume20)
    has_blowoff_top = False
    for idx in range(start20, len(closes)):
        if volumes[idx] > avg_volume20 * 2.5 and closes[idx] <= opens[idx]:
            has_blowoff_top = True
            break

    has_divergence_5d = False
    if len(closes) >= 10:
        price_rise = closes[-1] > closes[-6]
        avg_v5 = _safe_mean(volumes[-5:])
        avg_prev5 = _safe_mean(volumes[-10:-5])
        has_divergence_5d = price_rise and avg_v5 < avg_prev5 * 0.9

    has_upper_shadow_risk = False
    for idx in range(max(0, len(closes) - 5), len(closes)):
        bar_range = highs[idx] - lows[idx]
        if bar_range <= 0:
            continue
        body_high = max(opens[idx], closes[idx])
        upper_shadow = highs[idx] - body_high
        if upper_shadow / bar_range > 0.5 and closes[idx] <= opens[idx]:
            has_upper_shadow_risk = True
            break

    score_raw = (
        45
        + ret_window * 90
        + up_down_volume_ratio * 8
        - pullback_volume_ratio * 15
        + max(0.0, (0.08 - abs(price_vs_ma20)) * 200)
    )
    score = int(round(_clamp(score_raw, 0, 100)))
    ai_confidence = round(
        _clamp(
            0.50 + ret_window * 0.30 + (up_down_volume_ratio - 1.0) * 0.10 - max(0.0, pullback_volume_ratio - 0.8) * 0.2,
            0.35,
            0.95,
        ),
        2,
    )

    labels = ["真实数据", "高波动" if trend_class == "B" else "趋势延续"]

    return ScreenerResult(
        symbol=series["symbol"],
        name=series["symbol"].upper(),
        latest_price=round(latest, 2),
        day_change=round(latest - prev, 2),
        day_change_pct=round((latest - prev) / max(prev, 0.01), 4),
        score=score,
        ret40=round(ret_window, 4),
        turnover20=round(turnover20, 4),
        amount20=float(_safe_mean(amount20)),
        amplitude20=round(amplitude20, 4),
        retrace20=round(retrace20, 4),
        pullback_days=_count_pullback_days(closes),
        ma10_above_ma20_days=ma10_above_ma20_days,
        ma5_above_ma10_days=ma5_above_ma10_days,
        price_vs_ma20=round(price_vs_ma20, 4),
        vol_slope20=round(vol_slope20, 4),
        up_down_volume_ratio=round(up_down_volume_ratio, 4),
        pullback_volume_ratio=round(pullback_volume_ratio, 4),
        has_blowoff_top=has_blowoff_top,
        has_divergence_5d=has_divergence_5d,
        has_upper_shadow_risk=has_upper_shadow_risk,
        ai_confidence=ai_confidence,
        theme_stage=theme_stage,
        trend_class=trend_class,
        stage=stage,
        labels=labels,
        reject_reasons=[],
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


def load_candles_for_symbol(
    tdx_root: str,
    symbol: str,
    window: int = 120,
    market_data_source: str = "tdx_then_akshare",
    akshare_cache_dir: str = "",
) -> list[CandlePoint] | None:
    if market_data_source not in {"tdx_only", "tdx_then_akshare", "akshare_only"}:
        market_data_source = "tdx_then_akshare"
    use_tdx = market_data_source in {"tdx_only", "tdx_then_akshare"}
    use_akshare = market_data_source in {"akshare_only", "tdx_then_akshare"}
    if not use_tdx:
        return _load_candles_from_akshare_cache(symbol, window, akshare_cache_dir) if use_akshare else None

    root = Path(tdx_root)
    if not root.exists() or len(symbol) < 8:
        if use_akshare:
            return _load_candles_from_akshare_cache(symbol, window, akshare_cache_dir)
        return None

    market = symbol[:2]
    market_dir = root / market / "lday"
    if not market_dir.exists():
        if use_akshare:
            return _load_candles_from_akshare_cache(symbol, window, akshare_cache_dir)
        return None

    file_path = market_dir / f"{symbol}.day"
    if not file_path.exists() and symbol[2:].isdigit():
        file_path = market_dir / f"{symbol[2:]}.day"
    if not file_path.exists():
        if use_akshare:
            return _load_candles_from_akshare_cache(symbol, window, akshare_cache_dir)
        return None

    parsed = _parse_day_file(file_path, symbol, max_bars=max(60, int(window)))
    if not parsed:
        if use_akshare:
            return _load_candles_from_akshare_cache(symbol, window, akshare_cache_dir)
        return None

    start = max(0, len(parsed["close"]) - window)
    points: list[CandlePoint] = []
    for i in range(start, len(parsed["close"])):
        points.append(
            CandlePoint(
                time=parsed["dates"][i],
                open=parsed["open"][i],
                high=parsed["high"][i],
                low=parsed["low"][i],
                close=parsed["close"][i],
                volume=parsed["volume"][i],
                amount=parsed["amount"][i],
                price_source="vwap",
            )
        )
    if points:
        return points
    if use_akshare:
        return _load_candles_from_akshare_cache(symbol, window, akshare_cache_dir)
    return None


def _input_pool_runtime_cache_enabled() -> bool:
    raw = os.getenv("TDX_TREND_INPUT_POOL_RUNTIME_CACHE", "").strip().lower()
    if not raw:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _input_pool_runtime_cache_ttl_sec() -> float:
    raw = os.getenv("TDX_TREND_INPUT_POOL_RUNTIME_CACHE_TTL_SEC", "").strip()
    if not raw:
        return 1800.0
    try:
        return max(0.0, float(raw))
    except Exception:
        return 1800.0


def _normalize_input_pool_load_timeout_sec(raw_value: object) -> float | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed


def _input_pool_load_workers() -> int:
    raw = os.getenv("TDX_TREND_INPUT_POOL_LOAD_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            pass
    cpu_count = os.cpu_count() or 4
    return max(1, min(8, int(cpu_count)))


def _decode_day_int_to_text(raw_day: int) -> str | None:
    if raw_day <= 0:
        return None
    day_text = str(int(raw_day))
    if len(day_text) != 8:
        return None
    year = int(day_text[0:4])
    month = int(day_text[4:6])
    day = int(day_text[6:8])
    if not (1990 <= year <= 2100):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _probe_day_file_bounds(file_path: Path) -> tuple[int, str | None, str | None] | None:
    try:
        size = file_path.stat().st_size
    except OSError:
        return None
    if size < DAY_RECORD.size * 60:
        return None

    total_bars = int(size // DAY_RECORD.size)
    if total_bars < 60:
        return None

    try:
        with file_path.open("rb") as fp:
            first_chunk = fp.read(DAY_RECORD.size)
            fp.seek((total_bars - 1) * DAY_RECORD.size)
            last_chunk = fp.read(DAY_RECORD.size)
    except OSError:
        return None

    if len(first_chunk) < DAY_RECORD.size or len(last_chunk) < DAY_RECORD.size:
        return None

    first_day = _decode_day_int_to_text(int(DAY_RECORD.unpack(first_chunk)[0]))
    last_day = _decode_day_int_to_text(int(DAY_RECORD.unpack(last_chunk)[0]))
    return total_bars, first_day, last_day


def probe_tdx_latest_trade_date(tdx_root: str) -> str | None:
    """Return the latest trading day available in TDX daily files (YYYY-MM-DD)."""
    root = Path(tdx_root)
    if root.name.lower() == "vipdoc":
        data_root = root
    elif (root / "vipdoc").is_dir():
        data_root = root / "vipdoc"
    else:
        data_root = _resolve_tdx_base_dir(tdx_root)
    candidates = [
        data_root / "sh" / "lday" / "sh000001.day",
        data_root / "sz" / "lday" / "sz399001.day",
    ]
    latest: str | None = None
    for path in candidates:
        probed = _probe_day_file_bounds(path)
        if probed is None:
            continue
        _total_bars, _first_day, last_day = probed
        if not last_day:
            continue
        if latest is None or last_day > latest:
            latest = last_day
    return latest


def _input_pool_runtime_cache_key(
    *,
    tdx_root: str,
    markets: list[str],
    return_window_days: int,
    as_of_date: str | None,
    tdx_last_trade_date: str | None = None,
) -> str:
    payload = {
        "tdx_root": str(Path(tdx_root)),
        "markets": sorted({str(item).strip().lower() for item in markets if str(item).strip()}),
        "return_window_days": int(return_window_days),
        "as_of_date": str(as_of_date or "").strip() or "__latest__",
        "tdx_last_trade_date": str(tdx_last_trade_date or "").strip() or "__unknown__",
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _load_input_pool_runtime_cache(
    cache_key: str,
) -> tuple[list[ScreenerResult], str | None] | None:
    ttl_sec = _input_pool_runtime_cache_ttl_sec()
    with _INPUT_POOL_RUNTIME_CACHE_LOCK:
        cached = _INPUT_POOL_RUNTIME_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, payload_rows, load_error = cached
        if ttl_sec > 0 and max(0.0, time.time() - cached_at) > ttl_sec:
            _INPUT_POOL_RUNTIME_CACHE.pop(cache_key, None)
            return None
        # Invalidate if cached data is from a previous day
        if datetime.fromtimestamp(cached_at).strftime("%Y-%m-%d") < datetime.now().strftime("%Y-%m-%d"):
            _INPUT_POOL_RUNTIME_CACHE.pop(cache_key, None)
            return None
    rows: list[ScreenerResult] = []
    for item in payload_rows:
        try:
            rows.append(ScreenerResult(**item))
        except Exception:
            continue
    return rows, load_error


def _save_input_pool_runtime_cache(
    cache_key: str,
    rows: list[ScreenerResult],
    load_error: str | None,
) -> None:
    payload_rows = tuple(row.model_dump(exclude_none=True) for row in rows)
    with _INPUT_POOL_RUNTIME_CACHE_LOCK:
        _INPUT_POOL_RUNTIME_CACHE[cache_key] = (time.time(), payload_rows, load_error)
        overflow = len(_INPUT_POOL_RUNTIME_CACHE) - _INPUT_POOL_RUNTIME_CACHE_MAX_KEYS
        if overflow > 0:
            old_keys = sorted(
                _INPUT_POOL_RUNTIME_CACHE.items(),
                key=lambda item: item[1][0],
            )[:overflow]
            for old_key, _ in old_keys:
                _INPUT_POOL_RUNTIME_CACHE.pop(old_key, None)


def _parse_input_pool_day_file_to_row(
    *,
    file_path: Path,
    symbol: str,
    parse_bars: int,
    return_window_days: int,
    as_of_date: str | None,
    float_shares: float | None,
    mapped_name: str | None,
) -> tuple[ScreenerResult | None, bool]:
    if as_of_date and parse_bars < 3000:
        probed = _probe_day_file_bounds(file_path)
        if probed is None:
            return None, False
        _total_bars, first_day, _last_day = probed
        if first_day and first_day > as_of_date:
            return None, False

    parsed = _parse_day_file(file_path, symbol, max_bars=parse_bars)
    if as_of_date and parse_bars < 3000:
        need_retry_full = False
        if not parsed:
            need_retry_full = True
        elif parsed["dates"]:
            first_loaded = str(parsed["dates"][0]).strip()
            if first_loaded and first_loaded > as_of_date:
                need_retry_full = True
        if need_retry_full:
            parsed = _parse_day_file(file_path, symbol, max_bars=3000)
    if not parsed:
        return None, False

    row = _build_row(parsed, return_window_days, float_shares, as_of_date)
    if row is None:
        return None, False
    if mapped_name:
        row = row.model_copy(update={"name": mapped_name})
    return row, bool(row.degraded)


def load_input_pool_from_tdx(
    tdx_root: str,
    markets: list[str],
    return_window_days: int,
    as_of_date: str | None = None,
    load_timeout_sec: float | None = None,
) -> tuple[list[ScreenerResult], str | None]:
    normalized_markets = [str(market).strip().lower() for market in markets if str(market).strip()]
    tdx_last_trade_date = probe_tdx_latest_trade_date(tdx_root)
    cache_key = _input_pool_runtime_cache_key(
        tdx_root=tdx_root,
        markets=normalized_markets,
        return_window_days=return_window_days,
        as_of_date=as_of_date,
        tdx_last_trade_date=tdx_last_trade_date,
    )
    if _input_pool_runtime_cache_enabled():
        cached = _load_input_pool_runtime_cache(cache_key)
        if cached is not None:
            return cached

    root = Path(tdx_root)
    if not root.exists():
        return [], "TDX_PATH_NOT_FOUND"

    markets_key = tuple(sorted(set(normalized_markets)))
    symbol_name_map = _cached_symbol_name_map_from_tnf(tdx_root, markets_key)
    float_shares_map, float_shares_error = _cached_float_shares_from_base_dbf(tdx_root, markets_key)
    rows: list[ScreenerResult] = []
    missing_float_shares = 0
    parse_bars = _resolve_day_parse_bars(return_window_days, as_of_date)
    timeout_sec = _normalize_input_pool_load_timeout_sec(load_timeout_sec)
    deadline_ts = (time.perf_counter() + timeout_sec) if timeout_sec is not None else None
    timed_out = False
    load_workers = _input_pool_load_workers()
    for market in markets_key:
        if deadline_ts is not None and time.perf_counter() >= deadline_ts:
            timed_out = True
            break
        market_dir = root / market / "lday"
        if not market_dir.exists():
            continue

        candidates: list[tuple[str, Path]] = []
        for file_path in market_dir.glob("*.day"):
            symbol = _normalize_symbol(file_path.stem, market)
            if not symbol or not _is_a_share_symbol(symbol):
                continue
            try:
                if file_path.stat().st_size < DAY_RECORD.size * 60:
                    continue
            except OSError:
                continue
            candidates.append((symbol, file_path))
        if not candidates:
            continue

        def _parse_candidate(candidate: tuple[str, Path]) -> tuple[ScreenerResult | None, bool]:
            symbol_local, path_local = candidate
            return _parse_input_pool_day_file_to_row(
                file_path=path_local,
                symbol=symbol_local,
                parse_bars=parse_bars,
                return_window_days=return_window_days,
                as_of_date=as_of_date,
                float_shares=float_shares_map.get(symbol_local),
                mapped_name=symbol_name_map.get(symbol_local),
            )

        if load_workers <= 1 or len(candidates) <= 1:
            for candidate in candidates:
                if deadline_ts is not None and time.perf_counter() >= deadline_ts:
                    timed_out = True
                    break
                row, degraded = _parse_candidate(candidate)
                if row is None:
                    continue
                if degraded:
                    missing_float_shares += 1
                rows.append(row)
        else:
            worker_count = max(1, min(load_workers, len(candidates)))
            executor = ThreadPoolExecutor(max_workers=worker_count)
            future_map = {
                executor.submit(_parse_candidate, candidate): candidate[0]
                for candidate in candidates
            }
            try:
                timeout_left = None
                if deadline_ts is not None:
                    timeout_left = max(0.0, deadline_ts - time.perf_counter())
                for future in as_completed(future_map, timeout=timeout_left):
                    try:
                        row, degraded = future.result()
                    except Exception:
                        continue
                    if row is None:
                        continue
                    if degraded:
                        missing_float_shares += 1
                    rows.append(row)
            except FuturesTimeoutError:
                timed_out = True
            finally:
                executor.shutdown(wait=not timed_out, cancel_futures=timed_out)
        if timed_out:
            break

    if not rows:
        if timed_out:
            return [], "INPUT_POOL_LOAD_TIMEOUT"
        return [], "TDX_VALID_SERIES_NOT_FOUND"

    rows.sort(key=lambda item: item.ret40, reverse=True)

    result_error: str | None = None
    if not float_shares_map:
        result_error = float_shares_error or "FLOAT_SHARES_NOT_FOUND"
    elif missing_float_shares > 0:
        result_error = "PARTIAL_FLOAT_SHARES_MISSING"
    if timed_out:
        timeout_error = "INPUT_POOL_LOAD_TIMEOUT_PARTIAL"
        result_error = f"{result_error}|{timeout_error}" if result_error else timeout_error

    if _input_pool_runtime_cache_enabled():
        _save_input_pool_runtime_cache(cache_key, rows, result_error)
    return rows, result_error


def _decode_lc1_date(raw_date: int) -> str | None:
    date_part = raw_date & 0x07FF
    year = (raw_date >> 11) + 2004
    month = date_part // 100
    day = date_part % 100
    if year < 2004 or not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _decode_lc1_time(raw_time: int) -> str | None:
    hour = raw_time // 60
    minute = raw_time % 60
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def load_intraday_for_symbol_date(
    tdx_root: str,
    symbol: str,
    target_date: str,
) -> tuple[list[IntradayPoint] | None, str | None]:
    root = Path(tdx_root)
    if not root.exists() or len(symbol) < 8:
        return None, None

    market = symbol[:2]
    market_dir = root / market / "minline"
    if not market_dir.exists():
        return None, None

    file_path = market_dir / f"{symbol}.lc1"
    if not file_path.exists() and symbol[2:].isdigit():
        file_path = market_dir / f"{symbol[2:]}.lc1"
    if not file_path.exists():
        return None, None

    try:
        raw = file_path.read_bytes()
    except OSError:
        return None, None

    if len(raw) < LC1_RECORD.size:
        return None, None

    by_date: dict[str, list[tuple[str, float, float, int]]] = {}
    for offset in range(0, len(raw), LC1_RECORD.size):
        chunk = raw[offset : offset + LC1_RECORD.size]
        if len(chunk) < LC1_RECORD.size:
            continue
        (
            raw_date,
            raw_time,
            _open_price,
            _high_price,
            _low_price,
            close_price,
            amount,
            volume,
            _reserved,
        ) = LC1_RECORD.unpack(chunk)

        date_text = _decode_lc1_date(raw_date)
        time_text = _decode_lc1_time(raw_time)
        if date_text is None or time_text is None:
            continue

        if close_price <= 0:
            continue

        by_date.setdefault(date_text, []).append(
            (time_text, float(close_price), float(amount), max(0, int(volume)))
        )

    if not by_date:
        return None, None

    selected_date = target_date if target_date and target_date in by_date else max(by_date.keys())
    day_rows = sorted(by_date[selected_date], key=lambda item: item[0])
    if not day_rows:
        return None, None

    points: list[IntradayPoint] = []
    turnover = 0.0
    total_volume = 0
    last_avg = day_rows[0][1]
    for time_text, close_price, amount, volume in day_rows:
        effective_amount = amount if amount > 0 else close_price * max(volume, 1)
        turnover += effective_amount
        total_volume += max(volume, 1)
        avg_price = turnover / max(total_volume, 1)
        last_avg = avg_price
        points.append(
            IntradayPoint(
                time=time_text,
                price=round(close_price, 2),
                avg_price=round(avg_price, 2),
                volume=max(volume, 0),
                price_source="vwap",
            )
        )

    if points:
        points[0] = points[0].model_copy(update={"avg_price": round(last_avg if len(points) == 1 else points[0].price, 2)})

    return points, selected_date
