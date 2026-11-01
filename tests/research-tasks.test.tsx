import { render, screen, within } from '@testing-library/react'
import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { ResearchTasks } from '../desktop/research-tasks'

const responses = vi.hoisted(() => ({ strategy: [] as any[], portfolio: [] as any[], reports: [] as any[] }))
vi.mock('@/shared/api/client', () => ({ apiRequest: vi.fn(async (path: string) => ({ items: path === '/api/atrade/tasks' ? responses.strategy : responses.reports })) }))
vi.mock('../desktop/research-bridge', () => ({ requestDesktop: vi.fn(async () => ({ tasks: responses.portfolio })), notifyDesktop: vi.fn() }))
vi.mock('@/shared/ai/PageAIContext', () => ({ useRegisterPageAIContext: vi.fn() }))
vi.mock('@/shared/components/PageHeader', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }))

beforeEach(() => {
  responses.strategy = []
  responses.portfolio = []
  responses.reports = []
})

function renderTasks(reports = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><MemoryRouter><App><ResearchTasks reports={reports} /></App></MemoryRouter></QueryClientProvider>)
}

async function taskRow(name: string) {
  const label = await screen.findByText(name)
  return within(label.closest('tr')!)
}

it('shows the same 25% progress for portfolio fractions and strategy percentage points', async () => {
  responses.portfolio = [{ id: 'local', name: '组合研究进行中', kind: 'portfolio', status: 'running', progress: 0.25 }]
  responses.strategy = [{ id: 'strategy', name: '策略回测进行中', kind: 'backtest', status: 'running', progress: 25 }]
  renderTasks()
  for (const name of ['组合研究进行中', '策略回测进行中']) {
    const row = await taskRow(name)
    expect(row.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
    expect(row.getByText('25%')).toBeInTheDocument()
  }
})

it('preserves pending progress and percentage objects without scaling them twice', async () => {
  responses.portfolio = [
    { id: 'pending', name: '等待开始的组合', kind: 'portfolio', status: 'queued', progress: 0 },
    { id: 'object', name: '兼容百分数进度', kind: 'portfolio', status: 'running', progress: { percent: 60 } },
  ]
  renderTasks()
  expect((await taskRow('等待开始的组合')).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
  expect((await taskRow('兼容百分数进度')).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '60')
})

it('shows completed portfolio and saved reports as completed results without a progress bar', async () => {
  responses.portfolio = [{ id: 'done', name: '完成的组合研究', kind: 'portfolio', status: 'completed', progress: 1 }]
  responses.reports = [{ report_id: 'saved', source_file_name: '保存的回测报告', has_plateau_result: false, total_return: 0.12, trade_count: 3 }]
  renderTasks(true)
  for (const name of ['完成的组合研究', '保存的回测报告']) {
    const row = await taskRow(name)
    expect(row.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(row.getByText('已完成')).toBeInTheDocument()
    expect(row.getByRole('button', { name: '查看结果' })).toBeEnabled()
  }
})
