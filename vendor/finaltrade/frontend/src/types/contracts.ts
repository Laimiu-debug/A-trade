export type Market = 'sh' | 'sz' | 'bj'
export type ScreenerMode = 'strict' | 'loose'
export type TrendClass = 'A' | 'A_B' | 'B' | 'Unknown'
export type ThemeStage = '发酵中' | '高潮' | '退潮' | 'Unknown'
export type SignalType = 'A' | 'B' | 'C'
export type SignalScanMode = 'trend_pool' | 'full_market'
export type TrendPoolStep = 'auto' | 'step1' | 'step2' | 'step3' | 'step4'
export type BacktestPriorityMode = 'phase_first' | 'balanced' | 'momentum'
export type BacktestPoolRollMode = 'daily' | 'weekly' | 'position'
export type BoardFilter = 'main' | 'gem' | 'star' | 'beijing' | 'st'
export type StrategyId = string
export type MarketDataSource = 'tdx_only' | 'tdx_then_akshare' | 'akshare_only'
export type MarketSyncProvider = 'baostock'
export type MarketSyncMode = 'incremental' | 'full'
export type PriceSource = 'vwap' | 'approx'

export interface ApiErrorPayload {
  code: string
  message: string
  degraded?: boolean
  degraded_reason?: string
  trace_id?: string
}

export interface DataProviderStatus {
  source: 'primary' | 'secondary' | 'cache'
  degraded: boolean
  degraded_reason?: string
  refreshed_at: string
}

export interface ScreenerParams {
  markets: Market[]
  mode: ScreenerMode
  as_of_date?: string
  return_window_days: number
  top_n: number
  turnover_threshold: number
  amount_threshold: number
  amplitude_threshold: number
  step_configs?: ScreenerStepConfigs
}

export interface ScreenerStep1Config {
  rank_start: number
  top_n: number
  turnover_threshold: number
  amount_threshold: number
  amplitude_threshold: number
}

export interface ScreenerStep2Config {
  retrace_min: number
  retrace_max: number
  max_pullback_days: number
  min_ma10_above_ma20_days: number
  min_ma5_above_ma10_days: number
  max_price_vs_ma20: number
  require_above_ma20: boolean
  allow_b_trend: boolean
}

export interface ScreenerStep3Config {
  min_vol_slope20: number
  min_up_down_volume_ratio: number
  max_pullback_volume_ratio: number
  allow_blowoff_top: boolean
  allow_divergence_5d: boolean
  allow_upper_shadow_risk: boolean
  allow_degraded: boolean
}

export interface ScreenerStep4Config {
  final_top_n: number
  min_ai_confidence: number
  allowed_theme_stages: ThemeStage[]
  allow_degraded: boolean
}

export interface ScreenerStepConfigs {
  step1: ScreenerStep1Config
  step2: ScreenerStep2Config
  step3: ScreenerStep3Config
  step4: ScreenerStep4Config
}

export interface ScreenerResult {
  symbol: string
  name: string
  latest_price: number
  day_change: number
  day_change_pct: number
  score: number
  ret40: number
  turnover20: number
  amount20: number
  amplitude20: number
  retrace20: number
  pullback_days: number
  ma10_above_ma20_days: number
  ma5_above_ma10_days: number
  price_vs_ma20: number
  vol_slope20: number
  up_down_volume_ratio: number
  pullback_volume_ratio: number
  has_blowoff_top: boolean
  has_divergence_5d: boolean
  has_upper_shadow_risk: boolean
  ai_confidence: number
  theme_stage: ThemeStage
  trend_class: TrendClass
  stage: 'Early' | 'Mid' | 'Late'
  labels: string[]
  reject_reasons: string[]
  degraded: boolean
  degraded_reason?: string
}

export type ScreenerPoolKey = 'input' | 'step1' | 'step2' | 'step3' | 'step4' | 'final' | 'watch'

export interface ScreenerStepSummary {
  input_count: number
  step1_count: number
  step2_count: number
  step3_count: number
  step4_count: number
  final_count?: number
  watch_count?: number
}

export interface ScreenerStepPools {
  input: ScreenerResult[]
  step1: ScreenerResult[]
  step2: ScreenerResult[]
  step3: ScreenerResult[]
  step4: ScreenerResult[]
  final: ScreenerResult[]
  watch: ScreenerResult[]
}

export interface WatchPoolConfig {
  enabled: boolean
  source_mode: 'filter' | 'strategy'
  strategy_id: string
  ret40_min: number
  ret40_max: number
  turnover20_min: number
  amount20_min: number
  amplitude20_min: number
  trend_classes: TrendClass[]
  retrace20_min: number
  retrace20_max: number
  pullback_days_max: number
  vol_slope20_min: number
  up_down_volume_ratio_min: number
  top_n: number
}

export interface B1ScreenerResult {
  symbol: string
  name: string
  close: number
  change_pct: number
  amplitude_pct: number
  volume_ratio: number
  kdj_j: number
  weekly_macd: number
  monthly_macd: number
}

export interface B1ScreenerResponse {
  total_scanned: number
  hit_count: number
  hits: B1ScreenerResult[]
  elapsed_sec: number
}

export interface B1ScreenerRequest {
  markets: Market[]
  as_of_date?: string
  b1_params?: B1Params
}

export interface B1Params {
  vol_ratio: number
  chg_limit: number
  amp_limit_10cm: number
  amp_limit_20cm: number
  kdj_j_upper: number
}

export interface B1ScreenerResult {
  symbol: string
  name: string
  close: number
  change_pct: number
  amplitude_pct: number
  volume_ratio: number
  kdj_j: number
  weekly_macd: number
  monthly_macd: number
}

export interface B1ScreenerResponse {
  total_scanned: number
  hit_count: number
  hits: B1ScreenerResult[]
  b1_params: B1Params
  elapsed_sec: number
}

export interface ScreenerRunResponse {
  run_id: string
}

export interface ScreenerRunDetail {
  run_id: string
  created_at: string
  as_of_date?: string
  params: ScreenerParams
  step_configs?: ScreenerStepConfigs
  step_summary: ScreenerStepSummary
  step_pools: ScreenerStepPools
  results: ScreenerResult[]
  degraded: boolean
  degraded_reason?: string
}

export interface CandlePoint {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  price_source?: PriceSource
}

export interface IntradayPoint {
  time: string
  price: number
  avg_price: number
  volume: number
  price_source?: PriceSource
}

export interface IntradayPayload {
  symbol: string
  date: string
  points: IntradayPoint[]
  degraded: boolean
  degraded_reason?: string
}

export interface TrendClassification {
  trend_class: TrendClass
  confidence: number
  reason: string
}

export interface StockAnalysis {
  symbol: string
  suggest_start_date: string
  suggest_stage: 'Early' | 'Mid' | 'Late'
  suggest_trend_class: TrendClass
  confidence: number
  reason: string
  theme_stage: ThemeStage
  degraded: boolean
  degraded_reason?: string
}

export interface StockAnnotation {
  symbol: string
  start_date: string
  stage: 'Early' | 'Mid' | 'Late'
  trend_class: TrendClass
  decision: '保留' | '排除'
  notes: string
  updated_by: 'manual' | 'auto'
}

export interface StockAnalysisResponse {
  analysis: StockAnalysis
  annotation?: StockAnnotation
  signal?: SignalResult
}

export interface SignalResult {
  symbol: string
  name: string
  primary_signal: SignalType
  secondary_signals: SignalType[]
  trigger_date: string
  expire_date: string
  signal_age_days?: number
  trigger_reason: string
  priority: number
  wyckoff_phase?: string
  wyckoff_signal?: string
  structure_hhh?: string
  wy_event_count?: number
  wy_sequence_ok?: boolean
  entry_quality_score?: number
  wy_events?: string[]
  wy_risk_events?: string[]
  wy_event_dates?: Record<string, string>
  wy_event_chain?: Array<{
    event: string
    date: string
    category?: string
  }>
  phase_hint?: string
  scan_mode?: SignalScanMode
  event_strength_score?: number
  phase_score?: number
  structure_score?: number
  trend_score?: number
  volatility_score?: number
  health_score?: number
  slope_stability?: number
  volatility_stability?: number
  pullback_quality?: number
  event_score?: number
  event_grade?: 'A' | 'B' | 'C'
  event_background_score?: number
  event_position_score?: number
  event_vol_price_score?: number
  event_confirmation_score?: number
  candle_quality_score?: number
  cost_center_shift_score?: number
  weekly_context_score?: number
  weekly_context_multiplier?: number
  event_recency_score?: number
  phase_context_score?: number
  risk_score?: number
  confirmation_status?: 'confirmed' | 'partial' | 'unconfirmed' | 'risk_blocked'
  event_confirmation_map?: Record<string, string>
  event_grade_map?: Record<string, 'A' | 'B' | 'C'>
}

export interface SignalsResponse {
  items: SignalResult[]
  mode: SignalScanMode
  as_of_date?: string
  generated_at: string
  cache_hit: boolean
  degraded: boolean
  degraded_reason?: string
  source_count: number
  strategy_id?: StrategyId
  strategy_version?: string
  strategy_params?: Record<string, unknown>
  strategy_params_hash?: string
  notes?: string[]
}

export interface CrossValidateStrategyConfig {
  strategy_id: string
  strategy_params?: Record<string, unknown>
  trend_step?: TrendPoolStep
  mode?: SignalScanMode
}

export interface CrossValidateRequest {
  as_of_date?: string
  date_from?: string
  date_to?: string
  strategies: CrossValidateStrategyConfig[]
  mode?: SignalScanMode
  run_id?: string
  trend_step?: TrendPoolStep
  window_days?: number
  min_score?: number
  min_event_count?: number
  market_filters?: Market[]
  board_filters?: BoardFilter[]
  min_overlap?: number
}

export interface CrossValidateStockDetail {
  strategy_id: string
  strategy_name: string
  score: number
  health_score: number
  event_score: number
  event_grade: string
  wyckoff_phase: string
  trigger_date: string
  trigger_dates: string[]
  appearance_days: number
  primary_signal: string
}

export interface CrossValidateStockResult {
  symbol: string
  name: string
  overlap_count: number
  strategy_details: CrossValidateStockDetail[]
  best_score: number
  avg_score: number
  total_appearance_days: number
  date_range: string
  range_return_pct: number
  range_max_drawdown_pct: number
}

export interface CrossValidateResponse {
  as_of_date: string
  date_from: string
  date_to: string
  strategy_pools: Record<string, number>
  results: CrossValidateStockResult[]
  total_unique_stocks: number
  elapsed_sec: number
  warnings: string[]
}

export interface CrossValidateTaskStartResponse {
  task_id: string
}

export interface CrossValidateTaskProgress {
  total_scans: number
  completed_scans: number
  percent: number
  current_strategy_id: string
  current_scan_date: string
  message: string
  started_at: string
  updated_at: string
}

export type CrossValidateTaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface CrossValidateTaskStatusResponse {
  task_id: string
  status: CrossValidateTaskStatus
  progress: CrossValidateTaskProgress
  result: CrossValidateResponse | null
  error: string | null
  error_code: string | null
}

export interface CrossValidateHistoryRecord {
  id: string
  label: string
  created_at: string
  strategy_ids: string[]
  strategy_names: string[]
  result_count: number
  date_label: string
  mode: string
  as_of_date: string
  date_from: string
  date_to: string
}

export interface CrossValidateHistoryDetail {
  record: CrossValidateHistoryRecord
  response: CrossValidateResponse
  request_params: Record<string, unknown>
}

export interface CrossValidateBacktestStockInput {
  symbol: string
  trigger_date: string
}

export interface CrossValidateBacktestRequest {
  stocks: CrossValidateBacktestStockInput[]
  hold_days: number
  hold_until_today?: boolean
  buy_mode: string
}

export interface CrossValidateBacktestStockResult {
  symbol: string
  name: string
  buy_date: string
  sell_date: string
  buy_price: number
  sell_price: number
  return_pct: number
  is_win: boolean
}

export interface CrossValidateBacktestResponse {
  hold_days: number
  hold_until_today?: boolean
  buy_mode: string
  total_count: number
  valid_count: number
  win_count: number
  win_rate: number
  avg_return_pct: number
  median_return_pct: number
  max_return_pct: number
  min_return_pct: number
  stocks: CrossValidateBacktestStockResult[]
}

export interface SignalEtfBacktestConstituentInput {
  symbol: string
  name?: string
  signal_date: string
  signal_primary?: string
  signal_event?: string
  signal_reason?: string
}

export interface SignalEtfBacktestCreateRequest {
  strategy_id: string
  strategy_name?: string
  signal_date: string
  name?: string
  notes?: string
  constituents: SignalEtfBacktestConstituentInput[]
}

export interface SignalEtfBacktestAutoCreateRequest {
  run_id?: string
  mode?: SignalScanMode
  strategy_id: string
  strategy_name?: string
  date_from: string
  date_to: string
  trend_step?: TrendPoolStep
  market_filters?: Market[]
  board_filters?: BoardFilter[]
  strategy_params?: Record<string, unknown>
  window_days?: number
  min_score?: number
  require_sequence?: boolean
  min_event_count?: number
  signal_age_min?: number
  signal_age_max?: number
  name_prefix?: string
  notes?: string
  refresh_signals?: boolean
}

export interface SignalEtfBacktestAutoCreateItem {
  record_id: string
  name: string
  signal_date: string
  as_of_date: string
  total_constituents: number
}

export interface SignalEtfBacktestAutoCreateIssue {
  as_of_date: string
  reason: string
}

export interface SignalEtfBacktestAutoCreateResponse {
  run_id: string
  strategy_id: string
  strategy_name: string
  date_from: string
  date_to: string
  processed_dates: number
  created_count: number
  skipped_count: number
  failed_count: number
  created: SignalEtfBacktestAutoCreateItem[]
  skipped: SignalEtfBacktestAutoCreateIssue[]
  failed: SignalEtfBacktestAutoCreateIssue[]
}

export interface SignalEtfBacktestUpdateRequest {
  name?: string
  notes?: string
}

export interface SignalEtfBacktestPerformance {
  return_pct: number
  benchmark_return_pct: number
  excess_return_pct: number
  stock_win_rate: number
  daily_win_rate: number
  tradable_count: number
  skipped_count: number
}

export interface SignalEtfBacktestStrategyStats {
  strategy_id: string
  total_records: number
  win_rate_t1: number
  win_rate_t2: number
}

export interface SignalEtfBacktestSummary {
  t1: SignalEtfBacktestPerformance
  t2: SignalEtfBacktestPerformance
  strategy_stats: SignalEtfBacktestStrategyStats
  holding_period_days?: number
  holding_target_date?: string
  holding_return_pct?: number
}

export interface SignalEtfBacktestRecord {
  record_id: string
  name: string
  notes: string
  signal_date: string
  strategy_id: string
  strategy_name: string
  benchmark_symbol: string
  total_constituents: number
  created_at: string
  updated_at: string
  summary: SignalEtfBacktestSummary
}

export interface SignalEtfBacktestConstituentDetail {
  symbol: string
  name: string
  signal_date: string
  signal_primary: string
  signal_event: string
  signal_reason: string
  current_date?: string
  current_price?: number
  buy_date_t1?: string
  buy_price_t1?: number
  return_pct_t1?: number
  status_t1: 'bought' | 'skipped'
  buy_date_t2?: string
  buy_price_t2?: number
  return_pct_t2?: number
  status_t2: 'bought' | 'skipped'
  holding_period_days?: number
  holding_target_date?: string
  return_pct_holding?: number
}

export interface SignalEtfBacktestCurvePoint {
  date: string
  etf_return_t1?: number
  etf_return_t2?: number
  benchmark_return_t1?: number
  benchmark_return_t2?: number
  excess_return_t1?: number
  excess_return_t2?: number
}

export interface SignalEtfBacktestDetail extends SignalEtfBacktestRecord {
  benchmark_available: boolean
  constituents: SignalEtfBacktestConstituentDetail[]
  curve: SignalEtfBacktestCurvePoint[]
}

export interface SignalEtfBacktestListResponse {
  items: SignalEtfBacktestRecord[]
}

export interface SignalEtfBacktestDeleteResponse {
  deleted: boolean
  record_id: string
}

export interface SimTradeOrder {
  order_id: string
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  signal_date: string
  submit_date: string
  status: 'pending' | 'filled' | 'cancelled' | 'rejected'
  expected_fill_date?: string
  filled_date?: string
  estimated_price?: number
  cash_impact?: number
  status_reason?: string
  reject_reason?: string
}

export interface SimTradeFill {
  order_id: string
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  fill_date: string
  fill_price: number
  price_source: PriceSource
  gross_amount: number
  net_amount: number
  fee_commission: number
  fee_stamp_tax: number
  fee_transfer: number
  warning?: string
}

export interface SimTradingConfig {
  initial_capital: number
  commission_rate: number
  min_commission: number
  stamp_tax_rate: number
  transfer_fee_rate: number
  slippage_rate: number
}

export interface SimOrdersResponse {
  items: SimTradeOrder[]
  total: number
  page: number
  page_size: number
}

export interface SimFillsResponse {
  items: SimTradeFill[]
  total: number
  page: number
  page_size: number
}

export interface SimSettleResponse {
  settled_count: number
  filled_count: number
  pending_count: number
  as_of_date: string
  last_settle_at: string
}

export interface SimResetResponse {
  success: true
  as_of_date: string
  cash: number
}

export interface PortfolioPosition {
  symbol: string
  name: string
  quantity: number
  available_quantity: number
  avg_cost: number
  current_price: number
  market_value: number
  pnl_amount: number
  pnl_ratio: number
  holding_days: number
}

export interface PortfolioSnapshot {
  as_of_date: string
  total_asset: number
  cash: number
  position_value: number
  realized_pnl: number
  unrealized_pnl: number
  pending_order_count: number
  positions: PortfolioPosition[]
}

export interface ReviewStats {
  win_rate: number
  total_return: number
  max_drawdown: number
  avg_pnl_ratio: number
  trade_count: number
  win_count: number
  loss_count: number
  profit_factor: number
}

export interface TradeRecord {
  symbol: string
  buy_date: string
  buy_price: number
  sell_date: string
  sell_price: number
  quantity: number
  holding_days: number
  pnl_amount: number
  pnl_ratio: number
}

export interface EquityPoint {
  date: string
  equity: number
  realized_pnl: number
}

export interface DrawdownPoint {
  date: string
  drawdown: number
}

export interface MonthlyReturnPoint {
  month: string
  return_ratio: number
  pnl_amount: number
  trade_count: number
}

export interface ReviewRange {
  date_from: string
  date_to: string
  date_axis: 'sell' | 'buy'
}

export interface ReviewResponse {
  stats: ReviewStats
  trades: TradeRecord[]
  equity_curve: EquityPoint[]
  drawdown_curve: DrawdownPoint[]
  monthly_returns: MonthlyReturnPoint[]
  top_trades: TradeRecord[]
  bottom_trades: TradeRecord[]
  cost_snapshot: SimTradingConfig
  range: ReviewRange
}

export interface BacktestRunRequest {
  mode: SignalScanMode
  run_id?: string
  trend_step: TrendPoolStep
  pool_roll_mode: BacktestPoolRollMode
  board_filters?: BoardFilter[]
  strategy_id?: StrategyId
  strategy_params?: Record<string, unknown>
  date_from: string
  date_to: string
  window_days: number
  min_score: number
  require_sequence: boolean
  min_event_count: number
  entry_events: string[]
  exit_events: string[]
  event_judgment_profile_id?: string
  event_judgment_profile_hash?: string
  event_judgment_profile_snapshot?: Record<string, unknown>
  initial_capital: number
  position_pct: number
  max_positions: number
  stop_loss: number
  take_profit: number
  ambiguity_policy?: 'conservative' | 'optimistic'
  trailing_stop_pct?: number
  intraday_trailing_enabled?: boolean
  daily_trailing_clear_enabled?: boolean
  intraday_trailing_reduce_ratio?: number
  daily_trailing_confirm_days?: number
  max_hold_days: number
  fee_bps: number
  prioritize_signals: boolean
  priority_mode: BacktestPriorityMode
  priority_topk_per_day: number
  rank_weight_health?: number
  rank_weight_event?: number
  health_score_min?: number
  event_score_min?: number
  event_grade_min?: 'A' | 'B' | 'C'
  require_key_event_confirmation?: boolean
  execution_path_preference?: 'auto' | 'matrix' | 'legacy'
  matrix_event_semantic_version?: 'matrix_v1' | 'aligned_wyckoff_v2'
  enforce_t1: boolean
  entry_delay_days?: number
  delay_invalidation_enabled?: boolean
  max_symbols: number
  enable_advanced_analysis?: boolean
}

export interface BacktestTrade {
  symbol: string
  name: string
  signal_date: string
  entry_date: string
  exit_date: string
  entry_signal: string
  entry_phase: string
  entry_quality_score: number
  candle_quality_score?: number
  cost_center_shift_score?: number
  weekly_context_score?: number
  weekly_context_multiplier?: number
  health_score?: number
  event_score?: number
  risk_score?: number
  confirmation_status?: 'confirmed' | 'partial' | 'unconfirmed' | 'risk_blocked'
  event_grade?: 'A' | 'B' | 'C'
  phase_context_score?: number
  event_recency_score?: number
  exit_reason: string
  delay_entry_days?: number
  delay_window_days?: number
  quantity: number
  entry_price: number
  exit_price: number
  holding_days: number
  pnl_amount: number
  pnl_ratio: number
}

export interface BacktestResponse {
  stats: ReviewStats
  trades: BacktestTrade[]
  equity_curve: EquityPoint[]
  drawdown_curve: DrawdownPoint[]
  monthly_returns: MonthlyReturnPoint[]
  top_trades: BacktestTrade[]
  bottom_trades: BacktestTrade[]
  cost_snapshot: SimTradingConfig
  range: ReviewRange
  notes: string[]
  candidate_count: number
  skipped_count: number
  fill_rate: number
  max_concurrent_positions: number
  plan_signals?: BacktestTrade[]
  open_signals?: BacktestTrade[]
  risk_metrics?: BacktestRiskMetrics | null
  stability_diagnostics?: BacktestStabilityDiagnostics | null
  regime_breakdown?: BacktestRegimeBucket[]
  monte_carlo?: BacktestMonteCarloSummary | null
  walk_forward?: BacktestWalkForwardReport | null
  execution_path?: 'matrix' | 'legacy' | null
  strategy_id?: StrategyId
  strategy_version?: string
  strategy_params?: Record<string, unknown>
  strategy_params_hash?: string
  effective_run_request?: BacktestRunRequest | null
}

export interface BacktestRiskMetrics {
  sharpe: number
  sortino: number
  calmar: number
  expectancy: number
  avg_win_pnl_ratio: number
  avg_loss_pnl_ratio: number
  max_consecutive_losses: number
  recovery_days: number
}

export interface BacktestStabilityDiagnostics {
  stability_score: number
  min_trade_count_threshold: number
  trade_count_penalty: number
  neighborhood_consistency: number
  return_variance_penalty: number
  monthly_return_std: number
  notes: string[]
}

export interface BacktestRegimeBucket {
  regime: 'bull' | 'range' | 'bear'
  label: string
  trade_count: number
  win_rate: number
  total_return: number
  avg_pnl_ratio: number
  max_drawdown: number
}

export interface BacktestMonteCarloSummary {
  simulations: number
  seed: number
  total_return_p5: number
  total_return_p50: number
  total_return_p95: number
  max_drawdown_p5: number
  max_drawdown_p50: number
  max_drawdown_p95: number
  ruin_probability: number
}

export interface BacktestWalkForwardFold {
  fold_index: number
  train_date_from: string
  train_date_to: string
  test_date_from: string
  test_date_to: string
  selected_params: BacktestPlateauParams
  train_score: number
  test_score: number
  train_stats: ReviewStats
  test_stats: ReviewStats
}

export interface BacktestWalkForwardReport {
  fold_count: number
  candidate_count: number
  oos_pass_rate: number
  avg_test_return: number
  avg_test_win_rate: number
  folds: BacktestWalkForwardFold[]
  notes: string[]
}

export interface BacktestTaskStartResponse {
  task_id: string
}

export interface BacktestTaskStageTiming {
  stage_key: string
  label: string
  elapsed_ms: number
}

export interface BacktestTaskProgress {
  mode: BacktestPoolRollMode
  current_date?: string | null
  processed_dates: number
  total_dates: number
  percent: number
  message: string
  warning?: string | null
  stage_timings: BacktestTaskStageTiming[]
  started_at: string
  updated_at: string
}

export interface BacktestTaskStatusResponse {
  task_id: string
  status: 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  progress: BacktestTaskProgress
  result?: BacktestResponse | null
  error?: string | null
  error_code?: string | null
}

export interface BacktestTaskListResponse {
  items: BacktestTaskStatusResponse[]
}

export interface BacktestTaskDeleteResponse {
  deleted: boolean
  task_id: string
}

export interface BacktestPlateauRunRequest {
  base_payload: BacktestRunRequest
  sampling_mode?: 'grid' | 'lhs'
  window_days_list: number[]
  min_score_list: number[]
  stop_loss_list: number[]
  take_profit_list: number[]
  trailing_stop_pct_list: number[]
  intraday_trailing_reduce_ratio_list: number[]
  max_positions_list: number[]
  position_pct_list: number[]
  max_symbols_list: number[]
  priority_topk_per_day_list: number[]
  sample_points?: number
  random_seed?: number
  max_points: number
}

export interface BacktestPlateauParams {
  window_days: number
  min_score: number
  stop_loss: number
  take_profit: number
  trailing_stop_pct: number
  intraday_trailing_reduce_ratio?: number
  max_positions: number
  position_pct: number
  max_symbols: number
  priority_topk_per_day: number
}

export interface BacktestPlateauPoint {
  params: BacktestPlateauParams
  stats: ReviewStats
  candidate_count: number
  skipped_count: number
  fill_rate: number
  max_concurrent_positions: number
  annual_trades?: number
  score: number
  point_score?: number
  local_score?: number
  plateau_score?: number
  neighbor_pass_rate?: number
  neighbor_median_score?: number
  neighbor_p25_score?: number
  sensitivity_penalty?: number
  passes_hard_filters?: boolean
  hard_filter_failures?: string[]
  region_id?: string | null
  region_rank?: number | null
  cache_hit: boolean
  detail_key?: string | null
  error?: string | null
}

export interface BacktestPlateauRegionSummary {
  region_id: string
  region_rank: number
  point_count: number
  parameter_ranges: Record<string, string>
  center_point: BacktestPlateauPoint
  median_local_score: number
  p25_local_score: number
  median_point_score: number
  median_total_return: number
  best_total_return: number
  center_margin_score: number
  size_score: number
  oos_pass_rate?: number | null
  region_score: number
  walk_forward?: BacktestWalkForwardReport | null
}

export interface BacktestPlateauCorrelationRow {
  parameter: string
  parameter_label: string
  score_corr: number
  total_return_corr: number
  win_rate_corr: number
}

export interface BacktestPlateauResponse {
  base_payload: BacktestRunRequest
  strategy_version?: string
  total_combinations: number
  evaluated_combinations: number
  points: BacktestPlateauPoint[]
  best_point?: BacktestPlateauPoint | null
  recommended_point?: BacktestPlateauPoint | null
  peak_point?: BacktestPlateauPoint | null
  regions?: BacktestPlateauRegionSummary[]
  correlations?: BacktestPlateauCorrelationRow[]
  generated_at: string
  notes: string[]
}

export interface BacktestPlateauTaskProgress {
  sampling_mode: 'grid' | 'lhs'
  processed_points: number
  total_points: number
  percent: number
  message: string
  started_at: string
  updated_at: string
}

export interface BacktestPlateauTaskStatusResponse {
  task_id: string
  status: 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  progress: BacktestPlateauTaskProgress
  result?: BacktestPlateauResponse | null
  error?: string | null
  error_code?: string | null
}

export interface BacktestPlateauTaskListResponse {
  items: BacktestPlateauTaskStatusResponse[]
}

export interface BacktestPlateauTaskDeleteResponse {
  deleted: boolean
  task_id: string
}

export interface BacktestPlateauPointDetailResponse {
  task_id: string
  detail_key: string
  saved_at: string
  params: BacktestPlateauParams
  run_request: BacktestRunRequest
  run_result: BacktestResponse
}

export interface StrategyCapabilities {
  supports_matrix: boolean
  supports_signal_age_filter: boolean
  supports_entry_delay: boolean
}

export interface StrategyDescriptor {
  strategy_id: StrategyId
  name: string
  version: string
  enabled: boolean
  is_default: boolean
  capabilities: StrategyCapabilities
  description?: string
  strategy_playbook?: Record<string, unknown>
  strategy_params_schema: Record<string, unknown>
  strategy_params_defaults: Record<string, unknown>
}

export interface StrategyCatalogResponse {
  items: StrategyDescriptor[]
}

export interface EventJudgmentMetricOption {
  metric_key: string
  label: string
  description: string
}

export interface EventJudgmentRuleOption {
  rule_key: string
  label: string
  description: string
  category: string
  value_type: 'number' | 'integer' | 'boolean'
  min_value?: number | null
  max_value?: number | null
  step?: number | null
  recommended_min?: number | null
  recommended_max?: number | null
  risk_hint_low?: string
  risk_hint_high?: string
  default_value: number | boolean
}

export interface EventJudgmentDimension {
  dimension_id: string
  label: string
  metric_key: string
  weight: number
  invert: boolean
  enabled: boolean
}

export interface EventJudgmentRuleValue {
  rule_key: string
  value: number | boolean
}

export interface EventJudgmentProfile {
  profile_id: string
  name: string
  description: string
  score_mode: 'legacy_formula' | 'dimension_weighted'
  is_system: boolean
  updated_at: string
  dimensions: EventJudgmentDimension[]
  rule_values: EventJudgmentRuleValue[]
}

export interface EventJudgmentCatalogResponse {
  active_profile_id: string
  metric_options: EventJudgmentMetricOption[]
  rule_options: EventJudgmentRuleOption[]
  profiles: EventJudgmentProfile[]
}

export interface EventJudgmentProfileUpsertRequest {
  profile_id?: string
  name: string
  description?: string
  dimensions: EventJudgmentDimension[]
  rule_values?: EventJudgmentRuleValue[]
  make_active?: boolean
}

export interface EventJudgmentProfileApplyRequest {
  profile_id: string
}

export interface EventJudgmentProfileDeleteResponse {
  success: true
  profile_id: string
}

export interface BacktestReportManifestFile {
  path: string
  sha256: string
  bytes: number
}

export interface BacktestReportManifestApp {
  name: string
  version: string
}

export interface BacktestReportManifest {
  schema_version: 'ftbt-1.0'
  package_type: 'backtest_report'
  created_at: string
  report_id: string
  app: BacktestReportManifestApp
  files: BacktestReportManifestFile[]
}

export interface BacktestReportBuildRequest {
  run_request: BacktestRunRequest
  run_result: BacktestResponse
  report_html: string
  report_xlsx_base64: string
  plateau_result?: BacktestPlateauResponse | null
  plateau_task_id?: string | null
  plateau_point_details?: BacktestPlateauPointDetailResponse[]
  report_id?: string
  app_name?: string
  app_version?: string
}

export interface BacktestReportBuildResponse {
  report_id: string
  file_name: string
  file_base64: string
  manifest: BacktestReportManifest
}

export interface BacktestReportSummary {
  report_id: string
  created_at: string
  first_imported_at: string
  last_imported_at: string
  source_file_name: string
  package_size_bytes: number
  trade_count: number
  total_return: number
  max_drawdown: number
  win_rate: number
  date_from: string
  date_to: string
  has_plateau_result: boolean
}

export interface BacktestReportListResponse {
  items: BacktestReportSummary[]
}

export interface BacktestReportDetail {
  summary: BacktestReportSummary
  manifest: BacktestReportManifest
  run_request: BacktestRunRequest
  run_result: BacktestResponse
  plateau_result?: BacktestPlateauResponse | null
}

export interface BacktestReportImportResponse {
  summary: BacktestReportSummary
}

export interface BacktestReportDeleteResponse {
  deleted: boolean
  report_id: string
}

export interface BacktestStrategySignalStrategyInfo {
  strategy_id: string
  strategy_name: string
  signal_count: number
  date_from: string
  date_to: string
  win_rate_1d: number
  avg_return_1d: number
  win_rate_3d: number
  avg_return_3d: number
  win_rate_5d: number
  avg_return_5d: number
}

export interface BacktestStrategySignalPoint {
  signal_date: string
  entry_signal: string
  entry_phase: string
  event_grade: 'A' | 'B' | 'C'
  entry_quality_score: number
  strategy_id: string
  strategy_name: string
  task_id: string
  source_type: 'task' | 'report' | 'scan'
  return_1d: number
  return_3d: number
  return_5d: number
}

export interface BacktestStrategySignalsResponse {
  symbol: string
  strategies: BacktestStrategySignalStrategyInfo[]
  signals: BacktestStrategySignalPoint[]
}

export type ReviewTagType = 'emotion' | 'reason'

export interface ReviewTag {
  id: string
  name: string
  color: string
  created_at: string
}

export interface ReviewTagsPayload {
  emotion: ReviewTag[]
  reason: ReviewTag[]
}

export interface ReviewTagCreateRequest {
  name: string
}

export interface TradeFillTagUpdateRequest {
  emotion_tag_id?: string | null
  reason_tag_ids: string[]
}

export interface TradeFillTagAssignment {
  order_id: string
  emotion_tag_id?: string | null
  reason_tag_ids: string[]
  updated_at: string
}

export interface ReviewTagStatItem {
  tag_id: string
  name: string
  color: string
  count: number
  gross_amount: number
  net_amount: number
}

export interface ReviewTagStatsResponse {
  date_from?: string
  date_to?: string
  emotion: ReviewTagStatItem[]
  reason: ReviewTagStatItem[]
}

export interface DailyReviewPayload {
  title: string
  market_summary: string
  operations_summary: string
  reflection: string
  tomorrow_plan: string
  summary: string
  tags: string[]
}

export interface DailyReviewRecord extends DailyReviewPayload {
  date: string
  updated_at: string
}

export interface DailyReviewListResponse {
  items: DailyReviewRecord[]
}

export interface WeeklyReviewPayload {
  start_date: string
  end_date: string
  core_goals: string
  achievements: string
  resource_analysis: string
  market_rhythm: string
  next_week_strategy: string
  key_insight: string
  tags: string[]
}

export interface WeeklyReviewRecord extends WeeklyReviewPayload {
  week_label: string
  updated_at: string
}

export interface WeeklyReviewListResponse {
  items: WeeklyReviewRecord[]
}

export interface MarketNewsItem {
  title: string
  url: string
  snippet: string
  pub_date: string
  source_name: string
}

export interface MarketNewsResponse {
  query: string
  age_hours: number
  symbol?: string
  symbol_name?: string
  source_domains: string[]
  items: MarketNewsItem[]
  fetched_at: string
  cache_hit: boolean
  fallback_used: boolean
  degraded: boolean
  degraded_reason?: string
}

export interface AIAnalysisRecord {
  provider: string
  symbol: string
  name: string
  fetched_at: string
  source_urls: string[]
  summary: string
  conclusion: string
  confidence: number
  breakout_date?: string
  trend_bull_type?: string
  theme_name?: string
  rise_reasons?: string[]
  error_code?: string
}

export interface DeleteAIRecordResponse {
  deleted: boolean
  remaining: number
}

export type AIChatPage =
  | 'chart'
  | 'screener'
  | 'signals'
  | 'signals_backtest'
  | 'cross_validate'
  | 'backtest'
  | 'strategy'
  | 'event_judgment'
  | 'review'
  | 'trade'
  | 'portfolio'
  | 'market_trend'
  | 'sector_capital'
  | 'abnormal_movement'
  | 'sentiment_valuation'
  | 'settings'
  | 'ai_records'
  | 'generic'

export interface AIChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export interface AIChatContext {
  page: AIChatPage
  title: string
  as_of_date?: string | null
  symbol?: string | null
  refs?: Record<string, string>
  payload?: Record<string, unknown>
}

export interface AIChatUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface AIChatRequest {
  session_id?: string | null
  messages: AIChatMessage[]
  context: AIChatContext
  stream?: boolean
}

export interface AIChatResponse {
  session_id: string
  message: AIChatMessage
  usage?: AIChatUsage | null
  citations?: string[]
}

export interface AIPlaybookResponse {
  playbook: Record<string, unknown>
  text: string
}

export interface AILocalPlaybookResponse {
  principles: string[]
  updated_at: string
}

export interface AIQuickPromptTemplate {
  id: string
  page: string
  label: string
  prompt: string
  pinned?: boolean
}

export interface AIQuickPromptsResponse {
  templates: AIQuickPromptTemplate[]
}

export interface AISessionSummary {
  session_id: string
  updated_at: string
  page: string
  title: string
  message_count: number
}

export interface AISessionsListResponse {
  items: AISessionSummary[]
}

export interface AIUsageResponse {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface AIParameterProposalChange {
  change_id: string
  scope: string
  target: string
  path: string[]
  old_value?: unknown
  new_value?: unknown
  reason?: string
  risk_level?: string
  validation_error?: string | null
  /** 后端无法直接写回前端表单时标记为 true,前端需据此自行写回表单(而非显示"已应用")。 */
  pending_frontend?: boolean
}

export interface AIParameterProposalResponse {
  proposal_id: string
  created_at: string
  expires_at: string
  context_page: string
  summary: string
  changes: AIParameterProposalChange[]
}

export interface AIParameterProposalApplyResponse {
  proposal_id: string
  applied: AIParameterProposalChange[]
  rejected: Array<{ change_id?: string; reason?: string }>
}

export interface AIProviderConfig {
  id: string
  label: string
  base_url: string
  model: string
  api_key: string
  api_key_path: string
  enabled: boolean
}

export interface AISourceConfig {
  id: string
  name: string
  url: string
  enabled: boolean
}

export interface AIProviderTestRequest {
  provider: AIProviderConfig
  fallback_api_key: string
  fallback_api_key_path: string
  timeout_sec: number
}

export interface AIProviderTestResponse {
  ok: boolean
  provider_id: string
  latency_ms: number
  message: string
  error_code?: string
}

export interface AppConfig {
  tdx_data_path: string
  market_data_source: MarketDataSource
  akshare_cache_dir: string
  markets: Market[]
  return_window_days: number
  candles_window_bars: number
  backtest_matrix_engine_enabled: boolean
  backtest_default_execution_path_preference: 'auto' | 'matrix' | 'legacy'
  backtest_plateau_workers: number
  top_n: number
  turnover_threshold: number
  amount_threshold: number
  amplitude_threshold: number
  initial_capital: number
  ai_provider: string
  ai_timeout_sec: number
  ai_retry_count: number
  api_key: string
  api_key_path: string
  ai_providers: AIProviderConfig[]
  ai_sources: AISourceConfig[]
}

export interface SystemStorageStatus {
  app_state_path: string
  app_state_exists: boolean
  sim_state_path: string
  sim_state_exists: boolean
  akshare_cache_dir: string
  akshare_cache_dir_resolved: string
  akshare_cache_dir_exists: boolean
  akshare_cache_file_count: number
  akshare_cache_candidates: string[]
  wyckoff_event_store_path?: string
  wyckoff_event_store_exists?: boolean
  wyckoff_event_store_read_only?: boolean
}

export interface WyckoffEventStoreStatsResponse {
  enabled: boolean
  read_only: boolean
  db_path: string
  db_exists: boolean
  db_record_count: number
  runtime_cache_size: number
  cache_hits: number
  cache_misses: number
  cache_hit_rate: number
  cache_miss_rate: number
  snapshot_reads: number
  avg_snapshot_read_ms: number
  lazy_fill_writes: number
  backfill_runs: number
  backfill_writes: number
  quality_empty_events: number
  quality_score_outliers: number
  quality_date_misaligned: number
  last_backfill_started_at?: string | null
  last_backfill_finished_at?: string | null
  last_backfill_duration_sec?: number | null
  last_backfill_scan_dates: number
  last_backfill_symbols: number
  last_backfill_quality_empty_events: number
  last_backfill_quality_score_outliers: number
  last_backfill_quality_date_misaligned: number
}

export interface WyckoffEventStoreBackfillRequest {
  date_from: string
  date_to: string
  markets?: Market[]
  window_days_list?: number[]
  max_symbols_per_day?: number
  force_rebuild?: boolean
}

export interface WyckoffEventStoreBackfillResponse {
  ok: boolean
  message: string
  date_from: string
  date_to: string
  markets: Market[]
  window_days_list: number[]
  scan_dates: number
  loaded_rows_total: number
  symbols_scanned: number
  cache_hits: number
  cache_misses: number
  computed_count: number
  write_count: number
  quality_empty_events: number
  quality_score_outliers: number
  quality_date_misaligned: number
  started_at: string
  finished_at: string
  duration_sec: number
  warnings: string[]
}

export interface MarketDataSyncRequest {
  provider: MarketSyncProvider
  mode: MarketSyncMode
  symbols: string
  all_market: boolean
  limit: number
  start_date: string
  end_date: string
  initial_days: number
  sleep_sec: number
  out_dir: string
}

export interface MarketDataSyncResponse {
  ok: boolean
  provider: MarketSyncProvider
  mode: MarketSyncMode
  message: string
  out_dir: string
  symbol_count: number
  ok_count: number
  fail_count: number
  skipped_count: number
  new_rows_total: number
  started_at: string
  finished_at: string
  duration_sec: number
  errors: string[]
}

export interface MarketTrendLeadersRequest {
  date_from?: string
  date_to?: string
  window_days?: number
  daily_top_n?: number
  board_filters?: BoardFilter[]
  min_amount_avg?: number
}

export interface MarketTrendLeaderItem {
  symbol: string
  name: string
  return_pct: number
  window_return_pct: number
  amount_avg: number
  board?: string | null
  leader_days: number
  first_leader_date?: string | null
  last_leader_date?: string | null
  max_return_pct: number
}

export interface MarketTrendSeriesPoint {
  date: string
  return_pct: number
}

export interface MarketTrendSeriesItem {
  symbol: string
  name: string
  points: MarketTrendSeriesPoint[]
}

export interface MarketTrendLeadersResponse {
  date_from: string
  date_to: string
  window_days: number
  daily_top_n: number
  total_scanned: number
  dates: string[]
  cache_hits: number
  cache_misses: number
  live_days: number
  frozen_days: number
  computed_days: number
  leaders: MarketTrendLeaderItem[]
  series: MarketTrendSeriesItem[]
  elapsed_sec: number
}

export interface LimitUpLadderRequest {
  date_from?: string
  date_to?: string
  recent_days?: number
  historical_min_boards?: number
  board_filters?: BoardFilter[]
}

export interface LimitUpTimelinePoint {
  symbol: string
  name: string
  date: string
  board_height: number
}

export interface LimitUpStockSummary {
  symbol: string
  name: string
  max_board_height: number
  active_days: number
  latest_board_height: number
}

export interface LimitUpLadderResponse {
  date_from: string
  date_to: string
  recent_days: number
  historical_min_boards: number
  total_scanned: number
  dates: string[]
  timeline: LimitUpTimelinePoint[]
  stocks: LimitUpStockSummary[]
  elapsed_sec: number
}

export type AbnormalMovementScanMode = 'snapshot' | 'full'

export interface AbnormalMovementRequest {
  date_from?: string
  date_to?: string
  include_warnings?: boolean
  board_filters?: BoardFilter[]
  /** snapshot=仅截至日仍在异动（快）；full=含历史段 */
  scan_mode?: AbnormalMovementScanMode
  parallel_workers?: number
}

export interface AbnormalMovementDailyLimit {
  date: string
  day_offset: number
  cum_dev_10?: number | null
  cum_dev_30?: number | null
  max_dev_10?: number | null
  max_dev_30?: number | null
  max_stock_pct_10?: number | null
  max_stock_pct_30?: number | null
  effective_max_stock_pct?: number | null
  actual_deviation?: number | null
  actual_stock_pct?: number | null
  actual_index_pct?: number | null
  reset_10: boolean
  reset_30: boolean
}

export interface AbnormalMovementEventItem {
  symbol: string
  name: string
  board?: string | null
  kind: '10d' | '30d' | string
  status: 'warning' | 'triggered' | string
  entry_date: string
  trigger_date?: string | null
  as_of_date: string
  cum_deviation: number
  episode_day?: number
  threshold: number
  warn_threshold: number
  benchmark_symbol: string
  benchmark_name: string
  daily_limits: AbnormalMovementDailyLimit[]
}

export interface AbnormalMovementResponse {
  date_from: string
  date_to: string
  trigger_threshold_10: number
  trigger_threshold_30: number
  warn_threshold_10: number
  warn_threshold_30: number
  total_scanned: number
  dates: string[]
  events: AbnormalMovementEventItem[]
  elapsed_sec: number
  algorithm_version?: string
  trade_date_to?: string
  symbols_analyzed?: number
  symbols_skipped_no_index?: number
  scan_mode?: AbnormalMovementScanMode
  bars_needed?: number
  parallel_workers?: number
  prep_sec?: number
  load_sec?: number
  analyze_sec?: number
}

export interface SectorCapitalFlowRequest {
  date_from?: string
  date_to?: string
  daily_top_n?: number
  flow_window?: number
}

export interface SectorFlowTableRow {
  date: string
  sector: string
  return_pct: number
  flow_score: number
  amount: number
  amount_delta: number
  rank_return: number
  rank_flow: number
}

export interface SectorFlowSeriesPoint {
  date: string
  return_pct: number
  flow_score: number
}

export interface SectorFlowSeriesItem {
  sector: string
  points: SectorFlowSeriesPoint[]
}

export interface SectorFlowLeaderItem {
  sector: string
  leader_days: number
  max_return_pct: number
  avg_flow_score: number
  first_leader_date?: string | null
  last_leader_date?: string | null
}

export interface SectorCapitalFlowResponse {
  date_from: string
  date_to: string
  daily_top_n: number
  flow_window: number
  dates: string[]
  flow_table: SectorFlowTableRow[]
  series: SectorFlowSeriesItem[]
  leaders: SectorFlowLeaderItem[]
  elapsed_sec: number
}

export type IndustryPeTier =
  | 'bank_insurance'
  | 'traditional'
  | 'consumer'
  | 'semi_tech'
  | 'tech'
  | 'high_growth'

export interface ValuationFormValues {
  symbol: string
  name: string
  earningsYi: number
  growthRatePct: number
  basePe: number
  indexPoints: number
  sentimentCoef: number
  actualCapYi: number | null
}

export interface ValuationBreakdown {
  theoreticalCapYi: number
  earningsComponentYi: number
  growthCoef: number
  basePe: number
  indexCoef: number
  sentimentCoef: number
  impliedSentimentCoef: number | null
  gapPct: number | null
}

export interface SentimentValuationQuoteResponse {
  symbol: string
  name: string
  industry: string
  price: number | null
  market_cap_yi: number | null
  pe_ttm: number | null
  implied_earnings_yi: number | null
  suggested_pe: number | null
  suggested_pe_tier: string
  suggested_pe_label: string
  index_symbol: string
  index_close: number | null
  index_coef: number
  limit_up_count_60d: number
  big_gain_days_60d: number
  suggested_sentiment_coef: number
  implied_sentiment_coef: number | null
  degraded: boolean
  degraded_reason: string | null
}

export interface ValuationPreset {
  key: string
  symbol: string
  name: string
  earningsYi: number
  growthRatePct: number
  basePe: number
  indexPoints: number
  sentimentCoef: number
  actualCapYi: number
  note: string
}
