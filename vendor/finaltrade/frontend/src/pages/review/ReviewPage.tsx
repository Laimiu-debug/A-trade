import { useMemo, useRef, useState } from 'react'
import dayjs, { Dayjs } from 'dayjs'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  DatePicker,
  Radio,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import ReactECharts from 'echarts-for-react'
import { jsPDF } from 'jspdf'
import autoTable from 'jspdf-autotable'
import * as XLSX from 'xlsx'
import { ApiError } from '@/shared/api/client'
import { getPortfolio, getReviewStats, getSimFills } from '@/shared/api/endpoints'
import { PageHeader } from '@/shared/components/PageHeader'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { buildPageTitle } from '@/state/aiAssistantStore'
import { ReviewWorkspacePanel } from '@/pages/review/ReviewWorkspacePanel'
import { ReviewMarketIndicesPanel } from '@/pages/review/ReviewMarketIndicesPanel'
import type { PortfolioPosition, ReviewResponse, SimTradeFill, TradeRecord } from '@/types/contracts'
import { formatMoney, formatPct } from '@/shared/utils/format'

function formatApiError(error: unknown) {
  if (error instanceof ApiError) {
    return error.message || `请求失败: ${error.code}`
  }
  if (error instanceof Error) {
    return error.message || '请求失败'
  }
  return '请求失败'
}

function bufferToBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function isSameDateRange(prev: [Dayjs, Dayjs], next: [Dayjs, Dayjs]) {
  return prev[0].isSame(next[0], 'day') && prev[1].isSame(next[1], 'day')
}

const tradeColumns: ColumnsType<TradeRecord> = [
  { title: '代码', dataIndex: 'symbol', width: 110 },
  { title: '买入日', dataIndex: 'buy_date', width: 120 },
  { title: '买入价', dataIndex: 'buy_price', width: 100 },
  { title: '卖出日', dataIndex: 'sell_date', width: 120 },
  { title: '卖出价', dataIndex: 'sell_price', width: 100 },
  { title: '数量', dataIndex: 'quantity', width: 90 },
  { title: '持仓天数', dataIndex: 'holding_days', width: 110 },
  {
    title: '盈亏金额',
    dataIndex: 'pnl_amount',
    width: 130,
    render: (value: number) => <span style={{ color: value >= 0 ? '#c4473d' : '#19744f' }}>{formatMoney(value)}</span>,
  },
  {
    title: '盈亏比',
    dataIndex: 'pnl_ratio',
    width: 100,
    render: (value: number) => formatPct(value),
  },
]

const fillColumns: ColumnsType<SimTradeFill> = [
  { title: '代码', dataIndex: 'symbol', width: 110 },
  {
    title: '方向',
    dataIndex: 'side',
    width: 90,
    render: (value: SimTradeFill['side']) => (
      <Tag color={value === 'buy' ? 'blue' : 'orange'}>{value === 'buy' ? '买入' : '卖出'}</Tag>
    ),
  },
  { title: '成交日', dataIndex: 'fill_date', width: 120 },
  { title: '成交价', dataIndex: 'fill_price', width: 100 },
  { title: '数量', dataIndex: 'quantity', width: 90 },
  {
    title: '成交额',
    dataIndex: 'gross_amount',
    width: 130,
    render: (value: number) => formatMoney(value),
  },
  {
    title: '净现金',
    dataIndex: 'net_amount',
    width: 130,
    render: (value: number) => <span style={{ color: value >= 0 ? '#c4473d' : '#19744f' }}>{formatMoney(value)}</span>,
  },
  {
    title: '手续费',
    key: 'fees',
    width: 120,
    render: (_, row) => formatMoney(row.fee_commission + row.fee_stamp_tax + row.fee_transfer),
  },
]

const positionColumns: ColumnsType<PortfolioPosition> = [
  { title: '代码', dataIndex: 'symbol', width: 110 },
  { title: '名称', dataIndex: 'name', width: 130 },
  { title: '持仓数量', dataIndex: 'quantity', width: 110 },
  { title: '可用数量', dataIndex: 'available_quantity', width: 110 },
  { title: '成本价', dataIndex: 'avg_cost', width: 100 },
  { title: '现价', dataIndex: 'current_price', width: 100 },
  {
    title: '市值',
    dataIndex: 'market_value',
    width: 130,
    render: (value: number) => formatMoney(value),
  },
  {
    title: '浮盈亏',
    dataIndex: 'pnl_amount',
    width: 130,
    render: (value: number) => <span style={{ color: value >= 0 ? '#c4473d' : '#19744f' }}>{formatMoney(value)}</span>,
  },
  {
    title: '浮盈亏比',
    dataIndex: 'pnl_ratio',
    width: 110,
    render: (value: number) => formatPct(value),
  },
  { title: '持仓天数', dataIndex: 'holding_days', width: 100 },
]

export function ReviewPage() {
  const { message } = AntdApp.useApp()
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(90, 'day'), dayjs()])
  const [dateAxis, setDateAxis] = useState<'sell' | 'buy'>('sell')
  const equityChartRef = useRef<ReactECharts | null>(null)
  const drawdownChartRef = useRef<ReactECharts | null>(null)
  const monthlyChartRef = useRef<ReactECharts | null>(null)

  const dateFrom = range[0].format('YYYY-MM-DD')
  const dateTo = range[1].format('YYYY-MM-DD')

  const reviewQuery = useQuery({
    queryKey: ['review', dateFrom, dateTo, dateAxis],
    queryFn: () =>
      getReviewStats({
        date_from: dateFrom,
        date_to: dateTo,
        date_axis: dateAxis,
      }),
  })

  const fillsQuery = useQuery({
    queryKey: ['sim-fills-review', dateFrom, dateTo],
    queryFn: () =>
      getSimFills({
        date_from: dateFrom,
        date_to: dateTo,
        page: 1,
        page_size: 500,
      }),
  })

  const portfolioQuery = useQuery({
    queryKey: ['portfolio', 'review'],
    queryFn: getPortfolio,
  })

  const review = reviewQuery.data
  const stats = review?.stats
  const fills = fillsQuery.data?.items ?? []
  const positions = portfolioQuery.data?.positions ?? []

  const pageAIContext = useMemo(
    () => ({
      page: 'review' as const,
      title: buildPageTitle('review', `${dateFrom} ~ ${dateTo}`),
      as_of_date: dateTo,
      refs: { date_from: dateFrom, date_to: dateTo, date_axis: dateAxis },
      payload: {
        stats,
        trade_count: review?.trades?.length ?? 0,
        fill_count: fills.length,
        position_count: positions.length,
      },
    }),
    [dateFrom, dateTo, dateAxis, stats, review?.trades?.length, fills.length, positions.length],
  )
  useRegisterPageAIContext(pageAIContext)

  const fillSummary = useMemo(() => {
    let buyCount = 0
    let sellCount = 0
    let buyAmount = 0
    let sellAmount = 0
    for (const row of fills) {
      if (row.side === 'buy') {
        buyCount += 1
        buyAmount += row.gross_amount
      } else {
        sellCount += 1
        sellAmount += row.gross_amount
      }
    }
    return {
      buyCount,
      sellCount,
      buyAmount,
      sellAmount,
    }
  }, [fills])

  const noClosedTradesHint =
    (stats?.trade_count ?? 0) === 0 &&
    fillSummary.buyCount > 0 &&
    !reviewQuery.isLoading &&
    !fillsQuery.isLoading &&
    !reviewQuery.error

  const equityOption = useMemo(() => {
    const curve = review?.equity_curve ?? []
    return {
      animationDuration: 300,
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curve.map((row) => row.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          name: '权益',
          type: 'line',
          smooth: true,
          data: curve.map((row) => row.equity),
          lineStyle: { width: 2, color: '#0f8b6f' },
          areaStyle: { color: 'rgba(15,139,111,0.16)' },
        },
      ],
      grid: { left: 48, right: 20, top: 24, bottom: 36 },
    }
  }, [review?.equity_curve])

  const drawdownOption = useMemo(() => {
    const curve = review?.drawdown_curve ?? []
    return {
      animationDuration: 300,
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curve.map((row) => row.date) },
      yAxis: {
        type: 'value',
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(1)}%`,
        },
      },
      series: [
        {
          name: '回撤',
          type: 'line',
          smooth: true,
          data: curve.map((row) => row.drawdown),
          lineStyle: { width: 2, color: '#c4473d' },
          areaStyle: { color: 'rgba(196,71,61,0.16)' },
        },
      ],
      grid: { left: 48, right: 20, top: 24, bottom: 36 },
    }
  }, [review?.drawdown_curve])

  const monthlyOption = useMemo(() => {
    const rows = review?.monthly_returns ?? []
    return {
      animationDuration: 300,
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: rows.map((row) => row.month) },
      yAxis: {
        type: 'value',
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(1)}%`,
        },
      },
      series: [
        {
          name: '月收益',
          type: 'bar',
          data: rows.map((row) => row.return_ratio),
          itemStyle: {
            color: (params: { data: number }) => (params.data >= 0 ? '#c4473d' : '#19744f'),
          },
        },
      ],
      grid: { left: 48, right: 20, top: 24, bottom: 36 },
    }
  }, [review?.monthly_returns])

  async function handleExportExcel() {
    try {
      const payload = review as ReviewResponse | undefined
      if (!payload) {
        message.warning('暂无可导出数据')
        return
      }

      const wb = XLSX.utils.book_new()
      const overview = [
        {
          date_from: payload.range.date_from,
          date_to: payload.range.date_to,
          date_axis: payload.range.date_axis,
          win_rate: payload.stats.win_rate,
          total_return: payload.stats.total_return,
          max_drawdown: payload.stats.max_drawdown,
          avg_pnl_ratio: payload.stats.avg_pnl_ratio,
          trade_count: payload.stats.trade_count,
          win_count: payload.stats.win_count,
          loss_count: payload.stats.loss_count,
          profit_factor: payload.stats.profit_factor,
          fill_buy_count: fillSummary.buyCount,
          fill_sell_count: fillSummary.sellCount,
          open_position_count: positions.length,
          open_position_value: portfolioQuery.data?.position_value ?? 0,
          open_unrealized_pnl: portfolioQuery.data?.unrealized_pnl ?? 0,
        },
      ]
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(overview), 'Overview')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(payload.trades), 'ClosedTrades')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(fills), 'Fills')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(positions), 'OpenPositions')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(payload.monthly_returns), 'Monthly')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(payload.equity_curve), 'Equity')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet([payload.cost_snapshot]), 'Config')
      XLSX.writeFile(wb, `review-${dayjs().format('YYYYMMDD-HHmmss')}.xlsx`)
      message.success('Excel 导出完成')
    } catch (error) {
      message.error(formatApiError(error))
    }
  }

  async function handleExportCSV() {
    try {
      const closedTrades = review?.trades ?? []
      if (closedTrades.length > 0) {
        const headers = ['symbol', 'buy_date', 'buy_price', 'sell_date', 'sell_price', 'quantity', 'holding_days', 'pnl_amount', 'pnl_ratio']
        const lines = [headers.join(',')]
        closedTrades.forEach((trade) => {
          lines.push(
            [
              trade.symbol,
              trade.buy_date,
              trade.buy_price,
              trade.sell_date,
              trade.sell_price,
              trade.quantity,
              trade.holding_days,
              trade.pnl_amount,
              trade.pnl_ratio,
            ].join(','),
          )
        })
        const blob = new Blob([`\ufeff${lines.join('\n')}`], { type: 'text/csv;charset=utf-8;' })
        downloadBlob(`closed-trades-${dayjs().format('YYYYMMDD-HHmmss')}.csv`, blob)
        message.success('CSV 导出完成')
        return
      }

      if (fills.length > 0) {
        const headers = ['symbol', 'side', 'fill_date', 'fill_price', 'quantity', 'gross_amount', 'net_amount']
        const lines = [headers.join(',')]
        fills.forEach((fill) => {
          lines.push(
            [fill.symbol, fill.side, fill.fill_date, fill.fill_price, fill.quantity, fill.gross_amount, fill.net_amount].join(','),
          )
        })
        const blob = new Blob([`\ufeff${lines.join('\n')}`], { type: 'text/csv;charset=utf-8;' })
        downloadBlob(`fills-${dayjs().format('YYYYMMDD-HHmmss')}.csv`, blob)
        message.success('CSV 导出完成')
        return
      }

      message.warning('暂无可导出数据')
    } catch (error) {
      message.error(formatApiError(error))
    }
  }

  async function handleExportPDF() {
    try {
      const payload = review as ReviewResponse | undefined
      if (!payload) {
        message.warning('暂无可导出数据')
        return
      }

      const doc = new jsPDF({ unit: 'pt', format: 'a4' })
      let useCnFont = false
      try {
        const fontResp = await fetch('/fonts/LXGWWenKai-Regular.ttf')
        if (fontResp.ok) {
          const buffer = await fontResp.arrayBuffer()
          const base64 = bufferToBase64(buffer)
          doc.addFileToVFS('LXGWWenKai-Regular.ttf', base64)
          doc.addFont('LXGWWenKai-Regular.ttf', 'LXGWWenKai', 'normal')
          doc.setFont('LXGWWenKai', 'normal')
          useCnFont = true
        }
      } catch {
        useCnFont = false
      }

      const safeText = (text: string) => {
        if (useCnFont) return text
        return Array.from(text)
          .filter((char) => {
            const code = char.charCodeAt(0)
            return code >= 32 && code <= 126
          })
          .join('')
      }

      let cursorY = 40
      doc.setFontSize(16)
      doc.text(safeText('模拟交易复盘报告'), 40, cursorY)
      cursorY += 20
      doc.setFontSize(10)
      doc.text(safeText(`区间: ${payload.range.date_from} ~ ${payload.range.date_to} (axis=${payload.range.date_axis})`), 40, cursorY)
      cursorY += 18

      const statRows = [
        ['胜率', formatPct(payload.stats.win_rate)],
        ['总收益率', formatPct(payload.stats.total_return)],
        ['最大回撤', formatPct(payload.stats.max_drawdown)],
        ['平均盈亏比', formatPct(payload.stats.avg_pnl_ratio)],
        ['已平仓交易', String(payload.stats.trade_count)],
        ['买入成交笔数', String(fillSummary.buyCount)],
        ['卖出成交笔数', String(fillSummary.sellCount)],
        ['当前持仓数', String(positions.length)],
        ['当前浮盈亏', formatMoney(portfolioQuery.data?.unrealized_pnl ?? 0)],
      ]
      autoTable(doc, {
        startY: cursorY,
        styles: { font: useCnFont ? 'LXGWWenKai' : 'helvetica', fontSize: 9 },
        head: [[safeText('指标'), safeText('数值')]],
        body: statRows.map((row) => [safeText(row[0]), row[1]]),
        margin: { left: 40, right: 40 },
      })
      cursorY = ((doc as unknown as { lastAutoTable?: { finalY?: number } }).lastAutoTable?.finalY ?? cursorY) + 18

      const addChart = (title: string, ref: React.RefObject<ReactECharts | null>) => {
        const instance = ref.current?.getEchartsInstance()
        if (!instance) return
        const img = instance.getDataURL({
          pixelRatio: 2,
          backgroundColor: '#ffffff',
          type: 'png',
        })
        if (cursorY > 680) {
          doc.addPage()
          cursorY = 40
        }
        doc.setFontSize(11)
        doc.text(safeText(title), 40, cursorY)
        cursorY += 8
        doc.addImage(img, 'PNG', 40, cursorY, 515, 150)
        cursorY += 168
      }

      addChart('权益曲线', equityChartRef)
      addChart('回撤曲线', drawdownChartRef)
      addChart('月度收益', monthlyChartRef)

      if (cursorY > 560) {
        doc.addPage()
        cursorY = 40
      }
      autoTable(doc, {
        startY: cursorY,
        styles: { font: useCnFont ? 'LXGWWenKai' : 'helvetica', fontSize: 8 },
        head: [[safeText('Closed Trades'), safeText('Symbol'), safeText('BuyDate'), safeText('SellDate'), safeText('PnL')]],
        body: (payload.trades || []).slice(0, 30).map((row) => [
          row.symbol,
          row.buy_date,
          row.sell_date,
          String(row.quantity),
          String(row.pnl_amount),
        ]),
        margin: { left: 40, right: 40 },
      })

      doc.save(`review-${dayjs().format('YYYYMMDD-HHmmss')}.pdf`)
      message.success('PDF 导出完成')
    } catch (error) {
      message.error(formatApiError(error))
    }
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="交易记录与复盘" subtitle="复盘统计默认展示已平仓交易，同时补充成交与持仓快照，避免空白。支持 Excel/CSV/PDF 导出。" />
      <ReviewMarketIndicesPanel />
      <ReviewWorkspacePanel dateFrom={dateFrom} dateTo={dateTo} fills={fills} />

      <Card className="glass-card" variant="borderless">
        <Space wrap>
          <DatePicker.RangePicker
            value={range}
            onChange={(value) => {
              if (!value || !value[0] || !value[1]) return
              const nextRange: [Dayjs, Dayjs] = [value[0], value[1]]
              setRange((prev) => (isSameDateRange(prev, nextRange) ? prev : nextRange))
            }}
          />
          <Radio.Group
            value={dateAxis}
            optionType="button"
            options={[
              { label: '按卖出日', value: 'sell' },
              { label: '按买入日', value: 'buy' },
            ]}
            onChange={(event) => setDateAxis(event.target.value as 'sell' | 'buy')}
          />
          <Button onClick={handleExportExcel}>导出 Excel</Button>
          <Button onClick={handleExportCSV}>导出 CSV</Button>
          <Button onClick={handleExportPDF}>导出 PDF</Button>
          <Typography.Text type="secondary">
            成本快照: 佣金 {review?.cost_snapshot?.commission_rate ?? 0} / 印花税 {review?.cost_snapshot?.stamp_tax_rate ?? 0}
          </Typography.Text>
        </Space>
      </Card>

      {noClosedTradesHint ? (
        <Alert
          showIcon
          type="info"
          title="当前仅有买入成交，暂无已平仓交易"
          description="复盘收益类指标只按已卖出交易计算。你可以先在模拟交易页卖出，或先参考下方成交统计与持仓浮盈亏。"
        />
      ) : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="胜率" value={stats?.win_rate ?? 0} formatter={(value) => formatPct(Number(value))} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="总收益率" value={stats?.total_return ?? 0} formatter={(value) => formatPct(Number(value))} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="最大回撤" value={stats?.max_drawdown ?? 0} formatter={(value) => formatPct(Number(value))} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="平均盈亏比" value={stats?.avg_pnl_ratio ?? 0} formatter={(value) => formatPct(Number(value))} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="已平仓笔数" value={stats?.trade_count ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="盈利笔数" value={stats?.win_count ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="亏损笔数" value={stats?.loss_count ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="Profit Factor" value={stats?.profit_factor ?? 0} precision={3} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="买入成交笔数" value={fillSummary.buyCount} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="卖出成交笔数" value={fillSummary.sellCount} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="当前持仓数量" value={positions.length} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card className="glass-card" variant="borderless">
            <Statistic
              title="当前浮盈亏"
              value={portfolioQuery.data?.unrealized_pnl ?? 0}
              formatter={(value) => <span style={{ color: Number(value) >= 0 ? '#c4473d' : '#19744f' }}>{formatMoney(Number(value))}</span>}
            />
          </Card>
        </Col>
      </Row>

      <Card className="glass-card" variant="borderless" title="权益曲线（已平仓）">
        <ReactECharts ref={equityChartRef} option={equityOption} style={{ height: 300 }} notMerge />
      </Card>

      <Card className="glass-card" variant="borderless" title="回撤曲线（已平仓）">
        <ReactECharts ref={drawdownChartRef} option={drawdownOption} style={{ height: 300 }} notMerge />
      </Card>

      <Card className="glass-card" variant="borderless" title="月度收益（已平仓）">
        <ReactECharts ref={monthlyChartRef} option={monthlyOption} style={{ height: 300 }} notMerge />
      </Card>

      <Card className="glass-card" variant="borderless" title="Top / Bottom 已平仓交易">
        <Row gutter={12}>
          <Col xs={24} md={12}>
            <Typography.Title level={5}>Top 交易</Typography.Title>
            <Table
              rowKey={(row) => `top-${row.symbol}-${row.sell_date}-${row.buy_date}`}
              columns={tradeColumns}
              dataSource={review?.top_trades ?? []}
              loading={reviewQuery.isLoading}
              pagination={false}
              size="small"
              scroll={{ x: 850 }}
            />
          </Col>
          <Col xs={24} md={12}>
            <Typography.Title level={5}>Bottom 交易</Typography.Title>
            <Table
              rowKey={(row) => `bottom-${row.symbol}-${row.sell_date}-${row.buy_date}`}
              columns={tradeColumns}
              dataSource={review?.bottom_trades ?? []}
              loading={reviewQuery.isLoading}
              pagination={false}
              size="small"
              scroll={{ x: 850 }}
            />
          </Col>
        </Row>
      </Card>

      <Card className="glass-card" variant="borderless" title="当前持仓快照">
        <Table
          rowKey={(row) => `${row.symbol}-${row.quantity}-${row.avg_cost}`}
          columns={positionColumns}
          dataSource={positions}
          loading={portfolioQuery.isLoading}
          pagination={{ pageSize: 10, showSizeChanger: true }}
          scroll={{ x: 1150 }}
        />
      </Card>

      <Card className="glass-card" variant="borderless" title="成交明细（区间内）">
        <Table
          rowKey={(row) => `${row.order_id}-${row.fill_date}-${row.symbol}`}
          columns={fillColumns}
          dataSource={fills}
          loading={fillsQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          scroll={{ x: 1050 }}
        />
      </Card>

      <Card className="glass-card" variant="borderless" title="已平仓交易明细">
        <Table
          rowKey={(row) => `${row.symbol}-${row.buy_date}-${row.sell_date}-${row.quantity}`}
          columns={tradeColumns}
          dataSource={review?.trades ?? []}
          loading={reviewQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          scroll={{ x: 1000 }}
        />
      </Card>

      {reviewQuery.error ? (
        <Card className="glass-card" variant="borderless">
          <Typography.Text type="danger">{formatApiError(reviewQuery.error)}</Typography.Text>
        </Card>
      ) : null}
    </Space>
  )
}
