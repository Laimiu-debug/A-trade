import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import dayjs from 'dayjs'
import * as XLSX from 'xlsx'
import ReactECharts from 'echarts-for-react'
import {
  App as AntdApp,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Slider,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { DeleteOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  deleteSignalEtfBacktest,
  getSignalEtfBacktest,
  listSignalEtfBacktests,
  updateSignalEtfBacktest,
} from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/client'
import { PageHeader } from '@/shared/components/PageHeader'
import type {
  SignalEtfBacktestConstituentDetail,
  SignalEtfBacktestRecord,
} from '@/types/contracts'

type ConstituentTableRow = SignalEtfBacktestConstituentDetail & {
  contribution_t1?: number
  contribution_t2?: number
  contribution_score: number
}

function toPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${(value * 100).toFixed(2)}%`
}

function toSignedPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  const pct = value * 100
  if (pct > 0) return `+${pct.toFixed(2)}%`
  return `${pct.toFixed(2)}%`
}

function toPrice(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '--'
  return value.toFixed(2)
}

function toDateValue(value: string | null | undefined) {
  const text = String(value || '').trim()
  const parsed = dayjs(text)
  if (!parsed.isValid()) return 0
  return parsed.valueOf()
}

function toSignedColor(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  if (value > 0) return '#cf1322'
  if (value < 0) return '#389e0d'
  return '#475467'
}

function buildFilters(values: string[]) {
  const unique = Array.from(new Set(values.map((item) => item.trim()).filter((item) => item.length > 0)))
  return unique
    .sort((a, b) => a.localeCompare(b, 'zh-CN'))
    .map((item) => ({ text: item, value: item }))
}

function toSingleDateLabel(values: Array<string | null | undefined>) {
  const unique = Array.from(
    new Set(values.map((item) => String(item || '').trim()).filter((item) => item.length > 0)),
  )
  if (unique.length === 1) return unique[0]
  if (unique.length > 1) return '多日'
  return '--'
}

function buildTwoLineTitle(title: string, subtitle: string) {
  return (
    <div style={{ lineHeight: 1.15 }}>
      <div>{title}</div>
      <div style={{ marginTop: 2, fontSize: 11, color: 'rgba(0, 0, 0, 0.45)' }}>{subtitle}</div>
    </div>
  )
}

type SignalsBacktestExportFormat = 'csv' | 'xlsx'
type SignalsBacktestPageState = {
  version: 1
  selected_record_id: string | null
  valuation_date: string | null
  list_page: number
  selected_record_ids: string[]
  constituent_page: number
  detail_holding_period_days: number
}

const SIGNALS_BACKTEST_LIST_PAGE_SIZE_KEY = 'tdx-signals-backtest-list-page-size-v1'
const SIGNALS_BACKTEST_CONSTITUENT_PAGE_SIZE_KEY = 'tdx-signals-backtest-constituent-page-size-v1'
const SIGNALS_BACKTEST_LIST_HOLDING_DAYS_KEY = 'tdx-signals-backtest-list-holding-days-v1'
const SIGNALS_BACKTEST_PAGE_STATE_KEY = 'tdx-signals-backtest-page-state-v1'

function loadPersistedPageSize(storageKey: string) {
  try {
    const raw = Number(window.localStorage.getItem(storageKey) ?? '')
    if (raw === 20 || raw === 50 || raw === 100) return raw
  } catch {
    // ignore localStorage failures
  }
  return 20
}

function loadPersistedHoldingDays(storageKey: string) {
  try {
    const raw = Number(window.localStorage.getItem(storageKey) ?? '')
    if (Number.isFinite(raw)) {
      const normalized = Math.round(raw)
      if (normalized >= 1 && normalized <= 120) return normalized
    }
  } catch {
    // ignore localStorage failures
  }
  return 5
}

function createDefaultSignalsBacktestPageState(): SignalsBacktestPageState {
  return {
    version: 1,
    selected_record_id: null,
    valuation_date: null,
    list_page: 1,
    selected_record_ids: [],
    constituent_page: 1,
    detail_holding_period_days: 5,
  }
}

function loadSignalsBacktestPageState(): SignalsBacktestPageState {
  const fallback = createDefaultSignalsBacktestPageState()
  try {
    const raw = window.localStorage.getItem(SIGNALS_BACKTEST_PAGE_STATE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<SignalsBacktestPageState>
    return {
      version: 1,
      selected_record_id:
        typeof parsed.selected_record_id === 'string' && parsed.selected_record_id.trim().length > 0
          ? parsed.selected_record_id.trim()
          : null,
      valuation_date:
        typeof parsed.valuation_date === 'string' && parsed.valuation_date.trim().length > 0
          ? parsed.valuation_date.trim()
          : null,
      list_page:
        Number.isFinite(parsed.list_page) && Number(parsed.list_page) > 0
          ? Math.max(1, Math.round(Number(parsed.list_page)))
          : fallback.list_page,
      selected_record_ids: Array.isArray(parsed.selected_record_ids)
        ? parsed.selected_record_ids.map((item) => String(item).trim()).filter((item) => item.length > 0)
        : fallback.selected_record_ids,
      constituent_page:
        Number.isFinite(parsed.constituent_page) && Number(parsed.constituent_page) > 0
          ? Math.max(1, Math.round(Number(parsed.constituent_page)))
          : fallback.constituent_page,
      detail_holding_period_days:
        Number.isFinite(parsed.detail_holding_period_days) && Number(parsed.detail_holding_period_days) >= 1
          ? Math.max(1, Math.min(120, Math.round(Number(parsed.detail_holding_period_days))))
          : fallback.detail_holding_period_days,
    }
  } catch {
    return fallback
  }
}

function saveSignalsBacktestPageState(state: SignalsBacktestPageState) {
  try {
    window.localStorage.setItem(SIGNALS_BACKTEST_PAGE_STATE_KEY, JSON.stringify(state))
  } catch {
    // ignore localStorage failures
  }
}

function isSignalEtfNotFoundError(error: unknown) {
  if (!(error instanceof ApiError)) return false
  return error.code === 'SIGNAL_ETF_NOT_FOUND' || error.code === 'HTTP_404'
}

function toPercentNumber(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return Number((value * 100).toFixed(2))
}

function sanitizeFileNameSegment(value: string | null | undefined) {
  const text = String(value || '').trim().replace(/[\\/:*?"<>|]+/g, '-')
  return text.length > 0 ? text : '未命名'
}

function formatExportTimestamp() {
  return dayjs().format('YYYYMMDD-HHmmss')
}

function csvEscape(value: unknown) {
  const text = String(value ?? '')
  if (!/[",\n]/.test(text)) return text
  return `"${text.replace(/"/g, '""')}"`
}

function downloadBlob(content: BlobPart, type: string, fileName: string) {
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

export function SignalsBacktestPage() {
  const { message } = AntdApp.useApp()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const initialPageState = useMemo(() => loadSignalsBacktestPageState(), [])
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(initialPageState.selected_record_id)
  const [editingRecord, setEditingRecord] = useState<SignalEtfBacktestRecord | null>(null)
  const [valuationDate, setValuationDate] = useState<string | null>(initialPageState.valuation_date)
  const [valuationDateOptions, setValuationDateOptions] = useState<string[]>([])
  const [listPage, setListPage] = useState(initialPageState.list_page)
  const [listPageSize, setListPageSize] = useState(() => loadPersistedPageSize(SIGNALS_BACKTEST_LIST_PAGE_SIZE_KEY))
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>(initialPageState.selected_record_ids)
  const [constituentPage, setConstituentPage] = useState(initialPageState.constituent_page)
  const [constituentPageSize, setConstituentPageSize] = useState(
    () => loadPersistedPageSize(SIGNALS_BACKTEST_CONSTITUENT_PAGE_SIZE_KEY),
  )
  const [listHoldingPeriodDays, setListHoldingPeriodDays] = useState(() =>
    loadPersistedHoldingDays(SIGNALS_BACKTEST_LIST_HOLDING_DAYS_KEY),
  )
  const [detailHoldingPeriodDays, setDetailHoldingPeriodDays] = useState(initialPageState.detail_holding_period_days)
  const [listExportingFormat, setListExportingFormat] = useState<SignalsBacktestExportFormat | null>(null)
  const [detailExporting, setDetailExporting] = useState(false)
  const [editForm] = Form.useForm<{ name: string; notes: string }>()
  const highlightedRecordId = (searchParams.get('highlight') ?? '').trim()
  const previousSelectedRecordIdRef = useRef<string | null>(initialPageState.selected_record_id)

  const listQuery = useQuery({
    queryKey: ['signal-etf-backtests', listHoldingPeriodDays],
    queryFn: () => listSignalEtfBacktests({ refresh: true, holdingDays: listHoldingPeriodDays }),
    refetchInterval: 60_000,
    staleTime: 15_000,
  })
  const rows = listQuery.data?.items ?? []
  const selectedRecordExists = useMemo(
    () => (selectedRecordId ? rows.some((item) => item.record_id === selectedRecordId) : false),
    [rows, selectedRecordId],
  )

  const detailQuery = useQuery({
    queryKey: ['signal-etf-backtest-detail', selectedRecordId, valuationDate ?? 'latest', detailHoldingPeriodDays],
    queryFn: () =>
      getSignalEtfBacktest(selectedRecordId as string, {
        refresh: true,
        asOfDate: valuationDate ?? undefined,
        holdingDays: detailHoldingPeriodDays,
      }),
    enabled: Boolean(selectedRecordId) && selectedRecordExists,
    staleTime: 0,
    retry: (failureCount, error) => {
      if (isSignalEtfNotFoundError(error)) return false
      return failureCount < 1
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ recordId, name, notes }: { recordId: string; name: string; notes: string }) =>
      updateSignalEtfBacktest(recordId, { name, notes }),
    onSuccess: (row) => {
      message.success(`已更新：${row.name}`)
      setEditingRecord(null)
      void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtests'] })
      void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtest-detail', row.record_id] })
    },
    onError: (error) => {
      const text = error instanceof Error ? error.message : '更新失败'
      message.error(text)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (recordId: string) => deleteSignalEtfBacktest(recordId),
    onSuccess: (resp) => {
      message.success(`已删除：${resp.record_id}`)
      if (selectedRecordId === resp.record_id) {
        setSelectedRecordId(null)
      }
      void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtests'] })
      void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtest-detail', resp.record_id] })
    },
    onError: (error) => {
      const text = error instanceof Error ? error.message : '删除失败'
      message.error(text)
    },
  })

  const batchDeleteMutation = useMutation({
    mutationFn: async (recordIds: string[]) => {
      const tasks = recordIds.map(async (recordId) => {
        try {
          const resp = await deleteSignalEtfBacktest(recordId)
          return { ok: true as const, recordId: resp.record_id || recordId }
        } catch (error) {
          const text = error instanceof Error ? error.message : '删除失败'
          return { ok: false as const, recordId, message: text }
        }
      })
      const results = await Promise.all(tasks)
      const successIds = results.filter((item) => item.ok).map((item) => item.recordId)
      const failed = results.filter((item) => !item.ok)
      return { successIds, failed }
    },
    onSuccess: (result) => {
      const { successIds, failed } = result
      if (successIds.length > 0) {
        message.success(`批量删除成功 ${successIds.length} 条`)
      }
      if (failed.length > 0) {
        const hint = failed
          .slice(0, 2)
          .map((item) => `${item.recordId}(${item.message})`)
          .join('；')
        message.error(`批量删除失败 ${failed.length} 条${hint ? `：${hint}` : ''}`)
      }
      if (selectedRecordId && successIds.includes(selectedRecordId)) {
        setSelectedRecordId(null)
      }
      setSelectedRecordIds((prev) => prev.filter((recordId) => !successIds.includes(recordId)))
      void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtests'] })
    },
  })

  const strategyFilters = useMemo(() => buildFilters(rows.map((row) => row.strategy_name || row.strategy_id)), [rows])
  const signalDateFilters = useMemo(() => buildFilters(rows.map((row) => row.signal_date)), [rows])
  const detail = detailQuery.data
  const constituents = detail?.constituents ?? []
  const rowsForExport = useMemo(() => {
    if (selectedRecordIds.length <= 0) return rows
    const selectedIds = new Set(selectedRecordIds)
    return rows.filter((row) => selectedIds.has(row.record_id))
  }, [rows, selectedRecordIds])

  useEffect(() => {
    if (!selectedRecordId) return
    if (listQuery.isLoading || listQuery.isFetching) return
    if (selectedRecordExists) return
    message.warning('该回测记录已不存在，已关闭详情弹窗。')
    setSelectedRecordId(null)
  }, [listQuery.isFetching, listQuery.isLoading, message, selectedRecordExists, selectedRecordId])

  useEffect(() => {
    if (previousSelectedRecordIdRef.current === selectedRecordId) return
    previousSelectedRecordIdRef.current = selectedRecordId
    setValuationDate(null)
    setValuationDateOptions([])
    setConstituentPage(1)
  }, [selectedRecordId])

  useEffect(() => {
    const validIds = new Set(rows.map((row) => row.record_id))
    setSelectedRecordIds((prev) => {
      const next = prev.filter((recordId) => validIds.has(recordId))
      return next.length === prev.length ? prev : next
    })
  }, [rows])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(rows.length / listPageSize))
    if (listPage > maxPage) {
      setListPage(maxPage)
    }
  }, [listPage, listPageSize, rows.length])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNALS_BACKTEST_LIST_PAGE_SIZE_KEY, String(listPageSize))
    } catch {
      // ignore localStorage failures
    }
  }, [listPageSize])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNALS_BACKTEST_LIST_HOLDING_DAYS_KEY, String(listHoldingPeriodDays))
    } catch {
      // ignore localStorage failures
    }
  }, [listHoldingPeriodDays])

  useEffect(() => {
    saveSignalsBacktestPageState({
      version: 1,
      selected_record_id: selectedRecordId,
      valuation_date: valuationDate,
      list_page: listPage,
      selected_record_ids: selectedRecordIds,
      constituent_page: constituentPage,
      detail_holding_period_days: detailHoldingPeriodDays,
    })
  }, [
    constituentPage,
    detailHoldingPeriodDays,
    listPage,
    selectedRecordId,
    selectedRecordIds,
    valuationDate,
  ])

  useEffect(() => {
    if (!detail) return
    const merged = Array.from(
      new Set(
        [
          detail.signal_date,
          ...detail.curve.map((item) => item.date),
          ...detail.constituents.map((item) => String(item.current_date || '').trim()),
        ]
          .map((item) => String(item || '').trim())
          .filter((item) => item.length > 0),
      ),
    ).sort((a, b) => a.localeCompare(b, 'zh-CN'))
    setValuationDateOptions(merged.length > 0 ? merged : [detail.signal_date])
  }, [detail])

  useEffect(() => {
    if (!valuationDate) return
    if (valuationDateOptions.length <= 0) return
    if (valuationDateOptions.includes(valuationDate)) return
    setValuationDate(null)
  }, [valuationDate, valuationDateOptions])

  const activeValuationDate = useMemo(() => {
    if (valuationDate) return valuationDate
    if (valuationDateOptions.length > 0) return valuationDateOptions[valuationDateOptions.length - 1]
    return detail?.signal_date ?? ''
  }, [detail?.signal_date, valuationDate, valuationDateOptions])

  const valuationSliderIndex = useMemo(() => {
    if (valuationDateOptions.length <= 0) return 0
    const idx = valuationDateOptions.indexOf(activeValuationDate)
    return idx >= 0 ? idx : valuationDateOptions.length - 1
  }, [activeValuationDate, valuationDateOptions])

  const holdingDays = useMemo(() => {
    if (!detail?.signal_date || !activeValuationDate) return '--'
    const diff = dayjs(activeValuationDate).diff(dayjs(detail.signal_date), 'day')
    return String(Math.max(0, diff))
  }, [activeValuationDate, detail?.signal_date])

  const timelineStartDate = useMemo(() => {
    if (valuationDateOptions.length > 0) return valuationDateOptions[0]
    return detail?.signal_date ?? '--'
  }, [detail?.signal_date, valuationDateOptions])

  const timelineEndDate = useMemo(() => {
    if (valuationDateOptions.length > 0) return valuationDateOptions[valuationDateOptions.length - 1]
    return detail?.signal_date ?? '--'
  }, [detail?.signal_date, valuationDateOptions])

  const handleValuationDateChange = (value: number) => {
    if (valuationDateOptions.length <= 0) return
    const rawIndex = Number(value)
    if (!Number.isFinite(rawIndex)) return
    const safeIndex = Math.max(0, Math.min(valuationDateOptions.length - 1, Math.round(rawIndex)))
    const picked = valuationDateOptions[safeIndex]
    const latest = valuationDateOptions[valuationDateOptions.length - 1]
    setValuationDate(picked && picked !== latest ? picked : null)
  }

  const buyDateT1Label = useMemo(
    () => toSingleDateLabel(constituents.map((row) => row.buy_date_t1)),
    [constituents],
  )
  const buyDateT2Label = useMemo(
    () => toSingleDateLabel(constituents.map((row) => row.buy_date_t2)),
    [constituents],
  )
  const currentDateLabel = useMemo(
    () => toSingleDateLabel(constituents.map((row) => row.current_date)),
    [constituents],
  )
  const holdingTargetDateLabel = useMemo(
    () => toSingleDateLabel(constituents.map((row) => row.holding_target_date)),
    [constituents],
  )

  const constituentRows = useMemo<ConstituentTableRow[]>(() => {
    const tradableCountT1 = detail?.summary.t1.tradable_count ?? 0
    const tradableCountT2 = detail?.summary.t2.tradable_count ?? 0
    return constituents.map((row) => {
      const contributionT1 =
        row.status_t1 === 'bought' && typeof row.return_pct_t1 === 'number' && tradableCountT1 > 0
          ? row.return_pct_t1 / tradableCountT1
          : undefined
      const contributionT2 =
        row.status_t2 === 'bought' && typeof row.return_pct_t2 === 'number' && tradableCountT2 > 0
          ? row.return_pct_t2 / tradableCountT2
          : undefined
      const contributionScore =
        typeof contributionT1 === 'number' && Number.isFinite(contributionT1)
          ? contributionT1
          : typeof contributionT2 === 'number' && Number.isFinite(contributionT2)
            ? contributionT2
            : Number.NEGATIVE_INFINITY
      return {
        ...row,
        contribution_t1: contributionT1,
        contribution_t2: contributionT2,
        contribution_score: contributionScore,
      }
    })
  }, [constituents, detail?.summary.t1.tradable_count, detail?.summary.t2.tradable_count])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(constituentRows.length / constituentPageSize))
    if (constituentPage > maxPage) {
      setConstituentPage(maxPage)
    }
  }, [constituentPage, constituentPageSize, constituentRows.length])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIGNALS_BACKTEST_CONSTITUENT_PAGE_SIZE_KEY, String(constituentPageSize))
    } catch {
      // ignore localStorage failures
    }
  }, [constituentPageSize])

  const chartOption = useMemo(() => {
    const curve = detail?.curve ?? []
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params]
          if (rows.length <= 0) return ''
          const first = rows[0] as { axisValueLabel?: string; axisValue?: string }
          const title = first?.axisValueLabel || first?.axisValue || ''
          const body = rows.map((row) => {
            const item = row as {
              marker?: string
              seriesName?: string
              value?: unknown
            }
            const rawValue = Array.isArray(item.value) ? item.value[item.value.length - 1] : item.value
            const num = Number(rawValue)
            const valueText = Number.isFinite(num)
              ? `${num > 0 ? '+' : ''}${(num * 100).toFixed(2)}%`
              : '--'
            return `${item.marker ?? ''}${item.seriesName ?? ''} <span style="float:right;margin-left:16px;font-weight:600;">${valueText}</span>`
          })
          return [title, ...body].join('<br/>')
        },
      },
      legend: {
        top: 8,
        data: ['ETF T+1', 'ETF T+2', '基准 T+1', '基准 T+2'],
      },
      grid: {
        left: 48,
        right: 20,
        top: 44,
        bottom: 34,
      },
      xAxis: {
        type: 'category',
        data: curve.map((item) => item.date),
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(1)}%`,
        },
      },
      series: [
        {
          name: 'ETF T+1',
          type: 'line',
          smooth: true,
          connectNulls: true,
          data: curve.map((item) => (typeof item.etf_return_t1 === 'number' ? item.etf_return_t1 : null)),
        },
        {
          name: 'ETF T+2',
          type: 'line',
          smooth: true,
          connectNulls: true,
          data: curve.map((item) => (typeof item.etf_return_t2 === 'number' ? item.etf_return_t2 : null)),
        },
        {
          name: '基准 T+1',
          type: 'line',
          smooth: true,
          connectNulls: true,
          lineStyle: { type: 'dashed' },
          data: curve.map((item) => (typeof item.benchmark_return_t1 === 'number' ? item.benchmark_return_t1 : null)),
        },
        {
          name: '基准 T+2',
          type: 'line',
          smooth: true,
          connectNulls: true,
          lineStyle: { type: 'dashed' },
          data: curve.map((item) => (typeof item.benchmark_return_t2 === 'number' ? item.benchmark_return_t2 : null)),
        },
      ],
    }
  }, [detail?.curve])

  const columns = useMemo<ColumnsType<SignalEtfBacktestRecord>>(
    () => [
      {
        title: '名称',
        dataIndex: 'name',
        width: 190,
        ellipsis: true,
        sorter: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
        render: (_, row) => {
          const highlighted = row.record_id === highlightedRecordId
          return (
            <Space size={6}>
              <Typography.Text strong={highlighted}>{row.name}</Typography.Text>
              {highlighted ? <Tag color="blue">新建</Tag> : null}
            </Space>
          )
        },
      },
      {
        title: '信号日',
        dataIndex: 'signal_date',
        width: 110,
        filters: signalDateFilters,
        onFilter: (value, row) => row.signal_date === String(value),
        sorter: (a, b) => toDateValue(a.signal_date) - toDateValue(b.signal_date),
      },
      {
        title: '策略',
        key: 'strategy',
        width: 136,
        ellipsis: true,
        filters: strategyFilters,
        onFilter: (value, row) => (row.strategy_name || row.strategy_id) === String(value),
        sorter: (a, b) => (a.strategy_name || a.strategy_id).localeCompare((b.strategy_name || b.strategy_id), 'zh-CN'),
        render: (_, row) => row.strategy_name || row.strategy_id,
      },
      {
        title: '股票数',
        dataIndex: 'total_constituents',
        width: 80,
        sorter: (a, b) => a.total_constituents - b.total_constituents,
      },
      {
        title: 'T+1收益',
        key: 't1_return',
        width: 96,
        sorter: (a, b) => a.summary.t1.return_pct - b.summary.t1.return_pct,
        render: (_, row) => (
          <Typography.Text style={{ color: toSignedColor(row.summary.t1.return_pct) }}>
            {toSignedPercent(row.summary.t1.return_pct)}
          </Typography.Text>
        ),
      },
      {
        title: 'T+2收益',
        key: 't2_return',
        width: 96,
        sorter: (a, b) => a.summary.t2.return_pct - b.summary.t2.return_pct,
        render: (_, row) => (
          <Typography.Text style={{ color: toSignedColor(row.summary.t2.return_pct) }}>
            {toSignedPercent(row.summary.t2.return_pct)}
          </Typography.Text>
        ),
      },
      {
        title: 'T+1 ETF内胜率',
        key: 't1_stock_win',
        width: 118,
        sorter: (a, b) => a.summary.t1.stock_win_rate - b.summary.t1.stock_win_rate,
        render: (_, row) => toPercent(row.summary.t1.stock_win_rate),
      },
      {
        title: 'T+2 ETF内胜率',
        key: 't2_stock_win',
        width: 118,
        sorter: (a, b) => a.summary.t2.stock_win_rate - b.summary.t2.stock_win_rate,
        render: (_, row) => toPercent(row.summary.t2.stock_win_rate),
      },
      {
        title: '基准收益(T+1/T+2)',
        key: 'benchmark',
        width: 152,
        sorter: (a, b) => a.summary.t1.benchmark_return_pct - b.summary.t1.benchmark_return_pct,
        render: (_, row) => `${toSignedPercent(row.summary.t1.benchmark_return_pct)} / ${toSignedPercent(row.summary.t2.benchmark_return_pct)}`,
      },
      {
        title: '超额(T+1/T+2)',
        key: 'excess',
        width: 136,
        sorter: (a, b) => a.summary.t1.excess_return_pct - b.summary.t1.excess_return_pct,
        render: (_, row) => `${toSignedPercent(row.summary.t1.excess_return_pct)} / ${toSignedPercent(row.summary.t2.excess_return_pct)}`,
      },
      {
        title: (
          <div onClick={(event) => event.stopPropagation()}>
            <Space size={4} align="center" wrap={false}>
              <Typography.Text style={{ fontSize: 12 }}>持仓</Typography.Text>
              <InputNumber
                value={listHoldingPeriodDays}
                min={1}
                max={120}
                size="small"
                style={{ width: 64 }}
                onChange={(value: number | null) => {
                  if (typeof value === 'number' && Number.isFinite(value)) {
                    const normalized = Math.max(1, Math.min(120, Math.round(value)))
                    setListHoldingPeriodDays(normalized)
                  }
                }}
              />
              <Typography.Text style={{ fontSize: 12 }}>天收益</Typography.Text>
            </Space>
          </div>
        ),
        key: 'holding_return',
        width: 156,
        sorter: (a, b) => Number(a.summary.holding_return_pct ?? -99) - Number(b.summary.holding_return_pct ?? -99),
        render: (_, row) => (
          <Typography.Text style={{ color: toSignedColor(row.summary.holding_return_pct) }}>
            {toSignedPercent(row.summary.holding_return_pct)}
          </Typography.Text>
        ),
      },
      {
        title: '操作',
        key: 'actions',
        width: 120,
        fixed: 'right',
        render: (_, row) => (
          <Space size={2}>
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={(event) => {
                event.stopPropagation()
                editForm.setFieldsValue({ name: row.name, notes: row.notes || '' })
                setEditingRecord(row)
              }}
            >
              编辑
            </Button>
            <Popconfirm
              title="删除该ETF记录？"
              description="删除后不可恢复。"
              onConfirm={(event) => {
                event?.stopPropagation()
                deleteMutation.mutate(row.record_id)
              }}
              onPopupClick={(event) => event.stopPropagation()}
            >
              <Button
                danger
                type="link"
                size="small"
                icon={<DeleteOutlined />}
                onClick={(event) => event.stopPropagation()}
                loading={deleteMutation.isPending && deleteMutation.variables === row.record_id}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    [
      deleteMutation.isPending,
      editForm,
      highlightedRecordId,
      listHoldingPeriodDays,
      signalDateFilters,
      strategyFilters,
    ],
  )

  const constituentColumns = useMemo<ColumnsType<ConstituentTableRow>>(
    () => [
      {
        title: '代码',
        dataIndex: 'symbol',
        width: 90,
        sorter: (a, b) => a.symbol.localeCompare(b.symbol, 'zh-CN'),
      },
      {
        title: '名称',
        dataIndex: 'name',
        width: 116,
        sorter: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
        render: (_, row) => (
          <Button
            type="link"
            size="small"
            onClick={() => {
              const params = new URLSearchParams({
                signal_stock_name: row.name || '',
                signal_as_of_date: row.signal_date,
                signal_age_min: '0',
              })
              navigate(`/stocks/${row.symbol}/chart?${params.toString()}`)
            }}
          >
            {row.name || row.symbol}
          </Button>
        ),
      },
      {
        title: '信号事件',
        dataIndex: 'signal_event',
        width: 90,
        sorter: (a, b) => a.signal_event.localeCompare(b.signal_event, 'zh-CN'),
        render: (value: string) => value || '-',
      },
      {
        title: '信号信息',
        dataIndex: 'signal_reason',
        width: 180,
        ellipsis: true,
        render: (value: string) => value || '-',
      },
      {
        title: buildTwoLineTitle('T+1买入', buyDateT1Label),
        key: 'buy_t1',
        width: 96,
        sorter: (a, b) => Number(a.buy_price_t1 ?? 0) - Number(b.buy_price_t1 ?? 0),
        render: (_, row) => (row.status_t1 === 'bought' ? toPrice(row.buy_price_t1) : <Tag>跳过</Tag>),
      },
      {
        title: buildTwoLineTitle('T+2买入', buyDateT2Label),
        key: 'buy_t2',
        width: 96,
        sorter: (a, b) => Number(a.buy_price_t2 ?? 0) - Number(b.buy_price_t2 ?? 0),
        render: (_, row) => (row.status_t2 === 'bought' ? toPrice(row.buy_price_t2) : <Tag>跳过</Tag>),
      },
      {
        title: buildTwoLineTitle('当前价', currentDateLabel),
        key: 'current_price',
        width: 92,
        sorter: (a, b) => Number(a.current_price ?? 0) - Number(b.current_price ?? 0),
        render: (_, row) => toPrice(row.current_price),
      },
      {
        title: 'T+1收益',
        key: 'return_t1',
        width: 90,
        sorter: (a, b) => Number(a.return_pct_t1 ?? -99) - Number(b.return_pct_t1 ?? -99),
        render: (_, row) => (
          <Typography.Text style={{ color: toSignedColor(row.return_pct_t1) }}>
            {toSignedPercent(row.return_pct_t1)}
          </Typography.Text>
        ),
      },
      {
        title: 'T+2收益',
        key: 'return_t2',
        width: 90,
        sorter: (a, b) => Number(a.return_pct_t2 ?? -99) - Number(b.return_pct_t2 ?? -99),
        render: (_, row) => (
          <Typography.Text style={{ color: toSignedColor(row.return_pct_t2) }}>
            {toSignedPercent(row.return_pct_t2)}
          </Typography.Text>
        ),
      },
      {
        title: (
          <div onClick={(event) => event.stopPropagation()}>
            <Space size={4} align="center" wrap={false}>
              <Typography.Text style={{ fontSize: 12 }}>持仓</Typography.Text>
              <InputNumber
                value={detailHoldingPeriodDays}
                min={1}
                max={120}
                size="small"
                style={{ width: 68 }}
                onChange={(value: number | null) => {
                  if (typeof value === 'number' && Number.isFinite(value)) {
                    const normalized = Math.max(1, Math.min(120, Math.round(value)))
                    setDetailHoldingPeriodDays(normalized)
                  }
                }}
              />
              <Typography.Text style={{ fontSize: 12 }}>天收益</Typography.Text>
            </Space>
            <div style={{ marginTop: 2, fontSize: 11, color: 'rgba(0, 0, 0, 0.45)' }}>
              {holdingTargetDateLabel}
            </div>
          </div>
        ),
        key: 'return_holding',
        width: 156,
        sorter: (a, b) => Number(a.return_pct_holding ?? -99) - Number(b.return_pct_holding ?? -99),
        render: (_, row) => (
          <Typography.Text style={{ color: toSignedColor(row.return_pct_holding) }}>
            {toSignedPercent(row.return_pct_holding)}
          </Typography.Text>
        ),
      },
      {
        title: buildTwoLineTitle('收益率贡献', 'T+1 / T+2'),
        key: 'contribution',
        width: 132,
        sorter: (a, b) => Number(a.contribution_score) - Number(b.contribution_score),
        render: (_, row) => (
          <Space size={4}>
            <Typography.Text style={{ color: toSignedColor(row.contribution_t1) }}>
              {toSignedPercent(row.contribution_t1)}
            </Typography.Text>
            <Typography.Text type="secondary">/</Typography.Text>
            <Typography.Text style={{ color: toSignedColor(row.contribution_t2) }}>
              {toSignedPercent(row.contribution_t2)}
            </Typography.Text>
          </Space>
        ),
      },
    ],
    [
      buyDateT1Label,
      buyDateT2Label,
      currentDateLabel,
      detailHoldingPeriodDays,
      holdingTargetDateLabel,
      navigate,
    ],
  )

  const handleExportList = async (format: SignalsBacktestExportFormat) => {
    if (rowsForExport.length <= 0) {
      message.info('暂无可导出的待买回测记录')
      return
    }
    setListExportingFormat(format)
    try {
      const exportRows = rowsForExport.map((row) => ({
        记录ID: row.record_id,
        名称: row.name,
        信号日: row.signal_date,
        策略名称: row.strategy_name || row.strategy_id,
        策略ID: row.strategy_id,
        基准代码: row.benchmark_symbol,
        股票数: row.total_constituents,
        持仓天数: row.summary.holding_period_days ?? listHoldingPeriodDays,
        持仓目标日: row.summary.holding_target_date ?? '',
        持仓收益率_pct: toPercentNumber(row.summary.holding_return_pct),
        T1收益率_pct: toPercentNumber(row.summary.t1.return_pct),
        T2收益率_pct: toPercentNumber(row.summary.t2.return_pct),
        T1基准收益率_pct: toPercentNumber(row.summary.t1.benchmark_return_pct),
        T2基准收益率_pct: toPercentNumber(row.summary.t2.benchmark_return_pct),
        T1超额收益率_pct: toPercentNumber(row.summary.t1.excess_return_pct),
        T2超额收益率_pct: toPercentNumber(row.summary.t2.excess_return_pct),
        T1股票胜率_pct: toPercentNumber(row.summary.t1.stock_win_rate),
        T2股票胜率_pct: toPercentNumber(row.summary.t2.stock_win_rate),
        T1日胜率_pct: toPercentNumber(row.summary.t1.daily_win_rate),
        T2日胜率_pct: toPercentNumber(row.summary.t2.daily_win_rate),
        T1可交易数: row.summary.t1.tradable_count,
        T2可交易数: row.summary.t2.tradable_count,
        T1跳过数: row.summary.t1.skipped_count,
        T2跳过数: row.summary.t2.skipped_count,
        策略样本数: row.summary.strategy_stats.total_records,
        策略胜率T1_pct: toPercentNumber(row.summary.strategy_stats.win_rate_t1),
        策略胜率T2_pct: toPercentNumber(row.summary.strategy_stats.win_rate_t2),
        备注: row.notes || '',
        创建时间: row.created_at,
        更新时间: row.updated_at,
      }))
      const fileBaseName = `待买回测概览-${selectedRecordIds.length > 0 ? `已选${rowsForExport.length}条` : `全部${rowsForExport.length}条`}-${formatExportTimestamp()}`
      if (format === 'csv') {
        const header = Object.keys(exportRows[0] ?? {})
        const lines = exportRows.map((row) =>
          header.map((key) => csvEscape((row as Record<string, unknown>)[key])).join(','),
        )
        const csv = `\uFEFF${[header.join(','), ...lines].join('\n')}`
        downloadBlob(csv, 'text/csv;charset=utf-8;', `${fileBaseName}.csv`)
      } else {
        const worksheet = XLSX.utils.json_to_sheet(exportRows)
        const workbook = XLSX.utils.book_new()
        XLSX.utils.book_append_sheet(workbook, worksheet, '概览')
        XLSX.writeFile(workbook, `${fileBaseName}.xlsx`)
      }
      message.success(`已导出${rowsForExport.length}条待买回测${format === 'csv' ? 'CSV' : 'Excel'}概览`)
    } finally {
      setListExportingFormat(null)
    }
  }

  const handleExportDetailExcel = async () => {
    if (!detail) {
      message.info('请先打开一条待买回测详情再导出')
      return
    }
    setDetailExporting(true)
    try {
      const summarySheetRows = [
        {
          记录ID: detail.record_id,
          名称: detail.name,
          信号日: detail.signal_date,
          策略名称: detail.strategy_name || detail.strategy_id,
          策略ID: detail.strategy_id,
          基准代码: detail.benchmark_symbol,
          基准可用: detail.benchmark_available ? '是' : '否',
          成分股数量: detail.total_constituents,
          当前估值日: activeValuationDate || '',
          当前持仓天数: holdingDays === '--' ? null : Number(holdingDays),
          当前持仓目标日: detail.summary.holding_target_date ?? '',
          当前持仓收益率_pct: toPercentNumber(detail.summary.holding_return_pct),
          T1收益率_pct: toPercentNumber(detail.summary.t1.return_pct),
          T2收益率_pct: toPercentNumber(detail.summary.t2.return_pct),
          T1基准收益率_pct: toPercentNumber(detail.summary.t1.benchmark_return_pct),
          T2基准收益率_pct: toPercentNumber(detail.summary.t2.benchmark_return_pct),
          T1超额收益率_pct: toPercentNumber(detail.summary.t1.excess_return_pct),
          T2超额收益率_pct: toPercentNumber(detail.summary.t2.excess_return_pct),
          T1股票胜率_pct: toPercentNumber(detail.summary.t1.stock_win_rate),
          T2股票胜率_pct: toPercentNumber(detail.summary.t2.stock_win_rate),
          T1日胜率_pct: toPercentNumber(detail.summary.t1.daily_win_rate),
          T2日胜率_pct: toPercentNumber(detail.summary.t2.daily_win_rate),
          T1可交易数: detail.summary.t1.tradable_count,
          T2可交易数: detail.summary.t2.tradable_count,
          T1跳过数: detail.summary.t1.skipped_count,
          T2跳过数: detail.summary.t2.skipped_count,
          策略样本数: detail.summary.strategy_stats.total_records,
          策略胜率T1_pct: toPercentNumber(detail.summary.strategy_stats.win_rate_t1),
          策略胜率T2_pct: toPercentNumber(detail.summary.strategy_stats.win_rate_t2),
          备注: detail.notes || '',
          创建时间: detail.created_at,
          更新时间: detail.updated_at,
        },
      ]
      const curveSheetRows = (detail.curve ?? []).map((row) => ({
        日期: row.date,
        ETF_T1收益率_pct: toPercentNumber(row.etf_return_t1),
        ETF_T2收益率_pct: toPercentNumber(row.etf_return_t2),
        基准_T1收益率_pct: toPercentNumber(row.benchmark_return_t1),
        基准_T2收益率_pct: toPercentNumber(row.benchmark_return_t2),
        超额_T1收益率_pct: toPercentNumber(row.excess_return_t1),
        超额_T2收益率_pct: toPercentNumber(row.excess_return_t2),
      }))
      const constituentSheetRows = constituentRows.map((row) => ({
        代码: row.symbol,
        名称: row.name,
        信号日: row.signal_date,
        信号主类: row.signal_primary,
        信号事件: row.signal_event,
        信号原因: row.signal_reason,
        T1状态: row.status_t1,
        T1买入日: row.buy_date_t1 ?? '',
        T1买入价: row.buy_price_t1 ?? null,
        T1收益率_pct: toPercentNumber(row.return_pct_t1),
        T2状态: row.status_t2,
        T2买入日: row.buy_date_t2 ?? '',
        T2买入价: row.buy_price_t2 ?? null,
        T2收益率_pct: toPercentNumber(row.return_pct_t2),
        当前估值日: row.current_date ?? '',
        当前价: row.current_price ?? null,
        持仓天数: row.holding_period_days ?? null,
        持仓目标日: row.holding_target_date ?? '',
        持仓收益率_pct: toPercentNumber(row.return_pct_holding),
        收益贡献T1_pct: toPercentNumber(row.contribution_t1),
        收益贡献T2_pct: toPercentNumber(row.contribution_t2),
      }))
      const workbook = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(summarySheetRows), '概览')
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(curveSheetRows), '净值曲线')
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(constituentSheetRows), '成分股明细')
      const fileName = `待买回测明细-${sanitizeFileNameSegment(detail.name)}-${sanitizeFileNameSegment(detail.signal_date)}-${formatExportTimestamp()}.xlsx`
      XLSX.writeFile(workbook, fileName)
      message.success(`已导出待买回测明细：${detail.name}`)
    } finally {
      setDetailExporting(false)
    }
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="待买回测"
        subtitle="由当前待买信号一键生成ETF记录，支持 T+1/T+2 买入口径、胜率统计与成分股明细。"
        badge="ETF"
      />

      <Card className="glass-card" variant="borderless">
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Text type="secondary">
            共 {rows.length} 条记录，自动按最新行情刷新收益与胜率。
          </Typography.Text>
          <Space size={8}>
            <Button
              onClick={() => {
                void handleExportList('xlsx')
              }}
              loading={listExportingFormat === 'xlsx'}
            >
              导出概览 Excel
            </Button>
            <Button
              onClick={() => {
                void handleExportList('csv')
              }}
              loading={listExportingFormat === 'csv'}
            >
              导出概览 CSV
            </Button>
            <Tag color={selectedRecordIds.length > 0 ? 'blue' : 'default'}>已选 {selectedRecordIds.length} 条</Tag>
            <Button disabled={selectedRecordIds.length <= 0} onClick={() => setSelectedRecordIds([])}>
              清空选择
            </Button>
            <Popconfirm
              title={`删除已选 ${selectedRecordIds.length} 条记录？`}
              description="删除后不可恢复。"
              disabled={selectedRecordIds.length <= 0}
              onConfirm={() => batchDeleteMutation.mutate([...selectedRecordIds])}
            >
              <Button
                danger
                icon={<DeleteOutlined />}
                disabled={selectedRecordIds.length <= 0}
                loading={batchDeleteMutation.isPending}
              >
                批量删除
              </Button>
            </Popconfirm>
            <Button
              icon={<ReloadOutlined />}
              loading={listQuery.isFetching}
              onClick={() => {
                void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtests'] })
                if (selectedRecordId) {
                  void queryClient.invalidateQueries({ queryKey: ['signal-etf-backtest-detail', selectedRecordId] })
                }
              }}
            >
              立即刷新
            </Button>
          </Space>
        </Space>

        <Table
          size="small"
          rowKey="record_id"
          style={{ marginTop: 12 }}
          loading={listQuery.isLoading || listQuery.isFetching}
          dataSource={rows}
          columns={columns}
          scroll={{ x: 1480 }}
          rowSelection={{
            selectedRowKeys: selectedRecordIds,
            onChange: (keys) => setSelectedRecordIds(keys.map((item) => String(item))),
          }}
          pagination={{
            current: listPage,
            pageSize: listPageSize,
            showSizeChanger: true,
            pageSizeOptions: ['20', '50', '100'],
            size: 'small',
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, pageSize) => {
              setListPage(page)
              setListPageSize(pageSize)
            },
          }}
          onRow={(record) => ({
            onClick: (event) => {
              const target = event.target as HTMLElement | null
              if (target?.closest('.ant-table-selection-column') || target?.closest('.ant-checkbox-wrapper')) return
              setSelectedRecordId(record.record_id)
            },
          })}
          locale={{
            emptyText: listQuery.isLoading ? '加载中...' : <Empty description="暂无待买回测记录，请去待买信号页一键生成。" />,
          }}
        />
      </Card>

      <Modal
        open={Boolean(selectedRecordId)}
        width={1320}
        title={detail ? `${detail.name}（${detail.signal_date}）` : '待买回测详情'}
        onCancel={() => setSelectedRecordId(null)}
        footer={null}
        destroyOnHidden
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
            <Typography.Text type="secondary">
              策略：{detail?.strategy_name || detail?.strategy_id || '--'} |
              胜率(T+1/T+2)：{toPercent(detail?.summary.strategy_stats.win_rate_t1)} / {toPercent(detail?.summary.strategy_stats.win_rate_t2)}
            </Typography.Text>
            <Button
              onClick={() => {
                void handleExportDetailExcel()
              }}
              loading={detailExporting}
              disabled={!detail || detailQuery.isLoading || detailQuery.isFetching}
            >
              导出当前明细 Excel
            </Button>
          </Space>
          <Card size="small" loading={detailQuery.isLoading || detailQuery.isFetching}>
            {(detail?.curve?.length ?? 0) > 0 ? (
              <ReactECharts option={chartOption} style={{ height: 320 }} notMerge />
            ) : (
              <Empty description="暂无可展示走势（可能成分股均被跳过）" />
            )}
          </Card>
          <Card
            size="small"
            title={`成分股 (${constituentRows.length})`}
            extra={
              <Space size={8} wrap>
                <Tag color={valuationDate ? 'orange' : 'green'}>{valuationDate ? '历史回放' : '最新口径'}</Tag>
                <Tag color="blue">估值日 {activeValuationDate || '--'}</Tag>
                <Tag>持仓 {holdingDays} 天</Tag>
                <Button type="link" size="small" disabled={!valuationDate} onClick={() => setValuationDate(null)}>
                  回到最新
                </Button>
                <Typography.Text type="secondary">点击名称可跳转K线（默认信号日）</Typography.Text>
              </Space>
            }
          >
            {valuationDateOptions.length > 1 ? (
              <div
                style={{
                  marginBottom: 8,
                  padding: '8px 12px 6px',
                  borderRadius: 10,
                  border: '1px solid rgba(5, 5, 5, 0.08)',
                  background: 'rgba(22, 119, 255, 0.04)',
                }}
              >
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Typography.Text style={{ fontSize: 12 }} strong>
                    估值时间轴
                  </Typography.Text>
                  <Typography.Text style={{ fontSize: 12 }} type="secondary">
                    当前：{activeValuationDate || '--'}
                  </Typography.Text>
                </Space>
                <Slider
                  min={0}
                  max={valuationDateOptions.length - 1}
                  value={valuationSliderIndex}
                  step={1}
                  style={{ margin: '8px 2px 2px' }}
                  tooltip={{
                    formatter: (value) => {
                      const index = Number(value)
                      if (!Number.isFinite(index)) return ''
                      return valuationDateOptions[index] || ''
                    },
                  }}
                  onChange={(value) => {
                    if (typeof value === 'number') handleValuationDateChange(value)
                  }}
                />
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 8,
                    marginTop: 4,
                    fontSize: 12,
                    color: 'rgba(0, 0, 0, 0.45)',
                  }}
                >
                  <span style={{ whiteSpace: 'nowrap' }}>{timelineStartDate}</span>
                  <span style={{ whiteSpace: 'nowrap' }}>{timelineEndDate}</span>
                </div>
              </div>
            ) : null}
            <Table
              size="small"
              rowKey={(row) => `${row.symbol}-${row.signal_date}`}
              loading={detailQuery.isLoading || detailQuery.isFetching}
              dataSource={constituentRows}
              columns={constituentColumns}
              scroll={{ x: 1220, y: 360 }}
              pagination={{
                current: constituentPage,
                pageSize: constituentPageSize,
                showSizeChanger: true,
                pageSizeOptions: ['20', '50', '100'],
                onShowSizeChange: (_current, pageSize) => {
                  const nextSize = [20, 50, 100].includes(pageSize) ? pageSize : 20
                  setConstituentPage(1)
                  setConstituentPageSize(nextSize)
                },
                onChange: (page) => {
                  setConstituentPage(page)
                },
              }}
            />
          </Card>
        </Space>
      </Modal>

      <Modal
        open={Boolean(editingRecord)}
        title="编辑ETF记录"
        okText="保存"
        cancelText="取消"
        confirmLoading={updateMutation.isPending}
        onCancel={() => setEditingRecord(null)}
        onOk={() => {
          const row = editingRecord
          if (!row) return
          editForm
            .validateFields()
            .then((values) => {
              updateMutation.mutate({
                recordId: row.record_id,
                name: values.name.trim(),
                notes: values.notes.trim(),
              })
            })
            .catch(() => undefined)
        }}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: '请输入名称' },
              { max: 128, message: '名称不能超过128个字符' },
            ]}
          >
            <Input placeholder="输入ETF记录名称" />
          </Form.Item>
          <Form.Item
            name="notes"
            label="备注"
            rules={[{ max: 1000, message: '备注不能超过1000个字符' }]}
          >
            <Input.TextArea rows={4} placeholder="输入备注（可留空）" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
