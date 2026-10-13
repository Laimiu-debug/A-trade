import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'

export function NotFoundPage() {
  const navigate = useNavigate()
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="路由未匹配，请返回主流程页面继续操作。"
      extra={
        <Button type="primary" onClick={() => navigate('/screener')}>
          返回选股控制台
        </Button>
      }
    />
  )
}
