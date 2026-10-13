import { useEffect, useMemo, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import {
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  InputNumber,
  Radio,
  Row,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { CaretRightOutlined, PauseCircleOutlined, StopOutlined, SyncOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { PageHeader } from '@/shared/components/PageHeader'
import { usePageAIContextRegistration } from '@/shared/ai/usePageAIContextRegistration'
import { summarizeTrendLeaders } from '@/shared/ai/contextPayloads'
import { getStockCandles } from '@/shared/api/endpoints'
import { formatTrendLeadersElapsed, useTrendLeadersRunStore } from '@/state/trendLeadersRunStore'
import { searchStockLibrary, type StockSearchResult } from '@/shared/services/stockLibrary'
import type {
  BoardFilter,
  LimitUpStockSummary,
  LimitUpTimelinePoint,
  MarketTrendLeaderItem,
  MarketTrendSeriesItem,
} from '@/types/contracts'

type TabKey = 'trend' | 'ladder'
type LadderChartMode = 'block' | 'line'

const STORAGE_KEY = 'final-trade-trend-leaders-v3'
const CHART_COLORS = [
  '#c4473d',
  '#19744f',
  '#1f6feb',
  '#b45309',
  '#7c3aed',
  '#0f766e',
  '#be123c',
  '#4d7c0f',
  '#0369a1',
  '#a16207',
]

const BOARD_OPTIONS: Array<{ label: string; value: BoardFilter }> = [
  { label: '主板', value: 'main' },
  { label: '创业板', value: 'gem' },
  { label: '科创板', value: 'star' },
  { label: '北交所', value: 'beijing' },
  { label: 'ST', value: 'st' },
]

function tsCodeToSymbol(tsCode: string) {
  const normalized = tsCode.trim().toUpperCase()
  const [code, suffix] = normalized.split('.')
  if (!code || !suffix) return ''
  if (suffix === 'SH') return `sh${code}`.toLowerCase()
  if (suffix === 'SZ') return `sz${code}`.toLowerCase()
  if (suffix === 'BJ') return `bj${code}`.toLowerCase()
  return ''
}

function defaultDateRange(): [Dayjs, Dayjs] {
  return [dayjs().subtract(3, 'year'), dayjs()]
}

function loadPersistedState() {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as {
      tab?: TabKey
      dateFrom?: string
      dateTo?: string
      windowDays?: number
      dailyTopN?: number
      recentDays?: number
      historicalMinBoards?: number
      boardFilters?: BoardFilter[]
      ladderChartMode?: LadderChartMode
      manualSymbols?: Array<{ symbol: string; name: string }>
      hiddenSymbols?: string[]
    }
  } catch {
    return null
  }
}

function savePersistedState(payload: Record<string, unknown>) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // ignore
  }
}

function formatPct(value: number) {
  if (!Number.isFinite(value)) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

const LEGEND_RIGHT_GAP = 156
const LADDER_BLOCK_MAX_STOCKS = 40
const TREND_CHART_MAX_SERIES = 30

function buildRightScrollLegend(data: string[]) {
  return {
    show: data.length > 0,
    type: 'scroll',
    orient: 'vertical',
    right: 10,
    top: 24,
    bottom: 40,
    data,
    textStyle: { fontSize: 11 },
  }
}

function buildRecentDataZoom(length: number) {
  const visibleDays = 80
  const start = length <= visibleDays ? 0 : Math.max(0, 100 - (visibleDays / length) * 100)
  return [
    { type: 'inside', start, end: 100 },
    { type: 'slider', height: 18, bottom: 8, start, end: 100 },
  ]
}

function buildManualTrendSeries(
  candles: Array<{ time: string; close: number }>,
  dateFrom: string,
  dates: string[],
) {
  const startBar = candles.find((item) => item.time >= dateFrom)
  if (!startBar || startBar.close <= 0) return null
  const base = startBar.close
  const closeMap = new Map(candles.map((item) => [item.time, item.close]))
  return dates.map((date) => {
    const close = closeMap.get(date)
    if (close == null || close <= 0) return null
    return Number((((close - base) / base) * 100).toFixed(2))
  })
}

function trimTrendSeriesForChart(
  series: MarketTrendSeriesItem[],
  leaders: MarketTrendLeaderItem[],
): MarketTrendSeriesItem[] {
  const leaderMap = new Map(leaders.map((leader) => [leader.symbol, leader]))
  const topSymbols = new Set(
    [...leaders]
      .sort((a, b) => b.max_return_pct - a.max_return_pct || b.leader_days - a.leader_days)
      .slice(0, TREND_CHART_MAX_SERIES)
      .map((leader) => leader.symbol),
  )

  return series
    .map((item) => {
      const leader = leaderMap.get(item.symbol)
      if (!leader?.first_leader_date) {
        return item
      }

      const windowStart = leader.first_leader_date
      const windowEnd = leader.last_leader_date ?? windowStart
      const inWindow = item.points.filter(
        (point) => point.date >= windowStart && point.date <= windowEnd,
      )
      if (inWindow.length === 0) {
        return { ...item, points: [] }
      }

      let peakDate = inWindow[0].date
      let peakReturn = inWindow[0].return_pct
      for (const point of inWindow) {
        if (point.return_pct > peakReturn) {
          peakReturn = point.return_pct
          peakDate = point.date
        }
      }

      const segment = inWindow.filter((point) => point.date <= peakDate)
      if (segment.length < 2) {
        return { ...item, points: [] }
      }

      const baseReturn = segment[0].return_pct
      return {
        ...item,
        points: segment.map((point) => ({
          date: point.date,
          return_pct: Number((point.return_pct - baseReturn).toFixed(2)),
        })),
      }
    })
    .filter((item) => {
      if (item.points.length < 2) return false
      if (!leaderMap.has(item.symbol)) return true
      return topSymbols.has(item.symbol)
    })
}

function trimManualTrendPoints(values: Array<number | null>, dates: string[]) {
  const pairs = dates.flatMap((date, index) => {
    const value = values[index]
    if (value == null) return []
    return [{ date, value }]
  })
  if (pairs.length < 2) return []

  let peakIndex = 0
  for (let index = 1; index < pairs.length; index += 1) {
    if (pairs[index].value > pairs[peakIndex].value) {
      peakIndex = index
    }
  }

  const segment = pairs.slice(0, peakIndex + 1)
  const base = segment[0].value
  return segment.map((row) => ({
    date: row.date,
    return_pct: Number((row.value - base).toFixed(2)),
  }))
}

function buildTrendChartOption(
  dates: string[],
  series: MarketTrendSeriesItem[],
  hiddenSymbols: Set<string>,
) {
  const visible = series.filter((item) => !hiddenSymbols.has(item.symbol))
  if (visible.length === 0 || dates.length === 0) return null

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const rows = Array.isArray(params) ? params : [params]
        if (rows.length <= 0) return ''
        const first = rows[0] as { axisValueLabel?: string; axisValue?: string }
        const title = first?.axisValueLabel || first?.axisValue || ''
        const body = rows
          .map((row) => {
            const item = row as { marker?: string; seriesName?: string; value?: unknown }
            const num = Number(item.value)
            const valueText = Number.isFinite(num) ? formatPct(num) : '--'
            return `${item.marker ?? ''}${item.seriesName ?? ''} <span style="float:right;margin-left:16px;font-weight:600;">${valueText}</span>`
          })
          .slice(0, 24)
        const suffix = rows.length > 24 ? `<br/>… 共 ${rows.length} 条` : ''
        return [title, ...body].join('<br/>') + suffix
      },
    },
    legend: buildRightScrollLegend(visible.map((item) => item.name)),
    grid: { left: 52, right: LEGEND_RIGHT_GAP, top: 16, bottom: 48 },
    dataZoom: buildRecentDataZoom(dates.length),
    xAxis: { type: 'category', data: dates, name: '日期' },
    yAxis: {
      type: 'value',
      name: '涨幅(%)',
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    series: visible.map((item, index) => {
      const pointMap = new Map(item.points.map((point) => [point.date, point.return_pct]))
      return {
        name: item.name,
        type: 'line',
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.6 },
        color: CHART_COLORS[index % CHART_COLORS.length],
        data: dates.map((date) => pointMap.get(date) ?? null),
      }
    }),
  }
}

function buildLadderBlockOption(dates: string[], timeline: LimitUpTimelinePoint[]) {
  if (dates.length === 0 || timeline.length === 0) return null
  const grouped = new Map<string, LimitUpTimelinePoint[]>()
  timeline.forEach((point) => {
    const bucket = grouped.get(point.symbol) ?? []
    bucket.push(point)
    grouped.set(point.symbol, bucket)
  })

  const entries = [...grouped.entries()]
    .sort((a, b) => {
      const maxA = Math.max(...a[1].map((item) => item.board_height))
      const maxB = Math.max(...b[1].map((item) => item.board_height))
      return maxB - maxA
    })
    .slice(0, LADDER_BLOCK_MAX_STOCKS)

  const legendNames = entries.map(([, points]) => points[0]?.name ?? '')

  return {
    tooltip: {
      trigger: 'item',
      formatter: (raw: { seriesName?: string; name?: string; value?: number | null }) => {
        const height = Number(raw.value)
        if (!raw.seriesName || !raw.name || !Number.isFinite(height)) return ''
        return `${raw.seriesName}<br/>${raw.name}<br/>连板高度：${height}板`
      },
    },
    legend: buildRightScrollLegend(legendNames),
    grid: { left: 52, right: LEGEND_RIGHT_GAP, top: 16, bottom: 48 },
    dataZoom: buildRecentDataZoom(dates.length),
    xAxis: { type: 'category', data: dates, name: '日期' },
    yAxis: {
      type: 'value',
      name: '连板高度',
      min: 0,
      interval: 1,
      axisLabel: { formatter: (value: number) => `${value}板` },
    },
    series: entries.map(([symbol, points], index) => {
      const pointMap = new Map(points.map((point) => [point.date, point.board_height]))
      const color = CHART_COLORS[index % CHART_COLORS.length]
      return {
        name: points[0]?.name ?? symbol,
        type: 'bar',
        color,
        barMaxWidth: 14,
        barMinHeight: 3,
        itemStyle: { color },
        data: dates.map((date) => pointMap.get(date) ?? '-'),
      }
    }),
  }
}

function buildLadderLineOption(dates: string[], timeline: LimitUpTimelinePoint[]) {
  if (dates.length === 0 || timeline.length === 0) return null
  const grouped = new Map<string, LimitUpTimelinePoint[]>()
  timeline.forEach((point) => {
    const bucket = grouped.get(point.symbol) ?? []
    bucket.push(point)
    grouped.set(point.symbol, bucket)
  })

  const entries = [...grouped.entries()].sort((a, b) => {
    const maxA = Math.max(...a[1].map((item) => item.board_height))
    const maxB = Math.max(...b[1].map((item) => item.board_height))
    return maxB - maxA
  })

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const rows = Array.isArray(params) ? params : [params]
        if (rows.length <= 0) return ''
        const first = rows[0] as { axisValueLabel?: string; axisValue?: string }
        const title = first?.axisValueLabel || first?.axisValue || ''
        const body = rows
          .map((row) => {
            const item = row as { marker?: string; seriesName?: string; value?: unknown }
            const num = Number(item.value)
            const valueText = Number.isFinite(num) ? `${num}板` : '--'
            return `${item.marker ?? ''}${item.seriesName ?? ''} <span style="float:right;margin-left:16px;font-weight:600;">${valueText}</span>`
          })
          .slice(0, 24)
        return [title, ...body].join('<br/>')
      },
    },
    legend: buildRightScrollLegend(entries.map(([, points]) => points[0]?.name ?? '')),
    grid: { left: 52, right: LEGEND_RIGHT_GAP, top: 16, bottom: 48 },
    dataZoom: buildRecentDataZoom(dates.length),
    xAxis: { type: 'category', data: dates, name: '日期' },
    yAxis: {
      type: 'value',
      name: '连板高度',
      min: 0,
      interval: 1,
      axisLabel: { formatter: (value: number) => `${value}板` },
    },
    series: entries.map(([symbol, points], index) => {
      const pointMap = new Map(points.map((point) => [point.date, point.board_height]))
      return {
        name: points[0]?.name ?? symbol,
        type: 'line',
        step: 'end',
        showSymbol: true,
        symbolSize: 6,
        connectNulls: false,
        color: CHART_COLORS[index % CHART_COLORS.length],
        data: dates.map((date) => pointMap.get(date) ?? null),
      }
    }),
  }
}

export function MarketTrendPage() {
  const navigate = useNavigate()
  const persisted = loadPersistedState()
  const [tab, setTab] = useState<TabKey>(persisted?.tab ?? 'trend')
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>(() => {
    if (persisted?.dateFrom && persisted?.dateTo) {
      return [dayjs(persisted.dateFrom), dayjs(persisted.dateTo)]
    }
    return defaultDateRange()
  })
  const [windowDays, setWindowDays] = useState(persisted?.windowDays ?? 20)
  const [dailyTopN, setDailyTopN] = useState(persisted?.dailyTopN ?? 5)
  const [recentDays, setRecentDays] = useState(persisted?.recentDays ?? 5)
  const [historicalMinBoards, setHistoricalMinBoards] = useState(persisted?.historicalMinBoards ?? 3)
  const [boardFilters, setBoardFilters] = useState<BoardFilter[]>(
    persisted?.boardFilters ?? ['main', 'gem', 'star'],
  )
  const [ladderChartMode, setLadderChartMode] = useState<LadderChartMode>(persisted?.ladderChartMode ?? 'block')
  const [hiddenSymbols, setHiddenSymbols] = useState<Set<string>>(
    () => new Set(persisted?.hiddenSymbols ?? []),
  )
  const [manualSymbols, setManualSymbols] = useState<Array<{ symbol: string; name: string }>>(
    persisted?.manualSymbols ?? [],
  )
  const [manualKeyword, setManualKeyword] = useState('')
  const [manualOptions, setManualOptions] = useState<StockSearchResult[]>([])
  const [manualLoading, setManualLoading] = useState(false)

  const runStatus = useTrendLeadersRunStore((s) => s.status)
  const taskKind = useTrendLeadersRunStore((s) => s.taskKind)
  const elapsedSeconds = useTrendLeadersRunStore((s) => s.elapsedSeconds)
  const trendData = useTrendLeadersRunStore((s) => s.trendResult)
  const ladderData = useTrendLeadersRunStore((s) => s.ladderResult)
  const runTrend = useTrendLeadersRunStore((s) => s.runTrend)
  const runLadder = useTrendLeadersRunStore((s) => s.runLadder)
  const pauseRun = useTrendLeadersRunStore((s) => s.pause)
  const stopRun = useTrendLeadersRunStore((s) => s.stop)
  const continueRun = useTrendLeadersRunStore((s) => s.continueRun)

  const marketTrendAIPayload = useMemo(
    () =>
      summarizeTrendLeaders({
        tab,
        leaderCount: trendData?.leaders?.length ?? 0,
        ladderCount: ladderData?.stocks?.length ?? 0,
        topSymbols: (trendData?.leaders ?? []).slice(0, 10).map((item) => ({
          symbol: item.symbol,
          name: item.name,
          score: item.max_return_pct,
        })),
      }),
    [ladderData?.stocks?.length, tab, trendData?.leaders],
  )
  usePageAIContextRegistration({ market_trend: marketTrendAIPayload })

  const isTrendTask = taskKind === 'trend'
  const isLadderTask = taskKind === 'ladder'
  const isRunning = runStatus === 'running'
  const isPaused = runStatus === 'paused'
  const activeTaskRunning = isRunning && ((tab === 'trend' && isTrendTask) || (tab === 'ladder' && isLadderTask))
  const activeTaskPaused = isPaused && ((tab === 'trend' && isTrendTask) || (tab === 'ladder' && isLadderTask))
  const activeTaskBusy = activeTaskRunning || activeTaskPaused

  useEffect(() => {
    savePersistedState({
      tab,
      dateFrom: dateRange[0]?.format('YYYY-MM-DD'),
      dateTo: dateRange[1]?.format('YYYY-MM-DD'),
      windowDays,
      dailyTopN,
      recentDays,
      historicalMinBoards,
      boardFilters,
      ladderChartMode,
      manualSymbols,
      hiddenSymbols: [...hiddenSymbols],
    })
  }, [
    tab,
    dateRange,
    windowDays,
    dailyTopN,
    recentDays,
    historicalMinBoards,
    boardFilters,
    ladderChartMode,
    manualSymbols,
    hiddenSymbols,
  ])

  useEffect(() => {
    let active = true
    const timer = window.setTimeout(async () => {
      const keyword = manualKeyword.trim()
      if (!keyword) {
        if (active) setManualOptions([])
        return
      }
      try {
        if (active) setManualLoading(true)
        const rows = await searchStockLibrary(keyword, 20)
        if (active) setManualOptions(rows)
      } catch (error) {
        if (active) message.error(error instanceof Error ? error.message : '股票搜索失败')
      } finally {
        if (active) setManualLoading(false)
      }
    }, 180)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [manualKeyword])

  const manualCandleQueries = useQueries({
    queries: manualSymbols.map((item) => ({
      queryKey: ['trend-leaders-manual', item.symbol, trendData?.date_from, trendData?.date_to],
      queryFn: async () => {
        const payload = await getStockCandles(item.symbol)
        return payload.candles
      },
      enabled: Boolean(trendData?.dates?.length) && tab === 'trend',
      staleTime: 60_000,
    })),
  })

  const mergedTrendSeries = useMemo(() => {
    const base = trendData?.series ?? []
    const leaders = trendData?.leaders ?? []
    if (!trendData?.dates?.length) return base

    const manualSeries: MarketTrendSeriesItem[] = manualSymbols
      .map((item, index) => {
        if (base.some((row) => row.symbol === item.symbol)) return null
        const candles = manualCandleQueries[index]?.data ?? []
        const values = buildManualTrendSeries(candles, trendData.date_from, trendData.dates)
        if (!values) return null
        const points = trimManualTrendPoints(values, trendData.dates)
        if (points.length < 2) return null
        return {
          symbol: item.symbol,
          name: item.name,
          points,
        }
      })
      .filter((item): item is MarketTrendSeriesItem => Boolean(item))

    return trimTrendSeriesForChart([...base, ...manualSeries], leaders)
  }, [manualCandleQueries, manualSymbols, trendData])

  const trendChartOption = useMemo(() => {
    if (!trendData?.dates?.length) return null
    return buildTrendChartOption(trendData.dates, mergedTrendSeries, hiddenSymbols)
  }, [hiddenSymbols, mergedTrendSeries, trendData?.dates])

  const ladderChartOption = useMemo(() => {
    if (!ladderData?.dates?.length || !ladderData.timeline.length) return null
    return ladderChartMode === 'block'
      ? buildLadderBlockOption(ladderData.dates, ladderData.timeline)
      : buildLadderLineOption(ladderData.dates, ladderData.timeline)
  }, [ladderChartMode, ladderData])

  function handleTrendScan() {
    const [from, to] = dateRange
    if (!from || !to) {
      message.warning('请选择日期区间')
      return
    }
    setHiddenSymbols(new Set())
    void runTrend(
      {
        date_from: from.format('YYYY-MM-DD'),
        date_to: to.format('YYYY-MM-DD'),
        window_days: windowDays,
        daily_top_n: dailyTopN,
        board_filters: boardFilters,
      },
      {
        onSuccess: () => {
          const data = useTrendLeadersRunStore.getState().trendResult
          if (data) {
            message.success(`扫描完成：${data.leaders.length} 只交替龙头，耗时 ${data.elapsed_sec.toFixed(1)}s`)
          }
        },
        onError: (errorMessage) => message.error(errorMessage),
        onPaused: () => message.info('趋势龙头扫描已暂停，可点击继续'),
      },
    )
  }

  function handleLadderScan() {
    const [from, to] = dateRange
    if (!from || !to) {
      message.warning('请选择日期区间')
      return
    }
    void runLadder(
      {
        date_from: from.format('YYYY-MM-DD'),
        date_to: to.format('YYYY-MM-DD'),
        recent_days: recentDays,
        historical_min_boards: historicalMinBoards,
        board_filters: boardFilters,
      },
      {
        onSuccess: () => {
          const data = useTrendLeadersRunStore.getState().ladderResult
          if (data) {
            message.success(`扫描完成：${data.stocks.length} 只连板票 / ${data.timeline.length} 个节点，耗时 ${data.elapsed_sec.toFixed(1)}s`)
          }
        },
        onError: (errorMessage) => message.error(errorMessage),
        onPaused: () => message.info('连板梯队扫描已暂停，可点击继续'),
      },
    )
  }

  function handleContinueScan() {
    void continueRun({
      onSuccess: () => {
        const state = useTrendLeadersRunStore.getState()
        if (state.taskKind === 'trend' && state.trendResult) {
          message.success(`扫描完成：${state.trendResult.leaders.length} 只交替龙头，耗时 ${state.trendResult.elapsed_sec.toFixed(1)}s`)
        } else if (state.taskKind === 'ladder' && state.ladderResult) {
          message.success(`扫描完成：${state.ladderResult.stocks.length} 只连板票 / ${state.ladderResult.timeline.length} 个节点，耗时 ${state.ladderResult.elapsed_sec.toFixed(1)}s`)
        }
      },
      onError: (errorMessage) => message.error(errorMessage),
      onPaused: () => message.info('扫描已暂停，可点击继续'),
    })
  }

  function toggleHiddenSymbol(symbol: string) {
    setHiddenSymbols((previous) => {
      const next = new Set(previous)
      if (next.has(symbol)) next.delete(symbol)
      else next.add(symbol)
      return next
    })
  }

  function addManualStock(tsCode: string) {
    const matched = manualOptions.find((item) => item.ts_code === tsCode)
    if (!matched) return
    const symbol = tsCodeToSymbol(matched.ts_code)
    if (!symbol) {
      message.warning('无法识别该股票代码')
      return
    }
    setManualSymbols((previous) => {
      if (previous.some((item) => item.symbol === symbol)) return previous
      return [...previous, { symbol, name: matched.name }]
    })
    setHiddenSymbols((previous) => {
      const next = new Set(previous)
      next.delete(symbol)
      return next
    })
    setManualKeyword('')
    setManualOptions([])
  }

  function openChart(symbol: string, name: string) {
    const params = new URLSearchParams({ signal_stock_name: name })
    navigate(`/stocks/${symbol}/chart?${params.toString()}`)
  }

  const trendColumns: ColumnsType<MarketTrendLeaderItem> = [
    {
      title: '股票',
      dataIndex: 'name',
      render: (_value, row) => (
        <Button type="link" size="small" onClick={() => openChart(row.symbol, row.name)}>
          {row.name}
        </Button>
      ),
    },
    { title: '代码', dataIndex: 'symbol', width: 110 },
    {
      title: '上榜天数',
      dataIndex: 'leader_days',
      width: 90,
      sorter: (a, b) => a.leader_days - b.leader_days,
      defaultSortOrder: 'descend',
    },
    {
      title: '区间最高涨幅',
      dataIndex: 'max_return_pct',
      width: 120,
      render: (value: number) => (
        <Typography.Text style={{ color: value >= 0 ? '#c4473d' : '#19744f', fontWeight: 600 }}>
          {formatPct(value)}
        </Typography.Text>
      ),
    },
    {
      title: '上榜区间',
      width: 180,
      render: (_value, row) => `${row.first_leader_date || '--'} ~ ${row.last_leader_date || '--'}`,
    },
    {
      title: '显示',
      width: 72,
      render: (_value, row) => (
        <Checkbox checked={!hiddenSymbols.has(row.symbol)} onChange={() => toggleHiddenSymbol(row.symbol)} />
      ),
    },
  ]

  const ladderColumns: ColumnsType<LimitUpStockSummary> = [
    {
      title: '股票',
      dataIndex: 'name',
      render: (_value, row) => (
        <Button type="link" size="small" onClick={() => openChart(row.symbol, row.name)}>
          {row.name}
        </Button>
      ),
    },
    { title: '代码', dataIndex: 'symbol', width: 110 },
    {
      title: '最高连板',
      dataIndex: 'max_board_height',
      width: 100,
      render: (value: number) => <Tag color="red">{value}板</Tag>,
      sorter: (a, b) => a.max_board_height - b.max_board_height,
      defaultSortOrder: 'descend',
    },
    {
      title: '最新连板',
      dataIndex: 'latest_board_height',
      width: 100,
      render: (value: number) => (value > 0 ? `${value}板` : '--'),
    },
    { title: '活跃天数', dataIndex: 'active_days', width: 90 },
  ]


  return (
    <div>
      <PageHeader
        title="趋势龙头"
        subtitle="每个交易日独立计算滚动 N 日涨幅排名；历史日期结果固化缓存，仅最近 N 个交易日重算。连板梯队近期含首板，历史 3 板起。"
      />

      <Card className="glass-card" variant="borderless">
        <Tabs
          activeKey={tab}
          onChange={(key) => setTab(key as TabKey)}
          items={[
            { key: 'trend', label: '趋势龙头' },
            { key: 'ladder', label: '连板梯队' },
          ]}
        />

        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={10} lg={8}>
              <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                <Typography.Text type="secondary">统计区间</Typography.Text>
                <DatePicker.RangePicker
                  style={{ width: '100%' }}
                  value={dateRange}
                  onChange={(values) => {
                    if (values?.[0] && values[1]) setDateRange([values[0], values[1]])
                  }}
                />
              </Space>
            </Col>
            {tab === 'trend' ? (
              <>
                <Col xs={24} md={7} lg={5}>
                  <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">滚动窗口（日，默认 20）</Typography.Text>
                    <InputNumber min={5} max={120} value={windowDays} onChange={(value) => setWindowDays(Number(value) || 20)} style={{ width: '100%' }} />
                  </Space>
                </Col>
                <Col xs={24} md={7} lg={5}>
                  <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">每日 Top N 入池</Typography.Text>
                    <InputNumber min={1} max={20} value={dailyTopN} onChange={(value) => setDailyTopN(Number(value) || 5)} style={{ width: '100%' }} />
                  </Space>
                </Col>
              </>
            ) : (
              <>
                <Col xs={24} md={7} lg={5}>
                  <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">近期天数（含首板，可设 3）</Typography.Text>
                    <InputNumber min={1} max={30} value={recentDays} onChange={(value) => setRecentDays(Number(value) || 5)} style={{ width: '100%' }} />
                  </Space>
                </Col>
                <Col xs={24} md={7} lg={5}>
                  <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">历史最低连板</Typography.Text>
                    <InputNumber min={2} max={20} value={historicalMinBoards} onChange={(value) => setHistoricalMinBoards(Number(value) || 3)} style={{ width: '100%' }} />
                  </Space>
                </Col>
              </>
            )}
            <Col xs={24} lg={6}>
              <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                <Typography.Text type="secondary">板块过滤</Typography.Text>
                <Checkbox.Group
                  options={BOARD_OPTIONS}
                  value={boardFilters}
                  onChange={(values) => setBoardFilters(values as BoardFilter[])}
                />
              </Space>
            </Col>
          </Row>

          <Space wrap align="center">
            {!activeTaskBusy ? (
              <Button type="primary" loading={isRunning} disabled={isRunning} onClick={tab === 'trend' ? handleTrendScan : handleLadderScan}>
                {tab === 'trend' ? '扫描交替龙头' : '扫描连板全时段'}
              </Button>
            ) : (
              <>
                {activeTaskRunning ? (
                  <Button icon={<PauseCircleOutlined />} onClick={() => pauseRun()}>
                    暂停
                  </Button>
                ) : (
                  <Button type="primary" icon={<CaretRightOutlined />} onClick={handleContinueScan}>
                    继续
                  </Button>
                )}
                <Button danger icon={<StopOutlined />} onClick={() => stopRun()}>
                  停止
                </Button>
                <Typography.Text type="secondary">
                  <SyncOutlined spin={activeTaskRunning} style={{ marginRight: 6 }} />
                  {activeTaskPaused ? '已暂停' : '扫描中'} · 已用时 {formatTrendLeadersElapsed(elapsedSeconds)}
                </Typography.Text>
              </>
            )}
            {isRunning && !activeTaskBusy ? (
              <Typography.Text type="secondary">
                <SyncOutlined spin style={{ marginRight: 6 }} />
                另一任务后台运行中 · {formatTrendLeadersElapsed(elapsedSeconds)}
              </Typography.Text>
            ) : null}
            {tab === 'trend' && trendData ? (
              <Typography.Text type="secondary">
                {trendData.date_from} ~ {trendData.date_to} · 固化 {trendData.cache_hits} 日 · 重算 {trendData.computed_days} 日（近 {trendData.live_days} 日）· 入图 {mergedTrendSeries.length} 只 · {trendData.elapsed_sec.toFixed(1)}s
              </Typography.Text>
            ) : null}
            {tab === 'ladder' && ladderData ? (
              <Typography.Text type="secondary">
                {ladderData.date_from} ~ {ladderData.date_to} · 节点 {ladderData.timeline.length} · 股票 {ladderData.stocks.length} 只
              </Typography.Text>
            ) : null}
          </Space>
        </Space>
      </Card>

      {tab === 'trend' ? (
        <Card className="glass-card" variant="borderless" style={{ marginTop: 16 }} title="涨幅折线图（入池 → 阶段高点）">
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
              <Typography.Text type="secondary">
                每段龙头仅展示入池日至阶段最高涨幅，入池日归一化为 0%；图表默认 Top {TREND_CHART_MAX_SERIES}，默认聚焦最近约 80 个交易日
              </Typography.Text>
              <AutoComplete
                style={{ width: 260 }}
                value={manualKeyword}
                options={manualOptions.map((item) => ({
                  value: item.ts_code,
                  label: `${item.name} (${item.ts_code})`,
                }))}
                onSearch={setManualKeyword}
                onSelect={addManualStock}
                placeholder="手动添加对比股票"
                notFoundContent={manualLoading ? '搜索中...' : '无匹配'}
              />
            </Space>
            {manualSymbols.length > 0 ? (
              <Space wrap>
                {manualSymbols.map((item) => (
                  <Tag
                    key={item.symbol}
                    closable
                    onClose={() => setManualSymbols((rows) => rows.filter((row) => row.symbol !== item.symbol))}
                  >
                    {item.name}
                  </Tag>
                ))}
              </Space>
            ) : null}
            {trendChartOption ? (
              <ReactECharts option={trendChartOption} style={{ height: 480 }} notMerge />
            ) : (
              <Typography.Text type="secondary">扫描后将展示全时段交替龙头涨幅曲线。</Typography.Text>
            )}
          </Space>
        </Card>
      ) : (
        <Card
          className="glass-card"
          variant="borderless"
          style={{ marginTop: 16 }}
          title="连板高度图"
          extra={
            <Radio.Group
              optionType="button"
              value={ladderChartMode}
              onChange={(event) => setLadderChartMode(event.target.value as LadderChartMode)}
              options={[
                { label: '方块', value: 'block' },
                { label: '折线', value: 'line' },
              ]}
            />
          }
        >
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              横轴日期 · 纵轴连板高度；近期 {recentDays} 日含首板，更早时段仅 {historicalMinBoards} 板及以上
              {ladderChartMode === 'block' ? ` · 方块图展示最高连板 Top ${LADDER_BLOCK_MAX_STOCKS}，默认聚焦最近约 80 个交易日` : ''}
            </Typography.Text>
            {ladderChartOption ? (
              <ReactECharts option={ladderChartOption} style={{ height: 520 }} notMerge />
            ) : (
              <Typography.Text type="secondary">扫描后将展示全时段连板方块/折线，可观察新龙与老龙交替。</Typography.Text>
            )}
          </Space>
        </Card>
      )}

      <Card
        className="glass-card"
        variant="borderless"
        style={{ marginTop: 16 }}
        title={tab === 'trend' ? '交替龙头列表' : '连板股票汇总'}
      >
        {tab === 'trend' ? (
          <Table
            rowKey="symbol"
            size="small"
            loading={isRunning && isTrendTask}
            columns={trendColumns}
            dataSource={trendData?.leaders ?? []}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            locale={{ emptyText: '点击「扫描交替龙头」获取结果' }}
          />
        ) : (
          <Table
            rowKey="symbol"
            size="small"
            loading={isRunning && isLadderTask}
            columns={ladderColumns}
            dataSource={ladderData?.stocks ?? []}
            pagination={{ pageSize: 30, showSizeChanger: true }}
            locale={{ emptyText: '点击「扫描连板全时段」获取结果' }}
          />
        )}
      </Card>
    </div>
  )
}
