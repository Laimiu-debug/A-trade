import dayjs from 'dayjs'
import type {
  AIAnalysisRecord,
  AIProviderTestRequest,
  AIProviderTestResponse,
  AppConfig,
  BacktestPlateauCorrelationRow,
  BacktestPlateauPoint,
  BacktestPlateauResponse,
  BacktestPlateauRunRequest,
  BacktestResponse,
  BacktestRunRequest,
  BacktestTrade,
  BoardFilter,
  CandlePoint,
  DailyReviewListResponse,
  DailyReviewPayload,
  DailyReviewRecord,
  IntradayPoint,
  Market,
  MarketDataSyncRequest,
  MarketDataSyncResponse,
  MarketNewsResponse,
  PortfolioPosition,
  PortfolioSnapshot,
  ReviewResponse,
  ReviewTag,
  ReviewTagCreateRequest,
  ReviewTagStatsResponse,
  ReviewTagsPayload,
  ReviewTagType,
  ScreenerMode,
  ScreenerParams,
  ScreenerResult,
  ScreenerRunDetail,
  SimFillsResponse,
  SimOrdersResponse,
  SimResetResponse,
  SimSettleResponse,
  SimTradeFill,
  SimTradeOrder,
  SimTradingConfig,
  SignalScanMode,
  SignalResult,
  SignalsResponse,
  StockAnalysis,
  StockAnalysisResponse,
  StockAnnotation,
  StrategyCatalogResponse,
  EventJudgmentCatalogResponse,
  EventJudgmentProfile,
  EventJudgmentProfileApplyRequest,
  EventJudgmentProfileDeleteResponse,
  EventJudgmentRuleValue,
  EventJudgmentProfileUpsertRequest,
  SystemStorageStatus,
  TradeFillTagAssignment,
  TradeFillTagUpdateRequest,
  WeeklyReviewListResponse,
  WeeklyReviewPayload,
  WeeklyReviewRecord,
  ThemeStage,
  TradeRecord,
  TrendClass,
} from '@/types/contracts'
import { resolveSignalPriority } from '@/shared/utils/signals'
import { normalizeScreenerStep1RankRange, sliceRowsByScreenerRankRange } from '@/shared/utils/screener'

const stockPool: Array<{ symbol: string; name: string; trend: TrendClass; stage: 'Early' | 'Mid' | 'Late' }> = [
  { symbol: 'sh600519', name: '贵州茅台', trend: 'A', stage: 'Mid' },
  { symbol: 'sz300750', name: '宁德时代', trend: 'A_B', stage: 'Early' },
  { symbol: 'sh601899', name: '紫金矿业', trend: 'A', stage: 'Mid' },
  { symbol: 'sz002594', name: '比亚迪', trend: 'A_B', stage: 'Mid' },
  { symbol: 'sh600030', name: '中信证券', trend: 'A', stage: 'Early' },
  { symbol: 'sz000333', name: '美的集团', trend: 'A', stage: 'Late' },
  { symbol: 'sh688041', name: '海光信息', trend: 'B', stage: 'Late' },
  { symbol: 'sz002230', name: '科大讯飞', trend: 'A_B', stage: 'Mid' },
]

const THEME_STAGES: ThemeStage[] = ['发酵中' as ThemeStage, '高潮' as ThemeStage, '退潮' as ThemeStage]

const candlesMap = new Map<string, CandlePoint[]>()
const runStore = new Map<string, ScreenerRunDetail>()
const annotationStore = new Map<string, StockAnnotation>()
let dailyReviewsStore: DailyReviewRecord[] = []
let weeklyReviewsStore: WeeklyReviewRecord[] = []
let fillTagStore: TradeFillTagAssignment[] = []
let reviewTagsStore: ReviewTagsPayload = {
  emotion: [
    { id: 'emotion-01', name: '冲动追高', color: 'red', created_at: '2026-01-01 00:00:00' },
    { id: 'emotion-02', name: '恐慌割肉', color: 'volcano', created_at: '2026-01-01 00:00:00' },
    { id: 'emotion-03', name: '理性建仓', color: 'blue', created_at: '2026-01-01 00:00:00' },
  ],
  reason: [
    { id: 'reason-01', name: '财报利好', color: 'geekblue', created_at: '2026-01-01 00:00:00' },
    { id: 'reason-02', name: '政策利好', color: 'magenta', created_at: '2026-01-01 00:00:00' },
    { id: 'reason-03', name: '技术突破', color: 'cyan', created_at: '2026-01-01 00:00:00' },
  ],
}

const ALLOWED_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star', 'beijing', 'st']
const ALLOWED_MARKET_FILTERS: Market[] = ['sh', 'sz', 'bj']
const strategyCatalogStore: StrategyCatalogResponse = {
  items: [
    {
      strategy_id: 'wyckoff_trend_v1',
      name: '维科夫趋势V1',
      version: '1.0.0',
      enabled: true,
      is_default: true,
      capabilities: {
        supports_matrix: true,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 55 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 55 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'B' },
      },
      strategy_params_defaults: {
        health_score_min: 55,
        event_score_min: 55,
        event_grade_min: 'B',
      },
    },
    {
      strategy_id: 'wyckoff_trend_v2',
      name: '维科夫趋势V2',
      version: '0.2.0',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: true,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 60 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 60 },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: true },
        rank_weight_health: { type: 'number', title: '健康分权重', minimum: 0, maximum: 1, default: 0.45 },
        rank_weight_event: { type: 'number', title: '事件分权重', minimum: 0, maximum: 1, default: 0.55 },
      },
      strategy_params_defaults: {
        health_score_min: 60,
        event_score_min: 60,
        require_key_event_confirmation: true,
        rank_weight_health: 0.45,
        rank_weight_event: 0.55,
      },
    },
    {
      strategy_id: 'relative_strength_breakout_v1',
      name: '相对强弱突破V1',
      version: '1.0.0-alpha',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        min_ret40: { type: 'number', title: '40日涨幅下限', minimum: 0, maximum: 1.5, default: 0.12 },
        max_retrace20: { type: 'number', title: '20日回撤上限', minimum: 0.01, maximum: 0.6, default: 0.22 },
        min_up_down_volume_ratio: { type: 'number', title: '量比下限', minimum: 0.8, maximum: 3, default: 1.15 },
        min_vol_slope20: { type: 'number', title: '量能斜率下限', minimum: -0.5, maximum: 0.5, default: 0.02 },
        rank_weight_health: { type: 'number', title: '健康分权重', minimum: 0, maximum: 1, default: 0.25 },
        rank_weight_event: { type: 'number', title: '事件分权重', minimum: 0, maximum: 1, default: 0.25 },
        rank_weight_strength: { type: 'number', title: '强势权重', minimum: 0, maximum: 1, default: 0.3 },
        rank_weight_volume: { type: 'number', title: '量能权重', minimum: 0, maximum: 1, default: 0.1 },
        rank_weight_structure: { type: 'number', title: '结构权重', minimum: 0, maximum: 1, default: 0.1 },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_ret40: 0.12,
        max_retrace20: 0.22,
        min_up_down_volume_ratio: 1.15,
        min_vol_slope20: 0.02,
        rank_weight_health: 0.25,
        rank_weight_event: 0.25,
        rank_weight_strength: 0.3,
        rank_weight_volume: 0.1,
        rank_weight_structure: 0.1,
        require_key_event_confirmation: false,
      },
    },
    {
      strategy_id: 'score_only_rank_v1',
      name: '评分买入V1',
      version: '1.0.0-alpha',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        min_score: { type: 'number', title: '评分下限', minimum: 0, maximum: 100, default: 60 },
        min_event_count: { type: 'integer', title: '最小事件数', minimum: 0, maximum: 12, default: 0 },
        require_sequence: { type: 'boolean', title: '要求事件序列', default: false },
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 0 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 0 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'C' },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_score: 60,
        min_event_count: 0,
        require_sequence: false,
        health_score_min: 0,
        event_score_min: 0,
        event_grade_min: 'C',
        require_key_event_confirmation: false,
      },
    },
    {
      strategy_id: 'ths_main_force_flip_v1',
      name: '主力紫转黄V1',
      version: '1.0.0-alpha',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        min_score: { type: 'number', title: '信号分下限', minimum: 0, maximum: 100, default: 0 },
        min_event_count: { type: 'integer', title: '最小事件数', minimum: 0, maximum: 12, default: 0 },
        require_sequence: { type: 'boolean', title: '要求事件序列', default: false },
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 0 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 0 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'C' },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_score: 0,
        min_event_count: 0,
        require_sequence: false,
        health_score_min: 0,
        event_score_min: 0,
        event_grade_min: 'C',
        require_key_event_confirmation: false,
      },
    },
    {
      strategy_id: 'ths_main_force_golden_cross_v1',
      name: '主力金叉V1',
      version: '1.0.0-alpha',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        min_score: { type: 'number', title: '信号分下限', minimum: 0, maximum: 100, default: 0 },
        min_event_count: { type: 'integer', title: '最小事件数', minimum: 0, maximum: 12, default: 0 },
        require_sequence: { type: 'boolean', title: '要求事件序列', default: false },
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 0 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 0 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'C' },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_score: 0,
        min_event_count: 0,
        require_sequence: false,
        health_score_min: 0,
        event_score_min: 0,
        event_grade_min: 'C',
        require_key_event_confirmation: false,
      },
    },
    {
      strategy_id: 'ths_force_rhythm_v1',
      name: '主散节奏波V1',
      version: '1.0.0-alpha',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        min_cycle_count: { type: 'integer', title: '最少完整周期数', minimum: 2, maximum: 12, default: 3 },
        max_cycle_cv: { type: 'number', title: '周期长度变异系数上限', minimum: 0.05, maximum: 1.5, default: 0.55 },
        max_amplitude_cv: { type: 'number', title: '波幅变异系数上限', minimum: 0.05, maximum: 1.5, default: 0.85 },
        min_autocorr: { type: 'number', title: '周期自相关下限', minimum: 0, maximum: 1, default: 0.05 },
        min_wave_swing_ratio: { type: 'number', title: '主力波幅/均值下限', minimum: 0.01, maximum: 2, default: 0.12 },
        trough_percentile_max: { type: 'number', title: '主力波谷分位上限', minimum: 0.05, maximum: 0.8, default: 0.40 },
        peak_percentile_min: { type: 'number', title: '主力波峰分位下限(卖出)', minimum: 0.4, maximum: 0.95, default: 0.65 },
        retail_high_percentile_min: { type: 'number', title: '散户高位分位(卖出)', minimum: 0.4, maximum: 0.95, default: 0.60 },
        require_trough_turn: { type: 'boolean', title: '要求主力波谷拐头', default: true },
        block_buy_on_retail_sell: { type: 'boolean', title: '散户卖出时禁止买入', default: true },
        min_event_count: { type: 'integer', title: '最小事件数', minimum: 0, maximum: 12, default: 0 },
        require_sequence: { type: 'boolean', title: '要求事件序列', default: false },
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 0 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 0 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'C' },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_cycle_count: 3,
        max_cycle_cv: 0.55,
        max_amplitude_cv: 0.85,
        min_autocorr: 0.05,
        min_wave_swing_ratio: 0.12,
        trough_percentile_max: 0.40,
        peak_percentile_min: 0.65,
        retail_high_percentile_min: 0.60,
        require_trough_turn: true,
        block_buy_on_retail_sell: true,
        min_event_count: 0,
        require_sequence: false,
        health_score_min: 0,
        event_score_min: 0,
        event_grade_min: 'C',
        require_key_event_confirmation: false,
      },
    },
    {
      strategy_id: 'emotion_limit_up_v1',
      name: '情绪涨停',
      version: '1.0.0',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: true,
        supports_entry_delay: true,
      },
      strategy_params_schema: {
        min_day_gain: { type: 'number', title: '涨停日涨幅下限(%)', minimum: 5, maximum: 20, default: 9.5 },
        lookback_days: { type: 'integer', title: '信号回溯窗口(交易日)', minimum: 3, maximum: 10, default: 5 },
        require_next_day_confirm: { type: 'boolean', title: '要求次日倍量+没死确认', default: false },
        next_day_volume_multiple: { type: 'number', title: '次日倍量倍数', minimum: 1, maximum: 5, default: 2 },
        max_drawdown_from_limit_pct: { type: 'number', title: '相对涨停价最大回撤(%)', minimum: 1, maximum: 15, default: 5 },
        min_score: { type: 'number', title: '入场质量分下限', minimum: 0, maximum: 100, default: 0 },
        min_event_count: { type: 'integer', title: '最小事件数', minimum: 0, maximum: 12, default: 0 },
        require_sequence: { type: 'boolean', title: '要求事件序列', default: false },
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 0 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 0 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'C' },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_day_gain: 9.5,
        lookback_days: 5,
        require_next_day_confirm: false,
        next_day_volume_multiple: 2,
        max_drawdown_from_limit_pct: 5,
        min_score: 0,
        min_event_count: 0,
        require_sequence: false,
        health_score_min: 0,
        event_score_min: 0,
        event_grade_min: 'C',
        require_key_event_confirmation: false,
      },
    },
    {
      strategy_id: 'limit_up_arb_v1',
      name: '涨停套利',
      version: '1.0.0',
      enabled: true,
      is_default: false,
      capabilities: {
        supports_matrix: false,
        supports_signal_age_filter: false,
        supports_entry_delay: false,
      },
      strategy_params_schema: {
        min_day_gain: { type: 'number', title: '当日涨幅下限(%)', minimum: 1, maximum: 20, default: 3 },
        min_volume_ratio_prev: { type: 'number', title: '相对昨日涨停日放量倍数', minimum: 1, maximum: 5, default: 1.2 },
        min_volume_ratio_ma5: { type: 'number', title: '相对5日均量倍数(0=不启用)', minimum: 0, maximum: 5, default: 0 },
        sector_rank_weight: { type: 'number', title: '板块热度排序权重', minimum: 0, maximum: 0.3, default: 0.08 },
        min_score: { type: 'number', title: '入场质量分下限', minimum: 0, maximum: 100, default: 0 },
        min_event_count: { type: 'integer', title: '最小事件数', minimum: 0, maximum: 12, default: 0 },
        require_sequence: { type: 'boolean', title: '要求事件序列', default: false },
        health_score_min: { type: 'number', title: '健康分下限', minimum: 0, maximum: 100, default: 0 },
        event_score_min: { type: 'number', title: '事件分下限', minimum: 0, maximum: 100, default: 0 },
        event_grade_min: { type: 'enum', title: '事件等级下限', options: ['A', 'B', 'C'], default: 'C' },
        require_key_event_confirmation: { type: 'boolean', title: '关键事件确认必需', default: false },
      },
      strategy_params_defaults: {
        min_day_gain: 3,
        min_volume_ratio_prev: 1.2,
        min_volume_ratio_ma5: 0,
        sector_rank_weight: 0.08,
        min_score: 0,
        min_event_count: 0,
        require_sequence: false,
        health_score_min: 0,
        event_score_min: 0,
        event_grade_min: 'C',
        require_key_event_confirmation: false,
      },
    },
  ],
}

const eventJudgmentMetricOptions: EventJudgmentCatalogResponse['metric_options'] = [
  { metric_key: 'event_background_score', label: '背景分', description: '趋势背景与风险环境（高分更优）' },
  { metric_key: 'event_position_score', label: '位置分', description: '事件发生位置是否处于理想区间（高分更优）' },
  { metric_key: 'event_vol_price_score', label: '量价分', description: '量价协同与主导方向（高分更优）' },
  { metric_key: 'event_confirmation_score', label: '确认分', description: '序列与关键确认状态（高分更优）' },
  { metric_key: 'phase_context_score', label: '阶段语境分', description: '前置事件完整度与阶段一致性（高分更优）' },
  { metric_key: 'event_recency_score', label: '新鲜度分', description: '事件时间衰减后的有效性（高分更优）' },
  { metric_key: 'candle_quality_score', label: 'K线质量分', description: '事件K线形态质量（高分更优）' },
  { metric_key: 'cost_center_shift_score', label: '筹码迁移分', description: '筹码重心迁移质量（高分更优）' },
  { metric_key: 'weekly_context_score', label: '周线语境分', description: '周线环境对日线事件的放大质量（高分更优）' },
  { metric_key: 'risk_score', label: '风险分', description: '风险越高分越高，通常建议勾选反向（invert）' },
]

const eventJudgmentRuleOptions: EventJudgmentCatalogResponse['rule_options'] = [
  { rule_key: 'lookback_core_days', label: '核心回看天数', description: '事件搜索起始窗口（天）', category: '基础窗口', value_type: 'integer', min_value: 20, max_value: 120, step: 1, default_value: 40 },
  { rule_key: 'sc_scan_lookback_days', label: 'SC搜索窗口', description: 'SC锚点搜索窗口（天）', category: '基础窗口', value_type: 'integer', min_value: 15, max_value: 120, step: 1, default_value: 35 },
  { rule_key: 'bc_scan_lookback_days', label: 'BC搜索窗口', description: 'BC锚点搜索窗口（天）', category: '基础窗口', value_type: 'integer', min_value: 15, max_value: 120, step: 1, default_value: 35 },
  { rule_key: 'pattern_window_days', label: '中后期模式窗口', description: 'TSO/Spring 搜索窗口（天）', category: '基础窗口', value_type: 'integer', min_value: 10, max_value: 80, step: 1, default_value: 28 },
  { rule_key: 'pattern_extra_spring_days', label: 'Spring额外窗口', description: 'Spring 额外扩展窗口（天）', category: '基础窗口', value_type: 'integer', min_value: 0, max_value: 30, step: 1, default_value: 8 },
  { rule_key: 'enable_sc', label: '启用SC', description: '是否启用SC事件', category: 'SC', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_ps', label: '启用PS', description: '是否启用PS事件', category: 'PS', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_ar', label: '启用AR', description: '是否启用AR事件', category: 'AR', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_st', label: '启用ST', description: '是否启用ST事件', category: 'ST', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_spring', label: '启用Spring', description: '是否启用Spring事件', category: 'Spring', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_tso', label: '启用TSO', description: '是否启用TSO事件', category: 'TSO', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_sos', label: '启用SOS', description: '是否启用SOS事件', category: 'SOS', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_joc', label: '启用JOC', description: '是否启用JOC事件', category: 'JOC', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_lps', label: '启用LPS', description: '是否启用LPS事件', category: 'LPS', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_utad', label: '启用UTAD', description: '是否启用UTAD风险事件', category: 'UTAD', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_sow', label: '启用SOW', description: '是否启用SOW风险事件', category: 'SOW', value_type: 'boolean', default_value: true },
  { rule_key: 'enable_lpsy', label: '启用LPSY', description: '是否启用LPSY风险事件', category: 'LPSY', value_type: 'boolean', default_value: true },
  { rule_key: 'sc_anchor_tr_pos_max', label: 'SC锚点位置上限', description: 'SC锚点TR位置最大值', category: 'SC', value_type: 'number', min_value: 0.1, max_value: 0.9, step: 0.005, default_value: 0.45 },
  { rule_key: 'sc_anchor_vol_ratio_min', label: 'SC锚点量比下限', description: 'SC锚点量比下限', category: 'SC', value_type: 'number', min_value: 0.5, max_value: 3, step: 0.01, default_value: 1.2 },
  { rule_key: 'ps_vol_ratio_min', label: 'PS量比下限', description: 'PS量比下限', category: 'PS', value_type: 'number', min_value: 0.5, max_value: 3, step: 0.01, default_value: 1.08 },
  { rule_key: 'ar_rebound_min', label: 'AR反弹下限', description: 'AR反弹阈值', category: 'AR', value_type: 'number', min_value: 1, max_value: 1.5, step: 0.001, default_value: 1.05 },
  { rule_key: 'st_low_near_sc_tol', label: 'ST贴近SC低点容差', description: 'ST低点贴近SC低点容差', category: 'ST', value_type: 'number', min_value: 0, max_value: 0.25, step: 0.001, default_value: 0.04 },
  { rule_key: 'spring_break_prior_low_max', label: 'Spring下破比例上限', description: 'Spring条件 low < prior_low * 阈值', category: 'Spring', value_type: 'number', min_value: 0.8, max_value: 1.05, step: 0.001, default_value: 0.985 },
  { rule_key: 'sos_ret10_min', label: 'SOS十日涨幅下限', description: 'SOS条件 ret10 > 阈值', category: 'SOS', value_type: 'number', min_value: -0.1, max_value: 0.5, step: 0.001, default_value: 0.05 },
  { rule_key: 'joc_close_break_prior_high_min', label: 'JOC突破比例下限', description: 'JOC条件 close >= prior_high * 阈值', category: 'JOC', value_type: 'number', min_value: 0.95, max_value: 1.2, step: 0.001, default_value: 1.005 },
  { rule_key: 'lps_vol_vs_prev5_max', label: 'LPS相对前5日均量上限', description: 'LPS量比上限', category: 'LPS', value_type: 'number', min_value: 0.1, max_value: 1.5, step: 0.005, default_value: 0.95 },
  { rule_key: 'utad_upper_shadow_min', label: 'UTAD上影占比下限', description: 'UTAD上影线占比最小值', category: 'UTAD', value_type: 'number', min_value: 0, max_value: 1, step: 0.005, default_value: 0.5 },
  { rule_key: 'sow_ret10_max', label: 'SOW十日涨幅上限', description: 'SOW条件 ret10 <= 阈值', category: 'SOW', value_type: 'number', min_value: -0.6, max_value: 0.1, step: 0.001, default_value: -0.05 },
  { rule_key: 'lpsy_lower_high_max', label: 'LPSY次高点比例上限', description: 'LPSY次高点比例上限', category: 'LPSY', value_type: 'number', min_value: 0.7, max_value: 1.1, step: 0.001, default_value: 0.99 },
]

function defaultEventRuleValues(): EventJudgmentRuleValue[] {
  return eventJudgmentRuleOptions.map((item) => ({
    rule_key: item.rule_key,
    value: item.default_value,
  }))
}

const eventJudgmentSystemProfiles: EventJudgmentProfile[] = [
  {
    profile_id: 'system_legacy_formula_v1',
    name: '系统预设：经典综合判别',
    description: '保持当前版本历史公式，兼容旧结果。',
    score_mode: 'legacy_formula',
    is_system: true,
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    dimensions: [],
    rule_values: defaultEventRuleValues(),
  },
  {
    profile_id: 'system_3d_core_v1',
    name: '系统预设：三维核心',
    description: '背景 + 位置 + 量价 三维判别。',
    score_mode: 'dimension_weighted',
    is_system: true,
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    dimensions: [
      {
        dimension_id: 'dim_background',
        label: '背景分',
        metric_key: 'event_background_score',
        weight: 0.34,
        invert: false,
        enabled: true,
      },
      {
        dimension_id: 'dim_position',
        label: '位置分',
        metric_key: 'event_position_score',
        weight: 0.33,
        invert: false,
        enabled: true,
      },
      {
        dimension_id: 'dim_vol_price',
        label: '量价分',
        metric_key: 'event_vol_price_score',
        weight: 0.33,
        invert: false,
        enabled: true,
      },
    ],
    rule_values: defaultEventRuleValues(),
  },
  {
    profile_id: 'system_6d_confirmation_v1',
    name: '系统预设：六维确认增强',
    description: '背景/位置/量价/确认/语境/新鲜度 六维综合。',
    score_mode: 'dimension_weighted',
    is_system: true,
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    dimensions: [
      { dimension_id: 'dim_background', label: '背景分', metric_key: 'event_background_score', weight: 0.22, invert: false, enabled: true },
      { dimension_id: 'dim_position', label: '位置分', metric_key: 'event_position_score', weight: 0.14, invert: false, enabled: true },
      { dimension_id: 'dim_vol_price', label: '量价分', metric_key: 'event_vol_price_score', weight: 0.2, invert: false, enabled: true },
      { dimension_id: 'dim_confirmation', label: '确认分', metric_key: 'event_confirmation_score', weight: 0.18, invert: false, enabled: true },
      { dimension_id: 'dim_context', label: '阶段语境分', metric_key: 'phase_context_score', weight: 0.14, invert: false, enabled: true },
      { dimension_id: 'dim_recency', label: '新鲜度分', metric_key: 'event_recency_score', weight: 0.12, invert: false, enabled: true },
    ],
    rule_values: defaultEventRuleValues(),
  },
]

let eventJudgmentCustomProfiles: EventJudgmentProfile[] = []
let eventJudgmentActiveProfileId = 'system_legacy_formula_v1'

const marketNewsSeed: MarketNewsResponse['items'] = [
  {
    title: 'AI 算力板块午后拉升，多只核心股放量走强',
    url: 'https://finance.eastmoney.com/',
    snippet: '盘中资金回流算力与光模块方向，短线风险偏好明显提升。',
    pub_date: dayjs().subtract(1, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    source_name: '东方财富',
  },
  {
    title: '机器人产业链持续活跃，政策催化叠加订单预期',
    url: 'https://www.cls.cn/',
    snippet: '多家机构认为机器人板块景气度仍有向上空间，但需关注高位波动。',
    pub_date: dayjs().subtract(3, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    source_name: '财联社',
  },
  {
    title: '半导体设备公司披露业绩预告，行业景气延续分化',
    url: 'https://www.cninfo.com.cn/',
    snippet: '龙头公司盈利改善较快，二线标的估值修复节奏存在差异。',
    pub_date: dayjs().subtract(6, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    source_name: '巨潮资讯',
  },
  {
    title: 'A股三大指数震荡整理，成交额较前一日温和放大',
    url: 'https://finance.eastmoney.com/',
    snippet: '市场风格在成长与价值之间来回切换，热点轮动速度仍然较快。',
    pub_date: dayjs().subtract(10, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    source_name: '东方财富',
  },
  {
    title: '上周市场风格复盘：成长与价值轮动的节奏',
    url: 'https://finance.eastmoney.com/',
    snippet: '该文用于验证时效筛选，默认不应出现在 24h/48h 结果中。',
    pub_date: dayjs().subtract(96, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    source_name: '东方财富',
  },
]
const marketNewsCacheStore = new Map<string, { ts: number; items: MarketNewsResponse['items'] }>()
let marketNewsLastSuccess: MarketNewsResponse['items'] = []

let aiRecordsStore: AIAnalysisRecord[] = [
  {
    provider: 'openai',
    symbol: 'sz300750',
    name: '宁德时代',
    fetched_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    source_urls: ['https://finance.eastmoney.com/', 'https://www.cls.cn/'],
    summary: '新能源主线保持活跃，回撤阶段承接较好。',
    conclusion: '发酵中',
    confidence: 0.78,
    breakout_date: dayjs().subtract(17, 'day').format('YYYY-MM-DD'),
    trend_bull_type: 'A_B 慢牛加速',
    theme_name: '固态电池',
    rise_reasons: ['20日量能斜率为正', '回调缩量且未破关键均线', '板块热度维持在发酵区间'],
  },
  {
    provider: 'openai',
    symbol: 'sh600519',
    name: '贵州茅台',
    fetched_at: dayjs().subtract(1, 'day').format('YYYY-MM-DD HH:mm:ss'),
    source_urls: ['https://www.cninfo.com.cn/'],
    summary: '消费龙头资金抱团明显，高位换手可控。',
    conclusion: '高潮',
    confidence: 0.66,
    breakout_date: dayjs().subtract(26, 'day').format('YYYY-MM-DD'),
    trend_bull_type: 'A 阶梯慢牛',
    theme_name: '高端消费',
    rise_reasons: ['龙头资金抱团', '回撤幅度有限', '基本面预期稳定'],
  },
]

let configStore: AppConfig = {
  tdx_data_path: 'D:\\new_tdx\\vipdoc',
  market_data_source: 'tdx_then_akshare',
  akshare_cache_dir: '%USERPROFILE%\\.tdx-trend\\akshare\\daily',
  markets: ['sh', 'sz'],
  return_window_days: 40,
  candles_window_bars: 120,
  backtest_matrix_engine_enabled: true,
  backtest_default_execution_path_preference: 'legacy',
  backtest_plateau_workers: 4,
  top_n: 500,
  turnover_threshold: 0.05,
  amount_threshold: 5e8,
  amplitude_threshold: 0.03,
  initial_capital: 1_000_000,
  ai_provider: 'openai',
  ai_timeout_sec: 10,
  ai_retry_count: 2,
  api_key: '',
  api_key_path: '%USERPROFILE%\\.tdx-trend\\app.config.json',
  ai_providers: [
    {
      id: 'openai',
      label: 'OpenAI',
      base_url: 'https://api.openai.com/v1',
      model: 'gpt-4o-mini',
      api_key: '',
      api_key_path: '%USERPROFILE%\\.tdx-trend\\openai.key',
      enabled: true,
    },
  ],
  ai_sources: [
    { id: 'eastmoney', name: '东方财富新闻', url: 'https://finance.eastmoney.com/', enabled: true },
  ],
}

let simConfigStore: SimTradingConfig = {
  initial_capital: 1_000_000,
  commission_rate: 0.0003,
  min_commission: 5,
  stamp_tax_rate: 0.001,
  transfer_fee_rate: 0.00001,
  slippage_rate: 0,
}

let simCash = simConfigStore.initial_capital
let simOrdersStore: SimTradeOrder[] = []
let simFillsStore: SimTradeFill[] = []
let simLotsStore: Array<{
  symbol: string
  buy_date: string
  buy_price: number
  unit_cost: number
  quantity: number
  remaining: number
}> = []
let simClosedTradesStore: TradeRecord[] = []

function hashSeed(text: string) {
  return text.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0)
}

function mean(values: number[]) {
  if (values.length === 0) return 0
  return values.reduce((acc, value) => acc + value, 0) / values.length
}

function clamp(value: number, lower: number, upper: number) {
  return Math.max(lower, Math.min(upper, value))
}

function uniqueTokens(values: string[]) {
  const seen = new Set<string>()
  const result: string[] = []
  values.forEach((item) => {
    const token = item.trim()
    if (!token || seen.has(token)) return
    seen.add(token)
    result.push(token)
  })
  return result
}

function getWeekRangeFromLabel(weekLabel: string) {
  const match = weekLabel.match(/^(\d{4})-W(\d{2})$/)
  if (!match) {
    const today = dayjs()
    return { start_date: today.startOf('week').format('YYYY-MM-DD'), end_date: today.endOf('week').format('YYYY-MM-DD') }
  }
  const year = Number(match[1])
  const week = Number(match[2])
  const jan4 = dayjs(`${year}-01-04`)
  const firstMonday = jan4.subtract((jan4.day() + 6) % 7, 'day')
  const start = firstMonday.add((week - 1) * 7, 'day')
  return {
    start_date: start.format('YYYY-MM-DD'),
    end_date: start.add(6, 'day').format('YYYY-MM-DD'),
  }
}

function genCandles(seed: number, startPrice = 40): CandlePoint[] {
  const points: CandlePoint[] = []
  let close = startPrice
  for (let i = 119; i >= 0; i -= 1) {
    const date = dayjs().subtract(i, 'day').format('YYYY-MM-DD')
    const drift = Math.sin((i + seed) / 9) * 0.9 + (seed % 3 === 0 ? 0.2 : 0.35)
    const open = Math.max(5, close + drift * 0.35)
    const high = open + Math.abs(drift) * 1.8 + 0.6
    const low = Math.max(1, open - Math.abs(drift) * 1.4 - 0.5)
    close = Number((low + (high - low) * 0.68).toFixed(2))
    points.push({
      time: date,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close,
      volume: Math.floor(2_000_000 + (Math.cos((i + seed) / 7) + 1.4) * 1_700_000),
      amount: Math.floor(close * 100_000_000),
      price_source: seed % 4 === 0 ? 'approx' : 'vwap',
    })
  }
  return points
}

function ensureCandles(symbol: string) {
  if (!candlesMap.has(symbol)) {
    const seed = hashSeed(symbol)
    candlesMap.set(symbol, genCandles(seed, 20 + (seed % 70)))
  }
  return candlesMap.get(symbol) ?? []
}

function buildIntradayTimeAxis() {
  const morning = Array.from({ length: 120 }, (_, i) => {
    const total = 9 * 60 + 30 + i
    const hh = Math.floor(total / 60)
    const mm = total % 60
    return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
  })
  const afternoon = Array.from({ length: 120 }, (_, i) => {
    const total = 13 * 60 + i
    const hh = Math.floor(total / 60)
    const mm = total % 60
    return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
  })
  return [...morning, ...afternoon]
}

function genIntradayPoints(symbol: string, date: string, basePrice: number): IntradayPoint[] {
  const times = buildIntradayTimeAxis()
  const seed = hashSeed(`${symbol}-${date}`)

  const points: IntradayPoint[] = []
  let price = basePrice
  let turnover = 0
  let totalVolume = 0

  times.forEach((time, index) => {
    const wave = Math.sin((index + seed) / 10) * 0.22
    const drift = (index / times.length - 0.5) * (seed % 3 === 0 ? 0.5 : 0.28)
    const micro = Math.cos((index + seed) / 5) * 0.06
    price = Math.max(1, price + wave * 0.05 + drift * 0.03 + micro)
    const roundedPrice = Number(price.toFixed(2))

    const volume = Math.max(1200, Math.floor(4000 + Math.sin((index + seed) / 8) * 1300 + (seed % 7) * 180))
    totalVolume += volume
    turnover += roundedPrice * volume
    const avgPrice = Number((turnover / totalVolume).toFixed(2))

    points.push({
      time,
      price: roundedPrice,
      avg_price: avgPrice,
      volume,
      price_source: index % 79 === 0 ? 'approx' : 'vwap',
    })
  })

  return points
}

function createMockSymbol(index: number) {
  const market = index % 2 === 0 ? 'sh' : 'sz'
  const code = `${100000 + index}`.padStart(6, '0').slice(-6)
  return `${market}${code}`
}

function createMockName(index: number) {
  const sectors = ['科技', '医药', '消费', '金融', '能源', '制造', '材料', '军工']
  return `${sectors[index % sectors.length]}样本${index + 1}`
}

function createPoolRecord(index: number, mode: ScreenerMode, stage: 'input' | 'step1' | 'step2' | 'step3'): ScreenerResult {
  const strictOffset = mode === 'strict' ? 0 : 0.015
  const baseRet = 0.06 + ((index % 200) / 1000) + strictOffset
  const trend: TrendClass = index % 17 === 0 ? 'B' : index % 5 === 0 ? 'A_B' : 'A'
  const stageLabel = index % 3 === 0 ? 'Early' : index % 3 === 1 ? 'Mid' : 'Late'
  const degraded = stage !== 'input' && index % 211 === 0
  const themeStage = THEME_STAGES[index % THEME_STAGES.length]

  return {
    symbol: createMockSymbol(index),
    name: createMockName(index),
    latest_price: Number((8 + (index % 220) * 0.9).toFixed(2)),
    day_change: Number((-2.1 + (index % 11) * 0.42).toFixed(2)),
    day_change_pct: Number((-0.03 + (index % 15) * 0.0045).toFixed(4)),
    score: Math.max(20, 92 - (index % 70)),
    ret40: baseRet,
    turnover20: 0.035 + (index % 25) * 0.002,
    amount20: 220_000_000 + (index % 150) * 18_000_000,
    amplitude20: 0.025 + (index % 12) * 0.002,
    retrace20: 0.03 + (index % 22) * 0.01,
    pullback_days: 1 + (index % 6),
    ma10_above_ma20_days: 4 + (index % 11),
    ma5_above_ma10_days: 2 + (index % 9),
    price_vs_ma20: -0.03 + (index % 13) * 0.008,
    vol_slope20: -0.2 + (index % 20) * 0.07,
    up_down_volume_ratio: 0.9 + (index % 18) * 0.06,
    pullback_volume_ratio: 0.45 + (index % 11) * 0.08,
    has_blowoff_top: stage !== 'input' && index % 31 === 0,
    has_divergence_5d: stage !== 'input' && index % 17 === 0,
    has_upper_shadow_risk: stage !== 'input' && index % 19 === 0,
    ai_confidence: 0.4 + (index % 11) * 0.05,
    theme_stage: themeStage,
    trend_class: trend,
    stage: stageLabel,
    labels: stage === 'input' ? ['全市场候选'] : ['活跃', '趋势延续'],
    reject_reasons: [],
    degraded,
    degraded_reason: degraded ? 'PARTIAL_CACHE_FALLBACK' : undefined,
  }
}

function createPoolRange(start: number, count: number, mode: ScreenerMode, stage: 'input' | 'step1' | 'step2' | 'step3') {
  return Array.from({ length: count }, (_, offset) => createPoolRecord(start + offset, mode, stage))
}

export function createScreenerRun(params: ScreenerParams) {
  const runId = `${Date.now()}-${Math.floor(Math.random() * 10000)}`
  const mode = params.mode
  const inputCount = 5100
  const step1Config = {
    rank_start: params.step_configs?.step1?.rank_start ?? 1,
    top_n: params.step_configs?.step1?.top_n ?? params.top_n,
    turnover_threshold: params.step_configs?.step1?.turnover_threshold ?? params.turnover_threshold,
    amount_threshold: params.step_configs?.step1?.amount_threshold ?? params.amount_threshold,
    amplitude_threshold: params.step_configs?.step1?.amplitude_threshold ?? params.amplitude_threshold,
  }
  const step2Count = mode === 'strict' ? 68 : 92
  const step3Count = mode === 'strict' ? 26 : 37

  const inputPool = createPoolRange(0, inputCount, mode, 'input')
  const step1Pool = sliceRowsByScreenerRankRange(
    [...inputPool].sort((left, right) => right.ret40 - left.ret40),
    normalizeScreenerStep1RankRange(step1Config),
  )
    .filter(
      (row) =>
        row.turnover20 >= step1Config.turnover_threshold
        && row.amount20 >= step1Config.amount_threshold
        && row.amplitude20 >= step1Config.amplitude_threshold,
    )
    .slice(0, 400)
    .map((row, index) => ({ ...row, score: 78 - (index % 30), labels: ['活跃强势池'] }))
  const step2Pool = step1Pool.slice(0, step2Count).map((row, index) => ({ ...row, score: 82 - (index % 28), labels: ['图形待确认'] }))
  const step3Pool = step2Pool.slice(0, step3Count).map((row, index) => ({ ...row, score: 86 - (index % 20), labels: ['量能健康'] }))

  const step4Pool = stockPool.slice(0, mode === 'strict' ? 5 : stockPool.length).map((item, index) => {
    const source = step3Pool[index] ?? step2Pool[index] ?? step1Pool[index] ?? inputPool[index]
    return {
      ...source,
      symbol: source?.symbol ?? item.symbol,
      name: source?.name ?? item.name,
      trend_class: item.trend,
      stage: item.stage,
      theme_stage: THEME_STAGES[index % THEME_STAGES.length],
      labels: ['题材发酵', '待买观察'],
      score: 90 - index,
      ai_confidence: 0.62 + index * 0.05,
    }
  })

  const detail: ScreenerRunDetail = {
    run_id: runId,
    created_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    params,
    step_configs: {
      step1: {
        ...step1Config,
        ...normalizeScreenerStep1RankRange(step1Config),
      },
      step2: params.step_configs?.step2 ?? {
        retrace_min: 0.05,
        retrace_max: 0.25,
        max_pullback_days: 3,
        min_ma10_above_ma20_days: 5,
        min_ma5_above_ma10_days: 3,
        max_price_vs_ma20: 0.08,
        require_above_ma20: true,
        allow_b_trend: false,
      },
      step3: params.step_configs?.step3 ?? {
        min_vol_slope20: 0.05,
        min_up_down_volume_ratio: 1.3,
        max_pullback_volume_ratio: 0.9,
        allow_blowoff_top: false,
        allow_divergence_5d: false,
        allow_upper_shadow_risk: false,
        allow_degraded: false,
      },
      step4: params.step_configs?.step4 ?? {
        final_top_n: 8,
        min_ai_confidence: 0.55,
        allowed_theme_stages: ['发酵中', '高潮'],
        allow_degraded: true,
      },
    },
    step_summary: {
      input_count: inputCount,
      step1_count: step1Pool.length,
      step2_count: step2Count,
      step3_count: step3Count,
      step4_count: step4Pool.length,
      final_count: 0,
    },
    step_pools: {
      input: inputPool,
      step1: step1Pool,
      step2: step2Pool,
      step3: step3Pool,
      step4: step4Pool,
      final: [],
      watch: [],
    },
    results: step4Pool,
    degraded: step4Pool.some((item) => item.degraded),
    degraded_reason: step4Pool.some((item) => item.degraded) ? 'PARTIAL_FLOAT_SHARES_FROM_CACHE' : undefined,
  }
  runStore.set(runId, detail)
  return detail
}

export function getScreenerRun(runId: string) {
  return runStore.get(runId)
}

export function getLatestScreenerRunStore() {
  let latest: ScreenerRunDetail | undefined
  for (const detail of runStore.values()) {
    latest = detail
  }
  if (latest) {
    return latest
  }

  const seeded = createScreenerRun({
    markets: configStore.markets,
    mode: 'strict',
    as_of_date: dayjs().subtract(1, 'day').format('YYYY-MM-DD'),
    return_window_days: configStore.return_window_days,
    top_n: configStore.top_n,
    turnover_threshold: configStore.turnover_threshold,
    amount_threshold: configStore.amount_threshold,
    amplitude_threshold: configStore.amplitude_threshold,
  })
  return seeded
}

export function getStrategiesStore(): StrategyCatalogResponse {
  return {
    items: strategyCatalogStore.items.map((item) => ({
      ...item,
      capabilities: { ...item.capabilities },
      strategy_params_schema: { ...item.strategy_params_schema },
      strategy_params_defaults: { ...item.strategy_params_defaults },
    })),
  }
}

function listEventJudgmentProfilesStore(): EventJudgmentProfile[] {
  return [
    ...eventJudgmentSystemProfiles.map((item) => ({
      ...item,
      dimensions: item.dimensions.map((dim) => ({ ...dim })),
      rule_values: item.rule_values.map((rule) => ({ ...rule })),
    })),
    ...eventJudgmentCustomProfiles.map((item) => ({
      ...item,
      dimensions: item.dimensions.map((dim) => ({ ...dim })),
      rule_values: item.rule_values.map((rule) => ({ ...rule })),
    })),
  ]
}

export function getEventJudgmentCatalogStore(): EventJudgmentCatalogResponse {
  const profiles = listEventJudgmentProfilesStore()
  const activeExists = profiles.some((item) => item.profile_id === eventJudgmentActiveProfileId)
  const activeProfileId = activeExists
    ? eventJudgmentActiveProfileId
    : (profiles[0]?.profile_id || 'system_legacy_formula_v1')
  eventJudgmentActiveProfileId = activeProfileId
  return {
    active_profile_id: activeProfileId,
    metric_options: eventJudgmentMetricOptions.map((item) => ({ ...item })),
    rule_options: eventJudgmentRuleOptions.map((item) => ({ ...item })),
    profiles,
  }
}

export function upsertEventJudgmentProfileStore(payload: EventJudgmentProfileUpsertRequest): EventJudgmentProfile {
  const name = String(payload.name || '').trim() || '自定义模板'
  const description = String(payload.description || '').trim()
  const dimensions = (Array.isArray(payload.dimensions) ? payload.dimensions : []).map((item, index) => ({
    dimension_id: String(item.dimension_id || `dim_${index + 1}`),
    label: String(item.label || item.metric_key || `维度${index + 1}`),
    metric_key: String(item.metric_key || ''),
    weight: Number(item.weight ?? 1),
    invert: Boolean(item.invert),
    enabled: Boolean(item.enabled),
  }))
  const baseRuleMap = new Map<string, number | boolean>()
  eventJudgmentRuleOptions.forEach((item) => {
    baseRuleMap.set(item.rule_key, item.default_value)
  })
  const sourceProfile = listEventJudgmentProfilesStore().find((item) => item.profile_id === String(payload.profile_id || '').trim())
  if (sourceProfile) {
    sourceProfile.rule_values.forEach((item) => {
      baseRuleMap.set(item.rule_key, item.value)
    })
  }
  if (Array.isArray(payload.rule_values)) {
    payload.rule_values.forEach((item) => {
      baseRuleMap.set(String(item.rule_key || '').trim(), item.value)
    })
  }
  const ruleValues: EventJudgmentRuleValue[] = eventJudgmentRuleOptions
    .map((item) => ({
      rule_key: item.rule_key,
      value: baseRuleMap.get(item.rule_key) ?? item.default_value,
    }))
    .filter((item) => item.rule_key.length > 0)
  const now = dayjs().format('YYYY-MM-DD HH:mm:ss')
  const profileId = String(payload.profile_id || '').trim() || `ejp_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
  const next: EventJudgmentProfile = {
    profile_id: profileId,
    name,
    description,
    score_mode: 'dimension_weighted',
    is_system: false,
    updated_at: now,
    dimensions,
    rule_values: ruleValues,
  }
  eventJudgmentCustomProfiles = [next, ...eventJudgmentCustomProfiles.filter((item) => item.profile_id !== profileId)]
  if (payload.make_active !== false) {
    eventJudgmentActiveProfileId = profileId
  }
  return {
    ...next,
    dimensions: next.dimensions.map((item) => ({ ...item })),
    rule_values: next.rule_values.map((item) => ({ ...item })),
  }
}

export function applyEventJudgmentProfileStore(payload: EventJudgmentProfileApplyRequest): EventJudgmentCatalogResponse {
  const profileId = String(payload.profile_id || '').trim()
  const exists = listEventJudgmentProfilesStore().some((item) => item.profile_id === profileId)
  if (exists) {
    eventJudgmentActiveProfileId = profileId
  }
  return getEventJudgmentCatalogStore()
}

export function deleteEventJudgmentProfileStore(profileIdRaw: string): EventJudgmentProfileDeleteResponse {
  const profileId = String(profileIdRaw || '').trim()
  eventJudgmentCustomProfiles = eventJudgmentCustomProfiles.filter((item) => item.profile_id !== profileId)
  if (eventJudgmentActiveProfileId === profileId) {
    eventJudgmentActiveProfileId = 'system_legacy_formula_v1'
  }
  return { success: true, profile_id: profileId }
}

export function getCandlePayload(symbol: string) {
  const candles = ensureCandles(symbol)
  const degraded = candles.some((point) => point.price_source === 'approx')
  return {
    symbol,
    candles,
    degraded,
    degraded_reason: degraded ? 'MINUTE_DATA_MISSING_PARTIAL' : undefined,
  }
}

export function getIntradayPayload(symbol: string, date: string) {
  const candles = ensureCandles(symbol)
  const fallbackDate = candles[candles.length - 1]?.time ?? dayjs().format('YYYY-MM-DD')
  const matched = candles.find((item) => item.time === date)
  const targetDate = matched ? date : fallbackDate
  const basePrice = matched?.close ?? candles[candles.length - 1]?.close ?? 20
  const points = genIntradayPoints(symbol, targetDate, basePrice)
  const degraded = points.some((point) => point.price_source === 'approx')

  return {
    symbol,
    date: targetDate,
    points,
    degraded,
    degraded_reason: degraded ? 'MINUTE_DATA_PARTIAL_APPROX' : undefined,
  }
}

export function getAnalysis(symbol: string): StockAnalysisResponse {
  const base = stockPool.find((stock) => stock.symbol === symbol)
  const analysis: StockAnalysis = {
    symbol,
    suggest_start_date: dayjs().subtract(53, 'day').format('YYYY-MM-DD'),
    suggest_stage: (base?.stage ?? 'Mid') as StockAnalysis['suggest_stage'],
    suggest_trend_class: (base?.trend ?? 'Unknown') as TrendClass,
    confidence: 0.74,
    reason: '均线结构稳定，回调量能可控，板块热度仍在发酵。',
    theme_stage: ('发酵中' as ThemeStage),
    degraded: symbol === 'sz002230',
    degraded_reason: symbol === 'sz002230' ? 'AI_TIMEOUT_CACHE_FALLBACK' : undefined,
  }
  const signal = getSignals({ min_score: 0, min_event_count: 0 }).items.find((item) => item.symbol === symbol)
  return {
    analysis,
    annotation: annotationStore.get(symbol),
    signal,
  }
}

export function saveAnnotation(annotation: StockAnnotation) {
  annotationStore.set(annotation.symbol, annotation)
  return annotation
}

function detectPrimaryBoardFromSymbol(symbol: string): Exclude<BoardFilter, 'st'> | null {
  const normalized = String(symbol).trim().toLowerCase()
  if (normalized.length < 8) return null
  const market = normalized.slice(0, 2)
  const code = normalized.slice(2)
  if (market === 'bj') return 'beijing'
  if (market === 'sh') return code.startsWith('688') || code.startsWith('689') ? 'star' : 'main'
  if (market === 'sz') return code.startsWith('300') || code.startsWith('301') ? 'gem' : 'main'
  return null
}

function isStStock(name: string) {
  return String(name).replace(/\s+/g, '').toUpperCase().includes('ST')
}

function matchesBoardFilters(symbol: string, name: string, boardFilters: BoardFilter[]) {
  if (!boardFilters.length) return true
  const selected = new Set(boardFilters)
  const st = isStStock(name)
  if (st && !selected.has('st')) return false
  const selectedBoards = boardFilters.filter((item) => item !== 'st')
  if (!selectedBoards.length) return st
  const board = detectPrimaryBoardFromSymbol(symbol)
  if (!board) return false
  return selectedBoards.includes(board)
}

function matchesMarketFilters(symbol: string, marketFilters: Market[]) {
  if (!marketFilters.length) return true
  const normalized = String(symbol).trim().toLowerCase()
  if (normalized.length < 2) return false
  const market = normalized.slice(0, 2)
  return marketFilters.includes(market as Market)
}

export function getSignals(params?: {
  mode?: SignalScanMode
  market_filters?: Market[]
  board_filters?: BoardFilter[]
  as_of_date?: string
  window_days?: number
  min_score?: number
  require_sequence?: boolean
  min_event_count?: number
  signal_age_min?: number
  signal_age_max?: number
}): SignalsResponse {
  const raw: Array<{ symbol: string; name: string; trigger_reason: string; signals: Array<'A' | 'B' | 'C'> }> = [
    { symbol: 'sz300750', name: '宁德时代', trigger_reason: '突破前高后缩量回踩确认', signals: ['A', 'B'] },
    { symbol: 'sh601899', name: '紫金矿业', trigger_reason: '板块分歧后转一致', signals: ['A', 'C'] },
    { symbol: 'sh600519', name: '贵州茅台', trigger_reason: 'MA10回踩确认', signals: ['A'] },
  ]
  const mode = params?.mode ?? 'trend_pool'
  const marketFilters = Array.from(
    new Set((params?.market_filters ?? []).filter((item): item is Market => ALLOWED_MARKET_FILTERS.includes(item))),
  )
  const boardFilters = Array.from(
    new Set((params?.board_filters ?? []).filter((item): item is BoardFilter => ALLOWED_BOARD_FILTERS.includes(item))),
  )
  const minScore = params?.min_score ?? 60
  const minEventCount = params?.min_event_count ?? 1
  const signalAgeMin = Math.max(0, Math.round(params?.signal_age_min ?? 0))
  const signalAgeMax = typeof params?.signal_age_max === 'number'
    ? Math.max(signalAgeMin, Math.round(params.signal_age_max))
    : null
  const requireSequence = params?.require_sequence ?? false
  const asOfDate = dayjs(params?.as_of_date || dayjs().format('YYYY-MM-DD'))

  const items: SignalResult[] = raw.map((item, index) => {
    const resolved = resolveSignalPriority(item.signals)
    const wyEvents = ['SC', 'AR', 'ST', 'SOS', 'LPS'].slice(0, 3 + (index % 3))
    const wyRisk = index === 1 ? ['UTAD'] : []
    const score = 70 + (2 - index) * 8 - wyRisk.length * 6
    const triggerDate = dayjs().subtract(index, 'day').format('YYYY-MM-DD')
    const signalAgeDays = Math.max(0, asOfDate.diff(dayjs(triggerDate), 'day'))
    return {
      symbol: item.symbol,
      name: item.name,
      primary_signal: resolved.primary ?? 'C',
      secondary_signals: resolved.secondary,
      trigger_date: triggerDate,
      expire_date: dayjs().add(2 - index, 'day').format('YYYY-MM-DD'),
      signal_age_days: signalAgeDays,
      trigger_reason: item.trigger_reason,
      priority: resolved.primary === 'B' ? 3 : resolved.primary === 'A' ? 2 : 1,
      wyckoff_phase: index === 0 ? '吸筹D' : index === 1 ? '派发A' : '吸筹B',
      wyckoff_signal: wyEvents[wyEvents.length - 1] ?? '',
      structure_hhh: index === 0 ? 'HH|HL|HC' : index === 1 ? 'HH|-|-' : '-|HL|-',
      wy_event_count: wyEvents.length,
      wy_sequence_ok: index !== 2,
      entry_quality_score: score,
      wy_events: wyEvents,
      wy_risk_events: wyRisk,
      phase_hint: index === 1 ? '出现派发迹象，建议降低仓位并谨慎追高。' : '结构偏强，可继续观察回踩承接。',
      scan_mode: mode,
      event_strength_score: 65 + index * 4,
      phase_score: 60 + index * 5,
      structure_score: 55 + index * 8,
      trend_score: 62 + index * 6,
      volatility_score: 58 + index * 4,
    }
  })

  const marketMatched = items.filter((row) => matchesMarketFilters(row.symbol, marketFilters))
  const boardMatched = marketMatched.filter((row) => matchesBoardFilters(row.symbol, row.name, boardFilters))
  const filtered = boardMatched.filter((row) => {
    if ((row.entry_quality_score ?? 0) < minScore) return false
    if ((row.wy_event_count ?? 0) < minEventCount) return false
    if ((row.signal_age_days ?? 0) < signalAgeMin) return false
    if (signalAgeMax !== null && (row.signal_age_days ?? 0) > signalAgeMax) return false
    if (requireSequence && !row.wy_sequence_ok) return false
    return true
  })

  return {
    items: filtered,
    mode,
    generated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    cache_hit: false,
    degraded: false,
    source_count: boardMatched.length,
  }
}

function applyOrderFill(order: SimTradeOrder): SimTradeFill | null {
  const symbol = order.symbol
  const submitDate = order.submit_date
  const fillPrice = Number(order.estimated_price ?? 20)
  const grossAmount = fillPrice * order.quantity
  const commission = Math.max(grossAmount * simConfigStore.commission_rate, simConfigStore.min_commission)
  const stampTax = order.side === 'sell' ? grossAmount * simConfigStore.stamp_tax_rate : 0
  const transferFee = grossAmount * simConfigStore.transfer_fee_rate
  const totalFee = commission + stampTax + transferFee

  if (order.side === 'buy' && simCash < grossAmount + totalFee) {
    order.status = 'rejected'
    order.reject_reason = 'SIM_INSUFFICIENT_CASH'
    order.status_reason = 'SIM_INSUFFICIENT_CASH'
    order.cash_impact = 0
    order.filled_date = undefined
    return null
  }

  const available = simLotsStore.filter((lot) => lot.symbol === symbol).reduce((sum, lot) => sum + lot.remaining, 0)
  if (order.side === 'sell' && available < order.quantity) {
    order.status = 'rejected'
    order.reject_reason = 'SIM_INSUFFICIENT_POSITION'
    order.status_reason = 'SIM_INSUFFICIENT_POSITION'
    order.cash_impact = 0
    order.filled_date = undefined
    return null
  }

  const fill: SimTradeFill = {
    order_id: order.order_id,
    symbol,
    side: order.side,
    quantity: order.quantity,
    fill_date: submitDate,
    fill_price: fillPrice,
    price_source: 'vwap',
    gross_amount: Number(grossAmount.toFixed(4)),
    net_amount: Number((order.side === 'buy' ? -(grossAmount + totalFee) : grossAmount - totalFee).toFixed(4)),
    fee_commission: Number(commission.toFixed(4)),
    fee_stamp_tax: Number(stampTax.toFixed(4)),
    fee_transfer: Number(transferFee.toFixed(4)),
  }

  if (order.side === 'buy') {
    simCash -= grossAmount + totalFee
    simLotsStore.push({
      symbol,
      buy_date: submitDate,
      buy_price: fillPrice,
      unit_cost: (grossAmount + totalFee) / order.quantity,
      quantity: order.quantity,
      remaining: order.quantity,
    })
  } else {
    simCash += grossAmount - totalFee
    let remaining = order.quantity
    for (const lot of simLotsStore) {
      if (lot.symbol !== symbol || lot.remaining <= 0 || remaining <= 0) continue
      const take = Math.min(lot.remaining, remaining)
      const buyCost = lot.unit_cost * take
      const sellFeePart = totalFee * (take / order.quantity)
      const sellNet = fillPrice * take - sellFeePart
      const pnlAmount = sellNet - buyCost
      simClosedTradesStore.push({
        symbol,
        buy_date: lot.buy_date,
        buy_price: lot.buy_price,
        sell_date: submitDate,
        sell_price: fillPrice,
        quantity: take,
        holding_days: Math.max(0, dayjs(submitDate).diff(dayjs(lot.buy_date), 'day')),
        pnl_amount: Number(pnlAmount.toFixed(4)),
        pnl_ratio: buyCost > 0 ? Number((pnlAmount / buyCost).toFixed(6)) : 0,
      })
      lot.remaining -= take
      remaining -= take
    }
    simLotsStore = simLotsStore.filter((lot) => lot.remaining > 0)
  }

  simCash = Number(simCash.toFixed(4))
  order.status = 'filled'
  order.filled_date = submitDate
  order.reject_reason = undefined
  order.status_reason = undefined
  order.cash_impact = fill.net_amount
  simFillsStore = [fill, ...simFillsStore]
  return fill
}

export function createOrder(payload: {
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  signal_date: string
  submit_date: string
}) {
  const symbol = payload.symbol.trim().toLowerCase()
  const orderId = `ord-${Date.now()}-${Math.floor(Math.random() * 1000)}`
  const submitDate = payload.submit_date || dayjs().format('YYYY-MM-DD')
  const candles = ensureCandles(symbol)
  const latest = candles[candles.length - 1]
  const basePrice = latest?.open ?? latest?.close ?? 20
  const slippage = simConfigStore.slippage_rate
  const estimatedPrice = Number(
    (
      payload.side === 'buy'
        ? basePrice * (1 + slippage)
        : basePrice * (1 - slippage)
    ).toFixed(4),
  )
  const estimatedAmount = estimatedPrice * payload.quantity
  const estimatedCommission = Math.max(estimatedAmount * simConfigStore.commission_rate, simConfigStore.min_commission)
  const estimatedStamp = payload.side === 'sell' ? estimatedAmount * simConfigStore.stamp_tax_rate : 0
  const estimatedTransfer = estimatedAmount * simConfigStore.transfer_fee_rate
  const estimatedFee = estimatedCommission + estimatedStamp + estimatedTransfer

  const baseOrder: SimTradeOrder = {
    order_id: orderId,
    symbol,
    side: payload.side,
    quantity: payload.quantity,
    signal_date: payload.signal_date,
    submit_date: submitDate,
    expected_fill_date: submitDate,
    filled_date: undefined,
    estimated_price: estimatedPrice,
    cash_impact: payload.side === 'buy' ? -(estimatedAmount + estimatedFee) : estimatedAmount - estimatedFee,
    status: 'pending',
  }

  if (payload.quantity % 100 !== 0 || payload.quantity <= 0) {
    baseOrder.status = 'rejected'
    baseOrder.reject_reason = 'SIM_INVALID_LOT_SIZE'
    baseOrder.status_reason = 'SIM_INVALID_LOT_SIZE'
    baseOrder.cash_impact = 0
  } else if (payload.side === 'sell') {
    const available = simLotsStore.filter((lot) => lot.symbol === symbol).reduce((sum, lot) => sum + lot.remaining, 0)
    if (available < payload.quantity) {
      baseOrder.status = 'rejected'
      baseOrder.reject_reason = 'SIM_INSUFFICIENT_POSITION'
      baseOrder.status_reason = 'SIM_INSUFFICIENT_POSITION'
      baseOrder.cash_impact = 0
    }
  } else if (payload.side === 'buy' && simCash < estimatedAmount + estimatedFee) {
    baseOrder.status = 'rejected'
    baseOrder.reject_reason = 'SIM_INSUFFICIENT_CASH'
    baseOrder.status_reason = 'SIM_INSUFFICIENT_CASH'
    baseOrder.cash_impact = 0
  }

  simOrdersStore = [baseOrder, ...simOrdersStore]
  return { order: baseOrder }
}

export function getPortfolio(): PortfolioSnapshot {
  settleOrders()
  const grouped = new Map<string, { quantity: number; cost: number; earliestBuy: string }>()
  simLotsStore.forEach((lot) => {
    const prev = grouped.get(lot.symbol)
    const remainingCost = lot.unit_cost * lot.remaining
    if (!prev) {
      grouped.set(lot.symbol, {
        quantity: lot.remaining,
        cost: remainingCost,
        earliestBuy: lot.buy_date,
      })
      return
    }
    grouped.set(lot.symbol, {
      quantity: prev.quantity + lot.remaining,
      cost: prev.cost + remainingCost,
      earliestBuy: prev.earliestBuy < lot.buy_date ? prev.earliestBuy : lot.buy_date,
    })
  })

  const positions: PortfolioPosition[] = Array.from(grouped.entries()).map(([symbol, item]) => {
    const candles = ensureCandles(symbol)
    const latest = candles[candles.length - 1]
    const currentPrice = latest?.close ?? 0
    const marketValue = currentPrice * item.quantity
    const pnlAmount = marketValue - item.cost
    return {
      symbol,
      name: stockPool.find((row) => row.symbol === symbol)?.name ?? symbol.toUpperCase(),
      quantity: item.quantity,
      available_quantity: item.quantity,
      avg_cost: item.quantity > 0 ? Number((item.cost / item.quantity).toFixed(4)) : 0,
      current_price: Number(currentPrice.toFixed(4)),
      market_value: Number(marketValue.toFixed(4)),
      pnl_amount: Number(pnlAmount.toFixed(4)),
      pnl_ratio: item.cost > 0 ? Number((pnlAmount / item.cost).toFixed(6)) : 0,
      holding_days: Math.max(0, dayjs().diff(dayjs(item.earliestBuy), 'day')),
    }
  })

  const positionValue = positions.reduce((sum, item) => sum + item.market_value, 0)
  const realizedPnl = simClosedTradesStore.reduce((sum, item) => sum + item.pnl_amount, 0)
  const unrealizedPnl = positions.reduce((sum, item) => sum + item.pnl_amount, 0)
  return {
    as_of_date: dayjs().format('YYYY-MM-DD'),
    total_asset: Number((simCash + positionValue).toFixed(4)),
    cash: simCash,
    position_value: Number(positionValue.toFixed(4)),
    realized_pnl: Number(realizedPnl.toFixed(4)),
    unrealized_pnl: Number(unrealizedPnl.toFixed(4)),
    pending_order_count: simOrdersStore.filter((item) => item.status === 'pending').length,
    positions,
  }
}

export function getReview(params?: {
  date_from?: string
  date_to?: string
  date_axis?: 'sell' | 'buy'
}): ReviewResponse {
  settleOrders()
  const now = dayjs()
  const dateFrom = params?.date_from ?? now.subtract(90, 'day').format('YYYY-MM-DD')
  const dateTo = params?.date_to ?? now.format('YYYY-MM-DD')
  const dateAxis = params?.date_axis === 'buy' ? 'buy' : 'sell'
  const resolveAxisDate = (trade: TradeRecord) => (dateAxis === 'buy' ? trade.buy_date : trade.sell_date)
  const trades = simClosedTradesStore.filter((item) => {
    const axisDate = resolveAxisDate(item)
    return axisDate >= dateFrom && axisDate <= dateTo
  })
  const tradeCount = trades.length
  const winCount = trades.filter((row) => row.pnl_amount > 0).length
  const lossCount = trades.filter((row) => row.pnl_amount < 0).length
  const grossProfit = trades.filter((row) => row.pnl_amount > 0).reduce((sum, row) => sum + row.pnl_amount, 0)
  const grossLoss = trades.filter((row) => row.pnl_amount < 0).reduce((sum, row) => sum + row.pnl_amount, 0)
  const totalPnl = trades.reduce((sum, row) => sum + row.pnl_amount, 0)
  const avgPnlRatio = tradeCount > 0 ? trades.reduce((sum, row) => sum + row.pnl_ratio, 0) / tradeCount : 0

  const equityCurve = [{ date: dateFrom, equity: simConfigStore.initial_capital, realized_pnl: 0 }]
  let runningPnl = 0
  trades
    .slice()
    .sort((a, b) => resolveAxisDate(a).localeCompare(resolveAxisDate(b)))
    .forEach((row) => {
      runningPnl += row.pnl_amount
      equityCurve.push({
        date: resolveAxisDate(row),
        equity: Number((simConfigStore.initial_capital + runningPnl).toFixed(4)),
        realized_pnl: Number(runningPnl.toFixed(4)),
      })
    })

  if (equityCurve[equityCurve.length - 1]?.date !== dateTo) {
    equityCurve.push({
      date: dateTo,
      equity: equityCurve[equityCurve.length - 1]?.equity ?? simConfigStore.initial_capital,
      realized_pnl: equityCurve[equityCurve.length - 1]?.realized_pnl ?? 0,
    })
  }

  let peak = equityCurve[0]?.equity ?? simConfigStore.initial_capital
  const drawdownCurve = equityCurve.map((point) => {
    peak = Math.max(peak, point.equity)
    return {
      date: point.date,
      drawdown: peak > 0 ? Number(((point.equity - peak) / peak).toFixed(6)) : 0,
    }
  })

  const monthlyMap = new Map<string, { pnl: number; count: number }>()
  trades.forEach((trade) => {
    const key = resolveAxisDate(trade).slice(0, 7)
    const prev = monthlyMap.get(key) ?? { pnl: 0, count: 0 }
    monthlyMap.set(key, { pnl: prev.pnl + trade.pnl_amount, count: prev.count + 1 })
  })

  const monthlyReturns = Array.from(monthlyMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, value]) => ({
      month,
      return_ratio: Number((value.pnl / simConfigStore.initial_capital).toFixed(6)),
      pnl_amount: Number(value.pnl.toFixed(4)),
      trade_count: value.count,
    }))

  const sortedByPnl = trades.slice().sort((a, b) => b.pnl_amount - a.pnl_amount)
  const topTrades = sortedByPnl.slice(0, 10)
  const bottomTrades = sortedByPnl.slice(-10).reverse()

  const maxDrawdown = Math.max(0, ...drawdownCurve.map((item) => Math.abs(item.drawdown)))
  return {
    stats: {
      win_rate: tradeCount > 0 ? winCount / tradeCount : 0,
      total_return: totalPnl / simConfigStore.initial_capital,
      max_drawdown: maxDrawdown,
      avg_pnl_ratio: avgPnlRatio,
      trade_count: tradeCount,
      win_count: winCount,
      loss_count: lossCount,
      profit_factor: grossLoss < 0 ? grossProfit / Math.abs(grossLoss) : grossProfit > 0 ? 999 : 0,
    },
    trades,
    equity_curve: equityCurve,
    drawdown_curve: drawdownCurve,
    monthly_returns: monthlyReturns,
    top_trades: topTrades,
    bottom_trades: bottomTrades,
    cost_snapshot: simConfigStore,
    range: {
      date_from: dateFrom,
      date_to: dateTo,
      date_axis: dateAxis,
    },
  }
}

export function runBacktestStore(payload: BacktestRunRequest): BacktestResponse {
  let dateFrom = dayjs(payload.date_from)
  let dateTo = dayjs(payload.date_to)
  if (!dateFrom.isValid()) dateFrom = dayjs().subtract(180, 'day')
  if (!dateTo.isValid()) dateTo = dayjs()
  if (dateFrom.isAfter(dateTo)) {
    const swap = dateFrom
    dateFrom = dateTo
    dateTo = swap
  }
  const rangeFrom = dateFrom.format('YYYY-MM-DD')
  const rangeTo = dateTo.format('YYYY-MM-DD')

  const notes: string[] = []
  const entryDelayDays = Math.max(1, Math.min(5, Math.round(payload.entry_delay_days ?? 1)))
  const delayInvalidationEnabled = payload.delay_invalidation_enabled !== false
  let delayInvalidationSkips = 0
  let delayNoEntryDaySkips = 0
  if (payload.mode === 'trend_pool' && !payload.run_id?.trim()) {
    notes.push('Mock 模式未传 run_id，已按当前信号池模拟趋势池回测。')
  }
  if (payload.priority_topk_per_day > 0 && !payload.prioritize_signals) {
    notes.push('未启用优先排序，priority_topk_per_day 配置未生效。')
  }
  if (entryDelayDays !== 1) {
    notes.push(`延迟入场已启用：entry_delay_days=${entryDelayDays}（交易日）`)
  }
  if (delayInvalidationEnabled) {
    notes.push('延迟窗口失效保护已启用。')
  }

  const signalRows = getSignals({
    mode: payload.mode,
    window_days: payload.window_days,
    min_score: payload.min_score,
    require_sequence: payload.require_sequence,
    min_event_count: payload.min_event_count,
  }).items
  const candidatesRaw = signalRows.slice(0, Math.max(0, payload.max_symbols))

  type Candidate = {
    symbol: string
    name: string
    signal_date: string
    entry_date: string
    exit_date: string
    entry_signal: string
    entry_phase: string
    entry_quality_score: number
    entry_phase_score: number
    entry_events_weight: number
    entry_trend_score: number
    entry_structure_score: number
    exit_reason: string
    entry_price: number
    exit_price: number
    holding_days: number
  }

  const candidateRows: Candidate[] = candidatesRaw
    .map((row, index) => {
      const candles = ensureCandles(row.symbol)
      if (candles.length < 10) return null

      const entrySignal = row.wyckoff_signal && payload.entry_events.includes(row.wyckoff_signal)
        ? row.wyckoff_signal
        : payload.entry_events[0] || row.wyckoff_signal || 'Signal'

      let signalDay = dayjs(row.trigger_date)
      if (!signalDay.isValid()) signalDay = dateFrom.add(index % 10, 'day')
      if (signalDay.isBefore(dateFrom)) signalDay = dateFrom
      if (signalDay.isAfter(dateTo)) signalDay = dateTo

      if (
        delayInvalidationEnabled
        && entryDelayDays > 1
        && (row.wy_risk_events ?? []).some((event) => ['UTAD', 'SOW', 'LPSY'].includes(String(event)))
      ) {
        delayInvalidationSkips += 1
        return null
      }

      let entryDay = signalDay.add(entryDelayDays, 'day')
      if (entryDay.isBefore(dateFrom)) entryDay = dateFrom
      if (entryDay.isAfter(dateTo)) {
        delayNoEntryDaySkips += 1
        return null
      }

      const proposedHoldDays = Math.max(2, Math.min(payload.max_hold_days, 3 + (index % 18)))
      let exitDay = entryDay.add(proposedHoldDays, 'day')
      if (exitDay.isAfter(dateTo)) exitDay = dateTo
      if (payload.enforce_t1 && !exitDay.isAfter(entryDay)) {
        const forced = entryDay.add(1, 'day')
        if (forced.isAfter(dateTo)) return null
        exitDay = forced
      }

      const resolvePrice = (targetDate: dayjs.Dayjs) => {
        const target = targetDate.format('YYYY-MM-DD')
        let chosen = candles[0]?.close ?? 20
        for (const candle of candles) {
          if (candle.time <= target) {
            chosen = candle.close
          } else {
            break
          }
        }
        return Math.max(0.01, chosen)
      }

      const entryPrice = resolvePrice(entryDay)
      const quality = row.entry_quality_score ?? 60
      const rawReturn = ((index % 11) - 5) / 100 + (quality - 60) / 500
      const lossCap = payload.stop_loss > 0 ? -payload.stop_loss * 0.95 : -0.12
      const profitCap = payload.take_profit > 0 ? payload.take_profit * 0.95 : 0.2
      const pnlRatio = clamp(rawReturn, lossCap, profitCap)
      const exitPrice = Math.max(0.01, entryPrice * (1 + pnlRatio))

      const pickedExitEvent = payload.exit_events[index % Math.max(1, payload.exit_events.length)] || 'EVENT'
      let exitReason = `event_exit:${pickedExitEvent}`
      if (payload.stop_loss > 0 && pnlRatio <= -payload.stop_loss * 0.9) {
        exitReason = 'stop_loss'
      } else if (payload.take_profit > 0 && pnlRatio >= payload.take_profit * 0.85) {
        exitReason = 'take_profit'
      } else if (index % 3 !== 0) {
        exitReason = index % 2 === 0 ? `event_exit:${pickedExitEvent}` : 'time_exit'
      }

      const phase = row.wyckoff_phase || '阶段未明'
      const entryEventsWeight = (row.wy_events || [])
        .filter((event) => payload.entry_events.includes(event))
        .reduce((sum, event) => sum + ({ PS: 1.0, SC: 1.2, AR: 1.4, ST: 1.6, TSO: 2.5, Spring: 3.0, SOS: 3.4, JOC: 4.0, LPS: 2.8, UTAD: 1.5, SOW: 1.5, LPSY: 1.3 }[event] ?? 1.0), 0)
      const phaseScore = phase.startsWith('吸筹') ? 2.0 : phase.startsWith('派发') ? -1.5 : 0
      const structureScore = String(row.structure_hhh || '-')
        .split('|')
        .reduce((sum, token) => sum + (token && token !== '-' ? 1 : 0), 0)

      return {
        symbol: row.symbol,
        name: row.name,
        signal_date: signalDay.format('YYYY-MM-DD'),
        entry_date: entryDay.format('YYYY-MM-DD'),
        exit_date: exitDay.format('YYYY-MM-DD'),
        entry_signal: entrySignal,
        entry_phase: phase,
        entry_quality_score: quality,
        entry_phase_score: phaseScore,
        entry_events_weight: entryEventsWeight,
        entry_trend_score: row.trend_score ?? 50,
        entry_structure_score: structureScore,
        exit_reason: exitReason,
        entry_price: Number(entryPrice.toFixed(4)),
        exit_price: Number(exitPrice.toFixed(4)),
        holding_days: Math.max(1, exitDay.diff(entryDay, 'day')),
      } satisfies Candidate
    })
    .filter((row): row is Candidate => Boolean(row))

  if (delayInvalidationSkips > 0 || delayNoEntryDaySkips > 0) {
    const detail = [
      delayInvalidationSkips > 0 ? `delay_invalidated_by_risk_event:${delayInvalidationSkips}` : '',
      delayNoEntryDaySkips > 0 ? `delay_no_entry_day:${delayNoEntryDaySkips}` : '',
    ]
      .filter(Boolean)
      .join(', ')
    notes.push(`延迟窗口跳过 ${delayInvalidationSkips + delayNoEntryDaySkips} 笔信号（${detail}）。`)
  }

  if (payload.prioritize_signals) {
    candidateRows.sort((a, b) => {
      if (a.entry_date !== b.entry_date) return a.entry_date.localeCompare(b.entry_date)
      if (payload.priority_mode === 'phase_first') {
        if (b.entry_phase_score !== a.entry_phase_score) return b.entry_phase_score - a.entry_phase_score
      } else if (payload.priority_mode === 'momentum') {
        if (b.entry_trend_score !== a.entry_trend_score) return b.entry_trend_score - a.entry_trend_score
      } else if (b.entry_quality_score !== a.entry_quality_score) {
        return b.entry_quality_score - a.entry_quality_score
      }
      if (b.entry_events_weight !== a.entry_events_weight) return b.entry_events_weight - a.entry_events_weight
      return a.symbol.localeCompare(b.symbol)
    })
    notes.push(`同日信号按优先级执行（模式: ${payload.priority_mode}）。`)
  } else {
    candidateRows.sort((a, b) => (a.entry_date === b.entry_date ? a.symbol.localeCompare(b.symbol) : a.entry_date.localeCompare(b.entry_date)))
  }

  if (payload.prioritize_signals && payload.priority_topk_per_day > 0) {
    const beforeCount = candidateRows.length
    const counter = new Map<string, number>()
    const kept = candidateRows.filter((row) => {
      const used = counter.get(row.signal_date) ?? 0
      if (used >= payload.priority_topk_per_day) return false
      counter.set(row.signal_date, used + 1)
      return true
    })
    candidateRows.length = 0
    candidateRows.push(...kept)
    const dropped = beforeCount - kept.length
    if (dropped > 0) {
      notes.push(`同日 TopK 限流已生效：每日保留前 ${payload.priority_topk_per_day} 笔候选，共过滤 ${dropped} 笔。`)
    }
  }

  const feeRate = clamp(payload.fee_bps / 10000, 0, 0.05)
  let cash = payload.initial_capital
  let equity = payload.initial_capital
  let maxConcurrentPositions = 0
  const activePositions: Array<{ exit_date: string; exit_amount: number; pnl_amount: number }> = []
  const trades: BacktestTrade[] = []
  const skipReasons = {
    max_positions: 0,
    insufficient_cash: 0,
    invalid_price: 0,
  }

  const releaseUntil = (entryDate: string) => {
    const remaining: typeof activePositions = []
    activePositions.forEach((row) => {
      if (row.exit_date < entryDate) {
        cash += row.exit_amount
        equity += row.pnl_amount
      } else {
        remaining.push(row)
      }
    })
    activePositions.length = 0
    activePositions.push(...remaining)
  }

  candidateRows.forEach((row) => {
    releaseUntil(row.entry_date)
    if (activePositions.length >= payload.max_positions) {
      skipReasons.max_positions += 1
      return
    }

    const entryExec = row.entry_price * (1 + feeRate)
    const exitExec = row.exit_price * (1 - feeRate)
    if (!Number.isFinite(entryExec) || !Number.isFinite(exitExec) || entryExec <= 0) {
      skipReasons.invalid_price += 1
      return
    }

    const allocation = Math.min(cash, Math.max(0, equity * payload.position_pct))
    const quantity = Math.floor(allocation / entryExec / 100) * 100
    if (quantity <= 0) {
      skipReasons.insufficient_cash += 1
      return
    }

    const invested = quantity * entryExec
    if (invested <= 0 || invested > cash + 1e-9) {
      skipReasons.insufficient_cash += 1
      return
    }

    const exitAmount = quantity * exitExec
    const pnlAmount = exitAmount - invested
    const pnlRatio = invested > 0 ? pnlAmount / invested : 0
    cash -= invested

    activePositions.push({
      exit_date: row.exit_date,
      exit_amount: exitAmount,
      pnl_amount: pnlAmount,
    })
    maxConcurrentPositions = Math.max(maxConcurrentPositions, activePositions.length)

    trades.push({
      symbol: row.symbol,
      name: row.name,
      signal_date: row.signal_date,
      entry_date: row.entry_date,
      exit_date: row.exit_date,
      entry_signal: row.entry_signal,
      entry_phase: row.entry_phase,
      entry_quality_score: Number(row.entry_quality_score.toFixed(2)),
      exit_reason: row.exit_reason,
      quantity,
      entry_price: row.entry_price,
      exit_price: row.exit_price,
      holding_days: row.holding_days,
      pnl_amount: Number(pnlAmount.toFixed(4)),
      pnl_ratio: Number(pnlRatio.toFixed(6)),
    })
  })

  activePositions
    .slice()
    .sort((a, b) => a.exit_date.localeCompare(b.exit_date))
    .forEach((row) => {
      cash += row.exit_amount
      equity += row.pnl_amount
    })

  const candidateCount = candidateRows.length
  const skippedCount = Object.values(skipReasons).reduce((sum, value) => sum + value, 0)
  const fillRate = candidateCount > 0 ? trades.length / candidateCount : 0
  if (skippedCount > 0) {
    const detail = Object.entries(skipReasons)
      .filter(([, value]) => value > 0)
      .map(([key, value]) => `${key}:${value}`)
      .join(', ')
    notes.push(`组合约束跳过 ${skippedCount} 笔信号（${detail}）。`)
  }

  const tradeCount = trades.length
  const winCount = trades.filter((row) => row.pnl_amount > 0).length
  const lossCount = trades.filter((row) => row.pnl_amount < 0).length
  const grossProfit = trades.filter((row) => row.pnl_amount > 0).reduce((sum, row) => sum + row.pnl_amount, 0)
  const grossLoss = trades.filter((row) => row.pnl_amount < 0).reduce((sum, row) => sum + row.pnl_amount, 0)
  const totalPnl = trades.reduce((sum, row) => sum + row.pnl_amount, 0)
  const avgPnlRatio = tradeCount > 0 ? trades.reduce((sum, row) => sum + row.pnl_ratio, 0) / tradeCount : 0

  const pnlByDate = new Map<string, number>()
  trades.forEach((row) => {
    pnlByDate.set(row.exit_date, (pnlByDate.get(row.exit_date) ?? 0) + row.pnl_amount)
  })

  const equityCurve: BacktestResponse['equity_curve'] = [
    {
      date: rangeFrom,
      equity: Number(payload.initial_capital.toFixed(4)),
      realized_pnl: 0,
    },
  ]
  let runningPnl = 0
  Array.from(pnlByDate.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .forEach(([date, pnl]) => {
      runningPnl += pnl
      equityCurve.push({
        date,
        equity: Number((payload.initial_capital + runningPnl).toFixed(4)),
        realized_pnl: Number(runningPnl.toFixed(4)),
      })
    })
  if (equityCurve[equityCurve.length - 1]?.date !== rangeTo) {
    equityCurve.push({
      date: rangeTo,
      equity: equityCurve[equityCurve.length - 1]?.equity ?? payload.initial_capital,
      realized_pnl: equityCurve[equityCurve.length - 1]?.realized_pnl ?? runningPnl,
    })
  }

  let peak = equityCurve[0]?.equity ?? payload.initial_capital
  const drawdownCurve = equityCurve.map((point) => {
    peak = Math.max(peak, point.equity)
    return {
      date: point.date,
      drawdown: peak > 0 ? Number(((point.equity - peak) / peak).toFixed(6)) : 0,
    }
  })
  const maxDrawdown = Math.max(0, ...drawdownCurve.map((row) => Math.abs(row.drawdown)))

  const monthlyMap = new Map<string, { pnl: number; count: number }>()
  trades.forEach((row) => {
    const month = row.exit_date.slice(0, 7)
    const prev = monthlyMap.get(month) ?? { pnl: 0, count: 0 }
    monthlyMap.set(month, { pnl: prev.pnl + row.pnl_amount, count: prev.count + 1 })
  })
  const monthlyReturns = Array.from(monthlyMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, value]) => ({
      month,
      return_ratio: payload.initial_capital > 0 ? Number((value.pnl / payload.initial_capital).toFixed(6)) : 0,
      pnl_amount: Number(value.pnl.toFixed(4)),
      trade_count: value.count,
    }))

  const sortedTrades = trades.slice().sort((a, b) => b.pnl_amount - a.pnl_amount)
  const topTrades = sortedTrades.slice(0, 10)
  const bottomTrades = sortedTrades.slice(-10).reverse()
  const dailyReturns: number[] = []
  for (let idx = 1; idx < equityCurve.length; idx += 1) {
    const prev = Number(equityCurve[idx - 1]?.equity ?? 0)
    const curr = Number(equityCurve[idx]?.equity ?? 0)
    if (prev > 0 && Number.isFinite(prev) && Number.isFinite(curr)) {
      dailyReturns.push(curr / prev - 1)
    }
  }
  const meanDaily = dailyReturns.length > 0 ? dailyReturns.reduce((sum, item) => sum + item, 0) / dailyReturns.length : 0
  const stdDaily = dailyReturns.length > 1
    ? Math.sqrt(dailyReturns.reduce((sum, item) => sum + (item - meanDaily) ** 2, 0) / dailyReturns.length)
    : 0
  const downside = dailyReturns.filter((item) => item < 0)
  const downsideStd = downside.length > 1
    ? Math.sqrt(downside.reduce((sum, item) => sum + (item - (downside.reduce((s, v) => s + v, 0) / downside.length)) ** 2, 0) / downside.length)
    : 0
  const avgWinRatio = winCount > 0 ? trades.filter((row) => row.pnl_ratio > 0).reduce((sum, row) => sum + row.pnl_ratio, 0) / winCount : 0
  const avgLossRatio = lossCount > 0 ? trades.filter((row) => row.pnl_ratio < 0).reduce((sum, row) => sum + row.pnl_ratio, 0) / lossCount : 0
  let lossStreak = 0
  let maxLossStreak = 0
  trades.forEach((row) => {
    if (row.pnl_ratio < 0) {
      lossStreak += 1
      maxLossStreak = Math.max(maxLossStreak, lossStreak)
    } else {
      lossStreak = 0
    }
  })
  const troughIndex = drawdownCurve.reduce((bestIdx, row, idx, arr) => (row.drawdown < arr[bestIdx]!.drawdown ? idx : bestIdx), 0)
  const recoveryIndex = drawdownCurve.findIndex((row, idx) => idx > troughIndex && row.drawdown >= -1e-6)
  const recoveryDays = recoveryIndex > troughIndex ? recoveryIndex - troughIndex : Math.max(0, drawdownCurve.length - troughIndex - 1)
  const monthlyStd = monthlyReturns.length > 1
    ? Math.sqrt(monthlyReturns.reduce((sum, item) => sum + (item.return_ratio - (monthlyReturns.reduce((s, v) => s + v.return_ratio, 0) / monthlyReturns.length)) ** 2, 0) / monthlyReturns.length)
    : 0
  const tradePenalty = tradeCount < 20 ? Math.min(1, (20 - tradeCount) / 20) * 0.35 : 0
  const variancePenalty = Math.min(0.35, monthlyStd * 3)
  const stabilityScore = Math.max(0, Math.min(1, 0.5 * 0.6 + 0.3 * (1 - tradePenalty) + 0.2 * (1 - variancePenalty)))
  const regimeBreakdown = [
    {
      regime: 'bull' as const,
      label: '牛市代理',
      trade_count: Math.floor(tradeCount * 0.35),
      win_rate: tradeCount > 0 ? Math.min(1, (winCount / Math.max(1, tradeCount)) + 0.06) : 0,
      total_return: payload.initial_capital > 0 ? Number((totalPnl / payload.initial_capital * 0.45).toFixed(6)) : 0,
      avg_pnl_ratio: Number((avgPnlRatio * 1.2).toFixed(6)),
      max_drawdown: Number((maxDrawdown * 0.8).toFixed(6)),
    },
    {
      regime: 'range' as const,
      label: '震荡代理',
      trade_count: Math.floor(tradeCount * 0.4),
      win_rate: tradeCount > 0 ? winCount / Math.max(1, tradeCount) : 0,
      total_return: payload.initial_capital > 0 ? Number((totalPnl / payload.initial_capital * 0.35).toFixed(6)) : 0,
      avg_pnl_ratio: Number((avgPnlRatio).toFixed(6)),
      max_drawdown: Number((maxDrawdown).toFixed(6)),
    },
    {
      regime: 'bear' as const,
      label: '熊市代理',
      trade_count: Math.max(0, tradeCount - Math.floor(tradeCount * 0.35) - Math.floor(tradeCount * 0.4)),
      win_rate: tradeCount > 0 ? Math.max(0, (winCount / Math.max(1, tradeCount)) - 0.08) : 0,
      total_return: payload.initial_capital > 0 ? Number((totalPnl / payload.initial_capital * 0.2).toFixed(6)) : 0,
      avg_pnl_ratio: Number((avgPnlRatio * 0.8).toFixed(6)),
      max_drawdown: Number((Math.min(1, maxDrawdown * 1.25)).toFixed(6)),
    },
  ]
  const monteCarlo = {
    simulations: 400,
    seed: 20260223,
    total_return_p5: Number(((payload.initial_capital > 0 ? totalPnl / payload.initial_capital : 0) * 0.45).toFixed(6)),
    total_return_p50: Number(((payload.initial_capital > 0 ? totalPnl / payload.initial_capital : 0) * 0.9).toFixed(6)),
    total_return_p95: Number(((payload.initial_capital > 0 ? totalPnl / payload.initial_capital : 0) * 1.25).toFixed(6)),
    max_drawdown_p5: Number((maxDrawdown * 0.8).toFixed(6)),
    max_drawdown_p50: Number((maxDrawdown).toFixed(6)),
    max_drawdown_p95: Number((Math.min(1, maxDrawdown * 1.2)).toFixed(6)),
    ruin_probability: tradeCount > 0 ? Number((Math.max(0, 0.22 - tradeCount * 0.002)).toFixed(6)) : 0.3,
  }
  const walkForward =
    payload.enable_advanced_analysis !== false
      ? {
          fold_count: 2,
          candidate_count: 4,
          oos_pass_rate: tradeCount > 0 ? Number((Math.min(1, Math.max(0, (winCount / Math.max(1, tradeCount)) + 0.1))).toFixed(6)) : 0,
          avg_test_return: payload.initial_capital > 0 ? Number((totalPnl / payload.initial_capital * 0.7).toFixed(6)) : 0,
          avg_test_win_rate: tradeCount > 0 ? Number((Math.max(0, Math.min(1, winCount / Math.max(1, tradeCount) - 0.03))).toFixed(6)) : 0,
          folds: [
            {
              fold_index: 1,
              train_date_from: rangeFrom,
              train_date_to: rangeFrom,
              test_date_from: rangeFrom,
              test_date_to: rangeTo,
              selected_params: {
                window_days: payload.window_days,
                min_score: payload.min_score,
                stop_loss: payload.stop_loss,
                take_profit: payload.take_profit,
                trailing_stop_pct: Number(payload.trailing_stop_pct ?? 0),
                max_positions: payload.max_positions,
                position_pct: payload.position_pct,
                max_symbols: payload.max_symbols,
                priority_topk_per_day: payload.priority_topk_per_day,
              },
              train_score: Number((payload.min_score / 100).toFixed(6)),
              test_score: Number((payload.min_score / 120).toFixed(6)),
              train_stats: {
                win_rate: tradeCount > 0 ? winCount / tradeCount : 0,
                total_return: payload.initial_capital > 0 ? totalPnl / payload.initial_capital : 0,
                max_drawdown: maxDrawdown,
                avg_pnl_ratio: Number(avgPnlRatio.toFixed(6)),
                trade_count: tradeCount,
                win_count: winCount,
                loss_count: lossCount,
                profit_factor: grossLoss < 0 ? Number((grossProfit / Math.abs(grossLoss)).toFixed(6)) : grossProfit > 0 ? 999 : 0,
              },
              test_stats: {
                win_rate: tradeCount > 0 ? Math.max(0, winCount / tradeCount - 0.04) : 0,
                total_return: payload.initial_capital > 0 ? Number((totalPnl / payload.initial_capital * 0.72).toFixed(6)) : 0,
                max_drawdown: Number((maxDrawdown * 1.05).toFixed(6)),
                avg_pnl_ratio: Number((avgPnlRatio * 0.9).toFixed(6)),
                trade_count: Math.max(1, Math.floor(tradeCount * 0.6)),
                win_count: Math.max(0, Math.floor(winCount * 0.55)),
                loss_count: Math.max(0, Math.floor(lossCount * 0.65)),
                profit_factor: grossLoss < 0 ? Number((grossProfit / Math.abs(grossLoss) * 0.92).toFixed(6)) : grossProfit > 0 ? 999 : 0,
              },
            },
          ],
          notes: ['mock walk-forward 结果，演示用途。'],
        }
      : null

  return {
    stats: {
      win_rate: tradeCount > 0 ? winCount / tradeCount : 0,
      total_return: payload.initial_capital > 0 ? totalPnl / payload.initial_capital : 0,
      max_drawdown: maxDrawdown,
      avg_pnl_ratio: Number(avgPnlRatio.toFixed(6)),
      trade_count: tradeCount,
      win_count: winCount,
      loss_count: lossCount,
      profit_factor: grossLoss < 0 ? Number((grossProfit / Math.abs(grossLoss)).toFixed(6)) : grossProfit > 0 ? 999 : 0,
    },
    trades,
    equity_curve: equityCurve,
    drawdown_curve: drawdownCurve,
    monthly_returns: monthlyReturns,
    top_trades: topTrades,
    bottom_trades: bottomTrades,
    cost_snapshot: {
      initial_capital: payload.initial_capital,
      commission_rate: Number(clamp(payload.fee_bps / 10000, 0, 0.01).toFixed(6)),
      min_commission: 0,
      stamp_tax_rate: 0,
      transfer_fee_rate: 0,
      slippage_rate: 0,
    },
    range: {
      date_from: rangeFrom,
      date_to: rangeTo,
      date_axis: 'sell',
    },
    notes,
    candidate_count: candidateCount,
    skipped_count: skippedCount,
    fill_rate: Number(fillRate.toFixed(6)),
    max_concurrent_positions: maxConcurrentPositions,
    risk_metrics: {
      sharpe: stdDaily > 1e-9 ? Number(((meanDaily / stdDaily) * Math.sqrt(252)).toFixed(6)) : 0,
      sortino: downsideStd > 1e-9 ? Number(((meanDaily / downsideStd) * Math.sqrt(252)).toFixed(6)) : 0,
      calmar: maxDrawdown > 1e-9 ? Number(((payload.initial_capital > 0 ? totalPnl / payload.initial_capital : 0) / maxDrawdown).toFixed(6)) : 0,
      expectancy: Number(((tradeCount > 0 ? winCount / tradeCount : 0) * avgWinRatio + (1 - (tradeCount > 0 ? winCount / tradeCount : 0)) * avgLossRatio).toFixed(6)),
      avg_win_pnl_ratio: Number(avgWinRatio.toFixed(6)),
      avg_loss_pnl_ratio: Number(avgLossRatio.toFixed(6)),
      max_consecutive_losses: maxLossStreak,
      recovery_days: recoveryDays,
    },
    stability_diagnostics: {
      stability_score: Number(stabilityScore.toFixed(6)),
      min_trade_count_threshold: 20,
      trade_count_penalty: Number(tradePenalty.toFixed(6)),
      neighborhood_consistency: 0.5,
      return_variance_penalty: Number(variancePenalty.toFixed(6)),
      monthly_return_std: Number(monthlyStd.toFixed(6)),
      notes: ['mock 稳定性评分，演示用途。'],
    },
    regime_breakdown: regimeBreakdown,
    monte_carlo: monteCarlo,
    walk_forward: walkForward,
    execution_path: 'legacy',
    strategy_id: payload.strategy_id ?? 'wyckoff_trend_v1',
    strategy_version: 'mock',
    strategy_params: payload.strategy_params ?? {},
    strategy_params_hash: 'mock',
  }
}

export function runBacktestPlateauStore(payload: BacktestPlateauRunRequest): BacktestPlateauResponse {
  const samplingMode = payload.sampling_mode ?? 'lhs'
  const samplePoints = Math.max(1, Math.min(2000, payload.sample_points ?? payload.max_points ?? 120))

  const base = payload.base_payload
  const axisWindow = payload.window_days_list.length > 0 ? payload.window_days_list : [base.window_days]
  const axisMinScore = payload.min_score_list.length > 0 ? payload.min_score_list : [base.min_score]
  const axisStopLoss = payload.stop_loss_list.length > 0 ? payload.stop_loss_list : [base.stop_loss]
  const axisTakeProfit = payload.take_profit_list.length > 0 ? payload.take_profit_list : [base.take_profit]
  const axisTrailingStop = (
    payload.trailing_stop_pct_list.length > 0
      ? payload.trailing_stop_pct_list
      : [base.trailing_stop_pct ?? 0]
  ).map((value) => Number(value ?? 0))
  const axisIntradayReduce = (
    payload.intraday_trailing_reduce_ratio_list?.length
      ? payload.intraday_trailing_reduce_ratio_list
      : [base.intraday_trailing_reduce_ratio ?? 0.5]
  ).map((value) => Number(value ?? 0.5))
  const axisMaxPositions = payload.max_positions_list.length > 0 ? payload.max_positions_list : [base.max_positions]
  const axisPositionPct = payload.position_pct_list.length > 0 ? payload.position_pct_list : [base.position_pct]
  const axisMaxSymbols = payload.max_symbols_list.length > 0 ? payload.max_symbols_list : [base.max_symbols]
  const axisTopK = payload.priority_topk_per_day_list.length > 0
    ? payload.priority_topk_per_day_list
    : [base.priority_topk_per_day]

  const gridTotal = Math.max(
    1,
    axisWindow.length
    * axisMinScore.length
    * axisStopLoss.length
    * axisTakeProfit.length
    * axisTrailingStop.length
    * axisIntradayReduce.length
    * axisMaxPositions.length
    * axisPositionPct.length
    * axisMaxSymbols.length
    * axisTopK.length,
  )

  let seed = Number.isFinite(payload.random_seed) ? Number(payload.random_seed) : 20260221
  const nextRandom = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }
  const randomBetween = (min: number, max: number) => {
    if (max <= min) return min
    return min + (max - min) * nextRandom()
  }
  const randomIntBetween = (min: number, max: number) => {
    if (max <= min) return min
    return Math.round(randomBetween(min, max))
  }

  const points: BacktestPlateauPoint[] = []
  const notes: string[] = []
  const targetEvaluate = samplingMode === 'lhs' ? samplePoints : Math.min(samplePoints, gridTotal)
  const windowBounds: [number, number] = [Math.min(...axisWindow), Math.max(...axisWindow)]
  const minScoreBounds: [number, number] = [Math.min(...axisMinScore), Math.max(...axisMinScore)]
  const stopLossBounds: [number, number] = [Math.min(...axisStopLoss), Math.max(...axisStopLoss)]
  const takeProfitBounds: [number, number] = [Math.min(...axisTakeProfit), Math.max(...axisTakeProfit)]
  const trailingStopBounds: [number, number] = [Math.min(...axisTrailingStop), Math.max(...axisTrailingStop)]
  const intradayReduceBounds: [number, number] = [Math.min(...axisIntradayReduce), Math.max(...axisIntradayReduce)]
  const maxPositionsBounds: [number, number] = [Math.min(...axisMaxPositions), Math.max(...axisMaxPositions)]
  const positionPctBounds: [number, number] = [Math.min(...axisPositionPct), Math.max(...axisPositionPct)]
  const maxSymbolsBounds: [number, number] = [Math.min(...axisMaxSymbols), Math.max(...axisMaxSymbols)]
  const topKBounds: [number, number] = [Math.min(...axisTopK), Math.max(...axisTopK)]
  const seen = new Set<string>()

  const appendPoint = (candidate: {
    window_days: number
    min_score: number
    stop_loss: number
    take_profit: number
    trailing_stop_pct: number
    intraday_trailing_reduce_ratio: number
    max_positions: number
    position_pct: number
    max_symbols: number
    priority_topk_per_day: number
  }) => {
    const key = [
      candidate.window_days,
      candidate.min_score.toFixed(4),
      candidate.stop_loss.toFixed(6),
      candidate.take_profit.toFixed(6),
      candidate.trailing_stop_pct.toFixed(6),
      candidate.intraday_trailing_reduce_ratio.toFixed(6),
      candidate.max_positions,
      candidate.position_pct.toFixed(6),
      candidate.max_symbols,
      candidate.priority_topk_per_day,
    ].join('|')
    if (seen.has(key)) return
    seen.add(key)
    const runPayload: BacktestRunRequest = {
      ...base,
      window_days: candidate.window_days,
      min_score: candidate.min_score,
      stop_loss: candidate.stop_loss,
      take_profit: candidate.take_profit,
      trailing_stop_pct: candidate.trailing_stop_pct,
      intraday_trailing_reduce_ratio: candidate.intraday_trailing_reduce_ratio,
      max_positions: candidate.max_positions,
      position_pct: candidate.position_pct,
      max_symbols: candidate.max_symbols,
      priority_topk_per_day: candidate.priority_topk_per_day,
    }
    const result = runBacktestStore(runPayload)
    const totalReturn = Number(result.stats.total_return)
    const maxDrawdown = Math.abs(Math.min(0, Number(result.stats.max_drawdown)))
    const winRate = Math.max(0, Math.min(1, Number(result.stats.win_rate)))
    const profitFactorRaw = Number(result.stats.profit_factor)
    const profitFactor = Number.isFinite(profitFactorRaw) ? Math.max(0, Math.min(5, profitFactorRaw)) : 5
    const winRateTerm = (winRate - 0.5) * 0.2
    const lowWinPenalty = Math.max(0, 0.45 - winRate) * 0.9
    const score = Number((totalReturn - maxDrawdown * 0.6 + profitFactor * 0.02 + winRateTerm - lowWinPenalty).toFixed(6))
    points.push({
      params: candidate,
      stats: result.stats,
      candidate_count: result.candidate_count,
      skipped_count: result.skipped_count,
      fill_rate: result.fill_rate,
      max_concurrent_positions: result.max_concurrent_positions,
      score,
      cache_hit: false,
      error: null,
    })
  }

  if (samplingMode === 'grid') {
    for (const windowDays of axisWindow) {
      for (const minScore of axisMinScore) {
        for (const stopLoss of axisStopLoss) {
          for (const takeProfit of axisTakeProfit) {
            for (const trailingStop of axisTrailingStop) {
              for (const intradayReduce of axisIntradayReduce) {
                for (const maxPositions of axisMaxPositions) {
                  for (const positionPct of axisPositionPct) {
                    for (const maxSymbols of axisMaxSymbols) {
                      for (const topK of axisTopK) {
                        if (points.length >= targetEvaluate) break
                        appendPoint({
                          window_days: Math.max(20, Math.min(240, Math.round(windowDays))),
                          min_score: Number(Math.max(0, Math.min(100, minScore)).toFixed(4)),
                          stop_loss: Number(Math.max(0, Math.min(0.5, stopLoss)).toFixed(6)),
                          take_profit: Number(Math.max(0, Math.min(1.5, takeProfit)).toFixed(6)),
                          trailing_stop_pct: Number(Math.max(0, Math.min(0.5, trailingStop)).toFixed(6)),
                          intraday_trailing_reduce_ratio: Number(Math.max(0.1, Math.min(1, intradayReduce)).toFixed(6)),
                          max_positions: Math.max(1, Math.min(100, Math.round(maxPositions))),
                          position_pct: Number(Math.max(0.0001, Math.min(1, positionPct)).toFixed(6)),
                          max_symbols: Math.max(20, Math.min(2000, Math.round(maxSymbols))),
                          priority_topk_per_day: Math.max(0, Math.min(500, Math.round(topK))),
                        })
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    if (gridTotal > targetEvaluate) {
      notes.push(`参数组合总数 ${gridTotal} 超过上限，已截断评估前 ${targetEvaluate} 组。`)
    }
  } else {
    let guard = 0
    const guardMax = Math.max(targetEvaluate * 20, 512)
    while (points.length < targetEvaluate && guard < guardMax) {
      guard += 1
      appendPoint({
        window_days: Math.max(20, Math.min(240, randomIntBetween(windowBounds[0], windowBounds[1]))),
        min_score: Number(randomBetween(minScoreBounds[0], minScoreBounds[1]).toFixed(4)),
        stop_loss: Number(randomBetween(stopLossBounds[0], stopLossBounds[1]).toFixed(6)),
        take_profit: Number(randomBetween(takeProfitBounds[0], takeProfitBounds[1]).toFixed(6)),
        trailing_stop_pct: Number(randomBetween(trailingStopBounds[0], trailingStopBounds[1]).toFixed(6)),
        intraday_trailing_reduce_ratio: Number(Math.max(0.1, Math.min(1, randomBetween(intradayReduceBounds[0], intradayReduceBounds[1]))).toFixed(6)),
        max_positions: Math.max(1, Math.min(100, randomIntBetween(maxPositionsBounds[0], maxPositionsBounds[1]))),
        position_pct: Number(Math.max(0.0001, Math.min(1, randomBetween(positionPctBounds[0], positionPctBounds[1]))).toFixed(6)),
        max_symbols: Math.max(20, Math.min(2000, randomIntBetween(maxSymbolsBounds[0], maxSymbolsBounds[1]))),
        priority_topk_per_day: Math.max(0, Math.min(500, randomIntBetween(topKBounds[0], topKBounds[1]))),
      })
    }
    notes.push(`参数采样模式: lhs，目标采样 ${targetEvaluate} 组。`)
    if (typeof payload.random_seed === 'number') {
      notes.push(`LHS 随机种子: ${payload.random_seed}。`)
    }
    if (points.length < targetEvaluate) {
      notes.push(`LHS 去重后仅生成 ${points.length} 组有效参数（目标 ${targetEvaluate} 组）。`)
    }
    notes.push(`参考网格组合规模（按列表离散值估算）: ${gridTotal}。`)
  }

  const safePearson = (xValues: number[], yValues: number[]) => {
    const count = Math.min(xValues.length, yValues.length)
    if (count < 2) return 0
    const xs = xValues.slice(0, count)
    const ys = yValues.slice(0, count)
    const meanX = xs.reduce((sum, value) => sum + value, 0) / count
    const meanY = ys.reduce((sum, value) => sum + value, 0) / count
    const varX = xs.reduce((sum, value) => sum + (value - meanX) ** 2, 0)
    const varY = ys.reduce((sum, value) => sum + (value - meanY) ** 2, 0)
    if (varX <= 1e-12 || varY <= 1e-12) return 0
    const cov = xs.reduce((sum, value, index) => sum + (value - meanX) * (ys[index]! - meanY), 0)
    const corr = cov / Math.sqrt(varX * varY)
    if (!Number.isFinite(corr)) return 0
    return Math.max(-1, Math.min(1, corr))
  }

  const buildCorrelationSummary = (
    rows: BacktestPlateauCorrelationRow[],
    metricLabel: string,
    key: 'score_corr' | 'total_return_corr' | 'win_rate_corr',
  ) => {
    if (rows.length <= 0) return null
    const sorted = rows.slice()
    sorted.sort((left, right) => Number(right[key]) - Number(left[key]))
    const positive = sorted[0]
    const negative = sorted[sorted.length - 1]
    const parts: string[] = []
    if (positive && Number(positive[key]) > 0.05) {
      parts.push(`${positive.parameter_label}正相关 ${Number(positive[key]).toFixed(3)}`)
    }
    if (negative && Number(negative[key]) < -0.05 && negative.parameter !== positive?.parameter) {
      parts.push(`${negative.parameter_label}负相关 ${Number(negative[key]).toFixed(3)}`)
    }
    if (parts.length <= 0) return null
    return `${metricLabel}相关性：${parts.join('；')}。`
  }

  points.sort((left, right) => right.score - left.score)
  const bestPoint = points[0] ?? null
  const validPoints = points.filter((row) => !row.error)
  const correlations: BacktestPlateauCorrelationRow[] = []
  if (validPoints.length >= 2) {
    const scoreValues = validPoints.map((row) => Number(row.score))
    const totalReturnValues = validPoints.map((row) => Number(row.stats.total_return))
    const winRateValues = validPoints.map((row) => Number(row.stats.win_rate))
    const paramExtractors: Array<{
      key: string
      label: string
      pick: (row: BacktestPlateauPoint) => number
    }> = [
      { key: 'window_days', label: '信号窗口天数', pick: (row) => Number(row.params.window_days) },
      { key: 'min_score', label: '最低评分', pick: (row) => Number(row.params.min_score) },
      { key: 'stop_loss', label: '止损比例', pick: (row) => Number(row.params.stop_loss) },
      { key: 'take_profit', label: '止盈比例', pick: (row) => Number(row.params.take_profit) },
      { key: 'trailing_stop_pct', label: '高位回撤比例', pick: (row) => Number(row.params.trailing_stop_pct) },
      { key: 'intraday_trailing_reduce_ratio', label: '日内减仓比例', pick: (row) => Number(row.params.intraday_trailing_reduce_ratio) },
      { key: 'max_positions', label: '最大并发持仓', pick: (row) => Number(row.params.max_positions) },
      { key: 'position_pct', label: '单笔仓位占比', pick: (row) => Number(row.params.position_pct) },
      { key: 'max_symbols', label: '最大股票数', pick: (row) => Number(row.params.max_symbols) },
      { key: 'priority_topk_per_day', label: '同日TopK', pick: (row) => Number(row.params.priority_topk_per_day) },
    ]
    paramExtractors.forEach((item) => {
      const xValues = validPoints.map(item.pick)
      correlations.push({
        parameter: item.key,
        parameter_label: item.label,
        score_corr: Number(safePearson(xValues, scoreValues).toFixed(6)),
        total_return_corr: Number(safePearson(xValues, totalReturnValues).toFixed(6)),
        win_rate_corr: Number(safePearson(xValues, winRateValues).toFixed(6)),
      })
    })
    correlations.sort((left, right) => {
      const leftStrength = Math.max(Math.abs(left.score_corr), Math.abs(left.total_return_corr), Math.abs(left.win_rate_corr))
      const rightStrength = Math.max(Math.abs(right.score_corr), Math.abs(right.total_return_corr), Math.abs(right.win_rate_corr))
      return rightStrength - leftStrength
    })
  }
  notes.push('评分规则已加入低胜率惩罚：胜率低于45%将额外扣分。')
  const scoreCorrNote = buildCorrelationSummary(correlations, '评分', 'score_corr')
  const returnCorrNote = buildCorrelationSummary(correlations, '收益', 'total_return_corr')
  const winRateCorrNote = buildCorrelationSummary(correlations, '胜率', 'win_rate_corr')
  if (scoreCorrNote) notes.push(scoreCorrNote)
  if (returnCorrNote) notes.push(returnCorrNote)
  if (winRateCorrNote) notes.push(winRateCorrNote)
  notes.push(`收益平原评估完成：总组合 ${samplingMode === 'lhs' ? targetEvaluate : gridTotal}，实际评估 ${points.length}。`)
  return {
    base_payload: base,
    total_combinations: samplingMode === 'lhs' ? targetEvaluate : gridTotal,
    evaluated_combinations: points.length,
    points,
    best_point: bestPoint,
    correlations,
    generated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    notes,
  }
}

export function getDailyReviewsStore(params?: { date_from?: string; date_to?: string }): DailyReviewListResponse {
  let rows = dailyReviewsStore.slice()
  if (params?.date_from) rows = rows.filter((row) => row.date >= params.date_from!)
  if (params?.date_to) rows = rows.filter((row) => row.date <= params.date_to!)
  rows.sort((a, b) => b.date.localeCompare(a.date))
  return { items: rows }
}

export function getDailyReviewStore(date: string): DailyReviewRecord | null {
  return dailyReviewsStore.find((row) => row.date === date) ?? null
}

export function upsertDailyReviewStore(date: string, payload: DailyReviewPayload): DailyReviewRecord {
  const record: DailyReviewRecord = {
    ...payload,
    tags: uniqueTokens(payload.tags || []),
    date,
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  }
  const next = dailyReviewsStore.filter((row) => row.date !== date)
  next.push(record)
  dailyReviewsStore = next.sort((a, b) => b.date.localeCompare(a.date))
  return record
}

export function deleteDailyReviewStore(date: string) {
  const before = dailyReviewsStore.length
  dailyReviewsStore = dailyReviewsStore.filter((row) => row.date !== date)
  return { deleted: dailyReviewsStore.length < before }
}

export function getWeeklyReviewsStore(params?: { year?: number }): WeeklyReviewListResponse {
  let rows = weeklyReviewsStore.slice()
  if (typeof params?.year === 'number') {
    const prefix = `${params.year}-W`
    rows = rows.filter((row) => row.week_label.startsWith(prefix))
  }
  rows.sort((a, b) => b.week_label.localeCompare(a.week_label))
  return { items: rows }
}

export function getWeeklyReviewStore(weekLabel: string): WeeklyReviewRecord | null {
  return weeklyReviewsStore.find((row) => row.week_label === weekLabel) ?? null
}

export function upsertWeeklyReviewStore(weekLabel: string, payload: WeeklyReviewPayload): WeeklyReviewRecord {
  const weekRange = getWeekRangeFromLabel(weekLabel)
  const record: WeeklyReviewRecord = {
    ...payload,
    tags: uniqueTokens(payload.tags || []),
    week_label: weekLabel,
    start_date: payload.start_date || weekRange.start_date,
    end_date: payload.end_date || weekRange.end_date,
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  }
  const next = weeklyReviewsStore.filter((row) => row.week_label !== weekLabel)
  next.push(record)
  weeklyReviewsStore = next.sort((a, b) => b.week_label.localeCompare(a.week_label))
  return record
}

export function deleteWeeklyReviewStore(weekLabel: string) {
  const before = weeklyReviewsStore.length
  weeklyReviewsStore = weeklyReviewsStore.filter((row) => row.week_label !== weekLabel)
  return { deleted: weeklyReviewsStore.length < before }
}

export function getReviewTagsStore(): ReviewTagsPayload {
  return {
    emotion: reviewTagsStore.emotion.slice(),
    reason: reviewTagsStore.reason.slice(),
  }
}

export function createReviewTagStore(tagType: ReviewTagType, payload: ReviewTagCreateRequest): ReviewTag {
  const name = payload.name.trim()
  const source = reviewTagsStore[tagType]
  const exists = source.find((item) => item.name === name)
  if (exists) return exists
  const palette = ['blue', 'cyan', 'green', 'gold', 'orange', 'red', 'magenta', 'purple', 'geekblue', 'lime']
  const created: ReviewTag = {
    id: `${tagType}-${Date.now().toString(36)}`,
    name,
    color: palette[source.length % palette.length],
    created_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  }
  reviewTagsStore = {
    ...reviewTagsStore,
    [tagType]: [...source, created],
  }
  return created
}

export function deleteReviewTagStore(tagType: ReviewTagType, tagId: string) {
  const before = reviewTagsStore[tagType].length
  reviewTagsStore = {
    ...reviewTagsStore,
    [tagType]: reviewTagsStore[tagType].filter((item) => item.id !== tagId),
  }
  if (tagType === 'emotion') {
    fillTagStore = fillTagStore.map((row) => ({
      ...row,
      emotion_tag_id: row.emotion_tag_id === tagId ? null : row.emotion_tag_id,
    }))
  } else {
    fillTagStore = fillTagStore.map((row) => ({
      ...row,
      reason_tag_ids: row.reason_tag_ids.filter((item) => item !== tagId),
    }))
  }
  fillTagStore = fillTagStore.filter((row) => row.emotion_tag_id || row.reason_tag_ids.length > 0)
  return { deleted: reviewTagsStore[tagType].length < before }
}

export function getReviewFillTagsStore(): TradeFillTagAssignment[] {
  return fillTagStore.slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at))
}

export function getReviewFillTagStore(orderId: string): TradeFillTagAssignment | null {
  return fillTagStore.find((row) => row.order_id === orderId) ?? null
}

export function updateReviewFillTagStore(orderId: string, payload: TradeFillTagUpdateRequest): TradeFillTagAssignment | null {
  const exists = simFillsStore.some((row) => row.order_id === orderId)
  if (!exists) return null
  const next: TradeFillTagAssignment = {
    order_id: orderId,
    emotion_tag_id: payload.emotion_tag_id ?? null,
    reason_tag_ids: uniqueTokens(payload.reason_tag_ids || []),
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  }
  fillTagStore = fillTagStore.filter((row) => row.order_id !== orderId)
  if (next.emotion_tag_id || next.reason_tag_ids.length > 0) {
    fillTagStore.unshift(next)
  }
  return next
}

export function getReviewTagStatsStore(params?: { date_from?: string; date_to?: string }): ReviewTagStatsResponse {
  let fills = simFillsStore.slice()
  if (params?.date_from) fills = fills.filter((row) => row.fill_date >= params.date_from!)
  if (params?.date_to) fills = fills.filter((row) => row.fill_date <= params.date_to!)

  const fillMap = new Map<string, SimTradeFill>()
  fills.forEach((row) => fillMap.set(row.order_id, row))

  const emotionRows = reviewTagsStore.emotion.map((tag) => ({
    tag_id: tag.id,
    name: tag.name,
    color: tag.color,
    count: 0,
    gross_amount: 0,
    net_amount: 0,
  }))
  const reasonRows = reviewTagsStore.reason.map((tag) => ({
    tag_id: tag.id,
    name: tag.name,
    color: tag.color,
    count: 0,
    gross_amount: 0,
    net_amount: 0,
  }))

  const emotionIndex = new Map(emotionRows.map((row) => [row.tag_id, row]))
  const reasonIndex = new Map(reasonRows.map((row) => [row.tag_id, row]))

  fillTagStore.forEach((assignment) => {
    const fill = fillMap.get(assignment.order_id)
    if (!fill) return
    if (assignment.emotion_tag_id && emotionIndex.has(assignment.emotion_tag_id)) {
      const row = emotionIndex.get(assignment.emotion_tag_id)!
      row.count += 1
      row.gross_amount += fill.gross_amount
      row.net_amount += fill.net_amount
    }
    assignment.reason_tag_ids.forEach((tagId) => {
      if (!reasonIndex.has(tagId)) return
      const row = reasonIndex.get(tagId)!
      row.count += 1
      row.gross_amount += fill.gross_amount
      row.net_amount += fill.net_amount
    })
  })

  return {
    date_from: params?.date_from,
    date_to: params?.date_to,
    emotion: emotionRows.filter((row) => row.count > 0).sort((a, b) => b.count - a.count),
    reason: reasonRows.filter((row) => row.count > 0).sort((a, b) => b.count - a.count),
  }
}

export function getMarketNewsStore(params?: {
  query?: string
  symbol?: string
  source_domains?: string[]
  age_hours?: 24 | 48 | 72
  refresh?: boolean
  limit?: number
}): MarketNewsResponse {
  const normalizedQuery = (params?.query || 'A股 热点').trim()
  const normalizedSymbol = (params?.symbol || '').trim().toLowerCase()
  const selectedDomains = [...new Set((params?.source_domains || []).map((item) => item.trim().toLowerCase()).filter(Boolean))]
  const ageHours = params?.age_hours && [24, 48, 72].includes(params.age_hours) ? params.age_hours : 72
  const defaultQueryTokens = new Set(['a股', '热点', 'a股热点', 'a股 热点'])
  const normalizedCompact = normalizedQuery.replace(/\s+/g, '').toLowerCase()
  const queryTokens = defaultQueryTokens.has(normalizedCompact)
    ? []
    : normalizedQuery.toLowerCase().split(/\s+/).filter(Boolean)
  const tokens = [...queryTokens, normalizedSymbol].filter(Boolean)
  const limit = Math.min(Math.max(params?.limit ?? 20, 1), 50)
  const cacheKey = `${normalizedQuery}|${normalizedSymbol}|${selectedDomains.join(',')}|${ageHours}|${limit}`
  const nowTs = Date.now()
  const cached = marketNewsCacheStore.get(cacheKey)
  if (!params?.refresh && cached && nowTs - cached.ts <= 180_000) {
    return {
      query: normalizedQuery || 'A股 热点',
      age_hours: ageHours,
      symbol: normalizedSymbol || undefined,
      symbol_name: normalizedSymbol ? normalizedSymbol.toUpperCase() : undefined,
      source_domains: selectedDomains,
      items: cached.items.slice(0, limit),
      fetched_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      cache_hit: true,
      fallback_used: false,
      degraded: false,
      degraded_reason: undefined,
    }
  }

  let rows = marketNewsSeed.slice()
  rows = rows.filter((item) => dayjs(item.pub_date).isAfter(dayjs().subtract(ageHours, 'hour')))
  if (tokens.length > 0) {
    rows = rows.filter((item) => {
      const corpus = `${item.title} ${item.snippet} ${item.source_name}`.toLowerCase()
      return tokens.some((token) => corpus.includes(token))
    })
  }
  if (selectedDomains.length > 0) {
    rows = rows.filter((item) => {
      try {
        const host = new URL(item.url).host.toLowerCase().replace(/^www\./, '')
        return selectedDomains.some((domain) => host === domain || host.endsWith(`.${domain}`))
      } catch {
        return false
      }
    })
  }

  const items = rows.slice(0, limit)
  if (items.length > 0) {
    marketNewsLastSuccess = items
    marketNewsCacheStore.set(cacheKey, { ts: nowTs, items })
    return {
      query: normalizedQuery || 'A股 热点',
      age_hours: ageHours,
      symbol: normalizedSymbol || undefined,
      symbol_name: normalizedSymbol ? normalizedSymbol.toUpperCase() : undefined,
      source_domains: selectedDomains,
      items,
      fetched_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      cache_hit: false,
      fallback_used: false,
      degraded: false,
      degraded_reason: undefined,
    }
  }

  if (marketNewsLastSuccess.length > 0) {
    return {
      query: normalizedQuery || 'A股 热点',
      age_hours: ageHours,
      symbol: normalizedSymbol || undefined,
      symbol_name: normalizedSymbol ? normalizedSymbol.toUpperCase() : undefined,
      source_domains: selectedDomains,
      items: marketNewsLastSuccess.slice(0, limit),
      fetched_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      cache_hit: false,
      fallback_used: true,
      degraded: true,
      degraded_reason: 'MOCK_NEWS_FALLBACK_CACHE',
    }
  }

  return {
    query: normalizedQuery || 'A股 热点',
    age_hours: ageHours,
    symbol: normalizedSymbol || undefined,
    symbol_name: normalizedSymbol ? normalizedSymbol.toUpperCase() : undefined,
    source_domains: selectedDomains,
    items: [],
    fetched_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    cache_hit: false,
    fallback_used: false,
    degraded: true,
    degraded_reason: 'MOCK_NEWS_EMPTY',
  }
}

export function getOrders(params?: {
  status?: 'pending' | 'filled' | 'cancelled' | 'rejected'
  symbol?: string
  side?: 'buy' | 'sell'
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}): SimOrdersResponse {
  const page = params?.page ?? 1
  const pageSize = params?.page_size ?? 50
  let rows = simOrdersStore.slice()
  if (params?.status) rows = rows.filter((row) => row.status === params.status)
  if (params?.symbol) rows = rows.filter((row) => row.symbol === params.symbol?.trim().toLowerCase())
  if (params?.side) rows = rows.filter((row) => row.side === params.side)
  if (params?.date_from) rows = rows.filter((row) => row.submit_date >= params.date_from!)
  if (params?.date_to) rows = rows.filter((row) => row.submit_date <= params.date_to!)
  const total = rows.length
  const start = Math.max(0, (page - 1) * pageSize)
  return {
    items: rows.slice(start, start + pageSize),
    total,
    page,
    page_size: pageSize,
  }
}

export function getFills(params?: {
  symbol?: string
  side?: 'buy' | 'sell'
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}): SimFillsResponse {
  const page = params?.page ?? 1
  const pageSize = params?.page_size ?? 50
  let rows = simFillsStore.slice()
  if (params?.symbol) rows = rows.filter((row) => row.symbol === params.symbol?.trim().toLowerCase())
  if (params?.side) rows = rows.filter((row) => row.side === params.side)
  if (params?.date_from) rows = rows.filter((row) => row.fill_date >= params.date_from!)
  if (params?.date_to) rows = rows.filter((row) => row.fill_date <= params.date_to!)
  const total = rows.length
  const start = Math.max(0, (page - 1) * pageSize)
  return {
    items: rows.slice(start, start + pageSize),
    total,
    page,
    page_size: pageSize,
  }
}

export function cancelOrder(orderId: string) {
  const index = simOrdersStore.findIndex((item) => item.order_id === orderId)
  if (index < 0) {
    return null
  }
  const target = simOrdersStore[index]
  if (target.status !== 'pending') {
    return { order: target, fill: undefined }
  }
  simOrdersStore[index] = {
    ...target,
    status: 'cancelled',
    status_reason: 'USER_CANCELLED',
  }
  return { order: simOrdersStore[index], fill: undefined }
}

export function settleOrders(): SimSettleResponse {
  let settledCount = 0
  let filledCount = 0
  simOrdersStore.forEach((order) => {
    if (order.status !== 'pending') return
    settledCount += 1
    const fill = applyOrderFill(order)
    if (fill) filledCount += 1
  })
  return {
    settled_count: settledCount,
    filled_count: filledCount,
    pending_count: simOrdersStore.filter((item) => item.status === 'pending').length,
    as_of_date: dayjs().format('YYYY-MM-DD'),
    last_settle_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  }
}

export function resetAccount(): SimResetResponse {
  simOrdersStore = []
  simFillsStore = []
  simLotsStore = []
  simClosedTradesStore = []
  simCash = simConfigStore.initial_capital
  return {
    success: true,
    as_of_date: dayjs().format('YYYY-MM-DD'),
    cash: simCash,
  }
}

export function getSimConfigStore() {
  return simConfigStore
}

export function setSimConfigStore(payload: SimTradingConfig) {
  simConfigStore = payload
  if (simOrdersStore.length === 0 && simFillsStore.length === 0 && simLotsStore.length === 0) {
    simCash = payload.initial_capital
  }
  return simConfigStore
}

export function getAIRecords(): AIAnalysisRecord[] {
  return aiRecordsStore
}

function guessTheme(symbol: string) {
  const themes = ['固态电池', '机器人', '算力应用', '高端消费', '有色资源', '创新药']
  return themes[hashSeed(symbol) % themes.length]
}

function inferTrendBullType(trend: TrendClass) {
  if (trend === 'A') return 'A 阶梯慢牛'
  if (trend === 'A_B') return 'A_B 慢牛加速'
  if (trend === 'B') return 'B 脉冲涨停牛'
  return 'Unknown'
}

export function analyzeStockWithAI(symbol: string): AIAnalysisRecord {
  const base = stockPool.find((item) => item.symbol === symbol)
  const candles = ensureCandles(symbol)
  const latest = candles[candles.length - 1]
  const start20 = candles[Math.max(0, candles.length - 20)]
  const ret20 = latest && start20 ? (latest.close - start20.close) / Math.max(start20.close, 0.01) : 0
  const volumes20 = candles.slice(-20).map((item) => item.volume)
  const recentVolume = mean(volumes20.slice(-5))
  const prevVolume = mean(volumes20.slice(0, 5))

  const trend = base?.trend ?? (ret20 > 0.25 ? 'A_B' : 'A')
  const conclusion = ret20 > 0.45 ? '高潮' : ret20 > 0.05 ? '发酵中' : 'Unknown'
  const confidence = ret20 > 0.45 ? 0.74 : 0.67

  const record: AIAnalysisRecord = {
    provider: configStore.ai_provider,
    symbol,
    name: base?.name ?? symbol.toUpperCase(),
    fetched_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    source_urls: configStore.ai_sources
      .filter((item) => item.enabled)
      .slice(0, 4)
      .map((item) => item.url),
    summary: `已完成 ${base?.name ?? symbol} 的题材与量价联合分析：近20日涨幅 ${(ret20 * 100).toFixed(2)}%，建议结合分时承接确认节奏。`,
    conclusion,
    confidence,
    breakout_date: dayjs().subtract(12 + (hashSeed(symbol) % 10), 'day').format('YYYY-MM-DD'),
    trend_bull_type: inferTrendBullType(trend),
    theme_name: guessTheme(symbol),
    rise_reasons: [
      recentVolume >= prevVolume ? '近端量能维持放大，资金活跃' : '量能趋稳，等待二次放量确认',
      ret20 > 0.1 ? '价格中枢上移，趋势结构保持多头' : '价格仍在构建底部区间',
      conclusion === '高潮' ? '题材一致性较高，但需防止高位波动' : '题材仍有扩散空间，可继续跟踪',
    ],
  }

  aiRecordsStore = [record, ...aiRecordsStore].slice(0, 200)
  return record
}

export function deleteAIRecord(symbol: string, fetchedAt: string, provider?: string) {
  const before = aiRecordsStore.length
  aiRecordsStore = aiRecordsStore.filter((item) => {
    if (item.symbol !== symbol) return true
    if (item.fetched_at !== fetchedAt) return true
    if (provider && item.provider !== provider) return true
    return false
  })
  return {
    deleted: aiRecordsStore.length < before,
    remaining: aiRecordsStore.length,
  }
}

export function testAIProvider(payload: AIProviderTestRequest): AIProviderTestResponse {
  const hasCredential =
    payload.provider.api_key.trim().length > 0
    || payload.provider.api_key_path.trim().length > 0
    || payload.fallback_api_key.trim().length > 0
    || payload.fallback_api_key_path.trim().length > 0

  if (!payload.provider.base_url.trim() || !payload.provider.model.trim()) {
    return {
      ok: false,
      provider_id: payload.provider.id,
      latency_ms: 0,
      message: '缺少 base_url 或 model',
      error_code: 'INVALID_PROVIDER_CONFIG',
    }
  }

  if (!hasCredential) {
    return {
      ok: false,
      provider_id: payload.provider.id,
      latency_ms: 0,
      message: '缺少 API 凭证，请填写 api_key 或 api_key_path',
      error_code: 'AI_KEY_MISSING',
    }
  }

  return {
    ok: true,
    provider_id: payload.provider.id,
    latency_ms: 120,
    message: '连接成功，耗时 120ms',
  }
}

export function getConfigStore() {
  return configStore
}

export function setConfigStore(payload: AppConfig) {
  configStore = payload
  return configStore
}

export function getSystemStorageStore(): SystemStorageStatus {
  const configured = configStore.akshare_cache_dir || ''
  const normalized = configured.replace(/%USERPROFILE%/gi, 'C:\\Users\\demo')
  const resolved = normalized || 'C:\\Users\\demo\\.tdx-trend\\akshare\\daily'
  return {
    app_state_path: 'C:\\Users\\demo\\.tdx-trend\\app_state.json',
    app_state_exists: true,
    sim_state_path: 'C:\\Users\\demo\\.tdx-trend\\sim_state.json',
    sim_state_exists: true,
    akshare_cache_dir: configured,
    akshare_cache_dir_resolved: resolved,
    akshare_cache_dir_exists: true,
    akshare_cache_file_count: 12,
    akshare_cache_candidates: [
      resolved,
      'C:\\Users\\demo\\.tdx-trend\\akshare\\daily',
      'D:\\data\\akshare\\daily',
    ],
  }
}

export function syncMarketDataStore(payload: MarketDataSyncRequest): MarketDataSyncResponse {
  const provider = payload.provider || 'baostock'
  const mode = payload.mode || 'incremental'
  const symbolCount = payload.all_market ? Math.min(Math.max(payload.limit || 300, 1), 3000) : 8
  const okCount = Math.max(1, Math.floor(symbolCount * 0.98))
  const failCount = Math.max(0, symbolCount - okCount)
  const skippedCount = mode === 'incremental' ? Math.floor(symbolCount * 0.7) : 0
  const newRowsTotal = mode === 'incremental' ? Math.max(1, Math.floor(symbolCount * 1.2)) : symbolCount * 180
  const now = dayjs().format('YYYY-MM-DD HH:mm:ss')
  const outDir = payload.out_dir?.trim() || configStore.akshare_cache_dir
  return {
    ok: failCount === 0,
    provider,
    mode,
    message: `Mock ${provider} 同步完成：成功 ${okCount}，失败 ${failCount}，跳过 ${skippedCount}，新增 ${newRowsTotal} 行`,
    out_dir: outDir,
    symbol_count: symbolCount,
    ok_count: okCount,
    fail_count: failCount,
    skipped_count: skippedCount,
    new_rows_total: newRowsTotal,
    started_at: now,
    finished_at: now,
    duration_sec: 0.8,
    errors: failCount > 0 ? ['mock: 网络波动导致少量失败'] : [],
  }
}
