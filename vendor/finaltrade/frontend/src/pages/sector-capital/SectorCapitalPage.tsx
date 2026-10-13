import { useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
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
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'
import ReactECharts from 'echarts-for-react'
import { PageHeader } from '@/shared/components/PageHeader'
import { usePageAIContextRegistration } from '@/shared/ai/usePageAIContextRegistration'
import { summarizeSectorCapital } from '@/shared/ai/contextPayloads'
import { scanSectorCapitalFlow } from '@/shared/api/endpoints'
import type { SectorFlowLeaderItem, SectorFlowTableRow } from '@/types/contracts'

type ChartMode = 'return' | 'flow'
type TableFilter = 'all' | 'top_flow' | 'top_return'

const STORAGE_KEY = 'final-trade-sector-capital-v1'
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

function defaultDateRange(): [Dayjs, Dayjs] {
  return [dayjs().subtract(3, 'year'), dayjs()]
}

function loadPersistedState() {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as {
      dateFrom?: string
      dateTo?: string
      dailyTopN?: number
      flowWindow?: number
      chartMode?: ChartMode
      tableFilter?: TableFilter
      hiddenSectors?: string[]
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

function formatAmount(value: number) {
  if (!Number.isFinite(value)) return '--'
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(0)}万`
  return value.toFixed(0)
}

export function SectorCapitalPage() {
  const persisted = loadPersistedState()
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>(() => {
    if (persisted?.dateFrom && persisted?.dateTo) {
      return [dayjs(persisted.dateFrom), dayjs(persisted.dateTo)]
    }
    return defaultDateRange()
  })
  const [dailyTopN, setDailyTopN] = useState(persisted?.dailyTopN ?? 5)
  const [flowWindow, setFlowWindow] = useState(persisted?.flowWindow ?? 5)
  const [chartMode, setChartMode] = useState<ChartMode>(persisted?.chartMode ?? 'return')
  const [tableFilter, setTableFilter] = useState<TableFilter>(persisted?.tableFilter ?? 'top_flow')
  const [hiddenSectors, setHiddenSectors] = useState<Set<string>>(() => new Set(persisted?.hiddenSectors ?? []))
  const [flowFocusDate, setFlowFocusDate] = useState<Dayjs | null>(null)

  const scanMutation = useMutation({ mutationFn: scanSectorCapitalFlow })

  useEffect(() => {
    savePersistedState({
      dateFrom: dateRange[0]?.format('YYYY-MM-DD'),
      dateTo: dateRange[1]?.format('YYYY-MM-DD'),
      dailyTopN,
      flowWindow,
      chartMode,
      tableFilter,
      hiddenSectors: [...hiddenSectors],
    })
  }, [chartMode, dailyTopN, dateRange, flowWindow, hiddenSectors, tableFilter])

  const data = scanMutation.data

  usePageAIContextRegistration({
    sector_flow: summarizeSectorCapital(data),
  })

  useEffect(() => {
    if (data?.dates?.length && !flowFocusDate) {
      setFlowFocusDate(dayjs(data.dates[data.dates.length - 1]))
    }
  }, [data?.dates, flowFocusDate])

  const returnChartOption = useMemo(() => {
    if (!data?.dates?.length || !data.series.length) return null
    const visible = data.series.filter((item) => !hiddenSectors.has(item.sector))
    if (visible.length === 0) return null
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params]
          if (rows.length <= 0) return ''
          const first = rows[0] as { axisValueLabel?: string; axisValue?: string }
          const title = first?.axisValueLabel || first?.axisValue || ''
          const body = rows.slice(0, 20).map((row) => {
            const item = row as { marker?: string; seriesName?: string; value?: unknown }
            const num = Number(item.value)
            return `${item.marker ?? ''}${item.seriesName ?? ''} <span style="float:right;margin-left:16px;font-weight:600;">${Number.isFinite(num) ? formatPct(num) : '--'}</span>`
          })
          return [title, ...body].join('<br/>')
        },
      },
      legend: { top: 8, type: 'scroll', data: visible.map((item) => item.sector) },
      grid: { left: 52, right: 120, top: 48, bottom: 48 },
      labelLayout: { hideOverlap: true },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
      xAxis: { type: 'category', data: data.dates, name: '日期' },
      yAxis: {
        type: 'value',
        name: '涨幅(%)',
        axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
      },
      series: visible.map((item, index) => {
        const pointMap = new Map(item.points.map((point) => [point.date, point.return_pct]))
        const color = CHART_COLORS[index % CHART_COLORS.length]
        return {
          name: item.sector,
          type: 'line',
          smooth: true,
          showSymbol: false,
          color,
          endLabel: {
            show: true,
            formatter: () => item.sector,
            color,
            fontSize: 11,
            distance: 6,
          },
          labelLayout: { hideOverlap: true },
          data: data.dates.map((date) => pointMap.get(date) ?? null),
        }
      }),
    }
  }, [data, hiddenSectors])

  const flowBarOption = useMemo(() => {
    if (!data?.flow_table.length || !flowFocusDate) return null
    const focus = flowFocusDate.format('YYYY-MM-DD')
    const rows = data.flow_table
      .filter((row) => row.date === focus)
      .sort((a, b) => b.flow_score - a.flow_score)
      .slice(0, Math.max(dailyTopN, 8))
    if (rows.length === 0) return null
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const rowsParam = Array.isArray(params) ? params : [params]
          const item = rowsParam[0] as { name?: string; value?: number; dataIndex?: number }
          const row = rows[item.dataIndex ?? 0]
          if (!row) return ''
          return [
            `${row.sector}`,
            `资金热度: ${formatPct(row.flow_score)}`,
            `涨幅: ${formatPct(row.return_pct)}`,
            `成交额: ${formatAmount(row.amount)}`,
            `较昨日: ${formatAmount(row.amount_delta)}`,
          ].join('<br/>')
        },
      },
      grid: { left: 88, right: 20, top: 24, bottom: 48 },
      xAxis: { type: 'value', name: '资金热度(%)' },
      yAxis: { type: 'category', data: rows.map((row) => row.sector).reverse() },
      series: [
        {
          type: 'bar',
          data: rows.map((row) => row.flow_score).reverse(),
          itemStyle: {
            color: (params: { dataIndex: number }) => CHART_COLORS[params.dataIndex % CHART_COLORS.length],
          },
        },
      ],
    }
  }, [dailyTopN, data, flowFocusDate])

  const filteredTableRows = useMemo(() => {
    const rows = data?.flow_table ?? []
    if (tableFilter === 'top_flow') {
      return rows.filter((row) => row.rank_flow <= dailyTopN)
    }
    if (tableFilter === 'top_return') {
      return rows.filter((row) => row.rank_return <= dailyTopN)
    }
    return rows
  }, [dailyTopN, data?.flow_table, tableFilter])

  function handleScan() {
    const [from, to] = dateRange
    if (!from || !to) {
      message.warning('请选择日期区间')
      return
    }
    scanMutation.mutate(
      {
        date_from: from.format('YYYY-MM-DD'),
        date_to: to.format('YYYY-MM-DD'),
        daily_top_n: dailyTopN,
        flow_window: flowWindow,
      },
      {
        onSuccess: (payload) => {
          setFlowFocusDate(dayjs(payload.date_to))
          message.success(
            `扫描完成：${payload.leaders.length} 个轮动板块 · ${payload.flow_table.length} 条流水 · ${payload.elapsed_sec.toFixed(1)}s`,
          )
        },
        onError: (error) => {
          message.error(error instanceof Error ? error.message : '板块资金扫描失败')
        },
      },
    )
  }

  function toggleSector(sector: string) {
    setHiddenSectors((previous) => {
      const next = new Set(previous)
      if (next.has(sector)) next.delete(sector)
      else next.add(sector)
      return next
    })
  }

  const leaderColumns: ColumnsType<SectorFlowLeaderItem> = [
    { title: '板块', dataIndex: 'sector', width: 120 },
    {
      title: '资金领先天数',
      dataIndex: 'leader_days',
      width: 110,
      sorter: (a, b) => a.leader_days - b.leader_days,
      defaultSortOrder: 'descend',
    },
    {
      title: '区间最高涨幅',
      dataIndex: 'max_return_pct',
      width: 120,
      render: (value: number) => formatPct(value),
    },
    {
      title: '平均资金热度',
      dataIndex: 'avg_flow_score',
      width: 120,
      render: (value: number) => formatPct(value),
    },
    {
      title: '领先区间',
      width: 200,
      render: (_value, row) => `${row.first_leader_date || '--'} ~ ${row.last_leader_date || '--'}`,
    },
    {
      title: '显示',
      width: 72,
      render: (_value, row) => (
        <Checkbox checked={!hiddenSectors.has(row.sector)} onChange={() => toggleSector(row.sector)} />
      ),
    },
  ]

  const flowTableColumns: ColumnsType<SectorFlowTableRow> = [
    { title: '日期', dataIndex: 'date', width: 110, sorter: (a, b) => a.date.localeCompare(b.date), defaultSortOrder: 'descend' },
    { title: '板块', dataIndex: 'sector', width: 110 },
    {
      title: '涨幅',
      dataIndex: 'return_pct',
      width: 90,
      render: (value: number) => (
        <Typography.Text style={{ color: value >= 0 ? '#c4473d' : '#19744f' }}>{formatPct(value)}</Typography.Text>
      ),
    },
    {
      title: '资金热度',
      dataIndex: 'flow_score',
      width: 100,
      render: (value: number) => formatPct(value),
    },
    {
      title: '成交额',
      dataIndex: 'amount',
      width: 100,
      render: (value: number) => formatAmount(value),
    },
    {
      title: '较昨日',
      dataIndex: 'amount_delta',
      width: 100,
      render: (value: number) => formatAmount(value),
    },
    {
      title: '涨幅排名',
      dataIndex: 'rank_return',
      width: 90,
      render: (value: number) => <Tag>{value}</Tag>,
    },
    {
      title: '资金排名',
      dataIndex: 'rank_flow',
      width: 90,
      render: (value: number) => <Tag color="blue">{value}</Tag>,
    },
  ]

  return (
    <div>
      <PageHeader
        title="板块资金"
        subtitle="基于 TDX 27 行业指数：每日资金热度（成交额相对均值）与涨幅排名，观察板块轮动与资金流向。默认统计近 3 年。"
      />

      <Card className="glass-card" variant="borderless">
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={10} lg={8}>
              <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                <Typography.Text type="secondary">统计区间（支持 3 年）</Typography.Text>
                <DatePicker.RangePicker
                  style={{ width: '100%' }}
                  value={dateRange}
                  onChange={(values) => {
                    if (values?.[0] && values[1]) setDateRange([values[0], values[1]])
                  }}
                />
              </Space>
            </Col>
            <Col xs={24} md={7} lg={5}>
              <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                <Typography.Text type="secondary">每日资金 Top N</Typography.Text>
                <InputNumber min={1} max={15} value={dailyTopN} onChange={(value) => setDailyTopN(Number(value) || 5)} style={{ width: '100%' }} />
              </Space>
            </Col>
            <Col xs={24} md={7} lg={5}>
              <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                <Typography.Text type="secondary">资金均值窗口（日）</Typography.Text>
                <InputNumber min={3} max={60} value={flowWindow} onChange={(value) => setFlowWindow(Number(value) || 5)} style={{ width: '100%' }} />
              </Space>
            </Col>
          </Row>
          <Space wrap>
            <Button type="primary" loading={scanMutation.isPending} onClick={handleScan}>
              扫描板块资金
            </Button>
            {data ? (
              <Typography.Text type="secondary">
                {data.date_from} ~ {data.date_to} · 轮动板块 {data.leaders.length} · {data.elapsed_sec.toFixed(1)}s
              </Typography.Text>
            ) : null}
          </Space>
        </Space>
      </Card>

      <Card
        className="glass-card"
        variant="borderless"
        style={{ marginTop: 16 }}
        title="板块对比图"
        extra={
          <Radio.Group
            optionType="button"
            value={chartMode}
            onChange={(event) => setChartMode(event.target.value as ChartMode)}
            options={[
              { label: '涨幅折线', value: 'return' },
              { label: '单日资金', value: 'flow' },
            ]}
          />
        }
      >
        {chartMode === 'return' ? (
          returnChartOption ? (
            <ReactECharts option={returnChartOption} style={{ height: 480 }} notMerge />
          ) : (
            <Typography.Text type="secondary">扫描后展示轮动板块涨幅曲线（区间起点 = 0%）。</Typography.Text>
          )
        ) : (
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <DatePicker
              value={flowFocusDate}
              onChange={setFlowFocusDate}
              placeholder="选择查看日期"
              disabled={!data?.dates?.length}
            />
            {flowBarOption ? (
              <ReactECharts option={flowBarOption} style={{ height: 420 }} notMerge />
            ) : (
              <Typography.Text type="secondary">选择日期后展示当日资金热度 Top 板块。</Typography.Text>
            )}
          </Space>
        )}
      </Card>

      <Card className="glass-card" variant="borderless" style={{ marginTop: 16 }} title="轮动板块汇总">
        <Table
          rowKey="sector"
          size="small"
          loading={scanMutation.isPending}
          columns={leaderColumns}
          dataSource={data?.leaders ?? []}
          pagination={{ pageSize: 15 }}
          locale={{ emptyText: '点击「扫描板块资金」获取结果' }}
        />
      </Card>

      <Card
        className="glass-card"
        variant="borderless"
        style={{ marginTop: 16 }}
        title="每日资金流动表"
        extra={
          <Radio.Group
            optionType="button"
            size="small"
            value={tableFilter}
            onChange={(event) => setTableFilter(event.target.value as TableFilter)}
            options={[
              { label: `资金Top${dailyTopN}`, value: 'top_flow' },
              { label: `涨幅Top${dailyTopN}`, value: 'top_return' },
              { label: '全部', value: 'all' },
            ]}
          />
        }
      >
        <Table
          rowKey={(row) => `${row.date}-${row.sector}`}
          size="small"
          loading={scanMutation.isPending}
          columns={flowTableColumns}
          dataSource={filteredTableRows}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: ['30', '50', '100', '200'] }}
          locale={{ emptyText: '扫描后展示每日板块资金与涨幅明细' }}
        />
      </Card>
    </div>
  )
}
