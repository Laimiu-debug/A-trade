import { apiRequest as engineRequest, ApiError, buildApiUrl } from '../vendor/finaltrade/frontend/src/shared/api/client'
import { requestDesktop } from './research-bridge'
export { ApiError, buildApiUrl }
export async function apiRequest<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const intraday=/^\/api\/stocks\/((?:sh|sz|bj)\d{6})\/intraday(?:\?|$)/.exec(path)
  if(intraday&&(!init.method||init.method==='GET')){
    const status=await engineRequest<any>('/api/atrade/status',init)
    if(status.source_changed)throw new ApiError({code:'SOURCE_IN_USE',message:'未完成任务仍使用此前的行情目录，请先完成或停止这些任务，再读取新目录的分时。'})
    const url=new URL(path,location.origin),date=url.searchParams.get('date')||url.searchParams.get('trade_date')||''
    const data=await requestDesktop<any>('market',{method:'market.intraday',payload:{symbol:intraday[1],...(date?{date}:{})}},120000)
    if(!data.rows?.length)throw new ApiError({code:'MARKET_DATA_UNAVAILABLE',message:data.warnings?.[0]||'所选日期没有真实分时数据，请在个股行情中下载或连接行情来源。'})
    let amount=0,volume=0
    const shareFactor=['股','shares'].includes(data.volumeUnit)?1:['手','lots'].includes(data.volumeUnit)?100:0,yuan=['元','CNY','yuan'].includes(data.amountUnit)
    const points=data.rows.map((row:any)=>{amount+=Number(row.amount||0);volume+=Number(row.volume||0)*shareFactor;return {time:row.time,price:row.close,avg_price:shareFactor&&yuan&&volume>0?amount/volume:null,volume:row.volume,price_source:'approx'}})
    return {symbol:data.symbol,date:data.date,points,degraded:!shareFactor||!yuan,degraded_reason:!shareFactor||!yuan?'当前来源未提供明确的成交量单位，均价暂缺。':undefined,provider:data.provider,volume_unit:data.volumeUnit} as T
  }
  return engineRequest<T>(path,init)
}
