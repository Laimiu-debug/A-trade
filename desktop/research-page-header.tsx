import { App, Button, Space, Tag } from 'antd'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { notifyDesktop, requestDesktop } from './research-bridge'
export function PageHeader({ title, subtitle, badge }: { title: string; subtitle: string; badge?: string }) {
  const navigate = useNavigate(), location=useLocation(), plateau=title==='策略回测'&&location.search.includes('section=plateau'),{message}=App.useApp(),symbol=/^\/stocks\/((?:sh|sz|bj)\d{6})\/chart/.exec(location.pathname)?.[1]
  const [watching,setWatching]=useState(false)
  async function addWatch(){if(watching||!symbol)return;setWatching(true);try{await requestDesktop('market',{method:'market.watchlist.save',payload:{symbol}});message.success('已加入项目跟踪池')}catch(error){message.error((error as Error).message)}finally{setWatching(false)}}
  return <div className="atrade-page-heading"><div><Space size={10}><h1>{title === '选股漏斗控制台' ? '选股漏斗' : title.startsWith('待买信号') ? '策略运行' : plateau?'收益平原':title}</h1>{badge && <Tag color="blue">{badge==='Wyckoff'?'威科夫':badge}</Tag>}</Space><p>{plateau?'沿用当前回测设置扫描参数，查看稳定区域和单组交易。':subtitle}</p></div>
    <Space wrap>{symbol&&<><Button loading={watching} onClick={()=>void addWatch()}>加入跟踪池</Button><Button onClick={()=>notifyDesktop('navigate',{page:'G01',symbol})}>个股行情</Button></>}{title==='策略回测'&&<><Button type={plateau?'default':'primary'} onClick={()=>navigate('/backtest')}>策略回测</Button><Button type={plateau?'primary':'default'} onClick={()=>navigate('/backtest?section=plateau')}>收益平原</Button></>}<Button onClick={() => navigate('/trade')}>交易计划</Button><Button onClick={() => navigate('/reports')}>报告库</Button><Button onClick={() => notifyDesktop('navigate', { page: 'C01' })}>行情设置</Button><Button onClick={() => notifyDesktop('ai-open', {})}>询问 AI</Button></Space>
  </div>
}
