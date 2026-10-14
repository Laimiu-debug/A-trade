import { Alert } from 'antd'
import { describeDataQuality } from '@/shared/utils/dataQuality'

export function DataQualityAlert({ reason }: { reason?: string | null }) {
  const notice = describeDataQuality(reason)
  return (
    <Alert showIcon type={notice.type} title={notice.title} description={(
      <div>
        {notice.description}
        {reason ? <details className="research-quality-detail"><summary>技术详情</summary><code>{reason}</code></details> : null}
      </div>
    )} />
  )
}
