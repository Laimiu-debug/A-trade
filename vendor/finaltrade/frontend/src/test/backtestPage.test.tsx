import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '@/mocks/server'
import { BacktestPage, buildImportedLocalTaskId, resolveEffectiveRunRequest } from '@/pages/backtest/BacktestPage'
import { BacktestTaskWatcher } from '@/shared/components/BacktestTaskWatcher'
import { useBacktestPlateauTaskStore } from '@/state/backtestPlateauTaskStore'
import { useBacktestTaskStore } from '@/state/backtestTaskStore'
import { renderWithProviders } from '@/test/renderWithProviders'
import type { BacktestPlateauPoint, BacktestPlateauTaskStatusResponse, BacktestResponse, BacktestRunRequest } from '@/types/contracts'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echart" />,
}))

function buildSucceededResult(note: string) {
  return {
    stats: {
      win_rate: 0.5,
      total_return: 0.08,
      max_drawdown: 0.05,
      avg_pnl_ratio: 0.02,
      trade_count: 2,
      win_count: 1,
      loss_count: 1,
      profit_factor: 1.4,
    },
    trades: [
      {
        symbol: 'sz300750',
        name: '宁德时代',
        signal_date: '2026-01-02',
        entry_date: '2026-01-03',
        exit_date: '2026-01-10',
        entry_signal: 'SOS',
        entry_phase: '吸筹D',
        entry_quality_score: 78.5,
        exit_reason: 'event_exit',
        quantity: 100,
        entry_price: 160,
        exit_price: 170,
        holding_days: 7,
        pnl_amount: 980,
        pnl_ratio: 0.06125,
      },
    ],
    equity_curve: [
      { date: '2026-01-01', equity: 1_000_000, realized_pnl: 0 },
      { date: '2026-01-10', equity: 1_000_980, realized_pnl: 980 },
    ],
    drawdown_curve: [
      { date: '2026-01-01', drawdown: 0 },
      { date: '2026-01-10', drawdown: -0.01 },
    ],
    monthly_returns: [{ month: '2026-01', return_ratio: 0.00098, pnl_amount: 980, trade_count: 1 }],
    top_trades: [],
    bottom_trades: [],
    cost_snapshot: {
      initial_capital: 1_000_000,
      commission_rate: 0.0008,
      min_commission: 0,
      stamp_tax_rate: 0,
      transfer_fee_rate: 0,
      slippage_rate: 0,
    },
    range: {
      date_from: '2026-01-01',
      date_to: '2026-01-31',
      date_axis: 'sell',
    },
    notes: [note],
    candidate_count: 6,
    skipped_count: 2,
    fill_rate: 0.333333,
    max_concurrent_positions: 2,
  }
}

function buildMockPlateauTask(): BacktestPlateauTaskStatusResponse {
  const basePayload: BacktestRunRequest = {
    mode: 'trend_pool',
    run_id: 'mock-run-001',
    trend_step: 'auto',
    pool_roll_mode: 'daily',
    board_filters: ['main', 'gem', 'star'],
    strategy_id: 'wyckoff_trend_v1',
    strategy_params: {},
    date_from: '2026-01-01',
    date_to: '2026-01-31',
    window_days: 60,
    min_score: 50,
    require_sequence: false,
    min_event_count: 1,
    entry_events: ['Spring', 'SOS'],
    exit_events: ['SOW', 'LPSY'],
    initial_capital: 1_000_000,
    position_pct: 0.2,
    max_positions: 5,
    stop_loss: 0.05,
    take_profit: 0.15,
    trailing_stop_pct: 0.03,
    max_hold_days: 60,
    fee_bps: 10,
    prioritize_signals: true,
    priority_mode: 'balanced',
    priority_topk_per_day: 0,
    enforce_t1: true,
    entry_delay_days: 1,
    delay_invalidation_enabled: true,
    max_symbols: 120,
    enable_advanced_analysis: true,
  }

  const points: BacktestPlateauPoint[] = [
    {
      params: {
        window_days: 55,
        min_score: 45,
        stop_loss: 0.04,
        take_profit: 0.1,
        trailing_stop_pct: 0.02,
        max_positions: 4,
        position_pct: 0.15,
        max_symbols: 90,
        priority_topk_per_day: 2,
      },
      stats: {
        win_rate: 0.55,
        total_return: 0.12,
        max_drawdown: -0.09,
        avg_pnl_ratio: 0.03,
        trade_count: 16,
        win_count: 9,
        loss_count: 7,
        profit_factor: 1.5,
      },
      candidate_count: 32,
      skipped_count: 8,
      fill_rate: 0.5,
      max_concurrent_positions: 4,
      annual_trades: 18.2,
      score: 88,
      point_score: 79,
      local_score: 82,
      plateau_score: 84,
      passes_hard_filters: true,
      region_rank: 2,
      cache_hit: false,
      detail_key: 'plateau-b',
      error: null,
    },
    {
      params: {
        window_days: 70,
        min_score: 52,
        stop_loss: 0.05,
        take_profit: 0.18,
        trailing_stop_pct: 0.03,
        max_positions: 5,
        position_pct: 0.2,
        max_symbols: 120,
        priority_topk_per_day: 1,
      },
      stats: {
        win_rate: 0.48,
        total_return: 0.04,
        max_drawdown: -0.05,
        avg_pnl_ratio: 0.02,
        trade_count: 11,
        win_count: 5,
        loss_count: 6,
        profit_factor: 1.2,
      },
      candidate_count: 28,
      skipped_count: 6,
      fill_rate: 0.39,
      max_concurrent_positions: 5,
      annual_trades: 14.4,
      score: 96,
      point_score: 87,
      local_score: 89,
      plateau_score: 91,
      passes_hard_filters: true,
      region_rank: 1,
      cache_hit: false,
      detail_key: 'plateau-a',
      error: null,
    },
    {
      params: {
        window_days: 40,
        min_score: 38,
        stop_loss: 0.03,
        take_profit: 0.08,
        trailing_stop_pct: 0.01,
        max_positions: 3,
        position_pct: 0.12,
        max_symbols: 60,
        priority_topk_per_day: 3,
      },
      stats: {
        win_rate: 0.41,
        total_return: -0.03,
        max_drawdown: -0.11,
        avg_pnl_ratio: -0.01,
        trade_count: 8,
        win_count: 3,
        loss_count: 5,
        profit_factor: 0.8,
      },
      candidate_count: 20,
      skipped_count: 10,
      fill_rate: 0.25,
      max_concurrent_positions: 3,
      annual_trades: 9.6,
      score: 71,
      point_score: 68,
      local_score: 65,
      plateau_score: 63,
      passes_hard_filters: false,
      region_rank: 3,
      cache_hit: true,
      detail_key: 'plateau-c',
      error: null,
    },
  ]

  return {
    task_id: 'plateau_test_001',
    status: 'succeeded',
    progress: {
      sampling_mode: 'grid',
      processed_points: 3,
      total_points: 3,
      percent: 100,
      message: '收益平原已完成',
      started_at: '2026-02-20T10:00:00.000Z',
      updated_at: '2026-02-20T10:05:00.000Z',
    },
    result: {
      base_payload: basePayload,
      total_combinations: 3,
      evaluated_combinations: 3,
      points,
      best_point: points[1],
      recommended_point: points[1],
      peak_point: points[0],
      regions: [],
      correlations: [],
      generated_at: '2026-02-20 10:05:00',
      notes: ['mock plateau result'],
    },
    error: null,
    error_code: null,
  }
}

describe('BacktestPage', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useBacktestTaskStore.setState({
      tasksById: {},
      payloadById: {},
      activeTaskIds: [],
      selectedTaskId: undefined,
    })
    useBacktestPlateauTaskStore.setState({
      tasksById: {},
      activeTaskIds: [],
      selectedTaskId: undefined,
    })
    server.use(
      http.get('/api/backtest/tasks', async () => HttpResponse.json({ items: [] })),
      http.get('/api/backtest/plateau/tasks', async () => HttpResponse.json({ items: [] })),
    )
  })

  it('runs backtest and renders result summary', async () => {
    let pollCount = 0
    server.use(
      http.post('/api/backtest/tasks', async () => {
        await delay(20)
        return HttpResponse.json({ task_id: 'bt_test_001' })
      }),
      http.get('/api/backtest/tasks/:taskId', async () => {
        pollCount += 1
        await delay(20)
        if (pollCount <= 1) {
          return HttpResponse.json({
            task_id: 'bt_test_001',
            status: 'running',
            progress: {
              mode: 'daily',
              current_date: '2026-01-10',
              processed_dates: 1,
              total_dates: 3,
              percent: 33.3,
              message: '滚动筛选进度 1/3',
              stage_timings: [],
              started_at: '2026-02-20 10:00:00',
              updated_at: '2026-02-20 10:00:01',
            },
          })
        }
        return HttpResponse.json({
          task_id: 'bt_test_001',
          status: 'succeeded',
          progress: {
            mode: 'daily',
            current_date: '2026-01-31',
            processed_dates: 3,
            total_dates: 3,
            percent: 100,
            message: '回测完成。',
            stage_timings: [],
            started_at: '2026-02-20 10:00:00',
            updated_at: '2026-02-20 10:00:05',
          },
          result: buildSucceededResult('mock note'),
        })
      }),
    )

    renderWithProviders(
      <>
        <BacktestTaskWatcher />
        <BacktestPage />
      </>,
      '/backtest',
    )

    const runButton = await screen.findByRole('button', { name: /开始回测/ })
    await userEvent.click(runButton)

    expect(await screen.findByText('候选信号')).toBeInTheDocument()
    expect(await screen.findByText('sz300750')).toBeInTheDocument()
    const chartLink = await screen.findByRole('link', { name: 'sz300750' })
    expect(chartLink.getAttribute('href')).toContain('/stocks/sz300750/chart')

    await waitFor(() => {
      const task = useBacktestTaskStore.getState().tasksById.bt_test_001
      expect(task?.result?.notes ?? []).toContain('mock note')
    })
  }, 60_000)

  it('binds local screener run_id into backtest form', async () => {
    window.localStorage.setItem(
      'tdx-trend-screener-cache-v4',
      JSON.stringify({
        run_meta: {
          runId: 'latest-run-20260220',
          asOfDate: '2026-02-19',
        },
        form_values: {
          board_filters: ['main', 'gem', 'star'],
        },
      }),
    )
    server.use(
      http.get('/api/screener/latest-run', async () => {
        return HttpResponse.json({ message: 'mock latest run unavailable' }, { status: 500 })
      }),
    )

    renderWithProviders(
      <>
        <BacktestTaskWatcher />
        <BacktestPage />
      </>,
      '/backtest',
    )

    const bindButton = await screen.findByRole('button', { name: /带入最新筛选/ })
    await userEvent.click(bindButton)

    await waitFor(() => {
      const input = screen.getByPlaceholderText('请输入筛选任务 run_id') as HTMLInputElement
      expect(input.value).toBe('latest-run-20260220')
    })
  }, 60_000)

  it('loads imported report with effective run_id and re-exports that snapshot', async () => {
    window.localStorage.setItem(
      'tdx-trend-backtest-collapsed-modules-v1',
      JSON.stringify({ report_share: false }),
    )
    const sharedRunRequest: BacktestRunRequest = {
      mode: 'trend_pool',
      run_id: 'shared-run-20260301',
      trend_step: 'auto',
      pool_roll_mode: 'daily',
      board_filters: ['main', 'gem', 'star'],
      strategy_id: 'wyckoff_trend_v1',
      strategy_params: {},
      date_from: '2026-01-01',
      date_to: '2026-01-31',
      window_days: 60,
      min_score: 50,
      require_sequence: false,
      min_event_count: 1,
      entry_events: ['Spring', 'SOS'],
      exit_events: ['SOW', 'LPSY'],
      initial_capital: 1_000_000,
      position_pct: 0.2,
      max_positions: 5,
      stop_loss: 0.05,
      take_profit: 0.15,
      trailing_stop_pct: 0.03,
      max_hold_days: 60,
      fee_bps: 10,
      prioritize_signals: true,
      priority_mode: 'balanced',
      priority_topk_per_day: 0,
      enforce_t1: true,
      entry_delay_days: 1,
      delay_invalidation_enabled: true,
      max_symbols: 120,
      enable_advanced_analysis: true,
    }
    let exportedRunRequest: BacktestRunRequest | null = null
    server.use(
      http.get('/api/backtest/reports', async () => HttpResponse.json({
        items: [
          {
            report_id: 'shared-report-001',
            created_at: '2026-03-01T10:00:00Z',
            first_imported_at: '2026-03-09T10:00:00Z',
            last_imported_at: '2026-03-09T10:00:00Z',
            source_file_name: 'shared-report-001.ftbt',
            package_size_bytes: 2048,
            trade_count: 2,
            total_return: 0.08,
            max_drawdown: 0.05,
            win_rate: 0.5,
            date_from: '2026-01-01',
            date_to: '2026-01-31',
            has_plateau_result: false,
          },
        ],
      })),
      http.get('/api/backtest/reports/:reportId', async () => HttpResponse.json({
        summary: {
          report_id: 'shared-report-001',
          created_at: '2026-03-01T10:00:00Z',
          first_imported_at: '2026-03-09T10:00:00Z',
          last_imported_at: '2026-03-09T10:00:00Z',
          source_file_name: 'shared-report-001.ftbt',
          package_size_bytes: 2048,
          trade_count: 2,
          total_return: 0.08,
          max_drawdown: 0.05,
          win_rate: 0.5,
          date_from: '2026-01-01',
          date_to: '2026-01-31',
          has_plateau_result: false,
        },
        manifest: {
          schema_version: 'ftbt-1.0',
          package_type: 'backtest_report',
          created_at: '2026-03-01T10:00:00Z',
          report_id: 'shared-report-001',
          app: { name: 'Final Trade', version: 'test' },
          files: [],
        },
        run_request: {
          ...sharedRunRequest,
          run_id: undefined,
        },
        run_result: {
          ...buildSucceededResult('imported report'),
          effective_run_request: sharedRunRequest,
        },
        plateau_result: null,
      })),
      http.post('/api/backtest/reports/build', async ({ request }) => {
        const body = await request.json() as { run_request: BacktestRunRequest }
        exportedRunRequest = body.run_request
        return HttpResponse.json({
          report_id: 'shared-report-001-reexport',
          file_name: 'shared-report-001-reexport.ftbt',
          file_base64: 'bW9jaw==',
          manifest: {
            schema_version: 'ftbt-1.0',
            package_type: 'backtest_report',
            created_at: '2026-03-09T10:05:00Z',
            report_id: 'shared-report-001-reexport',
            app: { name: 'Final Trade', version: 'test' },
            files: [],
          },
        })
      }),
    )

    renderWithProviders(
      <>
        <BacktestTaskWatcher />
        <BacktestPage />
      </>,
      '/backtest',
    )
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '加载报告' })).not.toBeDisabled()
    })
    fireEvent.click(screen.getByRole('button', { name: '加载报告' }))

    await waitFor(() => {
      const payload = useBacktestTaskStore.getState().payloadById[buildImportedLocalTaskId('shared-report-001', 'backtest')]
      expect(payload?.run_id).toBe('shared-run-20260301')
    })

    fireEvent.click(await screen.findByRole('button', { name: '导出 ftbt' }))

    await waitFor(() => {
      expect(exportedRunRequest?.run_id).toBe('shared-run-20260301')
    })
  }, 60_000)

  it('builds compact local task ids for imported reports with long report ids', () => {
    const longReportId = `shared-report-${'x'.repeat(96)}`
    const backtestTaskId = buildImportedLocalTaskId(longReportId, 'backtest')
    const plateauTaskId = buildImportedLocalTaskId(longReportId, 'plateau')
    expect(backtestTaskId.length).toBeLessThanOrEqual(64)
    expect(plateauTaskId.length).toBeLessThanOrEqual(64)
    expect(backtestTaskId.startsWith('imp_')).toBe(true)
    expect(plateauTaskId.startsWith('imp_plateau_')).toBe(true)
    expect(backtestTaskId).toBe(buildImportedLocalTaskId(longReportId, 'backtest'))
    expect(plateauTaskId).toBe(buildImportedLocalTaskId(longReportId, 'plateau'))
    expect(backtestTaskId).not.toBe(`imp_${longReportId}`)
    expect(plateauTaskId).not.toBe(`imp_plateau_${longReportId}`)
  })

  it('prefers effective run_request when report carries resolved run_id', () => {
    const rawRunRequest: BacktestRunRequest = {
      mode: 'trend_pool',
      run_id: undefined,
      trend_step: 'auto',
      pool_roll_mode: 'daily',
      board_filters: ['main', 'gem', 'star'],
      strategy_id: 'wyckoff_trend_v1',
      strategy_params: {},
      date_from: '2026-01-01',
      date_to: '2026-01-31',
      window_days: 60,
      min_score: 50,
      require_sequence: false,
      min_event_count: 1,
      entry_events: ['Spring', 'SOS'],
      exit_events: ['SOW', 'LPSY'],
      initial_capital: 1_000_000,
      position_pct: 0.2,
      max_positions: 5,
      stop_loss: 0.05,
      take_profit: 0.15,
      trailing_stop_pct: 0.03,
      max_hold_days: 60,
      fee_bps: 10,
      prioritize_signals: true,
      priority_mode: 'balanced',
      priority_topk_per_day: 0,
      enforce_t1: true,
      entry_delay_days: 1,
      delay_invalidation_enabled: true,
      max_symbols: 120,
      enable_advanced_analysis: true,
    }
    const effectiveRunRequest: BacktestRunRequest = {
      ...rawRunRequest,
      run_id: 'shared-run-20260301',
    }

    const resolved = resolveEffectiveRunRequest(rawRunRequest, {
      ...buildSucceededResult('imported report'),
      effective_run_request: effectiveRunRequest,
    } as BacktestResponse)

    expect(resolved.run_id).toBe('shared-run-20260301')
    expect(resolved.mode).toBe('trend_pool')
  })

  it('submits full_market via task api instead of sync api', async () => {
    let syncRunCalled = false
    server.use(
      http.post('/api/backtest/run', async () => {
        syncRunCalled = true
        return HttpResponse.json({ error: 'should not be called' }, { status: 500 })
      }),
      http.post('/api/backtest/tasks', async () => {
        await delay(20)
        return HttpResponse.json({ task_id: 'bt_test_full_001' })
      }),
      http.get('/api/backtest/tasks/:taskId', async () => {
        await delay(20)
        return HttpResponse.json({
          task_id: 'bt_test_full_001',
          status: 'succeeded',
          progress: {
            mode: 'weekly',
            current_date: '2026-01-31',
            processed_dates: 2,
            total_dates: 2,
            percent: 100,
            message: '回测完成。',
            stage_timings: [],
            started_at: '2026-02-20 10:00:00',
            updated_at: '2026-02-20 10:00:05',
          },
          result: {
            ...buildSucceededResult('full market task'),
            trades: [],
            candidate_count: 0,
            skipped_count: 0,
            fill_rate: 0,
            max_concurrent_positions: 0,
          },
        })
      }),
    )

    renderWithProviders(
      <>
        <BacktestTaskWatcher />
        <BacktestPage />
      </>,
      '/backtest',
    )

    await userEvent.click(await screen.findByText('全市场'))
    await userEvent.click(await screen.findByRole('button', { name: /开始回测/ }))

    await waitFor(() => {
      const task = useBacktestTaskStore.getState().tasksById.bt_test_full_001
      expect(task?.result?.notes ?? []).toContain('full market task')
    })
    expect(syncRunCalled).toBe(false)
  }, 60_000)

  it('submits THS main-force strategies without entry events and allows empty exit events', async () => {
    window.localStorage.setItem(
      'tdx-trend-backtest-form-v2',
      JSON.stringify({
        strategy_id: 'ths_main_force_golden_cross_v1',
        entry_events: [],
        exit_events: [],
        min_event_count: 0,
        require_sequence: false,
      }),
    )

    let submittedPayload: BacktestRunRequest | null = null
    server.use(
      http.get('/api/strategies', async () => HttpResponse.json({
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
            strategy_params_schema: {},
            strategy_params_defaults: {},
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
            strategy_params_schema: {},
            strategy_params_defaults: {},
          },
        ],
      })),
      http.post('/api/backtest/tasks', async ({ request }) => {
        submittedPayload = await request.json() as BacktestRunRequest
        return HttpResponse.json({ task_id: 'bt_test_ths_001' })
      }),
      http.get('/api/backtest/tasks/:taskId', async () => HttpResponse.json({
        task_id: 'bt_test_ths_001',
        status: 'succeeded',
        progress: {
          mode: 'daily',
          current_date: '2026-01-31',
          processed_dates: 3,
          total_dates: 3,
          percent: 100,
          message: '回测完成。',
          stage_timings: [],
          started_at: '2026-02-20 10:00:00',
          updated_at: '2026-02-20 10:00:05',
        },
        result: buildSucceededResult('ths note'),
      })),
    )

    renderWithProviders(
      <>
        <BacktestTaskWatcher />
        <BacktestPage />
      </>,
      '/backtest',
    )

    await userEvent.click(await screen.findByRole('button', { name: /开始回测/ }))

    await waitFor(() => {
      expect(submittedPayload).not.toBeNull()
    })
    const payload = submittedPayload as unknown as BacktestRunRequest
    expect(payload.strategy_id).toBe('ths_main_force_golden_cross_v1')
    expect(payload.entry_events).toEqual([])
    expect(payload.exit_events).toEqual([])
    expect(payload.min_event_count).toBe(0)
    expect(payload.require_sequence).toBe(false)
  }, 60_000)

  it('supports sorting plateau table by header in ascending and descending order', async () => {
    const plateauTask = buildMockPlateauTask()
    useBacktestPlateauTaskStore.setState({
      tasksById: { [plateauTask.task_id]: plateauTask },
      activeTaskIds: [],
      selectedTaskId: plateauTask.task_id,
    })

    const firstView = renderWithProviders(<BacktestPage />, '/backtest')

    const plateauCard = (await screen.findByText('收益平原结果')).closest('.ant-card')
    expect(plateauCard).not.toBeNull()
    const table = plateauCard?.querySelector('.ant-table')
    expect(table).not.toBeNull()

    const getBodyRows = () => {
      const rows = Array.from(table!.querySelectorAll('.ant-table-tbody > tr.ant-table-row'))
      return rows.map((row) => row.textContent?.replace(/\s+/g, ' ').trim() ?? '')
    }

    await waitFor(() => {
      expect(getBodyRows()[0]).toContain('70')
    })

    const totalReturnHeader = table!.querySelector('thead th[aria-label="总收益"]')
    expect(totalReturnHeader).not.toBeNull()
    await userEvent.click(totalReturnHeader!)

    await waitFor(() => {
      expect(getBodyRows()[0]).toContain('40')
    })

    await userEvent.click(totalReturnHeader!)

    await waitFor(() => {
      expect(getBodyRows()[0]).toContain('55')
    })

    await userEvent.click(screen.getByRole('button', { name: '恢复默认排序' }))

    await waitFor(() => {
      expect(getBodyRows()[0]).toContain('70')
    })
    firstView.unmount()

    const persistedDraft = JSON.parse(
      window.localStorage.getItem('tdx-trend-backtest-plateau-form-v2') || '{}',
    ) as { table_sort_key?: string; table_sort_order?: string }
    expect(persistedDraft.table_sort_key).toBe('score')
    expect(persistedDraft.table_sort_order).toBe('descend')
  }, 60_000)
})
