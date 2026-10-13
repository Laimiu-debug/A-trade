/* eslint-disable react-refresh/only-export-components */
import ReactECharts from 'echarts-for-react'
import type { BacktestStrategySignalPoint, BacktestStrategySignalStrategyInfo, CandlePoint, SignalResult, SignalType } from '@/types/contracts'
import clsx from 'clsx'
import { movingAverage } from '@/shared/utils/chart'
import './KLineChart.css'
import { resolveNearestTradingDateIndex } from '@/shared/utils/candleStats'
import { calculateThsMainRetailSeries } from '@/shared/utils/thsVolumeSignal'


const STRATEGY_COLORS_BRIGHT = [
  '#ff1744', '#00e676', '#2979ff', '#ff9100',
  '#d500f9', '#00e5ff', '#f50057', '#00bfa5',
  '#aeea00', '#651fff', '#ff6d00', '#18ffff',
]

const STRATEGY_SHAPES = [
  'triangle', 'diamond', 'circle', 'pin',
  'rect', 'roundRect', 'arrow',
]


const STRATEGY_SHAPE_MAP: Record<string, string> = {
  wyckoff_trend_v1: 'triangle',
  wyckoff_trend_v2: 'diamond',
  score_only_rank_v1: 'pin',
  ths_main_force_flip_v1: 'circle',
  ths_main_force_golden_cross_v1: 'rect',
  ths_force_rhythm_v1: 'path://M-6,0L0,-6L6,0L0,6Z',
  wulong_cluster_v1: 'roundRect',
  matrix_signal_v1: 'arrow',
  relative_strength_breakout_v1: 'path://M0,-6L6,0L0,6L-6,0Z',
  b1_mtf_v1: 'path://M0,-6L4,4L-6,-1L6,-1L-4,4Z',
  trend_king_v1: 'path://M-5,-5L5,-5L5,5L-5,5Z',
  trend_king_limitup_v1: 'path://M0,-7L3,-2L8,-2L4,2L5,7L0,4L-5,7L-4,2L-8,-2L-3,-2Z',
  trend_king_rally_v1: 'path://M-4,-6L4,-6L6,0L4,6L-4,6L-6,0Z',
  trend_king_pullback_v1: 'path://M0,6L-5,-2L5,-2Z',
  emotion_limit_up_v1: 'path://M0,-8L4,-2L8,0L4,4L0,8L-4,4L-8,0L-4,-2Z',
  limit_up_arb_v1: 'path://M-6,-2L0,-8L6,-2L4,6L-4,6Z',
}

function strategyShape(strategyId: string): string {
  return STRATEGY_SHAPE_MAP[strategyId] || STRATEGY_SHAPES[Math.abs(strategyId.length) % STRATEGY_SHAPES.length]
}

export function strategyColorBright(strategyId: string): string {
  let hash = 0
  for (let i = 0; i < strategyId.length; i++) {
    hash = ((hash << 5) - hash + strategyId.charCodeAt(i)) | 0
  }
  return STRATEGY_COLORS_BRIGHT[Math.abs(hash) % STRATEGY_COLORS_BRIGHT.length]
}

interface KLineChartProps {
  candles: CandlePoint[]
  signals?: SignalResult[]
  manualStartDate?: string
  aiBreakoutDate?: string
  statsRangeStartDate?: string
  statsRangeEndDate?: string
  onCandleDoubleClick?: (date: string) => void
  backtestSignals?: BacktestStrategySignalPoint[]
  backtestStrategies?: BacktestStrategySignalStrategyInfo[]
  hiddenStrategyIds?: Set<string>
  onToggleStrategy?: (strategyId: string) => void
}

interface MarkPointRecord {
  name: string
  coord: [string, number]
  value: string
  symbol?: string
  symbolSize?: number
  symbolOffset?: [number, number]
  tooltipText?: string
  itemStyle: {
    color: string
  }
}

interface MarkPointDraft extends MarkPointRecord {
  dateKey: string
}

type EventCategory = 'accumulation' | 'distributionRisk' | 'other'
type PhaseLegendType = 'accumulation' | 'distribution' | 'unknown'

interface EventPointRecord {
  value: [string, number]
  eventCode: string
  tooltipText: string
  symbolOffset?: [number, number]
  itemStyle: {
    color: string
  }
}

interface EventPointDraft extends EventPointRecord {
  dateKey: string
  category: EventCategory
  anchor: 'high' | 'low'
}

type StageRangeItem = [
  {
    name: string
    xAxis: string
    itemStyle: { color: string }
    label: { show: boolean; formatter: string; color: string; fontSize: number }
  },
  {
    xAxis: string
  },
]

interface AxisTooltipParam {
  seriesName?: string
  axisValue?: string
  dataIndex?: number
}

interface IndicatorMarkerRecord {
  value: [string, number]
  tooltipText: string
  label?: {
    position?: 'top' | 'bottom' | 'left' | 'right'
    distance?: number
  }
}

const WYCKOFF_EVENT_DISPLAY_MAP: Record<string, string> = {
  PS: 'PS Initial Support',
  SC: 'SC Selling Climax',
  AR: 'AR Automatic Rally',
  ST: 'ST Secondary Test',
  TSO: 'TSO Terminal Shakeout',
  SPRING: 'Spring',
  SOS: 'SOS Sign of Strength',
  JOC: 'JOC Jump Across Creek',
  LPS: 'LPS Last Point of Support',
  PSY: 'PSY Preliminary Supply',
  BC: 'BC Buying Climax',
  'AR(D)': 'AR(d) Auto Reaction',
  'ST(D)': 'ST(d) Secondary Test',
  UTAD: 'UTAD Upthrust After Distribution',
  SOW: 'SOW Sign of Weakness',
  LPSY: 'LPSY Last Point of Supply',
}

const WYCKOFF_EVENT_GUIDE = {
  accumulation: ['PS', 'SC', 'AR', 'ST', 'TSO', 'Spring', 'SOS', 'JOC', 'LPS'],
  risk: ['PSY', 'BC', 'AR(d)', 'ST(d)', 'UTAD', 'SOW', 'LPSY'],
}

const ACCUMULATION_EVENT_CODES = new Set(['PS', 'SC', 'AR', 'ST', 'TSO', 'SPRING', 'SOS', 'JOC', 'LPS'])
const DISTRIBUTION_EVENT_CODES = new Set(['UTAD', 'SOW', 'LPSY', 'PSY', 'BC', 'AR(D)', 'ST(D)'])
const CREEK_EVENT_CODES = new Set(['AR', 'ST', 'TSO', 'JOC', 'LPS'])
const ICE_EVENT_CODES = new Set(['AR(D)', 'ST(D)', 'LPSY', 'SOW'])

const WYCKOFF_EVENT_CN_MAP: Record<string, string> = {
  PS: '\u521d\u59cb\u652f\u6491',
  SC: '\u5356\u51fa\u9ad8\u6f6e',
  AR: '\u81ea\u52a8\u53cd\u5f39',
  ST: '\u4e8c\u6b21\u6d4b\u8bd5',
  TSO: '\u672b\u7aef\u9707\u4ed3',
  SPRING: '\u5f39\u7c27\u6d4b\u8bd5',
  SOS: '\u5f3a\u52bf\u4fe1\u53f7',
  JOC: '\u8dc3\u8fc7\u5c0f\u6eaa',
  LPS: '\u6700\u540e\u652f\u6491\u70b9',
  PSY: '\u521d\u59cb\u4f9b\u7ed9',
  BC: '\u4e70\u5165\u9ad8\u6f6e',
  'AR(D)': '\u81ea\u52a8\u56de\u843d',
  'ST(D)': '\u6d3e\u53d1\u4e8c\u6d4b',
  UTAD: '\u6d3e\u53d1\u540e\u4e0a\u51b2',
  SOW: '\u5f31\u52bf\u4fe1\u53f7',
  LPSY: '\u6700\u540e\u4f9b\u7ed9\u70b9',
}

type LegendSymbol = 'line' | 'bar' | 'triangle' | 'diamond' | 'circle' | 'pill' | 'pin' | 'area'

interface LegendItem {
  label: string
  color: string
  symbol: LegendSymbol
}

function renderLegendMarker(symbol: LegendSymbol, color: string) {
  const cssVar = { '--marker-color': color } as React.CSSProperties
  const cls = `kline-marker kline-marker--${symbol === 'pill' ? 'pill' : symbol}`
  return <span className={cls} style={cssVar} />
}

function splitPositiveSegments(value: number, segments: number[]) {
  if (!Number.isFinite(value) || value <= 0) return segments.map(() => 0)
  return segments.map((segment) => Number((value * segment).toFixed(4)))
}

function splitNegativeSegments(value: number, segments: number[]) {
  if (!Number.isFinite(value) || value <= 0) return segments.map(() => 0)
  return segments.map((segment) => Number((-value * segment).toFixed(4)))
}

function expandIndicatorAxisBound(value: number, factor = 1.12) {
  if (!Number.isFinite(value) || value === 0) return value
  return Number((value * factor).toFixed(2))
}

function resolveForceStateLabel(state: string) {
  if (state === 'rising') return '上升'
  if (state === 'falling') return '回落'
  return '走平'
}

function resolveSignalColor(signal: SignalType) {
  if (signal === 'B') return '#eb8f34'
  if (signal === 'A') return '#1677ff'
  return '#7f8c8d'
}

function resolveSignalShape(signal: SignalType) {
  if (signal === 'B') return 'triangle'
  if (signal === 'A') return 'diamond'
  return 'circle'
}

function normalizeEventCode(event: string) {
  const compact = event.trim().replace(/[\s_-]+/g, '').toUpperCase()
  if (compact === 'SPRING' || compact === 'SP') return 'SPRING'
  if (compact === 'ARD' || compact === 'AR(D)') return 'AR(D)'
  if (compact === 'STD' || compact === 'ST(D)') return 'ST(D)'
  if (compact === 'JUMPACROSSCREEK') return 'JOC'
  return compact
}

function formatEventGuideWithZh(codes: string[]) {
  return codes
    .map((code) => {
      const label = WYCKOFF_EVENT_CN_MAP[normalizeEventCode(code)]
      return label ? `${code}(${label})` : code
    })
    .join(' / ')
}

function resolveEventCategory(eventCode: string): EventCategory {
  const normalized = normalizeEventCode(eventCode)
  if (ACCUMULATION_EVENT_CODES.has(normalized)) return 'accumulation'
  if (DISTRIBUTION_EVENT_CODES.has(normalized)) return 'distributionRisk'
  return 'other'
}

function buildWyckoffBoundaryLine(
  points: EventPointDraft[],
  options: {
    allowedCodes: Set<string>
    anchor: 'high' | 'low'
    lineName: string
    color: string
  },
) {
  const {
    allowedCodes,
    anchor,
    lineName,
    color,
  } = options
  const picked = points
    .filter((item) => item.anchor === anchor && allowedCodes.has(normalizeEventCode(item.eventCode)))
    .map((item) => ({
      date: item.dateKey,
      price: Number(item.value[1]),
    }))
    .filter((item) => Number.isFinite(item.price) && item.price > 0)
  if (picked.length < 2) return null

  const dedupByDate = new Map<string, number>()
  for (const item of picked) {
    const previous = dedupByDate.get(item.date)
    if (previous === undefined) {
      dedupByDate.set(item.date, item.price)
      continue
    }
    if (anchor === 'high') {
      dedupByDate.set(item.date, Math.max(previous, item.price))
    } else {
      dedupByDate.set(item.date, Math.min(previous, item.price))
    }
  }
  const ordered = Array.from(dedupByDate.entries()).map(([date, price]) => ({ date, price }))
  if (ordered.length < 2) return null

  const start = ordered[0]
  const end = ordered[ordered.length - 1]
  if (!start || !end) return null
  return {
    tooltipText: `${lineName}: 基于 ${ordered.length} 个威科夫事件点自动计算`,
    data: [
      {
        name: lineName,
        coord: [start.date, start.price],
        symbol: 'none',
        lineStyle: { color, width: 1.8, type: 'dashed' },
        label: { show: true, formatter: lineName, color },
        tooltipText: `${lineName}: 基于 ${ordered.length} 个威科夫事件点自动计算`,
      },
      {
        coord: [end.date, end.price],
        symbol: 'none',
      },
    ] as const,
    legendItem: { label: `${lineName}(自动)`, color, symbol: 'line' as const },
  }
}

function resolveEventColor(category: EventCategory) {
  if (category === 'accumulation') return '#13c2c2'
  if (category === 'distributionRisk') return '#f5222d'
  return '#8c8c8c'
}

function resolveEventDisplayName(eventCode: string) {
  const normalized = normalizeEventCode(eventCode)
  return WYCKOFF_EVENT_DISPLAY_MAP[normalized] ?? eventCode
}

function resolvePhaseLegendType(phase: string): PhaseLegendType {
  const normalized = phase.toLowerCase()
  if (phase.includes('\u5438\u7b79') || normalized.includes('accum')) return 'accumulation'
  if (phase.includes('\u6d3e\u53d1') || normalized.includes('distrib')) return 'distribution'
  return 'unknown'
}

function resolvePhaseAreaColor(phase: string) {
  const phaseType = resolvePhaseLegendType(phase)
  if (phaseType === 'accumulation') return 'rgba(22, 119, 255, 0.10)'
  if (phaseType === 'distribution') return 'rgba(245, 34, 45, 0.10)'
  return 'rgba(120, 136, 153, 0.08)'
}

function toPrice(value: number) {
  if (!Number.isFinite(value)) return '--'
  return value.toFixed(2)
}

function toLargeNumber(value: number) {
  if (!Number.isFinite(value)) return '--'
  const abs = Math.abs(value)
  if (abs >= 100000000) return `${(value / 100000000).toFixed(2)}\u4ebf`
  if (abs >= 10000) return `${(value / 10000).toFixed(2)}\u4e07`
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function toSignedNumber(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--'
  if (value > 0) return `+${value.toFixed(digits)}`
  return value.toFixed(digits)
}

function toSignedPercent(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--'
  const pct = value * 100
  if (pct > 0) return `+${pct.toFixed(digits)}%`
  return `${pct.toFixed(digits)}%`
}

function toMaybeNumber(value: number | '-' | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return value.toFixed(2)
}

function toSignedPercentFromPrice(basePrice: number, price: number, digits = 2) {
  if (!Number.isFinite(basePrice) || basePrice <= 0 || !Number.isFinite(price)) return '--'
  return toSignedPercent(price / basePrice - 1, digits)
}

function buildStackOffsetsAbove(count: number) {
  if (count <= 1) return [-24]
  const step = 10
  const base = -30
  return Array.from({ length: count }, (_, index) => base + index * step)
}

function buildStackOffsetsBelow(count: number) {
  if (count <= 1) return [26]
  const step = 10
  const base = 30
  return Array.from({ length: count }, (_, index) => base + index * step)
}

function toStackedMarkPoints(points: MarkPointDraft[]) {
  const grouped = new Map<string, MarkPointDraft[]>()
  for (const point of points) {
    const existed = grouped.get(point.dateKey)
    if (existed) {
      existed.push(point)
    } else {
      grouped.set(point.dateKey, [point])
    }
  }

  const stacked: MarkPointRecord[] = []
  for (const group of grouped.values()) {
    const offsets = buildStackOffsetsAbove(group.length)
    group.forEach((point, index) => {
      stacked.push({
        name: point.name,
        coord: point.coord,
        value: point.value,
        symbol: point.symbol,
        symbolSize: point.symbolSize,
        tooltipText: point.tooltipText,
        itemStyle: point.itemStyle,
        symbolOffset: [0, offsets[index]],
      })
    })
  }
  return stacked
}

function toStackedEventPoints(points: EventPointDraft[]) {
  const grouped = new Map<string, EventPointDraft[]>()
  for (const point of points) {
    const groupKey = `${point.dateKey}|${point.anchor}`
    const existed = grouped.get(groupKey)
    if (existed) {
      existed.push(point)
    } else {
      grouped.set(groupKey, [point])
    }
  }

  const byCategory: Record<EventCategory, EventPointRecord[]> = {
    accumulation: [],
    distributionRisk: [],
    other: [],
  }

  for (const group of grouped.values()) {
    const anchor = group[0].anchor
    const offsets = anchor === 'high' ? buildStackOffsetsAbove(group.length) : buildStackOffsetsBelow(group.length)
    group.forEach((point, index) => {
      byCategory[point.category].push({
        value: point.value,
        eventCode: point.eventCode,
        tooltipText: point.tooltipText,
        itemStyle: point.itemStyle,
        symbolOffset: [0, offsets[index]],
      })
    })
  }

  return byCategory
}

function buildBacktestMarkersOnMainChart(
  backtestSignals: BacktestStrategySignalPoint[],
  hiddenStrategyIds: Set<string>,
  xData: string[],
  candles: CandlePoint[],
) {
  const visible = backtestSignals.filter((bs) => !hiddenStrategyIds.has(bs.strategy_id))
  if (visible.length === 0) return []

  const groupedByDate = new Map<string, BacktestStrategySignalPoint[]>()
  for (const bs of visible) {
    const idx = resolveNearestTradingDateIndex(bs.signal_date, xData)
    if (idx < 0) continue
    const mappedDate = xData[idx]
    const list = groupedByDate.get(mappedDate)
    if (list) list.push(bs)
    else groupedByDate.set(mappedDate, [bs])
  }

  const byStrategy = new Map<string, { data: { value: [string, number]; tooltipText: string }[]; color: string; shape: string; name: string }>()
  for (const [date, signals] of groupedByDate) {
    const idx = xData.indexOf(date)
    if (idx < 0) continue
    const high = candles[idx].high
    const priceRange = candles[idx].high - candles[idx].low
    const baseGap = Math.max(priceRange * 0.25, 0.15)
    const step = Math.max(priceRange * 0.12, 0.08)

    signals.forEach((bs, i) => {
      const sid = bs.strategy_id
      let entry = byStrategy.get(sid)
      if (!entry) {
        entry = { data: [], color: strategyColorBright(sid), shape: strategyShape(sid), name: bs.strategy_name || sid }
        byStrategy.set(sid, entry)
      }
      const yOffset = high + baseGap + step * i
      entry.data.push({
        value: [date, yOffset],
        tooltipText: `策略:${bs.strategy_name || sid}\n信号:${bs.entry_signal}\n阶段:${bs.entry_phase}\n评级:${bs.event_grade}\n品质分:${bs.entry_quality_score.toFixed(1)}\n日期:${bs.signal_date}`,
      })
    })
  }

  return Array.from(byStrategy.entries()).map(([_sid, { data, color, shape, name }]) => ({
    name: `BT:${name}`,
    type: 'scatter' as const,
    xAxisIndex: 0,
    yAxisIndex: 0,
    data,
    symbol: shape,
    symbolSize: 10,
    itemStyle: { color, borderColor: 'rgba(0,0,0,0.6)', borderWidth: 0.8 },
    z: 8,
    tooltip: {
      formatter: (params: { data?: { tooltipText?: string } }) => params.data?.tooltipText ?? '',
    },
  }))
}

function buildBacktestScatterSeries(
  backtestSignals: BacktestStrategySignalPoint[],
  hiddenStrategyIds: Set<string>,
  xData: string[],
  backtestStrategies: BacktestStrategySignalStrategyInfo[],
) {
  const visibleStrategies = backtestStrategies
    .filter((s) => s.signal_count > 0 && !hiddenStrategyIds.has(s.strategy_id))
  if (visibleStrategies.length === 0) return []

  const strategyYIndex = new Map<string, number>()
  visibleStrategies.forEach((s, i) => strategyYIndex.set(s.strategy_id, i))

  const byStrategy = new Map<string, BacktestStrategySignalPoint[]>()
  for (const bs of backtestSignals) {
    if (hiddenStrategyIds.has(bs.strategy_id)) continue
    if (!strategyYIndex.has(bs.strategy_id)) continue
    const list = byStrategy.get(bs.strategy_id)
    if (list) list.push(bs)
    else byStrategy.set(bs.strategy_id, [bs])
  }
  if (byStrategy.size === 0) return []

  const allData: { value: [string, number]; tooltipText: string; itemStyle: { color: string } }[] = []
  for (const [strategyId, signals] of byStrategy) {
    const color = strategyColorBright(strategyId)
    const yIdx = strategyYIndex.get(strategyId) ?? 0
    const sname = visibleStrategies[yIdx]?.strategy_name || strategyId
    for (const bs of signals) {
      const idx = resolveNearestTradingDateIndex(bs.signal_date, xData)
      if (idx < 0) continue
      allData.push({
        value: [xData[idx], yIdx],
        tooltipText: `策略:${sname}\n信号:${bs.entry_signal}\n阶段:${bs.entry_phase}\n评级:${bs.event_grade}\n品质分:${bs.entry_quality_score.toFixed(1)}\n日期:${bs.signal_date}`,
        itemStyle: { color },
      })
    }
  }

  if (allData.length === 0) return []

  return [{
    name: '策略信号',
    type: 'scatter',
    xAxisIndex: 3,
    yAxisIndex: 4,
    data: allData,
    symbol: 'circle',
    symbolSize: 6,
    z: 8,
    tooltip: {
      formatter: (params: { data?: { tooltipText?: string } }) => params.data?.tooltipText ?? '',
    },
  }]
}

function resolveDateMarkLine(date: string | undefined, xData: string[], name: string, color: string) {
  if (!date) return null
  const index = resolveNearestTradingDateIndex(date, xData)
  if (index < 0) return null
  const mappedDate = xData[index]
  const mappedSuffix = mappedDate === date ? '' : `\uff08\u6620\u5c04\u5230 ${mappedDate}\uff09`
  return {
    name,
    xAxis: mappedDate,
    lineStyle: { color, width: 1.4 },
    label: { formatter: `${name}${mappedSuffix}` },
  }
}

export function KLineChart({
  candles,
  signals = [],
  manualStartDate,
  aiBreakoutDate,
  statsRangeStartDate,
  statsRangeEndDate,
  onCandleDoubleClick,
  backtestSignals = [],
  backtestStrategies = [],
  hiddenStrategyIds = new Set(),
  onToggleStrategy,
}: KLineChartProps) {
  const xData = candles.map((item) => item.time)
  const candleData = candles.map((item) => [item.open, item.close, item.low, item.high])
  const volumes = candles.map((item) => item.volume)
  const ma5 = movingAverage(candles, 5)
  const ma10 = movingAverage(candles, 10)
  const ma20 = movingAverage(candles, 20)
  const thsIndicatorPoints = calculateThsMainRetailSeries(candles)
  const mainForceLine = thsIndicatorPoints.map((item) => Number(item.mainForce.toFixed(2)))
  const retailForceLine = thsIndicatorPoints.map((item) => Number(item.retailForce.toFixed(2)))
  const mainForceRisingSegments = [0, 1, 2].map(() => [] as number[])
  const mainForceFallingSegments = [0, 1, 2].map(() => [] as number[])
  const retailForceSegments = [0, 1, 2, 3, 4, 5, 6].map(() => [] as number[])
  const goldenCrossMarkers: IndicatorMarkerRecord[] = []
  const maxPositiveForce = thsIndicatorPoints.reduce(
    (currentMax, item) => Math.max(currentMax, item.mainForce, item.retailForce),
    0,
  )

  thsIndicatorPoints.forEach((item, index) => {
    const risingPieces = item.mainForceState === 'rising'
      ? splitPositiveSegments(item.mainForce, [0.4, 0.4, 0.2])
      : [0, 0, 0]
    const fallingPieces = item.mainForceState === 'falling'
      ? splitPositiveSegments(item.mainForce, [0.4, 0.4, 0.2])
      : [0, 0, 0]
    const retailPieces = splitNegativeSegments(item.retailForce, [0.35, 0.10, 0.10, 0.10, 0.10, 0.10, 0.15])

    risingPieces.forEach((value, index) => {
      mainForceRisingSegments[index].push(value)
    })
    fallingPieces.forEach((value, index) => {
      mainForceFallingSegments[index].push(value)
    })
    retailPieces.forEach((value, index) => {
      retailForceSegments[index].push(value)
    })

    if (item.goldenCross) {
      const crossValue = Number(item.retailForce.toFixed(2))
      const labelPosition =
        index <= 1
          ? 'right'
          : index >= thsIndicatorPoints.length - 2
            ? 'left'
            : crossValue >= maxPositiveForce * 0.82
              ? 'bottom'
              : 'top'
      goldenCrossMarkers.push({
        value: [item.time, crossValue],
        tooltipText: `金叉 | 主力=${item.mainForce.toFixed(2)} | 散户=${item.retailForce.toFixed(2)}`,
        label: {
          position: labelPosition,
          distance: 2,
        },
      })
    }
  })

  const markPointDrafts: MarkPointDraft[] = []
  const eventPointDrafts: EventPointDraft[] = []
  const stageRangeSet = new Set<string>()
  const stageRangeData: StageRangeItem[] = []
  const signalSummaryByDate = new Map<string, string[]>()
  const phaseLegendFlags = {
    accumulation: false,
    distribution: false,
    unknown: false,
    stats: false,
  }
  const unknownEventCodeSet = new Set<string>()
  const unknownPhaseNameSet = new Set<string>()

  for (const signal of signals) {
    const triggerIndex = resolveNearestTradingDateIndex(signal.trigger_date, xData)
    if (triggerIndex < 0) continue

    const mappedTriggerDate = xData[triggerIndex]
    const mappedHint = mappedTriggerDate === signal.trigger_date ? '' : ` -> ${mappedTriggerDate}`
    const phase = signal.wyckoff_phase?.trim() || '\u9636\u6bb5\u672a\u660e'
    const event = signal.wyckoff_signal?.trim() || signal.wy_events?.[signal.wy_events.length - 1] || '\u65e0\u4e8b\u4ef6'
    const normalizedEventDateMap = new Map<string, string>()
    Object.entries(signal.wy_event_dates ?? {}).forEach(([eventCode, eventDate]) => {
      const normalizedCode = normalizeEventCode(eventCode)
      const normalizedDate = typeof eventDate === 'string' ? eventDate.trim() : ''
      if (!normalizedCode || !normalizedDate) return
      normalizedEventDateMap.set(normalizedCode, normalizedDate)
    })
    const eventNodes: Array<{ eventCode: string; eventDate: string; category: EventCategory }> = []
    if (Array.isArray(signal.wy_event_chain)) {
      for (const node of signal.wy_event_chain) {
        const eventCode = typeof node?.event === 'string' ? node.event.trim() : ''
        const eventDate = typeof node?.date === 'string' ? node.date.trim() : ''
        if (!eventCode || !eventDate) continue
        const category =
          node.category === 'accumulation' || node.category === 'distributionRisk' || node.category === 'other'
            ? node.category
            : resolveEventCategory(eventCode)
        eventNodes.push({
          eventCode,
          eventDate,
          category,
        })
      }
    }
    if (eventNodes.length === 0) {
      const rawEventList = [
        ...Object.keys(signal.wy_event_dates ?? {}),
        ...(signal.wy_events ?? []),
        ...(signal.wy_risk_events ?? []),
        signal.wyckoff_signal,
      ]
        .map((item) => (item ?? '').trim())
        .filter((item) => item.length > 0)
      const fallbackKeySet = new Set<string>()
      for (const eventCode of rawEventList) {
        const normalizedCode = normalizeEventCode(eventCode)
        if (!normalizedCode) continue
        const eventDateSource = normalizedEventDateMap.get(normalizedCode) || signal.trigger_date
        const category = resolveEventCategory(eventCode)
        const dedupKey = `${normalizedCode}|${eventDateSource}|${category}`
        if (fallbackKeySet.has(dedupKey)) continue
        fallbackKeySet.add(dedupKey)
        eventNodes.push({
          eventCode,
          eventDate: eventDateSource,
          category,
        })
      }
    }

    const signalType = signal.primary_signal
    const triggerSummaryEntry = `${signalType}\u4e3b\u4fe1\u53f7 | ${phase} | \u89e6\u53d1:${signal.trigger_date}${mappedHint}`
    const existedTriggerSummary = signalSummaryByDate.get(mappedTriggerDate)
    if (existedTriggerSummary) {
      existedTriggerSummary.push(triggerSummaryEntry)
    } else {
      signalSummaryByDate.set(mappedTriggerDate, [triggerSummaryEntry])
    }

    const eventTimelineIndexes: number[] = []
    eventNodes.forEach(({ eventCode, eventDate, category }) => {
      const eventDateSource = eventDate || signal.trigger_date
      const eventIndex = resolveNearestTradingDateIndex(eventDateSource, xData)
      if (eventIndex < 0) return
      const mappedEventDate = xData[eventIndex]
      const mappedEventHint = mappedEventDate === eventDateSource ? '' : ` -> ${mappedEventDate}`
      eventTimelineIndexes.push(eventIndex)

      const normalizedEventCode = normalizeEventCode(eventCode)
      const anchor =
        CREEK_EVENT_CODES.has(normalizedEventCode)
          ? 'high'
          : ICE_EVENT_CODES.has(normalizedEventCode)
            ? 'low'
            : category === 'distributionRisk'
              ? 'high'
              : 'low'
      const anchorPrice = anchor === 'high' ? candles[eventIndex].high : candles[eventIndex].low
      const eventDisplayName = resolveEventDisplayName(eventCode)
      eventPointDrafts.push({
        dateKey: mappedEventDate,
        category,
        anchor,
        value: [mappedEventDate, anchorPrice],
        eventCode,
        tooltipText: `\u4e8b\u4ef6:${eventDisplayName} | \u9636\u6bb5:${phase} | \u4e8b\u4ef6\u65e5:${eventDateSource}${mappedEventHint}`,
        itemStyle: {
          color: resolveEventColor(category),
        },
      })
      if (category === 'other') {
        unknownEventCodeSet.add(eventCode)
      }

      const summaryEntry = `${signalType} | ${phase} | \u4e8b\u4ef6:${eventDisplayName}`
      const existedSummary = signalSummaryByDate.get(mappedEventDate)
      if (existedSummary) {
        existedSummary.push(summaryEntry)
      } else {
        signalSummaryByDate.set(mappedEventDate, [summaryEntry])
      }
    })

    markPointDrafts.push({
      dateKey: mappedTriggerDate,
      name: `${signalType}\u4e3b\u4fe1\u53f7`,
      coord: [mappedTriggerDate, candles[triggerIndex].high],
      value: signalType,
      symbol: resolveSignalShape(signalType),
      symbolSize: 20,
      tooltipText: `${signalType}\u4e3b\u4fe1\u53f7 | \u9636\u6bb5:${phase} | \u4e8b\u4ef6:${event} | \u89e6\u53d1:${signal.trigger_date}${mappedHint}`,
      itemStyle: {
        color: resolveSignalColor(signalType),
      },
    })

    if (eventNodes.length === 0) {
      const fallbackSummaryEntry = `${signalType} | ${phase} | \u4e8b\u4ef6:${event}`
      const existedSummary = signalSummaryByDate.get(mappedTriggerDate)
      if (existedSummary) {
        existedSummary.push(fallbackSummaryEntry)
      } else {
        signalSummaryByDate.set(mappedTriggerDate, [fallbackSummaryEntry])
      }
    }

    const stageTimelineIndexes = eventTimelineIndexes.length > 0 ? eventTimelineIndexes : [triggerIndex]
    const rangeStart = xData[Math.min(...stageTimelineIndexes)]
    const rangeEnd = xData[Math.max(...stageTimelineIndexes)]
    const rangeKey = `${phase}|${rangeStart}|${rangeEnd}`
    if (!stageRangeSet.has(rangeKey)) {
      stageRangeSet.add(rangeKey)
      const phaseType = resolvePhaseLegendType(phase)
      phaseLegendFlags[phaseType] = true
      if (phaseType === 'unknown') {
        unknownPhaseNameSet.add(phase)
      }
      stageRangeData.push([
        {
          name: `\u9636\u6bb5:${phase}`,
          xAxis: rangeStart,
          itemStyle: { color: resolvePhaseAreaColor(phase) },
          label: {
            show: false,
            formatter: phase,
            color: '#2f5452',
            fontSize: 11,
          },
        },
        {
          xAxis: rangeEnd,
        },
      ])
    }
  }

  if (statsRangeStartDate && statsRangeEndDate && xData.length > 0) {
    const rangeStartIndex = resolveNearestTradingDateIndex(statsRangeStartDate, xData)
    const rangeEndIndex = resolveNearestTradingDateIndex(statsRangeEndDate, xData)
    if (rangeStartIndex >= 0 && rangeEndIndex >= 0) {
      const left = Math.min(rangeStartIndex, rangeEndIndex)
      const right = Math.max(rangeStartIndex, rangeEndIndex)
      phaseLegendFlags.stats = true
      stageRangeData.push([
        {
          name: '\u7edf\u8ba1\u533a\u95f4',
          xAxis: xData[left],
          itemStyle: { color: 'rgba(250, 173, 20, 0.12)' },
          label: {
            show: false,
            formatter: '\u7edf\u8ba1\u533a\u95f4',
            color: '#8a5a00',
            fontSize: 11,
          },
        },
        {
          xAxis: xData[right],
        },
      ])
    }
  }

  function makeDateMarkPoint(
    date: string | undefined,
    label: string,
    type: 'high' | 'low',
    color: string,
  ): MarkPointDraft | null {
    if (!date) return null
    const index = resolveNearestTradingDateIndex(date, xData)
    if (index < 0) return null
    const mappedDate = xData[index]
    return {
      dateKey: mappedDate,
      name: label,
      coord: [mappedDate, type === 'high' ? candles[index].high : candles[index].low],
      value: label,
      symbol: 'pin',
      symbolSize: 14,
      tooltipText: `${label}: ${date}${mappedDate === date ? '' : ` -> ${mappedDate}`}`,
      itemStyle: { color },
    }
  }

  const manualPoint = makeDateMarkPoint(manualStartDate, '\u4eba\u5de5\u542f\u52a8\u65e5', 'low', '#13c2c2')
  if (manualPoint) {
    markPointDrafts.push(manualPoint)
  }
  const aiPoint = makeDateMarkPoint(aiBreakoutDate, 'AI\u8d77\u7206\u65e5', 'high', '#fa8c16')
  if (aiPoint) {
    markPointDrafts.push(aiPoint)
  }

  const allMarkPoints = toStackedMarkPoints(markPointDrafts)
  const eventPointsByCategory = toStackedEventPoints(eventPointDrafts)
  const unknownEventCodes = Array.from(unknownEventCodeSet).sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const unknownPhaseNames = Array.from(unknownPhaseNameSet).sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const creekLine = buildWyckoffBoundaryLine(eventPointDrafts, {
    allowedCodes: CREEK_EVENT_CODES,
    anchor: 'high',
    lineName: '小溪线',
    color: '#0f8b6f',
  })
  const iceLine = buildWyckoffBoundaryLine(eventPointDrafts, {
    allowedCodes: ICE_EVENT_CODES,
    anchor: 'low',
    lineName: '冰线',
    color: '#2458d3',
  })

  const markLineData = [
    resolveDateMarkLine(manualStartDate, xData, '\u4eba\u5de5\u542f\u52a8\u65e5', '#13c2c2'),
    resolveDateMarkLine(aiBreakoutDate, xData, 'AI\u8d77\u7206\u65e5', '#fa8c16'),
    creekLine?.data,
    iceLine?.data,
  ].filter(Boolean)
  const primaryLegendItems: LegendItem[] = [
    { label: '\u65e5K', color: '#ce5649', symbol: 'line' },
    { label: 'MA5', color: '#e88e1a', symbol: 'line' },
    { label: 'MA10', color: '#0f8b6f', symbol: 'line' },
    { label: 'MA20', color: '#3160db', symbol: 'line' },
    { label: '\u6210\u4ea4\u91cf', color: '#8ca9a7', symbol: 'bar' },
    { label: 'B\u4fe1\u53f7', color: '#eb8f34', symbol: 'triangle' },
    { label: 'A\u4fe1\u53f7', color: '#1677ff', symbol: 'diamond' },
    { label: 'C\u4fe1\u53f7', color: '#7f8c8d', symbol: 'circle' },
  ]
  const annotationLegendItems: LegendItem[] = []
  if (eventPointsByCategory.accumulation.length > 0) {
    annotationLegendItems.push({ label: '\u5438\u7b79\u4e8b\u4ef6', color: '#13c2c2', symbol: 'pill' })
  }
  if (eventPointsByCategory.distributionRisk.length > 0) {
    annotationLegendItems.push({ label: '\u6d3e\u53d1/\u98ce\u9669\u4e8b\u4ef6', color: '#f5222d', symbol: 'pill' })
  }
  if (eventPointsByCategory.other.length > 0) {
    const unknownLabel = unknownEventCodes.length > 0
      ? `\u672a\u5f52\u7c7b\u4e8b\u4ef6(${unknownEventCodes.slice(0, 4).join('/')}${unknownEventCodes.length > 4 ? '...' : ''})`
      : '\u672a\u5f52\u7c7b\u4e8b\u4ef6(\u7f3a\u5c11\u4e8b\u4ef6\u4ee3\u7801\u6620\u5c04)'
    annotationLegendItems.push({ label: unknownLabel, color: '#8c8c8c', symbol: 'pill' })
  }
  if (manualPoint) {
    annotationLegendItems.push({ label: '\u4eba\u5de5\u542f\u52a8\u65e5', color: '#13c2c2', symbol: 'pin' })
  }
  if (aiPoint) {
    annotationLegendItems.push({ label: 'AI\u8d77\u7206\u65e5', color: '#fa8c16', symbol: 'pin' })
  }
  if (phaseLegendFlags.accumulation) {
    annotationLegendItems.push({ label: '\u5438\u7b79\u9636\u6bb5\u533a\u95f4', color: 'rgba(22, 119, 255, 0.50)', symbol: 'area' })
  }
  if (phaseLegendFlags.distribution) {
    annotationLegendItems.push({ label: '\u6d3e\u53d1\u9636\u6bb5\u533a\u95f4', color: 'rgba(245, 34, 45, 0.50)', symbol: 'area' })
  }
  if (phaseLegendFlags.unknown) {
    const unknownPhaseLabel = unknownPhaseNames.length > 0
      ? `\u9636\u6bb5\u5f85\u5224\u5b9a\u533a\u95f4(${unknownPhaseNames.slice(0, 2).join('/')}${unknownPhaseNames.length > 2 ? '...' : ''})`
      : '\u9636\u6bb5\u5f85\u5224\u5b9a\u533a\u95f4(\u539f\u59cb\u9636\u6bb5\u503c\u672a\u5f52\u7c7b)'
    annotationLegendItems.push({ label: unknownPhaseLabel, color: 'rgba(120, 136, 153, 0.50)', symbol: 'area' })
  }
  if (phaseLegendFlags.stats) {
    annotationLegendItems.push({ label: '\u7edf\u8ba1\u533a\u95f4', color: 'rgba(250, 173, 20, 0.50)', symbol: 'area' })
  }
  if (creekLine) {
    annotationLegendItems.push(creekLine.legendItem)
  }
  if (iceLine) {
    annotationLegendItems.push(iceLine.legendItem)
  }
  const thsLegendItems: LegendItem[] = [
    { label: '主力上升柱', color: '#ffd666', symbol: 'bar' },
    { label: '主力回落柱', color: '#b37feb', symbol: 'bar' },
    { label: '散户柱', color: '#52c41a', symbol: 'bar' },
    { label: '主力线', color: '#f5f5f5', symbol: 'line' },
    { label: '散户线', color: '#1f9d55', symbol: 'line' },
  ]
  if (goldenCrossMarkers.length > 0) {
    thsLegendItems.push({ label: '金叉', color: '#ffec3d', symbol: 'diamond' })
  }
  const showWyckoffGuide =
    eventPointDrafts.length > 0
    || unknownEventCodes.length > 0
    || unknownPhaseNames.length > 0
    || phaseLegendFlags.accumulation
    || phaseLegendFlags.distribution
    || phaseLegendFlags.unknown

  const ratioBasePrice = Number(candles[0]?.close ?? 0)

  const backtestScatterSeries = buildBacktestScatterSeries(
    backtestSignals,
    hiddenStrategyIds,
    xData,
    backtestStrategies,
  )

  const backtestMainMarkers = buildBacktestMarkersOnMainChart(
    backtestSignals,
    hiddenStrategyIds,
    xData,
    candles,
  )

  const option = {
    animation: true,
    legend: { show: false },
    tooltip: {
      trigger: 'axis',
      formatter: (rawParams: AxisTooltipParam | AxisTooltipParam[]) => {
        const params = Array.isArray(rawParams) ? rawParams : [rawParams]
        const candleParam = params.find((item) => item.seriesName === '\u65e5K')
        const fallbackParam = params.find((item) => typeof item.dataIndex === 'number')
        const dataIndex = candleParam?.dataIndex ?? fallbackParam?.dataIndex ?? -1
        if (dataIndex < 0 || dataIndex >= candles.length) return ''

        const candle = candles[dataIndex]
        const prevClose = dataIndex > 0 ? candles[dataIndex - 1].close : candle.open
        const change = candle.close - prevClose
        const changePct = prevClose > 0 ? change / prevClose : 0
        const openPct = prevClose > 0 ? candle.open / prevClose - 1 : 0
        const closePct = prevClose > 0 ? candle.close / prevClose - 1 : 0
        const highPct = prevClose > 0 ? candle.high / prevClose - 1 : 0
        const lowPct = prevClose > 0 ? candle.low / prevClose - 1 : 0
        const amplitudePct = prevClose > 0 ? (candle.high - candle.low) / prevClose : 0
        const bodyPct = candle.open > 0 ? Math.abs(candle.close - candle.open) / candle.open : 0
        const upperShadow = candle.high - Math.max(candle.open, candle.close)
        const lowerShadow = Math.min(candle.open, candle.close) - candle.low

        const lookback = Math.min(5, dataIndex + 1)
        let volume5Avg = 0
        for (let i = dataIndex - lookback + 1; i <= dataIndex; i += 1) {
          volume5Avg += candles[i].volume
        }
        volume5Avg /= Math.max(lookback, 1)
        const volumeRatio = volume5Avg > 0 ? candle.volume / volume5Avg : 0

        const changeColor = change >= 0 ? '#ce5649' : '#1a8b66'
        const ma5Value = ma5[dataIndex]
        const ma10Value = ma10[dataIndex]
        const ma20Value = ma20[dataIndex]
        const thsIndicator = thsIndicatorPoints[dataIndex]
        const signalSummary = signalSummaryByDate.get(candle.time) ?? []

        const lines = [
          `<div style="min-width: 280px">`,
          `<div style="font-weight: 600; margin-bottom: 6px">${candle.time}</div>`,
          `<div>\u5f00\u76d8\uff1a${toPrice(candle.open)} (${toSignedPercent(openPct)})\u3000\u6536\u76d8\uff1a${toPrice(candle.close)} (${toSignedPercent(closePct)})</div>`,
          `<div>\u6700\u9ad8\uff1a${toPrice(candle.high)} (${toSignedPercent(highPct)})\u3000\u6700\u4f4e\uff1a${toPrice(candle.low)} (${toSignedPercent(lowPct)})</div>`,
          `<div>\u6da8\u8dcc\uff1a<span style="color:${changeColor};font-weight:600">${toSignedNumber(change)} (${toSignedPercent(changePct)})</span></div>`,
          `<div>\u632f\u5e45\uff1a${toSignedPercent(amplitudePct)}\u3000\u5b9e\u4f53\uff1a${toSignedPercent(bodyPct)}</div>`,
          `<div>\u4e0a\u5f71\uff1a${toPrice(upperShadow)}\u3000\u4e0b\u5f71\uff1a${toPrice(lowerShadow)}</div>`,
          `<div>\u6210\u4ea4\u91cf\uff1a${toLargeNumber(candle.volume)}\uff08\u91cf\u6bd45\u65e5\uff1a${volumeRatio.toFixed(2)}\uff09</div>`,
          `<div>\u6210\u4ea4\u989d\uff1a${toLargeNumber(candle.amount)}</div>`,
          `<div>\u5747\u7ebf\uff1aMA5 ${toMaybeNumber(ma5Value)} / MA10 ${toMaybeNumber(ma10Value)} / MA20 ${toMaybeNumber(ma20Value)}</div>`,
        ]

        if (thsIndicator) {
          const thsTags: string[] = []
          if (thsIndicator.purpleToYellow) thsTags.push('紫转黄')
          if (thsIndicator.goldenCross) thsTags.push('金叉')
          lines.push(
            `<div>\u4e3b\u6563\u91cf\u80fd\uff1a\u4e3b\u529b ${thsIndicator.mainForce.toFixed(2)} / \u6563\u6237 ${thsIndicator.retailForce.toFixed(2)} / \u72b6\u6001 ${resolveForceStateLabel(thsIndicator.mainForceState)}</div>`,
          )
          if (thsTags.length > 0) {
            lines.push(`<div>\u91cf\u80fd\u89e6\u53d1\uff1a${thsTags.join(' / ')}</div>`)
          }
        }

        if (signalSummary.length > 0) {
          lines.push(`<div style="margin-top: 4px">\u4fe1\u53f7\uff1a${signalSummary.join('\uff1b')}</div>`)
        }

        const btSummaryForDate: string[] = []
        for (const bs of backtestSignals) {
          if (hiddenStrategyIds.has(bs.strategy_id)) continue
          const btIdx = resolveNearestTradingDateIndex(bs.signal_date, xData)
          if (btIdx === dataIndex) {
            const c = strategyColorBright(bs.strategy_id)
            const name = bs.strategy_name || bs.strategy_id
            btSummaryForDate.push(
              `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${c};margin-right:3px;vertical-align:middle"></span>${name} ${bs.entry_signal}(G${bs.event_grade})`,
            )
          }
        }
        if (btSummaryForDate.length > 0) {
          lines.push(`<div style="margin-top: 4px">\u56de\u6d4b\u7b56\u7565\uff1a${btSummaryForDate.join('<br/>')}</div>`)
        }

        lines.push('</div>')
        return lines.join('')
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: [0, 1, 2, 3] }],
      label: {
        backgroundColor: '#65706f',
      },
    },
    grid: [
      { left: '6%', right: '5%', top: '8%', height: '43%' },
      { left: '6%', right: '5%', top: '56%', height: '9%' },
      { left: '6%', right: '5%', top: '69%', height: '10%' },
      { left: '6%', right: '5%', top: '83%', height: '8%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: xData,
        scale: true,
        boundaryGap: false,
        axisLine: { onZero: false },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: xData,
        scale: true,
        boundaryGap: false,
        axisLine: { onZero: false },
        axisLabel: { show: false },
      },
      {
        type: 'category',
        gridIndex: 2,
        data: xData,
        scale: true,
        boundaryGap: false,
        axisLine: { onZero: true },
        axisLabel: { show: false },
      },
      {
        type: 'category',
        gridIndex: 3,
        data: xData,
        scale: true,
        boundaryGap: false,
        axisLine: { onZero: false },
        axisLabel: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: {
          lineStyle: { color: 'rgba(31,49,48,0.14)' },
        },
      },
      {
        scale: true,
        position: 'right',
        axisLabel: {
          formatter: (value: number) => toSignedPercentFromPrice(ratioBasePrice, Number(value), 2),
        },
        splitLine: { show: false },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { show: false },
      },
      {
        scale: true,
        gridIndex: 2,
        min: (range: { min: number }) => {
          const minValue = Math.min(Number(range.min) || 0, 0)
          return minValue === 0 ? -1 : expandIndicatorAxisBound(minValue)
        },
        max: (range: { max: number }) => {
          const maxValue = Math.max(Number(range.max) || 0, 0)
          return maxValue === 0 ? 1 : expandIndicatorAxisBound(maxValue)
        },
        axisLabel: {
          formatter: (value: number) => Number(value).toFixed(0),
        },
        splitLine: {
          lineStyle: { color: 'rgba(31,49,48,0.14)' },
        },
      },
      {
        type: 'category',
        gridIndex: 3,
        data: backtestStrategies
          .filter((s) => s.signal_count > 0 && !hiddenStrategyIds.has(s.strategy_id))
          .map((s) => s.strategy_name || s.strategy_id),
        axisLabel: { fontSize: 9, color: '#5a6c6a' },
        axisTick: { show: false },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2, 3], start: 60, end: 100 },
      { show: true, xAxisIndex: [0, 1, 2, 3], type: 'slider', top: '94%', start: 60, end: 100 },
    ],
    series: [
      {
        name: '\u65e5K',
        type: 'candlestick',
        data: candleData,
        itemStyle: {
          color: '#ce5649',
          color0: '#1a8b66',
          borderColor: '#ce5649',
          borderColor0: '#1a8b66',
        },
        markPoint: {
          symbolSize: 16,
          label: {
            show: false,
          },
          tooltip: {
            formatter: (params: { data?: { tooltipText?: string }; name?: string }) =>
              params.data?.tooltipText ?? params.name ?? '',
          },
          data: allMarkPoints,
        },
        markArea: {
          silent: true,
          data: stageRangeData,
        },
        markLine: {
          symbol: ['none', 'none'],
          animation: false,
          label: {
            color: '#2f5452',
            backgroundColor: 'rgba(255,255,255,0.7)',
            padding: [2, 6],
          },
          tooltip: {
            formatter: (params: { data?: { tooltipText?: string; name?: string }; name?: string }) =>
              params.data?.tooltipText ?? params.data?.name ?? params.name ?? '',
          },
          data: markLineData,
        },
      },
      {
        name: 'MA5',
        type: 'line',
        data: ma5,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color: '#e88e1a' },
      },
      {
        name: 'MA10',
        type: 'line',
        data: ma10,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color: '#0f8b6f' },
      },
      {
        name: 'MA20',
        type: 'line',
        data: ma20,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color: '#3160db' },
      },
      {
        name: '\u6210\u4ea4\u91cf',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 2,
        data: volumes,
        itemStyle: {
          color: '#8ca9a7',
        },
      },
      {
        name: '主力上升-底',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: mainForceRisingSegments[0],
        itemStyle: { color: '#ffe58f' },
        emphasis: { disabled: true },
      },
      {
        name: '主力上升-中',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: mainForceRisingSegments[1],
        itemStyle: { color: '#ffc53d' },
        emphasis: { disabled: true },
      },
      {
        name: '主力上升-顶',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: mainForceRisingSegments[2],
        itemStyle: { color: '#ff4d4f' },
        emphasis: { disabled: true },
      },
      {
        name: '主力回落-底',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: mainForceFallingSegments[0],
        itemStyle: { color: '#f0abfc' },
        emphasis: { disabled: true },
      },
      {
        name: '主力回落-中',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: mainForceFallingSegments[1],
        itemStyle: { color: '#b37feb' },
        emphasis: { disabled: true },
      },
      {
        name: '主力回落-顶',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: mainForceFallingSegments[2],
        itemStyle: { color: '#722ed1' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-1',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[0],
        itemStyle: { color: '#355e3b' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-2',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[1],
        itemStyle: { color: '#4d7c0f' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-3',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[2],
        itemStyle: { color: '#65a30d' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-4',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[3],
        itemStyle: { color: '#84cc16' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-5',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[4],
        itemStyle: { color: '#a3e635' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-6',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[5],
        itemStyle: { color: '#bef264' },
        emphasis: { disabled: true },
      },
      {
        name: '散户-7',
        type: 'bar',
        xAxisIndex: 2,
        yAxisIndex: 3,
        stack: 'ths-force',
        barWidth: '66%',
        data: retailForceSegments[6],
        itemStyle: { color: '#d9f99d' },
        emphasis: { disabled: true },
      },
      {
        name: '主力线',
        type: 'line',
        xAxisIndex: 2,
        yAxisIndex: 3,
        data: mainForceLine,
        smooth: false,
        showSymbol: false,
        lineStyle: { width: 1.8, color: '#334155' },
        z: 5,
      },
      {
        name: '散户线',
        type: 'line',
        xAxisIndex: 2,
        yAxisIndex: 3,
        data: retailForceLine,
        smooth: false,
        showSymbol: false,
        lineStyle: { width: 1.6, color: '#1f9d55' },
        z: 5,
      },
      {
        name: '金叉',
        type: 'scatter',
        xAxisIndex: 2,
        yAxisIndex: 3,
        data: goldenCrossMarkers,
        symbol: 'circle',
        symbolSize: 1,
        clip: true,
        label: {
          show: true,
          distance: 2,
          color: '#8a6d00',
          fontSize: 10,
          formatter: () => '金叉',
        },
        labelLayout: { hideOverlap: false },
        tooltip: {
          formatter: (params: { data?: { tooltipText?: string } }) => params.data?.tooltipText ?? '金叉',
        },
        itemStyle: {
          color: 'rgba(0,0,0,0)',
          borderColor: 'rgba(0,0,0,0)',
          borderWidth: 0,
        },
        z: 6,
      },
      {
        name: '\u5438\u7b79\u4e8b\u4ef6',
        type: 'scatter',
        clip: false,
        symbol: 'roundRect',
        symbolSize: 12,
        data: eventPointsByCategory.accumulation,
        label: {
          show: true,
          fontSize: 9,
          color: '#0f5f59',
          formatter: (params: { data?: { eventCode?: string } }) => params.data?.eventCode ?? '',
        },
        labelLayout: { hideOverlap: false },
        tooltip: {
          formatter: (params: { data?: { tooltipText?: string } }) => params.data?.tooltipText ?? '',
        },
        itemStyle: { color: '#13c2c2' },
        z: 6,
      },
      {
        name: '\u98ce\u9669\u4e8b\u4ef6',
        type: 'scatter',
        clip: false,
        symbol: 'roundRect',
        symbolSize: 12,
        data: eventPointsByCategory.distributionRisk,
        label: {
          show: true,
          fontSize: 9,
          color: '#9f1239',
          formatter: (params: { data?: { eventCode?: string } }) => params.data?.eventCode ?? '',
        },
        labelLayout: { hideOverlap: false },
        tooltip: {
          formatter: (params: { data?: { tooltipText?: string } }) => params.data?.tooltipText ?? '',
        },
        itemStyle: { color: '#f5222d' },
        z: 6,
      },
      {
        name: '\u5176\u4ed6\u4e8b\u4ef6',
        type: 'scatter',
        clip: false,
        symbol: 'roundRect',
        symbolSize: 12,
        data: eventPointsByCategory.other,
        label: {
          show: true,
          fontSize: 9,
          color: '#4b5563',
          formatter: (params: { data?: { eventCode?: string } }) => params.data?.eventCode ?? '',
        },
        labelLayout: { hideOverlap: false },
        tooltip: {
          formatter: (params: { data?: { tooltipText?: string } }) => params.data?.tooltipText ?? '',
        },
        itemStyle: { color: '#8c8c8c' },
        z: 6,
      },
      {
        name: 'B\u4fe1\u53f7',
        type: 'scatter',
        data: [],
        symbol: 'triangle',
        symbolSize: 10,
        itemStyle: { color: '#eb8f34' },
      },
      {
        name: 'A\u4fe1\u53f7',
        type: 'scatter',
        data: [],
        symbol: 'diamond',
        symbolSize: 10,
        itemStyle: { color: '#1677ff' },
      },
      {
        name: 'C\u4fe1\u53f7',
        type: 'scatter',
        data: [],
        symbol: 'circle',
        symbolSize: 10,
        itemStyle: { color: '#7f8c8d' },
      },
      {
        name: '\u4eba\u5de5\u542f\u52a8\u65e5',
        type: 'scatter',
        data: [],
        symbol: 'pin',
        symbolSize: 10,
        itemStyle: { color: '#13c2c2' },
      },
      {
        name: 'AI\u8d77\u7206\u65e5',
        type: 'scatter',
        data: [],
        symbol: 'pin',
        symbolSize: 10,
        itemStyle: { color: '#fa8c16' },
      },
      {
        name: '\u5438\u7b79\u533a\u95f4',
        type: 'scatter',
        data: [],
        symbol: 'rect',
        symbolSize: 10,
        itemStyle: { color: 'rgba(22, 119, 255, 0.50)' },
      },
      {
        name: '\u6d3e\u53d1\u533a\u95f4',
        type: 'scatter',
        data: [],
        symbol: 'rect',
        symbolSize: 10,
        itemStyle: { color: 'rgba(245, 34, 45, 0.50)' },
      },
      {
        name: '\u672a\u660e\u533a\u95f4',
        type: 'scatter',
        data: [],
        symbol: 'rect',
        symbolSize: 10,
        itemStyle: { color: 'rgba(120, 136, 153, 0.50)' },
      },
      {
        name: '\u7edf\u8ba1\u533a\u95f4',
        type: 'scatter',
        data: [],
        symbol: 'rect',
        symbolSize: 10,
        itemStyle: { color: 'rgba(250, 173, 20, 0.50)' },
      },
      ...backtestMainMarkers,
      ...backtestScatterSeries,
    ],
  }

  const onEvents = onCandleDoubleClick
    ? {
        dblclick: (params: { name?: string; componentType?: string; seriesType?: string }) => {
          const clickedDate = typeof params?.name === 'string' ? params.name : ''
          if (!clickedDate) return
          const onCandleSeries = params.componentType === 'series' && params.seriesType === 'candlestick'
          const onXAxis = params.componentType === 'xAxis'
          if (!onCandleSeries && !onXAxis) return
          onCandleDoubleClick(clickedDate)
        },
      }
    : undefined

  return (
    <div className="kline-wrapper">
      <ReactECharts option={option} style={{ width: '100%', height: 760 }} onEvents={onEvents} />
      <div className="kline-legend-bar">
        {primaryLegendItems.map((item) => (
          <span
            key={`legend-primary-${item.label}`}
            className="kline-legend-chip kline-legend-chip--primary"
          >
            {renderLegendMarker(item.symbol, item.color)}
            <span>{item.label}</span>
          </span>
        ))}
        {thsLegendItems.map((item) => (
          <span
            key={`legend-ths-${item.label}`}
            className="kline-legend-chip kline-legend-chip--ths"
          >
            {renderLegendMarker(item.symbol, item.color)}
            <span>{item.label}</span>
          </span>
        ))}
        {annotationLegendItems.map((item) => (
          <span
            key={`legend-annotation-${item.label}`}
            className="kline-legend-chip kline-legend-chip--annotation"
          >
            {renderLegendMarker(item.symbol, item.color)}
            <span>{item.label}</span>
          </span>
        ))}
        {backtestStrategies.filter((s) => s.signal_count > 0).map((s) => {
          const isHidden = hiddenStrategyIds.has(s.strategy_id)
          const color = isHidden ? '#d9d9d9' : strategyColorBright(s.strategy_id)
          return (
            <span
              key={`legend-backtest-${s.strategy_id}`}
              onClick={() => onToggleStrategy?.(s.strategy_id)}
              className={clsx(
                'kline-legend-chip',
                isHidden ? 'kline-legend-chip--hidden' : 'kline-legend-chip--backtest',
              )}
            >
              {renderLegendMarker((STRATEGY_SHAPES.includes(strategyShape(s.strategy_id) as LegendSymbol) ? strategyShape(s.strategy_id) : 'diamond') as LegendSymbol, color)}
              <span>{s.strategy_name || s.strategy_id}</span>
              <span className="kline-signal-count">({s.signal_count})</span>
            </span>
          )
        })}
      </div>
      <div className="kline-info-panel">
        <div className="kline-info-panel-title">{'主力/散户量能副图说明'}</div>
        <div>{'主力 = EMA(MA(上涨量,3),3)；上涨量仅在收盘高于前一日时计入成交量。'}</div>
        <div>{'散户 = EMA(MA(下跌量,3),10)；下跌量仅在收盘低于前一日时计入成交量。'}</div>
        <div>{'紫转黄 = 昨天主力回落、今天主力回升；金叉 = 主力线自下而上穿越散户线。'}</div>
      </div>
      {showWyckoffGuide ? (
        <div className="kline-info-panel">
          <div className="kline-info-panel-title">
            {'\u5a01\u79d1\u592b\u4e8b\u4ef6\u6807\u6ce8\u6e05\u5355'}
          </div>
          <div>{'\u5438\u7b79\u4e8b\u4ef6\uff1a'}{formatEventGuideWithZh(WYCKOFF_EVENT_GUIDE.accumulation)}</div>
          <div>{'\u6d3e\u53d1/\u98ce\u9669\u4e8b\u4ef6\uff1a'}{formatEventGuideWithZh(WYCKOFF_EVENT_GUIDE.risk)}</div>
          {unknownEventCodes.length > 0 ? (
            <div>{'\u672a\u5f52\u7c7b\u4e8b\u4ef6\u4ee3\u7801\uff1a'}{unknownEventCodes.join(' / ')}</div>
          ) : null}
          {unknownPhaseNames.length > 0 ? (
            <div>{'\u672a\u5f52\u7c7b\u9636\u6bb5\u540d\u79f0\uff1a'}{unknownPhaseNames.join(' / ')}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
