"""Source-qualified Tongdaxin sector-index history, never a member average."""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import time

from . import tdx
from .sector_daily import SectorDaily


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class SectorHistory:
    def __init__(self, owner):
        self.owner = owner
        self.catalogues = {}
        self.daily = SectorDaily(owner)
        owner.db.execute("CREATE TABLE IF NOT EXISTS sector_history_cache (source TEXT NOT NULL, symbol TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(source,symbol))")
        owner.db.commit()

    def close(self):
        self.catalogues.clear()

    def remember(self, source, items):
        self.catalogues = {source: (time.monotonic(), {item["symbol"]: item for item in items})}

    def _entry(self, source, symbol, refresh):
        cached = self.catalogues.get(source)
        # Refresh applies to this index's bars. A batch of 5/8 charts shares one
        # bounded catalogue; market.sectors explicitly publishes fresh membership.
        if not cached or time.monotonic() - cached[0] > 300:
            result = self.owner.online.sectors()
            self.remember(source, result.get("items", []))
            cached = self.catalogues[source]
        if symbol not in cached[1]:
            raise ValueError("所选代码不在当前通达信板块目录中，请刷新板块列表")
        return cached[1][symbol]

    def history(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"id", "limit", "refresh", "basis", "asOf"}:
            raise ValueError("板块走势参数无效")
        market, code = tdx.symbol_parts(payload.get("id"))
        symbol = market + code
        limit, refresh = payload.get("limit", 250), payload.get("refresh", False)
        if type(limit) is not int or not 2 <= limit <= 1000 or type(refresh) is not bool:
            raise ValueError("板块走势数量须为 2 至 1000 的整数")
        if payload.get("basis") == "daily":
            return self.daily.history(payload, now())
        if payload.get("basis") not in (None, "live") or payload.get("asOf") not in (None, ""):
            raise ValueError("选择截至日期请使用板块日线口径")
        source = self.owner.online.source()
        saved = self.owner.db.execute("SELECT data FROM sector_history_cache WHERE source=? AND symbol=?", (source, symbol)).fetchone()
        saved = json.loads(saved[0]) if saved else None
        if saved and not refresh and saved.get("requestedCount", 0) >= limit:
            received = dt.datetime.fromisoformat(saved["receivedAt"])
            if (dt.datetime.now(dt.timezone.utc) - received).total_seconds() < 300:
                return {**saved, "rows": saved["rows"][-limit:], "cached": True}
        try:
            entry = self._entry(source, symbol, refresh)
            count = max(250, limit)
            result = self.owner.online.bars({"symbol": symbol, "period": "1d", "limit": count})
            if result.get("symbol") != symbol:
                raise ValueError("板块指数走势返回了不同的证券标识")
            rows = result.get("rows")
            if not isinstance(rows, list) or not rows or len(rows) > count:
                raise ValueError("通达信尚未返回有效的板块指数日线")
            dates = []
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
            for row in rows:
                date = dt.date.fromisoformat(row["date"])
                if date > today:
                    raise ValueError("板块日线日期超出当前日期")
                dates.append(row["date"])
                for field in ("open", "high", "low", "close", "volume", "amount"):
                    value = row.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        raise ValueError("板块日线含无效数值")
                if not 0 < row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                    raise ValueError("板块日线价格区间不一致")
            if dates != sorted(set(dates)):
                raise ValueError("板块日线日期重复或顺序无效")
            data = {"id": symbol, "symbol": symbol, "name": entry["name"], "rows": rows,
                    "requestedCount": count, "dataAsOf": dates[-1], "receivedAt": now(),
                    "provider": "tdx_mcp", "sourceRoot": source, "sourceName": "通达信板块指数日线",
                    "cached": False, "stale": False, "status": "ready", "adjustment": "none",
                    "amountUnit": "元", "volumeUnit": result.get("volumeUnit", "原始单位"),
                    "warnings": list(result.get("warnings", []))}
            with self.owner.db:
                self.owner.db.execute("INSERT OR REPLACE INTO sector_history_cache VALUES (?,?,?)", (source, symbol, json.dumps(data, ensure_ascii=False, allow_nan=False)))
            return {**data, "rows": rows[-limit:]}
        except (ValueError, KeyError, TypeError) as error:
            warning = str(error) if isinstance(error, ValueError) else "通达信板块日线返回格式无效"
            if saved:
                return {**saved, "rows": saved["rows"][-limit:], "cached": True, "stale": True,
                        "warnings": [*saved.get("warnings", []), warning, "本次未更新，显示同一来源上次成功读取的板块日线。"]}
            return {"id": symbol, "symbol": symbol, "name": code, "rows": [], "dataAsOf": None,
                    "receivedAt": None, "provider": "tdx_mcp", "sourceRoot": source,
                    "sourceName": "通达信板块指数日线", "cached": False, "stale": False,
                    "status": "unavailable", "warnings": [warning]}

    def snapshot(self, payload):
        self.daily.validate_snapshot(payload)
        root = self.daily.source()
        online = self.owner.online
        catalogue, warnings = None, []
        if online.config()["enabled"]:
            if Path(online.source()).resolve() != root:
                warnings.append("本地日线与 MCP 使用不同目录，不能将两个来源的板块成分混配。")
            else:
                source = online.source()
                cached = self.catalogues.get(source)
                try:
                    if not cached or payload.get("refresh") or time.monotonic() - cached[0] > 300:
                        result = online.sectors()
                        self.remember(source, result.get("items", []))
                        cached = self.catalogues[source]
                    catalogue = list(cached[1].values())
                    if not catalogue:
                        raise ValueError("MCP 尚未提供可核对的成分股目录")
                except ValueError:
                    catalogue = None
                    self.catalogues.pop(source, None)
                    warnings.append("本次未读到 MCP 成分股目录；保留本地指数日线，连接恢复后刷新即可查看成分。")
        return self.daily.snapshot(payload, now(), member_catalogue=catalogue, catalogue_warnings=warnings)
