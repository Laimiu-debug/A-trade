"""Daily turnover and volume activity from the user's selected TDX source.

Activity follows FINAL-TRADE's turnover / trailing mean - 1 formula. It is
deliberately separate from order-size money-flow estimates and net inflows.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
import re

from . import tdx


TTL = 300
MAX_ROWS = 770  # longest visible period plus a complete 20-day warm-up
LIMITS = (30, 60, 120, 250, 750)
WINDOWS = (5, 10, 20)
SCOPE = "通达信 sh8802xx 起的板块及分类指数、sh881 行业细分指数；不含 sh8800xx/sh8801xx 统计指数。"
NOTE = "量能热度＝（当日成交额 ÷ 含当日的完整窗口平均成交额－1）×100%；反映成交活跃程度，不代表主力净流入。"
UNITS = {"amount": "元", "amountAverage": "元", "amountDelta": "元",
         "activityPercent": "%", "changePercent": "%", "returnPercent": "%"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sector_symbol(symbol):
    return bool(re.fullmatch(r"sh(?:880[2-9][0-9]{2}|881[0-9]{3})", symbol, re.ASCII))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _validate_rows(rows, max_count=MAX_ROWS):
    if not isinstance(rows, list) or not rows or len(rows) > max_count:
        raise ValueError("尚未读取到有效日线记录")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    previous = ""
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("日线记录格式无效")
        day = row.get("date")
        try:
            parsed = dt.date.fromisoformat(day)
            if parsed.isoformat() != day or parsed > today or parsed.year < 1990 or day <= previous:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("日线日期重复、顺序无效或超出当前日期") from None
        for field in ("open", "high", "low", "close", "amount"):
            if not _number(row.get(field)) or row[field] < 0:
                raise ValueError("日线含有无效价格或成交额")
        if not 0 < row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise ValueError("日线开高低收价格无效")
        previous = day
        clean.append({key: row[key] for key in ("date", "open", "high", "low", "close", "amount")})
    return clean


def _tail(path, count, as_of=None):
    """Read only a bounded tail, rejecting a concurrent terminal rewrite."""
    if as_of:
        rows = _validate_rows(tdx.parse_daily(tdx.read_stable(path)), tdx.MAX_DAY_BYTES // tdx.DAY.size)
        rows = [row for row in rows if row["date"] <= as_of]
        if not rows:
            raise ValueError("所选日期之前没有日线记录")
        return rows[-count:], len(rows)
    try:
        with path.open("rb") as handle:
            before = path.stat()
            tdx.validate_size(before.st_size)
            size = min(count * tdx.DAY.size, before.st_size)
            handle.seek(-size, 2)
            data = handle.read(size)
            after = path.stat()
        if tdx._signature(before) != tdx._signature(after) or len(data) != size:
            raise ValueError("通达信正在更新日线文件，请稍后刷新")
        return _validate_rows(tdx.parse_daily(data)), before.st_size // tdx.DAY.size
    except OSError:
        raise ValueError("日线文件无法读取，请检查所选通达信目录或下载日线") from None


def _metrics(rows, window):
    result = []
    for index, row in enumerate(rows):
        prior = rows[index - 1] if index else None
        amounts = [item["amount"] for item in rows[index - window + 1:index + 1]] if index + 1 >= window else []
        average = sum(amount / window for amount in amounts) if amounts else None
        item = {"date": row["date"], "close": row["close"], "amount": row["amount"],
                "amountAverage": average,
                "amountDelta": row["amount"] - prior["amount"] if prior else None,
                "activityPercent": (row["amount"] / average - 1) * 100 if average else None,
                "changePercent": (row["close"] / prior["close"] - 1) * 100 if prior else None}
        if any(value is not None and not _number(value) for key, value in item.items() if key != "date"):
            raise ValueError("日线计算结果超出有效数值范围")
        result.append(item)
    return result


class FundsActivity:
    def __init__(self, owner):
        self.owner = owner
        owner.db.execute("CREATE TABLE IF NOT EXISTS funds_activity_cache (source TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(source,key))")
        owner.db.commit()

    def close(self):
        pass

    def _source(self):
        selected = self.owner._get("path") or self.owner.online.source()
        if not selected:
            raise ValueError("请先在设置中选择通达信目录")
        root = Path(selected)
        if not root.is_absolute():
            raise ValueError("通达信目录须为完整路径")
        root = root.resolve()
        return root.parent if root.name.lower() == "vipdoc" else root

    def _cached(self, root, key):
        row = self.owner.db.execute("SELECT data FROM funds_activity_cache WHERE source=? AND key=?",
                                    (os.path.normcase(str(root)), key)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, root, key, data):
        with self.owner.db:
            self.owner.db.execute("INSERT OR REPLACE INTO funds_activity_cache VALUES (?,?,?)",
                                  (os.path.normcase(str(root)), key, json.dumps(data, ensure_ascii=False, allow_nan=False)))
            self.owner.db.execute("DELETE FROM funds_activity_cache WHERE rowid NOT IN (SELECT rowid FROM funds_activity_cache ORDER BY rowid DESC LIMIT 300)")

    @staticmethod
    def _fresh(saved):
        if not saved:
            return False
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(saved["receivedAt"])).total_seconds()
        return 0 <= age < TTL

    @staticmethod
    def _meta(root, kind="sector", provider="tdx_local"):
        return {"sourceRoot": str(root), "provider": provider,
                "sourceName": "通达信板块与分类指数日线" if kind == "sector" else "通达信个股日线",
                "cached": False, "stale": False, "receivedAt": None, "warnings": [NOTE], "errors": [],
                "units": dict(UNITS), "scope": SCOPE if kind == "sector" else "所选通达信目录内的股票日线", "adjustment": "none"}

    def catalogue(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"refresh"} or type(payload.get("refresh", False)) is not bool:
            raise ValueError("板块量能目录参数无效")
        root = self._source()
        saved = self._cached(root, "catalogue")
        if not payload.get("refresh") and self._fresh(saved):
            return {**saved, "cached": True}
        try:
            tdx.normalize_directory(str(root))
            folder = tdx.within(root / "vipdoc/sh/lday", root)
            names, name_warnings = tdx.read_names(root)
            items, excluded = [], {"statistics": 0, "unnamed": 0, "invalid": 0}
            candidates = 0
            for path in sorted(folder.iterdir()):
                symbol = path.stem.lower()
                if path.suffix.lower() != ".day" or not re.fullmatch(r"sh88[01][0-9]{3}", symbol):
                    continue
                if not sector_symbol(symbol):
                    excluded["statistics"] += 1
                    continue
                candidates += 1
                if symbol not in names:
                    excluded["unnamed"] += 1
                    continue
                try:
                    rows, _ = _tail(tdx.within(path, root / "vipdoc"), 6)
                    row = _metrics(rows, 5)[-1]
                    items.append({"id": symbol, "symbol": symbol, "name": names[symbol], **row})
                except ValueError:
                    excluded["invalid"] += 1
            latest = max((item["date"] for item in items), default=None)
            for item in items:
                item["stale"] = item["date"] != latest
            items.sort(key=lambda item: (item["stale"], item["activityPercent"] is None, -(item["activityPercent"] or 0), item["symbol"]))
            warnings = [NOTE, *name_warnings]
            if excluded["unnamed"] or excluded["invalid"]:
                warnings.append(f"已跳过 {excluded['unnamed']} 个缺少名称、{excluded['invalid']} 个日线无效的板块或分类指数。")
            if not items:
                warnings.append("所选目录暂无有效板块日线，请在通达信中下载板块日线后刷新。")
            dates = sorted({row["date"] for row in items})
            if len(dates) > 1:
                warnings.append("各板块日线更新日期不同；最新观察日优先排序，旧日记录置后并保留各自数据日期。")
            data = {**self._meta(root), "items": items, "total": len(items), "window": 5,
                    "asOf": dates[-1] if dates else None, "rankingAsOf": latest, "startDate": dates[0] if dates else None,
                    "receivedAt": now(), "status": "ready" if items else "empty", "warnings": warnings,
                    "coverage": {"candidates": candidates, "included": len(items), "excluded": excluded}}
            # An empty or unreadable scan must not erase an earlier usable catalogue.
            if not items and saved and saved.get("items"):
                raise ValueError("本次未读到有效板块日线，保留同一目录上次成功读取的目录")
            self._save(root, "catalogue", data)
            return data
        except (ValueError, OSError) as error:
            message = str(error) if isinstance(error, ValueError) else "所选板块日线目录无法读取"
            if saved:
                return {**saved, "cached": True, "stale": True, "warnings": [*saved["warnings"], message], "errors": [message]}
            return {**self._meta(root), "items": [], "total": 0, "window": 5, "asOf": None,
                    "startDate": None, "rankingAsOf": None, "status": "unavailable", "errors": [message],
                    "coverage": {"candidates": 0, "included": 0, "excluded": {"statistics": 0, "unnamed": 0, "invalid": 0}}}

    def _read(self, root, symbol, kind, as_of):
        names, warnings = tdx.read_names(root)
        try:
            rows, count = _tail(tdx.daily_path(root, symbol), MAX_ROWS, as_of)
            return rows, count, names.get(symbol, symbol[2:]), "tdx_local", warnings
        except ValueError as local_error:
            # Never substitute bars from a separately configured terminal directory.
            online_source = self.owner.online.source()
            if not online_source or Path(online_source).resolve() != root:
                raise local_error
            try:
                if kind == "sector":
                    entries = self.owner.online.sectors().get("items", [])
                    entry = next((item for item in entries if item.get("symbol") == symbol), None)
                    if entry is None:
                        raise ValueError("当前通达信板块目录中没有所选板块")
                    name = entry.get("name") or names.get(symbol, symbol[2:])
                else:
                    name = names.get(symbol, symbol[2:])
                query = {"symbol": symbol, "period": "1d", "limit": MAX_ROWS}
                if as_of:
                    query["end"] = as_of
                response = self.owner.online.bars(query)
                if response.get("symbol") != symbol:
                    raise ValueError("通达信日线返回了不同的证券标识")
                rows = _validate_rows(response.get("rows"))
                if as_of:
                    rows = [row for row in rows if row["date"] <= as_of]
                    if not rows:
                        raise ValueError("所选日期之前没有日线记录")
                return rows, len(rows), name, "tdx_mcp", [*warnings, "本地日线不可用，本次使用同一通达信目录的 MCP 日线。", *response.get("warnings", [])]
            except (ValueError, KeyError, TypeError) as online_error:
                message = str(online_error) if isinstance(online_error, ValueError) else "通达信 MCP 日线格式无效"
                raise ValueError(f"{local_error}；{message}") from None

    def history(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"symbol", "kind", "limit", "window", "refresh", "asOf"}:
            raise ValueError("量能走势参数无效")
        market, code = tdx.symbol_parts(payload.get("symbol"))
        symbol = market + code
        kind, limit, window, refresh = payload.get("kind", "sector"), payload.get("limit", 60), payload.get("window", 5), payload.get("refresh", False)
        if kind not in ("sector", "stock") or (kind == "sector" and not sector_symbol(symbol)) or (kind == "stock" and tdx.security_kind(market, code) != "stock"):
            raise ValueError("请选择有效板块指数或股票代码")
        if type(limit) is not int or limit not in LIMITS or type(window) is not int or window not in WINDOWS or type(refresh) is not bool:
            raise ValueError("走势区间须为 30、60、120、250 或 750 日；均值窗口须为 5、10 或 20 日")
        as_of = payload.get("asOf")
        if as_of is not None:
            try:
                date = dt.date.fromisoformat(as_of)
                if date.isoformat() != as_of or date > dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date() or date.year < 1990:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError("截止日期须为不晚于今天的 YYYY-MM-DD 日期") from None
        root = self._source()
        key = f"history:{kind}:{symbol}:{as_of or 'latest'}"
        saved = self._cached(root, key)
        if not refresh and self._fresh(saved):
            return self._present({**saved, "cached": True}, limit, window)
        try:
            rows, available, name, provider, warnings = self._read(root, symbol, kind, as_of)
            data = {**self._meta(root, kind, provider), "id": symbol, "symbol": symbol, "name": name, "kind": kind,
                    "rows": rows, "availableCount": available, "asOf": rows[-1]["date"], "receivedAt": now(),
                    "status": "ready", "requestedAsOf": as_of, "warnings": [NOTE, *warnings]}
            result = self._present(data, limit, window)
            self._save(root, key, data)
            return result
        except (ValueError, OSError) as error:
            message = str(error) if isinstance(error, ValueError) else "无法读取所选日线来源"
            if saved:
                return self._present({**saved, "cached": True, "stale": True,
                                      "warnings": [*saved["warnings"], "本次未更新，保留同一来源上次成功读取的日线。"], "errors": [message]}, limit, window)
            return {**self._meta(root, kind), "id": symbol, "symbol": symbol, "name": code, "kind": kind,
                    "items": [], "availableCount": 0, "total": 0, "requestedLimit": limit, "window": window,
                    "asOf": None, "startDate": None, "endDate": None, "requestedAsOf": as_of, "status": "unavailable", "errors": [message]}

    @staticmethod
    def _present(data, limit, window):
        rows = data["rows"]
        items = _metrics(rows, window)[-limit:]
        base = items[0]["close"]
        for item in items:
            item["returnPercent"] = (item["close"] / base - 1) * 100
            if not _number(item["returnPercent"]):
                raise ValueError("日线累计涨跌幅超出有效数值范围")
        result = {key: value for key, value in data.items() if key != "rows"}
        result.update(items=items, total=len(items), requestedLimit=limit, window=window,
                      startDate=items[0]["date"], endDate=items[-1]["date"])
        return result
