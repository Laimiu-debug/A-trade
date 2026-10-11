"""Source-backed brokerage monitoring disclosures, never price-rule guesses.

Each source retains its own periods, including conflicting periods. Reaching
the published end date or disappearing from a feed does not prove release.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from service import trading_calendar


ZONE = dt.timezone(dt.timedelta(hours=8))
TTL = 300
MAX_BYTES = 512 * 1024
MAX_ROWS = 2000
KEEP_DAYS = 90
ENDPOINTS = {
    "eastmoney_securities": "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json",
    "gf_securities": "https://store.gf.com.cn/mixturebusiness/monitor/stock_list",
}
SOURCE_URLS = {
    "eastmoney_securities": "https://vipmoney.eastmoney.com/collect/min_data/point_stock_monitor/index.html",
    "gf_securities": "https://minisite.gf.com.cn/?src=other",
}
SOURCE_NAMES = {"eastmoney_securities": "东方财富证券重点监控名单", "gf_securities": "广发证券监控证券公示"}
GF_FOCUS = {
    "交易所交易监管中对市场公开的重点监控证券", "交易所监管提示函",
    "本所向会员发送风险警示相关通知、风险提示短信等涉及的证券",
    "本所向会员发送风险警示相关通知风险提示短信等涉及的证券",
    "交易所向会员发送风险警示相关通知、风险提示短信等涉及的证券",
    "交易所向会员发送风险警示相关通知风险提示短信等涉及的证券",
}
GF_SEVERE = {"上海严重异常波动的股票", "深圳严重异常波动的股票"}
STATUSES = ("active", "expiring", "upcoming", "elapsed", "unknown", "unlisted")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _today():
    return dt.datetime.now(ZONE).date().isoformat()


def _date(value, field):
    if value in (None, "", "2099-12-31", "9999-12-31"):
        return None
    try:
        if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError(f"监控名单的{field}日期无效") from None


def _text(value, field, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or re.search(r"[\x00-\x1f<>]", value):
        raise ValueError(f"监控名单的{field}无效")
    return value.strip()


def _notice_url(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 2000 or re.search(r"[\x00-\x20]", value):
        raise ValueError("监控名单的公告链接无效")
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        return value
    except ValueError:
        raise ValueError("监控名单的公告链接无效") from None


def _security(code, market):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise ValueError("监控名单的证券代码无效")
    prefixes = {"sh": ("60", "68", "5"), "sz": ("00", "30", "15", "16"), "bj": ("43", "83", "87", "88", "92")}
    if market not in prefixes or not code.startswith(prefixes[market]):
        raise ValueError("监控名单的证券代码与市场不匹配")
    kind = "fund" if market == "sh" and code.startswith("5") or market == "sz" and code.startswith(("15", "16")) else "stock"
    return market + code, kind


def parse_source(provider, payload):
    if provider == "eastmoney_securities":
        rows = payload
    elif provider == "gf_securities":
        if not isinstance(payload, dict) or type(payload.get("retcode")) is not int or payload["retcode"] != 0:
            raise ValueError("广发证券来源响应状态不兼容")
        rows = payload.get("data")
    else:
        raise ValueError("不支持的监控名单来源")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("监控名单响应结构或条目数量不兼容")
    items, warnings, skipped = {}, [], 0
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("监控名单条目结构不兼容")
        if provider == "gf_securities":
            title = _text(raw.get("key_monitor_stock_type"), "监控类型")
            if title not in GF_FOCUS | GF_SEVERE:
                skipped += 1
                continue
            validity = raw.get("data_status", raw.get("dataStatus"))
            if validity not in {"有效", "无效"} or "data_status" in raw and "dataStatus" in raw and raw["data_status"] != raw["dataStatus"]:
                raise ValueError("广发证券名单的有效状态不兼容")
            if validity == "无效":
                skipped += 1
                continue
            market = {"上海": "sh", "深圳": "sz", "北京": "bj"}.get(raw.get("exchange_type_desc"))
            code, name = raw.get("stock_code"), raw.get("stock_name")
            start, end = raw.get("monitor_start_date"), raw.get("monitor_end_date")
            category = "severe" if title in GF_SEVERE else "focus"
            notice = None
        else:
            market = {"0": "sz", "1": "sh", "B": "bj"}.get(raw.get("MARKET"))
            code, name = raw.get("STKCODE"), raw.get("STKNAME")
            start, end = raw.get("VALIDATESTARTDATE"), raw.get("VALIDATEENDDATE")
            category, title = "focus", "券商公示重点监控证券"
            notice = _notice_url(raw.get("LINK_URL"))
        symbol, kind = _security(code, market)
        start, end = _date(start, "开始"), _date(end, "截止")
        if start and end and end < start:
            raise ValueError("监控名单的截止日期早于开始日期")
        if start is None or end is None:
            warnings.append("部分名单期限未明确或使用占位日期，已留空，不据此计算退出倒计时。")
        identity = "|".join((provider, symbol, title, start or "unknown"))
        item = {"id": hashlib.sha256(identity.encode()).hexdigest()[:32], "provider": provider,
                "symbol": symbol, "code": code, "name": _text(name, "证券名称", 80), "market": market,
                "securityType": kind, "category": category, "title": title, "startDate": start, "endDate": end,
                "endDateKind": "expected" if provider == "eastmoney_securities" else "disclosed",
                "sourceName": SOURCE_NAMES[provider], "sourceUrl": SOURCE_URLS[provider], "noticeUrl": notice}
        old = items.get(item["id"])
        if old and old != item:
            raise ValueError("同来源同一监控期出现冲突记录，保留此前缓存等待核验")
        items[item["id"]] = item
    if skipped:
        warnings.append(f"广发证券源中 {skipped} 条普通异动、风险警示、退市或其他非目标类型未加入本页。")
    return list(items.values()), list(dict.fromkeys(warnings))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("监控名单来源返回重定向，未跟随其他地址")


def fetch_json(provider):
    if provider not in ENDPOINTS:
        raise ValueError("不支持的监控名单来源")
    request = urllib.request.Request(ENDPOINTS[provider], headers={"User-Agent": "Mozilla/5.0 A-trade/0.1",
        "Accept": "application/json", "Accept-Encoding": "identity", "Referer": SOURCE_URLS[provider]})
    began = time.monotonic()
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=5) as response:
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise ValueError("监控名单来源响应超过大小限制")
            content = bytearray()
            while True:
                if time.monotonic() - began > 12:
                    raise ValueError("监控名单来源读取超过时限")
                chunk = response.read(min(65536, MAX_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError("监控名单来源响应超过大小限制")
        return json.loads(content)
    except urllib.error.HTTPError as error:
        raise ValueError(f"公开监控名单暂不可用（HTTP {error.code}）") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("公开监控名单连接失败或超时，请稍后刷新") from None
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        raise ValueError("公开监控名单不是有效 JSON") from None


def countdown(record, as_of):
    result = dict(record)
    start, end = record["startDate"], record["endDate"]
    current = dt.date.fromisoformat(as_of)
    result.update({"remainingSessions": None, "elapsedSessions": None, "totalSessions": None,
                   "remainingCalendarDays": max(0, (dt.date.fromisoformat(end) - current).days) if end else None,
                   "calendarStatus": "unknown"})
    if start and end:
        total = trading_calendar.trading_sessions(start, end)
        remaining = trading_calendar.trading_sessions(max(start, (current + dt.timedelta(days=1)).isoformat()), end)
        elapsed = trading_calendar.trading_sessions(start, min(end, as_of))
        result.update({"remainingSessions": len(remaining) if remaining is not None else None,
                       "elapsedSessions": len(elapsed) if elapsed is not None else None,
                       "totalSessions": len(total) if total is not None else None,
                       "calendarStatus": "scheduled" if total is not None and remaining is not None and elapsed is not None else "unavailable"})
    if not record["listed"]:
        result["status"] = "unlisted"
    elif start is None or end is None:
        result["status"] = "unknown"
    elif as_of < start:
        result["status"] = "upcoming"
    elif as_of > end:
        result["status"] = "elapsed"
    elif result["remainingSessions"] is not None and result["remainingSessions"] <= 3:
        result["status"] = "expiring"
    else:
        result["status"] = "active"
    return result


class RegulatoryWatch:
    def __init__(self, market, fetcher=None):
        self.market = market
        self.fetcher = fetcher or fetch_json
        market.db.executescript("""
            CREATE TABLE IF NOT EXISTS regulatory_watch_sources (
                provider TEXT PRIMARY KEY, received_at TEXT NOT NULL, warnings TEXT NOT NULL,
                last_error TEXT);
            CREATE TABLE IF NOT EXISTS regulatory_watch_records (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, listed INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, data TEXT NOT NULL);
        """)
        if "last_error" not in {row[1] for row in market.db.execute("PRAGMA table_info(regulatory_watch_sources)")}:
            market.db.execute("ALTER TABLE regulatory_watch_sources ADD COLUMN last_error TEXT")
        market.db.commit()

    def close(self):
        pass

    def _refresh(self, provider, now, as_of):
        # Validate the complete feed before making any lifecycle/cache mutations.
        items, warnings = parse_source(provider, self.fetcher(provider))
        old = {row["id"]: row for row in self.market.db.execute("SELECT * FROM regulatory_watch_records WHERE provider=?", (provider,)).fetchall()}
        prepared = []
        for item in items:
            previous = old.get(item["id"])
            first = previous["first_seen_at"] if previous else now
            if previous:
                prior = json.loads(previous["data"])
                if prior["endDate"] != item["endDate"]:
                    item.update({"previousEndDate": prior["endDate"], "periodChanged": True})
                elif prior.get("periodChanged"):
                    item.update({"previousEndDate": prior.get("previousEndDate"), "periodChanged": True})
            prepared.append((item["id"], provider, 1, first, now, json.dumps(item, ensure_ascii=False, allow_nan=False)))
        cutoff = (dt.date.fromisoformat(as_of) - dt.timedelta(days=KEEP_DAYS)).isoformat()
        with self.market.db:
            self.market.db.execute("UPDATE regulatory_watch_records SET listed=0 WHERE provider=?", (provider,))
            self.market.db.executemany("INSERT OR REPLACE INTO regulatory_watch_records VALUES (?,?,?,?,?,?)", prepared)
            self.market.db.execute("DELETE FROM regulatory_watch_records WHERE provider=? AND listed=0 AND substr(last_seen_at,1,10)<?", (provider, cutoff))
            self.market.db.execute("INSERT OR REPLACE INTO regulatory_watch_sources(provider,received_at,warnings,last_error) VALUES (?,?,?,NULL)", (provider, now, json.dumps(warnings, ensure_ascii=False)))

    def list(self, payload=None):
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or set(payload) - {"refresh"} or type(payload.get("refresh", False)) is not bool:
            raise ValueError("监控名单只支持 refresh 参数；当前名单不支持历史截至日查询")
        now, as_of = _now(), _today()
        now_stamp = dt.datetime.fromisoformat(now)
        sources, errors, warnings, items = [], [], [], []
        for provider in ENDPOINTS:
            stored = self.market.db.execute("SELECT * FROM regulatory_watch_sources WHERE provider=?", (provider,)).fetchone()
            fresh = stored and 0 <= (now_stamp - dt.datetime.fromisoformat(stored["received_at"])).total_seconds() < TTL
            cached, stale, error = bool(fresh), bool(stored and stored["last_error"]), stored["last_error"] if stored else None
            if payload.get("refresh") or not fresh:
                try:
                    self._refresh(provider, now, as_of)
                    stored = self.market.db.execute("SELECT * FROM regulatory_watch_sources WHERE provider=?", (provider,)).fetchone()
                    cached, stale, error = False, False, None
                except (ValueError, TypeError, KeyError) as exc:
                    error = str(exc) if isinstance(exc, ValueError) else "监控名单来源字段不兼容"
                    cached, stale = bool(stored), bool(stored)
                    if stored:
                        with self.market.db:
                            self.market.db.execute("UPDATE regulatory_watch_sources SET last_error=? WHERE provider=?", (error, provider))
            if error:
                errors.append({"provider": provider, "message": error})
            source_items = []
            if stored:
                warnings.extend(json.loads(stored["warnings"]))
                cutoff = (dt.date.fromisoformat(as_of) - dt.timedelta(days=KEEP_DAYS)).isoformat()
                rows = self.market.db.execute("SELECT * FROM regulatory_watch_records WHERE provider=? AND (listed=1 OR substr(last_seen_at,1,10)>=?)", (provider, cutoff)).fetchall()
                for row in rows:
                    record = json.loads(row["data"])
                    record.update({"listed": bool(row["listed"]), "firstSeenAt": row["first_seen_at"], "lastSeenAt": row["last_seen_at"],
                                   "receivedAt": stored["received_at"], "cached": cached, "stale": stale})
                    source_items.append(countdown(record, as_of))
                if stale:
                    warnings.append(f"{SOURCE_NAMES[provider]}本次读取失败，保留 {stored['received_at']} 获取的旧名单；是否仍被公示尚待更新。")
            sources.append({"provider": provider, "sourceName": SOURCE_NAMES[provider], "sourceUrl": SOURCE_URLS[provider],
                            "receivedAt": stored["received_at"] if stored else None, "cached": cached, "stale": stale,
                            "status": "ready" if stored else "unavailable", "listedCount": sum(item["listed"] for item in source_items), "error": error})
            items.extend(source_items)
        order = {"expiring": 0, "active": 1, "upcoming": 2, "unknown": 3, "elapsed": 4, "unlisted": 5}
        items.sort(key=lambda item: (order[item["status"]], item["remainingSessions"] is None,
                                    item["remainingSessions"] if item["remainingSessions"] is not None else 0,
                                    item["endDate"] or "9999", item["symbol"], item["provider"], item["id"]))
        counts = {status: sum(item["status"] == status for item in items) for status in STATUSES}
        counts.update({"focus": sum(item["category"] == "focus" for item in items), "severe": sum(item["category"] == "severe" for item in items),
                       "listed": sum(item["listed"] for item in items), "securities": len({item["symbol"] for item in items})})
        received = [source["receivedAt"] for source in sources if source["receivedAt"]]
        warnings.extend(["券商公示为来源提供的监控证券线索；不同来源及不同监控类型分别保留，不代表完整交易所监管池。",
                         "东方财富截止日为预计日期，广发为公示期限；期限届满或名单移除不等于交易所已解除监管。",
                         "倒计时不含今天、含截止日，按已公布年度休市安排计算；临时休市或后续延长须以新公告为准。"])
        if any(item["calendarStatus"] == "unavailable" for item in items):
            warnings.append("部分期限超出已核验交易日历年份，交易日倒计时留空，不按工作日推算。")
        return {"status": "ready" if received else "unavailable", "provider": "public_brokerage_notices",
                "sourceName": "券商公开监控证券名单", "sourceUrl": SOURCE_URLS["eastmoney_securities"], "sources": sources,
                "asOf": as_of, "receivedAt": min(received) if received else None,
                "cached": any(source["cached"] for source in sources), "stale": any(source["stale"] for source in sources),
                "warnings": list(dict.fromkeys(warnings)), "errors": errors, "total": len(items), "counts": counts, "items": items,
                "calendarSources": trading_calendar.CALENDAR_SOURCES, "retentionDays": KEEP_DAYS}
