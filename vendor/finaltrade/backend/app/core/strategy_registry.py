from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from .strategy_plugins import (
    B1MultiTimeframePlugin,
    BaseStrategyPlugin,
    MatrixSignalPlugin,
    RelativeStrengthBreakoutPlugin,
    ScoreOnlyRankPlugin,
    StrategyPlugin,
    EmotionLimitUpPlugin,
    LimitUpArbPlugin,
    ForceRhythmPlugin,
    ThsMainRetailSignalPlugin,
    TrendKingPlugin,
    WulongClusterPlugin,
    WyckoffTrendPlugin,
)


@dataclass(frozen=True)
class StrategyCapabilities:
    supports_matrix: bool
    supports_signal_age_filter: bool
    supports_entry_delay: bool


@dataclass(frozen=True)
class StrategyDescriptor:
    strategy_id: str
    name: str
    version: str
    enabled: bool
    is_default: bool
    capabilities: StrategyCapabilities
    params_schema: dict[str, dict[str, Any]]
    default_params: dict[str, Any]
    signal_top_n: int = 0
    description: str = ""
    playbook: dict[str, Any] = field(default_factory=dict)


class StrategyRegistry:
    _DEFAULT_STRATEGY_ID = "wyckoff_trend_v1"

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyDescriptor] = {}
        self._plugins: dict[str, StrategyPlugin] = {}
        self._fallback_plugin: StrategyPlugin = BaseStrategyPlugin()
        self._register_builtin()

    def _register_builtin(self) -> None:
        v1 = StrategyDescriptor(
            strategy_id="wyckoff_trend_v1",
            name="维科夫趋势V1",
            version="1.0.0",
            enabled=True,
            is_default=True,
            capabilities=StrategyCapabilities(
                supports_matrix=True,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema={},
            default_params={},
        )
        v2_schema: dict[str, dict[str, Any]] = {
            "matrix_event_semantic_version": {
                "type": "enum",
                "title": "矩阵语义版本",
                "options": ["matrix_v1", "aligned_wyckoff_v2"],
                "default": "aligned_wyckoff_v2",
            },
            "rank_weight_health": {
                "type": "number",
                "title": "健康分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
            },
            "rank_weight_event": {
                "type": "number",
                "title": "事件分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 55.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 55.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "B",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": True,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 60.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 1,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
        }
        v2 = StrategyDescriptor(
            strategy_id="wyckoff_trend_v2",
            name="维科夫趋势V2",
            version="2.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=True,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v2_schema,
            default_params={
                "matrix_event_semantic_version": "aligned_wyckoff_v2",
                "rank_weight_health": 0.5,
                "rank_weight_event": 0.5,
                "health_score_min": 55.0,
                "event_score_min": 55.0,
                "event_grade_min": "B",
                "require_key_event_confirmation": True,
            },
        )
        self._strategies[v1.strategy_id] = v1
        self._strategies[v2.strategy_id] = v2
        v3_schema: dict[str, dict[str, Any]] = {
            "min_ret40": {
                "type": "number",
                "title": "40日涨幅下限",
                "minimum": 0.0,
                "maximum": 1.5,
                "default": 0.12,
            },
            "max_retrace20": {
                "type": "number",
                "title": "20日回撤上限",
                "minimum": 0.01,
                "maximum": 0.60,
                "default": 0.22,
            },
            "min_up_down_volume_ratio": {
                "type": "number",
                "title": "上下跌量比下限",
                "minimum": 0.8,
                "maximum": 3.0,
                "default": 1.15,
            },
            "min_vol_slope20": {
                "type": "number",
                "title": "20日量能斜率下限",
                "minimum": -0.5,
                "maximum": 0.5,
                "default": 0.02,
            },
            "min_ai_confidence": {
                "type": "number",
                "title": "AI置信度下限",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.0,
            },
            "rank_weight_health": {
                "type": "number",
                "title": "健康分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.25,
            },
            "rank_weight_event": {
                "type": "number",
                "title": "事件分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.25,
            },
            "rank_weight_strength": {
                "type": "number",
                "title": "强势分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.30,
            },
            "rank_weight_volume": {
                "type": "number",
                "title": "量能分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.10,
            },
            "rank_weight_structure": {
                "type": "number",
                "title": "结构分权重",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.10,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 52.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 45.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 45.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        v3 = StrategyDescriptor(
            strategy_id="relative_strength_breakout_v1",
            name="相对强弱突破V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v3_schema,
            default_params={
                "min_ret40": 0.12,
                "max_retrace20": 0.22,
                "min_up_down_volume_ratio": 1.15,
                "min_vol_slope20": 0.02,
                "min_ai_confidence": 0.0,
                "rank_weight_health": 0.25,
                "rank_weight_event": 0.25,
                "rank_weight_strength": 0.30,
                "rank_weight_volume": 0.10,
                "rank_weight_structure": 0.10,
                "min_score": 52.0,
                "min_event_count": 0,
                "require_sequence": False,
                "health_score_min": 45.0,
                "event_score_min": 45.0,
                "event_grade_min": "C",
                "require_key_event_confirmation": False,
            },
        )
        self._strategies[v3.strategy_id] = v3
        v35_schema: dict[str, dict[str, Any]] = {
            "min_ret40": {
                "type": "number",
                "title": "40日涨幅下限",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.06,
            },
            "max_ret40": {
                "type": "number",
                "title": "40日涨幅上限",
                "minimum": 0.05,
                "maximum": 1.2,
                "default": 0.42,
            },
            "min_up_down_volume_ratio": {
                "type": "number",
                "title": "主力占优量比下限",
                "minimum": 0.9,
                "maximum": 3.0,
                "default": 1.05,
            },
            "min_vol_slope20": {
                "type": "number",
                "title": "20日量能斜率下限",
                "minimum": -0.5,
                "maximum": 0.5,
                "default": 0.0,
            },
            "max_retrace20": {
                "type": "number",
                "title": "20日回撤上限",
                "minimum": 0.03,
                "maximum": 0.5,
                "default": 0.20,
            },
            "max_pullback_volume_ratio": {
                "type": "number",
                "title": "回调量能比上限",
                "minimum": 0.4,
                "maximum": 1.5,
                "default": 0.95,
            },
            "min_price_vs_ma20": {
                "type": "number",
                "title": "相对MA20偏离下限",
                "minimum": -0.2,
                "maximum": 0.1,
                "default": -0.02,
            },
            "max_price_vs_ma20": {
                "type": "number",
                "title": "相对MA20偏离上限",
                "minimum": 0.0,
                "maximum": 0.3,
                "default": 0.12,
            },
            "min_ma10_above_ma20_days": {
                "type": "integer",
                "title": "MA10站上MA20最少天数",
                "minimum": 0,
                "maximum": 30,
                "default": 3,
            },
            "min_main_force_score": {
                "type": "number",
                "title": "主力强度下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 52.0,
            },
            "min_path_quality_score": {
                "type": "number",
                "title": "上涨路径分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 55.0,
            },
            "min_force_gap_score": {
                "type": "number",
                "title": "主散差值下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 58.0,
            },
            "max_retail_pressure_score": {
                "type": "number",
                "title": "散户压力分上限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 52.0,
            },
            "min_pullback_days": {
                "type": "integer",
                "title": "最小回调天数",
                "minimum": 0,
                "maximum": 20,
                "default": 0,
            },
            "max_pullback_days": {
                "type": "integer",
                "title": "回调容忍天数上限",
                "minimum": 0,
                "maximum": 20,
                "default": 6,
            },
            "entry_quality_floor": {
                "type": "number",
                "title": "信号质量兜底分",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 45.0,
            },
            "allow_blowoff_top": {
                "type": "boolean",
                "title": "允许高潮尖顶",
                "default": False,
            },
            "allow_divergence_5d": {
                "type": "boolean",
                "title": "允许5日背离",
                "default": False,
            },
            "allow_upper_shadow_risk": {
                "type": "boolean",
                "title": "允许上影线风险",
                "default": False,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 50.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        v35 = StrategyDescriptor(
            strategy_id="__removed_force_crossover_v1",
            name="主力量能金叉V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v35_schema,
            default_params={
                "min_ret40": 0.06,
                "max_ret40": 0.42,
                "min_up_down_volume_ratio": 1.05,
                "min_vol_slope20": 0.0,
                "max_retrace20": 0.20,
                "max_pullback_volume_ratio": 0.95,
                "min_price_vs_ma20": -0.02,
                "max_price_vs_ma20": 0.12,
                "min_ma10_above_ma20_days": 3,
                "min_main_force_score": 52.0,
                "min_path_quality_score": 55.0,
                "min_force_gap_score": 58.0,
                "max_retail_pressure_score": 52.0,
                "min_pullback_days": 0,
                "max_pullback_days": 6,
                "entry_quality_floor": 45.0,
                "allow_blowoff_top": False,
                "allow_divergence_5d": False,
                "allow_upper_shadow_risk": False,
                "min_score": 50.0,
                "min_event_count": 0,
                "require_sequence": False,
                "health_score_min": 0.0,
                "event_score_min": 0.0,
                "event_grade_min": "C",
                "require_key_event_confirmation": False,
            },
        )
        v36 = StrategyDescriptor(
            strategy_id="__removed_force_launch_v1",
            name="主力启动金叉V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v35_schema,
            default_params={
                "min_ret40": 0.03,
                "max_ret40": 0.24,
                "min_up_down_volume_ratio": 1.10,
                "min_vol_slope20": 0.03,
                "max_retrace20": 0.12,
                "max_pullback_volume_ratio": 0.92,
                "min_price_vs_ma20": 0.0,
                "max_price_vs_ma20": 0.08,
                "min_ma10_above_ma20_days": 2,
                "min_main_force_score": 58.0,
                "min_path_quality_score": 52.0,
                "min_force_gap_score": 62.0,
                "max_retail_pressure_score": 45.0,
                "min_pullback_days": 0,
                "max_pullback_days": 3,
                "entry_quality_floor": 48.0,
                "allow_blowoff_top": False,
                "allow_divergence_5d": False,
                "allow_upper_shadow_risk": False,
                "min_score": 52.0,
                "min_event_count": 0,
                "require_sequence": False,
                "health_score_min": 0.0,
                "event_score_min": 0.0,
                "event_grade_min": "C",
                "require_key_event_confirmation": False,
            },
        )
        v37 = StrategyDescriptor(
            strategy_id="__removed_force_pullback_v1",
            name="主力回踩续涨V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v35_schema,
            default_params={
                "min_ret40": 0.10,
                "max_ret40": 0.38,
                "min_up_down_volume_ratio": 1.02,
                "min_vol_slope20": -0.01,
                "max_retrace20": 0.18,
                "max_pullback_volume_ratio": 0.82,
                "min_price_vs_ma20": -0.03,
                "max_price_vs_ma20": 0.06,
                "min_ma10_above_ma20_days": 5,
                "min_main_force_score": 50.0,
                "min_path_quality_score": 64.0,
                "min_force_gap_score": 56.0,
                "max_retail_pressure_score": 48.0,
                "min_pullback_days": 2,
                "max_pullback_days": 8,
                "entry_quality_floor": 45.0,
                "allow_blowoff_top": False,
                "allow_divergence_5d": False,
                "allow_upper_shadow_risk": False,
                "min_score": 50.0,
                "min_event_count": 0,
                "require_sequence": False,
                "health_score_min": 0.0,
                "event_score_min": 0.0,
                "event_grade_min": "C",
                "require_key_event_confirmation": False,
            },
        )
        v4_schema: dict[str, dict[str, Any]] = {
            "min_score": {
                "type": "number",
                "title": "Score threshold",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 60.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "Minimum event count",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "Require event sequence",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "Health score min",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "Event score min",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "Event grade min",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "Require key event confirmation",
                "default": False,
            },
        }
        v4 = StrategyDescriptor(
            strategy_id="score_only_rank_v1",
            name="ScoreOnlyRank V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v4_schema,
            default_params={
                "min_score": 60.0,
                "min_event_count": 0,
                "require_sequence": False,
                "health_score_min": 0.0,
                "event_score_min": 0.0,
                "event_grade_min": "C",
                "require_key_event_confirmation": False,
            },
        )
        self._strategies[v4.strategy_id] = v4

        volume_signal_schema: dict[str, dict[str, Any]] = {
            "min_score": {
                "type": "number",
                "title": "Signal score min",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "Minimum event count",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "Require event sequence",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "Health score min",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "Event score min",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "Event grade min",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "Require key event confirmation",
                "default": False,
            },
        }
        volume_signal_defaults = {
            "min_score": 0.0,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        v41 = StrategyDescriptor(
            strategy_id="ths_main_force_flip_v1",
            name="主力紫转黄V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(volume_signal_schema),
            default_params=dict(volume_signal_defaults),
        )
        self._strategies[v41.strategy_id] = v41
        v42 = StrategyDescriptor(
            strategy_id="ths_main_force_golden_cross_v1",
            name="主力金叉V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(volume_signal_schema),
            default_params=dict(volume_signal_defaults),
        )
        self._strategies[v42.strategy_id] = v42
        force_rhythm_schema: dict[str, dict[str, Any]] = {
            "min_cycle_count": {
                "type": "integer",
                "title": "最少完整周期数",
                "minimum": 2,
                "maximum": 12,
                "default": 3,
            },
            "max_cycle_cv": {
                "type": "number",
                "title": "周期长度变异系数上限",
                "minimum": 0.05,
                "maximum": 1.5,
                "default": 0.55,
            },
            "max_amplitude_cv": {
                "type": "number",
                "title": "波幅变异系数上限",
                "minimum": 0.05,
                "maximum": 1.5,
                "default": 0.85,
            },
            "min_autocorr": {
                "type": "number",
                "title": "周期自相关下限",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.05,
            },
            "min_wave_swing_ratio": {
                "type": "number",
                "title": "主力波幅/均值下限",
                "minimum": 0.01,
                "maximum": 2.0,
                "default": 0.12,
            },
            "trough_percentile_max": {
                "type": "number",
                "title": "主力波谷分位上限",
                "minimum": 0.05,
                "maximum": 0.8,
                "default": 0.40,
            },
            "peak_percentile_min": {
                "type": "number",
                "title": "主力波峰分位下限(卖出)",
                "minimum": 0.4,
                "maximum": 0.95,
                "default": 0.65,
            },
            "retail_high_percentile_min": {
                "type": "number",
                "title": "散户高位分位(卖出)",
                "minimum": 0.4,
                "maximum": 0.95,
                "default": 0.60,
            },
            "require_trough_turn": {
                "type": "boolean",
                "title": "要求主力波谷拐头",
                "default": True,
            },
            "block_buy_on_retail_sell": {
                "type": "boolean",
                "title": "散户卖出时禁止买入",
                "default": True,
            },
            "peak_reject_percentile_min": {
                "type": "number",
                "title": "波峰分位拒绝阈值",
                "minimum": 0.5,
                "maximum": 0.95,
                "default": 0.72,
            },
            "flip_trough_percentile_min": {
                "type": "number",
                "title": "紫转黄翻转带下限",
                "minimum": 0.2,
                "maximum": 0.7,
                "default": 0.42,
            },
            "flip_trough_percentile_max": {
                "type": "number",
                "title": "紫转黄翻转带上限",
                "minimum": 0.3,
                "maximum": 0.8,
                "default": 0.55,
            },
            "trough_turn_percentile_min": {
                "type": "number",
                "title": "波谷拐头分位下限",
                "minimum": 0.05,
                "maximum": 0.5,
                "default": 0.17,
            },
            "trough_turn_percentile_max": {
                "type": "number",
                "title": "波谷拐头分位上限",
                "minimum": 0.1,
                "maximum": 0.6,
                "default": 0.30,
            },
            "deep_trough_percentile_max": {
                "type": "number",
                "title": "极深波谷分位上限",
                "minimum": 0.005,
                "maximum": 0.08,
                "default": 0.015,
            },
            "deep_trough_streak_target": {
                "type": "integer",
                "title": "极深波谷连续天数",
                "minimum": 1,
                "maximum": 5,
                "default": 2,
            },
            "flip_follow_bars_ago": {
                "type": "integer",
                "title": "翻转跟随延迟(交易日)",
                "minimum": 1,
                "maximum": 5,
                "default": 2,
            },
            "flip_follow_max_trough_percentile": {
                "type": "number",
                "title": "翻转日波谷分位上限",
                "minimum": 0.05,
                "maximum": 0.5,
                "default": 0.35,
            },
            "flip_follow_min_trough_percentile": {
                "type": "number",
                "title": "翻转日波谷分位下限",
                "minimum": 0.05,
                "maximum": 0.5,
                "default": 0.17,
            },
            "retail_block_min_trough_percentile": {
                "type": "number",
                "title": "散户过滤波谷分位阈值",
                "minimum": 0.05,
                "maximum": 0.5,
                "default": 0.22,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件顺序",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        force_rhythm_defaults = {
            "min_cycle_count": 3,
            "max_cycle_cv": 0.55,
            "max_amplitude_cv": 0.85,
            "min_autocorr": 0.05,
            "min_wave_swing_ratio": 0.12,
            "trough_percentile_max": 0.40,
            "peak_percentile_min": 0.65,
            "retail_high_percentile_min": 0.60,
            "require_trough_turn": True,
            "block_buy_on_retail_sell": True,
            "peak_reject_percentile_min": 0.72,
            "flip_trough_percentile_min": 0.42,
            "flip_trough_percentile_max": 0.55,
            "trough_turn_percentile_min": 0.17,
            "trough_turn_percentile_max": 0.30,
            "deep_trough_percentile_max": 0.015,
            "deep_trough_streak_target": 2,
            "flip_follow_bars_ago": 2,
            "flip_follow_max_trough_percentile": 0.35,
            "flip_follow_min_trough_percentile": 0.17,
            "retail_block_min_trough_percentile": 0.22,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        force_rhythm = StrategyDescriptor(
            strategy_id="ths_force_rhythm_v1",
            name="主散节奏波V1",
            version="1.0.1",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(force_rhythm_schema),
            default_params=dict(force_rhythm_defaults),
        )
        self._strategies[force_rhythm.strategy_id] = force_rhythm
        wulong_schema: dict[str, dict[str, Any]] = {
            "min_ret40": {
                "type": "number",
                "title": "40日涨幅下限",
                "minimum": 0.0,
                "maximum": 1.2,
                "default": 0.06,
            },
            "max_retrace20": {
                "type": "number",
                "title": "20日回撤上限",
                "minimum": 0.01,
                "maximum": 0.5,
                "default": 0.18,
            },
            "min_up_down_volume_ratio": {
                "type": "number",
                "title": "上涨量能比下限",
                "minimum": 0.8,
                "maximum": 3.0,
                "default": 1.08,
            },
            "min_vol_slope20": {
                "type": "number",
                "title": "20日量能斜率下限",
                "minimum": -0.5,
                "maximum": 0.5,
                "default": 0.0,
            },
            "min_ma10_above_ma20_days": {
                "type": "integer",
                "title": "MA10站上MA20最少天数",
                "minimum": 0,
                "maximum": 30,
                "default": 4,
            },
            "min_ma5_above_ma10_days": {
                "type": "integer",
                "title": "MA5站上MA10最少天数",
                "minimum": 0,
                "maximum": 30,
                "default": 3,
            },
            "convergence_threshold_pct": {
                "type": "number",
                "title": "均线粘合阈值",
                "minimum": 0.002,
                "maximum": 0.05,
                "default": 0.04,
            },
            "pre_convergence_min_spread_pct": {
                "type": "number",
                "title": "粘合前最小分散度",
                "minimum": 0.005,
                "maximum": 0.15,
                "default": 0.02,
            },
            "min_current_spread_pct": {
                "type": "number",
                "title": "发散后最小均线带宽",
                "minimum": 0.003,
                "maximum": 0.08,
                "default": 0.01,
            },
            "min_spread_expansion_multiple": {
                "type": "number",
                "title": "带宽扩张倍数下限",
                "minimum": 1.0,
                "maximum": 6.0,
                "default": 1.6,
            },
            "min_volume_ratio20": {
                "type": "number",
                "title": "放量倍数下限(相对20日均量)",
                "minimum": 0.8,
                "maximum": 4.0,
                "default": 1.2,
            },
            "min_breakout_return_pct": {
                "type": "number",
                "title": "突破涨幅下限",
                "minimum": 0.0,
                "maximum": 0.15,
                "default": 0.006,
            },
            "max_convergence_age_days": {
                "type": "integer",
                "title": "允许的最近粘合天数",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
            },
            "min_rising_ma_count": {
                "type": "integer",
                "title": "向上拐头均线最少条数",
                "minimum": 1,
                "maximum": 5,
                "default": 3,
            },
            "allow_upper_shadow_risk": {
                "type": "boolean",
                "title": "允许上影线风险",
                "default": False,
            },
            "allow_blowoff_top": {
                "type": "boolean",
                "title": "允许高潮尖顶",
                "default": False,
            },
            "min_score": {
                "type": "number",
                "title": "信号质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 52.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件顺序",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "要求关键事件确认",
                "default": False,
            },
        }
        wulong_defaults = {
            "min_ret40": 0.06,
            "max_retrace20": 0.18,
            "min_up_down_volume_ratio": 1.08,
            "min_vol_slope20": 0.0,
            "min_ma10_above_ma20_days": 4,
            "min_ma5_above_ma10_days": 3,
            "convergence_threshold_pct": 0.040,
            "pre_convergence_min_spread_pct": 0.020,
            "min_current_spread_pct": 0.010,
            "min_spread_expansion_multiple": 1.6,
            "min_volume_ratio20": 1.2,
            "min_breakout_return_pct": 0.006,
            "max_convergence_age_days": 10,
            "min_rising_ma_count": 3,
            "allow_upper_shadow_risk": False,
            "allow_blowoff_top": False,
            "min_score": 52.0,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        v43 = StrategyDescriptor(
            strategy_id="wulong_cluster_v1",
            name="五龙聚首V1",
            version="2.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(wulong_schema),
            default_params=dict(wulong_defaults),
        )
        self._strategies[v43.strategy_id] = v43
        self._strategies[v43.strategy_id] = replace(self._strategies[v43.strategy_id], name="五龙聚首")
        wulong_v2_defaults = dict(wulong_defaults)
        wulong_v2_defaults.update(
            {
                "min_ret40": 0.08,
                "max_retrace20": 0.16,
                "min_up_down_volume_ratio": 1.12,
                "min_vol_slope20": 0.02,
                "min_ma10_above_ma20_days": 4,
                "min_ma5_above_ma10_days": 3,
                "convergence_threshold_pct": 0.010,
                "pre_convergence_min_spread_pct": 0.040,
                "min_current_spread_pct": 0.018,
                "min_spread_expansion_multiple": 2.2,
                "min_volume_ratio20": 1.5,
                "min_breakout_return_pct": 0.012,
                "max_convergence_age_days": 5,
                "min_rising_ma_count": 5,
                "min_score": 60.0,
            }
        )
        v44 = StrategyDescriptor(
            strategy_id="__removed_wulong_cluster_v2",
            name="五龙聚首V2",
            version="2.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(wulong_schema),
            default_params=dict(wulong_v2_defaults),
        )
        _ = v44

        # ── matrix_signal_v1 ──
        v5_schema: dict[str, dict[str, Any]] = {
            "atr_ratio_max": {
                "type": "number",
                "title": "S1 波动收敛阈值",
                "minimum": 0.1,
                "maximum": 2.0,
                "default": 0.7,
            },
            "vol_ratio_max": {
                "type": "number",
                "title": "S2 量能萎缩阈值",
                "minimum": 0.1,
                "maximum": 2.0,
                "default": 0.6,
            },
            "ret40_top_n": {
                "type": "integer",
                "title": "S3 40日涨幅TopN",
                "minimum": 50,
                "maximum": 2000,
                "default": 500,
            },
            "sideways_range_max": {
                "type": "number",
                "title": "S4 横盘振幅上限",
                "minimum": 0.05,
                "maximum": 0.50,
                "default": 0.18,
            },
            "near_ma20_max": {
                "type": "number",
                "title": "S4 偏离MA20上限",
                "minimum": 0.01,
                "maximum": 0.20,
                "default": 0.06,
            },
            "min_pool_score": {
                "type": "integer",
                "title": "入池最低得分(S1-S4)",
                "minimum": 1,
                "maximum": 4,
                "default": 2,
            },
            "breakout_vol_ratio": {
                "type": "number",
                "title": "S5 突破量比阈值",
                "minimum": 0.5,
                "maximum": 3.0,
                "default": 1.2,
            },
            "max_pullback_days": {
                "type": "integer",
                "title": "S6 最大回调天数",
                "minimum": 0,
                "maximum": 20,
                "default": 3,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 50.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        v5 = StrategyDescriptor(
            strategy_id="matrix_signal_v1",
            name="矩阵信号V1",
            version="1.0.0-alpha",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=True,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=v5_schema,
            default_params={
                "atr_ratio_max": 0.7,
                "vol_ratio_max": 0.6,
                "ret40_top_n": 500,
                "sideways_range_max": 0.18,
                "near_ma20_max": 0.06,
                "min_pool_score": 2,
                "breakout_vol_ratio": 1.2,
                "max_pullback_days": 3,
                "min_score": 50.0,
                "min_event_count": 0,
                "require_sequence": False,
                "health_score_min": 0.0,
                "event_score_min": 0.0,
                "event_grade_min": "C",
                "require_key_event_confirmation": False,
            },
        )
        self._strategies[v5.strategy_id] = v5

        # ── B1 Multi-Timeframe Strategy ──
        b1_schema: dict[str, dict[str, Any]] = {
            "vol_ratio": {
                "type": "number",
                "title": "缩量比例",
                "minimum": 0.1,
                "maximum": 1.5,
                "default": 0.8,
            },
            "chg_limit": {
                "type": "number",
                "title": "涨跌幅上限(%)",
                "minimum": 0.5,
                "maximum": 10.0,
                "default": 3.0,
            },
            "amp_limit_10cm": {
                "type": "number",
                "title": "10cm板振幅上限(%)",
                "minimum": 1.0,
                "maximum": 15.0,
                "default": 5.0,
            },
            "amp_limit_20cm": {
                "type": "number",
                "title": "20cm板振幅上限(%)",
                "minimum": 1.0,
                "maximum": 20.0,
                "default": 8.0,
            },
            "kdj_j_upper": {
                "type": "number",
                "title": "KDJ J值上限",
                "minimum": 10.0,
                "maximum": 90.0,
                "default": 50.0,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 35.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件顺序",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "要求关键事件确认",
                "default": False,
            },
        }
        b1_defaults = {
            "vol_ratio": 0.8,
            "chg_limit": 3.0,
            "amp_limit_10cm": 5.0,
            "amp_limit_20cm": 8.0,
            "kdj_j_upper": 50.0,
            "min_score": 35.0,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        b1 = StrategyDescriptor(
            strategy_id="b1_mtf_v1",
            name="B1战法V1",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(b1_schema),
            default_params=dict(b1_defaults),
        )
        self._strategies[b1.strategy_id] = b1

        # ── trend_king_v1: 趋势为王 ──
        tk_schema: dict[str, dict[str, Any]] = {
            "mode": {
                "type": "enum",
                "title": "选股模式",
                "options": ["all", "a", "b", "c"],
                "default": "all",
            },
            "min_day_gain_a": {
                "type": "number",
                "title": "模式A-涨停日涨幅下限(%)",
                "minimum": 5.0,
                "maximum": 20.0,
                "default": 9.5,
            },
            "min_hist": {
                "type": "number",
                "title": "历史弹性下限",
                "minimum": 10.0,
                "maximum": 100.0,
                "default": 30.0,
            },
            "min_gain_3d_b": {
                "type": "number",
                "title": "模式B-3日涨幅下限(%)",
                "minimum": 3.0,
                "maximum": 20.0,
                "default": 7.0,
            },
            "min_gain_5d_b": {
                "type": "number",
                "title": "模式B-5日涨幅下限(%)",
                "minimum": 5.0,
                "maximum": 30.0,
                "default": 10.0,
            },
            "max_vol_ratio_c": {
                "type": "number",
                "title": "模式C-量比上限",
                "minimum": 0.5,
                "maximum": 3.0,
                "default": 1.5,
            },
            "min_hist_c": {
                "type": "number",
                "title": "模式C-弹性下限",
                "minimum": 20.0,
                "maximum": 100.0,
                "default": 50.0,
            },
            "min_prev_gain_c": {
                "type": "number",
                "title": "模式C-前日涨幅下限(%)",
                "minimum": 0.0,
                "maximum": 10.0,
                "default": 3.0,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        tk_defaults = {
            "mode": "all",
            "min_day_gain_a": 9.5,
            "min_hist": 30.0,
            "min_gain_3d_b": 7.0,
            "min_gain_5d_b": 10.0,
            "max_vol_ratio_c": 1.5,
            "min_hist_c": 50.0,
            "min_prev_gain_c": 3.0,
            "min_score": 0.0,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        tk = StrategyDescriptor(
            strategy_id="trend_king_v1",
            name="趋势为王",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(tk_schema),
            default_params=dict(tk_defaults),
        )
        self._strategies[tk.strategy_id] = tk

        # ── 趋势为王三个子策略 ──
        # Keep "mode" in schema so normalize_params preserves it
        tk_a_schema = dict(tk_schema)
        tk_a_schema["mode"]["readOnly"] = True
        tk_b_schema = dict(tk_schema)
        tk_b_schema["mode"]["readOnly"] = True
        tk_c_schema = dict(tk_schema)
        tk_c_schema["mode"]["readOnly"] = True

        tk_a_defaults = {k: v for k, v in tk_defaults.items() if k != "mode"}
        tk_a_defaults["mode"] = "a"
        tk_b_defaults = {k: v for k, v in tk_defaults.items() if k != "mode"}
        tk_b_defaults["mode"] = "b"
        tk_c_defaults = {k: v for k, v in tk_defaults.items() if k != "mode"}
        tk_c_defaults["mode"] = "c"

        tk_a = StrategyDescriptor(
            strategy_id="trend_king_limitup_v1",
            name="涨停板精选",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(tk_a_schema),
            default_params=dict(tk_a_defaults),
            signal_top_n=8,
        )
        tk_b = StrategyDescriptor(
            strategy_id="trend_king_rally_v1",
            name="涨势确认",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(tk_b_schema),
            default_params=dict(tk_b_defaults),
            signal_top_n=10,
        )
        tk_c = StrategyDescriptor(
            strategy_id="trend_king_pullback_v1",
            name="涨停跟踪池",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(tk_c_schema),
            default_params=dict(tk_c_defaults),
            signal_top_n=0,
        )
        self._strategies[tk_a.strategy_id] = tk_a
        self._strategies[tk_b.strategy_id] = tk_b
        self._strategies[tk_c.strategy_id] = tk_c

        # ── emotion_limit_up_v1: 情绪涨停 ──
        elu_schema: dict[str, dict[str, Any]] = {
            "min_day_gain": {
                "type": "number",
                "title": "涨停日涨幅下限(%)",
                "minimum": 5.0,
                "maximum": 20.0,
                "default": 9.5,
            },
            "lookback_days": {
                "type": "integer",
                "title": "信号回溯窗口(交易日)",
                "minimum": 3,
                "maximum": 10,
                "default": 5,
            },
            "require_next_day_confirm": {
                "type": "boolean",
                "title": "要求次日倍量+没死确认",
                "default": False,
            },
            "next_day_volume_multiple": {
                "type": "number",
                "title": "次日倍量倍数",
                "minimum": 1.0,
                "maximum": 5.0,
                "default": 2.0,
            },
            "max_drawdown_from_limit_pct": {
                "type": "number",
                "title": "相对涨停价最大回撤(%)",
                "minimum": 1.0,
                "maximum": 15.0,
                "default": 5.0,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        elu_defaults = {
            "min_day_gain": 9.5,
            "lookback_days": 5,
            "require_next_day_confirm": False,
            "next_day_volume_multiple": 2.0,
            "max_drawdown_from_limit_pct": 5.0,
            "min_score": 0.0,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        elu = StrategyDescriptor(
            strategy_id="emotion_limit_up_v1",
            name="情绪涨停",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dict(elu_schema),
            default_params=dict(elu_defaults),
            signal_top_n=10,
        )
        self._strategies[elu.strategy_id] = elu

        # ── limit_up_arb_v1: 涨停套利 ──
        lua_schema: dict[str, dict[str, Any]] = {
            "min_day_gain": {
                "type": "number",
                "title": "当日涨幅下限(%)",
                "minimum": 1.0,
                "maximum": 20.0,
                "default": 3.0,
            },
            "min_volume_ratio_prev": {
                "type": "number",
                "title": "相对昨日涨停日放量倍数",
                "minimum": 1.0,
                "maximum": 5.0,
                "default": 1.2,
            },
            "min_volume_ratio_ma5": {
                "type": "number",
                "title": "相对5日均量倍数(0=不启用)",
                "minimum": 0.0,
                "maximum": 5.0,
                "default": 0.0,
            },
            "sector_rank_weight": {
                "type": "number",
                "title": "板块热度排序权重",
                "minimum": 0.0,
                "maximum": 0.3,
                "default": 0.08,
            },
            "min_score": {
                "type": "number",
                "title": "入场质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "min_event_count": {
                "type": "integer",
                "title": "最少事件数",
                "minimum": 0,
                "maximum": 12,
                "default": 0,
            },
            "require_sequence": {
                "type": "boolean",
                "title": "要求事件序列",
                "default": False,
            },
            "health_score_min": {
                "type": "number",
                "title": "健康分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_score_min": {
                "type": "number",
                "title": "事件分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": 0.0,
            },
            "event_grade_min": {
                "type": "enum",
                "title": "事件等级下限",
                "options": ["A", "B", "C"],
                "default": "C",
            },
            "require_key_event_confirmation": {
                "type": "boolean",
                "title": "关键事件确认必需",
                "default": False,
            },
        }
        lua_defaults = {
            "min_day_gain": 3.0,
            "min_volume_ratio_prev": 1.2,
            "min_volume_ratio_ma5": 0.0,
            "sector_rank_weight": 0.08,
            "min_score": 0.0,
            "min_event_count": 0,
            "require_sequence": False,
            "health_score_min": 0.0,
            "event_score_min": 0.0,
            "event_grade_min": "C",
            "require_key_event_confirmation": False,
        }
        lua = StrategyDescriptor(
            strategy_id="limit_up_arb_v1",
            name="涨停套利",
            version="1.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=False,
                supports_entry_delay=False,
            ),
            params_schema=dict(lua_schema),
            default_params=dict(lua_defaults),
            signal_top_n=15,
        )
        self._strategies[lua.strategy_id] = lua

        from .strategy_ai_meta import apply_strategy_ai_meta

        apply_strategy_ai_meta(self._strategies)

        self._plugins = {
            v1.strategy_id: WyckoffTrendPlugin(v1.strategy_id),
            v2.strategy_id: WyckoffTrendPlugin(v2.strategy_id),
            v3.strategy_id: RelativeStrengthBreakoutPlugin(),
            v4.strategy_id: ScoreOnlyRankPlugin(),
            v41.strategy_id: ThsMainRetailSignalPlugin(v41.strategy_id, trigger_key="purple_to_yellow"),
            v42.strategy_id: ThsMainRetailSignalPlugin(v42.strategy_id, trigger_key="golden_cross"),
            force_rhythm.strategy_id: ForceRhythmPlugin(force_rhythm.strategy_id),
            v43.strategy_id: WulongClusterPlugin(v43.strategy_id),
            v5.strategy_id: MatrixSignalPlugin(),
            b1.strategy_id: B1MultiTimeframePlugin(b1.strategy_id),
            tk.strategy_id: TrendKingPlugin(tk.strategy_id),
            tk_a.strategy_id: TrendKingPlugin(tk_a.strategy_id),
            tk_b.strategy_id: TrendKingPlugin(tk_b.strategy_id),
            tk_c.strategy_id: TrendKingPlugin(tk_c.strategy_id),
            elu.strategy_id: EmotionLimitUpPlugin(elu.strategy_id),
            lua.strategy_id: LimitUpArbPlugin(lua.strategy_id),
        }
        for strategy_id in self._strategies:
            plugin = self._plugins.get(strategy_id)
            if plugin is None or plugin.strategy_id != strategy_id:
                raise RuntimeError(f"策略实现未正确注册：{strategy_id}")

    @property
    def default_strategy_id(self) -> str:
        for item in self._strategies.values():
            if bool(item.is_default):
                return str(item.strategy_id)
        if self._DEFAULT_STRATEGY_ID in self._strategies:
            return self._DEFAULT_STRATEGY_ID
        if self._strategies:
            return next(iter(self._strategies.keys()))
        return self._DEFAULT_STRATEGY_ID

    def normalize_strategy_id(self, raw_strategy_id: str | None) -> str:
        text = str(raw_strategy_id or "").strip()
        if not text:
            return self._DEFAULT_STRATEGY_ID
        return text

    def get(self, strategy_id: str) -> StrategyDescriptor | None:
        return self._strategies.get(str(strategy_id).strip())

    def list(self) -> list[StrategyDescriptor]:
        return list(self._strategies.values())

    def update_descriptor(
        self,
        *,
        strategy_id: str,
        enabled: bool | None = None,
        is_default: bool | None = None,
        version: str | None = None,
    ) -> StrategyDescriptor:
        target_id = str(strategy_id).strip()
        descriptor = self.get(target_id)
        if descriptor is None:
            available = ",".join(item.strategy_id for item in self.list())
            raise ValueError(f"策略不存在: {target_id}（可用: {available}）")

        updated = descriptor
        if enabled is not None:
            updated = replace(updated, enabled=bool(enabled))
        if version is not None:
            normalized_version = str(version).strip()
            if normalized_version:
                updated = replace(updated, version=normalized_version)

        # Apply non-default updates first.
        self._strategies[target_id] = updated

        if is_default is not None:
            if bool(is_default):
                for item_id, item in list(self._strategies.items()):
                    self._strategies[item_id] = replace(item, is_default=(item_id == target_id))
            else:
                self._strategies[target_id] = replace(self._strategies[target_id], is_default=False)
                if not any(bool(item.is_default) for item in self._strategies.values()):
                    fallback_id = self._DEFAULT_STRATEGY_ID if self._DEFAULT_STRATEGY_ID in self._strategies else target_id
                    fallback = self._strategies[fallback_id]
                    self._strategies[fallback_id] = replace(fallback, is_default=True)

        return self._strategies[target_id]

    def _get_plugin(self, strategy_id: str) -> StrategyPlugin:
        text = str(strategy_id).strip()
        if text not in self._plugins:
            raise ValueError(f"策略实现未注册：{text}")
        return self._plugins[text]

    @staticmethod
    def _clamp_number(value: Any, *, minimum: float | None, maximum: float | None) -> float | None:
        try:
            parsed = float(value)
        except Exception:
            return None
        if minimum is not None and parsed < float(minimum):
            parsed = float(minimum)
        if maximum is not None and parsed > float(maximum):
            parsed = float(maximum)
        return parsed

    @staticmethod
    def _clamp_integer(value: Any, *, minimum: int | None, maximum: int | None) -> int | None:
        try:
            parsed = int(value)
        except Exception:
            return None
        if minimum is not None and parsed < int(minimum):
            parsed = int(minimum)
        if maximum is not None and parsed > int(maximum):
            parsed = int(maximum)
        return parsed

    def normalize_params(self, strategy_id: str, raw_params: dict[str, Any] | None) -> dict[str, Any]:
        descriptor = self.get(strategy_id)
        if descriptor is None:
            return {}
        params = raw_params if isinstance(raw_params, dict) else {}
        normalized: dict[str, Any] = {}
        for key, spec in descriptor.params_schema.items():
            if key not in params:
                continue
            value = params.get(key)
            type_name = str(spec.get("type") or "").strip().lower()
            if type_name == "number":
                parsed = self._clamp_number(
                    value,
                    minimum=float(spec["minimum"]) if "minimum" in spec else None,
                    maximum=float(spec["maximum"]) if "maximum" in spec else None,
                )
                if parsed is not None:
                    normalized[key] = float(parsed)
                continue
            if type_name == "integer":
                parsed = self._clamp_integer(
                    value,
                    minimum=int(spec["minimum"]) if "minimum" in spec else None,
                    maximum=int(spec["maximum"]) if "maximum" in spec else None,
                )
                if parsed is not None:
                    normalized[key] = int(parsed)
                continue
            if type_name == "boolean":
                if isinstance(value, bool):
                    normalized[key] = value
                    continue
                value_text = str(value).strip().lower()
                if value_text in {"1", "true", "yes", "y", "on"}:
                    normalized[key] = True
                elif value_text in {"0", "false", "no", "n", "off"}:
                    normalized[key] = False
                continue
            if type_name == "enum":
                options = [str(item) for item in spec.get("options", [])]
                value_text = str(value).strip()
                if value_text in options:
                    normalized[key] = value_text
                continue
        return normalized

    def resolve_backtest_overrides(self, strategy_id: str, normalized_params: dict[str, Any]) -> dict[str, Any]:
        _ = strategy_id
        allowed = {
            "matrix_event_semantic_version",
            "rank_weight_health",
            "rank_weight_event",
            "health_score_min",
            "event_score_min",
            "event_grade_min",
            "require_key_event_confirmation",
            "min_score",
            "min_event_count",
            "require_sequence",
            "atr_ratio_max",
            "vol_ratio_max",
            "ret40_top_n",
            "sideways_range_max",
            "near_ma20_max",
            "min_pool_score",
            "breakout_vol_ratio",
            "max_pullback_days",
            "vol_ratio",
            "chg_limit",
            "amp_limit_10cm",
            "amp_limit_20cm",
            "kdj_j_upper",
            "min_day_gain_a",
            "min_hist",
            "min_gain_3d_b",
            "min_gain_5d_b",
            "max_vol_ratio_c",
            "min_hist_c",
            "min_prev_gain_c",
        }
        return {
            key: value
            for key, value in normalized_params.items()
            if key in allowed
        }

    def resolve_signal_overrides(self, strategy_id: str, normalized_params: dict[str, Any]) -> dict[str, Any]:
        _ = strategy_id
        allowed = {
            "min_score",
            "min_event_count",
            "require_sequence",
            "health_score_min",
            "event_score_min",
            "event_grade_min",
            "require_key_event_confirmation",
            "atr_ratio_max",
            "vol_ratio_max",
            "ret40_top_n",
            "sideways_range_max",
            "near_ma20_max",
            "min_pool_score",
            "breakout_vol_ratio",
            "max_pullback_days",
            "vol_ratio",
            "chg_limit",
            "amp_limit_10cm",
            "amp_limit_20cm",
            "kdj_j_upper",
            "mode",
            "min_day_gain_a",
            "min_hist",
            "min_gain_3d_b",
            "min_gain_5d_b",
            "max_vol_ratio_c",
            "min_hist_c",
            "min_prev_gain_c",
        }
        return {
            key: value
            for key, value in normalized_params.items()
            if key in allowed
        }

    def build_universe(
        self,
        *,
        strategy_id: str,
        candidates: list[Any],
        params: dict[str, Any],
        mode: str,
    ) -> list[Any]:
        plugin = self._get_plugin(strategy_id)
        return plugin.build_universe(
            candidates=candidates,
            params=params,
            mode=mode,
        )

    def generate_signals(
        self,
        *,
        strategy_id: str,
        row: Any,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        plugin = self._get_plugin(strategy_id)
        return bool(
            plugin.generate_signals(
                row=row,
                snapshot=snapshot,
                params=params,
            )
        )

    def rank_signals(
        self,
        *,
        strategy_id: str,
        signal: Any,
        row: Any,
        params: dict[str, Any],
        fallback_score: float,
    ) -> float:
        plugin = self._get_plugin(strategy_id)
        return float(
            plugin.rank_signals(
                signal=signal,
                row=row,
                params=params,
                fallback_score=fallback_score,
            )
        )

    def entry_policy(
        self,
        *,
        strategy_id: str,
        payload: Any,
        params: dict[str, Any],
    ) -> Any:
        plugin = self._get_plugin(strategy_id)
        return plugin.entry_policy(payload=payload, params=params)

    def exit_policy(
        self,
        *,
        strategy_id: str,
        payload: Any,
        params: dict[str, Any],
    ) -> Any:
        plugin = self._get_plugin(strategy_id)
        return plugin.exit_policy(payload=payload, params=params)

    @staticmethod
    def params_hash(normalized_params: dict[str, Any]) -> str:
        raw = json.dumps(normalized_params, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
