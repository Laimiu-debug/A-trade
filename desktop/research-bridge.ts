import { useEffect, useState } from 'react'
import { readPendingBuyDrafts, writePendingBuyDrafts } from '@/shared/utils/simPendingOrders'
type Reply = { channel: string; id: string; ok: boolean; data?: unknown; error?: string }
const channel = 'atrade.research.v1'
const pending = new Map<string, { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>()
export let desktopContext: any = {}
const listeners = new Set<() => void>()
window.addEventListener('message', event => {
  if (event.origin !== 'atrade://app' || event.source !== window.parent || event.data?.channel !== channel) return
  const message = event.data as Reply & { action?: string }
  if(message.action==='plan-saved'){const id=(message.data as {id?:string})?.id;if(id){writePendingBuyDrafts(readPendingBuyDrafts().filter(row=>row.id!==id));window.dispatchEvent(new Event('atrade-plans-changed'))}return}
  if (message.action === 'context') { desktopContext = message.data; listeners.forEach(listener => listener()); return }
  const request = pending.get(message.id)
  if (!request) return
  pending.delete(message.id); clearTimeout(request.timer)
  if (message.ok) request.resolve(message.data)
  else request.reject(new Error(message.error || '操作未完成，请重试。'))
})
export function requestDesktop<T = any>(action: string, payload: unknown = {}, timeoutMs = 30000): Promise<T> {
  const id = crypto.randomUUID()
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('桌面接口暂未响应，请重试。')) }, timeoutMs)
    pending.set(id, { resolve, reject, timer })
    window.parent.postMessage({ channel, id, action, payload }, 'atrade://app')
  })
}
export function notifyDesktop(action: string, payload: unknown) { window.parent.postMessage({ channel, action, payload }, 'atrade://app') }
export function useDesktopContext() {
  const [, update] = useState(0)
  useEffect(() => { const listener = () => update(value => value + 1); listeners.add(listener); return () => { listeners.delete(listener) } }, [])
  return desktopContext
}
export async function initializeDesktop() { desktopContext = await requestDesktop('context'); listeners.forEach(listener => listener()) }
