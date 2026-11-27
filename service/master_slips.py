"""Dedicated local SQLite database for master-trader delivery slips and study records.

Stored separately in ``masters.sqlite3`` inside the workspace data directory so
study records never mix with the user's real account ledger.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from service import imports as trade_imports
from service import tdx

SCHEMA_VERSION = 1
MAX_MASTERS = 500
MAX_BATCH_ROWS = 10_000
MAX_IMPORT_BYTES = 12 * 1024 * 1024
MAX_REPORT_BYTES = 500_000
REPORT_KINDS = {"playbook", "trade_deep_dive", "non_realtime_plan", "self_compare"}
TRADE_SORTS = {"tradeDate", "code", "name", "side", "quantity", "price", "amount", "createdAt"}
BUY_TOKENS = {
    "buy", "b", "买", "买入", "证券买入", "股票买入", "普通买入", "建仓", "加仓",
    "融资买入", "担保品买入", "担保物买入", "买开", "申购",
}
SELL_TOKENS = {
    "sell", "s", "卖", "卖出", "证券卖出", "股票卖出", "普通卖出", "减仓", "清仓",
    "卖券还款", "担保品卖出", "担保物卖出", "融券卖出", "卖平",
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _id() -> str:
    return uuid.uuid4().hex


def _text(value, label: str, limit: int = 2000, *, required: bool = False) -> str:
    text = str(value if value is not None else "").strip()
    if "\0" in text:
        raise ValueError(f"{label}包含非法空字符")
    if required and not text:
        raise ValueError(f"请填写{label}")
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 字")
    return text


def _optional_float(value, label: str, *, min_val: float | None = None, max_val: float | None = None):
    if value in (None, "", "--", "—"):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数字")
    text = unicodedata.normalize("NFKC", str(value)).strip().replace(",", "").replace("，", "")
    text = re.sub(r"^[¥￥$]|[%％元]$", "", text).strip()
    try:
        num = float(Decimal(text))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}不是有效数字") from None
    if not math.isfinite(num):
        raise ValueError(f"{label}不是有效数字")
    if min_val is not None and num < min_val:
        raise ValueError(f"{label}不能小于 {min_val}")
    if max_val is not None and num > max_val:
        raise ValueError(f"{label}不能大于 {max_val}")
    return round(num, 6)


def _date(value, label: str = "成交日期") -> str:
    raw = str(value or "").strip().lstrip("'")
    if not raw:
        raise ValueError(f"{label}缺失")
    raw = raw.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    raw = re.split(r"[ T]", raw)[0]
    if re.fullmatch(r"\d{8}", raw):
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        parsed = dt.date.fromisoformat(raw)
        if not 1990 <= parsed.year <= 2100:
            raise ValueError()
        return parsed.isoformat()
    except ValueError:
        raise ValueError(f"{label}无效，请使用 YYYY-MM-DD 格式") from None


def _time(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if " " in raw or "T" in raw:
        parts = re.split(r"[ T]", raw)
        if len(parts) > 1:
            raw = parts[-1]
    raw = raw.replace("：", ":")
    if re.fullmatch(r"\d{6}", raw):
        raw = f"{raw[:2]}:{raw[2:4]}:{raw[4:6]}"
    elif re.fullmatch(r"\d{4}", raw):
        raw = f"{raw[:2]}:{raw[2:4]}:00"
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if not match:
        raise ValueError("成交时间格式无效，请使用 HH:MM 或 HH:MM:SS")
    hour, minute, second = int(match[1]), int(match[2]), int(match[3] or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError("成交时间超出有效范围")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _side(value) -> str:
    token = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    token = re.sub(r"[\s_\-()（）]", "", token)
    if token in BUY_TOKENS:
        return "buy"
    if token in SELL_TOKENS:
        return "sell"
    if "买" in token and "卖" not in token:
        return "buy"
    if "卖" in token and "买" not in token:
        return "sell"
    raise ValueError("买卖方向无效，请确认为买入或卖出")


def infer_symbol(code_or_symbol: str) -> tuple[str, str]:
    raw = unicodedata.normalize("NFKC", str(code_or_symbol or "")).strip().lower()
    if not raw:
        return "", ""
    match = re.fullmatch(r"(?:(sh|sz|bj)[.:\s-]?)?(\d{1,6})(?:[.:\s-]?(sh|sz|bj))?", raw)
    if not match or (match[1] and match[3] and match[1] != match[3]):
        raise ValueError("证券代码格式无效，请填写 6 位数字或 sh/sz/bj + 6 位代码")
    code = match[2].zfill(6)
    market = match[1] or match[3]
    if not market:
        if code.startswith(("43", "83", "87", "88", "92", "4", "8")):
            market = "bj"
        elif code.startswith(("5", "6", "9")):
            market = "sh"
        else:
            market = "sz"
    return f"{market}{code}", code


def _clean_stock_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or "")).strip().upper()
    return re.sub(r"[\s·•\-_]", "", text)


class MasterSlipsService:
    def __init__(self, owner):
        self.owner = owner
        self.directory = Path(owner.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "masters.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("大佬交割单数据库损坏，原文件已保留")
            self._init_schema()
        except Exception:
            self.db.close()
            raise

    def _init_schema(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("大佬交割单数据库来自更新版本，请升级软件后打开")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS masters (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                style_tag TEXT NOT NULL DEFAULT '',
                initial_capital REAL,
                source_desc TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS import_batches (
                id TEXT PRIMARY KEY,
                master_id TEXT NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                input_hash TEXT NOT NULL DEFAULT '',
                image_count INTEGER NOT NULL DEFAULT 0,
                row_count INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_batches_master ON import_batches(master_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS master_trades (
                id TEXT PRIMARY KEY,
                master_id TEXT NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
                batch_id TEXT REFERENCES import_batches(id) ON DELETE SET NULL,
                trade_date TEXT NOT NULL,
                trade_time TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL,
                symbol TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                fees REAL NOT NULL DEFAULT 0,
                position_ratio REAL,
                account_equity REAL,
                external_id TEXT NOT NULL DEFAULT '',
                import_key TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_master_trades_date
                ON master_trades(master_id, trade_date, trade_time, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_master_trades_import_key
                ON master_trades(master_id, import_key) WHERE import_key <> '';
            CREATE TABLE IF NOT EXISTS master_analysis_cache (
                master_id TEXT PRIMARY KEY REFERENCES masters(id) ON DELETE CASCADE,
                trades_digest TEXT NOT NULL,
                market_revision TEXT NOT NULL,
                result_json TEXT NOT NULL,
                computed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS master_study_reports (
                id TEXT PRIMARY KEY,
                master_id TEXT NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_master_reports
                ON master_study_reports(master_id, created_at DESC);
        """)
        self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.db.commit()

    def close(self):
        self.db.close()

    def invalidate_analysis(self, master_id: str | None = None):
        with self.db:
            if master_id:
                self.db.execute("DELETE FROM master_analysis_cache WHERE master_id=?", (master_id,))
            else:
                self.db.execute("DELETE FROM master_analysis_cache")

    def dispatch(self, method: str, payload: dict | None = None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise ValueError("参数格式无效")
        routes = {
            "market.masters.list": lambda: self.list_masters(),
            "market.masters.save": lambda: self.save_master(payload),
            "market.masters.delete": lambda: self.delete_master(payload),
            "market.masters.trades.list": lambda: self.list_trades(payload),
            "market.masters.trades.save": lambda: self.save_trade(payload),
            "market.masters.trades.delete": lambda: self.delete_trades(payload),
            "market.masters.import.parse": lambda: self.parse_import(payload),
            "market.masters.import.commit": lambda: self.commit_import(payload),
            "market.masters.analyze": lambda: self.analyze_master(payload),
            "market.masters.reports.list": lambda: self.list_reports(payload),
            "market.masters.reports.save": lambda: self.save_report(payload),
            "market.masters.reports.delete": lambda: self.delete_report(payload),
            "market.masters.export": lambda: self.export_master(payload),
            "market.masters.importDb": lambda: self.import_master_db(payload),
            "market.masters.presets.list": lambda: self.list_presets(),
            "market.masters.presets.install": lambda: self.install_presets(payload),
            "market.masters.advise": lambda: self.advise_master(payload),
            "market.masters.backtest": lambda: self.backtest_master(payload),
            "market.masters.stockMarkers": lambda: self.stock_markers(payload),
            "market.masters.stockSimulate": lambda: self.stock_simulate(payload),
        }
        if method not in routes:
            raise ValueError("不支持的大佬交割单操作")
        return routes[method]()

    # ── Security catalogue lookup ─────────────────────────────────────────────

    def _catalogue_maps(self):
        by_symbol: dict[str, tuple[str, str, str]] = {}
        by_code: dict[str, list[tuple[str, str, str]]] = {}
        by_name: dict[str, list[tuple[str, str, str]]] = {}
        try:
            rows = self.owner.db.execute(
                "SELECT symbol, code, name, kind FROM securities WHERE kind='stock'"
            ).fetchall()
            for row in rows:
                entry = (row["symbol"], row["code"], row["name"])
                by_symbol[row["symbol"]] = entry
                by_code.setdefault(row["code"], []).append(entry)
                clean = _clean_stock_name(row["name"])
                if clean and clean != "名称待补全":
                    by_name.setdefault(clean, []).append(entry)
        except Exception:
            pass
        if not by_symbol:
            chosen = self.owner._get("path")
            if chosen:
                try:
                    names, _ = tdx.read_names(tdx.normalize_directory(chosen))
                    for sym, stock_name in names.items():
                        if not tdx.SYMBOL.fullmatch(sym):
                            continue
                        market, code = sym[:2], sym[2:]
                        if tdx.security_kind(market, code) != "stock":
                            continue
                        entry = (sym, code, stock_name)
                        by_symbol[sym] = entry
                        by_code.setdefault(code, []).append(entry)
                        clean = _clean_stock_name(stock_name)
                        if clean:
                            by_name.setdefault(clean, []).append(entry)
                except Exception:
                    pass
        return by_symbol, by_code, by_name

    def _resolve_security(self, raw_code, raw_name, maps=None):
        by_symbol, by_code, by_name = maps if maps is not None else self._catalogue_maps()
        warnings = []
        name = _text(raw_name, "证券名称", 80)
        code_text = _text(raw_code, "证券代码", 32)
        symbol, code = "", ""

        if code_text:
            symbol, code = infer_symbol(code_text)
            if symbol in by_symbol:
                _, _, cat_name = by_symbol[symbol]
                if not name and cat_name and cat_name != "名称待补全":
                    name = cat_name
            elif code in by_code and len(by_code[code]) == 1:
                symbol, code, cat_name = by_code[code][0]
                if not name and cat_name and cat_name != "名称待补全":
                    name = cat_name
        elif name:
            clean = _clean_stock_name(name)
            candidates = by_name.get(clean, [])
            if not candidates and clean.startswith(("*ST", "ST")):
                candidates = by_name.get(clean.lstrip("*").removeprefix("ST"), [])
            if not candidates:
                # Try matching with ST prefix in catalogue
                candidates = by_name.get("ST" + clean, []) + by_name.get("*ST" + clean, [])
            if len(candidates) == 1:
                symbol, code, cat_name = candidates[0]
                name = cat_name or name
                warnings.append(f"已按证券名称自动匹配代码 {code}")
            elif len(candidates) > 1:
                warnings.append(f"证券名称「{name}」匹配到多个代码，请手动填写 6 位代码")
            else:
                warnings.append(f"未在本地行情索引中找到「{name}」，请手动填写 6 位代码")
        return symbol, code, name, warnings

    # ── Master Profile CRUD ───────────────────────────────────────────────────

    def _require_master(self, master_id: str) -> sqlite3.Row:
        mid = _text(master_id, "大佬档案编号", 80, required=True)
        row = self.db.execute("SELECT * FROM masters WHERE id=?", (mid,)).fetchone()
        if not row:
            raise ValueError("未找到该大佬档案")
        return row

    def _format_master(self, row: sqlite3.Row) -> dict:
        mid = row["id"]
        stats = self.db.execute(
            """SELECT COUNT(*) AS cnt,
                      MIN(trade_date) AS first_date,
                      MAX(trade_date) AS last_date,
                      COUNT(DISTINCT symbol) AS stock_cnt,
                      SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) AS buy_cnt,
                      SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) AS sell_cnt,
                      SUM(amount) AS total_turnover
               FROM master_trades WHERE master_id=?""",
            (mid,),
        ).fetchone()
        batch_cnt = self.db.execute(
            "SELECT COUNT(*) FROM import_batches WHERE master_id=?", (mid,)
        ).fetchone()[0]
        report_cnt = self.db.execute(
            "SELECT COUNT(*) FROM master_study_reports WHERE master_id=?", (mid,)
        ).fetchone()[0]
        cache = self.db.execute(
            "SELECT computed_at FROM master_analysis_cache WHERE master_id=?", (mid,)
        ).fetchone()
        return {
            "id": mid,
            "name": row["name"],
            "styleTag": row["style_tag"],
            "initialCapital": row["initial_capital"],
            "sourceDesc": row["source_desc"],
            "notes": row["notes"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "stats": {
                "tradeCount": stats["cnt"] or 0,
                "buyCount": stats["buy_cnt"] or 0,
                "sellCount": stats["sell_cnt"] or 0,
                "uniqueStocks": stats["stock_cnt"] or 0,
                "firstDate": stats["first_date"],
                "lastDate": stats["last_date"],
                "totalTurnover": round(float(stats["total_turnover"] or 0), 2),
                "batchCount": batch_cnt,
                "reportCount": report_cnt,
                "lastAnalyzedAt": cache["computed_at"] if cache else None,
            },
        }

    def list_masters(self) -> dict:
        rows = self.db.execute("SELECT * FROM masters ORDER BY updated_at DESC, created_at DESC").fetchall()
        return {
            "items": [self._format_master(row) for row in rows],
            "databasePath": "masters.sqlite3",
        }

    def save_master(self, payload: dict) -> dict:
        mid = _text(payload.get("id"), "大佬档案编号", 80)
        name = _text(payload.get("name"), "大佬名称", 80, required=True)
        style_tag = _text(payload.get("styleTag"), "风格标签", 120)
        initial_capital = _optional_float(payload.get("initialCapital"), "参考本金", min_val=1000, max_val=1e13)
        source_desc = _text(payload.get("sourceDesc"), "数据来源说明", 500)
        notes = _text(payload.get("notes"), "学习笔记", 20000)
        now = _now()
        with self.db:
            if mid:
                existing = self._require_master(mid)
                capital_changed = existing["initial_capital"] != initial_capital
                self.db.execute(
                    """UPDATE masters
                       SET name=?, style_tag=?, initial_capital=?, source_desc=?, notes=?, updated_at=?
                       WHERE id=?""",
                    (name, style_tag, initial_capital, source_desc, notes, now, mid),
                )
                if capital_changed:
                    self.db.execute("DELETE FROM master_analysis_cache WHERE master_id=?", (mid,))
            else:
                count = self.db.execute("SELECT COUNT(*) FROM masters").fetchone()[0]
                if count >= MAX_MASTERS:
                    raise ValueError(f"最多保存 {MAX_MASTERS} 位大佬档案")
                mid = _id()
                self.db.execute(
                    """INSERT INTO masters(id, name, style_tag, initial_capital, source_desc, notes, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (mid, name, style_tag, initial_capital, source_desc, notes, now, now),
                )
        row = self._require_master(mid)
        return {"master": self._format_master(row)}

    def delete_master(self, payload: dict) -> dict:
        mid = _text(payload.get("id") or payload.get("masterId"), "大佬档案编号", 80, required=True)
        with self.db:
            cur = self.db.execute("DELETE FROM masters WHERE id=?", (mid,))
        return {"deleted": cur.rowcount > 0, "id": mid}

    # ── Trade CRUD & Listing ──────────────────────────────────────────────────

    @staticmethod
    def _format_trade(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "masterId": row["master_id"],
            "batchId": row["batch_id"],
            "tradeDate": row["trade_date"],
            "tradeTime": row["trade_time"],
            "side": row["side"],
            "symbol": row["symbol"],
            "code": row["code"],
            "name": row["name"],
            "quantity": row["quantity"],
            "price": row["price"],
            "amount": row["amount"],
            "fees": row["fees"],
            "positionRatio": row["position_ratio"],
            "accountEquity": row["account_equity"],
            "externalId": row["external_id"],
            "importKey": row["import_key"],
            "note": row["note"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _validate_trade_row(self, raw: dict, maps=None) -> tuple[dict, list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = list(raw.get("warnings") or []) if isinstance(raw.get("warnings"), list) else []
        warnings = [str(w)[:300] for w in warnings if w]

        trade_date = ""
        try:
            trade_date = _date(raw.get("tradeDate") or raw.get("date"))
        except ValueError as exc:
            errors.append(str(exc))

        trade_time = ""
        try:
            trade_time = _time(raw.get("tradeTime") or raw.get("time"))
        except ValueError as exc:
            errors.append(str(exc))

        side = ""
        try:
            side = _side(raw.get("side") or raw.get("type") or raw.get("direction"))
        except ValueError as exc:
            errors.append(str(exc))

        symbol, code, name = "", "", ""
        try:
            symbol, code, name, sec_warnings = self._resolve_security(
                raw.get("symbol") or raw.get("code"),
                raw.get("name"),
                maps=maps,
            )
            warnings.extend(sec_warnings)
            if not code:
                errors.append("缺少有效的 6 位证券代码")
        except ValueError as exc:
            errors.append(str(exc))

        quantity = None
        price = None
        amount = None
        try:
            quantity = _optional_float(raw.get("quantity"), "成交数量", min_val=0.0001, max_val=1e12)
            if quantity is None:
                errors.append("成交数量缺失")
        except ValueError as exc:
            errors.append(str(exc))

        try:
            price = _optional_float(raw.get("price"), "成交均价", min_val=0.0001, max_val=1e8)
            if price is None:
                errors.append("成交均价缺失")
        except ValueError as exc:
            errors.append(str(exc))

        try:
            amount = _optional_float(raw.get("amount"), "成交金额", min_val=0.01, max_val=1e14)
        except ValueError as exc:
            errors.append(str(exc))

        if quantity is not None and price is not None:
            calc = round(quantity * price, 2)
            if amount is None:
                amount = calc
            elif abs(amount - calc) > max(1.0, calc * 0.02):
                warnings.append(f"成交金额({amount:.2f})与 数量×价格({calc:.2f})存在差异，请核对")
        elif amount is not None and price is not None and quantity is None:
            quantity = round(amount / price, 2)
            warnings.append("成交数量已按 金额÷价格 推算，请核对")
        elif amount is None:
            amount = 0.0

        fees = 0.0
        try:
            fee_val = raw.get("fees")
            if fee_val in (None, ""):
                parts = [
                    _optional_float(raw.get(k), k, min_val=0, max_val=1e10)
                    for k in ("commission", "stampTax", "transferFee", "otherFees", "totalFees")
                ]
                if parts[-1] is not None:
                    fees = parts[-1]
                else:
                    fees = round(sum(p for p in parts[:-1] if p is not None), 2)
            else:
                fees = round(_optional_float(fee_val, "手续费", min_val=0, max_val=1e10) or 0.0, 2)
        except ValueError as exc:
            errors.append(str(exc))

        position_ratio = None
        try:
            position_ratio = _optional_float(raw.get("positionRatio"), "仓位占比", min_val=0, max_val=100)
        except ValueError as exc:
            errors.append(str(exc))

        account_equity = None
        try:
            account_equity = _optional_float(raw.get("accountEquity"), "参考总资产", min_val=100, max_val=1e14)
        except ValueError as exc:
            errors.append(str(exc))

        external_id = _text(raw.get("externalId"), "成交编号", 120)
        import_key = _text(raw.get("importKey"), "导入键", 200)
        note = _text(raw.get("note") or raw.get("reason"), "备注", 500)

        cleaned = {
            "tradeDate": trade_date,
            "tradeTime": trade_time,
            "side": side,
            "symbol": symbol,
            "code": code,
            "name": name or code,
            "quantity": round(quantity, 4) if quantity is not None else None,
            "price": round(price, 4) if price is not None else None,
            "amount": round(amount, 2) if amount is not None else None,
            "fees": round(fees, 2),
            "positionRatio": position_ratio,
            "accountEquity": account_equity,
            "externalId": external_id,
            "importKey": import_key,
            "note": note,
        }
        return cleaned, errors, list(dict.fromkeys(warnings))

    def list_trades(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        query = _text(payload.get("query"), "搜索关键词", 100).lower()
        side = _text(payload.get("side"), "方向筛选", 10).lower()
        if side and side not in ("all", "buy", "sell"):
            raise ValueError("方向筛选无效")
        date_from = _date(payload["dateFrom"], "起始日期") if payload.get("dateFrom") else ""
        date_to = _date(payload["dateTo"], "结束日期") if payload.get("dateTo") else ""
        sort = payload.get("sort") or "tradeDate"
        if sort not in TRADE_SORTS:
            raise ValueError("排序字段无效")
        order = "ASC" if str(payload.get("order") or "desc").lower() == "asc" else "DESC"
        offset = int(payload.get("offset") or 0)
        limit = min(500, max(1, int(payload.get("limit") or 100)))
        col_map = {
            "tradeDate": "trade_date",
            "code": "code",
            "name": "name",
            "side": "side",
            "quantity": "quantity",
            "price": "price",
            "amount": "amount",
            "createdAt": "created_at",
        }
        clauses = ["master_id=?"]
        params: list = [mid]
        if side in ("buy", "sell"):
            clauses.append("side=?")
            params.append(side)
        if date_from:
            clauses.append("trade_date>=?")
            params.append(date_from)
        if date_to:
            clauses.append("trade_date<=?")
            params.append(date_to)
        if query:
            clauses.append("(LOWER(code) LIKE ? OR LOWER(name) LIKE ? OR LOWER(symbol) LIKE ? OR LOWER(note) LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like, like])
        where = " AND ".join(clauses)
        total = self.db.execute(f"SELECT COUNT(*) FROM master_trades WHERE {where}", params).fetchone()[0]
        rows = self.db.execute(
            f"SELECT * FROM master_trades WHERE {where} ORDER BY {col_map[sort]} {order}, trade_time {order}, created_at {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        batches = self.db.execute(
            "SELECT * FROM import_batches WHERE master_id=? ORDER BY created_at DESC LIMIT 50",
            (mid,),
        ).fetchall()
        return {
            "masterId": mid,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [self._format_trade(r) for r in rows],
            "batches": [
                {
                    "id": b["id"],
                    "source": b["source"],
                    "inputHash": b["input_hash"],
                    "imageCount": b["image_count"],
                    "rowCount": b["row_count"],
                    "meta": json.loads(b["meta_json"] or "{}"),
                    "createdAt": b["created_at"],
                }
                for b in batches
            ],
        }

    def save_trade(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        tid = _text(payload.get("id"), "成交编号", 80)
        cleaned, errors, warnings = self._validate_trade_row(payload)
        if errors:
            raise ValueError("；".join(errors))
        now = _now()
        with self.db:
            if tid:
                existing = self.db.execute(
                    "SELECT id FROM master_trades WHERE id=? AND master_id=?", (tid, mid)
                ).fetchone()
                if not existing:
                    raise ValueError("未找到要修改的成交记录")
                self.db.execute(
                    """UPDATE master_trades
                       SET trade_date=?, trade_time=?, side=?, symbol=?, code=?, name=?,
                           quantity=?, price=?, amount=?, fees=?, position_ratio=?, account_equity=?,
                           external_id=?, note=?, updated_at=?
                       WHERE id=? AND master_id=?""",
                    (
                        cleaned["tradeDate"], cleaned["tradeTime"], cleaned["side"],
                        cleaned["symbol"], cleaned["code"], cleaned["name"],
                        cleaned["quantity"], cleaned["price"], cleaned["amount"], cleaned["fees"],
                        cleaned["positionRatio"], cleaned["accountEquity"],
                        cleaned["externalId"], cleaned["note"], now, tid, mid,
                    ),
                )
            else:
                tid = _id()
                self.db.execute(
                    """INSERT INTO master_trades(
                           id, master_id, batch_id, trade_date, trade_time, side, symbol, code, name,
                           quantity, price, amount, fees, position_ratio, account_equity,
                           external_id, import_key, note, created_at, updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tid, mid, None, cleaned["tradeDate"], cleaned["tradeTime"], cleaned["side"],
                        cleaned["symbol"], cleaned["code"], cleaned["name"],
                        cleaned["quantity"], cleaned["price"], cleaned["amount"], cleaned["fees"],
                        cleaned["positionRatio"], cleaned["accountEquity"],
                        cleaned["externalId"], "", cleaned["note"], now, now,
                    ),
                )
            self.db.execute("UPDATE masters SET updated_at=? WHERE id=?", (now, mid))
            self.db.execute("DELETE FROM master_analysis_cache WHERE master_id=?", (mid,))
        row = self.db.execute("SELECT * FROM master_trades WHERE id=?", (tid,)).fetchone()
        return {"trade": self._format_trade(row), "warnings": warnings}

    def delete_trades(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        batch_id = _text(payload.get("batchId"), "批次编号", 80)
        ids = payload.get("ids")
        deleted = 0
        now = _now()
        with self.db:
            if batch_id:
                cur = self.db.execute(
                    "DELETE FROM master_trades WHERE master_id=? AND batch_id=?", (mid, batch_id)
                )
                deleted = cur.rowcount
                self.db.execute("DELETE FROM import_batches WHERE master_id=? AND id=?", (mid, batch_id))
            elif isinstance(ids, list) and ids:
                clean_ids = [_text(item, "记录编号", 80, required=True) for item in ids[:1000]]
                placeholders = ",".join("?" for _ in clean_ids)
                cur = self.db.execute(
                    f"DELETE FROM master_trades WHERE master_id=? AND id IN ({placeholders})",
                    [mid, *clean_ids],
                )
                deleted = cur.rowcount
            else:
                raise ValueError("请选择要删除的记录或导入批次")
            self.db.execute("UPDATE masters SET updated_at=? WHERE id=?", (now, mid))
            self.db.execute("DELETE FROM master_analysis_cache WHERE master_id=?", (mid,))
        return {"deleted": deleted}

    # ── Import Parsing & Atomic Commit ────────────────────────────────────────

    def _existing_fingerprints(self, master_id: str) -> tuple[set[str], set[tuple]]:
        rows = self.db.execute(
            "SELECT import_key, trade_date, trade_time, side, code, quantity, price FROM master_trades WHERE master_id=?",
            (master_id,),
        ).fetchall()
        keys = {r["import_key"] for r in rows if r["import_key"]}
        sigs = {
            (r["trade_date"], r["trade_time"], r["side"], r["code"], round(r["quantity"], 4), round(r["price"], 4))
            for r in rows
        }
        return keys, sigs

    def parse_import(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        raw_rows = payload.get("rows")
        warnings: list[str] = []
        source = _text(payload.get("source") or "ocr", "导入来源", 40)

        if raw_rows is None:
            filename = _text(payload.get("filename"), "文件名", 260, required=True)
            content_b64 = payload.get("contentBase64")
            if not isinstance(content_b64, str) or not content_b64:
                raise ValueError("缺少导入文件内容")
            try:
                raw_bytes = base64.b64decode(content_b64, validate=True)
            except Exception:
                raise ValueError("文件内容编码无效") from None
            if len(raw_bytes) > MAX_IMPORT_BYTES:
                raise ValueError("导入文件不能超过 12 MiB")
            ext = Path(filename).suffix.lower()
            if ext in (".csv", ".xlsx", ".json"):
                parsed = trade_imports.parse_import(filename, content_b64, kind="trades")
                warnings.extend(parsed.get("warnings") or [])
                raw_rows = []
                for item in parsed.get("rows") or []:
                    rec = item.get("record") or {}
                    if rec.get("type") not in ("buy", "sell", None, ""):
                        continue
                    raw_rows.append({
                        "tradeDate": rec.get("date"),
                        "tradeTime": rec.get("time"),
                        "side": rec.get("type"),
                        "code": rec.get("code"),
                        "name": rec.get("name"),
                        "quantity": rec.get("quantity"),
                        "price": rec.get("price"),
                        "amount": rec.get("amount"),
                        "fees": rec.get("fees"),
                        "commission": rec.get("commission"),
                        "stampTax": rec.get("stampTax"),
                        "transferFee": rec.get("transferFee"),
                        "otherFees": rec.get("otherFees"),
                        "externalId": rec.get("externalId"),
                        "importKey": rec.get("importKey"),
                        "note": rec.get("reason"),
                        "warnings": item.get("warnings") or [],
                    })
                source = ext.lstrip(".")
            else:
                raise ValueError("仅支持 CSV、XLSX、JSON 或交割单图片识别")

        if not isinstance(raw_rows, list) or len(raw_rows) > MAX_BATCH_ROWS:
            raise ValueError(f"单批最多核对 {MAX_BATCH_ROWS} 笔成交记录")

        maps = self._catalogue_maps()
        existing_keys, existing_sigs = self._existing_fingerprints(mid)
        preview_rows = []
        valid_count = 0
        duplicate_count = 0
        for index, raw in enumerate(raw_rows, 1):
            if not isinstance(raw, dict):
                raw = {}
            cleaned, errors, row_warnings = self._validate_trade_row(raw, maps=maps)
            sig = (
                cleaned["tradeDate"],
                cleaned["tradeTime"],
                cleaned["side"],
                cleaned["code"],
                round(cleaned["quantity"], 4) if cleaned["quantity"] is not None else None,
                round(cleaned["price"], 4) if cleaned["price"] is not None else None,
            )
            dup = bool((cleaned["importKey"] and cleaned["importKey"] in existing_keys) or (not errors and sig in existing_sigs))
            if dup:
                duplicate_count += 1
                row_warnings.append("该大佬交割单库中已存在同日同价同数量记录，默认取消勾选以避免重复入库")
            if not errors:
                valid_count += 1
            preview_rows.append({
                "rowNumber": index,
                "record": cleaned,
                "errors": errors,
                "warnings": list(dict.fromkeys(row_warnings)),
                "duplicate": dup,
                "selected": (not errors) and (not dup),
            })

        return {
            "masterId": mid,
            "source": source,
            "rows": preview_rows,
            "summary": {
                "total": len(preview_rows),
                "valid": valid_count,
                "invalid": len(preview_rows) - valid_count,
                "duplicates": duplicate_count,
            },
            "warnings": list(dict.fromkeys(warnings)),
        }

    def commit_import(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError("没有可写入的成交记录")
        if len(rows) > MAX_BATCH_ROWS:
            raise ValueError(f"单次最多导入 {MAX_BATCH_ROWS} 笔成交记录")
        source = _text(payload.get("source") or "ocr", "导入来源", 40)
        input_hash = _text(payload.get("inputHash"), "批次指纹", 128)
        image_count = max(0, min(100, int(payload.get("imageCount") or 0)))
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}

        maps = self._catalogue_maps()
        validated = []
        for idx, raw in enumerate(rows, 1):
            cleaned, errors, _ = self._validate_trade_row(raw, maps=maps)
            if errors:
                raise ValueError(f"第 {idx} 行校验未通过：{'；'.join(errors)}")
            validated.append(cleaned)

        now = _now()
        batch_id = _id()
        inserted = 0
        skipped = 0
        with self.db:
            self.db.execute(
                """INSERT INTO import_batches(id, master_id, source, input_hash, image_count, row_count, meta_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (batch_id, mid, source, input_hash, image_count, 0, _dump(meta), now),
            )
            for rec in validated:
                tid = _id()
                import_key = rec["importKey"]
                if import_key:
                    exists = self.db.execute(
                        "SELECT 1 FROM master_trades WHERE master_id=? AND import_key=?",
                        (mid, import_key),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue
                self.db.execute(
                    """INSERT INTO master_trades(
                           id, master_id, batch_id, trade_date, trade_time, side, symbol, code, name,
                           quantity, price, amount, fees, position_ratio, account_equity,
                           external_id, import_key, note, created_at, updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tid, mid, batch_id, rec["tradeDate"], rec["tradeTime"], rec["side"],
                        rec["symbol"], rec["code"], rec["name"],
                        rec["quantity"], rec["price"], rec["amount"], rec["fees"],
                        rec["positionRatio"], rec["accountEquity"],
                        rec["externalId"], import_key, rec["note"], now, now,
                    ),
                )
                inserted += 1
            if inserted == 0:
                self.db.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))
            else:
                self.db.execute("UPDATE import_batches SET row_count=? WHERE id=?", (inserted, batch_id))
                self.db.execute("UPDATE masters SET updated_at=? WHERE id=?", (now, mid))
                self.db.execute("DELETE FROM master_analysis_cache WHERE master_id=?", (mid,))
        return {
            "masterId": mid,
            "batchId": batch_id if inserted else None,
            "inserted": inserted,
            "skipped": skipped,
            "master": self._format_master(self._require_master(mid)),
        }

    # ── Quantitative Market-Aligned Reverse-Engineering Analysis ──────────────

    def _trades_digest(self, master: sqlite3.Row, trades: list[dict]) -> str:
        payload = {
            "initialCapital": master["initial_capital"],
            "trades": [
                (
                    t["id"], t["tradeDate"], t["tradeTime"], t["side"],
                    t["symbol"], t["quantity"], t["price"], t["amount"],
                    t["fees"], t["positionRatio"], t["accountEquity"],
                )
                for t in trades
            ],
        }
        return hashlib.sha256(_dump(payload).encode("utf-8")).hexdigest()

    def analyze_master(self, payload: dict) -> dict:
        from service.master_analyzer import analyze_master_trades

        master_row = self._require_master(payload.get("masterId"))
        mid = master_row["id"]
        refresh = bool(payload.get("refresh", False))
        self_summary = payload.get("selfSummary") if isinstance(payload.get("selfSummary"), dict) else None

        rows = self.db.execute(
            "SELECT * FROM master_trades WHERE master_id=? ORDER BY trade_date ASC, trade_time ASC, created_at ASC",
            (mid,),
        ).fetchall()
        trades = [self._format_trade(r) for r in rows]
        digest = self._trades_digest(master_row, trades)
        market_rev = self.owner.source_revision()

        cached_result = None
        if not refresh:
            cache = self.db.execute(
                "SELECT trades_digest, market_revision, result_json, computed_at FROM master_analysis_cache WHERE master_id=?",
                (mid,),
            ).fetchone()
            if cache and cache["trades_digest"] == digest and cache["market_revision"] == market_rev:
                cached_result = json.loads(cache["result_json"])
                cached_result["cached"] = True

        if cached_result is None:
            result = analyze_master_trades(
                self.owner,
                self._format_master(master_row),
                trades,
                progress=getattr(self.owner, "progress", None),
            )
            now = _now()
            result["computedAt"] = now
            result["cached"] = False
            with self.db:
                self.db.execute(
                    """INSERT OR REPLACE INTO master_analysis_cache(master_id, trades_digest, market_revision, result_json, computed_at)
                       VALUES (?,?,?,?,?)""",
                    (mid, digest, market_rev, _dump(result), now),
                )
        else:
            result = cached_result

        if self_summary is not None:
            from service.master_analyzer import compare_with_self_account
            result = dict(result)
            result["selfComparison"] = compare_with_self_account(result, self_summary)
        return result

    # ── Study Reports (AI Playbooks & Coaching Notes) ─────────────────────────

    def list_reports(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        rows = self.db.execute(
            "SELECT * FROM master_study_reports WHERE master_id=? ORDER BY created_at DESC LIMIT 100",
            (mid,),
        ).fetchall()
        return {
            "masterId": mid,
            "items": [
                {
                    "id": r["id"],
                    "masterId": r["master_id"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "content": r["content"],
                    "model": r["model"],
                    "meta": json.loads(r["meta_json"] or "{}"),
                    "createdAt": r["created_at"],
                }
                for r in rows
            ],
        }

    def save_report(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        kind = _text(payload.get("kind") or "playbook", "报告类型", 40)
        if kind not in REPORT_KINDS:
            raise ValueError("不支持的研究报告类型")
        title = _text(payload.get("title"), "报告标题", 200, required=True)
        content = _text(payload.get("content"), "报告正文", MAX_REPORT_BYTES, required=True)
        model = _text(payload.get("model"), "模型名称", 120)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        rid = _id()
        now = _now()
        with self.db:
            self.db.execute(
                """INSERT INTO master_study_reports(id, master_id, kind, title, content, model, meta_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (rid, mid, kind, title, content, model, _dump(meta), now),
            )
            self.db.execute("UPDATE masters SET updated_at=? WHERE id=?", (now, mid))
        return {
            "report": {
                "id": rid,
                "masterId": mid,
                "kind": kind,
                "title": title,
                "content": content,
                "model": model,
                "meta": meta,
                "createdAt": now,
            }
        }

    def delete_report(self, payload: dict) -> dict:
        mid = self._require_master(payload.get("masterId"))["id"]
        rid = _text(payload.get("id") or payload.get("reportId"), "报告编号", 80, required=True)
        with self.db:
            cur = self.db.execute(
                "DELETE FROM master_study_reports WHERE master_id=? AND id=?", (mid, rid)
            )
        return {"deleted": cur.rowcount > 0, "id": rid}

    # ── Export / Import Complete Master Database Bundle ───────────────────────

    def export_master(self, payload: dict) -> dict:
        master_row = self._require_master(payload.get("masterId"))
        mid = master_row["id"]
        trades = [
            self._format_trade(r)
            for r in self.db.execute(
                "SELECT * FROM master_trades WHERE master_id=? ORDER BY trade_date ASC, trade_time ASC, created_at ASC",
                (mid,),
            ).fetchall()
        ]
        reports = self.list_reports({"masterId": mid})["items"]
        return {
            "schema": "a-trade.master-slips.v1",
            "exportedAt": _now(),
            "master": {
                "name": master_row["name"],
                "styleTag": master_row["style_tag"],
                "initialCapital": master_row["initial_capital"],
                "sourceDesc": master_row["source_desc"],
                "notes": master_row["notes"],
            },
            "trades": [
                {
                    k: t[k]
                    for k in (
                        "tradeDate", "tradeTime", "side", "symbol", "code", "name",
                        "quantity", "price", "amount", "fees", "positionRatio",
                        "accountEquity", "externalId", "note",
                    )
                }
                for t in trades
            ],
            "reports": [
                {k: r[k] for k in ("kind", "title", "content", "model", "meta", "createdAt")}
                for r in reports
            ],
        }

    def import_master_db(self, payload: dict) -> dict:
        bundle = payload.get("bundle")
        if not isinstance(bundle, dict) or bundle.get("schema") != "a-trade.master-slips.v1":
            raise ValueError("不是有效的 A-trade 大佬交割单数据库文件")
        master_data = bundle.get("master")
        if not isinstance(master_data, dict):
            raise ValueError("数据库文件缺少大佬档案信息")
        trades_data = bundle.get("trades") or []
        reports_data = bundle.get("reports") or []
        if not isinstance(trades_data, list) or len(trades_data) > MAX_BATCH_ROWS:
            raise ValueError("数据库文件中的成交记录数量无效或超出上限")
        if not isinstance(reports_data, list) or len(reports_data) > 200:
            raise ValueError("数据库文件中的报告数量无效或超出上限")

        created = self.save_master({
            "name": master_data.get("name") or "导入的大佬档案",
            "styleTag": master_data.get("styleTag") or "",
            "initialCapital": master_data.get("initialCapital"),
            "sourceDesc": master_data.get("sourceDesc") or "来自导出的大佬交割单数据库",
            "notes": master_data.get("notes") or "",
        })["master"]
        mid = created["id"]
        inserted_trades = 0
        if trades_data:
            commit_res = self.commit_import({
                "masterId": mid,
                "source": "db-import",
                "rows": trades_data,
                "meta": {"schema": bundle.get("schema"), "exportedAt": bundle.get("exportedAt")},
            })
            inserted_trades = commit_res["inserted"]
        inserted_reports = 0
        for rep in reports_data:
            if not isinstance(rep, dict):
                continue
            kind = rep.get("kind") if rep.get("kind") in REPORT_KINDS else "playbook"
            if rep.get("title") and rep.get("content"):
                self.save_report({
                    "masterId": mid,
                    "kind": kind,
                    "title": rep["title"],
                    "content": rep["content"],
                    "model": rep.get("model") or "",
                    "meta": rep.get("meta") if isinstance(rep.get("meta"), dict) else {},
                })
                inserted_reports += 1
        return {
            "master": self._format_master(self._require_master(mid)),
            "insertedTrades": inserted_trades,
            "insertedReports": inserted_reports,
        }

    # ── Built-in 顽主杯 6-Master Presets, Daily Advisor & Backtest ───────────

    def list_presets(self) -> dict:
        from service.master_fallback_market import load_presets_bundle
        bundle = load_presets_bundle()
        existing_names = {r["name"] for r in self.db.execute("SELECT name FROM masters").fetchall()}
        items = []
        for p in bundle.get("presets") or []:
            m = p.get("master") or {}
            items.append({
                "presetId": p.get("presetId"),
                "strategyKey": p.get("strategyKey"),
                "name": m.get("name"),
                "styleTag": m.get("styleTag"),
                "sourceDesc": m.get("sourceDesc"),
                "tradeCount": len(p.get("trades") or []),
                "reportCount": len(p.get("reports") or []),
                "installed": m.get("name") in existing_names,
            })
        return {"items": items, "total": len(items)}

    def install_presets(self, payload: dict) -> dict:
        from service.master_fallback_market import load_presets_bundle
        bundle = load_presets_bundle()
        presets = bundle.get("presets") or []
        target_id = payload.get("presetId")
        if target_id:
            presets = [p for p in presets if p.get("presetId") == target_id]
            if not presets:
                raise ValueError("未找到指定的内置高手预设")

        installed_masters = []
        total_trades_added = 0
        total_reports_added = 0
        for p in presets:
            mdata = p.get("master") or {}
            mname = mdata.get("name")
            if not mname:
                continue
            row = self.db.execute("SELECT id FROM masters WHERE name=?", (mname,)).fetchone()
            if row:
                mid = row["id"]
                self.save_master({
                    "id": mid,
                    "name": mname,
                    "styleTag": mdata.get("styleTag") or "",
                    "initialCapital": mdata.get("initialCapital"),
                    "sourceDesc": mdata.get("sourceDesc") or "",
                    "notes": mdata.get("notes") or "",
                })
            else:
                mid = self.save_master({
                    "name": mname,
                    "styleTag": mdata.get("styleTag") or "",
                    "initialCapital": mdata.get("initialCapital"),
                    "sourceDesc": mdata.get("sourceDesc") or "",
                    "notes": mdata.get("notes") or "",
                })["master"]["id"]

            pid = p.get("presetId") or mname
            trades_data = p.get("trades") or []
            if trades_data:
                stamped_rows = [
                    {
                        **row,
                        "externalId": row.get("externalId") or f"{pid}:{idx}",
                        "importKey": row.get("importKey") or f"{pid}:{idx}",
                    }
                    for idx, row in enumerate(trades_data, 1)
                ]
                res = self.commit_import({
                    "masterId": mid,
                    "source": "db-import",
                    "rows": stamped_rows,
                    "meta": {"presetId": pid},
                })
                total_trades_added += res["inserted"]

            existing_titles = {
                r["title"]
                for r in self.db.execute("SELECT title FROM master_study_reports WHERE master_id=?", (mid,)).fetchall()
            }
            for rep in p.get("reports") or []:
                title = rep.get("title")
                content = rep.get("content")
                if not title or not content or title in existing_titles:
                    continue
                kind = rep.get("kind") if rep.get("kind") in REPORT_KINDS else "playbook"
                self.save_report({
                    "masterId": mid,
                    "kind": kind,
                    "title": title,
                    "content": content,
                    "model": rep.get("model") or "A-trade 量化实证研究",
                    "meta": rep.get("meta") if isinstance(rep.get("meta"), dict) else {},
                })
                total_reports_added += 1

            installed_masters.append(self._format_master(self._require_master(mid)))

        return {
            "installedCount": len(installed_masters),
            "insertedTrades": total_trades_added,
            "insertedReports": total_reports_added,
            "masters": installed_masters,
        }

    def advise_master(self, payload: dict) -> dict:
        from service.master_advisor import generate_master_advice
        master = None
        if payload.get("masterId"):
            master = self._format_master(self._require_master(payload.get("masterId")))
        return generate_master_advice(
            self.owner,
            master,
            style_key=str(payload.get("styleKey") or ""),
            target_date=payload.get("targetDate") or None,
            positions=payload.get("positions") if isinstance(payload.get("positions"), list) else [],
            account_summary=payload.get("accountSummary") if isinstance(payload.get("accountSummary"), dict) else {},
            overrides=payload,
        )

    def backtest_master(self, payload: dict) -> dict:
        from service.master_backtest import run_master_backtest
        master = None
        if payload.get("masterId"):
            master = self._format_master(self._require_master(payload.get("masterId")))
        return run_master_backtest(
            self.owner,
            master,
            payload,
            progress=getattr(self.owner, "progress", None),
        )

    def stock_markers(self, payload: dict) -> dict:
        from service.master_stock_markers import collect_stock_markers
        return collect_stock_markers(self.directory, payload.get("symbol"))

    def stock_simulate(self, payload: dict) -> dict:
        from service.master_backtest import simulate_single_stock_master
        return simulate_single_stock_master(self.owner, payload)
