import { create } from 'zustand'
import { ApiError } from '@/shared/api/client'
import {
  cancelCrossValidateTask,
  getCrossValidateTask,
  startCrossValidateTask,
} from '@/shared/api/endpoints'
import type {
  CrossValidateRequest,
  CrossValidateResponse,
  CrossValidateTaskProgress,
  CrossValidateTaskStatus,
} from '@/types/contracts'

const RESULT_CACHE_KEY = 'tdx-cross-validate-result-v1'
const TASK_POLL_INTERVAL_MS = 1200

export type CrossValidateRunStatus = 'idle' | 'running' | 'success' | 'error' | 'cancelled'

type RunCallbacks = {
  onSuccess?: (resp: CrossValidateResponse) => void
  onError?: (message: string, code?: string) => void
  onCancelled?: () => void
}

interface CrossValidateRunStoreState {
  status: CrossValidateRunStatus
  taskId: string | null
  progress: CrossValidateTaskProgress | null
  startedAt: number | null
  elapsedSeconds: number
  result: CrossValidateResponse | null
  errorMessage: string | null
  run: (payload: CrossValidateRequest, callbacks?: RunCallbacks) => Promise<void>
  stop: () => Promise<void>
  setResult: (result: CrossValidateResponse | null) => void
  resetError: () => void
}

let pollTimer: ReturnType<typeof setTimeout> | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let activeTaskId: string | null = null
let pollGeneration = 0

function loadCachedResult(): CrossValidateResponse | null {
  try {
    const raw = window.localStorage.getItem(RESULT_CACHE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as CrossValidateResponse
  } catch {
    return null
  }
}

function persistResult(result: CrossValidateResponse | null) {
  try {
    if (result) {
      window.localStorage.setItem(RESULT_CACHE_KEY, JSON.stringify(result))
    } else {
      window.localStorage.removeItem(RESULT_CACHE_KEY)
    }
  } catch {
    /* ignore */
  }
}

function clearPollTimer() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function clearElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function startElapsedTimer(set: (partial: Partial<CrossValidateRunStoreState>) => void, get: () => CrossValidateRunStoreState) {
  clearElapsedTimer()
  elapsedTimer = setInterval(() => {
    const startedAt = get().startedAt
    if (!startedAt) return
    set({ elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) })
  }, 1000)
}

function isTerminalStatus(status: CrossValidateTaskStatus) {
  return status === 'succeeded' || status === 'failed' || status === 'cancelled'
}

export const useCrossValidateRunStore = create<CrossValidateRunStoreState>((set, get) => ({
  status: 'idle',
  taskId: null,
  progress: null,
  startedAt: null,
  elapsedSeconds: 0,
  result: loadCachedResult(),
  errorMessage: null,

  setResult: (result) => {
    persistResult(result)
    set({ result })
  },

  resetError: () => set({ errorMessage: null }),

  stop: async () => {
    const taskId = activeTaskId ?? get().taskId
    pollGeneration += 1
    clearPollTimer()
    if (taskId) {
      try {
        await cancelCrossValidateTask(taskId)
      } catch {
        // 任务可能已结束，忽略取消失败
      }
    }
    activeTaskId = null
    clearElapsedTimer()
    set({
      status: 'cancelled',
      taskId: null,
      progress: null,
      startedAt: null,
      elapsedSeconds: 0,
      errorMessage: null,
    })
  },

  run: async (payload, callbacks) => {
    if (get().status === 'running') {
      return
    }

    pollGeneration += 1
    const generation = pollGeneration
    clearPollTimer()
    activeTaskId = null

    const startedAt = Date.now()
    set({
      status: 'running',
      taskId: null,
      progress: null,
      startedAt,
      elapsedSeconds: 0,
      errorMessage: null,
    })
    startElapsedTimer(set, get)

    try {
      const { task_id: taskId } = await startCrossValidateTask(payload)
      if (generation !== pollGeneration) return

      activeTaskId = taskId
      set({ taskId })

      await new Promise<void>((resolve, reject) => {
        const poll = async () => {
          if (generation !== pollGeneration) {
            resolve()
            return
          }

          try {
            const status = await getCrossValidateTask(taskId)
            if (generation !== pollGeneration) {
              resolve()
              return
            }

            set({ progress: status.progress })

            if (status.status === 'succeeded' && status.result) {
              persistResult(status.result)
              set({
                status: 'success',
                result: status.result,
                taskId: null,
                progress: status.progress,
                startedAt: null,
                errorMessage: null,
              })
              callbacks?.onSuccess?.(status.result)
              resolve()
              return
            }

            if (status.status === 'cancelled') {
              set({
                status: 'cancelled',
                taskId: null,
                progress: status.progress,
                startedAt: null,
                errorMessage: null,
              })
              callbacks?.onCancelled?.()
              resolve()
              return
            }

            if (status.status === 'failed') {
              const msg = status.error?.trim() || '交叉验证失败'
              set({
                status: 'error',
                taskId: null,
                progress: status.progress,
                startedAt: null,
                errorMessage: msg,
              })
              callbacks?.onError?.(msg, status.error_code ?? undefined)
              resolve()
              return
            }

            if (!isTerminalStatus(status.status)) {
              pollTimer = setTimeout(() => {
                void poll()
              }, TASK_POLL_INTERVAL_MS)
              return
            }

            resolve()
          } catch (error) {
            reject(error)
          }
        }

        void poll()
      })
    } catch (error) {
      if (generation !== pollGeneration) return
      const msg = error instanceof ApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : '交叉验证请求失败'
      set({
        status: 'error',
        taskId: null,
        progress: null,
        startedAt: null,
        errorMessage: msg,
      })
      callbacks?.onError?.(msg, error instanceof ApiError ? error.code : undefined)
    } finally {
      if (generation === pollGeneration) {
        activeTaskId = null
        clearPollTimer()
        clearElapsedTimer()
      }
    }
  },
}))

export function formatCrossValidateElapsed(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}
