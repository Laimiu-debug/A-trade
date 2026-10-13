"""AI-facing descriptions and playbook snippets for registered strategies."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

STRATEGY_AI_META: dict[str, dict[str, Any]] = {
    "wyckoff_trend_v1": {
        "description": "维科夫事件驱动趋势跟踪：在趋势池内等待 Spring/SOS/JOC/LPS 等买点，UTAD/SOW/LPSY 出场。",
        "playbook": {
            "intent": "趋势池 + 维科夫矩阵信号",
            "entry_events": ["Spring", "SOS", "JOC", "LPS"],
            "exit_events": ["UTAD", "SOW", "LPSY"],
        },
    },
    "wyckoff_trend_v2": {
        "description": "维科夫 V2：健康分与事件分加权，aligned_wyckoff_v2 语义，更强调事件等级与关键事件确认。",
        "playbook": {
            "intent": "健康分+事件分双门槛",
            "entry_events": ["Spring", "SOS", "JOC", "LPS"],
            "exit_events": ["UTAD", "SOW", "LPSY"],
        },
    },
    "relative_strength_breakout_v1": {
        "description": "相对强弱突破：要求 ret40、量比、回撤在区间，综合强度/量能/结构权重排序。",
        "playbook": {"intent": "强者恒强 + 结构突破", "entry_events": [], "exit_events": []},
    },
    "score_only_rank_v1": {
        "description": "纯评分排序：不依赖维科夫入场事件，按 score 阈值筛选。",
        "playbook": {"intent": "Score 排名入场", "entry_events": [], "exit_events": []},
    },
    "ths_main_force_flip_v1": {
        "description": "同花顺主力紫转黄信号触发，不依赖 Wyckoff 事件。",
        "playbook": {"intent": "主力紫转黄", "entry_events": [], "exit_events": []},
    },
    "ths_main_force_golden_cross_v1": {
        "description": "同花顺主力金叉信号触发，不依赖 Wyckoff 事件。",
        "playbook": {"intent": "主力金叉", "entry_events": [], "exit_events": []},
    },
    "ths_force_rhythm_v1": {
        "description": "主散节奏波：只看主力量能波形是否形成规律节奏（周期/波幅/自相关），波谷拐头买入；散户占优抬升为卖出参考，不参与形成判定。",
        "playbook": {
            "intent": "主力节奏波谷",
            "entry_events": [],
            "exit_events": ["散户卖出", "主力波峰"],
        },
    },
    "wulong_cluster_v1": {
        "description": "五龙聚首：均线粘合后发散放量突破，强调带宽扩张与量能配合。",
        "playbook": {"intent": "均线粘合→发散突破", "entry_events": [], "exit_events": []},
    },
    "matrix_signal_v1": {
        "description": "矩阵信号：ATR/量比压缩后突破，适合横盘整理后的放量突破。",
        "playbook": {"intent": "矩阵压缩突破", "entry_events": [], "exit_events": []},
    },
    "b1_mtf_v1": {
        "description": "B1 多周期：月 MACD 多头、周 DIF>0、日 MA5>MA20，缩量小振幅 + KDJ 勾头。",
        "playbook": {
            "intent": "B1 多周期共振",
            "conditions": ["月MACD多", "周DIF>0", "日MA5>MA20", "缩量", "KDJ勾头"],
        },
    },
    "trend_king_v1": {
        "description": "趋势为王：涨停/涨势/回踩三模式综合选股。",
        "playbook": {"intent": "趋势龙头多模式", "modes": ["a涨停", "b涨势", "c回踩"]},
    },
    "trend_king_limitup_v1": {
        "description": "涨停板精选：模式 A，抓涨停日弹性与历史弹性。",
        "playbook": {"intent": "涨停精选", "mode": "a"},
    },
    "trend_king_rally_v1": {
        "description": "涨势确认：模式 B，3/5 日涨幅确认。",
        "playbook": {"intent": "涨势确认", "mode": "b"},
    },
    "trend_king_pullback_v1": {
        "description": "涨停跟踪池：模式 C，涨停后缩量回踩跟踪。",
        "playbook": {"intent": "涨停跟踪", "mode": "c"},
    },
    "emotion_limit_up_v1": {
        "description": "情绪涨停：近5日天下无双金叉或紫转黄 + 当日涨停共振；次日倍量且未深跌时尾盘买入。",
        "playbook": {
            "intent": "信号确认+涨停共振",
            "entry_timing": "涨停次日14:45-15:00",
            "next_day_rules": ["倍量(>=2x)", "没死(相对涨停价-5%)"],
        },
    },
    "limit_up_arb_v1": {
        "description": "涨停套利：收盘后确认（昨涨停+今涨幅+今相对昨量同时满足）；触发日=确认日，入场=触发日收盘价，与回测一致。",
        "playbook": {
            "intent": "涨停接力确认",
            "signal_timing": "post_close_only",
            "screen_rules": ["昨涨停", "今量>=昨量×1.2", "今涨幅>=3%"],
            "entry_timing": "触发日收盘价（信号日=确认日，非盘中观察池）",
            "exit_rules": ["回测页 take_profit", "回测页 stop_loss", "回测页 max_hold_days"],
        },
    },
}


def apply_strategy_ai_meta(strategies: dict[str, Any]) -> None:
    for strategy_id, meta in STRATEGY_AI_META.items():
        descriptor = strategies.get(strategy_id)
        if descriptor is None:
            continue
        strategies[strategy_id] = replace(
            descriptor,
            description=str(meta.get("description", "")),
            playbook=dict(meta.get("playbook", {})),
        )
