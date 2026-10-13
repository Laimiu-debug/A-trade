import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { DeleteOutlined, DownloadOutlined, HistoryOutlined, SaveOutlined, StopOutlined, SyncOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { createSignalEtfBacktest, getStrategies, saveCrossValidateHistory, listCrossValidateHistory, getCrossValidateHistory, deleteCrossValidateHistory, runCrossValidateBacktest } from '@/shared/api/endpoints'
import { PageHeader } from '@/shared/components/PageHeader'
import { usePageAIContextRegistration } from '@/shared/ai/usePageAIContextRegistration'
import { summarizeCrossValidate } from '@/shared/ai/contextPayloads'
import {
  filterVisibleStrategies,
  parseStrategyParamSchema,
  normalizeStrategyParams,
  buildStrategyParamsPayload,
} from '@/shared/utils/strategyParams'
import type {
  BoardFilter,
  CrossValidateBacktestResponse,
  CrossValidateRequest,
  CrossValidateResponse,
  CrossValidateStockDetail,
  CrossValidateStockResult,
  Market,
  SignalEtfBacktestCreateRequest,
  SignalScanMode,
  StrategyId,
  TrendPoolStep,
} from '@/types/contracts'
import { useUIStore } from '@/state/uiStore'
import { formatCrossValidateElapsed, useCrossValidateRunStore } from '@/state/crossValidateRunStore'
import './CrossValidatePage.css'

const { RangePicker } = DatePicker

const ALLOWED_MARKET_FILTERS: Market[] = ['sh', 'sz', 'bj']
const ALLOWED_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star', 'beijing', 'st']
const MARKET_FILTER_LABELS: Record<Market, string> = { sh: '沪市', sz: '深市', bj: '北交所' }
const BOARD_FILTER_LABELS: Record<BoardFilter, string> = {
  main: '主板', gem: '创业板', star: '科创板', beijing: '北交所', st: 'ST',
}
const TREND_STEP_OPTIONS = [
  { value: 'step1', label: 'Step1' },
  { value: 'step2', label: 'Step2' },
  { value: 'step3', label: 'Step3' },
  { value: 'step4', label: 'Step4' },
  { value: 'auto', label: '自动(step4>step3)' },
]

const CACHE_KEY = 'tdx-cross-validate-state-v3'
const SIGNAL_RUN_CACHE_KEY = 'tdx-signals-run-id-v1'
const SIGNAL_BOARD_FILTERS_CACHE_KEY = 'tdx-signals-board-filters-v1'
const SCREENER_CACHE_KEY = 'tdx-trend-screener-cache-v5'

function readScreenerRunId(): string {
  // 1. Direct cache written by signals page
  try {
    const direct = (window.localStorage.getItem(SIGNAL_RUN_CACHE_KEY) ?? '').trim()
    if (direct) return direct
  } catch { /* ignore */ }
  // 2. Fallback: read from screener cache
  try {
    const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return ''
    const parsed = JSON.parse(raw) as { run_meta?: { runId?: unknown } }
    const runId = typeof parsed?.run_meta?.runId === 'string' ? parsed.run_meta.runId.trim() : ''
    return runId
  } catch {
    return ''
  }
}

function readScreenerBoardFilters(): BoardFilter[] {
  // 1. Direct cache written by signals page
  try {
    const raw = window.localStorage.getItem(SIGNAL_BOARD_FILTERS_CACHE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        return parsed.filter((f: unknown) =>
          ['main', 'gem', 'star', 'beijing', 'st'].includes(String(f)),
        ) as BoardFilter[]
      }
    }
  } catch { /* ignore */ }
  // 2. Fallback: read from screener cache
  try {
    const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as { form_values?: { board_filters?: unknown } }
    const filters = parsed?.form_values?.board_filters
    if (!Array.isArray(filters)) return []
    return filters.filter((f: unknown) =>
      ['main', 'gem', 'star', 'beijing', 'st'].includes(String(f)),
    ) as BoardFilter[]
  } catch {
    return []
  }
}

interface StrategyConfigState {
  strategy_id: StrategyId
  params: Record<string, unknown>
  trend_step?: TrendPoolStep
  strategyMode?: SignalScanMode
}

interface PageState {
  asOfDate: string
  dateFrom: string
  dateTo: string
  mode: SignalScanMode
  runId: string
  windowDays: number
  minScore: number
  minEventCount: number
  minOverlap: number
  marketFilters: Market[]
  boardFilters: BoardFilter[]
  selectedStrategyIds: StrategyId[]
  strategyConfigs: Record<string, StrategyConfigState>
}

function loadCachedState(): Partial<PageState> {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY)
    // Always try to read screener run_id and board_filters as defaults
    const screenerRunId = readScreenerRunId()
    const screenerBoards = readScreenerBoardFilters()
    if (!raw) {
      return {
        runId: screenerRunId,
        boardFilters: screenerBoards.length > 0 ? screenerBoards : [],
        mode: 'full_market',
      }
    }
    const cached = JSON.parse(raw) as Partial<PageState>
    // Always override with latest screener run_id if available
    if (screenerRunId) {
      cached.runId = screenerRunId
    }
    return cached
  } catch {
    return {}
  }
}

function saveCachedState(state: PageState) {
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(state))
  } catch {
    // ignore
  }
}

export function CrossValidatePage() {
  const { message } = AntdApp.useApp()
  const navigate = useNavigate()
  const setSelectedSymbol = useUIStore((s) => s.setSelectedSymbol)

  const cached = useMemo(() => loadCachedState(), [])

  const [asOfDate, setAsOfDate] = useState<string>(cached.asOfDate ?? '')
  const [dateFrom, setDateFrom] = useState<string>(cached.dateFrom ?? '')
  const [dateTo, setDateTo] = useState<string>(cached.dateTo ?? '')
  const [mode, setMode] = useState<SignalScanMode>(cached.mode ?? 'full_market')
  const [runId, setRunId] = useState(cached.runId ?? '')
  const [windowDays, setWindowDays] = useState(cached.windowDays ?? 60)
  const [minScore, setMinScore] = useState(cached.minScore ?? 0)
  const [minEventCount, setMinEventCount] = useState(cached.minEventCount ?? 0)
  const [minOverlap, setMinOverlap] = useState(cached.minOverlap ?? 1)
  const [marketFilters, setMarketFilters] = useState<Market[]>(cached.marketFilters ?? [])
  const [boardFilters, setBoardFilters] = useState<BoardFilter[]>(cached.boardFilters ?? [])
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<StrategyId[]>(
    cached.selectedStrategyIds ?? ['wyckoff_trend_v1', 'trend_king_v1'],
  )
  const [strategyConfigs, setStrategyConfigs] = useState<Record<string, StrategyConfigState>>(
    cached.strategyConfigs ?? {},
  )
  const result = useCrossValidateRunStore((s) => s.result)
  const setResult = useCrossValidateRunStore((s) => s.setResult)
  const runStatus = useCrossValidateRunStore((s) => s.status)
  const taskProgress = useCrossValidateRunStore((s) => s.progress)
  const elapsedSeconds = useCrossValidateRunStore((s) => s.elapsedSeconds)
  const runCrossValidate = useCrossValidateRunStore((s) => s.run)
  const stopCrossValidate = useCrossValidateRunStore((s) => s.stop)
  const isRunning = runStatus === 'running'

  usePageAIContextRegistration({
    cross_validate: summarizeCrossValidate(result),
    scan_status: runStatus,
    selected_strategy_ids: selectedStrategyIds,
  })

  const [filterStrategyIds, setFilterStrategyIds] = useState<string[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [saveLabel, setSaveLabel] = useState('')
  const [backtestResult, setBacktestResult] = useState<CrossValidateBacktestResponse | null>(null)
  const [holdDays, setHoldDays] = useState(5)
  const [holdUntilToday, setHoldUntilToday] = useState(false)
  const [buyMode, setBuyMode] = useState<string>('trigger_date')
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [filterMinScore, setFilterMinScore] = useState<number>(0)
  const [filterMinOverlap, setFilterMinOverlap] = useState<number>(0)
  // History query
  const historyQuery = useQuery({
    queryKey: ['cross-validate-history'],
    queryFn: listCrossValidateHistory,
    enabled: historyOpen,
    staleTime: 0,
  })

  // Persist state
  useEffect(() => {
    saveCachedState({
      asOfDate, dateFrom, dateTo, mode, runId, windowDays, minScore, minEventCount, minOverlap,
      marketFilters, boardFilters, selectedStrategyIds, strategyConfigs,
    })
  }, [asOfDate, dateFrom, dateTo, mode, runId, windowDays, minScore, minEventCount, minOverlap,
    marketFilters, boardFilters, selectedStrategyIds, strategyConfigs])

  const syncRunIdFromScreener = () => {
    const screenerRunId = readScreenerRunId()
    if (screenerRunId) {
      setRunId(screenerRunId)
      message.success(`已同步 Run ID: ${screenerRunId.slice(0, 8)}...`)
    } else {
      message.info('选股池中没有可用的 Run ID')
    }
    const screenerBoards = readScreenerBoardFilters()
    if (screenerBoards.length > 0 && boardFilters.length === 0) {
      setBoardFilters(screenerBoards)
    }
  }

  const strategyCatalogQuery = useQuery({
    queryKey: ['strategy-catalog'],
    queryFn: getStrategies,
    staleTime: 5 * 60_000,
  })
  const strategyItems = useMemo(
    () => filterVisibleStrategies(strategyCatalogQuery.data?.items),
    [strategyCatalogQuery.data?.items],
  )

  const updateStrategySource = (sid: StrategyId, value: string) => {
    setStrategyConfigs((prev) => {
      const existing = prev[sid] ?? { strategy_id: sid, params: {} }
      if (value === 'full_market') {
        return { ...prev, [sid]: { ...existing, strategy_id: sid, strategyMode: 'full_market' as SignalScanMode, trend_step: undefined } }
      }
      return { ...prev, [sid]: { ...existing, strategy_id: sid, strategyMode: 'trend_pool' as SignalScanMode, trend_step: value as TrendPoolStep } }
    })
  }

  const getStrategyConfig = (sid: StrategyId): StrategyConfigState => {
    if (strategyConfigs[sid]) return strategyConfigs[sid]
    const desc = strategyItems.find((s) => s.strategy_id === sid)
    return {
      strategy_id: sid,
      params: normalizeStrategyParams(desc?.strategy_params_defaults),
    }
  }

  const hasDateRange = dateFrom && dateTo

  const effectiveStrategyIds = useMemo(() => {
    if (filterStrategyIds.length > 0) return filterStrategyIds
    return selectedStrategyIds
  }, [filterStrategyIds, selectedStrategyIds])

  const effectiveStrategyNames = useMemo(
    () => effectiveStrategyIds.map((sid) => strategyItems.find((s) => s.strategy_id === sid)?.name || sid),
    [effectiveStrategyIds, strategyItems],
  )

  const createSignalEtfMutation = useMutation({
    mutationFn: (payload: SignalEtfBacktestCreateRequest) => createSignalEtfBacktest(payload),
    onSuccess: (detail) => {
      message.success(`已生成交叉验证ETF回测：${detail.name}`)
      navigate(`/signals/backtest?highlight=${encodeURIComponent(detail.record_id)}`)
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : '生成ETF回测失败')
    },
  })

  const handleRun = () => {
    if (selectedStrategyIds.length < 1) {
      message.warning('请至少选择 1 个策略进行交叉验证')
      return
    }
    const strategies = selectedStrategyIds.map((sid) => {
      const config = getStrategyConfig(sid)
      const desc = strategyItems.find((s) => s.strategy_id === sid)
      const schema = parseStrategyParamSchema(desc?.strategy_params_schema)
      const defaults = normalizeStrategyParams(desc?.strategy_params_defaults)
      const payload = buildStrategyParamsPayload({ schema, params: config.params, defaults, includeDefaults: true })
      return {
        strategy_id: sid,
        strategy_params: payload,
        trend_step: config.trend_step,
        mode: config.strategyMode,
      }
    })
    setResult(null)
    const reqPayload: CrossValidateRequest = {
      as_of_date: !hasDateRange ? (asOfDate || undefined) : undefined,
      date_from: hasDateRange ? dateFrom : undefined,
      date_to: hasDateRange ? dateTo : undefined,
      strategies,
      mode,
      run_id: runId || undefined,
      trend_step: 'step1',
      window_days: windowDays,
      min_score: minScore,
      min_event_count: minEventCount,
      market_filters: marketFilters.length > 0 ? marketFilters : undefined,
      board_filters: boardFilters.length > 0 ? boardFilters : undefined,
      min_overlap: minOverlap,
    }
    void runCrossValidate(reqPayload, {
      onSuccess: (resp) => {
        setFilterStrategyIds([])
        setSelectedRowKeys([])
        setBacktestResult(null)
        setFilterMinScore(0)
        setFilterMinOverlap(0)
        if (resp.warnings && resp.warnings.length > 0) {
          resp.warnings.forEach((w) => message.warning(w, 6))
        }
        if (resp.results.length > 0) {
          message.success(`交叉验证完成: ${resp.results.length} 只股票在 ${Object.keys(resp.strategy_pools).length} 个策略中重叠`)
        } else {
          message.info('交叉验证完成，未找到同时出现在多个策略池中的股票')
        }
      },
      onError: (msg) => {
        message.error(msg)
      },
      onCancelled: () => {
        message.info('交叉验证已停止')
      },
    })
  }

  const handleStop = () => {
    void stopCrossValidate()
  }

  const handleSaveResult = async (selectedOnly = false) => {
    if (!result) return
    const stocksToSave = selectedOnly && selectedRowKeys.length > 0
      ? filteredResults.filter((r) => selectedRowKeys.includes(r.symbol))
      : filteredResults
    const dateLabel = result.date_from && result.date_to
      ? `${result.date_from} ~ ${result.date_to}`
      : result.as_of_date || dayjs().format('YYYY-MM-DD')
    const names = selectedStrategyIds.map((sid) => {
      const desc = strategyItems.find((s) => s.strategy_id === sid)
      return desc?.name || sid
    })
    const suffix = selectedOnly && selectedRowKeys.length > 0 ? ` · 精选${selectedRowKeys.length}只` : ''
    const label = saveLabel.trim() || `${names.join('+')} · ${dateLabel}${suffix}`
    const partialResponse: CrossValidateResponse = {
      ...result,
      results: stocksToSave,
      total_unique_stocks: stocksToSave.length,
    }
    try {
      await saveCrossValidateHistory({
        response: partialResponse,
        label,
        strategy_ids: selectedStrategyIds,
        strategy_names: names,
        request_params: { mode, windowDays, minScore, minEventCount, minOverlap, filtered: selectedOnly },
      })
      message.success('已保存到历史记录')
      setSaveLabel('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    }
  }

  const handleLoadHistory = async (recordId: string) => {
    try {
      const detail = await getCrossValidateHistory(recordId)
      setResult(detail.response)
      setFilterStrategyIds([])
      setHistoryOpen(false)
      message.success(`已加载: ${detail.record.label}`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    }
  }

  const handleDeleteHistory = async (recordId: string) => {
    try {
      await deleteCrossValidateHistory(recordId)
      message.success('已删除')
      historyQuery.refetch()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const filteredResults = useMemo(() => {
    if (!result) return []
    let list = result.results
    if (filterStrategyIds.length > 0) {
      list = list.filter((r) =>
        filterStrategyIds.every((sid) =>
          r.strategy_details.some((d) => d.strategy_id === sid),
        ),
      )
    }
    if (filterMinScore > 0) {
      list = list.filter((r) => r.best_score >= filterMinScore)
    }
    if (filterMinOverlap > 0) {
      list = list.filter((r) => r.overlap_count >= filterMinOverlap)
    }
    return list
  }, [result, filterStrategyIds, filterMinScore, filterMinOverlap])

  const stocksToBacktest = useMemo(() => {
    const source = selectedRowKeys.length > 0
      ? filteredResults.filter((r) => selectedRowKeys.includes(r.symbol))
      : filteredResults
    return source.map((r) => {
      const dates = r.strategy_details.flatMap((d) => d.trigger_dates.length > 0 ? d.trigger_dates : [d.trigger_date]).filter(Boolean)
      const earliest = dates.length > 0 ? dates.sort()[0] : ''
      const latest = dates.length > 0 ? dates.sort()[dates.length - 1] : ''
      return {
        symbol: r.symbol,
        name: r.name,
        trigger_date: buyMode === 'range_end'
          ? (result?.date_to || latest || earliest)
          : earliest,
      }
    })
  }, [filteredResults, selectedRowKeys, buyMode, result])

  const handleRunBacktest = async () => {
    if (stocksToBacktest.length === 0) return
    try {
      const resp = await runCrossValidateBacktest({
        stocks: stocksToBacktest.map((s) => ({ symbol: s.symbol, trigger_date: s.trigger_date })),
        hold_days: holdDays,
        hold_until_today: holdUntilToday,
        buy_mode: buyMode,
      })
      setBacktestResult(resp)
      message.success(`回测完成: ${resp.valid_count} 只股票, 胜率 ${resp.win_rate}%`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '回测失败')
    }
  }

  const getStockSignalMeta = (row: CrossValidateStockResult, strategyIds: string[]) => {
    const relevantDetails = row.strategy_details.filter((d) => strategyIds.includes(d.strategy_id))
    const dates = relevantDetails
      .flatMap((d) => (d.trigger_dates.length > 0 ? d.trigger_dates : [d.trigger_date]))
      .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d))
    const bestDetail = relevantDetails.reduce<CrossValidateStockDetail | null>(
      (best, detail) => (!best || detail.score > best.score ? detail : best),
      null,
    )
    return {
      signal_date: dates.length > 0 ? dates.sort()[0] : '',
      signal_primary: bestDetail?.primary_signal ?? '',
      signal_event: bestDetail?.wyckoff_phase ?? '',
    }
  }

  const handleCreateCrossValidateEtfBacktest = () => {
    const sourceRows = selectedRowKeys.length > 0
      ? filteredResults.filter((r) => selectedRowKeys.includes(r.symbol))
      : filteredResults
    if (sourceRows.length <= 0) {
      message.warning('当前筛选结果为空，无法生成ETF回测。')
      return
    }
    if (effectiveStrategyIds.length <= 0) {
      message.warning('请先选择要叠加的策略组合。')
      return
    }
    const normalizedRows = sourceRows
      .map((row) => {
        const meta = getStockSignalMeta(row, effectiveStrategyIds)
        return {
          symbol: row.symbol.trim().toLowerCase(),
          name: row.name?.trim() || '',
          signal_date: meta.signal_date,
          signal_primary: meta.signal_primary,
          signal_event: meta.signal_event,
        }
      })
      .filter((row) => row.symbol.length >= 8 && /^\d{4}-\d{2}-\d{2}$/.test(row.signal_date))
    if (normalizedRows.length <= 0) {
      message.warning('筛选结果缺少有效信号日期，无法生成ETF回测。')
      return
    }
    const uniqueSignalDates = Array.from(new Set(normalizedRows.map((row) => row.signal_date))).sort()
    const asOfDateValue = (result?.as_of_date ?? '').trim()
    const rangeEnd = (result?.date_to ?? '').trim()
    const signalDate = /^\d{4}-\d{2}-\d{2}$/.test(rangeEnd)
      ? rangeEnd
      : /^\d{4}-\d{2}-\d{2}$/.test(asOfDateValue)
        ? asOfDateValue
        : uniqueSignalDates[0]
    if (uniqueSignalDates.length > 1) {
      message.info(`检测到多信号日期，已按 ${signalDate} 作为回测命名日期。`)
    }
    const comboStrategyId = effectiveStrategyIds.join('+')
    const comboStrategyName = effectiveStrategyNames.join('+')
    if (filterStrategyIds.length > 0 && filterStrategyIds.length < selectedStrategyIds.length) {
      message.info(`将按当前选中的 ${effectiveStrategyIds.length} 个策略组合生成ETF回测。`)
    }
    createSignalEtfMutation.mutate({
      strategy_id: comboStrategyId,
      strategy_name: comboStrategyName,
      signal_date: signalDate,
      notes: `交叉验证叠加：${comboStrategyName}`,
      constituents: normalizedRows,
    })
  }

  const handleExport = () => {
    if (!result || filteredResults.length === 0) {
      message.warning('没有可导出的数据')
      return
    }
    const strategyIds = Object.keys(result.strategy_pools)
    const detailHeaders = strategyIds.flatMap((sid) => {
      const name = strategyItems.find((s) => s.strategy_id === sid)?.name || sid
      return [`${name}_评分`, `${name}_健康分`, `${name}_事件分`, `${name}_阶段`, `${name}_触发日`, `${name}_出现天数`, `${name}_出现日期`]
    })
    const headers = [
      '代码', '名称', '重叠策略数', '共振强度', '最高分', '平均分',
      '区间涨幅%', '最大回撤%',
      ...detailHeaders,
    ]

    const escapeCsv = (v: string) => {
      if (v.includes(',') || v.includes('"') || v.includes('\n')) {
        return `"${v.replace(/"/g, '""')}"`
      }
      return v
    }

    const rows = filteredResults.map((r) => {
      const strength = r.overlap_count * Math.max(r.total_appearance_days, 1)
      const detailCols = strategyIds.map((sid) => {
        const d = r.strategy_details.find((det) => det.strategy_id === sid)
        if (!d) return ['', '', '', '', '', '', '']
        return [
          d.score.toFixed(1),
          d.health_score.toFixed(1),
          d.event_score.toFixed(1),
          d.wyckoff_phase || '',
          d.trigger_date || '',
          String(d.appearance_days),
          (d.trigger_dates || []).join('; '),
        ]
      })
      return [
        r.symbol, r.name, String(r.overlap_count), String(strength),
        r.best_score.toFixed(1), r.avg_score.toFixed(1),
        r.range_return_pct.toFixed(2), r.range_max_drawdown_pct.toFixed(2),
        ...detailCols.flat(),
      ].map(escapeCsv).join(',')
    })

    const dateLabel = result.date_from && result.date_to
      ? `${result.date_from}_${result.date_to}`
      : result.as_of_date || dayjs().format('YYYY-MM-DD')
    const strategiesLabel = selectedStrategyIds.join('+')

    const csv = '﻿' + [headers.map(escapeCsv).join(','), ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `交叉验证_${strategiesLabel}_${dateLabel}.csv`
    a.click()
    URL.revokeObjectURL(url)
    message.success(`已导出 ${filteredResults.length} 条记录`)
  }

  const columns: ColumnsType<CrossValidateStockResult> = useMemo(
    () => [
      {
        title: '代码',
        dataIndex: 'symbol',
        width: 110,
        fixed: 'left',
        sorter: (a, b) => a.symbol.localeCompare(b.symbol),
        render: (val: string) => (
          <Button
            type="link"
            size="small"
            onClick={() => {
              setSelectedSymbol(val, undefined)
              navigate(`/stocks/${val}/chart`)
            }}
          >
            {val}
          </Button>
        ),
      },
      {
        title: '名称',
        dataIndex: 'name',
        width: 110,
        sorter: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
      },
      {
        title: '共振强度',
        key: 'resonance',
        width: 100,
        defaultSortOrder: 'descend',
        sorter: (a, b) => (a.overlap_count * Math.max(a.total_appearance_days, 1)) - (b.overlap_count * Math.max(b.total_appearance_days, 1)),
        render: (_: unknown, row: CrossValidateStockResult) => {
          const strength = row.overlap_count * Math.max(row.total_appearance_days, 1)
          const color = strength >= 8 ? 'red' : strength >= 4 ? 'orange' : 'blue'
          return <Tag color={color}>{strength}</Tag>
        },
      },
      {
        title: '策略×天数',
        key: 'strategy_days',
        width: 280,
        render: (_: unknown, row: CrossValidateStockResult) => (
          <Space size={4} wrap>
            {row.strategy_details.map((d) => (
              <Tag key={d.strategy_id} color="geekblue">
                {d.strategy_name || d.strategy_id}
                {d.appearance_days > 0 ? ` ×${d.appearance_days}d` : ''}
              </Tag>
            ))}
          </Space>
        ),
      },
      {
        title: '最高分',
        dataIndex: 'best_score',
        width: 90,
        sorter: (a, b) => a.best_score - b.best_score,
        render: (val: number) => val.toFixed(1),
      },
      {
        title: '平均分',
        dataIndex: 'avg_score',
        width: 90,
        sorter: (a, b) => a.avg_score - b.avg_score,
        render: (val: number) => val.toFixed(1),
      },
      {
        title: hasDateRange ? '区间涨幅' : '当日涨幅',
        dataIndex: 'range_return_pct',
        width: 100,
        sorter: (a, b) => a.range_return_pct - b.range_return_pct,
        render: (val: number) => {
          const cls = val > 0 ? 'cv-return-up' : val < 0 ? 'cv-return-down' : 'cv-return-flat'
          return (
            <span className={cls}>
              {val > 0 ? '+' : ''}{val.toFixed(2)}%
            </span>
          )
        },
      },
      {
        title: '最大回撤',
        dataIndex: 'range_max_drawdown_pct',
        width: 100,
        sorter: (a, b) => a.range_max_drawdown_pct - b.range_max_drawdown_pct,
        render: (val: number) => (
          <span className="cv-drawdown">
            -{val.toFixed(2)}%
          </span>
        ),
      },
      {
        title: '操作',
        key: 'actions',
        width: 100,
        fixed: 'right',
        render: (_: unknown, row: CrossValidateStockResult) => (
          <Button
            type="link"
            size="small"
            onClick={() => {
              setSelectedSymbol(row.symbol, row.name)
              navigate(`/stocks/${row.symbol}/chart`)
            }}
          >
            查看K线
          </Button>
        ),
      },
    ],
    [navigate, setSelectedSymbol, hasDateRange, result],
  )

  const expandedRowRender = (row: CrossValidateStockResult) => (
    <Table
      size="small"
      rowKey="strategy_id"
      pagination={false}
      dataSource={row.strategy_details}
      columns={[
        { title: '策略', dataIndex: 'strategy_name', width: 140 },
        {
          title: '主信号',
          dataIndex: 'primary_signal',
          width: 80,
          render: (val: string) => (
            <Tag color={val === 'B' ? 'red' : val === 'A' ? 'green' : 'orange'}>{val}</Tag>
          ),
        },
        { title: '评分', dataIndex: 'score', width: 80, render: (v: number) => v.toFixed(1) },
        { title: '健康分', dataIndex: 'health_score', width: 80, render: (v: number) => v.toFixed(1) },
        { title: '事件分', dataIndex: 'event_score', width: 80, render: (v: number) => v.toFixed(1) },
        { title: '事件等级', dataIndex: 'event_grade', width: 80 },
        { title: '阶段', dataIndex: 'wyckoff_phase', width: 100 },
        { title: '触发日', dataIndex: 'trigger_date', width: 110 },
        {
          title: '出现天数',
          dataIndex: 'appearance_days',
          width: 90,
          render: (v: number) => v > 0 ? <Tag color={v >= 3 ? 'gold' : 'blue'}>{v} 天</Tag> : '-',
        },
        {
          title: '出现日期',
          dataIndex: 'trigger_dates',
          width: 300,
          render: (dates: string[]) => {
            if (!dates || dates.length === 0) return '-'
            return (
              <Space size={2} wrap>
                {dates.map((d, i) => {
                  const isFirst = i === 0
                  const isLast = i === dates.length - 1
                  const color = isFirst ? 'green' : isLast ? 'red' : 'default'
                  return <Tag key={d} color={color} className="cv-date-tag">{d.slice(5)}</Tag>
                })}
              </Space>
            )
          },
        },
      ]}
    />
  )

  const asOfDatePickerValue = useMemo(() => {
    if (!asOfDate) return undefined
    const parsed = dayjs(asOfDate)
    return parsed.isValid() ? parsed : undefined
  }, [asOfDate])

  const rangePickerValue = useMemo(() => {
    if (!dateFrom || !dateTo) return undefined
    const from = dayjs(dateFrom)
    const to = dayjs(dateTo)
    return from.isValid() && to.isValid() ? [from, to] as [dayjs.Dayjs, dayjs.Dayjs] : undefined
  }, [dateFrom, dateTo])

  return (
    <Space orientation="vertical" size={16} className="cv-root">
      <PageHeader
        title="策略交叉验证"
        subtitle="选择多个策略运行信号扫描，找出被多策略同时选中的高置信度股票"
        badge="Cross-Validate"
      />

      <Card className="glass-card" variant="borderless">
        <Space orientation="vertical" size={12} className="cv-config-inner">
          <Typography.Text strong>选择策略（至少 1 个）</Typography.Text>
          <Checkbox.Group
            value={selectedStrategyIds}
            onChange={(values) => {
              const filtered = (values as StrategyId[]).filter(
                (id) => strategyItems.some((s) => s.strategy_id === id),
              )
              setSelectedStrategyIds(filtered)
              filtered.forEach((sid) => {
                if (!strategyConfigs[sid]) {
                  const desc = strategyItems.find((s) => s.strategy_id === sid)
                  if (desc) {
                    setStrategyConfigs((prev) => ({
                      ...prev,
                      [sid]: {
                        strategy_id: sid,
                        params: normalizeStrategyParams(desc.strategy_params_defaults),
                      },
                    }))
                  }
                }
              })
            }}
          >
            <Row gutter={[8, 8]}>
              {strategyItems.map((item) => (
                <Col key={item.strategy_id}>
                  <Checkbox value={item.strategy_id}>
                    {item.name}
                  </Checkbox>
                </Col>
              ))}
            </Row>
          </Checkbox.Group>

          {selectedStrategyIds.length > 0 && (
            <Card size="small" title="每个策略的信号来源（可单独设置）">
              <Row gutter={[12, 8]}>
                {selectedStrategyIds.map((sid) => {
                  const desc = strategyItems.find((s) => s.strategy_id === sid)
                  const config = getStrategyConfig(sid)
                  const effectiveMode = config.strategyMode ?? mode
                  const sourceValue = effectiveMode === 'full_market' ? 'full_market' : (config.trend_step ?? 'step1')
                  return (
                    <Col key={sid} xs={24} sm={12} md={8}>
                      <Space size={4}>
                        <Typography.Text type="secondary">{desc?.name || sid}</Typography.Text>
                        <Select
                          size="small"
                          value={sourceValue}
                          onChange={(v) => updateStrategySource(sid, v)}
                          className="cv-source-select"
                          options={[
                            { value: 'full_market', label: '全市场扫描' },
                            ...TREND_STEP_OPTIONS,
                          ]}
                        />
                      </Space>
                    </Col>
                  )
                })}
              </Row>
            </Card>
          )}

          <Row gutter={[12, 12]}>
            <Col xs={24} md={12} lg={8}>
              <Typography.Text type="secondary">扫描模式</Typography.Text>
              <div>
                <Radio.Group
                  value={mode}
                  onChange={(e) => setMode(e.target.value as SignalScanMode)}
                  optionType="button"
                  options={[
                    { label: '全市场扫描', value: 'full_market' },
                    { label: '趋势池后置', value: 'trend_pool' },
                  ]}
                />
              </div>
            </Col>
            <Col xs={24} md={12} lg={8}>
              <Typography.Text type="secondary">时间区间（留空则使用单日快照，建议不超过10个交易日）</Typography.Text>
              <RangePicker
                allowClear
                format="YYYY-MM-DD"
                value={rangePickerValue}
                style={{ width: '100%' }}
                onChange={(vals) => {
                  if (vals && vals[0] && vals[1]) {
                    setDateFrom(vals[0].format('YYYY-MM-DD'))
                    setDateTo(vals[1].format('YYYY-MM-DD'))
                  } else {
                    setDateFrom('')
                    setDateTo('')
                  }
                }}
              />
              {hasDateRange && (
                <Typography.Text type="warning" style={{ fontSize: 12 }}>
                  日期范围扫描每增加一个交易日需要额外约1分钟/策略
                </Typography.Text>
              )}
            </Col>
            {!hasDateRange && (
              <Col xs={24} md={6} lg={4}>
                <Typography.Text type="secondary">单日快照日期</Typography.Text>
                <DatePicker
                  allowClear
                  format="YYYY-MM-DD"
                  value={asOfDatePickerValue}
                  style={{ width: '100%' }}
                  onChange={(val) => setAsOfDate(val ? val.format('YYYY-MM-DD') : '')}
                />
              </Col>
            )}
          </Row>

          <Row gutter={[12, 12]}>
            <Col xs={12} md={6} lg={3}>
              <Typography.Text type="secondary">窗口(天)</Typography.Text>
              <InputNumber
                min={20}
                max={240}
                value={windowDays}
                style={{ width: '100%' }}
                onChange={(v) => { if (typeof v === 'number' && Number.isFinite(v)) setWindowDays(Math.round(v)) }}
              />
            </Col>
            <Col xs={12} md={6} lg={3}>
              <Typography.Text type="secondary">最低评分</Typography.Text>
              <InputNumber
                min={0}
                max={100}
                value={minScore}
                style={{ width: '100%' }}
                onChange={(v) => { if (typeof v === 'number' && Number.isFinite(v)) setMinScore(v) }}
              />
            </Col>
            <Col xs={12} md={6} lg={3}>
              <Typography.Text type="secondary">最少事件数</Typography.Text>
              <InputNumber
                min={0}
                max={12}
                value={minEventCount}
                style={{ width: '100%' }}
                onChange={(v) => { if (typeof v === 'number' && Number.isFinite(v)) setMinEventCount(Math.round(v)) }}
              />
            </Col>
            <Col xs={12} md={6} lg={3}>
              <Typography.Text type="secondary">最少重叠数</Typography.Text>
              <InputNumber
                min={1}
                max={10}
                value={minOverlap}
                style={{ width: '100%' }}
                onChange={(v) => { if (typeof v === 'number' && Number.isFinite(v)) setMinOverlap(Math.round(v)) }}
              />
            </Col>
          </Row>

          {mode === 'trend_pool' && (
            <Row gutter={[12, 12]}>
              <Col xs={24} md={12}>
                <Typography.Text type="secondary">Run ID</Typography.Text>
                <Space.Compact style={{ width: '100%' }}>
                  <Input
                    value={runId}
                    placeholder="绑定 run_id"
                    style={{ flex: 1 }}
                    onChange={(e) => setRunId(e.target.value.trim())}
                  />
                  <Tooltip title="从选股池同步 Run ID">
                    <Button icon={<SyncOutlined />} onClick={syncRunIdFromScreener}>
                      同步
                    </Button>
                  </Tooltip>
                </Space.Compact>
              </Col>
            </Row>
          )}

          <Row gutter={[12, 12]}>
            <Col xs={24} md={12}>
              <Typography.Text type="secondary">市场过滤</Typography.Text>
              <div>
                <Checkbox.Group
                  value={marketFilters}
                  onChange={(v) => setMarketFilters(v as Market[])}
                  options={ALLOWED_MARKET_FILTERS.map((m) => ({ label: MARKET_FILTER_LABELS[m], value: m }))}
                />
              </div>
            </Col>
            <Col xs={24} md={12}>
              <Typography.Text type="secondary">板块过滤</Typography.Text>
              <div>
                <Checkbox.Group
                  value={boardFilters}
                  onChange={(v) => setBoardFilters(v as BoardFilter[])}
                  options={ALLOWED_BOARD_FILTERS.map((b) => ({ label: BOARD_FILTER_LABELS[b], value: b }))}
                />
              </div>
            </Col>
          </Row>

          <Row gutter={[12, 12]} align="middle">
            {!isRunning ? (
              <>
                <Col>
                  <Button
                    type="primary"
                    icon={<ThunderboltOutlined />}
                    size="large"
                    disabled={selectedStrategyIds.length < 1}
                    onClick={handleRun}
                  >
                    开始交叉验证 ({selectedStrategyIds.length} 策略)
                    {hasDateRange ? ` · ${dateFrom} ~ ${dateTo}` : asOfDate ? ` · ${asOfDate}` : ''}
                  </Button>
                </Col>
                <Col>
                  <Button
                    icon={<HistoryOutlined />}
                    size="large"
                    onClick={() => setHistoryOpen(true)}
                  >
                    历史记录
                  </Button>
                </Col>
              </>
            ) : (
              <Col>
                <Button
                  danger
                  icon={<StopOutlined />}
                  size="large"
                  onClick={handleStop}
                >
                  停止
                </Button>
              </Col>
            )}
          </Row>
        </Space>
      </Card>

      {isRunning && (
        <Card className="glass-card" variant="borderless" size="small">
          <Space orientation="vertical" style={{ width: '100%' }} size={8}>
            <Space wrap>
              <SyncOutlined spin />
              <Typography.Text strong>正在执行交叉验证...</Typography.Text>
              <Typography.Text type="secondary">
                已用时 {formatCrossValidateElapsed(elapsedSeconds)}
              </Typography.Text>
              {taskProgress && taskProgress.total_scans > 0 && (
                <Typography.Text type="secondary">
                  进度 {taskProgress.completed_scans}/{taskProgress.total_scans}
                </Typography.Text>
              )}
            </Space>
            {taskProgress?.message && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {taskProgress.message}
              </Typography.Text>
            )}
            <Progress
              percent={Math.min(100, Math.max(0, taskProgress?.percent ?? 0))}
              status="active"
              strokeColor={{ from: '#1677ff', to: '#69b1ff' }}
            />
          </Space>
        </Card>
      )}

      {result && (
        <>
          {result.warnings && result.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="交叉验证警告"
              description={
                <ul className="cv-warning-list">
                  {result.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              }
            />
          )}
          <Card className="glass-card" variant="borderless" size="small">
            <Space size={16} wrap>
              <Typography.Text type="secondary">
                {result.date_from && result.date_to
                  ? `测试区间: ${result.date_from} ~ ${result.date_to}`
                  : `快照日期: ${result.as_of_date || '最新'}`}
                {` · 窗口 ${windowDays} 天`}
              </Typography.Text>
              <Typography.Text type="secondary">
                模式: {mode === 'full_market' ? '全市场' : '趋势池'}
              </Typography.Text>
              <Typography.Text type="secondary">
                最少重叠: {minOverlap}
              </Typography.Text>
            </Space>
          </Card>

          <Row gutter={[12, 12]}>
            <Col xs={12} sm={8} md={6} lg={4}>
              <Card className="glass-card" variant="borderless">
                <Statistic title="重叠股票数" value={result.results.length} />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6} lg={4}>
              <Card className="glass-card" variant="borderless">
                <Statistic title="去重股票总数" value={result.total_unique_stocks} />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6} lg={4}>
              <Card className="glass-card" variant="borderless">
                <Statistic title="耗时(秒)" value={result.elapsed_sec} precision={1} />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6} lg={4}>
              <Card
                className={`glass-card ${filterStrategyIds.length === 0 ? 'cv-pool-card-all-active' : 'cv-pool-card-all'}`}
                variant="borderless"
                onClick={() => setFilterStrategyIds([])}
              >
                <Statistic title="全部显示" value={filteredResults.length} suffix="只" />
              </Card>
            </Col>
            {Object.entries(result.strategy_pools).map(([sid, size]) => {
              const desc = strategyItems.find((s) => s.strategy_id === sid)
              const active = filterStrategyIds.includes(sid)
              return (
                <Col key={sid} xs={12} sm={8} md={6} lg={4}>
                  <Card
                    className={`glass-card ${active ? 'cv-pool-card-active' : 'cv-pool-card'}`}
                    variant="borderless"
                    onClick={() => {
                      setFilterStrategyIds((prev) =>
                        prev.includes(sid) ? prev.filter((x) => x !== sid) : [...prev, sid],
                      )
                    }}
                  >
                    <Statistic
                      title={desc?.name || sid}
                      value={active ? filteredResults.length : size}
                      suffix="只"
                    />
                  </Card>
                </Col>
              )
            })}
          </Row>

          <Card className="glass-card" variant="borderless" size="small">
            <Space size={12} wrap>
              <Typography.Text strong>二次筛选:</Typography.Text>
              <Space size={4}>
                <Typography.Text type="secondary">最低评分</Typography.Text>
                <InputNumber size="small" min={0} max={100} value={filterMinScore} style={{ width: 70 }} onChange={(v) => setFilterMinScore(typeof v === 'number' ? v : 0)} />
              </Space>
              <Space size={4}>
                <Typography.Text type="secondary">最少重叠</Typography.Text>
                <InputNumber size="small" min={0} max={10} value={filterMinOverlap} style={{ width: 70 }} onChange={(v) => setFilterMinOverlap(typeof v === 'number' ? v : 0)} />
              </Space>
              <Typography.Text type="secondary">
                {filteredResults.length} 只{selectedRowKeys.length > 0 ? ` · 已选 ${selectedRowKeys.length} 只` : ''}
              </Typography.Text>
            </Space>
          </Card>

          <Card className="glass-card" variant="borderless" size="small">
            <div className="cv-backtest-bar">
              <Space size={12} wrap>
                <Typography.Text strong>回测:</Typography.Text>
                <Radio.Group
                  size="small"
                  value={buyMode}
                  onChange={(e) => setBuyMode(e.target.value)}
                  optionType="button"
                  options={[
                    { label: '触发日买入', value: 'trigger_date' },
                    { label: '区间末尾买入', value: 'range_end' },
                  ]}
                />
                <Space size={4}>
                  <Typography.Text type="secondary">持有天数</Typography.Text>
                  <Select
                    size="small"
                    value={holdUntilToday ? 'until_today' : holdDays}
                    style={{ width: 108 }}
                    onChange={(value) => {
                      if (value === 'until_today') {
                        setHoldUntilToday(true)
                        return
                      }
                      setHoldUntilToday(false)
                      setHoldDays(Number(value))
                    }}
                    options={[
                      { label: '3 天', value: 3 },
                      { label: '5 天', value: 5 },
                      { label: '10 天', value: 10 },
                      { label: '20 天', value: 20 },
                      { label: '60 天', value: 60 },
                      { label: '持有到今日', value: 'until_today' },
                    ]}
                  />
                </Space>
                <Button
                  type="primary"
                  size="small"
                  onClick={handleRunBacktest}
                  disabled={filteredResults.length === 0}
                >
                  运行回测 ({selectedRowKeys.length > 0 ? `${selectedRowKeys.length} 只` : `${filteredResults.length} 只`})
                </Button>
                <Button
                  size="small"
                  loading={createSignalEtfMutation.isPending}
                  disabled={filteredResults.length === 0 || effectiveStrategyIds.length <= 0}
                  onClick={handleCreateCrossValidateEtfBacktest}
                >
                  生成ETF回测 ({effectiveStrategyIds.length}策略 · {selectedRowKeys.length > 0 ? `${selectedRowKeys.length}只` : `${filteredResults.length}只`})
                </Button>
              </Space>
            </div>
            {backtestResult && (
              <Row gutter={[12, 12]} className="cv-backtest-stats">
                <Col xs={8} sm={4}>
                  <Statistic title="有效股票" value={backtestResult.valid_count} suffix={`/ ${backtestResult.total_count}`} />
                </Col>
                <Col xs={8} sm={4}>
                  <Statistic title="胜率" value={backtestResult.win_rate} suffix="%" valueStyle={{ color: backtestResult.win_rate >= 50 ? '#3f8600' : '#cf1322' }} />
                </Col>
                <Col xs={8} sm={4}>
                  <Statistic title="平均收益" value={backtestResult.avg_return_pct} precision={2} suffix="%" valueStyle={{ color: backtestResult.avg_return_pct >= 0 ? '#cf1322' : '#3f8600' }} />
                </Col>
                <Col xs={8} sm={4}>
                  <Statistic title="中位收益" value={backtestResult.median_return_pct} precision={2} suffix="%" valueStyle={{ color: backtestResult.median_return_pct >= 0 ? '#cf1322' : '#3f8600' }} />
                </Col>
                <Col xs={8} sm={4}>
                  <Statistic title="最大收益" value={backtestResult.max_return_pct} precision={2} suffix="%" valueStyle={{ color: '#cf1322' }} />
                </Col>
                <Col xs={8} sm={4}>
                  <Statistic title="最大亏损" value={backtestResult.min_return_pct} precision={2} suffix="%" valueStyle={{ color: '#3f8600' }} />
                </Col>
              </Row>
            )}
          </Card>

          <Card className="glass-card" variant="borderless">
            <div className="cv-export-bar">
              <Space>
                <Input
                  placeholder="备注（可选）"
                  size="small"
                  value={saveLabel}
                  onChange={(e) => setSaveLabel(e.target.value)}
                  className="cv-save-input"
                  onPressEnter={() => handleSaveResult(false)}
                />
                <Button
                  icon={<SaveOutlined />}
                  type="primary"
                  ghost
                  onClick={() => handleSaveResult(false)}
                >
                  保存全部 ({filteredResults.length})
                </Button>
                {selectedRowKeys.length > 0 && (
                  <Button
                    icon={<SaveOutlined />}
                    onClick={() => handleSaveResult(true)}
                  >
                    保存精选 ({selectedRowKeys.length})
                  </Button>
                )}
                <Button
                  icon={<DownloadOutlined />}
                  onClick={handleExport}
                  disabled={filteredResults.length === 0}
                >
                  导出 CSV
                </Button>
              </Space>
            </div>
            <Table
              rowKey="symbol"
              dataSource={filteredResults}
              columns={columns}
              scroll={{ x: 900 }}
              expandable={{ expandedRowRender }}
              rowSelection={{
                selectedRowKeys,
                onChange: (keys) => setSelectedRowKeys(keys as string[]),
              }}
              pagination={{ pageSize: 50, showTotal: (total) => `共 ${total} 条` }}
              locale={{
                emptyText: <Empty description="没有同时出现在多个策略中的股票" />,
              }}
            />
          </Card>
        </>
      )}

      <Drawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        title="交叉验证历史记录"
        width={640}
      >
        {historyQuery.isLoading ? (
          <div className="cv-history-loading">
            <SyncOutlined spin className="cv-history-spin" />
          </div>
        ) : !historyQuery.data || historyQuery.data.length === 0 ? (
          <Empty description="暂无保存的记录" />
        ) : (
          <div className="cv-history-list">
            {historyQuery.data.map((rec) => (
              <Card
                key={rec.id}
                size="small"
                className="cv-history-card"
                variant="borderless"
              >
                <div className="cv-history-header">
                  <div className="cv-history-title">{rec.label}</div>
                  <Space size={4}>
                    <Button
                      type="link"
                      size="small"
                      onClick={() => handleLoadHistory(rec.id)}
                    >
                      加载
                    </Button>
                    <Popconfirm
                      title="确定删除此记录？"
                      onConfirm={() => handleDeleteHistory(rec.id)}
                      okText="删除"
                      cancelText="取消"
                    >
                      <Button type="link" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                </div>
                <div className="cv-history-meta">
                  <Space size={8} wrap>
                    <Typography.Text type="secondary">
                      {dayjs(rec.created_at).format('YYYY-MM-DD HH:mm')}
                    </Typography.Text>
                    <Tag>{rec.mode === 'full_market' ? '全市场' : '趋势池'}</Tag>
                    <Tag color="blue">{rec.result_count} 只股票</Tag>
                    <Typography.Text type="secondary">{rec.date_label}</Typography.Text>
                  </Space>
                  <div className="cv-history-strategies">
                    {rec.strategy_names.map((name, i) => (
                      <Tag key={i} color="geekblue">{name}</Tag>
                    ))}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </Drawer>
    </Space>
  )
}
