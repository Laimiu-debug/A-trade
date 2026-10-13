import { useMemo, useState } from 'react'
import {
  AimOutlined,
  AreaChartOutlined,
  BarChartOutlined,
  ClusterOutlined,
  ControlOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
  PieChartOutlined,
  RadarChartOutlined,
  SettingOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import { Alert, Button, Drawer, Grid, Layout, Menu, Space, Typography } from 'antd'
import { SyncOutlined } from '@ant-design/icons'
import type { ItemType } from 'antd/es/menu/interface'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { formatAbnormalMovementElapsed, useAbnormalMovementRunStore } from '@/state/abnormalMovementRunStore'
import { formatCrossValidateElapsed, useCrossValidateRunStore } from '@/state/crossValidateRunStore'
import { formatTrendLeadersElapsed, useTrendLeadersRunStore } from '@/state/trendLeadersRunStore'
import { AIAssistantDrawer, AIAssistantLauncher } from '@/shared/components/AIAssistantDrawer'
import { useRouteAIContextSync } from '@/shared/ai/useRouteAIContextSync'

const { Header, Content, Sider } = Layout
const SCREENER_CACHE_KEY = 'tdx-trend-screener-cache-v4'

const navItems: ItemType[] = [
  { key: '/screener', icon: <FilterIcon />, label: '选股漏斗' },
  { key: '/signals', icon: <AimOutlined />, label: '待买信号' },
  { key: '/signals/backtest', icon: <LineChartOutlined />, label: '待买回测' },
  { key: '/signals/cross-validate', icon: <ClusterOutlined />, label: '策略交叉验证' },
  { key: '/market/trend', icon: <AreaChartOutlined />, label: '趋势龙头' },
  { key: '/market/sector-capital', icon: <BarChartOutlined />, label: '板块资金' },
  { key: '/market/abnormal-movement', icon: <AreaChartOutlined />, label: '异动票' },
  { key: '/market/sentiment-valuation', icon: <PieChartOutlined />, label: '情绪估值' },
  { key: '/strategy', icon: <ControlOutlined />, label: '策略中心' },
  { key: '/strategy/events', icon: <ControlOutlined />, label: '事件判别' },
  { key: '/backtest', icon: <LineChartOutlined />, label: '策略回测' },
  { key: '/trade', icon: <SwapOutlined />, label: '模拟交易' },
  { key: '/portfolio', icon: <LineChartOutlined />, label: '持仓管理' },
  { key: '/review', icon: <BarChartOutlined />, label: '复盘统计' },
  { key: '/review/share', icon: <AreaChartOutlined />, label: '复盘分享' },
  { key: '/review/news', icon: <BarChartOutlined />, label: '资讯面板' },
  { key: '/ai', icon: <RadarChartOutlined />, label: 'AI 分析' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
]

function FilterIcon() {
  return <FundProjectionScreenOutlined />
}

function resolveSelected(pathname: string) {
  if (pathname.startsWith('/stocks/')) return '/screener'
  if (pathname.startsWith('/signals/backtest')) return '/signals/backtest'
  if (pathname.startsWith('/signals/cross-validate')) return '/signals/cross-validate'
  if (pathname.startsWith('/market/trend')) return '/market/trend'
  if (pathname.startsWith('/market/sector-capital')) return '/market/sector-capital'
  if (pathname.startsWith('/market/abnormal-movement')) return '/market/abnormal-movement'
  if (pathname.startsWith('/market/sentiment-valuation')) return '/market/sentiment-valuation'
  if (pathname.startsWith('/strategy/events')) return '/strategy/events'
  if (pathname.startsWith('/review/share')) return '/review/share'
  if (pathname.startsWith('/review/news')) return '/review/news'
  return pathname
}

function buildSignalsRouteFromScreenerCache(): string {
  try {
    const raw = window.localStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return '/signals'
    const parsed = JSON.parse(raw) as {
      run_meta?: { runId?: unknown; asOfDate?: unknown }
      form_values?: { board_filters?: unknown }
    }
    const runId = typeof parsed?.run_meta?.runId === 'string' ? parsed.run_meta.runId.trim() : ''
    const asOfDate = typeof parsed?.run_meta?.asOfDate === 'string' ? parsed.run_meta.asOfDate.trim() : ''
    if (!runId) return '/signals'
    const boardFilters = Array.from(
      new Set(
        (Array.isArray(parsed?.form_values?.board_filters) ? parsed.form_values.board_filters : [])
          .map((item) => String(item).trim())
          .filter((item) => item === 'main' || item === 'gem' || item === 'star' || item === 'beijing' || item === 'st'),
      ),
    )
    const params = new URLSearchParams({
      mode: 'trend_pool',
      run_id: runId,
      trend_step: 'auto',
    })
    if (asOfDate) params.set('as_of_date', asOfDate)
    boardFilters.forEach((item) => params.append('board_filters', item))
    return `/signals?${params.toString()}`
  } catch {
    return '/signals'
  }
}

export function AppShell() {
  const screens = Grid.useBreakpoint()
  const location = useLocation()
  useRouteAIContextSync()
  const navigate = useNavigate()
  const [drawerOpen, setDrawerOpen] = useState(false)

  const selectedKey = resolveSelected(location.pathname)
  const isMobile = !screens.lg
  const crossValidateRunning = useCrossValidateRunStore((s) => s.status === 'running')
  const crossValidateElapsed = useCrossValidateRunStore((s) => s.elapsedSeconds)
  const stopCrossValidate = useCrossValidateRunStore((s) => s.stop)
  const isOnCrossValidatePage = location.pathname.startsWith('/signals/cross-validate')

  const trendLeadersRunning = useTrendLeadersRunStore((s) => s.status === 'running')
  const trendLeadersPaused = useTrendLeadersRunStore((s) => s.status === 'paused')
  const trendLeadersElapsed = useTrendLeadersRunStore((s) => s.elapsedSeconds)
  const trendLeadersTaskKind = useTrendLeadersRunStore((s) => s.taskKind)
  const pauseTrendLeaders = useTrendLeadersRunStore((s) => s.pause)
  const stopTrendLeaders = useTrendLeadersRunStore((s) => s.stop)
  const continueTrendLeaders = useTrendLeadersRunStore((s) => s.continueRun)
  const isOnTrendLeadersPage = location.pathname.startsWith('/market/trend')

  const abnormalMovementRunning = useAbnormalMovementRunStore((s) => s.status === 'running')
  const abnormalMovementElapsed = useAbnormalMovementRunStore((s) => s.elapsedSeconds)
  const stopAbnormalMovement = useAbnormalMovementRunStore((s) => s.stop)
  const isOnAbnormalMovementPage = location.pathname.startsWith('/market/abnormal-movement')

  const menu = useMemo(
    () => (
      <Menu
        mode="inline"
        selectedKeys={[selectedKey]}
        items={navItems}
        onClick={({ key }) => {
          const target = key === '/signals' ? buildSignalsRouteFromScreenerCache() : key
          navigate(target)
          setDrawerOpen(false)
        }}
        style={{ border: 'none', background: 'transparent' }}
      />
    ),
    [navigate, selectedKey],
  )

  return (
    <Layout style={{ minHeight: '100vh', background: 'transparent' }}>
      {isMobile ? (
        <Drawer
          title="导航"
          placement="left"
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          styles={{ body: { padding: 10 } }}
        >
          {menu}
        </Drawer>
      ) : (
        <Sider
          width={248}
          style={{
            background: 'rgba(251, 255, 252, 0.68)',
            borderRight: '1px solid rgba(31,49,48,0.08)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <div style={{ padding: '20px 16px 12px' }}>
            <Typography.Title level={4} style={{ margin: 0 }}>
              Final Trade
            </Typography.Title>
            <Typography.Text type="secondary">交易工作台</Typography.Text>
          </div>
          {menu}
        </Sider>
      )}

      <Layout style={{ background: 'transparent' }}>
        <Header
          style={{
            background: 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid rgba(31,49,48,0.08)',
            paddingInline: 16,
          }}
        >
          <Space size={12}>
            {isMobile ? (
              <a onClick={() => setDrawerOpen(true)} style={{ cursor: 'pointer', color: '#0a6b54' }}>
                导航
              </a>
            ) : null}
            <Typography.Text strong>Final Trade</Typography.Text>
          </Space>

          <Space size={18}>
            <AIAssistantLauncher />
            <Link to="/screener">主流程</Link>
            <Link to="/strategy">策略中心</Link>
            <Link to="/strategy/events">事件判别</Link>
            <Link to="/settings">设置</Link>
            <a
              href="https://laimiuworld.notion.site/Final-Trade-3155946e3698807e9c66d7052b5febf6?source=copy_link"
              target="_blank"
              rel="noopener noreferrer"
            >
              使用说明
            </a>
            <AreaChartOutlined style={{ color: '#0f8b6f' }} />
          </Space>
        </Header>

        <Content style={{ padding: '18px 18px 24px' }}>
          {crossValidateRunning && !isOnCrossValidatePage ? (
            <Alert
              type="info"
              showIcon
              icon={<SyncOutlined spin />}
              style={{ marginBottom: 12 }}
              title="策略交叉验证进行中"
              description={`任务在后台继续执行，已用时 ${formatCrossValidateElapsed(crossValidateElapsed)}。切换回本页可查看进度。`}
              action={(
                <Space>
                  <Button size="small" type="primary" onClick={() => navigate('/signals/cross-validate')}>
                    查看进度
                  </Button>
                  <Button size="small" danger onClick={() => stopCrossValidate()}>
                    停止
                  </Button>
                </Space>
              )}
            />
          ) : null}
          {abnormalMovementRunning && !isOnAbnormalMovementPage ? (
            <Alert
              type="info"
              showIcon
              icon={<SyncOutlined spin />}
              style={{ marginBottom: 12 }}
              title="异动票扫描进行中"
              description={`任务在后台继续执行，已用时 ${formatAbnormalMovementElapsed(abnormalMovementElapsed)}。切换回异动票页可查看进度。`}
              action={(
                <Space>
                  <Button size="small" type="primary" onClick={() => navigate('/market/abnormal-movement')}>
                    查看进度
                  </Button>
                  <Button size="small" danger onClick={() => stopAbnormalMovement()}>
                    停止
                  </Button>
                </Space>
              )}
            />
          ) : null}
          {(trendLeadersRunning || trendLeadersPaused) && !isOnTrendLeadersPage ? (
            <Alert
              type="info"
              showIcon
              icon={<SyncOutlined spin={trendLeadersRunning} />}
              style={{ marginBottom: 12 }}
              title={trendLeadersTaskKind === 'ladder' ? '连板梯队扫描' : '趋势龙头扫描'}
              description={`任务在后台${trendLeadersPaused ? '已暂停' : '继续执行'}，已用时 ${formatTrendLeadersElapsed(trendLeadersElapsed)}。切换回趋势龙头页可查看进度。`}
              action={(
                <Space>
                  <Button size="small" type="primary" onClick={() => navigate('/market/trend')}>
                    查看进度
                  </Button>
                  {trendLeadersPaused ? (
                    <Button size="small" onClick={() => void continueTrendLeaders()}>
                      继续
                    </Button>
                  ) : (
                    <Button size="small" onClick={() => pauseTrendLeaders()}>
                      暂停
                    </Button>
                  )}
                  <Button size="small" danger onClick={() => stopTrendLeaders()}>
                    停止
                  </Button>
                </Space>
              )}
            />
          ) : null}
          <div className="float-in">
            <Outlet />
          </div>
        </Content>
      </Layout>
      <AIAssistantDrawer />
    </Layout>
  )
}
