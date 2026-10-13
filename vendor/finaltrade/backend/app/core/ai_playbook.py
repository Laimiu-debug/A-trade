"""Build AI system playbook from strategy registry and static trading rules."""

from __future__ import annotations

import json
from typing import Any, Literal

from .strategy_registry import StrategyRegistry

PageScope = Literal[
    "chart",
    "screener",
    "signals",
    "signals_backtest",
    "cross_validate",
    "backtest",
    "strategy",
    "event_judgment",
    "review",
    "trade",
    "portfolio",
    "market_trend",
    "sector_capital",
    "abnormal_movement",
    "sentiment_valuation",
    "settings",
    "ai_records",
    "generic",
    "all",
]

SCREENER_FUNNEL: dict[str, str] = {
    "step1": "流动性：turnover20/amount20/amplitude20 达阈值，按 ret40 降序取 top_n（上限约400）",
    "step2": "趋势结构：retrace20 在 5%-25%，均线多头天数，price_vs_ma20 约束，排除 trend_class=B",
    "step3": "量价健康：vol_slope20、up_down_volume_ratio、pullback_volume_ratio，剔除爆量顶/背离/降级",
    "step4": "结构置信度 ai_confidence>=0.55（本地公式非LLM），theme_stage 为发酵中/高潮，Top8",
}

WYCKOFF_EVENTS: dict[str, str] = {
    "Spring": "跌破前低后收回且放量，常见吸筹末段买点",
    "SOS": "突破均线且10日收益>5%，趋势确认",
    "JOC": "收盘突破近20日高点，放量",
    "LPS": "SOS/JOC 后缩量回踩支撑",
    "UTAD": "派发段诱多，风险出场信号",
    "SOW": "弱势信号，派发确认",
    "LPSY": "反弹无力，派发末段",
}

BACKTEST_RULES: dict[str, str] = {
    "entry_delay": "默认 T+1 延迟入场（entry_delay_days）",
    "stop_take": "stop_loss / take_profit / trailing_stop 与分时、日线双层移动止盈",
    "pool_roll": "trend_pool 支持 daily/weekly/position 滚动股票池",
    "t1": "enforce_t1 遵守 A 股 T+1",
    "priority": "priority_mode 控制同日多信号排序",
}

SENTIMENT_VALUATION_MODEL: dict[str, Any] = {
    "formula": "市值(亿) = 当期盈利(亿) × 复合增速系数 × 基准PE × 大盘水位系数 × 情绪溢价",
    "why_not_dcf": (
        "A股因涨跌停、缺乏做空、散户占比高，普遍存在情绪溢价；"
        "教科书 DCF 常得出「全部高估」，需用本模型理解定价。"
    ),
    "factors": {
        "earnings": "当期盈利：今年预期净利润（亿），机构一致预期或用户修正值",
        "growth_coef": "复合增速系数 = 1 + 预期年化增速。例：+30%→1.3，+5%→1.05，-15%→0.85，-30%→0.7",
        "base_pe": (
            "基准PE按行业天花板：银行保险≈10；传统10-15(中值12.5)；消费15-20；"
            "半科技20-25；科技25-30；半导体/机器人等稀缺高成长≈30"
        ),
        "index_coef": "大盘水位系数 = 上证点位/3000。例：3000→1.0，3300→1.1，4090→1.36",
        "sentiment": (
            "情绪溢价：关注度/涨停频率/热门榜/稀缺叙事。中性≈1.0；"
            "温和1.1-2.0；高溢价2-3.5；妖股极值约5-6。"
            "隐含情绪 = 实际市值 / (盈利×增速×PE×大盘系数)"
        ),
    },
    "analysis_steps": [
        "先估算或引用 RUNTIME 中的五因子参数",
        "计算理论市值并与实际市值对比，推导隐含情绪溢价",
        "判断股价主要在赚哪个因子的钱：盈利、增速预期、大盘贝塔、还是情绪",
        "区分业绩驱动(盈利+增速)与纯情绪驱动：后者波动大、高位难站稳",
        "产品涨价模式最佳：同步抬升盈利、增速预期与情绪",
        "银行等板块若长期体现负增长预期但业绩不暴雷，预期会逐年修正、中枢抬升",
    ],
    "answer_format": [
        "用五因子逐项给出假设或 RUNTIME 数值",
        "给出理论市值区间与隐含情绪系数",
        "说明偏贵/偏便宜主要来自哪个因子",
        "提示主要风险（预期反转、情绪退潮、业绩下修）",
        "不给具体买卖价位，仅供研究理解",
    ],
    "reference_cases": {
        "长江电力": "358×1.05×12.5×1.36×1≈6390亿，稳定蓝筹情绪≈1",
        "贵州茅台": "863×0.85×15×1.36×1≈14964亿，定价含悲观增速预期",
        "源杰科技": "12×2.0×30×1.36≈979亿，实际2083亿→情绪≈2.1，稀缺+涨价叙事",
        "宏和科技": "10×2.5×30×1.36≈1020亿，实际2332亿→情绪≈2.3",
    },
}


def _strategy_playbook_entry(descriptor: Any) -> dict[str, Any]:
    return {
        "strategy_id": descriptor.strategy_id,
        "name": descriptor.name,
        "enabled": descriptor.enabled,
        "description": getattr(descriptor, "description", "") or "",
        "playbook": getattr(descriptor, "playbook", {}) or {},
        "default_params": dict(descriptor.default_params),
    }


def build_playbook(
    registry: StrategyRegistry,
    *,
    scope: PageScope = "all",
    strategy_ids: list[str] | None = None,
    user_principles: list[str] | None = None,
) -> dict[str, Any]:
    ids_filter = {item.strip() for item in (strategy_ids or []) if item.strip()}
    strategies: dict[str, Any] = {}
    for descriptor in registry.list():
        if ids_filter and descriptor.strategy_id not in ids_filter:
            continue
        if not descriptor.enabled and not ids_filter:
            continue
        if descriptor.strategy_id.startswith("__removed_"):
            continue
        strategies[descriptor.strategy_id] = _strategy_playbook_entry(descriptor)

    playbook: dict[str, Any] = {
        "version": "1.0",
        "user_principles": list(user_principles or []),
    }

    if scope in ("all", "screener", "chart", "signals"):
        playbook["screener"] = {"funnel": dict(SCREENER_FUNNEL)}
    if scope in ("all", "chart", "signals", "backtest", "strategy"):
        playbook["wyckoff"] = {"events": dict(WYCKOFF_EVENTS)}
    if scope in ("all", "backtest", "signals", "strategy"):
        playbook["backtest"] = {"execution": dict(BACKTEST_RULES)}
    if scope in ("all", "backtest", "strategy", "signals"):
        playbook["strategies"] = strategies
    if scope in ("all", "sentiment_valuation"):
        playbook["sentiment_valuation"] = dict(SENTIMENT_VALUATION_MODEL)

    return playbook


def build_playbook_text(
    registry: StrategyRegistry,
    *,
    scope: PageScope = "all",
    strategy_ids: list[str] | None = None,
    user_principles: list[str] | None = None,
    compact: bool = True,
) -> str:
    payload = build_playbook(
        registry,
        scope=scope,
        strategy_ids=strategy_ids,
        user_principles=user_principles,
    )
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(payload, ensure_ascii=False, indent=2)
