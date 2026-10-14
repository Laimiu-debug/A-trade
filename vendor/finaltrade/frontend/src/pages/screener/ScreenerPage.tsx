import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './ScreenerPage.css'
import { useMutation, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Dropdown,
  Input,
  InputNumber,
  Popover,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { MenuProps } from 'antd'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { DownOutlined, SettingOutlined, ShoppingCartOutlined, UpOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { jsPDF } from 'jspdf'
import autoTable from 'jspdf-autotable'
import * as XLSX from 'xlsx'
import { PageHeader } from '@/shared/components/PageHeader'
import { DismissibleAlert } from '@/shared/components/DismissibleAlert'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { buildPageTitle } from '@/state/aiAssistantStore'
import { useAIParameterApplyStore } from '@/state/aiParameterApplyStore'
import { ApiError } from '@/shared/api/client'
import { isApiNotFoundError } from '@/shared/api/errors'
import { getScreenerRun, getStrategies, runB1Screener, runScreener } from '@/shared/api/endpoints'
import { clearScreenerRunIdFromCaches } from '@/shared/screenerRunCache'
import {
  filterVisibleStrategies,
  getSharedLastStrategyId,
  resolveDefaultStrategyId,
  setSharedLastStrategyId,
} from '@/shared/utils/strategyParams'
import { useUIStore } from '@/state/uiStore'
import { upsertPendingBuyDraft } from '@/shared/utils/simPendingOrders'
import type {
  ScreenerParams,
  ScreenerPoolKey,
  ScreenerRunDetail,
  ScreenerResult,
  ScreenerStepConfigs,
  ScreenerStepPools,
  StrategyId,
  ThemeStage,
  TrendClass,
  WatchPoolConfig,
} from '@/types/contracts'
import { formatLargeMoney, formatPct } from '@/shared/utils/format'
import {
  formatScreenerRankRange,
  getLocalDayKey,
  getScreenerPathState,
  isScreenerCacheMarketDataFresh,
  type ScreenerPathState,
  normalizeScreenerStep1RankRange,
  SCREENER_STEP1_POOL_CAP,
  SCREENER_STEP1_POOL_MIN_WARN,
  SCREENER_STEP1_RANK_END_MIN,
  sliceRowsByScreenerRankRange,
} from '@/shared/utils/screener'
import { loadScreenerUserPrefs, saveScreenerUserPrefs } from '@/shared/utils/screenerPrefs'

const formSchema = z.object({
  board_filters: z.array(z.enum(['main', 'gem', 'star', 'beijing', 'st'])).min(1, '至少选择一个板块'),
  mode: z.enum(['strict', 'loose']),
  as_of_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal('')),
  return_window_days: z.number().min(5).max(120),
  top_n: z.number().min(SCREENER_STEP1_RANK_END_MIN).max(2000),
  turnover_threshold: z.number().min(0.01).max(0.2),
  amount_threshold: z.number().min(5e7).max(5e9),
  amplitude_threshold: z.number().min(0.01).max(0.15),
})

type FormValues = z.infer<typeof formSchema>
type ScreenerRunMeta = { runId: string; degraded: boolean; degradedReason?: string; asOfDate?: string }
type StepConfigs = ScreenerStepConfigs

const defaultStepConfigs: StepConfigs = {
  step1: {
    rank_start: 1,
    top_n: 500,
    turnover_threshold: 0.05,
    amount_threshold: 5e8,
    amplitude_threshold: 0.03,
  },
  step2: {
    retrace_min: 0.05,
    retrace_max: 0.25,
    max_pullback_days: 3,
    min_ma10_above_ma20_days: 5,
    min_ma5_above_ma10_days: 3,
    max_price_vs_ma20: 0.08,
    require_above_ma20: true,
    allow_b_trend: false,
  },
  step3: {
    min_vol_slope20: 0.05,
    min_up_down_volume_ratio: 1.3,
    max_pullback_volume_ratio: 0.9,
    allow_blowoff_top: false,
    allow_divergence_5d: false,
    allow_upper_shadow_risk: false,
    allow_degraded: false,
  },
  step4: {
    final_top_n: 8,
    min_ai_confidence: 0.55,
    allowed_theme_stages: ['发酵中', '高潮'],
    allow_degraded: true,
  },
}

const defaultFormValues: FormValues = {
  board_filters: ['main', 'gem', 'star'],
  mode: 'strict',
  as_of_date: '',
  return_window_days: 40,
  top_n: 500,
  turnover_threshold: 0.05,
  amount_threshold: 5e8,
  amplitude_threshold: 0.03,
}

const poolOrder: ScreenerPoolKey[] = ['input', 'step1', 'step2', 'step3', 'step4', 'final', 'watch']
const defaultPendingOrderQuantity = 1000

const poolStepIndex: Record<ScreenerPoolKey, number> = {
  input: 0,
  step1: 1,
  step2: 2,
  step3: 3,
  step4: 4,
  final: 0,
  watch: 0,
}

const poolLabelMap: Record<ScreenerPoolKey, string> = {
  input: '输入池',
  step1: '第1步通过',
  step2: '第2步通过',
  step3: '第3步通过',
  step4: '第4步通过',
  final: '终选池',
  watch: '观察池',
}

const emptyStepPools: ScreenerStepPools = {
  input: [],
  step1: [],
  step2: [],
  step3: [],
  step4: [],
  final: [],
  watch: [],
}

const previousPoolByStep: Record<1 | 2 | 3 | 4, ScreenerPoolKey> = {
  1: 'input',
  2: 'step1',
  3: 'step2',
  4: 'step3',
}

type ScreenerColumnKey =
  | 'symbol'
  | 'name'
  | 'latest_price'
  | 'day_change'
  | 'day_change_pct'
  | 'ret40'
  | 'turnover20'
  | 'amount20'
  | 'amplitude20'
  | 'retrace20'
  | 'pullback_days'
  | 'trend_class'
  | 'stage'
  | 'path_state'
  | 'score'
  | 'ai_confidence'
  | 'theme_stage'

const defaultColumnOrder: ScreenerColumnKey[] = [
  'symbol',
  'name',
  'latest_price',
  'day_change',
  'day_change_pct',
  'ret40',
  'turnover20',
  'amount20',
  'amplitude20',
  'retrace20',
  'pullback_days',
  'trend_class',
  'stage',
  'path_state',
  'score',
  'ai_confidence',
  'theme_stage',
]

const tableColumnMap: Record<ScreenerColumnKey, ColumnsType<ScreenerResult>[number]> = {
  symbol: { key: 'symbol', title: '代码', dataIndex: 'symbol', width: 110, fixed: 'left' },
  name: { key: 'name', title: '名称', dataIndex: 'name', width: 120 },
  latest_price: {
    key: 'latest_price',
    title: '最新收盘',
    dataIndex: 'latest_price',
    width: 100,
    render: (value: number) => (typeof value === 'number' ? value.toFixed(2) : '--'),
  },
  day_change: {
    key: 'day_change',
    title: '当日涨跌',
    dataIndex: 'day_change',
    width: 100,
    render: (value: number) => (
      typeof value === 'number' ? (
      <Typography.Text type={value >= 0 ? 'danger' : 'success'}>
        {value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2)}
      </Typography.Text>
      ) : '--'
    ),
  },
  day_change_pct: {
    key: 'day_change_pct',
    title: '当日涨跌幅',
    dataIndex: 'day_change_pct',
    width: 110,
    render: (value: number) => (
      typeof value === 'number' ? (
      <Typography.Text type={value >= 0 ? 'danger' : 'success'}>
        {value >= 0 ? `+${formatPct(value)}` : formatPct(value)}
      </Typography.Text>
      ) : '--'
    ),
  },
  ret40: {
    key: 'ret40',
    title: '涨幅',
    dataIndex: 'ret40',
    width: 110,
    render: (value: number) => formatPct(value),
  },
  turnover20: {
    key: 'turnover20',
    title: '换手',
    dataIndex: 'turnover20',
    width: 110,
    render: (value: number) => formatPct(value),
  },
  amount20: {
    key: 'amount20',
    title: '成交额',
    dataIndex: 'amount20',
    width: 130,
    render: (value: number) => formatLargeMoney(value),
  },
  amplitude20: {
    key: 'amplitude20',
    title: '振幅',
    dataIndex: 'amplitude20',
    width: 110,
    render: (value: number) => formatPct(value),
  },
  retrace20: {
    key: 'retrace20',
    title: '20日回撤',
    dataIndex: 'retrace20',
    width: 110,
    render: (value: number) => formatPct(value),
  },
  pullback_days: {
    key: 'pullback_days',
    title: '回调天数',
    dataIndex: 'pullback_days',
    width: 100,
  },
  trend_class: {
    key: 'trend_class',
    title: '趋势',
    dataIndex: 'trend_class',
    width: 90,
    render: (value: string) => <Tag color={value === 'B' ? 'red' : 'green'}>{value}</Tag>,
  },
  stage: { key: 'stage', title: '阶段', dataIndex: 'stage', width: 90 },
  path_state: {
    key: 'path_state',
    title: '走势状态',
    dataIndex: 'path_state',
    width: 120,
    render: (_: unknown, row: ScreenerResult) => {
      const value = getScreenerPathState(row)
      const colorMap: Record<ScreenerPathState, string> = {
        上涨路径中: 'success',
        回踩蓄势: 'processing',
        高位回撤: 'error',
        整理观察: 'default',
      }
      return <Tag color={colorMap[value]}>{value}</Tag>
    },
  },
  score: { key: 'score', title: '综合分', dataIndex: 'score', width: 90 },
  ai_confidence: {
    key: 'ai_confidence',
    title: 'AI置信度',
    dataIndex: 'ai_confidence',
    width: 110,
    render: (value: number) => formatPct(value),
  },
  theme_stage: { key: 'theme_stage', title: '题材阶段', dataIndex: 'theme_stage', width: 110 },
}

function buildColumnTitleMap(returnWindowDays: number): Record<ScreenerColumnKey, string> {
  return {
    symbol: '代码',
    name: '名称',
    latest_price: '最新收盘',
    day_change: '当日涨跌',
    day_change_pct: '当日涨跌幅',
    ret40: `${returnWindowDays}日涨幅`,
    turnover20: '20日平均换手',
    amount20: '20日平均成交额',
    amplitude20: '20日平均振幅',
    retrace20: '20日回撤',
    pullback_days: '回调天数',
    trend_class: '趋势',
    stage: '阶段',
    path_state: '走势状态',
    score: '综合分',
    ai_confidence: 'AI置信度',
    theme_stage: '题材阶段',
  }
}
type ExportFormat = 'csv' | 'xlsx' | 'pdf'

const exportFormatLabelMap: Record<ExportFormat, string> = {
  csv: 'CSV',
  xlsx: 'Excel',
  pdf: 'PDF',
}

const MANUAL_LABEL = '手工添加'
const SCREENER_CACHE_KEY = 'tdx-trend-screener-cache-v6'
const SIGNAL_RUN_CACHE_KEY = 'tdx-signals-run-id-v1'
const SIGNAL_BOARD_FILTERS_CACHE_KEY = 'tdx-signals-board-filters-v1'
const BACKTEST_FORM_CACHE_KEY = 'tdx-trend-backtest-form-v2'
const PDF_CJK_FONT_FILE = 'LXGWWenKai-Regular.ttf'
const PDF_CJK_FONT_NAME = 'LxgwWenKai'
const PDF_CJK_FONT_URL = '/fonts/LXGWWenKai-Regular.ttf'
type StageType = 'Early' | 'Mid' | 'Late'
type BoardFilterKey = 'main' | 'gem' | 'star' | 'beijing' | 'st'
type TableSortField =
  | 'manual'
  | 'score'
  | 'ret40'
  | 'turnover20'
  | 'amount20'
  | 'amplitude20'
  | 'retrace20'
  | 'latest_price'
  | 'day_change_pct'
  | 'theme_stage'
type TableSortDirection = 'asc' | 'desc'

const trendClassLabelMap: Record<TrendClass, string> = {
  A: 'A 阶梯慢牛',
  A_B: 'A_B 慢牛加速',
  B: 'B 脉冲涨停',
  Unknown: 'Unknown',
}

const stageLabelMap: Record<StageType, string> = {
  Early: '早期',
  Mid: '中期',
  Late: '后期',
}

const boardFilterLabelMap: Record<BoardFilterKey, string> = {
  main: '主板',
  gem: '创业板',
  star: '科创板',
  beijing: '北交所',
  st: 'ST',
}

const sortableFields: TableSortField[] = [
  'score',
  'ret40',
  'turnover20',
  'amount20',
  'amplitude20',
  'retrace20',
  'latest_price',
  'day_change_pct',
  'theme_stage',
]

function isTableSortField(value: string): value is TableSortField {
  return (
    value === 'manual'
    || value === 'score'
    || value === 'ret40'
    || value === 'turnover20'
    || value === 'amount20'
    || value === 'amplitude20'
    || value === 'retrace20'
    || value === 'latest_price'
    || value === 'day_change_pct'
    || value === 'theme_stage'
  )
}

function isTrendClass(value: string): value is TrendClass {
  return value === 'A' || value === 'A_B' || value === 'B' || value === 'Unknown'
}

function isStageType(value: string): value is StageType {
  return value === 'Early' || value === 'Mid' || value === 'Late'
}

function isThemeStage(value: string): value is ThemeStage {
  return value === '发酵中' || value === '高潮' || value === '退潮' || value === 'Unknown'
}

function isScreenerColumnKey(value: string): value is ScreenerColumnKey {
  return (
    value === 'symbol'
    || value === 'name'
    || value === 'latest_price'
    || value === 'day_change'
    || value === 'day_change_pct'
    || value === 'ret40'
    || value === 'turnover20'
    || value === 'amount20'
    || value === 'amplitude20'
    || value === 'retrace20'
    || value === 'pullback_days'
    || value === 'trend_class'
    || value === 'stage'
    || value === 'path_state'
    || value === 'score'
    || value === 'ai_confidence'
    || value === 'theme_stage'
  )
}

function normalizeStockName(name: string) {
  return name.toUpperCase().replace(/\s+/g, '')
}

function isStStock(row: ScreenerResult) {
  const name = normalizeStockName(row.name)
  return name.includes('ST')
}

function detectPrimaryBoard(row: ScreenerResult): Exclude<BoardFilterKey, 'st'> | null {
  const symbol = row.symbol.toLowerCase()
  if (symbol.length < 8) return null
  const market = symbol.slice(0, 2)
  const code = symbol.slice(2)
  if (market === 'bj') return 'beijing'
  if (market === 'sh') {
    if (code.startsWith('688') || code.startsWith('689')) return 'star'
    return 'main'
  }
  if (market === 'sz') {
    if (code.startsWith('300') || code.startsWith('301')) return 'gem'
    return 'main'
  }
  return null
}

function rowMatchesBoardFilters(row: ScreenerResult, filters: BoardFilterKey[]) {
  if (filters.length === 0) return false
  const selected = new Set(filters)
  const isSt = isStStock(row)
  if (isSt && !selected.has('st')) return false
  if (!isSt && selected.size === 1 && selected.has('st')) return false
  const board = detectPrimaryBoard(row)
  const selectedBoards = filters.filter((item) => item !== 'st')
  if (selectedBoards.length === 0) {
    return isSt
  }
  if (!board) return false
  return selectedBoards.includes(board)
}

function filterRowsByBoards(rows: ScreenerResult[], filters: BoardFilterKey[]) {
  if (!rows.length) return rows
  if (!filters.length) return []
  return rows.filter((row) => rowMatchesBoardFilters(row, filters))
}

interface ScreenerCachePayload {
  version: 1
  updated_at: string
  /** Local calendar day when market rows (prices) were last loaded from backend. */
  market_data_day?: string
  form_values: FormValues
  active_pool: ScreenerPoolKey
  executed_step: number
  pools: ScreenerStepPools
  run_meta?: ScreenerRunMeta
  step_configs: StepConfigs
  watch_config?: WatchPoolConfig
  column_order: ScreenerColumnKey[]
  column_visible: Record<ScreenerColumnKey, boolean>
  input_pool_key?: string
  raw_input_pool?: ScreenerResult[]
}

let memoryScreenerCache: ScreenerCachePayload | null = null
let pdfCjkFontBinaryCache: string | null = null
let pdfCjkFontLoadingPromise: Promise<string> | null = null

type JsPdfFontApi = jsPDF & {
  addFileToVFS: (filename: string, filecontent: string) => void
  addFont: (filename: string, fontname: string, fontstyle: string) => void
}

function defaultColumnVisibleState() {
  return defaultColumnOrder.reduce(
    (acc, key) => ({ ...acc, [key]: true }),
    {} as Record<ScreenerColumnKey, boolean>,
  )
}

function stripStaleScreenerMarketData(payload: ScreenerCachePayload): ScreenerCachePayload {
  const today = getLocalDayKey()
  if (payload.market_data_day === today) {
    return payload
  }
  const hadMarketRows = (payload.raw_input_pool?.length ?? 0) > 0
    || Object.values(payload.pools).some((rows) => (rows?.length ?? 0) > 0)
  if (hadMarketRows) {
    try {
      window.sessionStorage.setItem('tdx-trend-screener-stale-market-notice', '1')
    } catch {
      // ignore quota / privacy mode
    }
  }
  return {
    ...payload,
    executed_step: 0,
    pools: emptyStepPools,
    run_meta: undefined,
    input_pool_key: '',
    raw_input_pool: [],
    market_data_day: '',
  }
}

function createDefaultScreenerCache(): ScreenerCachePayload {
  return {
    version: 1,
    updated_at: new Date().toISOString(),
    market_data_day: '',
    form_values: defaultFormValues,
    active_pool: 'input',
    executed_step: 0,
    pools: emptyStepPools,
    run_meta: undefined,
    step_configs: defaultStepConfigs,
    watch_config: defaultWatchConfig,
    column_order: defaultColumnOrder,
    column_visible: defaultColumnVisibleState(),
    input_pool_key: '',
    raw_input_pool: [],
  }
}

function sanitizeStep3Config(raw: Partial<StepConfigs['step3']> | undefined): StepConfigs['step3'] {
  const merged = { ...defaultStepConfigs.step3, ...(raw ?? {}) }
  const isLegacyDefault =
    merged.min_vol_slope20 === 0.05
    && merged.min_up_down_volume_ratio === 1.3
    && merged.max_pullback_volume_ratio === 0.75
    && !merged.allow_blowoff_top
    && !merged.allow_divergence_5d
    && !merged.allow_upper_shadow_risk
    && !merged.allow_degraded
  if (!isLegacyDefault) return merged
  return {
    ...merged,
    max_pullback_volume_ratio: defaultStepConfigs.step3.max_pullback_volume_ratio,
  }
}

function sanitizeStep1Config(raw: Partial<StepConfigs['step1']> | undefined): StepConfigs['step1'] {
  return {
    ...defaultStepConfigs.step1,
    ...normalizeScreenerStep1RankRange({
      ...defaultStepConfigs.step1,
      ...(raw ?? {}),
    }),
    turnover_threshold:
      typeof raw?.turnover_threshold === 'number'
        ? raw.turnover_threshold
        : defaultStepConfigs.step1.turnover_threshold,
    amount_threshold:
      typeof raw?.amount_threshold === 'number'
        ? raw.amount_threshold
        : defaultStepConfigs.step1.amount_threshold,
    amplitude_threshold:
      typeof raw?.amplitude_threshold === 'number'
        ? raw.amplitude_threshold
        : defaultStepConfigs.step1.amplitude_threshold,
  }
}

function buildInputPoolKey(params: Pick<FormValues, 'return_window_days' | 'as_of_date'>) {
  const asOfDate = (params.as_of_date ?? '').trim()
  return `${params.return_window_days}:${asOfDate}`
}

function sanitizeColumnOrder(raw: unknown): ScreenerColumnKey[] {
  if (!Array.isArray(raw)) return defaultColumnOrder
  const valid = new Set(defaultColumnOrder)
  const picked = raw.filter((item): item is ScreenerColumnKey => valid.has(item as ScreenerColumnKey))
  const dedup: ScreenerColumnKey[] = []
  for (const key of picked) {
    if (!dedup.includes(key)) dedup.push(key)
  }
  for (const key of defaultColumnOrder) {
    if (!dedup.includes(key)) dedup.push(key)
  }
  return dedup
}

function sanitizeColumnVisible(raw: unknown): Record<ScreenerColumnKey, boolean> {
  const defaultVisible = defaultColumnVisibleState()
  if (!raw || typeof raw !== 'object') return defaultVisible
  return defaultColumnOrder.reduce(
    (acc, key) => ({
      ...acc,
      [key]: typeof (raw as Record<string, unknown>)[key] === 'boolean'
        ? ((raw as Record<string, unknown>)[key] as boolean)
        : defaultVisible[key],
    }),
    {} as Record<ScreenerColumnKey, boolean>,
  )
}

function sanitizeResultRows(raw: unknown): ScreenerResult[] {
  if (!Array.isArray(raw)) return []
  return raw.map((item) => {
    if (!item || typeof item !== 'object') return item as ScreenerResult
    const row = item as Partial<ScreenerResult>
    return {
      ...row,
      latest_price: typeof row.latest_price === 'number' ? row.latest_price : 0,
      day_change: typeof row.day_change === 'number' ? row.day_change : 0,
      day_change_pct: typeof row.day_change_pct === 'number' ? row.day_change_pct : 0,
    } as ScreenerResult
  })
}

function sanitizePools(raw: unknown): ScreenerStepPools {
  if (!raw || typeof raw !== 'object') return emptyStepPools
  const pools = raw as Partial<ScreenerStepPools>
  return {
    input: sanitizeResultRows(pools.input),
    step1: sanitizeResultRows(pools.step1),
    step2: sanitizeResultRows(pools.step2),
    step3: sanitizeResultRows(pools.step3),
    step4: sanitizeResultRows(pools.step4),
    final: sanitizeResultRows(pools.final),
    watch: sanitizeResultRows(pools.watch),
  }
}

function loadScreenerCache(): ScreenerCachePayload {
  if (memoryScreenerCache) {
    return memoryScreenerCache
  }
  const fallback = createDefaultScreenerCache()
  try {
    const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<ScreenerCachePayload>

    const stepConfigs = loadScreenerUserPrefs({
      step1: sanitizeStep1Config(parsed.step_configs?.step1),
      step2: { ...defaultStepConfigs.step2, ...(parsed.step_configs?.step2 ?? {}) },
      step3: sanitizeStep3Config(parsed.step_configs?.step3),
      step4: { ...defaultStepConfigs.step4, ...(parsed.step_configs?.step4 ?? {}) },
    })

    const formValues = {
      ...defaultFormValues,
      ...(parsed.form_values ?? {}),
      top_n: stepConfigs.step1.top_n,
      turnover_threshold: stepConfigs.step1.turnover_threshold,
      amount_threshold: stepConfigs.step1.amount_threshold,
      amplitude_threshold: stepConfigs.step1.amplitude_threshold,
    }

    const activePool = poolOrder.includes(parsed.active_pool as ScreenerPoolKey)
      ? (parsed.active_pool as ScreenerPoolKey)
      : 'input'

    const executedStep = Math.max(0, Math.min(4, Number(parsed.executed_step ?? 0)))
    const runMeta = parsed.run_meta && typeof parsed.run_meta.runId === 'string'
      ? parsed.run_meta
      : undefined

    const marketDataDay =
      typeof parsed.market_data_day === 'string' && parsed.market_data_day.trim()
        ? parsed.market_data_day.trim()
        : (isScreenerCacheMarketDataFresh(parsed.updated_at) ? getLocalDayKey() : '')

    const normalized = stripStaleScreenerMarketData({
      version: 1,
      updated_at: typeof parsed.updated_at === 'string' ? parsed.updated_at : fallback.updated_at,
      market_data_day: marketDataDay,
      form_values: formValues,
      active_pool: activePool,
      executed_step: executedStep,
      pools: sanitizePools(parsed.pools),
      run_meta: runMeta,
      step_configs: stepConfigs,
      column_order: sanitizeColumnOrder(parsed.column_order),
      column_visible: sanitizeColumnVisible(parsed.column_visible),
      input_pool_key: typeof parsed.input_pool_key === 'string' ? parsed.input_pool_key : '',
      raw_input_pool: sanitizeResultRows(parsed.raw_input_pool),
    })
    memoryScreenerCache = normalized
    return normalized
  } catch {
    return fallback
  }
}

function saveScreenerCache(payload: ScreenerCachePayload) {
  memoryScreenerCache = payload
  try {
    // Persist raw_input_pool along with everything else so page refresh doesn't lose data
    window.localStorage.setItem(SCREENER_CACHE_KEY, JSON.stringify(payload))
  } catch {
    // If quota exceeded, try again without raw_input_pool (large dataset)
    try {
      const fallback: ScreenerCachePayload = {
        ...payload,
        raw_input_pool: [],
      }
      window.localStorage.setItem(SCREENER_CACHE_KEY, JSON.stringify(fallback))
    } catch {
      // ignore further errors; runtime state remains available
    }
  }
}

function syncSharedTrendRunCaches(runId: string, boardFilters: BoardFilterKey[]) {
  if (!runId.trim()) return
  try {
    window.localStorage.setItem(SIGNAL_RUN_CACHE_KEY, runId)
    window.localStorage.setItem(SIGNAL_BOARD_FILTERS_CACHE_KEY, JSON.stringify(boardFilters))
  } catch {
    // ignore localStorage failures and keep the current page usable
  }
  try {
    const rawBacktestDraft = window.localStorage.getItem(BACKTEST_FORM_CACHE_KEY)
    const parsedBacktestDraft =
      rawBacktestDraft && rawBacktestDraft.trim().length > 0
        ? (JSON.parse(rawBacktestDraft) as Record<string, unknown>)
        : {}
    const nextBacktestDraft =
      parsedBacktestDraft && typeof parsedBacktestDraft === 'object'
        ? {
            ...parsedBacktestDraft,
            run_id: runId,
            board_filters: [...boardFilters],
          }
        : {
            run_id: runId,
            board_filters: [...boardFilters],
    }
    window.localStorage.setItem(BACKTEST_FORM_CACHE_KEY, JSON.stringify(nextBacktestDraft))
  } catch {
    // ignore parse/localStorage failures and keep the current page usable
  }
}

async function loadPdfCjkFontBinary() {
  if (pdfCjkFontBinaryCache) return pdfCjkFontBinaryCache
  if (pdfCjkFontLoadingPromise) return pdfCjkFontLoadingPromise

  pdfCjkFontLoadingPromise = (async () => {
    const response = await fetch(PDF_CJK_FONT_URL)
    if (!response.ok) {
      throw new Error(`字体文件加载失败 (${response.status})`)
    }
    const bytes = new Uint8Array(await response.arrayBuffer())
    // jsPDF 的 VFS 需要 8-bit binary string。
    let binary = ''
    try {
      binary = new TextDecoder('latin1').decode(bytes)
    } catch {
      const chunkSize = 0x8000
      for (let i = 0; i < bytes.length; i += chunkSize) {
        const chunk = bytes.subarray(i, i + chunkSize)
        binary += String.fromCharCode(...chunk)
      }
    }
    pdfCjkFontBinaryCache = binary
    return binary
  })()

  try {
    return await pdfCjkFontLoadingPromise
  } finally {
    pdfCjkFontLoadingPromise = null
  }
}

async function applyPdfCjkFont(doc: jsPDF) {
  try {
    const binary = await loadPdfCjkFontBinary()
    const fontDoc = doc as JsPdfFontApi
    fontDoc.addFileToVFS(PDF_CJK_FONT_FILE, binary)
    fontDoc.addFont(PDF_CJK_FONT_FILE, PDF_CJK_FONT_NAME, 'normal')
    doc.setFont(PDF_CJK_FONT_NAME, 'normal')
    return true
  } catch {
    return false
  }
}

function csvEscape(raw: string) {
  if (raw.includes(',') || raw.includes('"') || raw.includes('\n')) {
    return `"${raw.replaceAll('"', '""')}"`
  }
  return raw
}

function formatApiErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'REQUEST_TIMEOUT') {
      return '请求超时，请稍后重试或缩小筛选范围。'
    }
    if (error.code.startsWith('HTTP_5')) {
      return '后端服务不可用，请检查后端进程。'
    }
    return error.message
  }
  return '请求失败，请稍后重试。'
}

function formatTimestampForFile() {
  const now = new Date()
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const ss = String(now.getSeconds()).padStart(2, '0')
  return `${y}${m}${d}-${hh}${mm}${ss}`
}

function runStep1(inputPool: ScreenerResult[], config: StepConfigs['step1']) {
  const filtered = sliceRowsByScreenerRankRange(
    [...inputPool].sort((a, b) => b.ret40 - a.ret40),
    config,
  ).filter(
    (row) =>
      row.turnover20 >= config.turnover_threshold &&
      row.amount20 >= config.amount_threshold &&
      row.amplitude20 >= config.amplitude_threshold,
  )

  if (filtered.length > SCREENER_STEP1_POOL_CAP) return filtered.slice(0, SCREENER_STEP1_POOL_CAP)
  return filtered
}

function runStep2(
  step1Pool: ScreenerResult[],
  config: StepConfigs['step2'],
  mode: FormValues['mode'],
) {
  const loosePadding = mode === 'loose' ? 0.02 : 0
  const looseDays = mode === 'loose' ? 1 : 0
  const rawRetraceMin = Math.min(config.retrace_min, config.retrace_max)
  const rawRetraceMax = Math.max(config.retrace_min, config.retrace_max)
  const retraceMin = Math.max(0, rawRetraceMin - loosePadding)
  const retraceMax = Math.min(0.8, rawRetraceMax + loosePadding)
  const maxPullbackDays = config.max_pullback_days + looseDays
  const minMa10AboveMa20Days = Math.max(0, config.min_ma10_above_ma20_days - looseDays)
  const minMa5AboveMa10Days = Math.max(0, config.min_ma5_above_ma10_days - looseDays)
  return step1Pool.filter(
    (row) =>
      row.retrace20 >= retraceMin &&
      row.retrace20 <= retraceMax &&
      row.pullback_days <= maxPullbackDays &&
      row.ma10_above_ma20_days >= minMa10AboveMa20Days &&
      row.ma5_above_ma10_days >= minMa5AboveMa10Days &&
      Math.abs(row.price_vs_ma20) <= config.max_price_vs_ma20 &&
      (!config.require_above_ma20 || row.price_vs_ma20 >= 0) &&
      (config.allow_b_trend || row.trend_class !== 'B'),
  )
}

function runStep3(step2Pool: ScreenerResult[], config: StepConfigs['step3']) {
  return step2Pool.filter(
    (row) =>
      row.vol_slope20 >= config.min_vol_slope20 &&
      row.up_down_volume_ratio >= config.min_up_down_volume_ratio &&
      row.pullback_volume_ratio <= config.max_pullback_volume_ratio &&
      (config.allow_blowoff_top || !row.has_blowoff_top) &&
      (config.allow_divergence_5d || !row.has_divergence_5d) &&
      (config.allow_upper_shadow_risk || !row.has_upper_shadow_risk) &&
      (config.allow_degraded || !row.degraded),
  )
}

function runStep4(step3Pool: ScreenerResult[], config: StepConfigs['step4']) {
  const source = config.allow_degraded
    ? step3Pool
    : step3Pool.filter((row) => !row.degraded)
  return source
    .filter(
      (row) =>
        row.ai_confidence >= config.min_ai_confidence &&
        config.allowed_theme_stages.includes(row.theme_stage),
    )
    .sort((a, b) => b.score + b.ai_confidence * 20 - (a.score + a.ai_confidence * 20))
    .slice(0, config.final_top_n)
}

const defaultWatchConfig: WatchPoolConfig = {
  enabled: true,
  source_mode: 'filter',
  strategy_id: '',
  ret40_min: 0.2,
  ret40_max: 2.0,
  turnover20_min: 0.05,
  amount20_min: 5e8,
  amplitude20_min: 0.03,
  trend_classes: ['A', 'A_B'],
  retrace20_min: 0.0,
  retrace20_max: 0.3,
  pullback_days_max: 5,
  vol_slope20_min: 0.0,
  up_down_volume_ratio_min: 0.0,
  top_n: 50,
}

function runWatchPool(inputPool: ScreenerResult[], config: WatchPoolConfig) {
  if (!config.enabled || config.source_mode === 'strategy') return []
  return inputPool
    .filter(
      (row) =>
        row.ret40 >= config.ret40_min &&
        row.ret40 <= config.ret40_max &&
        row.turnover20 >= config.turnover20_min &&
        row.amount20 >= config.amount20_min &&
        row.amplitude20 >= config.amplitude20_min &&
        config.trend_classes.includes(row.trend_class) &&
        row.retrace20 >= config.retrace20_min &&
        row.retrace20 <= config.retrace20_max &&
        row.pullback_days <= config.pullback_days_max &&
        row.vol_slope20 >= config.vol_slope20_min &&
        row.up_down_volume_ratio >= config.up_down_volume_ratio_min,
    )
    .sort((a, b) => b.ret40 - a.ret40)
    .slice(0, config.top_n)
}

function mergeManualRows(calculatedRows: ScreenerResult[], currentRows: ScreenerResult[]) {
  const manualRows = currentRows.filter((row) => row.labels.includes(MANUAL_LABEL))
  if (manualRows.length === 0) return calculatedRows
  const existingSymbols = new Set(calculatedRows.map((row) => row.symbol))
  const appendedManual = manualRows.filter((row) => !existingSymbols.has(row.symbol))
  return [...appendedManual, ...calculatedRows]
}

interface LabeledNumberInputProps {
  label: string
  min?: number
  max?: number
  step?: number
  value?: number | null
  onChange?: (value: number | null) => void
  style?: CSSProperties
  className?: string
}

function LabeledNumberInput({ label, style, ...props }: LabeledNumberInputProps) {
  return (
    <Space.Compact className="screener-labeled-input-wrapper" style={style}>
      <div className="screener-labeled-input-label">
        {label}
      </div>
      <InputNumber {...props} className="screener-labeled-input-field" aria-label={label} />
    </Space.Compact>
  )
}

export function ScreenerPage() {
  const { message } = AntdApp.useApp()
  const navigate = useNavigate()
  const setSelectedSymbol = useUIStore((state) => state.setSelectedSymbol)
  const initialCache = useMemo(() => loadScreenerCache(), [])

  const [activePool, setActivePool] = useState<ScreenerPoolKey>(initialCache.active_pool)
  const [executedStep, setExecutedStep] = useState(initialCache.executed_step)
  const [pools, setPools] = useState<ScreenerStepPools>(initialCache.pools)
  const [runMeta, setRunMeta] = useState<ScreenerRunMeta | undefined>(initialCache.run_meta)
  const [stepConfigs, setStepConfigs] = useState<StepConfigs>(initialCache.step_configs)
  const [watchConfig, setWatchConfig] = useState<WatchPoolConfig>(initialCache.watch_config ?? defaultWatchConfig)
  const [runningStep, setRunningStep] = useState<number | null>(null)
  const [pendingAutoRecomputeStep, setPendingAutoRecomputeStep] = useState<1 | 2 | 3 | 4 | null>(null)
  const [columnOrder, setColumnOrder] = useState<ScreenerColumnKey[]>(initialCache.column_order)
  const [columnVisible, setColumnVisible] = useState<Record<ScreenerColumnKey, boolean>>(
    initialCache.column_visible,
  )
  const [draggingSymbol, setDraggingSymbol] = useState<string | null>(null)
  const [dragOverPool, setDragOverPool] = useState<ScreenerPoolKey | null>(null)
  const [dragOverSymbol, setDragOverSymbol] = useState<string | null>(null)
  const [draggingColumnKey, setDraggingColumnKey] = useState<ScreenerColumnKey | null>(null)
  const [dragOverColumnKey, setDragOverColumnKey] = useState<ScreenerColumnKey | null>(null)
  const [keywordFilter, setKeywordFilter] = useState('')
  const [trendFilters, setTrendFilters] = useState<TrendClass[]>([])
  const [stageFilters, setStageFilters] = useState<StageType[]>([])
  const [pathStateFilters, setPathStateFilters] = useState<ScreenerPathState[]>([])
  const [themeStageFilters, setThemeStageFilters] = useState<ThemeStage[]>([])
  const [sortField, setSortField] = useState<TableSortField>('manual')
  const [sortDirection, setSortDirection] = useState<TableSortDirection>('desc')
  const [tablePage, setTablePage] = useState(1)
  const [tablePageSize, setTablePageSize] = useState(8)
  const [inputPoolKey, setInputPoolKey] = useState(initialCache.input_pool_key ?? '')
  const [inputPoolDay, setInputPoolDay] = useState(initialCache.market_data_day ?? '')
  const [rawInputPool, setRawInputPool] = useState<ScreenerResult[]>(initialCache.raw_input_pool ?? [])
  const [b1Loading, setB1Loading] = useState(false)
  const [quickSearchKeyword, setQuickSearchKeyword] = useState('')
  const [strategyId, setStrategyId] = useState<StrategyId>(() => getSharedLastStrategyId('wyckoff_trend_v1'))

  const pageAIContext = useMemo(
    () => ({
      page: 'screener' as const,
      title: buildPageTitle('screener', runMeta?.runId ? runMeta.runId.slice(0, 12) : undefined),
      as_of_date: runMeta?.asOfDate,
      refs: runMeta?.runId ? { run_id: runMeta.runId } : ({} as Record<string, string>),
      payload: {
        executed_step: executedStep,
        active_pool: activePool,
        strategy_id: strategyId,
        step_configs: stepConfigs,
      },
    }),
    [runMeta, executedStep, activePool, strategyId, stepConfigs],
  )
  useRegisterPageAIContext(pageAIContext)

  const lastApplied = useAIParameterApplyStore((state) => state.lastApplied)
  const clearLastApplied = useAIParameterApplyStore((state) => state.clearLastApplied)
  useEffect(() => {
    if (!lastApplied?.length) return
    setStepConfigs((prev) => {
      let next = prev
      for (const change of lastApplied) {
        if (change.target === 'screener.step4.min_ai_confidence') {
          next = {
            ...next,
            step4: { ...next.step4, min_ai_confidence: Number(change.new_value) },
          }
        }
        if (change.target === 'screener.step4.final_top_n') {
          next = {
            ...next,
            step4: { ...next.step4, final_top_n: Number(change.new_value) },
          }
        }
      }
      return next
    })
    clearLastApplied()
  }, [lastApplied, clearLastApplied])

  const strategyCatalogQuery = useQuery({
    queryKey: ['strategy-catalog'],
    queryFn: getStrategies,
    staleTime: 5 * 60_000,
  })
  const strategyItems = useMemo(
    () => filterVisibleStrategies(strategyCatalogQuery.data?.items),
    [strategyCatalogQuery.data?.items],
  )
  const selectedStrategy = useMemo(
    () => strategyItems.find((item) => item.strategy_id === strategyId) ?? null,
    [strategyId, strategyItems],
  )
  const initialReturnWindowDays =
    typeof initialCache.form_values.return_window_days === 'number' && Number.isFinite(initialCache.form_values.return_window_days)
      ? initialCache.form_values.return_window_days
      : defaultFormValues.return_window_days

  const { control, handleSubmit, getValues, setValue, trigger } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: initialCache.form_values,
  })

  const mode = useWatch({
    control,
    name: 'mode',
    defaultValue: 'strict',
  })
  const returnWindowDays = useWatch({
    control,
    name: 'return_window_days',
    defaultValue: initialReturnWindowDays,
  })
  const watchedFormValues = useWatch({
    control,
    defaultValue: initialCache.form_values,
  }) as FormValues
  const currentInputPoolKey = useMemo(
    () =>
      buildInputPoolKey({
        return_window_days:
          watchedFormValues?.return_window_days ?? defaultFormValues.return_window_days,
        as_of_date: watchedFormValues?.as_of_date ?? defaultFormValues.as_of_date,
      }),
    [watchedFormValues],
  )
  const columnTitleMap = useMemo(
    () => buildColumnTitleMap(returnWindowDays),
    [returnWindowDays],
  )

  const cacheSnapshot = useMemo<ScreenerCachePayload>(
    () => ({
      version: 1,
      updated_at: new Date().toISOString(),
      form_values: watchedFormValues ?? defaultFormValues,
      active_pool: activePool,
      executed_step: executedStep,
      pools,
      run_meta: runMeta,
      step_configs: stepConfigs,
      watch_config: watchConfig,
      column_order: columnOrder,
      column_visible: columnVisible,
      input_pool_key: inputPoolKey,
      market_data_day: inputPoolDay,
      raw_input_pool: rawInputPool,
    }),
    [
      activePool,
      executedStep,
      pools,
      runMeta,
      stepConfigs,
      watchConfig,
      columnOrder,
      columnVisible,
      inputPoolKey,
      inputPoolDay,
      rawInputPool,
      watchedFormValues,
    ],
  )
  const latestCacheSnapshotRef = useRef<ScreenerCachePayload>(cacheSnapshot)
  const stepConfigsRef = useRef<StepConfigs>(stepConfigs)
  const recoveredFromRunRef = useRef(false)

  useEffect(() => {
    stepConfigsRef.current = stepConfigs
    saveScreenerUserPrefs(stepConfigs)
  }, [stepConfigs])

  useEffect(() => {
    if (strategyItems.length <= 0) return
    const allIds = new Set(strategyItems.map((item) => item.strategy_id))
    if (allIds.has(strategyId)) return
    setStrategyId(resolveDefaultStrategyId(strategyItems, 'wyckoff_trend_v1'))
  }, [strategyId, strategyItems])

  useEffect(() => {
    setSharedLastStrategyId(strategyId)
  }, [strategyId])

  useEffect(() => {
    try {
      if (window.sessionStorage.getItem('tdx-trend-screener-stale-market-notice') === '1') {
        window.sessionStorage.removeItem('tdx-trend-screener-stale-market-notice')
        message.info('行情数据已跨日，请重新点击「加载输入池」以刷新最新收盘价')
      }
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    latestCacheSnapshotRef.current = cacheSnapshot
    saveScreenerCache(cacheSnapshot)
  }, [cacheSnapshot])

  useEffect(() => () => {
    saveScreenerCache(latestCacheSnapshotRef.current)
  }, [])

  function buildScreenerParams(values: FormValues, configs: StepConfigs): ScreenerParams {
    const markets = ['sh', 'sz', 'bj'] as const
    const asOfDate = values.as_of_date.trim()
    return {
      markets: [...markets],
      mode: values.mode,
      as_of_date: asOfDate || undefined,
      return_window_days: values.return_window_days,
      top_n: values.top_n,
      turnover_threshold: values.turnover_threshold,
      amount_threshold: values.amount_threshold,
      amplitude_threshold: values.amplitude_threshold,
      step_configs: {
        step1: { ...configs.step1 },
        step2: { ...configs.step2 },
        step3: { ...configs.step3 },
        step4: {
          ...configs.step4,
          allowed_theme_stages: [...configs.step4.allowed_theme_stages],
        },
      },
    }
  }

  async function createScreenerRunDetail(values: FormValues) {
    const params = buildScreenerParams(values, stepConfigs)
    const run = await runScreener(params)
    return await getScreenerRun(run.run_id)
  }

  const applyScreenerRunDetail = useCallback((
    detail: ScreenerRunDetail,
    values: FormValues,
    options?: {
      executedStep?: 0 | 1 | 2 | 3 | 4
      preserveManualRows?: boolean
      syncSharedRunId?: boolean
    },
  ) => {
    const sourceInputPool = detail.step_pools?.input ?? detail.results
    const stepPools = detail.step_pools
    const currentStepConfigs = stepConfigsRef.current
    const filteredInputPool = filterRowsByBoards(sourceInputPool, values.board_filters)
    const filteredStep1Pool = filterRowsByBoards(stepPools.step1 ?? [], values.board_filters)
    const filteredStep2Pool = filterRowsByBoards(stepPools.step2 ?? [], values.board_filters)
    const filteredStep3Pool = filterRowsByBoards(stepPools.step3 ?? [], values.board_filters)
    const filteredStep4Pool = filterRowsByBoards(stepPools.step4 ?? detail.results ?? [], values.board_filters)
    const resolvedExecutedStep =
      options?.executedStep
      ?? (stepPools.step4?.length
        ? 4
        : stepPools.step3?.length
          ? 3
          : stepPools.step2?.length
            ? 2
            : stepPools.step1?.length
              ? 1
              : 0)

    const appliedPools: ScreenerStepPools = {
      input: filteredInputPool,
      step1: resolvedExecutedStep >= 1 ? filteredStep1Pool : [],
      step2: resolvedExecutedStep >= 2 ? filteredStep2Pool : [],
      step3: resolvedExecutedStep >= 3 ? filteredStep3Pool : [],
      step4: resolvedExecutedStep >= 4 ? filteredStep4Pool : [],
      final: [],
      watch: [],
    }
    setRawInputPool(sourceInputPool)
    setInputPoolDay(getLocalDayKey())
    setPools((prev) => {
      const mergeRows = (calculatedRows: ScreenerResult[], currentRows: ScreenerResult[]) =>
        options?.preserveManualRows ? mergeManualRows(calculatedRows, currentRows) : calculatedRows
      const nextPools: ScreenerStepPools = {
        input: filteredInputPool,
        step1: resolvedExecutedStep >= 1 ? mergeRows(filteredStep1Pool, prev.step1) : [],
        step2: resolvedExecutedStep >= 2 ? mergeRows(filteredStep2Pool, prev.step2) : [],
        step3: resolvedExecutedStep >= 3 ? mergeRows(filteredStep3Pool, prev.step3) : [],
        step4: resolvedExecutedStep >= 4 ? mergeRows(filteredStep4Pool, prev.step4) : [],
        final: prev.final,
        watch: prev.watch,
      }
      return nextPools
    })

    setExecutedStep(resolvedExecutedStep)
    setActivePool(resolvedExecutedStep === 0 ? 'input' : (`step${resolvedExecutedStep}` as ScreenerPoolKey))
    setPendingAutoRecomputeStep(null)
    setRunMeta({
      runId: detail.run_id,
      degraded: detail.degraded,
      degradedReason: detail.degraded_reason,
      asOfDate: detail.as_of_date ?? undefined,
    })
    setValue('top_n', currentStepConfigs.step1.top_n, { shouldDirty: false, shouldValidate: false })
    setValue('turnover_threshold', currentStepConfigs.step1.turnover_threshold, { shouldDirty: false, shouldValidate: false })
    setValue('amount_threshold', currentStepConfigs.step1.amount_threshold, { shouldDirty: false, shouldValidate: false })
    setValue('amplitude_threshold', currentStepConfigs.step1.amplitude_threshold, { shouldDirty: false, shouldValidate: false })

    const runWindowDays =
      typeof detail.params?.return_window_days === 'number' && Number.isFinite(detail.params.return_window_days)
        ? detail.params.return_window_days
        : values.return_window_days
    const runAsOfDate =
      typeof detail.params?.as_of_date === 'string'
        ? detail.params.as_of_date
        : (detail.as_of_date ?? values.as_of_date)
    setInputPoolKey(
      buildInputPoolKey({
        return_window_days: runWindowDays,
        as_of_date: runAsOfDate,
      }),
    )

    if (options?.syncSharedRunId) {
      syncSharedTrendRunCaches(detail.run_id, values.board_filters)
    }

    return {
      sourceInputCount: sourceInputPool.length,
      filteredInputCount: filteredInputPool.length,
      appliedPools,
      executedStep: resolvedExecutedStep,
    }
  }, [setValue])

  const loadInputMutation = useMutation({
    mutationFn: createScreenerRunDetail,
    onSuccess: (detail, values) => {
      const applied = applyScreenerRunDetail(detail, values, { executedStep: 0, syncSharedRunId: true })
      message.success(`已加载输入池（当前板块 ${applied.filteredInputCount} / 全量 ${applied.sourceInputCount}）`)
    },
    onError: (error) => {
      message.error(formatApiErrorMessage(error))
    },
  })

  const rows = useMemo(() => pools[activePool] ?? [], [activePool, pools])
  const allRowsBySymbol = useMemo(() => {
    const map = new Map<string, ScreenerResult>()
    for (const key of poolOrder) {
      for (const row of pools[key] ?? []) {
        if (!map.has(row.symbol)) {
          map.set(row.symbol, row)
        }
      }
    }
    return map
  }, [pools])
  const quickSearchRows = useMemo(() => {
    const keyword = quickSearchKeyword.trim().toLowerCase()
    if (!keyword) return [] as ScreenerResult[]
    return Array.from(allRowsBySymbol.values())
      .filter((row) =>
        row.symbol.toLowerCase().includes(keyword) || row.name.toLowerCase().includes(keyword),
      )
      .slice(0, 12)
  }, [allRowsBySymbol, quickSearchKeyword])

  const filteredRows = useMemo(() => {
    const keyword = keywordFilter.trim().toLowerCase()
    const list = rows.filter((row) => {
      if (
        keyword.length > 0 &&
        !row.symbol.toLowerCase().includes(keyword) &&
        !row.name.toLowerCase().includes(keyword)
      ) {
        return false
      }
      if (trendFilters.length > 0 && !trendFilters.includes(row.trend_class)) {
        return false
      }
      if (stageFilters.length > 0 && !stageFilters.includes(row.stage as StageType)) {
        return false
      }
      if (pathStateFilters.length > 0 && !pathStateFilters.includes(getScreenerPathState(row))) {
        return false
      }
      if (themeStageFilters.length > 0 && !themeStageFilters.includes(row.theme_stage)) {
        return false
      }
      return true
    })

    if (sortField === 'manual') {
      return list
    }

    const sorted = [...list].sort((a, b) => {
      const va = a[sortField]
      const vb = b[sortField]
      if (typeof va === 'number' && typeof vb === 'number') {
        return va - vb
      }
      return String(va ?? '').localeCompare(String(vb ?? ''), 'zh-CN')
    })
    return sortDirection === 'asc' ? sorted : sorted.reverse()
  }, [keywordFilter, pathStateFilters, rows, sortDirection, sortField, stageFilters, themeStageFilters, trendFilters])

  useEffect(() => {
    setTablePage(1)
  }, [activePool, keywordFilter, trendFilters, stageFilters, pathStateFilters, themeStageFilters, sortField, sortDirection])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(filteredRows.length / tablePageSize))
    if (tablePage > maxPage) {
      setTablePage(maxPage)
    }
  }, [filteredRows.length, tablePage, tablePageSize])

  const summary = useMemo(
    () => ({
      input_count: pools.input.length,
      step1_count: pools.step1.length,
      step2_count: pools.step2.length,
      step3_count: pools.step3.length,
      step4_count: pools.step4.length,
      final_count: pools.final.length,
      watch_count: pools.watch.length,
    }),
    [pools],
  )
  const hasAnyPoolData = useMemo(
    () => poolOrder.some((key) => (pools[key]?.length ?? 0) > 0),
    [pools],
  )

  useEffect(() => {
    if (hasAnyPoolData) return
    if (!runMeta?.runId) return
    if (recoveredFromRunRef.current) return

    recoveredFromRunRef.current = true
    let cancelled = false

    ;(async () => {
      try {
        const detail = await getScreenerRun(runMeta.runId)
        if (cancelled) return
        const values = getValues()
        applyScreenerRunDetail(detail, values)
        message.info('已恢复上次筛选数据')
      } catch (error) {
        if (cancelled) return
        if (runMeta.runId && isApiNotFoundError(error)) {
          clearScreenerRunIdFromCaches(runMeta.runId)
        }
        setRunMeta(undefined)
        message.warning('上次筛选任务已失效（后端无该 run），请重新加载输入池/运行筛选。')
      }
    })()

    return () => {
      cancelled = true
    }
  }, [applyScreenerRunDetail, getValues, hasAnyPoolData, message, runMeta?.runId])

  const nextPoolKey = useMemo<ScreenerPoolKey | null>(() => {
    const currentIndex = poolOrder.indexOf(activePool)
    if (currentIndex < 0 || currentIndex >= poolOrder.length - 1) return null
    return poolOrder[currentIndex + 1]
  }, [activePool])
  const inputPoolOutdated = pools.input.length > 0 && inputPoolKey !== '' && inputPoolKey !== currentInputPoolKey
  const hasTableFilters = keywordFilter.trim().length > 0
    || trendFilters.length > 0
    || stageFilters.length > 0
    || pathStateFilters.length > 0
    || themeStageFilters.length > 0
  const canReorderInTable = sortField === 'manual' && !hasTableFilters && rows.length > 1
  const canDragRows = Boolean(nextPoolKey) || canReorderInTable

  function toggleColumnVisibility(key: ScreenerColumnKey, visible: boolean) {
    setColumnVisible((prev) => {
      const visibleCount = Object.values(prev).filter(Boolean).length
      if (!visible && prev[key] && visibleCount <= 1) {
        message.info('至少保留一列显示')
        return prev
      }
      return { ...prev, [key]: visible }
    })
  }

  function moveColumn(key: ScreenerColumnKey, direction: 'up' | 'down') {
    setColumnOrder((prev) => {
      const index = prev.indexOf(key)
      if (index < 0) return prev
      const targetIndex = direction === 'up' ? index - 1 : index + 1
      if (targetIndex < 0 || targetIndex >= prev.length) return prev
      const next = [...prev]
      const temp = next[targetIndex]
      next[targetIndex] = next[index]
      next[index] = temp
      return next
    })
  }

  function reorderColumnByDrag(sourceKey: ScreenerColumnKey, targetKey: ScreenerColumnKey) {
    if (sourceKey === targetKey) return
    setColumnOrder((prev) => {
      const fromIndex = prev.indexOf(sourceKey)
      const toIndex = prev.indexOf(targetKey)
      if (fromIndex < 0 || toIndex < 0) return prev
      const next = [...prev]
      const [moved] = next.splice(fromIndex, 1)
      next.splice(toIndex, 0, moved)
      return next
    })
  }

  function resetColumns() {
    setColumnOrder(defaultColumnOrder)
    setColumnVisible(
      defaultColumnOrder.reduce(
        (acc, key) => ({ ...acc, [key]: true }),
        {} as Record<ScreenerColumnKey, boolean>,
      ),
    )
  }

  const addRowToPoolManually = useCallback((targetPool: ScreenerPoolKey, row: ScreenerResult) => {
    let inserted = false
    setPools((prev) => {
      const exists = prev[targetPool].some((item) => item.symbol === row.symbol)
      if (exists) return prev

      inserted = true
      const manualRow: ScreenerResult = {
        ...row,
        labels: Array.from(new Set([...(row.labels ?? []), MANUAL_LABEL])),
      }

      if (targetPool === 'step1') {
        return {
          ...prev,
          step1: [manualRow, ...prev.step1],
          step2: [],
          step3: [],
          step4: [],
          final: prev.final,
        }
      }
      if (targetPool === 'step2') {
        return {
          ...prev,
          step2: [manualRow, ...prev.step2],
          step3: [],
          step4: [],
          final: prev.final,
        }
      }
      if (targetPool === 'step3') {
        return {
          ...prev,
          step3: [manualRow, ...prev.step3],
          step4: [],
          final: prev.final,
        }
      }
      if (targetPool === 'final') {
        return { ...prev, final: [manualRow, ...prev.final] }
      }
      if (targetPool === 'watch') {
        return { ...prev, watch: [manualRow, ...prev.watch] }
      }
      return { ...prev, step4: [manualRow, ...prev.step4] }
    })

    if (!inserted) {
      message.info(`${row.symbol} 已在${poolLabelMap[targetPool]}中`)
      return
    }

    setExecutedStep((prev) =>
      targetPool === 'final' || targetPool === 'watch' ? prev : Math.max(prev, poolStepIndex[targetPool]),
    )
    setActivePool(targetPool)
    message.success(`${row.symbol} 已手工加入${poolLabelMap[targetPool]}`)
  }, [message])

  function reorderRowsInActivePool(sourceSymbol: string, targetSymbol: string) {
    if (sourceSymbol === targetSymbol) {
      return
    }
    setPools((prev) => {
      const sourceRows = prev[activePool]
      const fromIndex = sourceRows.findIndex((item) => item.symbol === sourceSymbol)
      const toIndex = sourceRows.findIndex((item) => item.symbol === targetSymbol)
      if (fromIndex < 0 || toIndex < 0) {
        return prev
      }
      const nextRows = [...sourceRows]
      const [moved] = nextRows.splice(fromIndex, 1)
      nextRows.splice(toIndex, 0, moved)
      return {
        ...prev,
        [activePool]: nextRows,
      } as ScreenerStepPools
    })
  }

  function handleDropToPool(targetPool: ScreenerPoolKey, symbolFromDrop?: string) {
    const canDropToNext = nextPoolKey !== null && targetPool === nextPoolKey
    const canDropToFinal = targetPool === 'final' && activePool !== 'final'
    const canDropToManualPool = targetPool !== 'input' && targetPool !== activePool
    if (!canDropToNext && !canDropToFinal && !canDropToManualPool) {
      message.info('仅支持拖动到其他池（下一池、终选池或手工目标池）')
      return
    }

    const symbol = symbolFromDrop ?? draggingSymbol
    if (!symbol) return
    const row = allRowsBySymbol.get(symbol) ?? pools[activePool].find((item) => item.symbol === symbol)
    if (!row) {
      message.warning('当前股票池未找到该股票')
      return
    }
    addRowToPoolManually(targetPool, row)
  }

  const visibleDataColumns = useMemo<ColumnsType<ScreenerResult>>(() => {
    return columnOrder
      .filter((key) => columnVisible[key])
      .map((key) => {
        const column: ColumnsType<ScreenerResult>[number] = {
          ...tableColumnMap[key],
          title: columnTitleMap[key],
        }

        if (sortableFields.includes(key as TableSortField)) {
          column.sorter = true
          column.sortOrder = sortField === key
            ? (sortDirection === 'asc' ? 'ascend' : 'descend')
            : null
          column.sortDirections = ['descend', 'ascend']
          column.showSorterTooltip = { title: '点击切换升降序' }
        }

        if (key === 'trend_class') {
          column.filters = (Object.keys(trendClassLabelMap) as TrendClass[]).map((value) => ({
            text: trendClassLabelMap[value],
            value,
          }))
          column.filteredValue = trendFilters.length > 0 ? trendFilters : null
        }

        if (key === 'stage') {
          column.filters = (Object.keys(stageLabelMap) as StageType[]).map((value) => ({
            text: stageLabelMap[value],
            value,
          }))
          column.filteredValue = stageFilters.length > 0 ? stageFilters : null
        }

        if (key === 'path_state') {
          const options: ScreenerPathState[] = ['上涨路径中', '回踩蓄势', '高位回撤', '整理观察']
          column.filters = options.map((value) => ({ text: value, value }))
          column.filteredValue = pathStateFilters.length > 0 ? pathStateFilters : null
        }

        if (key === 'theme_stage') {
          const options: ThemeStage[] = ['发酵中', '高潮', '退潮', 'Unknown']
          column.filters = options.map((value) => ({ text: value, value }))
          column.filteredValue = themeStageFilters.length > 0 ? themeStageFilters : null
        }

        if (key === 'symbol') {
          column.filterDropdown = ({ confirm, clearFilters }) => (
            <Space orientation="vertical" size={8} className="screener-config-220">
              <Typography.Text type="secondary" className="screener-text-12">
                输入代码或名称关键词
              </Typography.Text>
              <Input
                autoFocus
                allowClear
                value={keywordFilter}
                onChange={(event) => setKeywordFilter(event.target.value)}
                onPressEnter={() => confirm()}
                placeholder="例如: 600519 / 茅台"
              />
              <Space>
                <Button size="small" type="primary" onClick={() => confirm()}>
                  应用
                </Button>
                <Button
                  size="small"
                  onClick={() => {
                    setKeywordFilter('')
                    clearFilters?.()
                    confirm()
                  }}
                >
                  重置
                </Button>
              </Space>
            </Space>
          )
          column.filteredValue = keywordFilter.trim().length > 0 ? [keywordFilter] : null
        }

        return column
      })
  }, [
    columnOrder,
    columnTitleMap,
    columnVisible,
    keywordFilter,
    pathStateFilters,
    sortDirection,
    sortField,
    stageFilters,
    themeStageFilters,
    trendFilters,
  ])

  function formatExportValue(row: ScreenerResult, key: ScreenerColumnKey) {
    switch (key) {
      case 'latest_price':
      case 'day_change':
        return typeof row[key] === 'number' ? (row[key] as number).toFixed(2) : '--'
      case 'ret40':
      case 'turnover20':
      case 'amplitude20':
      case 'retrace20':
      case 'ai_confidence':
      case 'day_change_pct':
        return typeof row[key] === 'number' ? formatPct(row[key] as number) : '--'
      case 'amount20':
        return formatLargeMoney(row.amount20)
      case 'path_state':
        return getScreenerPathState(row)
      default:
        return String(row[key] ?? '')
    }
  }

  function buildExportDataset(poolKey: ScreenerPoolKey) {
    const rowsForExport = pools[poolKey]
    if (rowsForExport.length === 0) {
      return null
    }
    const baseKeys: ScreenerColumnKey[] = ['symbol', 'name']
    const visibleKeys = columnOrder.filter((key) => columnVisible[key])
    const exportKeys = [
      ...baseKeys,
      ...visibleKeys.filter((key) => !baseKeys.includes(key)),
    ]
    const header = exportKeys.map((key) => columnTitleMap[key])
    const lines = rowsForExport.map((row) =>
      exportKeys.map((key) => formatExportValue(row, key)),
    )
    return {
      header,
      lines,
      count: rowsForExport.length,
    }
  }

  function downloadByBlob(content: BlobPart, type: string, fileName: string) {
    const blob = new Blob([content], { type })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  async function exportPool(poolKey: ScreenerPoolKey, format: ExportFormat) {
    const dataset = buildExportDataset(poolKey)
    if (!dataset) {
      message.info(`${poolLabelMap[poolKey]}暂无可导出数据`)
      return
    }

    const { header, lines, count } = dataset
    const timestamp = formatTimestampForFile()
    const baseFileName = `stock-pool-${poolKey}-${timestamp}`

    if (format === 'csv') {
      const csv = `\uFEFF${[header, ...lines]
        .map((line) => line.map((cell) => csvEscape(cell)).join(','))
        .join('\n')}`
      downloadByBlob(csv, 'text/csv;charset=utf-8;', `${baseFileName}.csv`)
    } else if (format === 'xlsx') {
      const worksheet = XLSX.utils.aoa_to_sheet([header, ...lines])
      const workbook = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(workbook, worksheet, poolLabelMap[poolKey])
      XLSX.writeFile(workbook, `${baseFileName}.xlsx`)
    } else {
      const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' })
      const hasCjkFont = await applyPdfCjkFont(doc)
      if (!hasCjkFont) {
        message.warning('PDF中文字体加载失败，中文可能显示为乱码')
      }
      doc.setFontSize(12)
      doc.text(`${poolLabelMap[poolKey]} 导出`, 40, 30)
      autoTable(doc, {
        startY: 40,
        head: [header],
        body: lines,
        styles: {
          font: hasCjkFont ? PDF_CJK_FONT_NAME : 'helvetica',
          fontSize: 8,
          cellPadding: 3,
        },
        headStyles: {
          font: hasCjkFont ? PDF_CJK_FONT_NAME : 'helvetica',
          fillColor: [15, 139, 111],
        },
        margin: { left: 20, right: 20 },
      })
      doc.save(`${baseFileName}.pdf`)
    }

    message.success(`已导出${poolLabelMap[poolKey]}（${count}）- ${exportFormatLabelMap[format]}`)
  }

  function buildExportMenu(poolKey: ScreenerPoolKey): MenuProps {
    return {
      items: [
        { key: 'csv', label: '导出 CSV' },
        { key: 'xlsx', label: '导出 Excel' },
        { key: 'pdf', label: '导出 PDF' },
      ],
      onClick: (info) => {
        info.domEvent.stopPropagation()
        const key = String(info.key)
        if (key === 'csv' || key === 'xlsx' || key === 'pdf') {
          void exportPool(poolKey, key as ExportFormat)
        }
      },
    }
  }

  async function ensureFormValid() {
    const valid = await trigger()
    if (!valid) {
      message.error('请先修正筛选参数')
      return false
    }
    return true
  }

  async function ensureInputPoolLoaded() {
    const marketDataFresh = inputPoolDay === getLocalDayKey()
    if (rawInputPool.length > 0 && inputPoolKey === currentInputPoolKey && marketDataFresh) {
      const boardFilters = getValues('board_filters')
      const inputPool = filterRowsByBoards(rawInputPool, boardFilters)
      setPools((prev) => ({ ...prev, input: inputPool }))
      return inputPool
    }
    const ok = await ensureFormValid()
    if (!ok) return null
    try {
      const detail = await loadInputMutation.mutateAsync(getValues())
      const sourceInputPool = detail.step_pools?.input ?? detail.results
      return filterRowsByBoards(sourceInputPool, getValues('board_filters'))
    } catch {
      return null
    }
  }

  function invalidateFrom(step: 1 | 2 | 3 | 4) {
    const canAutoRecompute = pools.input.length > 0 && executedStep >= step
    if (canAutoRecompute) {
      setPendingAutoRecomputeStep((previous) => {
        if (previous === null) return step
        return Math.min(previous, step) as 1 | 2 | 3 | 4
      })
      return
    }
    setPools((prev) => {
      if (step === 1) return { ...prev, step1: [], step2: [], step3: [], step4: [] }
      if (step === 2) return { ...prev, step2: [], step3: [], step4: [] }
      if (step === 3) return { ...prev, step3: [], step4: [] }
      return { ...prev, step4: [] }
    })
    setExecutedStep((prev) => Math.min(prev, step - 1))
    setActivePool((prev) => (poolStepIndex[prev] >= step ? previousPoolByStep[step] : prev))
  }

  useEffect(() => {
    if (pendingAutoRecomputeStep === null) return
    if (!(pools.input.length > 0 && executedStep >= pendingAutoRecomputeStep)) {
      setPendingAutoRecomputeStep(null)
      return
    }
    setPools((prev) => {
      const next: ScreenerStepPools = { ...prev }
      if (pendingAutoRecomputeStep <= 1) {
        next.step1 = mergeManualRows(runStep1(prev.input, stepConfigs.step1), prev.step1)
      }
      if (pendingAutoRecomputeStep <= 2) {
        next.step2 = mergeManualRows(runStep2(next.step1, stepConfigs.step2, mode), prev.step2)
      }
      if (pendingAutoRecomputeStep <= 3) {
        next.step3 = mergeManualRows(runStep3(next.step2, stepConfigs.step3), prev.step3)
      }
      if (pendingAutoRecomputeStep <= 4) {
        next.step4 = mergeManualRows(runStep4(next.step3, stepConfigs.step4), prev.step4)
      }
      return next
    })
    setPendingAutoRecomputeStep(null)
  }, [executedStep, mode, pendingAutoRecomputeStep, pools.input.length, stepConfigs])

  // Auto-regenerate watch pool when input pool or config changes
  useEffect(() => {
    if (!watchConfig.enabled || pools.input.length === 0) return

    if (watchConfig.source_mode === 'strategy' && watchConfig.strategy_id) {
      // Strategy mode: call backend B1 API, then cross-reference with input pool for full data
      setB1Loading(true)
      runB1Screener({
        markets: ['sh', 'sz'],
        b1_params: undefined,
      })
        .then((res) => {
          // Build lookup from input pool for rich field data (ret40, turnover20, etc.)
          const inputBySymbol = new Map<string, ScreenerResult>()
          for (const r of rawInputPool) inputBySymbol.set(r.symbol, r)
          for (const r of pools.input) {
            if (!inputBySymbol.has(r.symbol)) inputBySymbol.set(r.symbol, r)
          }
          const emptyBase = (h: typeof res.hits[0]): ScreenerResult => {
            // Normalize symbol to include market prefix for chart navigation
            const code = h.symbol
            const prefixed = code.startsWith('sh') || code.startsWith('sz') ? code
              : code.startsWith('6') ? `sh${code}`
              : `sz${code}`
            return {
              symbol: prefixed,
              name: h.name,
              latest_price: h.close,
              day_change: +(h.close * h.change_pct / 100).toFixed(2),
              day_change_pct: +(h.change_pct / 100).toFixed(4),
              score: 0,
              ret40: 0,
              turnover20: 0,
              amount20: 0,
              amplitude20: 0,
              retrace20: 0,
              pullback_days: 0,
              ma10_above_ma20_days: 0,
              ma5_above_ma10_days: 0,
              price_vs_ma20: 0,
              vol_slope20: 0,
              up_down_volume_ratio: 0,
              pullback_volume_ratio: 0,
              has_blowoff_top: false,
              has_divergence_5d: false,
              has_upper_shadow_risk: false,
              ai_confidence: 0,
              theme_stage: 'Unknown' as ThemeStage,
              trend_class: 'Unknown' as TrendClass,
              stage: 'Early' as const,
              labels: [],
              reject_reasons: [],
              degraded: false,
            }
          }
          const mapped: ScreenerResult[] = res.hits.map((h) => {
            // B1 returns 6-digit codes (e.g. "600054"); input pool uses "sh600054"
            const existing = inputBySymbol.get(h.symbol)
              ?? inputBySymbol.get(`sh${h.symbol}`)
              ?? inputBySymbol.get(`sz${h.symbol}`)
            const base = existing ? { ...existing } : emptyBase(h)
            return {
              ...base,
              latest_price: h.close,
              name: h.name || base.name,
              day_change: +(h.close * h.change_pct / 100).toFixed(2),
              day_change_pct: +(h.change_pct / 100).toFixed(4),
              amplitude20: +(h.amplitude_pct / 100).toFixed(4),
              up_down_volume_ratio: h.volume_ratio,
              labels: [...(base.labels ?? []), `B1 J=${h.kdj_j}`],
            }
          })
          setPools((prev) => ({
            ...prev,
            watch: mergeManualRows(mapped.slice(0, watchConfig.top_n), prev.watch),
          }))
          message.success(`B1 策略筛选完成，共 ${Math.min(res.hits.length, watchConfig.top_n)} 只`)
        })
        .catch(() => {
          message.error('B1 策略执行失败')
        })
        .finally(() => {
          setB1Loading(false)
        })
      return
    }

    // Filter mode: client-side filtering
    setPools((prev) => ({
      ...prev,
      watch: mergeManualRows(runWatchPool(prev.input, watchConfig), prev.watch),
    }))
  }, [pools.input, watchConfig])

  async function refreshBackendSnapshotForExecution(step: 1 | 4) {
    const ok = await ensureFormValid()
    if (!ok) return null
    const values = getValues()
    const detail = await createScreenerRunDetail(values)
    return applyScreenerRunDetail(detail, values, {
      executedStep: step,
      preserveManualRows: true,
      syncSharedRunId: true,
    })
  }

  async function executeStep(step: 1 | 2 | 3 | 4) {
    if (step > executedStep + 1) {
      message.info(`请先运行第${executedStep + 1}步`)
      return
    }
    setRunningStep(step)
    try {
      if (step === 1) {
        const applied = await refreshBackendSnapshotForExecution(1)
        if (!applied) return
        message.success(`第1步筛选完成，规则命中 ${applied.appliedPools.step1.length} 只股票`)
        return
      }
      const inputPool = await ensureInputPoolLoaded()
      if (!inputPool) return
      setPools((prev) => {
        if (step === 2) {
          const step2 = mergeManualRows(runStep2(prev.step1, stepConfigs.step2, mode), prev.step2)
          return { ...prev, step2, step3: [], step4: [] }
        }
        if (step === 3) {
          const step3 = mergeManualRows(runStep3(prev.step2, stepConfigs.step3), prev.step3)
          return { ...prev, step3, step4: [] }
        }
        const step4 = mergeManualRows(runStep4(prev.step3, stepConfigs.step4), prev.step4)
        return { ...prev, step4 }
      })
      setExecutedStep(step)
      setActivePool(`step${step}` as ScreenerPoolKey)
      message.success(`第${step}步筛选完成，结果已更新`)
    } finally {
      setRunningStep(null)
    }
  }

  async function executeAll() {
    setRunningStep(4)
    try {
      const applied = await refreshBackendSnapshotForExecution(4)
      if (!applied) return
      message.success(`四步筛选完成，规则命中 ${applied.appliedPools.step4.length} 只股票`)
    } finally {
      setRunningStep(null)
    }
  }

  function onClickPool(poolKey: ScreenerPoolKey) {
    const requiredStep = poolStepIndex[poolKey]
    if (requiredStep > executedStep) {
      message.info(`请先运行到第${requiredStep}步`)
      return
    }
    setActivePool(poolKey)
  }

  const step1ConfigContent = (
    <Space orientation="vertical" size={8} className="screener-config-320">
      <Typography.Text strong>第1步配置</Typography.Text>
      <Typography.Text type="secondary" className="screener-text-12">
        规则：先按 {returnWindowDays} 日涨幅排序截取指定排名区间，再按活跃度过滤。
      </Typography.Text>
      <Typography.Text type="secondary" className="screener-text-12">
        固定边界：区间过滤后结果&gt;{SCREENER_STEP1_POOL_CAP} 截断为 {SCREENER_STEP1_POOL_CAP}；结果&lt;{SCREENER_STEP1_POOL_MIN_WARN} 保留全部并告警。
      </Typography.Text>
      <LabeledNumberInput
        label="涨幅窗口(天)"
        min={5}
        max={120}
        value={returnWindowDays}
        onChange={(value) => {
          const nextValue = Math.max(5, Math.min(120, Math.round(value ?? returnWindowDays)))
          setValue('return_window_days', nextValue, {
            shouldValidate: true,
            shouldDirty: true,
          })
          invalidateFrom(1)
        }}
        className="screener-full-width"
      />
      <LabeledNumberInput
        label="涨幅排名起始"
        min={1}
        max={2000}
        step={50}
        value={stepConfigs.step1.rank_start}
        onChange={(value) => {
          const nextRange = normalizeScreenerStep1RankRange({
            rank_start: value ?? stepConfigs.step1.rank_start,
            top_n: stepConfigs.step1.top_n,
          })
          setStepConfigs((prev) => ({
            ...prev,
            step1: { ...prev.step1, ...nextRange },
          }))
          setValue('top_n', nextRange.top_n, {
            shouldValidate: true,
            shouldDirty: true,
          })
          invalidateFrom(1)
        }}
        className="screener-full-width"
      />
      <LabeledNumberInput
        label="涨幅排名截止"
        min={SCREENER_STEP1_RANK_END_MIN}
        max={2000}
        step={50}
        value={stepConfigs.step1.top_n}
        onChange={(value) => {
          const nextRange = normalizeScreenerStep1RankRange({
            rank_start: stepConfigs.step1.rank_start,
            top_n: value ?? stepConfigs.step1.top_n,
          })
          setStepConfigs((prev) => ({
            ...prev,
            step1: { ...prev.step1, ...nextRange },
          }))
          setValue('top_n', nextRange.top_n, {
            shouldValidate: true,
            shouldDirty: true,
          })
          invalidateFrom(1)
        }}
        className="screener-full-width"
      />
      <LabeledNumberInput
        label="20日换手下限(%)"
        min={1}
        max={20}
        step={0.5}
        value={Number((stepConfigs.step1.turnover_threshold * 100).toFixed(2))}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step1: {
              ...prev.step1,
              turnover_threshold:
                ((value ?? prev.step1.turnover_threshold * 100) as number) / 100,
            },
          }))
          invalidateFrom(1)
        }}
        className="screener-full-width"
      />
      <LabeledNumberInput
        label="20日成交额下限(亿)"
        min={0.5}
        max={50}
        step={0.1}
        value={Number((stepConfigs.step1.amount_threshold / 1e8).toFixed(2))}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step1: {
              ...prev.step1,
              amount_threshold:
                ((value ?? prev.step1.amount_threshold / 1e8) as number) * 1e8,
            },
          }))
          invalidateFrom(1)
        }}
        className="screener-full-width"
      />
      <LabeledNumberInput
        label="20日振幅下限(%)"
        min={1}
        max={15}
        step={0.5}
        value={Number((stepConfigs.step1.amplitude_threshold * 100).toFixed(2))}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step1: {
              ...prev.step1,
              amplitude_threshold:
                ((value ?? prev.step1.amplitude_threshold * 100) as number) / 100,
            },
          }))
          invalidateFrom(1)
        }}
        className="screener-full-width"
      />
    </Space>
  )

  const step2ConfigContent = (
    <Space orientation="vertical" size={8} className="screener-config-290">
      <Typography.Text strong>第2步配置</Typography.Text>
      <Typography.Text type="secondary" className="screener-text-12">
        当前模式：{mode === 'strict' ? '严格（容差更小）' : '宽松（容差更大）'}
      </Typography.Text>
      <LabeledNumberInput
        label="回撤下限"
        min={0}
        max={0.4}
        step={0.01}
        value={stepConfigs.step2.retrace_min}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: {
              ...prev.step2,
              retrace_min: value ?? prev.step2.retrace_min,
            },
          }))
          invalidateFrom(2)
        }}
      />
      <LabeledNumberInput
        label="回撤上限"
        min={0.05}
        max={0.5}
        step={0.01}
        value={stepConfigs.step2.retrace_max}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: {
              ...prev.step2,
              retrace_max: value ?? prev.step2.retrace_max,
            },
          }))
          invalidateFrom(2)
        }}
      />
      <LabeledNumberInput
        label="回调天数"
        min={1}
        max={10}
        value={stepConfigs.step2.max_pullback_days}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: {
              ...prev.step2,
              max_pullback_days: value ?? prev.step2.max_pullback_days,
            },
          }))
          invalidateFrom(2)
        }}
      />
      <LabeledNumberInput
        label="MA10>MA20天数"
        min={0}
        max={20}
        value={stepConfigs.step2.min_ma10_above_ma20_days}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: {
              ...prev.step2,
              min_ma10_above_ma20_days: value ?? prev.step2.min_ma10_above_ma20_days,
            },
          }))
          invalidateFrom(2)
        }}
      />
      <LabeledNumberInput
        label="MA5>MA10天数"
        min={0}
        max={20}
        value={stepConfigs.step2.min_ma5_above_ma10_days}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: {
              ...prev.step2,
              min_ma5_above_ma10_days: value ?? prev.step2.min_ma5_above_ma10_days,
            },
          }))
          invalidateFrom(2)
        }}
      />
      <LabeledNumberInput
        label="距MA20容差"
        min={0.01}
        max={0.2}
        step={0.01}
        value={stepConfigs.step2.max_price_vs_ma20}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: {
              ...prev.step2,
              max_price_vs_ma20: value ?? prev.step2.max_price_vs_ma20,
            },
          }))
          invalidateFrom(2)
        }}
      />
      <Checkbox
        checked={stepConfigs.step2.require_above_ma20}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: { ...prev.step2, require_above_ma20: event.target.checked },
          }))
          invalidateFrom(2)
        }}
      >
        必须站上 MA20
      </Checkbox>
      <Checkbox
        checked={stepConfigs.step2.allow_b_trend}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step2: { ...prev.step2, allow_b_trend: event.target.checked },
          }))
          invalidateFrom(2)
        }}
      >
        允许 B 类趋势
      </Checkbox>
    </Space>
  )

  const step3ConfigContent = (
    <Space orientation="vertical" size={8} className="screener-config-290">
      <Typography.Text strong>第3步配置</Typography.Text>
      <LabeledNumberInput
        label="量能斜率下限"
        min={-1}
        max={2}
        step={0.05}
        value={stepConfigs.step3.min_vol_slope20}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: { ...prev.step3, min_vol_slope20: value ?? prev.step3.min_vol_slope20 },
          }))
          invalidateFrom(3)
        }}
      />
      <LabeledNumberInput
        label="涨跌量比下限"
        min={0.8}
        max={3}
        step={0.05}
        value={stepConfigs.step3.min_up_down_volume_ratio}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: {
              ...prev.step3,
              min_up_down_volume_ratio:
                value ?? prev.step3.min_up_down_volume_ratio,
            },
          }))
          invalidateFrom(3)
        }}
      />
      <LabeledNumberInput
        label="回调量比上限"
        min={0.4}
        max={1.2}
        step={0.05}
        value={stepConfigs.step3.max_pullback_volume_ratio}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: {
              ...prev.step3,
              max_pullback_volume_ratio:
                value ?? prev.step3.max_pullback_volume_ratio,
            },
          }))
          invalidateFrom(3)
        }}
      />
      <Checkbox
        checked={stepConfigs.step3.allow_blowoff_top}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: { ...prev.step3, allow_blowoff_top: event.target.checked },
          }))
          invalidateFrom(3)
        }}
      >
        允许天量天价风险
      </Checkbox>
      <Checkbox
        checked={stepConfigs.step3.allow_divergence_5d}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: { ...prev.step3, allow_divergence_5d: event.target.checked },
          }))
          invalidateFrom(3)
        }}
      >
        允许5日量价背离
      </Checkbox>
      <Checkbox
        checked={stepConfigs.step3.allow_upper_shadow_risk}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: { ...prev.step3, allow_upper_shadow_risk: event.target.checked },
          }))
          invalidateFrom(3)
        }}
      >
        允许长上影风险
      </Checkbox>
      <Checkbox
        checked={stepConfigs.step3.allow_degraded}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step3: { ...prev.step3, allow_degraded: event.target.checked },
          }))
          invalidateFrom(3)
        }}
      >
        包含降级数据
      </Checkbox>
    </Space>
  )

  const step4ConfigContent = (
    <Space orientation="vertical" size={8} className="screener-config-290">
      <Typography.Text strong>第4步配置</Typography.Text>
      <LabeledNumberInput
        label="最终数量"
        min={1}
        max={50}
        value={stepConfigs.step4.final_top_n}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step4: { ...prev.step4, final_top_n: value ?? prev.step4.final_top_n },
          }))
          invalidateFrom(4)
        }}
      />
      <LabeledNumberInput
        label="AI置信度"
        min={0}
        max={1}
        step={0.01}
        value={stepConfigs.step4.min_ai_confidence}
        onChange={(value) => {
          setStepConfigs((prev) => ({
            ...prev,
            step4: {
              ...prev.step4,
              min_ai_confidence: value ?? prev.step4.min_ai_confidence,
            },
          }))
          invalidateFrom(4)
        }}
      />
      <Checkbox.Group
        value={stepConfigs.step4.allowed_theme_stages}
        onChange={(values) => {
          setStepConfigs((prev) => ({
            ...prev,
            step4: {
              ...prev.step4,
              allowed_theme_stages: values as ThemeStage[],
            },
          }))
          invalidateFrom(4)
        }}
        options={[
          { label: '发酵中', value: '发酵中' },
          { label: '高潮', value: '高潮' },
          { label: '退潮', value: '退潮' },
          { label: 'Unknown', value: 'Unknown' },
        ]}
      />
      <Checkbox
        checked={stepConfigs.step4.allow_degraded}
        onChange={(event) => {
          setStepConfigs((prev) => ({
            ...prev,
            step4: { ...prev.step4, allow_degraded: event.target.checked },
          }))
          invalidateFrom(4)
        }}
      >
        包含降级数据
      </Checkbox>
    </Space>
  )

  const watchConfigContent = (
    <Space orientation="vertical" size={8} className="screener-config-290">
      <Typography.Text strong>观察池配置</Typography.Text>
      <div className="screener-flex-between">
        <Typography.Text>启用</Typography.Text>
        <Switch
          size="small"
          checked={watchConfig.enabled}
          onChange={(checked) => setWatchConfig((prev) => ({ ...prev, enabled: checked }))}
        />
      </div>
      {watchConfig.enabled && (
        <div className="screener-full-width">
          <Typography.Text type="secondary" className="screener-text-12">数据来源</Typography.Text>
          <Radio.Group
            size="small"
            value={watchConfig.source_mode}
            onChange={(e) => setWatchConfig((prev) => ({ ...prev, source_mode: e.target.value }))}
            optionType="button"
            buttonStyle="solid"
            className="screener-full-width"
          >
            <Radio.Button value="filter" className="screener-radio-half">条件筛选</Radio.Button>
            <Radio.Button value="strategy" className="screener-radio-half">策略</Radio.Button>
          </Radio.Group>
        </div>
      )}
      {watchConfig.enabled && watchConfig.source_mode === 'strategy' && (
        <Select
          className="screener-full-width"
          placeholder="选择策略"
          value={watchConfig.strategy_id || undefined}
          onChange={(value) => setWatchConfig((prev) => ({ ...prev, strategy_id: value }))}
          options={[
            { label: 'B1 战法（月MACD+周DIF+日KDJ）', value: 'b1_mtf_v1' },
          ]}
        />
      )}
      {watchConfig.enabled && watchConfig.source_mode === 'filter' && (
        <>
          <LabeledNumberInput
            label="涨幅下限"
            min={0}
            max={5}
            step={0.05}
            value={watchConfig.ret40_min}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, ret40_min: value ?? prev.ret40_min }))}
          />
          <LabeledNumberInput
            label="涨幅上限"
            min={0}
            max={5}
            step={0.05}
            value={watchConfig.ret40_max}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, ret40_max: value ?? prev.ret40_max }))}
          />
          <LabeledNumberInput
            label="换手率下限"
            min={0}
            max={1}
            step={0.01}
            value={watchConfig.turnover20_min}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, turnover20_min: value ?? prev.turnover20_min }))}
          />
          <LabeledNumberInput
            label="成交额下限(亿)"
            min={0}
            max={100}
            step={1}
            value={watchConfig.amount20_min / 1e8}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, amount20_min: (value ?? 0) * 1e8 }))}
          />
          <LabeledNumberInput
            label="振幅下限"
            min={0}
            max={1}
            step={0.01}
            value={watchConfig.amplitude20_min}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, amplitude20_min: value ?? prev.amplitude20_min }))}
          />
          <div className="screener-full-width">
            <Typography.Text type="secondary" className="screener-text-12">趋势类型</Typography.Text>
            <Checkbox.Group
              value={watchConfig.trend_classes}
              onChange={(values) => setWatchConfig((prev) => ({ ...prev, trend_classes: values as TrendClass[] }))}
              options={[
                { label: 'A', value: 'A' },
                { label: 'A_B', value: 'A_B' },
                { label: 'B', value: 'B' },
                { label: 'Unknown', value: 'Unknown' },
              ]}
            />
          </div>
          <LabeledNumberInput
            label="回撤下限"
            min={0}
            max={1}
            step={0.01}
            value={watchConfig.retrace20_min}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, retrace20_min: value ?? prev.retrace20_min }))}
          />
          <LabeledNumberInput
            label="回撤上限"
            min={0}
            max={1}
            step={0.01}
            value={watchConfig.retrace20_max}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, retrace20_max: value ?? prev.retrace20_max }))}
          />
          <LabeledNumberInput
            label="回调天数上限"
            min={0}
            max={30}
            step={1}
            value={watchConfig.pullback_days_max}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, pullback_days_max: value ?? prev.pullback_days_max }))}
          />
          <LabeledNumberInput
            label="量能斜率下限"
            min={0}
            max={2}
            step={0.01}
            value={watchConfig.vol_slope20_min}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, vol_slope20_min: value ?? prev.vol_slope20_min }))}
          />
          <LabeledNumberInput
            label="量比下限"
            min={0}
            max={20}
            step={0.1}
            value={watchConfig.up_down_volume_ratio_min}
            onChange={(value) => setWatchConfig((prev) => ({ ...prev, up_down_volume_ratio_min: value ?? prev.up_down_volume_ratio_min }))}
          />
        </>
      )}
      <LabeledNumberInput
        label="最大数量"
        min={1}
        max={500}
        step={1}
        value={watchConfig.top_n}
        onChange={(value) => setWatchConfig((prev) => ({ ...prev, top_n: value ?? prev.top_n }))}
      />
    </Space>
  )

  const cardValues: Record<ScreenerPoolKey, number | string> = {
    input: summary.input_count,
    step1: executedStep >= 1 ? summary.step1_count : '-',
    step2: executedStep >= 2 ? summary.step2_count : '-',
    step3: executedStep >= 3 ? summary.step3_count : '-',
    step4: executedStep >= 4 ? summary.step4_count : '-',
    final: summary.final_count,
    watch: watchConfig.enabled ? (b1Loading ? '筛选中...' : summary.watch_count) : '关',
  }

  const watchConfigHint = useMemo(() => {
    if (!watchConfig.enabled) return '已关闭'
    const parts = [`涨幅${formatPct(watchConfig.ret40_min)}~${formatPct(watchConfig.ret40_max)}`]
    if (watchConfig.turnover20_min > 0) parts.push(`换手>=${formatPct(watchConfig.turnover20_min)}`)
    if (watchConfig.trend_classes.length < 4) parts.push(watchConfig.trend_classes.join('/'))
    parts.push(`Top${watchConfig.top_n}`)
    return parts.join(' | ')
  }, [watchConfig])

  const stepConfigHint: Record<Exclude<ScreenerPoolKey, 'input' | 'final' | 'watch'>, string> = {
    step1: `${returnWindowDays}日 | ${formatScreenerRankRange(stepConfigs.step1)} | 换手>=${formatPct(stepConfigs.step1.turnover_threshold)}`,
    step2: `回撤${formatPct(stepConfigs.step2.retrace_min)}~${formatPct(stepConfigs.step2.retrace_max)} | 回调<=${stepConfigs.step2.max_pullback_days}天`,
    step3: `斜率>=${stepConfigs.step3.min_vol_slope20.toFixed(2)} | 量比>=${stepConfigs.step3.min_up_down_volume_ratio.toFixed(2)} | 回调量比<=${stepConfigs.step3.max_pullback_volume_ratio.toFixed(2)}`,
    step4: `Top=${stepConfigs.step4.final_top_n} | AI>=${stepConfigs.step4.min_ai_confidence.toFixed(2)}`,
  }

  const tableSettingsContent = (
    <Space orientation="vertical" size={8} className="screener-config-320">
      <Typography.Text strong>表头设置</Typography.Text>
      <Typography.Text type="secondary" className="screener-text-12">
        勾选控制显示列，可拖动行或使用上下按钮调整列顺序。
      </Typography.Text>
      {columnOrder.map((key, index) => (
        <Row
          key={key}
          align="middle"
          gutter={8}
          draggable
          onDragStart={(event) => {
            event.dataTransfer.effectAllowed = 'move'
            event.dataTransfer.setData('text/plain', key)
            setDraggingColumnKey(key)
          }}
          onDragOver={(event) => {
            event.preventDefault()
            event.dataTransfer.dropEffect = 'move'
            if (dragOverColumnKey !== key) setDragOverColumnKey(key)
          }}
          onDragLeave={() => {
            if (dragOverColumnKey === key) setDragOverColumnKey(null)
          }}
          onDrop={(event) => {
            event.preventDefault()
            const raw = event.dataTransfer.getData('text/plain') || draggingColumnKey || ''
            if (isScreenerColumnKey(raw)) {
              reorderColumnByDrag(raw, key)
            }
            setDraggingColumnKey(null)
            setDragOverColumnKey(null)
          }}
          onDragEnd={() => {
            setDraggingColumnKey(null)
            setDragOverColumnKey(null)
          }}
          className={`screener-col-drag${dragOverColumnKey === key ? ' screener-col-drag--over' : ''}`}
        >
          <Col flex="auto">
            <Checkbox
              checked={columnVisible[key]}
              onChange={(event) => toggleColumnVisibility(key, event.target.checked)}
            >
              {columnTitleMap[key]}
            </Checkbox>
          </Col>
          <Col>
            <Space size={4}>
              <Button
                size="small"
                icon={<UpOutlined />}
                disabled={index === 0}
                onClick={() => moveColumn(key, 'up')}
              />
              <Button
                size="small"
                icon={<DownOutlined />}
                disabled={index === columnOrder.length - 1}
                onClick={() => moveColumn(key, 'down')}
              />
            </Space>
          </Col>
        </Row>
      ))}
      <Button size="small" onClick={resetColumns}>
        重置默认列
      </Button>
    </Space>
  )

  const tableRenderColumns = useMemo<ColumnsType<ScreenerResult>>(() => {
    const actionColumn: ColumnsType<ScreenerResult>[number] = {
      title: '操作',
      key: 'actions',
      width: 260,
      fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          <Button
            type="link"
            onClick={() => {
              saveScreenerCache(latestCacheSnapshotRef.current)
              setSelectedSymbol(row.symbol, row.name)
              navigate(`/stocks/${row.symbol}/chart`)
            }}
          >
            看图标注
          </Button>
          <Button
            type="link"
            icon={<ShoppingCartOutlined />}
            onClick={() => {
              try {
                const result = upsertPendingBuyDraft({
                  symbol: row.symbol,
                  name: row.name,
                  source: 'screener',
                  signal_date: runMeta?.asOfDate || dayjs().format('YYYY-MM-DD'),
                  default_quantity: defaultPendingOrderQuantity,
                  reference_price: row.latest_price,
                })
                message.success(
                  result.inserted
                    ? `已加入待买单：${row.symbol} x ${defaultPendingOrderQuantity}`
                    : `待买单已更新：${row.symbol}`,
                )
              } catch {
                message.error('加入待买单失败，请检查筛选日期。')
              }
            }}
          >
            加入待买单
          </Button>
          {nextPoolKey ? (
            <Button
              type="link"
              onClick={() => addRowToPoolManually(nextPoolKey, row)}
            >
              加入下一池
            </Button>
          ) : null}
        </Space>
      ),
    }
    return [...visibleDataColumns, actionColumn]
  }, [addRowToPoolManually, message, navigate, nextPoolKey, runMeta?.asOfDate, setSelectedSymbol, visibleDataColumns])

  return (
    <Space orientation="vertical" size={16} className="screener-full-width">
      <PageHeader
        title="选股漏斗控制台"
        subtitle="支持按第1~第4步逐步运行。每一步可独立配置，运行后再查看对应股票池。"
        badge="分步运行"
      />

      <Alert
        type={runMeta?.runId ? (runMeta.degraded ? 'warning' : 'info') : 'warning'}
        showIcon
        title={runMeta?.runId ? `Run追踪: ${runMeta.runId}` : 'Run追踪: 暂无 run_id'}
        description={(
          <Space wrap>
            {runMeta?.runId ? (
              <>
                <Typography.Text type="secondary">当前漏斗结果固定关联该 run_id。</Typography.Text>
                <Typography.Text type="secondary">筛选日期: {runMeta.asOfDate ?? '最新'}</Typography.Text>
                <Typography.Text type="secondary">
                  状态: {runMeta.degraded ? `降级(${runMeta.degradedReason ?? '未知原因'})` : '正常'}
                </Typography.Text>
              </>
            ) : (
              <Typography.Text type="secondary">
                点击"加载输入池""运行第1步"或"快速一键运行"都会生成新的 run_id，并同步到 Signals/Backtest。
              </Typography.Text>
            )}
          </Space>
        )}
      />

      <Card className="glass-card" variant="borderless">
        <Space orientation="vertical" size={14} className="screener-full-width">
          <Typography.Text strong>筛选参数</Typography.Text>
          <Row gutter={[16, 12]}>
            <Col xs={24} md={12}>
              <Controller
                control={control}
                name="board_filters"
                render={({ field }) => (
                  <Checkbox.Group
                    value={field.value}
                    onChange={(value) => {
                      const next = value as FormValues['board_filters']
                      field.onChange(next)
                      if (rawInputPool.length > 0 && inputPoolKey === currentInputPoolKey) {
                        setPools((prev) => ({
                          ...prev,
                          input: filterRowsByBoards(rawInputPool, next),
                        }))
                      }
                      invalidateFrom(1)
                    }}
                    options={[
                      { label: boardFilterLabelMap.main, value: 'main' },
                      { label: boardFilterLabelMap.gem, value: 'gem' },
                      { label: boardFilterLabelMap.star, value: 'star' },
                      { label: boardFilterLabelMap.beijing, value: 'beijing' },
                      { label: boardFilterLabelMap.st, value: 'st' },
                    ]}
                  />
                )}
              />
            </Col>
            <Col xs={24} md={6}>
              <Controller
                control={control}
                name="mode"
                render={({ field }) => (
                  <Radio.Group
                    value={field.value}
                    onChange={(evt) => {
                      field.onChange(evt.target.value)
                      invalidateFrom(2)
                    }}
                    optionType="button"
                    options={[
                      { label: '严格', value: 'strict' },
                      { label: '宽松', value: 'loose' },
                    ]}
                  />
                )}
              />
            </Col>
            <Col xs={24} md={6}>
              <Controller
                control={control}
                name="as_of_date"
                render={({ field }) => {
                  const value = field.value ? dayjs(field.value) : null
                  const pickerValue = value && value.isValid() ? value : null
                  return (
                    <Space orientation="vertical" size={4} className="screener-space-compact-4">
                      <Typography.Text type="secondary">筛选日期（留空=最新）</Typography.Text>
                      <DatePicker
                        key={`screener-asof-${field.value || 'empty'}`}
                        aria-label="筛选日期"
                        allowClear
                        defaultValue={pickerValue ?? undefined}
                        format="YYYY-MM-DD"
                        className="screener-full-width"
                        onChange={(next) => {
                          const nextValue = next ? next.format('YYYY-MM-DD') : ''
                          if (nextValue !== (field.value ?? '')) {
                            field.onChange(nextValue)
                            invalidateFrom(1)
                          }
                        }}
                      />
                    </Space>
                  )
                }}
              />
            </Col>
          </Row>

          <Row gutter={[12, 12]}>
            <Col xs={24} md={8}>
              <Space orientation="vertical" size={4} className="screener-space-compact-4">
                <Typography.Text type="secondary">策略</Typography.Text>
                <Select
                  aria-label="策略"
                  loading={strategyCatalogQuery.isLoading}
                  value={strategyId}
                  onChange={(value) => setStrategyId(String(value) as StrategyId)}
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
            <Col xs={24} md={16}>
              <DismissibleAlert
                dismissKey="screener.strategy-info"
                type="info"
                showIcon
                title={selectedStrategy ? `当前策略：${selectedStrategy.name}` : '策略信息'}
                description={
                  <Space wrap size={8}>
                    <Typography.Text type="secondary">
                      选股漏斗页仅选择策略，策略参数请在策略中心统一维护。
                    </Typography.Text>
                    <Button type="link" size="small" onClick={() => navigate('/strategy')}>
                      前往策略中心
                    </Button>
                  </Space>
                }
              />
            </Col>
          </Row>

          <Space>
            <Button
              loading={loadInputMutation.isPending && runningStep === null}
              onClick={handleSubmit(async (values) => {
                try {
                  await loadInputMutation.mutateAsync(values)
                } catch {
                  // error handled by mutation onError
                }
              })}
            >
              加载输入池
            </Button>
            <Button
              loading={runningStep === 4}
              onClick={() => {
                void executeAll()
              }}
            >
              快速一键运行（可选）
            </Button>
          </Space>
        </Space>
      </Card>

      <Card className="glass-card" variant="borderless">
        <Space orientation="vertical" size={10} className="screener-full-width">
          <Typography.Text strong>快捷搜索与拖动加票</Typography.Text>
          <Input
            allowClear
            value={quickSearchKeyword}
            onChange={(event) => setQuickSearchKeyword(event.target.value)}
            placeholder="输入股票代码或名称，拖动结果卡片到任意池（推荐拖到终选池）"
          />
          <Space wrap size={[8, 8]}>
            {quickSearchRows.length > 0 ? (
              quickSearchRows.map((row) => (
                <Tag
                  key={`quick-${row.symbol}`}
                  color="processing"
                  className="screener-tag-grab"
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = 'move'
                    event.dataTransfer.setData('text/plain', row.symbol)
                    setDraggingSymbol(row.symbol)
                  }}
                  onDragEnd={() => {
                    setDraggingSymbol(null)
                    setDragOverPool(null)
                  }}
                >
                  {row.symbol.toUpperCase()} {row.name}
                </Tag>
              ))
            ) : (
              <Typography.Text type="secondary">暂无匹配结果</Typography.Text>
            )}
          </Space>
        </Space>
      </Card>

      <Row gutter={[12, 12]}>
        {poolOrder.map((poolKey) => {
          const requiredStep = poolStepIndex[poolKey]
          const isAvailable = requiredStep === 0 || requiredStep <= executedStep
          const isActive = activePool === poolKey
          const isDropTarget = isAvailable && poolKey !== 'input' && poolKey !== activePool
          const isDragOver = draggingSymbol !== null && dragOverPool === poolKey && isDropTarget
          const canExport = isAvailable && pools[poolKey].length > 0

          return (
            <Col xs={24} sm={12} md={8} lg={8} xl={6} xxl={6} key={poolKey}>
              <Card
                className={`glass-card screener-pool-card${isActive ? ' screener-pool-card--active' : ''}${isDragOver ? ' screener-pool-card--dragover' : ''}`}
                variant="borderless"
                hoverable
                onClick={() => onClickPool(poolKey)}
                onDragOver={(event) => {
                  if (!isDropTarget) return
                  event.preventDefault()
                  event.dataTransfer.dropEffect = 'move'
                  if (dragOverPool !== poolKey) setDragOverPool(poolKey)
                }}
                onDragLeave={() => {
                  if (dragOverPool === poolKey) setDragOverPool(null)
                }}
                onDrop={(event) => {
                  if (!isDropTarget) return
                  event.preventDefault()
                  event.stopPropagation()
                  const symbol = event.dataTransfer.getData('text/plain')
                  handleDropToPool(poolKey, symbol)
                  setDragOverPool(null)
                  setDraggingSymbol(null)
                }}
              >
                <Space orientation="vertical" size={6} className="screener-full-width-overflow">
                  <Statistic
                    title={poolLabelMap[poolKey]}
                    value={cardValues[poolKey]}
                  />
                  {poolKey === 'input' ? (
                    <div
                      className="screener-action-grid screener-action-grid-108"
                    >
                      <Button
                        size="small"
                        className="screener-btn-wrap"
                        onClick={(event) => {
                          event.stopPropagation()
                          void handleSubmit(async (values) => {
                            try {
                              await loadInputMutation.mutateAsync(values)
                            } catch {
                              // error handled by mutation onError
                            }
                          })()
                        }}
                      >
                        重新加载输入池
                      </Button>
                      <Dropdown
                        trigger={['click']}
                        menu={buildExportMenu(poolKey)}
                        disabled={!canExport}
                      >
                        <Button
                          size="small"
                          className="screener-btn-wrap"
                          onClick={(event) => event.stopPropagation()}
                        >
                          导出
                        </Button>
                      </Dropdown>
                    </div>
                  ) : poolKey === 'final' ? (
                    <div
                      className="screener-action-grid screener-action-grid-108"
                    >
                      <Dropdown
                        trigger={['click']}
                        menu={buildExportMenu(poolKey)}
                        disabled={!canExport}
                      >
                        <Button
                          size="small"
                          className="screener-btn-wrap"
                          onClick={(event) => event.stopPropagation()}
                        >
                          导出
                        </Button>
                      </Dropdown>
                      <Button
                        size="small"
                        danger
                        className="screener-btn-wrap"
                        disabled={pools.final.length === 0}
                        onClick={(event) => {
                          event.stopPropagation()
                          setPools((prev) => ({ ...prev, final: [] }))
                          if (activePool === 'final') {
                            const fallbackPool =
                              executedStep >= 4 ? 'step4' : executedStep >= 3 ? 'step3' : executedStep >= 2 ? 'step2' : executedStep >= 1 ? 'step1' : 'input'
                            setActivePool(fallbackPool)
                          }
                          message.success('终选池已清空')
                        }}
                      >
                        清空终选池
                      </Button>
                    </div>
                  ) : poolKey === 'watch' ? (
                    <div
                      className="screener-action-grid screener-action-grid-88"
                    >
                      <Popover trigger="click" placement="bottom" content={watchConfigContent}>
                        <Button
                          size="small"
                          icon={<SettingOutlined />}
                          className="screener-btn-wrap"
                          onClick={(event) => event.stopPropagation()}
                        />
                      </Popover>
                      <Dropdown
                        trigger={['click']}
                        menu={buildExportMenu(poolKey)}
                        disabled={!canExport}
                      >
                        <Button
                          size="small"
                          className="screener-btn-wrap"
                          onClick={(event) => event.stopPropagation()}
                        >
                          导出
                        </Button>
                      </Dropdown>
                      <Button
                        size="small"
                        danger
                        className="screener-btn-wrap"
                        disabled={pools.watch.length === 0}
                        onClick={(event) => {
                          event.stopPropagation()
                          setPools((prev) => ({ ...prev, watch: [] }))
                          if (activePool === 'watch') setActivePool('input')
                          message.success('观察池已清空')
                        }}
                      >
                        清空
                      </Button>
                    </div>
                  ) : (
                    <div
                      className="screener-action-grid screener-action-grid-88"
                    >
                      <Button
                        size="small"
                        type={requiredStep === executedStep + 1 ? 'primary' : 'default'}
                        loading={runningStep === requiredStep}
                        className="screener-btn-wrap"
                        onClick={(event) => {
                          event.stopPropagation()
                          void executeStep(requiredStep as 1 | 2 | 3 | 4)
                        }}
                      >
                        运行第{requiredStep}步
                      </Button>
                      <Popover
                        trigger="click"
                        placement="bottom"
                        content={
                          requiredStep === 1
                            ? step1ConfigContent
                            : requiredStep === 2
                              ? step2ConfigContent
                              : requiredStep === 3
                                ? step3ConfigContent
                                : step4ConfigContent
                        }
                      >
                        <Button
                          size="small"
                          icon={<SettingOutlined />}
                          className="screener-btn-wrap"
                          onClick={(event) => event.stopPropagation()}
                        />
                      </Popover>
                      <Dropdown
                        trigger={['click']}
                        menu={buildExportMenu(poolKey)}
                        disabled={!canExport}
                      >
                        <Button
                          size="small"
                          className="screener-btn-wrap"
                          onClick={(event) => event.stopPropagation()}
                        >
                          导出
                        </Button>
                      </Dropdown>
                    </div>
                  )}
                  {poolKey === 'watch' ? (
                    <Typography.Text type="secondary" className="screener-text-break-12">
                      {watchConfigHint}
                    </Typography.Text>
                  ) : poolKey !== 'input' && poolKey !== 'final' ? (
                    <Typography.Text type="secondary" className="screener-text-break-12">
                      {stepConfigHint[poolKey]}
                    </Typography.Text>
                  ) : null}
                  <Typography.Text type="secondary" className="screener-text-break">
                    {isAvailable
                      ? poolKey === 'final'
                        ? '任意池可手工拖入终选池'
                        : poolKey === 'watch'
                          ? '基于输入池按规则自动筛选'
                          : '点击查看该股票池'
                      : `请先运行到第${requiredStep}步`}
                  </Typography.Text>
                  {isDropTarget ? (
                    <Typography.Text type="secondary" className="screener-text-break">
                      可拖动股票到此池手工添加
                    </Typography.Text>
                  ) : null}
                </Space>
              </Card>
            </Col>
          )
        })}
      </Row>

      {inputPoolOutdated ? (
        <Alert
          type="info"
          showIcon
          title="输入池参数已变更，当前展示的是旧输入池"
          description="点击「运行第1步」「快速一键运行」或「加载输入池」后会按新参数重算，并刷新新的 run_id。"
        />
      ) : null}

      <Card className="glass-card" variant="borderless">
        <Space orientation="vertical" size={10} className="screener-full-width">
          <Row justify="space-between" align="middle">
            <Col>
              <Typography.Text strong>
                当前股票池: {poolLabelMap[activePool]}（筛选后 {filteredRows.length} / 总 {rows.length}）
              </Typography.Text>
            </Col>
            <Col>
              <Space wrap size={[8, 8]}>
                {nextPoolKey ? (
                  <Typography.Text type="secondary">
                    拖动行到任意目标池卡片可手工加票（常用: {poolLabelMap[nextPoolKey]} / 终选池）
                  </Typography.Text>
                ) : null}
                <Typography.Text type="secondary">
                  {canReorderInTable ? '支持在表格内拖动重排' : '切换"自定义顺序"后可拖动重排'}
                </Typography.Text>
                <Popover trigger="click" placement="bottomRight" content={tableSettingsContent}>
                  <Button size="small" icon={<SettingOutlined />}>
                    表头设置
                  </Button>
                </Popover>
                <Dropdown
                  trigger={['click']}
                  menu={buildExportMenu(activePool)}
                  disabled={rows.length === 0}
                >
                  <Button size="small">导出当前池</Button>
                </Dropdown>
              </Space>
            </Col>
          </Row>
          <Row justify="space-between" align="middle">
            <Col>
              <Typography.Text type="secondary" className="screener-text-12">
                说明: 在表头点击列名可排序，点击漏斗图标可筛选。拖动到下一池是"手工加票"。
              </Typography.Text>
            </Col>
            <Col>
              <Button
                size="small"
                onClick={() => {
                  setKeywordFilter('')
                  setTrendFilters([])
                  setStageFilters([])
                  setPathStateFilters([])
                  setThemeStageFilters([])
                  setSortField('manual')
                  setSortDirection('desc')
                }}
              >
                重置表头筛选/排序
              </Button>
            </Col>
          </Row>
          <Table<ScreenerResult>
            rowKey="symbol"
            rowClassName={(record) => {
              if (!canDragRows) return ''
              const cls = ['screener-table-row']
              if (canReorderInTable) cls.push('screener-table-row-reorderable')
              else cls.push('screener-table-row-draggable')
              if (canReorderInTable && dragOverSymbol === record.symbol) cls.push('screener-table-row-dragover')
              return cls.join(' ')
            }}
            dataSource={filteredRows}
            columns={tableRenderColumns}
            onChange={(_, filters, sorter) => {
              const trendValues = Array.isArray(filters.trend_class)
                ? filters.trend_class
                    .map((value) => String(value))
                    .filter((value): value is TrendClass => isTrendClass(value))
                : []
              const stageValues = Array.isArray(filters.stage)
                ? filters.stage
                    .map((value) => String(value))
                    .filter((value): value is StageType => isStageType(value))
                : []
              const pathStateValues = Array.isArray(filters.path_state)
                ? filters.path_state
                    .map((value) => String(value))
                    .filter((value): value is ScreenerPathState =>
                      value === '上涨路径中' || value === '回踩蓄势' || value === '高位回撤' || value === '整理观察')
                : []
              const themeStageValues = Array.isArray(filters.theme_stage)
                ? filters.theme_stage
                    .map((value) => String(value))
                    .filter((value): value is ThemeStage => isThemeStage(value))
                : []
              const keywordValue = Array.isArray(filters.symbol) && filters.symbol.length > 0
                ? String(filters.symbol[0] ?? '')
                : ''

              setTrendFilters(trendValues)
              setStageFilters(stageValues)
              setPathStateFilters(pathStateValues)
              setThemeStageFilters(themeStageValues)
              setKeywordFilter(keywordValue)

              const sorterObj = Array.isArray(sorter) ? sorter[0] : sorter
              const order = sorterObj?.order
              const field = String(sorterObj?.field ?? sorterObj?.columnKey ?? '')
              if (order && isTableSortField(field)) {
                setSortField(field)
                setSortDirection(order === 'ascend' ? 'asc' : 'desc')
              } else {
                setSortField('manual')
                setSortDirection('desc')
              }
            }}
            onRow={(record) => ({
              draggable: canDragRows,
              onDragStart: (event) => {
                if (!canDragRows) return
                event.dataTransfer.effectAllowed = 'move'
                event.dataTransfer.setData('text/plain', record.symbol)
                setDraggingSymbol(record.symbol)
              },
              onDragOver: (event) => {
                if (!canReorderInTable) return
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
                if (dragOverSymbol !== record.symbol) {
                  setDragOverSymbol(record.symbol)
                }
              },
              onDragLeave: () => {
                if (dragOverSymbol === record.symbol) {
                  setDragOverSymbol(null)
                }
              },
              onDrop: (event) => {
                if (!canReorderInTable) return
                event.preventDefault()
                event.stopPropagation()
                const sourceSymbol = event.dataTransfer.getData('text/plain') || draggingSymbol
                if (!sourceSymbol || sourceSymbol === record.symbol) return
                reorderRowsInActivePool(sourceSymbol, record.symbol)
                setDraggingSymbol(null)
                setDragOverSymbol(null)
              },
              onDragEnd: () => {
                setDraggingSymbol(null)
                setDragOverPool(null)
                setDragOverSymbol(null)
              },
              })}
            scroll={{ x: 1550 }}
            loading={loadInputMutation.isPending || (b1Loading && activePool === 'watch')}
            pagination={{
              current: tablePage,
              pageSize: tablePageSize,
              total: filteredRows.length,
              placement: ['bottomEnd'],
              showSizeChanger: true,
              pageSizeOptions: [8, 16, 24, 50],
              showQuickJumper: true,
              showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
              onChange: (page, pageSize) => {
                if (pageSize !== tablePageSize) {
                  setTablePageSize(pageSize)
                  setTablePage(1)
                  return
                }
                setTablePage(page)
              },
            }}
          />
        </Space>
      </Card>
    </Space>
  )
}
