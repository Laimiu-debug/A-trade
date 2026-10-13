import { create } from 'zustand'
import { ApiError } from '@/shared/api/client'
import { scanAbnormalMovement } from '@/shared/api/endpoints'
import type { AbnormalMovementRequest, AbnormalMovementResponse } from '@/types/contracts'

const RESULT_CACHE_KEY = 'final-trade-abnormal-movement-result-v1'

export type AbnormalMovementRunStatus = 'idle' | 'running' | 'success' | 'error'

type RunCallbacks = {
  onSuccess?: (resp: AbnormalMovementResponse) => void
  onError?: (message: string) => void
  onStopped?: () => void
}

interface AbnormalMovementRunStoreState {
  status: AbnormalMovementRunStatus
  startedAt: number | null
  elapsedSeconds: number
  result: AbnormalMovementResponse | null
  lastPayload: AbnormalMovementRequest | null
  errorMessage: string | null
  runScan: (payload: AbnormalMovementRequest, callbacks?: RunCallbacks) => Promise<void>
  stop: () => void
  resetError: () => void
}

let abortController: AbortController | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let stopRequested = false
let pendingCallbacks: RunCallbacks | undefined

function loadCachedResult(): AbnormalMovementResponse | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(RESULT_CACHE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as AbnormalMovementResponse
  } catch {
    return null
  }
}

function persistResult(result: AbnormalMovementResponse | null) {
  if (typeof window === 'undefined') return
  try {
    if (result) {
      window.localStorage.setItem(RESULT_CACHE_KEY, JSON.stringify(result))
    } else {
      window.localStorage.removeItem(RESULT_CACHE_KEY)
    }
  } catch {
    // ignore
  }
}

function clearElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function startElapsedTimer(
  set: (partial: Partial<AbnormalMovementRunStoreState>) => void,
  get: () => AbnormalMovementRunStoreState,
) {
  clearElapsedTimer()
  elapsedTimer = setInterval(() => {
    const startedAt = get().startedAt
    if (!startedAt) return
    set({ elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) })
  }, 1000)
}

async function executeRun(
  payload: AbnormalMovementRequest,
  set: (partial: Partial<AbnormalMovementRunStoreState>) => void,
  get: () => AbnormalMovementRunStoreState,
) {
  if (get().status === 'running') {
    return
  }

  abortController?.abort()
  const controller = new AbortController()
  abortController = controller
  stopRequested = false
  const startedAt = Date.now()

  set({
    status: 'running',
    startedAt,
    elapsedSeconds: 0,
    errorMessage: null,
    lastPayload: payload,
  })
  startElapsedTimer(set, get)

  try {
    const resp = await scanAbnormalMovement(payload, { signal: controller.signal })
    persistResult(resp)
    set({
      status: 'success',
      result: resp,
      startedAt: null,
      errorMessage: null,
    })
    pendingCallbacks?.onSuccess?.(resp)
  } catch (error) {
    if (error instanceof ApiError && error.code === 'REQUEST_ABORTED') {
      if (stopRequested) {
        set({
          status: get().result ? 'success' : 'idle',
          startedAt: null,
          errorMessage: null,
        })
        pendingCallbacks?.onStopped?.()
      }
      stopRequested = false
      return
    }
    const msg = error instanceof Error ? error.message : '扫描请求失败'
    set({
      status: 'error',
      startedAt: null,
      errorMessage: msg,
    })
    pendingCallbacks?.onError?.(msg)
  } finally {
    if (abortController === controller) {
      abortController = null
    }
    clearElapsedTimer()
    pendingCallbacks = undefined
  }
}

export const useAbnormalMovementRunStore = create<AbnormalMovementRunStoreState>((set, get) => {
  const cachedResult = loadCachedResult()
  return {
  status: cachedResult ? 'success' : 'idle',
  startedAt: null,
  elapsedSeconds: 0,
  result: cachedResult,
  lastPayload: null,
  errorMessage: null,

  resetError: () => set({ errorMessage: null }),

  stop: () => {
    if (get().status !== 'running') return
    stopRequested = true
    abortController?.abort()
    clearElapsedTimer()
    set({
      startedAt: null,
      elapsedSeconds: 0,
    })
  },

  runScan: async (payload, callbacks) => {
    pendingCallbacks = callbacks
    await executeRun(payload, set, get)
  },
}
})

export function formatAbnormalMovementElapsed(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}
