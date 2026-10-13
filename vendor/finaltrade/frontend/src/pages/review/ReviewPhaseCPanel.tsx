import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Empty, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getMarketNews } from '@/shared/api/endpoints'
import type { MarketNewsItem } from '@/types/contracts'

type ReviewPhaseCPanelProps = {
  dateFrom: string
  dateTo: string
}

const AGE_OPTIONS: Array<{ label: string; value: 24 | 48 | 72 }> = [
  { label: '近24小时', value: 24 },
  { label: '近48小时', value: 48 },
  { label: '近72小时', value: 72 },
]

const DEFAULT_NEWS_QUERY = 'A股 热点'

function compactText(value: string, limit = 120) {
  const text = value.trim()
  if (!text) return '-'
  if (text.length <= limit) return text
  return `${text.slice(0, limit - 1)}...`
}

export function ReviewPhaseCPanel({ dateFrom, dateTo }: ReviewPhaseCPanelProps) {
  const queryClient = useQueryClient()
  const [ageHours, setAgeHours] = useState<24 | 48 | 72>(72)

  const queryKey = ['market-news', DEFAULT_NEWS_QUERY, ageHours] as const
  const newsQuery = useQuery({
    queryKey,
    queryFn: () =>
      getMarketNews({
        query: DEFAULT_NEWS_QUERY,
        age_hours: ageHours,
        limit: 24,
      }),
    refetchInterval: 5 * 60 * 1000,
    refetchIntervalInBackground: true,
  })

  async function handleForceRefresh() {
    const data = await getMarketNews({
      query: DEFAULT_NEWS_QUERY,
      age_hours: ageHours,
      refresh: true,
      limit: 24,
    })
    queryClient.setQueryData(queryKey, data)
  }

  const columns: ColumnsType<MarketNewsItem> = useMemo(
    () => [
      {
        title: '时间',
        dataIndex: 'pub_date',
        width: 180,
        render: (value: string) => compactText(value, 22),
      },
      {
        title: '来源',
        dataIndex: 'source_name',
        width: 140,
        render: (value: string) => <Tag>{value || '-'}</Tag>,
      },
      {
        title: '标题',
        dataIndex: 'title',
        render: (value: string) => compactText(value, 96),
      },
      {
        title: '摘要',
        dataIndex: 'snippet',
        render: (value: string) => compactText(value, 140),
      },
    ],
    [],
  )

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space orientation="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>资讯面板</Typography.Text>
          <Typography.Text type="secondary">
            复盘区间 {dateFrom} ~ {dateTo}。默认拉取 A股 热点资讯，支持 24/48/72 小时时效窗口与手动刷新。
          </Typography.Text>

          <Space wrap>
            <Select style={{ width: 120 }} value={ageHours} onChange={(value) => setAgeHours(value)} options={AGE_OPTIONS} />
            <Button onClick={() => void handleForceRefresh()} loading={newsQuery.isFetching}>
              刷新
            </Button>
          </Space>

          <Space wrap>
            <Tag color={newsQuery.data?.cache_hit ? 'blue' : 'default'}>
              {newsQuery.data?.cache_hit ? '命中缓存' : '实时拉取'}
            </Tag>
            <Tag color={newsQuery.data?.fallback_used ? 'orange' : 'default'}>
              {newsQuery.data?.fallback_used ? '已启用兜底' : '未启用兜底'}
            </Tag>
            <Tag>窗口: {newsQuery.data?.age_hours ?? ageHours}h</Tag>
            <Tag color="geekblue">{DEFAULT_NEWS_QUERY}</Tag>
          </Space>
        </Space>
      </Card>

      {newsQuery.data?.degraded ? (
        <Alert
          showIcon
          type="warning"
          title="资讯已降级"
          description={newsQuery.data.degraded_reason || '当前资讯源可用性较低，已返回可用结果。'}
        />
      ) : null}

      <Card size="small" title={`资讯列表（${newsQuery.data?.items.length ?? 0} 条）`}>
        <Table
          rowKey={(row) => `${row.url}-${row.pub_date}`}
          columns={columns}
          dataSource={newsQuery.data?.items ?? []}
          loading={newsQuery.isLoading}
          size="small"
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 1100 }}
          locale={{
            emptyText: <Empty description="暂无资讯，建议切换窗口后刷新重试" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
          }}
        />
      </Card>
    </Space>
  )
}
