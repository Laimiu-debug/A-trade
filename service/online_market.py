"""Per-user local MCP configuration and source-qualified read caches."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

from service import tdx


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


class OnlineMarket:
    def __init__(self, owner):
        self.owner = owner
        self.client = None
        self.client_root = None
        self.status_root = None
        self.connected = False
        self.checked = None
        self.message = "尚未测试连接，请启动通达信并登录行情后测试。"
        self.names = None
        self.names_root = None
        self.names_revision = None
        owner.db.execute("CREATE TABLE IF NOT EXISTS online_cache (source TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(source,key))")
        owner.db.commit()

    def close(self):
        if self.client:
            self.client.close()
        self.client = None
        self.client_root = None
        self.connected = False

    def config(self):
        config = self.owner._get("onlineConfig", {})
        stored_root = config.get("root") or ""
        # Older nonempty roots are intentional fixed terminals. Empty roots keep
        # following the local source, including after later local path changes.
        follow_local = config.get("followLocal", not bool(stored_root))
        return {"enabled": config.get("enabled", False),
                "root": (self.owner._get("path") or "") if follow_local else stored_root,
                "configuredRoot": "" if follow_local else stored_root,
                "followLocal": follow_local,
                "refreshSeconds": config.get("refreshSeconds", 30)}

    def status(self):
        config = self.config()
        if self.status_root is not None and self.status_root != config["root"]:
            self.close()
            self.checked = None
            self.message = "行情目录已变更，请重新测试连接。"
        self.status_root = config["root"]
        root = Path(config["root"]) if config["root"] else None
        configured = bool(root and root.is_absolute() and (root / "PYPlugins/user/tqcenter.py").is_file()
                          and (root / "PYPlugins/TPythClient.dll").is_file())
        dependencies = all(importlib.util.find_spec(module) for module in ("mcp", "pandas", "numpy"))
        available = bool(configured and dependencies and os.name == "nt")
        message = self.message
        if not config["enabled"]:
            message = "未启用通达信 MCP，仍可查看本地日线与已下载分钟线。"
        elif not configured:
            message = "请选择具有 TQ 组件的通达信安装目录（需 PYPlugins/user/tqcenter.py 与 TPythClient.dll）。"
        elif not dependencies:
            message = "MCP 运行环境尚未安装，请在项目目录运行 npm run setup:market 后重启。"
        elif os.name != "nt":
            message = "通达信本地 MCP 需要 Windows。"
        if self.client_root and self.client_root != config["root"]:
            self.close()
        return {**config, "configured": configured, "available": available,
                "connected": bool(self.connected and config["enabled"] and available),
                "lastCheckedAt": self.checked, "message": message,
                "tools": list(getattr(self.client, "tools", []) or [])}

    def configure(self, payload):
        old = self.config()
        enabled = payload.get("enabled", old["enabled"])
        interval = payload.get("refreshSeconds", old["refreshSeconds"])
        raw = payload.get("root", old["configuredRoot"])
        if not isinstance(enabled, bool) or type(interval) is not int or not 10 <= interval <= 300:
            raise ValueError("刷新间隔须为 10 至 300 秒，并指定是否启用")
        if not isinstance(raw, str) or len(raw) > 32767:
            raise ValueError("通达信 MCP 目录无效")
        raw = raw.strip()
        follow_local = payload.get("followLocal", not bool(raw) if "root" in payload else old["followLocal"])
        if not isinstance(follow_local, bool):
            raise ValueError("请指定是否跟随本地日线目录")
        if not follow_local and not raw:
            raise ValueError("请填写独立的通达信 MCP 目录，或启用跟随本地日线目录")
        raw = "" if follow_local else raw
        if raw:
            location = Path(raw)
            if not location.is_absolute():
                raise ValueError("通达信 MCP 目录须为完整路径")
            raw = str(location.resolve())
        effective_root = (self.owner._get("path") or "") if follow_local else raw
        if enabled and (not effective_root or not (Path(effective_root) / "PYPlugins/user/tqcenter.py").is_file()
                        or not (Path(effective_root) / "PYPlugins/TPythClient.dll").is_file()):
            raise ValueError("所选目录缺少 TQ 组件，请选择支持 TQ 的通达信安装根目录")
        with self.owner.db:
            self.owner._set("onlineConfig", {"enabled": enabled, "root": raw, "followLocal": follow_local, "refreshSeconds": interval})
        self.close()
        self.checked = None
        self.message = "配置已保存，请测试连接。"
        return self.status()

    def call(self, tool, arguments):
        status = self.status()
        if not status["enabled"] or not status["available"]:
            raise ValueError(status["message"])
        try:
            if self.client is None:
                from service.mcp_client import MCPClient
                self.client = MCPClient(status["root"])
                self.client_root = status["root"]
            result = self.client.call(tool, arguments)
            self.connected = True
            self.checked = now()
            self.message = "通达信 MCP 已连接。快照不提供交易时间时会单独标注。"
            return result
        except Exception:
            self.close()
            self.checked = now()
            self.message = "通达信 MCP 未能返回数据，请确认所选终端已启动并登录行情，稍后重试。"
            raise ValueError(self.message) from None

    def test(self):
        try:
            result = self.call("tdx_status", {})
            if not result.get("connected"):
                self.close()
                self.message = "MCP 已启动，但通达信终端未连接，请检查对应终端的行情登录。"
        except ValueError:
            pass
        return self.status()

    def source(self):
        return self.config()["root"]

    def cached(self, key):
        row = self.owner.db.execute("SELECT data FROM online_cache WHERE source=? AND key=?", (self.source(), key)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, key, data):
        with self.owner.db:
            self.owner.db.execute("INSERT OR REPLACE INTO online_cache VALUES (?,?,?)", (self.source(), key, json.dumps(data, ensure_ascii=False, allow_nan=False)))

    def security_name(self, symbol):
        root = self.source() or self.owner._get("path")
        revision = self.owner.source_revision()
        if root != self.names_root or revision != self.names_revision:
            self.names = None
        if self.names is None:
            try:
                self.names, _ = tdx.read_names(Path(root)) if root else ({}, [])
            except (OSError, ValueError):
                self.names = {}
            self.names_root = root
            self.names_revision = revision
        record = self.owner.db.execute("SELECT name FROM securities WHERE symbol=?", (symbol,)).fetchone()
        return self.names.get(symbol) or (record[0] if record else "") or "名称待补全"

    def quotes(self, payload):
        symbols = payload.get("symbols")
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 24:
            raise ValueError("一次可读取 1 至 24 只证券的行情")
        symbols = list(dict.fromkeys("".join(tdx.symbol_parts(symbol)) for symbol in symbols))
        warnings, errors, items = [], [], []
        try:
            result = self.call("tdx_quotes", {"symbols": symbols})
            warnings.extend(result.get("warnings", []))
            records = result.get("items", [])
            identities = [item.get("symbol") for item in records]
            if len(set(identities)) != len(identities) or any(symbol not in symbols for symbol in identities):
                raise ValueError("行情身份不匹配")
            for item in records:
                item = {**item, "name": self.security_name(item["symbol"]), "provider": "tdx_mcp", "cached": False}
                price, previous = item.get("price"), item.get("previousClose")
                item["change"] = price - previous if price is not None and previous is not None else None
                item["changePercent"] = item["change"] / previous * 100 if previous and item["change"] is not None else None
                self.save("quote:" + item["symbol"], item)
                items.append(item)
            supplied = {item["symbol"] for item in items}
            errors = [{"symbol": symbol, "message": "通达信没有返回该证券的有效报价。"} for symbol in symbols if symbol not in supplied]
        except ValueError as error:
            warnings.append(str(error))
            errors = [{"symbol": symbol, "message": str(error)} for symbol in symbols]
        supplied = {item["symbol"] for item in items}
        for error in errors:
            if error["symbol"] in supplied:
                continue
            previous = self.cached("quote:" + error["symbol"])
            if previous:
                items.append({**previous, "cached": True, "warnings": [*previous.get("warnings", []), "来源未返回新报价，显示上次采集快照；不是本次更新。"]})
        return {"items": items, "errors": errors, "warnings": warnings, "provider": "tdx_mcp", "receivedAt": now()}

    def sectors(self):
        result = self.call("tdx_sectors", {})
        items = result.get("items", [])
        for item in items:
            item["id"] = item["symbol"]
            if item.get("symbol"):
                item["name"] = item.get("name") or self.security_name(item["symbol"])
        self.owner.sector_history.remember(self.source(), items)
        return {**result, "items": items, "provider": "tdx_mcp", "asOf": None, "receivedAt": now()}

    def sector(self, payload):
        identifier = payload.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 100:
            raise ValueError("请选择有效板块")
        entry = next((item for item in self.sectors()["items"] if item["id"] == identifier or item["name"] == identifier), None)
        if entry is None:
            raise ValueError("当前通达信目录中没有这个板块，请刷新列表")
        result = self.call("tdx_sector_stocks", {"sector": entry["name"]})
        items = [{"symbol": symbol, "code": symbol[2:], "name": self.security_name(symbol)} for symbol in result.get("symbols", [])]
        return {**result, "id": identifier, "name": entry["name"], "items": items, "provider": "tdx_mcp", "asOf": None, "receivedAt": now()}

    def bars(self, payload):
        result = self.call("tdx_bars", {key: value for key, value in payload.items() if key in ("symbol", "period", "limit", "start", "end")})
        rows = result.get("rows", [])
        if not rows:
            raise ValueError("通达信 MCP 尚未返回该证券的 K 线")
        return {**result, "name": self.security_name(result["symbol"]), "market": result["symbol"][:2],
                "kind": tdx.security_kind(result["symbol"][:2], result["code"]), "provider": "tdx_mcp",
                "lastReadAt": result["receivedAt"], "dataVersion": "mcp:" + result["dataAsOf"], "cached": False}
