import { useEffect, useMemo, useState } from 'react'
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
  Radio,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { SyncOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { PageHeader } from '@/shared/components/PageHeader'
import { usePageAIContextRegistration } from '@/shared/ai/usePageAIContextRegistration'
import { summarizeAbnormalMovement } from '@/shared/ai/contextPayloads'
import { formatAbnormalMovementElapsed, useAbnormalMovementRunStore } from '@/state/abnormalMovementRunStore'
import type {
  AbnormalMovementDailyLimit,
  AbnormalMovementEventItem,
  AbnormalMovementScanMode,
  BoardFilter,
} from '@/types/contracts'

const KNOWN_ALGORITHM_VERSIONS = ['v4.1-fast-scan', 'v4-snapshot-parallel', 'v3-window-snapshot']

type StatusFilter = 'all' | 'warning' | 'triggered'
type KindFilter = 'all' | '10d' | '30d'

const STORAGE_KEY = 'final-trade-abnormal-movement-v3'

const BOARD_OPTIONS: Array<{ label: string; value: BoardFilter }> = [
  { label: '主板', value: 'main' },
  { label: '创业板', value: 'gem' },
  { label: '科创板', value: 'star' },
  { label: '北交所', value: 'beijing' },
  { label: 'ST', value: 'st' },
]

const DEFAULT_BOARD_FILTERS: BoardFilter[] = ['main', 'gem', 'star', 'beijing']

function defaultDateRange(): [Dayjs, Dayjs] {
  // 全市场扫描较重，默认近 3 个月以缩短等待
  return [dayjs().subtract(3, 'month'), dayjs()]
}

function loadPersistedState() {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as {
      dateFrom?: string
      dateTo?: string
      includeWarnings?: boolean
      statusFilter?: StatusFilter
      kindFilter?: KindFilter
      boardFilters?: BoardFilter[]
      onlyActiveAtEnd?: boolean
      symbolKeyword?: string
      scanMode?: AbnormalMovementScanMode
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

function formatPct(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatDev(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}

function boardLabel(board?: string | null) {
  if (board === 'main') return '主板'
  if (board === 'gem') return '创业板'
  if (board === 'star') return '科创板'
  if (board === 'beijing') return '北交所'
  return board ?? '--'
}

export function AbnormalMovementPage() {
  const navigate = useNavigate()
  const { message } = AntdApp.useApp()
  const persisted = loadPersistedState()
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>(() => {
    if (persisted?.dateFrom && persisted?.dateTo) {
      return [dayjs(persisted.dateFrom), dayjs(persisted.dateTo)]
    }
    return defaultDateRange()
  })
  const [includeWarnings, setIncludeWarnings] = useState(persisted?.includeWarnings ?? true)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>(persisted?.statusFilter ?? 'all')
  const [kindFilter, setKindFilter] = useState<KindFilter>(persisted?.kindFilter ?? 'all')
  const [boardFilters, setBoardFilters] = useState<BoardFilter[]>(
    persisted?.boardFilters?.length ? persisted.boardFilters : DEFAULT_BOARD_FILTERS,
  )
  const [onlyActiveAtEnd, setOnlyActiveAtEnd] = useState(persisted?.onlyActiveAtEnd ?? false)
  const [symbolKeyword, setSymbolKeyword] = useState(persisted?.symbolKeyword ?? '')
  const [scanMode, setScanMode] = useState<AbnormalMovementScanMode>(persisted?.scanMode ?? 'snapshot')

  const runStatus = useAbnormalMovementRunStore((s) => s.status)
  const scanResult = useAbnormalMovementRunStore((s) => s.result)
  const scanError = useAbnormalMovementRunStore((s) => s.errorMessage)
  const elapsedSeconds = useAbnormalMovementRunStore((s) => s.elapsedSeconds)
  const runScan = useAbnormalMovementRunStore((s) => s.runScan)

  usePageAIContextRegistration({
    abnormal_movement: summarizeAbnormalMovement(scanResult),
    scan_status: runStatus,
  })
  const stopScan = useAbnormalMovementRunStore((s) => s.stop)
  const resetScanError = useAbnormalMovementRunStore((s) => s.resetError)

  const isScanning = runStatus === 'running'
  const isScanError = runStatus === 'error'

  useEffect(() => {
    savePersistedState({
      dateFrom: dateRange[0]?.format('YYYY-MM-DD'),
      dateTo: dateRange[1]?.format('YYYY-MM-DD'),
      includeWarnings,
      statusFilter,
      kindFilter,
      boardFilters,
      onlyActiveAtEnd,
      symbolKeyword,
      scanMode,
    })
  }, [boardFilters, dateRange, includeWarnings, kindFilter, onlyActiveAtEnd, scanMode, statusFilter, symbolKeyword])

  const data = scanResult

  const dateToKey = dateRange[1]?.format('YYYY-MM-DD') ?? ''

  /** 后端给出的行情截至交易日，优先于前端自行推算 */
  const tradeDateTo = data?.trade_date_to?.trim() || ''

  const latestTradingDate = useMemo(() => {
    if (tradeDateTo) return tradeDateTo
    const candidates: string[] = []
    if (data?.dates?.length) {
      candidates.push(...data.dates.filter((day) => !dateToKey || day <= dateToKey))
    }
    if (data?.events?.length) {
      candidates.push(...data.events.map((row) => row.as_of_date).filter((day) => !dateToKey || day <= dateToKey))
    }
    if (!candidates.length) return dateToKey
    return candidates.sort().at(-1) ?? dateToKey
  }, [data?.dates, data?.events, dateToKey, tradeDateTo])

  const keyword = symbolKeyword.trim().toLowerCase()

  const filteredEvents = useMemo(() => {
    const events = data?.events ?? []
    const activeCutoff = latestTradingDate || dateToKey
    return events
      .filter((row) => {
        if (onlyActiveAtEnd && activeCutoff && row.as_of_date !== activeCutoff) return false
        if (statusFilter !== 'all' && row.status !== statusFilter) return false
        if (kindFilter !== 'all' && row.kind !== kindFilter) return false
        if (keyword) {
          const code = row.symbol.slice(2).toLowerCase()
          const name = (row.name ?? '').toLowerCase()
          const sym = row.symbol.toLowerCase()
          if (!code.includes(keyword) && !name.includes(keyword) && !sym.includes(keyword)) return false
        }
        return true
      })
      .sort((a, b) => b.as_of_date.localeCompare(a.as_of_date))
  }, [data?.events, dateToKey, keyword, kindFilter, latestTradingDate, onlyActiveAtEnd, statusFilter])

  const isStaleBackend =
    Boolean(data?.algorithm_version) &&
    !KNOWN_ALGORITHM_VERSIONS.includes(String(data?.algorithm_version))

  const missingPinned =
    Boolean(data) &&
    !isStaleBackend &&
    !['sz002552', 'sh603629'].every((sym) => data?.events?.some((row) => row.symbol === sym))

  const hiddenByActiveFilter =
    Boolean(data?.events?.length) &&
    onlyActiveAtEnd &&
    filteredEvents.length === 0 &&
    (data?.events?.length ?? 0) > 0

  const handleScan = () => {
    const dateFrom = dateRange[0]?.format('YYYY-MM-DD')
    const dateTo = dateRange[1]?.format('YYYY-MM-DD')
    if (!dateFrom || !dateTo) {
      message.warning('请选择日期区间')
      return
    }
    resetScanError()
    void runScan(
      {
        date_from: dateFrom,
        date_to: dateTo,
        include_warnings: includeWarnings,
        board_filters: boardFilters,
        scan_mode: scanMode,
      },
      {
        onSuccess: (resp) => {
          const cutoff = resp.trade_date_to?.trim() || dateTo
          const shown = resp.events.filter((row) => !onlyActiveAtEnd || row.as_of_date === cutoff).length
          const modeLabel = resp.scan_mode === 'full' ? '完整' : '快速'
          const ver = resp.algorithm_version ? ` · ${resp.algorithm_version}` : ' · 请重启后端'
          message.success(
            `扫描完成（${modeLabel}）：命中 ${resp.events.length} 条，显示 ${shown} 条 · 截至 ${cutoff}${ver} · ${resp.elapsed_sec}s`,
          )
        },
        onError: (text) => {
          message.error(
            text.includes('Failed to fetch') || text.includes('fetch')
              ? '无法连接后端，请确认已用 start-dev.ps1 启动（后端默认 8010 端口）'
              : text,
          )
        },
        onStopped: () => {
          message.info('扫描已停止')
        },
      },
    )
  }

  const dailyColumns: ColumnsType<AbnormalMovementDailyLimit> = [
    {
      title: '异动第N天',
      dataIndex: 'day_offset',
      width: 88,
      render: (v) => `第 ${Number(v) + 1} 天`,
    },
    { title: '日期', dataIndex: 'date', width: 110 },
    {
      title: '10日窗口偏离',
      dataIndex: 'cum_dev_10',
      width: 120,
      render: (v) => formatDev(v),
    },
    {
      title: '30日窗口偏离',
      dataIndex: 'cum_dev_30',
      width: 120,
      render: (v) => formatDev(v),
    },
    {
      title: '可涨偏离(10日)',
      dataIndex: 'max_dev_10',
      width: 120,
      render: (v) => formatDev(v),
    },
    {
      title: '可涨偏离(30日)',
      dataIndex: 'max_dev_30',
      width: 120,
      render: (v) => formatDev(v),
    },
    {
      title: '可涨上限(平盘)',
      dataIndex: 'effective_max_stock_pct',
      width: 120,
      render: (v) => formatPct(v),
    },
    {
      title: '实际涨跌',
      dataIndex: 'actual_stock_pct',
      width: 100,
      render: (v) => formatPct(v),
    },
    {
      title: '实际偏离',
      dataIndex: 'actual_deviation',
      width: 100,
      render: (v) => formatDev(v),
    },
    {
      title: '重置',
      width: 80,
      render: (_, row) => (
        <Space size={4}>
          {row.reset_10 ? <Tag color="green">10日</Tag> : null}
          {row.reset_30 ? <Tag color="green">30日</Tag> : null}
        </Space>
      ),
    },
  ]

  const columns: ColumnsType<AbnormalMovementEventItem> = [
    {
      title: '代码',
      dataIndex: 'symbol',
      width: 100,
      render: (symbol: string) => (
        <Button type="link" size="small" onClick={() => navigate(`/stocks/${symbol}/chart`)}>
          {symbol.slice(2)}
        </Button>
      ),
    },
    { title: '名称', dataIndex: 'name', width: 100, ellipsis: true },
    { title: '板块', dataIndex: 'board', width: 72, render: boardLabel },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 72,
      render: (kind: string) => (
        <Tag color={kind === '10d' ? 'volcano' : 'purple'}>{kind === '10d' ? '10日' : '30日'}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 88,
      render: (status: string) => (
        <Tag color={status === 'triggered' ? 'red' : 'gold'}>{status === 'triggered' ? '已触发' : '预警'}</Tag>
      ),
    },
    { title: '入异动日', dataIndex: 'entry_date', width: 110 },
    {
      title: '触发日',
      dataIndex: 'trigger_date',
      width: 110,
      render: (v, row) => v ?? (row.status === 'warning' ? '未触发' : '--'),
    },
    {
      title: '截至日',
      dataIndex: 'as_of_date',
      width: 110,
      defaultSortOrder: 'descend',
      sorter: (a, b) => a.as_of_date.localeCompare(b.as_of_date),
    },
    {
      title: '累计偏离',
      dataIndex: 'cum_deviation',
      width: 100,
      render: (v) => formatDev(v),
    },
    {
      title: '基准指数',
      width: 140,
      render: (_, row) => `${row.benchmark_name} (${row.benchmark_symbol})`,
    },
    {
      title: '异动第N天',
      dataIndex: 'episode_day',
      width: 96,
      render: (v) => (v && v > 0 ? `第 ${v} 天` : '--'),
    },
    {
      title: '逐日明细',
      width: 88,
      render: (_, row) => (row.daily_limits.length > 0 ? `${row.daily_limits.length} 天` : '--'),
    },
  ]

  return (
    <div className="page-stack">
      <PageHeader
        title="异动票"
        subtitle="严重异常波动扫描。默认「快速」仅截至日仍在异动的票（并行读盘，通常 1～2 分钟）；「完整」含历史段，约 5～7 分钟。"
      />

      <Card className="glass-card" variant="borderless">
        <Row gutter={[16, 16]} align="middle">
          <Col xs={24}>
            <Typography.Text type="secondary">扫描模式</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <Radio.Group
                value={scanMode}
                onChange={(e) => setScanMode(e.target.value as AbnormalMovementScanMode)}
                optionType="button"
                buttonStyle="solid"
                options={[
                  { label: '快速（仅截至日仍在异动）', value: 'snapshot' },
                  { label: '完整（含历史异动段）', value: 'full' },
                ]}
              />
            </div>
          </Col>
          <Col xs={24} lg={10}>
            <Typography.Text type="secondary">统计区间</Typography.Text>
            <DatePicker.RangePicker
              style={{ width: '100%', marginTop: 8 }}
              value={dateRange}
              onChange={(values) => {
                if (values?.[0] && values[1]) setDateRange([values[0], values[1]])
              }}
            />
          </Col>
          <Col xs={24} lg={14}>
            <Typography.Text type="secondary">扫描板块</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <Checkbox.Group
                options={BOARD_OPTIONS}
                value={boardFilters}
                onChange={(values) => {
                  const next = values as BoardFilter[]
                  setBoardFilters(next.length > 0 ? next : DEFAULT_BOARD_FILTERS)
                }}
              />
            </div>
            <Checkbox
              style={{ marginTop: 8 }}
              checked={includeWarnings}
              onChange={(e) => setIncludeWarnings(e.target.checked)}
            >
              包含预警（10日≥80 / 30日≥160）
            </Checkbox>
            <Checkbox
              style={{ marginTop: 8, marginLeft: 0 }}
              checked={onlyActiveAtEnd}
              onChange={(e) => setOnlyActiveAtEnd(e.target.checked)}
            >
              仅显示截至日仍在异动（压着异动走的票；默认关闭以免漏看历史段）
            </Checkbox>
          </Col>
          <Col xs={24} lg={10}>
            <Space wrap align="start">
              <Button type="primary" icon={<SyncOutlined spin={isScanning} />} loading={isScanning} onClick={handleScan}>
                全市场扫描
              </Button>
              {isScanning ? (
                <Button danger onClick={() => stopScan()}>
                  停止
                </Button>
              ) : null}
              {data ? (
                <Typography.Text type="secondary">
                  扫描 {data.total_scanned} 只（分析 {data.symbols_analyzed ?? '—'}）
                  {data.symbols_skipped_no_index ? ` · 缺指数 ${data.symbols_skipped_no_index}` : ''} · {data.elapsed_sec}s · 命中{' '}
                  {data.events.length} 条
                  {data.scan_mode === 'full' ? ' · 完整' : ' · 快速'}
                  {data.bars_needed ? ` · K${data.bars_needed}` : ''}
                  {data.parallel_workers ? ` · ${data.parallel_workers}线程` : ''}
                  {data.load_sec != null ? ` · 读盘${data.load_sec}s` : ''}
                  {data.analyze_sec != null ? ` · 计算${data.analyze_sec}s` : ''}
                  {data.algorithm_version ? ` · ${data.algorithm_version}` : ' · 后端需重启'}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  快速模式约 1～2 分钟；完整模式约 5～7 分钟
                </Typography.Text>
              )}
            </Space>
          </Col>
        </Row>
      </Card>

      {isScanning ? (
        <Alert
          type="info"
          showIcon
          icon={<SyncOutlined spin />}
          title="全市场扫描进行中"
          description={
            scanMode === 'full'
              ? `完整模式：并行读盘并回溯历史异动段，已用时 ${formatAbnormalMovementElapsed(elapsedSeconds)}。切换页面不会中断扫描。`
              : `快速模式：并行读盘，仅计算截至日仍在异动区的股票，已用时 ${formatAbnormalMovementElapsed(elapsedSeconds)}。切换页面不会中断扫描。`
          }
        />
      ) : null}

      {isScanError && scanError ? (
        <Alert
          type="error"
          showIcon
          title="扫描失败"
          description={scanError}
        />
      ) : null}

      {isStaleBackend ? (
        <Alert
          type="error"
          showIcon
          title="后端算法未更新"
          description="当前接口算法版本过旧（需 v4-snapshot-parallel）。请关闭后重新运行 start-dev.ps1 或换用最新 FinalTrade-V2.4.exe，再扫描。"
        />
      ) : null}

      {missingPinned && !isStaleBackend ? (
        <Alert
          type="warning"
          showIcon
          title="未在结果中发现宝鼎(002552)或利通(603629)"
          description="请确认统计区间包含最近行情，且已勾选主板；也可在下方搜索框输入代码核对。"
        />
      ) : null}

      {hiddenByActiveFilter ? (
        <Alert
          type="warning"
          showIcon
          title="有命中记录，但当前筛选下表格为空"
          description={`共扫描到 ${data?.events?.length ?? 0} 条，但「仅显示截至日仍在异动」需要截至交易日 ${latestTradingDate || '—'} 的记录。请取消该勾选，或把统计结束日调到该交易日。`}
        />
      ) : null}

      <Card className="glass-card abnormal-movement-table-card" variant="borderless" title="异动列表">
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">筛选：</Typography.Text>
          <Button size="small" type={statusFilter === 'all' ? 'primary' : 'default'} onClick={() => setStatusFilter('all')}>
            全部状态
          </Button>
          <Button
            size="small"
            type={statusFilter === 'triggered' ? 'primary' : 'default'}
            onClick={() => setStatusFilter('triggered')}
          >
            已触发
          </Button>
          <Button
            size="small"
            type={statusFilter === 'warning' ? 'primary' : 'default'}
            onClick={() => setStatusFilter('warning')}
          >
            预警
          </Button>
          <Button size="small" type={kindFilter === 'all' ? 'primary' : 'default'} onClick={() => setKindFilter('all')}>
            全部类型
          </Button>
          <Button size="small" type={kindFilter === '10d' ? 'primary' : 'default'} onClick={() => setKindFilter('10d')}>
            10日
          </Button>
          <Button size="small" type={kindFilter === '30d' ? 'primary' : 'default'} onClick={() => setKindFilter('30d')}>
            30日
          </Button>
          <Input
            allowClear
            size="small"
            style={{ width: 160 }}
            placeholder="代码/名称"
            value={symbolKeyword}
            onChange={(e) => setSymbolKeyword(e.target.value)}
          />
          {data ? (
            <Typography.Text type="secondary">
              阈值：10日 {data.warn_threshold_10}/{data.trigger_threshold_10} · 30日 {data.warn_threshold_30}/
              {data.trigger_threshold_30}
              {onlyActiveAtEnd && latestTradingDate
                ? ` · 截至交易日 ${latestTradingDate}${latestTradingDate !== dateToKey ? `（行情末盘，非日历 ${dateToKey}）` : ''}`
                : ''}
            </Typography.Text>
          ) : null}
        </Space>

        <Spin spinning={isScanning} description="全市场扫描中，请稍候…">
          <Table<AbnormalMovementEventItem>
            rowKey={(row) => `${row.symbol}-${row.kind}-${row.entry_date}-${row.as_of_date}`}
            size="small"
            bordered
            columns={columns}
            dataSource={filteredEvents}
            pagination={{
              pageSize: 20,
              showSizeChanger: true,
              showTotal: (total) =>
                data ? `显示 ${total} / 命中 ${data.events.length} 条` : `共 ${total} 条`,
            }}
            locale={{
              emptyText: isScanning ? (
                <div style={{ padding: '48px 0' }}>
                  <Spin />
                  <div style={{ marginTop: 12, color: 'var(--ink-700)' }}>扫描中…</div>
                </div>
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <Space orientation="vertical" size={4}>
                      <Typography.Text strong style={{ color: 'var(--ink-900)' }}>
                        暂无数据
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        请先点击上方「全市场扫描」。下方空白是表格空状态，不是页面故障。
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        建议区间 ≤ 3 个月；若扫描完成仍为空，说明该时段内无预警/触发记录。
                      </Typography.Text>
                    </Space>
                  }
                />
              ),
            }}
            expandable={{
            expandedRowRender: (row) => (
              <Table<AbnormalMovementDailyLimit>
                rowKey={(item) => `${row.symbol}-${item.date}`}
                size="small"
                pagination={false}
                columns={dailyColumns}
                dataSource={row.daily_limits}
              />
            ),
            rowExpandable: (row) => row.daily_limits.length > 0,
          }}
          />
        </Spin>
      </Card>
    </div>
  )
}
