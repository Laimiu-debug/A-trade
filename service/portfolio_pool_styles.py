"""Candidate-universe presets, never strategy entry or order rules.

The catalogue exposes complete, validated poolSettings snapshots through
portfolio_config. Applying a preset is an explicit editor action; the scorer
continues to consume the saved conditions, not this preset name or version.
"""
from copy import deepcopy


POOL_STYLE_VERSION = "portfolio-pool-styles-v1"

_COMMON_NOTES = [
    "风格漏斗只确定候选范围；是否买入、何时退出和买多少仍由单策略回测决定。",
    "按每个交易日已出现的数据重新筛选，不把最后一天的名单固定用于过去。",
    "这些值是便于开始研究的假设，并非收益验证后的最优参数；先固定回测，再仅扫描少量关键参数。",
    "应用风格只替换分层条件，不修改基础资格、全 A 股来源或策略参数；调整后按实际保存的条件计算。",
]

_BASE_STAGE = {"pool": "base", "reason": "沿用已设置的上市历史、ST、成交额等基础可交易资格。"}

_DEFINITIONS = [
    {
        "id": "base", "name": "仅基础可交易池", "strategyKinds": [],
        "description": "先观察完整可交易范围，不增加趋势、回调或量能限制。",
        "stages": [_BASE_STAGE],
        "poolSettings": {"combined": {"pools": [], "mode": "intersection"}},
        "notes": ["可作为每种风格的对照组，区分策略本身与额外筛选条件的影响。"],
    },
    {
        "id": "breakout", "name": "突破候选漏斗", "strategyKinds": ["trendBreakout"],
        "description": "保留上行趋势且接近近期高位的股票，允许回撤为零的新高；突破事件留给策略确认。",
        "stages": [_BASE_STAGE,
                   {"pool": "trend", "reason": "收盘高于上行的 60 日均线，60 日涨幅不低于 0%。"},
                   {"pool": "structure", "reason": "距近 20 日最高收盘价回撤 0%–8%，包含当日新高。"}],
        "poolSettings": {
            "trend": {"window": 60, "minReturn": 0},
            "structure": {"window": 20, "minDrawdown": 0, "maxDrawdown": .08},
            "combined": {"pools": ["trend", "structure"], "mode": "intersection"},
        },
        "notes": ["候选池不要求当天已经突破，也不强制当天放量；单策略的突破窗口和放量条件独立生效。"],
    },
    {
        "id": "pullback", "name": "趋势回调候选漏斗", "strategyKinds": ["trendPullback"],
        "description": "保留上行趋势中的适度回撤，并检查均线持续性；当日是否修复仍由策略确认。",
        "stages": [_BASE_STAGE,
                   {"pool": "trend", "reason": "收盘高于上行的 60 日均线，60 日涨幅不低于 0%。"},
                   {"pool": "structure", "reason": "近 20 日回撤 2%–15%，收盘高于 MA20，MA10>MA20 至少连续 3 日。"}],
        "poolSettings": {
            "trend": {"window": 60, "minReturn": 0},
            "structure": {"window": 20, "minDrawdown": .02, "maxDrawdown": .15,
                          "requireAboveMa20": True, "minMa10Above20Days": 3,
                          "maDaysMode": "consecutive"},
            "combined": {"pools": ["trend", "structure"], "mode": "intersection"},
        },
        "notes": ["不要求回调日放量或已经回升；量能层未加入漏斗，避免提前把缩量回调排除。"],
    },
    {
        "id": "momentum", "name": "趋势动量候选漏斗", "strategyKinds": ["momentum"],
        "description": "按趋势与完整基础池的相对涨幅排名缩小范围，不强加回调形态。",
        "stages": [_BASE_STAGE,
                   {"pool": "trend", "reason": "收盘高于上行的 60 日均线，60 日涨幅≥5%，同窗口涨幅排名 1–500。"}],
        "poolSettings": {
            "trend": {"window": 60, "minReturn": .05, "rankEnabled": True,
                      "rankWindow": 60, "rankStart": 1, "rankEnd": 500},
            "combined": {"pools": ["trend"], "mode": "intersection"},
        },
        "notes": ["排名分母是基础资格通过且有完整 61 根日线的全部股票，同涨幅按代码排序；不是策略命中名单。"],
    },
    {
        "id": "volume", "name": "量能活跃候选漏斗",
        "strategyKinds": ["forceGoldenCross", "forcePurpleToYellow", "forceTrend"],
        "description": "用真实成交量和 K 线风险条件筛选活跃股票，不在池中判断主散金叉或紫转黄。",
        "stages": [_BASE_STAGE,
                   {"pool": "volume", "reason": "当日成交量/此前 20 日均量≥1.2，排除近 20 日放量收阴或平盘、近 5 日长上影收阴或平盘。"}],
        "poolSettings": {
            "volume": {"window": 20, "minRatio": 1.2, "excludeBlowoff": True,
                       "excludeUpperShadow": True},
            "combined": {"pools": ["volume"], "mode": "intersection"},
        },
        "notes": ["量能活跃只是研究条件，不代表机构资金；主散策略信号使用各自公式独立计算。",
                  "量能条件可能缩小主散策略的候选范围，应与仅基础池的固定参数结果比较。"],
    },
]


def definitions():
    """Owned copies prevent one editor request from mutating later catalogues."""
    result = [deepcopy(row) for row in _DEFINITIONS]
    for row in result:
        row["version"] = POOL_STYLE_VERSION
        row["notes"] += _COMMON_NOTES
    return result
