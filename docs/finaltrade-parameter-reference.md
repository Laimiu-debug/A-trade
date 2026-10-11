# FINAL TRADE 策略参数对照表

本附录逐项保存注册 schema 的标题、范围、声明默认与 descriptor 默认。它是代码理解报告的附件，不表示所有参数都已正确接入实际计算。正式后端先合并 descriptor 默认和用户覆盖；前端还会补充 schema 默认，所以两列不同可能导致 UI 与裸 API 请求行为不同。

审阅日期：2026-10-11。来源：`H:\CODE\FINAL-TRADE\backend\app\core\strategy_registry.py`。源文件 SHA256：`e0fbf337b191942d3f4a650ab3d053c2dbbb25e2808ac7b11c36d7a2538d2b97`。

[返回代码理解报告](/H:/CODE/A-trade/docs/finaltrade-code-understanding.md)

比例和百分点以字段实际代码语义为准；本表不把它们擅自统一转换。`未设`指descriptor未提供该键，不是数值零。

## 维科夫趋势V1

`wyckoff_trend_v1`；实际插件：`WyckoffTrendPlugin`；信号输出上限：0（0表示此处不另截断）。

无策略专属 schema，继承请求级参数。

## 维科夫趋势V2

`wyckoff_trend_v2`；实际插件：`WyckoffTrendPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `matrix_event_semantic_version` | 矩阵语义版本 | enum ["matrix_v1","aligned_wyckoff_v2"] | "aligned_wyckoff_v2" | "aligned_wyckoff_v2" |
| `rank_weight_health` | 健康分权重 | number 0.0 .. 1.0 | 0.5 | 0.5 |
| `rank_weight_event` | 事件分权重 | number 0.0 .. 1.0 | 0.5 | 0.5 |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 55.0 | 55.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 55.0 | 55.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "B" | "B" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | true | true |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 60.0 | 未设 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 1 | 未设 |
| `require_sequence` | 要求事件序列 | boolean  | false | 未设 |

## 相对强弱突破V1

`relative_strength_breakout_v1`；实际插件：`RelativeStrengthBreakoutPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_ret40` | 40日涨幅下限 | number 0.0 .. 1.5 | 0.12 | 0.12 |
| `max_retrace20` | 20日回撤上限 | number 0.01 .. 0.6 | 0.22 | 0.22 |
| `min_up_down_volume_ratio` | 上下跌量比下限 | number 0.8 .. 3.0 | 1.15 | 1.15 |
| `min_vol_slope20` | 20日量能斜率下限 | number -0.5 .. 0.5 | 0.02 | 0.02 |
| `min_ai_confidence` | AI置信度下限 | number 0.0 .. 1.0 | 0.0 | 0.0 |
| `rank_weight_health` | 健康分权重 | number 0.0 .. 1.0 | 0.25 | 0.25 |
| `rank_weight_event` | 事件分权重 | number 0.0 .. 1.0 | 0.25 | 0.25 |
| `rank_weight_strength` | 强势分权重 | number 0.0 .. 1.0 | 0.3 | 0.3 |
| `rank_weight_volume` | 量能分权重 | number 0.0 .. 1.0 | 0.1 | 0.1 |
| `rank_weight_structure` | 结构分权重 | number 0.0 .. 1.0 | 0.1 | 0.1 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 52.0 | 52.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 45.0 | 45.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 45.0 | 45.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## ScoreOnlyRank V1

`score_only_rank_v1`；实际插件：`ScoreOnlyRankPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_score` | Score threshold | number 0.0 .. 100.0 | 60.0 | 60.0 |
| `min_event_count` | Minimum event count | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | Require event sequence | boolean  | false | false |
| `health_score_min` | Health score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | Event score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | Event grade min | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | Require key event confirmation | boolean  | false | false |

## 主力紫转黄V1

`ths_main_force_flip_v1`；实际插件：`ThsMainRetailSignalPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_score` | Signal score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | Minimum event count | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | Require event sequence | boolean  | false | false |
| `health_score_min` | Health score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | Event score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | Event grade min | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | Require key event confirmation | boolean  | false | false |

## 主力金叉V1

`ths_main_force_golden_cross_v1`；实际插件：`ThsMainRetailSignalPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_score` | Signal score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | Minimum event count | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | Require event sequence | boolean  | false | false |
| `health_score_min` | Health score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | Event score min | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | Event grade min | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | Require key event confirmation | boolean  | false | false |

## 主散节奏波V1

`ths_force_rhythm_v1`；实际插件：`BaseStrategyPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_cycle_count` | 最少完整周期数 | integer 2 .. 12 | 3 | 3 |
| `max_cycle_cv` | 周期长度变异系数上限 | number 0.05 .. 1.5 | 0.55 | 0.55 |
| `max_amplitude_cv` | 波幅变异系数上限 | number 0.05 .. 1.5 | 0.85 | 0.85 |
| `min_autocorr` | 周期自相关下限 | number 0.0 .. 1.0 | 0.05 | 0.05 |
| `min_wave_swing_ratio` | 主力波幅/均值下限 | number 0.01 .. 2.0 | 0.12 | 0.12 |
| `trough_percentile_max` | 主力波谷分位上限 | number 0.05 .. 0.8 | 0.4 | 0.4 |
| `peak_percentile_min` | 主力波峰分位下限(卖出) | number 0.4 .. 0.95 | 0.65 | 0.65 |
| `retail_high_percentile_min` | 散户高位分位(卖出) | number 0.4 .. 0.95 | 0.6 | 0.6 |
| `require_trough_turn` | 要求主力波谷拐头 | boolean  | true | true |
| `block_buy_on_retail_sell` | 散户卖出时禁止买入 | boolean  | true | true |
| `peak_reject_percentile_min` | 波峰分位拒绝阈值 | number 0.5 .. 0.95 | 0.72 | 0.72 |
| `flip_trough_percentile_min` | 紫转黄翻转带下限 | number 0.2 .. 0.7 | 0.42 | 0.42 |
| `flip_trough_percentile_max` | 紫转黄翻转带上限 | number 0.3 .. 0.8 | 0.55 | 0.55 |
| `trough_turn_percentile_min` | 波谷拐头分位下限 | number 0.05 .. 0.5 | 0.17 | 0.17 |
| `trough_turn_percentile_max` | 波谷拐头分位上限 | number 0.1 .. 0.6 | 0.3 | 0.3 |
| `deep_trough_percentile_max` | 极深波谷分位上限 | number 0.005 .. 0.08 | 0.015 | 0.015 |
| `deep_trough_streak_target` | 极深波谷连续天数 | integer 1 .. 5 | 2 | 2 |
| `flip_follow_bars_ago` | 翻转跟随延迟(交易日) | integer 1 .. 5 | 2 | 2 |
| `flip_follow_max_trough_percentile` | 翻转日波谷分位上限 | number 0.05 .. 0.5 | 0.35 | 0.35 |
| `flip_follow_min_trough_percentile` | 翻转日波谷分位下限 | number 0.05 .. 0.5 | 0.17 | 0.17 |
| `retail_block_min_trough_percentile` | 散户过滤波谷分位阈值 | number 0.05 .. 0.5 | 0.22 | 0.22 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件顺序 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 五龙聚首

`wulong_cluster_v1`；实际插件：`WulongClusterPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_ret40` | 40日涨幅下限 | number 0.0 .. 1.2 | 0.06 | 0.06 |
| `max_retrace20` | 20日回撤上限 | number 0.01 .. 0.5 | 0.18 | 0.18 |
| `min_up_down_volume_ratio` | 上涨量能比下限 | number 0.8 .. 3.0 | 1.08 | 1.08 |
| `min_vol_slope20` | 20日量能斜率下限 | number -0.5 .. 0.5 | 0.0 | 0.0 |
| `min_ma10_above_ma20_days` | MA10站上MA20最少天数 | integer 0 .. 30 | 4 | 4 |
| `min_ma5_above_ma10_days` | MA5站上MA10最少天数 | integer 0 .. 30 | 3 | 3 |
| `convergence_threshold_pct` | 均线粘合阈值 | number 0.002 .. 0.05 | 0.04 | 0.04 |
| `pre_convergence_min_spread_pct` | 粘合前最小分散度 | number 0.005 .. 0.15 | 0.02 | 0.02 |
| `min_current_spread_pct` | 发散后最小均线带宽 | number 0.003 .. 0.08 | 0.01 | 0.01 |
| `min_spread_expansion_multiple` | 带宽扩张倍数下限 | number 1.0 .. 6.0 | 1.6 | 1.6 |
| `min_volume_ratio20` | 放量倍数下限(相对20日均量) | number 0.8 .. 4.0 | 1.2 | 1.2 |
| `min_breakout_return_pct` | 突破涨幅下限 | number 0.0 .. 0.15 | 0.006 | 0.006 |
| `max_convergence_age_days` | 允许的最近粘合天数 | integer 1 .. 20 | 10 | 10 |
| `min_rising_ma_count` | 向上拐头均线最少条数 | integer 1 .. 5 | 3 | 3 |
| `allow_upper_shadow_risk` | 允许上影线风险 | boolean  | false | false |
| `allow_blowoff_top` | 允许高潮尖顶 | boolean  | false | false |
| `min_score` | 信号质量分下限 | number 0.0 .. 100.0 | 52.0 | 52.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件顺序 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 要求关键事件确认 | boolean  | false | false |

## 矩阵信号V1

`matrix_signal_v1`；实际插件：`MatrixSignalPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `atr_ratio_max` | S1 波动收敛阈值 | number 0.1 .. 2.0 | 0.7 | 0.7 |
| `vol_ratio_max` | S2 量能萎缩阈值 | number 0.1 .. 2.0 | 0.6 | 0.6 |
| `ret40_top_n` | S3 40日涨幅TopN | integer 50 .. 2000 | 500 | 500 |
| `sideways_range_max` | S4 横盘振幅上限 | number 0.05 .. 0.5 | 0.18 | 0.18 |
| `near_ma20_max` | S4 偏离MA20上限 | number 0.01 .. 0.2 | 0.06 | 0.06 |
| `min_pool_score` | 入池最低得分(S1-S4) | integer 1 .. 4 | 2 | 2 |
| `breakout_vol_ratio` | S5 突破量比阈值 | number 0.5 .. 3.0 | 1.2 | 1.2 |
| `max_pullback_days` | S6 最大回调天数 | integer 0 .. 20 | 3 | 3 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 50.0 | 50.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## B1战法V1

`b1_mtf_v1`；实际插件：`B1MultiTimeframePlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `vol_ratio` | 缩量比例 | number 0.1 .. 1.5 | 0.8 | 0.8 |
| `chg_limit` | 涨跌幅上限(%) | number 0.5 .. 10.0 | 3.0 | 3.0 |
| `amp_limit_10cm` | 10cm板振幅上限(%) | number 1.0 .. 15.0 | 5.0 | 5.0 |
| `amp_limit_20cm` | 20cm板振幅上限(%) | number 1.0 .. 20.0 | 8.0 | 8.0 |
| `kdj_j_upper` | KDJ J值上限 | number 10.0 .. 90.0 | 50.0 | 50.0 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 35.0 | 35.0 |
| `min_event_count` | 最少事件数 | integer 0 ..  | 0 | 0 |
| `require_sequence` | 要求事件顺序 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 要求关键事件确认 | boolean  | false | false |

## 趋势为王

`trend_king_v1`；实际插件：`TrendKingPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `mode` | 选股模式 | enum ["all","a","b","c"] | "all" | "all" |
| `min_day_gain_a` | 模式A-涨停日涨幅下限(%) | number 5.0 .. 20.0 | 9.5 | 9.5 |
| `min_hist` | 历史弹性下限 | number 10.0 .. 100.0 | 30.0 | 30.0 |
| `min_gain_3d_b` | 模式B-3日涨幅下限(%) | number 3.0 .. 20.0 | 7.0 | 7.0 |
| `min_gain_5d_b` | 模式B-5日涨幅下限(%) | number 5.0 .. 30.0 | 10.0 | 10.0 |
| `max_vol_ratio_c` | 模式C-量比上限 | number 0.5 .. 3.0 | 1.5 | 1.5 |
| `min_hist_c` | 模式C-弹性下限 | number 20.0 .. 100.0 | 50.0 | 50.0 |
| `min_prev_gain_c` | 模式C-前日涨幅下限(%) | number 0.0 .. 10.0 | 3.0 | 3.0 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 涨停板精选

`trend_king_limitup_v1`；实际插件：`TrendKingPlugin`；信号输出上限：8（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `mode` | 选股模式 | enum ["all","a","b","c"] | "all" | "a" |
| `min_day_gain_a` | 模式A-涨停日涨幅下限(%) | number 5.0 .. 20.0 | 9.5 | 9.5 |
| `min_hist` | 历史弹性下限 | number 10.0 .. 100.0 | 30.0 | 30.0 |
| `min_gain_3d_b` | 模式B-3日涨幅下限(%) | number 3.0 .. 20.0 | 7.0 | 7.0 |
| `min_gain_5d_b` | 模式B-5日涨幅下限(%) | number 5.0 .. 30.0 | 10.0 | 10.0 |
| `max_vol_ratio_c` | 模式C-量比上限 | number 0.5 .. 3.0 | 1.5 | 1.5 |
| `min_hist_c` | 模式C-弹性下限 | number 20.0 .. 100.0 | 50.0 | 50.0 |
| `min_prev_gain_c` | 模式C-前日涨幅下限(%) | number 0.0 .. 10.0 | 3.0 | 3.0 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 涨势确认

`trend_king_rally_v1`；实际插件：`TrendKingPlugin`；信号输出上限：10（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `mode` | 选股模式 | enum ["all","a","b","c"] | "all" | "b" |
| `min_day_gain_a` | 模式A-涨停日涨幅下限(%) | number 5.0 .. 20.0 | 9.5 | 9.5 |
| `min_hist` | 历史弹性下限 | number 10.0 .. 100.0 | 30.0 | 30.0 |
| `min_gain_3d_b` | 模式B-3日涨幅下限(%) | number 3.0 .. 20.0 | 7.0 | 7.0 |
| `min_gain_5d_b` | 模式B-5日涨幅下限(%) | number 5.0 .. 30.0 | 10.0 | 10.0 |
| `max_vol_ratio_c` | 模式C-量比上限 | number 0.5 .. 3.0 | 1.5 | 1.5 |
| `min_hist_c` | 模式C-弹性下限 | number 20.0 .. 100.0 | 50.0 | 50.0 |
| `min_prev_gain_c` | 模式C-前日涨幅下限(%) | number 0.0 .. 10.0 | 3.0 | 3.0 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 涨停跟踪池

`trend_king_pullback_v1`；实际插件：`TrendKingPlugin`；信号输出上限：0（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `mode` | 选股模式 | enum ["all","a","b","c"] | "all" | "c" |
| `min_day_gain_a` | 模式A-涨停日涨幅下限(%) | number 5.0 .. 20.0 | 9.5 | 9.5 |
| `min_hist` | 历史弹性下限 | number 10.0 .. 100.0 | 30.0 | 30.0 |
| `min_gain_3d_b` | 模式B-3日涨幅下限(%) | number 3.0 .. 20.0 | 7.0 | 7.0 |
| `min_gain_5d_b` | 模式B-5日涨幅下限(%) | number 5.0 .. 30.0 | 10.0 | 10.0 |
| `max_vol_ratio_c` | 模式C-量比上限 | number 0.5 .. 3.0 | 1.5 | 1.5 |
| `min_hist_c` | 模式C-弹性下限 | number 20.0 .. 100.0 | 50.0 | 50.0 |
| `min_prev_gain_c` | 模式C-前日涨幅下限(%) | number 0.0 .. 10.0 | 3.0 | 3.0 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 情绪涨停

`emotion_limit_up_v1`；实际插件：`EmotionLimitUpPlugin`；信号输出上限：10（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_day_gain` | 涨停日涨幅下限(%) | number 5.0 .. 20.0 | 9.5 | 9.5 |
| `lookback_days` | 信号回溯窗口(交易日) | integer 3 .. 10 | 5 | 5 |
| `require_next_day_confirm` | 要求次日倍量+没死确认 | boolean  | false | false |
| `next_day_volume_multiple` | 次日倍量倍数 | number 1.0 .. 5.0 | 2.0 | 2.0 |
| `max_drawdown_from_limit_pct` | 相对涨停价最大回撤(%) | number 1.0 .. 15.0 | 5.0 | 5.0 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 涨停套利

`limit_up_arb_v1`；实际插件：`LimitUpArbPlugin`；信号输出上限：15（0表示此处不另截断）。

| 字段 | 含义 | 类型与范围 | Schema 默认 | Descriptor 默认 |
| --- | --- | --- | --- | --- |
| `min_day_gain` | 当日涨幅下限(%) | number 1.0 .. 20.0 | 3.0 | 3.0 |
| `min_volume_ratio_prev` | 相对昨日涨停日放量倍数 | number 1.0 .. 5.0 | 1.2 | 1.2 |
| `min_volume_ratio_ma5` | 相对5日均量倍数(0=不启用) | number 0.0 .. 5.0 | 0.0 | 0.0 |
| `sector_rank_weight` | 板块热度排序权重 | number 0.0 .. 0.3 | 0.08 | 0.08 |
| `min_score` | 入场质量分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `min_event_count` | 最少事件数 | integer 0 .. 12 | 0 | 0 |
| `require_sequence` | 要求事件序列 | boolean  | false | false |
| `health_score_min` | 健康分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_score_min` | 事件分下限 | number 0.0 .. 100.0 | 0.0 | 0.0 |
| `event_grade_min` | 事件等级下限 | enum ["A","B","C"] | "C" | "C" |
| `require_key_event_confirmation` | 关键事件确认必需 | boolean  | false | false |

## 注册异常

正式 `ths_force_rhythm_v1` 取到 `BaseStrategyPlugin`；真正的 `ForceRhythmPlugin` 绑定在未注册描述符 `__removed_wulong_cluster_v2` 上。这里只记录审计事实，本阶段没有修改 FINAL TRADE 源代码。

## 请求级公共默认

正式回测 `BacktestRunRequest` 的默认值包括：窗口60、最低分55、初始资金1000000、每仓比例0.2、最多5仓、止损0.05、止盈0.15、持有期60、费用10bps、T+1开启、入场延迟1、最多120证券、股票池daily刷新。策略参数覆盖、UI草稿与回填报告可能改变这些值；不能将其视为本次新实验设置。

完整请求字段参见 [models.py](/H:/CODE/FINAL-TRADE/backend/app/models.py:720)。实验图形量价与混合波段的全部默认参数见主报告，它们未注册在此表。
