import { Space, Tag, Typography } from 'antd'

interface PageHeaderProps {
  title: string
  subtitle: string
  badge?: string
}

export function PageHeader({ title, subtitle, badge }: PageHeaderProps) {
  return (
    <Space orientation="vertical" size={4} style={{ marginBottom: 18 }}>
      <Space align="center" size={12}>
        <Typography.Title level={3} className="page-title">
          {title}
        </Typography.Title>
        {badge ? <Tag color="green">{badge}</Tag> : null}
      </Space>
      <Typography.Paragraph className="page-subtitle">
        {subtitle}
      </Typography.Paragraph>
    </Space>
  )
}


