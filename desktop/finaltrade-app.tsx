/* Strategy workflows use the application's navigation, settings and accounts. */
import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Button } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from '@/shared/api/client'
import { AppProviders } from '@/app/providers'
import { PageAIContextProvider } from '@/shared/ai/PageAIContext'
import { BacktestTaskWatcher } from '@/shared/components/BacktestTaskWatcher'
import { BacktestPlateauTaskWatcher } from '@/shared/components/BacktestPlateauTaskWatcher'
import { StaleResourceHydrator } from '@/shared/components/StaleResourceHydrator'
import { useAIAssistantStore } from '@/state/aiAssistantStore'
import { initializeDesktop, notifyDesktop, useDesktopContext } from './research-bridge'
import { ResearchTasks } from './research-tasks'
import { ResearchPlans } from './research-plan'
import './research-integration.css'
const Screener=lazy(()=>import('@/pages/screener/ScreenerPage').then(m=>({default:m.ScreenerPage})))
const Strategy=lazy(()=>import('@/pages/strategy/StrategyCenterPage').then(m=>({default:m.StrategyCenterPage})))
const Signals=lazy(()=>import('@/pages/signals/SignalsPage').then(m=>({default:m.SignalsPage})))
const Backtest=lazy(()=>import('@/pages/backtest/BacktestPage').then(m=>({default:m.BacktestPage})))
// Stock routes are handed to the shared native market workspace by Integration.
const Chart=()=> <p role="status">正在打开个股行情与策略数据…</p>
const SignalBacktest=lazy(()=>import('@/pages/signals/SignalsBacktestPage').then(m=>({default:m.SignalsBacktestPage})))
const Events=lazy(()=>import('@/pages/strategy/EventJudgmentPage').then(m=>({default:m.EventJudgmentPage})))
function Integration() {
 const navigate=useNavigate(),location=useLocation(),context=useDesktopContext(),evidence=useAIAssistantStore(state=>state.pageContext),aiOpen=useAIAssistantStore(state=>state.open)
 useEffect(()=>{notifyDesktop('route',{route:location.pathname+location.search});document.title='A-trade · 策略研究'},[location.pathname,location.search])
 useEffect(()=>{
  let timer:ReturnType<typeof setTimeout>,previous=''
  const publish=()=>{
   const allowed=/^\/(screener|signals|backtest|strategy)(?:\/|$)/.test(location.pathname)
   const tables=allowed?Array.from(document.querySelectorAll<HTMLTableElement>('.atrade-research-content table')).filter(table=>table.getClientRects().length).slice(0,3).map((table,index)=>{
    const heads=Array.from(table.tHead?.rows[0]?.cells||[])
    const columns=heads.map((cell,column)=>({column,label:cell.textContent?.trim()||''})).filter(item=>item.label&&!/备注|笔记|理由|原因|说明|标注|操作|选择/.test(item.label)).slice(0,16)
    const loaded=Array.from(table.tBodies).flatMap(body=>Array.from(body.rows)).filter(row=>row.getClientRects().length&&!row.classList.contains('ant-table-measure-row')&&row.cells.length>1)
    const rows=loaded.slice(0,20).map(row=>columns.map(item=>(row.cells[item.column]?.textContent||'').replace(/\s+/g,' ').trim().slice(0,180)))
    return {title:`当前可见结果表 ${index+1}`,columns:columns.map(item=>item.label),rows,shownRows:rows.length,totalRows:null,loadedRows:loaded.length,scope:'仅当前已显示分页；未读取其他分页'}
   }).filter(table=>table.columns.length&&table.rows.length):[]
   const packet=evidence?{...evidence,desktopVisible:{tables}}:null,serialized=JSON.stringify(packet)
   if(serialized!==previous){previous=serialized;notifyDesktop('evidence',packet)}
  }
  const observer=new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(publish,150)})
  observer.observe(document.querySelector('.atrade-research-content')||document.body,{childList:true,subtree:true,characterData:true})
  publish();return()=>{clearTimeout(timer);observer.disconnect()}
 },[evidence,location.pathname,location.search])
 useEffect(()=>{if(aiOpen){notifyDesktop('ai-open',{});useAIAssistantStore.getState().setOpen(false)}},[aiOpen])
 useEffect(()=>{document.documentElement.classList.toggle('atrade-reduced',context.reduced===true)},[context.reduced])
 useEffect(()=>{
  const receive=(event:MessageEvent)=>{if(event.origin!=='atrade://app'||event.source!==window.parent||event.data?.channel!=='atrade.research.v1'||event.data.action!=='navigate')return;const route=event.data.data?.route;if(typeof route==='string'&&/^\/(screener|strategy|signals|backtest|reports|tasks|trade)(?:[/?]|$)/.test(route))navigate(route)}
  window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive)
 },[navigate])
 return null
}
function DataStatus(){
 const cache=useQueryClient()
 const data=useQuery({queryKey:['project-market-status'],queryFn:()=>apiRequest<any>('/api/atrade/status'),refetchInterval:15000})
 const status=data.data
 const previous=useRef<string|undefined>(undefined)
 useEffect(()=>{if(!status)return;if(previous.current&&previous.current!==status.source_revision){cache.removeQueries({predicate:query=>!['project-market-status','project-research-tasks'].includes(String(query.queryKey[0]))});window.dispatchEvent(new Event('atrade-market-source-changed'));window.dispatchEvent(new Event('atrade-reload-research-view'))}previous.current=status.source_revision},[status,cache])
 if(!status)return data.error?<Alert type="error" title={(data.error as Error).message} showIcon/>:null
 return <div className="atrade-data-status"><i style={{background:status.available?'#15803d':'#a16207'}}/><span>{status.available?'通达信日线':'尚未连接日线目录'}</span>{status.symbol_count>0&&<span>{status.symbol_count.toLocaleString('zh-CN')} 只股票</span>}{status.latest_date&&<span>截至 {status.latest_date}</span>}{status.source_changed&&<span>当前任务结束后切换至新目录</span>}<Button size="small" onClick={()=>notifyDesktop('navigate',{page:'C01'})}>{status.available?'数据源设置':'连接行情'}</Button></div>
}
function ResearchRoutes(){const [version,setVersion]=useState(0);useEffect(()=>{const reload=()=>setVersion(value=>value+1);window.addEventListener('atrade-reload-research-view',reload);return()=>window.removeEventListener('atrade-reload-research-view',reload)},[]);return <Routes key={version}><Route path="/screener" element={<Screener/>}/><Route path="/strategy" element={<Strategy/>}/><Route path="/signals" element={<Signals/>}/><Route path="/backtest" element={<Backtest/>}/><Route path="/stocks/:symbol/chart" element={<Chart/>}/><Route path="/signals/backtest" element={<SignalBacktest/>}/><Route path="/strategy/events" element={<Events/>}/><Route path="/trade" element={<ResearchPlans/>}/><Route path="/reports" element={<ResearchTasks reports/>}/><Route path="/tasks" element={<ResearchTasks/>}/><Route path="*" element={<Navigate to="/screener" replace/>}/></Routes>}
export function App(){
 const [ready,setReady]=useState(false),[error,setError]=useState('')
 useEffect(()=>{initializeDesktop().then(()=>setReady(true),failure=>setError(failure.message))},[])
 if(!ready)return <main className="atrade-research-content">{error?<Alert type="error" title={error}/>:<p>正在载入策略工作区…</p>}</main>
 return <AppProviders><PageAIContextProvider><Integration/><StaleResourceHydrator/><BacktestTaskWatcher/><BacktestPlateauTaskWatcher/><main className="atrade-research-content"><DataStatus/><Suspense fallback={<p>正在载入…</p>}><ResearchRoutes/></Suspense></main></PageAIContextProvider></AppProviders>
}
