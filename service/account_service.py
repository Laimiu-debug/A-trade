"""Local account ledger. All mutations replay the ledger in one SQLite transaction."""
from __future__ import annotations

import copy
from bisect import bisect_right
import datetime as dt
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

SCHEMA_VERSION = 1
MONEY = Decimal("0.01")
PRICE = Decimal("0.0001")
QUANTITY = Decimal("0.000001")
ZERO = Decimal(0)
KINDS = {"buy", "sell", "deposit", "withdrawal", "dividend", "fee", "opening_position", "split"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _number(value, label, places=MONEY, *, negative=False):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数字")
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
        if not number.is_finite() or abs(number) > Decimal("100000000000000"):
            raise ValueError(f"{label}超出范围")
        if not negative and number < 0:
            raise ValueError(f"{label}不能为负数")
        return number.quantize(places, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError(f"{label}必须是数字") from None


def _money(value):
    return float(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def _date(value, label="日期"):
    try:
        value = str(value)
        parsed = dt.date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError(f"{label}请使用 YYYY-MM-DD 格式") from None


def _text(value, label, limit=20000):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ValueError(f"{label}内容过长")
    return value


def _id():
    return uuid.uuid4().hex


def _code(value):
    code = _text(value, "证券代码", 32).upper()
    if not code:
        return ""
    match = re.fullmatch(r"(?:(SH|SZ|BJ)[.:\s-]?)?(\d{1,6})(?:[.:\s-]?(SH|SZ|BJ))?", code)
    if not match or (match[1] and match[3] and match[1] != match[3]):
        raise ValueError("证券代码请填写 1 至 6 位数字，可带 SH / SZ / BJ 市场前后缀")
    return match[2].zfill(6)


def _prices(values):
    result = {}
    for code, price in values.items():
        if price in (None, ""):
            continue
        code = _code(code)
        if not code:
            raise ValueError("估值价格缺少证券代码")
        number = float(_number(price, "估值价格", PRICE))
        if number <= 0:
            raise ValueError("估值价格必须大于零，缺价请留空")
        if code in result and result[code] != number:
            raise ValueError(f"{code} 重复填写了不同的估值价格")
        result[code] = number
    return result


def _price_details(values, prices, date):
    if values is None:
        values = {}
    if not isinstance(values, dict) or len(values) > 10000:
        raise ValueError("估值来源信息格式无效")
    normalized = {}
    for raw_code, detail in values.items():
        code = _code(raw_code)
        if code not in prices or not isinstance(detail, dict) or code in normalized:
            raise ValueError("估值来源必须逐一对应有效价格，不能重复")
        price_date = _date(detail.get("priceDate", detail.get("sourceDate", date)), "价格日期")
        requested_date = _date(detail.get("requestedDate", date), "估值目标日期")
        if price_date > date or requested_date != date:
            raise ValueError("价格日期不能晚于估值日期，目标日期必须与快照一致")
        if detail.get("adjustment", "none") != "none":
            raise ValueError("账户持仓估值必须使用不复权价格")
        if not isinstance(detail.get("stale", False), bool):
            raise ValueError("价格时效标记必须为布尔值")
        normalized[code] = {"source": _text(detail.get("source") or "manual-prices", "价格来源", 300),
                            "priceDate": price_date, "requestedDate": date, "adjustment": "none", "stale": detail.get("stale", False) or price_date < date}
        for field in ("provider", "fetchedAt"):
            if detail.get(field):
                normalized[code][field] = _text(detail[field], "价格来源信息", 300)
    for code in prices:
        normalized.setdefault(code, {"source": "manual-prices", "priceDate": date,
                                     "requestedDate": date, "adjustment": "none", "stale": False})
    return normalized


class AccountService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "accounts.sqlite3"
        self.previous_path = self.directory / "accounts.previous.sqlite3"
        self.recovery_warning = None
        self.db = self._connect()
        self._schema()

    def _connect(self):
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError("invalid database")
        except sqlite3.DatabaseError:
            try:
                connection.close()
            except Exception:
                pass
            if not self.previous_path.exists():
                raise ValueError("账户数据库损坏；原文件已保留，请从备份恢复") from None
            try:
                previous = sqlite3.connect(f"file:{self.previous_path.as_posix()}?mode=ro", uri=True)
                if previous.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("invalid backup")
                if previous.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise ValueError("unsupported backup")
                previous.close()
            except Exception:
                raise ValueError("账户数据库及自动备份不可用；原文件已保留") from None
            preserved = self.directory / f"accounts.damaged-{_id()}.sqlite3"
            shutil.copy2(self.path, preserved)
            shutil.copy2(self.previous_path, self.path)
            connection = sqlite3.connect(self.path, timeout=10)
            self.recovery_warning = "账户数据库异常，已从上一次一致备份恢复；异常文件已保留。"
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _schema(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self.db.close()
            raise ValueError("账户数据库来自更新版本，请升级软件后打开")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, data TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                account_id TEXT NOT NULL REFERENCES accounts(id),
                data TEXT NOT NULL, voided INTEGER NOT NULL DEFAULT 0,
                external_id TEXT NOT NULL DEFAULT '',
                import_key TEXT NOT NULL DEFAULT ''
            );
            DROP INDEX IF EXISTS ledger_external;
            CREATE UNIQUE INDEX ledger_external
                ON ledger(account_id, json_extract(data, '$.date'), external_id) WHERE external_id <> '';
            CREATE UNIQUE INDEX IF NOT EXISTS ledger_import
                ON ledger(account_id, import_key) WHERE import_key <> '';
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
                date TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(account_id, date)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                method TEXT NOT NULL, entity_id TEXT NOT NULL, before_data TEXT, after_data TEXT
            );
            CREATE TABLE IF NOT EXISTS import_receipts (
                account_id TEXT NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL,
                import_key TEXT NOT NULL, record_id TEXT NOT NULL,
                PRIMARY KEY(account_id,kind,import_key)
            );
        """)
        self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.db.commit()

    def close(self):
        self.db.close()

    def _automatic_backup(self):
        temporary = self.previous_path.with_suffix(".tmp")
        backup = sqlite3.connect(temporary)
        try:
            self.db.backup(backup)
            backup.close()
            os.replace(temporary, self.previous_path)
        finally:
            try:
                backup.close()
            except Exception:
                pass

    def dispatch(self, method, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise ValueError("操作参数必须是对象")
        if method == "workspace":
            return self.workspace(payload.get("accountId"))
        if method == "account.asof":
            account = self._account(payload.get("accountId"))
            date = _date(payload.get("date"))
            if date < account["startDate"]:
                raise ValueError("查询日期不能早于账户起算日期")
            result = self.workspace(account["id"], as_of_date=date)
            result.pop("accounts", None)
            result.update({"account": account, "date": date, "asOfDate": date})
            return result
        if method == "review.score":
            from service import reviews
            value = payload.get("review", payload)
            if not isinstance(value, dict):
                raise ValueError("复盘评分内容必须是对象")
            if value.get("rubricVersion", reviews.RULE_VERSION) != reviews.RULE_VERSION:
                raise ValueError("评分规则版本不支持")
            score = reviews.score_review(value)
            return dict(score, total=score.get("score"))
        if method == "import.preview":
            return self._import_preview(payload)
        if method == "backup.export":
            return self._export()
        mutations = {
            "account.save": self._account_save, "account.archive": self._account_archive,
            "ledger.save": self._ledger_save, "ledger.void": self._ledger_void,
            "import.commit": self._import_commit, "review.save": self._review_save,
            "review.delete": lambda p: self._delete_record("reviews", p),
            "plan.save": self._plan_save, "plan.delete": lambda p: self._delete_record("plans", p),
            "snapshot.save": self._snapshot_save, "backup.restore": self._restore,
        }
        if method not in mutations:
            raise ValueError("不支持的账户操作")
        try:
            self._automatic_backup()
            self.db.execute("BEGIN IMMEDIATE")
            result = mutations[method](payload)
            self.db.commit()
            return result
        except sqlite3.IntegrityError:
            self.db.rollback()
            raise ValueError("记录重复或所属账户无效，请检查成交编号与账户") from None
        except sqlite3.Error:
            self.db.rollback()
            raise ValueError("账户数据库保存失败，原数据未改变") from None
        except Exception:
            self.db.rollback()
            raise

    def _audit(self, method, entity_id, before, after):
        self.db.execute("INSERT INTO audit(timestamp,method,entity_id,before_data,after_data) VALUES(?,?,?,?,?)",
                        (_now(), method, entity_id, _dump(before) if before is not None else None,
                         _dump(after) if after is not None else None))

    def _account(self, account_id, *, writable=False):
        row = self.db.execute("SELECT data FROM accounts WHERE id=?", (str(account_id or ""),)).fetchone()
        if not row:
            raise ValueError("请选择有效账户")
        account = json.loads(row[0])
        if writable and account.get("archived"):
            raise ValueError("账户已归档，请先恢复账户再修改")
        return account

    def _account_save(self, payload):
        value = payload.get("account", payload)
        old = self._account(value["id"]) if value.get("id") else None
        name = _text(value.get("name", old.get("name") if old else ""), "账户名称", 100)
        if not name:
            raise ValueError("请输入账户名称")
        kind = value.get("kind", old.get("kind") if old else "paper")
        if kind not in ("real", "paper"):
            raise ValueError("账户类型只能是实盘或模拟")
        account = {
            "id": old["id"] if old else _id(), "name": name, "kind": kind,
            "startDate": _date(value.get("startDate", old.get("startDate") if old else dt.date.today().isoformat())),
            "openingCash": _money(_number(value.get("openingCash", old.get("openingCash") if old else 0), "期初现金")),
            "goalPercent": float(_number(value.get("goalPercent", old.get("goalPercent") if old else 0), "目标收益率", negative=True)),
            "stages": value.get("stages", old.get("stages") if old else []),
            "archived": old.get("archived", False) if old else False,
            "createdAt": old.get("createdAt", _now()) if old else _now(), "updatedAt": _now(),
            "revision": old.get("revision", 0) + 1 if old else 1,
        }
        if not isinstance(account["stages"], list) or len(account["stages"]) > 100:
            raise ValueError("账户阶段格式无效")
        _dump(account["stages"])
        self.db.execute("INSERT INTO accounts VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,archived=excluded.archived,updated_at=excluded.updated_at",
                        (account["id"], _dump(account), int(account["archived"]), account["createdAt"], account["updatedAt"]))
        self._replay(account)
        self._refresh_snapshots(account["id"])
        self._audit("account.save", account["id"], old, account)
        return self.workspace(account["id"])

    def _account_archive(self, payload):
        account = self._account(payload.get("accountId", payload.get("id")))
        old = copy.deepcopy(account)
        account["archived"] = bool(payload.get("archived", True))
        account["updatedAt"] = _now()
        account["revision"] += 1
        self.db.execute("UPDATE accounts SET data=?,archived=?,updated_at=? WHERE id=?",
                        (_dump(account), int(account["archived"]), account["updatedAt"], account["id"]))
        self._audit("account.archive", account["id"], old, account)
        return self.workspace(account["id"])

    def _normalize_entry(self, payload, account, old=None):
        if not isinstance(payload, dict):
            raise ValueError("流水内容必须是对象")
        value = {**(old or {}), **payload}
        if old and "amount" not in payload and any(key in payload for key in ("quantity", "price")):
            value["amount"] = None
        if old and "fees" not in payload and any(key in payload for key in ("commission", "stampTax", "transferFee", "otherFees")):
            # The aggregate is derived from edited components. An old aggregate
            # must never reappear as otherFees when the final component becomes zero.
            value["fees"] = 0
        if old and "sourceValues" in old and "reconciliation" not in payload:
            choices = copy.deepcopy(value.get("reconciliation", {}))
            amount_changed = any(key in payload and _number(payload[key], key, QUANTITY if key == "quantity" else PRICE if key == "price" else MONEY)
                                 != _number(old.get(key), key, QUANTITY if key == "quantity" else PRICE if key == "price" else MONEY)
                                 for key in ("quantity", "price", "amount"))
            fees_changed = any(key in payload and _number(payload[key], key) != _number(old.get(key), key)
                               for key in ("commission", "stampTax", "transferFee", "otherFees"))
            if amount_changed:
                choices["amount"] = {"resolution": "corrected"}
                if "amount" not in payload:
                    value["amount"] = None
            if fees_changed:
                choices["fees"] = {"resolution": "corrected"}
            value["reconciliation"] = choices
        if "sourceValues" in value or "reconciliation" in value:
            from service.imports import reconcile_import_entry
            value, conflicts = reconcile_import_entry(value)
            if conflicts:
                raise ValueError("；".join(conflicts))
        kind = value.get("type")
        if kind not in KINDS:
            raise ValueError("不支持该流水类型")
        date = _date(value.get("date"))
        if date < account["startDate"]:
            raise ValueError("流水日期不能早于账户起算日期")
        time = _text(value.get("time", ""), "成交时间", 12)
        if time:
            try:
                time = dt.time.fromisoformat(time).isoformat(timespec="seconds")
            except ValueError:
                raise ValueError("成交时间请使用 HH:MM:SS 格式") from None
        code = _code(value.get("code"))
        if kind in {"buy", "sell", "opening_position", "split"} and not code:
            raise ValueError("请输入证券代码")
        quantity = _number(value.get("quantity"), "数量", QUANTITY, negative=kind == "split")
        price = _number(value.get("price"), "成交价格", PRICE)
        amount = _number(value.get("amount"), "金额")
        fees = {key: _number(value.get(key), label) for key, label in
                (("commission", "佣金"), ("stampTax", "印花税"), ("transferFee", "过户费"), ("otherFees", "其他费用"))}
        if value.get("fees") not in (None, "", 0, "0") and sum(fees.values()) == ZERO:
            fees["otherFees"] = _number(value["fees"], "总费用")
        total_fees = sum(fees.values())
        if "fees" in payload and payload.get("fees") not in (None, ""):
            if _number(payload["fees"], "总费用") != total_fees:
                raise ValueError("总费用与佣金、税费明细之和不一致")
        if kind in {"buy", "sell", "opening_position"}:
            if quantity <= 0 or price <= 0:
                raise ValueError("成交数量与价格必须大于零")
            calculated = (quantity * price).quantize(MONEY, rounding=ROUND_HALF_UP)
            if value.get("amount") in (None, "", 0, "0"):
                amount = calculated
            elif abs(amount - calculated) > Decimal("0.05") and not (
                value.get("reconciliation", {}).get("amount", {}).get("status") == "resolved"
                and value.get("reconciliation", {}).get("amount", {}).get("resolution") == "source"
                and amount == _number(value.get("sourceValues", {}).get("amount"), "来源成交金额")
            ):
                raise ValueError("成交金额与数量乘价格不一致，请确认金额为未扣费用的成交额")
        elif kind == "split":
            if quantity == 0:
                raise ValueError("送转或合股的数量变化不能为零")
            if amount or total_fees or price:
                raise ValueError("送转或合股只调整数量，金额、价格和费用须为零")
        else:
            if amount <= 0:
                raise ValueError("资金流水金额必须大于零")
            quantity, price = ZERO, ZERO
            if total_fees:
                raise ValueError("资金流水请填写净金额，额外费用请单独记一笔费用流水")
        if kind == "opening_position" and total_fees:
            raise ValueError("期初持仓请使用包含历史费用的成本价，不单独填写费用")
        source_details = value.get("sourceDetails", old.get("sourceDetails", {}) if old else {})
        if not isinstance(source_details, dict) or len(_dump(source_details)) > 5000:
            raise ValueError("流水来源信息格式无效或过长")
        return {
            "id": old["id"] if old else str(value.get("id") or _id()), "accountId": account["id"],
            "type": kind, "date": date, "time": time, "code": code,
            "name": _text(value.get("name"), "证券名称", 100), "quantity": float(quantity),
            "price": float(price), "amount": _money(amount), "fees": _money(total_fees),
            **{key: _money(number) for key, number in fees.items()},
            "reason": _text(value.get("reason"), "交易理由"),
            "externalId": _text(value.get("externalId"), "外部成交编号", 200),
            "source": _text(value.get("source", "manual"), "来源", 300),
            "sourceDetails": source_details,
            "importKey": _text(value.get("importKey", old.get("importKey", "") if old else ""), "导入标识", 300),
            "voided": bool(old.get("voided", False)) if old else False,
            "voidReason": _text(value.get("voidReason", ""), "作废原因"),
            "createdAt": old.get("createdAt", _now()) if old else _now(), "updatedAt": _now(),
            "revision": old.get("revision", 0) + 1 if old else 1,
            **({"sourceValues": value["sourceValues"], "reconciliation": value["reconciliation"]} if "sourceValues" in value else {}),
        }

    def _entries(self, account_id):
        rows = self.db.execute("SELECT seq,data FROM ledger WHERE account_id=? ORDER BY seq", (account_id,)).fetchall()
        entries = [dict(json.loads(row["data"]), sequence=row["seq"]) for row in rows]
        return sorted(entries, key=lambda e: (e["date"], e.get("time") or "00:00:00", e["sequence"]))

    def _save_entry(self, entry):
        self.db.execute("""INSERT INTO ledger(id,account_id,data,voided,external_id,import_key) VALUES(?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data,voided=excluded.voided,
            external_id=excluded.external_id,import_key=excluded.import_key""",
            (entry["id"], entry["accountId"], _dump(entry), int(entry["voided"]), entry["externalId"], entry["importKey"]))

    def _ledger_save(self, payload):
        value = payload.get("entry", payload)
        account = self._account(payload.get("accountId", value.get("accountId")), writable=True)
        old = None
        if value.get("id"):
            old = self._find_entry(account["id"], value["id"])
            if old["voided"]:
                raise ValueError("作废流水不可修改，请新增正确记录")
        entry = self._normalize_entry(value, account, old)
        self._save_entry(entry)
        self._replay(account)
        self._refresh_snapshots(account["id"])
        self._audit("ledger.save", entry["id"], old, entry)
        return self.workspace(account["id"])

    def _find_entry(self, account_id, entry_id):
        row = self.db.execute("SELECT data FROM ledger WHERE account_id=? AND id=?", (account_id, entry_id)).fetchone()
        if not row:
            raise ValueError("流水不存在或不属于当前账户")
        return json.loads(row[0])

    def _ledger_void(self, payload):
        account = self._account(payload.get("accountId"), writable=True)
        entry = self._find_entry(account["id"], payload.get("id"))
        old = copy.deepcopy(entry)
        entry["voided"] = True
        entry["voidReason"] = _text(payload.get("reason", "手动作废"), "作废原因")
        entry["revision"] += 1
        entry["updatedAt"] = _now()
        self._save_entry(entry)
        self._replay(account)
        self._refresh_snapshots(account["id"])
        self._audit("ledger.void", entry["id"], old, entry)
        return self.workspace(account["id"])

    def _replay(self, account, cutoff=None):
        cash = _number(account["openingCash"], "期初现金")
        contributions, realized, total_fees = cash, ZERO, ZERO
        holdings, active_rounds, rounds, entries, attribution_warnings = {}, {}, [], [], []
        for original in self._entries(account["id"]):
            if original["voided"] or (cutoff and original["date"] > cutoff):
                continue
            entry = copy.deepcopy(original)
            if entry["date"] < account["startDate"]:
                raise ValueError("账户起算日期晚于已有流水，请先调整历史记录")
            kind, code = entry["type"], entry["code"]
            quantity, amount, fees = Decimal(str(entry["quantity"])), Decimal(str(entry["amount"])), Decimal(str(entry["fees"]))
            profit = ZERO
            if kind in {"buy", "sell", "opening_position", "split"}:
                position = holdings.setdefault(code, {"code": code, "name": entry["name"], "quantity": ZERO, "cost": ZERO})
                if entry["name"]:
                    position["name"] = entry["name"]
                if kind in {"buy", "opening_position"}:
                    if position["quantity"] == 0:
                        active_rounds[code] = {"id": entry["id"], "code": code, "name": position["name"], "startDate": entry["date"],
                            "endDate": None, "quantityBought": ZERO, "quantitySold": ZERO, "buyAmount": ZERO,
                            "sellAmount": ZERO, "fees": ZERO, "buyFees": ZERO, "realizedPnl": ZERO,
                            "dividendIncome": ZERO, "standaloneFees": ZERO, "attributedCashIds": [], "tradeIds": [], "status": "open"}
                    position["quantity"] += quantity
                    position["cost"] += amount + fees
                    if kind == "buy":
                        cash -= amount + fees
                    else:
                        contributions += amount
                    current = active_rounds[code]
                    current["quantityBought"] += quantity
                    current["buyAmount"] += amount
                    current["buyFees"] += fees
                elif kind == "sell":
                    if quantity > position["quantity"]:
                        raise ValueError(f"{entry['date']} {code} 卖出数量超过持仓，历史修改未保存")
                    if quantity == position["quantity"]:
                        allocated_cost = position["cost"]
                    else:
                        allocated_cost = (position["cost"] * quantity / position["quantity"]).quantize(MONEY, rounding=ROUND_HALF_UP)
                    position["quantity"] -= quantity
                    position["cost"] -= allocated_cost
                    cash += amount - fees
                    profit = amount - fees - allocated_cost
                    realized += profit
                    current = active_rounds[code]
                    current["quantitySold"] += quantity
                    current["sellAmount"] += amount
                    current["realizedPnl"] += profit
                else:
                    if position["quantity"] <= 0 or position["quantity"] + quantity <= 0:
                        raise ValueError(f"{entry['date']} {code} 送转合股后持仓数量必须大于零")
                    position["quantity"] += quantity
                    current = active_rounds[code]
                current["fees"] += fees
                current["tradeIds"].append(entry["id"])
                if position["quantity"] == 0:
                    current["endDate"], current["status"] = entry["date"], "closed"
                    rounds.append(active_rounds.pop(code))
            elif kind == "deposit":
                cash += amount
                contributions += amount
            elif kind == "withdrawal":
                cash -= amount
                contributions -= amount
            elif kind == "dividend":
                cash += amount
                realized += amount
                profit = amount
            elif kind == "fee":
                cash -= amount
                realized -= amount
                profit = -amount
                total_fees += amount
            if kind in {"dividend", "fee"}:
                current = active_rounds.get(code) if code else None
                if current is not None:
                    current["dividendIncome" if kind == "dividend" else "standaloneFees"] += amount
                    current["attributedCashIds"].append(entry["id"])
                    current["tradeIds"].append(entry["id"])
                    entry["attributedRoundId"] = current["id"]
                else:
                    attribution_warnings.append(f"{entry['date']} {code or '未填证券代码'} 的{'分红' if kind == 'dividend' else '独立费用'}未归属回合：当时无明确持仓回合，保留在账户损益中。")
            total_fees += fees
            if cash < 0:
                raise ValueError(f"{entry['date']} 流水后现金不足，请先记录真实入金或修正期初资金")
            entry["runningCash"], entry["realizedPnl"] = _money(cash), _money(profit)
            entries.append(entry)
        rounds.extend(active_rounds.values())
        for trade_round in rounds:
            trade_round["pricePnl"] = trade_round["realizedPnl"]
            trade_round["totalPnl"] = trade_round["pricePnl"] + trade_round["dividendIncome"] - trade_round["standaloneFees"]
            for key in ("quantityBought", "quantitySold", "buyAmount", "sellAmount", "fees", "buyFees", "realizedPnl", "pricePnl", "dividendIncome", "standaloneFees", "totalPnl"):
                trade_round[key] = float(trade_round[key])
            basis = trade_round["buyAmount"] + trade_round["buyFees"]
            trade_round["returnPct"] = round(trade_round["totalPnl"] / basis * 100, 4) if basis and trade_round["status"] == "closed" else None
            trade_round["holdingDays"] = (dt.date.fromisoformat(trade_round["endDate"] or cutoff or dt.date.today().isoformat()) - dt.date.fromisoformat(trade_round["startDate"])).days
        return {"cash": cash, "netContributions": contributions, "realizedPnl": realized,
                "totalFees": total_fees, "holdings": holdings, "rounds": rounds, "entries": entries,
                "attributionWarnings": attribution_warnings}

    @staticmethod
    def _rounds_summary(rounds):
        completed = [item for item in rounds if item["status"] == "closed"]
        wins = [Decimal(str(item["totalPnl"])) for item in completed if item["totalPnl"] > 0]
        losses = [Decimal(str(item["totalPnl"])) for item in completed if item["totalPnl"] < 0]
        average_win = sum(wins, ZERO) / len(wins) if wins else None
        average_loss = -sum(losses, ZERO) / len(losses) if losses else None
        securities = {}
        for item in completed:
            group = securities.setdefault(item["code"], {"code": item["code"], "name": item["name"], "completedCount": 0,
                "pricePnl": ZERO, "dividendIncome": ZERO, "standaloneFees": ZERO, "totalPnl": ZERO})
            group["completedCount"] += 1
            for key in ("pricePnl", "dividendIncome", "standaloneFees", "totalPnl"):
                group[key] += Decimal(str(item[key]))
        by_security = []
        for group in sorted(securities.values(), key=lambda item: item["code"]):
            by_security.append({key: _money(value) if isinstance(value, Decimal) else value for key, value in group.items()})
        return {"completedCount": len(completed), "winCount": len(wins), "lossCount": len(losses),
            "breakevenCount": len(completed) - len(wins) - len(losses),
            "winRate": round(len(wins) / len(completed) * 100, 4) if completed else None,
            "profitLossRatio": float((average_win / average_loss).quantize(PRICE, rounding=ROUND_HALF_UP)) if average_win is not None and average_loss else None,
            "averageWin": _money(average_win) if average_win is not None else None,
            "averageLoss": _money(average_loss) if average_loss is not None else None,
            "averageHoldingDays": round(sum(item["holdingDays"] for item in completed) / len(completed), 2) if completed else None,
            "totalPnl": _money(sum((Decimal(str(item["totalPnl"])) for item in completed), ZERO)) if completed else None,
            "bySecurity": by_security, "method": "closed-rounds-including-attributed-cashflows"}

    def _valuation(self, replay, prices=None, valuation_date=None, price_details=None):
        prices = prices or {}
        positions, missing, cost_total, market_total = [], [], ZERO, ZERO
        for code, holding in sorted(replay["holdings"].items()):
            if holding["quantity"] <= 0:
                continue
            quantity, cost = holding["quantity"], holding["cost"]
            price = prices.get(code)
            price = _number(price, "估值价格", PRICE) if price is not None else None
            value = (quantity * price).quantize(MONEY, rounding=ROUND_HALF_UP) if price is not None else None
            if value is None:
                missing.append(code)
            else:
                market_total += value
            cost_total += cost
            positions.append({"code": code, "name": holding["name"], "quantity": float(quantity),
                "cost": _money(cost), "averageCost": float((cost / quantity).quantize(PRICE, rounding=ROUND_HALF_UP)),
                "marketPrice": float(price) if price is not None else None,
                "marketValue": _money(value) if value is not None else None,
                "unrealizedPnl": _money(value - cost) if value is not None else None,
                "valuationDate": valuation_date if price is not None else None,
                "priceDetails": (price_details or {}).get(code) if price is not None else None})
        equity = replay["cash"] + market_total if not missing else None
        contribution = replay["netContributions"]
        # Profit / net contributions is explicitly labelled a simple return, not time-weighted performance.
        simple_return = float(((equity - contribution) / contribution * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if equity is not None and contribution > 0 else None
        summary = {key: _money(replay[key]) for key in ("cash", "netContributions", "realizedPnl", "totalFees")}
        summary.update({"investedCost": _money(cost_total), "marketValue": _money(market_total) if not missing else None,
            "equity": _money(equity) if equity is not None else None, "returnPct": simple_return,
            "returnMethod": "simple-net-contributions", "maxDrawdown": None,
            "unrealizedPnl": _money(market_total - cost_total) if not missing else None,
            "valuationDate": valuation_date, "missingPrices": missing, "valuationComplete": not missing})
        return positions, summary

    def _records(self, table, account_id):
        return [json.loads(row[0]) for row in self.db.execute(f"SELECT data FROM {table} WHERE account_id=?", (account_id,))]

    def workspace(self, account_id=None, *, as_of_date=None):
        accounts = [json.loads(row[0]) for row in self.db.execute("SELECT data FROM accounts ORDER BY archived,created_at,id")]
        if account_id is not None:
            account = self._account(account_id)
        else:
            account = next((item for item in accounts if not item["archived"]), accounts[0] if accounts else None)
        schema = self._review_schema()
        if account is None:
            return {"accounts": [], "accountId": None, "ledger": [], "positions": [], "summary": None,
                    "rounds": [], "snapshots": [], "reviews": [], "plans": [], "reviewSchema": schema,
                    "roundsSummary": self._rounds_summary([]), "attributionWarnings": [], "warning": self.recovery_warning}
        replay = self._replay(account, as_of_date)
        snapshots = sorted((record for record in self._records("snapshots", account["id"])
                            if as_of_date is None or record["date"] <= as_of_date), key=lambda s: s["date"])
        latest = snapshots[-1] if snapshots else None
        last_ledger_date = max((entry["date"] for entry in replay["entries"]), default=account["startDate"])
        current = bool(latest and latest["date"] >= last_ledger_date)
        details = _price_details(latest.get("priceDetails"), latest["prices"], latest["date"]) if current else None
        positions, summary = self._valuation(replay, latest["prices"] if current else None, latest["date"] if current else None, details)
        summary["valuationStale"] = bool(latest and not current)
        summary["latestSnapshotDate"] = latest["date"] if latest else None
        summary["asOfDate"] = as_of_date
        # Equal net contributions do not prove absence of external flows: an
        # intervening deposit and withdrawal can cancel. Missing valuations also
        # break continuity. Keep each valid segment and report their maximum.
        flow_dates = sorted({entry["date"] for entry in replay["entries"]
                             if entry["type"] in {"deposit", "withdrawal", "opening_position"}})
        peak, drawdown, comparable_pairs, previous = ZERO, ZERO, 0, None
        for snapshot in snapshots:
            snapshot_summary = snapshot.get("summary", {})
            if snapshot_summary.get("equity") is None:
                previous, peak = None, ZERO
                continue
            equity = Decimal(str(snapshot_summary["equity"]))
            capital_changed = previous is not None and (
                snapshot_summary["netContributions"] != previous["summary"]["netContributions"]
                or bisect_right(flow_dates, snapshot["date"]) > bisect_right(flow_dates, previous["date"]))
            if previous is None or capital_changed:
                peak = equity
            else:
                comparable_pairs += 1
                peak = max(peak, equity)
                if peak > 0:
                    drawdown = max(drawdown, (peak - equity) / peak * 100)
            previous = snapshot
        if comparable_pairs:
            summary["maxDrawdown"] = float(drawdown.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        summary["drawdownMethod"] = "recorded-snapshots-flow-segmented" if summary["maxDrawdown"] is not None else "insufficient-comparable-snapshots"
        enriched = {entry["id"]: entry for entry in replay["entries"]}
        ledger = [enriched.get(entry["id"], entry) for entry in self._entries(account["id"]) if as_of_date is None or entry["date"] <= as_of_date]
        reviews = sorted((record for record in self._records("reviews", account["id"]) if as_of_date is None or record["date"] <= as_of_date),
                         key=lambda r: (r["date"], r.get("updatedAt", "")), reverse=True)
        plans = sorted((record for record in self._records("plans", account["id"]) if as_of_date is None or record["date"] <= as_of_date),
                       key=lambda p: (p["date"], p.get("updatedAt", "")), reverse=True)
        for plan in plans:
            plan["execution"] = self._plan_execution(plan, ledger, as_of_date)
        return {"accounts": accounts, "accountId": account["id"], "ledger": ledger, "positions": positions,
                "summary": summary, "rounds": replay["rounds"], "snapshots": snapshots,
                "reviews": reviews, "plans": plans, "reviewSchema": schema, "warning": self.recovery_warning,
                "roundsSummary": self._rounds_summary(replay["rounds"]), "attributionWarnings": replay["attributionWarnings"]}

    @staticmethod
    def _plan_execution(plan, ledger, as_of_date=None):
        by_id = {entry["id"]: entry for entry in ledger}
        expected_type = {"buy": "buy", "add": "buy", "sell": "sell", "reduce": "sell"}.get(plan["action"])
        matched, excluded, warnings = [], [], []
        for trade_id in plan.get("linkedTradeIds", []):
            entry = by_id.get(trade_id)
            issue = None
            if entry is None:
                issue = "关联流水不存在或不在查询日期范围内"
            elif entry.get("voided"):
                issue = "关联流水已作废"
            elif not plan["code"] or entry["code"] != plan["code"]:
                issue = "关联流水证券与计划不匹配"
            elif expected_type is None or entry["type"] != expected_type:
                issue = "关联流水方向与计划不匹配"
            if issue:
                excluded.append(trade_id)
                warnings.append(f"{issue}：{trade_id}")
            else:
                matched.append(entry)
        quantity = sum((Decimal(str(entry["quantity"])) for entry in matched), ZERO)
        amount = sum((Decimal(str(entry["amount"])) for entry in matched), ZERO)
        fees = sum((Decimal(str(entry["fees"])) for entry in matched), ZERO)
        planned = Decimal(str(plan["quantity"]))
        status = "unexecuted" if quantity == 0 else ("unquantified" if planned == 0 else ("completed" if quantity >= planned else "partial"))
        if planned and quantity > planned:
            warnings.append("已关联成交数量超过计划数量，请检查是否超额执行或重复关联")
        return {"status": status, "quantity": float(quantity), "plannedQuantity": float(planned),
            "remainingQuantity": float(max(ZERO, planned - quantity)) if planned else None,
            "completionPct": float((quantity / planned * 100).quantize(PRICE, rounding=ROUND_HALF_UP)) if planned else None,
            "averagePrice": float((amount / quantity).quantize(PRICE, rounding=ROUND_HALF_UP)) if quantity else None,
            "amount": _money(amount), "fees": _money(fees), "matchedTradeIds": [entry["id"] for entry in matched],
            "excludedTradeIds": excluded, "warnings": warnings, "asOfDate": as_of_date}

    def _review_schema(self):
        from service import reviews
        return reviews.review_schema()

    def _linked_ids(self, value, account_id):
        if not isinstance(value, list) or len(value) > 10000:
            raise ValueError("关联流水格式无效")
        result = list(dict.fromkeys(str(item) for item in value))
        for entry_id in result:
            self._find_entry(account_id, entry_id)
        return result

    def _existing_record(self, table, record_id, account_id):
        if not record_id:
            return None
        row = self.db.execute(f"SELECT data FROM {table} WHERE id=? AND account_id=?", (record_id, account_id)).fetchone()
        if not row:
            raise ValueError("记录不存在或不属于当前账户")
        return json.loads(row[0])

    def _save_record(self, table, record, old, method):
        record["revision"] = old.get("revision", 0) + 1 if old else 1
        record["createdAt"] = old.get("createdAt", _now()) if old else _now()
        record["updatedAt"] = _now()
        self.db.execute(f"INSERT INTO {table}(id,account_id,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (record["id"], record["accountId"], _dump(record)))
        self._audit(method, record["id"], old, record)

    def _review_save(self, payload):
        value = payload.get("review", payload)
        account = self._account(payload.get("accountId", value.get("accountId")), writable=True)
        old = self._existing_record("reviews", value.get("id"), account["id"])
        self._put_review(value, account, old)
        return self.workspace(account["id"])

    def _put_review(self, value, account, old=None):
        from service import reviews
        value = {**(old or {}), **value}
        kind = value.get("kind", "daily")
        if kind not in {"daily", "weekly", "monthly", "trade", "idea"}:
            raise ValueError("复盘类型无效")
        record = {"id": old["id"] if old else _id(), "accountId": account["id"], "kind": kind,
            "date": _date(value.get("date")), "endDate": _date(value.get("endDate") or value.get("date")),
            "title": _text(value.get("title"), "复盘标题", 200),
            "fields": value.get("fields", {}), "scores": value.get("scores", {}),
            "scoreNotes": value.get("scoreNotes", {}),
            "relatedTradeIds": self._linked_ids(value.get("relatedTradeIds", []), account["id"]),
            "source": _text(value.get("source", "manual"), "来源", 300),
            "importKey": _text(value.get("importKey", ""), "导入标识", 300),
            "rubricVersion": value.get("rubricVersion", reviews.RULE_VERSION)}
        if record["endDate"] < record["date"]:
            raise ValueError("复盘结束日期不能早于开始日期")
        for key in ("fields", "scores", "scoreNotes"):
            if not isinstance(record[key], dict) or len(_dump(record[key])) > 200000:
                raise ValueError("复盘内容格式错误或过长")
        record = reviews.validate_review(record)
        record["score"] = reviews.score_review(record)
        record["score"]["total"] = record["score"].get("score")
        record["rubricVersion"] = self._review_schema().get("version", 1)
        self._save_record("reviews", record, old, "review.save")
        return record

    def _plan_save(self, payload):
        value = payload.get("plan", payload)
        account = self._account(payload.get("accountId", value.get("accountId")), writable=True)
        old = self._existing_record("plans", value.get("id"), account["id"])
        value = {**(old or {}), **value}
        record = self._normalize_plan(value, account, old["id"] if old else _id())
        self._save_record("plans", record, old, "plan.save")
        return self.workspace(account["id"])

    def _normalize_plan(self, value, account, record_id):
        status = value.get("status", "draft")
        if status not in {"draft", "watching", "ready", "triggered", "executed", "cancelled", "expired"}:
            raise ValueError("计划状态无效")
        record = {"id": record_id, "accountId": account["id"],
            "date": _date(value.get("date")), "code": _code(value.get("code")),
            "name": _text(value.get("name"), "证券名称", 100), "action": value.get("action", "watch"),
            "quantity": float(_number(value.get("quantity"), "计划数量", QUANTITY)),
            "trigger": _text(value.get("trigger"), "触发条件"),
            "stopLoss": _text(value.get("stopLoss"), "止损条件"),
            "takeProfit": _text(value.get("takeProfit"), "止盈条件"),
            "reason": _text(value.get("reason"), "计划理由"), "status": status,
            "executionNotes": _text(value.get("executionNotes"), "执行记录"),
            "linkedTradeIds": self._linked_ids(value.get("linkedTradeIds", []), account["id"])}
        if record["action"] not in {"buy", "sell", "hold", "watch", "reduce", "add"}:
            raise ValueError("计划动作无效")
        if not record["code"] and not record["reason"]:
            raise ValueError("请填写证券代码或计划理由")
        return record

    def _delete_record(self, table, payload):
        account = self._account(payload.get("accountId"), writable=True)
        old = self._existing_record(table, payload.get("id"), account["id"])
        if not old:
            raise ValueError("请选择需要删除的记录")
        self.db.execute(f"DELETE FROM {table} WHERE id=? AND account_id=?", (old["id"], account["id"]))
        if table == "reviews":
            self.db.execute("DELETE FROM import_receipts WHERE account_id=? AND kind='reviews' AND record_id=?", (account["id"], old["id"]))
        self._audit(table + ".delete", old["id"], old, None)
        return self.workspace(account["id"])

    def _snapshot_save(self, payload):
        value = payload.get("snapshot", payload)
        account = self._account(payload.get("accountId", value.get("accountId")), writable=True)
        date = _date(value.get("date"))
        if date < account["startDate"]:
            raise ValueError("估值日期不能早于账户起算日期")
        prices = value.get("prices", {})
        if isinstance(prices, list):
            prices = {item["code"]: item.get("price", item.get("marketPrice")) for item in prices}
        if not isinstance(prices, dict) or len(prices) > 10000:
            raise ValueError("估值价格格式无效")
        prices = _prices(prices)
        price_details = _price_details(value.get("priceDetails"), prices, date)
        old_row = self.db.execute("SELECT data FROM snapshots WHERE account_id=? AND date=?", (account["id"], date)).fetchone()
        old = json.loads(old_row[0]) if old_row else None
        positions, summary = self._valuation(self._replay(account, date), prices, date, price_details)
        record = {"id": old["id"] if old else _id(), "accountId": account["id"], "date": date,
            "prices": prices, "priceDetails": price_details, "positions": positions, "summary": summary,
            "source": "manual-prices" if all(item["source"] == "manual-prices" for item in price_details.values()) else "sourced-prices",
            "note": _text(value.get("note"), "估值备注"), "updatedAt": _now(), "revision": old.get("revision", 0) + 1 if old else 1}
        self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET data=excluded.data",
                        (record["id"], account["id"], date, _dump(record)))
        self._audit("snapshot.save", record["id"], old, record)
        return self.workspace(account["id"])

    def _refresh_snapshots(self, account_id):
        account = self._account(account_id)
        for record in self._records("snapshots", account_id):
            if record["date"] < account["startDate"]:
                raise ValueError("账户起算日期晚于已有估值快照")
            record["priceDetails"] = _price_details(record.get("priceDetails"), record["prices"], record["date"])
            positions, summary = self._valuation(self._replay(account, record["date"]), record["prices"], record["date"], record["priceDetails"])
            record["positions"], record["summary"] = positions, summary
            self.db.execute("UPDATE snapshots SET data=? WHERE id=?", (_dump(record), record["id"]))

    def _import_preview(self, payload):
        from service import imports
        account = self._account(payload.get("accountId"), writable=True)
        preview = imports.preview_import(payload)
        preview["accountId"] = account["id"]
        review_import = payload.get("kind") in {"review", "reviews"}
        preview["kind"] = "reviews" if review_import else "trades"
        for row in preview.get("rows", []):
            entry = row.get("entry", row.get("record", row.get("review")))
            if not entry:
                continue
            row["entry"] = entry
            entry["source"] = entry.get("source") or preview.get("source") or payload.get("fileName") or "import"
            prefix = "ocr:" if entry.get("source") in {"ocr", "screenshot"} else ""
            entry["importKey"] = entry.get("importKey") or f"{prefix}{preview.get('fileHash', '')}:{row.get('rowNumber', '')}"
            entry["sourceDetails"] = {**(entry.get("sourceDetails") if isinstance(entry.get("sourceDetails"), dict) else {}),
                "fileName": preview.get("fileName", payload.get("fileName")), "fileHash": preview.get("fileHash"), "rowNumber": row.get("rowNumber")}
            external = str(entry.get("externalId") or "")
            if review_import:
                duplicate = self.db.execute("SELECT record_id FROM import_receipts WHERE account_id=? AND kind='reviews' AND import_key=?",
                    (account["id"], entry["importKey"])).fetchone()
            else:
                duplicate = self.db.execute("SELECT id FROM ledger WHERE account_id=? AND ((external_id=? AND external_id<>'' AND json_extract(data,'$.date')=?) OR (import_key=? AND import_key<>''))",
                    (account["id"], external, entry.get("date"), entry["importKey"])).fetchone()
            row["duplicate"] = bool(duplicate)
            if duplicate:
                row["selected"] = False
                row["duplicateId"] = duplicate[0]
            if not row.get("errors"):
                try:
                    if review_import:
                        from service import reviews
                        reviews.validate_review(entry)
                        self._linked_ids(entry.get("relatedTradeIds", []), account["id"])
                    else:
                        self._normalize_entry(entry, account)
                except ValueError as error:
                    row["errors"] = [str(error)]
                    row["selected"] = False
        if not review_import:
            self._check_ocr_batch_consistency(account["id"], preview.get("rows", []))
        preview["summary"] = {
            "total": len(preview.get("rows", [])),
            "valid": sum(not row.get("errors") and not row.get("duplicate") for row in preview.get("rows", [])),
            "invalid": sum(bool(row.get("errors")) for row in preview.get("rows", [])),
            "duplicates": sum(bool(row.get("duplicate")) for row in preview.get("rows", [])),
            "selected": sum(row.get("selected") is True for row in preview.get("rows", [])),
        }
        return preview

    @staticmethod
    def _same_ocr_trade(previous, candidate):
        if previous.get("code") != str(candidate.get("code") or "").strip().upper() or previous.get("type") != candidate.get("type"):
            return False
        for key in ("date", "time"):
            if previous.get(key) and candidate.get(key) and previous[key] != candidate[key]:
                return False
        try:
            return (_number(previous.get("quantity"), "数量", QUANTITY) == _number(candidate.get("quantity"), "数量", QUANTITY)
                    and _number(previous.get("price"), "价格", PRICE) == _number(candidate.get("price"), "价格", PRICE))
        except ValueError:
            return False

    def _check_ocr_batch_consistency(self, account_id, rows):
        groups = {}
        for row in rows:
            entry = row.get("entry") or {}
            details = entry.get("sourceDetails") or {}
            image_hash = details.get("imageHash") if isinstance(details, dict) else None
            if entry.get("source") in {"ocr", "screenshot"} and image_hash:
                groups.setdefault(image_hash, []).append(row)
        if not groups:
            return
        prior = self._entries(account_id)
        for image_hash, candidates in groups.items():
            previous = [entry for entry in prior if entry.get("source") in {"ocr", "screenshot"} and entry.get("sourceDetails", {}).get("imageHash") == image_hash]
            if not previous:
                continue
            by_key = {row["entry"].get("importKey"): row["entry"] for row in candidates}
            conflict = any(entry.get("importKey") not in by_key or not self._same_ocr_trade(entry, by_key[entry["importKey"]]) for entry in previous)
            if conflict:
                explanation = "同组截图的识别内容或顺序已改变，与已入账流水不一致；请核对已有流水，将尚未导入的部分单独截图后再识别。"
                for row in candidates:
                    row["errors"] = list(row.get("errors", [])) + [explanation]
                    row["selected"] = False
                    row["conflict"] = True

    def _import_commit(self, payload):
        account = self._account(payload.get("accountId"), writable=True)
        rows = payload.get("rows", payload.get("preview", {}).get("rows", []))
        if not isinstance(rows, list) or len(rows) > 50000:
            raise ValueError("导入记录格式无效或数量过多")
        review_import = payload.get("kind", payload.get("preview", {}).get("kind")) in {"reviews", "review"}
        count, skipped = 0, 0
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("导入行格式无效")
            if row.get("selected", True) is not True:
                continue
            entry_data = row.get("entry", row)
            if not isinstance(entry_data, dict):
                raise ValueError("导入内容必须是对象")
            if not review_import and str(entry_data.get("voided", "")).strip().lower() in {"true", "1", "1.0", "yes", "是", "作废", "已作废"}:
                raise ValueError(f"第 {row.get('rowNumber', '?')} 行已作废，不能作为有效成交导入；完整历史请使用账本备份恢复")
            if review_import:
                key = _text(entry_data.get("importKey", ""), "导入标识", 300)
                duplicate = self.db.execute("SELECT record_id FROM import_receipts WHERE account_id=? AND kind='reviews' AND import_key=?", (account["id"], key)).fetchone() if key else None
                if duplicate:
                    skipped += 1
                    continue
                record = self._put_review(entry_data, account)
                if key:
                    self.db.execute("INSERT INTO import_receipts VALUES(?,?,?,?)", (account["id"], "reviews", key, record["id"]))
                count += 1
                continue
            if entry_data.get("feeMissing") and entry_data.get("type") in {"buy", "sell"}:
                if entry_data.get("feesConfirmed") is not True:
                    raise ValueError(f"第 {row.get('rowNumber', '?')} 行缺少费用，请核对并填写费用（确认无费用可填 0）")
            if entry_data.get("source") in {"ocr", "screenshot"} and entry_data.get("reviewed") is not True:
                raise ValueError(f"第 {row.get('rowNumber', '?')} 行截图识别结果尚未人工核对")
            # Revalidate corrected rows. Preview errors are explanatory, not an authority to bypass validation.
            from service.imports import reconcile_import_entry
            entry_data, conflicts = reconcile_import_entry(entry_data)
            if conflicts:
                raise ValueError("；".join(conflicts))
            entry = self._normalize_entry(entry_data, account)
            if not entry["source"] or entry["source"] == "manual":
                entry["source"] = "file-import"
            duplicate = self.db.execute("SELECT id FROM ledger WHERE account_id=? AND ((external_id=? AND external_id<>'' AND json_extract(data,'$.date')=?) OR (import_key=? AND import_key<>''))",
                (account["id"], entry["externalId"], entry["date"], entry["importKey"])).fetchone()
            if duplicate:
                if entry.get("sourceDetails", {}).get("imageHash"):
                    prior = self._find_entry(account["id"], duplicate[0])
                    if prior.get("importKey") == entry["importKey"] and not self._same_ocr_trade(prior, entry):
                        raise ValueError("同组截图的识别内容已改变，请核对已有流水后再导入")
                skipped += 1
                continue
            if entry_data.get("id"):
                entry["id"] = _id()
            self._save_entry(entry)
            self._audit("import.commit", entry["id"], None, entry)
            count += 1
        if count == 0 and skipped == 0:
            raise ValueError("请至少选择一条有效记录")
        self._replay(account)
        self._refresh_snapshots(account["id"])
        result = self.workspace(account["id"])
        result["importResult"] = {"imported": count, "duplicates": skipped}
        return result

    def _export(self):
        return {"format": "a-trade.accounts", "schemaVersion": SCHEMA_VERSION, "exportedAt": _now(),
            "accounts": [json.loads(row[0]) for row in self.db.execute("SELECT data FROM accounts ORDER BY created_at,id")],
            "ledger": [json.loads(row[0]) for row in self.db.execute("SELECT data FROM ledger ORDER BY seq")],
            **{table: [json.loads(row[0]) for row in self.db.execute(f"SELECT data FROM {table}")] for table in ("reviews", "plans", "snapshots")},
            "importReceipts": [dict(row) for row in self.db.execute("SELECT * FROM import_receipts")],
            "audit": [dict(row) for row in self.db.execute("SELECT * FROM audit ORDER BY id")]}

    def _restore(self, payload):
        backup = payload.get("backup", payload.get("data"))
        if isinstance(backup, str):
            try:
                backup = json.loads(backup)
            except ValueError:
                raise ValueError("备份不是有效 JSON 文件") from None
        if not isinstance(backup, dict) or backup.get("format") != "a-trade.accounts" or backup.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("备份格式或版本不受支持")
        for key in ("accounts", "ledger", "reviews", "plans", "snapshots"):
            if not isinstance(backup.get(key), list) or len(backup[key]) > 100000:
                raise ValueError("备份内容不完整或记录数量过多")
        before_counts = {table: self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("accounts", "ledger", "reviews", "plans", "snapshots")}
        for table in ("import_receipts", "reviews", "plans", "snapshots", "ledger", "accounts"):
            self.db.execute(f"DELETE FROM {table}")
        seen = set()
        for item in backup["accounts"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 128 or item["id"] in seen:
                raise ValueError("备份含有无效或重复账户")
            seen.add(item["id"])
            if item.get("kind") not in ("real", "paper") or not _text(item.get("name"), "账户名称", 100):
                raise ValueError("备份账户信息无效")
            _date(item.get("startDate"))
            opening_cash = _number(item.get("openingCash"), "期初现金")
            if not isinstance(item.get("stages", []), list) or not isinstance(item.get("archived", False), bool):
                raise ValueError("备份账户阶段或归档信息无效")
            record = dict(item, archived=bool(item.get("archived")), revision=int(item.get("revision", 1)))
            record["openingCash"] = _money(opening_cash)
            self.db.execute("INSERT INTO accounts VALUES(?,?,?,?,?)", (record["id"], _dump(record), int(record["archived"]), record.get("createdAt", _now()), record.get("updatedAt", _now())))
        ledger_ids = set()
        for item in backup["ledger"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 128 or item["id"] in ledger_ids:
                raise ValueError("备份含有无效或重复流水编号")
            ledger_ids.add(item["id"])
            account = self._account(item.get("accountId"))
            entry = self._normalize_entry(item, account)
            entry.update({"voided": bool(item.get("voided")), "revision": int(item.get("revision", 1)), "createdAt": item.get("createdAt", _now()), "updatedAt": item.get("updatedAt", _now())})
            self._save_entry(entry)
        for account_id in seen:
            self._replay(self._account(account_id))
        for table in ("reviews", "plans", "snapshots"):
            for item in backup[table]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 128:
                    raise ValueError("备份记录缺少编号")
                account = self._account(item.get("accountId"))
                _date(item.get("date"))
                if table == "snapshots":
                    if not isinstance(item.get("prices"), dict):
                        raise ValueError("备份估值价格格式无效")
                    item["prices"] = _prices(item["prices"])
                    self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?)", (item["id"], item["accountId"], item["date"], _dump(item)))
                else:
                    links_key = "relatedTradeIds" if table == "reviews" else "linkedTradeIds"
                    item[links_key] = self._linked_ids(item.get(links_key, []), item["accountId"])
                    if table == "reviews":
                        from service import reviews
                        item = reviews.validate_review(item)
                        item["score"] = reviews.score_review(item)
                        item["score"]["total"] = item["score"].get("score")
                    else:
                        timestamps = {key: item.get(key) for key in ("createdAt", "updatedAt", "revision") if key in item}
                        item = self._normalize_plan(item, account, item["id"])
                        item.update(timestamps)
                    self.db.execute(f"INSERT INTO {table} VALUES(?,?,?)", (item["id"], item["accountId"], _dump(item)))
        for account_id in seen:
            self._refresh_snapshots(account_id)
        for item in backup.get("importReceipts", []):
            if not isinstance(item, dict) or item.get("kind") != "reviews" or not item.get("import_key") or not item.get("record_id"):
                raise ValueError("备份导入标识无效")
            self._account(item.get("account_id"))
            linked = self.db.execute("SELECT account_id FROM reviews WHERE id=?", (item["record_id"],)).fetchone()
            if not linked:
                # Older versions retained receipts after review deletion.
                continue
            if linked[0] != item["account_id"]:
                raise ValueError("备份复盘导入标识不属于当前账户")
            self.db.execute("INSERT INTO import_receipts VALUES(?,?,?,?)", (item["account_id"], item["kind"], item["import_key"], item["record_id"]))
        for item in backup.get("audit", []):
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("timestamp", "method", "entity_id")):
                raise ValueError("备份审计记录格式无效")
            exists = self.db.execute("SELECT 1 FROM audit WHERE timestamp=? AND method=? AND entity_id=? AND before_data IS ? AND after_data IS ?",
                (item["timestamp"], item["method"], item["entity_id"], item.get("before_data"), item.get("after_data"))).fetchone()
            if not exists:
                self.db.execute("INSERT INTO audit(timestamp,method,entity_id,before_data,after_data) VALUES(?,?,?,?,?)",
                    (item["timestamp"], item["method"], item["entity_id"], item.get("before_data"), item.get("after_data")))
        self._audit("backup.restore", "workspace", before_counts, {"accounts": len(seen), "exportedAt": backup.get("exportedAt")})
        return self.workspace(payload.get("accountId") if payload.get("accountId") in seen else None)
