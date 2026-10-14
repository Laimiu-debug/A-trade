/* eslint-disable react-refresh/only-export-components */
import { useEffect, useMemo, useRef, useState } from 'react'
import dayjs, { Dayjs } from 'dayjs'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  DatePicker,
  Dropdown,
  Input,
  InputNumber,
  Modal,
  Progress,
  Radio,
  Row,
  Select,
  Spin,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { SortOrder, SorterResult } from 'antd/es/table/interface'
import type { MenuProps } from 'antd'
import ReactECharts from 'echarts-for-react'
import { Link, useLocation } from 'react-router-dom'
import * as XLSX from 'xlsx'
import { ApiError } from '@/shared/api/client'
import { denseRiskGrid, gridSummary, numericAxis } from '@/shared/utils/plateauGrid'
import { isLegacyRhythmResult, plateauRecommendation, resolvePlateauAxes } from '@/shared/utils/plateauSafety'
import { isApiNotFoundError } from '@/shared/api/errors'
import {
  buildBacktestReportPackage,
  cancelBacktestPlateauTask,
  cancelBacktestTask,
  deleteBacktestReport,
  deleteBacktestPlateauTask,
  deleteBacktestTask,
  getBacktestPlateauPointDetail,
  getBacktestReport,
  getConfig,
  listBacktestTasks,
  getEventJudgmentProfiles,
  getLatestScreenerRun,
  getStrategies,
  importBacktestReportPackage,
  listBacktestReports,
  listBacktestPlateauTasks,
  pauseBacktestPlateauTask,
  pauseBacktestTask,
  resumeBacktestPlateauTask,
  resumeBacktestTask,
  runBacktest,
  getBacktestTask,
  getBacktestPlateauTask,
  startBacktestPlateauTask,
  startBacktestTask,
} from '@/shared/api/endpoints'
import { DismissibleAlert } from '@/shared/components/DismissibleAlert'
import { EditableDetailTable } from '@/shared/components/EditableDetailTable'
import { PageHeader } from '@/shared/components/PageHeader'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { buildPageTitle } from '@/state/aiAssistantStore'
import { useAIParameterApplyStore } from '@/state/aiParameterApplyStore'
import { useBacktestPlateauTaskStore } from '@/state/backtestPlateauTaskStore'
import { useBacktestTaskStore } from '@/state/backtestTaskStore'
import {
  allowsOptionalBacktestExitEvents,
  buildStrategyParamsPayload,
  filterVisibleStrategies,
  getSharedLastStrategyId,
  getSharedStrategyParams,
  isEventIndependentBacktestStrategy,
  normalizeStrategyParams,
  parseStrategyParamSchema,
  resolveDefaultStrategyId,
  setSharedStrategyParams,
} from '@/shared/utils/strategyParams'
import type {
  BacktestPlateauCorrelationRow,
  BacktestPlateauParams,
  BacktestPlateauPoint,
  BacktestPlateauPointDetailResponse,
  BacktestPlateauResponse,
  BacktestPlateauTaskStatusResponse,
  BacktestPoolRollMode,
  BacktestPriorityMode,
  BacktestMonteCarloSummary,
  BacktestRegimeBucket,
  BacktestReportSummary,
  BacktestResponse,
  BacktestRunRequest,
  BacktestStabilityDiagnostics,
  BacktestTaskStatusResponse,
  BacktestTrade,
  BacktestWalkForwardReport,
  BoardFilter,
  SignalScanMode,
  StrategyId,
  TrendPoolStep,
} from '@/types/contracts'
import { formatMoney, formatPct } from '@/shared/utils/format'

const TRADE_BACKTEST_DEFAULTS = {
  mode: 'trend_pool' as SignalScanMode,
  trendStep: 'auto' as TrendPoolStep,
  poolRollMode: 'daily' as BacktestPoolRollMode,
  windowDays: 60,
  minScore: 55,
  minEventCount: 1,
  requireSequence: false,
  entryEvents: ['Spring', 'SOS', 'JOC', 'LPS'],
  exitEvents: ['UTAD', 'SOW', 'LPSY'],
  initialCapital: 1_000_000,
  positionPct: 0.2,
  maxPositions: 5,
  stopLoss: 0.05,
  takeProfit: 0.15,
  trailingStopPct: 0,
  intradayTrailingEnabled: true,
  dailyTrailingClearEnabled: true,
  intradayTrailingReduceRatio: 0.5,
  dailyTrailingConfirmDays: 2,
  maxHoldDays: 60,
  feeBps: 10,
  prioritizeSignals: true,
  priorityMode: 'balanced' as BacktestPriorityMode,
  priorityTopK: 0,
  enforceT1: true,
  entryDelayDays: 1,
  delayInvalidationEnabled: true,
  maxSymbols: 120,
  enableAdvancedAnalysis: true,
  defaultLookbackDays: 180,
}

const SCREENER_CACHE_KEY = 'tdx-trend-screener-cache-v4'
const BACKTEST_FORM_CACHE_KEY = 'tdx-trend-backtest-form-v2'
const BACKTEST_PLATEAU_FORM_CACHE_KEY = 'tdx-trend-backtest-plateau-form-v2'
const BACKTEST_PLATEAU_PRESETS_KEY = 'tdx-trend-backtest-plateau-presets-v1'
const BACKTEST_COLLAPSED_MODULES_KEY = 'tdx-trend-backtest-collapsed-modules-v1'
const WY_EVENTS = ['PS', 'SC', 'AR', 'ST', 'TSO', 'Spring', 'SOS', 'JOC', 'LPS', 'UTAD', 'SOW', 'LPSY']
const BACKTEST_DEFAULT_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star']
const ALLOWED_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star', 'beijing', 'st']
type BacktestPlateauTableRow = BacktestPlateauPoint & { __rowKey: string; __rank: number }
type BacktestPlateauPreset = {
  id: string
  name: string
  saved_at: string
  source?: 'plateau' | 'backtest_history'
  point?: BacktestPlateauPoint | null
  run_request?: BacktestRunRequest | null
  stats_summary?: {
    total_return: number
    max_drawdown?: number | null
  } | null
}
type PlateauAxisKey =
  | 'window_days'
  | 'min_score'
  | 'stop_loss'
  | 'take_profit'
  | 'trailing_stop_pct'
  | 'intraday_trailing_reduce_ratio'
  | 'max_positions'
  | 'position_pct'
  | 'max_symbols'
  | 'priority_topk_per_day'
type PlateauMetricKey = 'plateau_score' | 'local_score' | 'point_score' | 'score' | 'total_return'
type PlateauHistoryExportFormat = 'excel' | 'html' | 'ftbt'
type TradeExportFormat = 'excel' | 'csv' | 'html'
type TradePnlChartType = 'pie' | 'bar' | 'treemap'

type SymbolPnlAggregate = {
  symbol: string
  name: string
  pnl_amount: number
  trade_count: number
  trades: BacktestTrade[]
}

type TradeExportMeta = {
  title?: string
  date_from?: string
  date_to?: string
  task_id?: string
}
type PlateauPointRunRecord = {
  rank: number
  point: BacktestPlateauPoint
  runRequest: BacktestRunRequest
  runResult: BacktestResponse | null
  error: string | null
}
const PLATEAU_TABLE_SORT_KEYS = [
  'plateau_score',
  'local_score',
  'point_score',
  'score',
  'total_return',
  'max_drawdown',
  'win_rate',
  'trade_count',
  'annual_trades',
  'window_days',
  'min_score',
  'stop_loss',
  'take_profit',
  'trailing_stop_pct',
  'intraday_trailing_reduce_ratio',
  'max_positions',
  'position_pct',
  'max_symbols',
  'priority_topk_per_day',
  'region_rank',
] as const
type PlateauTableSortKey = (typeof PLATEAU_TABLE_SORT_KEYS)[number]
type PlateauTableSortOrder = Extract<SortOrder, 'ascend' | 'descend'>
type PlateauTableSortState = {
  key: PlateauTableSortKey
  order: PlateauTableSortOrder
}
const DEFAULT_PLATEAU_TABLE_SORT_STATE: PlateauTableSortState = {
  key: 'score',
  order: 'descend',
}

const PLATEAU_AXIS_OPTIONS: Array<{ value: PlateauAxisKey; label: string }> = [
  { value: 'window_days', label: '信号窗口天数' },
  { value: 'min_score', label: '最低评分' },
  { value: 'stop_loss', label: '止损比例(%)' },
  { value: 'take_profit', label: '止盈比例(%)' },
  { value: 'trailing_stop_pct', label: '高位回撤(%)' },
  { value: 'intraday_trailing_reduce_ratio', label: '日内减仓(%)' },
  { value: 'max_positions', label: '最大并发持仓' },
  { value: 'position_pct', label: '单笔仓位占比(%)' },
  { value: 'max_symbols', label: '最大股票数' },
  { value: 'priority_topk_per_day', label: '同日TopK' },
]

const PLATEAU_METRIC_OPTIONS: Array<{ value: PlateauMetricKey; label: string }> = [
  { value: 'plateau_score', label: '收益平原分' },
  { value: 'local_score', label: '局部分' },
  { value: 'point_score', label: '单点质分' },
  { value: 'score', label: '原始评分' },
  { value: 'total_return', label: '总收益率' },
]

const PLATEAU_AXIS_LABEL_MAP: Record<PlateauAxisKey, string> = Object.fromEntries(
  PLATEAU_AXIS_OPTIONS.map((item) => [item.value, item.label]),
) as Record<PlateauAxisKey, string>

const PLATEAU_METRIC_LABEL_MAP: Record<PlateauMetricKey, string> = Object.fromEntries(
  PLATEAU_METRIC_OPTIONS.map((item) => [item.value, item.label]),
) as Record<PlateauMetricKey, string>

const PLATEAU_HISTORY_EXPORT_LABEL_MAP: Record<PlateauHistoryExportFormat, string> = {
  excel: 'Excel',
  html: 'HTML',
  ftbt: 'ftbt',
}

const TRADE_EXPORT_LABEL_MAP: Record<TradeExportFormat, string> = {
  excel: 'Excel',
  csv: 'CSV',
  html: 'HTML',
}

const TRADE_PNL_PROFIT_COLOR = '#c4473d'
const TRADE_PNL_LOSS_COLOR = '#19744f'

type BacktestExecutionPathPreference = NonNullable<BacktestRunRequest['execution_path_preference']>

type BacktestFormDraft = {
  mode: SignalScanMode
  trend_step: TrendPoolStep
  pool_roll_mode: BacktestPoolRollMode
  run_id: string
  board_filters: BoardFilter[]
  strategy_id: StrategyId
  strategy_params: Record<string, unknown>
  date_from: string
  date_to: string
  window_days: number
  min_score: number
  require_sequence: boolean
  min_event_count: number
  entry_events: string[]
  exit_events: string[]
  event_judgment_profile_id?: string
  execution_path_preference?: BacktestExecutionPathPreference
  initial_capital: number
  position_pct: number
  max_positions: number
  stop_loss: number
  take_profit: number
  trailing_stop_pct: number
  intraday_trailing_enabled: boolean
  daily_trailing_clear_enabled: boolean
  intraday_trailing_reduce_ratio: number
  daily_trailing_confirm_days: number
  max_hold_days: number
  fee_bps: number
  prioritize_signals: boolean
  priority_mode: BacktestPriorityMode
  priority_topk_per_day: number
  enforce_t1: boolean
  entry_delay_days: number
  delay_invalidation_enabled: boolean
  max_symbols: number
  enable_advanced_analysis: boolean
  trades_page_size: number
}

type BacktestPlateauFormDraft = {
  sampling_mode: 'grid' | 'lhs'
  sample_points: number
  random_seed: number | null
  window_days_list_raw: string
  min_score_list_raw: string
  stop_loss_pct_list_raw: string
  take_profit_pct_list_raw: string
  trailing_stop_pct_list_raw: string
  intraday_trailing_reduce_ratio_list_raw: string
  max_positions_list_raw: string
  position_pct_list_raw: string
  max_symbols_list_raw: string
  topk_list_raw: string
  heatmap_x_axis: PlateauAxisKey
  heatmap_y_axis: PlateauAxisKey
  heatmap_metric: PlateauMetricKey
  heatmap_show_best_path: boolean
  heatmap_show_cell_label: boolean
  table_sort_key: PlateauTableSortKey
  table_sort_order: PlateauTableSortOrder
}

function formatApiError(error: unknown) {
  if (error instanceof ApiError) return error.message || `请求失败: ${error.code}`
  if (error instanceof Error) return error.message || '请求失败'
  return '请求失败'
}

function toPercent(value: number) {
  return Number((value * 100).toFixed(2))
}

function toRatio(percentValue: number, fallback: number, lower: number, upper: number) {
  const raw = Number.isFinite(percentValue) ? percentValue / 100 : fallback
  return Math.max(lower, Math.min(upper, Number(raw.toFixed(6))))
}

function parseNumericList(
  raw: string,
  options: {
    integer?: boolean
    min: number
    max: number
    precision?: number
    maxLength?: number
  },
): number[] {
  return numericAxis(raw, options)
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let out = ''
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, Math.min(bytes.length, offset + chunkSize))
    out += String.fromCharCode(...Array.from(chunk))
  }
  return btoa(out)
}

function base64ToUint8Array(raw: string): Uint8Array {
  const text = String(raw || '').trim()
  const binary = atob(text)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    out[i] = binary.charCodeAt(i)
  }
  return out
}

function downloadBlob(fileName: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function escapeHtml(raw: string): string {
  return String(raw || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function formatSigned(value: number): string {
  const numeric = Number(value || 0)
  const sign = numeric >= 0 ? '+' : ''
  return `${sign}${numeric.toFixed(3)}`
}

function classifyCorrelationDirection(value: number): '正相关' | '负相关' | '弱相关' {
  if (value > 0.05) return '正相关'
  if (value < -0.05) return '负相关'
  return '弱相关'
}

function correlationDirectionTagColor(direction: '正相关' | '负相关' | '弱相关'): string {
  if (direction === '正相关') return 'success'
  if (direction === '负相关') return 'error'
  return 'default'
}

function safePearsonCorrelation(xValues: number[], yValues: number[]): number {
  const count = Math.min(xValues.length, yValues.length)
  if (count < 2) return 0
  const xs = xValues.slice(0, count).map((item) => Number(item))
  const ys = yValues.slice(0, count).map((item) => Number(item))
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

function buildPlateauCorrelationsFromPoints(points: BacktestPlateauPoint[]): BacktestPlateauCorrelationRow[] {
  if (points.length < 2) return []
  const scoreValues = points.map((row) => Number(row.score))
  const totalReturnValues = points.map((row) => Number(row.stats.total_return))
  const winRateValues = points.map((row) => Number(row.stats.win_rate))
  const specs: Array<{
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
  const rows = specs.map((item) => {
    const xValues = points.map(item.pick)
    return {
      parameter: item.key,
      parameter_label: item.label,
      score_corr: Number(safePearsonCorrelation(xValues, scoreValues).toFixed(6)),
      total_return_corr: Number(safePearsonCorrelation(xValues, totalReturnValues).toFixed(6)),
      win_rate_corr: Number(safePearsonCorrelation(xValues, winRateValues).toFixed(6)),
    } as BacktestPlateauCorrelationRow
  })
  rows.sort((left, right) => {
    const leftStrength = Math.max(Math.abs(left.score_corr), Math.abs(left.total_return_corr), Math.abs(left.win_rate_corr))
    const rightStrength = Math.max(Math.abs(right.score_corr), Math.abs(right.total_return_corr), Math.abs(right.win_rate_corr))
    return rightStrength - leftStrength
  })
  return rows
}

function resolvePlateauCorrelations(plateauResult?: BacktestPlateauResponse | null): BacktestPlateauCorrelationRow[] {
  if (!plateauResult) return []
  const existingRows = Array.isArray(plateauResult.correlations) ? plateauResult.correlations : []
  if (existingRows.length > 0) {
    return existingRows
  }
  const validPoints = (plateauResult.points ?? []).filter((row) => !row.error)
  return buildPlateauCorrelationsFromPoints(validPoints)
}

const BACKTEST_TASK_ID_MAX_LENGTH = 64
const IMPORTED_BACKTEST_TASK_PREFIX = 'imp_'
const IMPORTED_PLATEAU_TASK_PREFIX = 'imp_plateau_'

function hashTextToBase36(text: string): string {
  let hash = 2166136261
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

export function buildImportedLocalTaskId(reportId: string, kind: 'backtest' | 'plateau'): string {
  const prefix = kind === 'plateau' ? IMPORTED_PLATEAU_TASK_PREFIX : IMPORTED_BACKTEST_TASK_PREFIX
  const raw = String(reportId || '').trim() || 'report'
  const safe = raw.replace(/[^0-9A-Za-z._-]/g, '_')
  const maxBodyLength = Math.max(8, BACKTEST_TASK_ID_MAX_LENGTH - prefix.length)
  if (safe.length <= maxBodyLength) {
    return `${prefix}${safe}`
  }
  const hashSuffix = hashTextToBase36(raw).padStart(8, '0').slice(-8)
  const headLength = Math.max(8, maxBodyLength - hashSuffix.length - 1)
  return `${prefix}${safe.slice(0, headLength)}_${hashSuffix}`
}

function isImportedLocalTaskId(taskId: string, kind: 'backtest' | 'plateau'): boolean {
  const prefix = kind === 'plateau' ? IMPORTED_PLATEAU_TASK_PREFIX : IMPORTED_BACKTEST_TASK_PREFIX
  return String(taskId || '').trim().startsWith(prefix)
}

function buildPlateauPointDetailPayloads(
  taskId: string | null | undefined,
  pointRuns: PlateauPointRunRecord[],
): BacktestPlateauPointDetailResponse[] {
  const normalizedTaskId = String(taskId || '').trim()
  if (!normalizedTaskId) return []
  const savedAt = new Date().toISOString()
  return pointRuns.flatMap((item) => {
    const detailKey = String(item.point.detail_key || '').trim()
    if (!detailKey || !item.runResult) {
      return []
    }
    return [
      {
        task_id: normalizedTaskId,
        detail_key: detailKey,
        saved_at: savedAt,
        params: item.point.params,
        run_request: item.runRequest,
        run_result: item.runResult,
      },
    ]
  })
}

export function resolveEffectiveRunRequest(runRequest: BacktestRunRequest, runResult: BacktestResponse): BacktestRunRequest {
  return runResult.effective_run_request ?? runRequest
}

function buildBacktestReportWorkbookBuffer(
  runRequest: BacktestRunRequest,
  runResult: BacktestResponse,
  plateauResult?: BacktestPlateauResponse | null,
): ArrayBuffer {
  const workbook = XLSX.utils.book_new()
  const effectiveRunRequest = resolveEffectiveRunRequest(runRequest, runResult)
  const plateauCorrelations = resolvePlateauCorrelations(plateauResult)
  const plateauBestPoint = plateauRecommendation(plateauResult)
  const riskMetrics = runResult.risk_metrics ?? null
  const stability = runResult.stability_diagnostics ?? null
  const regimeBreakdown = runResult.regime_breakdown ?? []
  const monteCarlo = runResult.monte_carlo ?? null
  const walkForward = runResult.walk_forward ?? null
  const summaryRows = [
    {
      date_from: runResult.range.date_from,
      date_to: runResult.range.date_to,
      mode: effectiveRunRequest.mode,
      pool_roll_mode: effectiveRunRequest.pool_roll_mode,
      execution_path: runResult.execution_path ?? 'unknown',
      strategy_id: runResult.strategy_id ?? effectiveRunRequest.strategy_id ?? '',
      strategy_version: runResult.strategy_version ?? '',
      strategy_params_hash: runResult.strategy_params_hash ?? '',
      entry_delay_days: effectiveRunRequest.entry_delay_days,
      delay_invalidation_enabled: effectiveRunRequest.delay_invalidation_enabled,
      matrix_event_semantic_version: effectiveRunRequest.matrix_event_semantic_version,
      trade_count: runResult.stats.trade_count,
      win_count: runResult.stats.win_count,
      loss_count: runResult.stats.loss_count,
      win_rate: runResult.stats.win_rate,
      total_return: runResult.stats.total_return,
      max_drawdown: runResult.stats.max_drawdown,
      avg_pnl_ratio: runResult.stats.avg_pnl_ratio,
      profit_factor: runResult.stats.profit_factor,
      candidate_count: runResult.candidate_count,
      skipped_count: runResult.skipped_count,
      fill_rate: runResult.fill_rate,
      max_concurrent_positions: runResult.max_concurrent_positions,
      has_plateau_result: Boolean(plateauResult),
      plateau_total_combinations: plateauResult?.total_combinations ?? null,
      plateau_evaluated_combinations: plateauResult?.evaluated_combinations ?? null,
      plateau_best_score: plateauBestPoint?.score ?? null,
      plateau_best_total_return: plateauBestPoint?.stats.total_return ?? null,
      plateau_best_win_rate: plateauBestPoint?.stats.win_rate ?? null,
      sharpe: riskMetrics?.sharpe ?? null,
      sortino: riskMetrics?.sortino ?? null,
      calmar: riskMetrics?.calmar ?? null,
      expectancy: riskMetrics?.expectancy ?? null,
      stability_score: stability?.stability_score ?? null,
      monte_carlo_ruin_probability: monteCarlo?.ruin_probability ?? null,
      walk_forward_oos_pass_rate: walkForward?.oos_pass_rate ?? null,
    },
  ]
  const paramRows = [effectiveRunRequest]
  const noteRows = runResult.notes.map((item, index) => ({ index: index + 1, note: item }))
  const tradeRows = runResult.trades.map((row) => ({
    ...row,
    entry_signal: formatEventSequence(row.entry_signal),
    exit_date: formatTradeExitDate(row),
    exit_price: formatTradeExitPrice(row) === '--' ? '' : row.exit_price,
    exit_reason: formatTradeExitReasonLabel(row),
  }))
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(summaryRows), 'Summary')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(paramRows), 'Params')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(tradeRows), 'Trades')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(runResult.equity_curve), 'Equity')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(runResult.drawdown_curve), 'Drawdown')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(runResult.monthly_returns), 'Monthly')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(noteRows), 'Notes')
  if (riskMetrics) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet([riskMetrics]), 'RiskMetrics')
  }
  if (stability) {
    const stabilityRows = [
      {
        stability_score: stability.stability_score,
        min_trade_count_threshold: stability.min_trade_count_threshold,
        trade_count_penalty: stability.trade_count_penalty,
        neighborhood_consistency: stability.neighborhood_consistency,
        return_variance_penalty: stability.return_variance_penalty,
        monthly_return_std: stability.monthly_return_std,
      },
    ]
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(stabilityRows), 'Stability')
    if (stability.notes.length > 0) {
      XLSX.utils.book_append_sheet(
        workbook,
        XLSX.utils.json_to_sheet(stability.notes.map((note, index) => ({ index: index + 1, note }))),
        'StabilityNotes',
      )
    }
  }
  if (regimeBreakdown.length > 0) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(regimeBreakdown), 'Regimes')
  }
  if (monteCarlo) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet([monteCarlo]), 'MonteCarlo')
  }
  if (walkForward) {
    const walkSummary = [
      {
        fold_count: walkForward.fold_count,
        candidate_count: walkForward.candidate_count,
        oos_pass_rate: walkForward.oos_pass_rate,
        avg_test_return: walkForward.avg_test_return,
        avg_test_win_rate: walkForward.avg_test_win_rate,
      },
    ]
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(walkSummary), 'WalkForward')
    if (walkForward.folds.length > 0) {
      const walkFoldRows = walkForward.folds.map((fold) => ({
        fold_index: fold.fold_index,
        train_date_from: fold.train_date_from,
        train_date_to: fold.train_date_to,
        test_date_from: fold.test_date_from,
        test_date_to: fold.test_date_to,
        train_score: fold.train_score,
        test_score: fold.test_score,
        train_total_return: fold.train_stats.total_return,
        train_win_rate: fold.train_stats.win_rate,
        test_total_return: fold.test_stats.total_return,
        test_win_rate: fold.test_stats.win_rate,
        window_days: fold.selected_params.window_days,
        min_score: fold.selected_params.min_score,
        stop_loss: fold.selected_params.stop_loss,
        take_profit: fold.selected_params.take_profit,
        trailing_stop_pct: fold.selected_params.trailing_stop_pct,
        intraday_trailing_reduce_ratio: fold.selected_params.intraday_trailing_reduce_ratio,
        max_positions: fold.selected_params.max_positions,
        position_pct: fold.selected_params.position_pct,
        max_symbols: fold.selected_params.max_symbols,
        priority_topk_per_day: fold.selected_params.priority_topk_per_day,
      }))
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(walkFoldRows), 'WalkForwardFolds')
    }
    if (walkForward.notes.length > 0) {
      XLSX.utils.book_append_sheet(
        workbook,
        XLSX.utils.json_to_sheet(walkForward.notes.map((note, index) => ({ index: index + 1, note }))),
        'WalkForwardNotes',
      )
    }
  }
  if (plateauResult) {
    const plateauSummaryRows = [
      {
        total_combinations: plateauResult.total_combinations,
        evaluated_combinations: plateauResult.evaluated_combinations,
        generated_at: plateauResult.generated_at,
        best_score: plateauBestPoint?.score ?? null,
        best_total_return: plateauBestPoint?.stats.total_return ?? null,
        best_win_rate: plateauBestPoint?.stats.win_rate ?? null,
      },
    ]
    const plateauPointRows = (plateauResult.points ?? []).map((row, index) => ({
      rank: index + 1,
      score: row.score,
      total_return: row.stats.total_return,
      max_drawdown: row.stats.max_drawdown,
      win_rate: row.stats.win_rate,
      trade_count: row.stats.trade_count,
      window_days: row.params.window_days,
      min_score: row.params.min_score,
      stop_loss: row.params.stop_loss,
      take_profit: row.params.take_profit,
      trailing_stop_pct: row.params.trailing_stop_pct,
      intraday_trailing_reduce_ratio: row.params.intraday_trailing_reduce_ratio,
      max_positions: row.params.max_positions,
      position_pct: row.params.position_pct,
      max_symbols: row.params.max_symbols,
      priority_topk_per_day: row.params.priority_topk_per_day,
      candidate_count: row.candidate_count,
      skipped_count: row.skipped_count,
      fill_rate: row.fill_rate,
      max_concurrent_positions: row.max_concurrent_positions,
      cache_hit: row.cache_hit,
      error: row.error ?? '',
    }))
    const plateauCorrelationRows = plateauCorrelations.map((item) => ({
      parameter: item.parameter,
      parameter_label: item.parameter_label,
      score_corr: item.score_corr,
      score_direction: classifyCorrelationDirection(item.score_corr),
      total_return_corr: item.total_return_corr,
      total_return_direction: classifyCorrelationDirection(item.total_return_corr),
      win_rate_corr: item.win_rate_corr,
      win_rate_direction: classifyCorrelationDirection(item.win_rate_corr),
    }))
    const plateauNoteRows = (plateauResult.notes ?? []).map((item, index) => ({ index: index + 1, note: item }))
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(plateauSummaryRows), 'Plateau')
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(plateauPointRows), 'PlateauPoints')
    if (plateauCorrelationRows.length > 0) {
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(plateauCorrelationRows), 'PlateauCorr')
    }
    if (plateauNoteRows.length > 0) {
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(plateauNoteRows), 'PlateauNotes')
    }
  }
  return XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
}

function buildPlateauTradeExportWorkbookBuffer(
  runRequest: BacktestRunRequest,
  runResult: BacktestResponse,
  point: BacktestPlateauPoint,
  rank: number,
): ArrayBuffer {
  const workbook = XLSX.utils.book_new()
  const effectiveRunRequest = resolveEffectiveRunRequest(runRequest, runResult)
  const paramsSheet = [
    { 参数项: '参数组排名', 参数值: `第${rank}名` },
    { 参数项: '评分', 参数值: Number(point.score.toFixed(3)) },
    { 参数项: 'run_id', 参数值: effectiveRunRequest.run_id ?? '' },
    { 参数项: '回测模式', 参数值: effectiveRunRequest.mode },
    { 参数项: '趋势池步骤', 参数值: effectiveRunRequest.trend_step },
    { 参数项: '滚动方式', 参数值: effectiveRunRequest.pool_roll_mode },
    { 参数项: '策略ID', 参数值: effectiveRunRequest.strategy_id ?? '' },
    { 参数项: '策略参数', 参数值: JSON.stringify(effectiveRunRequest.strategy_params ?? {}, null, 2) },
    { 参数项: '开始日期', 参数值: effectiveRunRequest.date_from },
    { 参数项: '结束日期', 参数值: effectiveRunRequest.date_to },
    { 参数项: '窗口天数', 参数值: effectiveRunRequest.window_days },
    { 参数项: '最低分', 参数值: effectiveRunRequest.min_score },
    {
      参数项: '入场事件',
      参数值: formatConfiguredEventList(effectiveRunRequest.strategy_id, effectiveRunRequest.entry_events, 'entry'),
    },
    {
      参数项: '离场事件',
      参数值: formatConfiguredEventList(effectiveRunRequest.strategy_id, effectiveRunRequest.exit_events, 'exit'),
    },
    { 参数项: '最大持仓数', 参数值: effectiveRunRequest.max_positions },
    { 参数项: '单笔仓位(%)', 参数值: Number((effectiveRunRequest.position_pct * 100).toFixed(2)) },
    { 参数项: '止损(%)', 参数值: Number((effectiveRunRequest.stop_loss * 100).toFixed(2)) },
    { 参数项: '止盈(%)', 参数值: Number((effectiveRunRequest.take_profit * 100).toFixed(2)) },
    { 参数项: '高位回撤(%)', 参数值: Number(((effectiveRunRequest.trailing_stop_pct ?? 0) * 100).toFixed(2)) },
    { 参数项: '最大股票数', 参数值: effectiveRunRequest.max_symbols },
    { 参数项: 'TopK/日', 参数值: effectiveRunRequest.priority_topk_per_day },
    { 参数项: '执行路径偏好', 参数值: effectiveRunRequest.execution_path_preference ?? 'legacy' },
    { 参数项: '事件语义版本', 参数值: effectiveRunRequest.matrix_event_semantic_version ?? 'aligned_wyckoff_v2' },
  ]
  const performanceSheet = [
    {
      统计区间: `${runResult.range.date_from} ~ ${runResult.range.date_to}`,
      执行路径: runResult.execution_path ?? 'unknown',
      交易数: runResult.stats.trade_count,
      胜场: runResult.stats.win_count,
      负场: runResult.stats.loss_count,
      胜率百分比: Number((runResult.stats.win_rate * 100).toFixed(2)),
      总收益百分比: Number((runResult.stats.total_return * 100).toFixed(2)),
      最大回撤百分比: Number((runResult.stats.max_drawdown * 100).toFixed(2)),
      平均盈亏比百分比: Number((runResult.stats.avg_pnl_ratio * 100).toFixed(2)),
      盈亏比因子: Number(runResult.stats.profit_factor.toFixed(4)),
      候选股票数: runResult.candidate_count,
      跳过数: runResult.skipped_count,
      成交率百分比: Number((runResult.fill_rate * 100).toFixed(2)),
      最大并发持仓: runResult.max_concurrent_positions,
      夏普: Number((runResult.risk_metrics?.sharpe ?? 0).toFixed(4)),
      索提诺: Number((runResult.risk_metrics?.sortino ?? 0).toFixed(4)),
      卡玛: Number((runResult.risk_metrics?.calmar ?? 0).toFixed(4)),
      期望值: Number((runResult.risk_metrics?.expectancy ?? 0).toFixed(4)),
      稳定性评分: Number((runResult.stability_diagnostics?.stability_score ?? 0).toFixed(4)),
    },
  ]
  const tradeSheet = runResult.trades.map((row, index) => {
    return {
      序号: index + 1,
      股票代码: row.symbol,
      股票名称: row.name,
      信号日期: row.signal_date,
      入场日期: row.entry_date,
      离场日期: formatTradeExitDate(row),
      入场事件: formatEventSequence(row.entry_signal),
      入场阶段: row.entry_phase,
      离场原因: formatTradeExitReasonLabel(row),
      数量: row.quantity,
      买入价: row.entry_price,
      卖出价: formatTradeExitPrice(row) === '--' ? '' : row.exit_price,
      持仓天数: row.holding_days,
      盈亏额: row.pnl_amount,
      盈亏比百分比: Number((row.pnl_ratio * 100).toFixed(2)),
      质量分: Number((row.entry_quality_score ?? 0).toFixed(2)),
      健康分: Number((row.health_score ?? 0).toFixed(2)),
      事件分: Number((row.event_score ?? 0).toFixed(2)),
      风险分: Number((row.risk_score ?? 0).toFixed(2)),
      事件等级: row.event_grade ?? 'C',
      确认状态: row.confirmation_status ?? 'unconfirmed',
    }
  })
  const noteSheet = runResult.notes.map((note, index) => ({ 序号: index + 1, 说明: note }))
  const equitySheet = runResult.equity_curve.map((row) => ({
    日期: row.date,
    权益: row.equity,
    已实现盈亏: row.realized_pnl,
  }))
  const drawdownSheet = runResult.drawdown_curve.map((row) => ({
    日期: row.date,
    回撤百分比: Number((row.drawdown * 100).toFixed(2)),
  }))
  const monthlySheet = runResult.monthly_returns.map((row) => ({
    月份: row.month,
    收益百分比: Number((row.return_ratio * 100).toFixed(2)),
    盈亏额: row.pnl_amount,
    交易数: row.trade_count,
  }))

  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(paramsSheet), '参数快照')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(performanceSheet), '绩效指标')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(tradeSheet), '交易记录')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(equitySheet), '资金曲线')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(drawdownSheet), '回撤曲线')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(monthlySheet), '月度收益')
  if (noteSheet.length > 0) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(noteSheet), '备注')
  }
  return XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
}

function mapBacktestTradeToExportRow(row: BacktestTrade, index: number) {
  return {
    序号: index + 1,
    股票代码: row.symbol,
    股票名称: row.name,
    信号日期: row.signal_date,
    入场日期: row.entry_date,
    离场日期: formatTradeExitDate(row),
    入场事件: formatEventSequence(row.entry_signal),
    入场阶段: row.entry_phase,
    离场原因: formatTradeExitReasonLabel(row),
    数量: row.quantity,
    买入价: row.entry_price,
    卖出价: formatTradeExitPrice(row) === '--' ? '' : row.exit_price,
    持仓天数: row.holding_days,
    盈亏额: row.pnl_amount,
    盈亏比百分比: Number((row.pnl_ratio * 100).toFixed(2)),
    质量分: Number((row.entry_quality_score ?? 0).toFixed(2)),
    健康分: Number((row.health_score ?? 0).toFixed(2)),
    事件分: Number((row.event_score ?? 0).toFixed(2)),
    风险分: Number((row.risk_score ?? 0).toFixed(2)),
    事件等级: row.event_grade ?? 'C',
    确认状态: row.confirmation_status ?? 'unconfirmed',
  }
}

function buildTradesExportRows(trades: BacktestTrade[]) {
  return trades.map((row, index) => mapBacktestTradeToExportRow(row, index))
}

function buildTradesOnlyWorkbookBuffer(trades: BacktestTrade[]): ArrayBuffer {
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(buildTradesExportRows(trades)), '交易明细')
  return XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
}

function escapeCsvCell(value: unknown): string {
  const text = String(value ?? '')
  if (/[",\n\r]/.test(text)) {
    return `"${text.replaceAll('"', '""')}"`
  }
  return text
}

export function buildTradesOnlyCsv(trades: BacktestTrade[]): string {
  const rows = buildTradesExportRows(trades)
  if (rows.length <= 0) return ''
  const headers = Object.keys(rows[0]!)
  const lines = [headers.join(',')]
  for (const row of rows) {
    lines.push(headers.map((header) => escapeCsvCell((row as Record<string, unknown>)[header])).join(','))
  }
  return `\ufeff${lines.join('\n')}`
}

function buildTradesOnlyHtml(trades: BacktestTrade[], meta?: TradeExportMeta): string {
  const exportRows = buildTradesExportRows(trades)
  const headers = exportRows.length > 0
    ? Object.keys(exportRows[0]!)
    : ['序号', '股票代码', '股票名称', '信号日期', '入场日期', '离场日期', '入场事件', '入场阶段', '离场原因', '数量', '买入价', '卖出价', '持仓天数', '盈亏额', '盈亏比百分比']
  const headerHtml = headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')
  const bodyHtml = exportRows
    .map((row) => {
      const cells = headers.map((header) => `<td>${escapeHtml(String((row as Record<string, unknown>)[header] ?? ''))}</td>`).join('')
      return `<tr>${cells}</tr>`
    })
    .join('\n')
  const title = escapeHtml(meta?.title || '交易明细')
  const rangeText = meta?.date_from && meta?.date_to
    ? `<div>回测区间：${escapeHtml(meta.date_from)} ~ ${escapeHtml(meta.date_to)}</div>`
    : ''
  const taskText = meta?.task_id ? `<div>任务ID：${escapeHtml(meta.task_id)}</div>` : ''
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>${title}</title>
  <style>
    body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; color: #1f1f1f; }
    h1 { font-size: 20px; margin-bottom: 12px; }
    .meta { margin-bottom: 16px; color: #595959; line-height: 1.6; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; }
    th, td { border: 1px solid #d9d9d9; padding: 6px 8px; text-align: left; }
    th { background: #fafafa; }
  </style>
</head>
<body>
  <h1>${title}</h1>
  <div class="meta">
    ${rangeText}
    ${taskText}
    <div>交易笔数：${trades.length}</div>
  </div>
  <table>
    <thead><tr>${headerHtml}</tr></thead>
    <tbody>${bodyHtml}</tbody>
  </table>
</body>
</html>`
}

function buildTradeExportFileName(prefix: string, format: TradeExportFormat): string {
  const timestamp = dayjs().format('YYYYMMDD_HHmmss')
  const safePrefix = prefix.replace(/[^0-9A-Za-z_.-]/g, '_') || 'trades'
  const extension = format === 'excel' ? 'xlsx' : format
  return `${safePrefix}_${timestamp}.${extension}`
}

export function aggregateTradesBySymbol(trades: BacktestTrade[]): SymbolPnlAggregate[] {
  const grouped = new Map<string, SymbolPnlAggregate>()
  for (const trade of trades) {
    const symbol = String(trade.symbol || '').trim()
    if (!symbol) continue
    const existing = grouped.get(symbol)
    if (existing) {
      existing.pnl_amount += Number(trade.pnl_amount || 0)
      existing.trade_count += 1
      existing.trades.push(trade)
      if (!existing.name && trade.name) {
        existing.name = String(trade.name).trim()
      }
      continue
    }
    grouped.set(symbol, {
      symbol,
      name: String(trade.name || '').trim() || symbol,
      pnl_amount: Number(trade.pnl_amount || 0),
      trade_count: 1,
      trades: [trade],
    })
  }
  return Array.from(grouped.values()).sort((left, right) => Math.abs(right.pnl_amount) - Math.abs(left.pnl_amount))
}

function formatTradePnlTooltipContent(aggregate: SymbolPnlAggregate): string {
  const lines = [
    `${aggregate.name} (${aggregate.symbol})`,
    `累计盈亏：${formatMoney(aggregate.pnl_amount)}`,
    `交易笔数：${aggregate.trade_count}`,
    '---',
  ]
  aggregate.trades.forEach((trade, index) => {
    lines.push(
      `#${index + 1} 买入 ${trade.entry_date} @${trade.entry_price} → 卖出 ${formatTradeExitDate(trade)} @${formatTradeExitPrice(trade)}`,
      `   盈亏 ${formatMoney(trade.pnl_amount)} (${formatPct(trade.pnl_ratio)})`,
    )
  })
  return lines.join('<br/>')
}

export function buildTradePnlChartOption(aggregates: SymbolPnlAggregate[], chartType: TradePnlChartType) {
  const itemTooltipFormatter = (params: unknown) => {
    const raw = Array.isArray(params) ? params[0] : params
    const aggregate = (raw as { data?: { aggregate?: SymbolPnlAggregate } })?.data?.aggregate
    if (!aggregate) return ''
    return formatTradePnlTooltipContent(aggregate)
  }

  if (chartType === 'pie') {
    return {
      tooltip: {
        trigger: 'item',
        enterable: true,
        confine: true,
        formatter: itemTooltipFormatter,
      },
      legend: {
        type: 'scroll',
        bottom: 0,
      },
      series: [
        {
          type: 'pie',
          radius: ['32%', '68%'],
          data: aggregates.map((item) => ({
            name: `${item.name} (${item.symbol})`,
            value: Math.max(Math.abs(item.pnl_amount), 1e-6),
            itemStyle: {
              color: item.pnl_amount >= 0 ? TRADE_PNL_PROFIT_COLOR : TRADE_PNL_LOSS_COLOR,
            },
            aggregate: item,
          })),
        },
      ],
    }
  }

  if (chartType === 'treemap') {
    return {
      tooltip: {
        trigger: 'item',
        enterable: true,
        confine: true,
        formatter: itemTooltipFormatter,
      },
      series: [
        {
          type: 'treemap',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            show: true,
            formatter: '{b}',
          },
          data: aggregates.map((item) => ({
            name: `${item.name}\n${item.symbol}`,
            value: Math.max(Math.abs(item.pnl_amount), 1e-6),
            itemStyle: {
              color: item.pnl_amount >= 0 ? TRADE_PNL_PROFIT_COLOR : TRADE_PNL_LOSS_COLOR,
            },
            aggregate: item,
          })),
        },
      ],
    }
  }

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      enterable: true,
      confine: true,
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params]
        const first = list[0] as { data?: { aggregate?: SymbolPnlAggregate }; dataIndex?: number }
        const aggregate = first?.data?.aggregate ?? aggregates[first?.dataIndex ?? -1]
        if (!aggregate) return ''
        return formatTradePnlTooltipContent(aggregate)
      },
    },
    xAxis: {
      type: 'category',
      data: aggregates.map((item) => item.symbol),
      axisLabel: { rotate: 45 },
    },
    yAxis: {
      type: 'value',
      name: '盈亏额',
    },
    grid: { left: 56, right: 20, top: 24, bottom: 72 },
    series: [
      {
        type: 'bar',
        data: aggregates.map((item) => ({
          value: item.pnl_amount,
          itemStyle: {
            color: item.pnl_amount >= 0 ? TRADE_PNL_PROFIT_COLOR : TRADE_PNL_LOSS_COLOR,
          },
          aggregate: item,
        })),
      },
    ],
  }
}

function buildBacktestReportHtml(
  runRequest: BacktestRunRequest,
  runResult: BacktestResponse,
  plateauResult?: BacktestPlateauResponse | null,
): string {
  const effectiveRunRequest = resolveEffectiveRunRequest(runRequest, runResult)
  const rows = runResult.trades
    .map((trade) => {
      const parsedExit = parseExitReason(trade.exit_reason)
      const exitDisplay = isBacktestOpenHolding(trade) ? '--' : (parsedExit.detail ? `${parsedExit.label}:${parsedExit.detail}` : parsedExit.label)
      return `<tr>
        <td>${escapeHtml(trade.symbol)}</td>
        <td>${escapeHtml(trade.name)}</td>
        <td>${escapeHtml(trade.signal_date)}</td>
        <td>${escapeHtml(trade.entry_date)}</td>
        <td>${escapeHtml(formatTradeExitDate(trade))}</td>
        <td>${escapeHtml(formatEventSequence(trade.entry_signal))}</td>
        <td>${Number(trade.candle_quality_score ?? 0).toFixed(2)}</td>
        <td>${Number(trade.cost_center_shift_score ?? 0).toFixed(2)}</td>
        <td>${Number(trade.weekly_context_score ?? 0).toFixed(2)}</td>
        <td>${Number(trade.weekly_context_multiplier ?? 1).toFixed(4)}</td>
        <td>${Number(trade.health_score ?? 0).toFixed(2)}</td>
        <td>${Number(trade.event_score ?? 0).toFixed(2)}</td>
        <td>${Number(trade.risk_score ?? 0).toFixed(2)}</td>
        <td>${escapeHtml(String(trade.confirmation_status ?? 'unconfirmed'))}</td>
        <td>${Number(trade.delay_entry_days ?? 1)}</td>
        <td>${Number(trade.delay_window_days ?? 0)}</td>
        <td>${escapeHtml(exitDisplay)}</td>
        <td>${trade.quantity}</td>
        <td>${trade.entry_price}</td>
        <td>${formatTradeExitPrice(trade)}</td>
        <td>${trade.holding_days}</td>
        <td>${trade.pnl_amount}</td>
        <td>${trade.pnl_ratio}</td>
      </tr>`
    })
    .join('\n')
  const noteList = runResult.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join('\n')
  const plateauCorrelations = resolvePlateauCorrelations(plateauResult)
  const plateauValidPoints = (plateauResult?.points ?? []).filter((row) => !row.error)
  const plateauPointRows = plateauValidPoints
    .slice(0, 100)
    .map((row, index) => (
      `<tr>
        <td>${index + 1}</td>
        <td>${row.score.toFixed(3)}</td>
        <td>${(Number(row.stats.total_return) * 100).toFixed(2)}%</td>
        <td>${(Number(row.stats.win_rate) * 100).toFixed(2)}%</td>
        <td>${row.params.window_days}</td>
        <td>${Number(row.params.min_score).toFixed(2)}</td>
        <td>${(Number(row.params.stop_loss) * 100).toFixed(2)}%</td>
        <td>${(Number(row.params.take_profit) * 100).toFixed(2)}%</td>
        <td>${(Number(row.params.trailing_stop_pct) * 100).toFixed(2)}%</td>
      </tr>`
    ))
    .join('\n')
  const plateauCorrelationRows = plateauCorrelations
    .map((item) => (
      `<tr>
        <td>${escapeHtml(item.parameter_label)}</td>
        <td>${formatSigned(item.score_corr)} (${classifyCorrelationDirection(item.score_corr)})</td>
        <td>${formatSigned(item.total_return_corr)} (${classifyCorrelationDirection(item.total_return_corr)})</td>
        <td>${formatSigned(item.win_rate_corr)} (${classifyCorrelationDirection(item.win_rate_corr)})</td>
      </tr>`
    ))
    .join('\n')
  const plateauNoteList = (plateauResult?.notes ?? []).map((note) => `<li>${escapeHtml(note)}</li>`).join('\n')
  const riskMetrics = runResult.risk_metrics ?? null
  const stability = runResult.stability_diagnostics ?? null
  const regimeRows = (runResult.regime_breakdown ?? [])
    .map((item) => (
      `<tr>
        <td>${escapeHtml(item.label)}</td>
        <td>${item.trade_count}</td>
        <td>${(Number(item.win_rate) * 100).toFixed(2)}%</td>
        <td>${(Number(item.total_return) * 100).toFixed(2)}%</td>
        <td>${(Number(item.avg_pnl_ratio) * 100).toFixed(2)}%</td>
        <td>${(Number(item.max_drawdown) * 100).toFixed(2)}%</td>
      </tr>`
    ))
    .join('\n')
  const monteCarlo = runResult.monte_carlo ?? null
  const walkForward = runResult.walk_forward ?? null
  const walkForwardRows = (walkForward?.folds ?? [])
    .map((fold) => (
      `<tr>
        <td>${fold.fold_index}</td>
        <td>${escapeHtml(fold.train_date_from)}~${escapeHtml(fold.train_date_to)}</td>
        <td>${escapeHtml(fold.test_date_from)}~${escapeHtml(fold.test_date_to)}</td>
        <td>${formatSigned(fold.train_score)}</td>
        <td>${formatSigned(fold.test_score)}</td>
        <td>${(Number(fold.test_stats.total_return) * 100).toFixed(2)}%</td>
        <td>${(Number(fold.test_stats.win_rate) * 100).toFixed(2)}%</td>
      </tr>`
    ))
    .join('\n')
  const walkForwardNotes = (walkForward?.notes ?? []).map((note) => `<li>${escapeHtml(note)}</li>`).join('\n')
  const generatedAt = dayjs().format('YYYY-MM-DD HH:mm:ss')
  const executionPathText =
    runResult.execution_path === 'matrix'
      ? 'matrix'
      : runResult.execution_path === 'legacy'
        ? 'legacy'
        : 'unknown'
  const strategyIdText = String(runResult.strategy_id || effectiveRunRequest.strategy_id || '').trim() || 'unknown'
  const strategyVersionText = String(runResult.strategy_version || '').trim() || 'unknown'
  const strategyParamsHashText = String(runResult.strategy_params_hash || '').trim() || 'unknown'
  const advancedSection = `
  <h2>高级分析</h2>
  ${riskMetrics
    ? `<div class="meta">
    <div>Sharpe：${Number(riskMetrics.sharpe).toFixed(3)}</div>
    <div>Sortino：${Number(riskMetrics.sortino).toFixed(3)}</div>
    <div>Calmar：${Number(riskMetrics.calmar).toFixed(3)}</div>
    <div>Expectancy：${(Number(riskMetrics.expectancy) * 100).toFixed(2)}%</div>
    <div>最大连续亏损：${riskMetrics.max_consecutive_losses}</div>
    <div>回撤恢复天数：${riskMetrics.recovery_days}</div>
  </div>`
    : '<div>无风险指标。</div>'}
  ${stability
    ? `<h2>稳定性评分</h2>
  <div class="meta">
    <div>稳定性评分：${Number(stability.stability_score).toFixed(3)}</div>
    <div>邻域一致性：${Number(stability.neighborhood_consistency).toFixed(3)}</div>
    <div>交易数惩罚：${Number(stability.trade_count_penalty).toFixed(3)}</div>
    <div>方差惩罚：${Number(stability.return_variance_penalty).toFixed(3)}</div>
    <div>月度收益标准差：${Number(stability.monthly_return_std).toFixed(4)}</div>
  </div>`
    : ''}
  ${(stability?.notes?.length ?? 0) > 0 ? `<ul>${(stability?.notes ?? []).map((note) => `<li>${escapeHtml(note)}</li>`).join('\n')}</ul>` : ''}
  <h2>状态拆分（代理）</h2>
  ${regimeRows
    ? `<table>
    <thead>
      <tr>
        <th>状态</th><th>交易数</th><th>胜率</th><th>总收益</th><th>平均单笔</th><th>最大回撤</th>
      </tr>
    </thead>
    <tbody>${regimeRows}</tbody>
  </table>`
    : '<div>无状态拆分数据。</div>'}
  <h2>蒙特卡洛压力测试</h2>
  ${monteCarlo
    ? `<div class="meta">
    <div>模拟次数：${monteCarlo.simulations}</div>
    <div>收益 P5/P50/P95：${(Number(monteCarlo.total_return_p5) * 100).toFixed(2)}% / ${(Number(monteCarlo.total_return_p50) * 100).toFixed(2)}% / ${(Number(monteCarlo.total_return_p95) * 100).toFixed(2)}%</div>
    <div>回撤 P5/P50/P95：${(Number(monteCarlo.max_drawdown_p5) * 100).toFixed(2)}% / ${(Number(monteCarlo.max_drawdown_p50) * 100).toFixed(2)}% / ${(Number(monteCarlo.max_drawdown_p95) * 100).toFixed(2)}%</div>
    <div>极端亏损概率：${(Number(monteCarlo.ruin_probability) * 100).toFixed(2)}%</div>
  </div>`
    : '<div>无蒙特卡洛数据。</div>'}
  <h2>Walk-forward</h2>
  ${walkForward
    ? `<div class="meta">
    <div>折叠数：${walkForward.fold_count}</div>
    <div>候选参数数：${walkForward.candidate_count}</div>
    <div>OOS 通过率：${(Number(walkForward.oos_pass_rate) * 100).toFixed(2)}%</div>
    <div>测试平均收益：${(Number(walkForward.avg_test_return) * 100).toFixed(2)}%</div>
    <div>测试平均胜率：${(Number(walkForward.avg_test_win_rate) * 100).toFixed(2)}%</div>
  </div>`
    : '<div>无 walk-forward 数据。</div>'}
  ${walkForwardRows
    ? `<table>
    <thead>
      <tr>
        <th>fold</th><th>train</th><th>test</th><th>train_score</th><th>test_score</th><th>test_return</th><th>test_win_rate</th>
      </tr>
    </thead>
    <tbody>${walkForwardRows}</tbody>
  </table>`
    : ''}
  ${(walkForward?.notes?.length ?? 0) > 0 ? `<ul>${walkForwardNotes || '<li>无</li>'}</ul>` : ''}`
  const plateauSection = plateauResult
    ? `
  <h2>收益平原概览</h2>
  <div class="meta">
    <div>总组合：${plateauResult.total_combinations}</div>
    <div>评估组数：${plateauResult.evaluated_combinations}</div>
    <div>最佳评分：${plateauResult.best_point ? plateauResult.best_point.score.toFixed(3) : '--'}</div>
    <div>最佳收益：${plateauResult.best_point ? `${(Number(plateauResult.best_point.stats.total_return) * 100).toFixed(2)}%` : '--'}</div>
    <div>最佳胜率：${plateauResult.best_point ? `${(Number(plateauResult.best_point.stats.win_rate) * 100).toFixed(2)}%` : '--'}</div>
    <div>生成时间：${escapeHtml(plateauResult.generated_at)}</div>
  </div>
  <h2>参数相关性分析</h2>
  ${plateauCorrelationRows
    ? `<table>
    <thead>
      <tr>
        <th>参数</th><th>与评分相关</th><th>与收益相关</th><th>与胜率相关</th>
      </tr>
    </thead>
    <tbody>${plateauCorrelationRows}</tbody>
  </table>`
    : '<div>有效参数组不足，暂无相关性分析。</div>'}
  <h2>收益平原说明</h2>
  <ul>${plateauNoteList || '<li>无</li>'}</ul>
  <h2>收益平原Top100（成功参数）</h2>
  <table>
    <thead>
      <tr>
        <th>rank</th><th>score</th><th>total_return</th><th>win_rate</th><th>window_days</th>
        <th>min_score</th><th>stop_loss</th><th>take_profit</th><th>trailing_stop_pct</th>
      </tr>
    </thead>
    <tbody>${plateauPointRows || '<tr><td colspan="9">无</td></tr>'}</tbody>
  </table>
  <div class="block">注：仅展示成功参数的前100名，完整数据见 report.xlsx 的 PlateauPoints 工作表。</div>`
    : ''
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Backtest Report ${escapeHtml(runResult.range.date_from)} - ${escapeHtml(runResult.range.date_to)}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; margin: 24px; color: #0f172a; }
    h1, h2 { margin: 8px 0; }
    .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; margin-bottom: 16px; }
    .block { margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }
    th { background: #f8fafc; }
  </style>
</head>
<body>
  <h1>策略回测报告</h1>
  <div class="block">生成时间：${escapeHtml(generatedAt)}</div>
  <div class="meta">
    <div>区间：${escapeHtml(runResult.range.date_from)} ~ ${escapeHtml(runResult.range.date_to)}</div>
    <div>模式：${escapeHtml(effectiveRunRequest.mode)} / ${escapeHtml(effectiveRunRequest.pool_roll_mode)}</div>
    <div>执行路径：${escapeHtml(executionPathText)}</div>
    <div>策略：${escapeHtml(strategyIdText)} @ ${escapeHtml(strategyVersionText)}</div>
    <div>参数哈希：${escapeHtml(strategyParamsHashText)}</div>
    <div>延迟入场：T+${Math.max(1, Number(effectiveRunRequest.entry_delay_days ?? 1))}</div>
    <div>延迟窗口失效保护：${effectiveRunRequest.delay_invalidation_enabled ? 'on' : 'off'}</div>
    <div>交易数：${runResult.stats.trade_count}</div>
    <div>胜率：${runResult.stats.win_rate}</div>
    <div>总收益：${runResult.stats.total_return}</div>
    <div>最大回撤：${runResult.stats.max_drawdown}</div>
    <div>成交率：${runResult.fill_rate}</div>
    <div>候选信号：${runResult.candidate_count}</div>
  </div>
  <h2>运行说明</h2>
  <ul>${noteList || '<li>无</li>'}</ul>
  ${advancedSection}
  ${plateauSection}
  <h2>交易明细</h2>
  <table>
    <thead>
      <tr>
        <th>symbol</th><th>name</th><th>signal_date</th><th>entry_date</th><th>exit_date</th>
        <th>entry_signal</th><th>candle_q</th><th>cost_shift</th><th>weekly_ctx</th><th>weekly_mult</th><th>health</th><th>event</th><th>risk</th><th>confirm</th><th>delay_days</th><th>delay_window</th>
        <th>exit_reason</th><th>quantity</th><th>entry_price</th><th>exit_price</th>
        <th>holding_days</th><th>pnl_amount</th><th>pnl_ratio</th>
      </tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>
</body>
</html>`
}

function buildRunRequestFromPlateauParams(
  basePayload: BacktestRunRequest,
  params: BacktestPlateauParams,
): BacktestRunRequest {
  return {
    ...basePayload,
    window_days: Number(params.window_days),
    min_score: Number(params.min_score),
    stop_loss: Number(params.stop_loss),
    take_profit: Number(params.take_profit),
    trailing_stop_pct: Number(params.trailing_stop_pct),
    intraday_trailing_reduce_ratio: Number(params.intraday_trailing_reduce_ratio),
    max_positions: Number(params.max_positions),
    position_pct: Number(params.position_pct),
    max_symbols: Number(params.max_symbols),
    priority_topk_per_day: Number(params.priority_topk_per_day),
    enable_advanced_analysis: false,
  }
}

function buildPlateauHistoryWorkbookBuffer(
  taskId: string,
  plateauResult: BacktestPlateauResponse,
): ArrayBuffer {
  const workbook = XLSX.utils.book_new()
  const bestPoint = plateauRecommendation(plateauResult)
  const peakPoint = plateauResult.peak_point ?? null
  const regions = plateauResult.regions ?? []
  const correlations = resolvePlateauCorrelations(plateauResult)
  const overviewRows = [
    {
      task_id: taskId,
      generated_at: plateauResult.generated_at,
      total_combinations: plateauResult.total_combinations,
      evaluated_combinations: plateauResult.evaluated_combinations,
      recommended_plateau_score: bestPoint?.plateau_score ?? null,
      recommended_local_score: bestPoint?.local_score ?? null,
      recommended_total_return: bestPoint?.stats.total_return ?? null,
      recommended_max_drawdown: bestPoint?.stats.max_drawdown ?? null,
      recommended_win_rate: bestPoint?.stats.win_rate ?? null,
      recommended_trade_count: bestPoint?.stats.trade_count ?? null,
      peak_score: peakPoint?.score ?? null,
      peak_total_return: peakPoint?.stats.total_return ?? null,
      region_count: regions.length,
      points_count: plateauResult.points.length,
      note_count: plateauResult.notes.length,
    },
  ]
  const pointsRows = plateauResult.points.map((row, index) => ({
    rank: index + 1,
    status: row.error ? 'failed' : 'succeeded',
    error: row.error ?? '',
    plateau_score: row.plateau_score,
    local_score: row.local_score,
    point_score: row.point_score,
    score: row.score,
    total_return: row.stats.total_return,
    max_drawdown: row.stats.max_drawdown,
    win_rate: row.stats.win_rate,
    trade_count: row.stats.trade_count,
    annual_trades: row.annual_trades,
    candidate_count: row.candidate_count,
    skipped_count: row.skipped_count,
    fill_rate: row.fill_rate,
    max_concurrent_positions: row.max_concurrent_positions,
    neighbor_pass_rate: row.neighbor_pass_rate,
    neighbor_median_score: row.neighbor_median_score,
    neighbor_p25_score: row.neighbor_p25_score,
    sensitivity_penalty: row.sensitivity_penalty,
    passes_hard_filters: row.passes_hard_filters,
    hard_filter_failures: (row.hard_filter_failures ?? []).join(' | '),
    region_id: row.region_id ?? '',
    region_rank: row.region_rank ?? null,
    cache_hit: row.cache_hit,
    window_days: row.params.window_days,
    min_score: row.params.min_score,
    stop_loss: row.params.stop_loss,
    take_profit: row.params.take_profit,
    trailing_stop_pct: row.params.trailing_stop_pct,
    intraday_trailing_reduce_ratio: row.params.intraday_trailing_reduce_ratio,
    max_positions: row.params.max_positions,
    position_pct: row.params.position_pct,
    max_symbols: row.params.max_symbols,
    priority_topk_per_day: row.params.priority_topk_per_day,
  }))
  const correlationRows = correlations.map((item) => ({
    parameter: item.parameter,
    parameter_label: item.parameter_label,
    score_corr: item.score_corr,
    score_direction: classifyCorrelationDirection(item.score_corr),
    total_return_corr: item.total_return_corr,
    total_return_direction: classifyCorrelationDirection(item.total_return_corr),
    win_rate_corr: item.win_rate_corr,
    win_rate_direction: classifyCorrelationDirection(item.win_rate_corr),
  }))
  const regionRows = regions.map((region) => ({
    region_id: region.region_id,
    region_rank: region.region_rank,
    point_count: region.point_count,
    region_score: region.region_score,
    median_local_score: region.median_local_score,
    p25_local_score: region.p25_local_score,
    median_point_score: region.median_point_score,
    median_total_return: region.median_total_return,
    best_total_return: region.best_total_return,
    center_margin_score: region.center_margin_score,
    size_score: region.size_score,
    oos_pass_rate: region.oos_pass_rate ?? null,
    center_plateau_score: region.center_point.plateau_score,
    center_total_return: region.center_point.stats.total_return,
    center_max_drawdown: region.center_point.stats.max_drawdown,
    center_params: JSON.stringify(region.center_point.params),
    parameter_ranges: JSON.stringify(region.parameter_ranges),
  }))
  const noteRows = plateauResult.notes.map((note, index) => ({ index: index + 1, note }))
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(overviewRows), 'PlateauSummary')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet([plateauResult.base_payload]), 'BasePayload')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(pointsRows), 'PlateauPoints')
  if (regionRows.length > 0) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(regionRows), 'PlateauRegions')
  }
  if (correlationRows.length > 0) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(correlationRows), 'PlateauCorr')
  }
  if (noteRows.length > 0) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(noteRows), 'PlateauNotes')
  }
  return XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
}

function buildPlateauHistoryHtml(taskId: string, plateauResult: BacktestPlateauResponse): string {
  const bestPoint = plateauRecommendation(plateauResult)
  const correlations = resolvePlateauCorrelations(plateauResult)
  const pointRows = plateauResult.points
    .map((row, index) => (
      `<tr>
        <td>${index + 1}</td>
        <td>${row.error ? 'failed' : 'succeeded'}</td>
        <td>${row.error ? escapeHtml(row.error) : '-'}</td>
        <td>${Number(row.score).toFixed(3)}</td>
        <td>${(Number(row.stats.total_return) * 100).toFixed(2)}%</td>
        <td>${(Number(row.stats.max_drawdown) * 100).toFixed(2)}%</td>
        <td>${(Number(row.stats.win_rate) * 100).toFixed(2)}%</td>
        <td>${row.stats.trade_count}</td>
        <td>${row.params.window_days}</td>
        <td>${Number(row.params.min_score).toFixed(2)}</td>
        <td>${(Number(row.params.stop_loss) * 100).toFixed(2)}%</td>
        <td>${(Number(row.params.take_profit) * 100).toFixed(2)}%</td>
        <td>${(Number(row.params.trailing_stop_pct) * 100).toFixed(2)}%</td>
      </tr>`
    ))
    .join('\n')
  const correlationRows = correlations
    .map((item) => (
      `<tr>
        <td>${escapeHtml(item.parameter_label)}</td>
        <td>${formatSigned(item.score_corr)} (${classifyCorrelationDirection(item.score_corr)})</td>
        <td>${formatSigned(item.total_return_corr)} (${classifyCorrelationDirection(item.total_return_corr)})</td>
        <td>${formatSigned(item.win_rate_corr)} (${classifyCorrelationDirection(item.win_rate_corr)})</td>
      </tr>`
    ))
    .join('\n')
  const notes = plateauResult.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join('\n')
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Plateau Report ${escapeHtml(taskId)}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; margin: 24px; color: #0f172a; }
    h1, h2 { margin: 8px 0; }
    .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; margin-bottom: 16px; }
    .block { margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }
    th { background: #f8fafc; }
  </style>
</head>
<body>
  <h1>收益平原历史报告</h1>
  <div class="block">任务ID：${escapeHtml(taskId)}</div>
  <div class="meta">
    <div>生成时间：${escapeHtml(plateauResult.generated_at)}</div>
    <div>总组合：${plateauResult.total_combinations}</div>
    <div>评估组数：${plateauResult.evaluated_combinations}</div>
    <div>最佳评分：${bestPoint ? bestPoint.score.toFixed(3) : '--'}</div>
    <div>最佳收益：${bestPoint ? `${(Number(bestPoint.stats.total_return) * 100).toFixed(2)}%` : '--'}</div>
    <div>最佳胜率：${bestPoint ? `${(Number(bestPoint.stats.win_rate) * 100).toFixed(2)}%` : '--'}</div>
  </div>
  <h2>基础参数快照</h2>
  <pre>${escapeHtml(JSON.stringify(plateauResult.base_payload, null, 2))}</pre>
  <h2>参数相关性分析</h2>
  ${correlationRows
    ? `<table>
      <thead>
        <tr><th>参数</th><th>与评分相关</th><th>与收益相关</th><th>与胜率相关</th></tr>
      </thead>
      <tbody>${correlationRows}</tbody>
    </table>`
    : '<div>有效参数组不足，暂无相关性分析。</div>'}
  <h2>收益平原说明</h2>
  <ul>${notes || '<li>无</li>'}</ul>
  <h2>参数组明细</h2>
  <table>
    <thead>
      <tr>
        <th>rank</th><th>status</th><th>error</th><th>score</th><th>total_return</th><th>max_drawdown</th><th>win_rate</th><th>trade_count</th>
        <th>window_days</th><th>min_score</th><th>stop_loss</th><th>take_profit</th><th>trailing_stop_pct</th>
      </tr>
    </thead>
    <tbody>${pointRows || '<tr><td colspan="13">无</td></tr>'}</tbody>
  </table>
</body>
</html>`
}

function buildPlateauAllTradesWorkbookBuffer(
  taskId: string,
  plateauResult: BacktestPlateauResponse,
  pointRuns: PlateauPointRunRecord[],
): ArrayBuffer {
  const workbook = XLSX.utils.book_new()
  const bestPoint = plateauRecommendation(plateauResult)
  const overviewRows = [
    {
      task_id: taskId,
      generated_at: plateauResult.generated_at,
      total_combinations: plateauResult.total_combinations,
      evaluated_combinations: plateauResult.evaluated_combinations,
      replay_total_points: pointRuns.length,
      replay_success_points: pointRuns.filter((item) => item.runResult).length,
      replay_failed_points: pointRuns.filter((item) => !item.runResult).length,
      best_score: bestPoint?.score ?? null,
      best_total_return: bestPoint?.stats.total_return ?? null,
      best_win_rate: bestPoint?.stats.win_rate ?? null,
    },
  ]
  const runSummaryRows = pointRuns.map((item) => ({
    rank: item.rank,
    status: item.runResult ? 'succeeded' : 'failed',
    error: item.error ?? '',
    score: item.point.score,
    total_return: item.runResult?.stats.total_return ?? item.point.stats.total_return,
    max_drawdown: item.runResult?.stats.max_drawdown ?? item.point.stats.max_drawdown,
    win_rate: item.runResult?.stats.win_rate ?? item.point.stats.win_rate,
    trade_count: item.runResult?.stats.trade_count ?? item.point.stats.trade_count,
    window_days: item.point.params.window_days,
    min_score: item.point.params.min_score,
    stop_loss: item.point.params.stop_loss,
    take_profit: item.point.params.take_profit,
    trailing_stop_pct: item.point.params.trailing_stop_pct,
    intraday_trailing_reduce_ratio: item.point.params.intraday_trailing_reduce_ratio,
    max_positions: item.point.params.max_positions,
    position_pct: item.point.params.position_pct,
    max_symbols: item.point.params.max_symbols,
    priority_topk_per_day: item.point.params.priority_topk_per_day,
  }))
  const allTradeRows = pointRuns.flatMap((item) => {
    if (!item.runResult) return []
    return item.runResult.trades.map((trade, index) => ({
      rank: item.rank,
        score: Number(item.point.score.toFixed(3)),
        trade_index: index + 1,
        symbol: trade.symbol,
        name: trade.name,
        signal_date: trade.signal_date,
        entry_date: trade.entry_date,
        exit_date: formatTradeExitDate(trade),
        entry_signal: formatEventSequence(trade.entry_signal),
        entry_phase: trade.entry_phase,
        exit_reason: formatTradeExitReasonLabel(trade),
        quantity: trade.quantity,
        entry_price: trade.entry_price,
        exit_price: formatTradeExitPrice(trade) === '--' ? '' : trade.exit_price,
        holding_days: trade.holding_days,
        pnl_amount: trade.pnl_amount,
        pnl_ratio: trade.pnl_ratio,
        health_score: trade.health_score ?? 0,
        event_score: trade.event_score ?? 0,
        risk_score: trade.risk_score ?? 0,
        confirmation_status: trade.confirmation_status ?? 'unconfirmed',
      }))
  })
  const allEquityRows = pointRuns.flatMap((item) => {
    if (!item.runResult) return []
    return item.runResult.equity_curve.map((row) => ({
      rank: item.rank,
      date: row.date,
      equity: row.equity,
      realized_pnl: row.realized_pnl,
    }))
  })
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(overviewRows), 'PlateauSummary')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet([plateauResult.base_payload]), 'BasePayload')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(runSummaryRows), 'PointRuns')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(allTradeRows), 'AllTrades')
  if (allEquityRows.length > 0) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(allEquityRows), 'AllEquity')
  }
  return XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
}

function buildPlateauAllTradesHtml(
  taskId: string,
  plateauResult: BacktestPlateauResponse,
  pointRuns: PlateauPointRunRecord[],
): string {
  const replaySuccessCount = pointRuns.filter((item) => item.runResult).length
  const replayFailedCount = pointRuns.length - replaySuccessCount
  const runSummaryRows = pointRuns
    .map((item) => (
      `<tr>
        <td>${item.rank}</td>
        <td>${item.runResult ? 'succeeded' : 'failed'}</td>
        <td>${item.error ? escapeHtml(item.error) : '-'}</td>
        <td>${item.point.score.toFixed(3)}</td>
        <td>${((item.runResult?.stats.total_return ?? item.point.stats.total_return) * 100).toFixed(2)}%</td>
        <td>${((item.runResult?.stats.max_drawdown ?? item.point.stats.max_drawdown) * 100).toFixed(2)}%</td>
        <td>${((item.runResult?.stats.win_rate ?? item.point.stats.win_rate) * 100).toFixed(2)}%</td>
        <td>${item.runResult?.stats.trade_count ?? item.point.stats.trade_count}</td>
      </tr>`
    ))
    .join('\n')
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Plateau ftbt ${escapeHtml(taskId)}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; margin: 24px; color: #0f172a; }
    h1, h2 { margin: 8px 0; }
    .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }
    th { background: #f8fafc; }
  </style>
</head>
<body>
  <h1>收益平原 ftbt 导出</h1>
  <div class="meta">
    <div>任务ID：${escapeHtml(taskId)}</div>
    <div>生成时间：${escapeHtml(plateauResult.generated_at)}</div>
    <div>总组合：${plateauResult.total_combinations}</div>
    <div>评估组数：${plateauResult.evaluated_combinations}</div>
    <div>回放成功：${replaySuccessCount}</div>
    <div>回放失败：${replayFailedCount}</div>
  </div>
  <h2>参数组回放摘要</h2>
  <table>
    <thead>
      <tr><th>rank</th><th>status</th><th>error</th><th>score</th><th>total_return</th><th>max_drawdown</th><th>win_rate</th><th>trade_count</th></tr>
    </thead>
    <tbody>${runSummaryRows || '<tr><td colspan="8">无</td></tr>'}</tbody>
  </table>
  <h2>说明</h2>
  <ul>
    <li>全部参数组交易记录已写入 report.xlsx 的 AllTrades 工作表。</li>
    <li>全部参数组资金曲线已写入 report.xlsx 的 AllEquity 工作表（若有）。</li>
  </ul>
</body>
</html>`
}

function buildPlateauRowKey(row: BacktestPlateauPoint, index: number) {
  const params = row.params
  return [
    params.window_days,
    Number(params.min_score).toFixed(4),
    Number(params.stop_loss).toFixed(6),
    Number(params.take_profit).toFixed(6),
    Number(params.trailing_stop_pct).toFixed(6),
    Number(params.intraday_trailing_reduce_ratio).toFixed(6),
    params.max_positions,
    Number(params.position_pct).toFixed(6),
    params.max_symbols,
    params.priority_topk_per_day,
    index,
  ].join('|')
}

function buildBacktestRunRequestKey(runRequest: BacktestRunRequest): string {
  return [
    String(runRequest.strategy_id || '').trim(),
    runRequest.mode,
    runRequest.pool_roll_mode,
    runRequest.date_from,
    runRequest.date_to,
    runRequest.window_days,
    Number(runRequest.min_score).toFixed(4),
    Number(runRequest.stop_loss).toFixed(6),
    Number(runRequest.take_profit).toFixed(6),
    Number(runRequest.trailing_stop_pct ?? 0).toFixed(6),
    Number(runRequest.intraday_trailing_reduce_ratio ?? 0).toFixed(6),
    runRequest.max_positions,
    Number(runRequest.position_pct).toFixed(6),
    runRequest.max_symbols,
    runRequest.priority_topk_per_day,
    Number(runRequest.initial_capital).toFixed(2),
    Number(runRequest.fee_bps).toFixed(2),
    runRequest.max_hold_days,
  ].join('|')
}

function buildPlateauParamsKey(point: BacktestPlateauPoint): string {
  const params = point.params
  return [
    params.window_days,
    Number(params.min_score).toFixed(4),
    Number(params.stop_loss).toFixed(6),
    Number(params.take_profit).toFixed(6),
    Number(params.trailing_stop_pct).toFixed(6),
    Number(params.intraday_trailing_reduce_ratio).toFixed(6),
    params.max_positions,
    Number(params.position_pct).toFixed(6),
    params.max_symbols,
    params.priority_topk_per_day,
  ].join('|')
}

function getPlateauAxisRawValue(point: BacktestPlateauPoint, axis: PlateauAxisKey): number {
  if (axis === 'window_days') return Number(point.params.window_days)
  if (axis === 'min_score') return Number(point.params.min_score)
  if (axis === 'stop_loss') return Number(point.params.stop_loss) * 100
  if (axis === 'take_profit') return Number(point.params.take_profit) * 100
  if (axis === 'trailing_stop_pct') return Number(point.params.trailing_stop_pct) * 100
  if (axis === 'intraday_trailing_reduce_ratio') return Number(point.params.intraday_trailing_reduce_ratio) * 100
  if (axis === 'max_positions') return Number(point.params.max_positions)
  if (axis === 'position_pct') return Number(point.params.position_pct) * 100
  if (axis === 'max_symbols') return Number(point.params.max_symbols)
  return Number(point.params.priority_topk_per_day)
}

function formatPlateauAxisCategory(axis: PlateauAxisKey, value: number): string {
  if (axis === 'window_days' || axis === 'max_positions' || axis === 'max_symbols' || axis === 'priority_topk_per_day') {
    return String(Math.round(value))
  }
  if (axis === 'min_score') return Number(value).toFixed(2)
  return `${Number(value).toFixed(2)}%`
}

function quantile(values: number[], ratio: number): number {
  if (values.length <= 0) return 0
  const clipped = Math.max(0, Math.min(1, Number(ratio)))
  const sorted = [...values].sort((left, right) => left - right)
  if (sorted.length === 1) return Number(sorted[0])
  const position = clipped * (sorted.length - 1)
  const lowerIndex = Math.floor(position)
  const upperIndex = Math.ceil(position)
  if (lowerIndex === upperIndex) return Number(sorted[lowerIndex])
  const weight = position - lowerIndex
  return Number(sorted[lowerIndex]) * (1 - weight) + Number(sorted[upperIndex]) * weight
}

function getPlateauPrimaryScore(point: BacktestPlateauPoint): number {
  if (Number.isFinite(Number(point.plateau_score)) && Number(point.plateau_score) > 0) return Number(point.plateau_score)
  if (Number.isFinite(Number(point.local_score)) && Number(point.local_score) > 0) return Number(point.local_score)
  if (Number.isFinite(Number(point.point_score)) && Number(point.point_score) > 0) return Number(point.point_score)
  return Number(point.score)
}

function getPlateauMetricValue(point: BacktestPlateauPoint, metric: PlateauMetricKey): number {
  if (metric === 'plateau_score') return Number(point.plateau_score ?? 0)
  if (metric === 'local_score') return Number(point.local_score ?? 0)
  if (metric === 'point_score') return Number(point.point_score ?? 0)
  if (metric === 'score') return Number(point.score)
  return Number(point.stats.total_return)
}

function formatPlateauMetricValue(metric: PlateauMetricKey, value: number): string {
  if (metric === 'plateau_score' || metric === 'local_score' || metric === 'point_score') return Number(value).toFixed(1)
  if (metric === 'score') return Number(value).toFixed(3)
  return formatPct(Number(value))
}

function formatBacktestDateRangeLabel(payload?: Pick<BacktestRunRequest, 'date_from' | 'date_to'> | null): string {
  const dateFrom = String(payload?.date_from || '').trim()
  const dateTo = String(payload?.date_to || '').trim()
  if (dateFrom && dateTo) return `${dateFrom} ~ ${dateTo}`
  if (dateFrom) return `${dateFrom} ~ --`
  if (dateTo) return `-- ~ ${dateTo}`
  return '--'
}

function resolveBacktestHistoryRunRequest(
  row: BacktestTaskStatusResponse,
  payloadById: Record<string, BacktestRunRequest>,
): BacktestRunRequest | null {
  const effective = row.result?.effective_run_request
  if (effective) {
    return normalizeEffectiveBacktestRunRequest(effective, row.result)
  }
  const stored = payloadById[row.task_id]
  if (!stored) return null
  return normalizeEffectiveBacktestRunRequest(stored, row.result)
}

function normalizeEffectiveBacktestRunRequest(
  runRequest: BacktestRunRequest,
  result?: BacktestResponse | null,
): BacktestRunRequest {
  const strategyId = String(
    runRequest.strategy_id || result?.strategy_id || '',
  ).trim() as StrategyId
  const strategyParams = { ...(runRequest.strategy_params ?? {}) }
  if (result?.strategy_params && typeof result.strategy_params === 'object') {
    Object.assign(strategyParams, result.strategy_params)
  }
  return {
    ...runRequest,
    strategy_id: strategyId || runRequest.strategy_id,
    strategy_params: strategyParams,
    entry_events: Array.isArray(runRequest.entry_events) ? runRequest.entry_events : [],
    exit_events: Array.isArray(runRequest.exit_events) ? runRequest.exit_events : [],
  }
}

function buildPlateauPointFromBacktestHistory(
  runRequest: BacktestRunRequest,
  result: BacktestResponse,
): BacktestPlateauPoint {
  return {
    params: {
      window_days: Number(runRequest.window_days),
      min_score: Number(runRequest.min_score),
      stop_loss: Number(runRequest.stop_loss),
      take_profit: Number(runRequest.take_profit),
      trailing_stop_pct: Number(runRequest.trailing_stop_pct ?? 0),
      intraday_trailing_reduce_ratio: Number(
        runRequest.intraday_trailing_reduce_ratio ?? TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio,
      ),
      max_positions: Number(runRequest.max_positions),
      position_pct: Number(runRequest.position_pct),
      max_symbols: Number(runRequest.max_symbols),
      priority_topk_per_day: Number(runRequest.priority_topk_per_day ?? 0),
    },
    stats: result.stats,
    candidate_count: Number(result.candidate_count ?? 0),
    skipped_count: Number(result.skipped_count ?? 0),
    fill_rate: Number(result.fill_rate ?? 0),
    max_concurrent_positions: Number(result.max_concurrent_positions ?? 0),
    score: Number(result.stats.total_return),
    cache_hit: false,
  }
}

function formatBacktestRunRequestSummary(payload: BacktestRunRequest): string {
  const strategyLabel = String(payload.strategy_id || '默认').trim() || '默认'
  const modeLabel = payload.mode === 'trend_pool' ? '趋势池' : '全市场'
  const rollLabel = payload.pool_roll_mode === 'daily' ? '日滚' : payload.pool_roll_mode
  const boardLabel =
    payload.mode === 'trend_pool' && payload.board_filters?.length
      ? payload.board_filters.join('/')
      : null
  const intradayLabel = payload.intraday_trailing_enabled === false
    ? '日内关'
    : `日内${(Number(payload.intraday_trailing_reduce_ratio ?? 0) * 100).toFixed(0)}%`
  const dailyLabel = payload.daily_trailing_clear_enabled === false
    ? '多日关'
    : `多日${payload.daily_trailing_confirm_days ?? TRADE_BACKTEST_DEFAULTS.dailyTrailingConfirmDays}天`
  const parts = [
    `策略=${strategyLabel}`,
    `模式=${modeLabel}`,
    `滚动=${rollLabel}`,
    boardLabel ? `板块=${boardLabel}` : null,
    `${payload.date_from}~${payload.date_to}`,
    `window=${payload.window_days}`,
    `min=${Number(payload.min_score).toFixed(2)}`,
    `sl=${(Number(payload.stop_loss) * 100).toFixed(2)}%`,
    `tp=${(Number(payload.take_profit) * 100).toFixed(2)}%`,
    `ts=${(Number(payload.trailing_stop_pct ?? 0) * 100).toFixed(2)}%`,
    intradayLabel,
    dailyLabel,
    `pos=${payload.max_positions}`,
    `单笔=${(Number(payload.position_pct) * 100).toFixed(1)}%`,
    `标的=${payload.max_symbols}`,
    `费率=${payload.fee_bps}bp`,
  ].filter((item): item is string => Boolean(item))
  return parts.join(', ')
}

function buildPlateauCellKey(xValue: number, yValue: number): string {
  return `${Number(xValue).toFixed(6)}|${Number(yValue).toFixed(6)}`
}

function sanitizeBoardFilters(raw: unknown): BoardFilter[] {
  if (!Array.isArray(raw)) return []
  const selected = raw
    .map((item) => String(item).trim())
    .filter((item): item is BoardFilter => ALLOWED_BOARD_FILTERS.includes(item as BoardFilter))
  return Array.from(new Set(selected))
}

function buildDefaultDraft(): BacktestFormDraft {
  const dateTo = dayjs().subtract(1, 'day').format('YYYY-MM-DD')
  const dateFrom = dayjs(dateTo).subtract(TRADE_BACKTEST_DEFAULTS.defaultLookbackDays, 'day').format('YYYY-MM-DD')
  return {
    mode: TRADE_BACKTEST_DEFAULTS.mode,
    trend_step: TRADE_BACKTEST_DEFAULTS.trendStep,
    pool_roll_mode: TRADE_BACKTEST_DEFAULTS.poolRollMode,
    run_id: '',
    board_filters: BACKTEST_DEFAULT_BOARD_FILTERS,
    strategy_id: 'wyckoff_trend_v1',
    strategy_params: {},
    date_from: dateFrom,
    date_to: dateTo,
    window_days: TRADE_BACKTEST_DEFAULTS.windowDays,
    min_score: TRADE_BACKTEST_DEFAULTS.minScore,
    require_sequence: TRADE_BACKTEST_DEFAULTS.requireSequence,
    min_event_count: TRADE_BACKTEST_DEFAULTS.minEventCount,
    entry_events: TRADE_BACKTEST_DEFAULTS.entryEvents,
    exit_events: TRADE_BACKTEST_DEFAULTS.exitEvents,
    initial_capital: TRADE_BACKTEST_DEFAULTS.initialCapital,
    position_pct: TRADE_BACKTEST_DEFAULTS.positionPct,
    max_positions: TRADE_BACKTEST_DEFAULTS.maxPositions,
    stop_loss: TRADE_BACKTEST_DEFAULTS.stopLoss,
    take_profit: TRADE_BACKTEST_DEFAULTS.takeProfit,
    trailing_stop_pct: TRADE_BACKTEST_DEFAULTS.trailingStopPct,
    intraday_trailing_enabled: TRADE_BACKTEST_DEFAULTS.intradayTrailingEnabled,
    daily_trailing_clear_enabled: TRADE_BACKTEST_DEFAULTS.dailyTrailingClearEnabled,
    intraday_trailing_reduce_ratio: TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio,
    daily_trailing_confirm_days: TRADE_BACKTEST_DEFAULTS.dailyTrailingConfirmDays,
    max_hold_days: TRADE_BACKTEST_DEFAULTS.maxHoldDays,
    fee_bps: TRADE_BACKTEST_DEFAULTS.feeBps,
    prioritize_signals: TRADE_BACKTEST_DEFAULTS.prioritizeSignals,
    priority_mode: TRADE_BACKTEST_DEFAULTS.priorityMode,
    priority_topk_per_day: TRADE_BACKTEST_DEFAULTS.priorityTopK,
    enforce_t1: TRADE_BACKTEST_DEFAULTS.enforceT1,
    entry_delay_days: TRADE_BACKTEST_DEFAULTS.entryDelayDays,
    delay_invalidation_enabled: TRADE_BACKTEST_DEFAULTS.delayInvalidationEnabled,
    max_symbols: TRADE_BACKTEST_DEFAULTS.maxSymbols,
    enable_advanced_analysis: TRADE_BACKTEST_DEFAULTS.enableAdvancedAnalysis,
    trades_page_size: 25,
  }
}

function resolveInitialStrategyId(raw: unknown): StrategyId {
  const draftStrategyId = String(raw || '').trim()
  if (draftStrategyId) return draftStrategyId as StrategyId
  return getSharedLastStrategyId()
}

function loadBacktestDraft(): BacktestFormDraft {
  const defaults = buildDefaultDraft()
  if (typeof window === 'undefined') return defaults
  try {
    const raw = window.localStorage.getItem(BACKTEST_FORM_CACHE_KEY)
    if (!raw) return defaults
    const parsed = JSON.parse(raw) as Partial<BacktestFormDraft>
    const merged = { ...defaults, ...parsed }
    const mergedStrategyId = String(merged.strategy_id || '').trim() || 'wyckoff_trend_v1'
    if (!dayjs(merged.date_from).isValid()) merged.date_from = defaults.date_from
    if (!dayjs(merged.date_to).isValid()) merged.date_to = defaults.date_to
    if (!Array.isArray(merged.entry_events)) {
      merged.entry_events = defaults.entry_events
    }
    if (!Array.isArray(merged.exit_events)) {
      merged.exit_events = defaults.exit_events
    }
    if (merged.entry_events.length === 0 && !isEventIndependentBacktestStrategy(mergedStrategyId)) {
      merged.entry_events = defaults.entry_events
    }
    if (merged.exit_events.length === 0 && !allowsOptionalBacktestExitEvents(mergedStrategyId)) {
      merged.exit_events = defaults.exit_events
    }
    const boardFilters = sanitizeBoardFilters(merged.board_filters)
    merged.board_filters = boardFilters.length > 0 ? boardFilters : defaults.board_filters
    merged.strategy_id = mergedStrategyId || 'wyckoff_trend_v1'
    merged.strategy_params = normalizeStrategyParams(merged.strategy_params)
    if (!['auto', 'matrix', 'legacy'].includes(String(merged.execution_path_preference || ''))) {
      merged.execution_path_preference = undefined
    }
    if (!['daily', 'weekly', 'position'].includes(String(merged.pool_roll_mode))) {
      merged.pool_roll_mode = defaults.pool_roll_mode
    }
    if (!Number.isFinite(merged.entry_delay_days)) {
      merged.entry_delay_days = defaults.entry_delay_days
    } else {
      merged.entry_delay_days = Math.max(1, Math.min(5, Math.round(Number(merged.entry_delay_days))))
    }
    merged.delay_invalidation_enabled = merged.delay_invalidation_enabled !== false
    merged.enable_advanced_analysis = merged.enable_advanced_analysis !== false
    if (!Number.isFinite(merged.trades_page_size) || (merged.trades_page_size ?? 0) <= 0) {
      merged.trades_page_size = defaults.trades_page_size
    }
    return merged
  } catch {
    return defaults
  }
}

function persistBacktestDraft(draft: BacktestFormDraft) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(BACKTEST_FORM_CACHE_KEY, JSON.stringify(draft))
  } catch {
    // ignore local storage errors
  }
}

function buildDefaultPlateauDraft(): BacktestPlateauFormDraft {
  return {
    sampling_mode: 'grid',
    sample_points: 121,
    random_seed: 20260221,
    window_days_list_raw: '',
    min_score_list_raw: '',
    stop_loss_pct_list_raw: denseRiskGrid(11).stopLoss,
    take_profit_pct_list_raw: denseRiskGrid(11).takeProfit,
    trailing_stop_pct_list_raw: '',
    intraday_trailing_reduce_ratio_list_raw: '',
    max_positions_list_raw: '',
    position_pct_list_raw: '',
    max_symbols_list_raw: '',
    topk_list_raw: '',
    heatmap_x_axis: 'window_days',
    heatmap_y_axis: 'min_score',
    heatmap_metric: 'plateau_score',
    heatmap_show_best_path: false,
    heatmap_show_cell_label: false,
    table_sort_key: DEFAULT_PLATEAU_TABLE_SORT_STATE.key,
    table_sort_order: DEFAULT_PLATEAU_TABLE_SORT_STATE.order,
  }
}

function loadBacktestPlateauDraft(): BacktestPlateauFormDraft {
  const defaults = buildDefaultPlateauDraft()
  if (typeof window === 'undefined') return defaults
  try {
    const raw = window.localStorage.getItem(BACKTEST_PLATEAU_FORM_CACHE_KEY)
    if (!raw) return defaults
    const parsed = JSON.parse(raw) as Partial<BacktestPlateauFormDraft>
    const merged = { ...defaults, ...parsed }
    if (!['grid', 'lhs'].includes(String(merged.sampling_mode))) merged.sampling_mode = defaults.sampling_mode
    if (!Number.isFinite(merged.sample_points) || Number(merged.sample_points) <= 0) merged.sample_points = defaults.sample_points
    const randomSeedRaw = merged.random_seed
    merged.random_seed = randomSeedRaw === null ? null : (Number.isFinite(Number(randomSeedRaw)) ? Number(randomSeedRaw) : defaults.random_seed)
    if (!PLATEAU_AXIS_OPTIONS.some((item) => item.value === merged.heatmap_x_axis)) merged.heatmap_x_axis = defaults.heatmap_x_axis
    if (!PLATEAU_AXIS_OPTIONS.some((item) => item.value === merged.heatmap_y_axis)) merged.heatmap_y_axis = defaults.heatmap_y_axis
    if (!PLATEAU_METRIC_OPTIONS.some((item) => item.value === merged.heatmap_metric)) merged.heatmap_metric = defaults.heatmap_metric
    merged.heatmap_show_best_path = Boolean(merged.heatmap_show_best_path)
    merged.heatmap_show_cell_label = Boolean(merged.heatmap_show_cell_label)
    const normalizedTableSort = normalizePlateauTableSortState(merged.table_sort_key, merged.table_sort_order)
    merged.table_sort_key = normalizedTableSort.key
    merged.table_sort_order = normalizedTableSort.order
    return merged
  } catch {
    return defaults
  }
}

function persistBacktestPlateauDraft(draft: BacktestPlateauFormDraft) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(BACKTEST_PLATEAU_FORM_CACHE_KEY, JSON.stringify(draft))
  } catch {
    // ignore local storage errors
  }
}

function loadBacktestPlateauPresets(): BacktestPlateauPreset[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(BACKTEST_PLATEAU_PRESETS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const out: BacktestPlateauPreset[] = []
    for (const item of parsed) {
      const row = item as Partial<BacktestPlateauPreset>
      const id = String(row?.id || '').trim()
      const name = String(row?.name || '').trim()
      const savedAt = String(row?.saved_at || '').trim()
      const point = row?.point as BacktestPlateauPoint | undefined
      const runRequest =
        row?.run_request && typeof row.run_request === 'object'
          ? (row.run_request as BacktestRunRequest)
          : null
      const statsSummary =
        row?.stats_summary && typeof row.stats_summary === 'object'
          ? {
              total_return: Number((row.stats_summary as { total_return?: number }).total_return ?? 0),
              max_drawdown:
                (row.stats_summary as { max_drawdown?: number | null }).max_drawdown ?? null,
            }
          : null
      const source = row?.source === 'backtest_history' ? 'backtest_history' : 'plateau'
      if (!id || !name || !savedAt) continue
      if (source === 'plateau') {
        if (!point || !point.params || !point.stats) continue
      } else if (!runRequest) {
        continue
      }
      out.push({
        id,
        name,
        saved_at: savedAt,
        source,
        point: point ?? null,
        run_request: runRequest,
        stats_summary: statsSummary,
      })
    }
    return out.slice(0, 300)
  } catch {
    return []
  }
}

function persistBacktestPlateauPresets(presets: BacktestPlateauPreset[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(
      BACKTEST_PLATEAU_PRESETS_KEY,
      JSON.stringify(presets.slice(0, 300)),
    )
  } catch {
    // ignore local storage errors
  }
}

type ExitReasonView = {
  label: string
  color?: string
  detail?: string
}

type AnalysisAdvice = {
  level: 'success' | 'warning' | 'error' | 'info'
  title: string
  tips: string[]
}

type BacktestModuleKey =
  | 'report_share'
  | 'plateau_params'
  | 'plateau_presets'
  | 'recent_task_progress'
  | 'backtest_history'
  | 'run_notes'

const DEFAULT_COLLAPSED_MODULES: Record<BacktestModuleKey, boolean> = {
  report_share: true,
  plateau_params: true,
  plateau_presets: false,
  recent_task_progress: true,
  backtest_history: false,
  run_notes: true,
}

function loadCollapsedModules(): Record<BacktestModuleKey, boolean> {
  if (typeof window === 'undefined') return { ...DEFAULT_COLLAPSED_MODULES }
  try {
    const raw = window.localStorage.getItem(BACKTEST_COLLAPSED_MODULES_KEY)
    if (!raw) return { ...DEFAULT_COLLAPSED_MODULES }
    const parsed = JSON.parse(raw) as Partial<Record<BacktestModuleKey, boolean>>
    return {
      ...DEFAULT_COLLAPSED_MODULES,
      report_share: Boolean(parsed.report_share ?? DEFAULT_COLLAPSED_MODULES.report_share),
      plateau_params: Boolean(parsed.plateau_params ?? DEFAULT_COLLAPSED_MODULES.plateau_params),
      plateau_presets: Boolean(parsed.plateau_presets ?? DEFAULT_COLLAPSED_MODULES.plateau_presets),
      recent_task_progress: Boolean(parsed.recent_task_progress ?? DEFAULT_COLLAPSED_MODULES.recent_task_progress),
      backtest_history: Boolean(parsed.backtest_history ?? DEFAULT_COLLAPSED_MODULES.backtest_history),
      run_notes: Boolean(parsed.run_notes ?? DEFAULT_COLLAPSED_MODULES.run_notes),
    }
  } catch {
    return { ...DEFAULT_COLLAPSED_MODULES }
  }
}

function persistCollapsedModules(state: Record<BacktestModuleKey, boolean>) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(BACKTEST_COLLAPSED_MODULES_KEY, JSON.stringify(state))
  } catch {
    // ignore local storage errors
  }
}

function normalizeEventToken(raw: string): string {
  const token = String(raw || '').trim()
  if (!token) return ''
  const mapped = {
    M5: 'SOS',
    M6: 'LPS',
    MATRIX: '矩阵入场',
    MATRIX_SELL: '矩阵卖出',
    SCORE: '评分触发',
  }[token]
  return mapped || token
}

function formatEventSequence(raw: string): string {
  const text = String(raw || '').trim()
  if (!text) return ''
  const parts = text
    .split('/')
    .map((item) => normalizeEventToken(item))
    .filter(Boolean)
  if (parts.length <= 0) return text
  return parts.join(' / ')
}

function formatConfiguredEventList(strategyId: unknown, events: string[], kind: 'entry' | 'exit'): string {
  if (Array.isArray(events) && events.length > 0) {
    return events.map((item) => normalizeEventToken(String(item || '').trim())).filter(Boolean).join(', ')
  }
  if (kind === 'entry' && isEventIndependentBacktestStrategy(strategyId)) {
    return '策略信号直连'
  }
  if (kind === 'exit' && allowsOptionalBacktestExitEvents(strategyId)) {
    return '未配置（使用止损/止盈/持有天数等规则退出）'
  }
  return '--'
}

function parseExitReason(value: string): ExitReasonView {
  const text = String(value || '').trim()
  if (text.startsWith('event_exit')) {
    const detail = text.includes(':') ? text.split(':').slice(1).join(':').trim() : ''
    const detailText = formatEventSequence(detail)
    return { label: '事件', color: 'blue', detail: detailText || undefined }
  }
  if (text === 'stop_loss') return { label: '止损', color: 'red' }
  if (text === 'take_profit') return { label: '止盈', color: 'green' }
  if (text === 'trailing_stop') return { label: '回撤', color: 'orange' }
  if (text === 'trailing_stop_daily') return { label: '日K回撤清仓', color: 'volcano' }
  if (text.startsWith('trailing_stop_intraday')) {
    const reduceMatch = text.match(/reduce=(\d+)/)
    const reducePct = reduceMatch ? Number(reduceMatch[1]) : 50
    return { label: '日内回撤减仓', color: 'orange', detail: `计划减仓${reducePct}%` }
  }
  if (text === 'time_exit') return { label: '超时', color: 'gold' }
  if (text === 'eod_exit' || text === 'backtest_end') return { label: '回测截止', color: 'purple' }
  if (text === 'open') return { label: '仍持仓', color: 'cyan' }
  if (!text) return { label: '未知' }
  return { label: text }
}

function isTrailingPlanSellReason(value: string): boolean {
  const text = String(value || '').trim()
  return (
    text === 'trailing_stop'
    || text === 'trailing_stop_daily'
    || text.startsWith('trailing_stop_intraday')
    || text.startsWith('event_exit')
  )
}

function isBacktestOpenHolding(trade: Pick<BacktestTrade, 'exit_reason' | 'quantity'>): boolean {
  return String(trade.exit_reason || '').trim().toLowerCase() === 'open' && Number(trade.quantity || 0) > 0
}

function buildBacktestEntryGroupKey(trade: Pick<BacktestTrade, 'symbol' | 'signal_date' | 'entry_date'>): string {
  return `${String(trade.symbol || '').trim().toLowerCase()}|${String(trade.signal_date || '').trim()}|${String(trade.entry_date || '').trim()}`
}

/** 同一笔买入若被拆成多条 leg，合并为一条展示记录（数量求和）。 */
function mergeBacktestEntryLegTrades(trades: BacktestTrade[]): BacktestTrade[] {
  const grouped = new Map<string, BacktestTrade>()
  trades.forEach((trade) => {
    const key = buildBacktestEntryGroupKey(trade)
    const existing = grouped.get(key)
    if (!existing) {
      grouped.set(key, { ...trade })
      return
    }
    grouped.set(key, {
      ...existing,
      quantity: Number(existing.quantity || 0) + Number(trade.quantity || 0),
    })
  })
  return [...grouped.values()]
}

function countDistinctBacktestSymbols(trades: Array<Pick<BacktestTrade, 'symbol'>>): number {
  return new Set(trades.map((trade) => String(trade.symbol || '').trim().toLowerCase()).filter(Boolean)).size
}

function formatTradeExitDate(trade: Pick<BacktestTrade, 'exit_date' | 'exit_reason' | 'quantity'>): string {
  return isBacktestOpenHolding(trade) ? '--' : String(trade.exit_date || '--')
}

function formatTradeExitPrice(trade: Pick<BacktestTrade, 'exit_price' | 'exit_reason' | 'quantity'>): string {
  if (isBacktestOpenHolding(trade)) return '--'
  const price = Number(trade.exit_price)
  return Number.isFinite(price) ? price.toFixed(2) : '--'
}

function formatTradeExitReasonLabel(trade: Pick<BacktestTrade, 'exit_reason' | 'quantity'>): string {
  if (isBacktestOpenHolding(trade)) return '--'
  const parsed = parseExitReason(trade.exit_reason)
  return parsed.detail ? `${parsed.label}:${parsed.detail}` : parsed.label
}

function renderTradeExitReason(value: string, row: Pick<BacktestTrade, 'exit_reason' | 'quantity'>) {
  if (isBacktestOpenHolding(row)) {
    return <span className="text-hint">--</span>
  }
  const parsed = parseExitReason(value)
  if (parsed.detail) {
    return (
      <Space size={6}>
        <Tag color={parsed.color}>{parsed.label}</Tag>
        <span>{parsed.detail}</span>
      </Space>
    )
  }
  return <Tag color={parsed.color}>{parsed.label}</Tag>
}

function buildStabilityAdvice(stability: BacktestStabilityDiagnostics | null): AnalysisAdvice | null {
  if (!stability) return null
  const score = Number(stability.stability_score || 0)
  const neighborhood = Number(stability.neighborhood_consistency || 0)
  const variancePenalty = Number(stability.return_variance_penalty || 0)
  const tradePenalty = Number(stability.trade_count_penalty || 0)
  if (score >= 0.78 && neighborhood >= 0.6 && variancePenalty <= 0.15) {
    return {
      level: 'success',
      title: '稳定性较好：参数在邻域变化下仍保持一致，当前组合更适合继续验证。',
      tips: [
        '先做小样本实盘/模拟跟踪，确认滑点和手续费后再放大仓位。',
        '保持参数微调幅度，避免一次性大改导致策略漂移。',
      ],
    }
  }
  if (score >= 0.6) {
    const tips = [
      '参数可用但仍偏敏感，建议先控制仓位并观察至少 1~2 个月。',
    ]
    if (variancePenalty >= 0.18) {
      tips.push('方差惩罚较高：优先收敛止盈止损、降低单笔仓位和并发持仓。')
    }
    if (neighborhood < 0.5) {
      tips.push('邻域一致性偏弱：在收益平原中选择更“平台区”的参数，少选尖峰点。')
    }
    if (tradePenalty > 0) {
      tips.push('交易样本偏少：扩展回测区间或放宽筛选条件，先提升样本量再定参数。')
    }
    return {
      level: 'warning',
      title: '稳定性中等：可用，但参数和行情切换时可能出现性能波动。',
      tips,
    }
  }
  return {
    level: 'error',
    title: '稳定性偏弱：当前参数对市场或参数扰动较敏感，实盘容易形变。',
    tips: [
      '优先在收益平原里选择胜率更高、回撤更低的“钝化参数”。',
      '降低仓位与并发持仓，先把极端亏损控制住，再追求收益。',
      '缩短单次持仓周期或收紧出场，减少尾部波动对净值的冲击。',
    ],
  }
}

function buildRegimeAdvice(regimes: BacktestRegimeBucket[]): AnalysisAdvice | null {
  if (!Array.isArray(regimes) || regimes.length <= 0) return null
  const totalTrades = regimes.reduce((sum, row) => sum + Number(row.trade_count || 0), 0)
  if (totalTrades <= 0) {
    return {
      level: 'info',
      title: '市场状态拆分暂无交易样本，暂时无法判断策略在不同环境下的适配性。',
      tips: ['补充样本后再观察牛/震荡/熊三类状态的收益差异。'],
    }
  }
  const dominant = [...regimes].sort((a, b) => Number(b.trade_count || 0) - Number(a.trade_count || 0))[0]!
  const dominantShare = Number(dominant.trade_count || 0) / totalTrades
  const bull = regimes.find((row) => row.regime === 'bull')
  const bear = regimes.find((row) => row.regime === 'bear')
  const allNonPositive = regimes.every((row) => Number(row.total_return || 0) <= 0)
  if (allNonPositive) {
    return {
      level: 'error',
      title: '三种市场状态下收益均不理想，策略当前缺乏可交易优势。',
      tips: [
        '先回到信号层重审入场条件，提升入场质量分阈值。',
        '减少参数自由度，避免过拟合后在所有状态都失效。',
      ],
    }
  }
  if (dominant.regime === 'bear' && dominantShare >= 0.45 && Number(dominant.total_return || 0) < 0) {
    return {
      level: 'warning',
      title: '交易主要集中在熊市代理且表现偏弱，回撤压力会显著放大。',
      tips: [
        '加入趋势过滤或市场风险开关，在弱势状态下降低出手频率。',
        '优先提高止损纪律，减少逆势持仓时间。',
      ],
    }
  }
  if ((bull?.total_return ?? 0) > 0 && (bear?.total_return ?? 0) < 0) {
    return {
      level: 'warning',
      title: '策略呈现“顺势有效、逆势偏弱”特征，择时过滤会明显提升稳定性。',
      tips: [
        '在熊市代理阶段降低仓位上限或直接切换防守参数。',
        '针对震荡/熊市单独做参数组，避免同一套参数全时段硬跑。',
      ],
    }
  }
  return {
    level: 'success',
    title: '不同市场状态下表现相对均衡，策略具备一定环境适应性。',
    tips: ['继续关注状态切换时的回撤变化，避免在单一状态下过度优化。'],
  }
}

function buildMonteCarloAdvice(monteCarlo: BacktestMonteCarloSummary | null): AnalysisAdvice | null {
  if (!monteCarlo || Number(monteCarlo.simulations || 0) <= 0) {
    return {
      level: 'info',
      title: '交易样本不足，暂未生成有效的蒙特卡洛压力测试。',
      tips: ['建议至少积累更多成交样本后再看尾部风险。'],
    }
  }
  const ruin = Number(monteCarlo.ruin_probability || 0)
  const p50 = Number(monteCarlo.total_return_p50 || 0)
  const p5 = Number(monteCarlo.total_return_p5 || 0)
  if (ruin >= 0.4 || p50 < -0.1) {
    return {
      level: 'error',
      title: '压力测试结果偏弱：中位收益或极端亏损概率都处于高风险区间。',
      tips: [
        '优先降低单笔仓位和最大并发持仓，先把尾部亏损压下来。',
        '收紧止损并降低持仓天数，避免单笔亏损拖垮整体权益。',
      ],
    }
  }
  if (ruin >= 0.2 || p5 < -0.25) {
    return {
      level: 'warning',
      title: '存在明显尾部风险：常规表现可接受，但坏场景下回撤仍偏大。',
      tips: [
        '建议先做“保守参数”版本（更低仓位、更紧止损）作为实盘基线。',
        '对高波动标的减少配置，降低组合回撤耦合。',
      ],
    }
  }
  return {
    level: 'success',
    title: '压力测试可接受：尾部风险相对受控，参数具备一定抗扰动能力。',
    tips: ['继续用滚动窗口复测，确认不同年份下压力结果一致。'],
  }
}

function buildWalkForwardAdvice(walkForward: BacktestWalkForwardReport | null): AnalysisAdvice | null {
  if (!walkForward || Number(walkForward.fold_count || 0) <= 0) {
    return {
      level: 'info',
      title: 'Walk-forward 未执行或无有效折叠，暂无法判断样本外泛化能力。',
      tips: ['扩大回测区间并确保交易样本充足后再评估 OOS 通过率。'],
    }
  }
  const passRate = Number(walkForward.oos_pass_rate || 0)
  const avgReturn = Number(walkForward.avg_test_return || 0)
  if (passRate >= 0.67 && avgReturn > 0) {
    return {
      level: 'success',
      title: '样本外验证较好：多数折叠通过，策略具备一定泛化能力。',
      tips: ['保持当前参数区间，优先做执行层优化（滑点、成交约束、风控细节）。'],
    }
  }
  if (passRate > 0) {
    return {
      level: 'warning',
      title: '样本外通过率一般：参数有效性不够稳定，存在阶段性失效风险。',
      tips: [
        '缩小参数搜索空间，优先选择在多折叠中表现更均衡的参数。',
        '将训练目标从“高收益”转为“低回撤+可接受收益”。',
      ],
    }
  }
  return {
    level: 'error',
    title: '样本外通过率为 0：当前参数在新样本下不可用，过拟合风险较高。',
    tips: [
      '重新设计入场/出场约束，先追求可复制性再追求收益峰值。',
      '增加稳健性约束（最低胜率、最大回撤上限）后再进行参数搜索。',
    ],
  }
}

function buildChartPath(symbol: string, name?: string, focusDate?: string) {
  const symbolText = String(symbol || '').trim()
  if (!symbolText) return ''
  const params = new URLSearchParams()
  const nameText = String(name || '').trim()
  if (nameText) params.set('signal_stock_name', nameText)
  if (focusDate) params.set('focus_date', focusDate)
  const query = params.toString()
  return `/stocks/${symbolText}/chart${query ? `?${query}` : ''}`
}

function taskStatusLabel(status: BacktestTaskStatusResponse['status']) {
  if (status === 'pending') return '准备中'
  if (status === 'running') return '运行中'
  if (status === 'paused') return '已暂停'
  if (status === 'succeeded') return '已完成'
  if (status === 'cancelled') return '已停止'
  return '失败'
}

function taskStatusColor(status: BacktestTaskStatusResponse['status']) {
  if (status === 'pending') return 'default'
  if (status === 'running') return 'processing'
  if (status === 'paused') return 'warning'
  if (status === 'succeeded') return 'success'
  if (status === 'cancelled') return 'default'
  return 'error'
}

function plateauTaskStatusLabel(status: BacktestPlateauTaskStatusResponse['status']) {
  if (status === 'pending') return '准备中'
  if (status === 'running') return '运行中'
  if (status === 'paused') return '已暂停'
  if (status === 'succeeded') return '已完成'
  if (status === 'cancelled') return '已停止'
  return '失败'
}

function plateauTaskStatusColor(status: BacktestPlateauTaskStatusResponse['status']) {
  if (status === 'pending') return 'default'
  if (status === 'running') return 'processing'
  if (status === 'paused') return 'warning'
  if (status === 'succeeded') return 'success'
  if (status === 'cancelled') return 'default'
  return 'error'
}

const openSignalColumns: ColumnsType<BacktestTrade> = [
  {
    title: '代码',
    dataIndex: 'symbol',
    width: 110,
    render: (value: string, row) => {
      const symbol = String(value || '').trim()
      if (!symbol) return '--'
      const target = buildChartPath(symbol, row.name, row.signal_date)
      return <Link to={target}>{symbol}</Link>
    },
  },
  {
    title: '名称',
    dataIndex: 'name',
    width: 120,
    render: (value: string, row) => {
      const symbol = String(row.symbol || '').trim()
      const name = String(value || '').trim()
      if (!symbol) return name || '--'
      const target = buildChartPath(symbol, name || row.name, row.signal_date)
      return <Link to={target}>{name || '--'}</Link>
    },
  },
  { title: '信号日', dataIndex: 'signal_date', width: 110 },
  {
    title: '入场事件',
    dataIndex: 'entry_signal',
    width: 130,
    render: (value: string) => formatEventSequence(value) || '--',
  },
  {
    title: '质量分',
    dataIndex: 'entry_quality_score',
    width: 88,
    render: (value: number) => value.toFixed(1),
  },
  {
    title: '筹码分',
    dataIndex: 'cost_center_shift_score',
    width: 92,
    render: (value: number | undefined) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '周线分',
    dataIndex: 'weekly_context_score',
    width: 92,
    render: (value: number | undefined) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '健康分',
    dataIndex: 'health_score',
    width: 88,
    render: (value: number | undefined) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '事件分',
    dataIndex: 'event_score',
    width: 88,
    render: (value: number | undefined) => Number(value ?? 0).toFixed(1),
  },
  { title: '参考价', dataIndex: 'entry_price', width: 90 },
  {
    title: '阶段',
    dataIndex: 'entry_phase',
    width: 100,
  },
]

const tradeColumns: ColumnsType<BacktestTrade> = [
  {
    title: '代码',
    dataIndex: 'symbol',
    width: 110,
    render: (value: string, row) => {
      const symbol = String(value || '').trim()
      if (!symbol) return '--'
      const target = buildChartPath(symbol, row.name, row.signal_date)
      return <Link to={target}>{symbol}</Link>
    },
  },
  {
    title: '名称',
    dataIndex: 'name',
    width: 120,
    render: (value: string, row) => {
      const symbol = String(row.symbol || '').trim()
      const name = String(value || '').trim()
      if (!symbol) return name || '--'
      const target = buildChartPath(symbol, name || row.name, row.signal_date)
      return <Link to={target}>{name || '--'}</Link>
    },
  },
  { title: '信号日', dataIndex: 'signal_date', width: 110 },
  { title: '买入日', dataIndex: 'entry_date', width: 110 },
  {
    title: '卖出日',
    dataIndex: 'exit_date',
    width: 110,
    render: (_value, row) => formatTradeExitDate(row),
  },
  {
    title: '入场事件',
    dataIndex: 'entry_signal',
    width: 130,
    render: (value: string) => formatEventSequence(value) || '--',
  },
  {
    title: '质量分',
    dataIndex: 'entry_quality_score',
    width: 88,
    render: (value: number) => value.toFixed(1),
  },
  {
    title: '筹码分',
    dataIndex: 'cost_center_shift_score',
    width: 92,
    render: (value: number | undefined) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '周线分',
    dataIndex: 'weekly_context_score',
    width: 92,
    render: (value: number | undefined) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '周乘数',
    dataIndex: 'weekly_context_multiplier',
    width: 88,
    render: (value: number | undefined) => Number(value ?? 1).toFixed(3),
  },
  {
    title: '离场事件/原因',
    dataIndex: 'exit_reason',
    width: 180,
    render: (value: string, row) => renderTradeExitReason(value, row),
  },
  { title: '数量', dataIndex: 'quantity', width: 92 },
  { title: '买入价', dataIndex: 'entry_price', width: 90 },
  {
    title: '卖出价',
    dataIndex: 'exit_price',
    width: 90,
    render: (_value, row) => formatTradeExitPrice(row),
  },
  { title: '持仓天数', dataIndex: 'holding_days', width: 92 },
  {
    title: '盈亏额',
    dataIndex: 'pnl_amount',
    width: 124,
    render: (value: number) => <span style={{ color: value >= 0 ? '#c4473d' : '#19744f' }}>{formatMoney(value)}</span>,
  },
  {
    title: '盈亏比',
    dataIndex: 'pnl_ratio',
    width: 96,
    render: (value: number) => formatPct(value),
  },
]

function buildPlanBuyColumns(trades: BacktestTrade[]): ColumnsType<BacktestTrade> {
  const executedKeys = new Set(
    trades.map((trade) => `${trade.symbol}|${trade.signal_date}`),
  )
  return [
    {
      title: '代码',
      dataIndex: 'symbol',
      width: 100,
      render: (value: string, row) => {
        const symbol = String(value || '').trim()
        if (!symbol) return '--'
        return <Link to={buildChartPath(symbol, row.name, row.signal_date)}>{symbol}</Link>
      },
    },
    {
      title: '名称',
      dataIndex: 'name',
      width: 100,
      render: (value: string, row) => {
        const symbol = String(row.symbol || '').trim()
        const name = String(value || '').trim()
        if (!symbol) return name || '--'
        return <Link to={buildChartPath(symbol, name || row.name, row.signal_date)}>{name || '--'}</Link>
      },
    },
    { title: '信号日', dataIndex: 'signal_date', width: 100 },
    { title: '计划买入日', dataIndex: 'entry_date', width: 110 },
    {
      title: '执行状态',
      key: 'execution_status',
      width: 100,
      render: (_value, row) => {
        const key = `${row.symbol}|${row.signal_date}`
        if (executedKeys.has(key)) {
          return <Tag color="success">已成交</Tag>
        }
        return <Tag color="warning">未成交</Tag>
      },
    },
    { title: '信号价', dataIndex: 'entry_price', width: 90, render: (v: number) => (v ? v.toFixed(2) : '--') },
    { title: '综合评分', dataIndex: 'entry_quality_score', width: 90, render: (v: number) => (v != null ? v.toFixed(1) : '--') },
    { title: '健康分', dataIndex: 'health_score', width: 80, render: (v: number) => (v != null ? v.toFixed(1) : '--') },
    { title: '事件分', dataIndex: 'event_score', width: 80, render: (v: number) => (v != null ? v.toFixed(1) : '--') },
    {
      title: '事件等级',
      dataIndex: 'event_grade',
      width: 80,
      render: (v: string) => {
        const colorMap: Record<string, string> = { A: 'gold', B: 'blue', C: 'default' }
        return <Tag color={colorMap[v] || 'default'}>{v || '--'}</Tag>
      },
    },
    { title: '入场信号', dataIndex: 'entry_signal', width: 120 },
    { title: '阶段', dataIndex: 'entry_phase', width: 100 },
    {
      title: '确认状态',
      dataIndex: 'confirmation_status',
      width: 90,
      render: (v: string) => {
        const labelMap: Record<string, string> = { confirmed: '已确认', partial: '部分确认', unconfirmed: '未确认', risk_blocked: '风险阻断' }
        const colorMap: Record<string, string> = { confirmed: 'green', partial: 'orange', unconfirmed: 'default', risk_blocked: 'red' }
        return <Tag color={colorMap[v] || 'default'}>{labelMap[v] || v || '--'}</Tag>
      },
    },
  ]
}

function buildEquityDateDetailColumns(totalEquity: number): ColumnsType<BacktestTrade> {
  return [
    {
      title: '代码',
      dataIndex: 'symbol',
      width: 100,
      render: (value: string, row) => {
        const symbol = String(value || '').trim()
        if (!symbol) return '--'
        return <Link to={buildChartPath(symbol, row.name, row.signal_date)}>{symbol}</Link>
      },
    },
    {
      title: '名称',
      dataIndex: 'name',
      width: 100,
      render: (value: string, row) => {
        const symbol = String(row.symbol || '').trim()
        const name = String(value || '').trim()
        if (!symbol) return name || '--'
        return <Link to={buildChartPath(symbol, name || row.name, row.signal_date)}>{name || '--'}</Link>
      },
    },
    { title: '信号日', dataIndex: 'signal_date', width: 100 },
    { title: '买入日', dataIndex: 'entry_date', width: 100 },
    {
      title: '卖出日',
      dataIndex: 'exit_date',
      width: 100,
      render: (_value, row) => formatTradeExitDate(row),
    },
    { title: '数量', dataIndex: 'quantity', width: 70 },
    { title: '买入价', dataIndex: 'entry_price', width: 80 },
    {
      title: '卖出价',
      dataIndex: 'exit_price',
      width: 80,
      render: (_value, row) => formatTradeExitPrice(row),
    },
    {
      title: '投入成本',
      width: 100,
      render: (_: unknown, row) => formatMoney(row.quantity * row.entry_price),
    },
    {
      title: '持仓占比',
      width: 80,
      render: (_: unknown, row) => {
        if (totalEquity <= 0) return '--'
        return formatPct((row.quantity * row.entry_price) / totalEquity)
      },
    },
    {
      title: '收益率',
      dataIndex: 'pnl_ratio',
      width: 80,
      render: (value: number) => {
        const color = value >= 0 ? '#c4473d' : '#19744f'
        return <span style={{ color }}>{formatPct(value)}</span>
      },
    },
    {
      title: '盈亏额',
      dataIndex: 'pnl_amount',
      width: 100,
      render: (value: number) => <span style={{ color: value >= 0 ? '#c4473d' : '#19744f' }}>{formatMoney(value)}</span>,
    },
    {
      title: '离场原因',
      dataIndex: 'exit_reason',
      width: 140,
      render: (value: string, row) => renderTradeExitReason(value, row),
    },
  ]
}

function toSortableNumber(value: unknown) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : Number.NEGATIVE_INFINITY
}

function compareSortableNumbers(left: unknown, right: unknown) {
  return toSortableNumber(left) - toSortableNumber(right)
}

function isPlateauTableSortKey(value: unknown): value is PlateauTableSortKey {
  return typeof value === 'string' && (PLATEAU_TABLE_SORT_KEYS as readonly string[]).includes(value)
}

function normalizePlateauTableSortState(
  key: unknown,
  order: unknown,
): PlateauTableSortState {
  const normalizedKey = isPlateauTableSortKey(key) ? key : DEFAULT_PLATEAU_TABLE_SORT_STATE.key
  const normalizedOrder = order === 'ascend' || order === 'descend'
    ? order
    : DEFAULT_PLATEAU_TABLE_SORT_STATE.order
  return {
    key: normalizedKey,
    order: normalizedOrder,
  }
}

function resolvePlateauTableSortState(
  sorter: SorterResult<BacktestPlateauTableRow> | SorterResult<BacktestPlateauTableRow>[],
): PlateauTableSortState | null {
  const resolved = Array.isArray(sorter)
    ? sorter.find((item) => isPlateauTableSortKey(item?.columnKey) && (item.order === 'ascend' || item.order === 'descend'))
    : sorter
  if (!resolved || !isPlateauTableSortKey(resolved.columnKey) || (resolved.order !== 'ascend' && resolved.order !== 'descend')) {
    return null
  }
  return {
    key: resolved.columnKey,
    order: resolved.order,
  }
}

const plateauColumns: ColumnsType<BacktestPlateauTableRow> = [
  {
    title: '收益平原分',
    key: 'plateau_score',
    dataIndex: 'plateau_score',
    width: 98,
    sorter: (left, right) => compareSortableNumbers(left.plateau_score, right.plateau_score),
    render: (value: number) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '局部分',
    key: 'local_score',
    dataIndex: 'local_score',
    width: 88,
    sorter: (left, right) => compareSortableNumbers(left.local_score, right.local_score),
    render: (value: number) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '单点质分',
    key: 'point_score',
    dataIndex: 'point_score',
    width: 90,
    sorter: (left, right) => compareSortableNumbers(left.point_score, right.point_score),
    render: (value: number) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '原始评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
    sorter: (left, right) => compareSortableNumbers(left.score, right.score),
    render: (value: number) => Number(value ?? 0).toFixed(3),
  },
  {
    title: '总收益',
    key: 'total_return',
    dataIndex: ['stats', 'total_return'],
    width: 90,
    sorter: (left, right) => compareSortableNumbers(left.stats.total_return, right.stats.total_return),
    render: (value: number) => formatPct(value),
  },
  {
    title: '最大回撤',
    key: 'max_drawdown',
    dataIndex: ['stats', 'max_drawdown'],
    width: 100,
    sorter: (left, right) => compareSortableNumbers(left.stats.max_drawdown, right.stats.max_drawdown),
    render: (value: number) => formatPct(value),
  },
  {
    title: '胜率',
    key: 'win_rate',
    dataIndex: ['stats', 'win_rate'],
    width: 90,
    sorter: (left, right) => compareSortableNumbers(left.stats.win_rate, right.stats.win_rate),
    render: (value: number) => formatPct(value),
  },
  {
    title: '交易数',
    key: 'trade_count',
    dataIndex: ['stats', 'trade_count'],
    width: 80,
    sorter: (left, right) => compareSortableNumbers(left.stats.trade_count, right.stats.trade_count),
  },
  {
    title: '年化交易',
    key: 'annual_trades',
    dataIndex: 'annual_trades',
    width: 92,
    sorter: (left, right) => compareSortableNumbers(left.annual_trades, right.annual_trades),
    render: (value: number) => Number(value ?? 0).toFixed(1),
  },
  {
    title: '窗口',
    key: 'window_days',
    dataIndex: ['params', 'window_days'],
    width: 70,
    sorter: (left, right) => compareSortableNumbers(left.params.window_days, right.params.window_days),
  },
  {
    title: '最低分',
    key: 'min_score',
    dataIndex: ['params', 'min_score'],
    width: 80,
    sorter: (left, right) => compareSortableNumbers(left.params.min_score, right.params.min_score),
  },
  {
    title: '止损%',
    key: 'stop_loss',
    dataIndex: ['params', 'stop_loss'],
    width: 80,
    sorter: (left, right) => compareSortableNumbers(left.params.stop_loss, right.params.stop_loss),
    render: (value: number) => `${(Number(value) * 100).toFixed(2)}%`,
  },
  {
    title: '止盈%',
    key: 'take_profit',
    dataIndex: ['params', 'take_profit'],
    width: 80,
    sorter: (left, right) => compareSortableNumbers(left.params.take_profit, right.params.take_profit),
    render: (value: number) => `${(Number(value) * 100).toFixed(2)}%`,
  },
  {
    title: '高位回撤%',
    key: 'trailing_stop_pct',
    dataIndex: ['params', 'trailing_stop_pct'],
    width: 92,
    sorter: (left, right) => compareSortableNumbers(left.params.trailing_stop_pct, right.params.trailing_stop_pct),
    render: (value: number) => `${(Number(value) * 100).toFixed(2)}%`,
  },
  {
    title: '日内减仓%',
    key: 'intraday_trailing_reduce_ratio',
    dataIndex: ['params', 'intraday_trailing_reduce_ratio'],
    width: 92,
    sorter: (left, right) =>
      compareSortableNumbers(left.params.intraday_trailing_reduce_ratio, right.params.intraday_trailing_reduce_ratio),
    render: (value: number) => `${(Number(value) * 100).toFixed(0)}%`,
  },
  {
    title: '仓位上限',
    key: 'max_positions',
    dataIndex: ['params', 'max_positions'],
    width: 86,
    sorter: (left, right) => compareSortableNumbers(left.params.max_positions, right.params.max_positions),
  },
  {
    title: '单笔%',
    key: 'position_pct',
    dataIndex: ['params', 'position_pct'],
    width: 78,
    sorter: (left, right) => compareSortableNumbers(left.params.position_pct, right.params.position_pct),
    render: (value: number) => `${(Number(value) * 100).toFixed(1)}%`,
  },
  {
    title: '股票数',
    key: 'max_symbols',
    dataIndex: ['params', 'max_symbols'],
    width: 78,
    sorter: (left, right) => compareSortableNumbers(left.params.max_symbols, right.params.max_symbols),
  },
  {
    title: 'TopK',
    key: 'priority_topk_per_day',
    dataIndex: ['params', 'priority_topk_per_day'],
    width: 70,
    sorter: (left, right) => compareSortableNumbers(left.params.priority_topk_per_day, right.params.priority_topk_per_day),
  },
  {
    title: '区域',
    key: 'region_rank',
    dataIndex: 'region_rank',
    width: 84,
    sorter: (left, right) => compareSortableNumbers(left.region_rank, right.region_rank),
    render: (value: number | null | undefined) => {
      if (!value) return <Tag>--</Tag>
      return <Tag color="geekblue">{`R${value}`}</Tag>
    },
  },
  {
    title: '硬门槛',
    dataIndex: 'passes_hard_filters',
    width: 100,
    render: (_value: boolean, row) => {
      if (row.error) return <Tag color="error">失败</Tag>
      if (row.passes_hard_filters) return <Tag color="success">通过</Tag>
      return <Tag color="warning">未通过</Tag>
    },
  },
  {
    title: '状态',
    dataIndex: 'error',
    width: 90,
    render: (value: string | null | undefined, row) => {
      if (value) return <Tag color="error">失败</Tag>
      if (row.cache_hit) return <Tag color="processing">缓存</Tag>
      return <Tag color="success">成功</Tag>
    },
  },
]

export function BacktestPage() {
  const plateauView = new URLSearchParams(useLocation().search).get('section') === 'plateau'
  const [showSharedConfig, setShowSharedConfig] = useState(false)
  const { message } = AntdApp.useApp()
  const initialDraft = useMemo(() => loadBacktestDraft(), [])
  const initialPlateauDraft = useMemo(() => loadBacktestPlateauDraft(), [])

  const [mode, setMode] = useState<SignalScanMode>(initialDraft.mode)
  const [trendStep, setTrendStep] = useState<TrendPoolStep>(initialDraft.trend_step)
  const [poolRollMode, setPoolRollMode] = useState<BacktestPoolRollMode>(initialDraft.pool_roll_mode)
  const [runId, setRunId] = useState(initialDraft.run_id)
  const [boardFilters, setBoardFilters] = useState<BoardFilter[]>(initialDraft.board_filters)
  const [strategyId, setStrategyId] = useState<StrategyId>(() => resolveInitialStrategyId(initialDraft.strategy_id))
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>(() => {
    const seedStrategyId = resolveInitialStrategyId(initialDraft.strategy_id)
    const draftParams = normalizeStrategyParams(initialDraft.strategy_params)
    if (Object.keys(draftParams).length > 0) return draftParams
    return getSharedStrategyParams(seedStrategyId)
  })
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs(initialDraft.date_from),
    dayjs(initialDraft.date_to),
  ])
  const [entryEvents, setEntryEvents] = useState<string[]>(initialDraft.entry_events)
  const [exitEvents, setExitEvents] = useState<string[]>(initialDraft.exit_events)
  const [initialCapital, setInitialCapital] = useState(initialDraft.initial_capital)
  const [positionPctPercent, setPositionPctPercent] = useState(toPercent(initialDraft.position_pct))
  const [maxPositions, setMaxPositions] = useState(initialDraft.max_positions)
  const [stopLossPercent, setStopLossPercent] = useState(toPercent(initialDraft.stop_loss))
  const [takeProfitPercent, setTakeProfitPercent] = useState(toPercent(initialDraft.take_profit))
  const [trailingStopPercent, setTrailingStopPercent] = useState(toPercent(initialDraft.trailing_stop_pct))
  const [intradayTrailingEnabled, setIntradayTrailingEnabled] = useState(
    initialDraft.intraday_trailing_enabled ?? TRADE_BACKTEST_DEFAULTS.intradayTrailingEnabled,
  )
  const [dailyTrailingClearEnabled, setDailyTrailingClearEnabled] = useState(
    initialDraft.daily_trailing_clear_enabled ?? TRADE_BACKTEST_DEFAULTS.dailyTrailingClearEnabled,
  )
  const [intradayTrailingReducePercent, setIntradayTrailingReducePercent] = useState(
    toPercent(initialDraft.intraday_trailing_reduce_ratio ?? TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio),
  )
  const [dailyTrailingConfirmDays, setDailyTrailingConfirmDays] = useState(
    initialDraft.daily_trailing_confirm_days ?? TRADE_BACKTEST_DEFAULTS.dailyTrailingConfirmDays,
  )
  const [maxHoldDays, setMaxHoldDays] = useState(initialDraft.max_hold_days)
  const [feeBps, setFeeBps] = useState(initialDraft.fee_bps)
  const [windowDays, setWindowDays] = useState(initialDraft.window_days)
  const [minScore, setMinScore] = useState(initialDraft.min_score)
  const [minEventCount, setMinEventCount] = useState(initialDraft.min_event_count)
  const [requireSequence, setRequireSequence] = useState(initialDraft.require_sequence)
  const [prioritizeSignals, setPrioritizeSignals] = useState(initialDraft.prioritize_signals)
  const [priorityMode, setPriorityMode] = useState<BacktestPriorityMode>(initialDraft.priority_mode)
  const [priorityTopK, setPriorityTopK] = useState(initialDraft.priority_topk_per_day)
  const [executionPathPreference, setExecutionPathPreference] = useState<BacktestExecutionPathPreference>(
    initialDraft.execution_path_preference ?? 'legacy',
  )
  const [enforceT1, setEnforceT1] = useState(initialDraft.enforce_t1)
  const [entryDelayDays, setEntryDelayDays] = useState(initialDraft.entry_delay_days)
  const [delayInvalidationEnabled, setDelayInvalidationEnabled] = useState(initialDraft.delay_invalidation_enabled)
  const [maxSymbols, setMaxSymbols] = useState(initialDraft.max_symbols)
  const [enableAdvancedAnalysis, setEnableAdvancedAnalysis] = useState(initialDraft.enable_advanced_analysis)
  const [plateauSamplingMode, setPlateauSamplingMode] = useState<'grid' | 'lhs'>(initialPlateauDraft.sampling_mode)
  const [plateauSamplePoints, setPlateauSamplePoints] = useState(initialPlateauDraft.sample_points)
  const [plateauRandomSeed, setPlateauRandomSeed] = useState<number | null>(initialPlateauDraft.random_seed)
  const [plateauWindowListRaw, setPlateauWindowListRaw] = useState(initialPlateauDraft.window_days_list_raw)
  const [plateauMinScoreListRaw, setPlateauMinScoreListRaw] = useState(initialPlateauDraft.min_score_list_raw)
  const [plateauStopLossPctListRaw, setPlateauStopLossPctListRaw] = useState(initialPlateauDraft.stop_loss_pct_list_raw)
  const [plateauTakeProfitPctListRaw, setPlateauTakeProfitPctListRaw] = useState(initialPlateauDraft.take_profit_pct_list_raw)
  const [plateauTrailingStopPctListRaw, setPlateauTrailingStopPctListRaw] = useState(initialPlateauDraft.trailing_stop_pct_list_raw)
  const [plateauIntradayReducePctListRaw, setPlateauIntradayReducePctListRaw] = useState(
    initialPlateauDraft.intraday_trailing_reduce_ratio_list_raw,
  )
  const [plateauMaxPositionsListRaw, setPlateauMaxPositionsListRaw] = useState(initialPlateauDraft.max_positions_list_raw)
  const [plateauPositionPctListRaw, setPlateauPositionPctListRaw] = useState(initialPlateauDraft.position_pct_list_raw)
  const [plateauMaxSymbolsListRaw, setPlateauMaxSymbolsListRaw] = useState(initialPlateauDraft.max_symbols_list_raw)
  const [plateauTopKListRaw, setPlateauTopKListRaw] = useState(initialPlateauDraft.topk_list_raw)
  const [plateauTableSort, setPlateauTableSort] = useState<PlateauTableSortState>(() => ({
    key: initialPlateauDraft.table_sort_key,
    order: initialPlateauDraft.table_sort_order,
  }))
  const [plateauError, setPlateauError] = useState<string | null>(null)
  const [plateauApplyRank, setPlateauApplyRank] = useState(1)
  const [plateauHeatmapXAxis, setPlateauHeatmapXAxis] = useState<PlateauAxisKey>(initialPlateauDraft.heatmap_x_axis)
  const [plateauHeatmapYAxis, setPlateauHeatmapYAxis] = useState<PlateauAxisKey>(initialPlateauDraft.heatmap_y_axis)
  const [plateauHeatmapMetric, setPlateauHeatmapMetric] = useState<PlateauMetricKey>(initialPlateauDraft.heatmap_metric)
  const [plateauHeatmapShowBestPath, setPlateauHeatmapShowBestPath] = useState(initialPlateauDraft.heatmap_show_best_path)
  const [plateauHeatmapShowCellLabel, setPlateauHeatmapShowCellLabel] = useState(initialPlateauDraft.heatmap_show_cell_label)
  const [plateauHeatmapSelectedCoord, setPlateauHeatmapSelectedCoord] = useState<[string, string] | null>(null)
  const [plateauBrushSelectedKeys, setPlateauBrushSelectedKeys] = useState<string[]>([])
  const [plateauCandidatePoints, setPlateauCandidatePoints] = useState<BacktestPlateauPoint[]>([])
  const [plateauCandidatePickRank, setPlateauCandidatePickRank] = useState(1)
  const [plateauSavedPresets, setPlateauSavedPresets] = useState<BacktestPlateauPreset[]>(() => loadBacktestPlateauPresets())
  const [plateauSavedPresetId, setPlateauSavedPresetId] = useState<string | null>(null)
  const [plateauExportingRowKey, setPlateauExportingRowKey] = useState<string | null>(null)
  const [plateauTradeViewingRow, setPlateauTradeViewingRow] = useState<BacktestPlateauTableRow | null>(null)
  const [plateauTradeViewResult, setPlateauTradeViewResult] = useState<BacktestResponse | null>(null)
  const [plateauTradeViewLoading, setPlateauTradeViewLoading] = useState(false)
  const [plateauTradeViewError, setPlateauTradeViewError] = useState<string | null>(null)
  const [plateauTradePage, setPlateauTradePage] = useState(1)
  const [plateauTradePageSize, setPlateauTradePageSize] = useState(20)
  const [plateauHistoryExportingTaskId, setPlateauHistoryExportingTaskId] = useState<string | null>(null)
  const [plateauHistoryExportingFormat, setPlateauHistoryExportingFormat] = useState<PlateauHistoryExportFormat | null>(null)
  const [tradeDetailExportingFormat, setTradeDetailExportingFormat] = useState<TradeExportFormat | null>(null)
  const [backtestHistoryTradesExportingTaskId, setBacktestHistoryTradesExportingTaskId] = useState<string | null>(null)
  const [backtestHistoryTradesExportingFormat, setBacktestHistoryTradesExportingFormat] = useState<TradeExportFormat | null>(null)
  const [tradePnlChartType, setTradePnlChartType] = useState<TradePnlChartType>('pie')
  const [tradePage, setTradePage] = useState(1)
  const [tradePageSize, setTradePageSize] = useState(initialDraft.trades_page_size)
  const [equityDateDetailDate, setEquityDateDetailDate] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [reportLibrary, setReportLibrary] = useState<BacktestReportSummary[]>([])
  const [reportLibraryLoading, setReportLibraryLoading] = useState(false)
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null)
  const [plateauHistoryLoading, setPlateauHistoryLoading] = useState(false)
  const [collapsedModules, setCollapsedModules] = useState<Record<BacktestModuleKey, boolean>>(() => loadCollapsedModules())
  useEffect(() => {
    if (plateauView) setCollapsedModules(previous => previous.plateau_params ? { ...previous, plateau_params: false } : previous)
  }, [plateauView])
  const importReportFileRef = useRef<HTMLInputElement | null>(null)
  const backtestResultSectionRef = useRef<HTMLDivElement | null>(null)
  const plateauPointDetailCacheRef = useRef<Map<string, BacktestPlateauPointDetailResponse>>(new Map())
  const executionPathPreferenceInitializedRef = useRef(initialDraft.execution_path_preference !== undefined)
  const backfillPayloadExtrasRef = useRef<Partial<BacktestRunRequest>>({})
  const tasksById = useBacktestTaskStore((state) => state.tasksById)
  const payloadById = useBacktestTaskStore((state) => state.payloadById)
  const activeTaskIds = useBacktestTaskStore((state) => state.activeTaskIds)
  const selectedTaskId = useBacktestTaskStore((state) => state.selectedTaskId)
  const enqueueTask = useBacktestTaskStore((state) => state.enqueueTask)
  const upsertTaskPayload = useBacktestTaskStore((state) => state.upsertTaskPayload)
  const upsertTaskStatus = useBacktestTaskStore((state) => state.upsertTaskStatus)
  const setSelectedTask = useBacktestTaskStore((state) => state.setSelectedTask)
  const removeTask = useBacktestTaskStore((state) => state.removeTask)
  const plateauTasksById = useBacktestPlateauTaskStore((state) => state.tasksById)
  const plateauActiveTaskIds = useBacktestPlateauTaskStore((state) => state.activeTaskIds)
  const plateauSelectedTaskId = useBacktestPlateauTaskStore((state) => state.selectedTaskId)
  const enqueuePlateauTask = useBacktestPlateauTaskStore((state) => state.enqueueTask)
  const upsertPlateauTaskStatus = useBacktestPlateauTaskStore((state) => state.upsertTaskStatus)
  const setSelectedPlateauTask = useBacktestPlateauTaskStore((state) => state.setSelectedTask)
  const removePlateauTask = useBacktestPlateauTaskStore((state) => state.removeTask)

  const taskOptions = useMemo(
    () =>
      Object.values(tasksById)
        .sort((left, right) => {
          const leftTs = Date.parse(left.progress.updated_at || left.progress.started_at || '')
          const rightTs = Date.parse(right.progress.updated_at || right.progress.started_at || '')
          return rightTs - leftTs
        })
        .map((task) => ({
          value: task.task_id,
          label: `${taskStatusLabel(task.status)} | ${task.task_id.slice(0, 12)} | ${task.progress.updated_at}`,
        })),
    [tasksById],
  )
  const taskStatus = selectedTaskId ? tasksById[selectedTaskId] ?? null : null
  const taskProgress = taskStatus?.progress
  const result = taskStatus?.result ?? undefined
  const riskMetrics = result?.risk_metrics ?? null
  const stabilityDiagnostics = result?.stability_diagnostics ?? null
  const regimeBreakdown = result?.regime_breakdown ?? []
  const monteCarlo = result?.monte_carlo ?? null
  const walkForward = result?.walk_forward ?? null
  const stabilityAdvice = useMemo(() => buildStabilityAdvice(stabilityDiagnostics), [stabilityDiagnostics])
  const regimeAdvice = useMemo(() => buildRegimeAdvice(regimeBreakdown), [regimeBreakdown])
  const monteCarloAdvice = useMemo(() => buildMonteCarloAdvice(monteCarlo), [monteCarlo])
  const walkForwardAdvice = useMemo(() => buildWalkForwardAdvice(walkForward), [walkForward])
  const scrollToBacktestResultSection = () => {
    window.requestAnimationFrame(() => {
      backtestResultSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }
  const toggleModule = (key: BacktestModuleKey) => {
    setCollapsedModules((prev) => ({ ...prev, [key]: !prev[key] }))
  }
  const renderModuleTitle = (key: BacktestModuleKey, label: string) => (
    <span
      role="button"
      tabIndex={0}
      style={{ display: 'block', width: '100%', cursor: 'pointer', userSelect: 'none' }}
      onClick={() => toggleModule(key)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          toggleModule(key)
        }
      }}
    >
      {label}
    </span>
  )
  const renderModuleToggle = (key: BacktestModuleKey) => (
    <Button
      type="text"
      size="small"
      onClick={(event) => {
        event.stopPropagation()
        toggleModule(key)
      }}
    >
      {collapsedModules[key] ? '▶' : '▼'}
    </Button>
  )
  const selectedTaskPayload = selectedTaskId
    ? payloadById[selectedTaskId] ?? tasksById[selectedTaskId]?.result?.effective_run_request ?? null
    : null
  const runningTaskCount = activeTaskIds.length
  const plateauTaskOptions = useMemo(
    () =>
      Object.values(plateauTasksById)
        .sort((left, right) => {
          const leftTs = Date.parse(left.progress.updated_at || left.progress.started_at || '')
          const rightTs = Date.parse(right.progress.updated_at || right.progress.started_at || '')
          return rightTs - leftTs
        })
        .map((task) => ({
          value: task.task_id,
          label: `${plateauTaskStatusLabel(task.status)} | ${task.task_id.slice(0, 12)} | ${task.progress.updated_at}`,
        })),
    [plateauTasksById],
  )
  const plateauTaskStatus = plateauSelectedTaskId ? plateauTasksById[plateauSelectedTaskId] ?? null : null
  const plateauTaskProgress = plateauTaskStatus?.progress
  const plateauResult = plateauTaskStatus?.result ?? null
  const plateauTaskRunningCount = plateauActiveTaskIds.length
  const strategyCatalogQuery = useQuery({
    queryKey: ['strategy-catalog'],
    queryFn: getStrategies,
    staleTime: 5 * 60_000,
  })
  const configQuery = useQuery({
    queryKey: ['config'],
    queryFn: getConfig,
    staleTime: 60_000,
  })
  const eventJudgmentCatalogQuery = useQuery({
    queryKey: ['event-judgment-profiles'],
    queryFn: getEventJudgmentProfiles,
    staleTime: 60_000,
  })
  const strategyItems = useMemo(
    () => filterVisibleStrategies(strategyCatalogQuery.data?.items),
    [strategyCatalogQuery.data?.items],
  )
  const strategyNameById = useMemo(
    () => new Map(strategyItems.map((item) => [item.strategy_id, item.name])),
    [strategyItems],
  )
  const defaultExecutionPathPreference =
    configQuery.data?.backtest_default_execution_path_preference ?? 'legacy'
  const activeEventJudgmentProfileId = eventJudgmentCatalogQuery.data?.active_profile_id || undefined
  const selectedStrategy = useMemo(
    () => strategyItems.find((item) => item.strategy_id === strategyId) ?? null,
    [strategyId, strategyItems],
  )
  const strategySupportsMatrix = selectedStrategy?.capabilities?.supports_matrix !== false
  const strategyForcesLegacyPath = selectedStrategy?.capabilities?.supports_matrix === false
  const strategySupportsSignalAgeFilter = selectedStrategy?.capabilities?.supports_signal_age_filter !== false
  const strategySupportsEntryDelay = selectedStrategy?.capabilities?.supports_entry_delay !== false
  const strategyUsesBuiltInEntrySignal = isEventIndependentBacktestStrategy(strategyId)
  const strategyAllowsOptionalExitEvents = allowsOptionalBacktestExitEvents(strategyId)
  const strategyParamsSchema = useMemo(
    () => parseStrategyParamSchema(selectedStrategy?.strategy_params_schema ?? {}),
    [selectedStrategy?.strategy_params_schema],
  )
  const strategyParamsDefaults = useMemo(
    () => normalizeStrategyParams(selectedStrategy?.strategy_params_defaults),
    [selectedStrategy?.strategy_params_defaults],
  )
  const strategyParamsPayload = useMemo(
    () => buildStrategyParamsPayload({
      schema: strategyParamsSchema,
      params: strategyParams,
      defaults: strategyParamsDefaults,
      includeDefaults: true,
    }),
    [strategyParams, strategyParamsDefaults, strategyParamsSchema],
  )

  useEffect(() => {
    if (executionPathPreferenceInitializedRef.current) return
    setExecutionPathPreference(defaultExecutionPathPreference)
    executionPathPreferenceInitializedRef.current = true
  }, [defaultExecutionPathPreference])

  useEffect(() => {
    if (strategyItems.length <= 0) return
    const allIds = new Set(strategyItems.map((item) => item.strategy_id))
    if (allIds.has(strategyId)) return
    const defaultId = resolveDefaultStrategyId(strategyItems, 'wyckoff_trend_v1')
    setStrategyId(defaultId)
    setStrategyParams(getSharedStrategyParams(defaultId))
  }, [strategyId, strategyItems])

  useEffect(() => {
    if (!selectedStrategy) return
    setStrategyParams((previous) => {
      const normalizedPrevious = normalizeStrategyParams(previous)
      const merged = {
        ...strategyParamsDefaults,
        ...normalizedPrevious,
      }
      const next = buildStrategyParamsPayload({
        schema: strategyParamsSchema,
        params: merged,
        defaults: strategyParamsDefaults,
        includeDefaults: true,
      })
      return JSON.stringify(next) === JSON.stringify(normalizedPrevious) ? previous : next
    })
  }, [selectedStrategy?.strategy_id, strategyParamsDefaults, strategyParamsSchema])

  useEffect(() => {
    if (strategySupportsEntryDelay) return
    setEntryDelayDays((previous) => (previous === 1 ? previous : 1))
    setDelayInvalidationEnabled((previous) => (previous ? false : previous))
  }, [strategySupportsEntryDelay])

  useEffect(() => {
    if (!strategyUsesBuiltInEntrySignal) return
    setEntryEvents((previous) => (previous.length === 0 ? previous : []))
    setMinEventCount((previous) => (previous === 0 ? previous : 0))
    setRequireSequence((previous) => (previous ? false : previous))
  }, [strategyUsesBuiltInEntrySignal])

  useEffect(() => {
    if (!strategyForcesLegacyPath) return
    if (executionPathPreference === 'legacy') return
    executionPathPreferenceInitializedRef.current = true
    setExecutionPathPreference('legacy')
  }, [executionPathPreference, strategyForcesLegacyPath])

  useEffect(() => {
    setSharedStrategyParams(strategyId, strategyParamsPayload)
  }, [strategyId, strategyParamsPayload])

  function applyRunMeta(nextRunId: string, asOfDate?: string) {
    setMode('trend_pool')
    setTrendStep('auto')
    setRunId(nextRunId)
    if (!asOfDate) return
    const parsed = dayjs(asOfDate)
    if (!parsed.isValid()) return
    setRange((current) => {
      const spanDays = Math.max(30, current[1].diff(current[0], 'day'))
      return [parsed.subtract(spanDays, 'day'), parsed]
    })
  }

  function readScreenerRunMetaFromStorage() {
    try {
      const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw) as {
        run_meta?: { runId?: unknown; asOfDate?: unknown }
        form_values?: { board_filters?: unknown }
      }
      const runIdText = typeof parsed?.run_meta?.runId === 'string' ? parsed.run_meta.runId.trim() : ''
      const asOfDateText =
        typeof parsed?.run_meta?.asOfDate === 'string' ? parsed.run_meta.asOfDate.trim() : ''
      const boardFilters = sanitizeBoardFilters(parsed?.form_values?.board_filters)
      if (!runIdText) return null
      return {
        runId: runIdText,
        asOfDate: asOfDateText || undefined,
        boardFilters: boardFilters.length > 0 ? boardFilters : undefined,
      }
    } catch {
      return null
    }
  }

  useEffect(() => {
    const draft: BacktestFormDraft = {
      mode,
      trend_step: trendStep,
      pool_roll_mode: poolRollMode,
      run_id: runId,
      board_filters: boardFilters,
      strategy_id: strategyId,
      strategy_params: strategyParamsPayload,
      date_from: range[0].format('YYYY-MM-DD'),
      date_to: range[1].format('YYYY-MM-DD'),
      window_days: windowDays,
      min_score: minScore,
      require_sequence: requireSequence,
      min_event_count: minEventCount,
      entry_events: entryEvents,
      exit_events: exitEvents,
      event_judgment_profile_id: activeEventJudgmentProfileId,
      execution_path_preference: strategyForcesLegacyPath ? 'legacy' : executionPathPreference,
      initial_capital: initialCapital,
      position_pct: toRatio(positionPctPercent, TRADE_BACKTEST_DEFAULTS.positionPct, 0.0001, 1),
      max_positions: maxPositions,
      stop_loss: toRatio(stopLossPercent, TRADE_BACKTEST_DEFAULTS.stopLoss, 0, 0.5),
      take_profit: toRatio(takeProfitPercent, TRADE_BACKTEST_DEFAULTS.takeProfit, 0, 1.5),
      trailing_stop_pct: toRatio(trailingStopPercent, TRADE_BACKTEST_DEFAULTS.trailingStopPct, 0, 0.5),
      intraday_trailing_enabled: intradayTrailingEnabled,
      daily_trailing_clear_enabled: dailyTrailingClearEnabled,
      intraday_trailing_reduce_ratio: toRatio(
        intradayTrailingReducePercent,
        TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio,
        0.1,
        1,
      ),
      daily_trailing_confirm_days: dailyTrailingConfirmDays,
      max_hold_days: maxHoldDays,
      fee_bps: feeBps,
      prioritize_signals: prioritizeSignals,
      priority_mode: priorityMode,
      priority_topk_per_day: priorityTopK,
      enforce_t1: enforceT1,
      entry_delay_days: entryDelayDays,
      delay_invalidation_enabled: delayInvalidationEnabled,
      max_symbols: maxSymbols,
      enable_advanced_analysis: enableAdvancedAnalysis,
      trades_page_size: tradePageSize,
    }
    persistBacktestDraft(draft)
  }, [
    mode,
    trendStep,
    poolRollMode,
    runId,
    boardFilters,
    strategyId,
    strategyParamsPayload,
    range,
    windowDays,
    minScore,
    requireSequence,
    minEventCount,
    entryEvents,
    exitEvents,
    activeEventJudgmentProfileId,
    executionPathPreference,
    initialCapital,
    positionPctPercent,
    maxPositions,
    stopLossPercent,
    takeProfitPercent,
    trailingStopPercent,
    intradayTrailingEnabled,
    dailyTrailingClearEnabled,
    intradayTrailingReducePercent,
    dailyTrailingConfirmDays,
    maxHoldDays,
    feeBps,
    prioritizeSignals,
    priorityMode,
    priorityTopK,
    enforceT1,
    entryDelayDays,
    delayInvalidationEnabled,
    maxSymbols,
    enableAdvancedAnalysis,
    tradePageSize,
  ])

  const startTaskMutation = useMutation({
    mutationFn: startBacktestTask,
    onSuccess: (payload, variables) => {
      enqueueTask(payload.task_id, variables.pool_roll_mode, variables)
      setSelectedTask(payload.task_id)
      setRunError(null)
      message.info('回测任务已提交，正在按日期滚动计算...')
    },
    onError: (error) => {
      const text = formatApiError(error)
      setRunError(text)
      message.error(text)
    },
  })

  const startPlateauTaskMutation = useMutation({
    mutationFn: startBacktestPlateauTask,
    onSuccess: (payload, variables) => {
      enqueuePlateauTask(payload.task_id, variables.sampling_mode ?? 'lhs')
      setSelectedPlateauTask(payload.task_id)
      setPlateauError(null)
      message.info('收益平原任务已提交，正在后台评估参数组合...')
    },
    onError: (error) => {
      const text = formatApiError(error)
      setPlateauError(text)
      message.error(text)
    },
  })

  const bindLatestRunMutation = useMutation({
    mutationFn: getLatestScreenerRun,
    onSuccess: (detail) => {
      applyRunMeta(detail.run_id, detail.as_of_date)
      const cached = readScreenerRunMetaFromStorage()
      if (cached?.boardFilters?.length) {
        setBoardFilters(cached.boardFilters)
      }
      message.success(`已带入后端最新筛选任务：${detail.run_id}`)
    },
    onError: (error) => {
      const cached = readScreenerRunMetaFromStorage()
      if (cached?.runId) {
        applyRunMeta(cached.runId, cached.asOfDate)
        if (cached.boardFilters?.length) {
          setBoardFilters(cached.boardFilters)
        }
        message.warning(`后端最新筛选获取失败，已回退本地缓存：${cached.runId}`)
        return
      }
      message.error(formatApiError(error))
    },
  })

  const controlPlateauTaskMutation = useMutation({
    mutationFn: async (payload: { action: 'pause' | 'resume' | 'cancel'; taskId: string }) => {
      if (payload.action === 'pause') return pauseBacktestPlateauTask(payload.taskId)
      if (payload.action === 'resume') return resumeBacktestPlateauTask(payload.taskId)
      return cancelBacktestPlateauTask(payload.taskId)
    },
    onSuccess: (status, variables) => {
      upsertPlateauTaskStatus(status)
      if (variables.action === 'pause') {
        message.info(`收益平原任务已暂停：${status.task_id}`)
      } else if (variables.action === 'resume') {
        message.success(`收益平原任务已继续：${status.task_id}`)
      } else {
        message.warning(`收益平原任务已停止：${status.task_id}`)
      }
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  const deletePlateauTaskMutation = useMutation({
    mutationFn: deleteBacktestPlateauTask,
    onSuccess: (payload) => {
      removePlateauTask(payload.task_id)
      message.success(`收益平原任务已删除：${payload.task_id}`)
    },
    onError: (error, taskId) => {
      if (error instanceof ApiError && error.code === 'BACKTEST_PLATEAU_TASK_NOT_FOUND') {
        const normalizedTaskId = String(taskId || '').trim()
        if (normalizedTaskId) {
          removePlateauTask(normalizedTaskId)
          message.warning(`任务 ${normalizedTaskId} 在后端不存在，已清理本地历史记录`)
          return
        }
      }
      message.error(formatApiError(error))
    },
  })

  const deleteBacktestTaskMutation = useMutation({
    mutationFn: deleteBacktestTask,
    onSuccess: (payload) => {
      removeTask(payload.task_id)
      message.success(`策略回测任务已删除：${payload.task_id}`)
    },
    onError: (error, taskId) => {
      if (error instanceof ApiError && error.code === 'BACKTEST_TASK_NOT_FOUND') {
        const normalizedTaskId = String(taskId || '').trim()
        if (normalizedTaskId) {
          removeTask(normalizedTaskId)
          message.warning(`任务 ${normalizedTaskId} 在后端不存在，已清理本地历史记录`)
          return
        }
      }
      message.error(formatApiError(error))
    },
  })

  const controlTaskMutation = useMutation({
    mutationFn: async (payload: { action: 'pause' | 'resume' | 'cancel'; taskId: string }) => {
      if (payload.action === 'pause') return pauseBacktestTask(payload.taskId)
      if (payload.action === 'resume') return resumeBacktestTask(payload.taskId)
      return cancelBacktestTask(payload.taskId)
    },
    onSuccess: (status, variables) => {
      upsertTaskStatus(status)
      if (variables.action === 'pause') {
        message.info(`任务已暂停：${status.task_id}`)
      } else if (variables.action === 'resume') {
        message.success(`任务已继续：${status.task_id}`)
      } else {
        message.warning(`任务已停止：${status.task_id}`)
      }
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  async function refreshReportLibrary(preferredReportId?: string | null) {
    setReportLibraryLoading(true)
    try {
      const response = await listBacktestReports()
      const rows = response.items
      setReportLibrary(rows)
      setSelectedReportId((current) => {
        const preferred = preferredReportId === undefined ? current : preferredReportId
        if (preferred && rows.some((item) => item.report_id === preferred)) return preferred
        return rows[0]?.report_id ?? null
      })
    } catch (error) {
      message.error(formatApiError(error))
    } finally {
      setReportLibraryLoading(false)
    }
  }

  async function refreshBacktestTaskHistory() {
    try {
      const response = await listBacktestTasks({ includeResult: true })
      const remoteTaskIds = new Set(response.items.map((row) => row.task_id))
      const localTasksById = useBacktestTaskStore.getState().tasksById
      for (const taskId of Object.keys(localTasksById)) {
        if (isImportedLocalTaskId(taskId, 'backtest')) continue
        if (!remoteTaskIds.has(taskId)) {
          removeTask(taskId)
        }
      }
      for (const row of response.items) {
        upsertTaskStatus(row)
        if (row.result?.effective_run_request) {
          upsertTaskPayload(row.task_id, row.result.effective_run_request)
        }
      }
    } catch (error) {
      message.warning(`策略回测历史加载失败：${formatApiError(error)}`)
    }
  }

  async function refreshPlateauTaskHistory() {
    setPlateauHistoryLoading(true)
    try {
      const response = await listBacktestPlateauTasks({ includeResult: true })
      for (const row of response.items) {
        upsertPlateauTaskStatus(row)
      }
    } catch (error) {
      message.warning(`收益平原历史加载失败：${formatApiError(error)}`)
    } finally {
      setPlateauHistoryLoading(false)
    }
  }

  const exportReportMutation = useMutation({
    mutationFn: buildBacktestReportPackage,
    onSuccess: (payload) => {
      const bytes = base64ToUint8Array(payload.file_base64)
      const safeBytes = new Uint8Array(bytes.byteLength)
      safeBytes.set(bytes)
      const blob = new Blob([safeBytes], { type: 'application/octet-stream' })
      downloadBlob(payload.file_name, blob)
      message.success(`回测报告已导出：${payload.file_name}`)
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  const importReportMutation = useMutation({
    mutationFn: importBacktestReportPackage,
    onSuccess: (payload) => {
      message.success(`报告导入成功：${payload.summary.report_id}`)
      void refreshReportLibrary(payload.summary.report_id)
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  const loadReportMutation = useMutation({
    mutationFn: getBacktestReport,
    onSuccess: (payload) => {
      const effectiveRunRequest = resolveEffectiveRunRequest(payload.run_request, payload.run_result)
      const taskId = buildImportedLocalTaskId(payload.summary.report_id, 'backtest')
      const status: BacktestTaskStatusResponse = {
        task_id: taskId,
        status: 'succeeded',
        progress: {
          mode: effectiveRunRequest.pool_roll_mode,
          current_date: payload.run_result.range.date_to,
          processed_dates: 1,
          total_dates: 1,
          percent: 100,
          message: `已加载导入报告（${payload.summary.report_id}）`,
          warning: null,
          stage_timings: [],
          started_at: payload.summary.first_imported_at,
          updated_at: payload.summary.last_imported_at,
        },
        result: payload.run_result,
        error: null,
        error_code: null,
      }
      upsertTaskStatus(status)
      upsertTaskPayload(taskId, effectiveRunRequest)
      setSelectedTask(taskId)
      if (payload.plateau_result) {
        const plateauTaskId = buildImportedLocalTaskId(payload.summary.report_id, 'plateau')
        const normalizedPlateauResult: BacktestPlateauResponse = {
          ...payload.plateau_result,
          correlations: resolvePlateauCorrelations(payload.plateau_result),
        }
        const plateauStatus: BacktestPlateauTaskStatusResponse = {
          task_id: plateauTaskId,
          status: 'succeeded',
          progress: {
            sampling_mode: 'lhs',
            processed_points: Number(normalizedPlateauResult.evaluated_combinations || 0),
            total_points: Number(normalizedPlateauResult.total_combinations || 0),
            percent: 100,
            message: `已加载导入平原结果（${payload.summary.report_id}）`,
            started_at: payload.summary.first_imported_at,
            updated_at: payload.summary.last_imported_at,
          },
          result: normalizedPlateauResult,
          error: null,
          error_code: null,
        }
        upsertPlateauTaskStatus(plateauStatus)
        setSelectedPlateauTask(plateauTaskId)
      }
      message.success(`已加载导入报告：${payload.summary.report_id}`)
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  const deleteReportMutation = useMutation({
    mutationFn: deleteBacktestReport,
    onSuccess: (payload) => {
      message.success(`已删除报告：${payload.report_id}`)
      void refreshReportLibrary()
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  function buildReportExportPayload() {
    if (!result) {
      message.warning('暂无可导出的回测结果')
      return null
    }
    if (!selectedTaskPayload) {
      message.warning('当前任务缺少参数快照（旧任务），请重新运行一次后再导出')
      return null
    }
    try {
      const effectiveRunRequest = resolveEffectiveRunRequest(selectedTaskPayload, result)
      const exportPlateauResult: BacktestPlateauResponse | null = plateauResult
        ? {
            ...plateauResult,
            correlations: resolvePlateauCorrelations(plateauResult),
          }
        : null
      const workbookBuffer = buildBacktestReportWorkbookBuffer(effectiveRunRequest, result, exportPlateauResult)
      const reportHtml = buildBacktestReportHtml(effectiveRunRequest, result, exportPlateauResult)
      const dateFrom = String(result.range.date_from || '').replaceAll('-', '')
      const dateTo = String(result.range.date_to || '').replaceAll('-', '')
      const fileBaseName = [
        'backtest_report',
        String(effectiveRunRequest.mode || 'backtest'),
        dateFrom || 'from',
        dateTo || 'to',
        dayjs().format('YYYYMMDD_HHmmss'),
      ]
        .join('_')
        .replace(/[^0-9A-Za-z_.-]/g, '_')
      return {
        runRequest: effectiveRunRequest,
        runResult: result,
        exportPlateauResult,
        plateauTaskId: exportPlateauResult ? plateauTaskStatus?.task_id ?? null : null,
        workbookBuffer,
        reportHtml,
        fileBaseName,
      }
    } catch (error) {
      message.error(formatApiError(error))
      return null
    }
  }

  function handleExportFtbt() {
    const payload = buildReportExportPayload()
    if (!payload) return
    const reportXlsxBase64 = bufferToBase64(payload.workbookBuffer)
    exportReportMutation.mutate({
      run_request: payload.runRequest,
      run_result: payload.runResult,
      report_html: payload.reportHtml,
      report_xlsx_base64: reportXlsxBase64,
      plateau_result: payload.exportPlateauResult,
      plateau_task_id: payload.plateauTaskId,
      app_name: 'Final Trade',
      app_version: String(import.meta.env.VITE_APP_VERSION || 'frontend'),
    })
  }

  function handleExportXlsxReport() {
    const payload = buildReportExportPayload()
    if (!payload) return
    const blob = new Blob([payload.workbookBuffer], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const fileName = `${payload.fileBaseName}.xlsx`
    downloadBlob(fileName, blob)
    message.success(`已导出 Excel 报告：${fileName}`)
  }

  function handleExportHtmlReport() {
    const payload = buildReportExportPayload()
    if (!payload) return
    const blob = new Blob([payload.reportHtml], { type: 'text/html;charset=utf-8' })
    const fileName = `${payload.fileBaseName}.html`
    downloadBlob(fileName, blob)
    message.success(`已导出 HTML 报告：${fileName}`)
  }

  function handleImportFtbt(file: File | null | undefined) {
    if (!file) return
    if (!String(file.name || '').toLowerCase().endsWith('.ftbt')) {
      message.warning('仅支持导入 .ftbt 文件')
      return
    }
    importReportMutation.mutate(file)
  }

  function handleLoadSelectedReport() {
    if (!selectedReportId) {
      message.warning('请先选择报告')
      return
    }
    loadReportMutation.mutate(selectedReportId)
  }

  function handleDeleteSelectedReport() {
    if (!selectedReportId) {
      message.warning('请先选择报告')
      return
    }
    deleteReportMutation.mutate(selectedReportId)
  }

  useEffect(() => {
    persistCollapsedModules(collapsedModules)
  }, [collapsedModules])

  useEffect(() => {
    void refreshReportLibrary()
  }, [])

  useEffect(() => {
    void refreshBacktestTaskHistory()
  }, [])

  useEffect(() => {
    void refreshPlateauTaskHistory()
  }, [])

  useEffect(() => {
    if (!plateauSelectedTaskId) return
    const current = plateauTasksById[plateauSelectedTaskId]
    if (!current || current.result || isImportedLocalTaskId(plateauSelectedTaskId, 'plateau')) return
    let active = true
    void getBacktestPlateauTask(plateauSelectedTaskId)
      .then((status) => {
        if (!active) return
        upsertPlateauTaskStatus(status)
      })
      .catch((error) => {
        if (!active) return
        if (isApiNotFoundError(error)) {
          removePlateauTask(plateauSelectedTaskId)
        }
      })
    return () => {
      active = false
    }
  }, [plateauSelectedTaskId, plateauTasksById, removePlateauTask, upsertPlateauTaskStatus])

  useEffect(() => {
    setTradePage(1)
  }, [result?.trades])

  useEffect(() => {
    setPlateauTradePage(1)
  }, [plateauTradeViewingRow?.__rowKey, plateauTradeViewResult?.trades])

  useEffect(() => {
    const draft: BacktestPlateauFormDraft = {
      sampling_mode: plateauSamplingMode,
      sample_points: plateauSamplePoints,
      random_seed: plateauRandomSeed,
      window_days_list_raw: plateauWindowListRaw,
      min_score_list_raw: plateauMinScoreListRaw,
      stop_loss_pct_list_raw: plateauStopLossPctListRaw,
      take_profit_pct_list_raw: plateauTakeProfitPctListRaw,
      trailing_stop_pct_list_raw: plateauTrailingStopPctListRaw,
      intraday_trailing_reduce_ratio_list_raw: plateauIntradayReducePctListRaw,
      max_positions_list_raw: plateauMaxPositionsListRaw,
      position_pct_list_raw: plateauPositionPctListRaw,
      max_symbols_list_raw: plateauMaxSymbolsListRaw,
      topk_list_raw: plateauTopKListRaw,
      heatmap_x_axis: plateauHeatmapXAxis,
      heatmap_y_axis: plateauHeatmapYAxis,
      heatmap_metric: plateauHeatmapMetric,
      heatmap_show_best_path: plateauHeatmapShowBestPath,
      heatmap_show_cell_label: plateauHeatmapShowCellLabel,
      table_sort_key: plateauTableSort.key,
      table_sort_order: plateauTableSort.order,
    }
    persistBacktestPlateauDraft(draft)
  }, [
    plateauSamplingMode,
    plateauSamplePoints,
    plateauRandomSeed,
    plateauWindowListRaw,
    plateauMinScoreListRaw,
    plateauStopLossPctListRaw,
    plateauTakeProfitPctListRaw,
    plateauTrailingStopPctListRaw,
    plateauIntradayReducePctListRaw,
    plateauMaxPositionsListRaw,
    plateauPositionPctListRaw,
    plateauMaxSymbolsListRaw,
    plateauTopKListRaw,
    plateauHeatmapXAxis,
    plateauHeatmapYAxis,
    plateauHeatmapMetric,
    plateauHeatmapShowBestPath,
    plateauHeatmapShowCellLabel,
    plateauTableSort,
  ])

  useEffect(() => {
    setPlateauApplyRank(1)
  }, [plateauResult?.generated_at])

  useEffect(() => {
    setPlateauHeatmapSelectedCoord(null)
    setPlateauBrushSelectedKeys([])
  }, [plateauResult?.generated_at, plateauHeatmapXAxis, plateauHeatmapYAxis, plateauHeatmapMetric])

  useEffect(() => {
    if (plateauCandidatePoints.length <= 0) {
      if (plateauCandidatePickRank !== 1) setPlateauCandidatePickRank(1)
      return
    }
    if (plateauCandidatePickRank < 1 || plateauCandidatePickRank > plateauCandidatePoints.length) {
      setPlateauCandidatePickRank(1)
    }
  }, [plateauCandidatePickRank, plateauCandidatePoints.length])

  useEffect(() => {
    persistBacktestPlateauPresets(plateauSavedPresets)
  }, [plateauSavedPresets])

  useEffect(() => {
    if (plateauSavedPresets.length <= 0) {
      if (plateauSavedPresetId !== null) setPlateauSavedPresetId(null)
      return
    }
    if (!plateauSavedPresetId || !plateauSavedPresets.some((item) => item.id === plateauSavedPresetId)) {
      setPlateauSavedPresetId(plateauSavedPresets[0].id)
    }
  }, [plateauSavedPresetId, plateauSavedPresets])

  useEffect(() => {
    if (selectedTaskId) return
    if (taskOptions.length <= 0) return
    const firstTaskId = String(taskOptions[0]?.value || '').trim()
    if (!firstTaskId) return
    setSelectedTask(firstTaskId)
  }, [selectedTaskId, setSelectedTask, taskOptions])

  useEffect(() => {
    if (plateauSelectedTaskId) return
    if (plateauTaskOptions.length <= 0) return
    const firstTaskId = String(plateauTaskOptions[0]?.value || '').trim()
    if (!firstTaskId) return
    setSelectedPlateauTask(firstTaskId)
  }, [plateauSelectedTaskId, plateauTaskOptions, setSelectedPlateauTask])

  const equityOption = useMemo(() => {
    const curve = result?.equity_curve ?? []
    return {
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curve.map((row) => row.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          type: 'line',
          smooth: true,
          data: curve.map((row) => row.equity),
          lineStyle: { width: 2, color: '#0f8b6f' },
          areaStyle: { color: 'rgba(15,139,111,0.16)' },
        },
      ],
      grid: { left: 48, right: 20, top: 24, bottom: 36 },
    }
  }, [result?.equity_curve])

  const drawdownOption = useMemo(() => {
    const curve = result?.drawdown_curve ?? []
    return {
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curve.map((row) => row.date) },
      yAxis: {
        type: 'value',
        axisLabel: { formatter: (value: number) => `${(value * 100).toFixed(1)}%` },
      },
      series: [
        {
          type: 'line',
          smooth: true,
          data: curve.map((row) => row.drawdown),
          lineStyle: { width: 2, color: '#c4473d' },
          areaStyle: { color: 'rgba(196,71,61,0.16)' },
        },
      ],
      grid: { left: 48, right: 20, top: 24, bottom: 36 },
    }
  }, [result?.drawdown_curve])

  const symbolPnlAggregates = useMemo(
    () => aggregateTradesBySymbol(result?.trades ?? []),
    [result?.trades],
  )

  const tradePnlChartOption = useMemo(
    () => buildTradePnlChartOption(symbolPnlAggregates, tradePnlChartType),
    [symbolPnlAggregates, tradePnlChartType],
  )

  const equityCurveOnEvents = useMemo(() => ({
    click: (params: { componentType?: string; name?: string }) => {
      if (params?.componentType !== 'series') return
      const clickedDate = params?.name
      if (!clickedDate) return
      setEquityDateDetailDate(clickedDate)
    },
  }), [])

  const plateauTradeEquityOption = useMemo(() => {
    const curve = plateauTradeViewResult?.equity_curve ?? []
    return {
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curve.map((row) => row.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          type: 'line',
          smooth: true,
          data: curve.map((row) => row.equity),
          lineStyle: { width: 2, color: '#0f8b6f' },
          areaStyle: { color: 'rgba(15,139,111,0.16)' },
        },
      ],
      grid: { left: 48, right: 20, top: 24, bottom: 36 },
    }
  }, [plateauTradeViewResult?.equity_curve])

  function buildBacktestPayload(effectiveBoardFilters?: BoardFilter[]) {
    return {
      mode,
      run_id: runId.trim() || undefined,
      trend_step: trendStep,
      pool_roll_mode: poolRollMode,
      board_filters: mode === 'trend_pool' ? effectiveBoardFilters : undefined,
      strategy_id: strategyId,
      strategy_params: strategyParamsPayload,
      date_from: range[0].format('YYYY-MM-DD'),
      date_to: range[1].format('YYYY-MM-DD'),
      window_days: windowDays,
      min_score: minScore,
      require_sequence: strategyUsesBuiltInEntrySignal ? false : requireSequence,
      min_event_count: strategyUsesBuiltInEntrySignal ? 0 : minEventCount,
      entry_events: strategyUsesBuiltInEntrySignal ? [] : entryEvents,
      exit_events: exitEvents,
      initial_capital: initialCapital,
      position_pct: toRatio(positionPctPercent, TRADE_BACKTEST_DEFAULTS.positionPct, 0.0001, 1),
      max_positions: maxPositions,
      stop_loss: toRatio(stopLossPercent, TRADE_BACKTEST_DEFAULTS.stopLoss, 0, 0.5),
      take_profit: toRatio(takeProfitPercent, TRADE_BACKTEST_DEFAULTS.takeProfit, 0, 1.5),
      trailing_stop_pct: toRatio(trailingStopPercent, TRADE_BACKTEST_DEFAULTS.trailingStopPct, 0, 0.5),
      intraday_trailing_enabled: intradayTrailingEnabled,
      daily_trailing_clear_enabled: dailyTrailingClearEnabled,
      intraday_trailing_reduce_ratio: toRatio(
        intradayTrailingReducePercent,
        TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio,
        0.1,
        1,
      ),
      daily_trailing_confirm_days: dailyTrailingConfirmDays,
      max_hold_days: maxHoldDays,
      fee_bps: feeBps,
      prioritize_signals: prioritizeSignals,
      priority_mode: prioritizeSignals ? priorityMode : TRADE_BACKTEST_DEFAULTS.priorityMode,
      priority_topk_per_day: prioritizeSignals ? priorityTopK : 0,
      enforce_t1: enforceT1,
      entry_delay_days: entryDelayDays,
      delay_invalidation_enabled: delayInvalidationEnabled,
      execution_path_preference: strategyForcesLegacyPath ? 'legacy' : executionPathPreference,
      matrix_event_semantic_version: 'aligned_wyckoff_v2' as const,
      max_symbols: maxSymbols,
      enable_advanced_analysis: enableAdvancedAnalysis,
      ...backfillPayloadExtrasRef.current,
    }
  }

  const pageAIContext = useMemo(
    () => ({
      page: 'backtest' as const,
      title: buildPageTitle(
        'backtest',
        selectedReportId ?? (runId.trim() ? runId.trim().slice(0, 12) : undefined),
      ),
      refs: {
        ...(runId.trim() ? { run_id: runId.trim() } : {}),
        ...(selectedReportId ? { report_id: selectedReportId } : {}),
        strategy_id: strategyId,
      },
      payload: {
        ...(result ? { run_result: result } : {}),
        strategy_id: strategyId,
        strategy_params: strategyParamsPayload,
        run_request: buildBacktestPayload(),
      },
    }),
    [selectedReportId, runId, strategyId, result, strategyParamsPayload, mode, trendStep, poolRollMode, boardFilters, range, windowDays, minScore, requireSequence, minEventCount, entryEvents, exitEvents, initialCapital, positionPctPercent, maxPositions, stopLossPercent, takeProfitPercent, trailingStopPercent, maxHoldDays, entryDelayDays, maxSymbols, enableAdvancedAnalysis],
  )
  useRegisterPageAIContext(pageAIContext)

  const lastApplied = useAIParameterApplyStore((state) => state.lastApplied)
  const clearLastApplied = useAIParameterApplyStore((state) => state.clearLastApplied)
  useEffect(() => {
    if (!lastApplied?.length) return
    for (const change of lastApplied) {
      const value = change.new_value
      if (change.target === 'backtest.stop_loss' && typeof value === 'number') {
        setStopLossPercent(toPercent(value))
      }
      if (change.target === 'backtest.take_profit' && typeof value === 'number') {
        setTakeProfitPercent(toPercent(value))
      }
      if (change.target === 'backtest.trailing_stop_pct' && typeof value === 'number') {
        setTrailingStopPercent(toPercent(value))
      }
      if (change.target === 'backtest.position_pct' && typeof value === 'number') {
        setPositionPctPercent(toPercent(value))
      }
      if (change.target === 'backtest.max_positions' && typeof value === 'number') {
        setMaxPositions(Math.round(value))
      }
      if (change.target === 'backtest.max_hold_days' && typeof value === 'number') {
        setMaxHoldDays(Math.round(value))
      }
    }
    clearLastApplied()
  }, [lastApplied, clearLastApplied])

  function handleRun() {
    if (runLoading) return
    if (!strategyUsesBuiltInEntrySignal && entryEvents.length === 0) {
      message.warning('请至少选择一个入场事件')
      return
    }
    if (!strategyAllowsOptionalExitEvents && exitEvents.length === 0) {
      message.warning('请至少选择一个离场事件')
      return
    }
    const cached = readScreenerRunMetaFromStorage()
    const effectiveBoardFilters = cached?.boardFilters?.length ? cached.boardFilters : boardFilters
    const shouldApplyBoardFilters = mode === 'trend_pool'
    if (shouldApplyBoardFilters && effectiveBoardFilters.length === 0) {
      message.warning('请至少选择一个板块后再回测')
      return
    }
    if (cached?.boardFilters?.length) {
      setBoardFilters(cached.boardFilters)
    }
    const payload = buildBacktestPayload(shouldApplyBoardFilters ? effectiveBoardFilters : undefined)
    setRunError(null)
    startTaskMutation.mutate(payload)
  }

  function handleBindLatestRunId() {
    if (bindLatestRunMutation.isPending) return
    bindLatestRunMutation.mutate()
  }

  function useDensePlateau(size: number) {
    const dense = denseRiskGrid(size)
    setPlateauSamplingMode('grid')
    setPlateauSamplePoints(dense.points)
    setPlateauStopLossPctListRaw(dense.stopLoss)
    setPlateauTakeProfitPctListRaw(dense.takeProfit)
    setPlateauWindowListRaw(''); setPlateauMinScoreListRaw('')
    setPlateauTrailingStopPctListRaw(''); setPlateauIntradayReducePctListRaw('')
    setPlateauMaxPositionsListRaw(''); setPlateauPositionPctListRaw('')
    setPlateauMaxSymbolsListRaw(''); setPlateauTopKListRaw('')
    setPlateauHeatmapXAxis('stop_loss'); setPlateauHeatmapYAxis('take_profit')
    message.success(`已生成 ${size}×${size} 完整网格，共${dense.points}点；其余参数使用当前回测设置`)
  }
  const plateauGridPreview = (() => {
    try {
      return { ...gridSummary({
        window: numericAxis(plateauWindowListRaw,{integer:true,min:20,max:240}),
        score: numericAxis(plateauMinScoreListRaw,{min:0,max:100,precision:4}),
        stop: numericAxis(plateauStopLossPctListRaw,{min:0,max:50,precision:4}),
        take: numericAxis(plateauTakeProfitPctListRaw,{min:0,max:150,precision:4}),
        trailing: numericAxis(plateauTrailingStopPctListRaw,{min:0,max:50,precision:4}),
        reduce: numericAxis(plateauIntradayReducePctListRaw,{min:10,max:100,precision:4}),
        positions: numericAxis(plateauMaxPositionsListRaw,{integer:true,min:1,max:100}),
        position: numericAxis(plateauPositionPctListRaw,{min:1,max:100,precision:4}),
        symbols: numericAxis(plateauMaxSymbolsListRaw,{integer:true,min:20,max:2000}),
        topk: numericAxis(plateauTopKListRaw,{integer:true,min:0,max:500}),
      }), error:'' }
    } catch(error) { return {total:0,dimensions:'',isSurface:false,exceedsLimit:false,error:error instanceof Error ? error.message : '参数无效'} }
  })()

  function handleRunPlateau() {
    try { runPlateauChecked() } catch (error) { message.error(error instanceof Error ? error.message : '请检查平原参数') }
  }

  function runPlateauChecked() {
    if (startPlateauTaskMutation.isPending) return
    if (!strategyUsesBuiltInEntrySignal && entryEvents.length === 0) {
      message.warning('请至少选择一个入场事件')
      return
    }
    if (!strategyAllowsOptionalExitEvents && exitEvents.length === 0) {
      message.warning('请至少选择一个离场事件')
      return
    }
    const cached = readScreenerRunMetaFromStorage()
    const effectiveBoardFilters = cached?.boardFilters?.length ? cached.boardFilters : boardFilters
    if (mode === 'trend_pool' && effectiveBoardFilters.length === 0) {
      message.warning('请至少选择一个板块后再执行收益平原')
      return
    }
    if (cached?.boardFilters?.length) {
      setBoardFilters(cached.boardFilters)
    }
    const stopLossPctList = parseNumericList(plateauStopLossPctListRaw, {
      integer: false,
      min: 0,
      max: 50,
      precision: 4,
    })
    const takeProfitPctList = parseNumericList(plateauTakeProfitPctListRaw, {
      integer: false,
      min: 0,
      max: 150,
      precision: 4,
    })
    const trailingStopPctList = parseNumericList(plateauTrailingStopPctListRaw, {
      integer: false,
      min: 0,
      max: 50,
      precision: 4,
    })
    const intradayReducePctList = parseNumericList(plateauIntradayReducePctListRaw, {
      integer: false,
      min: 10,
      max: 100,
      precision: 4,
    })
    const positionPctList = parseNumericList(plateauPositionPctListRaw, {
      integer: false,
      min: 1,
      max: 100,
      precision: 4,
    })
    const samplePoints = Math.max(1, Math.min(2000, Number(plateauSamplePoints || 120)))
    const payload = {
      base_payload: buildBacktestPayload(mode === 'trend_pool' ? effectiveBoardFilters : undefined),
      sampling_mode: plateauSamplingMode,
      window_days_list: parseNumericList(plateauWindowListRaw, {
        integer: true,
        min: 20,
        max: 240,
      }),
      min_score_list: parseNumericList(plateauMinScoreListRaw, {
        integer: false,
        min: 0,
        max: 100,
        precision: 4,
      }),
      stop_loss_list: stopLossPctList.map((item) => Number((item / 100).toFixed(6))),
      take_profit_list: takeProfitPctList.map((item) => Number((item / 100).toFixed(6))),
      trailing_stop_pct_list: trailingStopPctList.map((item) => Number((item / 100).toFixed(6))),
      intraday_trailing_reduce_ratio_list: intradayReducePctList.map((item) =>
        Number((Math.max(10, Math.min(100, item)) / 100).toFixed(6)),
      ),
      max_positions_list: parseNumericList(plateauMaxPositionsListRaw, {
        integer: true,
        min: 1,
        max: 100,
      }),
      position_pct_list: positionPctList.map((item) => Number((item / 100).toFixed(6))),
      max_symbols_list: parseNumericList(plateauMaxSymbolsListRaw, {
        integer: true,
        min: 20,
        max: 2000,
      }),
      priority_topk_per_day_list: parseNumericList(plateauTopKListRaw, {
        integer: true,
        min: 0,
        max: 500,
      }),
      sample_points: samplePoints,
      random_seed: plateauRandomSeed ?? undefined,
      max_points: samplePoints,
    }
    const summary = gridSummary(Object.fromEntries(Object.entries(payload).filter(([key,value]) => key.endsWith('_list') && Array.isArray(value))) as Record<string,number[]>)
    if (!summary.isSurface) {
      message.warning('至少设置两个变化的参数轴，才能扫描一整片平原；可点击上方“一键生成完整平原”')
      return
    }
    if (plateauSamplingMode === 'grid') {
      if (summary.exceedsLimit) { message.warning(`完整网格共 ${summary.total} 点，超过单次2000点，请缩小范围或改用区间采样`); return }
      payload.sample_points = summary.total
      payload.max_points = summary.total
    }
    setPlateauError(null)
    startPlateauTaskMutation.mutate(payload)
  }

  function applyBacktestRunRequestToForm(runRequest: BacktestRunRequest) {
    const nextStrategyId = (
      String(runRequest.strategy_id || '').trim()
      || resolveDefaultStrategyId(strategyItems, strategyId)
    ) as StrategyId
    const nextStrategyParams = normalizeStrategyParams(runRequest.strategy_params)
    const nextBoardFilters = sanitizeBoardFilters(runRequest.board_filters)
    const nextDateFrom = dayjs(runRequest.date_from)
    const nextDateTo = dayjs(runRequest.date_to)

    setMode(runRequest.mode)
    setTrendStep(runRequest.trend_step)
    setPoolRollMode(runRequest.pool_roll_mode)
    setRunId(String(runRequest.run_id ?? ''))
    if (runRequest.mode === 'trend_pool') {
      setBoardFilters(nextBoardFilters.length > 0 ? nextBoardFilters : BACKTEST_DEFAULT_BOARD_FILTERS)
    } else {
      setBoardFilters(nextBoardFilters)
    }
    setStrategyId(nextStrategyId)
    setStrategyParams(nextStrategyParams)
    if (nextDateFrom.isValid() && nextDateTo.isValid()) {
      setRange([nextDateFrom, nextDateTo])
    }
    if (Array.isArray(runRequest.entry_events)) {
      setEntryEvents(runRequest.entry_events)
    }
    if (Array.isArray(runRequest.exit_events)) {
      setExitEvents(runRequest.exit_events)
    }
    setInitialCapital(Number(runRequest.initial_capital))
    setPositionPctPercent(toPercent(Number(runRequest.position_pct)))
    setMaxPositions(Number(runRequest.max_positions))
    setStopLossPercent(toPercent(Number(runRequest.stop_loss)))
    setTakeProfitPercent(toPercent(Number(runRequest.take_profit)))
    setTrailingStopPercent(toPercent(Number(runRequest.trailing_stop_pct ?? 0)))
    setIntradayTrailingEnabled(runRequest.intraday_trailing_enabled !== false)
    setDailyTrailingClearEnabled(runRequest.daily_trailing_clear_enabled !== false)
    setIntradayTrailingReducePercent(
      toPercent(Number(runRequest.intraday_trailing_reduce_ratio ?? TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio)),
    )
    setDailyTrailingConfirmDays(
      Math.max(1, Math.min(5, Number(runRequest.daily_trailing_confirm_days ?? TRADE_BACKTEST_DEFAULTS.dailyTrailingConfirmDays))),
    )
    setMaxHoldDays(Number(runRequest.max_hold_days))
    setFeeBps(Number(runRequest.fee_bps))
    setWindowDays(Number(runRequest.window_days))
    setMinScore(Number(runRequest.min_score))
    setMinEventCount(Number(runRequest.min_event_count))
    setRequireSequence(Boolean(runRequest.require_sequence))
      setPrioritizeSignals(Boolean(runRequest.prioritize_signals))
      setPriorityMode(runRequest.priority_mode)
      setPriorityTopK(Number(runRequest.priority_topk_per_day))
      setExecutionPathPreference(
        (runRequest.execution_path_preference ?? defaultExecutionPathPreference) as BacktestExecutionPathPreference,
      )
      executionPathPreferenceInitializedRef.current = true
      setEnforceT1(Boolean(runRequest.enforce_t1))
    setEntryDelayDays(Math.max(1, Number(runRequest.entry_delay_days ?? 1)))
    setDelayInvalidationEnabled(runRequest.delay_invalidation_enabled !== false)
    setMaxSymbols(Number(runRequest.max_symbols))
    setEnableAdvancedAnalysis(runRequest.enable_advanced_analysis !== false)
    backfillPayloadExtrasRef.current = {
      event_judgment_profile_id: runRequest.event_judgment_profile_id,
      event_judgment_profile_hash: runRequest.event_judgment_profile_hash,
      event_judgment_profile_snapshot: runRequest.event_judgment_profile_snapshot,
      matrix_event_semantic_version: runRequest.matrix_event_semantic_version,
    }
  }

  function saveBacktestHistoryPreset(row: BacktestTaskStatusResponse) {
    if (row.status !== 'succeeded' || !row.result) {
      message.warning('仅支持保存已成功完成的回测任务参数。')
      return
    }
    const payload = resolveBacktestHistoryRunRequest(row, payloadById)
    if (!payload) {
      message.warning('该任务缺少可保存的回测参数。')
      return
    }
    const point = buildPlateauPointFromBacktestHistory(payload, row.result)
    const strategyId = String(payload.strategy_id || '').trim()
    const strategyName = strategyNameById.get(strategyId) || strategyId || '默认策略'
    const rangeLabel = formatBacktestDateRangeLabel(payload)
    const totalReturn = Number(row.result.stats.total_return)
    const presetName = `回测历史 | ${strategyName} | ${rangeLabel} | 收益 ${formatPct(totalReturn)}`
    const paramsKey = buildBacktestRunRequestKey(payload)
    const nowText = dayjs().format('YYYY-MM-DD HH:mm:ss')
    let persistedId = ''
    setPlateauSavedPresets((prev) => {
      const idx = prev.findIndex(
        (item) =>
          item.source === 'backtest_history'
          && item.run_request
          && buildBacktestRunRequestKey(item.run_request) === paramsKey,
      )
      if (idx >= 0) {
        const next = [...prev]
        const existing = next[idx]
        persistedId = existing.id
        next[idx] = {
          ...existing,
          name: presetName,
          saved_at: nowText,
          source: 'backtest_history',
          point,
          run_request: payload,
          stats_summary: {
            total_return: totalReturn,
            max_drawdown: Number(row.result?.stats.max_drawdown ?? 0),
          },
        }
        return next
      }
      const created: BacktestPlateauPreset = {
        id: `bh_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
        name: presetName,
        saved_at: nowText,
        source: 'backtest_history',
        point,
        run_request: payload,
        stats_summary: {
          total_return: totalReturn,
          max_drawdown: Number(row.result?.stats.max_drawdown ?? 0),
        },
      }
      persistedId = created.id
      return [created, ...prev].slice(0, 300)
    })
    if (persistedId) {
      setPlateauSavedPresetId(persistedId)
    }
    message.success(`已保存到参数收藏库：${presetName}`)
  }

  async function applyBacktestHistoryToForm(row: BacktestTaskStatusResponse) {
    if (row.status !== 'succeeded') {
      message.warning('仅支持回填已成功完成的回测任务参数。')
      return
    }
    try {
      const status = await getBacktestTask(row.task_id)
      upsertTaskStatus(status)
      const effective = status.result?.effective_run_request
      if (effective) {
        upsertTaskPayload(row.task_id, effective)
      }
      const mergedPayloadById = effective
        ? { ...payloadById, [row.task_id]: effective }
        : payloadById
      const runRequest = resolveBacktestHistoryRunRequest(
        { ...row, result: status.result ?? row.result },
        mergedPayloadById,
      )
      if (!runRequest) {
        message.warning('该任务缺少可回填的回测参数。')
        return
      }
      applyBacktestRunRequestToForm(runRequest)
      setRunError(null)
      message.success(
        effective
          ? '已回填本次回测实际执行参数。'
          : '已回填任务参数（该任务未保存完整执行快照，可能与当时略有差异）。',
      )
    } catch (error) {
      message.error(`加载任务参数失败：${formatApiError(error)}`)
    }
  }

  function buildPlateauPointRunRequest(
    point: BacktestPlateauPoint,
    basePayload?: BacktestRunRequest | null,
  ): BacktestRunRequest | null {
    if (point.error || !basePayload) return null
    return buildRunRequestFromPlateauParams(basePayload, point.params)
  }

  async function loadPlateauPointDetail(
    taskId: string | null | undefined,
    point: BacktestPlateauPoint,
  ): Promise<BacktestPlateauPointDetailResponse | null> {
    const normalizedTaskId = String(taskId || '').trim()
    const detailKey = String(point.detail_key || '').trim()
    if (!normalizedTaskId || !detailKey) return null
    const cacheKey = `${normalizedTaskId}:${detailKey}`
    const cached = plateauPointDetailCacheRef.current.get(cacheKey)
    if (cached) return cached
    try {
      const detail = await getBacktestPlateauPointDetail(normalizedTaskId, detailKey)
      plateauPointDetailCacheRef.current.set(cacheKey, detail)
      return detail
    } catch (error) {
      if (error instanceof ApiError && error.code === 'BACKTEST_PLATEAU_POINT_DETAIL_NOT_FOUND') {
        return null
      }
      throw error
    }
  }

  async function resolvePlateauPointRunArtifact(
    point: BacktestPlateauPoint,
    options: {
      actionLabel: string
      taskId?: string | null
      basePayload?: BacktestRunRequest | null
      notifyOnRerunFallback?: boolean
    },
  ): Promise<{ runRequest: BacktestRunRequest; runResult: BacktestResponse; source: 'persisted' | 'rerun' } | null> {
    if (point.error) {
      message.warning(`该参数组评估失败，无法${options.actionLabel}`)
      return null
    }
    const detail = await loadPlateauPointDetail(options.taskId, point)
    if (detail) {
      return {
        runRequest: detail.run_request,
        runResult: detail.run_result,
        source: 'persisted',
      }
    }
    const runRequest = buildPlateauPointRunRequest(point, options.basePayload)
    if (!runRequest) {
      message.warning(`当前收益平原结果缺少完整上下文，无法${options.actionLabel}`)
      return null
    }
    if (options.notifyOnRerunFallback) {
      message.warning('未找到已持久化的参数组明细，正在临时重算一次。')
    }
    const runResult = await runBacktest(runRequest)
    return { runRequest, runResult, source: 'rerun' }
  }

  async function handleViewPlateauTradeRecords(row: BacktestPlateauTableRow) {
    if (plateauTradeViewLoading) return
    setPlateauTradeViewingRow(row)
    setPlateauTradeViewResult(null)
    setPlateauTradeViewError(null)
    setPlateauTradeViewLoading(true)
    try {
      const resolved = await resolvePlateauPointRunArtifact(row, {
        actionLabel: '查看交易记录',
        taskId: plateauTaskStatus?.task_id ?? null,
        basePayload: plateauResult?.base_payload ?? null,
        notifyOnRerunFallback: true,
      })
      if (resolved) {
        setPlateauTradeViewResult(resolved.runResult)
      }
    } catch (error) {
      setPlateauTradeViewError(formatApiError(error))
    } finally {
      setPlateauTradeViewLoading(false)
    }
  }

  async function handleExportPlateauTradeRecords(row: BacktestPlateauTableRow) {
    if (plateauExportingRowKey) return
    setPlateauExportingRowKey(row.__rowKey)
    try {
      const resolved = await resolvePlateauPointRunArtifact(row, {
        actionLabel: '导出交易记录',
        taskId: plateauTaskStatus?.task_id ?? null,
        basePayload: plateauResult?.base_payload ?? null,
        notifyOnRerunFallback: true,
      })
      if (!resolved) return
      const workbookBuffer = buildPlateauTradeExportWorkbookBuffer(
        resolved.runRequest,
        resolved.runResult,
        row,
        row.__rank,
      )
      const blob = new Blob([workbookBuffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      })
      const safeRunId = (resolved.runRequest.run_id ?? '').trim().replace(/[^0-9a-zA-Z_-]/g, '')
      const runSuffix = safeRunId ? `_run_${safeRunId.slice(0, 16)}` : ''
      const fileName = `收益平原第${row.__rank}名_交易记录${runSuffix}_${dayjs().format('YYYYMMDD_HHmmss')}.xlsx`
      downloadBlob(fileName, blob)
      message.success(`已导出第${row.__rank}名参数组交易记录：${fileName}`)
    } catch (error) {
      message.error(formatApiError(error))
    } finally {
      setPlateauExportingRowKey(null)
    }
  }

  function applyPlateauPointToForm(
    point: BacktestPlateauPoint,
    rankLabel?: string,
    runRequest?: BacktestRunRequest | null,
  ) {
    if (point.error) {
      message.warning('该参数组评估失败，无法回填')
      return
    }
    const effectiveRunRequest = runRequest ?? buildPlateauPointRunRequest(point, plateauResult?.base_payload ?? null)
    const prefix = rankLabel ? `${rankLabel}参数已回填` : '参数已回填'
    if (effectiveRunRequest) {
      applyBacktestRunRequestToForm(effectiveRunRequest)
      setRunError(null)
      const strategyText = String(effectiveRunRequest.strategy_id || '').trim() || '默认策略'
      message.success(`${prefix}：已包含策略=${strategyText}、滚动=${effectiveRunRequest.pool_roll_mode}`)
      return
    }
    setWindowDays(Number(point.params.window_days))
    setMinScore(Number(point.params.min_score))
    setStopLossPercent(toPercent(Number(point.params.stop_loss)))
    setTakeProfitPercent(toPercent(Number(point.params.take_profit)))
    setTrailingStopPercent(toPercent(Number(point.params.trailing_stop_pct)))
    setIntradayTrailingReducePercent(
      toPercent(Number(point.params.intraday_trailing_reduce_ratio ?? TRADE_BACKTEST_DEFAULTS.intradayTrailingReduceRatio)),
    )
    setMaxPositions(Number(point.params.max_positions))
    setPositionPctPercent(toPercent(Number(point.params.position_pct)))
    setMaxSymbols(Number(point.params.max_symbols))
    setPriorityTopK(Number(point.params.priority_topk_per_day))
    setRunError(null)
    message.warning(`${prefix}：仅回填了参数组数值，策略/滚动模式等上下文缺失`)
  }

  function handleExportPlateauPointTrades(row: BacktestPlateauTableRow) {
    void handleExportPlateauTradeRecords(row)
  }

  const taskRunning = taskStatus?.status === 'running' || taskStatus?.status === 'pending' || taskStatus?.status === 'paused'
  const runLoading = startTaskMutation.isPending
  const plateauLoading = startPlateauTaskMutation.isPending
  const plateauTaskRunning =
    plateauTaskStatus?.status === 'running'
    || plateauTaskStatus?.status === 'pending'
    || plateauTaskStatus?.status === 'paused'
  const effectivePlateauError =
    plateauError
    || (plateauTaskStatus?.status === 'failed'
      ? (plateauTaskStatus.error?.trim() || '收益平原任务失败')
      : null)
  const plateauRecommendedPoint = plateauRecommendation(plateauResult)
  const plateauPeakPoint = plateauResult?.peak_point ?? null
  const plateauRegions = isLegacyRhythmResult(plateauResult?.base_payload.strategy_id, plateauResult?.strategy_version) ? [] : plateauResult?.regions ?? []
  const plateauSummaryPoint = plateauRecommendedPoint
  const initializedPlateauAxes = useRef('')
  useEffect(() => {
    if (!plateauResult) return
    const key = `${plateauSelectedTaskId}:${selectedReportId}:${plateauResult.generated_at}`
    if (initializedPlateauAxes.current === key) return
    initializedPlateauAxes.current = key
    const [x, y] = resolvePlateauAxes(plateauResult.points, PLATEAU_AXIS_OPTIONS.map(axis => axis.value), plateauHeatmapXAxis, plateauHeatmapYAxis)
    setPlateauHeatmapXAxis(x as PlateauAxisKey)
    setPlateauHeatmapYAxis(y as PlateauAxisKey)
  }, [plateauResult, plateauSelectedTaskId, selectedReportId, plateauHeatmapXAxis, plateauHeatmapYAxis])
  const plateauValidPoints = useMemo(
    () => (plateauResult?.points ?? []).filter((row) => !row.error),
    [plateauResult?.points],
  )
  const plateauCorrelationRows = useMemo(
    () => resolvePlateauCorrelations(plateauResult),
    [plateauResult],
  )
  const plateauCorrelationColumns = useMemo<ColumnsType<BacktestPlateauCorrelationRow>>(
    () => [
      {
        title: '参数',
        dataIndex: 'parameter_label',
        width: 180,
      },
      {
        title: '与评分相关',
        width: 160,
        render: (_value, row) => {
          const direction = classifyCorrelationDirection(row.score_corr)
          return (
            <Space size={6}>
              <span>{formatSigned(row.score_corr)}</span>
              <Tag color={correlationDirectionTagColor(direction)}>{direction}</Tag>
            </Space>
          )
        },
      },
      {
        title: '与收益相关',
        width: 160,
        render: (_value, row) => {
          const direction = classifyCorrelationDirection(row.total_return_corr)
          return (
            <Space size={6}>
              <span>{formatSigned(row.total_return_corr)}</span>
              <Tag color={correlationDirectionTagColor(direction)}>{direction}</Tag>
            </Space>
          )
        },
      },
      {
        title: '与胜率相关',
        width: 160,
        render: (_value, row) => {
          const direction = classifyCorrelationDirection(row.win_rate_corr)
          return (
            <Space size={6}>
              <span>{formatSigned(row.win_rate_corr)}</span>
              <Tag color={correlationDirectionTagColor(direction)}>{direction}</Tag>
            </Space>
          )
        },
      },
    ],
    [],
  )
  const regimeColumns = useMemo<ColumnsType<BacktestRegimeBucket>>(
    () => [
      {
        title: '状态',
        dataIndex: 'label',
        width: 120,
      },
      {
        title: '交易数',
        dataIndex: 'trade_count',
        width: 90,
      },
      {
        title: '胜率',
        dataIndex: 'win_rate',
        width: 100,
        render: (value: number) => formatPct(Number(value)),
      },
      {
        title: '总收益',
        dataIndex: 'total_return',
        width: 110,
        render: (value: number) => formatPct(Number(value)),
      },
      {
        title: '平均单笔',
        dataIndex: 'avg_pnl_ratio',
        width: 110,
        render: (value: number) => formatPct(Number(value)),
      },
      {
        title: '最大回撤',
        dataIndex: 'max_drawdown',
        width: 110,
        render: (value: number) => formatPct(Number(value)),
      },
    ],
    [],
  )
  const walkForwardColumns = useMemo<ColumnsType<BacktestWalkForwardReport['folds'][number]>>(
    () => [
      {
        title: 'Fold',
        dataIndex: 'fold_index',
        width: 70,
      },
      {
        title: '训练区间',
        width: 220,
        render: (_value, row) => `${row.train_date_from} ~ ${row.train_date_to}`,
      },
      {
        title: '测试区间',
        width: 220,
        render: (_value, row) => `${row.test_date_from} ~ ${row.test_date_to}`,
      },
      {
        title: '训练评分',
        dataIndex: 'train_score',
        width: 100,
        render: (value: number) => Number(value).toFixed(3),
      },
      {
        title: '测试评分',
        dataIndex: 'test_score',
        width: 100,
        render: (value: number) => Number(value).toFixed(3),
      },
      {
        title: '测试收益',
        width: 110,
        render: (_value, row) => formatPct(Number(row.test_stats.total_return)),
      },
      {
        title: '测试胜率',
        width: 110,
        render: (_value, row) => formatPct(Number(row.test_stats.win_rate)),
      },
      {
        title: '入选参数',
        width: 320,
        render: (_value, row) => {
          const p = row.selected_params
          return `window=${p.window_days}, min=${Number(p.min_score).toFixed(2)}, sl=${(Number(p.stop_loss) * 100).toFixed(2)}%, tp=${(Number(p.take_profit) * 100).toFixed(2)}%, ts=${(Number(p.trailing_stop_pct) * 100).toFixed(2)}%`
        },
      },
    ],
    [],
  )
  const plateauTopRankOptions = useMemo(
    () =>
      plateauValidPoints.slice(0, 20).map((row, idx) => ({
        value: idx + 1,
        label: `第${idx + 1}名 | 平原分 ${getPlateauPrimaryScore(row).toFixed(1)} | 收益 ${formatPct(row.stats.total_return)}`,
      })),
    [plateauValidPoints],
  )
  const plateauTableRows = useMemo<BacktestPlateauTableRow[]>(
    () =>
      (plateauResult?.points ?? []).map((row, index) => ({
        ...row,
        __rank: index + 1,
        __rowKey: buildPlateauRowKey(row, index),
      })),
    [plateauResult?.points],
  )
  const plateauUsingDefaultSort = (
    plateauTableSort.key === DEFAULT_PLATEAU_TABLE_SORT_STATE.key
    && plateauTableSort.order === DEFAULT_PLATEAU_TABLE_SORT_STATE.order
  )
  const selectedRankPoint = plateauValidPoints[plateauApplyRank - 1] ?? null
  const plateauColumnsWithAction: ColumnsType<BacktestPlateauTableRow> = [
    ...plateauColumns.map((column) => {
      const columnKey = column.key
      if (!isPlateauTableSortKey(columnKey)) return column
      return {
        ...column,
        sortOrder: plateauTableSort.key === columnKey ? plateauTableSort.order : null,
      }
    }),
    {
      title: '操作',
      key: 'action',
      width: 300,
      render: (_value, row) => {
        const exportingCurrentRow = plateauExportingRowKey === row.__rowKey
        const exportingOtherRow = plateauExportingRowKey !== null && plateauExportingRowKey !== row.__rowKey
        return (
          <Space size={6}>
            <Button
              size="small"
              loading={plateauTradeViewLoading && plateauTradeViewingRow?.__rowKey === row.__rowKey}
              disabled={plateauTradeViewLoading && plateauTradeViewingRow?.__rowKey !== row.__rowKey}
              onClick={() => {
                void handleViewPlateauTradeRecords(row)
              }}
            >
              查看交易记录
            </Button>
            <Button
              size="small"
              disabled={Boolean(row.error)}
              onClick={() => applyPlateauPointToForm(row, `第${row.__rank}名`)}
            >
              回填
            </Button>
            <Button
              size="small"
              disabled={Boolean(row.error)}
              onClick={() => savePlateauPreset(row, `第${row.__rank}名`)}
            >
              保存
            </Button>
            <Button
              size="small"
              type="primary"
              ghost
              loading={exportingCurrentRow}
              disabled={Boolean(row.error) || exportingOtherRow}
              onClick={() => {
                handleExportPlateauPointTrades(row)
              }}
            >
              导出交易记录
            </Button>
          </Space>
        )
      },
    },
  ]
  const handlePlateauTableChange = (
    _pagination: unknown,
    _filters: Record<string, unknown>,
    sorter: SorterResult<BacktestPlateauTableRow> | SorterResult<BacktestPlateauTableRow>[],
  ) => {
    const nextSort = resolvePlateauTableSortState(sorter)
    if (nextSort) {
      setPlateauTableSort(nextSort)
    }
  }
  const handleResetPlateauTableSort = () => {
    setPlateauTableSort(DEFAULT_PLATEAU_TABLE_SORT_STATE)
  }
  const backtestHistoryRows = useMemo(
    () =>
      Object.values(tasksById).sort((left, right) => {
        const leftTs = Date.parse(left.progress.updated_at || left.progress.started_at || '')
        const rightTs = Date.parse(right.progress.updated_at || right.progress.started_at || '')
        return rightTs - leftTs
      }),
    [tasksById],
  )
  const backtestHistoryColumns = useMemo<ColumnsType<BacktestTaskStatusResponse>>(
    () => [
      {
        title: '完成时间',
        width: 180,
        render: (_value, row) => row.progress.updated_at || row.progress.started_at || '--',
      },
      {
        title: '策略名称',
        width: 180,
        render: (_value, row) => {
          const payload = payloadById[row.task_id] ?? row.result?.effective_run_request
          const strategyId = String(payload?.strategy_id || '').trim()
          const strategyName = strategyNameById.get(strategyId)
          if (strategyName) return strategyName
          if (strategyId) return strategyId
          return '--'
        },
      },
      {
        title: '回测区间',
        width: 240,
        render: (_value, row) => {
          const payload = payloadById[row.task_id] ?? row.result?.effective_run_request
          return formatBacktestDateRangeLabel(payload)
        },
      },
      {
        title: '核心参数摘要',
        width: 460,
        render: (_value, row) => {
          const payload = resolveBacktestHistoryRunRequest(row, payloadById)
          if (!payload) return '--'
          return formatBacktestRunRequestSummary(payload)
        },
      },
      {
        title: '收益率',
        width: 110,
        render: (_value, row) => (row.result ? formatPct(Number(row.result.stats.total_return)) : '--'),
      },
      {
        title: '回撤',
        width: 110,
        render: (_value, row) => (row.result ? formatPct(Number(row.result.stats.max_drawdown)) : '--'),
      },
      {
        title: '状态',
        width: 100,
        render: (_value, row) => <Tag color={taskStatusColor(row.status)}>{taskStatusLabel(row.status)}</Tag>,
      },
      {
        title: '操作',
        width: 540,
        render: (_value, row) => {
          const controlTaskId = controlTaskMutation.variables?.taskId
          const controlAction = controlTaskMutation.variables?.action
          const controlLoadingThisRow = controlTaskMutation.isPending && controlTaskId === row.task_id
          const controlLoadingOtherRow = controlTaskMutation.isPending && controlTaskId !== row.task_id
          const deleteTaskId = deleteBacktestTaskMutation.variables
          const deleteLoadingThisRow = deleteBacktestTaskMutation.isPending && deleteTaskId === row.task_id
          const deleteLoadingOtherRow = deleteBacktestTaskMutation.isPending && deleteTaskId !== row.task_id
          const exportingThisRow = backtestHistoryTradesExportingTaskId === row.task_id
          const exportingOtherRow = backtestHistoryTradesExportingTaskId !== null && backtestHistoryTradesExportingTaskId !== row.task_id
          const disableActions = controlLoadingOtherRow || deleteLoadingOtherRow
          const canExport = row.status === 'succeeded'
          const canPause = row.status === 'pending' || row.status === 'running'
          const canResume = row.status === 'paused'
          const canCancel = row.status === 'pending' || row.status === 'running' || row.status === 'paused'
          const canDelete = row.status !== 'pending' && row.status !== 'running'
          const canRestoreParams =
            row.status === 'succeeded'
            && Boolean(resolveBacktestHistoryRunRequest(row, payloadById))
          const exportButtonText = exportingThisRow && backtestHistoryTradesExportingFormat
            ? `导出${TRADE_EXPORT_LABEL_MAP[backtestHistoryTradesExportingFormat]}`
            : '导出'
          return (
            <Space size={6} wrap>
              <Button
                size="small"
                disabled={disableActions}
                onClick={() => {
                  void handleViewBacktestHistoryTask(row.task_id)
                }}
              >
                查看
              </Button>
              {canRestoreParams ? (
                <Button
                  size="small"
                  disabled={disableActions}
                  onClick={() => {
                    void applyBacktestHistoryToForm(row)
                  }}
                >
                  回填
                </Button>
              ) : null}
              {canRestoreParams ? (
                <Button
                  size="small"
                  type="primary"
                  ghost
                  disabled={disableActions}
                  onClick={() => saveBacktestHistoryPreset(row)}
                >
                  保存
                </Button>
              ) : null}
              <Dropdown
                trigger={['click']}
                menu={buildBacktestHistoryTradesExportMenu(row)}
                disabled={
                  !canExport
                  || disableActions
                  || controlLoadingThisRow
                  || deleteLoadingThisRow
                  || exportingOtherRow
                }
              >
                <Button
                  size="small"
                  type="primary"
                  ghost
                  loading={exportingThisRow}
                  onClick={(event) => event.stopPropagation()}
                >
                  {exportButtonText}
                </Button>
              </Dropdown>
              {canPause ? (
                <Button
                  size="small"
                  loading={controlLoadingThisRow && controlAction === 'pause'}
                  disabled={disableActions || deleteLoadingThisRow}
                  onClick={() => controlTaskMutation.mutate({ action: 'pause', taskId: row.task_id })}
                >
                  暂停
                </Button>
              ) : null}
              {canResume ? (
                <Button
                  size="small"
                  type="primary"
                  ghost
                  loading={controlLoadingThisRow && controlAction === 'resume'}
                  disabled={disableActions || deleteLoadingThisRow}
                  onClick={() => controlTaskMutation.mutate({ action: 'resume', taskId: row.task_id })}
                >
                  继续
                </Button>
              ) : null}
              {canCancel ? (
                <Button
                  size="small"
                  danger
                  loading={controlLoadingThisRow && controlAction === 'cancel'}
                  disabled={disableActions || deleteLoadingThisRow}
                  onClick={() => controlTaskMutation.mutate({ action: 'cancel', taskId: row.task_id })}
                >
                  停止
                </Button>
              ) : null}
              <Button
                size="small"
                danger
                loading={deleteLoadingThisRow}
                disabled={!canDelete || disableActions || controlLoadingThisRow}
                onClick={() => handleDeleteBacktestHistoryTask(row.task_id)}
              >
                删除
              </Button>
            </Space>
          )
        },
      },
    ],
    [
      applyBacktestHistoryToForm,
      backtestHistoryTradesExportingFormat,
      backtestHistoryTradesExportingTaskId,
      buildBacktestHistoryTradesExportMenu,
      controlTaskMutation.isPending,
      controlTaskMutation.variables,
      deleteBacktestTaskMutation.isPending,
      deleteBacktestTaskMutation.variables,
      handleDeleteBacktestHistoryTask,
      handleViewBacktestHistoryTask,
      payloadById,
      saveBacktestHistoryPreset,
      strategyNameById,
    ],
  )
  const plateauHistoryRows = useMemo(
    () =>
      Object.values(plateauTasksById).sort((left, right) => {
        const leftTs = Date.parse(left.progress.updated_at || left.progress.started_at || '')
        const rightTs = Date.parse(right.progress.updated_at || right.progress.started_at || '')
        return rightTs - leftTs
      }),
    [plateauTasksById],
  )
  const plateauHistoryColumns = useMemo<ColumnsType<BacktestPlateauTaskStatusResponse>>(
    () => [
      {
        title: '完成时间',
        width: 180,
        render: (_value, row) => row.progress.updated_at || row.progress.started_at || '--',
      },
      {
        title: '策略名称',
        width: 180,
        render: (_value, row) => {
          const strategyId = String(row.result?.base_payload?.strategy_id || '').trim()
          const strategyName = strategyNameById.get(strategyId)
          if (strategyName) return strategyName
          if (strategyId) return strategyId
          return '--'
        },
      },
      {
        title: '回测区间',
        width: 240,
        render: (_value, row) => formatBacktestDateRangeLabel(row.result?.base_payload),
      },
      {
        title: '核心参数摘要',
        width: 460,
        render: (_value, row) => {
          const best = row.result?.best_point?.params
          if (!best) return '--'
          return `window=${best.window_days}, min=${Number(best.min_score).toFixed(2)}, sl=${(Number(best.stop_loss) * 100).toFixed(2)}%, tp=${(Number(best.take_profit) * 100).toFixed(2)}%, ts=${(Number(best.trailing_stop_pct) * 100).toFixed(2)}%`
        },
      },
      {
        title: '收益率',
        width: 110,
        render: (_value, row) => {
          const best = row.result?.best_point
          return best ? formatPct(Number(best.stats.total_return)) : '--'
        },
      },
      {
        title: '回撤',
        width: 110,
        render: (_value, row) => {
          const best = row.result?.best_point
          return best ? formatPct(Number(best.stats.max_drawdown)) : '--'
        },
      },
      {
        title: '状态',
        width: 100,
        render: (_value, row) => <Tag color={plateauTaskStatusColor(row.status)}>{plateauTaskStatusLabel(row.status)}</Tag>,
      },
      {
        title: '操作',
        width: 420,
        render: (_value, row) => {
          const controlTaskId = controlPlateauTaskMutation.variables?.taskId
          const controlAction = controlPlateauTaskMutation.variables?.action
          const controlLoadingThisRow = controlPlateauTaskMutation.isPending && controlTaskId === row.task_id
          const controlLoadingOtherRow = controlPlateauTaskMutation.isPending && controlTaskId !== row.task_id
          const deleteTaskId = deletePlateauTaskMutation.variables
          const deleteLoadingThisRow = deletePlateauTaskMutation.isPending && deleteTaskId === row.task_id
          const deleteLoadingOtherRow = deletePlateauTaskMutation.isPending && deleteTaskId !== row.task_id
          const exportingThisRow = plateauHistoryExportingTaskId === row.task_id
          const exportingOtherRow = plateauHistoryExportingTaskId !== null && plateauHistoryExportingTaskId !== row.task_id
          const disableActions = controlLoadingOtherRow || deleteLoadingOtherRow
          const canExport = row.status === 'succeeded'
          const canPause = row.status === 'pending' || row.status === 'running'
          const canResume = row.status === 'paused'
          const canCancel = row.status === 'pending' || row.status === 'running' || row.status === 'paused'
          const canDelete = row.status !== 'pending' && row.status !== 'running'
          const exportButtonText = exportingThisRow && plateauHistoryExportingFormat
            ? `导出${PLATEAU_HISTORY_EXPORT_LABEL_MAP[plateauHistoryExportingFormat]}`
            : '导出'
          return (
            <Space size={6} wrap>
              <Button
                size="small"
                disabled={disableActions}
                onClick={() => {
                  void handleViewPlateauHistoryTask(row.task_id)
                }}
              >
                查看
              </Button>
              {canPause ? (
                <Button
                  size="small"
                  loading={controlLoadingThisRow && controlAction === 'pause'}
                  disabled={disableActions || deleteLoadingThisRow}
                  onClick={() => controlPlateauTaskMutation.mutate({ action: 'pause', taskId: row.task_id })}
                >
                  暂停
                </Button>
              ) : null}
              {canResume ? (
                <Button
                  size="small"
                  type="primary"
                  ghost
                  loading={controlLoadingThisRow && controlAction === 'resume'}
                  disabled={disableActions || deleteLoadingThisRow}
                  onClick={() => controlPlateauTaskMutation.mutate({ action: 'resume', taskId: row.task_id })}
                >
                  继续
                </Button>
              ) : null}
              {canCancel ? (
                <Button
                  size="small"
                  danger
                  loading={controlLoadingThisRow && controlAction === 'cancel'}
                  disabled={disableActions || deleteLoadingThisRow}
                  onClick={() => controlPlateauTaskMutation.mutate({ action: 'cancel', taskId: row.task_id })}
                >
                  停止
                </Button>
              ) : null}
              <Button
                size="small"
                danger
                loading={deleteLoadingThisRow}
                disabled={!canDelete || disableActions || controlLoadingThisRow}
                onClick={() => handleDeletePlateauHistoryTask(row.task_id)}
              >
                删除
              </Button>
              <Dropdown
                trigger={['click']}
                menu={buildPlateauHistoryExportMenu(row)}
                disabled={
                  !canExport
                  || disableActions
                  || controlLoadingThisRow
                  || deleteLoadingThisRow
                  || exportingOtherRow
                }
              >
                <Button
                  size="small"
                  type="primary"
                  ghost
                  loading={exportingThisRow}
                  onClick={(event) => event.stopPropagation()}
                >
                  {exportButtonText}
                </Button>
              </Dropdown>
            </Space>
          )
        },
      },
    ],
    [
      buildPlateauHistoryExportMenu,
      controlPlateauTaskMutation.isPending,
      controlPlateauTaskMutation.variables,
      deletePlateauTaskMutation.isPending,
      deletePlateauTaskMutation.variables,
      plateauHistoryExportingFormat,
      plateauHistoryExportingTaskId,
      handleViewPlateauHistoryTask,
      handleDeletePlateauHistoryTask,
      setSelectedPlateauTask,
      strategyNameById,
    ],
  )
  const plateauScoreBarOption = useMemo(() => {
    const rows = plateauValidPoints.slice(0, 30)
    if (rows.length === 0) return null
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
      },
      grid: {
        left: 56,
        right: 20,
        top: 24,
        bottom: 56,
      },
      xAxis: {
        type: 'category',
        data: rows.map((_row, idx) => `第${idx + 1}名`),
      },
      yAxis: {
        type: 'value',
        name: '收益平原分',
      },
      series: [
        {
          type: 'bar',
          data: rows.map((row) => Number(getPlateauPrimaryScore(row).toFixed(4))),
          itemStyle: {
            color: '#0f8b6f',
            borderRadius: [3, 3, 0, 0],
          },
        },
      ],
    }
  }, [plateauValidPoints])
  const plateauHeatmapView = useMemo(() => {
    const xAxisLabel = PLATEAU_AXIS_LABEL_MAP[plateauHeatmapXAxis]
    const yAxisLabel = PLATEAU_AXIS_LABEL_MAP[plateauHeatmapYAxis]
    const metricLabel = PLATEAU_METRIC_LABEL_MAP[plateauHeatmapMetric]
    const rows = plateauValidPoints
    if (rows.length === 0) {
      return {
        option: null,
        reason: '暂无可视化数据，请先执行收益平原扫描。',
        chartHeight: 320,
        xAxisLabel,
        yAxisLabel,
        metricLabel,
        pointByCategory: new Map<string, BacktestPlateauPoint>(),
        cellKeyByDataIndex: [] as string[],
      }
    }
    if (plateauHeatmapXAxis === plateauHeatmapYAxis) {
      return {
        option: null,
        reason: '横轴与纵轴不能相同，请分别选择不同参数。',
        chartHeight: 320,
        xAxisLabel,
        yAxisLabel,
        metricLabel,
        pointByCategory: new Map<string, BacktestPlateauPoint>(),
        cellKeyByDataIndex: [] as string[],
      }
    }

    const xAxisValues = Array.from(
      new Set(rows.map((row) => getPlateauAxisRawValue(row, plateauHeatmapXAxis))),
    ).sort((a, b) => a - b)
    const yAxisValues = Array.from(
      new Set(rows.map((row) => getPlateauAxisRawValue(row, plateauHeatmapYAxis))),
    ).sort((a, b) => a - b)

    if (xAxisValues.length <= 1 || yAxisValues.length <= 1) {
      return {
        option: null,
        reason: `当前结果中“${xAxisLabel}”或“${yAxisLabel}”只有一个取值，无法绘制二维方块热力图。`,
        chartHeight: 320,
        xAxisLabel,
        yAxisLabel,
        metricLabel,
        pointByCategory: new Map<string, BacktestPlateauPoint>(),
        cellKeyByDataIndex: [] as string[],
      }
    }

    const cellPointsByKey = new Map<string, BacktestPlateauPoint[]>()
    rows.forEach((row) => {
      const xValue = getPlateauAxisRawValue(row, plateauHeatmapXAxis)
      const yValue = getPlateauAxisRawValue(row, plateauHeatmapYAxis)
      const key = buildPlateauCellKey(xValue, yValue)
      const existing = cellPointsByKey.get(key)
      if (existing) {
        existing.push(row)
      } else {
        cellPointsByKey.set(key, [row])
      }
    })

    const xAxisCategories = xAxisValues.map((value) => formatPlateauAxisCategory(plateauHeatmapXAxis, value))
    const yAxisCategories = yAxisValues.map((value) => formatPlateauAxisCategory(plateauHeatmapYAxis, value))
    const heatmapData: Array<[string, string, number]> = []
    const cellKeyByDataIndex: string[] = []
    const metricByCategory = new Map<string, number>()
    const pointByCategory = new Map<string, BacktestPlateauPoint>()
    const pointCountByCategory = new Map<string, number>()
    let minMetricValue = Number.POSITIVE_INFINITY
    let maxMetricValue = Number.NEGATIVE_INFINITY

    xAxisValues.forEach((xValue) => {
      yAxisValues.forEach((yValue) => {
        const cellPoints = cellPointsByKey.get(buildPlateauCellKey(xValue, yValue)) ?? []
        if (cellPoints.length <= 0) return
        const metricValues = cellPoints
          .map((row) => getPlateauMetricValue(row, plateauHeatmapMetric))
          .filter((value) => Number.isFinite(value))
        if (metricValues.length <= 0) return
        const xCategory = formatPlateauAxisCategory(plateauHeatmapXAxis, xValue)
        const yCategory = formatPlateauAxisCategory(plateauHeatmapYAxis, yValue)
        const categoryKey = `${xCategory}|${yCategory}`
        const roundedMetric = Number(quantile(metricValues, 0.5).toFixed(6))
        heatmapData.push([xCategory, yCategory, roundedMetric])
        cellKeyByDataIndex.push(categoryKey)
        metricByCategory.set(categoryKey, roundedMetric)
        pointCountByCategory.set(categoryKey, cellPoints.length)
        const point = [...cellPoints].sort((left, right) => {
          return (
            getPlateauPrimaryScore(right) - getPlateauPrimaryScore(left)
            || Number(right.local_score ?? 0) - Number(left.local_score ?? 0)
            || Number(right.point_score ?? 0) - Number(left.point_score ?? 0)
            || Number(right.score ?? 0) - Number(left.score ?? 0)
            || Number(right.stats.total_return ?? 0) - Number(left.stats.total_return ?? 0)
          )
        })[0]
        if (point) {
          pointByCategory.set(categoryKey, point)
        }
        minMetricValue = Math.min(minMetricValue, roundedMetric)
        maxMetricValue = Math.max(maxMetricValue, roundedMetric)
      })
    })

    if (heatmapData.length === 0) {
      return {
        option: null,
        reason: '当前参数组合没有形成有效二维网格数据，请扩大参数扫描范围后重试。',
        chartHeight: 320,
        xAxisLabel,
        yAxisLabel,
        metricLabel,
        pointByCategory: new Map<string, BacktestPlateauPoint>(),
        cellKeyByDataIndex: [] as string[],
      }
    }

    const bestPathData: Array<[string, string]> = []
    if (plateauHeatmapShowBestPath) {
      xAxisValues.forEach((xValue) => {
        let bestMetric = Number.NEGATIVE_INFINITY
        let bestYCategory: string | null = null
        yAxisValues.forEach((yValue) => {
          const cellPoints = cellPointsByKey.get(buildPlateauCellKey(xValue, yValue)) ?? []
          if (cellPoints.length <= 0) return
          const metric = quantile(
            cellPoints
              .map((row) => getPlateauMetricValue(row, plateauHeatmapMetric))
              .filter((value) => Number.isFinite(value)),
            0.5,
          )
          if (typeof metric !== 'number') return
          if (metric > bestMetric) {
            bestMetric = metric
            bestYCategory = formatPlateauAxisCategory(plateauHeatmapYAxis, yValue)
          }
        })
        if (bestYCategory) {
          bestPathData.push([
            formatPlateauAxisCategory(plateauHeatmapXAxis, xValue),
            bestYCategory,
          ])
        }
      })
    }

    const visualMin = Number.isFinite(minMetricValue) ? Number(minMetricValue.toFixed(6)) : 0
    const visualMax = Number.isFinite(maxMetricValue) ? Number(maxMetricValue.toFixed(6)) : 1
    const chartHeight = Math.max(260, Math.min(480, yAxisCategories.length * 24 + 110))
    const selectedKey = plateauHeatmapSelectedCoord
      ? `${plateauHeatmapSelectedCoord[0]}|${plateauHeatmapSelectedCoord[1]}`
      : ''
    const selectedCoordData =
      selectedKey && pointByCategory.has(selectedKey) && plateauHeatmapSelectedCoord
        ? [[plateauHeatmapSelectedCoord[0], plateauHeatmapSelectedCoord[1]]]
        : []

    const option = {
      tooltip: {
        trigger: 'item',
        formatter: (params: { value?: [string, string, number] }) => {
          const value = params?.value
          if (!Array.isArray(value) || value.length < 3) return ''
          const [xCategory, yCategory, metric] = value
          const actualMetric = metricByCategory.get(`${xCategory}|${yCategory}`) ?? Number(metric)
          const pointCount = pointCountByCategory.get(`${xCategory}|${yCategory}`) ?? 0
          return [
            `${xAxisLabel}: ${xCategory}`,
            `${yAxisLabel}: ${yCategory}`,
            `该格参数数: ${pointCount}`,
            `${metricLabel}: ${formatPlateauMetricValue(plateauHeatmapMetric, actualMetric)}`,
            '说明: 当前方块展示该格参数组的中位数；点击时会回填该格收益平原分最高的代表点。',
          ].join('<br/>')
        },
      },
      grid: {
        left: 88,
        right: 64,
        top: 24,
        bottom: 84,
      },
      xAxis: {
        type: 'category',
        data: xAxisCategories,
        name: xAxisLabel,
        nameLocation: 'middle',
        nameGap: 34,
        axisLabel: {
          interval: 0,
          hideOverlap: true,
          rotate: xAxisCategories.length > 8 ? 26 : 0,
          fontSize: 11,
        },
      },
      yAxis: {
        type: 'category',
        data: yAxisCategories,
        name: yAxisLabel,
        nameLocation: 'middle',
        nameGap: 56,
        axisLabel: {
          hideOverlap: true,
          fontSize: 11,
        },
      },
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: 0,
          filterMode: 'none',
        },
        {
          type: 'inside',
          yAxisIndex: 0,
          filterMode: 'none',
        },
        {
          type: 'slider',
          yAxisIndex: 0,
          right: 18,
          top: 36,
          bottom: 78,
          width: 12,
          filterMode: 'none',
        },
      ],
      visualMap: {
        min: visualMin,
        max: visualMax,
        dimension: 2,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 18,
        text: [metricLabel, ''],
        inRange: {
          color: ['#dff5e8', '#8fd7b8', '#3aaf87', '#0f8b6f'],
        },
      },
      toolbox: {
        right: 8,
        top: 2,
        feature: {
          brush: {
            type: ['rect', 'clear'],
          },
        },
      },
      brush: {
        xAxisIndex: 'all',
        yAxisIndex: 'all',
        brushLink: 'all',
        inBrush: {
          opacity: 1,
        },
        outOfBrush: {
          opacity: 0.25,
        },
      },
      series: [
        {
          type: 'heatmap',
          data: heatmapData,
          itemStyle: {
            borderColor: 'rgba(255,255,255,0.9)',
            borderWidth: 1,
          },
          label: {
            show: plateauHeatmapShowCellLabel,
            color: '#123026',
            fontSize: 10,
            formatter: (params: { value?: [string, string, number] }) => {
              const metric = Number(params?.value?.[2] ?? 0)
              return formatPlateauMetricValue(plateauHeatmapMetric, metric)
            },
          },
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowColor: 'rgba(15, 139, 111, 0.35)',
            },
          },
          z: 1,
        },
        ...(bestPathData.length > 0
          ? [
              {
                type: 'line',
                data: bestPathData,
                showSymbol: true,
                symbol: 'diamond',
                symbolSize: 9,
                lineStyle: {
                  color: '#fa8c16',
                  width: 2,
                },
                itemStyle: {
                  color: '#fa8c16',
                  borderColor: '#fff',
                  borderWidth: 1,
                },
                z: 3,
              },
            ]
          : []),
        ...(selectedCoordData.length > 0
          ? [
              {
                type: 'scatter',
                data: selectedCoordData,
                symbol: 'roundRect',
                symbolSize: 14,
                itemStyle: {
                  color: '#f5222d',
                  borderColor: '#fff',
                  borderWidth: 1,
                },
                z: 4,
              },
            ]
          : []),
      ],
    }

    return {
      option,
      reason: null,
      chartHeight,
      xAxisLabel,
      yAxisLabel,
      metricLabel,
      pointByCategory,
      cellKeyByDataIndex,
    }
  }, [
    plateauHeatmapMetric,
    plateauHeatmapShowBestPath,
    plateauHeatmapShowCellLabel,
    plateauHeatmapSelectedCoord,
    plateauHeatmapXAxis,
    plateauHeatmapYAxis,
    plateauValidPoints,
  ])
  const plateauHeatmapOption = plateauHeatmapView.option
  const plateauHeatmapUnavailableReason = plateauHeatmapView.reason
  const plateauHeatmapChartHeight = plateauHeatmapView.chartHeight
  const plateauHeatmapTitle = `方块热力图（${plateauHeatmapView.xAxisLabel} × ${plateauHeatmapView.yAxisLabel}，值=${plateauHeatmapView.metricLabel}）`
  const plateauHeatmapPointByCategory = plateauHeatmapView.pointByCategory
  const plateauHeatmapCellKeyByDataIndex = plateauHeatmapView.cellKeyByDataIndex
  const plateauBrushSelectedPoints = useMemo(
    () =>
      plateauBrushSelectedKeys
        .map((key) => plateauHeatmapPointByCategory.get(key))
        .filter((item): item is BacktestPlateauPoint => Boolean(item)),
    [plateauBrushSelectedKeys, plateauHeatmapPointByCategory],
  )
  const plateauCandidateOptions = useMemo(
    () =>
      plateauCandidatePoints.map((point, index) => ({
        value: index + 1,
        label: `候选#${index + 1} | 评分 ${point.score.toFixed(3)} | 收益 ${formatPct(point.stats.total_return)}`,
      })),
    [plateauCandidatePoints],
  )
  const selectedCandidatePoint = plateauCandidatePoints[plateauCandidatePickRank - 1] ?? null
  const plateauSavedPresetOptions = useMemo(
    () =>
      plateauSavedPresets.map((item, index) => ({
        value: item.id,
        label: `收藏#${index + 1} | ${item.name} | ${item.saved_at}`,
      })),
    [plateauSavedPresets],
  )
  const selectedSavedPreset = useMemo(
    () => plateauSavedPresets.find((item) => item.id === plateauSavedPresetId) ?? null,
    [plateauSavedPresetId, plateauSavedPresets],
  )
  const plateauSavedPresetColumns = useMemo<ColumnsType<BacktestPlateauPreset>>(
    () => [
      {
        title: '收藏名',
        dataIndex: 'name',
        width: 280,
      },
      {
        title: '保存时间',
        dataIndex: 'saved_at',
        width: 170,
      },
      {
        title: '评分',
        width: 90,
        render: (_value, row) => (row.point ? row.point.score.toFixed(3) : '--'),
      },
      {
        title: '收益',
        width: 100,
        render: (_value, row) => {
          if (row.point) return formatPct(row.point.stats.total_return)
          if (row.stats_summary) return formatPct(Number(row.stats_summary.total_return))
          return '--'
        },
      },
      {
        title: '来源',
        width: 100,
        render: (_value, row) => (row.source === 'backtest_history' ? '回测历史' : '收益平原'),
      },
      {
        title: '参数摘要',
        width: 420,
        render: (_value, row) => {
          if (row.run_request) {
            return formatBacktestRunRequestSummary(row.run_request)
          }
          if (row.point) {
            const p = row.point.params
            return `window=${p.window_days}, min_score=${Number(p.min_score).toFixed(2)}, stop_loss=${(Number(p.stop_loss) * 100).toFixed(2)}%, take_profit=${(Number(p.take_profit) * 100).toFixed(2)}%, trailing_stop=${(Number(p.trailing_stop_pct) * 100).toFixed(2)}%, pos=${p.max_positions}`
          }
          return '--'
        },
      },
      {
        title: '操作',
        key: 'action',
        width: 150,
        render: (_value, row) => (
          <Space size={6}>
            <Button
              size="small"
              onClick={() => {
                if (row.source === 'backtest_history' && row.run_request) {
                  applyBacktestRunRequestToForm(row.run_request)
                  setRunError(null)
                  message.success('收藏参数已回填到回测表单。')
                  return
                }
                if (row.point) {
                  applyPlateauPointToForm(row.point, '收藏参数', row.run_request ?? null)
                  return
                }
                if (row.run_request) {
                  applyBacktestRunRequestToForm(row.run_request)
                  setRunError(null)
                  message.success('收藏参数已回填到回测表单。')
                }
              }}
            >
              回填
            </Button>
            <Button
              size="small"
              danger
              onClick={() => {
                setPlateauSavedPresets((prev) => prev.filter((item) => item.id !== row.id))
                if (plateauSavedPresetId === row.id) setPlateauSavedPresetId(null)
              }}
            >
              删除
            </Button>
          </Space>
        ),
      },
    ],
    [applyBacktestRunRequestToForm, applyPlateauPointToForm, plateauSavedPresetId],
  )

  function savePlateauPreset(
    point: BacktestPlateauPoint,
    sourceLabel: string,
    runRequest?: BacktestRunRequest | null,
  ) {
    if (point.error) {
      message.warning('该参数组评估失败，无法保存。')
      return
    }
    const nowText = dayjs().format('YYYY-MM-DD HH:mm:ss')
    const paramsKey = buildPlateauParamsKey(point)
    const presetName = `${sourceLabel} | 评分 ${point.score.toFixed(3)} | 收益 ${formatPct(point.stats.total_return)}`
    const effectiveRunRequest = runRequest ?? buildPlateauPointRunRequest(point, plateauResult?.base_payload ?? null)
    let persistedId = ''
    setPlateauSavedPresets((prev) => {
      const idx = prev.findIndex((item) => item.point && buildPlateauParamsKey(item.point) === paramsKey)
      if (idx >= 0) {
        const next = [...prev]
        const existing = next[idx]
        persistedId = existing.id
        next[idx] = {
          ...existing,
          name: presetName,
          saved_at: nowText,
          source: 'plateau',
          point,
          run_request: effectiveRunRequest,
        }
        return next
      }
      const created: BacktestPlateauPreset = {
        id: `pp_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
        name: presetName,
        saved_at: nowText,
        source: 'plateau',
        point,
        run_request: effectiveRunRequest,
      }
      persistedId = created.id
      return [created, ...prev].slice(0, 300)
    })
    if (persistedId) {
      setPlateauSavedPresetId(persistedId)
    }
    message.success(`已保存参数：${presetName}`)
  }

  function handleApplySavedPreset() {
    if (!selectedSavedPreset) {
      message.info('请先选择一个收藏参数。')
      return
    }
    if (selectedSavedPreset.point) {
      applyPlateauPointToForm(selectedSavedPreset.point, '收藏参数', selectedSavedPreset.run_request ?? null)
      return
    }
    if (selectedSavedPreset.run_request) {
      applyBacktestRunRequestToForm(selectedSavedPreset.run_request)
      setRunError(null)
      message.success('收藏参数已回填到回测表单。')
      return
    }
    message.warning('该收藏缺少可回填的参数。')
  }

  function handleDeleteSavedPreset() {
    if (!selectedSavedPreset) {
      message.info('请先选择一个收藏参数。')
      return
    }
    const deletingId = selectedSavedPreset.id
    setPlateauSavedPresets((prev) => prev.filter((item) => item.id !== deletingId))
    if (plateauSavedPresetId === deletingId) {
      setPlateauSavedPresetId(null)
    }
    message.success('已删除收藏参数。')
  }

  async function resolvePlateauHistoryTaskForExport(
    row: BacktestPlateauTaskStatusResponse,
  ): Promise<BacktestPlateauTaskStatusResponse | null> {
    const taskId = String(row.task_id || '').trim()
    if (!taskId) return null
    const current = plateauTasksById[taskId] ?? row
    if (current.result) {
      return current
    }
    if (isImportedLocalTaskId(taskId, 'plateau')) {
      message.warning(`本地导入任务 ${taskId} 已丢失结果缓存，请重新加载报告后再导出。`)
      return null
    }
    try {
      const status = await getBacktestPlateauTask(taskId)
      upsertPlateauTaskStatus(status)
      if (!status.result) {
        message.warning(`任务 ${taskId} 暂无可导出的收益平原结果`)
        return null
      }
      return status
    } catch (error) {
      message.warning(`收益平原任务详情加载失败：${formatApiError(error)}`)
      return null
    }
  }

  async function handleExportPlateauHistoryTask(
    row: BacktestPlateauTaskStatusResponse,
    format: PlateauHistoryExportFormat,
  ) {
    if (plateauHistoryExportingTaskId) return
    const taskId = String(row.task_id || '').trim()
    if (!taskId) return
    if (row.status !== 'succeeded') {
      message.warning('仅支持导出已完成的收益平原任务')
      return
    }
    setPlateauHistoryExportingTaskId(taskId)
    setPlateauHistoryExportingFormat(format)
    const ftbtProgressKey = `plateau-history-ftbt-${taskId}`
    try {
      const resolved = await resolvePlateauHistoryTaskForExport(row)
      if (!resolved?.result) return
      const plateauResultWithCorr: BacktestPlateauResponse = {
        ...resolved.result,
        correlations: resolvePlateauCorrelations(resolved.result),
      }
      const safeTaskId = taskId.replace(/[^0-9A-Za-z_.-]/g, '_') || 'plateau_task'
      const timestamp = dayjs().format('YYYYMMDD_HHmmss')
      if (format === 'excel') {
        const workbookBuffer = buildPlateauHistoryWorkbookBuffer(taskId, plateauResultWithCorr)
        const fileName = `plateau_history_${safeTaskId}_${timestamp}.xlsx`
        const blob = new Blob([workbookBuffer], {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        downloadBlob(fileName, blob)
        message.success(`已导出收益平原 Excel：${fileName}`)
        return
      }
      if (format === 'html') {
        const reportHtml = buildPlateauHistoryHtml(taskId, plateauResultWithCorr)
        const fileName = `plateau_history_${safeTaskId}_${timestamp}.html`
        const blob = new Blob([reportHtml], { type: 'text/html;charset=utf-8' })
        downloadBlob(fileName, blob)
        message.success(`已导出收益平原 HTML：${fileName}`)
        return
      }

      const points = plateauResultWithCorr.points ?? []
      if (points.length <= 0) {
        message.warning('当前收益平原任务没有参数组合，无法导出 ftbt')
        return
      }
      const pointRuns: PlateauPointRunRecord[] = []
      message.open({
        key: ftbtProgressKey,
        type: 'loading',
        content: `正在整理参数组 0/${points.length} ...`,
        duration: 0,
      })
      for (let index = 0; index < points.length; index += 1) {
        const point = points[index]!
        const rank = index + 1
        if (point.error) {
          pointRuns.push({
            rank,
            point,
            runRequest: buildRunRequestFromPlateauParams(plateauResultWithCorr.base_payload, point.params),
            runResult: null,
            error: String(point.error),
          })
          message.open({
            key: ftbtProgressKey,
            type: 'loading',
            content: `正在整理参数组 ${rank}/${points.length} ...`,
            duration: 0,
          })
          continue
        }
        try {
          const resolved = await resolvePlateauPointRunArtifact(point, {
            actionLabel: '导出 ftbt',
            taskId,
            basePayload: plateauResultWithCorr.base_payload,
            notifyOnRerunFallback: false,
          })
          if (!resolved) {
            pointRuns.push({
              rank,
              point,
              runRequest: buildRunRequestFromPlateauParams(plateauResultWithCorr.base_payload, point.params),
              runResult: null,
              error: '缺少可用的参数组明细结果',
            })
          } else {
            pointRuns.push({
              rank,
              point,
              runRequest: resolved.runRequest,
              runResult: resolved.runResult,
              error: null,
            })
          }
        } catch (error) {
          pointRuns.push({
            rank,
            point,
            runRequest: buildRunRequestFromPlateauParams(plateauResultWithCorr.base_payload, point.params),
            runResult: null,
            error: formatApiError(error),
          })
        }
        message.open({
          key: ftbtProgressKey,
          type: 'loading',
          content: `正在整理参数组 ${rank}/${points.length} ...`,
          duration: 0,
        })
      }
      const successRuns = pointRuns.filter(
        (item): item is PlateauPointRunRecord & { runResult: BacktestResponse } => item.runResult !== null,
      )
      if (successRuns.length <= 0) {
        throw new Error('全部参数组回放失败，无法生成 ftbt 包')
      }
      const ref = successRuns[0]
      const workbookBuffer = buildPlateauAllTradesWorkbookBuffer(taskId, plateauResultWithCorr, pointRuns)
      const reportHtml = buildPlateauAllTradesHtml(taskId, plateauResultWithCorr, pointRuns)
      const reportId = `plateau_all_trades_${safeTaskId}_${timestamp}`
        .replace(/[^0-9A-Za-z_.-]/g, '_')
        .slice(0, 96)
      const payload = await buildBacktestReportPackage({
        run_request: ref.runRequest,
        run_result: ref.runResult,
        report_html: reportHtml,
        report_xlsx_base64: bufferToBase64(workbookBuffer),
        plateau_result: plateauResultWithCorr,
        plateau_task_id: taskId,
        plateau_point_details: buildPlateauPointDetailPayloads(taskId, pointRuns),
        report_id: reportId,
        app_name: 'Final Trade',
        app_version: String(import.meta.env.VITE_APP_VERSION || 'frontend'),
      })
      const bytes = base64ToUint8Array(payload.file_base64)
      const safeBytes = new Uint8Array(bytes.byteLength)
      safeBytes.set(bytes)
      const blob = new Blob([safeBytes], { type: 'application/octet-stream' })
      downloadBlob(payload.file_name, blob)
      message.success({
        key: ftbtProgressKey,
        content: `已导出 ftbt：${payload.file_name}（成功整理 ${successRuns.length}/${pointRuns.length} 组）`,
        duration: 2,
      })
    } catch (error) {
      const text = formatApiError(error)
      if (format === 'ftbt') {
        message.error({ key: ftbtProgressKey, content: `导出 ftbt 失败：${text}` })
      } else {
        message.error(`导出失败：${text}`)
      }
    } finally {
      setPlateauHistoryExportingTaskId(null)
      setPlateauHistoryExportingFormat(null)
    }
  }

  function buildPlateauHistoryExportMenu(row: BacktestPlateauTaskStatusResponse): MenuProps {
    return {
      items: [
        { key: 'excel', label: '导出 Excel' },
        { key: 'html', label: '导出 HTML' },
        { key: 'ftbt', label: '导出 ftbt（含全部参数组合交易记录）' },
      ],
      onClick: (info) => {
        info.domEvent.stopPropagation()
        const key = String(info.key)
        if (key === 'excel' || key === 'html' || key === 'ftbt') {
          void handleExportPlateauHistoryTask(row, key as PlateauHistoryExportFormat)
        }
      },
    }
  }

  function downloadTradesExport(
    trades: BacktestTrade[],
    format: TradeExportFormat,
    fileNamePrefix: string,
    meta?: TradeExportMeta,
  ) {
    if (trades.length <= 0) {
      message.warning('暂无可导出的交易明细')
      return
    }
    const fileName = buildTradeExportFileName(fileNamePrefix, format)
    if (format === 'excel') {
      const workbookBuffer = buildTradesOnlyWorkbookBuffer(trades)
      const blob = new Blob([workbookBuffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      })
      downloadBlob(fileName, blob)
    } else if (format === 'csv') {
      const blob = new Blob([buildTradesOnlyCsv(trades)], { type: 'text/csv;charset=utf-8;' })
      downloadBlob(fileName, blob)
    } else {
      const blob = new Blob([buildTradesOnlyHtml(trades, meta)], { type: 'text/html;charset=utf-8' })
      downloadBlob(fileName, blob)
    }
    message.success(`已导出交易明细：${fileName}`)
  }

  function buildTradesExportMenu(
    trades: BacktestTrade[],
    fileNamePrefix: string,
    meta: TradeExportMeta | undefined,
    onStart: (format: TradeExportFormat) => void,
    onFinish: () => void,
  ): MenuProps {
    return {
      items: [
        { key: 'excel', label: '导出 Excel' },
        { key: 'csv', label: '导出 CSV' },
        { key: 'html', label: '导出 HTML' },
      ],
      onClick: (info) => {
        info.domEvent.stopPropagation()
        const key = String(info.key)
        if (key !== 'excel' && key !== 'csv' && key !== 'html') return
        const format = key as TradeExportFormat
        onStart(format)
        try {
          downloadTradesExport(trades, format, fileNamePrefix, meta)
        } finally {
          onFinish()
        }
      },
    }
  }

  function buildTradeDetailExportMenu(): MenuProps {
    if (!result) {
      return { items: [] }
    }
    const meta: TradeExportMeta = {
      title: '策略回测交易明细',
      date_from: result.range.date_from,
      date_to: result.range.date_to,
      task_id: selectedTaskId ?? undefined,
    }
    return buildTradesExportMenu(
      result.trades,
      'backtest_trades',
      meta,
      (format) => setTradeDetailExportingFormat(format),
      () => setTradeDetailExportingFormat(null),
    )
  }

  async function resolveBacktestHistoryTaskForTradesExport(
    row: BacktestTaskStatusResponse,
  ): Promise<BacktestTaskStatusResponse | null> {
    const taskId = String(row.task_id || '').trim()
    if (!taskId) return null
    const current = tasksById[taskId] ?? row
    if (current.result?.trades?.length) {
      return current
    }
    if (isImportedLocalTaskId(taskId, 'backtest')) {
      message.warning(`本地导入任务 ${taskId} 已丢失结果缓存，请重新加载报告后再导出。`)
      return null
    }
    try {
      const status = await getBacktestTask(taskId)
      upsertTaskStatus(status)
      if (!status.result?.trades?.length) {
        message.warning(`任务 ${taskId} 暂无可导出的交易明细`)
        return null
      }
      return status
    } catch (error) {
      message.warning(`回测任务详情加载失败：${formatApiError(error)}`)
      return null
    }
  }

  async function handleExportBacktestHistoryTrades(
    row: BacktestTaskStatusResponse,
    format: TradeExportFormat,
  ) {
    if (backtestHistoryTradesExportingTaskId) return
    const taskId = String(row.task_id || '').trim()
    if (!taskId) return
    if (row.status !== 'succeeded') {
      message.warning('仅支持导出已完成的回测任务')
      return
    }
    setBacktestHistoryTradesExportingTaskId(taskId)
    setBacktestHistoryTradesExportingFormat(format)
    try {
      const resolved = await resolveBacktestHistoryTaskForTradesExport(row)
      if (!resolved?.result?.trades?.length) return
      const safeTaskId = taskId.replace(/[^0-9A-Za-z_.-]/g, '_') || 'backtest_task'
      downloadTradesExport(resolved.result.trades, format, `backtest_trades_${safeTaskId}`, {
        title: '策略回测交易明细',
        date_from: resolved.result.range.date_from,
        date_to: resolved.result.range.date_to,
        task_id: taskId,
      })
    } finally {
      setBacktestHistoryTradesExportingTaskId(null)
      setBacktestHistoryTradesExportingFormat(null)
    }
  }

  function buildBacktestHistoryTradesExportMenu(row: BacktestTaskStatusResponse): MenuProps {
    return {
      items: [
        { key: 'excel', label: '导出 Excel' },
        { key: 'csv', label: '导出 CSV' },
        { key: 'html', label: '导出 HTML' },
      ],
      onClick: (info) => {
        info.domEvent.stopPropagation()
        const key = String(info.key)
        if (key === 'excel' || key === 'csv' || key === 'html') {
          void handleExportBacktestHistoryTrades(row, key as TradeExportFormat)
        }
      },
    }
  }

  function handleDeletePlateauHistoryTask(taskId: string) {
    const normalizedTaskId = String(taskId || '').trim()
    if (!normalizedTaskId) return
    if (isImportedLocalTaskId(normalizedTaskId, 'plateau')) {
      removePlateauTask(normalizedTaskId)
      message.success(`已删除本地导入任务：${normalizedTaskId}`)
      return
    }
    deletePlateauTaskMutation.mutate(normalizedTaskId)
  }

  async function handleViewPlateauHistoryTask(taskId: string) {
    const normalizedTaskId = String(taskId || '').trim()
    if (!normalizedTaskId) return
    setSelectedPlateauTask(normalizedTaskId)
    const current = plateauTasksById[normalizedTaskId]
    if (current?.result) {
      return
    }
    if (isImportedLocalTaskId(normalizedTaskId, 'plateau')) {
      message.info(`本地导入任务 ${normalizedTaskId} 需要重新加载报告后才能查看。`)
      return
    }
    try {
      const status = await getBacktestPlateauTask(normalizedTaskId)
      upsertPlateauTaskStatus(status)
      if (!status.result) {
        message.info(`任务 ${normalizedTaskId} 暂无可展示结果`)
      }
    } catch (error) {
      message.warning(`收益平原任务详情加载失败：${formatApiError(error)}`)
    }
  }

  async function handleViewBacktestHistoryTask(taskId: string) {
    const normalizedTaskId = String(taskId || '').trim()
    if (!normalizedTaskId) return
    setSelectedTask(normalizedTaskId)
    const current = tasksById[normalizedTaskId]
    if (current?.result) {
      scrollToBacktestResultSection()
      return
    }
    if (isImportedLocalTaskId(normalizedTaskId, 'backtest')) {
      message.info(`本地导入任务 ${normalizedTaskId} 需要重新加载报告后才能查看。`)
      return
    }
    try {
      const status = await getBacktestTask(normalizedTaskId)
      upsertTaskStatus(status)
      if (status.result) {
        scrollToBacktestResultSection()
      } else {
        message.info(`任务 ${normalizedTaskId} 暂无可展示结果`)
      }
    } catch (error) {
      message.warning(`策略回测任务详情加载失败：${formatApiError(error)}`)
    }
  }

  function handleDeleteBacktestHistoryTask(taskId: string) {
    const normalizedTaskId = String(taskId || '').trim()
    if (!normalizedTaskId) return
    if (isImportedLocalTaskId(normalizedTaskId, 'backtest')) {
      removeTask(normalizedTaskId)
      message.success(`已删除本地导入任务：${normalizedTaskId}`)
      return
    }
    deleteBacktestTaskMutation.mutate(normalizedTaskId)
  }

  function appendPointsToCandidateSet(points: BacktestPlateauPoint[], sourceLabel: string) {
    if (points.length <= 0) {
      message.info(`未从${sourceLabel}获取可加入的参数组。`)
      return
    }
    let addedCount = 0
    setPlateauCandidatePoints((prev) => {
      const next = [...prev]
      const seen = new Set(prev.map((item) => buildPlateauParamsKey(item)))
      points.forEach((point) => {
        const key = buildPlateauParamsKey(point)
        if (seen.has(key)) return
        seen.add(key)
        next.push(point)
        addedCount += 1
      })
      return next
    })
    setPlateauCandidatePickRank(1)
    if (addedCount > 0) {
      message.success(`已从${sourceLabel}加入候选参数 ${addedCount} 组。`)
    } else {
      message.info(`候选参数集中已包含${sourceLabel}选中的参数组。`)
    }
  }

  function handleAddBrushSelectionToCandidates() {
    appendPointsToCandidateSet(plateauBrushSelectedPoints, '框选区域')
  }

  function handleApplySelectedCandidatePoint() {
    if (!selectedCandidatePoint) {
      message.info('请先选择一个候选参数组。')
      return
    }
    applyPlateauPointToForm(selectedCandidatePoint, `候选#${plateauCandidatePickRank}`)
  }

  function handleClearCandidatePoints() {
    setPlateauCandidatePoints([])
    setPlateauCandidatePickRank(1)
  }

  const plateauHeatmapOnEvents = useMemo(
    () => ({
      click: (params: { seriesType?: string; value?: unknown }) => {
        if (params?.seriesType !== 'heatmap') return
        const value = params?.value
        if (!Array.isArray(value) || value.length < 2) return
        const xCategory = String(value[0] ?? '').trim()
        const yCategory = String(value[1] ?? '').trim()
        if (!xCategory || !yCategory) return
        const key = `${xCategory}|${yCategory}`
        const point = plateauHeatmapPointByCategory.get(key)
        if (!point) return
        setPlateauHeatmapSelectedCoord([xCategory, yCategory])
        applyPlateauPointToForm(point, `热力图点(${xCategory}, ${yCategory})`)
      },
      brushSelected: (params: {
        batch?: Array<{
          selected?: Array<{
            seriesIndex?: number
            dataIndex?: number[]
          }>
        }>
      }) => {
        const indexSet = new Set<number>()
        const batches = Array.isArray(params?.batch) ? params.batch : []
        batches.forEach((batchItem) => {
          const selectedSeries = Array.isArray(batchItem?.selected) ? batchItem.selected : []
          selectedSeries.forEach((seriesItem) => {
            if (typeof seriesItem?.seriesIndex === 'number' && seriesItem.seriesIndex !== 0) return
            const dataIndices = Array.isArray(seriesItem?.dataIndex) ? seriesItem.dataIndex : []
            dataIndices.forEach((idx) => {
              const numericIndex = Number(idx)
              if (Number.isInteger(numericIndex) && numericIndex >= 0) {
                indexSet.add(numericIndex)
              }
            })
          })
        })
        const keys = Array.from(indexSet)
          .sort((a, b) => a - b)
          .map((idx) => plateauHeatmapCellKeyByDataIndex[idx])
          .filter((item): item is string => Boolean(item))
        setPlateauBrushSelectedKeys(keys)
      },
    }),
    [applyPlateauPointToForm, plateauHeatmapCellKeyByDataIndex, plateauHeatmapPointByCategory],
  )
  const effectiveRunError =
    runError
    || (taskStatus?.status === 'failed' ? (taskStatus.error?.trim() || '回测任务失败') : null)
  const taskStageRows = useMemo(() => {
    const rows = Array.isArray(taskProgress?.stage_timings) ? taskProgress.stage_timings : []
    const normalized = rows
      .map((row) => ({
        stageKey: String(row?.stage_key || '').trim(),
        label: String(row?.label || '').trim() || '未命名阶段',
        elapsedMs: Math.max(0, Number(row?.elapsed_ms || 0)),
      }))
      .filter((row) => Boolean(row.stageKey))
    const rowsForShare = normalized.filter((row) => !(row.stageKey === 'run_total' || row.stageKey.endsWith('_total')))
    const totalMs = (rowsForShare.length > 0 ? rowsForShare : normalized).reduce((acc, row) => acc + row.elapsedMs, 0)
    return {
      totalMs,
      rows: normalized.map((row) => ({
        ...row,
        elapsedSecText: `${(row.elapsedMs / 1000).toFixed(2)}s`,
        shareText:
          row.stageKey === 'run_total' || row.stageKey.endsWith('_total')
            ? '--'
            : (totalMs > 0 ? `${((row.elapsedMs / totalMs) * 100).toFixed(1)}%` : '0.0%'),
      })),
    }
  }, [taskProgress?.stage_timings])
  return (
    <Space data-research="page" data-view={plateauView ? "plateau" : "backtest"} orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="策略回测" subtitle="回放历史信号，评估策略收益、回撤与执行质量。" />

      {plateauView ? <Card data-research="shared-summary" size="small"><Space wrap><strong>当前回测设置</strong><Tag>{selectedStrategy?.name || strategyId}</Tag><span>{range[0].format('YYYY-MM-DD')} 至 {range[1].format('YYYY-MM-DD')}</span><span>{mode === 'trend_pool' ? '趋势池' : '全市场'} · {maxSymbols}只股票上限</span><Button size="small" onClick={() => setShowSharedConfig(value => !value)}>{showSharedConfig ? '收起设置' : '调整回测设置'}</Button></Space></Card> : null}

      <Card data-research="backtest-config" hidden={plateauView && !showSharedConfig} title="回测设置">
        <Row data-research="fields"><Col xs={24} md={6}>
          <Space orientation="vertical" style={{ width: '100%' }}>
            <span>回测范围</span>
            <Radio.Group
              value={mode}
              optionType="button"
              onChange={(event) => setMode(event.target.value)}
              options={[
                { label: '趋势池', value: 'trend_pool' },
                { label: '全市场', value: 'full_market' },
              ]}
            />
          </Space>
        </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>趋势池阶段</span>
              <Select
                value={trendStep}
                onChange={(value) => setTrendStep(value)}
                options={[
                  { value: 'auto', label: 'auto（自动）' },
                  { value: 'step1', label: 'step1' },
                  { value: 'step2', label: 'step2' },
                  { value: 'step3', label: 'step3' },
                  { value: 'step4', label: 'step4' },
                ]}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>滚动模式</span>
              <Radio.Group
                value={poolRollMode}
                optionType="button"
                onChange={(event) => setPoolRollMode(event.target.value)}
                options={[
                  { label: '每日滚动', value: 'daily' },
                  { label: '每周滚动', value: 'weekly' },
                  { label: '持仓触发', value: 'position' },
                ]}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>策略</span>
              <Select
                loading={strategyCatalogQuery.isLoading}
                value={strategyId}
                onChange={(value) => {
                  const next = String(value) as StrategyId
                  setStrategyId(next)
                  setStrategyParams(getSharedStrategyParams(next))
                }}
                options={(strategyItems.length > 0
                  ? strategyItems
                  : [{ strategy_id: 'wyckoff_trend_v1', name: '维科夫趋势V1', version: '1.0.0', enabled: true }])
                  .map((item) => ({
                    value: item.strategy_id,
                    label: `${item.name} (${item.version})${item.enabled === false ? ' - 已禁用' : ''}`,
                    disabled: item.enabled === false,
                  }))}
              />
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>回测区间</span>
              <DatePicker.RangePicker
                style={{ width: '100%' }}
                value={range}
                onChange={(value) => {
                  if (value?.[0] && value?.[1]) setRange([value[0], value[1]])
                }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>信号窗口天数</span>
              <InputNumber
                min={20}
                max={240}
                value={windowDays}
                onChange={(value) => setWindowDays(Number(value || TRADE_BACKTEST_DEFAULTS.windowDays))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>最低评分</span>
              <InputNumber
                min={0}
                max={100}
                value={minScore}
                onChange={(value) => setMinScore(Number(value || TRADE_BACKTEST_DEFAULTS.minScore))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>初始资金</span>
              <InputNumber
                min={10_000}
                max={100_000_000}
                step={10_000}
                value={initialCapital}
                onChange={(value) => setInitialCapital(Number(value || TRADE_BACKTEST_DEFAULTS.initialCapital))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>单笔仓位占比(%)</span>
              <InputNumber
                min={1}
                max={100}
                step={1}
                suffix="%"
                value={positionPctPercent}
                onChange={(value) => setPositionPctPercent(Number(value || toPercent(TRADE_BACKTEST_DEFAULTS.positionPct)))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>最大并发持仓</span>
              <InputNumber
                min={1}
                max={100}
                value={maxPositions}
                onChange={(value) => setMaxPositions(Number(value || TRADE_BACKTEST_DEFAULTS.maxPositions))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>最大股票数</span>
              <InputNumber
                min={20}
                max={2000}
                step={20}
                value={maxSymbols}
                onChange={(value) => setMaxSymbols(Number(value || TRADE_BACKTEST_DEFAULTS.maxSymbols))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>止损比例(%)</span>
              <InputNumber
                min={0}
                max={50}
                step={0.5}
                suffix="%"
                value={stopLossPercent}
                onChange={(value) => setStopLossPercent(Number(value || toPercent(TRADE_BACKTEST_DEFAULTS.stopLoss)))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>止盈比例(%)</span>
              <InputNumber
                min={0}
                max={150}
                step={0.5}
                suffix="%"
                value={takeProfitPercent}
                onChange={(value) => setTakeProfitPercent(Number(value || toPercent(TRADE_BACKTEST_DEFAULTS.takeProfit)))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>单边费率(bps)</span>
              <InputNumber
                min={0}
                max={500}
                step={0.5}
                value={feeBps}
                onChange={(value) => setFeeBps(Number(value || TRADE_BACKTEST_DEFAULTS.feeBps))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical">
              <span>启用 T+1</span>
              <Switch checked={enforceT1} onChange={setEnforceT1} />
            </Space>
          </Col>
          <Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>延迟入场天数(交易日)</span>
              <InputNumber
                min={1}
                max={5}
                value={entryDelayDays}
                disabled={!strategySupportsEntryDelay}
                onChange={(value) => setEntryDelayDays(Number(value || TRADE_BACKTEST_DEFAULTS.entryDelayDays))}
                style={{ width: '100%' }}
              />
            </Space>
          </Col></Row><details className="research-fold" ><summary>高级执行与退出设置</summary><div className="research-fold-body"><Row data-research="fields"><Col xs={24} md={6}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <span>筛选任务 run_id（可选）</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <Input
                  value={runId}
                  onChange={(event) => setRunId(event.target.value)}
                  placeholder="请输入筛选任务 run_id"
                />
                <Button loading={bindLatestRunMutation.isPending} onClick={handleBindLatestRunId}>
                  带入最新筛选
                </Button>
              </div>
            </Space>
          </Col>
            {!strategyUsesBuiltInEntrySignal ? (
              <Col xs={24} md={12}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>入场事件</span>
                  <Select
                    mode="multiple"
                    value={entryEvents}
                    onChange={setEntryEvents}
                    options={WY_EVENTS.map((event) => ({ label: event, value: event }))}
                  />
                </Space>
              </Col>
            ) : null}
            <Col xs={24} md={12}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>{strategyAllowsOptionalExitEvents ? '离场事件（可选）' : '离场事件'}</span>
                <Select
                  mode="multiple"
                  value={exitEvents}
                  onChange={setExitEvents}
                  placeholder={strategyAllowsOptionalExitEvents ? '可留空，改用风控规则退出' : undefined}
                  options={WY_EVENTS.map((event) => ({ label: event, value: event }))}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>高位回撤(%)</span>
                <InputNumber
                  min={0}
                  max={50}
                  step={0.5}
                  suffix="%"
                  value={trailingStopPercent}
                  onChange={(value) => setTrailingStopPercent(Number(value ?? 0))}
                  style={{ width: '100%' }}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>日内回撤减仓</span>
                <Switch
                  checked={intradayTrailingEnabled}
                  disabled={trailingStopPercent <= 0}
                  onChange={setIntradayTrailingEnabled}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>多条日K回撤清仓</span>
                <Switch
                  checked={dailyTrailingClearEnabled}
                  disabled={trailingStopPercent <= 0}
                  onChange={setDailyTrailingClearEnabled}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>日内减仓比例(%)</span>
                <InputNumber
                  min={10}
                  max={100}
                  step={5}
                  suffix="%"
                  disabled={!intradayTrailingEnabled || trailingStopPercent <= 0}
                  value={intradayTrailingReducePercent}
                  onChange={(value) => setIntradayTrailingReducePercent(Number(value ?? 50))}
                  style={{ width: '100%' }}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>日K确认天数</span>
                <InputNumber
                  min={1}
                  max={5}
                  disabled={!dailyTrailingClearEnabled || trailingStopPercent <= 0}
                  value={dailyTrailingConfirmDays}
                  onChange={(value) => setDailyTrailingConfirmDays(Number(value ?? TRADE_BACKTEST_DEFAULTS.dailyTrailingConfirmDays))}
                  style={{ width: '100%' }}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>最大持仓天数</span>
                <InputNumber
                  min={1}
                  max={365}
                  value={maxHoldDays}
                  onChange={(value) => setMaxHoldDays(Number(value || TRADE_BACKTEST_DEFAULTS.maxHoldDays))}
                  style={{ width: '100%' }}
                />
              </Space>
            </Col>
            {!strategyUsesBuiltInEntrySignal ? (
              <Col xs={24} md={6}>
                <Space orientation="vertical">
                  <span>要求事件顺序</span>
                  <Switch checked={requireSequence} onChange={setRequireSequence} />
                </Space>
              </Col>
            ) : null}
            {!strategyUsesBuiltInEntrySignal ? (
              <Col xs={24} md={6}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>最少事件数</span>
                  <InputNumber
                    min={0}
                    max={12}
                    value={minEventCount}
                    onChange={(value) => setMinEventCount(Number(value || TRADE_BACKTEST_DEFAULTS.minEventCount))}
                    style={{ width: '100%' }}
                  />
                </Space>
              </Col>
            ) : null}
            <Col xs={24} md={6}>
              <Space orientation="vertical">
                <span>启用同日优先级</span>
                <Switch checked={prioritizeSignals} onChange={setPrioritizeSignals} />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical">
                <span>延迟窗口失效保护</span>
                <Switch
                  checked={delayInvalidationEnabled}
                  disabled={!strategySupportsEntryDelay}
                  onChange={setDelayInvalidationEnabled}
                />
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>{strategyForcesLegacyPath ? '执行路径（当前策略固定旧路径）' : `执行路径（默认 ${defaultExecutionPathPreference}）`}</span>
                <Select
                  value={strategyForcesLegacyPath ? 'legacy' : executionPathPreference}
                  disabled={strategyForcesLegacyPath}
                  onChange={(value) => {
                    executionPathPreferenceInitializedRef.current = true
                    setExecutionPathPreference(value as BacktestExecutionPathPreference)
                  }}
                  options={[
                    { label: '自动', value: 'auto' },
                    { label: '矩阵路径', value: 'matrix' },
                    { label: '旧路径', value: 'legacy' },
                  ]}
                />
                {strategyForcesLegacyPath ? (
                  <span style={{ color: '#64748b', fontSize: 12 }}>
                    当前策略不支持矩阵路径，回测与收益平原都会自动按旧路径执行。
                  </span>
                ) : null}
              </Space>
            </Col>
            <Col xs={24} md={6}>
              <Space orientation="vertical">
                <span>高级分析</span>
                <Switch checked={enableAdvancedAnalysis} onChange={setEnableAdvancedAnalysis} />
              </Space>
            </Col>
            {prioritizeSignals ? (
              <Col xs={24} md={12}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>优先级模式</span>
                  <Radio.Group
                    value={priorityMode}
                    optionType="button"
                    onChange={(event) => setPriorityMode(event.target.value)}
                    options={[
                      { label: '阶段优先', value: 'phase_first' },
                      { label: '均衡', value: 'balanced' },
                      { label: '动量优先', value: 'momentum' },
                    ]}
                  />
                </Space>
              </Col>
            ) : null}
            {prioritizeSignals ? (
              <Col xs={24} md={12}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>同日 TopK（0 = 不限制）</span>
                  <InputNumber
                    min={0}
                    max={500}
                    value={priorityTopK}
                    onChange={(value) => setPriorityTopK(Number(value || TRADE_BACKTEST_DEFAULTS.priorityTopK))}
                    style={{ width: '100%' }}
                  />
                </Space>
              </Col>
            ) : null}</Row></div></details><details className="research-fold" ><summary>策略与数据说明</summary><div className="research-fold-body"><Row data-research="fields"><Col xs={24} md={18}>
              <DismissibleAlert
                dismissKey="backtest.strategy-info"
                type="info"
                showIcon
                title={selectedStrategy ? `策略：${selectedStrategy.name}` : '策略信息'}
                description={
                  selectedStrategy
                    ? `id=${selectedStrategy.strategy_id}, version=${selectedStrategy.version}, cap=matrix:${strategySupportsMatrix ? 1 : 0}|age:${strategySupportsSignalAgeFilter ? 1 : 0}|delay:${strategySupportsEntryDelay ? 1 : 0}`
                    : '未加载到策略目录，先按默认策略继续。'
                }
              />
            </Col>
              {!strategySupportsEntryDelay ? (
                <Col xs={24}>
                  <DismissibleAlert
                    dismissKey="backtest.unsupported-entry-delay-tip"
                    type="warning"
                    showIcon
                    title="当前策略不支持延迟入场"
                    description="已自动固定为 entry_delay_days=1，并禁用延迟窗口失效保护。"
                  />
                </Col>
              ) : null}
              {strategyUsesBuiltInEntrySignal ? (
                <Col xs={24}>
                  <DismissibleAlert
                    dismissKey={`backtest.eventless-strategy-tip.${strategyId}`}
                    type="info"
                    showIcon
                    title="当前策略直接用策略信号入场"
                    description="已自动关闭入场事件、最少事件数和事件顺序要求。离场事件可留空；留空时仅使用止损、止盈、回撤和持有天数等规则退出。"
                  />
                </Col>
              ) : null}
              <Col xs={24}>
                <DismissibleAlert
                  dismissKey="backtest.strategy-center-tip"
                  type="info"
                  showIcon
                  title="策略参数与预设已迁移到策略中心"
                  description={
                    <Space wrap size={8}>
                      <span>当前页面仅保留策略选择，参数统一在策略中心维护。</span>
                      <Link to="/strategy">前往策略中心</Link>
                    </Space>
                  }
                />
              </Col>
              {mode === 'full_market' ? (
                <Col xs={24}>
                  <DismissibleAlert
                    dismissKey={`backtest.full-market-roll-mode.${poolRollMode}`}
                    type="warning"
                    showIcon
                    title={
                      poolRollMode === 'daily'
                        ? '全市场-每日滚动：每个交易日全量重算候选池，结果最严格，耗时最高。'
                        : poolRollMode === 'weekly'
                          ? '全市场-每周滚动：每周首个交易日重算候选池，其余交易日沿用。'
                          : '全市场-持仓触发：首日建池；出现卖出信号当日重算候选池，卖出后下一交易日再刷新一轮。'
                    }
                    description={
                      poolRollMode === 'position'
                        ? '该模式更贴近“卖出后再补仓”的实盘节奏；仍建议控制回测区间与最大股票数。'
                        : '该模式计算量较大，建议缩短区间或降低“最大股票数”，并通过任务进度面板观察执行状态。'
                    }
                  />
                </Col>
              ) : null}
              {mode === 'trend_pool' ? (
                <Col xs={24}>
                  <DismissibleAlert
                    dismissKey={`backtest.trend-pool-roll-mode.${poolRollMode}`}
                    type={poolRollMode === 'position' ? 'info' : 'warning'}
                    showIcon
                    title={
                      poolRollMode === 'daily'
                        ? '每日滚动：每个交易日重算一次股票池，最严格，耗时最长。'
                        : poolRollMode === 'weekly'
                          ? '每周滚动：每周首个交易日重算股票池，速度更快。'
                          : '持仓触发：首日建池；出现卖出信号当日重算候选池，卖出后下一交易日再刷新一轮。'
                    }
                    description={
                      poolRollMode === 'daily' || poolRollMode === 'weekly'
                        ? '该模式使用后台任务执行，页面会按日期显示进度，耗时可能较久。'
                        : '该模式通过持仓空位触发滚动筛选，适合模拟“卖出后再补仓”的交易节奏。'
                    }
                  />
                </Col>
              ) : null}
              <Col xs={24} md={12}>
                <DismissibleAlert
                  dismissKey={`backtest.advanced-analysis-tip.${enableAdvancedAnalysis ? 'on' : 'off'}`}
                  type={enableAdvancedAnalysis ? 'info' : 'warning'}
                  showIcon
                  title={enableAdvancedAnalysis ? '已启用高级分析' : '已关闭高级分析'}
                  description={
                    enableAdvancedAnalysis
                      ? '将额外计算稳定性评分、市场状态拆分、蒙特卡洛压力测试和 Walk-forward 验证，耗时会增加。'
                      : '仅执行主回测，速度更快但不生成稳定性/压力测试/样本外验证。'
                  }
                />
              </Col>
              {prioritizeSignals ? (
                <Col xs={24}>
                  <DismissibleAlert
                    dismissKey="backtest.priority-mode-help"
                    type="info"
                    showIcon
                    title="优先级模式说明"
                    description={
                      <div>
                        <div>阶段优先：同日先按阶段分，再看质量分。</div>
                        <div>均衡：同日先看质量分，再看阶段分与事件权重。</div>
                        <div>动量优先：同日先看趋势分，再看质量分。</div>
                      </div>
                    }
                  />
                </Col>
              ) : null}</Row></div></details>

        <Space wrap data-research="toolbar" style={{ marginTop: 14 }}>
          <Button type="primary" loading={runLoading} onClick={handleRun}>
            开始回测
          </Button>
          {taskStatus && (taskStatus.status === 'running' || taskStatus.status === 'pending') ? (
            <Button
              loading={controlTaskMutation.isPending && controlTaskMutation.variables?.action === 'pause'}
              onClick={() => controlTaskMutation.mutate({ action: 'pause', taskId: taskStatus.task_id })}
            >
              暂停
            </Button>
          ) : null}
          {taskStatus && taskStatus.status === 'paused' ? (
            <Button
              type="default"
              loading={controlTaskMutation.isPending && controlTaskMutation.variables?.action === 'resume'}
              onClick={() => controlTaskMutation.mutate({ action: 'resume', taskId: taskStatus.task_id })}
            >
              继续
            </Button>
          ) : null}
          {taskStatus
            && (taskStatus.status === 'running' || taskStatus.status === 'pending' || taskStatus.status === 'paused') ? (
            <Button
              danger
              loading={controlTaskMutation.isPending && controlTaskMutation.variables?.action === 'cancel'}
              onClick={() => controlTaskMutation.mutate({ action: 'cancel', taskId: taskStatus.task_id })}
            >
              停止
            </Button>
          ) : null}
          {taskOptions.length > 0 ? (
            <Select
              style={{ minWidth: 320 }}
              placeholder="选择回测任务"
              value={selectedTaskId}
              options={taskOptions}
              onChange={(value) => setSelectedTask(String(value))}
            />
          ) : null}
          {runningTaskCount > 0 ? <Tag color="processing">{`运行中 ${runningTaskCount}`}</Tag> : null}
          {taskStatus ? (
            <Tag color={taskStatusColor(taskStatus.status)}>{taskStatusLabel(taskStatus.status)}</Tag>
          ) : null}
          {result ? <Tag color="green">{`${result.range.date_from} ~ ${result.range.date_to}`}</Tag> : null}
        </Space>
        {taskStatus ? (
          <Card
            size="small"
            title={renderModuleTitle('recent_task_progress', '最近任务进度')}
            style={{ marginTop: 12 }}
            extra={renderModuleToggle('recent_task_progress')}
          >
            {!collapsedModules.recent_task_progress ? (
              <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                <Progress
                  percent={Math.max(0, Math.min(100, Number(taskProgress?.percent ?? 0)))}
                  status={taskRunning ? 'active' : (taskStatus.status === 'succeeded' ? 'success' : 'normal')}
                />
                <div>{taskProgress?.message || '任务执行中...'}</div>
                {taskProgress?.current_date ? (
                  <div>当前日期：{taskProgress.current_date}</div>
                ) : (
                  <div>当前日期：准备中...</div>
                )}
                <div>
                  进度：{taskProgress?.processed_dates ?? 0} / {taskProgress?.total_dates ?? 0}
                </div>
                {taskProgress?.warning ? <Alert type="warning" showIcon title={taskProgress.warning} /> : null}
                {taskStageRows.rows.length > 0 ? (
                  <Card size="small" title={`阶段耗时（累计 ${(taskStageRows.totalMs / 1000).toFixed(2)}s）`}>
                    <Space orientation="vertical" size={6} style={{ width: '100%' }}>
                      {taskStageRows.rows.map((row) => (
                        <Space key={row.stageKey} wrap style={{ justifyContent: 'space-between', width: '100%' }}>
                          <span>{row.label}</span>
                          <Space wrap size={6}>
                            <Tag color="geekblue">{row.elapsedSecText}</Tag>
                            <Tag>{row.shareText}</Tag>
                          </Space>
                        </Space>
                      ))}
                    </Space>
                  </Card>
                ) : null}
              </Space>
            ) : null}
          </Card>
        ) : null}

      </Card>

      <Card data-research="plateau-config" hidden={!plateauView}
        title={renderModuleTitle('plateau_params', '收益平原参数与进度')}
        extra={renderModuleToggle('plateau_params')}
      >
        {!collapsedModules.plateau_params ? (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              <strong>一键生成完整平原</strong>
              <Button onClick={() => useDensePlateau(7)}>7 × 7 · 49点</Button>
              <Button onClick={() => useDensePlateau(11)}>11 × 11 · 121点</Button>
              <Button onClick={() => useDensePlateau(21)}>21 × 21 · 441点</Button>
            </Space>
            <Alert showIcon type={plateauGridPreview.error || plateauGridPreview.exceedsLimit || !plateauGridPreview.isSurface ? 'warning' : 'info'}
              title={plateauGridPreview.error || (plateauSamplingMode === 'grid' ? `完整网格 ${plateauGridPreview.dimensions || '1'}，共 ${plateauGridPreview.total} 点` : `区间采样 ${plateauSamplePoints} 点`)}
              description={plateauGridPreview.exceedsLimit ? '超过单次2000点，请减少参数取值，或选择区间采样。不会只截取网格前几个点。' : !plateauGridPreview.isSurface ? '当前不足两个变化轴，无法形成平原。请生成完整网格或为两个参数填写多个取值。' : '一键网格扫描止损3%–13%、止盈5%–35%，其余参数沿用当前回测。每个格子都是真实计算，空缺不会插值冒充结果。'} />
            <Row data-research="fields"><Col xs={24} md={8}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <span>采样模式</span>
                <Radio.Group
                  value={plateauSamplingMode}
                  optionType="button"
                  onChange={(event) => setPlateauSamplingMode(event.target.value)}
                  options={[
                    { label: '区间采样（高维研究）', value: 'lhs' },
                    { label: '完整网格（二维平原）', value: 'grid' },
                  ]}
                />
              </Space>
            </Col>
              <Col xs={24} md={8}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>{plateauSamplingMode === 'lhs' ? '采样点数' : '完整网格点数（自动计算）'}</span>
                  <InputNumber
                    min={1}
                    max={2000}
                    value={plateauSamplingMode === 'grid' ? plateauGridPreview.total : plateauSamplePoints}
                    disabled={plateauSamplingMode === 'grid'}
                    onChange={(value) => setPlateauSamplePoints(Number(value || 120))}
                    style={{ width: '100%' }}
                  />
                </Space>
              </Col>
              <Col xs={24} md={8}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>随机种子（可选）</span>
                  <InputNumber
                    min={0}
                    max={2_147_483_647}
                    value={plateauRandomSeed ?? undefined}
                    onChange={(value) => {
                      if (value === null || value === undefined) {
                        setPlateauRandomSeed(null)
                        return
                      }
                      setPlateauRandomSeed(Number(value))
                    }}
                    style={{ width: '100%' }}
                  />
                </Space>
              </Col></Row><details className="research-fold" ><summary>自定义参数范围</summary><div className="research-fold-body"><Row data-research="fields"><Col xs={24} md={6}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>信号窗口天数列表（window_days）</span>
                  <Input
                    value={plateauWindowListRaw}
                    placeholder="如: 40,60,80"
                    onChange={(event) => setPlateauWindowListRaw(event.target.value)}
                  />
                </Space>
              </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>最低评分列表（min_score）</span>
                    <Input
                      value={plateauMinScoreListRaw}
                      placeholder="如: 50,55,60"
                      onChange={(event) => setPlateauMinScoreListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>止损比例列表（stop_loss，%）</span>
                    <Input
                      value={plateauStopLossPctListRaw}
                      placeholder="如: 3,5,8"
                      onChange={(event) => setPlateauStopLossPctListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>止盈比例列表（take_profit，%）</span>
                    <Input
                      value={plateauTakeProfitPctListRaw}
                      placeholder="如: 10,15,20"
                      onChange={(event) => setPlateauTakeProfitPctListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>高位回撤列表（trailing_stop_pct，%）</span>
                    <Input
                      value={plateauTrailingStopPctListRaw}
                      placeholder="如: 0,3,5"
                      onChange={(event) => setPlateauTrailingStopPctListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>日内减仓比例列表（intraday_trailing_reduce_ratio，%）</span>
                    <Input
                      value={plateauIntradayReducePctListRaw}
                      placeholder="如: 30,50,70"
                      onChange={(event) => setPlateauIntradayReducePctListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>最大并发持仓列表（max_positions）</span>
                    <Input
                      value={plateauMaxPositionsListRaw}
                      placeholder="如: 3,5,8"
                      onChange={(event) => setPlateauMaxPositionsListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>单笔仓位占比列表（position_pct，%）</span>
                    <Input
                      value={plateauPositionPctListRaw}
                      placeholder="如: 10,15,20"
                      onChange={(event) => setPlateauPositionPctListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>最大股票数列表（max_symbols）</span>
                    <Input
                      value={plateauMaxSymbolsListRaw}
                      placeholder="如: 80,120,200"
                      onChange={(event) => setPlateauMaxSymbolsListRaw(event.target.value)}
                    />
                  </Space>
                </Col>
                <Col xs={24} md={6}>
                  <Space orientation="vertical" style={{ width: '100%' }}>
                    <span>同日 TopK 列表（priority_topk）</span>
                    <Input
                      value={plateauTopKListRaw}
                      placeholder="如: 0,5,10"
                      onChange={(event) => setPlateauTopKListRaw(event.target.value)}
                    />
                  </Space>
                </Col></Row></div></details>

            <Alert
              type="info"
              showIcon
              title="参数说明"
              description="空列表固定为当前回测参数。完整网格评估全部组合（最多2000点）；区间采样按各轴最小至最大值抽取指定点数。以上列表每轴最多64个值。"
            />

            <Space wrap data-research="toolbar">
              <Button type="primary" ghost loading={plateauLoading} onClick={handleRunPlateau}>
                开始平原扫描
              </Button>
              {plateauTaskStatus && (plateauTaskStatus.status === 'running' || plateauTaskStatus.status === 'pending') ? (
                <Button
                  loading={controlPlateauTaskMutation.isPending && controlPlateauTaskMutation.variables?.action === 'pause'}
                  onClick={() => controlPlateauTaskMutation.mutate({ action: 'pause', taskId: plateauTaskStatus.task_id })}
                >
                  暂停平原任务
                </Button>
              ) : null}
              {plateauTaskStatus && plateauTaskStatus.status === 'paused' ? (
                <Button
                  type="primary"
                  ghost
                  loading={controlPlateauTaskMutation.isPending && controlPlateauTaskMutation.variables?.action === 'resume'}
                  onClick={() => controlPlateauTaskMutation.mutate({ action: 'resume', taskId: plateauTaskStatus.task_id })}
                >
                  继续平原任务
                </Button>
              ) : null}
              {plateauTaskStatus
                && (plateauTaskStatus.status === 'running' || plateauTaskStatus.status === 'pending' || plateauTaskStatus.status === 'paused') ? (
                <Button
                  danger
                  loading={controlPlateauTaskMutation.isPending && controlPlateauTaskMutation.variables?.action === 'cancel'}
                  onClick={() => controlPlateauTaskMutation.mutate({ action: 'cancel', taskId: plateauTaskStatus.task_id })}
                >
                  停止平原任务
                </Button>
              ) : null}
              {plateauTaskOptions.length > 0 ? (
                <Select
                  style={{ minWidth: 280 }}
                  value={plateauSelectedTaskId}
                  options={plateauTaskOptions}
                  onChange={(value) => setSelectedPlateauTask(String(value))}
                />
              ) : null}
              {plateauTaskStatus ? (
                <Tag color={plateauTaskStatusColor(plateauTaskStatus.status)}>{plateauTaskStatusLabel(plateauTaskStatus.status)}</Tag>
              ) : null}
              {plateauTaskRunningCount > 0 ? <Tag color="processing">{`运行中 ${plateauTaskRunningCount}`}</Tag> : null}
              {plateauResult ? (
                <Tag color="green">
                  {`已评估 ${plateauResult.evaluated_combinations} / ${plateauResult.total_combinations}`}
                </Tag>
              ) : null}
              {plateauRecommendedPoint ? (
                <Tag color="processing">
                  {`推荐平原分 ${getPlateauPrimaryScore(plateauRecommendedPoint).toFixed(1)} | 收益 ${formatPct(plateauRecommendedPoint.stats.total_return)}`}
                </Tag>
              ) : null}
              {plateauPeakPoint ? (
                <Tag>{`峰值收益 ${formatPct(plateauPeakPoint.stats.total_return)} | 原始评分 ${Number(plateauPeakPoint.score ?? 0).toFixed(3)}`}</Tag>
              ) : null}
              {plateauTopRankOptions.length > 0 ? (
                <Select
                  style={{ minWidth: 260 }}
                  value={plateauApplyRank}
                  options={plateauTopRankOptions}
                  onChange={(value) => setPlateauApplyRank(Number(value))}
                />
              ) : null}
              {plateauRecommendedPoint ? (
                <Button onClick={() => applyPlateauPointToForm(plateauRecommendedPoint, '推荐区域中心')}>
                  回填推荐参数
                </Button>
              ) : null}
              {plateauRecommendedPoint ? (
                <Button onClick={() => savePlateauPreset(plateauRecommendedPoint, '推荐区域中心')}>
                  保存推荐参数
                </Button>
              ) : null}
              {selectedRankPoint ? (
                <Button onClick={() => applyPlateauPointToForm(selectedRankPoint, `第${plateauApplyRank}名`)}>
                  回填第 {plateauApplyRank} 名
                </Button>
              ) : null}
              {selectedRankPoint ? (
                <Button onClick={() => savePlateauPreset(selectedRankPoint, `第${plateauApplyRank}名`)}>
                  保存第 {plateauApplyRank} 名
                </Button>
              ) : null}
              {selectedCandidatePoint ? (
                <Button onClick={() => savePlateauPreset(selectedCandidatePoint, `候选#${plateauCandidatePickRank}`)}>
                  保存候选参数
                </Button>
              ) : null}
            </Space>
            {plateauTaskStatus ? (
              <Card size="small" title="收益平原进度">
                <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                  <Progress
                    percent={Math.max(0, Math.min(100, Number(plateauTaskProgress?.percent ?? 0)))}
                    status={plateauTaskRunning ? 'active' : (plateauTaskStatus.status === 'succeeded' ? 'success' : 'normal')}
                  />
                  <div>{plateauTaskProgress?.message || '任务执行中...'}</div>
                  <div>
                    进度：{plateauTaskProgress?.processed_points ?? 0} / {plateauTaskProgress?.total_points ?? 0}
                  </div>
                </Space>
              </Card>
            ) : null}
            <details className="research-fold">
              <summary>收益平原历史回测列表（{plateauHistoryRows.length} 条）</summary>
              <div className="research-fold-body">
                <Table
                  size="small"
                  columns={plateauHistoryColumns}
                  dataSource={plateauHistoryRows}
                  rowKey={(row) => row.task_id}
                  loading={plateauHistoryLoading}
                  scroll={{ x: 1820 }}
                  pagination={{
                    defaultPageSize: 8,
                    pageSizeOptions: [8, 20, 50, 100],
                    showSizeChanger: true,
                    showTotal: (total) => `共 ${total} 条`,
                  }}
                />
              </div>
            </details>
          </Space>
        ) : null}
      </Card>

      <Modal
        title={plateauTradeViewingRow ? `收益平原交易记录 - 第${plateauTradeViewingRow.__rank}名` : '收益平原交易记录'}
        open={Boolean(plateauTradeViewingRow)}
        onCancel={() => {
          if (plateauTradeViewLoading) return
          setPlateauTradeViewingRow(null)
          setPlateauTradeViewResult(null)
          setPlateauTradeViewError(null)
        }}
        destroyOnHidden
        width={1200}
        footer={plateauTradeViewingRow ? [
          <Button
            key="close"
            disabled={plateauTradeViewLoading}
            onClick={() => {
              setPlateauTradeViewingRow(null)
              setPlateauTradeViewResult(null)
              setPlateauTradeViewError(null)
            }}
          >
            关闭
          </Button>,
          <Button
            key="fill"
            type="primary"
            disabled={Boolean(plateauTradeViewingRow.error)}
            onClick={() => applyPlateauPointToForm(plateauTradeViewingRow, `第${plateauTradeViewingRow.__rank}名`)}
          >
            回填参数
          </Button>,
        ] : null}
      >
        {plateauTradeViewingRow ? (
          <Space orientation="vertical" size={10} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color="processing">{`评分 ${plateauTradeViewingRow.score.toFixed(3)}`}</Tag>
              <Tag>{`收益 ${formatPct(plateauTradeViewingRow.stats.total_return)}`}</Tag>
              <Tag>{`回撤 ${formatPct(plateauTradeViewingRow.stats.max_drawdown)}`}</Tag>
              <Tag>{`胜率 ${formatPct(plateauTradeViewingRow.stats.win_rate)}`}</Tag>
              <Tag>{`预估交易数 ${plateauTradeViewingRow.stats.trade_count}`}</Tag>
            </Space>
            {plateauTradeViewingRow.error ? (
              <Alert type="error" showIcon title={`该参数组评估失败：${plateauTradeViewingRow.error}`} />
            ) : plateauTradeViewLoading ? (
              <div className="loading-center">
                <Spin />
                <div className="spin-text">正在加载交易记录...</div>
              </div>
            ) : plateauTradeViewError ? (
              <Alert type="error" showIcon title={`交易记录加载失败：${plateauTradeViewError}`} />
            ) : plateauTradeViewResult ? (
              <>
                {plateauTradeViewResult.equity_curve.length > 0 ? (
                  <Card size="small" title="收益曲线">
                    <ReactECharts option={plateauTradeEquityOption} style={{ height: 240 }} />
                  </Card>
                ) : (
                  <Alert type="info" showIcon title="暂无收益曲线数据" />
                )}
                <Space wrap>
                  <Tag color="success">{`实际交易数 ${plateauTradeViewResult.stats.trade_count}`}</Tag>
                  <Tag>{`实际收益 ${formatPct(plateauTradeViewResult.stats.total_return)}`}</Tag>
                  <Tag>{`实际回撤 ${formatPct(plateauTradeViewResult.stats.max_drawdown)}`}</Tag>
                  <Tag>{`实际胜率 ${formatPct(plateauTradeViewResult.stats.win_rate)}`}</Tag>
                </Space>
                <EditableDetailTable
                  settingsKey="backtest-trades"
                  size="small"
                  columns={tradeColumns}
                  dataSource={plateauTradeViewResult.trades}
                  rowKey={(row) => `${row.symbol}-${row.entry_date}-${row.exit_date}-${row.quantity}`}
                  scroll={{ x: 1480, y: 480 }}
                  pagination={{
                    current: plateauTradePage,
                    pageSize: plateauTradePageSize,
                    pageSizeOptions: [20, 50, 100],
                    showSizeChanger: true,
                    showTotal: (total) => `共 ${total} 条`,
                    onChange: (page, pageSize) => {
                      if (pageSize !== plateauTradePageSize) {
                        setPlateauTradePageSize(pageSize)
                        setPlateauTradePage(1)
                        return
                      }
                      setPlateauTradePage(page)
                    },
                  }}
                />
              </>
            ) : (
              <Alert type="info" showIcon title="暂无交易记录数据" />
            )}
          </Space>
        ) : null}
      </Modal>

      <section data-research="plateau-results" hidden={!plateauView}>{effectivePlateauError ? <Alert type="error" title={effectivePlateauError} showIcon /> : null}{plateauResult ? (
        <>
          {isLegacyRhythmResult(plateauResult.base_payload.strategy_id, plateauResult.strategy_version) ? (
            <Alert type="error" showIcon title="旧版节奏波结果需要重新回测" description="这份报告使用修复前的策略实现，不能作为节奏波收益或稳定区域的依据。原始记录保留供核对，请使用当前策略重新运行。" />
          ) : null}
          {!plateauRecommendedPoint ? (
            <Alert type="warning" showIcon title="未发现符合条件的稳定区域" description="本次不推荐任何参数。仍可查看、回填或保存单组计算结果，但它们不代表通过了稳定性判断。" />
          ) : null}
          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="总组合" value={plateauResult.total_combinations} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="评估组数" value={plateauResult.evaluated_combinations} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="推荐平原分" value={plateauSummaryPoint ? getPlateauPrimaryScore(plateauSummaryPoint) : '—'} precision={1} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic
                  title="推荐收益"
                  value={plateauSummaryPoint ? plateauSummaryPoint.stats.total_return * 100 : '—'}
                  precision={2}
                  suffix={plateauSummaryPoint ? '%' : undefined}
                />
              </Card>
            </Col>
          </Row>

          {plateauRegions.length > 0 ? (
            <Card title="自动识别收益平原区域">
              <Row gutter={[12, 12]}>
                {plateauRegions.slice(0, 3).map((region) => (
                  <Col xs={24} lg={8} key={region.region_id}>
                    <Card size="small" title={`Region ${region.region_rank}`}>
                      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                        <Space wrap>
                          <Tag color="geekblue">{`区域分 ${region.region_score.toFixed(1)}`}</Tag>
                          <Tag color="processing">{`点数 ${region.point_count}`}</Tag>
                          {typeof region.oos_pass_rate === 'number' ? (
                            <Tag color={region.oos_pass_rate >= 60 ? 'success' : 'warning'}>{`样本外 ${region.oos_pass_rate.toFixed(1)}%`}</Tag>
                          ) : null}
                        </Space>
                        <div>{`中心参数收益 ${formatPct(region.center_point.stats.total_return)} | 回撤 ${formatPct(region.center_point.stats.max_drawdown)}`}</div>
                        <div>{`中心参数: window=${region.center_point.params.window_days}, min=${Number(region.center_point.params.min_score).toFixed(2)}, maxPos=${region.center_point.params.max_positions}`}</div>
                        <div>{`区间: ${Object.entries(region.parameter_ranges).slice(0, 4).map(([key, value]) => `${PLATEAU_AXIS_LABEL_MAP[key as PlateauAxisKey]}=${value}`).join(' | ')}`}</div>
                        {region.walk_forward?.notes?.length ? (
                          <Alert type="info" showIcon title={region.walk_forward?.notes?.[0] ?? 'walk-forward 未生成有效折叠'} />
                        ) : null}
                        <Space wrap>
                          <Button size="small" onClick={() => applyPlateauPointToForm(region.center_point, `Region ${region.region_rank} 中心`)}>
                            回填区域中心
                          </Button>
                          <Button size="small" onClick={() => savePlateauPreset(region.center_point, `Region ${region.region_rank}`)}>
                            保存区域中心
                          </Button>
                        </Space>
                      </Space>
                    </Card>
                  </Col>
                ))}
              </Row>
            </Card>
          ) : null}

          {plateauScoreBarOption ? (
            <Card title="收益平原分可视化（Top 30）">
              <ReactECharts option={plateauScoreBarOption} style={{ height: 220 }} />
            </Card>
          ) : null}

          {plateauCorrelationRows.length > 0 ? (
            <Card title="参数相关性分析（原始评分/收益/胜率）">
              <Table
                size="small"
                columns={plateauCorrelationColumns}
                dataSource={plateauCorrelationRows}
                rowKey={(row) => row.parameter}
                pagination={false}
                scroll={{ x: 760 }}
              />
            </Card>
          ) : (
            <Alert type="info" showIcon title="参数相关性分析暂不可用" description="至少需要 2 组成功参数，才能计算相关性。" />
          )}

          <Card size="small" title="热力图设置">
            <Row gutter={[12, 12]}>
              <Col xs={24} md={8}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>横轴参数</span>
                  <Select aria-label="热力图横轴" style={{ width: '100%' }} value={plateauHeatmapXAxis}
                    options={PLATEAU_AXIS_OPTIONS.filter(item => item.value !== plateauHeatmapYAxis)}
                    onChange={value => setPlateauHeatmapXAxis(value as PlateauAxisKey)} />
                </Space>
              </Col>
              <Col xs={24} md={8}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>纵轴参数</span>
                  <Select aria-label="热力图纵轴" style={{ width: '100%' }} value={plateauHeatmapYAxis}
                    options={PLATEAU_AXIS_OPTIONS.filter(item => item.value !== plateauHeatmapXAxis)}
                    onChange={value => setPlateauHeatmapYAxis(value as PlateauAxisKey)} />
                </Space>
              </Col>
              <Col xs={24} md={8}>
                <Space orientation="vertical" style={{ width: '100%' }}>
                  <span>颜色映射指标</span>
                  <Select aria-label="热力图颜色指标" style={{ width: '100%' }} value={plateauHeatmapMetric}
                    options={PLATEAU_METRIC_OPTIONS} onChange={value => setPlateauHeatmapMetric(value as PlateauMetricKey)} />
                </Space>
              </Col>
            </Row>
            <Space wrap style={{ marginTop: 12 }}>
              <span>显示方块值</span><Switch checked={plateauHeatmapShowCellLabel} onChange={setPlateauHeatmapShowCellLabel} />
              <span>列内峰值线</span><Switch checked={plateauHeatmapShowBestPath} onChange={setPlateauHeatmapShowBestPath} />
            </Space>
          </Card>

          {plateauHeatmapOption ? (
            <Card title={plateauHeatmapTitle}>
              <Row gutter={[12, 12]}>
                <Col xs={24} lg={16} xl={17}>
                  <ReactECharts
                    option={plateauHeatmapOption}
                    style={{ height: plateauHeatmapChartHeight, width: '100%' }}
                    onEvents={plateauHeatmapOnEvents}
                  />
                </Col>
                <Col xs={24} lg={8} xl={7}>
                  <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                    <Alert
                      type="info"
                      showIcon
                      title="交互说明"
                      description="方块展示该格参数组的中位数；点击时会回填该格收益平原分最高的代表点。橙线仅表示当前横轴下的列内峰值，不代表可执行的参数路径。"
                    />

                    <Card size="small" title="候选参数集">
                      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                        <Space wrap>
                          <Tag color="blue">{`框选方块: ${plateauBrushSelectedKeys.length}`}</Tag>
                          <Tag color="geekblue">{`候选参数: ${plateauCandidatePoints.length}`}</Tag>
                        </Space>
                        <Space wrap>
                          <Button size="small" disabled={plateauBrushSelectedKeys.length <= 0} onClick={handleAddBrushSelectionToCandidates}>
                            加入候选
                          </Button>
                          <Button
                            size="small"
                            disabled={plateauBrushSelectedKeys.length <= 0}
                            onClick={() => setPlateauBrushSelectedKeys([])}
                          >
                            清空框选
                          </Button>
                        </Space>
                        {plateauCandidateOptions.length > 0 ? (
                          <Select
                            value={plateauCandidatePickRank}
                            options={plateauCandidateOptions}
                            onChange={(value) => setPlateauCandidatePickRank(Number(value))}
                            size="small"
                          />
                        ) : null}
                        <Space wrap>
                          <Button size="small" disabled={!selectedCandidatePoint} onClick={handleApplySelectedCandidatePoint}>
                            回填候选
                          </Button>
                          <Button size="small" disabled={plateauCandidatePoints.length <= 0} onClick={handleClearCandidatePoints}>
                            清空候选
                          </Button>
                        </Space>
                      </Space>
                    </Card>
                  </Space>
                </Col>
              </Row>
            </Card>
          ) : (
            <Alert
              type="info"
              showIcon
              title="热力图暂不可用"
              description={
                plateauHeatmapUnavailableReason
                || '当前参数分布不足以形成二维网格，请扩展参数扫描范围后重试。'
              }
            />
          )}

          <Card
            title="收益平原结果"
            extra={(
              <Space size={8} wrap>
                <span className="text-muted">默认按原始评分倒序，可点击表头切换并自动记住</span>
                <Button
                  size="small"
                  onClick={handleResetPlateauTableSort}
                  disabled={plateauUsingDefaultSort}
                >
                  恢复默认排序
                </Button>
              </Space>
            )}
          >
            <Table
              size="small"
              columns={plateauColumnsWithAction}
              dataSource={plateauTableRows}
              rowKey="__rowKey"
              scroll={{ x: 1460 }}
              sortDirections={['ascend', 'descend', 'ascend']}
              onChange={handlePlateauTableChange}
              pagination={{
                defaultPageSize: 10,
                pageSizeOptions: [10, 20, 50, 100],
                showSizeChanger: true,
                showTotal: (total) => `共 ${total} 条`,
              }}
            />
          </Card>

          <Card title="平原运行说明">
            {plateauResult.notes.length === 0 ? (
              <span>无说明。</span>
            ) : (
              <ul className="ul-list">
                {plateauResult.notes.map((note, idx) => (
                  <li key={`${idx}-${note}`}>{note}</li>
                ))}
              </ul>
            )}
          </Card>
        </>
      ) : null}</section>

      <section data-research="backtest-results" hidden={plateauView}>{effectiveRunError ? <Alert type="error" title={effectiveRunError} showIcon /> : null}<div ref={backtestResultSectionRef} />{result && isLegacyRhythmResult(result.strategy_id, result.strategy_version) ? (
        <Alert type="error" showIcon title="旧版节奏波结果需要重新回测" description="这份报告使用修复前的策略实现，买点可能并非节奏波信号。请保留原记录供核对，并使用当前策略重新运行。" />
      ) : null}{result ? (
        <>
          <Alert
            type="info"
            showIcon
            title="执行路径与策略快照"
            description={(
              <Space wrap>
                <Tag color={result.execution_path === 'matrix' ? 'processing' : 'default'}>
                  {`path=${result.execution_path ?? 'unknown'}`}
                </Tag>
                <Tag>{`strategy=${result.strategy_id ?? 'unknown'}@${result.strategy_version ?? 'unknown'}`}</Tag>
                <Tag>{`params=${result.strategy_params_hash || 'unknown'}`}</Tag>
              </Space>
            )}
          />

          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="成交笔数" value={result.stats.trade_count} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="胜率" value={result.stats.win_rate * 100} precision={2} suffix="%" />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="总收益" value={result.stats.total_return * 100} precision={2} suffix="%" />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="最大回撤" value={result.stats.max_drawdown * 100} precision={2} suffix="%" />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="候选信号" value={result.candidate_count} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="跳过信号" value={result.skipped_count} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="成交率" value={result.fill_rate * 100} precision={2} suffix="%" />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="最大并发持仓" value={result.max_concurrent_positions} />
              </Card>
            </Col>
          </Row>

          {riskMetrics ? (
            <Row gutter={[12, 12]}>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="Sharpe" value={riskMetrics.sharpe} precision={3} />
                </Card>
              </Col>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="Sortino" value={riskMetrics.sortino} precision={3} />
                </Card>
              </Col>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="Calmar" value={riskMetrics.calmar} precision={3} />
                </Card>
              </Col>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="Expectancy" value={riskMetrics.expectancy * 100} precision={2} suffix="%" />
                </Card>
              </Col>
            </Row>
          ) : (
            <Alert type="info" showIcon title="当前结果未包含风险指标。" />
          )}

          {stabilityDiagnostics ? (
            <Card title="稳定性评分">
              <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                <Space wrap>
                  <Tag color="processing">{`评分 ${stabilityDiagnostics.stability_score.toFixed(3)}`}</Tag>
                  <Tag color="default">{`邻域一致性 ${stabilityDiagnostics.neighborhood_consistency.toFixed(3)}`}</Tag>
                  <Tag color="warning">{`交易数惩罚 ${stabilityDiagnostics.trade_count_penalty.toFixed(3)}`}</Tag>
                  <Tag color="warning">{`方差惩罚 ${stabilityDiagnostics.return_variance_penalty.toFixed(3)}`}</Tag>
                </Space>
                {stabilityDiagnostics.notes.length > 0 ? (
                  <ul className="ul-list">
                    {stabilityDiagnostics.notes.map((note, idx) => (
                      <li key={`${idx}-${note}`}>{note}</li>
                    ))}
                  </ul>
                ) : null}
                {stabilityAdvice ? (
                  <Alert
                    type={stabilityAdvice.level}
                    showIcon
                    title={stabilityAdvice.title}
                    description={
                      stabilityAdvice.tips.length > 0 ? (
                        <ul className="ul-list">
                          {stabilityAdvice.tips.map((tip, index) => (
                            <li key={`${index}-${tip}`}>{tip}</li>
                          ))}
                        </ul>
                      ) : undefined
                    }
                  />
                ) : null}
              </Space>
            </Card>
          ) : null}

          {regimeBreakdown.length > 0 ? (
            <Card title="市场状态拆分（代理）">
              <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                <Table
                  size="small"
                  columns={regimeColumns}
                  dataSource={regimeBreakdown}
                  rowKey={(row) => row.regime}
                  pagination={false}
                  scroll={{ x: 760 }}
                />
                {regimeAdvice ? (
                  <Alert
                    type={regimeAdvice.level}
                    showIcon
                    title={regimeAdvice.title}
                    description={
                      regimeAdvice.tips.length > 0 ? (
                        <ul className="ul-list">
                          {regimeAdvice.tips.map((tip, index) => (
                            <li key={`${index}-${tip}`}>{tip}</li>
                          ))}
                        </ul>
                      ) : undefined
                    }
                  />
                ) : null}
              </Space>
            </Card>
          ) : null}

          {monteCarlo ? (
            <Card title="蒙特卡洛压力测试">
              <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                <Space wrap>
                  <Tag color="geekblue">{`模拟次数 ${monteCarlo.simulations}`}</Tag>
                  <Tag>{`收益 P5/P50/P95 ${formatPct(monteCarlo.total_return_p5)} / ${formatPct(monteCarlo.total_return_p50)} / ${formatPct(monteCarlo.total_return_p95)}`}</Tag>
                  <Tag>{`回撤 P5/P50/P95 ${formatPct(monteCarlo.max_drawdown_p5)} / ${formatPct(monteCarlo.max_drawdown_p50)} / ${formatPct(monteCarlo.max_drawdown_p95)}`}</Tag>
                  <Tag color="red">{`极端亏损概率 ${(monteCarlo.ruin_probability * 100).toFixed(2)}%`}</Tag>
                </Space>
                {monteCarloAdvice ? (
                  <Alert
                    type={monteCarloAdvice.level}
                    showIcon
                    title={monteCarloAdvice.title}
                    description={
                      monteCarloAdvice.tips.length > 0 ? (
                        <ul className="ul-list">
                          {monteCarloAdvice.tips.map((tip, index) => (
                            <li key={`${index}-${tip}`}>{tip}</li>
                          ))}
                        </ul>
                      ) : undefined
                    }
                  />
                ) : null}
              </Space>
            </Card>
          ) : null}

          {walkForward ? (
            <Card title="Walk-forward 验证">
              <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                <Space wrap>
                  <Tag color="processing">{`folds ${walkForward.fold_count}`}</Tag>
                  <Tag>{`候选参数 ${walkForward.candidate_count}`}</Tag>
                  <Tag color="green">{`OOS通过率 ${(walkForward.oos_pass_rate * 100).toFixed(2)}%`}</Tag>
                  <Tag>{`测试均值收益 ${formatPct(walkForward.avg_test_return)}`}</Tag>
                  <Tag>{`测试均值胜率 ${formatPct(walkForward.avg_test_win_rate)}`}</Tag>
                </Space>
                {walkForward.folds.length > 0 ? (
                  <Table
                    size="small"
                    columns={walkForwardColumns}
                    dataSource={walkForward.folds}
                    rowKey={(row) => `${row.fold_index}-${row.train_date_from}-${row.test_date_from}`}
                    pagination={false}
                    scroll={{ x: 1400 }}
                  />
                ) : null}
                {walkForward.notes.length > 0 ? (
                  <ul className="ul-list">
                    {walkForward.notes.map((note, idx) => (
                      <li key={`${idx}-${note}`}>{note}</li>
                    ))}
                  </ul>
                ) : null}
                {walkForwardAdvice ? (
                  <Alert
                    type={walkForwardAdvice.level}
                    showIcon
                    title={walkForwardAdvice.title}
                    description={
                      walkForwardAdvice.tips.length > 0 ? (
                        <ul className="ul-list">
                          {walkForwardAdvice.tips.map((tip, index) => (
                            <li key={`${index}-${tip}`}>{tip}</li>
                          ))}
                        </ul>
                      ) : undefined
                    }
                  />
                ) : null}
              </Space>
            </Card>
          ) : null}

          <Card title={<Space><span>资金曲线</span><span className="text-hint">（点击数据点或选择日期查看当日交易和持仓）</span></Space>}>
            <ReactECharts option={equityOption} style={{ height: 420 }} onEvents={equityCurveOnEvents} />
            <Space style={{ marginTop: 12 }}>
              <span>选择日期：</span>
              <Select
                size="small"
                showSearch
                placeholder="选择交易日"
                value={equityDateDetailDate ?? undefined}
                onChange={(val: string) => setEquityDateDetailDate(val || null)}
                allowClear
                style={{ width: 160 }}
                options={(result?.equity_curve ?? []).map((p) => ({ label: p.date, value: p.date })).reverse()}
              />
              <Button
                size="small"
                disabled={!equityDateDetailDate || (result?.equity_curve ?? []).findIndex((p) => p.date === equityDateDetailDate) <= 0}
                onClick={() => {
                  const curve = result?.equity_curve ?? []
                  const idx = curve.findIndex((p) => p.date === equityDateDetailDate)
                  if (idx > 0) setEquityDateDetailDate(curve[idx - 1].date)
                }}
              >前一天</Button>
              <Button
                size="small"
                disabled={!equityDateDetailDate || (result?.equity_curve ?? []).findIndex((p) => p.date === equityDateDetailDate) >= (result?.equity_curve ?? []).length - 1}
                onClick={() => {
                  const curve = result?.equity_curve ?? []
                  const idx = curve.findIndex((p) => p.date === equityDateDetailDate)
                  if (idx >= 0 && idx < curve.length - 1) setEquityDateDetailDate(curve[idx + 1].date)
                }}
              >后一天</Button>
            </Space>
            {equityDateDetailDate && result ? (() => {
              const datePoint = result.equity_curve.find((p) => p.date === equityDateDetailDate)
              const trades = result.trades
              const equityDates = result.equity_curve.map((p) => p.date)
              const isLastDay = equityDates.length > 0 && equityDates[equityDates.length - 1] === equityDateDetailDate
              // open on last day = still held, not real sells
              const isOpenHolding = (t: BacktestTrade) => isBacktestOpenHolding(t)
              const soldTrades = trades.filter(
                (t) => !isOpenHolding(t) && t.exit_date === equityDateDetailDate,
              )
              const boughtTrades = mergeBacktestEntryLegTrades(
                trades.filter((t) => t.entry_date === equityDateDetailDate),
              )
              const heldTrades = trades.filter((t) => {
                if (isOpenHolding(t)) {
                  return t.entry_date <= equityDateDetailDate
                }
                return t.entry_date <= equityDateDetailDate && equityDateDetailDate < t.exit_date
              })
              const totalEquity = datePoint?.equity ?? 0
              const columns = buildEquityDateDetailColumns(totalEquity)
              // 次日计划卖出：当日已触发 event_exit / trailing_stop，且 exit_date 晚于当前查看日
              const planSellTrades = heldTrades.filter((t) =>
                !isOpenHolding(t) &&
                t.exit_date > equityDateDetailDate &&
                isTrailingPlanSellReason(t.exit_reason)
              )
              // 次日计划买入：使用后端 plan_signals，末日额外合并 open_signals。
              const hasPlanSignals = Array.isArray(result.plan_signals)
              const nextDayBuyFromSignals = hasPlanSignals
                ? (result.plan_signals ?? []).filter((t) => t.signal_date === equityDateDetailDate)
                : trades.filter((t) => t.signal_date === equityDateDetailDate)
              const seenSymbols = new Set(nextDayBuyFromSignals.map((t) => t.symbol))
              const lastDayExtra = isLastDay ? (result.open_signals ?? []).filter((t) => !seenSymbols.has(t.symbol)) : []
              const allBuyPlans = [...nextDayBuyFromSignals, ...lastDayExtra]
              const planSellSymbols = new Set(planSellTrades.map((t) => t.symbol))
              const pureHeldTrades = heldTrades.filter((t) => !planSellSymbols.has(t.symbol))
              return (
                <Space orientation="vertical" size={12} style={{ width: '100%', marginTop: 8 }}>
                  <Space wrap>
                    <Tag color="processing">{`${equityDateDetailDate}`}</Tag>
                    {datePoint ? (
                      <>
                        <Tag>{`总权益 ${formatMoney(datePoint.equity)}`}</Tag>
                        <Tag>{`已实现盈亏 ${formatMoney(datePoint.realized_pnl)}`}</Tag>
                      </>
                    ) : null}
                    <Tag color="success">{`持仓 ${countDistinctBacktestSymbols(heldTrades)} 只`}</Tag>
                    <Tag color="blue">{`买入 ${boughtTrades.length} 只`}</Tag>
                    <Tag color="orange">{`卖出 ${soldTrades.length} 只`}</Tag>
                  </Space>
                  {boughtTrades.length > 0 ? (
                    <Card size="small" title={`当日买入 (${boughtTrades.length} 只)`}>
                      <Table size="small" columns={columns} dataSource={boughtTrades} rowKey={(row) => `buy-${buildBacktestEntryGroupKey(row)}`} scroll={{ x: 1200 }} pagination={false} />
                    </Card>
                  ) : null}
                  {soldTrades.length > 0 ? (
                    <Card size="small" title={`当日卖出 (${soldTrades.length} 只)`}>
                      <Table size="small" columns={columns} dataSource={soldTrades} rowKey={(row) => `sell-${row.symbol}-${row.exit_date}`} scroll={{ x: 1200 }} pagination={false} />
                    </Card>
                  ) : null}
                  <Card size="small" title={`当日持仓 (${countDistinctBacktestSymbols(heldTrades)} 只)`}>
                    {heldTrades.length > 0 ? (
                      <Table size="small" columns={columns} dataSource={heldTrades} rowKey={(row) => `hold-${buildBacktestEntryGroupKey(row)}-${row.exit_date}-${row.exit_reason}-${row.quantity}`} scroll={{ x: 1200 }} pagination={false} />
                    ) : (
                      <span className="text-hint">当日无持仓</span>
                    )}
                  </Card>
                  <Card size="small" title={<Space><span>次日操作计划</span>{planSellTrades.length > 0 ? <Tag color="red">{`计划卖出 ${planSellTrades.length} 只`}</Tag> : null}<Tag color="blue">{`买入信号 ${allBuyPlans.length} 只`}</Tag>{pureHeldTrades.length > 0 ? <Tag color="orange">{`持仓待管 ${pureHeldTrades.length} 只`}</Tag> : null}</Space>}>
                    {planSellTrades.length > 0 ? (
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontWeight: 600, marginBottom: 4, color: '#cf1322' }}>计划卖出（当日触发卖出信号，次日开盘卖出）</div>
                        <Table size="small" columns={columns} dataSource={planSellTrades} rowKey={(row) => `plan-sell-${row.symbol}-${row.exit_date}`} scroll={{ x: 1200 }} pagination={false} />
                      </div>
                    ) : null}
                    {pureHeldTrades.length > 0 ? (
                      <div style={{ marginBottom: allBuyPlans.length > 0 ? 12 : 0 }}>
                        <div style={{ fontWeight: 600, marginBottom: 4, color: '#d46b08' }}>持仓待管（暂无卖出信号）</div>
                        <Table size="small" columns={columns} dataSource={pureHeldTrades} rowKey={(row) => `plan-hold-${row.symbol}-${row.entry_date}`} scroll={{ x: 1200 }} pagination={false} />
                      </div>
                    ) : null}
                    <div>
                      <div style={{ fontWeight: 600, marginBottom: 4, color: '#1890ff' }}>买入信号（当日触发；计划买入日见下表，实际成交受持仓/资金/优先级约束）</div>
                      {allBuyPlans.length > 0 ? (
                        <Table size="small" columns={buildPlanBuyColumns(trades)} dataSource={allBuyPlans} rowKey={(row) => `plan-${row.symbol}-${row.signal_date}`} scroll={{ x: 1200 }} pagination={false} />
                      ) : (
                        <span className="text-hint">当日无买入信号</span>
                      )}
                      {allBuyPlans.some((plan) => !trades.some((trade) => trade.symbol === plan.symbol && trade.signal_date === plan.signal_date)) ? (
                        <span className="text-hint" style={{ display: 'block', marginTop: 8 }}>
                          未成交常见原因：最大持仓已满、资金不足、同日更高优先级信号抢先、或 T+1 下样本内无可卖出日。详见下方「运行说明」中的组合约束/延迟跳过统计。
                        </span>
                      ) : null}
                      {isLastDay && pureHeldTrades.some((t) => isOpenHolding(t)) ? (
                        <span className="text-hint" style={{ display: 'block', marginTop: 8 }}>
                          回测截止日仍持仓：延长结束日期后，可能因出现下一交易日 K 线而触发回撤/事件离场，此前「持仓待管」会变为「计划卖出」。
                        </span>
                      ) : null}
                    </div>
                  </Card>
                </Space>
              )
            })() : null}
          </Card>
          <Card title="回撤曲线">
            <ReactECharts option={drawdownOption} style={{ height: 220 }} />
          </Card>

          <Card
            title={renderModuleTitle('run_notes', '运行说明')}
            extra={renderModuleToggle('run_notes')}
          >
            {!collapsedModules.run_notes ? (
              result.notes.length === 0 ? (
                <span>无说明。</span>
              ) : (
                <ul className="ul-list">
                  {result.notes.map((note, idx) => (
                    <li key={`${idx}-${note}`}>{note}</li>
                  ))}
                </ul>
              )
            ) : null}
          </Card>

          <Card
            title={
              <Space>
                <span>末日未入场信号</span>
                {result.open_signals && result.open_signals.length > 0 ? (
                  <Tag color="cyan">{result.open_signals.length} 只</Tag>
                ) : null}
                <span className="text-hint">（回测区间最后一日因T+1无法入场的买入信号，不计入盈亏统计）</span>
              </Space>
            }
          >
            {result.open_signals && result.open_signals.length > 0 ? (
              <Table
                size="small"
                columns={openSignalColumns}
                dataSource={result.open_signals}
                rowKey={(row) => `open-${row.symbol}-${row.signal_date}`}
                scroll={{ x: 1100 }}
                pagination={{
                  defaultPageSize: 25,
                  pageSizeOptions: [10, 25, 50],
                  showSizeChanger: true,
                  showTotal: (total) => `共 ${total} 只`,
                }}
              />
            ) : (
              <span className="text-hint">回测区间最后一日无符合条件且无法入场的买入信号。</span>
            )}
          </Card>

          <Card
            title="交易明细"
          >
            <EditableDetailTable
              settingsKey="backtest-trades"
              toolbarExtra={(
                <Dropdown
                  trigger={['click']}
                  menu={buildTradeDetailExportMenu()}
                  disabled={!result.trades.length}
                >
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    loading={tradeDetailExportingFormat !== null}
                    onClick={(event) => event.stopPropagation()}
                  >
                    {tradeDetailExportingFormat
                      ? `导出${TRADE_EXPORT_LABEL_MAP[tradeDetailExportingFormat]}`
                      : '导出交易明细'}
                  </Button>
                </Dropdown>
              )}
              size="small"
              columns={tradeColumns}
              dataSource={result.trades}
              rowKey={(row) => `${row.symbol}-${row.entry_date}-${row.exit_date}-${row.quantity}`}
              scroll={{ x: 1480 }}
              pagination={{
                current: tradePage,
                pageSize: tradePageSize,
                showSizeChanger: true,
                pageSizeOptions: [10, 20, 25, 50, 100],
                showTotal: (total) => `共 ${total} 条`,
                onChange: (page, pageSize) => {
                  if (pageSize !== tradePageSize) {
                    setTradePageSize(pageSize)
                    setTradePage(1)
                    return
                  }
                  setTradePage(page)
                },
              }}
            />
          </Card>

          <Card
            title="收益构成"
            extra={(
              <Radio.Group
                size="small"
                value={tradePnlChartType}
                onChange={(event) => setTradePnlChartType(event.target.value as TradePnlChartType)}
              >
                <Radio.Button value="pie">饼图</Radio.Button>
                <Radio.Button value="bar">柱状图</Radio.Button>
                <Radio.Button value="treemap">方块图</Radio.Button>
              </Radio.Group>
            )}
          >
            {symbolPnlAggregates.length > 0 ? (
              <ReactECharts option={tradePnlChartOption} style={{ height: 420 }} />
            ) : (
              <span className="text-hint">暂无交易数据，无法生成收益构成图。</span>
            )}
          </Card>
        </>
      ) : (
        <Alert type="info" title="暂无回测结果，请先设置参数并点击“开始回测”。" showIcon />
      )}</section>

      <Card data-research="backtest-history" hidden={plateauView}
        size="small"
        title={renderModuleTitle('backtest_history', '策略回测历史任务列表')}
        style={{ marginTop: 12 }}
        extra={renderModuleToggle('backtest_history')}
      >
        {!collapsedModules.backtest_history ? (
          <Table
            size="small"
            columns={backtestHistoryColumns}
            dataSource={backtestHistoryRows}
            rowKey={(row) => row.task_id}
            scroll={{ x: 1740 }}
            pagination={{
              defaultPageSize: 8,
              pageSizeOptions: [8, 20, 50, 100],
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }}
          />
        ) : null}
      </Card>

      <Card data-research="plateau-presets" hidden={!plateauView}
        title={renderModuleTitle('plateau_presets', '参数收藏库（本地持久化）')}
        extra={renderModuleToggle('plateau_presets')}
      >
        {!collapsedModules.plateau_presets ? (
          <Space orientation="vertical" size={10} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color="geekblue">{`已收藏: ${plateauSavedPresets.length}`}</Tag>
              {plateauSavedPresetOptions.length > 0 ? (
                <Select
                  style={{ minWidth: 420 }}
                  value={plateauSavedPresetId ?? undefined}
                  options={plateauSavedPresetOptions}
                  onChange={(value) => setPlateauSavedPresetId(String(value))}
                />
              ) : null}
              <Button disabled={!selectedSavedPreset} onClick={handleApplySavedPreset}>
                回填收藏参数
              </Button>
              <Button danger disabled={!selectedSavedPreset} onClick={handleDeleteSavedPreset}>
                删除当前收藏
              </Button>
            </Space>
            <Table
              size="small"
              columns={plateauSavedPresetColumns}
              dataSource={plateauSavedPresets}
              rowKey="id"
              scroll={{ x: 1220 }}
              pagination={{
                defaultPageSize: 8,
                pageSizeOptions: [8, 20, 50, 100],
                showSizeChanger: true,
                showTotal: (total) => `共 ${total} 条`,
              }}
            />
          </Space>
        ) : null}
      </Card>

      <Card
        title={renderModuleTitle('report_share', '回测报告导出与共享')}
        extra={renderModuleToggle('report_share')}
      >
        {!collapsedModules.report_share ? (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              <Button
                type="primary"
                ghost
                onClick={handleExportFtbt}
                disabled={!result}
                loading={exportReportMutation.isPending}
              >
                导出 ftbt
              </Button>
              <Button onClick={handleExportXlsxReport} disabled={!result}>
                导出 Excel
              </Button>
              <Button onClick={handleExportHtmlReport} disabled={!result}>
                导出 HTML
              </Button>
              <Button
                onClick={() => importReportFileRef.current?.click()}
                loading={importReportMutation.isPending}
              >
                导入 ftbt
              </Button>
              <input
                ref={importReportFileRef}
                type="file"
                accept=".ftbt"
                aria-label="导入回测报告"
                style={{ display: 'none' }}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  handleImportFtbt(file)
                  event.target.value = ''
                }}
              />
              <Tag color={plateauResult ? 'success' : 'default'}>
                {plateauResult
                  ? `本次导出包含收益平原（${plateauResult.evaluated_combinations}/${plateauResult.total_combinations}）`
                  : '本次导出不包含收益平原'}
              </Tag>
              <Tag color="blue">{`已导入 ${reportLibrary.length}`}</Tag>
            </Space>
            <Space wrap style={{ width: '100%' }}>
              <Select
                style={{ minWidth: 440 }}
                placeholder={reportLibraryLoading ? '加载报告列表中...' : '选择导入报告'}
                value={selectedReportId ?? undefined}
                onChange={(value) => setSelectedReportId(String(value))}
                loading={reportLibraryLoading}
                options={reportLibrary.map((item) => ({
                  value: item.report_id,
                  label: `${item.report_id} | ${item.date_from}~${item.date_to} | 收益 ${formatPct(item.total_return)}`,
                }))}
              />
              <Button
                type="primary"
                onClick={handleLoadSelectedReport}
                disabled={!selectedReportId}
                loading={loadReportMutation.isPending}
              >
                加载报告
              </Button>
              <Button
                danger
                onClick={handleDeleteSelectedReport}
                disabled={!selectedReportId}
                loading={deleteReportMutation.isPending}
              >
                删除报告
              </Button>
            </Space>
            <Alert
              type="info"
              showIcon
              title="导入说明"
              description="可直接导出 Excel/HTML；.ftbt 用于跨设备共享（内含报告HTML、Excel与回测数据）。重复导入同一 report_id 将覆盖旧报告并保留导入时间。"
            />
          </Space>
        ) : null}
      </Card>
    </Space>
  )
}
