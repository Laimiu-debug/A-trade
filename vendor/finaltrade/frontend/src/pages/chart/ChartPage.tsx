import { useEffect, useMemo, useState } from 'react'
import dayjs, { type Dayjs } from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Input,
  Modal,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { Controller, useForm } from 'react-hook-form'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  analyzeStockWithAI,
  getAIRecords,
  getBacktestStrategySignals,
  scanStockStrategies,
  getSignals,
  getStockAnalysis,
  getStockCandles,
  getStockIntraday,
  updateStockAnnotation,
} from '@/shared/api/endpoints'
import { IntradayChart } from '@/shared/charts/IntradayChart'
import { KLineChart, strategyColorBright } from '@/shared/charts/KLineChart'
import { PageHeader } from '@/shared/components/PageHeader'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { computeCandleRangeStats } from '@/shared/utils/candleStats'
import { buildPageTitle, useAIAssistantStore } from '@/state/aiAssistantStore'
import { useUIStore } from '@/state/uiStore'
import type { AIAnalysisRecord, BacktestStrategySignalStrategyInfo, BoardFilter, Market, SignalScanMode, StockAnnotation, StrategyId, TrendPoolStep } from '@/types/contracts'
import './ChartPage.css'

const ALLOWED_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star', 'beijing', 'st']
const ALLOWED_MARKET_FILTERS: Market[] = ['sh', 'sz', 'bj']

function parseSignalBoardFilters(searchParams: URLSearchParams): BoardFilter[] {
  const merged = searchParams
    .getAll('signal_board_filters')
    .flatMap((item) => item.split(','))
    .map((item) => item.trim())
    .filter((item) => item.length > 0 && ALLOWED_BOARD_FILTERS.includes(item as BoardFilter))
  return Array.from(new Set(merged)) as BoardFilter[]
}

function parseSignalMarketFilters(searchParams: URLSearchParams): Market[] {
  const merged = searchParams
    .getAll('signal_market_filters')
    .flatMap((item) => item.split(','))
    .map((item) => item.trim())
    .filter((item) => item.length > 0 && ALLOWED_MARKET_FILTERS.includes(item as Market))
  return Array.from(new Set(merged)) as Market[]
}

function defaultAnnotation(symbol: string): StockAnnotation {
  return {
    symbol,
    start_date: dayjs().subtract(45, 'day').format('YYYY-MM-DD'),
    stage: 'Mid',
    trend_class: 'A',
    decision: '保留',
    notes: '',
    updated_by: 'manual',
  }
}

function formatPrice(value: number) {
  return Number.isFinite(value) ? value.toFixed(2) : '--'
}

function formatSigned(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--'
  if (value > 0) return `+${value.toFixed(digits)}`
  return value.toFixed(digits)
}

function formatSignedPct(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--'
  const pct = value * 100
  if (pct > 0) return `+${pct.toFixed(digits)}%`
  return `${pct.toFixed(digits)}%`
}

function formatLarge(value: number) {
  if (!Number.isFinite(value)) return '--'
  const abs = Math.abs(value)
  if (abs >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (abs >= 10000) return `${(value / 10000).toFixed(2)}万`
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function ChartPage() {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const symbol = useParams().symbol ?? ''
  const [searchParams] = useSearchParams()
  const routeStockName = searchParams.get('signal_stock_name') ?? undefined
  const stockName = useUIStore((state) => state.stockNameMap[symbol])
  const cachedAIRecord = useUIStore((state) => state.latestAIBySymbol[symbol])
  const [intradayDate, setIntradayDate] = useState<string | null>(null)
  const [intradayChartReady, setIntradayChartReady] = useState(false)
  const [lastAIRecord, setLastAIRecord] = useState<AIAnalysisRecord | null>(null)
  const [statsRange, setStatsRange] = useState<[string | null, string | null]>([null, null])
  const [hiddenStrategyIds, setHiddenStrategyIds] = useState<Set<string>>(new Set())
  const cachedDraft = useUIStore((state) => state.annotationDrafts[symbol])
  const upsertDraft = useUIStore((state) => state.upsertDraft)
  const upsertLatestAIRecord = useUIStore((state) => state.upsertLatestAIRecord)
  const setSelectedSymbol = useUIStore((state) => state.setSelectedSymbol)
  const symbolText = symbol.toUpperCase()
  const resolvedName = stockName || routeStockName || lastAIRecord?.name || cachedAIRecord?.name
  const stockTitle = resolvedName ? `${resolvedName} (${symbolText})` : symbolText
  const setAIAssistantOpen = useAIAssistantStore((state) => state.setOpen)
  const pageAIContext = useMemo(
    () => ({
      page: 'chart' as const,
      title: buildPageTitle('chart', stockTitle),
      symbol,
      payload: {
        latest_ai_record: lastAIRecord,
      },
    }),
    [symbol, stockTitle, lastAIRecord],
  )
  useRegisterPageAIContext(pageAIContext)
  const signalModeParam = searchParams.get('signal_mode')
  const signalMode: SignalScanMode = signalModeParam === 'full_market' ? 'full_market' : 'trend_pool'
  const signalTrendStepRaw = searchParams.get('signal_trend_step') ?? searchParams.get('trend_step') ?? 'auto'
  const signalTrendStep: TrendPoolStep =
    signalTrendStepRaw === 'step1' ||
    signalTrendStepRaw === 'step2' ||
    signalTrendStepRaw === 'step3' ||
    signalTrendStepRaw === 'step4'
      ? signalTrendStepRaw
      : 'auto'
  const signalRunId = searchParams.get('signal_run_id') ?? undefined
  const signalAsOfDateRaw = (searchParams.get('signal_as_of_date') ?? searchParams.get('as_of_date') ?? '').trim()
  const signalAsOfDate = /^\d{4}-\d{2}-\d{2}$/.test(signalAsOfDateRaw) ? signalAsOfDateRaw : undefined
  const parsedWindowDays = Number(searchParams.get('signal_window_days') ?? 60)
  const signalWindowDays = Number.isFinite(parsedWindowDays) ? Math.max(20, Math.round(parsedWindowDays)) : 60
  const parsedMinScore = Number(searchParams.get('signal_min_score') ?? 60)
  const signalMinScore = Number.isFinite(parsedMinScore) ? Math.max(0, Math.min(100, parsedMinScore)) : 60
  const parsedMinEventCount = Number(searchParams.get('signal_min_event_count') ?? 1)
  const signalMinEventCount = Number.isFinite(parsedMinEventCount) ? Math.max(1, Math.round(parsedMinEventCount)) : 1
  const parsedSignalAgeMin = Number(searchParams.get('signal_age_min') ?? 0)
  const signalAgeMin = Number.isFinite(parsedSignalAgeMin) ? Math.max(0, Math.min(240, Math.round(parsedSignalAgeMin))) : 0
  const parsedSignalAgeMax = Number(searchParams.get('signal_age_max') ?? '')
  const signalAgeMax = Number.isFinite(parsedSignalAgeMax)
    ? Math.max(signalAgeMin, Math.min(240, Math.round(parsedSignalAgeMax)))
    : undefined
  const signalRequireSequence = searchParams.get('signal_require_sequence') === 'true'
  const signalMarketFilters = parseSignalMarketFilters(searchParams)
  const signalBoardFilters = parseSignalBoardFilters(searchParams)
  const signalStrategyIdRaw = (searchParams.get('signal_strategy_id') ?? searchParams.get('strategy_id') ?? '').trim()
  const signalStrategyId = signalStrategyIdRaw ? (signalStrategyIdRaw as StrategyId) : undefined
  const signalStrategyParamsRaw = (searchParams.get('signal_strategy_params') ?? searchParams.get('strategy_params') ?? '').trim()
  const signalStrategyParams = useMemo(() => {
    if (!signalStrategyParamsRaw) return undefined
    try {
      const parsed = JSON.parse(signalStrategyParamsRaw) as unknown
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined
      return parsed as Record<string, unknown>
    } catch {
      return undefined
    }
  }, [signalStrategyParamsRaw])

  const candlesQuery = useQuery({
    queryKey: ['candles', symbol],
    queryFn: () => getStockCandles(symbol),
    enabled: Boolean(symbol),
  })

  const analysisQuery = useQuery({
    queryKey: ['analysis', symbol],
    queryFn: () => getStockAnalysis(symbol),
    enabled: Boolean(symbol),
  })

  const signalsQuery = useQuery({
    queryKey: [
      'signals',
      signalMode,
      signalTrendStep,
      signalRunId ?? '',
      signalAsOfDate ?? '',
      signalWindowDays,
      signalMinScore,
      signalMinEventCount,
      signalAgeMin,
      signalAgeMax ?? -1,
      signalRequireSequence,
      signalMarketFilters.join(','),
      signalBoardFilters.join(','),
      signalStrategyId ?? '',
      JSON.stringify(signalStrategyParams ?? {}),
    ],
    queryFn: () =>
      getSignals({
        mode: signalMode,
        run_id: signalRunId,
        trend_step: signalMode === 'trend_pool' ? signalTrendStep : undefined,
        strategy_id: signalStrategyId,
        strategy_params: signalStrategyParams,
        market_filters: signalMode === 'full_market' && signalMarketFilters.length > 0 ? signalMarketFilters : undefined,
        board_filters: signalBoardFilters.length > 0 ? signalBoardFilters : undefined,
        as_of_date: signalAsOfDate,
        window_days: signalWindowDays,
        min_score: signalMinScore,
        min_event_count: signalMinEventCount,
        signal_age_min: signalAgeMin,
        signal_age_max: signalAgeMax,
        require_sequence: signalRequireSequence,
      }),
  })

  const aiRecordsQuery = useQuery({
    queryKey: ['ai-records'],
    queryFn: getAIRecords,
  })

  const intradayQuery = useQuery({
    queryKey: ['intraday', symbol, intradayDate],
    queryFn: () => getStockIntraday(symbol, intradayDate ?? ''),
    enabled: Boolean(symbol && intradayDate),
  })

  const backtestSignalsQuery = useQuery({
    queryKey: ['backtest-strategy-signals', symbol],
    queryFn: () => getBacktestStrategySignals(symbol),
    enabled: Boolean(symbol),
    staleTime: 5 * 60 * 1000,
  })

  const strategyScanQuery = useQuery({
    queryKey: ['strategy-scan', symbol],
    queryFn: () => scanStockStrategies(symbol),
    enabled: Boolean(symbol),
    staleTime: 10 * 60 * 1000,
  })

  const combinedSignals = useMemo(() => {
    const scanSignals = strategyScanQuery.data?.signals ?? []
    const btSignals = backtestSignalsQuery.data?.signals ?? []
    const all = [...scanSignals, ...btSignals]
    const dedup = new Map<string, typeof all[0]>()
    for (const sig of all) {
      const key = `${sig.strategy_id}|${sig.signal_date}`
      if (!dedup.has(key)) {
        dedup.set(key, sig)
      }
    }
    return Array.from(dedup.values())
  }, [strategyScanQuery.data?.signals, backtestSignalsQuery.data?.signals])

  const combinedStrategies = useMemo(() => {
    const scanStrats = strategyScanQuery.data?.strategies ?? []
    const btStrats = backtestSignalsQuery.data?.strategies ?? []
    const byId = new Map<string, typeof scanStrats[0]>()
    for (const s of btStrats) {
      byId.set(s.strategy_id, s)
    }
    for (const s of scanStrats) {
      const existing = byId.get(s.strategy_id)
      if (!existing) {
        byId.set(s.strategy_id, s)
      } else {
        byId.set(s.strategy_id, {
          ...existing,
          signal_count: existing.signal_count + s.signal_count,
          date_from: existing.date_from && s.date_from && existing.date_from < s.date_from ? existing.date_from : s.date_from || existing.date_from,
          date_to: existing.date_to && s.date_to && existing.date_to > s.date_to ? existing.date_to : s.date_to || existing.date_to,
        })
      }
    }
    return Array.from(byId.values()).sort((a, b) => b.signal_count - a.signal_count)
  }, [strategyScanQuery.data?.strategies, backtestSignalsQuery.data?.strategies])

  const { control, getValues, handleSubmit, reset, setValue, watch } = useForm<StockAnnotation>({
    defaultValues: defaultAnnotation(symbol),
  })
  const manualStartDate = watch('start_date')

  useEffect(() => {
    if (routeStockName) {
      setSelectedSymbol(symbol, routeStockName)
    }
  }, [routeStockName, setSelectedSymbol, symbol])

  useEffect(() => {
    if (!analysisQuery.data) return
    const serverAnnotation = analysisQuery.data.annotation
    const fallback = defaultAnnotation(symbol)
    reset(
      cachedDraft ?? {
        ...fallback,
        start_date: serverAnnotation?.start_date ?? analysisQuery.data.analysis.suggest_start_date,
        stage: serverAnnotation?.stage ?? analysisQuery.data.analysis.suggest_stage,
        trend_class: serverAnnotation?.trend_class ?? analysisQuery.data.analysis.suggest_trend_class,
        decision: serverAnnotation?.decision ?? '保留',
        notes: serverAnnotation?.notes ?? '',
      },
    )
  }, [analysisQuery.data, cachedDraft, reset, symbol])

  useEffect(() => {
    const latestFromServer = (aiRecordsQuery.data?.items ?? []).find((item) => item.symbol === symbol)
    if (latestFromServer) {
      setLastAIRecord(latestFromServer)
      upsertLatestAIRecord(latestFromServer)
      return
    }
    if (cachedAIRecord) {
      setLastAIRecord(cachedAIRecord)
    }
  }, [aiRecordsQuery.data?.items, cachedAIRecord, symbol, upsertLatestAIRecord])

  const saveMutation = useMutation({
    mutationFn: (payload: StockAnnotation) => updateStockAnnotation(symbol, payload),
    onSuccess: (_, payload) => {
      upsertDraft(payload)
      void queryClient.invalidateQueries({ queryKey: ['analysis', symbol] })
      message.success('已保存图上标注（手工优先）')
    },
  })

  const analyzeMutation = useMutation({
    mutationFn: () => analyzeStockWithAI(symbol),
    onSuccess: (record) => {
      setLastAIRecord(record)
      upsertLatestAIRecord(record)
      void queryClient.invalidateQueries({ queryKey: ['analysis', symbol] })
      void queryClient.invalidateQueries({ queryKey: ['ai-records'] })
      message.success('AI分析完成')
    },
    onError: () => {
      message.error('AI分析失败，请稍后重试')
    },
  })

  const symbolSignals = (signalsQuery.data?.items ?? []).filter(
    (item) => item.symbol.toLowerCase() === symbol.toLowerCase(),
  )
  const chartSignals = symbolSignals.length > 0
    ? symbolSignals
    : analysisQuery.data?.signal
      ? [analysisQuery.data.signal]
      : []
  const candles = candlesQuery.data?.candles ?? []
  const candlesLen = candles.length
  const intradayReferencePrice = useMemo(() => {
    const targetDate = String(intradayQuery.data?.date || intradayDate || '').trim()
    if (!targetDate || candles.length <= 0) return undefined

    const directIndex = candles.findIndex((item) => item.time === targetDate)
    if (directIndex > 0) return Number(candles[directIndex - 1].close)
    if (directIndex === 0) {
      const first = candles[0]
      return Number(first?.open || first?.close || 0) || undefined
    }

    const previous = [...candles]
      .filter((item) => item.time < targetDate)
      .sort((left, right) => left.time.localeCompare(right.time))
      .at(-1)
    if (previous) return Number(previous.close)

    return Number(candles[0]?.close || 0) || undefined
  }, [candles, intradayDate, intradayQuery.data?.date])

  useEffect(() => {
    setStatsRange([null, null])
    setHiddenStrategyIds(new Set())
  }, [symbol])

  useEffect(() => {
    if (candlesLen === 0) {
      setStatsRange([null, null])
      return
    }
    setStatsRange((prev) => {
      if (prev[0] && prev[1]) return prev
      const end = candles[candles.length - 1].time
      const start = candles[Math.max(0, candles.length - 20)].time
      return [start, end]
    })
  }, [candlesLen, symbol])

  const rangeStats = useMemo(
    () => computeCandleRangeStats(candles, statsRange[0], statsRange[1]),
    [candles, statsRange],
  )

  const rangePickerValue = useMemo<[Dayjs, Dayjs] | null>(() => {
    if (!statsRange[0] || !statsRange[1]) return null
    const start = dayjs(statsRange[0])
    const end = dayjs(statsRange[1])
    if (!start.isValid() || !end.isValid()) return null
    return [start, end]
  }, [statsRange])

  function backToPrev() {
    if (window.history.length > 1) {
      navigate(-1)
      return
    }
    navigate('/screener')
  }

  function openIntradayForDate(date: string) {
    setIntradayChartReady(false)
    setIntradayDate(date)
  }

  function applyRecentRange(days: number) {
    if (candles.length === 0) return
    const end = candles[candles.length - 1].time
    const start = candles[Math.max(0, candles.length - days)].time
    setStatsRange([start, end])
  }

  function handleToggleStrategy(strategyId: string) {
    setHiddenStrategyIds((prev) => {
      const next = new Set(prev)
      if (next.has(strategyId)) {
        next.delete(strategyId)
      } else {
        next.add(strategyId)
      }
      return next
    })
  }

  function toTrendClassFromAIBullType(value?: string): StockAnnotation['trend_class'] {
    const raw = value ?? ''
    if (raw.includes('A_B')) return 'A_B'
    if (raw.startsWith('A')) return 'A'
    if (raw.startsWith('B')) return 'B'
    return 'Unknown'
  }

  function toStageFromConclusion(value?: string): StockAnnotation['stage'] {
    if (value === '发酵中') return 'Early'
    if (value === '高潮') return 'Mid'
    if (value === '退潮') return 'Late'
    return getValues('stage')
  }

  function applyAIToManual() {
    if (!lastAIRecord) return
    const current = getValues()
    if (lastAIRecord.breakout_date) {
      setValue('start_date', lastAIRecord.breakout_date, { shouldDirty: true })
    }
    setValue('trend_class', toTrendClassFromAIBullType(lastAIRecord.trend_bull_type), { shouldDirty: true })
    setValue('stage', toStageFromConclusion(lastAIRecord.conclusion), { shouldDirty: true })
    const aiNote = `[AI ${lastAIRecord.fetched_at}] 结论=${lastAIRecord.conclusion} 题材=${lastAIRecord.theme_name ?? '--'} 原因=${(lastAIRecord.rise_reasons ?? []).join('；')}`
    const mergedNotes = current.notes?.trim() ? `${current.notes.trim()}\n${aiNote}` : aiNote
    setValue('notes', mergedNotes, { shouldDirty: true })
    message.success('已将 AI 结论回填到人工标注，请确认后点击“保存人工标注”')
  }

  return (
    <Space orientation="vertical" size={16} className="chart-page-full">
      <Button icon={<ArrowLeftOutlined />} onClick={backToPrev} className="chart-page-fit">
        返回选股池
      </Button>
      <PageHeader
        title={`K线标注 - ${stockTitle}`}
        subtitle="K线上已叠加人工启动日与AI起爆日标记；双击K线可查看对应分时图。"
        badge="手工优先"
      />

      {analysisQuery.data?.analysis.degraded ? (
        <Alert
          type="warning"
          showIcon
          title="题材分析使用降级结果"
          description={analysisQuery.data.analysis.degraded_reason}
        />
      ) : null}

      {candlesQuery.data?.degraded ? (
        <Alert
          type="info"
          showIcon
          title="存在分钟线缺失"
          description={`${candlesQuery.data.degraded_reason}，已使用近似价 price_source=approx`}
        />
      ) : null}

      <Card
        className="glass-card"
        variant="borderless"
        title={
          <Space size={8}>
            <Typography.Text strong>日线K线图</Typography.Text>
            <Tag color="processing">{stockTitle}</Tag>
          </Space>
        }
      >
        <KLineChart
          candles={candles}
          signals={chartSignals}
          manualStartDate={manualStartDate}
          aiBreakoutDate={lastAIRecord?.breakout_date}
          statsRangeStartDate={statsRange[0] ?? undefined}
          statsRangeEndDate={statsRange[1] ?? undefined}
          onCandleDoubleClick={openIntradayForDate}
          backtestSignals={combinedSignals}
          backtestStrategies={combinedStrategies}
          hiddenStrategyIds={hiddenStrategyIds}
          onToggleStrategy={handleToggleStrategy}
        />
        <Space className="chart-page-btn-bar">
          <Button
            size="small"
            disabled={!manualStartDate}
            onClick={() => {
              if (manualStartDate) {
                openIntradayForDate(manualStartDate)
              }
            }}
          >
            查看人工启动日分时
          </Button>
          <Button
            size="small"
            disabled={!lastAIRecord?.breakout_date}
            onClick={() => {
              if (lastAIRecord?.breakout_date) {
                openIntradayForDate(lastAIRecord.breakout_date)
              }
            }}
          >
            查看AI起爆日分时
          </Button>
        </Space>
        <Space orientation="vertical" size={10} className="chart-page-full" style={{ marginTop: 14 }}>
          <Space wrap>
            <Typography.Text type="secondary">区间统计</Typography.Text>
            <DatePicker.RangePicker
              value={rangePickerValue}
              onChange={(values) => {
                if (!values || !values[0] || !values[1]) {
                  setStatsRange((prev) => (prev[0] === null && prev[1] === null ? prev : [null, null]))
                  return
                }
                const nextRange: [string, string] = [
                  values[0].format('YYYY-MM-DD'),
                  values[1].format('YYYY-MM-DD'),
                ]
                setStatsRange((prev) => (prev[0] === nextRange[0] && prev[1] === nextRange[1] ? prev : nextRange))
              }}
              allowClear
            />
            <Button size="small" onClick={() => applyRecentRange(20)}>
              近20日
            </Button>
            <Button size="small" onClick={() => applyRecentRange(60)}>
              近60日
            </Button>
            <Button size="small" onClick={() => applyRecentRange(120)}>
              近120日
            </Button>
          </Space>
          {rangeStats ? (
            <Descriptions size="small" bordered column={3}>
              <Descriptions.Item label="统计区间">
                {rangeStats.startDate} ~ {rangeStats.endDate}
              </Descriptions.Item>
              <Descriptions.Item label="K线数量">{rangeStats.bars}</Descriptions.Item>
              <Descriptions.Item label="区间涨跌">
                {formatSigned(rangeStats.change)} ({formatSignedPct(rangeStats.changePct)})
              </Descriptions.Item>
              <Descriptions.Item label="起止收盘">
                {formatPrice(rangeStats.startClose)} {' -> '} {formatPrice(rangeStats.endClose)}
              </Descriptions.Item>
              <Descriptions.Item label="区间最高">
                {formatPrice(rangeStats.highest)} ({rangeStats.highestDate})
              </Descriptions.Item>
              <Descriptions.Item label="区间最低">
                {formatPrice(rangeStats.lowest)} ({rangeStats.lowestDate})
              </Descriptions.Item>
              <Descriptions.Item label="区间振幅">{formatSignedPct(rangeStats.amplitudePct)}</Descriptions.Item>
              <Descriptions.Item label="阳/阴/平">
                {rangeStats.upDays}/{rangeStats.downDays}/{rangeStats.flatDays}
              </Descriptions.Item>
              <Descriptions.Item label="阳线占比">{formatSignedPct(rangeStats.upRatio)}</Descriptions.Item>
              <Descriptions.Item label="最大回撤">
                {formatSignedPct(-rangeStats.maxDrawdownPct)} ({rangeStats.maxDrawdownDate})
              </Descriptions.Item>
              <Descriptions.Item label="最大单日涨幅">
                {formatSignedPct(rangeStats.maxDailyGainPct)} ({rangeStats.maxDailyGainDate})
              </Descriptions.Item>
              <Descriptions.Item label="最大单日跌幅">
                {formatSignedPct(rangeStats.maxDailyLossPct)} ({rangeStats.maxDailyLossDate})
              </Descriptions.Item>
              <Descriptions.Item label="成交量(合计/日均)">
                {formatLarge(rangeStats.totalVolume)} / {formatLarge(rangeStats.avgVolume)}
              </Descriptions.Item>
              <Descriptions.Item label="成交额(合计/日均)">
                {formatLarge(rangeStats.totalAmount)} / {formatLarge(rangeStats.avgAmount)}
              </Descriptions.Item>
            </Descriptions>
          ) : (
            <Typography.Text type="secondary">暂无可统计区间</Typography.Text>
          )}
        </Space>
      </Card>

      {combinedStrategies.length > 0 && (
        <Card
          className="glass-card"
          variant="borderless"
          title={<Typography.Text strong>策略信号回测统计</Typography.Text>}
        >
          <Table
            size="small"
            pagination={false}
            dataSource={combinedStrategies}
            rowKey="strategy_id"
            scroll={{ x: 700 }}
            columns={[
              {
                title: '策略',
                dataIndex: 'strategy_name',
                fixed: 'left',
                width: 130,
                render: (name: string, record: BacktestStrategySignalStrategyInfo) => {
                  const color = strategyColorBright(record.strategy_id)
                  return (
                    <Space size={4}>
                      <span className="chart-strategy-dot" style={{ background: color }} />
                      <span>{name}</span>
                    </Space>
                  )
                },
              },
              { title: '信号数', dataIndex: 'signal_count', width: 70, align: 'center' },
              {
                title: '次日胜率',
                dataIndex: 'win_rate_1d',
                width: 100,
                align: 'center',
                render: (v: number) => v > 0 ? <Progress percent={Math.round(v * 100)} size="small" strokeColor={v >= 0.7 ? '#fadb14' : v >= 0.5 ? '#ff4d4f' : '#52c41a'} /> : '-',
              },
              {
                title: '次日均收益',
                dataIndex: 'avg_return_1d',
                width: 100,
                align: 'right',
                render: (v: number) => {
                  if (!v) return '-'
                  const pct = (v * 100).toFixed(2)
                  return <span className={v >= 0 ? 'chart-return-pos' : 'chart-return-neg'}>{v >= 0 ? '+' : ''}{pct}%</span>
                },
              },
              {
                title: '3日胜率',
                dataIndex: 'win_rate_3d',
                width: 100,
                align: 'center',
                render: (v: number) => v > 0 ? <Progress percent={Math.round(v * 100)} size="small" strokeColor={v >= 0.7 ? '#fadb14' : v >= 0.5 ? '#ff4d4f' : '#52c41a'} /> : '-',
              },
              {
                title: '3日均收益',
                dataIndex: 'avg_return_3d',
                width: 100,
                align: 'right',
                render: (v: number) => {
                  if (!v) return '-'
                  const pct = (v * 100).toFixed(2)
                  return <span className={v >= 0 ? 'chart-return-pos' : 'chart-return-neg'}>{v >= 0 ? '+' : ''}{pct}%</span>
                },
              },
              {
                title: '5日胜率',
                dataIndex: 'win_rate_5d',
                width: 100,
                align: 'center',
                render: (v: number) => v > 0 ? <Progress percent={Math.round(v * 100)} size="small" strokeColor={v >= 0.7 ? '#fadb14' : v >= 0.5 ? '#ff4d4f' : '#52c41a'} /> : '-',
              },
              {
                title: '5日均收益',
                dataIndex: 'avg_return_5d',
                width: 100,
                align: 'right',
                render: (v: number) => {
                  if (!v) return '-'
                  const pct = (v * 100).toFixed(2)
                  return <span className={v >= 0 ? 'chart-return-pos' : 'chart-return-neg'}>{v >= 0 ? '+' : ''}{pct}%</span>
                },
              },
            ]}
          />
        </Card>
      )}

      <Card
        className="glass-card"
        variant="borderless"
        title={<Typography.Text strong>标注面板</Typography.Text>}
      >
        <Space orientation="vertical" size={14} className="chart-page-full">
          <Row gutter={[16, 12]}>
            <Col xs={24} md={8}>
              <Space orientation="vertical" size={4} className="chart-page-full">
                <Typography.Text type="secondary">启动日</Typography.Text>
                <Controller
                  name="start_date"
                  control={control}
                  render={({ field }) => {
                    const parsedStartDate = field.value ? dayjs(field.value) : null
                    const startDatePickerValue =
                      parsedStartDate && parsedStartDate.isValid() ? parsedStartDate : null
                    return (
                      <DatePicker
                        key={`chart-start-date-${field.value || 'empty'}`}
                        className="chart-date-picker-full"
                        defaultValue={startDatePickerValue ?? undefined}
                        onChange={(value) => {
                          const nextValue = value ? value.format('YYYY-MM-DD') : ''
                          if (nextValue !== (field.value ?? '')) {
                            field.onChange(nextValue)
                          }
                        }}
                        allowClear={false}
                      />
                    )
                  }}
                />
              </Space>
            </Col>
            <Col xs={12} md={4}>
              <Space orientation="vertical" size={4} className="chart-page-full">
                <Typography.Text type="secondary">阶段</Typography.Text>
                <Controller
                  name="stage"
                  control={control}
                  render={({ field }) => (
                    <Select
                      value={field.value}
                      onChange={field.onChange}
                      options={[
                        { value: 'Early', label: '早期 (Early)' },
                        { value: 'Mid', label: '中期 (Mid)' },
                        { value: 'Late', label: '后期 (Late)' },
                      ]}
                    />
                  )}
                />
              </Space>
            </Col>
            <Col xs={12} md={4}>
              <Space orientation="vertical" size={4} className="chart-page-full">
                <Typography.Text type="secondary">趋势类型</Typography.Text>
                <Controller
                  name="trend_class"
                  control={control}
                  render={({ field }) => (
                    <Select
                      value={field.value}
                      onChange={field.onChange}
                      options={[
                        { value: 'A', label: 'A 阶梯慢牛' },
                        { value: 'A_B', label: 'A_B 慢牛加速' },
                        { value: 'B', label: 'B 脉冲涨停' },
                        { value: 'Unknown', label: 'Unknown 未识别' },
                      ]}
                    />
                  )}
                />
              </Space>
            </Col>
            <Col xs={24} md={8}>
              <Space orientation="vertical" size={4} className="chart-page-full">
                <Typography.Text type="secondary">交易决策</Typography.Text>
                <Controller
                  name="decision"
                  control={control}
                  render={({ field }) => (
                    <Radio.Group
                      value={field.value}
                      onChange={(evt) => field.onChange(evt.target.value)}
                      options={[
                        { label: '保留', value: '保留' },
                        { label: '排除', value: '排除' },
                      ]}
                      optionType="button"
                    />
                  )}
                />
              </Space>
            </Col>
          </Row>

          <Controller
            name="notes"
            control={control}
            render={({ field }) => (
              <Input.TextArea
                value={field.value}
                onChange={field.onChange}
                rows={3}
                placeholder="记录人工判断理由"
              />
            )}
          />

          <Space>
            <Button
              loading={analyzeMutation.isPending}
              onClick={() => {
                analyzeMutation.mutate()
              }}
            >
              AI分析本股
            </Button>
            <Button onClick={() => setAIAssistantOpen(true)}>打开 AI 助手</Button>
            <Button disabled={!lastAIRecord} onClick={applyAIToManual}>
              一键应用AI到人工标注
            </Button>
            <Button
              type="primary"
              loading={saveMutation.isPending}
              onClick={handleSubmit((values) =>
                saveMutation.mutate({
                  ...values,
                  symbol,
                  updated_by: 'manual',
                }),
              )}
            >
              保存人工标注
            </Button>
            <Tag color="green">manual &gt; auto</Tag>
            <Tag color="blue">confidence {analysisQuery.data?.analysis.confidence ?? '-'}</Tag>
          </Space>
          {lastAIRecord ? (
            <Alert
              type={lastAIRecord.error_code ? 'warning' : 'success'}
              showIcon
              title={`AI结论: ${lastAIRecord.conclusion} | 置信度 ${lastAIRecord.confidence}`}
              description={
                <Space orientation="vertical" size={4}>
                  <Typography.Text>
                    起爆日期: {lastAIRecord.breakout_date || '--'} | 趋势牛: {lastAIRecord.trend_bull_type || '--'} | 题材: {lastAIRecord.theme_name || '--'}
                  </Typography.Text>
                  <Typography.Text>
                    上涨原因: {(lastAIRecord.rise_reasons ?? []).length > 0 ? (lastAIRecord.rise_reasons ?? []).join('；') : '--'}
                  </Typography.Text>
                  <Typography.Text>{lastAIRecord.summary}</Typography.Text>
                  {lastAIRecord.error_code ? (
                    <Typography.Text type="warning">
                      回退原因: {lastAIRecord.error_code}
                    </Typography.Text>
                  ) : null}
                </Space>
              }
            />
          ) : null}
        </Space>
      </Card>

      <Modal
        title={`${stockTitle} ${intradayQuery.data?.date ?? intradayDate ?? ''} 分时图`}
        open={Boolean(intradayDate)}
        onCancel={() => setIntradayDate(null)}
        afterOpenChange={(open) => {
          setIntradayChartReady(open)
        }}
        footer={null}
        width={980}
        destroyOnHidden
      >
        {intradayQuery.data?.degraded ? (
          <Alert
            type="warning"
            showIcon
            className="chart-degraded-alert"
            title="分时图包含近似数据"
            description={intradayQuery.data.degraded_reason}
          />
        ) : null}
        {intradayChartReady ? (
          <IntradayChart
            points={intradayQuery.data?.points ?? []}
            referencePrice={intradayReferencePrice}
          />
        ) : (
          <div className="chart-intraday-placeholder" />
        )}
      </Modal>
    </Space>
  )
}

