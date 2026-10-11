"""Small, auditable research hypotheses. All indicators end at the signal close.

These rules are candidate specifications, not claims of profitable strategies.
Configuration percentages are fractions; reported performance percentages are
percentage points. Multiple selected hypotheses must ALL accept a security.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
from datetime import date
import math
from statistics import mean

STRATEGY_VERSION = "causal-candidates-force-v2"
FORCE_VERSION = "finaltrade-main-retail-v1"
FORCE_STRATEGIES = frozenset({"forceGoldenCross", "forceTrend"})
FORCE_WARMUP = 30  # preceding observations; fixed, never an optimization axis


def _parameter(key, label, default, low, high, step=1, kind="integer"):
    return dict(key=key, label=label, default=default, min=low, max=high, step=step, type=kind)


_CATALOGUE = [
    {"id": "trendBreakout", "name": "趋势突破与量能确认", "description": "收盘突破此前区间高点，趋势均线向上，成交量高于此前均量。",
     "rationale": "检验趋势延续与交易活跃度确认假设；突破区间不包含信号当天。",
     "caveats": "可能遭遇假突破；量能确认不是机构资金或未来收益的证据。",
     "parameters": [_parameter("lookback", "突破观察期", 40, 2, 252), _parameter("volumeRatio", "放量倍数", 1.2, 0.1, 5, .1, "number"), _parameter("trendWindow", "趋势均线期", 80, 2, 500)]},
    {"id": "momentum", "name": "绝对趋势与相对动量", "description": "过去区间收益达标且位于上行均线上；候选按历史动量排序进入有限仓位。",
     "rationale": "结合时间序列趋势过滤和所选股票池内的相对动量排序。",
     "caveats": "动量反转和股票池选择偏差可能很大；不代表已验证的A股超额收益。",
     "parameters": [_parameter("lookback", "动量观察期", 60, 2, 252), _parameter("trendWindow", "趋势均线期", 120, 2, 500), _parameter("minReturn", "最低区间收益比例", .05, 0, 1, .01, "number")]},
    {"id": "trendPullback", "name": "上升趋势中的回调修复", "description": "保持上行趋势，距离近期收盘峰值适度回撤，且当日收盘开始回升。",
     "rationale": "检验趋势中的短期回撤修复；仅使用收盘数据确认修复。",
     "caveats": "下跌初期容易误判为回调；与突破策略组合时交集可能为空。",
     "parameters": [_parameter("trendWindow", "趋势均线期", 80, 3, 500), _parameter("pullbackWindow", "回调观察期", 10, 2, 120), _parameter("minDrawdown", "最低回调比例", .03, 0, .5, .01, "number"), _parameter("maxDrawdown", "最高回调比例", .12, .001, .8, .01, "number")]},
    {"id": "forceGoldenCross", "name": "主散量能金叉", "description": "主散公式的主力线上穿散户线；默认不叠加价格均线或涨停条件。",
     "rationale": "沿用 FINAL-TRADE：主力=EMA(MA(上涨日量,3),3)，散户=EMA(MA(下跌日量,3),10)；前根主力不高于散户，本根主力高于散户。",
     "caveats": "主散线仅由收盘涨跌和成交量估算，不是真实主力/散户资金流。固定30根前置预热；金叉发生不等于随后上涨。",
     "source": "FINAL-TRADE/backend/app/core/ths_volume_signal.py; preview/market-indicators.js",
     "parameters": [_parameter("minDominance", "最低主导强度比例", 0, 0, .9, .05, "number")]},
    {"id": "forceTrend", "name": "主散量能持续主导", "description": "主力线连续数根强于散户线且达到主导强度，本根主力线回升；紫转黄只作为观察证据。",
     "rationale": "使用 FINAL-TRADE 同一主散公式检验持续量能主导假设。主导强度=(主力-散户)/(主力+散户)，计算周期固定为3/3与3/10。",
     "caveats": "这是同源指标上的简化候选，并非原主散节奏波的完整周期、波谷与分位策略；不代表真实资金身份或已验证盈利。固定30根前置预热。",
     "source": "FINAL-TRADE/backend/app/core/ths_volume_signal.py; force_rhythm_strategy.py (formula only)",
     "parameters": [_parameter("confirmDays", "连续主导根数", 3, 1, 10), _parameter("minDominance", "最低主导强度比例", .1, 0, .9, .05, "number")]},
]

_NUMBERS = {
    "initialCapital": (100000, 1000, 1e10, False), "maxPositions": (5, 1, 100, True),
    "positionFraction": (.2, .001, 1, False), "commissionRate": (.0003, 0, .02, False),
    "minCommission": (5, 0, 1000, False), "transferFeeRate": (.00001, 0, .01, False),
    "slippageBps": (10, 0, 500, False), "stopLossPct": (.08, 0, .9, False),
    "takeProfitPct": (.2, 0, 5, False), "partialTakeProfitPct": (.12, 0, 5, False),
    "partialSellFraction": (.5, .01, .99, False), "trailingStopPct": (.1, 0, .9, False),
    "maxHoldingDays": (60, 1, 2500, True), "maxParticipationRate": (.01, .0001, .1, False),
    "costMultiplier": (1, 1, 5, False),
}


def catalogue():
    return deepcopy(_CATALOGUE)


def strategy_conflicts(config):
    """Only report provable necessary-condition conflicts, not name heuristics."""
    conflicts = []
    if {"trendBreakout", "trendPullback"}.issubset(config["strategyIds"]):
        breakout = config["strategyParams"]["trendBreakout"]
        pullback = config["strategyParams"]["trendPullback"]
        if breakout["lookback"] >= pullback["pullbackWindow"] and pullback["minDrawdown"] > 0:
            conflicts.append("策略条件互斥：突破观察期覆盖回调窗口时，收盘突破此前高点意味着回撤为零，无法同时满足正的最低回调比例；保留全部交集条件，不会自动删除策略。")
    if FORCE_STRATEGIES.issubset(config["strategyIds"]) and config["strategyParams"]["forceTrend"]["confirmDays"] >= 2:
        conflicts.append("策略条件互斥：当日主散金叉要求前根主力不高于散户，连续两根及以上主导则要求前根主力高于散户；同日AND交集为空。保留全部条件，不自动把金叉改为近期金叉或删除策略。")
    return conflicts


def _number(value, key, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} 必须是有限数值")
    # Compare before float conversion: arbitrarily large JSON integers must
    # produce an ordinary validation error rather than OverflowError.
    if not low <= value <= high or not math.isfinite(value) or integer and int(value) != value:
        raise ValueError(f"{key} 超出允许范围 {low}..{high}" + ("，且必须为整数" if integer else ""))
    return int(value) if integer else float(value)


def normalize_config(config=None):
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("回测参数须为对象")
    unknown = set(config) - set(_NUMBERS) - {"strategyIds", "strategyParams", "startDate", "endDate", "stampDutyRate"}
    if unknown:
        raise ValueError("未知回测参数: " + ", ".join(sorted(unknown)))
    result = {key: _number(config.get(key, default), key, low, high, integer)
              for key, (default, low, high, integer) in _NUMBERS.items()}
    ids = config.get("strategyIds", ["trendBreakout"])
    known = {item["id"] for item in _CATALOGUE}
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or item not in known for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("至少选择一个已支持的策略，且不可重复")
    result["strategyIds"] = list(ids)
    params = config.get("strategyParams", {})
    if not isinstance(params, dict) or set(params) - known:
        raise ValueError("策略参数包含未知策略")
    result["strategyParams"] = {}
    for item in _CATALOGUE:
        values = params.get(item["id"], {})
        if not isinstance(values, dict) or set(values) - {p["key"] for p in item["parameters"]}:
            raise ValueError(f"{item['id']} 含有未知参数")
        result["strategyParams"][item["id"]] = {
            p["key"]: _number(values.get(p["key"], p["default"]), f"{item['id']}.{p['key']}", p["min"], p["max"], p["type"] == "integer")
            for p in item["parameters"]}
    pullback = result["strategyParams"]["trendPullback"]
    if pullback["minDrawdown"] >= pullback["maxDrawdown"]:
        raise ValueError("最低回调比例必须小于最高回调比例")
    if result["partialTakeProfitPct"] and result["takeProfitPct"] and result["partialTakeProfitPct"] >= result["takeProfitPct"]:
        raise ValueError("分批止盈阈值必须低于全部止盈阈值")
    for key in ("startDate", "endDate"):
        value = config.get(key)
        if value in (None, ""):
            result[key] = None
        else:
            try:
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError(f"{key} 必须是 YYYY-MM-DD 日期") from None
            result[key] = value
    if result["startDate"] and result["endDate"] and result["startDate"] > result["endDate"]:
        raise ValueError("回测开始日期不得晚于结束日期")
    tax = config.get("stampDutyRate")
    result["stampDutyRate"] = None if tax is None else _number(tax, "stampDutyRate", 0, .02)
    return result


def required_history(config):
    """Number of preceding observations required by all selected hypotheses."""
    windows = []
    for strategy_id in config["strategyIds"]:
        if strategy_id in FORCE_STRATEGIES:
            windows.append(FORCE_WARMUP)
        else:
            p = config["strategyParams"][strategy_id]
            windows.append(max(p["trendWindow"], p.get("lookback", p.get("pullbackWindow", 2))))
    return max(windows, default=0)


def _force_state(current, previous):
    return "rising" if current > previous else "falling" if current < previous else "flat"


def _positive_ratio(current, previous, zero_cap):
    if current <= 0:
        return 0.
    return current / previous if previous > 0 else float(zero_cap)


def _ratio_score(ratio, ceiling):
    if not math.isfinite(ratio) or ratio <= 1:
        return 0.
    return max(0., min(100., math.log1p(min(ratio, ceiling) - 1) / math.log1p(ceiling - 1) * 100))


def prepare_bars(bars, preclose_by_date=None):
    """Precompute immutable-by-convention causal features in O(N) time.

    Source: FINAL-TRADE ``ths_volume_signal.py``. Shares are converted to lots
    exactly once; the first directional volumes are zero. Rolling means use
    their available initial length and EMA starts at its first input value.
    Output masks the first two numeric rows and early events like this app's
    chart observations. Trading additionally requires 30 preceding bars.

    Call once per frozen security snapshot, outside the daily simulation loop.
    This returns separate feature rows and never mutates source OHLCV rows.
    """
    if not isinstance(bars, list):
        raise ValueError("主散预计算须传入日线列表")
    if preclose_by_date is None:
        preclose_by_date = {}
    if not isinstance(preclose_by_date, dict):
        raise ValueError("主散参考昨收须为日期到价格的映射")
    up_queue, down_queue = deque(), deque()
    up_sum, down_sum, main, retail = 0., 0., 0., 0.
    main_values, retail_values, features = [], [], []
    previous_close, previous_date = None, ""
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ValueError("主散预计算日线格式错误")
        close = _number(bar.get("close"), "close", .000001, 1e8)
        volume = _number(bar.get("volume"), "volume", 0, 1e15) / 100
        trading_date = bar.get("date")
        try:
            if not isinstance(trading_date, str) or date.fromisoformat(trading_date).isoformat() != trading_date or trading_date <= previous_date:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("主散预计算日期必须有效且严格递增") from None
        reference = preclose_by_date.get(trading_date)
        if reference is not None:
            reference = _number(reference, "preclose", .000001, 1e8)
        direction_reference = reference if reference is not None else previous_close
        up = volume if index and direction_reference is not None and close > direction_reference else 0.
        down = volume if index and direction_reference is not None and close < direction_reference else 0.
        up_queue.append(up)
        down_queue.append(down)
        up_sum += up
        down_sum += down
        if len(up_queue) > 3:
            up_sum -= up_queue.popleft()
            down_sum -= down_queue.popleft()
        up_mean, down_mean = up_sum / len(up_queue), down_sum / len(down_queue)
        main = up_mean if index == 0 else .5 * up_mean + .5 * main
        retail = down_mean if index == 0 else (2 / 11) * down_mean + (1 - 2 / 11) * retail
        prev_main = main_values[-1] if main_values else main
        prev_retail = retail_values[-1] if retail_values else retail
        prev2_main = main_values[-2] if len(main_values) >= 2 else prev_main
        main_values.append(main)
        retail_values.append(retail)
        current_state, previous_state = _force_state(main, prev_main), _force_state(prev_main, prev2_main)
        raw_turn = index >= 2 and prev_main > 0 and previous_state == "falling" and main > 0 and current_state == "rising"
        raw_cross = index >= 1 and main > retail and prev_main <= prev_retail
        dominance = (main - retail) / (main + retail) if main + retail > 0 else None
        growth = _positive_ratio(main, prev_main, 12)
        prev_growth = _positive_ratio(prev_main, prev2_main, 12)
        explosion = _positive_ratio(main, max((prev_main + prev2_main) / 2, 0), 15)
        acceleration = _positive_ratio(growth, max(prev_growth, 1), 6)
        versus = _positive_ratio(main, retail, 8)
        power_score = min(100., max(0., .45 * _ratio_score(growth, 12) + .40 * _ratio_score(explosion, 15) + .15 * _ratio_score(acceleration, 6)))
        gap_score = min(100., max(0., .55 * max(0., dominance or 0.) * 100 + .45 * _ratio_score(versus, 8)))
        state_bonus = (6 if current_state == "rising" else 0) + (4 if previous_state == "falling" and current_state == "rising" else 0)
        signal_score = min(100., max(0., 12 + (12 if raw_turn else 0) + (18 if raw_cross else 0) + power_score * .48 + gap_score * .32 + state_bonus))
        features.append({
            "date": trading_date, "available": index >= 2,
            "directionReference": "exAdjustedPreclose" if reference is not None else "previousRawClose",
            "mainForce": main if index >= 2 else None, "retailForce": retail if index >= 2 else None,
            "prevMainForce": prev_main if index >= 3 else None,
            "prevRetailForce": prev_retail if index >= 3 else None,
            "prev2MainForce": prev2_main if index >= 4 else None,
            "mainForceState": current_state if index >= 3 else None,
            "prevMainForceState": previous_state if index >= 4 else None,
            "forceGap": main - retail if index >= 2 else None,
            "forceDominance": dominance if index >= 2 else None,
            "goldenCross": bool(index >= 3 and raw_cross),
            "purpleToYellow": bool(index >= 4 and raw_turn),
            "mainForcePowerScore": round(power_score, 2) if index >= 2 else None,
            "forceGapScore": round(gap_score, 2) if index >= 2 else None,
            "retailPressureScore": round(100 - gap_score, 2) if index >= 2 else None,
            "mainForceExplosionRatio": round(explosion, 4) if index >= 2 else None,
            "mainVsRetailRatio": round(versus, 4) if index >= 2 else None,
            "signalScore": round(signal_score, 2) if index >= 2 else None,
        })
        previous_close, previous_date = close, trading_date
    return {"version": FORCE_VERSION, "length": len(bars), "force": features}


def _evaluate_force(bars, index, strategy_id, parameters, prepared):
    if not isinstance(prepared, dict) or prepared.get("version") != FORCE_VERSION or prepared.get("length") != len(bars) or not isinstance(prepared.get("force"), list) or len(prepared["force"]) != len(bars):
        raise ValueError("主散策略需先调用 prepare_bars，不能在逐日循环中重算全历史EMA")
    feature = prepared["force"][index]
    if feature.get("date") != bars[index]["date"]:
        raise ValueError("主散预计算与日线日期不匹配")
    if index < FORCE_WARMUP or any(bar["volume"] <= 0 for bar in bars[index-FORCE_WARMUP:index+1]):
        return None
    dominance = feature["forceDominance"]
    if dominance is None or feature["mainForce"] <= feature["retailForce"] or dominance < parameters["minDominance"]:
        return None
    if strategy_id == "forceGoldenCross":
        if not feature["goldenCross"]:
            return None
        score = feature["signalScore"] / 100
    else:
        recent = prepared["force"][index-parameters["confirmDays"]+1:index+1]
        if feature["mainForceState"] != "rising" or any(item["forceDominance"] is None or item["forceDominance"] < parameters["minDominance"] or item["mainForce"] <= item["retailForce"] for item in recent):
            return None
        score = dominance
    return {"score": score, "detail": {"strategyId": strategy_id, **feature, "formulaVersion": FORCE_VERSION,
                                         "confirmDays": parameters.get("confirmDays", 1), "minDominance": parameters["minDominance"]}}


def evaluate(bars, index, config, prepared=None):
    """Return a causal AND signal and ranking score, or None for no signal."""
    row = bars[index]
    if row["volume"] <= 0:
        return None
    details, scores = [], []
    for strategy_id in config["strategyIds"]:
        p = config["strategyParams"][strategy_id]
        if strategy_id in FORCE_STRATEGIES:
            signal = _evaluate_force(bars, index, strategy_id, p, prepared)
            if signal is None:
                return None
            scores.append(signal["score"])
            details.append(signal["detail"])
            continue
        window = p["trendWindow"]
        lookback = p.get("lookback", p.get("pullbackWindow", 2))
        if index < max(window, lookback):
            return None
        history = bars[index - max(window, lookback):index + 1]
        if any(b["volume"] <= 0 for b in history):
            return None
        average = mean(b["close"] for b in bars[index-window+1:index+1])
        previous_average = mean(b["close"] for b in bars[index-window:index])
        if not row["close"] > average > previous_average:
            return None
        if strategy_id == "trendBreakout":
            previous = bars[index-lookback:index]
            high = max(b["high"] for b in previous)
            volume_ratio = row["volume"] / mean(b["volume"] for b in previous)
            if row["close"] <= high or volume_ratio < p["volumeRatio"]:
                return None
            score = row["close"] / high - 1
            detail = {"breakoutPrice": high, "volumeRatio": volume_ratio}
        elif strategy_id == "momentum":
            score = row["close"] / bars[index-lookback]["close"] - 1
            if score < p["minReturn"]:
                return None
            detail = {"momentumReturn": score}
        else:
            peak = max(b["close"] for b in bars[index-lookback:index+1])
            drawdown = 1 - row["close"] / peak
            if not p["minDrawdown"] <= drawdown <= p["maxDrawdown"] or row["close"] <= bars[index-1]["close"]:
                return None
            score = row["close"] / average - 1
            detail = {"pullback": drawdown, "recentPeak": peak}
        scores.append(score)
        details.append({"strategyId": strategy_id, "trendAverage": average, **detail})
    return {"score": mean(scores), "strategies": details}
