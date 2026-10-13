import { delay, http, HttpResponse } from 'msw'
import {
  analyzeStockWithAI,
  cancelOrder,
  createReviewTagStore,
  createOrder,
  createScreenerRun,
  deleteDailyReviewStore,
  deleteReviewTagStore,
  deleteWeeklyReviewStore,
  deleteAIRecord,
  getDailyReviewStore,
  getDailyReviewsStore,
  getFills,
  getAIRecords,
  getAnalysis,
  getMarketNewsStore,
  getCandlePayload,
  getIntradayPayload,
  getConfigStore,
  getOrders,
  getPortfolio,
  getReview,
  getReviewFillTagStore,
  getReviewFillTagsStore,
  getReviewTagStatsStore,
  getReviewTagsStore,
  getSimConfigStore,
  getLatestScreenerRunStore,
  getScreenerRun,
  getSignals,
  getStrategiesStore,
  getEventJudgmentCatalogStore,
  getSystemStorageStore,
  getWeeklyReviewStore,
  getWeeklyReviewsStore,
  resetAccount,
  saveAnnotation,
  setSimConfigStore,
  syncMarketDataStore,
  settleOrders,
  setConfigStore,
  testAIProvider,
  runBacktestStore,
  runBacktestPlateauStore,
  updateReviewFillTagStore,
  upsertDailyReviewStore,
  upsertWeeklyReviewStore,
  upsertEventJudgmentProfileStore,
  applyEventJudgmentProfileStore,
  deleteEventJudgmentProfileStore,
} from '@/mocks/data'
import type {
  AIProviderTestRequest,
  AppConfig,
  BacktestPlateauPointDetailResponse,
  BacktestReportDetail,
  BacktestReportSummary,
  BacktestPlateauResponse,
  BacktestPlateauRunRequest,
  BacktestRunRequest,
  BacktestResponse,
  BoardFilter,
  Market,
  DailyReviewPayload,
  MarketDataSyncRequest,
  ReviewTagCreateRequest,
  ReviewTagType,
  ScreenerParams,
  StockAnnotation,
  TradeFillTagUpdateRequest,
  EventJudgmentProfileUpsertRequest,
  EventJudgmentProfileApplyRequest,
  WeeklyReviewPayload,
} from '@/types/contracts'

type MockBacktestTask = {
  status: 'running' | 'succeeded'
  poll_count: number
  payload: BacktestRunRequest
  result: BacktestResponse
  updated_at: string
}
type MockBacktestPlateauTask = {
  status: 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  poll_count: number
  payload: BacktestPlateauRunRequest
  point_details: Record<string, BacktestPlateauPointDetailResponse>
  result: BacktestPlateauResponse
  sampling_mode: 'grid' | 'lhs'
  updated_at: string
}

type MockBacktestReport = {
  summary: BacktestReportSummary
  detail: BacktestReportDetail
}

const mockBacktestTaskStore = new Map<string, MockBacktestTask>()
const mockBacktestPlateauTaskStore = new Map<string, MockBacktestPlateauTask>()
const mockBacktestReportStore = new Map<string, MockBacktestReport>()

function buildMockPlateauRunRequest(basePayload: BacktestRunRequest, params: BacktestPlateauResponse['points'][number]['params']): BacktestRunRequest {
  return {
    ...basePayload,
    window_days: Number(params.window_days),
    min_score: Number(params.min_score),
    stop_loss: Number(params.stop_loss),
    take_profit: Number(params.take_profit),
    trailing_stop_pct: Number(params.trailing_stop_pct),
    max_positions: Number(params.max_positions),
    position_pct: Number(params.position_pct),
    max_symbols: Number(params.max_symbols),
    priority_topk_per_day: Number(params.priority_topk_per_day),
    enable_advanced_analysis: false,
  }
}

export const handlers = [
  http.get('/api/backtest/strategy-signals/:symbol', ({ params }) => HttpResponse.json({ symbol: String(params.symbol), strategies: [], signals: [] })),
  http.get('/api/backtest/scan-stock-strategies/:symbol', ({ params }) => HttpResponse.json({ symbol: String(params.symbol), strategies: [], signals: [] })),
  http.post('/api/screener/run', async ({ request }) => {
    const params = (await request.json()) as ScreenerParams
    await delay(420)
    const detail = createScreenerRun(params)
    return HttpResponse.json({ run_id: detail.run_id })
  }),

  http.get('/api/screener/runs/:runId', async ({ params }) => {
    await delay(260)
    const run = getScreenerRun(String(params.runId))
    if (!run) {
      return HttpResponse.json(
        {
          code: 'RUN_NOT_FOUND',
          message: '绛涢€変换鍔′笉瀛樺湪',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(run)
  }),

  http.get('/api/screener/latest-run', async () => {
    await delay(220)
    const run = getLatestScreenerRunStore()
    return HttpResponse.json(run)
  }),

  http.get('/api/stocks/:symbol/candles', async ({ params }) => {
    await delay(180)
    return HttpResponse.json(getCandlePayload(String(params.symbol)))
  }),

  http.get('/api/stocks/:symbol/intraday', async ({ params, request }) => {
    await delay(150)
    const date = new URL(request.url).searchParams.get('date') ?? ''
    return HttpResponse.json(getIntradayPayload(String(params.symbol), date))
  }),

  http.get('/api/stocks/:symbol/analysis', async ({ params }) => {
    await delay(160)
    return HttpResponse.json(getAnalysis(String(params.symbol)))
  }),

  http.post('/api/stocks/:symbol/ai-analyze', async ({ params }) => {
    await delay(420)
    return HttpResponse.json(analyzeStockWithAI(String(params.symbol)))
  }),

  http.put('/api/stocks/:symbol/annotations', async ({ request, params }) => {
    const body = (await request.json()) as StockAnnotation
    if (body.symbol !== params.symbol) {
      return HttpResponse.json(
        {
          code: 'SYMBOL_MISMATCH',
          message: 'symbol 与 URL 不一致',
          trace_id: `${Date.now()}`,
        },
        { status: 400 },
      )
    }
    const saved = saveAnnotation(body)
    return HttpResponse.json({
      success: true,
      annotation: saved,
    })
  }),

  http.get('/api/signals', async ({ request }) => {
    await delay(120)
    const url = new URL(request.url)
    const mode = (url.searchParams.get('mode') ?? 'trend_pool') as 'trend_pool' | 'full_market'
    const marketFilters = Array.from(
      new Set(
        url.searchParams
          .getAll('market_filters')
          .flatMap((item) => item.split(','))
          .map((item) => item.trim())
          .filter((item): item is Market => item === 'sh' || item === 'sz' || item === 'bj'),
      ),
    )
    const boardFilters = Array.from(
      new Set(
        url.searchParams
          .getAll('board_filters')
          .flatMap((item) => item.split(','))
          .map((item) => item.trim())
          .filter(
            (item): item is BoardFilter =>
              item === 'main' || item === 'gem' || item === 'star' || item === 'beijing' || item === 'st',
          ),
      ),
    )
    const refresh = url.searchParams.get('refresh') === 'true'
    const asOfDateRaw = (url.searchParams.get('as_of_date') ?? '').trim()
    const asOfDate = /^\d{4}-\d{2}-\d{2}$/.test(asOfDateRaw) ? asOfDateRaw : undefined
    const windowDays = Number(url.searchParams.get('window_days') ?? 60)
    const minScore = Number(url.searchParams.get('min_score') ?? 60)
    const requireSequence = url.searchParams.get('require_sequence') === 'true'
    const minEventCount = Number(url.searchParams.get('min_event_count') ?? 1)
    const signalAgeMin = Number(url.searchParams.get('signal_age_min') ?? 0)
    const signalAgeMax = Number(url.searchParams.get('signal_age_max') ?? '')
    return HttpResponse.json(
      getSignals({
        mode,
        market_filters: marketFilters,
        board_filters: boardFilters,
        as_of_date: asOfDate,
        window_days: Number.isFinite(windowDays) ? windowDays : 60,
        min_score: Number.isFinite(minScore) ? minScore : 60,
        require_sequence: requireSequence,
        min_event_count: Number.isFinite(minEventCount) ? minEventCount : 1,
        signal_age_min: Number.isFinite(signalAgeMin) ? signalAgeMin : 0,
        signal_age_max: Number.isFinite(signalAgeMax) ? signalAgeMax : undefined,
      }),
      {
        headers: {
          'x-mock-refresh': String(refresh),
        },
      },
    )
  }),

  http.get('/api/strategies', async () => {
    await delay(80)
    return HttpResponse.json(getStrategiesStore())
  }),

  http.get('/api/event-judgment/profiles', async () => {
    await delay(80)
    return HttpResponse.json(getEventJudgmentCatalogStore())
  }),

  http.post('/api/event-judgment/profiles', async ({ request }) => {
    await delay(100)
    const payload = (await request.json()) as EventJudgmentProfileUpsertRequest
    return HttpResponse.json(upsertEventJudgmentProfileStore(payload))
  }),

  http.post('/api/event-judgment/profiles/apply', async ({ request }) => {
    await delay(80)
    const payload = (await request.json()) as EventJudgmentProfileApplyRequest
    return HttpResponse.json(applyEventJudgmentProfileStore(payload))
  }),

  http.delete('/api/event-judgment/profiles/:profileId', async ({ params }) => {
    await delay(60)
    return HttpResponse.json(deleteEventJudgmentProfileStore(String(params.profileId || '')))
  }),

  http.post('/api/backtest/run', async ({ request }) => {
    await delay(220)
    const payload = (await request.json()) as BacktestRunRequest
    return HttpResponse.json(runBacktestStore(payload))
  }),

  http.post('/api/backtest/plateau', async ({ request }) => {
    await delay(280)
    const payload = (await request.json()) as BacktestPlateauRunRequest
    return HttpResponse.json(runBacktestPlateauStore(payload))
  }),

  http.get('/api/backtest/plateau/tasks', async () => {
    await delay(80)
    const items = Array.from(mockBacktestPlateauTaskStore.entries())
      .sort((left, right) => right[1].updated_at.localeCompare(left[1].updated_at))
      .map(([taskId, task]) => {
        const total = Number(task.result.total_combinations || 0)
        const processed = task.status === 'succeeded' ? total : Math.max(0, Math.floor(total * 0.5))
        const percent = total > 0 ? (processed / total) * 100 : 0
        return {
          task_id: taskId,
          status: task.status,
          progress: {
            sampling_mode: task.sampling_mode,
            processed_points: processed,
            total_points: total,
            percent,
            message: task.status === 'succeeded' ? '收益平原评估完成。' : '收益平原任务执行中...',
            started_at: task.updated_at,
            updated_at: task.updated_at,
          },
          result: task.status === 'succeeded' ? task.result : null,
          error: null,
          error_code: null,
        }
    })
    return HttpResponse.json({ items })
  }),

  http.post('/api/backtest/plateau/tasks', async ({ request }) => {
    await delay(120)
    const payload = (await request.json()) as BacktestPlateauRunRequest
    const taskId = `bp_mock_${Date.now()}_${Math.floor(Math.random() * 10000)}`
    const result = runBacktestPlateauStore(payload)
    const pointDetails: Record<string, BacktestPlateauPointDetailResponse> = {}
    const points = result.points.map((point, index) => {
      if (point.error) return point
      const detailKey = `pt_mock_${index + 1}`
      const runRequest = buildMockPlateauRunRequest(payload.base_payload, point.params)
      pointDetails[detailKey] = {
        task_id: taskId,
        detail_key: detailKey,
        saved_at: new Date().toISOString(),
        params: point.params,
        run_request: runRequest,
        run_result: runBacktestStore(runRequest),
      }
      return {
        ...point,
        detail_key: detailKey,
      }
    })
    mockBacktestPlateauTaskStore.set(taskId, {
      status: 'running',
      poll_count: 0,
      payload,
      point_details: pointDetails,
      result: {
        ...result,
        points,
        best_point: points.find((point) => !point.error) ?? result.best_point ?? null,
      },
      sampling_mode: payload.sampling_mode ?? 'lhs',
      updated_at: new Date().toISOString(),
    })
    return HttpResponse.json({ task_id: taskId })
  }),

  http.get('/api/backtest/plateau/tasks/:taskId', async ({ params }) => {
    await delay(120)
    const taskId = String(params.taskId)
    const task = mockBacktestPlateauTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json(
        {
          code: 'BACKTEST_PLATEAU_TASK_NOT_FOUND',
          message: '收益平原任务不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    task.poll_count += 1
    if (task.status === 'running' && task.poll_count >= 2) {
      task.status = 'succeeded'
      task.updated_at = new Date().toISOString()
    }
    const total = Number(task.result.total_combinations || 0)
    const processed = task.status === 'succeeded' ? total : Math.max(0, Math.floor(total * 0.5))
    const percent = total > 0 ? (processed / total) * 100 : 0
    return HttpResponse.json({
      task_id: taskId,
      status: task.status,
      progress: {
        sampling_mode: task.sampling_mode,
        processed_points: processed,
        total_points: total,
        percent,
        message: task.status === 'succeeded' ? '收益平原评估完成。' : '收益平原任务执行中...',
        started_at: task.updated_at,
        updated_at: task.updated_at,
      },
      result: task.status === 'succeeded' ? task.result : null,
      error: null,
      error_code: null,
    })
  }),

  http.get('/api/backtest/plateau/tasks/:taskId/points/:detailKey', async ({ params }) => {
    await delay(80)
    const taskId = String(params.taskId)
    const detailKey = String(params.detailKey)
    const task = mockBacktestPlateauTaskStore.get(taskId)
    const detail = task?.point_details?.[detailKey]
    if (!detail) {
      return HttpResponse.json(
        {
          code: 'BACKTEST_PLATEAU_POINT_DETAIL_NOT_FOUND',
          message: '收益平原参数组结果不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(detail)
  }),

  http.post('/api/backtest/plateau/tasks/:taskId/pause', async ({ params }) => {
    await delay(80)
    const taskId = String(params.taskId)
    const task = mockBacktestPlateauTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json({ code: 'BACKTEST_PLATEAU_TASK_NOT_FOUND', message: '收益平原任务不存在' }, { status: 404 })
    }
    task.status = 'paused'
    task.updated_at = new Date().toISOString()
    return HttpResponse.json({
      task_id: taskId,
      status: 'paused',
      progress: {
        sampling_mode: task.sampling_mode,
        processed_points: Math.max(0, Math.floor(task.result.total_combinations * 0.5)),
        total_points: task.result.total_combinations,
        percent: 50,
        message: '任务已暂停。',
        started_at: task.updated_at,
        updated_at: task.updated_at,
      },
      result: null,
      error: null,
      error_code: null,
    })
  }),

  http.post('/api/backtest/plateau/tasks/:taskId/resume', async ({ params }) => {
    await delay(80)
    const taskId = String(params.taskId)
    const task = mockBacktestPlateauTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json({ code: 'BACKTEST_PLATEAU_TASK_NOT_FOUND', message: '收益平原任务不存在' }, { status: 404 })
    }
    task.status = 'running'
    task.updated_at = new Date().toISOString()
    return HttpResponse.json({
      task_id: taskId,
      status: 'running',
      progress: {
        sampling_mode: task.sampling_mode,
        processed_points: Math.max(0, Math.floor(task.result.total_combinations * 0.5)),
        total_points: task.result.total_combinations,
        percent: 50,
        message: '任务已继续。',
        started_at: task.updated_at,
        updated_at: task.updated_at,
      },
      result: null,
      error: null,
      error_code: null,
    })
  }),

  http.post('/api/backtest/plateau/tasks/:taskId/cancel', async ({ params }) => {
    await delay(80)
    const taskId = String(params.taskId)
    const task = mockBacktestPlateauTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json({ code: 'BACKTEST_PLATEAU_TASK_NOT_FOUND', message: '收益平原任务不存在' }, { status: 404 })
    }
    task.status = 'cancelled'
    task.updated_at = new Date().toISOString()
    return HttpResponse.json({
      task_id: taskId,
      status: 'cancelled',
      progress: {
        sampling_mode: task.sampling_mode,
        processed_points: Math.max(0, Math.floor(task.result.total_combinations * 0.5)),
        total_points: task.result.total_combinations,
        percent: 50,
        message: '任务已停止。',
        started_at: task.updated_at,
        updated_at: task.updated_at,
      },
      result: null,
      error: null,
      error_code: null,
    })
  }),

  http.delete('/api/backtest/plateau/tasks/:taskId', async ({ params }) => {
    await delay(80)
    const taskId = String(params.taskId)
    const task = mockBacktestPlateauTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json({ code: 'BACKTEST_PLATEAU_TASK_NOT_FOUND', message: '收益平原任务不存在' }, { status: 404 })
    }
    if (task.status === 'pending' || task.status === 'running') {
      return HttpResponse.json(
        { code: 'BACKTEST_PLATEAU_TASK_CONTROL_INVALID', message: `任务当前状态为 ${task.status}，请先暂停或停止后再删除。` },
        { status: 400 },
      )
    }
    mockBacktestPlateauTaskStore.delete(taskId)
    return HttpResponse.json({ deleted: true, task_id: taskId })
  }),

  http.post('/api/backtest/tasks', async ({ request }) => {
    await delay(120)
    const payload = (await request.json()) as BacktestRunRequest
    const taskId = `bt_mock_${Date.now()}_${Math.floor(Math.random() * 10000)}`
    mockBacktestTaskStore.set(taskId, {
      status: 'running',
      poll_count: 0,
      payload,
      result: runBacktestStore(payload),
      updated_at: new Date().toISOString(),
    })
    return HttpResponse.json({ task_id: taskId })
  }),

  http.get('/api/backtest/tasks', async () => {
    await delay(80)
    const items = Array.from(mockBacktestTaskStore.entries())
      .sort((left, right) => right[1].updated_at.localeCompare(left[1].updated_at))
      .map(([taskId, task]) => ({
        task_id: taskId,
        status: task.status,
        progress: {
          mode: task.payload.pool_roll_mode,
          current_date: task.status === 'succeeded' ? task.result.range.date_to : task.result.range.date_from,
          processed_dates: task.status === 'succeeded' ? 2 : 1,
          total_dates: 2,
          percent: task.status === 'succeeded' ? 100 : 50,
          message: task.status === 'succeeded' ? '回测完成。' : '滚动筛选进度 1/2',
          warning: null,
          stage_timings: [],
          started_at: task.updated_at,
          updated_at: task.updated_at,
        },
        result: task.status === 'succeeded' ? task.result : null,
        error: null,
        error_code: null,
      }))
    return HttpResponse.json({ items })
  }),

  http.delete('/api/backtest/tasks/:taskId', async ({ params }) => {
    await delay(80)
    const taskId = String(params.taskId)
    const task = mockBacktestTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json({ code: 'BACKTEST_TASK_NOT_FOUND', message: '回测任务不存在' }, { status: 404 })
    }
    if (task.status === 'running') {
      return HttpResponse.json(
        { code: 'BACKTEST_TASK_CONTROL_INVALID', message: `任务当前状态为 ${task.status}，请先暂停或停止后再删除。` },
        { status: 400 },
      )
    }
    mockBacktestTaskStore.delete(taskId)
    return HttpResponse.json({ deleted: true, task_id: taskId })
  }),

  http.get('/api/backtest/tasks/:taskId', async ({ params }) => {
    await delay(120)
    const taskId = String(params.taskId)
    const task = mockBacktestTaskStore.get(taskId)
    if (!task) {
      return HttpResponse.json(
        {
          code: 'BACKTEST_TASK_NOT_FOUND',
          message: '回测任务不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    task.poll_count += 1
    if (task.status === 'running' && task.poll_count >= 2) {
      task.status = 'succeeded'
    }
    if (task.status === 'running') {
      return HttpResponse.json({
        task_id: taskId,
        status: 'running',
        progress: {
          mode: task.payload.pool_roll_mode,
          current_date: task.result.range.date_from,
          processed_dates: 1,
          total_dates: 2,
          percent: 50,
          message: '滚动筛选进度 1/2',
          warning: null,
          stage_timings: [],
          started_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      })
    }
    return HttpResponse.json({
      task_id: taskId,
      status: 'succeeded',
      progress: {
        mode: task.payload.pool_roll_mode,
        current_date: task.result.range.date_to,
        processed_dates: 2,
        total_dates: 2,
        percent: 100,
        message: '回测完成。',
        warning: null,
        stage_timings: [],
        started_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      result: task.result,
      error: null,
    })
  }),

  http.post('/api/backtest/reports/build', async ({ request }) => {
    await delay(80)
    const payload = (await request.json()) as {
      report_id?: string
      app_name?: string
      app_version?: string
    }
    const reportId = String(payload.report_id || `bt_mock_report_${Date.now()}`)
    const createdAt = new Date().toISOString()
    return HttpResponse.json({
      report_id: reportId,
      file_name: `${reportId}.ftbt`,
      file_base64: 'bW9jay1mdGJ0',
      manifest: {
        schema_version: 'ftbt-1.0',
        package_type: 'backtest_report',
        created_at: createdAt,
        report_id: reportId,
        app: {
          name: String(payload.app_name || 'Final Trade'),
          version: String(payload.app_version || 'mock'),
        },
        files: [],
      },
    })
  }),

  http.post('/api/backtest/reports/import', async ({ request }) => {
    await delay(100)
    const formData = await request.formData()
    const file = formData.get('file') as File | null
    const nowText = new Date().toISOString()
    const fileName = file?.name || 'mock-import.ftbt'
    const reportId = String(fileName).replace(/\.ftbt$/i, '') || `bt_mock_import_${Date.now()}`
    const summary: BacktestReportSummary = {
      report_id: reportId,
      created_at: nowText,
      first_imported_at: nowText,
      last_imported_at: nowText,
      source_file_name: fileName,
      package_size_bytes: Number(file?.size || 0),
      trade_count: 0,
      total_return: 0,
      max_drawdown: 0,
      win_rate: 0,
      date_from: '2025-01-01',
      date_to: '2025-01-31',
      has_plateau_result: false,
    }
    const detail: BacktestReportDetail = {
      summary,
      manifest: {
        schema_version: 'ftbt-1.0',
        package_type: 'backtest_report',
        created_at: nowText,
        report_id: reportId,
        app: {
          name: 'Final Trade',
          version: 'mock',
        },
        files: [],
      },
      run_request: {
        mode: 'full_market',
        trend_step: 'auto',
        pool_roll_mode: 'daily',
        date_from: '2025-01-01',
        date_to: '2025-01-31',
        window_days: 60,
        min_score: 55,
        require_sequence: false,
        min_event_count: 1,
        entry_events: ['Spring', 'SOS'],
        exit_events: ['SOW', 'UTAD'],
        initial_capital: 1_000_000,
        position_pct: 0.2,
        max_positions: 5,
        stop_loss: 0.05,
        take_profit: 0.15,
        max_hold_days: 60,
        fee_bps: 8,
        prioritize_signals: true,
        priority_mode: 'balanced',
        priority_topk_per_day: 0,
        enforce_t1: true,
        max_symbols: 120,
      },
      run_result: runBacktestStore({
        mode: 'full_market',
        trend_step: 'auto',
        date_from: '2025-01-01',
        date_to: '2025-01-31',
        window_days: 60,
        min_score: 55,
        require_sequence: false,
        min_event_count: 1,
        entry_events: ['Spring', 'SOS'],
        exit_events: ['SOW', 'UTAD'],
        initial_capital: 1_000_000,
        position_pct: 0.2,
        max_positions: 5,
        stop_loss: 0.05,
        take_profit: 0.15,
        max_hold_days: 60,
        fee_bps: 8,
        prioritize_signals: true,
        priority_mode: 'balanced',
        priority_topk_per_day: 0,
        enforce_t1: true,
        max_symbols: 120,
        pool_roll_mode: 'daily',
      }),
      plateau_result: null,
    }
    mockBacktestReportStore.set(reportId, { summary, detail })
    return HttpResponse.json({ summary })
  }),

  http.get('/api/backtest/reports', async () => {
    await delay(50)
    return HttpResponse.json({
      items: Array.from(mockBacktestReportStore.values()).map((item) => item.summary),
    })
  }),

  http.get('/api/backtest/reports/:reportId', async ({ params }) => {
    await delay(60)
    const reportId = String(params.reportId || '')
    const report = mockBacktestReportStore.get(reportId)
    if (!report) {
      return HttpResponse.json(
        {
          code: 'BACKTEST_REPORT_NOT_FOUND',
          message: '回测报告不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(report.detail)
  }),

  http.delete('/api/backtest/reports/:reportId', async ({ params }) => {
    await delay(60)
    const reportId = String(params.reportId || '')
    const exists = mockBacktestReportStore.has(reportId)
    if (!exists) {
      return HttpResponse.json(
        {
          code: 'BACKTEST_REPORT_NOT_FOUND',
          message: '回测报告不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    mockBacktestReportStore.delete(reportId)
    return HttpResponse.json({ deleted: true, report_id: reportId })
  }),

  http.post('/api/sim/orders', async ({ request }) => {
    await delay(160)
    const payload = (await request.json()) as {
      symbol: string
      side: 'buy' | 'sell'
      quantity: number
      signal_date: string
      submit_date: string
    }
    return HttpResponse.json(createOrder(payload))
  }),

  http.get('/api/sim/orders', async ({ request }) => {
    await delay(120)
    const url = new URL(request.url)
    const page = Number(url.searchParams.get('page') ?? 1)
    const pageSize = Number(url.searchParams.get('page_size') ?? 50)
    return HttpResponse.json(
      getOrders({
        status: (url.searchParams.get('status') ?? undefined) as
          | 'pending'
          | 'filled'
          | 'cancelled'
          | 'rejected'
          | undefined,
        symbol: url.searchParams.get('symbol') ?? undefined,
        side: (url.searchParams.get('side') ?? undefined) as 'buy' | 'sell' | undefined,
        date_from: url.searchParams.get('date_from') ?? undefined,
        date_to: url.searchParams.get('date_to') ?? undefined,
        page: Number.isFinite(page) ? page : 1,
        page_size: Number.isFinite(pageSize) ? pageSize : 50,
      }),
    )
  }),

  http.get('/api/sim/fills', async ({ request }) => {
    await delay(120)
    const url = new URL(request.url)
    const page = Number(url.searchParams.get('page') ?? 1)
    const pageSize = Number(url.searchParams.get('page_size') ?? 50)
    return HttpResponse.json(
      getFills({
        symbol: url.searchParams.get('symbol') ?? undefined,
        side: (url.searchParams.get('side') ?? undefined) as 'buy' | 'sell' | undefined,
        date_from: url.searchParams.get('date_from') ?? undefined,
        date_to: url.searchParams.get('date_to') ?? undefined,
        page: Number.isFinite(page) ? page : 1,
        page_size: Number.isFinite(pageSize) ? pageSize : 50,
      }),
    )
  }),

  http.post('/api/sim/orders/:orderId/cancel', async ({ params }) => {
    await delay(120)
    const result = cancelOrder(String(params.orderId))
    if (!result) {
      return HttpResponse.json(
        {
          code: 'SIM_ORDER_NOT_FOUND',
          message: '订单不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(result)
  }),

  http.post('/api/sim/settle', async () => {
    await delay(120)
    return HttpResponse.json(settleOrders())
  }),

  http.post('/api/sim/reset', async () => {
    await delay(120)
    return HttpResponse.json(resetAccount())
  }),

  http.get('/api/sim/config', async () => {
    await delay(90)
    return HttpResponse.json(getSimConfigStore())
  }),

  http.put('/api/sim/config', async ({ request }) => {
    await delay(120)
    const payload = (await request.json()) as Parameters<typeof setSimConfigStore>[0]
    return HttpResponse.json(setSimConfigStore(payload))
  }),

  http.get('/api/sim/portfolio', async () => {
    await delay(100)
    return HttpResponse.json(getPortfolio())
  }),

  http.get('/api/review/stats', async ({ request }) => {
    await delay(100)
    const url = new URL(request.url)
    return HttpResponse.json(
      getReview({
        date_from: url.searchParams.get('date_from') ?? undefined,
        date_to: url.searchParams.get('date_to') ?? undefined,
        date_axis: (url.searchParams.get('date_axis') as 'sell' | 'buy' | null) ?? undefined,
      }),
    )
  }),

  http.get('/api/review/daily', async ({ request }) => {
    await delay(100)
    const url = new URL(request.url)
    return HttpResponse.json(
      getDailyReviewsStore({
        date_from: url.searchParams.get('date_from') ?? undefined,
        date_to: url.searchParams.get('date_to') ?? undefined,
      }),
    )
  }),

  http.get('/api/review/daily/:date', async ({ params }) => {
    await delay(80)
    const row = getDailyReviewStore(String(params.date))
    if (!row) {
      return HttpResponse.json(
        {
          code: 'REVIEW_DAILY_NOT_FOUND',
          message: '鏃ュ鐩樹笉瀛樺湪',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(row)
  }),

  http.put('/api/review/daily/:date', async ({ request, params }) => {
    await delay(120)
    const payload = (await request.json()) as DailyReviewPayload
    return HttpResponse.json(upsertDailyReviewStore(String(params.date), payload))
  }),

  http.delete('/api/review/daily/:date', async ({ params }) => {
    await delay(80)
    return HttpResponse.json(deleteDailyReviewStore(String(params.date)))
  }),

  http.get('/api/review/weekly', async ({ request }) => {
    await delay(100)
    const url = new URL(request.url)
    const yearRaw = url.searchParams.get('year')
    const year = yearRaw ? Number(yearRaw) : undefined
    return HttpResponse.json(getWeeklyReviewsStore({ year: Number.isFinite(year as number) ? year : undefined }))
  }),

  http.get('/api/review/weekly/:weekLabel', async ({ params }) => {
    await delay(80)
    const row = getWeeklyReviewStore(String(params.weekLabel))
    if (!row) {
      return HttpResponse.json(
        {
          code: 'REVIEW_WEEKLY_NOT_FOUND',
          message: '鍛ㄥ鐩樹笉瀛樺湪',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(row)
  }),

  http.put('/api/review/weekly/:weekLabel', async ({ request, params }) => {
    await delay(120)
    const payload = (await request.json()) as WeeklyReviewPayload
    return HttpResponse.json(upsertWeeklyReviewStore(String(params.weekLabel), payload))
  }),

  http.delete('/api/review/weekly/:weekLabel', async ({ params }) => {
    await delay(80)
    return HttpResponse.json(deleteWeeklyReviewStore(String(params.weekLabel)))
  }),

  http.get('/api/review/tags', async () => {
    await delay(80)
    return HttpResponse.json(getReviewTagsStore())
  }),

  http.post('/api/review/tags/:tagType', async ({ request, params }) => {
    await delay(100)
    const payload = (await request.json()) as ReviewTagCreateRequest
    const tagType = String(params.tagType) as ReviewTagType
    return HttpResponse.json(createReviewTagStore(tagType, payload))
  }),

  http.delete('/api/review/tags/:tagType/:tagId', async ({ params }) => {
    await delay(100)
    const tagType = String(params.tagType) as ReviewTagType
    return HttpResponse.json(deleteReviewTagStore(tagType, String(params.tagId)))
  }),

  http.get('/api/review/fill-tags', async () => {
    await delay(80)
    return HttpResponse.json(getReviewFillTagsStore())
  }),

  http.get('/api/review/fill-tags/:orderId', async ({ params }) => {
    await delay(80)
    const row = getReviewFillTagStore(String(params.orderId))
    if (!row) {
      return HttpResponse.json(
        {
          code: 'REVIEW_FILL_TAG_NOT_FOUND',
          message: '成交标签不存在',
          trace_id: `${Date.now()}`,
        },
        { status: 404 },
      )
    }
    return HttpResponse.json(row)
  }),

  http.put('/api/review/fill-tags/:orderId', async ({ request, params }) => {
    await delay(100)
    const payload = (await request.json()) as TradeFillTagUpdateRequest
    const updated = updateReviewFillTagStore(String(params.orderId), payload)
    if (!updated) {
      return HttpResponse.json(
        {
          code: 'REVIEW_FILL_TAG_INVALID',
          message: 'order_id not found in fill records',
          trace_id: `${Date.now()}`,
        },
        { status: 400 },
      )
    }
    return HttpResponse.json(updated)
  }),

  http.get('/api/review/tag-stats', async ({ request }) => {
    await delay(100)
    const url = new URL(request.url)
    return HttpResponse.json(
      getReviewTagStatsStore({
        date_from: url.searchParams.get('date_from') ?? undefined,
        date_to: url.searchParams.get('date_to') ?? undefined,
      }),
    )
  }),

  http.get('/api/market/news', async ({ request }) => {
    await delay(140)
    const url = new URL(request.url)
    const limitRaw = Number(url.searchParams.get('limit') ?? 20)
    const ageHoursRaw = Number(url.searchParams.get('age_hours') ?? 72)
    const ageHours = ageHoursRaw === 24 || ageHoursRaw === 48 || ageHoursRaw === 72 ? ageHoursRaw : 72
    const refresh = (url.searchParams.get('refresh') ?? '').toLowerCase() === 'true'
    const sourceDomainsRaw = url.searchParams.get('source_domains') ?? ''
    const sourceDomains = sourceDomainsRaw
      .replace(/;/g, ',')
      .replace(/\|/g, ',')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    return HttpResponse.json(
      getMarketNewsStore({
        query: url.searchParams.get('query') ?? undefined,
        symbol: url.searchParams.get('symbol') ?? undefined,
        source_domains: sourceDomains,
        age_hours: ageHours,
        refresh,
        limit: Number.isFinite(limitRaw) ? limitRaw : 20,
      }),
    )
  }),

  http.get('/api/ai/records', async () => {
    await delay(160)
    return HttpResponse.json({ items: getAIRecords() })
  }),

  http.delete('/api/ai/records', async ({ request }) => {
    await delay(140)
    const url = new URL(request.url)
    const symbol = url.searchParams.get('symbol') ?? ''
    const fetchedAt = url.searchParams.get('fetched_at') ?? ''
    const provider = url.searchParams.get('provider') ?? undefined
    return HttpResponse.json(deleteAIRecord(symbol, fetchedAt, provider))
  }),

  http.post('/api/ai/providers/test', async ({ request }) => {
    await delay(260)
    const payload = (await request.json()) as AIProviderTestRequest
    return HttpResponse.json(testAIProvider(payload))
  }),

  http.get('/api/ai/playbook', async () => {
    await delay(120)
    return HttpResponse.json({
      playbook: { screener: { funnel: {} }, strategies: {} },
      text: '{}',
    })
  }),

  http.get('/api/ai/playbook/local', async () => {
    await delay(80)
    return HttpResponse.json({ principles: [], updated_at: '' })
  }),

  http.put('/api/ai/playbook/local', async ({ request }) => {
    await delay(80)
    const payload = (await request.json()) as { principles?: string[] }
    return HttpResponse.json({
      principles: payload.principles ?? [],
      updated_at: new Date().toISOString(),
    })
  }),

  http.get('/api/ai/quick-prompts', async () => {
    await delay(80)
    return HttpResponse.json({ templates: [] })
  }),

  http.put('/api/ai/quick-prompts', async ({ request }) => {
    await delay(80)
    const payload = (await request.json()) as { templates?: unknown[] }
    return HttpResponse.json({ templates: payload.templates ?? [] })
  }),

  http.post('/api/ai/chat', async ({ request }) => {
    await delay(200)
    const payload = (await request.json()) as {
      session_id?: string
      messages?: Array<{ role: string; content: string }>
      stream?: boolean
    }
    const lastUser = [...(payload.messages ?? [])].reverse().find((item) => item.role === 'user')
    const reply = `（Mock）已收到：${lastUser?.content ?? ''}`
    if (payload.stream) {
      const body = [
        `data: ${JSON.stringify({ type: 'delta', content: reply })}\n\n`,
        `data: ${JSON.stringify({
          type: 'done',
          session_id: payload.session_id ?? 'mock-session',
          message: { role: 'assistant', content: reply },
          citations: ['playbook', 'runtime'],
        })}\n\n`,
      ].join('')
      return new HttpResponse(body, {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    return HttpResponse.json({
      session_id: payload.session_id ?? 'mock-session',
      message: {
        role: 'assistant',
        content: reply,
      },
      citations: ['playbook', 'runtime'],
    })
  }),

  http.get('/api/ai/sessions', async () => {
    await delay(80)
    return HttpResponse.json({ items: [] })
  }),

  http.get('/api/ai/usage', async () => {
    await delay(80)
    return HttpResponse.json({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 })
  }),

  http.post('/api/ai/parameter-proposals', async ({ request }) => {
    await delay(120)
    const payload = (await request.json()) as { message?: string }
    return HttpResponse.json({
      proposal_id: 'pp_mock001',
      created_at: '2026-06-02 12:00:00',
      expires_at: '2026-06-02 12:15:00',
      context_page: 'backtest',
      summary: payload.message ?? 'mock proposal',
      changes: [],
    })
  }),

  http.post('/api/ai/parameter-proposals/:proposalId/apply', async () => {
    await delay(120)
    return HttpResponse.json({ proposal_id: 'pp_mock001', applied: [], rejected: [] })
  }),

  http.get('/api/config', async () => {
    await delay(90)
    return HttpResponse.json(getConfigStore())
  }),

  http.put('/api/config', async ({ request }) => {
    const payload = (await request.json()) as AppConfig
    await delay(120)
    return HttpResponse.json(setConfigStore(payload))
  }),

  http.get('/api/system/storage', async () => {
    await delay(100)
    return HttpResponse.json(getSystemStorageStore())
  }),

  http.get('/api/system/wyckoff-event-store/stats', async () => {
    await delay(120)
    return HttpResponse.json({
      enabled: true,
      read_only: false,
      db_path: 'C:/Users/mock/.tdx-trend/wyckoff_events.sqlite',
      db_exists: true,
      db_record_count: 1280,
      runtime_cache_size: 320,
      cache_hits: 5600,
      cache_misses: 1200,
      cache_hit_rate: 0.823529,
      cache_miss_rate: 0.176471,
      snapshot_reads: 6800,
      avg_snapshot_read_ms: 1.842105,
      lazy_fill_writes: 1200,
      backfill_runs: 3,
      backfill_writes: 980,
      quality_empty_events: 12,
      quality_score_outliers: 0,
      quality_date_misaligned: 3,
      last_backfill_started_at: '2026-02-20 09:30:00',
      last_backfill_finished_at: '2026-02-20 09:33:20',
      last_backfill_duration_sec: 200.0,
      last_backfill_scan_dates: 20,
      last_backfill_symbols: 580,
      last_backfill_quality_empty_events: 2,
      last_backfill_quality_score_outliers: 0,
      last_backfill_quality_date_misaligned: 1,
    })
  }),

  http.post('/api/system/wyckoff-event-store/backfill', async ({ request }) => {
    const payload = (await request.json()) as {
      date_from?: string
      date_to?: string
      markets?: string[]
      window_days_list?: number[]
      max_symbols_per_day?: number
      force_rebuild?: boolean
    }
    await delay(450)
    return HttpResponse.json({
      ok: true,
      message: '事件库回填完成：扫描 10 日，标的 260，命中 320，重算 180，写入 180。',
      date_from: payload.date_from || '2026-01-01',
      date_to: payload.date_to || '2026-01-15',
      markets: Array.isArray(payload.markets) && payload.markets.length > 0 ? payload.markets : ['sh', 'sz'],
      window_days_list:
        Array.isArray(payload.window_days_list) && payload.window_days_list.length > 0 ? payload.window_days_list : [60],
      scan_dates: 10,
      loaded_rows_total: 2600,
      symbols_scanned: 260,
      cache_hits: 320,
      cache_misses: 180,
      computed_count: 180,
      write_count: 180,
      quality_empty_events: 4,
      quality_score_outliers: 0,
      quality_date_misaligned: 1,
      started_at: '2026-02-20 09:30:00',
      finished_at: '2026-02-20 09:33:20',
      duration_sec: 200.0,
      warnings: payload.force_rebuild ? ['已开启 force_rebuild：命中记录也会重算并覆盖写入。'] : [],
    })
  }),

  http.post('/api/system/sync-market-data', async ({ request }) => {
    const payload = (await request.json()) as MarketDataSyncRequest
    await delay(400)
    return HttpResponse.json(syncMarketDataStore(payload))
  }),
]
