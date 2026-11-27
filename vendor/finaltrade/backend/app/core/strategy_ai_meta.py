"""AI-facing descriptions and playbook snippets for the 9 active production strategies (6 顽主杯大佬模式 + 3 经典结构策略)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

STRATEGY_AI_META: dict[str, dict[str, Any]] = {
    "wujiang_mainline_v1": {
        "description": "乌江望月 · 双轨主线核心：20cm 科创/创业大容量趋势起爆 + 10cm 主板题材封板/首阴反包双轨切换，含情绪四阶段压仓、封板浮盈加仓与早盘极速纠错。",
        "playbook": {
            "intent": "乌江望月模式 · 动态主线切换 + 浮盈加仓 + 次日10:30极速纠错",
            "entry_events": ["乌江20cm容量起爆", "乌江主板首阴反包", "乌江主板连板核心", "乌江主板首板突破"],
            "exit_events": ["破MA20趋势失效", "炸板回落次日快砍", "大阴破MA5极速纠错", "高位巨量长上影派发"],
        },
    },
    "mainline_halfway_v1": {
        "description": "灰常懒 · 主线半路秒砍：MA5>MA10>MA20 多头紧贴均线放量启动半路（+2.5%~+6.5%），拒追高位加速，不及预期次日早盘 15 分钟秒砍回笼子弹。",
        "playbook": {
            "intent": "灰常懒模式 · 绝对主线低位半路 + 早盘15分秒砍",
            "entry_events": ["灰常懒主线半路"],
            "exit_events": ["跌破MA20退出", "炸板转弱早盘快砍", "破MA5早盘15分秒砍"],
        },
    },
    "first_to_second_v1": {
        "description": "希望姐姐 · 纯主板一进二：严格限定主板 10cm 首板换手晋级一进二、良性换手量比（0.8x–2.8x）、剔除一字板，炸板或断板次日 09:35 无条件清仓。",
        "playbook": {
            "intent": "希望姐姐模式 · 纯主板 1 进 2 等权接力",
            "entry_events": ["纯主板一进二备选", "纯主板一进二晋级"],
            "exit_events": ["炸板次日早盘核出", "断板转弱退出", "破MA5快砍退出"],
        },
    },
    "highboard_sniper_v1": {
        "description": "暴涨基金董事长 · 主板高标狙击：66%交易日空仓等待，仅在纯主板走出 2 连板及以上辨识度最高标时精准出击，断板转弱次日早盘果断离场。",
        "playbook": {
            "intent": "暴涨董事长模式 · 空仓等待狙击 2板+ 主板最高标",
            "entry_events": ["董事长主板连板高标"],
            "exit_events": ["炸板次日早盘核出", "断板转弱退出", "破MA5快砍退出"],
        },
    },
    "ruhan_halfway_v1": {
        "description": "如晗 · 分仓半路推土机：4–5 仓均衡分散在 MA20 上方 0 板/首板放量启动股，弱势退潮期自动触发 66% 降仓开关，封板浮盈顺势加仓。",
        "playbook": {
            "intent": "如晗模式 · 均衡分仓半路推土机 + 弱市66%自动降仓",
            "entry_events": ["如晗分仓半路启动"],
            "exit_events": ["跌破MA20退出", "炸板转弱早盘快砍", "破MA5早盘15分秒砍"],
        },
    },
    "strong_trend_dip_v1": {
        "description": "松雅湖的松松 · 强趋势低吸防守：MA20/MA60 上方强趋势核心股分歧回撤缩量承接，期货级紧止损，主跌退潮期强制断电空仓防守。",
        "playbook": {
            "intent": "松松模式 · 强趋势回踩低吸 + 弱市断电空仓防守",
            "entry_events": ["松松强趋势低吸"],
            "exit_events": ["支撑跌破止损", "松松铁血破位止损"],
        },
    },
    "vcp_contraction_v1": {
        "description": "VCP 三段收缩突破（A-trade 49 批实证优胜升级版）：波幅与成交量逐段收敛蓄势后，放量有效突破 20/40 日平台高点。",
        "playbook": {
            "intent": "波动与成交量紧致收缩后放量突破",
            "entry_events": ["VCP收缩突破"],
            "exit_events": ["突破失效跌破MA20"],
        },
    },
    "wulong_cluster_v1": {
        "description": "五龙聚首：MA5/10/20/30/60 五均线粘合收敛后多头发散放量突破，保留原实证最优形态突破机制。",
        "playbook": {
            "intent": "五均线粘合→发散放量突破",
            "entry_events": ["五龙聚首"],
            "exit_events": [],
        },
    },
    "wyckoff_trend_v2": {
        "description": "维科夫趋势 V2：健康分与事件分双门槛，支持矩阵引擎，等待 Spring/SOS/JOC/LPS 蓄势买点，UTAD/SOW/LPSY 派发退出。",
        "playbook": {
            "intent": "结构蓄势 + 威科夫事件确认",
            "entry_events": ["Spring", "SOS", "JOC", "LPS"],
            "exit_events": ["UTAD", "SOW", "LPSY"],
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
