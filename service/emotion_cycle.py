"""Close-of-day, point-in-time emotion-cycle observations.

The four-stage vocabulary follows public discussions attributed to 92科比.
All numerical thresholds below are A-trade research rules, not his formula.
Theme membership is an explicit dated user observation, never backfilled.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from statistics import median

from service.leader_research import Leadership
from service.market_research import board_of, validate_as_of, _now


VERSION = "emotion-cycle-v1"
SOURCES = [
    {"title": "2021-04-02 直播回放：周期与时机", "url": "https://www.bilibili.com/video/BV16N4y127C3/", "kind": "直播回放转载"},
    {"title": "四阶段讲解（原帖文字转载）", "url": "https://m.tgb.cn/a/274STO5sg7W?type=hot", "kind": "第三方转载"},
    {"title": "2021-04-07 直播整理：龙头、补涨、切换", "url": "https://xueqiu.com/7735440616/176435731", "kind": "第三方整理"},
]
STAGES = {"trial": "低位试错", "advance": "主升", "chop": "高位震荡", "decline": "主跌退潮", "unknown": "待确认"}
RULES = [
    "至少有 10 个此前交易日；当日有效股票不少于 20 只、覆盖已出现样本的 70%。不足时不判断阶段。",
    "主跌退潮：昨涨停溢价≤-2%且高标失败率≥50%，或跌停≥涨停且下跌面≥65%；风险条件即时显示。",
    "主升：至少3只可比连板队列，晋级率≥45%、昨涨停溢价>0、最高板≥3，涨停数不低于此前10日中位数。",
    "高位震荡：此前10日高度达到4板，且高标失败率≥35%、昨涨停溢价≤0或涨停数收缩至中位数75%以下。",
    "低位试错：最高板≤3且涨停溢价≥0、风险未触发。其余显示待确认；常规阶段连续两日确认，旧阶段最多沿用5日。",
    "高标失败率、晋级率和涨停溢价均来自昨日固定队列；覆盖不足70%或分母不足3只时不参与阶段判断。",
]


def usable(bucket, key="rate", minimum=3):
    if not bucket or bucket.get("observed", 0) < minimum or not bucket.get("eligible"):
        return None
    if bucket["observed"] / bucket["eligible"] < .7:
        return None
    return bucket.get(key)


def cycle_timeline(timeline, totals):
    """Only consume current/past observations; confirmation expires after 5 days."""
    output, confirmed, age, pending, streak = [], "unknown", 0, None, 0
    for i, row in enumerate(timeline):
        metrics = row["metrics"]
        observed = metrics["observedCount"]
        eligible = totals[i]
        coverage = observed / eligible if eligible else 0
        premium = usable(metrics.get("premium"), "mean")
        promotion = usable(metrics.get("promotion", {}).get("all"))
        high = metrics.get("highBoardPromotion", {})
        high_rate = usable(high)
        failed = 1 - high_rate if high_rate is not None else None
        up, down = metrics["limitUpCount"], metrics["risk"]["limitDownCount"]
        decline_ratio = metrics["risk"].get("declineRatio")
        history = timeline[max(0, i - 10):i]
        typical = median(x["limitUpCount"] for x in history) if history else None
        peak = max((x["maxHeight"] for x in history), default=0)
        evidence = {"limitUp": up, "limitDown": down, "maxHeight": row["maxHeight"],
                    "premium": premium, "promotion": promotion, "highFailure": failed,
                    "declineRatio": decline_ratio, "priorMedianLimitUp": typical,
                    "observed": observed, "eligible": eligible, "coverage": round(coverage, 6)}
        insufficient = i < 10 or observed < 20 or coverage < .7
        raw, reason = "unknown", "接力与赚钱效应尚未形成一致证据"
        if insufficient:
            reason = "此前交易日不足10日" if i < 10 else "有效股票少于20只或覆盖不足70%"
        elif (premium is not None and premium <= -2 and failed is not None and failed >= .5) or (down > 0 and down >= max(1, up) and decline_ratio is not None and decline_ratio >= .65):
            raw, reason = "decline", "昨日队列负反馈或跌停与下跌面同步扩散"
        elif promotion is not None and promotion >= .45 and premium is not None and premium > 0 and row["maxHeight"] >= 3 and up >= max(1, typical):
            raw, reason = "advance", "连板晋级、昨日溢价与高度共同走强"
        elif peak >= 4 and ((failed is not None and failed >= .35) or (premium is not None and premium <= 0) or up < typical * .75):
            raw, reason = "chop", "前期高标打开空间后出现分歧或赚钱效应收缩"
        elif row["maxHeight"] <= 3 and premium is not None and premium >= 0:
            raw, reason = "trial", "高度较低，昨日涨停反馈转为非负，低位试错观察"
        if raw == pending:
            streak += 1
        else:
            pending, streak = raw, 1
        age += 1
        if insufficient or raw == "decline" or raw != "unknown" and streak >= 2:
            confirmed, age = raw, 0
        elif age > 5:
            confirmed = "unknown"
        output.append({"date": row["date"], "stage": confirmed, "label": STAGES[confirmed],
                       "observation": raw, "observationLabel": STAGES[raw], "confirmDays": streak,
                       "pending": raw != confirmed, "reason": reason if raw == confirmed else f"{'阶段待确认' if confirmed == 'unknown' else '已确认' + STAGES[confirmed]}；当日观察为{STAGES[raw]}，尚未连续确认",
                       "observationReason": reason, "evidence": evidence,
                       "metrics": metrics, "scope": "market" if observed >= 1000 and coverage >= .7 else "sample"})
    return output


def build_emotion(research, universe, params, themes):
    dates = universe["dates"]
    indexes = {day: i for i, day in enumerate(dates)}
    leadership, totals = Leadership(dates), [0] * len(dates)
    active_themes = [t for t in themes if t["effectiveFrom"] <= (universe["asOf"] or "")]
    theme_accumulators = {t["id"]: Leadership(dates) for t in active_themes}
    membership = {}
    for theme in active_themes:
        for symbol in theme["symbols"]:
            membership.setdefault(symbol, []).append(theme)
    items, started = [], 0
    progress = getattr(getattr(research, "owner", None), "progress", None)
    for number, security in enumerate(research.iter_inputs(universe), 1):
        if progress and number % 100 == 0:
            progress("计算情绪周期", number, len(universe["inputs"]))
        if security["kind"] != "stock" or params["excludeST"] and ("ST" in security["name"].upper() or "退" in security["name"]):
            continue
        aligned = [None] * len(dates)
        for row in security["rows"]:
            if row[0] in indexes:
                aligned[indexes[row[0]]] = row
        first = next((i for i, row in enumerate(aligned) if row), None)
        if first is None:
            continue
        started += 1
        for i in range(first, len(dates)):
            totals[i] += 1
        board = board_of(security["symbol"], "stock")
        leadership.add(security, aligned, board)
        for theme in membership.get(security["symbol"], []):
            bounded = [row if day >= theme["effectiveFrom"] else None for day, row in zip(dates, aligned)]
            theme_accumulators[theme["id"]].add(security, bounded, board)
        if aligned and aligned[-1] and aligned[-1][2] > 0:
            current, previous = aligned[-1], aligned[-2] if len(aligned) > 1 else None
            def ret(days):
                prior = aligned[-1 - days] if len(aligned) > days else None
                return round((current[1] / prior[1] - 1) * 100, 6) if prior and prior[2] > 0 else None
            items.append({key: security[key] for key in ("symbol", "code", "name", "market", "kind")} |
                         {"board": board, "date": current[0], "close": current[1], "amount": current[3],
                          "changePercent": round((current[1] / previous[1] - 1) * 100, 6) if previous and previous[2] > 0 else None,
                          "return5": ret(5), "return20": ret(20), "boardHeight": 0})
    result = leadership.finish()
    current = {x["symbol"]: x for x in result["current"]}
    for item in items:
        item.update(current.get(item["symbol"], {}))
    items.sort(key=lambda x: (-x["boardHeight"], -(x["return5"] if x["return5"] is not None else -10000), -x["amount"], x["symbol"]))
    high = max((x["boardHeight"] for x in items), default=0)
    leaders = [dict(x, role="空间龙头候选", roleReason=f"当前最高可观察高度 {high} 板；并列保留，非人气认定") for x in items if high >= 2 and x["boardHeight"] == high]
    leader_symbols = {x["symbol"] for x in leaders}
    low = [dict(x, role="低位候选", roleReason="首板或两板；未确认同题材关系，不能自动认定补涨") for x in items if 1 <= x["boardHeight"] <= 2 and x["symbol"] not in leader_symbols][:60]
    by_symbol = {x["symbol"]: x for x in items}
    theme_rows = []
    as_of = universe["asOf"] or ""
    for theme in active_themes:
        members = [by_symbol[s] for s in theme["symbols"] if s in by_symbol]
        ranked = sorted(members, key=lambda x: (-x["boardHeight"], -(x["return5"] if x["return5"] is not None else -10000), -x["amount"], x["symbol"]))
        max_height = max((x["boardHeight"] for x in ranked), default=0)
        cores = [x for x in ranked if max_height >= 2 and x["boardHeight"] == max_height]
        core_symbols = {x["symbol"] for x in cores}
        followers = [dict(x, role="同题材跟随候选", roleReason="同一已确认题材内，今日上涨且非最高板；不推断盘中带动因果") for x in ranked if x["symbol"] not in core_symbols and (x["changePercent"] or 0) > 0]
        catchups = [dict(x, role="低位补涨候选", roleReason="同题材高标至少3板，本股首板/两板且5日涨幅低于高标；需复核启动时机") for x in followers if max_height >= 3 and 1 <= x["boardHeight"] <= 2 and x["return5"] is not None and all(c["return5"] is not None and x["return5"] < c["return5"] for c in cores)]
        relay = theme_accumulators[theme["id"]].finish()["timeline"]
        theme_rows.append({**theme, "observed": len(members), "missing": len(theme["symbols"]) - len(members),
                           "limitUpCount": sum(x["boardHeight"] > 0 for x in members), "maxHeight": max_height,
                           "leaders": cores, "followers": followers, "catchups": catchups,
                           "relay": relay[-1]["metrics"] if relay else {},
                           "previousRelay": relay[-2]["metrics"] if len(relay) > 1 else {},
                           "meanChange": round(sum(x["changePercent"] for x in members if x["changePercent"] is not None) / sum(x["changePercent"] is not None for x in members), 6) if any(x["changePercent"] is not None for x in members) else None})
    timeline = cycle_timeline(result["timeline"], [n + universe.get("failedStocks", 0) for n in totals])[-params["historyDays"]:]
    selected = next((x for x in theme_rows if x["id"] == params["themeId"]), None)
    rotation_candidates = []
    for old in theme_rows:
        bucket = old["relay"].get("highBoardPromotion", {})
        if old["previousRelay"].get("maxHeight", 0) < 3 or not bucket.get("observed") or bucket["observed"] / max(1, bucket["eligible"]) < .7 or bucket["broken"] / bucket["observed"] < .5:
            continue
        for new in theme_rows:
            if new["id"] == old["id"] or set(old["symbols"]) & set(new["symbols"]):
                continue
            if new["limitUpCount"] >= 2 and 1 <= new["maxHeight"] <= 3 and new["previousRelay"].get("maxHeight", 0) <= 2:
                rotation_candidates.append({"oldTheme": old["name"], "newTheme": new["name"],
                    "oldBroken": bucket["broken"], "oldObserved": bucket["observed"], "newLimitUps": new["limitUpCount"],
                    "label": "切换待验证", "reason": "旧题材昨日高标过半断板，不重叠题材出现至少两只低位涨停；仍需后续连续表现确认"})
    return {"timeline": timeline, "current": timeline[-1] if timeline else None,
            "leaders": leaders, "lowCandidates": low, "yesterdayLeaders": result["yesterdayLeaders"],
            "themes": theme_rows, "selectedTheme": selected,
            "rotationCandidates": rotation_candidates,
            "items": selected["followers"] if selected else leaders, "total": len(selected["followers"] if selected else leaders),
            "coverage": {"valid": len(items), "total": started, "excluded": universe.get("failedStocks", 0)},
            "rules": RULES, "references": SOURCES}


def themes_dispatch(owner, method, payload):
    owner.db.execute("CREATE TABLE IF NOT EXISTS emotion_themes (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    owner.db.commit()
    allowed = {"market.emotion.themes.list", "market.emotion.themes.save", "market.emotion.themes.delete"}
    if method not in allowed or not isinstance(payload, dict):
        raise ValueError("不支持的题材操作")
    if method.endswith("list"):
        if payload:
            raise ValueError("题材列表不接受参数")
        return {"items": [json.loads(row[0]) for row in owner.db.execute("SELECT data FROM emotion_themes ORDER BY id")]}
    if method.endswith("delete"):
        if set(payload) != {"id"} or not isinstance(payload["id"], str):
            raise ValueError("请选择题材记录")
        with owner.db:
            cursor = owner.db.execute("DELETE FROM emotion_themes WHERE id=?", (payload["id"],))
        return {"deleted": cursor.rowcount > 0}
    if set(payload) - {"id", "name", "symbols", "effectiveFrom", "source"}:
        raise ValueError("存在不支持的题材参数")
    name, symbols = payload.get("name"), payload.get("symbols")
    effective = validate_as_of(payload.get("effectiveFrom"))
    if not effective or not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
        raise ValueError("请填写40字以内题材名称和归属生效日期")
    if not isinstance(symbols, list) or not 2 <= len(symbols) <= 500 or any(not isinstance(s, str) for s in symbols):
        raise ValueError("每个题材须包含2至500只证券")
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) < 2:
        raise ValueError("每个题材至少两只不同证券")
    for symbol in symbols:
        if not owner.db.execute("SELECT 1 FROM securities WHERE symbol=? AND kind='stock'", (symbol,)).fetchone():
            raise ValueError(f"证券 {symbol[:20]} 不在当前本地股票目录")
    identifier = payload.get("id") or "theme_" + uuid.uuid4().hex[:16]
    if not isinstance(identifier, str) or len(identifier) > 80:
        raise ValueError("题材记录标识无效")
    if owner.db.execute("SELECT 1 FROM emotion_themes WHERE id=?", (identifier,)).fetchone():
        raise ValueError("题材快照已存在；修改归属请保存为新快照，保留旧日期的观察")
    if owner.db.execute("SELECT COUNT(*) FROM emotion_themes").fetchone()[0] >= 100:
        raise ValueError("最多保存100份题材快照")
    source = payload.get("source", "manual")
    if source not in ("manual", "tdx_snapshot"):
        raise ValueError("题材来源无效")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    if source == "tdx_snapshot" and effective < today:
        raise ValueError("当前板块成分不能回填到历史日期，请使用今天作为生效日期")
    item = {"id": identifier, "name": name.strip(), "symbols": symbols, "effectiveFrom": effective,
            "source": source, "createdAt": _now(), "historicalAnnotation": source == "manual" and effective < today}
    with owner.db:
        owner.db.execute("INSERT INTO emotion_themes VALUES (?,?)", (identifier, json.dumps(item, ensure_ascii=False)))
    return item
