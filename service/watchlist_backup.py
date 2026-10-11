"""Portable backups of user watch observations, never a market database dump."""
from __future__ import annotations

import datetime as dt
import json

from service import tdx

FORMAT = "a-trade.watchlist"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
MAX_ITEMS = 5000
STATUSES = {"watching", "planned", "holding", "archived"}
FIELDS = {"symbol", "code", "name", "status", "note", "tags", "createdAt", "updatedAt"}


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("跟踪池备份时间格式无效")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise ValueError("跟踪池备份时间格式无效") from None
    return parsed


def validate(backup):
    if not isinstance(backup, dict) or set(backup) != {"format", "schemaVersion", "exportedAt", "items"} or backup.get("format") != FORMAT or type(backup.get("schemaVersion")) is not int or backup["schemaVersion"] != VERSION:
        raise ValueError("请选择受支持的跟踪池备份，账本和界面备份请使用各自的恢复入口")
    _timestamp(backup["exportedAt"])
    if not isinstance(backup["items"], list) or len(backup["items"]) > MAX_ITEMS:
        raise ValueError("跟踪池备份最多支持 5000 条记录")
    try:
        if len(json.dumps(backup, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
            raise ValueError("跟踪池备份超过 20 MiB 限制")
    except (TypeError, OverflowError):
        raise ValueError("跟踪池备份内容无效") from None
    seen, records = set(), []
    for item in backup["items"]:
        if not isinstance(item, dict) or not FIELDS <= item.keys() or set(item) - FIELDS - {"research"}:
            raise ValueError("跟踪池备份记录字段不完整或含不支持字段")
        market, code = tdx.symbol_parts(item["symbol"])
        symbol = market + code
        if item["symbol"] != symbol or item["code"] != code or symbol in seen:
            raise ValueError("跟踪池备份含重复或不一致的证券标识")
        seen.add(symbol)
        if not isinstance(item["name"], str) or len(item["name"]) > 200 or "\0" in item["name"]:
            raise ValueError("跟踪池备份证券名称无效")
        if not isinstance(item["status"], str) or item["status"] not in STATUSES:
            raise ValueError("跟踪池备份状态无效")
        if not isinstance(item["note"], str) or len(item["note"]) > 2000 or "\0" in item["note"]:
            raise ValueError("跟踪池备份备注最多 2000 字且不能含空字符")
        tags = item["tags"]
        if not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 20 or tag != tag.strip() or "\0" in tag for tag in tags) or len(set(tags)) != len(tags):
            raise ValueError("跟踪池备份标签无效")
        if _timestamp(item["updatedAt"]) < _timestamp(item["createdAt"]):
            raise ValueError("跟踪池备份更新时间早于创建时间")
        # Lazy import avoids the service/backup import cycle while sharing exactly
        # the provenance validation used for ordinary saves and merge imports.
        from service.analysis_service import AnalysisService
        research = AnalysisService._research(item.get("research"))
        records.append((symbol, code, item["name"], item["status"], item["note"], json.dumps(tags, ensure_ascii=False), item["createdAt"], item["updatedAt"],
                        json.dumps(research, ensure_ascii=False, allow_nan=False) if research is not None else None))
    return records


def export(analysis):
    backup = {"format": FORMAT, "schemaVersion": VERSION,
              "exportedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
              "items": analysis.watchlist({})["items"]}
    validate(backup)
    return backup


def dispatch(analysis, method, payload):
    if not isinstance(payload, dict):
        raise ValueError("跟踪池备份操作参数无效")
    if method == "market.watchlist.backup.export":
        if payload:
            raise ValueError("导出跟踪池备份不接受额外参数")
        return export(analysis)
    if method not in {"market.watchlist.backup.preview", "market.watchlist.backup.restore"} or set(payload) != {"backup"}:
        raise ValueError("不支持的跟踪池备份操作")
    records = validate(payload["backup"])
    summary = {"total": len(records), "currentTotal": analysis.owner.db.execute("SELECT count(*) FROM analysis_watchlist").fetchone()[0],
               "archived": sum(record[3] == "archived" for record in records), "exportedAt": payload["backup"]["exportedAt"]}
    if method.endswith(".preview"):
        return summary
    db = analysis.owner.db
    # Validate every record before entering the single replacement transaction.
    # Keep one recovery snapshot in the same database, committed with the replacement.
    with db:
        db.execute("BEGIN IMMEDIATE")
        previous = export(analysis)
        db.execute("CREATE TABLE IF NOT EXISTS analysis_watchlist_previous (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        db.execute("INSERT OR REPLACE INTO analysis_watchlist_previous VALUES(1,?)", (json.dumps(previous, ensure_ascii=False),))
        db.execute("DELETE FROM analysis_watchlist")
        db.executemany("INSERT INTO analysis_watchlist (symbol,code,name,status,note,tags,created_at,updated_at,research) VALUES (?,?,?,?,?,?,?,?,?)", records)
    return {**summary, "restored": True, "previousBackup": previous}
