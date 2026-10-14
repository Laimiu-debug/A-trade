import { useEffect, useMemo, useRef, useState } from 'react'
import './SignalsPage.css'
import { DataQualityAlert } from '@/shared/components/DataQualityAlert'
import dayjs, { type Dayjs } from 'dayjs'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Empty,
  Input,
  InputNumber,
  Modal,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import { LineChartOutlined, ReloadOutlined, ShoppingCartOutlined, SwapOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate, useSearchParams } from 'react-router-dom'
import wyckoffCycleDiagram from '@/assets/wyckoff-cycle.svg'
import { ApiError } from '@/shared/api/client'
import { isApiNotFoundError } from '@/shared/api/errors'
import {
  createSignalEtfBacktest,
  createSignalEtfBacktestsAuto,
  getLatestScreenerRun,
  getScreenerRun,
  getSignals,
  getStrategies,
} from '@/shared/api/endpoints'
import { clearScreenerRunIdFromCaches } from '@/shared/screenerRunCache'
import { DismissibleAlert } from '@/shared/components/DismissibleAlert'
import { PageHeader } from '@/shared/components/PageHeader'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { buildPageTitle } from '@/state/aiAssistantStore'
import {
  buildStrategyParamsPayload,
  filterVisibleStrategies,
  getSharedLastStrategyId,
  getSharedStrategyParams,
  normalizeStrategyParams,
  parseStrategyParamSchema,
  resolveDefaultStrategyId,
  setSharedStrategyParams,
} from '@/shared/utils/strategyParams'
import { upsertPendingBuyDraft } from '@/shared/utils/simPendingOrders'
import { useBacktestTaskStore } from '@/state/backtestTaskStore'
import { useUIStore } from '@/state/uiStore'
import type {
  BoardFilter,
  Market,
  SignalResult,
  SignalScanMode,
  SignalEtfBacktestAutoCreateRequest,
  SignalEtfBacktestCreateRequest,
  SignalType,
  StrategyId,
  TrendPoolStep,
} from '@/types/contracts'

type StatusFilter = 'active' | 'expiring' | 'expired' | 'all'

interface SignalTableRow extends SignalResult {
  key: string
  phase_label: string
  event_label: string
  event_count: number
  quality_score: number
  sequence_ok: boolean
  signal_age_days: number
  days_to_expire: number
  is_today_trigger: boolean
}

const signalColor: Record<SignalType, string> = {
  B: 'red',
  A: 'green',
  C: 'orange',
}

const signalWeight: Record<SignalType, number> = {
  B: 3,
  A: 2,
  C: 1,
}

const signalMeaningGuide: Array<{ code: SignalType; description: string }> = [
  {
    code: 'B',
    description: '强确认信号：阶段与事件结构更完整、综合评分更高，优先级最高。',
  },
  {
    code: 'A',
    description: '标准待买信号：量价结构较健康，满足核心条件，可跟踪择机参与。',
  },
  {
    code: 'C',
    description: '弱确认/风险偏高：常见于结构未完全确认或存在风险事件，需谨慎。',
  },
]

const wyckoffEventGuide: Array<{ event: string; name: string; description: string }> = [
  { event: 'PS', name: '初步支撑', description: '下跌尾段首次出现明显承接，卖压开始衰减。' },
  { event: 'SC', name: '卖出高潮', description: '恐慌抛售集中释放，常伴随放量与长下影。' },
  { event: 'AR', name: '自动反弹', description: 'SC 后技术性反抽，形成区间上沿参考。' },
  { event: 'ST', name: '二次测试', description: '回测 SC 区域，确认供给是否继续减少。' },
  { event: 'TSO', name: '终极洗盘', description: '吸筹末段最后一次假跌破（常与 Spring 同义/变体）。' },
  { event: 'Spring', name: '弹簧', description: '短暂跌破区间下沿后快速收回，洗盘特征明显。' },
  { event: 'SOS', name: '强势信号', description: '放量上攻并站稳关键位，需求主导增强。' },
  { event: 'JOC', name: '越区确认', description: '放量突破吸筹区上沿（Creek），是进入拉升段的关键确认。' },
  { event: 'LPS', name: '最后支撑点', description: '突破后回踩承接，常作为更稳妥介入位。' },
  { event: 'UTAD', name: '上冲假突破', description: '派发端假突破，多为风险预警而非买点。' },
  { event: 'SOW', name: '弱势信号', description: '破位下行并放量，供给重新占优。' },
  { event: 'LPSY', name: '最后供给点', description: '反抽无力后再走弱，派发风险延续。' },
]

const distributionIntroGuide: Array<{ event: string; description: string }> = [
  { event: 'PSY', description: '派发初步供给：上涨动能开始钝化，供给端逐渐抬头。' },
  { event: 'BC', description: '买入高潮：冲高放量后难以持续，常为派发区的重要高点。' },
  { event: 'AR(d)', description: '派发自动反应：从 BC 高位快速回落，形成区间下沿参考。' },
  { event: 'ST(d)', description: '派发二次测试：反抽测试上沿但承接不足，弱势结构更清晰。' },
]

const wyckoffLogicGuide = [
  '威科夫核心思想是“价格反映结果，成交量揭示意图”：看价格位置，也看放量/缩量背后的主导力量。',
  '完整周期通常经历 吸筹 -> 拉升 -> 派发 -> 下跌；事件是这些阶段中主力行为留下的痕迹。',
  'A-E 分段可理解为：A 终止前趋势，B 区间建仓/换手，C 假突破测试，D 方向确认，E 趋势延续。',
  '买点更偏向吸筹后段的确认（如 SOS/LPS/JOC），派发侧事件（UTAD/SOW/LPSY）更多是风险信号。',
  '事件不是机械顺序，关键在于“价量是否匹配”：上涨若无量常不稳，回踩若缩量更利于趋势延续。',
  '示意图已补充派发前段的 PSY/BC/AR/ST，并与 UTAD/SOW/LPSY 衔接成完整派发链。',
]

const SIGNAL_MODE_CACHE_KEY = 'tdx-signals-mode-v1'
const SIGNAL_RUN_CACHE_KEY = 'tdx-signals-run-id-v1'
const SCREENER_CACHE_KEY = 'tdx-trend-screener-cache-v4'
const SIGNAL_STEP_CACHE_KEY = 'tdx-signals-trend-step-v1'
const SIGNAL_BOARD_FILTERS_CACHE_KEY = 'tdx-signals-board-filters-v1'
const SIGNAL_MARKET_FILTERS_CACHE_KEY = 'tdx-signals-market-filters-v1'
const SIGNAL_TABLE_PAGE_SIZE_CACHE_KEY = 'tdx-signals-table-page-size-v1'
const ALLOWED_MARKET_FILTERS: Market[] = ['sh', 'sz', 'bj']
const ALLOWED_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star', 'beijing', 'st']
const MARKET_FILTER_LABELS: Record<Market, string> = {
  sh: '沪市',
  sz: '深市',
  bj: '北交所',
}
const BOARD_FILTER_LABELS: Record<BoardFilter, string> = {
  main: '主板',
  gem: '创业板',
  star: '科创板',
  beijing: '北交所',
  st: 'ST',
}

interface SignalPageCopy {
  isThsMainRetail: boolean
  title: string
  subtitle: string
  badge: string
  phaseColumnTitle: string
  eventColumnTitle: string
  scoreColumnTitle: string
  sequenceLabel: string
  riskLabel: string
  hintEmptyText: string
  detailTitle?: string
  detailDescription?: string
}

const THS_SIGNAL_STRATEGY_IDS = new Set([
  'ths_main_force_flip_v1',
  'ths_main_force_golden_cross_v1',
  'ths_force_rhythm_v1',
  'emotion_limit_up_v1',
  'limit_up_arb_v1',
])

function resolveSignalPageCopy(strategyId: StrategyId, strategyName?: string | null): SignalPageCopy {
  const normalizedStrategyId = String(strategyId ?? '').trim()
  if (THS_SIGNAL_STRATEGY_IDS.has(normalizedStrategyId)) {
    const strategyLabel = (strategyName ?? '').trim() || '主力/散户量能'
    const isRhythm = normalizedStrategyId === 'ths_force_rhythm_v1'
    const isEmotionLimitUp = normalizedStrategyId === 'emotion_limit_up_v1'
    const isLimitUpArb = normalizedStrategyId === 'limit_up_arb_v1'
    return {
      isThsMainRetail: true,
      title: isLimitUpArb ? `收盘确认信号（${strategyLabel}）` : `待买信号（${strategyLabel}）`,
      subtitle: isLimitUpArb
        ? '涨停套利：收盘后确认（昨涨停 + 今涨幅 + 今相对昨量同时满足）；触发日=确认日，入场价=触发日收盘价（与回测一致）。止盈/止损/持仓天数请在回测页配置。'
        : isEmotionLimitUp
        ? '情绪涨停：近5日天下无双金叉或紫转黄 + 当日涨停共振；信号日筛选，建议次日观察倍量与回撤后尾盘买入。'
        : isRhythm
        ? '节奏波为结构判定：只看主力量能波形（周期/波幅/自相关）；买点在主力波谷拐头。散户占优抬升为卖出信号，不参与形成判定。分数仅用于排序。'
        : '按同花顺量能公式重算：主力=EMA(MA(多,3),3)，散户=EMA(MA(空,3),10)。',
      badge: isLimitUpArb ? '涨停套利' : isEmotionLimitUp ? '情绪涨停' : isRhythm ? '主散节奏' : '主散量能',
      phaseColumnTitle: isRhythm ? '节奏分组' : '量能分组',
      eventColumnTitle: '触发标签',
      scoreColumnTitle: isRhythm ? '节奏分' : '量能分',
      sequenceLabel: '触发序列',
      riskLabel: '风险提示',
      hintEmptyText: isRhythm ? '无节奏说明' : '无量能说明',
      detailTitle: isRhythm
        ? '当前策略按主散量能波形规律筛选'
        : '当前策略按同花顺主力/散户量能公式筛选',
      detailDescription: isRhythm
        ? '表内“节奏分组”固定为主力节奏，“触发标签”对应主力波谷买点；展开行显示周期CV、主力分位与散户卖出过滤状态（非打分门槛）。'
        : '表内“量能分组”固定为主散量能，“触发标签”对应紫转黄或金叉，展开行会显示精确触发说明。',
    }
  }
  return {
    isThsMainRetail: false,
    title: '待买信号（威科夫增强）',
    subtitle: '默认趋势池后置模式；支持全市场扫描与阶段/事件评分。',
    badge: 'Wyckoff',
    phaseColumnTitle: '阶段',
    eventColumnTitle: '主事件',
    scoreColumnTitle: '评分',
    sequenceLabel: '事件序列',
    riskLabel: '风险事件',
    hintEmptyText: '无阶段提示',
  }
}

function sanitizeMarketFilters(raw: unknown): Market[] {
  if (!Array.isArray(raw)) return []
  const normalized = raw
    .map((item) => String(item).trim())
    .filter((item): item is Market => ALLOWED_MARKET_FILTERS.includes(item as Market))
  return Array.from(new Set(normalized))
}

function parseMarketFiltersFromSearchParams(searchParams: URLSearchParams): Market[] {
  const merged = searchParams
    .getAll('market_filters')
    .flatMap((item) => item.split(','))
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
  return sanitizeMarketFilters(merged)
}

function isSameMarketFilters(left: Market[], right: Market[]) {
  if (left.length !== right.length) return false
  return left.join(',') === right.join(',')
}

function sanitizeBoardFilters(raw: unknown): BoardFilter[] {
  if (!Array.isArray(raw)) return []
  const normalized = raw
    .map((item) => String(item).trim())
    .filter((item): item is BoardFilter => ALLOWED_BOARD_FILTERS.includes(item as BoardFilter))
  return Array.from(new Set(normalized))
}

function parseBoardFiltersFromSearchParams(searchParams: URLSearchParams): BoardFilter[] {
  const merged = searchParams
    .getAll('board_filters')
    .flatMap((item) => item.split(','))
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
  return sanitizeBoardFilters(merged)
}

function isSameBoardFilters(left: BoardFilter[], right: BoardFilter[]) {
  if (left.length !== right.length) return false
  return left.join(',') === right.join(',')
}

function loadCachedSignalMode(): SignalScanMode | null {
  try {
    const raw = window.localStorage.getItem(SIGNAL_MODE_CACHE_KEY)
    if (raw === 'full_market' || raw === 'trend_pool') return raw
  } catch {
    // ignore localStorage failures
  }
  return null
}

function loadCachedTrendRunId(): string | null {
  try {
    const direct = (window.localStorage.getItem(SIGNAL_RUN_CACHE_KEY) ?? '').trim()
    if (direct) return direct
  } catch {
    // ignore localStorage failures
  }

  try {
    const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { run_meta?: { runId?: unknown } }
    const runId = typeof parsed?.run_meta?.runId === 'string' ? parsed.run_meta.runId.trim() : ''
    return runId || null
  } catch {
    // ignore parse/localStorage failures
  }
  return null
}

function readScreenerRunMetaFromStorage(): { runId: string; asOfDate?: string; boardFilters: BoardFilter[] } | null {
  try {
    const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as {
      run_meta?: { runId?: unknown; asOfDate?: unknown }
      form_values?: { board_filters?: unknown }
    }
    const runId = typeof parsed?.run_meta?.runId === 'string' ? parsed.run_meta.runId.trim() : ''
    const asOfDate = typeof parsed?.run_meta?.asOfDate === 'string' ? parsed.run_meta.asOfDate.trim() : ''
    const boardFilters = sanitizeBoardFilters(parsed?.form_values?.board_filters)
    if (!runId) return null
    return { runId, asOfDate: asOfDate || undefined, boardFilters }
  } catch {
    return null
  }
}

function normalizeTrendStep(raw: string | null | undefined): TrendPoolStep {
  if (raw === 'step1' || raw === 'step2' || raw === 'step3' || raw === 'step4') return raw
  return 'auto'
}

function loadCachedTrendStep(): TrendPoolStep {
  try {
    return normalizeTrendStep(window.localStorage.getItem(SIGNAL_STEP_CACHE_KEY))
  } catch {
    return 'auto'
  }
}

function loadCachedSignalBoardFilters(): BoardFilter[] {
  try {
    const raw = window.localStorage.getItem(SIGNAL_BOARD_FILTERS_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    return sanitizeBoardFilters(parsed)
  } catch {
    return []
  }
}

function loadCachedSignalMarketFilters(): Market[] {
  try {
    const raw = window.localStorage.getItem(SIGNAL_MARKET_FILTERS_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    return sanitizeMarketFilters(parsed)
  } catch {
    return []
  }
}

function loadCachedSignalTablePageSize() {
  try {
    const raw = Number(window.localStorage.getItem(SIGNAL_TABLE_PAGE_SIZE_CACHE_KEY) ?? '')
    if (raw === 20 || raw === 50 || raw === 100) return raw
  } catch {
    // ignore localStorage failures
  }
  return 20
}

function isRunNotFoundError(error: unknown) {
  return isApiNotFoundError(error) || (error instanceof ApiError && error.code === 'RUN_NOT_FOUND')
}

function formatSignalError(error: unknown) {
  if (error instanceof ApiError) {
    const errorCode = typeof error.code === 'string' ? error.code : ''
    if (errorCode === 'RUN_NOT_FOUND') {
      return '筛选任务不存在或已失效，请重新绑定最新筛选任务。'
    }
    if (errorCode === 'REQUEST_TIMEOUT') {
      return '待买信号请求超时，请稍后重试（全市场扫描可能需要更长时间）。'
    }
    if (errorCode === 'HTTP_404') {
      return '接口不存在（404），请确认后端已更新并重启。'
    }
    if (errorCode.startsWith('HTTP_')) {
      return `待买信号请求失败（${errorCode}）：${error.message}`
    }
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return '待买信号请求失败，请检查后端服务。'
}

function buildFilters(values: string[]) {
  const unique = Array.from(new Set(values.filter((value) => value.trim().length > 0)))
  return unique
    .sort((a, b) => a.localeCompare(b, 'zh-CN'))
    .map((value) => ({ text: value, value }))
}

function parseDateValue(value: string) {
  const parsed = dayjs(value)
  if (!parsed.isValid()) return 0
  return parsed.valueOf()
}

function normalizeSignalRow(item: SignalResult, todayStart: dayjs.Dayjs): SignalTableRow {
  const trigger = dayjs(item.trigger_date).startOf('day')
  const expire = dayjs(item.expire_date).startOf('day')
  const safeTrigger = trigger.isValid() ? trigger : todayStart
  const safeExpire = expire.isValid() ? expire : safeTrigger.add(2, 'day')
  const eventCount = item.wy_event_count ?? item.wy_events?.length ?? 0
  const phaseLabel = item.wyckoff_phase?.trim() || '阶段未明'
  const eventLabel = item.wyckoff_signal?.trim() || item.wy_events?.[item.wy_events.length - 1] || '-'
  const qualityScore =
    typeof item.entry_quality_score === 'number'
      ? item.entry_quality_score
      : Math.min(99, item.priority * 25 + eventCount * 6)
  const sequenceOk = item.wy_sequence_ok ?? false
  const signalAgeDays =
    typeof item.signal_age_days === 'number'
      ? Math.max(0, Math.round(item.signal_age_days))
      : Math.max(0, todayStart.diff(safeTrigger, 'day'))
  const daysToExpire = safeExpire.diff(todayStart, 'day')
  const isTodayTrigger = safeTrigger.isSame(todayStart, 'day')

  return {
    ...item,
    key: item.symbol,
    phase_label: phaseLabel,
    event_label: eventLabel,
    event_count: eventCount,
    quality_score: qualityScore,
    sequence_ok: sequenceOk,
    signal_age_days: signalAgeDays,
    days_to_expire: daysToExpire,
    is_today_trigger: isTodayTrigger,
  }
}

function renderTimelinessTag(row: SignalTableRow) {
  if (row.days_to_expire < 0) {
    return <Tag>已失效</Tag>
  }
  if (row.days_to_expire <= 1) {
    return <Tag color="orange">临期({row.days_to_expire}天)</Tag>
  }
  return <Tag color="green">有效({row.days_to_expire}天)</Tag>
}

export function SignalsPage() {
  const { message } = AntdApp.useApp()
  const navigate = useNavigate()
  const setSelectedSymbol = useUIStore((state) => state.setSelectedSymbol)
  const backtestPayloadById = useBacktestTaskStore((state) => state.payloadById)
  const backtestTasksById = useBacktestTaskStore((state) => state.tasksById)
  const [searchParams, setSearchParams] = useSearchParams()
  const cachedMode = useMemo(() => loadCachedSignalMode(), [])
  const cachedTrendStep = useMemo(() => loadCachedTrendStep(), [])
  const cachedSignalBoardFilters = useMemo(() => loadCachedSignalBoardFilters(), [])
  const cachedSignalMarketFilters = useMemo(() => loadCachedSignalMarketFilters(), [])
  const cachedSignalTablePageSize = useMemo(() => loadCachedSignalTablePageSize(), [])
  const cachedRunMeta = useMemo(() => readScreenerRunMetaFromStorage(), [])
  const [cachedRunId, setCachedRunId] = useState<string | null>(() => loadCachedTrendRunId())
  const runIdFromQuery = (searchParams.get('run_id') ?? searchParams.get('signal_run_id') ?? '').trim()
  const runId = runIdFromQuery || cachedRunId || undefined
  const initialMarketFiltersFromQuery = parseMarketFiltersFromSearchParams(searchParams)
  const initialMarketFilters =
    initialMarketFiltersFromQuery.length > 0 ? initialMarketFiltersFromQuery : cachedSignalMarketFilters
  const initialBoardFiltersFromQuery = parseBoardFiltersFromSearchParams(searchParams)
  const initialBoardFilters =
    initialBoardFiltersFromQuery.length > 0
      ? initialBoardFiltersFromQuery
      : cachedRunMeta?.boardFilters?.length
        ? cachedRunMeta.boardFilters
        : cachedSignalBoardFilters
  const initialAsOfDate = searchParams.get('as_of_date') ?? ''
  const initialTrendStep = normalizeTrendStep(searchParams.get('trend_step') ?? searchParams.get('signal_trend_step') ?? cachedTrendStep)
  const initialModeParam = searchParams.get('mode') ?? searchParams.get('signal_mode')
  const hasModeInQuery = initialModeParam === 'full_market' || initialModeParam === 'trend_pool'
  const initialMode: SignalScanMode = hasModeInQuery
    ? (initialModeParam as SignalScanMode)
    : (cachedMode ?? 'trend_pool')
  const parsedInitialWindowDays = Number(searchParams.get('window_days') ?? searchParams.get('signal_window_days') ?? 60)
  const initialWindowDays = Number.isFinite(parsedInitialWindowDays)
    ? Math.max(20, Math.min(240, Math.round(parsedInitialWindowDays)))
    : 60
  const parsedInitialMinScore = Number(searchParams.get('min_score') ?? searchParams.get('signal_min_score') ?? 60)
  const initialMinScore = Number.isFinite(parsedInitialMinScore)
    ? Math.max(0, Math.min(100, parsedInitialMinScore))
    : 60
  const parsedInitialMinEventCount = Number(
    searchParams.get('min_event_count') ?? searchParams.get('signal_min_event_count') ?? 1,
  )
  const initialMinEventCount = Number.isFinite(parsedInitialMinEventCount)
    ? Math.max(1, Math.min(12, Math.round(parsedInitialMinEventCount)))
    : 1
  const parsedInitialSignalAgeMin = Number(
    searchParams.get('signal_age_min') ?? 0,
  )
  const initialSignalAgeMin = Number.isFinite(parsedInitialSignalAgeMin)
    ? Math.max(0, Math.min(240, Math.round(parsedInitialSignalAgeMin)))
    : 0
  const initialSignalAgeMaxRaw = searchParams.get('signal_age_max') ?? ''
  const parsedInitialSignalAgeMax = Number(initialSignalAgeMaxRaw)
  const initialSignalAgeMax = Number.isFinite(parsedInitialSignalAgeMax)
    ? Math.max(initialSignalAgeMin, Math.min(240, Math.round(parsedInitialSignalAgeMax)))
    : null
  const initialCostCenterMinRaw = (searchParams.get('cost_center_min') ?? '').trim()
  const parsedInitialCostCenterMin = Number(initialCostCenterMinRaw)
  const initialCostCenterMin = initialCostCenterMinRaw && Number.isFinite(parsedInitialCostCenterMin)
    ? Math.max(0, Math.min(100, parsedInitialCostCenterMin))
    : null
  const initialWeeklyContextMinRaw = (searchParams.get('weekly_context_min') ?? '').trim()
  const parsedInitialWeeklyContextMin = Number(initialWeeklyContextMinRaw)
  const initialWeeklyContextMin = initialWeeklyContextMinRaw && Number.isFinite(parsedInitialWeeklyContextMin)
    ? Math.max(0, Math.min(100, parsedInitialWeeklyContextMin))
    : null
  const initialRequireSequence =
    (searchParams.get('require_sequence') ?? searchParams.get('signal_require_sequence')) === 'true'
  const strategyIdFromQuery = (searchParams.get('strategy_id') ?? '').trim()
  const initialStrategyId: StrategyId = strategyIdFromQuery
    ? strategyIdFromQuery
    : getSharedLastStrategyId('wyckoff_trend_v1')
  const initialStrategyParams = (() => {
    const raw = (searchParams.get('strategy_params') ?? '').trim()
    if (!raw) return getSharedStrategyParams(initialStrategyId)
    try {
      return normalizeStrategyParams(JSON.parse(raw) as unknown)
    } catch {
      return getSharedStrategyParams(initialStrategyId)
    }
  })()

  const [mode, setMode] = useState<SignalScanMode>(initialMode)
  const [trendStep, setTrendStep] = useState<TrendPoolStep>(initialTrendStep)
  const [marketFilters, setMarketFilters] = useState<Market[]>(initialMarketFilters)
  const [boardFilters, setBoardFilters] = useState<BoardFilter[]>(initialBoardFilters)
  const [asOfDate, setAsOfDate] = useState(initialAsOfDate)
  const [asOfDateTouched, setAsOfDateTouched] = useState(initialAsOfDate.length > 0)
  const [windowDays, setWindowDays] = useState(initialWindowDays)
  const [minScore, setMinScore] = useState(initialMinScore)
  const [minEventCount, setMinEventCount] = useState(initialMinEventCount)
  const [signalAgeMin, setSignalAgeMin] = useState(initialSignalAgeMin)
  const [signalAgeMax, setSignalAgeMax] = useState<number | null>(initialSignalAgeMax)
  const [costCenterMin, setCostCenterMin] = useState<number | null>(initialCostCenterMin)
  const [weeklyContextMin, setWeeklyContextMin] = useState<number | null>(initialWeeklyContextMin)
  const [requireSequence, setRequireSequence] = useState(initialRequireSequence)
  const [strategyId, setStrategyId] = useState<StrategyId>(initialStrategyId)
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>(initialStrategyParams)
  const [keyword, setKeyword] = useState('')
  const [signalFilter, setSignalFilter] = useState<'all' | SignalType>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active')
  const [quickQuantity, setQuickQuantity] = useState(1000)
  const [refreshCounter, setRefreshCounter] = useState(0)
  const [manualRefreshing, setManualRefreshing] = useState(false)
  const [guideExpanded, setGuideExpanded] = useState(false)
  const [tablePage, setTablePage] = useState(1)
  const [tablePageSize, setTablePageSize] = useState(cachedSignalTablePageSize)
  const [autoCreateModalOpen, setAutoCreateModalOpen] = useState(false)
  const [autoCreateDateRange, setAutoCreateDateRange] = useState<[Dayjs, Dayjs] | null>(null)
  const [autoCreateProgressPct, setAutoCreateProgressPct] = useState(0)

  const refreshTrackerRef = useRef(0)
  const missingRunHandledRef = useRef('')
  const searchParamsSnapshot = searchParams.toString()
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
  const signalPageCopy = useMemo(
    () => resolveSignalPageCopy(strategyId, selectedStrategy?.name ?? null),
    [selectedStrategy?.name, strategyId],
  )
  const strategySupportsMatrix = selectedStrategy?.capabilities?.supports_matrix !== false
  const strategySupportsSignalAgeFilter = selectedStrategy?.capabilities?.supports_signal_age_filter !== false
  const strategySupportsEntryDelay = selectedStrategy?.capabilities?.supports_entry_delay !== false
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
  const matchedBacktestPayload = useMemo(() => {
    if (mode !== 'trend_pool' || !runId) return null
    const strategyText = String(strategyId || '').trim()
    const entries = Object.entries(backtestPayloadById).filter(([taskId, payload]) => {
      if (!payload) return false
      if (payload.mode !== 'trend_pool') return false
      if (String(payload.run_id ?? '').trim() !== runId) return false
      if (strategyText && String(payload.strategy_id ?? '').trim() !== strategyText) return false
      if (!payload.date_from || !payload.date_to || !payload.pool_roll_mode) return false
      return Boolean(backtestTasksById[taskId])
    })
    if (entries.length <= 0) return null
    entries.sort((left, right) => {
      const leftTask = backtestTasksById[left[0]]
      const rightTask = backtestTasksById[right[0]]
      const leftTs = Date.parse(leftTask?.progress.updated_at || leftTask?.progress.started_at || '')
      const rightTs = Date.parse(rightTask?.progress.updated_at || rightTask?.progress.started_at || '')
      return (Number.isFinite(rightTs) ? rightTs : 0) - (Number.isFinite(leftTs) ? leftTs : 0)
    })
    return entries[0]?.[1] ?? null
  }, [backtestPayloadById, backtestTasksById, mode, runId, strategyId])
  const matchedBacktestDateFrom = String(matchedBacktestPayload?.date_from ?? '').trim()
  const matchedBacktestDateTo = String(matchedBacktestPayload?.date_to ?? '').trim()
  const matchedBacktestTrendStep = matchedBacktestPayload?.trend_step
  const matchedBacktestPoolRollMode = matchedBacktestPayload?.pool_roll_mode
  const matchedBacktestMaxSymbols = matchedBacktestPayload?.max_symbols

  useEffect(() => {
    if (strategyItems.length <= 0) return
    const allIds = new Set(strategyItems.map((item) => item.strategy_id))
    if (allIds.has(strategyId)) return
    const fallback = resolveDefaultStrategyId(strategyItems, 'wyckoff_trend_v1')
    setStrategyId(fallback)
    setStrategyParams(getSharedStrategyParams(fallback))
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
    if (strategySupportsSignalAgeFilter) return
    setSignalAgeMin((previous) => (previous === 0 ? previous : 0))
    setSignalAgeMax((previous) => (previous === null ? previous : null))
  }, [strategySupportsSignalAgeFilter])

  useEffect(() => {
    setSharedStrategyParams(strategyId, strategyParamsPayload)
  }, [strategyId, strategyParamsPayload])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNAL_MODE_CACHE_KEY, mode)
    } catch {
      // ignore localStorage failures
    }
  }, [mode])

  useEffect(() => {
    if (!runId) return
    try {
      window.localStorage.setItem(SIGNAL_RUN_CACHE_KEY, runId)
    } catch {
      // ignore localStorage failures
    }
    setCachedRunId((previous) => (previous === runId ? previous : runId))
  }, [runId])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNAL_STEP_CACHE_KEY, trendStep)
    } catch {
      // ignore localStorage failures
    }
  }, [trendStep])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNAL_MARKET_FILTERS_CACHE_KEY, JSON.stringify(marketFilters))
    } catch {
      // ignore localStorage failures
    }
  }, [marketFilters])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNAL_BOARD_FILTERS_CACHE_KEY, JSON.stringify(boardFilters))
    } catch {
      // ignore localStorage failures
    }
  }, [boardFilters])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNAL_TABLE_PAGE_SIZE_CACHE_KEY, String(tablePageSize))
    } catch {
      // ignore localStorage failures
    }
  }, [tablePageSize])

  useEffect(() => {
    const queryMarketFilters = parseMarketFiltersFromSearchParams(new URLSearchParams(searchParamsSnapshot))
    if (queryMarketFilters.length > 0) {
      setMarketFilters((previous) => (isSameMarketFilters(previous, queryMarketFilters) ? previous : queryMarketFilters))
    }

    const queryBoardFilters = parseBoardFiltersFromSearchParams(new URLSearchParams(searchParamsSnapshot))
    if (queryBoardFilters.length > 0) {
      setBoardFilters((previous) => (isSameBoardFilters(previous, queryBoardFilters) ? previous : queryBoardFilters))
      return
    }
    const cached = readScreenerRunMetaFromStorage()
    if (!cached || cached.boardFilters.length === 0) return
    setBoardFilters((previous) => (isSameBoardFilters(previous, cached.boardFilters) ? previous : cached.boardFilters))
  }, [mode, runId, searchParamsSnapshot])

  useEffect(() => {
    const current = new URLSearchParams(searchParamsSnapshot)
    const next = new URLSearchParams(current)

    next.set('mode', mode)
    if (mode === 'trend_pool' && runId) {
      next.set('run_id', runId)
    } else {
      next.delete('run_id')
    }
    if (mode === 'trend_pool') {
      next.set('trend_step', trendStep)
    } else {
      next.delete('trend_step')
    }
    next.set('strategy_id', strategyId)
    if (Object.keys(strategyParamsPayload).length > 0) {
      next.set('strategy_params', JSON.stringify(strategyParamsPayload))
    } else {
      next.delete('strategy_params')
    }
    next.delete('market_filters')
    if (mode === 'full_market' && marketFilters.length > 0) {
      marketFilters.forEach((item) => next.append('market_filters', item))
    }
    next.delete('board_filters')
    if (boardFilters.length > 0) {
      boardFilters.forEach((item) => next.append('board_filters', item))
    }
    next.delete('signal_run_id')
    next.delete('signal_trend_step')
    next.set('window_days', String(windowDays))
    next.set('min_score', String(minScore))
    next.set('min_event_count', String(minEventCount))
    next.set('signal_age_min', String(signalAgeMin))
    if (signalAgeMax !== null) {
      next.set('signal_age_max', String(Math.max(signalAgeMin, signalAgeMax)))
    } else {
      next.delete('signal_age_max')
    }
    if (costCenterMin !== null) {
      next.set('cost_center_min', String(Math.max(0, Math.min(100, costCenterMin))))
    } else {
      next.delete('cost_center_min')
    }
    if (weeklyContextMin !== null) {
      next.set('weekly_context_min', String(Math.max(0, Math.min(100, weeklyContextMin))))
    } else {
      next.delete('weekly_context_min')
    }
    next.set('require_sequence', String(requireSequence))

    const normalizedAsOfDate = asOfDate.trim()
    if (normalizedAsOfDate) {
      next.set('as_of_date', normalizedAsOfDate)
    } else {
      next.delete('as_of_date')
    }

    if (next.toString() !== current.toString()) {
      setSearchParams(next, { replace: true })
    }
  }, [
    asOfDate,
    minEventCount,
    minScore,
    signalAgeMax,
    signalAgeMin,
    costCenterMin,
    weeklyContextMin,
    mode,
    marketFilters,
    boardFilters,
    runId,
    trendStep,
    requireSequence,
    strategyId,
    strategyParamsPayload,
    searchParamsSnapshot,
    setSearchParams,
    windowDays,
  ])

  const runDetailQuery = useQuery({
    queryKey: ['screener-run', runId],
    queryFn: () => getScreenerRun(runId as string),
    enabled: mode === 'trend_pool' && Boolean(runId),
    retry: (failureCount, error) => !isRunNotFoundError(error) && failureCount < 2,
    staleTime: 60_000,
  })

  useEffect(() => {
    if (mode !== 'trend_pool' || !runId) return
    if (!isRunNotFoundError(runDetailQuery.error)) return
    if (missingRunHandledRef.current === runId) return
    missingRunHandledRef.current = runId

    // Auto-recover: try to bind the latest available run instead of just clearing
    getLatestScreenerRun()
      .then((latestRun) => {
        const newRunId = latestRun.run_id
        setCachedRunId(newRunId)
        try {
          window.localStorage.setItem(SIGNAL_RUN_CACHE_KEY, newRunId)
        } catch {
          // ignore localStorage failures
        }
        const next = new URLSearchParams(searchParamsSnapshot)
        next.set('run_id', newRunId)
        setSearchParams(next, { replace: true })
        message.info(`筛选任务已自动切换至最新：${newRunId}`)
      })
      .catch(() => {
        // No latest run available — clear stale ID
        clearScreenerRunIdFromCaches(runId)
        setCachedRunId((previous) => (previous === runId ? null : previous))

        const next = new URLSearchParams(searchParamsSnapshot)
        if ((next.get('run_id') ?? '').trim() === runId) {
          next.delete('run_id')
        }
        if ((next.get('signal_run_id') ?? '').trim() === runId) {
          next.delete('signal_run_id')
        }
        next.delete('board_filters')
        setSearchParams(next, { replace: true })
        message.warning(`筛选任务已失效：${runId}，请重新绑定最新筛选任务。`)
      })
  }, [message, mode, runDetailQuery.error, runId, searchParamsSnapshot, setSearchParams])

  useEffect(() => {
    if (mode !== 'trend_pool') return
    if (asOfDateTouched) return
    const runAsOfDate = runDetailQuery.data?.as_of_date?.trim() ?? ''
    if (runAsOfDate) {
      setAsOfDate(runAsOfDate)
    }
  }, [asOfDateTouched, mode, runDetailQuery.data?.as_of_date])

  const normalizedAsOfDate = asOfDate.trim()
  const replayContext = useMemo(() => {
    if (mode !== 'trend_pool') return null
    if (!normalizedAsOfDate) return null
    // If there's a matched backtest task, use its parameters
    if (matchedBacktestDateFrom && matchedBacktestDateTo && matchedBacktestPoolRollMode && matchedBacktestTrendStep) {
      if (normalizedAsOfDate >= matchedBacktestDateFrom && normalizedAsOfDate <= matchedBacktestDateTo) {
        return {
          dateFrom: matchedBacktestDateFrom,
          trendStep: matchedBacktestTrendStep,
          poolRollMode: matchedBacktestPoolRollMode,
          maxSymbols: typeof matchedBacktestMaxSymbols === 'number' ? matchedBacktestMaxSymbols : undefined,
        }
      }
    }
    // Fallback: when user sets as_of_date manually, use daily rolling from that date
    // so the candidate pool is rebuilt using historical data as of that date
    return {
      dateFrom: normalizedAsOfDate,
      trendStep: trendStep,
      poolRollMode: 'daily' as const,
      maxSymbols: undefined,
    }
  }, [
    matchedBacktestDateFrom,
    matchedBacktestDateTo,
    matchedBacktestMaxSymbols,
    matchedBacktestPoolRollMode,
    matchedBacktestTrendStep,
    mode,
    normalizedAsOfDate,
    trendStep,
  ])

  const trendPoolReady = mode !== 'trend_pool' || Boolean(runId)

  const signalQuery = useQuery({
    queryKey: [
      'signals',
      mode,
      runId,
      trendStep,
      strategyId,
      JSON.stringify(strategyParamsPayload),
      marketFilters.join(','),
      boardFilters.join(','),
      normalizedAsOfDate,
      windowDays,
      minScore,
      minEventCount,
      signalAgeMin,
      signalAgeMax ?? -1,
      requireSequence,
      replayContext?.dateFrom ?? '',
      replayContext?.trendStep ?? '',
      replayContext?.poolRollMode ?? '',
      replayContext?.maxSymbols ?? -1,
      refreshCounter,
    ],
    enabled: trendPoolReady,
    queryFn: async () => {
      const shouldRefresh = refreshCounter > refreshTrackerRef.current
      if (shouldRefresh) {
        refreshTrackerRef.current = refreshCounter
      }
      return getSignals({
        mode,
        run_id: mode === 'trend_pool' ? runId : undefined,
        trend_step: mode === 'trend_pool' ? (replayContext?.trendStep ?? trendStep) : undefined,
        strategy_id: strategyId,
        strategy_params: strategyParamsPayload,
        market_filters: mode === 'full_market' && marketFilters.length > 0 ? marketFilters : undefined,
        board_filters: boardFilters.length > 0 ? boardFilters : undefined,
        as_of_date: normalizedAsOfDate || undefined,
        refresh: shouldRefresh,
        window_days: windowDays,
        min_score: minScore,
        require_sequence: requireSequence,
        min_event_count: minEventCount,
        signal_age_min: signalAgeMin,
        signal_age_max: signalAgeMax ?? undefined,
        backtest_date_from: replayContext?.dateFrom,
        backtest_pool_roll_mode: replayContext?.poolRollMode,
        backtest_max_symbols: replayContext?.maxSymbols,
      })
    },
    staleTime: 30_000,
  })

  useEffect(() => {
    if (!signalQuery.isFetching) {
      setManualRefreshing(false)
    }
  }, [signalQuery.isFetching])

  const referenceDate = useMemo(() => {
    const raw = (signalQuery.data?.as_of_date ?? asOfDate).trim()
    if (!raw) return dayjs().startOf('day')
    const parsed = dayjs(raw).startOf('day')
    return parsed.isValid() ? parsed : dayjs().startOf('day')
  }, [asOfDate, signalQuery.data?.as_of_date])
  const asOfDatePickerValue = useMemo(() => {
    if (!asOfDate) return null
    const parsed = dayjs(asOfDate)
    return parsed.isValid() ? parsed : null
  }, [asOfDate])

  const bindLatestRunMutation = useMutation({
    mutationFn: getLatestScreenerRun,
    onSuccess: (detail) => {
      const next = new URLSearchParams(searchParamsSnapshot)
      const cached = readScreenerRunMetaFromStorage()
      const nextBoardFilters = cached?.boardFilters?.length ? cached.boardFilters : boardFilters
      next.set('mode', 'trend_pool')
      next.set('run_id', detail.run_id)
      next.delete('board_filters')
      nextBoardFilters.forEach((item) => next.append('board_filters', item))
      setBoardFilters(nextBoardFilters)
      if (detail.as_of_date?.trim()) {
        next.set('as_of_date', detail.as_of_date.trim())
      }
      setSearchParams(next, { replace: true })
      message.success(`已绑定最新筛选任务：${detail.run_id}`)
    },
    onError: (error) => {
      message.error(formatSignalError(error))
    },
  })

  const bindRunFromScreenerCache = () => {
    if (bindLatestRunMutation.isPending) {
      return
    }
    const cached = readScreenerRunMetaFromStorage()
    if (!cached?.runId) {
      message.info('当前域名下未找到选股池 run 缓存，正在尝试绑定最新筛选任务。')
      bindLatestRunMutation.mutate()
      return
    }
    const next = new URLSearchParams(searchParamsSnapshot)
    next.set('mode', 'trend_pool')
    next.set('run_id', cached.runId)
    next.delete('board_filters')
    cached.boardFilters.forEach((item) => next.append('board_filters', item))
    setBoardFilters(cached.boardFilters)
    if (cached.asOfDate) {
      next.set('as_of_date', cached.asOfDate)
    }
    setSearchParams(next, { replace: true })
    message.success(`已从选股池缓存绑定任务：${cached.runId}`)
  }

  const createSignalEtfMutation = useMutation({
    mutationFn: (payload: SignalEtfBacktestCreateRequest) => createSignalEtfBacktest(payload),
    onSuccess: (detail) => {
      message.success(`已生成待买回测：${detail.name}`)
      navigate(`/signals/backtest?highlight=${encodeURIComponent(detail.record_id)}`)
    },
    onError: (error) => {
      message.error(formatSignalError(error))
    },
  })

  const createSignalEtfAutoMutation = useMutation({
    mutationFn: (payload: SignalEtfBacktestAutoCreateRequest) => createSignalEtfBacktestsAuto(payload),
    onSuccess: (resp) => {
      setAutoCreateProgressPct(100)
      if (resp.created_count > 0) {
        message.success(`批量生成完成：新增 ${resp.created_count} 条，跳过 ${resp.skipped_count} 条，失败 ${resp.failed_count} 条。`)
      } else {
        message.warning(`未生成新记录：跳过 ${resp.skipped_count} 条，失败 ${resp.failed_count} 条。`)
      }
      setAutoCreateModalOpen(false)
      setAutoCreateProgressPct(0)
      if (resp.created.length > 0) {
        navigate(`/signals/backtest?highlight=${encodeURIComponent(resp.created[0].record_id)}`)
      }
    },
    onError: (error) => {
      setAutoCreateProgressPct(0)
      message.error(formatSignalError(error))
    },
  })

  useEffect(() => {
    if (!createSignalEtfAutoMutation.isPending) return
    setAutoCreateProgressPct((previous) => (previous > 5 ? previous : 5))
    const timer = window.setInterval(() => {
      setAutoCreateProgressPct((previous) => {
        if (previous >= 92) return previous
        const step = previous < 60 ? 4 : previous < 80 ? 2 : 1
        return Math.min(92, previous + step)
      })
    }, 450)
    return () => {
      window.clearInterval(timer)
    }
  }, [createSignalEtfAutoMutation.isPending])

  const handleOpenAutoCreateModal = () => {
    if (mode === 'trend_pool' && !runId) {
      message.warning('趋势池模式下请先绑定 run_id 后再批量生成。')
      return
    }
    const anchorRaw = (signalQuery.data?.as_of_date ?? asOfDate).trim()
    const anchor = dayjs(anchorRaw).isValid() ? dayjs(anchorRaw) : dayjs()
    setAutoCreateDateRange([anchor.subtract(20, 'day').startOf('day'), anchor.startOf('day')])
    setAutoCreateProgressPct(0)
    setAutoCreateModalOpen(true)
  }

  const handleSubmitAutoCreate = () => {
    if (mode === 'trend_pool' && !runId) {
      message.warning('趋势池模式下请先绑定 run_id 后再批量生成。')
      return
    }
    if (!autoCreateDateRange || !autoCreateDateRange[0] || !autoCreateDateRange[1]) {
      message.warning('请选择有效的日期区间。')
      return
    }
    const start = autoCreateDateRange[0]
    const end = autoCreateDateRange[1]
    if (!start.isValid() || !end.isValid()) {
      message.warning('日期区间格式无效。')
      return
    }
    const dateFrom = start.isBefore(end, 'day') ? start : end
    const dateTo = start.isBefore(end, 'day') ? end : start
    createSignalEtfAutoMutation.mutate({
      mode,
      run_id: mode === 'trend_pool' ? runId : undefined,
      strategy_id: strategyId,
      strategy_name: selectedStrategy?.name || strategyId,
      date_from: dateFrom.format('YYYY-MM-DD'),
      date_to: dateTo.format('YYYY-MM-DD'),
      trend_step: mode === 'trend_pool' ? trendStep : undefined,
      market_filters: mode === 'full_market' && marketFilters.length > 0 ? marketFilters : undefined,
      board_filters: boardFilters,
      strategy_params: strategyParamsPayload,
      window_days: windowDays,
      min_score: minScore,
      require_sequence: requireSequence,
      min_event_count: minEventCount,
      signal_age_min: signalAgeMin,
      signal_age_max: signalAgeMax ?? undefined,
      refresh_signals: false,
    })
  }

  const handleCreateSignalEtfBacktest = () => {
    if (filteredRows.length <= 0) {
      message.warning('当前筛选结果为空，无法生成待买回测。')
      return
    }
    const normalizedRows = filteredRows
      .filter((row) => row.symbol.trim().length >= 8 && /^\d{4}-\d{2}-\d{2}$/.test(row.trigger_date))
      .map((row) => ({
        symbol: row.symbol.trim().toLowerCase(),
        name: row.name?.trim() || '',
        signal_date: row.trigger_date,
        signal_primary: row.primary_signal,
        signal_event: row.event_label || row.wyckoff_signal || '',
        signal_reason: row.trigger_reason || '',
      }))
    if (normalizedRows.length <= 0) {
      message.warning('筛选结果缺少有效信号日期，无法生成待买回测。')
      return
    }
    const uniqueSignalDates = Array.from(new Set(normalizedRows.map((row) => row.signal_date))).sort()
    const asOfDate = (signalQuery.data?.as_of_date ?? '').trim()
    const signalDate = /^\d{4}-\d{2}-\d{2}$/.test(asOfDate) ? asOfDate : uniqueSignalDates[0]
    if (uniqueSignalDates.length > 1) {
      message.info(`检测到多信号日期，已按 ${signalDate} 作为回测命名日期。`)
    }
    createSignalEtfMutation.mutate({
      strategy_id: strategyId,
      strategy_name: selectedStrategy?.name || strategyId,
      signal_date: signalDate,
      constituents: normalizedRows,
    })
  }

  const effectiveSignalItems = trendPoolReady ? (signalQuery.data?.items ?? []) : []

  const pageAIContext = useMemo(
    () => ({
      page: 'signals' as const,
      title: buildPageTitle('signals', selectedStrategy?.name ?? strategyId),
      as_of_date: (signalQuery.data?.as_of_date ?? asOfDate) || undefined,
      refs: {
        ...(runId ? { run_id: runId } : {}),
        strategy_id: strategyId,
      },
      payload: {
        mode,
        trend_step: trendStep,
        strategy_id: strategyId,
        signal_count: effectiveSignalItems.length,
        min_score: minScore,
        window_days: windowDays,
      },
    }),
    [selectedStrategy?.name, strategyId, signalQuery.data?.as_of_date, asOfDate, runId, mode, trendStep, effectiveSignalItems.length, minScore, windowDays],
  )
  useRegisterPageAIContext(pageAIContext)

  const allRows = useMemo(
    () => effectiveSignalItems.map((item) => normalizeSignalRow(item, referenceDate)),
    [effectiveSignalItems, referenceDate],
  )

  const kpi = useMemo(() => {
    const valid = allRows.filter((row) => row.days_to_expire > 1)
    const expiring = allRows.filter((row) => row.days_to_expire >= 0 && row.days_to_expire <= 1)
    const expired = allRows.filter((row) => row.days_to_expire < 0)
    const todayTriggered = allRows.filter((row) => row.is_today_trigger)
    const bSignals = allRows.filter((row) => row.primary_signal === 'B')
    return {
      valid: valid.length,
      expiring: expiring.length,
      expired: expired.length,
      todayTriggered: todayTriggered.length,
      bSignals: bSignals.length,
      sourceCount: trendPoolReady ? (signalQuery.data?.source_count ?? allRows.length) : 0,
    }
  }, [allRows, signalQuery.data?.source_count, trendPoolReady])

  const filteredRows = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase()
    return allRows.filter((row) => {
      if (signalFilter !== 'all' && row.primary_signal !== signalFilter) {
        return false
      }
      if (statusFilter === 'active' && row.days_to_expire <= 1) {
        return false
      }
      if (statusFilter === 'expiring' && (row.days_to_expire < 0 || row.days_to_expire > 1)) {
        return false
      }
      if (statusFilter === 'expired' && row.days_to_expire >= 0) {
        return false
      }
      if (costCenterMin !== null && Number(row.cost_center_shift_score ?? 0) < costCenterMin) {
        return false
      }
      if (weeklyContextMin !== null && Number(row.weekly_context_score ?? 0) < weeklyContextMin) {
        return false
      }
      if (!normalizedKeyword) {
        return true
      }
      const haystack = [
        row.symbol,
        row.name,
        row.trigger_reason,
        row.phase_label,
        row.event_label,
      ]
        .join(' ')
        .toLowerCase()
      return haystack.includes(normalizedKeyword)
    })
  }, [allRows, keyword, signalFilter, statusFilter, costCenterMin, weeklyContextMin])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(filteredRows.length / tablePageSize))
    if (tablePage > maxPage) {
      setTablePage(maxPage)
    }
  }, [filteredRows.length, tablePage, tablePageSize])

  const primarySignalFilters = useMemo(
    () =>
      (['B', 'A', 'C'] as SignalType[])
        .filter((signal) => allRows.some((row) => row.primary_signal === signal))
        .map((signal) => ({ text: signal, value: signal })),
    [allRows],
  )

  const phaseFilters = useMemo(() => buildFilters(allRows.map((row) => row.phase_label)), [allRows])
  const eventFilters = useMemo(() => buildFilters(allRows.map((row) => row.event_label)), [allRows])

  const columns = useMemo<ColumnsType<SignalTableRow>>(
    () => [
      {
        title: '代码',
        dataIndex: 'symbol',
        width: 110,
        fixed: 'left',
        sorter: (a, b) => a.symbol.localeCompare(b.symbol, 'zh-CN'),
      },
      {
        title: '名称',
        dataIndex: 'name',
        width: 120,
        sorter: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
      },
      {
        title: '主信号',
        dataIndex: 'primary_signal',
        width: 92,
        filters: primarySignalFilters,
        onFilter: (value, row) => row.primary_signal === String(value),
        sorter: (a, b) => signalWeight[a.primary_signal] - signalWeight[b.primary_signal],
        render: (value: SignalType) => <Tag color={signalColor[value]}>{value}</Tag>,
      },
      {
        title: signalPageCopy.phaseColumnTitle,
        key: 'phase_label',
        width: 130,
        filters: phaseFilters,
        onFilter: (value, row) => row.phase_label === String(value),
        sorter: (a, b) => a.phase_label.localeCompare(b.phase_label, 'zh-CN'),
        render: (_, row) => row.phase_label,
      },
      {
        title: signalPageCopy.eventColumnTitle,
        key: 'event_label',
        width: 110,
        filters: eventFilters,
        onFilter: (value, row) => row.event_label === String(value),
        sorter: (a, b) => a.event_label.localeCompare(b.event_label, 'zh-CN'),
        render: (_, row) => row.event_label,
      },
      {
        title: signalPageCopy.scoreColumnTitle,
        key: 'quality_score',
        width: 90,
        sorter: (a, b) => a.quality_score - b.quality_score,
        defaultSortOrder: 'descend',
        render: (_, row) => row.quality_score.toFixed(1),
      },
      {
        title: '筹码分',
        key: 'cost_center_shift_score',
        width: 94,
        sorter: (a, b) =>
          Number(a.cost_center_shift_score ?? 0) - Number(b.cost_center_shift_score ?? 0),
        render: (_, row) => Number(row.cost_center_shift_score ?? 0).toFixed(1),
      },
      {
        title: '周线分',
        key: 'weekly_context_score',
        width: 94,
        sorter: (a, b) =>
          Number(a.weekly_context_score ?? 0) - Number(b.weekly_context_score ?? 0),
        render: (_, row) => Number(row.weekly_context_score ?? 0).toFixed(1),
      },
      {
        title: '周乘数',
        key: 'weekly_context_multiplier',
        width: 90,
        sorter: (a, b) =>
          Number(a.weekly_context_multiplier ?? 1) - Number(b.weekly_context_multiplier ?? 1),
        render: (_, row) => Number(row.weekly_context_multiplier ?? 1).toFixed(3),
      },
      {
        title: '事件数',
        key: 'event_count',
        width: 88,
        sorter: (a, b) => a.event_count - b.event_count,
        render: (_, row) => row.event_count,
      },
      {
        title: '信号年龄',
        key: 'signal_age_days',
        width: 96,
        sorter: (a, b) => a.signal_age_days - b.signal_age_days,
        render: (_, row) => `${row.signal_age_days}天`,
      },
      {
        title: '序列完整',
        key: 'sequence_ok',
        width: 96,
        filters: [
          { text: '是', value: 'yes' },
          { text: '否', value: 'no' },
        ],
        onFilter: (value, row) => (value === 'yes' ? row.sequence_ok : !row.sequence_ok),
        sorter: (a, b) => Number(a.sequence_ok) - Number(b.sequence_ok),
        render: (_, row) => (row.sequence_ok ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
      },
      {
        title: '时效',
        key: 'timeliness',
        width: 120,
        sorter: (a, b) => a.days_to_expire - b.days_to_expire,
        render: (_, row) => renderTimelinessTag(row),
      },
      {
        title: '触发日',
        dataIndex: 'trigger_date',
        width: 112,
        sorter: (a, b) => parseDateValue(a.trigger_date) - parseDateValue(b.trigger_date),
      },
      {
        title: '操作',
        key: 'actions',
        width: 240,
        fixed: 'right',
        render: (_, row) => (
          <Space size={4}>
            <Button
              type="link"
              size="small"
              icon={<LineChartOutlined />}
              onClick={() => {
                const params = new URLSearchParams({
                  signal_mode: mode,
                  signal_trend_step: trendStep,
                  signal_strategy_id: strategyId,
                  signal_window_days: String(windowDays),
                  signal_min_score: String(minScore),
                  signal_min_event_count: String(minEventCount),
                  signal_age_min: String(signalAgeMin),
                  signal_require_sequence: String(requireSequence),
                })
                if (Object.keys(strategyParamsPayload).length > 0) {
                  params.set('signal_strategy_params', JSON.stringify(strategyParamsPayload))
                }
                if (signalAgeMax !== null) {
                  params.set('signal_age_max', String(Math.max(signalAgeMin, signalAgeMax)))
                }
                if (costCenterMin !== null) {
                  params.set('signal_cost_center_min', String(Math.max(0, Math.min(100, costCenterMin))))
                }
                if (weeklyContextMin !== null) {
                  params.set('signal_weekly_context_min', String(Math.max(0, Math.min(100, weeklyContextMin))))
                }
                const signalAsOfDate = (signalQuery.data?.as_of_date ?? asOfDate).trim()
                if (signalAsOfDate) {
                  params.set('signal_as_of_date', signalAsOfDate)
                }
                if (runId) {
                  params.set('signal_run_id', runId)
                }
                marketFilters.forEach((item) => params.append('signal_market_filters', item))
                boardFilters.forEach((item) => params.append('signal_board_filters', item))
                if (row.name) {
                  params.set('signal_stock_name', row.name)
                }
                setSelectedSymbol(row.symbol, row.name)
                navigate(`/stocks/${row.symbol}/chart?${params.toString()}`)
              }}
            >
              查看K线
            </Button>
            <Button
              type="link"
              size="small"
              icon={<ShoppingCartOutlined />}
              onClick={() => {
                try {
                  const result = upsertPendingBuyDraft({
                    symbol: row.symbol,
                    name: row.name,
                    source: 'signals',
                    signal_date: row.trigger_date,
                    default_quantity: quickQuantity,
                  })
                  message.success(
                    result.inserted
                      ? `已加入待买单：${row.symbol} x ${quickQuantity}`
                      : `待买单已更新：${row.symbol}`,
                  )
                } catch {
                  message.error('加入待买单失败，请检查信号日期格式。')
                }
              }}
            >
              加入待买单
            </Button>
            <Button
              type="link"
              size="small"
              icon={<SwapOutlined />}
              onClick={() =>
                navigate(
                  `/trade?symbol=${encodeURIComponent(row.symbol)}&side=buy&quantity=${quickQuantity}&signal_date=${row.trigger_date}`,
                )
              }
            >
              去交易页
            </Button>
          </Space>
        ),
      },
    ],
    [
      bindLatestRunMutation.isPending,
      eventFilters,
      minEventCount,
      minScore,
      signalAgeMax,
      signalAgeMin,
      costCenterMin,
      weeklyContextMin,
      mode,
      marketFilters,
      navigate,
      boardFilters,
      phaseFilters,
      primarySignalFilters,
      quickQuantity,
      requireSequence,
      runId,
      signalPageCopy.eventColumnTitle,
      signalPageCopy.phaseColumnTitle,
      signalPageCopy.scoreColumnTitle,
      trendStep,
      asOfDate,
      signalQuery.data?.as_of_date,
      windowDays,
    ],
  )

  const expandedRowRender = (row: SignalTableRow) => (
    <Space orientation="vertical" size={8} style={{ width: '100%' }}>
      <Space size={[6, 6]} wrap>
        <Typography.Text type="secondary">{signalPageCopy.sequenceLabel}</Typography.Text>
        {(row.wy_events ?? []).length > 0
          ? (row.wy_events ?? []).map((event) => (
              <Tag key={`${row.symbol}-${event}`} color="blue">
                {event}
              </Tag>
            ))
          : <Tag>-</Tag>}
      </Space>
      <Space size={[6, 6]} wrap>
        <Typography.Text type="secondary">{signalPageCopy.riskLabel}</Typography.Text>
        {(row.wy_risk_events ?? []).length > 0
          ? (row.wy_risk_events ?? []).map((event) => (
              <Tag key={`${row.symbol}-risk-${event}`} color="red">
                {event}
              </Tag>
            ))
          : <Tag color="green">无</Tag>}
      </Space>
      <Typography.Text>{row.trigger_reason || row.phase_hint || signalPageCopy.hintEmptyText}</Typography.Text>
      {row.phase_hint && row.phase_hint !== row.trigger_reason ? (
        <Typography.Text type="secondary">{row.phase_hint}</Typography.Text>
      ) : null}
      <Space size={[6, 6]} wrap>
        <Tag>事件强度 {Number(row.event_strength_score ?? 0).toFixed(1)}</Tag>
        <Tag>阶段评分 {Number(row.phase_score ?? 0).toFixed(1)}</Tag>
        <Tag>结构评分 {Number(row.structure_score ?? 0).toFixed(1)}</Tag>
        <Tag>趋势评分 {Number(row.trend_score ?? 0).toFixed(1)}</Tag>
        <Tag>波动评分 {Number(row.volatility_score ?? 0).toFixed(1)}</Tag>
        <Tag>筹码分 {Number(row.cost_center_shift_score ?? 0).toFixed(1)}</Tag>
        <Tag>周线分 {Number(row.weekly_context_score ?? 0).toFixed(1)}</Tag>
        <Tag>周乘数 {Number(row.weekly_context_multiplier ?? 1).toFixed(3)}</Tag>
      </Space>
    </Space>
  )

  const handleForceRefresh = () => {
    setManualRefreshing(true)
    setRefreshCounter((previous) => previous + 1)
  }

  const errorMessage = trendPoolReady && signalQuery.error ? formatSignalError(signalQuery.error) : ''
  const signalNotes = Array.isArray(signalQuery.data?.notes) ? signalQuery.data.notes : []

  return (
    <Space data-research="page" data-page="signals" orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title={signalPageCopy.title}
        subtitle={signalPageCopy.subtitle}
        badge={signalPageCopy.badge}
      />

      {errorMessage ? (
        <Alert
          type="error"
          showIcon
          title="信号加载失败"
          description={errorMessage}
        />
      ) : null}

      {signalQuery.data?.degraded ? (
        <DataQualityAlert reason={signalQuery.data.degraded_reason} />
      ) : null}

      <div data-research="signal-source">{mode === 'trend_pool' ? (
        <Alert
          type={runId ? 'info' : 'warning'}
          showIcon
          title={runId ? `Run追踪: ${runId}` : 'Run追踪: 未绑定 run_id'}
          description={(
            <Space wrap>
              {runId ? (
                <>
                  <Typography.Text type="secondary">
                    当前趋势池信号使用该 run_id 的规则快照，并按筛选日期重建候选池。
                  </Typography.Text>
                  {replayContext ? (
                    <Typography.Text type="secondary">
                      已启用回测复盘口径: step={replayContext.trendStep}，{replayContext.poolRollMode} 滚动，起始日 {replayContext.dateFrom}。
                    </Typography.Text>
                  ) : null}
                  <Typography.Text type="secondary">
                    筛选日期: {(runDetailQuery.data?.as_of_date ?? signalQuery.data?.as_of_date ?? asOfDate) || '--'}
                  </Typography.Text>
                </>
              ) : (
                <>
                  <Typography.Text type="secondary">
                    当前 URL 缺少 run_id。请先绑定筛选任务后再查看趋势池信号。
                  </Typography.Text>
                  <Typography.Text type="secondary">建议保持同一域名访问（都用 127.0.0.1 或都用 localhost）。</Typography.Text>
                </>
              )}
              <Button
                size="small"
                loading={bindLatestRunMutation.isPending}
                onClick={bindRunFromScreenerCache}
              >
                从选股池缓存绑定
              </Button>
              <Button
                size="small"
                loading={bindLatestRunMutation.isPending}
                onClick={() => bindLatestRunMutation.mutate()}
              >
                绑定最新筛选任务
              </Button>
              <Button size="small" onClick={() => navigate('/screener')}>去选股池</Button>
            </Space>
          )}
        />
      ) : (
        <Alert
          type="info"
          showIcon
          title="Run追踪: 全市场模式"
          description="当前为 full_market，不依赖 run_id。切回趋势池模式后需绑定 run_id。"
        />
      )}</div>

      <Row data-research="stat-grid" gutter={[12, 12]}>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="有效信号" value={kpi.valid} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="临期(<=1天)" value={kpi.expiring} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="已失效" value={kpi.expired} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="今日触发" value={kpi.todayTriggered} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="B类主信号" value={kpi.bSignals} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="源候选数" value={kpi.sourceCount} />
          </Card>
        </Col>
      </Row>

      <Card data-research="signal-config" className="glass-card" variant="borderless">
        <Space orientation="vertical" size={12} style={{ width: '100%' }}><Row data-research="fields"><Col xs={24} md={12} lg={8}>
          <Typography.Text type="secondary">扫描模式</Typography.Text>
          <div>
            <Radio.Group
              value={mode}
              onChange={(event) => setMode(event.target.value as SignalScanMode)}
              optionType="button"
              options={[
                { label: '趋势池后置', value: 'trend_pool' },
                { label: '全市场扫描', value: 'full_market' },
              ]}
            />
          </div>
        </Col>
          <Col xs={24} md={12} lg={4}>
            <Typography.Text type="secondary">窗口(天)</Typography.Text>
            <InputNumber
              value={windowDays}
              min={20}
              max={240}
              style={{ width: '100%' }}
              onChange={(value) => {
                if (typeof value === 'number' && Number.isFinite(value)) {
                  setWindowDays(Math.round(value))
                }
              }}
            />
          </Col>
          <Col xs={24} md={12} lg={4}>
            <Typography.Text type="secondary">最低评分</Typography.Text>
            <InputNumber
              value={minScore}
              min={0}
              max={100}
              style={{ width: '100%' }}
              onChange={(value) => {
                if (typeof value === 'number' && Number.isFinite(value)) {
                  setMinScore(value)
                }
              }}
            />
          </Col>
          <Col xs={24} md={12} lg={4}>
            <Typography.Text type="secondary">最少事件数</Typography.Text>
            <InputNumber
              value={minEventCount}
              min={1}
              max={12}
              style={{ width: '100%' }}
              onChange={(value) => {
                if (typeof value === 'number' && Number.isFinite(value)) {
                  setMinEventCount(Math.round(value))
                }
              }}
            />
          </Col>
          <Col xs={24} md={12} lg={4}>
            <Typography.Text type="secondary">条件</Typography.Text>
            <div className="signals-filter-gap">
              <Checkbox
                checked={requireSequence}
                onChange={(event) => setRequireSequence(event.target.checked)}
              >
                要求序列完整
              </Checkbox>
            </div>
          </Col>
          <Col xs={24} md={12} lg={8}>
            <Typography.Text type="secondary">策略</Typography.Text>
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
          </Col>
          <Col xs={24} md={12} lg={6}>
            <Typography.Text type="secondary">快照日期（留空=最新）</Typography.Text>
            <DatePicker
              key={`signals-asof-${asOfDate || 'empty'}`}
              allowClear
              defaultValue={asOfDatePickerValue ?? undefined}
              format="YYYY-MM-DD"
              style={{ width: '100%' }}
              onChange={(value) => {
                const nextDate = value ? value.format('YYYY-MM-DD') : ''
                if (!asOfDateTouched) {
                  setAsOfDateTouched(true)
                }
                setAsOfDate((prev) => (prev === nextDate ? prev : nextDate))
              }}
            />
          </Col></Row><details className="research-fold"><summary>股票池、市场与高级过滤</summary><div className="research-fold-body"><Row data-research="fields"><Col xs={24} md={12} lg={4}>
            <Typography.Text type="secondary">信号年龄最小(天)</Typography.Text>
            <InputNumber
              min={0}
              max={240}
              value={signalAgeMin}
              disabled={!strategySupportsSignalAgeFilter}
              style={{ width: '100%' }}
              onChange={(value) => {
                if (!strategySupportsSignalAgeFilter) return
                const nextMin = Number.isFinite(Number(value)) ? Math.max(0, Math.min(240, Math.round(Number(value)))) : 0
                setSignalAgeMin(nextMin)
                if (signalAgeMax !== null && signalAgeMax < nextMin) {
                  setSignalAgeMax(nextMin)
                }
              }}
            />
          </Col>
            <Col xs={24} md={12} lg={4}>
              <Typography.Text type="secondary">信号年龄最大(天)</Typography.Text>
              <InputNumber
                min={signalAgeMin}
                max={240}
                value={signalAgeMax ?? undefined}
                disabled={!strategySupportsSignalAgeFilter}
                placeholder="留空=不限"
                style={{ width: '100%' }}
                onChange={(value) => {
                  if (!strategySupportsSignalAgeFilter) return
                  if (value === null || value === undefined || String(value).trim() === '') {
                    setSignalAgeMax(null)
                    return
                  }
                  const nextMax = Math.max(signalAgeMin, Math.min(240, Math.round(Number(value))))
                  setSignalAgeMax(nextMax)
                }}
              />
            </Col>
            <Col xs={24} md={12} lg={5}>
              <Typography.Text type="secondary">筹码分下限</Typography.Text>
              <InputNumber
                min={0}
                max={100}
                value={costCenterMin ?? undefined}
                placeholder="留空=不限"
                style={{ width: '100%' }}
                onChange={(value) => {
                  if (value === null || value === undefined || String(value).trim() === '') {
                    setCostCenterMin(null)
                    return
                  }
                  const next = Math.max(0, Math.min(100, Number(value)))
                  setCostCenterMin(next)
                }}
              />
            </Col>
            <Col xs={24} md={12} lg={5}>
              <Typography.Text type="secondary">周线分下限</Typography.Text>
              <InputNumber
                min={0}
                max={100}
                value={weeklyContextMin ?? undefined}
                placeholder="留空=不限"
                style={{ width: '100%' }}
                onChange={(value) => {
                  if (value === null || value === undefined || String(value).trim() === '') {
                    setWeeklyContextMin(null)
                    return
                  }
                  const next = Math.max(0, Math.min(100, Number(value)))
                  setWeeklyContextMin(next)
                }}
              />
            </Col></Row>{mode === 'trend_pool' ? (
              <Row data-research="fields" gutter={[12, 12]}>
                <Col xs={24} lg={12}>
                  <Typography.Text type="secondary">趋势池来源</Typography.Text>
                  <div>
                    <Radio.Group
                      value={trendStep}
                      onChange={(event) => setTrendStep(event.target.value as TrendPoolStep)}
                      optionType="button"
                      options={[
                        { label: '自动(step4>step3)', value: 'auto' },
                        { label: '仅Step4', value: 'step4' },
                        { label: '仅Step3', value: 'step3' },
                        { label: '仅Step2', value: 'step2' },
                        { label: '仅Step1', value: 'step1' },
                      ]}
                    />
                  </div>
                </Col>
                <Col xs={24} lg={12}>
                  <Typography.Text type="secondary">
                    板块过滤：
                    {boardFilters.length > 0
                      ? boardFilters.map((item) => BOARD_FILTER_LABELS[item]).join('、')
                      : '未设置（等同全板块）'}
                  </Typography.Text>
                  <div className="signals-filter-gap">
                    <Checkbox.Group
                      value={boardFilters}
                      onChange={(values) => setBoardFilters(sanitizeBoardFilters(values))}
                      options={ALLOWED_BOARD_FILTERS.map((item) => ({
                        label: BOARD_FILTER_LABELS[item],
                        value: item,
                      }))}
                    />
                  </div>
                </Col>
              </Row>
            ) : null}
            {mode === 'full_market' ? (
              <Row data-research="fields" gutter={[12, 12]}>
                <Col xs={24} lg={12}>
                  <Typography.Text type="secondary">
                    市场过滤：
                    {marketFilters.length > 0
                      ? marketFilters.map((item) => MARKET_FILTER_LABELS[item]).join('、')
                      : '未设置（等同全市场）'}
                  </Typography.Text>
                  <div className="signals-filter-gap">
                    <Checkbox.Group
                      value={marketFilters}
                      onChange={(values) => setMarketFilters(sanitizeMarketFilters(values))}
                      options={ALLOWED_MARKET_FILTERS.map((item) => ({
                        label: MARKET_FILTER_LABELS[item],
                        value: item,
                      }))}
                    />
                  </div>
                </Col>
                <Col xs={24} lg={12}>
                  <Typography.Text type="secondary">
                    板块过滤：
                    {boardFilters.length > 0
                      ? boardFilters.map((item) => BOARD_FILTER_LABELS[item]).join('、')
                      : '未设置（等同全板块）'}
                  </Typography.Text>
                  <div className="signals-filter-gap">
                    <Checkbox.Group
                      value={boardFilters}
                      onChange={(values) => setBoardFilters(sanitizeBoardFilters(values))}
                      options={ALLOWED_BOARD_FILTERS.map((item) => ({
                        label: BOARD_FILTER_LABELS[item],
                        value: item,
                      }))}
                    />
                  </div>
                </Col>
              </Row>
            ) : null}<details className="research-fold"><summary>策略能力说明</summary><div className="research-fold-body"><Row data-research="fields"><Col xs={24} md={12} lg={16}>
              <DismissibleAlert
                dismissKey="signals.strategy-info"
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
              <DismissibleAlert
                dismissKey="signals.strategy-center-tip"
                type="info"
                showIcon
                title="策略参数与预设已迁移到策略中心"
                description={
                  <Space wrap size={8}>
                    <Typography.Text type="secondary">当前页面仅保留策略选择，参数统一在策略中心维护。</Typography.Text>
                    <Button type="link" size="small" onClick={() => navigate('/strategy')}>
                      前往策略中心
                    </Button>
                  </Space>
                }
              /></Row></div></details></div></details>{!strategySupportsSignalAgeFilter ? (
                <Alert
                  type="warning"
                  showIcon
                  title="当前策略不支持信号年龄过滤"
                  description="已自动忽略 signal_age_min / signal_age_max，并固定为默认值。"
                />
              ) : null}
          <Row data-research="fields" gutter={[12, 12]} align="middle">
            <Col xs={24} lg={8}>
              <Input
                allowClear
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索代码/名称/触发依据"
              />
            </Col>
            <Col xs={24} lg={8}>
              <Radio.Group
                value={signalFilter}
                onChange={(event) => setSignalFilter(event.target.value as 'all' | SignalType)}
                optionType="button"
                options={[
                  { label: '全部信号', value: 'all' },
                  { label: 'B', value: 'B' },
                  { label: 'A', value: 'A' },
                  { label: 'C', value: 'C' },
                ]}
              />
            </Col>
            <Col xs={24} lg={8}>
              <Radio.Group
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
                optionType="button"
                options={[
                  { label: '有效', value: 'active' },
                  { label: '临期', value: 'expiring' },
                  { label: '已失效', value: 'expired' },
                  { label: '全部', value: 'all' },
                ]}
              />
            </Col>
          </Row>
          <Row data-research="run-actions" justify="space-between" gutter={[12, 12]}>
            <Col xs={24} lg={12}>
              <Typography.Text type="secondary">
                快照 {signalQuery.data?.as_of_date || asOfDate || '最新'} |
                生成时间 {signalQuery.data?.generated_at ?? '--'}
                {signalQuery.data?.cache_hit ? '（缓存命中）' : '（实时计算）'}
              </Typography.Text>
            </Col>
            <Col xs={24} lg={12}>
              <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                <Typography.Text type="secondary">快捷买入数量</Typography.Text>
                <InputNumber
                  min={100}
                  step={100}
                  value={quickQuantity}
                  onChange={(value) => {
                    if (typeof value === 'number' && Number.isFinite(value) && value >= 100) {
                      setQuickQuantity(Math.round(value / 100) * 100)
                    }
                  }}
                />
                <Button
                  type="primary"
                  icon={<LineChartOutlined />}
                  loading={createSignalEtfMutation.isPending}
                  disabled={filteredRows.length <= 0 || signalQuery.isLoading}
                  onClick={handleCreateSignalEtfBacktest}
                >
                  一键生成ETF回测
                </Button>
                <Button
                  icon={<LineChartOutlined />}
                  loading={createSignalEtfAutoMutation.isPending}
                  disabled={signalQuery.isLoading || !trendPoolReady}
                  onClick={handleOpenAutoCreateModal}
                >
                  区间生成ETF回测
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  loading={manualRefreshing && signalQuery.isFetching}
                  onClick={handleForceRefresh}
                >
                  手动刷新（重算）
                </Button>
              </Space>
            </Col>
          </Row></Space>
      </Card>

      <Card data-research="signal-results" className="glass-card" variant="borderless">
        <Table
          rowKey="key"
          loading={trendPoolReady && (signalQuery.isLoading || signalQuery.isFetching)}
          dataSource={filteredRows}
          columns={columns}
          scroll={{ x: 1480 }}
          pagination={{
            current: tablePage,
            pageSize: tablePageSize,
            pageSizeOptions: ['20', '50', '100'],
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, pageSize) => {
              setTablePage(page)
              setTablePageSize(pageSize)
            },
          }}
          expandable={{ expandedRowRender }}
          locale={{
            emptyText: signalQuery.isLoading ? '信号计算中...' : <Empty description="没有匹配的待买信号" />,
          }}
        />
      </Card>

      <details className="research-fold" ><summary>信号定义与策略说明</summary><div className="research-fold-body">{signalPageCopy.detailTitle ? (
        <Alert
          type="info"
          showIcon
          title={signalPageCopy.detailTitle}
          description={signalPageCopy.detailDescription}
        />
      ) : null}{signalNotes.length > 0 ? (
        <Alert
          type="info"
          showIcon
          title="策略能力提示"
          description={(
            <ul className="signals-notes-list">
              {signalNotes.map((note, idx) => (
                <li key={`${idx}-${note}`}>{note}</li>
              ))}
            </ul>
          )}
        />
      ) : null}<Card className="glass-card" variant="borderless">
          <Space orientation="vertical" size={10} style={{ width: '100%' }}>
            <Row justify="space-between" align="middle">
              <Col>
                <Typography.Text strong>信号说明（ABC / 威科夫事件 / 判定逻辑）</Typography.Text>
              </Col>
              <Col>
                <Button type="link" onClick={() => setGuideExpanded((previous) => !previous)}>
                  {guideExpanded ? '收起说明' : '展开说明'}
                </Button>
              </Col>
            </Row>

            {!guideExpanded ? (
              <Typography.Text type="secondary">
                点击“展开说明”查看 ABC 主信号定义、12 个威科夫事件释义、威科夫量价思想与标准周期示意图。
              </Typography.Text>
            ) : (
              <>
                <Typography.Text type="secondary">
                  这里介绍的是威科夫量价事件的市场思想，用于理解主力行为与阶段转换，不是软件计算流程说明。
                </Typography.Text>

                <Row gutter={[12, 12]}>
                  {signalMeaningGuide.map((item) => (
                    <Col key={item.code} xs={24} md={8}>
                      <Card size="small">
                        <Space orientation="vertical" size={6} style={{ width: '100%' }}>
                          <Space>
                            <Typography.Text strong>主信号</Typography.Text>
                            <Tag color={signalColor[item.code]}>{item.code}</Tag>
                          </Space>
                          <Typography.Text type="secondary">{item.description}</Typography.Text>
                        </Space>
                      </Card>
                    </Col>
                  ))}
                </Row>

                <Card size="small">
                  <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                    <Typography.Text strong>标准威科夫事件周期示意图</Typography.Text>
                    <img
                      src={wyckoffCycleDiagram}
                      alt="标准威科夫周期示意图：吸筹、拉升、派发、下跌及关键事件"
                      className="signals-wyckoff-diagram"
                    />
                    <Typography.Text type="secondary">
                      图示为教学模板。真实市场中事件可能重叠、变形或跳步，需结合量价强弱与市场环境综合判断。
                    </Typography.Text>
                  </Space>
                </Card>

                <Table
                  size="small"
                  rowKey="event"
                  pagination={false}
                  dataSource={wyckoffEventGuide}
                  columns={[
                    {
                      title: '事件',
                      dataIndex: 'event',
                      width: 90,
                      render: (value: string) => <Tag color="blue">{value}</Tag>,
                    },
                    { title: '中文含义', dataIndex: 'name', width: 130 },
                    { title: '说明', dataIndex: 'description' },
                  ]}
                />

                <Card size="small">
                  <Space orientation="vertical" size={6} style={{ width: '100%' }}>
                    <Typography.Text strong>派发前段补充事件（教学扩展）</Typography.Text>
                    {distributionIntroGuide.map((item) => (
                      <Typography.Text key={item.event} type="secondary">
                        <Tag color="gold">{item.event}</Tag>
                        {item.description}
                      </Typography.Text>
                    ))}
                  </Space>
                </Card>

                <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                  <Typography.Text strong>威科夫量价思想与事件逻辑</Typography.Text>
                  {wyckoffLogicGuide.map((item, index) => (
                    <Typography.Text key={item} type="secondary">
                      {index + 1}. {item}
                    </Typography.Text>
                  ))}
                </Space>
              </>
            )}
          </Space>
        </Card></div></details>

      <Modal
        title="区间生成ETF回测"
        open={autoCreateModalOpen}
        okText="开始生成"
        cancelText="取消"
        confirmLoading={createSignalEtfAutoMutation.isPending}
        onCancel={() => {
          if (createSignalEtfAutoMutation.isPending) return
          setAutoCreateModalOpen(false)
          setAutoCreateProgressPct(0)
        }}
        onOk={handleSubmitAutoCreate}
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            {mode === 'full_market'
              ? '按当前全市场扫描设置，逐日重算信号并批量生成 ETF 回测。'
              : '使用同一 run_id 快照逐日重建候选池，并按当前策略设置批量生成 ETF 回测。'}
          </Typography.Text>
          <Typography.Text>扫描模式：{mode === 'full_market' ? '全市场' : '趋势池'}</Typography.Text>
          {mode === 'trend_pool' ? <Typography.Text>run_id：{runId || '--'}</Typography.Text> : null}
          <Typography.Text>策略：{selectedStrategy?.name || strategyId}</Typography.Text>
          <DatePicker.RangePicker
            style={{ width: '100%' }}
            allowClear
            value={autoCreateDateRange ? [autoCreateDateRange[0], autoCreateDateRange[1]] : null}
            onChange={(values) => {
              if (!values || !values[0] || !values[1]) {
                setAutoCreateDateRange(null)
                return
              }
              setAutoCreateDateRange([values[0], values[1]])
            }}
          />
          <Typography.Text type="secondary">
            当前附带参数：step={trendStep}，窗口={windowDays}，最低分={minScore}，最少事件={minEventCount}。
          </Typography.Text>
          {createSignalEtfAutoMutation.isPending || autoCreateProgressPct > 0 ? (
            <Progress
              percent={Math.round(autoCreateProgressPct)}
              status={createSignalEtfAutoMutation.isPending ? 'active' : 'normal'}
              size="small"
            />
          ) : null}
        </Space>
      </Modal>
    </Space>
  )
}
