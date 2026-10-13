import { useMemo, useState } from 'react'
import { Alert, App, Button, Input, Modal, Progress, Select, Space, Table, Tag } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiRequest } from '@/shared/api/client'
import { useBacktestTaskStore } from '@/state/backtestTaskStore'
import { useBacktestPlateauTaskStore } from '@/state/backtestPlateauTaskStore'
import { PageHeader } from '@/shared/components/PageHeader'
import { requestDesktop, notifyDesktop } from './research-bridge'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'

const statusText: Record<string, string> = { pending: '排队中', queued: '排队中', running: '运行中', paused: '已暂停', pausing: '正在暂停', stopping: '正在停止', succeeded: '已完成', completed: '已完成', failed: '失败', cancelled: '已停止', stopped: '已停止' }
const typeText: Record<string, string> = { backtest: '策略回测', plateau: '收益平原', singleBacktest: '策略回测', singlePlateau: '收益平原', portfolio: '组合研究', comparison: '组合对比', walkForward: '滚动研究', systemPlateau: '系统平原', frozenValidation: '参数验证', poolPreview: '选股计算' }
const complete = (status: string) => ['succeeded', 'completed'].includes(status)
function saveJSON(data: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export function ResearchTasks({ reports = false }: { reports?: boolean }) {
  const navigate = useNavigate(), { message } = App.useApp()
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [busy, setBusy] = useState(''), [rename, setRename] = useState<any>(null), [name, setName] = useState('')
  const data = useQuery({ queryKey: ['project-research-tasks'], queryFn: async () => {
    const outcomes = await Promise.allSettled([apiRequest<any>('/api/atrade/tasks'), requestDesktop<any>('business', { method: 'research.list' }),apiRequest<any>('/api/backtest/reports')])
    const rows: any[] = [], errors: string[] = []
    if (outcomes[0].status === 'fulfilled') rows.push(...outcomes[0].value.items.map((row: any) => ({ ...row, transport: 'strategy' })))
    else errors.push(outcomes[0].reason.message)
    if (outcomes[1].status === 'fulfilled') rows.push(...outcomes[1].value.tasks.map((row: any) => ({ ...row, transport: 'portfolio', date_from: row.config?.startDate || row.startDate, date_to: row.config?.endDate || row.endDate, created_at: row.createdAt, updated_at: row.updatedAt, progress: typeof row.progress === 'number' ? row.progress : row.progress?.percent || 0 })))
    else errors.push(outcomes[1].reason.message)
    if(outcomes[2].status==='fulfilled')rows.push(...outcomes[2].value.items.map((row:any)=>({...row,id:row.report_id,name:row.source_file_name||'已保存报告',kind:row.has_plateau_result?'plateau':'backtest',transport:'report',status:'succeeded',progress:100,stats:{total_return:row.total_return,trade_count:row.trade_count}})))
    else errors.push(outcomes[2].reason.message)
    return { rows: rows.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))), errors }
  }, refetchInterval: 2500 })
  const rows = useMemo(() => (data.data?.rows || []).filter(row => (!reports || complete(row.status)) && (filter === 'all' || filter === 'active' && ['pending', 'queued', 'running', 'paused'].includes(row.status) || filter === 'done' && complete(row.status) || filter === 'failed' && row.status === 'failed') && (!query || JSON.stringify([row.name, typeText[row.kind], row.date_from, row.date_to]).toLowerCase().includes(query.toLowerCase()))), [data.data, filter, reports, query])
  useRegisterPageAIContext({page:'generic',title:reports?'报告库':'研究任务',filters:{query,status:filter},data:{tasks:rows.slice(0,30).map(row=>({name:row.name,type:typeText[row.kind],status:statusText[row.status],progress:row.progress,stats:row.stats}))}})
  async function perform(row: any, action: string) {
    if (busy) return
    setBusy(row.id)
    try {
      if (action === 'view') {
        if(row.transport==='report')navigate('/backtest?report='+encodeURIComponent(row.id)+(row.has_plateau_result?'&section=plateau':''))
        else if (row.transport === 'portfolio') await requestDesktop('navigate', { page: 'R04', taskId: row.id })
        else {
          const plateau = row.kind === 'plateau', task = await apiRequest<any>(`/api/backtest/${plateau ? 'plateau/' : ''}tasks/${encodeURIComponent(row.id)}`)
          const store = plateau ? useBacktestPlateauTaskStore.getState() : useBacktestTaskStore.getState()
          store.upsertTaskStatus(task)
          store.setSelectedTask(row.id)
          navigate('/backtest' + (plateau ? '?section=plateau' : ''))
        }
      } else if (action === 'export') {
        if(row.transport==='portfolio')await requestDesktop('export-report',{id:row.id});
        else{const result=await apiRequest(row.transport==='report'?`/api/backtest/reports/${encodeURIComponent(row.id)}`:`/api/backtest/${row.kind === 'plateau' ? 'plateau/' : ''}tasks/${encodeURIComponent(row.id)}`);saveJSON(result,`${row.name||'研究报告'}.json`)}
      } else if (action === 'delete') {
        if (row.transport === 'portfolio') await requestDesktop('business', { method: 'research.delete', payload: { ids: [row.id] } })
        else await apiRequest(row.transport==='report'?`/api/backtest/reports/${encodeURIComponent(row.id)}`:`/api/backtest/${row.kind === 'plateau' ? 'plateau/' : ''}tasks/${encodeURIComponent(row.id)}`, { method: 'DELETE' })
        message.success('记录已删除')
      } else {
        if (row.transport === 'portfolio') await requestDesktop('business', { method: 'research.control', payload: { ids: [row.id], action: action === 'cancel' ? 'stop' : action } })
        else await apiRequest(`/api/backtest/${row.kind === 'plateau' ? 'plateau/' : ''}tasks/${encodeURIComponent(row.id)}/${action}`, { method: 'POST' })
      }
      await data.refetch(); notifyDesktop('changed', {})
    } catch (error) { message.error((error as Error).message) } finally { setBusy('') }
  }
  async function saveName() {
    if (!rename || !name.trim()) return
    setBusy(rename.id)
    try {
      if (rename.transport === 'portfolio') await requestDesktop('business', { method: 'research.rename', payload: { id: rename.id, name: name.trim() } })
      else await apiRequest(`/api/atrade/tasks/${encodeURIComponent(rename.id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) })
      setRename(null); await data.refetch()
    } catch (error) { message.error((error as Error).message) } finally { setBusy('') }
  }
  return <><PageHeader title={reports ? '报告库' : '研究任务'} subtitle={reports ? '回测、收益平原与组合研究的已完成结果集中保存在这里。' : '查看所有研究任务的进度，切换页面后仍可暂停、继续或停止。'} />
    <div className="atrade-task-toolbar"><Input.Search aria-label="搜索研究任务" placeholder="搜索名称、类型或日期" value={query} onChange={event => setQuery(event.target.value)} allowClear /><Select aria-label="任务状态" value={filter} onChange={setFilter} options={[{ value: 'all', label: '全部状态' }, { value: 'active', label: '未完成' }, { value: 'done', label: '已完成' }, { value: 'failed', label: '失败' }]} /><Button onClick={() => void data.refetch()}>刷新</Button><Button type="primary" onClick={() => navigate('/backtest')}>新建回测</Button><Button onClick={() => notifyDesktop('navigate', { page: 'R15' })}>组合与系统研究</Button></div>
    {data.data?.errors.map(error => <Alert key={error} type="warning" showIcon title={error} style={{ marginBottom: 12 }} />)}
    <Table rowKey={row => row.transport + ':' + row.id} loading={data.isLoading} dataSource={rows} scroll={{ x: 1100 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: total => `共 ${total} 项` }} columns={[
      { title: '研究任务', width: 260, render: (_, row) => <><div className="atrade-task-name">{row.name}</div><div className="atrade-task-meta">{typeText[row.kind] || '研究'} · {row.date_from || '—'} ～ {row.date_to || '—'}</div></> },
      { title: '状态 / 进度', width: 220, render: (_, row) => <><Tag color={complete(row.status) ? 'success' : row.status === 'failed' ? 'error' : 'blue'}>{statusText[row.status] || row.status}</Tag>{!complete(row.status) && <Progress percent={Math.max(0, Math.min(100, Number(row.progress || 0)))} size="small" />}<div className="atrade-task-meta">{row.error || row.message || ''}</div></> },
      { title: '结果', width: 150, render: (_, row) => row.stats ? <><div>收益 {(Number(row.stats.total_return || 0) * 100).toFixed(2)}%</div><div className="atrade-task-meta">成交 {row.stats.trade_count ?? '—'} 笔</div></> : complete(row.status) ? '查看完整报告' : '计算完成后显示' },
      { title: '创建时间', width: 160, render: (_, row) => row.created_at ? new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }) : '—' },
      { title: '操作', width: 300, render: (_, row) => <Space wrap size={4}><Button size="small" disabled={!!busy} onClick={() => void perform(row, 'view')}>{complete(row.status) ? '查看结果' : '查看任务'}</Button>{['running', 'pending', 'queued'].includes(row.status) && <Button size="small" disabled={!!busy} onClick={() => void perform(row, 'pause')}>暂停</Button>}{row.status === 'paused' && <Button size="small" disabled={!!busy} onClick={() => void perform(row, 'resume')}>继续</Button>}{['pending', 'queued', 'running', 'paused'].includes(row.status) && <Button size="small" disabled={!!busy} onClick={() => void perform(row, 'cancel')}>停止</Button>}{row.transport!=='report'&&<Button size="small" disabled={!!busy} onClick={() => { setRename(row); setName(row.name) }}>改名</Button>}{complete(row.status) && <Button size="small" disabled={!!busy} onClick={() => void perform(row, 'export')}>导出 JSON</Button>}{!['running', 'pending', 'queued', 'pausing', 'stopping'].includes(row.status) && <Button size="small" danger disabled={!!busy} onClick={() => Modal.confirm({ title: '删除这项研究记录？', content: row.name, okText: '删除', cancelText: '取消', onOk: () => perform(row, 'delete') })}>删除</Button>}</Space> },
    ]} />
    <Modal title="修改任务名称" open={!!rename} onCancel={() => setRename(null)} onOk={() => void saveName()} confirmLoading={!!busy} okText="保存" cancelText="取消"><Input aria-label="任务名称" maxLength={120} value={name} onChange={event => setName(event.target.value)} onPressEnter={() => void saveName()} /></Modal>
  </>
}
