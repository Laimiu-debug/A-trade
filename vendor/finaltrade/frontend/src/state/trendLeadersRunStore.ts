import { create } from 'zustand'
import { ApiError } from '@/shared/api/client'
import { scanLimitUpLadder, scanMarketTrendLeaders } from '@/shared/api/endpoints'
import type {
  LimitUpLadderRequest,
  LimitUpLadderResponse,
  MarketTrendLeadersRequest,
  MarketTrendLeadersResponse,
} from '@/types/contracts'

export type TrendLeadersTaskKind = 'trend' | 'ladder'
export type TrendLeadersRunStatus = 'idle' | 'running' | 'paused' | 'success' | 'error'

type RunCallbacks = {
  onSuccess?: () => void
  onError?: (message: string) => void
  onPaused?: () => void
  onStopped?: () => void
}

interface TrendLeadersRunStoreState {
  status: TrendLeadersRunStatus
  taskKind: TrendLeadersTaskKind | null
  startedAt: number | null
  elapsedSeconds: number
  trendResult: MarketTrendLeadersResponse | null
  ladderResult: LimitUpLadderResponse | null
  lastTrendPayload: MarketTrendLeadersRequest | null
  lastLadderPayload: LimitUpLadderRequest | null
  errorMessage: string | null
  runTrend: (payload: MarketTrendLeadersRequest, callbacks?: RunCallbacks) => Promise<void>
  runLadder: (payload: LimitUpLadderRequest, callbacks?: RunCallbacks) => Promise<void>
  pause: () => void
  stop: () => void
  continueRun: (callbacks?: RunCallbacks) => Promise<void>
  resetError: () => void
}

let abortController: AbortController | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let pauseRequested = false
let pendingCallbacks: RunCallbacks | undefined

function clearElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function startElapsedTimer(set: (partial: Partial<TrendLeadersRunStoreState>) => void, get: () => TrendLeadersRunStoreState) {
  clearElapsedTimer()
  elapsedTimer = setInterval(() => {
    const startedAt = get().startedAt
    if (!startedAt) return
    set({ elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) })
  }, 1000)
}

async function executeRun(
  kind: TrendLeadersTaskKind,
  payload: MarketTrendLeadersRequest | LimitUpLadderRequest,
  set: (partial: Partial<TrendLeadersRunStoreState>) => void,
  get: () => TrendLeadersRunStoreState,
) {
  if (get().status === 'running') {
    return
  }

  abortController?.abort()
  const controller = new AbortController()
  abortController = controller
  pauseRequested = false
  const startedAt = Date.now()

  set({
    status: 'running',
    taskKind: kind,
    startedAt,
    elapsedSeconds: 0,
    errorMessage: null,
    ...(kind === 'trend' ? { lastTrendPayload: payload as MarketTrendLeadersRequest } : { lastLadderPayload: payload as LimitUpLadderRequest }),
  })
  startElapsedTimer(set, get)

  try {
    if (kind === 'trend') {
      const resp = await scanMarketTrendLeaders(payload as MarketTrendLeadersRequest, { signal: controller.signal })
      set({
        status: 'success',
        trendResult: resp,
        startedAt: null,
        errorMessage: null,
      })
    } else {
      const resp = await scanLimitUpLadder(payload as LimitUpLadderRequest, { signal: controller.signal })
      set({
        status: 'success',
        ladderResult: resp,
        startedAt: null,
        errorMessage: null,
      })
    }
    pendingCallbacks?.onSuccess?.()
  } catch (error) {
    if (error instanceof ApiError && error.code === 'REQUEST_ABORTED') {
      if (pauseRequested) {
        set({
          status: 'paused',
          startedAt: null,
          errorMessage: null,
        })
        pendingCallbacks?.onPaused?.()
      } else {
        set({
          status: 'idle',
          taskKind: null,
          startedAt: null,
          elapsedSeconds: 0,
          errorMessage: null,
        })
        pendingCallbacks?.onStopped?.()
      }
      pauseRequested = false
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

export const useTrendLeadersRunStore = create<TrendLeadersRunStoreState>((set, get) => ({
  status: 'idle',
  taskKind: null,
  startedAt: null,
  elapsedSeconds: 0,
  trendResult: null,
  ladderResult: null,
  lastTrendPayload: null,
  lastLadderPayload: null,
  errorMessage: null,

  resetError: () => set({ errorMessage: null }),

  pause: () => {
    if (get().status !== 'running') return
    pauseRequested = true
    abortController?.abort()
  },

  stop: () => {
    pauseRequested = false
    abortController?.abort()
    clearElapsedTimer()
    set({
      status: 'idle',
      taskKind: null,
      startedAt: null,
      elapsedSeconds: 0,
      errorMessage: null,
    })
  },

  continueRun: async (callbacks) => {
    const { status, taskKind, lastTrendPayload, lastLadderPayload } = get()
    if (status !== 'paused' || !taskKind) return
    pendingCallbacks = callbacks
    if (taskKind === 'trend' && lastTrendPayload) {
      await executeRun('trend', lastTrendPayload, set, get)
    } else if (taskKind === 'ladder' && lastLadderPayload) {
      await executeRun('ladder', lastLadderPayload, set, get)
    }
  },

  runTrend: async (payload, callbacks) => {
    pendingCallbacks = callbacks
    await executeRun('trend', payload, set, get)
  },

  runLadder: async (payload, callbacks) => {
    pendingCallbacks = callbacks
    await executeRun('ladder', payload, set, get)
  },
}))

export function formatTrendLeadersElapsed(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}
