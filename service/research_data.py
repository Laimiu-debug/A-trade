"""Read-only research evidence, isolated public-history cache and quality checks.

BaoStock wire layout follows its published Python client 0.9.4 (PyPI), not an
undocumented dependency on pandas. Public anonymous access is TCP, not HTTPS.
Provider-adjusted prices/factors are signal inputs, NEVER dividend cash flows.
The local market.sqlite3 and .day files are never written by this module.
"""
from __future__ import annotations

import bisect
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import struct
import time
import zlib

BAOSTOCK_DOC = "https://www.baostock.com/mainContent?file=history_A_stock_k_data.md"
BAOSTOCK_SOURCE = "https://pypi.org/project/baostock/0.9.4/"
BAOSTOCK_FACTOR_DOC = "https://www.baostock.com/helpdocs/pdf/BaoStock复权因子简介.pdf"
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100000
PROFILE_VERSION = "research-data-v1"


class DataSourceError(ValueError):
    """An unavailable or malformed source is never converted into empty success."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期必须为 YYYY-MM-DD")
    dt.date.fromisoformat(value)
    return value


def _period(start, end):
    _date(start)
    _date(end)
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")


def _symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh|sz|bj)\.?\d{6}", symbol):
        raise ValueError("证券代码格式错误")
    return symbol.replace(".", "")


def _number(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _positive(value):
    number = _number(value)
    return number if number is not None and number > 0 else None


class BaoStockClient:
    """Bounded, connection-local anonymous client; context manager required.

    Query methods return {fields, rows, provenance}; rows retain original strings.
    Pass a client factory to HistoryCache.fetch for deterministic/offline tests.
    """

    def __init__(self, timeout=30, *, socket_factory=None):
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 120:
            raise ValueError("数据源超时必须在 0 到 120 秒之间")
        self.socket_factory = socket_factory or socket.create_connection
        self.sock = None
        self.server_version = None

    def __enter__(self):
        if self.sock is not None:
            raise DataSourceError("数据源连接已打开")
        try:
            self.sock = self.socket_factory(("public-api.baostock.com", 10030), timeout=self.timeout)
            self._request("00", ["login", "anonymous", "123456", "0"])
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __exit__(self, *_):
        self.close()

    def _request(self, kind, parts):
        if self.sock is None:
            raise DataSourceError("请在 with BaoStockClient() 上下文中查询")
        if any("\1" in str(v) or "\n" in str(v) for v in parts):
            raise ValueError("数据源查询参数包含无效字符")
        body = "\1".join(str(v) for v in parts).encode("utf-8")
        packet = b"00.9.40\1" + kind.encode("ascii") + b"\1" + str(len(body)).zfill(10).encode() + body
        self.sock.sendall(packet + b"\1" + str(zlib.crc32(packet)).encode() + b"\n")
        data = bytearray()
        deadline = time.monotonic() + self.timeout
        while not data.endswith(b"<![CDATA[]]>\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DataSourceError("BaoStock 查询超时")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise DataSourceError("BaoStock 连接提前关闭")
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                raise DataSourceError("BaoStock 响应超过安全大小")
        try:
            header = bytes(data[:21]).decode("ascii").split("\1")
            length = int(header[2])
            if len(header) != 3 or header[1] != str(int(kind) + 1).zfill(2) or not 0 <= length <= MAX_RESPONSE_BYTES:
                raise ValueError("invalid response header")
            if len(data) < 21 + length:
                raise ValueError("truncated response")
            payload = bytes(data[21:21 + length])
            if kind == "95":
                decoder = zlib.decompressobj()
                payload = decoder.decompress(payload, MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES or decoder.unconsumed_tail or not decoder.eof:
                    raise ValueError("invalid or oversized compressed response")
            result = payload.decode("utf-8").split("\1")
            if len(result) < 4 or result[2] != parts[0]:
                raise ValueError("response method mismatch")
            self.server_version = header[0]
            if result[0] != "0":
                raise DataSourceError(f"BaoStock {parts[0]}: {result[0]} {result[1][:300]}")
            return result, hashlib.sha256(data).hexdigest()
        except (UnicodeError, ValueError, IndexError, zlib.error) as exc:
            if isinstance(exc, DataSourceError):
                raise
            raise DataSourceError("BaoStock 响应格式错误") from exc

    def _query(self, kind, method, args, field_index):
        rows, fields, hashes = [], None, []
        started = _now()
        for page in range(1, 102):
            response, digest = self._request(kind, [method, "anonymous", str(page), "2000", *args])
            try:
                current_fields = [v.strip() for v in response[field_index].split(",")]
                page_rows = json.loads(response[6])["record"]
                if response[4] != str(page) or not isinstance(page_rows, list):
                    raise ValueError("page mismatch")
                if fields is not None and fields != current_fields:
                    raise ValueError("schema drift")
                fields = current_fields
                for row in page_rows:
                    # Official dividend API currently appends one undocumented empty field.
                    # Keep it explicitly rather than shifting or discarding named columns.
                    if not isinstance(row, list) or len(row) < len(fields):
                        raise ValueError("field count mismatch")
                    record = dict(zip(fields, row))
                    if len(row) > len(fields):
                        record["_extraFields"] = row[len(fields):]
                    rows.append(record)
                hashes.append(digest)
                if len(rows) > MAX_ROWS:
                    raise ValueError("too many rows")
                # 0.9.4 documents some methods return an entire unpaged response (>2000).
                if len(page_rows) != 2000:
                    break
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise DataSourceError(f"BaoStock {method} 数据结构无效") from exc
        else:
            raise DataSourceError("BaoStock 分页超过限制")
        return {"fields": fields, "rows": rows, "provenance": {
            "provider": "baostock", "transport": "tcp", "endpoint": "public-api.baostock.com:10030",
            "method": method, "parameters": list(args), "retrievedAt": started,
            "clientProtocol": "00.9.40", "serverVersion": self.server_version,
            "source": BAOSTOCK_SOURCE, "documentation": BAOSTOCK_DOC,
            "responseSha256": hashes, "rowCount": len(rows)}}

    def history(self, symbol, start_date, end_date, adjustflag="3"):
        symbol = _symbol(symbol)
        if symbol.startswith("bj"):
            raise ValueError("BaoStock 历史日线适配仅支持沪深证券")
        _period(start_date, end_date)
        if str(adjustflag) not in {"1", "2", "3"}:
            raise ValueError("复权标识无效")
        return self._query("95", "query_history_k_data_plus", [symbol[:2] + "." + symbol[2:], DAILY_FIELDS,
                           start_date, end_date, "d", str(adjustflag)], 8)

    def adjust_factors(self, symbol, start_date="1990-01-01", end_date=None):
        symbol = _symbol(symbol)
        end_date = end_date or dt.date.today().isoformat()
        _period(start_date, end_date)
        return self._query("15", "query_adjust_factor", [symbol[:2] + "." + symbol[2:], start_date, end_date], 10)

    def dividends(self, symbol, year, year_type="operate"):
        symbol = _symbol(symbol)
        if not 1990 <= int(year) <= 2200 or year_type not in {"operate", "report"}:
            raise ValueError("分红查询参数无效")
        return self._query("13", "query_dividend_data", [symbol[:2] + "." + symbol[2:], str(year), year_type], 10)

    def all_stock(self, day):
        return self._query("35", "query_all_stock", [_date(day)], 8)

    def trade_dates(self, start_date, end_date):
        _period(start_date, end_date)
        return self._query("33", "query_trade_dates", [start_date, end_date], 9)


def _normalize_bundle(symbol, start, end, responses):
    """No adjusted/raw ratio inference; only the provider's explicit factor field."""
    raw, adjusted, st, preclose, status, factor_events = [], [], {}, {}, {}, {}
    warnings = []
    expected_code = symbol[:2] + "." + symbol[2:]
    for query_key, destination in (("raw", raw), ("adjusted", adjusted)):
        seen = set()
        for record in responses.get(query_key, {}).get("rows", []):
            day = _date(record["date"])
            if day in seen or record.get("code") != expected_code or not start <= day <= end:
                raise DataSourceError("历史数据存在重复键、证券或日期范围不匹配")
            seen.add(day)
            values = {key: _number(record.get(key)) for key in ("open", "high", "low", "close", "volume", "amount")}
            if any(values[k] is None for k in values):
                raise DataSourceError("历史数据包含缺失或非有限数值")
            if any(values[k] <= 0 for k in ("open", "high", "low", "close")) or values["volume"] < 0 or values["amount"] < 0:
                raise DataSourceError("历史数据包含非法价格或成交量")
            if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"]):
                raise DataSourceError("历史数据 OHLC 包络错误")
            destination.append({"date": day, **values})
            if query_key == "raw":
                if record.get("adjustflag") != "3":
                    raise DataSourceError("执行行情不是不复权价格")
                if record.get("isST") in ("0", "1"):
                    st[day] = record["isST"] == "1"
                if record.get("tradestatus") in ("0", "1"):
                    status[day] = int(record["tradestatus"])
                if _positive(record.get("preclose")):
                    preclose[day] = float(record["preclose"])
        destination.sort(key=lambda r: r["date"])
    for row in responses.get("factors", {}).get("rows", []):
        day = _date(row["dividOperateDate"])
        value = _positive(row.get("backAdjustFactor"))
        if row.get("code") != expected_code or day > end or value is None or day in factor_events:
            raise DataSourceError("复权因子键、日期或数值无效")
        factor_events[day] = value
    factor_dates = sorted(factor_events)
    factors = {}
    for row in raw:
        index = bisect.bisect_right(factor_dates, row["date"]) - 1
        if index >= 0:
            factors[row["date"]] = factor_events[factor_dates[index]]
    actions = []
    for key, response in responses.items():
        if not key.startswith("dividends:"):
            continue
        for row in response["rows"]:
            if row.get("code") != expected_code:
                raise DataSourceError("企业行动证券代码不匹配")
            ex_date = row.get("dividOperateDate")
            if not ex_date:
                warnings.append("分红数据含尚无除权日的记录，原始响应保留但不用于事件账本")
                continue
            _date(ex_date)
            if not start <= ex_date <= end:
                continue
            before_tax = _number(row.get("dividCashPsBeforeTax"))
            shares = _number(row.get("dividStocksPs"))
            action = {"symbol": symbol, "exDate": ex_date, "recordDate": row.get("dividRegistDate") or None,
                      "payDate": row.get("dividPayDate") or None,
                      "shareListingDate": row.get("dividStockMarketDate") or None,
                      "cashPerShareBeforeTax": before_tax, "cashPerShareGross": before_tax,
                      "cashAfterTaxText": row.get("dividCashPsAfterTax") or None,
                      "stockDividendPerShare": shares, "source": "baostock.query_dividend_data", "raw": copy.deepcopy(row)}
            action["id"] = hashlib.sha256(_json(action).encode()).hexdigest()
            actions.append(action)
    # Exact duplicates are errors: multiplying the same dividend twice corrupts cash.
    if len({a["id"] for a in actions}) != len(actions):
        raise DataSourceError("企业行动记录重复")
    provenance = [r["provenance"] for r in responses.values()]
    raw_dates = {r["date"] for r in raw}
    if raw_dates != {r["date"] for r in adjusted}:
        warnings.append("原始和后复权日线日期未完全对齐，复权覆盖必须单独审查")
    warnings.append("历史分红接口不保证完整覆盖配股、合并和其他企业行动；税后现金需按持有期及实际政策核算")
    return {"schemaVersion": PROFILE_VERSION, "provider": "baostock", "symbol": symbol,
            "startDate": start, "endDate": end, "rawBars": raw, "adjustedBars": adjusted,
            "stByDate": st, "precloseByDate": preclose, "tradeStatusByDate": status,
            "factorByDate": factors, "factorEvents": factor_events, "corporateActions": actions, "dividendEvents": copy.deepcopy(actions),
            "corporateActionsComplete": False, "factorPurpose": "signal-only-back-adjustment",
            "coverage": {"rawBars": len(raw), "historicalSt": len(st), "tradeStatus": len(status),
                         "preclose": len(preclose), "factors": len(factors), "corporateActions": len(actions),
                         "firstDate": raw[0]["date"] if raw else None, "lastDate": raw[-1]["date"] if raw else None},
            "provenance": provenance, "warnings": warnings, "rawResponses": responses}


class HistoryCache:
    """Immutable source snapshots in a dedicated cache, never the market database.

    refresh=False returns the exact saved snapshot with its original retrieval time.
    refresh=True either stores a new traceable version or raises; no silent stale fallback.
    """

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "research-history.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    digest TEXT PRIMARY KEY, request_key TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                    content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current_snapshots (
                    request_key TEXT PRIMARY KEY, digest TEXT NOT NULL REFERENCES snapshots(digest));
            """)

    def _get(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT s.digest,s.content FROM snapshots s JOIN current_snapshots c ON c.digest=s.digest WHERE c.request_key=?", (key,)).fetchone()
        if not row:
            return None
        if hashlib.sha256(row[1].encode()).hexdigest() != row[0]:
            raise DataSourceError("历史缓存校验失败，原文件已保留")
        result = json.loads(row[1])
        result["cache"] = {"hit": True, "digest": row[0], "immutable": True}
        return result

    def _put(self, key, content):
        encoded = _json(content)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)", (digest, key, _now(), encoded))
            db.execute("INSERT INTO current_snapshots VALUES(?,?) ON CONFLICT(request_key) DO UPDATE SET digest=excluded.digest", (key, digest))
        result = copy.deepcopy(content)
        result["cache"] = {"hit": False, "digest": digest, "immutable": True}
        return result

    def fetch(self, symbol, start_date, end_date, *, refresh=False, client_factory=BaoStockClient):
        symbol = _symbol(symbol)
        _period(start_date, end_date)
        key = _json([PROFILE_VERSION, "baostock", "history", symbol, start_date, end_date])
        if not refresh:
            saved = self._get(key)
            if saved is not None:
                return saved
        with client_factory() as client:
            responses = {"raw": client.history(symbol, start_date, end_date, "3"),
                         "adjusted": client.history(symbol, start_date, end_date, "1"),
                         "factors": client.adjust_factors(symbol, "1990-01-01", end_date)}
            for year in range(int(start_date[:4]), int(end_date[:4]) + 1):
                responses[f"dividends:{year}"] = client.dividends(symbol, year, "operate")
        result = _normalize_bundle(symbol, start_date, end_date, responses)
        if not result["rawBars"]:
            raise DataSourceError("请求日期内没有真实历史日线，未写入成功缓存")
        return self._put(key, result)

    def all_stock(self, day, *, refresh=False, client_factory=BaoStockClient):
        _date(day)
        key = _json([PROFILE_VERSION, "baostock", "all_stock", day])
        if not refresh:
            saved = self._get(key)
            if saved is not None:
                return saved
        with client_factory() as client:
            result = client.all_stock(day)
        return self._put(key, {**result, "day": day, "provider": "baostock"})

    def trade_dates(self, start_date, end_date, *, refresh=False, client_factory=BaoStockClient):
        _period(start_date, end_date)
        key = _json([PROFILE_VERSION, "baostock", "calendar", start_date, end_date])
        if not refresh:
            saved = self._get(key)
            if saved is not None:
                return saved
        with client_factory() as client:
            result = client.trade_dates(start_date, end_date)
        dates = [_date(r["calendar_date"]) for r in result["rows"] if r.get("is_trading_day") == "1"]
        return self._put(key, {**result, "dates": dates, "startDate": start_date, "endDate": end_date, "provider": "baostock"})


def profile_dataset(dataset, calendar=None, *, start_date=None, end_date=None):
    """Pure audit of supplied facts. Unknown coverage stays unknown, never passes.

    calendar may be {dates:[...], provenance:...} or a supplied iterable of true
    exchange sessions. Without one, the observed date union is a LOWER BOUND.
    Missing before/after observed history is shown separately from internal holes.
    """
    securities = dataset.get("securities", [])
    if start_date:
        _date(start_date)
    if end_date:
        _date(end_date)
    if start_date and end_date:
        _period(start_date, end_date)
    observations = set()
    for security in securities:
        for bar in security.get("bars", []):
            try:
                observations.add(_date(bar.get("date")))
            except (TypeError, ValueError):
                pass
    if calendar is None:
        reference = observations
        calendar_source = "observed-union-lower-bound"
        calendar_provenance = None
    else:
        values = calendar.get("dates", []) if isinstance(calendar, dict) else calendar
        reference = {_date(value) for value in values}
        calendar_source = "supplied-trading-calendar"
        calendar_provenance = calendar.get("provenance") if isinstance(calendar, dict) else None
    reference = sorted(day for day in reference if (not start_date or day >= start_date) and (not end_date or day <= end_date))
    issues, summaries, seen_symbols = [], [], set()

    def issue(code, symbol, dates=None, severity="warning", **details):
        dates = dates or []
        issues.append({"code": code, "symbol": symbol, "severity": severity, "count": len(dates) or 1,
                       "dates": dates[:100], "sampleTruncated": len(dates) > 100, **details})

    for security in securities:
        symbol = security.get("symbol")
        if symbol in seen_symbols:
            issue("duplicate-symbol", symbol, severity="error")
        seen_symbols.add(symbol)
        try:
            _symbol(symbol)
        except ValueError:
            issue("invalid-symbol", symbol, severity="error")
        valid, duplicate, invalid_dates, bad_prices, bad_volume, zero_volume, out_of_order = {}, [], [], [], [], [], []
        previous_date = None
        for index, bar in enumerate(security.get("bars", [])):
            try:
                day = _date(bar.get("date"))
            except (ValueError, TypeError):
                invalid_dates.append(str(bar.get("date")))
                continue
            if previous_date and day < previous_date:
                out_of_order.append(day)
            previous_date = day
            if day in valid:
                duplicate.append(day)
                continue
            if (start_date and day < start_date) or (end_date and day > end_date):
                continue
            valid[day] = bar
            prices = [_positive(bar.get(k)) for k in ("open", "high", "low", "close")]
            if None in prices or (None not in prices and (prices[1] < max(prices[0], prices[2], prices[3]) or prices[2] > min(prices[0], prices[3]))):
                bad_prices.append(day)
            volume, amount = _number(bar.get("volume")), _number(bar.get("amount"))
            if volume is None or volume < 0 or ("amount" in bar and (amount is None or amount < 0)):
                bad_volume.append(day)
            if volume == 0:
                zero_volume.append(day)
        for code, dates in (("duplicate-symbol-date", duplicate), ("invalid-date", invalid_dates),
                            ("invalid-ohlc", bad_prices), ("invalid-volume-amount", bad_volume), ("non-monotonic-dates", out_of_order)):
            if dates:
                issue(code, symbol, dates, "error")
        dates = sorted(valid)
        st = security.get("stByDate", security.get("stHistory", {})) or {}
        statuses = security.get("tradeStatusByDate", security.get("tradeStatusHistory", {})) or {}
        factors = security.get("factorByDate", security.get("factors", {})) or {}
        preclose = security.get("precloseByDate", {}) or {}
        known_st = [d for d in dates if type(st.get(d)) is bool]
        known_status = [d for d in dates if type(statuses.get(d)) is int and statuses[d] in (0, 1)]
        known_factors = [d for d in dates if _positive(factors.get(d)) is not None]
        historical_st_complete = bool(dates) and len(known_st) == len(dates)
        factor_complete = bool(dates) and len(known_factors) == len(dates)
        if not historical_st_complete:
            issue("historical-st-unknown", symbol, [d for d in dates if d not in st or type(st[d]) is not bool])
        if not factor_complete:
            issue("adjustment-factor-unknown", symbol, [d for d in dates if _positive(factors.get(d)) is None])
        missing = [d for d in reference if d not in valid]
        internal = [d for d in missing if dates and dates[0] < d < dates[-1]]
        known_suspensions = [d for d in missing if statuses.get(d) == 0 and type(statuses.get(d)) is int]
        unexplained = [d for d in internal if d not in known_suspensions]
        if unexplained:
            issue("missing-trading-session", symbol, unexplained)
        if zero_volume:
            issue("zero-volume-observed", symbol, zero_volume, "info")
        if not dates:
            issue("empty-history", symbol, severity="error")
        discontinuities = []
        factor_changes = []
        for previous, day in zip(dates, dates[1:]):
            prev_close = _positive(valid[previous].get("close"))
            opening = _positive(valid[day].get("open"))
            ref = _positive(preclose.get(day))
            prev_factor, factor = _positive(factors.get(previous)), _positive(factors.get(day))
            if prev_factor and factor and abs(factor / prev_factor - 1) > 1e-7:
                factor_changes.append(day)
            # A normal limit move is NOT an ex-right event. Reference/factor changes
            # are stronger evidence; a >35% gap is only a potential discontinuity.
            evidence = []
            if prev_close and ref and abs(ref / prev_close - 1) > max(0.0005, 0.015 / prev_close):
                evidence.append("provider-preclose-differs")
            if day in factor_changes:
                evidence.append("provider-factor-changed")
            if prev_close and opening and abs(opening / prev_close - 1) > 0.35:
                evidence.append("large-raw-gap")
            if evidence:
                discontinuities.append({"date": day, "previousDate": previous, "evidence": evidence,
                                        "previousClose": prev_close, "open": opening, "preclose": ref})
        action_dates = {a.get("exDate", a.get("date")) for a in security.get("corporateActions", [])}
        unexplained_events = [e["date"] for e in discontinuities if e["date"] not in action_dates]
        if unexplained_events:
            issue("corporate-action-unexplained", symbol, unexplained_events)
        if security.get("corporateActionsComplete") is not True:
            issue("corporate-action-coverage-unknown", symbol)
        summaries.append({"symbol": symbol, "barCount": len(dates), "firstDate": dates[0] if dates else None,
            "lastDate": dates[-1] if dates else None, "expectedSessions": len(reference),
            "observedExpectedSessions": sum(d in valid for d in reference),
            "missingSessions": len(missing), "internalMissingSessions": len(internal),
            "unexplainedInternalMissing": len(unexplained), "missingBeforeObserved": sum(bool(dates) and d < dates[0] for d in missing),
            "missingAfterObserved": sum(bool(dates) and d > dates[-1] for d in missing),
            "knownMissingSuspensions": len(known_suspensions), "zeroVolumeBars": len(zero_volume),
            "historicalSt": {"known": len(known_st), "total": len(dates), "complete": historical_st_complete},
            "tradeStatus": {"known": len(known_status), "total": len(dates)},
            "adjustmentFactors": {"known": len(known_factors), "total": len(dates), "complete": factor_complete},
            "corporateActionsComplete": security.get("corporateActionsComplete") is True,
            "potentialDiscontinuities": discontinuities, "factorChangeDates": factor_changes,
            "provenance": copy.deepcopy(security.get("provenance", []))})
    if not securities:
        issue("empty-universe", None, severity="error")
    return {"version": PROFILE_VERSION, "calendarSource": calendar_source, "calendarProvenance": calendar_provenance,
            "startDate": start_date or (reference[0] if reference else None),
            "endDate": end_date or (reference[-1] if reference else None),
            "securityCount": len(securities), "barCount": sum(s["barCount"] for s in summaries),
            "structurallyValid": not any(i["severity"] == "error" for i in issues),
            "performanceCertification": False,
            "certificationReason": "数据质量检查不等于现金分红、历史股票池与策略执行审计；禁止自动认证收益",
            "securities": summaries, "issues": issues,
            "warnings": ["当前证券名称不用于推断历史 ST。", "未发现企业行动不等于企业行动覆盖完整。"] +
                        (["缺少交易所日历；日期并集只能发现部分缺失，不能证明交易日完整。"] if calendar is None else [])}
