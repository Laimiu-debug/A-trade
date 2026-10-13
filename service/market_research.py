"""Source-qualified, daily market breadth and screening snapshots.

The rules are transparent price/volume observations, adapted from TRADE's
market_path_domain.py. They do not infer industries, market capitalisation,
historical membership, adjusted prices, or an external popularity ranking.
"""
from __future__ import annotations

from bisect import bisect_right
from array import array
import datetime as dt
import json
import math
import hashlib
import struct
import sys
import uuid
from pathlib import Path
from statistics import median

from service import tdx
from service.leader_research import Leadership, limit_flags
from service.market_style import profile as style_profile, RULES as PROFILE_RULES


VERSION = "local-market-research-v6-multidimensional"
SNAPSHOT_VERSION = "local-market-snapshot-v1"
TAIL_RECORDS = 120
HISTORY_CHOICES = (60, 120, 250, 500, 1000)
WARMUP_RECORDS = 100
MIN_MARKET_SYMBOLS = 1000
MIN_COVERAGE = .7
CONFIRM_DAYS = 2
CONFIRM_MAX_AGE = 20
LABELS = {"insufficient": "数据不足", "defensive": "退潮防守", "boards": "连板活跃",
          "speculative": "高弹性活跃", "trend": "趋势扩散", "rotation": "震荡轮动", "unconfirmed": "风格待确认"}
LIMITATIONS = [
    "风格仅为日线价格与成交量代理观察，不代表实时盘面，也不构成交易信号。",
    "样本来自当前通达信目录，缺少历史上市与退市成分，历史风格存在存续样本偏差。",
    "价格未复权，除权、分红和送转可能影响收益、均线及风格判断。",
    "涨跌停与连板为按交易板及当前 ST 名称估算的收盘价格代理；未还原历史 ST 身份、新股无涨跌幅限制、除权参考价等规则，不能替代交易所涨停名单。",
    "名义限价按前收四舍五入到分：主板普通 10%；主板 ST 在 2026-07-06 前 5%、起 10%；创业板在 2020-08-24 起 20%（此前普通 10%、ST 5%）；科创板 20%、北交所 30%。",
    "仅校验各证券最近 120 根日线；缺日不补值，排名统一交易日，旧日证券不参与当日榜单与风格。",
    "本地数据不含可靠行业、市值与外部人气字段；热门池只能按实际成交额、涨幅或放量规则观察。",
    "六维风格仅使用量价证据，不能据此判定成长/价值、大小盘、机构/游资或资金净流入；量价观察分档未经收益优化。",
]


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def _rounded(value, places=6):
    return round(value, places) if value is not None and math.isfinite(value) else None


def board_of(symbol, kind):
    if kind == "index":
        return "index"
    if symbol.startswith("bj"):
        return "bse"
    if symbol.startswith(("sh68",)):
        return "star"
    if symbol.startswith("sz30"):
        return "chinext"
    return "main"


def read_raw_tail(path, limit=TAIL_RECORDS, as_of=None):
    """Read bounded complete records without reading decades of older history."""
    try:
        with path.open("rb") as handle:
            before = path.stat()
            tdx.validate_size(before.st_size)
            end = before.st_size // tdx.DAY.size
            if as_of:
                target = int(as_of.replace("-", ""))
                left, right = 0, end
                while left < right:
                    middle = (left + right) // 2
                    handle.seek(middle * tdx.DAY.size)
                    record = handle.read(tdx.DAY.size)
                    if len(record) != tdx.DAY.size:
                        raise ValueError("日线读取不完整")
                    tdx.decode_record(record)
                    if struct.unpack_from("<I", record)[0] <= target:
                        left = middle + 1
                    else:
                        right = middle
                end = left
            count = min(end, limit)
            handle.seek((end - count) * tdx.DAY.size)
            raw = handle.read(count * tdx.DAY.size)
            after = path.stat()
        if tdx._signature(before) != tdx._signature(after) or len(raw) != count * tdx.DAY.size:
            raise ValueError("通达信正在更新日线文件，请更新完成后刷新")
        rows = tdx.parse_daily(raw) if raw else []
        if rows and rows[-1]["date"] > (as_of or _today()):
            raise ValueError("日线含有未来日期，未加入当日研究")
        return raw, {sys.intern(row["date"]) for row in rows}
    except OSError:
        raise ValueError("日线文件无法读取") from None


def rows_from_raw(raw):
    rows = []
    for date, _opening, _high, _low, close, amount, volume, _reserved in tdx.DAY.iter_unpack(raw):
        day = sys.intern(f"{date // 10000:04d}-{date // 100 % 100:02d}-{date % 100:02d}")
        rows.append((day, close / 100, volume, amount))
    return rows


def read_tail(path, limit=TAIL_RECORDS, as_of=None):
    raw, _ = read_raw_tail(path, limit, as_of)
    return rows_from_raw(raw)


def validate_as_of(value):
    if value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value or value > _today():
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError("截至日期须为 YYYY-MM-DD，且不能晚于今天") from None


def sort_items(items, key="amount", descending=True):
    """Deterministic numeric ordering; unknown values always follow known ones."""
    if key not in {"amount", "volume", "close", "changePercent", "return5", "return20", "volumeRatio5"}:
        raise ValueError("不支持的排名字段")
    return sorted(items, key=lambda item: (item.get(key) is None,
                  (-item[key] if descending else item[key]) if item.get(key) is not None else 0,
                  item["symbol"]))


def _progress(callback, stage, completed, total, start, span):
    if callback:
        callback({"stage": stage, "completed": completed, "total": total,
                  "percent": round(start + span * min(1, completed / total if total else 1), 1)})


def _current_item(security, aligned, as_of):
    """Identical current-day features for the light list and full style history."""
    rows, kind = security["rows"], security["kind"]
    base = {key: security[key] for key in ("symbol", "code", "name", "market", "kind")}
    base.update({"board": board_of(security["symbol"], kind), "date": rows[-1][0], "close": rows[-1][1],
                 "amount": rows[-1][3], "volume": rows[-1][2], "changePercent": None,
                 "return5": None, "return20": None, "volumeRatio5": None, "ma20": None,
                 "aboveMA20": None, "sparkline": [row[1] if row else None for row in aligned[-20:]],
                 "stale": rows[-1][0] != as_of, "volumeUnit": "股" if kind == "stock" else "手"})
    if not aligned or aligned[-1] is None or aligned[-1][0] != as_of:
        return base
    row = aligned[-1]
    previous = aligned[-2] if len(aligned) >= 2 else None
    def period_return(period):
        prior = aligned[-period - 1] if len(aligned) > period else None
        return (row[1] / prior[1] - 1) * 100 if prior else None
    window20, previous5 = aligned[-20:], aligned[-6:-1]
    ma20 = sum(value[1] for value in window20) / 20 if len(window20) == 20 and all(window20) else None
    volume = sum(value[2] for value in previous5) / 5 if len(previous5) == 5 and all(previous5) else None
    base.update({"changePercent": _rounded((row[1] / previous[1] - 1) * 100 if previous else None),
                 "return5": _rounded(period_return(5)), "return20": _rounded(period_return(20)),
                 "volumeRatio5": _rounded(row[2] / volume if volume else None), "ma20": _rounded(ma20),
                 "aboveMA20": row[1] > ma20 if ma20 is not None else None})
    return base


def build_current_snapshot(inputs, *, total, source, calendar, as_of, computed_at,
                           progress=None, progress_total=0):
    """Only one feature row per security; no style timeline or leadership work."""
    dates = list(calendar)
    indexes = {day: index for index, day in enumerate(dates)}
    items, stale_items = [], []
    for count, security in enumerate(inputs, 1):
        aligned = [None] * len(dates)
        for row in security["rows"]:
            index = indexes.get(row[0])
            if index is not None:
                aligned[index] = row
        item = _current_item(security, aligned, as_of)
        (stale_items if item["stale"] else items).append(item)
        if count % 50 == 0 or count == progress_total:
            _progress(progress, "计算当前股票特征", count, progress_total, 25, 70)
    valid = sum(item["kind"] == "stock" for item in items)
    stale = sum(item["kind"] == "stock" for item in stale_items)
    return {"items": sort_items(items), "staleItems": sorted(stale_items, key=lambda item: item["symbol"]),
            "asOf": as_of, "computedAt": computed_at, "cached": False,
            "provider": "tdx_local_snapshot", "source": source, "version": SNAPSHOT_VERSION,
            "sparklineDates": dates[-20:],
            "coverage": {"total": total, "valid": valid, "excluded": max(0, total - valid - stale),
                         "stale": stale, "indexCount": sum(item["kind"] == "index" for item in items)},
            "warnings": ["价格未复权，除权、分红和送转可能影响收益、均线及涨跌幅。",
                         "股票与指数按统一交易日观察；小图按市场近 20 个交易日对齐，缺日保持断线。",
                         "5 日均量比为当日成交量除以前五个市场交易日均量；缺日或零分母不计算。"]}


def _raw_style(metrics):
    m = metrics
    if m["expectedCount"] < MIN_MARKET_SYMBOLS or m["coverage"] < MIN_COVERAGE:
        return "insufficient"
    if m["breadth"] < .30 or m["medianReturn20"] < -6 or m["limitDownApprox"] >= max(20, m["limitUpApprox"] * 2):
        return "defensive"
    if m["maxBoardHeight"] >= 4 and m["multiBoardApprox"] >= 20 and m["limitUpApprox"] >= 30:
        return "boards"
    if m["wildCount"] / m["sampleCount"] >= .02 and m["wildAmountShare"] >= .15:
        return "speculative"
    if m["breadth"] >= .55 and m["medianReturn20"] > 2:
        return "trend"
    return "rotation"


def _reason(key, metrics, pending=None, pending_count=0):
    if key == "unconfirmed":
        return (f"最近 {CONFIRM_MAX_AGE} 个交易日没有连续 {CONFIRM_DAYS} 日确认同一普通风格，也未触发即时防守；"
                f"旧标签已过期，当日观察为{LABELS[pending]}，暂待连续确认。")
    if pending:
        return (f"当日价格观察为{LABELS[pending]}，连续确认 {pending_count}/{CONFIRM_DAYS} 日；"
                f"当前标签仍为此前确认的{LABELS[key]}。")
    if key == "insufficient":
        text = (f"需至少 {MIN_MARKET_SYMBOLS} 只有 60 日观察历史的股票，且当日有效覆盖达到 70%；"
                f"当前有效 {metrics['sampleCount']} / 应观察 {metrics['expectedCount']} 只。")
    else:
        text = {
            "defensive": "站上 MA20 占比低于 30%、20 日中位收益低于 -6%，或近似跌停显著占优。",
            "boards": "近似最高连板不少于 4，连板股票不少于 20 只，近似涨停不少于 30 只。",
            "speculative": "10 日涨幅超过 30% 的股票占有效样本至少 2%，其成交额占比至少 15%。",
            "trend": "站上 MA20 占比至少 55%，且 20 日中位收益大于 2%。",
            "rotation": "覆盖充足，未达到退潮、连板、高弹性或趋势扩散阈值。",
        }[key]
    if key not in ("insufficient", "defensive"):
        text += f" 非防守风格需连续 {CONFIRM_DAYS} 日确认。"
    return text


def confirm_styles(raw_keys):
    """A bounded confirmation state prevents window-dependent old labels.

    An isolated recurrence of the old label does not renew confirmation; only
    two consecutive ordinary observations (or an immediate defensive/coverage
    event) renew the 20-session lifetime.
    """
    style, previous, count, confirmed_at = "insufficient", None, 0, None
    for index, raw in enumerate(raw_keys):
        count = count + 1 if previous == raw else 1
        previous = raw
        if raw in ("insufficient", "defensive") or count >= CONFIRM_DAYS:
            style, confirmed_at = raw, index
        elif index - (confirmed_at if confirmed_at is not None else 0) > CONFIRM_MAX_AGE:
            style = "unconfirmed"
        pending = raw if raw != style else None
        yield {"key": style, "rawKey": raw, "pendingKey": pending,
               "pendingCount": count if pending else 0, "confirmationExpired": style == "unconfirmed"}


def build_snapshot(inputs, *, total, failures=0, source="", computed_at=None,
                   history_days=20, calendar=None, as_of=None, history_bars=TAIL_RECORDS,
                   progress=None, progress_total=0):
    """Calculate aligned features; every historical point uses only its own past."""
    if calendar is None:
        inputs = list(inputs)
        stock_inputs = [item for item in inputs if item["kind"] == "stock"]
        as_of = max((item["rows"][-1][0] for item in stock_inputs), default=None)
        if as_of is None:
            as_of = max((item["rows"][-1][0] for item in inputs), default=None)
        dates = sorted({row[0] for item in stock_inputs for row in item["rows"] if row[0] <= as_of})[-history_bars:] if as_of else []
        if not dates and as_of:
            dates = sorted({row[0] for item in inputs for row in item["rows"] if row[0] <= as_of})[-history_bars:]
    else:
        dates = list(calendar)
    date_indexes = {day: i for i, day in enumerate(dates)}
    stats = [{"expected": 0, "returns": array("d"), "returns5": array("d"), "changes": array("d"),
              "amountRatios": array("d"), "preferenceAmountCount": 0,
              "pairedBreadthCount": 0, "pairedAboveNow": 0, "pairedAboveBefore": 0,
              "above": 0, "up": 0, "down": 0, "flat": 0,
              "limitUp": 0, "limitDown": 0, "multi": 0, "height": 0,
              "wild": 0, "wildAmount": 0, "amount": 0} for _ in dates]
    leadership = Leadership(dates)
    items, stale_items = [], []
    for completed, security in enumerate(inputs, 1):
        rows, kind, name = security["rows"], security["kind"], security["name"]
        board = board_of(security["symbol"], kind)
        aligned = [None] * len(dates)
        for row in rows:
            index = date_indexes.get(row[0])
            if index is not None:
                aligned[index] = row
        base = _current_item(security, aligned, as_of)
        observations = [row[0] for row in rows]
        leadership.add(security, aligned, board)
        height, positive_volume_run = 0, 0
        for i, day in enumerate(dates):
            count = bisect_right(observations, day)
            if kind == "stock" and count >= 60:
                stats[i]["expected"] += 1
            row = aligned[i]
            if row is None:
                height, positive_volume_run = 0, 0
                continue
            positive_volume_run = positive_volume_run + 1 if row[2] > 0 else 0
            previous = aligned[i - 1] if i else None
            change = row[1] / previous[1] - 1 if previous else None
            def period_return(period):
                start = aligned[i - period] if i >= period else None
                return (row[1] / start[1] - 1) * 100 if start else None
            window20 = aligned[i - 19:i + 1] if i >= 19 else []
            ma20 = sum(value[1] for value in window20) / 20 if len(window20) == 20 and all(window20) else None
            ret20 = period_return(20)
            up, down, _ = limit_flags(previous, row, board, name)
            height = height + 1 if up else 0
            window60 = aligned[i - 59:i + 1] if i >= 59 else []
            usable = kind == "stock" and len(window60) == 60 and all(window60) and ret20 is not None and row[2] > 0 and previous is not None
            if usable:
                stat = stats[i]
                stat["returns"].append(ret20)
                stat["returns5"].append(period_return(5))
                stat["changes"].append(change * 100)
                stat["preferenceAmountCount"] += int(positive_volume_run >= 60 and row[3] > 0)
                previous_amounts = aligned[i - 20:i]
                if row[3] > 0 and all(value[2] > 0 and value[3] > 0 for value in previous_amounts):
                    stat["amountRatios"].append(row[3] / (sum(value[3] for value in previous_amounts) / 20))
                # Current usable has 60 aligned rows. Five earlier rows prove
                # that this SAME security was also usable five sessions ago.
                if i >= 64 and all(aligned[i - 64:i - 59]) and aligned[i - 5][2] > 0:
                    previous_ma20 = sum(value[1] for value in aligned[i - 24:i - 4]) / 20
                    stat["pairedBreadthCount"] += 1
                    stat["pairedAboveNow"] += int(row[1] > ma20)
                    stat["pairedAboveBefore"] += int(aligned[i - 5][1] > previous_ma20)
                stat["above"] += int(row[1] > ma20)
                stat["up"] += int(change > 0)
                stat["down"] += int(change < 0)
                stat["flat"] += int(change == 0)
                stat["limitUp"] += int(up)
                stat["limitDown"] += int(down)
                stat["multi"] += int(height >= 2)
                stat["height"] = max(stat["height"], height)
                stat["amount"] += row[3]
                if (period_return(10) or 0) > 30:
                    stat["wild"] += 1
                    stat["wildAmount"] += row[3]
        (stale_items if base["stale"] else items).append(base)
        if completed % 50 == 0 or completed == progress_total:
            _progress(progress, "计算市场风格", completed, progress_total, 25, 70)
    leader_result = leadership.finish()
    leader_rows = {row["date"]: row for row in leader_result["timeline"]}
    metric_rows = []
    for day, stat in zip(dates, stats):
        count = len(stat["returns"])
        metrics = {"sampleCount": count, "expectedCount": stat["expected"],
                   "coverage": _rounded(count / stat["expected"]) if stat["expected"] else 0,
                   "breadth": _rounded(stat["above"] / count) if count else None,
                   "advanceRatio": _rounded(stat["up"] / count) if count else None,
                   "declineRatio": _rounded(stat["down"] / count) if count else None,
                   "medianChangePercent": _rounded(median(stat["changes"])) if count else None,
                   "medianReturn5": _rounded(median(stat["returns5"])) if count else None,
                   "medianReturn20": _rounded(median(stat["returns"])) if count else None,
                   "medianAmountRatio20": _rounded(median(stat["amountRatios"])) if stat["amountRatios"] else None,
                   "amountRatio20Count": len(stat["amountRatios"]),
                   "preferenceAmountCount": stat["preferenceAmountCount"],
                   "breadthComparableCount": stat["pairedBreadthCount"],
                   "breadthChange5": _rounded((stat["pairedAboveNow"] - stat["pairedAboveBefore"]) / stat["pairedBreadthCount"] * 100) if stat["pairedBreadthCount"] else None,
                   "advanceCount": stat["up"], "declineCount": stat["down"], "flatCount": stat["flat"],
                   "limitUpApprox": leader_rows[day]["metrics"]["limitUpCount"], "limitDownApprox": leader_rows[day]["metrics"]["risk"]["limitDownCount"],
                   "multiBoardApprox": leader_rows[day]["metrics"]["multiBoardCount"], "maxBoardHeight": leader_rows[day]["metrics"]["maxHeight"],
                   "wildCount": stat["wild"], "wildAmountShare": _rounded(stat["wildAmount"] / stat["amount"]) if stat["amount"] else 0}
        metrics["leadership"] = leader_rows[day]["metrics"]
        metric_rows.append(metrics)
    timeline = []
    previous_profile = None
    for day, metrics, state in zip(dates, metric_rows, confirm_styles(_raw_style(metrics) for metrics in metric_rows)):
        current_profile = style_profile(metrics, previous_profile, min_market_symbols=MIN_MARKET_SYMBOLS, min_coverage=MIN_COVERAGE)
        timeline.append({"date": day, **state, "label": LABELS[state["key"]],
                         "confirmationDays": CONFIRM_DAYS,
                         "reason": _reason(state["key"], metrics, state["pendingKey"], state["pendingCount"]),
                         "metrics": metrics, "profile": current_profile,
                         "leadership": {"scope": "all_stocks", "asOf": day, "metrics": metrics["leadership"],
                                        "rotationEvents": [leader_rows[day]["rotationEvent"]] if leader_rows[day]["rotationEvent"] else []}})
        previous_profile = current_profile
    empty_metrics = {"sampleCount": 0, "expectedCount": 0, "coverage": 0, "breadth": None,
                     "medianReturn20": None, "advanceCount": 0, "declineCount": 0, "flatCount": 0,
                     "limitUpApprox": 0, "limitDownApprox": 0, "multiBoardApprox": 0,
                     "maxBoardHeight": 0, "wildCount": 0, "wildAmountShare": 0}
    current = {key: value for key, value in timeline[-1].items() if key != "date"} if timeline else {
        "key": "insufficient", "label": LABELS["insufficient"], "rawKey": "insufficient", "pendingKey": None,
        "pendingCount": 0, "confirmationDays": CONFIRM_DAYS, "reason": "请先下载并扫描本地日线。", "metrics": empty_metrics,
        "profile": style_profile(empty_metrics, min_market_symbols=MIN_MARKET_SYMBOLS, min_coverage=MIN_COVERAGE)}
    valid = sum(item["kind"] == "stock" for item in items)
    stale = sum(item["kind"] == "stock" for item in stale_items)
    return {"items": sort_items(items), "staleItems": sorted(stale_items, key=lambda item: item["symbol"]),
            "asOf": as_of, "computedAt": computed_at or _now(), "cached": False,
            "provider": "tdx_local_research", "source": source, "version": VERSION,
            "sparklineDates": dates[-20:],
            "coverage": {"total": total, "valid": valid, "excluded": max(0, total - valid - stale),
                         "stale": stale, "indexCount": sum(item["kind"] == "index" for item in items)},
            "style": current, "timeline": timeline[-history_days:],
            "historyDays": history_days,
            "warnings": [warning.replace("最近 120 根", f"最近 {history_bars} 根") for warning in LIMITATIONS],
            "rules": {"minMarketSymbols": MIN_MARKET_SYMBOLS, "minCoverage": MIN_COVERAGE,
                      "confirmDays": CONFIRM_DAYS, "confirmationMaxAge": CONFIRM_MAX_AGE, "historyBars": history_bars,
                      "leadershipScope": "全市场股票，包括当前名称 ST；连板指标与分析工具同口径，广度概况仍需 60 日日线样本",
                      "trendStrength": "连续 60 个市场日有成交、站上 MA20 与 MA60，且最近 5 日的 20 日收益均为正",
                      "promotion": "昨日连板且今日可核验为分母；缺日、无成交、超名义涨跌幅单列未观测；分母为零返回空值",
                      "profile": PROFILE_RULES,
                      "volumeRatio5": "当日成交量 / 前五个市场交易日平均成交量；不含当日，缺日则不计算"}}


class MarketResearch:
    def __init__(self, owner):
        self.owner = owner
        self._universe = None
        self._generation = 0
        self.progress = None
        owner.db.execute("CREATE TABLE IF NOT EXISTS research_source_failure (source TEXT NOT NULL, as_of TEXT NOT NULL, revision TEXT NOT NULL, warning TEXT NOT NULL, failures TEXT NOT NULL, PRIMARY KEY(source,as_of))")
        owner.db.execute("CREATE TABLE IF NOT EXISTS research_data_state (source TEXT PRIMARY KEY, source_revision TEXT NOT NULL, signature TEXT NOT NULL, revision TEXT NOT NULL)")
        owner.db.execute("CREATE TABLE IF NOT EXISTS research_cache_v2 (source TEXT NOT NULL, history_days INTEGER NOT NULL, as_of TEXT NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(source,history_days,as_of))")
        owner.db.execute("CREATE TABLE IF NOT EXISTS market_snapshot_cache (source TEXT NOT NULL, as_of TEXT NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(source,as_of))")
        owner.db.commit()

    def close(self):
        self._universe = None

    def invalidate(self):
        self._universe = None
        source = self.owner._get("path")
        if source:
            with self.owner.db:
                self.owner.db.execute("DELETE FROM research_cache_v2 WHERE source=?", (source,))
                self.owner.db.execute("DELETE FROM market_snapshot_cache WHERE source=?", (source,))

    def assert_current(self, result):
        if result["source"] != (self.owner._get("path") or "") or result.get("sourceRevision") != self.owner.source_revision():
            raise ValueError("行情来源或目录索引已更新，请重新读取")
        if "dataRevision" in result and result["dataRevision"] != self._data_revision(result["source"], result["sourceRevision"]):
            raise ValueError("行情数据已更新，请重新读取")

    def _data_revision(self, source, source_revision):
        row = self.owner.db.execute("SELECT revision FROM research_data_state WHERE source=? AND source_revision=?", (source, source_revision)).fetchone()
        return row[0] if row else ""

    def _is_current(self, result):
        try:
            self.assert_current(result)
            return "dataRevision" in result
        except ValueError:
            return False

    def _failure(self, source, as_of, revision, warning, failures=None, data_revision=None):
        with self.owner.db:
            self.owner.db.execute("BEGIN IMMEDIATE")
            self.assert_current({"source": source, "sourceRevision": revision})
            current_data = self._data_revision(source, revision)
            if data_revision is not None and data_revision != current_data:
                raise ValueError("行情数据已更新，请重新读取")
            self.owner.db.execute("INSERT OR REPLACE INTO research_source_failure VALUES (?,?,?,?,?)",
                                  (source, as_of or "", current_data, warning, json.dumps(failures or [], ensure_ascii=False)))

    def _failure_state(self, source, as_of, revision):
        row = self.owner.db.execute("SELECT warning,failures FROM research_source_failure WHERE source=? AND as_of=? AND revision=?",
                                    (source, as_of or "", revision)).fetchone()
        return {"cacheWarning": row[0], "failures": json.loads(row[1]), "stale": True} if row else {}

    def _with_failure(self, result, as_of):
        self.assert_current(result)
        result = {**result, "warnings": list(result.get("warnings", []))}
        failure = self._failure_state(result["source"], as_of, result.get("dataRevision", ""))
        if failure:
            result["stale"] = True
            if failure["cacheWarning"] not in result["warnings"]:
                result["warnings"].insert(0, failure["cacheWarning"])
        return result

    def universe(self, *, as_of=None, records=330, refresh=False):
        """Cache compact validated source bytes, then decode one security at a time.

        A longer resident window can serve shorter tools without another disk scan.
        No raw histories are returned over IPC or retained as millions of dicts.
        """
        source = self.owner._get("path") or ""
        revision = self.owner.source_revision()
        data_revision = self._data_revision(source, revision)
        prior = self._universe
        if prior and (prior["source"] != source or prior.get("sourceRevision") != revision or prior.get("dataRevision") != data_revision):
            prior = self._universe = None
        if not refresh and prior and prior["requestedAsOf"] == as_of and prior["records"] >= records:
            _progress(self.progress, "读取日线", len(prior["inputs"]), len(prior["inputs"]), 0, 25)
            return {**prior, **self._failure_state(source, as_of, data_revision), "cached": True}
        empty = {"source": source, "requestedAsOf": as_of, "records": records, "inputs": [], "generation": self._generation,
                 "sourceRevision": revision, "dataRevision": data_revision, "dates": [], "asOf": None, "total": 0, "failures": [], "cached": False, "computedAt": _now()}
        if not source:
            return empty
        try:
            root = tdx.normalize_directory(source)
        except ValueError:
            self._failure(source, as_of, revision, "所选日线目录当前不可访问，保留此前研究快照；数据尚未更新", data_revision=data_revision)
            if prior and prior["requestedAsOf"] == as_of:
                return {**prior, **self._failure_state(source, as_of, data_revision), "cached": True}
            raise
        securities = self.owner.db.execute("SELECT symbol,code,name,market,kind FROM securities ORDER BY symbol").fetchall()
        vipdoc = tdx.within(root / "vipdoc", root)
        folders = {market: tdx.within(vipdoc / market / "lday", vipdoc) for market in tdx.MARKETS}
        inputs, failures, stock_dates, index_dates = [], [], set(), set()
        not_observed, failed_stocks = 0, 0
        latest_stock, latest_index = None, None
        signatures = hashlib.sha256()
        _progress(self.progress, "读取日线", 0, len(securities), 0, 25)
        for completed, security in enumerate(securities, 1):
            try:
                path = folders[security["market"]] / f"{security['symbol']}.day"
                if path.is_symlink():
                    path = tdx.within(path, vipdoc)
                raw, dates = read_raw_tail(path, records, as_of)
                signatures.update(json.dumps([dict(security), tdx._signature(path.stat())], ensure_ascii=False, sort_keys=True).encode("utf-8"))
                if not raw:
                    not_observed += int(security["kind"] == "stock")
                    continue  # Not yet listed by the selected cutoff.
                inputs.append({**dict(security), "raw": raw})
                last = max(dates)
                if security["kind"] == "stock":
                    stock_dates.update(dates)
                    latest_stock = max(latest_stock or last, last)
                else:
                    index_dates.update(dates)
                    latest_index = max(latest_index or last, last)
            except (ValueError, OSError) as error:
                message = str(error) if isinstance(error, ValueError) else "日线文件无法读取"
                failures.append(f"{security['symbol']}：{message}")
                signatures.update(json.dumps([dict(security), message], ensure_ascii=False, sort_keys=True).encode("utf-8"))
                failed_stocks += int(security["kind"] == "stock")
            finally:
                if completed % 50 == 0 or completed == len(securities):
                    _progress(self.progress, "读取日线", completed, len(securities), 0, 25)
        latest = latest_stock or latest_index
        dates = sorted(day for day in (stock_dates or index_dates) if latest and day <= latest)[-records:]
        result = {**empty, "inputs": inputs, "dates": dates, "asOf": latest, "failures": failures,
                  "total": sum(item["kind"] == "stock" for item in securities),
                  "notYetObserved": not_observed, "failedStocks": failed_stocks}
        if source != (self.owner._get("path") or "") or revision != self.owner.source_revision():
            raise ValueError("行情来源或目录索引已更新，请重新读取")
        # A wholly damaged refresh must not replace a usable resident snapshot.
        if not inputs and failures:
            self._failure(source, as_of, revision, "本次未能读取有效日线，保留此前研究快照；数据尚未更新", failures, data_revision)
            if prior and prior["source"] == source and prior["requestedAsOf"] == as_of:
                self._universe = {**prior, **self._failure_state(source, as_of, data_revision)}
                return {**self._universe, "cached": True}
        else:
            self._generation += 1
            result["generation"] = self._generation
            # Keep the data epoch separate from the configuration/catalogue epoch
            # used by frozen research jobs. The signature is cutoff/window agnostic.
            with self.owner.db:
                self.owner.db.execute("BEGIN IMMEDIATE")
                self.assert_current({"source": source, "sourceRevision": revision})
                state = self.owner.db.execute("SELECT signature,revision FROM research_data_state WHERE source=? AND source_revision=?", (source, revision)).fetchone()
                signature = signatures.hexdigest()
                if state and state[1] != data_revision and state[0] != signature:
                    raise ValueError("行情数据已更新，请重新读取")
                recovering = self.owner.db.execute("SELECT 1 FROM research_source_failure WHERE source=? AND revision=? LIMIT 1", (source, data_revision)).fetchone()
                if refresh or recovering or not state or state[0] != signature:
                    result["dataRevision"] = uuid.uuid4().hex
                    self.owner.db.execute("INSERT OR REPLACE INTO research_data_state VALUES (?,?,?,?)", (source, revision, signature, result["dataRevision"]))
                    self.owner.db.execute("DELETE FROM research_cache_v2 WHERE source=?", (source,))
                    self.owner.db.execute("DELETE FROM market_snapshot_cache WHERE source=?", (source,))
                else:
                    result["dataRevision"] = state[1]
                self.owner.db.execute("DELETE FROM research_source_failure WHERE source=?", (source,))
            self._universe = result
        return result

    @staticmethod
    def iter_inputs(universe, records=None):
        for security in universe["inputs"]:
            raw = security["raw"][-records * tdx.DAY.size:] if records else security["raw"]
            yield {key: value for key, value in security.items() if key != "raw"} | {"rows": rows_from_raw(raw)}

    def snapshot(self, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict) or set(payload) - {"refresh", "historyDays", "asOf"} or type(payload.get("refresh", False)) is not bool:
            raise ValueError("研究参数仅支持 refresh、historyDays 与 asOf")
        days = payload.get("historyDays", 250)
        if type(days) is not int or days not in HISTORY_CHOICES:
            raise ValueError("风格历史天数须为 60、120、250、500 或 1000")
        as_of = validate_as_of(payload.get("asOf"))
        source = self.owner._get("path") or ""
        cached = self.owner.db.execute("SELECT data FROM research_cache_v2 WHERE source=? AND history_days=? AND as_of=? AND version=?", (source, days, as_of or "", VERSION)).fetchone()
        if cached and not self._is_current(json.loads(cached[0])):
            cached = None
        if cached and not payload.get("refresh"):
            result = json.loads(cached[0]); result["cached"] = True
            _progress(self.progress, "读取已验证缓存", 1, 1, 100, 0)
            return self._with_failure(result, as_of)
        try:
            universe = self.universe(as_of=as_of, records=days + WARMUP_RECORDS, refresh=payload.get("refresh", False))
        except ValueError:
            if not cached or not self._is_current(json.loads(cached[0])):
                raise
            result = json.loads(cached[0]); result["cached"] = True
            result["warnings"].insert(0, "所选日线目录当前不可访问，保留此前研究快照；数据尚未更新")
            _progress(self.progress, "保留已验证缓存", 1, 1, 100, 0)
            return self._with_failure(result, as_of)
        if not universe["inputs"] and cached:
            result = json.loads(cached[0]); result["cached"] = True
            result["warnings"].insert(0, "本次未能读取有效日线，保留此前研究快照；数据尚未更新")
            _progress(self.progress, "保留已验证缓存", 1, 1, 100, 0)
            return self._with_failure(result, as_of)
        result = build_snapshot(self.iter_inputs(universe, days + WARMUP_RECORDS), total=universe["total"], source=source,
                                computed_at=universe["computedAt"], history_days=days, calendar=universe["dates"][-(days + WARMUP_RECORDS):],
                                as_of=universe["asOf"], history_bars=days + WARMUP_RECORDS,
                                progress=self.progress, progress_total=len(universe["inputs"]))
        result["requestedAsOf"] = as_of
        result["sourceRevision"] = universe["sourceRevision"]
        result["dataRevision"] = universe["dataRevision"]
        result["cached"] = universe["cached"]
        if universe.get("cacheWarning"):
            result["warnings"].insert(0, universe["cacheWarning"])
        if universe["failures"]:
            result["warnings"].insert(0, f"{len(universe['failures'])} 个日线文件未通过读取校验，已排除。" + "；".join(universe["failures"][:3]))
        if not source or not universe["inputs"]:
            result["warnings"].insert(0, "请先在设置中选择通达信目录并扫描日线；所选截至日可能尚无记录")
        elif result["coverage"]["stale"]:
            result["warnings"].insert(0, f"{result['coverage']['stale']} 只股票的最后日线早于统一排名日，已从当日榜单及当日风格中排除")
        with self.owner.db:
            self.owner.db.execute("BEGIN IMMEDIATE")
            self.assert_current(universe)
            self.owner.db.execute("INSERT OR REPLACE INTO research_cache_v2 VALUES (?,?,?,?,?)",
                                  (source, days, as_of or "", VERSION, json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))))
        _progress(self.progress, "市场风格已完成", 1, 1, 100, 0)
        return self._with_failure(result, as_of)

    def current_snapshot(self, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict) or set(payload) - {"refresh", "asOf"} or type(payload.get("refresh", False)) is not bool:
            raise ValueError("股票快照参数仅支持 refresh 与 asOf")
        as_of = validate_as_of(payload.get("asOf"))
        source = self.owner._get("path") or ""
        saved = self.owner.db.execute("SELECT data FROM market_snapshot_cache WHERE source=? AND as_of=? AND version=?",
                                      (source, as_of or "", SNAPSHOT_VERSION)).fetchone()
        saved = json.loads(saved[0]) if saved else None
        if saved and not self._is_current(saved):
            saved = None
        if saved and not payload.get("refresh"):
            _progress(self.progress, "读取已验证缓存", 1, 1, 100, 0)
            return self._with_failure({**saved, "cached": True}, as_of)
        try:
            universe = self.universe(as_of=as_of, records=TAIL_RECORDS, refresh=payload.get("refresh", False))
        except ValueError:
            if not saved or not self._is_current(saved):
                raise
            _progress(self.progress, "保留已验证缓存", 1, 1, 100, 0)
            return self._with_failure({**saved, "cached": True, "warnings": ["本次未能读取日线，保留此前股票快照；数据尚未更新", *saved["warnings"]]}, as_of)
        if not universe["inputs"] and saved:
            _progress(self.progress, "保留已验证缓存", 1, 1, 100, 0)
            return self._with_failure({**saved, "cached": True, "warnings": ["本次未能读取有效日线，保留此前股票快照；数据尚未更新", *saved["warnings"]]}, as_of)
        result = build_current_snapshot(self.iter_inputs(universe, TAIL_RECORDS), total=universe["total"], source=source,
                                        calendar=universe["dates"][-TAIL_RECORDS:], as_of=universe["asOf"],
                                        computed_at=universe["computedAt"], progress=self.progress,
                                        progress_total=len(universe["inputs"]))
        result.update(requestedAsOf=as_of, sourceRevision=universe["sourceRevision"], dataRevision=universe["dataRevision"], cached=universe["cached"])
        if universe.get("cacheWarning"):
            result["warnings"].insert(0, universe["cacheWarning"])
        if universe["failures"]:
            result["warnings"].insert(0, f"{len(universe['failures'])} 个日线文件未通过读取校验，已排除。" + "；".join(universe["failures"][:3]))
        if not source or not universe["inputs"]:
            result["warnings"].insert(0, "请先在设置中选择通达信目录并扫描日线；所选截至日可能尚无记录")
        elif result["coverage"]["stale"]:
            result["warnings"].insert(0, f"{result['coverage']['stale']} 只股票的最后日线早于统一排名日，已从当日榜单排除")
        with self.owner.db:
            self.owner.db.execute("BEGIN IMMEDIATE")
            self.assert_current(universe)
            self.owner.db.execute("INSERT OR REPLACE INTO market_snapshot_cache VALUES (?,?,?,?)",
                                  (source, as_of or "", SNAPSHOT_VERSION, json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))))
        _progress(self.progress, "股票快照已完成", 1, 1, 100, 0)
        return self._with_failure(result, as_of)
