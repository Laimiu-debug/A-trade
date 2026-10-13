import { ApiError, apiRequest, buildApiUrl } from '@/shared/api/client'
import type {
  AIAnalysisRecord,
  AIChatRequest,
  AIChatResponse,
  AIChatContext,
  AIChatMessage,
  AIChatUsage,
  AILocalPlaybookResponse,
  AIParameterProposalApplyResponse,
  AIParameterProposalResponse,
  AIPlaybookResponse,
  AIProviderTestRequest,
  AIProviderTestResponse,
  AIQuickPromptTemplate,
  AIQuickPromptsResponse,
  AISessionsListResponse,
  AIUsageResponse,
  AppConfig,
  BacktestPlateauPointDetailResponse,
  BacktestTaskStartResponse,
  BacktestTaskListResponse,
  BacktestTaskDeleteResponse,
  BacktestTaskStatusResponse,
  BacktestReportBuildRequest,
  BacktestReportBuildResponse,
  BacktestReportDeleteResponse,
  BacktestStrategySignalsResponse,
  BacktestReportDetail,
  BacktestReportImportResponse,
  BacktestReportListResponse,
  BacktestResponse,
  BacktestPlateauResponse,
  BacktestPlateauTaskStatusResponse,
  BacktestPlateauTaskListResponse,
  BacktestPlateauTaskDeleteResponse,
  BacktestPlateauRunRequest,
  BacktestPoolRollMode,
  BacktestRunRequest,
  BoardFilter,
  CandlePoint,
  CrossValidateRequest,
  CrossValidateResponse,
  CrossValidateTaskStartResponse,
  CrossValidateTaskStatusResponse,
  CrossValidateHistoryRecord,
  CrossValidateHistoryDetail,
  CrossValidateBacktestRequest,
  CrossValidateBacktestResponse,
  DeleteAIRecordResponse,
  IntradayPayload,
  MarketNewsResponse,
  MarketDataSyncRequest,
  MarketDataSyncResponse,
  MarketTrendLeadersRequest,
  MarketTrendLeadersResponse,
  LimitUpLadderRequest,
  LimitUpLadderResponse,
  AbnormalMovementRequest,
  AbnormalMovementResponse,
  SectorCapitalFlowRequest,
  SectorCapitalFlowResponse,
  SentimentValuationQuoteResponse,
  Market,
  PortfolioSnapshot,
  DailyReviewListResponse,
  DailyReviewPayload,
  DailyReviewRecord,
  ReviewResponse,
  ReviewTag,
  ReviewTagCreateRequest,
  ReviewTagStatsResponse,
  ReviewTagsPayload,
  ReviewTagType,
  ScreenerParams,
  ScreenerRunDetail,
  ScreenerRunResponse,
  B1ScreenerResponse,
  SignalScanMode,
  SignalEtfBacktestCreateRequest,
  SignalEtfBacktestAutoCreateRequest,
  SignalEtfBacktestAutoCreateResponse,
  SignalEtfBacktestDeleteResponse,
  SignalEtfBacktestDetail,
  SignalEtfBacktestListResponse,
  SignalEtfBacktestRecord,
  SignalEtfBacktestUpdateRequest,
  SignalsResponse,
  TrendPoolStep,
  SimFillsResponse,
  SimOrdersResponse,
  SimResetResponse,
  SimSettleResponse,
  SimTradeFill,
  SimTradeOrder,
  SimTradingConfig,
  StockAnalysisResponse,
  StockAnnotation,
  StrategyCatalogResponse,
  EventJudgmentCatalogResponse,
  EventJudgmentProfile,
  EventJudgmentProfileApplyRequest,
  EventJudgmentProfileDeleteResponse,
  EventJudgmentProfileUpsertRequest,
  StrategyId,
  SystemStorageStatus,
  TradeFillTagAssignment,
  TradeFillTagUpdateRequest,
  WeeklyReviewListResponse,
  WeeklyReviewPayload,
  WeeklyReviewRecord,
  WyckoffEventStoreBackfillRequest,
  WyckoffEventStoreBackfillResponse,
  WyckoffEventStoreStatsResponse,
} from '@/types/contracts'

export function runScreener(params: ScreenerParams) {
  return apiRequest<ScreenerRunResponse>('/api/screener/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    timeoutMs: 420_000,
  })
}

export function getScreenerRun(runId: string) {
  return apiRequest<ScreenerRunDetail>(`/api/screener/runs/${runId}`, {
    timeoutMs: 60_000,
  })
}

export function getLatestScreenerRun() {
  return apiRequest<ScreenerRunDetail>('/api/screener/latest-run', {
    timeoutMs: 60_000,
  })
}

export function runB1Screener(params: {
  markets: string[]
  as_of_date?: string
  b1_params?: Record<string, unknown>
}) {
  return apiRequest<B1ScreenerResponse>('/api/screener/b1-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    timeoutMs: 600_000,
  })
}

export function scanMarketTrendLeaders(payload: MarketTrendLeadersRequest, options?: { signal?: AbortSignal }) {
  return apiRequest<MarketTrendLeadersResponse>('/api/market/trend-leaders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 0,
    signal: options?.signal,
  })
}

export function scanLimitUpLadder(payload: LimitUpLadderRequest, options?: { signal?: AbortSignal }) {
  return apiRequest<LimitUpLadderResponse>('/api/market/limit-up-ladder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 0,
    signal: options?.signal,
  })
}

export function scanAbnormalMovement(payload: AbnormalMovementRequest, options?: { signal?: AbortSignal }) {
  return apiRequest<AbnormalMovementResponse>('/api/market/abnormal-movement', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 0,
    signal: options?.signal,
  })
}

export function scanSectorCapitalFlow(payload: SectorCapitalFlowRequest) {
  return apiRequest<SectorCapitalFlowResponse>('/api/market/sector-capital-flow', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 120_000,
  })
}

export function getSentimentValuationQuote(symbol: string) {
  const params = new URLSearchParams({ symbol })
  return apiRequest<SentimentValuationQuoteResponse>(`/api/market/sentiment-valuation/quote?${params.toString()}`)
}

export function getStockCandles(symbol: string) {
  return apiRequest<{ symbol: string; candles: CandlePoint[]; degraded: boolean; degraded_reason?: string }>(
    `/api/stocks/${symbol}/candles`,
  )
}

export function getStockIntraday(symbol: string, date: string) {
  const query = new URLSearchParams({ date }).toString()
  return apiRequest<IntradayPayload>(`/api/stocks/${symbol}/intraday?${query}`)
}

export function getStockAnalysis(symbol: string) {
  return apiRequest<StockAnalysisResponse>(
    `/api/stocks/${symbol}/analysis`,
  )
}

export function updateStockAnnotation(symbol: string, payload: StockAnnotation) {
  return apiRequest<{ success: true; annotation: StockAnnotation }>(`/api/stocks/${symbol}/annotations`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getSignals(params?: {
  mode?: SignalScanMode
  run_id?: string
  trend_step?: TrendPoolStep
  strategy_id?: StrategyId
  strategy_params?: Record<string, unknown>
  market_filters?: Market[]
  board_filters?: BoardFilter[]
  as_of_date?: string
  refresh?: boolean
  window_days?: number
  min_score?: number
  require_sequence?: boolean
  min_event_count?: number
  signal_age_min?: number
  signal_age_max?: number
  backtest_date_from?: string
  backtest_pool_roll_mode?: BacktestPoolRollMode
  backtest_max_symbols?: number
}) {
  const query = new URLSearchParams()
  if (params?.mode) query.set('mode', params.mode)
  if (params?.run_id) query.set('run_id', params.run_id)
  if (params?.trend_step) query.set('trend_step', params.trend_step)
  if (params?.strategy_id) query.set('strategy_id', params.strategy_id)
  if (params?.strategy_params && Object.keys(params.strategy_params).length > 0) {
    query.set('strategy_params', JSON.stringify(params.strategy_params))
  }
  if (params?.market_filters?.length) {
    params.market_filters.forEach((item) => query.append('market_filters', item))
  }
  if (params?.board_filters?.length) {
    params.board_filters.forEach((item) => query.append('board_filters', item))
  }
  if (params?.as_of_date) query.set('as_of_date', params.as_of_date)
  if (typeof params?.refresh === 'boolean') query.set('refresh', String(params.refresh))
  if (typeof params?.window_days === 'number') query.set('window_days', String(params.window_days))
  if (typeof params?.min_score === 'number') query.set('min_score', String(params.min_score))
  if (typeof params?.require_sequence === 'boolean') {
    query.set('require_sequence', String(params.require_sequence))
  }
  if (typeof params?.min_event_count === 'number') {
    query.set('min_event_count', String(params.min_event_count))
  }
  if (typeof params?.signal_age_min === 'number') {
    query.set('signal_age_min', String(params.signal_age_min))
  }
  if (typeof params?.signal_age_max === 'number') {
    query.set('signal_age_max', String(params.signal_age_max))
  }
  if (params?.backtest_date_from) query.set('backtest_date_from', params.backtest_date_from)
  if (params?.backtest_pool_roll_mode) query.set('backtest_pool_roll_mode', params.backtest_pool_roll_mode)
  if (typeof params?.backtest_max_symbols === 'number') {
    query.set('backtest_max_symbols', String(params.backtest_max_symbols))
  }
  const suffix = query.toString()
  const timeoutMs = params?.mode === 'full_market' ? 240_000 : 45_000
  return apiRequest<SignalsResponse>(`/api/signals${suffix ? `?${suffix}` : ''}`, {
    timeoutMs,
  })
}

export function crossValidateSignals(payload: CrossValidateRequest, options?: { signal?: AbortSignal }) {
  return apiRequest<CrossValidateResponse>('/api/signals/cross-validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 0,
    signal: options?.signal,
  })
}

export function startCrossValidateTask(payload: CrossValidateRequest) {
  return apiRequest<CrossValidateTaskStartResponse>('/api/signals/cross-validate/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 30_000,
  })
}

export function getCrossValidateTask(taskId: string) {
  return apiRequest<CrossValidateTaskStatusResponse>(`/api/signals/cross-validate/tasks/${taskId}`, {
    timeoutMs: 30_000,
  })
}

export function cancelCrossValidateTask(taskId: string) {
  return apiRequest<CrossValidateTaskStatusResponse>(`/api/signals/cross-validate/tasks/${taskId}/cancel`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function runCrossValidateBacktest(payload: CrossValidateBacktestRequest) {
  return apiRequest<CrossValidateBacktestResponse>('/api/signals/cross-validate/backtest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function saveCrossValidateHistory(payload: {
  response: CrossValidateResponse
  label: string
  strategy_ids: string[]
  strategy_names: string[]
  request_params: Record<string, unknown>
}) {
  return apiRequest<CrossValidateHistoryRecord>('/api/signals/cross-validate/history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function listCrossValidateHistory() {
  return apiRequest<CrossValidateHistoryRecord[]>('/api/signals/cross-validate/history')
}

export function getCrossValidateHistory(recordId: string) {
  return apiRequest<CrossValidateHistoryDetail>(`/api/signals/cross-validate/history/${recordId}`)
}

export function deleteCrossValidateHistory(recordId: string) {
  return apiRequest<{ ok: boolean }>(`/api/signals/cross-validate/history/${recordId}`, {
    method: 'DELETE',
  })
}

export function createSignalEtfBacktest(payload: SignalEtfBacktestCreateRequest) {
  return apiRequest<SignalEtfBacktestDetail>('/api/signals/etf-backtests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 90_000,
  })
}

export function createSignalEtfBacktestsAuto(payload: SignalEtfBacktestAutoCreateRequest) {
  return apiRequest<SignalEtfBacktestAutoCreateResponse>('/api/signals/etf-backtests/auto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 420_000,
  })
}

export function listSignalEtfBacktests(params?: { refresh?: boolean; holdingDays?: number }) {
  const query = new URLSearchParams()
  if (typeof params?.refresh === 'boolean') query.set('refresh', String(params.refresh))
  if (typeof params?.holdingDays === 'number' && Number.isFinite(params.holdingDays) && params.holdingDays > 0) {
    query.set('holding_days', String(Math.round(params.holdingDays)))
  }
  const suffix = query.toString()
  return apiRequest<SignalEtfBacktestListResponse>(`/api/signals/etf-backtests${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 90_000,
  })
}

export function getSignalEtfBacktest(recordId: string, params?: { refresh?: boolean; asOfDate?: string; holdingDays?: number }) {
  const query = new URLSearchParams()
  if (typeof params?.refresh === 'boolean') query.set('refresh', String(params.refresh))
  if (typeof params?.asOfDate === 'string' && params.asOfDate.trim()) query.set('as_of_date', params.asOfDate.trim())
  if (typeof params?.holdingDays === 'number' && Number.isFinite(params.holdingDays) && params.holdingDays > 0) {
    query.set('holding_days', String(Math.round(params.holdingDays)))
  }
  const suffix = query.toString()
  return apiRequest<SignalEtfBacktestDetail>(
    `/api/signals/etf-backtests/${encodeURIComponent(recordId)}${suffix ? `?${suffix}` : ''}`,
    {
      timeoutMs: 90_000,
    },
  )
}

export function updateSignalEtfBacktest(recordId: string, payload: SignalEtfBacktestUpdateRequest) {
  return apiRequest<SignalEtfBacktestRecord>(`/api/signals/etf-backtests/${encodeURIComponent(recordId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 45_000,
  })
}

export function deleteSignalEtfBacktest(recordId: string) {
  return apiRequest<SignalEtfBacktestDeleteResponse>(`/api/signals/etf-backtests/${encodeURIComponent(recordId)}`, {
    method: 'DELETE',
    timeoutMs: 30_000,
  })
}

export function postSimOrder(payload: {
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  signal_date: string
  submit_date: string
}) {
  return apiRequest<{ order: SimTradeOrder; fill?: SimTradeFill }>('/api/sim/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 45_000,
  })
}

export function getPortfolio() {
  return apiRequest<PortfolioSnapshot>('/api/sim/portfolio', {
    timeoutMs: 45_000,
  })
}

export function getSimOrders(params?: {
  status?: 'pending' | 'filled' | 'cancelled' | 'rejected'
  symbol?: string
  side?: 'buy' | 'sell'
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}) {
  const query = new URLSearchParams()
  if (params?.status) query.set('status', params.status)
  if (params?.symbol) query.set('symbol', params.symbol)
  if (params?.side) query.set('side', params.side)
  if (params?.date_from) query.set('date_from', params.date_from)
  if (params?.date_to) query.set('date_to', params.date_to)
  if (typeof params?.page === 'number') query.set('page', String(params.page))
  if (typeof params?.page_size === 'number') query.set('page_size', String(params.page_size))
  const suffix = query.toString()
  return apiRequest<SimOrdersResponse>(`/api/sim/orders${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 45_000,
  })
}

export function getSimFills(params?: {
  symbol?: string
  side?: 'buy' | 'sell'
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}) {
  const query = new URLSearchParams()
  if (params?.symbol) query.set('symbol', params.symbol)
  if (params?.side) query.set('side', params.side)
  if (params?.date_from) query.set('date_from', params.date_from)
  if (params?.date_to) query.set('date_to', params.date_to)
  if (typeof params?.page === 'number') query.set('page', String(params.page))
  if (typeof params?.page_size === 'number') query.set('page_size', String(params.page_size))
  const suffix = query.toString()
  return apiRequest<SimFillsResponse>(`/api/sim/fills${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 45_000,
  })
}

export function cancelSimOrder(orderId: string) {
  return apiRequest<{ order: SimTradeOrder; fill?: SimTradeFill }>(`/api/sim/orders/${orderId}/cancel`, {
    method: 'POST',
    timeoutMs: 45_000,
  })
}

export function settleSim() {
  return apiRequest<SimSettleResponse>('/api/sim/settle', {
    method: 'POST',
    timeoutMs: 60_000,
  })
}

export function resetSim() {
  return apiRequest<SimResetResponse>('/api/sim/reset', {
    method: 'POST',
    timeoutMs: 45_000,
  })
}

export function getSimConfig() {
  return apiRequest<SimTradingConfig>('/api/sim/config')
}

export function updateSimConfig(payload: SimTradingConfig) {
  return apiRequest<SimTradingConfig>('/api/sim/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getReviewStats(params?: {
  date_from?: string
  date_to?: string
  date_axis?: 'sell' | 'buy'
}) {
  const query = new URLSearchParams()
  if (params?.date_from) query.set('date_from', params.date_from)
  if (params?.date_to) query.set('date_to', params.date_to)
  if (params?.date_axis) query.set('date_axis', params.date_axis)
  const suffix = query.toString()
  return apiRequest<ReviewResponse>(`/api/review/stats${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 45_000,
  })
}

export function runBacktest(payload: BacktestRunRequest) {
  const timeoutMs = payload.mode === 'full_market' ? 240_000 : 60_000
  return apiRequest<BacktestResponse>('/api/backtest/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs,
  })
}

export function getStrategies() {
  return apiRequest<StrategyCatalogResponse>('/api/strategies', {
    timeoutMs: 45_000,
  })
}

export function getEventJudgmentProfiles() {
  return apiRequest<EventJudgmentCatalogResponse>('/api/event-judgment/profiles', {
    timeoutMs: 45_000,
  })
}

export function upsertEventJudgmentProfile(payload: EventJudgmentProfileUpsertRequest) {
  return apiRequest<EventJudgmentProfile>('/api/event-judgment/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 45_000,
  })
}

export function applyEventJudgmentProfile(payload: EventJudgmentProfileApplyRequest) {
  return apiRequest<EventJudgmentCatalogResponse>('/api/event-judgment/profiles/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 45_000,
  })
}

export function deleteEventJudgmentProfile(profileId: string) {
  return apiRequest<EventJudgmentProfileDeleteResponse>(`/api/event-judgment/profiles/${encodeURIComponent(profileId)}`, {
    method: 'DELETE',
    timeoutMs: 45_000,
  })
}

const BACKTEST_PLATEAU_LEGACY_POLL_INTERVAL_MS = 1_500
const BACKTEST_PLATEAU_LEGACY_MAX_WAIT_MS = 12 * 60 * 60 * 1000

function sleep(ms: number) {
  return new Promise<void>((resolve) => {
    globalThis.setTimeout(resolve, Math.max(0, Number(ms) || 0))
  })
}

async function waitBacktestPlateauTaskResult(taskId: string): Promise<BacktestPlateauResponse> {
  const deadline = Date.now() + BACKTEST_PLATEAU_LEGACY_MAX_WAIT_MS
  while (true) {
    const status = await getBacktestPlateauTask(taskId)
    if (status.status === 'succeeded') {
      if (status.result) return status.result
      throw new ApiError({
        code: 'BACKTEST_PLATEAU_TASK_EMPTY_RESULT',
        message: '收益平原任务已完成，但未返回结果。',
      })
    }
    if (status.status === 'failed') {
      throw new ApiError({
        code: status.error_code || 'BACKTEST_PLATEAU_TASK_FAILED',
        message: status.error || '收益平原任务失败',
      })
    }
    if (status.status === 'cancelled') {
      throw new ApiError({
        code: 'BACKTEST_PLATEAU_TASK_CANCELLED',
        message: '收益平原任务已停止',
      })
    }
    if (Date.now() >= deadline) {
      throw new ApiError({
        code: 'BACKTEST_PLATEAU_TASK_POLL_TIMEOUT',
        message: '收益平原任务等待超时，请在任务列表继续查看进度。',
      })
    }
    await sleep(BACKTEST_PLATEAU_LEGACY_POLL_INTERVAL_MS)
  }
}

export async function runBacktestPlateau(payload: BacktestPlateauRunRequest) {
  const started = await startBacktestPlateauTask(payload)
  return waitBacktestPlateauTaskResult(started.task_id)
}

export function startBacktestPlateauTask(payload: BacktestPlateauRunRequest) {
  return apiRequest<BacktestTaskStartResponse>('/api/backtest/plateau/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 60_000,
  })
}

export function listBacktestPlateauTasks(params?: { includeResult?: boolean }) {
  const query = new URLSearchParams()
  if (typeof params?.includeResult === 'boolean') query.set('include_result', String(params.includeResult))
  const suffix = query.toString()
  return apiRequest<BacktestPlateauTaskListResponse>(`/api/backtest/plateau/tasks${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 60_000,
  })
}

export function getBacktestPlateauTask(taskId: string) {
  return apiRequest<BacktestPlateauTaskStatusResponse>(`/api/backtest/plateau/tasks/${taskId}`, {
    timeoutMs: 60_000,
  })
}

export function pauseBacktestPlateauTask(taskId: string) {
  return apiRequest<BacktestPlateauTaskStatusResponse>(`/api/backtest/plateau/tasks/${taskId}/pause`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function resumeBacktestPlateauTask(taskId: string) {
  return apiRequest<BacktestPlateauTaskStatusResponse>(`/api/backtest/plateau/tasks/${taskId}/resume`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function cancelBacktestPlateauTask(taskId: string) {
  return apiRequest<BacktestPlateauTaskStatusResponse>(`/api/backtest/plateau/tasks/${taskId}/cancel`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function deleteBacktestPlateauTask(taskId: string) {
  return apiRequest<BacktestPlateauTaskDeleteResponse>(`/api/backtest/plateau/tasks/${taskId}`, {
    method: 'DELETE',
    timeoutMs: 30_000,
  })
}

export function startBacktestTask(payload: BacktestRunRequest) {
  return apiRequest<BacktestTaskStartResponse>('/api/backtest/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 60_000,
  })
}

export function listBacktestTasks(params?: { includeResult?: boolean }) {
  const query = new URLSearchParams()
  if (typeof params?.includeResult === 'boolean') query.set('include_result', String(params.includeResult))
  const suffix = query.toString()
  return apiRequest<BacktestTaskListResponse>(`/api/backtest/tasks${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 60_000,
  })
}

export function getBacktestTask(taskId: string) {
  return apiRequest<BacktestTaskStatusResponse>(`/api/backtest/tasks/${taskId}`, {
    timeoutMs: 60_000,
  })
}

export function getBacktestPlateauPointDetail(taskId: string, detailKey: string) {
  return apiRequest<BacktestPlateauPointDetailResponse>(`/api/backtest/plateau/tasks/${taskId}/points/${detailKey}`, {
    timeoutMs: 60_000,
  })
}

export function pauseBacktestTask(taskId: string) {
  return apiRequest<BacktestTaskStatusResponse>(`/api/backtest/tasks/${taskId}/pause`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function resumeBacktestTask(taskId: string) {
  return apiRequest<BacktestTaskStatusResponse>(`/api/backtest/tasks/${taskId}/resume`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function cancelBacktestTask(taskId: string) {
  return apiRequest<BacktestTaskStatusResponse>(`/api/backtest/tasks/${taskId}/cancel`, {
    method: 'POST',
    timeoutMs: 30_000,
  })
}

export function deleteBacktestTask(taskId: string) {
  return apiRequest<BacktestTaskDeleteResponse>(`/api/backtest/tasks/${taskId}`, {
    method: 'DELETE',
    timeoutMs: 30_000,
  })
}

export function buildBacktestReportPackage(payload: BacktestReportBuildRequest) {
  return apiRequest<BacktestReportBuildResponse>('/api/backtest/reports/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 120_000,
  })
}

export function importBacktestReportPackage(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  return apiRequest<BacktestReportImportResponse>('/api/backtest/reports/import', {
    method: 'POST',
    body: formData,
    timeoutMs: 120_000,
  })
}

export function listBacktestReports() {
  return apiRequest<BacktestReportListResponse>('/api/backtest/reports', {
    timeoutMs: 30_000,
  })
}

export function getBacktestReport(reportId: string) {
  return apiRequest<BacktestReportDetail>(`/api/backtest/reports/${reportId}`, {
    timeoutMs: 45_000,
  })
}

export function deleteBacktestReport(reportId: string) {
  return apiRequest<BacktestReportDeleteResponse>(`/api/backtest/reports/${reportId}`, {
    method: 'DELETE',
    timeoutMs: 30_000,
  })
}

export function getBacktestStrategySignals(symbol: string) {
  return apiRequest<BacktestStrategySignalsResponse>(
    `/api/backtest/strategy-signals/${encodeURIComponent(symbol)}`,
    { timeoutMs: 30_000 },
  )
}

export function scanStockStrategies(symbol: string, params?: { window_days?: number; scan_days?: number }) {
  const query = new URLSearchParams()
  if (params?.window_days) query.set('window_days', String(params.window_days))
  if (params?.scan_days) query.set('scan_days', String(params.scan_days))
  const qs = query.toString()
  return apiRequest<BacktestStrategySignalsResponse>(
    `/api/backtest/scan-stock-strategies/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ''}`,
    { timeoutMs: 60_000 },
  )
}

export function getDailyReviews(params?: { date_from?: string; date_to?: string }) {
  const query = new URLSearchParams()
  if (params?.date_from) query.set('date_from', params.date_from)
  if (params?.date_to) query.set('date_to', params.date_to)
  const suffix = query.toString()
  return apiRequest<DailyReviewListResponse>(`/api/review/daily${suffix ? `?${suffix}` : ''}`)
}

export function getDailyReview(date: string) {
  return apiRequest<DailyReviewRecord>(`/api/review/daily/${date}`)
}

export function upsertDailyReview(date: string, payload: DailyReviewPayload) {
  return apiRequest<DailyReviewRecord>(`/api/review/daily/${date}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteDailyReview(date: string) {
  return apiRequest<{ deleted: boolean }>(`/api/review/daily/${date}`, {
    method: 'DELETE',
  })
}

export function getWeeklyReviews(params?: { year?: number }) {
  const query = new URLSearchParams()
  if (typeof params?.year === 'number') query.set('year', String(params.year))
  const suffix = query.toString()
  return apiRequest<WeeklyReviewListResponse>(`/api/review/weekly${suffix ? `?${suffix}` : ''}`)
}

export function getWeeklyReview(weekLabel: string) {
  return apiRequest<WeeklyReviewRecord>(`/api/review/weekly/${weekLabel}`)
}

export function upsertWeeklyReview(weekLabel: string, payload: WeeklyReviewPayload) {
  return apiRequest<WeeklyReviewRecord>(`/api/review/weekly/${weekLabel}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteWeeklyReview(weekLabel: string) {
  return apiRequest<{ deleted: boolean }>(`/api/review/weekly/${weekLabel}`, {
    method: 'DELETE',
  })
}

export function getReviewTags() {
  return apiRequest<ReviewTagsPayload>('/api/review/tags')
}

export function createReviewTag(tagType: ReviewTagType, payload: ReviewTagCreateRequest) {
  return apiRequest<ReviewTag>(`/api/review/tags/${tagType}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteReviewTag(tagType: ReviewTagType, tagId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/review/tags/${tagType}/${tagId}`, {
    method: 'DELETE',
  })
}

export function getReviewFillTags() {
  return apiRequest<TradeFillTagAssignment[]>('/api/review/fill-tags')
}

export function getReviewFillTag(orderId: string) {
  return apiRequest<TradeFillTagAssignment>(`/api/review/fill-tags/${orderId}`)
}

export function updateReviewFillTag(orderId: string, payload: TradeFillTagUpdateRequest) {
  return apiRequest<TradeFillTagAssignment>(`/api/review/fill-tags/${orderId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getReviewTagStats(params?: { date_from?: string; date_to?: string }) {
  const query = new URLSearchParams()
  if (params?.date_from) query.set('date_from', params.date_from)
  if (params?.date_to) query.set('date_to', params.date_to)
  const suffix = query.toString()
  return apiRequest<ReviewTagStatsResponse>(`/api/review/tag-stats${suffix ? `?${suffix}` : ''}`)
}

export function getMarketNews(params?: {
  query?: string
  symbol?: string
  source_domains?: string[]
  age_hours?: 24 | 48 | 72
  refresh?: boolean
  limit?: number
}) {
  const query = new URLSearchParams()
  if (params?.query) query.set('query', params.query)
  if (params?.symbol) query.set('symbol', params.symbol)
  if (params?.source_domains && params.source_domains.length > 0) {
    query.set('source_domains', params.source_domains.join(','))
  }
  if (typeof params?.age_hours === 'number') query.set('age_hours', String(params.age_hours))
  if (params?.refresh) query.set('refresh', 'true')
  if (typeof params?.limit === 'number') query.set('limit', String(params.limit))
  const suffix = query.toString()
  return apiRequest<MarketNewsResponse>(`/api/market/news${suffix ? `?${suffix}` : ''}`, {
    timeoutMs: 20_000,
  })
}

export function getAIRecords() {
  return apiRequest<{ items: AIAnalysisRecord[] }>('/api/ai/records')
}

export function analyzeStockWithAI(symbol: string) {
  return apiRequest<AIAnalysisRecord>(`/api/stocks/${symbol}/ai-analyze`, {
    method: 'POST',
    timeoutMs: 45_000,
  })
}

export function deleteAIRecord(symbol: string, fetchedAt: string, provider?: string) {
  const params = new URLSearchParams({ symbol, fetched_at: fetchedAt })
  if (provider) {
    params.set('provider', provider)
  }
  return apiRequest<DeleteAIRecordResponse>(`/api/ai/records?${params.toString()}`, {
    method: 'DELETE',
  })
}

export function testAIProvider(payload: AIProviderTestRequest) {
  return apiRequest<AIProviderTestResponse>('/api/ai/providers/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 20_000,
  })
}

export function postAIChat(payload: AIChatRequest, signal?: AbortSignal) {
  return apiRequest<AIChatResponse>('/api/ai/chat', {
    signal,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 60_000,
  })
}

const STREAM_TIMEOUT_MS = 120_000

export async function streamAIChat(
  payload: AIChatRequest,
  handlers: {
    onDelta: (content: string) => void
    onDone: (result: { session_id: string; message: AIChatMessage; usage?: AIChatUsage | null; citations?: string[] }) => void
    onError?: (message: string) => void
  },
  signal?: AbortSignal,
) {
  const controller = new AbortController()
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  let timedOut = false
  const onAbort = () => {
    controller.abort()
    void reader?.cancel().catch(() => {})
  }
  signal?.addEventListener('abort', onAbort, { once: true })
  if (signal?.aborted) onAbort()
  const timeoutId = setTimeout(() => { timedOut = true; onAbort() }, STREAM_TIMEOUT_MS)
  const acceptEvent = (part: string) => {
    if (controller.signal.aborted) return false
    const data = part.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n')
    if (!data) return false
    let event: { type?: string; content?: string; session_id?: string; message?: AIChatMessage; usage?: AIChatUsage | null; citations?: string[] }
    try { event = JSON.parse(data) } catch { return false }
    if (event.type === 'delta' && event.content) handlers.onDelta(event.content)
    if (event.type === 'done' && event.session_id && event.message) {
      handlers.onDone({ session_id: event.session_id, message: event.message, usage: event.usage, citations: event.citations })
      return true
    }
    return false
  }
  try {
    const response = await fetch(buildApiUrl('/api/ai/chat'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, stream: true }), signal: controller.signal,
    })
    if (!response.ok) {
      let message = response.statusText || '请求失败'
      try { const body = await response.json(); message = body.message || message } catch { /* use status */ }
      throw new Error(message)
    }
    if (!(response.headers.get('content-type') ?? '').includes('text/event-stream')) {
      const body = await response.json() as AIChatResponse
      if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
      if (!body.session_id || !body.message) throw new Error('流式响应格式异常')
      handlers.onDone(body)
      return
    }
    reader = response.body?.getReader()
    if (!reader) throw new Error('无法读取流式响应')
    const decoder = new TextDecoder()
    let buffer = ''
    let finished = false
    while (!finished) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) { if (acceptEvent(part)) { finished = true; break } }
    }
    if (!finished) finished = acceptEvent(buffer + decoder.decode())
    if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
    if (!finished) handlers.onError?.('流式响应未完成')
  } catch (error) {
    if (signal?.aborted) throw error
    const message = timedOut ? '流式请求超时' : error instanceof Error ? error.message : '无法连接后端'
    handlers.onError?.(message)
    throw new Error(message)
  } finally {
    clearTimeout(timeoutId)
    signal?.removeEventListener('abort', onAbort)
    if (reader) { await reader.cancel().catch(() => {}); reader.releaseLock() }
  }
}

export function getAISessions(limit = 20) {
  return apiRequest<AISessionsListResponse>(`/api/ai/sessions?limit=${limit}`)
}

export function getAIUsage() {
  return apiRequest<AIUsageResponse>('/api/ai/usage')
}

export function postAIParameterProposal(message: string, context: AIChatContext) {
  return apiRequest<AIParameterProposalResponse>('/api/ai/parameter-proposals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, context }),
    timeoutMs: 60_000,
  })
}

export function applyAIParameterProposal(
  proposalId: string,
  changeIds: string[],
  confirmHighRisk = false,
) {
  return apiRequest<AIParameterProposalApplyResponse>(
    `/api/ai/parameter-proposals/${proposalId}/apply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ change_ids: changeIds, confirm_high_risk: confirmHighRisk }),
      timeoutMs: 30_000,
    },
  )
}

export function getAIPlaybook(params?: { scope?: string; strategy_id?: string }) {
  const query = new URLSearchParams()
  if (params?.scope) query.set('scope', params.scope)
  if (params?.strategy_id) query.set('strategy_id', params.strategy_id)
  const suffix = query.toString()
  return apiRequest<AIPlaybookResponse>(`/api/ai/playbook${suffix ? `?${suffix}` : ''}`)
}

export function getAILocalPlaybook() {
  return apiRequest<AILocalPlaybookResponse>('/api/ai/playbook/local')
}

export function putAILocalPlaybook(principles: string[]) {
  return apiRequest<AILocalPlaybookResponse>('/api/ai/playbook/local', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ principles }),
  })
}

export function getAIQuickPrompts() {
  return apiRequest<AIQuickPromptsResponse>('/api/ai/quick-prompts')
}

export function putAIQuickPrompts(templates: AIQuickPromptTemplate[]) {
  return apiRequest<AIQuickPromptsResponse>('/api/ai/quick-prompts', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ templates }),
  })
}

export function getConfig() {
  return apiRequest<AppConfig>('/api/config')
}

export function updateConfig(payload: AppConfig) {
  return apiRequest<AppConfig>('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getSystemStorage() {
  return apiRequest<SystemStorageStatus>('/api/system/storage')
}

export function getWyckoffEventStoreStats() {
  return apiRequest<WyckoffEventStoreStatsResponse>('/api/system/wyckoff-event-store/stats')
}

export function backfillWyckoffEventStore(payload: WyckoffEventStoreBackfillRequest) {
  return apiRequest<WyckoffEventStoreBackfillResponse>('/api/system/wyckoff-event-store/backfill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 180_000,
  })
}

export function syncMarketData(payload: MarketDataSyncRequest) {
  return apiRequest<MarketDataSyncResponse>('/api/system/sync-market-data', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeoutMs: 180_000,
  })
}
