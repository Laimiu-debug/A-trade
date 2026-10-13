import { useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App as AntdApp,
  Button,
  Card,
  Col,
  Form,
  InputNumber,
  Modal,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ApiError } from '@/shared/api/client'
import { getPortfolio, postSimOrder } from '@/shared/api/endpoints'
import { PageHeader } from '@/shared/components/PageHeader'
import { usePageAIContextRegistration } from '@/shared/ai/usePageAIContextRegistration'
import { summarizePortfolioSnapshot } from '@/shared/ai/contextPayloads'
import type { PortfolioPosition } from '@/types/contracts'
import { formatMoney, formatPct } from '@/shared/utils/format'

function formatApiError(error: unknown) {
  if (error instanceof ApiError) {
    return error.message || `请求失败：${error.code}`
  }
  if (error instanceof Error) {
    return error.message || '请求失败'
  }
  return '请求失败'
}

function profitColor(value: number) {
  return value >= 0 ? '#c4473d' : '#19744f'
}

export function PortfolioPage() {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [sellForm] = Form.useForm<{ quantity: number }>()
  const [sellTarget, setSellTarget] = useState<PortfolioPosition | null>(null)
  const [sellOpen, setSellOpen] = useState(false)

  const portfolioQuery = useQuery({
    queryKey: ['portfolio'],
    queryFn: getPortfolio,
  })

  const quickSellMutation = useMutation({
    mutationFn: (payload: { symbol: string; quantity: number }) =>
      postSimOrder({
        symbol: payload.symbol,
        side: 'sell',
        quantity: payload.quantity,
        signal_date: dayjs().format('YYYY-MM-DD'),
        submit_date: dayjs().format('YYYY-MM-DD'),
      }),
    onSuccess: (res) => {
      if (res.order.status === 'rejected') {
        message.error(res.order.reject_reason || res.order.status_reason || '卖出失败')
        return
      }
      message.success('卖出订单已提交')
      setSellOpen(false)
      setSellTarget(null)
      sellForm.resetFields()
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
        queryClient.invalidateQueries({ queryKey: ['sim-orders'] }),
        queryClient.invalidateQueries({ queryKey: ['sim-fills'] }),
        queryClient.invalidateQueries({ queryKey: ['review'] }),
      ])
    },
    onError: (error) => {
      message.error(formatApiError(error))
    },
  })

  const columns: ColumnsType<PortfolioPosition> = useMemo(
    () => [
      { title: '代码', dataIndex: 'symbol', width: 110 },
      { title: '名称', dataIndex: 'name', width: 120 },
      { title: '持仓数量', dataIndex: 'quantity', width: 110 },
      { title: '可卖数量', dataIndex: 'available_quantity', width: 110 },
      { title: '成本', dataIndex: 'avg_cost', width: 100 },
      { title: '现价', dataIndex: 'current_price', width: 100 },
      {
        title: '市值',
        dataIndex: 'market_value',
        width: 120,
        render: (value: number) => formatMoney(value),
      },
      {
        title: '盈亏额',
        dataIndex: 'pnl_amount',
        width: 120,
        render: (value: number) => (
          <span style={{ color: profitColor(value) }}>{formatMoney(value)}</span>
        ),
      },
      {
        title: '盈亏比',
        dataIndex: 'pnl_ratio',
        width: 100,
        render: (value: number) => formatPct(value),
      },
      { title: '持仓天数', dataIndex: 'holding_days', width: 110 },
      {
        title: '操作',
        key: 'actions',
        width: 120,
        fixed: 'right',
        render: (_: unknown, row: PortfolioPosition) => (
          <Button
            size="small"
            disabled={row.available_quantity < 100}
            onClick={() => {
              setSellTarget(row)
              setSellOpen(true)
              sellForm.setFieldsValue({ quantity: row.available_quantity })
            }}
          >
            快捷卖出
          </Button>
        ),
      },
    ],
    [sellForm],
  )

  const data = portfolioQuery.data

  usePageAIContextRegistration({
    portfolio: summarizePortfolioSnapshot(data),
  })

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="持仓管理" subtitle="展示模拟账户持仓、盈亏和资金分布，支持快捷卖出。" />
      <Row gutter={[12, 12]}>
        <Col xs={12} md={8}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="总资产" value={data?.total_asset ?? 0} formatter={(val) => formatMoney(Number(val))} />
          </Card>
        </Col>
        <Col xs={12} md={8}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="可用现金" value={data?.cash ?? 0} formatter={(val) => formatMoney(Number(val))} />
          </Card>
        </Col>
        <Col xs={12} md={8}>
          <Card className="glass-card" variant="borderless">
            <Statistic
              title="持仓市值"
              value={data?.position_value ?? 0}
              formatter={(val) => formatMoney(Number(val))}
            />
          </Card>
        </Col>
        <Col xs={12} md={8}>
          <Card className="glass-card" variant="borderless">
            <Statistic
              title="已实现盈亏"
              value={data?.realized_pnl ?? 0}
              styles={{ content: { color: profitColor(data?.realized_pnl ?? 0) } }}
              formatter={(val) => formatMoney(Number(val))}
            />
          </Card>
        </Col>
        <Col xs={12} md={8}>
          <Card className="glass-card" variant="borderless">
            <Statistic
              title="未实现盈亏"
              value={data?.unrealized_pnl ?? 0}
              styles={{ content: { color: profitColor(data?.unrealized_pnl ?? 0) } }}
              formatter={(val) => formatMoney(Number(val))}
            />
          </Card>
        </Col>
        <Col xs={12} md={8}>
          <Card className="glass-card" variant="borderless">
            <Statistic title="挂单数" value={data?.pending_order_count ?? 0} />
            {data?.as_of_date ? <Tag style={{ marginTop: 8 }}>as_of {data.as_of_date}</Tag> : null}
          </Card>
        </Col>
      </Row>
      <Card className="glass-card" variant="borderless">
        <Table
          rowKey="symbol"
          columns={columns}
          dataSource={data?.positions ?? []}
          loading={portfolioQuery.isLoading}
          pagination={false}
          scroll={{ x: 1300 }}
        />
      </Card>

      <Modal
        title={`快捷卖出 ${sellTarget?.symbol ?? ''}`}
        open={sellOpen}
        confirmLoading={quickSellMutation.isPending}
        onCancel={() => {
          setSellOpen(false)
          setSellTarget(null)
        }}
        onOk={() => {
          sellForm
            .validateFields()
            .then((values) => {
              if (!sellTarget) return
              quickSellMutation.mutate({
                symbol: sellTarget.symbol,
                quantity: values.quantity,
              })
            })
            .catch(() => undefined)
        }}
      >
        <Form form={sellForm} layout="vertical" initialValues={{ quantity: sellTarget?.available_quantity ?? 100 }}>
          <Form.Item label="可卖数量">
            <InputNumber value={sellTarget?.available_quantity ?? 0} style={{ width: '100%' }} disabled />
          </Form.Item>
          <Form.Item
            name="quantity"
            label="卖出数量(股)"
            rules={[
              { required: true, message: '请输入卖出数量' },
              {
                validator: (_, value: number) => {
                  if (!sellTarget) return Promise.resolve()
                  if (value % 100 !== 0) return Promise.reject(new Error('需为100股整数倍'))
                  if (value > sellTarget.available_quantity) return Promise.reject(new Error('超过可卖数量'))
                  return Promise.resolve()
                },
              },
            ]}
          >
            <InputNumber min={100} max={sellTarget?.available_quantity ?? 0} step={100} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
