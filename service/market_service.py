"""Separate, restart-safe local market catalogue and validated daily-bar cache."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from service import tdx
from service.online_market import OnlineMarket
from service.market_research import MarketResearch
from service.analysis_service import AnalysisService
from service.market_information import MarketInformation
from service.sector_history import SectorHistory
from service.funds_activity import FundsActivity
from service.regulatory_watch import RegulatoryWatch
from service.valuation_context import finance_context
from service.security_search import SecuritySearch


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _integer(value, label, default, minimum, maximum):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label}须为 {minimum} 至 {maximum} 的整数")
    return value


def _date(value):
    if value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError()
        return value
    except ValueError:
        raise ValueError("查询日期请使用 YYYY-MM-DD 格式") from None


class MarketService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "market.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("行情缓存数据库损坏，原文件已保留")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS securities (
                    symbol TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
                    market TEXT NOT NULL, kind TEXT NOT NULL, earliest_date TEXT NOT NULL,
                    latest_date TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bars_cache (
                    symbol TEXT PRIMARY KEY, digest TEXT NOT NULL, rows_json TEXT NOT NULL,
                    validated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS intraday_cache (
                    source TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(source,symbol,date));
            """)
            self._get("path")
            self.online = OnlineMarket(self)
            self.security_search = SecuritySearch(self)
            self.research = MarketResearch(self)
            self.analysis = AnalysisService(self)
            self.information = MarketInformation(self)
            self.sector_history = SectorHistory(self)
            self.funds_activity = FundsActivity(self)
            self.regulatory = RegulatoryWatch(self)
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.regulatory.close()
        self.analysis.close()
        self.information.close()
        self.sector_history.close()
        self.funds_activity.close()
        self.research.close()
        self.online.close()
        self.db.close()

    def _get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, _dump(value)))

    def source_revision(self):
        return self._get("marketSourceRevision", "")

    def bump_source_revision(self):
        """Call inside the source update transaction, shared by every worker."""
        revision = uuid.uuid4().hex
        self._set("marketSourceRevision", revision)
        return revision

    def dispatch(self, method, payload=None):
        payload = payload or {}
        if method.startswith(("market.analysis.", "market.watchlist.")):
            return self.analysis.dispatch(method, payload)
        routes = {"market.status": lambda: self.status(), "market.configure": lambda: self.configure(payload),
                  "market.scan": lambda: self.scan(), "market.search": lambda: self.search(payload),
                  "market.bars": lambda: self.bars(payload),
                  "market.research": lambda: self.research.snapshot(payload),
                  "market.snapshot": lambda: self.research.current_snapshot(payload),
                  "market.news.list": lambda: self.information.news_list(payload),
                  "market.finance.snapshot": lambda: self.information.finance_snapshot(payload),
                  "market.finance.screen": lambda: self.information.finance_screen(payload),
                  "market.finance.benchmark": lambda: self.information.finance_benchmark(payload),
                  "market.finance.context": lambda: finance_context(self, payload),
                  "market.funds.sectors": lambda: self.information.funds_sectors(payload),
                  "market.funds.history": lambda: self.information.funds_history(payload),
                  "market.funds.activity.list": lambda: self.funds_activity.catalogue(payload),
                  "market.funds.activity.history": lambda: self.funds_activity.history(payload),
                  "market.regulatory.list": lambda: self.regulatory.list(payload),
                  "market.online.status": self.online.status,
                  "market.online.configure": lambda: self.online.configure(payload),
                  "market.online.test": self.online.test,
                  "market.quotes": lambda: self.online.quotes(payload),
                  "market.intraday": lambda: self.intraday(payload),
                  "market.sectors": self.online.sectors,
                  "market.sector.history": lambda: self.sector_history.history(payload),
                  "market.sector": lambda: self.online.sector(payload)}
        if method not in routes:
            raise ValueError("不支持的行情操作")
        return routes[method]()

    def status(self):
        path = self._get("path")
        warnings = list(self._get("warnings", []))
        available = False
        if path:
            try:
                tdx.normalize_directory(path)
                available = True
            except ValueError:
                warnings.insert(0, "所选通达信目录当前不可访问；已缓存的日线仍可查看")
        stats = self.db.execute("SELECT COUNT(*),MIN(earliest_date),MAX(latest_date) FROM securities").fetchone()
        markets = [{"market": row[0], "symbolCount": row[1]} for row in self.db.execute(
            "SELECT market,COUNT(*) FROM securities GROUP BY market ORDER BY market")]
        return {"configured": bool(path), "path": path or "", "available": available,
                "symbolCount": stats[0], "earliestDate": stats[1], "latestDate": stats[2],
                "lastScannedAt": self._get("lastScannedAt"), "warnings": warnings, "markets": markets,
                "online": self.online.status()}

    def configure(self, payload):
        root = tdx.normalize_directory(payload.get("path"))
        previous = self._get("path")
        # A different source must never inherit bars/catalogue from the old directory.
        same = previous and Path(previous) == root
        if not same:
            self.analysis.invalidate()
            self.research.invalidate()
        with self.db:
            if not same:
                self.db.execute("DELETE FROM securities")
                self.db.execute("DELETE FROM bars_cache")
                self._set("securityNameCatalogue", None)
                self.security_search.invalidate()
                self._set("lastScannedAt", None)
                self._set("warnings", [])
                self.bump_source_revision()
            self._set("path", str(root))
        return self.status()

    def scan(self):
        chosen = self._get("path")
        if not chosen:
            raise ValueError("请先在设置中选择通达信目录")
        root = tdx.normalize_directory(chosen)
        names, warnings = tdx.read_names(root)
        catalogue, failures, skipped = [], [], 0
        vipdoc = tdx.within(root / "vipdoc", root)
        for market in tdx.MARKETS:
            folder = tdx.within(vipdoc / market / "lday", vipdoc)
            if not folder.is_dir():
                continue
            try:
                files = sorted(folder.iterdir())
            except OSError:
                raise ValueError("日线目录无法读取，请检查读取权限后重新扫描") from None
            for path in files:
                if path.suffix.lower() != ".day":
                    continue
                symbol = path.stem.lower()
                if not tdx.SYMBOL.fullmatch(symbol) or symbol[:2] != market:
                    continue
                code = symbol[2:]
                kind = tdx.security_kind(market, code)
                if kind == "other":
                    skipped += 1
                    continue
                try:
                    # The parent is already canonical and bounded. Resolve only file
                    # links here: re-resolving three complete Windows paths per item
                    # makes a 6,000-security scan take tens of seconds needlessly.
                    if path.is_symlink():
                        path = tdx.within(path, vipdoc)
                    if not path.is_file():
                        continue
                    first, last = tdx.read_range(path)
                    catalogue.append((symbol, code, names.get(symbol, "名称待补全"), market,
                                      kind, first, last))
                except ValueError as error:
                    failures.append(f"{symbol}：{error}")
        if failures:
            warnings.append(f"{len(failures)} 个日线文件异常，未加入索引。" + "；".join(failures[:3]))
        if skipped:
            warnings.append(f"本阶段支持 A 股和指数，已跳过 {skipped} 个基金、债券或其他品种日线文件")
        if not catalogue:
            warnings.append("没有可读取的日线文件，请先在通达信下载日线数据后重新扫描")
        elif any(row[2] == "名称待补全" for row in catalogue):
            warnings.append("部分证券缺少名称，已保留市场及代码，可按代码查找")
        with self.db:
            self.db.execute("DELETE FROM securities")
            self.db.executemany("INSERT INTO securities VALUES (?,?,?,?,?,?,?)", catalogue)
            self._set("lastScannedAt", _now())
            self._set("warnings", warnings)
            self.bump_source_revision()
        self.analysis.invalidate()
        self.research.invalidate()
        return self.status()

    def search(self, payload):
        query = payload.get("query", "")
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError("搜索内容过长")
        query = query.strip().lower()
        offset = _integer(payload.get("offset"), "起始位置", 0, 0, 1000000)
        limit = _integer(payload.get("limit"), "每页数量", 50, 1, 200)
        kind = payload.get("kind")
        if kind not in (None, "stock", "index"):
            raise ValueError("搜索类型须为 stock 或 index")
        return self.security_search.search(query, kind=kind, offset=offset, limit=limit)

    def bars(self, payload):
        market, code = tdx.symbol_parts(payload.get("symbol"))
        symbol = market + code
        limit = _integer(payload.get("limit"), "日线数量", 240, 1, 5000)
        start, end = _date(payload.get("start")), _date(payload.get("end"))
        if start and end and start > end:
            raise ValueError("起始日期不能晚于结束日期")
        chosen = self._get("path")
        security = self.db.execute("SELECT * FROM securities WHERE symbol=?", (symbol,)).fetchone()
        if (not chosen or not security) and self.online.config()["enabled"]:
            return self.online.bars({**payload, "symbol": symbol, "limit": min(limit, 2400)})
        if not chosen:
            raise ValueError("请先在设置中选择通达信目录")
        security = self.db.execute("SELECT * FROM securities WHERE symbol=?", (symbol,)).fetchone()
        if not security:
            raise ValueError("该证券没有可用日线索引，请重新扫描目录后按市场及代码查找")
        cache = self.db.execute("SELECT * FROM bars_cache WHERE symbol=?", (symbol,)).fetchone()
        path = tdx.daily_path(Path(chosen), symbol)
        warnings, cached, stale = [], False, False
        if not path.is_file():
            if cache is None:
                raise ValueError("日线文件当前不可访问，且此证券尚无本地缓存")
            rows, digest, validated_at = json.loads(cache["rows_json"]), cache["digest"], cache["validated_at"]
            warnings.append("源日线文件当前不可访问，正在显示此前缓存；数据尚未更新")
            cached = True
            stale = True
        else:
            data = tdx.read_stable(path)
            digest = hashlib.sha256(data).hexdigest()
            if cache is not None and cache["digest"] == digest:
                rows = json.loads(cache["rows_json"])
                cached, validated_at = True, cache["validated_at"]
            else:
                # Parse before the transaction so corrupted/new partial data cannot replace cache.
                rows, validated_at = tdx.parse_daily(data), _now()
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO bars_cache VALUES (?,?,?,?)", (symbol, digest, _dump(rows), validated_at))
                    self.db.execute("UPDATE securities SET earliest_date=?,latest_date=? WHERE symbol=?", (rows[0]["date"], rows[-1]["date"], symbol))
        if security["name"] == "名称待补全":
            warnings.append("证券名称待补全，行情身份以市场和代码为准")
        kind = security["kind"]
        return {"symbol": symbol, "code": code, "name": security["name"], "market": market, "kind": kind,
                "adjustment": "none", "volumeUnit": "股" if kind == "stock" else "手", "amountUnit": "元",
                "dataAsOf": rows[-1]["date"], "lastReadAt": _now(), "lastValidatedAt": validated_at,
                "dataVersion": digest, "rows": [row for row in rows if (not start or row["date"] >= start) and (not end or row["date"] <= end)][-limit:],
                "warnings": warnings, "cached": cached, "stale": stale}

    def intraday(self, payload):
        from service.intraday import read_intraday, MissingIntraday

        market, code = tdx.symbol_parts(payload.get("symbol"))
        symbol = market + code
        requested = _date(payload.get("date"))
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        if requested and requested > today:
            raise ValueError("不能读取尚未发生的分时日期")
        chosen = self._get("path")
        source_key = _dump([chosen, self.online.source()])
        dates, warnings = set(), []
        local = None
        target = requested or today
        try:
            local = read_intraday(Path(chosen) if chosen else None, symbol, target)
            dates.update(local["availableDates"])
        except MissingIntraday as error:
            dates.update(error.available_dates)
            warnings.append(str(error))
            if not requested and dates:
                local = read_intraday(Path(chosen), symbol, max(dates))
        except ValueError as error:
            warnings.append(str(error))

        def freshness(data):
            # A day alone is not a freshness boundary: a disconnected terminal
            # can still expose the morning of a day already cached at the close.
            minutes = {row.get("time", "") for row in data.get("rows", [])}
            return data["date"], max(minutes, default=""), len(minutes)

        def can_replace(candidate, prior):
            new, old = freshness(candidate), freshness(prior)
            # A later final minute does not make a truncated same-day series
            # complete. Keep the proven coverage without mixing provider units.
            return new[0] > old[0] or (new[0] == old[0] and new[1] >= old[1] and new[2] >= old[2])

        def note_partial(candidate, prior):
            if candidate["date"] == prior["date"] and freshness(candidate)[2] < freshness(prior)[2]:
                warnings.append("本次来源返回的同日分钟记录较少，保留此前更完整的分时，未拼接不同来源。")

        def cached_result(data):
            return {**data, "cached": True,
                    "availableDates": sorted(dates.union(data.get("availableDates", []))),
                    "warnings": list(dict.fromkeys([*data.get("warnings", []), *warnings,
                        "本次来源未提供更完整的分时，保留此前缓存；不是本次更新。"]))}

        saved = self.db.execute("SELECT data FROM intraday_cache WHERE source=? AND symbol=? AND (? IS NULL OR date=?) ORDER BY date DESC LIMIT 1",
                                (source_key, symbol, requested, requested)).fetchone()
        saved = json.loads(saved[0]) if saved else None

        def finish(data):
            if saved and not can_replace(data, saved):
                note_partial(data, saved)
                return cached_result(saved)
            data = {**data, "name": self.online.security_name(symbol), "cached": False,
                    "availableDates": sorted(dates.union(data.get("availableDates", []))),
                    "warnings": list(dict.fromkeys([*data.get("warnings", []), *warnings]))}
            if data["rows"]:
                with self.db:
                    self.db.execute("BEGIN IMMEDIATE")
                    # A second worker may have refreshed this same day while the
                    # network request was in flight. Never replace its later bars.
                    current = self.db.execute("SELECT data FROM intraday_cache WHERE source=? AND symbol=? AND date=?",
                                              (source_key, symbol, data["date"])).fetchone()
                    current = json.loads(current[0]) if current else None
                    if current and not can_replace(data, current):
                        note_partial(data, current)
                        return cached_result(current)
                    self.db.execute("INSERT OR REPLACE INTO intraday_cache VALUES (?,?,?,?)", (source_key, symbol, data["date"], _dump(data)))
            return data

        # A completed local historical day needs no network round trip.
        if local and requested and requested < today:
            return finish(local)
        if self.online.config()["enabled"]:
            try:
                arguments = {"symbol": symbol, "period": "1m", "limit": 500}
                if requested:
                    arguments.update(start=requested, end=requested)
                data = self.online.call("tdx_bars", arguments)
                days = {row["date"][:10] for row in data["rows"]}
                dates.update(days)
                selected = requested or (max(days) if days else target)
                rows = [{**row, "time": row["date"][11:16]} for row in data["rows"] if row["date"][:10] == selected]
                mcp = {"symbol": symbol, "code": code, "date": selected, "rows": rows,
                       "provider": "tdx_mcp", "volumeUnit": data["volumeUnit"], "amountUnit": data["amountUnit"],
                       "asOf": f"{selected}T{rows[-1]['time']}:00+08:00" if rows else None,
                       "receivedAt": data["receivedAt"], "warnings": data.get("warnings", [])}
                if rows and local is not None:
                    note_partial(mcp, local)
                if rows and (local is None or can_replace(mcp, local)):
                    local = mcp
                    if (requested or selected >= today) and (not saved or can_replace(mcp, saved)):
                        return finish(mcp)
            except ValueError as error:
                warnings.append(str(error))
            # The terminal may have daily files newer than its minute download.
            # Fetch only public minute observations; never rewrite TDX source files.
            try:
                from service.intraday_online import read_online_intraday
                data = read_online_intraday(symbol, requested)
                dates.update(data.get("availableDates", []))
                if data["rows"] and local is not None:
                    note_partial(data, local)
                if data["rows"] and (local is None or can_replace(data, local)):
                    return finish(data)
            except MissingIntraday as error:
                dates.update(error.available_dates)
                warnings.append(str(error))
            except ValueError as error:
                warnings.append(str(error))
        if saved and (local is None or not can_replace(local, saved)):
            if local:
                note_partial(local, saved)
            return cached_result(saved)
        if local:
            return finish(local)
        return {"symbol": symbol, "code": code, "name": self.online.security_name(symbol), "date": target,
                "rows": [], "provider": "unavailable", "asOf": None, "receivedAt": _now(),
                "volumeUnit": "原始单位", "amountUnit": "元", "cached": False,
                "availableDates": sorted(dates), "warnings": list(dict.fromkeys(warnings)) or ["该日尚无真实分钟数据"]}
