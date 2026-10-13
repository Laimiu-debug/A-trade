"""Explainable, causal daily price/turnover observations, separate from trading regimes."""
from __future__ import annotations

VERSION = "market-style-profile-v1"
STREAK_CAP = 20
MIN_RELAY_SAMPLE = 10
TITLES = {"trend": "趋势阶段", "breadth": "上涨广度", "activity": "成交活跃", "relay": "连板接力",
          "preference": "活跃偏好", "risk": "风险压力"}
RULES = {
    "version": VERSION,
    "observation": "六维标签是截至所选日收盘的当日观察，不是连续确认的大类风格，不参与策略交易或仓位切换。阈值是可解释的观察分档，未经收益优化。",
    "trend": "20 日中位涨幅以 ±3% 区分偏强/偏弱，5 日以 ±1% 区分改善/走弱；偏弱时上涨称修复，偏强时下跌称回调。上行延续另需 MA20 广度至少 55%。",
    "breadth": "当日上涨占比至少 70% 且 MA20 广度至少 60% 为广泛走强；下跌占比至少 70% 且 MA20 广度至多 40% 为广泛走弱，平盘不算下跌。5 日广度变化以 ±5 个百分点分档，只比较两日都符合 60 日样本资格的同一批股票，共同样本至少占当日有效样本 70%。",
    "activity": "先逐股计算当日成交额 / 前 20 个市场交易日均额（不含当日），再取中位数；20 日须连续有成交额与成交量，有效样本至少覆盖当日样本 70%。≥1.2 倍为放量，≤0.8 倍为缩量；当日中位涨幅 ±0.3% 区分涨跌。不是市场总成交额同比，也不是资金流入。",
    "relay": "昨日连板（至少 2 板）今日可核验样本至少 10 只、核验覆盖至少 70%；晋级率 ≥50% 为接力较顺、<25% 为接力受阻。昨日涨停溢价另列其自己的样本数，不把小队列的 100% 当作全市场结论。",
    "preference": "趋势候选与高弹性股票均使用 Leadership 连续 60 日有成交样本及同一成交额分母。连续成交样本须覆盖当日有效股票 70% 且达到最低市场样本数的 70%，其中当日正成交额样本也须覆盖当日有效股票 70%。趋势成交占比 ≥45%、高弹性成交占比 ≥15% 且数量占比 ≥2% 为突出；两组可重叠，不能相加，不能推断机构/游资、大小盘或成长/价值。",
    "risk": "跌停代理 ≥max(20,2×涨停代理)，或下跌占比 ≥70% 且当日中位跌幅超过 1%，为压力较高；下跌占比 ≥60% 且中位跌幅超过 0.3%、5 日中位跌幅超过 3%，或至少 10 只可核验高标晋级率 <25%，为压力抬升。低压力不等于低交易风险。",
    "duration": "各维持续天数只数截至当日连续相同且有效的观察，最多显示 20 日；变化仅比较相邻两个有效交易日，不跨越缺样本日期。",
}


def evidence(label, value, unit, threshold=""):
    return {"label": label, "value": value, "unit": unit, "threshold": threshold}


def dimension(identifier, key, label, tone, reason, sample_count, values, *, observed=True):
    return {"id": identifier, "title": TITLES[identifier], "key": key, "label": label, "tone": tone,
            "status": "observed" if observed else "insufficient", "reason": reason,
            "sampleCount": sample_count, "evidence": values, "streakDays": 0, "durationCapped": False}


def unavailable(identifier, reason, sample_count=0, values=None):
    return dimension(identifier, "insufficient", "样本不足", "unknown", reason, sample_count, values or [], observed=False)


def profile(metrics, previous=None, *, min_market_symbols=1000, min_coverage=.7):
    """Use only supplied current evidence and a bounded previous-day duration state."""
    m = metrics
    count, expected = m.get("sampleCount", 0), m.get("expectedCount", 0)
    complete = expected >= min_market_symbols and m.get("coverage", 0) >= min_coverage and count > 0
    dimensions = []
    if not complete:
        reason = f"全市场观察需至少 {min_market_symbols} 只有 60 日历史的股票、当日有效覆盖至少 {min_coverage:.0%}；当前有效 {count} / 应观察 {expected} 只。"
        dimensions = [unavailable(identifier, reason, count, [evidence("有效股票", count, "count"),
                      evidence("当日覆盖", m.get("coverage"), "ratio", f"至少 {min_coverage:.0%}")]) for identifier in TITLES]
    else:
        long, short, daily = m.get("medianReturn20"), m.get("medianReturn5"), m.get("medianChangePercent")
        breadth, advance, decline = m.get("breadth"), m.get("advanceRatio"), m.get("declineRatio")
        values = [evidence("20 日中位涨幅", long, "percent", "偏强 ≥3%；偏弱 ≤-3%"),
                  evidence("5 日中位涨幅", short, "percent", "改善 >1%；走弱 <-1%"),
                  evidence("站上 MA20", breadth, "ratio", "上行延续需 ≥55%")]
        if any(value is None for value in (long, short, breadth)):
            dimensions.append(unavailable("trend", "需要完整的 5 日、20 日收益和 MA20 广度。", count, values))
        else:
            if long <= -3 and short > 1:
                state = ("weak_rebound", "弱势修复", "warning", "20 日整体仍偏弱，最近 5 日已经回升；尚不能把修复当作上升趋势。")
            elif long >= 3 and short < -1:
                state = ("trend_pullback", "趋势回调", "warning", "20 日整体仍偏强，但最近 5 日转弱，属于强势背景下的回调观察。")
            elif long >= 3 and short > 1 and breadth >= .55:
                state = ("uptrend", "上行延续", "positive", "20 日和 5 日中位涨幅均偏强，且超过一半股票站上 MA20。")
            elif long <= -3 and short < -1:
                state = ("downtrend", "下行延续", "negative", "20 日和 5 日中位涨幅均偏弱，短中期方向一致向下。")
            elif long >= 3 and -1 <= short <= 1:
                state = ("strong_consolidation", "偏强整理", "neutral", "20 日涨幅仍偏强，最近 5 日中位涨幅收敛在 ±1% 内。")
            elif long <= -3 and -1 <= short <= 1:
                state = ("weak_consolidation", "偏弱整理", "warning", "20 日跌幅仍偏弱，最近 5 日中位涨幅收敛在 ±1% 内。")
            elif short > 1:
                state = ("improving", "短线改善", "positive", "最近 5 日中位涨幅超过 1%，但尚未同时满足上行延续条件。")
            elif short < -1:
                state = ("weakening", "短线走弱", "warning", "最近 5 日中位跌幅超过 1%，但尚未同时满足下行延续条件。")
            else:
                state = ("sideways", "区间震荡", "neutral", "5 日中位涨幅在 ±1% 内，20 日也未达到偏强或偏弱分档。")
            dimensions.append(dimension("trend", *state, count, values))

        paired = m.get("breadthComparableCount", 0)
        delta = m.get("breadthChange5") if paired >= count * min_coverage else None
        values = [evidence("当日上涨占比", advance, "ratio", "广泛上涨 ≥70%，结合 MA20 广度"),
                  evidence("当日下跌占比", decline, "ratio", "广泛下跌 ≥70%，平盘不算下跌"),
                  evidence("站上 MA20", breadth, "ratio", "较广 ≥60%；偏窄 ≤40%"),
                  evidence("同样本 5 日广度变化", delta, "points", "扩张 ≥5；收缩 ≤-5 个百分点"),
                  evidence("5 日共同样本", paired, "count", "至少覆盖当日样本 70%")]
        if advance is None or decline is None or breadth is None:
            dimensions.append(unavailable("breadth", "当日上涨/下跌比例或 MA20 广度缺失。", count, values))
        else:
            if advance >= .7 and breadth >= .6:
                state = ("broad_strength", "广泛走强", "positive")
            elif decline >= .7 and breadth <= .4:
                state = ("broad_weakness", "广泛走弱", "negative")
            elif delta is not None and delta >= 5:
                state = ("expanding", "强势面扩张", "positive")
            elif delta is not None and delta <= -5:
                state = ("contracting", "强势面收缩", "warning")
            elif breadth >= .6:
                state = ("broad_base", "强势面较广", "neutral")
            elif breadth <= .4:
                state = ("narrow_base", "强势面偏窄", "warning")
            else:
                state = ("divided", "涨跌分化", "neutral")
            reason = "同时观察当日上涨、下跌与站上 MA20 的股票比例，平盘不算下跌；扩张/收缩使用同一批股票的 5 日变化。"
            if delta is None:
                reason += " 5 日共同样本不足，本日仅判断当前广度，不判断扩张或收缩。"
            dimensions.append(dimension("breadth", *state, reason, count, values))

        amount_count, amount_ratio = m.get("amountRatio20Count", 0), m.get("medianAmountRatio20")
        values = [evidence("成交额相对 20 日均额中位倍数", amount_ratio, "multiple", "放量 ≥1.2 倍；缩量 ≤0.8 倍"),
                  evidence("当日中位涨幅", daily, "percent", "上涨 >0.3%；下跌 <-0.3%"),
                  evidence("成交额有效样本", amount_count, "count", "至少覆盖当日样本 70%"),
                  evidence("成交额样本覆盖", amount_count / count, "ratio", "至少 70%")]
        if amount_ratio is None or daily is None or amount_count < count * min_coverage:
            dimensions.append(unavailable("activity", "前 20 日须连续有成交量和正成交额；有效倍数样本未覆盖当日股票的 70% 时不判量能。", amount_count, values))
        else:
            if amount_ratio >= 1.2:
                state = ("expanding_up", "放量上涨", "positive") if daily > .3 else ("expanding_down", "放量下跌", "negative") if daily < -.3 else ("expanding_flat", "放量震荡", "warning")
            elif amount_ratio <= .8:
                state = ("contracting_up", "缩量上涨", "warning") if daily > .3 else ("contracting_down", "缩量下跌", "warning") if daily < -.3 else ("contracting_flat", "缩量整理", "neutral")
            else:
                state = ("balanced", "量能平稳", "neutral")
            dimensions.append(dimension("activity", *state, "逐股除以前 20 日均成交额，再取中位数，避免新增/退出样本造成总额假变化；涨跌使用当日有效股票的中位涨幅。", amount_count, values))

        leader = m.get("leadership") or {}
        relay = (leader.get("promotion") or {}).get("all") or {}
        relay_count, relay_eligible, rate = relay.get("observed", 0), relay.get("eligible", 0), relay.get("rate")
        premium = leader.get("premium") or {}
        values = [evidence("昨日连板今日晋级率", rate, "ratio", "较顺 ≥50%；受阻 <25%"),
                  evidence("可核验昨日连板", relay_count, "count", f"至少 {MIN_RELAY_SAMPLE} 只"),
                  evidence("昨日连板应观察", relay_eligible, "count"),
                  evidence("连板核验覆盖", relay_count / relay_eligible if relay_eligible else None, "ratio", "至少 70%"),
                  evidence("昨日连板待核验", relay.get("missing", 0), "count"),
                  evidence("昨日涨停溢价中位数", premium.get("median"), "percent", "独立的昨日涨停队列，不参与小样本外推"),
                  evidence("溢价观察样本", premium.get("observed", 0), "count")]
        if relay_count < MIN_RELAY_SAMPLE or not relay_eligible or relay_count / relay_eligible < min_coverage or rate is None:
            dimensions.append(unavailable("relay", f"昨日连板今日可核验 {relay_count} / {relay_eligible} 只；需至少 {MIN_RELAY_SAMPLE} 只且覆盖 70%，小队列晋级率仅列为证据。", relay_count, values))
        else:
            state = ("smooth", "接力较顺", "positive") if rate >= .5 else ("blocked", "接力受阻", "negative") if rate < .25 else ("mixed", "晋级分化", "neutral")
            dimensions.append(dimension("relay", *state, "跟踪昨日至少 2 板的固定队列在今日是否继续涨停；缺日、无成交和超名义价格区间从晋级率分母剔除。", relay_count, values))

        trend = leader.get("trend") or {}
        elastic = leader.get("highElasticity") or {}
        preference_count = trend.get("eligible", 0)
        preference_amount_count = m.get("preferenceAmountCount", 0)
        trend_share, elastic_share = trend.get("amountShare"), elastic.get("amountShare")
        elastic_ratio = elastic.get("count", 0) / preference_count if preference_count else None
        values = [evidence("趋势候选成交占比", trend_share, "ratio", "突出 ≥45%"),
                  evidence("高弹性股票成交占比", elastic_share, "ratio", "突出 ≥15% 且数量占比 ≥2%"),
                  evidence("高弹性数量占比", elastic_ratio, "ratio", "至少 2%"),
                  evidence("连续 60 日成交样本", preference_count, "count"),
                  evidence("连续成交样本覆盖", preference_count / count, "ratio", "至少覆盖当日有效股票的 70%"),
                  evidence("有效成交额样本", preference_amount_count, "count", "连续 60 日有成交量且当日成交额为正"),
                  evidence("偏好样本覆盖", preference_amount_count / count, "ratio", "有效成交额样本至少覆盖当日有效股票的 70%")]
        if preference_count < max(min_market_symbols, count) * min_coverage or preference_amount_count < count * min_coverage or trend_share is None or elastic_share is None:
            dimensions.append(unavailable("preference", "需要足够的连续 60 日有成交股票及同一成交额分母，其中当日正成交额样本须覆盖当日有效股票至少 70%；缺少时不能推断偏好。", preference_amount_count, values))
        else:
            strong_trend, strong_elastic = trend_share >= .45, elastic_share >= .15 and elastic_ratio >= .02
            state = ("parallel", "趋势与高弹性并行", "positive") if strong_trend and strong_elastic else ("trend", "趋势偏活跃", "positive") if strong_trend else ("elastic", "高弹性偏活跃", "warning") if strong_elastic else ("unclear", "两类特征均不突出", "neutral")
            dimensions.append(dimension("preference", *state, "比较同口径成交占比，两组股票可能重叠，不能相加；这是量价特征，不代表投资者身份、行业或成长价值。", preference_amount_count, values))

        decline = m.get("declineRatio")
        down, up = m.get("limitDownApprox", 0), m.get("limitUpApprox", 0)
        high = leader.get("highBoardPromotion") or {}
        high_observed, high_eligible = high.get("observed", 0), high.get("eligible", 0)
        high_valid = high_observed >= MIN_RELAY_SAMPLE and high_eligible > 0 and high_observed / high_eligible >= min_coverage and high.get("rate") is not None
        values = [evidence("当日下跌占比", decline, "ratio", "压力抬升 ≥60%；较高 ≥70%，结合跌幅"),
                  evidence("当日中位涨幅", daily, "percent", "结合 -0.3% / -1%"),
                  evidence("5 日中位涨幅", short, "percent", "压力抬升 <-3%"),
                  evidence("近似跌停数量", down, "count", f"较高阈值 {max(20, up * 2)} 只"),
                  evidence("高标可核验样本", high_observed, "count", "至少 10 只且覆盖 70% 才参与判断"),
                  evidence("高标应观察样本", high_eligible, "count"),
                  evidence("高标核验覆盖", high_observed / high_eligible if high_eligible else None, "ratio", "至少 70%"),
                  evidence("高标晋级率", high.get("rate") if high_valid else None, "ratio", "压力抬升 <25%")]
        if daily is None or decline is None or short is None:
            dimensions.append(unavailable("risk", "当日下跌比例、当日或 5 日中位收益缺失。", count, values))
        else:
            if down >= max(20, up * 2) or decline >= .7 and daily < -1:
                state = ("high", "压力较高", "negative")
            elif decline >= .6 and daily < -.3 or short < -3 or high_valid and high["rate"] < .25:
                state = ("rising", "压力抬升", "warning")
            elif down == 0 and decline <= .4 and daily >= 0:
                state = ("low", "压力较低", "positive")
            else:
                state = ("ordinary", "压力一般", "neutral")
            dimensions.append(dimension("risk", *state, "结合下跌面、跌幅、近似跌停和样本充足的高标接力；低压力只是当日日线观察，不保证次日风险较低。", count, values))

    previous_dimensions = {row["id"]: row for row in (previous or {}).get("dimensions", [])}
    changes = []
    for item in dimensions:
        old = previous_dimensions.get(item["id"])
        if item["status"] == "observed":
            same = old and old.get("status") == "observed" and old.get("key") == item["key"]
            item["streakDays"] = min(STREAK_CAP, old.get("streakDays", 0) + 1) if same else 1
            item["durationCapped"] = item["streakDays"] == STREAK_CAP
            if old and old.get("status") == "observed" and not same:
                changes.append({"id": item["id"], "title": item["title"], "from": old["label"], "to": item["label"]})
    observed = {item["id"]: item for item in dimensions if item["status"] == "observed"}
    headline = " · ".join(observed[key]["label"] for key in ("trend", "activity", "risk") if key in observed) if complete else "数据不足，暂不判别市场风格"
    return {"version": VERSION, "status": "observed" if complete else "insufficient", "headline": headline,
            "summary": "当日六维量价观察可以同时成立；与下方连续确认的大类标签分别阅读，不直接生成交易信号。" if complete else "有效覆盖未达到全市场观察门槛，保留原始证据，不用局部样本替代市场。",
            "dimensions": dimensions, "changes": changes}
