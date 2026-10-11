"""A bounded, display-only Eastmoney minute source for gaps in local TDX data.

The upstream returns a rolling window of at most five trading days. Dates and
market identity are checked, and missing historical OHLC values remain missing.
Nothing here downloads to or updates the user's Tongdaxin installation.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from service.intraday import MissingIntraday
from service.tdx import security_kind, symbol_parts


URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
MAX_BYTES = 512 * 1024
MAX_ROWS = 1600
SHANGHAI = dt.timezone(dt.timedelta(hours=8))
INDEX_SYMBOLS = frozenset(("sh000001", "sh000016", "sh000300", "sh000688",
                           "sh000905", "sh000852", "sz399001", "sz399005",
                           "sz399006", "sz399007"))
_SLOTS = threading.BoundedSemaphore(2)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("在线分时来源重定向，未读取其他地址")


urlopen = build_opener(_NoRedirect()).open


def request_config(symbol: str, date: str | None, observed_at: dt.datetime | None = None) -> dict:
    market, code = symbol_parts(symbol)
    symbol = market + code
    kind = security_kind(market, code)
    if market not in ("sh", "sz") or not (
            kind == "stock" or (kind == "index" and symbol in INDEX_SYMBOLS)):
        raise ValueError("在线分时目前支持沪深 A 股及已验证的主要指数")
    day = None
    if date is not None:
        if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
            raise ValueError("分时日期须为 YYYY-MM-DD")
        try:
            day = dt.date.fromisoformat(date)
        except ValueError:
            raise ValueError("分时日期无效") from None
    observed = observed_at or dt.datetime.now(dt.timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("分时采集时间必须包含时区")
    if day is not None and day > observed.astimezone(SHANGHAI).date():
        raise ValueError("不能查询尚未发生的分时日期")
    return {"symbol": symbol, "code": code, "market": market, "kind": kind,
            "date": date, "observedAt": observed,
            "secid": ("1." if market == "sh" else "0.") + code}


def fetch_bytes(secid: str) -> bytes:
    """Use one fixed public endpoint with response and execution bounds."""
    if not re.fullmatch(r"[01]\.[0-9]{6}", secid):
        raise ValueError("在线分时证券标识无效")
    if not _SLOTS.acquire(blocking=False):
        raise ValueError("已有分时请求正在联网，请稍后重试")
    try:
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": "5", "iscr": "0", "secid": secid,
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
        }
        request = Request(URL + "?" + urlencode(params), headers={"User-Agent": "A-trade/1.0"})
        deadline = time.monotonic() + 15
        with urlopen(request, timeout=5) as response:
            if response.status != 200 or response.geturl() != request.full_url:
                raise ValueError("在线分时来源暂不可用")
            size = response.headers.get("Content-Length")
            if size is not None and (not size.isascii() or not size.isdigit() or int(size) > MAX_BYTES):
                raise ValueError("在线分时响应大小无效或超过上限")
            chunks = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("在线分时请求超时，请稍后重试")
                part = response.read(min(64 * 1024, MAX_BYTES + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
                if len(chunks) > MAX_BYTES:
                    raise ValueError("在线分时响应超过 512 KiB")
            return bytes(chunks)
    except (TimeoutError, HTTPError, URLError, OSError):
        raise ValueError("在线分时连接失败或超时，请稍后重试") from None
    finally:
        _SLOTS.release()


def _number(raw: str, *, price=False, required=False):
    if raw.strip() in ("", "-", "--", "null"):
        if required:
            raise ValueError()
        return None
    if len(raw) > 40:
        raise ValueError()
    value = Decimal(raw)
    if not value.is_finite() or value < 0 or value > Decimal("1e18"):
        raise ValueError()
    if value.as_tuple().exponent < -8:
        raise ValueError()
    if price and value == 0:
        if required:
            raise ValueError()
        return None
    return float(value)


def parse_response(contents: bytes, config: dict) -> dict:
    if len(contents) > MAX_BYTES:
        raise ValueError("在线分时响应超过 512 KiB")
    try:
        payload = json.loads(contents)
        if not isinstance(payload, dict) or type(payload.get("rc", 0)) is not int or payload.get("rc", 0) != 0:
            raise ValueError()
        source = payload.get("data")
        if not isinstance(source, dict):
            raise ValueError()
        if (source.get("code") != config["code"] or type(source.get("market")) is not int
                or source["market"] != int(config["secid"][0])):
            raise ValueError()
        records = source.get("trends")
        if not isinstance(records, list) or len(records) > MAX_ROWS:
            raise ValueError()
        rows_by_day, seen, available_dates = {}, set(), set()
        missing_fields = set()
        excluded = {}
        for raw in records:
            if not isinstance(raw, str) or len(raw) > 512:
                raise ValueError()
            fields = raw.split(",")
            if len(fields) != 8 or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}", fields[0]):
                raise ValueError()
            stamp = dt.datetime.strptime(fields[0], "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
            if fields[0] in seen:
                raise ValueError()
            seen.add(fields[0])
            opening, close, high, low = (_number(fields[i], price=True, required=i == 2)
                                         for i in (1, 2, 3, 4))
            volume, amount, average = (_number(fields[5]), _number(fields[6]),
                                       _number(fields[7], price=True))
            if high is not None and low is not None and high < low:
                raise ValueError()
            for value in (opening, close):
                if value is not None and ((high is not None and value > high)
                                          or (low is not None and value < low)):
                    raise ValueError()
            day = stamp.date().isoformat()
            if stamp + dt.timedelta(minutes=1) > config["observedAt"]:
                excluded[day] = excluded.get(day, 0) + 1
                continue
            available_dates.add(day)
            row = {"time": stamp.strftime("%H:%M"), "open": opening,
                   "high": high, "low": low, "close": close, "volume": volume,
                   "amount": amount, "average": average}
            if any(row[key] is None for key in ("open", "high", "low", "volume", "amount")):
                missing_fields.add(day)
            rows_by_day.setdefault(day, []).append(row)
            if len(rows_by_day[day]) > 500:
                raise ValueError()
        selected_date = config["date"] or max(available_dates, default=None)
        rows = rows_by_day.get(selected_date, [])
        if not rows:
            label = config["date"] or "近期交易日"
            raise MissingIntraday(f"在线来源没有 {label} 的真实分时记录（仅提供近期窗口）",
                                  sorted(available_dates))
        rows.sort(key=lambda row: row["time"])
        warnings = ["在线分时来自东方财富近期交易日窗口，历史可用范围以返回日期为准"]
        if selected_date in missing_fields:
            warnings.append("来源未提供部分分钟的开高低价或成交量额，缺失值保持为空")
        if excluded.get(selected_date):
            warnings.append("尚未结束的分钟已排除，等待后续刷新")
        return {
            "symbol": config["symbol"], "code": config["code"], "date": selected_date,
            "name": str(source.get("name") or "")[:80], "rows": rows,
            "provider": "eastmoney", "asOf": f"{selected_date}T{rows[-1]['time']}:00+08:00",
            "receivedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "volumeUnit": "手", "amountUnit": "元", "warnings": warnings,
            "cached": False, "availableDates": sorted(available_dates),
        }
    except MissingIntraday:
        raise
    except (ValueError, TypeError, KeyError, InvalidOperation, UnicodeError,
            RecursionError, OverflowError):
        raise ValueError("在线分时来源格式、证券标识或数值无效，未保存为有效缓存") from None


def read_online_intraday(symbol: str, date: str | None = None, *, observed_at: dt.datetime | None = None) -> dict:
    config = request_config(symbol, date, observed_at)
    return parse_response(fetch_bytes(config["secid"]), config)
