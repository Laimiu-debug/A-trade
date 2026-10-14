"""Local daily research tools and an independent persistent observation pool."""
from __future__ import annotations

import json
import math
import datetime as dt
from collections import OrderedDict
from statistics import mean

from service import tdx
from service.market_research import board_of, _now, _rounded, validate_as_of, LIMITATIONS
from service.leader_research import Leadership, limit_flags


VERSION = "local-analysis-v3-paging"
STATUSES = {"watching", "planned", "holding", "archived"}
COMMON = {"asOf", "refresh"}
PAGE_DEFAULTS = {"query": "", "sort": None, "order": "desc", "offset": 0}
SORT_FIELDS = {
    "trends": {"symbol", "code", "name", "date", "close", "amount", "volume", "changePercent", "return5", "return20", "return60", "ma20", "ma60", "volumeRatio5", "boardHeight"},
    "rebounds": {"symbol", "code", "name", "date", "eventDate", "priorEndDate", "priorBoards", "gapDays", "close", "amount", "volume", "changePercent", "boardHeight"},
}
FEATURE_CACHE_SIZE = 4
WATCHLIST_LIMIT = 5000
WATCHLIST_SCHEMA = "a-trade.watchlist"
WATCHLIST_BYTES = 20 * 1024 * 1024 - 1024
DEFAULTS = {
    "trends": {"minReturn20": 0, "minReturn60": 0, "requireAboveMA20": True,
               "requireAboveMA60": True, "minVolumeRatio": 0, "minAmount": 0, "excludeST": True, "limit": 200},
    "moves": {"kind": "all", "changeThreshold": 5, "volumeThreshold": 2,
              "breakoutDays": 20, "minAmount": 0, "limit": 200},
    "rebounds": {"minBoards": 2, "minGap": 1, "maxGap": 10, "lookbackDays": 60, "limit": 200},
    "ladder": {"historyDays": 60, "board": "all", "minHeight": 2, "excludeST": True},
    "sentiment": {"historyDays": 120, "excludeST": True, "themeId": ""},
    "overview": {},
}


def _number(value, field, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or integer and not isinstance(value, int):
        raise ValueError(f"{field} 超出范围（{low}–{high}）")
    return value


def parameters(tool, payload):
    defaults = {**DEFAULTS[tool], **(PAGE_DEFAULTS if tool in SORT_FIELDS else {})}
    if not isinstance(payload, dict) or set(payload) - COMMON - defaults.keys():
        raise ValueError("存在不支持的分析参数")
    if type(payload.get("refresh", False)) is not bool:
        raise ValueError("refresh 须为布尔值")
    result = {**defaults, **{key: value for key, value in payload.items() if key not in COMMON}}
    if tool in SORT_FIELDS:
        if not isinstance(result["query"], str) or len(result["query"]) > 100 or "\0" in result["query"]:
            raise ValueError("搜索内容最多 100 字且不能含空字符")
        result["query"] = result["query"].strip()
        if result["sort"] not in (None, "") and (not isinstance(result["sort"], str) or result["sort"] not in SORT_FIELDS[tool]):
            raise ValueError("不支持的分析排序字段")
        if not isinstance(result["order"], str) or result["order"] not in {"asc", "desc"}:
            raise ValueError("排序方向须为 asc 或 desc")
        _number(result["offset"], "offset", 0, 1000000, True)
    for key in ("requireAboveMA20", "requireAboveMA60", "excludeST"):
        if key in result and type(result[key]) is not bool:
            raise ValueError(f"{key} 须为布尔值")
    for key in ("minReturn20", "minReturn60"):
        if key in result:
            _number(result[key], key, -100, 10000)
    for key in ("minVolumeRatio", "volumeThreshold"):
        if key in result:
            _number(result[key], key, 0, 100)
    if "changeThreshold" in result:
        _number(result["changeThreshold"], "changeThreshold", .1, 100)
    if "minAmount" in result:
        _number(result["minAmount"], "minAmount", 0, 1e13)
    for key, low, high in (("limit", 1, 1000), ("breakoutDays", 5, 60), ("minBoards", 2, 10),
                           ("minGap", 1, 10), ("maxGap", 1, 10), ("lookbackDays", 1, 250), ("historyDays", 1, 1000), ("minHeight", 2, 10)):
        if key in result:
            _number(result[key], key, low, high, True)
    if "minGap" in result and result["minGap"] > result["maxGap"]:
        raise ValueError("最短间隔不能大于最长间隔")
    if "kind" in result and (not isinstance(result["kind"], str) or result["kind"] not in {"all", "rise", "fall", "volume", "breakout"}):
        raise ValueError("异动类型不支持")
    if "board" in result and (not isinstance(result["board"], str) or result["board"] not in {"all", "main", "chinext", "star", "bse"}):
        raise ValueError("交易板块不支持")
    if "themeId" in result and (not isinstance(result["themeId"], str) or len(result["themeId"]) > 80):
        raise ValueError("题材标识无效")
    return result, validate_as_of(payload.get("asOf"))


def analyse_universe(research, universe, *, records=180):
    """Build per-date causal observations using a shared validated byte window."""
    dates = universe["dates"][-records:]
    indexes = {day: i for i, day in enumerate(dates)}
    leadership = Leadership(dates)
    items, events = [], []
    valid, stale, historical = 0, 0, 0
    progress = getattr(getattr(research, "owner", None), "progress", None)
    input_count = len(universe.get("inputs", []))
    for number, security in enumerate(research.iter_inputs(universe, records), 1):
        if progress and (number == 1 or number % 100 == 0 or number == input_count):
            progress("计算分析指标", number, input_count)
        if security["kind"] != "stock":
            continue
        historical += 1
        board = board_of(security["symbol"], "stock")
        aligned = [None] * len(dates)
        for row in security["rows"]:
            if row[0] in indexes:
                aligned[indexes[row[0]]] = row
        leadership.add(security, aligned, board)
        base = {key: security[key] for key in ("symbol", "code", "name", "market", "kind")}
        base["board"] = board
        height, prior_run = 0, None
        for i, row in enumerate(aligned):
            if row is None:
                # A gap is not proof of a broken limit-up sequence.
                height, prior_run = 0, None
                continue
            previous = aligned[i - 1] if i else None
            change = (row[1] / previous[1] - 1) * 100 if previous else None
            is_up, _, observable = limit_flags(previous, row, board, security["name"])
            before_height = height
            if row[2] <= 0:
                height, prior_run = 0, None
            elif is_up:
                height += 1
            else:
                if before_height >= 2 and observable:
                    prior_run = {"priorBoards": before_height, "priorEndDate": dates[i - 1], "index": i - 1}
                elif not observable:
                    prior_run = None
                height = 0
            if is_up:
                if before_height == 0 and prior_run:
                    gap = i - prior_run["index"] - 1
                    events.append({**base, "eventDate": row[0], "date": row[0], "close": row[1],
                                   "changePercent": _rounded(change), "amount": row[3], "volume": row[2],
                                   "priorBoards": prior_run["priorBoards"], "priorEndDate": prior_run["priorEndDate"],
                                   "gapDays": gap, "boardHeight": 1, "limitUpApprox": True})
                    prior_run = None
            if i != len(dates) - 1:
                continue
            def window(period, before=False):
                end = i if before else i + 1
                values = aligned[max(0, end - period):end]
                return values if len(values) == period and all(values) else None
            def average(period):
                values = window(period)
                return mean(value[1] for value in values) if values else None
            def returns(period):
                start = aligned[i - period] if i >= period else None
                return (row[1] / start[1] - 1) * 100 if start else None
            previous5 = window(5, before=True)
            average_volume = mean(value[2] for value in previous5) if previous5 else None
            ratio = row[2] / average_volume if average_volume else None
            ma20, ma60 = average(20), average(60)
            highs, highest, complete_window = {}, None, True
            for period in range(1, 61):
                earlier = aligned[i - period] if i >= period else None
                complete_window = complete_window and earlier is not None
                if complete_window:
                    highest = earlier[1] if highest is None else max(highest, earlier[1])
                if period >= 5:
                    highs[str(period)] = highest if complete_window else None
            item = {**base, "date": row[0], "close": row[1], "amount": row[3], "volume": row[2],
                    "changePercent": _rounded(change), "return5": _rounded(returns(5)),
                    "return20": _rounded(returns(20)), "return60": _rounded(returns(60)),
                    "ma20": _rounded(ma20), "ma60": _rounded(ma60),
                    "aboveMA20": row[1] > ma20 if ma20 is not None else None,
                    "aboveMA60": row[1] > ma60 if ma60 is not None else None,
                    "volumeRatio5": _rounded(ratio), "volumeUnit": "股", "boardHeight": height,
                    "limitUpApprox": bool(is_up), "stale": False,
                    "sparkline": [value[1] if value else None for value in aligned[max(0, i - 19):i + 1]],
                    "_previousCloseHigh": highs}
            items.append(item)
            valid += 1
        if not aligned or aligned[-1] is None:
            stale += 1
    leaders = leadership.finish()
    enrichment = {item["symbol"]: item for item in leaders["current"]}
    items = [{**item, **enrichment.get(item["symbol"], {})} for item in items]
    return {"items": items, "events": events, "timeline": leaders["timeline"], "leadership": leaders,
            "coverage": {"total": universe["total"], "valid": valid, "stale": stale,
                         "excluded": universe.get("failedStocks", max(0, universe["total"] - historical)),
                         "notYetObserved": universe.get("notYetObserved", 0)}}


def trends(items, params):
    result = []
    for item in items:
        if item["volume"] <= 0:
            continue
        if params["excludeST"] and ("ST" in item["name"].upper() or "退" in item["name"]):
            continue
        if params["requireAboveMA20"] and item["aboveMA20"] is not True:
            continue
        if params["requireAboveMA60"] and item["aboveMA60"] is not True:
            continue
        if any(item[field] is None or item[field] < params[param] for field, param in (("return20", "minReturn20"), ("return60", "minReturn60"), ("volumeRatio5", "minVolumeRatio"))):
            continue
        if item["amount"] < params["minAmount"]:
            continue
        result.append(item)
    return sorted(result, key=lambda item: (-item["return20"], -item["amount"], item["symbol"]))


def moves(items, params):
    result = []
    for item in items:
        if item["amount"] < params["minAmount"] or item["volume"] <= 0:
            continue
        signals = []
        change, ratio = item["changePercent"], item["volumeRatio5"]
        if change is not None and change >= params["changeThreshold"]:
            signals.append("rise")
        if change is not None and change <= -params["changeThreshold"]:
            signals.append("fall")
        if ratio is not None and ratio >= params["volumeThreshold"]:
            signals.append("volume")
        high = item["_previousCloseHigh"][str(params["breakoutDays"])]
        if high is not None and item["close"] > high:
            signals.append("breakout")
        if signals and (params["kind"] == "all" or params["kind"] in signals):
            result.append({**item, "signals": signals, "breakoutHigh": high, "breakoutDays": params["breakoutDays"]})
    return sorted(result, key=lambda item: (-len(item["signals"]), -abs(item["changePercent"] or 0), -item["amount"], item["symbol"]))


class AnalysisService:
    def __init__(self, owner):
        self.owner = owner
        self._features = OrderedDict()
        with owner.db:
            owner.db.execute("BEGIN IMMEDIATE")
            owner.db.execute("CREATE TABLE IF NOT EXISTS analysis_watchlist (symbol TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, tags TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            if "research" not in {row[1] for row in owner.db.execute("PRAGMA table_info(analysis_watchlist)")}:
                owner.db.execute("ALTER TABLE analysis_watchlist ADD COLUMN research TEXT")

    def close(self):
        self._features.clear()

    def invalidate(self):
        self._features.clear()

    def dispatch(self, method, payload=None):
        payload = payload or {}
        if method.startswith("market.watchlist.backup."):
            from service.watchlist_backup import dispatch
            return dispatch(self, method, payload)
        if method == "market.watchlist.list":
            return self.watchlist(payload)
        if method == "market.watchlist.save":
            return self.save_watchlist(payload)
        if method == "market.watchlist.delete":
            return self.delete_watchlist(payload)
        if method == "market.watchlist.save-many":
            return self.save_many_watchlist(payload)
        if method == "market.watchlist.export":
            return self.export_watchlist(payload)
        if method == "market.watchlist.import":
            return self.import_watchlist(payload)
        prefix = "market.analysis."
        tool = method[len(prefix):] if method.startswith(prefix) else ""
        if tool not in DEFAULTS:
            raise ValueError("不支持的分析操作")
        return self.analyse(tool, payload)

    def analyse(self, tool, payload):
        params, as_of = parameters(tool, payload)
        records = 80 + max(60, params.get("lookbackDays", 0), params.get("historyDays", 0))
        universe = self.owner.research.universe(as_of=as_of, records=records, refresh=payload.get("refresh", False))
        if tool == "sentiment":
            from service.emotion_cycle import build_emotion, themes_dispatch, VERSION as EMOTION_VERSION
            themes = themes_dispatch(self.owner, "market.emotion.themes.list", {})["items"]
            result = build_emotion(self.owner.research, universe, params, themes)
            warnings = [x for x in LIMITATIONS if "120 根" not in x]
            warnings += ["四阶段名称参考公开讲解；数值阈值为本产品可复核的观察规则，并非92科比原版公式或买卖信号。",
                         "最高连板只作为空间龙头候选；日线不能确认人气、封单、炸板回封或盘中带动关系。",
                         "题材归属使用具备生效日期的手工确认/当日板块快照，缺少归属时不自动认定跟风、补涨和新题材切换。"]
            if universe.get("failures"):
                warnings.insert(0, f"{len(universe['failures'])} 个文件读取失败，覆盖和阶段判断可能受限")
            if universe.get("cacheWarning"):
                warnings.insert(0, universe["cacheWarning"])
            if (result.get("current") or {}).get("scope") == "sample":
                warnings.insert(0, "当前不足1000只有效股票，仅展示所选目录的样本周期，不能代表全市场")
            if as_of and universe["asOf"] and as_of != universe["asOf"]:
                warnings.insert(0, f"请求日期 {as_of} 的最近实际交易日为 {universe['asOf']}")
            self.owner.research.assert_current(universe)
            return {**result, "asOf": universe["asOf"], "requestedAsOf": as_of,
                    "computedAt": universe["computedAt"], "cached": universe["cached"], "source": universe["source"],
                    "provider": "tdx_local_emotion", "version": EMOTION_VERSION, "parameters": params,
                    "sourceRevision": universe.get("sourceRevision", ""), "dataRevision": universe.get("dataRevision", ""),
                    "stale": bool(universe.get("cacheWarning")), "warnings": warnings}
        key = (universe["source"], as_of, universe.get("sourceRevision", ""), universe.get("dataRevision", ""), universe["generation"], records)
        if key in self._features:
            features = self._features.pop(key)
            self._features[key] = features
        else:
            features = analyse_universe(self.owner.research, universe, records=records)
            self._features[key] = features
            while len(self._features) > FEATURE_CACHE_SIZE:
                self._features.popitem(last=False)
        warnings = [warning for warning in LIMITATIONS if "120 根" not in warning]
        warnings.append("突破指收盘价超过此前指定交易日的最高收盘价；异动为收盘观察，不是盘中预警。")
        if universe["failures"]:
            warnings.insert(0, f"{len(universe['failures'])} 个文件读取失败，未作为新数据使用。" + "；".join(universe["failures"][:3]))
        if universe.get("cacheWarning"):
            warnings.insert(0, universe["cacheWarning"])
        if not universe["source"]:
            warnings.insert(0, "请先选择并扫描通达信日线目录")
        if as_of and universe["asOf"] and as_of != universe["asOf"]:
            warnings.insert(0, f"截至 {as_of} 的最近实际交易日为 {universe['asOf']}，未填充缺失交易日")
        items = features["items"]
        response = {"asOf": universe["asOf"], "requestedAsOf": as_of, "computedAt": universe["computedAt"],
                    "cached": universe["cached"], "source": universe["source"], "provider": "tdx_local_analysis",
                    "version": VERSION, "coverage": features["coverage"], "warnings": warnings, "parameters": params}
        response["sourceRevision"] = universe.get("sourceRevision", "")
        response["dataRevision"] = universe.get("dataRevision", "")
        response["stale"] = bool(universe.get("cacheWarning"))
        if tool == "trends":
            output = trends(items, params)
        elif tool == "moves":
            output = moves(items, params)
        elif tool == "rebounds":
            floor = universe["dates"][-params["lookbackDays"]] if len(universe["dates"]) >= params["lookbackDays"] else (universe["dates"][0] if universe["dates"] else "")
            output = [event for event in features["events"] if event["eventDate"] >= floor and event["priorBoards"] >= params["minBoards"] and params["minGap"] <= event["gapDays"] <= params["maxGap"]]
            output.sort(key=lambda item: (item["eventDate"], item["priorBoards"], item["amount"], item["symbol"]), reverse=True)
        elif tool == "ladder":
            def included(item):
                return (params["board"] == "all" or item["board"] == params["board"]) and (not params["excludeST"] or "ST" not in item["name"].upper() and "退" not in item["name"])
            output = sorted([item for item in items if item["limitUpApprox"] and included(item)], key=lambda item: (-item["boardHeight"], -item["amount"], item["symbol"]))
            response["groups"] = [{"height": height, "items": [self._public(item) for item in output if item["boardHeight"] == height]} for height in sorted({item["boardHeight"] for item in output}, reverse=True)]
            response["timeline"] = features["timeline"][-params["historyDays"]:]
            response["leaders"] = [self._public(item) for item in output if item["boardHeight"] >= params["minHeight"]]
            previous = [item for item in features["leadership"]["previousCandidates"] if included(item)]
            previous_max = max((item["previousHeight"] for item in previous), default=0)
            response["yesterdayLeaders"] = [item for item in previous if item["previousHeight"] >= (3 if previous_max >= 3 else 2)]
            response["yesterdayLeaders"].sort(key=lambda item: (-item["previousHeight"], -(item["amount"] or 0), item["symbol"]))
            floor = response["timeline"][0]["date"] if response["timeline"] else ""
            response["leadership"] = {"scope": "all_stocks", "asOf": universe["asOf"],
                                      "metrics": response["timeline"][-1]["metrics"] if response["timeline"] else {},
                                      "rotationEvents": [event for event in features["leadership"]["rotationEvents"] if event["date"] >= floor]}
            response["rules"] = {"timelineScope": "全市场股票，含当前名称 ST；不受榜单筛选影响",
                                 "leaderScope": "候选与昨日高标按交易板及 ST 筛选，最低高度只筛选当前候选",
                                 "promotion": "昨日相应板高且今日可核验的样本为分母；缺日、无成交或超名义涨跌幅不算断板",
                                 "trend": "连续 60 个市场日有成交，站上 MA20 与 MA60，最近 5 日的 20 日收益均为正；成交额占比不是资金净流入"}
        else:
            trend_items, move_items = trends(items, DEFAULTS["trends"]), moves(items, DEFAULTS["moves"])
            floor = universe["dates"][-60] if len(universe["dates"]) >= 60 else (universe["dates"][0] if universe["dates"] else "")
            events = [item for item in features["events"] if item["eventDate"] >= floor and 1 <= item["gapDays"] <= 10]
            response.update({"counts": {"trends": len(trend_items), "moves": len(move_items), "rebounds": len(events), "limitUp": sum(item["limitUpApprox"] for item in items)},
                             "recentEvents": sorted(events, key=lambda item: item["eventDate"], reverse=True)[:12],
                             "watchlistCount": self.owner.db.execute("SELECT COUNT(*) FROM analysis_watchlist WHERE status!='archived'").fetchone()[0],
                             "timeline": features["timeline"][-60:]})
            output = trend_items[:12]
        if tool in SORT_FIELDS:
            response["candidateTotal"] = len(output)
            query = params["query"].casefold()
            if query:
                output = [item for item in output if query in (item["symbol"] + " " + item["code"] + " " + item["name"]).casefold()]
            if params["sort"]:
                field = params["sort"]
                known = [item for item in output if item.get(field) is not None]
                unknown = [item for item in output if item.get(field) is None]
                # Stable ties retain the documented default order; unknowns remain last.
                output = sorted(known, key=lambda item: item[field], reverse=params["order"] == "desc") + unknown
            offset, limit = params["offset"], params["limit"]
            response.update({"offset": offset, "limit": limit, "hasMore": offset + limit < len(output)})
        else:
            offset, limit = 0, params.get("limit", 1000)
        response.update({"items": [self._public(item) for item in output[offset:offset + limit]], "total": len(output)})
        if hasattr(self.owner.research, "assert_current"):
            self.owner.research.assert_current(universe)
        return response

    @staticmethod
    def _public(item):
        return {key: value for key, value in item.items() if not key.startswith("_")}

    def watchlist(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"status"} or (payload.get("status") is not None and not isinstance(payload.get("status"), str)) or payload.get("status") not in STATUSES | {None, "all"}:
            raise ValueError("跟踪池状态不支持")
        rows = self.owner.db.execute("SELECT * FROM analysis_watchlist ORDER BY updated_at DESC,symbol").fetchall()
        items = [self._watch_public(row)
                 for row in rows if payload.get("status") in (None, "all", row["status"])]
        return {"items": items, "total": len(items)}

    def save_watchlist(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"symbol", "name", "note", "appendNote", "tags", "status", "research"}:
            raise ValueError("跟踪池字段不支持")
        if "appendNote" in payload and ("note" in payload or not isinstance(payload["appendNote"], str)
                or not payload["appendNote"].strip() or len(payload["appendNote"]) > 2000 or "\0" in payload["appendNote"]):
            raise ValueError("追加观察理由须为 1–2000 字，且不能同时替换原备注")
        market, code = tdx.symbol_parts(payload.get("symbol"))
        symbol = market + code
        with self.owner.db:
            # Read and append under one write reservation, so a concurrent observation
            # cannot replace a note read before the other writer committed.
            self.owner.db.execute("BEGIN IMMEDIATE")
            old = self.owner.db.execute("SELECT * FROM analysis_watchlist WHERE symbol=?", (symbol,)).fetchone()
            security = self.owner.db.execute("SELECT name FROM securities WHERE symbol=?", (symbol,)).fetchone()
            if not old and not security:
                raise ValueError("此证券尚未收录本地行情，请先下载并扫描日线后添加")
            status = payload.get("status", old["status"] if old else "watching")
            note = payload.get("note", old["note"] if old else "")
            if "appendNote" in payload:
                note = (note + "\n\n" if note else "") + payload["appendNote"].strip()
            tags = payload.get("tags", json.loads(old["tags"]) if old else [])
            if not isinstance(status, str) or status not in STATUSES:
                raise ValueError("跟踪池状态不支持")
            if not isinstance(note, str) or len(note) > 2000 or "\0" in note:
                raise ValueError("跟踪备注合计最多 2000 字且不能含空字符；原备注已保留")
            if not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 20 or "\0" in tag for tag in tags):
                raise ValueError("跟踪标签最多 8 个，每个 1–20 字")
            tags = list(dict.fromkeys(tag.strip() for tag in tags))
            research = self._research(payload["research"]) if "research" in payload else (json.loads(old["research"]) if old and old["research"] else None)
            now = _now()
            item = {"symbol": symbol, "code": code, "name": security["name"] if security else old["name"],
                    "status": status, "note": note, "tags": tags, "research": research,
                    "createdAt": old["created_at"] if old else now, "updatedAt": now}
            existing = {row["symbol"]: row for row in self.watchlist({})["items"]}
            existing[symbol] = item
            self._check_watch_size(list(existing.values()))
            self._put_watch(item)
        return item

    @staticmethod
    def _watch_public(row):
        return {"symbol": row["symbol"], "code": row["code"], "name": row["name"], "status": row["status"],
                "note": row["note"], "tags": json.loads(row["tags"]), "createdAt": row["created_at"],
                "updatedAt": row["updated_at"], "research": json.loads(row["research"]) if row["research"] else None}

    @staticmethod
    def _research(value):
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) - {"page", "asOf", "parameters", "source"}:
            raise ValueError("研究来源仅支持 page、asOf、parameters 与 source")
        page, source = value.get("page", ""), value.get("source", "")
        if not isinstance(page, str) or not 1 <= len(page) <= 40 or any(ord(char) < 32 for char in page):
            raise ValueError("研究来源页面须为 1 至 40 字")
        if not isinstance(source, str) or len(source) > 2048 or any(ord(char) < 32 for char in source):
            raise ValueError("研究来源路径最多 2048 字且不能含控制字符")
        as_of = validate_as_of(value.get("asOf"))
        params = value.get("parameters", {})
        def safe_json(item, depth=0):
            if depth > 4:
                return False
            if item is None or type(item) is bool:
                return True
            if type(item) in (int, float):
                return math.isfinite(item) and abs(item) <= 1e20
            if isinstance(item, str):
                return len(item) <= 2000 and "\0" not in item
            if isinstance(item, dict):
                return len(item) <= 100 and all(isinstance(key, str) and 1 <= len(key) <= 100 and "\0" not in key and safe_json(child, depth + 1) for key, child in item.items())
            if isinstance(item, list):
                return len(item) <= 100 and all(safe_json(child, depth + 1) for child in item)
            return False
        if not isinstance(params, dict) or not safe_json(params):
            raise ValueError("研究来源参数格式无效或过大")
        result = {"page": page, "asOf": as_of, "parameters": params, "source": source}
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 8192:
            raise ValueError("单条研究来源不能超过 8 KiB")
        return result

    @staticmethod
    def _watch_fields(status, note, tags):
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError("跟踪池状态不支持")
        if not isinstance(note, str) or len(note) > 2000 or "\0" in note:
            raise ValueError("跟踪备注最多 2000 字且不能含空字符")
        if not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 20 or "\0" in tag for tag in tags):
            raise ValueError("跟踪标签最多 8 个，每个 1–20 字")
        return list(dict.fromkeys(tag.strip() for tag in tags))

    @staticmethod
    def _stamp(value):
        try:
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError()
            stamp = dt.datetime.fromisoformat(value)
            if stamp.tzinfo is None:
                raise ValueError()
            return stamp
        except (ValueError, TypeError):
            raise ValueError("备份时间须为含时区的 ISO 时间") from None

    def _put_watch(self, item):
        self.owner.db.execute("INSERT OR REPLACE INTO analysis_watchlist (symbol,code,name,status,note,tags,created_at,updated_at,research) VALUES (?,?,?,?,?,?,?,?,?)",
                              (item["symbol"], item["code"], item["name"], item["status"], item["note"],
                               json.dumps(item["tags"], ensure_ascii=False), item["createdAt"], item["updatedAt"],
                               json.dumps(item["research"], ensure_ascii=False, allow_nan=False) if item["research"] is not None else None))

    def _check_watch_size(self, items):
        if len(items) > WATCHLIST_LIMIT:
            raise ValueError("跟踪池最多保存 5000 条记录")
        if len(json.dumps(items, ensure_ascii=False, allow_nan=False).encode("utf-8")) > WATCHLIST_BYTES:
            raise ValueError("跟踪池数据不能超过 20 MiB，请精简备注或研究参数")

    def save_many_watchlist(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"items"} or not isinstance(payload["items"], list) or not 1 <= len(payload["items"]) <= WATCHLIST_LIMIT:
            raise ValueError("批量添加须包含 1 至 5000 条记录")
        with self.owner.db:
            self.owner.db.execute("BEGIN IMMEDIATE")
            prepared, now = [], _now()
            for item in payload["items"]:
                if not isinstance(item, dict) or set(item) - {"symbol", "name", "status", "note", "tags", "research"}:
                    raise ValueError("批量跟踪池字段不支持")
                market, code = tdx.symbol_parts(item.get("symbol"))
                symbol = market + code
                old = self.owner.db.execute("SELECT name FROM analysis_watchlist WHERE symbol=?", (symbol,)).fetchone()
                security = self.owner.db.execute("SELECT name FROM securities WHERE symbol=?", (symbol,)).fetchone()
                if not old and not security:
                    raise ValueError("此证券尚未收录本地行情，请先下载并扫描日线后添加")
                status, note = item.get("status", "watching"), item.get("note", "")
                tags = self._watch_fields(status, note, item.get("tags", []))
                prepared.append({"symbol": symbol, "code": code, "name": security["name"] if security else old["name"],
                                 "status": status, "note": note, "tags": tags, "research": self._research(item.get("research")),
                                 "createdAt": now, "updatedAt": now})
            result = self._merge_watch(prepared)
        return result

    def _merge_watch(self, prepared):
        existing = {item["symbol"]: item for item in self.watchlist({})["items"]}
        added = []
        for item in prepared:
            if item["symbol"] not in existing:
                existing[item["symbol"]] = item
                added.append(item)
        self._check_watch_size(list(existing.values()))
        for item in added:
            self._put_watch(item)
        return {"added": len(added), "skipped": len(prepared) - len(added), "total": len(existing), "items": added}

    def export_watchlist(self, payload):
        if not isinstance(payload, dict) or payload:
            raise ValueError("导出备份不接受筛选参数")
        from service import watchlist_backup
        return watchlist_backup.export(self)

    def import_watchlist(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"backup", "mode"} or payload.get("mode", "merge") != "merge":
            raise ValueError("跟踪池导入仅支持 merge 合并模式")
        from service import watchlist_backup
        records = watchlist_backup.validate(payload.get("backup"))
        columns = ("symbol", "code", "name", "status", "note", "tags", "created_at", "updated_at", "research")
        prepared = [self._watch_public(dict(zip(columns, row))) for row in records]
        self._check_watch_size(prepared)
        # Restore independent observations even if the former TDX source is absent.
        # Existing records win, including notes, dates and research provenance.
        with self.owner.db:
            self.owner.db.execute("BEGIN IMMEDIATE")
            result = self._merge_watch(prepared)
        return result

    def delete_watchlist(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"symbol"}:
            raise ValueError("请指定要移除的证券")
        market, code = tdx.symbol_parts(payload.get("symbol"))
        symbol = market + code
        with self.owner.db:
            deleted = self.owner.db.execute("DELETE FROM analysis_watchlist WHERE symbol=?", (symbol,)).rowcount > 0
        return {"deleted": deleted, "symbol": symbol}
