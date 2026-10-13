import { useState } from 'react'
import dayjs, { Dayjs } from 'dayjs'
import { Card, DatePicker, Space, Typography } from 'antd'
import { PageHeader } from '@/shared/components/PageHeader'
import { ReviewPhaseBPanel } from '@/pages/review/ReviewPhaseBPanel'

function isSameDateRange(prev: [Dayjs, Dayjs], next: [Dayjs, Dayjs]) {
  return prev[0].isSame(next[0], 'day') && prev[1].isSame(next[1], 'day')
}

export function ReviewSharePage() {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(90, 'day'), dayjs()])
  const dateFrom = range[0].format('YYYY-MM-DD')
  const dateTo = range[1].format('YYYY-MM-DD')

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="股票搜索与分享"
        subtitle="用于搜索股票并生成复盘分享卡片，展示价格、走势和复盘思考。"
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

      <ReviewPhaseBPanel dateFrom={dateFrom} dateTo={dateTo} />
    </Space>
  )
}
