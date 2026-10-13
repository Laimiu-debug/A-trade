"""
A-share sentiment valuation model.

Formula (market cap in 亿):
  市值 = 当期盈利 × 复合增速系数 × 基准PE × 大盘水位系数 × 情绪溢价
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

IndustryPeTier = Literal[
    "bank_insurance",
    "traditional",
    "consumer",
    "semi_tech",
    "tech",
    "high_growth",
]

INDUSTRY_PE_PRESETS: dict[IndustryPeTier, tuple[float, float, str]] = {
    "bank_insurance": (10.0, 10.0, "银行/保险（成熟行业，天花板明显）"),
    "traditional": (10.0, 15.0, "传统行业（电力、煤炭、基建等）"),
    "consumer": (15.0, 20.0, "消费行业（白酒、食品、家电等）"),
    "semi_tech": (20.0, 25.0, "半科技（新能源、高端制造等）"),
    "tech": (25.0, 30.0, "科技（软件、通信设备等）"),
    "high_growth": (30.0, 30.0, "高成长稀缺（半导体、机器人等，行业空间被认为无限）"),
}

INDUSTRY_KEYWORD_TO_TIER: list[tuple[tuple[str, ...], IndustryPeTier]] = [
    (("银行", "保险", "证券"), "bank_insurance"),
    (("半导体", "芯片", "机器人", "人工智能", "算力", "光模块", "激光"), "high_growth"),
    (("软件", "互联网", "通信", "电子", "计算机"), "tech"),
    (("新能源", "光伏", "锂电", "汽车", "军工", "机械"), "semi_tech"),
    (("白酒", "食品", "饮料", "家电", "医药", "消费"), "consumer"),
    (("电力", "煤炭", "石油", "钢铁", "化工", "建筑", "交通", "公用"), "traditional"),
]

SENTIMENT_EXTREME_LOW = 0.85
SENTIMENT_EXTREME_HIGH = 6.0


@dataclass(frozen=True)
class ValuationInputs:
    earnings_yi: float
    growth_coef: float
    base_pe: float
    index_coef: float
    sentiment_coef: float = 1.0


@dataclass(frozen=True)
class ValuationResult:
    theoretical_cap_yi: float
    earnings_yi: float
    growth_coef: float
    base_pe: float
    index_coef: float
    sentiment_coef: float
    earnings_component_yi: float
    growth_uplift_pct: float
    pe_component_yi: float
    index_uplift_pct: float
    sentiment_uplift_pct: float


def growth_rate_to_coef(growth_rate_pct: float) -> float:
    """Map expected annual growth (%) to the model coefficient (1 + rate)."""
    return 1.0 + growth_rate_pct / 100.0


def index_points_to_coef(index_points: float, *, base_points: float = 3000.0) -> float:
    if base_points <= 0:
        return 1.0
    return max(0.5, index_points / base_points)


def calc_theoretical_cap(inputs: ValuationInputs) -> ValuationResult:
    base = inputs.earnings_yi * inputs.growth_coef * inputs.base_pe * inputs.index_coef
    theoretical = base * inputs.sentiment_coef
    earnings_component = inputs.earnings_yi
    growth_uplift_pct = (inputs.growth_coef - 1.0) * 100.0
    pe_component = inputs.earnings_yi * inputs.growth_coef * inputs.base_pe
    index_uplift_pct = (inputs.index_coef - 1.0) * 100.0
    sentiment_uplift_pct = (inputs.sentiment_coef - 1.0) * 100.0
    return ValuationResult(
        theoretical_cap_yi=theoretical,
        earnings_yi=inputs.earnings_yi,
        growth_coef=inputs.growth_coef,
        base_pe=inputs.base_pe,
        index_coef=inputs.index_coef,
        sentiment_coef=inputs.sentiment_coef,
        earnings_component_yi=earnings_component,
        growth_uplift_pct=growth_uplift_pct,
        pe_component_yi=pe_component,
        index_uplift_pct=index_uplift_pct,
        sentiment_uplift_pct=sentiment_uplift_pct,
    )


def calc_implied_sentiment(
    actual_cap_yi: float,
    *,
    earnings_yi: float,
    growth_coef: float,
    base_pe: float,
    index_coef: float,
) -> float | None:
    base = earnings_yi * growth_coef * base_pe * index_coef
    if base <= 0:
        return None
    return actual_cap_yi / base


def suggest_industry_pe_tier(industry: str) -> IndustryPeTier:
    text = industry.strip()
    if not text:
        return "traditional"
    for keywords, tier in INDUSTRY_KEYWORD_TO_TIER:
        if any(keyword in text for keyword in keywords):
            return tier
    return "traditional"


def suggest_base_pe(industry: str) -> tuple[float, IndustryPeTier, str]:
    tier = suggest_industry_pe_tier(industry)
    low, high, label = INDUSTRY_PE_PRESETS[tier]
    mid = (low + high) / 2.0
    return mid, tier, label


def sentiment_label(coef: float) -> str:
    if coef < 0.95:
        return "偏低（关注度不足或悲观预期）"
    if coef <= 1.15:
        return "中性（基本面定价为主）"
    if coef <= 2.0:
        return "温和溢价（有一定关注度）"
    if coef <= 3.5:
        return "高溢价（题材/稀缺性驱动）"
    if coef <= SENTIMENT_EXTREME_HIGH:
        return "极高溢价（连板/妖股区间）"
    return "超极值（>6.0，需极度谨慎）"


def limit_up_threshold_pct(symbol: str, name: str = "") -> float:
    normalized = symbol.lower().strip()
    label = f"{normalized}{name}"
    if "st" in label.lower():
        return 4.9
    if normalized.startswith(("sz3", "sh68")):
        return 19.9
    if normalized.startswith("bj"):
        return 29.9
    return 9.9


def count_limit_ups(candles: list[Any], symbol: str, name: str = "", window: int = 60) -> dict[str, int | float]:
    if not candles:
        return {"limit_up_count": 0, "big_gain_days": 0, "window_days": window}
    threshold = limit_up_threshold_pct(symbol, name) / 100.0
    recent = candles[-window:] if len(candles) > window else candles
    limit_up_count = 0
    big_gain_days = 0
    for idx, candle in enumerate(recent):
        close = _candle_float(candle, "close")
        if close is None or close <= 0:
            continue
        prev_close = None
        if idx > 0:
            prev_close = _candle_float(recent[idx - 1], "close")
        elif idx == 0 and len(candles) > len(recent):
            prev_close = _candle_float(candles[len(candles) - len(recent) - 1], "close")
        if prev_close is None or prev_close <= 0:
            continue
        pct = close / prev_close - 1.0
        if pct >= threshold - 0.002:
            limit_up_count += 1
        if pct >= 0.07:
            big_gain_days += 1
    return {
        "limit_up_count": limit_up_count,
        "big_gain_days": big_gain_days,
        "window_days": len(recent),
    }


def suggest_sentiment_from_activity(limit_up_count: int, big_gain_days: int) -> float:
    if limit_up_count >= 8:
        return 4.5
    if limit_up_count >= 5:
        return 3.5
    if limit_up_count >= 3:
        return 2.5
    if limit_up_count >= 1:
        return 1.8
    if big_gain_days >= 8:
        return 1.5
    if big_gain_days >= 4:
        return 1.25
    if big_gain_days >= 2:
        return 1.1
    return 1.0


def _candle_float(candle: Any, field: str) -> float | None:
    if isinstance(candle, dict):
        raw = candle.get(field)
    else:
        raw = getattr(candle, field, None)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not __import__("math").isfinite(value):
        return None
    return value


def _eastmoney_num(data: dict[str, Any], key: str, *, scale: float = 1.0) -> float | None:
    raw = data.get(key)
    try:
        value = float(raw) / scale
    except (TypeError, ValueError):
        return None
    if not __import__("math").isfinite(value):
        return None
    return value


def _eastmoney_pe(data: dict[str, Any], *keys: str) -> float | None:
    """PE fields from Eastmoney are scaled by 100; non-positive means loss / unavailable."""
    for key in keys:
        value = _eastmoney_num(data, key, scale=100.0)
        if value is not None and value > 0:
            return value
    return None


def parse_eastmoney_quote(data: dict[str, Any]) -> dict[str, float | str | None]:
    price = _eastmoney_num(data, "f43", scale=100.0)
    market_cap_yuan = _eastmoney_num(data, "f116", scale=1.0)
    market_cap_yi = market_cap_yuan / 1e8 if market_cap_yuan is not None and market_cap_yuan > 0 else None
    # f164=滚动市盈率(TTM), f162=动态, f163=静态；f173 不是 PE
    pe_ttm = _eastmoney_pe(data, "f164", "f162", "f163")
    implied_earnings_yi = None
    if market_cap_yi is not None and pe_ttm is not None:
        implied_earnings_yi = market_cap_yi / pe_ttm

    return {
        "name": str(data.get("f58", "")).strip() or None,
        "industry": str(data.get("f127", "")).strip() or None,
        "price": price,
        "market_cap_yi": market_cap_yi,
        "pe_ttm": pe_ttm,
        "implied_earnings_yi": implied_earnings_yi,
    }


def symbol_to_secid(symbol: str) -> str | None:
    raw = symbol.lower().strip()
    market = ""
    code = ""
    if raw.startswith("sz"):
        market, code = "0", raw[2:]
    elif raw.startswith("sh"):
        market, code = "1", raw[2:]
    elif raw.startswith("bj"):
        market, code = "0", raw[2:]
    elif re.fullmatch(r"\d{6}", raw):
        code = raw
        market = "1" if raw.startswith(("5", "6", "9")) else "0"
    if not code or not re.fullmatch(r"\d{6}", code):
        return None
    return f"{market}.{code}"


def summarize_valuation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    earnings = _to_float(payload.get("earnings_yi") or payload.get("earningsYi"))
    growth_rate = _to_float(payload.get("growth_rate_pct") or payload.get("growthRatePct"))
    growth_coef = _to_float(payload.get("growth_coef") or payload.get("growthCoef"))
    if growth_coef is None and growth_rate is not None:
        growth_coef = growth_rate_to_coef(growth_rate)
    base_pe = _to_float(payload.get("base_pe") or payload.get("basePe"))
    index_points = _to_float(payload.get("index_points") or payload.get("indexPoints"))
    index_coef = _to_float(payload.get("index_coef") or payload.get("indexCoef"))
    if index_coef is None and index_points is not None:
        index_coef = index_points_to_coef(index_points)
    sentiment_coef = _to_float(payload.get("sentiment_coef") or payload.get("sentimentCoef"))
    actual_cap = _to_float(payload.get("actual_cap_yi") or payload.get("actualCapYi"))
    theoretical_cap = _to_float(payload.get("theoretical_cap_yi") or payload.get("theoreticalCapYi"))
    implied_sentiment = _to_float(payload.get("implied_sentiment_coef") or payload.get("impliedSentimentCoef"))

    if earnings is not None and growth_coef is not None and base_pe is not None and index_coef is not None:
        if theoretical_cap is None:
            theoretical_cap = earnings * growth_coef * base_pe * index_coef * (sentiment_coef or 1.0)
        if implied_sentiment is None and actual_cap is not None:
            base = earnings * growth_coef * base_pe * index_coef
            if base > 0:
                implied_sentiment = actual_cap / base

    summary: dict[str, Any] = {
        "valuation_inputs": {
            "earnings_yi": earnings,
            "growth_rate_pct": growth_rate,
            "growth_coef": growth_coef,
            "base_pe": base_pe,
            "index_points": index_points,
            "index_coef": index_coef,
            "sentiment_coef": sentiment_coef,
            "actual_cap_yi": actual_cap,
        },
        "valuation_outputs": {
            "theoretical_cap_yi": theoretical_cap,
            "implied_sentiment_coef": implied_sentiment,
            "sentiment_label": sentiment_label(implied_sentiment) if implied_sentiment is not None else None,
        },
        "formula": "市值 = 当期盈利 × 复合增速系数 × 基准PE × 大盘水位系数 × 情绪溢价",
    }
    note = str(payload.get("note") or "").strip()
    if note:
        summary["user_note"] = note
    return summary


def _to_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not __import__("math").isfinite(parsed):
        return None
    return parsed
