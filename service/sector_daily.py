"""Sector returns and charts from the same dated, local index bars."""
from __future__ import annotations

from . import tdx
from .funds_activity import sector_symbol
from .market_research import read_raw_tail, validate_as_of


NOTE = "通达信板块指数未复权日线；5日、20日涨幅按对应交易日前的收盘点位计算，不是成分股平均涨幅。"


class SectorDaily:
    def __init__(self, owner):
        self.owner = owner

    def source(self):
        return tdx.normalize_directory(self.owner._get("path"))

    @staticmethod
    def rows(root, symbol, count, as_of):
        if not sector_symbol(symbol):
            raise ValueError("所选代码不是支持的通达信板块指数")
        raw, _ = read_raw_tail(tdx.daily_path(root, symbol), count, as_of)
        return tdx.parse_daily(raw) if raw else []

    @staticmethod
    def meta(root, as_of, received_at):
        return {"basis": "daily", "requestedAsOf": as_of, "provider": "tdx_local",
                "sourceRoot": str(root), "sourceName": "通达信本地板块指数日线",
                "adjustment": "none", "cached": False, "stale": False,
                "receivedAt": received_at, "methodology": NOTE, "warnings": []}

    @staticmethod
    def validate_snapshot(payload):
        if (not isinstance(payload, dict) or set(payload) - {"basis", "asOf", "refresh"}
                or payload.get("basis") != "daily" or type(payload.get("refresh", False)) is not bool):
            raise ValueError("板块日线快照参数无效")
        return validate_as_of(payload.get("asOf"))

    def snapshot(self, payload, received_at, *, member_catalogue=None, catalogue_warnings=()):
        as_of = self.validate_snapshot(payload)
        root = self.source()
        names, warnings = tdx.read_names(root)
        members_by_symbol = {item["symbol"]: item for item in member_catalogue} if member_catalogue is not None else None
        observations, failures, excluded = [], [], 0
        folder = tdx.within(root / "vipdoc/sh/lday", root)
        for path in sorted(folder.iterdir()):
            symbol = path.stem.lower()
            if path.suffix.lower() != ".day" or not sector_symbol(symbol) or symbol not in names:
                continue
            # Legacy indices can share a name with a different current sector.
            # Only an identical source and symbol establishes membership identity.
            if members_by_symbol is not None and symbol not in members_by_symbol:
                excluded += 1
                continue
            try:
                rows = self.rows(root, symbol, 21, as_of)
                if rows:
                    observations.append((symbol, names[symbol], rows))
            except ValueError:
                failures.append(symbol)
        dates = sorted({row["date"] for _, _, rows in observations for row in rows})
        actual = dates[-1] if dates else None
        items = []
        for symbol, name, rows in observations:
            by_date = {row["date"]: row for row in rows}
            current = by_date.get(actual)
            def change(days):
                base = by_date.get(dates[-days - 1]) if len(dates) > days else None
                return (current["close"] / base["close"] - 1) * 100 if current and base else None
            items.append({"id": symbol, "symbol": symbol, "code": symbol[2:], "name": name,
                          "date": rows[-1]["date"], "older": current is None,
                          "membershipAvailable": member_catalogue is not None,
                          "price": current["close"] if current else None,
                          "changePercent": change(1), "return5": change(5), "return20": change(20),
                          "amount": current["amount"] if current else None})
        result = {**self.meta(root, as_of, received_at), "items": items, "asOf": actual,
                  "computedAt": received_at, "total": len(items), "status": "ready" if items else "empty",
                  "coverage": {"included": len(items), "current": sum(not item["older"] for item in items),
                               "older": sum(item["older"] for item in items), "invalid": len(failures),
                               "outsideCurrentCatalogue": excluded},
                  "membershipCatalogueAvailable": member_catalogue is not None}
        result["warnings"].extend(warnings)
        result["warnings"].extend(catalogue_warnings)
        if member_catalogue is None:
            result["warnings"].append("当前无法核对 MCP 成分股目录，仅展示本地指数日线；点击走势可看图，成分股入口暂不可用。")
        elif excluded:
            result["warnings"].append(f"已排除 {excluded} 个不在当前 MCP 成分股目录中的本地指数，避免误配旧指数或同名板块。")
        if as_of and actual and actual < as_of:
            result["warnings"].append(f"所选截至日期为 {as_of}，可用板块日线截至 {actual}；没有补造所选日期的行情。")
        if any(item["older"] for item in items):
            result["warnings"].append("部分板块缺少当前观察日的日线，涨幅与成交额留空，并保留各自实际日期。")
        if failures:
            result["warnings"].append(f"已排除 {len(failures)} 个无效或正在更新的板块日线文件。")
        if not items:
            result["warnings"].append("所选日期前没有可用板块指数日线，请在通达信下载板块日线后刷新。")
        return result

    def history(self, payload, received_at):
        as_of = validate_as_of(payload.get("asOf"))
        symbol = "".join(tdx.symbol_parts(payload.get("id")))
        root = self.source()
        names, _ = tdx.read_names(root)
        data = {**self.meta(root, as_of, received_at), "id": symbol, "symbol": symbol,
                "name": names.get(symbol, symbol), "rows": [], "dataAsOf": None,
                "status": "unavailable", "amountUnit": "元", "volumeUnit": "原始单位"}
        try:
            data["rows"] = self.rows(root, symbol, payload.get("limit", 250), as_of)
            if data["rows"]:
                data["dataAsOf"] = data["rows"][-1]["date"]
                data["status"] = "ready"
                if as_of and data["dataAsOf"] < as_of:
                    data["warnings"].append(f"所选截至日期为 {as_of}，此板块日线实际截至 {data['dataAsOf']}。")
            else:
                data["warnings"].append("所选日期前没有此板块的日线记录。")
        except ValueError as error:
            data["warnings"].append(str(error))
        return data
