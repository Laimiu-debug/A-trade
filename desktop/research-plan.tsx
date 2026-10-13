import { useEffect, useState } from 'react'
import { App, Button, Card, Empty, Space, Table } from 'antd'
import { PageHeader } from '@/shared/components/PageHeader'
import { readPendingBuyDrafts, writePendingBuyDrafts, upsertPendingBuyDraft } from '@/shared/utils/simPendingOrders'
import { useSearchParams } from 'react-router-dom'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { requestDesktop } from './research-bridge'
export function ResearchPlans() {
  const [params]=useSearchParams()
  const [rows, setRows] = useState(()=>{const symbol=params.get('symbol')||'',date=params.get('signal_date')||'';if(/^(sh|sz|bj)\d{6}$/.test(symbol)&&/^\d{4}-\d{2}-\d{2}$/.test(date))upsertPendingBuyDraft({symbol,signal_date:date,source:'signals',default_quantity:Number(params.get('quantity'))||1000});return readPendingBuyDrafts()}), [busy, setBusy] = useState(''), { message } = App.useApp()
  useRegisterPageAIContext({page:'generic',title:'交易计划',data:{pendingPlans:rows.slice(0,30)}})
  useEffect(()=>{const update=()=>setRows(readPendingBuyDrafts());window.addEventListener('atrade-plans-changed',update);return()=>window.removeEventListener('atrade-plans-changed',update)},[])
  async function open(row: any) { if (busy) return; setBusy(row.id); try { await requestDesktop('plan-draft', row); } catch (error) { message.error((error as Error).message) } finally { setBusy('') } }
  return <><PageHeader title="待确认交易计划" subtitle="把选股和策略信号带入当前账户的计划，核对数量与触发条件后保存。" /><Card><Table rowKey="id" dataSource={rows} locale={{ emptyText: <Empty description="还没有待确认计划，可从选股漏斗或策略运行中加入股票。" /> }} columns={[
    { title: '股票', render: (_, row) => `${row.name || ''} ${row.symbol.toUpperCase()}` }, { title: '信号日期', dataIndex: 'signal_date' }, { title: '参考价格', dataIndex: 'reference_price', render: value => value ? `${value.toFixed(2)} 元` : '—' },
    { title: '操作', render: (_, row) => <Space><Button type="primary" disabled={!!busy} onClick={() => void open(row)}>填写账户计划</Button><Button disabled={!!busy} onClick={() => { const next = rows.filter(item => item.id !== row.id); writePendingBuyDrafts(next); setRows(next) }}>移出待确认</Button></Space> },
  ]} /></Card></>
}
