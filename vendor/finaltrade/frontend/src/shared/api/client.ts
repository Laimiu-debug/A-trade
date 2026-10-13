import type { ApiErrorPayload } from '@/types/contracts'

const REQUEST_TIMEOUT = 10_000
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
const DEFAULT_ERROR_MESSAGE = '请求失败'

export function buildApiUrl(path: string) {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path
  }
  return `${API_BASE_URL}${path}`
}

export class ApiError extends Error {
  code: string
  degraded?: boolean
  degraded_reason?: string
  trace_id?: string

  constructor(payload: ApiErrorPayload) {
    const message = typeof payload?.message === 'string' && payload.message.trim()
      ? payload.message
      : DEFAULT_ERROR_MESSAGE
    super(message)
    this.code = typeof payload?.code === 'string' && payload.code.trim()
      ? payload.code
      : 'HTTP_UNKNOWN'
    this.degraded = payload.degraded
    this.degraded_reason = payload.degraded_reason
    this.trace_id = payload.trace_id
  }
}

function isAbortError(error: unknown) {
  return (
    (error instanceof DOMException && error.name === 'AbortError')
    || (error instanceof Error && error.name === 'AbortError')
  )
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
) {
  const { timeoutMs = REQUEST_TIMEOUT, ...requestInit } = init
  const controller = new AbortController()
  let response: Response | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let rejectCancellation: (error: ApiError) => void = () => {}
  const cancellation = new Promise<never>((_, reject) => { rejectCancellation = reject })
  const cancel = (code: string, message: string) => {
    controller.abort()
    if (response?.body && !response.body.locked) void response.body.cancel().catch(() => {})
    rejectCancellation(new ApiError({ code, message }))
  }
  const onAbort = () => cancel('REQUEST_ABORTED', '请求已取消')
  requestInit.signal?.addEventListener('abort', onAbort, { once: true })
  if (requestInit.signal?.aborted) onAbort()
  if (timeoutMs > 0) {
    timer = setTimeout(() => cancel('REQUEST_TIMEOUT', `请求超时（${Math.ceil(timeoutMs / 1000)}秒）`), timeoutMs)
  }
  const operation = async () => {
    response = await fetch(buildApiUrl(path), { ...requestInit, signal: controller.signal })
    if (!response.ok) {
      let payload: ApiErrorPayload = {
        code: `HTTP_${response.status}`,
        message: DEFAULT_ERROR_MESSAGE,
      }
      try {
        const raw = await response.json() as unknown
        if (raw && typeof raw === 'object') {
          const body = raw as Record<string, unknown>
          const code = typeof body.code === 'string' && body.code.trim()
            ? body.code
            : `HTTP_${response.status}`
          const message = typeof body.message === 'string' && body.message.trim()
            ? body.message
            : typeof body.detail === 'string' && body.detail.trim()
              ? body.detail
              : response.statusText || DEFAULT_ERROR_MESSAGE
          payload = {
            code,
            message,
            degraded: typeof body.degraded === 'boolean' ? body.degraded : undefined,
            degraded_reason: typeof body.degraded_reason === 'string' ? body.degraded_reason : undefined,
            trace_id: typeof body.trace_id === 'string' ? body.trace_id : undefined,
          }
        } else {
          payload = {
            code: `HTTP_${response.status}`,
            message: response.statusText || DEFAULT_ERROR_MESSAGE,
          }
        }
      } catch {
        payload = {
          code: `HTTP_${response.status}`,
          message: response.statusText,
        }
      }
      throw new ApiError(payload)
    }

    return (await response.json()) as T
  }
  try {
    // The deadline covers headers, error bodies and the complete JSON body.
    return await Promise.race([operation(), cancellation])
  } catch (error) {
    if (isAbortError(error)) throw new ApiError({ code: 'REQUEST_ABORTED', message: '请求已取消' })
    throw error
  } finally {
    clearTimeout(timer)
    requestInit.signal?.removeEventListener('abort', onAbort)
  }
}
