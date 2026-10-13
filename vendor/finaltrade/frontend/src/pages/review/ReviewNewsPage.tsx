import { useState } from 'react'
import dayjs, { Dayjs } from 'dayjs'
import { Card, DatePicker, Space, Typography } from 'antd'
import { PageHeader } from '@/shared/components/PageHeader'
import { ReviewPhaseCPanel } from '@/pages/review/ReviewPhaseCPanel'

function isSameDateRange(prev: [Dayjs, Dayjs], next: [Dayjs, Dayjs]) {
  return prev[0].isSame(next[0], 'day') && prev[1].isSame(next[1], 'day')
}

export function ReviewNewsPage() {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(90, 'day'), dayjs()])
  const dateFrom = range[0].format('YYYY-MM-DD')
  const dateTo = range[1].format('YYYY-MM-DD')

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="资讯面板"
        subtitle="聚合展示复盘期市场资讯，支持 24/48/72 小时时效窗口与刷新。"
      />

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
          <Typography.Text type="secondary">
            复盘区间: {dateFrom} ~ {dateTo}
          </Typography.Text>
        </Space>
      </Card>

      <ReviewPhaseCPanel dateFrom={dateFrom} dateTo={dateTo} />
    </Space>
  )
}
