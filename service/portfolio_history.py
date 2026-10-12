"""Validated, immutable historical research packages, separate from market data.

Certification means the supplied universe and evidence are internally complete;
it is not independent verification of a vendor's completeness declaration.
"""
from copy import deepcopy
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import zlib

VERSION = "historical-package-v1"


def digest(value):
    result = hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).iterencode(value):
        result.update(part.encode("utf-8"))
    return result.hexdigest()


def day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("历史数据日期须为 YYYY-MM-DD")
    return value


def normalize_package(raw, *, copy_data=True):
    from .portfolio_actions import normalize_actions
    from .portfolio_engine import PortfolioEngine
    from .portfolio_config import normalize_config
    if not isinstance(raw, dict) or raw.get("schema") != VERSION:
        raise ValueError("历史数据包 schema 须为 historical-package-v1")
    declaration = raw.get("declaration")
    if not isinstance(declaration, dict) or not all(isinstance(declaration.get(k), str) and declaration[k].strip()
                                                  for k in ("universe", "source", "calendarSource")):
        raise ValueError("须明确声明历史股票池、数据来源及交易日历来源")
    if declaration.get("completeMembership") is not True or declaration.get("completeActions") is not True:
        raise ValueError("历史包必须明确声明成分与权益事件覆盖完整，不能把当前股票名单当历史成分")
    coverage = raw.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("须提供 coverage.startDate/endDate")
    start, end = day(coverage.get("startDate")), day(coverage.get("endDate"))
    if start > end:
        raise ValueError("历史包覆盖日期倒序")
    data = deepcopy(raw.get("dataset")) if copy_data else raw.get("dataset")
    if not isinstance(data, dict) or not isinstance(data.get("securities"), list) or not data["securities"]:
        raise ValueError("历史包 dataset.securities 不可为空")
    if data.get("adjustment", "raw") not in ("raw", "none", "unadjusted") or any(
            s.get("adjustment", "raw") not in ("raw", "none", "unadjusted") for s in data["securities"]):
        raise ValueError("历史包只接受原始未复权成交价格，不能把复权价当成交价")
    if declaration["calendarSource"].startswith("observed"):
        raise ValueError("历史数据包交易日历须来自独立来源，不能用所选股票日期并集")
    dates = data.get("calendar")
    if not isinstance(dates, list) or not dates or dates != sorted(set(dates)):
        raise ValueError("须提供独立、完整、顺序唯一的交易日历")
    for value in dates:
        day(value)
    date_pool = {value: value for value in dates}
    interval = [value for value in dates if start <= value <= end]
    if not interval:
        raise ValueError("历史包覆盖期没有交易日")
    members = data.get("membershipByDate")
    if not isinstance(members, dict) or any(value not in members for value in interval):
        raise ValueError("覆盖期每个交易日都须有当日完整成分名单")
    securities = {s.get("symbol"): s for s in data["securities"] if isinstance(s, dict)}
    if len(securities) != len(data["securities"]):
        raise ValueError("证券记录重复或无效")
    for value in interval:
        if not isinstance(members[value], list) or len(members[value]) != len(set(members[value])):
            raise ValueError("历史成分须为不重复的证券代码列表")
        if set(members[value]) - securities.keys():
            raise ValueError(f"{value} 历史成分缺少证券记录（包括已退市证券）")
        members[value] = [sys.intern(symbol) for symbol in members[value]]
    member_sets = {value: set(members[value]) for value in interval}
    evidence_count = 0
    for symbol, security in securities.items():
        security["symbol"] = sys.intern(symbol)
        for bar in security.get("bars", []):
            bar["date"] = date_pool.get(bar["date"], bar["date"])
        for field in ("stByDate", "tradeStatusByDate", "precloseByDate", "memberByDate", "memberHistory", "factorByDate"):
            if isinstance(security.get(field), dict):
                security[field] = {date_pool.get(key, key): value for key, value in security[field].items()}
        listing = day(security.get("listingDate"))
        delisting = day(security["delistDate"]) if security.get("delistDate") else None
        if delisting and delisting < listing:
            raise ValueError("退市日期不能早于上市日期")
        bars = {bar["date"]: bar for bar in security.get("bars", [])}
        for value in interval:
            if symbol not in member_sets[value]:
                continue
            if value < listing or delisting and value >= delisting:
                raise ValueError(f"{symbol} 历史成分与上市/退市日期矛盾")
            if type(security.get("stByDate", {}).get(value)) is not bool:
                raise ValueError(f"{symbol} {value} 缺历史 ST 状态")
            status = security.get("tradeStatusByDate", {}).get(value)
            if status not in (True, False, 0, 1, "0", "1") or status is None:
                raise ValueError(f"{symbol} {value} 缺历史交易状态")
            if value not in bars:
                raise ValueError(f"{symbol} {value} 缺日线；停牌日也须提供可追溯估值记录")
            if value not in security.get("precloseByDate", {}):
                raise ValueError(f"{symbol} {value} 缺当日参考昨收")
            evidence_count += 1
        actions = normalize_actions(security.get("corporateActions", []))
        for action in actions:
            for key in ("recordDate", "exDate"):
                if start <= action[key] <= end and action[key] not in dates:
                    raise ValueError(f"{symbol} 权益事件 {key} 不在交易日历")
        action_dates = {action["exDate"] for action in actions}
        previous = None
        for value, bar in sorted(bars.items()):
            reference = security.get("precloseByDate", {}).get(value)
            if previous is not None and reference is not None and start <= value <= end and (
                    abs(reference - previous) > max(.005, previous * 1e-7)) and value not in action_dates:
                raise ValueError(f"{symbol} {value} 参考昨收发生变化，但完整权益账本未提供事件")
            previous = bar["close"]
        if security.get("corporateActionsComplete") is not True:
            raise ValueError(f"{symbol} 缺权益事件完整性声明（无事件也须明确声明）")
        security["corporateActions"] = actions
        # Derive history from dated membership, never present-day names.
        security["memberByDate"] = {value: symbol in member_sets[value] for value in interval}
    data.update(adjustment="raw", calendarSource=declaration["calendarSource"],
                source="imported historical package", historyCoverage={"startDate": start, "endDate": end},
                historyDeclaration=declaration,
                dataQuality={"certified": True, "pointInTimeComplete": True, "sourceCoverageComplete": True,
                             "certificateScope": "internallyValidatedDeclaredUniverseAndNetCashModel",
                             "verifiedEvidenceRows": evidence_count, "reasons": []},
                universeCoverage={"scope": declaration["universe"], "loadedCount": len(securities),
                                  "coverageComplete": True, "pointInTimeComplete": True,
                                  "failureCount": 0, "failures": [], "batchTruncation": False})
    data.pop("fingerprint", None)
    data["fingerprint"] = digest(data)
    # Reuse the ledger's price, symbol, calendar and state-map validation.
    validator = PortfolioEngine.__new__(PortfolioEngine)
    validator.config = normalize_config({"startDate": interval[0], "endDate": interval[-1],
                                         "universe": {"strictHistory": True}})
    validator._validate_dataset(data)
    return data


class HistoryStore:
    def __init__(self, directory):
        self.path = Path(directory).resolve() / "portfolio-history.sqlite3"

    def import_package(self, raw, *, owned=False):
        data = normalize_package(raw, copy_data=not owned)
        identity = data["fingerprint"]
        summary = {"id": identity, "coverage": data["historyCoverage"], "declaration": data["historyDeclaration"],
                   "symbolCount": len(data["securities"]), "quality": data["dataQuality"]}
        from .research_service import _pack
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS packages(id TEXT PRIMARY KEY, summary TEXT NOT NULL, data BLOB NOT NULL)")
            if db.execute("SELECT 1 FROM packages WHERE id=?", (identity,)).fetchone():
                return summary
            db.execute("CREATE TABLE IF NOT EXISTS package_parts(package_id TEXT, kind TEXT, key TEXT, data BLOB NOT NULL, PRIMARY KEY(package_id,kind,key))")
            header = {key: value for key, value in data.items() if key not in ("securities", "membershipByDate")}
            manifest = {"securities": {}, "membership": {}}
            for kind, records in (("securities", ((s["symbol"], s) for s in data["securities"])),
                                  ("membership", data["membershipByDate"].items())):
                for key, value in records:
                    blob = _pack(value)
                    manifest[kind][key] = hashlib.sha256(blob).hexdigest()
                    db.execute("INSERT INTO package_parts VALUES (?,?,?,?)", (identity, kind, key, blob))
            header["_storage"] = {"version": "sharded-v1", "manifest": manifest}
            db.execute("INSERT INTO packages VALUES (?,?,?)", (identity, json.dumps(summary, ensure_ascii=False), _pack(header)))
        return summary

    def list(self):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT summary FROM packages ORDER BY rowid DESC")]

    def snapshot(self, selection, start, end):
        from .portfolio_universe import board_of
        if not self.path.exists():
            raise ValueError("请先导入历史数据包")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT data FROM packages WHERE id=?", (selection.get("sourceId"),)).fetchone()
            if not row:
                raise ValueError("历史数据源不存在，请重新选择已导入数据包")
            data = json.loads(zlib.decompress(row[0]))
            coverage = data["historyCoverage"]
            if not start or not end or start < coverage["startDate"] or end > coverage["endDate"] or start > end:
                raise ValueError("历史数据包研究须指定其完整覆盖范围内的起止日期")
            storage = data.pop("_storage", None)
            if storage:
                if storage.get("version") != "sharded-v1":
                    raise ValueError("历史包存储版本不支持，请重新导入")
                manifest = storage["manifest"]
                def read_part(kind, key):
                    part = db.execute("SELECT data FROM package_parts WHERE package_id=? AND kind=? AND key=?",
                                      (selection.get("sourceId"), kind, key)).fetchone()
                    if not part or hashlib.sha256(part[0]).hexdigest() != manifest[kind][key]:
                        raise ValueError("历史包存储校验失败，请从原始文件重新导入")
                    return json.loads(zlib.decompress(part[0]))
                symbols = [symbol for symbol in manifest["securities"] if symbol[:2] in selection["markets"]
                           and board_of(symbol) in selection["boards"]
                           and (selection["mode"] != "explicit" or symbol in selection["symbols"])]
                data["securities"] = []
                from .portfolio_universe import _retain_causal_window
                for symbol in symbols:
                    security = read_part("securities", symbol)
                    security["bars"] = [bar for bar in security["bars"] if not end or bar["date"] <= end]
                    if not security["bars"]:
                        continue  # Not observed by this cutoff, never a historical candidate.
                    _retain_causal_window(security, start, lookback=selection["lookbackDays"])
                    first = security["bars"][0]["date"]
                    for key, values in list(security.items()):
                        if (key.endswith("ByDate") or key in ("stHistory", "memberHistory")) and isinstance(values, dict):
                            security[key] = {day: value for day, value in values.items() if first <= day <= end}
                    data["securities"].append(security)
                if data["securities"]:
                    first = min(s["bars"][0]["date"] for s in data["securities"])
                    data["calendar"] = [day for day in data["calendar"] if first <= day <= end]
                    data["membershipByDate"] = {day: read_part("membership", day) for day in manifest["membership"] if first <= day <= end}
                    if isinstance(data.get("benchmark"), dict):
                        data["benchmark"]["bars"] = [bar for bar in data["benchmark"]["bars"] if first <= bar["date"] <= end]
        coverage = data["historyCoverage"]
        if not start or not end or start < coverage["startDate"] or end > coverage["endDate"]:
            raise ValueError("历史数据包研究须指定其完整覆盖范围内的起止日期")
        selected = {s["symbol"] for s in data["securities"] if s["symbol"][:2] in selection["markets"]
                    and board_of(s["symbol"]) in selection["boards"]
                    and (selection["mode"] != "explicit" or s["symbol"] in selection["symbols"])}
        if selection["mode"] == "explicit" and set(selection["symbols"]) - selected:
            raise ValueError("指定股票池存在历史包未覆盖的证券或被市场/板块过滤")
        if not selected:
            raise ValueError("历史包在所选范围没有证券")
        if selection.get("benchmarkSymbol") and selection["benchmarkSymbol"] != (data.get("benchmark") or {}).get("symbol"):
            raise ValueError("所选指数基准不在历史数据包中")
        data["securities"] = [s for s in data["securities"] if s["symbol"] in selected]
        data["membershipByDate"] = {d: [s for s in members if s in selected] for d, members in data["membershipByDate"].items()}
        public_selection = {key: deepcopy(value) for key, value in selection.items() if key != "batchSize"}
        data["universeCoverage"].update(loadedCount=len(selected), selection=public_selection)
        data.pop("fingerprint", None)
        data["fingerprint"] = digest(data)
        return data
