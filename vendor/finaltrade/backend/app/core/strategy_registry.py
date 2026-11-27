from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from .strategy_plugins import (
    BaseStrategyPlugin,
    MasterAlphaStrategyPlugin,
    StrategyPlugin,
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
    """Purified 7-strategy production registry (14 redundant/broken/losing legacy strategies removed)."""

    _DEFAULT_STRATEGY_ID = "wujiang_mainline_v1"

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyDescriptor] = {}
        self._plugins: dict[str, StrategyPlugin] = {}
        self._fallback_plugin: StrategyPlugin = BaseStrategyPlugin()
        self._register_builtin()

    @staticmethod
    def _master_discipline_schema(*, default_add_on_win: bool = False) -> dict[str, dict[str, Any]]:
        return {
            "use_regime_switch": {
                "type": "boolean",
                "title": "四阶段情绪择时压仓",
                "default": True,
            },
            "use_fast_weak_cut": {
                "type": "boolean",
                "title": "炸板/破MA5早盘快砍",
                "default": True,
            },
            "use_add_on_win": {
                "type": "boolean",
                "title": "封板确认次日浮盈加仓",
                "default": default_add_on_win,
            },
            "use_partial_trim": {
                "type": "boolean",
                "title": "冲高回落减半锁利(50%)",
                "default": True,
            },
        }

    @staticmethod
    def _common_gate_schema(default_min_score: float = 58.0) -> dict[str, dict[str, Any]]:
        return {
            "min_score": {
                "type": "number",
                "title": "信号质量分下限",
                "minimum": 0.0,
                "maximum": 100.0,
                "default": default_min_score,
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

    def _register_builtin(self) -> None:
        # 1. 乌江双轨主线核心 (Default)
        wj_schema: dict[str, dict[str, Any]] = {
            "track_mode": {
                "type": "enum",
                "title": "赛道轨道模式",
                "options": ["dual", "growth_20cm", "main_10cm"],
                "default": "dual",
            },
            "min_amount_yi": {
                "type": "number",
                "title": "最低成交额(亿元)",
                "minimum": 0.5,
                "maximum": 50.0,
                "default": 2.5,
            },
            "min_vol_ratio_20": {
                "type": "number",
                "title": "20日均量比下限",
                "minimum": 0.8,
                "maximum": 5.0,
                "default": 1.35,
            },
            "max_dist_ma20_pct": {
                "type": "number",
                "title": "相对MA20最大偏离(%)",
                "minimum": 4.0,
                "maximum": 35.0,
                "default": 16.0,
            },
            "min_close_strength": {
                "type": "number",
                "title": "收盘强度下限",
                "minimum": 0.4,
                "maximum": 1.0,
                "default": 0.80,
            },
            **self._master_discipline_schema(default_add_on_win=True),
            **self._common_gate_schema(62.0),
        }
        wj = StrategyDescriptor(
            strategy_id="wujiang_mainline_v1",
            name="乌江望月 · 双轨主线核心",
            version="2.1.0",
            enabled=True,
            is_default=True,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=wj_schema,
            default_params={k: v["default"] for k, v in wj_schema.items()},
            signal_top_n=10,
        )
        self._strategies[wj.strategy_id] = wj

        # 2. 灰常懒 · 主线半路秒砍
        hw_schema: dict[str, dict[str, Any]] = {
            "min_day_gain_pct": {
                "type": "number",
                "title": "当日涨幅下限(%)",
                "minimum": 0.5,
                "maximum": 8.0,
                "default": 2.5,
            },
            "max_day_gain_pct": {
                "type": "number",
                "title": "当日涨幅上限(%)",
                "minimum": 3.0,
                "maximum": 12.0,
                "default": 6.5,
            },
            "min_vol_ratio_20": {
                "type": "number",
                "title": "20日均量比下限",
                "minimum": 0.8,
                "maximum": 4.0,
                "default": 1.35,
            },
            "min_up_down_vol_ratio": {
                "type": "number",
                "title": "涨跌量能比下限",
                "minimum": 0.9,
                "maximum": 3.0,
                "default": 1.25,
            },
            "max_dist_ma20_pct": {
                "type": "number",
                "title": "相对MA20最大偏离(%)",
                "minimum": 3.0,
                "maximum": 20.0,
                "default": 9.5,
            },
            "min_ret20_pct": {
                "type": "number",
                "title": "20日涨幅下限(%)",
                "minimum": 0.0,
                "maximum": 30.0,
                "default": 2.0,
            },
            **self._master_discipline_schema(default_add_on_win=False),
            **self._common_gate_schema(60.0),
        }
        hw = StrategyDescriptor(
            strategy_id="mainline_halfway_v1",
            name="灰常懒 · 主线半路秒砍",
            version="2.1.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=hw_schema,
            default_params={k: v["default"] for k, v in hw_schema.items()},
            signal_top_n=10,
        )
        self._strategies[hw.strategy_id] = hw

        # 3. 希望姐姐 · 纯主板一进二接力
        f2s_schema: dict[str, dict[str, Any]] = {
            "min_amount_yi": {
                "type": "number",
                "title": "最低成交额(亿元)",
                "minimum": 0.5,
                "maximum": 30.0,
                "default": 1.5,
            },
            "min_vol_ratio_prev": {
                "type": "number",
                "title": "相对昨日量比下限",
                "minimum": 0.4,
                "maximum": 2.0,
                "default": 0.8,
            },
            "max_vol_ratio_prev": {
                "type": "number",
                "title": "相对昨日量比上限",
                "minimum": 1.5,
                "maximum": 6.0,
                "default": 2.8,
            },
            "require_above_ma20": {
                "type": "boolean",
                "title": "要求站上MA20",
                "default": True,
            },
            "exclude_one_word": {
                "type": "boolean",
                "title": "剔除一字无量板",
                "default": True,
            },
            **self._master_discipline_schema(default_add_on_win=False),
            **self._common_gate_schema(58.0),
        }
        f2s = StrategyDescriptor(
            strategy_id="first_to_second_v1",
            name="希望姐姐 · 纯主板一进二",
            version="2.1.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=f2s_schema,
            default_params={k: v["default"] for k, v in f2s_schema.items()},
            signal_top_n=8,
        )
        self._strategies[f2s.strategy_id] = f2s

        # 4. 暴涨基金董事长 · 主板高标狙击
        hb_schema: dict[str, dict[str, Any]] = {
            "min_board_height": {
                "type": "integer",
                "title": "最低连板高度(板)",
                "minimum": 2,
                "maximum": 7,
                "default": 2,
            },
            "min_amount_yi": {
                "type": "number",
                "title": "最低成交额(亿元)",
                "minimum": 0.5,
                "maximum": 30.0,
                "default": 1.8,
            },
            "max_vol_ratio_prev": {
                "type": "number",
                "title": "相对昨日量比上限",
                "minimum": 1.2,
                "maximum": 6.0,
                "default": 3.2,
            },
            "require_above_ma20": {
                "type": "boolean",
                "title": "要求站上MA20",
                "default": True,
            },
            "exclude_one_word": {
                "type": "boolean",
                "title": "剔除一字板",
                "default": False,
            },
            **self._master_discipline_schema(default_add_on_win=False),
            **self._common_gate_schema(65.0),
        }
        hb = StrategyDescriptor(
            strategy_id="highboard_sniper_v1",
            name="暴涨董事长 · 主板高标狙击",
            version="2.1.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=hb_schema,
            default_params={k: v["default"] for k, v in hb_schema.items()},
            signal_top_n=6,
        )
        self._strategies[hb.strategy_id] = hb

        # 5. 如晗 · 分仓半路推土机
        rh_schema: dict[str, dict[str, Any]] = {
            "min_day_gain_pct": {
                "type": "number",
                "title": "当日涨幅下限(%)",
                "minimum": 1.0,
                "maximum": 8.0,
                "default": 3.0,
            },
            "max_day_gain_pct": {
                "type": "number",
                "title": "当日涨幅上限(%)",
                "minimum": 4.0,
                "maximum": 12.0,
                "default": 9.6,
            },
            "min_amount_yi": {
                "type": "number",
                "title": "最低成交额(亿元)",
                "minimum": 0.8,
                "maximum": 30.0,
                "default": 2.5,
            },
            "min_vol_ratio_5": {
                "type": "number",
                "title": "5日均量比下限",
                "minimum": 0.8,
                "maximum": 4.0,
                "default": 1.20,
            },
            "min_ret5_pct": {
                "type": "number",
                "title": "5日涨幅下限(%)",
                "minimum": -5.0,
                "maximum": 25.0,
                "default": 2.0,
            },
            **self._master_discipline_schema(default_add_on_win=True),
            **self._common_gate_schema(58.0),
        }
        rh = StrategyDescriptor(
            strategy_id="ruhan_halfway_v1",
            name="如晗 · 分仓半路推土机",
            version="2.1.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=rh_schema,
            default_params={k: v["default"] for k, v in rh_schema.items()},
            signal_top_n=12,
        )
        self._strategies[rh.strategy_id] = rh

        # 6. 松雅湖的松松 · 强趋势低吸防守
        dip_schema: dict[str, dict[str, Any]] = {
            "min_ret20_pct": {
                "type": "number",
                "title": "20日趋势涨幅下限(%)",
                "minimum": 3.0,
                "maximum": 40.0,
                "default": 8.0,
            },
            "min_drawdown20": {
                "type": "number",
                "title": "距20日高点最小回撤",
                "minimum": 0.01,
                "maximum": 0.15,
                "default": 0.04,
            },
            "max_drawdown20": {
                "type": "number",
                "title": "距20日高点最大回撤",
                "minimum": 0.05,
                "maximum": 0.30,
                "default": 0.14,
            },
            "max_vol_ratio_20": {
                "type": "number",
                "title": "缩量上限(相对20日均量)",
                "minimum": 0.4,
                "maximum": 1.2,
                "default": 0.85,
            },
            "max_dist_ma20_pct": {
                "type": "number",
                "title": "距MA20最大偏离(%)",
                "minimum": 1.0,
                "maximum": 15.0,
                "default": 6.0,
            },
            "min_up_down_vol_ratio": {
                "type": "number",
                "title": "涨跌量能比下限",
                "minimum": 1.0,
                "maximum": 3.0,
                "default": 1.25,
            },
            **self._master_discipline_schema(default_add_on_win=False),
            **self._common_gate_schema(58.0),
        }
        dip = StrategyDescriptor(
            strategy_id="strong_trend_dip_v1",
            name="松松 · 强趋势低吸防守",
            version="2.1.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=dip_schema,
            default_params={k: v["default"] for k, v in dip_schema.items()},
            signal_top_n=10,
        )
        self._strategies[dip.strategy_id] = dip

        # 5. VCP 三段收缩突破
        vcp_schema: dict[str, dict[str, Any]] = {
            "max_base_range": {
                "type": "number",
                "title": "末段收敛振幅上限",
                "minimum": 0.04,
                "maximum": 0.25,
                "default": 0.12,
            },
            "max_vol_shrink": {
                "type": "number",
                "title": "末段缩量比上限",
                "minimum": 0.4,
                "maximum": 1.2,
                "default": 0.85,
            },
            "min_breakout_vol_ratio": {
                "type": "number",
                "title": "突破放量倍数下限",
                "minimum": 1.0,
                "maximum": 4.0,
                "default": 1.30,
            },
            "min_close_strength": {
                "type": "number",
                "title": "突破收盘强度下限",
                "minimum": 0.4,
                "maximum": 1.0,
                "default": 0.68,
            },
            **self._common_gate_schema(58.0),
        }
        vcp = StrategyDescriptor(
            strategy_id="vcp_contraction_v1",
            name="VCP三段收缩突破",
            version="2.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=vcp_schema,
            default_params={k: v["default"] for k, v in vcp_schema.items()},
            signal_top_n=8,
        )
        self._strategies[vcp.strategy_id] = vcp

        # 6. 五龙聚首 (Retained strongest legacy convergence-breakout strategy)
        wulong_schema: dict[str, dict[str, Any]] = {
            "min_ret40": {"type": "number", "title": "40日涨幅下限", "minimum": 0.0, "maximum": 1.2, "default": 0.06},
            "max_retrace20": {"type": "number", "title": "20日回撤上限", "minimum": 0.01, "maximum": 0.5, "default": 0.18},
            "min_up_down_volume_ratio": {"type": "number", "title": "上涨量能比下限", "minimum": 0.8, "maximum": 3.0, "default": 1.08},
            "min_vol_slope20": {"type": "number", "title": "20日量能斜率下限", "minimum": -0.5, "maximum": 0.5, "default": 0.0},
            "min_ma10_above_ma20_days": {"type": "integer", "title": "MA10站上MA20最少天数", "minimum": 0, "maximum": 30, "default": 4},
            "min_ma5_above_ma10_days": {"type": "integer", "title": "MA5站上MA10最少天数", "minimum": 0, "maximum": 30, "default": 3},
            "convergence_threshold_pct": {"type": "number", "title": "均线粘合阈值", "minimum": 0.002, "maximum": 0.05, "default": 0.04},
            "pre_convergence_min_spread_pct": {"type": "number", "title": "粘合前最小分散度", "minimum": 0.005, "maximum": 0.15, "default": 0.02},
            "min_current_spread_pct": {"type": "number", "title": "发散后最小均线带宽", "minimum": 0.003, "maximum": 0.08, "default": 0.01},
            "min_spread_expansion_multiple": {"type": "number", "title": "带宽扩张倍数下限", "minimum": 1.0, "maximum": 6.0, "default": 1.6},
            "min_volume_ratio20": {"type": "number", "title": "放量倍数下限(相对20日均量)", "minimum": 0.8, "maximum": 4.0, "default": 1.2},
            "min_breakout_return_pct": {"type": "number", "title": "突破涨幅下限", "minimum": 0.0, "maximum": 0.15, "default": 0.006},
            "max_convergence_age_days": {"type": "integer", "title": "允许的最近粘合天数", "minimum": 1, "maximum": 20, "default": 10},
            "min_rising_ma_count": {"type": "integer", "title": "向上拐头均线最少条数", "minimum": 1, "maximum": 5, "default": 3},
            "allow_upper_shadow_risk": {"type": "boolean", "title": "允许上影线风险", "default": False},
            "allow_blowoff_top": {"type": "boolean", "title": "允许高潮尖顶", "default": False},
            **self._common_gate_schema(52.0),
        }
        wulong = StrategyDescriptor(
            strategy_id="wulong_cluster_v1",
            name="五龙聚首",
            version="2.0.0",
            enabled=True,
            is_default=False,
            capabilities=StrategyCapabilities(
                supports_matrix=False,
                supports_signal_age_filter=True,
                supports_entry_delay=True,
            ),
            params_schema=wulong_schema,
            default_params={k: v["default"] for k, v in wulong_schema.items()},
        )
        self._strategies[wulong.strategy_id] = wulong

        # 7. 维科夫趋势 V2 (Retained structural accumulation + matrix engine support)
        v2_schema: dict[str, dict[str, Any]] = {
            "matrix_event_semantic_version": {
                "type": "enum",
                "title": "矩阵语义版本",
                "options": ["matrix_v1", "aligned_wyckoff_v2"],
                "default": "aligned_wyckoff_v2",
            },
            "rank_weight_health": {"type": "number", "title": "健康分权重", "minimum": 0.0, "maximum": 1.0, "default": 0.5},
            "rank_weight_event": {"type": "number", "title": "事件分权重", "minimum": 0.0, "maximum": 1.0, "default": 0.5},
            "health_score_min": {"type": "number", "title": "健康分下限", "minimum": 0.0, "maximum": 100.0, "default": 55.0},
            "event_score_min": {"type": "number", "title": "事件分下限", "minimum": 0.0, "maximum": 100.0, "default": 55.0},
            "event_grade_min": {"type": "enum", "title": "事件等级下限", "options": ["A", "B", "C"], "default": "B"},
            "require_key_event_confirmation": {"type": "boolean", "title": "关键事件确认必需", "default": True},
            "min_score": {"type": "number", "title": "入场质量分下限", "minimum": 0.0, "maximum": 100.0, "default": 60.0},
            "min_event_count": {"type": "integer", "title": "最少事件数", "minimum": 0, "maximum": 12, "default": 1},
            "require_sequence": {"type": "boolean", "title": "要求事件序列", "default": False},
        }
        v2 = StrategyDescriptor(
            strategy_id="wyckoff_trend_v2",
            name="维科夫趋势V2",
            version="2.0.0",
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
        self._strategies[v2.strategy_id] = v2

        from .strategy_ai_meta import apply_strategy_ai_meta

        apply_strategy_ai_meta(self._strategies)

        self._plugins = {
            wj.strategy_id: MasterAlphaStrategyPlugin(wj.strategy_id),
            hw.strategy_id: MasterAlphaStrategyPlugin(hw.strategy_id),
            f2s.strategy_id: MasterAlphaStrategyPlugin(f2s.strategy_id),
            hb.strategy_id: MasterAlphaStrategyPlugin(hb.strategy_id),
            rh.strategy_id: MasterAlphaStrategyPlugin(rh.strategy_id),
            dip.strategy_id: MasterAlphaStrategyPlugin(dip.strategy_id),
            vcp.strategy_id: MasterAlphaStrategyPlugin(vcp.strategy_id),
            wulong.strategy_id: WulongClusterPlugin(wulong.strategy_id),
            v2.strategy_id: WyckoffTrendPlugin(v2.strategy_id),
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
            "track_mode",
            "min_amount_yi",
            "min_vol_ratio_20",
            "max_dist_ma20_pct",
            "min_close_strength",
            "min_day_gain_pct",
            "max_day_gain_pct",
            "min_up_down_vol_ratio",
            "min_ret20_pct",
            "min_vol_ratio_prev",
            "max_vol_ratio_prev",
            "require_above_ma20",
            "exclude_one_word",
            "min_drawdown20",
            "max_drawdown20",
            "max_vol_ratio_20",
            "max_base_range",
            "max_vol_shrink",
            "min_breakout_vol_ratio",
            "min_board_height",
            "min_vol_ratio_5",
            "min_ret5_pct",
            "use_regime_switch",
            "use_fast_weak_cut",
            "use_add_on_win",
            "use_partial_trim",
        }
        return {key: value for key, value in normalized_params.items() if key in allowed}

    def resolve_signal_overrides(self, strategy_id: str, normalized_params: dict[str, Any]) -> dict[str, Any]:
        return self.resolve_backtest_overrides(strategy_id, normalized_params)

    def build_universe(
        self,
        *,
        strategy_id: str,
        candidates: list[Any],
        params: dict[str, Any],
        mode: str,
    ) -> list[Any]:
        plugin = self._get_plugin(strategy_id)
        return plugin.build_universe(candidates=candidates, params=params, mode=mode)

    def generate_signals(
        self,
        *,
        strategy_id: str,
        row: Any,
        snapshot: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        plugin = self._get_plugin(strategy_id)
        return bool(plugin.generate_signals(row=row, snapshot=snapshot, params=params))

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
