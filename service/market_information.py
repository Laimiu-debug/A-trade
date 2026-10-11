"""Bounded public information adapters; no fabricated news, earnings or fund flows."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request


ZONE = dt.timezone(dt.timedelta(hours=8))
ENDPOINTS = {
    "news": "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
    "announcements": "https://np-anotice-stock.eastmoney.com/api/security/ann",
    "finance": "https://datacenter-web.eastmoney.com/api/data/v1/get",
    "quote": "https://push2.eastmoney.com/api/qt/stock/get",
    "funds": "https://push2.eastmoney.com/api/qt/clist/get",
    "funds_chart": "https://data.eastmoney.com/dataapi/bkzj/getbkzj",
    "funds_history": "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
}
PAGES = {"news": "https://kuaixun.eastmoney.com/", "announcements": "https://data.eastmoney.com/notices/",
         "finance": "https://data.eastmoney.com/bbsj/", "funds": "https://data.eastmoney.com/bkzj/hy.html"}
MAX_BYTES = 2 * 1024 * 1024
COLUMNS = "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,REPORTDATE,NOTICE_DATE,PARENT_NETPROFIT"
FINANCE_NOTE = "归母净利润采用累计财报构造 TTM；未取得完整同期与年报时保持缺失，不使用市盈率倒推。"
FUNDS_NOTE = "资金净流入为东方财富按成交单大小划分的估算口径；主力为超大单与大单之和，不是实际机构账户资金。"
VALUATION_COLUMNS = "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_CODE,BOARD_NAME,PE_TTM,TRADE_DATE"


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _today():
    return dt.datetime.now(ZONE).date().isoformat()


def _num(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _date(value):
    if not isinstance(value, str):
        return None
    raw = value[:10]
    try:
        return raw if dt.date.fromisoformat(raw).isoformat() == raw else None
    except ValueError:
        return None


def _stamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(value, ZONE).isoformat() if 946684800 <= value <= time.time() + 300 else None
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(value, str):
        return None
    try:
        # Announcement display_time sometimes appends milliseconds after a colon.
        raw = value.strip()
        if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d:\d{3}", raw):
            raw = raw[:19]
        parsed = dt.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZONE)
        return parsed.isoformat() if parsed <= dt.datetime.now(ZONE) + dt.timedelta(minutes=5) else None
    except (ValueError, OverflowError):
        return None


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit=240):
    parser = _Plain()
    parser.feed(html.unescape(str(value or ""))[:20000])
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:limit]


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", value):
        raise ValueError("请选择带市场的证券代码，例如 sh600900")
    return value


def _reported_symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", value):
        return None
    code, market = value.split(".")
    return market.lower() + code


def _pe_statistics(values):
    """Linear interpolated sample quantiles; no winsorization or invented multiples."""
    ordered = sorted(value for value in values if value is not None and value > 0)
    def quantile(fraction):
        if not ordered:
            return None
        position = (len(ordered) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 6)
    return {"medianPE": quantile(.5), "p25PE": quantile(.25), "p75PE": quantile(.75),
            "meanPE": round(math.fsum(value / len(ordered) for value in ordered), 6) if ordered else None,
            "sampleCount": len(ordered)}


def _valuation_page(payload):
    result = payload.get("result")
    if payload.get("success") is not True or not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ValueError("估值来源响应结构不兼容")
    rows, count, pages = result["data"], result.get("count"), result.get("pages")
    if type(count) is not int or type(pages) is not int or count < 0 or pages < 0 or len(rows) > 500 or not all(isinstance(row, dict) for row in rows):
        raise ValueError("估值来源分页信息不兼容")
    return rows, count, pages


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("来源返回重定向，未跟随到其他地址")


def fetch_json(endpoint, params):
    """Only these fixed public HTTPS endpoints can ever be requested."""
    if endpoint not in ENDPOINTS:
        raise ValueError("不支持的信息来源")
    url = ENDPOINTS[endpoint] + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "A-trade/0.1",
        "Accept": "application/json", "Referer": "https://data.eastmoney.com/", "Accept-Encoding": "identity"})
    started = time.monotonic()
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=5) as response:
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise ValueError("来源响应超过大小限制")
            content = bytearray()
            while True:
                if time.monotonic() - started > 12:
                    raise ValueError("来源读取超过时限")
                chunk = response.read(min(65536, MAX_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError("来源响应超过大小限制")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("来源响应结构不兼容")
        return data
    except urllib.error.HTTPError as error:
        raise ValueError(f"公开来源暂不可用（HTTP {error.code}）") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ValueError("公开来源连接失败或超时，请稍后刷新") from None
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        raise ValueError("来源响应不是有效 JSON") from None


def parse_reports(payload):
    result = payload.get("result")
    if payload.get("success") is not True or not isinstance(result, dict) or not isinstance(result.get("data"), list):
        # The public API uses null result for a legitimate zero-row filter.
        if payload.get("success") is True and result is None:
            return [], 0, 0
        raise ValueError("财报来源响应结构不兼容")
    raw_rows = result["data"]
    count, pages = result.get("count"), result.get("pages")
    if type(count) is not int or type(pages) is not int or count < 0 or pages < 0 or len(raw_rows) > 500:
        raise ValueError("财报分页信息不兼容")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError("财报条目格式不兼容")
        symbol = _reported_symbol(raw.get("SECUCODE"))
        report = _date(raw.get("REPORTDATE"))
        announced = _date(raw.get("NOTICE_DATE"))
        if symbol is None or raw.get("SECURITY_CODE") != symbol[2:] or report is None or report[5:] not in ("03-31", "06-30", "09-30", "12-31"):
            raise ValueError("财报证券身份或报告期不兼容")
        if announced is None or report > _today() or announced > _today() or announced < report:
            raise ValueError("财报公告日期不明确或尚未发生")
        rows.append({"symbol": symbol, "name": _text(raw.get("SECURITY_NAME_ABBR"), 80),
                     "industry": _text(raw.get("BOARD_NAME"), 80) or None,
                     "reportDate": report, "announcementDate": announced,
                     "parentNetProfitYTD": _num(raw.get("PARENT_NETPROFIT"))})
    return rows, count, pages


def ttm_snapshot(symbol, reports):
    """Current YTD + previous annual - prior-year same YTD, never sum four YTDs."""
    relevant = [row for row in reports if row["symbol"] == symbol]
    relevant.sort(key=lambda row: (row["reportDate"], row["announcementDate"]), reverse=True)
    latest = relevant[0] if relevant else None
    result = {"symbol": symbol, "name": latest["name"] if latest else symbol[2:],
              "industry": latest["industry"] if latest else None, "marketCap": None, "price": None,
              "quoteAsOf": None, "quoteReceivedAt": None, "peTTM": None,
              "ttmNetProfit": None, "reportDate": latest["reportDate"] if latest else None,
              "announcementDate": latest["announcementDate"] if latest else None,
              "ttmMethod": None, "components": [], "missing": [],
              "units": {"ttmNetProfit": "元", "marketCap": "元", "price": "元"}}
    if not latest:
        result["missing"].append("没有可用的已披露财报")
        return result
    by_date = {}
    for row in relevant:
        by_date.setdefault(row["reportDate"], row)
    report = latest["reportDate"]
    if report.endswith("12-31"):
        required = [(report, 1, "全年归母净利润")]
        result["ttmMethod"] = "annual"
    else:
        year = int(report[:4])
        required = [(report, 1, "本年累计归母净利润"), (f"{year - 1}-12-31", 1, "上年全年归母净利润"),
                    (f"{year - 1}{report[4:]}", -1, "上年同期累计归母净利润")]
        result["ttmMethod"] = "current_ytd_plus_previous_annual_minus_previous_ytd"
    for day, sign, label in required:
        row = by_date.get(day)
        result["components"].append({"reportDate": day, "sign": sign, "label": label,
            "netProfit": row["parentNetProfitYTD"] if row else None, "announcementDate": row["announcementDate"] if row else None})
        if row is None or row["parentNetProfitYTD"] is None:
            result["missing"].append(f"缺少 {day} 的累计归母净利润")
    if not result["missing"]:
        value = sum(row["netProfit"] * row["sign"] for row in result["components"])
        result["ttmNetProfit"] = round(value, 2) if math.isfinite(value) else None
    return result


class MarketInformation:
    def __init__(self, owner, fetcher=None):
        self.owner = owner
        self.fetcher = fetcher or fetch_json
        self.deadline = 0
        self.calls = 0
        self.request_limit = 30
        self._cache_depth = 0
        owner.db.execute("CREATE TABLE IF NOT EXISTS information_cache (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at TEXT NOT NULL)")
        owner.db.commit()

    def close(self):
        pass

    def _request(self, endpoint, params):
        self.calls += 1
        if self.calls > self.request_limit or time.monotonic() > self.deadline:
            raise ValueError("本次读取已到请求数量或时间上限，请稍后刷新")
        return self.fetcher(endpoint, params)

    def _read_cache(self, key):
        row = self.owner.db.execute("SELECT payload FROM information_cache WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _cached(self, key, provider, source_name, url, ttl, refresh, loader, empty, budget=(30, 60)):
        key = "public-information-v2|" + key
        previous = self._read_cache(key)
        if previous and not refresh:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(previous["receivedAt"])).total_seconds()
            if age < ttl:
                previous["cached"] = True
                return previous
        if self._cache_depth == 0:
            self.deadline = time.monotonic() + budget[1]
            self.calls = 0
            self.request_limit = budget[0]
        self._cache_depth += 1
        try:
            data = loader()
            received = _now()
            result = {**data, "provider": provider, "sourceName": source_name, "sourceUrl": url,
                "receivedAt": received, "cached": False, "stale": False,
                "status": data.get("status") or ("ready" if data.get("items") or data.get("reportDate") else "empty"),
                "warnings": list(data.get("warnings", [])), "errors": list(data.get("errors", []))}
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            with self.owner.db:
                self.owner.db.execute("INSERT OR REPLACE INTO information_cache VALUES (?,?,?)", (key, encoded, received))
                self.owner.db.execute("DELETE FROM information_cache WHERE cache_key NOT IN (SELECT cache_key FROM information_cache ORDER BY received_at DESC LIMIT 300)")
            return result
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            message = str(error) if isinstance(error, ValueError) else "来源字段结构已变化，未采用本次响应"
            result = previous or {**empty, "provider": provider, "sourceName": source_name, "sourceUrl": url,
                                  "receivedAt": None, "warnings": [], "errors": []}
            result.update(cached=previous is not None, stale=previous is not None,
                          status=previous.get("status", "ready") if previous else "unavailable")
            result["errors"] = [{"provider": provider, "message": message}]
            result["warnings"] = list(result.get("warnings", [])) + [
                "本次更新失败，保留上次有效数据及原采集时间。" if previous else "来源暂不可用，当前没有有效缓存。"]
            return result
        finally:
            self._cache_depth -= 1

    @staticmethod
    def _payload(payload, allowed):
        if payload is None:
            payload = {}
        if not isinstance(payload, dict) or set(payload) - set(allowed):
            raise ValueError("包含不支持的请求参数")
        if "refresh" in payload and type(payload["refresh"]) is not bool:
            raise ValueError("refresh 必须是布尔值")
        return payload

    @staticmethod
    def _page(payload, size=20):
        page, page_size = payload.get("page", 1), payload.get("pageSize", size)
        if type(page) is not int or not 1 <= page <= 400 or type(page_size) is not int or not 1 <= page_size <= 30:
            raise ValueError("请选择有效分页：每页最多 30 项")
        return page, page_size

    def _report_page(self, filters, page=1, size=500):
        return parse_reports(self._request("finance", {"reportName": "RPT_LICO_FN_CPD", "columns": COLUMNS,
            "filter": filters, "sortColumns": "SECURITY_CODE,REPORTDATE", "sortTypes": "1,-1", "pageSize": size, "pageNumber": page}))

    def _live_quote(self, symbol):
        payload = self._request("quote", {"secid": ("1." if symbol.startswith("sh") else "0.") + symbol[2:],
            "fields": "f57,f58,f43,f116,f164,f127,f124", "fltt": "2"})
        data = payload.get("data")
        if payload.get("rc") != 0 or not isinstance(data, dict) or data.get("f57") != symbol[2:]:
            raise ValueError("市值来源证券身份不匹配或暂无响应")
        price, market_cap = _num(data.get("f43")), _num(data.get("f116"))
        if price is None or price <= 0 or market_cap is None or market_cap <= 0:
            raise ValueError("市值来源未提供有效价格和总市值")
        return {"price": price if price is not None and price > 0 else None,
                "marketCap": market_cap if market_cap is not None and market_cap > 0 else None,
                "peTTM": _num(data.get("f164")), "quoteAsOf": _stamp(data.get("f124")), "quoteReceivedAt": _now(),
                "quoteProvider": "eastmoney_quote", "quoteSourceUrl": "https://quote.eastmoney.com/" + symbol + ".html"}

    def _daily_quote(self, symbol):
        payload = self._request("finance", {"reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECUCODE,SECURITY_CODE,TOTAL_MARKET_CAP,CLOSE_PRICE,PE_TTM,TRADE_DATE",
            "filter": f'(SECUCODE="{symbol[2:]}.{symbol[:2].upper()}")', "sortColumns": "TRADE_DATE",
            "sortTypes": "-1", "pageSize": 1, "pageNumber": 1})
        result = payload.get("result")
        rows = result.get("data") if isinstance(result, dict) else None
        if payload.get("success") is not True or not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("日收盘估值来源暂无有效快照")
        return self._daily_quote_row(rows[0], symbol)

    @staticmethod
    def _daily_quote_row(row, symbol):
        if not isinstance(row, dict) or _reported_symbol(row.get("SECUCODE")) != symbol or row.get("SECURITY_CODE") != symbol[2:]:
            raise ValueError("日收盘估值证券身份不匹配")
        price, cap, day = _num(row.get("CLOSE_PRICE")), _num(row.get("TOTAL_MARKET_CAP")), _date(row.get("TRADE_DATE"))
        if price is None or price <= 0 or cap is None or cap <= 0 or day is None or day > _today():
            raise ValueError("日收盘估值价格、市值或交易日无效")
        return {"price": price, "marketCap": cap, "peTTM": _num(row.get("PE_TTM")), "quoteAsOf": day,
            "quoteReceivedAt": _now(), "quoteProvider": "eastmoney_daily_valuation",
            "quoteSourceUrl": "https://data.eastmoney.com/gzfx/detail/" + symbol[2:] + ".html"}

    def _batch_daily_quotes(self, symbols, refresh):
        symbols = sorted(set(symbols))
        if not symbols:
            return {"items": [], "warnings": [], "errors": [], "cached": False, "stale": False}
        key = "finance-screen-quotes|" + hashlib.sha256(",".join(symbols).encode()).hexdigest()
        def load():
            codes = ",".join('"' + symbol[2:] + '"' for symbol in symbols)
            filters = f"(SECURITY_CODE in ({codes}))"
            params = {"reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "SECUCODE,SECURITY_CODE,TOTAL_MARKET_CAP,CLOSE_PRICE,PE_TTM,TRADE_DATE",
                "filter": filters, "sortColumns": "TRADE_DATE", "sortTypes": "-1", "pageSize": 1, "pageNumber": 1}
            latest = self._request("finance", params)
            latest_rows = latest.get("result", {}).get("data") if isinstance(latest.get("result"), dict) else None
            if latest.get("success") is not True or not isinstance(latest_rows, list) or len(latest_rows) != 1:
                raise ValueError("本页证券尚无可用的日收盘估值")
            first = latest_rows[0]
            symbol = _reported_symbol(first.get("SECUCODE")) if isinstance(first, dict) else None
            if symbol not in symbols:
                raise ValueError("日估值交易日查询证券身份不匹配")
            day = self._daily_quote_row(first, symbol)["quoteAsOf"]
            params.update(filter=filters + f"(TRADE_DATE='{day}')", pageSize=500)
            payload = self._request("finance", params)
            result = payload.get("result")
            if payload.get("success") is not True or not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("本页日收盘估值来源结构不兼容")
            rows = result["data"]
            if type(result.get("count")) is not int or len(rows) != result["count"] or len(rows) > len(symbols) or type(result.get("pages")) is not int or result["pages"] != 1:
                raise ValueError("本页日收盘估值分页不完整")
            items, seen = [], set()
            for row in rows:
                symbol = _reported_symbol(row.get("SECUCODE")) if isinstance(row, dict) else None
                if symbol not in symbols or symbol in seen:
                    raise ValueError("本页日收盘估值证券重复或身份不匹配")
                quote = self._daily_quote_row(row, symbol)
                if quote["quoteAsOf"] != day:
                    raise ValueError("本页日收盘估值交易日不一致")
                seen.add(symbol)
                items.append({"symbol": symbol, **quote})
            return {"items": items, "asOf": day,
                    "warnings": ["本页市值与价格采用来源同一最近交易日收盘快照，不是实时行情；无当日估值的证券保持缺失。"]}
        return self._cached(key, "eastmoney_daily_valuation", "东方财富日收盘估值", "https://data.eastmoney.com/gzfx/",
                            300, refresh, load, {"items": [], "asOf": None})

    def _quote(self, symbol, refresh):
        def load():
            try:
                quote = self._live_quote(symbol)
                quote["warnings"] = [] if quote["quoteAsOf"] else ["市值快照未提供行情时间，仅记录采集时间。"]
            except ValueError as error:
                quote = self._daily_quote(symbol)
                quote["warnings"] = ["即时市值接口暂不可用，采用来源最近交易日收盘价格与总市值；不是实时行情。"]
                quote["errors"] = [{"provider": "eastmoney_quote", "message": str(error)}]
            quote["status"] = "ready"
            return quote
        return self._cached("finance-quote|" + symbol, "eastmoney_valuation", "东方财富估值", PAGES["finance"],
            300, refresh, load, {"price": None, "marketCap": None, "peTTM": None,
                              "quoteAsOf": None, "quoteReceivedAt": None, "quoteProvider": None, "quoteSourceUrl": None})

    def finance_snapshot(self, payload=None):
        payload = self._payload(payload, {"symbol", "refresh"})
        symbol = _symbol(payload.get("symbol"))
        def load():
            rows, _, _ = self._report_page(f'(SECUCODE="{symbol[2:]}.{symbol[:2].upper()}")', size=12)
            if any(row["symbol"] != symbol for row in rows):
                raise ValueError("财报来源返回了其他证券")
            result = ttm_snapshot(symbol, rows)
            result["warnings"] = [FINANCE_NOTE]
            quote = self._quote(symbol, payload.get("refresh", False))
            result.update({key: quote.get(key) for key in ("price", "marketCap", "peTTM", "quoteAsOf", "quoteReceivedAt", "quoteProvider", "quoteSourceUrl")})
            result.update(quoteCached=quote["cached"], quoteStale=quote["stale"])
            result["warnings"].extend(quote["warnings"])
            result["errors"] = quote["errors"]
            return result
        return self._cached("finance|" + symbol, "eastmoney_finance", "东方财富财报", PAGES["finance"], 300,
                            payload.get("refresh", False), load, ttm_snapshot(symbol, []))

    def _industry_peers(self, industry_id, industry, day, refresh):
        def load():
            params = {"reportName": "RPT_VALUEANALYSIS_DET", "columns": VALUATION_COLUMNS,
                "filter": f'(BOARD_CODE="{industry_id}")(TRADE_DATE=\'{day}\')',
                "sortColumns": "SECURITY_CODE", "sortTypes": "1", "pageSize": 500, "pageNumber": 1}
            rows, total, pages = _valuation_page(self._request("finance", params))
            if pages > 4 or total > 2000:
                raise ValueError("行业估值样本超过当前读取上限")
            for page in range(2, pages + 1):
                more, count, page_count = _valuation_page(self._request("finance", {**params, "pageNumber": page}))
                if count != total or page_count != pages:
                    raise ValueError("行业估值分页更新中，请稍后刷新")
                rows.extend(more)
            if len(rows) != total:
                raise ValueError("行业估值样本分页不完整")
            items, seen = [], set()
            for row in rows:
                symbol = _reported_symbol(row.get("SECUCODE"))
                if symbol is None or row.get("SECURITY_CODE") != symbol[2:] or symbol in seen:
                    raise ValueError("行业估值证券身份重复或不匹配")
                if row.get("BOARD_CODE") != industry_id or _text(row.get("BOARD_NAME"), 80) != industry or _date(row.get("TRADE_DATE")) != day:
                    raise ValueError("行业估值样本行业或交易日不一致")
                seen.add(symbol)
                items.append({"symbol": symbol, "name": _text(row.get("SECURITY_NAME_ABBR"), 80) or symbol[2:],
                              "peTTM": _num(row.get("PE_TTM")), "asOf": day})
            values = [row["peTTM"] for row in items]
            statistics = _pe_statistics(values)
            return {"items": items, "industry": industry, "industryId": industry_id, "asOf": day,
                **statistics, "totalCount": len(items),
                "excludedNonPositiveCount": sum(value is not None and value <= 0 for value in values),
                "excludedMissingCount": sum(value is None for value in values),
                "warnings": (["正 PE 同行不足 3 家，分布代表性有限。"] if statistics["sampleCount"] < 3 else [])}
        return self._cached(f"industry-peers|{industry_id}|{day}", "eastmoney_daily_valuation", "东方财富同行收盘估值",
            "https://data.eastmoney.com/gzfx/", 3600, refresh, load,
            {"items": [], "industry": industry, "industryId": industry_id, "asOf": day})

    def _industry_pe_history(self, industry_id, industry, as_of, refresh):
        def load():
            payload = self._request("finance", {"reportName": "RPT_VALUEINDUSTRY_DET",
                "columns": "BOARD_CODE,BOARD_NAME,TRADE_DATE,PE_TTM",
                "filter": f'(BOARD_CODE="{industry_id}")(TRADE_DATE<=\'{as_of}\')',
                "sortColumns": "TRADE_DATE", "sortTypes": "-1", "pageSize": 250, "pageNumber": 1})
            rows, _, _ = _valuation_page(payload)
            if len(rows) > 250:
                raise ValueError("行业历史估值条目超出读取范围")
            items, seen = [], set()
            for row in rows:
                day = _date(row.get("TRADE_DATE"))
                if day is None or day > as_of or day in seen or row.get("BOARD_CODE") != industry_id or _text(row.get("BOARD_NAME"), 80) != industry:
                    raise ValueError("行业历史估值日期或行业身份不匹配")
                seen.add(day)
                items.append({"date": day, "peTTM": _num(row.get("PE_TTM"))})
            items.sort(key=lambda row: row["date"])
            values = [row["peTTM"] for row in items]
            statistics = _pe_statistics(values)
            return {"metric": "provider_industry_pe_ttm", "items": items, **statistics,
                "days": len(items), "startDate": items[0]["date"] if items else None,
                "endDate": items[-1]["date"] if items else None, "currentPE": items[-1]["peTTM"] if items else None,
                "excludedNonPositiveCount": sum(value is not None and value <= 0 for value in values),
                "excludedMissingCount": sum(value is None for value in values),
                "warnings": ["历史参考采用来源行业整体 PE(TTM) 的最近 250 个已提供交易日，与当前同行个股 PE 分布口径不同；不是公允或保证回归的倍数。"]}
        return self._cached(f"industry-pe-history|{industry_id}|{as_of}", "eastmoney_industry_valuation",
            "东方财富行业历史估值", "https://data.eastmoney.com/gzfx/", 3600, refresh, load,
            {"metric": "provider_industry_pe_ttm", "items": [], "days": 0, "sampleCount": 0,
             "medianPE": None, "p25PE": None, "p75PE": None, "meanPE": None,
             "startDate": None, "endDate": None, "currentPE": None})

    def finance_benchmark(self, payload=None):
        payload = self._payload(payload, {"symbol", "refresh"})
        symbol = _symbol(payload.get("symbol"))
        refresh = payload.get("refresh", False)
        def load():
            rows, _, _ = _valuation_page(self._request("finance", {"reportName": "RPT_VALUEANALYSIS_DET",
                "columns": VALUATION_COLUMNS, "filter": f'(SECUCODE="{symbol[2:]}.{symbol[:2].upper()}")',
                "sortColumns": "TRADE_DATE", "sortTypes": "-1", "pageSize": 1, "pageNumber": 1}))
            if len(rows) != 1:
                raise ValueError("尚未取得该证券的日收盘行业估值")
            row = rows[0]
            if _reported_symbol(row.get("SECUCODE")) != symbol or row.get("SECURITY_CODE") != symbol[2:]:
                raise ValueError("行业基准证券身份不匹配")
            day, industry_id, industry = _date(row.get("TRADE_DATE")), row.get("BOARD_CODE"), _text(row.get("BOARD_NAME"), 80)
            if day is None or day > _today() or not isinstance(industry_id, str) or not re.fullmatch(r"\d{6}", industry_id) or not industry:
                raise ValueError("行业基准交易日或行业分类缺失")
            peers = self._industry_peers(industry_id, industry, day, refresh)
            if peers["status"] == "unavailable" or peers["stale"]:
                raise ValueError(peers["errors"][0]["message"] if peers["errors"] else "同行估值尚未读取")
            subject = next((item for item in peers["items"] if item["symbol"] == symbol), None)
            if subject is None:
                raise ValueError("行业估值样本缺少所选证券，目录可能正在更新")
            history = self._industry_pe_history(industry_id, industry, day, refresh)
            keys = ("industry", "industryId", "asOf", "medianPE", "p25PE", "p75PE", "meanPE", "sampleCount", "totalCount",
                    "excludedNonPositiveCount", "excludedMissingCount", "items")
            return {**{key: peers[key] for key in keys}, "symbol": symbol,
                "name": _text(row.get("SECURITY_NAME_ABBR"), 80) or symbol[2:],
                "subjectPE": subject["peTTM"], "method": "same_day_positive_ttm_pe", "includesSubject": True,
                "historical": history, "historicalMedianPE": history.get("medianPE"),
                "historicalNote": "行业历史 PE 和当前同行个股 PE 为两个独立口径，仅用于比较参考。",
                "units": {"peTTM": "倍"}, "errors": history["errors"],
                "warnings": peers["warnings"] + history["warnings"] + [
                    "当前同行参考采用来源估值行业分类、同一交易日收盘 PE(TTM)，包含所选证券；排除非正值与缺失值，不混入静态 PE。",
                    "中位数和 25%—75% 分位反映样本分布；均值可能受极高 PE 影响，不代表合理估值或收益预测。"]}
        return self._cached("finance-benchmark|" + symbol, "eastmoney_daily_valuation", "东方财富行业估值参考",
            "https://data.eastmoney.com/gzfx/detail/" + symbol[2:] + ".html", 3600, refresh, load,
            {"symbol": symbol, "name": symbol[2:], "industry": None, "industryId": None, "asOf": None,
             "medianPE": None, "p25PE": None, "p75PE": None, "meanPE": None, "sampleCount": 0, "totalCount": 0,
             "excludedNonPositiveCount": 0, "excludedMissingCount": 0, "subjectPE": None, "items": [],
             "method": "same_day_positive_ttm_pe", "includesSubject": True, "historical": None, "historicalMedianPE": None})

    def _catalog(self, refresh=False):
        def load():
            self._progress("整理财务目录", 0, 1)
            rows, total, pages = self._report_page('(ISNEW="1")(SECURITY_TYPE_CODE="058001001")')
            if pages > 20 or total > 10000:
                raise ValueError("财报目录超过当前读取上限")
            for page in range(2, pages + 1):
                more, next_total, next_pages = self._report_page('(ISNEW="1")(SECURITY_TYPE_CODE="058001001")', page)
                if next_total != total or next_pages != pages:
                    raise ValueError("财报目录更新中，请稍后重试")
                rows.extend(more)
                self._progress("整理财务目录", page, pages)
            if len(rows) != total or len({row["symbol"] for row in rows}) != len(rows):
                raise ValueError("财报目录存在重复或分页缺口")
            return {"items": rows, "total": total, "warnings": []}
        return self._cached("finance-catalog", "eastmoney_finance", "东方财富财报", PAGES["finance"], 21600,
                            refresh, load, {"items": [], "total": 0})

    def _progress(self, stage, completed, total):
        callback = getattr(self.owner, "progress", None)
        if callable(callback):
            callback(stage, completed, total)

    def _screen_universe(self, refresh=False):
        """Publish only a fully paged financial universe and same-day quotes.

        Filters share this snapshot, including across worker processes. Missing
        financial components remain explicit; a failed source page never becomes
        a new partial cross-market ranking.
        """
        def load():
            catalog = self._catalog(refresh)
            if catalog["status"] == "unavailable" or catalog["stale"]:
                raise ValueError(catalog["errors"][0]["message"] if catalog["errors"] else "财报目录尚未读取")
            current = catalog["items"]
            required = {}
            for row in current:
                if not row["reportDate"].endswith("12-31"):
                    year = int(row["reportDate"][:4]) - 1
                    for day in (f"{year}-12-31", f"{year}{row['reportDate'][4:]}"):
                        required.setdefault(day, set()).add(row["symbol"])
            # Common periods use a complete A-share report directory. Rare old
            # periods use bounded symbol filters, avoiding years of unrelated data.
            groups = {}
            for day, symbols in required.items():
                identity = tuple(sorted(symbols)) if len(symbols) <= 100 else None
                groups.setdefault(identity, []).append(day)
            history = {}
            for index, (identities, days) in enumerate(groups.items()):
                self._progress("补充 TTM 财报", index, len(groups))
                dates = ",".join("'" + day + "'" for day in sorted(days))
                filters = f"(REPORTDATE in ({dates}))"
                if identities is None:
                    filters += '(SECURITY_TYPE_CODE="058001001")'
                else:
                    codes = ",".join('"' + symbol[2:] + '"' for symbol in identities)
                    filters += f"(SECURITY_CODE in ({codes}))"
                rows, total, pages = self._report_page(filters)
                if total > 40000 or pages > 80:
                    raise ValueError("完整财报补充超出读取上限，未发布部分候选")
                for page in range(2, pages + 1):
                    more, count, page_count = self._report_page(filters, page)
                    if count != total or page_count != pages:
                        raise ValueError("全候选历史财报分页正在变化，请稍后刷新")
                    rows.extend(more)
                    self._progress("补充 TTM 财报", page, pages)
                if len(rows) != total:
                    raise ValueError("全候选历史财报分页不完整")
                seen = set()
                for row in rows:
                    if row["reportDate"] not in days or identities is not None and row["symbol"] not in identities:
                        raise ValueError("历史财报证券或报告期与请求不匹配")
                    revision = (row["symbol"], row["reportDate"], row["announcementDate"])
                    if revision in seen:
                        raise ValueError("全候选历史财报分页存在重复版本")
                    seen.add(revision)
                    if row["symbol"] in required[row["reportDate"]]:
                        history.setdefault(row["symbol"], []).append(row)
            items = [ttm_snapshot(row["symbol"], [row] + history.get(row["symbol"], [])) for row in current]
            self._progress("读取全候选同日估值", 0, 1)
            quotes, as_of = {}, None
            if items:
                recent = (dt.date.fromisoformat(_today()) - dt.timedelta(days=40)).isoformat()
                params = {"reportName": "RPT_VALUEANALYSIS_DET",
                          "columns": "SECUCODE,SECURITY_CODE,TOTAL_MARKET_CAP,CLOSE_PRICE,PE_TTM,TRADE_DATE",
                          "filter": f"(TRADE_DATE>='{recent}')", "sortColumns": "TRADE_DATE",
                          "sortTypes": "-1", "pageSize": 1, "pageNumber": 1}
                latest, _, _ = _valuation_page(self._request("finance", params))
                if len(latest) != 1 or not isinstance(latest[0], dict):
                    raise ValueError("全候选估值尚无最近交易日，未发布部分候选")
                as_of = _date(latest[0].get("TRADE_DATE"))
                if as_of is None or as_of > _today() or as_of < recent:
                    raise ValueError("全候选估值交易日期无效")
                params.update(filter=f"(TRADE_DATE='{as_of}')", sortColumns="SECURITY_CODE", sortTypes="1", pageSize=500)
                rows, total, pages = _valuation_page(self._request("finance", params))
                if total > 10000 or pages > 20:
                    raise ValueError("完整日估值目录超出读取上限")
                for page in range(2, pages + 1):
                    more, count, page_count = _valuation_page(self._request("finance", {**params, "pageNumber": page}))
                    if count != total or page_count != pages:
                        raise ValueError("完整日估值目录分页正在变化，请稍后刷新")
                    rows.extend(more)
                    self._progress("读取全候选同日估值", page, pages)
                if len(rows) != total:
                    raise ValueError("完整日估值目录分页不完整")
                for row in rows:
                    identity = _reported_symbol(row.get("SECUCODE"))
                    if identity is None or row.get("SECURITY_CODE") != identity[2:] or identity in quotes:
                        raise ValueError("完整日估值目录证券重复或身份不匹配")
                    if _date(row.get("TRADE_DATE")) != as_of:
                        raise ValueError("完整日估值目录交易日不一致")
                    cap, price = _num(row.get("TOTAL_MARKET_CAP")), _num(row.get("CLOSE_PRICE"))
                    quotes[identity] = {"marketCap": cap if cap is not None and cap > 0 else None,
                        "price": price if price is not None and price > 0 else None, "peTTM": _num(row.get("PE_TTM")),
                        "quoteAsOf": as_of, "quoteReceivedAt": _now(), "quoteProvider": "eastmoney_daily_valuation",
                        "quoteSourceUrl": "https://data.eastmoney.com/gzfx/detail/" + identity[2:] + ".html"}
                if not quotes or not any(row["marketCap"] is not None for row in quotes.values()):
                    raise ValueError("完整日估值目录市值全部缺失，未发布本次候选")
            for row in items:
                if row["symbol"] in quotes:
                    row.update(quotes[row["symbol"]])
                if row["marketCap"] is None:
                    row["missing"].append("没有取得完整目录同一交易日的收盘市值和价格")
                row.update(quoteCached=False, quoteStale=False)
            self._progress("全候选整理完成", len(items), len(items))
            return {"items": items, "asOf": as_of, "scope": "complete_catalog", "candidateTotal": len(items),
                    "industries": sorted({row["industry"] for row in current if row["industry"]}),
                    "catalogReceivedAt": catalog["receivedAt"], "warnings": [FINANCE_NOTE,
                    "候选覆盖完整最新已披露 A 股财报目录；全候选先筛选和排序，再分页，各公司报告期可能不同。",
                    "市值采用完整来源目录同一交易日收盘值；缺失财报或市值保留缺口，不用 PE 倒推利润。"]}
        return self._cached("finance-screen-universe-v1", "eastmoney_finance", "东方财富完整财报与估值目录",
            PAGES["finance"], 3600, refresh, load,
            {"items": [], "asOf": None, "scope": "unavailable", "candidateTotal": 0, "industries": []}, budget=(100, 180))

    def finance_screen(self, payload=None):
        payload = self._payload(payload, {"query", "industry", "page", "pageSize", "refresh", "positive", "maxPe",
                                          "below", "scenario", "sortBy", "sortOrder"})
        page, size = self._page(payload)
        query, industry = payload.get("query", ""), payload.get("industry", "")
        if any(not isinstance(value, str) or len(value) > 80 for value in (query, industry)):
            raise ValueError("名称、代码和行业筛选最多 80 字")
        query, industry = query.strip(), industry.strip()
        for flag in ("positive", "below"):
            if flag in payload and type(payload[flag]) is not bool:
                raise ValueError("盈利及底座筛选必须是布尔值")
        max_pe = payload.get("maxPe")
        if max_pe is not None and (type(max_pe) not in (int, float) or not math.isfinite(max_pe) or max_pe <= 0):
            raise ValueError("市值 / TTM 上限必须是正数或空值")
        scenario = payload.get("scenario", {})
        if not isinstance(scenario, dict) or set(scenario) - {"growth", "pe", "water"}:
            raise ValueError("情景参数仅支持增长、基准 PE 和水位")
        factors = [scenario.get("growth", 1), scenario.get("pe"), scenario.get("water", 1)]
        if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0) for value in factors):
            raise ValueError("情景参数必须为正数或空值")
        multiple = math.prod(factors) if all(value is not None for value in factors) else None
        if multiple is not None and not math.isfinite(multiple):
            raise ValueError("情景参数超出可计算范围")
        if payload.get("below") and multiple is None:
            raise ValueError("按情景底座筛选前，请填写有效的增长、基准 PE 和水位")
        sort_by, order = payload.get("sortBy", "symbol"), payload.get("sortOrder", "asc")
        if sort_by not in ("symbol", "name", "industry", "ttmNetProfit", "marketCap", "actualPe", "scenarioGap", "reportDate") or order not in ("asc", "desc"):
            raise ValueError("筛选排序字段或方向无效")
        universe = self._screen_universe(payload.get("refresh", False))
        rows, ttm_count, cap_count, comparable = [], 0, 0, 0
        for original in universe["items"]:
            row = dict(original)
            profit, cap = row["ttmNetProfit"], row["marketCap"]
            ttm_count += profit is not None
            cap_count += cap is not None and cap > 0
            valid = profit is not None and profit > 0 and cap is not None and cap > 0
            comparable += valid
            actual_pe = cap / profit if valid else None
            gap = (profit * multiple / cap - 1) * 100 if valid and multiple is not None else None
            row.update(actualPe=actual_pe if actual_pe is not None and math.isfinite(actual_pe) else None,
                       scenarioGap=gap if gap is not None and math.isfinite(gap) else None,
                       quoteCached=universe["cached"], quoteStale=universe["stale"])
            if query and query.casefold() not in (row["symbol"] + row["name"]).casefold() or industry and row["industry"] != industry:
                continue
            if payload.get("positive") and (profit is None or profit <= 0):
                continue
            if max_pe is not None and (row["actualPe"] is None or row["actualPe"] > max_pe):
                continue
            if payload.get("below") and (row["scenarioGap"] is None or row["scenarioGap"] <= 0):
                continue
            rows.append(row)
        rows.sort(key=lambda row: row["symbol"])
        present = [row for row in rows if row.get(sort_by) is not None]
        missing = [row for row in rows if row.get(sort_by) is None]
        present.sort(key=lambda row: row[sort_by].casefold() if isinstance(row[sort_by], str) else row[sort_by], reverse=order == "desc")
        rows = present + missing
        return {**universe, "items": rows[(page - 1) * size:page * size], "page": page, "pageSize": size,
                "total": len(rows), "filteredTotal": len(rows), "hasMore": page * size < len(rows),
                "sortBy": sort_by, "sortOrder": order,
                "status": "unavailable" if universe["status"] == "unavailable" else "ready" if rows else "empty",
                "coverage": {"total": len(universe["items"]), "ttmAvailable": ttm_count, "marketCapAvailable": cap_count,
                             "comparable": comparable, "excluded": len(universe["items"]) - comparable}}

    def _news_source(self, source, symbol, refresh):
        def load():
            if source == "eastmoney":
                payload = self._request("news", {"client": "web", "biz": "web_724", "fastColumn": "102",
                    "sortEnd": "", "pageSize": 150, "req_trace": str(int(time.time() * 1000))})
                data = payload.get("data")
                if str(payload.get("code")) != "1" or not isinstance(data, dict) or not isinstance(data.get("fastNewsList"), list):
                    raise ValueError("快讯来源响应结构不兼容")
                raw_rows = data["fastNewsList"]
                if len(raw_rows) > 150:
                    raise ValueError("快讯来源返回过多条目")
                items = []
                for row in raw_rows:
                    if not isinstance(row, dict) or not re.fullmatch(r"\d{10,30}", str(row.get("code", ""))):
                        raise ValueError("快讯条目身份无效")
                    title, published = _text(row.get("title")), _stamp(row.get("showTime"))
                    if not title or not published:
                        continue
                    stocks = row.get("stockList", [])
                    if not isinstance(stocks, list):
                        raise ValueError("快讯证券关联字段不兼容")
                    if symbol and (("1." if symbol.startswith("sh") else "0.") + symbol[2:]) not in stocks:
                        continue
                    items.append({"id": "eastmoney:" + row["code"], "title": title, "summary": _text(row.get("summary"), 700),
                        "url": "https://finance.eastmoney.com/a/" + row["code"] + ".html", "publishedAt": published,
                        "sourceName": "东方财富快讯", "symbol": symbol or None})
            else:
                params = {"sr": "-1", "page_size": 100, "page_index": 1, "ann_type": "A", "client_source": "web", "f_node": "0", "s_node": "0"}
                if symbol:
                    params["stock_list"] = symbol[2:]
                payload = self._request("announcements", params)
                data = payload.get("data")
                if payload.get("success") != 1 or not isinstance(data, dict) or not isinstance(data.get("list"), list):
                    raise ValueError("公告来源响应结构不兼容")
                if len(data["list"]) > 100:
                    raise ValueError("公告来源返回过多条目")
                items = []
                for row in data["list"]:
                    if not isinstance(row, dict) or not re.fullmatch(r"AN\d{10,30}", str(row.get("art_code", ""))):
                        raise ValueError("公告条目身份无效")
                    codes = row.get("codes")
                    if not isinstance(codes, list):
                        raise ValueError("公告证券关联字段不兼容")
                    if symbol and not any(code.get("stock_code") == symbol[2:] and str(code.get("market_code")) == ("1" if symbol.startswith("sh") else "0") for code in codes if isinstance(code, dict)):
                        continue
                    title = _text(row.get("title"))
                    published = _stamp(row.get("display_time")) or _stamp(row.get("notice_date"))
                    if not title or not published:
                        continue
                    security = next((code.get("stock_code") for code in codes if isinstance(code, dict) and re.fullmatch(r"\d{6}", str(code.get("stock_code", "")))), None)
                    url = f"https://data.eastmoney.com/notices/detail/{security}/{row['art_code']}.html" if security else PAGES["announcements"]
                    items.append({"id": "announcement:" + row["art_code"], "title": title, "summary": "",
                        "url": url, "publishedAt": published, "sourceName": "东方财富公司公告", "symbol": symbol or None,
                        "announcementDate": _date(row.get("notice_date"))})
            items = list({row["id"]: row for row in items}.values())
            items.sort(key=lambda row: (row["publishedAt"], row["id"]), reverse=True)
            return {"items": items, "warnings": ["仅提供来源近期条目与原始摘要，不代表完整历史检索；公告正文请打开原文。"]}
        source_name = "东方财富快讯" if source == "eastmoney" else "东方财富公司公告"
        return self._cached("news|" + source + "|" + (symbol or ""), source, source_name,
            PAGES["news" if source == "eastmoney" else "announcements"], 300, refresh, load, {"items": []})

    def news_list(self, payload=None):
        payload = self._payload(payload, {"source", "query", "symbol", "page", "refresh"})
        source = payload.get("source", "all")
        if source not in ("all", "eastmoney", "announcements"):
            raise ValueError("请选择快讯、公司公告或全部来源")
        symbol = _symbol(payload["symbol"]) if payload.get("symbol") else None
        query = payload.get("query", "")
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError("资讯关键词最多 100 字")
        page, size = self._page(payload)
        snapshots = [self._news_source(provider, symbol, payload.get("refresh", False)) for provider in (["eastmoney", "announcements"] if source == "all" else [source])]
        items = [row for result in snapshots for row in result["items"] if not query.strip() or query.strip().casefold() in (row["title"] + " " + row["summary"]).casefold()]
        items.sort(key=lambda row: (row["publishedAt"], row["id"]), reverse=True)
        available = [result for result in snapshots if result["status"] != "unavailable"]
        return {"items": items[(page - 1) * size:page * size], "page": page, "pageSize": size, "total": len(items), "hasMore": page * size < len(items),
                "provider": source, "sourceName": "公开市场资讯" if source == "all" else snapshots[0]["sourceName"],
                "sourceUrl": PAGES["news" if source != "announcements" else "announcements"],
                "receivedAt": min((row["receivedAt"] for row in available), default=None),
                "cached": any(row["cached"] for row in snapshots), "stale": any(row["stale"] for row in snapshots),
                "status": "ready" if items else "empty" if available else "unavailable",
                "sources": [{key: row[key] for key in ("provider", "sourceName", "receivedAt", "status", "cached", "stale")} for row in snapshots],
                "warnings": list(dict.fromkeys(warning for row in snapshots for warning in row["warnings"])),
                "errors": [error for row in snapshots for error in row["errors"]]}

    def funds_history(self, payload=None):
        payload = self._payload(payload, {"id", "limit", "refresh"})
        code, limit = payload.get("id"), payload.get("limit", 60)
        if not isinstance(code, str) or not re.fullmatch(r"BK\d{4,6}", code):
            raise ValueError("请选择东方财富资金板块代码，例如 BK0428")
        if type(limit) is not int or limit not in (30, 60, 120, 250):
            raise ValueError("资金历史支持最近 30、60、120 或 250 个交易日")
        units = {"flow": "元", "mainNetInflow": "元", "changePercent": "%", "close": "指数点"}
        def load():
            response = self._request("funds_history", {"secid": "90." + code, "lmt": "0", "klt": "101",
                "fields1": "f1,f2,f3,f7", "fields2": ",".join("f" + str(number) for number in range(51, 66)),
                "ut": "b2884a393a59ad64002292a3e90d46a5"})
            data = response.get("data")
            if response.get("rc") != 0 or not isinstance(data, dict) or data.get("code") != code or data.get("market") != 90:
                raise ValueError("板块资金历史来源身份不匹配或暂不可用")
            rows, name = data.get("klines"), _text(data.get("name"), 80)
            if not name or not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("板块资金历史格式不兼容或超过读取范围")
            items, seen = [], set()
            # Official daykline fields: date, main, small, medium, large, super-large,
            # five net ratios, index close, index change%, and two reserved columns.
            for raw in rows:
                values = raw.split(",") if isinstance(raw, str) else []
                if len(values) != 15:
                    raise ValueError("板块资金历史字段数量已变化")
                day = _date(values[0])
                if day is None or values[0] != day or day > _today() or day in seen:
                    raise ValueError("板块资金历史日期缺失、重复或尚未发生")
                seen.add(day)
                items.append({"date": day, "mainNetInflow": _num(values[1]), "smallNetInflow": _num(values[2]),
                    "mediumNetInflow": _num(values[3]), "largeNetInflow": _num(values[4]),
                    "superLargeNetInflow": _num(values[5]), "close": _num(values[11]), "changePercent": _num(values[12])})
            if items and not any(row["mainNetInflow"] is not None for row in items):
                raise ValueError("板块资金历史主力净流入全部缺失")
            items.sort(key=lambda row: row["date"])
            missing = sum(any(row[key] is None for key in ("mainNetInflow", "superLargeNetInflow", "largeNetInflow", "mediumNetInflow", "smallNetInflow")) for row in items)
            return {"id": code, "name": name, "items": items, "availableCount": len(items),
                "asOf": items[-1]["date"] if items else None, "units": units,
                "warnings": [FUNDS_NOTE, "每个日期为该交易日单日净流入，正数流入、负数流出；未提供的日期或金额不补零。"] +
                    ([f"{missing} 个交易日存在缺失金额，图中应断开显示。"] if missing else [])}
        result = self._cached("funds-history|" + code, "eastmoney_funds_history", "东方财富板块历史资金",
            "https://data.eastmoney.com/bkzj/" + code + ".html", 300, payload.get("refresh", False), load,
            {"id": code, "name": code, "items": [], "availableCount": 0, "asOf": None, "units": units})
        items = result["items"][-limit:]
        warnings = list(result["warnings"])
        if 0 < result["availableCount"] < limit:
            warnings.append(f"来源目前仅提供 {result['availableCount']} 个交易日，少于所选 {limit} 日；未补造更早数据。")
        return {**result, "items": items, "total": len(items), "requestedLimit": limit, "warnings": warnings,
                "startDate": items[0]["date"] if items else None, "endDate": items[-1]["date"] if items else None}

    def funds_sectors(self, payload=None):
        payload = self._payload(payload, {"period", "refresh"})
        period = payload.get("period", "today")
        fields = {"today": ("f3", "f62", "f184", "f66", "f72", "f78", "f84"),
                  "5d": ("f109", "f164", "f165", "f166", "f168", "f170", "f172"),
                  "10d": ("f160", "f174", "f175", "f176", "f178", "f180", "f182")}
        if not isinstance(period, str) or period not in fields:
            raise ValueError("资金周期支持当日、5 日或 10 日")
        change, main, main_ratio, huge, large, medium, small = fields[period]
        field_names = {"f124": "行情时间", change: "涨跌幅", main_ratio: "主力净占比", huge: "超大单净流入",
                       large: "大单净流入", medium: "中单净流入", small: "小单净流入"}
        units = {"flow": "元", "mainNetInflow": "元", "mainNetRatio": "%", "changePercent": "%"}
        def read_table(payload, limit):
            data = payload.get("data")
            if payload.get("rc") != 0 or not isinstance(data, dict) or not isinstance(data.get("diff"), list) or type(data.get("total")) is not int:
                raise ValueError("板块资金来源响应结构不兼容")
            if not 0 <= data["total"] <= 1500 or len(data["diff"]) > limit:
                raise ValueError("板块资金条目超出读取范围")
            rows = data["diff"]
            seen = set()
            for raw in rows:
                if not isinstance(raw, dict) or not re.fullmatch(r"BK\d{4,6}", str(raw.get("f12", ""))) or raw["f12"] in seen or not _text(raw.get("f14"), 80):
                    raise ValueError("板块资金身份重复或格式不兼容")
                seen.add(raw["f12"])
            return rows, data["total"]

        def live_rows():
            rows, total, seen = [], None, set()
            for page in range(1, 31):
                payload = self._request("funds", {"pn": page, "pz": 50, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                    "fid": main, "fs": "m:90 s:4", "fields": ",".join(("f12", "f14", "f124", change, main, main_ratio, huge, large, medium, small))})
                more, next_total = read_table(payload, 50)
                if total is not None and next_total != total:
                    raise ValueError("板块资金分页变化，请稍后刷新")
                if any(row["f12"] in seen for row in more):
                    raise ValueError("板块资金分页存在重复")
                total = next_total
                rows.extend(more)
                seen.update(row["f12"] for row in more)
                if len(rows) >= total:
                    break
                if not more:
                    raise ValueError("板块资金分页存在缺口")
            if len(rows) != total or (rows and not any(_num(row.get(main)) is not None for row in rows)):
                raise ValueError("板块资金条目不完整或关键金额全部缺失")
            return rows

        def chart_rows():
            # This is the same site's industry fund chart endpoint, with raw hundredths for percentages.
            rows, total = read_table(self._request("funds_chart", {"key": main, "code": "m:90+s:4"}), 1500)
            if len(rows) != total or (rows and not any(_num(row.get(main)) is not None for row in rows)):
                raise ValueError("官网资金图表未提供完整行业净流入")
            by_id = {row["f12"]: {"f12": row["f12"], "f14": row["f14"], main: row.get(main)} for row in rows}
            warnings = []
            for field in ("f124", change, main_ratio, huge, large, medium, small):
                try:
                    more, count = read_table(self._request("funds_chart", {"key": field, "code": "m:90+s:4"}), 1500)
                    if count != total or {row["f12"] for row in more} != set(by_id):
                        raise ValueError("行业目录与净流入批次不一致")
                    for row in more:
                        if _text(row["f14"], 80) != _text(by_id[row["f12"]]["f14"], 80):
                            raise ValueError("行业名称与净流入批次不一致")
                    for row in more:
                        value = _num(row.get(field))
                        by_id[row["f12"]][field] = value / 100 if value is not None and field in (change, main_ratio) else value
                except ValueError as error:
                    warnings.append(field_names[field] + "暂缺：" + str(error))
            return list(by_id.values()), warnings

        def load():
            warnings, errors, adapter = [], [], "quote_list"
            try:
                rows = live_rows()
            except ValueError as error:
                rows, warnings = chart_rows()
                adapter = "official_chart"
                warnings.insert(0, "行情明细接口暂不可用，采用东方财富官网行业资金图表接口。")
                errors.append({"provider": "eastmoney_funds_quote", "message": str(error)})
            items = []
            for raw in rows:
                items.append({"id": raw["f12"], "name": _text(raw.get("f14"), 80), "changePercent": _num(raw.get(change)),
                        "mainNetInflow": _num(raw.get(main)), "mainNetRatio": _num(raw.get(main_ratio)),
                        "superLargeNetInflow": _num(raw.get(huge)), "largeNetInflow": _num(raw.get(large)),
                        "mediumNetInflow": _num(raw.get(medium)), "smallNetInflow": _num(raw.get(small)),
                        "asOf": _stamp(raw.get("f124")), "url": "https://data.eastmoney.com/bkzj/" + raw["f12"] + ".html"})
            stamps = sorted({row["asOf"] for row in items if row["asOf"]})
            return {"period": period, "items": items, "total": len(items), "asOf": stamps[-1] if stamps else None,
                    "units": units, "adapter": adapter, "errors": errors,
                    "warnings": warnings + [FUNDS_NOTE, "行业板块口径来自当前来源，成分可能重叠，勿将板块净流入相加当作全市场资金。",
                                 "所选周期为来源最新一期，休市时可能是最近交易日；不提供任意历史日期回溯。"] +
                                (["部分板块未提供行情时间，采集时间不代表资金发生时间。"] if any(row["asOf"] is None for row in items) else [])}
        return self._cached("funds|" + period, "eastmoney_funds", "东方财富板块资金", PAGES["funds"], 300,
                            payload.get("refresh", False), load, {"period": period, "items": [], "total": 0, "asOf": None, "units": units})
