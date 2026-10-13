import { Card, Empty, Space, Tag } from 'antd'
import type { ReactNode } from 'react'
import { PageHeader } from '@/shared/components/PageHeader'

interface PlaceholderPageProps {
  title: string
  subtitle: string
  tags?: string[]
  extra?: ReactNode
}

export function PlaceholderPage({ title, subtitle, tags = [], extra }: PlaceholderPageProps) {
  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title={title} subtitle={subtitle} />
      <Card className="glass-card" variant="borderless">
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Space>
            {tags.map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </Space>
          {extra}
          <Empty description="模块已接入路由与Mock，等待后续深化实现" />
        </Space>
      </Card>
    </Space>
  )
}


